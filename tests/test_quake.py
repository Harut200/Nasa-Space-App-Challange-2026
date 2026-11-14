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


# ---------------------------------------------------------------- BEFORE layers from the control pair
def _ev(**kw):
    ev = {"id": "syn", "time": "2026-06-28T22:00:00Z", "window": [0.0, 0.0, 1.0, 1.0], "los_lim_cm": 20, "fringe_cm": 10,
          "primary_view": "asc", "export_views": ["asc"],
          "views": {"asc": {"direction": "A", "label": "ascending", "co": {"pair": "20260625_20260707"},
                            "control": {"pair": "20260613_20260625"}}}}
    ev.update(kw)
    return ev


def test_control_is_before_rules():
    assert q.control_is_before(_ev(), "asc")
    e = _ev(); e["views"]["asc"]["control_after_event"] = True
    assert not q.control_is_before(e, "asc")                       # flagged post-event (Flores-like)
    e = _ev(); e["views"]["asc"]["control"]["pair"] = "20260707_20260719"
    assert not q.control_is_before(e, "asc")                       # control after the event pair (Qaidam asc11-like)
    e = _ev(time="2026-06-20T00:00:00Z")
    assert not q.control_is_before(e, "asc")                       # control ends after the event date


def test_pairing_block_well_formed_and_empty_for_post_event_control():
    ba, skipped = q.before_pairing(_ev())
    assert skipped == {} and [b["before"] for b in ba] == ["fringes_before", "los_before_cm"]
    assert [b["after"] for b in ba] == ["fringes", "los_cm"]
    for b in ba:
        assert set(b) == {"view", "before", "after", "label", "before_dates", "after_dates"}
        assert b["before_dates"] == ["2026-06-13", "2026-06-25"] and b["after_dates"] == ["2026-06-25", "2026-07-07"]
    two = _ev(export_views=["asc", "desc"]); two["views"]["desc"] = dict(two["views"]["asc"], direction="D")
    assert q.before_pairing(two)[0][1]["after"] == "los_asc_cm"
    bad = _ev(); bad["views"]["asc"]["control_after_event"] = True
    ba, skipped = q.before_pairing(bad)
    assert ba == [] and "asc" in skipped


def test_before_layers_use_event_scale():
    G = np.array([[-20.0, 0.0, 20.0, np.nan]])
    a = q.before_layers(_ev(), G, 20.0, 10.0)
    assert a["los_before_cm"].shape == (1, 4, 4) and list(a["los_before_cm"][..., 3][0]) == [255, 255, 255, 0]
    assert tuple(a["los_before_cm"][0, 0, :3]) == (103, 0, 31) and tuple(a["los_before_cm"][0, 2, :3]) == (5, 48, 97)  # RdBu ends = legend colours
    G2 = np.array([[0.0, 10.0, 25.0, 5.0]])               # re-wrapped at the same 10 cm cycle: 0, 10 and 20 look alike
    f = q.before_layers(_ev(), G2, 20.0, 10.0)["fringes_before"]
    assert (f[0, 0] == f[0, 1]).all() and f[0, 0, :3].tolist() != f[0, 3, :3].tolist()


def test_export_before_end_to_end(tmp_path, monkeypatch):
    import json, os
    ev = _ev()
    app, vd = tmp_path / "products", tmp_path / "insar"
    app.mkdir(); vd.mkdir()
    x, y = np.linspace(0.005, 0.995, 100), np.linspace(0.995, 0.005, 100)
    ctl = np.random.default_rng(1).normal(0, 1, (100, 100)).astype(np.float32); ctl[:, :10] = np.nan
    np.savez(vd / "products_cm.npz", ctl_cm=ctl, ctl_x=x, ctl_y=y, epsg=4326)
    legend_los = {"type": "diverging", "unit": "cm", "min": -20.0, "max": 20.0, "colors": ["a", "b", "c"]}
    legend_fr = {"type": "ramp", "min": 0, "max": 10.0, "colors": ["x"]}
    ev_layers = [{"id": "los_cm", "label": "L", "dated": False, "legend": legend_los},
                 {"id": "fringes", "label": "F", "dated": False, "legend": legend_fr}]
    (app / "layers.json").write_text(json.dumps(ev_layers))
    monkeypatch.setattr(q, "app_dir", lambda e: str(app)); monkeypatch.setattr(q, "view_dir", lambda e, v: str(vd))
    q.export_before(ev); q.export_before(ev)                        # idempotent
    L = json.loads((app / "layers.json").read_text())
    assert [l["id"] for l in L] == ["los_cm", "fringes", "fringes_before", "los_before_cm"] and L[:2] == ev_layers
    assert L[2]["legend"] == legend_fr and L[3]["legend"] == legend_los and all(l["dated"] is False for l in L)
    assert L[2]["label"] == "Before the earthquake: fringes (12 days, 13 Jun–25 Jun)"
    assert L[3]["label"] == "Before the earthquake: ground motion (cm)"
    import rasterio
    with rasterio.open(app / "los_before_cm.tif") as f:             # same app grid as the event layers
        (W, S, E, N), w, h = q._grid(ev)
        assert (f.width, f.height) == (w, h) and abs(f.bounds.left - W) < 1e-9 and abs(f.bounds.top - N) < 1e-9
    ba = json.loads((app / "before_after.json").read_text())["before_after"]
    assert len(ba) == 2 and ba[0]["after"] == "fringes"
    # a post-event control: no before layer, no before tif, layers untouched
    e2 = _ev(); e2["views"]["asc"]["control_after_event"] = True
    for n in ("fringes_before.tif", "los_before_cm.tif"):
        os.remove(app / n)
    q.export_before(e2)
    assert json.loads((app / "layers.json").read_text()) == ev_layers
    assert not (app / "los_before_cm.tif").exists() and json.loads((app / "before_after.json").read_text())["before_after"] == []
