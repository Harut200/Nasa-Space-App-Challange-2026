"""Fast CPU tests of the cross-scene (self-calibrating) water detector: .venv/bin/python -m pytest tests/test_crossscene.py"""
import math

import pytest
import torch
import torch.nn.functional as F

from scripts.detect import crossscene as cs

T, H, W = 4, 96, 96


def _smooth_noise(gen, t, h, w, k=7):
    z = torch.randn(t, 1, h, w, generator=gen)
    return F.avg_pool2d(z, k, 1, k // 2, count_include_pad=False)[:, 0] * math.sqrt(k)


def _inv_logit(z, e=0.01):
    ex = torch.exp(z)
    return ((ex * (1 + e) - e) / (1 + ex)).clamp(0, 1)


def scene(h=H, w=W, water=True, windy_date=None, seed=0):
    """Synthetic preview stack [T,2,h,w] in 0..1: textured land (HH ~0.4, HV ~0.3) and a round lake near the
    display floor in both channels; optionally one date on which the lake is wind-roughened (brightened)."""
    gen = torch.Generator().manual_seed(seed)
    yy, xx = torch.meshgrid(torch.arange(h), torch.arange(w), indexing="ij")
    lake = ((yy - 48) ** 2 + (xx - 40) ** 2) < 16 ** 2 if water else torch.zeros(h, w, dtype=torch.bool)
    hh = -0.4 + 0.45 * _smooth_noise(gen, T, h, w) + 0.15 * torch.randn(T, h, w, generator=gen)
    hv = -0.9 + 0.45 * _smooth_noise(gen, T, h, w) + 0.15 * torch.randn(T, h, w, generator=gen)
    wl = -4.2 + 0.05 * torch.randn(T, h, w, generator=gen)
    hh = torch.where(lake[None], wl, hh)
    hv = torch.where(lake[None], wl + 0.02, hv)
    if windy_date is not None:  # wind: the lake surface brightens to about 40 % of the way to land in HH
        hh[windy_date] = torch.where(lake, -2.6 + 0.2 * torch.randn(h, w, generator=gen), hh[windy_date])
        hv[windy_date] = torch.where(lake, -3.6 + 0.1 * torch.randn(h, w, generator=gen), hv[windy_date])
    L = torch.stack([_inv_logit(hh), _inv_logit(hv)], 1)
    valid = torch.ones(T, h, w, dtype=torch.bool)
    return L, valid, lake


STRETCHES = {
    "identity": lambda x: x,
    "gamma 0.6": lambda x: x ** 0.6,
    "gamma 1.6": lambda x: x ** 1.6,
    "no floor": lambda x: 0.15 + 0.8 * x,
    "logit affine": lambda x: _inv_logit(0.7 * cs.feat(x) + 0.8),
}


def iou(a, b):
    return float((a & b).sum()) / max(float((a | b).sum()), 1.0)


def test_stretch_invariance_seeded_vs_fixed():
    L, valid, lake = scene()
    truth = lake[None].expand(T, -1, -1)
    ious_seeded, ious_fixed = {}, {}
    for name, fn in STRETCHES.items():
        Ls = fn(L)
        r = cs.detect_water_adaptive(Ls, valid, method="seeded")
        assert r["has_water"], name
        ious_seeded[name] = iou(r["water"], truth)
        rf = cs.detect_water_adaptive(Ls, valid, method="fixed")
        ious_fixed[name] = iou(rf["water"], truth)
    # the adaptive method returns (nearly) the same mask under every monotone display stretch ...
    assert min(ious_seeded.values()) > 0.9, ious_seeded
    # ... while the fixed thresholds do not (they lose the lake when the floor is lifted)
    assert ious_fixed["identity"] > 0.9
    assert min(ious_fixed.values()) < 0.5, ious_fixed


def test_no_water_scene():
    L, valid, _ = scene(water=False)
    r = cs.detect_water_adaptive(L, valid, method="seeded")
    assert r["has_water"] is False
    assert not r["water"].any()


def test_windy_date_found_via_stable_seeds():
    L, valid, lake = scene(windy_date=2)
    r = cs.detect_water_adaptive(L, valid, method="seeded")
    assert r["has_water"]
    rec = float(r["water"][2][lake].float().mean())
    assert rec > 0.8, rec
    # the fixed rule loses the windy date (HH 0.06 strong seed no longer reached)
    rf = cs.detect_water_adaptive(L, valid, method="fixed")
    assert float(rf["water"][2][lake].float().mean()) < rec
    # and no land is called water on that date
    assert float(r["water"][2][~lake].float().mean()) < 0.01


def test_knobs_independent_of_scene_size():
    """The same lake in a frame four times larger (more land around it) gives the same mask where they overlap."""
    L, valid, lake = scene()
    Lb, validb, _ = scene(h=2 * H, w=2 * W, water=False, seed=1)
    Lb[:, :, :H, :W] = L
    r1 = cs.detect_water_adaptive(L, valid, method="seeded")
    r2 = cs.detect_water_adaptive(Lb, validb, method="seeded")
    assert r2["has_water"]
    assert iou(r1["water"], r2["water"][:, :H, :W]) > 0.95
    assert not r2["water"][:, H:, :].any() and not r2["water"][:, :, W:].any()


def test_dead_corner_is_not_water():
    """An unimaged corner at the display floor, touching the frame border, must not become a seed."""
    L, valid, _ = scene(water=False)
    L[:, :, 70:, :20] = 0.0
    r = cs.detect_water_adaptive(L, valid, method="seeded")
    assert not r["water"][:, 70:, :20].any()


def test_valley_and_relative_units():
    x = torch.cat([torch.randn(5000) * 0.3 - 4.0, torch.randn(50000) * 0.6])
    v = cs.valleys(x)
    assert any(-3.5 < d["pos"] < -1.0 and d["depth"] < 0.2 for d in v)
    # a unimodal sample has no valley that would pass the dark-mode test (noise dips in the tails hold few px)
    kn = cs.CKNOBS
    uni = [d for d in cs.valleys(torch.randn(50000, generator=torch.Generator().manual_seed(0)))
           if d["depth"] <= kn["valley_depth_max"] and d["n_left"] >= kn["mode_min_px"]
           and d["frac_left"] <= kn["mode_max_frac"]]
    assert uni == []
