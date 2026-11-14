"""Synthetic `demo` area: contract A, a grey contract B stand-in, then export (contract C)."""
import json
from pathlib import Path
import numpy as np
import rasterio
from rasterio.transform import from_origin, xy
from rasterio.warp import transform
from export import export

ROOT = Path(__file__).resolve().parents[2]
DATES = ["2026-06-20", "2026-07-02", "2026-07-14", "2026-08-10", "2026-08-22", "2026-09-03"]
CRS, N, PIXEL = "EPSG:32648", 500, 100  # UTM 48N, 50 km square
ORIGIN = (500000.0, 1450000.0)  # upper-left corner of the grid, metres
T = from_origin(*ORIGIN, PIXEL, PIXEL)
DISC_RC = (200, 300)  # disc centre pixel (row, col)
_x, _y = xy(T, *DISC_RC)  # pixel centre
DISC_LON, DISC_LAT = (v[0] for v in transform(CRS, "EPSG:4326", [_x], [_y]))
_bl = transform(CRS, "EPSG:4326", [ORIGIN[0], ORIGIN[0] + N * PIXEL], [ORIGIN[1] - N * PIXEL, ORIGIN[1]])
BBOX = [round(_bl[0][0], 4), round(_bl[1][0], 4), round(_bl[0][1], 4), round(_bl[1][1], 4)]
RANGES = {"hh": (-25, -5), "hv": (-30, -10)}


def write(path, arr, **kw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=N, width=N, count=arr.shape[0] if arr.ndim == 3 else 1,
                       dtype=arr.dtype, crs=CRS, transform=T, compress="deflate", **kw) as dst:
        dst.write(arr if arr.ndim == 3 else arr[None])


def main():
    rng = np.random.default_rng(42)
    rr, cc = np.mgrid[:N, :N]
    dist = np.hypot(rr - DISC_RC[0], cc - DISC_RC[1])
    area, A, P = ROOT / "areas/demo", ROOT / "data/processed/demo/gamma0", ROOT / "data/processed/demo/products"
    area.mkdir(parents=True, exist_ok=True)
    (area / "area.json").write_text(json.dumps(
        {"id": "demo", "name": "Demo (synthetic)", "bbox": BBOX, "track": None, "frame": None,
         "direction": None, "pixel_m": PIXEL}, indent=2))
    for pol, (lo, hi) in RANGES.items():
        base_db = (lo + hi) / 2 + 3  # bright background, 3 dB above mid-range
        for i, d in enumerate(DATES):
            db = np.where(dist < 20 + 20 * i, base_db - 12, base_db) + rng.normal(0, 1, (N, N))
            g = (10 ** (db / 10)).astype("float32")
            write(A / pol / f"{d}.tif", g, nodata=np.nan)
            # stand-in grey contract B until scripts/detect/run.py exists
            v = ((db - lo) / (hi - lo) * 255).clip(0, 255).astype("uint8")
            write(P / pol / f"{d}.tif", np.stack([v, v, v, np.full_like(v, 255)]))
    legend = lambda lo, hi: {"type": "ramp", "unit": "dB", "min": lo, "max": hi, "colors": ["#000000", "#ffffff"]}
    (P / "layers.json").write_text(json.dumps([
        {"id": "hh", "label": "HH backscatter", "dated": True, "legend": legend(-25, -5)},
        {"id": "hv", "label": "HV backscatter", "dated": True, "legend": legend(-30, -10)}], indent=2))
    (P / "timeseries.json").write_text(json.dumps({"dates": DATES, "series": []}, indent=2))
    export("demo")


if __name__ == "__main__":
    main()
