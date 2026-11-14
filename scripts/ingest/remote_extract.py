"""remote_extract.py CONFIG [--dates D,D] [--out DIR] [--download] [--search-only]
       remote_extract.py --to-geotiff SRC_DIR DST_DIR          (local side, needs rasterio)

Calibrated NISAR GCOV -> block-averaged gamma-naught on a fixed per-area grid.
Written to run on a ~0.5 GB RAM server: Python 3.9, numpy + h5py + requests only.

CONFIG is either an area.json (id, bbox, pixel_m, track, frame, direction) or a job file
{"id", "track", "frame", "direction", "windows": [{"id", "bbox", "pixel_m"}, ...]}; every
window is cut from the same file, so one 7.5 GB Armenia file is opened once for two areas.

Per pass: search ASF (no login) -> resolve the download URL with an Earthdata bearer token
(credentials read from ~/.netrc through the netrc module, never printed) -> open the remote
HDF5 through HTTP range requests with a small block cache (no full download) -> read each
window in chunk-aligned blocks -> sum and count valid linear-power samples per target cell.

Output per window: OUT/<window>/grid.json, OUT/<window>/<date>.npz (hh, hv float32 mean
linear power, NaN no-data; n_hh, n_hv uint16 valid-sample counts), OUT/<window>/summary.json.
OUT/passes.json, OUT/b4_report.txt (file layout of the first file opened).
The --to-geotiff mode turns one window folder into contract A GeoTIFFs on the local machine.
"""
import argparse, io, json, math, netrc, os, sys, time, traceback
from collections import OrderedDict

import numpy as np

GROUP = "/science/LSAR/GCOV/grids/frequencyA"
IDENT = "/science/LSAR/identification"
POLS = {"hh": "HHHH", "hv": "HVHV"}
PAD_M = 2000
ASF = "https://api.daac.asf.alaska.edu/services/search/param"
URS = "urs.earthdata.nasa.gov"
HDF5_SIG = b"\x89HDF\r\n\x1a\n"
BLOCK_PX = 4 * 1024 * 1024  # max source pixels per read block (16 MB as float32)
MIN_VALID_FRAC = 0.25       # a target cell needs this share of its nominal samples
MASK_INVALID = (0, 255)     # mask values treated as no-data (checked against B4, see docs)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---------------------------------------------------------------- projection (UTM, no pyproj)
def utm_forward(lon, lat, epsg):
    """WGS84 lon/lat (degrees) -> UTM easting/northing (m) for EPSG 326zz / 327zz.
    Krueger series to n^3: sub-millimetre inside a zone."""
    zone, south = epsg % 100, epsg // 100 == 327
    if epsg // 100 not in (326, 327) or not 1 <= zone <= 60:
        raise ValueError(f"EPSG {epsg} is not WGS84 UTM; extend utm_forward or pass a projected bbox")
    a, f, k0 = 6378137.0, 1 / 298.257223563, 0.9996
    n = f / (2 - f)
    A = a / (1 + n) * (1 + n ** 2 / 4 + n ** 4 / 64)
    al = (n / 2 - 2 * n ** 2 / 3 + 5 * n ** 3 / 16, 13 * n ** 2 / 48 - 3 * n ** 3 / 5, 61 * n ** 3 / 240)
    phi = np.radians(np.asarray(lat, float))
    lam = np.radians(np.asarray(lon, float) - (zone * 6 - 183))
    c = 2 * math.sqrt(n) / (1 + n)
    t = np.sinh(np.arctanh(np.sin(phi)) - c * np.arctanh(c * np.sin(phi)))
    xi = np.arctan2(t, np.cos(lam))
    eta = np.arctanh(np.sin(lam) / np.sqrt(1 + t * t))
    E = eta + sum(al[j] * np.cos(2 * (j + 1) * xi) * np.sinh(2 * (j + 1) * eta) for j in range(3))
    N = xi + sum(al[j] * np.sin(2 * (j + 1) * xi) * np.cosh(2 * (j + 1) * eta) for j in range(3))
    return 500000 + k0 * A * E, (10000000.0 if south else 0.0) + k0 * A * N


def bbox_to_proj(bbox, epsg, densify=21):
    w, s, e, n = bbox
    t = np.linspace(0, 1, densify)
    lon = np.concatenate([w + (e - w) * t, w + (e - w) * t, np.full(densify, w), np.full(densify, e)])
    lat = np.concatenate([np.full(densify, s), np.full(densify, n), s + (n - s) * t, s + (n - s) * t])
    x, y = utm_forward(lon, lat, epsg)
    return float(x.min()), float(y.min()), float(x.max()), float(y.max())


# ---------------------------------------------------------------- grid + block averaging
def make_grid(bbox, epsg, p, x_edge, y_edge):
    """One target grid per window. Cells are p metres, aligned to the SOURCE pixel-edge
    lattice (x_edge, y_edge = any source pixel edge) so a cell is an exact block of source
    pixels when p is a multiple of the source spacing."""
    w, s, e, n = bbox_to_proj(bbox, epsg)
    w, s, e, n = w - PAD_M, s - PAD_M, e + PAD_M, n + PAD_M
    x0 = x_edge + math.floor((w - x_edge) / p) * p
    y0 = y_edge + math.ceil((n - y_edge) / p) * p
    width, height = math.ceil((e - x0) / p), math.ceil((y0 - s) / p)
    return {"crs": f"EPSG:{epsg}", "epsg": epsg, "pixel_m": p,
            "transform": [p, 0, x0, 0, -p, y0, 0, 0, 1], "width": width, "height": height,
            "note": "transform = upper-left corner of the upper-left cell (GDAL convention)"}


def _groups(t):
    """Start indices and values of runs of equal target index in a monotonic 1-D array."""
    st = np.r_[0, np.nonzero(np.diff(t))[0] + 1]
    return st, t[st]


class Binner:
    """Accumulates sums and counts of valid samples per target cell for one window."""

    def __init__(self, grid, xc, yc):
        p, x0, y0 = grid["pixel_m"], grid["transform"][2], grid["transform"][5]
        self.H, self.W = grid["height"], grid["width"]
        tcol = np.floor((np.asarray(xc, float) - x0) / p).astype(np.int64)
        trow = np.floor((y0 - np.asarray(yc, float)) / p).astype(np.int64)
        assert np.all(np.diff(tcol) >= 0), "xCoordinates must increase"
        assert np.all(np.diff(trow) >= 0) or np.all(np.diff(trow) <= 0), "yCoordinates not monotonic"
        cols = np.nonzero((tcol >= 0) & (tcol < self.W))[0]
        rows = np.nonzero((trow >= 0) & (trow < self.H))[0]
        if not len(cols) or not len(rows):
            raise ValueError("window is outside this granule")
        self.c0, self.c1, self.r0, self.r1 = int(cols[0]), int(cols[-1]) + 1, int(rows[0]), int(rows[-1]) + 1
        self.tcol, self.trow = tcol, trow
        sx = abs(float(xc[1] - xc[0])) if len(xc) > 1 else p
        self.nominal = max(1, round(p / sx) ** 2)
        self.sum = {k: np.zeros((self.H, self.W), np.float32) for k in POLS}
        self.cnt = {k: np.zeros((self.H, self.W), np.uint16) for k in POLS}

    def add(self, pol, a, ok, rs, cs):
        """a: float32 block of source rows rs.., cols cs..; ok: bool validity of same shape."""
        a = np.where(ok, a, np.float32(0))
        cst, gcol = _groups(self.tcol[cs:cs + a.shape[1]])
        rst, grow = _groups(self.trow[rs:rs + a.shape[0]])
        s = np.add.reduceat(np.add.reduceat(a, cst, axis=1), rst, axis=0)
        n = np.add.reduceat(np.add.reduceat(ok.astype(np.uint16), cst, axis=1), rst, axis=0)
        cm = (gcol >= 0) & (gcol < self.W)
        rm = (grow >= 0) & (grow < self.H)
        gc, gr = gcol[cm], grow[rm]
        if not len(gc) or not len(gr):
            return
        ix = np.ix_(gr, gc)
        self.sum[pol][ix] += s[np.ix_(rm, cm)]
        self.cnt[pol][ix] += n[np.ix_(rm, cm)]

    def result(self, pol):
        n = self.cnt[pol]
        need = max(1, int(math.ceil(MIN_VALID_FRAC * self.nominal)))
        with np.errstate(invalid="ignore", divide="ignore"):
            m = self.sum[pol] / np.maximum(n, 1)
        m[n < need] = np.nan
        return m.astype(np.float32), n


def blocks(shape_rows, shape_cols, chunks, r0, r1, c0, c1, max_px=BLOCK_PX):
    """Chunk-aligned read blocks covering rows r0:r1, cols c0:c1 with <= max_px pixels each."""
    cr, cc = chunks or (256, 256)
    ncols = c1 - c0
    if cr * ncols <= max_px:  # full-width strips of whole chunk rows
        rows_per, cols_per = max(1, max_px // ncols // cr) * cr, None
    else:                     # one chunk row high, chunk-aligned column blocks
        rows_per, cols_per = cr, max(1, max_px // cr // cc) * cc
    r = r0
    while r < r1:
        re = min(r1, (r // cr) * cr + rows_per)
        c = c0
        while c < c1:
            ce = c1 if cols_per is None else min(c1, (c // cc) * cc + cols_per)
            yield r, re, c, ce
            c = ce
        r = re


def extract_file(f, windows, out, date, mask_invalid=MASK_INVALID, max_px=BLOCK_PX):
    """Read every window of one open h5py.File; write <window>/<date>.npz. Returns stats."""
    g = f[GROUP]
    epsg = read_epsg(g)
    xc, yc = g["xCoordinates"][()], g["yCoordinates"][()]
    sx, sy = float(xc[1] - xc[0]), float(yc[1] - yc[0])
    pols = {k: v for k, v in POLS.items() if v in g}
    mk = g["mask"] if "mask" in g else None
    stats = {}
    for win in windows:
        wd = os.path.join(out, win["id"])
        os.makedirs(wd, exist_ok=True)
        gp = os.path.join(wd, "grid.json")
        if os.path.exists(gp):
            grid = json.load(open(gp))
            if grid["epsg"] != epsg:
                raise ValueError(f"{win['id']}: file EPSG {epsg} != grid EPSG {grid['epsg']}")
        else:
            grid = make_grid(win["bbox"], epsg, win["pixel_m"], xc[0] - sx / 2, yc[0] - sy / 2)
            grid.update(source_spacing_m=[abs(sx), abs(sy)], bbox=win["bbox"])
            json.dump(grid, open(gp, "w"), indent=1)
        b = Binner(grid, xc, yc)
        t = time.time()
        ds0 = g[pols.get("hh", next(iter(pols.values())))]
        for rs, re, cs, ce in blocks(ds0.shape[0], ds0.shape[1], ds0.chunks, b.r0, b.r1, b.c0, b.c1, max_px):
            m = mk[rs:re, cs:ce] if (mk is not None and mask_invalid) else None
            bad = np.isin(m, mask_invalid) if m is not None else None
            for pol, name in pols.items():
                a = g[name][rs:re, cs:ce].astype(np.float32, copy=False)
                ok = np.isfinite(a) & (a > 0)
                if bad is not None:
                    ok &= ~bad
                b.add(pol, a, ok, rs, cs)
                del a, ok
        res = {}
        for pol in pols:
            res[pol], res["n_" + pol] = b.result(pol)
        np.savez_compressed(os.path.join(wd, f"{date}.npz"), **res)
        st = {"read_s": round(time.time() - t, 1), "src_window": [b.r0, b.r1, b.c0, b.c1],
              "pols": list(pols)}
        for pol in pols:
            v = res[pol][np.isfinite(res[pol])]
            st[pol] = {"valid_frac": round(float(v.size / res[pol].size), 4)}
            if v.size:
                q = 10 * np.log10(np.percentile(v, [0, 1, 50, 99, 100]))
                st[pol].update({k: round(float(x), 2) for k, x in zip(["min_db", "p1_db", "median_db", "p99_db", "max_db"], q)})
            st[pol]["median_count"] = int(np.median(res["n_" + pol][res["n_" + pol] > 0])) if (res["n_" + pol] > 0).any() else 0
        stats[win["id"]] = st
        del b, res
    return stats


def read_epsg(g):
    p = g["projection"]
    for k in ("epsg_code", "epsg"):
        if k in p.attrs:
            return int(np.asarray(p.attrs[k]).ravel()[0])
    return int(np.asarray(p[()]).ravel()[0])


# ---------------------------------------------------------------- B4 report
def _attrs(o):
    out = {}
    for k, v in o.attrs.items():
        v = np.asarray(v)
        if v.dtype.kind in "SO":
            v = [x.decode() if isinstance(x, bytes) else str(x) for x in v.ravel()]
            v = v[0] if len(v) == 1 else v
        elif v.size <= 8:
            v = v.ravel().tolist()
            v = v[0] if len(v) == 1 else v
        else:
            v = f"<{v.dtype} {v.shape}>"
        out[k] = v
    return out


def b4_report(f, path):
    import h5py
    L = []
    for top in (IDENT, GROUP):
        L.append(f"== {top}")
        def visit(name, o, top=top):
            if isinstance(o, h5py.Dataset):
                info = f"{top}/{name}: shape={o.shape} dtype={o.dtype} chunks={o.chunks} compression={o.compression}"
                if o.compression_opts is not None:
                    info += f"({o.compression_opts})"
                if o.shuffle:
                    info += " shuffle"
                if o.fillvalue is not None and o.dtype.kind in "fiu":
                    info += f" fillvalue={o.fillvalue}"
                L.append(info)
                if o.size <= 16:
                    v = o[()]
                    if isinstance(v, bytes):
                        v = v.decode(errors="replace")
                    elif isinstance(v, np.ndarray) and v.dtype.kind in "SO":
                        v = [x.decode(errors="replace") if isinstance(x, bytes) else x for x in v.ravel()]
                    L.append(f"    value = {v!r}"[:400])
                a = _attrs(o)
                if a:
                    L.append(f"    attrs = {a!r}"[:1500])
        f[top].visititems(visit)
    g = f[GROUP]
    xc, yc = g["xCoordinates"][()], g["yCoordinates"][()]
    L.append(f"== geometry: EPSG={read_epsg(g)} x[0]={xc[0]} x[-1]={xc[-1]} dx={xc[1]-xc[0]} "
             f"y[0]={yc[0]} y[-1]={yc[-1]} dy={yc[1]-yc[0]} -> y {'DESCENDING (north first)' if yc[1] < yc[0] else 'ascending'}")
    L.append(f"   x[0] mod |dx| = {xc[0] % abs(xc[1]-xc[0])}, y[0] mod |dy| = {yc[0] % abs(yc[1]-yc[0])} (0 -> centres on the spacing lattice)")
    ny, nx = len(yc), len(xc)
    for label, (r, c) in {"centre": (ny // 2, nx // 2), "near-edge": (ny // 2, 300)}.items():
        sl = (slice(max(0, r - 512), r + 512), slice(max(0, c - 512), c + 512))
        L.append(f"== sample window {label} rows {sl[0].start}:{sl[0].stop} cols {sl[1].start}:{sl[1].stop}")
        m = g["mask"][sl] if "mask" in g else None
        if m is not None:
            u, n = np.unique(m, return_counts=True)
            L.append(f"   mask values: {dict(zip(u.tolist(), n.tolist()))}")
        for name in ("HHHH", "HVHV", "numberOfLooks", "rtcGammaToSigmaFactor"):
            if name not in g:
                continue
            a = g[name][sl].astype(np.float64)
            fin = np.isfinite(a)
            line = f"   {name}: nan={int((~fin).sum())} zero={int((a == 0).sum())} neg={int((a < 0).sum())}"
            if fin.any():
                v = a[fin]
                line += f" min={v.min():.4g} median={np.median(v):.4g} max={v.max():.4g}"
                if name in ("HHHH", "HVHV") and (v > 0).any():
                    line += f" median_dB={10*np.log10(np.median(v[v > 0])):.2f}"
            L.append(line)
            if m is not None and name == "HHHH":
                for mv in np.unique(m).tolist()[:12]:
                    sel = (m == mv)
                    vv = a[sel & fin & (a > 0)]
                    L.append(f"     mask={mv}: n={int(sel.sum())} finite>0={vv.size}"
                             + (f" median_dB={10*np.log10(np.median(vv)):.2f}" if vv.size else ""))
    txt = "\n".join(L)
    with open(path, "w") as fo:
        fo.write(txt + "\n")
    return txt


# ---------------------------------------------------------------- search + auth + remote file
def search(cfg):
    import requests
    bb = np.array([w["bbox"] for w in cfg["windows"]])
    w, s, e, n = bb[:, 0].min(), bb[:, 1].min(), bb[:, 2].max(), bb[:, 3].max()
    q = {"dataset": "NISAR", "processingLevel": "GCOV", "dataMaturity": "PROVISIONAL",
         "relativeOrbit": cfg["track"], "frame": cfg["frame"], "flightDirection": cfg["direction"].upper(),
         "intersectsWith": f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))",
         "start": "2026-06-17T00:00:00Z", "end": "2026-10-24T00:00:00Z", "output": "jsonlite"}
    r = requests.get(ASF, params=q, timeout=120)
    r.raise_for_status()
    best = {}
    for x in r.json()["results"]:
        nz = x.get("nisar") or {}
        fn = x["fileName"]
        if "_PR_" not in fn or nz.get("frameCoverage") != "Full":
            continue
        if not {"HH", "HV"} <= set(nz.get("mainBandPolarization") or []):
            continue
        d = x["startTime"][:10]
        key = (nz.get("crid") or "", fn[-6:-3])  # processing version, then product counter
        size = ((nz.get("sizeMB") or {}).get(fn) or {}).get("bytes")
        rec = {"date": d, "url": x["downloadUrl"], "granule": x["granuleName"], "crid": nz.get("crid"),
               "bandwidth": nz.get("rangeBandwidth"), "bytes": size, "startTime": x["startTime"]}
        if d not in best or key > best[d][0]:
            best[d] = (key, rec)
    return [best[d][1] for d in sorted(best)]


def _creds():
    a = netrc.netrc(os.path.expanduser("~/.netrc")).authenticators(URS)
    if not a:
        raise RuntimeError(f"~/.netrc has no entry for {URS}")
    return a[0], a[2]


def get_token():
    """Short-lived Earthdata bearer token; never printed or written to disk."""
    import requests
    u, p = _creds()
    r = requests.post(f"https://{URS}/api/users/find_or_create_token", auth=(u, p), timeout=60)
    if r.status_code == 401:
        raise RuntimeError("Earthdata rejected the netrc login (401): check user/password in ~/.netrc")
    r.raise_for_status()
    return r.json()["access_token"]


class Remote:
    """Resolves the ASF URL to a signed direct URL; re-resolves when it expires."""

    def __init__(self, url, token):
        import requests
        self.url, self.token, self.rq = url, token, requests
        self.plain = requests.Session()
        self.plain.trust_env = False  # no netrc auth on the storage host
        self.resolve()

    def resolve(self):
        s = self.rq.Session()  # trust_env: netrc basic auth is re-applied on the URS host if the
        # bearer is refused and the cookie/OAuth redirect flow is used instead
        hdr = {"Range": "bytes=0-7"}
        if self.token:
            hdr["Authorization"] = f"Bearer {self.token}"
        r = s.get(self.url, headers=hdr, allow_redirects=True, timeout=120, stream=True)
        body = r.raw.read(4096, decode_content=True) if r.status_code < 500 else b""
        ctype = r.headers.get("Content-Type", "")
        if r.status_code not in (200, 206) or "html" in ctype or not body.startswith(HDF5_SIG):
            snippet = body[:300].decode(errors="replace").replace("\n", " ")
            hint = ("the response is an HTML page: the data-use agreement (EULA) is probably not accepted yet -- "
                    "log in once at https://search.asf.alaska.edu and download any NISAR file, then rerun"
                    if "html" in ctype or b"<html" in body[:300].lower() else "unexpected response")
            raise RuntimeError(f"download check failed: HTTP {r.status_code} {ctype}; {hint}; host={r.url.split('/')[2]}; body: {snippet[:200]}")
        self.direct = r.url
        self.session = s if r.url.split("/")[2] == self.url.split("/")[2] else self.plain
        cr = r.headers.get("Content-Range", "")
        self.size = int(cr.split("/")[-1]) if "/" in cr else int(r.headers.get("Content-Length", 0))
        r.close()
        self.t_resolved = time.time()

    def get(self, start, end):
        for attempt in range(6):
            try:
                hdr = {"Range": f"bytes={start}-{end - 1}"}
                if self.session is not self.plain and self.token:
                    hdr["Authorization"] = f"Bearer {self.token}"
                r = self.session.get(self.direct, headers=hdr, timeout=120)
                if r.status_code in (401, 403):
                    self.resolve()
                    continue
                r.raise_for_status()
                if len(r.content) != end - start:
                    raise IOError(f"short read {len(r.content)} != {end - start}")
                return r.content
            except Exception as e:
                if attempt == 5:
                    raise
                log(f"  range {start}-{end} failed ({e.__class__.__name__}: {str(e)[:120]}), retry {attempt + 1}")
                time.sleep(2 * (attempt + 1))
                if time.time() - self.t_resolved > 1800:
                    self.resolve()


class RangeFile(io.RawIOBase):
    """Read-only file object over a fetch(start, end)->bytes function, LRU block cache.
    A miss right after a cached block reads ahead `ahead` blocks (sequential chunk reads)."""

    def __init__(self, fetch, size, block=2 * 1024 * 1024, max_blocks=32, ahead=4):
        self.fetch, self.size, self.block, self.max_blocks, self.ahead = fetch, size, block, max_blocks, ahead
        self.pos, self.cache, self.bytes_fetched, self.requests = 0, OrderedDict(), 0, 0

    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.pos

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else self.pos + off if whence == 1 else self.size + off
        return self.pos

    def _load(self, b):
        n = self.ahead if (b - 1) in self.cache else 1
        last = min(b + n, (self.size + self.block - 1) // self.block)
        while last > b + 1 and (last - 1) in self.cache:
            last -= 1
        s, e = b * self.block, min(self.size, last * self.block)
        data = self.fetch(s, e)
        self.bytes_fetched += len(data)
        self.requests += 1
        for i in range(b, last):
            self.cache[i] = data[(i - b) * self.block:(i - b + 1) * self.block]
        while len(self.cache) > self.max_blocks:
            self.cache.popitem(last=False)

    def readinto(self, buf):
        mv = memoryview(buf).cast("B")
        n = min(len(mv), max(0, self.size - self.pos))
        done = 0
        while done < n:
            b, o = divmod(self.pos + done, self.block)
            if b not in self.cache:
                self._load(b)
            self.cache.move_to_end(b)
            chunk = self.cache[b][o:o + n - done]
            mv[done:done + len(chunk)] = chunk
            done += len(chunk)
        self.pos += n
        return n


def download(rem, path):
    """Whole-file fallback with resume; streams to disk in 8 MB pieces."""
    have = os.path.getsize(path) if os.path.exists(path) else 0
    step = 8 * 1024 * 1024
    with open(path, "ab") as fo:
        while have < rem.size:
            data = rem.get(have, min(rem.size, have + step))
            fo.write(data)
            have += len(data)


# ---------------------------------------------------------------- main
def load_cfg(path):
    c = json.load(open(path))
    if "windows" not in c:
        c = {"id": c["id"], "track": c["track"], "frame": c["frame"], "direction": c["direction"],
             "windows": [{"id": c["id"], "bbox": c["bbox"], "pixel_m": c["pixel_m"]}]}
    return c


def run(cfg, out, dates=None, use_download=False, search_only=False, open_local=None):
    import h5py
    os.makedirs(out, exist_ok=True)
    pp = os.path.join(out, "passes.json")
    passes = search(cfg) if open_local is None else open_local["passes"]
    json.dump(passes, open(pp, "w"), indent=1)
    log(f"{len(passes)} passes -> {pp}: {[p['date'] for p in passes]}")
    if search_only:
        return 0
    if dates:
        passes = [p for p in passes if p["date"] in dates]
    token = None
    if open_local is None:
        if not os.path.exists(os.path.expanduser("~/.netrc")):
            raise SystemExit("NO_NETRC: create ~/.netrc (machine urs.earthdata.nasa.gov login ... password ...), chmod 600")
        try:
            token = get_token()
            log("Earthdata token obtained")
        except Exception as e:
            log(f"token route failed ({str(e)[:200]}); falling back to netrc redirect login")
    failed = 0
    for p in passes:
        d = p["date"]
        if all(os.path.exists(os.path.join(out, w["id"], f"{d}.npz")) for w in cfg["windows"]):
            log(f"{d}: done already, skipped")
            continue
        t0, local = time.time(), None
        try:
            if open_local is not None:
                fobj, rf = open_local["files"][d], None
            else:
                rem = Remote(p["url"], token)
                log(f"{d}: {p['granule']} ({rem.size / 1e9:.2f} GB), signed URL resolved")
                if use_download:
                    local = os.path.join(out, "tmp_" + p["granule"] + ".h5")
                    download(rem, local)
                    log(f"{d}: downloaded in {time.time() - t0:.0f}s")
                    fobj, rf = local, None
                else:
                    rf = RangeFile(rem.get, rem.size)
                    fobj = io.BufferedReader(rf, buffer_size=256 * 1024)
            with h5py.File(fobj, "r", rdcc_nbytes=8 * 1024 * 1024) as f:
                b4 = os.path.join(out, "b4_report.txt")
                if not os.path.exists(b4):
                    tb = time.time()
                    print(b4_report(f, b4), flush=True)
                    log(f"B4 report -> {b4} ({time.time() - tb:.0f}s)")
                st = extract_file(f, cfg["windows"], out, d)
            st_all = {"date": d, "granule": p.get("granule"), "total_s": round(time.time() - t0, 1),
                      "MB_fetched": round(rf.bytes_fetched / 1e6, 1) if rf else None,
                      "requests": rf.requests if rf else None, "windows": st}
            for w in cfg["windows"]:
                sp = os.path.join(out, w["id"], "summary.json")
                sm = json.load(open(sp)) if os.path.exists(sp) else {"dates": {}}
                sm["dates"][d] = dict(st_all, windows=None, **st[w["id"]])
                sm["grid"] = json.load(open(os.path.join(out, w["id"], "grid.json")))
                json.dump(sm, open(sp, "w"), indent=1)
            log(f"{d}: OK {json.dumps(st_all)}")
        except Exception:
            failed += 1
            log(f"{d}: FAILED\n{traceback.format_exc()}")
            if "download check failed" in traceback.format_exc():
                break
        finally:
            if local and os.path.exists(local):
                os.remove(local)
    log(f"finished: {failed} failed")
    return failed


def to_geotiff(src, dst):
    """Local side: <window> folder of npz -> contract A GeoTIFFs (+ nlooks)."""
    import glob
    import rasterio
    from rasterio.transform import Affine
    grid = json.load(open(os.path.join(src, "grid.json")))
    tf = Affine(*grid["transform"][:6])
    prof = dict(driver="GTiff", width=grid["width"], height=grid["height"], count=1, crs=grid["crs"],
                transform=tf, compress="deflate", tiled=True, blockxsize=256, blockysize=256)
    n = 0
    for fp in sorted(glob.glob(os.path.join(src, "*.npz"))):
        d = os.path.basename(fp)[:-4]
        z = np.load(fp)
        for pol in POLS:
            if pol not in z:
                continue
            o = os.path.join(dst, "gamma0", pol, f"{d}.tif")
            os.makedirs(os.path.dirname(o), exist_ok=True)
            with rasterio.open(o, "w", dtype="float32", nodata=float("nan"), predictor=3, **prof) as r:
                r.write(z[pol].astype(np.float32), 1)
            o = os.path.join(dst, "nlooks", pol, f"{d}.tif")
            os.makedirs(os.path.dirname(o), exist_ok=True)
            with rasterio.open(o, "w", dtype="uint16", predictor=2, **prof) as r:
                r.write(z["n_" + pol], 1)
        n += 1
    for name in ("grid.json", "summary.json"):
        if os.path.exists(os.path.join(src, name)):
            with open(os.path.join(src, name)) as fi, open(os.path.join(dst, name), "w") as fo:
                fo.write(fi.read())
    return n


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("config", nargs="?")
    ap.add_argument("--dates", default="")
    ap.add_argument("--out", default=None)
    ap.add_argument("--download", action="store_true", help="download whole files instead of range reads")
    ap.add_argument("--search-only", action="store_true")
    ap.add_argument("--to-geotiff", nargs=2, metavar=("SRC", "DST"))
    a = ap.parse_args(argv)
    if a.to_geotiff:
        print(f"{to_geotiff(*a.to_geotiff)} dates written to {a.to_geotiff[1]}")
        return 0
    cfg = load_cfg(a.config)
    out = a.out or os.path.join(os.path.expanduser("~/work/out"), cfg["id"])
    return 1 if run(cfg, out, [d for d in a.dates.split(",") if d], a.download, a.search_only) else 0


if __name__ == "__main__":
    sys.exit(main())
