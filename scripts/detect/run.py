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


# ------------------------------- main ---------------------------------------
def run(area, source="auto", dev=None):
    if source == "auto":
        source = "gamma0" if any((ROOT / "data/processed" / area / "gamma0/hh").glob("*.tif")) else "preview"
    dates, x, valid_in, grid, km2 = load_gamma0(area) if source == "gamma0" else load_preview(area)
    dev = dev or device()
    from scripts.detect.terrain import cached_water_possible
    water_ok = cached_water_possible(area, tuple(x.shape[-2:]))
    print("  terrain mask:", "applied" if water_ok is not None else "not built for this area")
    out = ops.run_pipeline(x, valid_in, source, dev=dev, water_ok=water_ok)
    o = {k: (v.cpu() if torch.is_tensor(v) else v) for k, v in out.items()}
    L, valid, T = o["L"], o["valid"], len(dates)
    H, W = grid.h, grid.w
    cfg = load_area(area)
    pol_name = cfg.get("change_pol", "hh").lower()
    pol = {"hh": 0, "hv": 1}[pol_name]
    disp = DISPLAY[source]
    prod = ROOT / "data/processed" / area / "products"
    prod.mkdir(parents=True, exist_ok=True)

    # hh, hv: filtered intensity, grey
    for c, name in enumerate(("hh", "hv")):
        lo, hi = disp["grey"][name]
        for t, d in enumerate(dates):
            write_rgba(prod / name / f"{d}.tif", grey(L[t, c], lo, hi, valid[t]), grid)

    # water: open water, flooded vegetation, usually-water-not-today
    for t, d in enumerate(dates):
        img = paint([(o["wind_suspect"][t] & valid[t], WIND, 255),
                     (o["flooded_veg"][t] & valid[t], FLOODED_VEG, 255),
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
    layers = [
        {"id": "hh", "label": f"HH {kind}", "dated": True, "legend": hh_leg},
        {"id": "water", "label": "Water and flooded vegetation", "dated": True,
         "legend": cls_legend([(WATER, "Open water"), (FLOODED_VEG, "Flooded vegetation"),
                               (WIND, "Usually water, not today (wind?)")])},
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
    series = []
    for s in spots:
        circle = grid.pixel_lonlat_dist(*s["lngLat"]) <= s.get("radius_m", 1000)
        n_c = int(circle.sum())
        row = {"spot": s["id"]}
        for c, name in enumerate(("hh", "hv")):
            vals = []
            for t in range(T):
                m = circle & valid[t]
                if n_c == 0 or int(m.sum()) < 0.5 * n_c:
                    vals.append(None)
                elif source == "gamma0":
                    vals.append(round(10 * math.log10(max(float(raw[t, c][m].mean()), 1e-10)), 2))
                else:
                    vals.append(round(float(L[t, c][m].mean()) * 100, 1))
            row[name] = vals
        series.append(row)
    km = lambda m: [round(float((m[t] & valid[t]).sum()) * km2, 1) for t in range(T)]
    ts = {"dates": dates, "unit": "dB" if source == "gamma0" else PREVIEW_UNIT, "series": series,
          "facts": [{"label": "Open water", "unit": "km²", "values": km(o["water"])},
                    {"label": "Flooded vegetation", "unit": "km²", "values": km(o["flooded_veg"])}]}
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
