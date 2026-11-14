import torch

from scripts.detect import fusion


def _experts(n=20000, seed=0):
    g = torch.Generator().manual_seed(seed)
    y = (torch.rand(n, generator=g) < 0.5).float()
    s = 2 * y - 1
    good = torch.sigmoid(2.5 * s + 1.5 * torch.randn(n, generator=g))
    noisy = torch.sigmoid(0.8 * s + 2.0 * torch.randn(n, generator=g))
    copy = good.clone()
    useless = torch.sigmoid(torch.randn(n, generator=g))
    return y, {"good": good, "noisy": noisy, "copy": copy, "useless": useless}


def test_weights_favour_good_copy_gets_no_extra_credit_useless_zero():
    y, E = _experts()
    X = torch.stack([fusion.logit(E[k]) for k in ("good", "noisy", "copy", "useless")], 1)
    w, b = fusion.fit_pool(X, y)
    w_alone, _ = fusion.fit_pool(fusion.logit(E["good"])[:, None], y)
    wg, wn, wc, wu = w.tolist()
    assert wg + wc > 3 * wn                       # the good expert (and its copy) dominate the noisy one
    assert wg + wc <= 1.15 * float(w_alone[0])    # the copy adds no credit beyond the good one alone
    assert wu < 0.05 * (wg + wc)                  # useless expert ~ 0
    assert min(w.tolist()) >= 0                   # non-negative weights


def test_fused_is_better_calibrated_than_any_expert():
    y, E = _experts(seed=1)
    X = torch.stack([fusion.logit(E[k]) for k in ("good", "noisy", "copy", "useless")], 1)
    w, b = fusion.fit_pool(X, y)
    yt, Et = _experts(seed=2)                     # evaluated on fresh data
    Xt = torch.stack([fusion.logit(Et[k]) for k in ("good", "noisy", "copy", "useless")], 1)
    q = torch.sigmoid(Xt @ w + b)
    bq = fusion.balanced_brier(q, yt)
    assert all(bq < fusion.balanced_brier(Et[k], yt) for k in Et)


def test_two_value_mapping_is_balanced_posterior():
    y = torch.tensor([1.0] * 100 + [0.0] * 900)
    x = torch.cat([torch.ones(90), torch.zeros(10), torch.ones(9), torch.zeros(891)])
    l1, l0 = fusion.two_value(x, y)
    p1 = torch.sigmoid(torch.tensor(l1))
    assert 0.98 < float(p1) < 1.0 and l0 < -1.5   # TPR 0.9, FPR 0.01 -> P(w|1) = 0.9/0.91 under 50/50


def test_markov_smoothing_removes_windy_flip_keeps_real_change():
    hmm = {"pi": [0.5, 0.5], "A": [[0.95, 0.05], [0.05, 0.95]], "dt_steps": [1, 1, 1, 2, 1, 1, 1, 1, 1]}
    windy = torch.full((10,), 0.9)
    windy[5] = 0.2                                # one windy date looks like land
    change = torch.tensor([0.1] * 5 + [0.9] * 5)  # a lake that really filled
    g = fusion.smooth_time(torch.stack([windy, change], 1), hmm)
    assert g[5, 0] > 0.5 and (g[:, 0] > 0.5).all()
    assert (g[:4, 1] < 0.5).all() and (g[6:, 1] > 0.5).all()


def test_space_smoothing_stays_in_footprint():
    g = torch.zeros(1, 5, 5)
    g[0, 2, 2] = 1.0
    foot = torch.ones(5, 5, dtype=torch.bool)
    foot[0] = False
    s = fusion.smooth_space(g, foot)
    assert float(s[0, 0].abs().sum()) == 0 and abs(float(s[0, 2, 2]) - 1 / 9) < 1e-6


def _m(ba=0.99, far=20.0, dip=60.0, flips=0.03, sec=5.0):
    return {"ba": {"tonle-sap": ba, "sevan": ba, "ararat": ba}, "far_km2": far, "sevan_dip": dip,
            "sevan_mae_calm": 10.0, "flips": flips, "max_seconds": sec}


def test_admission_rejects_expert_that_adds_nothing():
    ok, why = fusion.admit(_m(), _m())
    assert not ok and why["weakness_improved"] == []


def test_admission_rejects_accuracy_loss_even_if_weakness_improves():
    ok, why = fusion.admit(_m(), _m(ba=0.98, far=5.0))
    assert not ok and not why["ba_ok"]


def test_admission_accepts_real_improvement_and_rejects_slow_module():
    ok, why = fusion.admit(_m(), _m(far=10.0))
    assert ok and why["weakness_improved"] == ["far_improves"]
    ok, _ = fusion.admit(_m(), _m(dip=10.0, sec=5000.0))
    assert not ok


def test_end_to_end_useless_expert_gets_zero_weight_on_synthetic_samples():
    y, E = _experts(n=4000, seed=3)
    T = 10
    samp = {"y": y, "X": {"math_noterrain": (E["good"] > 0.5).float(), "useless": fusion.logit(E["useless"])},
            "is_prob": {"math_noterrain": False, "useless": True}, "terrain_off": torch.zeros_like(y),
            "dates": ["2026-06-20", "2026-07-02", "2026-07-14", "2026-07-26", "2026-08-19", "2026-08-31",
                      "2026-09-12", "2026-09-24", "2026-10-06", "2026-10-18"],
            "hmm_X": {"math_noterrain": (torch.rand(T, 50) > 0.5).float(), "useless": torch.randn(T, 50)},
            "hmm_terrain_off": torch.zeros(50), "experts": ["math_noterrain", "useless"]}
    p = fusion.fit_config([samp], {"experts": ["math_noterrain", "useless"], "terrain": None, "time": True,
                                   "space": False})
    assert p["w"]["useless"] < 0.05 * p["w"]["math_noterrain"]
    A = torch.tensor(p["hmm"]["A"])
    assert torch.allclose(A.sum(1), torch.ones(2), atol=1e-4)
