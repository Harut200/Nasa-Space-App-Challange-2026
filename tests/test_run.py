"""scripts/detect/run.py on the synthetic demo area (contract A -> contract B).
Run: .venv/bin/python -m pytest tests/test_run.py
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import rasterio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/ingest"))
import make_fixture as mf  # noqa: E402

PROD = ROOT / "data/processed/demo/products"
DATED = ("hh", "hv", "water", "change")
UNDATED = ("when", "classes", "rgb")


def read(p):
    with rasterio.open(p) as r:
        return r.read(), (r.crs, tuple(r.transform), r.height, r.width), r.count, r.dtypes


def test_run_demo():
    mf.main()
    subprocess.run([sys.executable, str(ROOT / "scripts/detect/run.py"), "demo", "--source", "gamma0", "--cpu"],
                   check=True, cwd=ROOT)
    with rasterio.open(ROOT / "data/processed/demo/gamma0/hh" / f"{mf.DATES[0]}.tif") as r:
        grid_a = (r.crs, tuple(r.transform), r.height, r.width)

    files = [PROD / L / f"{d}.tif" for L in DATED for d in (mf.DATES[1:] if L == "change" else mf.DATES)]
    files += [PROD / f"{L}.tif" for L in UNDATED]
    assert not (PROD / "change" / f"{mf.DATES[0]}.tif").exists()
    for f in files:
        assert f.exists(), f
        _, grid, count, dtypes = read(f)
        assert count == 4 and set(dtypes) == {"uint8"}, f
        assert grid == grid_a, f"{f.name}: not on the contract A grid"

    rr, cc = np.mgrid[:mf.N, :mf.N]
    dist = np.hypot(rr - mf.DISC_RC[0], cc - mf.DISC_RC[1])
    r_last = 20 + 20 * (len(mf.DATES) - 1)
    inside, far = dist < r_last - 10, dist > r_last + 30

    w, *_ = read(PROD / "water" / f"{mf.DATES[-1]}.tif")
    blue = (w[3] == 255) & (w[0] == 0x2F) & (w[1] == 0x6F) & (w[2] == 0xDE)
    assert blue[inside].mean() > 0.9, blue[inside].mean()

    for d in mf.DATES[1:]:
        c, *_ = read(PROD / "change" / f"{d}.tif")
        assert (c[3][far] == 0).mean() >= 0.99, (d, (c[3][far] == 0).mean())
    c, *_ = read(PROD / "change" / f"{mf.DATES[-1]}.tif")
    ring = (dist < r_last - 10) & (dist > 30)  # water only at the last dates: darker -> blue
    assert ((c[3][ring] > 0) & (c[2][ring] == 0xAC)).mean() > 0.9

    layers = json.loads((PROD / "layers.json").read_text())
    assert [L["id"] for L in layers] == ["hh", "water", "when", "classes", "change", "rgb", "hv"]
    for L in layers:
        assert {"id", "label", "dated", "legend"} <= set(L) and "type" in L["legend"]
        assert L["dated"] == (L["id"] in DATED)
    by = {L["id"]: L for L in layers}
    assert by["water"]["legend"]["type"] == by["when"]["legend"]["type"] == "classes"
    assert len(by["when"]["legend"]["items"]) == len(mf.DATES) - 1

    ts = json.loads((PROD / "timeseries.json").read_text())
    assert ts["dates"] == mf.DATES and isinstance(ts["series"], list) and ts["unit"] == "dB"
    water_km2 = ts["facts"][0]["values"]
    assert len(water_km2) == len(mf.DATES) and water_km2[-1] > 5 * water_km2[0] > 0
    src = json.loads((PROD / "source.json").read_text())
    assert src["source"] == "gamma0" and src["credits"]


if __name__ == "__main__":
    test_run_demo()
    print("ok")
