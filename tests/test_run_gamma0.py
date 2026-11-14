"""scripts/detect/run.py on a tiny synthetic calibrated (gamma0) area in a temp dir: linear power in,
dB out, source flag and credits. Run: .venv/bin/python -m pytest tests/test_run_gamma0.py"""
import json
import math
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

import scripts.detect.calibrated as cal
import scripts.detect.qc as qcmod
import scripts.detect.run as run
import scripts.detect.stack as stack

N, PIX, AREA = 100, 80.0, "gtest-cal"
DATES = ["2026-06-19", "2026-07-01", "2026-07-13", "2026-07-25"]
LAND_DB, WATER_HH, WATER_HV, LAND_HV = -8.0, -22.0, -28.0, -14.0
CENTRE = (50, 50)


def build(root: Path):
    T = from_origin(500000.0, 1400000.0, PIX, PIX)
    rng = np.random.default_rng(1)
    rr, cc = np.mgrid[:N, :N]
    dist = np.hypot(rr - CENTRE[0], cc - CENTRE[1])
    for pol, land, wat in (("hh", LAND_DB, WATER_HH), ("hv", LAND_HV, WATER_HV)):
        for i, d in enumerate(DATES):
            db = np.where(dist < 8 + 8 * i, wat, land)  # a lake that grows each date
            lin = 10 ** (db / 10) * rng.gamma(60.0, 1 / 60.0, (N, N))  # multiplicative speckle, ENL 60
            p = root / "data/processed" / AREA / "gamma0" / pol / f"{d}.tif"
            p.parent.mkdir(parents=True, exist_ok=True)
            with rasterio.open(p, "w", driver="GTiff", height=N, width=N, count=1, dtype="float32",
                               crs="EPSG:32648", transform=T) as o:
                o.write(lin.astype("float32"), 1)
    lon, lat = rasterio.warp.transform("EPSG:32648", "EPSG:4326", [500000.0 + 50.5 * PIX], [1400000.0 - 50.5 * PIX])
    a = root / "areas" / AREA
    a.mkdir(parents=True)
    (a / "area.json").write_text(json.dumps({"id": AREA, "name": "synthetic", "bbox": [0, 0, 1, 1],
                                             "pixel_m": PIX, "extra_layers": []}))
    (a / "story.json").write_text(json.dumps({"spots": [
        {"id": "land", "label": "land", "lngLat": [lon[0] + 0.03, lat[0] + 0.03], "radius_m": 400},
        {"id": "lake", "label": "lake", "lngLat": [lon[0], lat[0]], "radius_m": 300}]}))
    return lon[0], lat[0]


def test_run_gamma0_db_path(tmp_path, monkeypatch):
    for m in (run, stack, qcmod, cal):
        monkeypatch.setattr(m, "ROOT", tmp_path)
    build(tmp_path)
    prod = run.run(AREA, "gamma0")
    ts = json.loads((prod / "timeseries.json").read_text())
    src = json.loads((prod / "source.json").read_text())
    layers = json.loads((prod / "layers.json").read_text())
    assert src["source"] == "gamma0" and "PROVISIONAL" in src["credits"] and "GCOV" in src["credits"]
    assert ts["unit"] == "dB" and ts["dates"] == DATES
    lake = next(s for s in ts["series"] if s["spot"] == "lake")
    # the lake centre is water on every date: HH about -22 dB, HV about -28 dB, negative dB values
    assert all(-24 < v < -20 for v in lake["hh"]), lake["hh"]
    assert all(-30 < v < -26 for v in lake["hv"]), lake["hv"]
    ow = next(f for f in ts["facts"] if f["label"] == "Open water")
    assert ow["unit"] == "km²" and "lo" in ow and "hi" in ow
    v = ow["values"]
    assert v == sorted(v) and v[-1] > 2 * v[0] > 0
    expect = math.pi * (8 + 8 * 3) ** 2 * (PIX / 1000) ** 2  # km2 of the last disc
    assert abs(v[-1] - expect) / expect < 0.15, (v[-1], expect)
    assert all(lo - 1 <= x <= hi + 1 for lo, x, hi in zip(ow["lo"], v, ow["hi"]))
    by = {L["id"]: L for L in layers}
    assert [L["id"] for L in layers] == ["hh", "water", "when", "classes", "change", "rgb", "hv"]
    assert by["hh"]["legend"]["unit"] == "dB" and by["hh"]["legend"]["min"] == -25 and by["hh"]["legend"]["max"] == -3
    assert by["change"]["legend"]["unit"] == "dB" and by["change"]["legend"]["max"] == 6.0
    assert "sig" not in by
