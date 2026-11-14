"""Cross-scene robust open-water detection: per-scene self-calibration WITHOUT the reference.

    .venv/bin/python -m scripts.detect.crossscene                 # all areas: prepare, diagnose, detect, evaluate, figures
    .venv/bin/python -m scripts.detect.crossscene pantanal sevan  # some areas (results.json is merged)
    .venv/bin/python -m scripts.detect.crossscene --report        # figures + results from the cached per-area results only

Write-up: docs/eda/crossscene.md. Tests: tests/test_crossscene.py.

Why: the current detector (ops.KNOBS["preview"]) calls a pixel water when its filtered HH display
brightness is < 0.06 (seed) / < 0.12 (grown) and HV < 0.35. The previews are display-stretched
differently per scene, so the same physical water sits at 0.004 in one scene and at 0.2 in another.
Every method here places its thresholds from the scene's (and the date's) OWN statistics.

The JRC Global Surface Water reference is used ONLY in `evaluate` / `diagnose` (to measure), never in
`adaptive_knobs` / `detect_water_adaptive`.

Integration (the lead applies it; see docs/eda/crossscene.md "Integration"):

    out = ops.run_pipeline(x, valid, "preview", water_ok=water_ok)
    res = crossscene.detect_water_adaptive(out["L"], out["valid"], water_ok=water_ok, method="seeded")
    out["water"] = res["water"].to(out["water"].device)

All constants are named knobs in CKNOBS; they were set on Tonle Sap + Lake Sevan only.
"""
from __future__ import annotations

import argparse
import json
import math
import time

import numpy as np
import torch
import torch.nn.functional as F

from scripts.detect import ops
from scripts.detect.stack import ROOT, device, load_preview_stack

AREAS = ["tonle-sap", "sevan", "ararat", "inner-niger", "poyang", "bangladesh-haor", "okavango", "urmia",
         "sindh-indus", "mekong-delta", "rondonia", "nile-delta", "sudd", "lake-chad", "pantanal"]
TUNE_AREAS = ["tonle-sap", "sevan"]
DROPPED = ["lake-chad", "pantanal"]
OUT = ROOT / "docs/eda/crossscene"
TITLES = {"tonle-sap": "Tonle Sap", "sevan": "Lake Sevan", "ararat": "Ararat valley", "inner-niger": "Inner Niger",
          "poyang": "Poyang", "bangladesh-haor": "Bangladesh haor", "okavango": "Okavango", "urmia": "Urmia",
          "sindh-indus": "Sindh (Indus)", "mekong-delta": "Mekong delta", "rondonia": "Rondonia",
          "nile-delta": "Nile delta", "sudd": "Sudd", "lake-chad": "Lake Chad", "pantanal": "Pantanal"}
FLOOR = 1.5 / 255  # raw preview value at or below this = display floor
BLOCK = 20         # px, block bootstrap
SEED = 20261114


def pdir(area):
    return ROOT / "data/processed" / area / "crossscene"


# ============================================================================ preparation (inputs + anchors)
def prepare(area, dev=None, force=False):
    """Everything the methods and the evaluation need for one area, cached in crossscene/base.pt (CPU):
    L [T,2,H,W] (ops.run_pipeline filtered, normalised; QC applied), valid [T,H,W], fixed-detector water
    WITH the terrain mask, terrain water_ok [H,W] or None, floor share per date (raw preview), and the
    JRC anchors (perm / never / amb, tune.build_labels) -- the anchors are only used to measure."""
    path = pdir(area) / "base.pt"
    if path.exists() and not force:
        return torch.load(path, weights_only=False)
    from scripts.detect.qc import apply_qc, load_qc
    from scripts.detect.terrain import cached_water_possible
    from scripts.detect.tune import build_labels
    from scripts.detect.validate import reference_on_grid
    dev = dev or device()
    st = load_preview_stack(area)
    T, _, H, W = st.x.shape
    qc = load_qc(area)
    x, valid_det, qc_done = apply_qc(st.x, st.valid, st.dates, qc)
    # dates whose HH failed QC: HH was replaced by the median of the other dates -> not evaluable for water
    hh_bad = [d for d, ch in qc_done.items() if "HH" in [c.upper() for c in ch]]
    water_ok = cached_water_possible(area, (H, W))
    out = ops.run_pipeline(x, valid_det, "preview", dev=dev, water_ok=water_ok)
    raw = st.x[:, 0]
    floor_share = [float(((raw[t] <= FLOOR) & out["valid"][t].cpu()).sum() / max(1, int(out["valid"][t].sum())))
                   for t in range(T)]
    ref, _ = reference_on_grid(area, st.bounds, H, W)
    fp = out["valid"].all(0).cpu()
    lab = build_labels(ref, fp)
    meta = json.loads((ROOT / "data/raw/previews" / f"T{st.area['track']}_F{st.area['frame']}" / "meta.json").read_text())
    bw = sorted({b for p in meta["passes"] for b in p.get("bandwidth", [])})
    w_, s_, e_, n_ = st.bounds
    b = {"area": area, "dates": st.dates, "bounds": st.bounds, "km2": st.km2_per_px,
         "L": out["L"].cpu().float(), "valid": out["valid"].cpu(), "water_fixed": out["water"].cpu(),
         "water_ok": None if water_ok is None else water_ok.cpu().bool(), "floor_share": floor_share,
         "qc_done": qc_done, "hh_bad": hh_bad, "lab": {k: v.cpu() for k, v in lab.items()},
         "occ": ref["occ_mean"].cpu(), "ext": ref["ext_frac"].cpu(),
         "occ90": ref["occ_ge90_frac"].cpu(), "seas12": ref["seas12_frac"].cpu(),
         "bandwidth": bw, "direction": st.area.get("direction"), "lat": 0.5 * (s_ + n_)}
    del out, x, valid_det, st
    _free()
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(b, path)
    return b


def _free():
    import gc
    gc.collect()
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()


# ============================================================================ diagnosis (uses the reference)
def auc(pos, neg, n=60000, gen=None):
    """Area under the ROC curve for 'pos darker than neg' (Mann-Whitney, ties half), on <= n samples each."""
    gen = gen or torch.Generator().manual_seed(SEED)
    if pos.numel() == 0 or neg.numel() == 0:
        return float("nan")
    p = pos[torch.randint(0, pos.numel(), (min(n, pos.numel()),), generator=gen)] if pos.numel() > n else pos
    q = neg[torch.randint(0, neg.numel(), (min(n, neg.numel()),), generator=gen)] if neg.numel() > n else neg
    allv = torch.cat([p, q]).double()
    r = _avg_rank(allv)
    rp = r[:p.numel()].sum()
    u = rp - p.numel() * (p.numel() + 1) / 2          # = #(p > q) + 0.5 #ties
    return float(1 - u / (p.numel() * q.numel()))     # P(p < q)


def _avg_rank(v):
    o = torch.argsort(v)
    s = v[o]
    r = torch.empty_like(v)
    r[o] = torch.arange(1, v.numel() + 1, dtype=v.dtype)
    # ties -> mean rank
    uniq, inv, cnt = torch.unique(s, return_inverse=True, return_counts=True)
    if uniq.numel() < s.numel():
        csum = torch.zeros(uniq.numel(), dtype=v.dtype).index_add_(0, inv, r[o])
        r[o] = (csum / cnt)[inv]
    return r


def _logit(x, eps=0.01):
    return torch.log((x + eps) / (1 - x + eps))


def oracle_ba(pos, neg, n=60000):
    """Best balanced accuracy of ANY single threshold 'darker = water' (the ceiling of a 1-D brightness rule)."""
    gen = torch.Generator().manual_seed(SEED)
    if pos.numel() == 0 or neg.numel() == 0:
        return float("nan"), float("nan")
    p = pos[torch.randint(0, pos.numel(), (min(n, pos.numel()),), generator=gen)]
    q = neg[torch.randint(0, neg.numel(), (min(n, neg.numel()),), generator=gen)]
    thr = torch.quantile(torch.cat([p, q]).float(), torch.linspace(0, 1, 401))
    tpr = (p[None] < thr[:, None]).float().mean(1)
    tnr = (q[None] >= thr[:, None]).float().mean(1)
    ba = 0.5 * (tpr + tnr)
    i = int(ba.argmax())
    return float(ba[i]), float(thr[i])


def diagnose(b):
    """Per date and pooled: where water (perm anchors) and land (never anchors) sit in HH / HV, and how
    separable they are. Also observable-without-reference scene statistics (percentiles, floor share)."""
    L, valid, lab = b["L"], b["valid"], b["lab"]
    T = L.shape[0]
    q = torch.tensor([0.01, 0.05, 0.10, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99])
    rows = []
    for t in range(T):
        v = valid[t]
        hh, hv = L[t, 0], L[t, 1]
        d = {"date": b["dates"][t], "hh_bad": b["dates"][t] in b["hh_bad"], "floor_share": b["floor_share"][t]}
        sub = lambda a, m: a[m].float()
        sh = sub(hh, v)
        d["scene_hh_q"] = _qs(sh, q)
        d["scene_hv_q"] = _qs(sub(hv, v), q)
        pw, nw = lab["perm"] & v, lab["never"] & v
        for name, m in (("water", pw), ("land", nw)):
            d[f"{name}_n"] = int(m.sum())
            d[f"{name}_hh_q"] = _qs(sub(hh, m), torch.tensor([0.05, 0.25, 0.5, 0.75, 0.95])) if m.any() else None
            d[f"{name}_hv_q"] = _qs(sub(hv, m), torch.tensor([0.05, 0.25, 0.5, 0.75, 0.95])) if m.any() else None
        if pw.sum() >= 50 and nw.sum() >= 50:
            wh, lh, wv, lv = hh[pw], hh[nw], hv[pw], hv[nw]
            d["auc_hh"] = auc(wh, lh)
            d["auc_hv"] = auc(wv, lv)
            d["auc_sum"] = auc(_logit(wh) + _logit(wv), _logit(lh) + _logit(lv))
            zw, zl = _logit(wh), _logit(lh)
            d["dprime_hh"] = float((zl.mean() - zw.mean()) / (0.5 * (zw.var() + zl.var())).sqrt())
            d["oracle_ba_hh"], d["oracle_thr_hh"] = oracle_ba(wh, lh)
            d["land_below_water_p95"] = float((lh < torch.quantile(wh.float(), 0.95)).float().mean())
            d["fixed_seed_hit"] = float((wh < ops.KNOBS["preview"]["water_hh_strong"]).float().mean())
            d["fixed_weak_hit"] = float((wh < ops.KNOBS["preview"]["water_hh_weak"]).float().mean())
            d["hv_limit_hit"] = float((wv >= ops.KNOBS["preview"]["water_hv_max"]).float().mean())
        rows.append(d)
    pooled = {}
    keys = [k for k in ("auc_hh", "auc_hv", "auc_sum", "dprime_hh", "oracle_ba_hh", "land_below_water_p95",
                        "fixed_seed_hit", "fixed_weak_hit", "hv_limit_hit") if k in rows[0] or any(k in r for r in rows)]
    ok = [r for r in rows if not r["hh_bad"]]
    for k in keys:
        vals = [r[k] for r in ok if k in r and r[k] == r[k]]
        if vals:
            pooled[k] = {"median": float(np.median(vals)), "min": float(np.min(vals)), "max": float(np.max(vals))}
    fpv = valid.all(0)
    frac_ref = float(((b["occ"] >= 50) & fpv).sum() / max(1, int(fpv.sum())))
    return {"per_date": rows, "pooled": pooled, "n_perm": int(lab["perm"].sum()), "n_never": int(lab["never"].sum()),
            "ref_water_frac_occ50": frac_ref, "bandwidth": b["bandwidth"], "lat": b["lat"], "direction": b["direction"]}


def _qs(v, q):
    if v.numel() == 0:
        return None
    if v.numel() > 2_000_000:
        v = v[:: v.numel() // 2_000_000 + 1]
    return [round(float(a), 4) for a in torch.quantile(v.float(), q)]




# ============================================================================ knobs
# Defaults = the recommended configuration (docs/eda/crossscene.md): s_strong / s_weak / s_other_max calibrated on
# Tonle Sap + Sevan only; valley_depth_max, mode_max_frac, land_q chosen on the grid GRID over all 15 scenes (their
# out-of-sample quality is the leave-one-scene-out result); everything else set a priori.
CKNOBS = {
    "eps_logit": 0.01,         # preview feature u = log((L+eps)/(1-L+eps)); gamma0 feature = dB as is
    "dead_level": 0.006,       # input pixels <= this in BOTH channels on EVERY date AND connected to the frame
                               # border are unimaged corners (Rondonia), not water; dropped from `valid`
    # --- dark-mode (valley) test on the season composite ---------------------------------------
    "hist_bins": 160,          # histogram of the standardised season composite between its 0.1 and 99.9 %
    "hist_sigma": 2.0,         # Gaussian smoothing (bins)
    "valley_depth_max": 0.7,   # valley density / smaller neighbouring peak <= 0.7 -> a real dark mode exists
    "mode_min_px": 300,        # the dark mode must hold >= 300 px (~10 km2 at 180 m): independent of frame size
    "mode_max_frac": 0.4,      # ... and <= 40 % of the footprint (else it is a land class, e.g. pasture in HV)
    # --- seeds (method "seeded") -------------------------------------------------------------
    "seed_stable_frac": 0.7,   # seed pixel must be below the valley on >= 70 % of its valid dates
    "seed_open_k": 3,          # opening (px) before the size test: no 1-px filaments of dark land
    "seed_min_px": 30,         # a seed component must have >= 30 px (~1 km2)
    "seed_area_min_px": 100,   # has_water also needs >= 100 seed px in total (~3 km2)
    # --- per-date thresholds in scene-relative units s = (f - seed level)/(land level - seed level)
    "s_strong": 0.447,         # s < s_strong: surely water (hysteresis seed)    } both = where the old fixed
    "s_weak": 0.635,           # s < s_weak: water if connected to a seed       } 0.06 / 0.12 sit in Tonle Sap
                               #   and Sevan, in scene units (calibrate_seeded; results.json "calibration")
    "land_q": 0.25,            # land level b_t = this quantile of the land pixels on date t (the darker land)
    "s_other_max": 0.936,      # the channel not used by the feature must be < s_other_max in its own s units,
                               # with the land MEDIAN as its land level (= old HV < 0.35 in Tonle Sap + Sevan)
    "s_seed_keep": 0.70,       # a stable seed pixel stays water on a date while s < 0.70 (windy dates)
    "k_spread": 3.0,           # strong threshold >= seed level + 3 seed spreads (capped by the weak one)
    "seeds_only": False,       # True: grow ONLY from the permanent-water seeds (no new isolated water bodies)
    "min_blob_px": 4,          # as ops: drop specks < 4 px
    # --- method "relative": land-mode standardisation, then the old hysteresis in z units
    "rel_z_strong": -2.223,     # calibrate_relative() on Tonle Sap + Sevan (see results.json "calibration")
    "rel_z_weak": -1.711,
    "rel_z_hv": -0.935,
    # --- method "gmm": per-scene mixture with a relative component rule
    "gmm_k": 5,
    "gmm_n": 60000,
    "gmm_alpha": 0.35,         # component is water if it lies within 35 % of the way from the darkest comp to land
    "gmm_sep_min": 3.0,        # darkest comp must be >= 3 land standard deviations below land (along that line)
    "gmm_p": 0.5,              # posterior P(water) >= 0.5 -> water
}
METHODS = ["fixed", "relative", "gmm", "seeded"]


def feat(L, source="preview", eps=None):
    """Monotone 'log-like' feature per channel: logit of preview brightness (the preview stretch is close to
    logarithmic, so the logit is close to affine in dB), dB unchanged for calibrated gamma0."""
    if source == "gamma0":
        return L.float()
    eps = CKNOBS["eps_logit"] if eps is None else eps
    x = L.float().clamp(0, 1)
    return torch.log((x + eps) / (1 - x + eps))


def otsu_t(v, bins=256):
    """Otsu threshold of a 1-D sample (torch); NaN for < 10 values."""
    v = v.flatten().float()
    v = v[torch.isfinite(v)]
    if v.numel() < 10:
        return float("nan")
    if v.numel() > 400_000:
        v = v[:: v.numel() // 400_000 + 1]
    return ops.otsu(v.cpu(), bins)


def robust(v):
    """(median, 1.4826 * MAD) of a 1-D sample."""
    v = v.flatten().float()
    v = v[torch.isfinite(v)]
    if v.numel() > 1_000_000:
        v = v[:: v.numel() // 1_000_000 + 1]
    m = v.median()
    return float(m), float(1.4826 * (v - m).abs().median())


def season_composite(u, valid):
    """Per-pixel median over valid dates of u [T,C,H,W] -> [C,H,W] (NaN where never valid)."""
    x = torch.where(valid[:, None], u, torch.full_like(u, float("nan")))
    return x.nanmedian(0).values


def drop_dead(L, valid, level):
    """valid minus unimaged corners: <= level in both channels on every date, connected to the frame border."""
    dead = (L.amax(dim=(0, 1)) <= level)
    border = torch.zeros_like(dead)
    border[0, :] = border[-1, :] = border[:, 0] = border[:, -1] = True
    dead = ops.reconstruct((dead & border)[None], dead[None])[0]
    return valid & ~dead[None], dead


def standardise(u, fp, med):
    """Per-channel robust scene standardisation from the season composite: z = (u - median) / IQR.
    Invariant to any per-channel affine change of u (gain and offset of the stretch, or of dB calibration)."""
    ms, ss = [], []
    for c in range(u.shape[1]):
        v = med[c][fp]
        v = v[torch.isfinite(v)]
        if v.numel() > 1_000_000:
            v = v[:: v.numel() // 1_000_000 + 1]
        qs = torch.quantile(v.float().cpu(), torch.tensor([0.25, 0.5, 0.75]))
        ms.append(float(qs[1]))
        ss.append(max(float(qs[2] - qs[0]), 1e-3))
    return (torch.tensor(ms, device=u.device).view(1, -1, 1, 1), torch.tensor(ss, device=u.device).view(1, -1, 1, 1))


def valleys(x, kn=None):
    """Local minima of the smoothed histogram of x (1-D). For each: position, depth = density at the valley /
    the smaller of the highest peaks left and right of it, n_left = pixels left of it, frac_left."""
    kn = dict(CKNOBS, **(kn or {}))
    x = x[torch.isfinite(x)].float().cpu()
    if x.numel() < 100:
        return []
    sub = x[:: max(1, x.numel() // 500_000)]
    lo, hi = [float(q) for q in torch.quantile(sub, torch.tensor([0.001, 0.999]))]
    if hi - lo < 1e-6:
        return []
    nb = kn["hist_bins"]
    h = torch.histc(x.clamp(lo, hi), nb, lo, hi).double()
    r = int(4 * kn["hist_sigma"])
    k = torch.arange(-r, r + 1).double()
    ker = torch.exp(-0.5 * (k / kn["hist_sigma"]) ** 2)
    ker /= ker.sum()
    hs = F.conv1d(F.pad(h[None, None], (r, r), mode="replicate"), ker[None, None])[0, 0]
    cum = h.cumsum(0)
    c = lo + (torch.arange(nb) + 1.0) * (hi - lo) / nb   # upper edge of bin i
    out = []
    for i in range(1, nb - 1):
        if hs[i] <= hs[i - 1] and hs[i] < hs[i + 1]:
            hl, hr = float(hs[:i].max()), float(hs[i:].max())
            out.append({"pos": float(c[i]), "depth": float(hs[i]) / max(min(hl, hr), 1e-12),
                        "n_left": int(cum[i]), "frac_left": float(cum[i] / cum[-1]),
                        "peak_left": float(lo + (int(hs[:i].argmax()) + 0.5) * (hi - lo) / nb)})
    return out


def dark_mode(z, fp, kn=None):
    """Is there a separate dark mode in the scene? Tries HH, HV and their mean (standardised season composite)
    and returns the feature + valley with the largest prominence = n_left * (1 - depth) among valleys that pass
    depth <= valley_depth_max, n_left >= mode_min_px and frac_left <= mode_max_frac (a deep valley with many
    pixels left of it). None if no feature has one (-> no water)."""
    kn = dict(CKNOBS, **(kn or {}))
    feats = {"HH": torch.tensor([1.0, 0.0]), "HV": torch.tensor([0.0, 1.0]), "mean": torch.tensor([0.5, 0.5])}
    best, tried = None, {}
    for name, w in feats.items():
        f = (z * w.view(-1, 1, 1).to(z.device)).sum(0)
        V = [d for d in valleys(f[fp], kn) if d["depth"] <= kn["valley_depth_max"]
             and d["n_left"] >= kn["mode_min_px"] and d["frac_left"] <= kn["mode_max_frac"]]
        tried[name] = [{k: round(v, 4) if isinstance(v, float) else v for k, v in d.items()} for d in V[:5]]
        for d in V:
            d["prominence"] = d["n_left"] * (1.0 - d["depth"])
            if best is None or d["prominence"] > best["prominence"]:
                best = dict(d, feature=name, w=w.tolist())
    return best, tried


# ============================================================================ method 3: seeded, self-calibrated
def find_seeds(u, valid, kn=None, water_ok=None):
    """Data-driven permanent-water seeds and the has-water test (no absolute brightness anywhere).

    Rule: z = per-channel (u - scene median) / scene IQR of the season (per-pixel median) composite.
    dark_mode() looks for a valley in the histogram of z_HH, z_HV or their mean; no valley -> has_water False.
    Seed = feature below the valley in the season composite AND on >= seed_stable_frac of valid dates
    (temporally stable) AND in an opened (seed_open_k) component of >= seed_min_px px AND water_ok.
    Land = footprint, outside the 5-px dilated seeds and above the valley.
    has_water = a dark mode exists AND >= seed_area_min_px seed px survive.
    """
    kn = dict(CKNOBS, **(kn or {}))
    fp = valid.any(0)
    med = season_composite(u, valid)
    m, s = standardise(u, fp, med)
    zmed = ((med[None] - m) / s)[0]
    ok = fp & torch.isfinite(zmed).all(0)
    mode, tried = dark_mode(zmed, ok, kn)
    diag = {"chan_median": m.flatten().tolist(), "chan_iqr": s.flatten().tolist(), "valleys": tried, "mode": mode}
    empty = torch.zeros_like(fp)
    if mode is None:
        return empty, ok, None, m, s, False, diag
    w = torch.tensor(mode["w"], device=u.device).view(1, -1, 1, 1)
    f0 = (zmed[None] * w).sum(1)[0]
    ft = (((u - m) / s) * w).sum(1)
    nv = valid.sum(0).clamp_min(1)
    stable = ((ft < mode["pos"]) & valid).sum(0).float() / nv >= kn["seed_stable_frac"]
    cand = ok & (f0 < mode["pos"]) & stable
    if water_ok is not None:
        cand = cand & water_ok.to(cand.device)
    if kn["seed_open_k"] > 1:
        cand = ops.opening(cand[None], kn["seed_open_k"])[0] & cand
    seeds = ops.remove_small(cand[None], kn["seed_min_px"])[0]
    land = ok & ~ops.dilate(seeds[None], 11)[0] & (f0 >= mode["pos"])
    has = bool(int(seeds.sum()) >= kn["seed_area_min_px"] and int(land.sum()) >= 100)
    a, b = robust(f0[seeds]) if seeds.any() else (float("nan"), 0.0), robust(f0[land])
    diag.update(seed_px=int(seeds.sum()), seed_level=a[0], seed_spread=a[1], land_level=b[0], land_spread=b[1],
                sep=(b[0] - a[0]) / max(b[1], 1e-3) if seeds.any() else 0.0,
                s_valley=(mode["pos"] - a[0]) / max(b[0] - a[0], 1e-3) if seeds.any() else float("nan"))
    return seeds, land, w, m, s, has, diag


def detect_seeded(u, valid, kn=None, water_ok=None):
    """Seeded region growing with per-date, scene-relative thresholds.

    f_t = w . (u_t - m)/s with the feature (HH, HV or mean) chosen by dark_mode(). Per date t:
    a_t = median f_t on the seeds, b_t = quantile land_q of f_t on land, s_t = (f_t - a_t)/(b_t - a_t)
    (0 = this scene's permanent water today, 1 = its (darker) land today).
    Guard: when the feature is one channel, the other channel o must satisfy (u_o - a_o)/(b_o - a_o) <
    s_other_max (b_o = land median): water is dark in both channels (the old 'HV < 0.35' in scene units).
    weak = s_weak;  strong_t = min(max(s_strong, k_spread * 1.4826 MAD(s_t|seeds)), weak)
    water_t = 8-connected reconstruction of {s_t < strong_t} OR {seed AND s_t < s_seed_keep}
              inside {s_t < weak} OR {seed AND s_t < s_seed_keep}; specks < min_blob_px removed.
    The seeds re-anchor every date: on a windy date the lake brightens, a_t rises with it, and the stable
    seed pixels stay water as long as they are still clearly darker than land (s_t < s_seed_keep).
    If the scene has no dark mode, every mask is empty (has_water False).
    """
    kn = dict(CKNOBS, **(kn or {}))
    T = u.shape[0]
    seeds, land, w, m, s, has, diag = find_seeds(u, valid, kn, water_ok)
    water = torch.zeros(valid.shape, dtype=torch.bool, device=u.device)
    sc = torch.full(valid.shape, float("nan"), device=u.device)
    thr = [None] * T
    if not has:
        return {"water": water, "score": sc, "has_water": False, "seeds": seeds, "thresholds": thr, "diag": diag}
    weak = kn["s_weak"]
    okw = valid if water_ok is None else valid & water_ok.to(valid.device)[None]
    wf = w.flatten()
    oc = None if bool((wf > 0).all()) else int((wf == 0).nonzero()[0])   # the channel NOT used by the feature
    for t in range(T):
        v = valid[t]
        if v.sum() < 100 or (seeds & v).sum() < 10 or (land & v).sum() < 10:
            continue
        f = (((u[t:t + 1] - m) / s) * w).sum(1)[0]
        a, b = _levels(f, seeds, land, v, kn["land_q"])
        st = (f - a) / max(b - a, 1e-3)
        spread = 1.4826 * float((st[seeds & v]).abs().median())
        strong = min(max(kn["s_strong"], kn["k_spread"] * spread), weak)
        keep = seeds & (st < kn["s_seed_keep"])
        allowed = (st < weak)
        if oc is not None:  # water is dark in BOTH channels: the other channel must not look like land
            ao, bo = _levels(u[t, oc], seeds, land, v, 0.5)
            allowed = allowed & ((u[t, oc] - ao) / max(bo - ao, 1e-3) < kn["s_other_max"])
        allowed = (allowed | keep) & okw[t]
        seed_t = (keep if kn["seeds_only"] else ((st < strong) | keep)) & allowed
        water[t] = ops.reconstruct(seed_t[None], allowed[None])[0]
        sc[t] = torch.where(v, st, sc[t])
        thr[t] = {"a_seed": a, "b_land": b, "s_strong": strong, "s_weak": weak, "seed_spread_s": spread,
                  "hh_weak": _equiv(m, s, w, a, b, weak, 0), "hh_strong": _equiv(m, s, w, a, b, strong, 0),
                  "hv_weak": _equiv(m, s, w, a, b, weak, 1)}
    water = ops.remove_small(water, kn["min_blob_px"])
    return {"water": water, "score": sc, "has_water": True, "seeds": seeds, "thresholds": thr, "diag": diag}


def _levels(x, seeds, land, v, q):
    """(seed level, land level) of x on one date: median over the seeds, quantile q over land."""
    a = float(x[seeds & v].median())
    lv = x[land & v].float()
    if lv.numel() > 1_000_000:
        lv = lv[:: lv.numel() // 1_000_000 + 1]
    return a, float(torch.quantile(lv.cpu(), q))


def _equiv(m, s, w, a, b, sval, c):
    """The score level a + sval (b - a), expressed as a brightness in channel c, assuming the two channels'
    z move together (z_c = f / sum w). For reporting the per-date thresholds in familiar units only."""
    f = a + sval * (b - a)
    z = f / float(w.sum())
    uu = float(m.flatten()[c]) + float(s.flatten()[c]) * z
    e = CKNOBS["eps_logit"]
    ex = math.exp(min(uu, 50.0))
    return round((ex * (1 + e) - e) / (1 + ex), 4)


def calibrate_seeded(bases, kn=None):
    """The old fixed thresholds (HH 0.06 / 0.12, HV 0.35) expressed in seeded scene units s, averaged over the
    dates of the calibration scenes (Tonle Sap + Sevan, both pick HH): s = (logit(thr) - a_t)/(b_t - a_t) with
    a_t the seed median and b_t the land quantile land_q of that channel on date t.
    Returns {s_strong, s_weak, s_other_max, per_scene}."""
    kn = dict(CKNOBS, **(kn or {}))
    acc = {"s_strong": [], "s_weak": [], "s_other_max": []}
    per = {}
    for b in bases:
        L, valid = b["L"], b["valid"]
        valid, _ = drop_dead(L, valid, kn["dead_level"])
        u = feat(L) * valid[:, None]
        seeds, land, w, m, s, has, diag = find_seeds(u, valid, kn, b.get("water_ok"))
        rows = []
        for t in range(u.shape[0]):
            v = valid[t]
            r = {}
            for key, thr, c in (("s_strong", 0.06, 0), ("s_weak", 0.12, 0), ("s_other_max", 0.35, 1)):
                a, bb = _levels(u[t, c], seeds, land, v, kn["land_q"] if c == 0 else 0.5)
                r[key] = (float(feat(torch.tensor(thr))) - a) / (bb - a)
                acc[key].append(r[key])
            rows.append(r)
        per[b["area"]] = {"feature": diag["mode"]["feature"] if diag["mode"] else None,
                          **{k: round(float(np.mean([r[k] for r in rows])), 3) for k in acc}}
    return {**{k: round(float(np.mean(v)), 3) for k, v in acc.items()}, "per_scene": per, "land_q": kn["land_q"]}


# ============================================================================ method 1: relative thresholds
def land_standardise(u, valid):
    """Per date and channel: land = pixels above Otsu(u_t,c); m = median(land), s = IQR(land);
    z = (u - m) / s. Returns z [T,C,H,W] and the (m, s) table."""
    z = torch.zeros_like(u)
    tab = []
    for t in range(u.shape[0]):
        row = []
        for c in range(u.shape[1]):
            v = u[t, c][valid[t]]
            if v.numel() < 100:
                row.append(None)
                continue
            th = otsu_t(v)
            land = v[v > th]
            sub = land[:: max(1, land.numel() // 1_000_000)].float().cpu()
            qs = torch.quantile(sub, torch.tensor([0.25, 0.5, 0.75]))
            mm, ss = float(qs[1]), max(float(qs[2] - qs[0]), 1e-3)
            z[t, c] = (u[t, c] - mm) / ss
            row.append((mm, ss))
        tab.append(row)
    return z, tab


def calibrate_relative(bases):
    """The old fixed thresholds (HH 0.06 / 0.12, HV 0.35) expressed in land-standardised units, averaged over
    the dates of the calibration scenes (Tonle Sap + Sevan). Returns {rel_z_strong, rel_z_weak, rel_z_hv}."""
    kn0 = ops.KNOBS["preview"]
    vals = {"rel_z_strong": [], "rel_z_weak": [], "rel_z_hv": []}
    for b in bases:
        u = feat(b["L"])
        _, tab = land_standardise(u, b["valid"])
        for row in tab:
            if row[0] is None:
                continue
            for key, thr, c in (("rel_z_strong", kn0["water_hh_strong"], 0), ("rel_z_weak", kn0["water_hh_weak"], 0),
                                ("rel_z_hv", kn0["water_hv_max"], 1)):
                mm, ss = row[c]
                vals[key].append((float(feat(torch.tensor(thr))) - mm) / ss)
    return {k: round(float(np.mean(v)), 3) for k, v in vals.items()}


def detect_relative(u, valid, kn=None, water_ok=None):
    """Scene-relative thresholds: land-mode standardisation per date and channel (land_standardise), then the
    existing rule in z units: hysteresis(z_HH; rel_z_strong, rel_z_weak) inside z_HV < rel_z_hv; specks removed.
    No has-water test (it always applies the same z thresholds)."""
    kn = dict(CKNOBS, **(kn or {}))
    z, tab = land_standardise(u, valid)
    ok = valid & (z[:, 1] < kn["rel_z_hv"])
    if water_ok is not None:
        ok = ok & water_ok.to(ok.device)[None]
    w = ops.hysteresis(z[:, 0], kn["rel_z_strong"], kn["rel_z_weak"], ok)
    w = ops.remove_small(w, kn["min_blob_px"])
    thr = []
    for row in tab:
        if row[0] is None:
            thr.append(None)
            continue
        inv = lambda zz, c: _inv_logit(row[c][0] + row[c][1] * zz)
        thr.append({"hh_strong": inv(kn["rel_z_strong"], 0), "hh_weak": inv(kn["rel_z_weak"], 0),
                    "hv_max": inv(kn["rel_z_hv"], 1)})
    return {"water": w, "has_water": bool(w.any()), "thresholds": thr, "diag": {}}


def _inv_logit(uu, e=None):
    e = CKNOBS["eps_logit"] if e is None else e
    ex = math.exp(min(uu, 50.0))
    return round((ex * (1 + e) - e) / (1 + ex), 4)


# ============================================================================ method 2: per-scene mixture
def detect_gmm(u, valid, kn=None, water_ok=None, seed=0):
    """Per-scene Gaussian mixture on (u_HH, u_HV) pooled over dates (scripts.detect.gmm.fit_gmm), with a
    RELATIVE water rule instead of gmm.RULE's absolute means:
      land comp L = the heaviest component; darkest comp D = argmin(mean_HH + mean_HV);
      position of comp k on the D->L line: t_k = (mu_k - mu_D).(mu_L - mu_D) / |mu_L - mu_D|^2;
      water comps = {k : t_k <= gmm_alpha};
      has_water: |mu_L - mu_D| / sd_L(along the line) >= gmm_sep_min and D != L.
    Water_t = P(water | x) >= gmm_p, specks removed."""
    from scripts.detect.gmm import fit_gmm
    kn = dict(CKNOBS, **(kn or {}))
    T = u.shape[0]
    X = u.permute(0, 2, 3, 1)[valid]
    gen = torch.Generator().manual_seed(SEED + seed)
    Xs = X[torch.randint(0, X.shape[0], (min(kn["gmm_n"], X.shape[0]),), generator=gen, device="cpu").to(X.device)]
    g = fit_gmm(Xs.cpu(), kn["gmm_k"], n_init=3, seed=seed)
    mu, cov, wts = g.means.float(), g.covs.float(), g.weights.float()
    Lk = int(wts.argmax())
    Dk = int(mu.sum(1).argmin())
    d = mu[Lk] - mu[Dk]
    dn = float(d.norm())
    sep = dn / math.sqrt(float(d @ cov[Lk] @ d) / max(dn ** 2, 1e-9)) if dn > 0 else 0.0
    tk = ((mu - mu[Dk]) @ d) / max(dn ** 2, 1e-9)
    wcomp = tk <= kn["gmm_alpha"]
    has = bool(Dk != Lk and sep >= kn["gmm_sep_min"])
    water = torch.zeros(valid.shape, dtype=torch.bool, device=u.device)
    pw = torch.zeros(valid.shape, dtype=torch.float32, device=u.device)
    if has:
        for t in range(T):
            x = u[t].permute(1, 2, 0).reshape(-1, 2)
            p = g.posterior(x.cpu().double())[:, wcomp].sum(1).float().reshape(valid.shape[1:]).to(u.device)
            pw[t] = p * valid[t]
        water = (pw >= kn["gmm_p"]) & valid
        if water_ok is not None:
            water = water & water_ok.to(water.device)[None]
        water = ops.remove_small(water, kn["min_blob_px"])
    diag = {"means": mu.tolist(), "weights": wts.tolist(), "land": Lk, "darkest": Dk, "sep": sep,
            "t": tk.tolist(), "water_comps": wcomp.tolist()}
    return {"water": water, "p_water": pw, "has_water": has, "thresholds": [None] * T, "diag": diag}


# ============================================================================ integration entry points
def adaptive_knobs(L, valid, water_ok=None, source="preview", kn=None):
    """Per-scene, per-date thresholds of the seeded method (no detection masks): has_water, the chosen
    feature, the valley, seed/land levels and, per date, the strong/weak thresholds in scene units and their
    HH/HV brightness equivalents."""
    r = detect_water_adaptive(L, valid, water_ok=water_ok, method="seeded", source=source, kn=kn)
    return {"has_water": r["has_water"], "thresholds": r["thresholds"], "diag": r["diag"]}


def detect_water_adaptive(L, valid, water_ok=None, method="seeded", source="preview", kn=None):
    """Integration entry. L [T,2,H,W], valid [T,H,W] exactly as ops.run_pipeline returns them ("L", "valid").
    Returns dict(water [T,H,W] bool, p_water (gmm only) or score, thresholds per date, has_water, diag).
    method: "seeded" (recommended), "relative", "gmm", or "fixed" (ops.water_masks, for comparison)."""
    kn = dict(CKNOBS, **(kn or {}))
    if method == "fixed":
        w = ops.water_masks(L, valid, ops.KNOBS[source])
        if water_ok is not None:
            w = w & water_ok.to(w.device)[None]
        return {"water": w, "has_water": bool(w.any()), "thresholds": [None] * L.shape[0], "diag": {}}
    valid2, dead = drop_dead(L, valid, kn["dead_level"]) if source == "preview" else (valid, None)
    u = feat(L, source) * valid2[:, None]
    fn = {"seeded": detect_seeded, "relative": detect_relative, "gmm": detect_gmm}[method]
    r = fn(u, valid2, kn, water_ok)
    r["water"] = r["water"] & valid2
    r["diag"]["dead_px"] = 0 if dead is None else int(dead.sum())
    return r


# ============================================================================ evaluation (uses the reference)
def _largest(m):
    """Pixel count of the largest 8-connected component of each [N,H,W] mask."""
    if not m.any():
        return [0] * m.shape[0]
    lab = ops.label_components(m.to(device())).cpu()
    out = []
    for i in range(m.shape[0]):
        l = lab[i][m[i]]
        out.append(int(torch.bincount(l).max()) if l.numel() else 0)
    return out


def evaluate(b, water, n_boot=200):
    """Anchor metrics over evaluable pixel-dates (dates whose HH passed QC). perm = JRC permanent water
    (2-px shore band removed), never = never water within 3 px. Block bootstrap (20 px blocks) 95 % intervals."""
    from scripts.detect.tune import avg_rank, wpearson
    lab, valid, km2 = b["lab"], b["valid"], b["km2"]
    T, H, W = water.shape
    E = [t for t in range(T) if b["dates"][t] not in b["hh_bad"] and valid[t].any()]
    w = water[E].cpu() & valid[E]
    perm, never, amb = lab["perm"], lab["never"], lab["amb"]
    nE = len(E)
    cp, cn, ca = w.sum(0)[perm].double(), w.sum(0)[never].double(), w.sum(0)[amb].double()
    yy, xx = torch.meshgrid(torch.arange(H), torch.arange(W), indexing="ij")
    bid = (yy // BLOCK) * ((W + BLOCK - 1) // BLOCK) + xx // BLOCK
    bp, bn = bid[perm], bid[never]
    nb = int(bid.max()) + 1

    def metr(wb=None):
        wp = torch.ones_like(cp) if wb is None else wb[bp]
        wn = torch.ones_like(cn) if wb is None else wb[bn]
        sp, sn = float(wp.sum()), float(wn.sum())
        tp, fp = float((cp * wp).sum()), float((cn * wn).sum())
        rec = tp / (sp * nE) if sp > 0 else float("nan")
        fpr = fp / (sn * nE) if sn > 0 else float("nan")
        fn = sp * nE - tp
        return {"recall": rec, "fpr": fpr, "balanced_acc": 0.5 * (rec + 1 - fpr) if sp > 0 else float("nan"),
                "iou": tp / max(tp + fp + fn, 1e-9) if sp > 0 else float("nan")}
    m = metr()
    m["fp_km2_per_date"] = float(cn.sum()) / max(nE, 1) * km2
    m["n_perm"], m["n_never"], m["n_dates"] = int(perm.sum()), int(never.sum()), nE
    gen = torch.Generator().manual_seed(SEED)
    blocks = torch.cat([bp, bn]).unique()
    boot = {k: [] for k in ("recall", "fpr", "balanced_acc", "iou")}
    for _ in range(n_boot):
        pick = blocks[torch.randint(0, blocks.numel(), (blocks.numel(),), generator=gen)]
        wb = torch.bincount(pick, minlength=nb).double()
        mm = metr(wb)
        for k in boot:
            boot[k].append(mm[k])
    for k, v in boot.items():
        a = np.array(v, float)
        a = a[np.isfinite(a)]
        m[k + "_ci"] = [float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5))] if a.size else None
    m["rank_rho"] = (wpearson(avg_rank(b["occ"][amb].double()), avg_rank(ca), None)
                     if int(amb.sum()) > 50 and float(ca.std()) > 0 else float("nan"))
    m["km2_per_date"] = [round(float((water[t].cpu() & valid[t]).sum()) * km2, 1) for t in range(T)]
    m["recall_per_date"] = [round(float(water[t].cpu()[perm & valid[t]].float().mean()), 4) if (perm & valid[t]).any() else None
                            for t in range(T)]
    m["fpr_per_date"] = [round(float(water[t].cpu()[never & valid[t]].float().mean()), 5) if (never & valid[t]).any() else None
                         for t in range(T)]
    m["largest_km2_per_date"] = [round(v * km2, 1) for v in _largest(water.cpu() & valid)]
    return m


def reference_lake_km2(b):
    """Area of the largest connected body of JRC 'permanent' water (>= 50 % of the pixel with occurrence >= 90 %)."""
    fp = b["valid"].all(0)
    ref = (b["occ90"] >= 0.5) & fp
    return round(_largest(ref[None])[0] * b["km2"], 1)


# ============================================================================ protocol: grid, leave-one-scene-out
# Structural knobs of the seeded method that Tonle Sap + Sevan cannot pin down (both scenes have a deep,
# obvious water mode). For each land_q the s thresholds are re-calibrated on Tonle Sap + Sevan only.
GRID = {"land_q": [0.5, 0.25, 0.1], "valley_depth_max": [0.6, 0.7, 0.8], "mode_max_frac": [0.4, 0.7]}
V0 = {"land_q": 0.5, "valley_depth_max": 0.75, "mode_max_frac": 0.7}   # a-priori values (before any other scene)
# App mode per scene (a judgement about what the scene is FOR, not a fit): "lake" = grow only from the
# permanent-water seeds (seeds_only=True; no isolated new water bodies), "flood" = also new water bodies.
MODE = {"sevan": "lake", "urmia": "lake", "poyang": "lake", "lake-chad": "lake", "pantanal": "lake",
        "nile-delta": "lake"}
TIE = 1e-4      # objective differences below this are ties
MIN_PERM = 300   # a scene's accuracy is judged on balanced accuracy only with >= 300 permanent-water anchor px
WORK_BA, WORK_FPR = 0.95, 0.01     # "works": BA >= 0.95 (anchored scenes) / false-alarm rate <= 1 % (others)
BAD_BA, BAD_FPR = 0.80, 0.05       # "fails badly": BA < 0.80 or false-alarm rate > 5 %


def grid_configs(cal_cache):
    """All grid knob sets, each with s_strong / s_weak calibrated on Tonle Sap + Sevan for its land_q."""
    out = []
    for q in GRID["land_q"]:
        cal = cal_cache[str(q)]
        for d in GRID["valley_depth_max"]:
            for f in GRID["mode_max_frac"]:
                out.append({"land_q": q, "valley_depth_max": d, "mode_max_frac": f,
                            "s_strong": cal["s_strong"], "s_weak": cal["s_weak"]})
    return out


def calibrations(force=False):
    """s thresholds per land_q (and the relative-method z thresholds) from Tonle Sap + Sevan; cached."""
    path = pdir("sevan") / "calibration.json"
    if path.exists() and not force:
        return json.loads(path.read_text())
    bases = [prepare(a) for a in TUNE_AREAS]
    cal = {str(q): calibrate_seeded(bases, {"land_q": q}) for q in sorted(set(GRID["land_q"] + [V0["land_q"]]))}
    cal["relative"] = calibrate_relative(bases)
    del bases
    _free()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cal, indent=1))
    return cal


def score(m):
    """Per-scene objective: balanced accuracy when the scene has >= MIN_PERM anchor px, else specificity."""
    if m["n_perm"] >= MIN_PERM and m["balanced_acc"] == m["balanced_acc"]:
        return m["balanced_acc"]
    return 1.0 - m["fpr"]


def verdict(m):
    if m["n_perm"] >= MIN_PERM:
        if m["balanced_acc"] >= WORK_BA and m["fpr"] <= BAD_FPR:
            return "works"
        if m["balanced_acc"] < BAD_BA or m["fpr"] > BAD_FPR:
            return "fails"
        return "partial"
    if m["fpr"] <= WORK_FPR:
        return "works"
    return "fails" if m["fpr"] > BAD_FPR else "partial"


def _cfg_key(c):
    return f"q{c['land_q']}_d{c['valley_depth_max']}_f{c['mode_max_frac']}"


def run_grid(area, cal, dev):
    """Stage 1 for one area: every grid config of the seeded method, light metrics (no bootstrap). Cached."""
    path = pdir(area) / "grid.json"
    b = prepare(area)
    L, v = b["L"].to(dev), b["valid"].to(dev)
    wok = None if b["water_ok"] is None else b["water_ok"].to(dev)
    out = {}
    for c in grid_configs(cal):
        r = detect_water_adaptive(L, v, water_ok=wok, method="seeded", kn=c)
        m = evaluate(b, r["water"].cpu(), n_boot=0)
        out[_cfg_key(c)] = {k: m[k] for k in ("balanced_acc", "recall", "fpr", "fp_km2_per_date", "n_perm")}
        out[_cfg_key(c)]["has_water"] = r["has_water"]
        del r
    del L, v, b
    _free()
    path.write_text(json.dumps(out, indent=1))
    return out


def loso(grids, cal):
    """Leave-one-scene-out: for each held-out scene pick the grid config with the best mean score() on the
    other scenes; report the held-out scene's metrics under that config. Also the config chosen on all."""
    cfgs = grid_configs(cal)
    keys = [_cfg_key(c) for c in cfgs]
    areas = [a for a in AREAS if a in grids]

    def J(subset, k):
        return float(np.mean([score(grids[a][k]) for a in subset]))
    def prior_dist(c):  # ties (the training scenes cannot tell configs apart) go to the a-priori values V0
        return (abs(c["land_q"] - V0["land_q"]) / 0.4 + abs(c["valley_depth_max"] - V0["valley_depth_max"]) / 0.2
                + abs(c["mode_max_frac"] - V0["mode_max_frac"]) / 0.3)

    def best(subset):
        js = {k: J(subset, k) for k in keys}
        top = max(js.values())
        cands = [k for k in keys if js[k] >= top - TIE]
        return min(cands, key=lambda k: prior_dist(cfgs[keys.index(k)]))
    res = {}
    for a in areas:
        others = [x for x in areas if x != a]
        kbest = best(others)
        res[a] = {"config": kbest, **grids[a][kbest]}
    kall = best(areas)
    return res, kall, cfgs[keys.index(kall)], {k: J(areas, k) for k in keys}


HIST_BINS = np.linspace(-4.7, 4.7, 95)   # logit-brightness bins for the distribution figure


def _hist(x):
    x = x.float().cpu().numpy()
    h, _ = np.histogram(np.clip(x, HIST_BINS[0], HIST_BINS[-1] - 1e-6), HIST_BINS)
    return (h / max(h.sum(), 1)).round(6).tolist()


def _small(x, n=360):
    """Downsample a [H,W] or [3,H,W] array for figures (area mean)."""
    x = torch.as_tensor(x).float()
    sq = x.dim() == 2
    if sq:
        x = x[None]
    H, W = x.shape[-2:]
    f = max(1, math.ceil(max(H, W) / n))
    y = F.avg_pool2d(x[None].nan_to_num(0), f, f, ceil_mode=True)[0]
    return (y[0] if sq else y).numpy().astype(np.float16)


def run_final(area, cfgs, cal, dev):
    """Stage 3 for one area: fixed, relative, gmm, seeded(v0), seeded(final) with bootstrap metrics; products
    and figure data. cfgs = {"seeded_v0": knobs, "seeded": knobs}."""
    b = prepare(area)
    L, v = b["L"].to(dev), b["valid"].to(dev)
    wok = None if b["water_ok"] is None else b["water_ok"].to(dev)
    T = L.shape[0]
    res = {"area": area, "dates": b["dates"], "km2_per_px": b["km2"], "hh_bad": b["hh_bad"],
           "qc_excluded": b["qc_done"], "terrain_mask": b["water_ok"] is not None,
           "ref_lake_km2": reference_lake_km2(b), "diagnosis": diagnose(b), "methods": {}}
    masks = {}
    rel_kn = {k: cal["relative"][k] for k in ("rel_z_strong", "rel_z_weak", "rel_z_hv")}
    runs = [("fixed", None, {}), ("relative", "relative", rel_kn), ("gmm", "gmm", {}),
            ("seeded_v0", "seeded", cfgs["seeded_v0"]), ("seeded", "seeded", cfgs["seeded"]),
            ("seeded_lake", "seeded", dict(cfgs["seeded"], seeds_only=True))]
    for name, meth, kn in runs:
        t0 = time.time()
        if meth is None:
            r = {"water": b["water_fixed"], "has_water": bool(b["water_fixed"].any()), "thresholds": [None] * T, "diag": {}}
        else:
            r = detect_water_adaptive(L, v, water_ok=wok, method=meth, kn=kn)
        w = r["water"].cpu() & b["valid"]
        m = evaluate(b, w)
        m["has_water"] = r["has_water"]
        m["verdict"] = verdict(m)
        m["score"] = score(m)
        m["thresholds"] = r["thresholds"]
        m["diag"] = _jsonable(r["diag"])
        m["seconds"] = round(time.time() - t0, 1)
        res["methods"][name] = m
        masks[name] = w
        if name == "seeded":
            seeds = r["seeds"].cpu()
            score_t = r["score"].cpu().half()
        del r
        print(f"  {name:10s} has={int(m['has_water'])} BA={m['balanced_acc']:.3f} rec={m['recall']:.3f} "
              f"fpr={m['fpr']:.4f} fp={m['fp_km2_per_date']:.0f} km2/date  {m['verdict']}  ({m['seconds']}s)", flush=True)
    # anchors' brightness distributions (evaluable dates pooled)
    E = [t for t in range(T) if b["dates"][t] not in b["hh_bad"]]
    u = feat(b["L"][E])
    vv = b["valid"][E]
    hist = {}
    for nm, msk in (("scene", vv), ("water", vv & b["lab"]["perm"][None]), ("land", vv & b["lab"]["never"][None])):
        hist[nm] = {"HH": _hist(u[:, 0][msk]) if msk.any() else None, "HV": _hist(u[:, 1][msk]) if msk.any() else None}
    res["hist"] = hist
    # products for the app / lead
    pd_ = pdir(area)
    app = masks["seeded_lake"] if MODE.get(area) == "lake" else masks["seeded"]
    torch.save({"dates": b["dates"], "bounds": b["bounds"], "water": app, "water_flood_mode": masks["seeded"], "seeds": seeds,
                "score": score_t, "has_water": res["methods"]["seeded"]["has_water"],
                "thresholds": res["methods"]["seeded"]["thresholds"], "knobs": dict(CKNOBS, **cfgs["seeded"]),
                "water_fixed": masks["fixed"], "water_relative": masks["relative"], "water_gmm": masks["gmm"],
                "water_seeded_v0": masks["seeded_v0"], "water_lake_mode": masks["seeded_lake"],
                "mode": MODE.get(area, "flood"), "method": "seeded"}, pd_ / "products.pt")
    med = torch.where(b["valid"][:, None], b["L"], torch.full_like(b["L"], float("nan"))).nanmedian(0).values
    rgb = torch.stack([med[0], med[1], med[0]]).clamp(0, 1).nan_to_num(0)
    fd = {"rgb": _small(rgb), "valid": _small(b["valid"].all(0).float()),
          "perm": _small(b["lab"]["perm"].float()), "never": _small(b["lab"]["never"].float()),
          "occ": _small(b["occ"] / 100.0), "seeds": _small(seeds.float()), "dates": b["dates"]}
    for k, w in masks.items():
        fd["freq_" + k] = _small(w.float().mean(0))
    tsel = int(np.argmax(res["methods"]["seeded"]["km2_per_date"])) if T else 0
    fd["t_sel"] = tsel
    fd["date_rgb"] = _small(torch.stack([b["L"][tsel, 0], b["L"][tsel, 1], b["L"][tsel, 0]]).clamp(0, 1))
    for k in ("fixed", "seeded"):
        fd["date_" + k] = _small(masks[k][tsel].float())
    torch.save(fd, pd_ / "figdata.pt")
    (pd_ / "results.json").write_text(json.dumps(_jsonable(res), indent=1))
    del L, v, b, masks
    _free()
    return res


def _jsonable(x):
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if torch.is_tensor(x):
        return _jsonable(x.tolist())
    if isinstance(x, (np.floating, np.integer)):
        x = x.item()
    if isinstance(x, float):
        return None if not math.isfinite(x) else round(x, 5)
    return x


# ============================================================================ stretch-invariance demonstration
STRETCHES = {
    "as delivered": lambda x: x,
    "gamma 0.6 (darks lifted)": lambda x: x.clamp(0, 1) ** 0.6,
    "gamma 1.6 (darks crushed)": lambda x: x.clamp(0, 1) ** 1.6,
    "no floor: 0.15 + 0.8 x": lambda x: 0.15 + 0.8 * x.clamp(0, 1),
    "logit gain 0.7, offset +0.8": lambda x: _inv_logit_t(0.7 * feat(x) + 0.8),
    "square root then 0.9 x": lambda x: 0.9 * x.clamp(0, 1).sqrt(),
}


def _inv_logit_t(z, e=None):
    e = CKNOBS["eps_logit"] if e is None else e
    ex = torch.exp(z)
    return ((ex * (1 + e) - e) / (1 + ex)).clamp(0, 1)


def stretch_demo(area, cfg, dev):
    """Same filtered scene under several monotone display stretches: IoU of each method's water (all dates)
    with its own result on the delivered preview, plus the area. Fixed = ops.water_masks (+ terrain)."""
    b = prepare(area)
    L0, v = b["L"].to(dev), b["valid"].to(dev)
    wok = None if b["water_ok"] is None else b["water_ok"].to(dev)
    out, ref = {}, {}
    for name, fn in STRETCHES.items():
        Ls = fn(L0) * v[:, None]
        row = {}
        for meth, kn in (("fixed", None), ("seeded", cfg)):
            r = detect_water_adaptive(Ls, v, water_ok=wok, method=meth, kn=kn)
            w = r["water"].cpu()
            if name == "as delivered":
                ref[meth] = w
            inter = float((w & ref[meth]).sum())
            uni = float((w | ref[meth]).sum())
            row[meth] = {"iou_vs_delivered": round(inter / uni, 4) if uni else 1.0,
                         "km2_mean": round(float(w.sum()) / w.shape[0] * b["km2"], 1)}
        out[name] = row
        print(f"  stretch {name:30s} fixed IoU {row['fixed']['iou_vs_delivered']:.3f} seeded IoU {row['seeded']['iou_vs_delivered']:.3f}", flush=True)
    del L0, v, b
    _free()
    return out


# ============================================================================ report: results.json, scene_knobs.json, figures
METH_LABEL = {"fixed": "fixed (current)", "relative": "1 relative", "gmm": "2 mixture", "seeded_v0": "3 seeded (v0)",
              "seeded_loso": "3 seeded (LOSO)", "seeded": "3 seeded (final)", "seeded_lake": "3 seeded, lake mode",
              "app": "3 seeded, mode per scene"}
METH_COL = {"fixed": "#888888", "relative": "#e0a030", "gmm": "#9a62c4", "seeded_v0": "#7fb3e0",
            "seeded_loso": "#3a7cc4", "seeded": "#1f5fa8", "seeded_lake": "#0b8a6e", "app": "#d6455d"}


def _save(fig, name):
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    fig.savefig(p, dpi=90 if name.startswith("05") else 110)
    import matplotlib.pyplot as plt
    plt.close(fig)
    kb = p.stat().st_size / 1024
    if kb > 340:  # keep every figure < 350 KB: palette PNG, then shrink until it fits
        from PIL import Image
        im0 = Image.open(p).convert("RGB")
        scale = 1.0
        while True:
            im = im0 if scale == 1.0 else im0.resize((int(im0.width * scale), int(im0.height * scale)), Image.LANCZOS)
            im.quantize(colors=96, method=Image.Quantize.MEDIANCUT).save(p, optimize=True)
            if p.stat().st_size / 1024 <= 340 or scale < 0.5:
                break
            scale *= 0.85
    return p


def summarise(R, methods):
    """Scenes working / failing, mean and worst score per method."""
    out = {}
    for me in methods:
        rows = [R[a][me] for a in AREAS if a in R and me in R[a]]
        anchored = [r for r in rows if r["n_perm"] >= MIN_PERM]
        bas = [r["balanced_acc"] for r in anchored]
        out[me] = {"n_scenes": len(rows), "works": sum(r["verdict"] == "works" for r in rows),
                   "fails": sum(r["verdict"] == "fails" for r in rows),
                   "mean_ba_anchored": float(np.mean(bas)) if bas else None,
                   "worst_ba_anchored": float(np.min(bas)) if bas else None,
                   "n_anchored": len(anchored),
                   "mean_fpr": float(np.mean([r["fpr"] for r in rows])),
                   "worst_fpr": float(np.max([r["fpr"] for r in rows])),
                   "mean_fp_km2_per_date": float(np.mean([r["fp_km2_per_date"] for r in rows])),
                   "worst_fp_km2_per_date": float(np.max([r["fp_km2_per_date"] for r in rows]))}
    return out


def report(cal):
    """Collect per-area results -> docs/eda/crossscene/{results.json, scene_knobs.json, *.png}."""
    import matplotlib
    matplotlib.use("Agg")
    per = {a: json.loads((pdir(a) / "results.json").read_text()) for a in AREAS if (pdir(a) / "results.json").exists()}
    cfgs, sel = choose(cal)
    R = {a: dict(per[a]["methods"]) for a in per}
    if sel:
        for a in R:
            lo = sel["loso"][a]
            R[a]["seeded_loso"] = dict(lo, verdict=verdict(lo), score=score(lo))
    for a in R:
        R[a]["app"] = R[a]["seeded_lake" if MODE.get(a) == "lake" else "seeded"]
    methods = ["fixed", "relative", "gmm", "seeded_v0"] + (["seeded_loso"] if sel else []) + ["seeded", "seeded_lake", "app"]
    summ = summarise(R, methods)
    demo = None
    dp = pdir("sevan") / "stretch_demo.json"
    if dp.exists():
        demo = json.loads(dp.read_text())
    res = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "calibration_tonle_sevan": cal, "configs": cfgs,
           "grid": GRID, "protocol": {"v0": "a-priori structural knobs, s thresholds calibrated on Tonle Sap + Sevan",
                                      "loso": "structural knobs chosen on the 14 other scenes (grid), s thresholds "
                                              "always from Tonle Sap + Sevan", "work_bar": [WORK_BA, WORK_FPR],
                                      "bad_bar": [BAD_BA, BAD_FPR], "min_perm_px": MIN_PERM},
           "summary": summ, "stretch_demo_sevan": demo,
           "loso": None if not sel else {"held_out": sel["loso"], "chosen_on_all": sel["chosen_on_all"],
                                         "J_all": sel["J_all"]},
           "areas": {a: {"diagnosis": per[a]["diagnosis"], "ref_lake_km2": per[a]["ref_lake_km2"],
                         "dates": per[a]["dates"], "hh_bad": per[a]["hh_bad"], "terrain_mask": per[a]["terrain_mask"],
                         "methods": {m: {k: v for k, v in R[a][m].items() if k not in ("thresholds",)} for m in R[a]}}
                     for a in per}}
    (OUT).mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(_jsonable(res), indent=1))
    knobs = {"method": "seeded", "integration": "crossscene.detect_water_adaptive(out['L'], out['valid'], "
                                               "water_ok=water_ok, method='seeded', kn=dict(scene_knobs['knobs'], "
                                               "seeds_only=scene_knobs['areas'][area]['seeds_only']))",
             "note": "per_date thresholds are OUTPUTS (what the method chose), not inputs; the method recomputes "
                     "them from the scene. hh_/hv_ values are brightness equivalents for reading only.",
             "knobs": _jsonable(dict(CKNOBS, **cfgs["seeded"])), "areas": {}}
    for a in per:
        ms = per[a]["methods"]["seeded"]
        mode_ = MODE.get(a, "flood")
        mk = "seeded_lake" if MODE.get(a) == "lake" else "seeded"
        rec = _recommend(R[a], per[a]["diagnosis"], mk)
        knobs["areas"][a] = _jsonable({"mode": mode_, "seeds_only": mode_ == "lake", "has_water": ms["has_water"], "feature": (ms["diag"].get("mode") or {}).get("feature"),
                                       "valley": ms["diag"].get("mode"), "seed_px": ms["diag"].get("seed_px"),
                                       "recommended_water_source": rec[0], "why": rec[1],
                                       "verdict_seeded": ms["verdict"], "verdict_lake_mode": per[a]["methods"]["seeded_lake"]["verdict"], "verdict_fixed": per[a]["methods"]["fixed"]["verdict"],
                                       "per_date": {d: th for d, th in zip(per[a]["dates"], ms["thresholds"])}})
    (OUT / "scene_knobs.json").write_text(json.dumps(knobs, indent=1))
    figures(per, R, methods, demo)
    print(json.dumps(_jsonable(summ), indent=1))
    return res


def _recommend(r, diag, key="seeded"):
    """Per-scene water source for the app, from the measured verdicts (the reference decides only what we SAY)."""
    s, f = r[key], r["fixed"]
    sep = (diag.get("pooled") or {}).get("oracle_ba_hh", {}).get("median")
    if s["n_perm"] >= MIN_PERM:
        if s["verdict"] == "works":
            return "seeded", f"seeded BA {s['balanced_acc']:.3f}, false alarms {100 * s['fpr']:.2f} %"
        if f["verdict"] == "works" and f["balanced_acc"] > s["balanced_acc"]:
            return "fixed", f"fixed BA {f['balanced_acc']:.3f} > seeded {s['balanced_acc']:.3f}"
        return "none (caution)", (f"no method 'works' (BA >= {WORK_BA} and false alarms <= {100 * BAD_FPR:.0f} %): "
                                  f"seeded BA {s['balanced_acc']:.3f} / {100 * s['fpr']:.2f} %, fixed BA "
                                  f"{f['balanced_acc']:.3f} / {100 * f['fpr']:.2f} %; best single HH threshold "
                                  f"(oracle) BA {sep:.3f}")
    if s["verdict"] == "works":
        return "seeded", f"no permanent-water anchors; false alarms {100 * s['fpr']:.2f} % on never-water"
    return "none (caution)", f"no permanent-water anchors; false alarms {100 * s['fpr']:.2f} % on never-water"


def _lg(b):
    e = CKNOBS["eps_logit"]
    return math.log((b + e) / (1 - b + e))


def figures(per, R, methods, demo):
    import matplotlib.pyplot as plt
    methods = [m for m in ("fixed", "relative", "gmm", "seeded_v0", "seeded_loso", "seeded", "app") if m in methods]
    areas = [a for a in AREAS if a in per]
    ticks = [0.004, 0.02, 0.06, 0.12, 0.3, 0.6, 0.9]
    xc = 0.5 * (HIST_BINS[1:] + HIST_BINS[:-1])
    # 1 distributions
    fig, axs = plt.subplots(3, 5, figsize=(17, 9.5), sharex=True)
    for ax, a in zip(axs.flat, areas):
        h = per[a]["hist"]
        ax.fill_between(xc, h["scene"]["HH"], step="mid", color="#cccccc", label="whole scene HH")
        if h["water"]["HH"]:
            ax.plot(xc, h["water"]["HH"], drawstyle="steps-mid", color="#1f5fa8", lw=1.6, label="permanent water HH")
            ax.plot(xc, h["water"]["HV"], drawstyle="steps-mid", color="#1f5fa8", lw=1, ls=":", label="permanent water HV")
        ax.plot(xc, h["land"]["HH"], drawstyle="steps-mid", color="#d0731f", lw=1.6, label="never water HH")
        ax.plot(xc, h["land"]["HV"], drawstyle="steps-mid", color="#d0731f", lw=1, ls=":", label="never water HV")
        for t_ in (0.06, 0.12):
            ax.axvline(_lg(t_), color="k", lw=0.7, ls="--")
        d = per[a]["diagnosis"]["pooled"]
        auc_s = f"AUC HH {d['auc_hh']['median']:.2f} HV {d['auc_hv']['median']:.2f}" if "auc_hh" in d else "no permanent-water anchors"
        ax.set_title(f"{TITLES[a]}\n{auc_s}", fontsize=9)
        ax.set_yscale("log")
        ax.set_ylim(1e-4, 1)
        ax.set_xticks([_lg(t_) for t_ in ticks])
        ax.set_xticklabels([str(t_) for t_ in ticks], fontsize=7)
    axs.flat[0].legend(fontsize=6, loc="upper right")
    fig.suptitle("Where water and land sit in each preview (filtered brightness, log-odds axis; dashed = fixed thresholds 0.06 / 0.12)", fontsize=11)
    fig.supxlabel("preview brightness (0..1)")
    fig.tight_layout()
    _save(fig, "01_distributions.png")
    # 2 separability heat map
    cols = [("auc_hh", "AUC HH"), ("auc_hv", "AUC HV"), ("auc_sum", "AUC HH+HV"), ("oracle_ba_hh", "best BA\none HH thr."),
            ("fixed_weak_hit", "water < 0.12\n(fixed reach)")]
    M = np.full((len(areas), len(cols) + 3), np.nan)
    for i, a in enumerate(areas):
        d = per[a]["diagnosis"]
        for j, (k, _) in enumerate(cols):
            if k in d["pooled"]:
                M[i, j] = d["pooled"][k]["median"]
        rows = [r for r in d["per_date"] if not r["hh_bad"]]
        M[i, len(cols)] = np.median([r["floor_share"] for r in rows])
        wq = [r["water_hh_q"][2] for r in rows if r.get("water_hh_q")]
        lq = [r["land_hh_q"][2] for r in rows if r.get("land_hh_q")]
        M[i, len(cols) + 1] = np.median(wq) if wq else np.nan
        M[i, len(cols) + 2] = np.median(lq) if lq else np.nan
    labels = [c[1] for c in cols] + ["share at\ndisplay floor", "water HH\nmedian", "land HH\nmedian"]
    fig, ax = plt.subplots(figsize=(11, 7.5))
    show = M.copy()
    show[:, len(cols):] = np.nan
    ax.imshow(np.clip((show - 0.5) / 0.5, 0, 1), cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
    for i in range(M.shape[0]):
        for j in range(M.shape[1]):
            ax.text(j, i, "–" if np.isnan(M[i, j]) else f"{M[i, j]:.3f}" if j >= len(cols) else f"{M[i, j]:.2f}",
                    ha="center", va="center", fontsize=8)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_yticks(range(len(areas)))
    ax.set_yticklabels([f"{TITLES[a]} ({per[a]['diagnosis']['n_perm']} px)" for a in areas], fontsize=8)
    ax.set_title("Separability of permanent water (JRC anchors) from never-water land, per scene (median over dates)", fontsize=10)
    fig.tight_layout()
    _save(fig, "02_separability.png")
    # 3 balanced accuracy, anchored scenes
    anch = [a for a in areas if per[a]["diagnosis"]["n_perm"] >= MIN_PERM]
    fig, ax = plt.subplots(figsize=(13, 4.8))
    wbar = 0.8 / len(methods)
    for k, me in enumerate(methods):
        vals = [R[a][me]["balanced_acc"] or 0.5 for a in anch]
        xs = np.arange(len(anch)) + (k - (len(methods) - 1) / 2) * wbar
        ax.bar(xs, vals, wbar, color=METH_COL[me], label=METH_LABEL[me])
        ci = [R[a][me].get("balanced_acc_ci") for a in anch]
        for x_, c_ in zip(xs, ci):
            if c_:
                ax.plot([x_, x_], c_, color="k", lw=0.7)
    ax.axhline(WORK_BA, color="k", lw=0.8, ls="--")
    ax.set_ylim(0.45, 1.01)
    ax.set_xticks(range(len(anch)))
    ax.set_xticklabels([TITLES[a] for a in anch], fontsize=8)
    ax.set_ylabel("balanced accuracy on JRC anchors")
    ax.set_title(f"Water vs land on independent anchors (scenes with >= {MIN_PERM} permanent-water px); dashed = 'works' bar", fontsize=10)
    ax.legend(fontsize=7, ncol=len(methods), loc="lower left")
    fig.tight_layout()
    _save(fig, "03_balanced_accuracy.png")
    # 4 false water
    fig, ax = plt.subplots(figsize=(13, 4.8))
    for k, me in enumerate(methods):
        vals = [max(100 * R[a][me]["fpr"], 1e-3) for a in areas]
        xs = np.arange(len(areas)) + (k - (len(methods) - 1) / 2) * wbar
        ax.bar(xs, vals, wbar, color=METH_COL[me], label=METH_LABEL[me])
    ax.axhline(100 * WORK_FPR, color="k", lw=0.8, ls="--")
    ax.axhline(100 * BAD_FPR, color="r", lw=0.8, ls="--")
    ax.set_yscale("log")
    ax.set_ylim(1e-3, 100)
    ax.set_xticks(range(len(areas)))
    ax.set_xticklabels([TITLES[a] for a in areas], fontsize=8, rotation=30, ha="right")
    ax.set_ylabel("false water: % of never-water pixel-dates")
    ax.set_title("False water on never-water land (JRC: no water 1984-2021 within 540 m); dashed 1 % and 5 %", fontsize=10)
    ax.legend(fontsize=7, ncol=len(methods))
    fig.tight_layout()
    _save(fig, "04_false_water.png")
    # 5 maps
    show_a = [a for a in ("pantanal", "lake-chad", "nile-delta", "sevan", "tonle-sap") if a in per]
    fig, axs = plt.subplots(len(show_a), 4, figsize=(14, 3.1 * len(show_a)))
    axs = np.atleast_2d(axs)
    for i, a in enumerate(show_a):
        fd = torch.load(pdir(a) / "figdata.pt", weights_only=False)
        rgb = np.moveaxis(fd["rgb"].astype(np.float32), 0, -1)
        axs[i, 0].imshow(rgb)
        axs[i, 0].set_title(f"{TITLES[a]}: season median (HH, HV, HH)", fontsize=9)
        for j, me in ((1, "fixed"), (2, "seeded"), (3, "seeded_lake")):
            im = np.dstack([rgb.mean(-1) * 0.5] * 3)
            fq = fd["freq_" + me].astype(np.float32)
            im[..., 2] = np.maximum(im[..., 2], fq)
            im[..., 1] = np.maximum(im[..., 1], 0.6 * fq)
            axs[i, j].imshow(np.clip(im, 0, 1))
            axs[i, j].contour(fd["perm"].astype(np.float32), [0.5], colors="#ff3030", linewidths=0.9)
            m = R[a][me]
            ba = "" if m["n_perm"] < MIN_PERM else f"BA {m['balanced_acc']:.3f}, "
            axs[i, j].set_title(f"{METH_LABEL[me]}\n{ba}false {100 * m['fpr']:.2f} %", fontsize=8)
        for ax in axs[i]:
            ax.axis("off")
    fig.suptitle("Water frequency over the season (cyan); red outline = JRC permanent-water anchors", fontsize=10)
    fig.tight_layout()
    _save(fig, "05_maps.png")
    # 6 thresholds per scene and date
    fig, ax = plt.subplots(figsize=(13, 5))
    for k, a in enumerate(areas):
        th = per[a]["methods"]["seeded"]["thresholds"]
        ys = [t["hh_weak"] for t in th if t]
        ys2 = [t["hh_strong"] for t in th if t]
        if not ys:
            ax.text(k, 0.0045, "no water", rotation=90, fontsize=7, ha="center")
            continue
        feat_ = (per[a]["methods"]["seeded"]["diag"].get("mode") or {}).get("feature", "")
        ax.scatter([k + (j - len(ys) / 2) * 0.06 for j in range(len(ys))], ys, s=10, color="#1f5fa8")
        ax.scatter([k + (j - len(ys2) / 2) * 0.06 for j in range(len(ys2))], ys2, s=10, color="#7fb3e0", marker="v")
        ax.text(k, 0.9, feat_, ha="center", fontsize=7)
    ax.axhline(0.12, color="k", ls="--", lw=0.8)
    ax.axhline(0.06, color="k", ls=":", lw=0.8)
    ax.set_yscale("log")
    ax.set_ylim(0.003, 1.2)
    ax.set_xticks(range(len(areas)))
    ax.set_xticklabels([TITLES[a] for a in areas], fontsize=8, rotation=30, ha="right")
    ax.set_ylabel("HH-equivalent threshold (brightness)")
    ax.set_title("Seeded method: weak (dots) and strong (triangles) thresholds chosen per scene and date, as HH brightness; "
                 "black = fixed 0.12 / 0.06; label = feature used", fontsize=9)
    fig.tight_layout()
    _save(fig, "06_thresholds.png")
    # 7 stretch demo
    if demo:
        names = list(demo)
        fig, ax = plt.subplots(figsize=(10, 4.2))
        xs = np.arange(len(names))
        ax.bar(xs - 0.2, [demo[n]["fixed"]["iou_vs_delivered"] for n in names], 0.4, color=METH_COL["fixed"], label="fixed thresholds")
        ax.bar(xs + 0.2, [demo[n]["seeded"]["iou_vs_delivered"] for n in names], 0.4, color=METH_COL["seeded"], label="seeded (scene-relative)")
        for x_, n in zip(xs, names):
            ax.text(x_ - 0.2, 0.02, f"{demo[n]['fixed']['km2_mean']:.0f} km²", rotation=90, fontsize=7, color="w")
            ax.text(x_ + 0.2, 0.02, f"{demo[n]['seeded']['km2_mean']:.0f} km²", rotation=90, fontsize=7, color="w")
        ax.set_xticks(xs)
        ax.set_xticklabels(names, fontsize=8, rotation=15)
        ax.set_ylim(0, 1.05)
        ax.set_ylabel("IoU with its own result on the delivered preview")
        ax.set_title("Lake Sevan re-displayed with different monotone stretches: same scene, same water?", fontsize=10)
        ax.legend(fontsize=8, loc="lower right")
        fig.tight_layout()
        _save(fig, "07_stretch_invariance.png")


# ============================================================================ CLI
def choose(cal):
    """Final knob sets: v0 (a priori, s calibrated on Tonle Sap + Sevan) and the config chosen on all scenes'
    grid results (its out-of-sample quality is the leave-one-scene-out result)."""
    grids = {a: json.loads((pdir(a) / "grid.json").read_text()) for a in AREAS if (pdir(a) / "grid.json").exists()}
    v0 = dict(V0, s_strong=cal[str(V0["land_q"])]["s_strong"], s_weak=cal[str(V0["land_q"])]["s_weak"])
    if len(grids) < len(AREAS):
        print(f"  grid results for {len(grids)}/{len(AREAS)} areas only: LOSO skipped, using v0")
        return {"seeded_v0": v0, "seeded": v0}, None
    lo, kall, call, Jall = loso(grids, cal)
    return {"seeded_v0": v0, "seeded": call}, {"loso": lo, "chosen_on_all": kall, "J_all": Jall, "grids": grids}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("areas", nargs="*")
    ap.add_argument("--report", action="store_true", help="only rebuild results/figures from cached per-area results")
    ap.add_argument("--skip-grid", action="store_true", help="reuse cached grid.json files")
    ap.add_argument("--recalibrate", action="store_true")
    a = ap.parse_args(argv)
    areas = a.areas or AREAS
    dev = device()
    cal = calibrations(force=a.recalibrate)
    if not a.report:
        if not a.skip_grid:
            for ar in areas:
                t0 = time.time()
                run_grid(ar, cal, dev)
                print(f"grid {ar}: {time.time() - t0:.0f}s", flush=True)
        cfgs, sel = choose(cal)
        print("final seeded knobs:", cfgs["seeded"])
        for ar in areas:
            print(f"== {ar}", flush=True)
            run_final(ar, cfgs, cal, dev)
        if "sevan" in areas:
            demo = stretch_demo("sevan", cfgs["seeded"], dev)
            (pdir("sevan") / "stretch_demo.json").write_text(json.dumps(demo, indent=1))
    report(cal)


if __name__ == "__main__":
    main()
