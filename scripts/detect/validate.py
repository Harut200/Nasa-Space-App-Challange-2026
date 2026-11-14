"""Independent check of the radar water maps against JRC Global Surface Water (Pekel et al. 2016).

    .venv/bin/python -m scripts.detect.validate            # all areas
    .venv/bin/python -m scripts.detect.validate sevan      # one area

Reads only our window of each 10x10 degree GSW v1.4 (1984-2021) tile remotely (HTTP range requests via
GDAL /vsicurl/) and caches the clip as GeoTIFF in data/external/gsw/<area>_<product>.tif.
Resamples 30 m -> our ~180 m grid exactly (area-weighted overlap matrices, PyTorch), then compares.
Writes docs/eda/validation/results.json and docs/eda/validation/*.png. Write-up: docs/eda/validation.md.

Reference: Pekel, Cottam, Gorelick, Belward (2016) High-resolution mapping of global surface water and
its long-term changes. Nature 540:418-422. Source: EC JRC/Google.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import rasterio  # noqa: E402
import torch  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402
from rasterio.windows import Window  # noqa: E402

from scripts.detect import ops  # noqa: E402
from scripts.detect.stack import ROOT  # noqa: E402

AREAS = ["tonle-sap", "sevan", "ararat"]
PRODUCTS = ["occurrence", "extent", "seasonality"]
BASE = "https://storage.googleapis.com/global-surface-water/downloads2021"
RES = 0.00025  # degrees, GSW pixel
NODATA = 255   # GSW download tiles: 255 = no data (0 = never water / not water)
CACHE = ROOT / "data/external/gsw"
OUT = ROOT / "docs/eda/validation"
GAP = ("2026-07-27", "2026-08-10")
OCC_BINS = [0, 1, 10, 25, 50, 75, 90, 101]  # reference mean-occurrence bins (%), last bin 90-100
OCC_LABELS = ["0", "1-10", "10-25", "25-50", "50-75", "75-90", "90-100"]
PUBLISHED_SEVAN_KM2 = 1242.0


# ------------------------------------------------------------------ reference access
def _tiles(w, s, e, n):
    """GSW tile names (top-left corner) intersecting the box."""
    out = []
    for lon0 in range(int(math.floor(w / 10) * 10), int(math.ceil(e / 10) * 10), 10):
        for lat_top in range(int(math.floor(s / 10) * 10) + 10, int(math.ceil(n / 10) * 10) + 10, 10):
            ew = f"{abs(lon0)}{'E' if lon0 >= 0 else 'W'}"
            ns = f"{abs(lat_top)}{'N' if lat_top >= 0 else 'S'}"
            out.append((f"{ew}_{ns}", lon0, lat_top))
    return out


def fetch_clip(area, product, bounds):
    """Return (uint8 array, west, north) of the GSW product over bounds (rounded out to the 30 m grid)."""
    w, s, e, n = bounds
    w0 = math.floor(w / RES + 1e-6) * RES
    n0 = math.ceil(n / RES - 1e-6) * RES
    ncol = int(round((math.ceil(e / RES - 1e-6) * RES - w0) / RES))
    nrow = int(round((n0 - math.floor(s / RES + 1e-6) * RES) / RES))
    path = CACHE / f"{area}_{product}.tif"
    if path.exists():
        with rasterio.open(path) as src:
            return src.read(1), src.transform.c, src.transform.f, "cache"
    CACHE.mkdir(parents=True, exist_ok=True)
    arr = np.full((nrow, ncol), NODATA, np.uint8)
    for name, lon0, lat_top in _tiles(w, s, e, n):
        url = f"/vsicurl/{BASE}/{product}/{product}_{name}v1_4_2021.tif"
        with rasterio.open(url) as src:
            col0 = int(round((w0 - src.transform.c) / RES))
            row0 = int(round((src.transform.f - n0) / RES))
            c_lo, c_hi = max(col0, 0), min(col0 + ncol, src.width)
            r_lo, r_hi = max(row0, 0), min(row0 + nrow, src.height)
            if c_lo >= c_hi or r_lo >= r_hi:
                continue
            a = src.read(1, window=Window(c_lo, r_lo, c_hi - c_lo, r_hi - r_lo))
            arr[r_lo - row0:r_hi - row0, c_lo - col0:c_hi - col0] = a
            print(f"  {product} {name}: read window {a.shape} remotely")
    with rasterio.open(path, "w", driver="GTiff", width=ncol, height=nrow, count=1, dtype="uint8",
                       crs="EPSG:4326", transform=from_origin(w0, n0, RES, RES), nodata=NODATA,
                       compress="lzw", tiled=True) as dst:
        dst.write(arr, 1)
    return arr, w0, n0, "remote"


def _overlap(t_edges, s_edges):
    """R[i, j] = length of overlap of target cell i with source cell j, divided by source cell length."""
    lo = torch.maximum(t_edges[:-1, None], s_edges[None, :-1])
    hi = torch.minimum(t_edges[1:, None], s_edges[None, 1:])
    return (hi - lo).clamp(min=0) / (s_edges[1:] - s_edges[:-1])[None]


def resample(arr, w0, n0, bounds, H, W):
    """Area-weighted resampling of a 30 m GSW clip onto our H x W lon/lat grid.

    Returns dict of [H, W] float tensors: valid fraction, mean occurrence etc. are computed by the caller
    from `agg(f)` = area-weighted mean of f over valid 30 m pixels.
    """
    w, s, e, n = bounds
    nr, nc = arr.shape
    ty = torch.linspace(0, n - s, H + 1, dtype=torch.float64)          # distance south of our north edge
    tx = torch.linspace(0, e - w, W + 1, dtype=torch.float64)
    sy = (n - n0) + torch.arange(nr + 1, dtype=torch.float64) * RES     # source rows, same origin
    sx = (w0 - w) + torch.arange(nc + 1, dtype=torch.float64) * RES
    Ry = _overlap(ty, sy).float()
    Rx = _overlap(tx, sx).float()
    a = torch.from_numpy(arr)
    valid = (a != NODATA).float()

    def wsum(f):
        return Ry @ f @ Rx.T

    vw = wsum(valid)

    def agg(f):
        return wsum(f.float() * valid) / vw.clamp(min=1e-9)

    full = wsum(torch.ones_like(valid))  # expected 30 m pixel count per our pixel
    return vw / full.clamp(min=1e-9), agg, a


def reference_on_grid(area, bounds, H, W):
    ref, how = {}, {}
    occ, w0, n0, how["occurrence"] = fetch_clip(area, "occurrence", bounds)
    vfrac, agg, a = resample(occ, w0, n0, bounds, H, W)
    ref["valid_frac"] = vfrac
    ref["occ_mean"] = agg(a)
    for thr in (75, 90, 95):
        ref[f"occ_ge{thr}_frac"] = agg(a >= thr)
    ref["occ_any_frac"] = agg(a > 0)
    ext, w0, n0, how["extent"] = fetch_clip(area, "extent", bounds)
    _, agg, a = resample(ext, w0, n0, bounds, H, W)
    ref["ext_frac"] = agg(a == 1)
    sea, w0, n0, how["seasonality"] = fetch_clip(area, "seasonality", bounds)
    _, agg, a = resample(sea, w0, n0, bounds, H, W)
    ref["seas12_frac"] = agg(a == 12)
    return ref, how


# ------------------------------------------------------------------ metrics
def _r(x, nd=4):
    return round(float(x), nd)


def confusion(ours, refm, dom, km2):
    tp = float((ours & refm & dom).sum()); fp = float((ours & ~refm & dom).sum())
    fn = float((~ours & refm & dom).sum()); tn = float((~ours & ~refm & dom).sum())
    return {
        "tp_km2": _r(tp * km2, 1), "fp_km2": _r(fp * km2, 1), "fn_km2": _r(fn * km2, 1), "tn_km2": _r(tn * km2, 1),
        "ours_km2": _r((tp + fp) * km2, 1), "ref_km2": _r((tp + fn) * km2, 1),
        "iou": _r(tp / max(tp + fp + fn, 1)), "precision": _r(tp / max(tp + fp, 1)),
        "recall": _r(tp / max(tp + fn, 1)),
    }


def boundary_band(m, k):
    """Pixels within k of the water/land boundary of mask m (both sides)."""
    kk = 2 * k + 1
    return (ops.dilate(m[None], kk) & ~ops.erode(m[None], kk))[0]


def boundary_analysis(ours, refm, dom, km2):
    dis = (ours ^ refm) & dom
    nd = float(dis.sum())
    out = {"disagree_km2": _r(nd * km2, 1)}
    for k in (1, 2):
        band = boundary_band(refm, k) | boundary_band(ours, k)
        out[f"disagree_within_{k}px_share"] = _r(float((dis & band).sum()) / max(nd, 1))
    band1 = boundary_band(refm, 1) | boundary_band(ours, 1)
    c = confusion(ours, refm, dom & ~band1, km2)
    out["excl_1px_band"] = {k: c[k] for k in ("iou", "precision", "recall", "fp_km2", "fn_km2")}
    return out


def occ_hist(mask, occ, km2):
    o = occ[mask]
    cnt = torch.histc(o, bins=101, min=0, max=101)  # 1 %-wide bins
    out = {}
    for lab, lo, hi in zip(OCC_LABELS, OCC_BINS[:-1], OCC_BINS[1:]):
        out[lab] = float(((o >= lo) & (o < hi)).sum()) if lab != "0" else float((o < 1).sum())
    tot = max(sum(out.values()), 1)
    return {"km2": _r(float(mask.sum()) * km2, 1),
            "share_by_occ_bin": {k: _r(v / tot) for k, v in out.items()},
            "median_occ": _r(o.median() if o.numel() else float("nan"), 1),
            "mean_occ": _r(o.mean() if o.numel() else float("nan"), 1)}, cnt


def inside_extent(mask, ext_frac, km2):
    n = float(mask.sum())
    inside = mask & (ext_frac >= 0.5)
    return {"km2": _r(n * km2, 1), "share_inside_max_extent": _r(float(inside.sum()) / max(n, 1)),
            "mean_extent_cover": _r(ext_frac[mask].mean() if n else float("nan")),
            "outside_km2": _r((n - float(inside.sum())) * km2, 1)}


def where_outside(mask_out, dom, ref_any, bounds, km2, H, W):
    """Characterise water we see outside the reference maximum extent."""
    n = float(mask_out.sum())
    if n == 0:
        return {"km2": 0.0}
    # distance (in our px) from the footprint edge and from any reference water
    edge = ~dom
    near_edge = ops.dilate(edge[None], 2 * 10 + 1)[0]
    near_ref1 = ops.dilate(ref_any[None], 3)[0]
    near_ref3 = ops.dilate(ref_any[None], 7)[0]
    w, s, e, n_ = bounds
    lab = ops.label_components(mask_out[None])[0]
    ids, counts = torch.unique(lab[lab > 0], return_counts=True)
    order = torch.argsort(counts, descending=True)[:6]
    yy, xx = torch.meshgrid(torch.arange(H), torch.arange(W), indexing="ij")
    clusters = []
    for i in order.tolist():
        m = lab == ids[i]
        cy, cx = float(yy[m].float().mean()), float(xx[m].float().mean())
        clusters.append({"km2": _r(float(counts[i]) * km2, 1),
                         "lon": _r(w + (cx + 0.5) / W * (e - w), 3), "lat": _r(n_ - (cy + 0.5) / H * (n_ - s), 3),
                         "within_10px_of_frame_edge": bool((m & near_edge).any()),
                         "touches_ref_water_within_1px": bool((m & near_ref1).any())})
    return {"km2": _r(n * km2, 1),
            "share_within_10px_of_frame_edge": _r(float((mask_out & near_edge).sum()) / n),
            "share_within_1px_of_ref_water": _r(float((mask_out & near_ref1).sum()) / n),
            "share_within_3px_of_ref_water": _r(float((mask_out & near_ref3).sum()) / n),
            "share_far_from_ref_water_gt3px": _r(float((mask_out & ~near_ref3).sum()) / n),
            "n_clusters": int(ids.numel()), "largest_clusters": clusters}


def analyse(area):
    p = torch.load(ROOT / f"data/processed/{area}/math/products.pt", weights_only=False, map_location="cpu")
    bounds, km2, dates = p["bounds"], p["km2_per_px"], p["dates"]
    water, fv, cls = p["water"], p["flooded_veg"], p["classes"]
    T, H, W = water.shape
    print(f"{area}: grid {H}x{W}, bounds {bounds}")
    ref, how = reference_on_grid(area, bounds, H, W)
    dom = p["valid"].all(0) & (ref["valid_frac"] >= 0.5)
    ref_nodata_in_frame = float((p["valid"].all(0) & (ref["valid_frac"] < 0.5)).sum()) * km2
    perm = cls == 1
    seas = cls == 2
    fvc = cls == 3
    other = cls == 4
    stable = cls == 0
    R = {"area": area, "bounds": bounds, "km2_per_px": km2, "dates": dates, "grid": [H, W],
         "reference_access": how,
         "domain_km2": _r(float(dom.sum()) * km2, 1), "ref_nodata_in_footprint_km2": _r(ref_nodata_in_frame, 1),
         "ref_km2_in_domain": {
             "occ_ge90_fraction_weighted": _r(float((ref["occ_ge90_frac"] * dom).sum()) * km2, 1),
             "occ_ge75_fraction_weighted": _r(float((ref["occ_ge75_frac"] * dom).sum()) * km2, 1),
             "max_extent_fraction_weighted": _r(float((ref["ext_frac"] * dom).sum()) * km2, 1),
             "seasonality12_2021_fraction_weighted": _r(float((ref["seas12_frac"] * dom).sum()) * km2, 1)},
         "ref_km2_whole_box": {
             "occ_ge90_fraction_weighted": _r(float(ref["occ_ge90_frac"].sum()) * km2, 1),
             "max_extent_fraction_weighted": _r(float(ref["ext_frac"].sum()) * km2, 1)}}
    # permanent water vs occurrence thresholds (reference pixel = >= 50 % of it covered)
    R["permanent"] = {}
    for thr in (75, 90, 95):
        refm = ref[f"occ_ge{thr}_frac"] >= 0.5
        c = confusion(perm, refm, dom, km2)
        if thr == 90:
            c["boundary"] = boundary_analysis(perm, refm, dom, km2)
        R["permanent"][f"occ_ge{thr}"] = c
    R["permanent"]["seasonality12_2021"] = confusion(perm, ref["seas12_frac"] >= 0.5, dom, km2)
    # seasonal water and late-season water vs maximum extent
    ext = ref["ext_frac"]
    extm = ext >= 0.5
    late = water[-3:].any(0) & dom
    R["vs_max_extent"] = {
        "permanent_class": inside_extent(perm & dom, ext, km2),
        "seasonal_open_water_class": inside_extent(seas & dom, ext, km2),
        "flooded_veg_class": inside_extent(fvc & dom, ext, km2),
        "flooded_veg_any_date": inside_extent(fv.any(0) & dom, ext, km2),
        "other_change_class": inside_extent(other & dom, ext, km2),
        "last_date_water": inside_extent(water[-1] & dom, ext, km2),
        "late_season_water_last3": inside_extent(late, ext, km2),
        "any_date_water": inside_extent(water.any(0) & dom, ext, km2),
    }
    ref_any = (ref["ext_frac"] > 0) & dom
    R["late_outside_max_extent"] = where_outside(late & ~extm, dom, ref_any, bounds, km2, H, W)
    # occurrence distributions per class
    R["occ_by_class"] = {}
    hists = {}
    for name, m in [("stable", stable), ("permanent water", perm), ("seasonal open water", seas),
                    ("flooded vegetation", fvc), ("other change", other), ("last-date water", water[-1])]:
        R["occ_by_class"][name], hists[name] = occ_hist(m & dom, ref["occ_mean"], km2)
    # per-date water as a fraction of the reference maximum extent
    ext_km2 = float((ext * dom).sum()) * km2
    per = []
    for t in range(T):
        wt = water[t] & dom
        per.append({"date": dates[t], "water_km2": _r(float(wt.sum()) * km2, 1),
                    "fraction_of_max_extent_area": _r(float(wt.sum()) * km2 / max(ext_km2, 1e-9)),
                    "share_inside_max_extent": _r(float((wt & extm).sum()) / max(float(wt.sum()), 1))})
    R["per_date"] = per
    R["permanent_outside_max_extent"] = where_outside(perm & dom & ~extm, dom, ref_any, bounds, km2, H, W)
    R["seasonal_outside_max_extent"] = where_outside(seas & dom & ~extm, dom, ref_any, bounds, km2, H, W)
    R["flooded_veg_outside_max_extent"] = where_outside(fv.any(0) & dom & ~extm, dom, ref_any, bounds, km2, H, W)
    # geolocation sanity check: does shifting our map by a few pixels improve agreement?
    ours_s, ref_s = (water.any(0), extm) if area == "ararat" else (perm, ref["occ_ge75_frac"] >= 0.5)
    best = (0, 0, -1.0)
    grid_iou = {}
    for dy in range(-4, 5):
        for dx in range(-4, 5):
            o = torch.roll(ours_s, (dy, dx), (0, 1))
            d = dom & torch.roll(dom, (dy, dx), (0, 1))
            iou = float((o & ref_s & d).sum()) / max(float(((o | ref_s) & d).sum()), 1)
            grid_iou[(dy, dx)] = iou
            if iou > best[2]:
                best = (dy, dx, iou)
    R["shift_test"] = {"compared": "any-date water vs max extent" if area == "ararat" else
                       "permanent class vs occurrence >= 75 %",
                       "iou_unshifted": _r(grid_iou[(0, 0)]), "best_shift_px_dy_dx": [best[0], best[1]],
                       "best_iou": _r(best[2])}
    R["first_vs_lowwater_occ_ge90"] =confusion(water[0], ref["occ_ge90_frac"] >= 0.5, dom, km2)
    R["first_vs_lowwater_occ_ge75"] = confusion(water[0], ref["occ_ge75_frac"] >= 0.5, dom, km2)
    R["last_vs_max_extent"] = confusion(water[-1], extm, dom, km2)
    R["last_vs_max_extent"]["boundary"] = boundary_analysis(water[-1], extm, dom, km2)
    R["first_vs_max_extent"] = confusion(water[0], extm, dom, km2)
    maps = {"perm": perm, "ref90": ref["occ_ge90_frac"] >= 0.5, "last": water[-1], "extm": extm,
            "late": late, "dom": dom, "any": water.any(0), "occ": ref["occ_mean"]}
    return R, maps, hists


# ------------------------------------------------------------------ figures
def _agree_rgb(ours, refm, dom):
    img = np.ones(ours.shape + (3,)) * 0.93
    o, r, d = ours.numpy(), refm.numpy(), dom.numpy()
    img[~d] = [0.75, 0.75, 0.75]
    img[o & r & d] = [0.13, 0.4, 0.8]
    img[o & ~r & d] = [0.9, 0.3, 0.1]
    img[~o & r & d] = [0.55, 0.2, 0.65]
    return img


def _save(fig, name):
    path = OUT / name
    fig.savefig(path, dpi=fig.dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  wrote {path.relative_to(ROOT)} ({path.stat().st_size / 1024:.0f} KB)")


def fig_agreement(area, R, M, title):
    w, s, e, n = R["bounds"]
    ext = (w, e, s, n)
    fig, axs = plt.subplots(1, 2, figsize=(11, 5.6), dpi=90)
    pc = R["permanent"]["occ_ge90"]
    lc = R["last_vs_max_extent"]
    for ax, (o, r, t) in zip(axs, [
            (M["perm"], M["ref90"], f"Our permanent water vs GSW occurrence >= 90 %\nIoU {pc['iou']:.2f}, "
                                    f"precision {pc['precision']:.2f}, recall {pc['recall']:.2f}"),
            (M["last"], M["extm"], f"Our water on {R['dates'][-1]} vs GSW maximum extent\nIoU {lc['iou']:.2f}, "
                                   f"precision {lc['precision']:.2f}, recall {lc['recall']:.2f}")]):
        ax.imshow(_agree_rgb(o, r, M["dom"]), extent=ext, interpolation="nearest")
        ax.set_title(t, fontsize=9)
        ax.set_xlabel("longitude"); ax.set_ylabel("latitude")
    axs[1].legend(handles=[Patch(color=[0.13, 0.4, 0.8], label="both water"),
                           Patch(color=[0.9, 0.3, 0.1], label="only ours"),
                           Patch(color=[0.55, 0.2, 0.65], label="only reference"),
                           Patch(color=[0.75, 0.75, 0.75], label="outside our footprint")],
                  loc="lower left", fontsize=7, framealpha=0.9)
    fig.suptitle(f"{title}: radar (2026) vs JRC Global Surface Water (Landsat 1984-2021). "
                 "Source: EC JRC/Google", fontsize=9)
    _save(fig, f"{area}_agreement.png")


def fig_ararat(R, M):
    w, s, e, n = R["bounds"]
    fig, ax = plt.subplots(figsize=(6.5, 4.8), dpi=90)
    ax.imshow(_agree_rgb(M["any"], M["extm"], M["dom"]), extent=(w, e, s, n), interpolation="nearest")
    v = R["vs_max_extent"]["any_date_water"]
    ax.set_title(f"Ararat valley: our water on any date vs GSW maximum extent\n"
                 f"{v['share_inside_max_extent'] * 100:.0f} % of {v['km2']:.0f} km2 inside; "
                 f"{v['outside_km2']:.0f} km2 outside. Source: EC JRC/Google", fontsize=9)
    ax.legend(handles=[Patch(color=[0.13, 0.4, 0.8], label="both"), Patch(color=[0.9, 0.3, 0.1], label="only ours"),
                       Patch(color=[0.55, 0.2, 0.65], label="only reference")], loc="lower left", fontsize=7)
    _save(fig, "ararat_agreement.png")


def fig_hist(allR):
    names = ["permanent water", "seasonal open water", "flooded vegetation", "other change", "stable"]
    cols = ["#08306b", "#4292c6", "#41ab5d", "#e6550d", "#969696"]
    fig, axs = plt.subplots(1, len(allR), figsize=(4.2 * len(allR), 3.8), dpi=90, sharey=True)
    axs = np.atleast_1d(axs)
    x = np.arange(len(OCC_LABELS))
    wbar = 0.16
    for ax, R in zip(axs, allR):
        for k, (nm, c) in enumerate(zip(names, cols)):
            d = R["occ_by_class"][nm]
            if d["km2"] < 1:
                continue
            ax.bar(x + (k - 2) * wbar, [d["share_by_occ_bin"][b] for b in OCC_LABELS], wbar, color=c,
                   label=f"{nm} ({d['km2']:.0f} km2)")
        ax.set_xticks(x, OCC_LABELS, fontsize=8)
        ax.set_xlabel("GSW occurrence 1984-2021 (%)", fontsize=8)
        ax.set_title(R["area"], fontsize=10)
        ax.legend(fontsize=7)
    axs[0].set_ylabel("share of class pixels")
    fig.suptitle("Where our classes fall in the long-term water record. Source: EC JRC/Google", fontsize=9)
    _save(fig, "occurrence_by_class.png")


def fig_curve(allR):
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=90)
    # Ararat is left out: almost none of its detected water overlaps the reference (see results.json)
    for R, c in zip([R for R in allR if R["area"] != "ararat"], ["#08519c", "#d94801"]):
        d = [dt.date.fromisoformat(r["date"]) for r in R["per_date"]]
        f = [r["fraction_of_max_extent_area"] * 100 for r in R["per_date"]]
        ax.plot(d, f, "o-", color=c, label=R["area"])
    ax.axvspan(dt.date.fromisoformat(GAP[0]), dt.date.fromisoformat(GAP[1]), color="0.85", label="no passes")
    ax.set_ylabel("our water area / GSW max-extent area (%)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.set_title("Our 2026 water extent relative to the 1984-2021 maximum water extent (same footprint)",
                 fontsize=9)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    ax.text(0.99, 0.01, "Reference: JRC GSW v1.4, Source: EC JRC/Google", transform=ax.transAxes,
            ha="right", va="bottom", fontsize=7, color="0.4")
    _save(fig, "fraction_of_max_extent.png")


def main(areas):
    OUT.mkdir(parents=True, exist_ok=True)
    allR, titles = [], {"tonle-sap": "Tonle Sap", "sevan": "Lake Sevan", "ararat": "Ararat valley"}
    for a in areas:
        R, M, _ = analyse(a)
        allR.append(R)
        if a == "ararat":
            fig_ararat(R, M)
        else:
            fig_agreement(a, R, M, titles[a])
    fig_hist(allR)
    fig_curve(allR)
    res = {"generated": dt.datetime.now().isoformat(timespec="seconds"),
           "reference": {"dataset": "JRC Global Surface Water v1.4 (1984-2021)",
                         "citation": "Pekel, Cottam, Gorelick, Belward (2016) Nature 540:418-422",
                         "attribution": "Source: EC JRC/Google", "url_pattern": BASE + "/<product>/<product>_<tile>v1_4_2021.tif",
                         "nodata": NODATA},
           "method": {"resampling": "exact area-weighted average of 30 m pixels onto our grid (no-data excluded)",
                      "ref_binary_rule": "our pixel counts as reference water if >= 50 % of its area is covered",
                      "domain": "our common radar footprint and >= 50 % valid reference",
                      "late_season": "water on any of the last 3 dates"},
           "published_sevan_km2": PUBLISHED_SEVAN_KM2,
           "areas": {R["area"]: R for R in allR}}
    if (OUT / "results.json").exists() and len(areas) < len(AREAS):
        old = json.loads((OUT / "results.json").read_text())
        old["areas"].update(res["areas"]); res["areas"] = old["areas"]
    (OUT / "results.json").write_text(json.dumps(res, indent=1))
    print(f"  wrote {(OUT / 'results.json').relative_to(ROOT)}")


if __name__ == "__main__":
    main(sys.argv[1:] or AREAS)
