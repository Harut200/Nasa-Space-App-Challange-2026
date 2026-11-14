"""Fast CPU tests for scripts/detect/supervised.py (no downloads, no caches)."""
import numpy as np
import torch

from scripts.detect import supervised as S


def _ref_from_mask(lake, partial=None):
    H, W = lake.shape
    occ90 = lake.float().clone()
    if partial is not None:
        for (r, c), f in partial.items():
            occ90[r, c] = f
    one = torch.ones(H, W)
    return {"valid_frac": one, "occ_ge90_frac": occ90, "seas12_frac": occ90.clone(), "occ_any_frac": occ90.clone(),
            "ext_frac": occ90.clone(), "occ_mean": occ90 * 100}


def _disc(H, W, cy, cx, r):
    yy, xx = torch.meshgrid(torch.arange(H), torch.arange(W), indexing="ij")
    return ((yy - cy) ** 2 + (xx - cx) ** 2) < r ** 2


def test_labels_ignore_band():
    H = W = 48
    lake = _disc(H, W, 24, 24, 10)
    ref = _ref_from_mask(lake, partial={(24, 34): 0.5})   # one shoreline pixel half water, stable
    lab, frac, L = S.build_labels(ref, torch.ones(H, W, dtype=torch.bool))
    assert lab[24, 24] == 1                    # deep inside: water
    assert lab[24, 32] == -1                   # inside the lake but within 2 px of the shore: ignored
    assert lab[24, 36] == -1                   # outside, within 3 px of JRC water: ignored
    assert lab[24, 40] == 0                    # far outside: land
    assert lab[2, 2] == 0
    # the strict test rule: water is the lake eroded by 2 px, land is >3 px from any water
    from scripts.detect import ops
    exp_w = ops.erode(lake[None], 5)[0]
    assert torch.equal(lab == 1, exp_w)
    ever = ref["occ_any_frac"] > 0
    assert torch.equal(lab == 0, ~ops.dilate(ever[None], 7)[0])
    # soft fraction target on the stable half-water shoreline pixel; none on labelled pixels
    assert abs(float(frac[24, 34]) - 0.5) < 1e-6
    assert not torch.isfinite(frac[lab >= 0]).any()


def test_normalisation_gain_invariant():
    g = torch.Generator().manual_seed(0)
    x = torch.rand(5, 2, 30, 40, generator=g) * 0.8 + 0.01
    fp = torch.ones(30, 40, dtype=torch.bool)
    fp[:3] = False
    z1, _ = S.normalise_scene(x, fp)
    z2, _ = S.normalise_scene(x * 3.7, fp)
    assert torch.allclose(z1, z2, atol=1e-5)
    v = S.BASE
    dm = torch.ones(1, 5, dtype=torch.bool)
    f1 = S.features(z1[None], dm, torch.tensor([2]), v)
    f2 = S.features(z2[None], dm, torch.tensor([2]), v)
    assert f1.shape[1] == S.n_channels(v)
    assert torch.allclose(f1, f2, atol=1e-4)


def _synth_scene(seed, H=128, W=128, T=6):
    """Lakes (dark on every date except one windy date, when their HH is as bright as land) and dark non-water
    blobs (dark HH on every date, but bright HV)."""
    rng = np.random.default_rng(seed)
    lake = torch.zeros(H, W, dtype=torch.bool)
    blob = torch.zeros(H, W, dtype=torch.bool)
    for _ in range(2):
        lake |= _disc(H, W, int(rng.integers(25, H - 25)), int(rng.integers(25, W - 45)), int(rng.integers(12, 20)))
    for _ in range(3):
        b = _disc(H, W, int(rng.integers(10, H - 10)), int(rng.integers(10, W - 10)), int(rng.integers(5, 9)))
        blob |= b & ~ops_dilate(lake, 9)
    windy = int(rng.integers(1, T))
    g = torch.Generator().manual_seed(seed)
    sp = lambda: torch.exp(torch.randn(T, H, W, generator=g) * 0.3)  # noqa: E731
    hh = torch.ones(T, H, W) * sp()
    hv = torch.ones(T, H, W) * sp()
    hh = torch.where(lake[None], 0.03 * sp(), hh)
    hv = torch.where(lake[None], 0.05 * sp(), hv)
    hh[windy][lake] = (1.0 * sp()[0])[lake]
    hv[windy][lake] = (0.3 * sp()[0])[lake]
    hh = torch.where(blob[None], 0.06 * sp(), hh)
    hv = torch.where(blob[None], 0.9 * sp(), hv)
    x = torch.stack([hh, hv], 1) * float(rng.uniform(0.3, 0.7))  # unknown display stretch
    fp = torch.ones(H, W, dtype=torch.bool)
    z, _ = S.normalise_scene(x, fp)
    ref = _ref_from_mask(lake)
    lab, frac, L = S.build_labels(ref, fp)
    return {"area": f"syn{seed}", "dates": [f"d{t}" for t in range(T)], "km2": 0.03, "z": z.half(), "fp": fp,
            "lab": lab, "frac": frac.half(), "domain": L["domain"], "amb": L["amb"],
            "ref": {k: v.half() for k, v in ref.items()}, "terrain": None, "windy": windy, "blob": blob,
            "math_valid": fp}


def ops_dilate(m, k):
    from scripts.detect import ops
    return ops.dilate(m[None], k)[0]


def _ba(water, d):
    lab = d["lab"]
    T = water.shape[0]
    perm, never = (lab == 1), (lab == 0)
    rec = float(water[:, perm].float().mean())
    fpr = float(water[:, never].float().mean())
    return 0.5 * (rec + 1 - fpr)


def test_qc_drop_and_impute(monkeypatch):
    monkeypatch.setattr(S, "qc_exclusions", lambda a: ([], {"d2": ["HV"]}))
    g = torch.Generator().manual_seed(1)
    z = torch.rand(4, 2, 8, 8, generator=g).half()
    mk = lambda: {"area": "x", "dates": ["d0", "d1", "d2", "d3"], "z": z.clone(),  # noqa: E731
                  "math_water": torch.zeros(4, 8, 8, dtype=torch.bool), "math_water_terrain": None}
    d = S.apply_qc(mk(), "drop")
    assert d["dates"] == ["d0", "d1", "d3"] and d["z"].shape[0] == 3 and d["math_water"].shape[0] == 3
    assert d["keep_idx"] == [0, 1, 3] and d["qc_bad_dates"] == ["d2"]
    d = S.apply_qc(mk(), "impute")
    assert d["z"].shape[0] == 4 and torch.equal(d["z"][2, 0], z[2, 0])          # HH kept
    assert torch.allclose(d["z"][2, 1].float(), z[[0, 1, 3], 1].float().median(0).values, atol=1e-3)


def test_reported_variant_ignores_hv():
    v = S.VARIANTS[S.FINAL]
    g = torch.Generator().manual_seed(2)
    z = torch.rand(1, 5, 2, 16, 16, generator=g) + 0.05
    dm = torch.ones(1, 5, dtype=torch.bool)
    terr = torch.zeros(1, 2, 16, 16)
    f1 = S.features(z, dm, torch.tensor([1]), v, terr)
    z2 = z.clone()
    z2[:, :, 1] *= 5.0                                 # change HV only
    f2 = S.features(z2, dm, torch.tensor([1]), v, terr)
    assert f1.shape[1] == S.n_channels(v) and torch.equal(f1, f2)


def test_weak_land_loss_only_with_weight():
    out = torch.zeros(1, 2, 4, 4)
    out[:, 0] = 3.0                                    # confident water everywhere
    lab = torch.full((1, 4, 4), -1, dtype=torch.long)
    lab[0, 0, 0], lab[0, 0, 1] = 1, 0
    lab[0, 3] = -2                                     # weak land row
    frac = torch.full((1, 4, 4), float("nan"))
    fp = torch.ones(1, 4, 4, dtype=torch.bool)
    l0, _, _ = S.loss_fn(out, lab, frac, fp, 1.0, 0.0)
    l1, _, _ = S.loss_fn(out, lab, frac, fp, 1.0, 0.2)
    assert float(l1) > float(l0) + 0.1


def test_tiny_training_generalises_to_unseen_scene():
    torch.manual_seed(0)
    data = {f"syn{s}": _synth_scene(s) for s in (1, 2, 3)}
    test = _synth_scene(7)
    model, info = S.train(list(data), variant="tiny", save=False, verbose=False, data=data, dev=torch.device("cpu"))
    P, Fr = S.predict_scene(model, test, info["config"], torch.device("cpu"))
    assert P.shape == (6, 128, 128) and Fr.shape == P.shape
    ba_cnn = _ba(P.float() >= 0.5, test)
    # best single HH threshold, chosen on the TRAINING scenes
    best_t, best = None, -1
    for t in np.linspace(0.01, 2.0, 80):
        b = np.mean([_ba(d["z"][:, 0].float() < t, d) for d in data.values()])
        if b > best:
            best, best_t = b, t
    ba_thr = _ba(test["z"][:, 0].float() < best_t, test)
    assert ba_cnn > ba_thr + 0.02, (ba_cnn, ba_thr)
    # and specifically: the lake is kept on its windy date
    w = test["windy"]
    assert float((P[w].float() >= 0.5)[test["lab"] == 1].float().mean()) > 0.8
