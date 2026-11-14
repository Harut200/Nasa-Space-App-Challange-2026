"""Fusion layer: log-linear pooling of water experts + terrain prior + hidden Markov chain over dates
(+ optional spatial smoothing), with weights fitted on TUNE-split anchors and every addition passed
through the admission rule of docs/ARCHITECTURE.md section 5 on the TEST split and Lake Sevan.

    .venv/bin/python -m scripts.detect.fusion               # admission + products for all three areas
    .venv/bin/python -m scripts.detect.fusion sevan         # products for one area (re-uses the admitted config)

Write-up: docs/eda/fusion.md. Numbers: docs/eda/fusion/results.json (section "fusion").
Products: data/processed/<area>/fusion/products.pt.

Model (per pixel i, date t; s = water):
    f_k(i,t)   = logit p_k(water | data)            probabilistic expert, p clipped to [EPS, 1-EPS]
               = logit P(water | expert says m)     hard-mask expert, two values estimated on TUNE anchors
    z(i,t)     = b + sum_k w_k f_k(i,t) - c * [terrain says water impossible]       w_k, c >= 0
    q_t(i)     = sigmoid(z)        (class-balanced fit: q is the posterior under a 50/50 prior, i.e.
                                    q/(1-q) is the likelihood ratio the Markov chain needs)
    terrain    'hard': q = EPS where water_possible is false;  'soft': the c term above, c fitted.
    time       two-state HMM, emissions (q, 1-q), pi and A by Baum-Welch on TUNE-strip pixels,
               A^2 across the 27 Jul - 10 Aug outage (phantom slot; gmm.fit_hmm / gmm.forward_backward)
               -> gamma_t(i) = P(S_t = water | q_1..q_T).
    space      masked 3x3 mean of gamma inside the footprint (optional).
Weights: L2 + L1 penalised, class-balanced logistic regression (projected Adam, w >= 0) on TUNE-split
anchor pixel-dates of Tonle Sap and Lake Sevan (Ararat has no tune strip). Never on TEST.
A missing expert in an area contributes f = 0 (no evidence).
"""
from __future__ import annotations

import datetime as dt
import gc
import json
import sys
import time

import numpy as np
import torch

from scripts.detect import gmm
from scripts.detect import scoreboard as sb
from scripts.detect.stack import ROOT
from scripts.detect.tune import SEED

AREAS = sb.AREAS
TUNE_AREAS = ["tonle-sap", "sevan"]
EPS = sb.EPS
LOGIT_MAX = float(np.log((1 - EPS) / EPS))
N_ANCHOR = 120_000      # TUNE anchor pixel-dates sampled per class per area
N_HMM_PX = 30_000       # TUNE-strip pixels per area used to fit pi and A
CHUNK = 200_000         # pixels per forward-backward chunk
L2, L1 = 1e-3, 1e-3
THRESH = 0.5            # water = gamma > THRESH
# admission rule, declared before any TEST number was read
RULE = {"ba_tol": 0.001,               # balanced accuracy (Ararat: specificity) may not drop by more, any TEST area
        "far_rel": 0.10, "far_abs": 1.0,   # mountain false water (km2 ever, Sevan TEST strip + Ararat): -10 % and -1 km2
        "dip_abs": 10.0,               # Sevan windy-date dip: |dip| down by >= 10 km2
        "flip_rel": 0.10, "flip_abs": 0.005,  # flips per stable TEST anchor pixel: -10 % and -0.005
        "max_seconds": 600.0}          # per area, on this laptop CPU
PRIORS = ["terrain", "time", "space"]
NO_FUSED = [n for n in sb.REGISTRY if n != "fused"]


def logit(p):
    p = p.clamp(EPS, 1 - EPS)
    return torch.log(p) - torch.log1p(-p)


def experts_of(area_names):
    return [n for n in sb.REGISTRY if sb.REGISTRY[n]["role"] == "expert" and n in area_names]


# ================================================================== logistic pooling
def fit_pool(X, y, sw=None, l2=L2, l1=L1, iters=800, lr=0.05, nonneg=True):
    """Penalised logistic regression logit q = b + X w, w >= 0 (projected Adam). X [n,K], y [n] {0,1}.
    sw: sample weights (default: class-balanced). Returns (w [K], b)."""
    X = X.float()
    y = y.float()
    if sw is None:
        n1 = y.sum().clamp_min(1)
        n0 = (1 - y).sum().clamp_min(1)
        sw = torch.where(y > 0.5, 0.5 / n1, 0.5 / n0)
    sw = sw / sw.sum()
    K = X.shape[1]
    w = torch.zeros(K, requires_grad=True)
    b = torch.zeros(1, requires_grad=True)
    opt = torch.optim.Adam([w, b], lr=lr)
    for it in range(iters):
        opt.zero_grad()
        z = X @ w + b
        loss = (sw * torch.nn.functional.binary_cross_entropy_with_logits(z, y, reduction="none")).sum()
        loss = loss + l2 * (w ** 2).sum() + l1 * w.abs().sum()
        loss.backward()
        opt.step()
        if nonneg:
            with torch.no_grad():
                w.clamp_(min=0)
        if it == iters // 2:
            for g in opt.param_groups:
                g["lr"] = lr / 5
    return w.detach(), float(b.detach())


def two_value(x, y):
    """Calibrated mapping of a hard mask on TUNE anchors, class-balanced (50/50 prior), Laplace-smoothed:
    returns (logit P(water | says water), logit P(water | says land))."""
    x = x > 0.5
    y = y > 0.5
    tpr = (float((x & y).sum()) + 1) / (float(y.sum()) + 2)
    fpr = (float((x & ~y).sum()) + 1) / (float((~y).sum()) + 2)
    p1 = tpr / (tpr + fpr)
    p0 = (1 - tpr) / ((1 - tpr) + (1 - fpr))
    lg = lambda p: float(np.log(np.clip(p, EPS, 1 - EPS) / (1 - np.clip(p, EPS, 1 - EPS))))  # noqa: E731
    return lg(p1), lg(p0)


def balanced_brier(p, y):
    y = y > 0.5
    return 0.5 * (float(((1 - p[y]) ** 2).mean()) + float((p[~y] ** 2).mean()))


# ================================================================== features
def raw_feature(A, name, t=None):
    """Probabilistic expert -> logit p; hard mask -> {0,1} float (mapped later by its two-value calibration)."""
    c = A.E[name]
    if c["p"] is not None:
        v = c["p"] if t is None else c["p"][t]
        return logit(v.float())
    v = c["mask"] if t is None else c["mask"][t]
    return v.float()


def feature(A, name, params, t=None):
    """Calibrated feature f_k for one date (or all): logit for prob experts, two-value logit for masks;
    0 (no evidence) if the expert is missing in this area."""
    if name not in A.E:
        shape = (A.H, A.W) if t is not None else (A.T, A.H, A.W)
        return torch.zeros(shape)
    r = raw_feature(A, name, t)
    if A.E[name]["p"] is None:
        l1, l0 = params["two_value"][name]
        return torch.where(r > 0.5, torch.tensor(l1), torch.tensor(l0))
    return r


def collect(area):
    """TUNE-split samples of one area: anchor pixel-dates (features for every expert, label) and a random
    set of TUNE-strip pixels with their full time series (for the Markov chain)."""
    A = sb.Area(area, names=NO_FUSED, verbose=True)
    g = torch.Generator().manual_seed(SEED + AREAS.index(area))
    names = experts_of(list(A.E))
    tune = A.strips["tune"]
    out = {"area": area, "experts": names, "dates": A.dates}
    idx = {}
    for cls, m in (("perm", A.lab["perm"] & tune), ("never", A.lab["never"] & tune)):
        pix = torch.nonzero(m.flatten()).flatten()
        n_all = pix.numel() * A.T
        k = min(N_ANCHOR, n_all)
        sel = torch.randperm(n_all, generator=g)[:k]
        idx[cls] = (sel % A.T, pix[sel // A.T])     # (t, flat pixel)
    tt = torch.cat([idx["perm"][0], idx["never"][0]])
    pp = torch.cat([idx["perm"][1], idx["never"][1]])
    out["y"] = torch.cat([torch.ones(idx["perm"][0].numel()), torch.zeros(idx["never"][0].numel())])
    out["X"] = {n: raw_feature(A, n).reshape(A.T, -1)[tt, pp].clone() for n in names}
    out["is_prob"] = {n: A.E[n]["p"] is not None for n in names}
    out["terrain_off"] = (~A.wp).flatten()[pp].float()
    tp = torch.nonzero((tune & A.foot).flatten()).flatten()
    hp = tp[torch.randperm(tp.numel(), generator=g)[:N_HMM_PX]]
    out["hmm_X"] = {n: raw_feature(A, n).reshape(A.T, -1)[:, hp].clone() for n in names}
    out["hmm_terrain_off"] = (~A.wp).flatten()[hp].float()
    print(f"  [{area}] TUNE anchors: {int(out['y'].sum())} water / {int((1 - out['y']).sum())} never pixel-dates; "
          f"{hp.numel()} px for the Markov chain")
    del A
    gc.collect()
    return out


# ================================================================== configuration -> parameters
def area_experts(area):
    """Experts whose product file exists for the area (what sb.Area will load)."""
    return [n for n in sb.available(area) if sb.REGISTRY[n]["role"] == "expert"]


def fit_config(samples, cfg, sub=True):
    """cfg: {"experts": [...], "terrain": None|"hard"|"soft", "time": bool, "space": bool}.
    Fits two-value mappings, pooling weights (and c for soft terrain), and the Markov chain, on TUNE only,
    using the TUNE areas in which every expert of cfg exists. For an area that lacks some of the experts,
    a separate fit on the experts it has is stored in params["sub"][area] (a missing expert is NOT set to
    'no evidence' inside a model whose bias was fitted with it present)."""
    ex = cfg["experts"]
    params = {"two_value": {}, "cfg": cfg}
    samples_all = samples
    samples = [s for s in samples if all(n in s["X"] for n in ex)] or samples
    params["fit_areas"] = [s.get("area", "?") for s in samples]
    if sub:
        params["sub"] = {}
        for a in AREAS:
            have = [n for n in ex if n in area_experts(a)]
            if have != ex and have:
                params["sub"][a] = fit_config(samples_all, {**cfg, "experts": have}, sub=False)
    ys = torch.cat([s["y"] for s in samples])
    for n in ex:
        if not any(n in s["X"] and not s["is_prob"][n] for s in samples):
            continue
        xs = torch.cat([s["X"][n] for s in samples if n in s["X"]])
        yy = torch.cat([s["y"] for s in samples if n in s["X"]])
        params["two_value"][n] = two_value(xs, yy)

    def cal(s, n, x):
        if n not in s["X"]:
            return torch.zeros_like(x if torch.is_tensor(x) else s["y"])
        if s["is_prob"][n]:
            return x
        l1_, l0_ = params["two_value"][n]
        return torch.where(x > 0.5, torch.tensor(l1_), torch.tensor(l0_))

    cols = []
    for n in ex:
        cols.append(torch.cat([cal(s, n, s["X"][n]) if n in s["X"] else torch.zeros_like(s["y"]) for s in samples]))
    if cfg.get("terrain") == "soft":
        cols.append(-torch.cat([s["terrain_off"] for s in samples]))
    X = torch.stack(cols, 1) if cols else torch.zeros(ys.numel(), 0)
    # balanced per area and class: each (area, class) cell gets equal total weight
    sw = torch.cat([torch.where(s["y"] > 0.5, 1.0 / s["y"].sum().clamp_min(1), 1.0 / (1 - s["y"]).sum().clamp_min(1))
                    for s in samples])
    w, b = fit_pool(X, ys, sw)
    params["w"] = {n: float(w[i]) for i, n in enumerate(ex)}
    params["c_terrain"] = float(w[-1]) if cfg.get("terrain") == "soft" else None
    params["b"] = b
    z = X @ w + b
    q = torch.sigmoid(z)
    if cfg.get("terrain") == "hard":
        toff = torch.cat([s["terrain_off"] for s in samples]) > 0.5
        q = torch.where(toff, torch.tensor(EPS), q)
    params["tune_brier_bal"] = round(balanced_brier(q, ys), 5)
    if cfg.get("time"):
        lbs = []
        for s in samples:
            T, N = s["hmm_X"][ex[0]].shape if ex and ex[0] in s["hmm_X"] else (len(s["dates"]), s["hmm_terrain_off"].numel())
            zz = torch.full((T, N), b)
            for n in ex:
                if n in s["hmm_X"]:
                    zz += params["w"][n] * cal(s, n, s["hmm_X"][n])
            q2 = _apply_terrain(torch.sigmoid(zz), zz, s["hmm_terrain_off"][None] > 0.5, params)
            lbs.append(_log_emission(q2))
        lb = torch.cat(lbs, 2)
        dts = gmm.dt_from_dates(samples[0]["dates"])
        A0 = torch.tensor([[0.95, 0.05], [0.05, 0.95]])
        hm = gmm.fit_hmm(lb, dts, prior={"A": torch.tensor([[1.0, 0.0], [0.0, 1.0]]) * 10, "pi": torch.ones(2)},
                         n_iter=40, init=(torch.tensor([0.5, 0.5]), A0))
        params["hmm"] = {"pi": hm.pi.tolist(), "A": hm.A.tolist(), "dt_steps": dts, "loglik": hm.history[-1]}
    return params


def _apply_terrain(q, z, toff, params):
    cfg = params["cfg"]
    if cfg.get("terrain") == "hard":
        return torch.where(toff, torch.tensor(EPS), q)
    if cfg.get("terrain") == "soft":
        return torch.sigmoid(z - params["c_terrain"] * toff.float())
    return q


def _log_emission(q):
    """[T,N] P(water) -> [T,2,N] log emissions for states (water, not water)."""
    q = q.clamp(EPS, 1 - EPS)
    return torch.stack([torch.log(q), torch.log1p(-q)], 1)


def smooth_time(q, hmm, foot_idx=None):
    """Forward-backward over dates per pixel, chunked. q [T,N] -> gamma_water [T,N]."""
    T, N = q.shape
    lpi = torch.log(torch.tensor(hmm["pi"]).clamp_min(1e-12))
    lA = torch.log(torch.tensor(hmm["A"]).clamp_min(1e-12))
    out = torch.empty(T, N)
    for i in range(0, N, CHUNK):
        lb, pos = gmm._expand(_log_emission(q[:, i:i + CHUNK]), hmm["dt_steps"])
        g, _, _ = gmm.forward_backward(lb, lpi, lA)
        out[:, i:i + CHUNK] = g[pos, 0]
        del lb, g
    return out


def smooth_space(g, foot):
    """Masked 3x3 mean of gamma inside the footprint. g [T,H,W]."""
    f = foot.float()[None, None]
    num = torch.nn.functional.avg_pool2d((g * foot)[:, None], 3, 1, 1, count_include_pad=False)
    den = torch.nn.functional.avg_pool2d(f.expand(g.shape[0], 1, -1, -1), 3, 1, 1, count_include_pad=False)
    return torch.where(foot[None], (num / den.clamp_min(1e-6))[:, 0], torch.zeros_like(g))


def apply(A, params):
    """Fused P(water) [T,H,W] float32 for area A with fitted params (per date pooling, then time, space)."""
    if A.area in params.get("sub", {}):
        params = params["sub"][A.area]
    cfg = params["cfg"]
    T, H, W = A.T, A.H, A.W
    q = torch.empty(T, H, W)
    miss = [n for n in cfg["experts"] if n not in A.E]
    if miss:
        print(f"  [{A.area}] WARNING experts missing at apply time, treated as no evidence: {miss}")
    toff = ~A.wp
    for t in range(T):
        z = torch.full((H, W), params["b"])
        for n in cfg["experts"]:
            if n in A.E:
                z += params["w"][n] * feature(A, n, params, t)
        q[t] = _apply_terrain(torch.sigmoid(z), z, toff, params)
    if cfg.get("time"):
        fi = torch.nonzero(A.foot.flatten()).flatten()
        qf = q.reshape(T, -1)[:, fi]
        gf = smooth_time(qf, params["hmm"])
        q = torch.zeros(T, H * W)
        q[:, fi] = gf
        q = q.reshape(T, H, W)
        del qf, gf
    if cfg.get("space"):
        q = smooth_space(q, A.foot)
    return q * A.foot


# ================================================================== evaluation and admission
def eval_config(params, areas=AREAS):
    """Admission metrics of one fitted config: per TEST area balanced accuracy (Ararat: specificity),
    mountain false water (Sevan TEST strip + Ararat, km2 ever), Sevan windy dip, flips on stable anchors."""
    m = {"per_area": {}, "seconds": {}}
    for a in areas:
        t0 = time.time()
        A = sb.Area(a, names=NO_FUSED, verbose=False)
        p = apply(A, params)
        o = sb.evaluate(A, p > THRESH, light=True)
        m["seconds"][a] = round(time.time() - t0, 1)
        o["n_perm"], o["n_never"] = A.reg["test"].n_perm, A.reg["test"].n_never
        m["per_area"][a] = o
        del A, p
        gc.collect()
    pa = m["per_area"]
    m["ba"] = {a: sb.ba_or_spec(pa[a]) for a in areas}
    m["far_km2"] = round(sum(pa[a]["far_fp_km2_test_ever"] for a in areas if a in ("sevan", "ararat")), 2)
    m["sevan_dip"] = pa["sevan"]["sevan_windy_dip_km2"] if "sevan" in pa else None
    m["sevan_mae_calm"] = pa["sevan"]["sevan_mae_calm_km2"] if "sevan" in pa else None
    nst = sum(pa[a]["n_perm"] + pa[a]["n_never"] for a in areas)
    m["flips"] = round(sum(pa[a]["flips_stable"] * (pa[a]["n_perm"] + pa[a]["n_never"]) for a in areas) / max(nst, 1), 5)
    m["max_seconds"] = max(m["seconds"].values())
    if "tonle-sap" in pa:
        m["tonle_rank_rho"] = pa["tonle-sap"].get("rank_rho")
        m["tonle_last_km2"] = pa["tonle-sap"]["water_km2"][-1]
        m["tonle_late_inside_ext"] = pa["tonle-sap"].get("late_share_inside_max_extent")
    m["val_far_km2"] = round(sum(pa[a].get("val_far_fp_km2_ever") or 0 for a in areas), 2)
    m["val_ba"] = {a: pa[a]["val_balanced_acc"] for a in areas if pa[a].get("val_balanced_acc") is not None}
    return m


def admit(cur, new, rule=RULE):
    """The admission rule (ARCHITECTURE.md section 5), mechanically. Returns (accept, reasons dict)."""
    r = {}
    drops = {a: new["ba"][a] - cur["ba"][a] for a in cur["ba"] if cur["ba"][a] is not None and new["ba"].get(a) is not None}
    r["ba_ok"] = all(d >= -rule["ba_tol"] for d in drops.values())
    r["ba_delta"] = {a: round(d, 5) for a, d in drops.items()}
    dfar = cur["far_km2"] - new["far_km2"]
    r["far_improves"] = bool(dfar >= rule["far_abs"] and dfar >= rule["far_rel"] * max(cur["far_km2"], 1e-9))
    r["dip_improves"] = bool(cur["sevan_dip"] is not None and abs(cur["sevan_dip"]) - abs(new["sevan_dip"]) >= rule["dip_abs"])
    dfl = cur["flips"] - new["flips"]
    r["flips_improves"] = bool(dfl >= rule["flip_abs"] and dfl >= rule["flip_rel"] * max(cur["flips"], 1e-9))
    r["fv_stability"] = "not measured (two-state water model)"
    r["weakness_improved"] = [k for k in ("far_improves", "dip_improves", "flips_improves") if r[k]]
    r["fast_ok"] = bool(new["max_seconds"] <= rule["max_seconds"])
    return bool(r["ba_ok"] and r["weakness_improved"] and r["fast_ok"]), r


def _row(name, cur, new, ok, why, params):
    return {"addition": name, "accepted": ok,
            "ba_delta_pp": {a: round(100 * d, 3) for a, d in why["ba_delta"].items()},
            "mountain_far_km2": [cur["far_km2"], new["far_km2"]],
            "sevan_windy_dip_km2": [cur["sevan_dip"], new["sevan_dip"]],
            "sevan_calm_mae_km2": [cur["sevan_mae_calm"], new["sevan_mae_calm"]],
            "flips_per_stable_px": [cur["flips"], new["flips"]],
            "weakness_improved": why["weakness_improved"], "ba_ok": why["ba_ok"], "fast_ok": why["fast_ok"],
            "seconds_max_area": new["max_seconds"],
            "guard_tonle_rank_rho": [cur.get("tonle_rank_rho"), new.get("tonle_rank_rho")],
            "guard_tonle_last_date_km2": [cur.get("tonle_last_km2"), new.get("tonle_last_km2")],
            "hmm_A": params.get("hmm", {}).get("A") if params.get("hmm") else None,
            "weights": {k: round(v, 4) for k, v in params["w"].items()},
            "c_terrain": params.get("c_terrain"), "b": round(params["b"], 4)}


def run_admission(samples, all_experts):
    """Greedy, in a fixed declared order: priors (terrain, time, space), then experts in registry order."""
    base = {"experts": ["math_noterrain"], "terrain": None, "time": False, "space": False}
    t0 = time.time()
    p_cur = fit_config(samples, base)
    cur = eval_config(p_cur)
    print(f"  base (math alone): BA {cur['ba']} far {cur['far_km2']} dip {cur['sevan_dip']} flips {cur['flips']}")
    cfg = dict(base)
    rows = []
    # terrain: both variants against the same base; if both pass, the VALIDATION strips choose
    tv = {}
    for name, var in (("terrain (hard mask)", "hard"), ("terrain (soft, fitted)", "soft")):
        p_new = fit_config(samples, {**cfg, "terrain": var})
        new = eval_config(p_new)
        ok, why = admit(cur, new)
        rows.append(_row(name, cur, new, ok, why, p_new) | {"val_far_km2": new["val_far_km2"],
                                                           "val_ba": new["val_ba"]})
        print(f"  {'pass  ' if ok else 'reject'} {name}: BA d {why['ba_delta']} far {cur['far_km2']}->{new['far_km2']} "
              f"flips {cur['flips']}->{new['flips']} val far {new['val_far_km2']}")
        if ok:
            tv[var] = (new, p_new)
    if tv:
        pick = min(tv, key=lambda v: (tv[v][0]["val_far_km2"], -np.mean(list(tv[v][0]["val_ba"].values())),
                                      v != "hard"))
        cfg = {**cfg, "terrain": pick}
        cur, p_cur = tv[pick]
        for r in rows:
            r["accepted"] = r["addition"].startswith("terrain") and (("hard" in r["addition"]) == (pick == "hard"))
            r["chosen_on"] = "validation strips (lower far-from-record water, then balanced accuracy)"
    order = [("time (2-state HMM)", {"time": True}), ("space (3x3 smoothing)", {"space": True})]
    order += [(f"expert: {n}", {"add": n}) for n in all_experts if n != "math_noterrain"]
    for name, ch in order:
        new_cfg = dict(cfg)
        if "add" in ch:
            new_cfg["experts"] = cfg["experts"] + [ch["add"]]
        else:
            new_cfg.update(ch)
        p_new = fit_config(samples, new_cfg)
        new = eval_config(p_new)
        ok, why = admit(cur, new)
        rows.append(_row(name, cur, new, ok, why, p_new))
        print(f"  {'ACCEPT' if ok else 'reject'} {name}: BA d {why['ba_delta']} far {cur['far_km2']}->{new['far_km2']} "
              f"dip {cur['sevan_dip']}->{new['sevan_dip']} flips {cur['flips']}->{new['flips']} {why['weakness_improved']} "
              f"| rho {cur.get('tonle_rank_rho')}->{new.get('tonle_rank_rho')} TS last {cur.get('tonle_last_km2')}->{new.get('tonle_last_km2')}")
        if ok:
            cfg, cur, p_cur = new_cfg, new, p_new
    # for reference: everything at once, no rule; and each expert alone added to math (marginal vs base)
    full_cfg = {"experts": all_experts, "terrain": "hard", "time": True, "space": False}
    p_full = fit_config(samples, full_cfg)
    full = eval_config(p_full)
    base_m = eval_config(fit_config(samples, base))
    marg = []
    for n in all_experts:
        if n == "math_noterrain":
            continue
        p_new = fit_config(samples, {**base, "experts": base["experts"] + [n]})
        new = eval_config(p_new)
        ok, why = admit(base_m, new)
        marg.append(_row(f"math + {n} (no priors)", base_m, new, ok, why, p_new))
        print(f"  marginal {n}: BA d {why['ba_delta']} far {base_m['far_km2']}->{new['far_km2']} "
              f"dip {base_m['sevan_dip']}->{new['sevan_dip']} flips {base_m['flips']}->{new['flips']}")
    return {"base": base, "base_metrics": _summ(base_m), "accepted_config": cfg, "final_metrics": _summ(cur),
            "final_params": _pjson(p_cur), "table": rows, "marginal_vs_math_alone": marg,
            "all_experts_no_rule": {"config": full_cfg, "metrics": _summ(full), "params": _pjson(p_full)},
            "seconds": round(time.time() - t0, 1)}, p_cur, p_full


def _summ(m):
    return {k: m[k] for k in ("ba", "far_km2", "sevan_dip", "sevan_mae_calm", "flips", "seconds", "val_far_km2",
                                    "tonle_rank_rho", "tonle_last_km2", "tonle_late_inside_ext")}


def _pjson(p):
    return {k: v for k, v in p.items() if k != "cfg"} | {"cfg": p["cfg"]}


# ================================================================== products
def fuse(area, params=None):
    """Fused products for one area with the admitted configuration (re-fitted from results.json if needed)."""
    if params is None:
        params = load_params()
    t0 = time.time()
    A = sb.Area(area, names=NO_FUSED, verbose=False)
    p = apply(A, params)
    km2 = A.km2
    water = p > THRESH
    eff = params.get("sub", {}).get(area, params)   # the fit this area actually uses
    out = {"p_water": p.half(), "water": water, "water_km2": (water.sum((1, 2)).float() * km2),
           "water_km2_expected": (p.sum((1, 2)) * km2).float(),
           "water_km2_lo": ((p > 0.75).sum((1, 2)).float() * km2), "water_km2_hi": ((p > 0.25).sum((1, 2)).float() * km2),
           "threshold": THRESH, "weights": eff["w"], "bias": eff["b"], "c_terrain": eff.get("c_terrain"),
           "two_value": eff["two_value"], "hmm": eff.get("hmm"), "accepted": params["cfg"],
           "experts_used_here": eff["cfg"]["experts"], "fit_areas": eff.get("fit_areas"),
           "dates": A.dates, "bounds": A.bounds, "km2_per_px": km2, "valid": A.foot,
           "note": "p_water = fused P(open water), smoothed in time if 'time' is accepted; water = p_water > threshold; "
                   "water_km2_expected = sum of p_water x km2; lo/hi = area at p > 0.75 / p > 0.25. See docs/eda/fusion.md."}
    out["runtime_s"] = round(time.time() - t0, 1)
    od = sb.PROC / area / "fusion"
    od.mkdir(parents=True, exist_ok=True)
    torch.save(out, od / "products.pt")
    print(f"  [{area}] wrote fusion/products.pt in {out['runtime_s']} s; water km2 "
          f"{[round(float(v)) for v in out['water_km2']]}")
    res = {k: (v.tolist() if torch.is_tensor(v) else v) for k, v in out.items()
           if k in ("water_km2", "water_km2_expected", "water_km2_lo", "water_km2_hi", "runtime_s")}
    del A, p, out
    gc.collect()
    return res


def load_params():
    r = json.loads((sb.OUT / "results.json").read_text())["fusion"]["admission"]["final_params"]
    return r


# ================================================================== figures
def fig_weights(adm):
    plt = sb._plt()
    full = adm["all_experts_no_rule"]["params"]
    fin = adm["final_params"]
    names = list(full["w"])
    fig, ax = plt.subplots(figsize=(7.5, 3.8), dpi=72)
    x = np.arange(len(names))
    ax.bar(x - 0.2, [full["w"][n] for n in names], 0.4, label="all experts (+ hard terrain, time), no admission")
    ax.bar(x + 0.2, [fin["w"].get(n, 0) for n in names], 0.4, label="admitted configuration")
    ax.set_xticks(x, names, rotation=30, ha="right", fontsize=8)
    ax.set_ylabel("pooling weight $w_k$ (on logit $p_k$)")
    ax.set_title("Fitted weights (TUNE anchors, Tonle Sap + Sevan; L1+L2, w ≥ 0)", fontsize=9)
    ax.legend(fontsize=7); ax.grid(alpha=0.3, axis="y")
    sb._save(fig, "weights.png")


def fig_admission(adm):
    plt = sb._plt()
    rows = [r for r in adm["table"] if "skipped" not in r] + adm.get("marginal_vs_math_alone", [])
    if not rows:
        return
    fig, axs = plt.subplots(1, 3, figsize=(13, 0.45 * len(rows) + 1.6), dpi=72, sharey=True)
    y = np.arange(len(rows))
    lab = [r["addition"] + ("  ✓" if r["accepted"] else "") for r in rows]
    for ax, k, t in zip(axs, ("mountain_far_km2", "sevan_windy_dip_km2", "flips_per_stable_px"),
                        ("mountain false water, km² ever (Sevan TEST + Ararat)", "Sevan windy dip, km²",
                         "flips per stable TEST anchor px")):
        before = [r[k][0] for r in rows]; after = [r[k][1] for r in rows]
        ax.barh(y + 0.2, before, 0.4, color="#bbb", label="before")
        ax.barh(y - 0.2, after, 0.4, color=["#1b9e77" if r["accepted"] else "#d95f02" for r in rows], label="with addition")
        ax.set_title(t, fontsize=8); ax.grid(alpha=0.3, axis="x")
    axs[0].set_yticks(y, lab, fontsize=7); axs[0].invert_yaxis(); axs[0].legend(fontsize=7)
    fig.suptitle("Admission rule, one addition at a time (green = accepted)", fontsize=9)
    sb._save(fig, "admission.png")


# ================================================================== main
def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    areas = [a for a in argv if a in AREAS]
    t0 = time.time()
    if areas:  # products only, with the admitted configuration
        for a in areas:
            fuse(a)
        return
    samples = [collect(a) for a in TUNE_AREAS]
    all_experts = [n for n in sb.REGISTRY if sb.REGISTRY[n]["role"] == "expert"
                   and any(n in s["experts"] for s in samples)]
    # an expert not present in any TUNE area cannot be weighted: report it
    print("experts offered:", all_experts)
    adm, p_fin, p_full = run_admission(samples, all_experts)
    del samples
    gc.collect()
    res = {"generated": dt.datetime.now().isoformat(timespec="seconds"), "rule": RULE, "threshold": THRESH,
           "penalty": {"l2": L2, "l1": L1}, "n_anchor_per_class_area": N_ANCHOR, "n_hmm_px_per_area": N_HMM_PX,
           "experts_offered": all_experts, "admission": adm}
    sb.merge_results("fusion", res)
    res["products"] = {}
    for a in AREAS:
        res["products"][a] = fuse(a, p_fin)
    res["seconds"] = round(time.time() - t0, 1)
    sb.merge_results("fusion", res)
    fig_weights(adm)
    fig_admission(adm)
    print(f"fusion done in {res['seconds']} s")


if __name__ == "__main__":
    main()
