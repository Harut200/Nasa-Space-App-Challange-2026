"""NISAR interferometric (GUNW) and soil-moisture (SME2) analysis for the Ararat Valley, Lake Sevan
(control) and Tonle Sap.

    .venv/bin/python -m scripts.detect.insar fetch        # copy server windows -> data/processed/<area>-insar|sm
    .venv/bin/python -m scripts.detect.insar coherence    # Ararat coherence change + land-type series
    .venv/bin/python -m scripts.detect.insar motion       # Ararat displacement, noise, stability (+ Sevan control)
    .venv/bin/python -m scripts.detect.insar sm           # SME2 maps and series
    .venv/bin/python -m scripts.detect.insar tonle        # Tonle Sap coherence vs amplitude flood map
    .venv/bin/python -m scripts.detect.insar all

Inputs are the npz windows written by scripts/ingest/remote_products.py (one per product and window).
Outputs: figures + results.json in docs/eda/insar/. Method and numbers: docs/eda/insar.md.

Conventions (see docs/eda/insar.md):
  phase phi [rad] of a GUNW pair (reference t1, secondary t2) is positive for a range INCREASE
  (ISCE3 interferogram = ref * conj(sec)); line-of-sight displacement d = -lambda/(4 pi) * phi is
  positive TOWARD the satellite (uplift-like). Ionosphere: phi_corr = phi - phi_iono.
"""
import glob, json, math, os, sys

import numpy as np
import torch

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOCS = os.path.join(ROOT, "docs", "eda", "insar")
PROC = os.path.join(ROOT, "data", "processed")
C_LIGHT = 299792458.0


# ============================================================ core maths (unit-tested)
def phase_to_los(phi, wavelength):
    """Unwrapped phase [rad] -> LOS displacement [m], positive toward the satellite."""
    return -wavelength / (4 * math.pi) * phi


def design(x, y, order=1, extra=None):
    """Columns 1, x, y (, x^2, xy, y^2) (+ extra columns, e.g. height) for a polynomial surface."""
    cols = [torch.ones_like(x), x, y]
    if order >= 2:
        cols += [x * x, x * y, y * y]
    if extra is not None:
        cols += list(extra)
    return torch.stack(cols, -1)


def fit_surface(z, x, y, mask, order=1, extra=None):
    """Least-squares polynomial surface (plus optional extra regressors) fitted to z over `mask`,
    evaluated everywhere. x, y are normalised internally. Returns (model, coefficients)."""
    z, x, y = [torch.as_tensor(a, dtype=torch.float64) for a in (z, x, y)]
    m = torch.as_tensor(mask, dtype=torch.bool) & torch.isfinite(z)
    xs, ys = (x - x[m].mean()) / 1e4, (y - y[m].mean()) / 1e4
    ex = None
    if extra is not None:
        ex = []
        for e in extra:
            e = torch.as_tensor(e, dtype=torch.float64)
            ex.append((e - e[m].mean()) / 1e3)
    A = design(xs, ys, order, ex)
    coef = torch.linalg.lstsq(A[m], z[m].unsqueeze(-1)).solution.squeeze(-1)
    return (A @ coef), coef


def deramp(z, x, y, stable, order=1, extra=None):
    model, coef = fit_surface(z, x, y, stable, order, extra)
    return torch.as_tensor(z, dtype=torch.float64) - model, coef


def stack_rate(disp, dt_days, valid=None):
    """Classic stacking: rate = sum_k d_k / sum_k dt_k per pixel (pairs with NaN are skipped per
    pixel). disp: (K, H, W). Returns rate [unit/day] and the summed time span per pixel."""
    d = torch.as_tensor(disp, dtype=torch.float64)
    t = torch.as_tensor(dt_days, dtype=torch.float64).view(-1, *([1] * (d.dim() - 1))).expand_as(d)
    ok = torch.isfinite(d) if valid is None else (torch.as_tensor(valid, dtype=torch.bool) & torch.isfinite(d))
    s = torch.where(ok, d, torch.zeros_like(d)).sum(0)
    T = torch.where(ok, t, torch.zeros_like(t)).sum(0)
    return torch.where(T > 0, s / T.clamp(min=1e-9), torch.full_like(s, float("nan"))), T


def chain_timeseries(disp, dates_days):
    """Sequential pairs (d_k = x(t_{k+1}) - x(t_k)) -> cumulative series x(t_0..t_K), x(t_0)=0."""
    d = torch.as_tensor(disp, dtype=torch.float64)
    z = torch.zeros_like(d[:1])
    return torch.cat([z, torch.cumsum(d, 0)], 0)


def linear_rate(series, t_days):
    """Least-squares slope of a (N, ...) series against time [unit/day] and its formal 1-sigma
    from the residual scatter."""
    s = torch.as_tensor(series, dtype=torch.float64)
    t = torch.as_tensor(t_days, dtype=torch.float64)
    tc = t - t.mean()
    shape = [-1] + [1] * (s.dim() - 1)
    sc = s - s.mean(0, keepdim=True)
    slope = (tc.view(shape) * sc).sum(0) / (tc ** 2).sum()
    res = sc - slope * tc.view(shape)
    n = len(t)
    sig = torch.sqrt((res ** 2).sum(0) / max(n - 2, 1) / (tc ** 2).sum())
    return slope, sig


def coherence_change(coh, never=0.35, drop=0.25, low=0.45):
    """coh: (K, H, W) coherence per pair. Returns dict of maps:
       perm   - permanently incoherent (90th percentile over pairs < `never`: water, shadow, dense forest)
       lost   - (K, H, W) bool: pair k fell `drop` below the pixel's own 90th percentile AND below `low`
       n_lost - number of pairs in which the pixel lost coherence (pixels not perm)
       ref    - the pixel's 90th-percentile coherence (its 'normal' coherent level)."""
    c = torch.as_tensor(coh, dtype=torch.float64)
    cf = torch.where(torch.isfinite(c), c, torch.full_like(c, float("nan")))
    ref = torch.nanquantile(cf, 0.9, dim=0)
    perm = ref < never
    lost = (cf < ref.unsqueeze(0) - drop) & (cf < low) & (~perm).unsqueeze(0)
    return {"perm": perm, "lost": lost, "n_lost": lost.sum(0), "ref": ref}


def region_series(maps, mask):
    """Mean of each (K, H, W) map over a boolean region (NaN-aware)."""
    m = torch.as_tensor(maps, dtype=torch.float64)
    r = torch.as_tensor(mask, dtype=torch.bool).unsqueeze(0).expand_as(m) & torch.isfinite(m)
    return torch.where(r, m, torch.zeros_like(m)).sum((1, 2)) / r.sum((1, 2)).clamp(min=1)


def signal_stability(disp, dt_days, target, refs, frac_max=0.6, sigma=None):
    """Robustness of a target-minus-reference rate.
    disp: (K, H, W) per-pair displacement; target: bool region; refs: list of bool reference regions.
    Returns dict: rate (first ref), rate per reference, leave-one-pair-out rates, the share of the
    summed signal carried by the single largest pair, and `robust` = signal not dominated by one pair,
    all leave-one-out rates have the same sign, all references agree in sign."""
    d = torch.as_tensor(disp, dtype=torch.float64)
    dt = torch.as_tensor(dt_days, dtype=torch.float64)
    K = d.shape[0]
    tgt = region_series(d, target)
    by_ref = []
    for r in refs:
        diff = tgt - region_series(d, r)
        by_ref.append(float(diff.sum() / dt.sum()))
    diff = tgt - region_series(d, refs[0])
    total = diff.sum()
    loo = [float((total - diff[k]) / (dt.sum() - dt[k])) for k in range(K)]
    share = float(diff.abs().max() / diff.abs().sum().clamp(min=1e-12))
    same_sign = lambda v: all(np.sign(x) == np.sign(v[0]) for x in v)
    robust = (share <= frac_max) and same_sign(loo) and same_sign(by_ref)
    if sigma is not None:
        robust = robust and abs(by_ref[0]) > 2 * sigma
    return {"rate": by_ref[0], "rate_by_ref": by_ref, "loo": loo, "max_pair_share": share,
            "per_pair": diff.tolist(), "robust": bool(robust)}


def noise_vs_distance(d, x, y, stable, ref_xy, bins):
    """Std of displacement over stable pixels in distance rings around the reference point."""
    d, x, y = [torch.as_tensor(a, dtype=torch.float64) for a in (d, x, y)]
    m = torch.as_tensor(stable, dtype=torch.bool) & torch.isfinite(d)
    r = torch.sqrt((x - ref_xy[0]) ** 2 + (y - ref_xy[1]) ** 2)
    out = []
    for a, b in zip(bins[:-1], bins[1:]):
        s = m & (r >= a) & (r < b)
        v = d[s]
        out.append({"r0_km": a / 1e3, "r1_km": b / 1e3, "n": int(s.sum()),
                    "std": float(v.std()) if v.numel() > 10 else float("nan"),
                    "rms": float(torch.sqrt((v ** 2).mean())) if v.numel() > 10 else float("nan")})
    return out


# ============================================================ data access
SSH = ["ssh", "-o", "IdentitiesOnly=yes", "-i", os.path.expanduser("~/.ssh/oracle_team"), "-o", "BatchMode=yes",
       "-o", "ConnectTimeout=15", "opc@151.145.86.139"]
SCP = ["scp", "-q", "-o", "IdentitiesOnly=yes", "-i", os.path.expanduser("~/.ssh/oracle_team")]
FETCH = {  # server folder -> (local area folder, local sub folder)
    "armenia_85_22/ararat": ("ararat-insar", "a085"), "armenia_85_22/sevan": ("sevan-insar", "a085"),
    "armenia_122_68/ararat": ("ararat-insar", "d122"), "tonle_83_8/tonle-sap": ("tonle-sap-insar", "a083"),
}


def fetch():
    """Copy npz windows from the server (GUNW_* -> <area>-insar, SME2_* -> <area>-sm)."""
    import subprocess
    for src, (dst, sub) in FETCH.items():
        src = src + "/small2"  # reduced copies (uint8 coherence, int16 phase, ...), see remote_products.reduce_npz2
        ls = subprocess.run(SSH + [f"ls ~/work2/out/{src}/ 2>/dev/null"], capture_output=True, text=True).stdout.split()
        for kind, area_dir in (("GUNW", dst), ("SME2", dst.replace("-insar", "-sm"))):
            files = [f for f in ls if f.startswith(kind) and f.endswith(".npz")]
            if not files:
                continue
            out = os.path.join(PROC, area_dir, sub)
            os.makedirs(out, exist_ok=True)
            need = [f for f in files if not os.path.exists(os.path.join(out, f))]
            if need:  # one remote path per argument (the SFTP-based scp does not expand {a,b})
                subprocess.run(SCP + [f"opc@151.145.86.139:work2/out/{src}/{f}" for f in need] + [out + "/"], check=False)
            got = [f for f in need if os.path.exists(os.path.join(out, f))]
            print(f"{src}: {kind} {len(files)} on server, {len(got)}/{len(need)} new copied -> {out}")


def _days(d):
    import datetime as dt
    return (dt.date(int(d[:4]), int(d[4:6]), int(d[6:8])) - dt.date(2026, 1, 1)).days


def _decode(z, k, shape=None):
    """Undo remote_products.reduce_npz2: uint8 coherence (255 = no data), int16 phase steps,
    float16 + offset, block-averaged smooth screens (interpolated back to `shape`)."""
    a = z[k]
    if shape is not None and a.ndim == 2 and k + "__fac" not in z.files and a.shape != tuple(shape) \
            and a.shape[0] >= shape[0] and a.shape[1] >= shape[1]:
        a = a[:shape[0], :shape[1]]  # subsampled layers (components, mask) vs block-averaged phase grid
    if k + "__step" in z.files:
        return np.where(a == -32768, np.nan, a.astype(np.float32) * np.float32(z[k + "__step"])
                        + np.float32(z[k + "__offset"]))
    if k + "__fac" in z.files and shape is not None and a.shape != tuple(shape):
        t = torch.as_tensor(np.nan_to_num(a, nan=float(np.nanmean(a))), dtype=torch.float32)[None, None]
        return torch.nn.functional.interpolate(t, size=tuple(shape), mode="bilinear", align_corners=False)[0, 0].numpy()
    if k + "__scale" in z.files:
        return np.where(a == 255, np.nan, a.astype(np.float32) / float(z[k + "__scale"]))
    if k + "__offset" in z.files:
        return a.astype(np.float32) + np.float32(z[k + "__offset"])
    return a


def load_gunw(area, sub="a085"):
    files = sorted(glob.glob(os.path.join(PROC, f"{area}-insar", sub, "GUNW_*.npz")))
    if not files:
        raise FileNotFoundError(f"no GUNW windows for {area}/{sub}; run `insar fetch`")
    P = {"pairs": [], "t1": [], "t2": []}
    stacks = {}
    for fp in files:
        z = np.load(fp, allow_pickle=False)
        a, b = os.path.basename(fp)[5:-4].split("_")
        P["pairs"].append((a, b)); P["t1"].append(_days(a)); P["t2"].append(_days(b))
        shape = (len(z["y"]), len(z["x"]))
        for k in [k for k in z.files if not k.endswith(("__scale", "__offset", "__step", "__fac"))]:
            if k in ("x", "y", "epsg", "x40", "y40", "pol", "url") or k.startswith("meta_"):
                P.setdefault(k, z[k][()] if z[k].ndim == 0 else z[k])
                continue
            if k.startswith("cube_"):
                if k in ("cube_x", "cube_y", "cube_h"):
                    P.setdefault(k, _decode(z, k))
                elif k[5:] in ("incidenceAngle", "losUnitVectorX", "losUnitVectorY", "perpendicularBaseline"):
                    P.setdefault(k, _decode(z, k))
                    if k[5:] == "perpendicularBaseline":
                        stacks.setdefault(k, []).append(_decode(z, k))
                else:
                    stacks.setdefault(k, []).append(_decode(z, k))
                continue
            stacks.setdefault(k, []).append(_decode(z, k, shape))
    for k, v in stacks.items():
        try:
            P[k] = np.stack(v)
        except ValueError:
            pass
    P["t1"], P["t2"] = np.array(P["t1"]), np.array(P["t2"])
    P["dt"] = P["t2"] - P["t1"]
    f0 = float(P.get("meta_centerFrequency", 1.239e9))
    P["wavelength"] = C_LIGHT / f0
    P["X"], P["Y"] = np.meshgrid(P["x"], P["y"])
    return P


def lonlat(X, Y, epsg):
    from rasterio.warp import transform
    lo, la = transform(f"EPSG:{int(epsg)}", "EPSG:4326", X.ravel().tolist(), Y.ravel().tolist())
    return np.array(lo).reshape(X.shape), np.array(la).reshape(X.shape)


def to_grid(src_path, x, y, epsg, band=1, resampling="bilinear", src_array=None, src_transform=None, src_crs=None):
    """Reproject a raster (file or array) onto a regular grid with centre coordinates x, y."""
    import rasterio
    from rasterio.warp import reproject, Resampling
    from rasterio.transform import Affine
    dx, dy = float(x[1] - x[0]), float(y[1] - y[0])
    dst_t = Affine(dx, 0, float(x[0]) - dx / 2, 0, dy, float(y[0]) - dy / 2)
    out = np.full((len(y), len(x)), np.nan, np.float32)
    if src_array is None:
        with rasterio.open(src_path) as r:
            src_array, src_transform, src_crs = r.read(band).astype(np.float32), r.transform, r.crs
            if r.nodata is not None:
                src_array[src_array == r.nodata] = np.nan
    reproject(src_array.astype(np.float32), out, src_transform=src_transform, src_crs=src_crs,
              dst_transform=dst_t, dst_crs=f"EPSG:{int(epsg)}", resampling=getattr(Resampling, resampling),
              src_nodata=np.nan, dst_nodata=np.nan)
    return out


def cube_at_height(cube, cube_x, cube_y, cube_h, X, Y, H):
    """Interpolate a (L, ny, nx) radar-grid cube to pixel positions X, Y and heights H (trilinear)."""
    c = torch.as_tensor(cube, dtype=torch.float64)
    L, ny, nx = c.shape
    fx = (torch.as_tensor(X, dtype=torch.float64) - float(cube_x[0])) / float(cube_x[1] - cube_x[0])
    fy = (torch.as_tensor(Y, dtype=torch.float64) - float(cube_y[0])) / float(cube_y[1] - cube_y[0])
    hh = np.asarray(cube_h, float)
    fh = (torch.as_tensor(np.nan_to_num(H, nan=float(np.nanmedian(H))), dtype=torch.float64) - hh[0]) / (hh[1] - hh[0])
    # grid_sample wants normalised coords in [-1, 1] (align_corners=True)
    gx = 2 * fx / (nx - 1) - 1
    gy = 2 * fy / (ny - 1) - 1
    gz = 2 * fh.clamp(0, L - 1) / (L - 1) - 1
    grid = torch.stack([gx, gy, gz], -1).unsqueeze(0).unsqueeze(0)  # (1,1,H,W,3)
    v = torch.nn.functional.grid_sample(c[None, None], grid, mode="bilinear", align_corners=True,
                                        padding_mode="border")
    return v[0, 0, 0].numpy()


# ============================================================ shared helpers
def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 8, "axes.titlesize": 9, "figure.dpi": 110})
    return plt


def _save(fig, name, dpi=110):
    os.makedirs(DOCS, exist_ok=True)
    p = os.path.join(DOCS, name)
    fig.savefig(p, dpi=dpi, bbox_inches="tight")
    if os.path.getsize(p) > 400 * 1024:  # keep figures < 400 KB: palette-quantise
        from PIL import Image
        Image.open(p).convert("RGB").quantize(colors=128, method=Image.Quantize.MEDIANCUT).save(p, optimize=True)
    import matplotlib.pyplot as plt
    plt.close(fig)
    kb = os.path.getsize(p) / 1024
    print(f"  figure {name} {kb:.0f} KB")
    return p


def results_update(key, val):
    p = os.path.join(DOCS, "results.json")
    r = json.load(open(p)) if os.path.exists(p) else {}
    r[key] = val
    os.makedirs(DOCS, exist_ok=True)
    json.dump(r, open(p, "w"), indent=1, default=float)


def _lab(d):
    return f"{d[4:6]}-{d[6:8]}"


# Places named in the brief (lon, lat)
PLACES = {"Yerevan": (44.515, 40.180), "Masis": (44.43, 40.07), "Aknalich": (44.169, 40.144),
          "Ararat-Vedi fish farms": (44.60, 39.90), "Artashat": (44.55, 39.96), "Armavir": (44.03, 40.15),
          "Vedi": (44.73, 39.91)}


def box_mask(LON, LAT, w, s, e, n):
    return (LON >= w) & (LON <= e) & (LAT >= s) & (LAT <= n)


def ararat_regions(P, dem):
    """Land types picked by elevation + coordinates (no land-cover map; see docs):
    valley floor = DEM < 900 m (the irrigated Ararat plain, excluding Yerevan);
    Yerevan city = box around the centre; fish-farm cluster = box around 44.6E 39.9N;
    foothills/rock = DEM 1200-2200 m (dry slopes and bedrock at the valley margins)."""
    LON, LAT = P["LON"], P["LAT"]
    yer = box_mask(LON, LAT, 44.47, 40.15, 44.56, 40.21)
    fish = box_mask(LON, LAT, 44.53, 39.86, 44.68, 39.95)
    floor = (dem < 900) & ~yer & ~fish
    hills = (dem > 1200) & (dem < 2200)
    return {"valley floor (irrigated plain)": floor, "fish-farm cluster": fish,
            "Yerevan (city)": yer, "foothills / bedrock": hills}


def prepare(area, sub="a085"):
    P = load_gunw(area, sub)
    P["LON"], P["LAT"] = lonlat(P["X"], P["Y"], P["epsg"])
    P["dem"] = to_grid(os.path.join(ROOT, "data", "external", "dem", f"{area}_dem.tif"), P["x"], P["y"], P["epsg"])
    P["px_km2"] = abs(float(P["x"][1] - P["x"][0]) * float(P["y"][1] - P["y"][0])) / 1e6
    return P


def _extent(x, y):
    dx, dy = float(x[1] - x[0]), float(y[1] - y[0])
    return [x[0] / 1e3 - dx / 2e3, x[-1] / 1e3 + dx / 2e3, y[-1] / 1e3 + dy / 2e3, y[0] / 1e3 - dy / 2e3]


def _mark_places(ax, P, names=None, color="k"):
    from rasterio.warp import transform
    for n, (lo, la) in PLACES.items():
        if names and n not in names:
            continue
        xx, yy = transform("EPSG:4326", f"EPSG:{int(P['epsg'])}", [lo], [la])
        ax.plot(xx[0] / 1e3, yy[0] / 1e3, "o", ms=3, mfc="none", mec=color)
        ax.annotate(n, (xx[0] / 1e3, yy[0] / 1e3), xytext=(3, 3), textcoords="offset points", fontsize=6, color=color)


def pair_timeline():
    """Figure: which dates each GUNW pair connects, per track, with the instrument outage."""
    plt = _plt()
    import datetime as dt
    cat = os.path.join(DOCS, "catalogue.json")
    C = json.load(open(cat))
    fig, ax = plt.subplots(figsize=(7.5, 3.2))
    rows = [(k, v) for k, v in C["gunw_pairs"].items()]
    for i, (k, pairs) in enumerate(rows):
        for a, b in pairs:
            ta, tb = [dt.date(int(s[:4]), int(s[4:6]), int(s[6:8])) for s in (a, b)]
            ax.plot([ta, tb], [i, i], "-", lw=3, color="C0" if (tb - ta).days <= 12 else "C3", solid_capstyle="butt")
            ax.plot([ta, tb], [i, i], "|", color="k", ms=8)
    ax.axvspan(dt.date(2026, 7, 27), dt.date(2026, 8, 10), color="0.85", zorder=0)
    ax.text(dt.date(2026, 7, 28), len(rows) - 0.4, "instrument\noutage", fontsize=6, va="top")
    ax.set_yticks(range(len(rows)), [k for k, _ in rows], fontsize=6)
    ax.set_title("NISAR GUNW interferograms over our areas (PROVISIONAL): blue = 12-day pair, red = longer (bridging the outage)")
    ax.set_ylim(-0.7, len(rows) - 0.3)
    fig.autofmt_xdate()
    _save(fig, "fig1_pair_timeline.png")


def run_coherence():
    """Ararat Valley: coherence per pair, permanently incoherent vs became incoherent, land-type series,
    comparison with the amplitude change test. Sevan gets the same series as a control."""
    plt = _plt()
    P = prepare("ararat")
    K = len(P["pairs"])
    coh = P["coherenceMagnitude"].astype(np.float64)
    cc = coherence_change(coh)
    regs = ararat_regions(P, P["dem"])
    labels = [f"{_lab(a)}→{_lab(b)}" for a, b in P["pairs"]]
    out = {"pairs": labels, "px_km2": P["px_km2"], "regions": {}}
    for name, m in regs.items():
        s = region_series(coh, m)
        out["regions"][name] = {"km2": float(m.sum() * P["px_km2"]), "mean_coh": [round(float(v), 3) for v in s]}
    floor = regs["valley floor (irrigated plain)"]
    perm = cc["perm"].numpy()
    lost = cc["lost"].numpy()
    out["perm_incoherent_km2_floor"] = float((perm & floor).sum() * P["px_km2"])
    out["lost_km2_floor_per_pair"] = [round(float((lost[k] & floor).sum() * P["px_km2"]), 1) for k in range(K)]
    out["lost_ever_km2_floor"] = float(((lost.any(0)) & floor).sum() * P["px_km2"])
    out["floor_km2"] = float(floor.sum() * P["px_km2"])
    hills = regs["foothills / bedrock"]
    okc = np.isfinite(coh)
    out["floor_frac_coh_lt_0.3"] = [round(float((coh[k][floor & okc[k]] < 0.3).mean()), 3) for k in range(K)]
    out["hills_frac_coh_lt_0.3"] = [round(float((coh[k][hills & okc[k]] < 0.3).mean()), 3) for k in range(K)]
    out["floor_median_coh"] = [round(float(np.median(coh[k][floor & okc[k]])), 3) for k in range(K)]
    out["hills_median_coh"] = [round(float(np.median(coh[k][hills & okc[k]])), 3) for k in range(K)]
    # consecutive pairs: share of the floor that was coherent (>= 0.5) in pair k-1 and fell below 0.3 in pair k
    out["floor_frac_fell_0.5_to_0.3"] = [None] + [
        round(float(((coh[k - 1] >= 0.5) & (coh[k] < 0.3))[floor & okc[k] & okc[k - 1]].mean()), 3) for k in range(1, K)]
    out["hills_frac_fell_0.5_to_0.3"] = [None] + [
        round(float(((coh[k - 1] >= 0.5) & (coh[k] < 0.3))[hills & okc[k] & okc[k - 1]].mean()), 3) for k in range(1, K)]
    out["region_rule"] = ("valley floor = SRTM/Copernicus DEM < 900 m minus Yerevan and fish-farm boxes; foothills/bedrock = "
                          "DEM 1200-2200 m; Yerevan = box 44.47-44.56E 40.15-40.21N; fish farms = box 44.53-44.68E 39.86-39.95N")
    yer = regs["Yerevan (city)"]
    out["yerevan_lost_frac"] = float((lost.any(0) & yer).sum() / max(1, yer.sum()))
    # patch sizes of coherence loss on the valley floor (field-like?)
    from scipy import ndimage
    sizes = []
    for k in range(K):
        lab, n = ndimage.label(lost[k] & floor)
        if n:
            sizes += list(np.bincount(lab.ravel())[1:] * P["px_km2"] * 100)  # hectares
    sizes = np.array(sizes)
    out["loss_patch_ha"] = {"n": int(sizes.size), "median": float(np.median(sizes)) if sizes.size else None,
                            "p90": float(np.percentile(sizes, 90)) if sizes.size else None,
                            "share_area_in_patches_le_50ha": float(sizes[sizes <= 50].sum() / sizes.sum()) if sizes.size else None}
    # pixel-level temporal variability of coherence on the floor vs city
    sd = np.nanstd(coh, 0)
    out["temporal_std_coh"] = {n: round(float(np.nanmedian(sd[m])), 3) for n, m in regs.items()}
    # amplitude change test (preview GCOV, ~180 m, docs/eda/stats-change.md) on the same grid
    try:
        S = torch.load(os.path.join(PROC, "ararat", "stats", "products.pt"), weights_only=False)
        from rasterio.transform import from_bounds
        w, s_, e, n_ = S["bounds"]
        Hs, Ws = S["valid"].shape
        tr = from_bounds(w, s_, e, n_, Ws, Hs)
        amp_changed = to_grid(None, P["x"], P["y"], P["epsg"], src_array=np.asarray(S["changed_fdr"], np.float32),
                              src_transform=tr, src_crs="EPSG:4326", resampling="nearest") > 0.5
        amp_sp = to_grid(None, P["x"], P["y"], P["epsg"], src_array=np.asarray(S["changed_fdr_speckle"], np.float32),
                         src_transform=tr, src_crs="EPSG:4326", resampling="nearest") > 0.5
        out["amplitude_changed_km2_floor"] = float((amp_changed & floor).sum() * P["px_km2"])
        out["amplitude_changed_speckle_null_km2_floor"] = float((amp_sp & floor).sum() * P["px_km2"])
        out["overlap_coh_lost_and_amp_speckle_null_frac"] = float((amp_sp & lost.any(0) & floor).sum() / max(1, (lost.any(0) & floor).sum()))
    except Exception as ex:
        out["amplitude_error"] = str(ex)
    # first pair in which coherence was lost
    first = np.where(lost.any(0), lost.argmax(0), -1).astype(float)
    first[~np.isfinite(coh[0])] = np.nan
    # summer (first 3 pairs) vs autumn (last 3 pairs), per region; floor-specific = floor change minus hills change
    S_, A_ = list(range(0, 3)), list(range(K - 3, K))
    out["summer_vs_autumn_mean_coh"] = {n: [round(float(np.mean([r["mean_coh"][k] for k in S_])), 3),
                                            round(float(np.mean([r["mean_coh"][k] for k in A_])), 3)]
                                        for n, r in out["regions"].items()}
    # spatial structure of the change at 40 m: lag correlation of (autumn - summer) vs of a noise-only
    # difference (pair 1 - pair 0); excess correlation at 80-320 m lags = patches of field size
    if "coh40" in P:
        c40 = P["coh40"]

        def lagc(x, l):
            u, v = x[:, :-l].ravel(), x[:, l:].ravel()
            m = np.isfinite(u) & np.isfinite(v)
            return round(float(np.corrcoef(u[m], v[m])[0, 1]), 3)
        dA = np.nanmean(c40[A_], 0) - np.nanmean(c40[S_], 0)
        dN = c40[1] - c40[0]
        out["lagcorr_40m"] = {"lags_m": [40, 80, 160, 320, 640],
                              "autumn_minus_summer": [lagc(dA, l) for l in (1, 2, 4, 8, 16)],
                              "pair1_minus_pair0_noise": [lagc(dN, l) for l in (1, 2, 4, 8, 16)]}
    results_update("ararat_coherence", out)

    # ---- figure: summer vs autumn coherence, difference, and a 40 m zoom
    ext = _extent(P["x"], P["y"])
    S_, A_ = list(range(0, 3)), list(range(K - 3, K))
    cs, ca = np.nanmean(coh[S_], 0), np.nanmean(coh[A_], 0)
    lab_s = f"{labels[S_[0]][:5]}…{labels[S_[-1]][-5:]}"
    lab_a = f"{labels[A_[0]][:5]}…{labels[A_[-1]][-5:]}"
    fig, axs = plt.subplots(2, 2, figsize=(9.5, 8))
    for ax, img, t in ((axs[0, 0], cs, f"summer: mean of 3 pairs\n{lab_s}"),
                       (axs[0, 1], ca, f"autumn: mean of 3 pairs\n{lab_a}")):
        im = ax.imshow(img, vmin=0, vmax=1, cmap="magma", extent=ext, interpolation="nearest")
        ax.set_title(t)
        _mark_places(ax, P, ["Yerevan", "Masis", "Aknalich", "Ararat-Vedi fish farms", "Armavir", "Artashat"], color="w")
    fig.colorbar(im, ax=axs[0, :], shrink=0.8, label="coherence |γ| (80 m)")
    ax = axs[1, 0]
    im = ax.imshow(ca - cs, vmin=-0.4, vmax=0.4, cmap="RdBu", extent=ext, interpolation="nearest")
    ax.contour(np.where(np.isfinite(P["dem"]), P["dem"], 2000), levels=[900], colors="k", linewidths=0.4,
               extent=ext, origin="upper")
    _mark_places(ax, P, ["Yerevan", "Masis", "Aknalich", "Ararat-Vedi fish farms"])
    zoom = (44.33, 39.98, 44.50, 40.10)
    from rasterio.warp import transform as _tr
    zx, zy = _tr("EPSG:4326", f"EPSG:{int(P['epsg'])}", [zoom[0], zoom[2]], [zoom[1], zoom[3]])
    ax.plot([zx[0] / 1e3, zx[1] / 1e3, zx[1] / 1e3, zx[0] / 1e3, zx[0] / 1e3],
            [zy[0] / 1e3, zy[0] / 1e3, zy[1] / 1e3, zy[1] / 1e3, zy[0] / 1e3], "g-", lw=1)
    ax.set_title("autumn minus summer (blue = became coherent)\nblack line: 900 m contour = valley floor; green: zoom")
    fig.colorbar(im, ax=ax, shrink=0.8)
    ax = axs[1, 1]
    if "coh40" in P:
        c40 = P["coh40"]
        X40, Y40 = np.meshgrid(P["x40"], P["y40"])
        L40, A40 = lonlat(X40, Y40, P["epsg"])
        rr, cc_ = np.nonzero(box_mask(L40, A40, *zoom))
        r0, r1, c0, c1 = rr.min(), rr.max() + 1, cc_.min(), cc_.max() + 1
        d40 = np.nanmean(c40[A_, r0:r1, c0:c1], 0) - np.nanmean(c40[S_, r0:r1, c0:c1], 0)
        ax.imshow(d40, vmin=-0.4, vmax=0.4, cmap="RdBu", extent=_extent(P["x40"][c0:c1], P["y40"][r0:r1]),
                  interpolation="nearest")
        _mark_places(ax, P, ["Masis"])
        ax.set_title("zoom at 40 m (from the 20 m layer): autumn minus summer\nfield-sized blocks = fields that changed state")
    for a in axs.ravel():
        a.set_xlabel("UTM 38N east [km]", fontsize=6)
        a.tick_params(labelsize=6)
    _save(fig, "fig2_ararat_coherence_maps.png")

    # ---- figure: coherence series by land type (+ Sevan control)
    fig, ax = plt.subplots(figsize=(7.5, 3.6))
    mid = (P["t1"] + P["t2"]) / 2
    for name, r in out["regions"].items():
        ax.plot(mid, r["mean_coh"], "o-", label=f"{name} ({r['km2']:.0f} km²)")
    try:
        S2 = prepare("sevan")
        sv = S2["coherenceMagnitude"].astype(np.float64)
        land = (S2["dem"] > 1950) & np.isfinite(sv[0]) & (np.nanpercentile(sv, 90, axis=0) > 0.35)
        ax.plot((S2["t1"] + S2["t2"]) / 2, region_series(sv, land).numpy(), "s--", color="0.4",
                label="Sevan basin land (control)")
        out["sevan_land_mean_coh"] = [round(float(v), 3) for v in region_series(sv, land)]
        results_update("ararat_coherence", out)
    except Exception as ex:
        print("sevan control skipped:", ex)
    ax.axvspan(_days("20260727"), _days("20260810"), color="0.9")
    ax.axvspan(_days("20260615"), _days("20260731"), color="#f6e7c1", alpha=0.5, zorder=0)
    ax.text(_days("20260617"), 0.05, "wheat harvest (Jun–Jul)", fontsize=6)
    ax.set_xticks(mid, labels, rotation=45, fontsize=6)
    ax.set_ylabel("mean coherence")
    ax.set_ylim(0, 1)
    ax.legend(fontsize=6, loc="upper right")
    ax.set_title("Ararat Valley: mean 12-day coherence by land type (track 85 ascending, 80 m)")
    _save(fig, "fig3_coherence_series.png")
    return out


# ============================================================ ground motion
def disk(P, lon, lat, r_km):
    from rasterio.warp import transform
    xx, yy = transform("EPSG:4326", f"EPSG:{int(P['epsg'])}", [lon], [lat])
    return (P["X"] - xx[0]) ** 2 + (P["Y"] - yy[0]) ** 2 <= (r_km * 1e3) ** 2, (xx[0], yy[0])


def corrections(P, which=("iono",)):
    """Phase screens [rad] to subtract, per pair (K, H, W)."""
    K = len(P["pairs"])
    tot = np.zeros_like(P["unwrappedPhase"], dtype=np.float64)
    if "iono" in which and "ionospherePhaseScreen" in P:
        tot += np.nan_to_num(P["ionospherePhaseScreen"].astype(np.float64))
    for name, key in (("tropo", "cube_wetTroposphericPhaseScreen"), ("tropo", "cube_hydrostaticTroposphericPhaseScreen"),
                      ("set", "cube_slantRangeSolidEarthTidesPhase")):
        if name in which and key in P:
            for k in range(K):
                tot[k] += cube_at_height(P[key][k], P["cube_x"], P["cube_y"], P["cube_h"], P["X"], P["Y"], P["dem"])
    return tot


def motion_maps(P, gmin=0.4, corr=("iono",), ref="Yerevan", order=1, use_height=True, stable=None,
                target_mask=None, largest_cc=True):
    """Per-pair LOS displacement [mm, toward satellite +], referenced and de-ramped.
    Returns (disp (K,H,W) with NaN where masked, info)."""
    K = len(P["pairs"])
    phi = P["unwrappedPhase"].astype(np.float64) - corrections(P, corr)
    coh = P["coherenceMagnitude"]
    cc = P["connectedComponents"].astype(np.int64)
    disp = np.full(phi.shape, np.nan)
    info = {"kept_frac": [], "ramp_coef": [], "resid_std_stable_mm": []}
    for k in range(K):
        ok = np.isfinite(phi[k]) & (coh[k] >= gmin) & (cc[k] != 0) & (cc[k] != 65535)
        if largest_cc and ok.any():
            lab = np.bincount(cc[k][ok])
            ok &= cc[k] == lab.argmax()
        d = 1e3 * phase_to_los(phi[k], P["wavelength"])
        d[~ok] = np.nan
        st = ok & stable
        res, coef = deramp(d, P["X"], P["Y"], st, order=order, extra=[P["dem"]] if use_height else None)
        res = res.numpy()
        res[~ok] = np.nan
        rm = REFS[ref](P) & ok
        res -= np.nanmedian(res[rm]) if rm.sum() > 20 else np.nanmedian(res[st])
        disp[k] = res
        info["kept_frac"].append(float(ok.sum() / np.isfinite(phi[k]).sum()))
        info["ramp_coef"].append([float(c) for c in coef])
        info["resid_std_stable_mm"].append(float(np.nanstd(res[st])))
    return disp, info


REFS = {  # reference areas (bool masks); all on rock / town at the valley margin
    "Yerevan": lambda P: box_mask(P["LON"], P["LAT"], 44.47, 40.15, 44.56, 40.21),
    "N hills (above Yerevan)": lambda P: box_mask(P["LON"], P["LAT"], 44.45, 40.22, 44.60, 40.30) & (P["dem"] > 1100),
    "SE hills (Urtsadzor)": lambda P: box_mask(P["LON"], P["LAT"], 44.68, 39.85, 44.80, 40.05) & (P["dem"] > 1100),
}
TARGETS = {"Masis": (44.43, 40.07), "Aknalich": (44.169, 40.144), "Ararat-Vedi fish farms": (44.60, 39.90),
           "Artashat": (44.55, 39.96)}


def run_motion(gmin_list=(0.3, 0.4, 0.5), sub="a085", area="ararat", control=False):
    plt = _plt()
    P = prepare(area, sub)
    K = len(P["pairs"])
    T = float(P["t2"].max() - P["t1"].min())
    inc = None
    if "cube_incidenceAngle" in P:
        inc = cube_at_height(P["cube_incidenceAngle"], P["cube_x"], P["cube_y"], P["cube_h"], P["X"], P["Y"], P["dem"])
    if area == "ararat":
        floor = (P["dem"] < 900)
        stable = (P["dem"] > 1000) | REFS["Yerevan"](P)
        refs = ["Yerevan", "N hills (above Yerevan)", "SE hills (Urtsadzor)"]
        targets = {n: disk(P, lo, la, 3.0)[0] & floor for n, (lo, la) in TARGETS.items()}
        ref_xy = disk(P, 44.515, 40.18, 0.1)[1]
    else:  # Sevan control: land around the lake, reference = Gavar town, pseudo-targets on flat shore land
        c90 = np.nanpercentile(P["coherenceMagnitude"], 90, axis=0)
        lake = ((P["dem"] > 1905) & (P["dem"] < 1920) & (c90 < 0.3)) | ~np.isfinite(c90)
        stable = ~lake & (P["dem"] > 1925)
        REFS["Gavar"] = lambda Q: box_mask(Q["LON"], Q["LAT"], 45.10, 40.33, 45.16, 40.38)
        REFS["Sevan town"] = lambda Q: box_mask(Q["LON"], Q["LAT"], 44.93, 40.53, 44.98, 40.57)
        REFS["Martuni"] = lambda Q: box_mask(Q["LON"], Q["LAT"], 45.28, 40.12, 45.33, 40.16)
        refs = ["Gavar", "Sevan town", "Martuni"]
        targets = {"Masrik plain (pseudo-target)": disk(P, 45.55, 40.22, 3.0)[0] & ~lake,
                   "Gavaraget delta (pseudo-target)": disk(P, 45.17, 40.30, 3.0)[0] & ~lake}
        ref_xy = disk(P, 45.13, 40.355, 0.1)[1]
    out = {"area": area, "track": sub, "pairs": [f"{a}_{b}" for a, b in P["pairs"]], "span_days": T,
           "wavelength_m": P["wavelength"], "variants": {}}
    main = None
    variants = [("main", dict(gmin=0.4, corr=("iono",), ref=refs[0], use_height=True))]
    variants += [(f"gmin={g}", dict(gmin=g, corr=("iono",), ref=refs[0], use_height=True)) for g in gmin_list if g != 0.4]
    variants += [("no iono correction", dict(gmin=0.4, corr=(), ref=refs[0], use_height=True)),
                 ("iono+tropo+SET", dict(gmin=0.4, corr=("iono", "tropo", "set"), ref=refs[0], use_height=True)),
                 ("no height term", dict(gmin=0.4, corr=("iono",), ref=refs[0], use_height=False)),
                 ("quadratic ramp", dict(gmin=0.4, corr=("iono",), ref=refs[0], use_height=True, order=2))]
    variants += [(f"ref={r}", dict(gmin=0.4, corr=("iono",), ref=r, use_height=True)) for r in refs[1:]]
    for name, kw in variants:
        if not control and name not in ("main",) and area != "ararat":
            pass
        disp, info = motion_maps(P, stable=stable, **kw)
        n_ok = np.isfinite(disp).sum(0)
        rate, Tsum = stack_rate(disp, P["dt"])
        rate = rate.numpy()
        rate[n_ok < max(3, K - 2)] = np.nan
        tot = rate * T  # mm over the whole span
        ref_masks = [REFS[r](P) & np.isfinite(rate) for r in refs]
        v = {"kept_frac_mean": float(np.mean(info["kept_frac"])),
             "pair_resid_std_stable_mm": [round(x, 2) for x in info["resid_std_stable_mm"]],
             "stable_std_mm_total": float(np.nanstd(tot[stable])),
             "targets": {}}
        for tn, tm in targets.items():
            st = signal_stability(disp, P["dt"], tm & (n_ok >= K - 2), [REFS[r](P) for r in refs])
            v["targets"][tn] = {"mm_total": st["rate"] * T, "mm_total_by_ref": [x * T for x in st["rate_by_ref"]],
                                "loo_mm_total": [x * T for x in st["loo"]], "max_pair_share": st["max_pair_share"],
                                "per_pair_mm": st["per_pair"], "consistent": st["robust"]}
        hm = stable & np.isfinite(tot)
        v["corr_rate_dem_stable"] = float(np.corrcoef(tot[hm], P["dem"][hm])[0, 1]) if hm.sum() > 100 else None
        if area == "ararat":
            fm = floor & np.isfinite(tot)
            v["corr_rate_dem_floor"] = float(np.corrcoef(tot[fm], P["dem"][fm])[0, 1]) if fm.sum() > 100 else None
        out["variants"][name] = v
        if name == "main":
            main = (disp, tot, info, rate)
    disp, tot, info, rate = main
    # noise vs distance from the reference over stable ground (main variant)
    bins = [0, 5e3, 10e3, 20e3, 30e3, 45e3, 70e3]
    nvd = noise_vs_distance(torch.as_tensor(tot), P["X"], P["Y"], stable & np.isfinite(tot), ref_xy, bins)
    out["noise_vs_distance_mm_total"] = nvd
    # per-pair noise -> expected noise of the 120-day stack (independent pair noise assumption)
    # and the detection limit actually achieved: 2 x std of the stable-ground total at 10-45 km
    far = [b for b in nvd if b["r0_km"] >= 10 and b["n"] > 50]
    det = 2 * max(b["std"] for b in far) if far else float("nan")
    out["pixel_noise_2sigma_mm_total_max_10_45km"] = 2 * max(b["std"] for b in far if b["r1_km"] <= 45) if far else None
    # empirical detection limit for TARGET-SIZED areas: means of random 3-km disks on stable ground
    # (same size as the target disks), each already referenced to the reference area; 2 x their std
    rng = np.random.default_rng(0)
    rr_ = np.hypot(P["X"] - ref_xy[0], P["Y"] - ref_xy[1])
    cand = np.argwhere(stable & np.isfinite(tot) & (rr_ > 5e3) & (rr_ < 45e3))
    vals = []
    for i in rng.permutation(len(cand))[:3000]:
        r_, c_ = cand[i]
        m = (P["X"] - P["X"][r_, c_]) ** 2 + (P["Y"] - P["Y"][r_, c_]) ** 2 <= 3e3 ** 2
        m &= stable & np.isfinite(tot)
        if m.sum() > 400:
            vals.append(float(np.mean(tot[m])))
        if len(vals) >= 300:
            break
    vals = np.array(vals)
    out["disk3km_stable_mm_total"] = {"n": int(vals.size), "std": float(vals.std()), "mean": float(vals.mean()),
                                      "p95_abs": float(np.percentile(np.abs(vals), 95))}
    det = 2 * float(vals.std())
    out["detection_limit_mm_total_2sigma"] = det
    out["detection_limit_mm_per_year_2sigma"] = det / T * 365.25
    if inc is not None:
        out["incidence_deg_median"] = float(np.nanmedian(inc))
    # time series for pixels valid in all pairs (chain) and linear-rate formal error
    full = np.isfinite(disp).all(0)
    out["full_chain_frac"] = float(full.mean())
    ts_targets = {}
    tdates = np.concatenate([[P["t1"][0]], P["t2"]])
    for tn, tm in targets.items():
        s = region_series(disp, tm & full).numpy()
        cum = np.concatenate([[0], np.cumsum(s)])
        slope, sig = linear_rate(torch.as_tensor(cum), torch.as_tensor(tdates.astype(float)))
        ts_targets[tn] = {"cum_mm": [round(float(c), 2) for c in cum], "slope_mm_per_120d": float(slope) * 120,
                          "slope_sigma_mm_per_120d": float(sig) * 120}
    out["timeseries"] = ts_targets
    out["dates_days"] = tdates.tolist()
    key = f"motion_{area}_{sub}"
    results_update(key, out)
    np.savez_compressed(os.path.join(PROC, f"{area}-insar", f"motion_{sub}.npz"), tot=tot.astype(np.float32),
                        x=P["x"], y=P["y"], epsg=P["epsg"], disp=disp.astype(np.float32))
    return P, out, (disp, tot, stable, targets, refs, ref_xy)


# ============================================================ soil moisture (SME2)
def load_sme2(area="ararat", sub="a085"):
    files = sorted(glob.glob(os.path.join(PROC, f"{area}-sm", sub, "SME2_*.npz")))
    if not files:
        raise FileNotFoundError(f"no SME2 windows for {area}/{sub}")
    S = {"dates": []}
    st = {}
    for fp in files:
        z = np.load(fp)
        S["dates"].append(os.path.basename(fp)[5:13])
        for k in [k for k in z.files if not k.endswith(("__scale", "__offset", "__step", "__fac"))]:
            if k in ("x", "y", "lat", "lon", "epsg", "url"):
                S.setdefault(k, z[k])
            else:
                a = np.asarray(_decode(z, k), np.float64)
                st.setdefault(k, []).append(a)
    for k, v in st.items():
        S[k] = np.stack(v)
    for k in list(S):
        if isinstance(S[k], np.ndarray) and S[k].dtype.kind == "f" and k not in ("x", "y", "lat", "lon"):
            S[k][S[k] <= -9998] = np.nan
    return S


def rain_before(dates, hours=(48, 168), pass_utc_h=2.1):
    """Yerevan Zvartnots METAR (UDYZ) precipitation p01m [mm] summed over windows before each pass."""
    import csv, datetime as dt
    fp = os.path.join(ROOT, "data", "external", "weather", "metar_UDYZ.csv")
    rows = []
    for r in csv.DictReader(open(fp)):
        try:
            p = float(r["p01m"])
        except (ValueError, KeyError):
            continue
        t = dt.datetime.strptime(r["valid"], "%Y-%m-%d %H:%M")
        rows.append((t, p))
    # p01m is the precipitation accumulated in the hour of the report; METAR repeats it every
    # half hour, so keep one report per clock hour (the last one)
    hourly = {}
    for t, p in rows:
        hourly[t.replace(minute=0)] = p
    out = {}
    for d in dates:
        tp = dt.datetime(int(d[:4]), int(d[4:6]), int(d[6:8])) + dt.timedelta(hours=pass_utc_h)
        out[d] = {f"{h}h": round(sum(p for t, p in hourly.items() if tp - dt.timedelta(hours=h) <= t < tp), 1)
                  for h in hours}
    return out


def run_sm(area="ararat", sub="a085"):
    plt = _plt()
    S = load_sme2(area, sub)
    sm = S["soilMoisture"]
    flag = S.get("retrievalQualityFlag")
    rec = np.isfinite(sm)
    if flag is not None:
        rec &= (np.nan_to_num(flag, nan=1).astype(np.int64) & 1) == 0
    X, Y = np.meshgrid(S["x"], S["y"])
    dem = to_grid(os.path.join(ROOT, "data", "external", "dem", f"{area}_dem.tif"), S["x"], S["y"], int(S["epsg"]))
    LON, LAT = np.meshgrid(S["lon"], S["lat"])
    yer = box_mask(LON, LAT, 44.47, 40.15, 44.56, 40.21)
    inbox = box_mask(LON, LAT, 44.0, 39.8, 44.8, 40.3)
    water = (np.nanmax(S.get("ancillaryData__waterbodyFraction", np.zeros_like(sm)), 0) > 0.2)
    regs = {"valley floor (DEM < 900 m)": inbox & (dem < 900) & ~yer & ~water,
            "foothills (1200–2000 m)": inbox & (dem > 1200) & (dem < 2000) & ~water}
    out = {"dates": S["dates"], "regions": {}, "valid_frac_recommended": [], "lc_values": None}
    for k in range(len(S["dates"])):
        out["valid_frac_recommended"].append(round(float((rec[k] & inbox).sum() / max(1, (np.isfinite(sm[k]) & inbox).sum())), 3))
    for n, m in regs.items():
        mean_all, mean_rec = [], []
        for k in range(len(S["dates"])):
            a = sm[k][m & np.isfinite(sm[k])]
            b = sm[k][m & rec[k]]
            mean_all.append(round(float(a.mean()), 4) if a.size else None)
            mean_rec.append(round(float(b.mean()), 4) if b.size > 30 else None)
        out["regions"][n] = {"km2": float(m.sum() * 0.04), "mean_all": mean_all, "mean_recommended": mean_rec}
    for alg in ("DSG", "PMI", "TSR"):
        kk = f"algorithmCandidates__{alg}__soilMoisture"
        if kk in S:
            out.setdefault("by_algorithm", {})[alg] = {
                n: [round(float(np.nanmean(S[kk][k][m])), 4) if np.isfinite(S[kk][k][m]).any() else None
                    for k in range(len(S["dates"]))] for n, m in regs.items()}
    if "ancillaryData__landCover" in S:
        lc = S["ancillaryData__landCover"][0]
        u, c = np.unique(lc[inbox & np.isfinite(lc)], return_counts=True)
        out["lc_values"] = {int(a): int(b) for a, b in zip(u, c)}
    out["rain_note"] = ("Rain before the 31 Aug pass (docs/eda/validation-2026.md, SYNOP gauges, 48 h): Sevan 29.1 mm, "
                        "Hrazdan 29.9 mm, Armavir (western valley) 0.4 mm; all other passes <= 1 mm. The Zvartnots METAR "
                        "precipitation field is missing (M) for the whole period, so it is not used.")
    # date-to-date change of the regional means: is 19 Aug -> 31 Aug (the rain) unusual?
    for n, r in out["regions"].items():
        v = np.array(r["mean_all"], float)
        r["step_change"] = [None] + [round(float(b - a), 4) for a, b in zip(v[:-1], v[1:])]
    # north (Yerevan/Hrazdan side, lat > 40.2) vs south-west (Armavir side) foothills, the rain was in the north
    north = inbox & (LAT > 40.2) & (dem > 1100) & ~water
    out["north_foothills_mean"] = [round(float(np.nanmean(sm[k][north])), 4) for k in range(len(S["dates"]))]
    out["algorithm_flags_seen"] = sorted(int(v) for v in np.unique(flag[np.isfinite(flag)])) if flag is not None else None
    # Sevan basin land (control and the wet date): 19 Aug, 31 Aug, 12 Sep
    try:
        Q = load_sme2("sevan")
        LQ, AQ = np.meshgrid(Q["lon"], Q["lat"])
        wq = np.nanmax(Q.get("ancillaryData__waterbodyFraction", np.zeros_like(Q["soilMoisture"])), 0) > 0.05
        land = box_mask(LQ, AQ, 44.9, 40.1, 45.7, 40.7) & ~wq
        out["sevan_land_mean"] = {d: round(float(np.nanmean(Q["soilMoisture"][k][land])), 4) for k, d in enumerate(Q["dates"])}
    except FileNotFoundError:
        pass
    unc = S.get("soilMoistureUncertainty")
    if unc is not None:
        out["uncertainty_median"] = float(np.nanmedian(unc[:, inbox]))
    results_update(f"sm_{area}", out)
    # figure: four maps, the 31 Aug - 19 Aug difference, and the regional series
    ext = [S["x"][0] / 1e3, S["x"][-1] / 1e3, S["y"][-1] / 1e3, S["y"][0] / 1e3]
    show = [d for d in ("20260620", "20260726", "20260831", "20261018") if d in S["dates"]]
    fig = plt.figure(figsize=(11, 6.6))
    gs = fig.add_gridspec(2, 4, height_ratios=[1, 1.1])
    for j, d in enumerate(show):
        ax = fig.add_subplot(gs[0, j])
        k = S["dates"].index(d)
        im = ax.imshow(np.where(inbox, sm[k], np.nan), vmin=0.02, vmax=0.4, cmap="YlGnBu", extent=ext, interpolation="nearest")
        ax.contour(np.where(inbox, dem, np.nan), levels=[900], colors="r", linewidths=0.5, extent=ext, origin="upper")
        ax.set_title(f"SME2 {d[4:6]}-{d[6:8]} (200 m)")
        ax.set_xticks([]); ax.set_yticks([])
    fig.colorbar(im, ax=fig.axes[:len(show)], shrink=0.8, label="soil moisture [m³/m³]; red = 900 m contour")
    ax = fig.add_subplot(gs[1, 0:2])
    if "20260819" in S["dates"] and "20260831" in S["dates"]:
        k0, k1 = S["dates"].index("20260819"), S["dates"].index("20260831")
        im = ax.imshow(np.where(inbox, sm[k1] - sm[k0], np.nan), vmin=-0.12, vmax=0.12, cmap="BrBG", extent=ext,
                       interpolation="nearest")
        ax.contour(np.where(inbox, dem, np.nan), levels=[900], colors="k", linewidths=0.5, extent=ext, origin="upper")
        fig.colorbar(im, ax=ax, shrink=0.8, label="Δ m³/m³")
        ax.set_title("31 Aug minus 19 Aug (29 mm rain at Sevan/Hrazdan, 0.4 mm at Armavir)")
        ax.set_xticks([]); ax.set_yticks([])
    ax = fig.add_subplot(gs[1, 2:4])
    td = [_days(d) for d in S["dates"]]
    for n, r in out["regions"].items():
        ax.plot(td, r["mean_all"], "o-", label=n)
    ax.plot(td, out["north_foothills_mean"], "s--", label="northern foothills (lat > 40.2)")
    if "sevan_land_mean" in out:
        ax.plot([_days(d) for d in out["sevan_land_mean"]], list(out["sevan_land_mean"].values()), "^:", color="0.4",
                label="Sevan basin land")
    ax.axvline(_days("20260831"), color="b", lw=0.6, ls=":")
    ax.text(_days("20260831") + 1, ax.get_ylim()[0] + 0.005 if False else 0.125, "31 Aug rain", fontsize=6, color="b")
    ax.set_xticks(td, [_lab(d) for d in S["dates"]], rotation=60, fontsize=6)
    ax.set_ylabel("mean soil moisture [m³/m³]")
    ax.legend(fontsize=6)
    ax.set_title("regional means (PROVISIONAL SME2, track 85)")
    _save(fig, "fig8_soil_moisture.png")
    return out


# ============================================================ Tonle Sap: coherence vs amplitude flood map
def run_tonle():
    plt = _plt()
    P = load_gunw("tonle-sap", "a083")
    coh = P["coherenceMagnitude"].astype(np.float64)
    K = len(P["pairs"])
    A = torch.load(os.path.join(PROC, "tonle-sap", "math", "products.pt"), weights_only=False)
    from rasterio.transform import from_bounds
    Hs, Ws = A["water"].shape[1:]
    tr = from_bounds(103.4, 11.8, 105.2, 13.6, Ws, Hs)
    adates = [d.replace("-", "") for d in A["dates"]]

    def amp(layer, d):
        k = adates.index(d)
        return to_grid(None, P["x"], P["y"], P["epsg"], src_array=np.asarray(A[layer][k], np.float32),
                       src_transform=tr, src_crs="EPSG:4326", resampling="nearest") > 0.5
    cc = coherence_change(coh, never=0.3, drop=0.25, low=0.35)
    perm, lost = cc["perm"].numpy(), cc["lost"].numpy()
    valid = np.isfinite(coh).all(0)
    out = {"pairs": [f"{a}_{b}" for a, b in P["pairs"]], "per_pair": []}
    px = abs(float(P["x"][1] - P["x"][0]) * float(P["y"][1] - P["y"][0])) / 1e6
    for k, (a, b) in enumerate(P["pairs"]):
        if a not in adates or b not in adates:
            continue
        w1, w2 = amp("water", a), amp("water", b)
        v1, v2 = amp("flooded_veg", a), amp("flooded_veg", b)
        ok = valid & np.isfinite(coh[k])
        cls = {"water both dates": w1 & w2, "newly open water": w2 & ~w1 & ~v1,
               "newly flooded vegetation": v2 & ~v1 & ~w1 & ~w2, "flooded veg both dates": v1 & v2,
               "dry land both dates": ~w1 & ~w2 & ~v1 & ~v2}
        row = {"pair": f"{a}_{b}"}
        for n, m in cls.items():
            mm = m & ok
            row[n] = {"km2": round(float(mm.sum() * px), 1),
                      "median_coh": round(float(np.median(coh[k][mm])), 3) if mm.sum() > 50 else None,
                      "frac_coh_lt_0.3": round(float((coh[k][mm] < 0.3).mean()), 3) if mm.sum() > 50 else None}
        new_amp = (cls["newly open water"] | cls["newly flooded vegetation"]) & ok
        new_coh = lost[k] & ok
        row["coh_lost_km2"] = round(float(new_coh.sum() * px), 1)
        row["amp_new_flood_km2"] = round(float(new_amp.sum() * px), 1)
        row["coh_lost_and_amp_new_km2"] = round(float((new_coh & new_amp).sum() * px), 1)
        row["coh_lost_on_amp_dry_km2"] = round(float((new_coh & cls["dry land both dates"]).sum() * px), 1)
        out["per_pair"].append(row)
    out["perm_incoherent_km2"] = round(float((perm & valid).sum() * px), 1)
    results_update("tonle_coherence", out)
    # figure: onset pair (07-25 -> 08-18) coherence, amplitude new water, coherence loss
    k = [i for i, (a, b) in enumerate(P["pairs"]) if a == "20260725"]
    k = k[0] if k else min(3, K - 1)
    a, b = P["pairs"][k]
    ext = _extent(P["x"], P["y"])
    fig, axs = plt.subplots(1, 3, figsize=(11, 4))
    axs[0].imshow(coh[k], vmin=0, vmax=0.8, cmap="magma", extent=ext, interpolation="nearest")
    axs[0].set_title(f"coherence {_lab(a)}→{_lab(b)} (24 days, flood onset)")
    if a in adates and b in adates:
        w1, w2, v1, v2 = amp("water", a), amp("water", b), amp("flooded_veg", a), amp("flooded_veg", b)
        rgb = np.ones(coh[k].shape + (3,))
        rgb[w1 & w2] = (0.15, 0.3, 0.7)
        rgb[w2 & ~w1] = (0.2, 0.6, 1.0)
        rgb[v2 & ~v1 & ~w2] = (0.2, 0.7, 0.3)
        axs[1].imshow(rgb, extent=ext, interpolation="nearest")
        axs[1].set_title("amplitude map: dark blue water both dates,\nlight blue new open water, green new flooded veg.")
    rgb = np.ones(coh[k].shape + (3,))
    rgb[perm] = (0.15, 0.3, 0.7)
    rgb[lost[k]] = (0.9, 0.3, 0.1)
    rgb[~np.isfinite(coh[k])] = (0.8, 0.8, 0.8)
    axs[2].imshow(rgb, extent=ext, interpolation="nearest")
    axs[2].set_title("coherence: dark blue always incoherent,\norange became incoherent in this pair")
    for ax in axs:
        ax.tick_params(labelsize=5)
    _save(fig, "fig9_tonle_coherence_vs_amplitude.png")
    return out


def _profile(P, tot, p0, p1, n=200, halfwidth_m=1500):
    from rasterio.warp import transform
    xs, ys = transform("EPSG:4326", f"EPSG:{int(P['epsg'])}", [p0[0], p1[0]], [p0[1], p1[1]])
    t = np.linspace(0, 1, n)
    px, py = xs[0] + t * (xs[1] - xs[0]), ys[0] + t * (ys[1] - ys[0])
    L = math.hypot(xs[1] - xs[0], ys[1] - ys[0])
    ux, uy = (xs[1] - xs[0]) / L, (ys[1] - ys[0]) / L
    s = (P["X"] - xs[0]) * ux + (P["Y"] - ys[0]) * uy
    o = -(P["X"] - xs[0]) * uy + (P["Y"] - ys[0]) * ux
    sel = (np.abs(o) < halfwidth_m) & (s >= 0) & (s <= L)
    edges = np.linspace(0, L, 41)
    mid, med, p25, p75, h = [], [], [], [], []
    for a, b in zip(edges[:-1], edges[1:]):
        m = sel & (s >= a) & (s < b) & np.isfinite(tot)
        mid.append((a + b) / 2e3)
        if m.sum() > 5:
            v = tot[m]
            med.append(np.median(v)); p25.append(np.percentile(v, 25)); p75.append(np.percentile(v, 75))
        else:
            med.append(np.nan); p25.append(np.nan); p75.append(np.nan)
        hm = sel & (s >= a) & (s < b) & np.isfinite(P["dem"])
        h.append(np.median(P["dem"][hm]) if hm.sum() else np.nan)
    return (xs, ys), np.array(mid), np.array(med), np.array(p25), np.array(p75), np.array(h)


def motion_figures(A, S):
    """A = run_motion('ararat') result, S = run_motion('sevan') result."""
    plt = _plt()
    (P, out, (disp, tot, stable, targets, refs, ref_xy)) = A
    (Q, outq, (dq, totq, stq, tq, rq, rxq)) = S
    lim = 30
    fig, axs = plt.subplots(1, 3, figsize=(12, 4.2), gridspec_kw={"width_ratios": [1.15, 1.15, 1]})
    ax = axs[0]
    im = ax.imshow(tot, vmin=-lim, vmax=lim, cmap="RdBu", extent=_extent(P["x"], P["y"]), interpolation="nearest")
    for r in refs:
        m = REFS[r](P)
        ax.contour(m.astype(float), levels=[0.5], colors="k", linewidths=0.6, extent=_extent(P["x"], P["y"]), origin="upper")
    for n, m in targets.items():
        ax.contour(m.astype(float), levels=[0.5], colors="m", linewidths=0.6, extent=_extent(P["x"], P["y"]), origin="upper")
    _mark_places(ax, P, ["Yerevan", "Masis", "Aknalich", "Ararat-Vedi fish farms"])
    (xs, ys), mid, med, p25, p75, h = _profile(P, tot, (44.62, 40.24), (44.22, 39.86))
    ax.plot(np.array(xs) / 1e3, np.array(ys) / 1e3, "g-", lw=1)
    ax.set_title(f"Ararat: LOS change {_lab(P['pairs'][0][0])}→{_lab(P['pairs'][-1][1])} [mm, + toward satellite]\n"
                 f"black boxes = references, magenta = 3-km targets")
    fig.colorbar(im, ax=ax, shrink=0.7, label="mm over the period")
    ax = axs[1]
    im = ax.imshow(totq, vmin=-lim, vmax=lim, cmap="RdBu", extent=_extent(Q["x"], Q["y"]), interpolation="nearest")
    for r in rq:
        ax.contour(REFS[r](Q).astype(float), levels=[0.5], colors="k", linewidths=0.6, extent=_extent(Q["x"], Q["y"]), origin="upper")
    ax.set_title("control: Lake Sevan basin, same processing\n(lake masked by low coherence)")
    fig.colorbar(im, ax=ax, shrink=0.7, label="mm")
    ax = axs[2]
    ax.fill_between(mid, p25, p75, color="C0", alpha=0.3)
    ax.plot(mid, med, "C0-", label="displacement (median, IQR) ±1.5 km")
    ax.axhspan(-out["detection_limit_mm_total_2sigma"], out["detection_limit_mm_total_2sigma"], color="0.85",
               label=f"±2σ detection limit ({out['detection_limit_mm_total_2sigma']:.0f} mm)")
    ax.set_xlabel("distance along profile NE→SW [km]")
    ax.set_ylabel("mm over the period")
    ax2 = ax.twinx()
    ax2.plot(mid, h, "k:", lw=1)
    ax2.set_ylabel("elevation [m] (dotted)")
    ax.legend(fontsize=6, loc="lower left")
    ax.set_title("profile across the valley (green line)")
    for a in axs[:2]:
        a.tick_params(labelsize=6)
    _save(fig, "fig5_ararat_displacement.png")

    # noise vs distance + leave-one-out / reference stability
    fig, axs = plt.subplots(1, 3, figsize=(12, 3.8))
    ax = axs[0]
    for o, lab in ((out, "Ararat (stable ground)"), (outq, "Sevan control (land)")):
        nv = [b for b in o["noise_vs_distance_mm_total"] if b["n"] > 50]
        ax.plot([(b["r0_km"] + b["r1_km"]) / 2 for b in nv], [b["std"] for b in nv], "o-", label=lab)
    ax.set_xlabel("distance from reference [km]")
    ax.set_ylabel("std of stacked displacement [mm]")
    ax.set_title("noise grows with distance from the reference")
    ax.legend(fontsize=6)
    ax = axs[1]
    names = list(out["variants"]["main"]["targets"])
    for i, n in enumerate(names):
        t = out["variants"]["main"]["targets"][n]
        ax.plot([i] * len(t["loo_mm_total"]), t["loo_mm_total"], "_", color="C1", ms=12)
        ax.plot(i, t["mm_total"], "o", color="C0")
        ax.plot([i] * len(t["mm_total_by_ref"]), t["mm_total_by_ref"], "x", color="C3")
    ax.axhspan(-out["detection_limit_mm_total_2sigma"], out["detection_limit_mm_total_2sigma"], color="0.85")
    ax.axhline(0, color="k", lw=0.5)
    ax.set_xticks(range(len(names)), names, rotation=20, fontsize=6)
    ax.set_ylabel("target minus reference [mm over period]")
    ax.set_title("o main · – leave one pair out · × other references\n(grey: ±2σ detection limit)")
    ax = axs[2]
    vn = [v for v in out["variants"] if v != "main"]
    for j, n in enumerate(names):
        ax.plot(range(len(vn)), [out["variants"][v]["targets"][n]["mm_total"] for v in vn], "o-", ms=3, label=n)
    ax.axhspan(-out["detection_limit_mm_total_2sigma"], out["detection_limit_mm_total_2sigma"], color="0.85")
    ax.set_xticks(range(len(vn)), vn, rotation=45, ha="right", fontsize=5)
    ax.set_title("same, under processing variants")
    ax.legend(fontsize=5)
    _save(fig, "fig6_noise_and_stability.png")

    # per-pair target series (time series) for Ararat
    fig, ax = plt.subplots(figsize=(6.5, 3.2))
    td = out["dates_days"]
    for n, s in out["timeseries"].items():
        ax.plot(td, s["cum_mm"], "o-", ms=3, label=f"{n} (slope {s['slope_mm_per_120d']:+.1f} ± {s['slope_sigma_mm_per_120d']:.1f} mm/120 d)")
    ax.axhline(0, color="k", lw=0.5)
    ax.set_xticks(td, [f"{int(d)}" for d in td], fontsize=5)
    ax.set_xlabel("day of 2026")
    ax.set_ylabel("cumulative LOS [mm] vs Yerevan")
    ax.legend(fontsize=6)
    ax.set_title("Ararat Valley targets: cumulative displacement from the chain of 12-day pairs")
    _save(fig, "fig7_ararat_timeseries.png")


def export_layer():
    """Candidate app layer: Ararat 12-day coherence, summer mean, autumn mean and autumn - summer, as a
    3-band GeoTIFF (UTM 38N, 80 m; bands uint8: coherence x 250, difference as 125 + 250 * diff, 255 = no data)."""
    import rasterio
    from rasterio.transform import from_origin
    P = load_gunw("ararat")
    coh = P["coherenceMagnitude"].astype(np.float64)
    K = coh.shape[0]
    cs, ca = np.nanmean(coh[:3], 0), np.nanmean(coh[K - 3:], 0)
    q = lambda a: np.where(np.isfinite(a), np.clip(np.round(a * 250), 0, 250), 255).astype(np.uint8)
    qd = lambda a: np.where(np.isfinite(a), np.clip(np.round(125 + 250 * a), 0, 250), 255).astype(np.uint8)
    dx = float(P["x"][1] - P["x"][0])
    tr = from_origin(float(P["x"][0]) - dx / 2, float(P["y"][0]) + dx / 2, dx, dx)
    fp = os.path.join(PROC, "ararat-insar", "ararat_coherence_summer_autumn.tif")
    with rasterio.open(fp, "w", driver="GTiff", width=len(P["x"]), height=len(P["y"]), count=3, dtype="uint8",
                       crs=f"EPSG:{int(P['epsg'])}", transform=tr, nodata=255, compress="deflate") as d:
        d.write(q(cs), 1); d.write(q(ca), 2); d.write(qd(ca - cs), 3)
        d.update_tags(source="NISAR L2 GUNW PROVISIONAL (NASA/JPL-Caltech, ISRO; ASF DAAC), track 85 frame 22",
                      band1="summer mean coherence x250 (pairs 06-20..07-26)",
                      band2="autumn mean coherence x250 (pairs 09-12..10-18)",
                      band3="125 + 250*(autumn - summer)")
    print("layer", fp, os.path.getsize(fp) // 1024, "KB")
    return fp


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "all"
    if cmd in ("layer", "all"):
        export_layer()
    if cmd in ("fetch", "all"):
        fetch()
    if cmd in ("timeline", "all"):
        pair_timeline()
    if cmd in ("coherence", "all"):
        print(json.dumps(run_coherence(), indent=1, default=float)[:3000])
    if cmd in ("motion", "all"):
        A = run_motion(area="ararat")
        S = run_motion(area="sevan")
        motion_figures(A, S)
        # descending track 122 frame 68 was extracted too, but it covers only the window west of 44.30 E
        # (23 % of it: no Yerevan reference, no Masis or fish farms), so it is not used for motion
        results_update("motion_ararat_d122", {"excluded": "track 122 frame 68 (descending) covers only 43.96-44.30 E "
                                              "of the Ararat window (23 %); the Yerevan reference, Masis, Artashat and the "
                                              "fish farms are outside it; per-pair residual scatter 7-102 mm. Not used."})
    if cmd in ("sm", "all"):
        print(json.dumps(run_sm(), indent=1, default=float)[:3000])
    if cmd in ("tonle", "all"):
        print(json.dumps(run_tonle(), indent=1, default=float)[:3000])


if __name__ == "__main__":
    main(sys.argv)
