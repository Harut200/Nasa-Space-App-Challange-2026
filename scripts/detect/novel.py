"""The flood as a moving front, and the choreography of every pixel.

Idea A (flood front). Each pass gives a shoreline; the shorelines of a rising flood are
nested. We (1) make the per-pixel "first date under water" monotone in time (exact
isotonic fit of a binary sequence), (2) interpolate a continuous arrival day between
two consecutive shorelines from the distances to both (shape-based interpolation in
time), (3) test that interpolation by leaving each interior pass out, (4) read the
order of flooding as a relative floodplain height and check it against the Copernicus
DEM and against JRC Global Surface Water occurrence (both never used to build it).

Idea B (choreography). Fit a logistic step a + b / (1 + exp(-(t - t0)/tau)) to every
pixel's HH and HV series at once on the GPU; amplitude, moment t0, tempo tau and
direction sign(b) are painted as one HSV image.

    .venv/bin/python -m scripts.detect.novel tonle-sap
    .venv/bin/python -m scripts.detect.novel sevan      # null control
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pathlib
import time

import numpy as np
import torch

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIG = ROOT / "docs" / "eda" / "novel"
DEM_CACHE = ROOT / "data" / "external" / "dem_novel"
GAP = ("2026-07-27", "2026-08-10")
DEM_ATTRIBUTION = ("Copernicus DEM (c) DLR e.V. 2010-2014 and (c) Airbus Defence and Space GmbH "
                   "2014-2018, provided under COPERNICUS by the European Union and ESA")
BIG = 1.0e5
# units of L: preview display brightness 0..1 (not dB). Stable-pixel drift between dates is ~0.04;
# the detector's lasting-change threshold is 0.12 (docs/DETECTION.md 3.12).
STEP_THR = 0.10   # |b| above which a significant step counts as up / down in the step-vector classes
AMP_SAT = 0.40    # |b| at which the choreography colour is fully saturated


# ============================================================ helpers
def doy(d: str) -> float:
    return float(dt.date.fromisoformat(d).timetuple().tm_yday)


def doy_to_str(x: float) -> str:
    return (dt.date(2026, 1, 1) + dt.timedelta(days=float(x) - 1)).strftime("%d %b")


def edt(seed: torch.Tensor, chunk: int = 32) -> torch.Tensor:
    """Exact Euclidean distance (pixels) from every pixel to the nearest True pixel of seed [H,W].

    Separable: 1-D distance along rows by two cumulative-max scans, then the exact
    min over rows of g^2 + (i - i')^2 (brute force in chunks; fine on a GPU for ~1000 px).
    Empty seed -> BIG everywhere.
    """
    H, W = seed.shape
    dev = seed.device
    if not bool(seed.any()):
        return torch.full((H, W), BIG, device=dev)
    j = torch.arange(W, device=dev, dtype=torch.float32).expand(H, W)
    left = torch.where(seed, j, torch.full_like(j, -BIG)).cummax(1).values
    right = (W - 1) - torch.flip(torch.where(torch.flip(seed, [1]), j, torch.full_like(j, -BIG)).cummax(1).values, [1])
    g = torch.minimum(j - left, right - j).clamp(max=BIG)
    g2 = g * g
    ii = torch.arange(H, device=dev, dtype=torch.float32)
    out = torch.empty(H, W, device=dev)
    for s in range(0, H, chunk):
        rows = ii[s:s + chunk]
        d = (rows[:, None] - ii[None, :]) ** 2  # [c, H]
        out[s:s + chunk] = (g2[None] + d[:, :, None]).amin(1)
    return out.sqrt()


def rankdata(x: torch.Tensor) -> torch.Tensor:
    """Average ranks (ties averaged) of a 1-D tensor, as float64."""
    xs, order = torch.sort(x.double())
    n = xs.numel()
    ranks = torch.empty(n, dtype=torch.float64, device=x.device)
    ranks[order] = torch.arange(1, n + 1, dtype=torch.float64, device=x.device)
    # average ties
    uniq, inv, counts = torch.unique(xs, return_inverse=True, return_counts=True)
    sums = torch.zeros(uniq.numel(), dtype=torch.float64, device=x.device).index_add_(0, inv, torch.arange(1, n + 1, dtype=torch.float64, device=x.device))
    avg = sums / counts
    ranks[order] = avg[inv]
    return ranks


def spearman(a: torch.Tensor, b: torch.Tensor) -> float:
    ra, rb = rankdata(a.cpu()), rankdata(b.cpu())
    ra, rb = ra - ra.mean(), rb - rb.mean()
    return float((ra * rb).sum() / (ra.norm() * rb.norm()).clamp_min(1e-12))


# ============================================================ Idea A: arrival time
def first_wet_index(water: torch.Tensor, valid: torch.Tensor, ties: str = "late"):
    """Best monotone (dry...dry wet...wet) fit to each pixel's binary series.

    For a 0/1 series the least-squares isotonic (non-decreasing) fit is a single step;
    we pick the step index k (0..T; k=T means never wet) minimising the number of
    disagreeing valid dates. Ties go to the latest k (conservative). Returns (k*, n_disagree).
    """
    T = water.shape[0]
    w = (water & valid).float()
    d = (~water & valid).float()
    zero = torch.zeros_like(w[:1])
    wet_before = torch.cat([zero, w.cumsum(0)], 0)                  # [T+1] wet dates before k
    dry_from = torch.cat([d.flip(0).cumsum(0).flip(0), zero], 0)    # [T+1] dry dates at or after k
    err = wet_before + dry_from
    # ties go to the LATEST step: a pixel is called flooded only once the evidence for water
    # is at least as strong as against it from then on (e.g. 0 0 1 0 -> never flooded)
    if ties == "early":
        emin, k = err.min(0)
    else:
        emin, kr = err.flip(0).min(0)
        k = T - kr
    return k, emin


def arrival_time(water, dates, valid, km2_per_px=1.0, max_front_km=2.0, ties="late"):
    """Monotone staircase + continuous arrival day by distance-ratio interpolation.

    water, valid [T,H,W] bool. Returns dict with
      kstar [H,W] (0 = water from the first pass, T = never), violations,
      arrival_day, arrival_lo, arrival_hi [H,W] (NaN unless first flooded inside the record),
      front_speed_km_day [H,W], stair [T,H,W] (monotone masks), base (always water).
    """
    T, H, W = water.shape
    dev = water.device
    t = torch.tensor([doy(d) for d in dates], device=dev)
    vall = valid.all(0)
    kstar, emin = first_wet_index(water, valid, ties)
    kstar = torch.where(vall, kstar, torch.full_like(kstar, T))
    stair = torch.stack([(kstar <= k) & vall for k in range(T)])
    arr = torch.full((H, W), float("nan"), device=dev)
    lo, hi, speed = arr.clone(), arr.clone(), arr.clone()
    px_km = math.sqrt(km2_per_px)
    dry_seed_ok = vall
    for k in range(1, T):
        band = (kstar == k) & vall
        if not bool(band.any()):
            continue
        d_prev = edt(stair[k - 1])
        d_next = edt(~stair[k] & dry_seed_ok)
        frac = d_prev / (d_prev + d_next)
        dtk = float(t[k] - t[k - 1])
        arr[band] = (t[k - 1] + dtk * frac)[band]
        lo[band], hi[band] = float(t[k - 1]), float(t[k])
        # local front speed: width of the band through this pixel over the interval
        width = (d_prev + d_next - 1.0).clamp(min=1.0, max=BIG)
        sp = width * px_km / dtk
        # speed only where the new water touches the previous water (a front); patches more than
        # max_front_km away (rain-filled paddies, false positives) have no front to measure
        sp = torch.where(d_prev * px_km > max_front_km, torch.full_like(sp, float("nan")), sp)
        speed[band] = sp[band]
    viol = (emin > 0) & vall
    return {"kstar": kstar, "n_disagree": emin, "violations_px": int(viol.sum()),
            "violation_frac": float(viol.sum() / vall.sum().clamp_min(1)),
            "flips_frac_of_changing": float((viol & (kstar > 0) & (kstar < T)).sum() / ((kstar > 0) & (kstar < T)).sum().clamp_min(1)),
            "arrival_day": arr, "arrival_lo": lo, "arrival_hi": hi, "front_speed_km_day": speed,
            "stair": stair, "base": stair[0], "t": t, "valid_all": vall}


def daily_area(A, km2_per_px, step=1.0):
    """Flooded area per day (central estimate + certain/possible bounds)."""
    t = A["t"]
    days = torch.arange(float(t[0]), float(t[-1]) + 0.01, step, device=t.device)
    base = float(A["base"].sum())
    arr, lo, hi = A["arrival_day"], A["arrival_lo"], A["arrival_hi"]
    f = torch.isfinite(arr)
    a, l, h = arr[f], lo[f], hi[f]
    mid = torch.stack([(a <= d).sum() for d in days]).float()
    certain = torch.stack([(h <= d).sum() for d in days]).float()
    possible = torch.stack([(l < d).sum() for d in days]).float()
    k = km2_per_px
    return {"days": days.cpu(), "area_km2": ((base + mid) * k).cpu(),
            "area_lo_km2": ((base + certain) * k).cpu(), "area_hi_km2": ((base + possible) * k).cpu()}


def _boundary(m):
    import torch.nn.functional as F
    er = -F.max_pool2d(-m.float()[None, None], 3, 1, 1)[0, 0] > 0
    return m & ~er


def mask_metrics(pred, true, region, px_km):
    inter = (pred & true & region).sum().float()
    union = ((pred | true) & region).sum().float()
    bp, bt = _boundary(pred) & region, _boundary(true) & region
    if bool(bp.any()) and bool(bt.any()):
        mbd = 0.5 * (edt(bt)[bp].mean() + edt(bp)[bt].mean()) * px_km
    else:
        mbd = torch.tensor(float("nan"))
    return float(inter / union.clamp_min(1)), float(mbd)


def leave_one_date_out(water, dates, valid, km2_per_px, ties="late"):
    """Drop each interior pass, rebuild the front from the rest, predict that pass's water.

    Truth = the observed water mask of the dropped pass. Compared methods, all built
    from the remaining passes only: 'front' (ours), 'previous' (nothing changed since the
    previous pass) and 'next' (already as on the next pass). Metrics: IoU over the whole
    scene, IoU over the zone that changed between the two neighbours, mean boundary
    distance (km), area error (km2).
    """
    T = water.shape[0]
    px_km = math.sqrt(km2_per_px)
    vall = valid.all(0)
    full = arrival_time(water, dates, valid, km2_per_px, ties=ties)["stair"]
    rows = []
    for k in range(1, T - 1):
        keep = [i for i in range(T) if i != k]
        sub = arrival_time(water[keep], [dates[i] for i in keep], valid[keep], km2_per_px, ties=ties)
        tk = doy(dates[k])
        front = (sub["base"] | (sub["arrival_day"] <= tk)) & vall
        prev = sub["stair"][k - 1]
        nxt = sub["stair"][k]  # index k in the reduced list is the next pass
        band = nxt & ~prev & vall  # where the three predictions can differ
        r = {"date": dates[k], "gap_days": doy(dates[k + 1]) - doy(dates[k - 1]),
             "band_km2": float(band.sum()) * km2_per_px}
        # truth 'lasting': full-record monotone water of pass k; truth 'raw': its water mask as detected
        for tname, truth in (("lasting", full[k]), ("raw", water[k] & vall)):
            r[f"truth_{tname}_wet_in_band_frac"] = float((truth & band).sum() / band.sum().clamp_min(1))
            for name, pred in (("front", front), ("previous", prev), ("next", nxt)):
                acc = float(((pred == truth) & band).sum() / band.sum().clamp_min(1))
                biou, _ = mask_metrics(pred, truth, band, px_km)
                iou, mbd = mask_metrics(pred, truth, vall, px_km)
                r.setdefault(tname, {})[name] = {
                    "band_accuracy": acc, "band_iou": biou, "iou": iou, "mean_boundary_km": mbd,
                    "area_err_km2": (float(pred.sum()) - float(truth.sum())) * km2_per_px}
        # ordering skill alone: threshold the front at the true area of the hidden pass, so only
        # WHERE (not how much) is tested; a random order inside the band scores p^2 + (1-p)^2
        truth = full[k]
        n_need = int((truth & band).sum())
        ab = sub["arrival_day"][band]
        if 0 < n_need < ab.numel():
            thr = torch.sort(ab).values[n_need - 1]
            pred_b = band & (sub["arrival_day"] <= thr)
            r["lasting"]["front_area_matched"] = {"band_accuracy": float(((pred_b == truth) & band).sum() / band.sum())}
            pw = n_need / ab.numel()
            r["lasting"]["random_area_matched"] = {"band_accuracy": pw * pw + (1 - pw) * (1 - pw)}
        rows.append(r)
    return rows


def height_from_order(arrival_day, valid_flood, levels_m=None, dates=None):
    """Relative floodplain height from the order of flooding (0 = flooded first = lowest).

    With per-date water levels (metres, e.g. a lake gauge on the pass dates) the
    arrival day is mapped to an absolute height by linear interpolation in time:
    the next step once gauge data are at hand.
    """
    out = torch.full_like(arrival_day, float("nan"))
    v = valid_flood & torch.isfinite(arrival_day)
    if bool(v.any()):
        r = rankdata(arrival_day[v].cpu()).float().to(arrival_day.device)
        out[v] = (r - 1) / max(int(v.sum()) - 1, 1)
    res = {"rel_height_rank": out}
    if levels_m is not None:
        td = torch.tensor([doy(d) for d in dates], dtype=torch.float64)
        lv = torch.tensor(levels_m, dtype=torch.float64)
        a = arrival_day.double().cpu()
        h = torch.from_numpy(np.interp(a.numpy(), td.numpy(), lv.numpy()))
        h[~torch.isfinite(a)] = float("nan")
        res["height_m"] = h.float()
    return res


# ============================================================ DEM
def dem_on_grid(area, bounds, H, W):
    """Copernicus GLO-90 averaged onto our grid; cached under data/external/dem_novel/."""
    import rasterio
    from rasterio.transform import from_bounds
    from rasterio.warp import Resampling, reproject
    path = DEM_CACHE / f"{area}_dem_grid.tif"
    if path.exists():
        with rasterio.open(path) as s:
            a = s.read(1)
        if a.shape == (H, W):
            return torch.from_numpy(a.astype(np.float32))
    w, s_, e, n = bounds
    tr = from_bounds(w, s_, e, n, W, H)
    dst = np.full((H, W), np.nan, np.float32)
    for lat in range(math.floor(s_), math.ceil(n)):
        for lon in range(math.floor(w), math.ceil(e)):
            nm = f"Copernicus_DSM_COG_30_{'N' if lat >= 0 else 'S'}{abs(lat):02d}_00_{'E' if lon >= 0 else 'W'}{abs(lon):03d}_00_DEM"
            try:
                with rasterio.open(f"/vsicurl/https://copernicus-dem-90m.s3.amazonaws.com/{nm}/{nm}.tif") as src:
                    tmp = np.full((H, W), np.nan, np.float32)
                    reproject(src.read(1).astype(np.float32), tmp, src_transform=src.transform, src_crs=src.crs,
                              dst_transform=tr, dst_crs="EPSG:4326", resampling=Resampling.average,
                              src_nodata=-32767, dst_nodata=np.nan)
                    m = np.isfinite(tmp)
                    dst[m] = tmp[m]
            except Exception as ex:  # tile missing (sea) or network
                print("  DEM tile", nm, "unavailable:", ex)
    DEM_CACHE.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=H, width=W, count=1, dtype="float32",
                       crs="EPSG:4326", transform=tr, nodata=np.nan, compress="deflate") as d:
        d.write(dst, 1)
    return torch.from_numpy(dst)


def dem_validation(arrival, kstar, dem, occ, dates, block=64):
    """Rank agreement between flood order and the DEM / JRC occurrence (never used upstream)."""
    T = len(dates)
    arrival, kstar, dem = arrival.cpu(), kstar.cpu(), dem.cpu()
    occ = occ.cpu() if occ is not None else None
    f = torch.isfinite(arrival) & torch.isfinite(dem)
    a, z = arrival[f], dem[f]
    res = {"n_px": int(f.sum()), "spearman_arrival_dem": spearman(a, z)}
    res["spearman_band_dem"] = spearman(kstar[f].float(), z)
    # the DEM sets water bodies to a constant (1.5 m here); drop those so the lake mask of
    # the DEM cannot create the agreement
    flat = float(torch.quantile(dem[torch.isfinite(dem)].float()[::7], 0.02))
    g = f & (dem > flat + 0.05)
    res["dem_flattened_value_m"] = flat
    res["n_px_excl_flattened"] = int(g.sum())
    res["spearman_arrival_dem_excl_flattened"] = spearman(arrival[g], dem[g])
    if occ is not None:
        g = f & torch.isfinite(occ)
        res["spearman_arrival_occurrence"] = spearman(arrival[g], occ[g])
    bands = []
    for k in range(1, T):
        m = f & (kstar == k)
        if int(m.sum()) < 20:
            continue
        q = torch.quantile(dem[m].float(), torch.tensor([0.25, 0.5, 0.75]))
        bands.append({"band": k, "from": dates[k - 1], "to": dates[k], "n_px": int(m.sum()),
                      "dem_q25": float(q[0]), "dem_median": float(q[1]), "dem_q75": float(q[2])})
    res["bands"] = bands
    meds = [b["dem_median"] for b in bands]
    res["band_medians_monotone_steps"] = int(sum(m2 >= m1 for m1, m2 in zip(meds, meds[1:])))
    res["band_steps_total"] = max(len(meds) - 1, 0)
    # local (block) correlations remove large-scale tilt of the plain and DEM bias
    H, W = arrival.shape
    loc = []
    for i in range(0, H, block):
        for j in range(0, W, block):
            m = f[i:i + block, j:j + block]
            if int(m.sum()) >= 50:
                aa, zz = arrival[i:i + block, j:j + block][m], dem[i:i + block, j:j + block][m]
                if float(aa.std()) > 0 and float(zz.std()) > 0:
                    loc.append(spearman(aa, zz))
    res["block_km"] = None
    res["n_blocks"] = len(loc)
    res["block_spearman_median"] = float(np.median(loc)) if loc else float("nan")
    res["block_spearman_frac_positive"] = float(np.mean(np.array(loc) > 0)) if loc else float("nan")
    # DEM of always-water vs flooded vs never-flooded neighbours
    return res


# ============================================================ Idea B: logistic steps
def _ab_rss(y, v, s):
    """Closed-form weighted LS for y ~ a + b s. y,v [T,...], s [T] or [T,...]. Returns a,b,rss."""
    while s.dim() < y.dim():
        s = s.unsqueeze(-1)
    Sw = v.sum(0)
    Ss = (v * s).sum(0)
    Sss = (v * s * s).sum(0)
    Sy = (v * y).sum(0)
    Ssy = (v * s * y).sum(0)
    det = Sw * Sss - Ss * Ss
    ok = det.abs() > 1e-6
    b = torch.where(ok, (Sw * Ssy - Ss * Sy) / det.clamp_min(1e-6) * ok, torch.zeros_like(det))
    a = (Sy - b * Ss) / Sw.clamp_min(1e-6)
    r = y - a - b * s
    return a, b, (v * r * r).sum(0)


def fit_logistic_steps(L, dates, valid, t0_step=2.0, taus=(1.5, 3.0, 6.0, 12.0, 24.0), refine_steps=150):
    """Per-pixel, per-channel logistic step fit, L(t) = a + b / (1 + exp(-(t - t0)/tau)).

    L [T,C,H,W], valid [T,H,W]. Variable projection: (a, b) in closed form for every
    (t0, tau); coarse grid over (t0, tau) for all pixels at once, then Adam refines
    t0 and log tau. The profile interval of t0 (all t0 whose best RSS is within
    the 1-sigma F threshold of the minimum) is returned as t0_lo, t0_hi: inside the
    data gap it spans the gap, because the data cannot locate a step there.
    """
    from scipy.stats import f as fdist
    T, C, H, W = L.shape
    dev = L.device
    t = torch.tensor([doy(d) for d in dates], device=dev)
    y = L.float()
    v = valid[:, None].expand(T, C, H, W).float()
    n = v.sum(0)
    t0s = torch.arange(float(t[0]) + 1, float(t[-1]), t0_step, device=dev)
    best = torch.full((C, H, W), float("inf"), device=dev)
    bt0, btau = torch.zeros_like(best), torch.zeros_like(best)
    for t0 in t0s:
        for tau in taus:
            s = torch.sigmoid((t - t0) / tau)
            _, _, rss = _ab_rss(y, v, s)
            better = rss < best
            best = torch.where(better, rss, best)
            bt0 = torch.where(better, t0.expand_as(best), bt0)
            btau = torch.where(better, torch.full_like(best, tau), btau)
    # Adam refinement of (t0, log tau), a and b profiled out
    p_t0 = bt0.clone().requires_grad_(True)
    p_lt = btau.log().clone().requires_grad_(True)
    opt = torch.optim.Adam([p_t0, p_lt], lr=0.3)
    tt = t[:, None, None, None]
    for _ in range(refine_steps):
        opt.zero_grad()
        s = torch.sigmoid((tt - p_t0) / p_lt.exp())
        _, _, rss = _ab_rss(y, v, s)
        rss.sum().backward()
        opt.step()
        with torch.no_grad():
            p_t0.clamp_(float(t[0]), float(t[-1]))
            p_lt.clamp_(math.log(0.5), math.log(60.0))
    with torch.no_grad():
        s = torch.sigmoid((tt - p_t0) / p_lt.exp())
        a, b, rss = _ab_rss(y, v, s)
        keep = rss <= best
        t0 = torch.where(keep, p_t0, bt0)
        tau = torch.where(keep, p_lt.exp(), btau)
        a2, b2, rss2 = _ab_rss(y, v, torch.sigmoid((tt - bt0) / btau))
        a, b, rss = torch.where(keep, a, a2), torch.where(keep, b, b2), torch.where(keep, rss, rss2)
        # profile interval of t0
        dfe = (n - 4).clamp_min(1)
        crit1 = float(fdist.ppf(0.68, 1, 6))
        thr = rss * (1 + crit1 / dfe)
        lo = torch.full_like(best, float("inf"))
        hi = torch.full_like(best, -float("inf"))
        for t0c in t0s:
            prof = torch.full_like(best, float("inf"))
            for tau_c in taus:
                _, _, r = _ab_rss(y, v, torch.sigmoid((t - t0c) / tau_c))
                prof = torch.minimum(prof, r)
            inside = prof <= thr
            lo = torch.where(inside, torch.minimum(lo, t0c.expand_as(lo)), lo)
            hi = torch.where(inside, torch.maximum(hi, t0c.expand_as(hi)), hi)
        lo = torch.minimum(lo, t0)
        hi = torch.maximum(hi, t0)
        # constant model and tests
        mean = (v * y).sum(0) / n.clamp_min(1)
        rss0 = (v * (y - mean) ** 2).sum(0)
        F = ((rss0 - rss) / 3) / (rss / dfe).clamp_min(1e-9)
        Fcrit = float(fdist.ppf(0.99, 3, 6))
        bic1 = n * torch.log(rss.clamp_min(1e-9) / n) + 4 * torch.log(n)
        bic0 = n * torch.log(rss0.clamp_min(1e-9) / n) + 1 * torch.log(n)
        yq = torch.where(v > 0, y, torch.full_like(y, float("nan")))
        amp = torch.nanquantile(yq.permute(1, 2, 3, 0).reshape(-1, T), 0.9, dim=1) - \
            torch.nanquantile(yq.permute(1, 2, 3, 0).reshape(-1, T), 0.1, dim=1)
        amp = amp.reshape(C, H, W)
    return {"t0": t0.detach(), "tau": tau.detach(), "a": a, "b": b, "rss": rss, "rss0": rss0,
            "t0_lo": lo, "t0_hi": hi, "F": F, "F_crit_p01": Fcrit, "sig": F > Fcrit,
            "bic_prefers_step": bic1 < bic0, "amp": amp, "sign": torch.sign(b), "mean": mean}


def step_classes(fit, thr):
    """Joint HH/HV step vector -> 0 none, 1 both darker = flooding or wetting, 2 flooded vegetation
    (HH up, HV not up), 3 growth / volume (HV up), 4 other darkening (HH down only)."""
    bH, bV = fit["b"][0], fit["b"][1]
    sig = fit["sig"].any(0)
    cls = torch.zeros_like(bH, dtype=torch.uint8)
    cls[sig & (bH < -thr) & (bV < -thr)] = 1
    cls[sig & (bH > thr) & (bV <= thr)] = 2
    cls[sig & (bV > thr)] = 3
    cls[sig & (bH < -thr) & (bV >= -thr)] = 4
    return cls


STEP_NAMES = ["no step", "both darker: flooding / wetting (HH and HV down)", "flooded vegetation (HH up, HV not)",
              "growth / volume (HV up)", "darkening in HH only"]


def choreography_rgb(fit, dates, valid, amp_sat=AMP_SAT, hue_span=0.8):
    """HSV composite: hue = moment t0 (of the channel with the larger step), saturation =
    step size |b| (saturates at amp_sat), value = mean HH brightness. Pixels whose step
    is not significant (F-test, p<0.01) are grey."""
    from matplotlib.colors import hsv_to_rgb
    t = [doy(d) for d in dates]
    ch = fit["b"].abs().argmax(0, keepdim=True)
    t0 = fit["t0"].gather(0, ch)[0]
    bb = fit["b"].abs().gather(0, ch)[0]
    sig = fit["sig"].gather(0, ch)[0]
    hue = ((t0 - t[0]) / (t[-1] - t[0])).clamp(0, 1) * hue_span
    sat = (bb / amp_sat).clamp(0, 1) * sig
    m = fit["mean"][0]
    vv = valid.all(0)
    lo, hi = torch.quantile(m[vv].float().cpu(), torch.tensor([0.02, 0.98]))
    val = (0.25 + 0.75 * ((m.cpu() - lo) / (hi - lo)).clamp(0, 1))
    hsv = torch.stack([hue.cpu(), sat.cpu(), val], -1).numpy()
    rgb = (hsv_to_rgb(hsv) * 255).astype(np.uint8)
    rgb[~vv.cpu().numpy()] = 255
    return torch.from_numpy(rgb)


# ============================================================ run
def _free():
    import gc
    gc.collect()
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()


def run(area, use_terrain=True, ties="late", fit_steps=True):
    from scripts.detect import ops
    from scripts.detect.stack import device, load_preview_stack
    tic = time.time()
    st = load_preview_stack(area)
    dev = device()
    H0, W0 = st.x.shape[-2:]
    water_ok = None
    if use_terrain:
        from scripts.detect.terrain import cached_water_possible
        water_ok = cached_water_possible(area, (H0, W0))
    out = ops.run_pipeline(st.x, st.valid, st.source, dev=dev, water_ok=water_ok)
    for key in ("change_first", "change_consec", "flooded_veg", "wind_suspect"):
        out.pop(key, None)  # memory: keep only what we use
    del st.x
    _free()
    water, valid, L = out["water"], out["valid"], out["L"]
    T, _, H, W = L.shape
    A = arrival_time(water, st.dates, valid, st.km2_per_px, ties=ties)
    DA = daily_area(A, st.km2_per_px)
    _free()
    loo = leave_one_date_out(water, st.dates, valid, st.km2_per_px, ties=ties)
    _free()
    flooded = torch.isfinite(A["arrival_day"])
    HO = height_from_order(A["arrival_day"], flooded)
    fit = fit_logistic_steps(L, st.dates, valid)
    vall = valid.all(0)
    rgb = choreography_rgb(fit, st.dates, valid)
    sc = step_classes(fit, STEP_THR)
    # independent references
    try:
        if area != "tonle-sap" and not (DEM_CACHE / f"{area}_dem_grid.tif").exists():
            raise RuntimeError("DEM check is run for the floodplain only (tonle-sap)")
        tp = ROOT / "data" / "processed" / area / "terrain.pt"
        dem = None
        if tp.exists():
            d = torch.load(tp, weights_only=False).get("dem")
            if d is not None and tuple(d.shape) == (H, W):
                dem = torch.as_tensor(d, dtype=torch.float32).to(dev)
                dem_src = "terrain.pt (Copernicus GLO-90, shared cache)"
        if dem is None:
            dem = dem_on_grid(area, st.bounds, H, W).to(dev)
            dem_src = "data/external/dem_novel (Copernicus GLO-90)"
    except Exception as ex:
        print("DEM unavailable:", ex)
        dem = None
    try:
        from scripts.detect.validate import reference_on_grid
        ref, _ = reference_on_grid(area, st.bounds, H, W)
        occ = torch.as_tensor(np.asarray(ref["occ_mean"]), dtype=torch.float32, device=dev)
        occ = torch.where(torch.as_tensor(np.asarray(ref["valid_frac"]), device=dev) > 0.5, occ, torch.full_like(occ, float("nan")))
    except Exception as ex:
        print("GSW unavailable:", ex)
        occ = None
    km2 = st.km2_per_px
    t = A["t"].cpu().tolist()
    summ = {"area": area, "dates": st.dates, "km2_per_px": km2,
            "terrain_mask_applied": water_ok is not None, "tie_rule": ties,
            "terrain_mask_removed_km2": float((~water_ok.to(dev) & vall).sum()) * km2 if water_ok is not None else 0.0,
            "valid_km2": float(vall.sum()) * km2,
            "always_water_km2": float(A["base"].sum()) * km2,
            "flooded_in_record_km2": float(flooded.sum()) * km2,
            "monotonicity_violations_px": A["violations_px"],
            "monotonicity_violation_frac_of_valid": A["violation_frac"],
            "violation_frac_of_flooded_in_record": A["flips_frac_of_changing"],
            "violation_n_disagree_hist_px": [int(((A["n_disagree"] == i) & vall).sum()) for i in range(1, 6)],
            "pass_area_raw_km2": [float((water[i] & vall).sum()) * km2 for i in range(T)],
            "pass_area_monotone_km2": [float(A["stair"][i].sum()) * km2 for i in range(T)]}
    # per-interval front statistics
    iv = []
    for k in range(1, T):
        m = (A["kstar"] == k) & vall
        sp = A["front_speed_km_day"][m]
        sp = sp[torch.isfinite(sp)]
        iv.append({"from": st.dates[k - 1], "to": st.dates[k], "days": t[k] - t[k - 1],
                   "new_km2": float(m.sum()) * km2,
                   "new_km2_per_day": float(m.sum()) * km2 / (t[k] - t[k - 1]),
                   "front_speed_median_km_day": float(sp.median()) if sp.numel() else float("nan"),
                   "front_speed_q25": float(sp.quantile(0.25)) if sp.numel() else float("nan"),
                   "front_speed_q75": float(sp.quantile(0.75)) if sp.numel() else float("nan")})
    summ["intervals"] = iv
    # plausibility of a front: newly flooded pixels should touch the previous water (a real
    # rising flood spreads from it); isolated patches far from it are rain ponds or false positives
    near = torch.zeros_like(vall)
    for k in range(1, T):
        m = (A["kstar"] == k) & vall
        if bool(m.any()):
            near |= m & (edt(A["stair"][k - 1]) * math.sqrt(km2) <= 2.0)
    summ["flooded_in_record_within_2km_of_previous_water_frac"] = float(near.sum() / flooded.sum().clamp_min(1))
    # the data gap band
    g0, g1 = doy("2026-07-25"), doy("2026-08-18")
    gb = (A["arrival_lo"] == g0) & (A["arrival_hi"] == g1)
    if bool(gb.any()):
        ad = A["arrival_day"][gb]
        q = torch.quantile(ad.cpu(), torch.tensor([0.1, 0.25, 0.5, 0.75, 0.9]))
        inside = ((ad >= doy(GAP[0])) & (ad <= doy(GAP[1]))).float().mean()
        summ["gap_band"] = {"km2": float(gb.sum()) * km2, "arrival_q10": doy_to_str(q[0]), "arrival_q25": doy_to_str(q[1]),
                            "arrival_median": doy_to_str(q[2]), "arrival_q75": doy_to_str(q[3]), "arrival_q90": doy_to_str(q[4]),
                            "frac_interpolated_inside_outage": float(inside),
                            "uncertainty": "each pixel lies between 25 Jul and 18 Aug (24 days); half-width 12 days"}
    summ["daily_area"] = {"first": float(DA["area_km2"][0]), "last": float(DA["area_km2"][-1]),
                          "max_daily_growth_km2": float(DA["area_km2"].diff().max()),
                          "max_daily_growth_day": doy_to_str(float(DA["days"][1:][DA["area_km2"].diff().argmax()]))}
    summ["leave_one_date_out"] = loo
    for tn in ("lasting", "raw"):
        if not loo:
            break
        agg = {}
        for nm in ("front", "previous", "next"):
            agg[nm] = {m: float(np.nanmean([r[tn][nm][m] for r in loo])) for m in
                       ("band_accuracy", "band_iou", "iou", "mean_boundary_km")}
            agg[nm]["abs_area_err_km2"] = float(np.mean([abs(r[tn][nm]["area_err_km2"]) for r in loo]))
        for nm in ("front_area_matched", "random_area_matched"):
            vals = [r[tn][nm]["band_accuracy"] for r in loo if nm in r[tn]]
            if vals:
                agg[nm] = {"band_accuracy": float(np.mean(vals)), "n": len(vals)}
        agg["truth_wet_in_band_frac_mean"] = float(np.mean([r[f"truth_{tn}_wet_in_band_frac"] for r in loo]))
        agg["front_best_band_accuracy_count"] = int(sum(r[tn]["front"]["band_accuracy"] >= max(r[tn]["previous"]["band_accuracy"], r[tn]["next"]["band_accuracy"]) for r in loo))
        agg["front_best_boundary_count"] = int(sum(r[tn]["front"]["mean_boundary_km"] <= min(r[tn]["previous"]["mean_boundary_km"], r[tn]["next"]["mean_boundary_km"]) for r in loo))
        agg["n_dates"] = len(loo)
        summ[f"loo_{tn}"] = agg
    if dem is not None and bool(flooded.any()):
        summ["dem_validation"] = dem_validation(A["arrival_day"], A["kstar"], dem, occ, st.dates)
        summ["dem_validation"]["block_km"] = 64 * math.sqrt(km2)
        summ["dem_validation"]["dem_source"] = dem_src
        bw = A["base"] & torch.isfinite(dem)
        nv = (A["kstar"] == T) & vall & torch.isfinite(dem)
        summ["dem_median_always_water"] = float(dem[bw].median()) if bool(bw.any()) else None
        summ["dem_median_flooded_in_record"] = float(dem[flooded & torch.isfinite(dem)].median())
        near = (edt(flooded | A["base"]) <= 10) & nv
        summ["dem_median_never_flooded_within_1.8km"] = float(dem[near].median()) if bool(near.any()) else None
    # choreography statistics
    sig = fit["sig"]
    summ["logistic"] = {
        "frac_sig_F_p01": [float(sig[c][vall].float().mean()) for c in range(2)],
        "frac_bic_prefers_step": [float(fit["bic_prefers_step"][c][vall].float().mean()) for c in range(2)],
        "median_amp_L": [float(fit["amp"][c][vall].median()) for c in range(2)],
        "median_abs_step_L_sig": [float(fit["b"][c][sig[c] & vall].abs().median()) if bool((sig[c] & vall).any()) else float("nan") for c in range(2)],
        "step_classes_km2": {STEP_NAMES[i]: float(((sc == i) & vall).sum()) * km2 for i in range(5)},
    }
    gapsig = sig[0] & vall & (fit["t0"][0] > doy("2026-07-25")) & (fit["t0"][0] < doy("2026-08-18"))
    if bool(gapsig.any()):
        w = (fit["t0_hi"][0] - fit["t0_lo"][0])[gapsig]
        summ["logistic"]["hh_steps_between_25Jul_18Aug_km2"] = float(gapsig.sum()) * km2
        summ["logistic"]["hh_steps_gap_t0_profile_width_median_days"] = float(w.median())
    ok = sig[0] & vall & flooded
    if bool(ok.any()):
        summ["logistic"]["spearman_t0HH_vs_arrival"] = spearman(fit["t0"][0][ok], A["arrival_day"][ok])
        summ["logistic"]["median_abs_t0HH_minus_arrival_days"] = float((fit["t0"][0][ok] - A["arrival_day"][ok]).abs().median())
    cd = out["change_date"]
    ok2 = sig[0] & vall & (cd > 0)
    if bool(ok2.any()):
        cdd = torch.tensor(t, device=dev)[cd.long().clamp(0, T - 1)]
        summ["logistic"]["spearman_t0HH_vs_cusum_change_date"] = spearman(fit["t0"][0][ok2], cdd[ok2])

    prod = {"area": area, "dates": st.dates, "bounds": st.bounds, "km2_per_px": km2,
            "arrival_day": A["arrival_day"].cpu(), "arrival_lo": A["arrival_lo"].cpu(), "arrival_hi": A["arrival_hi"].cpu(),
            "first_wet_index": A["kstar"].to(torch.int16).cpu(), "always_water": A["base"].cpu(), "valid": vall.cpu(),
            "front_speed_km_day": A["front_speed_km_day"].cpu(), "rel_height_rank": HO["rel_height_rank"].cpu(),
            "area_daily_km2": DA["area_km2"], "area_daily_lo_km2": DA["area_lo_km2"], "area_daily_hi_km2": DA["area_hi_km2"],
            "area_daily_doy": DA["days"],
            "t0": fit["t0"].permute(1, 2, 0).cpu(), "t0_lo": fit["t0_lo"].permute(1, 2, 0).cpu(), "t0_hi": fit["t0_hi"].permute(1, 2, 0).cpu(),
            "tau": fit["tau"].permute(1, 2, 0).cpu(), "amp": fit["amp"].permute(1, 2, 0).cpu(), "step_L": fit["b"].permute(1, 2, 0).cpu(),
            "sign": fit["sign"].permute(1, 2, 0).to(torch.int8).cpu(), "step_significant": fit["sig"].permute(1, 2, 0).cpu(),
            "step_class": sc.cpu(), "step_class_names": STEP_NAMES, "choreo_rgb": rgb,
            "dem": dem.cpu() if dem is not None else None, "gsw_occurrence": occ.cpu() if occ is not None else None,
            "summary": summ}
    tag = "" if (ties == "late" and use_terrain) else f"_ties-{ties}" + ("" if use_terrain else "_noterrain")
    if not tag:  # sensitivity runs write only their summary
        od = ROOT / "data" / "processed" / area / "novel"
        od.mkdir(parents=True, exist_ok=True)
        torch.save(prod, od / "products.pt")
    FIG.mkdir(parents=True, exist_ok=True)
    prod["tag"] = tag
    (FIG / f"{area}{tag}_summary.json").write_text(json.dumps(summ, indent=1, default=float))
    print(f"{area}: done in {time.time() - tic:.0f}s")
    return prod, out


# ============================================================ figures
def _save(fig, name):
    FIG.mkdir(parents=True, exist_ok=True)
    path = FIG / name
    fig.savefig(path, dpi=110, bbox_inches="tight")
    import matplotlib.pyplot as plt
    plt.close(fig)
    if path.stat().st_size > 500_000:
        from PIL import Image
        Image.open(path).convert("RGB").quantize(colors=200).save(path, optimize=True)
    print("  wrote", path.relative_to(ROOT), f"{path.stat().st_size // 1024} KB")


def _date_ticks(ax_or_cb, dates, axis="x"):
    t = [doy(d) for d in dates]
    lab = [doy_to_str(x) for x in t]
    if hasattr(ax_or_cb, "set_ticks"):
        ax_or_cb.set_ticks(t)
        ax_or_cb.set_ticklabels(lab)
    else:
        getattr(ax_or_cb, f"set_{axis}ticks")(t)
        getattr(ax_or_cb, f"set_{axis}ticklabels")(lab, rotation=45, ha="right" if axis == "x" else None)


def figures(prod):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap, hsv_to_rgb
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    area, dates, S = prod["area"], prod["dates"], prod["summary"]
    w, s_, e, n = prod["bounds"]
    ext = (w, e, s_, n)
    t = np.array([doy(d) for d in dates])
    arr = prod["arrival_day"].numpy()
    lo = prod["arrival_lo"].numpy()
    base = prod["always_water"].numpy()
    valid = prod["valid"].numpy()
    k = prod["first_wet_index"].numpy()
    T = len(dates)
    title_area = {"tonle-sap": "Tonle Sap", "sevan": "Lake Sevan", "ararat": "Ararat Valley"}.get(area, area)
    gap0, gap1 = doy(GAP[0]), doy(GAP[1])
    Hh, Ww = arr.shape  # pixel-centre coordinates for contours (row 0 = north)
    LON = w + (np.arange(Ww) + 0.5) * (e - w) / Ww
    LAT = n - (np.arange(Hh) + 0.5) * (n - s_) / Hh

    def background(ax):
        bg = np.ones((*arr.shape, 3)) * 0.93
        bg[base] = (0.55, 0.62, 0.72)
        bg[~valid] = 1.0
        ax.imshow(bg, extent=ext, interpolation="nearest")
        ax.set_xlabel("longitude (°E)")
        ax.set_ylabel("latitude (°N)")
        step = 0.5 if (e - w) > 1.2 else 0.2
        ax.set_xticks(np.arange(math.ceil(w / step) * step, e + 1e-9, step))

    # 1 arrival-time map
    fig, ax = plt.subplots(figsize=(8.2, 7.4))
    background(ax)
    cmap = plt.get_cmap("turbo")
    im = ax.imshow(np.ma.masked_invalid(arr), extent=ext, cmap=cmap, vmin=t[0], vmax=t[-1], interpolation="nearest")
    gapband = (lo == doy("2026-07-25")).astype(float)
    if gapband.any():
        ax.contour(LON, LAT, gapband, levels=[0.5], colors="k", linewidths=0.45, alpha=0.8)
    cb = fig.colorbar(im, ax=ax, shrink=0.8, label="estimated day the water arrived (shaded: no data)")
    _date_ticks(cb, dates)
    cb.ax.axhspan(gap0, gap1, color="k", alpha=0.25)
    ax.set_title(f"{title_area}: when did each place go under water?\n"
                 "arrival day interpolated between shorelines; black outline = band that flooded across the outage",
                 fontsize=10)
    ax.text(0.01, 0.01, "blue-grey: water from the first pass   light grey: never flooded",
            transform=ax.transAxes, fontsize=8, bbox=dict(fc="white", alpha=0.8, lw=0))
    _save(fig, f"{area}_arrival_time.png")

    # 2 nested shorelines
    fig, ax = plt.subplots(figsize=(8.2, 7.4))
    background(ax)
    cols = plt.get_cmap("viridis")(np.linspace(0, 1, T))
    for i in range(T):
        m = ((k <= i) & valid).astype(float)
        ax.contour(LON, LAT, m, levels=[0.5], colors=[cols[i]], linewidths=0.9)
    for i in range(T):
        ax.plot([], [], color=cols[i], lw=2, label=f"{doy_to_str(t[i])}  {S['pass_area_monotone_km2'][i]:,.0f} km²")
    ax.legend(loc="lower left", fontsize=7.5, title="shoreline (monotone)", title_fontsize=8, framealpha=0.85)
    ax.set_title(f"{title_area}: ten passes = ten nested shorelines\n"
                 "each line is (approximately) a line of equal ground height", fontsize=10)
    _save(fig, f"{area}_shorelines.png")

    # 3 leave-one-date-out
    loo = S.get("leave_one_date_out", [])
    if loo:
        fig, axs = plt.subplots(1, 3, figsize=(15.5, 4.3))
        xs = np.arange(len(loo))
        names = [("front", "front interpolation (ours)", "#1b6ca8"), ("previous", "nothing changed since previous pass", "#bbbbbb"),
                 ("next", "already as on the next pass", "#e8a33d")]
        for j, (nm, lab, c) in enumerate(names):
            axs[0].bar(xs + (j - 1) * 0.27, [r["lasting"][nm]["band_accuracy"] for r in loo], 0.27, color=c, label=lab)
            axs[1].bar(xs + (j - 1) * 0.27, [r["lasting"][nm]["mean_boundary_km"] for r in loo], 0.27, color=c, label=lab)
        am = [r["lasting"].get("front_area_matched", {}).get("band_accuracy", np.nan) for r in loo]
        ra = [r["lasting"].get("random_area_matched", {}).get("band_accuracy", np.nan) for r in loo]
        axs[2].bar(xs - 0.2, am, 0.4, color="#1b6ca8", label="front order, told the true area")
        axs[2].bar(xs + 0.2, ra, 0.4, color="#bbbbbb", label="random order, same area (expected)")
        axs[2].set_ylim(0, 1)
        axs[2].set_ylabel("share of pixels right in the zone")
        axs[2].legend(fontsize=8, loc="upper left")
        axs[2].set_title("does the ORDER inside the zone carry information?", fontsize=9)
        axs[0].set_title("how much and where (all predictions as made)", fontsize=9)
        axs[1].set_title("shoreline position", fontsize=9)
        for a_ in axs:
            a_.set_xticks(xs)
            a_.set_xticklabels([f"{doy_to_str(doy(r['date']))}\n({r['gap_days']:.0f} d)" for r in loo], fontsize=8)
        axs[0].set_ylabel("share of pixels right, in the zone\nflooded between the neighbouring passes")
        axs[0].set_ylim(0, 1)
        axs[1].set_ylabel("mean distance between predicted\nand actual shoreline (km)")
        axs[0].legend(fontsize=8, loc="upper left")
        fig.suptitle(f"{title_area}: hide one pass, predict its shoreline from the other nine "
                     "(label: hidden date and the interval it must bridge)", fontsize=10)
        fig.tight_layout()
        _save(fig, f"{area}_leave_one_out.png")

    # 4 DEM per band + scatter
    dem = prod.get("dem")
    dv = S.get("dem_validation")
    if dem is not None and dv:
        dem = dem.numpy()
        fig, axs = plt.subplots(1, 2, figsize=(11.5, 4.4), gridspec_kw={"width_ratios": [1.25, 1]})
        groups, labels = [], []
        bw = base & np.isfinite(dem)
        groups.append(dem[bw]); labels.append("water from\nfirst pass")
        for b in dv["bands"]:
            m = (k == b["band"]) & np.isfinite(dem) & np.isfinite(arr)
            groups.append(dem[m]); labels.append(f"→{doy_to_str(doy(b['to']))}")
        axs[0].boxplot(groups, tick_labels=labels, showfliers=False, patch_artist=True,
                       boxprops=dict(facecolor="#cfe0f0"), medianprops=dict(color="#1b6ca8", lw=2))
        axs[0].set_ylabel("Copernicus DEM height (m)")
        axs[0].set_xlabel("flood band (first pass on which the pixel was water)")
        axs[0].tick_params(axis="x", labelsize=8)
        axs[0].set_title("ground height of each flood band (box: quartiles, whiskers 1.5 IQR)", fontsize=9)
        f = np.isfinite(arr) & np.isfinite(dem)
        hb = axs[1].hexbin(arr[f], dem[f], gridsize=45, bins="log", cmap="Blues", mincnt=1)
        axs[1].set_xlabel("arrival day")
        _date_ticks(axs[1], dates)
        axs[1].tick_params(axis="x", labelsize=7)
        axs[1].set_ylabel("DEM height (m)")
        axs[1].axvspan(gap0, gap1, color="k", alpha=0.08)
        hi_ = np.nanpercentile(dem[f], 99.5)
        axs[1].set_ylim(np.nanpercentile(dem[f], 0.2), hi_)
        axs[1].set_title(f"Spearman ρ = {dv['spearman_arrival_dem']:.2f} (n = {dv['n_px']:,} px); "
                         f"median of {dv['n_blocks']} local {dv['block_km']:.0f}-km blocks ρ = {dv['block_spearman_median']:.2f}",
                         fontsize=8.5)
        fig.colorbar(hb, ax=axs[1], label="pixels")
        fig.suptitle(f"{title_area}: is 'flooded later' really 'higher ground'? Check against an elevation model never used "
                     "(Copernicus GLO-90)", fontsize=10)
        fig.tight_layout()
        _save(fig, f"{area}_dem_check.png")

    # 4b radar-timing height map next to the DEM
    if dem is not None and dv:
        rh = prod["rel_height_rank"].numpy()
        f = np.isfinite(rh) & np.isfinite(dem)
        zr = np.full_like(rh, np.nan)
        if f.any():
            from scipy.stats import rankdata as _rk
            zr[f] = (_rk(dem[f]) - 1) / max(f.sum() - 1, 1)
        fig, axs = plt.subplots(1, 2, figsize=(12.5, 5.6))
        for a_, img, ttl in ((axs[0], rh, "from radar timing only: rank of arrival day\n(0 = flooded first = lowest)"),
                             (axs[1], zr, "Copernicus DEM, same pixels, as a rank\n(0 = lowest)")):
            background(a_)
            im = a_.imshow(np.ma.masked_invalid(img), extent=ext, cmap="terrain", vmin=0, vmax=1, interpolation="nearest")
            a_.set_title(ttl, fontsize=9)
        fig.colorbar(im, ax=axs, shrink=0.75, label="relative height (rank among pixels flooded during the record)")
        fig.suptitle(f"{title_area}: a floodplain height map read from the order of flooding, beside the elevation model "
                     f"(Spearman ρ = {dv['spearman_arrival_dem']:.2f}; {dv['spearman_arrival_dem_excl_flattened']:.2f} "
                     "without the DEM's flattened-water pixels)", fontsize=10)
        _save(fig, f"{area}_height_maps.png")

    # 5 daily area
    days = prod["area_daily_doy"].numpy()
    fig, ax = plt.subplots(figsize=(9, 4.4))
    ax.fill_between(days, prod["area_daily_lo_km2"].numpy(), prod["area_daily_hi_km2"].numpy(), color="#1b6ca8", alpha=0.18,
                    label="bounds: certainly flooded … possibly flooded")
    ax.plot(days, prod["area_daily_km2"].numpy(), color="#1b6ca8", lw=2, label="daily estimate from the front interpolation")
    ax.plot(t, S["pass_area_raw_km2"], "o", color="#333", ms=5, label="water mapped on each pass (detector)")
    ax.axvspan(gap0, gap1, color="#d62728", alpha=0.12)
    ax.text((gap0 + gap1) / 2, ax.get_ylim()[0] + 0.03 * np.ptp(ax.get_ylim()), "no data\n(outage)", ha="center",
            fontsize=8, color="#a01010")
    _date_ticks(ax, dates)
    ax.set_ylabel("open water (km²)")
    ax.legend(fontsize=8, loc="upper left")
    ax.set_title(f"{title_area}: flooded area as a continuous curve (daily), with the outage bridged", fontsize=10)
    _save(fig, f"{area}_area_daily.png")

    # 6 front speed
    sp = prod["front_speed_km_day"].numpy()
    fig, axs = plt.subplots(1, 2, figsize=(12, 5.2), gridspec_kw={"width_ratios": [1.15, 1]})
    background(axs[0])
    im = axs[0].imshow(np.ma.masked_invalid(sp), extent=ext, cmap="magma_r", norm=matplotlib.colors.LogNorm(0.02, 2),
                       interpolation="nearest")
    fig.colorbar(im, ax=axs[0], shrink=0.8, label="front advance (km/day)")
    axs[0].set_title("local speed of the water's edge\n(band width ÷ time between passes; only within 2 km of earlier water)", fontsize=9)
    iv = S["intervals"]
    mids = [(doy(r["from"]) + doy(r["to"])) / 2 for r in iv]
    widths = [r["days"] for r in iv]
    axs[1].bar(mids, [r["front_speed_median_km_day"] for r in iv], width=np.array(widths) * 0.92,
               color=["#d62728" if r["days"] > 12 else "#7a5195" for r in iv], alpha=0.8)
    axs[1].errorbar(mids, [r["front_speed_median_km_day"] for r in iv],
                    yerr=[[r["front_speed_median_km_day"] - r["front_speed_q25"] for r in iv],
                          [r["front_speed_q75"] - r["front_speed_median_km_day"] for r in iv]], fmt="none", color="k", lw=1)
    ax2 = axs[1].twinx()
    ax2.plot(mids, [r["new_km2_per_day"] for r in iv], "o-", color="#1b6ca8", ms=4)
    ax2.set_ylabel("newly flooded area (km²/day)", color="#1b6ca8")
    ax2.spines["right"].set_visible(True)
    axs[1].set_ylabel("median front speed (km/day), bar = IQR")
    _date_ticks(axs[1], dates)
    axs[1].tick_params(axis="x", labelsize=7)
    axs[1].set_title("per interval (red: the 24-day interval with the outage)", fontsize=9)
    fig.suptitle(f"{title_area}: how fast did the flood front move?", fontsize=10)
    fig.tight_layout()
    _save(fig, f"{area}_front_speed.png")

    # 7 choreography composite + colour wheel
    rgb = prod["choreo_rgb"].numpy()
    fig = plt.figure(figsize=(10.5, 7.4))
    ax = fig.add_axes([0.05, 0.08, 0.68, 0.84])
    ax.imshow(rgb, extent=ext, interpolation="nearest")
    ax.set_xlabel("longitude (°E)"); ax.set_ylabel("latitude (°N)")
    ax.set_title(f"{title_area}: choreography map: colour = when it moved, colour strength = how much,\n"
                 "brightness = mean HH (grey = no significant step, F-test p < 0.01)", fontsize=10)
    wa = fig.add_axes([0.76, 0.45, 0.2, 0.3], projection="polar")
    th = np.linspace(0, 0.8 * 2 * np.pi, 200)
    r_ = np.linspace(0, 1, 40)
    TH, R = np.meshgrid(th, r_)
    hsv = np.stack([TH / (2 * np.pi), R, np.full_like(R, 0.95)], -1)
    wa.pcolormesh(TH, R, hsv[..., 0], color=None, shading="auto", alpha=0)
    wa.scatter(TH.ravel(), R.ravel(), c=hsv_to_rgb(hsv).reshape(-1, 3), s=6, marker="s", linewidths=0)
    tick_i = [0, 3, 4, 6, 9]
    wa.set_xticks([(t[i] - t[0]) / (t[-1] - t[0]) * 0.8 * 2 * np.pi for i in tick_i])
    wa.set_xticklabels([doy_to_str(t[i]) for i in tick_i], fontsize=7)
    wa.set_yticks([0.5, 1.0]); wa.set_yticklabels([f"{AMP_SAT/2:.2f}", f"≥{AMP_SAT:.2f}"], fontsize=6)
    wa.set_title("angle = moment t₀\nradius = step size |b|\n(display brightness)", fontsize=8)
    wa.grid(alpha=0.3)
    lg = S["logistic"]
    fig.text(0.76, 0.2, f"significant step (p<0.01):\nHH {lg['frac_sig_F_p01'][0]:.0%} of pixels\nHV {lg['frac_sig_F_p01'][1]:.0%} of pixels",
             fontsize=8.5)
    _save(fig, f"{area}_choreography.png")

    # 8 step-vector classes
    sc = prod["step_class"].numpy()
    bH, bV = prod["step_L"][..., 0].numpy(), prod["step_L"][..., 1].numpy()
    pal = np.array([[0.93, 0.93, 0.93], [0.12, 0.38, 0.72], [0.18, 0.62, 0.32], [0.95, 0.65, 0.1], [0.55, 0.35, 0.65]])
    img = pal[sc]
    img[~valid] = 1
    fig, axs = plt.subplots(1, 2, figsize=(12.5, 5.4), gridspec_kw={"width_ratios": [1.2, 1]})
    axs[0].imshow(img, extent=ext, interpolation="nearest")
    for i in range(1, 5):
        axs[0].plot([], [], "s", color=pal[i], ms=8, label=f"{STEP_NAMES[i]}: {lg['step_classes_km2'][STEP_NAMES[i]]:,.0f} km²")
    axs[0].legend(fontsize=7.5, loc="lower left", framealpha=0.9)
    axs[0].set_title("which way did each pixel step? (joint HH/HV step vector)", fontsize=9)
    sig = prod["step_significant"].numpy().any(-1) & valid
    rng = np.random.default_rng(0)
    idx = np.flatnonzero(sig.ravel())
    idx = rng.choice(idx, min(40000, idx.size), replace=False) if idx.size else idx
    axs[1].scatter(bH.ravel()[idx], bV.ravel()[idx], c=pal[sc.ravel()[idx]], s=1.5, alpha=0.5, linewidths=0)
    axs[1].axhline(0, color="k", lw=0.5); axs[1].axvline(0, color="k", lw=0.5)
    for v_ in (-STEP_THR, STEP_THR):
        axs[1].axhline(v_, color="k", lw=0.5, ls=":"); axs[1].axvline(v_, color="k", lw=0.5, ls=":")
    axs[1].set_xlabel("HH step b (display brightness)")
    axs[1].set_ylabel("HV step b (display brightness)")
    axs[1].set_xlim(-0.8, 0.8); axs[1].set_ylim(-0.8, 0.8)
    axs[1].set_title("step vectors of significant pixels (random 40,000)", fontsize=9)
    fig.suptitle(f"{title_area}: flooding, flooded vegetation and growth told apart by the direction of the step", fontsize=10)
    fig.tight_layout()
    _save(fig, f"{area}_step_vectors.png")


def figure_null_control():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    P = {}
    for a in ("tonle-sap", "sevan"):
        p = ROOT / "data" / "processed" / a / "novel" / "products.pt"
        if not p.exists():
            return
        P[a] = torch.load(p, weights_only=False)
    fig, axs = plt.subplots(2, 2, figsize=(11, 9.5))
    for j, a in enumerate(("tonle-sap", "sevan")):
        pr = P[a]
        w, s_, e, n = pr["bounds"]
        ext = (w, e, s_, n)
        t = [doy(d) for d in pr["dates"]]
        arr = pr["arrival_day"].numpy()
        bg = np.ones((*arr.shape, 3)) * 0.93
        bg[pr["always_water"].numpy()] = (0.55, 0.62, 0.72)
        bg[~pr["valid"].numpy()] = 1
        axs[0, j].imshow(bg, extent=ext, interpolation="nearest")
        axs[0, j].imshow(np.ma.masked_invalid(arr), extent=ext, cmap="turbo", vmin=t[0], vmax=t[-1], interpolation="nearest")
        S = pr["summary"]
        axs[0, j].set_title(f"{'Tonle Sap' if j == 0 else 'Lake Sevan (control)'}: arrival time\n"
                            f"newly flooded {S['flooded_in_record_km2']:,.0f} km² = "
                            f"{S['flooded_in_record_km2'] / max(S['always_water_km2'], 1):.0%} of the first-pass water", fontsize=9)
        axs[1, j].imshow(pr["choreo_rgb"].numpy(), extent=ext, interpolation="nearest")
        stp = 0.5 if (e - w) > 1.2 else 0.2
        for r_ in (0, 1):
            axs[r_, j].set_xticks(np.arange(math.ceil(w / stp) * stp, e + 1e-9, stp))
        lg = S["logistic"]
        axs[1, j].set_title(f"choreography: significant HH step on {lg['frac_sig_F_p01'][0]:.0%} of pixels, "
                            f"median |b| {lg['median_abs_step_L_sig'][0]:.2f}", fontsize=9)
    fig.suptitle("Null control: a lake that does not move gives (almost) no front", fontsize=11)
    fig.tight_layout()
    _save(fig, "null_control_sevan.png")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("area", nargs="?", default="tonle-sap")
    ap.add_argument("--no-figs", action="store_true")
    ap.add_argument("--no-terrain", action="store_true", help="do not apply the terrain water_ok mask")
    ap.add_argument("--ties", default="late", choices=["late", "early"],
                    help="isotonic tie rule (sensitivity run writes <area>_ties-early_summary.json only)")
    ap.add_argument("--figs-only", action="store_true", help="redraw figures from the saved products")
    a = ap.parse_args()
    if a.figs_only:
        prod = torch.load(ROOT / "data" / "processed" / a.area / "novel" / "products.pt", weights_only=False)
        figures(prod)
        figure_null_control()
        return
    prod, _ = run(a.area, use_terrain=not a.no_terrain, ties=a.ties)
    if prod.get("tag"):
        return
    print(json.dumps({k: v for k, v in prod["summary"].items() if k != "leave_one_date_out"}, indent=1, default=float)[:6000])
    if not a.no_figs:
        figures(prod)
        figure_null_control()


if __name__ == "__main__":
    main()
