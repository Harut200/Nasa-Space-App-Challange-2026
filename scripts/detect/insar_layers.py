"""Ararat interferometric coherence as app layers (contract B), added to a place's existing products.

    .venv/bin/python -m scripts.detect.insar_layers ararat

Adds to data/processed/<area>/products/:
  coh/<date>.tif     "Coherence per 12-day pair": one frame per pair, dated by the pair's second pass
  coh_change.tif     "Summer to autumn: where the fields settled" (static, band 3 of the coherence GeoTIFF)
  layers.json        entries `coh`, `coh_change` (replaced if present, placed after `change`)
  timeseries.json    fact "Valley floor changed between passes" (% of valley floor with coherence < 0.3)
Idempotent: running twice gives the same files. Method and numbers: docs/eda/insar.md (section 3).
Reads data/processed/<area>-insar/ (GUNW windows, summer/autumn GeoTIFF) and data/external/dem/<area>_dem.tif.
Hook for scripts/detect/run.py: see the end of this file.
"""
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject

ROOT = Path(__file__).resolve().parents[2]

FACT_LABEL = "Valley floor changed between passes"
THRESH = 0.3                      # coherence below this = the surface changed between the two passes
COH_MAX = 0.8                     # ramp top: 0.8 and above is shown as the brightest colour
COH_COLORS = ["#2a0a3d", "#b5367a", "#fde9b0"]                     # dark = changed, bright = unchanged
DIFF_LIM = 0.4                    # diverging legend limit (coherence difference)
DIFF_COLORS = ["#b2182b", "#00000000", "#2166ac"]                  # red = lost, blue = became coherent
DIFF_FADE = (0.06, 0.18)          # |difference| below the first is transparent, full colour at the second

COH_LAYER = {"id": "coh", "label": "Coherence per 12-day pair", "dated": True,
             "legend": {"type": "ramp", "unit": "coherence · dark = changed, bright = unchanged",
                        "min": 0, "max": COH_MAX, "colors": COH_COLORS}}
DIFF_LAYER = {"id": "coh_change", "label": "Summer to autumn: where the fields settled", "dated": False,
              "legend": {"type": "diverging", "unit": "autumn minus summer coherence", "min": -DIFF_LIM, "max": DIFF_LIM,
                         "colors": DIFF_COLORS,
                         "labels": ["lost coherence", "became coherent by autumn (active in summer)"]}}


def _hex(c):
    return [int(c[i:i + 2], 16) for i in (1, 3, 5)]


def ramp_rgb(t, colors):
    """t in 0..1 (array) -> [3, ...] uint8 along a list of '#rrggbb' stops."""
    stops = np.array([_hex(c) for c in colors], float)
    x = np.clip(t, 0, 1) * (len(stops) - 1)
    i = np.minimum(np.floor(x).astype(int), len(stops) - 2)
    f = (x - i)[..., None]
    rgb = stops[i] * (1 - f) + stops[i + 1] * f
    return np.moveaxis(np.round(rgb), -1, 0).astype(np.uint8)


def coh_rgba(coh):
    """Coherence [H, W] float (NaN = no data) -> RGBA uint8 [4, H, W]."""
    ok = np.isfinite(coh)
    rgb = ramp_rgb(np.nan_to_num(coh) / COH_MAX, COH_COLORS)
    return np.concatenate([rgb, (ok * 255).astype(np.uint8)[None]])


def diff_rgba(d):
    """Autumn minus summer coherence [H, W] float (NaN = no data) -> RGBA uint8; small |d| is transparent."""
    ok = np.isfinite(d)
    d0 = np.nan_to_num(d)
    rgb = ramp_rgb((d0 + DIFF_LIM) / (2 * DIFF_LIM), [DIFF_COLORS[0], "#bdbdbd", DIFF_COLORS[2]])
    lo, hi = DIFF_FADE
    alpha = np.clip((np.abs(d0) - lo) / (hi - lo), 0, 1) * 255 * ok
    return np.concatenate([rgb, np.round(alpha).astype(np.uint8)[None]])


def decode_diff(band3):
    """Band 3 of the summer/autumn GeoTIFF: 125 + 250 * (autumn - summer), 255 = no data."""
    b = band3.astype(np.float64)
    return np.where(b == 255, np.nan, (b - 125) / 250)


def floor_share_below(coh, floor, thr=THRESH):
    """Per pair: percent of valley-floor pixels with a valid coherence that are below `thr` (None if no pixel)."""
    out = []
    for c in coh:
        m = floor & np.isfinite(c)
        out.append(round(100 * float((c[m] < thr).mean()), 1) if m.any() else None)
    return out


def to_product_grid(arr, src_transform, src_crs, ref):
    """Resample a float array (NaN = no data) onto the product grid of the reference raster (area-average)."""
    out = np.full((ref.height, ref.width), np.nan, np.float32)
    reproject(arr.astype(np.float32), out, src_transform=src_transform, src_crs=src_crs, src_nodata=np.nan,
              dst_transform=ref.transform, dst_crs=ref.crs, dst_nodata=np.nan, resampling=Resampling.average)
    return out


def write_rgba(path, rgba, ref):
    """RGBA uint8 [4, H, W] -> deflate GeoTIFF on the grid of `ref` (same layout as scripts/detect/run.py)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=ref.height, width=ref.width, count=4, dtype="uint8",
                       crs=ref.crs, transform=ref.transform, compress="deflate", photometric="RGB") as o:
        o.write(rgba)
        o.colorinterp = [rasterio.enums.ColorInterp.red, rasterio.enums.ColorInterp.green,
                         rasterio.enums.ColorInterp.blue, rasterio.enums.ColorInterp.alpha]


def write_layers(prod, coh, floor, pair_dates, diff, src_transform, src_crs):
    """Write the GeoTIFFs and update layers.json and timeseries.json under `prod`. Returns the layer ids added.

    coh [K, H, W] float, NaN = no data; floor [H, W] bool (valley floor on the same grid); pair_dates: the K second-pass
    dates; diff [H, W] autumn minus summer; the source grid is given by src_transform / src_crs."""
    prod = Path(prod)
    lj, tj = prod / "layers.json", prod / "timeseries.json"
    layers, ts = json.loads(lj.read_text()), json.loads(tj.read_text())
    dates = ts["dates"]
    missing = [d for d in pair_dates if d not in dates]
    if missing:
        raise ValueError(f"pair dates not among the place's passes: {missing}")
    with rasterio.open(next(prod.glob("*/*.tif"), None) or next(prod.glob("*.tif"))) as ref:
        for k, d in enumerate(pair_dates):
            write_rgba(prod / "coh" / f"{d}.tif", coh_rgba(to_product_grid(coh[k], src_transform, src_crs, ref)), ref)
        write_rgba(prod / "coh_change.tif", diff_rgba(to_product_grid(diff, src_transform, src_crs, ref)), ref)
    shares = dict(zip(pair_dates, floor_share_below(coh, floor)))
    fact = {"label": FACT_LABEL, "unit": "%", "values": [shares.get(d) for d in dates]}  # null on dates with no pair
    ts["facts"] = [f for f in ts.get("facts", []) if f.get("label") != FACT_LABEL] + [fact]
    layers = [l for l in layers if l["id"] not in ("coh", "coh_change")]
    at = next((i for i, l in enumerate(layers) if l["id"] == "change"), len(layers) - 1) + 1
    layers[at:at] = [json.loads(json.dumps(COH_LAYER)), json.loads(json.dumps(DIFF_LAYER))]
    lj.write_text(json.dumps(layers, indent=2, ensure_ascii=False))
    tj.write_text(json.dumps(ts, indent=2, ensure_ascii=False))
    return ["coh", "coh_change"]


def load_inputs(area, root=ROOT):
    """Coherence stack, valley-floor mask and summer/autumn difference from data/processed/<area>-insar/."""
    from scripts.detect import insar
    P = insar.prepare(area)
    floor = insar.ararat_regions(P, P["dem"])["valley floor (irrigated plain)"]
    coh = P["coherenceMagnitude"].astype(np.float64)
    pair_dates = ["{}-{}-{}".format(b[:4], b[4:6], b[6:]) for _, b in P["pairs"]]
    with rasterio.open(Path(root) / "data/processed" / f"{area}-insar" / f"{area}_coherence_summer_autumn.tif") as r:
        diff, tr, crs = decode_diff(r.read(3)), r.transform, r.crs
    if diff.shape != coh.shape[1:]:
        raise ValueError(f"summer/autumn tif {diff.shape} does not match the coherence stack {coh.shape[1:]}")
    return coh, floor, pair_dates, diff, tr, crs


def add_to_products(area, root=ROOT):
    """Add the coherence layers and the valley-floor fact to <root>/data/processed/<area>/products. Idempotent."""
    coh, floor, pair_dates, diff, tr, crs = load_inputs(area, root)
    prod = Path(root) / "data/processed" / area / "products"
    return write_layers(prod, coh, floor, pair_dates, diff, tr, crs)


# Hook for scripts/detect/run.py (after both `(prod / "layers.json").write_text(...)` / timeseries.json writes, i.e. right
# after `(prod / "timeseries.json").write_text(json.dumps(ts, ...))` inside run(), before the final print):
#     if area == "ararat":
#         from scripts.detect.insar_layers import add_to_products; add_to_products(area)
if __name__ == "__main__":
    print(add_to_products(sys.argv[1] if len(sys.argv) > 1 else "ararat"))
