"""Synthetic checks for the tuning procedure (no files, no network)."""
import numpy as np
import torch

from scripts.detect import ops, tune


def _ref(H=60, W=60):
    """Lake (rows/cols 20..39) permanently water; a ring around it sometimes water; the rest never."""
    z = torch.zeros(H, W)
    occ90 = z.clone(); occ90[20:40, 20:40] = 1.0
    ever = z.clone(); ever[15:45, 15:45] = 1.0
    occ = z.clone(); occ[15:45, 15:45] = 40.0; occ[20:40, 20:40] = 99.0
    return {"valid_frac": torch.ones(H, W), "occ_ge90_frac": occ90, "seas12_frac": occ90.clone(),
            "occ_any_frac": ever, "ext_frac": ever.clone(), "occ_mean": occ}


def test_label_masks_shapes_and_bands():
    ref = _ref()
    lab = tune.build_labels(ref, torch.ones(60, 60, dtype=torch.bool), shore_px=2, never_dist_px=3)
    # permanent = lake shrunk by 2 px
    assert lab["perm"].sum() == 16 * 16 and lab["perm"][22, 22] and not lab["perm"][21, 21]
    # never = >= 3 px from any ever-water pixel (ever spans 15..44)
    assert not lab["never"][12, 30] and lab["never"][11, 30]
    # partition of the domain, no overlap
    tot = lab["perm"].int() + lab["never"].int() + lab["amb"].int()
    assert torch.equal(tot, torch.ones(60, 60, dtype=torch.int32))
    # pixels outside the footprint are unlabelled
    fp = torch.ones(60, 60, dtype=torch.bool); fp[:, :10] = False
    lab2 = tune.build_labels(ref, fp)
    assert not lab2["never"][:, :10].any() and not lab2["amb"][:, :10].any()


def test_split_masks_disjoint_with_buffer():
    sm = tune.split_masks(10, 100, {"tune": (0, 0.6), "val": (0.6, 0.7), "test": (0.7, 1.0)}, buf=10)
    tot = sum(m.int() for m in sm.values())
    assert tot.max() == 1
    assert (tot[0] == 0).sum() == 20  # two 10 px buffers


def test_water_fast_equals_ops():
    g = torch.Generator().manual_seed(0)
    L = torch.rand(3, 2, 50, 50, generator=g) * 0.4
    valid = torch.ones(50, 50, dtype=torch.bool); valid[:5] = False
    for strong, weak, hv_max, mb in [(0.06, 0.12, 0.35, 4), (0.1, 0.2, 1.0, 1), (0.05, 0.3, 0.3, 9)]:
        fast = tune.water_fast(L, valid, strong, weak, hv_max, 0.0, mb)
        kn = {"water_hh_strong": strong, "water_hh_weak": weak, "water_hv_max": hv_max, "open_k": 0, "min_blob_px": mb}
        ref = ops.water_masks(L, valid[None].expand(3, -1, -1), kn)
        assert torch.equal(fast, ref)


def test_block_bootstrap_ci_contains_truth_and_shrinks():
    H = W = 80
    ref = _ref(H, W)
    ref["occ_ge90_frac"][:] = 0; ref["seas12_frac"][:] = 0
    ref["occ_ge90_frac"][10:70, 10:40] = 1; ref["seas12_frac"][10:70, 10:40] = 1
    ref["occ_any_frac"][:] = 0; ref["ext_frac"][:] = 0
    ref["occ_any_frac"][5:75, 5:45] = 1; ref["ext_frac"][5:75, 5:45] = 1
    lab = tune.build_labels(ref, torch.ones(H, W, dtype=torch.bool))
    R = tune.Region(lab, torch.ones(H, W, dtype=torch.bool), ref["occ_mean"], 1.0, 1)
    g = torch.Generator().manual_seed(1)
    water = (torch.rand(1, H, W, generator=g) < 0.8) & lab["perm"][None]   # recall 0.8
    water |= (torch.rand(1, H, W, generator=g) < 0.05) & lab["never"][None]  # FPR 0.05
    m = R.metrics(water)
    assert abs(m["recall_perm"] - 0.8) < 0.03 and abs(m["fpr_never"] - 0.05) < 0.02
    ci = R.bootstrap(water, 200, torch.Generator().manual_seed(2))
    lo, hi = ci["recall_perm"]
    assert lo <= m["recall_perm"] <= hi and hi - lo < 0.1
    # a constant map has a degenerate interval
    ci0 = R.bootstrap(lab["perm"][None].clone(), 50, torch.Generator().manual_seed(3))
    assert ci0["recall_perm"] == [1.0, 1.0] and ci0["fpr_never"] == [0.0, 0.0]


def test_objective_peaks_at_true_threshold():
    """Water at brightness 0.05, land at 0.30, noise; labels from the truth. J must peak near the gap."""
    g = torch.Generator().manual_seed(4)
    H = W = 80
    ref = _ref(H, W)
    lab = tune.build_labels(ref, torch.ones(H, W, dtype=torch.bool))
    truth = torch.zeros(H, W, dtype=torch.bool); truth[20:40, 20:40] = True
    T = 4
    L = torch.full((T, 2, H, W), 0.30)
    L[:, :, truth] = 0.05
    L = (L + 0.03 * torch.randn(L.shape, generator=g)).clamp(0, 1)
    R = tune.Region(lab, torch.ones(H, W, dtype=torch.bool), ref["occ_mean"], 1.0, T)
    thr = np.round(np.arange(0.02, 0.40, 0.02), 3)
    J = [tune.objective([R.metrics(tune.water_fast(L, torch.ones(H, W, dtype=torch.bool), t, t, 1.0))])
         for t in thr]
    best = thr[int(np.argmax(J))]
    assert 0.1 <= best <= 0.25, (best, J)
    assert J[int(np.argmax(J))] > J[0] and J[int(np.argmax(J))] > J[-1]


def test_avg_rank_ties():
    r = tune.avg_rank(torch.tensor([3.0, 1.0, 3.0, 2.0]))
    assert r.tolist() == [3.5, 1.0, 3.5, 2.0]
