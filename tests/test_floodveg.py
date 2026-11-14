"""Synthetic scene for scripts.detect.floodveg.detect_flooded_veg.

Layout (64 x 64 px, 8 dates, log-like brightness 0..1, Gaussian "speckle" sd 0.02 + per-date offset drift):
  * dry forest everywhere else (HH 0.85, HV 0.82)
  * lake on the left, growing one column per date in the upper rows
  * ring (rows 0-44, cols 18-22): dry (0.60/0.55) -> double bounce dates 2-4 (0.78/0.40) -> open water dates 5-7
  * already flooded shrub (rows 50-59, cols 11-12) on the lake edge, double bounce on ALL dates incl. date 0
  * town (rows 50-59, cols 26-30): permanently bright HH, high HH-HV, 6-8 px from water
  * cropland (rows 25-40, cols 25-34) next to the ring: brightness drifting up, HV faster than HH
"""
import torch

from scripts.detect import ops
from scripts.detect.floodveg import detect_flooded_veg


def scene(seed=0):
    g = torch.Generator().manual_seed(seed)
    T, H, W = 8, 64, 64
    hh = torch.full((T, H, W), 0.85)
    hv = torch.full((T, H, W), 0.82)
    for t in range(T):
        hh[t, :45, : 11 + t] = 0.02; hv[t, :45, : 11 + t] = 0.01
        hh[t, 45:, :11] = 0.02; hv[t, 45:, :11] = 0.01
        if t < 2:
            hh[t, :45, 18:23], hv[t, :45, 18:23] = 0.60, 0.55
        elif t < 5:
            hh[t, :45, 18:23], hv[t, :45, 18:23] = 0.78, 0.40
        else:
            hh[t, :45, 18:23], hv[t, :45, 18:23] = 0.03, 0.02
        hh[t, 50:60, 11:13], hv[t, 50:60, 11:13] = 0.80, 0.35
        hh[t, 50:60, 26:31], hv[t, 50:60, 26:31] = 0.92, 0.45
        hh[t, 25:41, 25:35], hv[t, 25:41, 25:35] = 0.45 + 0.02 * t, 0.30 + 0.03 * t
    drift = torch.tensor([0.0, 0.02, -0.02, 0.03, 0.0, -0.03, 0.02, 0.01])[:, None, None]
    hh = (hh + drift + 0.02 * torch.randn(T, H, W, generator=g)).clamp(0, 1)
    hv = (hv + drift + 0.02 * torch.randn(T, H, W, generator=g)).clamp(0, 1)
    L = torch.stack([hh, hv], 1)
    valid = torch.ones(T, H, W, dtype=torch.bool)
    water = ops.water_masks(L, valid, ops.KNOBS["preview"])
    return L, valid, water


def masks():
    H = W = 64
    z = lambda: torch.zeros(H, W, dtype=torch.bool)  # noqa: E731
    ring, patch, town, crop = z(), z(), z(), z()
    ring[:45, 18:23] = True
    patch[50:60, 11:13] = True
    town[50:60, 26:31] = True
    crop[25:41, 25:35] = True
    return ring, patch, town, crop


def test_ring_patch_found_town_crop_not():
    L, valid, water = scene()
    out = detect_flooded_veg(L, valid, water)
    fv, st = out["flooded_veg"], out["fv_state"]
    ring, patch, town, crop = masks()
    for t in (2, 3, 4):  # double-bounce phase of the ring
        assert fv[t][ring].float().mean() > 0.7, (t, fv[t][ring].float().mean())
        assert (st[t][ring & fv[t]] == 1).float().mean() > 0.9
    for t in (0, 1):  # ring still dry
        assert fv[t][ring].float().mean() < 0.05
    for t in (5, 6, 7):  # ring is open water now, not flooded vegetation
        assert fv[t][ring].float().mean() < 0.05
    assert fv[0][patch].float().mean() > 0.8  # already flooded on the first date
    assert int(out["first_fv_date"][patch].float().median()) == 0
    assert fv[:, town].float().mean() < 0.02
    assert fv[:, crop].float().mean() < 0.02


def test_area_tolerance_and_shapes():
    L, valid, water = scene(1)
    out = detect_flooded_veg(L, valid, water)
    ring, patch, _, _ = masks()
    truth = float(ring.sum() + patch.sum())
    got = float(out["flooded_veg"][3].sum())
    assert abs(got - truth) / truth < 0.25, (got, truth)
    T, H, W = water.shape
    assert out["fv_score"].shape == (T, H, W) and out["fv_state"].dtype == torch.uint8
    assert out["ever_flooded_veg"].shape == (H, W) and out["first_fv_date"].shape == (H, W)
    assert 0 <= float(out["fv_score"].min()) and float(out["fv_score"].max()) <= 1


def test_global_offset_invariance():
    """A per-date brightness offset applied to both channels must barely change the result."""
    L, valid, water = scene(2)
    a = detect_flooded_veg(L, valid, water)["fv_px"].float()
    off = torch.tensor([0.0, 0.03, -0.03, 0.02, 0.04, -0.02, 0.0, 0.03])[:, None, None, None]
    b = detect_flooded_veg((L + off).clamp(0, 1), valid, water)["fv_px"].float()
    assert ((a - b).abs().sum() / a.sum().clamp_min(1)) < 0.1
