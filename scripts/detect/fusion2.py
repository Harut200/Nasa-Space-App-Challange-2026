"""Fusion v2: floodplain-aware, three states (open water, water under vegetation, land), a time model that
can follow a flood, and an admission rule with a floodplain criterion and a "no flood eaten" guard.

    .venv/bin/python -m scripts.detect.fusion2                 # admission + products + fresh hold-out + figures
    .venv/bin/python -m scripts.detect.fusion2 sevan tonle-sap # products only, with the admitted configuration

Write-up: docs/eda/fusion2.md. Numbers: docs/eda/fusion2/results.json. Products: data/processed/<area>/fusion2/.
Reuses fusion.py (v1) for the per-date pooling of experts (log-linear, TUNE anchors, w >= 0, L1+L2, per-area
refit on the experts an area has) and scoreboard.py for the reduced, cached inputs and the anchor metrics.

Model (pixel i, date t, states s in {W = open water, V = water under vegetation, L = land}):
    q_t(i)   fused P(open water | experts) under a 50/50 prior (v1 pooling, hard terrain mask), so
             q/(1-q) is a likelihood ratio W : not-W.
    r_t(i)   = R_LO + (R_HI - R_LO) * fv_score_t(i): flooded-vegetation evidence, read as V : L likelihood ratio
             r/(1-r) (floodveg.py score in [0,1]; W and L share the "no double bounce" likelihood).
    emission b_t(i) = ( q (1-r),  (1-q) r,  (1-q)(1-r) )                      for (W, V, L)
    optional floodplain-regime term (config "loose"): logit q += R(i) * (a * HH_t(i) + c), a, c fitted on the
             Tonle Sap TUNE strip against the hydro expected extent (weak supervision, spatial hold-out).
    time     3-state hidden Markov chain over dates with an input-dependent transition matrix
             A_{k(t,i)}, k = (interval [a], darkening flag [b], regime [c]) -- an input-output HMM with discrete
             inputs -- fitted by Baum-Welch per area on its own pixels (unsupervised, no labels), partial
             pooling of the A_k towards the pooled matrix, outage = phantom slot (A applied twice).
    space    optional masked 3x3 mean of the state posteriors.
Regime R (reference-free): flat (slope < 1 deg), low (height above the local low < 5 m), water possible, and
temporally variable (inter-quartile range of HH over the dates > 0.08). No JRC, no hydro.
"""
from __future__ import annotations

import copy
import datetime as dt
import gc
import json
import sys
import time

import numpy as np
import scipy.ndimage as ndi
import torch

from scripts.detect import fusion as f1
from scripts.detect import gmm
from scripts.detect import scoreboard as sb
from scripts.detect.stack import ROOT
from scripts.detect.tune import SEED, Region, avg_rank, build_labels  # noqa: F401

AREAS = sb.AREAS
FRESH = ["inner-niger", "poyang", "bangladesh-haor", "urmia", "sudd", "mekong-delta"]
# the other app places (crossscene inputs exist): products + the same evaluation, reported separately
EXTRA = ["lake-chad", "nile-delta", "okavango", "pantanal", "rondonia", "sindh-indus"]
OUT = ROOT / "docs/eda/fusion2"
PROC = sb.PROC
CACHE = sb.CACHE
EPS = sb.EPS
S = 3
W_, V_, L_ = 0, 1, 2
STATE_NAMES = ["open water", "water under vegetation", "land"]
THRESH = 0.5
R_LO, R_HI = 0.05, 0.95          # flooded-vegetation evidence -> r in [0.05, 0.95] (likelihood ratio 1:19 .. 19:1)
R_NONE = 0.02                    # no flooded-vegetation product: V state effectively off
N_FIT = 40_000                   # pixels per area for Baum-Welch (half regime R, half the rest when both exist)
N_ITER = 40
CHUNK = 150_000
SHRINK = 0.3                     # partial pooling: A_k = (1 - SHRINK) * A_k(MLE) + SHRINK * A_pooled
DIRICHLET = 2.0                  # pseudo-count per transition cell (all rows), keeps every A_k > 0
REGIME = {"slope_max": 1.0, "relh_max": 5.0, "iqr_min": 0.08}
DARK_DROP = 0.03                 # darkening flag: stats change event AND HH(t) - HH(t-1) < -DARK_DROP
DARK_INTO = 0.12                 # ... AND HH(t) below the math detector's weak threshold (into the water range)
SEVAN_KM2 = sb.PUBLISHED_SEVAN_KM2
SEEDED = "seeded"                # the self-calibrating detector (crossscene.py), offered as an expert in amendment 2
SCENE_KNOBS = ROOT / "docs/eda/crossscene/scene_knobs.json"

# ------------------------------------------------------------------ the admission rule (declared before TEST)
RULE2 = {
    # lakes and mountains: keep the v1 gains (compared with the v1 fused product, every addition)
    "ba_tol": 0.001,              # TEST balanced accuracy (Ararat: specificity) may not drop by more, any area
    "far_tol_rel": 0.10, "far_tol_abs": 1.0,   # mountain false water may not rise by more than max(1 km2, 10 %)
    "dip_max_km2": 5.0,           # Sevan windy-date |dip| must stay <= 5 km2 (v1: 0.7; math: 66)
    "mae_tol_km2": 5.0,           # Sevan calm-date largest-body error may not rise by more than 5 km2 over v1
    "flips_max": 0.005,           # flips per stable TEST anchor pixel must stay <= 0.005 (v1: 0.0009; math 0.018)
    # floodplain (Tonle Sap): may not degrade, relative to the configuration accepted so far
    "spearman_tol": 0.02,         # Spearman(area series, altimeter level) may not drop by more
    "rho_amb_tol": 0.01,          # rank consistency in the ambiguous zone (TEST strip) may not drop by more
    "hydro_err_tol": 0.01,        # mean |area / hydro expected - 1| may not rise by more (absolute)
    "hydro_iou_tol": 0.005,       # mean IoU with the hydro expected set (TEST strip) may not drop by more
    "guard_tol": 0.02,            # "no flood eaten": area_t >= (1 - tol) * core_t on every date (hard)
    # what counts as an improvement (at least one, from either terrain)
    "imp_far_rel": 0.10, "imp_far_abs": 1.0, "imp_dip": 2.0, "imp_flips_rel": 0.10, "imp_flips_abs": 0.0002,
    "imp_spearman": 0.02, "imp_rho_amb": 0.02, "imp_hydro_err": 0.02, "imp_hydro_iou": 0.01,
    "max_seconds": 600.0,
    # amendment 1 (written after the first admission run, see fusion2.md): a capability addition -- the
    # three-state model, which adds the water-under-vegetation layer and has no metric of its own here -- is
    # admitted when nothing degrades (no improvement clause).
    "capability_additions": ["three states"],
}


# ================================================================== generic IO-HMM (tested)
def expand_index(kidx, dt_steps):
    """kidx [T-1, N] long (input class per observed interval) -> [Tx-1, N] for the expanded chain (a gap of d
    base steps becomes d unit steps that share the interval's class)."""
    rows = []
    for t, d in enumerate(dt_steps):
        rows += [kidx[t]] * int(d)
    return torch.stack(rows) if rows else kidx[:0]


def fb_io(logb, log_pi, log_Ak, kx, K=None):
    """Forward-backward with an input-dependent transition matrix, vectorised over N sequences.
    logb [Tx,S,N]; log_pi [S]; log_Ak [K,S,S] (rows = from); kx [Tx-1,N] long (class of each step).
    Returns gamma [Tx,S,N], expected transition counts per class [K,S,S], log P(x) [N]."""
    Tx, S_, N = logb.shape
    K = log_Ak.shape[0] if K is None else K
    la = torch.empty_like(logb)
    la[0] = log_pi[:, None] + logb[0]
    for t in range(1, Tx):
        lA = log_Ak[kx[t - 1]].permute(1, 2, 0)                     # [S,S,N]
        la[t] = torch.logsumexp(la[t - 1][:, None, :] + lA, 0) + logb[t]
    lb = torch.zeros_like(logb)
    for t in range(Tx - 2, -1, -1):
        lA = log_Ak[kx[t]].permute(1, 2, 0)
        lb[t] = torch.logsumexp(lA + (logb[t + 1] + lb[t + 1])[None], 1)
    logZ = torch.logsumexp(la[-1], 0)
    gamma = torch.exp(la + lb - logZ[None, None])
    C = torch.zeros(K, S_, S_, dtype=torch.float64)
    for t in range(Tx - 1):
        lA = log_Ak[kx[t]].permute(1, 2, 0)
        xi = torch.exp(la[t][:, None, :] + lA + (logb[t + 1] + lb[t + 1])[None] - logZ[None, None])  # [S,S,N]
        C.index_add_(0, kx[t], xi.permute(2, 0, 1).double())
    return gamma, C, logZ


def mstep(C, shrink=SHRINK, dirichlet=DIRICHLET, prior=None):
    """Expected counts per class [K,S,S] -> A_k [K,S,S]: MLE with Dirichlet pseudo-counts, then partial pooling
    towards the matrix of all classes pooled. prior: optional extra pseudo-counts [S,S] added to every class."""
    C = C.double() + dirichlet
    if prior is not None:
        C = C + prior.double()[None]
    Ak = C / C.sum(-1, keepdim=True)
    Cp = C.sum(0)
    Ap = Cp / Cp.sum(-1, keepdim=True)
    return ((1 - shrink) * Ak + shrink * Ap[None]).float(), Ap.float()


def fit_io_hmm(logb, dt_steps, kidx, K, n_iter=N_ITER, tol=1e-6, init_A=None, prior=None, shrink=SHRINK):
    """Baum-Welch for pi and A_k. logb [T,S,N] (observed dates only), kidx [T-1,N] long. Returns dict."""
    T, S_, N = logb.shape
    lbx, pos = gmm._expand(logb, dt_steps)
    kx = expand_index(kidx, dt_steps)
    A0 = torch.tensor([[0.96, 0.02, 0.02], [0.05, 0.90, 0.05], [0.02, 0.02, 0.96]]) if init_A is None else init_A
    Ak = A0[None].repeat(K, 1, 1)
    pi = torch.full((S_,), 1.0 / S_)
    hist, prev = [], -float("inf")
    for _ in range(n_iter):
        g, C, logZ = fb_io(lbx, torch.log(pi), torch.log(Ak.clamp_min(1e-12)), kx, K)
        ll = float(logZ.mean())
        hist.append(ll)
        Ak, Ap = mstep(C, shrink=shrink, prior=prior)
        cpi = g[0].sum(-1).double() + 1.0
        pi = (cpi / cpi.sum()).float()
        if ll - prev < tol * abs(ll):
            break
        prev = ll
    counts = C.sum((1, 2))
    return {"pi": pi, "A": Ak, "A_pooled": Ap, "K": K, "loglik": hist, "counts_per_class": counts.tolist()}


def smooth_io(logb, hm, dt_steps, kidx):
    """Posterior state probabilities on the observed dates, chunked. logb [T,S,N], kidx [T-1,N] -> [T,S,N]."""
    T, S_, N = logb.shape
    out = torch.empty(T, S_, N)
    lpi = torch.log(hm["pi"].clamp_min(1e-12))
    lA = torch.log(hm["A"].clamp_min(1e-12))
    for i in range(0, N, CHUNK):
        lbx, pos = gmm._expand(logb[:, :, i:i + CHUNK], dt_steps)
        kx = expand_index(kidx[:, i:i + CHUNK], dt_steps)
        g, _, _ = fb_io(lbx, lpi, lA, kx, hm["K"])
        out[:, :, i:i + CHUNK] = g[pos]
        del lbx, kx, g
    return out


# ================================================================== emissions and inputs
def log_emission(q, r):
    """q, r [T,N] -> log b [T,3,N] for (open water, water under vegetation, land)."""
    q = q.clamp(EPS, 1 - EPS)
    r = r.clamp(EPS, 1 - EPS)
    lq, l1q, lr, l1r = torch.log(q), torch.log1p(-q), torch.log(r), torch.log1p(-r)
    return torch.stack([lq + l1r, l1q + lr, l1q + l1r], 1)


def regime_map(hh, wp, slope, relh, rule=REGIME):
    """Reference-free floodplain regime [H,W] bool: flat, low, water possible, and temporally variable."""
    T = hh.shape[0]
    s, _ = hh.float().sort(0)
    iqr = s[int(round(0.75 * (T - 1)))] - s[int(round(0.25 * (T - 1)))]
    R = (iqr > rule["iqr_min"]) & wp
    if slope is not None:
        R &= slope < rule["slope_max"]
    if relh is not None:
        R &= relh < rule["relh_max"]
    return R


def dark_flags(events, hh, drop=DARK_DROP, into=DARK_INTO):
    """[T-1,H,W] bool: a significant change (stats change test, FDR 1 %) between t and t+1 with HH falling by
    more than `drop` and ending below `into` (the water range of the strict detector)."""
    d = hh[1:].float() - hh[:-1].float()
    return events[1:].bool() & (d < -drop) & (hh[1:].float() < into)


def drop_flags(hh, drop=DARK_DROP, into=DARK_INTO):
    """[T-1,H,W] bool without a change test: HH falls by more than `drop` and ends below `into`."""
    d = hh[1:].float() - hh[:-1].float()
    return (d < -drop) & (hh[1:].float() < into)


def class_index(cfg, T, flags, R, n):
    """Input class per (interval, pixel) for the time options in cfg["time"] (a string of letters among a, b, c).
    flags [T-1,n] bool or None; R [n] bool or None. Returns (kidx [T-1,n] long, K, legend)."""
    opts = cfg.get("time", "")
    k = torch.zeros(T - 1, n, dtype=torch.long)
    K = 1
    legend = [("all",)]
    if "a" in opts:
        k = k * (T - 1) + torch.arange(T - 1)[:, None]
        legend = [l + (f"interval {j}",) for l in legend for j in range(T - 1)]
        K *= T - 1
    if "b" in opts:
        f = flags if flags is not None else torch.zeros(T - 1, n, dtype=torch.bool)
        k = k * 2 + f.long()
        legend = [l + (x,) for l in legend for x in ("no darkening", "darkening")]
        K *= 2
    if "c" in opts:
        rr = R if R is not None else torch.zeros(n, dtype=torch.bool)
        k = k * 2 + rr.long()[None]
        legend = [l + (x,) for l in legend for x in ("stable regime", "floodplain regime")]
        K *= 2
    return k, K, [" / ".join(l[1:]) or "all" for l in legend]


# ================================================================== the seeded detector as an expert
def _seeded_fn(d, mb):
    """crossscene/products.pt -> (p None, water mask [T,H,W], valid None); the 'water' key is the output of the
    mode the detector chose for the scene itself (lake or flood). Refuses another date list."""
    if list(d["dates"]) != list(mb["dates"]):
        raise ValueError("crossscene dates differ from the math detector's")
    return None, d["water"].bool().clone(), None


def register_seeded():
    """Make the seeded detector visible to scoreboard.Area / fusion.collect in THIS process only (no file is
    changed): a registry entry like the other experts, cached reduced mask in $FUSION_CACHE."""
    if SEEDED not in sb.REGISTRY:
        sb.REGISTRY[SEEDED] = dict(path="crossscene/products.pt", fn=_seeded_fn, role="expert",
                                   label="seeded (self-calibrating)", cost="see crossscene.md")
    if SEEDED not in f1.NO_FUSED:
        f1.NO_FUSED.append(SEEDED)


def scene_mode(area):
    """The seeded detector's own, reference-free scene decision: ('flood' | 'lake', has_water) or (None, None)."""
    if not SCENE_KNOBS.exists():
        return None, None
    a = json.loads(SCENE_KNOBS.read_text()).get("areas", {}).get(area)
    return (a["mode"], bool(a.get("has_water", True))) if a else (None, None)


# ================================================================== data per area
def _cached(area, name, src, build):
    """Reduced array cache in $FUSION_CACHE keyed on the source file mtime (as scoreboard.py does)."""
    cp = CACHE / area / f"f2_{name}.pt"
    mt = src.stat().st_mtime if src is not None and src.exists() else None
    if cp.exists():
        c = sb._ld(cp)
        if c.get("src_mtime") == mt:
            return c["v"]
    v = build()
    cp.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"v": v, "src_mtime": mt}, cp)
    return v


def aux_inputs(area, H, W, hh, flag="stats"):
    """fv_score [T,H,W] f16 or None, darkening flags [T-1,H,W] bool or None, slope, relh [H,W] or None."""
    fvp = PROC / area / "floodveg/products.pt"
    fv = None
    if fvp.exists():
        def b_fv():
            d = sb._ld(fvp)
            v = d["fv_score"].half().clone()
            del d
            gc.collect()
            return v
        fv = _cached(area, "fv", fvp, b_fv)
    stp = PROC / area / "stats/products.pt"
    fl = None
    if flag == "drop":
        fl = drop_flags(hh)
    elif stp.exists():
        def b_ev():
            d = sb._ld(stp)
            v = d["change_events"].bool().clone()
            del d
            gc.collect()
            return v
        ev = _cached(area, "events", stp, b_ev)
        fl = dark_flags(ev, hh)
        del ev
    tp = PROC / area / "terrain.pt"
    slope = relh = None
    if tp.exists():
        t = sb._ld(tp)
        if isinstance(t, dict) and "slope_deg" in t and tuple(t["slope_deg"].shape) == (H, W):
            slope, relh = t["slope_deg"].float(), t["rel_height_m"].float()
        del t
    return fv, fl, slope, relh


class Fresh:
    """A new place without the expert suite: the math detector (with terrain) and the HH score from
    crossscene/base.pt, plus `supervised` if its product exists; JRC labels from the same file.
    Duck-types the parts of scoreboard.Area that fusion.apply / scoreboard.evaluate use."""

    def __init__(self, area):
        b = sb._ld(PROC / area / "crossscene/base.pt")
        self.area = area
        self.dates, self.bounds, self.km2 = list(b["dates"]), tuple(b["bounds"]), float(b["km2"])
        self.T = len(self.dates)
        self.H, self.W = b["valid"].shape[-2:]
        valid = b["valid"].all(0) if b["valid"].dim() == 3 else b["valid"]
        self.wp = b["water_ok"].bool() if b.get("water_ok") is not None else torch.ones(self.H, self.W, dtype=torch.bool)
        self.has_terrain = b.get("water_ok") is not None
        self.hh = b["L"][:, 0].half().clone()
        self.E = {"math_noterrain": {"p": None, "mask": b["water_fixed"].bool().clone(), "valid": None}}
        self.foot = valid.clone()
        hs = self.hh.float()
        self.E["math_score"] = {"p": torch.sigmoid((0.09 - hs) / 0.03).half(), "mask": hs < 0.09, "valid": None}
        del hs
        sp = PROC / area / "supervised/products.pt"
        if sp.exists():
            try:
                mb = {"dates": self.dates, "water": self.E["math_noterrain"]["mask"]}
                p, m, v = sb.REGISTRY["supervised"]["fn"](sb._ld(sp), mb)
                self.E["supervised"] = {"p": p.clamp(0, 1).half(), "mask": m.bool(), "valid": v}
                if v is not None:
                    self.foot &= v
            except Exception as e:  # noqa: BLE001
                print(f"  [{area}] supervised unusable: {e}")
        cp = PROC / area / "crossscene/products.pt"
        if cp.exists():
            d = sb._ld(cp)
            try:
                _, m, _ = _seeded_fn(d, {"dates": self.dates})
                if tuple(m.shape) == (self.T, self.H, self.W):
                    self.E[SEEDED] = {"p": None, "mask": m, "valid": None}
            except Exception as e:  # noqa: BLE001
                print(f"  [{area}] seeded unusable: {e}")
            del d
            gc.collect()
        for c in self.E.values():
            c["mask"] &= self.foot
        self.ref = {"occ_mean": b["occ"].float(), "ext_frac": b["ext"].float(), "occ_ge90_frac": b["occ90"].float()}
        lab = {k: v.bool() & self.foot for k, v in b["lab"].items()}
        self.lab = lab
        self.strips = {"test": torch.ones(self.H, self.W, dtype=torch.bool), "all": torch.ones(self.H, self.W, dtype=torch.bool)}
        self.reg = {k: Region(lab, m, self.ref["occ_mean"], self.km2, self.T) for k, m in self.strips.items()}
        del b
        gc.collect()


# ================================================================== configuration -> fused states
def pool_params(samples, experts, terrain="hard"):
    """v1 per-date pooling (no time, no space) refitted on TUNE anchors for an expert list (with per-area refits)."""
    return f1.fit_config(samples, {"experts": list(experts), "terrain": terrain, "time": False, "space": False})


def pool_for_fresh(samples, have):
    return f1.fit_config(samples, {"experts": list(have), "terrain": "hard", "time": False, "space": False}, sub=False)


def pooled_q(A, pp):
    """Per-date fused P(open water) [T,H,W] (v1 pooling + hard terrain)."""
    return f1.apply(A, pp)


def fit_loose(A, R, hydro, strip="tune", grid=None):
    """Floodplain-regime threshold: inside the regime R the strict math mask (HH < 0.12 with hysteresis) is
    replaced by HH < theta. theta is chosen on the TUNE strip of Tonle Sap only, so that the area of
    (math water OR (R AND HH < theta)), summed over the dates, matches the hydro expected area there
    (weak supervision from the altimeter + 38-year record; the TEST strip is never used). Returns dict."""
    grid = grid if grid is not None else [round(0.12 + 0.005 * i, 3) for i in range(37)]
    m = A.strips[strip] & A.foot & hydro["domain"]
    target = sum(float((hydro["expected"][t] & m).sum()) for t in range(A.T))
    math = A.E["math"]["mask"]
    hh = A.hh.float()
    best, curve = None, []
    for th in grid:
        a = sum(float(((math[t] | (R & (hh[t] < th))) & m).sum()) for t in range(A.T))
        curve.append((th, round(a / target, 4)))
        if best is None or abs(a - target) < best[1]:
            best = (th, abs(a - target))
    return {"theta": best[0], "ratio_curve_tune": curve, "target_px_dates_tune": int(target)}


def loose_q(A, pp_fp, R, theta):
    """Pooled P(open water) with the math expert evaluated at the loose threshold (used inside R only)."""
    keep = A.E["math_noterrain"]["mask"]
    A.E["math_noterrain"]["mask"] = keep | ((A.hh.float() < theta) & R[None] & A.foot)
    keep_s = A.E["math_score"]["p"] if "math_score" in A.E else None
    if keep_s is not None:   # the HH score keeps its form (centre = 3/4 of the weak threshold, scale = 1/4 of it)
        A.E["math_score"]["p"] = torch.sigmoid((0.75 * theta - A.hh.float()) / (0.25 * theta)).half()
    try:
        return pooled_q(A, pp_fp)
    finally:
        A.E["math_noterrain"]["mask"] = keep
        if keep_s is not None:
            A.E["math_score"]["p"] = keep_s


def build_inputs(A, cfg, pp, loose=None):
    """Everything the time model needs for one area, on footprint pixels: logb [T,3,N], kidx, regime, etc."""
    T, H, W = A.T, A.H, A.W
    pp, pp_fp = pp if isinstance(pp, tuple) else (pp, None)
    q = pooled_q(A, pp)
    hh = A.hh if hasattr(A, "hh") else sb.math_base(A.area)["hh"]
    flag = cfg.get("flag", "stats")
    if A.area in AREAS:
        fv, fl, slope, relh = aux_inputs(A.area, H, W, hh, flag)
    else:
        fv, fl, (slope, relh) = None, (drop_flags(hh) if flag == "drop" else None), _fresh_terrain(A)
    R = regime_map(hh, A.wp, slope, relh)
    if cfg.get("regime") == "jrc":                 # comparison only: JRC maximum extent as the regime
        R = (A.ref["ext_frac"] >= 0.5) & A.wp
    share = float((R & A.foot).sum()) / max(float(A.foot.sum()), 1)
    if cfg.get("scene_min_share") is not None and share < cfg["scene_min_share"]:
        R = torch.zeros_like(R)                    # scene-level switch: too little floodplain -> stable scene
    if cfg.get("scene_mode"):                      # second scene switch: the seeded detector's own mode decision
        mode, has_w = scene_mode(A.area)
        if mode is not None and (mode != "flood" or not has_w):
            R = torch.zeros_like(R)
    if pp_fp is not None:                          # regime-gated experts: inside R only the floodplain set
        if loose is not None and cfg.get("loose"):
            A.hh = hh
            q = torch.where(R[None], loose_q(A, pp_fp, R, loose["theta"]), q)
        else:
            q = torch.where(R[None], pooled_q(A, pp_fp), q)
    fi = torch.nonzero(A.foot.flatten()).flatten()
    qf = q.reshape(T, -1)[:, fi]
    if cfg.get("states", 3) == 3 and fv is not None:
        rf = R_LO + (R_HI - R_LO) * fv.reshape(T, -1)[:, fi].float()
    else:
        rf = torch.full_like(qf, R_NONE)
    logb = log_emission(qf, rf)
    if fl is not None and cfg.get("flag_in_regime"):   # evidence-dependent transitions only inside the regime
        fl = fl & R[None]
    flf = fl.reshape(T - 1, -1)[:, fi] if fl is not None else None
    Rf = R.flatten()[fi]
    kidx, K, legend = class_index(cfg, T, flf, Rf, fi.numel())
    info = {"regime_km2": round(float(R.sum()) * A.km2, 1), "regime_share_raw": round(share, 4), "has_fv": fv is not None, "has_flags": fl is not None}
    del fv, fl
    return {"q": q, "logb": logb, "kidx": kidx, "K": K, "legend": legend, "fi": fi, "R": R, "info": info,
            "flags_km2": None if flf is None else [round(float(x) * A.km2, 1) for x in flf.sum(1)]}


def _fresh_terrain(A):
    tp = PROC / A.area / "terrain.pt"
    if tp.exists():
        t = sb._ld(tp)
        if isinstance(t, dict) and "slope_deg" in t and tuple(t["slope_deg"].shape) == (A.H, A.W):
            return t["slope_deg"].float(), t["rel_height_m"].float()
    return None, None


def fit_time(inp, dts, gen_seed=0):
    """Baum-Welch on a sample of footprint pixels: half from the regime R, half from the rest (when both exist)."""
    g = torch.Generator().manual_seed(SEED + gen_seed)
    Rf = inp["R"].flatten()[inp["fi"]]
    fm = inp.get("fitmask")
    ok = fm.flatten()[inp["fi"]] if fm is not None else torch.ones_like(Rf)
    if inp.get("uniform"):
        c = torch.nonzero(ok).flatten()
        sel = c[torch.randperm(c.numel(), generator=g)[:N_FIT]]
        return fit_io_hmm(inp["logb"][:, :, sel], dts, inp["kidx"][:, sel], inp["K"])
    a, b = torch.nonzero(Rf & ok).flatten(), torch.nonzero(~Rf & ok).flatten()
    na = min(a.numel(), N_FIT // 2)
    nb = min(b.numel(), N_FIT - na)
    sel = torch.cat([a[torch.randperm(a.numel(), generator=g)[:na]], b[torch.randperm(b.numel(), generator=g)[:nb]]])
    return fit_io_hmm(inp["logb"][:, :, sel], dts, inp["kidx"][:, sel], inp["K"])


def fuse_states(A, cfg, pp, loose=None, hm=None):
    """Fused state posteriors gamma [T,3,H,W] for area A, configuration cfg. Returns (gamma, hm, inp-info)."""
    T, H, W = A.T, A.H, A.W
    inp = build_inputs(A, cfg, pp, loose)
    if cfg.get("fit_strip") and cfg["fit_strip"] in getattr(A, "strips", {}) and A.area in AREAS and A.area != "ararat":
        inp["fitmask"] = A.strips[cfg["fit_strip"]]      # Baum-Welch on the TUNE strip only (Ararat: whole area)
    inp["uniform"] = cfg.get("fit_sample") == "uniform"
    dts = gmm.dt_from_dates(A.dates)
    fi = inp["fi"]
    if cfg.get("time", "") == "none":
        gf = torch.softmax(inp["logb"], 1)
        hm = None
    else:
        if hm is None:
            hm = fit_time(inp, dts)
        gf = smooth_io(inp["logb"], hm, dts, inp["kidx"])
    gam = torch.zeros(T, S, H * W)
    gam[:, L_] = 1.0
    gam[:, :, fi] = gf
    gam = gam.reshape(T, S, H, W)
    q = inp["q"]
    del gf, inp["logb"], inp["kidx"]
    if cfg.get("space"):
        gam = smooth_states(gam, A.foot)
    info = dict(inp["info"], legend=inp["legend"], flags_km2=inp["flags_km2"])
    return gam, hm, info, q, inp["R"]


def smooth_states(g, foot):
    """Masked 3x3 mean of each state posterior inside the footprint, renormalised."""
    out = torch.empty_like(g)
    for s in range(S):
        out[:, s] = f1.smooth_space(g[:, s], foot)
    out = out / out.sum(1, keepdim=True).clamp_min(1e-6)
    out[:, L_] = torch.where(foot[None], out[:, L_], torch.ones_like(out[:, L_]))
    return out


# ================================================================== floodplain references
_HYDRO = {}


def hydro_ref():
    """Independent 2026 flooded-extent estimate for Tonle Sap (altimeter level + 38-year occurrence), or None."""
    if "v" in _HYDRO:
        return _HYDRO["v"]
    p = PROC / "tonle-sap/hydro/products.pt"
    if not p.exists():
        _HYDRO["v"] = None
        return None
    d = sb._ld(p)
    v = {k: d[k] for k in ("expected", "expected_lo", "expected_hi", "domain") if k in d}
    v.update({k: [float(x) for x in d[k]] for k in ("expected_km2", "expected_km2_lo", "expected_km2_hi",
                                                     "level_m_egm2008")})
    v["dates"] = list(d["dates"])
    del d
    gc.collect()
    _HYDRO["v"] = v
    return v


def altimeter_levels():
    r = json.loads((ROOT / "docs/eda/validation2026/results.json").read_text())
    return r["test3_tonle_sap"]["altimetry_egm2008_at_pass_m"]


def spearman(a, b):
    a, b = torch.tensor(a, dtype=torch.float64), torch.tensor(b, dtype=torch.float64)
    from scripts.detect.tune import wpearson
    return wpearson(avg_rank(a), avg_rank(b))


def pearson(a, b):
    from scripts.detect.tune import wpearson
    return wpearson(torch.tensor(a, dtype=torch.float64), torch.tensor(b, dtype=torch.float64))


def core_series(math_mask):
    """Strict math detector's confident-water core per date: water on t AND on every later date. [T] px."""
    T = math_mask.shape[0]
    later = torch.ones_like(math_mask[0])
    out = [None] * T
    for t in range(T - 1, -1, -1):
        later = later & math_mask[t]
        out[t] = later.clone()
    return torch.stack(out)


def guard(area_km2, core_km2, tol=RULE2["guard_tol"]):
    """'No flood eaten': area_t >= (1 - tol) * core_t for every date."""
    ok = [a >= (1 - tol) * c for a, c in zip(area_km2, core_km2)]
    worst = min((a / c if c > 0 else 1.0) for a, c in zip(area_km2, core_km2))
    return bool(all(ok)), round(float(worst), 4)


def floodplain_metrics(A, water, gam=None):
    """Tonle Sap floodplain criteria for a water mask [T,H,W]."""
    km2 = A.km2
    area = [float(water[t].sum()) * km2 for t in range(A.T)]
    lev = altimeter_levels()
    o = {"water_km2": [round(x, 1) for x in area], "spearman_alt": round(spearman(area, lev), 4),
         "pearson_alt": round(pearson(area, lev), 4)}
    if gam is not None:
        e = [float(gam[t, W_][A.foot].sum()) * km2 for t in range(A.T)]
        o["water_km2_expected"] = [round(x, 1) for x in e]
    core = core_series(A.E["math"]["mask"])
    ck = [float(core[t].sum()) * km2 for t in range(A.T)]
    o["core_km2"] = [round(x, 1) for x in ck]
    o["guard_ok"], o["guard_worst_ratio"] = guard(area, ck)
    o["core_retained"] = [round(float((water[t] & core[t]).sum()) / max(float(core[t].sum()), 1), 4) for t in range(A.T)]
    h = hydro_ref()
    if h is not None:
        ex, lo, hi = h["expected_km2"], h["expected_km2_lo"], h["expected_km2_hi"]
        o["hydro_in_band"] = int(sum(lo[t] <= area[t] <= hi[t] for t in range(A.T)))
        o["hydro_err"] = round(float(np.mean([abs(area[t] / ex[t] - 1) for t in range(A.T)])), 4)
        o["hydro_ratio"] = [round(area[t] / ex[t], 3) for t in range(A.T)]
        dom = h["domain"] & A.foot
        ious, ious_test, prec, rec = [], [], [], []
        for t in range(A.T):
            for m, lst in ((dom, ious), (dom & A.strips["test"], ious_test)):
                a_, b_ = water[t] & m, h["expected"][t] & m
                i, u = float((a_ & b_).sum()), float((a_ | b_).sum())
                lst.append(i / u if u else float("nan"))
            a_, b_ = water[t] & dom, h["expected"][t] & dom
            prec.append(float((a_ & b_).sum()) / max(float(a_.sum()), 1))
            rec.append(float((a_ & b_).sum()) / max(float(b_.sum()), 1))
        o["hydro_iou"] = round(float(np.nanmean(ious)), 4)
        o["hydro_iou_test"] = round(float(np.nanmean(ious_test)), 4)
        o["hydro_iou_per_date"] = [round(x, 4) for x in ious]
        o["hydro_precision"] = [round(x, 4) for x in prec]
        o["hydro_recall"] = [round(x, 4) for x in rec]
    return o


# ================================================================== evaluation
def area_eval(A, gam, light=True, p=None):
    water = (gam[:, W_] > THRESH) & A.foot
    o = sb.evaluate(A, water, p=p, light=light)
    o["n_perm"], o["n_never"] = A.reg["test"].n_perm, A.reg["test"].n_never
    state = gam.argmax(1)
    o["fv_km2"] = [round(float(((state[t] == V_) & A.foot).sum()) * A.km2, 1) for t in range(A.T)]
    o["water_km2_expected"] = [round(float(gam[t, W_][A.foot].sum()) * A.km2, 1) for t in range(A.T)]
    if A.area == "tonle-sap":
        o["floodplain"] = floodplain_metrics(A, water, gam)
    return o, water


def summarise(per_area):
    pa = per_area
    m = {"per_area": pa}
    m["ba"] = {a: sb.ba_or_spec(pa[a]) for a in pa}
    m["far_km2"] = round(sum(pa[a]["far_fp_km2_test_ever"] for a in pa if a in ("sevan", "ararat")), 2)
    m["sevan_dip"] = pa["sevan"]["sevan_windy_dip_km2"] if "sevan" in pa else None
    m["sevan_mae_calm"] = pa["sevan"]["sevan_mae_calm_km2"] if "sevan" in pa else None
    nst = sum(pa[a]["n_perm"] + pa[a]["n_never"] for a in pa)
    m["flips"] = round(sum(pa[a]["flips_stable"] * (pa[a]["n_perm"] + pa[a]["n_never"]) for a in pa) / max(nst, 1), 5)
    if "tonle-sap" in pa:
        fp = pa["tonle-sap"]["floodplain"]
        m["fp"] = {k: fp.get(k) for k in ("spearman_alt", "pearson_alt", "hydro_err", "hydro_iou", "hydro_iou_test",
                                          "hydro_in_band", "guard_ok", "guard_worst_ratio")}
        m["fp"]["rho_amb"] = pa["tonle-sap"].get("rank_rho")
        m["fp"]["late_inside_ext"] = pa["tonle-sap"].get("late_share_inside_max_extent")
        m["fp"]["water_km2"] = fp["water_km2"]
        m["fp"]["fv_km2"] = pa["tonle-sap"]["fv_km2"]
    return m


def admit2(v1m, cur, new, rule=RULE2, capability=False):
    """The v2 admission rule, mechanically. v1m: metrics of the v1 fused product (the lake/mountain bar);
    cur: configuration accepted so far; new: candidate. Returns (accept, reasons)."""
    r = {}
    # lakes / mountains: keep the v1 gains
    d_ba = {a: new["ba"][a] - v1m["ba"][a] for a in v1m["ba"] if v1m["ba"][a] is not None and new["ba"].get(a) is not None}
    r["lm_ba_ok"] = all(d >= -rule["ba_tol"] for d in d_ba.values())
    r["lm_far_ok"] = new["far_km2"] <= v1m["far_km2"] + max(rule["far_tol_abs"], rule["far_tol_rel"] * v1m["far_km2"])
    r["lm_dip_ok"] = abs(new["sevan_dip"]) <= rule["dip_max_km2"]
    r["lm_mae_ok"] = new["sevan_mae_calm"] <= v1m["sevan_mae_calm"] + rule["mae_tol_km2"]
    r["lm_flips_ok"] = new["flips"] <= rule["flips_max"]
    lm_ok = all(r[k] for k in ("lm_ba_ok", "lm_far_ok", "lm_dip_ok", "lm_mae_ok", "lm_flips_ok"))
    # floodplain: do not degrade
    c, n = cur["fp"], new["fp"]
    r["fp_spearman_ok"] = n["spearman_alt"] >= c["spearman_alt"] - rule["spearman_tol"]
    r["fp_rho_ok"] = n["rho_amb"] >= c["rho_amb"] - rule["rho_amb_tol"]
    has_h = n.get("hydro_err") is not None and c.get("hydro_err") is not None
    r["fp_hydro_ok"] = (not has_h) or (n["hydro_err"] <= c["hydro_err"] + rule["hydro_err_tol"]
                                       and n["hydro_iou_test"] >= c["hydro_iou_test"] - rule["hydro_iou_tol"]
                                       and n["hydro_in_band"] >= c["hydro_in_band"])
    r["fp_guard_ok"] = bool(n["guard_ok"])
    fp_ok = all(r[k] for k in ("fp_spearman_ok", "fp_rho_ok", "fp_hydro_ok", "fp_guard_ok"))
    # at least one improvement over the current configuration
    imp = []
    dfar = cur["far_km2"] - new["far_km2"]
    if dfar >= rule["imp_far_abs"] and dfar >= rule["imp_far_rel"] * max(cur["far_km2"], 1e-9):
        imp.append("mountain false water")
    if abs(cur["sevan_dip"]) - abs(new["sevan_dip"]) >= rule["imp_dip"]:
        imp.append("Sevan dip")
    dfl = cur["flips"] - new["flips"]
    if dfl >= rule["imp_flips_abs"] and dfl >= rule["imp_flips_rel"] * max(cur["flips"], 1e-9):
        imp.append("flips")
    if n["spearman_alt"] - c["spearman_alt"] >= rule["imp_spearman"]:
        imp.append("altimeter Spearman")
    if n["rho_amb"] - c["rho_amb"] >= rule["imp_rho_amb"]:
        imp.append("rank consistency")
    if has_h and c["hydro_err"] - n["hydro_err"] >= rule["imp_hydro_err"]:
        imp.append("hydro area error")
    if has_h and n["hydro_iou_test"] - c["hydro_iou_test"] >= rule["imp_hydro_iou"]:
        imp.append("hydro IoU")
    if n["guard_ok"] and not c["guard_ok"]:
        imp.append("no-flood-eaten guard now passes")
    r["improved"] = imp
    r["fast_ok"] = bool(new.get("max_seconds", 0) <= rule["max_seconds"])
    r["d_ba_pp"] = {a: round(100 * d, 3) for a, d in d_ba.items()}
    r["capability_addition"] = capability
    return bool(lm_ok and fp_ok and (imp or capability) and r["fast_ok"]), r


# ================================================================== running configurations
class Runner:
    """Holds the TUNE samples (for pooling refits), the v1 parameters and per-area contexts (one at a time)."""

    def __init__(self, samples):
        self.samples = samples
        self.pool_cache = {}
        self.loose = None

    def pp(self, experts):
        key = tuple(experts)
        if key == ("v1",):
            return v1_pool_params()
        if key not in self.pool_cache:
            self.pool_cache[key] = pool_params(self.samples, experts)
        return self.pool_cache[key]

    def pps(self, cfg):
        """Pooling parameters for cfg: one set, or (main, floodplain-regime set) when cfg has fp_experts."""
        pp = self.pp(cfg["experts"])
        return (pp, self.pp(cfg["fp_experts"])) if cfg.get("fp_experts") else pp

    def area(self, a):
        A = sb.Area(a, names=f1.NO_FUSED, verbose=False)
        A.hh = sb.math_base(a)["hh"]
        return A

    def run(self, cfg, areas=AREAS, keep=False, light=True):
        """Evaluate one configuration over the areas. keep=True also returns gamma per area (memory!)."""
        per, hms, infos, secs, kept = {}, {}, {}, {}, {}
        pp = self.pps(cfg)
        for a in areas:
            t0 = time.time()
            A = self.area(a)
            if cfg.get("v1"):
                gam = v1_states(A)
                hm, info = None, {}
            else:
                gam, hm, info, q, R = fuse_states(A, cfg, pp, self.loose)
                del q, R
            o, water = area_eval(A, gam, light=light, p=gam[:, W_] if not light else None)
            secs[a] = round(time.time() - t0, 1)
            per[a], hms[a], infos[a] = o, hm, info
            if keep:
                kept[a] = gam.half()
            del A, gam, water
            gc.collect()
        m = summarise(per)
        m["seconds"] = secs
        m["max_seconds"] = max(secs.values())
        m["hmm"] = {a: _hm_json(h) for a, h in hms.items()}
        m["inputs"] = infos
        return (m, kept) if keep else m


def v1_pool_params():
    """The admitted v1 pooling (weights, biases, two-value maps, per-area refits) with time and space off."""
    p = copy.deepcopy(f1.load_params())
    for q in [p] + list(p.get("sub", {}).values()):
        q["cfg"] = dict(q["cfg"], time=False, space=False)
    return p


def v1_states(A):
    """The v1 fused product as a 2-state gamma (water / land)."""
    d = sb._ld(PROC / A.area / "fusion/products.pt")
    p = d["p_water"].float()
    del d
    g = torch.zeros(A.T, S, A.H, A.W)
    g[:, W_] = p
    g[:, L_] = 1 - p
    return g


def math_states(A, name="math"):
    m = A.E[name]["mask"].float()
    g = torch.zeros(A.T, S, A.H, A.W)
    g[:, W_] = m
    g[:, L_] = 1 - m
    return g


def _hm_json(h):
    if h is None:
        return None
    return {"pi": [round(float(x), 5) for x in h["pi"]], "A": [[[round(float(x), 6) for x in row] for row in Ak] for Ak in h["A"]],
            "A_pooled": [[round(float(x), 6) for x in row] for row in h["A_pooled"]], "K": h["K"],
            "counts_per_class": [round(float(x), 1) for x in h["counts_per_class"]], "iters": len(h["loglik"])}


def _brief(m):
    out = {k: m.get(k) for k in ("ba", "far_km2", "sevan_dip", "sevan_mae_calm", "flips", "max_seconds")}
    out["fp"] = m.get("fp")
    return out


def _row(name, cfg, cur, new, ok, why):
    return {"addition": name, "accepted": ok, "config": cfg, "before": _brief(cur), "after": _brief(new),
            "reasons": why}


def _print(name, ok, why, new):
    fp = new["fp"]
    print(f"  {'ACCEPT' if ok else 'reject'} {name}: BA {[round(100 * v, 2) for v in new['ba'].values()]} far {new['far_km2']} "
          f"dip {new['sevan_dip']} mae {new['sevan_mae_calm']} flips {new['flips']} | TS last {fp['water_km2'][-1]:.0f} "
          f"spear {fp['spearman_alt']} rho {fp['rho_amb']} herr {fp.get('hydro_err')} iou {fp.get('hydro_iou_test')} "
          f"band {fp.get('hydro_in_band')} guard {fp['guard_ok']} ({fp['guard_worst_ratio']}) | {why['improved']}",
          flush=True)


V1_EXPERTS = ["math_noterrain", "unet", "deep_ae", "subpixel"]
TIME_OPTIONS = [("d: stationary (no input)", ""),
                ("a: one matrix per interval", "a"),
                ("b: darkening-flag input", "b"),
                ("c: regime input", "c"),
                ("b+c", "bc"),
                ("a+b", "ab")]
# the floodplain package: v1 pooling everywhere, but inside the reference-free regime R (only in scenes where R
# covers >= 5 % of the footprint) the floodplain expert set; darkening flags only inside R; Baum-Welch on the
# TUNE strip (Ararat: whole area), uniform pixel sample.
PKG = {"experts": ["v1"], "fp_experts": ["math_noterrain"], "flag": "drop", "flag_in_regime": True, "states": 2,
       "space": True, "fit_strip": "tune", "fit_sample": "uniform", "scene_min_share": 0.05}
OFFER = ["math_score", "stats", "gmm_date", "graph", "novel_front", "supervised", "unet", "deep_ae", "subpixel"]


def expand_experts(lst):
    out = []
    for n in lst:
        out += V1_EXPERTS if n == "v1" else [n]
    return list(dict.fromkeys(out))


def _tie(m):
    return (round(m["fp"].get("hydro_err") or 0, 3), m["flips"])


def run_admission(R):
    """Declared order (greedy, each step against the configuration accepted so far, lake/mountain bar = v1):
    1. floodplain package with each time option (2 states) -- among those that pass, the lowest hydro area
       error, then the fewest flips; 2. three states; 3. each other expert added to the floodplain set;
    4. each expert added to the main set (main set refitted: v1 experts + it); 5. space smoothing off;
    6. floodplain-regime threshold fitted on the hydro reference (TUNE strip only); 7. the same with the JRC
    maximum extent as regime (comparison, never admitted: it uses the validation reference)."""
    t0 = time.time()
    rows, abl, abl_ng = [], {}, {}
    v1m = R.run({"v1": True, "experts": ["math_noterrain"]})
    print(f"  v1 product: far {v1m['far_km2']} dip {v1m['sevan_dip']} TS last {v1m['fp']['water_km2'][-1]} "
          f"guard {v1m['fp']['guard_ok']}", flush=True)
    best = None
    for name, opt in TIME_OPTIONS:
        c = dict(PKG, time=opt)
        new = R.run(c)
        ok, why = admit2(v1m, v1m, new)
        abl[name] = _brief(new) | {"accepted_vs_v1": ok, "reasons": why, "hmm_tonle": new["hmm"]["tonle-sap"],
                                   "hmm_sevan": new["hmm"]["sevan"], "legend": new["inputs"]["tonle-sap"].get("legend"),
                                   "water_km2": {a: new["per_area"][a]["water_km2"] for a in new["per_area"]},
                                   "sevan_largest_km2": new["per_area"]["sevan"]["sevan_largest_km2"]}
        rows.append(_row(f"time {name} (in the floodplain package)", c, v1m, new, ok, why))
        _print(f"time {name}", ok, why, new)
        if ok and (best is None or _tie(new) < _tie(best[1])):
            best = (c, new)
    # the same options without the regime-gated experts (time model alone; reported, not admitted)
    for name, opt in TIME_OPTIONS:
        c = {k: v for k, v in dict(PKG, time=opt).items() if k != "fp_experts"}
        new = R.run(c)
        ok, why = admit2(v1m, v1m, new)
        abl_ng[name] = _brief(new) | {"passes_vs_v1": ok, "reasons": why}
        _print(f"[no gating] time {name}", ok, why, new)
    if best is None:
        print("  no time option passes the rule against v1")
        return {"rows": rows, "ablation": abl, "ablation_no_gating": abl_ng, "accepted_config": None,
                "v1": _brief(v1m), "seconds": round(time.time() - t0, 1)}, None, v1m
    cfg, cur = best
    for r in rows:
        r["chosen"] = r["config"] == cfg

    def step(name, c, capability=False):
        nonlocal cfg, cur
        new = R.run(c)
        ok, why = admit2(v1m, cur, new, capability=capability)
        rows.append(_row(name, c, cur, new, ok, why))
        _print(name, ok, why, new)
        if ok:
            cfg, cur = c, new
        return ok, new

    step("three states (water under vegetation, flooded-vegetation evidence)", dict(cfg, states=3), capability=True)
    for n in OFFER:
        if n not in cfg["fp_experts"]:
            step(f"floodplain set + {n}", dict(cfg, fp_experts=cfg["fp_experts"] + [n]))
    for n in OFFER:
        main = expand_experts(cfg["experts"])
        if n not in main:
            step(f"main set + {n} (main set refitted)", dict(cfg, experts=main + [n]))
    step(f"space smoothing {'off' if cfg['space'] else 'on'}", dict(cfg, space=not cfg["space"]))
    jrc = None
    if hydro_ref() is not None:
        R.loose = fit_loose_tonle(R, cfg)
        step("floodplain-regime threshold (HH < theta inside R; theta fitted on hydro, TUNE strip)",
             dict(cfg, loose=True))
        c = dict(cfg, loose=True, regime="jrc")
        jrc = R.run(c)
        ok, why = admit2(v1m, cur, jrc)
        rows.append(_row("comparison: regime = JRC maximum extent (not admissible)", c, cur, jrc, False, why))
        _print("comparison: JRC regime", False, why, jrc)
    fin_ok = final_check(v1m, cur)
    return {"rows": rows, "ablation": abl, "ablation_no_gating": abl_ng, "accepted_config": cfg,
            "final": _brief(cur), "final_hmm": cur["hmm"], "final_inputs": cur["inputs"], "v1": _brief(v1m),
            "final_check": fin_ok, "loose": R.loose,
            "jrc_regime": _brief(jrc) if jrc else None, "seconds": round(time.time() - t0, 1)}, cfg, v1m


def final_check(v1m, cur, mathm=None):
    """The verdict bar: the final configuration must meet the v1 lake/mountain bar AND be better than v1 on the
    floodplain (Spearman, rank consistency, hydro error, guard)."""
    ok, why = admit2(v1m, v1m, cur)
    out = {k: v for k, v in why.items() if k.startswith("lm_")}
    out["floodplain_better_than_v1"] = {k: [v1m["fp"][k], cur["fp"][k]] for k in ("spearman_alt", "rho_amb", "hydro_err",
                                                                                  "hydro_iou_test", "guard_ok")}
    return out


def run_amend2(R, adm):
    """Amendment 2 (written after the first final run, see fusion2.md): the self-calibrating seeded detector
    became available; it is offered with the SAME rule, one addition at a time, against the accepted
    configuration: (i) in the floodplain set, (ii) in the main set (refitted). The seeded detector's own scene
    decision (lake / flood mode) as a second scene switch changes nothing on the three areas (Tonle Sap is
    'flood', Sevan has no regime, Ararat has none) -- it is evaluated on the fresh hold-out only, not admitted."""
    cfg = adm["accepted_config"]
    R.loose = adm.get("loose")
    v1m = R.run({"v1": True, "experts": ["math_noterrain"]})
    cur = R.run(cfg)
    print(f"  accepted configuration re-run: TS last {cur['fp']['water_km2'][-1]} far {cur['far_km2']}", flush=True)
    rows = []
    for name, mk in (("floodplain set + seeded", lambda c: dict(c, fp_experts=c["fp_experts"] + [SEEDED])),
                     ("main set + seeded (main set refitted)",
                      lambda c: dict(c, experts=expand_experts(c["experts"]) + [SEEDED]))):
        c = mk(cfg)
        new = R.run(c)
        ok, why = admit2(v1m, cur, new)
        rows.append(_row(f"amendment 2: {name}", c, cur, new, ok, why))
        _print(name, ok, why, new)
        if ok:
            cfg, cur = c, new
    out = dict(adm, amendment2=rows, accepted_config=cfg, final=_brief(cur), final_hmm=cur["hmm"],
               final_inputs=cur["inputs"], final_check=final_check(v1m, cur))
    out["rows"] = [r for r in adm["rows"] if not r["addition"].startswith("amendment 2")] + rows
    return out


def fit_loose_tonle(R, cfg):
    A = R.area("tonle-sap")
    fv, fl, slope, relh = aux_inputs("tonle-sap", A.H, A.W, A.hh)
    Rg = regime_map(A.hh, A.wp, slope, relh)
    out = fit_loose(A, Rg, hydro_ref())
    del A, fv, fl
    gc.collect()
    print(f"  floodplain-regime threshold fitted on the TUNE strip: HH < {out['theta']}", flush=True)
    return out


# ================================================================== products, band
def band(A, gam, water, experts_used, region=None):
    """Expected open-water area and a band: three half-widths combined in quadrature.
    thr: the statistical study's threshold interval (stats water_km2_lo/hi relative half-width, per date);
    exp: spread (std) of the open-water area across the experts used plus the math detector (terrain-masked);
    shore: half a pixel along the water boundary (mixed pixels)."""
    km2, T = A.km2, A.T
    foot = A.foot if region is None else A.foot & region
    water = water & foot
    E = torch.tensor([float(gam[t, W_][foot].sum()) * km2 for t in range(T)])
    rel = None
    stp = PROC / A.area / "stats/products.pt"
    if stp.exists():
        rel = _cached(A.area, "stats_rel", stp, lambda: _stats_rel(stp))
    if rel is None:
        rel = torch.tensor([_THR_REL_DEFAULT] * T)
    h_thr = rel * E
    areas = []
    for n in set(expand_experts(experts_used)) | {"math_noterrain"}:
        if n in A.E:
            m = A.E[n]["mask"] & A.wp & foot
            areas.append([float(m[t].sum()) * km2 for t in range(T)])
    h_exp = torch.tensor(np.std(np.array(areas), 0)) if len(areas) > 1 else torch.zeros(T)
    er = water & torch.nn.functional.max_pool2d((~water).float()[:, None], 3, 1, 1)[:, 0].bool()
    h_sh = torch.tensor([0.5 * float(er[t].sum()) * km2 for t in range(T)])
    half = (h_thr ** 2 + h_exp.float() ** 2 + h_sh ** 2).sqrt()
    return E, (E - half).clamp_min(0), E + half, {"thr": h_thr.tolist(), "experts": h_exp.tolist(), "shore": h_sh.tolist()}


_THR_REL_DEFAULT = 0.05


def _stats_rel(stp):
    d = sb._ld(stp)
    b, lo, hi = d["water_km2_baseline"].float(), d["water_km2_lo"].float(), d["water_km2_hi"].float()
    del d
    gc.collect()
    return (0.5 * (hi - lo) / b.clamp_min(1e-6)).clamp(0, 1)


def products(A, gam, cfg, hm, info, extra=None, gam3=None, hm3=None):
    """gam: the admitted model (open-water decision); gam3: the three-state model with the same configuration
    (the water-under-vegetation layer) when the admitted model has two states."""
    T = A.T
    water = (gam[:, W_] > THRESH) & A.foot
    gam3 = gam if gam3 is None else gam3
    state = gam3.argmax(1).to(torch.uint8)
    state[:, ~A.foot] = L_
    E, lo, hi, parts = band(A, gam, water, cfg["experts"])
    km2 = A.km2
    out = {"p_state": gam3.half(), "state": state, "water": water, "p_water": gam[:, W_].half(),
           "water_km2": water.sum((1, 2)).float() * km2, "water_km2_expected": E.float(),
           "water_km2_lo": lo.float(), "water_km2_hi": hi.float(), "band_parts_km2": parts,
           "fv_km2": ((state == V_) & A.foot).sum((1, 2)).float() * km2,
           "fv_km2_expected": torch.tensor([float(gam3[t, V_][A.foot].sum()) * km2 for t in range(T)]),
           "weights": None, "transitions": _hm_json(hm), "transition_classes": info.get("legend"),
           "transitions_three_state": _hm_json(hm3) if hm3 is not None else _hm_json(hm),
           "accepted": cfg, "dates": A.dates, "bounds": A.bounds, "km2_per_px": km2, "valid": A.foot,
           "state_names": STATE_NAMES, "threshold": THRESH,
           "note": "water / p_water / water_km2*: the admitted model (p_water > 0.5). p_state [T,3,H,W] = posterior of "
                   "(open water, water under vegetation, land) of the three-state model with the same configuration; "
                   "state = its argmax; fv_km2 = area where state is 'water under vegetation'. water_km2_expected = sum of P(open water) x km2; lo/hi = expected "
                   "area -/+ the quadrature sum of threshold, expert-spread and shoreline half-widths. "
                   "See docs/eda/fusion2.md."}
    if extra:
        out.update(extra)
    return out


def fuse2(area, cfg=None, R=None, save=True, keep=False):
    """Fusion v2 products for one area with the admitted configuration (read from results.json if not given)."""
    t0 = time.time()
    register_seeded()
    if cfg is None or R is None:
        res = load_results()
        cfg = cfg or res["admission"]["accepted_config"]
        if R is None:
            R = Runner([f1.collect(a) for a in f1.TUNE_AREAS])
            R.loose = res["admission"].get("loose")
    if area in AREAS:
        A = R.area(area)
        pp = R.pps(cfg)
        main = pp[0] if isinstance(pp, tuple) else pp
        eff = main.get("sub", {}).get(area, main)
    else:
        A = Fresh(area)
        have = [n for n in expand_experts(cfg["experts"]) if n in A.E]
        main = pool_for_fresh(R.samples, have)
        fpx = [n for n in cfg.get("fp_experts", []) if n in A.E]
        pp = (main, pool_for_fresh(R.samples, fpx)) if cfg.get("fp_experts") and fpx else main
        eff = main
    gam, hm, info, q, Rg = fuse_states(A, cfg, pp, R.loose)
    gam3, hm3 = None, None
    if cfg.get("states", 3) == 2 and A.area in AREAS and (PROC / A.area / "floodveg/products.pt").exists():
        gam3, hm3, _, q3, _ = fuse_states(A, dict(cfg, states=3), pp, R.loose)
        del q3
    o = products(A, gam, cfg, hm, info, gam3=gam3, hm3=hm3, extra={"weights": eff["w"], "bias": eff["b"], "two_value": eff["two_value"],
                                          "experts_used_here": eff["cfg"]["experts"], "regime": Rg,
                                          "regime_rule": REGIME, "regime_info": info,
                                          "loose_term": R.loose if cfg.get("loose") else None})
    o["runtime_s"] = round(time.time() - t0, 1)
    if save:
        od = PROC / area / "fusion2"
        od.mkdir(parents=True, exist_ok=True)
        torch.save(o, od / "products.pt")
        print(f"  [{area}] wrote fusion2/products.pt ({o['runtime_s']} s); water km2 "
              f"{[round(float(v)) for v in o['water_km2']]}; fv km2 {[round(float(v)) for v in o['fv_km2']]}", flush=True)
    summ = {k: [round(float(x), 1) for x in o[k]] for k in ("water_km2", "water_km2_expected", "water_km2_lo",
                                                            "water_km2_hi", "fv_km2", "fv_km2_expected")}
    summ["band_parts_km2"] = {k: [round(float(x), 1) for x in v] for k, v in o["band_parts_km2"].items()}
    summ["runtime_s"] = o["runtime_s"]
    summ["experts_used_here"] = o["experts_used_here"]
    summ["weights"] = {k: round(v, 4) for k, v in o["weights"].items()}
    summ["transitions"] = o["transitions"]
    summ["transitions_three_state"] = o["transitions_three_state"]
    summ["transition_classes"] = o["transition_classes"]
    summ["regime_km2"] = round(float(Rg.sum()) * A.km2, 1)
    summ["dates"] = A.dates
    out = (summ, A, gam, gam if gam3 is None else gam3) if keep else summ
    if not keep:
        del A, gam, gam3
    del o, q
    gc.collect()
    return out


def load_results():
    return json.loads((OUT / "results.json").read_text())["fusion2"]


def merge(section, data):
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "results.json"
    allr = json.loads(p.read_text()) if p.exists() else {}
    allr[section] = data
    allr["updated"] = dt.datetime.now().isoformat(timespec="seconds")
    p.write_text(json.dumps(allr, indent=1, default=float))


# ================================================================== final table, coverage, fresh hold-out
def _compact(o):
    keep = ("balanced_acc", "spec_never", "recall_perm", "fpr_never", "ci95", "flips_stable", "far_fp_km2_ever",
            "far_fp_km2_test_ever", "water_km2", "rank_rho", "late_share_inside_max_extent", "sevan_largest_km2",
            "sevan_mae_calm_km2", "sevan_windy_dip_km2", "brier_bal", "auc", "reliability", "fv_km2",
            "water_km2_expected", "floodplain")
    return {k: o[k] for k in keep if k in o}


def lake_region(A, dil=5):
    """Sevan: the permanent-water lake (largest connected JRC >= 90 % component) dilated by `dil` px."""
    perm = (A.ref["occ_ge90_frac"] >= 0.5).numpy()
    lab, n = ndi.label(perm, structure=np.ones((3, 3), bool))
    if n == 0:
        return None
    big = np.bincount(lab.ravel())[1:].argmax() + 1
    m = ndi.binary_dilation(lab == big, iterations=dil)
    return torch.from_numpy(m)


def three_state_report(A, gam3):
    """Tonle Sap, three-state model: areas per state, transitions per interval (argmax states), and the late
    season (second-to-last -> last date): where the new open water came from, and how much of it lies inside
    the hydro expected extent of the last date."""
    km2, T = A.km2, A.T
    st = gam3.argmax(1)
    st[:, ~A.foot] = L_
    o = {"open_km2": [round(float((st[t] == W_).sum()) * km2, 1) for t in range(T)],
         "veg_km2": [round(float((st[t] == V_).sum()) * km2, 1) for t in range(T)],
         "veg_km2_expected": [round(float(gam3[t, V_][A.foot].sum()) * km2, 1) for t in range(T)]}
    tr = {}
    for nm, i, j in (("V->W", V_, W_), ("L->W", L_, W_), ("L->V", L_, V_), ("V->L", V_, L_), ("W->L", W_, L_),
                     ("W->V", W_, V_)):
        tr[nm] = [round(float(((st[t] == i) & (st[t + 1] == j)).sum()) * km2, 1) for t in range(T - 1)]
    o["transitions_km2"] = tr
    a, b = st[-2], st[-1]
    new = (b == W_) & (a != W_)
    o["late"] = {"from": A.dates[-2], "to": A.dates[-1],
                 "open_net_change_km2": round(float((b == W_).sum() - (a == W_).sum()) * km2, 1),
                 "new_open_km2": round(float(new.sum()) * km2, 1),
                 "new_open_from_veg_km2": round(float((new & (a == V_)).sum()) * km2, 1),
                 "new_open_from_land_km2": round(float((new & (a == L_)).sum()) * km2, 1)}
    h = hydro_ref()
    if h is not None:
        o["late"]["new_open_inside_expected_share"] = round(float((new & h["expected"][-1]).sum()) / max(float(new.sum()), 1), 3)
        o["veg_inside_expected_share"] = [round(float(((st[t] == V_) & h["expected"][t]).sum()) /
                                                max(float((st[t] == V_).sum()), 1), 3) for t in range(T)]
    try:
        o["rule_veg_km2"] = json.loads((ROOT / "docs/eda/validation2026/results.json").read_text())["test3_tonle_sap"]["flooded_veg_km2"]
    except Exception:  # noqa: BLE001
        pass
    return o


def final_table(R, cfg):
    """Every row of the comparison table, per area, with bootstrap intervals; products; band coverage; maps."""
    out, cov, maps = {}, {}, {}
    for a in AREAS:
        summ, A, gam, gam3 = fuse2(a, cfg, R, save=True, keep=True)
        rows = {}
        for n, lab in (("math", "math + terrain"), ("gmm", "Markov detector (GMM+HMM)"), ("graph", "graph random walker"),
                       (SEEDED, "seeded detector (self-calibrating)")):
            if n in A.E:
                o = sb.evaluate(A, A.E[n]["mask"], A.p(n))
                if a == "tonle-sap":
                    o["floodplain"] = floodplain_metrics(A, A.E[n]["mask"] & A.foot)
                rows[n] = _compact(o) | {"label": lab}
        g1 = v1_states(A)
        o, w1 = area_eval(A, g1, light=False, p=g1[:, W_])
        rows["v1"] = _compact(o) | {"label": "fusion v1"}
        o, water = area_eval(A, gam, light=False, p=gam[:, W_])
        rows["v2"] = _compact(o) | {"label": "fusion v2"}
        out[a] = {"rows": rows, "products": summ}
        # probability band: area at P(open water) > 0.75 .. > 0.25 (the model's own uncertainty only)
        pw = gam[:, W_]
        pb_lo = [round(float(((pw[t] > 0.75) & A.foot).sum()) * A.km2, 1) for t in range(A.T)]
        pb_hi = [round(float(((pw[t] > 0.25) & A.foot).sum()) * A.km2, 1) for t in range(A.T)]
        out[a]["p_band"] = {"lo": pb_lo, "hi": pb_hi}
        if a == "tonle-sap":
            out[a]["three_state"] = three_state_report(A, gam3)
        # coverage of the band
        if a == "sevan":
            reg = lake_region(A)
            E, lo, hi, parts = band(A, gam, water, cfg["experts"], region=reg)
            inside = [bool(lo[t] <= SEVAN_KM2 <= hi[t]) for t in range(A.T)]
            calm = [t for t in range(A.T) if t not in sb.SEVAN_WINDY]
            cov[a] = {"lake_expected_km2": [round(float(x), 1) for x in E], "lo": [round(float(x), 1) for x in lo],
                      "hi": [round(float(x), 1) for x in hi], "truth_km2": SEVAN_KM2, "inside": inside,
                      "coverage_all": round(sum(inside) / A.T, 3),
                      "coverage_calm": round(sum(inside[t] for t in calm) / len(calm), 3),
                      "parts": {k: [round(float(x), 1) for x in v] for k, v in parts.items()},
                      "v1_band_inside": None}
            # v1's band (area at p > 0.75 .. p > 0.25) for the same lake region
            p1 = g1[:, W_]
            lo1 = [float(((p1[t] > 0.75) & reg & A.foot).sum()) * A.km2 for t in range(A.T)]
            hi1 = [float(((p1[t] > 0.25) & reg & A.foot).sum()) * A.km2 for t in range(A.T)]
            cov[a]["v1_lo"], cov[a]["v1_hi"] = [round(x, 1) for x in lo1], [round(x, 1) for x in hi1]
            cov[a]["v1_coverage_all"] = round(sum(lo1[t] <= SEVAN_KM2 <= hi1[t] for t in range(A.T)) / A.T, 3)
            p2 = gam[:, W_]
            lo2 = [float(((p2[t] > 0.75) & reg & A.foot).sum()) * A.km2 for t in range(A.T)]
            hi2 = [float(((p2[t] > 0.25) & reg & A.foot).sum()) * A.km2 for t in range(A.T)]
            cov[a]["v2_pband_lo"], cov[a]["v2_pband_hi"] = [round(x, 1) for x in lo2], [round(x, 1) for x in hi2]
            cov[a]["v2_pband_coverage_all"] = round(sum(lo2[t] <= SEVAN_KM2 <= hi2[t] for t in range(A.T)) / A.T, 3)
            cov[a]["band_halfwidth_km2"] = [round(float(hi[t] - lo[t]) / 2, 1) for t in range(A.T)]
        if a == "tonle-sap" and hydro_ref() is not None:
            h = hydro_ref()
            lo, hi = summ["water_km2_lo"], summ["water_km2_hi"]
            ex, hlo, hhi = h["expected_km2"], h["expected_km2_lo"], h["expected_km2_hi"]
            cov[a] = {"hydro_expected_inside_band": [bool(lo[t] <= ex[t] <= hi[t]) for t in range(A.T)],
                      "bands_overlap": [bool(lo[t] <= hhi[t] and hlo[t] <= hi[t]) for t in range(A.T)]}
            cov[a]["coverage_expected"] = round(sum(cov[a]["hydro_expected_inside_band"]) / A.T, 3)
            cov[a]["coverage_overlap"] = round(sum(cov[a]["bands_overlap"]) / A.T, 3)
            pl, ph = out[a]["p_band"]["lo"], out[a]["p_band"]["hi"]
            cov[a]["pband_expected_inside"] = [bool(pl[t] <= ex[t] <= ph[t]) for t in range(A.T)]
            cov[a]["pband_coverage_expected"] = round(sum(cov[a]["pband_expected_inside"]) / A.T, 3)
            cov[a]["pband_overlap"] = round(sum(pl[t] <= hhi[t] and hlo[t] <= ph[t] for t in range(A.T)) / A.T, 3)
            cov[a]["band_halfwidth_rel"] = [round((hi[t] - lo[t]) / 2 / max(summ["water_km2_expected"][t], 1), 3)
                                            for t in range(A.T)]
            st = gam3.argmax(1)
            st[:, ~A.foot] = 3
            late = torch.zeros_like(st[-1], dtype=torch.uint8)               # 0 nothing, 1 V->W, 2 L->W, 3 W before
            late[(st[-1] == W_) & (st[-2] == V_)] = 1
            late[(st[-1] == W_) & (st[-2] == L_)] = 2
            late[(st[-1] == W_) & (st[-2] == W_)] = 3
            late[~A.foot] = 4
            maps[a] = {"state": st[:, ::2, ::2].to(torch.uint8).clone(), "bounds": A.bounds, "dates": A.dates,
                       "hydro": h["expected"][:, ::2, ::2].clone(), "late": late[::2, ::2].clone()}
        del A, gam, gam3, g1, water, w1
        gc.collect()
    return out, cov, maps


def perm_lake_ratio(A, water):
    """Water inside the permanent-water lake zone (largest JRC >= 90 % body, dilated 3 px) / that body's area."""
    perm = (A.ref["occ_ge90_frac"] >= 0.5) & A.foot
    lab, n = ndi.label(perm.numpy(), structure=np.ones((3, 3), bool))
    if n == 0:
        return None
    cnt = np.bincount(lab.ravel())[1:]
    big = cnt.argmax() + 1
    ref_km2 = float(cnt.max()) * A.km2
    if ref_km2 < 20:
        return {"ref_km2": round(ref_km2, 1), "note": "no stable lake >= 20 km2"}
    zone = torch.from_numpy(ndi.binary_dilation(lab == big, iterations=3)) & A.foot
    ar = [float((water[t] & zone).sum()) * A.km2 for t in range(A.T)]
    return {"ref_km2": round(ref_km2, 1), "zone_water_km2": [round(x, 1) for x in ar],
            "median_ratio": round(float(np.median(ar)) / ref_km2, 3)}


def fresh_holdout(R, cfg, areas=None):
    """Whole new places that fusion v1 never touched: math, a v1-style chain (sticky, no regime) and v2."""
    out = {}
    v1style = {"experts": ["math_noterrain"], "time": "", "states": 2, "space": True, "fit_sample": "uniform"}
    for a in (FRESH if areas is None else areas):
        if not (PROC / a / "crossscene/base.pt").exists():
            out[a] = {"skipped": "no crossscene/base.pt"}
            continue
        t0 = time.time()
        try:
            summ, A, gam, _ = fuse2(a, cfg, R, save=True, keep=True)
        except Exception as e:  # noqa: BLE001
            out[a] = {"skipped": f"{type(e).__name__}: {e}"}
            print(f"  [{a}] skipped: {e}")
            continue
        res = {"dates": A.dates, "experts": list(A.E), "regime_km2": summ["regime_km2"],
               "footprint_km2": round(float(A.foot.sum()) * A.km2, 1),
               "anchors": {"perm_px": A.reg["test"].n_perm, "never_px": A.reg["test"].n_never}}
        m = A.E["math_noterrain"]["mask"] & A.wp & A.foot
        pp1 = pool_for_fresh(R.samples, ["math_noterrain"])
        g1, _, _, q1, _ = fuse_states(A, v1style, pp1)
        cands = [("math", m), ("v1_style", (g1[:, W_] > THRESH) & A.foot), ("v2", (gam[:, W_] > THRESH) & A.foot)]
        if SEEDED in A.E:
            cands.append(("seeded", A.E[SEEDED]["mask"] & A.foot))
        mode, has_w = scene_mode(a)
        res["seeded_scene_mode"] = {"mode": mode, "has_water": has_w}
        if mode is not None and (mode != "flood" or not has_w):     # second scene switch changes this place
            have = [n for n in expand_experts(cfg["experts"]) if n in A.E]
            gs, _, _, qs, _ = fuse_states(A, dict(cfg, scene_mode=True), pool_for_fresh(R.samples, have), R.loose)
            cands.append(("v2_scene_switch", (gs[:, W_] > THRESH) & A.foot))
            del gs, qs
        else:
            cands.append(("v2_scene_switch", cands[2][1]))
        for name, w in cands:
            o = sb.evaluate(A, w, light=True)
            res[name] = {"balanced_acc": o.get("balanced_acc"), "spec_never": o.get("spec_never"),
                         "recall_perm": o.get("recall_perm"), "far_fp_km2_ever": o["far_fp_km2_ever"],
                         "flips_stable": o["flips_stable"], "rank_rho": o.get("rank_rho"),
                         "water_km2": o["water_km2"], "lake": perm_lake_ratio(A, w)}
        res["v2"]["fv_km2"] = summ["fv_km2"]
        res["seconds"] = round(time.time() - t0, 1)
        out[a] = res
        print(f"  [{a}] " + " | ".join(f"{n}: BA {res[n]['balanced_acc']} far {res[n]['far_fp_km2_ever']} flips "
                                       f"{res[n]['flips_stable']}" for n, _ in cands), flush=True)
        del A, gam, g1, q1
        gc.collect()
    return out


# ================================================================== figures
def _plt():
    return sb._plt()


def _save(fig, name):
    plt = _plt()
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    fig.savefig(p, dpi=fig.dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  wrote {p.relative_to(ROOT)} ({p.stat().st_size / 1024:.0f} KB)")


def _dates(ds):
    return [dt.date.fromisoformat(x) for x in ds]


def fig_tonle(tab, cov):
    import matplotlib.dates as mdates
    plt = _plt()
    r = tab["tonle-sap"]["rows"]
    pr = tab["tonle-sap"]["products"]
    d = _dates(pr["dates"])
    fig, axs = plt.subplots(3, 1, figsize=(8.2, 9.0), dpi=72, sharex=True, gridspec_kw={"height_ratios": [3, 1.3, 1.8]})
    ax = axs[0]
    h = hydro_ref()
    if h is not None:
        ax.fill_between(d, h["expected_km2_lo"], h["expected_km2_hi"], color="#2c7fb8", alpha=0.15,
                        label="hydro: expected flooded extent, low..high (altimeter + 38-yr record)")
        ax.plot(d, h["expected_km2"], color="#2c7fb8", ls="--", lw=1.5, label="hydro: expected")
    ax.fill_between(d, pr["water_km2_lo"], pr["water_km2_hi"], color="k", alpha=0.12, label="v2 band")
    ax.plot(d, r["math"]["water_km2"], "s-", color="#d95f02", lw=2, label="math + terrain")
    ax.plot(d, r["v1"]["water_km2"], "^-", color="#7570b3", lw=1.8, label="fusion v1")
    if "gmm" in r:
        ax.plot(d, r["gmm"]["water_km2"], ".-", color="#999", lw=1, label="Markov detector")
    if "graph" in r:
        ax.plot(d, r["graph"]["water_km2"], ".:", color="#999", lw=1, label="graph")
    if SEEDED in r:
        ax.plot(d, r[SEEDED]["water_km2"], "d-", color="#e7298a", lw=1.5, label="seeded detector")
    ax.plot(d, r["v2"]["water_km2"], "o-", color="k", lw=2.5, label="fusion v2")
    ax.set_ylabel("open water, km²")
    ax.set_title("Tonle Sap open water per date", fontsize=10)
    ax.legend(fontsize=7, ncol=2); ax.grid(alpha=0.3)
    lev = altimeter_levels()
    axs[1].plot(d, lev, "o-", color="#2c7fb8")
    axs[1].set_ylabel("lake level, m\n(altimeter)"); axs[1].grid(alpha=0.3)
    fvr = json.loads((ROOT / "docs/eda/validation2026/results.json").read_text())["test3_tonle_sap"]["flooded_veg_km2"]
    axs[2].plot(d, fvr, "s--", color="#1b9e77", label="flooded-vegetation rule (floodveg.py)")
    axs[2].plot(d, pr["fv_km2"], "o-", color="#1b9e77", lw=2.2, label="v2: water under vegetation (state)")
    axs[2].plot(d, pr["fv_km2_expected"], ":", color="#1b9e77", label="v2: Σ P(water under vegetation)")
    axs[2].set_ylabel("km²"); axs[2].legend(fontsize=7); axs[2].grid(alpha=0.3)
    axs[2].xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    _save(fig, "tonle_area.png")


def fig_sevan(tab, cov):
    import matplotlib.dates as mdates
    plt = _plt()
    r = tab["sevan"]["rows"]
    d = _dates(tab["sevan"]["products"]["dates"])
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=72)
    c = cov.get("sevan")
    if c:
        ax.fill_between(d, c["lo"], c["hi"], color="k", alpha=0.12, label="v2 band (lake zone)")
        ax.plot(d, c["lake_expected_km2"], "k--", lw=1, label="v2 expected Σγ (lake zone)")
    for n, st in (("math", dict(color="#d95f02", marker="s", lw=2)), ("v1", dict(color="#7570b3", marker="^", lw=1.8)),
                  ("gmm", dict(color="#999", marker=".", lw=1)), ("graph", dict(color="#999", marker=".", lw=1, ls=":")),
                  (SEEDED, dict(color="#e7298a", marker="d", lw=1.5)), ("v2", dict(color="k", marker="o", lw=2.5))):
        if n in r:
            ax.plot(d, r[n]["sevan_largest_km2"], label=f"{r[n]['label']} (calm MAE {r[n]['sevan_mae_calm_km2']:.0f}, "
                    f"dip {r[n]['sevan_windy_dip_km2']:.0f})", **st)
    ax.axhline(SEVAN_KM2, color="b", ls="--", lw=1, label="published 1,242 km²")
    for i in sb.SEVAN_WINDY:
        ax.axvspan(d[i] - dt.timedelta(days=3), d[i] + dt.timedelta(days=3), color="grey", alpha=0.2)
    ax.set_ylabel("largest water body, km²"); ax.set_ylim(1100, 1300)
    ax.set_title("Lake Sevan per date (grey: the two rough-surface dates)", fontsize=10)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.legend(fontsize=7, ncol=2); ax.grid(alpha=0.3)
    _save(fig, "sevan_area.png")


def fig_transitions(adm):
    plt = _plt()
    import matplotlib.dates as mdates
    fin = adm["final_hmm"]["tonle-sap"]
    legend = adm["final_inputs"]["tonle-sap"]["legend"]
    fig, axs = plt.subplots(1, 2, figsize=(11.5, 4.2), dpi=72)
    ax = axs[0]
    names = [("W→L", 0, 2), ("L→W", 2, 0), ("L→V", 2, 1), ("V→W", 1, 0), ("V→L", 1, 2), ("W→V", 0, 1)]
    x = np.arange(len(names))
    K = len(fin["A"])
    for k in range(K):
        ax.bar(x + (k - (K - 1) / 2) * 0.8 / K, [fin["A"][k][i][j] for _, i, j in names], 0.8 / K, label=legend[k])
    ax.set_xticks(x, [n for n, _, _ in names]); ax.set_yscale("log"); ax.set_ylim(1e-5, 1)
    ax.set_ylabel("probability per 12 days"); ax.legend(fontsize=7)
    ax.set_title("Tonle Sap, admitted model: transitions per input class\n(W open water, V water under vegetation, L land)",
                 fontsize=9)
    ax.grid(alpha=0.3, axis="y")
    ax = axs[1]
    a = adm["ablation"].get("a: one matrix per interval")
    dts = gmm.dt_from_dates(adm["dates"])
    if a and a.get("hmm_tonle"):
        Ak = a["hmm_tonle"]["A"]
        dd = _dates(adm["dates"])
        mid = [dd[t] + (dd[t + 1] - dd[t]) / 2 for t in range(len(dd) - 1)]
        ax.plot(mid, [Ak[t][2][0] for t in range(len(Ak))], "o-", label="option (a), Tonle Sap: L→W per interval")
        ax.plot(mid, [Ak[t][0][2] for t in range(len(Ak))], "s-", label="option (a), Tonle Sap: W→L per interval")
        if a.get("hmm_sevan"):
            As = a["hmm_sevan"]["A"]
            ax.plot(mid, [As[t][2][0] for t in range(len(As))], "o--", color="grey", label="option (a), Sevan: L→W")
            ax.plot(mid, [As[t][0][2] for t in range(len(As))], "s--", color="grey", label="option (a), Sevan: W→L")
        ax.set_yscale("log"); ax.legend(fontsize=7); ax.grid(alpha=0.3)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
        ax.set_title("Non-stationary option: rates per interval (a gap counts twice)", fontsize=9)
    _save(fig, "transitions.png")


def fig_maps(maps):
    if "tonle-sap" not in maps:
        return
    plt = _plt()
    from matplotlib.colors import ListedColormap
    m = maps["tonle-sap"]
    T = m["state"].shape[0]
    pick = [2, 5, T - 2, T - 1]
    cmap = ListedColormap(["#1f5fbf", "#2a9d4b", "#efe6d2", "#ffffff"])
    w, s_, e, n = m["bounds"]
    fig, axs = plt.subplots(2, 3, figsize=(13.5, 9.6), dpi=60)
    axs = axs.flatten()
    for ax, t in zip(axs[:4], pick):
        ax.imshow(m["state"][t].numpy(), cmap=cmap, vmin=0, vmax=3, extent=(w, e, s_, n), interpolation="nearest")
        ax.set_title(f"{m['dates'][t]}: three states", fontsize=9); ax.tick_params(labelsize=7)
    ax = axs[4]
    ax.imshow(m["hydro"][T - 1].numpy().astype(float), cmap=ListedColormap(["#efe6d2", "#1f5fbf"]), vmin=0, vmax=1,
              extent=(w, e, s_, n), interpolation="nearest")
    ax.set_title(f"{m['dates'][T - 1]}: hydro expected\n(altimeter + Landsat record, no radar)", fontsize=9)
    ax.tick_params(labelsize=7)
    ax = axs[5]
    ax.imshow(m["late"].numpy(), cmap=ListedColormap(["#efe6d2", "#2a9d4b", "#d95f02", "#9ecae1", "#ffffff"]),
              vmin=0, vmax=4, extent=(w, e, s_, n), interpolation="nearest")
    ax.set_title(f"open water {m['dates'][T - 1]}: light blue = open on {m['dates'][T - 2]};\n"
                 "green = new, was under vegetation; orange = new, was land", fontsize=8)
    ax.tick_params(labelsize=7)
    fig.suptitle("Tonle Sap, fusion v2 (three-state layer): blue open water, green water under vegetation, beige land",
                 fontsize=10)
    _save(fig, "states_maps.png")


def fig_reliability(tab):
    plt = _plt()
    fig, axs = plt.subplots(1, 2, figsize=(9.5, 4.2), dpi=72)
    for ax, a in zip(axs, ("tonle-sap", "sevan")):
        r = tab[a]["rows"]
        for n, st in (("gmm", dict(color="#999", marker=".")), ("graph", dict(color="#bbb", marker=".")),
                      ("v1", dict(color="#7570b3", marker="^")), ("v2", dict(color="k", marker="o", lw=2.2))):
            rel = r.get(n, {}).get("reliability")
            if rel:
                ax.plot([x["mean_p"] for x in rel], [x["frac_water"] for x in rel],
                        label=f"{r[n]['label']} (Brier {r[n].get('brier_bal')})", **st)
        ax.plot([0, 1], [0, 1], ":", color="grey")
        ax.set_title(f"{sb.TITLES[a]}: TEST anchors", fontsize=9); ax.legend(fontsize=7); ax.grid(alpha=0.3)
        ax.set_xlabel("mean predicted P(open water) in bin"); ax.set_ylabel("share of water (class-balanced)")
    _save(fig, "reliability.png")


def fig_admission(adm):
    plt = _plt()
    rows = adm["rows"]
    fig, axs = plt.subplots(1, 4, figsize=(15, 0.42 * len(rows) + 1.8), dpi=66, sharey=True)
    y = np.arange(len(rows))
    took = [r["accepted"] and r.get("chosen", True) for r in rows]
    lab = [r["addition"][:62] + ("  ✓" if t else "  (passes, not chosen)" if r["accepted"] else "")
           for r, t in zip(rows, took)]
    cols = ["#1b9e77" if t else "#a6d854" if r["accepted"] else "#d95f02" for r, t in zip(rows, took)]
    spec = [("mountain false water, km²", lambda m: m["far_km2"]), ("flips per stable px ×1000", lambda m: 1000 * m["flips"]),
            ("Tonle Sap: |area / hydro − 1|", lambda m: m["fp"].get("hydro_err") or 0),
            ("Tonle Sap: rank consistency ρ", lambda m: m["fp"]["rho_amb"])]
    for ax, (t, f) in zip(axs, spec):
        ax.barh(y + 0.2, [f(r["before"]) for r in rows], 0.4, color="#bbb", label="before")
        ax.barh(y - 0.2, [f(r["after"]) for r in rows], 0.4, color=cols, label="with change")
        ax.set_title(t, fontsize=8); ax.grid(alpha=0.3, axis="x")
    axs[0].set_yticks(y, lab, fontsize=7); axs[0].invert_yaxis(); axs[0].legend(fontsize=7)
    fig.suptitle("Admission, one change at a time (dark green = admitted; light green = passes but another time option "
                 "was chosen)", fontsize=9, y=1.0)
    _save(fig, "admission.png")


def fig_fresh(fr):
    plt = _plt()
    areas = [a for a in fr if "v2" in fr[a]]
    if not areas:
        return
    fig, axs = plt.subplots(1, 3, figsize=(13, 3.8), dpi=72)
    x = np.arange(len(areas))
    for ax, (k, t) in zip(axs, (("balanced_acc", "balanced accuracy (JRC anchors, whole place)"),
                                ("far_fp_km2_ever", "false water far from the record, km² ever"),
                                ("flips_stable", "flips per stable anchor px"))):
        ser = (("math", "#d95f02"), ("v1_style", "#7570b3"), ("seeded", "#e7298a"), ("v2", "k"),
               ("v2_scene_switch", "#666"))
        ser = [(n, c) for n, c in ser if any(n in fr[a] for a in areas)]
        wd = 0.8 / len(ser)
        for j, (n, c) in enumerate(ser):
            v = [fr[a][n].get(k) if n in fr[a] and fr[a][n].get(k) is not None else np.nan for a in areas]
            ax.bar(x + (j - (len(ser) - 1) / 2) * wd, v, wd, color=c,
                   label={"math": "math + terrain", "v1_style": "v1-style chain", "seeded": "seeded detector",
                          "v2": "fusion v2", "v2_scene_switch": "v2 + seeded scene switch"}[n])
        ax.set_xticks(x, areas, rotation=30, fontsize=8); ax.set_title(t, fontsize=9); ax.grid(alpha=0.3, axis="y")
        if k == "balanced_acc":
            lo = np.nanmin([fr[a][n].get(k) or 1 for a in areas for n, _ in ser if n in fr[a]])
            ax.set_ylim(max(0.5, lo - 0.02), 1.0)
    axs[0].legend(fontsize=7)
    fig.suptitle("Fresh hold-out: places fusion v1 never touched (experts there: math detector, seeded detector, "
                 "supervised where it exists)",
                 fontsize=9)
    _save(fig, "fresh_holdout.png")


def fig_coverage(cov, tab):
    import matplotlib.dates as mdates
    plt = _plt()
    fig, axs = plt.subplots(1, 2, figsize=(12, 4), dpi=72)
    c = cov.get("sevan")
    if c:
        d = _dates(tab["sevan"]["products"]["dates"])
        ax = axs[0]
        ax.fill_between(d, c["lo"], c["hi"], color="k", alpha=0.15, label=f"v2 band (coverage {c['coverage_all']:.0%})")
        ax.fill_between(d, c["v1_lo"], c["v1_hi"], color="#7570b3", alpha=0.25,
                        label=f"v1 band P>0.75..P>0.25 (coverage {c['v1_coverage_all']:.0%})")
        if c.get("v2_pband_lo"):
            ax.plot(d, c["v2_pband_lo"], "k:", lw=1, label=f"v2 P>0.75 .. P>0.25 (coverage {c['v2_pband_coverage_all']:.0%})")
            ax.plot(d, c["v2_pband_hi"], "k:", lw=1)
        ax.plot(d, c["lake_expected_km2"], "k-", lw=1.5, label="v2 expected")
        ax.axhline(SEVAN_KM2, color="b", ls="--", label="published 1,242 km²")
        ax.set_title("Lake Sevan zone: does the band contain the known area?", fontsize=9)
        ax.legend(fontsize=7); ax.grid(alpha=0.3); ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    c = cov.get("tonle-sap")
    h = hydro_ref()
    if c and h is not None:
        pr = tab["tonle-sap"]["products"]
        d = _dates(pr["dates"])
        ax = axs[1]
        ax.fill_between(d, pr["water_km2_lo"], pr["water_km2_hi"], color="k", alpha=0.15, label="v2 band")
        ax.plot(d, pr["water_km2_expected"], "k-", lw=1.5, label="v2 expected")
        pb = tab["tonle-sap"].get("p_band")
        if pb:
            ax.plot(d, pb["lo"], "k:", lw=1, label=f"v2 P>0.75 .. P>0.25 (hydro inside {c['pband_coverage_expected']:.0%})")
            ax.plot(d, pb["hi"], "k:", lw=1)
        ax.errorbar(d, h["expected_km2"], yerr=[np.array(h["expected_km2"]) - np.array(h["expected_km2_lo"]),
                                                 np.array(h["expected_km2_hi"]) - np.array(h["expected_km2"])],
                    fmt="o", color="#2c7fb8", capsize=3, label="hydro expected (low..high)")
        ax.set_title(f"Tonle Sap: hydro inside v2 band {c['coverage_expected']:.0%} of dates; "
                     f"bands overlap {c['coverage_overlap']:.0%}", fontsize=9)
        ax.legend(fontsize=7); ax.grid(alpha=0.3); ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    _save(fig, "coverage.png")


# ================================================================== orchestration
def run_all(stage="all"):
    """stage 'admission': the rule only (writes results.json); 'final': table, products, coverage, fresh hold-out
    and figures with the admitted configuration from results.json; 'all': both (one process)."""
    t0 = time.time()
    register_seeded()
    samples = [f1.collect(a) for a in f1.TUNE_AREAS]
    R = Runner(samples)
    hyd = hydro_ref() is not None
    print(f"hydro reference available: {hyd}", flush=True)
    if stage == "amend2":
        res = load_results()
        res["admission"] = run_amend2(R, res["admission"])
        res["amendment2_generated"] = dt.datetime.now().isoformat(timespec="seconds")
        merge("fusion2", res)
    if stage == "amend3":
        # amendment 3: the floodplain regime only where BOTH reference-free scene signals say floodplain (regime
        # share >= 5 % AND the seeded detector chose flood mode with water present). Identical on the three
        # study areas; decided after seeing the hold-out places, so those places no longer count as fresh for
        # this one decision (documented in fusion2.md). Evidence = v2 without vs with the switch, as measured.
        res = load_results()
        ev = {}
        for sec in ("fresh_holdout", "fresh_holdout_extra"):
            for a, r in res.get(sec, {}).items():
                if "v2" in r and "v2_scene_switch" in r:
                    ev[a] = {k: {m: r[k].get(m) for m in ("balanced_acc", "far_fp_km2_ever", "flips_stable", "rank_rho")}
                             for k in ("v2", "v2_scene_switch")} | {"mode": r.get("seeded_scene_mode")}
        res["admission"]["amendment3"] = {"change": "scene_mode = True", "evidence_before": ev,
                                          "generated": dt.datetime.now().isoformat(timespec="seconds")}
        res["admission"]["accepted_config"] = dict(res["admission"]["accepted_config"], scene_mode=True)
        merge("fusion2", res)
    if stage in ("all", "admission"):
        adm, cfg, v1m = run_admission(R)
        adm["dates"] = sb.math_base("tonle-sap")["dates"]
        res = {"generated": dt.datetime.now().isoformat(timespec="seconds"), "rule": RULE2, "regime_rule": REGIME,
               "constants": {"R_LO": R_LO, "R_HI": R_HI, "R_NONE": R_NONE, "N_FIT": N_FIT, "SHRINK": SHRINK,
                             "DIRICHLET": DIRICHLET, "DARK_DROP": DARK_DROP, "DARK_INTO": DARK_INTO,
                             "THRESH": THRESH, "PKG": PKG},
               "hydro_available": hyd, "admission": adm, "seconds_admission": round(time.time() - t0, 1)}
        merge("fusion2", res)
        if stage == "admission" or cfg is None:
            return
    res = load_results()
    adm = res["admission"]
    cfg = adm["accepted_config"]
    R.loose = adm.get("loose")
    t1 = time.time()
    tab, cov, maps = final_table(R, cfg)
    res["table"], res["coverage"] = tab, cov
    res["hydro_available"] = hydro_ref() is not None
    merge("fusion2", res)
    fr = fresh_holdout(R, cfg)
    res["fresh_holdout"] = fr
    if stage == "amend3":
        res["fresh_holdout_extra"] = fresh_holdout(R, cfg, EXTRA)
    res["seconds_final"] = round(time.time() - t1, 1)
    merge("fusion2", res)
    fig_tonle(tab, cov); fig_sevan(tab, cov); fig_transitions(adm); fig_maps(maps); fig_reliability(tab)
    fig_admission(adm); fig_fresh(fr); fig_coverage(cov, tab)
    print(f"fusion2 stage {stage} done in {round(time.time() - t0, 1)} s")


def figures_only():
    r = load_results()
    fig_tonle(r["table"], r["coverage"]); fig_sevan(r["table"], r["coverage"]); fig_transitions(r["admission"])
    fig_reliability(r["table"]); fig_admission(r["admission"]); fig_fresh(r["fresh_holdout"])
    fig_coverage(r["coverage"], r["table"])


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    areas = [a for a in argv if a in AREAS + FRESH + EXTRA]
    if "--figures" in argv:
        figures_only()
        return
    if "--extra" in argv:                 # products + evaluation for the other app places
        register_seeded()
        res = load_results()
        R = Runner([f1.collect(a) for a in f1.TUNE_AREAS])
        R.loose = res["admission"].get("loose")
        res["fresh_holdout_extra"] = fresh_holdout(R, res["admission"]["accepted_config"], EXTRA)
        merge("fusion2", res)
        return
    if areas:
        for a in areas:
            fuse2(a)
        return
    run_all("admission" if "--admission" in argv else "final" if "--final" in argv
            else "amend2" if "--amend2" in argv else "amend3" if "--amend3" in argv else "all")


if __name__ == "__main__":
    main()
