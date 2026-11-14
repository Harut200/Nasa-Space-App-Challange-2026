"""Mathematics-only (no learning) change-detection operators, all in PyTorch.

Every function works on a generic "log-like intensity" tensor L:
  * previews  : L = display brightness 0..1 as is (mapping unknown, assumed monotonic)
  * gamma0    : L = 10*log10(power) in dB
Speckle filtering is done before the log, on the "linear-like" input
(brightness for previews, linear power for gamma0); see `to_loglike`.

Shapes: stacks are [T, C, H, W] (C=2: HH, HV), masks [T, H, W] bool.
All thresholds live in KNOBS[source]; nothing numeric is hard-coded below.
"""
from __future__ import annotations

import torch
import torch.nn.functional as F

# ----------------------------------------------------------------------------
# Knobs. "preview" values were chosen by looking at the preview data itself
# (see docs/eda/math-detection.md). "gamma0" values are literature-typical
# L-band starting points in dB and are UNTESTED.
# ----------------------------------------------------------------------------
KNOBS = {
    "preview": {
        "box": 3,                 # boxcar window (px) for local means (~540 m)
        "quegan": True,           # multi-temporal speckle filter on/off
        "normalise": True,        # relative radiometric normalisation on/off
        "norm_mode": "gain",      # multiplicative per-date gain (black point is fixed)
        "stable_frac": 0.2,       # fraction of pixels (lowest temporal std) used as stable
        "water_hh_strong": 0.06,  # HH below this = surely water (hysteresis seed)
        "water_hh_weak": 0.12,    # HH below this = water if connected to a seed
        "water_hv_max": 0.35,     # HV must also be below this (loose; wind brightens HV)
        "perm_frac": 0.9,         # water on >= 90 % of valid dates -> permanent water
        "seasonal_min_dates": 2,  # water on >= 2 dates (not permanent) -> seasonal water
        "n_ref": 2,               # first n dates averaged = "dry-season" reference
        "fv_hh_rise": 0.15,       # flooded veg: HH rise above reference (0.12 -> 0.20 changes area x4!)
        "fv_hv_rise_max": 0.06,   # ... while HV rise stays below this
        "fv_min_dates": 2,        # flooded veg on >= 2 dates -> flooded-veg class
        "change_step_min": 0.12,  # CUSUM: minimum step size (L units, any channel norm)
        "persist_frac": 0.8,      # CUSUM: share of later dates that must stay on the new side
        "change_t_min": 5.0,      # CUSUM: step must be >= 5x its standard error (t statistic)
        "min_blob_px": 4,         # remove connected specks smaller than this
        "wind_frac": 0.7,         # "usually water" = water on first, last and >= 70 % of dates
        "nodata_floor": 0.01,     # preview no-data margins are drawn at ~1/255; cut them
        "edge_erode_px": 8,       # shrink the footprint by 8 px (EDA: edge ~8 px at half brightness)
        "common_footprint": True, # keep only pixels valid on all dates
        "open_k": 0,              # morphological opening kernel (0 = off)
        "untested": False,
    },
    "gamma0": {
        "box": 7,                 # ~7x7 at 30-80 m
        "quegan": True,
        "normalise": False,       # calibrated data should not need it
        "norm_mode": "offset",    # in dB a drift is additive
        "stable_frac": 0.2,
        "water_hh_strong": -20.0,  # dB, calm open water at L-band HH
        "water_hh_weak": -16.0,
        "water_hv_max": -24.0,
        "perm_frac": 0.9,
        "seasonal_min_dates": 2,
        "n_ref": 2,
        "fv_hh_rise": 3.0,        # dB, double bounce
        "fv_hv_rise_max": 1.5,    # dB
        "fv_min_dates": 2,
        "change_step_min": 3.0,   # dB
        "persist_frac": 0.8,
        "change_t_min": 5.0,
        "min_blob_px": 20,
        "wind_frac": 0.7,
        "nodata_floor": None,     # gamma0 has NaN no-data instead
        "edge_erode_px": 1,
        "common_footprint": True,
        "open_k": 3,
        "untested": True,          # literature-typical starting values, NOT tuned
    },
}


# ------------------------------- adapter ------------------------------------
def to_loglike(x: torch.Tensor, source: str) -> torch.Tensor:
    """Map the speckle-filtered linear-like input to log-like intensity L.

    preview: L = x (brightness already display-stretched).
    gamma0 : L = 10*log10(max(x, 1e-6)) dB.
    """
    if source == "preview":
        return x
    if source == "gamma0":
        return 10.0 * torch.log10(x.clamp_min(1e-6))
    raise ValueError(source)


def clean_input(x: torch.Tensor, valid: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """Zero-fill NaN / invalid pixels and AND their mask into `valid` (gamma0 has NaN no-data)."""
    finite = torch.isfinite(x).all(dim=1)
    valid = valid & finite
    x = torch.where(valid[:, None], torch.nan_to_num(x, nan=0.0), torch.zeros_like(x))
    return x, valid


# ------------------------------- filters ------------------------------------
def box_mean(x: torch.Tensor, mask: torch.Tensor, k: int) -> torch.Tensor:
    """Mask-aware boxcar mean: m = conv(x*mask, 1) / conv(mask, 1).

    x [N, C, H, W], mask [N, H, W]. Pixels with no valid neighbour get 0.
    """
    if k <= 1:
        return x * mask[:, None]
    w = torch.ones(1, 1, k, k, device=x.device, dtype=x.dtype)
    n, c, h, wd = x.shape
    m = mask[:, None].to(x.dtype)
    num = F.conv2d((x * m).reshape(n * c, 1, h, wd), w, padding=k // 2).reshape(n, c, h, wd)
    den = F.conv2d(m, w, padding=k // 2)
    return num / den.clamp_min(1e-6) * (den > 0)


def quegan_filter(x: torch.Tensor, valid: torch.Tensor, k: int, eps: float = 1e-4) -> torch.Tensor:
    """Multi-temporal speckle filter (Quegan & Yu 2001), per channel.

    J_t = E[I_t] * (1/N) * sum_k I_k / E[I_k], with E[.] the k x k boxcar mean and
    the sum over the N dates valid at that pixel. Needs a multiplicative-noise
    intensity (linear power, or a power-law brightness); eps guards dark pixels.
    """
    m = box_mean(x, valid, k)
    ratio = x / (m + eps) * valid[:, None]
    n = valid.sum(0).clamp_min(1)[None, None]  # [1,1,H,W]
    avg_ratio = ratio.sum(0, keepdim=True) / n
    return m * avg_ratio * valid[:, None]


def normalise_dates(L: torch.Tensor, valid: torch.Tensor, stable_frac: float,
                    exclude: torch.Tensor | None = None, mode: str = "gain"):
    """Relative radiometric normalisation to the temporal median using stable pixels.

    Stable pixels = valid at all dates and in the lowest `stable_frac` of temporal
    std (per channel), excluding `exclude` (e.g. water stuck at the display floor,
        which has ~zero std but says nothing about gain). For each date t and channel c:
      mode "gain"  : a = mean(ref)/mean(L_t), b = 0  (previews: black point is pinned at 1/255)
      mode "offset": a = 1, b = mean(ref) - mean(L_t) (dB data: a calibration drift is additive)
    and return a*L_t + b. Returns (L_norm, gains, offsets).
    (A least-squares fit of ref on L_t was tried first and rejected: noise in L_t biases
    the slope toward 0 -- regression dilution -- which turned dark water grey.)
    """
    T, C = L.shape[:2]
    allv = valid.all(0)
    if exclude is not None:
        allv = allv & ~exclude
    ref = L.median(dim=0).values
    out = L.clone()
    gains = torch.ones(T, C)
    offs = torch.zeros(T, C)
    for c in range(C):
        sd = L[:, c].std(0)
        sd_v = sd[allv]
        if sd_v.numel() < 100:
            continue
        cut = torch.quantile(sd_v.float().cpu()[:: max(1, sd_v.numel() // 200000)], stable_frac).to(L.device)
        stab = allv & (sd <= cut)
        y = ref[c][stab]
        for t in range(T):
            xv = L[t, c][stab]
            xm, ym = xv.mean(), y.mean()
            if mode == "gain":
                a, b = ym / xm.clamp_min(1e-6), torch.zeros(())
            else:
                a, b = torch.ones(()), ym - xm
            out[t, c] = a * L[t, c] + b
            gains[t, c], offs[t, c] = float(a), float(b)
    return out * valid[:, None], gains, offs


# ------------------------------- thresholds ---------------------------------
def otsu(v: torch.Tensor, bins: int = 256) -> float:
    """Otsu threshold: argmax_t w0(t) w1(t) (mu0(t) - mu1(t))^2 over a torch histogram."""
    v = v.flatten().float().cpu()
    lo, hi = float(v.min()), float(v.max())
    h = torch.histc(v, bins=bins, min=lo, max=hi)
    p = h / h.sum()
    centers = lo + (torch.arange(bins) + 0.5) * (hi - lo) / bins
    w0 = p.cumsum(0)
    mu = (p * centers).cumsum(0)
    w1 = 1 - w0
    between = (mu[-1] * w0 - mu) ** 2 / (w0 * w1).clamp_min(1e-12)
    return float(centers[between.argmax()] + 0.5 * (hi - lo) / bins)


def reconstruct(seed: torch.Tensor, allowed: torch.Tensor, max_iter: int = 5000) -> torch.Tensor:
    """Geodesic reconstruction: grow `seed` by 3x3 dilation inside `allowed` until stable.

    seed, allowed [N, H, W] bool. Used for hysteresis (8-connectivity).
    """
    cur = (seed & allowed).float()[:, None]
    a = allowed.float()[:, None]
    for i in range(max_iter):
        nxt = F.max_pool2d(cur, 3, 1, 1) * a
        if i % 16 == 0 and torch.equal(nxt, cur):
            break
        cur = nxt
    return cur[:, 0] > 0


def hysteresis(L: torch.Tensor, strong: float, weak: float, mask: torch.Tensor) -> torch.Tensor:
    """Two-threshold ("dark") hysteresis: pixels with L < weak that are 8-connected to a pixel with L < strong."""
    return reconstruct((L < strong) & mask, (L < weak) & mask)


# ------------------------------- change -------------------------------------
def change_vs_first(L: torch.Tensor, valid: torch.Tensor) -> torch.Tensor:
    """D_t = L_t - L_0 per channel (0 where either date invalid). L is already averaged."""
    both = valid & valid[:1]
    return (L - L[:1]) * both[:, None]


def change_consecutive(L: torch.Tensor, valid: torch.Tensor) -> torch.Tensor:
    """D_t = L_t - L_{t-1} per channel for t >= 1 (D_0 = 0)."""
    d = torch.zeros_like(L)
    both = valid[1:] & valid[:-1]
    d[1:] = (L[1:] - L[:-1]) * both[:, None]
    return d


def temporal_stats(L: torch.Tensor, valid: torch.Tensor) -> dict:
    """Per-pixel stats over valid dates: mean, std, CV = std/|mean|, p95 - p5 (per channel)."""
    v = valid[:, None].to(L.dtype)
    n = v.sum(0).clamp_min(1)
    mean = (L * v).sum(0) / n
    std = (((L - mean) ** 2) * v).sum(0).div(n).sqrt()
    cv = std / mean.abs().clamp_min(1e-6)
    big = torch.where(valid[:, None], L, torch.full_like(L, float("inf")))
    s = big.sort(dim=0).values  # invalid at the end
    nn = valid.sum(0)[None].clamp_min(1)  # [1,H,W]
    def pct(q):
        idx = ((nn - 1).float() * q).round().long()[:, None].expand(1, L.shape[1], *L.shape[2:])
        return s.gather(0, idx)[0]
    return {"mean": mean, "std": std, "cv": cv, "p95_p5": pct(0.95) - pct(0.05)}


def cusum_changepoint(L: torch.Tensor, valid: torch.Tensor, step_min: float, persist_frac: float,
                      t_min: float = 0.0):
    """Single persistent change point per pixel (two-segment mean model).

    For each split k (1..T-1): m1 = mean(L[:k]), m2 = mean(L[k:]) over valid dates,
    score_k = ||m2 - m1|| * sqrt(n1*n2/(n1+n2)) (norm over channels, i.e. the
    max of the CUSUM of deviations from the overall mean). k* = argmax score.
    A change is kept if ||m2 - m1|| >= step_min and, in the channel with the
    largest step, >= persist_frac of the dates before k* and after k* lie on
    their own side of the midpoint (the series "departs for good"), and the
    two-sample t statistic |step| / (sd_resid*sqrt(1/n1+1/n2)) >= t_min.
    Returns change_date [H,W] int16 (-1 = none) and the signed step of the
    dominant channel [H,W], plus the step per channel [C,H,W].
    """
    T, C, H, W = L.shape
    v = valid[:, None].to(L.dtype)
    cs = (L * v).cumsum(0)
    cn = v.cumsum(0)
    tot, ntot = cs[-1], cn[-1]
    best = torch.full((H, W), -1.0, device=L.device)
    bestk = torch.zeros((H, W), dtype=torch.long, device=L.device)
    beststep = torch.zeros((C, H, W), device=L.device)
    for k in range(1, T):
        n1, n2 = cn[k - 1], ntot - cn[k - 1]
        m1 = cs[k - 1] / n1.clamp_min(1)
        m2 = (tot - cs[k - 1]) / n2.clamp_min(1)
        ok = (n1[0] > 0) & (n2[0] > 0)
        step = (m2 - m1)
        score = step.norm(dim=0) * (n1[0] * n2[0] / (n1[0] + n2[0]).clamp_min(1)).sqrt()
        score = torch.where(ok, score, torch.full_like(score, -1.0))
        upd = score > best
        best = torch.where(upd, score, best)
        bestk = torch.where(upd, torch.full_like(bestk, k), bestk)
        beststep = torch.where(upd[None], step, beststep)
    dom = beststep.abs().argmax(0)  # dominant channel
    sstep = beststep.gather(0, dom[None])[0]
    # persistence check in the dominant channel
    Ld = L.gather(1, dom[None, None].expand(T, 1, H, W))[:, 0]
    tidx = torch.arange(T, device=L.device)[:, None, None]
    after = tidx >= bestk[None]
    csd = cs.gather(1, dom[None, None].expand(T, 1, H, W))[:, 0]  # cumsum, dominant channel
    n1 = cn[:, 0].gather(0, (bestk - 1).clamp_min(0)[None])[0]
    s1 = csd.gather(0, (bestk - 1).clamp_min(0)[None])[0]
    m1 = s1 / n1.clamp_min(1)
    m2 = m1 + sstep
    midpt = 0.5 * (m1 + m2)
    sgn = torch.sign(sstep)[None]
    on_new = ((Ld - midpt[None]) * sgn > 0) & valid
    on_old = ((Ld - midpt[None]) * sgn <= 0) & valid
    frac_after = (on_new & after).sum(0) / (valid & after).sum(0).clamp_min(1)
    frac_before = (on_old & ~after).sum(0) / (valid & ~after).sum(0).clamp_min(1)
    # significance: t = |step| / (sd_resid * sqrt(1/n1 + 1/n2)), residuals of the 2-segment fit
    n2 = cn[-1, 0] - n1
    fit = torch.where(after, m2[None], m1[None])
    sse = (((Ld - fit) ** 2) * valid).sum(0)
    sd = (sse / (n1 + n2 - 2).clamp_min(1)).sqrt().clamp_min(1e-6)
    tstat = sstep.abs() / (sd * (1 / n1.clamp_min(1) + 1 / n2.clamp_min(1)).sqrt())
    keep = ((beststep.norm(dim=0) >= step_min) & (frac_after >= persist_frac)
            & (frac_before >= persist_frac) & (tstat >= t_min))
    date = torch.where(keep, bestk, torch.full_like(bestk, -1)).to(torch.int16)
    return date, torch.where(keep, sstep, torch.zeros_like(sstep)), beststep


# ------------------------------- morphology ---------------------------------
def dilate(m: torch.Tensor, k: int = 3) -> torch.Tensor:
    """Binary dilation with a k x k square: max_pool2d. m [N,H,W] bool."""
    return F.max_pool2d(m.float()[:, None], k, 1, k // 2)[:, 0] > 0


def erode(m: torch.Tensor, k: int = 3) -> torch.Tensor:
    """Binary erosion = NOT dilate(NOT m). Outside the frame counts as 'on' (no edge erosion)."""
    return ~dilate(~m, k)


def opening(m, k=3):
    """Opening = dilate(erode(m)): removes features thinner than k."""
    return dilate(erode(m, k), k)


def closing(m, k=3):
    """Closing = erode(dilate(m)): fills gaps thinner than k."""
    return erode(dilate(m, k), k)


def label_components(m: torch.Tensor, max_iter: int = 10000) -> torch.Tensor:
    """8-connected component labels by iterative max-label propagation (0 = background).

    m [N,H,W] bool -> int64 labels, unique per image (label = 1 + max flat index in component).
    """
    N, H, W = m.shape
    idx = torch.arange(1, H * W + 1, device=m.device, dtype=torch.float32).reshape(1, H, W).expand(N, H, W)
    # float32 is exact for integers < 2**24 (16.7 M px) which covers our frames
    lab = (idx * m)[:, None].contiguous()
    mf = m.float()[:, None]
    for i in range(max_iter):
        nxt = F.max_pool2d(lab, 3, 1, 1) * mf
        if i % 16 == 0 and torch.equal(nxt, lab):
            break
        lab = nxt
    return lab[:, 0].long()


def remove_small(m: torch.Tensor, min_px: int) -> torch.Tensor:
    """Drop 8-connected components with fewer than min_px pixels. m [N,H,W] bool."""
    if min_px <= 1:
        return m
    lab = label_components(m).cpu()
    out = torch.zeros_like(m, device="cpu")
    for i in range(m.shape[0]):
        l = lab[i].flatten()
        cnt = torch.bincount(l)
        big = cnt >= min_px
        big[0] = False
        out[i] = big[l].reshape(m.shape[1:])
    return out.to(m.device)


def clean_mask(m: torch.Tensor, open_k: int, min_px: int) -> torch.Tensor:
    """Optional opening, then small-speck removal."""
    if open_k and open_k > 1:
        m = opening(m, open_k)
    return remove_small(m, min_px)


# ------------------------------- classification ------------------------------
def water_masks(L, valid, kn):
    """Open water per date: hysteresis on HH (strong/weak) AND HV < water_hv_max, inside valid.

    HH is used as the main cue because wind roughening brightens HV/HH of the lake
    surface unevenly; hysteresis keeps rougher patches connected to calm water.
    """
    hh, hv = L[:, 0], L[:, 1]
    ok = valid & (hv < kn["water_hv_max"])
    w = hysteresis(hh, kn["water_hh_strong"], kn["water_hh_weak"], ok)
    return clean_mask(w, kn["open_k"], kn["min_blob_px"])


def permanent_water_prior(L, valid, kn):
    """Darkest-ever composite: 2nd-lowest valid HH per pixel < water_hh_weak.

    Using the 2nd-lowest (not the minimum) avoids a single noisy dark date.
    """
    hh = torch.where(valid, L[:, 0], torch.full_like(L[:, 0], float("inf")))
    s = hh.sort(0).values
    second = s[1] if s.shape[0] > 1 else s[0]
    prior = hysteresis(second[None], kn["water_hh_strong"], kn["water_hh_weak"], torch.isfinite(second)[None])[0]
    return clean_mask(prior[None], kn["open_k"], kn["min_blob_px"])[0]


def reference(L, valid, n_ref):
    """Dry-season reference: mean of the first n_ref valid dates per pixel."""
    v = valid[:n_ref, None].to(L.dtype)
    return (L[:n_ref] * v).sum(0) / v.sum(0).clamp_min(1), valid[:n_ref].any(0)


def flooded_veg_masks(L, valid, water, prior, kn):
    """Flooded vegetation per date (double bounce):
    HH_t - HH_ref > fv_hh_rise AND HV_t - HV_ref < fv_hv_rise_max AND not water_t
    AND not in the permanent-water prior (a brightened lake is wind, not trees)."""
    ref, refv = reference(L, valid, kn["n_ref"])
    dhh = L[:, 0] - ref[0]
    dhv = L[:, 1] - ref[1]
    fv = (dhh > kn["fv_hh_rise"]) & (dhv < kn["fv_hv_rise_max"]) & valid & refv & ~water & ~prior[None]
    return clean_mask(fv, kn["open_k"], kn["min_blob_px"])


CLASS_NAMES = ["stable", "permanent water", "seasonal open water", "flooded vegetation", "other change"]


def classify(water, fv, valid, change_date, kn):
    """Season class per pixel, first match wins:
    1 permanent water  : water on >= perm_frac of valid dates
    2 seasonal water   : water on >= seasonal_min_dates dates
    3 flooded veg      : flooded veg on >= fv_min_dates dates
    4 other change     : CUSUM change point found
    0 stable           : otherwise
    """
    nv = valid.sum(0).clamp_min(1)
    nw = (water & valid).sum(0)
    nf = (fv & valid).sum(0)
    cls = torch.zeros(water.shape[1:], dtype=torch.uint8, device=water.device)
    cls[change_date >= 0] = 4
    cls[nf >= kn["fv_min_dates"]] = 3
    cls[nw >= kn["seasonal_min_dates"]] = 2
    cls[(nw.float() / nv >= kn["perm_frac"]) & (nw > 0)] = 1
    cls[~valid.any(0)] = 0
    return cls


def wind_suspect(water, valid, kn):
    """Usually-water pixels that are NOT water on date t: water on the first and the last
    valid date and on >= wind_frac of valid dates, but not today. A temporary dip inside a
    lake is far more likely wind roughening (or a geolocation shift at the shore) than a
    real drying. Flagged only -- never silently filled into the water mask."""
    T = water.shape[0]
    idx = torch.arange(T, device=water.device)[:, None, None]
    big = torch.where(valid, idx, torch.full_like(idx.expand_as(valid), T))
    first = big.min(0).values.clamp_max(T - 1)
    last = torch.where(valid, idx, torch.full_like(idx.expand_as(valid), -1)).max(0).values.clamp_min(0)
    w_first = water.gather(0, first[None])[0]
    w_last = water.gather(0, last[None])[0]
    nv = valid.sum(0).clamp_min(1)
    usual = w_first & w_last & ((water & valid).sum(0).float() / nv >= kn["wind_frac"])
    return usual[None] & ~water & valid


def nodata_margin(x, valid, floor):
    """Footprint margins that the preview renders at the display floor (both channels
    <= floor) and that touch the edge of the footprint polygon. Found by geodesic
    reconstruction from the pixels next to the outside of `valid`, through floor pixels.
    (Calm water is also at the floor, so a lake touching the swath edge would be cut too.)"""
    at_floor = (x[:, 0] <= floor) & (x[:, 1] <= floor) & valid
    edge = valid & dilate(~valid, 3)
    return reconstruct(edge & at_floor, at_floor)


# ------------------------------- pipeline -----------------------------------
def run_pipeline(x, valid, source, kn=None, dev=None, normalise=None, water_ok=None):
    """Full chain. x [T,2,H,W] linear-like (brightness or linear power). Returns dict of tensors.

    water_ok [H,W] bool, optional: where open water is physically possible (terrain mask).
    """
    kn = dict(kn or KNOBS[source])
    dev = dev or x.device
    x, valid = clean_input(x.to(dev), valid.to(dev))
    if kn.get("nodata_floor") is not None:
        valid = valid & ~nodata_margin(x, valid, kn["nodata_floor"])
    if kn.get("common_footprint", False):
        # only pixels imaged on every date: per-date areas become comparable and the
        # swath edge (which moves between dates) cannot fake a change
        valid = valid & valid.all(0, keepdim=True)
    if kn.get("edge_erode_px", 0) > 0:
        valid = erode(valid, 2 * kn["edge_erode_px"] + 1)
    xf = quegan_filter(x, valid, kn["box"]) if kn["quegan"] else box_mean(x, valid, kn["box"])
    L = to_loglike(xf, source) * valid[:, None]
    do_norm = kn["normalise"] if normalise is None else normalise
    gains = offs = None
    if do_norm:
        med_hh = torch.where(valid, L[:, 0], torch.full_like(L[:, 0], float("nan"))).nanmedian(0).values
        L, gains, offs = normalise_dates(L, valid, kn["stable_frac"], mode=kn["norm_mode"], exclude=~(med_hh >= kn["water_hh_weak"]))
    water = water_masks(L, valid, kn)
    if water_ok is not None:
        water = water & water_ok.to(water.device)
    prior = permanent_water_prior(L, valid, kn)
    fv = flooded_veg_masks(L, valid, water, prior, kn)
    cdate, cstep, cstep_all = cusum_changepoint(L, valid, kn["change_step_min"], kn["persist_frac"],
                                                  kn["change_t_min"])
    cls = classify(water, fv, valid, cdate, kn)
    return {
        "L": L, "valid": valid, "water": water, "flooded_veg": fv, "prior_water": prior,
        "wind_suspect": wind_suspect(water, valid, kn),
        "change_first": change_vs_first(L, valid), "change_consec": change_consecutive(L, valid),
        "change_date": cdate, "change_step": cstep, "classes": cls,
        "gains": gains, "offsets": offs, "knobs": kn,
    }
