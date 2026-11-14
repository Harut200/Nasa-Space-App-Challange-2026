"""Synthetic-stack tests for the math change-detection ops (no files needed)."""
import math

import pytest
import torch

from scripts.detect import ops

T, H, W = 6, 160, 160
CY = CX = 80
RADII = [15 + 5 * k for k in range(T)]   # dark disc radius at date k
RING = (48, 56)                          # HH-only brightening ring
RING_FROM = 2                            # ring brightens from this date on
LOOKS = 30   # gamma speckle: raw rel. noise 1/sqrt(30)=0.18, ~2x the 0.06-0.10 measured on previews


def make_stack(seed=0):
    g = torch.Generator().manual_seed(seed)
    yy, xx = torch.meshgrid(torch.arange(H).float(), torch.arange(W).float(), indexing="ij")
    r = ((yy - CY) ** 2 + (xx - CX) ** 2).sqrt()
    x = torch.empty(T, 2, H, W)
    for t in range(T):
        hh = torch.full((H, W), 0.5)
        hv = torch.full((H, W), 0.4)
        disc = r <= RADII[t]
        hh[disc], hv[disc] = 0.02, 0.02
        if t >= RING_FROM:
            hh[(r >= RING[0]) & (r <= RING[1])] = 0.85
        noise = torch._standard_gamma(torch.full((2, H, W), float(LOOKS)), generator=g) / LOOKS
        x[t] = (torch.stack([hh, hv]) * noise).clamp(0, 1)
    return x, torch.ones(T, H, W, dtype=torch.bool), r


@pytest.fixture(scope="module")
def result():
    x, valid, r = make_stack()
    return ops.run_pipeline(x, valid, "preview", dev=torch.device("cpu")), r


def test_water_area_tracks_disc(result):
    out, r = result
    for t in range(T):
        true_px = (r <= RADII[t]).sum().item()
        got = out["water"][t].sum().item()
        assert abs(got - true_px) / true_px < 0.10, (t, got, true_px)


def test_change_date_in_annuli(result):
    out, r = result
    for k in range(1, T):
        ann = (r > RADII[k - 1] + 1.5) & (r < RADII[k] - 1.5)
        frac = (out["change_date"][ann] == k).float().mean().item()
        assert frac > 0.8, (k, frac)


def test_far_pixels_stable(result):
    out, r = result
    far = r > RING[1] + 6
    frac = (out["classes"][far] == 0).float().mean().item()
    assert frac >= 0.99, frac


def test_ring_is_flooded_vegetation(result):
    out, r = result
    ring = (r >= RING[0] + 1.5) & (r <= RING[1] - 1.5)
    cls = out["classes"][ring]
    assert (cls == 3).float().mean().item() > 0.9
    assert (out["water"][:, ring]).float().mean().item() < 0.01


def test_otsu_and_morphology():
    v = torch.cat([torch.full((500,), 0.1), torch.full((500,), 0.9)])
    assert 0.1 < ops.otsu(v) < 0.9
    m = torch.zeros(1, 20, 20, dtype=torch.bool)
    m[0, 5:12, 5:12] = True
    m[0, 16, 16] = True  # speck
    cleaned = ops.remove_small(m, 4)
    assert cleaned.sum() == 49
    assert ops.opening(m, 3).sum() == 49
    assert ops.dilate(m[:, :, :])[0, 4, 4] and not ops.erode(m)[0, 5, 5]
