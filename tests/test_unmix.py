"""Fast CPU tests for scripts/detect/unmix.py (physics autoencoder)."""
import math

import torch

from scripts.detect import unmix as U

CPU = torch.device("cpu")


def _model(**over):
    cfg = U.cfg_of("full", **over)
    return U.PhysAE(cfg, {"s": 3}), cfg


def test_decoder_reproduces_hand_computed_mixture():
    m, cfg = _model()
    with torch.no_grad():
        m.scene("s").g_raw.zero_()                       # gain 0 dB
    db = cfg["db"]
    bl = torch.tensor([0.6, 0.4]).view(1, 2, 1, 1)
    f = torch.tensor([0.5, 0.2, 0.3]).view(1, 1, 3, 1, 1).expand(1, 3, 3, 1, 1)
    out = U.decode(m.phys, m.scene("s"), bl, f, cfg)[0, 0, :, 0, 0]
    bw = m.phys.water_b().detach()
    lg = m.phys.log10_g().detach()
    lr = m.scene("s").log10_r().detach()[0]
    for c in range(2):
        Il = 10 ** ((float(bl[0, c]) - 1) * db / 10)
        Iw = 10 ** ((float(bw[c]) - 1) * db / 10) * 10 ** float(lr[c])
        I = 0.5 * Iw + 0.2 * Il * 10 ** float(lg[c]) + 0.3 * Il
        b = 1 + 10 / db * math.log10(I)
        assert abs(float(out[c]) - b) < 2e-3, (c, float(out[c]), b)


def test_fractions_nonnegative_and_sum_to_one():
    m, cfg = _model(base=8)
    g = torch.Generator().manual_seed(0)
    b = torch.rand(2, 3, 2, 16, 16, generator=g)
    v = torch.ones(2, 3, 16, 16, dtype=torch.bool)
    v[0, 1, :4] = False
    s = torch.stack([U.pixel_stats(b[i], v[i]) for i in range(2)])
    f, bl = m.enc(b, v, s)
    assert (f >= 0).all()
    assert torch.allclose(f.sum(2), torch.ones_like(f.sum(2)), atol=1e-5)
    lf = torch.tensor(cfg["land_floor"]).view(1, 2, 1, 1)
    assert (bl[:, :2] >= lf - 1e-6).all() and (bl[:, :2] <= 1.05 + 1e-6).all()
    assert (bl[:, 2:] >= 0).all() and (bl[:, 2:] <= U.Q_MAX).all()


def test_power_mixing_differs_from_log_mixing():
    """Half water + half land in POWER is only ~3 dB darker than the land (b drops by 10*log10(2)/D);
    mixing the log-like brightness linearly would put it half-way to the water level."""
    m, cfg = _model()
    with torch.no_grad():
        m.scene("s").g_raw.zero_()
        m.scene("s").r_raw.fill_(-20.0)                  # no roughness
    bl = torch.tensor([0.6, 0.6]).view(1, 2, 1, 1)
    f = torch.tensor([0.5, 0.0, 0.5]).view(1, 1, 3, 1, 1).expand(1, 3, 3, 1, 1)
    bp = float(U.decode(m.phys, m.scene("s"), bl, f, cfg)[0, 0, 0])
    bg = float(U.decode(m.phys, m.scene("s"), bl, f, dict(cfg, mix="log"))[0, 0, 0])
    assert abs(bp - (0.6 - 10 * math.log10(2) / cfg["db"])) < 5e-3
    bw = float(m.phys.water_b()[0])
    assert abs(bg - 0.5 * (0.6 + max(bw, U.FLOOR))) < 0.03 or abs(bg - U.soft_clip(torch.tensor(0.5 * (0.6 + bw))).item()) < 1e-3
    assert bp - bg > 0.2


def _small_run(seed=0):
    torch.manual_seed(0)
    s = U.synth_scene(H=48, W=48, T=6, seed=seed, wind_date=3, looks=20)
    cfg = U.cfg_of("full", base=8, steps=400, batch=6, patch=32, minutes=1.0, lr=4e-3)
    d = U.AreaData(s, cfg["patch"])
    m, _ = U.train_model([d], cfg, dev=CPU, verbose=False)
    f, bl, bh = U.infer(m, d, tile=48, margin=0, dev=CPU)
    return s, d, m, f, bl


def test_short_training_beats_per_pixel_fraction_and_flags_constant_dark_pixels():
    s, d, m, f, bl = _small_run()
    truth = s["f"][:, 0]
    an = U.analytic_fraction(s["b"], s["valid"], m.cfg)
    e_net = U.rmse(f[:, 0], truth)
    e_an = U.rmse(an, truth)
    assert e_net < e_an, (e_net, e_an)   # (the hard threshold needs the full 4-minute run: docs/eda/unmix.md)
    # A pixel dark on every date is flagged prior-driven: its water fraction comes from the priors, not the data.
    # (Whether the trained network then calls it dark LAND is NOT asserted here: in this 48 x 48, few-second run
    # it calls the dark patch ~0.7-0.9 water. Only the full 128 x 128, 3-minute study gets it right (0.002),
    # see test_synthetic_study_dark_land_is_land and docs/eda/unmix.md -- this is the identifiability problem.)
    dark = s["masks"]["dark_land"]
    pdm = U.prior_driven(d.stats, d.valid, m.cfg)
    assert float(pdm[dark].float().mean()) > 0.5


def test_synthetic_study_dark_land_is_land():
    """Regression check on the saved full synthetic study (skipped if it has not been run)."""
    import json
    import pytest
    p = U.DOCS / "results.json"
    if not p.exists() or "synthetic" not in json.loads(p.read_text()):
        pytest.skip("synthetic study not run")
    m = json.loads(p.read_text())["synthetic"]["methods"]
    ae, hard = m["physics_ae"], m["hard_threshold"]
    assert ae["water_mean_dark_land"] < 0.05
    assert ae["water_rmse"] < hard["water_rmse"]


def test_terrain_prior_moves_water_to_land():
    f = torch.tensor([0.6, 0.1, 0.3]).view(1, 3, 1, 1).repeat(2, 1, 2, 2)
    ok = torch.tensor([[True, False], [True, True]])
    g = U.apply_terrain(f, ok)
    assert float(g[0, 0, 0, 1]) == 0.0 and float(g[0, 1, 0, 1]) == 0.0 and abs(float(g[0, 2, 0, 1]) - 1.0) < 1e-6
    assert torch.allclose(g.sum(1), torch.ones(2, 2, 2))


def test_noise_floor_map_follows_a_swath_gradient():
    """Open water whose HV floor rises from 0.02 (west) to 0.22 (east): the floor map follows it; land is
    never used to measure it; a scene without sure water gets the flat default."""
    T, H, W = 6, 64, 96
    g = torch.Generator().manual_seed(0)
    b = torch.zeros(T, 2, H, W)
    ramp = torch.linspace(0.02, 0.22, W).view(1, W).expand(H, W)
    b[:, 0] = 0.01 + 0.005 * torch.rand(T, H, W, generator=g)
    b[:, 1] = ramp + 0.01 * torch.randn(T, H, W, generator=g)
    b[:, :, :12] = 0.6                      # a strip of land on top
    v = torch.ones(T, H, W, dtype=torch.bool)
    fl = U.noise_floor_map(b, v)
    assert abs(float(fl[1, 40, 10]) - (float(ramp[0, 10]) - 0.02)) < 0.03
    assert abs(float(fl[1, 40, 85]) - (float(ramp[0, 85]) - 0.02)) < 0.03
    assert float(fl[0, 5, 50]) < 0.05       # land strip: filled from the water, not from the land
    dry = torch.full((T, 2, H, W), 0.5)
    assert torch.all(U.noise_floor_map(dry, v) == U.FLOOR_DEFAULT)


def test_impute_bad_channel_uses_other_dates():
    b = torch.rand(4, 2, 5, 5)
    v = torch.ones(4, 5, 5, dtype=torch.bool)
    U_q = U.qc_bad_channels
    try:
        U.qc_bad_channels = lambda area, dates: [(2, 1)]
        out = U.impute_bad_channels("x", b, v, ["a", "b", "c", "d"])
    finally:
        U.qc_bad_channels = U_q
    assert torch.allclose(out[2, 1], b[[0, 1, 3], 1].median(0).values)
    assert torch.equal(out[2, 0], b[2, 0]) and torch.equal(out[0], b[0])
