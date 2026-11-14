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


# ---------------------------------------------------------------- multi-event config and two-look decomposition
def test_events_config_complete():
    evs = q.load_events()
    assert len({e["id"] for e in evs}) == len(evs) >= 2
    tib = q.get_event("tibet-m70-2026")
    assert tib["views"]["asc"]["co"]["pair"] == "20261019_20261031" and tib["views"]["asc"]["control"]["pair"] == "20261007_20261019"
    for e in evs:
        for k in ("id", "usgs_id", "time", "lat", "lon", "depth_km", "mag", "window", "views", "mechanism"):
            assert k in e, (e["id"], k)
        w = e["window"]
        assert w[0] < e["lon"] < w[2] and w[1] < e["lat"] < w[3]
        for v in e["views"].values():
            assert v["co"]["pair"] != v["control"]["pair"]
            assert q.granule_url(v["co"]["granule"]).endswith(v["co"]["granule"] + ".h5")


def test_job_for_and_no_server_in_repo_config():
    ev = q.get_event("venezuela-m72-2026")
    j = q.job_for(ev, "asc")
    assert [p["id"] for p in j["products"]] == [ev["views"]["asc"]["co"]["pair"], ev["views"]["asc"]["control"]["pair"]]
    assert j["windows"][0]["bbox"] == ev["window"]
    text = open(q.EVENTS_JSON).read() + open(q.__file__).read()
    assert "151." not in text and "oracle" not in text


def test_decompose_east_up_synthetic():
    # ascending looks east (ue > 0), descending looks west (ue < 0); build LOS from known E, U and recover them
    E, U = np.array([5.0, -3.0, 0.0, 12.0]), np.array([2.0, 8.0, -6.0, 0.0])
    ua, ud, ia, idd = 0.62, -0.60, 38.0, 41.0
    d_a = -E * ua + U * np.cos(np.radians(ia))
    d_d = -E * ud + U * np.cos(np.radians(idd))
    E2, U2 = q.decompose_east_up(d_a, d_d, ua, ud, ia, idd)
    assert np.allclose(E2, E) and np.allclose(U2, U)
    # pure uplift: both looks see the same sign; pure east motion: opposite signs
    e0, u0 = q.decompose_east_up(np.array([np.cos(np.radians(ia))]), np.array([np.cos(np.radians(idd))]), ua, ud, ia, idd)
    assert abs(e0[0]) < 1e-9 and abs(u0[0] - 1) < 1e-9
