"""Fast tests for scripts/detect/deep_ae.py (random weights only: no download)."""
import math

import pytest
import torch

from scripts.detect import deep_ae as d


def small_model(**kw):
    torch.manual_seed(0)
    return d.DeepAE(n_in=8, pretrained=False, **kw).eval()


@pytest.mark.parametrize("hw", [(37, 53), (64, 64), (101, 17)])
def test_shapes_odd_sizes(hw):
    m = small_model()
    x = torch.randn(2, 8, 64, 64)
    rec, z = m(x)
    assert rec.shape == x.shape and z.shape[1] == 16
    e = m.embed(torch.randn(1, 8, *hw))
    assert e.shape == (1, 16, *hw)


def test_rgb_and_skip_variants():
    m = d.DeepAE(n_in=3, inputs="rgb", pretrained=False, skip=True).eval()
    rec, _ = m(torch.randn(1, 3, 48, 48))
    assert rec.shape == (1, 3, 48, 48)


@pytest.mark.parametrize("dev", ["cpu", "mps"])
def test_runs_on_device(dev):
    if dev == "mps" and not torch.backends.mps.is_available():
        pytest.skip("no MPS")
    m = small_model().to(dev)
    e = m.embed(torch.randn(1, 8, 33, 45, device=dev))
    assert e.shape == (1, 16, 33, 45) and torch.isfinite(e).all()


def test_embedding_depends_on_input():
    # an untrained random EfficientNet in eval mode (BN running stats 0/1) nearly ignores its input;
    # a few train-mode passes (as in training) set the batch-norm statistics
    m = small_model().train()
    with torch.no_grad():
        for _ in range(50):
            m(torch.randn(4, 8, 32, 32))
    m.eval()
    a = m.embed(torch.zeros(1, 8, 32, 32))
    b = m.embed(torch.randn(1, 8, 32, 32))
    assert (a - b).abs().mean() > 1e-4


def test_gmm_recovers_mixture():
    g = torch.Generator().manual_seed(0)
    means = torch.tensor([[0.0, 0.0], [6.0, 0.0], [0.0, 6.0]])
    x = torch.cat([torch.randn(2000, 2, generator=g) * s + mu for mu, s in zip(means, (1.0, 0.5, 0.8))])
    fits = {k: d.fit_gmm(x, k, restarts=2, seed=0) for k in (2, 3, 4, 5)}
    assert min(fits, key=lambda k: fits[k]["bic"]) == 3
    got = fits[3]["means"].float()
    for mu in means:
        assert (got - mu).norm(dim=1).min() < 0.2
    assert torch.allclose(fits[3]["weights"].float(), torch.full((3,), 1 / 3), atol=0.02)
    lab, p, ll = d.gmm_predict(x, fits[3], "cpu")
    assert lab.shape == (6000,) and float(d.ari(lab.numpy(), torch.arange(3).repeat_interleave(2000).numpy())) > 0.95


def test_ari_nmi_basics():
    import numpy as np
    a = np.array([0, 0, 1, 1, 2, 2])
    assert d.ari(a, a) == pytest.approx(1.0) and d.nmi(a, a) == pytest.approx(1.0)
    assert d.ari(a, np.array([2, 2, 0, 0, 1, 1])) == pytest.approx(1.0)


def test_tiny_training_decreases_loss():
    torch.manual_seed(0)
    m = d.DeepAE(n_in=8, pretrained=False, emb=8).train()
    yy, xx = torch.meshgrid(torch.linspace(0, 1, 32), torch.linspace(0, 1, 32), indexing="ij")
    base = torch.stack([torch.sin(6 * xx + k) * torch.cos(4 * yy - k) for k in range(8)])[None].repeat(4, 1, 1, 1)
    valid = torch.ones(4, 1, 32, 32, dtype=torch.bool)
    g = torch.Generator().manual_seed(0)
    opt = torch.optim.Adam(m.parameters(), lr=2e-3)
    losses = []
    for _ in range(25):
        hid = d.block_mask(4, 32, 8, 0.3, g)
        rec, _ = m(base * (~hid).float())
        loss = d.masked_loss(rec, base, hid, valid)
        opt.zero_grad()
        loss.backward()
        opt.step()
        losses.append(loss.item())
    assert sum(losses[-5:]) / 5 < 0.7 * sum(losses[:5]) / 5
