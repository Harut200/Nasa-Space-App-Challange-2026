"""A small transformer over TIME for radar pixel series, trained by hiding dates.

Each pixel's season is a sequence of tokens, one per pass. A token is that pass's
filtered HH, HV, HH-HV (optionally plus the k x k neighbourhood mean / standard deviation)
plus a continuous-time encoding of the day of year. Hidden dates are replaced by a learned
mask token (at their true day), and the network predicts the hidden values with a mean
and a log-variance (Gaussian negative log-likelihood). Because time is a continuous input,
the trained model can be queried on ANY day, e.g. inside the 27 Jul - 10 Aug 2026 outage.

    .venv/bin/python -m scripts.detect.ttransformer train --area tonle-sap [--variant base] [--epochs 4] [--context 0|3|5]
    .venv/bin/python -m scripts.detect.ttransformer evaluate --area tonle-sap [--variant base] [--strict]
    .venv/bin/python -m scripts.detect.ttransformer predict-gap --area tonle-sap|sevan|ararat [--weights-area tonle-sap]
    .venv/bin/python -m scripts.detect.ttransformer figures

Values are the detector's filtered, normalised log-like intensity L (ops.run_pipeline):
preview display brightness 0..1, NOT calibrated backscatter. No labels are used anywhere.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import time

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from scripts.detect import ops
from scripts.detect.stack import ROOT, device as auto_device, load_preview_stack

MODELS = ROOT / "data" / "processed" / "models"
FIGS = ROOT / "docs" / "eda" / "ttransformer"
HOLDOUT_FRAC = 0.3          # eastern 30 % of columns = evaluation pixels (same split as train.py)
GAP = ("2026-07-27", "2026-08-10")
PERIODS = (6.0, 12.0, 24.0, 48.0, 96.0, 192.0, 384.0)   # days, sinusoidal time encoding
DAY0, DAYSPAN = 170.0, 120.0                            # normalised day (d - DAY0) / DAYSPAN as an extra feature

# model variants: name -> settings (compare two or three with `train --variant ...`)
VARIANTS = {
    "base":   {"d": 48, "layers": 3, "heads": 4, "ff": 96, "dropout": 0.05, "context": 0, "var_head": True},
    "ctx5":   {"d": 48, "layers": 3, "heads": 4, "ff": 96, "dropout": 0.05, "context": 5, "var_head": True},
    "novar":  {"d": 48, "layers": 3, "heads": 4, "ff": 96, "dropout": 0.05, "context": 0, "var_head": False},
    "ctx5res": {"d": 48, "layers": 3, "heads": 4, "ff": 96, "dropout": 0.05, "context": 5, "var_head": True, "residual": True},
    "baseres": {"d": 48, "layers": 3, "heads": 4, "ff": 96, "dropout": 0.05, "context": 0, "var_head": True, "residual": True},
    "small":  {"d": 32, "layers": 2, "heads": 4, "ff": 64, "dropout": 0.05, "context": 0, "var_head": True},
}
TRAIN = {"pixels": 300_000, "epochs": 4, "batch": 2048, "lr": 2e-3, "wd": 1e-4, "seed": 0}


# ============================================================ time + data helpers
def doy(d: str) -> float:
    return float(dt.date.fromisoformat(d).timetuple().tm_yday)


def doy_str(x: float) -> str:
    return (dt.date(2026, 1, 1) + dt.timedelta(days=float(x) - 1)).strftime("%d %b")


def time_features(day: torch.Tensor) -> torch.Tensor:
    """Continuous-time encoding: [sin(2 pi d/P), cos(2 pi d/P) for P in PERIODS] + (d - DAY0)/DAYSPAN."""
    p = torch.tensor(PERIODS, device=day.device, dtype=torch.float32)
    ang = 2 * math.pi * day[..., None].float() / p
    return torch.cat([ang.sin(), ang.cos(), ((day.float() - DAY0) / DAYSPAN)[..., None]], -1)


def token_features(L: torch.Tensor, valid: torch.Tensor, context: int) -> torch.Tensor:
    """L [T,2,H,W], valid [T,H,W] -> per-date features [T,F,H,W]: HH, HV, HH-HV (+ k x k mean, sd of HH, HV)."""
    f = [L[:, 0], L[:, 1], L[:, 0] - L[:, 1]]
    if context and context > 1:
        m = ops.box_mean(L, valid, context)
        m2 = ops.box_mean(L * L, valid, context)
        sd = (m2 - m * m).clamp_min(0).sqrt()
        f += [m[:, 0], m[:, 1], sd[:, 0], sd[:, 1]]
    return torch.stack(f, 1) * valid[:, None]


def n_feat(context: int) -> int:
    return 7 if context and context > 1 else 3


def prepare(area: str, dev) -> dict:
    """Detector pipeline on all dates -> L, water, region (valid on every date), classes, east hold-out."""
    from scripts.detect.terrain import cached_water_possible
    st = load_preview_stack(area)
    water_ok = cached_water_possible(area, tuple(st.x.shape[-2:]))
    out = ops.run_pipeline(st.x, st.valid, st.source, dev=dev, water_ok=water_ok)
    out = {k: (v.cpu() if torch.is_tensor(v) else v) for k, v in out.items()}
    _free()
    vall = out["valid"].all(0)
    H, W = vall.shape
    east = torch.zeros(H, W, dtype=torch.bool, device=vall.device)
    east[:, int(W * (1 - HOLDOUT_FRAC)):] = True
    cls = out["classes"]
    changing = (cls >= 2) & vall     # seasonal open water, flooded vegetation, other lasting change
    return {"area": area, "st": st, "dates": st.dates, "days": torch.tensor([doy(d) for d in st.dates]),
            "L": out["L"], "water": out["water"], "vall": vall, "east": east, "changing": changing,
            "classes": cls, "km2": st.km2_per_px, "bounds": st.bounds, "kn": out["knobs"],
            "water_ok": None if water_ok is None else water_ok.cpu()}


def _free():
    import gc
    gc.collect()
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()


def pixel_series(feat: torch.Tensor, idx: torch.Tensor) -> torch.Tensor:
    """feat [T,F,H,W] -> [N,T,F] for flat pixel indices idx."""
    T, Fn = feat.shape[:2]
    return feat.reshape(T, Fn, -1)[:, :, idx].permute(2, 0, 1).contiguous()


# ============================================================ model
class Block(nn.Module):
    """Pre-norm transformer encoder block with explicit attention weights (for the attention figure)."""

    def __init__(self, d, heads, ff, dropout):
        super().__init__()
        self.h = heads
        self.n1, self.n2 = nn.LayerNorm(d), nn.LayerNorm(d)
        self.qkv, self.proj = nn.Linear(d, 3 * d), nn.Linear(d, d)
        self.ff = nn.Sequential(nn.Linear(d, ff), nn.GELU(), nn.Dropout(dropout), nn.Linear(ff, d))
        self.drop = nn.Dropout(dropout)

    def forward(self, x, pad=None):
        B, N, D = x.shape
        q, k, v = self.qkv(self.n1(x)).reshape(B, N, 3, self.h, D // self.h).permute(2, 0, 3, 1, 4)
        s = q @ k.transpose(-1, -2) / math.sqrt(D // self.h)          # [B,h,N,N]
        if pad is not None:
            s = s.masked_fill(pad[:, None, None, :], float("-inf"))
        a = s.softmax(-1)
        y = (self.drop(a) @ v).transpose(1, 2).reshape(B, N, D)
        x = x + self.drop(self.proj(y))
        x = x + self.drop(self.ff(self.n2(x)))
        return x, a


class TTransformer(nn.Module):
    """x [B,N,F] token features, day [B,N] day of year, query [B,N] bool (hidden / asked-for dates),
    pad [B,N] bool (slots that do not exist). Returns mu [B,N,2] (HH, HV) and log-variance [B,N,2]."""

    def __init__(self, n_in=3, d=48, layers=3, heads=4, ff=96, dropout=0.05, var_head=True, residual=False, **_):
        super().__init__()
        self.cfg = dict(n_in=n_in, d=d, layers=layers, heads=heads, ff=ff, dropout=dropout, var_head=var_head,
                        residual=residual)
        self.residual = residual
        self.inp = nn.Linear(n_in, d)
        if residual:   # query tokens also see the linear interpolation of their visible neighbours
            self.inp_q = nn.Linear(n_in + 1, d)
        self.time = nn.Sequential(nn.Linear(2 * len(PERIODS) + 1, d), nn.GELU(), nn.Linear(d, d))
        self.mask_tok = nn.Parameter(torch.randn(d) * 0.02)
        self.blocks = nn.ModuleList([Block(d, heads, ff, dropout) for _ in range(layers)])
        self.norm = nn.LayerNorm(d)
        self.mu = nn.Linear(d, 2)
        self.lv = nn.Linear(d, 2) if var_head else None
        self.register_buffer("f_mu", torch.zeros(n_in))
        self.register_buffer("f_sd", torch.ones(n_in))

    @staticmethod
    def interp_prior(x, day, vis):
        """Linear interpolation in time between the nearest VISIBLE earlier and later tokens, per slot
        (nearest one if only one side exists). Returns (prior [B,N,F], gap_days [B,N]). Uses no hidden value."""
        di, dk = day[:, :, None].float(), day[:, None, :].float()
        big = 1e4
        prev_d = torch.where(vis[:, None, :] & (dk < di), dk, torch.full_like(dk, -big))
        next_d = torch.where(vis[:, None, :] & (dk > di), dk, torch.full_like(dk, big))
        pa, pb = prev_d.max(-1), next_d.min(-1)
        has_a, has_b = pa.values > -big / 2, pb.values < big / 2
        Fn = x.shape[-1]
        xa = x.gather(1, pa.indices[..., None].expand(-1, -1, Fn))
        xb = x.gather(1, pb.indices[..., None].expand(-1, -1, Fn))
        da, db = pa.values, pb.values
        w = ((day - da) / (db - da).clamp_min(1e-3)).clamp(0, 1)
        w = torch.where(has_a & has_b, w, torch.where(has_a, torch.zeros_like(w), torch.ones_like(w)))
        prior = xa * (1 - w[..., None]) + xb * w[..., None]
        gap = torch.where(has_a & has_b, db - da, torch.full_like(da, 99.0))
        return prior, gap

    def forward(self, x, day, query, pad=None, attn=False):
        h = self.inp((x - self.f_mu) / self.f_sd)
        if self.residual:
            vis = ~query if pad is None else (~query & ~pad)
            prior, gap = self.interp_prior(x, day, vis)
            hq = self.mask_tok + self.inp_q(torch.cat([(prior - self.f_mu) / self.f_sd, (gap / 24.0)[..., None]], -1))
        else:
            hq = self.mask_tok.expand_as(h)
        h = torch.where(query[..., None], hq, h) + self.time(time_features(day))
        if pad is not None:
            h = h * (~pad)[..., None]
        att = []
        for b in self.blocks:
            h, a = b(h, pad)
            att.append(a)
        h = self.norm(h)
        mu = self.mu(h)
        if self.residual:
            mu = mu + prior[..., :2]     # predict a correction to linear interpolation
        lv = self.lv(h).clamp(-14, 3) if self.lv is not None else torch.full_like(mu, -6.0)
        return (mu, lv, att) if attn else (mu, lv)


def n_params(m):
    return sum(p.numel() for p in m.parameters())


def gaussian_nll(mu, lv, y, m):
    """0.5 * (log sigma^2 + (y - mu)^2 / sigma^2), averaged over masked entries (constant dropped)."""
    e = 0.5 * (lv + (y - mu) ** 2 * torch.exp(-lv))
    mm = m[..., None].float()
    return (e * mm).sum() / (mm.sum() * 2).clamp_min(1)


def masked_mse(mu, y, m):
    mm = m[..., None].float()
    return (((mu - y) ** 2) * mm).sum() / (mm.sum() * 2).clamp_min(1)


def sample_hidden(B, T, allowed: list[int], g: torch.Generator) -> torch.Tensor:
    """Hidden-date pattern per sample: 50 % one date, 25 % two random dates, 25 % two ADJACENT dates
    (so a hole twice the normal step is in the training distribution). Only `allowed` slots."""
    al = torch.tensor(allowed)
    hid = torch.zeros(B, T, dtype=torch.bool)
    mode = torch.rand(B, generator=g)
    r = torch.rand(B, al.numel(), generator=g)
    order = al[r.argsort(1)]
    hid[torch.arange(B), order[:, 0]] = True
    two = (mode >= 0.5) & (mode < 0.75)
    hid[torch.nonzero(two).squeeze(1), order[two, 1]] = True
    pairs = [(a, a + 1) for a in allowed if a + 1 in allowed]
    adj = torch.nonzero(mode >= 0.75).squeeze(1)
    if pairs and adj.numel():
        hid[adj] = False
        pp = torch.tensor(pairs)[torch.randint(len(pairs), (adj.numel(),), generator=g)]
        hid[adj, pp[:, 0]] = True
        hid[adj, pp[:, 1]] = True
    return hid


# ============================================================ training
def train_model(P: dict, var: dict, dev, exclude: list[int] = (), epochs=None, pixels=None, seed=0, log=True):
    """Masked-date training on west-70 % pixels. `exclude` = dates removed from training entirely
    (never seen as input or target: the strict test of an unobserved pass)."""
    torch.manual_seed(seed)
    g = torch.Generator().manual_seed(seed)
    epochs = epochs or TRAIN["epochs"]
    pixels = pixels or TRAIN["pixels"]
    T = len(P["dates"])
    ctx = var["context"]
    feat = token_features(P["L"], P["vall"][None].expand(T, -1, -1), ctx)
    region = P["vall"] & ~P["east"]
    idx = torch.nonzero(region.flatten()).squeeze(1).cpu()
    tr = idx[torch.randperm(idx.numel(), generator=g)[:pixels]]
    ev_idx = torch.nonzero((P["vall"] & P["east"]).flatten()).squeeze(1).cpu()
    va = ev_idx[torch.randperm(ev_idx.numel(), generator=g)[:20000]]
    X, Xv = pixel_series(feat, tr), pixel_series(feat, va).to(dev)   # training set stays on CPU
    del feat
    Y, Yv = X[..., :2].clone(), Xv[..., :2].clone()
    model = TTransformer(n_in=X.shape[-1], **var).to(dev)
    model.f_mu.copy_(X.reshape(-1, X.shape[-1]).mean(0))
    model.f_sd.copy_(X.reshape(-1, X.shape[-1]).std(0).clamp_min(1e-4))
    days = P["days"].to(dev)
    allowed = [i for i in range(T) if i not in exclude]
    pad_row = torch.zeros(T, dtype=torch.bool, device=dev)
    pad_row[list(exclude)] = True
    # validation protocol: one interior date hidden per pixel (fixed), plus linear interpolation on the same
    gv = torch.Generator().manual_seed(123)
    inter = [i for i in allowed if 0 < allowed.index(i) < len(allowed) - 1]
    kv = torch.tensor(inter)[torch.randint(len(inter), (va.numel(),), generator=gv)].to(dev)
    hv = F.one_hot(kv, T).bool()
    li_mse = _linear_val(Yv, kv, allowed, P["days"])
    opt = torch.optim.AdamW(model.parameters(), lr=TRAIN["lr"], weight_decay=TRAIN["wd"])
    B = TRAIN["batch"]
    steps = math.ceil(X.shape[0] / B) * epochs
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=TRAIN["lr"], total_steps=steps, pct_start=0.15)
    hist = {"train_loss": [], "train_mse": [], "val_mse": [], "val_nll": [], "epoch_s": [], "linear_val_mse": li_mse}
    t_all = time.time()
    for ep in range(epochs):
        t0 = time.time()
        model.train()
        perm = torch.randperm(X.shape[0], generator=g)
        tl = tm = 0.0
        nb = 0
        for i in range(0, X.shape[0], B):
            b = perm[i:i + B]
            hid = sample_hidden(b.numel(), T, allowed, g).to(dev)
            pad = pad_row.expand(b.numel(), -1)
            xb, yb = X[b].to(dev), Y[b].to(dev)
            mu, lv = model(xb, days.expand(b.numel(), -1), hid, pad)
            loss = gaussian_nll(mu, lv, yb, hid) if var["var_head"] else masked_mse(mu, yb, hid)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            tl += loss.item()
            tm += masked_mse(mu.detach(), yb, hid).item()
            nb += 1
        model.eval()
        with torch.no_grad():
            mu, lv = model(Xv, days.expand(va.numel(), -1), hv, pad_row.expand(va.numel(), -1))
            vm, vn = masked_mse(mu, Yv, hv).item(), gaussian_nll(mu, lv, Yv, hv).item()
        if dev.type == "mps":
            torch.mps.synchronize()
        es = time.time() - t0
        for k_, v_ in (("train_loss", tl / nb), ("train_mse", tm / nb), ("val_mse", vm), ("val_nll", vn), ("epoch_s", es)):
            hist[k_].append(v_)
        if log:
            print(f"  epoch {ep + 1}/{epochs}  loss {tl / nb:.4f}  train mse {tm / nb:.5f}  "
                  f"east val mse {vm:.5f} (linear interp {li_mse:.5f})  {es:.1f}s on {dev}")
    hist["train_s"] = time.time() - t_all
    del X, Y, Xv, Yv
    _free()
    model.eval()
    return model, hist


def _linear_val(Yv, kv, allowed, days):
    """MSE of linear interpolation between the nearest allowed neighbours of each hidden date kv."""
    days = days.to(Yv.device)
    al = torch.tensor(allowed, device=Yv.device)
    pos = torch.searchsorted(al, kv)
    a, b = al[pos - 1], al[pos + 1]
    w = ((days[kv] - days[a]) / (days[b] - days[a]))[:, None]
    ar = torch.arange(Yv.shape[0], device=Yv.device)
    pred = Yv[ar, a] * (1 - w) + Yv[ar, b] * w
    return float(((pred - Yv[ar, kv]) ** 2).mean())


def save_model(model, hist, var, name, extra=None):
    MODELS.mkdir(parents=True, exist_ok=True)
    p = MODELS / f"ttransformer_{name}.pt"
    torch.save({"state_dict": model.state_dict(), "cfg": model.cfg, "variant": var, "history": hist,
                "params": n_params(model), **(extra or {})}, p)
    return p


def load_model(name, dev):
    ck = torch.load(MODELS / f"ttransformer_{name}.pt", map_location="cpu", weights_only=False)
    m = TTransformer(**ck["cfg"])
    m.load_state_dict(ck["state_dict"])
    return m.to(dev).eval(), ck


# ============================================================ inference
@torch.no_grad()
def predict_image(model, feat_slots, days, query, pad, idx, dev, chunk=65536):
    """feat_slots [N_slots,F,H,W] features (any values at query slots), days [N_slots], query/pad [N_slots] bool,
    idx flat pixel indices -> mu, sd [N_q, 2, len(idx)] for the query slots."""
    q = torch.nonzero(query).squeeze(1)
    mus, sds = [], []
    d = days.to(dev)
    for i in range(0, idx.numel(), chunk):
        x = pixel_series(feat_slots, idx[i:i + chunk]).to(dev)
        B = x.shape[0]
        mu, lv = model(x, d.expand(B, -1), query.to(dev).expand(B, -1), pad.to(dev).expand(B, -1))
        mus.append(mu[:, q.to(dev)].permute(1, 2, 0).cpu())
        sds.append(torch.exp(0.5 * lv[:, q.to(dev)]).permute(1, 2, 0).cpu())
    return torch.cat(mus, -1), torch.cat(sds, -1)


@torch.no_grad()
def attention_from_queries(model, feat_slots, days, query, pad, idx, dev, n=20000):
    """Mean attention (all layers, all heads) from each query slot to every slot [N_q, N_slots]."""
    x = pixel_series(feat_slots, idx[:n]).to(dev)
    B = x.shape[0]
    _, _, att = model(x, days.to(dev).expand(B, -1), query.to(dev).expand(B, -1), pad.to(dev).expand(B, -1), attn=True)
    a = torch.stack(att).mean((0, 1, 2))   # [N, N] averaged over layers, pixels, heads
    q = torch.nonzero(query).squeeze(1)
    return a[q.to(a.device)].cpu()


def to_image(vals, idx, H, W, fill=0.0):
    """vals [..., N] at flat idx -> [..., H, W]."""
    out = torch.full((*vals.shape[:-1], H * W), fill, device=vals.device, dtype=vals.dtype)
    out[..., idx] = vals
    return out.reshape(*vals.shape[:-1], H, W)


def water_rule(Limg, region, kn, water_ok=None):
    """The detector's open-water rule (ops.water_masks: HH hysteresis strong 0.06 / weak 0.12, HV < 0.35,
    8-connected, specks < 4 px removed) applied to an image [2,H,W] or stack [N,2,H,W]."""
    one = Limg.dim() == 3
    L = Limg[None] if one else Limg
    dev = auto_device()
    w = ops.water_masks(L.float().to(dev), region.to(dev)[None].expand(L.shape[0], -1, -1), kn).cpu()
    if water_ok is not None:   # the detector's terrain mask (water impossible on steep / high ground)
        w = w & water_ok[None]
    return w[0] if one else w


# ============================================================ metrics
def boundary(m):
    er = -F.max_pool2d(-m.float()[None, None], 3, 1, 1)[0, 0] > 0
    return m & ~er


def mask_metrics(pred, true, region, km2):
    from scipy.ndimage import distance_transform_edt
    pr, tr = pred & region, true & region
    inter, union = (pr & tr).sum().item(), (pr | tr).sum().item()
    bp, bt = (boundary(pred) & region).cpu().numpy(), (boundary(true) & region).cpu().numpy()
    if bp.any() and bt.any():
        dp, dtt = distance_transform_edt(~bp), distance_transform_edt(~bt)
        mbd = 0.5 * (dtt[bp].mean() + dp[bt].mean()) * math.sqrt(km2)
    else:
        mbd = float("nan")
    return {"iou": inter / max(union, 1), "missed_km2": (tr & ~pr).sum().item() * km2,
            "false_km2": (pr & ~tr).sum().item() * km2, "area_err_km2": (pr.sum().item() - tr.sum().item()) * km2,
            "mean_boundary_km": float(mbd)}


def value_metrics(pred, truth, m):
    """pred, truth [2,H,W]; m [H,W] -> MAE / RMSE per channel and MSE (both channels)."""
    e = (pred - truth)[:, m]
    return {"mae_hh": e[0].abs().mean().item(), "mae_hv": e[1].abs().mean().item(),
            "rmse_hh": e[0].pow(2).mean().sqrt().item(), "rmse_hv": e[1].pow(2).mean().sqrt().item(),
            "mse": e.pow(2).mean().item(), "n": int(m.sum())}


def baselines(Lr, kept, k, days):
    """Predictions for hidden date k from the kept dates' (reduced pipeline) images Lr [len(kept),2,H,W]."""
    before = [j for j, i in enumerate(kept) if i < k]
    after = [j for j, i in enumerate(kept) if i > k]
    a, b = before[-1], after[0]
    da, db, dk = float(days[kept[a]]), float(days[kept[b]]), float(days[k])
    w = (dk - da) / (db - da)
    others = Lr.mean(0)
    return {"persistence": Lr[a], "next": Lr[b], "linear": Lr[a] * (1 - w) + Lr[b] * w, "mean_others": others}


# ============================================================ evaluation
def reduced_case(P, hidden, dev):
    """Re-run the detector's pre-processing WITHOUT the hidden dates (the Quegan filter and the
    normalisation mix dates, so the full-stack L would leak a little of the hidden pass)."""
    st = P["st"]
    T = len(P["dates"])
    kept = [i for i in range(T) if i not in hidden]
    out = ops.run_pipeline(st.x[kept], st.valid[kept], st.source, dev=dev, water_ok=P["water_ok"])
    region = P["vall"] & out["valid"].all(0).cpu()
    Lr = out["L"].cpu()
    del out
    _free()
    return kept, Lr, region


def ae_baseline(area, cases, dev):
    """The GRU TemporalAE (train.py, ae_<area>.pt) queried for a hidden date: that date's features
    and valid flag are zeroed (the AE was trained with 10 % random date dropout and a loss on all
    dates, so it does fill dropped dates). It lives in its own value space (3x3-smoothed raw
    brightness), so it is scored there against linear interpolation in the same space."""
    from scripts.detect.models import TemporalAE
    from scripts.detect.train import ae_features, day_offsets, load_stack
    p = MODELS / f"ae_{area}.pt"
    if not p.exists():
        return None
    ck = torch.load(p, map_location="cpu", weights_only=False)
    ae = TemporalAE(**ck["config"]).to(dev).eval()
    ae.load_state_dict(ck["state_dict"])
    st = load_stack(area)
    feats, target, mask, tt = ae_features(st)
    H, W = st.x.shape[-2:]
    cols = torch.arange(H * W) % W
    ok = mask.all(1) & (cols >= int(W * (1 - HOLDOUT_FRAC)))
    idx = torch.nonzero(ok).squeeze(1)
    idx = idx[torch.randperm(idx.numel(), generator=torch.Generator().manual_seed(0))[:200000]]
    days = day_offsets(st.dates)
    res = {}
    for hidden in cases:
        x = feats[idx].clone()
        x[:, hidden, 0:3] = 0.0   # HH, HV, HH-HV
        x[:, hidden, 4] = 0.0     # valid flag (the day feature stays)
        with torch.no_grad():
            rec = torch.cat([ae(x[i:i + 65536].to(dev), tt.to(dev).expand(min(65536, x.shape[0] - i), -1, -1))[0].cpu()
                             for i in range(0, x.shape[0], 65536)])
        y = target[idx]
        kept = [i for i in range(len(st.dates)) if i not in hidden]
        for k in hidden:
            if k == 0 or k == len(st.dates) - 1:
                continue
            a = max(i for i in kept if i < k)
            b = min(i for i in kept if i > k)
            w = float((days[k] - days[a]) / (days[b] - days[a]))
            li = y[:, a] * (1 - w) + y[:, b] * w
            res[(tuple(hidden), k)] = {"ae_mse": float(((rec[:, k] - y[:, k]) ** 2).mean()),
                                       "linear_mse": float(((li - y[:, k]) ** 2).mean())}
    return res


def evaluate(P, model, var, dev, strict=False, strict_epochs=3, cases=None, strict_pixels=100_000):
    """Leave-one-date-out (each interior date) and double-gap (each adjacent interior pair) tests.
    All numbers on the east 30 % hold-out, pixels valid on every date."""
    T = len(P["dates"])
    H, W = P["vall"].shape
    km2 = P["km2"]
    days = P["days"]
    if cases is None:
        cases = [("single", [k]) for k in range(1, T - 1)] + [("double", [k, k + 1]) for k in range(1, T - 2)]
    ctx = var["context"]
    novel = None
    try:
        from scripts.detect import novel as _nv
        novel = _nv
    except Exception:
        pass
    rows, attn_rows = [], []
    # cache: reduced-pipeline images and baseline metrics do not depend on the model
    cpath = MODELS / f"ttransformer_cache_{P['area']}.pt"
    cache = torch.load(cpath, weights_only=False) if cpath.exists() else {"reduced": {}, "base": {}}
    dirty = False
    for kind, hidden in cases:
        t0 = time.time()
        key = tuple(hidden)
        kept, Lr, region = reduced_case(P, hidden, dev)   # (not cached: ~40 MB per case)
        ev = region & P["east"]
        idx = torch.nonzero(region.flatten()).squeeze(1)
        m = model
        if strict:
            m, _ = train_model(P, var, dev, exclude=hidden, epochs=strict_epochs, pixels=strict_pixels, log=False)
        # slots: all T dates; kept ones carry reduced-pipeline features, hidden ones are queries
        fr = token_features(Lr, region[None].expand(len(kept), -1, -1), ctx)
        slots = torch.zeros(T, fr.shape[1], H, W, device=fr.device)
        slots[kept] = fr
        query = torch.zeros(T, dtype=torch.bool)
        query[hidden] = True
        mu, sd = predict_image(m, slots, days, query, torch.zeros(T, dtype=torch.bool), idx, dev)
        mu_img, sd_img = to_image(mu, idx, H, W), to_image(sd, idx, H, W)
        if kind == "single" and len(attn_rows) < T:
            attn_rows.append((hidden[0], attention_from_queries(m, slots, days, query, torch.zeros(T, dtype=torch.bool),
                                                                 torch.nonzero(ev.flatten()).squeeze(1), dev)[0]))
        for j, k in enumerate(hidden):
            if not (any(i < k for i in kept) and any(i > k for i in kept)):
                continue
            truth = P["L"][k]
            bl = baselines(Lr, kept, k, days)
            cached = cache["base"].get((key, k))
            preds = {"transformer": mu_img[j], **({} if cached else bl)}
            r = {"kind": kind, "hidden": hidden, "date": P["dates"][k], "k": k,
                 "gap_days": float(days[min(i for i in kept if i > k)] - days[max(i for i in kept if i < k)]),
                 "methods": {}}
            tw = P["water"][k] & region
            if cached:
                r["methods"].update(cached)
            for nm, pimg in preds.items():
                vm_all = value_metrics(pimg, truth, ev)
                vm_ch = value_metrics(pimg, truth, ev & P["changing"])
                pw = water_rule(pimg, region, P["kn"], P["water_ok"])
                r["methods"][nm] = {"all": vm_all, "changing": vm_ch, "water": mask_metrics(pw, tw, ev, km2)}
            if novel is not None and not cached:   # physics-style flood front between the neighbouring shorelines
                A = novel.arrival_time(P["water"][kept].to(dev), [P["dates"][i] for i in kept],
                                       P["vall"][None].expand(len(kept), -1, -1).to(dev), km2)
                front = ((A["base"] | (A["arrival_day"] <= float(days[k]))).cpu() & P["vall"])
                del A
                r["methods"]["front (novel.py)"] = {"water": mask_metrics(front, tw, ev, km2)}
            if not cached:
                cache["base"][(key, k)] = {m_: v_ for m_, v_ in r["methods"].items() if m_ != "transformer"}
                dirty = True
            preds["linear"] = bl["linear"]
            # calibration of the predicted sd (east pixels)
            z = ((truth - mu_img[j]) / sd_img[j].clamp_min(1e-6))[:, ev]
            r["calib"] = {"frac_within_1sd": float((z.abs() < 1).float().mean()),
                          "frac_within_1.645sd": float((z.abs() < 1.645).float().mean()),
                          "mean_pred_sd": float(sd_img[j][:, ev].mean()), "rmse": float((truth - mu_img[j])[:, ev].pow(2).mean().sqrt())}
            # reliability: binned predicted sd vs actual RMSE (both channels pooled)
            sdv = sd_img[j][:, ev].flatten().cpu()
            err = (truth - mu_img[j])[:, ev].flatten().cpu()
            qs = torch.quantile(sdv[::max(1, sdv.numel() // 100000)], torch.linspace(0, 1, 11))
            rel = []
            for a_, b_ in zip(qs[:-1], qs[1:]):
                mm = (sdv >= a_) & (sdv <= b_)
                if mm.any():
                    rel.append((float(sdv[mm].pow(2).mean().sqrt()), float(err[mm].pow(2).mean().sqrt())))
            r["calib"]["reliability"] = rel
            # area band from the predicted sd (shared z across the image), as used for the outage
            r["area_band"] = {}
            for zz in (-1.645, 0.0, 1.645):
                pw = water_rule(mu_img[j] + zz * sd_img[j], region, P["kn"], P["water_ok"])
                r["area_band"][str(zz)] = float((pw & region).sum()) * km2
            r["area_true_km2"] = float(tw.sum()) * km2
            r["area_linear_km2"] = float((water_rule(preds["linear"], region, P["kn"], P["water_ok"]) & region).sum()) * km2
            if kind == "single" and k == 5:     # keep one example image for the figure
                r["_example"] = {"truth": truth.cpu(), "pred": mu_img[j].cpu(), "linear": preds["linear"].cpu(),
                                 "sd": sd_img[j].cpu(), "water_true": tw.cpu(),
                                 "water_pred": water_rule(mu_img[j], region, P["kn"], P["water_ok"]).cpu(),
                                 "water_linear": water_rule(preds["linear"], region, P["kn"], P["water_ok"]).cpu()}
            rows.append(r)
        del Lr, fr, slots, mu, sd, mu_img, sd_img
        _free()
        print(f"  {kind} {[P['dates'][i] for i in hidden]}  {time.time() - t0:.1f}s", flush=True)
    if dirty:
        torch.save(cache, cpath)
    return rows, attn_rows


def summarise(rows, key="all"):
    """Mean over dates of MSE / MAE per method, and skill vs linear = 1 - MSE_m / MSE_linear."""
    out = {}
    for kind in ("single", "double"):
        rr = [r for r in rows if r["kind"] == kind]
        if not rr:
            continue
        meths = [m for m in rr[0]["methods"] if key in rr[0]["methods"][m]]
        s = {}
        for m in meths:
            mse = float(np.mean([r["methods"][m][key]["mse"] for r in rr]))
            lin = float(np.mean([r["methods"]["linear"][key]["mse"] for r in rr]))
            s[m] = {"rmse": math.sqrt(mse), "mae_hh": float(np.mean([r["methods"][m][key]["mae_hh"] for r in rr])),
                    "mae_hv": float(np.mean([r["methods"][m][key]["mae_hv"] for r in rr])),
                    "rmse_hh": float(np.mean([r["methods"][m][key]["rmse_hh"] for r in rr])),
                    "rmse_hv": float(np.mean([r["methods"][m][key]["rmse_hv"] for r in rr])),
                    "skill_vs_linear": 1 - mse / lin}
        out[kind] = s
    return out


def summarise_water(rows):
    out = {}
    for kind in ("single", "double"):
        rr = [r for r in rows if r["kind"] == kind]
        if not rr:
            continue
        out[kind] = {m: {q: float(np.nanmean([r["methods"][m]["water"][q] for r in rr]))
                         for q in ("iou", "missed_km2", "false_km2", "mean_boundary_km")}
                     | {"abs_area_err_km2": float(np.mean([abs(r["methods"][m]["water"]["area_err_km2"]) for r in rr]))}
                     for m in rr[0]["methods"]}
    return out


# ============================================================ outage prediction
def gap_query_days(P):
    days = P["days"].tolist()
    g0, g1 = doy(GAP[0]), doy(GAP[1])
    a = max(i for i, d in enumerate(days) if d < g0)
    b = a + 1
    da, db = days[a], days[b]
    return a, b, [da + 5, (da + db) / 2, db - 6]


@torch.no_grad()
def predict_gap(P, model, var, dev, n_draws=0):
    T = len(P["dates"])
    H, W = P["vall"].shape
    region = P["vall"]
    km2 = P["km2"]
    a, b, gdays = gap_query_days(P)
    feat = token_features(P["L"], region[None].expand(T, -1, -1), var["context"])
    idx = torch.nonzero(region.flatten()).squeeze(1)
    curve_days = [float(v) for v in np.arange(float(P["days"][a]), float(P["days"][b]) + 0.1, 2.0)]
    all_days = gdays + [d for d in curve_days if d not in gdays]
    mus, sds = {}, {}
    for d in all_days:
        slots = torch.cat([feat, torch.zeros_like(feat[:1])])
        dd = torch.cat([P["days"].float(), torch.tensor([float(d)], dtype=torch.float32)])
        q = torch.zeros(T + 1, dtype=torch.bool)
        q[-1] = True
        mu, sd = predict_image(model, slots, dd, q, torch.zeros(T + 1, dtype=torch.bool), idx, dev)
        mus[d], sds[d] = to_image(mu[0], idx, H, W), to_image(sd[0], idx, H, W)
    res = {"gap_days": gdays, "curve": []}
    for d in all_days:
        area = {}
        for zz in (-1.645, 0.0, 1.645):
            area[zz] = float((water_rule(mus[d] + zz * sds[d], region, P["kn"], P["water_ok"]) & region).sum()) * km2
        res["curve"].append({"day": d, "date": doy_str(d), "km2": area[0.0], "km2_lo": min(area.values()), "km2_hi": max(area.values())})
    res["pred_gap"] = torch.stack([mus[d] for d in gdays])
    res["pred_gap_sd"] = torch.stack([sds[d] for d in gdays])
    res["water_gap"] = water_rule(res["pred_gap"], region, P["kn"], P["water_ok"]) & region
    cur = {c["day"]: c for c in res["curve"]}
    res["water_gap_km2"] = [cur[d]["km2"] for d in gdays]
    res["water_gap_km2_lo"] = [cur[d]["km2_lo"] for d in gdays]
    res["water_gap_km2_hi"] = [cur[d]["km2_hi"] for d in gdays]
    # linear interpolation of the neighbours, for comparison
    lin = []
    for d in gdays:
        w = (d - float(P["days"][a])) / float(P["days"][b] - P["days"][a])
        li = P["L"][a] * (1 - w) + P["L"][b] * w
        lin.append(li)
    lin = torch.stack(lin)
    res["water_linear"] = water_rule(lin, region, P["kn"], P["water_ok"]) & region
    res["water_linear_km2"] = [float(res["water_linear"][i].sum()) * km2 for i in range(len(gdays))]
    res["diff_vs_linear_mae"] = [float((res["pred_gap"][i] - lin[i])[:, region].abs().mean()) for i in range(len(gdays))]
    res["neighbour_km2"] = [float((P["water"][a] & region).sum()) * km2, float((P["water"][b] & region).sum()) * km2]
    res["neighbours"] = (a, b)
    # independent per-pixel sampling (spatially uncorrelated errors): spread of the area
    if n_draws:
        g = torch.Generator(device="cpu").manual_seed(0)
        ar = []
        for _ in range(n_draws):
            z = torch.randn(res["pred_gap"].shape, generator=g).to(res["pred_gap"].device)
            ar.append([float((water_rule(res["pred_gap"][i] + z[i] * res["pred_gap_sd"][i], region, P["kn"], P["water_ok"]) & region).sum()) * km2
                       for i in range(len(gdays))])
        ar = np.array(ar)
        res["water_gap_km2_indep_q05"] = np.quantile(ar, 0.05, 0).tolist()
        res["water_gap_km2_indep_q95"] = np.quantile(ar, 0.95, 0).tolist()
    # where the predicted new water is: in the newly-flooded-at-b band (south-east river plain?)
    newb = P["water"][b] & ~P["water"][a] & region
    res["new_band_km2"] = float(newb.sum()) * km2
    res["pred_new_in_band_km2"] = [float((res["water_gap"][i] & newb).sum()) * km2 for i in range(len(gdays))]
    res["pred_new_outside_band_km2"] = [float((res["water_gap"][i] & ~newb & ~P["water"][a]).sum()) * km2 for i in range(len(gdays))]
    return res


# ============================================================ figures
def _save(fig, name):
    import matplotlib.pyplot as plt
    FIGS.mkdir(parents=True, exist_ok=True)
    p = FIGS / name
    fig.savefig(p, dpi=90, bbox_inches="tight")
    plt.close(fig)
    if p.stat().st_size > 380_000:
        from PIL import Image
        Image.open(p).convert("RGB").quantize(256).save(p, optimize=True)
    print(f"  figure {p.relative_to(ROOT)} ({p.stat().st_size // 1024} KB)")


def make_figures(area="tonle-sap", variant="ctx5res", variants=("base", "ctx5", "ctx5res")):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    COL = {"transformer": "#c0392b", "linear": "#2c7fb8", "persistence": "#7f7f7f", "next": "#bdbdbd",
           "mean_others": "#e6ab02", "front (novel.py)": "#1b9e77"}
    # 1 training curves
    fig, ax = plt.subplots(figsize=(6, 3.6))
    for v, ls in zip(variants, ("-", "--", ":")):
        p = MODELS / f"ttransformer_{area}_{v}.pt"
        if not p.exists():
            continue
        h = torch.load(p, map_location="cpu", weights_only=False)["history"]
        e = range(1, len(h["val_mse"]) + 1)
        ax.plot(e, h["train_mse"], "o" + ls, color="0.5", ms=3, label=f"{v}: train (west 70 %)")
        ax.plot(e, h["val_mse"], "s" + ls, color="#c0392b", ms=3, label=f"{v}: east hold-out")
        lin = h["linear_val_mse"]
    ax.axhline(lin, color="#2c7fb8", lw=1.2, label="linear interpolation, same hidden dates")
    ax.set(yscale="log", xlabel="epoch", ylabel="MSE on hidden dates (brightness²)",
           title="Masked-date training (one interior date hidden per held-out pixel)")
    ax.legend(fontsize=7)
    _save(fig, "training_curve.png")

    ev_p = MODELS / f"ttransformer_eval_{area}_{variant}_strict.pt"
    if not ev_p.exists():
        ev_p = MODELS / f"ttransformer_eval_{area}_{variant}.pt"
    E = torch.load(ev_p, map_location="cpu", weights_only=False)
    rows = E["rows"]
    strict = "strict" in ev_p.name
    # 2 per-date errors: single and double gap, all pixels and changing pixels
    meths = ["transformer", "linear", "persistence", "next", "mean_others"]
    fig, axs = plt.subplots(2, 2, figsize=(12, 6.5), sharey="row")
    for c, kind in enumerate(("single", "double")):
        rr = [r for r in rows if r["kind"] == kind]
        if kind == "double":   # average the two partner cases per date
            dd = sorted({r["k"] for r in rr})
            rr = [{"k": k, "date": next(r["date"] for r in rr if r["k"] == k),
                   "methods": {m: {key: {"mse": float(np.mean([r["methods"][m][key]["mse"] for r in rr if r["k"] == k]))}
                                   for key in ("all", "changing")} for m in meths}} for k in dd]
        x = np.arange(len(rr))
        for rI, key in enumerate(("all", "changing")):
            ax = axs[rI, c]
            for j, m in enumerate(meths):
                ax.bar(x + (j - 2) * 0.16, [math.sqrt(r["methods"][m][key]["mse"]) for r in rr], 0.16, color=COL[m], label=m)
            ax.set_xticks(x)
            ax.set_xticklabels([doy_str(doy(r["date"])) for r in rr], fontsize=8)
            ax.set_ylabel(f"RMSE, {'all' if key == 'all' else 'changing'} east pixels")
            ax.set_title(("ONE date hidden (hole 24 d; 36 d at 25 Jul / 18 Aug)" if kind == "single"
                          else "DOUBLE gap: date + a neighbour hidden (36-48 d)") + (", all pixels" if rI == 0 else ", changing pixels"),
                         fontsize=9)
    axs[0, 0].legend(fontsize=7, ncol=3)
    fig.suptitle(f"Predicting a hidden pass, Tonle Sap east 30 % hold-out ({'model retrained without the hidden pass' if strict else 'model trained on all passes of the west 70 %'})", fontsize=10)
    _save(fig, "loo_error_per_date.png")

    # 3 example: real vs predicted vs linear for one left-out date
    ex = E.get("example")
    if ex is not None:
        k = 5
        fig, axs = plt.subplots(2, 4, figsize=(15, 7.2))
        H, W = ex["truth"].shape[-2:]
        c0 = int(W * (1 - HOLDOUT_FRAC))
        sl = (slice(None), slice(c0, None))
        kw = dict(cmap="gray", vmin=0, vmax=float(np.quantile(ex["truth"][0][sl].numpy(), 0.99)))
        axs[0, 0].imshow(ex["truth"][0][sl], **kw); axs[0, 0].set_title(f"observed HH {doy_str(doy(E['dates'][k]))} (hidden)")
        axs[0, 1].imshow(ex["pred"][0][sl], **kw); axs[0, 1].set_title("transformer prediction HH")
        axs[0, 2].imshow(ex["linear"][0][sl], **kw); axs[0, 2].set_title("linear interpolation HH")
        im = axs[0, 3].imshow((ex["pred"][0] - ex["truth"][0])[sl], cmap="RdBu_r", vmin=-0.15, vmax=0.15)
        axs[0, 3].set_title("transformer − observed (HH)")
        fig.colorbar(im, ax=axs[0, 3], shrink=0.7)
        def wcmp(ax, pred, true, title):
            rgb = np.ones((*true.shape, 3)) * 0.92
            t, p = true.numpy(), pred.numpy()
            rgb[t & p] = (0.15, 0.35, 0.8); rgb[t & ~p] = (0.95, 0.6, 0.1); rgb[~t & p] = (0.85, 0.1, 0.1)
            ax.imshow(rgb[sl]); ax.set_title(title, fontsize=9)
        r5 = next(r for r in rows if r["kind"] == "single" and r["k"] == k)
        wt = r5["methods"]["transformer"]["water"]; wl = r5["methods"]["linear"]["water"]
        wcmp(axs[1, 0], ex["water_true"], ex["water_true"], "observed water (detector)")
        wcmp(axs[1, 1], ex["water_pred"], ex["water_true"], f"transformer water: IoU {wt['iou']:.3f}\nmissed {wt['missed_km2']:.0f} km² (orange), false {wt['false_km2']:.0f} km² (red)")
        wcmp(axs[1, 2], ex["water_linear"], ex["water_true"], f"linear-interp water: IoU {wl['iou']:.3f}\nmissed {wl['missed_km2']:.0f} km², false {wl['false_km2']:.0f} km²")
        im = axs[1, 3].imshow(ex["sd"][0][sl], cmap="magma", vmin=0, vmax=float(np.quantile(ex["sd"][0][sl].numpy(), 0.99)))
        axs[1, 3].set_title("predicted σ (HH)")
        fig.colorbar(im, ax=axs[1, 3], shrink=0.7)
        for a in axs.flat:
            a.set_xticks([]); a.set_yticks([])
        fig.suptitle(f"Hidden pass {E['dates'][k]} predicted from the other nine — east 30 % hold-out only", fontsize=10)
        _save(fig, "example_hidden_date.png")

    # 4 attention
    att = E["attention"]
    if att:
        A = np.stack([a.numpy() if torch.is_tensor(a) else np.array(a) for _, a in att])
        fig, ax = plt.subplots(figsize=(7, 4))
        im = ax.imshow(A, cmap="viridis")
        ax.set_xticks(range(len(E["dates"])))
        ax.set_xticklabels([doy_str(doy(d)) for d in E["dates"]], rotation=45, fontsize=8)
        ax.set_yticks(range(len(att)))
        ax.set_yticklabels([f"hidden {doy_str(doy(E['dates'][k]))}" for k, _ in att], fontsize=8)
        for i, (k, _) in enumerate(att):
            ax.add_patch(plt.Rectangle((k - 0.5, i - 0.5), 1, 1, fill=False, ec="w", lw=1.5))
        fig.colorbar(im, ax=ax, label="mean attention weight")
        ax.set_title("Where the hidden-date query looks (mean over layers, heads, 20k east pixels)", fontsize=9)
        _save(fig, "attention.png")

    # 5 reliability
    rel = [r["calib"]["reliability"] for r in rows if r["kind"] == "single"]
    fig, ax = plt.subplots(figsize=(4.6, 4.2))
    mx = 0
    for r, rr in zip([r for r in rows if r["kind"] == "single"], rel):
        a = np.array(rr)
        ax.plot(a[:, 0], a[:, 1], "o-", ms=3, lw=1, label=doy_str(doy(r["date"])))
        mx = max(mx, a.max())
    ax.plot([0, mx], [0, mx], "k--", lw=1, label="perfect calibration")
    ax.set(xlabel="predicted σ (RMS within decile of σ)", ylabel="actual RMSE in that decile",
           title="Is the predicted uncertainty honest?", xscale="log", yscale="log")
    from matplotlib.ticker import NullFormatter, ScalarFormatter
    for axis in (ax.xaxis, ax.yaxis):
        axis.set_minor_formatter(NullFormatter())
        axis.set_major_formatter(ScalarFormatter())
    ax.legend(fontsize=6, ncol=2)
    _save(fig, "reliability.png")

    # 6 outage images + 7 area curve
    gp = MODELS / f"ttransformer_gap_{area}.pt"
    if gp.exists():
        G = torch.load(gp, map_location="cpu", weights_only=False)
        S = G["summ"]
        fig, axs = plt.subplots(2, 4, figsize=(15, 7))
        kw = dict(cmap="gray", vmin=0, vmax=float(np.quantile(G["obs_a"][0][G["vall"]].numpy(), 0.99)))
        da, db = G["dates_ab"]
        axs[0, 0].imshow(G["obs_a"][0], **kw); axs[0, 0].set_title(f"observed {doy_str(doy(da))} HH")
        axs[0, 1].imshow(G["pred"][1][0], **kw); axs[0, 1].set_title(f"MODEL ESTIMATE {S['gap_days'][1]} HH\n(no pass exists)")
        axs[0, 2].imshow(G["obs_b"][0], **kw); axs[0, 2].set_title(f"observed {doy_str(doy(db))} HH")
        sdv = G["sd"][1][0].numpy()
        im = axs[0, 3].imshow(np.where(G["vall"].numpy(), sdv, np.nan), cmap="magma", vmin=0, vmax=float(np.nanquantile(sdv[G["vall"].numpy()], 0.99)))
        axs[0, 3].set_title(f"predicted σ (HH), {S['gap_days'][1]}")
        fig.colorbar(im, ax=axs[0, 3], shrink=0.7)
        def wmap(ax, w, base, title):
            rgb = np.ones((*w.shape, 3)) * 0.92
            rgb[~G["vall"].numpy()] = 1.0
            b_, w_ = base.numpy(), w.numpy()
            rgb[b_] = (0.15, 0.35, 0.8)
            rgb[w_ & ~b_] = (0.1, 0.75, 0.9)
            rgb[b_ & ~w_] = (0.9, 0.5, 0.1)
            ax.imshow(rgb); ax.set_title(title, fontsize=9)
        wa = G["water_a"]
        wmap(axs[1, 0], wa, wa, f"water {doy_str(doy(da))}: {S['neighbour_km2'][0]:,.0f} km²")
        wmap(axs[1, 1], G["water"][1], wa, f"estimated water {S['gap_days'][1]}: {S['water_gap_km2'][1]:,.0f} km²\n(cyan: new vs {doy_str(doy(da))})")
        wmap(axs[1, 2], G["water_b"], wa, f"water {doy_str(doy(db))}: {S['neighbour_km2'][1]:,.0f} km²")
        wmap(axs[1, 3], G["water_linear"][1], wa, f"linear interpolation {S['gap_days'][1]}: {S['water_linear_km2'][1]:,.0f} km²")
        for a in axs.flat:
            a.set_xticks([]); a.set_yticks([])
        fig.suptitle("The outage pass (~6 Aug 2026) as estimated by the time transformer — a model estimate bounded by the passes on both sides, not an observation", fontsize=10)
        _save(fig, f"outage_{area}.png")

        fig, ax = plt.subplots(figsize=(8, 4))
        dts = [dt.datetime.fromisoformat(d) for d in G["dates"]]
        ax.axvspan(dt.datetime.fromisoformat(GAP[0]), dt.datetime.fromisoformat(GAP[1]), color="0.88", label="no data (outage)")
        ax.plot(dts, G["area_obs"], "ko-", label="detector on observed passes")
        cv = sorted(S["curve"], key=lambda c: c["day"])
        cd = [dt.datetime(2026, 1, 1) + dt.timedelta(days=c["day"] - 1) for c in cv]
        ax.fill_between(cd, [c["km2_lo"] for c in cv], [c["km2_hi"] for c in cv], color="#c0392b", alpha=0.2,
                        label="raw estimate, all pixels shifted by ±1.645 σ (too wide to be useful)")
        ax.plot(cd, [c["km2"] for c in cv], "-", color="#c0392b", lw=1.5, label="transformer estimate (every 2 days)")
        # the same model queried AT the two bounding observed days does not reproduce their area (it is biased
        # low); anchoring removes that offset linearly in time: corr(d) = pred(d) + (1-w) e_a + w e_b
        ia, ib = G["dates"].index(da), G["dates"].index(db)
        ea, eb = G["area_obs"][ia] - cv[0]["km2"], G["area_obs"][ib] - cv[-1]["km2"]
        corr = [c["km2"] + (1 - (c["day"] - cv[0]["day"]) / (cv[-1]["day"] - cv[0]["day"])) * ea
                + (c["day"] - cv[0]["day"]) / (cv[-1]["day"] - cv[0]["day"]) * eb for c in cv]
        ax.plot(cd, corr, "--", color="#8e44ad", lw=1.5, label="transformer, anchored to the observed end points")
        for c, v in zip(cv, corr):
            if c["date"] in S["gap_days"]:
                print(f"    anchored estimate {c['date']}: {v:,.0f} km2 (raw {c['km2']:,.0f})")
        gd = [dt.datetime(2026, 1, 1) + dt.timedelta(days=d - 1) for d in [doy(f"2026-{m}") for m in []]]
        ax.set(ylabel="open water (km²)", title=f"{area}: open-water area, with the outage bridged by the model")
        ax.legend(fontsize=7)
        fig.autofmt_xdate()
        _save(fig, f"area_curve_{area}.png")


# ============================================================ CLI
def get_var(args):
    var = dict(VARIANTS[args.variant])
    if getattr(args, "context", None) is not None:
        var["context"] = args.context
    return var


def cmd_train(args):
    dev = auto_device()
    print(f"device {dev}")
    P = prepare(args.area, dev)
    var = get_var(args)
    t0 = time.time()
    model, hist = train_model(P, var, dev, epochs=args.epochs)
    print(f"variant {args.variant} {var}  params {n_params(model):,}  training {hist['train_s']:.1f}s "
          f"({hist['train_s'] / len(hist['epoch_s']):.1f}s/epoch) on {dev}")
    p = save_model(model, hist, var, f"{args.area}_{args.variant}", {"device": str(dev)})
    print(f"saved {p.relative_to(ROOT)}  total {time.time() - t0:.1f}s")


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items() if not str(k).startswith("_")}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


def cmd_evaluate(args):
    dev = auto_device()
    P = prepare(args.area, dev)
    model, ck = load_model(f"{args.area}_{args.variant}", dev)
    var = ck["variant"]
    t0 = time.time()
    T = len(P["dates"])
    cases = ([("single", [k]) for k in range(1, T - 1)] if args.cases != "double" else []) + \
            ([("double", [k, k + 1]) for k in range(1, T - 2)] if args.cases != "single" else [])
    if args.only:
        cases = [("single" if "," not in c else "double", [int(v) for v in c.split(",")]) for c in args.only.split(";")]
    rows, attn = evaluate(P, model, var, dev, strict=args.strict, strict_epochs=args.strict_epochs, cases=cases,
                          strict_pixels=args.strict_pixels)
    tag = f"{args.area}_{args.variant}{'_strict' if args.strict else ''}{'' if args.cases == 'all' else '_' + args.cases}{'_subset' if args.only else ''}"
    ae = None
    if not args.no_ae:
        ae = ae_baseline(args.area, [[k] for k in range(1, T - 1)] + [[k, k + 1] for k in range(1, T - 2)], dev)
    summ = {"variant": args.variant, "strict": args.strict, "params": ck["params"],
            "train_s": ck["history"]["train_s"], "all": summarise(rows, "all"), "changing": summarise(rows, "changing"),
            "water": summarise_water(rows), "eval_s": time.time() - t0}
    if ae:
        for kind, n in (("single", 1), ("double", 2)):
            vals = [v for (h, k), v in ae.items() if len(h) == n]
            summ.setdefault("gru_ae", {})[kind] = {"ae_mse": float(np.mean([v["ae_mse"] for v in vals])),
                                                   "linear_mse_same_space": float(np.mean([v["linear_mse"] for v in vals])),
                                                   "skill_vs_linear": 1 - np.mean([v["ae_mse"] for v in vals]) / np.mean([v["linear_mse"] for v in vals])}
    cal = [r["calib"] for r in rows if r["kind"] == "single"]
    summ["calibration_single"] = {"frac_within_1sd": float(np.mean([c["frac_within_1sd"] for c in cal])),
                                  "frac_within_1.645sd": float(np.mean([c["frac_within_1.645sd"] for c in cal])),
                                  "mean_pred_sd": float(np.mean([c["mean_pred_sd"] for c in cal])),
                                  "rmse": float(np.mean([c["rmse"] for c in cal]))}
    summ["area_band_coverage"] = {kind: float(np.mean([r["area_band"]["-1.645"] <= r["area_true_km2"] <= r["area_band"]["1.645"]
                                                       or r["area_band"]["1.645"] <= r["area_true_km2"] <= r["area_band"]["-1.645"]
                                                       for r in rows if r["kind"] == kind])) for kind in ("single", "double")}
    FIGS.mkdir(parents=True, exist_ok=True)
    out = {"summary": summ, "rows": rows, "attention": [(k, a.tolist()) for k, a in attn], "dates": P["dates"]}
    (FIGS / f"loo_{tag}.json").write_text(json.dumps(_clean(out), indent=1, default=float))
    ex = [r["_example"] for r in rows if "_example" in r]
    torch.save({"rows": rows, "attention": attn, "summary": summ, "example": ex[0] if ex else None, "dates": P["dates"]},
               MODELS / f"ttransformer_eval_{tag}.pt")
    print(json.dumps(_clean({k: summ[k] for k in summ}), indent=1, default=float)[:8000])


def cmd_predict_gap(args):
    dev = auto_device()
    P = prepare(args.area, dev)
    wa = args.weights_area or "tonle-sap"
    model, ck = load_model(f"{wa}_{args.variant}", dev)
    res = predict_gap(P, model, ck["variant"], dev, n_draws=args.draws)
    loo = None
    for tag in (f"{wa}_{args.variant}_strict", f"{wa}_{args.variant}"):
        p = FIGS / f"loo_{tag}.json"
        if p.exists():
            loo = json.loads(p.read_text())["summary"]
            break
    out_dir = ROOT / "data" / "processed" / args.area / "ttransformer"
    out_dir.mkdir(parents=True, exist_ok=True)
    prod = {"dates": P["dates"], "bounds": P["bounds"], "km2_per_px": P["km2"],
            "gap_days": [doy_str(d) for d in res["gap_days"]], "gap_doy": res["gap_days"],
            "pred_gap": res["pred_gap"].cpu().half(), "pred_gap_sd": res["pred_gap_sd"].cpu().half(),
            "water_gap": res["water_gap"].cpu(), "water_gap_km2": res["water_gap_km2"],
            "water_gap_km2_lo": res["water_gap_km2_lo"], "water_gap_km2_hi": res["water_gap_km2_hi"],
            "loo_metrics": loo, "weights": f"ttransformer_{wa}_{args.variant}.pt",
            "note": "MODEL ESTIMATE of an unobserved pass (no data exists 27 Jul - 10 Aug 2026), bounded by the passes "
                    "on both sides; values are preview display brightness, not calibrated backscatter.",
            "area_curve": res["curve"], "water_linear_km2": res["water_linear_km2"],
            "neighbour_km2": res["neighbour_km2"]}
    torch.save(prod, out_dir / "products.pt")
    summ = {k: res[k] for k in ("water_gap_km2", "water_gap_km2_lo", "water_gap_km2_hi", "water_linear_km2",
                                 "diff_vs_linear_mae", "neighbour_km2", "new_band_km2", "pred_new_in_band_km2",
                                 "pred_new_outside_band_km2", "water_gap_km2_indep_q05", "water_gap_km2_indep_q95") if k in res}
    summ["gap_days"] = [doy_str(d) for d in res["gap_days"]]
    summ["curve"] = res["curve"]
    (FIGS / f"gap_{args.area}.json").write_text(json.dumps(summ, indent=1, default=float))
    # keep a few images for figures
    a, b = res["neighbours"]
    torch.save({"pred": res["pred_gap"].cpu(), "sd": res["pred_gap_sd"].cpu(), "water": res["water_gap"].cpu(),
                "water_linear": res["water_linear"].cpu(), "obs_a": P["L"][a].cpu(), "obs_b": P["L"][b].cpu(),
                "water_a": (P["water"][a] & P["vall"]).cpu(), "water_b": (P["water"][b] & P["vall"]).cpu(),
                "dates_ab": (P["dates"][a], P["dates"][b]), "summ": summ, "vall": P["vall"].cpu(),
                "area_obs": [float((P["water"][i] & P["vall"]).sum()) * P["km2"] for i in range(len(P["dates"]))],
                "dates": P["dates"]}, MODELS / f"ttransformer_gap_{args.area}.pt")
    print(json.dumps(summ, indent=1, default=float))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("train", "evaluate", "predict-gap", "figures"):
        s = sub.add_parser(name)
        s.add_argument("--area", default="tonle-sap")
        s.add_argument("--variant", default="base", choices=list(VARIANTS))
        if name == "train":
            s.add_argument("--epochs", type=int, default=TRAIN["epochs"])
            s.add_argument("--context", type=int, default=None, choices=[0, 3, 5])
        if name == "evaluate":
            s.add_argument("--strict", action="store_true", help="retrain without the hidden date(s) for every case")
            s.add_argument("--strict-epochs", type=int, default=3)
            s.add_argument("--strict-pixels", type=int, default=100_000)
            s.add_argument("--cases", default="all", choices=["all", "single", "double"])
            s.add_argument("--only", default=None, help="explicit cases, e.g. '3;4;5;6;4,5' (date indices)")
            s.add_argument("--no-ae", action="store_true")
        if name == "predict-gap":
            s.add_argument("--weights-area", default=None)
            s.add_argument("--draws", type=int, default=20)
    args = ap.parse_args()
    if args.cmd == "train":
        cmd_train(args)
    elif args.cmd == "evaluate":
        cmd_evaluate(args)
    elif args.cmd == "predict-gap":
        cmd_predict_gap(args)
    else:
        make_figures(args.area, args.variant)


if __name__ == "__main__":
    main()
