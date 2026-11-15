"""Synthetic tests of scripts/detect/events.py (coherence loss with a control, clusters, config, job file)."""
import numpy as np

from scripts.detect import events as E


def _grid(n=200, pix=80.0):
    x = np.arange(n) * pix
    y = -np.arange(n) * pix
    return np.meshgrid(x, y)


def test_coherence_loss_area_and_control():
    X, Y = _grid()
    rng = np.random.default_rng(0)
    ctl = np.clip(0.7 + 0.1 * rng.standard_normal(X.shape), 0, 1)
    ev = ctl.copy()
    cx, cy = X[100, 100], Y[100, 100]
    lava = (X - cx) ** 2 + (Y - cy) ** 2 < 1500.0 ** 2          # disc of radius 1.5 km
    ev[lava] = 0.05
    pix = 0.08 * 0.08
    st, loss, comp = E.loss_stats(ev, ctl, X, Y, (cx, cy), 4000.0, pix)
    true_area = lava.sum() * pix
    assert abs(st["loss_area_km2"] - true_area) < 0.05 * true_area
    assert st["mean_coh_control_at_loss_px"] > 0.5 and st["mean_coh_event_at_loss_px"] < 0.1
    assert st["loss_fraction_outside"] < 0.01


def test_no_event_no_loss():
    X, Y = _grid()
    rng = np.random.default_rng(1)
    ctl = np.clip(0.7 + 0.1 * rng.standard_normal(X.shape), 0, 1)
    ev = np.clip(0.7 + 0.1 * rng.standard_normal(X.shape), 0, 1)
    st, loss, _ = E.loss_stats(ev, ctl, X, Y, (X[100, 100], Y[100, 100]), 4000.0, 0.0064)
    assert st["loss_area_km2"] == 0.0


def test_control_already_incoherent_is_not_counted():
    X, Y = _grid(60)
    ctl = np.full(X.shape, 0.1)           # water / dense forest: never coherent
    ev = np.full(X.shape, 0.05)
    st, loss, comp = E.loss_stats(ev, ctl, X, Y, (X[30, 30], Y[30, 30]), 3000.0, 0.0064)
    assert comp.sum() == 0 and st["loss_area_km2"] == 0.0


def test_min_cluster_removes_speckle():
    m = np.zeros((50, 50), bool)
    m[5, 5] = True                         # single pixel
    m[20:30, 20:30] = True                 # 100 px block
    out = E.min_cluster(m, 12)
    assert not out[5, 5] and out[25, 25] and out.sum() == 100


def test_nan_coherence_ignored():
    ce = np.array([[np.nan, 0.0], [0.1, 0.9]]); cc = np.array([[0.9, np.nan], [0.8, 0.8]])
    loss, comp = E.coherence_loss(ce, cc)
    assert comp.sum() == 2 and loss.sum() == 1 and loss[1, 0]


def test_job_file_has_two_pairs_and_no_server_details(monkeypatch):
    ev = {"id": "x-2026", "window": [1, 2, 3, 4], "heights": [0, 100],
          "views": {"asc": {"co": {"pair": "20260101_20260113", "granule": "NISAR_L2_PR_GUNW_a"},
                            "control": {"pair": "20251220_20260101", "granule": "NISAR_L2_UR_GUNW_b"}}}}
    j = E.job_for(ev, "asc")
    assert [p["id"] for p in j["products"]] == ["20260101_20260113", "20251220_20260101"]
    assert "NISAR-UR" in j["products"][1]["url"] and "PROVISIONAL" in j["products"][0]["url"]
    assert "ssh" not in str(j).lower() and j["out"].startswith("~/work4/")


def test_config_ids_unique_and_views_have_pairs():
    evs = E.load_events()
    assert len({e["id"] for e in evs}) == len(evs)
    for e in evs:
        for v in e["views"].values():
            assert "_" in v["co"]["pair"] and "_" in v["control"]["pair"]
            # the control is before the event pair unless the view says control_after_event
            if not v.get("control_after_event"):
                assert v["control"]["pair"].split("_")[1] <= v["co"]["pair"].split("_")[0] or \
                    v["control"]["pair"].split("_")[0] < v["co"]["pair"].split("_")[0]
            assert v["control"]["pair"] != v["co"]["pair"]


def test_drop_rule_follows_partial_loss_and_clusters_rank_by_area():
    X, Y = _grid(120)
    ctl = np.full(X.shape, 0.8)
    ev = ctl.copy()
    ev[40:60, 40:60] = 0.3         # partial loss: below 0.35 but not near zero
    ev[100:103, 10:13] = 0.0       # small cluster, 9 px
    loss, comp = E.coherence_loss(ev, ctl, hi=0.45, lo=0.35, drop=0.25)
    assert loss.sum() == 400 + 9
    big = E.min_cluster(loss, 12)
    assert big.sum() == 400        # speckle-sized cluster dropped
    # without the drop rule a pixel just under `lo` but with a small drop would count; with it it does not
    ev2 = ctl.copy(); ev2[5, 5] = 0.5
    assert not E.coherence_loss(ev2, np.full(X.shape, 0.55), lo=0.6, drop=0.25)[0].any()


# ---------------------------------------------------------------- BEFORE layer (control coherence)
def _ev_syn(**kw):
    ev = {"id": "syn-ev", "time": "2026-08-06T00:00:00Z", "window": [0.0, 0.0, 1.0, 1.0], "primary_view": "asc",
          "views": {"asc": {"direction": "A", "label": "ascending", "co": {"pair": "20260723_20260816"},
                            "control": {"pair": "20260629_20260723"}}}}
    ev.update(kw)
    return ev


def test_pairing_block_and_post_event_control():
    ba = E.before_pairing(_ev_syn())
    assert ba == [{"view": "asc", "before": "coherence_before", "after": "coherence", "label": "Radar coherence",
                   "before_dates": ["2026-06-29", "2026-07-23"], "after_dates": ["2026-07-23", "2026-08-16"]}]
    e = _ev_syn(); e["views"]["asc"]["control_after_event"] = True      # kilauea-ep54-like: control is after the event
    assert E.before_pairing(e) == []
    e = _ev_syn(); e["views"]["asc"]["control"]["pair"] = "20260816_20260828"
    assert E.before_pairing(e) == []


def test_export_before_same_grid_ramp_and_legend(tmp_path, monkeypatch):
    import json
    import rasterio
    ev = _ev_syn()
    app, vd = tmp_path / "products", tmp_path / "insar"
    app.mkdir(); vd.mkdir()
    x, y = np.linspace(0.005, 0.995, 100), np.linspace(0.995, 0.005, 100)
    coh_ctl = np.tile(np.linspace(0, 1, 100, dtype=np.float32), (100, 1)); coh_ctl[:5] = np.nan
    np.savez(vd / "products_cm.npz", coh_ctl=coh_ctl, coh=coh_ctl, x=x, y=y, epsg=4326)
    leg = {"type": "ramp", "unit": "coherence 0-1", "min": 0, "max": 1, "colors": ["#000000", "#ffffff"]}
    ev_layers = [{"id": "coherence", "label": "C", "dated": False, "legend": leg}]
    (app / "layers.json").write_text(json.dumps(ev_layers))
    monkeypatch.setattr(E, "app_dir", lambda e: str(app)); monkeypatch.setattr(E, "view_dir", lambda e, v: str(vd))
    E.export_before(ev); E.export_before(ev)
    L = json.loads((app / "layers.json").read_text())
    assert [l["id"] for l in L] == ["coherence", "coherence_before"] and L[0] == ev_layers[0]
    assert L[1]["legend"] == leg and L[1]["dated"] is False and L[1]["label"] == "Before the event: radar coherence (29 Jun–23 Jul)"
    with rasterio.open(app / "coherence_before.tif") as f:
        a = f.read()
    (W, S, Ee, N), w, h = E._grid(ev)
    assert a.shape == (4, h, w) and a[3].max() == 255 and a[3].min() == 0          # NaN rows transparent
    ok = a[3] == 255
    assert a[0][ok].min() < 10 and a[0][ok].max() > 245                              # grey ramp black -> white
    # control flagged after the event: nothing exported
    (app / "coherence_before.tif").unlink()
    e2 = _ev_syn(); e2["views"]["asc"]["control_after_event"] = True
    E.export_before(e2)
    assert not (app / "coherence_before.tif").exists() and json.loads((app / "layers.json").read_text()) == ev_layers


def test_views_sharing_a_data_folder_download_each_pair_once():
    mk = lambda p, g: {"pair": p, "granule": g}
    ev = {"id": "y-2026", "window": [1, 2, 3, 4], "heights": [0, 100],
          "views": {"a": {"co": mk("20260201_20260213", "NISAR_L2_PR_GUNW_1"), "control": mk("20260120_20260201", "NISAR_L2_PR_GUNW_0")},
                    "b": {"data_view": "a", "co": mk("20260301_20260313", "NISAR_L2_PR_GUNW_2"), "control": mk("20260120_20260201", "NISAR_L2_PR_GUNW_0")},
                    "c": {"co": mk("20260401_20260413", "NISAR_L2_PR_GUNW_3"), "control": mk("20260320_20260401", "NISAR_L2_PR_GUNW_4")}}}
    ids = [p["id"] for p in E.job_for(ev, "a")["products"]]
    assert ids == ["20260201_20260213", "20260120_20260201", "20260301_20260313"]
    assert [p["id"] for p in E.job_for(ev, "c")["products"]] == ["20260401_20260413", "20260320_20260401"]
    assert E.view_dir(ev, "b") == E.view_dir(ev, "a") and E.pcm_path(ev, "b") != E.pcm_path(ev, "a")


def test_fire_rule_is_wider_than_default_and_catches_a_partial_drop():
    X, Y = _grid()
    ctl = np.full(X.shape, 0.75)
    ev = ctl.copy()
    ev[60:140, 60:140] = 0.32                      # a burn scar: coherence falls to about 0.3, not to zero
    pix = 0.08 * 0.08
    cx, cy = X[100, 100], Y[100, 100]
    st_default, _, _ = E.loss_stats(ev, ctl, X, Y, (cx, cy), 8000.0, pix)
    st_fire, _, _ = E.loss_stats(ev, ctl, X, Y, (cx, cy), 8000.0, pix, hi=0.5, lo=0.45, drop=0.3, min_px=30)
    assert st_default["loss_area_km2"] == 0.0
    assert abs(st_fire["loss_area_km2"] - 80 * 80 * pix) < 0.02 * 80 * 80 * pix


def test_loss_by_elevation_separates_a_snow_line_from_a_local_patch():
    n = 100
    yy, xx = np.mgrid[0:n, 0:n]
    height = yy * 20.0                                   # a ramp, 0-2000 m
    ctl = np.full((n, n), 0.7)
    snow = ctl.copy(); snow[height >= 1000] = 0.1          # everything above 1000 m decorrelates
    local = ctl.copy(); local[40:60, 40:60] = 0.1         # one patch crossing the bands
    edges = [0, 500, 1000, 1500, 2001]
    s = E.loss_by_elevation(snow, ctl, height, edges)
    l = E.loss_by_elevation(local, ctl, height, edges)
    assert s[0]["lost_share"] == 0 and s[2]["lost_share"] > 0.99 and s[3]["lost_share"] > 0.99
    assert max(r["lost_share"] for r in l) < 0.2 and sum(r["lost_km2"] for r in l) > 0
