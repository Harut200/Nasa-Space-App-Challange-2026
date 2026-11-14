"""Synthetic checks for the level -> exceedance -> occurrence method (no files, no network, CPU, fast)."""
import datetime as dt

import numpy as np
import torch

from scripts.detect import hydro


def _basin(H=40, W=40, seed=0):
    """Bowl-shaped floodplain: height grows with distance from the centre, plus small noise."""
    g = torch.Generator().manual_seed(seed)
    yy, xx = torch.meshgrid(torch.arange(H), torch.arange(W), indexing="ij")
    r = ((yy - H / 2) ** 2 + (xx - W / 2) ** 2).double().sqrt()
    return 1.0 + 0.4 * r + 0.01 * torch.rand(H, W, generator=g, dtype=torch.float64)


def _levels(n_years=12, seed=1):
    """Daily lake level: low in the dry season (Nov-May), high in Jun-Oct, with year-to-year noise."""
    rng = np.random.default_rng(seed)
    t, h = [], []
    for y in range(2000, 2000 + n_years):
        amp = 3.0 + rng.normal(0, 0.5)
        for d in range(365):
            day = dt.datetime(y, 1, 1) + dt.timedelta(days=d)
            t.append((day - hydro.EPOCH).total_seconds() / 86400)
            h.append(2.0 + amp * (1 + np.sin(2 * np.pi * (d - 180) / 365 + np.pi / 2)) + rng.normal(0, 0.05))
    return hydro.daily_series(np.array(t), np.array(h))


def _observe(z, series, months=None):
    """Occurrence (%) by 'observing' the basin every day (optionally only in some calendar months),
    pooling days within each observed month and averaging months, as JRC does."""
    h, mo = series["h"], series["month"]
    use = range(1, 13) if months is None else months
    occ = torch.zeros_like(z)
    for m in use:
        hm = h[mo == m]
        occ += (z[None] <= hm[:, None, None]).double().mean(0)
    return 100 * occ / len(list(use))


def test_occurrence_reproduces_height_order():
    z, s = _basin(), _levels()
    occ = _observe(z, s)
    rho = np.corrcoef(np.argsort(np.argsort(occ.flatten().numpy())), np.argsort(np.argsort(-z.flatten().numpy())))[0, 1]
    assert rho > 0.98
    # exceedance of each pixel's own height is its occurrence (ideal sampling, months balanced)
    e = hydro.exceedance(s, z.flatten())
    assert torch.allclose(100 * e, occ.flatten(), atol=1.0)


def test_expected_extent_recovers_true_set_and_dry_season_bias():
    z, s = _basin(), _levels()
    occ = _observe(z, s)
    for h in (3.0, 5.0, 7.0):
        truth = z <= h
        exp = hydro.expected_extent(occ, hydro.exceedance(s, h))[0]
        assert (exp == truth).float().mean() > 0.99
    # dry-season-only observation (Nov-May): occurrence is biased low ...
    dry = [1, 2, 3, 4, 5, 11, 12]
    occ_dry = _observe(z, s, months=dry)
    h = 6.0
    truth = z <= h
    naive = hydro.expected_extent(occ_dry, hydro.exceedance(s, h))[0]
    assert naive.sum() < 0.7 * truth.sum()                     # ... so the ideal mapping misses flooded pixels
    mw = [1.0 if m in dry else 0.0 for m in range(1, 13)]
    fixed = hydro.expected_extent(occ_dry, None, sampling={"series": s, "h": torch.tensor([h]), "month_weights": mw})[0]
    assert (fixed == truth).float().mean() > 0.98             # the sampling-weighted threshold recovers it


def test_level_for_exceedance_inverts():
    s = _levels()
    for e in (0.1, 0.5, 0.9):
        h = hydro.level_for_exceedance(s, e)
        assert abs(float(hydro.exceedance(s, h)[0]) - e) < 0.01


def test_occ_hist_fraction():
    # one 2x2 output cell from a 4x4 block of 30 m values
    arr = np.array([[0, 10, 50, 100]] * 4, dtype=np.uint8)
    hist = hydro.occ_hist_on_grid(arr, 0.0, 1.0, 0.25, (0.0, 0.0, 1.0, 1.0), 1, 1)
    assert int(hist.n_valid[0, 0]) == 16
    assert abs(float(hist.frac_ge(50)[0, 0, 0]) - 0.5) < 1e-6
    assert abs(float(hist.frac_ge(100)[0, 0, 0]) - 0.25) < 1e-6
    assert abs(float(hist.frac_ge(0)[0, 0, 0]) - 1.0) < 1e-6


def test_bathtub_respects_connectivity():
    dem = torch.full((20, 30), 3.0)
    dem[:, :5] = 0.0            # lake
    dem[:, 5:12] = 2.0          # low plain connected to the lake
    dem[:, 12:14] = 10.0        # ridge
    dem[:, 14:20] = 1.0         # pit behind the ridge, lower than the plain
    seed = torch.zeros(20, 30, dtype=torch.bool)
    seed[:, :5] = True
    w = hydro.bathtub(dem, 2.5, seed)
    assert w[:, :12].all()
    assert not w[:, 12:].any()  # the pit stays dry although it is below the level
    w2 = hydro.bathtub(dem, torch.tensor([2.5, 11.0]), seed)
    assert w2.shape == (2, 20, 30) and w2[1].all()


def test_calibration_recovers_known_threshold():
    g = torch.Generator().manual_seed(3)
    Lx = torch.rand(10, 50, 50, generator=g)
    true_thr = 0.17
    target = (Lx < true_thr).sum((1, 2)).double().numpy()
    grid = np.round(np.arange(0.05, 0.40, 0.01), 3)
    det = np.array([(Lx < t).sum((1, 2)).double().numpy() for t in grid])
    c = hydro.calibrate_threshold(grid, det, target)
    assert abs(c["best"] - true_thr) < 0.003
    c2 = hydro.calibrate_threshold(grid, det, target, fit=[0, 2, 4, 6, 8])
    assert abs(c2["best"] - true_thr) < 0.005
