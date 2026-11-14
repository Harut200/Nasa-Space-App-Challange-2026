"""remote_products.py -- NISAR interferometric (GUNW) and soil-moisture (SME2) windows, server side.

    remote_products.py anatomy URL OUT.txt                             HDF5 tree of one remote file
    remote_products.py extract JOB.json                                 cut windows for every product in a job

Runs on the ~0.5 GB RAM team server: Python 3.9, numpy + h5py + requests only. Reuses the
authentication / HTTP-range machinery of remote_extract.py (same folder): Earthdata bearer
token from ~/.netrc via the netrc module (never printed or written), signed URL, LRU block cache.
Nothing is downloaded whole; each layer is read in row strips and only the window is kept.

JOB.json:
  {"out": "~/work2/out/ararat", "products": [{"id": "20260620_20260702", "url": "...h5",
     "kind": "GUNW"|"SME2"}...],
   "windows": [{"id": "ararat", "bbox": [w, s, e, n]}...],
   "gunw_fine": true}          # also cut the 20 m wrapped-layer coherence (block-averaged x2 -> 40 m)

Output per window and product: OUT/<window>/<kind>_<id>.npz with float32/int layers on the
product's own grid, plus x/y coordinate vectors and the EPSG code; OUT/<window>/log.json.
"""
import io, json, math, os, sys, time, traceback

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from remote_extract import (ASF, Remote, RangeFile, bbox_to_proj, get_token, log)  # noqa: E402

GUNW = "/science/LSAR/GUNW/grids/frequencyA"
SME2_ROOT = "/science/LSAR/SME2"
PAD_M = 3000


# ---------------------------------------------------------------- EASE-Grid 2.0 (EPSG:6933)
def ease2_forward(lon, lat):
    """WGS84 lon/lat -> EASE-Grid 2.0 global (EPSG:6933, cylindrical equal-area, phi_s = 30 deg)."""
    a, f = 6378137.0, 1 / 298.257223563
    e2 = f * (2 - f)
    e = math.sqrt(e2)
    ps = math.radians(30.0)
    k0 = math.cos(ps) / math.sqrt(1 - e2 * math.sin(ps) ** 2)
    phi = np.radians(np.asarray(lat, float))
    s = np.sin(phi)
    q = (1 - e2) * (s / (1 - e2 * s * s) - 1 / (2 * e) * np.log((1 - e * s) / (1 + e * s)))
    return a * k0 * np.radians(np.asarray(lon, float)), a * q / (2 * k0)


def bbox_xy(bbox, epsg):
    if epsg == 6933:
        w, s, e, n = bbox
        x, y = ease2_forward([w, e, w, e], [s, s, n, n])
        return float(min(x)), float(min(y)), float(max(x)), float(max(y))
    if epsg == 4326:
        return tuple(bbox)
    return bbox_to_proj(bbox, epsg)


# ---------------------------------------------------------------- generic helpers
def epsg_of(g):
    for name in ("projection", "epsg"):
        if name in g:
            p = g[name]
            for k in ("epsg_code", "epsg"):
                if k in p.attrs:
                    return int(np.asarray(p.attrs[k]).ravel()[0])
            try:
                return int(np.asarray(p[()]).ravel()[0])
            except Exception:
                pass
    return None


def tree(f, path):
    """Full HDF5 tree with shapes, dtypes, chunks, fill values and short attributes."""
    import h5py
    L = []

    def visit(name, o):
        if isinstance(o, h5py.Dataset):
            s = f"{name}: shape={o.shape} dtype={o.dtype} chunks={o.chunks} comp={o.compression}"
            if o.fillvalue is not None and o.dtype.kind in "fiuc":
                s += f" fill={o.fillvalue}"
            L.append(s)
            if o.size <= 6 and o.dtype.kind != "V":
                try:
                    v = o[()]
                    if isinstance(v, bytes):
                        v = v.decode(errors="replace")
                    elif isinstance(v, np.ndarray) and v.dtype.kind in "SO":
                        v = [x.decode(errors="replace") if isinstance(x, bytes) else x for x in v.ravel()]
                    L.append(f"    value={v!r}"[:300])
                except Exception as ex:
                    L.append(f"    value=<{ex.__class__.__name__}>")
        else:
            L.append(f"{name}/")
        for k, v in o.attrs.items():
            v = np.asarray(v)
            if v.dtype.kind in "SO":
                v = " ".join(x.decode(errors="replace") if isinstance(x, bytes) else str(x) for x in v.ravel())
            elif v.size > 8:
                v = f"<{v.dtype} {v.shape}>"
            else:
                v = v.ravel().tolist()
            L.append(f"    @{k} = {v}"[:400])

    f.visititems(visit)
    txt = "\n".join(L)
    with open(path, "w") as fo:
        fo.write(txt + "\n")
    return txt


def open_remote(url, token, max_blocks=48):
    rem = Remote(url, token)
    rf = RangeFile(rem.get, rem.size, block=1024 * 1024, max_blocks=max_blocks, ahead=4)
    return rem, rf, io.BufferedReader(rf, buffer_size=256 * 1024)


def window_index(xc, yc, bbox, epsg, pad=PAD_M):
    w, s, e, n = bbox_xy(bbox, epsg)
    if epsg not in (4326,):
        w, s, e, n = w - pad, s - pad, e + pad, n + pad
    ci = np.nonzero((xc >= w) & (xc <= e))[0]
    ri = np.nonzero((yc >= s) & (yc <= n))[0]
    if not len(ci) or not len(ri):
        return None
    return int(ri[0]), int(ri[-1]) + 1, int(ci[0]), int(ci[-1]) + 1


def read_strips(ds, r0, r1, c0, c1, fac=1, max_px=2 * 1024 * 1024, fn=None):
    """Read ds[r0:r1, c0:c1] in row strips; optional per-strip transform fn; block-mean by fac
    (NaN-aware) so the in-memory result stays small. Returns float32 (or the native int type)."""
    cr = (ds.chunks or (256, 256))[0]
    ncol = c1 - c0
    rows = max(cr, max_px // max(1, ncol) // cr * cr)  # whole chunk rows per strip
    rows -= rows % fac
    out = []
    r = r0
    while r < r1:
        re = min(r1, r + rows)
        a = ds[r:re, c0:c1]
        if fn is not None:
            a = fn(a)
        if fac > 1:
            h, w = (a.shape[0] // fac) * fac, (a.shape[1] // fac) * fac
            a = a[:h, :w].astype(np.float32).reshape(h // fac, fac, w // fac, fac)
            with np.errstate(invalid="ignore"):
                a = np.nanmean(np.nanmean(a, axis=3), axis=1) if np.isnan(a).any() else a.mean(axis=(1, 3))
        out.append(a)
        r = re
    return np.concatenate(out, axis=0)


# ---------------------------------------------------------------- per-product extraction
def find_pol_group(g):
    for p in ("HH", "VV", "HV", "VH"):
        if p in g:
            return p
    return None


GUNW_LAYERS = ("unwrappedPhase", "coherenceMagnitude", "connectedComponents",
               "ionospherePhaseScreen", "ionospherePhaseScreenUncertainty")


def extract_gunw(f, windows, fine=True, layers=GUNW_LAYERS, with_mask=True):
    """Return {window_id: dict of arrays} for one GUNW file."""
    res = {}
    gu = f[GUNW + "/unwrappedInterferogram"]
    pol = find_pol_group(gu)
    gp = gu[pol]
    xc, yc = gu["xCoordinates"][()], gu["yCoordinates"][()]
    epsg = epsg_of(gu) or epsg_of(f[GUNW])
    gw = f[GUNW + "/wrappedInterferogram"] if fine and "wrappedInterferogram" in f[GUNW] else None
    for win in windows:
        ix = window_index(xc, yc, win["bbox"], epsg)
        if ix is None:
            res[win["id"]] = None
            continue
        r0, r1, c0, c1 = ix
        d = {"epsg": epsg, "x": xc[c0:c1], "y": yc[r0:r1], "pol": pol}
        for name in layers:
            if name in gp:
                t = time.time()
                d[name] = read_strips(gp[name], r0, r1, c0, c1)
                log(f"   {win['id']} {name} {d[name].shape} {time.time() - t:.1f}s")
        if with_mask and "mask" in gu:
            d["mask"] = read_strips(gu["mask"], r0, r1, c0, c1)
        if gw is not None and win.get("fine", True):
            gwp = gw[find_pol_group(gw)]
            xw, yw = gw["xCoordinates"][()], gw["yCoordinates"][()]
            iw = window_index(xw, yw, win["bbox"], epsg)
            if iw is not None and "coherenceMagnitude" in gwp:
                a0, a1, b0, b1 = iw
                fac = 2
                a1 = a0 + (a1 - a0) // fac * fac
                b1 = b0 + (b1 - b0) // fac * fac
                d["coh40"] = read_strips(gwp["coherenceMagnitude"], a0, a1, b0, b1, fac=fac,
                                         fn=lambda a: np.where(np.isfinite(a), a, np.nan).astype(np.float32))
                d["x40"] = xw[b0:b1].reshape(-1, fac).mean(1)
                d["y40"] = yw[a0:a1].reshape(-1, fac).mean(1)
        res[win["id"]] = d
    return res


def _find_ds(f, names):
    import h5py
    found = {}

    def visit(name, o):
        if isinstance(o, h5py.Dataset):
            leaf = name.split("/")[-1]
            if leaf in names and leaf not in found:
                found[leaf] = name
    f.visititems(visit)
    return found


def extract_sme2(f, windows, layers):
    """SME2 (EASE-Grid 2.0, EPSG:6933, 200 m): crop each window for the listed layer paths
    (relative to /science/LSAR/SME2/grids). Output keys use '/' -> '__'."""
    grp = f[SME2_ROOT + "/grids"]
    xc, yc = grp["xCoordinates"][()], grp["yCoordinates"][()]
    epsg = epsg_of(grp) or 6933
    res = {}
    for win in windows:
        ix = window_index(xc, yc, win["bbox"], epsg)
        if ix is None:
            res[win["id"]] = None
            continue
        r0, r1, c0, c1 = ix
        d = {"epsg": epsg, "x": xc[c0:c1], "y": yc[r0:r1],
             "lat": grp["latitude"][r0:r1], "lon": grp["longitude"][c0:c1]}
        for p in layers:
            if p in grp:
                d[p.replace("/", "__")] = read_strips(grp[p], r0, r1, c0, c1)
        res[win["id"]] = d
    return res


SME2_LAYERS = ["soilMoisture", "soilMoistureUncertainty", "retrievalQualityFlag", "surfaceQualityFlag",
               "ancillaryData/landCover", "ancillaryData/waterbodyFraction",
               "algorithmCandidates/DSG/soilMoisture", "algorithmCandidates/PMI/soilMoisture",
               "algorithmCandidates/TSR/soilMoisture"]

CUBES = ["incidenceAngle", "losUnitVectorX", "losUnitVectorY", "wetTroposphericPhaseScreen",
         "hydrostaticTroposphericPhaseScreen", "slantRangeSolidEarthTidesPhase", "perpendicularBaseline"]


GEOM_CUBES = {"incidenceAngle", "losUnitVectorX", "losUnitVectorY", "perpendicularBaseline"}


def extract_cubes(f, windows, hmin=0.0, hmax=4000.0, geometry=True):
    """Small 3-D geometry/correction cubes (heights x ~500 m grid) cropped to each window, only
    the height levels in [hmin, hmax] (each level is a separate compressed chunk on the network)."""
    g = f["/science/LSAR/GUNW/metadata/radarGrid"]
    xc, yc = g["xCoordinates"][()], g["yCoordinates"][()]
    hh = g["heightAboveEllipsoid"][()]
    lev = np.nonzero((hh >= hmin) & (hh <= hmax))[0]
    l0, l1 = int(lev[0]), int(lev[-1]) + 1
    epsg = epsg_of(g)
    out = {}
    for win in windows:
        ix = window_index(xc, yc, win["bbox"], epsg, pad=6000)
        if ix is None:
            continue
        r0, r1, c0, c1 = ix
        d = {"cube_x": xc[c0:c1], "cube_y": yc[r0:r1], "cube_h": hh[l0:l1]}
        for n in CUBES:
            if n in g and (geometry or n not in GEOM_CUBES):
                d["cube_" + n] = g[n][l0:l1, r0:r1, c0:c1].astype(np.float32)
        out[win["id"]] = d
    return out


def mem_available_mb():
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024
    except OSError:
        pass
    return 1e9


def other_job_running():
    import subprocess
    r = subprocess.run(["pgrep", "-f", "remote_extract.py"], capture_output=True, text=True)
    return bool(r.stdout.strip())


def wait_for_gap(min_mb=200, poll_s=60):
    """Be polite to the other agent's GCOV extraction (it has priority): start a product only when
    its job is not running, or when MemAvailable is above min_mb."""
    waited = 0
    while other_job_running() and mem_available_mb() < min_mb:
        if waited % 600 == 0:
            log(f"waiting for a gap (other job running, MemAvailable {mem_available_mb():.0f} MB)")
        time.sleep(poll_s)
        waited += poll_s


def run_job(job):
    import h5py
    out = os.path.expanduser(job["out"])
    os.makedirs(out, exist_ok=True)
    token = get_token()
    log("Earthdata token obtained")
    for p in job["products"]:
        kind, pid = p["kind"], p["id"]
        targets = [os.path.join(out, w["id"], f"{kind}_{pid}.npz") for w in job["windows"]]
        if all(os.path.exists(t) or os.path.exists(t + ".none") for t in targets):
            log(f"{kind} {pid}: done already")
            continue
        wait_for_gap(job.get("min_mb", 200))
        t0 = time.time()
        try:
            rem, rf, fobj = open_remote(p["url"], token, job.get("cache_mb", 48))
            with h5py.File(fobj, "r", rdcc_nbytes=2 * 1024 * 1024) as f:
                meta = {}
                if kind == "GUNW":
                    res = extract_gunw(f, job["windows"], job.get("gunw_fine", True),
                                       tuple(job.get("layers", GUNW_LAYERS)), job.get("mask", True))
                    log(f"   80 m layers done {time.time() - t0:.0f}s, {rf.bytes_fetched / 1e6:.1f} MB")
                    first = p is job["products"][0]
                    cubes = extract_cubes(f, job["windows"], job.get("hmin", 0.0), job.get("hmax", 4000.0),
                                          geometry=first) if job.get("cubes", True) else {}
                    for wid, c in cubes.items():
                        if res.get(wid) is not None:
                            res[wid].update(c)
                    meta["centerFrequency"] = float(f[GUNW + "/centerFrequency"][()])
                    meta["temporalBaseline"] = float(f["/science/LSAR/GUNW/metadata/orbit/temporalBaseline"][()])
                else:
                    res = extract_sme2(f, job["windows"], job.get("sme2_layers", SME2_LAYERS))
            for w in job["windows"]:
                d = res.get(w["id"])
                wd = os.path.join(out, w["id"])
                os.makedirs(wd, exist_ok=True)
                tp = os.path.join(wd, f"{kind}_{pid}.npz")
                if d is None:
                    open(tp + ".none", "w").close()
                    continue
                d = dict(d)
                d.update({f"meta_{k}": v for k, v in meta.items()})
                d["url"] = p["url"]
                np.savez_compressed(tp, **{k: np.asarray(v) for k, v in d.items()})
                os.makedirs(os.path.join(wd, "small2"), exist_ok=True)
                a, b = reduce_npz2(tp, os.path.join(wd, "small2", f"{kind}_{pid}.npz"), w["id"],
                                   REDUCE_FAC.get(w["id"], 1) if kind == "GUNW" else 1)
                log(f"   {w['id']}: {a / 1e6:.1f} MB -> small {b / 1e6:.1f} MB")
            log(f"{kind} {pid}: OK {time.time() - t0:.0f}s, {rf.bytes_fetched / 1e6:.1f} MB in {rf.requests} requests")
        except Exception:
            log(f"{kind} {pid}: FAILED\n{traceback.format_exc()}")
            if "download check failed" in traceback.format_exc():
                break


COH_KEYS = ("coherenceMagnitude", "coh40")
F16_KEYS = ("unwrappedPhase", "ionospherePhaseScreen", "ionospherePhaseScreenUncertainty")


def reduce_npz(src, dst):
    """Shrink one window for the slow server->laptop link (keys unchanged, dtypes reduced):
    coherence -> uint8 q = round(250 c), 255 = no data (step 0.004);
    phase-like layers -> float16 after removing their median (stored as <key>__offset; the
    unwrapped phase is only defined up to a constant anyway); step <= 0.016 rad for |phi| < 32
    rad, i.e. < 0.4 mm of line-of-sight motion at L-band; connected components / masks -> uint8/16.
    Everything else (coordinates, small cubes, metadata, SME2 at 200 m) is kept as float32."""
    z = np.load(src, allow_pickle=False)
    d = {}
    for k in z.files:
        a = z[k]
        if k in COH_KEYS:
            q = np.where(np.isfinite(a), np.round(np.clip(a, 0, 1) * 250), 255).astype(np.uint8)
            d[k] = q
            d[k + "__scale"] = np.asarray(250.0)
        elif k in F16_KEYS or (a.dtype == np.float32 and a.ndim == 2 and a.size > 20000 and k != "lat"):
            off = float(np.nanmedian(a)) if np.isfinite(a).any() else 0.0
            d[k] = (a - off).astype(np.float16)
            d[k + "__offset"] = np.asarray(off)
        elif k in ("connectedComponents", "mask") and a.dtype.kind in "iu":
            d[k] = a.astype(np.uint8 if a.max(initial=0) < 256 else np.uint16)
        elif a.dtype == np.float64 and a.ndim >= 1 and a.size > 1000:
            d[k] = a.astype(np.float32)
        else:
            d[k] = a
    np.savez_compressed(dst, **d)
    return os.path.getsize(src), os.path.getsize(dst)


ZOOM40 = {"ararat": (44.28, 39.88, 44.68, 40.14)}  # 40 m coherence kept only over this part of the valley floor


def _bmean(a, f):
    h, w = (a.shape[-2] // f) * f, (a.shape[-1] // f) * f
    b = a[..., :h, :w].astype(np.float32).reshape(a.shape[:-2] + (h // f, f, w // f, f))
    with np.errstate(invalid="ignore"), __import__("warnings").catch_warnings():
        __import__("warnings").simplefilter("ignore")
        return np.nanmean(np.nanmean(b, axis=-1), axis=-2)


def reduce_npz2(src, dst, window, fac=1, gmin_keep=0.2, phase_step=0.02):
    """Second, much smaller transfer format (the server->laptop link runs at ~50 KB/s):
      coherence (80 m, or fac*80 m)          uint8, q = round(250 c), 255 = no data
      unwrappedPhase                          int16 steps of `phase_step` rad (0.02 rad = 0.39 mm LOS) after
                                              removing the median; NaN (-32768) where coherence < gmin_keep or
                                              connected component 0 (unusable either way)
      ionosphere screen + its uncertainty     block mean x5 (400 m; the screen is smooth by construction)
      tropo / solid-earth-tide cubes          every 2nd node horizontally (~1 km), float16 + offset
      coh40 (20 m layer averaged to 40 m)     cropped to ZOOM40[window], uint8
      fac > 1 (control area): phase/coherence block-averaged by fac, components/mask subsampled.
    Keys keep their names; <key>__scale / __offset / __step / __fac tell the reader how to decode."""
    z = np.load(src, allow_pickle=False)
    d = {}
    coh = z["coherenceMagnitude"] if "coherenceMagnitude" in z.files else None
    cc = z["connectedComponents"] if "connectedComponents" in z.files else None

    def q_coh(a):
        return np.where(np.isfinite(a), np.round(np.clip(a, 0, 1) * 250), 255).astype(np.uint8)

    for k in z.files:
        a = z[k]
        if k == "unwrappedPhase":
            a = a.astype(np.float32).copy()
            if coh is not None:
                a[~(coh >= gmin_keep)] = np.nan
            if cc is not None:
                a[(cc == 0) | (cc == 65535)] = np.nan
            if fac > 1:
                a = _bmean(a, fac)
            off = float(np.nanmedian(a)) if np.isfinite(a).any() else 0.0
            d[k] = np.where(np.isfinite(a), np.round((a - off) / phase_step), -32768).astype(np.int16)
            d[k + "__offset"], d[k + "__step"] = np.asarray(off), np.asarray(phase_step)
        elif k == "coherenceMagnitude":
            d[k] = q_coh(_bmean(a, fac) if fac > 1 else a)
            d[k + "__scale"] = np.asarray(250.0)
        elif k in ("ionospherePhaseScreen", "ionospherePhaseScreenUncertainty"):
            d[k] = _bmean(a, 5).astype(np.float32)  # reader interpolates back onto the phase grid
            d[k + "__fac"] = np.asarray(5)
        elif k in ("connectedComponents", "mask"):
            a = a[::fac, ::fac] if fac > 1 else a
            d[k] = a.astype(np.uint8 if a.max(initial=0) < 256 else np.uint16)
        elif k == "coh40":
            if window not in ZOOM40:
                continue
            w, s, e, n = bbox_xy(ZOOM40[window], int(z["epsg"]))
            xs, ys = z["x40"], z["y40"]
            ci = np.nonzero((xs >= w) & (xs <= e))[0]
            ri = np.nonzero((ys >= s) & (ys <= n))[0]
            d[k] = q_coh(a[ri[0]:ri[-1] + 1, ci[0]:ci[-1] + 1])
            d[k + "__scale"] = np.asarray(250.0)
            d["x40"], d["y40"] = xs[ci[0]:ci[-1] + 1], ys[ri[0]:ri[-1] + 1]
        elif k in ("x40", "y40"):
            continue
        elif k in ("x", "y") and fac > 1:
            m = (len(a) // fac) * fac
            d[k] = a[:m].reshape(-1, fac).mean(1)
        elif k.startswith("cube_") and a.ndim == 3:
            a = a[:, ::2, ::2]
            if k[5:] in GEOM_CUBES:
                d[k] = a.astype(np.float32)
            else:
                off = float(np.nanmedian(a))
                d[k] = (a - off).astype(np.float16)
                d[k + "__offset"] = np.asarray(off)
        elif k in ("cube_x", "cube_y"):
            d[k] = a[::2]
        elif a.dtype.kind == "f" and a.ndim == 2 and a.size > 20000:  # SME2 layers at 200 m
            off = float(np.nanmedian(a)) if np.isfinite(a).any() else 0.0
            d[k] = (a - off).astype(np.float16)
            d[k + "__offset"] = np.asarray(off)
        else:
            d[k] = a
    np.savez_compressed(dst, **d)
    return os.path.getsize(src), os.path.getsize(dst)


REDUCE_FAC = {"sevan": 2, "tonle-sap": 2}  # control area and the big Tonle Sap window: 160 m


def reduce_dir(out, sub="small2"):
    """Write OUT/<window>/small2/<file>.npz for every finished window not yet reduced."""
    out = os.path.expanduser(out)
    for wd in sorted(os.listdir(out)):
        p = os.path.join(out, wd)
        if not os.path.isdir(p):
            continue
        sd = os.path.join(p, sub)
        os.makedirs(sd, exist_ok=True)
        for fn in sorted(os.listdir(p)):
            if fn.endswith(".npz") and not os.path.exists(os.path.join(sd, fn)):
                a, b = reduce_npz2(os.path.join(p, fn), os.path.join(sd, fn), wd,
                                   REDUCE_FAC.get(wd, 1) if fn.startswith("GUNW") else 1)
                log(f"reduced {wd}/{fn}: {a / 1e6:.1f} -> {b / 1e6:.2f} MB")


def main(argv):
    import h5py
    cmd = argv[1]
    if cmd == "reduce":  # reduce OUT... : (re)write the small2 transfer copies
        for o in argv[2:]:
            reduce_dir(o)
        return 0
    if cmd == "anatomy":
        url, path = argv[2], argv[3]
        token = get_token()
        rem, rf, fobj = open_remote(url, token)
        t0 = time.time()
        with h5py.File(fobj, "r") as f:
            tree(f, path)
        log(f"anatomy -> {path} in {time.time() - t0:.0f}s, {rf.bytes_fetched / 1e6:.1f} MB")
    elif cmd == "extract":
        for jp in argv[2:]:
            log(f"job {jp}")
            run_job(json.load(open(jp)))
    elif cmd == "values":  # print small arrays/attrs for listed dataset paths (anatomy follow-up)
        url = argv[2]
        token = get_token()
        rem, rf, fobj = open_remote(url, token)
        with h5py.File(fobj, "r") as f:
            for p in argv[3:]:
                if p not in f:
                    print(p, "MISSING")
                    continue
                o = f[p]
                v = o[()] if o.size <= 64 else f"<{o.shape}> first={np.asarray(o[tuple(slice(0, 3) for _ in o.shape)]).ravel()[:6]}"
                print(p, "=", v, dict(o.attrs))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
