import numpy as np
from scripts.detect import quake as q


def test_phase_to_cm_one_cycle_is_half_wavelength():
    lam = 0.2420
    assert abs(q.phase_to_los_cm(2 * np.pi, lam) + 12.1) < 1e-6   # 2pi = 12.1 cm, away from satellite -> negative
    assert abs(q.phase_to_los_cm(-2 * np.pi, lam) - 12.1) < 1e-6
    assert q.phase_to_los_cm(0.0, lam) == 0.0


def test_rewrap_interval():
    d = np.array([0.0, 2.5, 5.0, 7.5, 10.0, -2.5])
    w = q.rewrap(d, 10.0)
    assert np.allclose(w, [0, 0.25, 0.5, 0.75, 0.0, 0.75])


def _grid():
    y, x = np.mgrid[0:200, 0:200] * 1000.0
    return x, y


def test_ramp_removal_keeps_central_signal():
    x, y = _grid()
    ramp = 3e-5 * x - 2e-5 * y + 1.5
    sig = 8.0 * np.exp(-((x - 1e5) ** 2 + (y - 1e5) ** 2) / (2 * 15e3 ** 2))
    z = ramp + sig + np.random.default_rng(0).normal(0, 0.1, x.shape)
    far = ~q.disc_mask(x, y, 1e5, 1e5, 50e3)
    res, _ = q.remove_ramp_far(z, x, y, far)
    assert abs(res.max() - 8.0) < 0.4          # signal survives
    assert abs(np.median(res[far])) < 0.05     # far field flat
    # naive fit over everything shrinks nothing here but must differ from the far-field fit slightly
    assert np.std(res[far]) < 0.15


def test_ramp_far_ignores_nan_and_quadric():
    x, y = _grid()
    z = 1e-9 * (x - 1e5) ** 2 + 2e-5 * x
    z[:20] = np.nan
    res, c = q.remove_ramp_far(z, x, y, np.ones_like(z, bool), order=2)
    assert np.nanmax(np.abs(res)) < 1e-3


def test_largest_component():
    cc = np.zeros((10, 10), np.uint16)
    cc[:5] = 1
    cc[5:7] = 2
    cc[7:] = 65535
    m = q.largest_component(cc, np.ones_like(cc, bool))
    assert m[:5].all() and not m[5:].any()
