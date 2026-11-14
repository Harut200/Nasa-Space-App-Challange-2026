"""quake.py -- did NISAR (L2 GUNW) capture the ground displacement of an earthquake? One config per event.

    export SERVER=user@host SSH_KEY=~/.ssh/key              # only for jobs/launch/fetch (never stored in the repo)
    .venv/bin/python -m scripts.detect.quake list                       # events in docs/eda/quake/events.json
    .venv/bin/python -m scripts.detect.quake jobs  EVENT [VIEW]         # write server job files (scratch dir)
    .venv/bin/python -m scripts.detect.quake launch EVENT [VIEW]        # copy job to server and start it (nohup)
    .venv/bin/python -m scripts.detect.quake fetch EVENT [VIEW]         # copy the reduced server windows
    .venv/bin/python -m scripts.detect.quake run   EVENT                # numbers + figures (docs/eda/quake/<EVENT>/)
    .venv/bin/python -m scripts.detect.quake export EVENT               # app layers + area (clear events only)
    .venv/bin/python -m scripts.detect.quake overview                   # docs/eda/quake/overview.png
    .venv/bin/python -m pytest -q tests/test_quake.py

An event is a dict in docs/eda/quake/events.json: USGS facts, window, views (look directions: co-seismic and
control GUNW granule each), analysis settings, verdict. Only pure numpy maths here (no torch).
"""
import glob
import json
import math
import os
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOCS = os.path.join(ROOT, "docs", "eda", "quake")
EVENTS_JSON = os.path.join(DOCS, "events.json")
C_LIGHT = 299792458.0


# ============================================================ core maths (unit-tested)
def phase_to_los_cm(phi, wavelength):
    """Unwrapped phase [rad] -> LOS displacement [cm], positive toward the satellite (range decrease).
    Same convention as scripts/detect/insar.py (ISCE3 interferogram = reference x conj(secondary));
    the ASF GUNW guide does not state it, so the sign is UNCONFIRMED."""
    return -wavelength / (4 * math.pi) * np.asarray(phi, dtype=np.float64) * 100.0


def rewrap(los_cm, interval_cm):
    """Re-wrap a displacement map into fringes: returns values in [0, 1) = position in one colour cycle
    of `interval_cm` of LOS displacement."""
    return np.mod(np.asarray(los_cm, dtype=np.float64) / interval_cm, 1.0)


def _design(xs, ys, order):
    cols = [np.ones_like(xs), xs, ys]
    if order >= 2:
        cols += [xs * xs, xs * ys, ys * ys]
    return np.stack(cols, -1)


def fit_ramp_far(z, x, y, use, order=1):
    """Least-squares plane (order 1) or quadric (order 2) fitted ONLY where `use` is True
    (and z finite); returns (model over the full grid, coefficients)."""
    z = np.asarray(z, dtype=np.float64)
    m = np.asarray(use, bool) & np.isfinite(z)
    x0, y0 = x[m].mean(), y[m].mean()
    xs, ys = (x - x0) / 1e4, (y - y0) / 1e4
    A = _design(xs, ys, order)
    coef = np.linalg.lstsq(A[m], z[m], rcond=None)[0]
    return A @ coef, coef


def remove_ramp_far(z, x, y, use, order=1):
    model, coef = fit_ramp_far(z, x, y, use, order)
    return z - model, coef


def disc_mask(X, Y, cx, cy, r):
    """True inside a disc of radius r (same units as X, Y)."""
    return (X - cx) ** 2 + (Y - cy) ** 2 <= r * r


def largest_component(cc, ok):
    """Mask of the connected component (label) with the most valid pixels; labels 0 and 65535 are invalid."""
    ok = ok & (cc != 0) & (cc != 65535)
    if not ok.any():
        return ok
    lab = np.bincount(cc[ok].astype(np.int64))
    return ok & (cc == lab.argmax())


def decode(z, k, shape=None):
    """Undo remote_products.reduce_npz2 (uint8 coherence, int16 phase steps, block-averaged screens)."""
    a = z[k]
    if shape is not None and a.ndim == 2 and k + "__fac" not in z.files and a.shape != tuple(shape) \
            and a.shape[0] >= shape[0] and a.shape[1] >= shape[1]:
        a = a[:shape[0], :shape[1]]
    if k + "__step" in z.files:
        return np.where(a == -32768, np.nan, a.astype(np.float32) * np.float32(z[k + "__step"])
                        + np.float32(z[k + "__offset"]))
    if k + "__fac" in z.files and shape is not None and a.shape != tuple(shape):
        from scipy.ndimage import zoom
        a = np.nan_to_num(a, nan=float(np.nanmean(a)))
        out = zoom(a, (shape[0] / a.shape[0], shape[1] / a.shape[1]), order=1)
        return out[:shape[0], :shape[1]]
    if k + "__scale" in z.files:
        return np.where(a == 255, np.nan, a.astype(np.float32) / float(z[k + "__scale"]))
    if k + "__offset" in z.files:
        return a.astype(np.float32) + np.float32(z[k + "__offset"])
    return a




# ============================================================ two-look decomposition (unit-tested)
def decompose_east_up(d_asc, d_desc, ue_asc, ue_desc, inc_asc_deg, inc_desc_deg):
    """Approximate east and up components [same unit as input] from two LOS maps (positive toward the satellite).
    ue_* = east component of the unit vector pointing from satellite to ground; inc_* = incidence angle.
    LOS(+toward satellite) = -E*ue - N*un + U*cos(inc). North is neglected (the orbit is nearly polar, the
    north component of the LOS vector is small); a real north-south motion would leak into E and U."""
    a11, a12 = -np.asarray(ue_asc, float), np.cos(np.radians(inc_asc_deg))
    a21, a22 = -np.asarray(ue_desc, float), np.cos(np.radians(inc_desc_deg))
    det = a11 * a22 - a12 * a21
    E = (d_asc * a22 - a12 * d_desc) / det
    U = (a11 * d_desc - a21 * d_asc) / det
    return E, U


# ============================================================ config
def load_events():
    with open(EVENTS_JSON) as f:
        return json.load(f)


def get_event(eid):
    for e in load_events():
        if e["id"] == eid:
            return e
    raise SystemExit(f"unknown event {eid!r}; see `list`")


def views_of(ev, only=None):
    return {k: v for k, v in ev["views"].items() if only in (None, k)}


def proc_dir(ev):
    return os.path.join(ROOT, "data", "processed", f"{ev['id']}-insar")


def view_dir(ev, view):
    """Local folder of one look direction (the first Tibet pass stored its files flat)."""
    return proc_dir(ev) if ev.get("legacy_flat") else os.path.join(proc_dir(ev), view)


def fig_dir(ev):
    return DOCS if ev.get("legacy_flat") else os.path.join(DOCS, ev["id"])


def app_dir(ev):
    return os.path.join(ROOT, "data", "processed", ev["id"], "products")


def sources_of(ev):
    """Epicentres excluded from the far field: the event itself and any other large event in the pair."""
    return [(ev["lon"], ev["lat"])] + [(s["lon"], s["lat"]) for s in ev.get("sources", [])]


def granule_url(g):
    base = "https://nisar.asf.earthdatacloud.nasa.gov"
    folder = "NISAR-UR/UR_L2_L_GUNW" if "_UR_GUNW_" in g else "NISAR/NISAR_L2_GUNW_PROVISIONAL_V1"
    return f"{base}/{folder}/{g}/{g}.h5"


def job_for(ev, view):
    v = ev["views"][view]
    return {"out": f"~/work2/out/quake/{ev['id']}/{view}",
            "windows": [{"id": "win", "bbox": v.get("window", ev["window"]), "fine": False}],
            "gunw_fine": False, "cache_mb": 12, "hmin": v.get("heights", ev["heights"])[0], "hmax": v.get("heights", ev["heights"])[1],
            "layers": ["unwrappedPhase", "coherenceMagnitude", "connectedComponents"] if not ev.get("legacy_flat")
            else ["unwrappedPhase", "coherenceMagnitude", "connectedComponents", "ionospherePhaseScreen",
                  "ionospherePhaseScreenUncertainty"],
            "products": [{"id": v[k]["pair"], "url": granule_url(v[k]["granule"]), "kind": "GUNW"}
                         for k in ("co", "control")]}


# ============================================================ server (address and key come from the environment)
def _ssh_base():
    srv, key = os.environ.get("SERVER", ""), os.path.expanduser(os.environ.get("SSH_KEY", ""))
    if not srv or not key:
        raise SystemExit("set SERVER and SSH_KEY in the environment")
    return srv, ["-o", "IdentitiesOnly=yes", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]


def launch(ev, view):
    """Copy the job file to the server's ~/work2 and start the extraction detached."""
    srv, opts = _ssh_base()
    name = f"job_quake_{ev['id']}_{view}.json"
    log = f"~/work2/logs/quake_{ev['id']}_{view}.log"
    cmd = (f"cat > ~/work2/{name}; mkdir -p ~/work2/logs; cd ~/work2; "
           f"(setsid nohup ~/work/venv/bin/python remote_products.py extract ~/work2/{name} >> {log} 2>&1 < /dev/null &); "
           "sleep 1; echo started")
    r = subprocess.run(["ssh"] + opts + [srv, cmd], input=json.dumps(job_for(ev, view)), text=True, capture_output=True)
    print(ev["id"], view, r.stdout.strip()[-40:], r.returncode)


def queue(items, lanes=2, batch=0):
    """Upload the job files of items [(event, view), ...] and start detached server lanes (default two) that each run their
    share one extraction at a time, waiting until at least 100 MB of memory is available before each start
    (the server has 2 CPUs and 0.5 GB and is shared)."""
    srv, opts = _ssh_base()
    names = []
    for ev, view in items:
        name = f"job_quake_{ev['id']}_{view}.json"
        subprocess.run(["ssh"] + opts + [srv, f"cat > ~/work2/{name}"], input=json.dumps(job_for(ev, view)), text=True, capture_output=True)
        names.append((name, f"{ev['id']}_{view}"))
    for lane in range(lanes):
        lines = ["cd ~/work2; mkdir -p logs"]
        if batch:  # wait for the lanes of all earlier batches (numbered lower) so that never more than `lanes` extractions run
            lines.append("while ps aux | grep '[b]ash /home/opc/work2/queue_quake_b' | grep -o 'queue_quake_b[0-9]*' | tr -d 'a-z_' | awk '{if ($1+0 < %d) f=1} END{exit !f}'; do sleep 20; done" % batch)
        for name, tag in names[lane::lanes]:
            lines.append("while [ $(free -m | awk '/Mem:/{print $7}') -lt 100 ]; do sleep 15; done")
            lines.append(f"~/work/venv/bin/python remote_products.py extract ~/work2/{name} >> ~/work2/logs/quake_{tag}.log 2>&1 < /dev/null")
        script = "\n".join(lines) + "\n"
        r = subprocess.run(["ssh"] + opts + [srv, f"cat > ~/work2/queue_quake_b{batch}_{lane}.sh; (setsid nohup bash ~/work2/queue_quake_b{batch}_{lane}.sh > ~/work2/logs/queue_quake_b{batch}_{lane}.log 2>&1 < /dev/null &); echo queued"],
                           input=script, text=True, capture_output=True)
        print("lane", lane, r.stdout.strip()[-30:], [t for _, t in names[lane::lanes]])


def fetch(ev, view):
    srv, opts = _ssh_base()
    d = view_dir(ev, view)
    os.makedirs(d, exist_ok=True)
    remote = "~/work2/out/quake_tibet/tibet/small2" if ev.get("legacy_flat") else \
        f"~/work2/out/quake/{ev['id']}/{view}/win/small2"
    cmd = ["scp", "-q"] + opts + [f"{srv}:{remote}/GUNW_*.npz", d + "/"]
    print(f"fetch {ev['id']} {view}:", subprocess.run(cmd).returncode)


def load_pair(ev, view, which):
    pair = ev["views"][view][which]["pair"]
    z = np.load(os.path.join(view_dir(ev, view), f"GUNW_{pair}.npz"), allow_pickle=False)
    shape = (len(z["y"]), len(z["x"]))
    P = {"x": z["x"], "y": z["y"], "epsg": int(z["epsg"]), "pair": pair}
    for k in ("unwrappedPhase", "coherenceMagnitude", "ionospherePhaseScreen", "ionospherePhaseScreenUncertainty"):
        if k in z.files:
            P[k] = decode(z, k, shape)
    P["connectedComponents"] = decode(z, "connectedComponents", shape)
    for k in z.files:
        if k.startswith("cube_") or k.startswith("meta_"):
            P[k] = z[k][()] if z[k].ndim == 0 else decode(z, k)
    f0 = float(P.get("meta_centerFrequency", 1.239e9))
    P["wavelength"] = C_LIGHT / f0
    P["X"], P["Y"] = np.meshgrid(P["x"], P["y"])
    return P


# ============================================================ analysis
def lonlat_xy(epsg, lon, lat):
    from rasterio.warp import transform
    a, b = transform("EPSG:4326", f"EPSG:{epsg}", [lon], [lat])
    return a[0], b[0]


def stripe_line(P, ex, ey):
    """Tibet: the co-seismic pair has a straight low-coherence band SW-NE south of the epicentre across which the
    unwrapped phase jumps by whole cycles (processing artefact). Find it per column block (minimum of the
    smoothed coherence south of the epicentre) and fit a line; returns (intercept, slope) for y = a + b*x."""
    from scipy.ndimage import uniform_filter
    coh = np.nan_to_num(P["coherenceMagnitude"], nan=0.0)
    sm = uniform_filter(coh, size=(25, 25))
    xs, ys = [], []
    for c0 in range(0, len(P["x"]) - 40, 40):
        col = sm[:, c0:c0 + 40].mean(1)
        rows = np.nonzero(P["y"] < ey - 25e3)[0]
        if len(rows) < 10:
            continue
        r = rows[np.argmin(col[rows])]
        xs.append(P["x"][c0:c0 + 40].mean()); ys.append(P["y"][r])
    xs, ys = np.array(xs), np.array(ys)
    b, a = np.polyfit(xs, ys, 1)
    for _ in range(3):  # drop outliers
        res = ys - (a + b * xs)
        k = np.abs(res) < 2.5 * max(res.std(), 500)
        b, a = np.polyfit(xs[k], ys[k], 1)
    return a, b


def valid_mask(ev, P, view_cfg=None):
    view_cfg = view_cfg or {}
    """Event-specific extra mask: Tibet artefact band; optional lon/lat boxes `mask_boxes` [[w,s,e,n],...]."""
    keep = np.ones(P["X"].shape, bool)
    for w, s, e, n in ev.get("mask_boxes", []) + view_cfg.get("mask_boxes", []):
        x0, y0 = lonlat_xy(P["epsg"], w, s); x1, y1 = lonlat_xy(P["epsg"], e, n)
        keep &= ~((P["X"] >= min(x0, x1)) & (P["X"] <= max(x0, x1)) & (P["Y"] >= min(y0, y1)) & (P["Y"] <= max(y0, y1)))
    return keep


def tibet_north_masks(Pc, Pt, ex, ey):
    a, b = stripe_line(Pc, ex, ey)

    def north_mask(P):  # north of the artefact band (+16 km), minus the SW corner where the band's edge still leaks
        m = P["Y"] > (a + b * P["X"] + 16e3)
        return m & ~((P["X"] < ex - 55e3) & (P["Y"] < ey - 25e3))
    return north_mask(Pc), north_mask(Pt), {"intercept_m": a, "slope": b}


def process(P, keep, srcs_xy, comp=None, use_iono=False, r_km=60.0, gmin=0.35, order=1):
    """LOS displacement [cm, + toward satellite], referenced and de-ramped on the far field only
    (outside r_km of every source in srcs_xy). comp: connected component to keep (None = the largest)."""
    phi = P["unwrappedPhase"].astype(np.float64)
    if use_iono:
        phi = phi - P["ionospherePhaseScreen"]
    cc = P["connectedComponents"]
    ccm = (cc == comp) if comp is not None else largest_component(cc, np.isfinite(phi))
    ok = np.isfinite(phi) & (P["coherenceMagnitude"] >= gmin) & ccm & keep
    d = phase_to_los_cm(phi, P["wavelength"])
    d[~ok] = np.nan
    far = ok.copy()
    for sx, sy in srcs_xy:
        far &= ~disc_mask(P["X"], P["Y"], sx, sy, r_km * 1e3)
    res, coef = remove_ramp_far(d, P["X"], P["Y"], far, order)
    res -= np.nanmedian(res[far])
    res[~ok] = np.nan
    return res, ok, far, coef


def local_noise(rr, ff):  # std of the map minus its own 10 km running mean, far field only (NaN-aware)
    from scipy.ndimage import uniform_filter
    v = np.where(np.isfinite(rr), rr, 0.0); w = np.isfinite(rr).astype(float)
    sm = uniform_filter(v, 125) / np.maximum(uniform_filter(w, 125), 1e-6)
    sel = ff & (uniform_filter(w, 125) > 0.6)
    return float(np.nanstd((rr - sm)[sel])) if sel.any() else float("nan")


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def _save(ev, fig, name, dpi=80):
    d = fig_dir(ev)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name)
    fig.savefig(p, dpi=dpi, bbox_inches="tight")
    print("wrote", p, os.path.getsize(p) // 1024, "KB")


def analyse_view(ev, view):
    """All numbers, arrays and figures for one look direction. Returns (result dict, arrays dict)."""
    plt = _plt()
    from matplotlib.colors import TwoSlopeNorm
    v = ev["views"][view]
    suf = "" if len(ev["views"]) == 1 else "_" + view
    Pc, Pt = load_pair(ev, view, "co"), load_pair(ev, view, "control")
    ex, ey = lonlat_xy(Pc["epsg"], ev["lon"], ev["lat"])
    srcs = [lonlat_xy(Pc["epsg"], lo, la) for lo, la in sources_of(ev)]
    keep_c, keep_t = valid_mask(ev, Pc, v), valid_mask(ev, Pt, v)
    out = {"event": f"USGS {ev['usgs_id']} M{ev['mag']} {ev['time']}, {ev['lat']}N {ev['lon']}E, depth {ev['depth_km']} km",
           "view": view, "track": v["track"], "frame": v["frame"], "direction": v["direction"],
           "coseismic_pair": v["co"]["pair"], "control_pair": v["control"]["pair"], "wavelength_m": Pc["wavelength"]}
    if ev.get("stripe"):
        keep_c, keep_t, out["stripe_line"] = tibet_north_masks(Pc, Pt, ex, ey)
    comp, gmin, radius = ev.get("component"), v.get("gmin", ev.get("gmin", 0.35)), v.get("radius_km", ev.get("radius_km", 45))
    R = v.get("far_km", ev.get("far_km", 60))
    var = {}
    variants = {"main_noiono_R%d" % R: {}}
    if "ionospherePhaseScreen" in Pc:
        variants["iono_R%d" % R] = {"use_iono": True}
    variants.update({"noiono_R%d" % (R - 15): {"r_km": R - 15}, "noiono_R%d" % (R + 15): {"r_km": R + 15},
                     "noiono_quadric_R%d" % R: {"order": 2}, "noiono_coh05_R%d" % R: {"gmin": 0.5}})
    for name, kw in variants.items():
        kw = dict(kw); kw.setdefault("r_km", R); kw.setdefault("gmin", gmin)
        r, ok, far, coef = process(Pc, keep_c, srcs, comp, **kw)
        rt, okt, fart, _ = process(Pt, keep_t, srcs, comp, **kw)
        near = ok & disc_mask(Pc["X"], Pc["Y"], ex, ey, radius * 1e3)
        nt = okt & disc_mask(Pt["X"], Pt["Y"], ex, ey, radius * 1e3)
        if near.sum() < 50:
            var[name] = {"near_valid_pixels": int(near.sum())}
            continue
        vv = r[near]
        lo, hi = np.nanpercentile(vv, [0.5, 99.5])
        var[name] = {"p2p_cm_0.5_99.5pct": float(hi - lo), "min_cm": float(np.nanmin(vv)), "max_cm": float(np.nanmax(vv)),
                     "p0.5_cm": float(lo), "p99.5_cm": float(hi),
                     "far_std_cm": float(np.nanstd(r[far])), "control_far_std_cm": float(np.nanstd(rt[fart])),
                     "control_p2p_cm_0.5_99.5pct": float(np.diff(np.nanpercentile(rt[nt], [0.5, 99.5]))[0]) if nt.sum() > 50 else None,
                     "control_max_abs_cm": float(np.nanmax(np.abs(rt[okt]))), "far_pixels": int(far.sum()),
                     "near_valid_pixels": int(near.sum())}
    out["radius_km"], out["far_km"] = radius, R
    out["variants"] = var
    m = var["main_noiono_R%d" % R]
    r, ok, far, coef = process(Pc, keep_c, srcs, comp, r_km=R, gmin=gmin)
    rt, okt, fart, _ = process(Pt, keep_t, srcs, comp, r_km=R, gmin=gmin)
    if "p0.5_cm" in m:
        out["snr_peak_over_control_std"] = max(abs(m["p0.5_cm"]), abs(m["p99.5_cm"])) / m["control_far_std_cm"]
        if m.get("control_p2p_cm_0.5_99.5pct"):
            out["snr_p2p_over_control_p2p"] = m["p2p_cm_0.5_99.5pct"] / m["control_p2p_cm_0.5_99.5pct"]
    out["local_noise_std_cm_coseismic_far"] = local_noise(r, far)
    out["local_noise_std_cm_control_far"] = local_noise(rt, fart)
    pix_km2 = abs((Pc["x"][1] - Pc["x"][0]) * (Pc["y"][1] - Pc["y"][0])) / 1e6
    thr = 3 * m.get("control_far_std_cm", 1.0)
    for t in (5, 10, 20):
        out[f"area_km2_absLOS_gt_{t}cm"] = float((np.abs(np.nan_to_num(r)) > t).sum() * pix_km2)
        out[f"control_area_km2_absLOS_gt_{t}cm"] = float((np.abs(np.nan_to_num(rt)) > t).sum() * pix_km2)
    out["area_km2_absLOS_gt_%.1fcm" % max(thr, 2.0)] = float(((np.abs(np.nan_to_num(r)) > max(thr, 2.0)) & ok).sum() * pix_km2)
    out["deformed_area_km2_absLOS_gt_%.1fcm" % max(thr, 2.0)] = out["area_km2_absLOS_gt_%.1fcm" % max(thr, 2.0)]
    s5 = (np.abs(np.nan_to_num(r)) > 5) & ok
    if s5.any():
        yy, xx = np.nonzero(s5)
        out["extent_gt5cm_km_EW_NS"] = [float((xx.max() - xx.min()) * abs(Pc["x"][1] - Pc["x"][0]) / 1e3),
                                        float((yy.max() - yy.min()) * abs(Pc["y"][1] - Pc["y"][0]) / 1e3)]
    out["masked_fraction_within_15km_of_epicentre"] = float(1 - ok[disc_mask(Pc["X"], Pc["Y"], ex, ey, 15e3)].mean())
    out["north_fraction_of_window"] = float(keep_c.mean())
    out["valid_fraction_window"] = float(ok.mean())
    out["valid_fraction_within_radius"] = float(ok[disc_mask(Pc["X"], Pc["Y"], ex, ey, radius * 1e3)].mean())
    out["valid_fraction_within_15km"] = float(ok[disc_mask(Pc["X"], Pc["Y"], ex, ey, 15e3)].mean())
    out["mean_coherence_valid"] = float(np.nanmean(Pc["coherenceMagnitude"][ok])) if ok.any() else None
    out["mean_coherence_control_valid"] = float(np.nanmean(Pt["coherenceMagnitude"][okt])) if okt.any() else None
    out["whole_window_coherence_ge_gmin"] = float((Pc["coherenceMagnitude"] >= gmin).mean())
    if "ionospherePhaseScreen" in Pc:
        for tag, P in (("coseismic", Pc), ("control", Pt)):
            s = P["ionospherePhaseScreen"]; d30 = disc_mask(P["X"], P["Y"], ex, ey, 30e3)
            out[f"iono_screen_{tag}_rad_range_within30km"] = [float(np.nanmin(s[d30])), float(np.nanmax(s[d30]))]
            out[f"iono_screen_{tag}_rad_std_outside60km"] = float(np.nanstd(s[~disc_mask(P['X'], P['Y'], ex, ey, 60e3)]))
        rti, okti, farti, _ = process(Pt, keep_t, srcs, comp, use_iono=True, r_km=R, gmin=gmin)
        out["control_with_iono_far_std_cm"] = float(np.nanstd(rti[farti]))
        out["control_without_iono_far_std_cm"] = float(np.nanstd(rt[fart]))
    out["los_unit_vector_EN_mean"] = [float(np.nanmean(Pc["cube_losUnitVectorX"])), float(np.nanmean(Pc["cube_losUnitVectorY"]))]
    out["incidence_deg_mean"] = float(np.nanmean(Pc["cube_incidenceAngle"]))
    # ---- profile (azimuth AZ, swath 5 km)
    AZ = float(ev.get("profile_az", 0))
    L = 70e3 if radius >= 40 else 40e3
    t = np.linspace(-L, L, 141)
    ux, uy = math.sin(math.radians(AZ)), math.cos(math.radians(AZ))

    def prof(rr, P):
        vals = []
        for tt in t:
            m_ = disc_mask(P["X"], P["Y"], ex + tt * ux, ey + tt * uy, 2.5e3)
            vals.append(np.nanmedian(rr[m_]) if np.isfinite(rr[m_]).sum() > 20 else np.nan)
        return np.array(vals)
    pr_c, pr_t = prof(r, Pc), prof(rt, Pt)
    out["profile_azimuth_deg"] = AZ
    out["profile_coseismic_cm"] = {"km": (t[::10] / 1e3).tolist(), "cm": [None if not np.isfinite(q) else round(float(q), 1) for q in pr_c[::10]]}
    # ---- figures
    ext = lambda P: ((P["x"][0] - ex) / 1e3, (P["x"][-1] - ex) / 1e3, (P["y"][-1] - ey) / 1e3, (P["y"][0] - ey) / 1e3)
    coh = Pc["coherenceMagnitude"]
    cmap = plt.get_cmap("RdBu").copy(); cmap.set_bad("none")
    lim = float(ev.get("los_lim_cm", 40)); fcm = float(ev.get("fringe_cm", 10))
    norm = TwoSlopeNorm(vmin=-lim, vcenter=0, vmax=lim)
    d1, d2 = v["co"]["pair"].split("_"), v["control"]["pair"].split("_")
    dt = lambda a: f"{a[6:]}/{a[4:6]}"
    span_c, span_t = f"{dt(d1[0])}-{dt(d1[1])}", f"{dt(d2[0])}-{dt(d2[1])}"
    ttl_ev = f"{ev['title']} ({v['label']} track {v['track']}, frame {v['frame']})"
    stars = lambda ax: [ax.plot((sx - ex) / 1e3, (sy - ey) / 1e3, marker="*", ms=16 if i == 0 else 11, mfc="yellow" if i == 0 else "white", mec="k")
                        for i, (sx, sy) in enumerate([(ex, ey)] + srcs[1:])]
    fig, ax = plt.subplots(figsize=(7.2, 6.4))
    ax.imshow(coh, extent=ext(Pc), cmap="gray", vmin=0, vmax=1)
    fr = rewrap(np.where(ok, r, np.nan), fcm)
    im = ax.imshow(np.ma.masked_invalid(fr), extent=ext(Pc), cmap="twilight", vmin=0, vmax=1, alpha=0.9, interpolation="nearest")
    stars(ax); ax.set_xlabel("km east of epicentre"); ax.set_ylabel("km north of epicentre")
    ax.set_title(f"{ttl_ev}\nNISAR {span_c}: LOS displacement re-wrapped, one colour cycle = {fcm:g} cm (grey = coherence where unusable)", fontsize=8)
    plt.colorbar(im, ax=ax, shrink=0.8).set_label(f"fraction of {fcm:g} cm cycle")
    _save(ev, fig, f"fig_a_fringes{suf}.png"); plt.close(fig)
    fig, ax = plt.subplots(figsize=(7.2, 6.4))
    ax.imshow(coh, extent=ext(Pc), cmap="gray", vmin=0, vmax=1)
    im = ax.imshow(np.ma.masked_invalid(r), extent=ext(Pc), cmap=cmap, norm=norm, interpolation="nearest")
    stars(ax); ax.plot(t * ux / 1e3, t * uy / 1e3, "k--", lw=0.8)
    ax.set_xlabel("km east of epicentre"); ax.set_ylabel("km north of epicentre")
    ax.set_title(f"{ttl_ev}\nLOS displacement {span_c} (cm; + = toward satellite, sign unconfirmed), ramp removed on far field (> {R} km)", fontsize=8)
    plt.colorbar(im, ax=ax, shrink=0.8).set_label("LOS displacement (cm)")
    _save(ev, fig, f"fig_b_los_cm{suf}.png"); plt.close(fig)
    fig, ax = plt.subplots(figsize=(7.2, 6.4))
    ax.imshow(Pt["coherenceMagnitude"], extent=ext(Pt), cmap="gray", vmin=0, vmax=1)
    im = ax.imshow(np.ma.masked_invalid(rt), extent=ext(Pt), cmap=cmap, norm=norm, interpolation="nearest")
    stars(ax); ax.set_xlabel("km east of epicentre"); ax.set_ylabel("km north of epicentre")
    ax.set_title(f"Control, same track and frame, {span_t}, same processing and colour scale\nfar-field std {np.nanstd(rt[fart]):.2f} cm", fontsize=8)
    plt.colorbar(im, ax=ax, shrink=0.8).set_label("LOS displacement (cm)")
    _save(ev, fig, f"fig_c_control_cm{suf}.png"); plt.close(fig)
    fig, ax = plt.subplots(figsize=(7.2, 3.8))
    ax.plot(t / 1e3, pr_c, "C3", label=f"co-seismic {span_c}"); ax.plot(t / 1e3, pr_t, "C0", label=f"control {span_t}")
    ax.axvline(0, color="k", lw=0.6); ax.axhline(0, color="k", lw=0.6)
    ax.set_xlabel(f"km along azimuth {AZ:g} deg through the epicentre, 5 km median"); ax.set_ylabel("LOS displacement (cm)")
    ax.legend(); ax.set_title(f"Profile through the epicentre, {v['label']}", fontsize=9)
    _save(ev, fig, f"fig_d_profile{suf}.png"); plt.close(fig)
    if ev.get("legacy_flat") and "ionospherePhaseScreen" in Pc:
        fig, axs = plt.subplots(1, 2, figsize=(11, 4.6))
        for a_, P, ttl in ((axs[0], Pc, "co-seismic pair"), (axs[1], Pt, "control pair")):
            im = a_.imshow(P["ionospherePhaseScreen"], extent=ext(P), cmap="viridis", vmin=-22, vmax=2)
            a_.plot(0, 0, marker="*", ms=12, mfc="yellow", mec="k"); a_.set_title("ionosphere phase screen in the product, " + ttl, fontsize=9)
            plt.colorbar(im, ax=a_, shrink=0.8).set_label("rad")
        _save(ev, fig, "fig_e_iono_screen.png"); plt.close(fig)
    arrays = {"los_cm": np.where(np.isfinite(r), r, np.nan).astype(np.float32), "coh": coh.astype(np.float32),
              "x": Pc["x"], "y": Pc["y"], "epsg": Pc["epsg"], "ex": ex, "ey": ey,
              "ue": out["los_unit_vector_EN_mean"][0], "un": out["los_unit_vector_EN_mean"][1],
              "inc": out["incidence_deg_mean"], "ctl_cm": np.where(np.isfinite(rt), rt, np.nan).astype(np.float32),
              "ctl_x": Pt["x"], "ctl_y": Pt["y"]}
    np.savez_compressed(os.path.join(view_dir(ev, view), "products_cm.npz"), **arrays)
    return out, arrays


def run(ev, only=None):
    p = os.path.join(DOCS, "results.json")
    prev = json.load(open(p)).get(ev["id"], {}) if os.path.exists(p) else {}
    res = {"event_id": ev["id"], "verdict": ev.get("verdict"), "verdict_reason": ev.get("verdict_reason"),
           "views": dict(prev.get("views", {}))}
    for view in views_of(ev, only):
        out, arr = analyse_view(ev, view)
        res["views"][view] = out
        print(json.dumps({k: v for k, v in out.items() if k not in ("profile_coseismic_cm", "variants")}, indent=1))
    if len(ev["views"]) > 1 and ev.get("decompose") and all(os.path.exists(os.path.join(view_dir(ev, k), "products_cm.npz")) for k in ev["decompose"]):
        res["decomposition"] = decomposition(ev)
    store_results(ev["id"], res)


def store_results(eid, res):
    p = os.path.join(DOCS, "results.json")
    all_ = json.load(open(p)) if os.path.exists(p) else {}
    if "event_id" in all_ or "event" in all_:  # old flat Tibet file -> keyed layout
        all_ = {"tibet-m70-2026": {"event_id": "tibet-m70-2026", "views": {"asc": all_}}}
    all_[eid] = res
    with open(p, "w") as f:
        json.dump(all_, f, indent=1)


# ============================================================ stage 3: static app layers (contract B)
def _hex(c):
    return "#%02x%02x%02x" % tuple(int(round(255 * v)) for v in c[:3])


def _grid(ev):
    W, S, E, N = ev.get("app_bbox", ev["window"])
    w = 1000
    h = int(round(w * (N - S) / ((E - W) * math.cos(math.radians((N + S) / 2)))))
    return (W, S, E, N), w, h


def to_app_grid(ev, arr2d, x, y, epsg, resampling="nearest"):
    """Reproject a 2-D float array from the product grid to the common EPSG:4326 app grid (NaN outside)."""
    import rasterio
    from rasterio.transform import from_bounds
    from rasterio.warp import Resampling, reproject
    (W, S, E, N), w, h = _grid(ev)
    dx = float(x[1] - x[0]); dy = float(y[1] - y[0])
    src_t = rasterio.Affine(dx, 0, x[0] - dx / 2, 0, dy, y[0] - dy / 2)
    dst = np.full((h, w), np.nan, np.float32)
    reproject(np.ascontiguousarray(arr2d.astype(np.float32)), dst, src_transform=src_t, src_crs=f"EPSG:{epsg}",
              dst_transform=from_bounds(W, S, E, N, w, h), dst_crs="EPSG:4326", resampling=Resampling.nearest,
              src_nodata=np.nan, dst_nodata=np.nan)
    return dst


def decomposition(ev, views=None):
    """Approximate east/up maps (cm) on the app grid from the first ascending and first descending view."""
    views = views or ev["decompose"]  # [ascending view name, descending view name]
    A, D = [np.load(os.path.join(view_dir(ev, k), "products_cm.npz")) for k in views]
    ga = to_app_grid(ev, A["los_cm"], A["x"], A["y"], int(A["epsg"]))
    gd = to_app_grid(ev, D["los_cm"], D["x"], D["y"], int(D["epsg"]))
    E, U = decompose_east_up(ga, gd, float(A["ue"]), float(D["ue"]), float(A["inc"]), float(D["inc"]))
    (W, S, Ee, N), w, h = _grid(ev)
    lon = np.linspace(W, Ee, w)[None, :] + 0 * np.linspace(S, N, h)[:, None]
    lat = np.linspace(N, S, h)[:, None] + 0 * lon
    near = np.hypot((lon - ev["lon"]) * 111.32 * math.cos(math.radians(ev["lat"])), (lat - ev["lat"]) * 110.57) <= ev.get("radius_km", 45)
    fin = np.isfinite(E) & np.isfinite(U) & near
    out = {"views": views, "overlap_pixels_within_radius": int(fin.sum()), "ue_asc_desc": [float(A["ue"]), float(D["ue"])],
           "inc_asc_desc": [float(A["inc"]), float(D["inc"])], "note": "north component neglected; approximate"}
    if fin.sum() > 100:
        for nm, a in (("east", E), ("up", U)):
            lo, hi = np.nanpercentile(a[fin], [0.5, 99.5])
            out[nm + "_p0.5_p99.5_cm"] = [float(lo), float(hi)]
    np.savez_compressed(os.path.join(proc_dir(ev), "decomp.npz"), east=E, up=U, asc=ga, desc=gd)
    plt = _plt()
    lim = float(ev.get("decomp_lim_cm", 40))
    fig, axs = plt.subplots(1, 2, figsize=(11, 3.8))
    for ax, (nm, a, cmap) in zip(axs, (("east (+ = toward east), approximate", E, "PuOr"), ("up (+ = up), approximate", U, "PiYG"))):
        im = ax.imshow(np.ma.masked_invalid(a), extent=(W, Ee, S, N), cmap=cmap, vmin=-lim, vmax=lim, interpolation="nearest")
        ax.plot(ev["lon"], ev["lat"], marker="*", ms=12, mfc="yellow", mec="k"); ax.set_title(nm + " (cm)", fontsize=9)
        plt.colorbar(im, ax=ax, shrink=0.8)
    fig.suptitle(f"{ev['title']}: two-look decomposition (north neglected, far field referenced, sign unconfirmed)", fontsize=9)
    _save(ev, fig, "fig_e_decomposition.png"); plt.close(fig)
    return out


def export_products(ev):
    """Static RGBA GeoTIFFs (EPSG:4326, north up, uint8) + layers.json + timeseries.json + source.json."""
    import rasterio
    from rasterio.transform import from_bounds
    import matplotlib
    cm = matplotlib.colormaps
    if ev.get("frozen"):
        raise SystemExit(f"{ev['id']}: app layers already exported by the first pass; not overwritten")
    if ev.get("verdict") != "clear":
        raise SystemExit(f"{ev['id']}: verdict is {ev.get('verdict')!r}; only 'clear' events are exported")
    views = ev.get("export_views", list(ev["views"]))
    prim = ev.get("primary_view", views[0])
    lim, fcm = float(ev.get("los_lim_cm", 40)), float(ev.get("fringe_cm", 10))
    (W, S, E, N), w, h = _grid(ev)
    D = {k: np.load(os.path.join(view_dir(ev, k), "products_cm.npz")) for k in views}
    G = {k: to_app_grid(ev, D[k]["los_cm"], D[k]["x"], D[k]["y"], int(D[k]["epsg"])) for k in views}
    coh = to_app_grid(ev, D[prim]["coh"], D[prim]["x"], D[prim]["y"], int(D[prim]["epsg"]))

    def rgba(v, cmap, vmin, vmax, finite):
        t = np.clip((v - vmin) / (vmax - vmin), 0, 1)
        c = (cm[cmap](np.nan_to_num(t)) * 255).astype(np.uint8)
        c[..., 3] = np.where(finite, 255, 0)
        return c
    layers = {}
    for k in views:
        name = "los_cm" if len(views) == 1 else "los_%s_cm" % ("asc" if ev["views"][k]["direction"] == "A" else "desc")
        layers[name] = rgba(G[k], "RdBu", -lim, lim, np.isfinite(G[k]))
    layers["fringes"] = rgba(rewrap(np.nan_to_num(G[prim]), fcm), "twilight", 0, 1, np.isfinite(G[prim]))
    layers["coherence"] = rgba(coh, "gray", 0, 1, np.isfinite(coh))
    dec = ev.get("decompose") and os.path.exists(os.path.join(proc_dir(ev), "decomp.npz")) and ev.get("export_decomposition")
    if dec:
        z = np.load(os.path.join(proc_dir(ev), "decomp.npz"))
        dl = float(ev.get("decomp_lim_cm", lim))
        layers["east_cm"] = rgba(z["east"], "PuOr", -dl, dl, np.isfinite(z["east"]))
        layers["up_cm"] = rgba(z["up"], "PiYG", -dl, dl, np.isfinite(z["up"]))
    app = app_dir(ev)
    os.makedirs(app, exist_ok=True)
    tr = from_bounds(W, S, E, N, w, h)
    for name, arr in layers.items():
        with rasterio.open(os.path.join(app, name + ".tif"), "w", driver="GTiff", height=h, width=w, count=4,
                           dtype="uint8", crs="EPSG:4326", transform=tr, compress="deflate",
                           photometric="RGB", alpha="YES") as f:
            f.write(np.moveaxis(arr, -1, 0))
        print("wrote", name, os.path.getsize(os.path.join(app, name + ".tif")) // 1024, "KB")
    tw = [_hex(cm["twilight"](v)) for v in np.linspace(0, 1, 9)]
    leg = lambda cmap: {"type": "diverging", "unit": "cm", "min": -lim, "max": lim,
                        "colors": [_hex(cm[cmap](0.0)), "#f7f7f7", _hex(cm[cmap](1.0))]}
    L = []
    for k in views:
        name = "los_cm" if len(views) == 1 else "los_%s_cm" % ("asc" if ev["views"][k]["direction"] == "A" else "desc")
        lg = leg("RdBu")
        lg["labels"] = ["red: ground moved away from the satellite",
                        "blue: ground moved toward the satellite (sign convention not confirmed by the product guide)"]
        lab = "Ground motion along the line of sight (cm)" if len(views) == 1 else \
            f"Ground motion along the line of sight, {ev['views'][k]['label']} pass (cm)"
        L.append({"id": name, "label": lab, "dated": False, "legend": lg})
    L.append({"id": "fringes", "label": f"Fringes (one colour cycle = {fcm:g} cm)", "dated": False,
              "legend": {"type": "ramp", "unit": f"one colour cycle = {fcm:g} cm of line-of-sight change", "min": 0, "max": fcm, "colors": tw}})
    L.append({"id": "coherence", "label": "Radar coherence (how comparable the two passes are)", "dated": False,
              "legend": {"type": "ramp", "unit": "coherence 0-1", "min": 0, "max": 1, "colors": ["#000000", "#ffffff"]}})
    if dec:
        dl = float(ev.get("decomp_lim_cm", lim))
        for nm, cmap, lab, lo, hi in (("east_cm", "PuOr", "Approximate east-west component (cm, from two look directions; sign convention not confirmed)", "orange: toward west", "purple: toward east"),
                                      ("up_cm", "PiYG", "Approximate vertical component (cm, from two look directions; sign convention not confirmed)", "pink: down", "green: up")):
            lg = {"type": "diverging", "unit": "cm", "min": -dl, "max": dl,
                  "colors": [_hex(cm[cmap](0.0)), "#f7f7f7", _hex(cm[cmap](1.0))], "labels": [lo, hi]}
            L.append({"id": nm, "label": lab, "dated": False, "legend": lg})
    with open(os.path.join(app, "layers.json"), "w") as f:
        json.dump(L, f, indent=2)
    last = max(ev["views"][k]["co"]["pair"].split("_")[1] for k in views)
    with open(os.path.join(app, "timeseries.json"), "w") as f:
        json.dump({"dates": [f"{last[:4]}-{last[4:6]}-{last[6:]}"], "unit": "cm", "series": [], "facts": []}, f, indent=2)
    with open(os.path.join(app, "source.json"), "w") as f:
        json.dump({"event": f"USGS {ev['usgs_id']}",
                   "views": {k: {"pair": ev["views"][k]["co"]["pair"], "track": ev["views"][k]["track"],
                                 "frame": ev["views"][k]["frame"], "direction": ev["views"][k]["label"],
                                 "control_pair": ev["views"][k]["control"]["pair"]} for k in views},
                   "product": "NISAR_L2 GUNW (PROVISIONAL, some urgent response)", "script": "scripts/detect/quake.py"}, f, indent=2)
    print("exported", app)


# ============================================================ app area + story (facts: USGS + our measured numbers only)
def _lobe_spots(ev, view):
    """Locations of the strongest positive and negative smoothed LOS change within the stated radius."""
    from scipy.ndimage import median_filter
    z = np.load(os.path.join(view_dir(ev, view), "products_cm.npz"))
    r = z["los_cm"]
    sm = median_filter(np.nan_to_num(r, nan=0.0), size=37)  # about 3 km at 80 m
    X, Y = np.meshgrid(z["x"], z["y"])
    ok = np.isfinite(r) & disc_mask(X, Y, float(z["ex"]), float(z["ey"]), ev.get("radius_km", 45) * 1e3)
    from rasterio.warp import transform
    out = {}
    for nm, fn in (("toward", np.argmax), ("away", np.argmin)):
        v = np.where(ok, sm, np.nan)
        i = int(fn(np.nan_to_num(v, nan=-1e9 if nm == "toward" else 1e9)))
        yy, xx = np.unravel_index(i, v.shape)
        lo, la = transform(f"EPSG:{int(z['epsg'])}", "EPSG:4326", [float(X[yy, xx])], [float(Y[yy, xx])])
        out[nm] = [round(lo[0], 4), round(la[0], 4)]
    return out


def write_area(ev, res):
    """areas/<id>/area.json + story.json for a clear event, numbers taken from results.json (`res`)."""
    views = ev.get("export_views", list(ev["views"]))
    prim = ev.get("primary_view", views[0])
    pv = ev["views"][prim]
    r0 = res["views"][prim]
    R = r0["radius_km"]
    m = r0["variants"]["main_noiono_R%d" % r0["far_km"]]
    two = len(views) > 1 and ev.get("export_two_looks", len(views) > 1)
    d1, d2 = pv["co"]["pair"].split("_")
    mon = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    dd = lambda a: f"{int(a[6:])} {mon[int(a[4:6])]}"
    t = ev["time"]
    evdate = f"{int(t[8:10])} {mon[int(t[5:7])]} 2026"
    p2p = m["p2p_cm_0.5_99.5pct"]
    p2ps = f"{p2p / 100:.1f} m" if p2p >= 100 else f"{p2p:.0f} cm"
    lim = ev.get("los_lim_cm", 40)
    noise = m["control_far_std_cm"]
    cp = pv["control"]["pair"].split("_")
    look = (f"two look directions (ascending and descending passes) were processed separately"
            if two else "one look direction only (provisional)")
    spots = [{"id": "epicentre", "label": "USGS epicentre", "lngLat": [ev["lon"], ev["lat"]], "radius_m": 3000}]
    ls = _lobe_spots(ev, prim)
    spots.append({"id": "toward-lobe", "label": "Strongest movement toward the satellite (blue)", "lngLat": ls["toward"], "radius_m": 3000})
    if not ev.get("toward_spot_only"):  # small events: the red extreme is only atmosphere, so no spot for it
        spots.append({"id": "away-lobe", "label": "Strongest movement away from the satellite (red)", "lngLat": ls["away"], "radius_m": 3000})
    W_, S_, E_, N_ = ev.get("app_bbox", ev["window"])
    for s_ in ev.get("sources", []):
        if not (W_ <= s_["lon"] <= E_ and S_ <= s_["lat"] <= N_):
            continue  # a second epicentre outside the map is not a spot
        spots.append({"id": "usgs-" + s_["usgs_id"], "label": f"USGS epicentre of the M{s_['mag']} event", "lngLat": [s_["lon"], s_["lat"]], "radius_m": 3000})
    extra = ev.get("story_extra", {})
    sees = (f"On {evdate} a magnitude {ev['mag']} earthquake struck {ev['place']} (USGS: {abs(ev['lat']):.2f} {'N' if ev['lat'] >= 0 else 'S'}, "
            f"{abs(ev['lon']):.2f} {'E' if ev['lon'] >= 0 else 'W'}, depth about {ev['depth_km']:.0f} km). The map shows how far the ground moved along the radar's line of sight "
            f"between two NISAR passes, {dd(d1)} and {dd(d2)} 2026. Blue is ground that moved toward the satellite, red away from it. "
            f"In the fringe layer, one colour cycle is {ev.get('fringe_cm', 10):g} cm. " + extra.get("see", ""))
    phys = (f"The US Geological Survey reports {ev['mechanism'].split(' (')[0]} for this event. Within {R:g} km of the epicentre the line-of-sight change "
            f"spans about {p2ps} from the most red to the most blue pixels (0.5th to 99.5th percentile). " + extra.get("physics", "") +
            " Fault position and mechanism come from USGS; our map does not by itself tell which fault plane moved.")
    lb = (f"NISAR's L-band radar has a wavelength of about 24 cm, so one full colour cycle means about 12 cm of ground movement along the line of sight "
          f"(we show {ev.get('fringe_cm', 10):g} cm cycles). The same track and frame, {dd(cp[0])} to {dd(cp[1])}, with no event in it, "
          f"shows no such pattern: its far-field noise is about {noise:.1f} cm. The products are PROVISIONAL, {look}, and the sign convention "
          f"(blue = toward the satellite) follows the interferogram definition we assumed, not a statement in the product guide. " + extra.get("lband", ""))
    people = ("This is a measurement of ground movement, not of damage. We did not look at reports of damage or casualties. Maps like this show where the ground "
              "moved and by how much, which feeds hazard models. " +
              ("With two look directions we can separate, only approximately, vertical and east-west motion; " if ev.get("export_decomposition") else
               "From one look direction we see only one component of the motion, so uplift and sideways movement are mixed together. ") +
              extra.get("people", ""))
    area = {"id": ev["id"], "name": f"{ev['title'].replace(' earthquake', ' earthquake').replace(', ' + evdate.split()[1], ', ' + evdate.split()[0] + ' ' + evdate.split()[1])}".strip(),
            "bbox": ev.get("app_bbox", ev["window"]), "track": pv["track"], "frame": pv["frame"],
            "direction": "ascending and descending" if two else pv["label"], "pixel_m": 80}
    parts = {"see": sees.strip(), "physics": phys.strip(), "lband": lb.strip(), "people": people.strip()}
    parts.update(ev.get("story", {}))           # hand-written parts (facts: USGS + our numbers) replace the template
    if ev.get("area_name"):
        area["name"] = ev["area_name"]
    for sp_ in ev.get("extra_spots", []):
        spots.append(sp_)
    story = {"story": parts, "spots": spots}
    d = os.path.join(ROOT, "areas", ev["id"])
    os.makedirs(d, exist_ok=True)
    json.dump(area, open(os.path.join(d, "area.json"), "w"), indent=2)
    json.dump(story, open(os.path.join(d, "story.json"), "w"), indent=2)
    print("wrote", d)


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "list":
        for e in load_events():
            print(e["id"], e["mag"], e.get("verdict"), list(e["views"]))
    elif cmd in ("jobs", "launch", "fetch"):
        ev = get_event(argv[2]); only = argv[3] if len(argv) > 3 else None
        for k in views_of(ev, only):
            if cmd == "jobs":
                p = os.path.join(proc_dir(ev), f"job_{k}.json")
                os.makedirs(proc_dir(ev), exist_ok=True)
                json.dump(job_for(ev, k), open(p, "w"), indent=1); print("wrote", p)
            else:
                (launch if cmd == "launch" else fetch)(ev, k)
    elif cmd == "queue":  # queue EVENT:VIEW ...
        b = 0
        if argv[2].startswith("batch="):
            b, argv = int(argv[2][6:]), argv[:2] + argv[3:]
        queue([(get_event(a.split(":")[0]), a.split(":")[1]) for a in argv[2:]], batch=b)
    elif cmd == "run":
        run(get_event(argv[2]), argv[3] if len(argv) > 3 else None)
    elif cmd == "decompose":
        ev = get_event(argv[2]); print(json.dumps(decomposition(ev), indent=1))
    elif cmd == "export":
        ev = get_event(argv[2])
        export_products(ev)
        write_area(ev, json.load(open(os.path.join(DOCS, "results.json")))[ev["id"]])
    elif cmd == "overview":
        overview()
    else:
        print(__doc__)


def overview():
    """All clear events side by side, same fringe interval (primary view of each)."""
    plt = _plt()
    evs = [e for e in load_events() if e.get("verdict") == "clear"]
    fig, axs = plt.subplots(1, len(evs), figsize=(4.2 * len(evs), 4.4), squeeze=False)
    for ax, ev in zip(axs[0], evs):
        k = ev.get("primary_view", list(ev["views"])[0])
        z = np.load(os.path.join(view_dir(ev, k), "products_cm.npz"))
        ex, ey = float(z["ex"]), float(z["ey"])
        ext = ((z["x"][0] - ex) / 1e3, (z["x"][-1] - ex) / 1e3, (z["y"][-1] - ey) / 1e3, (z["y"][0] - ey) / 1e3)
        ax.imshow(z["coh"], extent=ext, cmap="gray", vmin=0, vmax=1)
        fr = rewrap(z["los_cm"], 10.0)
        ax.imshow(np.ma.masked_invalid(np.where(np.isfinite(z["los_cm"]), fr, np.nan)), extent=ext, cmap="twilight", vmin=0, vmax=1, alpha=0.9, interpolation="nearest")
        ax.plot(0, 0, marker="*", ms=12, mfc="yellow", mec="k")
        ax.set_title(f"M{ev['mag']} {ev['id'].split('-')[0].title()}, {ev['time'][:10]}\n{ev['views'][k]['label']}, pair {ev['views'][k]['co']['pair']}", fontsize=8)
        ax.set_xlabel("km east of epicentre", fontsize=8)
    fig.suptitle("NISAR LOS displacement, one colour cycle = 10 cm in every panel", fontsize=10)
    _save({"id": "", "legacy_flat": True}, fig, "overview.png", dpi=58)


if __name__ == "__main__":
    main(sys.argv)
