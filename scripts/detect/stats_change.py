"""Statistical radar change detection with a stated false-alarm rate, and uncertainty on areas.

    .venv/bin/python -m scripts.detect.stats_change [area ...]   # default: tonle-sap sevan ararat

Saves data/processed/<area>/stats/products.pt and docs/eda/stats/<area>_*.png.
Method note (formulas, assumptions, results): docs/eda/stats-change.md.

Pieces (all numerics in PyTorch, float64 on the CPU for the statistics):
  * noise model     : ENL estimators (spatial / temporal quantile matching), noise-vs-level curve
  * change tests    : pairwise gamma ratio test (exact, F / incomplete beta) and the
                      Conradsen-Nielsen-Skriver (2016) omnibus test with its sequential
                      factorisation R_j (several changes per pixel), single channel or HH+HV
  * calibration     : empirical null ("effective ENL") from the central body of the omnibus
                      statistic (Efron 2004 style), because the previews violate the gamma model
  * multiple testing: Benjamini-Hochberg FDR over pixels
  * water areas     : threshold / boundary / speckle-bootstrap uncertainty, per-pixel confidence
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import time

import torch
import torch.nn.functional as F

from scripts.detect import ops
from scripts.detect.stack import GAPS, ROOT, device, load_preview_stack

DT = torch.float64
FLOOR = 1.5 / 255          # preview display floor (calm water is drawn at 1/255)
PREVIEW_DB_RANGE = 30.0    # assumed dB span of the preview stretch (nuisance; cancels to first order)
SEVAN_KM2 = 1242.0         # published lake area used to bound the water threshold
SEVAN_WINDY = (5, 6)       # 31 Aug, 12 Sep 2026 (EDA)
FDR_Q = 0.01


# =============================== special functions ===============================
def chi2_sf(x, f):
    """P(chi2_f > x) = Q(f/2, x/2) (regularised upper incomplete gamma)."""
    x = torch.as_tensor(x, dtype=DT)
    f = torch.as_tensor(f, dtype=DT)
    return torch.special.gammaincc(f / 2, x.clamp_min(0) / 2)


def chi2_cdf(x, f):
    return 1.0 - chi2_sf(x, f)


def chi2_ppf(q, f, iters: int = 80):
    """Inverse chi-square CDF by bisection on gammainc (vectorised)."""
    q = torch.as_tensor(q, dtype=DT)
    f = torch.as_tensor(f, dtype=DT)
    lo = torch.zeros_like(q + f)
    hi = torch.full_like(lo, 1.0) * (f + 40 * torch.sqrt(2 * f) + 100)
    for _ in range(iters):
        mid = (lo + hi) / 2
        below = torch.special.gammainc(f / 2, mid / 2) < q
        lo, hi = torch.where(below, mid, lo), torch.where(below, hi, mid)
    return (lo + hi) / 2


def betainc(a, b, x, iters: int = 300):
    """Regularised incomplete beta I_x(a, b) by Lentz's continued fraction (Numerical Recipes betacf).

    torch.special has no betainc; this is vectorised float64 and checked against scipy in the tests.
    """
    a, b, x = (torch.as_tensor(v, dtype=DT) for v in (a, b, x))
    a, b, x = torch.broadcast_tensors(a, b, x)
    flip = x > (a + 1) / (a + b + 2)  # use symmetry I_x(a,b) = 1 - I_{1-x}(b,a) for convergence
    aa, bb, xx = torch.where(flip, b, a), torch.where(flip, a, b), torch.where(flip, 1 - x, x)
    tiny = 1e-300
    qab, qap, qam = aa + bb, aa + 1, aa - 1
    c = torch.ones_like(xx)
    d = 1 - qab * xx / qap
    d = torch.where(d.abs() < tiny, torch.full_like(d, tiny), d)
    d = 1 / d
    h = d.clone()
    for m in range(1, iters + 1):
        m2 = 2 * m
        num = m * (bb - m) * xx / ((qam + m2) * (aa + m2))
        d = 1 + num * d
        d = torch.where(d.abs() < tiny, torch.full_like(d, tiny), d)
        c = 1 + num / c
        c = torch.where(c.abs() < tiny, torch.full_like(c, tiny), c)
        d = 1 / d
        h = h * d * c
        num = -(aa + m) * (qab + m) * xx / ((aa + m2) * (qap + m2))
        d = 1 + num * d
        d = torch.where(d.abs() < tiny, torch.full_like(d, tiny), d)
        c = 1 + num / c
        c = torch.where(c.abs() < tiny, torch.full_like(c, tiny), c)
        d = 1 / d
        h = h * d * c
    lbeta = torch.lgamma(aa) + torch.lgamma(bb) - torch.lgamma(aa + bb)
    front = torch.exp(aa * torch.log(xx.clamp_min(tiny)) + bb * torch.log1p(-xx).clamp_min(-1e300) - lbeta) / aa
    res = (front * h).clamp(0, 1)
    res = torch.where(xx <= 0, torch.zeros_like(res), torch.where(xx >= 1, torch.ones_like(res), res))
    return torch.where(flip, 1 - res, res)


# =============================== input adapter ===================================
def pseudo_intensity(x: torch.Tensor, source: str, db_range: float = PREVIEW_DB_RANGE) -> torch.Tensor:
    """Linear-power-like intensity I for the gamma tests.

    gamma0 : I = linear power (clamped at 1e-6).
    preview: the display stretch looks logarithmic (noise is additive in brightness, see
             noise_curve), so b ~ (dB - dB_min)/db_range and I = 10**(db_range*b/10), with b
             clamped at the display floor. db_range is a nuisance: the effective ENL fitted
             from the data scales like 1/db_range**2, and -2 ln Q ~ n * db_range**2 * sum (b-b_mean)^2,
             so the calibrated test is invariant to it to first order.
    """
    if source == "gamma0":
        return x.to(DT).clamp_min(1e-6)
    if source == "preview":
        return torch.pow(10.0, db_range * x.to(DT).clamp_min(FLOOR / 3) / 10.0)
    raise ValueError(source)


# =============================== noise model =====================================
def _cv2_quantile_gamma(L: float, N: int, q: float, nsim: int = 40000, seed: int = 0) -> float:
    g = torch.Generator().manual_seed(seed)
    s = torch._standard_gamma(torch.full((nsim, N), float(L), dtype=DT), generator=g) / L
    cv2 = s.var(1, unbiased=True) / s.mean(1) ** 2
    return float(torch.quantile(cv2, q))


def enl_from_cv2(cv2: torch.Tensor, N: int, q: float = 0.25) -> float:
    """Quantile-matching ENL estimator.

    Given per-window squared coefficients of variation CV^2 = s^2 / m^2 (s^2 unbiased, N samples
    per window), find L such that the q-quantile of CV^2 for pure gamma(L) speckle (simulated)
    equals the data's q-quantile. A LOW quantile is used because texture and change only ever
    increase CV^2, so the lower part of the distribution is dominated by homogeneous windows.
    For pure speckle this is unbiased (no 1/CV^2 small-sample bias), which the tests check.
    """
    target = float(torch.quantile(cv2.to(DT).flatten()[:: max(1, cv2.numel() // 2_000_000)], q))
    lo, hi = math.log(0.3), math.log(1e5)
    for _ in range(30):
        mid = (lo + hi) / 2
        if _cv2_quantile_gamma(math.exp(mid), N, q) > target:  # too noisy -> need more looks
            lo = mid
        else:
            hi = mid
    return math.exp((lo + hi) / 2)


def enl_spatial(I: torch.Tensor, mask: torch.Tensor, k: int = 7, q: float = 0.25) -> float:
    """Classic spatial ENL = m^2/s^2 in non-overlapping k x k windows, robustified by quantile matching.

    I [N, H, W] intensities (any number of images), mask [N, H, W]; windows must be fully valid.
    """
    I = I.to(DT)
    m = F.avg_pool2d(I[:, None], k, k)[:, 0]
    m2 = F.avg_pool2d(I[:, None] ** 2, k, k)[:, 0]
    full = F.avg_pool2d(mask.to(DT)[:, None], k, k)[:, 0] > 0.999
    N = k * k
    var = (m2 - m ** 2).clamp_min(0) * N / (N - 1)
    cv2 = (var / m.clamp_min(1e-30) ** 2)[full]
    return enl_from_cv2(cv2, N, q)


def enl_temporal(I: torch.Tensor, mask: torch.Tensor, q: float = 0.25) -> float:
    """Temporal ENL: CV^2 over the T dates of each pixel (I [T, H, W], mask [H, W]), quantile matched."""
    T = I.shape[0]
    cv2 = (I.to(DT).var(0, unbiased=True) / I.to(DT).mean(0) ** 2)[mask]
    return enl_from_cv2(cv2, T, q)


def noise_curve(b: torch.Tensor, mask: torch.Tensor, edges=None, qs=(0.1, 0.25, 0.5)) -> dict:
    """Noise vs level for one channel. b [T, H, W] (brightness or dB), mask [H, W] (no floor pixels).

    Per pixel: temporal mean and temporal std over the T dates; spatial 5x5 std per date.
    Returns, per level bin, low quantiles of both stds (low quantiles = least real change / texture).
    """
    if edges is None:
        edges = torch.linspace(0.02, 0.92, 19)
    b = b.to(DT)
    tm, ts = b.mean(0), b.std(0)
    sm = F.avg_pool2d(b[:, None], 5, 1, 2)[:, 0]
    ss = (F.avg_pool2d(b[:, None] ** 2, 5, 1, 2)[:, 0] - sm ** 2).clamp_min(0).sqrt()
    out = {"level": [], "n": [], "temporal": [], "spatial": []}
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = mask & (tm >= lo) & (tm < hi)
        sel_s = mask[None] & (sm >= lo) & (sm < hi)
        if sel.sum() < 300 or sel_s.sum() < 300:
            continue
        qq = torch.tensor(qs, dtype=DT)
        out["level"].append(float((lo + hi) / 2))
        out["n"].append(int(sel.sum()))
        out["temporal"].append(torch.quantile(ts[sel][:: max(1, int(sel.sum()) // 200000)], qq).tolist())
        out["spatial"].append(torch.quantile(ss[sel_s][:: max(1, int(sel_s.sum()) // 200000)], qq).tolist())
    out["qs"] = list(qs)
    return out


# =============================== change tests ====================================
def rho_omnibus(k: int, n):
    """Conradsen et al. 2016, p = 1: rho = 1 - (2p^2-1)/(6(k-1)p) * (k/n - 1/(n k))."""
    n = torch.as_tensor(n, dtype=DT)
    return 1 - (1.0 / (6 * (k - 1))) * (k / n - 1 / (n * k))


def omega2_omnibus(k: int, n):
    """p = 1: omega2 = -p^2/4 (1 - 1/rho)^2 + p^2(p^2-1)/24 (...) = -(1/4)(1 - 1/rho)^2."""
    r = rho_omnibus(k, n)
    return -0.25 * (1 - 1 / r) ** 2


def rho_rj(j: int, n):
    """Sequential R_j (j = number of dates in the segment incl. the tested one, j >= 2), p = 1:
    rho_j = 1 - (2p^2-1)/(6pn) (1 + 1/(j(j-1)))."""
    n = torch.as_tensor(n, dtype=DT)
    return 1 - (1 + 1 / (j * (j - 1))) / (6 * n)


def lnq_unit(I: torch.Tensor) -> torch.Tensor:
    """ln Q / n per channel for "all k dates have the same mean" (I [k, C, ...]):
    ln Q = n [k ln k + sum_i ln I_i - k ln sum_i I_i]  (<= 0; = 0 when all equal)."""
    k = I.shape[0]
    return k * math.log(k) + torch.log(I).sum(0) - k * torch.log(I.sum(0))


def chi2_corrected_sf(z, f, w2):
    """Box-type approximation P(-2 rho ln Q > z) = P(chi2_f > z) + w2 [P(chi2_{f+4} > z) - P(chi2_f > z)]."""
    p = chi2_sf(z, f)
    return (p + w2 * (chi2_sf(z, f + 4) - p)).clamp(0, 1)


def omnibus_pvalue(I: torch.Tensor, n) -> tuple[torch.Tensor, torch.Tensor]:
    """Omnibus test H0: E[I_1] = ... = E[I_k], channels treated as independent.

    I [k, C, H, W], n = ENL per channel (len C). Statistic Z = sum_c -2 rho_c n_c lnq_unit_c,
    approximately chi2 with f = C (k-1) degrees of freedom (omega2 summed over channels).
    Returns (p [H, W], Z [H, W]).
    """
    k, C = I.shape[:2]
    n = torch.as_tensor(n, dtype=DT).reshape(C, *([1] * (I.dim() - 2)))
    lq = lnq_unit(I)
    Z = (-2 * rho_omnibus(k, n) * n * lq).sum(0)
    w2 = omega2_omnibus(k, n).sum()
    return chi2_corrected_sf(Z, C * (k - 1), w2), Z


def pair_pvalue_exact(I1: torch.Tensor, I2: torch.Tensor, n) -> torch.Tensor:
    """Exact two-sided test of equal means for two gamma(n) intensities (same n).

    Under H0, r = I1/I2 ~ F(2n, 2n), with CDF I_{r/(1+r)}(n, n). The likelihood-ratio statistic
    Q = 4^n (I1 I2)^n / (I1+I2)^{2n} is a symmetric function of ln r, so the LR test is the
    equal-tailed F test: p = 2 min(F(r), 1 - F(r)).
    """
    n = torch.as_tensor(n, dtype=DT)
    r = I1.to(DT) / I2.to(DT)
    cdf = betainc(n, n, r / (1 + r))
    return (2 * torch.minimum(cdf, 1 - cdf)).clamp(0, 1)


def pair_pvalue_lr(I1: torch.Tensor, I2: torch.Tensor, n) -> torch.Tensor:
    """Same test through -2 rho ln Q with k = 2 and its chi2_1 approximation (any number of channels on dim 0)."""
    return omnibus_pvalue(torch.stack([I1, I2]), n)[0]


def suffix_omnibus_p(I: torch.Tensor, n) -> torch.Tensor:
    """p-value of the omnibus test over dates s..T-1, for every start s (p[s] = 1 if fewer than 2 dates)."""
    T = I.shape[0]
    out = torch.ones((T,) + tuple(I.shape[2:]), dtype=DT)
    for s in range(T - 1):
        out[s] = omnibus_pvalue(I[s:], n)[0]
    return out


def sequential_changes(I: torch.Tensor, n, gate0: torch.Tensor, alpha: float) -> dict:
    """Points of change (Conradsen et al. 2016, Sec. on the factorisation Q = prod_j R_j).

    I [T, C, H, W] intensities, n = ENL per channel, gate0 [H, W] bool: pixels whose omnibus test
    over ALL dates is significant (after FDR). Per pixel, segment start s = 0:
      for j = s+1 .. T-1:  m = j - s + 1 dates in the segment,
        ln R_j / n = m ln m - (m-1) ln(m-1) + (m-1) ln S + ln I_j - m ln(S + I_j),  S = sum_{i=s}^{j-1} I_i
        Z_j = sum_c -2 rho_j n_c lnR_c ~ chi2_C  (date j vs the pooled dates s..j-1)
        a change at j is declared if the segment's omnibus test (dates s..T-1) is significant
        (gate0 for s = 0, level alpha for s > 0) AND p(Z_j) < alpha; then s = j (restart).
    Returns change [T, H, W] bool (change at date j), n_changes, first, largest (date of max Z_j
    among detected changes), direction_ch [C, H, W] (+1 brighter / -1 darker at the largest change),
    direction [H, W] (channel with the larger |log-ratio|), zmax.
    """
    T, C, H, W = I.shape
    n = torch.as_tensor(n, dtype=DT).reshape(C, 1, 1)
    p_suffix = suffix_omnibus_p(I, n.flatten())
    s = torch.zeros((H, W), dtype=torch.long)
    S = I[0].clone()
    change = torch.zeros((T, H, W), dtype=torch.bool)
    zbest = torch.zeros((H, W), dtype=DT)
    largest = torch.full((H, W), -1, dtype=torch.long)
    dir_ch = torch.zeros((C, H, W), dtype=torch.int8)
    for j in range(1, T):
        m = (j - s + 1).to(DT)  # [H, W]
        mm1 = m - 1
        Ij = I[j]
        lnr = (m * torch.log(m) - mm1 * torch.log(mm1) + mm1 * torch.log(S) + torch.log(Ij)
               - m * torch.log(S + Ij))
        rho = 1 - (1 + 1 / (m * mm1)) / (6 * n)
        Z = (-2 * rho * n * lnr).sum(0)
        w2 = (-0.25 * (1 - 1 / rho) ** 2).sum(0)
        p = chi2_corrected_sf(Z, C, w2)
        gate = torch.where(s == 0, gate0, p_suffix.gather(0, s[None])[0] < alpha)
        hit = gate & (p < alpha)
        change[j] = hit
        logratio = torch.log(Ij / (S / mm1))  # [C, H, W]
        better = hit & (Z > zbest)
        zbest = torch.where(better, Z, zbest)
        largest = torch.where(better, torch.full_like(largest, j), largest)
        dir_ch = torch.where(better[None], torch.sign(logratio).to(torch.int8), dir_ch)
        s = torch.where(hit, torch.full_like(s, j), s)
        S = torch.where(hit[None], Ij, S + Ij)
        if j == 1:
            lr_store = torch.zeros((T, C, H, W), dtype=torch.float32)
        lr_store[j] = logratio.float()
    first = torch.where(change.any(0), change.to(torch.uint8).argmax(0), torch.full((H, W), -1, dtype=torch.long))
    # direction (dominant channel) at the largest change
    lj = largest.clamp_min(0)
    lr_at = lr_store.gather(0, lj[None, None].expand(1, C, H, W))[0]
    dom = lr_at.abs().argmax(0)
    direction = torch.where(largest >= 0, torch.sign(lr_at.gather(0, dom[None])[0]).to(torch.int8),
                            torch.zeros((H, W), dtype=torch.int8))
    return {"change": change, "n_changes": change.sum(0).to(torch.int16), "first": first.to(torch.int16),
            "largest": largest.to(torch.int16), "direction": direction, "direction_ch": dir_ch, "zmax": zbest}


def bh(p: torch.Tensor, mask: torch.Tensor, q: float) -> tuple[torch.Tensor, float]:
    """Benjamini-Hochberg: reject p_(i) <= p* where p* = max{p_(k) : p_(k) <= k q / m} over the m tested pixels."""
    v = p[mask].flatten().to(DT)
    m = v.numel()
    if m == 0:
        return torch.zeros_like(mask), 0.0
    srt = v.sort().values
    ok = srt <= torch.arange(1, m + 1, dtype=DT) * q / m
    pstar = float(srt[ok.nonzero().max()]) if ok.any() else 0.0
    return (p <= pstar) & mask & ok.any(), pstar


# =============================== empirical null =================================
def calibrate_neff(I: torch.Tensor, mask: torch.Tensor, qs=(0.1, 0.2, 0.3, 0.4, 0.5)) -> tuple[list, dict]:
    """Effective ENL per channel from the central body of the omnibus statistic (empirical null).

    For each channel, u = -2 lnq_unit (n-free). Under H0, rho(n) n u ~ chi2_{k-1}. Assuming at least
    half of the pixels are unchanged (changes only make u larger), match the q-quantiles of u for
    q in qs:  n_q = chi2^{-1}_{k-1}(q) / (rho(n) u_q), iterate for rho, take the median over q.
    (Efron 2004: fit the null to the centre of the observed distribution, not to theory.)
    Returns ([n_eff per channel], diagnostics).
    """
    k, C = I.shape[:2]
    out, diag = [], {}
    qq = torch.tensor(qs, dtype=DT)
    chi = chi2_ppf(qq, k - 1)
    for c in range(C):
        u = (-2 * lnq_unit(I[:, c]))[mask]
        u = u[:: max(1, u.numel() // 2_000_000)]
        uq = torch.quantile(u, qq)
        n = torch.full_like(uq, 10.0)
        for _ in range(20):
            n = chi / (rho_omnibus(k, n) * uq)
        out.append(float(n.median()))
        diag[c] = {"n_per_quantile": n.tolist()}
    return out, diag


def null_fit_report(I: torch.Tensor, mask: torch.Tensor, n_theory, n_emp, qs=(0.5, 0.9, 0.95, 0.99, 0.999)) -> dict:
    """Quantiles of the joint omnibus statistic Z on `mask` pixels versus chi2_f, under both ENLs,
    and the fraction of `mask` pixels with p < 0.05 / 0.01 / 0.001 under each."""
    k, C = I.shape[:2]
    f = C * (k - 1)
    rep = {"f": f}
    qq = torch.tensor(qs, dtype=DT)
    for name, n in (("theory", n_theory), ("empirical", n_emp)):
        p, Z = omnibus_pvalue(I, n)
        z, pv = Z[mask], p[mask]
        rep[name] = {"n": list(map(float, n)), "Z_quantiles": torch.quantile(z[:: max(1, z.numel() // 1_000_000)], qq).tolist(),
                     "chi2_quantiles": chi2_ppf(qq, f).tolist(),
                     "frac_p_below": {a: float((pv < a).to(DT).mean()) for a in (0.05, 0.01, 0.001)}}
    rep["qs"] = list(qs)
    return rep


def empirical_null_p(Z: torch.Tensor, ref: torch.Tensor, tail_q: float = 0.95) -> torch.Tensor:
    """p-values of Z against an empirical null sample `ref` (statistics of known-stable pixels).

    Body: p = (1 + #{ref >= z}) / (N + 1). Tail (z above the tail_q quantile u of ref): peaks over
    threshold with an exponential excess (generalised Pareto, shape 0), p = (1 - tail_q) exp(-(z - u)/beta),
    beta = mean excess of ref over u. This extrapolates below 1/N, which BH needs.
    """
    ref = ref.to(DT).flatten().sort().values
    N = ref.numel()
    u = float(torch.quantile(ref, tail_q))
    beta = float((ref[ref > u] - u).mean().clamp_min(1e-9))
    z = Z.to(DT)
    ge = N - torch.searchsorted(ref, z.flatten().contiguous(), right=False).reshape(z.shape)
    p_body = (1 + ge.to(DT)) / (N + 1)
    p_tail = (1 - tail_q) * torch.exp(-(z - u) / beta)
    return torch.where(z > u, p_tail, p_body).clamp(0, 1)


# =============================== water uncertainty ===============================
def perimeter_km(mask: torch.Tensor, px_km: float) -> torch.Tensor:
    """Water/non-water 4-neighbour edge length per date (mask [T, H, W]) in km."""
    m = mask.to(torch.int8)
    e = (m[:, 1:] != m[:, :-1]).sum((1, 2)) + (m[:, :, 1:] != m[:, :, :-1]).sum((1, 2))
    return e.to(DT) * px_km


def seasonal_mask(water: torch.Tensor, valid: torch.Tensor, kn: dict) -> torch.Tensor:
    """Seasonal open water as in ops.classify: water on >= seasonal_min_dates dates, not permanent."""
    nv = valid.sum(0).clamp_min(1)
    nw = (water & valid).sum(0)
    perm = (nw.float() / nv >= kn["perm_frac"]) & (nw > 0)
    return (nw >= kn["seasonal_min_dates"]) & ~perm


def _water_with(L, valid, kn, weak):
    k2 = dict(kn)
    ratio = kn["water_hh_strong"] / kn["water_hh_weak"] if kn["water_hh_weak"] > 0 else None
    k2["water_hh_weak"] = weak
    k2["water_hh_strong"] = weak * ratio if ratio is not None else weak - (kn["water_hh_weak"] - kn["water_hh_strong"])
    return ops.water_masks(L, valid, k2)


_SEVAN_CACHE = {}


def sevan_threshold_interval(dev=None, grid=None) -> dict:
    """Interval of weak HH thresholds (strong kept at the same ratio) whose mean Sevan area over the
    calm dates stays within +-1 shoreline pixel (perimeter x pixel side) of the published 1,242 km2."""
    if "res" in _SEVAN_CACHE:
        return _SEVAN_CACHE["res"]
    dev = dev or device()
    st = load_preview_stack("sevan")
    out = ops.run_pipeline(st.x, st.valid, "preview", dev=dev)
    kn = out["knobs"]
    px_km = math.sqrt(st.km2_per_px)
    calm = [t for t in range(len(st.dates)) if t not in SEVAN_WINDY]
    tol = float(perimeter_km(out["water"][calm].cpu(), px_km).mean()) * px_km
    grid = grid if grid is not None else torch.linspace(0.03, 0.22, 39).tolist()
    areas = []
    for w in grid:
        wm = _water_with(out["L"], out["valid"], kn, w).cpu()
        areas.append(float(wm[calm].sum((1, 2)).to(DT).mean()) * st.km2_per_px)
    inside = [w for w, a in zip(grid, areas) if abs(a - SEVAN_KM2) <= tol]
    res = {"grid": grid, "calm_area_km2": areas, "tol_km2": tol, "lo": min(inside), "hi": max(inside)}
    _SEVAN_CACHE["res"] = res
    return res


def _chain(x, valid, kn, source):
    """Filter -> log-like -> (normalise) as in ops.run_pipeline, on a fixed `valid`."""
    xf = ops.quegan_filter(x, valid, kn["box"]) if kn["quegan"] else ops.box_mean(x, valid, kn["box"])
    L = ops.to_loglike(xf, source) * valid[:, None]
    if kn["normalise"]:
        med = torch.where(valid, L[:, 0], torch.full_like(L[:, 0], float("nan"))).nanmedian(0).values
        L, _, _ = ops.normalise_dates(L, valid, kn["stable_frac"], mode=kn["norm_mode"],
                                      exclude=~(med >= kn["water_hh_weak"]))
    return L


def water_uncertainty(x, out, source, km2, n_eff, thr_lo, thr_hi, n_thr=9, n_boot=16, n_mc=4000,
                      db_range=PREVIEW_DB_RANGE, seed=0) -> dict:
    """Open-water area per date and seasonal-water area with three uncertainty components.

    (a) threshold: weak threshold uniform on [thr_lo, thr_hi] (n_thr values), re-detect (ops.water_masks).
    (b) boundary : mixed shoreline pixels, +-(perimeter x pixel/2), uniform, fully correlated along the shore.
    (c) speckle  : parametric bootstrap. x_hat = Quegan-filtered input (signal estimate); new noise at the
                   calibrated level (preview: additive Gaussian in brightness, sd = 10 sqrt(psi1(n))/(D ln10);
                   gamma0: x_hat * Gamma(n, 1/n)); filter, normalise, re-detect, n_boot times.
    Monte Carlo combination: A = A_thr[i] + (A_boot[j] - mean A_boot) + U(-1,1) * P * px/2.
    Also returns per-pixel sd of the filtered HH (bootstrap) for the confidence layer.
    """
    dev = out["L"].device
    kn, valid, L0 = out["knobs"], out["valid"], out["L"]
    T = L0.shape[0]
    px_km = math.sqrt(km2)
    g = torch.Generator(device="cpu").manual_seed(seed)
    thr = torch.linspace(thr_lo, thr_hi, n_thr).tolist()
    A_thr, S_thr = [], []
    for w in thr:
        wm = _water_with(L0, valid, kn, w)
        A_thr.append((wm.sum((1, 2)).cpu().to(DT) * km2))
        S_thr.append(float(seasonal_mask(wm, valid, kn).sum()) * km2)
    A_thr, S_thr = torch.stack(A_thr), torch.tensor(S_thr, dtype=DT)
    base = out["water"]
    P = perimeter_km(base.cpu(), px_km)
    P_seas = float(perimeter_km(seasonal_mask(base, valid, kn)[None].cpu(), px_km)[0])
    # bootstrap
    xin = ops.clean_input(x.to(dev), valid)[0]
    xhat = ops.quegan_filter(xin, valid, kn["box"]) if kn["quegan"] else ops.box_mean(xin, valid, kn["box"])
    nn = torch.tensor(n_eff, dtype=torch.float32).reshape(1, -1, 1, 1)
    sd_b = (10 * torch.special.polygamma(1, nn.to(DT)).sqrt() / (db_range * math.log(10))).float()
    A_b, S_b = [], []
    s1 = torch.zeros_like(L0[:, 0])
    s2 = torch.zeros_like(L0[:, 0])
    for b in range(n_boot):
        if source == "preview":
            eps = torch.randn(xhat.shape, generator=g).to(dev)
            xs = (xhat + sd_b.to(dev) * eps).clamp(1 / 255, 1.0)
            xs = (xs * 255).round() / 255  # 8-bit quantisation as in the PNGs
        else:
            gm = torch._standard_gamma(nn.expand_as(xhat).cpu().contiguous(), generator=g).to(dev) / nn.to(dev)
            xs = xhat * gm
        xs = xs * valid[:, None]
        Ls = _chain(xs, valid, kn, source)
        wm = ops.water_masks(Ls, valid, kn)
        A_b.append(wm.sum((1, 2)).cpu().to(DT) * km2)
        S_b.append(float(seasonal_mask(wm, valid, kn).sum()) * km2)
        s1 += Ls[:, 0]
        s2 += Ls[:, 0] ** 2
    A_b, S_b = torch.stack(A_b), torch.tensor(S_b, dtype=DT)
    sd_L = ((s2 / n_boot - (s1 / n_boot) ** 2).clamp_min(0) * n_boot / max(1, n_boot - 1)).sqrt()
    # Monte Carlo combination
    i = torch.randint(0, n_thr, (n_mc,), generator=g)
    j = torch.randint(0, n_boot, (n_mc,), generator=g)
    u = torch.rand(n_mc, generator=g, dtype=DT) * 2 - 1
    A_mc = A_thr[i] + (A_b[j] - A_b.mean(0)) + u[:, None] * (P * px_km / 2)[None]
    S_mc = S_thr[i] + (S_b[j] - S_b.mean()) + u * P_seas * px_km / 2
    qq = torch.tensor([0.05, 0.5, 0.95], dtype=DT)

    def band(v):  # 5-95 % width of each component alone
        return (torch.quantile(v, 0.95, dim=0) - torch.quantile(v, 0.05, dim=0))

    comp = {"threshold": band(A_thr), "boundary": 0.9 * P * px_km, "speckle": band(A_b)}
    comp_s = {"threshold": float(band(S_thr)), "boundary": 0.9 * P_seas * px_km, "speckle": float(band(S_b))}
    A_mc, S_mc = A_mc.clamp_min(0), S_mc.clamp_min(0)  # an area cannot be negative
    qa, qs_ = torch.quantile(A_mc, qq, dim=0), torch.quantile(S_mc, qq)
    return {"water_km2_baseline": base.sum((1, 2)).cpu().to(DT) * km2,
            "water_km2_lo": qa[0], "water_km2_median": qa[1], "water_km2_hi": qa[2],
            "seasonal_km2": {"baseline": float(seasonal_mask(base, valid, kn).sum()) * km2,
                             "lo": float(qs_[0]), "median": float(qs_[1]), "hi": float(qs_[2]), "components": comp_s},
            "components_5_95_width": {k: v.tolist() for k, v in comp.items()},
            "perimeter_km": P.tolist(), "thresholds": thr, "sd_L_hh": sd_L,
            "A_thr": A_thr, "A_boot": A_b}


def water_confidence(L_hh: torch.Tensor, sd_L: torch.Tensor, thr: float, sd_thr: float) -> torch.Tensor:
    """p_water = Phi((thr - L_hh) / sqrt(sd_L^2 + sd_thr^2)) in 0..1 (0.5 = on the threshold).

    sd_L: per-pixel noise sd of the filtered HH (bootstrap); sd_thr: sd of the threshold uncertainty
    (uniform interval -> width/sqrt(12)). Ignores hysteresis connectivity and the loose HV test.
    """
    s = (sd_L.to(DT) ** 2 + sd_thr ** 2).sqrt().clamp_min(1e-6)
    return torch.special.ndtr((thr - L_hh.to(DT)) / s)


# =============================== area driver =====================================
def load_stack(area: str):
    """(dates, x [T,2,H,W], valid [T,H,W], km2_per_px, source, bounds)."""
    if area == "demo" or (ROOT / "data/processed" / area / "gamma0/hh").exists():
        from scripts.detect.run import load_gamma0
        dates, x, valid, grid, km2 = load_gamma0(area)
        b = grid.transform
        bounds = (b.c, b.f + b.e * grid.h, b.c + b.a * grid.w, b.f)
        return dates, x, valid, km2, "gamma0", bounds
    st = load_preview_stack(area)
    return st.dates, st.x, st.valid, st.km2_per_px, "preview", st.bounds


def analyse(area: str, q: float = FDR_Q, dev=None, n_boot: int = 16, verbose: bool = True) -> dict:
    """Everything for one area. Returns the products dict (CPU tensors + metadata)."""
    t0 = time.time()
    dev = dev or device()
    dates, x, valid_in, km2, source, bounds = load_stack(area)
    out = ops.run_pipeline(x, valid_in, source, dev=dev)
    kn = out["knobs"]
    valid = out["valid"].cpu()
    dom = valid.all(0)
    T, C, H, W = x.shape
    I = pseudo_intensity(torch.nan_to_num(x, nan=1.0), source)
    # ---- noise model
    if source == "preview":
        nofloor = dom & (x[:, 0] > FLOOR).all(0) & (x[:, 1] > FLOOR).all(0)
    else:
        nofloor = dom.clone()
    curves = {ch: noise_curve(x[:, c] if source == "preview" else 10 * torch.log10(I[:, c]),
                              nofloor, None if source == "preview" else torch.linspace(-35, 5, 21))
              for c, ch in enumerate(("HH", "HV"))}
    enl_sp = [enl_spatial(I[:, c], nofloor[None].expand(T, H, W)) for c in range(C)]
    enl_tm = [enl_temporal(I[:, c], nofloor) for c in range(C)]
    n_emp, cal_diag = calibrate_neff(I, nofloor)
    # ---- stable reference targets (chosen by level / location, not by the statistic)
    ever_water = out["water"].cpu().any(0) | out["prior_water"].cpu()
    near_water = ops.dilate(ever_water[None], 21)[0]
    hv_mean = x[:, 1].mean(0)
    hv_cut = float(torch.quantile(hv_mean[nofloor & ~near_water][::7], 0.85)) if (nofloor & ~near_water).any() else 1.0
    forest = nofloor & ~near_water & (hv_mean >= hv_cut)
    lake_core = ops.erode(out["water"].cpu().all(0)[None], 11)[0] & dom
    nulls = {"all_nofloor": null_fit_report(I, nofloor, enl_sp, n_emp),
             "forest_proxy": null_fit_report(I, forest, enl_sp, n_emp) if forest.sum() > 500 else None}
    # ---- tests
    p_spk, Z = omnibus_pvalue(I, n_emp)                     # speckle-only null (effective ENL)
    p_omni_th, _ = omnibus_pvalue(I, enl_sp)               # textbook null (spatial ENL)
    p_omni_ch = [omnibus_pvalue(I[:, c:c + 1], n_emp[c:c + 1])[0] for c in range(C)]
    rr, cc = torch.meshgrid(torch.arange(H), torch.arange(W), indexing="ij")
    checker = ((rr // 32 + cc // 32) % 2 == 0)
    ref_cal, ref_out = forest & checker, forest & ~checker   # calibrate on half, test on the other half
    if ref_cal.sum() >= 500:
        p_omni = empirical_null_p(Z, Z[ref_cal])           # stable-land null (main)
        null_kind = "stable-land (bright-HV land far from water, checkerboard half)"
    else:
        p_omni, null_kind = p_spk, "speckle (effective ENL); too few stable-land pixels"
        ref_out = forest
    changed, pstar = bh(p_omni, dom, q)
    changed_spk, pstar_spk = bh(p_spk, dom, q)
    seq = sequential_changes(I, n_emp, changed, q)
    p_pair_ch = torch.stack([pair_pvalue_exact(I[-1, c], I[0, c], n_emp[c]) for c in range(C)]).float()
    p_pair = pair_pvalue_lr(I[0], I[-1], n_emp).float()
    # ---- compare with CUSUM
    cus = out["change_date"].cpu().long()
    cmp = compare_cusum(seq, changed, cus, dom, km2)
    fa = {}
    for name, m in (("forest_proxy_heldout", ref_out), ("lake_core", lake_core)):
        if m.sum() < 100:
            continue
        fa[name] = {"km2": float(m.sum()) * km2,
                    "stats_fdr": float(changed[m].to(DT).mean()),
                    "stats_speckle_fdr": float(changed_spk[m].to(DT).mean()),
                    "stats_theory_p<p*": float((p_omni_th[m] <= pstar_spk).to(DT).mean()),
                    "stats_HH_only_p<p*": float((p_omni_ch[0][m] <= pstar).to(DT).mean()),
                    "cusum": float((cus[m] >= 0).to(DT).mean())}
    # ---- water uncertainty
    if source == "preview":
        si = sevan_threshold_interval(dev)
        thr_lo, thr_hi = si["lo"], si["hi"]
    else:
        si = None
        thr_lo, thr_hi = kn["water_hh_weak"] - 2.0, kn["water_hh_weak"] + 2.0  # +-2 dB, untested
    wu = water_uncertainty(x, out, source, km2, n_emp, thr_lo, thr_hi, n_boot=n_boot)
    sd_thr = (thr_hi - thr_lo) / math.sqrt(12)
    conf = water_confidence(out["L"][:, 0].cpu(), wu["sd_L_hh"].cpu(), kn["water_hh_weak"], sd_thr)
    conf = torch.where(valid, conf, torch.zeros_like(conf)).to(torch.float16)
    flood = flood_wave(seq, cus, out, dom, bounds, H, W) if area == "tonle-sap" else None
    prod = {
        "area": area, "source": source, "dates": dates, "bounds": bounds, "km2_per_px": km2, "fdr_q": q,
        "bh_p_threshold": pstar, "valid": dom, "null_kind": null_kind,
        "changed_fdr_speckle": changed_spk, "bh_p_threshold_speckle": pstar_spk,
        "changed_km2_speckle": float(changed_spk.sum()) * km2, "p_omnibus_speckle": p_spk.float(),
        "p_omnibus": p_omni.float(), "p_omnibus_theory": p_omni_th.float(),
        "p_omnibus_hh": p_omni_ch[0].float(), "p_omnibus_hv": p_omni_ch[1].float(),
        "changed_fdr": changed, "change_events": seq["change"], "n_changes": seq["n_changes"],
        "first_change": seq["first"], "largest_change": seq["largest"], "direction": seq["direction"],
        "direction_ch": seq["direction_ch"], "p_pair_first_last": p_pair, "p_pair_first_last_ch": p_pair_ch,
        "water_conf": conf, "water_km2_baseline": wu["water_km2_baseline"].float(),
        "water_km2_median": wu["water_km2_median"].float(), "water_km2_lo": wu["water_km2_lo"].float(),
        "water_km2_hi": wu["water_km2_hi"].float(), "seasonal_km2": wu["seasonal_km2"],
        "area_components_5_95_width": wu["components_5_95_width"], "perimeter_km": wu["perimeter_km"],
        "water_thresholds": wu["thresholds"], "sevan_interval": si,
        "enl": {"spatial_quantile_match": enl_sp, "temporal_quantile_match": enl_tm, "effective_empirical": n_emp,
                "calibration": cal_diag, "estimator": "quantile matching of CV^2 (q=0.25) to simulated gamma",
                "preview_db_range_assumed": PREVIEW_DB_RANGE if source == "preview" else None},
        "noise_curve": curves, "null_fit": nulls, "false_alarm": fa, "cusum_compare": cmp, "flood_wave": flood,
        "changed_km2": float(changed.sum()) * km2, "domain_km2": float(dom.sum()) * km2,
        "runtime_s": round(time.time() - t0, 1),
        "_aux": {"cusum_date": cus.to(torch.int16), "forest": forest, "lake_core": lake_core,
                 "hh_raw": x[:, 0].float(), "L_hh": out["L"][:, 0].cpu().float(), "water": out["water"].cpu(),
                 "A_thr": wu["A_thr"], "A_boot": wu["A_boot"]},
    }
    if verbose:
        print_tables(prod)
    return prod


def compare_cusum(seq, changed, cus, dom, km2) -> dict:
    a, b = changed & dom, (cus >= 0) & dom
    both = a & b
    first = seq["first"].long()
    exact = (first[both] == cus[both]).to(DT).mean() if both.any() else torch.tensor(float("nan"))
    near = ((first[both] - cus[both]).abs() <= 1).to(DT).mean() if both.any() else torch.tensor(float("nan"))
    # CUSUM dates the single biggest step: compare with the stats "largest change" too
    larg = seq["largest"].long()
    exact_l = (larg[both] == cus[both]).to(DT).mean() if both.any() else torch.tensor(float("nan"))
    return {"stats_km2": float(a.sum()) * km2, "cusum_km2": float(b.sum()) * km2,
            "both_km2": float(both.sum()) * km2, "stats_only_km2": float((a & ~b).sum()) * km2,
            "cusum_only_km2": float((b & ~a).sum()) * km2,
            "agreement_changed_unchanged": float(((a == b) & dom).sum() / dom.sum()),
            "date_first_eq_cusum": float(exact), "date_first_within1": float(near),
            "date_largest_eq_cusum": float(exact_l),
            "n_changes_hist_km2": {int(k): float((seq["n_changes"][dom] == k).sum()) * km2 for k in range(0, 6)}}


def flood_wave(seq, cus, out, dom, bounds, H, W) -> dict:
    """Tonle Sap: seasonal-water pixels in the south-east plain (lon > 104.5, lat < 12.5) and the rest
    (lake ring). Reference timing = first date the pixel is mapped as water (ops.water_masks)."""
    w, s, e, n = bounds
    lon = w + (torch.arange(W) + 0.5) * (e - w) / W
    lat = n - (torch.arange(H) + 0.5) * (n - s) / H
    LAT, LON = torch.meshgrid(lat, lon, indexing="ij")
    seas = (out["classes"].cpu() == 2) & dom
    water = out["water"].cpu()
    fw = torch.where(water.any(0), water.to(torch.uint8).argmax(0), torch.full((H, W), -1)).long()
    res = {}
    for name, reg in (("south-east plain", seas & (LON > 104.5) & (LAT < 12.5)),
                      ("lake ring (rest)", seas & ~((LON > 104.5) & (LAT < 12.5)))):
        if reg.sum() == 0:
            continue
        f, c = seq["first"].long()[reg], cus[reg]
        r = {"px": int(reg.sum()),
             "stats_detected": float((f >= 0).to(DT).mean()), "cusum_detected": float((c >= 0).to(DT).mean()),
             "first_water_hist": torch.bincount(fw[reg].clamp_min(0), minlength=10).tolist(),
             "stats_first_hist": torch.bincount(f[f >= 0], minlength=10).tolist(),
             "cusum_hist": torch.bincount(c[c >= 0], minlength=10).tolist()}
        # do the change dates match the first water date? (any detected change within +-1 date)
        ev = seq["change"][:, reg]  # [T, P]
        fwr = fw[reg]
        tt = torch.arange(ev.shape[0])[:, None]
        hitw = (ev & ((tt - fwr[None]).abs() <= 1)).any(0)
        r["stats_any_change_within1_of_first_water"] = float(hitw.to(DT).mean())
        r["cusum_within1_of_first_water"] = float(((c - fwr).abs() <= 1).to(DT).mean())
        res[name] = r
    return res


# =============================== reporting =======================================
def print_tables(p: dict):
    e = p["enl"]
    print(f"\n=== {p['area']} ({p['source']}) domain {p['domain_km2']:.0f} km2, runtime {p['runtime_s']} s")
    print(f"ENL spatial (q-match) HH {e['spatial_quantile_match'][0]:.1f} HV {e['spatial_quantile_match'][1]:.1f} | "
          f"temporal HH {e['temporal_quantile_match'][0]:.1f} HV {e['temporal_quantile_match'][1]:.1f} | "
          f"effective (empirical null) HH {e['effective_empirical'][0]:.1f} HV {e['effective_empirical'][1]:.1f}")
    for name, r in p["null_fit"].items():
        if r is None:
            continue
        print(f" null on {name}: f={r['f']} quantiles {r['qs']}")
        print(f"   chi2      {[round(v, 1) for v in r['theory']['chi2_quantiles']]}")
        for k in ("theory", "empirical"):
            print(f"   Z({k:9s}) {[round(v, 1) for v in r[k]['Z_quantiles']]}  frac p<a {r[k]['frac_p_below']}")
    print(f"FDR q={p['fdr_q']} [{p['null_kind']}]: BH p* = {p['bh_p_threshold']:.2e}; changed {p['changed_km2']:.0f} km2 "
          f"({100 * p['changed_km2'] / p['domain_km2']:.1f} %) | speckle-null: p* {p['bh_p_threshold_speckle']:.2e}, "
          f"changed {p['changed_km2_speckle']:.0f} km2 ({100 * p['changed_km2_speckle'] / p['domain_km2']:.1f} %)")
    c = p["cusum_compare"]
    print(f"vs CUSUM: stats {c['stats_km2']:.0f}, cusum {c['cusum_km2']:.0f}, both {c['both_km2']:.0f}, "
          f"stats-only {c['stats_only_km2']:.0f}, cusum-only {c['cusum_only_km2']:.0f} km2; agreement "
          f"{c['agreement_changed_unchanged']:.3f}; first==cusum {c['date_first_eq_cusum']:.3f}, "
          f"within 1 {c['date_first_within1']:.3f}, largest==cusum {c['date_largest_eq_cusum']:.3f}")
    print(f" n_changes km2: {c['n_changes_hist_km2']}")
    for k, v in p["false_alarm"].items():
        print(f" stable target {k} ({v['km2']:.0f} km2): flagged stats-FDR {100 * v['stats_fdr']:.2f} %, "
              f"speckle-null FDR {100 * v['stats_speckle_fdr']:.2f} %, theory-null {100 * v['stats_theory_p<p*']:.2f} %, HH-only {100 * v['stats_HH_only_p<p*']:.2f} %, "
              f"CUSUM {100 * v['cusum']:.2f} %")
    if p["flood_wave"]:
        for k, v in p["flood_wave"].items():
            print(f" flood {k}: {v['px']} px, detected stats {v['stats_detected']:.2f} cusum {v['cusum_detected']:.2f};"
                  f" change within 1 date of first water: stats {v['stats_any_change_within1_of_first_water']:.2f}"
                  f" cusum {v['cusum_within1_of_first_water']:.2f}")
            print(f"   first-water hist {v['first_water_hist']}\n   stats first hist {v['stats_first_hist']}\n"
                  f"   cusum hist       {v['cusum_hist']}")
    print(" date        baseline  median  (5-95 %)        thr-width boundary speckle  [km2]")
    comp = p["area_components_5_95_width"]
    for t, d in enumerate(p["dates"]):
        print(f" {d}  {float(p['water_km2_baseline'][t]):8.0f}  {float(p['water_km2_median'][t]):6.0f}  "
              f"({float(p['water_km2_lo'][t]):.0f}-{float(p['water_km2_hi'][t]):.0f})"
              f"   {comp['threshold'][t]:7.0f} {comp['boundary'][t]:7.0f} {comp['speckle'][t]:7.0f}")
    s = p["seasonal_km2"]
    print(f" seasonal water {s['baseline']:.0f}: median {s['median']:.0f} (5-95 %: {s['lo']:.0f}-{s['hi']:.0f}); "
          f"widths {({k: round(v) for k, v in s['components'].items()})}")
    if p["sevan_interval"]:
        si = p["sevan_interval"]
        print(f" water threshold interval from Sevan (+-{si['tol_km2']:.0f} km2): {si['lo']:.3f}-{si['hi']:.3f}")


# =============================== figures =========================================
def _d(s):
    return dt.date.fromisoformat(s)


def figures(p: dict, outdir=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import BoundaryNorm, ListedColormap

    outdir = outdir or ROOT / "docs/eda/stats"
    outdir.mkdir(parents=True, exist_ok=True)
    area, aux = p["area"], p["_aux"]
    w, s, e, n = p["bounds"]
    ext = (w, e, s, n)
    dom = p["valid"].numpy()
    T = len(p["dates"])

    def save(fig, name):
        path = outdir / f"{area}_{name}.png"
        fig.savefig(path, dpi=fig.dpi, bbox_inches="tight")
        plt.close(fig)
        print(f"  wrote {path.relative_to(ROOT)} ({path.stat().st_size / 1024:.0f} KB)")

    # 1. noise vs level
    fig, axs = plt.subplots(1, 2, figsize=(9, 3.6), dpi=90)
    for ax, ch in zip(axs, ("HH", "HV")):
        cv = p["noise_curve"][ch]
        lv = np.array(cv["level"])
        tq, sq = np.array(cv["temporal"]), np.array(cv["spatial"])
        ax.plot(lv, tq[:, 1], "o-", c="#2166ac", label="temporal sd, 25th pct")
        ax.fill_between(lv, tq[:, 0], tq[:, 2], color="#2166ac", alpha=0.15, label="temporal 10-50th pct")
        ax.plot(lv, sq[:, 1], "s--", c="#b2182b", label="spatial 5x5 sd, 25th pct")
        if p["source"] == "preview":
            k = tq[:, 1].mean() / lv.mean()
            ax.plot(lv, k * lv, ":", c="k", label="multiplicative (sd ∝ level)")
        ax.set_xlabel("level (brightness 0..1)" if p["source"] == "preview" else "level (dB)")
        ax.set_ylabel("std (same units)")
        ax.set_title(f"{area} {ch}")
    axs[0].legend(fontsize=7)
    fig.suptitle("Noise vs level: flat temporal sd = additive in brightness (log-like stretch)", fontsize=9)
    save(fig, "noise_vs_level")

    # 2. theoretical vs empirical null (QQ of the joint omnibus statistic on the stable proxy)
    r = p["null_fit"]["forest_proxy"] or p["null_fit"]["all_nofloor"]
    fig, ax = plt.subplots(figsize=(4.6, 4.2), dpi=90)
    qs = np.array(r["qs"])
    chi = np.array(r["theory"]["chi2_quantiles"])
    ax.plot(chi, r["theory"]["Z_quantiles"], "o-", c="#b2182b", label=f"theory ENL {np.round(r['theory']['n'], 1)}")
    ax.plot(chi, r["empirical"]["Z_quantiles"], "s-", c="#2166ac", label=f"empirical ENL {np.round(r['empirical']['n'], 1)}")
    ax.plot([0, chi.max()], [0, chi.max()], "k:", label="perfect fit")
    for qv, cv_ in zip(qs, chi):
        ax.annotate(f"{qv:g}", (cv_, cv_), fontsize=6, xytext=(3, -8), textcoords="offset points")
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlabel(f"chi2_{r['f']} quantile"); ax.set_ylabel("observed quantile of -2 rho ln Q")
    ax.set_title(f"{area}: null on stable proxy pixels", fontsize=9)
    ax.legend(fontsize=7)
    save(fig, "null_fit")

    # 3. first change (FDR) next to CUSUM, 4. number of changes
    cmap = ListedColormap(plt.cm.viridis(np.linspace(0, 1, T - 1)))
    norm = BoundaryNorm(np.arange(0.5, T), cmap.N)
    H, W = dom.shape
    figw = 11 if W >= H else 9
    fig, axs = plt.subplots(1, 2, figsize=(figw, figw / 2 * H / W + 0.8), dpi=70)
    for ax, (title, arr) in zip(axs, ((f"omnibus + BH (q={p['fdr_q']}): first change", p["first_change"].numpy()),
                                      ("existing CUSUM change date", aux["cusum_date"].numpy()))):
        bg = np.where(dom, 0.85, 1.0)
        ax.imshow(np.stack([bg] * 3, -1), extent=ext, interpolation="nearest")
        a = np.ma.masked_where((arr < 0) | ~dom, arr)
        im = ax.imshow(a, cmap=cmap, norm=norm, extent=ext, interpolation="nearest")
        ax.set_title(title, fontsize=9)
    cb = fig.colorbar(im, ax=axs, ticks=range(1, T), shrink=0.8)
    cb.ax.set_yticklabels([d[5:] for d in p["dates"][1:]], fontsize=7)
    save(fig, "first_change_vs_cusum")

    fig, ax = plt.subplots(figsize=(figw / 2 + 1, figw / 2 * H / W + 0.6), dpi=70)
    nc = p["n_changes"].numpy().astype(float)
    nc[~dom] = np.nan
    cm = ListedColormap(["#e0e0e0", "#fdae61", "#d7191c", "#7b3294", "#2c7bb6"])
    im = ax.imshow(np.clip(nc, 0, 4), cmap=cm, vmin=-0.5, vmax=4.5, extent=ext, interpolation="nearest")
    cb = fig.colorbar(im, ax=ax, ticks=range(5), shrink=0.8)
    cb.ax.set_yticklabels(["0", "1", "2", "3", "4+"])
    ax.set_title(f"{area}: number of detected changes per pixel", fontsize=9)
    save(fig, "n_changes")

    # 5. area curves with bands
    fig, ax = plt.subplots(figsize=(7, 3.4), dpi=90)
    dts = [_d(d) for d in p["dates"]]
    ax.fill_between(dts, p["water_km2_lo"].numpy(), p["water_km2_hi"].numpy(), color="#4292c6", alpha=0.3,
                    label="5-95 % (threshold + boundary + speckle)")
    ax.plot(dts, p["water_km2_median"].numpy(), "o-", c="#08519c", label="median")
    ax.plot(dts, p["water_km2_baseline"].numpy(), "x", c="k", label="current pipeline")
    for g0, g1 in GAPS:
        ax.axvspan(_d(g0), _d(g1), color="0.85", label="no data (outage)")
    if area == "sevan":
        ax.axhline(SEVAN_KM2, ls=":", c="r", label="published 1,242 km2")
    ax.set_ylabel("open water km2"); ax.set_title(f"{area}: open water per pass with uncertainty", fontsize=9)
    ax.legend(fontsize=7)
    fig.autofmt_xdate()
    save(fig, "water_area_bands")

    # 6. water confidence (windiest Sevan date = 12 Sep; else last date)
    t = 6 if area == "sevan" else T - 1
    fig, axs = plt.subplots(1, 2, figsize=(figw, figw / 2 * H / W + 0.8), dpi=70)
    axs[0].imshow(np.where(dom, aux["hh_raw"][t].numpy(), 1.0), cmap="gray", vmin=0, vmax=1, extent=ext)
    axs[0].set_title(f"HH {p['dates'][t]}", fontsize=9)
    pc = p["water_conf"][t].float().numpy()
    im = axs[1].imshow(np.ma.masked_where(~dom, pc), cmap="RdBu", vmin=0, vmax=1, extent=ext, interpolation="nearest")
    axs[1].set_title("P(water) = Phi((thr - L)/sd)", fontsize=9)
    fig.colorbar(im, ax=axs[1], shrink=0.8)
    save(fig, "water_confidence")


# =============================== CLI =============================================
SAVE_DROP = ("_aux",)


def save_products(p: dict):
    d = ROOT / "data/processed" / p["area"] / "stats"
    d.mkdir(parents=True, exist_ok=True)
    torch.save({k: v for k, v in p.items() if k not in SAVE_DROP}, d / "products.pt")
    print(f"  wrote {(d / 'products.pt').relative_to(ROOT)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("areas", nargs="*", default=["tonle-sap", "sevan", "ararat"])
    ap.add_argument("--q", type=float, default=FDR_Q)
    ap.add_argument("--boot", type=int, default=16)
    ap.add_argument("--no-fig", action="store_true")
    ap.add_argument("--cpu", action="store_true")
    a = ap.parse_args(argv)
    for area in a.areas:
        p = analyse(area, q=a.q, n_boot=a.boot, dev=torch.device("cpu") if a.cpu else None)
        save_products(p)
        if not a.no_fig:
            figures(p)


if __name__ == "__main__":
    main()
