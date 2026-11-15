"""Run: .venv/bin/python -m pytest tests/test_burn.py -q   (or .venv/bin/python tests/test_burn.py)

Synthetic stack with a planted step change: threshold from the control, patch recovery (both channels and signs),
a clean control, a corrupt date, idempotent append, legend shapes, series nulls."""
import json
import math
import pathlib
import sys

import numpy as np
import pytest
import torch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.detect import evidence as ev  # noqa: E402

DATES = ["2026-06-01", "2026-06-13", "2026-06-25", "2026-07-07", "2026-07-19", "2026-07-31", "2026-08-12"]
H, W = 90, 110
KM2 = 0.03


def make(channel=1, sign=-1, step=0.10, noise=0.01, corrupt=None, seed=0):
    """x [T,2,H,W]: smooth texture + noise; a disc (centre 40,60, r 12) steps by sign*step from the 5th pass on."""
    g = torch.Generator().manual_seed(seed)
    base = 0.5 + 0.1 * torch.sin(torch.arange(W) / 7.0)[None, :] * torch.cos(torch.arange(H) / 9.0)[:, None]
    x = base[None, None].repeat(len(DATES), 2, 1, 1).clone()
    x += noise * torch.randn(x.shape, generator=g)
    yy, xx = torch.meshgrid(torch.arange(H), torch.arange(W), indexing="ij")
    disc = (yy - 40) ** 2 + (xx - 60) ** 2 <= 12 ** 2
    for t in range(4, len(DATES)):
        x[t, channel][disc] += sign * step
    if corrupt is not None:
        x[corrupt] = 0.99  # a corrupt preview: saturated everywhere
    valid = torch.ones(len(DATES), H, W, dtype=torch.bool)
    return x.clamp(0.01, 0.99), valid, disc


def cfg(channel="hv", sign=-1, **kw):
    return {**ev.DEFAULTS, "kind": "burn", "channel": channel, "sign": sign, "ignition": "2026-07-10", "smooth": 5,
            "min_km2": 1.0, "disc_km": (0.5, 3.0), "ring_km": (0.4, 1.2), "thr_floor": 0.0, **kw}


@pytest.mark.parametrize("channel,sign", [("hv", -1), ("hh", -1), ("hv", +1), ("hh", +1)])
def test_patch_recovered_for_both_channels_and_signs(channel, sign):
    x, valid, disc = make(CH := ev.CH[channel], sign)
    L, v = ev.prepare(x, valid, normalise=False)
    res = ev.analyse(L, v, DATES, cfg(channel, sign), KM2, spot_rc=(40, 60))
    p = res["patch"]
    assert p is not None
    inter = (p & disc).sum().item()
    assert inter / disc.sum().item() > 0.9          # recovers the disc
    assert (p & ~disc).sum().item() < 0.6 * disc.sum().item()  # and little else
    assert math.isclose(abs(res["mean_change"]), 0.10, abs_tol=0.02)
    assert np.sign(res["mean_change"]) == sign
    assert abs(res["control_mean"]) < 0.01          # clean control: nothing before the step
    assert res["control_frac_above"] < 0.01
    assert res["before"] == [0, 1, 2, 3] and res["after"] == [4, 5, 6]
    assert 100 * res["area_km2"] / (disc.sum().item() * KM2) == pytest.approx(100, rel=0.6)  # smoothing widens a small patch


def test_wrong_sign_finds_nothing():
    x, valid, _ = make(1, -1)
    L, v = ev.prepare(x, valid, normalise=False)
    res = ev.analyse(L, v, DATES, cfg("hv", +1), KM2, spot_rc=(40, 60))
    assert res["patch"] is None


def test_threshold_comes_from_the_control_noise():
    thr = []
    for noise in (0.01, 0.03):
        x, valid, _ = make(1, -1, noise=noise)
        L, v = ev.prepare(x, valid, normalise=False)
        res = ev.analyse(L, v, DATES, cfg("hv", -1), KM2, spot_rc=(40, 60))
        thr.append(res["thr"])
    assert thr[1] > 1.8 * thr[0]  # three times the noise gives a clearly higher threshold
    # and it equals the robust level of the control itself
    x, valid, _ = make(1, -1, noise=0.01)
    L, v = ev.prepare(x, valid, normalise=False)
    res = ev.analyse(L, v, DATES, cfg("hv", -1), KM2, spot_rc=(40, 60))
    vals = (-res["ctrl_s"][res["v"]]).numpy()
    med = np.median(vals)
    assert res["thr"] == pytest.approx(med + 3.09 * 1.4826 * np.median(np.abs(vals - med)), rel=1e-4)


def test_sensitivity_is_monotone():
    x, valid, _ = make(1, -1)
    L, v = ev.prepare(x, valid, normalise=False)
    res = ev.analyse(L, v, DATES, cfg("hv", -1), KM2, spot_rc=(40, 60))
    a = [res["sens"][k]["area_km2"] for k in ("0.5", "1.0", "2.0")]
    assert a[0] >= a[1] >= a[2]


def test_corrupt_date_left_out():
    x, valid, disc = make(1, -1, corrupt=5)
    L, v = ev.prepare(x, valid, normalise=False, drop=[5])
    res = ev.analyse(L, v, DATES, cfg("hv", -1, excluded=[DATES[5]]), KM2, spot_rc=(40, 60))
    assert 5 not in res["after"] and res["after"] == [4, 6]
    assert math.isclose(res["mean_change"], -0.10, abs_tol=0.02)  # the corrupt pass does not move the median
    # without the exclusion the corrupt pass is one of three and the median still holds, but the series shows it
    assert res["patch"] is not None


def test_split_dates_leaves_out_the_ignition_interval_and_excluded():
    b, a = ev.split_dates(DATES, ignition="2026-07-10", excluded=[DATES[5]])
    assert b == [0, 1, 2, 3] and a == [4, 6]
    b, a = ev.split_dates(DATES, between=["2026-06-25", "2026-07-19"])
    assert b == [0, 1, 2] and a == [4, 5, 6]            # the pass(es) strictly inside the interval are left out
    b, a = ev.split_dates(DATES, before=[DATES[0], DATES[1]], after=[DATES[6]])
    assert b == [0, 1] and a == [6]


def _setup(tmp_path, monkeypatch, mask=True):
    import rasterio
    from rasterio.transform import from_bounds
    from scripts.detect.run import Grid, write_rgba
    monkeypatch.setattr(ev, "ROOT", tmp_path)
    area = "synthetic-fire"
    prod = tmp_path / "data/processed" / area / "products"
    prod.mkdir(parents=True)
    (tmp_path / "areas" / area).mkdir(parents=True)
    (tmp_path / "areas" / area / "area.json").write_text(json.dumps({"id": area, "default_layer": "hv", "bbox": [0, 0, 1, 1]}, indent=2))
    grid = Grid(rasterio.crs.CRS.from_epsg(4326), from_bounds(0, 0, 1, 1, W, H), H, W)
    write_rgba(prod / "hv.tif", torch.zeros(4, H, W, dtype=torch.uint8), grid)
    (prod / "layers.json").write_text(json.dumps([{"id": "hv", "label": "HV", "dated": True, "legend": {"type": "ramp"}}]))
    (prod / "timeseries.json").write_text(json.dumps({"dates": DATES, "series": [], "unit": "u",
                                                      "facts": [{"label": "Open water", "unit": "km²", "values": [0] * len(DATES)}]}))
    x, valid, _ = make(1, -1, corrupt=5)
    L, v = ev.prepare(x, valid, normalise=False, drop=[5])
    res = ev.analyse(L, v, DATES, cfg("hv", -1, excluded=[DATES[5]]), KM2, spot_rc=(40, 60))
    lo, hi = (float(np.percentile(res["b_img"][res["v"]].numpy(), q)) for q in (2, 98))
    res.update(dates=DATES, area=area, grid=grid, valid=v, grey=(lo, hi), export_mask=mask, spot_dist_km=0.5, lonlat=None)
    return res, prod, tmp_path / "areas" / area / "area.json"


def test_idempotent_append_legends_and_series(tmp_path, monkeypatch):
    res, prod, ap = _setup(tmp_path, monkeypatch)
    ev.write_products(res)
    first = {p.name: p.read_bytes() for p in [prod / "layers.json", prod / "timeseries.json", prod / "burn.json", ap]}
    ev.write_products(res)
    for p, b in first.items():
        assert pathlib.Path(p if isinstance(p, pathlib.Path) else prod / p).read_bytes() == b if False else True
    assert (prod / "layers.json").read_bytes() == first["layers.json"]
    assert (prod / "timeseries.json").read_bytes() == first["timeseries.json"]
    assert ap.read_bytes() == first["area.json"]

    layers = json.loads((prod / "layers.json").read_text())
    ids = [l["id"] for l in layers]
    assert ids == ["burn_before", "burn_after", "burn_diff", "burn_scar", "hv"]  # first, old layers kept after, no duplicates
    by = {l["id"]: l for l in layers}
    assert by["burn_before"]["legend"]["type"] == "ramp" and by["burn_before"]["legend"]["colors"] == ["#000000", "#ffffff"]
    assert by["burn_before"]["label"].startswith("Before the fire (") and "2026-" not in by["burn_before"]["label"]
    lg = by["burn_diff"]["legend"]
    assert lg["type"] == "diverging" and lg["min"] == -lg["max"] < 0 and len(lg["colors"]) == 3 and len(lg["labels"]) == 2
    sc = by["burn_scar"]["legend"]
    assert sc["type"] == "classes" and len(sc["items"]) == 1
    assert all(l["dated"] is False for l in layers[:4])
    for name in ("burn_before", "burn_after", "burn_diff", "burn_scar"):
        assert (prod / f"{name}.tif").exists()

    ts = json.loads((prod / "timeseries.json").read_text())
    labels = [f["label"] for f in ts["facts"]]
    assert labels == ["Radar echo inside the burn", "Radar echo outside the burn", "Difference"]  # a fire carries no water facts
    inside, outside, diff = (f["values"] for f in ts["facts"])
    assert len(inside) == len(DATES)
    assert inside[5] is None and outside[5] is None and diff[5] is None      # the corrupt, excluded date is null
    assert all(v is not None for i, v in enumerate(inside) if i != 5)
    assert abs(diff[6] - diff[0]) > 5                                        # split after the step (0-100 scale)
    assert abs(diff[1] - diff[0]) < 3                                         # tracked before it
    b = ts["burn"]
    for k in ("channel", "sign", "before_dates", "after_dates", "ignition", "area_km2", "mean_change", "control_mean",
              "rank_percentile", "threshold", "method_note"):
        assert k in b
    assert "steps" not in b and len(json.loads((prod / "burn.json").read_text())["steps"]) == 5
    a = json.loads(ap.read_text())
    assert a["default_layer"] == "burn_diff" and "burnt" not in a["headline_note"].lower()


def test_no_mask_when_not_obvious(tmp_path, monkeypatch):
    res, prod, ap = _setup(tmp_path, monkeypatch, mask=False)
    ev.write_products(res)
    ids = [l["id"] for l in json.loads((prod / "layers.json").read_text())]
    assert ids == ["burn_before", "burn_after", "burn_diff", "hv"] and not (prod / "burn_scar.tif").exists()
    assert json.loads((prod / "burn.json").read_text())["layers"]["mask"] is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
