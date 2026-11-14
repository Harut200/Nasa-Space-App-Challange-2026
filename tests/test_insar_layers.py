"""Synthetic checks for scripts/detect/insar_layers.py (small rasters in a temp dir, no real data)."""
import json

import numpy as np
import rasterio
import rasterio.warp
from rasterio.crs import CRS
from rasterio.transform import from_bounds, from_origin

from scripts.detect import insar_layers as il

DATES = ["2026-06-20", "2026-07-02", "2026-07-14", "2026-07-26"]


def _setup(tmp_path):
    prod = tmp_path / "products"
    prod.mkdir()
    ref_t = from_bounds(44.0, 40.0, 44.4, 40.3, 40, 30)           # product grid, EPSG:4326, 30 x 40
    with rasterio.open(prod / "rgb.tif", "w", driver="GTiff", height=30, width=40, count=4, dtype="uint8",
                       crs="EPSG:4326", transform=ref_t) as o:
        o.write(np.zeros((4, 30, 40), np.uint8))
    (prod / "layers.json").write_text(json.dumps([{"id": "hh", "label": "HH", "dated": True, "legend": {}},
                                                  {"id": "change", "label": "C", "dated": True, "legend": {}},
                                                  {"id": "rgb", "label": "R", "dated": False, "legend": {}}]))
    (prod / "timeseries.json").write_text(json.dumps(
        {"dates": DATES, "series": [], "facts": [{"label": "Open water", "unit": "km²", "values": [1, 1, 1, 1]}]}))
    # source: UTM 38N 80 m, 60 x 80 px around 44.2E 40.15N (a 6 x 5 km patch inside the product grid)
    (x0,), (y0,) = rasterio.warp.transform("EPSG:4326", "EPSG:32638", [44.1], [40.2])
    tr = from_origin(x0, y0, 80.0, 80.0)
    rng = np.random.default_rng(0)
    coh = rng.uniform(0.1, 0.8, (3, 60, 80)).astype(np.float64)
    coh[0, :5, :5] = np.nan
    floor = np.zeros((60, 80), bool)
    floor[:, :40] = True
    diff = np.full((60, 80), 0.0)
    diff[:, :20], diff[:, 20:40] = 0.3, -0.3
    diff[0, 0] = np.nan
    return prod, coh, floor, DATES[1:], diff, tr, CRS.from_epsg(32638)


def test_idempotent_and_layers_added(tmp_path):
    args = _setup(tmp_path)
    prod = args[0]
    assert il.write_layers(*args) == ["coh", "coh_change"]
    first = {p.name: p.read_bytes() for p in [prod / "layers.json", prod / "timeseries.json", prod / "coh_change.tif"]}
    il.write_layers(*args)
    for n, b in first.items():
        assert (prod / n).read_bytes() == b
    layers = json.loads((prod / "layers.json").read_text())
    assert [l["id"] for l in layers] == ["hh", "change", "coh", "coh_change", "rgb"]
    assert sorted(p.stem for p in (prod / "coh").glob("*.tif")) == DATES[1:]
    ts = json.loads((prod / "timeseries.json").read_text())
    assert [f["label"] for f in ts["facts"]] == ["Open water", il.FACT_LABEL]


def test_legend_shape(tmp_path):
    args = _setup(tmp_path)
    il.write_layers(*args)
    L = {l["id"]: l for l in json.loads((args[0] / "layers.json").read_text())}
    assert L["coh"]["dated"] is True and L["coh"]["legend"]["type"] == "ramp"
    assert set(L["coh"]["legend"]) >= {"unit", "min", "max", "colors"} and len(L["coh"]["legend"]["colors"]) >= 2
    g = L["coh_change"]["legend"]
    assert L["coh_change"]["dated"] is False and g["type"] == "diverging" and g["min"] == -g["max"]
    assert len(g["colors"]) == 3 and g["colors"][1].endswith("00") and len(g["labels"]) == 2


def test_fact_null_first_and_values(tmp_path):
    args = _setup(tmp_path)
    il.write_layers(*args)
    f = json.loads((args[0] / "timeseries.json").read_text())["facts"][-1]
    assert f["unit"] == "%" and len(f["values"]) == len(DATES) and f["values"][0] is None
    coh, floor = args[1], args[2]
    for k in range(3):
        m = floor & np.isfinite(coh[k])
        assert abs(f["values"][k + 1] - 100 * (coh[k][m] < 0.3).mean()) < 0.06


def test_grid_match_and_transparency(tmp_path):
    args = _setup(tmp_path)
    il.write_layers(*args)
    prod = args[0]
    with rasterio.open(prod / "rgb.tif") as ref, rasterio.open(prod / "coh" / f"{DATES[1]}.tif") as a, \
            rasterio.open(prod / "coh_change.tif") as b:
        for r in (a, b):
            assert (r.crs, r.transform, r.shape, r.count, r.dtypes[0]) == (ref.crs, ref.transform, ref.shape, 4, "uint8")
        alpha = b.read(4)
    assert alpha.min() == 0 and alpha.max() == 255          # |diff| 0 is transparent, |diff| 0.3 is opaque


def test_dates_must_be_passes(tmp_path):
    prod, coh, floor, _, diff, tr, crs = _setup(tmp_path)
    try:
        il.write_layers(prod, coh, floor, ["2026-01-01"] * 3, diff, tr, crs)
    except ValueError:
        return
    raise AssertionError("expected ValueError")
