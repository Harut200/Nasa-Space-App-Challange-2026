"""scripts/ingest/optimize.py on a tiny synthetic data folder (never touches the real public data)."""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/ingest"))
import optimize as opt  # noqa: E402

CLASSES = ["#2f6fde", "#35b04a", "#f28e2b"]


def _class_img(w=120, h=100, seed=0):
    rng = np.random.default_rng(seed)
    pal = np.array([opt.hex_rgb(c) for c in CLASSES], np.uint8)
    k = (np.indices((h, w))[1] // 20 + np.indices((h, w))[0] // 25) % 3
    rgba = np.zeros((h, w, 4), np.uint8)
    rgba[..., :3] = pal[k]
    rgba[..., 3] = 255
    rgba[:30, :40] = 0                                   # transparent corner
    noisy = rgba.copy()                                  # what a lossy export looks like
    noisy[..., :3] = np.clip(noisy[..., :3].astype(int) + rng.integers(-4, 5, (h, w, 3)), 0, 255)
    return rgba, noisy


def _grey_img(w=160, h=140, seed=1):
    rng = np.random.default_rng(seed)
    g = np.clip(rng.normal(90, 40, (h, w)), 0, 255).astype(np.uint8)
    rgba = np.dstack([g, g, g, np.full((h, w), 255, np.uint8)])
    rgba[:20, :20, 3] = 0
    return rgba


def _build(tmp, season=True):
    pub = Path(tmp)
    rgba, noisy = _class_img()
    grey = _grey_img()
    dates = ["2026-06-01", "2026-06-13"]
    layers = [
        {"id": "hh", "label": "HH", "dates": dates, "legend": {"type": "ramp", "colors": ["#000000", "#ffffff"]}},
        {"id": "water", "label": "Water", "dates": dates,
         "legend": {"type": "classes", "items": [{"color": c, "label": c} for c in CLASSES]}},
    ]
    p = pub / "t1"
    for d in dates:
        (p / "hh").mkdir(parents=True, exist_ok=True)
        (p / "water").mkdir(parents=True, exist_ok=True)
        Image.fromarray(grey, "RGBA").save(p / "hh" / f"{d}.webp", quality=80)
        Image.fromarray(noisy, "RGBA").save(p / "water" / f"{d}.webp", quality=80)
    m = {"id": "t1", "name": "Test Lake, Testland", "bounds": [10, 20, 11, 21], "dates": dates, "gaps": [],
         "layers": layers, "source": "gamma0"}
    (p / "manifest.json").write_text(json.dumps(m))
    ts = {"dates": dates, "unit": "dB", "series": [], "facts": [
        {"label": "Open water", "unit": "km²", "values": [10.04, 12.5], "lo": [9, 11], "hi": [11, 14]}]}
    (p / "timeseries.json").write_text(json.dumps(ts))
    (pub / "index.json").write_text(json.dumps({"areas": ["t1"]}))
    return pub, p


def test_class_map_exact_colours_and_alpha(tmp_path):
    pub, p = _build(tmp_path)
    ref_alpha = np.array(Image.open(p / "water/2026-06-01.webp").convert("RGBA"))[..., 3]
    opt.run(["t1"], pub, verbose=False)
    out = np.array(Image.open(p / "water/2026-06-01.webp").convert("RGBA"))
    op = out[..., 3] > 0
    legend = {opt.hex_rgb(c) for c in CLASSES}
    colours = {tuple(int(x) for x in c) for c in np.unique(out[op][:, :3], axis=0)}
    assert colours <= legend and len(colours) == 3       # exactly the legend colours
    assert (out[..., 3] == ref_alpha).all()              # transparent stays transparent
    assert not op[:30, :40].any()


def test_grey_lossy_keeps_alpha_and_size(tmp_path):
    pub, p = _build(tmp_path)
    before = np.array(Image.open(p / "hh/2026-06-01.webp").convert("RGBA"))
    opt.run(["t1"], pub, verbose=False)
    after = np.array(Image.open(p / "hh/2026-06-01.webp").convert("RGBA"))
    assert after.shape == before.shape
    assert (after[..., 3] == before[..., 3]).all()
    op = before[..., 3] > 0
    mse = ((after[op][:, :3].astype(float) - before[op][:, :3].astype(float)) ** 2).mean()
    assert 10 * np.log10(255 ** 2 / max(mse, 1e-9)) > 28


def test_idempotent(tmp_path):
    pub, p = _build(tmp_path)
    opt.run(["t1"], pub, verbose=False)
    snap = {f: f.read_bytes() for f in pub.rglob("*") if f.is_file() and f.name != "summary.json"}
    opt.run(["t1"], pub, verbose=False)
    for f, b in snap.items():
        assert f.read_bytes() == b, f


def test_never_bigger_and_thumbs(tmp_path):
    pub, p = _build(tmp_path)
    b = sum(f.stat().st_size for f in p.rglob("*.webp"))
    opt.run(["t1"], pub, verbose=False)
    assert sum(f.stat().st_size for f in p.rglob("*.webp")) <= b
    t = pub / "thumbs" / "t1.webp"
    assert t.exists() and t.stat().st_size <= opt.THUMB_MAX
    assert Image.open(t).width <= opt.THUMB_W
    t2 = pub / "thumbs" / "t1@2x.webp"
    assert not t2.exists()                               # source narrower than 960 px: no @2x


def test_summary_schema(tmp_path):
    pub, _ = _build(tmp_path)
    opt.run(["t1"], pub, verbose=False)
    s = json.loads((pub / "summary.json").read_text())
    assert set(s) == {"generated", "categories", "places"}
    assert s["categories"] == [{"id": "other", "label": "Other", "n": 1}]
    pl = s["places"][0]
    for k in ("id", "name", "kind", "eventType", "source", "centre", "bounds", "dates", "defaultLayer", "thumb",
              "headline", "spark", "change", "searchText", "category"):
        assert k in pl, k
    assert pl["kind"] == "season" and pl["defaultLayer"] == "water" and pl["thumb"] == "thumbs/t1.webp"
    assert pl["centre"] == [10.5, 20.5]
    assert pl["dates"] == {"count": 2, "first": "2026-06-01", "last": "2026-06-13"}
    h = pl["headline"]
    assert h["value"] == 12.5 and h["date"] == "2026-06-13" and h["lo"] == 11 and h["hi"] == 14
    assert pl["spark"] == [10.0, 12.5]
    assert pl["change"]["delta"] == 2.46 or abs(pl["change"]["delta"] - 2.46) < 0.01
    assert "calibrated" in pl["searchText"] and "testland" in pl["searchText"]
    # stable output: a second run leaves the file identical
    first = (pub / "summary.json").read_text()
    opt.run(["t1"], pub, verbose=False)
    assert (pub / "summary.json").read_text() == first


def test_event_summary(tmp_path):
    pub, p = _build(tmp_path)
    m = json.loads((p / "manifest.json").read_text())
    m.update(kind="event", dates=[], source=None,
             event={"type": "earthquake", "magnitude": 6.8, "time": "2026-07-28T07:27:15Z", "lngLat": [10.2, 20.3],
                    "place": "Somewhere", "views": [{"id": "a", "before": "2026-07-21", "after": "2026-08-14", "p2p_cm": 38}]})
    m["layers"] = [{"id": "fringes", "label": "F", "dates": None, "legend": {"type": "ramp", "colors": ["#000", "#fff"]}}]
    Image.fromarray(_grey_img(), "RGBA").save(p / "fringes.webp", quality=80)
    (p / "manifest.json").write_text(json.dumps(m))
    opt.run(["t1"], pub, verbose=False)
    pl = json.loads((pub / "summary.json").read_text())["places"][0]
    assert pl["kind"] == "event" and pl["eventType"] == "earthquake" and pl["defaultLayer"] == "fringes"
    assert pl["headline"]["value"] == 6.8 and pl["headline"]["p2p_cm"] == 38
    assert pl["dates"]["first"] == "2026-07-21" and pl["dates"]["last"] == "2026-08-14"
