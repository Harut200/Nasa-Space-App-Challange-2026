"""Before / after / difference evidence for a place whose story the water layers do not show.

Shared machinery for scripts/detect/burn.py (fires, layer ids burn_*) and for any other season place (layer ids ev_*):

    .venv/bin/python -m scripts.detect.evidence <area> [--no-mask] [--dry]

Per place the config (CONFIG in scripts/detect/burn.py and scripts/detect/evidence_places.py, or areas/<id>/area.json under
"evidence") names the channel hh|hv and the sign, chosen from the documented story, not by search, and the date groups:
  1. speckle filter (3x3 boxcar; the Quegan multi-temporal filter is optional and off because it flattens a step) and per-date gain normalisation;
  2. BEFORE / AFTER = per-pixel medians of the valid passes of the two date groups (a fire: before / after the ignition;
     otherwise explicit date lists); DIFF = AFTER - BEFORE, smoothed with a boxcar;
  3. CONTROL = the same difference built from two halves of the BEFORE group (or an explicit quiet interval). The noise
     is a robust Gaussian 99.9 % level of the control's same-signed change (median + 3.09 x 1.4826 MAD); connected
     patches >= min_km2 of signed change above it are kept, the one nearest the reference position (or the largest) reported;
  4. numbers: area of changed radar echo (NOT a burnt-area / deforestation / harvest measurement), mean change, the control's
     mean at the same pixels, rank against equal-size discs elsewhere in the frame, persistence on each later pass,
     threshold sensitivity (0.5x, 1x, 2x), and a per-pass inside / ring series.
Adds to data/processed/<area>/products/ (idempotent): <p>_before.tif, <p>_after.tif, <p>_diff.tif, <p>_scar|mask.tif,
<p>.json, entries first in layers.json, facts + a top-level key in timeseries.json; updates areas/<id>/area.json.
Method and results: docs/eda/burn.md and docs/eda/evidence.md.

Units. The previews are display brightness 0..1, not backscatter. DB_PER_UNIT = 30 assumes the display stretch spans
about 30 dB; that is an assumption we cannot check, so every number is given in brightness units first and the dB-like
figure is only indicative.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import pathlib
import sys

import numpy as np
import torch
from scipy import ndimage as ndi

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.detect import ops  # noqa: E402

DB_PER_UNIT = 30.0          # assumed width of the preview stretch in dB (unverified)
CH = {"hh": 0, "hv": 1}
CH_NAME = {"hh": "co-polarised (HH)", "hv": "cross-polarised (HV)"}
FLAME = "#ff5a1f"
DARKER, BRIGHTER = "#2166ac", "#b2182b"

KINDS = {
    "burn": {"prefix": "burn", "mask": "burn_scar", "when": "the fire", "was": "Before the fire", "after": "After the fire",
             "mask_label": "Where the radar echo changed after the fire", "in": "Radar echo inside the burn",
             "out": "Radar echo outside the burn", "ref": "the published fire position",
             "diff_label": "Radar echo change after the fire", "dark": "darker after the fire", "bright": "brighter after the fire"},
    "ev": {"prefix": "ev", "mask": "ev_mask", "when": "the change", "was": "Before", "after": "After",
           "mask_label": "Where the radar echo changed", "in": "Radar echo inside the changed patch",
           "out": "Radar echo outside the changed patch", "ref": "the reference position",
           "diff_label": "Radar echo change", "dark": "darker after", "bright": "brighter after"},
}
SERIES_DIFF = "Difference"
UNIT = "relative brightness (0–100, uncalibrated)"
LAYER_IDS = ("burn_before", "burn_after", "burn_diff", "burn_scar", "ev_before", "ev_after", "ev_diff", "ev_mask")
DEFAULTS = {"smooth": 9, "box": 3, "q": None, "min_km2": 10.0, "ring_km": (1.5, 6.0), "disc_km": (3.0, 15.0),
            "export_mask": True, "thr_floor": 0.02, "kind": "burn"}


# ----------------------------------------------------------------------------- date bookkeeping
def day_label(d):
    x = dt.date.fromisoformat(d)
    return f"{x.day} {x.strftime('%b')}"


def span(ds):
    return day_label(ds[0]) if len(ds) == 1 else f"{day_label(ds[0])}–{day_label(ds[-1])}"


def split_dates(dates, ignition=None, between=None, excluded=(), before=None, after=None):
    """(before, after) index lists. Passes before the ignition (or before the first date of the interval where the
    change appears) are BEFORE, passes after it (or after the interval's second date) are AFTER; excluded dates and
    any pass inside the interval holding the ignition are left out."""
    ex = set(excluded)
    if before is not None:  # explicit date groups (a place with no ignition date)
        return ([i for i, d in enumerate(dates) if d in set(before) and d not in ex],
                [i for i, d in enumerate(dates) if d in set(after) and d not in ex])
    lo, hi = (ignition, ignition) if ignition else tuple(between)
    before = [i for i, d in enumerate(dates) if d <= lo and d not in ex]
    after = [i for i, d in enumerate(dates) if d >= hi and d not in ex and d != ignition]
    return before, after


# ----------------------------------------------------------------------------- image operations
def prepare(x, valid, box=3, normalise=True, drop=(), quegan=False):
    """x [T,2,H,W] 0..1, valid [T,H,W] -> filtered, gain-normalised brightness and the cleaned valid mask.
    `drop`: pass indices (corrupt / QC-failed) made invalid BEFORE the filters, so they cannot leak into the others
    through the multi-temporal speckle filter or the gain normalisation."""
    valid = valid.clone()
    for t in drop:
        valid[t] = False
    x, valid = ops.clean_input(x.float(), valid)
    # Quegan's multi-temporal filter is off by default: it averages each pixel's temporal ratios, which flattens a
    # permanent step (a planted 0.10 step came out at 0.067), so it would understate the change we report.
    xf = ops.quegan_filter(x, valid, box) if quegan else ops.box_mean(x, valid, box)
    if normalise:
        xf, _, _ = ops.normalise_dates(xf, valid, 0.2, mode="gain")
    return xf * valid[:, None], valid


def median_image(L, valid, idx):
    """Per-pixel median over the passes in idx (common footprint: valid on all of them) -> (image, valid)."""
    v = valid[idx].all(0)
    img = L[idx].median(0).values * v
    return img, v


def smooth(a, v, k):
    return ops.box_mean(a[None, None], v[None], k)[0, 0] * v if k > 1 else a * v


def control_halves(Lc, valid, before, groups=None):
    """Same construction on the pre-fire passes only: later half minus earlier half (first ceil(n/2) vs the rest);
    `groups` = (indices_a, indices_b) gives an explicit quiet interval instead."""
    h = (len(before) + 1) // 2
    a, b = groups if groups else (before[:h], before[h:])
    ia, va = median_image(Lc, valid, a)
    ib, vb = median_image(Lc, valid, b)
    return ib - ia, va & vb, a, b


def threshold_from_control(ctrl_s, v, sign, q=99.9, floor=0.0):
    """The q-th percentile of the control's change in the direction of the sign (noise level of the same test)."""
    vals = (sign * ctrl_s[v]).flatten().numpy()
    if q is None:  # robust: a Gaussian 99.9 % level from the median and MAD, blind to heavy tails (water edges, rain)
        med = float(np.median(vals))
        return float(max(med + 3.09 * 1.4826 * np.median(np.abs(vals - med)), floor))
    return float(max(np.percentile(vals, q), floor))


def detect(diff_s, v, sign, thr, km2, min_km2, spot_rc=None, pix_km=0.18, union=False):
    """Boolean mask of the connected patch (>= min_km2) of signed change above thr nearest spot_rc (0 distance when
    the spot lies inside), or None. Returns (mask, info)."""
    m = ((sign * diff_s) > thr) & v
    m = ndi.binary_fill_holes(m.numpy())
    lab, n = ndi.label(m, structure=np.ones((3, 3)))
    if n == 0:
        return None, {}
    sizes = np.bincount(lab.ravel())[1:]
    ok = [i + 1 for i, s in enumerate(sizes) if s * km2 >= min_km2]
    if not ok:
        return None, {}
    if union:  # every patch >= min_km2 (a place with many small changed blocks)
        return torch.from_numpy(np.isin(lab, ok)), {"n_patches": len(ok), "largest_km2": float(sizes[[i - 1 for i in ok]].max() * km2)}
    if spot_rc is None:
        best = max(ok, key=lambda i: sizes[i - 1])
    else:
        r0, c0 = spot_rc
        def dist(i):
            rr, cc = np.nonzero(lab == i)
            return float(np.hypot(rr - r0, cc - c0).min())
        best = min(ok, key=lambda i: (dist(i), -sizes[i - 1]))
    mask = lab == best
    return torch.from_numpy(mask), {"n_patches": len(ok), "largest_km2": float(sizes[[i - 1 for i in ok]].max() * km2)}


def disc_means(sig, v, r_px):
    """Mean of `sig` over a disc of radius r_px centred on every pixel (valid pixels only); NaN where < 90 % of the
    disc is valid. FFT convolution, zero padded."""
    r = int(math.ceil(r_px))
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    k = torch.from_numpy((yy ** 2 + xx ** 2 <= r_px ** 2).astype(np.float32))
    H, W = sig.shape
    s = (H + 2 * r, W + 2 * r)
    fk = torch.fft.rfft2(k, s=s)
    conv = lambda a: torch.fft.irfft2(torch.fft.rfft2(a, s=s) * fk, s=s)[r:r + H, r:r + W]
    num, cnt = conv(sig * v), conv(v.float())
    out = num / cnt.clamp_min(1e-6)
    out[cnt < 0.9 * k.sum()] = float("nan")
    return out


def disc_rank(signed, v, patch, r_px):
    """Where the patch's best disc (centre inside the patch) sits among discs of the same radius whose centre is
    farther than r_px from the patch. Returns (best value, percent of the other discs at least as strong, n others)."""
    dm = disc_means(signed, v, r_px)
    inside = patch & torch.isfinite(dm)
    if not inside.any():
        return float("nan"), float("nan"), 0
    best = float(dm[inside].max())
    far = torch.from_numpy(ndi.distance_transform_edt(~patch.numpy()) > r_px) & torch.isfinite(dm)
    others = dm[far]
    if others.numel() == 0:
        return best, float("nan"), 0
    return best, 100.0 * float((others >= best).float().mean()), int(others.numel())


def ring_mask(patch, v, r_in_px, r_out_px):
    """Valid land between r_in and r_out pixels from the patch (not in it)."""
    d = ndi.distance_transform_edt(~patch.numpy())
    return torch.from_numpy((d > r_in_px) & (d <= r_out_px)) & v


def series(Lc, valid, patch, ring):
    """Per pass: mean channel brightness inside the patch and in the ring (None if the pass is invalid there)."""
    ins, out = [], []
    for t in range(Lc.shape[0]):
        a, b = patch & valid[t], ring & valid[t]
        ok = a.sum() > 0.5 * patch.sum() and b.sum() > 0.5 * ring.sum()
        ins.append(float(Lc[t][a].mean()) if ok else None)
        out.append(float(Lc[t][b].mean()) if ok else None)
    return ins, out


# ----------------------------------------------------------------------------- the analysis
def analyse(L, valid, dates, cfg, km2, spot_rc=None, pix_km=None, unusable=()):
    """The whole detector on a prepared stack. L [T,2,H,W] filtered brightness 0..1; cfg: channel, sign, ignition /
    ignition_between, excluded, smooth, q, min_km2, ring_km, disc_km. Returns a dict of numbers and arrays."""
    cfg = {**DEFAULTS, **cfg}
    c, sign = CH[cfg["channel"]], cfg["sign"]
    pix_km = pix_km or math.sqrt(km2)
    excluded = set(cfg.get("excluded", [])) | set(unusable)
    before, after = split_dates(dates, cfg.get("ignition"), cfg.get("ignition_between"), excluded,
                                cfg.get("before"), cfg.get("after"))
    groups = None
    if cfg.get("control"):
        groups = tuple([i for i, d in enumerate(dates) if d in set(g) and d not in excluded] for g in cfg["control"])
    if (len(before) < 2 and not groups) or not after or not before:
        raise ValueError(f"need >= 2 passes before (or an explicit control) and >= 1 after, got {len(before)} / {len(after)}")
    Lc = L[:, c]
    b_img, vb = median_image(Lc, valid, before)
    a_img, va = median_image(Lc, valid, after)
    v = vb & va
    k = cfg["smooth"]
    diff = (a_img - b_img) * v
    diff_s = smooth(diff, v, k)
    ctrl, vc, ca, cb = control_halves(Lc, valid, before, groups)
    vv = v & vc
    ctrl_s = smooth(ctrl * vv, vv, k)
    thr = threshold_from_control(ctrl_s, vv, sign, cfg["q"], cfg["thr_floor"])
    union = bool(cfg.get("all_patches"))
    patch, info = detect(diff_s, vv, sign, thr, km2, cfg["min_km2"], spot_rc, union=union)
    primary = detect(diff_s, vv, sign, thr, km2, cfg["min_km2"], spot_rc)[0] if union else patch  # rank: the nearest / largest one
    out = {"cfg": cfg, "before": before, "after": after, "control_a": ca, "control_b": cb, "thr": thr,
           "b_img": b_img, "a_img": a_img, "diff": diff, "diff_s": diff_s, "ctrl_s": ctrl_s, "v": vv, "patch": patch,
           "info": info, "primary": primary, "excluded": sorted(excluded), "ctrl_p99_9": thr}
    if patch is None:
        return out
    n = int(patch.sum())
    r_eq = math.sqrt(int(primary.sum()) / math.pi)
    d_lo, d_hi = (x / pix_km for x in cfg["disc_km"])
    r_disc = float(min(max(r_eq, d_lo), d_hi))
    ring = ring_mask(patch, vv, cfg["ring_km"][0] / pix_km, cfg["ring_km"][1] / pix_km)
    ins, outs = series(Lc, valid, patch, ring)
    sig = sign * diff_s
    best, pct, n_oth = disc_rank(sig, vv, primary, r_disc)
    per_pass = {}
    for t in after:
        ch = sign * (smooth(Lc[t] - b_img, vv, 1))
        per_pass[dates[t]] = {"patch": float(ch[patch].mean()), "ring": float(ch[ring].mean()) if ring.any() else None}
    sens = {}
    for f in (0.5, 1.0, 2.0):
        m, _ = detect(diff_s, vv, sign, thr * f, km2, cfg["min_km2"], spot_rc, union=union)
        sens[str(f)] = {"threshold": thr * f, "area_km2": float(m.sum() * km2) if m is not None else 0.0}
    # the smoothing widens a patch by about k/2 pixels, which would dilute its mean: report means over the core
    core = torch.from_numpy(ndi.binary_erosion(patch.numpy(), iterations=max(k // 2, 1)))
    if core.sum() < 0.2 * patch.sum():
        core = patch
    rr, cc = np.nonzero(primary.numpy())
    out.update({
        "area_km2": float(n * km2), "mean_change": float(diff[core].mean()), "control_mean": float(ctrl[core & vv].mean()),
        "control_frac_above": float(((sign * ctrl_s[core]) > thr).float().mean()), "core": core,
        "after_frac_above": float(((sign * diff_s[patch]) > thr).float().mean()),
        "rank_percentile": pct, "rank_n_discs": n_oth, "disc_radius_km": r_disc * pix_km, "disc_mean": best * sign,
        "sens": sens, "ring": ring, "series_in": ins, "series_out": outs, "per_pass": per_pass,
        "centroid_rc": (float(rr.mean()), float(cc.mean())), "ring_mean_change": float(diff[ring].mean()) if ring.any() else None,
    })
    return out


# ----------------------------------------------------------------------------- products
def ramp_rgba(img, v, lo, hi):
    g = ((img - lo) / (hi - lo)).clamp(0, 1).mul(255).round().to(torch.uint8)
    return torch.stack([g, g, g, v.to(torch.uint8) * 255])


def hexrgb(h):
    return [int(h[i:i + 2], 16) for i in (1, 3, 5)]


def diff_rgba(diff_s, v, thr, lim, dark=DARKER, bright=BRIGHTER):
    """Signed change -> RGBA: blue when darker, red when brighter, transparent below the control noise `thr`,
    full colour from 2 x thr. lim is only the legend range."""
    d = diff_s.numpy()
    a = np.where(np.abs(d) > thr, np.clip(0.4 + 0.6 * (np.abs(d) - thr) / max(0.5 * thr, 1e-6), 0, 1), 0)
    rgb = np.where((d < 0)[None], np.array(hexrgb(dark))[:, None, None], np.array(hexrgb(bright))[:, None, None])
    alpha = np.round(a * 255 * v.numpy()).astype(np.uint8)
    return np.concatenate([rgb.astype(np.uint8), alpha[None]])


def scar_rgba(patch):
    m = patch.numpy()
    edge = m & ~ndi.binary_erosion(m, iterations=1)
    rgb = np.array(hexrgb(FLAME), np.uint8)[:, None, None] * np.ones((1,) + m.shape, np.uint8)
    alpha = np.where(edge, 255, np.where(m, 120, 0)).astype(np.uint8)
    return np.concatenate([rgb, alpha[None]])


def layer_defs(res, dates, sign, channel, thr):
    K = KINDS[res["cfg"]["kind"]]
    p = K["prefix"]
    ch = channel.upper()
    b, a = res["before"], res["after"]
    lo, hi = res["grey"]
    lim = round(2.5 * thr, 3)
    ramp = {"type": "ramp", "unit": "relative brightness (0–100, uncalibrated)", "min": round(lo * 100), "max": round(hi * 100),
            "colors": ["#000000", "#ffffff"]}
    out = [
        {"id": f"{p}_before", "label": f"{K['was']} ({ch}, median of passes {span([dates[i] for i in b])})",
         "dated": False, "legend": dict(ramp)},
        {"id": f"{p}_after", "label": f"{K['after']} ({ch}, median of passes {span([dates[i] for i in a])})",
         "dated": False, "legend": dict(ramp)},
        {"id": f"{p}_diff", "label": f"{K['diff_label']} ({ch})", "dated": False,
         "legend": {"type": "diverging", "unit": "change in echo brightness (0–1 preview scale), after minus before",
                    "min": -lim, "max": lim, "colors": [DARKER, "#00000000", BRIGHTER],
                    "labels": [K["dark"], K["bright"]]}},
    ]
    if res.get("export_mask", True):
        out.append({"id": K["mask"], "label": K["mask_label"], "dated": False,
                    "legend": {"type": "classes", "items": [{"color": FLAME, "label": K["mask_label"]}]}})
    return out


def steps_text(res, dates, spot_dist_km):
    cfg = res["cfg"]
    K = KINDS[cfg["kind"]]
    ch = cfg["channel"]
    b, a = [dates[i] for i in res["before"]], [dates[i] for i in res["after"]]
    ca, cb = [dates[i] for i in res["control_a"]], [dates[i] for i in res["control_b"]]
    word = "rose" if res["mean_change"] > 0 else "fell"
    if cfg.get("all_patches"):
        where = ", summed over every changed patch in the frame"
    elif spot_dist_km is None:
        where = ""
    elif spot_dist_km <= 10:
        where = f" at {K['ref']}"
    else:
        where = f", {spot_dist_km:.0f} km from {K['ref']}"
    s = [f"1. Radar image {'before the fire' if cfg['kind'] == 'burn' else 'before'}, {span(b)} (median of {len(b)} passes, {ch.upper()} channel).",
         f"2. Radar image after, {span(a)} (median of {len(a)} pass{'es' if len(a) != 1 else ''}).",
         f"3. The difference: the echo {word} by about {abs(res['mean_change']):.2f} (0–1 brightness scale) inside a patch of "
         f"about {res['area_km2']:,.0f} km² of changed echo{where}."]
    cm = res["control_mean"]
    quiet = "before the fire" if cfg["kind"] == "burn" else "within the before group"
    ctrl_span = span(sorted(ca + cb))
    if res["control_frac_above"] <= 0.05:
        s.append(f"4. A check: between {ctrl_span}, {quiet}, the same test finds nothing there "
                 f"(mean change {cm:+.2f} in the same pixels).")
    else:
        s.append(f"4. A check: between {ctrl_span}, {quiet}, the same pixels change by {cm:+.2f} on average, "
                 f"and {100 * res['control_frac_above']:.0f} % of them cross the threshold, so the control is not clean.")
    n_above = sum(1 for d in res["per_pass"].values() if d["patch"] > res["thr"])
    N = len(res["per_pass"])
    s.append("5. Only one later pass is used, so whether it stays cannot be checked here." if N == 1 else
             "5. It stays: the patch is still there on every later pass." if n_above == N
             else f"5. On later passes the patch is above the threshold on {n_above} of {N}; not on "
                  + ", ".join(day_label(k) for k, d in res["per_pass"].items() if d["patch"] <= res["thr"]) + ".")
    return s


def method_note(res):
    k = res["cfg"]["smooth"]
    area_txt = {"burn": "not a burnt-area measurement", "ev": "not a measurement of land cover, crop or forest loss"}[res["cfg"]["kind"]]
    return (f"Preview brightness (uncalibrated, ~180 m), 3x3 boxcar + per-date gain "
            f"normalisation; AFTER minus BEFORE (medians), {k}x{k} boxcar; threshold = median + 3.09 robust sigma of the "
            f"same-signed change in a control built from two halves of a quiet interval (a Gaussian 99.9 % level that heavy "
            f"tails do not inflate); connected patches of at least {res['cfg']['min_km2']:.0f} km² kept, the one nearest the "
            f"reference position (else the largest) reported. The area is the area of changed radar echo, {area_txt}.")


def headline(res, dates):
    ch = CH_NAME[res["cfg"]["channel"]]
    ch = ch[0].upper() + ch[1:].replace("(hh)", "(HH)").replace("(hv)", "(HV)")
    word = "higher" if res["mean_change"] > 0 else "lower"
    tail = " after the fire" if res["cfg"]["kind"] == "burn" else " in the later passes"
    return (f"{ch} radar echo about {abs(res['mean_change']):.2f} {word} (0–1 brightness) inside a patch of changed echo "
            f"of about {res['area_km2']:,.0f} km²{tail}")


def run(area, config, export_mask=None, dry=False):
    from scripts.detect import run as detect_run
    from scripts.detect import qc as qcmod
    cfg = {**DEFAULTS, **config}
    acfg = json.loads((ROOT / "areas" / area / "area.json").read_text())
    cfg.update(acfg.get("evidence", {}))
    if export_mask is not None:
        cfg["export_mask"] = export_mask
    dates, x, valid, grid, km2 = detect_run.load_preview(area)
    qc = qcmod.load_qc(area)
    unusable = []
    if qc:
        unusable = [p["date"] for p in qc["passes"] if p["verdict"] == "fail" and cfg["channel"].upper() in (p["fail_channels"] or [])]
    drop = [i for i, d in enumerate(dates) if d in set(cfg.get("excluded", [])) | set(unusable)]
    L, v = prepare(x, valid, cfg.get("box", DEFAULTS["box"]), drop=drop, quegan=cfg.get("quegan", False))
    sp = ROOT / "areas" / area / "story.json"
    story = json.loads(sp.read_text()) if sp.exists() else {}
    lonlat = cfg.get("ref_lonlat")
    if cfg.get("ref_spot"):
        lonlat = next(s["lngLat"] for s in story["spots"] if s["id"] == cfg["ref_spot"])
    spot_rc = None
    if lonlat:
        w, s_, e, n = (acfg["bbox"])
        H, W = L.shape[-2:]
        spot_rc = (int((n - lonlat[1]) / (n - s_) * H), int((lonlat[0] - w) / (e - w) * W))
    res = analyse(L, v, dates, cfg, km2, spot_rc, unusable=unusable)
    res.update(dates=dates, unusable=unusable, lonlat=lonlat, area=area, grid=grid, km2=km2, valid=v, L=L, bbox=acfg["bbox"], spot_rc=spot_rc)
    if res["patch"] is None:
        print(area, "no patch found; threshold", res["thr"])
        return res
    pix_km = math.sqrt(km2)
    res["spot_dist_km"] = (float(np.hypot(res["centroid_rc"][0] - spot_rc[0], res["centroid_rc"][1] - spot_rc[1]) * pix_km)
                           if spot_rc else None)
    lo, hi = (float(np.percentile(res["b_img"][res["v"]].numpy(), q)) for q in (2, 98))
    res["grey"] = (lo, hi)
    res["export_mask"] = cfg["export_mask"]
    if dry:
        return res
    write_products(res)
    return res


def write_products(res):
    from scripts.detect.run import write_rgba
    area, dates, grid = res["area"], res["dates"], res["grid"]
    K = KINDS[res["cfg"]["kind"]]
    p = K["prefix"]
    prod = ROOT / "data/processed" / area / "products"
    sign, channel, thr = res["cfg"]["sign"], res["cfg"]["channel"], res["thr"]
    v = res["v"]
    lo, hi = res["grey"]
    write_rgba(prod / f"{p}_before.tif", ramp_rgba(res["b_img"], v, lo, hi), grid)
    write_rgba(prod / f"{p}_after.tif", ramp_rgba(res["a_img"], v, lo, hi), grid)
    write_rgba(prod / f"{p}_diff.tif", diff_rgba(res["diff_s"], v, thr, 2.5 * thr), grid)
    scar = prod / f"{K['mask']}.tif"
    if res["export_mask"]:
        write_rgba(scar, scar_rgba(res["patch"]), grid)
    elif scar.exists():
        scar.unlink()
    layers = layer_defs(res, dates, sign, channel, thr)
    lj = prod / "layers.json"
    old = [l for l in json.loads(lj.read_text()) if l["id"] not in LAYER_IDS]
    lj.write_text(json.dumps(layers + old, indent=2, ensure_ascii=False))

    ok = lambda t: res["series_in"][t] is not None and dates[t] not in res["excluded"]
    r1 = lambda z: None if z is None else round(z * 100, 2)
    ins = [r1(res["series_in"][t]) if ok(t) else None for t in range(len(dates))]
    outs = [r1(res["series_out"][t]) if ok(t) else None for t in range(len(dates))]
    dif = [None if (a is None or b is None) else round(a - b, 2) for a, b in zip(ins, outs)]
    info = burn_dict(res)
    ts = json.loads((prod / "timeseries.json").read_text())
    labels = {k["in"] for k in KINDS.values()} | {k["out"] for k in KINDS.values()} | {SERIES_DIFF}
    facts = [f for f in ts.get("facts", []) if f.get("label") not in labels]
    new = [{"label": K["in"], "unit": UNIT, "values": ins}, {"label": K["out"], "unit": UNIT, "values": outs},
           {"label": SERIES_DIFF, "unit": UNIT, "values": dif}]
    ts["facts"] = new + ([] if res["cfg"]["kind"] == "burn" else facts)  # a fire carries no water facts (a scar read as "flooded vegetation")
    for k in ("burn", "evidence"):
        ts.pop(k, None)
    ts["burn" if res["cfg"]["kind"] == "burn" else "evidence"] = {k: v for k, v in info.items() if k != "steps"}
    (prod / "timeseries.json").write_text(json.dumps(ts, indent=2, ensure_ascii=False))
    for k in ("burn", "ev"):
        if (prod / f"{k}.json").exists():
            (prod / f"{k}.json").unlink()
    (prod / f"{p}.json").write_text(json.dumps(info, indent=2, ensure_ascii=False))
    ap = ROOT / "areas" / area / "area.json"
    a = json.loads(ap.read_text())
    if res["cfg"].get("set_default", True):
        a["default_layer"] = f"{p}_diff"
    if res["cfg"].get("set_headline_note", True):
        a["headline_note"] = headline(res, dates)
    a.update(res["cfg"].get("area_set", {}))
    txt = ap.read_text()
    ap.write_text(json.dumps(a, indent=2, ensure_ascii=False) + ("\n" if txt.endswith("\n") else ""))
    print(area, "evidence layers written:", [l["id"] for l in layers])


def burn_dict(res):
    dates, cfg = res["dates"], res["cfg"]
    K = KINDS[cfg["kind"]]
    d = {"kind": cfg["kind"], "channel": cfg["channel"], "sign": cfg["sign"],
         "before_dates": [dates[i] for i in res["before"]], "after_dates": [dates[i] for i in res["after"]],
         "excluded_dates": res["excluded"], "ignition": cfg.get("ignition"), "ignition_between": cfg.get("ignition_between"),
         "control_dates": [dates[i] for i in res["control_a"] + res["control_b"]],
         "area_km2": round(res["area_km2"]),
         "area_label": "area of changed radar echo, not a burnt-area measurement" if cfg["kind"] == "burn"
         else "area of changed radar echo, not a measurement of land cover, crop or forest loss",
         "mean_change": round(res["mean_change"], 4), "mean_change_db_like": round(res["mean_change"] * DB_PER_UNIT, 2),
         "control_mean": round(res["control_mean"], 4), "control_frac_above": round(res["control_frac_above"], 3),
         "rank_percentile": round(res["rank_percentile"], 3), "rank_n_discs": res["rank_n_discs"],
         "disc_radius_km": round(res["disc_radius_km"], 1), "threshold": round(res["thr"], 4),
         "threshold_db_like": round(res["thr"] * DB_PER_UNIT, 2),
         "sensitivity": {k: {"threshold": round(v["threshold"], 4), "area_km2": round(v["area_km2"])} for k, v in res["sens"].items()},
         "persistence": {k: {"patch_change": round(v["patch"], 4), "above_threshold": v["patch"] > res["thr"]} for k, v in res["per_pass"].items()},
         "unit_note": "brightness units are the preview's 0-1 display scale; dB-like = 30 x brightness assumes a ~30 dB stretch (unverified)",
         "published": cfg.get("published"), "distance_to_ref_km": None if res.get("spot_dist_km") is None else round(res["spot_dist_km"], 1),
         "mask_exported": bool(res.get("export_mask", True)), "method_note": method_note(res),
         "layers": {"before": f"{K['prefix']}_before", "after": f"{K['prefix']}_after", "diff": f"{K['prefix']}_diff",
                    "mask": K["mask"] if res.get("export_mask", True) else None},
         "series": [K["in"], K["out"]],
         "steps": steps_text(res, dates, res.get("spot_dist_km"))}
    return d


def figure(res, path):
    """before | after | difference with the patch outlined | inside / outside curves."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    cfg, dates = res["cfg"], res["dates"]
    sign, thr = cfg["sign"], res["thr"]
    patch = res["patch"].numpy() if res.get("patch") is not None else None
    v = res["v"].numpy()
    lo, hi = res["grey"]
    fig, ax = plt.subplots(1, 4, figsize=(22, 5.6), gridspec_kw={"width_ratios": [1, 1, 1, 1.15]})
    for a_, img, ttl in ((ax[0], res["b_img"], f"before ({span([dates[i] for i in res['before']])})"),
                         (ax[1], res["a_img"], f"after ({span([dates[i] for i in res['after']])})")):
        a_.imshow(np.where(v, img.numpy(), np.nan), cmap="gray", vmin=lo, vmax=hi)
        a_.set_title(f"{cfg['channel'].upper()} {ttl}", fontsize=10)
    lim = 2.5 * thr
    ax[2].imshow(np.where(v, 0.0, np.nan), cmap="gray", vmin=-1, vmax=1)
    ax[2].imshow(np.ma.masked_where(~v, res["diff_s"].numpy()), cmap="RdBu_r" if False else matplotlib.colors.LinearSegmentedColormap.from_list("d", [DARKER, "#f2f2f2", BRIGHTER]), vmin=-lim, vmax=lim)
    if patch is not None:
        ax[2].contour(patch, levels=[0.5], colors=[FLAME], linewidths=1.4)
    if res.get("spot_rc"):
        for a_ in ax[:3]:
            a_.plot(res["spot_rc"][1], res["spot_rc"][0], "c+", ms=14, mew=2)
    ax[2].set_title(f"change after minus before (blue darker, red brighter; faint below ±{thr:.3f})\n"
                    f"orange outline = patch, cyan + = reference position", fontsize=9)
    for a_ in ax[:3]:
        a_.axis("off")
    t = np.arange(len(dates))
    if res.get("series_in"):
        ins = [np.nan if x is None or dates[i] in res["excluded"] else x for i, x in enumerate(res["series_in"])]
        outs = [np.nan if x is None or dates[i] in res["excluded"] else x for i, x in enumerate(res["series_out"])]
        K = KINDS[cfg["kind"]]
        ax[3].plot(t, ins, "o-", color=FLAME, label="inside the patch")
        ax[3].plot(t, outs, "s-", color="#555", label="ring around it")
        for i in res["after"]:
            ax[3].axvline(i, color="#ddd", lw=0.6, zorder=0)
        ax[3].set_xticks(t)
        ax[3].set_xticklabels([day_label(d) for d in dates], rotation=60, fontsize=8)
        ax[3].set_ylabel(f"{cfg['channel'].upper()} preview brightness (0–1)")
        ax[3].legend(fontsize=8)
        ax[3].set_title(f"mean change {res['mean_change']:+.3f}, control {res['control_mean']:+.3f}, "
                        f"area {res['area_km2']:,.0f} km²", fontsize=9)
    fig.suptitle(f"{res['area']}: {cfg['channel'].upper()}, sign {sign:+d}. Area of changed radar echo, not a measurement of loss or damage.", fontsize=11)
    fig.tight_layout()
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=70)
    plt.close(fig)


def main(argv, config_table, figdir="docs/eda/burn"):
    args = [a for a in argv if not a.startswith("--")]
    r = run(args[0], config_table[args[0]], export_mask=False if "--no-mask" in argv else None, dry=("--dry" in argv or "--fig" in argv))
    if r.get("patch") is not None:
        if "--dry" not in argv:  # --fig: figure only, nothing written to the products
            figure(r, ROOT / figdir / f"{args[0]}.png")
        b = burn_dict(r)
        print(json.dumps({k: v for k, v in b.items() if k not in ("method_note",)}, indent=1, ensure_ascii=False))
    return r


if __name__ == "__main__":
    from scripts.detect.burn import CONFIG as BURN
    from scripts.detect.evidence_places import CONFIG as PLACES
    main(sys.argv[1:], {**BURN, **PLACES}, figdir="docs/eda/evidence")
