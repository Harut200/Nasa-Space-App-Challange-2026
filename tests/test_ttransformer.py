"""Fast CPU tests for the time transformer (scripts/detect/ttransformer.py)."""
import math

import pytest
import torch

from scripts.detect import ttransformer as tt

DATES = ["2026-06-19", "2026-07-01", "2026-07-13", "2026-07-25", "2026-08-18",
         "2026-08-30", "2026-09-11", "2026-09-23", "2026-10-05", "2026-10-17"]
DAYS = torch.tensor([tt.doy(d) for d in DATES])


def _model(n_in=3, **kw):
    torch.manual_seed(0)
    return tt.TTransformer(n_in=n_in, d=32, layers=2, heads=4, ff=64, dropout=0.0, **kw).eval()


def test_shapes_and_param_count():
    m = _model()
    x = torch.rand(5, 10, 3)
    q = torch.zeros(5, 10, dtype=torch.bool)
    q[:, 4] = True
    mu, lv = m(x, DAYS.expand(5, -1), q)
    assert mu.shape == (5, 10, 2) and lv.shape == (5, 10, 2)
    assert 5_000 < tt.n_params(m) < 200_000
    assert tt.n_params(tt.TTransformer(**tt.VARIANTS["base"])) < 200_000


def test_query_unobserved_day():
    """11 tokens: the ten passes + a query at 6 Aug (inside the outage)."""
    m = _model()
    x = torch.cat([torch.rand(2, 10, 3), torch.zeros(2, 1, 3)], 1)
    d = torch.cat([DAYS, torch.tensor([tt.doy("2026-08-06")])]).expand(2, -1)
    q = torch.zeros(2, 11, dtype=torch.bool)
    q[:, -1] = True
    mu, lv = m(x, d, q)
    assert mu.shape == (2, 11, 2) and torch.isfinite(mu).all() and torch.isfinite(lv).all()


@pytest.mark.parametrize("residual", [False, True])
def test_no_leakage_from_hidden_input(residual):
    """Changing the hidden date's input values must not change its prediction."""
    m = _model(residual=residual)
    x = torch.rand(3, 10, 3)
    q = torch.zeros(3, 10, dtype=torch.bool)
    q[:, 5] = True
    mu1, lv1 = m(x, DAYS.expand(3, -1), q)
    x2 = x.clone()
    x2[:, 5] = torch.rand(3, 3) * 10
    mu2, lv2 = m(x2, DAYS.expand(3, -1), q)
    assert torch.allclose(mu1, mu2) and torch.allclose(lv1, lv2)
    # and a padded slot is invisible too
    pad = torch.zeros(3, 10, dtype=torch.bool)
    pad[:, 7] = True
    x3 = x.clone()
    x3[:, 7] = 99.0
    a, _ = m(x, DAYS.expand(3, -1), q, pad)
    b, _ = m(x3, DAYS.expand(3, -1), q, pad)
    assert torch.allclose(a[:, 5], b[:, 5], atol=1e-6)


def test_time_encoding_distinguishes_gaps():
    """A query 12 days after its neighbour vs 24 days after: different encodings, different predictions."""
    f = tt.time_features(torch.tensor([200.0, 212.0, 224.0]))
    assert (f[1] - f[0]).abs().sum() > 0.1 and (f[2] - f[0]).abs().sum() > 0.1
    assert (f[2] - f[1]).abs().sum() > 0.1
    # exact day differences: a 12-day and a 24-day difference give different encoding differences
    d12 = tt.time_features(torch.tensor(224.0)) - tt.time_features(torch.tensor(212.0))
    d24 = tt.time_features(torch.tensor(224.0)) - tt.time_features(torch.tensor(200.0))
    assert not torch.allclose(d12, d24, atol=1e-3)


def test_hidden_patterns_include_adjacent_pairs():
    g = torch.Generator().manual_seed(0)
    h = tt.sample_hidden(4000, 10, list(range(10)), g)
    n = h.sum(1)
    assert set(n.tolist()) <= {1, 2}
    adj = ((h[:, :-1] & h[:, 1:]).any(1)).float().mean()
    assert adj > 0.2          # adjacent pairs (double-length gaps) are in training
    excl = tt.sample_hidden(500, 10, [0, 1, 2, 3, 5, 6, 7, 8, 9], g)
    assert not excl[:, 4].any()


def _synthetic(H=64, W=64, seed=0):
    g = torch.Generator().manual_seed(seed)
    t = DAYS[:, None, None]
    t0 = 190 + 80 * torch.rand(H, W, generator=g)
    tau = 3 + 10 * torch.rand(H, W, generator=g)
    a = 0.3 + 0.3 * torch.rand(H, W, generator=g)
    b = torch.tensor([-0.35, 0.0, 0.2])[torch.randint(3, (H, W), generator=g)]
    hh = a + b / (1 + torch.exp(-(t - t0) / tau)) + 0.015 * torch.randn(10, H, W, generator=g)
    hv = 0.5 * hh + 0.1 + 0.015 * torch.randn(10, H, W, generator=g)
    L = torch.stack([hh, hv], 1)
    east = torch.zeros(H, W, dtype=torch.bool)
    east[:, int(W * 0.7):] = True
    return {"L": L, "vall": torch.ones(H, W, dtype=torch.bool), "east": east, "dates": DATES, "days": DAYS}


def test_synthetic_training_beats_persistence():
    P = _synthetic()
    old = dict(tt.TRAIN)
    tt.TRAIN.update(batch=128, lr=3e-3)
    try:
        var = {"d": 32, "layers": 2, "heads": 4, "ff": 64, "dropout": 0.0, "context": 0, "var_head": True}
        model, hist = tt.train_model(P, var, torch.device("cpu"), epochs=40, pixels=3000, log=False)
    finally:
        tt.TRAIN.clear()
        tt.TRAIN.update(old)
    # hold-out pixels, hide 30 Aug (k=5), predict from the rest
    idx = torch.nonzero(P["east"].flatten()).squeeze(1)
    feat = tt.token_features(P["L"], P["vall"][None].expand(10, -1, -1), 0)
    q = torch.zeros(10, dtype=torch.bool)
    k = 5
    q[k] = True
    mu, sd = tt.predict_image(model, feat, DAYS, q, torch.zeros(10, dtype=torch.bool), idx, torch.device("cpu"))
    y = P["L"][k].reshape(2, -1)[:, idx]
    prev, nxt = P["L"][k - 1].reshape(2, -1)[:, idx], P["L"][k + 1].reshape(2, -1)[:, idx]
    mse = lambda p: float(((p - y) ** 2).mean())
    m_model, m_prev, m_lin = mse(mu[0]), mse(prev), mse(0.5 * (prev + nxt))
    assert m_model < m_prev, (m_model, m_prev, m_lin)
    assert m_model < 2.0 * m_lin, (m_model, m_prev, m_lin)
    assert torch.isfinite(sd).all() and sd.min() > 0
    assert math.isfinite(hist["val_mse"][-1])


def test_interp_prior_is_linear_interpolation():
    """The residual model's prior for a hidden date = linear interpolation of the visible neighbours."""
    x = torch.zeros(1, 10, 3)
    x[0, :, 0] = DAYS / 100
    q = torch.zeros(1, 10, dtype=torch.bool)
    q[0, 4] = True
    prior, gap = tt.TTransformer.interp_prior(x, DAYS[None], ~q)
    assert torch.allclose(prior[0, 4, 0], DAYS[4] / 100, atol=1e-5)
    assert float(gap[0, 4]) == float(DAYS[5] - DAYS[3])
