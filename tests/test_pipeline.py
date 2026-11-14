"""End to end on synthetic NISAR-shaped files: extract -> (stand-in detect) -> export.
Run: .venv/bin/python tests/test_pipeline.py          offline, temp dir only
     .venv/bin/python tests/test_pipeline.py --net    also runs the real ASF search for tonle-sap
Not covered: reading a real NISAR file (needs Earthdata login) and scripts/detect/run.py (detection branch).
"""
import json, math, subprocess, sys, tempfile
from pathlib import Path
import h5py, numpy as np, rasterio
from PIL import Image
from rasterio.transform import from_origin
from rasterio.warp import transform, transform_bounds

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/ingest"))
import extract, export as ex

BBOX = [104.0, 12.9, 104.2, 13.1]
LON0, LAT0 = 104.12, 13.03            # disc centre
STRIP_LON = BBOX[0] + 0.01            # source is zero west of this: must become NaN, then alpha 0
PIXEL = 40
# (date, EPSG of the file, source spacing m, y ascending?, disc radius m)
PASSES = [("2026-07-13", 32648, 20, False, 2000),
          ("2026-07-25", 32648, 25, True, 3000),    # other spacing + south-first rows
          ("2026-08-18", 32647, 20, False, 4000)]   # neighbouring UTM zone
DATES = [p[0] for p in PASSES]
R = 6378137.0
merc_x = lambda lon: math.radians(lon) * R
merc_y = lambda lat: R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def make_h5(path, epsg, sp, ascending, radius):
    w, s, e, n = transform_bounds("EPSG:4326", f"EPSG:{epsg}", *BBOX, densify_pts=21)
    xs = np.arange(math.floor(w) - 3000, e + 3000, sp) + sp / 2    # pixel centres
    ys = np.arange(math.ceil(n) + 3000, s - 3000, -sp) - sp / 2    # north first
    X, Y = np.meshgrid(xs, ys)
    lon, lat = (np.array(v).reshape(X.shape) for v in
                transform(f"EPSG:{epsg}", "EPSG:4326", X.ravel(), Y.ravel()))
    dist = np.hypot((lon - LON0) * math.cos(math.radians(LAT0)) * 111320, (lat - LAT0) * 110574)
    hh = np.where(dist < radius, 0.005, 0.1).astype("float32")
    hh[lon < STRIP_LON] = 0
    if ascending:
        ys, hh = ys[::-1], hh[::-1]
    with h5py.File(path, "w") as f:
        g = f.create_group(extract.GROUP)
        g["HHHH"], g["HVHV"] = hh, hh * 0.3
        g["xCoordinates"], g["yCoordinates"] = xs, ys
        g["xCoordinateSpacing"], g["yCoordinateSpacing"] = float(sp), float(sp if ascending else -sp)
        g["projection"] = np.uint32(epsg)


def stand_in_detect(proc):
    """Grey colouring so export has contract B. Replaced by scripts/detect/run.py."""
    prod = proc / "products"
    for pol, lo in (("hh", -25), ("hv", -30)):
        for d in DATES:
            with rasterio.open(proc / "gamma0" / pol / f"{d}.tif") as r:
                a, prof = r.read(1), r.profile
            v = np.nan_to_num((10 * np.log10(a) - lo) / 20 * 255).clip(0, 255).astype("uint8")
            rgba = np.stack([v, v, v, np.where(np.isfinite(a), 255, 0).astype("uint8")])
            prof.update(count=4, dtype="uint8", nodata=None)
            targets = [prod / pol / f"{d}.tif"]
            if pol == "hh" and d != DATES[0]:
                targets.append(prod / "change" / f"{d}.tif")   # change layer skips the first date
            if pol == "hh" and d == DATES[-1]:
                targets.append(prod / "rgb.tif")               # undated layer
            for t in targets:
                t.parent.mkdir(parents=True, exist_ok=True)
                with rasterio.open(t, "w", **prof) as o:
                    o.write(rgba)
    L = lambda i, dated: {"id": i, "label": i.upper(), "dated": dated, "legend": {"type": "text", "text": i}}
    (prod / "layers.json").write_text(json.dumps([L("hh", True), L("hv", True), L("change", True), L("rgb", False)]))
    (prod / "timeseries.json").write_text(json.dumps(
        {"dates": DATES, "series": [{"spot": "disc", "hh": [-23, -23, None], "hv": [-28, -28, None]}]}))


def webp_px(m, img, lon, lat):
    w, s, e, n = m["bounds"]
    return ((merc_x(lon) - merc_x(w)) / (merc_x(e) - merc_x(w)) * img.shape[1],
            (merc_y(n) - merc_y(lat)) / (merc_y(n) - merc_y(s)) * img.shape[0])


def centroid(mask):
    rr, cc = np.nonzero(mask)
    assert len(rr), "feature not found"
    return cc.mean() + 0.5, rr.mean() + 0.5


def test_pipeline():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for d in ("areas/t", "data/raw/t", "src/app/public/data"):
            (root / d).mkdir(parents=True)
        (root / "areas/t/area.json").write_text(json.dumps(
            {"id": "t", "name": "Test", "bbox": BBOX, "track": 1, "frame": 1, "direction": "ascending", "pixel_m": PIXEL}))
        (root / "areas/t/story.json").write_text(json.dumps(
            {"story": {"see": "a", "physics": "b", "lband": "c", "people": "d"},
             "spots": [{"id": "disc", "label": "Disc", "lngLat": [LON0, LAT0], "radius_m": 1000}]}))
        (root / "src/app/public/data/index.json").write_text(json.dumps({"areas": ["other"]}))
        passes = []
        for d, epsg, sp, asc, rad in PASSES:
            make_h5(root / f"{d}.h5", epsg, sp, asc, rad)
            passes.append({"date": d, "url": str(root / f"{d}.h5"), "granule": d})
        (root / "data/raw/t/passes.json").write_text(json.dumps(passes))

        # ---- extract: contract A
        assert extract.run("t", root) == 0
        proc = root / "data/processed/t"
        grids = set()
        for pol in ("hh", "hv"):
            for d in DATES:
                with rasterio.open(proc / "gamma0" / pol / f"{d}.tif") as r:
                    assert r.count == 1 and r.dtypes[0] == "float32" and r.crs.to_epsg() == 32648
                    assert r.compression.value.lower() == "deflate"
                    assert abs(r.transform.a) == PIXEL == abs(r.transform.e)
                    grids.add((r.width, r.height, tuple(r.transform)[:6]))
                    a = r.read(1)
                    x0, y0 = (v[0] for v in transform("EPSG:4326", r.crs, [LON0], [LAT0]))
                    xs, ys = (v[0] for v in transform("EPSG:4326", r.crs, [BBOX[0] + 0.004], [13.0]))
                    assert np.isnan(a[r.index(xs, ys)]), "zeros must be NaN"
                    assert not (a[np.isfinite(a)] <= 0).any(), "linear power must be > 0"
                    cx, cy = centroid(a < (0.03 if pol == "hh" else 0.009))
                    mx, my = r.transform * (cx, cy)
                    assert abs(mx - x0) <= PIXEL and abs(my - y0) <= PIXEL, (pol, d, mx - x0, my - y0)
        assert len(grids) == 1, "every file of one area must share one grid"
        assert 400 < max(next(iter(grids))[:2]) < 900

        # ---- detect stand-in: contract B, then export: contract C
        stand_in_detect(proc)
        ex.export("t", root)
        pub = root / "src/app/public/data"
        m = json.loads((pub / "t/manifest.json").read_text())
        assert set(m) == {"id", "name", "bounds", "dates", "gaps", "layers", "story", "spots", "credits", "category"}
        assert m["dates"] == DATES and m["gaps"] == [["2026-07-27", "2026-08-10"]]
        by = {L["id"]: L for L in m["layers"]}
        assert [L["id"] for L in m["layers"]] == ["hh", "hv", "change", "rgb"], "display order kept"
        assert by["hh"]["dates"] == DATES and by["change"]["dates"] == DATES[1:] and by["rgb"]["dates"] is None
        assert by["rgb"]["legend"] == {"type": "text", "text": "rgb"}, "legend copied unchanged"
        assert m["spots"] == [{"id": "disc", "label": "Disc", "lngLat": [LON0, LAT0]}]
        assert m["story"]["lband"] == "c"
        assert json.loads((pub / "t/timeseries.json").read_text())["series"][0]["hh"][2] is None
        assert json.loads((pub / "index.json").read_text())["areas"] == ["other", "t"]
        w, s, e, n = m["bounds"]
        assert w < e and s < n and abs(w - BBOX[0]) < 0.05 and abs(n - BBOX[3]) < 0.05

        files = [pub / "t" / L / f"{d}.webp" for L in ("hh", "hv") for d in DATES]
        files += [pub / "t/change" / f"{d}.webp" for d in DATES[1:]] + [pub / "t/rgb.webp"]
        assert not (pub / "t/change" / f"{DATES[0]}.webp").exists()
        sizes = set()
        for f in files:
            img = np.asarray(Image.open(f).convert("RGBA")).astype(int)
            sizes.add(img.shape)
            px, py = webp_px(m, img, LON0, LAT0)
            cx, cy = centroid((img[..., 3] > 0) & (img[..., 0] < 100))
            assert abs(cx - px) <= 1 and abs(cy - py) <= 1, (f.name, cx - px, cy - py)
            sx, sy = webp_px(m, img, BBOX[0] + 0.004, 13.0)
            assert img[int(sy), int(sx), 3] == 0, "no-data must be transparent"
            assert img[int(py), int(px), 3] == 255
        assert len(sizes) == 1, "all images of one area share one size"
        assert sum(f.stat().st_size for f in files) < 15e6

        # ---- export: 3,000 px cap, no gap outside the range
        prod = root / "data/processed/big/products"
        (prod / "hh").mkdir(parents=True)
        (root / "areas/big").mkdir()
        (root / "areas/big/area.json").write_text(json.dumps({"id": "big", "name": "Big"}))
        a = np.full((4, 2500, 4000), 200, "uint8")
        a[:3, 1000:1100, 3000:3100] = 0
        tf = from_origin(400000, 1500000, 30, 30)
        with rasterio.open(prod / "hh/2026-09-01.tif", "w", driver="GTiff", width=4000, height=2500, count=4,
                           dtype="uint8", crs="EPSG:32648", transform=tf) as o:
            o.write(a)
        (prod / "layers.json").write_text(json.dumps([{"id": "hh", "label": "HH", "dated": True, "legend": {}}]))
        (prod / "timeseries.json").write_text(json.dumps({"dates": ["2026-09-01"], "series": []}))
        ex.export("big", root)
        mb = json.loads((pub / "big/manifest.json").read_text())
        img = np.asarray(Image.open(pub / "big/hh/2026-09-01.webp").convert("RGBA")).astype(int)
        assert 2990 <= max(img.shape[:2]) <= 3000, img.shape
        assert mb["gaps"] == [] and mb["spots"] == [] and mb["story"]["see"] == ""
        lon, lat = (v[0] for v in transform("EPSG:32648", "EPSG:4326", *[[v] for v in tf * (3050, 1050)]))
        px, py = webp_px(mb, img, lon, lat)
        cx, cy = centroid((img[..., 3] > 0) & (img[..., 0] < 100))
        assert abs(cx - px) <= 1 and abs(cy - py) <= 1, (cx - px, cy - py)
        assert json.loads((pub / "index.json").read_text())["areas"] == ["other", "t", "big"]
    print("pipeline ok")


def test_search_live():
    """Real ASF query, no login needed. Rewrites data/raw/tonle-sap/passes.json (not in git)."""
    subprocess.run([sys.executable, str(ROOT / "scripts/ingest/search.py"), "tonle-sap"], check=True)
    p = json.loads((ROOT / "data/raw/tonle-sap/passes.json").read_text())
    dates = [x["date"] for x in p]
    assert len(p) >= 6 and dates == sorted(set(dates)), dates
    assert all(x["url"].startswith("https://") and x["url"].endswith(".h5") and "_083_A_008_" in x["granule"] for x in p)
    print(f"search ok: {len(p)} passes {dates[0]} .. {dates[-1]}")


if __name__ == "__main__":
    test_pipeline()
    if "--net" in sys.argv:
        test_search_live()
