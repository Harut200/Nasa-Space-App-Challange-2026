"""Supervised water segmentation trained on INDEPENDENT labels (JRC Global Surface Water), tested on unseen PLACES.

Every earlier network in this repo learned from our own math detector's output and so copied it. Here the labels
come from 38 years of Landsat (JRC GSW v1.4, Pekel et al. 2016; a different sensor and team), and only for pixels
whose state is unambiguous on ANY 2026 date:
  water  = >= 90 % of the 180 m pixel has occurrence >= 90 % and >= 50 % is water in all 12 months of 2021,
           shrunk by a 2 px shoreline band              (the tune.py rule, imported)
  land   = no JRC water ever within 3 px                 (the tune.py rule, imported)
  ignore = everything else (shorelines, seasonal floodplain whose 2026 state is unknown)
  soft   = stable shorelines (every 30 m sub-pixel is either >= 90 % occurrence or never water): the target of a
           second head that regresses the sub-pixel water FRACTION.
Labels are the same on every date, so a windy date on a lake is still labelled water and a dark slope is land.

Reported model (variant "jrc"): HH channels only (the preview HV noise floor differs between scenes and across the
swath), terrain channels, and a weak land target on ignored pixels that JRC saw as water < 10 % of the time.
Every training/scoring step honours the per-pass QC verdicts (data/processed/<area>/qc.json).

    .venv/bin/python -m scripts.detect.supervised build                          # caches + JRC reference per area
    .venv/bin/python -m scripts.detect.supervised train --holdout sevan,tonle-sap --products   # main table + products
    .venv/bin/python -m scripts.detect.supervised loao --redo --only sevan,ararat # leave-one-area-out
    .venv/bin/python -m scripts.detect.supervised curve                          # learning curve by number of places
    .venv/bin/python -m scripts.detect.supervised ablate --variants jrc_noseason,jrc_noneigh
    .venv/bin/python -m scripts.detect.supervised predict --area ararat          # model that never saw the area
    .venv/bin/python -m scripts.detect.supervised figures

Write-up: docs/eda/supervised.md. Results: docs/eda/supervised/results.json.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import math
import time

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from scripts.detect import ops
from scripts.detect.stack import ROOT, device

AREAS_ALL = ["tonle-sap", "sevan", "ararat", "bangladesh-haor", "inner-niger", "lake-chad", "mekong-delta",
             "pantanal", "sindh-indus", "sudd"]
MAIN_HOLDOUT = ["sevan", "tonle-sap"]
WINDY = {"sevan": ["2026-08-31", "2026-09-12"]}
SEVAN_KM2 = 1242.0
FINAL = "jrc"            # the reported model (main run: trained without Sevan and Tonle Sap)
LOAO_VARIANT = "jrc"
MODELS = ROOT / "data/processed/models"
OUT = ROOT / "docs/eda/supervised"
SEED = 20261115
TMAX = 10
PATCH = 128
VAL_FRAC = 0.15      # east strip of every TRAINING area = validation / early stopping
BUF = 10             # px between train and validation strips
EPS = 0.02           # log(z + EPS) of median-normalised brightness

BASE = dict(width=32, depth=3, season=True, neigh=True, terrain=False, paste=True, wind=True, floor=True,
            iters=1000, batch=16, lr=2e-3, wd=1e-4, w_frac=1.0)
FAST = {**BASE, "iters": 500}
VARIANTS = {
    "base": BASE,                                            # ~2.1 M parameters, 1000 iterations
    "small": {**BASE, "width": 16},                          # ~0.5 M
    "large": {**BASE, "width": 48},                          # ~4.6 M
    "fast": FAST,                                            # LOAO, learning curve, reference for the ablations
    "no_season": {**FAST, "season": False},
    "no_neigh": {**FAST, "neigh": False},
    "terrain": {**FAST, "terrain": True},
    "no_paste": {**FAST, "paste": False},
    "no_wind": {**FAST, "wind": False},
    "no_synth": {**FAST, "paste": False, "wind": False},
    "no_floor": {**FAST, "floor": False},
    "tiny": {**BASE, "width": 8, "depth": 2, "iters": 150, "batch": 8, "lr": 3e-3},   # unit tests only
    # fixes for the two failure modes found on the held-out places (moderately dark flat land called water)
    "hvfield": {**FAST, "hvfield": True},                     # smooth random HV gain field: HV is unreliable
    "weakneg": {**FAST, "weakneg": True},                     # weak land target where JRC occurrence < 5 %
    "fix": {**FAST, "hvfield": True, "weakneg": True},
    "no_chad_pantanal": {**FAST, "exclude_train": ["lake-chad", "pantanal"]},
    "small_fix": {**FAST, "hvfield": True, "weakneg": True, "width": 16},
    "large_fix": {**FAST, "hvfield": True, "weakneg": True, "width": 48},
    "base_fix": {**BASE, "hvfield": True, "weakneg": True},
    "hh_only": {**FAST, "hh_only": True, "floor": False},
    "hh_weakneg": {**FAST, "hh_only": True, "floor": False, "weakneg": True},
    "hh_weakneg_s2": {**FAST, "hh_only": True, "floor": False, "weakneg": True, "seed": SEED + 1},
    "hh_weakneg_terrain": {**FAST, "hh_only": True, "floor": False, "weakneg": True, "terrain": True},
    "hh_weakneg_noseason": {**FAST, "hh_only": True, "floor": False, "weakneg": True, "season": False},
    "hh_weakneg_noneigh": {**FAST, "hh_only": True, "floor": False, "weakneg": True, "neigh": False},
    "hh_weakneg_small": {**FAST, "hh_only": True, "floor": False, "weakneg": True, "width": 16},
    "hh_weakneg_large": {**FAST, "hh_only": True, "floor": False, "weakneg": True, "width": 48},
    "final": {**BASE, "hh_only": True, "floor": False, "weakneg": True, "terrain": True},   # reported model, 1000 it
    "final_strongweak": {**FAST, "hh_only": True, "floor": False, "weakneg": True, "terrain": True, "weak_w": 1.0,
                         "weak_occ": 10.0},
    # THE REPORTED MODEL: HH only, terrain channels, weak land target (JRC occurrence < 10 %, weight 1), 500 it
    "jrc": {**FAST, "hh_only": True, "floor": False, "weakneg": True, "terrain": True, "weak_w": 1.0, "weak_occ": 10.0},
    "jrc_s2": {**FAST, "hh_only": True, "floor": False, "weakneg": True, "terrain": True, "weak_w": 1.0,
               "weak_occ": 10.0, "seed": SEED + 1},
    "jrc_noseason": {**FAST, "hh_only": True, "floor": False, "weakneg": True, "terrain": True, "weak_w": 1.0,
                     "weak_occ": 10.0, "season": False},
    "jrc_noneigh": {**FAST, "hh_only": True, "floor": False, "weakneg": True, "terrain": True, "weak_w": 1.0,
                    "weak_occ": 10.0, "neigh": False},
    "jrc_small": {**FAST, "hh_only": True, "floor": False, "weakneg": True, "terrain": True, "weak_w": 1.0,
                  "weak_occ": 10.0, "width": 16},
    "jrc_large": {**FAST, "hh_only": True, "floor": False, "weakneg": True, "terrain": True, "weak_w": 1.0,
                  "weak_occ": 10.0, "width": 48},
    "jrc_hhhv": {**FAST, "floor": True, "weakneg": True, "terrain": True, "weak_w": 1.0, "weak_occ": 10.0},
    "final_fast": {**FAST, "hh_only": True, "floor": False, "weakneg": True, "terrain": True},  # LOAO + curve, 500 it
}
WEAK_OCC = 5.0       # % mean JRC occurrence under which an ignored pixel gets the weak land target
WEAK_W = 0.2         # weight of that target relative to the main loss


# ====================================================================== labels + normalisation
def build_labels(ref, footprint):
    """Static labels [H, W] int8 (1 water, 0 land, -1 ignore) + soft fraction target (NaN = none)."""
    from scripts.detect.tune import LABEL_RULE
    from scripts.detect.tune import build_labels as tune_labels
    L = tune_labels(ref, footprint, **LABEL_RULE)
    lab = torch.full(footprint.shape, -1, dtype=torch.int8)
    lab[L["never"]] = 0
    lab[L["perm"]] = 1
    # stable shoreline: no sub-pixel has intermediate occurrence (all are >= 90 % or never water)
    stable = ((ref["occ_any_frac"] - ref["occ_ge90_frac"]) <= 0.05) & ((ref["ext_frac"] - ref["occ_ge90_frac"]) <= 0.05)
    soft = L["domain"] & stable & ~L["perm"] & ~L["never"] & (ref["seas12_frac"] >= ref["occ_ge90_frac"] - 0.1)
    frac = torch.full(footprint.shape, float("nan"))
    frac[soft] = ref["occ_ge90_frac"][soft].clamp(0, 1)
    return lab, frac, L


def normalise_scene(x, footprint):
    """Median-normalise each channel over the footprint and all dates: z = x / median. Invariant to a global gain
    (the preview black point is pinned at the display floor, so a gain is the main scene-to-scene difference)."""
    z = torch.zeros_like(x, dtype=torch.float32)
    meds = []
    for c in range(x.shape[1]):
        v = x[:, c][footprint[None].expand(x.shape[0], -1, -1)].float()
        v = v[:: max(1, v.numel() // 2_000_000)]
        m = float(v.median().clamp_min(1e-4))
        z[:, c] = x[:, c].float() / m
        meds.append(m)
    return z * footprint[None, None], meds


def register_reference(z, footprint, ref, max_shift=4):
    """Integer (dy, dx) that best aligns the JRC occurrence with radar darkness (corner georeferencing of the
    previews is good to a few pixels). Darkness = -mean log HH over dates. Pure image registration."""
    dark = -torch.log(z[:, 0].mean(0) + EPS)
    occ = ref["occ_mean"]
    best, best_c = (0, 0), -2.0
    m0 = footprint & (ref["valid_frac"] > 0.99)
    for dy in range(-max_shift, max_shift + 1):
        for dx in range(-max_shift, max_shift + 1):
            o = torch.roll(occ, (dy, dx), (0, 1))
            m = m0 & torch.roll(m0, (dy, dx), (0, 1))
            a, b = dark[m], o[m]
            if b.std() < 1e-6:
                continue
            c = float(((a - a.mean()) * (b - b.mean())).mean() / (a.std() * b.std() + 1e-9))
            if c > best_c:
                best, best_c = (dy, dx), c
    c0 = None
    m = m0
    a, b = dark[m], occ[m]
    if b.std() > 1e-6:
        c0 = float(((a - a.mean()) * (b - b.mean())).mean() / (a.std() * b.std() + 1e-9))
    return best, best_c, c0


def shift_ref(ref, dy, dx):
    if dy == 0 and dx == 0:
        return ref
    out = {}
    for k, v in ref.items():
        r = torch.roll(v, (dy, dx), (0, 1))
        if k == "valid_frac":  # wrapped rows/cols are not valid
            if dy > 0: r[:dy] = 0
            if dy < 0: r[dy:] = 0
            if dx > 0: r[:, :dx] = 0
            if dx < 0: r[:, dx:] = 0
        out[k] = r
    return out


# ====================================================================== dataset (one area at a time, CPU, float16)
def cache_path(area):
    return MODELS / f"supervised_cache_{area}.pt"


def build_area(area, dev=None, force=False):
    """Load the preview stack, the JRC reference (cached), terrain, and both math-detector baselines for one area."""
    p = cache_path(area)
    if p.exists() and not force:
        return torch.load(p, weights_only=False)
    from scripts.detect.stack import load_preview_stack
    from scripts.detect.terrain import cached_water_possible
    from scripts.detect.validate import reference_on_grid
    dev = dev or device()
    t0 = time.time()
    st = load_preview_stack(area)
    T, _, H, W = st.x.shape
    kn = ops.KNOBS["preview"]
    x, valid = ops.clean_input(st.x, st.valid)
    valid = valid & ~ops.nodata_margin(x, valid, kn["nodata_floor"])
    valid = valid & valid.all(0, keepdim=True)
    fp = ops.erode(valid, 2 * kn["edge_erode_px"] + 1)[0]
    z, meds = normalise_scene(x, fp)
    ref, _ = reference_on_grid(area, st.bounds, H, W)
    (dy, dx), c_best, c0 = register_reference(z, fp, ref)
    ref = shift_ref(ref, dy, dx)
    lab, frac, L = build_labels(ref, fp)
    # terrain (never downloads)
    terr = None
    tp = ROOT / f"data/processed/{area}/terrain.pt"
    if tp.exists():
        t = torch.load(tp, weights_only=False, map_location="cpu")
        if tuple(t["slope_deg"].shape) == (H, W):
            terr = {"slope": t["slope_deg"].half(), "relh": t["rel_height_m"].half(), "water_ok": t["water_possible"].bool()}
    wok = cached_water_possible(area, (H, W))
    # math detector baselines: with and without terrain mask (same run, mask applied as ops does)
    out = ops.run_pipeline(st.x, st.valid, "preview", dev=dev)
    w_nt = out["water"].cpu()
    w_t = w_nt & wok[None] if wok is not None else None
    math_valid = out["valid"][0].cpu()
    del out
    if dev.type == "mps":
        torch.mps.empty_cache()
    d = {"area": area, "dates": st.dates, "bounds": st.bounds, "km2": st.km2_per_px, "z": z.half(), "fp": fp,
         "medians": meds, "lab": lab, "frac": frac.half(), "domain": L["domain"], "amb": L["amb"],
         "ref": {k: v.half() for k, v in ref.items()}, "ref_shift": (dy, dx), "ref_corr": (c0, c_best),
         "terrain": terr, "math_water": w_nt, "math_water_terrain": w_t, "math_valid": math_valid}
    MODELS.mkdir(parents=True, exist_ok=True)
    torch.save(d, p)
    print(f"  {area}: {T}x{H}x{W}, water {int((lab == 1).sum())} px, land {int((lab == 0).sum())} px, "
          f"soft {int(torch.isfinite(frac).sum())} px, ref shift {(dy, dx)} (corr {c0} -> {c_best:.3f}), "
          f"terrain {terr is not None}, {time.time() - t0:.0f}s")
    return d


def qc_exclusions(area):
    """(excluded dates, {date: [channels]}) from data/processed/<area>/qc.json (per-pass quality verdicts)."""
    p = ROOT / f"data/processed/{area}/qc.json"
    if not p.exists():
        return [], {}
    s = json.loads(p.read_text()).get("summary", {})
    return list(s.get("exclude_dates", [])), dict(s.get("exclude_channels", {}))


def apply_qc(d, mode="drop"):
    """Honour the per-pass QC verdicts on an area cache (in place, returns d).
    mode 'drop'  : remove every date with a failed date OR a failed channel (training and scoring; every method
                   is then scored on the same remaining dates);
    mode 'impute': keep all dates but replace a failed channel by the per-pixel median of that channel over the
                   good dates (products only, so the app still gets one map per date; flagged in the product)."""
    ex_d, ex_c = qc_exclusions(d["area"])
    bad = [i for i, t in enumerate(d["dates"]) if t in ex_d or t in ex_c]
    d["qc_bad_dates"] = [d["dates"][i] for i in bad]
    d["keep_idx"] = [i for i in range(len(d["dates"])) if i not in bad]
    d["dates_all"] = list(d["dates"])
    if not bad:
        return d
    if mode == "drop":
        k = torch.tensor(d["keep_idx"])
        d["z"] = d["z"][k]
        d["dates"] = [d["dates"][i] for i in d["keep_idx"]]
        for key in ("math_water", "math_water_terrain"):
            if d.get(key) is not None:
                d[key] = d[key][k]
    else:
        good = torch.tensor(d["keep_idx"])
        z = d["z"].clone()
        for i in bad:
            t = d["dates"][i]
            chans = [0, 1] if t in ex_d else [{"HH": 0, "HV": 1}[c] for c in ex_c[t]]
            for c in chans:
                z[i, c] = z[good, c].float().median(0).values.half()
        d["z"] = z
        d["keep_idx"] = list(range(len(d["dates"])))
    return d


def load_area(area, qc="drop"):
    d = torch.load(cache_path(area), weights_only=False)
    return apply_qc(d, qc) if qc else d


def available_areas():
    out = []
    for a in AREAS_ALL:
        if cache_path(a).exists():
            out.append(a)
    return out


def build_dataset(areas):
    """Build (or load) the per-area caches; areas that fail to load are skipped and reported."""
    ok, failed = [], {}
    for a in areas:
        try:
            build_area(a)
            ok.append(a)
        except Exception as e:  # noqa: BLE001  (a new area may be half-ingested)
            failed[a] = f"{type(e).__name__}: {e}"
            print(f"  {a}: FAILED {failed[a]}")
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
    return ok, failed


def train_labels(d):
    """Training labels: the same rule with a 1 px shoreline band and land >= 2 px from any JRC water (the reference
    was registered to the radar, so the 2 px / 3 px guards of the test labels can be relaxed for training; more
    river water). Test anchors always use the strict tune.py rule (d["lab"])."""
    from scripts.detect.tune import LABEL_RULE
    from scripts.detect.tune import build_labels as tune_labels
    ref = {k: v.float() for k, v in d["ref"].items()}
    L = tune_labels(ref, d["fp"], **{**LABEL_RULE, "shore_px": 1, "never_dist_px": 2})
    lab = torch.full(d["fp"].shape, -1, dtype=torch.int8)
    lab[L["never"]] = 0
    lab[L["perm"]] = 1
    return lab


# ====================================================================== features
def n_channels(v):
    if v.get("hh_only"):
        return 1 + (4 if v["season"] else 0) + (2 if v["neigh"] else 0) + (2 if v["terrain"] else 0)
    return 3 + (8 if v["season"] else 0) + (4 if v["neigh"] else 0) + (2 if v["terrain"] else 0)


def features(z, dmask, t, v, terr=None, fp=None):
    """Per-date input channels from the median-normalised stack.

    z [B, T, 2, h, w] (padded to T), dmask [B, T] bool (real dates), t [B] long target date, terr [B, 2, h, w].
    Channels (log domain, like dB): HH_t, HV_t, HH_t - HV_t; season min/mean/max/std of HH and HV over the real
    dates; HH/HV of the previous and next date; optionally slope and relative height.
    The season channels are computed from radar alone; they carry no label information.
    """
    B, T = dmask.shape
    if fp is not None:  # outside the radar footprint = scene median (1), so no-data never looks like calm water
        z = torch.where(fp[:, None, None], z, torch.ones_like(z))
    if v.get("hh_only"):   # HV dropped entirely (its noise floor is scene- and swath-dependent in the previews)
        z = z[:, :, :1]
    lz = torch.log(z.clamp_min(0) + EPS)
    bi = torch.arange(B, device=z.device)
    cur = lz[bi, t]                                                   # [B, 2, h, w] (or [B, 1, h, w])
    ch = [cur] if v.get("hh_only") else [cur, (cur[:, 0] - cur[:, 1])[:, None]]
    if v["season"]:
        m = dmask[:, :, None, None, None].to(lz.dtype)
        n = m.sum(1).clamp_min(1)
        mean = (lz * m).sum(1) / n
        std = (((lz - mean[:, None]) ** 2) * m).sum(1).div(n).sqrt()
        big = torch.where(m.bool(), lz, torch.full_like(lz, 1e4)).amin(1)
        small = torch.where(m.bool(), lz, torch.full_like(lz, -1e4)).amax(1)
        ch += [big, mean, small, std]
    if v["neigh"]:
        nreal = dmask.sum(1)
        tp = (t - 1).clamp_min(0)
        tn = torch.minimum(t + 1, nreal - 1)
        ch += [lz[bi, tp], lz[bi, tn]]
    if v["terrain"]:
        if terr is None:
            terr = torch.zeros(B, 2, *z.shape[-2:], device=z.device)
        ch += [terr]
    return torch.cat(ch, 1)


def terrain_channels(d):
    H, W = d["fp"].shape
    if d["terrain"] is None:
        return torch.zeros(2, H, W)
    s = d["terrain"]["slope"].float() / 20.0
    r = (d["terrain"]["relh"].float() / 100.0).clamp(-3, 5)
    return torch.stack([s, r])


# ====================================================================== model
class ResBlock(nn.Module):
    def __init__(self, cin, cout):
        super().__init__()
        self.c1 = nn.Conv2d(cin, cout, 3, padding=1, bias=False)
        self.b1 = nn.BatchNorm2d(cout)
        self.c2 = nn.Conv2d(cout, cout, 3, padding=1, bias=False)
        self.b2 = nn.BatchNorm2d(cout)
        self.skip = nn.Conv2d(cin, cout, 1, bias=False) if cin != cout else nn.Identity()

    def forward(self, x):
        y = F.relu(self.b1(self.c1(x)))
        return F.relu(self.b2(self.c2(y)) + self.skip(x))


class ResUNet(nn.Module):
    """Residual U-Net with two heads: water logit and sub-pixel water-fraction logit."""

    def __init__(self, n_in, width=32, depth=3):
        super().__init__()
        ws = [width * 2 ** i for i in range(depth + 1)]
        self.enc = nn.ModuleList([ResBlock(n_in, ws[0])] + [ResBlock(ws[i], ws[i + 1]) for i in range(depth)])
        self.dec = nn.ModuleList([ResBlock(ws[i + 1] + ws[i], ws[i]) for i in reversed(range(depth))])
        self.head = nn.Conv2d(ws[0], 2, 1)
        self.depth = depth

    def forward(self, x):
        skips = []
        for i, blk in enumerate(self.enc):
            x = blk(x if i == 0 else F.max_pool2d(x, 2))
            skips.append(x)
        x = skips.pop()
        for blk in self.dec:
            s = skips.pop()
            x = F.interpolate(x, size=s.shape[-2:], mode="bilinear", align_corners=False)
            x = blk(torch.cat([x, s], 1))
        return self.head(x)


def n_params(m):
    return sum(p.numel() for p in m.parameters())


# ====================================================================== sampling + augmentation
class Scenes:
    """Training areas held on CPU (float16); random crops from each area's training strip."""

    def __init__(self, areas, gen, val=True, relaxed=True, data=None, weakneg=False, weak_occ=WEAK_OCC):
        self.D, self.areas, self.gen = {}, list(areas), gen
        for a in areas:
            d = data[a] if data is not None else load_area(a)
            H, W = d["fp"].shape
            c_val = int(round((1 - VAL_FRAC) * W)) if val else W
            c_tr = c_val - BUF if val else W
            lab = train_labels(d) if relaxed else d["lab"].clone()
            if weakneg:  # -2 = weak land: ignored pixel that JRC saw as water < WEAK_OCC % of the time (1984-2021)
                occ = d["ref"]["occ_mean"].float()
                lab[(lab == -1) & (occ < weak_occ) & (d["ref"]["valid_frac"].float() > 0.5)] = -2
            trainable = torch.zeros(H, W, dtype=torch.bool)
            trainable[:, :c_tr] = True
            T = d["z"].shape[0]
            z = torch.zeros(TMAX, 2, H, W, dtype=torch.float16)
            z[:T] = d["z"]
            z[T:] = d["z"][-1:]
            e = {"z": z, "T": T, "lab": lab, "frac": d["frac"], "fp": d["fp"], "terr": terrain_channels(d).half(),
                 "c_tr": c_tr, "c_val": c_val, "H": H, "W": W, "d": d}
            e["perm_idx"] = torch.nonzero((lab == 1) & trainable)
            e["lab_idx"] = torch.nonzero((lab >= 0) & trainable)
            self.D[a] = e
        self.paste_ok = [a for a in areas if self.D[a]["perm_idx"].shape[0] >= 400]

    def _crop_origin(self, e, center):
        H, c_tr = e["H"], e["c_tr"]
        y0 = int(np.clip(int(center[0]) - PATCH // 2, 0, max(H - PATCH, 0)))
        x0 = int(np.clip(int(center[1]) - PATCH // 2, 0, max(c_tr - PATCH, 0)))
        return y0, x0

    def _crop(self, e, y0, x0, train_only=True):
        sl = (slice(y0, y0 + PATCH), slice(x0, x0 + PATCH))
        z = e["z"][:, :, sl[0], sl[1]]
        lab = e["lab"][sl].clone()
        frac = e["frac"][sl].clone()
        if train_only:  # labels east of the training strip are never used
            cols = torch.arange(x0, x0 + z.shape[-1])
            lab[:, cols >= e["c_tr"]] = -1
            frac[:, cols >= e["c_tr"]] = float("nan")
        out = [z, lab, frac, e["fp"][sl], e["terr"][:, sl[0], sl[1]]]
        h, w = z.shape[-2:]
        if h < PATCH or w < PATCH:  # pad small scenes
            ph, pw = PATCH - h, PATCH - w
            out = [F.pad(out[0].float(), (0, pw, 0, ph)).half(), F.pad(out[1], (0, pw, 0, ph), value=-1),
                   F.pad(out[2].float(), (0, pw, 0, ph), value=float("nan")).half(),
                   F.pad(out[3], (0, pw, 0, ph)), F.pad(out[4].float(), (0, pw, 0, ph)).half()]
        return out

    def batch(self, B):
        g = self.gen
        Z, LAB, FR, FP, TR, TT, SRC, SRCP, HASP = [], [], [], [], [], [], [], [], []
        for _ in range(B):
            a = self.areas[int(torch.randint(len(self.areas), (1,), generator=g))]
            e = self.D[a]
            pool = e["perm_idx"] if (e["perm_idx"].shape[0] > 0 and torch.rand(1, generator=g) < 0.5) else e["lab_idx"]
            c = pool[int(torch.randint(pool.shape[0], (1,), generator=g))]
            z, lab, fr, fp, tr = self._crop(e, *self._crop_origin(e, c))
            Z.append(z); LAB.append(lab); FR.append(fr); FP.append(fp); TR.append(tr); TT.append(e["T"])
            # paste source: a crop of permanent water from the same area (same dates)
            if a in self.paste_ok and torch.rand(1, generator=g) < 0.5:
                pc = e["perm_idx"][int(torch.randint(e["perm_idx"].shape[0], (1,), generator=g))]
                sz, slab, _, _, _ = self._crop(e, *self._crop_origin(e, pc))
                SRC.append(sz); SRCP.append(slab == 1); HASP.append(True)
            elif a in self.paste_ok:
                # time series of randomly drawn permanent-water pixels of this scene: lets a pasted lake be
                # larger than any water body in the training strip (big lakes look uniform inside a 128 px crop)
                ii = e["perm_idx"][torch.randint(e["perm_idx"].shape[0], (PATCH * PATCH,), generator=g)]
                sz = e["z"][:, :, ii[:, 0], ii[:, 1]].reshape(TMAX, 2, PATCH, PATCH)
                SRC.append(sz); SRCP.append(torch.ones(PATCH, PATCH, dtype=torch.bool)); HASP.append(True)
            else:
                SRC.append(torch.zeros_like(z)); SRCP.append(torch.zeros_like(lab, dtype=torch.bool)); HASP.append(False)
        nT = torch.tensor(TT)
        dmask = torch.arange(TMAX)[None] < nT[:, None]
        return {"z": torch.stack(Z), "lab": torch.stack(LAB), "frac": torch.stack(FR), "fp": torch.stack(FP),
                "terr": torch.stack(TR), "dmask": dmask, "src": torch.stack(SRC), "srcp": torch.stack(SRCP),
                "hasp": torch.tensor(HASP)}


def smooth_field(B, h, w, dev, gen, scale=16):
    n = torch.rand(B, 1, max(2, h // scale), max(2, w // scale), generator=gen).to(dev)
    return F.interpolate(n, size=(h, w), mode="bicubic", align_corners=False)[:, 0]


def augment(b, v, dev, gen):
    """On the GPU: synthetic floods / recessions pasted from real permanent water of the same scene (labels follow),
    synthetic wind on permanent water (label stays water), per-channel gain / offset, speckle, flips, rot90.
    Returns (features, target date labels, fraction targets, footprint)."""
    z = b["z"].to(dev).float()
    lab = b["lab"].to(dev).long()
    frac = b["frac"].to(dev).float()
    fp = b["fp"].to(dev)
    dmask = b["dmask"].to(dev)
    B, T, _, h, w = z.shape
    nT = dmask.sum(1)
    t = (torch.rand(B, generator=gen) * nT.cpu()).long().clamp_max(nT.cpu() - 1).to(dev)
    lab_t = lab.clone()
    if v["paste"]:
        src = b["src"].to(dev).float()
        srcp = b["srcp"].to(dev)
        do = b["hasp"].to(dev) & (torch.rand(B, generator=gen).to(dev) < 0.5)
        f = smooth_field(B, h, w, dev, gen, scale=int(np.random.default_rng(int(torch.randint(1 << 30, (1,), generator=gen))).choice([8, 16, 32])))
        q = torch.rand(B, 1, 1, generator=gen).to(dev) * 0.85 + 0.05          # blob covers 5-90 % of the crop
        fs = f.reshape(B, -1).sort(1).values
        qi = ((1 - q[:, 0, 0]) * (fs.shape[1] - 1)).long()
        thr = fs.gather(1, qi[:, None])[:, :, None]
        blob = (f >= thr) & (lab == 0) & srcp & fp & do[:, None, None]
        # which dates are flooded: onset (>= k), recession (< k) or a short episode
        k1 = (torch.rand(B, generator=gen).to(dev) * nT).long()
        k2 = k1 + 1 + (torch.rand(B, generator=gen).to(dev) * 3).long()
        mode = torch.randint(3, (B,), generator=gen).to(dev)
        ti = torch.arange(T, device=dev)[None]
        flood = torch.where(mode[:, None] == 0, ti >= k1[:, None],
                            torch.where(mode[:, None] == 1, ti < k1[:, None].clamp_min(1),
                                        (ti >= k1[:, None]) & (ti < k2[:, None])))
        flood = flood & dmask
        cond = blob[:, None, None] & flood[:, :, None, None, None]
        z = torch.where(cond, src, z)
        on_t = flood[torch.arange(B, device=dev), t]
        lab_t = torch.where(blob & on_t[:, None, None], torch.ones_like(lab_t), lab_t)
        frac = torch.where(blob, torch.full_like(frac, float("nan")), frac)
    if v["wind"]:
        do = torch.rand(B, generator=gen).to(dev) < 0.3
        tw = torch.where(torch.rand(B, generator=gen).to(dev) < 0.5, t,
                         (torch.rand(B, generator=gen).to(dev) * nT).long().clamp_max(nT - 1))
        f = smooth_field(B, h, w, dev, gen, scale=32).clamp(0, 1) ** 2
        amp = torch.rand(B, generator=gen).to(dev) * 1.7 + 0.3
        bump = (amp[:, None, None] * f) * (lab == 1) * do[:, None, None]
        bi = torch.arange(B, device=dev)
        z[bi, tw, 0] = z[bi, tw, 0] + bump
        z[bi, tw, 1] = z[bi, tw, 1] + 0.3 * bump
    if v.get("hvfield"):
        # HV is the least reliable channel of the previews: its noise floor varies across the swath and between
        # scenes (Sevan's calm lake sits ABOVE the HV of the surrounding steppe). A smooth random HV gain field,
        # static over the season, x0.3..x3, on half the samples, so the network cannot read water from HV alone.
        do = (torch.rand(B, generator=gen).to(dev) < 0.5)[:, None, None]
        f = smooth_field(B, h, w, dev, gen, scale=int([16, 32, 64][int(torch.randint(3, (1,), generator=gen))]))
        amp = torch.rand(B, 1, 1, generator=gen).to(dev) * math.log(3.0)
        gain = torch.exp(torch.where(do, amp * (2 * f - 1), torch.zeros_like(f)))
        z = torch.cat([z[:, :, :1], z[:, :, 1:] * gain[:, None, None]], 2)
    # radiometric: per-sample-channel gain, per-date gain, small offset, speckle
    g = torch.exp((torch.rand(B, 1, 2, 1, 1, generator=gen).to(dev) - 0.5) * 2 * math.log(1.4))
    gd = torch.exp((torch.rand(B, T, 2, 1, 1, generator=gen).to(dev) - 0.5) * 2 * math.log(1.1))
    off = (torch.rand(B, 1, 2, 1, 1, generator=gen).to(dev) - 0.5) * 0.06
    z = z * g * gd + off
    # sensor noise floor: scenes differ in how far calm water sits above the display floor (Sevan HV water is at
    # ~0.6 x the scene median, Tonle Sap HV water at ~0.01), so add a random per-scene floor, mostly to HV
    nf = torch.rand(B, 1, 2, 1, 1, generator=gen).to(dev) * torch.tensor([0.3, 1.5], device=dev).view(1, 1, 2, 1, 1)
    nf = nf * (torch.rand(B, 1, 2, 1, 1, generator=gen).to(dev) < torch.tensor([0.3, 0.6], device=dev).view(1, 1, 2, 1, 1))
    nf = nf * float(v.get("floor", True))
    nf_noise = torch.exp(torch.randn(z.shape, device=dev) * 0.35 - 0.06)   # device RNG (seeded by manual_seed)
    z = z + nf * nf_noise
    # speckle: log-normal multiplicative noise with 5-30 looks (sd of log intensity ~ 1/sqrt(looks))
    looks = torch.rand(B, 1, 1, 1, 1, generator=gen).to(dev) * 25 + 5
    sp = (torch.rand(B, generator=gen).to(dev) < 0.5)[:, None, None, None, None]
    sd = 1.0 / looks.sqrt()
    noise = torch.exp(torch.randn(z.shape, device=dev) * sd - 0.5 * sd ** 2)
    z = torch.where(sp, z * noise, z).clamp_min(0)
    terr = b["terr"].to(dev).float()
    X = features(z, dmask, t, v, terr, fp)
    # geometric
    k = int(torch.randint(4, (1,), generator=gen))
    fl = bool(torch.rand(1, generator=gen) < 0.5)
    def geo(a, dims=(-2, -1)):
        a = torch.rot90(a, k, dims)
        return torch.flip(a, (-1,)) if fl else a
    return geo(X), geo(lab_t), geo(frac), geo(fp)


# ====================================================================== loss
def loss_fn(out, lab, frac, fp, w_frac=1.0, w_weak=0.0):
    logit, flog = out[:, 0], out[:, 1]
    m = (lab >= 0) & fp
    pos, neg = m & (lab == 1), m & (lab == 0)
    npos, nneg = pos.sum().clamp_min(1), neg.sum().clamp_min(1)
    tot = (npos + nneg).float()
    # class-balanced BCE; a class is never weighted more than 20x its pixel share (rare-positive batches)
    wp = torch.minimum(0.5 / npos, 10.0 / tot) if bool(pos.any()) else torch.zeros(())
    wn = torch.minimum(0.5 / nneg, 10.0 / tot) if bool(neg.any()) else torch.zeros(())
    bce = F.binary_cross_entropy_with_logits(logit, (lab == 1).float(), reduction="none")
    l_cls = (bce * (pos * wp + neg * wn)).sum() / max(float((pos * wp + neg * wn).sum()), 1e-9)
    # fraction head: soft shoreline targets + hard labels (1 / 0)
    tgt = torch.where(torch.isfinite(frac), frac, (lab == 1).float())
    fm = (torch.isfinite(frac) | (lab >= 0)) & fp
    soft = torch.isfinite(frac) & (frac > 0.02) & fp   # true shoreline pixels (zero-fraction ones count as hard)
    pf = torch.sigmoid(flog)
    l_frac = torch.zeros((), device=out.device)
    if bool(fm.any()):
        err = F.smooth_l1_loss(pf, tgt, reduction="none", beta=0.1)
        # soft pixels count as much as all hard ones together (they are few and they are the point)
        l_frac = err[fm & ~soft].mean() if bool((fm & ~soft).any()) else l_frac
        if bool(soft.any()):
            l_frac = 0.5 * l_frac + 0.5 * err[soft].mean()
    weak = (lab == -2) & fp
    l_weak = torch.zeros((), device=out.device)
    if w_weak > 0 and bool(weak.any()):
        l_weak = F.binary_cross_entropy_with_logits(logit[weak], torch.zeros_like(logit[weak]))
    return l_cls + w_frac * l_frac + w_weak * l_weak, float(l_cls.detach()), float(l_frac.detach())


# ====================================================================== inference
@torch.no_grad()
def predict_scene(model, d, v, dev, tile=512, ov=32):
    """p_water, frac [T, H, W] float16 for a whole area (tiled, padded to a multiple of 8)."""
    model.eval()
    T = d["z"].shape[0]
    H, W = d["fp"].shape
    z = d["z"].float()[None].to(dev)
    dmask = torch.ones(1, T, dtype=torch.bool, device=dev)
    terr = terrain_channels(d)[None].to(dev)
    P = torch.zeros(T, H, W, dtype=torch.float16)
    Fr = torch.zeros(T, H, W, dtype=torch.float16)
    step = tile - 2 * ov
    for t in range(T):
        X = features(z, dmask, torch.tensor([t], device=dev), v, terr, d["fp"][None].to(dev))[0]
        for y0 in range(0, H, step):
            for x0 in range(0, W, step):
                ya, xa = max(y0 - ov, 0), max(x0 - ov, 0)
                yb, xb = min(y0 + step + ov, H), min(x0 + step + ov, W)
                xt = X[:, ya:yb, xa:xb]
                ph, pw = (-xt.shape[1]) % 8, (-xt.shape[2]) % 8
                xt = F.pad(xt[None], (0, pw, 0, ph), mode="replicate")
                o = model(xt)[0, :, :yb - ya, :xb - xa]
                ys, xs = y0 - ya, x0 - xa
                ye, xe = ys + min(step, H - y0), xs + min(step, W - x0)
                P[t, y0:y0 + ye - ys, x0:x0 + xe - xs] = torch.sigmoid(o[0, ys:ye, xs:xe]).half().cpu()
                Fr[t, y0:y0 + ye - ys, x0:x0 + xe - xs] = torch.sigmoid(o[1, ys:ye, xs:xe]).half().cpu()
    fp = d["fp"]
    return P * fp[None], Fr * fp[None]


def val_score(model, scenes, v, dev):
    """Balanced accuracy on the east validation strip of each training area (mean over areas)."""
    from scripts.detect.tune import Region
    bas = []
    for a, e in scenes.D.items():
        d = e["d"]
        H, W = d["fp"].shape
        x0 = max(e["c_val"] - 96, 0)
        sub = {"z": d["z"][:, :, :, x0:], "fp": d["fp"][:, x0:], "terrain": None}
        if v["terrain"] and d["terrain"] is not None:
            sub["terrain"] = {k: t[:, x0:] for k, t in d["terrain"].items()}
        P, _ = predict_scene(model, sub, v, dev)
        strip = torch.zeros(H, W - x0, dtype=torch.bool)
        strip[:, e["c_val"] - x0:] = True
        lab = d["lab"][:, x0:]
        labd = {"perm": (lab == 1), "never": (lab == 0), "amb": torch.zeros_like(strip)}
        R = Region(labd, strip, torch.zeros(H, W - x0), d["km2"], P.shape[0])
        if R.n_perm + R.n_never == 0:
            continue
        m = R.metrics(P.float() >= 0.5)
        bas.append(m["balanced_acc"] if np.isfinite(m["balanced_acc"]) else m["spec_never"])
    model.train()
    return float(np.mean(bas)) if bas else float("nan")


# ====================================================================== training
def free_memory_pct():
    import subprocess
    try:
        out = subprocess.run(["memory_pressure"], capture_output=True, text=True, timeout=20).stdout
        return int(out.strip().splitlines()[-1].split(":")[-1].strip().rstrip("%"))
    except Exception:  # noqa: BLE001
        return 100


def run_name(variant, holdout):
    return f"supervised_{variant}_{'+'.join(sorted(holdout)) if holdout else 'none'}"


def train(areas_train, areas_val=None, variant="base", holdout=(), seed=SEED, verbose=True, save=True, iters=None,
          data=None, dev=None):
    """Train on areas_train (west 85 % of each); early-stop on their east strips. Returns (model, info)."""
    v = dict(VARIANTS[variant])
    if iters:
        v["iters"] = iters
    dev = dev or device()
    free = free_memory_pct()
    B = v["batch"]
    if free < 25:
        B = max(4, B // 2)
        print(f"  memory pressure (free {free} %): batch {B}")
    seed = v.get("seed", seed)
    torch.manual_seed(seed)
    gen = torch.Generator().manual_seed(seed)
    scenes = Scenes(areas_train, gen, data=data, weakneg=bool(v.get("weakneg")), weak_occ=v.get("weak_occ", WEAK_OCC))
    model = ResUNet(n_channels(v), v["width"], v["depth"]).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=v["lr"], weight_decay=v["wd"])
    warm = 100
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda i: min(1.0, (i + 1) / warm) * 0.5 * (1 + math.cos(math.pi * min(i, v["iters"]) / v["iters"])))
    hist = {"it": [], "loss": [], "l_cls": [], "l_frac": [], "val_it": [], "val_ba": []}
    best, best_state, best_it = -1.0, None, 0
    t0 = time.time()
    run = {"l": 0.0, "c": 0.0, "f": 0.0, "n": 0}
    every = max(100, v["iters"] // 8)
    for it in range(v["iters"]):
        b = scenes.batch(B)
        X, lab, frac, fp = augment(b, v, dev, gen)
        out = model(X)
        loss, lc, lf = loss_fn(out, lab, frac, fp, v["w_frac"], v.get("weak_w", WEAK_W) if v.get("weakneg") else 0.0)
        opt.zero_grad(set_to_none=True)
        loss.backward()
        opt.step()
        sched.step()
        run["l"] += float(loss.detach()); run["c"] += lc; run["f"] += lf; run["n"] += 1
        if (it + 1) % 50 == 0:
            n = run["n"]
            hist["it"].append(it + 1); hist["loss"].append(run["l"] / n)
            hist["l_cls"].append(run["c"] / n); hist["l_frac"].append(run["f"] / n)
            run = {"l": 0.0, "c": 0.0, "f": 0.0, "n": 0}
        if (it + 1) % every == 0 or it + 1 == v["iters"]:
            ba = val_score(model, scenes, v, dev)
            hist["val_it"].append(it + 1); hist["val_ba"].append(ba)
            if np.isfinite(ba) and ba >= best:
                best, best_it = ba, it + 1
                best_state = copy.deepcopy({k: t.detach().cpu() for k, t in model.state_dict().items()})
            if verbose:
                print(f"    it {it + 1}: loss {hist['loss'][-1]:.4f} val BA {ba:.4f} ({time.time() - t0:.0f}s)")
    if best_state is not None:
        model.load_state_dict(best_state)
    info = {"variant": variant, "config": v, "train_areas": list(areas_train), "holdout": list(holdout),
            "params": n_params(model), "train_s": round(time.time() - t0, 1), "best_val_ba": round(best, 5),
            "best_it": best_it, "history": hist, "batch": B, "free_mem_pct_at_start": free, "seed": seed}
    if save:
        torch.save({"state": model.state_dict(), "info": info}, MODELS / f"{run_name(variant, holdout)}.pt")
    del scenes
    return model, info


def load_model(variant, holdout, dev=None):
    dev = dev or device()
    ck = torch.load(MODELS / f"{run_name(variant, holdout)}.pt", weights_only=False, map_location="cpu")
    v = ck["info"]["config"]
    m = ResUNet(n_channels(v), v["width"], v["depth"])
    m.load_state_dict(ck["state"])
    return m.to(dev).eval(), ck["info"]


# ====================================================================== logistic regression baseline (no spatial context)
def train_logreg(areas_train, v=None, n_per_area=60_000, steps=600, seed=SEED):
    """Per-pixel logistic regression on exactly the CNN's input channels (training strips, real dates only)."""
    v = dict(v or BASE)
    gen = torch.Generator().manual_seed(seed)
    Xs, Ys = [], []
    for a in areas_train:
        d = load_area(a)
        H, W = d["fp"].shape
        c_tr = int(round((1 - VAL_FRAC) * W)) - BUF
        T = d["z"].shape[0]
        tl = train_labels(d)
        idx = torch.nonzero((tl >= 0) & d["fp"] & (torch.arange(W)[None] < c_tr))
        if idx.shape[0] == 0:
            continue
        lab = tl[idx[:, 0], idx[:, 1]]
        # balanced draw per area
        sel = []
        for c in (0, 1):
            ii = torch.nonzero(lab == c)[:, 0]
            if ii.numel():
                sel.append(ii[torch.randint(ii.numel(), (n_per_area // 2,), generator=gen)])
        sel = torch.cat(sel)
        tt = torch.randint(T, (sel.numel(),), generator=gen)
        z = d["z"].float()[None]
        Xall = []
        for t in range(T):
            X = features(z, torch.ones(1, T, dtype=torch.bool), torch.tensor([t]), v, terrain_channels(d)[None], d["fp"][None])[0]
            Xall.append(X[:, idx[sel[tt == t], 0], idx[sel[tt == t], 1]].T)
            Ys.append(lab[sel[tt == t]].float())
        Xs.append(torch.cat(Xall))
    X, Y = torch.cat(Xs), torch.cat(Ys)
    mu, sd = X.mean(0), X.std(0).clamp_min(1e-6)
    Xn = (X - mu) / sd
    lin = nn.Linear(X.shape[1], 1)
    opt = torch.optim.LBFGS(lin.parameters(), lr=0.5, max_iter=steps // 10)
    wpos = 0.5 / max(float(Y.mean()), 1e-6)
    wneg = 0.5 / max(1 - float(Y.mean()), 1e-6)
    wt = torch.where(Y > 0.5, torch.tensor(wpos), torch.tensor(wneg))

    def closure():
        opt.zero_grad()
        l = (F.binary_cross_entropy_with_logits(lin(Xn)[:, 0], Y, reduction="none") * wt).mean() + 1e-4 * lin.weight.pow(2).sum()
        l.backward()
        return l
    for _ in range(10):
        opt.step(closure)
    return {"w": lin.weight.detach()[0], "b": float(lin.bias.detach()), "mu": mu, "sd": sd, "v": v}


def predict_logreg(lr, d):
    T = d["z"].shape[0]
    z = d["z"].float()[None]
    P = []
    for t in range(T):
        X = features(z, torch.ones(1, T, dtype=torch.bool), torch.tensor([t]), lr["v"], terrain_channels(d)[None], d["fp"][None])[0]
        s = torch.einsum("chw,c->hw", (X - lr["mu"][:, None, None]) / lr["sd"][:, None, None], lr["w"]) + lr["b"]
        P.append(torch.sigmoid(s))
    return torch.stack(P) * d["fp"][None]


# ====================================================================== evaluation
def _r(x, nd=4):
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


def largest_body_km2(w, km2):
    lab = ops.label_components(w)
    out = []
    for t in range(w.shape[0]):
        ids, cnt = torch.unique(lab[t][lab[t] > 0], return_counts=True)
        out.append(round(float(cnt.max()) * km2, 1) if cnt.numel() else 0.0)
    return out


def spearman(a, b):
    from scripts.detect.tune import avg_rank, wpearson
    return wpearson(avg_rank(a.double()), avg_rank(b.double()))


def evaluate(d, methods, boot=200, p_water=None, frac=None, seed=SEED):
    """Metrics of every method's water [T, H, W] bool on the area's anchors (+ Sevan, sub-pixel, floodplain).
    All methods are scored on the same pixels: the radar footprint of the CNN AND of the math detector."""
    from scripts.detect.tune import Region
    area, km2, dates = d["area"], d["km2"], d["dates"]
    H, W = d["fp"].shape
    T = len(dates)
    fp = d["fp"] & d["math_valid"]
    lab = d["lab"]
    ref = {k: v.float() for k, v in d["ref"].items()}
    labd = {"perm": (lab == 1) & fp, "never": (lab == 0) & fp, "amb": d["amb"] & fp}
    R = Region(labd, torch.ones(H, W, dtype=torch.bool), ref["occ_mean"], km2, T)
    dom = fp & (ref["valid_frac"] >= 0.5)
    ext = ref["ext_frac"] >= 0.5
    ref_any = (ref["ext_frac"] > 0) & dom
    near3 = ops.dilate(ref_any[None], 7)[0]
    floodplain = dom & ext & (ref["occ_ge90_frac"] < 0.5)
    gen = torch.Generator().manual_seed(seed)
    res = {"area": area, "dates": dates, "n_perm_px": R.n_perm, "n_never_px": R.n_never, "n_amb_px": R.n_amb,
           "domain_km2": round(float(dom.sum()) * km2, 1), "methods": {}}
    for name, w in methods.items():
        if w is None:
            continue
        w = w.bool() & fp[None]
        m = {k: _r(v, 5) for k, v in R.metrics(w).items()}
        if boot:
            m["ci95"] = R.bootstrap(w, boot, gen)
        m["water_km2"] = [round(float(w[t].sum()) * km2, 1) for t in range(T)]
        m["false_far_km2_per_date"] = [round(float((w[t] & dom & ~ext & ~near3).sum()) * km2, 1) for t in range(T)]
        m["false_far_km2_late3"] = round(float((w[-3:].any(0) & dom & ~ext & ~near3).sum()) * km2, 1)
        late = w[-3:].any(0) & dom
        m["late_share_inside_max_extent"] = _r(float((late & ext).sum()) / max(float(late.sum()), 1))
        if float(floodplain.sum()) > 100:
            m["floodplain_spearman"] = _r(spearman(ref["occ_mean"][floodplain], w.sum(0)[floodplain].float()), 3)
        if area == "sevan":
            big = largest_body_km2(w, km2)
            m["largest_body_km2"] = big
            m["largest_body_mae_km2"] = round(float(np.mean([abs(b - SEVAN_KM2) for b in big])), 1)
            m["windy"] = {dd: big[dates.index(dd)] for dd in WINDY["sevan"] if dd in dates}
        res["methods"][name] = m
    hp = ROOT / f"data/processed/{area}/hydro/products.pt"
    if hp.exists():
        res["hydro"] = hydro_compare(d, {k: v for k, v in methods.items() if v is not None}, hp, fp)
    # sub-pixel: fraction head vs JRC fraction on stable-shoreline pixels
    ff = d["frac"].float()
    soft = torch.isfinite(ff) & (ff > 0.02) & (ff < 0.98) & fp   # true shoreline pixels (partly water)
    if frac is not None and int(soft.sum()) > 50:
        tgt = d["frac"].float()[soft]
        pf = frac.float()[:, soft]
        ph = (p_water.float()[:, soft] >= 0.5).float()
        res["subpixel"] = {"n_px": int(soft.sum()),
                           "rmse_fraction_head": _r(float(((pf - tgt) ** 2).mean().sqrt())),
                           "rmse_hard_mask": _r(float(((ph - tgt) ** 2).mean().sqrt())),
                           "rmse_probability": _r(float(((p_water.float()[:, soft] - tgt) ** 2).mean().sqrt())),
                           "bias_fraction_head": _r(float((pf - tgt).mean()))}
    if p_water is not None:
        # calibration on held-out anchors
        pm = labd["perm"] | labd["never"]
        p = p_water.float()[:, pm].flatten()
        y = (lab == 1)[pm][None].expand(T, -1).flatten().float()
        bins = torch.linspace(0, 1, 11)
        rel = []
        for lo, hi in zip(bins[:-1], bins[1:]):
            s = (p >= lo) & ((p < hi) if hi < 1 else (p <= hi))
            if int(s.sum()) > 0:
                rel.append({"lo": round(float(lo), 2), "hi": round(float(hi), 2), "n": int(s.sum()),
                            "mean_p": _r(float(p[s].mean())), "frac_water": _r(float(y[s].mean()))})
        # Brier with the two classes weighted equally (the loss is class-balanced)
        bw = float(((p - y) ** 2)[y > 0.5].mean()) if bool((y > 0.5).any()) else float("nan")
        bl = float(((p - y) ** 2)[y < 0.5].mean())
        res["calibration"] = {"reliability": rel, "brier": _r(float(((p - y) ** 2).mean()), 5),
                              "brier_balanced": _r(0.5 * (bw + bl) if np.isfinite(bw) else bl, 5)}
        if frac is not None and area == "sevan":
            lake = ops.dilate((p_water.float() >= 0.5), 7)
            big = []
            labc = ops.label_components(p_water.float() >= 0.5)
            for t in range(T):
                ids, cnt = torch.unique(labc[t][labc[t] > 0], return_counts=True)
                body = labc[t] == ids[cnt.argmax()] if cnt.numel() else torch.zeros(H, W, dtype=torch.bool)
                ring = ops.dilate(body[None], 7)[0] & fp
                big.append(round(float(frac[t].float()[ring].sum()) * km2, 1))
            res["sevan_area_from_fractions_km2"] = big
            del lake
    return res


BASELINE_PRODUCTS = [("hmm", "gmm", "water"), ("fusion_v1", "fusion", "water"),
                     ("crossscene", "crossscene", "water"), ("fusion_v2", "fusion2", "water"),
                     ("unet_v2", "dl", None)]


def hydro_compare(d, methods, path, fp):
    """Floodplain check against the independent expected extent (altimeter level + Landsat record, no radar;
    docs/eda/hydro.md): area per date vs expected and its range, and pixel agreement (IoU) with the expected set."""
    h = torch.load(path, weights_only=False, map_location="cpu")
    keep = torch.tensor(d.get("keep_idx", list(range(len(d["dates"])))))
    E, dom = h["expected"][keep].bool(), h["domain"].bool() & fp
    exp = [float(x) for x in h["expected_km2"][keep]]
    lo = [float(x) for x in h["expected_km2_lo"][keep]]
    hi = [float(x) for x in h["expected_km2_hi"][keep]]
    km2 = d["km2"]
    out = {"_expected_km2": [round(x, 1) for x in exp], "_lo_km2": [round(x, 1) for x in lo],
           "_hi_km2": [round(x, 1) for x in hi]}
    for name, w in methods.items():
        w = w.bool() & dom[None]
        a = [float(w[t].sum()) * km2 for t in range(w.shape[0])]
        inter = (w & E).flatten(1).sum(1).float()
        union = (w | E).flatten(1).sum(1).float().clamp_min(1)
        out[name] = {"km2": [round(x, 1) for x in a],
                     "ratio_first": _r(a[0] / exp[0], 3), "ratio_last": _r(a[-1] / exp[-1], 3),
                     "mae_km2": round(float(np.mean([abs(x - e) for x, e in zip(a, exp)])), 1),
                     "dates_in_range": int(sum(l_ <= x <= h_ for x, l_, h_ in zip(a, lo, hi))),
                     "iou_expected": [_r(float(v), 3) for v in (inter / union)],
                     "iou_mean": _r(float((inter / union).mean()), 3),
                     "extra_km2_first": round(float((w[0] & ~E[0]).sum()) * km2, 1),
                     "missed_km2_last": round(float((~w[-1] & E[-1]).sum()) * km2, 1)}
    return out


def baselines(d, holdout_areas_train=None):
    """Baselines on the area's grid, on the same (QC-kept) dates: math detector without / with terrain mask (same
    run, terrain applied as ops does), hidden Markov model (gmm), fusion v1, self-calibrating seeded detector
    (crossscene) and U-Net v2 (which learned the math detector), where their products exist."""
    area = d["area"]
    T, H, W = d["math_water"].shape
    keep = torch.tensor(d.get("keep_idx", list(range(T))))
    n_all = len(d.get("dates_all", d["dates"]))
    out = {"math": d["math_water"], "math+terrain": d["math_water_terrain"]}
    for name, sub, key in BASELINE_PRODUCTS:
        p = ROOT / f"data/processed/{area}/{sub}/products.pt"
        if not p.exists():
            continue
        g = torch.load(p, weights_only=False, map_location="cpu")
        w = (g["seg"] == g["seg_names"].index("open water")) if key is None else g[key]
        if tuple(w.shape) == (n_all, H, W):
            out[name] = w[keep].bool()
        del g
    return out


def full_eval(variant, holdout, areas_train, dev, boot=200, logreg=True, models_extra=None):
    """Evaluate a trained model (and the baselines) on each held-out area."""
    model, info = load_model(variant, holdout, dev)
    lr = train_logreg(areas_train, info["config"]) if logreg else None
    res = {}
    preds = {}
    for a in holdout:
        d = load_area(a)
        P, Fr = predict_scene(model, d, info["config"], dev)
        meth = {"cnn": P.float() >= 0.5}
        meth.update(baselines(d))
        if lr is not None:
            meth["logreg"] = predict_logreg(lr, d) >= 0.5
        res[a] = evaluate(d, meth, boot=boot, p_water=P, frac=Fr)
        preds[a] = (P, Fr)
    del model
    if dev.type == "mps":
        torch.mps.empty_cache()
    return res, info, preds


# ====================================================================== products
def save_products(area, model, info, trained_without, dev):
    """Product for one area from a model that never saw it. Every date is predicted; a QC-failed channel is
    replaced by its per-pixel median over the good dates (flagged in 'qc_imputed_dates')."""
    d = load_area(area, qc="impute")
    P, Fr = predict_scene(model, d, info["config"], dev)
    w = (P.float() >= 0.5) & d["fp"][None]
    out = ROOT / f"data/processed/{area}/supervised"
    out.mkdir(parents=True, exist_ok=True)
    torch.save({"p_water": P.half(), "water": w, "frac": Fr.half(), "water_km2": [round(float(w[t].sum()) * d["km2"], 1)
                for t in range(w.shape[0])], "dates": d["dates"], "bounds": d["bounds"], "km2_per_px": d["km2"],
                "trained_without": sorted(trained_without), "trained_on": info["train_areas"],
                "variant": info["variant"], "footprint": d["fp"], "ref_shift_px": d["ref_shift"],
                "qc_imputed_dates": d["qc_bad_dates"],
                "note": "p_water = P(open water) from a network trained on JRC GSW labels of OTHER places "
                        "(docs/eda/supervised.md); frac = sub-pixel water fraction head"},
               out / "products.pt")
    return out / "products.pt", [round(float(w[t].sum()) * d["km2"], 1) for t in range(w.shape[0])]


# ====================================================================== results file helpers
def load_results():
    p = OUT / "results.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save_results(R):
    OUT.mkdir(parents=True, exist_ok=True)
    R["updated"] = dt.datetime.now().isoformat(timespec="seconds")
    (OUT / "results.json").write_text(json.dumps(R, indent=1, default=float))


def slim_info(info):
    return {k: v for k, v in info.items() if k != "history"}


# ====================================================================== CLI
def cmd_build(args):
    areas = args.areas.split(",") if args.areas else AREAS_ALL
    ok, failed = build_dataset(areas)
    R = load_results()
    R["dataset"] = {}
    for a in ok:
        d = torch.load(cache_path(a), weights_only=False)
        lab = d["lab"]
        ref = d["ref"]
        R["dataset"][a] = {"dates": d["dates"], "grid": list(d["fp"].shape), "km2_per_px": d["km2"],
                           "water_px": int((lab == 1).sum()), "land_px": int((lab == 0).sum()),
                           "ignore_px_in_footprint": int(((lab < 0) & d["fp"]).sum()),
                           "soft_px": int(torch.isfinite(d["frac"].float()).sum()),
                           "water_km2": round(float((lab == 1).sum()) * d["km2"], 1),
                           "ref_shift_px": list(d["ref_shift"]), "ref_corr_before_after": list(d["ref_corr"]),
                           "terrain": d["terrain"] is not None,
                           "jrc_occ90_km2": round(float((ref["occ_ge90_frac"].float() * d["fp"]).sum()) * d["km2"], 1),
                           "jrc_max_extent_km2": round(float((ref["ext_frac"].float() * d["fp"]).sum()) * d["km2"], 1)}
    R["dataset_failed"] = failed
    save_results(R)
    print("built:", ok, "failed:", failed)


def _holdout(s):
    return [a for a in s.split(",") if a]


def cmd_rescore(args):
    """Re-score a saved model on its held-out areas against the current baselines (no retraining)."""
    hold = _holdout(args.holdout)
    tr = [a for a in available_areas() if a not in hold and a not in VARIANTS[args.variant].get("exclude_train", [])]
    res, info, preds = full_eval(args.variant, hold, tr, device(), boot=args.boot)
    del preds
    R = load_results()
    R.setdefault("runs", {})[run_name(args.variant, hold)] = {"info": slim_info(info), "history": info["history"],
                                                             "heldout": res}
    save_results(R)
    for a, r in res.items():
        print_summary(a, r)


def cmd_train(args):
    areas = available_areas()
    hold = _holdout(args.holdout)
    tr = [a for a in areas if a not in hold and a not in VARIANTS[args.variant].get("exclude_train", [])]
    dev = device()
    print(f"train {args.variant} on {tr}, holdout {hold}")
    _, info = train(tr, None, args.variant, hold, iters=args.iters)
    print(f"  params {info['params']:,}, {info['train_s']} s, best val BA {info['best_val_ba']} at it {info['best_it']}")
    res, info, preds = full_eval(args.variant, hold, tr, dev, boot=args.boot)
    R = load_results()
    R.setdefault("runs", {})[run_name(args.variant, hold)] = {"info": slim_info(info), "history": info["history"],
                                                             "heldout": res}
    save_results(R)
    if args.products:
        model, info = load_model(args.variant, hold, dev)
        for a in hold:
            print("  wrote", *save_products(a, model, info, hold, dev))
        del model
    for a, r in res.items():
        print_summary(a, r)


def print_summary(a, r):
    print(f"  == {a}")
    for k, m in r["methods"].items():
        extra = ""
        if "largest_body_km2" in m:
            extra = f" windy {m['windy']} MAE {m['largest_body_mae_km2']}"
        print(f"    {k:14s} rec {m['recall_perm']} fpr {m['fpr_never']} BA {m['balanced_acc']} IoU {m['iou']} "
              f"far-false late3 {m['false_far_km2_late3']} rho {m.get('floodplain_spearman')} "
              f"km2 {m['water_km2'][0]}->{m['water_km2'][-1]}{extra}")
    if "subpixel" in r:
        print("    subpixel", r["subpixel"])
    for k, m in (r.get("hydro") or {}).items():
        if not k.startswith("_"):
            print(f"    hydro {k:14s} km2 {m['km2'][0]:.0f}->{m['km2'][-1]:.0f} (exp {r['hydro']['_expected_km2'][0]:.0f}->"
                  f"{r['hydro']['_expected_km2'][-1]:.0f}) in-range {m['dates_in_range']} IoU {m['iou_mean']} MAE {m['mae_km2']}")


def cmd_loao(args):
    areas = available_areas()
    dev = device()
    R = load_results()
    L = R.setdefault("loao", {})
    todo = _holdout(args.only) if args.only else areas
    for a in todo:
        if a in L and not args.redo:
            continue
        tr = [b for b in areas if b != a]
        print(f"LOAO {a}: train on {tr}")
        _, info = train(tr, None, args.variant, [a], verbose=False)
        del _
        res, info, preds = full_eval(args.variant, [a], tr, dev, boot=args.boot)
        del preds
        L[a] = {"info": slim_info(info), "heldout": res[a]}
        print_summary(a, res[a])
        R = {**load_results(), "loao": L}
        save_results(R)
        if dev.type == "mps":
            torch.mps.empty_cache()


def cmd_curve(args):
    """Learning curve by number of training PLACES, tested on the main hold-out areas."""
    areas = [a for a in available_areas() if a not in MAIN_HOLDOUT]
    dev = device()
    rng = np.random.default_rng(SEED)
    R = load_results()
    C = R.setdefault("curve", {})
    sizes = [int(s) for s in args.sizes.split(",")]
    for n in sizes:
        draws = 1 if n >= len(areas) else args.draws
        for k in range(draws):
            sub = sorted(rng.choice(areas, size=min(n, len(areas)), replace=False).tolist())
            key = f"{n}:{'+'.join(sub)}"
            if key in C:
                continue
            print(f"curve n={n}: {sub}")
            _, info = train(sub, None, args.variant, MAIN_HOLDOUT, verbose=False, save=False)
            torch.save({"state": _.state_dict(), "info": info}, MODELS / "supervised_curve_tmp.pt")
            P = {}
            for a in MAIN_HOLDOUT:
                d = load_area(a)
                p, fr = predict_scene(_, d, info["config"], dev)
                r = evaluate(d, {"cnn": p.float() >= 0.5}, boot=0, p_water=p, frac=fr)
                P[a] = r["methods"]["cnn"]
                P[a]["subpixel"] = r.get("subpixel")
            C[key] = {"n": n, "areas": sub, "train_s": info["train_s"], "best_val_ba": info["best_val_ba"], "heldout": P}
            print("   ", {a: (P[a]["balanced_acc"], P[a]["false_far_km2_late3"]) for a in P})
            del _
            if dev.type == "mps":
                torch.mps.empty_cache()
            R = {**load_results(), "curve": C}
            save_results(R)
    (MODELS / "supervised_curve_tmp.pt").unlink(missing_ok=True)


def cmd_ablate(args):
    areas = available_areas()
    dev = device()
    R = load_results()
    A = R.setdefault("ablations", {})
    for var in args.variants.split(","):
        if var in A and not args.redo:
            continue
        tr = [a for a in areas if a not in MAIN_HOLDOUT and a not in VARIANTS[var].get("exclude_train", [])]
        print(f"ablation {var} on {tr}")
        _, info = train(tr, None, var, MAIN_HOLDOUT, verbose=False)
        del _
        res, info, _p = full_eval(var, MAIN_HOLDOUT, tr, dev, boot=0, logreg=False)
        A[var] = {"info": slim_info(info), "heldout": {a: {"cnn": r["methods"]["cnn"], "subpixel": r.get("subpixel"),
                                                            "calibration": {"brier_balanced": r.get("calibration", {}).get("brier_balanced")},
                                                            "hydro": (r.get("hydro") or {}).get("cnn")}
                                                       for a, r in res.items()}}
        for a, r in res.items():
            m = r["methods"]["cnn"]
            print(f"   {a}: BA {m['balanced_acc']} rec {m['recall_perm']} fpr {m['fpr_never']} far-false {m['false_far_km2_late3']}"
                  + (f" windy {m['windy']}" if "windy" in m else ""))
        R = {**load_results(), "ablations": A}
        save_results(R)


def cmd_predict(args):
    """Products for one area from a model that never saw it (main hold-out model or its LOAO model)."""
    a = args.area
    dev = device()
    hold = _holdout(args.holdout) if args.holdout else (MAIN_HOLDOUT if a in MAIN_HOLDOUT else [a])
    assert a in hold, "predict only with a model that never saw the area"
    model, info = load_model(args.variant, hold, dev)
    path, km2 = save_products(a, model, info, hold, dev)
    print("wrote", path, "trained on", info["train_areas"])
    print("water km2:", km2)


def main(argv=None):
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("build"); b.add_argument("--areas", default="")
    t = sp.add_parser("train"); t.add_argument("--holdout", default=",".join(MAIN_HOLDOUT))
    t.add_argument("--variant", default=FINAL); t.add_argument("--iters", type=int, default=None)
    t.add_argument("--boot", type=int, default=200)
    lo = sp.add_parser("loao"); lo.add_argument("--variant", default=LOAO_VARIANT); lo.add_argument("--only", default="")
    lo.add_argument("--redo", action="store_true"); lo.add_argument("--boot", type=int, default=100)
    t.add_argument("--products", action="store_true", help="write data/processed/<held-out area>/supervised")
    c = sp.add_parser("curve"); c.add_argument("--variant", default=LOAO_VARIANT); c.add_argument("--sizes", default="1,2,4,8")
    c.add_argument("--draws", type=int, default=2)
    ab = sp.add_parser("ablate"); ab.add_argument("--variants", default="fast,no_season,no_neigh,terrain,no_synth,no_floor")
    ab.add_argument("--redo", action="store_true")
    p = sp.add_parser("predict"); p.add_argument("--area", required=True); p.add_argument("--variant", default=FINAL)
    p.add_argument("--holdout", default="", help="which hold-out model, e.g. 'sevan' for the leave-one-area-out model")
    sp.add_parser("figures")
    rs = sp.add_parser("rescore"); rs.add_argument("--variant", default=FINAL)
    rs.add_argument("--holdout", default=",".join(MAIN_HOLDOUT)); rs.add_argument("--boot", type=int, default=200)
    args = ap.parse_args(argv)
    {"build": cmd_build, "train": cmd_train, "loao": cmd_loao, "curve": cmd_curve, "ablate": cmd_ablate,
     "predict": cmd_predict, "rescore": cmd_rescore, "figures": lambda a: make_figures()}[args.cmd](args)


METHOD_STYLE = {"cnn": ("#1b7837", "this network (never saw the area)"), "math": ("#b2182b", "math detector"),
                "math+terrain": ("#ef8a62", "math detector + terrain mask"), "hmm": ("#2166ac", "hidden Markov model"),
                "unet_v2": ("#999999", "U-Net v2 (learned the math detector)"),
                "logreg": ("#8073ac", "per-pixel logistic regression"),
                "fusion_v1": ("#e7298a", "fusion v1"), "fusion_v2": ("#000000", "fusion v2 (app)"), "crossscene": ("#a6761d", "self-calibrating seeded detector")}


def _save(fig, name):
    import matplotlib.pyplot as plt
    p = OUT / name
    fig.savefig(p, dpi=fig.dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  wrote {p.relative_to(ROOT)} ({p.stat().st_size / 1024:.0f} KB)")


def make_figures():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    R = load_results()
    OUT.mkdir(parents=True, exist_ok=True)
    main = R["runs"][run_name(FINAL, MAIN_HOLDOUT)]
    H = main["history"]
    # 1 training curves
    fig, ax = plt.subplots(1, 2, figsize=(10, 3.6), dpi=80)
    ax[0].plot(H["it"], H["loss"], label="total"); ax[0].plot(H["it"], H["l_cls"], label="water/land BCE")
    ax[0].plot(H["it"], H["l_frac"], label="fraction head")
    ax[0].set_xlabel("iteration"); ax[0].set_ylabel("training loss (50-it mean)"); ax[0].legend(fontsize=8)
    ax[0].set_yscale("log"); ax[0].grid(alpha=.3)
    ax[1].plot(H["val_it"], H["val_ba"], "o-", color="#1b7837")
    bi = main["info"]["best_it"]
    ax[1].axvline(bi, color="k", ls=":", label=f"kept (it {bi})")
    ax[1].set_xlabel("iteration"); ax[1].set_ylabel("balanced accuracy, validation strips"); ax[1].legend(fontsize=8)
    ax[1].grid(alpha=.3)
    fig.suptitle(f"Main model ({main['info']['params']:,} parameters), trained on "
                 f"{len(main['info']['train_areas'])} areas without Sevan and Tonle Sap", fontsize=9)
    _save(fig, "training_curves.png")

    # 2 Sevan windy date maps
    d = load_area("sevan")
    P = torch.load(ROOT / "data/processed/sevan/supervised/products.pt", weights_only=False)
    bl = baselines(d)
    t = d["dates"].index("2026-09-12")
    fp = d["fp"] & d["math_valid"]
    lab = d["lab"]
    panels = [("radar HH, 12 Sep (windy)", None), ("math detector", bl["math"][t]),
              ("math + terrain mask", bl["math+terrain"][t]), ("hidden Markov model", bl.get("hmm", bl["math"])[t]),
              ("this network (never saw Sevan)", P["water"][t]), ("JRC reference labels", None)]
    fig, axs = plt.subplots(2, 3, figsize=(11, 7.6), dpi=72)
    for ax, (title, w) in zip(axs.flat, panels):
        if title.startswith("radar"):
            ax.imshow(d["z"][t, 0].float().clamp(0, 3).numpy(), cmap="gray")
        elif title.startswith("JRC"):
            img = np.full(lab.shape + (3,), 0.85)
            img[(lab == 0).numpy()] = [0.97, 0.95, 0.88]
            img[(lab == 1).numpy()] = [0.1, 0.35, 0.75]
            ff = d["frac"].float()
            s = torch.isfinite(ff) & (ff > 0.02)
            img[s.numpy()] = np.stack([0.97 - 0.87 * ff[s].numpy(), 0.95 - 0.6 * ff[s].numpy(), 0.88 - 0.13 * ff[s].numpy()], 1)
            ax.imshow(img)
        else:
            img = np.full(lab.shape + (3,), 0.96)
            ww = (w.bool() & fp).numpy()
            img[ww] = [0.1, 0.35, 0.75]
            far = (ww & (lab == 0).numpy())
            img[far] = [0.85, 0.15, 0.1]
            img[~fp.numpy()] = 0.75
            ax.imshow(img)
        ax.set_title(title, fontsize=9); ax.set_xticks([]); ax.set_yticks([])
    fig.legend(handles=[Patch(color=[0.1, 0.35, 0.75], label="water"), Patch(color=[0.85, 0.15, 0.1], label="water on JRC never-water land"),
                        Patch(color=[0.97, 0.95, 0.88], label="reference: never water"), Patch(color=[0.85, 0.85, 0.85], label="reference: ignored (shore / uncertain)")],
               loc="lower center", ncol=4, fontsize=8)
    fig.suptitle("Lake Sevan on a windy date, held out of training. Reference: JRC GSW 1984-2021, Source: EC JRC/Google", fontsize=9)
    _save(fig, "sevan_windy.png")

    HO = main["heldout"]   # the reported model: trained on 8 places, never saw Sevan or Tonle Sap
    LO = {a: v for a, v in R.get("loao", {}).items() if v["info"]["variant"] == LOAO_VARIANT}
    hs = HO["sevan"]["methods"]
    # 3 Sevan area per date
    fig, ax = plt.subplots(figsize=(8.5, 4), dpi=80)
    dd = [dt.date.fromisoformat(x) for x in d["dates"]]
    for k, m in hs.items():
        c, lbl = METHOD_STYLE.get(k, ("k", k))
        ax.plot(dd, m["largest_body_km2"], "o-", color=c, label=f"{lbl} (MAE {m['largest_body_mae_km2']:.0f})", lw=2.2 if k == "cnn" else 1.2)
    if "sevan_area_from_fractions_km2" in HO["sevan"]:
        ax.plot(dd, HO["sevan"]["sevan_area_from_fractions_km2"], "*--", color="#1b7837", label="this network, summed fractions")
    if "sevan" in LO:
        ax.plot(dd, LO["sevan"]["heldout"]["methods"]["cnn"]["largest_body_km2"], "o--", color="#1b7837", alpha=0.5,
                label="network trained on 9 places incl. Tonle Sap")
    ax.axhline(SEVAN_KM2, color="k", ls="--", lw=0.8, label="published 1,242 km2")
    for w in WINDY["sevan"]:
        ax.axvline(dt.date.fromisoformat(w), color="0.6", ls=":", lw=1)
    ax.set_ylabel("largest water body, km2"); ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.set_title("Lake Sevan area per date (dotted lines: windy dates)", fontsize=9); ax.grid(alpha=.3)
    ax.legend(fontsize=7, ncol=2)
    lo = min(min(m["largest_body_km2"]) for m in hs.values())
    ax.set_ylim(max(lo - 30, 900), max(max(m["largest_body_km2"]) for m in hs.values()) + 30)
    _save(fig, "sevan_area.png")

    # 4 false water on slopes (Sevan, far from any JRC water), late season + per-date range
    fig, ax = plt.subplots(figsize=(7.5, 3.8), dpi=80)
    names = list(hs)
    vals = [hs[k]["false_far_km2_late3"] for k in names]
    lo_ = [min(hs[k]["false_far_km2_per_date"]) for k in names]
    hi_ = [max(hs[k]["false_far_km2_per_date"]) for k in names]
    ax.bar(range(len(names)), vals, color=[METHOD_STYLE.get(k, ("k",))[0] for k in names])
    ax.errorbar(range(len(names)), [(a + b) / 2 for a, b in zip(lo_, hi_)], yerr=[[(b - a) / 2 for a, b in zip(lo_, hi_)]] * 2,
                fmt="none", ecolor="k", capsize=4, label="per-date range")
    for i, vv in enumerate(vals):
        ax.text(i, vv + 2, f"{vv:.1f}", ha="center", fontsize=8)
    ax.set_xticks(range(len(names)), [METHOD_STYLE.get(k, (0, k))[1].replace(" (", "\n(") for k in names], fontsize=7)
    ax.set_ylabel("km2 of water > 3 px from any JRC water")
    ax.set_title("Sevan: false water on the surrounding land (bars: any of the last 3 dates)", fontsize=9)
    ax.legend(fontsize=8); ax.grid(alpha=.3, axis="y")
    _save(fig, "sevan_slope_false_water.png")

    # 5 LOAO heat-map
    L = LO
    if L:
        areas = list(L)
        meths = ["cnn", "logreg", "math", "math+terrain", "crossscene", "hmm", "fusion_v1", "fusion_v2", "unet_v2"]
        for metric, fname, fmt in (("balanced_acc", "loao_heatmap.png", "{:.3f}"),):
            M = np.full((len(areas), len(meths)), np.nan)
            F2 = np.full((len(areas), len(meths)), np.nan)
            for i, a in enumerate(areas):
                for j, k in enumerate(meths):
                    m = L[a]["heldout"]["methods"].get(k)
                    if m is None:
                        continue
                    val = m[metric] if m[metric] is not None else m["spec_never"]
                    M[i, j] = val if val is not None else np.nan
                    F2[i, j] = m["false_far_km2_late3"]
            fig, axs = plt.subplots(1, 2, figsize=(12, 0.45 * len(areas) + 2), dpi=80)
            im = axs[0].imshow(M, cmap="RdYlGn", vmin=0.8, vmax=1.0, aspect="auto")
            for i in range(len(areas)):
                for j in range(len(meths)):
                    if np.isfinite(M[i, j]):
                        axs[0].text(j, i, fmt.format(M[i, j]), ha="center", va="center", fontsize=7)
            axs[0].set_title("balanced accuracy on anchors (specificity where no permanent water)", fontsize=9)
            im2 = axs[1].imshow(np.log10(F2 + 1), cmap="Reds", aspect="auto")
            for i in range(len(areas)):
                for j in range(len(meths)):
                    if np.isfinite(F2[i, j]):
                        axs[1].text(j, i, f"{F2[i, j]:.0f}", ha="center", va="center", fontsize=7)
            axs[1].set_title("false water far from any JRC water, km2 (last 3 dates)", fontsize=9)
            for a_ in axs:
                a_.set_xticks(range(len(meths)), meths, fontsize=8, rotation=20)
                a_.set_yticks(range(len(areas)), areas, fontsize=8)
            fig.colorbar(im, ax=axs[0], fraction=0.04)
            fig.suptitle("Leave-one-area-out: each row is an area the network never saw. Grey = baseline not available", fontsize=9)
            _save(fig, fname)

    # 6 learning curve by number of places
    C = R.get("curve", {})
    if C:
        fig, axs = plt.subplots(1, 2, figsize=(10, 3.8), dpi=80)
        for a, c in (("sevan", "#d94801"), ("tonle-sap", "#08519c")):
            xs = [v["n"] for v in C.values()]
            ba = [v["heldout"][a]["balanced_acc"] for v in C.values()]
            ff = [v["heldout"][a]["false_far_km2_late3"] for v in C.values()]
            axs[0].plot(xs, ba, "o", color=c, alpha=0.6)
            axs[1].plot(xs, ff, "o", color=c, alpha=0.6)
            ns = sorted(set(xs))
            axs[0].plot(ns, [np.mean([b for x, b in zip(xs, ba) if x == n]) for n in ns], "-", color=c, label=a)
            axs[1].plot(ns, [np.mean([b for x, b in zip(xs, ff) if x == n]) for n in ns], "-", color=c, label=a)
        axs[0].set_ylabel("balanced accuracy on the held-out area"); axs[1].set_ylabel("false water far from JRC water, km2")
        for a_ in axs:
            a_.set_xlabel("number of training PLACES"); a_.grid(alpha=.3); a_.legend(fontsize=8); a_.set_xscale("log", base=2)
        axs[1].set_yscale("symlog", linthresh=1)
        fig.suptitle("Learning curve by number of places (dots: random draws of training areas)", fontsize=9)
        _save(fig, "learning_curve.png")

    # 7 reliability
    fig, ax = plt.subplots(figsize=(5, 4.5), dpi=80)
    ax.plot([0, 1], [0, 1], "k:", lw=1)
    sets = [(a, HO[a]["calibration"]) for a in MAIN_HOLDOUT]
    for a, c in zip(MAIN_HOLDOUT, ("#d94801", "#08519c")):
        rel = dict(sets)[a]["reliability"]
        ax.plot([r["mean_p"] for r in rel], [r["frac_water"] for r in rel], "o-", color=c,
                label=f"{a} (balanced Brier {dict(sets)[a]['brier_balanced']:.3f})")
    ax.set_xlabel("predicted water probability"); ax.set_ylabel("share that is JRC permanent water")
    ax.set_title("Reliability on held-out anchors\n(class-balanced loss: 0.5 means 'equal evidence')", fontsize=9)
    ax.legend(fontsize=8); ax.grid(alpha=.3)
    _save(fig, "reliability.png")

    # 8 Tonle Sap area per date + map of the last date
    dT = load_area("tonle-sap")
    PT = torch.load(ROOT / "data/processed/tonle-sap/supervised/products.pt", weights_only=False)
    blT = baselines(dT)
    ht = HO["tonle-sap"]["methods"]
    fig, axs = plt.subplots(1, 2, figsize=(12, 4.6), dpi=72, gridspec_kw={"width_ratios": [1.3, 1]})
    dd = [dt.date.fromisoformat(x) for x in dT["dates"]]
    hyd = HO["tonle-sap"].get("hydro") or main["heldout"]["tonle-sap"]["hydro"]
    axs[0].fill_between(dd, hyd["_lo_km2"], hyd["_hi_km2"], color="0.5", alpha=0.2,
                        label="expected range (altimeter level + Landsat record, no radar)")
    axs[0].plot(dd, hyd["_expected_km2"], "k--", lw=1.2, label="expected open water")
    for k, m in ht.items():
        c, lbl = METHOD_STYLE.get(k, ("k", k))
        axs[0].plot(dd, m["water_km2"], "o-", color=c, label=lbl, lw=2.2 if k == "cnn" else 1.2)
    old = R.get("ablations", {}).get("fast", {}).get("heldout", {}).get("tonle-sap", {}).get("cnn")
    if old:
        axs[0].plot(dd, old["water_km2"], "o--", color="#1b7837", alpha=0.5, label="before the fix (HH+HV, no weak target)")
    axs[0].set_ylabel("open water, km2"); axs[0].xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    axs[0].legend(fontsize=7); axs[0].grid(alpha=.3)
    axs[0].set_title("Tonle Sap open water per date (network never saw Tonle Sap)", fontsize=9)
    t = len(dT["dates"]) - 1
    fpT = dT["fp"] & dT["math_valid"]
    a_ = PT["water"][t] & fpT
    b_ = blT["math"][t] & fpT
    img = np.full(a_.shape + (3,), 0.96)
    img[(a_ & b_).numpy()] = [0.1, 0.35, 0.75]
    img[(a_ & ~b_).numpy()] = [0.1, 0.65, 0.3]
    img[(~a_ & b_).numpy()] = [0.85, 0.3, 0.1]
    ext = (dT["ref"]["ext_frac"].float() >= 0.5).numpy()
    img[~fpT.numpy()] = 0.75
    axs[1].imshow(img)
    axs[1].contour(ext, levels=[0.5], colors="k", linewidths=0.4)
    axs[1].set_title(f"{dT['dates'][t]}: blue both, green network only, orange math only;\nblack line = JRC maximum extent",
                     fontsize=9)
    axs[1].set_xticks([]); axs[1].set_yticks([])
    _save(fig, "tonle_sap_area.png")


if __name__ == "__main__":
    main()
