"""Calibrated-data detector (scripts/detect/calibrated.py) on a synthetic gamma-naught scene.
Run: .venv/bin/python -m pytest tests/test_calibrated.py -q
"""
import math

import pytest
import torch

from scripts.detect import calibrated as cal
from scripts.detect import ops
from scripts.detect import stats_change as sc


def test_db_conversion_and_power_domain_filter():
    x = torch.tensor([1.0, 0.1, 0.01, 1e-3])
    assert torch.allclose(ops.to_loglike(x, "gamma0"), torch.tensor([0.0, -10.0, -20.0, -30.0]), atol=1e-5)
    # gamma speckle: averaging in POWER is unbiased, averaging in dB is biased by (10/ln10)(psi(L) - ln L)
    g = torch.Generator().manual_seed(1)
    for L in (4.0, 16.0):
        I = torch._standard_gamma(torch.full((400_000,), L, dtype=torch.float64), generator=g) / L * 0.01
        assert abs(10 * math.log10(float(I.mean())) - (-20.0)) < 0.02
        assert abs(float(cal.db(I).mean()) - (-20.0 + cal.db_bias(L))) < 0.02
        assert abs(float(cal.db(I).std()) - cal.db_std(L)) < 0.02
    # ops.box_mean on linear power keeps a uniform field's mean and ignores invalid pixels
    f = torch.full((1, 1, 20, 20), 0.05)
    m = torch.ones(1, 20, 20, dtype=torch.bool)
    m[0, :, :3] = False
    out = ops.box_mean(f, m, 5)
    assert torch.allclose(out[0, 0][m[0]], torch.tensor(0.05), atol=1e-7)


def test_synthetic_scene_shapes_and_levels():
    x, valid, tr = cal.synth_scene(enl=16.0)
    T, C, H, W = x.shape
    assert C == 2 and valid.shape == (T, H, W) and torch.isnan(x[:, :, tr["nodata"]]).all()
    lake = tr["water"].all(0) & valid.all(0)
    assert abs(10 * math.log10(float(x[0, 0][lake].double().mean())) - cal.CLASSES["water"][0]) < 0.3
    assert tr["water"][-1].sum() > 3 * tr["water"][0].sum()


@pytest.fixture(scope="module")
def synth():
    return cal.synthetic_check(enls=(4, 16, 64), verbose=False)


def test_water_detected_at_every_enl(synth):
    for enl, r in synth.items():
        assert r["cal"]["water"]["iou"] > 0.98, (enl, r["cal"]["water"])
        # the untested gamma0 knobs (box 7 + opening 3) lose a shoreline band; the calibrated chain does not
        assert r["ops_untested"]["water"]["iou"] < r["cal"]["water"]["iou"]
        # rough water (-14 dB HH) is NOT calm water: honest miss, about half the lake on that date
        assert r["cal"]["water_rough_date_recall"] < 0.8


def test_flooded_vegetation_db_rule(synth):
    for enl in ("16", "64"):
        fv = synth[enl]["cal"]["fv"]
        assert fv["recall"] > 0.95 and fv["precision"] > 0.8, (enl, fv)
    # ENL 4 at a 3x3 window: dry-land noise of HH-HV ~1.3 dB, 3 sigma ~ the 4-5 dB signal -> low recall, no false alarms
    assert synth["4"]["cal"]["fv"]["precision"] > 0.95


def test_gamma_null_is_calibrated(synth):
    """Omnibus test with the THEORETICAL gamma null and the true ENL: false-alarm rate = nominal level."""
    for enl, r in synth.items():
        fa = r["stats"]["fa_stable"]
        for a in ("0.1", "0.05", "0.01"):
            assert abs(fa[a] - float(a)) < 0.25 * float(a) + 0.0015, (enl, a, fa[a])
        assert r["stats"]["bh_fdr"] < 0.012, (enl, r["stats"]["bh_fdr"])
    assert synth["16"]["stats"]["bh_power"] > 0.95


def test_enl_estimators(synth):
    for enl, r in synth.items():
        # temporal ENL on stable land is unbiased; spatial ENL is pulled down by static texture
        assert abs(r["stats"]["enl_land_temporal"] / float(enl) - 1) < 0.1, (enl, r["stats"])
        assert abs(r["stats"]["enl_water_spatial"] / float(enl) - 1) < 0.1, (enl, r["stats"])
        assert r["stats"]["enl_land_spatial"] < r["stats"]["enl_land_temporal"]


def test_refine_shore_restores_band():
    w = torch.zeros(1, 30, 30, dtype=torch.bool)
    w[0, 10:20, 10:20] = True
    raw = torch.full((1, 30, 30), -8.0)
    raw[0, 9:21, 9:21] = -24.0   # true water is one pixel larger on each side
    raw[0, 2, 2] = -24.0         # isolated dark pixel far away: not added
    out = cal.refine_shore(w, raw, torch.ones(30, 30, dtype=torch.bool), -16.0, px=2)
    assert int(out.sum()) == 144 and not out[0, 2, 2]


def test_cond_quantile_patch_restored():
    orig = cal.floodveg.cond_quantile
    with cal._db_cond_quantile():
        assert cal.floodveg.cond_quantile is not orig
    assert cal.floodveg.cond_quantile is orig


def test_pair_test_exact_matches_lr():
    g = torch.Generator().manual_seed(3)
    I1 = torch._standard_gamma(torch.full((20000,), 16.0, dtype=torch.float64), generator=g) / 16
    I2 = torch._standard_gamma(torch.full((20000,), 16.0, dtype=torch.float64), generator=g) / 16
    p = sc.pair_pvalue_exact(I1, I2, 16.0)
    assert abs(float((p < 0.01).double().mean()) - 0.01) < 0.003


def test_run_pipeline_gamma0_dropin():
    """The run.py drop-in returns ops.run_pipeline's keys plus fv_state, and finds the synthetic water."""
    x, valid, tr = cal.synth_scene(enl=16.0, seed=2)
    kn = cal.knobs_for(80.0, water_hh_strong=-20.0, water_hh_weak=-17.0, water_hv_max=-24.0, refine_px=2)
    out = cal.run_pipeline_gamma0(x, valid, 80.0, kn=kn)
    for k in ("L", "valid", "water", "flooded_veg", "fv_state", "wind_suspect", "change_first", "change_date",
              "classes", "knobs"):
        assert k in out, k
    v = out["valid"]
    calm = [t for t in range(x.shape[0]) if t != 2]
    acc = cal._acc(out["water"] & v, tr["water"] & v, calm)
    assert acc["iou"] > 0.97, acc
    assert out["flooded_veg"].shape == out["water"].shape


def test_hist_valley_and_seeded_equiv():
    g = torch.Generator().manual_seed(5)
    v = torch.cat([torch.randn(20000, generator=g) * 1.0 - 19.5, torch.randn(60000, generator=g) * 2.5 - 8.0])
    t = cal.hist_valley(v)
    assert t is not None and -17.5 < t < -13.0, t
    assert cal.hist_valley(torch.randn(50000, generator=g) * 2 - 8) is None
    diag = {"mode": {"w": [1.0, 0.0]}, "chan_median": [-8.0, -16.0], "chan_iqr": [5.0, 6.0]}
    th = {"a_seed": -2.0, "b_land": 0.0, "s_strong": 0.1, "s_weak": 0.25}
    e = cal.seeded_equiv_db(diag, th)
    assert abs(e["weak"] - (-8.0 + 5.0 * -1.5)) < 1e-9 and e["strong"] < e["weak"]
