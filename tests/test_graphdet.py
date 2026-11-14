"""Tests for scripts/detect/graphdet.py (graph and spectral detectors). CPU only, small synthetic data.

    .venv/bin/python -m pytest tests/test_graphdet.py -q
"""
import math

import numpy as np
import pytest
import torch

from scripts.detect import graphdet as G
from scripts.detect import ops

CPU = torch.device("cpu")


def test_laplacian_matches_hand_computed():
    # 1 x 3 path graph, 4-neighbours, features 0, 1, 3, sigma = 1:
    # w01 = exp(-1/2), w12 = exp(-4/2)
    f = torch.tensor([0.0, 1.0, 3.0]).reshape(1, 1, 1, 3)
    valid = torch.ones(1, 1, 3, dtype=torch.bool)
    w, _ = G.grid_weights(f, valid, G.OFFS4, sigma=1.0)
    Wm, idx = G.to_sparse(w[0], valid[0], G.OFFS4)
    a, b = math.exp(-0.5), math.exp(-2.0)
    L_hand = torch.tensor([[a, -a, 0.0], [-a, a + b, -b], [0.0, -b, b]], dtype=torch.float64)
    assert torch.allclose(G.dense_laplacian(Wm), L_hand, atol=1e-12)
    d = torch.tensor([a, a + b, b], dtype=torch.float64)
    Lsym_hand = torch.eye(3, dtype=torch.float64) - torch.diag(d.rsqrt()) @ (torch.diag(d) - L_hand) @ torch.diag(d.rsqrt())
    assert torch.allclose(G.dense_laplacian(Wm, normalized=True), Lsym_hand, atol=1e-12)
    # matrix-free grid operator gives the same L x
    x = torch.tensor([0.3, -1.0, 2.0]).reshape(1, 1, 3)
    assert torch.allclose(G.laplacian_apply(w, x, G.OFFS4).flatten().double(), L_hand @ x.flatten().double(), atol=1e-6)


def test_random_walker_matches_dense_solve():
    torch.manual_seed(0)
    H, W = 6, 7
    f = torch.rand(1, 2, H, W)
    valid = torch.ones(1, H, W, dtype=torch.bool)
    w, _ = G.grid_weights(f, valid, G.OFFS8)
    lab = torch.full((1, H, W), -1, dtype=torch.long)
    lab[0, 0, :3] = 0
    lab[0, -1, -3:] = 1
    prob, _, _ = G.random_walker(w.double(), lab, 2, G.OFFS8, tol=1e-12, max_iter=500)
    Wm, idx = G.to_sparse(w[0], valid[0], G.OFFS8)
    Lm = G.dense_laplacian(Wm)
    l = lab[0].flatten()
    u, s = (l < 0).nonzero()[:, 0], (l >= 0).nonzero()[:, 0]
    xs = (l[s] == 0).double()
    xu = torch.linalg.solve(Lm[u][:, u], -Lm[u][:, s] @ xs)
    assert torch.allclose(prob[0, 0].flatten()[u], xu, atol=1e-6)


def test_eigenvectors_match_scipy():
    sp = pytest.importorskip("scipy.sparse")
    spl = pytest.importorskip("scipy.sparse.linalg")
    torch.manual_seed(1)
    H, W = 12, 13
    f = torch.rand(1, 2, H, W)
    f[:, :, :, :6] += 1.0  # two regions
    valid = torch.ones(1, H, W, dtype=torch.bool)
    w, _ = G.grid_weights(f, valid, G.OFFS8)
    Wm, _ = G.to_sparse(w[0], valid[0], G.OFFS8)
    mv, d = G.sym_laplacian_matvec(Wm)
    vals, vecs, res = G.lanczos_smallest(mv, Wm.shape[0], 4, m=Wm.shape[0])
    Ls = G.dense_laplacian(Wm, normalized=True).numpy()
    ev, evec = spl.eigsh(sp.csr_matrix(Ls), k=4, which="SA", tol=1e-12)
    order = np.argsort(ev)
    assert np.allclose(vals.numpy(), ev[order], atol=1e-8)
    for i in range(4):
        a, b = vecs[:, i].numpy(), evec[:, order[i]]
        assert min(np.abs(a - b).max(), np.abs(a + b).max()) < 1e-5  # equal up to sign
    assert float(res.max()) < 1e-8


def _scene(seed=0, looks=8):
    """Speckled land (0.45), a lake (0.02) with a bright windy patch (0.25) in the middle, and a
    detached dark blob (0.02) far from the lake."""
    g = torch.Generator().manual_seed(seed)
    H = W = 96
    hh = torch.full((H, W), 0.45)
    hv = torch.full((H, W), 0.30)
    yy, xx = torch.meshgrid(torch.arange(H), torch.arange(W), indexing="ij")
    lake = (yy - 40) ** 2 + (xx - 40) ** 2 < 26 ** 2
    wind = (yy - 40) ** 2 + (xx - 40) ** 2 < 8 ** 2
    blob = (yy - 85) ** 2 + (xx - 85) ** 2 < 5 ** 2
    hh[lake] = 0.02; hv[lake] = 0.10
    hh[wind] = 0.25; hv[wind] = 0.20
    hh[blob] = 0.02; hv[blob] = 0.10
    n = torch.distributions.Gamma(torch.full((2, H, W), float(looks)), torch.full((2, H, W), float(looks))).sample() \
        if seed is None else torch.from_numpy(np.random.default_rng(seed).gamma(looks, 1 / looks, (2, H, W))).float()
    x = torch.stack([hh, hv]) * n
    L = ops.box_mean(x[None], torch.ones(1, H, W, dtype=torch.bool), 3)  # light smoothing as in the pipeline
    return L, lake, wind, blob


def test_graph_keeps_windy_lake_and_rejects_blob():
    # a 5-date season; wind brightens the lake centre on date 2 only
    Ls = []
    for t in range(5):
        L, lake, wind, blob = _scene(seed=t)
        if t != 2:  # calm date: the windy patch looks like the rest of the lake
            L = torch.where(wind[None, None], torch.tensor([0.02, 0.10])[None, :, None, None], L)
        Ls.append(L[0])
    L = torch.stack(Ls)
    valid = torch.ones(5, *lake.shape, dtype=torch.bool)
    g = G.refine_water(L, valid, dev=CPU, kn={"seed_min_px": 300})
    water = g["water"][2]
    thr = ops.water_masks(L, valid, ops.KNOBS["preview"])[2]  # per-pixel hysteresis threshold
    # graph: windy patch recovered (enclosed by confident water), blob rejected (no seed reaches it)
    assert water[wind].float().mean() > 0.95
    assert water[lake].float().mean() > 0.93  # shoreline ring blurred by the 3x3 smoothing
    assert water[blob].float().mean() < 0.05
    # per-pixel threshold: loses the windy patch and keeps the blob
    assert thr[wind].float().mean() < 0.5
    assert thr[blob].float().mean() > 0.5


def test_tv_cut_relaxation_beats_threshold_energy():
    L, lake, wind, blob = _scene(seed=3, looks=3)
    valid = torch.ones(1, *lake.shape, dtype=torch.bool)
    w, _ = G.grid_weights(L, valid, G.OFFS8)
    c = G.brightness_unary(L)
    u = G.tv_segment(c, w[:, :2], valid, 0.25, iters=400)
    e_cut = G.cut_energy(u > 0.5, c, w[:, :2], valid, 0.25)
    e_thr = G.cut_energy(c < 0, c, w[:, :2], valid, 0.25)  # pointwise minimiser of the unary alone
    assert float(e_cut) <= float(e_thr) + 1e-3


def test_structural_change_invariant_to_global_gain():
    torch.manual_seed(0)
    H = W = 64
    base = torch.rand(2, H, W) * 0.5 + 0.2
    base = ops.box_mean(base[None], torch.ones(1, H, W, dtype=torch.bool), 3)[0]
    B = base.clone()
    B[:, 20:30, 20:30] = 0.02  # a real change (flooded square)
    valid = torch.ones(2, H, W, dtype=torch.bool)
    L0 = torch.stack([base, B])
    L1 = torch.stack([base, B * 1.1 + 0.02])  # global gain + offset on date B
    s0 = G.structural_change(L0, valid, k=6, radius=6, stride=1)[0]
    s1 = G.structural_change(L1, valid, k=6, radius=6, stride=1)[0]
    assert torch.allclose(s0, s1, rtol=1e-4, atol=1e-5)
    # the plain difference detector is not invariant: the gain flags (almost) the whole image
    d0 = (L0[1] - L0[0]).norm(dim=0) > 0.05
    d1 = (L1[1] - L1[0]).norm(dim=0) > 0.05
    assert d1.float().mean() > d0.float().mean() + 0.3
    # and the real change scores higher than the unchanged background
    assert float(s0[22:28, 22:28].mean()) > 2 * float(s0[40:, 40:].median())


def test_heat_filter_preserves_constants_and_smooths():
    torch.manual_seed(0)
    H = W = 40
    x = torch.full((1, H, W), 0.5)
    valid = torch.ones(1, H, W, dtype=torch.bool)
    w, _ = G.grid_weights(x[:, None] + 0.01 * torch.rand(1, 1, H, W), valid, G.OFFS8)
    y = G.heat_filter(x, w, s=3.0)
    assert torch.allclose(y, x, atol=1e-5)  # exp(-s L_rw) 1 = 1
    noisy = x * torch.from_numpy(np.random.default_rng(0).gamma(4, 0.25, (1, H, W))).float()
    assert float(G.heat_filter(noisy, w, s=3.0).std()) < 0.7 * float(noisy.std())


def test_ls_periodogram_recovers_amplitude():
    t = torch.tensor([0, 12, 24, 36, 60, 72, 84, 96, 108, 120], dtype=torch.float64)
    y = (0.3 + 0.1 * torch.cos(2 * math.pi * t / 60 + 0.4))[:, None]
    amp, slope = G.ls_periodogram(y, t, [60.0])
    assert abs(float(amp[0, 0]) - 0.1) < 1e-6
