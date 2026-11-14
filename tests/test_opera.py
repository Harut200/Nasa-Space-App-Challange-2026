"""Synthetic tests for scripts/detect/opera.py: class-code mapping, majority resampling, metrics, compositing."""
import numpy as np
import pytest

from scripts.detect import opera


def test_classify_s1_codes():
    w = np.array([[0, 1, 2, 3], [250, 251, 254, 255]], np.uint8)
    o, p, v = opera.classify(w, "s1")
    assert o.tolist() == [[False, True, False, False], [False] * 4]
    assert p.tolist() == [[False, False, True, True], [False] * 4]
    assert v.tolist() == [[True] * 4, [False] * 4]


def test_classify_hls_codes_and_unknown_is_invalid():
    w = np.array([0, 1, 2, 3, 4, 252, 253, 255], np.uint8)
    o, p, v = opera.classify(w, "hls")
    assert o.tolist() == [0, 1, 0, 0, 0, 0, 0, 0]
    assert p.tolist() == [0, 0, 1, 0, 0, 0, 0, 0]
    # 3 and 4 are reserved in HLS: never counted as land; cloud 253 and snow 252 and fill 255 invalid
    assert v.tolist() == [1, 1, 1, 0, 0, 0, 0, 0]


def test_block_fraction_and_majority():
    m = np.zeros((4, 4), bool)
    m[:2, :2] = True          # block (0,0) all water
    m[2, 2] = True            # block (1,1): 1 of 4 -> 25 %
    m[0, 2:] = True           # block (0,1): 2 of 4 -> 50 %
    valid = np.ones((4, 4), bool)
    valid[3, 3] = False       # block (1,1) has 3 valid, 1 water -> 33 %
    frac, vfrac = opera.block_fraction(m, valid, 2)
    assert frac[0, 0] == 1.0 and frac[0, 1] == 0.5 and frac[1, 0] == 0.0
    assert frac[1, 1] == pytest.approx(1 / 3)
    assert vfrac[1, 1] == 0.75 and vfrac[0, 0] == 1.0
    assert opera.majority(frac).tolist() == [[True, True], [False, False]]


def test_block_fraction_all_invalid_is_nan_and_not_water():
    m = np.ones((2, 2), bool)
    frac, vfrac = opera.block_fraction(m, np.zeros((2, 2), bool), 2)
    assert np.isnan(frac[0, 0]) and vfrac[0, 0] == 0
    assert not opera.majority(frac)[0, 0]


def test_confusion_metrics_known_case():
    ours = np.array([1, 1, 1, 0, 0, 0, 0, 0], bool)
    ref = np.array([1, 1, 0, 1, 0, 0, 0, 0], bool)
    m = opera.confusion_metrics(ours, ref, np.ones(8, bool), km2=2.0)
    assert (m["tp_km2"], m["fp_km2"], m["fn_km2"], m["tn_km2"]) == (4.0, 2.0, 2.0, 8.0)
    assert m["iou"] == pytest.approx(2 / 4)
    assert m["precision"] == pytest.approx(2 / 3) and m["recall"] == pytest.approx(2 / 3)
    assert m["agreement"] == pytest.approx(6 / 8)
    assert m["balanced_accuracy"] == pytest.approx((2 / 3 + 4 / 5) / 2)
    assert m["area_ratio"] == pytest.approx(1.0)


def test_confusion_respects_domain():
    ours = np.array([1, 1, 0, 0], bool)
    ref = np.array([1, 0, 0, 0], bool)
    dom = np.array([1, 0, 1, 1], bool)       # the disagreeing pixel is outside the domain
    m = opera.confusion_metrics(ours, ref, dom, km2=1.0)
    assert m["iou"] == 1.0 and m["n_px"] == 3


def test_perfect_and_disjoint():
    a = np.zeros((6, 6), bool); a[:3] = True
    assert opera.confusion_metrics(a, a, np.ones_like(a), 1.0)["iou"] == 1.0
    assert opera.confusion_metrics(a, ~a, np.ones_like(a), 1.0)["iou"] == 0.0


def test_bootstrap_interval_contains_point_estimate():
    rng = np.random.default_rng(1)
    ref = rng.random((64, 64)) > 0.7
    ours = ref ^ (rng.random((64, 64)) > 0.9)
    dom = np.ones_like(ref)
    pt = opera.confusion_metrics(ours, ref, dom, 1.0)["iou"]
    ci = opera.block_bootstrap(ours, ref, dom, 1.0, block=8, n=100)["iou_ci95"]
    assert ci[0] <= pt <= ci[1]


def test_parse_title_and_pick_window():
    t = "OPERA_L3_DSWx-S1_T48PWA_20260610T225249Z_20260615T210107Z_S1A_30_v1.0"
    m = opera.parse_title(t)
    assert m["product"] == "s1" and m["tile"] == "T48PWA" and m["acq"].startswith("2026-06-10T22:52:49")
    grans = [dict(m, acq=f"2026-06-{d:02d}T10:00:00") for d in (10, 14, 20, 30)]
    sel = opera.pick(grans, ["2026-06-16"])["2026-06-16"]["T48PWA"]
    assert [g["acq"][8:10] for g in sel] == ["14", "20", "10"]    # 30 June is outside +-6 d; nearest first
    assert sel[0]["dt_days"] == pytest.approx(-1.58, abs=0.01)


def test_composite_nearest_valid_pixel_wins(tmp_path):
    import rasterio
    from rasterio.transform import from_origin

    def write(name, arr):
        p = tmp_path / name
        with rasterio.open(p, "w", driver="GTiff", height=2, width=2, count=1, dtype="uint8",
                           crs="EPSG:32648", transform=from_origin(0, 60, 30, 30)) as d:
            d.write(arr, 1)
        return p

    near = write("near.tif", np.array([[1, 255], [0, 253]], np.uint8))     # nearest: two invalid pixels
    far = write("far.tif", np.array([[0, 1], [1, 1]], np.uint8))
    out, _, sdt, n = opera.composite_tile([(near, 1.0), (far, 5.0)], "s1")
    assert out.tolist() == [[1, 1], [0, 1]]    # valid pixels of the nearest stay, gaps filled from the far one
    assert n == 4 and sdt == pytest.approx(2 * 1.0 + 2 * 5.0)
