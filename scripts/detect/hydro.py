"""Independent estimate of the 2026 flooded extent from water LEVEL data plus the long-term record.

    .venv/bin/python -m scripts.detect.hydro               # tonle-sap (default)
    .venv/bin/python -m scripts.detect.hydro tonle-sap

Idea 1 (main): a pixel's long-term water occurrence o (JRC GSW 1984-2021, % of observations that saw
water) is, to first order, a monotone function of its height on the floodplain. The multi-year lake level
record says how often the lake stands at or above any level h: the exceedance e(h) = P(H >= h). A pixel of
height z has o = 100 e(z) (bathtub + ideal sampling), so on a date with level h_t the flooded set is
{o >= 100 e(h_t)}. No radar is used.

Idea 2 (cross-check): bathtub fill of the Copernicus DEM from the permanent lake, through pixels below the
level (connectivity respected).

Then: judge the radar detectors on the floodplain, calibrate the HH threshold against the expected set
(fit on half the dates / half the frame, test on the rest, bootstrap), check the threshold at Lake Sevan
and in Armenia, and assemble a range for the headline.

Writes data/processed/<area>/hydro/products.pt, docs/eda/hydro/results.json and figures.
Write-up: docs/eda/hydro.md. Numerics in PyTorch on CPU (one process; memory kept below ~3 GB).
"""
from __future__ import annotations

import datetime as dt
import gc
import json
import math
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import rasterio  # noqa: E402
import scipy.ndimage as ndi  # noqa: E402
import torch  # noqa: E402
import torch.nn.functional as F  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from scipy import stats as sstats  # noqa: E402

from scripts.detect.stack import ROOT  # noqa: E402

OUT = ROOT / "docs/eda/hydro"
LV = ROOT / "data/external/levels"
GSW = ROOT / "data/external/gsw"
NODATA = 255
EPOCH = dt.datetime(1990, 1, 1)
GAP = (dt.datetime(2026, 7, 27), dt.datetime(2026, 8, 10))
OLD_WEAK, OLD_STRONG = 0.12, 0.06
SEVAN_INTERVAL = (0.045, 0.160)   # docs/eda/stats-change.md
OTSU2 = 0.24                      # EDA second-level Otsu
SEVAN_KM2 = 1242.0
SEVAN_WINDY = (5, 6)              # 31 Aug, 12 Sep
CORRIDOR = {"lon_min": 104.5, "lat_max": 12.5}   # south-east river plain, as in stats_change.flood_wave
# Kummu et al. (2014), as printed in Chua et al. (2022) HESS 26:609, Eq. 18 (H = Kampong Luong level, m)
KUMMU = (-5.5701, 137.40, 470.29, 1680.2)
KL_STATS = {"1996-2009": {"min": 0.70, "max": 8.58}, "2010-2019": {"min": 0.60, "max": 7.52}}
# Landsat clear-sky climatology for Cambodia (ASSUMED, illustrative): share of acquisitions usable, Jan..Dec
CLEAR_SKY = [0.70, 0.70, 0.60, 0.50, 0.30, 0.15, 0.10, 0.10, 0.10, 0.20, 0.45, 0.65]
WET = [6, 7, 8, 9, 10]

C = {"s1": "#2a78d6", "s2": "#eb6834", "s3": "#1baf7a", "s4": "#eda100", "s5": "#8e5cc4", "s6": "#d6457a",
     "ink": "#0b0b0b", "ink2": "#52514e", "grid": "#e4e3df", "surf": "#fcfcfb", "hl": "#f0efec"}


# ====================================================================== core functions (tested)
def daily_series(t_days, h, max_gap=40.0) -> dict:
    """Daily level series from irregular observations (days since EPOCH). Linear interpolation;
    days inside a gap longer than max_gap are NaN. Returns dict of tensors day, h, year, month."""
    t = np.asarray(t_days, float)
    v = np.asarray(h, float)
    o = np.argsort(t)
    t, v = t[o], v[o]
    days = np.arange(math.ceil(t[0]), math.floor(t[-1]) + 1)
    hd = np.interp(days, t, v)
    j = np.searchsorted(t, days)
    j = np.clip(j, 1, len(t) - 1)
    gap = t[j] - t[j - 1]
    hd[gap > max_gap] = np.nan
    dates = [EPOCH + dt.timedelta(days=int(d)) for d in days]
    return {"day": torch.tensor(days, dtype=torch.float64), "h": torch.tensor(hd, dtype=torch.float64),
            "year": torch.tensor([d.year for d in dates]), "month": torch.tensor([d.month for d in dates])}


def _day_weights(series, month_weights=None, year_weights=None, years=None):
    """Weight per day so that each calendar month m gets total weight w_m and, inside a month, each year y
    gets a share proportional to year_weights[y] (JRC pools all years of a month, then averages months)."""
    h, yr, mo = series["h"], series["year"], series["month"]
    ok = torch.isfinite(h)
    if years is not None:
        ok &= (yr >= years[0]) & (yr <= years[1])
    mw = torch.ones(13, dtype=torch.float64)
    if month_weights is not None:
        mw[1:] = torch.as_tensor(month_weights, dtype=torch.float64)
    yw_all = torch.ones(int(yr.max()) + 1, dtype=torch.float64)
    if year_weights is not None:
        for y, w in year_weights.items():
            if y < len(yw_all):
                yw_all[y] = w
    w = torch.zeros_like(h)
    for m in range(1, 13):
        sel = ok & (mo == m)
        if not sel.any():
            continue
        ys = torch.unique(yr[sel])
        tot = sum(float(yw_all[y]) for y in ys)
        for y in ys:
            s = sel & (yr == y)
            w[s] = mw[m] * yw_all[y] / tot / s.sum()
    return w, ok


def exceedance(level_series, h=None, month_weights=None, year_weights=None, years=None):
    """Exceedance probability e(h) = P(H >= h) under a sampling model.

    level_series: dict from daily_series. month_weights: 12 relative weights (None = every calendar month
    equal, which is how JRC occurrence is built). year_weights: {year: w} (None = equal).
    years: (first, last) inclusive. h: query levels (tensor/array/float) -> e(h) tensor of the same shape;
    if h is None returns (levels sorted descending, e at those levels) = the exceedance curve."""
    w, ok = _day_weights(level_series, month_weights, year_weights, years)
    lv, ww = level_series["h"][ok & (w > 0)], w[ok & (w > 0)]
    o = torch.argsort(lv, descending=True)
    lv, ww = lv[o], ww[o]
    cum = torch.cumsum(ww, 0) / ww.sum()       # P(H >= lv[i])
    if h is None:
        return lv, cum
    q = torch.as_tensor(h, dtype=torch.float64)
    # number of levels >= q  (lv descending)
    k = torch.searchsorted(-lv.contiguous(), -q.reshape(-1).contiguous(), right=True)
    e = torch.where(k > 0, cum[(k - 1).clamp_min(0)], torch.zeros_like(q.reshape(-1)))
    return e.reshape(q.shape)


def level_for_exceedance(level_series, e, **kw):
    """Inverse of exceedance: the level that is reached or exceeded with probability e."""
    lv, cum = exceedance(level_series, None, **kw)
    e = torch.as_tensor(e, dtype=torch.float64).reshape(-1)
    k = torch.searchsorted(cum.contiguous(), e.contiguous()).clamp_max(len(lv) - 1)
    return lv[k]


class OccHist:
    """Per-pixel histogram of 30 m occurrence values (0..100) inside each of our grid cells:
    cum[i, j, k] = number of valid 30 m pixels with occurrence >= k (uint8)."""

    def __init__(self, cum: torch.Tensor):
        self.cum = cum

    @property
    def n_valid(self):
        return self.cum[..., 0].float()

    def frac_ge(self, o):
        """Fraction of each cell with occurrence >= o (o in %, float or [T]); linear between integers."""
        o = torch.as_tensor(o, dtype=torch.float64).reshape(-1)
        n = self.n_valid.clamp_min(1)
        out = []
        for v in o.tolist():
            # occurrence is stored as integers; interpolate linearly between ceil/floor so the area is a
            # continuous function of the level (exact at integer o)
            v = min(max(v, 0.0), 100.0)
            k = int(math.floor(v))
            r = v - k
            f = self.cum[..., k].float()
            if r > 0 and k < 100:
                f = (1 - r) * f + r * self.cum[..., k + 1].float()
            out.append(f / n)
        return torch.stack(out)


def occ_hist_on_grid(arr, w0, n0, res, bounds, H, W, row_chunk=480) -> OccHist:
    """Assign every 30 m GSW pixel to the grid cell containing its centre and count occurrence values."""
    w, s, e, n = bounds
    nr, nc = arr.shape
    lon_c = w0 + (np.arange(nc) + 0.5) * res
    ix = np.floor((lon_c - w) / (e - w) * W).astype(np.int64)
    okc = (ix >= 0) & (ix < W)
    cum = torch.zeros(H, W, 101, dtype=torch.uint8)
    for r0 in range(0, nr, row_chunk):
        r1 = min(nr, r0 + row_chunk)
        lat_c = n0 - (np.arange(r0, r1) + 0.5) * res
        iy = np.floor((n - lat_c) / (n - s) * H).astype(np.int64)
        okr = (iy >= 0) & (iy < H)
        if not okr.any():
            continue
        a = torch.from_numpy(arr[r0:r1].astype(np.int64))
        Y = torch.from_numpy(iy)[:, None].expand(-1, nc)
        X = torch.from_numpy(ix)[None].expand(r1 - r0, -1)
        m = torch.from_numpy(okr)[:, None] & torch.from_numpy(okc)[None] & (a != NODATA) & (a <= 100)
        y0, y1 = int(iy[okr].min()), int(iy[okr].max())
        idx = ((Y[m] - y0) * W + X[m]) * 101 + a[m]
        cnt = torch.bincount(idx, minlength=(y1 - y0 + 1) * W * 101).reshape(y1 - y0 + 1, W, 101)
        cum[y0:y1 + 1] += cnt.to(torch.uint8)
    # reverse cumulative: count of values >= k
    cum = torch.flip(torch.cumsum(torch.flip(cum.to(torch.int16), [-1]), -1), [-1]).to(torch.uint8)
    return OccHist(cum)


def expected_extent(occurrence, e, sampling=None, return_frac=False, efficiency=1.0):
    """Expected flooded set for exceedance probabilities e ([T] or scalar).

    occurrence: [H, W] tensor of occurrence in % (pixel flooded iff occ >= 100 d e), or an OccHist
    (fraction of the cell with occ >= 100 d e; flooded iff that fraction >= 0.5).
    sampling: optional dict(series=..., h=[T], month_weights=..., year_weights=..., years=...): the
    threshold is then recomputed as the exceedance under that (biased) sampling model; `e` is ignored.
    efficiency d: share of the true water time that the optical record actually labels water (Landsat misses
    water in some clear scenes: turbidity, glint, cloud shadow, mixed pixels). A permanently wet pixel then
    shows o = 100 d, not 100; d is estimated from the core of the permanent lake.
    Returns bool [T, H, W] (and the fraction [T, H, W] if return_frac)."""
    if sampling is not None:
        s = dict(sampling)
        e = exceedance(s.pop("series"), s.pop("h"), **s)
    e = torch.as_tensor(e, dtype=torch.float64).reshape(-1)
    o = 100.0 * efficiency * e
    if isinstance(occurrence, OccHist):
        frac = occurrence.frac_ge(o)
        m = frac >= 0.5
        return (m, frac) if return_frac else m
    occ = torch.as_tensor(occurrence, dtype=torch.float64)
    m = occ[None] >= o[:, None, None] - 1e-9
    return (m, m.float()) if return_frac else m


def bathtub(dem, level, seed_mask):
    """Bathtub inundation: pixels with dem <= level that are 8-connected to seed_mask through such pixels
    (the seed itself always counts as water). level: float or [T] -> bool [T, H, W] (or [H, W])."""
    d = torch.as_tensor(dem).numpy()
    sd = torch.as_tensor(seed_mask).numpy().astype(bool)
    lv = torch.as_tensor(level, dtype=torch.float64).reshape(-1)
    st8 = np.ones((3, 3), bool)
    out = np.zeros((len(lv),) + d.shape, bool)
    for i, L in enumerate(lv.tolist()):
        wet = (d <= L) | sd
        lab, n = ndi.label(wet, structure=st8)
        keep = np.zeros(n + 1, bool)
        keep[np.unique(lab[sd])] = True
        keep[0] = False
        out[i] = keep[lab]
    o = torch.from_numpy(out)
    return o if torch.as_tensor(level).dim() > 0 else o[0]


def calibrate_threshold(thresholds, det_area, target, fit=None, fine=2001):
    """Threshold whose detected area best matches the target (least squares over the fit dates).

    thresholds [n]; det_area [n, T] (area detected at each threshold and date); target [T].
    det_area is interpolated linearly between thresholds. Returns dict(best, sse_curve, per_date)."""
    th = np.asarray(thresholds, float)
    A = np.asarray(det_area, float)
    tg = np.asarray(target, float)
    idx = np.arange(A.shape[1]) if fit is None else np.asarray(fit)
    tf = np.linspace(th.min(), th.max(), fine)
    Af = np.stack([np.interp(tf, th, A[:, t]) for t in range(A.shape[1])], 1)
    sse = ((Af[:, idx] - tg[idx]) ** 2).sum(1)
    best = float(tf[int(np.argmin(sse))])
    per = []
    for t in range(A.shape[1]):   # threshold that matches each date alone (area is monotone in threshold)
        a = Af[:, t]
        per.append(float(tf[int(np.argmin(np.abs(a - tg[t])))]) if (a.min() <= tg[t] <= a.max()) else None)
    return {"best": best, "sse_curve": (tf, sse), "per_date": per}


# ====================================================================== helpers
def _r(x, nd=1):
    if x is None:
        return None
    if isinstance(x, (list, tuple, np.ndarray, torch.Tensor)):
        return [_r(v, nd) for v in (x.tolist() if hasattr(x, "tolist") else x)]
    x = float(x)
    return None if not math.isfinite(x) else round(x, nd)


def _days(t: dt.datetime) -> float:
    return (t.replace(tzinfo=None) - EPOCH).total_seconds() / 86400


def style(ax):
    ax.set_facecolor(C["surf"])
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(C["ink2"])
    ax.tick_params(colors=C["ink2"], labelsize=8)
    ax.grid(axis="y", color=C["grid"], lw=0.8)
    ax.set_axisbelow(True)


FIGS = []


def savefig(fig, name, dpi=105):
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    fig.savefig(p, dpi=dpi, facecolor=C["surf"], bbox_inches="tight")
    plt.close(fig)
    kb = p.stat().st_size / 1024
    if kb > 340:
        im = __import__("PIL.Image", fromlist=["Image"]).open(p)
        im = im.convert("P", palette=1, colors=128)
        im.save(p, optimize=True)
        kb = p.stat().st_size / 1024
    FIGS.append({"file": str(p.relative_to(ROOT)), "kB": round(kb, 1)})
    print("  fig", name, round(kb), "kB")


def merge_obs(rows, tol_days=3.0):
    """Altimetry: merge observations from overlapping missions within tol_days (mean)."""
    t = np.array([_days(r["time"]) for r in rows])
    h = np.array([r["h_egm2008"] for r in rows])
    o = np.argsort(t)
    t, h = t[o], h[o]
    gt, gh, cur = [], [], [0]
    for i in range(1, len(t)):
        if t[i] - t[cur[0]] <= tol_days:
            cur.append(i)
        else:
            gt.append(t[cur].mean()); gh.append(h[cur].mean()); cur = [i]
    gt.append(t[cur].mean()); gh.append(h[cur].mean())
    return np.array(gt), np.array(gh)


def landsat_year_weights():
    """Relative number of Landsat acquisitions per year (sensors in orbit used by JRC)."""
    w = {}
    for y in range(1984, 2027):
        w[y] = 1.0 if y <= 1998 else (2.0 if y <= 2011 else (1.0 if y == 2012 else 2.0))
    return w


# ====================================================================== main analysis
def run(area="tonle-sap"):
    if area != "tonle-sap":
        raise SystemExit("only tonle-sap has an independent level record wired in (see docs/eda/hydro.md)")
    from scripts.detect import validate_2026 as v26
    from scripts.detect.validate import fetch_clip, reference_on_grid
    R = {"generated": dt.datetime.now().isoformat(timespec="seconds"), "area": area}
    torch.manual_seed(20261114)

    # ---------------------------------------------------------------- grid, our detector, domain
    p = torch.load(ROOT / f"data/processed/{area}/math/products.pt", weights_only=False, map_location="cpu")
    dates, bounds, km2 = p["dates"], tuple(p["bounds"]), float(p["km2_per_px"])
    water_ours = p["water"].clone()
    valid_all = p["valid"].all(0).clone()
    ours_km2_pub = p["water_km2"].tolist()
    del p; gc.collect()
    T, H, W = water_ours.shape
    w_, s_, e_, n_ = bounds
    lon = w_ + (torch.arange(W) + 0.5) / W * (e_ - w_)
    lat = n_ - (torch.arange(H) + 0.5) / H * (n_ - s_)
    LAT, LON = torch.meshgrid(lat, lon, indexing="ij")
    ref, _ = reference_on_grid(area, bounds, H, W)
    dom = valid_all & (ref["valid_frac"] >= 0.5)
    corridor = (LON > CORRIDOR["lon_min"]) & (LAT < CORRIDOR["lat_max"])
    occ_arr, ow0, on0, _ = fetch_clip(area, "occurrence", bounds)
    hist = occ_hist_on_grid(occ_arr, ow0, on0, 0.00025, bounds, H, W)
    del occ_arr; gc.collect()
    # permanent lake (largest component of occurrence >= 90 %) and the optical detection efficiency d
    seed_raw = ref["occ_ge90_frac"] >= 0.5
    lab, nlab = ndi.label(seed_raw.numpy(), structure=np.ones((3, 3), bool))
    cnt = np.bincount(lab.ravel()); cnt[0] = 0
    seed = torch.from_numpy(lab == int(cnt.argmax()))
    core = torch.from_numpy(ndi.binary_erosion(seed.numpy(), iterations=8)) & dom
    oc = ref["occ_mean"][core]
    d_c, d_lo, d_hi = (float(oc.quantile(q)) / 100 for q in (0.5, 0.1, 0.9))
    R["detection_efficiency"] = {"rule": "occurrence of the permanent-lake core (largest occ>=90 component eroded by 8 px) / 100",
                                 "core_km2": _r(float(core.sum()) * km2), "d_median": _r(d_c, 4), "d_p10": _r(d_lo, 4), "d_p90": _r(d_hi, 4)}
    D = dom.float()
    A = lambda m: float((m.float() * D).sum()) * km2  # noqa: E731  area (km2) inside the domain
    R["grid"] = {"H": H, "W": W, "bounds": bounds, "km2_per_px": km2, "domain_km2": _r(A(dom)),
                 "domain_rule": "radar footprint valid on all 10 dates (math products) and >= 50 % valid GSW",
                 "corridor_rule": f"lon > {CORRIDOR['lon_min']} and lat < {CORRIDOR['lat_max']} (south-east river plain)",
                 "corridor_km2": _r(A(dom & corridor))}
    ours_km2 = [A(water_ours[t]) for t in range(T)]
    R["ours_open_water_km2_domain"] = _r(ours_km2)
    R["ours_open_water_km2_published"] = _r(ours_km2_pub)

    # frame share of the lake system (JRC maximum extent)
    ext = ref["ext_frac"]
    box_ext = float(ext.sum()) * km2
    fr_ext = float((ext * D).sum()) * km2
    edge = torch.zeros(H, W, dtype=torch.bool)
    edge[:5] = edge[-5:] = True
    edge[:, :5] = edge[:, -5:] = True
    R["frame_share"] = {"jrc_max_extent_km2_whole_box": _r(box_ext), "jrc_max_extent_km2_in_domain": _r(fr_ext),
                        "share_of_box_max_extent_in_domain": _r(fr_ext / box_ext, 3),
                        "jrc_max_extent_km2_on_box_edge_5px": _r(float((ext * edge).sum()) * km2),
                        "jrc_occ_ge90_km2_whole_box": _r(float(ref["occ_ge90_frac"].sum()) * km2),
                        "jrc_occ_ge90_km2_in_domain": _r(float((ref["occ_ge90_frac"] * D).sum()) * km2)}

    # ---------------------------------------------------------------- levels
    passes = v26.pass_times("T83_F8")
    assert [d for d, _ in passes] == dates
    tp = np.array([_days(T_) for _, T_ in passes])
    alt_rows = v26.parse_gwm(LV / "gwm_lake000319.10d.2.txt")
    at, ah = merge_obs(alt_rows)
    series = daily_series(at, ah)
    h_pass = np.interp(tp, at, ah)
    yrs_all = sorted(set(int(y) for y in series["year"].tolist()))
    R["levels"] = {"source": "G-REALM lake000319 (NASA GSFC Global Water Monitor / USDA FAS), EGM2008 heights",
                   "n_obs_raw": len(alt_rows), "n_obs_merged": int(len(at)),
                   "first": (EPOCH + dt.timedelta(days=float(at[0]))).date().isoformat(),
                   "last": (EPOCH + dt.timedelta(days=float(at[-1]))).date().isoformat(),
                   "pass_level_m": _r(h_pass, 2), "pass_utc": [T_.strftime("%Y-%m-%dT%H:%M") for _, T_ in passes]}
    # annual extremes (for datum checks)
    ann = {}
    for y in range(1993, 2026):
        s = (series["year"] == y) & torch.isfinite(series["h"])
        if s.sum() > 300:
            ann[y] = (float(series["h"][s].min()), float(series["h"][s].max()))
    R["levels"]["annual_min_max_m"] = {str(y): _r(v, 2) for y, v in ann.items()}

    # ---------------------------------------------------------------- sampling variants
    LYW = landsat_year_weights()
    variants = {
        "central (months balanced, 1993-2021, equal years)": dict(years=(1993, 2021)),
        "Landsat-count year weights 1993-2021": dict(years=(1993, 2021), year_weights=LYW),
        "era 1993-2009 only": dict(years=(1993, 2009)),
        "era 2010-2021 only": dict(years=(2010, 2021)),
        "wet months Jun-Oct half weight": dict(years=(1993, 2021), month_weights=[0.5 if m in WET else 1.0 for m in range(1, 13)]),
        "detection efficiency d = lake-core p10": dict(years=(1993, 2021), efficiency=d_lo),
        "detection efficiency d = lake-core p90": dict(years=(1993, 2021), efficiency=d_hi),
        "no efficiency correction (d = 1)": dict(years=(1993, 2021), efficiency=1.0),
    }
    extreme = {
        "no monthly balancing (clear-sky weighted)": dict(years=(1993, 2021), month_weights=CLEAR_SKY),
        "dry season only (Nov-May)": dict(years=(1993, 2021), month_weights=[0.0 if m in WET else 1.0 for m in range(1, 13)]),
    }
    KEY0 = "central (months balanced, 1993-2021, equal years)"
    hp = torch.tensor(h_pass)
    var_res, exp_masks = {}, {}
    for name, kw in {**variants, **extreme}.items():
        kw = dict(kw)
        dd_ = kw.pop("efficiency", d_c)
        e = exceedance(series, hp, **kw)
        m, frac = expected_extent(hist, e, return_frac=True, efficiency=dd_)
        km_frac = [float((frac[t] * D).sum()) * km2 for t in range(T)]
        km_bool = [A(m[t]) for t in range(T)]
        var_res[name] = {"e": _r(e, 4), "efficiency": _r(dd_, 4), "o_percent": _r(100 * dd_ * e, 2), "km2": _r(km_frac),
                         "km2_majority_mask": _r(km_bool), "plausible": name in variants and dd_ != 1.0}
        if name in variants:
            exp_masks[name] = m
        del frac
    # zoned variant: corridor gets the lake level equivalent of the Prek Kdam gauge state
    pre = v26.parse_ffw(LV / "ffw_PRE.txt")
    pre_days = np.array([_days(dt.datetime.fromisoformat(r["date_gmt"])) for r in pre])
    years_tab = sorted(k for k in pre[0] if k.isdigit() and k != "2026")
    lv26 = np.array([r.get("2026") if isinstance(r.get("2026"), float) and r.get("2026") != 0 else np.nan for r in pre])
    PY = np.array([[r.get(y) if isinstance(r.get(y), float) and r.get(y) != 0 else np.nan for y in years_tab] for r in pre])
    pre_pass = np.interp(tp, pre_days[np.isfinite(lv26)], lv26[np.isfinite(lv26)])
    h_equiv, q_pre, q_alt = [], [], []
    for i, t0 in enumerate(tp):
        win = np.abs(pre_days - t0) <= 5
        g = PY[win].ravel()
        yy = np.repeat(np.array([int(y) for y in years_tab])[None], win.sum(), 0).ravel()
        dd = np.repeat(pre_days[win][:, None], len(years_tab), 1).ravel()
        ok = np.isfinite(g)
        q = float(np.mean(g[ok] <= pre_pass[i]))
        # altimeter on the same calendar days of the same years
        al = []
        for y, d0 in zip(yy[ok], dd[ok]):
            day_y = d0 + (dt.datetime(int(y), 1, 1) - dt.datetime(2026, 1, 1)).days
            k = int(round(day_y - float(series["day"][0])))
            if 0 <= k < len(series["h"]) and math.isfinite(float(series["h"][k])):
                al.append(float(series["h"][k]))
        al = np.array(al)
        qa = float(np.mean(al <= h_pass[i]))
        h_equiv.append(float(np.quantile(al, min(max(q, 0.0), 1.0))))
        q_pre.append(q); q_alt.append(qa)
    e_lake = exceedance(series, hp, years=(1993, 2021))
    e_corr = exceedance(series, torch.tensor(h_equiv), years=(1993, 2021))
    fl = hist.frac_ge(100 * d_c * e_lake)
    fc = hist.frac_ge(100 * d_c * e_corr)
    zoned = torch.where(corridor[None], fc, fl)
    zkm = [float((zoned[t] * D).sum()) * km2 for t in range(T)]
    exp_masks["zoned: corridor at Prek Kdam-equivalent level"] = zoned >= 0.5
    var_res["zoned: corridor at Prek Kdam-equivalent level"] = {
        "km2": _r(zkm), "plausible": True, "e_corridor": _r(e_corr, 4), "o_corridor_percent": _r(100 * d_c * e_corr, 2),
        "corridor_equiv_level_m": _r(h_equiv, 2), "prek_kdam_2026_m": _r(pre_pass, 2),
        "prek_kdam_same_day_percentile": _r(q_pre, 3), "altimeter_same_day_percentile_same_years": _r(q_alt, 3),
        "corridor_km2_lake_level": _r([float((fl[t] * D * corridor).sum()) * km2 for t in range(T)]),
        "corridor_km2_gauge_level": _r([float((fc[t] * D * corridor).sum()) * km2 for t in range(T)]),
        "gauge_years": years_tab}
    del fl, fc, zoned; gc.collect()
    plaus = [k for k, v in var_res.items() if v["plausible"]]
    km_tab = np.array([var_res[k]["km2"] for k in plaus])
    exp_c = np.array(var_res[KEY0]["km2"])
    exp_lo, exp_hi = km_tab.min(0), km_tab.max(0)
    R["sampling_variants"] = var_res
    R["expected_km2"] = {"central": _r(exp_c), "lo": _r(exp_lo), "hi": _r(exp_hi),
                         "range_from": plaus, "area_rule": "sum over cells of the fraction of 30 m pixels with occurrence >= o_t"}
    e_c = exceedance(series, hp, years=(1993, 2021))
    o_c = 100 * d_c * e_c
    # leave-years-out (5-year blocks) stability
    lyo = []
    for y0 in range(1993, 2022, 5):
        keep = {y: (0.0 if y0 <= y < y0 + 5 else 1.0) for y in range(1984, 2027)}
        e = exceedance(series, hp, years=(1993, 2021), year_weights=keep)
        f = hist.frac_ge(100 * d_c * e)
        lyo.append({"left_out": f"{y0}-{min(y0 + 4, 2021)}", "o_percent": _r(100 * d_c * e, 2),
                    "km2": _r([float((f[t] * D).sum()) * km2 for t in range(T)])})
        del f
    lk = np.array([r["km2"] for r in lyo])
    R["leave_years_out"] = {"blocks": lyo, "km2_min": _r(lk.min(0)), "km2_max": _r(lk.max(0)),
                            "o_spread_pp": _r(np.ptp(np.array([r["o_percent"] for r in lyo]), 0), 2)}
    # altimeter error +-0.05 m
    e_p = exceedance(series, hp + 0.05, years=(1993, 2021)); e_m = exceedance(series, hp - 0.05, years=(1993, 2021))
    R["altimeter_error_effect_km2"] = _r([abs(float(((hist.frac_ge(100 * d_c * e_m)[t] - hist.frac_ge(100 * d_c * e_p)[t]) * D).sum())) * km2 / 2
                                          for t in range(T)])

    # ---------------------------------------------------------------- hypsometry (expected area vs level)
    hg = torch.linspace(1.5, 11.0, 39, dtype=torch.float64)
    eg = exceedance(series, hg, years=(1993, 2021))
    fg = hist.frac_ge(100 * d_c * eg)
    A_h = np.array([float((fg[i] * D).sum()) * km2 for i in range(len(hg))])
    A_h_box = np.array([float(fg[i].sum()) * km2 for i in range(len(hg))])
    del fg
    A_h_var = []
    for k in plaus:
        if k.startswith("zoned"):
            continue
        kwk = dict(variants[k])
        dk = kwk.pop("efficiency", d_c)
        if dk == 1.0:
            continue
        egk = exceedance(series, hg, **kwk)
        fgk = hist.frac_ge(100 * dk * egk)
        A_h_var.append([float((fgk[i] * D).sum()) * km2 for i in range(len(hg))])
        del fgk
    A_h_var = np.array(A_h_var)
    # altimeter -> Kampong Luong datum from annual extremes
    def era_stats(a, b):
        mins = [ann[y][0] for y in ann if a <= y <= b]
        maxs = [ann[y][1] for y in ann if a <= y <= b]
        return np.mean(mins), np.mean(maxs), np.min(mins), np.max(maxs)
    datum = {}
    for era, (a, b) in {"1996-2009": (1996, 2009), "2010-2019": (2010, 2019)}.items():
        mn, mx, amn, amx = era_stats(a, b)
        datum[era] = {"alt_mean_annual_min": _r(mn, 2), "alt_mean_annual_max": _r(mx, 2),
                      "alt_abs_min": _r(amn, 2), "alt_abs_max": _r(amx, 2),
                      "offset_from_mean_min": _r(mn - KL_STATS[era]["min"], 2),
                      "offset_from_mean_max": _r(mx - KL_STATS[era]["max"], 2),
                      "offset_from_abs_min": _r(amn - KL_STATS[era]["min"], 2),
                      "offset_from_abs_max": _r(amx - KL_STATS[era]["max"], 2)}
    offs = [datum[e][k] for e in datum for k in ("offset_from_mean_min", "offset_from_mean_max")]
    kl_off = float(np.mean(offs))
    Hkl = hg.numpy() - kl_off
    kummu = np.polyval(KUMMU, np.clip(Hkl, 0, None))
    share = fr_ext / box_ext
    ok_rng = (Hkl >= 0.5) & (Hkl <= 9.0)
    # local slopes (km2 per m) over the 2026 range 3.2-9.6 m
    def slope(x, y, a, b):
        s = (x >= a) & (x <= b)
        return float(np.polyfit(x[s], y[s], 1)[0])
    hx = hg.numpy()
    ratio = A_h_box[ok_rng] / kummu[ok_rng]
    R["hypsometry"] = {
        "levels_m_egm2008": _r(hx, 2), "expected_km2_domain": _r(A_h), "expected_km2_whole_box": _r(A_h_box),
        "expected_km2_domain_variant_min": _r(A_h_var.min(0)), "expected_km2_domain_variant_max": _r(A_h_var.max(0)),
        "kummu_2014_whole_lake_km2": _r(kummu), "kummu_equation": "A = -5.5701 H^3 + 137.40 H^2 + 470.29 H + 1680.2 (H at Kampong Luong, m)",
        "kummu_source": "Kummu et al. (2014) Hydrol. Process. 28:1722-1733, as printed in Chua et al. (2022) HESS 26:609-625 Eq. 18",
        "altimeter_to_kampong_luong_offset_m": _r(kl_off, 2), "offset_estimates_m": datum,
        "offset_rule": "mean of (altimeter mean annual min/max) - (Kampong Luong min/max reported by Chua et al. 2022) over 1996-2009 and 2010-2019",
        "frame_share_of_jrc_max_extent": _r(share, 3),
        "kummu_times_frame_share_km2": _r(kummu * share),
        "slope_km2_per_m_3.2_9.6": {"expected_domain": _r(slope(hx, A_h, 3.2, 9.6)),
                                     "expected_box": _r(slope(hx, A_h_box, 3.2, 9.6)),
                                     "kummu_whole_lake": _r(slope(hx, kummu, 3.2, 9.6)),
                                     "kummu_times_share": _r(slope(hx, kummu * share, 3.2, 9.6))},
        "box_expected_over_kummu_ratio": {"min": _r(ratio.min(), 3), "max": _r(ratio.max(), 3),
                                           "at_levels_m": _r(hx[ok_rng], 2), "ratio": _r(ratio, 3)},
    }

    # ---------------------------------------------------------------- DEM consistency (occurrence vs height)
    terr = torch.load(ROOT / f"data/processed/{area}/terrain.pt", weights_only=False)
    dem = terr["dem"].float()
    del terr; gc.collect()
    dem_lake = dem[seed]
    flat_val = float(dem_lake.median())
    flat_mask = (dem - flat_val).abs() <= 0.05
    occ_m = ref["occ_mean"]
    fp = dom & ~seed & ~flat_mask & (occ_m >= 1) & (occ_m < 90)
    bins = [1, 5, 10, 20, 30, 40, 50, 60, 70, 80, 90]
    dem_bins = []
    lvm, cum_ = exceedance(series, None, years=(1993, 2021))
    for a, b in zip(bins[:-1], bins[1:]):
        s = fp & (occ_m >= a) & (occ_m < b)
        dv = dem[s]
        mid = (a + b) / 2
        z_impl = float(level_for_exceedance(series, min(mid / (100 * d_c), 1.0), years=(1993, 2021))[0])
        dem_bins.append({"occ_bin": f"{a}-{b}", "n_px": int(s.sum()), "km2": _r(float(s.sum()) * km2),
                         "dem_median": _r(float(dv.median()) if dv.numel() else None, 2),
                         "dem_p25": _r(float(dv.quantile(0.25)) if dv.numel() else None, 2),
                         "dem_p75": _r(float(dv.quantile(0.75)) if dv.numel() else None, 2),
                         "implied_level_egm2008": _r(z_impl, 2)})
    idx = torch.nonzero(fp.flatten()).flatten()
    sub = idx[torch.randperm(len(idx))[:200000]]
    rho = sstats.spearmanr(occ_m.flatten()[sub].numpy(), dem.flatten()[sub].numpy()).statistic
    # rank of DEM vs implied height (slope of the bin medians)
    zi = np.array([b_["implied_level_egm2008"] for b_ in dem_bins], float)
    dm = np.array([b_["dem_median"] for b_ in dem_bins], float)
    okb = np.isfinite(dm)
    sl = np.polyfit(zi[okb], dm[okb], 1)
    R["dem_check"] = {"floodplain_rule": "domain, not the permanent lake component, not the flattened DEM lake value, 1 <= occurrence < 90",
                      "floodplain_km2": _r(float(fp.sum()) * km2), "spearman_occ_vs_dem": _r(rho, 3),
                      "bins": dem_bins, "fit_dem_median_vs_implied_level": {"slope": _r(sl[0], 3), "intercept_m": _r(sl[1], 2)},
                      "dem_flattened_lake_value_m": _r(flat_val, 2),
                      "dem_bins_monotone_decreasing": bool(np.all(np.diff(dm[okb]) <= 0.05))}

    # ---------------------------------------------------------------- bathtub (Idea 2)
    lowwater = np.mean([ann[y][0] for y in ann if 2011 <= y <= 2015])
    delta = flat_val - lowwater
    ring = ops_dilate(seed, 7) & ~ops_dilate(seed, 3)
    ring_med = float(dem[ring & ~flat_mask].median())
    min5 = -F.max_pool2d(-dem[None, None], 5, 1, 2)[0, 0]
    bt_c = bathtub(dem, torch.tensor(h_pass) + delta, seed)
    bt_km = {"surface model (raw DEM)": [A(bt_c[t]) for t in range(T)]}
    bt_min = bathtub(min5, torch.tensor(h_pass) + delta, seed)
    bt_km["lower envelope (5x5 min of DEM)"] = [A(bt_min[t]) for t in range(T)]
    pert = {}
    for dl in (-0.5, 0.5):
        b = bathtub(dem, torch.tensor(h_pass) + delta + dl, seed)
        pert[f"level {dl:+.1f} m"] = [A(b[t]) for t in range(T)]
        b2 = bathtub(min5, torch.tensor(h_pass) + delta + dl, seed)
        pert[f"lower envelope, level {dl:+.1f} m"] = [A(b2[t]) for t in range(T)]
        del b, b2
    g = torch.Generator().manual_seed(7)
    for k in range(4):
        noise = torch.randn(1, 1, H // 6 + 1, W // 6 + 1, generator=g)
        noise = F.interpolate(noise, size=(H, W), mode="bilinear", align_corners=False)[0, 0]
        noise = noise / noise.std() * 1.2
        b = bathtub(dem + noise, torch.tensor(h_pass) + delta, seed)
        pert[f"DEM noise realisation {k + 1} (sd 1.2 m, ~1 km correlation)"] = [A(b[t]) for t in range(T)]
        del b
    allb = np.array(list(bt_km.values()) + list(pert.values()))
    hb = torch.linspace(1.5, 11.0, 39)
    btc = bathtub(dem, hb + delta, seed)
    A_bt_h = [A(btc[i]) for i in range(len(hb))]
    del btc
    btm = bathtub(min5, hb + delta, seed)
    A_btm_h = [A(btm[i]) for i in range(len(hb))]
    del btm; gc.collect()
    R["bathtub"] = {"seed": "largest connected component of GSW occurrence >= 90 % (the permanent lake)",
                    "dem": "Copernicus GLO-90 on our grid (EGM2008 heights; a surface model)",
                    "dem_flattened_lake_m": _r(flat_val, 2), "altimeter_mean_annual_min_2011_2015_m": _r(lowwater, 2),
                    "datum_offset_added_to_altimeter_m": _r(delta, 2),
                    "ring_3_7px_outside_lake_dem_median_m": _r(ring_med, 2),
                    "km2": {k: _r(v) for k, v in bt_km.items()}, "perturbations_km2": {k: _r(v) for k, v in pert.items()},
                    "km2_lo": _r(allb.min(0)), "km2_hi": _r(allb.max(0)),
                    "curve_levels_m": _r(hb, 2), "curve_km2_raw": _r(A_bt_h), "curve_km2_lower_envelope": _r(A_btm_h)}

    # ---------------------------------------------------------------- judge the detectors
    exp_c_mask = exp_masks[KEY0]
    exp_lo_mask = torch.stack([exp_masks[plaus[int(np.argmin(km_tab[:, t]))]][t] for t in range(T)])
    exp_hi_mask = torch.stack([exp_masks[plaus[int(np.argmax(km_tab[:, t]))]][t] for t in range(T)])
    del exp_masks; gc.collect()

    def compare(mask):
        out = []
        for t in range(T):
            m, e = mask[t] & dom, exp_c_mask[t] & dom
            tp_, fp_, fn_ = float((m & e).sum()), float((m & ~e).sum()), float((~m & e).sum())
            out.append({"km2": _r((tp_ + fp_) * km2), "iou": _r(tp_ / max(tp_ + fp_ + fn_, 1), 3),
                        "share_of_expected_found": _r(tp_ / max(tp_ + fn_, 1), 3),
                        "share_inside_expected": _r(tp_ / max(tp_ + fp_, 1), 3),
                        "only_expected_km2": _r(fn_ * km2), "only_detector_km2": _r(fp_ * km2)})
        return out

    det = {}
    det["ours (math, open water)"] = compare(water_ours)
    fv = torch.load(ROOT / f"data/processed/{area}/floodveg/products.pt", weights_only=False)["flooded_veg"]
    det["ours open water + water under vegetation (floodveg)"] = compare(water_ours | fv)
    fv_km = [A(fv[t] & ~water_ours[t]) for t in range(T)]
    agree_maps = {"fv": fv[[0, 8, 9]].clone()}
    del fv; gc.collect()
    for name, path, key in [("gmm", "gmm", "water"), ("graph", "graph", "water_graph"), ("fusion", "fusion", "water"),
                            ("supervised", "supervised", "water")]:
        pth = ROOT / f"data/processed/{area}/{path}/products.pt"
        if not pth.exists():
            continue
        d = torch.load(pth, weights_only=False, map_location="cpu")
        det[name] = compare(d[key])
        if name == "gmm":
            det["gmm water + flooded veg"] = compare(d["water"] | d["flooded_veg"])
        del d; gc.collect()
    sp = ROOT / f"data/processed/{area}/subpixel/products.pt"
    if sp.exists():
        d = torch.load(sp, weights_only=False, map_location="cpu")
        fr = d["frac"].float()
        det["subpixel (fraction >= 0.5)"] = compare(fr >= 0.5)
        R["subpixel_fraction_km2"] = _r([float((fr[t] * D).sum()) * km2 for t in range(T)])
        del d, fr; gc.collect()
    R["detectors_vs_expected"] = det
    R["flooded_veg_only_km2"] = _r(fv_km)
    # lower bound: shortfall per date
    R["shortfall_ours_vs_expected"] = {
        "km2": _r(exp_c - np.array(ours_km2)), "ratio_expected_over_ours": _r(exp_c / np.array(ours_km2), 3),
        "ratio_lo": _r(exp_lo / np.array(ours_km2), 3), "ratio_hi": _r(exp_hi / np.array(ours_km2), 3)}
    # where does late-season growth go? pixels new on 17 Oct vs 5 Oct
    new = water_ours[9] & ~water_ours[8] & dom
    gone = water_ours[8] & ~water_ours[9] & dom
    fvm = agree_maps["fv"]
    R["late_growth_5_to_17_Oct"] = {
        "new_open_water_km2": _r(float(new.sum()) * km2), "lost_open_water_km2": _r(float(gone.sum()) * km2),
        "new_that_was_flooded_veg_on_5_Oct_km2": _r(float((new & fvm[1]).sum()) * km2),
        "new_inside_expected_17_Oct_km2": _r(float((new & exp_c_mask[9]).sum()) * km2),
        "new_inside_expected_5_Oct_km2": _r(float((new & exp_c_mask[8]).sum()) * km2),
        "new_median_occurrence": _r(float(occ_m[new].median()), 1),
        "new_in_corridor_share": _r(float((new & corridor).sum()) / max(float(new.sum()), 1), 3),
        "expected_change_km2": _r(exp_c[9] - exp_c[8]), "level_change_m": _r(h_pass[9] - h_pass[8], 2)}

    # ---------------------------------------------------------------- threshold calibration
    from scripts.detect import tune
    b = tune.base(area)
    L = tune.filtered(b, 3, True, "cpu")
    v = b["valid"]
    del b; gc.collect()
    grid_w = np.round(np.arange(0.03, 0.3001, 0.01), 3)
    BS = 20
    nby, nbx = math.ceil(H / BS), math.ceil(W / BS)

    def blocks(m):  # [T,H,W] -> [T, nb] pixel counts per 20x20 block (domain only)
        x = (m & dom).float()[:, None]
        x = F.pad(x, (0, nbx * BS - W, 0, nby * BS - H))
        return (F.avg_pool2d(x, BS) * BS * BS)[:, 0].reshape(m.shape[0], -1)

    exp_blk = blocks(exp_c_mask)
    lo_blk, hi_blk = blocks(exp_lo_mask), blocks(exp_hi_mask)
    fam = {"ratio (strong = weak/2)": lambda w: (w / 2, w), "fixed strong 0.06": lambda w: (min(0.06, w), w)}
    calib = {}
    det_blk_store = {}
    for fname, fn in fam.items():
        areas, ious, blk = [], [], []
        for w in grid_w:
            s_, w2 = fn(float(w))
            wm = tune.water_fast(L, v, s_, w2, 0.35, 0.0, 4)
            areas.append([A(wm[t]) for t in range(T)])
            ious.append([float((wm[t] & exp_c_mask[t] & dom).sum()) / max(float(((wm[t] | exp_c_mask[t]) & dom).sum()), 1)
                         for t in range(T)])
            blk.append(blocks(wm))
            del wm
        areas, ious = np.array(areas), np.array(ious)
        blk = torch.stack(blk)  # [n_w, T, nb]
        det_blk_store[fname] = blk
        res = {"thresholds_weak": _r(grid_w, 3), "area_km2": [_r(a) for a in areas], "iou": [_r(a, 3) for a in ious]}
        allc = calibrate_threshold(grid_w, areas, exp_c)
        res["fit_all_dates"] = _r(allc["best"], 4)
        res["per_date_match"] = _r(allc["per_date"], 4)
        res["fit_to_lo"] = _r(calibrate_threshold(grid_w, areas, exp_lo)["best"], 4)
        res["fit_to_hi"] = _r(calibrate_threshold(grid_w, areas, exp_hi)["best"], 4)
        res["best_mean_iou"] = {"weak": _r(grid_w[int(ious.mean(1).argmax())], 3), "iou": _r(ious.mean(1).max(), 3),
                                "iou_at_old": _r(ious.mean(1)[int(np.argmin(np.abs(grid_w - OLD_WEAK)))], 3)}
        # split tests
        splits = {}
        ev, od = np.arange(0, T, 2), np.arange(1, T, 2)
        for nm, (fi, te) in {"fit even dates, test odd": (ev, od), "fit odd dates, test even": (od, ev),
                             "fit first 5, test last 5": (np.arange(5), np.arange(5, 10)),
                             "fit last 5, test first 5": (np.arange(5, 10), np.arange(5))}.items():
            c = calibrate_threshold(grid_w, areas, exp_c, fit=fi)
            Af = np.array([np.interp(c["best"], grid_w, areas[:, t]) for t in range(T)])
            Ao = areas[int(np.argmin(np.abs(grid_w - OLD_WEAK)))]
            splits[nm] = {"fitted": _r(c["best"], 4), "test_rmse_km2": _r(np.sqrt(np.mean((Af[te] - exp_c[te]) ** 2))),
                          "test_rmse_old_km2": _r(np.sqrt(np.mean((Ao[te] - exp_c[te]) ** 2)))}
        # spatial: west / east halves of blocks
        bx = torch.arange(nby * nbx) % nbx
        west, east = bx < nbx // 2, bx >= nbx // 2
        for nm, (fi, te) in {"fit west half, test east": (west, east), "fit east half, test west": (east, west)}.items():
            Afit = (blk[:, :, fi].sum(-1) * km2).numpy()
            Atest = (blk[:, :, te].sum(-1) * km2).numpy()
            tg_f = (exp_blk[:, fi].sum(-1) * km2).numpy()
            tg_t = (exp_blk[:, te].sum(-1) * km2).numpy()
            c = calibrate_threshold(grid_w, Afit, tg_f)
            At = np.array([np.interp(c["best"], grid_w, Atest[:, t]) for t in range(T)])
            Ao = Atest[int(np.argmin(np.abs(grid_w - OLD_WEAK)))]
            splits[nm] = {"fitted": _r(c["best"], 4), "test_rmse_km2": _r(np.sqrt(np.mean((At - tg_t) ** 2))),
                          "test_rmse_old_km2": _r(np.sqrt(np.mean((Ao - tg_t) ** 2)))}
        res["splits"] = splits
        # bootstrap over dates and 20x20 blocks
        gen = np.random.default_rng(20261114)
        nb = blk.shape[-1]
        bl = blk.double()
        eb = exp_blk.double()
        boots = []
        for _ in range(1000):
            ti = gen.integers(0, T, T)
            bi = torch.from_numpy(np.bincount(gen.integers(0, nb, nb), minlength=nb)).double()
            Ad = (bl @ bi).numpy() * km2          # [n_w, T]
            tg = (eb @ bi).numpy() * km2
            boots.append(calibrate_threshold(grid_w, Ad[:, ti], tg[ti], fine=561)["best"])
        boots = np.array(boots)
        res["bootstrap"] = {"n": 1000, "median": _r(np.median(boots), 4), "p2.5": _r(np.quantile(boots, 0.025), 4),
                            "p97.5": _r(np.quantile(boots, 0.975), 4), "sd": _r(boots.std(), 4)}
        # implied areas at the fitted threshold (interpolated)
        res["implied_km2_at_fit"] = _r([np.interp(allc["best"], grid_w, areas[:, t]) for t in range(T)])
        res["implied_km2_at_ci"] = {"lo": _r([np.interp(res["bootstrap"]["p2.5"], grid_w, areas[:, t]) for t in range(T)]),
                                    "hi": _r([np.interp(res["bootstrap"]["p97.5"], grid_w, areas[:, t]) for t in range(T)])}
        res["sse_curve"] = {"weak": _r(allc["sse_curve"][0][::40], 4), "rmse_km2": _r(np.sqrt(allc["sse_curve"][1][::40] / T))}
        calib[fname] = res
        print(f"  calibration {fname}: fit {allc['best']:.4f} boot {res['bootstrap']}")
    R["calibration"] = calib
    w_fit = calib["ratio (strong = weak/2)"]["fit_all_dates"]
    w_ci = (calib["ratio (strong = weak/2)"]["bootstrap"]["p2.5"], calib["ratio (strong = weak/2)"]["bootstrap"]["p97.5"])
    # maps of the fitted-threshold detection for 3 dates
    wfit_mask = tune.water_fast(L, v, w_fit / 2, w_fit, 0.35, 0.0, 4)
    R["fitted_vs_expected"] = compare(wfit_mask)
    # per-terrain rule: fitted threshold only inside the 1984-2021 maximum water extent, 0.12 elsewhere
    old_fast = tune.water_fast(L, v, OLD_STRONG, OLD_WEAK, 0.35, 0.0, 4)
    extm = ref["ext_frac"] > 0
    rule = old_fast | (wfit_mask & extm[None])
    R["per_terrain_rule"] = {"rule": f"water = water(0.06/0.12) OR (water(weak {w_fit:.3f}, strong weak/2) AND inside GSW maximum extent)",
                             "tonle_sap_km2": _r([A(rule[t]) for t in range(T)]),
                             "tonle_sap_fitted_outside_max_extent_km2": _r([A(wfit_mask[t] & ~extm) for t in range(T)]),
                             "tonle_sap_old_fast_km2": _r([A(old_fast[t]) for t in range(T)])}
    del old_fast, rule
    # lower bound for TOTAL inundation: union of radar open water, radar water under vegetation, expected set
    fv_full = torch.load(ROOT / f"data/processed/{area}/floodveg/products.pt", weights_only=False)["flooded_veg"]
    R["union_open_fv_expected_km2"] = _r([A(water_ours[t] | fv_full[t] | exp_c_mask[t]) for t in range(T)])
    del fv_full; gc.collect()
    # Kummu curve at the pass levels (whole lake and x frame share), with the datum-offset spread
    offs_all = [datum[e][k] for e in datum for k in ("offset_from_mean_min", "offset_from_mean_max")]
    kp = {f"offset {o:.2f} m": _r(np.polyval(KUMMU, np.clip(h_pass - o, 0, None)) * share) for o in [kl_off] + sorted(offs_all)}
    R["kummu_times_share_at_pass_km2"] = kp
    del L, det_blk_store; gc.collect()

    # ---------------------------------------------------------------- Sevan + Armenia check
    arm = {}
    sev_curve = torch.load(ROOT / "data/processed/tonle-sap/stats/products.pt", weights_only=False)["sevan_interval"]
    gs, ca = np.array(sev_curve["grid"]), np.array(sev_curve["calm_area_km2"])
    tol = float(sev_curve["tol_km2"])
    arm["sevan_curve_from_stats_change"] = {"weak": _r(gs, 4), "calm_mean_km2": _r(ca), "tol_km2": _r(tol)}
    for nm, wv in {"old 0.12": OLD_WEAK, "fitted": w_fit, "fit CI lo": w_ci[0], "fit CI hi": w_ci[1], "otsu2 0.24": OTSU2}.items():
        arm.setdefault("sevan_calm_mean_km2_interp", {})[nm] = {"weak": _r(wv, 4), "km2": _r(np.interp(wv, gs, ca)) if wv <= gs.max() else None,
                                                                 "inside_tolerance": bool(abs(np.interp(wv, gs, ca) - SEVAN_KM2) <= tol) if wv <= gs.max() else False}
    test_w = sorted(set([OLD_WEAK, round(w_fit, 4), round(w_ci[0], 4), round(w_ci[1], 4), OTSU2]))
    for a2 in ("sevan", "ararat"):
        try:
            b2 = tune.base(a2)
        except Exception as ex:  # noqa: BLE001
            arm[a2] = {"error": str(ex)}
            continue
        L2 = tune.filtered(b2, 3, True, "cpu")
        lab2 = tune.build_labels(b2["ref"], b2["valid"], **tune.LABEL_RULE)
        k2 = b2["km2"]
        out2 = {}
        for wv in test_w:
            wm = tune.water_fast(L2, b2["valid"], wv / 2, wv, 0.35, 0.0, 4)
            tot = (wm & b2["valid"][None]).sum((1, 2)).double() * k2
            never = (wm & lab2["never"][None]).sum((1, 2)).double() * k2
            calm = [t for t in range(wm.shape[0]) if not (a2 == "sevan" and t in SEVAN_WINDY)]
            if abs(wv - w_fit) < 1e-6:
                old2 = tune.water_fast(L2, b2["valid"], OLD_STRONG, OLD_WEAK, 0.35, 0.0, 4)
                ext2 = b2["ref"]["ext_frac"] > 0
                r2_ = old2 | (wm & ext2[None])
                tr = (r2_ & b2["valid"][None]).sum((1, 2)).double() * k2
                nr_ = (r2_ & lab2["never"][None]).sum((1, 2)).double() * k2
                calm_ = [t for t in range(wm.shape[0]) if not (a2 == "sevan" and t in SEVAN_WINDY)]
                arm.setdefault("per_terrain_rule", {})[a2] = {"total_km2_per_date": _r(tr), "never_water_km2_per_date": _r(nr_),
                                                              "total_calm_mean_km2": _r(float(tr[calm_].mean())),
                                                              "never_water_mean_km2": _r(float(nr_.mean()))}
                del old2, r2_
            out2[f"{wv:.4f}"] = {"total_km2_per_date": _r(tot), "never_water_km2_per_date": _r(never),
                                 "total_calm_mean_km2": _r(float(tot[calm].mean())),
                                 "never_water_mean_km2": _r(float(never.mean()))}
            del wm
        arm[a2] = out2
        del b2, L2, lab2; gc.collect()
    R["armenia_check"] = arm

    # ---------------------------------------------------------------- headline
    t17 = 9
    R["headline_17_Oct"] = {
        "detector_open_water_km2": _r(ours_km2[t17]),
        "detector_open_plus_fv_km2": _r(ours_km2[t17] + fv_km[t17]),
        "expected_central_km2": _r(exp_c[t17]), "expected_lo_km2": _r(exp_lo[t17]), "expected_hi_km2": _r(exp_hi[t17]),
        "expected_extreme_sampling_km2": {k: var_res[k]["km2"][t17] for k in extreme},
        "fitted_threshold_km2": calib["ratio (strong = weak/2)"]["implied_km2_at_fit"][t17],
        "fitted_threshold_ci_km2": [calib["ratio (strong = weak/2)"]["implied_km2_at_ci"]["lo"][t17],
                                    calib["ratio (strong = weak/2)"]["implied_km2_at_ci"]["hi"][t17]],
        "bathtub_raw_km2": _r(bt_km["surface model (raw DEM)"][t17]),
        "bathtub_lower_envelope_km2": _r(bt_km["lower envelope (5x5 min of DEM)"][t17]),
        "bathtub_lo_km2": _r(allb.min(0)[t17]), "bathtub_hi_km2": _r(allb.max(0)[t17]),
        "union_open_fv_expected_km2": R["union_open_fv_expected_km2"][t17],
        "kummu_times_share_km2": {k: v[t17] for k, v in R["kummu_times_share_at_pass_km2"].items()},
        "per_terrain_rule_km2": R["per_terrain_rule"]["tonle_sap_km2"][t17],
    }
    R["pass_dates"] = dates

    # ---------------------------------------------------------------- products
    PD = ROOT / f"data/processed/{area}/hydro"
    PD.mkdir(parents=True, exist_ok=True)
    torch.save({"expected": exp_c_mask, "expected_lo": exp_lo_mask, "expected_hi": exp_hi_mask,
                "expected_km2": torch.tensor(exp_c), "expected_km2_lo": torch.tensor(exp_lo), "expected_km2_hi": torch.tensor(exp_hi),
                "bathtub": bt_c, "bathtub_km2": torch.tensor(bt_km["surface model (raw DEM)"]),
                "bathtub_km2_lo": torch.tensor(allb.min(0)), "bathtub_km2_hi": torch.tensor(allb.max(0)),
                "level_m_egm2008": torch.tensor(h_pass), "exceedance": e_c, "occ_threshold_percent": o_c,
                "fitted_weak_threshold": w_fit, "fitted_weak_threshold_ci": w_ci, "fitted_strong_rule": "weak/2",
                "domain": dom, "dates": dates, "bounds": bounds, "km2_per_px": km2,
                "note": "expected = cells where >= 50 % of 30 m GSW pixels have occurrence >= 100*e(h_t) (central "
                        "sampling: months balanced, 1993-2021 altimetry); lo/hi = smallest/largest plausible "
                        "sampling variant per date. See docs/eda/hydro.md."}, PD / "products.pt")

    # ---------------------------------------------------------------- figures
    figures(R, series, at, ah, h_pass, e_c, passes, dates, exp_c, exp_lo, exp_hi, ours_km2, fv_km, bt_km, allb,
            A_h, A_h_var, hx, kummu, share, A_bt_h, A_btm_h, exp_c_mask, water_ours, wfit_mask, dom, bounds, calib,
            arm, var_res, extreme, w_fit, w_ci, tol, gs, ca, dem_bins, corridor)
    R["figures"] = FIGS
    R["sources"] = SOURCES
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(R, indent=1, default=str))
    print("wrote", OUT / "results.json")


def ops_dilate(m, k):
    return F.max_pool2d(m.float()[None, None], k, 1, k // 2)[0, 0] > 0


SOURCES = [
    {"name": "JRC Global Surface Water v1.4 (1984-2021): occurrence, maximum extent, seasonality",
     "citation": "Pekel, Cottam, Gorelick, Belward (2016) Nature 540:418-422", "attribution": "Source: EC JRC/Google",
     "url": "https://storage.googleapis.com/global-surface-water/downloads2021/", "cache": "data/external/gsw/tonle-sap_*.tif"},
    {"name": "JRC GSW Data Users Guide v2 (2021): occurrence = mean of monthly occurrences",
     "url": "https://storage.googleapis.com/global-surface-water/downloads_ancillary/DataUsersGuidev2021.pdf", "accessed": "2026-11-14"},
    {"name": "G-REALM Tonle Sap target 000319, 10-day radar altimetry 1992-2026 (TOPEX/Poseidon, Jason-1/2/3, Sentinel-6A MF)",
     "provider": "NASA GSFC Global Water Monitor / USDA FAS; Birkett et al.",
     "url": "https://earth.gsfc.nasa.gov/gwm/timeseries/lake000319.10d.2.txt", "cache": "data/external/levels/gwm_lake000319.10d.2.txt"},
    {"name": "MRC daily water level, Prek Kdam (Tonle Sap River), 2026 and listed past years",
     "provider": "Mekong River Commission flood forecasting (c) MRC", "url": "https://ffw.mrcmekong.org/fetchwet_st.php?StCode=PRE",
     "cache": "data/external/levels/ffw_PRE.txt"},
    {"name": "Copernicus DEM GLO-90", "attribution": "(c) DLR e.V. 2010-2014 and (c) Airbus Defence and Space GmbH 2014-2018, provided under COPERNICUS by the European Union and ESA",
     "cache": "data/processed/tonle-sap/terrain.pt"},
    {"name": "Tonle Sap area-level relation", "citation": "Kummu et al. (2014) Water balance analysis for the Tonle Sap Lake-floodplain system. Hydrological Processes 28:1722-1733; equation as printed in Chua et al. (2022) HESS 26:609-625, Eq. 18",
     "url": "https://hess.copernicus.org/articles/26/609/2022/", "accessed": "2026-11-14"},
]


# ====================================================================== figures
def figures(R, series, at, ah, h_pass, e_c, passes, dates, exp_c, exp_lo, exp_hi, ours_km2, fv_km, bt_km, allb,
            A_h, A_h_var, hx, kummu, share, A_bt_h, A_btm_h, exp_mask, water_ours, wfit_mask, dom, bounds, calib,
            arm, var_res, extreme, w_fit, w_ci, tol, gs, ca, dem_bins, corridor):
    pt = [T_.replace(tzinfo=None) for _, T_ in passes]
    lab = [dt.date.fromisoformat(d).strftime("%-d %b") for d in dates]

    # 1 level record + exceedance
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 4.0), gridspec_kw={"width_ratios": [1.7, 1]})
    style(a1); style(a2)
    hd, yr = series["h"].numpy(), series["year"].numpy()
    day = series["day"].numpy()
    for y in range(1993, 2026):
        s = yr == y
        doy = day[s] - _days(dt.datetime(y, 1, 1))
        a1.plot(doy, hd[s], color=C["ink2"], lw=0.5, alpha=0.25)
    s21 = (yr >= 1993) & (yr <= 2021)
    doy_all = np.array([(EPOCH + dt.timedelta(days=float(d))).timetuple().tm_yday for d in day[s21]])
    clim = [np.nanmean(hd[s21][doy_all == k]) for k in range(1, 366)]
    a1.plot(np.arange(1, 366), clim, color=C["ink"], lw=1.8, label="mean 1993-2021")
    s26 = at >= _days(dt.datetime(2026, 1, 1))
    a1.plot(at[s26] - _days(dt.datetime(2026, 1, 1)), ah[s26], "o-", color=C["s2"], ms=3.5, lw=1.2, label="2026 (Sentinel-6A)")
    pdoy = [_days(T_) - _days(dt.datetime(2026, 1, 1)) for T_ in pt]
    a1.plot(pdoy, h_pass, "D", color=C["s1"], ms=6, mec=C["surf"], label="radar pass dates (interpolated)", zorder=4)
    for i, off in ((0, (-30, 10)), (3, (-38, 6)), (4, (-40, 4)), (6, (-42, 4)), (8, (-10, 10)), (9, (6, -14))):
        a1.annotate(f"{lab[i]}  e={e_c[i]:.2f}", (pdoy[i], h_pass[i]), xytext=off,
                    textcoords="offset points", fontsize=6.5, color=C["s1"])
    a1.set_xticks([1, 60, 121, 182, 244, 305], ["Jan", "Mar", "May", "Jul", "Sep", "Nov"])
    a1.set_ylabel("lake level, EGM2008 (m)", fontsize=8.5, color=C["ink2"])
    a1.set_title("Tonle Sap lake level 1993-2025 (grey, one line per year) and 2026", fontsize=9.5, loc="left")
    a1.legend(fontsize=7, frameon=False, loc="upper left")
    lv, cum = exceedance(series, None, years=(1993, 2021))
    a2.plot(cum.numpy(), lv.numpy(), color=C["ink"], lw=1.6, label="months balanced (central)")
    for k, col in (("era 1993-2009 only", C["s3"]), ("era 2010-2021 only", C["s4"]),
                   ("no monthly balancing (clear-sky weighted)", C["s6"])):
        kw = {"era 1993-2009 only": dict(years=(1993, 2009)), "era 2010-2021 only": dict(years=(2010, 2021)),
              "no monthly balancing (clear-sky weighted)": dict(years=(1993, 2021), month_weights=CLEAR_SKY)}[k]
        l2, c2 = exceedance(series, None, **kw)
        a2.plot(c2.numpy(), l2.numpy(), color=col, lw=1.0, ls="--", label=k)
    a2.plot(e_c.numpy(), h_pass, "D", color=C["s1"], ms=5, mec=C["surf"], zorder=4)
    a2.set_xlabel("exceedance e(h) = P(level >= h)", fontsize=8.5, color=C["ink2"])
    a2.set_title("How often the lake reaches each level", fontsize=9.5, loc="left")
    a2.legend(fontsize=6.3, frameon=False, loc="upper right")
    savefig(fig, "fig1_level_record_exceedance.png")

    # 2 area vs level
    fig, ax = plt.subplots(figsize=(8.4, 4.6))
    style(ax)
    k3 = hx >= 3.0  # below the lowest annual minimum the mapping saturates (occurrence ~ 100 d)
    ax.fill_between(hx[k3], A_h_var.min(0)[k3], A_h_var.max(0)[k3], color=C["s1"], alpha=0.15, lw=0)
    ax.plot(hx[k3], A_h[k3], color=C["s1"], lw=2, label="expected from occurrence + level record (band: sampling variants)")
    hb = np.array(R["bathtub"]["curve_levels_m"])
    ax.plot(hb, A_bt_h, color=C["s4"], lw=1.6, label="bathtub, raw DEM (surface model)")
    ax.plot(hb, A_btm_h, color=C["s4"], lw=1.2, ls="--", label="bathtub, DEM lower envelope (5x5 min)")
    ax.plot(hx, kummu * share, color=C["ink2"], lw=1.4, ls=":",
            label=f"Kummu et al. 2014 whole-lake curve x frame share {share:.2f} (crude)")
    ax.plot(h_pass, ours_km2, "o", color=C["s3"], ms=6, mec=C["surf"], label="radar open water (ours), 10 passes")
    ax.plot(h_pass, np.array(ours_km2) + np.array(fv_km), "s", color=C["s5"], ms=5, mec=C["surf"],
            label="radar open water + water under vegetation")
    for i in (0, 7, 8, 9):
        ax.annotate(lab[i], (h_pass[i], ours_km2[i]), xytext=(4, -11), textcoords="offset points", fontsize=7, color=C["ink2"])
    ax.set_xlabel("lake level, altimeter EGM2008 (m)", fontsize=8.5, color=C["ink2"])
    ax.set_ylabel("area inside our radar footprint (km2)", fontsize=8.5, color=C["ink2"])
    ax.set_xlim(3.0, 11)
    ax.set_title("Area against lake level inside our frame (levels below 3 m: lake at its dry-season floor)", fontsize=9.5, loc="left")
    ax.legend(fontsize=7, frameon=False, loc="upper left")
    savefig(fig, "fig2_area_vs_level.png")

    # 3 maps: early, peak, last
    w_, s_, e_, n_ = bounds
    fig, axs = plt.subplots(1, 3, figsize=(12, 4.6))
    for ax, t in zip(axs, (0, 8, 9)):
        e, o, d = exp_mask[t].numpy(), water_ours[t].numpy(), dom.numpy()
        img = np.ones(e.shape + (3,)) * 0.94
        img[~d] = [0.80, 0.80, 0.80]
        img[e & o & d] = [0.16, 0.42, 0.80]
        img[e & ~o & d] = [0.93, 0.55, 0.20]
        img[~e & o & d] = [0.60, 0.25, 0.65]
        ax.imshow(img[::2, ::2], extent=(w_, e_, s_, n_), interpolation="nearest")
        dd = R["detectors_vs_expected"]["ours (math, open water)"][t]
        ax.set_title(f"{lab[t]}: level {h_pass[t]:.2f} m, occurrence >= {float(R['sampling_variants'][list(R['sampling_variants'])[0]]['o_percent'][t]):.0f} %\n"
                     f"IoU {dd['iou']:.2f}; only expected {dd['only_expected_km2']:.0f} km2;\nonly radar {dd['only_detector_km2']:.0f} km2", fontsize=8)
        ax.set_xticks([103.5, 104.0, 104.5, 105.0])
        ax.tick_params(labelsize=7)
    axs[0].legend(handles=[Patch(color=[0.16, 0.42, 0.80], label="both"), Patch(color=[0.93, 0.55, 0.20], label="only expected (level + occurrence)"),
                           Patch(color=[0.60, 0.25, 0.65], label="only radar open water"), Patch(color=[0.80, 0.80, 0.80], label="outside footprint")],
                  fontsize=6.5, loc="lower left", framealpha=0.9)
    fig.suptitle("Expected inundation from the level record vs our radar open water. Occurrence: EC JRC/Google", fontsize=9)
    savefig(fig, "fig3_expected_vs_detected_maps.png", dpi=90)

    # 4 area per date, all estimates
    fig, ax = plt.subplots(figsize=(9, 4.6))
    style(ax)
    ax.axvspan(*GAP, color=C["hl"], zorder=0)
    ax.fill_between(pt, exp_lo, exp_hi, color=C["s1"], alpha=0.18, lw=0)
    ax.plot(pt, exp_c, "-o", color=C["s1"], lw=2, ms=5, label="expected from level + occurrence (band: sampling variants)")
    ax.fill_between(pt, allb.min(0), allb.max(0), color=C["s4"], alpha=0.15, lw=0)
    ax.plot(pt, bt_km["surface model (raw DEM)"], "-^", color=C["s4"], lw=1.4, ms=5, label="bathtub, raw DEM (band: level +-0.5 m, DEM noise, envelope)")
    ax.plot(pt, ours_km2, "-o", color=C["s3"], lw=2, ms=5, label="radar open water (ours, 0.06/0.12)")
    ax.plot(pt, np.array(ours_km2) + np.array(fv_km), "-s", color=C["s5"], lw=1.4, ms=4, label="radar open water + water under vegetation")
    cr = calib["ratio (strong = weak/2)"]
    ax.fill_between(pt, cr["implied_km2_at_ci"]["lo"], cr["implied_km2_at_ci"]["hi"], color=C["s6"], alpha=0.12, lw=0)
    ax.plot(pt, cr["implied_km2_at_fit"], "--", color=C["s6"], lw=1.4, label=f"radar open water at fitted threshold {w_fit:.3f}")
    ax.text(GAP[0], ax.get_ylim()[0] + 150, " no passes", fontsize=7, color=C["ink2"], va="bottom")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%-d %b"))
    ax.set_ylabel("km2 inside our footprint", fontsize=8.5, color=C["ink2"])
    ax.set_title("Tonle Sap 2026: flooded area per radar date, all estimates", fontsize=9.5, loc="left")
    ax.legend(fontsize=6.8, frameon=False, loc="upper left")
    savefig(fig, "fig4_area_per_date.png")

    # 5 calibration
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10.5, 4.0))
    style(a1); style(a2)
    gw = np.array(cr["thresholds_weak"])
    Aw = np.array(cr["area_km2"])
    for t, col in zip((0, 4, 7, 9), (C["s3"], C["s4"], C["s2"], C["s5"])):
        a1.plot(gw, Aw[:, t], color=col, lw=1.4, label=f"radar area {lab[t]}")
        a1.axhline(exp_c[t], color=col, lw=0.9, ls=":")
    a1.axvline(OLD_WEAK, color=C["ink2"], lw=1, ls="--"); a1.text(OLD_WEAK, a1.get_ylim()[1] * 0.98, " current 0.12", fontsize=7, va="top")
    a1.axvspan(*SEVAN_INTERVAL, color=C["hl"], zorder=0)
    a1.axvline(OTSU2, color=C["ink2"], lw=1, ls="-."); a1.text(OTSU2, a1.get_ylim()[0] + 200, " Otsu-2 0.24", fontsize=7)
    a1.set_xlabel("weak HH threshold (strong = weak/2)", fontsize=8.5, color=C["ink2"])
    a1.set_ylabel("km2", fontsize=8.5, color=C["ink2"])
    a1.set_title("Detected area vs threshold (lines); expected (dotted)\nshaded: Sevan-compatible 0.045-0.160", fontsize=9, loc="left")
    a1.legend(fontsize=6.5, frameon=False, loc="upper left")
    sc = cr["sse_curve"]
    a2.plot(sc["weak"], sc["rmse_km2"], color=C["s1"], lw=1.8, label="all dates")
    a2.axvspan(cr["bootstrap"]["p2.5"], cr["bootstrap"]["p97.5"], color=C["s1"], alpha=0.15, label="fit 95 % bootstrap")
    a2.axvline(w_fit, color=C["s1"], lw=1)
    a2.axvline(OLD_WEAK, color=C["ink2"], lw=1, ls="--", label="current 0.12")
    a2.axvline(OTSU2, color=C["ink2"], lw=1, ls="-.", label="Otsu-2 0.24")
    pdm = [x for x in cr["per_date_match"]]
    for i, x in enumerate(pdm):
        if x is not None:
            a2.plot(x, 120 + 95 * i, "o", color=C["s2"], ms=4)
            a2.text(x, 120 + 95 * i, f" {lab[i]}", fontsize=6, va="center", color=C["s2"])
    a2.set_ylim(0, max(sc["rmse_km2"]) * 1.02)
    a2.set_xlabel("weak HH threshold", fontsize=8.5, color=C["ink2"])
    a2.set_ylabel("RMSE vs expected area (km2)", fontsize=8.5, color=C["ink2"])
    a2.set_title("Area mismatch; orange dots: threshold that matches each date alone", fontsize=9, loc="left")
    a2.legend(fontsize=6.5, frameon=False, loc="upper right")
    savefig(fig, "fig5_threshold_calibration.png")

    # 6 sampling sensitivity
    fig, ax = plt.subplots(figsize=(9, 4.2))
    style(ax)
    ax.axvspan(*GAP, color=C["hl"], zorder=0)
    cols = [C["s1"], C["s3"], C["s4"], C["s2"], C["s5"], "#5b8fd0", "#7fc8a9", "#bbbbbb", C["s6"], "#999999", C["ink2"]]
    for (k, vv), col in zip(var_res.items(), cols):
        ax.plot(pt, vv["km2"], "-o" if vv["plausible"] else ":x", color=col, lw=1.8 if k.startswith("central") else 1.0, ms=3.5,
                label=k + ("" if vv["plausible"] else "  [not in range: see text]"))
    ax.plot(pt, ours_km2, "-", color=C["ink"], lw=1, alpha=0.6, label="radar open water (ours), for scale")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%-d %b"))
    ax.set_ylabel("expected km2 inside our footprint", fontsize=8.5, color=C["ink2"])
    ax.set_title("Sensitivity of the expected area to the sampling assumption", fontsize=9.5, loc="left")
    ax.legend(fontsize=6.3, frameon=False, loc="upper left")
    savefig(fig, "fig6_sampling_sensitivity.png")

    # 7 Sevan + Armenia
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10.5, 3.8))
    style(a1); style(a2)
    a1.plot(gs, ca, color=C["s1"], lw=1.8, label="Sevan, mean area on calm dates (stats-change curve)")
    a1.axhspan(SEVAN_KM2 - tol, SEVAN_KM2 + tol, color=C["hl"], zorder=0, label="1,242 km2 +- 1 shoreline pixel")
    a1.axvline(OLD_WEAK, color=C["ink2"], ls="--", lw=1, label="current 0.12")
    a1.axvspan(w_ci[0], w_ci[1], color=C["s6"], alpha=0.15, label="Tonle Sap fitted threshold, 95 % CI")
    a1.axvline(w_fit, color=C["s6"], lw=1.2)
    ptr = arm.get("per_terrain_rule", {}).get("sevan")
    if ptr:
        a1.plot(w_fit, ptr["total_calm_mean_km2"], "*", color=C["s3"], ms=11, mec=C["surf"],
                label="per-terrain rule (fitted value only inside GSW max extent)")
    a1.set_xlabel("weak HH threshold (strong = weak/2)", fontsize=8.5, color=C["ink2"])
    a1.set_ylabel("km2", fontsize=8.5, color=C["ink2"])
    a1.set_title("Lake Sevan area", fontsize=9.5, loc="left")
    a1.legend(fontsize=6.5, frameon=False, loc="upper left")
    for a2n, col in (("sevan", C["s1"]), ("ararat", C["s2"])):
        if "error" in arm.get(a2n, {"error": 1}):
            continue
        ks = sorted(arm[a2n], key=float)
        a2.plot([float(k) for k in ks], [arm[a2n][k]["never_water_mean_km2"] for k in ks], "-o", color=col, lw=1.6,
                label=f"{a2n}: water on never-water land (km2 per date)")
    a2.axvline(OLD_WEAK, color=C["ink2"], ls="--", lw=1)
    a2.axvspan(w_ci[0], w_ci[1], color=C["s6"], alpha=0.15)
    a2.axvline(w_fit, color=C["s6"], lw=1.2)
    a2.set_xlabel("weak HH threshold", fontsize=8.5, color=C["ink2"])
    a2.set_title("False water in Armenia", fontsize=9.5, loc="left")
    a2.legend(fontsize=6.5, frameon=False, loc="upper left")
    savefig(fig, "fig7_threshold_at_sevan_armenia.png")

    # 8 DEM consistency
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    style(ax)
    zi = [b["implied_level_egm2008"] for b in dem_bins]
    md = [b["dem_median"] for b in dem_bins]
    lo = [b["dem_p25"] for b in dem_bins]
    hi = [b["dem_p75"] for b in dem_bins]
    ax.fill_between(zi, lo, hi, color=C["s1"], alpha=0.15, lw=0, label="DEM 25-75 %")
    ax.plot(zi, md, "-o", color=C["s1"], lw=1.6, ms=4, label="DEM median per occurrence bin")
    ax.plot([min(zi), max(zi)], [min(zi), max(zi)], color=C["ink2"], ls=":", lw=1, label="1:1 (same datum, bare ground)")
    dlt = R["bathtub"]["datum_offset_added_to_altimeter_m"]
    ax.plot([min(zi), max(zi)], [min(zi) + dlt, max(zi) + dlt], color=C["s2"], ls="--", lw=1,
            label=f"1:1 shifted by the datum offset {dlt:+.2f} m (from the flattened lake)")
    for b, x, y in zip(dem_bins, zi, md):
        ax.annotate(b["occ_bin"] + " %", (x, y), xytext=(4, 2), textcoords="offset points", fontsize=6.5, color=C["ink2"])
    ax.set_xlabel("height implied by occurrence and level record, EGM2008 (m)", fontsize=8.5, color=C["ink2"])
    ax.set_ylabel("Copernicus DEM height (m)", fontsize=8.5, color=C["ink2"])
    ax.set_title(f"Is occurrence consistent with elevation? Spearman {R['dem_check']['spearman_occ_vs_dem']}", fontsize=9.5, loc="left")
    ax.legend(fontsize=7, frameon=False, loc="upper left")
    savefig(fig, "fig8_dem_consistency.png")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "tonle-sap")
