"""Detector entry point: contract A (or the free previews) -> contract B.

    python scripts/detect/run.py <area> [--source auto|gamma0|preview] [--cpu]
    python -m scripts.detect.run <area>

auto = gamma0 if data/processed/<area>/gamma0/hh/*.tif exist, else the preview images.
Detection itself is ops.run_pipeline (PyTorch); this file only loads, colours and writes.
Writes data/processed/<area>/products/: coloured RGBA GeoTIFFs, layers.json,
timeseries.json, source.json.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import rasterio  # noqa: E402
import torch  # noqa: E402
from rasterio.transform import from_bounds  # noqa: E402
from rasterio.warp import transform as warp_transform  # noqa: E402

from scripts.detect import ops  # noqa: E402
from scripts.detect.stack import device, load_area, load_preview_stack  # noqa: E402

# ----------------------------------------------------------------------------
# Display knobs (detection knobs live in ops.KNOBS).
# change: |diff| below "thr" transparent, full opacity at "full"; units of L
# (preview: brightness 0..1, gamma0: dB). grey: range of L mapped to 0..255.
# ----------------------------------------------------------------------------
DISPLAY = {
    "preview": {"change": {"thr": 0.10, "full": 0.35}, "grey": {"hh": (0.0, 1.0), "hv": (0.0, 1.0)}},
    "gamma0": {"change": {"thr": 3.0, "full": 8.0}, "grey": {"hh": (-25.0, -5.0), "hv": (-30.0, -10.0)}},
}
CHANGE_MIN_ALPHA = 0.3   # opacity just above the threshold (rises linearly to 1 at "full")

WATER = "#2f6fde"
FLOODED_VEG = "#35b04a"
GOING_UNDER = "#7fd3c4"
WIND = "#f28e2b"
DARKER, BRIGHTER = "#2166ac", "#b2182b"
CLASS_ITEMS = [  # ops.classify codes 1..3, then code 4 split by the sign of the step (0 = stable, transparent)
    ("#08306b", "Permanent water"),
    ("#4292c6", "Seasonal open water"),
    ("#41ab5d", "Flooded vegetation"),
    ("#9ecae1", "Got darker, not open water (wetter ground, shallow or partial flooding)"),
    ("#c994c7", "Got brighter (growing crops and other causes)"),
]
PREVIEW_UNIT = "relative brightness (0–100, uncalibrated)"
CREDITS = {
    "preview": ("NISAR GCOV preview images: NASA/JPL-Caltech and ISRO, distributed by ASF DAAC. "
                "Uncalibrated previews at about 180 m; values are display brightness, not backscatter."),
    "gamma0": "NISAR data: NASA/JPL-Caltech and ISRO, distributed by ASF DAAC. PROVISIONAL calibration.",
}


def hex_rgb(h):
    h = h.lstrip("#")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


def viridis(n):
    import matplotlib
    cm = matplotlib.colormaps["viridis"].resampled(max(n, 1))
    return ["#%02x%02x%02x" % tuple(int(round(c * 255)) for c in cm(i)[:3]) for i in range(n)]


def day_label(d):
    x = dt.date.fromisoformat(d)
    return f"{x.day} {x.strftime('%b')}"


# ------------------------------- loading ------------------------------------
class Grid:
    def __init__(self, crs, transform, h, w):
        self.crs, self.transform, self.h, self.w = crs, transform, h, w

    def pixel_lonlat_dist(self, lon, lat):
        """Distance in metres from every pixel centre to (lon, lat), as a torch [H, W] tensor."""
        rr, cc = np.mgrid[:self.h, :self.w]
        t = self.transform
        x = t.c + (cc + 0.5) * t.a + (rr + 0.5) * t.b
        y = t.f + (cc + 0.5) * t.d + (rr + 0.5) * t.e
        if self.crs.is_geographic:
            kx = 111320.0 * math.cos(math.radians(lat))
            d = np.hypot((x - lon) * kx, (y - lat) * 110574.0)
        else:
            (sx,), (sy,) = warp_transform("EPSG:4326", self.crs, [lon], [lat])
            d = np.hypot(x - sx, y - sy)
        return torch.from_numpy(d.astype(np.float32))


def load_gamma0(area):
    base = ROOT / "data/processed" / area / "gamma0"
    dates = sorted(p.stem for p in (base / "hh").glob("*.tif"))
    hv_dates = sorted(p.stem for p in (base / "hv").glob("*.tif"))
    assert dates and dates == hv_dates, f"contract A: hh and hv dates differ ({dates} vs {hv_dates})"
    ref, arrs = None, []
    for d in dates:
        pair = []
        for pol in ("hh", "hv"):
            with rasterio.open(base / pol / f"{d}.tif") as r:
                g = (r.crs, tuple(r.transform), r.height, r.width)
                if ref is None:
                    ref, grid = g, Grid(r.crs, r.transform, r.height, r.width)
                assert g == ref, f"contract A violation: {pol}/{d}.tif is on a different grid"
                pair.append(r.read(1).astype(np.float32))
        arrs.append(np.stack(pair))
    x = torch.from_numpy(np.stack(arrs))
    x[~(x > 0)] = float("nan")  # zeros/negatives are no-data by contract
    valid = torch.isfinite(x).all(1)
    km2 = abs(grid.transform.a * grid.transform.e) / 1e6
    return dates, x, valid, grid, km2


def load_preview(area):
    st = load_preview_stack(area)
    w, s, e, n = st.bounds
    H, W = st.x.shape[-2:]
    grid = Grid(rasterio.crs.CRS.from_epsg(4326), from_bounds(w, s, e, n, W, H), H, W)
    return st.dates, st.x, st.valid, grid, st.km2_per_px


# ------------------------------- writing ------------------------------------
def write_rgba(path, rgba, grid):
    """rgba torch/np [4, H, W] uint8 -> deflate GeoTIFF on the input grid."""
    a = rgba.cpu().numpy() if torch.is_tensor(rgba) else rgba
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=grid.h, width=grid.w, count=4, dtype="uint8",
                       crs=grid.crs, transform=grid.transform, compress="deflate",
                       photometric="RGB") as o:
        o.write(a.astype(np.uint8))
        o.colorinterp = [rasterio.enums.ColorInterp.red, rasterio.enums.ColorInterp.green,
                         rasterio.enums.ColorInterp.blue, rasterio.enums.ColorInterp.alpha]


def grey(v, lo, hi, alpha):
    g = ((v - lo) / (hi - lo)).clamp(0, 1).mul(255).round().to(torch.uint8)
    return torch.stack([g, g, g, alpha.to(torch.uint8) * 255])


def paint(masks_colors, shape):
    """[(mask [H,W] bool, '#rrggbb' or (r,g,b), alpha 0..255 or tensor)] -> RGBA, later entries on top."""
    out = torch.zeros(4, *shape, dtype=torch.uint8)
    for m, c, a in masks_colors:
        m = m.cpu()
        rgb = hex_rgb(c) if isinstance(c, str) else c
        for i in range(3):
            out[i][m] = rgb[i]
        out[3][m] = a[m] if torch.is_tensor(a) else a
    return out


def ramp(unit, lo, hi):
    return {"type": "ramp", "unit": unit, "min": lo, "max": hi, "colors": ["#000000", "#ffffff"]}


def _product(area, name, shape):
    """An analysis product for this area, or None if absent or on another grid."""
    fp = ROOT / "data/processed" / area / name / "products.pt"
    if not fp.exists():
        return None
    d = torch.load(fp, map_location="cpu", weights_only=False)
    return d if tuple(d["valid"].shape[-2:]) == tuple(shape) else None


def area_range(area, T):
    """5-95 % range of open-water area per date from the uncertainty study (threshold, shoreline, noise)."""
    fp = ROOT / "data/processed" / area / "stats/products.pt"
    if not fp.exists():
        return {}
    d = torch.load(fp, map_location="cpu", weights_only=False)
    if "water_km2_lo" not in d or len(d["water_km2_lo"]) != T:
        return {}
    return {"lo": [round(float(v)) for v in d["water_km2_lo"]], "hi": [round(float(v)) for v in d["water_km2_hi"]]}


def extra_layers(area, prod, grid, dates, valid, when_cols, cls_legend):
    """Whole-season layers built by the analysis modules; each is added only if its product exists."""
    H, W, T = grid.h, grid.w, len(dates)
    vany = valid.any(0)
    out = []
    wanted = set(load_area(area).get("extra_layers", ["sig"]))  # arrival and choreo only where a flood moves
    st = _product(area, "stats", (H, W)) if "sig" in wanted else None
    if st is not None and "changed_fdr" in st:
        big = st["largest_change"]
        img = paint([(st["changed_fdr"] & (big == k) & vany, when_cols[k - 1], 255) for k in range(1, T)], (H, W))
        write_rgba(prod / "sig.tif", img, grid)
        out.append({"id": "sig", "label": "Significant change (1 % false-discovery rate)", "dated": False,
                    "legend": cls_legend([(when_cols[k - 1], "largest change by " + day_label(dates[k])) for k in range(1, T)])})
    nv = _product(area, "novel", (H, W)) if wanted & {"arrival", "choreo"} else None
    if nv is not None and "arrival" in wanted and "arrival_day" in nv:
        doy = lambda d: dt.date.fromisoformat(d).timetuple().tm_yday
        d0, d1, n = doy(dates[0]), doy(dates[-1]), 48
        cols = viridis(n)
        day = nv["arrival_day"]
        idx = ((day - d0) / max(d1 - d0, 1) * (n - 1)).round().clamp(0, n - 1)
        ok = torch.isfinite(day) & vany
        write_rgba(prod / "arrival.tif", paint([(ok & (idx == i), cols[i], 255) for i in range(n)], (H, W)), grid)
        out.append({"id": "arrival", "label": "Day the water arrived (estimated between passes)", "dated": False,
                    "legend": {"type": "ramp", "unit": "", "min": day_label(dates[0]), "max": day_label(dates[-1]),
                               "colors": viridis(7)}})
    if nv is not None and "choreo" in wanted and "choreo_rgb" in nv:
        rgb = nv["choreo_rgb"].permute(2, 0, 1).to(torch.uint8)
        alpha = (nv["valid"] & vany).to(torch.uint8) * 255
        write_rgba(prod / "choreo.tif", torch.cat([rgb, alpha[None]]), grid)
        out.append({"id": "choreo", "label": "Choreography: when and how much each place changed", "dated": False,
                    "legend": {"type": "text", "text": "Colour shows when a place changed: yellow-green in late July, green "
                               "in mid-August, blue in September, purple in October. Stronger colour means a bigger change. "
                               "Grey means no significant change."}})
    return out


# ------------------------------- main ---------------------------------------
def run(area, source="auto", dev=None):
    if source == "auto":
        source = "gamma0" if any((ROOT / "data/processed" / area / "gamma0/hh").glob("*.tif")) else "preview"
    dates, x, valid_in, grid, km2 = load_gamma0(area) if source == "gamma0" else load_preview(area)
    dev = dev or device()
    cfg = load_area(area)
    from scripts.detect.qc import apply_qc, load_qc
    x_obs = x  # as observed, for the image layers
    x, valid_det, qc_done = apply_qc(x, valid_in, dates, load_qc(area))
    if qc_done:
        print("  quality control: left out of detection:", qc_done)
    from scripts.detect.terrain import cached_water_possible
    water_ok = cached_water_possible(area, tuple(x.shape[-2:]))
    print("  terrain mask:", "applied" if water_ok is not None else "not built for this area")
    out = ops.run_pipeline(x, valid_det, source, dev=dev, water_ok=water_ok)
    water_src = "math"
    src = cfg.get("water_source")
    if src in ("fusion", "fusion2"):  # fused, time-smoothed water map (fusion2: also handles floodplains)
        fp = ROOT / "data/processed" / area / src / "products.pt"
        fz = torch.load(fp, map_location="cpu", weights_only=False) if fp.exists() else None
        if fz is not None and tuple(fz["water"].shape) == tuple(out["water"].shape) and list(fz["dates"]) == list(dates):
            wdev = out["water"].device
            out["water"] = fz["water"].to(wdev) & out["valid"]
            out["wind_suspect"] = torch.zeros_like(out["water"])
            out["classes"] = ops.classify(out["water"], out["flooded_veg"], out["valid"], out["change_date"], out["knobs"])
            water_src = src
        else:
            print(f"  fused water map missing or on another grid: run scripts.detect.{src} first; using the math detector")
    print("  water source:", water_src)
    if source == "preview":  # thresholds of the rebuilt rule are in preview brightness; gamma0 keeps the simple rule
        from scripts.detect.floodveg import detect_flooded_veg
        fvp = detect_flooded_veg(out["L"], out["valid"], out["water"],
                                 terrain={"water_possible": water_ok} if water_ok is not None else None)
        wdev = out["water"].device
        out["flooded_veg"] = fvp["flooded_veg"].to(wdev)
        out["fv_state"] = fvp["fv_state"].to(wdev)
        out["classes"] = ops.classify(out["water"], out["flooded_veg"], out["valid"], out["change_date"], out["knobs"])
    o = {k: (v.cpu() if torch.is_tensor(v) else v) for k, v in out.items()}
    L, valid, T = o["L"], o["valid"], len(dates)
    H, W = grid.h, grid.w
    pol_name = cfg.get("change_pol", "hh").lower()
    pol = {"hh": 0, "hv": 1}[pol_name]
    disp = DISPLAY[source]
    prod = ROOT / "data/processed" / area / "products"
    prod.mkdir(parents=True, exist_ok=True)

    # hh, hv: filtered intensity, grey
    for c, name in enumerate(("hh", "hv")):
        lo, hi = disp["grey"][name]
        for t, d in enumerate(dates):
            img, vis = L[t, c], valid[t]
            if name.upper() in qc_done.get(d, []):
                vis = valid_in[t].cpu() & valid.any(0)
                xo = torch.nan_to_num(x_obs[t:t + 1].cpu().float(), nan=0.0)
                img = ops.to_loglike(ops.box_mean(xo, vis[None], o["knobs"]["box"]).clamp_min(1e-6), source)[0, c] * vis
            write_rgba(prod / name / f"{d}.tif", grey(img, lo, hi, vis), grid)

    # water: open water, flooded vegetation, usually-water-not-today
    for t, d in enumerate(dates):
        fv = o["flooded_veg"][t] & valid[t]
        going_under = fv & (o["fv_state"][t] == 2) if "fv_state" in o else torch.zeros_like(fv)
        img = paint([(o["wind_suspect"][t] & valid[t], WIND, 255),
                     (fv, FLOODED_VEG, 255),
                     (going_under, GOING_UNDER, 255),
                     (o["water"][t] & valid[t], WATER, 255)], (H, W))
        write_rgba(prod / "water" / f"{d}.tif", img, grid)

    # change vs first date on change_pol, specks removed
    thr, full = disp["change"]["thr"], disp["change"]["full"]
    diff = o["change_first"][:, pol]
    both = valid & valid[:1]
    big = (diff.abs() >= thr) & both
    big = ops.remove_small(big, o["knobs"]["min_blob_px"])
    alpha = (CHANGE_MIN_ALPHA + (1 - CHANGE_MIN_ALPHA) * ((diff.abs() - thr) / (full - thr)).clamp(0, 1)) * 255
    alpha = alpha.round().to(torch.uint8)
    for t in range(1, T):
        img = paint([(big[t] & (diff[t] < 0), DARKER, alpha[t]),
                     (big[t] & (diff[t] > 0), BRIGHTER, alpha[t])], (H, W))
        write_rgba(prod / "change" / f"{dates[t]}.tif", img, grid)

    # when: date of the lasting step (CUSUM change point k = first date of the new level)
    cd = o["change_date"].long()
    when_cols = viridis(T - 1)
    vany = valid.any(0)
    img = paint([((cd == k) & vany, when_cols[k - 1], 255) for k in range(1, T)], (H, W))
    write_rgba(prod / "when.tif", img, grid)

    # classes
    cls = o["classes"]
    step = o["change_step"]
    members = [cls == 1, cls == 2, cls == 3, (cls == 4) & (step < 0), (cls == 4) & (step >= 0)]
    img = paint([(m & vany, col, 255) for m, (col, _) in zip(members, CLASS_ITEMS)], (H, W))
    write_rgba(prod / "classes.tif", img, grid)

    # rgb: three-date composite on the change polarisation (first, middle, last)
    lo, hi = disp["grey"][pol_name]
    picks = [0, T // 2, T - 1]
    chans = [((L[t, pol] - lo) / (hi - lo)).clamp(0, 1).mul(255).round().to(torch.uint8) for t in picks]
    a3 = (valid[picks[0]] & valid[picks[1]] & valid[picks[2]]).to(torch.uint8) * 255
    write_rgba(prod / "rgb.tif", torch.stack(chans + [a3]), grid)

    # layers.json
    if source == "gamma0":
        hh_leg, hv_leg = ramp("dB", -25, -5), ramp("dB", -30, -10)
        ch_unit, ch_lim, kind = "dB", full, "backscatter"
    else:
        hh_leg, hv_leg = ramp(PREVIEW_UNIT, 0, 100), ramp(PREVIEW_UNIT, 0, 100)
        ch_unit, ch_lim, kind = PREVIEW_UNIT, round(full * 100), "brightness (preview)"
    cls_legend = lambda items: {"type": "classes", "items": [{"color": c, "label": l} for c, l in items]}
    extra = extra_layers(area, prod, grid, dates, valid, when_cols, cls_legend)
    layers = [
        {"id": "hh", "label": f"HH {kind}", "dated": True, "legend": hh_leg},
        {"id": "water", "label": "Water and flooded vegetation", "dated": True,
         "legend": cls_legend([(WATER, "Open water"), (FLOODED_VEG, "Possible water under vegetation")]
                              + ([(GOING_UNDER, "Going under: vegetation submerging, or part open water")] if "fv_state" in o else [])
                              + [(WIND, "Usually water, not today (rain or wind?)")])},
        {"id": "when", "label": "When it changed for good", "dated": False,
         "legend": cls_legend([(when_cols[k - 1], day_label(dates[k])) for k in range(1, T)])},
        {"id": "classes", "label": "Season classes", "dated": False, "legend": cls_legend(CLASS_ITEMS)},
        {"id": "change", "label": f"Change since first pass ({pol_name.upper()})", "dated": True,
         "legend": {"type": "diverging", "unit": ch_unit, "min": -ch_lim, "max": ch_lim,
                    "colors": [DARKER, "#00000000", BRIGHTER],
                    "labels": ["darker: new water, cleared forest", "brighter: flooded vegetation, growth"]}},
        {"id": "rgb", "label": "Three-date composite", "dated": False,
         "legend": {"type": "text", "text": (f"{pol_name.upper()}: red = {day_label(dates[picks[0]])}, "
                                             f"green = {day_label(dates[picks[1]])}, blue = {day_label(dates[picks[2]])}. "
                                             "Grey = no change.")}},
        {"id": "hv", "label": f"HV {kind}", "dated": True, "legend": hv_leg},
    ]
    (prod / "layers.json").write_text(json.dumps(layers, indent=2, ensure_ascii=False))

    # timeseries.json
    story_p = ROOT / "areas" / area / "story.json"
    spots = json.loads(story_p.read_text()).get("spots", []) if story_p.exists() else []
    raw = torch.nan_to_num(x.cpu().float(), nan=0.0)
    at = next(i for i, l in enumerate(layers) if l["id"] == "when") + 1
    layers[at:at] = extra
    (prod / "layers.json").write_text(json.dumps(layers, indent=2, ensure_ascii=False))
    series = []
    for s in spots:
        circle = grid.pixel_lonlat_dist(*s["lngLat"]) <= s.get("radius_m", 1000)
        n_c = int(circle.sum())
        row = {"spot": s["id"]}
        for c, name in enumerate(("hh", "hv")):
            vals = []
            for t in range(T):
                m = circle & valid[t]
                if n_c == 0 or int(m.sum()) < 0.5 * n_c or name.upper() in qc_done.get(dates[t], []):
                    vals.append(None)
                elif source == "gamma0":
                    vals.append(round(10 * math.log10(max(float(raw[t, c][m].mean()), 1e-10)), 2))
                else:
                    vals.append(round(float(L[t, c][m].mean()) * 100, 1))
            row[name] = vals
        series.append(row)
    km = lambda m: [round(float((m[t] & valid[t]).sum()) * km2, 1) for t in range(T)]
    ts = {"dates": dates, "unit": "dB" if source == "gamma0" else PREVIEW_UNIT, "series": series,
          "facts": [dict({"label": "Open water", "unit": "km²", "values": km(o["water"])},
                         **(area_range(area, T) if water_src == "math" else {})),
                    {"label": "Flooded vegetation", "unit": "km²", "values": km(o["flooded_veg"])}]
                   + ([{"label": "of which going under", "unit": "km²", "values": km(o["flooded_veg"] & (o["fv_state"] == 2))}]
                      if "fv_state" in o else [])}
    (prod / "timeseries.json").write_text(json.dumps(ts, indent=2, ensure_ascii=False))
    (prod / "source.json").write_text(json.dumps({"source": source, "credits": CREDITS[source]}, indent=2))
    print(f"{area}: source={source} grid={H}x{W} dates={T} spots={len(spots)} change_pol={pol_name}")
    print("  open water km2:", ts["facts"][0]["values"])
    print("  flooded veg km2:", ts["facts"][1]["values"])
    return prod


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("area")
    ap.add_argument("--source", choices=["auto", "gamma0", "preview"], default="auto")
    ap.add_argument("--cpu", action="store_true", help="run on CPU instead of the Apple GPU")
    a = ap.parse_args(argv)
    run(a.area, a.source, torch.device("cpu") if a.cpu else None)


if __name__ == "__main__":
    main()
