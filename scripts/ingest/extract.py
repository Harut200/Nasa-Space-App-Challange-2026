"""extract.py {area} | --selftest : NISAR GCOV -> contract A GeoTIFFs (plan B5 + B9)."""
import json, math, sys, tempfile, time, traceback
from pathlib import Path

import h5py, numpy as np, rasterio
from rasterio.transform import Affine
from rasterio.warp import Resampling, reproject, transform_bounds

REPO = Path(__file__).resolve().parents[2]
GROUP = "/science/LSAR/GCOV/grids/frequencyA"
POLS = {"hh": "HHHH", "hv": "HVHV"}
PAD_M = 2000
# ponytail: UNVERIFIED against a real file (check at B4): `mask` semantics (unused here),
# no-data convention in HHHH (assumed NaN and/or <= 0), yCoordinates order (both handled),
# spacing datasets xCoordinateSpacing/yCoordinateSpacing (assumed present; y may be negative),
# `projection` as an int EPSG scalar, HHHH/HVHV being real float linear power (not complex/dB).


def open_h5(url):
    if not url.startswith(("http://", "https://")):  # local path: test hook / whole-file fallback
        return h5py.File(url, "r")
    import earthaccess
    earthaccess.login()  # reads ~/.netrc
    fs = earthaccess.get_fsspec_https_session()
    return h5py.File(fs.open(url, mode="rb", block_size=8 * 1024 * 1024, cache_type="blockcache"), "r")


def read_window(f, bbox):
    """Return {pol: float32 array}, source Affine, EPSG for the padded bbox window."""
    g = f[GROUP]
    epsg = int(np.asarray(g["projection"][()]).item())
    x, y = g["xCoordinates"][:], g["yCoordinates"][:]
    sx = abs(float(np.asarray(g["xCoordinateSpacing"][()]).item()))
    sy = abs(float(np.asarray(g["yCoordinateSpacing"][()]).item()))
    w, s, e, n = transform_bounds("EPSG:4326", f"EPSG:{epsg}", *bbox, densify_pts=21)
    w, s, e, n = w - PAD_M, s - PAD_M, e + PAD_M, n + PAD_M
    cols = np.where((x >= w) & (x <= e))[0]
    rows = np.where((y >= s) & (y <= n))[0]
    if not len(cols) or not len(rows):
        raise ValueError("bbox is outside this granule")
    c0, c1, r0, r1 = cols[0], cols[-1] + 1, rows.min(), rows.max() + 1
    flip = y[r0] < y[r1 - 1]  # ascending y: read south-first, flip so row 0 is north
    top = (y[r1 - 1] if flip else y[r0]) + sy / 2
    tf = Affine(sx, 0, x[c0] - sx / 2, 0, -sy, top)
    out = {}
    for pol, name in POLS.items():
        if name not in g:
            print(f"  {pol}: {name} missing in file, skipped")
            continue
        t = time.time()
        a = g[name][r0:r1, c0:c1].astype("float32")
        print(f"  {pol}: read {a.shape} in {time.time() - t:.1f}s")
        a[~np.isfinite(a) | (a <= 0)] = np.nan
        out[pol] = a[::-1] if flip else a
    return out, tf, epsg


def make_grid(bbox, epsg, p):
    w, s, e, n = transform_bounds("EPSG:4326", f"EPSG:{epsg}", *bbox, densify_pts=21)
    w, s = math.floor((w - PAD_M) / p) * p, math.floor((s - PAD_M) / p) * p
    e, n = math.ceil((e + PAD_M) / p) * p, math.ceil((n + PAD_M) / p) * p
    return {"crs": f"EPSG:{epsg}", "transform": [p, 0, w, 0, -p, n, 0, 0, 1],
            "width": round((e - w) / p), "height": round((n - s) / p)}


def run(area, root):
    cfg = json.loads((root / "areas" / area / "area.json").read_text())
    passes = json.loads((root / "data/raw" / area / "passes.json").read_text())
    proc = root / "data/processed" / area
    gridf, failed = proc / "grid.json", 0
    for p in passes:
        d = p["date"]
        paths = {pol: proc / "gamma0" / pol / f"{d}.tif" for pol in POLS}
        if all(v.exists() for v in paths.values()):
            print(f"{d}: exists, skipped")
            continue
        try:
            t = time.time()
            with open_h5(p["url"]) as f:
                arrs, src_tf, epsg = read_window(f, cfg["bbox"])
            read_s = time.time() - t
            if not gridf.exists():
                proc.mkdir(parents=True, exist_ok=True)
                gridf.write_text(json.dumps(make_grid(cfg["bbox"], epsg, cfg["pixel_m"])))
            grid = json.loads(gridf.read_text())
            dst_tf = Affine(*grid["transform"][:6])
            shares = []
            for pol, a in arrs.items():
                dst = np.full((grid["height"], grid["width"]), np.nan, "float32")
                reproject(a, dst, src_transform=src_tf, src_crs=f"EPSG:{epsg}", src_nodata=np.nan,
                          dst_transform=dst_tf, dst_crs=grid["crs"], dst_nodata=np.nan,
                          resampling=Resampling.average)
                paths[pol].parent.mkdir(parents=True, exist_ok=True)
                with rasterio.open(paths[pol], "w", driver="GTiff", count=1, dtype="float32",
                                   width=grid["width"], height=grid["height"], crs=grid["crs"],
                                   transform=dst_tf, nodata=np.nan, compress="deflate") as o:
                    o.write(dst, 1)
                v = float(np.isfinite(dst).mean())
                shares.append(f"{pol}={v:.0%}" + ("  WARNING: under 80% valid" if v < 0.8 else ""))
            print(f"{d}: read {read_s:.1f}s, valid {' '.join(shares) or 'no pols'}")
            if not arrs:
                raise ValueError("no polarisations found")
        except Exception:
            failed += 1
            print(f"{d}: FAILED\n{traceback.format_exc()}")
    return failed


def selftest():
    p = 40
    cx, cy = 500400.0, 4982900.0  # bright square centre, UTM 31N
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "areas/t").mkdir(parents=True)
        (root / "data/raw/t").mkdir(parents=True)
        (root / "areas/t/area.json").write_text(json.dumps(
            {"id": "t", "bbox": [2.99, 44.99, 3.01, 45.01], "pixel_m": p}))
        xs = 495010 + 20.0 * np.arange(500)   # pixel centres, 10 km box
        ys = 4987990 - 20.0 * np.arange(500)  # descending
        X, Y = np.meshgrid(xs, ys)
        passes = []
        for i, d in enumerate(["2026-07-02", "2026-07-14"]):
            a = np.full(X.shape, 0.1, "float32")
            a[(abs(X - cx) <= 100) & (abs(Y - cy) <= 100)] = 5.0
            a[(abs(X - 499000) <= 200) & (abs(Y - 4984000) <= 200)] = 0.0   # zero patch
            a[(abs(X - 499000) <= 100) & (abs(Y - 4984000) <= 100)] = -1.0  # negative core
            fp = root / f"{d}.h5"
            with h5py.File(fp, "w") as f:
                g = f.create_group(GROUP)
                g["HHHH"] = a
                if i == 0:
                    g["HVHV"] = a * 0.1  # date 2 has no HV: exercises the skip
                g["xCoordinates"], g["yCoordinates"] = xs, ys
                g["xCoordinateSpacing"], g["yCoordinateSpacing"] = 20.0, -20.0
                g["projection"] = np.uint32(32631)
            passes.append({"date": d, "url": str(fp), "granule": fp.name})
        (root / "data/raw/t/passes.json").write_text(json.dumps(passes))
        assert run("t", root) == 0
        proc = root / "data/processed/t"
        grid = json.loads((proc / "grid.json").read_text())
        gt = Affine(*grid["transform"][:6])
        assert abs(gt.a - p) < 1e-9 and abs(gt.e + p) < 1e-9, "pixel size != pixel_m"
        assert gt.c % p == 0 and gt.f % p == 0, "grid not snapped"
        for d in ["2026-07-02", "2026-07-14"]:
            with rasterio.open(proc / f"gamma0/hh/{d}.tif") as r:
                assert (r.width, r.height, r.crs.to_string(), tuple(r.transform)[:6]) == (
                    grid["width"], grid["height"], grid["crs"], tuple(gt)[:6]), "grid != grid.json"
                assert r.count == 1 and r.dtypes[0] == "float32"
                a = r.read(1)
            assert np.isnan(a[r.index(499000, 4984000)]), "zero patch not NaN"
            m = a > 1
            assert m.any()
            rr, cc = np.nonzero(m)
            mx, my = gt * (cc.mean() + 0.5, rr.mean() + 0.5)
            assert abs(mx - cx) <= p and abs(my - cy) <= p, f"square off: {mx - cx}, {my - cy}"
        assert (proc / "gamma0/hv/2026-07-02.tif").exists() and not (proc / "gamma0/hv/2026-07-14.tif").exists()
        # resume: rerun must skip date 1 (both pols) and not fail
        assert run("t", root) == 0
    print("selftest OK")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
    elif len(sys.argv) == 2:
        sys.exit(1 if run(sys.argv[1], REPO) else 0)
    else:
        sys.exit("usage: extract.py {area} | --selftest")
