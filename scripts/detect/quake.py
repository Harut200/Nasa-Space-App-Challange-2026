"""quake.py -- did NISAR (L2 GUNW) capture the ground displacement of an earthquake?

    .venv/bin/python -m scripts.detect.quake fetch     # copy reduced server windows (needs env SERVER, SSH_KEY)
    .venv/bin/python -m scripts.detect.quake run       # process, figures, results.json
    .venv/bin/python -m pytest -q tests/test_quake.py

Event: USGS us6000u43g, M7.0 normal-faulting, 22 Oct 2026, Tibetan Plateau. Co-seismic pair: track 112 frame 18
ascending, 19 Oct -> 31 Oct 2026 (NISAR_L2_UR_GUNW, urgent-response, PROVISIONAL). Control pair on the same
track and frame, 7 -> 19 Oct (before the event). Only pure numpy maths here (no torch).
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
EVENT_ID = "tibet-m70-2026"
PROC = os.path.join(ROOT, "data", "processed", f"{EVENT_ID}-insar")
C_LIGHT = 299792458.0
EPI = (93.7837, 32.3376)       # USGS lon, lat
WIN = [93.0, 31.7, 94.6, 33.0]  # extracted window lon/lat


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


# ============================================================ data
SERVER = os.environ.get("SERVER", "")
_KEY = os.path.expanduser(os.environ.get("SSH_KEY", ""))
REMOTE = "~/work2/out/quake_tibet/tibet/small2"


def fetch():
    os.makedirs(PROC, exist_ok=True)
    cmd = ["scp", "-q", "-o", "IdentitiesOnly=yes", "-i", _KEY, "-o", "BatchMode=yes",
           f"{SERVER}:{REMOTE}/GUNW_*.npz", PROC + "/"]
    print(" ".join(cmd[:4]), "...", subprocess.run(cmd).returncode)


def load_pair(pair):
    z = np.load(os.path.join(PROC, f"GUNW_{pair}.npz"), allow_pickle=False)
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
CO, CT = "20261019_20261031", "20261007_20261019"   # co-seismic pair, pre-event control pair
GMIN = 0.35
R_EXCL_KM = 60.0                                     # far field = outside this disc around the epicentre


def epi_xy(epsg):
    from rasterio.warp import transform
    a, b = transform("EPSG:4326", f"EPSG:{epsg}", [EPI[0]], [EPI[1]])
    return a[0], b[0]


def stripe_line(P, ex, ey):
    """The co-seismic pair has a straight low-coherence band SW-NE south of the epicentre across which the
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


def process(P, north, use_iono=False, r_km=R_EXCL_KM, gmin=GMIN, order=1):
    """LOS displacement [cm, + toward satellite], referenced and de-ramped on the far field only."""
    ex, ey = epi_xy(P["epsg"])
    phi = P["unwrappedPhase"].astype(np.float64)
    if use_iono:
        phi = phi - P["ionospherePhaseScreen"]
    ok = np.isfinite(phi) & (P["coherenceMagnitude"] >= gmin) & (P["connectedComponents"] == 1) & north
    d = phase_to_los_cm(phi, P["wavelength"])
    d[~ok] = np.nan
    far = ok & ~disc_mask(P["X"], P["Y"], ex, ey, r_km * 1e3)
    res, coef = remove_ramp_far(d, P["X"], P["Y"], far, order)
    res -= np.nanmedian(res[far])
    res[~ok] = np.nan
    return res, ok, far, coef


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def _save(fig, name, dpi=100):
    os.makedirs(DOCS, exist_ok=True)
    p = os.path.join(DOCS, name)
    fig.savefig(p, dpi=dpi, bbox_inches="tight")
    print("wrote", p, os.path.getsize(p) // 1024, "KB")


def run():
    plt = _plt()
    from matplotlib.colors import TwoSlopeNorm
    Pc, Pt = load_pair(CO), load_pair(CT)
    ex, ey = epi_xy(Pc["epsg"])
    a, b = stripe_line(Pc, ex, ey)
    def north_mask(P):  # north of the artefact band (+16 km), minus the SW corner where the band's edge still leaks
        m = P["Y"] > (a + b * P["X"] + 16e3)
        return m & ~((P["X"] < ex - 55e3) & (P["Y"] < ey - 25e3))
    north, north_t = north_mask(Pc), north_mask(Pt)  # same geometry for the control (like for like)
    out = {"event": "USGS us6000u43g M7.0 22 Oct 2026 14:33:40 UTC, 32.338N 93.784E, depth 17-18 km",
           "coseismic_pair": CO, "control_pair": CT, "wavelength_m": Pc["wavelength"],
           "stripe_line": {"intercept_m": a, "slope": b}}
    km = lambda P: ((P["x"][0] - ex) / 1e3, (P["x"][-1] - ex) / 1e3, (P["y"][-1] - ey) / 1e3, (P["y"][0] - ey) / 1e3)

    # ---- main results (no ionosphere screen) and variants
    var = {}
    for name, kw in {"main_noiono_R60": {}, "iono_R60": {"use_iono": True}, "noiono_R45": {"r_km": 45},
                     "noiono_R75": {"r_km": 75}, "noiono_quadric_R60": {"order": 2}, "noiono_coh05_R60": {"gmin": 0.5}}.items():
        r, ok, far, coef = process(Pc, north, **kw)
        rt, okt, fart, _ = process(Pt, north_t, **kw)
        near = ok & disc_mask(Pc["X"], Pc["Y"], ex, ey, 45e3)
        v = r[near]
        lo, hi = np.nanpercentile(v, [0.5, 99.5])
        var[name] = {"p2p_cm_0.5_99.5pct": float(hi - lo), "min_cm": float(np.nanmin(v)), "max_cm": float(np.nanmax(v)),
                     "p0.5_cm": float(lo), "p99.5_cm": float(hi),
                     "far_std_cm": float(np.nanstd(r[far])), "control_far_std_cm": float(np.nanstd(rt[fart])),
                     "control_p2p_cm_0.5_99.5pct": float(np.diff(np.nanpercentile(rt[okt & disc_mask(Pt["X"], Pt["Y"], ex, ey, 45e3)], [0.5, 99.5]))[0]),
                     "control_max_abs_cm": float(np.nanmax(np.abs(rt[okt]))), "far_pixels": int(far.sum())}
    out["variants"] = var
    m = var["main_noiono_R60"]
    out["snr_peak_over_control_std"] = max(abs(m["p0.5_cm"]), abs(m["p99.5_cm"])) / m["control_far_std_cm"]
    r, ok, far, coef = process(Pc, north)
    rt, okt, fart, _ = process(Pt, north_t)
    rti, okti, farti, _ = process(Pt, north_t, use_iono=True)
    from scipy.ndimage import uniform_filter

    def local_noise(rr, ff):  # std of the map minus its own 10 km running mean, far field only (NaN-aware)
        v = np.where(np.isfinite(rr), rr, 0.0); w = np.isfinite(rr).astype(float)
        sm = uniform_filter(v, 125) / np.maximum(uniform_filter(w, 125), 1e-6)
        return float(np.nanstd((rr - sm)[ff & (uniform_filter(w, 125) > 0.6)]))
    out["local_noise_std_cm_coseismic_far"] = local_noise(r, far)
    out["local_noise_std_cm_control_far"] = local_noise(rt, fart)
    out["control_with_iono_far_std_cm"] = float(np.nanstd(rti[farti]))
    out["control_without_iono_far_std_cm"] = float(np.nanstd(rt[fart]))
    thr = 3 * m["control_far_std_cm"]
    sig = (np.abs(np.nan_to_num(r)) > max(thr, 2.0)) & ok
    pix = (Pc["x"][1] - Pc["x"][0]) ** 2
    out["deformed_area_km2_absLOS_gt_%.1fcm" % max(thr, 2.0)] = float(sig.sum() * abs(pix) / 1e6)
    # extent of > 5 cm region, north part only (clipped by the window and the stripe)
    s5 = (np.abs(np.nan_to_num(r)) > 5) & ok
    yy, xx = np.nonzero(s5)
    out["extent_gt5cm_km_EW_NS"] = [float((xx.max() - xx.min()) * 80 / 1e3), float((yy.max() - yy.min()) * 80 / 1e3)]
    out["masked_fraction_within_15km_of_epicentre"] = float(1 - ok[disc_mask(Pc["X"], Pc["Y"], ex, ey, 15e3)].mean())
    out["north_fraction_of_window"] = float(north.mean())
    # iono screen leakage diagnostic: screen range inside 30 km of the epicentre vs outside, both pairs
    for tag, P in (("coseismic", Pc), ("control", Pt)):
        s = P["ionospherePhaseScreen"]; d30 = disc_mask(P["X"], P["Y"], ex, ey, 30e3)
        out[f"iono_screen_{tag}_rad_range_within30km"] = [float(np.nanmin(s[d30])), float(np.nanmax(s[d30]))]
        out[f"iono_screen_{tag}_rad_std_outside60km"] = float(np.nanstd(s[~disc_mask(P['X'], P['Y'], ex, ey, 60e3)]))
    out["los_unit_vector_EN_mean"] = [float(np.nanmean(Pc["cube_losUnitVectorX"])), float(np.nanmean(Pc["cube_losUnitVectorY"]))]
    out["incidence_deg_mean"] = float(np.nanmean(Pc["cube_incidenceAngle"]))

    # ---- profile (azimuth AZ, swath 5 km)
    AZ = 130.0  # perpendicular to USGS nodal plane 2 strike (40 deg); the sign check is in the doc
    t = np.linspace(-70e3, 70e3, 141)
    ux, uy = math.sin(math.radians(AZ)), math.cos(math.radians(AZ))
    def prof(rr):
        vals = []
        for tt in t:
            cx, cy = ex + tt * ux, ey + tt * uy
            m_ = disc_mask(Pc["X"], Pc["Y"], cx, cy, 2.5e3)
            vals.append(np.nanmedian(rr[m_]) if np.isfinite(rr[m_]).sum() > 20 else np.nan)
        return np.array(vals)
    pr_c, pr_t = prof(r), prof(rt)
    # ---- figures
    ext = km(Pc)
    coh = Pc["coherenceMagnitude"]
    cmap = plt.get_cmap("RdBu").copy(); cmap.set_bad("none")
    lim = 40.0
    norm = TwoSlopeNorm(vmin=-lim, vcenter=0, vmax=lim)
    # (a) fringes
    fig, ax = plt.subplots(figsize=(7.2, 6.4))
    ax.imshow(coh, extent=ext, cmap="gray", vmin=0, vmax=1)
    fr = rewrap(np.where(ok, r, np.nan), 10.0)
    im = ax.imshow(np.ma.masked_invalid(fr), extent=ext, cmap="twilight", vmin=0, vmax=1, alpha=0.9, interpolation="nearest")
    ax.plot(0, 0, marker="*", ms=16, mfc="yellow", mec="k"); ax.set_xlabel("km east of epicentre"); ax.set_ylabel("km north of epicentre")
    ax.set_title("NISAR 19-31 Oct 2026, LOS displacement re-wrapped: one colour cycle = 10 cm\n(masked south of the artefact band; grey = coherence where phase is unusable)", fontsize=9)
    cb = plt.colorbar(im, ax=ax, shrink=0.8); cb.set_label("fraction of 10 cm cycle")
    _save(fig, "fig_a_fringes.png", dpi=80); plt.close(fig)
    # (b) LOS displacement
    fig, ax = plt.subplots(figsize=(7.2, 6.4))
    ax.imshow(coh, extent=ext, cmap="gray", vmin=0, vmax=1)
    im = ax.imshow(np.ma.masked_invalid(r), extent=ext, cmap=cmap, norm=norm, interpolation="nearest")
    ax.plot(0, 0, marker="*", ms=16, mfc="yellow", mec="k")
    ax.plot(t * ux / 1e3, t * uy / 1e3, "k--", lw=0.8)
    ax.set_xlabel("km east of epicentre"); ax.set_ylabel("km north of epicentre")
    ax.set_title("Line-of-sight displacement, 19-31 Oct 2026 (cm; + = toward satellite, sign unconfirmed)\nramp removed on far field (> 60 km), no ionosphere screen applied", fontsize=9)
    cb = plt.colorbar(im, ax=ax, shrink=0.8); cb.set_label("LOS displacement (cm)")
    _save(fig, "fig_b_los_cm.png"); plt.close(fig)
    # (c) control
    fig, ax = plt.subplots(figsize=(7.2, 6.4))
    ax.imshow(Pt["coherenceMagnitude"], extent=km(Pt), cmap="gray", vmin=0, vmax=1)
    im = ax.imshow(np.ma.masked_invalid(rt), extent=km(Pt), cmap=cmap, norm=norm, interpolation="nearest")
    ax.plot(0, 0, marker="*", ms=16, mfc="yellow", mec="k")
    ax.set_xlabel("km east of epicentre"); ax.set_ylabel("km north of epicentre")
    ax.set_title("Control: same track and frame, 7-19 Oct 2026 (before the event), same processing and colour scale\nfar-field std %.2f cm" % np.nanstd(rt[fart]), fontsize=9)
    cb = plt.colorbar(im, ax=ax, shrink=0.8); cb.set_label("LOS displacement (cm)")
    _save(fig, "fig_c_control_cm.png"); plt.close(fig)
    # (d) profile
    fig, ax = plt.subplots(figsize=(7.2, 3.8))
    ax.plot(t / 1e3, pr_c, "C3", label="co-seismic 19-31 Oct")
    ax.plot(t / 1e3, pr_t, "C0", label="control 7-19 Oct")
    ax.axvline(0, color="k", lw=0.6); ax.axhline(0, color="k", lw=0.6)
    ax.set_xlabel("km along azimuth 130 deg (SE +) through the epicentre, 5 km median"); ax.set_ylabel("LOS displacement (cm)")
    ax.legend(); ax.set_title("Profile through the epicentre", fontsize=9)
    _save(fig, "fig_d_profile.png"); plt.close(fig)
    # (e) ionosphere screen leakage
    fig, axs = plt.subplots(1, 2, figsize=(11, 4.6))
    for a_, P, ttl in ((axs[0], Pc, "co-seismic pair"), (axs[1], Pt, "control pair")):
        im = a_.imshow(P["ionospherePhaseScreen"], extent=km(P), cmap="viridis", vmin=-22, vmax=2)
        a_.plot(0, 0, marker="*", ms=12, mfc="yellow", mec="k"); a_.set_title("ionosphere phase screen in the product, " + ttl, fontsize=9)
        plt.colorbar(im, ax=a_, shrink=0.8).set_label("rad")
    _save(fig, "fig_e_iono_screen.png"); plt.close(fig)
    out["profile_azimuth_deg"] = AZ
    out["profile_coseismic_cm"] = {"km": (t[::10] / 1e3).tolist(), "cm": [None if not np.isfinite(v) else round(float(v), 1) for v in pr_c[::10]]}
    # arrays for stage 3
    np.savez_compressed(os.path.join(PROC, "products_cm.npz"), los_cm=np.where(np.isfinite(r), r, np.nan).astype(np.float32),
                        coh=coh.astype(np.float32), x=Pc["x"], y=Pc["y"], epsg=Pc["epsg"], ex=ex, ey=ey)
    with open(os.path.join(DOCS, "results.json"), "w") as f:
        json.dump(out, f, indent=1)
    print(json.dumps({k: v for k, v in out.items() if k not in ("profile_coseismic_cm",)}, indent=1))


# ============================================================ stage 3: static app layers (contract B)
APP = os.path.join(ROOT, "data", "processed", "tibet-m70-2026", "products")
APP_BBOX = (93.0, 31.7, 94.6, 33.0)
LOS_LIM = 40.0
FRINGE_CM = 10.0


def _hex(c):
    return "#%02x%02x%02x" % tuple(int(round(255 * v)) for v in c[:3])


def export_products():
    """Three static RGBA GeoTIFFs (EPSG:4326, north up, uint8) + layers.json + timeseries.json."""
    import rasterio
    from rasterio.transform import from_bounds
    from rasterio.warp import Resampling, reproject
    import matplotlib
    cm = matplotlib.colormaps
    z = np.load(os.path.join(PROC, "products_cm.npz"))
    r, coh, x, y, epsg = z["los_cm"], z["coh"], z["x"], z["y"], int(z["epsg"])
    dx = float(x[1] - x[0]); dy = float(y[1] - y[0])
    src_t = rasterio.Affine(dx, 0, x[0] - dx / 2, 0, dy, y[0] - dy / 2)
    W, S, E, N = APP_BBOX
    w, h = 1000, 813
    dst_t = from_bounds(W, S, E, N, w, h)

    def rgba(v, cmap, vmin, vmax, finite):
        t = np.clip((v - vmin) / (vmax - vmin), 0, 1)
        c = (cm[cmap](np.nan_to_num(t)) * 255).astype(np.uint8)
        c[..., 3] = np.where(finite, 255, 0)
        return c

    layers = {
        "los_cm": rgba(r, "RdBu", -LOS_LIM, LOS_LIM, np.isfinite(r)),
        "fringes": rgba(rewrap(np.nan_to_num(r), FRINGE_CM), "twilight", 0, 1, np.isfinite(r)),
        "coherence": rgba(coh, "gray", 0, 1, np.isfinite(coh)),
    }
    os.makedirs(APP, exist_ok=True)
    for name, arr in layers.items():
        dst = np.zeros((4, h, w), np.uint8)
        for b in range(4):
            reproject(np.ascontiguousarray(arr[..., b]), dst[b], src_transform=src_t, src_crs=f"EPSG:{epsg}",
                      dst_transform=dst_t, dst_crs="EPSG:4326", resampling=Resampling.nearest)
        with rasterio.open(os.path.join(APP, name + ".tif"), "w", driver="GTiff", height=h, width=w, count=4,
                           dtype="uint8", crs="EPSG:4326", transform=dst_t, compress="deflate",
                           photometric="RGB", alpha="YES") as f:
            f.write(dst)
        print("wrote", name, os.path.getsize(os.path.join(APP, name + ".tif")) // 1024, "KB")
    tw = [_hex(cm["twilight"](v)) for v in np.linspace(0, 1, 9)]
    L = [
        {"id": "los_cm", "label": "Ground motion along the line of sight (cm)", "dated": False,
         "legend": {"type": "diverging", "unit": "cm", "min": -LOS_LIM, "max": LOS_LIM,
                    "colors": [_hex(cm["RdBu"](0.0)), "#f7f7f7", _hex(cm["RdBu"](1.0))],
                    "labels": ["red: ground moved away from the satellite", "blue: ground moved toward the satellite (sign convention not confirmed by the product guide)"]}},
        {"id": "fringes", "label": "Fringes (one colour cycle = 10 cm)", "dated": False,
         "legend": {"type": "ramp", "unit": "one colour cycle = 10 cm of line-of-sight change", "min": 0, "max": FRINGE_CM,
                    "colors": tw}},
        {"id": "coherence", "label": "Radar coherence (how comparable the two passes are)", "dated": False,
         "legend": {"type": "ramp", "unit": "coherence 0-1", "min": 0, "max": 1, "colors": ["#000000", "#ffffff"]}},
    ]
    with open(os.path.join(APP, "layers.json"), "w") as f:
        json.dump(L, f, indent=2)
    ts = {"dates": ["2026-10-31"], "unit": "cm", "series": [], "facts": []}
    with open(os.path.join(APP, "timeseries.json"), "w") as f:
        json.dump(ts, f, indent=2)
    with open(os.path.join(APP, "source.json"), "w") as f:
        json.dump({"event": "USGS us6000u43g", "pair": CO, "track": 112, "frame": 18, "direction": "ascending",
                   "product": "NISAR_L2_UR_GUNW (urgent response, PROVISIONAL)", "script": "scripts/detect/quake.py"}, f, indent=2)


def main(argv):
    if len(argv) > 1 and argv[1] == "fetch":
        fetch()
    elif len(argv) > 1 and argv[1] == "run":
        run()
    elif len(argv) > 1 and argv[1] == "export":
        export_products()
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
