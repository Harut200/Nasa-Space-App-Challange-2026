"""Tests for scripts/detect/gmm.py: EM, forward-backward, Viterbi, and a synthetic flood scene."""
import itertools
import math

import numpy as np
import pytest
import torch

from scripts.detect import gmm as G


def _mix3(n=6000, seed=0):
    g = torch.Generator().manual_seed(seed)
    mus = torch.tensor([[-3.0, -3.0], [0.0, 1.0], [3.0, -1.0]], dtype=torch.float64)
    covs = torch.tensor([[[0.3, 0.1], [0.1, 0.2]], [[0.5, -0.2], [-0.2, 0.4]], [[0.2, 0.0], [0.0, 0.6]]], dtype=torch.float64)
    w = torch.tensor([0.2, 0.5, 0.3], dtype=torch.float64)
    z = torch.multinomial(w, n, replacement=True, generator=g)
    Lc = torch.linalg.cholesky(covs)
    e = torch.randn(n, 2, generator=g, dtype=torch.float64)
    X = mus[z] + torch.einsum("nij,nj->ni", Lc[z], e)
    return X, mus, covs, w


def test_em_recovers_three_components():
    X, mus, covs, w = _mix3()
    g = G.fit_gmm(X, 3, n_init=3, reg_covar=1e-6)
    o = torch.argsort(mus[:, 0])
    assert torch.allclose(g.means, mus[o], atol=0.08)
    assert torch.allclose(g.weights, w[o], atol=0.03)
    assert torch.allclose(g.covs, covs[o], atol=0.08)
    h = np.array(g.history)
    assert np.all(np.diff(h) >= -1e-9), "EM log-likelihood must not decrease"
    assert g.converged


def test_logpdf_matches_scipy_and_bic_prefers_true_k():
    from scipy.stats import multivariate_normal
    X, *_ = _mix3(n=3000)
    g = G.fit_gmm(X, 3, n_init=2)
    ref = np.stack([multivariate_normal(g.means[k].numpy(), g.covs[k].numpy()).logpdf(X.numpy()) for k in range(3)], 1)
    assert np.allclose(g.component_logpdf(X).numpy(), ref, atol=1e-8)
    mix = np.log(np.exp(ref) @ g.weights.numpy())
    assert np.allclose(g.score_samples(X).numpy(), mix, atol=1e-8)
    bics = {k: G.fit_gmm(X, k, n_init=2).bic(X) for k in (1, 2, 3, 4, 5)}
    assert min(bics, key=bics.get) == 3


def test_em_one_component_is_closed_form():
    X, *_ = _mix3(n=500)
    sw = torch.rand(500, generator=torch.Generator().manual_seed(1), dtype=torch.float64)
    g = G.fit_gmm(X, 1, sample_weight=sw, n_init=1, reg_covar=0.0)
    mu = (X * sw[:, None]).sum(0) / sw.sum()
    d = X - mu
    C = (sw[:, None, None] * d[:, :, None] * d[:, None, :]).sum(0) / sw.sum()
    assert torch.allclose(g.means[0], mu, atol=1e-10)
    assert torch.allclose(g.covs[0], C, atol=1e-10)


def _brute(logb, pi, A):
    """enumerate all paths; returns marginals [T,S] and the best path."""
    T, S = logb.shape
    marg = torch.zeros(T, S, dtype=torch.float64)
    Z, best, bp = 0.0, -1e300, None
    for path in itertools.product(range(S), repeat=T):
        lp = math.log(pi[path[0]]) + float(logb[0, path[0]])
        for t in range(1, T):
            lp += math.log(A[path[t - 1], path[t]]) + float(logb[t, path[t]])
        p = math.exp(lp)
        Z += p
        for t in range(T):
            marg[t, path[t]] += p
        if lp > best:
            best, bp = lp, path
    return marg / Z, list(bp), math.log(Z)


def test_forward_backward_and_viterbi_match_brute_force():
    g = torch.Generator().manual_seed(3)
    T, S = 5, 3
    logb = torch.randn(T, S, generator=g, dtype=torch.float64) * 2
    pi = torch.tensor([0.5, 0.3, 0.2], dtype=torch.float64)
    A = torch.softmax(torch.randn(S, S, generator=g, dtype=torch.float64), 1)
    gam, xi, logZ = G.forward_backward(logb[:, :, None], torch.log(pi), torch.log(A))
    marg, path, lz = _brute(logb, pi.tolist(), A.numpy())
    assert torch.allclose(gam[:, :, 0].sum(1), torch.ones(T, dtype=torch.float64))
    assert torch.allclose(gam[:, :, 0], marg, atol=1e-10)
    assert abs(float(logZ[0]) - lz) < 1e-10
    assert abs(float(xi.sum()) - (T - 1)) < 1e-9
    assert G.viterbi(logb[:, :, None], torch.log(pi), torch.log(A))[:, 0].tolist() == path


def test_phantom_slot_equals_squared_transition():
    """a 2-step gap through an unobserved slot gives the same likelihood as using A @ A for that step."""
    g = torch.Generator().manual_seed(4)
    logb = torch.randn(3, 2, 1, generator=g, dtype=torch.float64)
    pi = torch.tensor([0.6, 0.4], dtype=torch.float64)
    A = torch.tensor([[0.9, 0.1], [0.3, 0.7]], dtype=torch.float64)
    lbx, pos = G._expand(logb, [1, 2])
    assert pos == [0, 1, 3]
    _, _, lz = G.forward_backward(lbx, torch.log(pi), torch.log(A))
    A2 = A @ A
    a1 = pi * torch.exp(logb[0, :, 0])
    a2 = (a1 @ A) * torch.exp(logb[1, :, 0])
    a3 = (a2 @ A2) * torch.exp(logb[2, :, 0])
    assert abs(float(lz[0]) - math.log(float(a3.sum()))) < 1e-10


def test_viterbi_known_path():
    S, T = 3, 8
    truth = [2, 2, 1, 1, 0, 0, 0, 0]
    logb = torch.full((T, S, 1), -5.0)
    for t, s in enumerate(truth):
        logb[t, s, 0] = 0.0
    A = torch.full((S, S), 0.1) + torch.eye(S) * 0.7
    p = G.viterbi(logb, torch.log(torch.ones(S) / S), torch.log(A))
    assert p[:, 0].tolist() == truth


def _scene(T=10, H=48, W=48, seed=0):
    """brightness scene: background land; a growing lake; a ring dry -> bright (double bounce) -> dark;
    a permanent lake that is wind-roughened (land-like brightness) on one date."""
    g = torch.Generator().manual_seed(seed)
    L = torch.empty(T, 2, H, W)
    L[:, 0] = 0.45 + 0.08 * torch.randn(T, H, W, generator=g)
    L[:, 1] = 0.35 + 0.07 * torch.randn(T, H, W, generator=g)
    yy, xx = torch.meshgrid(torch.arange(H), torch.arange(W), indexing="ij")
    r = ((yy - 16) ** 2 + (xx - 16) ** 2).float().sqrt()
    water = torch.zeros(T, H, W, dtype=torch.bool)
    ring = (r > 8) & (r <= 11)
    for t in range(T):
        rad = 4 + 0.5 * t
        m = r <= min(rad, 8)
        water[t] |= m
        if 3 <= t < 6:                         # ring: bright double bounce (HH up, HV flat)
            L[t, 0][ring] = 0.85 + 0.04 * torch.randn(int(ring.sum()), generator=g)
        if t >= 6:
            water[t] |= ring
    lake = ((yy - 36) ** 2 + (xx - 34) ** 2) <= 36
    water |= lake[None]
    L[:, 0][water] = 0.004 + 0.002 * torch.rand(int(water.sum()), generator=g)
    L[:, 1][water] = 0.004 + 0.002 * torch.rand(int(water.sum()), generator=g)
    windy = 7
    L[windy, 0][lake] = 0.35 + 0.08 * torch.randn(int(lake.sum()), generator=g)   # wind: lake looks like land
    L[windy, 1][lake] = 0.15 + 0.05 * torch.randn(int(lake.sum()), generator=g)
    return L.clamp(0.002, 1), torch.ones(T, H, W, dtype=torch.bool), lake, ring, windy


def test_synthetic_scene_hmm_keeps_windy_lake_and_finds_ring_sequence():
    L, valid, lake, ring, windy = _scene()
    out = G.detect(L, valid, kn={"k_app": 4, "k_chg": 4, "n_fit": 20000, "hmm_fit": 2304, "min_blob_px": 0})
    # per-date mixture loses the windy lake, the HMM keeps it
    assert float(out["water_date"][windy][lake].float().mean()) < 0.2
    assert float(out["water"][windy][lake].float().mean()) > 0.9
    # the ring goes land -> flooded vegetation -> water in the Viterbi path
    v = out["viterbi"][:, ring]
    assert float((v[0] == 2).float().mean()) > 0.9
    assert float((v[4] == 1).float().mean()) > 0.8
    assert float((v[8] == 0).float().mean()) > 0.9
    assert float(out["path_dry_fv_water"][ring].float().mean()) > 0.8
    # per-date thresholding of HH (the current kind of rule) calls the windy lake land
    assert float((L[windy, 0][lake] < 0.12).float().mean()) < 0.1
    # posteriors are distributions
    assert out["gamma_sum_check"] < 2e-3
    # chunked evaluation gives the same answer as one chunk
    out2 = G.detect(L, valid, kn={"k_app": 4, "k_chg": 4, "n_fit": 20000, "hmm_fit": 2304, "min_blob_px": 0},
                    models=(out["gmm_app"], out["gmm_chg"]), chunk=500)
    assert torch.equal(out2["viterbi"], out["viterbi"])
