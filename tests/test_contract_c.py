"""Contract C (backend -> frontend). Run: .venv/bin/python tests/test_contract_c.py"""
import json, math, re, sys, tempfile
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/ingest"))
import make_fixture as mf
from export import export

REAL = ROOT / "src/app/public/data"
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
nums_or_null = lambda a, n: isinstance(a, list) and len(a) == n and all(v is None or num(v) for v in a)
listing = lambda p: sorted(str(f.relative_to(p)) for f in p.rglob("*")) if p.exists() else []


def check_legend(g):  # what App.jsx Legend renders
    if g["type"] == "text":
        assert isinstance(g["text"], str)
    else:
        assert num(g["min"]) and num(g["max"]) and isinstance(g["unit"], str), g
        assert isinstance(g["colors"], list) and g["colors"] and all(isinstance(c, str) for c in g["colors"])


def check_area(pub, area):
    out = pub / area
    assert json.loads((pub / "index.json").read_text())["areas"] == [area]
    m = json.loads((out / "manifest.json").read_text())
    assert m["id"] == area and isinstance(m["name"], str) and isinstance(m["credits"], str)
    w, s, e, n = m["bounds"]
    assert all(num(v) for v in m["bounds"]) and w < e and s < n
    D = m["dates"]
    assert D == sorted(D) and len(set(D)) == len(D) and all(DATE.match(d) for d in D)
    assert isinstance(m["gaps"], list) and all(
        isinstance(g, list) and len(g) == 2 and all(DATE.match(d) for d in g) for g in m["gaps"])
    assert set(m["story"]) >= {"see", "physics", "lband", "people"}
    assert all(isinstance(m["story"][k], str) for k in ("see", "physics", "lband", "people"))
    assert m["spots"] and all(isinstance(p["id"], str) and isinstance(p["label"], str) and
                              len(p["lngLat"]) == 2 and all(num(v) for v in p["lngLat"]) for p in m["spots"])
    sizes = set()
    for L in m["layers"]:
        assert {"id", "label", "dates", "legend"} <= set(L)
        check_legend(L["legend"])
        assert L["dates"] is None or (L["dates"] and set(L["dates"]) <= set(D)), L["id"]
        for f in [out / L["id"] / f"{d}.webp" for d in L["dates"]] if L["dates"] else [out / f"{L['id']}.webp"]:
            assert f.exists(), f
            assert f.read_bytes()[:4] == b"RIFF" and f.read_bytes()[8:12] == b"WEBP", f
            with Image.open(f) as im:
                assert im.format == "WEBP" and max(im.size) <= 3000
                im.convert("RGBA")
                sizes.add(im.size)
    assert len(sizes) == 1, sizes
    t = json.loads((out / "timeseries.json").read_text())
    n = len(t["dates"])
    assert all(DATE.match(d) for d in t["dates"])
    for r in t["series"]:
        assert isinstance(r["spot"], str) and nums_or_null(r["hh"], n) and nums_or_null(r["hv"], n)
    for f in t.get("facts", []):
        assert isinstance(f["label"], str) and nums_or_null(f["values"], n)
    return m, t


def test_export_output():
    before = listing(REAL)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        mf.main(root)
        (root / "areas/demo/story.json").write_text(json.dumps({
            "story": {k: "x" for k in ("see", "physics", "lband", "people")},
            "spots": [{"id": "a", "label": "A", "lngLat": [mf.DISC_LON, mf.DISC_LAT]}]}))
        # a dated layer with no .tif files must be dropped from the manifest
        lj = root / "data/processed/demo/products/layers.json"
        L = json.loads(lj.read_text())
        lj.write_text(json.dumps(L + [{**L[0], "id": "ghost", "label": "Ghost"}]))
        export("demo", root)
        m, _ = check_area(root / "src/app/public/data", "demo")
        ids = [x["id"] for x in m["layers"]]
        assert ids == ["hh", "hv"], ids
    assert listing(REAL) == before, "fixture/export touched the real web app data"


def test_real_app():
    idx = json.loads((REAL / "index.json").read_text())["areas"]
    assert "demo" not in idx and not (REAL / "demo").exists()
    for a in idx:
        assert json.loads((REAL / a / "manifest.json").read_text())["id"] == a


if __name__ == "__main__":
    test_export_output()
    test_real_app()
    print("contract ok")
