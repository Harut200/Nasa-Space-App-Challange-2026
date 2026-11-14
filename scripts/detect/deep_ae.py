"""Large pretrained CNN encoder (EfficientNet-B4) in a masked autoencoder + Gaussian mixture on its features.

An honest test of the "big pretrained backbone" idea against the small models we already have.

    .venv/bin/python -m scripts.detect.deep_ae train   --area tonle-sap --variant b4_pre_ft [--epochs N]
    .venv/bin/python -m scripts.detect.deep_ae sweep   --jobs 2          # train every variant, 2 at a time
    .venv/bin/python -m scripts.detect.deep_ae compare                   # the whole comparison table + figures
    .venv/bin/python -m scripts.detect.deep_ae predict --area sevan      # products.pt with Tonle Sap weights

Weights:  data/processed/models/deep_ae_<variant>.pt  (+ deep_ae_<method>_gmm.pt, the fitted mixture)
Products: data/processed/<area>/deep_ae/products.pt
Results:  docs/eda/deep_ae/results.json and figures in docs/eda/deep_ae/
No ground truth exists. Every score is agreement with the math detector, with the independent
JRC Global Surface Water reference, or a self-consistency number -- never accuracy.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from scripts.detect.models import TemporalAE, kmeans, n_params
from scripts.detect.stack import ROOT
from scripts.detect.train import (HOLDOUT_FRAC, MODELS, SEVAN_TRUE_KM2, ae_features, ae_run_all, as_dates,
                                  load_stack, pick_device, seed_all, shade_gap, sync)
from scripts.detect.weak_labels import smooth

FIGS = ROOT / "docs" / "eda" / "deep_ae"

# ----------------------------------------------------------------------------- variants (edit freely)
# Every variant uses the same seed, the same west-70 % / east-30 % split of Tonle Sap and the same
# masked-reconstruction loss, so they compare like with like.
DEFAULTS = {"backbone": "b4", "pretrained": True, "freeze": False, "inputs": "adapter", "skip": False,
            "stages": 4, "up": 2, "emb": 16, "epochs": 6, "steps": 50, "batch": 8, "patch": 128,
            "lr": 1e-3, "enc_lr": 2e-4, "mask_frac": 0.4, "block": 16, "seed": 0}
VARIANTS = {
    "b4_pre_ft":      {},                                    # (i)   ImageNet weights, fine-tuned (main model)
    "b4_pre_frozen":  {"freeze": True},                      # (ii)  ImageNet weights, encoder frozen
    "b4_rand_ft":     {"pretrained": False, "enc_lr": 1e-3},  # (iii) same architecture, random init
    "b4_pre_ft_rgb":  {"inputs": "rgb"},                     # input option (a): HH, HV, HH-HV as an "RGB" photo
    "b4_pre_ft_skip": {"skip": True},                        # decoder with a full-resolution skip connection
}
MAIN = "b4_pre_ft"
# methods of the comparison table: name -> (kind, variant)
METHODS = {
    "B4 pretrained, fine-tuned": ("deep", "b4_pre_ft"),
    "B4 pretrained, frozen": ("deep", "b4_pre_frozen"),
    "B4 random init": ("deep", "b4_rand_ft"),
    "B4 pretrained, RGB input": ("deep", "b4_pre_ft_rgb"),
    "B4 pretrained, with skip": ("deep", "b4_pre_ft_skip"),
    "TemporalAE (19.5k) + date HH/HV": ("tae", None),
    "No network: raw features": ("raw", None),
    "No network: date HH/HV only": ("raw2", None),
}
SHORT = {"B4 pretrained, fine-tuned": "B4 ft", "B4 pretrained, frozen": "B4 frozen", "B4 random init": "B4 rand",
         "B4 pretrained, RGB input": "B4 RGB", "B4 pretrained, with skip": "B4 skip",
         "TemporalAE (19.5k) + date HH/HV": "TempAE", "No network: raw features": "raw GMM",
         "No network: date HH/HV only": "raw HH/HV"}
WATER, FVEG, OTHER = 0, 1, 2
K_RANGE = range(3, 11)
GMM_SAMPLES = 60_000
NEVER_DIST_PX = 3
IMAGENET_MEAN = torch.tensor([0.485, 0.456, 0.406])[:, None, None]
IMAGENET_STD = torch.tensor([0.229, 0.224, 0.225])[:, None, None]
STAGE_STRIDE = {2: 2, 3: 4, 4: 8, 5: 16, 6: 16, 7: 32, 8: 32}
STAGE_CH = {2: 24, 3: 32, 4: 56, 5: 112, 6: 160, 7: 272, 8: 448}  # EfficientNet-B4 output channels


def config(variant: str, **over) -> dict:
    cfg = {**DEFAULTS, **VARIANTS[variant]}
    cfg.update({k: v for k, v in over.items() if v is not None})
    return cfg


# ----------------------------------------------------------------------------- model
B4_PARTIAL = MODELS / "deep_ae_b4_imagenet_stages0-3.pt"


def b4_features(pretrained: bool, stages: int = 4) -> nn.Sequential:
    """torchvision EfficientNet-B4 `features` (ImageNet-1k weights or random init).

    The full 78 MB weight file could not be downloaded on the hackathon network (~10 KB/s), so the
    ImageNet tensors of features[0:4] (the only stages used) were fetched from the official
    download.pytorch.org file by HTTP range requests and saved to B4_PARTIAL. If the full file is in the
    torch hub cache, torchvision loads it as usual."""
    from torchvision.models import EfficientNet_B4_Weights, efficientnet_b4
    if not pretrained:
        return efficientnet_b4(weights=None).features
    try:
        wts = EfficientNet_B4_Weights.IMAGENET1K_V1
        cached = (torch.hub.get_dir() + "/checkpoints/" + wts.url.split("/")[-1])
        import os
        if os.path.exists(cached) or not B4_PARTIAL.exists():
            return efficientnet_b4(weights=wts).features
    except Exception:
        pass
    assert stages <= 4, "only features[0:4] of the ImageNet weights are available locally"
    feats = efficientnet_b4(weights=None).features
    sd = torch.load(B4_PARTIAL, map_location="cpu")
    missing = feats.load_state_dict({k[len("features."):]: v for k, v in sd.items()}, strict=False)
    need = [k for k in missing.missing_keys if int(k.split(".")[0]) < stages]
    assert not need, f"pretrained tensors missing: {need[:5]}"
    return feats


class DeepAE(nn.Module):
    """Input [B, C, H, W] at native 180 m pixels -> 2x bilinear upsample -> (1x1 adapter to 3 channels)
    -> EfficientNet-B4 stem + MBConv stages[1:stages] (stride 8 on the upsampled grid = 4 native px)
    -> 1x1 projection to `emb` channels (the bottleneck / embedding) -> light decoder (bilinear
    upsample + 3x3 convs, no transposed convs, so no checkerboard) -> reconstruction at native size.
    skip=True adds the stride-2 stem features (= native resolution) to the last decoder level."""

    def __init__(self, n_in: int = 8, n_out: int | None = None, inputs: str = "adapter", pretrained: bool = True,
                 stages: int = 4, up: int = 2, emb: int = 16, skip: bool = False, features: nn.Sequential | None = None):
        super().__init__()
        n_out = n_out or n_in
        feats = features if features is not None else b4_features(pretrained, stages)
        self.inputs, self.up, self.skip, self.stages = inputs, up, skip, stages
        self.adapter = None
        if inputs == "adapter":
            self.adapter = nn.Conv2d(n_in, 3, 1)
            with torch.no_grad():  # start as R = HH, G = HV, B = HH - HV, plus small noise on the other inputs
                w = torch.randn(3, n_in) * 0.02
                w[0, 0], w[1, 1], w[2, 0], w[2, 1] = 1.0, 1.0, 1.0, -1.0
                self.adapter.weight.copy_(w[..., None, None])
                self.adapter.bias.zero_()
        else:
            assert n_in == 3, "rgb input expects 3 channels"
        self.stem = feats[:2]                # stride 2, 24 channels
        self.body = feats[2:stages]          # up to stride STAGE_STRIDE[stages]
        c_top = STAGE_CH[stages]
        self.proj = nn.Conv2d(c_top, emb, 1)
        self.native_stride = max(STAGE_STRIDE[stages] // up, 1)
        n_up = int(round(math.log2(self.native_stride)))
        chans = [64] + [32] * n_up
        dec = [nn.Conv2d(emb, chans[0], 3, padding=1), nn.GELU()]
        for i in range(n_up):
            dec += [nn.Upsample(scale_factor=2, mode="bilinear", align_corners=False),
                    nn.Conv2d(chans[i], chans[i + 1], 3, padding=1), nn.GELU()]
        self.dec = nn.Sequential(*dec)
        self.skip_proj = nn.Conv2d(24, 16, 1) if skip else None
        self.head = nn.Sequential(nn.Conv2d(chans[-1] + (16 if skip else 0), 32, 3, padding=1), nn.GELU(),
                                  nn.Conv2d(32, n_out, 1))

    def encoder_parameters(self):
        return list(self.stem.parameters()) + list(self.body.parameters())

    def encode(self, x: torch.Tensor):
        if self.up != 1:
            x = F.interpolate(x, scale_factor=self.up, mode="bilinear", align_corners=False)
        if self.adapter is not None:
            x = self.adapter(x)
        s = self.stem(x)
        return self.proj(self.body(s)), s

    def forward(self, x: torch.Tensor):
        h, w = x.shape[-2:]
        z, s = self.encode(x)
        d = self.dec(z)
        d = F.interpolate(d, size=(h, w), mode="bilinear", align_corners=False) if d.shape[-2:] != (h, w) else d
        if self.skip_proj is not None:
            sk = self.skip_proj(s)
            sk = F.interpolate(sk, size=(h, w), mode="bilinear", align_corners=False) if sk.shape[-2:] != (h, w) else sk
            d = torch.cat([d, sk], 1)
        return self.head(d), z

    @torch.no_grad()
    def embed(self, x: torch.Tensor) -> torch.Tensor:
        """x [B, C, H, W] of any size -> dense embedding [B, emb, H, W] (bottleneck, bilinear-upsampled)."""
        h, w = x.shape[-2:]
        m = self.native_stride
        ph, pw = (-h) % m, (-w) % m
        xp = F.pad(x, (0, pw, 0, ph), mode="reflect") if (ph or pw) else x
        z, _ = self.encode(xp)
        z = F.interpolate(z, size=xp.shape[-2:], mode="bilinear", align_corners=False)
        return z[..., :h, :w]


def build_model(cfg: dict, n_in: int, pretrained: bool | None = None) -> DeepAE:
    pre = cfg["pretrained"] if pretrained is None else pretrained
    return DeepAE(n_in=n_in, inputs=cfg["inputs"], pretrained=pre, stages=cfg["stages"], up=cfg["up"],
                  emb=cfg["emb"], skip=cfg["skip"])


# ----------------------------------------------------------------------------- inputs
def season_stats(x: torch.Tensor, valid: torch.Tensor) -> torch.Tensor:
    """x [T, 2, H, W], valid [T, H, W] -> [6, H, W]: min, mean, std over valid dates of HH then HV."""
    v = valid[:, None].float()
    n = v.sum(0).clamp_min(1)
    mean = (x * v).sum(0) / n
    sd = (((x - mean) ** 2) * v).sum(0).div(n).sqrt()
    mn = torch.where(valid[:, None], x, torch.full_like(x, 2.0)).amin(0)
    mn = torch.where(mn > 1.5, torch.zeros_like(mn), mn)
    return torch.stack([mn[0], mean[0], sd[0], mn[1], mean[1], sd[1]])


def adapter_inputs(x: torch.Tensor, valid: torch.Tensor) -> torch.Tensor:
    """[T, 2, H, W] -> [T, 8, H, W]: this date's HH, HV and season min/mean/std of HH and HV, scaled
    (level - 0.5) / 0.25 and std / 0.1; zero on invalid dates."""
    s = season_stats(x, valid)
    s = torch.stack([(s[0] - .5) / .25, (s[1] - .5) / .25, s[2] / .1, (s[3] - .5) / .25, (s[4] - .5) / .25, s[5] / .1])
    T = x.shape[0]
    out = torch.cat([(x - 0.5) / 0.25, s[None].expand(T, -1, -1, -1)], 1)
    return out * valid[:, None].float()


def rgb_inputs(x: torch.Tensor, valid: torch.Tensor) -> torch.Tensor:
    """[T, 2, H, W] -> [T, 3, H, W]: R = HH, G = HV, B = HH - HV + 0.5 as a photo, ImageNet-normalised."""
    rgb = torch.stack([x[:, 0], x[:, 1], (x[:, 0] - x[:, 1] + 0.5).clamp(0, 1)], 1)
    return ((rgb - IMAGENET_MEAN) / IMAGENET_STD) * valid[:, None].float()


def net_inputs(cfg: dict, st) -> torch.Tensor:
    return adapter_inputs(st.x, st.valid) if cfg["inputs"] == "adapter" else rgb_inputs(st.x, st.valid)


def raw_features(st) -> torch.Tensor:
    """No-network baseline: the same 8 numbers as the adapter input, from the 3x3-smoothed stack
    (a per-pixel method gets the spatial averaging the CNN does implicitly)."""
    return adapter_inputs(smooth(st.x, st.valid), st.valid)


# ----------------------------------------------------------------------------- training
def block_mask(b: int, p: int, block: int, frac: float, gen: torch.Generator) -> torch.Tensor:
    """[b, 1, p, p] bool, True = hidden. Random `block` x `block` squares, each hidden with prob `frac`."""
    nb = math.ceil(p / block)
    m = torch.rand(b, 1, nb, nb, generator=gen) < frac
    return m.repeat_interleave(block, 2).repeat_interleave(block, 3)[..., :p, :p]


def augment(*ts):
    k = int(torch.randint(4, (1,)))
    fh, fv = bool(torch.rand(1) < 0.5), bool(torch.rand(1) < 0.5)
    out = []
    for t in ts:
        t = torch.rot90(t, k, (-2, -1))
        t = t.flip(-1) if fh else t
        out.append(t.flip(-2) if fv else t)
    return out


def masked_loss(rec, tgt, hidden, valid):
    """0.5 * MSE over all valid pixels + MSE over hidden valid pixels."""
    v = valid.float()
    hv = (hidden & valid).float()
    se = ((rec - tgt) ** 2).mean(1, keepdim=True)
    return 0.5 * (se * v).sum() / v.sum().clamp_min(1) + (se * hv).sum() / hv.sum().clamp_min(1)


def sample_patches(inp, valid, split, P, n, gen, east=False):
    T, C, H, W = inp.shape
    xs, vs = [], []
    while len(xs) < n:
        t = int(torch.randint(T, (1,), generator=gen))
        r = int(torch.randint(0, H - P + 1, (1,), generator=gen))
        c = int(torch.randint(split, W - P + 1, (1,), generator=gen)) if east else \
            int(torch.randint(0, split - P + 1, (1,), generator=gen))
        v = valid[t, r:r + P, c:c + P]
        if v.float().mean() < 0.5:
            continue
        xs.append(inp[t, :, r:r + P, c:c + P])
        vs.append(v)
    return torch.stack(xs), torch.stack(vs)[:, None]


def cmd_train(args):
    cfg = config(args.variant, epochs=args.epochs, steps=args.steps)
    dev = pick_device(args.device)
    seed_all(cfg["seed"])
    st = load_stack(args.area)
    inp = net_inputs(cfg, st)
    T, C, H, W = inp.shape
    split = int(W * (1 - HOLDOUT_FRAC))
    model = build_model(cfg, C).to(dev)
    enc = model.encoder_parameters()
    if cfg["freeze"]:
        for p in enc:
            p.requires_grad_(False)
    n_enc = sum(p.numel() for p in enc)
    n_all = n_params(model)
    print(f"{args.variant}: params total {n_all:,} (encoder {n_enc:,}, trainable "
          f"{sum(p.numel() for p in model.parameters() if p.requires_grad):,})  native stride {model.native_stride}  device {dev}")
    other = [p for n_, p in model.named_parameters() if not (n_.startswith("stem") or n_.startswith("body"))]
    groups = [{"params": other, "lr": cfg["lr"]}]
    if not cfg["freeze"]:
        groups.append({"params": enc, "lr": cfg["enc_lr"]})
    opt = torch.optim.AdamW(groups, weight_decay=1e-4)
    total = cfg["epochs"] * cfg["steps"]
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=[g["lr"] for g in groups], total_steps=total)
    g = torch.Generator().manual_seed(cfg["seed"])
    gv = torch.Generator().manual_seed(1234)
    xv, vv = sample_patches(inp, st.valid, split, cfg["patch"], 24, gv, east=True)
    hv = block_mask(24, cfg["patch"], cfg["block"], cfg["mask_frac"], gv)
    xv, vv, hv = xv.to(dev), vv.to(dev), hv.to(dev)
    hist = {"train": [], "val": [], "val_identity": None}
    inp_d, valid_d = inp, st.valid
    t_start = time.time()
    for ep in range(cfg["epochs"]):
        t0 = time.time()
        model.train()
        if cfg["freeze"]:
            model.stem.eval(), model.body.eval()   # frozen BN statistics too
        tot = 0.0
        for s in range(cfg["steps"]):
            xb, vb = sample_patches(inp_d, valid_d, split, cfg["patch"], cfg["batch"], g)
            hb = block_mask(cfg["batch"], cfg["patch"], cfg["block"], cfg["mask_frac"], g)
            xb, vb, hb = augment(xb, vb, hb)
            xb, vb, hb = xb.to(dev), vb.to(dev), hb.to(dev)
            rec, _ = model(xb * (~hb).float())
            loss = masked_loss(rec, xb, hb, vb)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            sched.step()
            tot += loss.item()
        model.eval()
        with torch.no_grad():
            rv, _ = model(xv * (~hv).float())
            lv = masked_loss(rv, xv, hv, vv).item()
            if hist["val_identity"] is None:  # baseline: predict 0 (the scaled mean) where hidden
                hist["val_identity"] = masked_loss(xv * (~hv).float(), xv, hv, vv).item()
        sync(dev)
        hist["train"].append(tot / cfg["steps"])
        hist["val"].append(lv)
        print(f"epoch {ep + 1}/{cfg['epochs']}  train {tot / cfg['steps']:.4f}  held-out east {lv:.4f}  "
              f"{time.time() - t0:.1f}s", flush=True)
    train_time = time.time() - t_start
    print(f"training time {train_time:.1f}s on {dev}")
    MODELS.mkdir(parents=True, exist_ok=True)
    path = MODELS / f"deep_ae_{args.variant}.pt"
    torch.save({"state_dict": model.state_dict(), "cfg": cfg, "n_in": C, "variant": args.variant,
                "params": n_all, "params_encoder": n_enc, "history": hist, "train_time_s": train_time,
                "device": str(dev), "area": args.area}, path)
    print(f"saved {path} ({path.stat().st_size / 1e6:.1f} MB)")


def load_deep(variant: str, dev):
    ck = torch.load(MODELS / f"deep_ae_{variant}.pt", map_location="cpu", weights_only=False)
    m = build_model(ck["cfg"], ck["n_in"], pretrained=False)   # weights come from the checkpoint
    m.load_state_dict(ck["state_dict"])
    return m.to(dev).eval(), ck


@torch.no_grad()
def deep_embeddings(model, inp: torch.Tensor, dev, tile: int = 384, margin: int = 32) -> torch.Tensor:
    """[T, C, H, W] -> [T, emb, H, W] float16 on CPU; tiles of `tile` px with a `margin`-px overlap that is
    cropped away (no seams; keeps GPU memory small)."""
    T, C, H, W = inp.shape
    out = None
    for t in range(T):
        for r in range(0, H, tile):
            for c in range(0, W, tile):
                r0, c0 = max(r - margin, 0), max(c - margin, 0)
                r1, c1 = min(r + tile + margin, H), min(c + tile + margin, W)
                e = model.embed(inp[t:t + 1, :, r0:r1, c0:c1].to(dev))[0].half().cpu()
                if out is None:
                    out = torch.zeros(T, e.shape[0], H, W, dtype=torch.float16)
                rr, cc = min(r + tile, H), min(c + tile, W)
                out[t, :, r:rr, c:cc] = e[:, r - r0:rr - r0, c - c0:cc - c0]
    if dev.type == "mps":
        torch.mps.empty_cache()
    return out


# ----------------------------------------------------------------------------- Gaussian mixture (EM, torch)
class Whiten:
    """Standardise, then PCA-whiten keeping components up to 99 % of the variance (max 16)."""

    def __init__(self, x: torch.Tensor, var: float = 0.99, max_dim: int = 16):
        x = x.double()
        self.mu, self.sd = x.mean(0), x.std(0).clamp_min(1e-6)
        xs = (x - self.mu) / self.sd
        ev, evec = torch.linalg.eigh(torch.cov(xs.T))
        ev, evec = ev.flip(0).clamp_min(1e-9), evec.flip(1)
        d = int((torch.cumsum(ev, 0) / ev.sum() < var).sum()) + 1
        d = min(d, max_dim, x.shape[1])
        self.P = evec[:, :d] / ev[:d].sqrt()
        self.dim = d

    def __call__(self, x: torch.Tensor) -> torch.Tensor:
        P, mu, sd = (a.to(x.device, torch.float32) for a in (self.P, self.mu, self.sd))
        return ((x.float() - mu) / sd) @ P

    def state(self):
        return {"mu": self.mu, "sd": self.sd, "P": self.P}

    @classmethod
    def from_state(cls, s):
        o = cls.__new__(cls)
        o.mu, o.sd, o.P, o.dim = s["mu"], s["sd"], s["P"], s["P"].shape[1]
        return o


def gmm_logprob(x: torch.Tensor, means, covs, weights) -> torch.Tensor:
    """x [N, D] -> per-component log(w_k N(x | mu_k, S_k)) [N, K]."""
    D = x.shape[1]
    L = torch.linalg.cholesky(covs)                                    # [K, D, D]
    diff = (x[None] - means[:, None]).transpose(1, 2)                  # [K, D, N]
    sol = torch.linalg.solve_triangular(L, diff, upper=False)
    maha = (sol ** 2).sum(1)                                           # [K, N]
    logdet = 2 * torch.log(torch.diagonal(L, dim1=-2, dim2=-1)).sum(-1)
    return (torch.log(weights)[:, None] - 0.5 * (D * math.log(2 * math.pi) + logdet[:, None] + maha)).T


def fit_gmm(x: torch.Tensor, k: int, restarts: int = 3, iters: int = 200, seed: int = 0, reg: float = 1e-3,
            tol: float = 1e-5) -> dict:
    """Full-covariance GMM by EM in float64 on CPU, k-means++ init, best of `restarts` by log-likelihood.
    Returns weights [K], means [K, D], covs [K, D, D], loglik (total), bic, k."""
    x = x.double().cpu()
    N, D = x.shape
    eye = torch.eye(D, dtype=x.dtype)
    best = None
    for r in range(restarts):
        c, lab = kmeans(x.float(), k, seed=seed * 100 + r, iters=30)
        resp = F.one_hot(lab, k).double()
        prev = -float("inf")
        for it in range(iters):
            nk = resp.sum(0) + 1e-10
            w = nk / N
            mu = (resp.T @ x) / nk[:, None]
            d = x[None] - mu[:, None]                                    # [K, N, D]
            cov = torch.einsum("kn,knd,kne->kde", resp.T, d, d) / nk[:, None, None] + reg * eye
            lp = gmm_logprob(x, mu, cov, w)
            ll = torch.logsumexp(lp, 1)
            resp = torch.exp(lp - ll[:, None])
            tot = ll.sum().item()
            if abs(tot - prev) < tol * abs(tot):
                break
            prev = tot
        if best is None or tot > best["loglik"]:
            best = {"weights": w, "means": mu, "covs": cov, "loglik": tot, "iters": it + 1}
    n_par = (k - 1) + k * D + k * D * (D + 1) / 2
    best["bic"] = -2 * best["loglik"] + n_par * math.log(N)
    best["k"] = k
    return best


@torch.no_grad()
def gmm_predict(x: torch.Tensor, g: dict, dev, chunk: int = 1_000_000):
    """x [N, D] (already whitened) -> labels [N] uint8, max posterior [N] f16, log-likelihood [N] f32."""
    mu, cov, w = (g[k].to(dev, torch.float32) for k in ("means", "covs", "weights"))
    labs, pmax, lls = [], [], []
    for i in range(0, x.shape[0], chunk):
        lp = gmm_logprob(x[i:i + chunk].to(dev, torch.float32), mu, cov, w)
        ll = torch.logsumexp(lp, 1)
        post = torch.exp(lp - ll[:, None])
        p, lab = post.max(1)
        labs.append(lab.to(torch.uint8).cpu()), pmax.append(p.half().cpu()), lls.append(ll.cpu())
    return torch.cat(labs), torch.cat(pmax), torch.cat(lls)


@torch.no_grad()
def gmm_posteriors(x: torch.Tensor, g: dict, dev) -> torch.Tensor:
    mu, cov, w = (g[k].to(dev, torch.float32) for k in ("means", "covs", "weights"))
    lp = gmm_logprob(x.to(dev, torch.float32), mu, cov, w)
    return torch.exp(lp - torch.logsumexp(lp, 1, keepdim=True)).cpu()


# ----------------------------------------------------------------------------- scenes, features per method
def math_target(area: str, st) -> dict:
    """The math detector WITH the terrain mask (ops.run_pipeline(..., water_ok=terrain.cached_water_possible)),
    cached in data/processed/models/deep_ae_math_<area>.pt."""
    cache = MODELS / f"deep_ae_math_{area}.pt"
    if cache.exists():
        return torch.load(cache, map_location="cpu", weights_only=False)
    from scripts.detect import ops
    from scripts.detect.terrain import cached_water_possible
    H, W = st.x.shape[-2:]
    ok = cached_water_possible(area, (H, W))
    o = ops.run_pipeline(st.x, st.raw_valid, st.source, dev=pick_device(None), water_ok=ok)
    mp = {k: o[k].cpu() for k in ("water", "flooded_veg", "valid")}
    mp.update(bounds=st.bounds, terrain_mask=ok is not None)
    del o
    torch.save(mp, cache)
    return mp



class Scene:
    """Stack + math-detector classes per date (water / flooded veg / other) + JRC reference anchors."""

    def __init__(self, area: str):
        from scripts.detect.validate import reference_on_grid
        self.area = area
        self.st = load_stack(area)
        mp = math_target(area, self.st)
        T, H, W = mp["water"].shape
        self.valid = self.st.valid & mp["valid"]
        lab = torch.full((T, H, W), OTHER, dtype=torch.uint8)
        lab[mp["flooded_veg"]] = FVEG
        lab[mp["water"]] = WATER
        self.lab, self.H, self.W, self.T = lab, H, W, T
        self.split = int(W * (1 - HOLDOUT_FRAC))
        self.east = torch.zeros(H, W, dtype=torch.bool)
        self.east[:, self.split:] = True
        self.math_water = mp["water"] & self.valid
        ref, _ = reference_on_grid(area, mp["bounds"], H, W)
        dom = ref["valid_frac"] >= 0.5
        self.occ = ref["occ_mean"].float()
        any_w = ref["occ_any_frac"] > 0
        near = F.max_pool2d(any_w[None, None].float(), 2 * NEVER_DIST_PX + 1, 1, NEVER_DIST_PX)[0, 0] > 0
        self.pos = dom & (self.occ >= 90)
        self.neg = dom & ~near
        self.dom = dom
        self.km2 = self.st.km2_per_px


def method_features(kind: str, variant: str | None, sc: Scene, dev):
    """-> (features [T, D, H, W] float16 CPU, inference seconds, info dict)."""
    st = sc.st
    t0 = time.time()
    if kind in ("raw", "raw2"):
        f = raw_features(st).half()
        f = f[:, :2].contiguous() if kind == "raw2" else f
        return f, time.time() - t0, {"params": 0, "train_time_s": 0.0, "weights_mb": 0.0, "dim": f.shape[1]}
    if kind == "tae":
        path = MODELS / "ae_tonle-sap.pt"
        ck = torch.load(path, map_location="cpu", weights_only=False)
        m = TemporalAE(**ck["config"]).to(dev)
        m.load_state_dict(ck["state_dict"])
        feats, target, mask, tt = ae_features(st)
        z, _ = ae_run_all(m, feats, target, mask, tt, dev)
        sync(dev)
        z = ((z - ck["z_mu"]) / ck["z_sd"]).T.reshape(-1, sc.H, sc.W)
        s = smooth(st.x, st.valid)
        f = torch.cat([z[None].expand(sc.T, -1, -1, -1), (s - 0.5) / 0.25], 1).half()
        return f, time.time() - t0, {"params": n_params(m), "train_time_s": ck["train_time_s"],
                                     "weights_mb": path.stat().st_size / 1e6, "dim": f.shape[1]}
    model, ck = load_deep(variant, dev)
    inp = net_inputs(ck["cfg"], st)
    f = deep_embeddings(model, inp, dev)
    sync(dev)
    path = MODELS / f"deep_ae_{variant}.pt"
    return f, time.time() - t0, {"params": ck["params"], "params_encoder": ck["params_encoder"],
                                 "train_time_s": ck["train_time_s"], "weights_mb": path.stat().st_size / 1e6,
                                 "dim": f.shape[1], "history": ck["history"], "cfg": ck["cfg"]}


def flat(f: torch.Tensor) -> torch.Tensor:
    """[T, D, H, W] -> [T*H*W, D] (pixel-date rows)."""
    return f.permute(0, 2, 3, 1).reshape(-1, f.shape[1])


def sample_idx(mask: torch.Tensor, n: int, seed: int) -> torch.Tensor:
    idx = torch.nonzero(mask.reshape(-1)).squeeze(1)
    g = torch.Generator().manual_seed(seed)
    return idx[torch.randperm(idx.numel(), generator=g)[:n]]


# ----------------------------------------------------------------------------- metrics
def contingency(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ka, kb = int(a.max()) + 1, int(b.max()) + 1
    return np.bincount(a.astype(np.int64) * kb + b, minlength=ka * kb).reshape(ka, kb).astype(np.float64)


def ari(a, b) -> float:
    n = contingency(np.asarray(a), np.asarray(b))
    c2 = lambda v: v * (v - 1) / 2
    s_ij, s_a, s_b, N = c2(n).sum(), c2(n.sum(1)).sum(), c2(n.sum(0)).sum(), c2(n.sum())
    exp = s_a * s_b / N
    return float((s_ij - exp) / (0.5 * (s_a + s_b) - exp))


def nmi(a, b) -> float:
    n = contingency(np.asarray(a), np.asarray(b))
    p = n / n.sum()
    pa, pb = p.sum(1), p.sum(0)
    nz = p > 0
    mi = (p[nz] * np.log(p[nz] / (pa[:, None] * pb[None])[nz])).sum()
    h = lambda q: -(q[q > 0] * np.log(q[q > 0])).sum()
    return float(mi / (0.5 * (h(pa) + h(pb))))


def iou(a: torch.Tensor, b: torch.Tensor) -> float:
    u = (a | b).sum().item()
    return (a & b).sum().item() / u if u else float("nan")


def largest_cc(mask: np.ndarray) -> int:
    import scipy.ndimage as ndi
    lab, n = ndi.label(mask)
    return int(np.bincount(lab.ravel())[1:].max()) if n else 0


def logistic_probe(xtr, ytr, xte, n_cls=3, wd=1e-3, iters=100, log_prior=None):
    """Multinomial logistic regression (L-BFGS, torch). Inputs standardised by training statistics.
    Trained on class-balanced samples; `log_prior` (true class log-frequencies) is added to the logits
    at prediction time (Bayes correction for the balanced training set)."""
    mu, sd = xtr.mean(0), xtr.std(0).clamp_min(1e-6)
    xtr, xte = (xtr - mu) / sd, (xte - mu) / sd
    lin = nn.Linear(xtr.shape[1], n_cls).double()
    opt = torch.optim.LBFGS(lin.parameters(), max_iter=iters, line_search_fn="strong_wolfe")

    def closure():
        opt.zero_grad()
        loss = F.cross_entropy(lin(xtr), ytr) + wd * (lin.weight ** 2).sum()
        loss.backward()
        return loss
    opt.step(closure)
    with torch.no_grad():
        lo = lin(xte)
        return (lo + log_prior if log_prior is not None else lo).argmax(1)


def miou3(pred, y):
    return float(np.nanmean([iou(pred == c, y == c) for c in range(3)]))


# ----------------------------------------------------------------------------- cluster naming
def name_cluster(hh: float, hv: float, water_frac: float, fveg_frac: float) -> str:
    if water_frac >= 0.5:
        return "WATER: dark in HH and HV"
    if fveg_frac >= 0.3:
        return "flooded-vegetation-like: bright HH"
    if hh < 0.2 and hv < 0.2:
        return "dark, not math water (shore / dark land)"
    if hh - hv > 0.15 and hh > 0.45:
        return "bright HH >> HV (double bounce?)"
    if hv > 0.6:
        return "bright HV (forest / built-up)"
    if hh > 0.45:
        return "mid-bright land"
    return "mid-dark land (fields / grass)"


# ----------------------------------------------------------------------------- the comparison
def fit_method(name, kind, variant, ts: Scene, dev, seeds=(0, 1, 2)):
    """Features, whitening, BIC-selected GMM (seed 0) and refits with other seeds, for one method on Tonle Sap."""
    f, t_inf, info = method_features(kind, variant, ts, dev)
    X = flat(f)
    vflat = ts.valid.reshape(-1)
    west = (~ts.east)[None].expand(ts.T, -1, -1).reshape(-1) & vflat
    tr = sample_idx(west, GMM_SAMPLES, 0)
    wh = Whiten(X[tr].float())
    xs = wh(X[tr].float())
    t0 = time.time()
    bics = {}
    fits = {}
    for k in K_RANGE:
        fits[k] = fit_gmm(xs, k, seed=0)
        bics[k] = fits[k]["bic"]
    k_best = min(bics, key=bics.get)
    gm = fits[k_best]
    t_gmm = time.time() - t0
    # order clusters by mean HH (dark first) on training sample, for readable, comparable colours
    lab_tr = gmm_predict(xs, gm, "cpu")[0].long()
    hh = flat(ts.st.x[:, :1]).squeeze(1)[tr]
    order = sorted(range(k_best), key=lambda c: hh[lab_tr == c].mean().item() if (lab_tr == c).any() else 9)
    o = torch.tensor(order)
    gm = {**gm, "weights": gm["weights"][o], "means": gm["means"][o], "covs": gm["covs"][o]}
    others = [fit_gmm(xs, k_best, seed=s) for s in seeds[1:]]
    print(f"[{name}] D={X.shape[1]} -> whitened {wh.dim}; BIC best k={k_best}; GMM fits {t_gmm:.1f}s; "
          f"feature inference {t_inf:.1f}s", flush=True)
    return {"f": f, "X": X, "wh": wh, "gmm": gm, "others": others, "bics": bics, "k": k_best,
            "t_inf": t_inf, "t_gmm": t_gmm, "info": info}


def assign_scene(fm: dict, X: torch.Tensor, T, H, W, dev):
    t0 = time.time()
    lab, pmax, ll = gmm_predict(fm["wh"](X.to(dev)), fm["gmm"], dev)
    sync(dev)
    return lab.reshape(T, H, W), pmax.reshape(T, H, W), ll.reshape(T, H, W), time.time() - t0


def evaluate_tonle(name, fm, ts: Scene, dev) -> dict:
    T, H, W = ts.T, ts.H, ts.W
    lab, pmax, ll, t_assign = assign_scene(fm, fm["X"], T, H, W, dev)
    fm.update(lab=lab, pmax=pmax, ll=ll)
    v = ts.valid
    K = fm["k"]
    westm = v & (~ts.east)[None]
    eastm = v & ts.east[None]
    # designate clusters from the WEST part's math labels (a cluster is "water" if >= 50 % of its west
    # pixel-dates are math water); the east hold-out is never used for naming
    wf, ff, hh_m, hv_m = [], [], [], []
    curves = np.zeros((K, T, 2))
    for c in range(K):
        m = (lab == c) & westm
        n = max(m.sum().item(), 1)
        wf.append(((ts.lab == WATER) & m).sum().item() / n)
        ff.append(((ts.lab == FVEG) & m).sum().item() / n)
        mm = (lab == c) & v
        hh_m.append(ts.st.x[:, 0][mm].mean().item() if mm.any() else float("nan"))
        hv_m.append(ts.st.x[:, 1][mm].mean().item() if mm.any() else float("nan"))
        for t in range(T):
            mt = mm[t]
            if mt.any():
                curves[c, t] = [ts.st.x[t, 0][mt].mean().item(), ts.st.x[t, 1][mt].mean().item()]
            else:
                curves[c, t] = np.nan
    water_cl = [c for c in range(K) if wf[c] >= 0.5]
    fveg_cl = [c for c in range(K) if ff[c] >= 0.3 and c not in water_cl]
    names = [name_cluster(hh_m[c], hv_m[c], wf[c], ff[c]) for c in range(K)]
    frac = [((lab == c) & v).sum().item() / v.sum().item() for c in range(K)]
    fm.update(water_cl=water_cl, fveg_cl=fveg_cl, names=names, curves=curves, frac=frac)
    # (a) agreement with the math detector, east hold-out, all dates
    a = lab[eastm].numpy()
    b = ts.lab[eastm].numpy()
    wmask = torch.isin(lab, torch.tensor(water_cl, dtype=lab.dtype)) if water_cl else torch.zeros_like(v)
    fmask = torch.isin(lab, torch.tensor(fveg_cl, dtype=lab.dtype)) if fveg_cl else torch.zeros_like(v)
    best = {}
    for cls, nm in ((WATER, "water"), (FVEG, "flooded_veg")):
        ref = (ts.lab == cls) & eastm
        best[nm] = max(iou((lab == c) & eastm, ref) for c in range(K))
    res = {"k": K, "bic": {str(k): v_ for k, v_ in fm["bics"].items()}, "whitened_dim": fm["wh"].dim,
           "feature_dim": fm["info"]["dim"], "cluster_frac": frac, "cluster_names": names,
           "water_clusters": water_cl, "fveg_clusters": fveg_cl, "cluster_west_math_water_frac": wf,
           "cluster_west_math_fveg_frac": ff,
           "ari_math": ari(a, b), "nmi_math": nmi(a, b),
           "best_cluster_iou_water": best["water"], "best_cluster_iou_fveg": best["flooded_veg"],
           "water_union_iou": iou(wmask & eastm, (ts.lab == WATER) & eastm),
           "fveg_union_iou": iou(fmask & eastm, (ts.lab == FVEG) & eastm) if fveg_cl else 0.0}
    # (b) independent JRC reference (whole Tonle Sap scene, valid pixel-dates)
    res.update(jrc_metrics(wmask & v, v, ts))
    # (d) stability: ARI between seeds on an east-hold-out sample
    idx = sample_idx(eastm, 300_000, 7)
    xe = fm["wh"](fm["X"][idx].to(dev))
    l0 = gmm_predict(xe, fm["gmm"], dev)[0].numpy()
    res["seed_ari"] = [ari(l0, gmm_predict(xe, o, dev)[0].numpy()) for o in fm["others"]]
    res["seed_ari_mean"] = float(np.mean(res["seed_ari"]))
    # (e) cost
    info = fm["info"]
    res.update({"params": info["params"], "params_encoder": info.get("params_encoder", info["params"]),
                "train_time_s": info["train_time_s"], "weights_mb": info["weights_mb"],
                "infer_features_s": fm["t_inf"], "infer_gmm_assign_s": t_assign,
                "infer_total_s": fm["t_inf"] + t_assign, "gmm_fit_bic_s": fm["t_gmm"]})
    return res


def jrc_metrics(wmask, v, sc: Scene) -> dict:
    pos, neg = sc.pos[None] & v, sc.neg[None] & v
    tp, fp = (wmask & pos).sum().item(), (wmask & neg).sum().item()
    nvalid = v.sum(0)
    freq = wmask.sum(0).float() / nvalid.clamp_min(1)
    keep = sc.dom & (nvalid >= 6)
    from scipy.stats import spearmanr
    idx = sample_idx(keep, 200_000, 3)
    rho = spearmanr(sc.occ.reshape(-1)[idx].numpy(), freq.reshape(-1)[idx].numpy()).statistic
    return {"jrc_anchor_purity": tp / max(tp + fp, 1), "jrc_perm_recall": tp / max(pos.sum().item(), 1),
            "jrc_never_false_rate": fp / max(neg.sum().item(), 1), "jrc_spearman_occ_vs_waterfreq": float(rho)}


def evaluate_sevan(fm, sv: Scene, kind, variant, dev) -> dict:
    f, _, _ = method_features(kind, variant, sv, dev)
    X = flat(f)
    lab, pmax, ll, _ = assign_scene(fm, X, sv.T, sv.H, sv.W, dev)
    v = sv.valid
    wc = fm["water_cl"]
    wmask = (torch.isin(lab, torch.tensor(wc, dtype=lab.dtype)) if wc else torch.zeros_like(v)) & v
    km2 = sv.km2
    water = [wmask[t].sum().item() * km2 for t in range(sv.T)]
    lcc = [largest_cc(wmask[t].numpy()) * km2 for t in range(sv.T)]
    far = [(wmask[t] & sv.neg).sum().item() * km2 for t in range(sv.T)]
    out = {"dates": sv.st.dates, "water_km2": water, "largest_body_km2": lcc, "false_water_far_km2": far,
           "largest_body_mae_km2": float(np.mean([abs(x - SEVAN_TRUE_KM2) for x in lcc])),
           "total_water_mae_km2": float(np.mean([abs(x - SEVAN_TRUE_KM2) for x in water])),
           "mean_false_water_far_km2": float(np.mean(far))}
    out.update({f"sevan_{k}": val for k, val in jrc_metrics(wmask, v, sv).items()})
    fm["sevan_lab"], fm["sevan_wmask"], fm["sevan_ll"] = lab, wmask, ll
    return out


def math_reference_rows(ts: Scene, sv: Scene) -> dict:
    """The math detector scored with the same JRC / Sevan code, for context."""
    r = jrc_metrics(ts.math_water, ts.valid, ts)
    w = sv.math_water
    km2 = sv.km2
    r["sevan"] = {"water_km2": [w[t].sum().item() * km2 for t in range(sv.T)],
                  "largest_body_km2": [largest_cc(w[t].numpy()) * km2 for t in range(sv.T)],
                  "false_water_far_km2": [(w[t] & sv.neg).sum().item() * km2 for t in range(sv.T)]}
    r["sevan"]["largest_body_mae_km2"] = float(np.mean([abs(x - SEVAN_TRUE_KM2) for x in r["sevan"]["largest_body_km2"]]))
    r["sevan"]["mean_false_water_far_km2"] = float(np.mean(r["sevan"]["false_water_far_km2"]))
    return r


def linear_probe(fm, ts: Scene, ns=(100, 1000, 10000), seeds=(0, 1, 2)) -> dict:
    """Frozen per-pixel features -> logistic regression on n math-labelled WEST pixel-dates (class-balanced:
    n/3 per class), scored on 200,000 EAST pixel-dates (mean IoU over the 3 classes, pixel agreement)."""
    X = fm["X"]
    v = ts.valid
    y = ts.lab.reshape(-1).long()
    west = (v & (~ts.east)[None]).reshape(-1)
    east = (v & ts.east[None]).reshape(-1)
    te = sample_idx(east.reshape(v.shape), 200_000, 11)
    xte, yte = X[te].double(), y[te]
    yw = y[west]
    log_prior = torch.log(torch.bincount(yw, minlength=3).double() / yw.numel() * 3)
    out = {}
    for n in ns:
        mi, ag, wi = [], [], []
        for s in seeds:
            tr = torch.cat([sample_idx((west & (y == c)).reshape(v.shape), n // 3, 100 + s) for c in range(3)])
            pred = logistic_probe(X[tr].double(), y[tr], xte, log_prior=log_prior)
            mi.append(miou3(pred, yte))
            wi.append(iou(pred == WATER, yte == WATER))
            ag.append((pred == yte).double().mean().item())
        out[str(n)] = {"miou": float(np.mean(mi)), "miou_sd": float(np.std(mi)), "agree": float(np.mean(ag)),
                  "iou_water": float(np.mean(wi))}
    return out


def cmd_sweep(args):
    """Train every variant (missing weights only unless --force), `jobs` at a time, as subprocesses."""
    todo = [v for v in VARIANTS if args.force or not (MODELS / f"deep_ae_{v}.pt").exists()]
    if args.variants:
        todo = [v for v in args.variants.split(",") if v in todo or args.force]
    print("training:", todo)
    procs, logs = [], ROOT / "data" / "processed" / "models" / "runs"
    logs.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    queue = list(todo)
    while queue or procs:
        while queue and len(procs) < args.jobs:
            v = queue.pop(0)
            cmd = [sys.executable, "-m", "scripts.detect.deep_ae", "train", "--area", "tonle-sap", "--variant", v]
            if args.epochs:
                cmd += ["--epochs", str(args.epochs)]
            fh = open(logs / f"deep_ae_{v}.log", "w")
            procs.append((v, subprocess.Popen(cmd, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT), fh))
        time.sleep(2)
        for p in list(procs):
            if p[1].poll() is not None:
                p[2].close()
                print(f"  {p[0]} finished (code {p[1].returncode}) at {time.time() - t0:.0f}s", flush=True)
                procs.remove(p)
    print(f"sweep {time.time() - t0:.0f}s")


def cmd_compare(args):
    dev = pick_device(args.device)
    seed_all(0)
    FIGS.mkdir(parents=True, exist_ok=True)
    t_all = time.time()
    ts, sv = Scene("tonle-sap"), Scene("sevan")
    print(f"scenes loaded {time.time() - t_all:.1f}s; anchors Tonle Sap: perm {ts.pos.sum().item()} px, never "
          f"{ts.neg.sum().item()} px; Sevan perm {sv.pos.sum().item()}, never {sv.neg.sum().item()}")
    results = {"methods": {}, "math_detector": math_reference_rows(ts, sv), "dates": ts.st.dates,
               "sevan_dates": sv.st.dates, "notes": "see docs/eda/deep-ae.md"}
    fms = {}
    pick = [m.strip() for m in args.methods.split(",")] if args.methods else None
    for name, (kind, variant) in METHODS.items():
        if pick and SHORT[name] not in pick:
            continue
        if kind == "deep" and not (MODELS / f"deep_ae_{variant}.pt").exists():
            print(f"skip {name}: no weights (run `train --variant {variant}`)")
            continue
        fm = fit_method(name, kind, variant, ts, dev)
        r = evaluate_tonle(name, fm, ts, dev)
        r["sevan"] = evaluate_sevan(fm, sv, kind, variant, dev)
        r["probe"] = linear_probe(fm, ts)
        r["variant"] = variant
        if kind == "deep":
            r["history"] = fm["info"]["history"]
            r["cfg"] = fm["info"]["cfg"]
            emb_sd = fm["X"][sample_idx(ts.valid, 100_000, 5)].float().std(0)
            r["embedding_channel_sd"] = emb_sd.tolist()
        results["methods"][name] = r
        print(f"  ARI {r['ari_math']:.3f} NMI {r['nmi_math']:.3f} best IoU water {r['best_cluster_iou_water']:.3f} "
              f"fveg {r['best_cluster_iou_fveg']:.3f} water-union {r['water_union_iou']:.3f} | JRC purity "
              f"{r['jrc_anchor_purity']:.3f} rho {r['jrc_spearman_occ_vs_waterfreq']:.3f} | seed ARI "
              f"{r['seed_ari_mean']:.3f} | Sevan body MAE {r['sevan']['largest_body_mae_km2']:.0f} far-false "
              f"{r['sevan']['mean_false_water_far_km2']:.0f} | probe " +
              " ".join(f"{n}:{p['miou']:.3f}" for n, p in r["probe"].items()), flush=True)
        # keep only what the figures need
        fms[name] = {k: fm[k] for k in ("lab", "ll", "water_cl", "names", "curves", "frac", "k", "sevan_wmask",
                                         "sevan_ll")}
        if variant == MAIN or kind.startswith("raw"):
            torch.save({"whiten": fm["wh"].state(), "gmm": fm["gmm"], "water_clusters": fm["water_cl"],
                        "fveg_clusters": fm["fveg_cl"], "cluster_names": fm["names"], "kind": kind,
                        "variant": variant}, MODELS / f"deep_ae_{variant or kind}_gmm.pt")
        del fm
    results["total_compare_s"] = time.time() - t_all
    (FIGS / "results.json").write_text(json.dumps(results, indent=1, default=float))
    print(f"wrote {FIGS / 'results.json'}  ({time.time() - t_all:.0f}s)")
    make_figures(results, fms, ts, sv)
    print_table(results)


def print_table(results):
    print("\n| Method | k | ARI | NMI | IoU water (best / union) | IoU fl-veg | JRC purity | JRC rho | seed ARI | "
          "Sevan body MAE | Sevan far-false km2 | params | train s | infer s | MB |")
    for n, r in results["methods"].items():
        print(f"| {n} | {r['k']} | {r['ari_math']:.3f} | {r['nmi_math']:.3f} | {r['best_cluster_iou_water']:.3f} / "
              f"{r['water_union_iou']:.3f} | {r['best_cluster_iou_fveg']:.3f} | {r['jrc_anchor_purity']:.3f} | "
              f"{r['jrc_spearman_occ_vs_waterfreq']:.3f} | {r['seed_ari_mean']:.3f} | "
              f"{r['sevan']['largest_body_mae_km2']:.0f} | {r['sevan']['mean_false_water_far_km2']:.1f} | "
              f"{r['params']:,} | {r['train_time_s']:.0f} | {r['infer_total_s']:.1f} | {r['weights_mb']:.2f} |")


# ----------------------------------------------------------------------------- figures
def savefig(fig, name):
    FIGS.mkdir(parents=True, exist_ok=True)
    p = FIGS / name
    fig.savefig(p, dpi=85, bbox_inches="tight")
    plt.close(fig)
    if p.stat().st_size > 380_000:
        from PIL import Image
        Image.open(p).convert("RGB").quantize(256).save(p, optimize=True)
    print(f"  figure {p.relative_to(ROOT)} ({p.stat().st_size // 1024} KB)")


def make_figures(results, fms, ts: Scene, sv: Scene):
    M = results["methods"]
    names = list(M)
    # 1 training curves
    deep = [n for n in names if "history" in M[n]]
    if deep:
        fig, ax = plt.subplots(1, 2, figsize=(11, 3.6))
        for n in deep:
            h = M[n]["history"]
            e = range(1, len(h["train"]) + 1)
            l, = ax[0].plot(e, h["train"], "o-", ms=3, label=SHORT[n])
            ax[1].plot(e, np.array(h["val"]) / h["val_identity"], "s--", ms=3, color=l.get_color(), label=SHORT[n])
        ax[0].set(xlabel="epoch", ylabel="masked-reconstruction loss", title="Training loss (west 70 %)", yscale="log")
        ax[1].axhline(1, color="0.5", lw=0.8)
        ax[1].set(xlabel="epoch", ylabel="loss / loss of 'predict the mean'",
                  title="Held-out east patches (relative to trivial baseline)")
        ax[0].legend(fontsize=7), ax[1].legend(fontsize=7)
        ax[0].text(0.02, 0.02, "RGB variant has 3 targets, others 8: absolute losses not comparable",
                   transform=ax[0].transAxes, fontsize=7)
        savefig(fig, "training_curves.png")
    # 2 cluster maps side by side, one date
    t = min(range(ts.T), key=lambda i: abs(int(ts.st.dates[i][5:7]) * 31 + int(ts.st.dates[i][8:10]) - (9 * 31 + 23)))
    show = names[:]
    ncol = 4
    nrow = math.ceil((len(show) + 1) / ncol)
    fig, axs = plt.subplots(nrow, ncol, figsize=(4 * ncol, 4 * nrow))
    axs = axs.ravel()
    r0, r1, c0, c1 = int(ts.H * .35), int(ts.H * .85), int(ts.W * .45), int(ts.W * .95)
    from matplotlib.colors import ListedColormap
    mcls = ts.lab[t].float().numpy()
    mcls[~ts.valid[t].numpy()] = np.nan
    axs[0].imshow(mcls[r0:r1, c0:c1], cmap=ListedColormap(["#1f4fbf", "#2ca02c", "#d9d0b8"]), vmin=-.5, vmax=2.5,
                  interpolation="nearest")
    axs[0].set_title(f"math detector {ts.st.dates[t]}\nblue water, green flooded veg", fontsize=9)
    for i, n in enumerate(show):
        lab = fms[n]["lab"][t].float().numpy()
        lab[~ts.valid[t].numpy()] = np.nan
        k = fms[n]["k"]
        axs[i + 1].imshow(lab[r0:r1, c0:c1], cmap=plt.get_cmap("tab10", 10), vmin=-.5, vmax=9.5, interpolation="nearest")
        axs[i + 1].set_title(f"{SHORT[n]}: k={k}, water clusters {fms[n]['water_cl']}\nARI vs math "
                             f"{M[n]['ari_math']:.2f}", fontsize=9)
    for a in axs:
        a.set_xticks([]), a.set_yticks([])
    for a in axs[len(show) + 1:]:
        a.axis("off")
    axs[0].axvline((ts.split - c0), color="r", lw=1, ls="--")
    fig.suptitle("Tonle Sap, south-east part (red line: start of the east hold-out); clusters ordered dark -> bright",
                 fontsize=10)
    savefig(fig, "cluster_maps.png")
    # 3 cluster mean curves of the main model and the raw baseline
    pick = [n for n in names if M[n]["variant"] == MAIN] + [n for n in names if n.startswith("No network: raw")]
    fig, axs = plt.subplots(1, len(pick), figsize=(7 * len(pick), 4.2), squeeze=False)
    dts = as_dates(ts.st.dates)
    for a, n in zip(axs[0], pick):
        cur = fms[n]["curves"]
        shade_gap(a)
        for c in range(fms[n]["k"]):
            col = plt.get_cmap("tab10", 10)(c)
            a.plot(dts, cur[c, :, 0], "o-", ms=3, color=col, label=f"[{c}] {fms[n]['frac'][c] * 100:.0f}% {fms[n]['names'][c]}")
            a.plot(dts, cur[c, :, 1], ":", color=col)
        a.set(ylim=(0, 1), ylabel="mean brightness (solid HH, dotted HV)", title=f"{n}: cluster means per date")
        a.legend(fontsize=6, loc="upper left")
        a.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%b"))
    savefig(fig, "cluster_curves.png")
    # 4 Sevan transfer
    fig, axs = plt.subplots(1, 2, figsize=(13, 4))
    sd = as_dates(sv.st.dates)
    md = results["math_detector"]["sevan"]
    shade_gap(axs[0])
    axs[0].axhline(SEVAN_TRUE_KM2, color="k", lw=1, label="published 1,242 km²")
    axs[0].plot(sd, md["largest_body_km2"], "k--", lw=1.5, label="math detector")
    for n in names:
        axs[0].plot(sd, M[n]["sevan"]["largest_body_km2"], "o-", ms=3, label=SHORT[n])
        axs[1].plot(sd, M[n]["sevan"]["false_water_far_km2"], "o-", ms=3, label=SHORT[n])
    axs[1].plot(sd, md["false_water_far_km2"], "k--", lw=1.5, label="math detector")
    for a in axs:
        for d in ("2026-08-31", "2026-09-12"):
            a.axvline(np.datetime64(d), color="orange", lw=6, alpha=0.25)
        a.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%d %b"))
    axs[0].set(ylabel="km²", title="Lake Sevan, Tonle-Sap-fitted model: largest water-cluster body")
    axs[1].set(ylabel="km²", title=f"'Water' > {NEVER_DIST_PX} px from any JRC water (false), per date",
               yscale="symlog")
    axs[0].legend(fontsize=6, ncol=2)
    axs[1].text(0.01, 0.95, "orange: windy dates", transform=axs[1].transAxes, fontsize=7)
    savefig(fig, "sevan_transfer.png")
    # 5 comparison bars
    mets = [("ari_math", "ARI vs math (east)"), ("water_union_iou", "IoU water clusters (east)"),
            ("best_cluster_iou_fveg", "best IoU fl-veg (east)"), ("jrc_anchor_purity", "JRC anchor purity"),
            ("jrc_spearman_occ_vs_waterfreq", "JRC rank corr."), ("seed_ari_mean", "seed stability (ARI)")]
    fig, ax = plt.subplots(figsize=(13, 4))
    w = 0.8 / len(names)
    for i, n in enumerate(names):
        ax.bar(np.arange(len(mets)) + i * w, [M[n][m] for m, _ in mets], w, label=SHORT[n])
    ax.set_xticks(np.arange(len(mets)) + 0.4 - w / 2, [l for _, l in mets], fontsize=8)
    ax.set(ylim=(0, 1.05), title="Comparison (higher = better; all are agreement scores, none is accuracy)")
    ax.legend(fontsize=7, ncol=4)
    savefig(fig, "comparison_bars.png")
    # 6 linear probe
    fig, ax = plt.subplots(figsize=(6, 4))
    for n in names:
        p = M[n]["probe"]
        ns = [int(k) for k in p]
        ax.errorbar(ns, [p[k]["miou"] for k in p], yerr=[p[k]["miou_sd"] for k in p], marker="o", ms=4,
                    capsize=2, label=SHORT[n])
    ax.set(xscale="log", xlabel="labelled west pixels (balanced, from the math detector)",
           ylabel="east hold-out mean IoU (3 classes)", title="Linear probe: label efficiency")
    ax.legend(fontsize=7)
    savefig(fig, "linear_probe.png")
    # 7 anomaly maps (main model): Tonle Sap season max and Sevan
    mn = pick[0]
    fig, axs = plt.subplots(1, 2, figsize=(12, 5.5))
    for a, ll, sc, ttl in ((axs[0], fms[mn]["ll"], ts, "Tonle Sap"), (axs[1], fms[mn]["sevan_ll"], sv, "Lake Sevan")):
        an = (-ll).masked_fill(~sc.valid, float("nan"))
        a_max = torch.nan_to_num(an, nan=-1e9).amax(0).numpy()
        a_max[~sc.valid.any(0).numpy()] = np.nan
        lo, hi = np.nanpercentile(a_max, [5, 99.5])
        im = a.imshow(a_max, cmap="magma", vmin=lo, vmax=hi)
        a.set_title(f"{ttl}: season-max anomaly (-log GMM likelihood), {SHORT[mn]}", fontsize=9)
        a.set_xticks([]), a.set_yticks([])
        fig.colorbar(im, ax=a, shrink=0.7)
    savefig(fig, "anomaly_map.png")


# ----------------------------------------------------------------------------- predict
def cmd_predict(args):
    dev = pick_device(args.device)
    variant = args.variant or MAIN
    gpath = MODELS / f"deep_ae_{variant}_gmm.pt"
    if not gpath.exists():
        sys.exit(f"{gpath} missing: run `compare` first (it fits and saves the mixture)")
    gs = torch.load(gpath, map_location="cpu", weights_only=False)
    wh = Whiten.from_state(gs["whiten"])
    st = load_stack(args.area)
    model, ck = load_deep(variant, dev)
    t0 = time.time()
    f = deep_embeddings(model, net_inputs(ck["cfg"], st), dev)
    T, D, H, W = f.shape
    x = wh(flat(f).to(dev))
    lab, pmax, ll = gmm_predict(x, gs["gmm"], dev)
    wc = gs["water_clusters"]
    post_w = torch.zeros(x.shape[0])
    if wc:
        for i in range(0, x.shape[0], 1_000_000):
            post_w[i:i + 1_000_000] = gmm_posteriors(x[i:i + 1_000_000], gs["gmm"], dev)[:, wc].sum(1)
    sync(dev)
    lab, pmax, ll, post_w = (a.reshape(T, H, W) for a in (lab, pmax, ll, post_w))
    v = st.valid
    lab[~v] = 255
    wmask = torch.isin(lab, torch.tensor(wc, dtype=lab.dtype)) & v
    out = {"dates": st.dates, "bounds": st.bounds, "km2_per_px": st.km2_per_px, "cluster": lab,
           "p_cluster": pmax.masked_fill(~v, 0), "p_water": post_w.half().masked_fill(~v, 0),
           "cluster_names": gs["cluster_names"], "water_clusters": wc,
           "anomaly": (-ll).half().masked_fill(~v, float("nan")), "valid": v,
           "water_km2": torch.tensor([wmask[t].sum().item() * st.km2_per_px for t in range(T)]),
           "model": f"deep_ae_{variant} (Tonle Sap weights) + GMM", "notes":
           "cluster [T,H,W] uint8 (255 invalid); p_cluster = posterior of the assigned cluster; p_water = summed "
           "posterior of the water clusters; anomaly = -log mixture likelihood (whitened embedding space)"}
    p = ROOT / "data" / "processed" / args.area / "deep_ae" / "products.pt"
    p.parent.mkdir(parents=True, exist_ok=True)
    torch.save(out, p)
    print(f"{args.area}: {time.time() - t0:.1f}s, water km2 per date "
          f"{[round(x, 0) for x in out['water_km2'].tolist()]} -> {p}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["train", "sweep", "compare", "predict"])
    ap.add_argument("--area", default="tonle-sap")
    ap.add_argument("--variant", default=None)
    ap.add_argument("--variants", default=None, help="sweep: comma list")
    ap.add_argument("--epochs", type=int, default=None)
    ap.add_argument("--steps", type=int, default=None)
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--methods", default=None, help="compare: comma list of short names, e.g. 'raw GMM,TempAE'")
    ap.add_argument("--device", default=None)
    args = ap.parse_args(argv)
    if args.cmd == "train":
        args.variant = args.variant or MAIN
        cmd_train(args)
    elif args.cmd == "sweep":
        cmd_sweep(args)
    elif args.cmd == "compare":
        cmd_compare(args)
    else:
        cmd_predict(args)


if __name__ == "__main__":
    main()
