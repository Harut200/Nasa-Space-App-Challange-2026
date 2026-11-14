"""Calibration and power of the statistical change tests on simulated gamma speckle.
Run: .venv/bin/python -m pytest tests/test_stats_change.py
"""
import math

import numpy as np
import pytest
import torch
from scipy import stats as ss

from scripts.detect import stats_change as sc


def gamma_stack(T, C, H, W, L, mean=None, seed=0):
    g = torch.Generator().manual_seed(seed)
    x = torch._standard_gamma(torch.full((T, C, H, W), float(L), dtype=torch.float64), generator=g) / L
    return x * (1.0 if mean is None else mean)


@pytest.mark.parametrize("C", [1, 2])
def test_no_change_is_calibrated(C):
    L, T = 8.0, 8
    I = gamma_stack(T, C, 200, 200, L, seed=1)
    p, Z = sc.omnibus_pvalue(I, [L] * C)
    for a in (0.05, 0.01):
        frac = float((p < a).double().mean())
        assert abs(frac - a) < 0.25 * a + 0.002, (a, frac)
    rej, _ = sc.bh(p, torch.ones(200, 200, dtype=torch.bool), 0.01)
    assert int(rej.sum()) <= 3
    # pairwise exact test is calibrated too
    pp = sc.pair_pvalue_exact(I[0, 0], I[1, 0], L)
    assert abs(float((pp < 0.05).double().mean()) - 0.05) < 0.006
    # sequential test: with the gate forced open, false change events per date ~ alpha
    seq = sc.sequential_changes(I, [L] * C, torch.ones(200, 200, dtype=torch.bool), 0.01)
    rate = float(seq["change"][1:].double().mean())
    assert rate < 0.02, rate


def test_step_change_detected_with_right_date():
    # ENL 16 ~ the effective ENL of the previews; x3 = +4.8 dB (a water <-> land step is >> 10 dB).
    # Measured power for weaker steps (documented in docs/eda/stats-change.md): ENL 8, +3 dB -> 31 %.
    L, T, j = 16.0, 10, 4
    mean = torch.ones(T, 2, 1, 1, dtype=torch.float64)
    mean[j:] = 3.0
    I = gamma_stack(T, 2, 100, 100, L, mean, seed=2)
    p, _ = sc.omnibus_pvalue(I, [L, L])
    rej, _ = sc.bh(p, torch.ones(100, 100, dtype=torch.bool), 0.01)
    assert float(rej.double().mean()) > 0.99
    seq = sc.sequential_changes(I, [L, L], rej, 0.01)
    assert float((seq["first"] == j).double().mean()) > 0.9  # measured 0.97
    assert float((seq["direction"] == 1).double().mean()) > 0.95


def test_two_changes_found():
    L, T = 16.0, 10
    mean = torch.ones(T, 2, 1, 1, dtype=torch.float64)
    mean[3:7] = 3.0   # flood-like brightening (+4.8 dB) then back
    I = gamma_stack(T, 2, 100, 100, L, mean, seed=3)
    p, _ = sc.omnibus_pvalue(I, [L, L])
    rej, _ = sc.bh(p, torch.ones(100, 100, dtype=torch.bool), 0.01)
    seq = sc.sequential_changes(I, [L, L], rej, 0.01)
    both = seq["change"][3] & seq["change"][7]
    assert float(both.double().mean()) > 0.9  # measured 0.98
    assert float((seq["n_changes"] == 2).double().mean()) > 0.9  # measured 0.95


@pytest.mark.parametrize("L", [4.0, 10.0, 30.0])
def test_enl_estimators(L):
    I = gamma_stack(6, 1, 210, 210, L, seed=4)[:, 0]
    m = torch.ones_like(I, dtype=torch.bool)
    est = sc.enl_spatial(I, m)
    assert abs(est / L - 1) < 0.15, est
    est_t = sc.enl_temporal(I, m[0])
    assert abs(est_t / L - 1) < 0.15, est_t
    # empirical-null calibration recovers L when all pixels are unchanged
    n_eff, _ = sc.calibrate_neff(I[:, None], m[0])
    assert abs(n_eff[0] / L - 1) < 0.15, n_eff


def test_pairwise_matches_scipy():
    rng = np.random.default_rng(5)
    for n in (1.0, 3.0, 8.5, 25.0):
        r = np.exp(rng.normal(0, 0.8, 20))
        cdf = ss.f.cdf(r, 2 * n, 2 * n)
        ref = np.minimum(1, 2 * np.minimum(cdf, 1 - cdf))
        got = sc.pair_pvalue_exact(torch.tensor(r), torch.ones(20, dtype=torch.float64), n).numpy()
        np.testing.assert_allclose(got, ref, rtol=1e-7, atol=1e-12)
        # the chi2 approximation of -2 rho ln Q is close for moderate n
        if n >= 8:
            lr = sc.pair_pvalue_lr(torch.tensor(r)[None], torch.ones(1, 20, dtype=torch.float64), [n]).numpy()
            np.testing.assert_allclose(lr, ref, atol=0.01)


def test_special_functions():
    x = np.array([0.5, 3.0, 17.0, 40.0])
    np.testing.assert_allclose(sc.chi2_sf(torch.tensor(x), 9).numpy(), ss.chi2.sf(x, 9), rtol=1e-10)
    q = np.array([0.1, 0.5, 0.99])
    np.testing.assert_allclose(sc.chi2_ppf(torch.tensor(q), 9).numpy(), ss.chi2.ppf(q, 9), rtol=1e-6)
    a, b, xx = np.array([0.5, 2, 10, 30]), np.array([3, 2, 10, 5]), np.array([0.1, 0.5, 0.7, 0.9])
    np.testing.assert_allclose(sc.betainc(torch.tensor(a), torch.tensor(b), torch.tensor(xx)).numpy(),
                               ss.beta.cdf(xx, a, b), rtol=1e-9)


def test_empirical_null_and_omnibus_scipy():
    # omnibus with k = 2 equals the LR test; chi2_corrected_sf vs scipy at w2 = 0
    z = torch.tensor([1.0, 5.0, 20.0], dtype=torch.float64)
    np.testing.assert_allclose(sc.chi2_corrected_sf(z, 4, 0.0).numpy(), ss.chi2.sf(z.numpy(), 4), rtol=1e-10)
    ref = torch.distributions.Chi2(9.0).sample((20000,)).double()
    zz = torch.distributions.Chi2(9.0).sample((20000,)).double()
    p = sc.empirical_null_p(zz, ref)
    assert abs(float((p < 0.05).double().mean()) - 0.05) < 0.01
    assert abs(float((p < 0.01).double().mean()) - 0.01) < 0.004
