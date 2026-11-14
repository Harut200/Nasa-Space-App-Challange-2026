"""Flooded-vegetation detector v2 (multi-evidence, self-calibrating), all numerics in PyTorch.

    .venv/bin/python -m scripts.detect.floodveg              # all three areas -> products.pt
    .venv/bin/python -m scripts.detect.floodveg tonle-sap    # one area
    # checks, sensitivity, figures (docs/eda/floodveg/), one area and one pipeline per process:
    .venv/bin/python -m scripts.detect.floodveg --report-nonorm tonle-sap
    .venv/bin/python -m scripts.detect.floodveg --report tonle-sap

Integration (drop-in for ops.flooded_veg_masks):
    r = ops.run_pipeline(x, valid, "preview")
    fvp = detect_flooded_veg(r["L"], r["valid"], r["water"])
    r["flooded_veg"] = fvp["flooded_veg"]

What the data showed (docs/eda/floodveg.md): at Tonle Sap the textbook "HH up, HV flat" signature is rare.
Two signatures ARE common and spatially tied to the flood: (1) HH-HV rising (HV volume signal lost while HH
stays bright = double bounce / emergent stems), and (2) both channels falling next to, or just before
becoming, open water (canopy submerging, or partly open water inside the 180 m pixel -- not separable).
A third, (0) "already flooded on date 0", is an absolute HH-HV excess compared with dry land of the same HV,
only accepted directly on the open-water edge.

Every per-date threshold is relative to the distribution of the same feature over "null land" (valid pixels
far from any open water on that date): the per-date median is subtracted and the spread (1.4826 MAD) sets
the noise floor. A per-image brightness offset/gain therefore largely cancels.
"""
from __future__ import annotations

import json
import sys
import time

import torch

from scripts.detect import ops

FV_KNOBS = {
    "n_ref": 2,            # first n dates = early-season reference
    "null_dist_px": 20,    # null land: > 20 px (3.6 km) from open water on any date
    "db_rise_min": 0.08,   # state 1: centred rise of HH-HV over the reference must exceed max(this, k*sigma_t)
    "db_k_sigma": 3.0,
    "db_hh_drop_max": 0.10,  # ... while HH falls by less than this (HH stays bright: double bounce)
    "db_hh_min": 0.30,     # ... and HH itself is not dark (not a partly-water pixel)
    "abs_q": 0.95,         # state 1 on any date (incl. date 0): HH-HV above this quantile of null land at same HV
    "abs_margin": 0.02,    # ... by at least this margin
    "abs_dist_px": 2,      # ... and only within 2 px of open water on that date
    "sub_drop": 0.10,      # state 2: both channels fall by more than this (centred) vs reference
    "sub_dist_px": 1,      # ... and the pixel touches open water now, or becomes open water later
    "tau": 0.02,           # softness of the sigmoid score (brightness units)
    "near_px": 10,         # context weight 1 within 10 px (1.8 km) of open water on any date
    "decay_px": 5.0,       # ... then exp(-(d-near)/decay)
    "max_px": 30,          # ... and 0 beyond 30 px (5.4 km)
    "score_thr": 0.5,      # decision threshold on the final score
    "persist": 2,          # must be flagged on >= 2 consecutive valid dates (per-date mask)
    "min_blob_px": 4,
    "town_q": 0.95,        # built-up: DF above null quantile on >= 90 % of dates and stable
    "town_std_max": 0.04,
    "perm_frac": 0.9,      # pixels water on >= 90 % of dates are lake, never flooded vegetation
}


# ------------------------------------------------------------------ helpers
def dist_px(m: torch.Tensor, K: int) -> torch.Tensor:
    """Chebyshev distance (px) to the nearest True pixel, capped at K+1. m [N,H,W] bool."""
    d = torch.full(m.shape, float(K + 1), device=m.device)
    d[m] = 0
    cur = m.clone()
    for k in range(1, K + 1):
        nxt = ops.dilate(cur, 3)
        d[nxt & ~cur] = k
        cur = nxt
    return d


def robust_stats(x: torch.Tensor, null: torch.Tensor, sub: int = 4):
    """Per-date median and 1.4826*MAD of x [T,H,W] over null [T,H,W] (subsampled every `sub` px)."""
    med, sig = [], []
    for t in range(x.shape[0]):
        v = x[t][null[t]][::sub]
        if v.numel() < 50:
            med.append(torch.zeros((), device=x.device)); sig.append(torch.full((), 0.03, device=x.device)); continue
        m = v.median()
        med.append(m); sig.append(((v - m).abs().median() * 1.4826).clamp_min(1e-3))
    return torch.stack(med), torch.stack(sig)


def cond_quantile(df: torch.Tensor, hv: torch.Tensor, null: torch.Tensor, q: float, nb: int = 20, sub: int = 4,
                  lo: float = 0.0, hi: float = 1.0):
    """Per date, the q-quantile of DF among null land in each HV bin (nb bins over [lo, hi]: previews 0..1
    brightness, calibrated dB e.g. -36..-6); returns the map Q_t(HV_t(pixel)) [T,H,W]. Bins with < 30 null
    pixels inherit the global null quantile."""
    if (lo, hi) != (0.0, 1.0):
        hv = ((hv - lo) / (hi - lo)).clamp(0, 0.999999)
    out = torch.empty_like(df)
    for t in range(df.shape[0]):
        d, h = df[t][null[t]][::sub], hv[t][null[t]][::sub]
        glob = torch.quantile(d, q) if d.numel() > 30 else torch.tensor(1.0, device=df.device)
        qs = torch.full((nb,), float(glob), device=df.device)
        b = (h * nb).long().clamp(0, nb - 1)
        for i in range(nb):
            sel = d[b == i]
            if sel.numel() >= 30:
                qs[i] = torch.quantile(sel, q)
        out[t] = qs[(hv[t] * nb).long().clamp(0, nb - 1)]
    return out


def persist_mask(m: torch.Tensor, valid: torch.Tensor, n: int) -> torch.Tensor:
    """Keep m_t only where it is part of a run of >= n consecutive valid flagged dates."""
    if n <= 1:
        return m
    T = m.shape[0]
    run_end = torch.zeros_like(m, dtype=torch.int16)
    c = torch.zeros(m.shape[1:], dtype=torch.int16, device=m.device)
    for t in range(T):
        c = torch.where(m[t], c + 1, torch.zeros_like(c))
        run_end[t] = c
    # propagate the run length backwards so every member of a long run is kept
    keep = torch.zeros_like(m)
    best = torch.zeros(m.shape[1:], dtype=torch.int16, device=m.device)
    for t in range(T - 1, -1, -1):
        best = torch.where(m[t], torch.maximum(best, run_end[t]), torch.zeros_like(best))
        keep[t] = m[t] & (best >= n)
    return keep


def remove_small_fast(m: torch.Tensor, min_px: int) -> torch.Tensor:
    """Same result as ops.remove_small (8-connected, per date) but labelled with scipy.ndimage
    (bookkeeping only; ops' iterative torch labelling takes ~20 s on the Tonle Sap stack)."""
    import numpy as np
    from scipy import ndimage
    if min_px <= 1:
        return m
    out = m.cpu().numpy().copy()
    st = np.ones((3, 3), bool)
    for t in range(out.shape[0]):
        lab, n = ndimage.label(out[t], structure=st)
        if n:
            cnt = np.bincount(lab.ravel())
            small = cnt < min_px
            small[0] = False
            out[t][small[lab]] = False
    return torch.from_numpy(out).to(m.device)


def sig(x, tau):
    return torch.sigmoid(x / tau)


# ------------------------------------------------------------------ detector
def detect_flooded_veg(L, valid, water, kn=None, terrain=None) -> dict:
    """L [T,2,H,W] log-like (ops.run_pipeline 'L'), valid [T,H,W], water [T,H,W] bool.
    terrain: optional dict with 'water_possible' [H,W] bool (flat/low-lying) -> AND-ed into the context.
    Returns flooded_veg [T,H,W] bool, fv_score [T,H,W], fv_state [T,H,W] uint8 (0 none, 1 emergent/double
    bounce, 2 submerging toward open water), ever_flooded_veg, first_fv_date (index, -1 none), fv_px [T],
    per-evidence scores and the knobs used."""
    k = dict(FV_KNOBS, **(kn or {}))
    dev = L.device
    T = L.shape[0]
    hh, hv = L[:, 0], L[:, 1]
    df = hh - hv
    v = valid
    nv = v.sum(0).clamp_min(1)
    lake = (water & v).sum(0).float() / nv >= k["perm_frac"]
    anyw = (water & v).any(0)

    # distances to water: on this date, on any date
    d_now = dist_px(water, max(k["abs_dist_px"], k["sub_dist_px"]) + 1)
    d_any = dist_px(anyw[None], k["max_px"])[0]
    null = v & (d_any > k["null_dist_px"])[None] & ~water
    # later water: pixel is open water on some date after t
    later = torch.zeros_like(water)
    acc = torch.zeros_like(water[0])
    for t in range(T - 1, -1, -1):
        later[t] = acc
        acc = acc | water[t]

    nref = k["n_ref"]
    vr = v[:nref].float()
    ref = (L[:nref] * vr[:, None]).sum(0) / vr.sum(0).clamp_min(1)[None]
    refv = v[:nref].any(0)
    d_hh, d_hv = hh - ref[0], hv - ref[1]
    d_df = d_hh - d_hv
    m_hh, _ = robust_stats(d_hh, null)
    m_hv, _ = robust_stats(d_hv, null)
    m_df, s_df = robust_stats(d_df, null)
    c_hh = d_hh - m_hh[:, None, None]
    c_hv = d_hv - m_hv[:, None, None]
    c_df = d_df - m_df[:, None, None]
    tau = k["tau"]

    # (1a) temporal double bounce: HH-HV rises above the noise floor while HH stays bright
    thr_db = torch.maximum(k["db_k_sigma"] * s_df, torch.tensor(k["db_rise_min"], device=dev))[:, None, None]
    e_db = sig(c_df - thr_db, tau) * sig(c_hh + k["db_hh_drop_max"], tau) * sig(hh - k["db_hh_min"], tau)
    e_db = e_db * refv[None]
    e_db[:nref] = 0  # by construction no temporal evidence on reference dates

    # (1b) absolute double bounce on the open-water edge (also date 0): DF above dry land of the same HV
    q_abs = cond_quantile(df, hv, null, k["abs_q"])
    q_town = q_abs if k["town_q"] == k["abs_q"] else cond_quantile(df, hv, null, k["town_q"])
    town = (((df > q_town) & v).sum(0).float() / nv >= 0.9) & (df.std(0) < k["town_std_max"]) & (d_any > k["abs_dist_px"])
    e_abs = sig(df - q_abs - k["abs_margin"], tau) * sig(hh - k["db_hh_min"], tau) * (d_now <= k["abs_dist_px"])
    e_abs = e_abs * ~town[None]

    # (2) submerging: both channels fall toward water, next to water now or open water later
    drop = torch.minimum(-c_hh, -c_hv)
    e_sub = sig(drop - k["sub_drop"], tau) * ((d_now <= k["sub_dist_px"]) | later) * refv[None]
    e_sub[:nref] = 0

    # context: near open water (any date), optional terrain
    ctx = torch.where(d_any <= k["near_px"], torch.ones_like(d_any),
                      torch.exp(-(d_any - k["near_px"]) / k["decay_px"])) * (d_any <= k["max_px"])
    if terrain is not None and "water_possible" in terrain:
        ctx = ctx * terrain["water_possible"].to(dev).float()
    ok = (v & ~water & ~lake[None]).float()
    s1 = torch.maximum(e_db, e_abs) * ctx * ok
    s2 = e_sub * ctx * ok
    score = torch.maximum(s1, s2)

    cand = score >= k["score_thr"]
    fv = persist_mask(cand, v, k["persist"]) & v
    fv = remove_small_fast(fv, k["min_blob_px"])
    state = torch.zeros(fv.shape, dtype=torch.uint8, device=dev)
    state[fv & (s1 >= s2)] = 1
    state[fv & (s2 > s1)] = 2
    idx = torch.arange(T, device=dev)[:, None, None].expand_as(fv)
    first = torch.where(fv, idx, torch.full_like(idx, T)).min(0).values
    first = torch.where(first == T, torch.full_like(first, -1), first)
    return {"flooded_veg": fv, "fv_score": score, "fv_state": state, "ever_flooded_veg": fv.any(0),
            "first_fv_date": first.to(torch.int16), "fv_px": fv.sum((1, 2)),
            "e_db": e_db, "e_abs": e_abs, "e_sub": e_sub, "context": ctx, "town": town,
            "noise_sigma_df": s_df, "centre": {"hh": m_hh, "hv": m_hv, "df": m_df}, "fv_knobs": k}


# ------------------------------------------------------------------ CLI
AREAS = ["tonle-sap", "sevan", "ararat"]


KEEP = ("L", "valid", "water", "prior_water", "knobs")


def load_run(area, normalise=None, dev=None, use_terrain=True):
    """ops.run_pipeline with the terrain water mask (if built); keeps only what this module needs."""
    from scripts.detect.stack import load_preview_stack
    st = load_preview_stack(area)
    dev = dev or torch.device("cpu")
    ok = load_terrain(area, st.x.shape[2:]) if use_terrain else None
    r = ops.run_pipeline(st.x, st.valid, "preview", dev=dev, normalise=normalise,
                         water_ok=None if ok is None else ok["water_possible"])
    r = {k: r[k] for k in KEEP}
    r["terrain"] = ok
    return st, r


def save_products(area, st, r, out):
    km2 = st.km2_per_px
    p = {"flooded_veg": out["flooded_veg"].cpu(), "fv_score": out["fv_score"].half().cpu(),
         "fv_state": out["fv_state"].cpu(), "fv_km2": (out["fv_px"].float() * km2).cpu(),
         "ever_flooded_veg": out["ever_flooded_veg"].cpu(), "first_fv_date": out["first_fv_date"].cpu(),
         "fv_state_km2": {s: [float(((out["fv_state"][t] == s).sum()) * km2) for t in range(len(st.dates))] for s in (1, 2)},
         "dates": st.dates, "bounds": st.bounds, "km2_per_px": km2, "thresholds": out["fv_knobs"],
         "noise_sigma_df": out["noise_sigma_df"].cpu(), "town": out["town"].cpu(), "valid": r["valid"].cpu()}
    d = ops_root() / f"data/processed/{area}/floodveg"
    d.mkdir(parents=True, exist_ok=True)
    torch.save(p, d / "products.pt")
    return p


def load_terrain(area, shape):
    """Optional terrain constraint from the parallel terrain agent; None if absent or on another grid."""
    f = ops_root() / f"data/processed/{area}/terrain.pt"
    if not f.exists():
        return None
    try:
        t = torch.load(f, weights_only=False, map_location="cpu")
        wp = t.get("water_possible")
        if wp is None or tuple(wp.shape) != tuple(shape):
            return None
        return {"water_possible": wp.bool()}
    except Exception:  # noqa: BLE001
        return None


def ops_root():
    from scripts.detect.stack import ROOT
    return ROOT


def main(argv):
    areas = [a for a in argv if not a.startswith("--")] or AREAS
    if "--report-nonorm" in argv:   # report = run --report-nonorm then --report, one area per process
        return [report_stage_nonorm(a) for a in areas]
    if "--report" in argv:
        return [report_stage_main(a) for a in areas]
    for a in areas:
        t0 = time.time()
        st, r = load_run(a, use_terrain="--no-terrain" not in argv)
        out = detect_flooded_veg(r["L"], r["valid"], r["water"], terrain=r["terrain"])
        p = save_products(a, st, r, out)
        print(a, "fv km2:", [round(x, 1) for x in p["fv_km2"].tolist()],
              "ever:", round(float(p["ever_flooded_veg"].sum()) * st.km2_per_px, 1), f"({time.time() - t0:.0f}s)")


# ------------------------------------------------------------------ report (validation, sensitivity, figures)
OLD_SENS = {"fv_hh_rise": (0.12, 0.15, 0.20), "fv_hv_rise_max": (0.04, 0.06, 0.08)}
NEW_SENS = {"db_rise_min": (0.06, 0.08, 0.10), "db_k_sigma": (2.5, 3.0, 3.5), "sub_drop": (0.08, 0.10, 0.12),
            "near_px": (5, 10, 15), "score_thr": (0.4, 0.5, 0.6)}
SPOTS = {"Prek Toal": (103.7, 13.1), "flood corridor": (104.85, 12.0), "Chong Kneas": (103.84, 13.27),
         "NW plain": (103.79, 12.82)}


def old_rule(r, kn=None):
    k = dict(r["knobs"], **(kn or {}))
    return ops.flooded_veg_masks(r["L"], r["valid"], r["water"], r["prior_water"], k)


def _rc(st, lon, lat):
    return st.lonlat_to_rc(lon, lat)


def evaluate(area, st, r, r_nonorm, out):
    """All the checks. Returns a JSON-able dict. GSW is used ONLY here, to measure."""
    from scripts.detect import validate as V
    km2 = st.km2_per_px
    T, H, W = r["water"].shape
    dom = r["valid"].all(0)
    res = {"area": area, "dates": st.dates, "km2_per_px": km2}
    old = old_rule(r)
    old_nn = old_rule(r_nonorm)
    new_nn = detect_flooded_veg(r_nonorm["L"], r_nonorm["valid"], r_nonorm["water"], terrain=r["terrain"])["flooded_veg"]
    new = out["flooded_veg"]
    oldc = (old & r["valid"]).sum(0) >= r["knobs"]["fv_min_dates"]
    newc = new.sum(0) >= 2
    a = lambda m: round(float(m.sum()) * km2, 1)
    res["km2_per_date"] = {"old": [a(m) for m in old], "new": [a(m) for m in new],
                           "old_no_norm": [a(m) for m in old_nn], "new_no_norm": [a(m) for m in new_nn],
                           "new_state1": [a(out["fv_state"][t] == 1) for t in range(T)],
                           "new_state2": [a(out["fv_state"][t] == 2) for t in range(T)]}
    res["ever_km2"] = {"old": a(old.any(0)), "new": a(new.any(0)), "old_class": a(oldc), "new_class": a(newc)}
    res["norm_change_max_rel"] = {
        k: round(max(abs(x - y) / max(x, y, 1e-9) for x, y in zip(res["km2_per_date"][k], res["km2_per_date"][k + "_no_norm"])
                     if max(x, y) > 20) if any(max(x, y) > 20 for x, y in zip(res["km2_per_date"][k], res["km2_per_date"][k + "_no_norm"])) else 0.0, 3)
        for k in ("old", "new")}
    # threshold sensitivity (peak area for each knob value, others default)
    sens = {"old": {}, "new": {}}
    curves = {"old": [res["km2_per_date"]["old_no_norm"]], "new": [res["km2_per_date"]["new_no_norm"]]}
    for kname, vals in OLD_SENS.items():
        sens["old"][kname] = {}
        for v in vals:
            m = old_rule(r, {kname: v})
            sens["old"][kname][str(v)] = a(m[m.sum((1, 2)).argmax()])
            curves["old"].append([a(x) for x in m])
    for kname, vals in NEW_SENS.items():
        sens["new"][kname] = {}
        for v in vals:
            m = detect_flooded_veg(r["L"], r["valid"], r["water"], {kname: v}, terrain=r["terrain"])["flooded_veg"]
            sens["new"][kname][str(v)] = a(m[m.sum((1, 2)).argmax()])
            curves["new"].append([a(x) for x in m])
    for side in sens:
        for kname, d in sens[side].items():
            vs = list(d.values())
            d["max_over_min"] = round(max(vs) / max(min(vs), 1e-9), 2)
    res["sensitivity_peak_km2"] = sens
    res["sensitivity_curves_km2"] = curves
    # reference checks
    try:
        ref, _ = V.reference_on_grid(area, st.bounds, H, W)
        ext = (ref["ext_frac"] >= 0.5) & dom
        near1 = ops.dilate(ext[None], 3)[0]
        near3 = ops.dilate(ext[None], 7)[0]
        def share(m):
            n = float(m.sum())
            return {"km2": a(m), "inside_max_extent": round(float((m & ext).sum()) / max(n, 1), 3),
                    "within_1px": round(float((m & near1).sum()) / max(n, 1), 3),
                    "within_3px": round(float((m & near3).sum()) / max(n, 1), 3),
                    "median_occurrence": round(float(ref["occ_mean"][m].median()), 1) if n else None}
        tp_o, tp_n = int(old.sum((1, 2)).argmax()), int(new.sum((1, 2)).argmax())
        res["reference"] = {"old_ever": share(old.any(0) & dom), "new_ever": share(new.any(0) & dom),
                            "old_class": share(oldc & dom), "new_class": share(newc & dom),
                            "old_peak": share(old[tp_o] & dom), "new_peak": share(new[tp_n] & dom),
                            "new_state1_ever": share((out["fv_state"] == 1).any(0) & dom),
                            "new_state2_ever": share((out["fv_state"] == 2).any(0) & dom)}
        res["_ext"] = ext
    except Exception as ex:  # noqa: BLE001
        res["reference"] = {"error": str(ex)}
    # time consistency: distance of newly flagged pixels from the permanent lake, per date
    lake = ((r["water"] & r["valid"]).sum(0).float() / r["valid"].sum(0).clamp_min(1)) >= 0.9
    dl = dist_px(lake[None], 200)[0]
    fd = out["first_fv_date"]
    idx = torch.arange(T)[:, None, None]
    fw = torch.where(r["water"], idx, torch.full_like(idx.expand(T, H, W), T)).min(0).values
    res["outward"] = {"first_fv_median_dist_px": [round(float(dl[fd == t].median()), 1) if (fd == t).sum() > 20 else None for t in range(T)],
                      "first_fv_km2": [a(fd == t) for t in range(T)],
                      "first_water_median_dist_px": [round(float(dl[(fw == t) & ~lake].median()), 1) if ((fw == t) & ~lake).sum() > 20 else None for t in range(T)]}
    # sequence: of pixels that become open water at onset t>=3, share flagged fv on a date before onset
    seq = (fw >= 3) & (fw < T) & ~lake & dom
    before = torch.zeros(H, W, dtype=torch.bool)
    before_old = torch.zeros(H, W, dtype=torch.bool)
    for t in range(T):
        before |= new[t] & (fw > t)
        before_old |= old[t] & (fw > t)
    res["fv_before_open_water"] = {"n_px": int(seq.sum()), "new_share": round(float((before & seq).sum()) / max(int(seq.sum()), 1), 3),
                                   "old_share": round(float((before_old & seq).sum()) / max(int(seq.sum()), 1), 3)}
    if area == "tonle-sap":
        res["spots"] = {}
        for nm, (lon, lat) in SPOTS.items():
            y, x = _rc(st, lon, lat)
            sl = (slice(y - 5, y + 6), slice(x - 5, x + 6))
            res["spots"][nm] = {"new_ever_share": round(float(new.any(0)[sl].float().mean()), 2),
                                "old_ever_share": round(float(old.any(0)[sl].float().mean()), 2),
                                "new_first_date": [st.dates[i] for i in sorted(set(fd[sl][fd[sl] >= 0].tolist()))][:1]}
    res["_old"], res["_new_nn"], res["_old_nn"] = old, new_nn, old_nn
    return res


def _save(fig, name):
    from scripts.detect.stack import ROOT
    d = ROOT / "docs/eda/floodveg"
    d.mkdir(parents=True, exist_ok=True)
    fig.savefig(d / name, dpi=90, bbox_inches="tight")
    import matplotlib.pyplot as plt
    plt.close(fig)


def figures(area, st, r, out, res):
    import datetime as dt
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import ListedColormap
    T = len(st.dates)
    dates = [dt.date.fromisoformat(x) for x in st.dates]
    L, v, w = r["L"], r["valid"], r["water"]
    w_, s_, e_, n_ = st.bounds
    ext = [w_, e_, s_, n_]
    new, old = out["flooded_veg"], res["_old"]
    if area == "tonle-sap":
        # 1. class trajectories in the (HH, HV) plane
        lake = ((w & v).sum(0).float() / v.sum(0).clamp_min(1)) >= 0.9
        idx = torch.arange(T)[:, None, None]
        fw = torch.where(w, idx, torch.full_like(idx.expand_as(w), T)).min(0).values
        d_any = dist_px(w.any(0)[None], 30)[0]
        hh, hv = L[:, 0], L[:, 1]
        classes = {"permanent lake": lake,
                   "ring -> open water (Sep)": (fw >= 6) & (fw <= 7) & w[-1],
                   "upland forest (stable, far)": (hh.mean(0) > 0.6) & (hv.mean(0) > 0.6) & (d_any > 30) & (hh.std(0) < 0.05),
                   "cropland/mid (far, mid bright)": ((hh.mean(0) > 0.3) & (hh.mean(0) < 0.55)) & (d_any > 30),
                   "new fv state 1 (ever)": (out["fv_state"] == 1).any(0),
                   "new fv state 2 (ever)": (out["fv_state"] == 2).any(0) & ~w[-1]}
        for nm, (lon, lat) in SPOTS.items():
            y, x = st.lonlat_to_rc(lon, lat)
            m = torch.zeros_like(lake); m[y - 2:y + 3, x - 2:x + 3] = True
            classes["spot: " + nm] = m
        fig, ax = plt.subplots(1, 2, figsize=(13, 5.5))
        cols = plt.cm.tab10(np.arange(len(classes)))
        for (nm, m), c in zip(classes.items(), cols):
            m = m & v.all(0)
            if m.sum() < 5:
                continue
            a_ = hh[:, m].mean(1).numpy(); b_ = hv[:, m].mean(1).numpy()
            ls = "--" if nm.startswith("spot") else "-"
            ax[0].plot(a_, b_, ls, color=c, marker="o", ms=3, label=f"{nm} (n={int(m.sum())})")
            ax[0].plot(a_[0], b_[0], "s", color=c, ms=7)
            ax[1].plot(dates, (a_ - b_), ls, color=c, marker="o", ms=3)
        ax[0].plot([0, 1], [0, 1], color="0.7", lw=0.8)
        ax[0].set_xlabel("HH brightness"); ax[0].set_ylabel("HV brightness")
        ax[0].set_title("Tonle Sap: class mean trajectories (square = 19 Jun)")
        ax[0].legend(fontsize=7, loc="upper left")
        ax[1].set_title("HH - HV over time (ratio proxy)")
        ax[1].axvspan(dt.date(2026, 7, 27), dt.date(2026, 8, 10), color="0.85")
        ax[1].tick_params(axis="x", rotation=30)
        _save(fig, "01_trajectories_tonle-sap.png")

        # 2. evidence/score maps on the peak date
        tp = int(new.sum((1, 2)).argmax())
        fig, ax = plt.subplots(1, 4, figsize=(17, 4.6))
        for a_, (nm, im) in zip(ax, [("HH " + st.dates[tp], L[tp, 0]), ("temporal double bounce e_db", out["e_db"][tp] * out["context"]),
                                     ("submerging e_sub", out["e_sub"][tp] * out["context"]), ("final score", out["fv_score"][tp].float())]):
            h = a_.imshow(im.numpy(), extent=ext, vmin=0, vmax=1, cmap="gray" if nm.startswith("HH") else "magma", interpolation="nearest")
            a_.set_title(nm, fontsize=9)
        fig.colorbar(h, ax=ax, shrink=0.7)
        _save(fig, "02_score_maps_tonle-sap.png")

        # 3. old vs new on the peak date
        to = int(old.sum((1, 2)).argmax())
        fig, ax = plt.subplots(1, 2, figsize=(13, 6))
        cm = ListedColormap(["#202020", "#3b6fd6", "#f2c12e", "#4bd16a", "#e0559a"])
        for a_, (nm, m, tt) in zip(ax, [("old rule", old, to), ("new rule", new, tp)]):
            im = torch.zeros(L.shape[2:], dtype=torch.uint8)
            im[w[tt]] = 1
            if nm == "old rule":
                im[m[tt]] = 3
            else:
                im[out["fv_state"][tt] == 1] = 3
                im[out["fv_state"][tt] == 2] = 4
            im[~v[tt]] = 0
            a_.imshow(im.numpy(), extent=ext, cmap=cm, vmin=0, vmax=4, interpolation="nearest")
            a_.set_title(f"{nm}, {st.dates[tt]}: {float(m[tt].sum()) * st.km2_per_px:.0f} km2 flooded veg", fontsize=10)
            if "_ext" in res:
                a_.contour(res["_ext"].float().numpy(), levels=[0.5], colors="white", linewidths=0.4, extent=ext, origin="upper")
        from matplotlib.patches import Patch
        ax[1].legend(handles=[Patch(color="#3b6fd6", label="open water"), Patch(color="#4bd16a", label="fv state 1 / old fv"),
                              Patch(color="#e0559a", label="fv state 2 (submerging)"), Patch(edgecolor="white", facecolor="none", label="GSW max extent")],
                     fontsize=8, loc="lower left")
        _save(fig, "03_old_vs_new_peak_tonle-sap.png")

        # 4. per-date small multiples
        fig, ax = plt.subplots(2, 5, figsize=(17, 7.5))
        for t in range(T):
            im = torch.zeros(L.shape[2:], dtype=torch.uint8)
            im[w[t]] = 1; im[out["fv_state"][t] == 1] = 3; im[out["fv_state"][t] == 2] = 4; im[~v[t]] = 0
            a_ = ax.flat[t]
            a_.imshow(im.numpy(), extent=ext, cmap=cm, vmin=0, vmax=4, interpolation="nearest")
            a_.set_title(f"{st.dates[t]}  fv {res['km2_per_date']['new'][t]:.0f} km2", fontsize=9)
            a_.set_xticks([]); a_.set_yticks([])
        fig.suptitle("Tonle Sap: blue open water, green state 1 (double bounce), pink state 2 (submerging)")
        _save(fig, "04_per_date_tonle-sap.png")

        # 5. area vs date with sensitivity bands
        fig, ax = plt.subplots(figsize=(10, 4.8))
        sens_new = [torch.tensor(c) for c in res["sensitivity_curves_km2"]["new"]]
        sens_old = [torch.tensor(c) for c in res["sensitivity_curves_km2"]["old"]]
        for nm, curve, band, c in [("old rule", res["km2_per_date"]["old"], sens_old, "#d95f02"),
                                   ("new rule", res["km2_per_date"]["new"], sens_new, "#1b9e77")]:
            b = torch.stack(band)
            ax.fill_between(dates, b.min(0).values.numpy(), b.max(0).values.numpy(), color=c, alpha=0.2)
            ax.plot(dates, curve, "o-", color=c, label=nm + " (band: each threshold +/- one step, normalisation off)")
        ax.plot(dates, res["km2_per_date"]["new_state1"], ":", color="#1b9e77", label="new, state 1 only")
        ax.plot(dates, res["km2_per_date"]["new_state2"], "--", color="#1b9e77", label="new, state 2 only")
        ax.axvspan(dt.date(2026, 7, 27), dt.date(2026, 8, 10), color="0.85", label="no data")
        ax.set_ylabel("flooded vegetation, km2"); ax.legend(fontsize=8); ax.set_title("Tonle Sap flooded-vegetation area per pass")
        ax.tick_params(axis="x", rotation=30)
        _save(fig, "05_area_vs_date_tonle-sap.png")


def fig_armenia(allres):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    fig, ax = plt.subplots(1, 2, figsize=(11, 4))
    for a_, area in zip(ax, ["sevan", "ararat"]):
        R = allres.get(area)
        if R is None:
            continue
        x = np.arange(len(R["dates"]))
        a_.bar(x - 0.2, R["km2_per_date"]["old"], 0.4, color="#d95f02", label="old rule")
        a_.bar(x + 0.2, R["km2_per_date"]["new"], 0.4, color="#1b9e77", label="new rule")
        a_.set_xticks(x); a_.set_xticklabels([d[5:] for d in R["dates"]], rotation=45, fontsize=8)
        a_.set_title(f"{area}: false alarms (expected ~0). ever: old {R['ever_km2']['old']:.0f}, new {R['ever_km2']['new']:.0f} km2", fontsize=9)
        a_.set_ylabel("km2"); a_.legend(fontsize=8)
    _save(fig, "06_armenia_false_alarms.png")


def _scratch(area):
    import os
    import pathlib
    d = pathlib.Path(os.environ.get("FV_CACHE", "/tmp")) / "floodveg_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{area}_nonorm.pt"


def report_stage_nonorm(area):
    """Process 1 of 2 (memory: one pipeline per process): the normalisation-off run, cached to disk."""
    _, rn = load_run(area, normalise=False)
    torch.save({k: rn[k] for k in KEEP if k != "knobs"} | {"knobs": rn["knobs"]}, _scratch(area))
    print(area, "no-norm run cached at", _scratch(area))


def report_stage_main(area):
    """Process 2 of 2: normalised run, products, checks, sensitivity, figures, results.json (merged per area)."""
    from scripts.detect.stack import ROOT
    t0 = time.time()
    st, r = load_run(area)
    rn = torch.load(_scratch(area), weights_only=False)
    rn["terrain"] = r["terrain"]
    out = detect_flooded_veg(r["L"], r["valid"], r["water"], terrain=r["terrain"])
    save_products(area, st, r, out)
    res = evaluate(area, st, r, rn, out)
    res["terrain_used"] = r["terrain"] is not None
    del rn
    d = ROOT / "docs/eda/floodveg"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "results.json"
    allres = json.loads(f.read_text()) if f.exists() else {}
    allres[area] = {k: v for k, v in res.items() if not k.startswith("_")}
    f.write_text(json.dumps(allres, indent=1))
    figures(area, st, r, out, res)
    if all(a in allres for a in ("sevan", "ararat")):
        fig_armenia(allres)
    print(area, f"{time.time() - t0:.0f}s", json.dumps(allres[area])[:2500])


if __name__ == "__main__":
    main(sys.argv[1:])
