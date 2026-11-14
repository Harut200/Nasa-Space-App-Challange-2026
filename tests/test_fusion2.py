"""Fast CPU tests for fusion v2 (scripts/detect/fusion2.py): synthetic scenes only, no data files."""
import torch

from scripts.detect import fusion as f1
from scripts.detect import fusion2 as F

T = 10


def _scene(n=400, seed=0):
    """Four populations with per-date open-water probability q and flooded-vegetation evidence r:
    lake that flickers bright on date 4; stable land; floodplain that turns dark at date 5 and stays dark;
    vegetation that brightens in HH (double bounce) on dates 4-5 and then becomes open water from date 6."""
    g = torch.Generator().manual_seed(seed)
    noise = lambda: 0.01 * torch.rand(T, n, generator=g)  # noqa: E731
    q, r, fl = [], [], []
    # lake
    ql = torch.full((T, n), 0.97) - noise()
    ql[4] = 0.03
    q.append(ql); r.append(torch.full((T, n), 0.05)); fl.append(torch.zeros(T - 1, n, dtype=torch.bool))
    # stable land
    q.append(torch.full((T, n), 0.03) + noise()); r.append(torch.full((T, n), 0.05))
    fl.append(torch.zeros(T - 1, n, dtype=torch.bool))
    # floodplain: dark from date 5 (flag on interval 4 -> 5)
    qf = torch.full((T, n), 0.03) + noise()
    qf[5:] = 0.95
    ff = torch.zeros(T - 1, n, dtype=torch.bool)
    ff[4] = True
    q.append(qf); r.append(torch.full((T, n), 0.05)); fl.append(ff)
    # vegetation: HH brightens (r high) on dates 4-5, open water from date 6 (flag on interval 5 -> 6)
    qv = torch.full((T, n), 0.03) + noise()
    qv[6:] = 0.95
    rv = torch.full((T, n), 0.05)
    rv[4:6] = 0.95
    fv = torch.zeros(T - 1, n, dtype=torch.bool)
    fv[5] = True
    q.append(qv); r.append(rv); fl.append(fv)
    return torch.cat(q, 1), torch.cat(r, 1), torch.cat(fl, 1), n


def _fit_and_smooth(time_opt="b"):
    q, r, fl, n = _scene()
    logb = F.log_emission(q, r)
    R = torch.zeros(q.shape[1], dtype=torch.bool)
    kidx, K, _ = F.class_index({"time": time_opt}, T, fl, R, q.shape[1])
    dts = [1] * (T - 1)
    hm = F.fit_io_hmm(logb, dts, kidx, K)
    g = F.smooth_io(logb, hm, dts, kidx)
    return g, hm, n


def test_lake_flicker_stays_water():
    g, _, n = _fit_and_smooth()
    lake = g[:, :, :n]
    assert (lake[4, F.W_] > 0.5).float().mean() > 0.99          # the rough-surface date does not flip the lake
    assert (lake[:, F.W_] > 0.5).float().mean() > 0.99


def test_floodplain_becomes_water_on_its_date():
    g, _, n = _fit_and_smooth()
    fp = g[:, :, 2 * n:3 * n]
    first = (fp[:, F.W_] > 0.5).float().argmax(0)
    assert ((first - 5).abs() <= 1).float().mean() > 0.99
    assert (fp[-1, F.W_] > 0.5).all()                             # and the last date is kept (no flood eaten)
    land = g[:, :, n:2 * n]
    assert (land[:, F.W_] < 0.5).all()


def test_vegetation_passes_through_middle_state():
    g, hm, n = _fit_and_smooth()
    v = g[:, :, 3 * n:]
    st = v.argmax(1)
    assert ((st[4] == F.V_) | (st[5] == F.V_)).float().mean() > 0.95
    assert (st[-1] == F.W_).float().mean() > 0.95
    assert (st[1] == F.L_).float().mean() > 0.95


def test_evidence_dependent_transition_only_on_flagged_interval():
    # class index: the flagged interval and only it gets class 1
    fl = torch.zeros(T - 1, 3, dtype=torch.bool)
    fl[4, 1] = True
    kidx, K, legend = F.class_index({"time": "b"}, T, fl, None, 3)
    assert K == 2 and kidx[4, 1] == 1 and int(kidx.sum()) == 1
    # with a known matrix pair, P(water) rises only after the flagged interval for neutral evidence
    A0 = torch.tensor([[0.99, 0.005, 0.005], [0.01, 0.98, 0.01], [0.002, 0.002, 0.996]])
    A1 = torch.tensor([[0.99, 0.005, 0.005], [0.30, 0.60, 0.10], [0.60, 0.05, 0.35]])
    hm = {"pi": torch.tensor([0.01, 0.01, 0.98]), "A": torch.stack([A0, A1]), "K": 2}
    q = torch.full((T, 3), 0.5)
    r = torch.full((T, 3), 0.5)
    g = F.smooth_io(F.log_emission(q, r), hm, [1] * (T - 1), kidx)
    pw_flag, pw_none = g[:, F.W_, 1], g[:, F.W_, 0]
    assert torch.allclose(pw_flag[:5], pw_none[:5], atol=1e-6)    # identical before the flagged interval
    assert pw_flag[5] > pw_none[5] + 0.3                          # raised right after it
    # and fitting learns a higher land -> water rate for the flagged class
    _, hmf, _ = _fit_and_smooth()
    assert hmf["A"][1][F.L_, F.W_] > 10 * hmf["A"][0][F.L_, F.W_]


def test_gap_expands_to_two_steps():
    kidx = torch.tensor([[0], [1], [0]])
    kx = F.expand_index(kidx, [1, 2, 1])
    assert kx.flatten().tolist() == [0, 1, 1, 0]


def test_exact_copy_gets_no_extra_credit():
    """v1 pooling (L1 + L2, w >= 0): a duplicated expert splits its weight; the fused probability is unchanged."""
    g = torch.Generator().manual_seed(1)
    y = (torch.rand(6000, generator=g) > 0.5).float()
    x = (2 * y - 1) * 0.8 + torch.randn(6000, generator=g)       # an informative but noisy expert (logit units)
    w1, b1 = f1.fit_pool(x[:, None], y)
    w2, b2 = f1.fit_pool(torch.stack([x, x], 1), y)
    p1 = torch.sigmoid(x * w1[0] + b1)
    p2 = torch.sigmoid(x * w2.sum() + b2)
    assert (p1 - p2).abs().max() < 0.02
    assert abs(float(w2.sum()) - float(w1[0])) < 0.05 * float(w1[0])


def _m(guard_ok, far=4.0, dip=0.7, flips=0.0005, herr=0.2, iou=0.48, band=1, rho=0.45, sp=0.97):
    return {"ba": {"tonle-sap": 0.999, "sevan": 0.998, "ararat": 0.9999}, "far_km2": far, "sevan_dip": dip,
            "sevan_mae_calm": 10.0, "flips": flips, "max_seconds": 3.0,
            "fp": {"spearman_alt": sp, "rho_amb": rho, "hydro_err": herr, "hydro_iou_test": iou, "hydro_in_band": band,
                   "guard_ok": guard_ok, "guard_worst_ratio": 1.0 if guard_ok else 0.8}}


def test_no_flood_eaten_guard():
    core = [2000.0, 2100.0, 3800.0]
    assert F.guard([2000.0, 2100.0, 3790.0], core)[0]
    ok, worst = F.guard([2100.0, 2200.0, 3100.0], core)            # loses 700 km2 of confident flood at the end
    assert not ok and worst < 0.85
    v1 = _m(False)
    cur = _m(True)
    better_lakes_but_eats_flood = _m(False, far=1.0, flips=0.0001)
    acc, why = F.admit2(v1, cur, better_lakes_but_eats_flood)
    assert not acc and not why["fp_guard_ok"]
    acc, why = F.admit2(v1, cur, _m(True, far=2.0))
    assert acc and "mountain false water" in why["improved"]


def test_core_series():
    m = torch.zeros(4, 1, 3, dtype=torch.bool)
    m[1:, 0, 0] = True        # water from date 1 on: core from 1
    m[2, 0, 1] = True         # one date only: core only if it is the last date -> never here
    m[3, 0, 2] = True         # last date only: core on the last date
    c = F.core_series(m)
    assert c[:, 0, 0].tolist() == [False, True, True, True]
    assert not c[:, 0, 1].any()
    assert c[:, 0, 2].tolist() == [False, False, False, True]


def test_seeded_expert_refuses_other_dates():
    d = {"dates": ["2026-06-19", "2026-07-01"], "water": torch.zeros(2, 3, 3, dtype=torch.bool)}
    p, m, v = F._seeded_fn(d, {"dates": ["2026-06-19", "2026-07-01"]})
    assert p is None and v is None and m.shape == (2, 3, 3)
    try:
        F._seeded_fn(d, {"dates": ["2026-06-20", "2026-07-02"]})
        assert False, "date mismatch must be refused"
    except ValueError:
        pass


def test_regime_map_reference_free():
    hh = torch.full((T, 4, 4), 0.5)
    hh[5:, 0, 0] = 0.05       # variable, flat, low -> regime
    hh[5:, 0, 1] = 0.05       # variable but steep -> not
    wp = torch.ones(4, 4, dtype=torch.bool)
    slope = torch.zeros(4, 4)
    slope[0, 1] = 10.0
    R = F.regime_map(hh, wp, slope, torch.zeros(4, 4))
    assert R[0, 0] and not R[0, 1] and not R[1, 1]
