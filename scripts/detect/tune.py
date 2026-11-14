"""Tune-then-test the radar water thresholds against JRC Global Surface Water (independent optical reference).

    .venv/bin/python -m scripts.detect.tune               # full search (400 evals) + held-out evaluation
    .venv/bin/python -m scripts.detect.tune --evals 200   # fewer search evaluations
    .venv/bin/python -m scripts.detect.tune --quick       # smoke run (60 evals, 100 bootstrap resamples)

Procedure (write-up: docs/eda/tuning.md):
  1. Labels from GSW 1984-2021 (resampled with validate.reference_on_grid):
     permanent = >= 90 % of the pixel has occurrence >= 90 % AND >= 50 % is water all 12 months of 2021,
                 shrunk by a 2 px shoreline band;
     never     = no 30 m pixel ever water (occurrence 0 and outside maximum extent) within 3 px;
     ambiguous = everything else in the domain (NOT labelled; used only for the rank-consistency check).
  2. Spatial split fixed before any tuning (column strips with 10 px buffers): Tonle Sap west = tune,
     middle strip = validation, east = TEST; Sevan likewise; Ararat entirely TEST.
  3. Seeded random + local search on the tune strips only; the validation strip picks among the top 20.
  4. TEST touched once, with block-bootstrap confidence intervals (20x20 px blocks).

Speed: the footprint / filtering / normalisation are cached per (box, quegan); the per-setting water mask
uses a connected-component shortcut that is exactly ops.hysteresis + ops.remove_small (checked against
ops.run_pipeline at the end and in tests/test_tune.py).
Writes docs/eda/tuning/{results.json,best_knobs.json,*.png} and data/processed/tuning/*.pt.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import time

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import scipy.ndimage as ndi  # noqa: E402
import torch  # noqa: E402

from scripts.detect import ops  # noqa: E402
from scripts.detect.stack import ROOT, device, load_preview_stack  # noqa: E402
from scripts.detect.validate import PUBLISHED_SEVAN_KM2, reference_on_grid  # noqa: E402

AREAS = ["tonle-sap", "sevan", "ararat"]
CACHE = ROOT / "data/processed/tuning"
OUT = ROOT / "docs/eda/tuning"
SEED = 20261114
BUF = 10          # px left out between split strips
BLOCK = 20        # px, bootstrap block
LAMBDA = 0.2      # weight of the rank-consistency term in the objective
# column fractions [lo, hi) of each strip; buffers are cut from the inner edges
SPLIT = {
    "tonle-sap": {"tune": (0.0, 0.42), "val": (0.42, 0.50), "test": (0.50, 1.0)},  # lake spans cols 10-60 %
    "sevan": {"tune": (0.0, 0.45), "val": (0.45, 0.55), "test": (0.55, 1.0)},
    "ararat": {"test": (0.0, 1.0)},
}
LABEL_RULE = {"perm_occ90_frac": 0.9, "perm_seas12_frac": 0.5, "shore_px": 2, "never_dist_px": 3,
              "ref_valid_frac": 0.99}
OLD = {k: ops.KNOBS["preview"][k] for k in
       ("water_hh_strong", "water_hh_weak", "water_hv_max", "box", "quegan", "min_blob_px")}
OLD.update(hv_w=0.0, terrain=False)
TITLES = {"tonle-sap": "Tonle Sap", "sevan": "Lake Sevan", "ararat": "Ararat valley"}


# ------------------------------------------------------------------ data + labels
def base(area):
    """Stack with the ops footprint rules applied (no-data margin, common footprint, edge erosion) + GSW."""
    path = CACHE / f"{area}_base.pt"
    if path.exists():
        return torch.load(path, weights_only=False)
    st = load_preview_stack(area)
    kn = ops.KNOBS["preview"]
    x, valid = ops.clean_input(st.x, st.valid)
    valid = valid & ~ops.nodata_margin(x, valid, kn["nodata_floor"])
    valid = valid & valid.all(0, keepdim=True)
    valid = ops.erode(valid, 2 * kn["edge_erode_px"] + 1)
    T, _, H, W = x.shape
    ref, _ = reference_on_grid(area, st.bounds, H, W)
    terrain = None
    tp = ROOT / f"data/processed/{area}/terrain.pt"
    if tp.exists():
        try:
            t = torch.load(tp, weights_only=False)
            wp = t["water_possible"] if isinstance(t, dict) else t
            if tuple(wp.shape[-2:]) == (H, W):
                terrain = wp.bool().reshape(H, W)
        except Exception as e:  # never depend on it
            print(f"  terrain mask unreadable: {e}")
    b = {"area": area, "x": x, "valid": valid[0], "dates": st.dates, "bounds": st.bounds,
         "km2": st.km2_per_px, "ref": ref, "terrain": terrain}
    CACHE.mkdir(parents=True, exist_ok=True)
    if terrain is None:  # do not cache a missing terrain mask; it may appear later
        torch.save(b, path)
    return b


def build_labels(ref, footprint, perm_occ90_frac=0.9, perm_seas12_frac=0.5, shore_px=2, never_dist_px=3,
                 ref_valid_frac=0.99):
    """Three-way label masks [H, W] bool from GSW fractions on our grid.

    ref: dict with valid_frac, occ_ge90_frac, seas12_frac, occ_any_frac, ext_frac ([H, W] float).
    Returns dict(domain, perm, never, amb). perm/never exclude a shoreline band; amb = domain - perm - never.
    """
    dom = footprint & (ref["valid_frac"] >= ref_valid_frac)
    raw = (ref["occ_ge90_frac"] >= perm_occ90_frac) & (ref["seas12_frac"] >= perm_seas12_frac)
    perm = ops.erode(raw[None], 2 * shore_px + 1)[0] & dom if shore_px > 0 else raw & dom
    ever = (ref["occ_any_frac"] > 0) | (ref["ext_frac"] > 0)
    never = ~ops.dilate(ever[None], 2 * never_dist_px + 1)[0] & dom
    amb = dom & ~perm & ~never
    return {"domain": dom, "perm": perm, "never": never, "amb": amb}


def split_masks(H, W, spec, buf=BUF):
    """Column strips {name: [H, W] bool}; a strip loses buf//2 px at each inner edge (buffer = buf px)."""
    out = {}
    for name, (lo, hi) in spec.items():
        c0, c1 = round(lo * W), round(hi * W)
        if lo > 0:
            c0 += buf // 2
        if hi < 1:
            c1 -= buf - buf // 2
        m = torch.zeros(H, W, dtype=torch.bool)
        m[:, c0:c1] = True
        out[name] = m
    return out


# ------------------------------------------------------------------ detection (fast, exact)
def filtered(b, box, quegan, dev):
    """Filtered + normalised log-like stack [T,2,H,W] (CPU), as ops.run_pipeline computes it.
    The normalisation excludes pixels whose median HH is < 0.12 (ops uses the candidate's weak threshold;
    the difference is checked at the end against ops.run_pipeline)."""
    x = b["x"].to(dev)
    v = b["valid"].to(dev)[None].expand(x.shape[0], -1, -1).contiguous()
    xf = ops.quegan_filter(x, v, box) if quegan else ops.box_mean(x, v, box)
    L = xf * v[:, None]
    med = torch.where(v, L[:, 0], torch.full_like(L[:, 0], float("nan"))).nanmedian(0).values
    L, _, _ = ops.normalise_dates(L, v, ops.KNOBS["preview"]["stable_frac"], mode="gain", exclude=~(med >= 0.12))
    return L.cpu()


def water_fast(L, valid, strong, weak, hv_max, hv_w=0.0, min_blob=1, terrain=None):
    """Per-date water [T,H,W] bool = ops.hysteresis(s, strong, weak, valid & HV<hv_max) then
    ops.remove_small(min_blob), with s = (1-hv_w)*HH + hv_w*HV.

    The hysteresis result is a union of whole 8-connected components of the weak mask, so one
    component labelling gives both the seed test and the component sizes."""
    hh, hv = L[:, 0], L[:, 1]
    s = hh if hv_w == 0 else (1 - hv_w) * hh + hv_w * hv
    ok = valid[None] & (hv < hv_max)
    wk = (ok & (s < weak)).numpy()
    sg = (ok & (s < strong)).numpy()
    out = np.zeros_like(wk)
    st8 = np.ones((3, 3), bool)
    for t in range(wk.shape[0]):
        lab, n = ndi.label(wk[t], structure=st8)
        if n == 0:
            continue
        seeded = np.bincount(lab[sg[t]], minlength=n + 1) > 0
        keep = seeded & (np.bincount(lab.ravel(), minlength=n + 1) >= max(min_blob, 1))
        keep[0] = False
        out[t] = keep[lab]
    w = torch.from_numpy(out)
    if terrain is not None:
        w = w & terrain[None]
    return w


# ------------------------------------------------------------------ metrics
def avg_rank(v):
    """Average ranks (ties share the mean rank), float64, 1-based."""
    v = v.double()
    order = torch.argsort(v)
    sv = v[order]
    _, inv, cnt = torch.unique_consecutive(sv, return_inverse=True, return_counts=True)
    ends = cnt.cumsum(0).double()
    avg = ends - (cnt.double() - 1) / 2
    r = torch.empty_like(v)
    r[order] = avg[inv]
    return r


def wpearson(a, b, w=None):
    if w is None:
        w = torch.ones_like(a)
    w = w.double()
    sw = w.sum().clamp_min(1e-12)
    ma, mb = (w * a).sum() / sw, (w * b).sum() / sw
    cov = (w * (a - ma) * (b - mb)).sum()
    den = ((w * (a - ma) ** 2).sum() * (w * (b - mb) ** 2).sum()).sqrt()
    return float(cov / den) if den > 0 else float("nan")


class Region:
    """Labelled pixels of one area-strip, with block ids for the bootstrap and occurrence ranks."""

    def __init__(self, lab, strip, occ, km2, T):
        H, W = strip.shape
        yy, xx = torch.meshgrid(torch.arange(H), torch.arange(W), indexing="ij")
        bid = (yy // BLOCK) * ((W + BLOCK - 1) // BLOCK) + xx // BLOCK
        self.perm, self.never, self.amb = lab["perm"] & strip, lab["never"] & strip, lab["amb"] & strip
        self.km2, self.T = km2, T
        self.n_perm, self.n_never, self.n_amb = int(self.perm.sum()), int(self.never.sum()), int(self.amb.sum())
        self.bid_perm, self.bid_never, self.bid_amb = bid[self.perm], bid[self.never], bid[self.amb]
        self.occ_amb = occ[self.amb]
        self.r_occ = avg_rank(self.occ_amb) if self.n_amb else None
        used = torch.cat([self.bid_perm, self.bid_never, self.bid_amb]).unique()
        self.blocks = used
        self.nb = int(bid.max()) + 1

    def counts(self, water):
        """Per-pixel water-date counts on perm / never / amb pixels."""
        nw = water.sum(0)
        return nw[self.perm], nw[self.never], nw[self.amb]

    def metrics(self, water, w_block=None, counts=None):
        cp, cn, ca = counts if counts is not None else self.counts(water)
        T = self.T

        def wsum(vals, bid):
            if w_block is None:
                return float(vals.double().sum()), float(vals.numel())
            ww = w_block[bid]
            return float((vals.double() * ww).sum()), float(ww.sum())
        tp, np_ = wsum(cp, self.bid_perm)
        fp, nn = wsum(cn, self.bid_never)
        rec = tp / (np_ * T) if np_ else float("nan")
        fpr = fp / (nn * T) if nn else float("nan")
        fn = np_ * T - tp
        rho = float("nan")
        if self.n_amb > 50:
            rho = wpearson(self.r_occ, avg_rank(ca.double()), None if w_block is None else w_block[self.bid_amb])
        ba = 0.5 * (rec + 1 - fpr) if np_ else float("nan")
        return {"recall_perm": rec, "fpr_never": fpr, "spec_never": 1 - fpr if nn else float("nan"),
                "fp_km2_per_date": fp / T * self.km2, "balanced_acc": ba,
                "iou": tp / max(tp + fp + fn, 1) if np_ else float("nan"), "rank_rho": rho}

    def bootstrap(self, water, n, gen):
        """Block bootstrap: resample the 20x20 blocks that hold labelled pixels, n times; 95 % percentile CI."""
        keys = ["recall_perm", "fpr_never", "fp_km2_per_date", "balanced_acc", "iou", "rank_rho"]
        vals = {k: [] for k in keys}
        nb = self.blocks.numel()
        cnt = self.counts(water)
        for _ in range(n):
            pick = self.blocks[torch.randint(0, nb, (nb,), generator=gen)]
            w = torch.bincount(pick, minlength=self.nb).double()
            m = self.metrics(water, w, cnt)
            for k in keys:
                vals[k].append(m[k])
        out = {}
        for k in keys:
            a = np.array(vals[k], float)
            a = a[np.isfinite(a)]
            out[k] = [round(float(np.percentile(a, 2.5)), 5), round(float(np.percentile(a, 97.5)), 5)] if a.size else None
        return out


def objective(ms):
    """J = mean over areas of balanced accuracy + LAMBDA * mean rank correlation (areas where defined)."""
    ba = [m["balanced_acc"] for m in ms if np.isfinite(m["balanced_acc"])]
    rho = [m["rank_rho"] for m in ms if np.isfinite(m["rank_rho"])]
    return float(np.mean(ba)) + LAMBDA * (float(np.mean(rho)) if rho else 0.0)


# ------------------------------------------------------------------ search
class Ctx:
    def __init__(self, areas, dev):
        self.dev = dev
        self.B = {a: base(a) for a in areas}
        self.Lc = {}
        self.lab, self.reg = {}, {}
        for a, b in self.B.items():
            H, W = b["valid"].shape
            T = b["x"].shape[0]
            self.lab[a] = build_labels(b["ref"], b["valid"], **LABEL_RULE)
            sm = split_masks(H, W, SPLIT[a])
            sm["all"] = torch.ones(H, W, dtype=torch.bool)
            self.reg[a] = {k: Region(self.lab[a], m, b["ref"]["occ_mean"], b["km2"], T) for k, m in sm.items()}
            self.reg[a]["_masks"] = sm
        self.has_terrain = any(b["terrain"] is not None for b in self.B.values())

    def L(self, a, box, quegan):
        k = (a, box, quegan)
        if k not in self.Lc:
            self.Lc[k] = filtered(self.B[a], box, quegan, self.dev)
        return self.Lc[k]

    def water(self, a, s):
        b = self.B[a]
        terr = b["terrain"] if s.get("terrain") else None
        return water_fast(self.L(a, s["box"], s["quegan"]), b["valid"], s["water_hh_strong"], s["water_hh_weak"],
                          s["water_hv_max"], s["hv_w"], s["min_blob_px"], terr)

    def score(self, s, regions):
        """regions: list of (area, strip). Returns (J, per-region metrics)."""
        ms, cache = {}, {}
        for a, r in regions:
            if a not in cache:
                cache[a] = self.water(a, s)
            ms[f"{a}/{r}"] = self.reg[a][r].metrics(cache[a])
        return objective(list(ms.values())), ms


def sample(rng, has_terrain, around=None, scale=1.0):
    if around is None:
        strong = float(rng.uniform(0.02, 0.22))
        s = {"water_hh_strong": strong, "water_hh_weak": strong + float(rng.uniform(0.0, 0.22)),
             "water_hv_max": float(rng.choice([0.2, 0.25, 0.3, 0.35, 0.45, 1.0])),
             "box": int(rng.choice([3, 5, 7])), "quegan": bool(rng.integers(0, 2)),
             "min_blob_px": int(rng.choice([1, 4, 9, 16])), "hv_w": float(rng.choice([0.0, 0.0, 0.25, 0.5])),
             "terrain": bool(rng.integers(0, 2)) if has_terrain else False}
    else:
        s = dict(around)
        s["water_hh_strong"] = float(np.clip(s["water_hh_strong"] + rng.normal(0, 0.015 * scale), 0.01, 0.3))
        s["water_hh_weak"] = float(np.clip(s["water_hh_weak"] + rng.normal(0, 0.02 * scale), 0.01, 0.5))
        if rng.random() < 0.2:
            s["water_hv_max"] = float(rng.choice([0.2, 0.25, 0.3, 0.35, 0.45, 1.0]))
        if rng.random() < 0.2:
            s["min_blob_px"] = int(rng.choice([1, 4, 9, 16]))
        if rng.random() < 0.15:
            s["box"] = int(rng.choice([3, 5, 7]))
        if rng.random() < 0.15:
            s["quegan"] = not s["quegan"]
    s["water_hh_weak"] = max(s["water_hh_weak"], s["water_hh_strong"])  # constraint weak >= strong
    for k in ("water_hh_strong", "water_hh_weak"):
        s[k] = round(s[k], 4)
    return s


def search(ctx, regions, n, seed):
    rng = np.random.default_rng(seed)
    hist = []
    n_rand = int(n * 0.7)
    for i in range(n):
        if i == 0:
            s = dict(OLD)
        elif i < n_rand:
            s = sample(rng, ctx.has_terrain)
        else:
            top = sorted(hist, key=lambda h: -h["J"])[:5]
            s = sample(rng, ctx.has_terrain, around=top[int(rng.integers(0, len(top)))]["s"],
                       scale=1.0 - 0.7 * (i - n_rand) / max(n - n_rand, 1))
        J, ms = ctx.score(s, regions)
        hist.append({"s": s, "J": J, "m": ms})
    return hist


def km2_series(ctx, a, w):
    b = ctx.B[a]
    return [round(float((w[t] & b["valid"]).sum()) * b["km2"], 1) for t in range(w.shape[0])]


def rnd(d, nd=5):
    return {k: (round(v, nd) if isinstance(v, float) else v) for k, v in d.items()}


# ------------------------------------------------------------------ baselines
def baselines(ctx, a):
    """Fixed, label-free alternatives: old knobs, single Otsu on filtered HH, EDA's 2nd-level Otsu on raw mean."""
    b = ctx.B[a]
    v = b["valid"]
    L = ctx.L(a, 3, True)
    hh = L[:, 0][v[None].expand_as(L[:, 0])]
    t_otsu = ops.otsu(hh[:: max(1, hh.numel() // 4_000_000)])
    raw = b["x"].mean(1)
    rv = raw[v[None].expand_as(raw)]
    from scripts.detect.eda import otsu2
    _, t2 = otsu2(rv[:: max(1, rv.numel() // 6_000_000)])
    return {"old_knobs": ctx.water(a, OLD),
            "otsu_hh": (L[:, 0] < t_otsu) & v[None],
            "otsu2_mean": (raw < t2) & v[None]}, {"otsu_hh": t_otsu, "otsu2_mean": t2}


# ------------------------------------------------------------------ figures
def _save(fig, name):
    p = OUT / name
    fig.savefig(p, dpi=fig.dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  wrote {p.relative_to(ROOT)} ({p.stat().st_size / 1024:.0f} KB)")


def fig_split(ctx):
    areas = list(ctx.B)
    fig, axs = plt.subplots(1, len(areas), figsize=(5 * len(areas), 4.8), dpi=80)
    for ax, a in zip(np.atleast_1d(axs), areas):
        lab, sm = ctx.lab[a], ctx.reg[a]["_masks"]
        H, W = lab["domain"].shape
        img = np.full((H, W, 3), 0.8)
        img[lab["amb"].numpy()] = [0.97, 0.85, 0.55]
        img[lab["never"].numpy()] = [0.93, 0.93, 0.9]
        img[lab["perm"].numpy()] = [0.1, 0.35, 0.75]
        anyst = torch.zeros(H, W, dtype=torch.bool)
        for k in ("tune", "val", "test"):
            if k in sm:
                anyst |= sm[k]
        img[(~anyst).numpy() & lab["domain"].numpy()] *= 0.55  # buffers darker
        ax.imshow(img, interpolation="nearest")
        for k, c in (("tune", "#1b9e77"), ("val", "#7570b3"), ("test", "#d95f02")):
            if k in sm:
                cols = torch.nonzero(sm[k][0]).flatten()
                ax.axvspan(int(cols[0]), int(cols[-1]), ymin=0.97, ymax=1.0, color=c)
                ax.text((int(cols[0]) + int(cols[-1])) / 2, H * 0.06, k.upper(), color=c, ha="center",
                        fontsize=9, weight="bold", bbox=dict(fc="white", ec="none", alpha=0.7))
        ax.set_title(f"{TITLES[a]}", fontsize=10)
        ax.set_xticks([]); ax.set_yticks([])
    from matplotlib.patches import Patch
    fig.legend(handles=[Patch(color=[0.1, 0.35, 0.75], label="permanent water label"),
                        Patch(color=[0.93, 0.93, 0.9], label="never-water label"),
                        Patch(color=[0.97, 0.85, 0.55], label="ambiguous (not labelled; rank check only)"),
                        Patch(color=[0.8, 0.8, 0.8], label="outside domain")],
               loc="lower center", ncol=4, fontsize=8)
    fig.suptitle("Labels from JRC GSW 1984-2021 and the spatial split (buffers shaded darker). "
                 "Source: EC JRC/Google", fontsize=9)
    _save(fig, "split_map.png")


def fig_landscape(grid, old, new):
    st, wk, J = grid["strong"], grid["weak"], grid["J"]
    fig, ax = plt.subplots(figsize=(6.4, 5), dpi=80)
    Z = np.full((len(wk), len(st)), np.nan)
    for i, s in enumerate(st):
        for j, w in enumerate(wk):
            if (s, w) in J:
                Z[j, i] = J[(s, w)]
    im = ax.pcolormesh(st, wk, Z, shading="nearest", cmap="viridis")
    fig.colorbar(im, ax=ax, label="objective J on TUNE strips")
    ax.plot(old[0], old[1], "o", mfc="none", mec="red", ms=11, mew=2, label=f"old ({old[0]}, {old[1]})")
    ax.plot(new[0], new[1], "*", color="white", mec="k", ms=15, label=f"new ({new[0]}, {new[1]})")
    ax.set_xlabel("water_hh_strong (seed threshold)")
    ax.set_ylabel("water_hh_weak (grow threshold)")
    ax.set_title(f"Score landscape (other knobs at the chosen values)\nJ = mean balanced accuracy + {LAMBDA} x rank rho",
                 fontsize=9)
    ax.legend(fontsize=8, loc="lower right")
    _save(fig, "score_landscape.png")


def fig_curves(curves):
    fig, axs = plt.subplots(1, 2, figsize=(11, 4.4), dpi=80)
    cols = {"tonle-sap": "#08519c", "sevan": "#d94801"}
    for a, c in cols.items():
        if a not in curves:
            continue
        for fam, ls, mk in (("new", "-", "*"), ("old", ":", "o")):
            cv = curves[a][fam]
            f = np.array([p["fpr"] for p in cv]); r = np.array([p["rec"] for p in cv])
            axs[0].plot(np.maximum(f, 1e-6), r, ls, color=c, label=f"{TITLES[a]}, {fam} config family")
            k = [p["scale"] for p in cv].index(1.0)
            axs[0].plot(max(f[k], 1e-6), r[k], mk, color=c, ms=12 if mk == "*" else 8, mec="k")
            km = np.array([p["fp_km2"] for p in cv])
            axs[1].plot([p["strong"] for p in cv], km, ls, color=c, label=f"{TITLES[a]} {fam}")
    if "ararat" in curves:
        for fam, ls in (("new", "-"), ("old", ":")):
            cv = curves["ararat"][fam]
            axs[1].plot([p["strong"] for p in cv], [p["fp_km2"] for p in cv], ls, color="#555",
                        label=f"Ararat {fam}")
    axs[0].set_xscale("log")
    axs[0].set_xlabel("false-positive rate on never-water pixels (TEST, log)")
    axs[0].set_ylabel("recall on permanent water (TEST)")
    axs[0].set_title("Operating curves: thresholds scaled x0.25..x4\nstar = new knobs, circle = old knobs", fontsize=9)
    axs[0].legend(fontsize=7); axs[0].grid(alpha=0.3)
    axs[1].set_yscale("symlog", linthresh=1)
    axs[1].set_xlabel("water_hh_strong along the curve (weak scaled with it)")
    axs[1].set_ylabel("false water on never-water TEST pixels, km2 per date")
    axs[1].set_title("Cost of a looser threshold, in km2", fontsize=9)
    axs[1].legend(fontsize=7); axs[1].grid(alpha=0.3)
    _save(fig, "operating_curves.png")


def fig_rank(rank):
    fig, axs = plt.subplots(1, 2, figsize=(10, 4), dpi=80, sharey=True)
    for ax, a in zip(axs, ["tonle-sap", "sevan"]):
        if a not in rank:
            continue
        for name, c in (("new", "#1b7837"), ("old_knobs", "#b2182b"), ("otsu2_mean", "#777")):
            d = rank[a][name]
            ax.plot(d["bin_mid"], d["mean_dates"], "o-", color=c, label=f"{name} (rho {d['rho']:.2f})")
        ax.set_title(f"{TITLES[a]}, ambiguous zone of the TEST strip", fontsize=9)
        ax.set_xlabel("GSW long-term occurrence (%)")
        ax.grid(alpha=0.3); ax.legend(fontsize=7)
    axs[0].set_ylabel("mean number of our 10 dates detected as water")
    fig.suptitle("Rank consistency: wetter-in-the-record pixels should be water on more 2026 dates. "
                 "Source: EC JRC/Google", fontsize=9)
    _save(fig, "occurrence_vs_frequency.png")


def fig_areas(ctx, area_res):
    fig, axs = plt.subplots(1, 2, figsize=(11, 4.2), dpi=80)
    for ax, a in zip(axs, ["tonle-sap", "sevan"]):
        if a not in area_res:
            continue
        R = area_res[a]
        d = [dt.date.fromisoformat(x) for x in ctx.B[a]["dates"]]
        ax.fill_between(d, R["top5pct_min"], R["top5pct_max"], color="#1b7837", alpha=0.2,
                        label=f"top 5 % of settings (n={R['top5pct_n']})")
        ax.plot(d, R["new"], "*-", color="#1b7837", label="new knobs")
        ax.plot(d, R["old_knobs"], "o:", color="#b2182b", label="old knobs")
        ax.plot(d, R["otsu2_mean"], "s--", color="#777", ms=4, label="EDA 2nd-level Otsu")
        if a == "sevan":
            ax.axhline(PUBLISHED_SEVAN_KM2, color="k", lw=0.8, ls="--", label="published 1,242 km2")
        ax.set_title(f"{TITLES[a]}: open water per date (common footprint)", fontsize=9)
        ax.set_ylabel("km2"); ax.grid(alpha=0.3)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
        ax.legend(fontsize=7)
    _save(fig, "area_curves.png")


# ------------------------------------------------------------------ main
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--evals", type=int, default=400)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--boot", type=int, default=500)
    ap.add_argument("--cpu", action="store_true")
    args = ap.parse_args(argv)
    if args.quick:
        args.evals, args.boot = min(args.evals, 60), 100
    t0 = time.time()
    torch.manual_seed(SEED)
    OUT.mkdir(parents=True, exist_ok=True)
    dev = torch.device("cpu") if args.cpu else device()
    ctx = Ctx(AREAS, dev)
    print(f"loaded in {time.time() - t0:.0f}s; terrain mask available: {ctx.has_terrain}")

    sizes = {}
    for a in AREAS:
        km2 = ctx.B[a]["km2"]
        sizes[a] = {r: {"perm_px": R.n_perm, "never_px": R.n_never, "amb_px": R.n_amb,
                        "perm_km2": round(R.n_perm * km2, 1), "never_km2": round(R.n_never * km2, 1),
                        "amb_km2": round(R.n_amb * km2, 1), "blocks": int(R.blocks.numel())}
                    for r, R in ctx.reg[a].items() if r != "_masks"}
        sizes[a]["domain_km2"] = round(float(ctx.lab[a]["domain"].sum()) * km2, 1)
        print(a, {r: (v["perm_px"], v["never_px"], v["amb_px"]) for r, v in sizes[a].items() if r != "domain_km2"})

    TUNE = [("tonle-sap", "tune"), ("sevan", "tune")]
    VAL = [("tonle-sap", "val"), ("sevan", "val")]
    TEST = [("tonle-sap", "test"), ("sevan", "test"), ("ararat", "test")]

    # ---- main search on TUNE
    t1 = time.time()
    hist = search(ctx, TUNE, args.evals, SEED)
    print(f"search: {len(hist)} evals in {time.time() - t1:.0f}s")
    hist.sort(key=lambda h: -h["J"])
    # ---- validation picks among the top 20 (ops-compatible = hv_w 0, so the lead can apply it to ops.KNOBS)
    def pick(cands):
        best = None
        for h in cands[:20]:
            Jv, mv = ctx.score(h["s"], VAL)
            h["J_val"], h["m_val"] = Jv, mv
            if best is None or Jv > best["J_val"]:
                best = h
        return best
    compat = [h for h in hist if h["s"]["hv_w"] == 0 and not h["s"]["terrain"]]  # applicable to ops today
    best = pick(compat)
    best_ext = pick(hist)
    J_old_t, m_old_t = ctx.score(OLD, TUNE)
    J_old_v, m_old_v = ctx.score(OLD, VAL)
    new = best["s"]
    print("chosen:", new, f"J tune {best['J']:.4f} val {best['J_val']:.4f}; old tune {J_old_t:.4f} val {J_old_v:.4f}")
    print("best extended (terrain / HV weight allowed):", best_ext["s"], f"val {best_ext['J_val']:.4f}")

    # ---- pure balanced-accuracy optimum (sensitivity of the choice to LAMBDA)
    def ba_only(ms):
        return float(np.mean([m["balanced_acc"] for m in ms.values() if np.isfinite(m["balanced_acc"])]))
    ba_best = max(compat, key=lambda h: ba_only(h["m"]))

    # ---- leave-one-area-out (tuned on one area's TUNE strip only, tested on the whole other area)
    loao = {}
    for src, dst in (("tonle-sap", "sevan"), ("sevan", "tonle-sap")):
        h1 = search(ctx, [(src, "tune")], max(args.evals // 2, 30), SEED + 1)
        h1 = [h for h in h1 if h["s"]["hv_w"] == 0 and not h["s"]["terrain"]]
        b1 = max(h1, key=lambda h: h["J"])
        _, m_dst = ctx.score(b1["s"], [(dst, "all")])
        _, m_dst_new = ctx.score(new, [(dst, "all")])
        _, m_dst_old = ctx.score(OLD, [(dst, "all")])
        loao[f"{src}->{dst}"] = {"knobs": b1["s"], "J_on_source_tune": round(b1["J"], 5),
                                 "on_target_all": rnd(m_dst[f"{dst}/all"]),
                                 "joint_choice_on_target_all": rnd(m_dst_new[f"{dst}/all"]),
                                 "old_on_target_all": rnd(m_dst_old[f"{dst}/all"])}
        print("LOAO", src, "->", dst, b1["s"]["water_hh_strong"], b1["s"]["water_hh_weak"], rnd(m_dst[f"{dst}/all"], 3))

    # ---- landscape over the two HH thresholds (other knobs at chosen values), TUNE only
    sg = np.round(np.arange(0.02, 0.2201, 0.01), 3)
    wg = np.round(np.arange(0.02, 0.4201, 0.02), 3)
    if args.quick:
        sg, wg = sg[::3], wg[::3]
    land = {}
    for s_ in sg:
        for w_ in wg:
            if w_ >= s_:
                land[(float(s_), float(w_))] = ctx.score({**new, "water_hh_strong": float(s_),
                                                          "water_hh_weak": float(w_)}, TUNE)[0]

    # ---- TEST, touched once
    gen = torch.Generator().manual_seed(SEED)
    test, base_thr, water_by = {}, {}, {}
    for a in AREAS:
        bl, thr = baselines(ctx, a)
        base_thr[a] = thr
        methods = {"new": ctx.water(a, new), **bl}
        if best_ext["s"] != new:
            methods["best_extended"] = ctx.water(a, best_ext["s"])
        water_by[a] = methods
        test[a] = {}
        for name, w in methods.items():
            R = ctx.reg[a]["test"]
            m = rnd(R.metrics(w))
            m["ci95"] = R.bootstrap(w, args.boot, gen)
            test[a][name] = m
        print("TEST", a, {k: (round(v["recall_perm"], 4) if np.isfinite(v["recall_perm"]) else None,
                              round(v["fpr_never"], 5), round(v["rank_rho"], 3) if np.isfinite(v["rank_rho"]) else None)
                          for k, v in test[a].items()})

    # ---- operating curves on TEST (descriptive only)
    curves = {}
    scales = [0.25, 0.35, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.15, 1.3, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0]
    for a in AREAS:
        curves[a] = {}
        for fam, s0 in (("new", new), ("old", OLD)):
            pts = []
            for sc in scales:
                s = {**s0, "water_hh_strong": round(s0["water_hh_strong"] * sc, 4),
                     "water_hh_weak": round(min(s0["water_hh_weak"] * sc, 1.0), 4)}
                m = ctx.reg[a]["test"].metrics(ctx.water(a, s))
                pts.append({"scale": sc, "strong": s["water_hh_strong"], "weak": s["water_hh_weak"],
                            "rec": m["recall_perm"], "fpr": m["fpr_never"], "fp_km2": m["fp_km2_per_date"]})
            curves[a][fam] = pts

    # ---- rank-consistency curves (TEST ambiguous zone)
    rank = {}
    edges = np.arange(0, 101, 10)
    for a in ("tonle-sap", "sevan"):
        R = ctx.reg[a]["test"]
        rank[a] = {}
        for name in ("new", "old_knobs", "otsu2_mean", "otsu_hh"):
            _, _, ca = R.counts(water_by[a][name])
            occ = R.occ_amb.numpy(); ca = ca.numpy()
            mids, means, ns = [], [], []
            for lo, hi in zip(edges[:-1], edges[1:]):
                sel = (occ >= lo) & ((occ < hi) if hi < 100 else (occ <= hi))
                if sel.sum() >= 20:
                    mids.append(float((lo + hi) / 2)); means.append(float(ca[sel].mean())); ns.append(int(sel.sum()))
            mono = bool(np.all(np.diff(means) >= -1e-9))
            rank[a][name] = {"rho": test[a][name]["rank_rho"], "bin_mid": mids, "mean_dates": [round(m, 3) for m in means],
                             "n": ns, "monotonic": mono}

    # ---- headline areas: old vs new, band from the top 5 % of compatible settings
    top = compat[:max(1, int(round(0.05 * len(compat))))]
    area_res = {}
    for a in AREAS:
        b = ctx.B[a]
        series = {k: km2_series(ctx, a, w) for k, w in water_by[a].items()}
        band = np.array([km2_series(ctx, a, ctx.water(a, h["s"])) for h in top])
        R = {**series, "top5pct_min": band.min(0).tolist(), "top5pct_max": band.max(0).tolist(),
             "top5pct_n": len(top)}
        extm = b["ref"]["ext_frac"] >= 0.5
        dom = ctx.lab[a]["domain"]
        ext_px = float((extm & dom).sum())
        for k in ("new", "old_knobs", "otsu2_mean"):
            w = water_by[a][k]
            late = w[-3:].any(0) & dom
            R[f"{k}_late_share_inside_max_extent"] = round(float((late & extm).sum()) / max(float(late.sum()), 1), 4)
            R[f"{k}_last_date_recall_of_max_extent"] = round(float((w[-1] & extm & dom).sum()) / max(ext_px, 1), 4)
            R[f"{k}_any_date_km2_outside_max_extent"] = round(float((w.any(0) & dom & ~extm).sum()) * b["km2"], 1)
        area_res[a] = R
        print("AREAS", a, "old", series["old_knobs"], "new", series["new"])

    # ---- check: the fast path equals ops.run_pipeline (with the candidate's own normalisation exclusion)
    agree = {}
    for a in AREAS:
        st = load_preview_stack(a)
        for name, s in (("old_knobs", OLD), ("new", new)):
            kn = dict(ops.KNOBS["preview"]); kn.update({k: s[k] for k in OLD if k in kn})
            w_ops = ops.run_pipeline(st.x, st.valid, "preview", kn, dev=dev)["water"].cpu()
            if s.get("terrain") and ctx.B[a]["terrain"] is not None:
                w_ops = w_ops & ctx.B[a]["terrain"][None]  # ops has no terrain step; applied after
            w_fast = water_by[a][name]
            agree.setdefault(a, {})[name] = {
                "pixel_dates_differ": int((w_ops ^ w_fast).sum()),
                "share_of_water_pixel_dates": round(float((w_ops ^ w_fast).sum()) / max(float(w_ops.sum()), 1), 6)}
    print("fast vs ops agreement:", agree)

    # ---- outputs
    def pack(h, keys=("J",)):
        return {"knobs": h["s"], **{k: round(h[k], 5) for k in keys if k in h},
                "metrics_tune": {k: rnd(v) for k, v in h["m"].items()},
                **({"metrics_val": {k: rnd(v) for k, v in h["m_val"].items()}} if "m_val" in h else {})}
    # ---- adoption gate (declared before TEST was read; no selection among candidates happens here):
    # adopt the tuned knobs only if J on TEST beats the old knobs AND balanced accuracy (Ararat: specificity)
    # is not lower in any TEST area
    def jt(name):
        return objective([test[a][name] for a in AREAS])
    def ba_or_spec(m):
        return m["balanced_acc"] if np.isfinite(m["balanced_acc"]) else m["spec_never"]
    worse = [a for a in AREAS if ba_or_spec(test[a]["new"]) < ba_or_spec(test[a]["old_knobs"])]
    gate = {"J_test_new": round(jt("new"), 5), "J_test_old": round(jt("old_knobs"), 5),
            "areas_where_new_is_worse": worse}
    if "best_extended" in test["ararat"]:
        gate["J_test_best_extended"] = round(jt("best_extended"), 5)
        gate["areas_where_extended_is_worse"] = [a for a in AREAS if ba_or_spec(test[a]["best_extended"])
                                                 < ba_or_spec(test[a]["old_knobs"])]
    gate["adopt"] = bool(gate["J_test_new"] > gate["J_test_old"] and not worse)
    print("adoption gate:", gate)
    res = {
        "adoption_gate": gate,
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "seed": SEED, "evals": len(hist), "bootstrap": {"resamples": args.boot, "block_px": BLOCK},
        "objective": f"J = mean over areas of balanced accuracy 0.5*(recall_perm + specificity_never), pixel-dates, "
                     f"+ {LAMBDA} * mean Spearman(occurrence, n water dates) on ambiguous pixels",
        "label_rule": LABEL_RULE, "split": {"strips_col_fraction": SPLIT, "buffer_px": BUF,
                                            "tune": TUNE, "val": VAL, "test": TEST},
        "label_sizes": sizes, "terrain_available": ctx.has_terrain,
        "old": {"knobs": OLD, "J_tune": round(J_old_t, 5), "J_val": round(J_old_v, 5),
                "metrics_tune": {k: rnd(v) for k, v in m_old_t.items()},
                "metrics_val": {k: rnd(v) for k, v in m_old_v.items()}},
        "chosen": pack(best, ("J", "J_val")), "best_extended_incl_terrain_and_hv_weight": pack(best_ext, ("J", "J_val")),
        "best_by_balanced_accuracy_only": {"knobs": ba_best["s"], "ba_tune": round(ba_only(ba_best["m"]), 5)},
        "top10_tune": [{"knobs": h["s"], "J": round(h["J"], 5)} for h in hist[:10]],
        "top5pct_knob_range": {k: [min(h["s"][k] for h in top), max(h["s"][k] for h in top)]
                               for k in ("water_hh_strong", "water_hh_weak", "water_hv_max", "box", "min_blob_px")},
        "baseline_thresholds": base_thr, "test": test, "leave_one_area_out": loao, "rank_consistency_test": rank,
        "operating_curves_test": curves, "areas_km2": area_res, "dates": {a: ctx.B[a]["dates"] for a in AREAS},
        "published_sevan_km2": PUBLISHED_SEVAN_KM2, "fast_vs_ops_agreement": agree,
        "landscape": [{"strong": k[0], "weak": k[1], "J": round(v, 5)} for k, v in land.items()],
    }
    (OUT / "results.json").write_text(json.dumps(res, indent=1, default=float))
    torch.save({"hist": [{"s": h["s"], "J": h["J"]} for h in hist]}, CACHE / "search_history.pt")
    comments = {
        "water_hh_strong": "seed threshold on filtered HH (display brightness); tuned on TUNE strips, chosen on VAL",
        "water_hh_weak": "grow threshold (hysteresis); constrained >= strong",
        "water_hv_max": "HV must be below this",
        "box": "speckle-filter window (px)", "quegan": "multi-temporal Quegan filter on/off",
        "min_blob_px": "drop water components smaller than this",
    }
    bk = {"_note": "Recommended KNOBS['preview'] overrides from scripts/detect/tune.py (see docs/eda/tuning.md). "
                   "Valid ONLY for the uncalibrated ASF previews; re-run the procedure on calibrated gamma0.",
          "_old_values": {k: OLD[k] for k in comments}}
    rec = new if gate["adopt"] else OLD
    bk["_decision"] = ("ADOPT the tuned values" if gate["adopt"] else
                       "KEEP the old values: the tuned candidate failed the TEST adoption gate "
                       f"(J_test new {gate['J_test_new']} vs old {gate['J_test_old']}; worse in {worse})")
    bk["_tuned_candidate"] = {k: new[k] for k in comments}
    for k, c in comments.items():
        bk[k] = {"value": rec[k], "old": OLD[k], "tuned_candidate": new[k], "comment": c}
    (OUT / "best_knobs.json").write_text(json.dumps(bk, indent=1))
    print("figures")
    fig_split(ctx)
    fig_landscape({"strong": sg, "weak": wg, "J": land}, (OLD["water_hh_strong"], OLD["water_hh_weak"]),
                  (new["water_hh_strong"], new["water_hh_weak"]))
    fig_curves(curves)
    fig_rank(rank)
    fig_areas(ctx, area_res)
    print(f"done in {time.time() - t0:.0f}s")
    return res


if __name__ == "__main__":
    main()
