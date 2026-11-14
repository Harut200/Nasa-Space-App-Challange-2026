"""Run: .venv/bin/python tests/test_export.py"""
import json, math, sys, tempfile
from pathlib import Path
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/ingest"))
import make_fixture as mf
from export import export

R = 6378137.0
merc_x = lambda lon: math.radians(lon) * R
merc_y = lambda lat: R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def test():
    with tempfile.TemporaryDirectory() as td:  # never write the demo area into the real web app
        root = Path(td)
        mf.main(root)
        export("demo", root)
        check(root / "src/app/public/data")


def check(pub):
    out = pub / "demo"
    m = json.loads((out / "manifest.json").read_text())
    for k in ("id", "name", "bounds", "dates", "gaps", "layers", "story", "spots", "credits"):
        assert k in m, k
    for L in m["layers"]:
        assert {"id", "label", "dates", "legend"} <= set(L)
    assert "demo" in json.loads((pub / "index.json").read_text())["areas"]
    assert (out / "timeseries.json").exists()

    w, s, e, n = m["bounds"]
    for d in (mf.DATES[0], mf.DATES[-1]):
        a = np.asarray(Image.open(out / "hh" / f"{d}.webp").convert("RGBA")).astype(float)
        H, W = a.shape[:2]
        # continuous pixel coords of the disc centre; pixel i covers [i, i+1) so centre pixel = floor
        px = (merc_x(mf.DISC_LON) - merc_x(w)) / (merc_x(e) - merc_x(w)) * W
        py = (merc_y(n) - merc_y(mf.DISC_LAT)) / (merc_y(n) - merc_y(s)) * H
        dark = (a[..., 3] > 0) & (a[..., 0] < 100)  # background ~ 166, disc ~ 13
        ys, xs = np.nonzero(dark)
        cx, cy = xs.mean() + 0.5, ys.mean() + 0.5  # centroid in continuous coords
        assert dark[int(py), int(px)], "predicted pixel is not dark"
        assert abs(cx - px) <= 1 and abs(cy - py) <= 1, (d, cx, px, cy, py)
    print("ok")


if __name__ == "__main__":
    test()
