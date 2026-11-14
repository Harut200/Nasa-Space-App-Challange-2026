"""Probabilistic water / flooded-vegetation detector: Gaussian mixtures (EM) + a hidden Markov model.

Pure PyTorch, no learning from labels. Design and results: docs/eda/gmm.md.

Pipeline (preview data):
  L [T,2,H,W]  (filtered, date-normalised brightness from ops.run_pipeline)
  -> features  z = (logit HH, logit HV, logit HH - pixel's dry-season logit HH)      [T,3,H,W]
  -> GMM with K full-covariance components, fitted by EM on a pooled random subsample
  -> per-date component posteriors -> physical states (open water / flooded veg / land) by a fixed rule
  -> HMM over the ten dates per pixel, emissions = state-specific mixtures of the same K Gaussians
     (semi-continuous / tied-mixture HMM), pi, A, B by Baum-Welch, unequal time step handled by a
     phantom (unobserved) slot in the 24-day August gap, so the double step uses A @ A exactly.
  -> smoothed posteriors gamma, Viterbi path, sequence descriptors.

Entry points: fit_gmm, GMM.posterior, fit_hmm, forward_backward, viterbi, detect.
CLI: python -m scripts.detect.gmm [area ...] [--pooled]
"""
from __future__ import annotations

import json
import math
import sys
import time
from dataclasses import dataclass, field

import torch

LOG2PI = math.log(2 * math.pi)
EPS_LOGIT = 0.01          # logit(x) = log((x+eps)/(1-x+eps)); floor 1/255 -> -4.3, saturation 1 -> +4.6
STATE_NAMES = ["open water", "flooded vegetation", "land"]
STATE_NAMES4 = ["open water", "flooded vegetation", "dark land", "bright land"]


# =============================================================================================
# Gaussian mixture
# =============================================================================================
@dataclass
class GMM:
    weights: torch.Tensor   # [K]
    means: torch.Tensor     # [K,D]
    covs: torch.Tensor      # [K,D,D]
    loglik: float = float("nan")      # mean (weighted) log-likelihood per sample at convergence
    history: list = field(default_factory=list)
    n_iter: int = 0
    converged: bool = False

    @property
    def K(self):
        return self.weights.shape[0]

    @property
    def D(self):
        return self.means.shape[1]

    def n_params(self):
        K, D = self.K, self.D
        return (K - 1) + K * D + K * D * (D + 1) // 2

    def _prec_chol(self):
        """inverse Cholesky factor W_k with Sigma_k^-1 = W_k W_k^T, and log det Sigma_k."""
        Lc = torch.linalg.cholesky(self.covs.cpu().double())
        eye = torch.eye(self.D, dtype=torch.float64).expand_as(Lc)
        Linv = torch.linalg.solve_triangular(Lc, eye, upper=False)
        logdet = 2 * torch.log(torch.diagonal(Lc, dim1=-2, dim2=-1)).sum(-1)
        return Linv.transpose(-1, -2), logdet

    def component_logpdf(self, X: torch.Tensor, chunk: int = 2_000_000) -> torch.Tensor:
        """log N(x | mu_k, Sigma_k) for X [N,D] -> [N,K] on X's device/dtype (GPU friendly)."""
        W, logdet = self._prec_chol()
        dev, dt = X.device, (X.dtype if X.dtype == torch.float64 and X.device.type == "cpu" else torch.float32)
        W, logdet, mu = W.to(dev, dt), logdet.to(dev, dt), self.means.to(dev, dt)
        out = []
        for i in range(0, X.shape[0], chunk):
            x = X[i:i + chunk].to(dt)
            d = x[:, None, :] - mu[None]                        # [n,K,D]
            y = torch.einsum("nkd,kde->nke", d, W)               # Mahalanobis via inverse Cholesky
            out.append(-0.5 * (self.D * LOG2PI + logdet[None] + (y * y).sum(-1)))
        return torch.cat(out, 0)

    def joint_log(self, X):
        c = self.component_logpdf(X)
        return c + torch.log(self.weights).to(c.device, c.dtype)[None]

    def score_samples(self, X):
        return torch.logsumexp(self.joint_log(X), 1)

    def posterior(self, X):
        """responsibilities r_nk = w_k N(x_n|k) / sum_j w_j N(x_n|j), [N,K]."""
        j = self.joint_log(X)
        return torch.softmax(j, 1)

    def bic(self, X, sw=None):
        ll, n = _wsum(self.score_samples(X), sw)
        return -2 * ll + self.n_params() * math.log(n)

    def aic(self, X, sw=None):
        ll, _ = _wsum(self.score_samples(X), sw)
        return -2 * ll + 2 * self.n_params()

    def to_dict(self):
        return {"weights": self.weights.tolist(), "means": self.means.tolist(), "covs": self.covs.tolist(),
                "loglik": self.loglik, "n_iter": self.n_iter, "converged": self.converged}

    @staticmethod
    def from_dict(d):
        return GMM(torch.tensor(d["weights"], dtype=torch.float64), torch.tensor(d["means"], dtype=torch.float64),
                   torch.tensor(d["covs"], dtype=torch.float64), d.get("loglik", float("nan")))


def _wsum(v, sw):
    v = v.cpu().double()
    if sw is None:
        return float(v.sum()), v.numel()
    sw = sw.cpu().double()
    return float((v * sw).sum()), float(sw.sum())


def kmeanspp(X, k, gen, sw=None, lloyd=10):
    """k-means++ seeding (Arthur & Vassilvitskii 2007) + a few weighted Lloyd iterations."""
    N = X.shape[0]
    w = torch.ones(N, dtype=X.dtype) if sw is None else sw.to(X.dtype)
    c = [X[torch.multinomial(w, 1, generator=gen)][0]]
    d2 = ((X - c[0]) ** 2).sum(1)
    for _ in range(1, k):
        p = (d2 * w).clamp_min(0)
        idx = torch.multinomial(p / p.sum(), 1, generator=gen) if p.sum() > 0 else torch.randint(N, (1,), generator=gen)
        c.append(X[idx][0])
        d2 = torch.minimum(d2, ((X - c[-1]) ** 2).sum(1))
    C = torch.stack(c)
    for _ in range(lloyd):
        lab = torch.cdist(X, C).argmin(1)
        for j in range(k):
            m = lab == j
            if m.any():
                C[j] = (X[m] * w[m, None]).sum(0) / w[m].sum()
    return C, torch.cdist(X, C).argmin(1)


def _mstep(X, R, sw, reg):
    """weighted M-step: Nk = sum_n s_n r_nk; mu_k = sum s r x / Nk; Sigma_k = sum s r (x-mu)(x-mu)^T / Nk + reg I."""
    Rw = R * sw[:, None]
    Nk = Rw.sum(0).clamp_min(1e-10)
    w = Nk / Nk.sum()
    mu = (Rw.T @ X) / Nk[:, None]
    d = X[:, None, :] - mu[None]
    cov = torch.einsum("nk,nkd,nke->kde", Rw, d, d) / Nk[:, None, None]
    cov = cov + reg * torch.eye(X.shape[1], dtype=X.dtype)[None]
    return w, mu, cov


def fit_gmm(features: torch.Tensor, k: int, sample_weight=None, n_init: int = 4, max_iter: int = 300,
            tol: float = 1e-6, reg_covar: float = 1e-3, seed: int = 0, init_means=None) -> GMM:
    """EM for a full-covariance Gaussian mixture on X [N,D] (fitted in float64 on the CPU; N ~ 1e5).

    E-step: r_nk = softmax_k(log w_k + log N(x_n|mu_k,Sigma_k))   (log-sum-exp, never exponentiates densities)
    M-step: closed form, weighted by sample_weight; Sigma_k += reg_covar * I keeps it positive definite.
    Stops when the mean log-likelihood improves by < tol. Best of n_init k-means++ restarts (seeds seed..).
    """
    X = features.detach().cpu().double()
    sw = torch.ones(X.shape[0], dtype=torch.float64) if sample_weight is None else sample_weight.cpu().double()
    best = None
    for r in range(n_init):
        gen = torch.Generator().manual_seed(seed + 1000 * r)
        if init_means is not None:
            C = init_means.double()
            lab = torch.cdist(X, C).argmin(1)
        else:
            C, lab = kmeanspp(X, k, gen, sw)
        R = torch.nn.functional.one_hot(lab, k).double()
        w, mu, cov = _mstep(X, R, sw, reg_covar)
        g = GMM(w, mu, cov)
        hist, prev, conv = [], -float("inf"), False
        for it in range(max_iter):
            j = g.component_logpdf(X) + torch.log(g.weights)[None]
            lse = torch.logsumexp(j, 1)
            ll = float((lse * sw).sum() / sw.sum())
            hist.append(ll)
            R = torch.exp(j - lse[:, None])
            g = GMM(*_mstep(X, R, sw, reg_covar))
            if ll - prev < tol:
                conv = True
                break
            prev = ll
        g.loglik, g.history, g.n_iter, g.converged = ll, hist, it + 1, conv
        if best is None or g.loglik > best.loglik:
            best = g
    # canonical order: by mean of feature 0 (HH)
    o = torch.argsort(best.means[:, 0])
    best.weights, best.means, best.covs = best.weights[o], best.means[o], best.covs[o]
    return best


# =============================================================================================
# features and the component -> state rule
# =============================================================================================
def logit(x, eps=EPS_LOGIT):
    return torch.log((x + eps) / (1 - x + eps))


def inv_logit(z, eps=EPS_LOGIT):
    e = torch.exp(z) if torch.is_tensor(z) else math.exp(z)
    return (e * (1 + eps) - eps) / (1 + e)

def features(L: torch.Tensor, valid: torch.Tensor, n_ref: int = 2):
    """[T,2,H,W] brightness in 0..1 -> appearance a [T,2,H,W] and change c [T,2,H,W] features.
    a_t = (logit HH_t, logit HV_t);  c_t = a_t - a_ref, a_ref = mean of a over the first n_ref valid dates
    (the dry-season reference of the current detector). All in logit units."""
    a = logit(L.clamp(0, 1))
    v = valid[:n_ref, None].to(a.dtype)
    ref = (a[:n_ref] * v).sum(0) / v.sum(0).clamp_min(1)
    return a, (a - ref[None]) * valid[:, None]


# Rule (documented in gmm.md). Thresholds apply to component MEANS (brightness 0..1 / logit change), not pixels.
RULE = {"water_hh_max": 0.08, "water_hv_max": 0.25,   # appearance: HH dark (specular) and HV not bright -> water
        "fv_dhh_min": 0.69,                            # change: HH odds at least doubled (ln 2) over dry reference
        "fv_dhv_max": 0.25,                            # ... while HV rose clearly less (double bounce is an HH effect)
        "fv_app_hh_min": 0.25,                         # flooded veg must LOOK bright in HH on that date
        "split_land_hh": 0.5}                          # 4-state variant: land appearance comps below -> "dark land"


def map_components(g_app: GMM, g_chg: GMM, rule=RULE):
    """Returns boolean component sets: app_water [Ka], app_bright [Ka], app_dark_land [Ka], chg_fv [Kc]."""
    hh, hv = inv_logit(g_app.means[:, 0]), inv_logit(g_app.means[:, 1])
    app_water = (hh <= rule["water_hh_max"]) & (hv <= rule["water_hv_max"])
    app_bright = (hh >= rule["fv_app_hh_min"]) & ~app_water
    app_dark_land = ~app_water & (hh < rule["split_land_hh"])
    dhh, dhv = g_chg.means[:, 0], g_chg.means[:, 1]
    chg_fv = (dhh >= rule["fv_dhh_min"]) & (dhv <= dhh - rule["fv_dhv_max"])
    return {"app_water": app_water, "app_bright": app_bright, "app_dark_land": app_dark_land, "chg_fv": chg_fv}


def describe(g: GMM, kind: str, sets):
    out = []
    for k in range(g.K):
        d = {"k": k, "weight": round(float(g.weights[k]), 4),
             "mean_logit": [round(float(v), 3) for v in g.means[k]],
             "sd_logit": [round(float(v), 3) for v in torch.diagonal(g.covs[k]).sqrt()],
             "corr": round(float(g.covs[k, 0, 1] / (g.covs[k, 0, 0] * g.covs[k, 1, 1]).sqrt()), 3)}
        if kind == "app":
            d["mean_HH"] = round(float(inv_logit(g.means[k, 0])), 4)
            d["mean_HV"] = round(float(inv_logit(g.means[k, 1])), 4)
            d["role"] = "open water" if sets["app_water"][k] else ("bright (can be flooded veg)" if sets["app_bright"][k] else "dark land")
        else:
            d["HH_ratio"] = round(math.exp(float(g.means[k, 0])), 3)   # odds ratio vs reference
            d["HV_ratio"] = round(math.exp(float(g.means[k, 1])), 3)
            d["role"] = "flooded-veg change" if sets["chg_fv"][k] else "other change"
        out.append(d)
    return out


def subset_logpdf(logN, w, mask):
    """log of the mixture density restricted to components in mask (weights renormalised): [T,K,N] -> [T,N]."""
    lw = torch.log(w.float().clamp_min(1e-30)).to(logN.device)
    lw = torch.where(mask.to(logN.device), lw - torch.log(w[mask].sum().float()).to(logN.device),
                     torch.full_like(lw, -float("inf")))
    return torch.logsumexp(logN + lw[None, :, None], 1)


# =============================================================================================
# hidden Markov model over the dates of one pixel (vectorised over all pixels)
# =============================================================================================
def expand_slots(dt_steps):
    """dt_steps [T-1] in base steps (1 = 12 days). Returns slot index of each real date in an expanded chain
    where a step of length d becomes d unit steps through d-1 phantom (unobserved) slots: A^d exactly."""
    pos = [0]
    for d in dt_steps:
        pos.append(pos[-1] + int(d))
    return pos, pos[-1] + 1


def _expand(logb, dt_steps):
    T, S, N = logb.shape
    pos, Tx = expand_slots(dt_steps)
    out = torch.zeros(Tx, S, N, device=logb.device, dtype=logb.dtype)  # phantom slot: log b = 0 (no evidence)
    out[pos] = logb
    return out, pos


def forward_backward(logb, log_pi, log_A):
    """log-space forward-backward, vectorised over N sequences. logb [T,S,N]. Returns gamma [T,S,N],
    xi_sum [S,S] (expected transitions summed over t and n) and log P(x) [N]."""
    T, S, N = logb.shape
    la = torch.empty_like(logb)
    la[0] = log_pi[:, None] + logb[0]
    for t in range(1, T):
        la[t] = torch.logsumexp(la[t - 1][:, None, :] + log_A[:, :, None], 0) + logb[t]
    lb = torch.zeros_like(logb)
    for t in range(T - 2, -1, -1):
        lb[t] = torch.logsumexp(log_A[:, :, None] + (logb[t + 1] + lb[t + 1])[None], 1)
    logZ = torch.logsumexp(la[-1], 0)
    gamma = torch.exp(la + lb - logZ[None, None])
    xi = torch.zeros(S, S, device=logb.device, dtype=logb.dtype)
    for t in range(T - 1):
        lx = la[t][:, None, :] + log_A[:, :, None] + (logb[t + 1] + lb[t + 1])[None] - logZ[None, None]
        xi += torch.exp(lx).sum(-1)
    return gamma, xi, logZ


def viterbi(logb, log_pi, log_A):
    """most likely state path per sequence, [T,N] long."""
    T, S, N = logb.shape
    d = log_pi[:, None] + logb[0]
    bp = []
    for t in range(1, T):
        sc = d[:, None, :] + log_A[:, :, None]          # [from,to,N]
        m, a = sc.max(0)
        bp.append(a)
        d = m + logb[t]
    path = torch.empty(T, N, dtype=torch.long, device=logb.device)
    path[-1] = d.argmax(0)
    for t in range(T - 2, -1, -1):
        path[t] = bp[t].gather(0, path[t + 1][None])[0]
    return path


@dataclass
class HMM:
    pi: torch.Tensor        # [S]
    A: torch.Tensor         # [S,S] per 12-day step
    B: torch.Tensor         # [S,K] state -> component weights
    history: list = field(default_factory=list)

    def logb(self, logN):
        """logN [T,K,N] component log densities -> state log emissions [T,S,N]."""
        lB = torch.log(self.B.clamp_min(1e-30)).to(logN.device, logN.dtype)
        return torch.logsumexp(lB[None, :, :, None] + logN[:, None], 2)

    def to_dict(self):
        return {"pi": self.pi.tolist(), "A": self.A.tolist(), "B": self.B.tolist(), "loglik_history": self.history}


def fit_hmm(log_emission, dt_steps, prior=None, n_iter=30, tol=1e-5, init=None, emission_counts=None):
    """Baum-Welch for pi and A on log_emission [T,S,N] (missing dates: log b = 0 there).

    dt_steps [T-1]: steps in base units; a 2-step gap goes through a phantom slot, so the M-step is exact EM.
    prior: optional dict with Dirichlet pseudo-counts "A" [S,S] and "pi" [S] (added to expected counts: MAP-EM).
    If emission_counts is a callable(gamma_x)->None it is invoked each iteration (used for B updates).
    Returns HMM (B left None here; see fit_hmm_tied for the semi-continuous version)."""
    T, S, N = log_emission.shape
    dev = log_emission.device
    lbx, pos = _expand(log_emission, dt_steps)
    A = (torch.full((S, S), 0.1 / max(S - 1, 1)) + torch.eye(S) * (0.9 - 0.1 / max(S - 1, 1))) if init is None else init[1]
    pi = torch.full((S,), 1.0 / S) if init is None else init[0]
    hist, prev = [], -float("inf")
    for it in range(n_iter):
        g, xi, logZ = forward_backward(lbx, torch.log(pi).to(dev), torch.log(A).to(dev))
        ll = float(logZ.mean())
        hist.append(ll)
        cA = xi.cpu().double()
        cpi = g[0].sum(-1).cpu().double()
        if prior is not None:
            cA = cA + prior.get("A", 0)
            cpi = cpi + prior.get("pi", 0)
        A = (cA / cA.sum(1, keepdim=True)).float()
        pi = (cpi / cpi.sum()).float()
        if ll - prev < tol * abs(ll):
            break
        prev = ll
    return HMM(pi, A, None, hist)



def transition_prior(S, n_seq, strength=0.01):
    """Physically motivated Dirichlet pseudo-counts for A (rows = from, cols = to), each row totalling
    strength * n_seq: water persists; land -> flooded veg -> water favoured over the reverse in a rising flood."""
    if S == 3:          # water, fv, land
        tmpl = torch.tensor([[0.94, 0.02, 0.04],
                             [0.20, 0.70, 0.10],
                             [0.03, 0.05, 0.92]], dtype=torch.float64)
    else:               # water, fv, dark land, bright land
        tmpl = torch.tensor([[0.94, 0.02, 0.02, 0.02],
                             [0.20, 0.70, 0.05, 0.05],
                             [0.03, 0.04, 0.88, 0.05],
                             [0.02, 0.06, 0.05, 0.87]], dtype=torch.float64)
    return {"A": tmpl * strength * n_seq, "pi": torch.zeros(S, dtype=torch.float64)}


# =============================================================================================
# detector
# =============================================================================================
DEFAULTS = {"k_app": 6, "k_chg": 6, "n_fit": 100_000, "n_init": 3, "n_states": 3, "hmm_fit": 200_000,
            "prior_strength": 0.0, "leak": 0.05, "p_thr": 0.5, "min_blob_px": 4, "seed": 0}


def dt_from_dates(dates):
    import datetime as _dt
    d = [_dt.date.fromisoformat(s) for s in dates]
    return [max(1, round((b - a).days / 12)) for a, b in zip(d[:-1], d[1:])]


def _flat(F, valid):
    T, D, H, W = F.shape
    return F.permute(0, 2, 3, 1).reshape(T, H * W, D), valid.reshape(T, H * W)


def sample_features(F, valid, n, gen):
    Xf, vf = _flat(F, valid)
    X = Xf[vf]
    idx = torch.randperm(X.shape[0], generator=gen)[:n] if X.shape[0] > n else torch.arange(X.shape[0])
    return X[idx.to(X.device)].cpu().double()


def state_log_emissions(lNa, lNc, g_app, g_chg, sets, n_states=3, leak=0.05, log_ref_land=None):
    """Per-state log emission [T,S,N] from the two mixtures (appearance a, change c; naive-Bayes product):
       water : p(a | water comps)          * p(c)                    (change is uninformative for water)
       fv    : p(a | bright comps)         * p(c | fv-change comps)
       land  : p(a | non-water comps)      * p(c | other-change comps)   (4 states: dark / bright land split)
    each mixed with a leak: b_s = (1-leak) b_s + leak p(a) p(c), so no state has zero density anywhere.
    log_ref_land [N]: log P(not water | the pixel's dry reference appearance); multiplies the fv emission
    (double bounce needs vegetation standing on land; a brightened lake is wind, not flooded forest)."""
    wa, wc = g_app.weights, g_chg.weights
    pa_all = subset_logpdf(lNa, wa, torch.ones_like(sets["app_water"]))
    pc_all = subset_logpdf(lNc, wc, torch.ones_like(sets["chg_fv"]))
    nonfv = ~sets["chg_fv"]
    rows = [subset_logpdf(lNa, wa, sets["app_water"]) + pc_all]
    rows.append(subset_logpdf(lNa, wa, sets["app_bright"]) + subset_logpdf(lNc, wc, sets["chg_fv"])
                if sets["chg_fv"].any() and sets["app_bright"].any() else torch.full_like(pa_all, -float("inf")))
    if log_ref_land is not None:
        rows[1] = rows[1] + log_ref_land[None]
    if n_states == 3:
        rows.append(subset_logpdf(lNa, wa, ~sets["app_water"]) + subset_logpdf(lNc, wc, nonfv))
    else:
        dark = sets["app_dark_land"]
        bright = ~sets["app_water"] & ~dark
        rows.append(subset_logpdf(lNa, wa, dark) + subset_logpdf(lNc, wc, nonfv))
        rows.append(subset_logpdf(lNa, wa, bright) + subset_logpdf(lNc, wc, nonfv))
    if not sets["app_water"].any():
        rows[0] = torch.full_like(pa_all, -float("inf"))
    lb = torch.stack(rows, 1)
    bg = (pa_all + pc_all)[:, None]
    return torch.logaddexp(math.log(1 - leak) + lb, math.log(leak) + bg), pa_all + pc_all


def state_priors(g_app, g_chg, sets, n_states=3):
    """Per-date prior P(s) implied by the mixture weights (for the per-date posterior without time)."""
    wa, wc = g_app.weights.double(), g_chg.weights.double()
    pw = float(wa[sets["app_water"]].sum())
    pfv = float(wa[sets["app_bright"]].sum() * wc[sets["chg_fv"]].sum())
    rest = 1 - pw - pfv
    if n_states == 3:
        return torch.tensor([pw, pfv, rest])
    pd = float(wa[sets["app_dark_land"]].sum()) / max(1 - pw, 1e-9)
    return torch.tensor([pw, pfv, rest * pd, rest * (1 - pd)])


def fit_models(L, valid, kn=None):
    """Fit the appearance and change mixtures on a random subsample of valid pixels pooled over dates."""
    kn = {**DEFAULTS, **(kn or {})}
    a, c = features(L, valid)
    gen = torch.Generator().manual_seed(kn["seed"])
    Xa = sample_features(a, valid, kn["n_fit"], gen)
    Xc = sample_features(c, valid, kn["n_fit"], gen)
    return (fit_gmm(Xa, kn["k_app"], n_init=kn["n_init"], seed=kn["seed"]),
            fit_gmm(Xc, kn["k_chg"], n_init=kn["n_init"], seed=kn["seed"]))


def _empty_cache():
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()


def detect(L, valid, kn=None, dates=None, models=None, dev=None, verbose=False, water_ok=None,
           chunk=250_000) -> dict:
    """Main entry point. L [T,2,H,W] from ops.run_pipeline (preview brightness 0..1), valid [T,H,W].
    kn: overrides of DEFAULTS (and of RULE via kn["rule"]). dates: ISO strings (the August gap -> 2-step).
    models: (g_app, g_chg) pre-fitted mixtures (pooled / transfer) instead of fitting here.
    water_ok [H,W] bool (optional terrain mask): where it is False the open-water emission is multiplied
    by 1e-6, i.e. a prior, not a post-hoc cut (the HMM then explains those pixels with the other states).
    Memory: emissions, forward-backward and Viterbi run in pixel chunks of `chunk` (float32).
    Returns HMM-smoothed p_water / p_fv [T,H,W] (float16), per-date mixture posteriors, hard masks,
    Viterbi path (uint8, 255 = invalid), sequence descriptors, the fitted models."""
    from scripts.detect import ops
    kn = {**DEFAULTS, **(kn or {})}
    rule = {**RULE, **kn.get("rule", {})}
    dev = dev or L.device
    t0 = time.time()
    T, _, H, W = L.shape
    N = H * W
    S = kn["n_states"]
    valid = valid.to(dev)
    if models is None:
        models = fit_models(L.to(dev).float(), valid, kn)
    g_app, g_chg = models
    t1 = time.time()
    sets = map_components(g_app, g_chg, rule)
    lp = torch.log(state_priors(g_app, g_chg, sets, S).clamp_min(1e-12)).to(dev)
    vf = valid.reshape(T, N)
    wok = None if water_ok is None else water_ok.to(dev).reshape(N)

    def emissions(idx):
        """log b [T,S,n] for flat pixel indices idx (float32)."""
        x = L.reshape(T, 2, N)[:, :, idx].to(dev).float()                      # [T,2,n]
        v = vf[:, idx]
        a, c = features(x[..., None], v[..., None])
        a, c = a[..., 0], c[..., 0]                                              # [T,2,n]
        n = idx.numel()
        lNa = g_app.component_logpdf(a.permute(0, 2, 1).reshape(-1, 2)).reshape(T, n, -1).permute(0, 2, 1)
        lNc = g_chg.component_logpdf(c.permute(0, 2, 1).reshape(-1, 2)).reshape(T, n, -1).permute(0, 2, 1)
        v2 = v[:2, None].float()
        aref = ((a[:2] * v2).sum(0) / v2.sum(0).clamp_min(1)).T
        if sets["app_water"].any():
            pref = g_app.posterior(aref)[:, sets["app_water"].to(dev)].sum(1)
        else:
            pref = torch.zeros(n, device=dev)
        lb, _ = state_log_emissions(lNa, lNc, g_app, g_chg, sets, S, kn["leak"], torch.log((1 - pref).clamp_min(1e-6)))
        if wok is not None:
            lb[:, 0] = lb[:, 0] + torch.where(wok[idx], 0.0, math.log(1e-6))[None]
        return lb * v[:, None]                                                   # invalid date: no evidence

    # HMM parameters: Baum-Welch on a random pixel subsample
    dts = dt_from_dates(dates) if dates is not None else [1] * (T - 1)
    gen = torch.Generator().manual_seed(kn["seed"])
    pix = torch.nonzero(vf.any(0).cpu()).squeeze(1)
    sub = pix[torch.randperm(pix.numel(), generator=gen)[:kn["hmm_fit"]]].to(dev)
    prior = transition_prior(S, sub.numel(), kn["prior_strength"]) if kn["prior_strength"] > 0 else None
    hmm = fit_hmm(emissions(sub), dts, prior=prior)
    t2 = time.time()
    lpi, lA = torch.log(hmm.pi.clamp_min(1e-30)).to(dev), torch.log(hmm.A.clamp_min(1e-30)).to(dev)
    gamma = torch.zeros(T, S, N, dtype=torch.float16, device=dev)
    P_date = torch.zeros(T, S, N, dtype=torch.float16, device=dev)
    vit = torch.full((T, N), 255, dtype=torch.uint8, device=dev)
    ll_sum = 0.0
    for i in range(0, N, chunk):
        idx = torch.arange(i, min(i + chunk, N), device=dev)
        lb = emissions(idx)
        P_date[:, :, idx] = (torch.softmax(lb + lp[None, :, None], 1) * vf[:, None, idx]).half()
        lbx, pos = _expand(lb, dts)
        g, _, logZ = forward_backward(lbx, lpi, lA)
        gamma[:, :, idx] = (g[pos] * vf[:, None, idx]).half()
        vit[:, idx] = viterbi(lbx, lpi, lA)[pos].to(torch.uint8)
        ll_sum += float(logZ.sum())
        del lb, lbx, g
    _empty_cache()
    t3 = time.time()
    shape = (T, H, W)
    gamma = gamma.reshape(T, S, H, W)
    P_date = P_date.reshape(T, S, H, W)
    vit = vit.reshape(shape)
    vit[~valid] = 255
    p_water, p_fv = gamma[:, 0], gamma[:, 1]
    pw_d, pf_d = P_date[:, 0], P_date[:, 1]
    mb, thr = kn["min_blob_px"], kn["p_thr"]
    water = ops.clean_mask((p_water >= thr) & valid, 0, mb)
    fv = ops.clean_mask((p_fv >= thr) & valid & ~water, 0, mb)
    water_d = ops.clean_mask((pw_d >= thr) & valid, 0, mb)
    fv_d = ops.clean_mask((pf_d >= thr) & valid & ~water_d, 0, mb)
    if wok is not None:   # per-date mixture: apply the same terrain prior as a post-hoc cut
        water_d = water_d & water_ok.to(dev)[None]
    tidx = torch.arange(T, device=dev, dtype=torch.int16)[:, None, None].expand(shape)
    big = torch.full(shape, 99, device=dev, dtype=torch.int16)
    first_water = torch.where(vit == 0, tidx, big).min(0).values
    first_fv = torch.where(vit == 1, tidx, big).min(0).values
    first_land = torch.where((vit >= 2) & (vit < 255), tidx, big).min(0).values
    path_dfw = (first_land < first_fv) & (first_fv < first_water) & (first_water < 99)
    first_water[first_water == 99] = -1
    first_fv[first_fv == 99] = -1
    n_trans = ((vit[1:] != vit[:-1]) & valid[1:] & valid[:-1]).sum(0).to(torch.uint8)
    del tidx, big, first_land
    _empty_cache()
    if verbose:
        print(f"  fit mixtures {t1 - t0:.1f}s, Baum-Welch {t2 - t1:.1f}s, smoothing+Viterbi {t3 - t2:.1f}s", flush=True)
    return {"gmm_app": g_app, "gmm_chg": g_chg, "sets": sets, "hmm": hmm,
            "state_names": STATE_NAMES if S == 3 else STATE_NAMES4,
            "p_water": p_water, "p_fv": p_fv, "water": water, "flooded_veg": fv,
            "p_water_date": pw_d, "p_fv_date": pf_d, "water_date": water_d, "fv_date": fv_d,
            "viterbi": vit, "first_water": first_water.to(torch.int8), "first_fv": first_fv.to(torch.int8),
            "n_transitions": n_trans, "path_dry_fv_water": path_dfw, "dt_steps": dts,
            "loglik_per_pixel": ll_sum / N, "gamma_sum_check": float((gamma.float().sum(1)[valid] - 1).abs().max()),
            "times_s": {"fit_mixtures": round(t1 - t0, 2), "baum_welch": round(t2 - t1, 2), "smooth_viterbi": round(t3 - t2, 2)},
            "valid": valid}


# =============================================================================================
# evaluation (JRC Global Surface Water is used ONLY here, never to fit) + products + CLI
# =============================================================================================
AREAS = ["tonle-sap", "sevan", "ararat"]
SEVAN_KM2 = 1242.0
WINDY = {"sevan": ["2026-08-31", "2026-09-12"]}


def _r(x, nd=1):
    return round(float(x), nd)


def _km2(m, km2):
    return _r(float(m.sum()) * km2)


def _ranks(x):
    """average ranks (ties share the mean rank) -> Spearman = Pearson of ranks."""
    x = x.cpu().double()
    o = torch.argsort(x)
    xs = x[o]
    _, inv, cnt = torch.unique_consecutive(xs, return_inverse=True, return_counts=True)
    ends = cnt.cumsum(0).double()
    avg = ends - (cnt.double() - 1) / 2
    r = torch.empty_like(x)
    r[o] = avg[inv]
    return r


def _pearson(a, b):
    a, b = a.cpu().double(), b.cpu().double()
    a, b = a - a.mean(), b - b.mean()
    return float((a * b).sum() / (a.norm() * b.norm()).clamp_min(1e-12))


def spearman(a, b):
    return _pearson(_ranks(a), _ranks(b))


def load_area_inputs(area, dev):
    """preview stack + the current detector WITH the terrain mask (when it is cached for this grid)."""
    from scripts.detect import ops
    from scripts.detect.stack import load_preview_stack
    from scripts.detect.terrain import cached_water_possible
    st = load_preview_stack(area)
    wok = cached_water_possible(area, tuple(st.x.shape[-2:]))
    out = ops.run_pipeline(st.x, st.valid, "preview", dev=dev, water_ok=wok)
    out["water_ok"] = None if wok is None else wok.to(dev)
    out["water_no_terrain"] = ops.water_masks(out["L"], out["valid"], out["knobs"])
    del out["change_first"], out["change_consec"]
    st.x = None
    return st, out


def jrc(area, st, dev):
    from scripts.detect import validate
    H, W = st.valid.shape[-2:]
    ref, _ = validate.reference_on_grid(area, st.bounds, H, W)
    return {k: v.to(dev) for k, v in ref.items()}


def evaluate(area, st, base, res, ref, dev, extra=None):
    """Side-by-side metrics: current detector (ops, with terrain mask) vs per-date mixture vs HMM (+ extra methods)."""
    from scripts.detect import ops
    km2, dates = st.km2_per_px, st.dates
    valid = base["valid"]
    dom = valid.all(0) & (ref["valid_frac"] >= 0.5)
    ext = ref["ext_frac"] >= 0.5
    ref_any = (ref["ext_frac"] > 0) & dom
    near3 = ops.dilate(ref_any[None], 7)[0]
    near1 = ops.dilate(ref_any[None], 3)[0]
    methods = {"current": (base["water"], base["flooded_veg"]),
               "gmm_per_date": (res["water_date"], res["fv_date"]),
               "hmm": (res["water"], res["flooded_veg"])}
    methods.update(extra or {})
    T = len(dates)
    R = {"dates": dates, "km2_per_px": km2, "domain_km2": _km2(dom, km2)}
    for name, (w, f) in methods.items():
        w, f = w & dom, f & dom
        m = {"water_km2": [_km2(w[t], km2) for t in range(T)],
             "flooded_veg_km2": [_km2(f[t], km2) for t in range(T)],
             "false_water_far_from_jrc_km2": [_km2(w[t] & ~ext & ~near3, km2) for t in range(T)],
             "late_false_water_far_from_jrc_km2": _km2(w[-3:].any(0) & ~ext & ~near3, km2),
             "water_share_inside_jrc_max_extent": [_r(float((w[t] & ext).sum()) / max(float(w[t].sum()), 1), 4) for t in range(T)],
             "fv_any_date_km2": _km2(f.any(0), km2),
             "fv_any_share_within_1px_of_jrc_water": _r(float((f.any(0) & near1).sum()) / max(float(f.any(0).sum()), 1), 4)}
        if area == "sevan":
            lab = ops.label_components(w)
            big = []
            for t in range(T):
                ids, cnt = torch.unique(lab[t][lab[t] > 0], return_counts=True)
                big.append(_r(float(cnt.max()) * km2) if cnt.numel() else 0.0)
            m["largest_body_km2"] = big
            m["largest_body_error_pct_vs_1242"] = [_r(100 * (b - SEVAN_KM2) / SEVAN_KM2) for b in big]
        nv = valid.sum(0).clamp_min(1)
        perm = ((w & valid).sum(0).float() / nv >= 0.9) & dom
        if name == "current":
            perm = (base["classes"] == 1) & dom
        m["permanent_water_vs_jrc"] = {}
        from scripts.detect import validate
        for key, refm in [("occ_ge75", ref["occ_ge75_frac"] >= 0.5), ("occ_ge90", ref["occ_ge90_frac"] >= 0.5),
                          ("seasonality12", ref["seas12_frac"] >= 0.5)]:
            c = validate.confusion(perm.cpu(), refm.cpu(), dom.cpu(), km2)
            m["permanent_water_vs_jrc"][key] = {k: c[k] for k in ("iou", "precision", "recall", "ours_km2", "ref_km2")}
        # rank consistency in the ambiguous floodplain (inside max extent, not >=90 % occurrence)
        fp = dom & ext & (ref["occ_ge90_frac"] < 0.5)
        if float(fp.sum()) > 100:
            nwd = (w & valid).sum(0).float()
            m["floodplain_spearman_occurrence_vs_n_water_dates"] = _r(spearman(ref["occ_mean"][fp], nwd[fp]), 3)
            if name == "hmm":
                m["floodplain_spearman_occurrence_vs_mean_p_water"] = _r(spearman(ref["occ_mean"][fp], res["p_water"].mean(0)[fp]), 3)
            if name == "gmm_per_date":
                m["floodplain_spearman_occurrence_vs_mean_p_water"] = _r(spearman(ref["occ_mean"][fp], res["p_water_date"].mean(0)[fp]), 3)
        R[name] = m
    # flood-wave timing: HMM first water date vs the current CUSUM change date (pixels that became water)
    fw = res["first_water"].long()
    cd = base["change_date"].long()
    both = (fw > 0) & (cd >= 0) & dom
    if float(both.sum()) > 50:
        dd = (fw - cd)[both].float()
        R["timing_vs_change_date"] = {"n_px": int(both.sum()), "km2": _km2(both, km2),
                                      "exact_share": _r(float((dd == 0).float().mean()), 3),
                                      "within_1_date_share": _r(float((dd.abs() <= 1).float().mean()), 3),
                                      "mean_diff_dates": _r(float(dd.mean()), 2), "pearson": _r(_pearson(fw[both], cd[both]), 3)}
    R["hmm_first_water_km2_by_date"] = {dates[t]: _km2((fw == t) & dom, km2) for t in range(1, T)}
    pdfw = res["path_dry_fv_water"] & dom
    R["path_dry_fv_water"] = {"km2": _km2(pdfw, km2),
                              "share_inside_jrc_max_extent": _r(float((pdfw & ext).sum()) / max(float(pdfw.sum()), 1), 3)}
    if float(pdfw.sum()) > 0:
        yy, xx = torch.nonzero(pdfw, as_tuple=True)
        w_, s_, e_, n_ = st.bounds
        H, W = pdfw.shape
        R["path_dry_fv_water"]["centroid_lonlat"] = [_r(w_ + (float(xx.float().mean()) + .5) / W * (e_ - w_), 3),
                                                     _r(n_ - (float(yy.float().mean()) + .5) / H * (n_ - s_), 3)]
    # flooded-vegetation sensitivity to the probability threshold (HMM and per-date) vs the old rule
    sens = {}
    for nm, p in [("hmm", res["p_fv"]), ("gmm_per_date", res["p_fv_date"])]:
        sens[nm] = {str(th): _r(max(float(((p >= th) & dom[None]).sum((1, 2)).max()), 0) * km2) for th in (0.3, 0.5, 0.7, 0.9)}
    old = {}
    from scripts.detect import ops as _ops
    for rise in (0.10, 0.12, 0.15, 0.20):
        kn = dict(base["knobs"]); kn["fv_hh_rise"] = rise
        f = _ops.flooded_veg_masks(base["L"], valid, base["water"], base["prior_water"], kn) & dom
        old[str(rise)] = _r(float(f.sum((1, 2)).max()) * km2)
    sens["current_rule_hh_rise"] = old
    R["fv_peak_km2_sensitivity"] = sens
    return R


def reliability_pairs(res, ref, valid, dev):
    """anchor pixels: permanent water (>= 99 % of the pixel has occurrence >= 90 %, 2-px interior) -> 1,
    never water (no water ever in 1984-2021, > 3 px from any) -> 0. Shoreline band excluded by construction."""
    from scripts.detect import ops
    dom = valid.all(0) & (ref["valid_frac"] >= 0.5)
    perm = ops.erode((ref["occ_ge90_frac"] >= 0.99)[None], 5)[0] & dom
    never = ~ops.dilate((ref["ext_frac"] > 0)[None], 7)[0] & dom & (ref["occ_any_frac"] == 0)
    m = perm | never
    out = {}
    idx = torch.nonzero(m.flatten()).squeeze(1)
    g = torch.Generator().manual_seed(0)
    idx = idx[torch.randperm(idx.numel(), generator=g)[:100_000].to(idx.device)]
    y = perm.flatten()[idx]
    for nm, p in [("hmm", res["p_water"]), ("gmm_per_date", res["p_water_date"])]:
        pp = p.reshape(p.shape[0], -1)[:, idx].float()
        out[nm] = (pp.flatten().cpu(), y[None].expand(p.shape[0], -1).flatten().cpu())
    return out


def reliability_table(pairs, bins=10):
    p, y = pairs
    edges = torch.linspace(0, 1, bins + 1)
    idx = torch.bucketize(p, edges[1:-1])
    rows = []
    for b in range(bins):
        s = idx == b
        n = int(s.sum())
        rows.append({"bin": [round(float(edges[b]), 2), round(float(edges[b + 1]), 2)], "n": n,
                     "mean_p": _r(p[s].mean(), 4) if n else None, "observed_water": _r(y[s].float().mean(), 4) if n else None})
    brier = float(((p - y.float()) ** 2).mean())
    return {"bins": rows, "brier": round(brier, 5), "n": int(p.numel()),
            "accuracy_at_0.5": round(float(((p >= 0.5) == y).float().mean()), 5)}


def bic_curves(L, valid, ks=range(2, 9), n=50_000, seed=0):
    a, c = features(L, valid)
    gen = torch.Generator().manual_seed(seed)
    Xa, Xc = sample_features(a, valid, n, gen), sample_features(c, valid, n, gen)
    out = {"app": {}, "chg": {}}
    for k in ks:
        for nm, X in (("app", Xa), ("chg", Xc)):
            g = fit_gmm(X, k, n_init=2, seed=seed)
            out[nm][k] = {"bic_per_sample": round(g.bic(X) / X.shape[0], 5), "aic_per_sample": round(g.aic(X) / X.shape[0], 5),
                          "loglik": round(g.loglik, 5), "n_iter": g.n_iter}
    return out, (Xa, Xc)


def seed_stability(X, k, seeds=(0, 1, 2)):
    """refit with different seeds; match components greedily by mean distance; report spread."""
    gs = [fit_gmm(X, k, n_init=1, seed=s * 7919 + 1) for s in seeds]
    ref = gs[0]
    dev_means, lls = [], [g.loglik for g in gs]
    for g in gs[1:]:
        D = torch.cdist(ref.means, g.means)
        used, mx = set(), 0.0
        for i in torch.argsort(D.min(1).values).tolist():
            j = min((j for j in range(g.K) if j not in used), key=lambda j: float(D[i, j]))
            used.add(j)
            mx = max(mx, float(D[i, j]))
        dev_means.append(round(mx, 3))
    return {"loglik_per_seed": [round(v, 5) for v in lls], "max_matched_mean_shift_logit": dev_means}


def save_products(area, st, res, path):
    T = len(st.dates)
    km2 = st.km2_per_px
    prod = {"dates": st.dates, "bounds": st.bounds, "km2_per_px": km2,
            "p_water": res["p_water"].half().cpu(), "p_fv": res["p_fv"].half().cpu(),
            "p_water_date": res["p_water_date"].half().cpu(), "p_fv_date": res["p_fv_date"].half().cpu(),
            "water": res["water"].cpu(), "flooded_veg": res["flooded_veg"].cpu(),
            "viterbi": res["viterbi"].cpu(), "state_names": res["state_names"],
            "first_water": res["first_water"].cpu(), "first_fv": res["first_fv"].cpu(),
            "n_transitions": res["n_transitions"].cpu(), "path_dry_fv_water": res["path_dry_fv_water"].cpu(),
            "water_km2": [float(res["water"][t].sum()) * km2 for t in range(T)],
            "flooded_veg_km2": [float(res["flooded_veg"][t].sum()) * km2 for t in range(T)],
            "trans_matrix": res["hmm"].A, "pi": res["hmm"].pi, "dt_steps": res["dt_steps"],
            "gmm_app": res["gmm_app"].to_dict(), "gmm_chg": res["gmm_chg"].to_dict(),
            "component_sets": {k: v.tolist() for k, v in res["sets"].items()}, "rule": RULE}
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(prod, path)


def _A(m):
    return [[round(float(v), 4) for v in r] for r in m]


def run_area(area, dev, docs, figures=True, rel_store=None, pooled_samples=None):
    """Everything for one area, sequentially, freeing memory as it goes. Returns the results dict."""
    from scripts.detect import ops
    from scripts.detect.stack import ROOT
    print(f"== {area}", flush=True)
    t0 = time.time()
    st, base = load_area_inputs(area, dev)
    t_in = time.time() - t0
    L, valid, wok = base["L"], base["valid"], base["water_ok"]
    km2 = st.km2_per_px
    ref = jrc(area, st, dev)
    dom = valid.all(0) & (ref["valid_frac"] >= 0.5)
    res = detect(L, valid, dates=st.dates, dev=dev, verbose=True, water_ok=wok)
    t_det = time.time() - t0 - t_in
    models = (res["gmm_app"], res["gmm_chg"])
    A = {"terrain_mask_available": wok is not None,
         "times_s": {"load_and_ops_pipeline": round(t_in, 1), "detect_total": round(t_det, 1), **res["times_s"]}}
    A["components_appearance"] = describe(res["gmm_app"], "app", res["sets"])
    A["components_change"] = describe(res["gmm_chg"], "chg", res["sets"])
    A["hmm"] = {"pi": [round(float(v), 4) for v in res["hmm"].pi], "A_per_12_days": _A(res["hmm"].A),
                "A_24_days": _A(res["hmm"].A @ res["hmm"].A), "baum_welch_loglik": [round(v, 4) for v in res["hmm"].history],
                "state_names": res["state_names"], "dt_steps": res["dt_steps"],
                "loglik_per_pixel": round(res["loglik_per_pixel"], 4), "max_abs_gamma_sum_minus_1": res["gamma_sum_check"]}
    # variants: no terrain prior, Dirichlet transition prior, land split in two
    extra = {"current_no_terrain": (base["water_no_terrain"], base["flooded_veg"])}
    for nm, kn, wk in [("hmm_no_terrain", {}, None), ("hmm_with_prior", {"prior_strength": 0.01}, wok),
                       ("hmm_4_states", {"n_states": 4}, wok)]:
        v = detect(L, valid, kn=kn, dates=st.dates, dev=dev, models=models, water_ok=wk)
        A[nm] = {"A_per_12_days": _A(v["hmm"].A), "loglik_per_pixel": round(v["loglik_per_pixel"], 4),
                 "water_km2": [_km2(v["water"][t] & dom, km2) for t in range(len(st.dates))],
                 "flooded_veg_km2": [_km2(v["flooded_veg"][t] & dom, km2) for t in range(len(st.dates))],
                 "path_dry_fv_water_km2": _km2(v["path_dry_fv_water"] & dom, km2)}
        if nm == "hmm_no_terrain":
            extra["hmm_no_terrain"] = (v["water"], v["flooded_veg"])
            extra["gmm_per_date_no_terrain"] = (v["water_date"], v["fv_date"])
        del v
        _empty_cache()
    A["metrics"] = evaluate(area, st, base, res, ref, dev, extra)
    # BIC curves and seed stability (CPU, subsample)
    bic, (Xa, Xc) = bic_curves(L, valid, n=30_000)
    A["bic"] = bic
    A["seed_stability"] = {"app": seed_stability(Xa, DEFAULTS["k_app"]), "chg": seed_stability(Xc, DEFAULTS["k_chg"])}
    if pooled_samples is not None:
        pooled_samples[area] = (Xa, Xc)
    # equivalent per-date decision boundary on HH (no change since the reference) vs hysteresis / Otsu
    hh = torch.linspace(0.002, 0.4, 400, dtype=torch.float64)
    bnd = {}
    for hvr in (0.5, 1.0):
        a_ = torch.stack([logit(hh), logit((hh * hvr).clamp(0, 1))], 1)
        lNa = res["gmm_app"].component_logpdf(a_).T[None]
        lNc = res["gmm_chg"].component_logpdf(torch.zeros_like(a_)).T[None]
        lb, _ = state_log_emissions(lNa.float(), lNc.float(), res["gmm_app"], res["gmm_chg"], res["sets"], 3, DEFAULTS["leak"])
        pw = torch.softmax(lb + torch.log(state_priors(res["gmm_app"], res["gmm_chg"], res["sets"]).clamp_min(1e-12)).float()[None, :, None], 1)[0, 0]
        cross = torch.nonzero(pw < 0.5)
        bnd[f"HV=HHx{hvr}"] = round(float(hh[cross[0, 0]]), 4) if cross.numel() and res["sets"]["app_water"].any() else None
    A["decision_boundary_HH"] = {"p_water_0.5_crossing": bnd, "hysteresis_strong": 0.06, "hysteresis_weak": 0.12,
                                 "otsu_HH_all_dates": round(ops.otsu(L[:, 0][valid][::5]), 4)}
    save_products(area, st, res, ROOT / f"data/processed/{area}/gmm/products.pt")
    if rel_store is not None:
        for nm, pr in reliability_pairs(res, ref, valid, dev).items():
            rel_store.setdefault(nm, []).append(pr)
    if figures:
        area_figures(area, st, base, res, docs)
    print(f"  water km2 hmm  {A['metrics']['hmm']['water_km2']}", flush=True)
    print(f"  water km2 cur  {A['metrics']['current']['water_km2']}", flush=True)
    print(f"  done {area} in {time.time() - t0:.0f}s", flush=True)
    del st, base, res, ref, L, valid
    _empty_cache()
    return A, models


def run_transfer(area, models, name, dev):
    """detect with a mixture pair fitted elsewhere; returns the headline metrics."""
    st, base = load_area_inputs(area, dev)
    ref = jrc(area, st, dev)
    v = detect(base["L"], base["valid"], dates=st.dates, dev=dev, models=models, water_ok=base["water_ok"])
    M = evaluate(area, st, base, v, ref, dev)["hmm"]
    a, _ = features(base["L"], base["valid"])
    Xh = sample_features(a, base["valid"], 30_000, torch.Generator().manual_seed(123))
    out = {"heldout_app_loglik": round(float(models[0].score_samples(Xh).mean()), 4),
           "water_km2": M["water_km2"], "flooded_veg_km2": M["flooded_veg_km2"],
           "late_false_water_far_from_jrc_km2": M["late_false_water_far_from_jrc_km2"],
           "permanent_iou_occ_ge90": M["permanent_water_vs_jrc"]["occ_ge90"]["iou"],
           "app_components": [(d["mean_HH"], d["mean_HV"], d["role"]) for d in describe(models[0], "app", map_components(*models))]}
    if "largest_body_km2" in M:
        out["largest_body_km2"] = M["largest_body_km2"]
    del st, base, ref, v
    _empty_cache()
    return out, Xh


def main(argv=None):
    import argparse
    from scripts.detect.stack import ROOT, device
    ap = argparse.ArgumentParser(description="GMM + HMM water / flooded-vegetation detector")
    ap.add_argument("areas", nargs="*", default=AREAS)
    ap.add_argument("--pooled", action="store_true", help="also fit one mixture pair across all areas and compare")
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args(argv)
    dev = device()
    docs = ROOT / "docs/eda/gmm"
    docs.mkdir(parents=True, exist_ok=True)
    rpath = docs / "results.json"
    results = json.loads(rpath.read_text()) if rpath.exists() else {}
    results.update({"rule": RULE, "defaults": DEFAULTS})
    results.setdefault("areas", {})
    rel = {}
    own_models, samples = {}, {}
    tt = time.time()
    for area in args.areas:
        results["areas"][area], own_models[area] = run_area(area, dev, docs, not args.no_figures, rel, samples)
        rpath.write_text(json.dumps(results, indent=1, default=float))
    if rel:
        results["reliability"] = {nm: reliability_table((torch.cat([p for p, _ in v]), torch.cat([y for _, y in v])))
                                  for nm, v in rel.items()}
    if args.pooled and len(samples) > 1:
        print("== pooled / transfer", flush=True)
        Xa = torch.cat([v[0] for v in samples.values()]); Xc = torch.cat([v[1] for v in samples.values()])
        pooled = (fit_gmm(Xa, DEFAULTS["k_app"], n_init=3), fit_gmm(Xc, DEFAULTS["k_chg"], n_init=3))
        P = {"components_appearance": describe(pooled[0], "app", map_components(*pooled)),
             "components_change": describe(pooled[1], "chg", map_components(*pooled)), "per_area": {}}
        for area in samples:
            P["per_area"][area] = {}
            P["per_area"][area]["pooled"], Xh = run_transfer(area, pooled, "pooled", dev)
            P["per_area"][area]["heldout_app_loglik_own"] = round(float(own_models[area][0].score_samples(Xh).mean()), 4)
            if area != "tonle-sap" and "tonle-sap" in own_models:
                P["per_area"][area]["tonle_sap_model"], _ = run_transfer(area, own_models["tonle-sap"], "tonle", dev)
        results["pooled_and_transfer"] = P
    results["total_runtime_s_last_run"] = round(time.time() - tt, 1)
    rpath.write_text(json.dumps(results, indent=1, default=float))
    print(f"wrote {rpath}  ({results['total_runtime_s_last_run']} s)", flush=True)
    if not args.no_figures:
        summary_figures(results, docs)
    return results


# =============================================================================================
# figures (matplotlib Agg)
# =============================================================================================
def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return matplotlib, plt


def _save(fig, docs, name):
    _, plt = _plt()
    fig.savefig(docs / name, dpi=100, bbox_inches="tight")
    plt.close(fig)
    print("  fig", name, (docs / name).stat().st_size // 1024, "KB", flush=True)


def _ext(st):
    w, s, e, n = st.bounds
    return [w, e, s, n]


ROLE_COL = {"open water": "#1f5fbf", "bright (can be flooded veg)": "#2a9d4b", "dark land": "#b07a2a",
            "flooded-veg change": "#d62828", "other change": "#666666"}


def _ellipses(ax, g, colors):
    import numpy as np
    from matplotlib.patches import Ellipse
    for k in range(g.K):
        vals, vecs = np.linalg.eigh(g.covs[k].numpy())
        ang = np.degrees(np.arctan2(vecs[1, 1], vecs[0, 1]))
        for nsd in (1, 2):
            ax.add_patch(Ellipse(g.means[k].numpy(), 2 * nsd * np.sqrt(vals[1]), 2 * nsd * np.sqrt(vals[0]), angle=ang,
                                 fill=False, color=colors[k], lw=1.6 if nsd == 1 else 0.8, ls="-" if nsd == 1 else "--"))
        ax.text(*g.means[k].numpy(), f"{k}", color="k", fontsize=9, ha="center", va="center",
                bbox=dict(boxstyle="round,pad=0.15", fc="white", ec=colors[k], alpha=0.85))


def area_figures(area, st, base, res, docs):
    import numpy as np
    matplotlib, plt = _plt()
    from matplotlib.colors import ListedColormap
    from matplotlib.patches import Patch
    dates = st.dates
    # mixtures over the density
    a, c = features(base["L"], base["valid"])
    gen = torch.Generator().manual_seed(1)
    Xa = sample_features(a, base["valid"], 200_000, gen).numpy()
    Xc = sample_features(c, base["valid"], 200_000, gen).numpy()
    del a, c
    fig, axs = plt.subplots(1, 2, figsize=(11, 4.6))
    axs[0].hist2d(Xa[:, 0], Xa[:, 1], bins=150, cmap="Greys", norm=matplotlib.colors.LogNorm())
    _ellipses(axs[0], res["gmm_app"], [ROLE_COL[d["role"]] for d in describe(res["gmm_app"], "app", res["sets"])])
    for v in (0.06, 0.12):
        axs[0].axvline(float(logit(torch.tensor(v))), color="#1f5fbf", lw=0.7, ls=":")
    axs[0].set_xlabel("logit HH   (brightness 0.01, 0.12, 0.5, 0.88 = -3.9, -1.9, 0, 1.9)")
    axs[0].set_ylabel("logit HV")
    axs[0].set_title(f"{area}: appearance mixture K={res['gmm_app'].K}\nblue = open water, dotted = old HH thresholds 0.06/0.12", fontsize=9)
    axs[1].hist2d(Xc[:, 0], Xc[:, 1], bins=150, range=[[-4, 4], [-4, 4]], cmap="Greys", norm=matplotlib.colors.LogNorm())
    _ellipses(axs[1], res["gmm_chg"], [ROLE_COL[d["role"]] for d in describe(res["gmm_chg"], "chg", res["sets"])])
    axs[1].set_xlim(-4, 4); axs[1].set_ylim(-4, 4)
    axs[1].plot([-4, 4], [-4, 4], color="k", lw=0.5, ls=":")
    axs[1].set_xlabel("change of logit HH since dry reference (first 2 dates)")
    axs[1].set_ylabel("change of logit HV")
    axs[1].set_title("change mixture; red = flooded-vegetation change\n(HH odds at least x2, HV clearly less)", fontsize=9)
    fig.suptitle("Gaussian mixtures fitted by EM over the pixel density (grey, log scale), all dates pooled; ellipses 1 and 2 sd", fontsize=10, y=1.04)
    _save(fig, docs, f"mixtures_{area}.png")
    if area == "tonle-sap":
        fig, axs = plt.subplots(1, 3, figsize=(15, 4.9))
        for ax, t in zip(axs[:2], (0, len(dates) - 1)):
            im = ax.imshow(res["p_water"][t].float().cpu().numpy(), cmap="Blues", vmin=0, vmax=1, extent=_ext(st))
            ax.set_title(f"HMM P(open water), {dates[t]}")
        axs[2].imshow(res["p_water_date"][-1].float().cpu().numpy(), cmap="Blues", vmin=0, vmax=1, extent=_ext(st))
        axs[2].set_title(f"per-date mixture P(open water), {dates[-1]}\n(no time model)")
        plt.colorbar(im, ax=list(axs), fraction=0.02, label="probability")
        _save(fig, docs, "p_water_tonle_sap.png")
        cmap = ListedColormap(["#1f5fbf", "#2a9d4b", "#e9e2cf"])
        fig, axs = plt.subplots(2, 5, figsize=(17, 7.2))
        for t, ax in enumerate(axs.flat):
            v = res["viterbi"][t].cpu().numpy().astype(float)
            v[v == 255] = np.nan
            ax.imshow(v, cmap=cmap, vmin=-0.5, vmax=2.5, extent=_ext(st), interpolation="nearest")
            ax.set_title(dates[t] + ("  (after 24-day gap)" if res["dt_steps"][t - 1] == 2 and t > 0 else ""), fontsize=9)
            ax.set_xticks([]); ax.set_yticks([])
        fig.legend(handles=[Patch(color=c_, label=n) for c_, n in zip(cmap.colors, STATE_NAMES)], loc="lower center", ncol=3)
        fig.suptitle("Tonle Sap: most likely state sequence per pixel (Viterbi path of the HMM, terrain prior on)")
        _save(fig, docs, "viterbi_tonle_sap.png")
        fig, axs = plt.subplots(1, 2, figsize=(13, 5.4))
        fw = res["first_water"].cpu().numpy().astype(float); fw[fw <= 0] = np.nan
        cd = base["change_date"].cpu().numpy().astype(float); cd[cd < 0] = np.nan
        cm2 = plt.get_cmap("viridis", len(dates) - 1)
        for ax, m, ttl in zip(axs, (fw, cd), ("HMM: first date in state open water\n(pixels not water on 19 Jun); red = land -> fl. veg -> water",
                                                   "current detector: CUSUM change date (any channel, any sign)")):
            im = ax.imshow(m, cmap=cm2, vmin=0.5, vmax=len(dates) - 0.5, extent=_ext(st), interpolation="nearest")
            ax.set_title(ttl, fontsize=9)
        yy, xx = np.nonzero(res["path_dry_fv_water"].cpu().numpy())
        w_, s_, e_, n_ = st.bounds; H, W = fw.shape
        axs[0].scatter(w_ + (xx + .5) / W * (e_ - w_), n_ - (yy + .5) / H * (n_ - s_), s=0.3, c="red")
        cb = plt.colorbar(im, ax=list(axs), fraction=0.025, ticks=range(1, len(dates)))
        cb.ax.set_yticklabels(dates[1:])
        _save(fig, docs, "first_water_tonle_sap.png")
    if area == "sevan":
        t = dates.index("2026-08-31")
        km2 = st.km2_per_px
        fig, axs = plt.subplots(1, 4, figsize=(18, 4.4))
        axs[0].imshow(base["L"][t, 0].cpu().numpy(), cmap="gray", vmin=0, vmax=0.6, extent=_ext(st))
        axs[0].set_title(f"HH brightness {dates[t]} (windy)")
        for ax, m, nm in zip(axs[1:], (base["water"][t], res["water_date"][t], res["water"][t]),
                             ("current detector (+terrain)", "per-date mixture", "HMM")):
            ax.imshow(m.cpu().numpy(), cmap=ListedColormap(["#efe9dc", "#1f5fbf"]), vmin=0, vmax=1, extent=_ext(st), interpolation="nearest")
            ax.set_title(f"{nm}: {float(m.sum()) * km2:.0f} km2")
        _save(fig, docs, "sevan_windy.png")


def summary_figures(results, docs):
    import datetime as _dt
    import numpy as np
    matplotlib, plt = _plt()
    import matplotlib.dates as mdates
    fig, axs = plt.subplots(1, 2, figsize=(10, 3.8))
    for area, A in results["areas"].items():
        for ax, nm in zip(axs, ("app", "chg")):
            ks = sorted(A["bic"][nm], key=int)
            ax.plot([int(k) for k in ks], [A["bic"][nm][k]["bic_per_sample"] for k in ks], "o-", label=area)
    for ax, nm in zip(axs, ("appearance (logit HH, logit HV)", "change (dHH, dHV)")):
        ax.axvline(DEFAULTS["k_app"], color="k", lw=0.6, ls=":")
        ax.set_xlabel("number of components K"); ax.set_ylabel("BIC per sample (lower = better)")
        ax.set_title(nm, fontsize=9); ax.legend(fontsize=8)
    fig.suptitle("BIC (30,000 samples per area) keeps falling with K: it cannot choose K here; dotted = K=6 used", fontsize=9)
    _save(fig, docs, "bic.png")
    fig, axs = plt.subplots(2, 2, figsize=(13, 8))
    sty = {"current": ("k", "o-", "current (hysteresis + terrain)"), "current_no_terrain": ("0.6", "o:", "current, no terrain"),
           "gmm_per_date": ("#e07b00", "s--", "per-date mixture"), "hmm": ("#1f5fbf", "^-", "HMM (+terrain prior)"),
           "hmm_no_terrain": ("#7fa7e0", "^:", "HMM, no terrain")}
    panels = [("tonle-sap", "water_km2", "Tonle Sap open water"), ("tonle-sap", "flooded_veg_km2", "Tonle Sap flooded vegetation"),
              ("sevan", "water_km2", "Lake Sevan open water (all water in frame)"), ("ararat", "water_km2", "Ararat Valley 'water' (all of it false)")]
    for ax, (area, key, ttl) in zip(axs.flat, panels):
        if area not in results["areas"]:
            continue
        M = results["areas"][area]["metrics"]
        x = [_dt.date.fromisoformat(d) for d in M["dates"]]
        for nm, (c_, ls, lab) in sty.items():
            if nm in M and not (key == "flooded_veg_km2" and nm.endswith("no_terrain")):
                ax.plot(x, M[nm][key], ls, color=c_, label=lab, ms=4)
        if area == "sevan":
            ax.axhline(SEVAN_KM2, color="g", lw=1, ls="--", label="published 1,242 km2")
            for wd in WINDY["sevan"]:
                ax.axvline(_dt.date.fromisoformat(wd), color="r", lw=0.6, ls="--")
            ax.text(_dt.date(2026, 9, 1), ax.get_ylim()[0], " windy", color="r", fontsize=8, va="bottom")
        ax.axvspan(_dt.date(2026, 7, 27), _dt.date(2026, 8, 10), color="0.88", label="no data (outage)")
        ax.set_title(ttl, fontsize=10); ax.set_ylabel("km2"); ax.legend(fontsize=7)
        ax.xaxis.set_major_locator(mdates.MonthLocator()); ax.xaxis.set_major_formatter(mdates.DateFormatter("1 %b"))
    _save(fig, docs, "areas.png")
    fig, axs = plt.subplots(1, 3, figsize=(16, 4.6))
    for nm, c_, lab in (("gmm_per_date", "#e07b00", "per-date mixture"), ("hmm", "#1f5fbf", "HMM")):
        R = results.get("reliability", {}).get(nm)
        if not R:
            continue
        b = [r for r in R["bins"] if r["n"] > 0]
        axs[0].plot([r["mean_p"] for r in b], [r["observed_water"] for r in b], "o-", color=c_, label=f"{lab} (Brier {R['brier']:.4f})")
        for r in b:
            axs[0].annotate(str(r["n"]), (r["mean_p"], r["observed_water"]), fontsize=6, color=c_, xytext=(3, -9), textcoords="offset points")
    axs[0].plot([0, 1], [0, 1], "k:", lw=0.8)
    axs[0].set_xlabel("predicted P(open water)"); axs[0].set_ylabel("observed share that is JRC permanent water")
    axs[0].set_title("reliability on JRC anchor pixels (permanent vs never water,\nshoreline excluded; all areas and dates; n per bin)", fontsize=9)
    axs[0].legend(fontsize=8)
    names = ["water", "fl. veg", "land"]
    for ax, area in zip(axs[1:], ("tonle-sap", "sevan")):
        if area not in results["areas"]:
            continue
        A = np.array(results["areas"][area]["hmm"]["A_per_12_days"])
        ax.imshow(A, cmap="viridis", vmin=0, vmax=1)
        for i in range(3):
            for j in range(3):
                ax.text(j, i, f"{A[i, j]:.3f}", ha="center", va="center", color="w" if A[i, j] < 0.6 else "k", fontsize=9)
        ax.set_xticks(range(3)); ax.set_yticks(range(3)); ax.set_xticklabels(names); ax.set_yticklabels(names)
        ax.set_xlabel("to (next pass, 12 days)"); ax.set_ylabel("from")
        ax.set_title(f"{area}: transition matrix learned by Baum-Welch (no prior)", fontsize=9)
    _save(fig, docs, "reliability_transitions.png")


if __name__ == "__main__":
    main()
