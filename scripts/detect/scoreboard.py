"""One scoreboard for every water detector: same pixels, same splits, same metrics.

    .venv/bin/python -m scripts.detect.scoreboard              # all three areas
    .venv/bin/python -m scripts.detect.scoreboard sevan        # one area (results.json keeps the others)

Write-up: docs/eda/scoreboard.md. Numbers: docs/eda/fusion/results.json ("scoreboard" section).

Pixels: the intersection of the valid footprints of every detector available in the area (the math
footprint already drops the 8 px swath-edge band and keeps only pixels seen on all ten dates).
Labels and split: scripts/detect/tune.py (JRC GSW permanent / never / ambiguous; column strips with
buffers; Ararat entirely TEST). Anchor metrics are on the TEST strip only.

Detectors are discovered from REGISTRY: one entry per detector, a loader that reduces the product file to
(probability [T,H,W] or None, water mask [T,H,W], valid [H,W] or None). A missing file is skipped. The
reduced arrays are cached in $FUSION_CACHE (default: the system temp dir) keyed on the source file's mtime,
so a re-run picks up new or rebuilt products automatically.

Memory: one area at a time; one product file at a time, reduced and freed before the next.
"""
from __future__ import annotations

import datetime as dt
import gc
import json
import os
import pathlib
import sys
import tempfile
import time

import numpy as np
import scipy.ndimage as ndi
import torch

from scripts.detect import ops
from scripts.detect.stack import ROOT
from scripts.detect.tune import (BLOCK, LABEL_RULE, SEED, SPLIT, Region, avg_rank, build_labels,  # noqa: F401
                                 split_masks)

AREAS = ["tonle-sap", "sevan", "ararat"]
TITLES = {"tonle-sap": "Tonle Sap", "sevan": "Lake Sevan", "ararat": "Ararat valley"}
OUT = ROOT / "docs/eda/fusion"
PROC = ROOT / "data/processed"
CACHE = pathlib.Path(os.environ.get("FUSION_CACHE", pathlib.Path(tempfile.gettempdir()) / "nisar_fusion_cache"))
PUBLISHED_SEVAN_KM2 = 1242.0
SEVAN_WINDY = (5, 6)          # 31 Aug, 12 Sep 2026
EPS = 1e-3                    # probabilities clipped to [EPS, 1-EPS]
BOOT = 200                    # block-bootstrap resamples for the scoreboard
AUC_MAX = 200_000             # anchor pixel-dates per class used for AUC / Brier / reliability


def _ld(p):
    return torch.load(p, map_location="cpu", weights_only=False)


def _free():
    gc.collect()


# ================================================================== math detector (run once, cached)
def math_base(area, recompute=False):
    """ops.run_pipeline on CPU without the terrain mask (the mask is applied separately), reduced to the
    small arrays the scoreboard and the fusion need, plus the JRC reference on the grid. Cached."""
    path = CACHE / area / "math_base.pt"
    if path.exists() and not recompute:
        return _ld(path)
    from scripts.detect import terrain
    from scripts.detect.stack import load_preview_stack
    from scripts.detect.validate import reference_on_grid
    t0 = time.time()
    st = load_preview_stack(area)
    T, _, H, W = st.x.shape
    with torch.no_grad():
        r = ops.run_pipeline(st.x, st.valid, "preview", dev=torch.device("cpu"))
    secs = time.time() - t0
    out = {"area": area, "dates": list(st.dates), "bounds": tuple(float(b) for b in st.bounds),
           "km2": float(st.km2_per_px), "water": r["water"].cpu().bool(), "fv": r["flooded_veg"].cpu().bool(),
           "valid": r["valid"].cpu().all(0), "hh": r["L"][:, 0].cpu().half(), "wind": r["wind_suspect"].cpu(),
           "knobs": {k: r["knobs"][k] for k in ("water_hh_strong", "water_hh_weak", "water_hv_max", "box",
                                                 "min_blob_px", "edge_erode_px")},
           "runtime_s": round(secs, 1)}
    del r, st
    _free()
    wp = terrain.cached_water_possible(area, (H, W))
    out["wp"] = wp.bool() if wp is not None else None
    ref, _ = reference_on_grid(area, out["bounds"], H, W)
    out["ref"] = {k: ref[k].float() for k in ("valid_frac", "occ_mean", "occ_ge90_frac", "seas12_frac",
                                              "occ_any_frac", "ext_frac")}
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(out, path)
    return out


# ================================================================== registry
def _v2(v):
    if v is None:
        return None
    v = v.bool()
    return v.all(0) if v.dim() == 3 else v


def _math(d, mb):
    return None, mb["water"] & (mb["wp"] if mb["wp"] is not None else True), None


def _math_nt(d, mb):
    return None, mb["water"].clone(), None


def _math_score(d, mb):
    """Distance to the math threshold in units of half the hysteresis gap: p = sigmoid((thr - HH) / s)."""
    k = mb["knobs"]
    thr = 0.5 * (k["water_hh_strong"] + k["water_hh_weak"])
    s = max(0.5 * (k["water_hh_weak"] - k["water_hh_strong"]), 1e-3)
    p = torch.sigmoid((thr - mb["hh"].float()) / s)
    return p.half(), p > 0.5, None


def _stats(d, mb):
    p = d["water_conf"]
    return p.half(), p.float() > 0.5, _v2(d.get("valid"))


def _gmm(d, mb):
    return d["p_water"].half(), d["water"].bool() if "water" in d else d["p_water"].float() > 0.5, None


def _gmm_date(d, mb):
    p = d["p_water_date"]
    return p.half(), p.float() > 0.5, None


def _graph(d, mb):
    p = d["p_water_rw"]
    return p.half(), d["water_graph"].bool() if "water_graph" in d else p.float() > 0.5, _v2(d.get("valid"))


def _unet(d, mb):
    names = list(d.get("seg_names", ["open water"]))
    return None, d["seg"] == names.index("open water"), None


def _novel(d, mb):
    """Flood-order product: monotone 'wet from first_wet_index on' masks (derived from the math detector)."""
    fwi = d["first_wet_index"].long()
    T = len(d["dates"])
    w = torch.arange(T)[:, None, None] >= fwi[None]
    return None, w, _v2(d.get("valid"))


def _generic(d, mb):
    """Unknown/new products: first probability-like key, else a boolean water key; must be [T,H,W]."""
    T = len(mb["dates"])
    H, W = mb["water"].shape[-2:]
    for k in ("p_water", "water_prob", "prob_water", "frac", "water_frac", "p_open_water", "p"):
        v = d.get(k) if isinstance(d, dict) else None
        if torch.is_tensor(v) and tuple(v.shape) == (T, H, W):
            msk = d.get("water")
            msk = msk.bool() if torch.is_tensor(msk) and tuple(msk.shape) == (T, H, W) else v.float() > 0.5
            return v.half(), msk, _v2(d.get("valid"))
    v = d.get("water") if isinstance(d, dict) else None
    if torch.is_tensor(v) and tuple(v.shape) == (T, H, W):
        return None, v.bool(), _v2(d.get("valid"))
    raise KeyError("no per-date [T,H,W] water probability or mask in this product")


def _fused(d, mb):
    return d["p_water"].half(), d["water"].bool(), None


# name: product path (relative to data/processed/<area>), loader, role, cost note.
# role: "expert" = offered to the fusion as evidence; "row" = scoreboard only.
REGISTRY = {
    "math": dict(path=None, fn=_math, role="row", label="math + terrain",
                 cost="math_base runtime (below) + terrain mask (cached)"),
    "math_noterrain": dict(path=None, fn=_math_nt, role="expert", label="math, no terrain",
                           cost="run_pipeline, measured here"),
    "math_score": dict(path=None, fn=_math_score, role="expert", label="math HH score",
                       cost="free (from the math run)"),
    "stats": dict(path="stats/products.pt", fn=_stats, role="expert", label="stats water_conf",
                  cost="runtime_s in product"),
    "gmm": dict(path="gmm/products.pt", fn=_gmm, role="row", label="GMM+HMM",
                cost="gmm.md: 121 s Tonle Sap, 26 s Sevan, 17 s Ararat"),
    "gmm_date": dict(path="gmm/products.pt", fn=_gmm_date, role="expert", label="GMM per date",
                     cost="as GMM+HMM"),
    "graph": dict(path="graph/products.pt", fn=_graph, role="expert", label="graph random walker",
                  cost="see graph.md"),
    "unet": dict(path="dl/products.pt", fn=_unet, role="expert", label="U-Net v2",
                 cost="dl-notes.md: predict 16 s Tonle Sap, 4 s Sevan (train 252 s sweep)"),
    "novel_front": dict(path="novel/products.pt", fn=_novel, role="expert", label="flood-order monotone",
                        cost="see novel.md"),
    "deep_ae": dict(path="deep_ae/products.pt", fn=_generic, role="expert", label="deep AE + GMM",
                    cost="see deep-ae notes"),
    "subpixel": dict(path="subpixel/products.pt", fn=_generic, role="expert", label="sub-pixel fraction",
                     cost="see subpixel.md"),
    "ttransformer": dict(path="ttransformer/products.pt", fn=_generic, role="expert", label="temporal transformer",
                         cost="see ttransformer notes"),
    "foundation": dict(path="foundation/products.pt", fn=_generic, role="expert", label="foundation model",
                       cost="see dl-notes / foundation json"),
    "supervised": dict(path="supervised/products.pt", fn=lambda d, mb: _generic({**d, "valid": d.get("footprint")}, mb),
                       role="expert", label="supervised (JRC labels, other places)",
                       cost="see supervised.md"),
    "fused": dict(path="fusion/products.pt", fn=_fused, role="row", label="FUSED (admitted)",
                  cost="fusion.py, measured"),
}


def _expert_cached(area, name, mb):
    """(p f16 or None, mask bool, valid or None, info) for one registry entry, or None if unavailable."""
    spec = REGISTRY[name]
    src = (CACHE / area / "math_base.pt") if spec["path"] is None else (PROC / area / spec["path"])
    if not src.exists():
        return None
    mt = src.stat().st_mtime
    cp = CACHE / area / f"exp_{name}.pt"
    if cp.exists():
        c = _ld(cp)
        if c.get("src_mtime") == mt:
            return c
    try:
        d = mb if spec["path"] is None else _ld(src)
        p, m, v = spec["fn"](d, mb)
        info = {}
        if isinstance(d, dict) and "runtime_s" in d and isinstance(d["runtime_s"], (int, float)):
            info["runtime_s"] = float(d["runtime_s"])
    except Exception as e:  # incomplete / incompatible product: skip, do not fail the board
        print(f"  [{area}] skip {name}: {type(e).__name__}: {e}")
        return None
    finally:
        if spec["path"] is not None:
            d = None
            _free()
    H, W = mb["water"].shape[-2:]
    if tuple(m.shape) != (len(mb["dates"]), H, W):
        print(f"  [{area}] skip {name}: shape {tuple(m.shape)}")
        return None
    c = {"p": None if p is None else p.clamp(0, 1).half(), "mask": m.bool(), "valid": v, "src_mtime": mt, "info": info}
    torch.save(c, cp)
    return c


def available(area):
    return [n for n, s in REGISTRY.items() if s["path"] is None or (PROC / area / s["path"]).exists()]


# ================================================================== area context
class Area:
    """All detectors of one area reduced to masks / probabilities on the common footprint, with labels."""

    def __init__(self, area, names=None, verbose=True):
        self.area = area
        mb = math_base(area)
        self.dates, self.bounds, self.km2 = mb["dates"], mb["bounds"], mb["km2"]
        self.T = len(self.dates)
        self.H, self.W = mb["water"].shape[-2:]
        self.wp = mb["wp"] if mb["wp"] is not None else torch.ones(self.H, self.W, dtype=torch.bool)
        self.has_terrain = mb["wp"] is not None
        self.ref = mb["ref"]
        self.knobs, self.math_runtime_s = mb["knobs"], mb["runtime_s"]
        self.E = {}
        foot = mb["valid"].clone()
        for n in (names or list(REGISTRY)):
            c = _expert_cached(area, n, mb)
            if c is None:
                continue
            self.E[n] = c
            if c["valid"] is not None and n != "fused":
                foot &= c["valid"]
        del mb
        _free()
        self.foot = foot
        for c in self.E.values():
            c["mask"] &= foot
        self.lab = build_labels(self.ref, foot, **LABEL_RULE)
        self.strips = split_masks(self.H, self.W, SPLIT[area])
        self.strips["all"] = torch.ones(self.H, self.W, dtype=torch.bool)
        self.reg = {k: Region(self.lab, m, self.ref["occ_mean"], self.km2, self.T) for k, m in self.strips.items()}
        if verbose:
            print(f"[{area}] detectors: {list(self.E)}; footprint {float(foot.sum()) * self.km2:.0f} km2; "
                  f"TEST perm {self.reg['test'].n_perm} px, never {self.reg['test'].n_never} px")

    def p(self, name):
        c = self.E[name]
        return c["p"].float() if c["p"] is not None else None


# ================================================================== metrics
def largest_body_px(m2d):
    lab, n = ndi.label(m2d.numpy(), structure=np.ones((3, 3), bool))
    if n == 0:
        return 0
    return int(np.bincount(lab.ravel())[1:].max())


def flips(mask):
    """Number of water<->land switches per pixel over the season, [H,W]."""
    return (mask[1:] ^ mask[:-1]).sum(0)


def auc_brier_rel(pp, pn, bins=10):
    """AUC (Mann-Whitney), class-balanced Brier and balanced reliability curve from anchor probabilities.
    pp: p on permanent-water pixel-dates (truth 1); pn: p on never-water pixel-dates (truth 0)."""
    g = torch.Generator().manual_seed(SEED)
    if pp.numel() > AUC_MAX:
        pp = pp[torch.randperm(pp.numel(), generator=g)[:AUC_MAX]]
    if pn.numel() > AUC_MAX:
        pn = pn[torch.randperm(pn.numel(), generator=g)[:AUC_MAX]]
    out = {"brier_bal": None, "auc": None, "reliability": None}
    if pn.numel() == 0:
        out["brier_never"] = round(float((pn ** 2).mean()), 5) if pn.numel() else None
        return out
    bn = float((pn.double() ** 2).mean())
    out["brier_never"] = round(bn, 5)
    if pp.numel() == 0:
        return out
    bp = float(((1 - pp.double()) ** 2).mean())
    out["brier_bal"] = round(0.5 * (bp + bn), 5)
    r = avg_rank(torch.cat([pp, pn]))
    npos, nneg = pp.numel(), pn.numel()
    out["auc"] = round(float((r[:npos].sum() - npos * (npos + 1) / 2) / (npos * nneg)), 5)
    edges = torch.linspace(0, 1, bins + 1)
    ip = torch.bucketize(pp.clamp(0, 1 - 1e-9), edges[1:-1])
    inn = torch.bucketize(pn.clamp(0, 1 - 1e-9), edges[1:-1])
    cp = torch.bincount(ip, minlength=bins).double() / npos   # class-balanced: each class weight 1/n_class
    cn = torch.bincount(inn, minlength=bins).double() / nneg
    sp = torch.bincount(ip, weights=pp.double(), minlength=bins) / npos
    sn = torch.bincount(inn, weights=pn.double(), minlength=bins) / nneg
    tot = cp + cn
    rel = []
    for b in range(bins):
        if tot[b] > 0:
            rel.append({"bin": [round(float(edges[b]), 2), round(float(edges[b + 1]), 2)],
                        "mean_p": round(float((sp[b] + sn[b]) / tot[b]), 4),
                        "frac_water": round(float(cp[b] / tot[b]), 4),
                        "weight": round(float(tot[b] / 2), 5)})
    out["reliability"] = rel
    return out


def evaluate(A, mask, p=None, boot=BOOT, gen=None, light=False):
    """Every scoreboard metric for one water mask [T,H,W] (and its probability, if any) in area A.
    light=True skips the bootstrap and the probability metrics (used by the admission loop)."""
    km2, T = A.km2, A.T
    mask = mask & A.foot
    R = A.reg["test"]
    m = R.metrics(mask)
    o = {k: (round(v, 6) if isinstance(v, float) and np.isfinite(v) else None) for k, v in m.items()}
    if boot and not light:
        o["ci95"] = R.bootstrap(mask, boot, gen or torch.Generator().manual_seed(SEED))
    if "val" in A.reg:
        mv = A.reg["val"].metrics(mask)
        o["val_balanced_acc"] = round(mv["balanced_acc"], 6) if np.isfinite(mv["balanced_acc"]) else None
        o["val_spec_never"] = round(mv["spec_never"], 6) if np.isfinite(mv["spec_never"]) else None
        o["val_far_fp_km2_ever"] = round(float((mask.any(0) & A.lab["never"] & A.strips["val"]).sum()) * km2, 2)
    f = flips(mask)
    o["flips_perm"] = round(float(f[R.perm].double().mean()), 4) if R.n_perm else None
    o["flips_never"] = round(float(f[R.never].double().mean()), 4) if R.n_never else None
    n_st = R.n_perm + R.n_never
    o["flips_stable"] = round(float(f[R.perm].double().sum() + f[R.never].double().sum()) / max(n_st, 1), 5)
    o["water_km2"] = [round(float(mask[t].sum()) * km2, 1) for t in range(T)]
    nev = A.lab["never"]
    o["far_fp_km2"] = [round(float((mask[t] & nev).sum()) * km2, 2) for t in range(T)]
    o["far_fp_km2_ever"] = round(float((mask.any(0) & nev).sum()) * km2, 2)
    nt = nev & A.strips["test"]
    o["far_fp_km2_test"] = [round(float((mask[t] & nt).sum()) * km2, 2) for t in range(T)]
    o["far_fp_km2_test_ever"] = round(float((mask.any(0) & nt).sum()) * km2, 2)
    if A.area == "sevan":
        lb = [largest_body_px(mask[t]) * km2 for t in range(T)]
        calm = [i for i in range(T) if i not in SEVAN_WINDY]
        o["sevan_largest_km2"] = [round(v, 1) for v in lb]
        o["sevan_mae_calm_km2"] = round(float(np.mean([abs(lb[i] - PUBLISHED_SEVAN_KM2) for i in calm])), 1)
        o["sevan_mae_all_km2"] = round(float(np.mean([abs(v - PUBLISHED_SEVAN_KM2) for v in lb])), 1)
        o["sevan_windy_dip_km2"] = round(float(np.mean([lb[i] for i in calm]) - np.mean([lb[i] for i in SEVAN_WINDY])), 1)
    if A.area == "tonle-sap":
        ext = A.ref["ext_frac"] >= 0.5
        late = mask[-3:].any(0) & A.foot
        o["late_share_inside_max_extent"] = round(float((late & ext).sum()) / max(float(late.sum()), 1), 4)
    if p is not None and not light:
        pc = p.clamp(0, 1)
        o.update(auc_brier_rel(pc[:, R.perm].flatten(), pc[:, R.never].flatten()))
    return o


def ba_or_spec(o):
    return o["balanced_acc"] if o.get("balanced_acc") is not None else o.get("spec_never")


def agreement(A, names):
    """Pairwise IoU of the last-date water masks on the common footprint."""
    M = {n: A.E[n]["mask"][-1] for n in names}
    out = {}
    for a in names:
        out[a] = {}
        for b in names:
            inter = float((M[a] & M[b]).sum())
            uni = float((M[a] | M[b]).sum())
            out[a][b] = round(inter / uni, 4) if uni else None
    return out


# ================================================================== board
def score_area(area, boot=BOOT):
    t0 = time.time()
    A = Area(area)
    gen = torch.Generator().manual_seed(SEED)
    rows = {}
    for n in A.E:
        rows[n] = evaluate(A, A.E[n]["mask"], A.p(n), boot, gen)
        rows[n]["label"] = REGISTRY[n]["label"]
        cost = REGISTRY[n]["cost"]
        rt = A.E[n]["info"].get("runtime_s")
        if n in ("math", "math_noterrain"):
            cost = f"{A.math_runtime_s} s (run_pipeline on CPU, this run)"
        elif rt is not None:
            cost = f"{rt:.1f} s (product runtime_s)"
        rows[n]["cost"] = cost
        rows[n]["probabilistic"] = A.E[n]["p"] is not None
    names = list(A.E)
    agree = agreement(A, names)
    votes = torch.stack([A.E[n]["mask"][-1] for n in names if n not in ("fused", "math")]).sum(0).to(torch.int16)
    extra = {}
    fp = PROC / area / "fusion/products.pt"
    if fp.exists():
        d = _ld(fp)
        extra = {k: d[k] for k in ("water_km2_expected", "water_km2_lo", "water_km2_hi", "water_km2") if k in d}
        extra = {k: [round(float(x), 1) for x in v] for k, v in extra.items()}
        del d
    lab = {k: {"px": int(v.sum()), "km2": round(float(v.sum()) * A.km2, 1)} for k, v in A.lab.items()}
    res = {"area": area, "dates": A.dates, "km2_per_px": A.km2, "footprint_km2": round(float(A.foot.sum()) * A.km2, 1),
           "terrain_available": A.has_terrain, "labels": lab,
           "test_anchor_px": {"perm": A.reg["test"].n_perm, "never": A.reg["test"].n_never, "amb": A.reg["test"].n_amb},
           "detectors": names, "rows": rows, "agreement_last_date_iou": agree, "fused_area": extra,
           "seconds": round(time.time() - t0, 1)}
    maps = {"votes": votes, "foot": A.foot, "n_voters": len([n for n in names if n not in ("fused", "math")]),
            "voters": [n for n in names if n not in ("fused", "math")], "bounds": A.bounds}
    del A
    _free()
    return res, maps


# ================================================================== outputs
def merge_results(section, data):
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "results.json"
    allr = json.loads(p.read_text()) if p.exists() else {}
    allr[section] = data
    allr["updated"] = dt.datetime.now().isoformat(timespec="seconds")
    p.write_text(json.dumps(allr, indent=1, default=float))


def _fmt(v, nd=3, pct=False):
    if v is None:
        return "–"
    if isinstance(v, float) and not np.isfinite(v):
        return "–"
    return f"{100 * v:.{nd}f}" if pct else f"{v:.{nd}f}" if isinstance(v, float) else str(v)


def _ci(o, k, pct=True, nd=1):
    ci = (o.get("ci95") or {}).get(k)
    if not ci:
        return ""
    return f" [{100 * ci[0]:.{nd}f}, {100 * ci[1]:.{nd}f}]" if pct else f" [{ci[0]:.{nd}f}, {ci[1]:.{nd}f}]"


def table_md(res):
    a = res["area"]
    hdr = ["detector", "recall perm % [95 % CI]", "false alarm never % [CI]", "false km²/date", "bal. acc. % [CI]",
           "Brier (bal.)", "AUC", "flips perm", "flips never", "far-from-record water km² ever (all / TEST)"]
    if a == "sevan":
        hdr += ["largest body km² (calm MAE)", "windy dip km²"]
    if a == "tonle-sap":
        hdr += ["rank ρ (amb., TEST)", "late water inside max extent"]
    hdr += ["water km² first → last", "cost"]
    lines = ["| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]
    for n, o in res["rows"].items():
        r = [f"**{o['label']}** (`{n}`)",
             _fmt(o["recall_perm"], 2, True) + _ci(o, "recall_perm"),
             _fmt(o["fpr_never"], 3, True) + _ci(o, "fpr_never", nd=3),
             _fmt(o["fp_km2_per_date"], 2),
             _fmt(o["balanced_acc"], 2, True) + _ci(o, "balanced_acc", nd=2),
             _fmt(o.get("brier_bal"), 4), _fmt(o.get("auc"), 4),
             _fmt(o["flips_perm"], 3), _fmt(o["flips_never"], 4),
             f"{o['far_fp_km2_ever']:.1f} / {o['far_fp_km2_test_ever']:.1f}"]
        if a == "sevan":
            r += [f"{min(o['sevan_largest_km2']):.0f}–{max(o['sevan_largest_km2']):.0f} ({o['sevan_mae_calm_km2']:.0f})",
                  f"{o['sevan_windy_dip_km2']:.0f}"]
        if a == "tonle-sap":
            r += [_fmt(o.get("rank_rho"), 3), _fmt(o.get("late_share_inside_max_extent"), 3)]
        r += [f"{o['water_km2'][0]:.0f} → {o['water_km2'][-1]:.0f}", o["cost"]]
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def agree_md(res):
    names = res["detectors"]
    M = res["agreement_last_date_iou"]
    lines = ["| IoU | " + " | ".join(f"`{n}`" for n in names) + " |", "|---|" + "---|" * len(names)]
    for a in names:
        lines.append(f"| `{a}` | " + " | ".join(_fmt(M[a][b], 2) for b in names) + " |")
    return "\n".join(lines)


def near_copies(res, thr=0.9):
    names = [n for n in res["detectors"] if n != "fused"]
    M = res["agreement_last_date_iou"]
    out = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if M[a][b] is not None and M[a][b] >= thr:
                out.append((a, b, M[a][b]))
    return sorted(out, key=lambda x: -x[2])


# ------------------------------------------------------------------ figures
def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def _save(fig, name):
    plt = _plt()
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    fig.savefig(p, dpi=fig.dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  wrote {p.relative_to(ROOT)} ({p.stat().st_size / 1024:.0f} KB)")


def fig_heat(board):
    """Scoreboard as a heat-map table: each column coloured by rank within the area (green = better)."""
    plt = _plt()
    cols = [("balanced_acc", "bal. acc.", True), ("recall_perm", "recall perm", True),
            ("fpr_never", "false alarm", False), ("far_fp_km2_ever", "far water km² ever", False),
            ("flips_stable", "flips stable", False), ("brier_bal", "Brier", False), ("auc", "AUC", True),
            ("sevan_mae_calm_km2", "Sevan MAE", False), ("sevan_windy_dip_km2", "Sevan dip", False),
            ("rank_rho", "rank ρ", True)]
    areas = [a for a in AREAS if a in board]
    fig, axs = plt.subplots(1, len(areas), figsize=(6.2 * len(areas), 5.2), dpi=72)
    for ax, a in zip(np.atleast_1d(axs), areas):
        res = board[a]
        names = res["detectors"]
        cc = [c for c in cols if any(res["rows"][n].get(c[0]) is not None for n in names)]
        Z = np.full((len(names), len(cc)), np.nan)
        txt = [["" for _ in cc] for _ in names]
        for j, (k, _, hi) in enumerate(cc):
            vals = np.array([np.nan if res["rows"][n].get(k) is None else abs(float(res["rows"][n][k]))
                             if k == "sevan_windy_dip_km2" else float(res["rows"][n][k]) for n in names])
            ok = np.isfinite(vals)
            if ok.sum() > 1:
                lo, hi_ = np.nanmin(vals), np.nanmax(vals)
                z = (vals - lo) / (hi_ - lo) if hi_ > lo else np.full_like(vals, 0.5)
                Z[:, j] = z if hi else 1 - z
            for i, v in enumerate(vals):
                if np.isfinite(v):
                    txt[i][j] = (f"{100 * v:.1f}" if k in ("balanced_acc", "recall_perm") else
                                 f"{100 * v:.2f}" if k == "fpr_never" else f"{v:.3f}" if v < 10 else f"{v:.0f}")
        ax.imshow(Z, cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
        for i in range(len(names)):
            for j in range(len(cc)):
                ax.text(j, i, txt[i][j], ha="center", va="center", fontsize=7)
        ax.set_xticks(range(len(cc)), [c[1] for c in cc], rotation=40, ha="right", fontsize=8)
        ax.set_yticks(range(len(names)), [res["rows"][n]["label"] for n in names], fontsize=8)
        ax.set_title(f"{TITLES[a]} (TEST anchors; % where shown)", fontsize=9)
    fig.subplots_adjust(wspace=0.75)
    fig.suptitle("Scoreboard: colour = rank within the column (green better); text = value", fontsize=10)
    _save(fig, "scoreboard_heat.png")


def fig_agree(board):
    plt = _plt()
    areas = [a for a in AREAS if a in board]
    fig, axs = plt.subplots(1, len(areas), figsize=(5.4 * len(areas), 4.8), dpi=72)
    for ax, a in zip(np.atleast_1d(axs), areas):
        res = board[a]
        names = res["detectors"]
        M = np.array([[np.nan if res["agreement_last_date_iou"][x][y] is None else res["agreement_last_date_iou"][x][y]
                       for y in names] for x in names])
        im = ax.imshow(M, cmap="viridis", vmin=0, vmax=1)
        for i in range(len(names)):
            for j in range(len(names)):
                if np.isfinite(M[i, j]):
                    ax.text(j, i, f"{M[i, j]:.2f}", ha="center", va="center", fontsize=6,
                            color="k" if M[i, j] > 0.6 else "w")
        ax.set_xticks(range(len(names)), names, rotation=60, ha="right", fontsize=7)
        ax.set_yticks(range(len(names)), names, fontsize=7)
        ax.set_title(f"{TITLES[a]}: IoU of water on {res['dates'][-1]}", fontsize=9)
    fig.colorbar(im, ax=axs, shrink=0.7, label="IoU")
    _save(fig, "agreement.png")


def fig_reliability(board):
    plt = _plt()
    areas = [a for a in AREAS if a in board and any(o.get("reliability") for o in board[a]["rows"].values())]
    if not areas:
        return
    fig, axs = plt.subplots(1, len(areas), figsize=(4.6 * len(areas), 4.2), dpi=72)
    for ax, a in zip(np.atleast_1d(axs), areas):
        for n, o in board[a]["rows"].items():
            rel = o.get("reliability")
            if not rel:
                continue
            x = [r["mean_p"] for r in rel]; y = [r["frac_water"] for r in rel]
            fused = n == "fused"
            ax.plot(x, y, "o-" if fused else ".-", lw=2.5 if fused else 1, ms=6 if fused else 4,
                    color="k" if fused else None, label=f"{n} (Brier {o['brier_bal']})" if o.get("brier_bal")
                    is not None else n, alpha=1 if fused else 0.75)
        ax.plot([0, 1], [0, 1], ":", color="grey")
        ax.set_xlabel("mean predicted P(water) in bin"); ax.set_ylabel("share of water (class-balanced)")
        ax.set_title(f"{TITLES[a]}: reliability on TEST anchors", fontsize=9)
        ax.legend(fontsize=6); ax.grid(alpha=0.3)
    fig.suptitle("Before (each expert) and after fusion (black). Anchors are easy pixels: most mass sits at 0 and 1.",
                 fontsize=9)
    _save(fig, "reliability.png")


def fig_sevan(board):
    if "sevan" not in board:
        return
    plt = _plt()
    import matplotlib.dates as mdates
    res = board["sevan"]
    d = [dt.date.fromisoformat(x) for x in res["dates"]]
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=72)
    for n, o in res["rows"].items():
        style = dict(lw=2.5, color="k", marker="o") if n == "fused" else dict(lw=2, color="#d95f02", marker="s") \
            if n == "math" else dict(lw=0.8, alpha=0.6, marker=".")
        ax.plot(d, o["sevan_largest_km2"], label=f"{n} (calm MAE {o['sevan_mae_calm_km2']:.0f})", **style)
    ax.axhline(PUBLISHED_SEVAN_KM2, color="b", ls="--", lw=1, label="published 1,242 km²")
    for i in SEVAN_WINDY:
        ax.axvspan(d[i] - dt.timedelta(days=3), d[i] + dt.timedelta(days=3), color="grey", alpha=0.2)
    ax.set_ylabel("largest connected water body, km²")
    ax.set_title("Lake Sevan per date (grey = the two windy dates)", fontsize=9)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.legend(fontsize=6, ncol=2); ax.grid(alpha=0.3)
    _save(fig, "sevan_area.png")


def fig_tonle(board):
    if "tonle-sap" not in board:
        return
    plt = _plt()
    import matplotlib.dates as mdates
    res = board["tonle-sap"]
    d = [dt.date.fromisoformat(x) for x in res["dates"]]
    fig, ax = plt.subplots(figsize=(8, 4.4), dpi=72)
    fa = res.get("fused_area") or {}
    if "water_km2_lo" in fa:
        ax.fill_between(d, fa["water_km2_lo"], fa["water_km2_hi"], color="k", alpha=0.15,
                        label="fused: area at P>0.75 .. P>0.25")
    if "water_km2_expected" in fa:
        ax.plot(d, fa["water_km2_expected"], "k--", lw=2, label="fused expected area Σγ")
    for n, o in res["rows"].items():
        style = dict(lw=2.5, color="k", marker="o") if n == "fused" else dict(lw=2, color="#d95f02", marker="s") \
            if n == "math" else dict(lw=0.9, alpha=0.7, marker=".")
        ax.plot(d, o["water_km2"], label=n, **style)
    ax.set_ylabel("open water, km² (common footprint)")
    ax.set_title("Tonle Sap open water per date, no 2026 truth exists here", fontsize=9)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.legend(fontsize=6, ncol=2); ax.grid(alpha=0.3)
    _save(fig, "tonle_area.png")


def fig_disagree(maps):
    plt = _plt()
    areas = [a for a in AREAS if a in maps]
    fig, axs = plt.subplots(1, len(areas), figsize=(5.2 * len(areas), 4.6), dpi=60)
    for ax, a in zip(np.atleast_1d(axs), areas):
        m = maps[a]
        v = m["votes"].float().numpy()
        v[~m["foot"].numpy()] = np.nan
        w, s, e, n = m["bounds"]
        im = ax.imshow(v, cmap="magma", vmin=0, vmax=m["n_voters"], extent=(w, e, s, n), interpolation="nearest")
        fig.colorbar(im, ax=ax, shrink=0.75, label=f"detectors calling water (of {m['n_voters']})")
        ax.set_title(f"{TITLES[a]}, last date", fontsize=9)
        ax.tick_params(labelsize=7)
    fig.suptitle("Where detectors disagree: 0 / max = all agree; middle values = disagreement", fontsize=9)
    _save(fig, "disagreement_map.png")


# ------------------------------------------------------------------ markdown
def write_md(board):
    L = ["# Scoreboard: every water detector measured the same way", "",
         f"Generated {dt.datetime.now().isoformat(timespec='minutes')} by `.venv/bin/python -m scripts.detect.scoreboard` "
         "(code: [`scripts/detect/scoreboard.py`](../../scripts/detect/scoreboard.py); numbers: "
         "[`fusion/results.json`](fusion/results.json), section `scoreboard`). The fusion layer that uses it: "
         "[fusion.md](fusion.md).", "",
         "## How every detector is measured", "",
         "* **Same pixels.** The intersection of the valid footprints of all detectors available in the area "
         "(swath-edge band and partly imaged pixels already removed by the math footprint).",
         "* **Same labels and split** as [tuning.md](tuning.md): from the JRC Global Surface Water record 1984–2021, "
         "*permanent water* (≥ 90 % occurrence and water all 12 months of 2021, minus a 2 px shoreline band), "
         "*never water* (no 30 m pixel ever water within 3 px), and *ambiguous* (everything else). Column strips "
         "tune / validation / TEST with 10 px buffers; Ararat is entirely TEST. **All anchor numbers below are on "
         "the TEST strip.** The TEST strip had already been looked at three times by the threshold tuning "
         "(tuning.md), so it is not perfectly untouched.",
         "* **Metrics.** Recall on permanent-water pixel-dates; false-alarm rate and false km² per date on "
         "never-water pixel-dates; balanced accuracy = ½(recall + 1 − false-alarm rate); 95 % intervals from a "
         f"{BOOT}× bootstrap over 20 × 20 px blocks. For probabilistic detectors: Brier score (class-balanced: mean "
         "of the two classes' Brier scores), AUC, reliability curve. *Flips* = water↔land switches per pixel over "
         "the ten dates on TEST anchors (a stable target should have 0). *Far-from-record water* = water more "
         "than 3 px from any water in 38 years of the record (the never-water label), over the whole area and on "
         "the TEST strip. Sevan: largest connected water body per date against the published 1,242 km², mean "
         "absolute error on the eight calm dates and the dip on the two windy dates (31 Aug, 12 Sep) = mean of the "
         "calm dates − mean of the windy dates. Tonle Sap has **no 2026 truth**: only open-water km², the rank "
         "correlation between long-term occurrence and number of dates called water in the ambiguous zone, and the "
         "share of late-season water inside the historical maximum extent.",
         "* **Cost** is from the detector's notes or product file where recorded.", ""]
    for a in AREAS:
        if a not in board:
            continue
        res = board[a]
        L += [f"## {TITLES[a]}", "",
              f"Common footprint {res['footprint_km2']:.0f} km²; TEST anchors: {res['test_anchor_px']['perm']} "
              f"permanent-water px, {res['test_anchor_px']['never']} never-water px "
              f"(× {len(res['dates'])} dates). Detectors: {', '.join('`' + n + '`' for n in res['detectors'])}.", "",
              table_md(res), "", "Agreement (IoU of the water masks on the last date):", "", agree_md(res), ""]
        nc = near_copies(res)
        if nc:
            L += ["Near-copies (IoU ≥ 0.90): " + "; ".join(f"`{x}`–`{y}` {v:.2f}" for x, y, v in nc[:12]) + ".", ""]
    L += ["## Figures", "",
          "![scoreboard](fusion/scoreboard_heat.png)", "", "![agreement](fusion/agreement.png)", "",
          "![reliability](fusion/reliability.png)", "", "![sevan](fusion/sevan_area.png)", "",
          "![tonle](fusion/tonle_area.png)", "", "![disagreement](fusion/disagreement_map.png)", ""]
    L += ["## What it shows", "", summary_text(board), ""]
    (ROOT / "docs/eda/scoreboard.md").write_text("\n".join(L))
    print("  wrote docs/eda/scoreboard.md")


def summary_text(board):
    """Three sentences computed from the numbers (so they stay true after a re-run)."""
    s = []
    ba = {}
    for a in AREAS:
        if a not in board:
            continue
        for n, o in board[a]["rows"].items():
            v = ba_or_spec(o)
            if v is not None:
                ba.setdefault(n, []).append(v)
    full = {n: float(np.mean(v)) for n, v in ba.items() if len(v) == len([a for a in AREAS if a in board])}
    if full:
        best = sorted(full.items(), key=lambda x: -x[1])
        s.append("On the TEST anchors almost every detector is near the ceiling (mean balanced accuracy / Ararat "
                 "specificity: " + ", ".join(f"`{n}` {100 * v:.2f} %" for n, v in best[:5]) +
                 "), so anchor accuracy cannot separate them; the differences are in false water far from the "
                 "record, in the windy Sevan dates and in date-to-date flips.")
    cps = []
    for a in AREAS:
        if a in board:
            cps += [f"{x}–{y}" for x, y, v in near_copies(board[a], 0.9)]
    if cps:
        from collections import Counter
        c = Counter(cps)
        s.append("The agreement matrix shows near-copies (last-date IoU ≥ 0.9), most often " +
                 ", ".join(f"`{k}`" for k, _ in c.most_common(5)) +
                 ", which is expected: several experts were trained on, or derived from, the math detector's masks.")
    if "sevan" in board:
        r = board["sevan"]["rows"]
        mm = r.get("math")
        if mm:
            bet = [n for n, o in r.items() if abs(o["sevan_windy_dip_km2"]) < abs(mm["sevan_windy_dip_km2"]) - 10]
            s.append(f"At Sevan the math detector's windy-date dip is {mm['sevan_windy_dip_km2']:.0f} km² "
                     f"(calm MAE {mm['sevan_mae_calm_km2']:.0f} km²); detectors with a smaller |dip|: "
                     + (", ".join(f"`{n}` ({r[n]['sevan_windy_dip_km2']:.0f})" for n in bet) if bet else "none") + ".")
    return " ".join(s)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    areas = [a for a in argv if a in AREAS] or AREAS
    t0 = time.time()
    board, maps = {}, {}
    p = OUT / "results.json"
    if p.exists():  # keep areas not re-run
        board = {k: v for k, v in json.loads(p.read_text()).get("scoreboard", {}).items() if k in AREAS}
    for a in areas:
        board[a], maps[a] = score_area(a)
        r = board[a]["rows"]
        for n, o in r.items():
            print(f"  {a:9s} {n:15s} BA {_fmt(ba_or_spec(o), 3, True):>7s}  far-fp ever {o['far_fp_km2_ever']:7.1f} "
                  f"flips {o['flips_stable']:.4f}" + (f" sevan MAE {o['sevan_mae_calm_km2']:.0f} dip "
                                                       f"{o['sevan_windy_dip_km2']:.0f}" if a == "sevan" else ""))
        _free()
    merge_results("scoreboard", board)
    fig_heat(board); fig_agree(board); fig_reliability(board); fig_sevan(board); fig_tonle(board)
    if maps:
        fig_disagree(maps)
    write_md(board)
    print(f"scoreboard done in {time.time() - t0:.0f}s")
    return board


if __name__ == "__main__":
    main()
