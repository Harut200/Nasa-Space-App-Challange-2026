"""Synthetic checks for scripts/detect/insar.py (CPU, no files, no network)."""
import math

import numpy as np
import torch

from scripts.detect import insar

LAM = 0.2420  # NISAR L-band wavelength for 1.239 GHz [m]


def _grid(H=60, W=80, px=80.0):
    yy, xx = torch.meshgrid(torch.arange(H, dtype=torch.float64), torch.arange(W, dtype=torch.float64), indexing="ij")
    return xx * px, -yy * px


def test_phase_to_los_value_and_sign():
    # one full fringe (2 pi) = half a wavelength of range change; positive phase = range increase
    # = motion away from the satellite = negative LOS displacement (toward-satellite positive)
    d = insar.phase_to_los(2 * math.pi, LAM)
    assert abs(d + LAM / 2) < 1e-12
    assert insar.phase_to_los(-1.0, LAM) > 0
    # 10 mm of subsidence (away) seen as phase: phi = 4 pi * 0.010 / lambda
    phi = 4 * math.pi * 0.010 / LAM
    assert abs(insar.phase_to_los(phi, LAM) + 0.010) < 1e-12


def test_ramp_removal_recovers_bowl():
    x, y = _grid()
    g = torch.Generator().manual_seed(0)
    cx, cy = x.mean(), y.mean()
    r2 = ((x - cx) ** 2 + (y - cy) ** 2) / (800.0 ** 2)
    bowl = -3.0 * torch.exp(-r2)                        # signal in the middle
    ramp = 2.0 + 4e-4 * x - 3e-4 * y                    # orbit / ionosphere tilt (several rad)
    noise = 0.05 * torch.randn(x.shape, generator=g, dtype=torch.float64)
    z = bowl + ramp + noise
    stable = r2 > 9.0                                   # fit only away from the bowl
    res, _ = insar.deramp(z, x, y, stable, order=1)
    err = (res - bowl)
    assert err[stable].abs().mean() < 0.1
    assert (res[~stable] - bowl[~stable]).abs().max() < 0.3
    # without excluding the bowl the fit would be biased; with it the bowl depth is recovered
    assert abs(float(res.min()) - (-3.0)) < 0.25


def test_height_regressor_removes_stratified_delay():
    x, y = _grid()
    h = 800 + 0.02 * x + 300 * torch.sin(y / 900)       # synthetic topography [m]
    z = 0.002 * h + 1e-4 * x                            # 2 rad per km of height + tilt
    res, coef = insar.deramp(z, x, y, torch.ones_like(z, dtype=torch.bool), order=1, extra=[h])
    assert res.abs().max() < 1e-6


def test_stacking_noise_falls_as_one_over_sqrt_n():
    g = torch.Generator().manual_seed(1)
    sig, dt, v = 5.0, 12.0, 0.1                          # mm noise per pair, days, mm/day
    stds = {}
    for N in (1, 4, 16):
        d = v * dt + sig * torch.randn(N, 4000, 1, generator=g, dtype=torch.float64)
        rate, T = insar.stack_rate(d, [dt] * N)
        stds[N] = float(rate.std())
        assert abs(float(rate.mean()) - v) < 4 * sig / dt / math.sqrt(N * 4000) + 1e-9
    assert abs(stds[1] / stds[4] - 2.0) < 0.2
    assert abs(stds[1] / stds[16] - 4.0) < 0.4


def test_chain_and_linear_rate():
    t = torch.arange(0, 120.1, 12.0, dtype=torch.float64)
    x = 0.05 * t                                        # 0.05 mm/day
    d = (x[1:] - x[:-1]).view(-1, 1, 1)
    s = insar.chain_timeseries(d, t)
    slope, sig = insar.linear_rate(s, t)
    assert abs(float(slope) - 0.05) < 1e-12 and float(sig) < 1e-9


def test_coherence_change_separates_water_from_harvest():
    K, H, W = 8, 20, 30
    g = torch.Generator().manual_seed(2)
    coh = 0.75 + 0.03 * torch.randn(K, H, W, generator=g, dtype=torch.float64)
    coh[:, :, :5] = 0.15 + 0.03 * torch.rand(K, H, 5, generator=g, dtype=torch.float64)   # water: always low
    coh[3, 5:10, 10:20] = 0.2                                                           # a field harvested in pair 3
    out = insar.coherence_change(coh)
    assert bool(out["perm"][:, :5].all()) and not bool(out["perm"][:, 5:].any())
    assert bool(out["lost"][3, 5:10, 10:20].all())
    assert int(out["lost"][:, :, :5].sum()) == 0                                         # water is not "became incoherent"
    assert int(out["n_lost"][~torch.zeros(H, W, dtype=torch.bool)].sum()) == 50
    assert int(out["lost"].sum()) == 50


def test_stability_flags_single_pair_signal():
    K, H, W = 9, 30, 30
    g = torch.Generator().manual_seed(3)
    dt = [12.0] * K
    target = torch.zeros(H, W, dtype=torch.bool); target[10:20, 10:20] = True
    ref1 = torch.zeros(H, W, dtype=torch.bool); ref1[:5, :5] = True
    ref2 = torch.zeros(H, W, dtype=torch.bool); ref2[-5:, -5:] = True
    noise = 0.5 * torch.randn(K, H, W, generator=g, dtype=torch.float64)
    # (a) steady subsidence of 2 mm per pair in the target: robust
    steady = noise.clone(); steady[:, 10:20, 10:20] -= 2.0
    a = insar.signal_stability(steady, dt, target, [ref1, ref2])
    assert a["robust"] and a["rate"] < 0
    # (b) the same total in ONE pair only (an unwrapping error or atmospheric blob): flagged
    blob = noise.clone(); blob[4, 10:20, 10:20] -= 18.0
    b = insar.signal_stability(blob, dt, target, [ref1, ref2])
    assert not b["robust"] and b["max_pair_share"] > 0.6
    # (c) a "signal" that is really a moving reference: changing the reference flips the sign
    refmove = noise.clone(); refmove[:, :5, :5] += 2.0
    c = insar.signal_stability(refmove, dt, target, [ref1, ref2])
    assert not c["robust"]


def test_noise_vs_distance_grows_with_distance():
    x, y = _grid(80, 80)
    d = 1e-3 * x + 0.1 * torch.randn(x.shape, generator=torch.Generator().manual_seed(4), dtype=torch.float64)
    out = insar.noise_vs_distance(d, x, y, torch.ones_like(d, dtype=torch.bool), (0.0, 0.0),
                                  [0, 1500, 3000, 6000])
    assert out[0]["rms"] < out[-1]["rms"]


def test_reduced_transfer_format_roundtrip(tmp_path):
    # remote_products.reduce_npz (server) -> insar._decode (laptop): coherence to 0.002, phase to < 0.4 mm
    import sys, types
    sys.modules.setdefault("remote_extract", types.SimpleNamespace(ASF=0, Remote=0, RangeFile=0, bbox_to_proj=0,
                                                                     get_token=0, log=print))
    sys.path.insert(0, "scripts/ingest")
    import remote_products
    rng = np.random.default_rng(5)
    coh = rng.random((200, 220)).astype(np.float32); coh[0, :3] = np.nan
    phi = (rng.normal(0, 8, (200, 220)) - 20).astype(np.float32); phi[5, 5] = np.nan
    np.savez_compressed(tmp_path / "a.npz", coherenceMagnitude=coh, unwrappedPhase=phi,
                        connectedComponents=np.ones((200, 220), np.uint16))
    remote_products.reduce_npz(str(tmp_path / "a.npz"), str(tmp_path / "b.npz"))
    z = np.load(tmp_path / "b.npz")
    c2, p2 = insar._decode(z, "coherenceMagnitude"), insar._decode(z, "unwrappedPhase")
    assert np.isnan(c2[0, :3]).all() and np.nanmax(np.abs(c2 - coh)) <= 0.0021
    assert np.isnan(p2[5, 5])
    err_mm = 1e3 * LAM / (4 * math.pi) * np.nanmax(np.abs(p2 - phi))
    assert err_mm < 0.4
    assert z["connectedComponents"].dtype == np.uint8


def test_reduced_v2_format_roundtrip(tmp_path):
    # reduce_npz2 (what is actually transferred): int16 phase masked by coherence, x5 iono screen, x2 control grid
    import sys, types
    sys.modules.setdefault("remote_extract", types.SimpleNamespace(ASF=0, Remote=0, RangeFile=0, bbox_to_proj=0,
                                                                     get_token=0, log=print))
    sys.path.insert(0, "scripts/ingest")
    import remote_products
    rng = np.random.default_rng(6)
    H, W = 100, 120
    coh = rng.uniform(0.1, 1, (H, W)).astype(np.float32)
    phi = (rng.normal(0, 5, (H, W)) - 12).astype(np.float32)
    yy, xx = np.mgrid[0:H, 0:W]
    iono = (-20 + 0.01 * xx + 0.02 * yy).astype(np.float32)          # smooth screen
    cc = np.ones((H, W), np.uint16); cc[:, :3] = 0
    np.savez_compressed(tmp_path / "a.npz", coherenceMagnitude=coh, unwrappedPhase=phi, ionospherePhaseScreen=iono,
                        connectedComponents=cc, x=np.arange(W) * 80.0, y=-np.arange(H) * 80.0, epsg=np.asarray(32638))
    remote_products.reduce_npz2(str(tmp_path / "a.npz"), str(tmp_path / "b.npz"), "other")
    z = np.load(tmp_path / "b.npz")
    p2 = insar._decode(z, "unwrappedPhase", (H, W))
    keep = (coh >= 0.2) & (cc != 0)
    assert np.isnan(p2[~keep]).all() and np.isfinite(p2[keep]).all()
    assert 1e3 * LAM / (4 * math.pi) * np.nanmax(np.abs(p2 - phi)) < 0.4           # < 0.4 mm LOS
    i2 = insar._decode(z, "ionospherePhaseScreen", (H, W))
    assert i2.shape == (H, W) and np.abs(i2 - iono)[5:-5, 5:-5].max() < 0.05      # rad, interior
    # control-area mode: x2 block average, components subsampled and cropped to the phase grid
    remote_products.reduce_npz2(str(tmp_path / "a.npz"), str(tmp_path / "c.npz"), "sevan", fac=2)
    z = np.load(tmp_path / "c.npz")
    shape = (len(z["y"]), len(z["x"]))
    assert shape == (50, 60) and insar._decode(z, "connectedComponents", shape).shape == shape
