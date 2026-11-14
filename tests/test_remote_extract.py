"""Checks scripts/ingest/remote_extract.py on a synthetic GCOV-like HDF5 (no network).

Run: .venv/bin/python tests/test_remote_extract.py
"""
import io, json, sys, tempfile
from pathlib import Path

import h5py
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "ingest"))
import remote_extract as rx  # noqa: E402

EPSG, S, P = 32648, 20.0, 80  # UTM 48N, 20 m source, 80 m target (4x4 blocks)


def test_utm_matches_rasterio():
    from rasterio.warp import transform
    lon, lat = np.array([103.4, 104.3, 105.2, 99.1]), np.array([11.8, 12.7, 13.6, 0.5])
    ex, ey = transform("EPSG:4326", f"EPSG:{EPSG}", lon, lat)
    x, y = rx.utm_forward(lon, lat, EPSG)
    assert np.abs(x - ex).max() < 0.01 and np.abs(y - ey).max() < 0.01, (x - ex, y - ey)
    ex, ey = transform("EPSG:4326", "EPSG:32738", [45.0], [-20.0])  # southern hemisphere
    x, y = rx.utm_forward([45.0], [-20.0], 32738)
    assert abs(x[0] - ex[0]) < 0.01 and abs(y[0] - ey[0]) < 0.01


def make_file(path, rng, n=1200, chunks=(128, 128)):
    """n x n source grid at 20 m; pixel centres on odd multiples of 10 m (edges on the 20 m lattice)."""
    xs = 450010.0 + S * np.arange(n)
    ys = 1420010.0 + S * (n - 1) - S * np.arange(n)  # descending (north first)
    hh = (10 ** (rng.normal(-12, 3, (n, n)) / 10)).astype("float32")
    hv = (hh * 0.1).astype("float32")
    mask = np.ones((n, n), "uint8")
    hh[400:440, 400:460] = np.nan   # no-data block
    hh[300, 300:310] = 0.0          # zeros
    hh[301, 300:310] = -1.0         # negatives
    mask[500:540, 500:540] = 0      # invalid by mask
    mask[600:604, 600:602] = 255    # fill, half a 4x4 block
    bright = (777, 333)             # one bright source pixel (row, col)
    hh[bright] = 1000.0
    with h5py.File(path, "w") as f:
        g = f.create_group(rx.GROUP)
        for k, v in (("HHHH", hh), ("HVHV", hv), ("mask", mask)):
            g.create_dataset(k, data=v, chunks=chunks, compression="gzip")
        g["xCoordinates"], g["yCoordinates"] = xs, ys
        g["xCoordinateSpacing"], g["yCoordinateSpacing"] = S, -S
        g["projection"] = np.uint32(EPSG)
        g["projection"].attrs["epsg_code"] = np.int32(EPSG)
        g["numberOfLooks"] = np.full((n, n), 9.0, "float32")
        i = f.create_group(rx.IDENT)
        i["listOfFrequencies"] = np.array([b"A"])
    return xs, ys, hh, hv, mask, bright


def window_bbox(xs, ys, margin=4000):
    """A lon/lat box comfortably inside the synthetic file (PAD_M is added by make_grid)."""
    from rasterio.warp import transform
    x0, x1 = xs[0] + margin + rx.PAD_M, xs[-1] - margin - rx.PAD_M
    y0, y1 = ys[-1] + margin + rx.PAD_M, ys[0] - margin - rx.PAD_M
    lon, lat = transform(f"EPSG:{EPSG}", "EPSG:4326", [x0, x1, x0, x1], [y0, y0, y1, y1])
    return [max(lon[0], lon[2]), max(lat[0], lat[1]), min(lon[1], lon[3]), min(lat[2], lat[3])]


def reference(arr, mask, xs, ys, grid):
    """Direct numpy: assign every source pixel to the cell containing its centre, average valid ones."""
    p, x0, y0 = grid["pixel_m"], grid["transform"][2], grid["transform"][5]
    c = np.floor((xs - x0) / p).astype(int)
    r = np.floor((y0 - ys) / p).astype(int)
    R, C = np.meshgrid(r, c, indexing="ij")
    ok = np.isfinite(arr) & (arr > 0) & ~np.isin(mask, rx.MASK_INVALID)
    ok &= (R >= 0) & (R < grid["height"]) & (C >= 0) & (C < grid["width"])
    idx = R[ok] * grid["width"] + C[ok]
    s = np.bincount(idx, arr[ok].astype(np.float64), grid["height"] * grid["width"])
    n = np.bincount(idx, None, grid["height"] * grid["width"])
    m = np.where(n >= 4, s / np.maximum(n, 1), np.nan)  # 25% of 16 nominal samples
    return m.reshape(grid["height"], grid["width"]), n.reshape(grid["height"], grid["width"])


def run_case(fobj, out, bbox, max_px):
    with h5py.File(fobj, "r") as f:
        return rx.extract_file(f, [{"id": "w", "bbox": bbox, "pixel_m": P}], out, "2026-06-19", max_px=max_px)


def test_extraction():
    rng = np.random.default_rng(0)
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        fp = td / "g.h5"
        xs, ys, hh, hv, mask, bright = make_file(fp, rng)
        bbox = window_bbox(xs, ys)
        # 1) strips: small blocks (many column blocks) vs one huge block
        st = run_case(str(fp), str(td / "a"), bbox, max_px=128 * 300)
        run_case(str(fp), str(td / "b"), bbox, max_px=10 ** 9)
        za, zb = np.load(td / "a/w/2026-06-19.npz"), np.load(td / "b/w/2026-06-19.npz")
        for k in ("hh", "hv", "n_hh", "n_hv"):
            assert np.allclose(za[k], zb[k], rtol=1e-6, equal_nan=True), f"strip vs whole differ: {k}"
        grid = json.loads((td / "a/w/grid.json").read_text())
        # grid aligned to source edges, so every interior cell is an exact 4x4 block
        assert grid["pixel_m"] == P and (grid["transform"][2] - (xs[0] - S / 2)) % P == 0
        assert (grid["transform"][5] - (ys[0] + S / 2)) % P == 0
        # 2) block average equals a direct numpy computation, in linear power
        for pol, arr in (("hh", hh), ("hv", hv)):
            ref, n = reference(arr, mask, xs, ys, grid)
            assert np.allclose(za[pol], ref, rtol=1e-5, equal_nan=True), pol
            assert (za["n_" + pol] == n).all()
            assert np.nanmax(np.abs(za[pol] - ref) / ref) < 1e-5
        assert za["n_hh"].max() == 16
        # 3) bright point lands in the right cell (half-pixel check)
        bx, by = xs[bright[1]], ys[bright[0]]
        col = int((bx - grid["transform"][2]) // P)
        row = int((grid["transform"][5] - by) // P)
        assert np.unravel_index(np.nanargmax(za["hh"]), za["hh"].shape) == (row, col)
        # 4) masked / invalid samples ignored
        def cell(r, c):  # target cell of source pixel (r, c)
            return int((grid["transform"][5] - ys[r]) // P), int((xs[c] - grid["transform"][2]) // P)
        assert np.isnan(za["hh"][cell(520, 520)]) and za["n_hh"][cell(520, 520)] == 0  # mask 0
        assert np.isnan(za["hh"][cell(420, 430)])                                       # NaN block
        rc = cell(600, 600)
        assert za["n_hh"][rc] == 8 and np.isfinite(za["hh"][rc])  # half the 4x4 block masked 255
        assert za["n_hv"][cell(300, 305)] == 16 and za["n_hh"][cell(300, 305)] < 16  # zeros/negatives HH only
        assert st["w"]["hh"]["valid_frac"] > 0.9
        # 5) the same file read through the HTTP-range file object (block cache) gives the same bytes
        raw = fp.read_bytes()
        calls = []
        def fetch(s, e):
            calls.append((s, e))
            return raw[s:e]
        rf = rx.RangeFile(fetch, len(raw), block=64 * 1024, max_blocks=8)
        run_case(io.BufferedReader(rf, 64 * 1024), str(td / "c"), bbox, max_px=128 * 300)
        zc = np.load(td / "c/w/2026-06-19.npz")
        assert np.array_equal(np.nan_to_num(zc["hh"], nan=-1), np.nan_to_num(za["hh"], nan=-1))
        assert rf.bytes_fetched >= 0 and len(calls) == rf.requests
        # 6) local conversion to contract A GeoTIFF keeps the grid
        import rasterio
        n = rx.to_geotiff(str(td / "a/w"), str(td / "A"))
        assert n == 1
        with rasterio.open(td / "A/gamma0/hh/2026-06-19.tif") as r:
            assert r.crs.to_epsg() == EPSG and r.dtypes[0] == "float32" and r.count == 1
            assert tuple(r.transform)[:6] == tuple(grid["transform"][:6])
            a = r.read(1)
            assert np.array_equal(np.isnan(a), np.isnan(za["hh"]))
            assert r.index(bx, by) == (row, col)


def test_b4_report():
    rng = np.random.default_rng(1)
    with tempfile.TemporaryDirectory() as td:
        fp = Path(td) / "g.h5"
        make_file(fp, rng, n=900)
        with h5py.File(fp, "r") as f:
            txt = rx.b4_report(f, str(Path(td) / "b4.txt"))
        assert "DESCENDING" in txt and "mask values" in txt and "HHHH" in txt and "EPSG=32648" in txt


def test_blocks_cover_exactly():
    seen = np.zeros((1000, 3000), int)
    for rs, re, cs, ce in rx.blocks(1000, 3000, (128, 128), 37, 991, 5, 2999, max_px=128 * 1000):
        assert (re - rs) * (ce - cs) <= 128 * 1000 + 128 * 128 * 2
        seen[rs:re, cs:ce] += 1
    assert (seen[37:991, 5:2999] == 1).all() and seen.sum() == (991 - 37) * (2999 - 5)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(name, "OK")
    print("all OK")
