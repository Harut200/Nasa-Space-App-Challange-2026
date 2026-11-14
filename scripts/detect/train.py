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
from scripts.detect.weak_labels import CLASS_NAMES, FLOODED_VEG, IGNORE, OTHER, WATER, smooth, weak_labels

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
VERSIONS = {
    "unet": {
        "base16_d3":       {"base": 16, "depth": 3},                       # the default model
        "base32_d3":       {"base": 32, "depth": 3},                       # wider
        "base16_d2":       {"base": 16, "depth": 2},                       # shallower (less context)
        "no_ref":          {"base": 16, "depth": 3, "no_ref": True},       # only this date's HH/HV
        "no_weights":      {"base": 16, "depth": 3, "no_class_weights": True},
        "base32_d2_noref": {"base": 32, "depth": 2, "no_ref": True},
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
    ax.set(xlabel="epoch", ylabel="masked MSE (brightness$^2$)", title=f"Temporal autoencoder — {args.area}")
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
        ax.set_title(f"[{c}] {frac[c] * 100:.0f}% — {names[c]}", fontsize=8, color=cmap(c), fontweight="bold")
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
    ax.set_title(f"Autoencoder reconstruction error — {args.area}\n(bright = season unlike the typical pixel; "
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


# ----------------------------------------------------------------------------- predict
def load_models(weights_area: str, dev, unet_version=None, ae_version=None):
    us = f"_{unet_version}" if unet_version else ""
    as_ = f"_{ae_version}" if ae_version else ""
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
    wa = args.weights_area or args.area
    t0 = time.time()
    st = load_stack(args.area)
    unet, ck_u, ae, ck_a = load_models(wa, dev, args.version, args.ae_version)
    T, _, H, W = st.x.shape
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
            "weights_area": wa, "source": st.source}
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
    fig.suptitle(f"U-Net segmentation per date — {args.area} (weights: {wa})")
    savefig(fig, f"unet_seg_dates_{args.area}.png")

    fig, ax = plt.subplots(figsize=(8, 4))
    dts = as_dates(st.dates)
    shade_gap(ax)
    ax.plot(dts, water, "o-", color=SEG_COLORS[0], label="open water — U-Net")
    ax.plot(dts, r_water, "o:", color=SEG_COLORS[0], alpha=0.6, label="open water — rules (confident only)")
    ax.plot(dts, fveg, "s-", color=SEG_COLORS[1], label="flooded vegetation — U-Net")
    ax.plot(dts, r_fveg, "s:", color=SEG_COLORS[1], alpha=0.6, label="flooded veg — rules (confident only)")
    ax.set(ylabel="km²", title=f"Area per date — {args.area} (uncalibrated previews, not validated)")
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


def cmd_sweep(args):
    names = args.versions.split(",") if args.versions else list(VERSIONS[args.model])
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
    md = leaderboard(args.model, args.area, names, wall, total)
    FIGS.mkdir(parents=True, exist_ok=True)
    out = FIGS / f"sweep_{args.model}_{args.area}.md"
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
    args = ap.parse_args()
    t0 = time.time()
    {"ae": cmd_ae, "unet": cmd_unet, "predict": cmd_predict, "sweep": cmd_sweep}[args.cmd](args)
    print(f"total wall time {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
