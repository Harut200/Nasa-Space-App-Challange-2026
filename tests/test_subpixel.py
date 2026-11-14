"""Sub-pixel water fraction (scripts/detect/subpixel.py) on a synthetic scene with known truth.

A 360 x 360 "fine" scene (think 30 m cells) holds a dark lake with a smooth, wavy shoreline on textured land,
with gamma speckle. It is block-averaged 6 x 6 IN POWER (as a radar multilooks) to a 60 x 60 "coarse" scene
(think 180 m pixels), so the true water fraction of every coarse pixel is known exactly. The coarse power
is shown as display brightness with a logarithmic 30 dB stretch, like our previews are assumed to be.
"""
import math

import torch
import torch.nn.functional as F

from scripts.detect import subpixel as sp

K = 6          # fine cells per coarse pixel (30 m -> 180 m)
HI = 360
D = 30.0       # dB span of the display stretch
DB0 = -35.0    # dB at brightness 0


def _smooth_noise(g, n, scale):
    z = torch.randn(1, 1, n // scale + 2, n // scale + 2, generator=g)
    return F.interpolate(z, size=(n, n), mode="bicubic", align_corners=False)[0, 0]


def scene(seed=0):
    g = torch.Generator().manual_seed(seed)
    yy, xx = torch.meshgrid(torch.arange(HI) + 0.5, torch.arange(HI) + 0.5, indexing="ij")
    cy, cx = HI * 0.5, HI * 0.46
    ang = torch.atan2(yy - cy, xx - cx)
    rad = HI * 0.30 * (1 + 0.10 * torch.sin(3 * ang + 0.4) + 0.05 * torch.cos(7 * ang))
    water = ((yy - cy) ** 2 + (xx - cx) ** 2).sqrt() < rad
    chans = []
    for land_mean, water_db, looks in ((-8.0, -24.0, 4.0), (-15.0, -27.0, 4.0)):   # HH, HV
        land_db = land_mean + 2.5 * _smooth_noise(g, HI, 36)       # land texture on ~1 km scales
        db = torch.where(water, torch.full_like(land_db, water_db), land_db)
        speckle = torch._standard_gamma(torch.full((HI, HI), looks), generator=g) / looks
        P = 10 ** (db / 10) * speckle
        Pc = F.avg_pool2d(P[None, None], K)[0, 0]                   # multilook in power
        b = ((10 * torch.log10(Pc) - DB0) / D).clamp(1 / 255, 1.0)
        chans.append(b)
    b = torch.stack(chans)[None]                                    # [1, 2, 60, 60]
    f_true = F.avg_pool2d(water.float()[None, None], K)[0, 0]
    return b, f_true


def hard_mask(b):
    """A threshold detector halfway (in brightness) between water and land, as ops.water_masks does."""
    thr = ((-16.0 - DB0) / D)
    return b[:, 0] < thr


def run(domain, b, water, channels="combined"):
    valid = torch.ones_like(water)
    return sp.water_fraction(b, valid, water, source="preview", x_frac=None, db_range=D, domain=domain,
                             channels=channels)


def test_power_mixing_recovers_true_fraction():
    b, f_true = scene(0)
    water = hard_mask(b)
    out = run("power", b, water)
    f = out["frac"][0].float()
    shore = (f_true > 0) & (f_true < 1)
    rmse = (f[shore] - f_true[shore]).pow(2).mean().sqrt()
    assert rmse < 0.20, float(rmse)                                  # stated tolerance (seen: 0.14-0.18)
    hard_rmse = (water[0].float()[shore] - f_true[shore]).pow(2).mean().sqrt()
    assert rmse < 0.6 * hard_rmse


def test_area_beats_hard_and_half_pixel():
    for seed in (0, 1, 2):
        b, f_true = scene(seed)
        water = hard_mask(b)
        truth = float(f_true.sum())
        frac = float(run("power", b, water)["frac"].float().sum())
        hard = float(water.sum())
        half = float(sp.half_pixel(water, torch.ones_like(water)).sum())
        assert abs(frac - truth) < abs(hard - truth), (frac, hard, truth)
        assert abs(frac - truth) < abs(half - truth), (frac, half, truth)
        assert abs(frac - truth) / truth < 0.015                     # < 1.5 % (hard mask: ~3.5 % low)


def test_power_domain_beats_log_domain():
    """Mixing done (wrongly) on the dB-like brightness badly underestimates partial water: a half-water
    pixel is only 3 dB darker than land, i.e. 0.1 of a 30 dB stretch, not halfway to the water level."""
    b, f_true = scene(3)
    water = hard_mask(b)
    shore = (f_true > 0) & (f_true < 1)
    fp = run("power", b, water)["frac"][0].float()
    fl = run("display", b, water)["frac"][0].float()
    e_p = (fp[shore] - f_true[shore]).pow(2).mean().sqrt()
    e_l = (fl[shore] - f_true[shore]).pow(2).mean().sqrt()
    assert e_p < 0.7 * e_l, (float(e_p), float(e_l))
    assert abs(fp.sum() - f_true.sum()) < abs(fl.sum() - f_true.sum())


def test_fractions_are_hard_away_from_shore():
    b, f_true = scene(4)
    water = hard_mask(b)
    out = run("power", b, water)
    f = out["frac"][0].float()
    far = ~out["band"][0]
    assert torch.equal(f[far], water[0].float()[far])
    assert set(torch.unique(f[far]).tolist()) <= {0.0, 1.0}
    assert (f >= 0).all() and (f <= 1).all()


def test_contract_and_channels():
    b, f_true = scene(5)
    water = hard_mask(b)
    b2 = torch.cat([b, b], 0)                                        # T = 2
    w2 = torch.cat([water, water], 0)
    out = sp.water_fraction(b2, torch.ones_like(w2), w2, km2_per_px=0.0324)
    assert out["frac"].dtype == torch.float16 and out["frac"].shape == (2, 60, 60)
    assert out["band"].dtype == torch.bool and out["band"].shape == (2, 60, 60)
    assert out["area_km2_frac"].shape == (2,) and out["area_km2_hard"].shape == (2,)
    assert math.isclose(float(out["area_km2_hard"][0]), float(water.sum()) * 0.0324, rel_tol=1e-6)
    assert out["domain"] == "display"                                # auto: previews mix in display units
    w = out["weights"][0]
    assert abs(float(w.sum()) - 1) < 1e-5 and (w >= 0).all()
    # HH alone is also a valid estimate
    hh = run("power", b, water, channels="HH")
    assert float(hh["weights"][0, 0]) == 1.0


def test_contour_points_straight_edge():
    """f ramps 1 -> 0 across columns; the f = 0.5 line must be found at the exact sub-pixel column."""
    f = torch.zeros(5, 8)
    f[:, :3] = 1.0
    f[:, 3] = 0.8     # crossing between col 3 (0.8) and col 4 (0.0) at 3 + 0.3/0.8 = 3.375
    pts = sp.contour_points(f, torch.ones(5, 8, dtype=torch.bool))
    assert pts.shape[0] == 5
    assert torch.allclose(pts[:, 1].float(), torch.full((5,), 3.375))
    # a hard 0/1 edge gives the pixel-edge midpoint (the staircase)
    pts = sp.contour_points((f > 0.9).float(), torch.ones(5, 8, dtype=torch.bool))
    assert torch.allclose(pts[:, 1].float(), torch.full((5,), 2.5))


def test_half_pixel_rule():
    w = torch.zeros(1, 6, 6, dtype=torch.bool)
    w[0, :, :3] = True
    h = sp.half_pixel(w, torch.ones_like(w))[0]
    assert torch.equal(h[:, 2], torch.full((6,), 0.5)) and torch.equal(h[:, 3], torch.full((6,), 0.5))
    assert torch.equal(h[:, 0], torch.ones(6)) and torch.equal(h[:, 5], torch.zeros(6))
