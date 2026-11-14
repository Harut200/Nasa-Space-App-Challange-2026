"""Synthetic-DEM tests for scripts/detect/terrain.py:  .venv/bin/python -m pytest tests/test_terrain.py -q"""
import math

import torch

from scripts.detect import terrain as T

PX = 30.0            # native pixel (m)
F = 6                # native pixels per grid pixel (180 m)
N = 360              # native size -> 60 x 60 grid pixels (10.8 km)
LOOK_AZ = 270.0      # radar looks due west (sensor to the east)
INC = 40.0


def _plane(gx, gy, n=64, px=PX):
    yy, xx = torch.meshgrid(torch.arange(n, dtype=torch.float32), torch.arange(n, dtype=torch.float32), indexing="ij")
    return gx * xx * px - gy * yy * px      # rows go south, so north gradient gy means z falls with row


def test_slope_aspect_plane():
    # 10 % rise to the east -> slope atan(0.1) = 5.71 deg, faces west (aspect 270)
    s, a = T.slope_aspect(_plane(0.1, 0.0), PX, PX)
    assert abs(float(s[5:-5, 5:-5].mean()) - math.degrees(math.atan(0.1))) < 0.01
    assert abs(float(a[5:-5, 5:-5].mean()) - 270) < 0.01
    # 30 deg plane rising to the north, faces south (180)
    g = math.tan(math.radians(30))
    s, a = T.slope_aspect(_plane(0.0, g), PX, PX)
    assert abs(float(s[5:-5, 5:-5].mean()) - 30) < 0.01
    assert abs(float(a[5:-5, 5:-5].mean()) - 180) < 0.01
    # lon pixel smaller than lat pixel (cos(lat)): same metric slope must come out
    s, _ = T.slope_aspect(_plane(0.1, 0.0, px=PX * 0.75), torch.full((64,), PX * 0.75), PX)
    assert abs(float(s[5:-5, 5:-5].mean()) - math.degrees(math.atan(0.1))) < 0.01


def test_local_incidence_flat_and_faces():
    flat = torch.zeros(1, 1)
    li, lay = T.local_incidence(flat, flat, LOOK_AZ, INC)
    assert abs(float(li) - INC) < 1e-3 and not bool(lay)
    # face tilted towards the sensor (terrain rising westward = along the look direction) by 20 deg
    g = torch.full((1, 1), -math.tan(math.radians(20)))       # dz/dx_east < 0  -> rises to the west
    li, _ = T.local_incidence(g, torch.zeros(1, 1), LOOK_AZ, INC)
    assert abs(float(li) - 20) < 1e-3
    # back-slope steeper than the grazing angle (90 - 40 = 50 deg) -> local incidence > 90 = shadow
    g = torch.full((1, 1), math.tan(math.radians(60)))        # falls to the west, faces away
    li, _ = T.local_incidence(g, torch.zeros(1, 1), LOOK_AZ, INC)
    assert float(li) > 90


def _scene():
    """West: flat plain at 100 m with a lake basin (flat 92 m floor, gentle 3 % banks).
    East: a ridge 450 m high, gentle face to the east (towards the radar), 60 deg cliff to the west."""
    yy, xx = torch.meshgrid(torch.arange(N, dtype=torch.float32), torch.arange(N, dtype=torch.float32), indexing="ij")
    x, y = xx * PX, yy * PX
    z = torch.full((N, N), 100.0)
    r = torch.sqrt((x - 2700) ** 2 + (y - 5400) ** 2)                     # basin centre
    z = torch.where(r < 1500, torch.full_like(z, 92.0), z)
    bank = (r >= 1500) & (r < 1500 + 8 / 0.03)
    z = torch.where(bank, 92 + (r - 1500) * 0.03, z)
    crest_x = 8100.0
    cliff = 450 / math.tan(math.radians(60))                              # 260 m wide cliff
    west = (x >= crest_x - cliff) & (x < crest_x)
    z = torch.where(west, 100 + (x - (crest_x - cliff)) * math.tan(math.radians(60)), z)
    east = x >= crest_x
    z = torch.where(east, (550 - (x - crest_x) * 0.12).clamp(min=100), z)
    return z


def _fields(z):
    gx, gy = T.gradient(z, PX, PX)
    slope = torch.rad2deg(torch.atan(torch.sqrt(gx ** 2 + gy ** 2)))
    li, _ = T.local_incidence(gx, gy, LOOK_AZ, INC)
    shadow = (li >= 90) | T.cast_shadow(z, PX, PX, LOOK_AZ, INC, max_m=2000)
    agg = T.Aggregator((N, N), 0.0, 0.0, 1.0, 1.0, (0.0, -float(N), float(N), 0.0), N // F, N // F)
    zm = agg.mean(z)
    km2 = (F * PX / 1000) ** 2
    out = {"dem": zm, "slope_mean": agg.mean(slope),
           "slope_p90": agg.quantile(slope, 0.9, [float(v) for v in torch.arange(0, 70.5, 0.5)]),
           "dim": agg.mean(li > T.LI_MAX_DEG), "shadow": agg.mean(shadow),
           "rel": T.rel_height(zm, F * PX)}
    out.update(T.classify(out["slope_mean"], out["slope_p90"], out["dim"], out["shadow"], out["rel"], km2))
    return out


def test_mask_on_synthetic_scene():
    z = _scene()
    o = _fields(z)
    poss = o["water_possible"]
    g = lambda xm, ym: (int(ym / (F * PX)), int(xm / (F * PX)))  # noqa: E731
    # lake basin floor and its banks: water possible everywhere inside the basin
    for xm, ym in [(2700, 5400), (1500, 5400), (2700, 4200), (3900, 5400)]:
        assert bool(poss[g(xm, ym)]), (xm, ym)
    basin = torch.zeros_like(poss)
    for r in range(poss.shape[0]):
        for c in range(poss.shape[1]):
            if math.hypot((c + 0.5) * F * PX - 2700, (r + 0.5) * F * PX - 5400) < 1700:
                basin[r, c] = True
    assert float(poss[basin].float().mean()) == 1.0
    # flat plain: possible
    assert bool(poss[g(500, 1000)])
    # steep cliff facing away from the radar: not possible, and it is flagged as shadow/back-slope
    r, c = g(8000, 3000)
    assert not bool(poss[r, c])
    assert bool(o["backslope"][r, c]) and bool(o["steep"][r, c])
    # gentle face towards the radar near the crest: 'high' above the local minimum -> not possible
    r, c = g(8500, 3000)
    assert bool(o["high"][r, c]) and not bool(poss[r, c])
    # cast shadow (native 30 m): the sensor is EAST, rays graze over the 450 m crest at 90 - 40 = 50 deg,
    # so the plain from the cliff foot (7840 m) to 450 / tan(50) = 378 m west of the crest (~7722 m) is dark
    cs = T.cast_shadow(z, PX, PX, LOOK_AZ, INC, max_m=2000)[100]
    assert bool(cs[int(7770 / PX)]) and bool(cs[int(7810 / PX)])      # flat ground, not a back-slope itself
    assert not bool(cs[int(7650 / PX)]) and not bool(cs[int(8400 / PX)])


def test_p90_finds_small_steep_slopes():
    # a 180 m pixel that is 1/3 cliff: mean slope stays modest, p90 sees the cliff
    z = torch.zeros(6, 6)
    z[:, 4:] = torch.arange(1, 3, dtype=torch.float32)[None] * 52.0       # ~60 deg steps
    gx, gy = T.gradient(z, PX, PX)
    slope = torch.rad2deg(torch.atan(torch.sqrt(gx ** 2 + gy ** 2)))
    agg = T.Aggregator((6, 6), 0.0, 0.0, 1.0, 1.0, (0.0, -6.0, 6.0, 0.0), 1, 1)
    p90 = float(agg.quantile(slope, 0.9, [float(v) for v in range(0, 71)]))
    assert p90 > 2 * float(agg.mean(slope)) and p90 > 40
