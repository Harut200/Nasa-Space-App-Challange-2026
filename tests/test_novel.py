"""Synthetic checks for scripts/detect/novel.py (flood front + logistic choreography)."""
import datetime as dt
import math

import numpy as np
import torch
from scipy.ndimage import distance_transform_edt

from scripts.detect import novel as N

DATES = ["2026-06-19", "2026-07-01", "2026-07-13", "2026-07-25", "2026-08-18",
         "2026-08-30", "2026-09-11", "2026-09-23", "2026-10-05", "2026-10-17"]


def cone(H=121, W=121):
    yy, xx = torch.meshgrid(torch.arange(H).float(), torch.arange(W).float(), indexing="ij")
    return ((yy - H // 2) ** 2 + (xx - W // 2) ** 2).sqrt()  # height = radius (pixels)


def level(day):  # water level (same units as the cone) rises 0.5 px per day from 5 at day 170
    return 5.0 + 0.5 * (day - 170.0)


def basin():
    z = cone()
    t = torch.tensor([N.doy(d) for d in DATES])
    water = torch.stack([z < level(tk) for tk in t])
    valid = torch.ones_like(water)
    return z, water, valid


def test_edt_matches_scipy():
    m = torch.rand(90, 70) < 0.01
    m[0, 0] = True
    assert np.abs(N.edt(m).numpy() - distance_transform_edt(~m.numpy())).max() < 1e-3


def test_isotonic_first_wet_and_violations():
    # series per pixel: 0 1 0 1 1 (one flip) -> first wet index 1 or 3 with one disagreement; ties -> later
    w = torch.tensor([[0, 1, 0, 1, 1], [1, 1, 1, 1, 1], [0, 0, 0, 0, 0], [0, 0, 1, 1, 1]]).T.bool()[:, :, None]
    k, e = N.first_wet_index(w, torch.ones_like(w))
    assert k[:, 0].tolist() == [3, 0, 5, 2]
    assert e[:, 0].tolist() == [1, 0, 0, 0]


def test_arrival_time_recovered_on_cone():
    z, water, valid = basin()
    A = N.arrival_time(water, DATES, valid)
    arr = A["arrival_day"]
    f = torch.isfinite(arr)
    true = 170.0 + (z - 5.0) / 0.5
    err = (arr - true)[f].abs()
    assert f.sum() > 1000
    assert float(err.median()) < 1.0 and float(err.quantile(0.95)) < 3.0
    # in the 24-day gap band as well
    g = f & (A["arrival_lo"] == N.doy("2026-07-25"))
    assert g.sum() > 50 and float((arr - true)[g].abs().median()) < 1.5
    assert A["violations_px"] == 0
    # front speed: level rises 0.5/day on a slope of 1 -> 0.5 px/day
    sp = A["front_speed_km_day"][f]
    assert abs(float(sp[torch.isfinite(sp)].median()) - 0.5) < 0.1


def test_leave_one_date_out_beats_naive():
    _, water, valid = basin()
    rows = N.leave_one_date_out(water, DATES, valid, 1.0)
    for r in rows:
        q = r["lasting"]
        assert q["front"]["mean_boundary_km"] < min(q["previous"]["mean_boundary_km"], q["next"]["mean_boundary_km"])
        assert q["front"]["band_accuracy"] > max(q["previous"]["band_accuracy"], q["next"]["band_accuracy"])
        assert q["front"]["band_accuracy"] > 0.9


def test_height_order_matches_cone():
    z, water, valid = basin()
    A = N.arrival_time(water, DATES, valid)
    f = torch.isfinite(A["arrival_day"])
    h = N.height_from_order(A["arrival_day"], f)["rel_height_rank"]
    assert N.spearman(h[f], z[f]) > 0.95
    # with gauge levels the staircase becomes metres
    hm = N.height_from_order(A["arrival_day"], f, levels_m=[level(N.doy(d)) for d in DATES], dates=DATES)["height_m"]
    assert float((hm[f] - z[f]).abs().median()) < 0.6


def test_static_scene_has_no_front():
    z = cone()
    water = (z < 20).expand(len(DATES), -1, -1).clone()
    water[3, 0:3, 0:3] = True  # one transient speckle blob (wind / noise)
    A = N.arrival_time(water, DATES, torch.ones_like(water))
    assert int(torch.isfinite(A["arrival_day"]).sum()) == 0
    assert A["violations_px"] == 9


def test_logistic_fit_recovers_step_through_gap():
    torch.manual_seed(0)
    t = torch.tensor([N.doy(d) for d in DATES])
    H, W = 20, 20
    t0_true, tau_true, a_true, b_true = 245.0, 4.0, -3.0, -6.0  # step on ~2 Sep
    clean = a_true + b_true * torch.sigmoid((t - t0_true) / tau_true)
    y = clean[:, None, None, None] + 0.3 * torch.randn(len(DATES), 2, H, W)
    y[:, 1] = -8.0 + 0.3 * torch.randn(len(DATES), H, W)  # HV: no step
    valid = torch.ones(len(DATES), H, W, dtype=torch.bool)
    f = N.fit_logistic_steps(y, DATES, valid, refine_steps=100)
    assert abs(float(f["t0"][0].median()) - t0_true) < 4
    assert abs(float(f["b"][0].median()) - b_true) < 0.6
    assert float(f["tau"][0].median()) < 12
    assert float(f["sig"][0].float().mean()) > 0.95
    assert float(f["sig"][1].float().mean()) < 0.1
    # a step inside the outage: the profile interval of t0 must cover the gap
    clean2 = a_true + b_true * torch.sigmoid((t - 220.0) / 1.5)
    y2 = clean2[:, None, None, None] + 0.1 * torch.randn(len(DATES), 2, 4, 4)
    f2 = N.fit_logistic_steps(y2, DATES, torch.ones(len(DATES), 4, 4, dtype=torch.bool), refine_steps=50)
    lo, hi = float(f2["t0_lo"][0].median()), float(f2["t0_hi"][0].median())
    assert 206 <= lo and hi <= 230 and hi - lo >= 8  # inside the gap, but not pinned to a day


def test_static_series_has_no_amplitude():
    torch.manual_seed(1)
    y = -5 + 0.2 * torch.randn(len(DATES), 2, 10, 10)
    f = N.fit_logistic_steps(y, DATES, torch.ones(len(DATES), 10, 10, dtype=torch.bool), refine_steps=30)
    assert float(f["sig"].float().mean()) < 0.05
    rgb = N.choreography_rgb(f, DATES, torch.ones(len(DATES), 10, 10, dtype=torch.bool))
    r, g, b = rgb[..., 0].int(), rgb[..., 1].int(), rgb[..., 2].int()
    assert float(((r - g).abs() + (g - b).abs()).float().mean()) < 10  # grey
