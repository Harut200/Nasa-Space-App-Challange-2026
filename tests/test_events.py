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
