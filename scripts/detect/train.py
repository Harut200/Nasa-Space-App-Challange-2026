"""Train / run the two small deep-learning models on a radar preview stack.

    .venv/bin/python -m scripts.detect.train ae      --area tonle-sap            # temporal autoencoder
    .venv/bin/python -m scripts.detect.train unet    --area tonle-sap            # weakly supervised U-Net
    .venv/bin/python -m scripts.detect.train predict --area tonle-sap            # both -> products.pt
    .venv/bin/python -m scripts.detect.train predict --area sevan --weights-area tonle-sap

Weights:   data/processed/models/<ae|unet>_<area>.pt
Products:  data/processed/<area>/dl/products.pt
Figures:   docs/eda/dl/*.png
No ground truth exists: every number here is agreement with physics rules or a
self-supervised quantity, never validated accuracy.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import subprocess
import sys
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
import numpy as np
import torch
import torch.nn.functional as F

from scripts.detect.models import TemporalAE, UNetSmall, kmeans, kmeans_assign, n_params
from scripts.detect.stack import GAPS, ROOT, device as auto_device, load_preview_stack
from scripts.detect import ops
from scripts.detect.weak_labels import (CLASS_NAMES, FLOODED_VEG, IGNORE, OTHER, WATER, math_labels, smooth,
                                        weak_labels)

MODELS = ROOT / "data" / "processed" / "models"
RUNS = MODELS / "runs"
FIGS = ROOT / "docs" / "eda" / "dl"
SEG_COLORS = ["#1f4fbf", "#2ca02c", "#d9d0b8"]  # water, flooded veg, other
MIN_VALID_DATES = 6
EDGE_PX = 8  # swath-edge pixels dropped from training, metrics and products
HOLDOUT_FRAC = 0.3  # eastern 30 % of columns is held out (U-Net validation, AE validation pixels)

# ----------------------------------------------------------------------------- model versions for `sweep`
# Edit freely: name -> overrides of the command-line defaults. Every version uses the same seed and
# the same west/east split, so the leaderboard compares like with like.
V2 = {"v2": True, "epochs": 8, "steps": 100}  # shared settings of all v2 versions
VERSIONS = {
    "unet": {
        "base16_d3":       {"base": 16, "depth": 3},                       # the default model
        "base32_d3":       {"base": 32, "depth": 3},                       # wider
        "base16_d2":       {"base": 16, "depth": 2},                       # shallower (less context)
        "no_ref":          {"base": 16, "depth": 3, "no_ref": True},       # only this date's HH/HV
        "no_weights":      {"base": 16, "depth": 3, "no_class_weights": True},
        "base32_d2_noref": {"base": 32, "depth": 2, "no_ref": True},
        # ---- v2: labels from the math detector, Tonle Sap + Sevan + Ararat, see cmd_unet_v2
        "v2_t_d2":       {**V2, "base": 16, "depth": 2, "temporal": True},
        "v2_t_d3":       {**V2, "base": 16, "depth": 3, "temporal": True},
        "v2_not_d2":     {**V2, "base": 16, "depth": 2, "temporal": False},
        "v2_t_d2_tsonly": {**V2, "base": 16, "depth": 2, "temporal": True, "scenes": "tonle-sap"},
        "v2_t_d2_aug":   {**V2, "base": 16, "depth": 2, "temporal": True, "aug_noise": True},
        "v2_not_d2_aug": {**V2, "base": 16, "depth": 2, "temporal": False, "aug_noise": True},
    },
    "ae": {
        "gru_e8_k8":  {"encoder": "gru", "emb": 8, "k": 8},                # the default model
        "lstm_e8_k8": {"encoder": "lstm", "emb": 8, "k": 8},
        "conv_e8_k8": {"encoder": "conv", "emb": 8, "k": 8},
        "gru_e4_k6":  {"encoder": "gru", "emb": 4, "k": 6},
        "gru_e16_k8": {"encoder": "gru", "emb": 16, "k": 8},
        "gru_e8_k6":  {"encoder": "gru", "emb": 8, "k": 6},
    },
}


# ----------------------------------------------------------------------------- helpers
def load_stack(area: str):
    """Preview stack with the swath-edge no-data rim removed from `valid`.

    The previews pad outside the swath with brightness 1/255, which looks exactly like calm
    water. We flood-fill from the invalid area through pixels that dark (<= 1.5/255 in both
    channels), up to 40 px deep, and mark them invalid. Lakes not touching the swath edge stay.
    """
    st = load_preview_stack(area)
    st.raw_valid = st.valid.clone()  # footprint before the edge hack (input of the math detector)
    dark = (st.x.amax(1) <= 1.5 / 255)
    bad = ~st.valid
    for _ in range(40):
        grown = (F.max_pool2d(bad[:, None].float(), 3, 1, 1)[:, 0] > 0) & dark
        new = bad | grown
        if torch.equal(new, bad):
            break
        bad = new
    # pixels within ~8 px of the swath edge are about half brightness (preview edge artefact)
    near_edge = F.max_pool2d(bad[:, None].float(), 2 * EDGE_PX + 1, 1, EDGE_PX)[:, 0] > 0
    st.valid = st.valid & ~bad & ~near_edge
    return st


def version_suffix(args) -> str:
    return f"_{args.version}" if getattr(args, "version", None) else ""


def apply_version(args, model: str):
    if getattr(args, "version", None):
        for k, v in VERSIONS[model][args.version].items():
            setattr(args, k, v)
    return args


def write_run(model: str, args, metrics: dict):
    RUNS.mkdir(parents=True, exist_ok=True)
    name = f"{model}_{args.area}{version_suffix(args)}"
    cfg = {k: v for k, v in vars(args).items() if isinstance(v, (int, float, str, bool, type(None)))}
    (RUNS / f"{name}.json").write_text(json.dumps({"model": model, "version": args.version or "default",
                                                   "args": cfg, **metrics}, indent=2))
def seed_all(seed: int):
    torch.manual_seed(seed)
    np.random.seed(seed)


def sync(dev):
    if dev.type == "mps":
        torch.mps.synchronize()


def pick_device(name: str | None) -> torch.device:
    return torch.device(name) if name else auto_device()


def day_offsets(dates: list[str]) -> torch.Tensor:
    d0 = dt.date.fromisoformat(dates[0])
    return torch.tensor([(dt.date.fromisoformat(d) - d0).days for d in dates], dtype=torch.float32)


def as_dates(dates):
    return [dt.datetime.fromisoformat(d) for d in dates]


def shade_gap(ax):
    for a, b in GAPS:
        ax.axvspan(dt.datetime.fromisoformat(a), dt.datetime.fromisoformat(b), color="0.85", zorder=0,
                   label="no data (outage)")


def savefig(fig, name: str):
    FIGS.mkdir(parents=True, exist_ok=True)
    p = FIGS / name
    fig.savefig(p, dpi=90, bbox_inches="tight")
    plt.close(fig)
    if p.stat().st_size > 380_000:  # keep figures small for the repo: re-save with a 256-colour palette
        from PIL import Image
        Image.open(p).convert("RGB").quantize(256).save(p, optimize=True)
    print(f"  figure {p.relative_to(ROOT)} ({p.stat().st_size // 1024} KB)")


def seg_cmap():
    return ListedColormap(SEG_COLORS + ["#ffffff"])  # index 3 = ignore/invalid


def seg_for_plot(seg: torch.Tensor) -> np.ndarray:
    a = seg.clone().long()
    a[a == IGNORE] = 3
    return a.numpy()


# ----------------------------------------------------------------------------- autoencoder
def ae_features(st, t_norm: float = 120.0):
    """Per-pixel series features [N, T, 5] (HH, HV, HH-HV, day/120, valid) and targets [N, T, 2]."""
    s = smooth(st.x, st.valid)                                       # [T, 2, H, W]
    T, _, H, W = s.shape
    v = st.valid.float()
    days = (day_offsets(st.dates) / t_norm)[:, None, None].expand(T, H, W)
    feats = torch.stack([s[:, 0], s[:, 1], s[:, 0] - s[:, 1], days, v], dim=-1)  # [T, H, W, 5]
    feats[..., :3] *= v[..., None]
    feats = feats.permute(1, 2, 0, 3).reshape(H * W, T, 5)
    target = s.permute(2, 3, 0, 1).reshape(H * W, T, 2)
    mask = st.valid.permute(1, 2, 0).reshape(H * W, T)
    tt = (day_offsets(st.dates) / t_norm)[None, :, None]
    return feats, target, mask, tt


def masked_mse(pred, target, mask):
    m = mask[..., None].float()
    return ((pred - target) ** 2 * m).sum() / (m.sum() * pred.shape[-1]).clamp_min(1)


@torch.no_grad()
def ae_run_all(model, feats, target, mask, tt, dev, chunk=131072):
    model.eval()
    zs, errs = [], []
    for i in range(0, feats.shape[0], chunk):
        f = feats[i:i + chunk].to(dev)
        rec, z = model(f, tt.to(dev).expand(f.shape[0], -1, -1))
        m = mask[i:i + chunk].to(dev).float()
        e = (((rec - target[i:i + chunk].to(dev)) ** 2).mean(-1) * m).sum(1) / m.sum(1).clamp_min(1)
        zs.append(z.cpu())
        errs.append(e.cpu())
    return torch.cat(zs), torch.cat(errs)


def describe_cluster(hh: np.ndarray, hv: np.ndarray, late_idx) -> str:
    """Rough automatic wording from a mean curve; a human should confirm it on the map."""
    lvl = hh.mean()
    early, late = hh[:3].mean(), hh[late_idx].mean()
    if lvl < 0.15 and hh.max() < 0.25:
        return "dark all season (permanent open water)"
    if early - late > 0.12 and late < 0.3:
        return "darkened late (newly open water / flooded late)"
    if late - early > 0.1:
        return "brightened late (flooding under vegetation?)"
    if early - late > 0.08:
        return "darkened (wetter / harvest)"
    if hv.mean() > 0.6:
        return "bright & stable, high HV (forest / built-up)"
    if lvl > 0.45:
        return "mid-bright, stable land"
    return "mid-dark, variable (cropland / grass)"


def cmd_ae(args):
    apply_version(args, "ae")
    dev = pick_device(args.device)
    seed_all(args.seed)
    print(f"device: {dev}")
    st = load_stack(args.area)
    feats, target, mask, tt = ae_features(st)
    good = mask.sum(1) >= MIN_VALID_DATES
    idx_all = torch.nonzero(good).squeeze(1)
    g = torch.Generator().manual_seed(args.seed)
    n_train = min(args.pixels, idx_all.numel())
    W_img = st.x.shape[-1]
    east = (idx_all % W_img) >= int(W_img * (1 - HOLDOUT_FRAC))   # same spatial hold-out as the U-Net
    west_idx, east_idx = idx_all[~east], idx_all[east]
    tr = west_idx[torch.randperm(west_idx.numel(), generator=g)[:n_train]]
    va = east_idx[torch.randperm(east_idx.numel(), generator=g)[:20000]]

    model = TemporalAE(n_in=feats.shape[-1], n_out=target.shape[-1], hidden=args.hidden, emb=args.emb,
                       encoder=args.encoder).to(dev)
    print(f"TemporalAE params: {n_params(model):,}   train pixels {tr.numel():,}   val pixels {va.numel():,}")
    opt = torch.optim.AdamW(model.parameters(), lr=3e-3, weight_decay=1e-4)
    steps = math.ceil(tr.numel() / args.batch) * args.epochs
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=3e-3, total_steps=steps)
    Xtr, Ytr, Mtr = feats[tr].to(dev), target[tr].to(dev), mask[tr].to(dev)
    Xva, Yva, Mva = feats[va].to(dev), target[va].to(dev), mask[va].to(dev)
    ttd = tt.to(dev)
    hist = {"train": [], "val": []}
    t_start = time.time()
    for ep in range(args.epochs):
        t0 = time.time()
        model.train()
        perm = torch.randperm(tr.numel(), generator=g).to(dev)
        tot, nb = 0.0, 0
        for i in range(0, tr.numel(), args.batch):
            b = perm[i:i + args.batch]
            x = Xtr[b]
            # input dropout of whole dates: forces the model to rely on the season, not one date
            drop = (torch.rand(x.shape[:2], device=dev) < 0.1)
            x = x.clone()
            x[..., [0, 1, 2, 4]] *= (~drop)[..., None].float()
            rec, _ = model(x, ttd.expand(b.numel(), -1, -1))
            loss = masked_mse(rec, Ytr[b], Mtr[b])
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            sched.step()
            tot += loss.item()
            nb += 1
        model.eval()
        with torch.no_grad():
            rv, _ = model(Xva, ttd.expand(va.numel(), -1, -1))
            lv = masked_mse(rv, Yva, Mva).item()
        sync(dev)
        hist["train"].append(tot / nb)
        hist["val"].append(lv)
        print(f"epoch {ep + 1}/{args.epochs}  train mse {tot / nb:.5f}  val mse {lv:.5f}  {time.time() - t0:.1f}s")
    train_time = time.time() - t_start
    print(f"training time {train_time:.1f}s on {dev}")

    # whole image: embedding, reconstruction error, k-means clusters
    z, err = ae_run_all(model, feats, target, mask, tt, dev)
    zs = z[idx_all]
    mu, sd = zs.mean(0), zs.std(0).clamp_min(1e-6)
    zn = ((z - mu) / sd)
    samp = idx_all[torch.randperm(idx_all.numel(), generator=g)[:200000]]
    t0 = time.time()
    cent, _ = kmeans(zn[samp].to(dev), args.k, seed=args.seed)
    lab = kmeans_assign(zn.to(dev), cent).cpu()
    print(f"k-means (k={args.k}) on {samp.numel():,} embeddings: {time.time() - t0:.1f}s")
    # order clusters by season-mean HH (dark first) so ids are stable and readable
    tgt_mean = []
    for c in range(args.k):
        m = (lab == c) & good
        tgt_mean.append(target[m].mean(0) if m.any() else torch.zeros(target.shape[1:]))
    order = sorted(range(args.k), key=lambda c: tgt_mean[c][:, 0].mean().item())
    remap = torch.empty(args.k, dtype=torch.long)
    remap[torch.tensor(order)] = torch.arange(args.k)
    lab = remap[lab]
    cent = cent[torch.tensor(order)]
    curves = torch.stack([tgt_mean[c] for c in order]).numpy()   # [k, T, 2]
    curves_sd = []
    H, W = st.x.shape[-2:]
    late_idx = [i for i, d in enumerate(st.dates) if d >= "2026-09-15"] or [-1]
    names = [describe_cluster(curves[c, :, 0], curves[c, :, 1], late_idx) for c in range(args.k)]
    frac = [((lab == c) & good).sum().item() / good.sum().item() for c in range(args.k)]
    for c in range(args.k):
        m = (lab == c) & good
        curves_sd.append(target[m].std(0).numpy())
        print(f"  cluster {c}: {frac[c] * 100:5.1f}%  HH {np.round(curves[c, :, 0], 2)}  -> {names[c]}")

    MODELS.mkdir(parents=True, exist_ok=True)
    torch.save({"state_dict": model.state_dict(), "config": {"n_in": feats.shape[-1], "n_out": target.shape[-1],
                "hidden": args.hidden, "emb": args.emb, "encoder": args.encoder}, "z_mu": mu, "z_sd": sd, "centroids": cent.cpu(),
                "cluster_names": names, "cluster_curves": torch.tensor(curves), "dates": st.dates,
                "history": hist, "train_time_s": train_time, "device": str(dev), "epochs": args.epochs,
                "seed": args.seed}, MODELS / f"ae_{args.area}{version_suffix(args)}.pt")
    print(f"saved {MODELS / f'ae_{args.area}{version_suffix(args)}.pt'}")
    n_cl = len(set(lab[good].tolist()))
    write_run("ae", args, {"params": n_params(model), "device": str(dev), "train_time_s": train_time,
                           "final_train_loss": hist["train"][-1], "heldout_recon_mse": hist["val"][-1],
                           "cluster_fractions": frac, "cluster_names": names, "nonempty_clusters": n_cl})
    if args.version:
        return  # sweep runs skip figures
    # figures
    fig, ax = plt.subplots(figsize=(5.5, 3.5))
    ax.plot(range(1, args.epochs + 1), hist["train"], "o-", label="train")
    ax.plot(range(1, args.epochs + 1), hist["val"], "s--", label="held-out pixels")
    ax.set(xlabel="epoch", ylabel="masked MSE (brightness$^2$)", title=f"Temporal autoencoder: {args.area}")
    ax.set_yscale("log")
    ax.legend()
    savefig(fig, f"ae_training_{args.area}.png")

    cmap = plt.get_cmap("tab10", args.k)
    trajmap = lab.reshape(H, W).float().numpy()
    trajmap[~good.reshape(H, W).numpy()] = np.nan
    fig = plt.figure(figsize=(14, 6.2))
    ax0 = fig.add_axes([0.0, 0.05, 0.42, 0.88])
    ax0.imshow(trajmap[::2, ::2], cmap=cmap, vmin=-0.5, vmax=args.k - 0.5, interpolation="nearest")
    ax0.set_title(f"Season-behaviour clusters (autoencoder embedding + k-means), {args.area}")
    ax0.axis("off")
    dts = as_dates(st.dates)
    ncol = 2
    nrow = math.ceil(args.k / ncol)
    for c in range(args.k):
        ax = fig.add_axes([0.47 + (c % ncol) * 0.27, 0.93 - (c // ncol + 1) * (0.88 / nrow) + 0.04, 0.24,
                           0.88 / nrow - 0.07])
        shade_gap(ax)
        for ch, col, nm in ((0, "tab:red", "HH"), (1, "tab:green", "HV")):
            ax.plot(dts, curves[c, :, ch], "o-", ms=3, color=col, label=nm)
            ax.fill_between(dts, curves[c, :, ch] - curves_sd[c][:, ch], curves[c, :, ch] + curves_sd[c][:, ch],
                            color=col, alpha=0.15)
        ax.set_ylim(0, 1)
        ax.set_title(f"[{c}] {frac[c] * 100:.0f}%: {names[c]}", fontsize=8, color=cmap(c), fontweight="bold")
        ax.tick_params(labelsize=7)
        ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%b"))
        if c == 0:
            ax.legend(fontsize=6, loc="upper right")
    savefig(fig, f"ae_clusters_{args.area}.png")

    em = err.reshape(H, W).numpy().copy()
    em[~good.reshape(H, W).numpy()] = np.nan
    fig, ax = plt.subplots(figsize=(7, 6.5))
    hi = np.nanpercentile(em, 99)
    im = ax.imshow(em[::2, ::2], cmap="magma", vmin=0, vmax=hi)
    ax.set_title(f"Autoencoder reconstruction error: {args.area}\n(bright = season unlike the typical pixel; "
                 f"top 1% saturate)", fontsize=10)
    ax.axis("off")
    fig.colorbar(im, ax=ax, shrink=0.7, label="mean squared error over valid dates")
    savefig(fig, f"ae_recon_error_{args.area}.png")


# ----------------------------------------------------------------------------- U-Net
def unet_inputs(x: torch.Tensor, valid: torch.Tensor, ref: int = 0, use_ref: bool = True) -> torch.Tensor:
    """[T, 2, H, W] raw -> [T, 4, H, W] network input (this date + reference date), normalised.
    use_ref=False gives [T, 2, H, W] (this date only)."""
    xv = x * valid[:, None].float()
    inp = torch.cat([xv, xv[ref:ref + 1].expand_as(xv)], dim=1) if use_ref else xv
    return (inp - 0.5) / 0.25


def augment(x, y):
    """Random flips and 90-degree rotations, same for the whole batch (cheap) and per-sample flips."""
    k = int(torch.randint(4, (1,)))
    x, y = torch.rot90(x, k, (-2, -1)), torch.rot90(y, k, (-2, -1))
    if torch.rand(1) < 0.5:
        x, y = x.flip(-1), y.flip(-1)
    if torch.rand(1) < 0.5:
        x, y = x.flip(-2), y.flip(-2)
    return x, y


def iou_table(pred: torch.Tensor, lab: torch.Tensor):
    m = lab != IGNORE
    out = []
    for c in range(3):
        p, l = (pred == c) & m, (lab == c) & m
        inter, union = (p & l).sum().item(), (p | l).sum().item()
        out.append(inter / union if union else float("nan"))
    agree = ((pred == lab) & m).sum().item() / max(m.sum().item(), 1)
    return out, agree


@torch.no_grad()
def unet_predict(model, inp: torch.Tensor, dev) -> torch.Tensor:
    model.eval()
    outs = []
    for t in range(inp.shape[0]):
        outs.append(model.predict_full(inp[t:t + 1].to(dev)).argmax(1)[0].to(torch.uint8).cpu())
    return torch.stack(outs)


def cmd_unet(args):
    apply_version(args, "unet")
    if getattr(args, "v2", False):
        return cmd_unet_v2(args)
    dev = pick_device(args.device)
    seed_all(args.seed)
    print(f"device: {dev}")
    st = load_stack(args.area)
    lab, th = weak_labels(st.x, st.valid)
    inp = unet_inputs(st.x, st.valid, use_ref=not args.no_ref)
    T, C, H, W = inp.shape
    split = int(W * (1 - HOLDOUT_FRAC))
    P = args.patch
    print("weak-label thresholds:", {k: round(v, 4) if isinstance(v, float) else v for k, v in th.items()})

    tr_lab = lab[:, :, :split]
    cnt = torch.stack([(tr_lab == c).sum() for c in range(3)]).float()
    w = (cnt.sum() / cnt).sqrt()
    w = w / w.mean()
    if args.no_class_weights:
        w = torch.ones(3)
    print(f"train labels (west 70%): {dict(zip(CLASS_NAMES, cnt.long().tolist()))}  class weights {w.numpy().round(2)}")

    model = UNetSmall(n_in=C, n_classes=3, base=args.base, depth=args.depth).to(dev)
    print(f"UNetSmall params: {n_params(model):,}")
    opt = torch.optim.AdamW(model.parameters(), lr=2e-3, weight_decay=1e-4)
    total = args.epochs * args.steps
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=2e-3, total_steps=total)
    crit = torch.nn.CrossEntropyLoss(weight=w.to(dev), ignore_index=IGNORE)
    inp_d, lab_d = inp.to(dev), lab.long().to(dev)
    # reference date is 87% "other": sample it less often
    p_date = torch.ones(T)
    p_date[0] = 0.3
    g = torch.Generator().manual_seed(args.seed)
    hist = {"train": [], "val": [], "val_iou": []}
    val_inp, val_lab = inp[..., split:], lab[..., split:]
    t_start = time.time()
    for ep in range(args.epochs):
        t0 = time.time()
        model.train()
        tot = 0.0
        for s in range(args.steps):
            ts = torch.multinomial(p_date, args.batch, replacement=True, generator=g)
            rs = torch.randint(0, H - P + 1, (args.batch,), generator=g)
            cs = torch.randint(0, split - P + 1, (args.batch,), generator=g)
            xb = torch.stack([inp_d[t, :, r:r + P, c:c + P] for t, r, c in zip(ts, rs, cs)])
            yb = torch.stack([lab_d[t, r:r + P, c:c + P] for t, r, c in zip(ts, rs, cs)])
            xb, yb = augment(xb, yb)
            loss = crit(model(xb), yb)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            sched.step()
            tot += loss.item()
        # held-out east 30 %
        model.eval()
        with torch.no_grad():
            vl, preds = 0.0, []
            for t in range(T):
                logit = model.predict_full(val_inp[t:t + 1].to(dev))
                vl += crit(logit, val_lab[t:t + 1].long().to(dev)).item() / T
                preds.append(logit.argmax(1)[0].to(torch.uint8).cpu())
        ious, agree = iou_table(torch.stack(preds), val_lab)
        sync(dev)
        hist["train"].append(tot / args.steps)
        hist["val"].append(vl)
        hist["val_iou"].append(ious)
        print(f"epoch {ep + 1}/{args.epochs}  train CE {tot / args.steps:.4f}  held-out CE {vl:.4f}  "
              f"IoU water {ious[0]:.3f} flooded-veg {ious[1]:.3f} other {ious[2]:.3f}  {time.time() - t0:.1f}s")
    train_time = time.time() - t_start
    print(f"training time {train_time:.1f}s on {dev}")

    pred = unet_predict(model, inp, dev)
    ious, agree = iou_table(pred[..., split:], lab[..., split:])
    ign = (lab == IGNORE) & st.valid
    ign_e = ign[..., split:]
    fill_all = [((pred == c) & ign).sum().item() / ign.sum().item() for c in range(3)]
    fill_east = [((pred[..., split:] == c) & ign_e).sum().item() / ign_e.sum().item() for c in range(3)]
    print("held-out (east 30%) agreement with weak labels:",
          {n: round(v, 3) for n, v in zip(CLASS_NAMES, ious)}, f"pixel agreement {agree:.3f}")
    print("ignored pixels -> network class (whole image):", {n: round(v, 3) for n, v in zip(CLASS_NAMES, fill_all)})
    print("ignored pixels -> network class (east hold-out):", {n: round(v, 3) for n, v in zip(CLASS_NAMES, fill_east)})

    MODELS.mkdir(parents=True, exist_ok=True)
    torch.save({"state_dict": model.state_dict(), "config": {"n_in": C, "n_classes": 3, "base": args.base, "depth": args.depth},
                "use_ref": not args.no_ref,
                "thresholds": th, "class_names": CLASS_NAMES, "history": hist, "heldout_iou": ious,
                "heldout_agree": agree, "ignored_fill_all": fill_all, "ignored_fill_east": fill_east,
                "train_time_s": train_time, "device": str(dev), "epochs": args.epochs, "steps": args.steps,
                "seed": args.seed, "split_col": split}, MODELS / f"unet_{args.area}{version_suffix(args)}.pt")
    print(f"saved {MODELS / f'unet_{args.area}{version_suffix(args)}.pt'}")
    write_run("unet", args, {"params": n_params(model), "device": str(dev), "train_time_s": train_time,
                             "final_train_loss": hist["train"][-1], "heldout_ce": hist["val"][-1],
                             "heldout_iou": dict(zip(CLASS_NAMES, ious)), "heldout_miou": float(np.nanmean(ious)),
                             "heldout_pixel_agreement": agree,
                             "ignored_fill_east": dict(zip(CLASS_NAMES, fill_east)),
                             "ignored_fill_all": dict(zip(CLASS_NAMES, fill_all))})
    if args.version:
        return

    # figures: curves
    fig, axs = plt.subplots(1, 2, figsize=(10, 3.5))
    e = range(1, args.epochs + 1)
    axs[0].plot(e, hist["train"], "o-", label="train (west 70%)")
    axs[0].plot(e, hist["val"], "s--", label="held-out (east 30%)")
    axs[0].set(xlabel="epoch", ylabel="weighted cross-entropy", title="U-Net loss vs weak labels")
    axs[0].legend()
    vi = np.array(hist["val_iou"])
    for c in range(3):
        axs[1].plot(e, vi[:, c], "o-", color=SEG_COLORS[c] if c < 2 else "0.4", label=CLASS_NAMES[c])
    axs[1].set(xlabel="epoch", ylabel="IoU with rule labels", ylim=(0, 1),
               title="Held-out agreement with RULES (not truth)")
    axs[1].legend()
    savefig(fig, f"unet_training_{args.area}.png")

    # figure: input vs rules vs network on a late flood date, two zooms
    t = min(range(T), key=lambda i: abs((dt.date.fromisoformat(st.dates[i]) - dt.date(2026, 9, 23)).days))
    fig, axs = plt.subplots(2, 3, figsize=(13, 8.6))
    rgb = torch.stack([st.x[t, 0], st.x[t, 1], st.x[t, 0]], -1).clamp(0, 1).numpy()
    for row, (r0, r1, c0, c1) in enumerate([(0, H, 0, W), (int(H * .45), int(H * .75), int(W * .55), int(W * .85))]):
        sl = (slice(r0, r1), slice(c0, c1))
        step = 2 if row == 0 else 1
        axs[row, 0].imshow(rgb[sl][::step, ::step])
        axs[row, 1].imshow(seg_for_plot(lab[t])[sl][::step, ::step], cmap=seg_cmap(), vmin=-0.5, vmax=3.5,
                           interpolation="nearest")
        axs[row, 2].imshow(seg_for_plot(pred[t])[sl][::step, ::step], cmap=seg_cmap(), vmin=-0.5, vmax=3.5,
                           interpolation="nearest")
        if row == 0:
            for a in axs[0]:
                a.axvline(split / step, color="r", lw=1, ls="--")
                a.add_patch(plt.Rectangle((c0 / 2 + W * .55 / 2, H * .45 / 2), W * .3 / 2, H * .3 / 2, fill=False,
                                          ec="yellow", lw=1.2))
        for a in axs[row]:
            a.set_xticks([]), a.set_yticks([])
    axs[0, 0].set_title(f"input {st.dates[t]} (R=B=HH, G=HV)")
    axs[0, 1].set_title("physics rules (white = not sure)")
    axs[0, 2].set_title("U-Net (fills every pixel)")
    axs[1, 0].set_ylabel("zoom (yellow box)")
    axs[0, 0].text(split / 2 + 5, 20, "hold-out →", color="r", fontsize=8)
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in SEG_COLORS] + [plt.Rectangle((0, 0), 1, 1, fc="w", ec="k")]
    fig.legend(handles, list(CLASS_NAMES) + ["not sure / no data"], loc="lower center", ncol=4)
    savefig(fig, f"unet_rules_vs_net_{args.area}.png")


# ----------------------------------------------------------------------------- U-Net v2 (math labels)
# scene -> fraction of columns (eastern side) held out; None = whole scene used for training
V2_SCENES = {"tonle-sap": 0.30, "sevan": 1 / 3, "ararat": None}
V2_SCENE_P = {"tonle-sap": 0.5, "sevan": 0.25, "ararat": 0.25}  # sampling share per scene (balance)
SEVAN_TRUE_KM2 = 1242.0
BEST_FILE = MODELS / "unet_v2_best.txt"


def v2_scene(area: str, dev=None, band_px: int = 1) -> dict:
    """Stack + math-detector products + dense v2 labels for one area (labels cached on disk)."""
    st = load_stack(area)
    cache = ROOT / "data" / "processed" / area / "dl" / "math_labels.pt"
    if cache.exists():
        mp = torch.load(cache, weights_only=False)
    else:
        o = ops.run_pipeline(st.x, st.raw_valid, st.source, dev=dev or auto_device())
        mp = {k: o[k].cpu() for k in ("water", "flooded_veg", "wind_suspect", "valid")}
        cache.parent.mkdir(parents=True, exist_ok=True)
        torch.save(mp, cache)
    valid = st.valid & mp["valid"]
    lab = math_labels(mp["water"], mp["flooded_veg"], valid, mp["wind_suspect"], band_px)
    W = st.x.shape[-1]
    hf = V2_SCENES.get(area)
    split = W if hf is None else int(W * (1 - hf))
    return {"area": area, "st": st, "math": mp, "valid": valid, "lab": lab, "split": split}


def math_dense(mp: dict) -> torch.Tensor:
    """Math products -> dense class map [T, H, W] (no ignore band; IGNORE only where invalid)."""
    c = torch.full(mp["water"].shape, OTHER, dtype=torch.uint8)
    c[mp["flooded_veg"]] = FLOODED_VEG
    c[mp["water"]] = WATER
    c[~mp["valid"]] = IGNORE
    return c


def v2_inputs(x: torch.Tensor, valid: torch.Tensor, temporal: bool = True, ref: int = 0) -> torch.Tensor:
    """x [B, T, 2, H, W], valid [B, T, H, W] -> network input [B, T, C, H, W].

    C = 4: this date HH, HV + reference date (first date) HH, HV (as v1);
    C = 7 with temporal=True: + per-pixel season minimum, mean and standard deviation of HH
    over the valid dates (permanent water: low min AND low mean AND low std; a dark slope:
    low on every date too, but water-like min alone does not decide it; flood: high std).
    """
    v = valid[:, :, None].float()
    xv = x * v
    B, T = x.shape[:2]
    parts = [xv, xv[:, ref:ref + 1].expand_as(xv)]
    out = (torch.cat(parts, dim=2) - 0.5) / 0.25
    if not temporal:
        return out
    hh, vv = x[:, :, 0], valid.float()
    n = vv.sum(1).clamp_min(1)
    mean = (hh * vv).sum(1) / n
    sd = (((hh - mean[:, None]) ** 2 * vv).sum(1) / n).sqrt()
    mn = torch.where(valid, hh, torch.full_like(hh, 2.0)).amin(1)
    mn = torch.where(mn > 1.5, torch.zeros_like(mn), mn)
    tc = torch.stack([(mn - 0.5) / 0.25, (mean - 0.5) / 0.25, sd / 0.1], dim=1)  # [B, 3, H, W]
    return torch.cat([out, tc[:, None].expand(B, T, 3, *tc.shape[-2:])], dim=2)


def speckle(x: torch.Tensor, looks: int, gen: torch.Generator | None = None) -> torch.Tensor:
    """Multiplicative gamma noise, mean 1, variance 1/looks (sum of `looks` exponentials / looks)."""
    acc = torch.zeros_like(x)
    for _ in range(looks):
        u = torch.rand(x.shape, generator=gen, device=x.device if gen is None else "cpu").to(x.device)
        acc -= torch.log(u.clamp_min(1e-7))
    return (x * acc / looks).clamp(0, 1)


def perturb(x: torch.Tensor, looks: int | None = None, gain: float = 1.0, seed: int = 0) -> torch.Tensor:
    """Robustness-test perturbation of a whole stack [T, 2, H, W]: speckle and/or a global gain."""
    g = torch.Generator().manual_seed(seed)
    y = x.clone()
    if looks:
        y = speckle(y, looks, g)
    return (y * gain).clamp(0, 1)


def largest_cc_px(mask: np.ndarray) -> int:
    import scipy.ndimage as ndi
    lab, n = ndi.label(mask)
    return int(np.bincount(lab.ravel())[1:].max()) if n else 0


@torch.no_grad()
def v2_predict(model, x, valid, temporal, dev) -> torch.Tensor:
    inp = v2_inputs(x[None].to(dev), valid[None].to(dev), temporal)[0]
    return unet_predict(model, inp.cpu(), dev)


def seg_metrics(pred: torch.Tensor, lab: torch.Tensor, dense: torch.Tensor, region: torch.Tensor, km2: float) -> dict:
    """pred/lab/dense [T, H, W]; region [H, W] bool (e.g. held-out columns).
    IoU on labelled (non-ignore) pixels; area error = network area vs math area over all valid pixels."""
    r = region[None].expand_as(lab)
    ious, agree = iou_table(torch.where(r, pred, torch.full_like(pred, IGNORE)), torch.where(r, lab, torch.full_like(lab, IGNORE)))
    vm = r & (dense != IGNORE)
    area_net = [((pred == c) & vm).sum().item() * km2 / lab.shape[0] for c in range(3)]
    area_math = [((dense == c) & vm).sum().item() * km2 / lab.shape[0] for c in range(3)]
    rel = [(a - b) / b if b else float("nan") for a, b in zip(area_net, area_math)]
    return {"iou": dict(zip(CLASS_NAMES, ious)), "miou": float(np.nanmean(ious)), "agree": agree,
            "mean_area_net_km2": dict(zip(CLASS_NAMES, area_net)), "mean_area_math_km2": dict(zip(CLASS_NAMES, area_math)),
            "area_rel_err": dict(zip(CLASS_NAMES, rel))}


def v2_evaluate(model, sc: dict, temporal: bool, dev) -> dict:
    """Held-out agreement, per-date areas, ignore-band and wind-suspect behaviour for one scene."""
    st, lab, valid = sc["st"], sc["lab"], sc["valid"]
    pred = v2_predict(model, st.x, st.valid, temporal, dev)
    pred[~valid] = IGNORE
    dense = math_dense(sc["math"])
    dense[~valid] = IGNORE
    H, W = valid.shape[-2:]
    cols = torch.arange(W)[None].expand(H, W)
    hold = cols >= sc["split"]
    km2 = st.km2_per_px
    out = {"split_col": sc["split"]}
    if sc["split"] < W:
        out["heldout"] = seg_metrics(pred, lab, dense, hold, km2)
    band = (lab == IGNORE) & valid & ~sc["math"]["wind_suspect"]
    wind = sc["math"]["wind_suspect"] & valid
    reg = hold if sc["split"] < W else torch.ones(H, W, dtype=torch.bool)
    for name, m in (("band", band), ("wind", wind)):
        m = m & reg[None]
        n = max(m.sum().item(), 1)
        out[f"{name}_px"] = m.sum().item()
        out[f"{name}_fill"] = dict(zip(CLASS_NAMES, [((pred == c) & m).sum().item() / n for c in range(3)]))
        out[f"{name}_math_agree"] = ((pred == dense) & m).sum().item() / n
    T = pred.shape[0]
    out["dates"] = st.dates
    out["water_km2"] = [((pred[t] == WATER).sum() * km2).item() for t in range(T)]
    out["fveg_km2"] = [((pred[t] == FLOODED_VEG).sum() * km2).item() for t in range(T)]
    out["math_water_km2"] = [((dense[t] == WATER).sum() * km2).item() for t in range(T)]
    out["math_fveg_km2"] = [((dense[t] == FLOODED_VEG).sum() * km2).item() for t in range(T)]
    if sc["area"] == "sevan":
        out["water_lcc_km2"] = [largest_cc_px((pred[t] == WATER).numpy()) * km2 for t in range(T)]
        out["math_water_lcc_km2"] = [largest_cc_px((dense[t] == WATER).numpy()) * km2 for t in range(T)]
    return out, pred


def v2_score(ev: dict) -> float:
    """Mean of: Tonle Sap held-out mean IoU (3 classes) and Sevan held-out mean IoU over water and other land
    (the math detector finds ~no flooded vegetation at Sevan, so that IoU is 0 or undefined by construction)."""
    ts = ev["tonle-sap"]["heldout"]["miou"]
    sv = ev["sevan"]["heldout"]["iou"]
    return 0.5 * (ts + 0.5 * (sv[CLASS_NAMES[WATER]] + sv[CLASS_NAMES[OTHER]]))


def cmd_unet_v2(args):
    dev = pick_device(args.device)
    seed_all(args.seed)
    print(f"device: {dev}  (v2: labels from the math detector)")
    areas = args.scenes.split(",") if args.scenes else list(V2_SCENES)
    t_load = time.time()
    scenes = {a: v2_scene(a, dev, args.band_px) for a in dict.fromkeys(areas + ["tonle-sap", "sevan"])}
    print(f"scenes loaded in {time.time() - t_load:.1f}s")
    P, temporal = args.patch, args.temporal
    train_sc = [scenes[a] for a in areas]
    cnt = torch.zeros(3)
    for sc in train_sc:
        tl = sc["lab"][..., :sc["split"]]
        c = torch.stack([(tl == k).sum() for k in range(3)]).float()
        dark_other = ((tl == OTHER) & (sc["st"].x[:, 0, :, :sc["split"]] < 0.15)).sum().item()
        print(f"  {sc['area']}: {tuple(sc['st'].x.shape)} train cols < {sc['split']}  labels "
              f"{dict(zip(CLASS_NAMES, c.long().tolist()))} ignore {(tl == IGNORE).float().mean():.3f}  "
              f"dark (HH<0.15) 'other land' labels {dark_other:,}")
        cnt += c
    w = (cnt.sum() / cnt.clamp_min(1)).sqrt()
    w = w / w.mean()
    print(f"class weights {w.numpy().round(2)}")
    C = 7 if temporal else 4
    model = UNetSmall(n_in=C, n_classes=3, base=args.base, depth=args.depth).to(dev)
    print(f"UNetSmall params: {n_params(model):,}  inputs {C}  temporal={temporal}  aug_noise={args.aug_noise}")
    opt = torch.optim.AdamW(model.parameters(), lr=2e-3, weight_decay=1e-4)
    total = args.epochs * args.steps
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=2e-3, total_steps=total)
    crit = torch.nn.CrossEntropyLoss(weight=w.to(dev), ignore_index=IGNORE)
    dd = [(sc["st"].x.to(dev), sc["st"].valid.to(dev), sc["lab"].long().to(dev), sc["split"]) for sc in train_sc]
    p_sc = torch.tensor([V2_SCENE_P[sc["area"]] for sc in train_sc])
    g = torch.Generator().manual_seed(args.seed)
    hist = {"train": []}
    t_start = time.time()
    for ep in range(args.epochs):
        t0 = time.time()
        model.train()
        tot = 0.0
        for s in range(args.steps):
            si = torch.multinomial(p_sc, args.batch, replacement=True, generator=g)
            xs, vs, ys = [], [], []
            for i in si.tolist():
                x, v, y, split = dd[i]
                T, _, H, W = x.shape
                t = int(torch.multinomial(torch.tensor([0.3] + [1.0] * (T - 1)), 1, generator=g))
                r = int(torch.randint(0, H - P + 1, (1,), generator=g))
                c = int(torch.randint(0, split - P + 1, (1,), generator=g))
                xs.append(x[:, :, r:r + P, c:c + P])
                vs.append(v[:, r:r + P, c:c + P])
                ys.append(y[t, r:r + P, c:c + P])
                ys[-1] = (ys[-1], t)
            xb, vb = torch.stack(xs), torch.stack(vs)
            ts = torch.tensor([t for _, t in ys], device=dev)
            yb = torch.stack([y for y, _ in ys])
            if args.aug_noise:  # half of the samples: speckle (2-16 looks) and gain error (+-10 %, per date +-3 %)
                looks = int(torch.randint(2, 17, (1,), generator=g))
                noisy = speckle(xb, looks)
                gain = (0.9 + 0.2 * torch.rand(xb.shape[0], 1, 1, 1, 1, device=dev)) * \
                       (0.97 + 0.06 * torch.rand(xb.shape[0], xb.shape[1], 1, 1, 1, device=dev))
                noisy = (noisy * gain).clamp(0, 1)
                pick = (torch.rand(xb.shape[0], device=dev) < 0.5)[:, None, None, None, None]
                xb = torch.where(pick, noisy, xb)
            inp = v2_inputs(xb, vb, temporal)[torch.arange(xb.shape[0], device=dev), ts]
            inp, yb = augment(inp, yb)
            loss = crit(model(inp), yb)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            sched.step()
            tot += loss.item()
        sync(dev)
        hist["train"].append(tot / args.steps)
        print(f"epoch {ep + 1}/{args.epochs}  train CE {tot / args.steps:.4f}  {time.time() - t0:.1f}s")
    train_time = time.time() - t_start
    print(f"training time {train_time:.1f}s on {dev}")

    ev = {}
    for a in ("tonle-sap", "sevan", "ararat"):
        if a not in scenes:
            scenes[a] = v2_scene(a, dev, args.band_px)
        ev[a], _ = v2_evaluate(model, scenes[a], temporal, dev)
        h = ev[a].get("heldout")
        if h:
            print(f"{a} held-out vs MATH labels: IoU " + " / ".join(f"{v:.3f}" for v in h["iou"].values())
                  + f"  mIoU {h['miou']:.3f}  area rel err " + " / ".join(f"{v:+.2f}" for v in h["area_rel_err"].values()))
        print(f"{a} ignore band -> " + " / ".join(f"{v:.2f}" for v in ev[a]["band_fill"].values())
              + f"   wind-suspect ({ev[a]['wind_px']} px) -> " + " / ".join(f"{v:.2f}" for v in ev[a]["wind_fill"].values()))
    sv = ev["sevan"]
    print("Sevan water km2 (net total / net largest body / math total):",
          [f"{a:.0f}/{b:.0f}/{c:.0f}" for a, b, c in zip(sv["water_km2"], sv["water_lcc_km2"], sv["math_water_km2"])])
    sevan_err = float(np.mean([abs(v - SEVAN_TRUE_KM2) for v in sv["water_lcc_km2"]]))
    score = v2_score(ev)
    print(f"score (mean held-out mIoU Tonle Sap + Sevan) {score:.3f}   Sevan largest-body MAE vs 1242 km2: {sevan_err:.0f}")

    MODELS.mkdir(parents=True, exist_ok=True)
    path = MODELS / f"unet_{args.area}{version_suffix(args)}.pt"
    torch.save({"state_dict": model.state_dict(), "config": {"n_in": C, "n_classes": 3, "base": args.base, "depth": args.depth},
                "v2": True, "temporal": temporal, "use_ref": True, "class_names": CLASS_NAMES, "history": hist,
                "scenes": areas, "aug_noise": args.aug_noise, "band_px": args.band_px, "eval": ev,
                "train_time_s": train_time, "device": str(dev), "epochs": args.epochs, "steps": args.steps,
                "seed": args.seed}, path)
    print(f"saved {path}")
    write_run("unet", args, {"v2": True, "params": n_params(model), "device": str(dev), "train_time_s": train_time,
                             "final_train_loss": hist["train"][-1], "score": score, "sevan_lcc_mae_km2": sevan_err,
                             "eval": ev})


# ----------------------------------------------------------------------------- v2 robustness + figures
ROBUST_CONDS = [("clean", None, 1.0), ("speckle 16 looks", 16, 1.0), ("speckle 8 looks", 8, 1.0),
                ("speckle 4 looks", 4, 1.0), ("speckle 2 looks", 2, 1.0), ("gain -10 %", None, 0.9),
                ("gain +10 %", None, 1.1), ("4 looks + gain +10 %", 4, 1.1)]


def load_unet(version: str | None, weights_area: str, dev):
    us = f"_{version}" if version else ""
    ck = torch.load(MODELS / f"unet_{weights_area}{us}.pt", map_location="cpu", weights_only=False)
    m = UNetSmall(**ck["config"]).to(dev)
    m.load_state_dict(ck["state_dict"])
    m.eval()
    return m, ck


def cmd_robust(args):
    """Speckle / gain robustness of v2 network(s) vs the math detector, on the held-out parts."""
    dev = pick_device(args.device)
    versions = args.versions.split(",") if args.versions else [BEST_FILE.read_text().strip()]
    nets = {v: load_unet(v, "tonle-sap", dev) for v in versions}
    res = {"conds": [c[0] for c in ROBUST_CONDS], "math": {}, **{v: {} for v in versions}}
    t0 = time.time()
    for area in ("tonle-sap", "sevan"):
        sc = v2_scene(area, dev)
        st, lab, valid = sc["st"], sc["lab"], sc["valid"]
        H, W = valid.shape[-2:]
        hold = (torch.arange(W)[None].expand(H, W) >= sc["split"])
        clean = {}
        for k in res:
            if k != "conds":
                res[k][area] = []
        for i, (name, looks, gain) in enumerate(ROBUST_CONDS):
            xp = perturb(st.x, looks, gain, seed=100 + i)
            o = ops.run_pipeline(xp, st.raw_valid, st.source, dev=dev)
            mp = {k: o[k].cpu() for k in ("water", "flooded_veg", "valid")}
            md = math_dense(mp)
            md[~valid] = IGNORE
            outs = {"math": md}
            for v, (m, ck) in nets.items():
                p = v2_predict(m, xp, st.valid, ck["temporal"], dev)
                p[~valid] = IGNORE
                outs[v] = p
            for k, p in outs.items():
                r = seg_metrics(p, lab, math_dense(sc["math"]), hold, st.km2_per_px)
                if i == 0:
                    clean[k] = p
                # self-consistency: IoU of the perturbed output against the SAME method's clean output,
                # on every valid held-out pixel (no ignore band) -- fair to both methods
                r["self_iou"] = iou_table(torch.where(hold[None], p, torch.full_like(p, IGNORE)),
                                          torch.where(hold[None], clean[k], torch.full_like(p, IGNORE)))[0]
                r["self_iou"] = dict(zip(CLASS_NAMES, r["self_iou"]))
                res[k][area].append(r)
            line = f"{area:10} {name:22} math IoU " + "/".join(f"{x:.3f}" for x in res["math"][area][-1]["iou"].values())
            for v in versions:
                line += f"   {v} " + "/".join(f"{x:.3f}" for x in res[v][area][-1]["iou"].values())
            print(line, flush=True)
    print(f"robustness test {time.time() - t0:.1f}s")
    FIGS.mkdir(parents=True, exist_ok=True)
    (MODELS / "runs" / "robust_v2.json").write_text(json.dumps(res, indent=1))
    # table
    lines = ["# v2 robustness: masks on perturbed inputs vs CLEAN-input math labels (held-out parts)\n",
             "IoU per class (water / flooded veg / other) against the math labels of the clean input, "
             "ignore band excluded. 'math' = the math detector re-run on the perturbed stack. "
             "This is stability, not accuracy.\n"]
    for area in ("tonle-sap", "sevan"):
        lines += [f"\n## {area}\n", "| condition | math | " + " | ".join(versions) + " |",
                  "|---|" + "---|" * (1 + len(versions))]
        for i, c in enumerate(res["conds"]):
            row = [res["math"][area][i]] + [res[v][area][i] for v in versions]
            lines.append(f"| {c} | " + " | ".join(" / ".join(f"{x:.3f}" for x in r["iou"].values()) for r in row) + " |")
        lines += [f"\nSelf-consistency on {area}: IoU of each method's perturbed output against ITS OWN clean output "
                  "(all valid held-out pixels).\n", "| condition | math | " + " | ".join(versions) + " |",
                  "|---|" + "---|" * (1 + len(versions))]
        for i, c in enumerate(res["conds"]):
            row = [res["math"][area][i]] + [res[v][area][i] for v in versions]
            lines.append(f"| {c} | " + " | ".join(" / ".join(f"{x:.3f}" for x in r["self_iou"].values()) for r in row) + " |")
    (FIGS / "robust_v2.md").write_text("\n".join(lines) + "\n")
    # figure
    fig, axs = plt.subplots(2, 3, figsize=(15, 7.5), sharex=True)
    xs = np.arange(len(res["conds"]))
    panels = [("iou", WATER, "open water: IoU vs clean math labels"),
              ("self_iou", WATER, "open water: IoU vs OWN clean output"),
              ("self_iou", FLOODED_VEG, "flooded veg: IoU vs OWN clean output")]
    for r, area in enumerate(("tonle-sap", "sevan")):
        for c, (key, cls, ttl) in enumerate(panels):
            ax = axs[r, c]
            ax.plot(xs, [m[key][CLASS_NAMES[cls]] for m in res["math"][area]], "ko-", lw=2, label="math detector")
            for j, v in enumerate(versions):
                ax.plot(xs, [m[key][CLASS_NAMES[cls]] for m in res[v][area]], "s--", color=f"C{j}", label=f"network {v}")
            ax.set_title(f"{area}: {ttl}", fontsize=9)
            ax.set_ylim(0, 1.02)
            ax.grid(alpha=0.3)
        axs[r, 0].set_ylabel("IoU (held-out part)")
    for ax in axs[1]:
        ax.set_xticks(xs, res["conds"], rotation=40, ha="right", fontsize=8)
    axs[0, 0].legend(fontsize=8)
    fig.suptitle("Robustness on held-out parts under synthetic speckle / gain error (stability, not accuracy; "
                 "Sevan has ~no flooded vegetation, so its right panel is mostly empty)", fontsize=10)
    savefig(fig, "v2_robustness.png")


def cmd_figs_v2(args):
    """Comparison maps (rules v1 / math / v1 net / v2 net) and area-vs-date curves."""
    dev = pick_device(args.device)
    best = args.version or BEST_FILE.read_text().strip()
    m2, ck2 = load_unet(best, "tonle-sap", dev)
    m1, ck1 = load_unet(None, "tonle-sap", dev)
    areas_out = {}
    for area, date in (("tonle-sap", "2026-10-17"), ("sevan", "2026-08-31")):
        sc = v2_scene(area, dev)
        st, valid = sc["st"], sc["valid"]
        t = st.dates.index(date) if date in st.dates else len(st.dates) - 1
        rules, _ = weak_labels(st.x, st.valid)
        p1 = unet_predict(m1, unet_inputs(st.x, st.valid, use_ref=ck1.get("use_ref", True)), dev)
        p2 = v2_predict(m2, st.x, st.valid, ck2["temporal"], dev)
        md = math_dense(sc["math"])
        for a in (p1, p2, md):
            a[~valid] = IGNORE
        areas_out[area] = (st, md, p1, p2)
        km2 = st.km2_per_px
        H, W = valid.shape[-2:]
        step = 2 if H > 600 else 1
        fig, axs = plt.subplots(1, 5, figsize=(20, 3.3 * H / W + 1.0))
        rgb = torch.stack([st.x[t, 0], st.x[t, 1], st.x[t, 0]], -1).clamp(0, 1).numpy()
        axs[0].imshow(rgb[::step, ::step])
        axs[0].set_title(f"input {st.dates[t]} (R=B=HH, G=HV)", fontsize=9)
        for ax, a, ttl in ((axs[1], rules[t], "v1 rules (white = not sure)"), (axs[2], md[t], "math detector"),
                           (axs[3], p1[t], "v1 network (rules-trained)"), (axs[4], p2[t], f"v2 network ({best})")):
            ax.imshow(seg_for_plot(a)[::step, ::step], cmap=seg_cmap(), vmin=-0.5, vmax=3.5, interpolation="nearest")
            wk = ((a == WATER).sum() * km2).item()
            fk = ((a == FLOODED_VEG).sum() * km2).item()
            ax.set_title(f"{ttl}\nwater {wk:,.0f} km², flooded veg {fk:,.0f} km²", fontsize=9)
        for ax in axs:
            ax.set_xticks([]), ax.set_yticks([])
        if sc["split"] < W:
            for ax in axs:
                ax.axvline(sc["split"] / step, color="r", lw=1, ls="--")
            axs[0].text(sc["split"] / step + 3, 15, "hold-out →", color="r", fontsize=8)
        handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in SEG_COLORS] + [plt.Rectangle((0, 0), 1, 1, fc="w", ec="k")]
        fig.legend(handles, list(CLASS_NAMES) + ["not sure / no data"], loc="lower center", ncol=4,
                   bbox_to_anchor=(0.5, -0.02))
        fig.subplots_adjust(bottom=0.12, top=0.85, wspace=0.05)
        savefig(fig, f"v2_compare_{area}.png")

    # area vs date
    fig, axs = plt.subplots(1, 2, figsize=(14, 4.2))
    st, md, p1, p2 = areas_out["tonle-sap"]
    km2, dts = st.km2_per_px, as_dates(st.dates)
    ar = lambda a, c: [((a[t] == c).sum() * km2).item() for t in range(a.shape[0])]  # noqa: E731
    ax = axs[0]
    shade_gap(ax)
    ax.plot(dts, ar(md, WATER), "ko-", label="open water: math")
    ax.plot(dts, ar(p2, WATER), "o-", color=SEG_COLORS[0], label="open water: v2 network")
    ax.plot(dts, ar(p1, WATER), "o:", color=SEG_COLORS[0], alpha=0.5, label="open water: v1 network")
    ax.plot(dts, ar(md, FLOODED_VEG), "ks--", label="flooded veg: math")
    ax.plot(dts, ar(p2, FLOODED_VEG), "s-", color=SEG_COLORS[1], label="flooded veg: v2 network")
    ax.plot(dts, ar(p1, FLOODED_VEG), "s:", color=SEG_COLORS[1], alpha=0.5, label="flooded veg: v1 network")
    ax.set(ylabel="km² (same valid footprint)", title="Tonle Sap: area per date (not validated)")
    ax.legend(fontsize=7)
    st, md, p1, p2 = areas_out["sevan"]
    km2, dts = st.km2_per_px, as_dates(st.dates)
    lcc = lambda a: [largest_cc_px((a[t] == WATER).numpy()) * km2 for t in range(a.shape[0])]  # noqa: E731
    ax = axs[1]
    shade_gap(ax)
    ax.axhline(SEVAN_TRUE_KM2, color="r", lw=1.5, label="true lake area 1,242 km²")
    ax.plot(dts, ar(md, WATER), "ko-", label="math: all water")
    ax.plot(dts, lcc(md), "k^:", label="math: largest water body")
    ax.plot(dts, ar(p2, WATER), "o-", color=SEG_COLORS[0], label="v2 network: all water")
    ax.plot(dts, lcc(p2), "^--", color=SEG_COLORS[0], label="v2 network: largest water body")
    ax.plot(dts, ar(p1, WATER), "o:", color="0.5", label="v1 network: all water")
    ax.set(ylabel="km²", title="Lake Sevan: water area per date")
    ax.legend(fontsize=7, loc="upper left")
    for ax in axs:
        ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%d %b"))
        ax.grid(alpha=0.3)
        # dedupe legend (gap label)
    savefig(fig, "v2_area_vs_date.png")


# ----------------------------------------------------------------------------- predict
def load_models(weights_area: str, dev, unet_version=None, ae_version=None):
    us = f"_{unet_version}" if unet_version else ""
    as_ = f"_{ae_version}" if ae_version else ""  # "" or None -> default AE
    ck_u = torch.load(MODELS / f"unet_{weights_area}{us}.pt", map_location="cpu", weights_only=False)
    unet = UNetSmall(**ck_u["config"]).to(dev)
    unet.load_state_dict(ck_u["state_dict"])
    ck_a = torch.load(MODELS / f"ae_{weights_area}{as_}.pt", map_location="cpu", weights_only=False)
    ae = TemporalAE(**ck_a["config"]).to(dev)
    ae.load_state_dict(ck_a["state_dict"])
    return unet, ck_u, ae, ck_a


def cmd_predict(args):
    dev = pick_device(args.device)
    seed_all(args.seed)
    print(f"device: {dev}")
    if args.version is None and BEST_FILE.exists():
        args.version = BEST_FILE.read_text().strip()   # default: best v2 model of the last v2 sweep
    if args.version == "v1":
        args.version = None                             # the old rules-trained default weights
    is_v2 = bool(args.version) and args.version.startswith("v2")
    wa = args.weights_area or ("tonle-sap" if is_v2 else args.area)
    print(f"U-Net weights: unet_{wa}{'_' + args.version if args.version else ''}.pt")
    t0 = time.time()
    st = load_stack(args.area)
    unet, ck_u, ae, ck_a = load_models(wa, dev, args.version, args.ae_version or ("" if is_v2 else None))
    T, _, H, W = st.x.shape
    if ck_u.get("v2"):
        seg = v2_predict(unet, st.x, st.valid, ck_u["temporal"], dev)
    else:
        seg = unet_predict(unet, unet_inputs(st.x, st.valid, use_ref=ck_u.get("use_ref", True)), dev)
    seg[~st.valid] = IGNORE
    if ck_u.get("use_ref", True):
        seg[~st.valid[0:1].expand_as(st.valid)] = IGNORE  # no reference date there
    water = torch.tensor([((seg[t] == WATER).sum() * st.km2_per_px).item() for t in range(T)])
    fveg = torch.tensor([((seg[t] == FLOODED_VEG).sum() * st.km2_per_px).item() for t in range(T)])
    # rules on this area with its own thresholds, for comparison
    rl, _ = weak_labels(st.x, st.valid)
    r_water = torch.tensor([((rl[t] == WATER).sum() * st.km2_per_px).item() for t in range(T)])
    r_fveg = torch.tensor([((rl[t] == FLOODED_VEG).sum() * st.km2_per_px).item() for t in range(T)])
    # transfer check: of the water the rules are sure about on the reference (first) date,
    # how much does each date's segmentation still call water, and how much water lies outside it?
    core = rl[0] == WATER
    core_kept = torch.tensor([((seg[t] == WATER) & core).sum().item() / max(core.sum().item(), 1) for t in range(T)])
    rules_kept = torch.tensor([((rl[t] == WATER) & core).sum().item() / max(core.sum().item(), 1) for t in range(T)])
    water_outside = torch.tensor([(((seg[t] == WATER) & ~core).sum() * st.km2_per_px).item() for t in range(T)])

    if len(st.dates) != len(ck_a["dates"]):
        print("WARNING: date count differs from AE training; trajectories still computed with true day offsets")
    feats, target, mask, tt = ae_features(st)
    z, err = ae_run_all(ae, feats, target, mask, tt, dev)
    good = mask.sum(1) >= MIN_VALID_DATES
    zn = (z - ck_a["z_mu"]) / ck_a["z_sd"]
    traj = kmeans_assign(zn.to(dev), ck_a["centroids"].to(dev)).cpu().to(torch.uint8)
    traj[~good] = IGNORE
    err[~good] = float("nan")
    elapsed = time.time() - t0

    out = ROOT / "data" / "processed" / args.area / "dl"
    out.mkdir(parents=True, exist_ok=True)
    prod = {"dates": st.dates, "bounds": torch.tensor(st.bounds), "km2_per_px": st.km2_per_px,
            "seg": seg, "seg_names": list(CLASS_NAMES), "water_km2": water, "flooded_veg_km2": fveg,
            "rules_water_km2": r_water, "rules_flooded_veg_km2": r_fveg,
            "ref_water_kept": core_kept, "rules_ref_water_kept": rules_kept, "water_outside_ref_km2": water_outside,
            "traj_class": traj.reshape(H, W), "traj_names": ck_a["cluster_names"],
            "recon_error": err.reshape(H, W), "embedding": z.reshape(H, W, -1).permute(2, 0, 1).half(),
            "weights_area": wa, "source": st.source, "unet_version": args.version or "v1"}
    if ck_u.get("v2"):
        sc = v2_scene(args.area, dev)
        md = math_dense(sc["math"])
        prod["math_water_km2"] = torch.tensor([((md[t] == WATER) & st.valid[t]).sum().item() * st.km2_per_px for t in range(T)])
        prod["math_flooded_veg_km2"] = torch.tensor([((md[t] == FLOODED_VEG) & st.valid[t]).sum().item() * st.km2_per_px
                                                     for t in range(T)])
        prod["water_largest_body_km2"] = torch.tensor([largest_cc_px((seg[t] == WATER).numpy()) * st.km2_per_px
                                                       for t in range(T)])
    torch.save(prod, out / "products.pt")
    print(f"saved {out / 'products.pt'}   ({elapsed:.1f}s on {dev}, weights from {wa})")
    print(f"reference-date confident water: {core.sum().item() * st.km2_per_px:.0f} km2")
    print(f"{'date':<12}{'water km2':>11}{'flooded-veg km2':>17}{'rules water':>13}{'rules fl-veg':>14}"
          f"{'net keeps ref water':>21}{'rules keep':>12}{'net water elsewhere':>21}")
    for t in range(T):
        print(f"{st.dates[t]:<12}{water[t]:>11.1f}{fveg[t]:>17.1f}{r_water[t]:>13.1f}{r_fveg[t]:>14.1f}"
              f"{core_kept[t]:>21.3f}{rules_kept[t]:>12.3f}{water_outside[t]:>21.1f}")

    # figures
    cols = 5
    rows = math.ceil(T / cols)
    step = 2 if H > 600 else 1
    fig, axs = plt.subplots(rows, cols, figsize=(3.0 * cols, 3.0 * rows * H / W + 0.6))
    for t, a in enumerate(axs.flat):
        a.axis("off")
        if t < T:
            a.imshow(seg_for_plot(seg[t])[::step, ::step], cmap=seg_cmap(), vmin=-0.5, vmax=3.5, interpolation="nearest")
            a.set_title(st.dates[t], fontsize=9)
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in SEG_COLORS]
    fig.legend(handles, CLASS_NAMES, loc="lower center", ncol=3)
    fig.suptitle(f"U-Net segmentation per date: {args.area} (weights: {wa})")
    savefig(fig, f"unet_seg_dates_{args.area}.png")

    fig, ax = plt.subplots(figsize=(8, 4))
    dts = as_dates(st.dates)
    shade_gap(ax)
    ax.plot(dts, water, "o-", color=SEG_COLORS[0], label="open water: U-Net")
    ax.plot(dts, r_water, "o:", color=SEG_COLORS[0], alpha=0.6, label="open water: rules (confident only)")
    ax.plot(dts, fveg, "s-", color=SEG_COLORS[1], label="flooded vegetation: U-Net")
    ax.plot(dts, r_fveg, "s:", color=SEG_COLORS[1], alpha=0.6, label="flooded veg: rules (confident only)")
    ax.set(ylabel="km²", title=f"Area per date: {args.area} (uncalibrated previews, not validated)")
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%d %b"))
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    savefig(fig, f"area_vs_date_{args.area}.png")


# ----------------------------------------------------------------------------- sweep
def leaderboard(model: str, area: str, names: list[str], wall: dict, total: float) -> str:
    rows = []
    for v in names:
        f = RUNS / f"{model}_{area}_{v}.json"
        if not f.exists():
            rows.append((v, None))
            continue
        rows.append((v, json.loads(f.read_text())))
    ok = [r for r in rows if r[1]]
    if model == "unet":
        ok.sort(key=lambda r: -r[1]["heldout_miou"])
        head = ("| rank | version | params | wall s | final train CE | IoU water | IoU flooded-veg | IoU other | "
                "mean IoU | not-sure -> water / fl-veg / other (east) |\n|" + "---|" * 10)
        lines = [head]
        for i, (v, m) in enumerate(ok, 1):
            iou = list(m["heldout_iou"].values())
            fill = list(m["ignored_fill_east"].values())
            lines.append(f"| {i} | {v} | {m['params']:,} | {wall.get(v, m['train_time_s']):.0f} | "
                         f"{m['final_train_loss']:.4f} | {iou[0]:.3f} | {iou[1]:.3f} | {iou[2]:.3f} | "
                         f"**{m['heldout_miou']:.3f}** | {fill[0]:.2f} / {fill[1]:.2f} / {fill[2]:.2f} |")
        note = ("Metric = agreement with the physics-rule weak labels on the eastern 30 % hold-out, "
                "NOT accuracy against ground truth (there is none).")
    else:
        ok.sort(key=lambda r: r[1]["heldout_recon_mse"])
        lines = ["| rank | version | params | wall s | final train MSE | held-out recon MSE | k | non-empty clusters |",
                 "|" + "---|" * 8]
        for i, (v, m) in enumerate(ok, 1):
            lines.append(f"| {i} | {v} | {m['params']:,} | {wall.get(v, m['train_time_s']):.0f} | "
                         f"{m['final_train_loss']:.5f} | **{m['heldout_recon_mse']:.5f}** | {m['args']['k']} | "
                         f"{m['nonempty_clusters']} |")
        note = ("Metric = reconstruction error on eastern-30 % pixels (self-supervised), NOT accuracy; "
                "lower only means the embedding keeps more of the series. k does not change it.")
    failed = [v for v, m in rows if not m]
    md = (f"# Sweep: {model} on {area}\n\n{note}\n\nWall s = wall time of the whole subprocess "
          f"(load + train + inference), runs sharing the GPU in parallel. Total sweep wall time: {total:.0f} s.\n\n"
          + "\n".join(lines) + "\n")
    if failed:
        md += f"\nFailed versions (see data/processed/models/runs/*.log): {', '.join(failed)}\n"
    return md


def leaderboard_v2(names, wall, total) -> str:
    ok = []
    for v in names:
        f = RUNS / f"unet_tonle-sap_{v}.json"
        if f.exists():
            ok.append((v, json.loads(f.read_text())))
    for _, m in ok:
        m["score"] = v2_score(m["eval"])  # recomputed so older runs use the current definition
    ok.sort(key=lambda r: -r[1]["score"])
    if ok:
        MODELS.mkdir(parents=True, exist_ok=True)
        BEST_FILE.write_text(ok[0][0] + "\n")
    lines = ["| rank | version | params | wall s | train s | Tonle Sap held-out IoU water / fl-veg / other | "
             "Sevan held-out IoU water / fl-veg / other | score | Sevan largest body MAE vs 1242 km² |",
             "|" + "---|" * 9]
    for i, (v, m) in enumerate(ok, 1):
        e = m["eval"]
        f = lambda h: " / ".join(f"{x:.3f}" for x in h["iou"].values())  # noqa: E731
        lines.append(f"| {i} | {v} | {m['params']:,} | {wall.get(v, 0):.0f} | {m['train_time_s']:.0f} | "
                     f"{f(e['tonle-sap']['heldout'])} | {f(e['sevan']['heldout'])} | **{m['score']:.3f}** | "
                     f"{m['sevan_lcc_mae_km2']:.0f} |")
    failed = [v for v in names if v not in dict(ok)]
    md = ("# Sweep: U-Net v2 (labels from the math detector)\n\nScore = mean of (Tonle Sap east-30 % held-out mean IoU, "
          "3 classes) and (Sevan east-third held-out mean IoU over water and other land; the math detector finds "
          "~no flooded vegetation at Sevan). It is AGREEMENT WITH ANOTHER ALGORITHM (the math detector), not "
          "accuracy. Flooded-veg IoU on Sevan is NaN/0 when neither has any. Wall s = whole subprocess with 3 runs "
          f"sharing the GPU. Total sweep wall time {total:.0f} s. Best -> `{BEST_FILE.name}` "
          f"(`{ok[0][0] if ok else '-'}`).\n\n" + "\n".join(lines) + "\n")
    if failed:
        md += f"\nFailed versions (see data/processed/models/runs/*.log): {', '.join(failed)}\n"
    return md


def cmd_sweep(args):
    if args.versions == "v2":
        names = [v for v in VERSIONS[args.model] if v.startswith("v2")]
    elif args.versions:
        names = args.versions.split(",")
    else:
        names = [v for v in VERSIONS[args.model] if not v.startswith("v2")]  # the v1 sweep as before
    RUNS.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONUNBUFFERED="1")  # live per-version logs
    queue, running, wall, codes = list(names), {}, {}, {}
    t0 = time.time()
    print(f"sweep {args.model} on {args.area}: {len(names)} versions, {args.workers} at once")
    while queue or running:
        while queue and len(running) < args.workers:
            v = queue.pop(0)
            cmd = [sys.executable, "-m", "scripts.detect.train", args.model, "--area", args.area,
                   "--version", v, "--seed", str(args.seed)]
            if args.epochs:
                cmd += ["--epochs", str(args.epochs)]
            if args.device:
                cmd += ["--device", args.device]
            log = open(RUNS / f"{args.model}_{args.area}_{v}.log", "w")
            stale = RUNS / f"{args.model}_{args.area}_{v}.json"
            if stale.exists():
                stale.unlink()
            running[v] = (subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=env), log,
                          time.time())
            print(f"  started {v}")
        time.sleep(1.0)
        for v, (pr, log, ts) in list(running.items()):
            if pr.poll() is not None:
                log.close()
                wall[v], codes[v] = time.time() - ts, pr.returncode
                print(f"  finished {v}: exit {pr.returncode}, {wall[v]:.0f}s"
                      + ("" if pr.returncode == 0 else f"  (see {log.name})"))
                del running[v]
    total = time.time() - t0
    if all(v.startswith("v2") for v in names):
        md = leaderboard_v2(names, wall, total)
    else:
        md = leaderboard(args.model, args.area, names, wall, total)
    FIGS.mkdir(parents=True, exist_ok=True)
    out = FIGS / (f"sweep_{args.model}_v2.md" if all(v.startswith("v2") for v in names)
                  else f"sweep_{args.model}_{args.area}.md")
    out.write_text(md)
    print(md)
    print(f"leaderboard written to {out.relative_to(ROOT)}; total sweep wall time {total:.0f}s")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("ae", "unet", "predict"):
        p = sub.add_parser(name)
        p.add_argument("--area", default="tonle-sap")
        p.add_argument("--epochs", type=int, default={"ae": 6, "unet": 10}.get(name, 1))
        p.add_argument("--device", default=None, help="mps / cpu (default: MPS when available)")
        p.add_argument("--seed", type=int, default=0)
        if name == "ae":
            p.add_argument("--pixels", type=int, default=400_000)
            p.add_argument("--batch", type=int, default=4096)
            p.add_argument("--hidden", type=int, default=48)
            p.add_argument("--emb", type=int, default=8)
            p.add_argument("--k", type=int, default=8)
            p.add_argument("--encoder", default="gru", choices=["gru", "lstm", "conv"])
        if name == "unet":
            p.add_argument("--steps", type=int, default=120, help="batches per epoch")
            p.add_argument("--batch", type=int, default=16)
            p.add_argument("--patch", type=int, default=128)
            p.add_argument("--base", type=int, default=16)
            p.add_argument("--depth", type=int, default=3)
            p.add_argument("--no-ref", action="store_true", help="drop the dry-season reference channels")
            p.add_argument("--no-class-weights", action="store_true")
            p.add_argument("--v2", action="store_true", help="v2: train on math-detector labels, several scenes")
            p.add_argument("--temporal", action="store_true", help="v2: add season min/mean/std of HH channels")
            p.add_argument("--scenes", default=None, help="v2: comma list (default tonle-sap,sevan,ararat)")
            p.add_argument("--aug-noise", action="store_true", help="v2: speckle + gain augmentation")
            p.add_argument("--band-px", type=int, default=1, help="v2: ignore band around class boundaries")
        if name in ("ae", "unet"):
            p.add_argument("--version", default=None, choices=list(VERSIONS[name]),
                           help="named setting from VERSIONS; weights get a _<version> suffix")
        if name == "predict":
            p.add_argument("--weights-area", default=None, help="area the weights were trained on (default: --area)")
            p.add_argument("--version", default=None, help="U-Net version to load (default: unversioned weights)")
            p.add_argument("--ae-version", default=None, help="autoencoder version to load")
    p = sub.add_parser("sweep", help="train several versions in parallel subprocesses")
    p.add_argument("--model", required=True, choices=["unet", "ae"])
    p.add_argument("--area", default="tonle-sap")
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--epochs", type=int, default=None)
    p.add_argument("--device", default=None)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--versions", default=None, help="comma list (default: all in VERSIONS)")
    p = sub.add_parser("robust", help="v2: speckle / gain robustness, network vs math detector")
    p.add_argument("--versions", default=None, help="comma list of v2 versions (default: best)")
    p.add_argument("--device", default=None)
    p = sub.add_parser("figs_v2", help="v2: comparison maps and area-vs-date figures")
    p.add_argument("--version", default=None)
    p.add_argument("--device", default=None)
    args = ap.parse_args()
    t0 = time.time()
    {"ae": cmd_ae, "unet": cmd_unet, "predict": cmd_predict, "sweep": cmd_sweep, "robust": cmd_robust,
     "figs_v2": cmd_figs_v2}[args.cmd](args)
    print(f"total wall time {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
