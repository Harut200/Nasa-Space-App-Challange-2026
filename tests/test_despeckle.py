"""Fast CPU tests for scripts/detect/despeckle.py (synthetic data only, no files needed)."""
import math

import pytest
import torch

from scripts.detect import despeckle as D

CPU = torch.device("cpu")


def scene(H=64, W=64, seed=0):
    """Piecewise-constant 2-channel scene: a vertical step, a bright square, a dark disc."""
    g = torch.Generator().manual_seed(seed)
    c = torch.full((2, H, W), 0.30)
    c[:, :, W // 2:] = 0.70
    c[:, 8:24, 8:24] = 0.90
    yy, xx = torch.meshgrid(torch.arange(H), torch.arange(W), indexing="ij")
    c[:, (yy - 44) ** 2 + (xx - 48) ** 2 < 64] = 0.05
    c[1] *= 0.8
    del g
    return c


def noisy_stack(clean, T=6, sigma=0.06, seed=1):
    g = torch.Generator().manual_seed(seed)
    return clean[None] + sigma * torch.randn(T, *clean.shape, generator=g)


@pytest.fixture(scope="module")
def n2n_model():
    """Short Noise2Noise training on random crops of independent noisy copies of the scene."""
    torch.manual_seed(0)
    gen = torch.Generator().manual_seed(0)
    clean = scene()
    m = D.ResUNet(2, 2, base=8, depth=2)
    opt = torch.optim.Adam(m.parameters(), lr=3e-3)
    for it in range(220):
        xs = noisy_stack(clean, T=4, seed=100 + it)                 # [T,2,H,W]
        r, c = torch.randint(0, 33, (2,), generator=gen).tolist()
        xs = torch.stack([xs[:, :, r:r + 32, c:c + 32] for _ in range(1)])  # [1,T,2,32,32]
        xs = xs.expand(6, -1, -1, -1, -1).contiguous()
        loss = D.step_loss("n2n", m, xs, None, gen, CPU, None)
        opt.zero_grad()
        loss.backward()
        opt.step()
    m.eval()
    return m, clean


def test_n2n_reduces_error_and_beats_boxcar_on_edges(n2n_model):
    m, clean = n2n_model
    x = noisy_stack(clean, T=1, seed=999)[0]
    with torch.no_grad():
        y = m(x[None])[0]
    mse_noisy = float(((x - clean) ** 2).mean())
    mse_net = float(((y - clean) ** 2).mean())
    assert mse_net < 0.6 * mse_noisy
    fp = torch.ones(64, 64, dtype=torch.bool)
    box = D.BASELINES["box3"](x[None], fp)[0]
    # edge sharpness: erf fit to the row-averaged profile across the vertical step (cols 24..40, rows 28..36)
    s = torch.arange(24, 41).float() - 31.5
    w_net = D.logistic_edge_fit(y[0, 28:37, 24:41], s)[1].median()
    w_box = D.logistic_edge_fit(box[0, 28:37, 24:41], s)[1].median()
    assert float(w_net) < float(w_box)


def test_blindspot_hides_centre_pixel():
    torch.manual_seed(0)
    for shift in (1, 3):
        net = D.BlindSpotNet(width=8, shift=shift).eval()
        x = torch.rand(1, 2, 40, 40, requires_grad=True)
        mu, _ = net(x)
        mu[0, :, 20, 20].sum().backward()
        g = x.grad[0].abs().sum(0)
        k = shift - 1
        assert float(g[20 - k:21 + k, 20 - k:21 + k].sum()) == 0.0      # excluded square
        assert float(g.sum()) > 0
        assert float(g[20 - k - 1:22 + k, 20 - k - 1:22 + k].sum()) > 0   # the ring just outside is seen
        # changing the centre pixel does not change its prediction
        with torch.no_grad():
            x2 = x.detach().clone()
            x2[0, :, 20, 20] += 0.5
            assert torch.allclose(net(x.detach())[0][0, :, 20, 20], net(x2)[0][0, :, 20, 20], atol=1e-6)


def test_multitemporal_cannot_see_target_noise():
    torch.manual_seed(0)
    T, P = 5, 32
    net = D.MTNet(width=8, heads=2, shift=3).eval()
    xs = torch.rand(1, T, 2, P, P, requires_grad=True)
    vs = torch.ones(1, T, P, P, dtype=torch.bool)
    days = torch.arange(T).float()[None] * 12
    t = torch.tensor([2])
    mu, _ = net(xs, vs, days, t)
    mu[0, :, 16, 16].sum().backward()
    g = xs.grad[0].abs().sum(1)                    # [T, P, P]
    assert float(g[2, 14:19, 14:19].sum()) == 0.0  # target date: 5x5 around p is invisible
    assert float(g[2].sum()) > 0                   # ...but its surroundings are used
    assert float(g[1, 16, 16]) > 0                 # other dates are seen at p itself
    # forward_all gives the same answer for date 2
    with torch.no_grad():
        mu_all, _ = net.forward_all(xs[0].detach(), vs[0], days[0])
        mu2, _ = net(xs.detach(), vs, days, t)
    assert torch.allclose(mu_all[2], mu2[0], atol=1e-5)


def test_posterior_is_between_prior_and_observation():
    y = torch.tensor([0.5])
    mu = torch.tensor([0.3])
    for ls, expect in ((torch.tensor([-30.0]), 0.3), (torch.tensor([5.0]), 0.5)):
        p = D.posterior(y, mu, ls, torch.tensor([0.02]))
        assert abs(float(p) - expect) < 1e-3


def test_denoise_preserves_mean_and_invalid(n2n_model):
    m, clean = n2n_model
    T = 3
    x = noisy_stack(clean, T=T, seed=5).clamp(0, 1)
    valid = torch.ones(T, 64, 64, dtype=torch.bool)
    valid[:, :10, :10] = False                     # a missing corner
    x = x * valid[:, None]
    y = D.denoise(x, valid, variant="test", model=(m, {"variant": "n2n"}, None), dev=CPU)
    assert y.shape == x.shape
    assert float(y[:, :, ~valid[0]].abs().max()) == 0.0           # invalid stays invalid (0)
    _, _, fp = D.footprint(x, valid)
    assert fp.sum() > 0
    dm = float((y - x)[:, :, fp].mean())
    assert abs(dm) < 0.01                                           # no brightening / darkening
    outside = valid[0] & ~fp
    assert torch.equal(y[:, :, outside], x[:, :, outside])          # outside the footprint: untouched
    # baselines go through the same entry
    q = D.denoise(x, valid, variant="quegan3")
    assert float(q[:, :, ~valid[0]].abs().max()) == 0.0


def test_noise_tools():
    gen = torch.Generator().manual_seed(0)
    # log-gamma noise has zero mean and the predicted std
    n = D.log_gamma_noise((200_000,), 4, gen)
    assert abs(float(n.mean())) < 1e-3
    assert abs(float(n.std()) - D.enl_noise_sigma(4)) < 2e-3
    assert abs(D.enl_from_sigma(D.enl_noise_sigma(10)) - 10) < 0.05
    # correlated noise reproduces a target lag-1 correlation
    lag = torch.arange(-5, 6).abs().float()
    acf = 0.5 ** (lag[:, None] + lag[None])        # separable exponential: a valid (positive) spectrum
    z = D.correlated_noise((128, 128), acf, gen)
    a = D.acf2d(z, torch.ones(128, 128, dtype=torch.bool), 3)
    assert abs(float(a[3, 4]) - 0.5) < 0.08
    # erf edge fit recovers the width
    s = torch.arange(-8, 8.01, 0.25)
    prof = 0.1 + 0.6 * 0.5 * (1 + torch.erf((s - 0.7) / (1.5 * math.sqrt(2))))
    s0, w, a_, c_ = D.logistic_edge_fit(prof[None], s)
    assert abs(float(s0) - 0.7) < 0.11 and abs(float(w) - 1.5) < 0.15
