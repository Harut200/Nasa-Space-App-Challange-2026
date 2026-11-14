"""Pass quality control (scripts/detect/qc.py) on synthetic ten-date stacks with speckle."""
import pathlib
import sys

import pytest
import torch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.detect import qc  # noqa: E402

DATES = ["2026-06-20", "2026-07-02", "2026-07-14", "2026-07-26", "2026-08-19",
         "2026-08-31", "2026-09-12", "2026-09-24", "2026-10-06", "2026-10-18"]
H = W = 160
BAD = 5  # index of the date we spoil


def meta(pols=None):
    pols = pols or {}
    return [{"date": d, "startTime": f"{d}T02:04:08Z", "polarization": pols.get(d, ["HH", "HV"]),
             "bandwidth": ["40+5"], "crid": "P05023", "wkt": ""} for d in DATES]


def smooth_field(g, h, w, k=9):
    f = torch.rand(1, 1, h + 2 * k, w + 2 * k, generator=g)
    for _ in range(3):
        f = torch.nn.functional.avg_pool2d(f, k, 1, 0)
        f = torch.nn.functional.pad(f, (k // 2,) * 4, mode="replicate")
    f = f[0, 0, :h, :w]
    return (f - f.min()) / (f.max() - f.min())


def scene(seed=0):
    g = torch.Generator().manual_seed(seed)
    # broad land-cover pattern plus field-scale texture (edges for the registration to lock onto)
    parcels = torch.rand(1, 1, (H + 16) // 8 + 1, (W + 16) // 8 + 1, generator=g)
    parcels = torch.nn.functional.interpolate(parcels, scale_factor=8, mode="nearest")[0, 0, :H + 16, :W + 16]
    hh = 0.30 + 0.20 * smooth_field(g, H + 16, W + 16) + 0.20 * parcels
    hv = 0.8 * hh + 0.05 + 0.08 * (smooth_field(g, H + 16, W + 16) - 0.5)
    return torch.stack([hh, hv]), g


def make_stack(seed=0, spoil=None):
    """[T, 2, H, W] brightness with 6-look speckle, 8-bit quantisation and a 1 % per-date gain wobble.
    spoil(t, img [2, H+16, W+16], g) may modify the noise-free image of date t (before speckle)."""
    base, g = scene(seed)
    xs = []
    for t in range(len(DATES)):
        img = base.clone() * (1 + 0.01 * torch.randn(2, 1, 1, generator=g))
        if spoil is not None:
            img = spoil(t, img, g)
        img = img[:, 8:8 + H, 8:8 + W]
        speck = torch._standard_gamma(torch.full(img.shape, 6.0), generator=g) / 6.0
        xs.append(((img * speck).clamp(1 / 255, 1) * 255).round() / 255)
    return torch.stack(xs), torch.ones(len(DATES), H, W, dtype=torch.bool)


def run(x, valid, pols=None, **kw):
    return qc.run_qc(x, valid, DATES, meta_passes=meta(pols), area_cfg={}, pixel_km=0.18, **kw)


def verdicts(res):
    return {p["date"]: (p["verdict"], p["fail_channels"], p["warn_channels"]) for p in res["passes"]}


def test_robust_z_and_local():
    v = torch.tensor([1.0, 1.01, 0.99, 1.0, 1.02, 1.5, 1.0, 0.98, 1.01, 1.0])
    z, med, sc = qc.robust_z(v, 0.01)
    assert z[BAD] > 20 and abs(z[0]) < 2
    # a smooth drift of several consecutive dates is not an isolated outlier with local=True
    drift = torch.tensor([1.0, 1.0, 1.0, 1.0, 1.0, 1.1, 1.1, 1.1, 1.1, 1.1])
    zl, _, _ = qc.robust_z(drift, 0.01, local=True)
    zg, _, _ = qc.robust_z(drift, 0.01)
    assert abs(zl[7]) < 1 and abs(zg[7]) > 4


def test_clean_stack_all_ok():
    x, v = make_stack()
    res = run(x, v)
    bad = {d: vv for d, vv in verdicts(res).items() if vv[0] != "ok"}
    assert not bad, bad
    assert res["summary"]["exclude_dates"] == [] and res["summary"]["exclude_channels"] == {}
    assert all(p["score"] == 100 for p in res["passes"])


def test_hv_gain_flagged_on_hv_only():
    def spoil(t, img, g):
        if t == BAD:
            img[1] *= 1.3
        return img
    x, v = make_stack(spoil=spoil)
    res = run(x, v)
    p = res["passes"][BAD]
    assert p["verdict"] == "fail" and p["fail_channels"] == ["HV"], p["reason"]
    assert "HH" not in p["warn_channels"]
    assert "HV" in p["reason"] and "HH is normal" in p["reason"]
    assert res["summary"]["exclude_channels"] == {DATES[BAD]: ["HV"]}
    others = [vv for d, vv in verdicts(res).items() if d != DATES[BAD]]
    assert all(vv[0] == "ok" for vv in others), others


def test_shift_flagged_without_alignment():
    def spoil(t, img, g):
        return torch.roll(img, shifts=4, dims=1) if t == BAD else img
    x, v = make_stack(spoil=spoil)
    res = run(x, v)
    p = res["passes"][BAD]
    ck = p["checks"]["residual_shift"]
    assert ck["verdict"] == "fail" and 3.0 < ck["value"] < 5.0, ck
    assert abs(ck["rows"]) > 3 and abs(ck["cols"]) < 1
    assert p["verdict"] == "fail" and p["fail_channels"] == ["HH", "HV"]
    assert DATES[BAD] in res["summary"]["exclude_dates"]
    assert "misregistered" in p["reason"]


def test_across_track_ramp_is_gradient():
    def spoil(t, img, g):
        if t == BAD:
            img = img * torch.linspace(0.8, 1.2, img.shape[-1])[None, None]
        return img
    x, v = make_stack(spoil=spoil)
    res = run(x, v)
    p = res["passes"][BAD]
    assert p["verdict"] in ("warn", "fail")
    pats = [p["checks"][f"loo_{c}"]["pattern"] for c in qc.CH]
    assert "gradient" in pats, pats
    assert any(p["checks"][f"slope_{c}"]["verdict"] != "ok" for c in qc.CH)


def test_growing_flood_not_flagged():
    yy, xx = torch.meshgrid(torch.arange(H + 16), torch.arange(W + 16), indexing="ij")
    dist = ((yy - 0.45 * H) ** 2 + (xx - 0.55 * W) ** 2).sqrt()
    r_final = (0.30 * H * W / torch.pi) ** 0.5  # 30 % of the scene by the last date

    def spoil(t, img, g):
        rad = r_final * t / (len(DATES) - 1)
        m = dist <= rad
        img[0][m], img[1][m] = 0.03, 0.04
        return img
    x, v = make_stack(spoil=spoil)
    res = run(x, v)
    share = float((x[-1, 0] < 0.1).float().mean())
    assert 0.25 < share < 0.4
    for p in res["passes"]:
        assert p["verdict"] == "ok", (p["date"], p["reason"])
        for c in qc.CH:
            assert p["checks"][f"loo_{c}"]["pattern"] not in ("uniform", "gradient"), (p["date"], c)


def test_fixed_blob_reported_as_mark():
    x, v = make_stack()
    x[:, :, 60:66, 100:107] = 0.05  # identical value on every date, no speckle
    res = run(x, v)
    marks = res["summary"]["fixed_marks"]
    assert len(marks) == 1, marks
    m = marks[0]
    assert m["kind"] == "dark" and m["pixels"] == 42
    assert 60 <= m["raw_row"] <= 66 and 100 <= m["raw_col"] <= 107
    assert any("fixed mark" in n for n in res["summary"]["notes"])


def test_hh_only_pass_reported_missing_hv():
    x, v = make_stack()
    x[3, 1] = x[3, 0]  # an HH-only preview draws HV = HH (grey)
    res = run(x, v, pols={DATES[3]: ["HH"]})
    p = res["passes"][3]
    assert p["checks"]["polarisation"]["missing"] == ["HV"]
    assert p["checks"]["missing_HV"]["verdict"] == "fail"
    assert p["fail_channels"] == ["HV"] and "HV" in p["reason"]
    assert res["summary"]["exclude_channels"] == {DATES[3]: ["HV"]}


def test_hh_only_detected_from_data_alone():
    x, v = make_stack()
    x[3, 1] = 1 / 255  # empty HV channel, metadata silent
    res = run(x, v)
    assert res["passes"][3]["fail_channels"] == ["HV"]


def test_metadata_mode_crid_and_cadence():
    m = meta()
    m[2]["bandwidth"] = ["20+5"]
    m[6]["crid"] = "P05099"
    out, s = qc.metadata_checks(DATES, m, {})
    assert s["mode"] == "40+5"
    assert out[DATES[2]]["mode"]["verdict"] == "fail"
    assert out[DATES[6]]["crid"]["verdict"] == "warn"
    assert out[DATES[4]]["cadence"]["label"].startswith("outage")
    dates = DATES[:8] + DATES[9:]  # 2026-10-06 missing, not in the outage
    out, _ = qc.metadata_checks(dates, [p for p in m if p["date"] in dates], {})
    assert out["2026-10-18"]["cadence"]["verdict"] == "warn"
    out, _ = qc.metadata_checks(dates, [p for p in m if p["date"] in dates], {"excluded_dates": ["2026-10-06"]})
    assert out["2026-10-18"]["cadence"]["verdict"] == "info"


def test_apply_qc_neutralises_failed_channel_and_date():
    x = torch.rand(len(DATES), 2, 8, 8)
    v = torch.ones(len(DATES), 8, 8, dtype=torch.bool)
    q = {"summary": {"exclude_dates": [DATES[2]], "exclude_channels": {DATES[BAD]: ["HV"]}}}
    x2, v2, done = qc.apply_qc(x, v, DATES, q)
    assert not v2[2].any() and v2[BAD].all()
    others = [i for i in range(len(DATES)) if i != BAD]
    assert torch.equal(x2[BAD, 1], x[others, 1].median(0).values)
    assert torch.equal(x2[BAD, 0], x[BAD, 0])
    assert done == {DATES[2]: ["HH", "HV"], DATES[BAD]: ["HV"]}
    assert qc.apply_qc(x, v, DATES, None)[2] == {}
