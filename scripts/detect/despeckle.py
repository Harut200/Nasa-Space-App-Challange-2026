"""Self-supervised radar despeckling (no labels): a learned replacement for the speckle filter.

Idea. Speckle is independent from one pass to the next while most of the ground is not, so two
dates are two noisy looks at (almost) the same scene. A network trained to predict one look from
the other (Noise2Noise) converges to the clean image, because the noise on the target is zero-mean
and independent of the input. Variants (see docs/eda/despeckle.md):

  n2n            plain L2 Noise2Noise: f(x_A) -> x_B, residual U-Net
  n2n_masked     same, but the target's low frequencies are replaced by the input's (change
                 compensation, SAR2SAR-style) and the loss is a Huber loss weighted by a
                 no-change probability from a coarse difference
  blindspot      single date, exact blind-spot network (Laine et al. 2019): the output at p never
                 sees the input in a (2s-1)x(2s-1) square around p. Bayesian combination with the
                 noisy pixel afterwards (posterior mean under a Gaussian noise model)
  multitemporal  learned Quegan filter: blind-spot features of the target date + attention over
                 all other dates (full resolution), Bayesian combination with the target pixel

Domain: preview brightness 0..1, a dB-like stretch of radar intensity, where speckle is
approximately ADDITIVE (0.02-0.05) and roughly level-independent (docs/eda/stats-change.md).

CLI (one process at a time):
    .venv/bin/python -m scripts.detect.despeckle noise
    .venv/bin/python -m scripts.detect.despeckle train --variant multitemporal --size S
    .venv/bin/python -m scripts.detect.despeckle evaluate
    .venv/bin/python -m scripts.detect.despeckle predict --area tonle-sap --variant multitemporal_S

Integration:
    xd = despeckle.denoise(st.x, st.valid, variant="multitemporal_S", dates=st.dates)
    out = ops.run_pipeline(xd, st.valid, "preview", kn={**ops.KNOBS["preview"], "box": 1, "quegan": False})
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import pathlib
import sys
import time

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.detect import ops  # noqa: E402

MODELS = ROOT / "data/processed/models"
CACHE = MODELS / "despeckle_cache"
OUT = ROOT / "docs/eda/despeckle"
D_DB = 30.0                      # assumed dB span of the preview stretch (stats-change.md)
HOLDOUT_AREA = "ararat"          # whole area never seen in training
TRAIN_COLS = {"tonle-sap": 0.42, "sevan": 0.45}   # tune strip only (tune.SPLIT); val/test strips held out
DEFAULT_TRAIN_COLS = 0.70        # other areas: train on the left 70 %, validate on the rest
BUF = 12                         # px gap between train and held-out columns
EXCLUDE_AREAS = {"demo"}
SEED = 20261114


# ============================================================================ data
def day_numbers(dates):
    return [(_dt.date.fromisoformat(d) - _dt.date(2026, 1, 1)).days for d in dates]


def footprint(x, valid):
    """ops footprint rules: clean input, no-data margin, common footprint, edge erosion.
    Returns (x clean [T,2,H,W], v_margin [T,H,W] per date, fp [H,W] eroded common footprint)."""
    kn = ops.KNOBS["preview"]
    x, v = ops.clean_input(x.float(), valid.bool())
    v = v & ~ops.nodata_margin(x, v, kn["nodata_floor"])
    common = v & v.all(0, keepdim=True)
    fp = ops.erode(common, 2 * kn["edge_erode_px"] + 1)[0]
    return x, v, fp


def list_areas():
    out = []
    for p in sorted((ROOT / "areas").iterdir()):
        if p.name in EXCLUDE_AREAS or not (p / "area.json").exists():
            continue
        a = json.loads((p / "area.json").read_text())
        if a.get("track") is None:
            continue
        out.append(p.name)
    return out


def load_area(area):
    """Cached {x float16 [T,2,H,W], v [T,H,W], fp [H,W], dates, bounds, km2}."""
    path = CACHE / f"{area}.pt"
    if path.exists():
        return torch.load(path, weights_only=False)
    from scripts.detect.stack import load_preview_stack
    st = load_preview_stack(area)
    x, v, fp = footprint(st.x, st.valid)
    d = {"area": area, "x": x.half(), "v": v, "fp": fp, "dates": st.dates, "bounds": st.bounds,
         "km2": st.km2_per_px}
    CACHE.mkdir(parents=True, exist_ok=True)
    torch.save(d, path)
    return d


def column_masks(area, H, W):
    """(train, held-out) [H, W] bool."""
    tr = torch.zeros(H, W, dtype=torch.bool)
    ho = torch.zeros(H, W, dtype=torch.bool)
    if area == HOLDOUT_AREA:
        ho[:] = True
        return tr, ho
    c = round(TRAIN_COLS.get(area, DEFAULT_TRAIN_COLS) * W)
    tr[:, : max(c - BUF // 2, 0)] = True
    ho[:, c + BUF // 2:] = True
    return tr, ho


def fill_invalid(x, v):
    """Replace invalid pixels by a smooth extension (temporal mean, then wide box mean, then global mean)."""
    x = x.clone()
    vf = v[:, None].float()
    n = v.float().sum(0)
    tm = (x * vf).sum(0) / n.clamp_min(1)
    has = n > 0
    for t in range(x.shape[0]):
        bad = ~v[t]
        if bad.any():
            x[t][:, bad & has] = tm[:, bad & has]
    anyv = v.any(0)
    if (~anyv).any():
        bm = ops.box_mean(tm[None], anyv[None], 31)[0]
        ok = ops.box_mean(anyv[None, None].float(), anyv[None], 31)[0, 0] > 0
        g = tm[:, anyv].mean(1) if anyv.any() else torch.full((x.shape[1],), 0.3)
        fillv = torch.where(ok[None], bm, g[:, None, None].expand_as(bm))
        x[:, :, ~anyv] = fillv[:, ~anyv][None]
    return x


# ============================================================================ noise model
def noise_from_differences(x, v, fp, nb=20, mask=None):
    """Per-channel noise std as a function of brightness level, from consecutive-date differences.

    d = x_{t+1} - x_t has the variance of two independent noises (2 sigma^2) where the ground is
    stable. Stable = |9x9 mean of d| small; sigma(level) = 1.4826 * MAD(d) / sqrt(2) per level bin.
    """
    T = x.shape[0]
    m = fp if mask is None else fp & mask
    num = torch.zeros(2, nb)
    edges = torch.linspace(0, 1, nb + 1)
    samples = [[[] for _ in range(nb)] for _ in range(2)]
    for t in range(T - 1):
        a, b = x[t].float(), x[t + 1].float()
        d = b - a
        vv = (v[t] & v[t + 1] & m)
        lo = ops.box_mean(d[None], vv[None], 9)[0]
        lev = ops.box_mean(((a + b) / 2)[None], vv[None], 5)[0]
        for c in range(2):
            ok = vv & (lo[c].abs() < 0.015)
            dc, lc = d[c][ok], lev[c][ok]
            if dc.numel() > 400_000:
                idx = torch.randperm(dc.numel(), generator=torch.Generator().manual_seed(t))[:400_000]
                dc, lc = dc[idx], lc[idx]
            bi = torch.bucketize(lc, edges[1:-1])
            for k in range(nb):
                samples[c][k].append(dc[bi == k])
    sig = torch.full((2, nb), float("nan"))
    for c in range(2):
        for k in range(nb):
            s = torch.cat(samples[c][k]) if samples[c][k] else torch.zeros(0)
            num[c, k] = s.numel()
            if s.numel() >= 200:
                sig[c, k] = 1.4826 * (s - s.median()).abs().median() / math.sqrt(2)
    return sig, num


def finish_table(sig):
    """Fill empty bins by nearest neighbour, clamp to a small minimum."""
    out = sig.clone()
    for c in range(out.shape[0]):
        ok = torch.isfinite(out[c])
        idx = torch.arange(out.shape[1])
        if ok.sum() == 0:
            out[c] = 0.03
            continue
        good = idx[ok]
        for k in idx[~ok]:
            out[c, k] = out[c, good[(good - k).abs().argmin()]]
    return out.clamp_min(0.004)


def acf2d(d, m, maxlag=5):
    """Normalised spatial autocorrelation of d over mask m, lags -maxlag..maxlag (FFT, masked)."""
    d = torch.where(m, d - d[m].mean(), torch.zeros_like(d)).double()
    mf = m.double()
    H, W = d.shape
    s = (H + maxlag * 2, W + maxlag * 2)
    Fd, Fm = torch.fft.rfft2(d, s=s), torch.fft.rfft2(mf, s=s)
    num = torch.fft.irfft2(Fd * Fd.conj(), s=s)
    den = torch.fft.irfft2(Fm * Fm.conj(), s=s)
    r = num / den.clamp_min(1.0)
    idx = torch.arange(-maxlag, maxlag + 1)
    out = r[idx % s[0]][:, idx % s[1]]
    return (out / out[maxlag, maxlag]).float()


def noise_acf(x, v, fp, maxlag=5):
    """ACF of the noise from consecutive-date differences on stable, homogeneous pixels (per channel)."""
    T = x.shape[0]
    tm = x.float().mean(0)
    sp = torch.stack([ops.box_mean((tm[c] ** 2)[None, None], fp[None], 7)[0, 0]
                      - ops.box_mean(tm[c][None, None], fp[None], 7)[0, 0] ** 2 for c in range(2)]).clamp_min(0).sqrt()
    acc = torch.zeros(2, 2 * maxlag + 1, 2 * maxlag + 1)
    for c in range(2):
        hom = fp & (tm[c] > 0.1) & (sp[c] <= torch.quantile(sp[c][fp & (tm[c] > 0.1)][::7], 0.3))
        for t in range(T - 1):
            d = (x[t + 1, c] - x[t, c]).float()
            lo = ops.box_mean(d[None, None], fp[None], 9)[0, 0]
            m = hom & (lo.abs() < 0.015)
            m = ops.erode(m[None], 3)[0]
            acc[c] += acf2d(d, m, maxlag)
    return acc / (T - 1)


def sigma_lookup(table, mu):
    """table [2, nb] noise std per level bin (uniform on 0..1); mu [B,2,H,W] -> sigma [B,2,H,W]."""
    nb = table.shape[1]
    pos = (mu.clamp(0, 1) * nb - 0.5).clamp(0, nb - 1)
    i0 = pos.floor().long().clamp(0, nb - 2)
    f = pos - i0
    tab = table.to(mu.device)
    out = []
    for c in range(mu.shape[1]):
        tc = tab[c]
        out.append(tc[i0[:, c]] * (1 - f[:, c]) + tc[i0[:, c] + 1] * f[:, c])
    return torch.stack(out, 1)


def enl_noise_sigma(L):
    """Std in brightness of 10log10(Gamma(L,1/L)) / D_DB (additive noise in a dB stretch)."""
    return (10 / math.log(10)) * math.sqrt(float(torch.special.polygamma(1, torch.tensor(float(L))))) / D_DB


def enl_from_sigma(s):
    """Inverse: brightness std -> equivalent number of looks (bisection on trigamma)."""
    target = (s * D_DB * math.log(10) / 10) ** 2
    lo, hi = 0.05, 1e6
    for _ in range(100):
        mid = math.sqrt(lo * hi)
        if float(torch.special.polygamma(1, torch.tensor(mid, dtype=torch.float64))) > target:
            lo = mid
        else:
            hi = mid
    return math.sqrt(lo * hi)


def log_gamma_noise(shape, L, gen):
    """Zero-mean additive brightness noise of an L-look speckle in a D_DB dB stretch (white)."""
    g = torch._standard_gamma(torch.full(shape, float(L)), generator=gen) / L
    n = 10 * torch.log10(g.clamp_min(1e-12)) / D_DB
    bias = (10 / math.log(10)) * (float(torch.special.digamma(torch.tensor(float(L)))) - math.log(L)) / D_DB
    return n - bias


# ============================================================================ networks
def act():
    return nn.LeakyReLU(0.1)


class Block(nn.Module):
    def __init__(self, ci, co):
        super().__init__()
        self.c1, self.c2 = nn.Conv2d(ci, co, 3, padding=1), nn.Conv2d(co, co, 3, padding=1)
        self.skip = nn.Conv2d(ci, co, 1) if ci != co else nn.Identity()
        self.a = act()

    def forward(self, x):
        return self.a(self.c2(self.a(self.c1(x))) + self.skip(x))


class ResUNet(nn.Module):
    """Residual U-Net: out = x + g(x). Both channels jointly."""

    def __init__(self, cin=2, cout=2, base=16, depth=3):
        super().__init__()
        w = [base * 2 ** i for i in range(depth)]
        self.inc = Block(cin, w[0])
        self.down = nn.ModuleList([Block(w[i - 1], w[i]) for i in range(1, depth)])
        self.up = nn.ModuleList([Block(w[i] + w[i - 1], w[i - 1]) for i in range(depth - 1, 0, -1)])
        self.out = nn.Conv2d(w[0], cout, 1)
        nn.init.zeros_(self.out.weight)
        nn.init.zeros_(self.out.bias)
        self.cout = cout
        self.mult = 2 ** (depth - 1)

    def forward(self, x):
        H, W = x.shape[-2:]
        ph, pw = (-H) % self.mult, (-W) % self.mult
        xi = F.pad(x, (0, pw, 0, ph), mode="replicate") if ph or pw else x
        hs = [self.inc(xi)]
        for d in self.down:
            hs.append(d(F.avg_pool2d(hs[-1], 2)))
        h = hs.pop()
        for u in self.up:
            skip = hs.pop()
            h = u(torch.cat([F.interpolate(h, size=skip.shape[-2:], mode="bilinear", align_corners=False), skip], 1))
        y = xi[:, : self.cout] + self.out(h)
        return y[..., :H, :W]


class UpConv(nn.Module):
    """Dilated 3x3 conv with its bottom kernel row zeroed: output row y depends only on input rows <= y."""

    def __init__(self, ci, co, d):
        super().__init__()
        self.d = d
        self.conv = nn.Conv2d(ci, co, 3, padding=d, dilation=d)
        m = torch.ones(1, 1, 3, 3)
        m[:, :, 2, :] = 0
        self.register_buffer("m", m)

    def forward(self, x):
        return F.conv2d(x, self.conv.weight * self.m, self.conv.bias, padding=self.d, dilation=self.d)


class BlindFeatures(nn.Module):
    """Exact blind-spot features. Four rotated copies of an 'upward-looking' branch (shared weights),
    each shifted by `shift` rows, see only pixels with |dy| >= shift or |dx| >= shift: the output at p
    never depends on the input inside the (2*shift-1)^2 square centred on p."""

    def __init__(self, cin, width, dil=(1, 1, 2, 2, 4, 4, 8, 8), shift=1):
        super().__init__()
        self.first = UpConv(cin, width, 1)
        self.layers = nn.ModuleList([UpConv(width, width, d) for d in dil])
        self.a = act()
        self.shift = shift
        self.mix = nn.Sequential(nn.Conv2d(4 * width, 2 * width, 1), act(), nn.Conv2d(2 * width, width, 1), act())

    def branch(self, x):
        h = self.a(self.first(x))
        for layer in self.layers:
            h = h + self.a(layer(h))
        s = self.shift
        return F.pad(h, (0, 0, s, 0))[:, :, : h.shape[2]]

    def forward(self, x):
        if x.shape[-1] == x.shape[-2]:   # square: run the four rotations as one batch
            h = self.branch(torch.cat([torch.rot90(x, k, (2, 3)) for k in range(4)])).chunk(4)
            outs = [torch.rot90(hk, -k, (2, 3)) for k, hk in enumerate(h)]
        else:
            outs = [torch.rot90(self.branch(torch.rot90(x, k, (2, 3))), -k, (2, 3)) for k in range(4)]
        return self.mix(torch.cat(outs, 1))


class BlindSpotNet(nn.Module):
    """Single-date blind-spot network: (mu, log s^2) of the clean pixel given its surroundings only."""

    def __init__(self, width=32, shift=1, dil=(1, 1, 2, 2, 4, 4, 8, 8)):
        super().__init__()
        self.feat = BlindFeatures(2, width, dil, shift)
        self.head = nn.Conv2d(width, 4, 1)

    def forward(self, x):
        o = self.head(self.feat(x))
        return o[:, :2], o[:, 2:].clamp(-14, 2)


class MTNet(nn.Module):
    """Learned multi-temporal filter.

    target date t : blind-spot features f_t (cannot see x_t near p)
    other dates   : encoder g_tau (full resolution, sees x_tau at p)
    attention     : alpha_tau(p) = softmax_tau MLP([f_t, g_tau, dt]) over valid tau != t (+ a null slot)
    output        : (mu, log s^2) = head([f_t, sum_tau alpha_tau v_tau])
    """

    def __init__(self, width=32, heads=4, shift=3, dil=(1, 1, 2, 2, 4, 4, 8, 8)):
        super().__init__()
        assert width % heads == 0
        self.w, self.h = width, heads
        self.blind = BlindFeatures(3, width, dil, shift)
        self.enc = nn.Sequential(nn.Conv2d(3, width, 3, padding=1), act(),
                                 nn.Conv2d(width, width, 3, padding=1), act(),
                                 nn.Conv2d(width, width, 3, padding=2, dilation=2), act(),
                                 nn.Conv2d(width, width, 3, padding=1), act())
        self.key = nn.Sequential(nn.Conv2d(2 * width + 2, width, 1), act(), nn.Conv2d(width, heads, 1))
        self.val = nn.Sequential(nn.Conv2d(width + 2, width, 1), act(), nn.Conv2d(width, width, 1))
        self.head = nn.Sequential(nn.Conv2d(2 * width + heads, width, 1), act(), nn.Conv2d(width, 4, 1))

    def _attend(self, f, g, xo, vo, dt):
        """f [B,w,H,W]; g [B,T,w,H,W]; xo [B,T,2,H,W]; vo [B,T,H,W] bool (False = excluded); dt [B,T]."""
        B, T, w, H, W = g.shape
        dtp = dt[:, :, None, None, None].expand(B, T, 1, H, W)
        k_in = torch.cat([f[:, None].expand(B, T, w, H, W), g, dtp / 100.0, dtp.abs() / 100.0], 2)
        logit = self.key(k_in.reshape(B * T, 2 * w + 2, H, W)).reshape(B, T, self.h, H, W)
        logit = logit.masked_fill(~vo[:, :, None], float("-inf"))
        null = torch.zeros(B, 1, self.h, H, W, device=f.device, dtype=f.dtype)
        a = torch.softmax(torch.cat([null, logit], 1), 1)          # [B, T+1, heads, H, W]
        val = self.val(torch.cat([g, xo], 2).reshape(B * T, w + 2, H, W)).reshape(B, T, self.h, w // self.h, H, W)
        hagg = (a[:, 1:, :, None] * val).sum(1).reshape(B, w, H, W)
        o = self.head(torch.cat([f, hagg, a[:, 0]], 1))
        return o[:, :2], o[:, 2:].clamp(-14, 2), a

    def forward(self, xs, vs, days, t_idx):
        """xs [B,T,2,H,W]; vs [B,T,H,W] bool; days [B,T] float; t_idx [B] long -> mu, logs2 [B,2,H,W]."""
        B, T, _, H, W = xs.shape
        ar = torch.arange(B, device=xs.device)
        xt, vt = xs[ar, t_idx], vs[ar, t_idx]
        f = self.blind(torch.cat([xt * vt[:, None], vt[:, None].float()], 1))
        g = self.enc(torch.cat([xs * vs[:, :, None], vs[:, :, None].float()], 2).reshape(B * T, 3, H, W)).reshape(B, T, self.w, H, W)
        excl = vs.clone()
        excl[ar, t_idx] = False
        dt = days - days[ar, t_idx][:, None]
        mu, ls, _ = self._attend(f, g, xs, excl, dt)
        return mu, ls

    def forward_all(self, x, v, days, return_attn=False):
        """Inference for every date of one stack: x [T,2,H,W], v [T,H,W] -> mu, logs2 [T,2,H,W]."""
        T, _, H, W = x.shape
        f = self.blind(torch.cat([x * v[:, None], v[:, None].float()], 1))
        g = self.enc(torch.cat([x * v[:, None], v[:, None].float()], 1))
        mus, lss, atts = [], [], []
        for t in range(T):
            excl = v.clone()
            excl[t] = False
            dt = (days - days[t])[None]
            mu, ls, a = self._attend(f[t:t + 1], g[None], x[None], excl[None], dt)
            mus.append(mu[0])
            lss.append(ls[0])
            if return_attn:
                atts.append(a[0].mean(1))  # [T+1, H, W] averaged over heads
        out = (torch.stack(mus), torch.stack(lss))
        return out + (torch.stack(atts),) if return_attn else out


def n_params(m):
    return sum(p.numel() for p in m.parameters())


SIZES = {
    "n2n": {"S": {"base": 16, "depth": 3}, "L": {"base": 48, "depth": 3}},
    "n2n_masked": {"S": {"base": 16, "depth": 3}, "L": {"base": 48, "depth": 3}},
    "blindspot": {"S": {"width": 32}, "L": {"width": 64}},
    "multitemporal": {"S": {"width": 32}, "L": {"width": 64}},
}


def build(variant, size, shift=None):
    cfg = SIZES[variant][size]
    if variant in ("n2n", "n2n_masked"):
        return ResUNet(2, 2, cfg["base"], cfg["depth"])
    if variant == "blindspot":
        return BlindSpotNet(cfg["width"], shift or 1)
    return MTNet(cfg["width"], 4, shift or 3)


def model_name(variant, size, shift=None, enl=None):
    s = f"{variant}_{size}"
    if variant == "blindspot":
        s += f"_s{shift or 1}"
    if variant == "multitemporal" and shift not in (None, 3):
        s += f"_s{shift}"
    if enl:
        s += f"_enl{enl:g}"
    return s


# ============================================================================ training
class Sampler:
    """Random fully-valid P x P patches from the train (or held-out) columns of every area."""

    def __init__(self, areas, P, split, gen, data=None):
        self.P, self.gen = P, gen
        self.items = []
        for a in areas:
            d = data[a] if data is not None else load_area(a)
            H, W = d["fp"].shape
            tr, ho = column_masks(a, H, W)
            allow = d["fp"] & (tr if split == "train" else ho)
            if allow.sum() < P * P:
                continue
            ok = F.avg_pool2d(allow.float()[None, None], P, stride=1)[0, 0] > 0.999
            pos = ok.nonzero()
            pos = pos[::3]
            if len(pos) == 0:
                continue
            self.items.append((a, d, pos))
        w = torch.tensor([math.sqrt(len(p)) for _, _, p in self.items], dtype=torch.float64)
        self.w = w / w.sum()

    def batch(self, B):
        i = int(torch.multinomial(self.w, 1, generator=self.gen))
        a, d, pos = self.items[i]
        P = self.P
        pick = pos[torch.randint(0, len(pos), (B,), generator=self.gen)]
        xs = torch.stack([d["x"][:, :, r:r + P, c:c + P] for r, c in pick.tolist()]).float()  # [B,T,2,P,P]
        k = torch.randint(0, 4, (B,), generator=self.gen)
        fl = torch.rand(B, generator=self.gen) < 0.5
        out = []
        for b in range(B):
            xb = torch.rot90(xs[b], int(k[b]), (2, 3))
            if fl[b]:
                xb = xb.flip(-1)
            out.append(xb)
        days = torch.tensor(day_numbers(d["dates"]), dtype=torch.float32)
        return torch.stack(out), days[None].expand(B, -1).clone(), a


def gauss_nll(y, mu, logs2, sigma):
    var = logs2.exp() + sigma ** 2
    return 0.5 * (torch.log(var) + (y - mu) ** 2 / var)


def split_loss(y, mu, logs2, sigma):
    """Mean by plain L2 (= E[y | what the network sees], which equals E[clean | ...] when the noise of y is
    independent of the input), prior variance s^2 by the Gaussian NLL with the mean held fixed.
    A joint NLL lets a large early variance stall the mean (1/var scaling of its gradient)."""
    return F.mse_loss(mu, y) / 1e-3 + gauss_nll(y, mu.detach(), logs2, sigma).mean()


def posterior(y, mu, logs2, sigma):
    s2 = logs2.exp()
    n2 = sigma ** 2
    return (n2 * mu + s2 * y) / (n2 + s2)


def step_loss(variant, model, xs, days, gen, dev, table, enl=None):
    """One batch loss. xs [B,T,2,P,P] on CPU."""
    B, T = xs.shape[:2]
    if enl:
        xs = (xs + log_gamma_noise(xs.shape, enl, gen)).clamp(1 / 255, 1)
    if variant in ("n2n", "n2n_masked"):
        a = torch.randint(0, T, (B,), generator=gen)
        b = (a + torch.randint(1, T, (B,), generator=gen)) % T
        ar = torch.arange(B)
        xa, xb = xs[ar, a].to(dev), xs[ar, b].to(dev)
        pred = model(xa)
        if variant == "n2n":
            return F.mse_loss(pred, xb)
        k = 9
        ones = torch.ones(2, 1, k, k, device=dev) / (k * k)
        lp = lambda z: F.conv2d(F.pad(z, (k // 2,) * 4, mode="reflect"), ones, groups=2)  # noqa: E731
        tgt = xb - lp(xb) + lp(xa)                  # change compensation of the low frequencies
        o3 = torch.ones(2, 1, 3, 3, device=dev) / 9
        r = F.conv2d(F.pad(tgt - xa, (1,) * 4, mode="reflect"), o3, groups=2)
        tau = 3 * 1.4826 * r.abs().flatten(2).median(-1).values[..., None, None] + 1e-3
        w = torch.exp(-0.5 * (r / tau) ** 2).prod(1, keepdim=True).detach()   # no-change probability
        hub = F.huber_loss(pred, tgt, reduction="none", delta=0.05)
        return (w * hub).sum() / (w.sum() * 2 + 1e-6)
    amp = torch.autocast(device_type=dev.type, dtype=torch.float16, enabled=dev.type == "mps")
    if variant == "blindspot":
        t = torch.randint(0, T, (B,), generator=gen)
        y = xs[torch.arange(B), t].to(dev)
        with amp:
            mu, ls = model(y)
        mu, ls = mu.float(), ls.float()
        return split_loss(y, mu, ls, sigma_lookup(table, mu.detach()))
    # multitemporal
    t = torch.randint(0, T, (B,), generator=gen)
    vs = torch.rand(B, T, generator=gen) > 0.2                   # date dropout
    vs[torch.arange(B), t] = True
    P = xs.shape[-1]
    vs = vs[:, :, None, None].expand(B, T, P, P).contiguous()
    xs_d, vs_d = xs.to(dev), vs.to(dev)
    with amp:
        mu, ls = model(xs_d, vs_d, days.to(dev), t.to(dev))
    mu, ls = mu.float(), ls.float()
    y = xs_d[torch.arange(B, device=dev), t.to(dev)]
    return split_loss(y, mu, ls, sigma_lookup(table, mu.detach()))


def get_noise_table(enl=None):
    path = MODELS / "despeckle_noise.json"
    if not path.exists():
        raise SystemExit("run `python -m scripts.detect.despeckle noise` first")
    tab = torch.tensor(json.loads(path.read_text())["sigma_table"], dtype=torch.float32)
    if enl:
        tab = (tab ** 2 + enl_noise_sigma(enl) ** 2).sqrt()
    return tab


def free_memory_pct():
    import subprocess
    try:
        out = subprocess.run(["memory_pressure"], capture_output=True, text=True, timeout=20).stdout
        for line in out.splitlines():
            if "free percentage" in line:
                return int(line.split(":")[1].strip().rstrip("%"))
    except Exception:
        pass
    return 100


def train(variant, size="S", iters=3000, batch=None, patch=None, lr=1e-3, shift=None, enl=None, seed=SEED,
          areas=None, log_every=250, time_limit=600):
    dev = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    fm = free_memory_pct()
    batch = batch or ({"multitemporal": 12 if size == "S" else 8}.get(variant, 16))
    patch = patch or {"multitemporal": 64, "blindspot": 64}.get(variant, 96)
    if fm < 35:
        batch = max(4, batch // 2)
        print(f"  memory free {fm} %: batch halved to {batch}")
    torch.manual_seed(seed)
    gen = torch.Generator().manual_seed(seed)
    areas = areas or [a for a in list_areas() if a != HOLDOUT_AREA]
    data = {}
    for a in areas:
        try:
            data[a] = load_area(a)
        except Exception as e:  # an area still being added
            print(f"  skip {a}: {e}")
    areas = list(data)
    tr = Sampler(areas, patch, "train", gen, data)
    va = Sampler(areas, patch, "val", torch.Generator().manual_seed(seed + 1), data)
    table = get_noise_table(enl) if variant in ("blindspot", "multitemporal") else None
    model = build(variant, size, shift).to(dev)
    name = model_name(variant, size, shift, enl)
    print(f"train {name}: {n_params(model):,} params, {len(tr.items)} areas, batch {batch} x {patch}^2, {iters} iters, {dev}")
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=lr, total_steps=iters, pct_start=0.05)
    vgen_seed = seed + 7
    val_batches = [va.batch(batch) for _ in range(12)]
    log = []
    t0 = time.time()
    run, nrun = 0.0, 0
    done = 0
    skipped = 0
    for it in range(iters):
        model.train()
        xs, days, _ = tr.batch(batch)
        loss = step_loss(variant, model, xs, days, gen, dev, table, enl)
        opt.zero_grad(set_to_none=True)
        loss.backward()
        gn = torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        if not torch.isfinite(gn):          # fp16 overflow: skip this step
            opt.zero_grad(set_to_none=True)
            skipped += 1
        else:
            opt.step()
        sched.step()
        if torch.isfinite(loss):
            run += float(loss.detach())
            nrun += 1
        done = it + 1
        if done % log_every == 0 or done == iters:
            model.eval()
            vg = torch.Generator().manual_seed(vgen_seed)
            with torch.no_grad():
                vl = float(np.mean([float(step_loss(variant, model, xb, db, vg, dev, table, enl)) for xb, db, _ in val_batches]))
            el = time.time() - t0
            log.append({"iter": done, "train": run / max(nrun, 1), "skipped": skipped, "val": vl, "sec": round(el, 1)})
            print(f"  {done:5d}  train {run / max(nrun, 1):.6f}  val {vl:.6f}  {el:.0f}s  skipped {skipped}")
            run, nrun = 0.0, 0
            if el > time_limit:
                print(f"  time limit {time_limit}s reached")
                break
    el = time.time() - t0
    MODELS.mkdir(parents=True, exist_ok=True)
    torch.save({"state": model.state_dict(), "variant": variant, "size": size, "shift": shift, "enl": enl,
                "params": n_params(model), "iters": done, "batch": batch, "patch": patch, "sec": el,
                "log": log, "areas": areas, "noise_table": None if table is None else table.tolist()},
               MODELS / f"despeckle_{name}.pt")
    print(f"  saved despeckle_{name}.pt ({el:.0f}s)")
    del model, opt
    if dev.type == "mps":
        torch.mps.empty_cache()
    return name


# ============================================================================ inference
_MODEL_CACHE = {}


def load_model(name, dev):
    key = (name, str(dev))
    if key not in _MODEL_CACHE:
        ck = torch.load(MODELS / f"despeckle_{name}.pt", weights_only=False, map_location="cpu")
        m = build(ck["variant"], ck["size"], ck["shift"])
        m.load_state_dict(ck["state"])
        m.eval().to(dev)
        tab = torch.tensor(ck["noise_table"]) if ck["noise_table"] is not None else None
        _MODEL_CACHE.clear()
        _MODEL_CACHE[key] = (m, ck, tab)
    return _MODEL_CACHE[key]


def _tiles(n, tile):
    if n <= tile:
        return [0]
    k = math.ceil(n / tile)
    return [round(i * (n - tile) / (k - 1)) for i in range(k)]


@torch.no_grad()
def apply_model(name, x, fp, days, bayes=True, dev=None, tile=256, pad=48, model=None):
    """Run a trained model over a filled stack x [T,2,H,W] (fp [H,W] bool marks pixels to denoise)."""
    dev = dev or torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    if model is None:
        m, ck, tab = load_model(name, dev)
    else:
        m, ck, tab = model
    variant = ck["variant"]
    T, C, H, W = x.shape
    out = x.clone()
    vfull = torch.ones(T, H, W, dtype=torch.bool)
    dd = torch.tensor(days, dtype=torch.float32)
    for r0 in _tiles(H, tile):
        for c0 in _tiles(W, tile):
            r1, c1 = min(r0 + tile, H), min(c0 + tile, W)
            ra, ca = max(r0 - pad, 0), max(c0 - pad, 0)
            rb, cb = min(r1 + pad, H), min(c1 + pad, W)
            xt = x[:, :, ra:rb, ca:cb].to(dev)
            if variant in ("n2n", "n2n_masked"):
                y = torch.cat([m(xt[i:i + 4]) for i in range(0, T, 4)])
            elif variant == "blindspot":
                ys = []
                for i in range(T):
                    mu, ls = m(xt[i:i + 1])
                    ys.append(posterior(xt[i:i + 1], mu, ls, sigma_lookup(tab, mu)) if bayes else mu)
                y = torch.cat(ys)
            else:
                mu, ls = m.forward_all(xt, vfull[:, ra:rb, ca:cb].to(dev), dd.to(dev))
                y = posterior(xt, mu, ls, sigma_lookup(tab, mu)) if bayes else mu
            out[:, :, r0:r1, c0:c1] = y[:, :, r0 - ra:r1 - ra, c0 - ca:c1 - ca].float().cpu()
            del xt, y
    if dev.type == "mps":
        torch.mps.empty_cache()
    return torch.where(fp[None, None], out, x)


def denoise(x, valid, variant="multitemporal_S", dates=None, bayes=True, dev=None, model=None):
    """Integration entry. x [T,2,H,W] preview brightness, valid [T,H,W] -> denoised x [T,2,H,W].

    Pixels inside the ops footprint (no-data margin removed, common footprint, 8 px edge erosion)
    are denoised; other valid pixels keep their input value (so ops.run_pipeline's own footprint
    rules give the same footprint); invalid pixels are 0. `variant` is a saved model name
    (e.g. "multitemporal_S", "n2n_masked_S", "blindspot_S_s3") or a classic filter
    ("none", "box3", "box5", "quegan3"). `model` = (module, {"variant": ...}, noise_table) bypasses loading."""
    x = x.float()
    valid = valid.bool()
    xc, v, fp = footprint(x, valid)
    if variant in BASELINES:
        y = BASELINES[variant](xc, fp)
        return torch.where(fp[None, None], y, xc) * valid[:, None]
    T = x.shape[0]
    days = day_numbers(dates) if dates is not None else [12 * i for i in range(T)]
    vin = fp[None].expand(T, -1, -1)
    xf = fill_invalid(xc, vin)
    y = apply_model(variant, xf, fp, days, bayes=bayes, dev=dev, model=model)
    return torch.where(fp[None, None], y, xc) * valid[:, None]


def _quegan(x, fp, k):
    v = fp[None].expand(x.shape[0], -1, -1).contiguous()
    return ops.quegan_filter(x, v, k)


def _box(x, fp, k):
    v = fp[None].expand(x.shape[0], -1, -1).contiguous()
    return ops.box_mean(x, v, k)


BASELINES = {
    "none": lambda x, fp: x.clone(),
    "box3": lambda x, fp: _box(x, fp, 3),
    "box5": lambda x, fp: _box(x, fp, 5),
    "quegan3": lambda x, fp: _quegan(x, fp, 3),
    "quegan5": lambda x, fp: _quegan(x, fp, 5),
}
KN_OF = {"none": (1, False), "box3": (3, False), "box5": (5, False), "quegan3": (3, True), "quegan5": (5, True)}


def predict(area, variant):
    from scripts.detect.stack import load_preview_stack
    st = load_preview_stack(area)
    t0 = time.time()
    xd = denoise(st.x, st.valid, variant, dates=st.dates)
    out = ROOT / f"data/processed/{area}/despeckle"
    out.mkdir(parents=True, exist_ok=True)
    torch.save({"x": xd.half(), "valid": st.valid, "dates": st.dates, "variant": variant, "bounds": st.bounds,
                "sec": time.time() - t0}, out / "x.pt")
    print(f"{area}: {variant} in {time.time() - t0:.1f}s -> {out / 'x.pt'}")


# ============================================================================ evaluation helpers
def logistic_edge_fit(prof, s):
    """Least-squares erf edge per profile: b(s) = a + (c - a) * Phi((s - s0) / w).
    prof [N, S], s [S]. Grid over (s0, w), closed-form (a, c). Returns s0, w, a, c (tensors [N])."""
    s0g = torch.arange(-4, 4.001, 0.1)
    wg = torch.exp(torch.linspace(math.log(0.15), math.log(5), 40))
    S0, WG = torch.meshgrid(s0g, wg, indexing="ij")
    S0, WG = S0.reshape(-1), WG.reshape(-1)
    phi = 0.5 * (1 + torch.erf((s[None] - S0[:, None]) / (WG[:, None] * math.sqrt(2))))   # [G, S]
    # model: a*(1-phi) + c*phi  -> normal equations per grid point
    A11 = ((1 - phi) ** 2).sum(1)
    A12 = ((1 - phi) * phi).sum(1)
    A22 = (phi ** 2).sum(1)
    det = A11 * A22 - A12 ** 2
    b1 = prof @ (1 - phi).T     # [N, G]
    b2 = prof @ phi.T
    a = (A22 * b1 - A12 * b2) / det
    c = (A11 * b2 - A12 * b1) / det
    sse = (prof ** 2).sum(1, keepdim=True) - 2 * (a * b1 + c * b2) + a ** 2 * A11 + 2 * a * c * A12 + c ** 2 * A22
    best = sse.argmin(1)
    i = torch.arange(prof.shape[0])
    return S0[best], WG[best], a[i, best], c[i, best]


def shore_profiles(ref_occ, valid, maxs=8.0, ds=0.25, stride=3, seed=0):
    """Shore-normal profiles across the JRC shoreline of the largest lake (occurrence >= 50 %).
    Returns rows, cols [N, S] sample coordinates (water side negative), s [S]."""
    import scipy.ndimage as ndi
    lake = (ref_occ >= 50).numpy() if ref_occ.max() > 1.5 else (ref_occ >= 0.5).numpy()
    lab, n = ndi.label(lake)
    big = lab == (np.bincount(lab.ravel())[1:].argmax() + 1)
    big = ndi.binary_fill_holes(big)
    sd = ndi.distance_transform_edt(~big) - ndi.distance_transform_edt(big)    # >0 land
    sds = ndi.gaussian_filter(sd, 2.0)
    gy, gx = np.gradient(sds)
    nrm = np.hypot(gx, gy) + 1e-9
    edge = big & ~ndi.binary_erosion(big)
    rr, cc = np.nonzero(edge)
    keep = np.arange(len(rr)) % stride == 0
    rr, cc = rr[keep], cc[keep]
    ny, nx = gy[rr, cc] / nrm[rr, cc], gx[rr, cc] / nrm[rr, cc]
    s = np.arange(-maxs, maxs + 1e-9, ds)
    # shore point = sub-pixel zero of the signed distance along the normal
    off = sds[rr, cc] / 1.0
    r0, c0 = rr - ny * off, cc - nx * off
    R = r0[:, None] + ny[:, None] * s[None]
    Cc = c0[:, None] + nx[:, None] * s[None]
    H, W = lake.shape
    inside = (R >= 0).all(1) & (R <= H - 1).all(1) & (Cc >= 0).all(1) & (Cc <= W - 1).all(1)
    R, Cc = R[inside], Cc[inside]
    # the profile must be water (JRC) on its water half and land on its land half, and fully valid
    lk = ndi.map_coordinates(big.astype(float), [R.ravel(), Cc.ravel()], order=0).reshape(R.shape)
    vv = ndi.map_coordinates(valid.numpy().astype(float), [R.ravel(), Cc.ravel()], order=0).reshape(R.shape)
    wside, lside = s < -1.5, s > 1.5
    ok = (lk[:, wside] > 0.5).all(1) & (lk[:, lside] < 0.5).all(1) & (vv > 0.5).all(1)
    return torch.from_numpy(R[ok]).float(), torch.from_numpy(Cc[ok]).float(), torch.from_numpy(s).float()


def sample_bilinear(img, R, C):
    """img [H,W]; R, C [N,S] float -> [N,S]."""
    H, W = img.shape
    g = torch.stack([C / (W - 1) * 2 - 1, R / (H - 1) * 2 - 1], -1)[None]
    return F.grid_sample(img[None, None].float(), g, mode="bilinear", align_corners=True)[0, 0]


def ssim_map(a, b, win=11, sigma=1.5):
    g = torch.exp(-(torch.arange(win) - win // 2) ** 2 / (2 * sigma ** 2))
    g = (g / g.sum())
    k = (g[:, None] * g[None])[None, None]
    C1, C2 = 0.01 ** 2, 0.03 ** 2
    a, b = a[:, None].double(), b[:, None].double()
    k = k.double()
    f = lambda z: F.conv2d(z, k, padding=win // 2)  # noqa: E731
    ma, mb = f(a), f(b)
    va, vb, cab = f(a * a) - ma ** 2, f(b * b) - mb ** 2, f(a * b) - ma * mb
    return (((2 * ma * mb + C1) * (2 * cab + C2)) / ((ma ** 2 + mb ** 2 + C1) * (va + vb + C2)))[:, 0].float()


def correlated_noise(shape, acf, gen):
    """Gaussian noise [*, H, W] with unit variance and the given 2-D autocorrelation (lags +-m)."""
    *lead, H, W = shape
    m = acf.shape[-1] // 2
    k = torch.zeros(H, W, dtype=torch.float64)
    for i in range(-m, m + 1):
        for j in range(-m, m + 1):
            k[i % H, j % W] = acf[i + m, j + m]
    psd = torch.fft.fft2(k).real.clamp_min(0)
    wn = torch.randn(*lead, H, W, generator=gen, dtype=torch.float64)
    n = torch.fft.ifft2(torch.fft.fft2(wn) * psd.sqrt()).real
    return (n / n.std()).float()


def synth_scene(d, acf, table, gen, enl=None, quantise=True):
    """Clean truth = time-mean of a held-out area's stack (+ a synthetic flood growing over a field
    area from date 5 on); noisy = truth + correlated, level-dependent noise like the real one (or
    white L-look log-speckle when enl is set), clipped and 8-bit quantised like a preview."""
    x, fp = d["x"].float(), d["fp"]
    T, _, H, W = x.shape
    clean = x.mean(0, keepdim=True).expand(T, -1, -1, -1).clone()
    yy, xx = torch.meshgrid(torch.arange(H).float(), torch.arange(W).float(), indexing="ij")
    flood = torch.zeros(T, H, W, dtype=torch.bool)
    cy, cx = H * 0.55, W * 0.45
    for t in range(T):
        if t >= T // 2:
            rad = 25 + 10 * (t - T // 2)
            blob = ((yy - cy) / 1.0) ** 2 + ((xx - cx) / 1.6) ** 2 < rad ** 2
            flood[t] = blob & ~(((yy - cy - 8) ** 2 + (xx - cx + 30) ** 2) < 12 ** 2)   # island
    clean[:, 0][flood] = 2 / 255
    clean[:, 1][flood] = 4 / 255
    if enl:
        noisy = (clean + log_gamma_noise(clean.shape, enl, gen)).clamp(1 / 255, 1)
    else:
        sig = sigma_lookup(table, clean.reshape(T, 2, H, W))
        n = torch.stack([torch.stack([correlated_noise((H, W), acf[c], gen) for c in range(2)]) for _ in range(T)])
        noisy = (clean + sig * n).clamp(1 / 255, 1)
        if quantise:
            noisy = torch.round(noisy * 255) / 255
    return clean, noisy, flood


# ============================================================================ evaluation
EVAL_AREAS = ["tonle-sap", "sevan", "ararat"]
CROPS = {"sevan": (230, 358, 200, 328), "tonle-sap": (520, 648, 560, 688), "ararat": (150, 278, 0, 128)}


def model_list():
    want = ["n2n_S", "n2n_masked_S", "n2n_masked_L", "blindspot_S_s1", "blindspot_S_s3",
            "multitemporal_S", "multitemporal_L"]
    return [w for w in want if (MODELS / f"despeckle_{w}.pt").exists()]


def filter_stack(name, xc, fp, days, bayes=True):
    if name in BASELINES:
        y = BASELINES[name](xc, fp)
    else:
        nm = name[:-6] if name.endswith("_blind") else name
        xf = fill_invalid(xc, fp[None].expand(xc.shape[0], -1, -1))
        y = apply_model(nm, xf, fp, days, bayes=bayes and not name.endswith("_blind"))
    return torch.where(fp[None, None], y, xc)


def evaluate(n_boot=300, quick=False, areas=None, final=True):
    """areas: subset of EVAL_AREAS to (re)compute this call (others are taken from the partial cache, so the
    evaluation can run as several short processes); final: also run the synthetic tests, write results + figures."""
    import scipy.ndimage as ndi
    from scripts.detect import tune
    from scripts.detect.terrain import cached_water_possible
    from scripts.detect.validate import PUBLISHED_SEVAN_KM2

    OUT.mkdir(parents=True, exist_ok=True)
    dev = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    noise = json.loads((MODELS / "despeckle_noise.json").read_text())
    table = torch.tensor(noise["sigma_table"])
    acf = torch.tensor(noise["acf"])
    models = model_list()
    filters = ["none", "box3", "box5", "quegan3", "quegan5"] + models
    if "multitemporal_S" in models:
        filters.append("multitemporal_S_blind")
    R = {"filters": filters, "noise_model": {k: noise[k] for k in ("sigma_table", "acf_lag", "levels")},
         "areas": {}, "train": {}}
    for m in models:
        ck = torch.load(MODELS / f"despeckle_{m}.pt", weights_only=False, map_location="cpu")
        R["train"][m] = {"params": ck["params"], "iters": ck["iters"], "sec": round(ck["sec"], 1),
                         "batch": ck["batch"], "patch": ck["patch"], "final_val": ck["log"][-1]["val"] if ck["log"] else None}
    figdata = {"crops": {}, "profiles": {}, "traj": {}, "resid": {}}
    part = MODELS / "despeckle_eval_partial.pt"
    if part.exists():
        pr = torch.load(part, weights_only=False)
        if pr["filters"] == filters:
            R["areas"].update(pr["areas"])
            for k_ in figdata:
                figdata[k_].update(pr["figdata"][k_])
    gen = torch.Generator().manual_seed(SEED)
    for area in (areas or EVAL_AREAS):
        print(f"== {area}")
        d = load_area(area)
        xc, v, fp = d["x"].float(), d["v"], d["fp"]
        T, _, H, W = xc.shape
        days = day_numbers(d["dates"])
        km2 = d["km2"]
        b = tune.base(area)
        assert tuple(b["valid"].shape) == (H, W)
        lab = tune.build_labels(b["ref"], b["valid"], **tune.LABEL_RULE)
        strips = tune.split_masks(H, W, tune.SPLIT[area])
        region = tune.Region(lab, strips["test"], b["ref"]["occ_mean"], km2, T)
        wok = cached_water_possible(area, (H, W))
        # ---- filter-independent selections from the RAW stack (split-sample: select on even dates)
        ev, od = list(range(0, T, 2)), list(range(1, T, 2))
        xr = xc
        tm_e = xr[ev].mean(0)
        tsd_e = xr[ev].std(0)
        sp_e = (ops.box_mean((tm_e ** 2)[None], fp[None], 5)[0] - ops.box_mean(tm_e[None], fp[None], 5)[0] ** 2).clamp_min(0).sqrt()
        hom = {}
        for c in range(2):
            cand = fp & (tm_e[c] > 0.12) & (tm_e[c] < 0.9)
            qt = torch.quantile(tsd_e[c][cand][::5], 0.2)
            qs = torch.quantile(sp_e[c][cand][::5], 0.2)
            hom[c] = ops.erode((cand & (tsd_e[c] <= qt) & (sp_e[c] <= qs))[None], 3)[0]
        gm = tm_e.mean(0)
        gy, gx = torch.gradient(ops.box_mean(gm[None, None], fp[None], 3)[0, 0])
        gmag = torch.hypot(gy, gx)
        edge_px = fp & (gmag >= torch.quantile(gmag[fp][::5], 0.9))
        flat_px = fp & (gmag <= torch.quantile(gmag[fp][::5], 0.3))
        floor_px = fp & (xr[:, 0].median(0).values < 0.03)
        # Sevan shoreline profiles
        prof = None
        if area == "sevan":
            Rr, Cc, s = shore_profiles(b["ref"]["occ_mean"], fp)
            prof = (Rr, Cc, s)
            print(f"  shore profiles: {len(Rr)}")
        # Tonle Sap changed pixels: single-step fit on raw HH
        chg = None
        if area == "tonle-sap":
            hh = xr[:, 0]
            best_sse, best_k = None, None
            for k in range(4, 8):
                m1, m2 = hh[:k].mean(0), hh[k:].mean(0)
                sse = ((hh[:k] - m1) ** 2).sum(0) + ((hh[k:] - m2) ** 2).sum(0)
                if best_sse is None:
                    best_sse, best_k = sse, torch.full_like(sse, k, dtype=torch.long)
                else:
                    upd = sse < best_sse
                    best_sse = torch.where(upd, sse, best_sse)
                    best_k = torch.where(upd, torch.full_like(best_k, k), best_k)
            kk = best_k
            ar = torch.arange(T)[:, None, None]
            bef = ((ar < kk[None] - 1) * hh).sum(0) / (kk - 1).float()
            aft = ((ar > kk[None]) * hh).sum(0) / (T - kk - 1).float()
            step = aft - bef
            resid = (best_sse / (T - 2)).sqrt()
            sel = fp & (step.abs() >= 0.25) & (resid <= 0.06)
            chg = {"k": kk[sel], "step": step[sel], "sel": sel}
            print(f"  changed pixels: {int(sel.sum())} ({float(sel.sum()) * km2:.0f} km2)")
        A = {"homog_px": [int(hom[0].sum()), int(hom[1].sum())], "filters": {}}
        for name in filters:
            t0 = time.time()
            y = filter_stack(name, xc, fp, days)
            tf = time.time() - t0
            M = {"filter_sec": round(tf, 1)}
            # ---- noise on homogeneous stable pixels (odd dates). The plain temporal std there is
            # dominated by real date-to-date radiometric change (~0.025), so the noise measure is the
            # temporal std of the 9x9 high-pass image (texture is constant in time, broad changes are
            # low-pass): what is left is the fine-scale noise.
            ns, nt, nhp = [], [], []
            for c in range(2):
                yo = y[od, c]
                nt.append(float(yo.std(0)[hom[c]].median()))
                hp = yo - ops.box_mean(yo[:, None], fp[None].expand(len(od), -1, -1), 9)[:, 0]
                nhp.append(float(hp.std(0)[hom[c]].median()))
                ss = []
                for t in od:
                    z = y[t, c]
                    vv = (ops.box_mean((z ** 2)[None, None], fp[None], 5)[0, 0] - ops.box_mean(z[None, None], fp[None], 5)[0, 0] ** 2).clamp_min(0).sqrt()
                    ss.append(vv[hom[c]])
                ns.append(float(torch.cat(ss).median()))
            M["noise_temporal_std"] = nt
            M["noise_hp_std"] = nhp
            M["noise_spatial_std"] = ns
            if name == "none":
                raw_hp = nhp
            M["enl_gain"] = [round((raw_hp[c] / max(nhp[c], 1e-6)) ** 2, 2) for c in range(2)]
            # ---- radiometric
            diff = (y - xc)
            fpm = fp[None].expand(T, -1, -1)
            M["bias_all"] = [float(diff[:, c][fpm].mean()) for c in range(2)]
            M["bias_floor_water"] = float(diff[:, 0][floor_px[None].expand(T, -1, -1)].mean()) if floor_px.any() else None
            r = (xc - y)
            M["resid_std"] = [float(r[:, c][fpm].std()) for c in range(2)]
            e_edge = float((r[:, 0][edge_px[None].expand(T, -1, -1)] ** 2).mean())
            e_flat = float((r[:, 0][flat_px[None].expand(T, -1, -1)] ** 2).mean())
            M["resid_edge_flat_ratio"] = e_edge / max(e_flat, 1e-12)
            if name != "none":
                a0 = noise_resid_acf(r, fp)
                M["resid_acf_lag1"] = [float(a0[c, 5, 6]) for c in range(2)]
            # ---- shoreline
            if prof is not None:
                Rr, Cc, s = prof
                pw = []
                for t in range(T):
                    pw.append(sample_bilinear(y[t, 0], Rr, Cc))
                P = torch.cat(pw)
                s0, w, aa, cc_ = logistic_edge_fit(P, s)
                good = (cc_ - aa) > 0.15
                M["shore_n_profiles"] = int(good.sum())
                M["shore_width_10_90_px"] = float((2 * 1.2816 * w[good]).median())
                M["shore_width_mean_px"] = float((2 * 1.2816 * w[good]).mean())
                M["shore_s0_px"] = float(s0[good].median())
                M["shore_water_level"] = float(aa[good].mean())
                M["shore_land_level"] = float(cc_[good].mean())
                figdata["profiles"][name] = (s, P[good].mean(0))
            # ---- change preservation
            if chg is not None:
                hhf = y[:, 0][:, chg["sel"]]          # [T, N]
                hhr = xc[:, 0][:, chg["sel"]]
                k = chg["k"]
                N = k.numel()
                ii = torch.arange(N)
                ar = torch.arange(T)[:, None]
                bef_f = ((ar < k[None] - 1) * hhf).sum(0) / (k - 1).float()
                aft_f = ((ar > k[None]) * hhf).sum(0) / (T - k - 1).float()
                S = chg["step"]
                M["change_leak_pre"] = float(((hhf[k - 1, ii] - bef_f) / S).median())
                M["change_leak_post"] = float(((aft_f - hhf[k, ii]) / S).median())
                M["change_jump_ratio"] = float(((hhf[k, ii] - hhf[k - 1, ii]) / (hhr[k, ii] - hhr[k - 1, ii])).median())
                M["change_step_ratio"] = float(((aft_f - bef_f) / S).median())
                rel = torch.arange(-4, 3)
                trj = []
                for rr_ in rel:
                    tt = (k + rr_).clamp(0, T - 1)
                    okk = ((k + rr_) >= 0) & ((k + rr_) < T)
                    val = (hhf[tt, ii] - bef_f) / S
                    trj.append(float(val[okk].mean()))
                figdata["traj"][name] = (rel.tolist(), trj)
            # ---- downstream detection (the production pipeline, filter bypassed)
            xin = y * v[:, None]
            box, que = KN_OF.get(name, (1, False))
            kn = dict(ops.KNOBS["preview"], box=box, quegan=que)
            yin = xc if name in BASELINES else xin
            out = ops.run_pipeline(yin.to(dev), v.to(dev), "preview", kn=kn)
            water = out["water"].cpu()
            vd = out["valid"].cpu()
            cdate = out["change_date"].cpu()
            if area == "sevan":
                M.update(sevan_subpixel(out, water & wok[None] if wok is not None else water, vd, d["bounds"]))
            del out
            if dev.type == "mps":
                torch.mps.empty_cache()
            wm = water & wok[None] if wok is not None else water
            met = region.metrics(wm)
            bt = region.bootstrap(wm, n_boot, gen)
            M["recall_perm"], M["fpr_never"], M["balanced_acc"] = met["recall_perm"], met["fpr_never"], met["balanced_acc"]
            M["ci"] = {k_: bt[k_] for k_ in ("recall_perm", "fpr_never", "balanced_acc")}
            M["rank_rho"] = met["rank_rho"]
            areas_km2 = [float((wm[t] & vd[t]).sum()) * km2 for t in range(T)]
            M["water_km2"] = [round(a_, 1) for a_ in areas_km2]
            st8 = np.ones((3, 3), bool)
            specks = []
            for t in range(T):
                labm, nlab = ndi.label(wm[t].numpy(), structure=st8)
                sz = np.bincount(labm.ravel())[1:]
                specks.append(int((sz < 10).sum()))
            M["specks_lt10_per_date"] = float(np.mean(specks))
            if wok is not None:
                M["slope_water_km2_per_date"] = float((water & ~wok[None] & vd).sum()) * km2 / T
            nev = lab["never"] & strips["test"]
            M["change_on_never_land_frac"] = float((cdate[nev] >= 0).float().mean()) if nev.any() else None
            if area == "sevan":
                calm = [t for t in range(T) if t not in (5, 6)]
                M["sevan_abs_err_pct_mean"] = float(np.mean([abs(areas_km2[t] / PUBLISHED_SEVAN_KM2 - 1) * 100 for t in calm]))
                M["sevan_consec_calm_abs_dkm2"] = float(np.mean([abs(areas_km2[calm[i + 1]] - areas_km2[calm[i]]) for i in range(len(calm) - 1)]))
            if area in CROPS:
                r0, r1, c0, c1 = CROPS[area]
                tsel = 7 if area != "sevan" else 7
                figdata["crops"].setdefault(area, {})[name] = y[tsel, :, r0:r1, c0:c1].clone()
                if area == "tonle-sap":
                    figdata["resid"][name] = (xc[tsel, 0, r0:r1, c0:c1] - y[tsel, 0, r0:r1, c0:c1]).clone()
            A["filters"][name] = M
            print(f"  {name:24s} {tf:5.1f}s  noise_hp {nhp[0]:.4f}/{nhp[1]:.4f} sp {ns[0]:.4f}  BA {M['balanced_acc']:.4f} "
                  f"rec {M['recall_perm']:.4f} fpr {M['fpr_never']:.5f} specks {M['specks_lt10_per_date']:.1f}"
                  + (f" shore_w {M['shore_width_10_90_px']:.2f}" if prof is not None else "")
                  + (f" leak {M['change_leak_pre']:.3f} jump {M['change_jump_ratio']:.3f}" if chg is not None else ""))
            del y, xin, yin
        R["areas"][area] = A
        if chg is not None:
            R["areas"][area]["changed_px"] = int(chg["sel"].sum())
        torch.save({"filters": filters, "areas": R["areas"], "figdata": figdata}, part)
    if not final:
        return R
    # ---- synthetic truth (held-out Ararat)
    R["synthetic"] = synthetic_eval(filters, table, acf, enl=None)
    enl_models = [m for m in ("n2n_masked_S_enl4", "blindspot_S_s1_enl4", "multitemporal_S_enl4")
                  if (MODELS / f"despeckle_{m}.pt").exists()]
    R["synthetic_enl4"] = synthetic_eval(["none", "box3", "box5", "quegan3", "quegan5"] + enl_models, table, acf, enl=4)
    old = OUT / "results.json"
    if old.exists():
        prev = json.loads(old.read_text())
        if "calibrated" in prev:
            R["calibrated"] = prev["calibrated"]
    (OUT / "results.json").write_text(json.dumps(R, indent=1, default=float))
    torch.save(figdata, MODELS / "despeckle_figdata.pt")
    figures(R, figdata)
    return R


_SHORE = {}


def sevan_subpixel(out, water, vd, bounds):
    """Shoreline location error at Lake Sevan against the JRC 30 m shoreline (occurrence >= 90 %), as in
    scripts/detect/subpixel.py: hard-mask staircase and the f = 0.5 line of the display-domain mixing
    fraction, both computed from this filter's pipeline output (L = the filtered brightness)."""
    from scripts.detect import subpixel as SP
    T, H, W = water.shape
    if "sh" not in _SHORE:
        refs = SP._ref("sevan", bounds, H, W)
        lab = ops.label_components((refs["occ90"] >= 0.5)[None])[0]
        cnt = torch.bincount(lab.flatten())
        cnt[0] = 0
        _SHORE["lake"] = ops.dilate((lab == cnt.argmax())[None], 11)[0]
        _SHORE["sh"] = SP.Shore30("sevan", bounds, H, W, "occurrence")
    dom = _SHORE["lake"] & vd.all(0)
    sh = _SHORE["sh"]
    L, prior = out["L"].float().cpu(), out["prior_water"].cpu()
    fr = SP.water_fraction(L, vd, water, "preview", prior, x_frac=None, domain="display")["frac"].float()
    calm = [t for t in range(T) if t not in SP.SEVAN_WINDY]
    res = {}
    for k, f in (("hard", water.float()), ("frac", fr)):
        pd = [SP.shoreline_distance(f[t], dom, sh) for t in calm]
        res[f"shore_err_{k}_m"] = float(np.mean([p["median_abs_m"] for p in pd if p["n"]]))
        res[f"shore_err_{k}_signed_m"] = float(np.mean([p["median_signed_m"] for p in pd if p["n"]]))
        res[f"shore_err_{k}_calm_median_map_m"] = SP.shoreline_distance(f[calm].median(0).values, dom, sh).get("median_abs_m")
    return res


def noise_resid_acf(r, fp, maxlag=5):
    out = torch.zeros(2, 2 * maxlag + 1, 2 * maxlag + 1)
    m = fp
    for c in range(2):
        acc = 0
        for t in range(0, r.shape[0], 3):
            acc = acc + acf2d(r[t, c], m, maxlag)
        out[c] = acc / len(range(0, r.shape[0], 3))
    return out


def synthetic_eval(filters, table, acf, enl=None):
    d = load_area(HOLDOUT_AREA)
    gen = torch.Generator().manual_seed(SEED + 3)
    clean, noisy, flood = synth_scene(d, acf, table, gen, enl=enl)
    fp = d["fp"]
    T = clean.shape[0]
    days = day_numbers(d["dates"])
    res = {}
    inner = ops.erode(fp[None], 11)[0]
    fl_edge = torch.zeros_like(flood)
    for t in range(T):
        fl_edge[t] = ops.dilate(flood[t][None], 7)[0] & ~ops.erode(flood[t][None], 7)[0] if flood[t].any() else False
    for name in filters:
        if name.endswith("_blind"):
            continue
        y = filter_stack(name, noisy, fp, days)
        e = (y - clean)
        mse = float((e ** 2)[:, :, inner].mean())
        sm = ssim_map(y.reshape(-1, *y.shape[-2:]), clean.reshape(-1, *clean.shape[-2:])).reshape(T, 2, *y.shape[-2:])
        ssim = float(sm[:, :, inner].mean())
        edge_mse = float((e[:, 0] ** 2)[fl_edge & inner[None]].mean())
        # change on the synthetic flood: fraction of the true step on the date the blob grows into a ring
        t5 = T // 2
        ring = flood[t5 + 1] & ~flood[t5] & inner
        step_true = (clean[t5 + 1, 0] - clean[t5, 0])[ring]
        step_f = (y[t5 + 1, 0] - y[t5, 0])[ring]
        pre = (y[t5, 0] - clean[t5, 0])[ring]
        res[name] = {"psnr": 10 * math.log10(1 / mse), "ssim": ssim,
                     "psnr_flood_edge": 10 * math.log10(1 / max(edge_mse, 1e-12)),
                     "synthetic_step_ratio": float((step_f / step_true).median()),
                     "synthetic_leak_pre": float((pre / step_true).median())}
        print(f"  synth{'' if not enl else f' ENL{enl}'} {name:24s} PSNR {res[name]['psnr']:.2f} SSIM {ssim:.4f} "
              f"edgePSNR {res[name]['psnr_flood_edge']:.2f} step {res[name]['synthetic_step_ratio']:.3f}")
        del y
    return res


# ============================================================================ figures
NICE = {"none": "no filter", "box3": "box 3x3", "box5": "box 5x5", "quegan3": "Quegan 3x3 (current)",
        "quegan5": "Quegan 5x5", "n2n_S": "N2N plain S", "n2n_masked_S": "N2N masked S", "n2n_masked_L": "N2N masked L",
        "blindspot_S_s1": "blind-spot 1 px", "blindspot_S_s3": "blind-spot 5x5",
        "multitemporal_S": "multi-temporal S", "multitemporal_L": "multi-temporal L",
        "multitemporal_S_blind": "multi-temporal S (no Bayes)",
        "n2n_masked_S_enl4": "N2N masked (ENL4)", "blindspot_S_s1_enl4": "blind-spot (ENL4)",
        "multitemporal_S_enl4": "multi-temporal (ENL4)"}


def _save(fig, name):
    import matplotlib.pyplot as plt
    p = OUT / name
    fig.savefig(p, dpi=fig.dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  wrote {p.relative_to(ROOT)} ({p.stat().st_size / 1024:.0f} KB)")


def _color(name):
    if name in BASELINES:
        return {"none": "#888888", "box3": "#c49a6c", "box5": "#a0522d", "quegan3": "#d62728", "quegan5": "#ff9896"}[name]
    if name.startswith("n2n"):
        return "#1f77b4" if "masked" in name else "#9ecae1"
    if name.startswith("blind"):
        return "#2ca02c" if "s3" in name else "#98df8a"
    return "#6a3d9a" if "blind" not in name else "#cab2d6"


def figures(R, figdata):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    filters = [f for f in R["filters"]]
    best = "multitemporal_S" if "multitemporal_S" in filters else filters[-1]
    # 1. crops
    show = [f for f in ("none", "quegan3", "n2n_masked_S", "blindspot_S_s3", best) if f in filters]
    fig, ax = plt.subplots(3, len(show), figsize=(2.3 * len(show), 7.0), dpi=72)
    for i, area in enumerate(("sevan", "tonle-sap", "ararat")):
        for j, f in enumerate(show):
            im = figdata["crops"][area][f]
            rgb = torch.stack([im[0], im[1], im[0]], -1).clamp(0, 1).numpy()
            ax[i, j].imshow(rgb, interpolation="nearest")
            ax[i, j].set_xticks([])
            ax[i, j].set_yticks([])
            if i == 0:
                ax[i, j].set_title(NICE.get(f, f), fontsize=9)
        ax[i, 0].set_ylabel({"sevan": "Sevan shore", "tonle-sap": "Tonle Sap floodplain", "ararat": "Ararat fields"}[area], fontsize=9)
    fig.suptitle("23 Sep / 24 Sep 2026 (HH red+blue, HV green), 128 x 128 px crops (~23 km)", fontsize=10)
    _save(fig, "crops.png")
    # 2. residuals + acf
    rs = [f for f in ("quegan3", "box3", "n2n_S", "n2n_masked_S", "blindspot_S_s3", best) if f in figdata["resid"]]
    fig, ax = plt.subplots(1, len(rs) + 1, figsize=(2.6 * (len(rs) + 1), 3.2), dpi=80)
    for j, f in enumerate(rs):
        ax[j].imshow(figdata["resid"][f].numpy(), cmap="RdBu", vmin=-0.08, vmax=0.08, interpolation="nearest")
        ax[j].set_title(NICE.get(f, f) + "\nnoisy - filtered (HH)", fontsize=8)
        ax[j].set_xticks([])
        ax[j].set_yticks([])
    acf = np.array(R["noise_model"]["acf_lag"])
    lags = np.arange(acf.shape[-1])
    ax[-1].plot(lags, acf[0], "o-", label="noise HH (date differences)")
    ax[-1].plot(lags, acf[1], "s-", label="noise HV")
    ax[-1].axhline(0, color="k", lw=0.5)
    ax[-1].set_xlabel("lag (px, mean of rows and cols)")
    ax[-1].set_ylabel("autocorrelation")
    ax[-1].legend(fontsize=7)
    ax[-1].set_title("Speckle is spatially correlated", fontsize=8)
    fig.suptitle("Residuals on the Tonle Sap floodplain crop (23 Sep). Structure = signal removed.", fontsize=9)
    _save(fig, "residuals.png")
    # 3. shoreline profiles
    sv = R["areas"]["sevan"]["filters"]
    fig, ax = plt.subplots(1, 2, figsize=(11, 3.8), dpi=80)
    for f in [f for f in filters if f in figdata["profiles"]]:
        s, p = figdata["profiles"][f]
        ax[0].plot(s.numpy(), p.numpy(), color=_color(f), lw=1.5 if f in ("none", "quegan3", best) else 0.8, label=NICE.get(f, f))
    ax[0].set_xlabel("distance from the JRC 30 m shoreline (px, + = land)")
    ax[0].set_ylabel("mean HH brightness")
    ax[0].legend(fontsize=6)
    names = [f for f in filters if "shore_width_10_90_px" in sv[f]]
    ax[1].barh([NICE.get(f, f) for f in names], [sv[f]["shore_width_10_90_px"] for f in names], color=[_color(f) for f in names])
    ax[1].set_xlabel("median 10-90 % edge width (px), erf fit per profile and date")
    ax[1].invert_yaxis()
    ax[1].tick_params(labelsize=7)
    fig.suptitle(f"Lake Sevan shoreline, {sv['none']['shore_n_profiles']} profile-dates", fontsize=9)
    _save(fig, "shoreline_profiles.png")
    # 4. trade-off
    fig, ax = plt.subplots(figsize=(6.5, 4.6), dpi=80)
    for f in names:
        x_ = np.mean([R["areas"][a]["filters"][f]["noise_spatial_std"][0] for a in EVAL_AREAS])
        ax.scatter(x_, sv[f]["shore_width_10_90_px"], color=_color(f), s=50, edgecolor="k", lw=0.5)
        ax.annotate(NICE.get(f, f), (x_, sv[f]["shore_width_10_90_px"]), fontsize=7, xytext=(4, 2), textcoords="offset points")
    ax.set_xlabel("residual noise: spatial std on stable homogeneous land (HH, mean of 3 areas)")
    ax.set_ylabel("Sevan shoreline 10-90 % width (px)")
    ax.set_title("Noise vs edge sharpness (bottom-left is better)", fontsize=9)
    _save(fig, "tradeoff.png")
    # 5. temporal leakage
    fig, ax = plt.subplots(figsize=(6.5, 4.2), dpi=80)
    for f in [f for f in filters if f in figdata["traj"]]:
        rel, trj = figdata["traj"][f]
        ax.plot(rel, trj, "o-", color=_color(f), lw=1.5 if f in ("none", "quegan3", best) else 0.8, ms=3, label=NICE.get(f, f))
    ax.axvline(-0.5, color="k", lw=0.5, ls=":")
    ax.set_xlabel("date relative to the step (0 = first date at the new level)")
    ax.set_ylabel("fraction of the step (HH)")
    ax.set_title(f"Tonle Sap: {R['areas']['tonle-sap']['changed_px']} px that changed once between 13 Jul and 23 Sep", fontsize=9)
    ax.legend(fontsize=6)
    _save(fig, "temporal_leakage.png")
    # 6. downstream scoreboard
    keys = [("balanced_acc", "balanced acc. (TEST anchors)"), ("fpr_never", "false alarm rate, never-water"),
            ("specks_lt10_per_date", "specks < 10 px per date"), ("change_on_never_land_frac", "change flagged on never-water land")]
    fig, ax = plt.subplots(len(keys), 3, figsize=(13, 2.3 * len(keys)), dpi=72)
    for i, (k, lab) in enumerate(keys):
        for j, a in enumerate(EVAL_AREAS):
            F_ = R["areas"][a]["filters"]
            vals = [F_[f][k] if F_[f].get(k) is not None else np.nan for f in filters]
            ax[i, j].bar(range(len(filters)), vals, color=[_color(f) for f in filters])
            if k in ("balanced_acc", "fpr_never") and all(F_[f]["ci"][k] is not None for f in filters):
                lo = [F_[f]["ci"][k][0] for f in filters]
                hi = [F_[f]["ci"][k][1] for f in filters]
                ax[i, j].errorbar(range(len(filters)), vals, yerr=[np.array(vals) - lo, np.array(hi) - vals], fmt="none", ecolor="k", lw=0.7)
                fin = [v_ for v_ in vals if np.isfinite(v_)]
                if fin and k == "balanced_acc":
                    ax[i, j].set_ylim(min(lo) - 0.01, max(hi) + 0.005)
            ax[i, j].set_xticks(range(len(filters)))
            ax[i, j].set_xticklabels([NICE.get(f, f) for f in filters], rotation=60, ha="right", fontsize=6) if i == len(keys) - 1 else ax[i, j].set_xticklabels([])
            if i == 0:
                ax[i, j].set_title(a, fontsize=9)
            if j == 0:
                ax[i, j].set_ylabel(lab, fontsize=7)
            ax[i, j].tick_params(axis="y", labelsize=7)
    _save(fig, "downstream.png")
    # 7. synthetic truth (+ ENL 4)
    fig, ax = plt.subplots(1, 3, figsize=(13, 3.8), dpi=80)
    for j, (key, ttl) in enumerate((("synthetic", "PSNR, preview-like noise"), ("synthetic", "SSIM, preview-like noise"),
                                     ("synthetic_enl4", "PSNR, ENL 4 (20 m-like) noise"))):
        S = R[key]
        nm = list(S)
        metric = "ssim" if "SSIM" in ttl else "psnr"
        ax[j].barh([NICE.get(f, f) for f in nm], [S[f][metric] for f in nm], color=[_color(f) for f in nm])
        lo = min(S[f][metric] for f in nm)
        hi = max(S[f][metric] for f in nm)
        ax[j].set_xlim(lo - 0.15 * (hi - lo) - 1e-3, hi + 0.05 * (hi - lo) + 1e-3)
        ax[j].invert_yaxis()
        ax[j].set_title(ttl, fontsize=9)
        ax[j].tick_params(labelsize=7)
    fig.suptitle("Synthetic truth: held-out Ararat time-mean + growing flood + simulated noise", fontsize=9)
    _save(fig, "synthetic.png")


# ============================================================================ calibrated data glimpse
CAL = ROOT / "data/processed/tonle-sap-cal/gamma0"
CAL_DB0 = -30.0                  # brightness b = (dB - CAL_DB0) / D_DB: lake -19.5 dB -> 0.35, forest -5 dB -> 0.83


def _cal_read(date):
    import rasterio
    out = []
    for p in ("hh", "hv"):
        with rasterio.open(CAL / p / f"{date}.tif") as s:
            out.append(torch.from_numpy(s.read(1).astype(np.float32)))
    g = torch.stack(out)
    valid = torch.isfinite(g).all(0) & (g > 0).all(0)
    db = 10 * torch.log10(torch.where(valid[None], g, torch.ones_like(g)))
    return db, valid


def _local_enl(p, m, k=7):
    """ENL = mean^2 / var of linear power in k x k windows, median over the mask."""
    mu = ops.box_mean(p[None, None], m[None], k)[0, 0]
    m2 = ops.box_mean((p ** 2)[None, None], m[None], k)[0, 0]
    var = (m2 - mu ** 2).clamp_min(1e-12)
    return float((mu ** 2 / var)[m].median())


def calibrated_glimpse(size=768):
    """One calibrated GCOV date (80 m gamma0): learned single-date filters in the dB domain (preview-trained
    weights; the 80 m noise of ~0.5 dB is close to the preview's), plus an ENL-4 simulation on the same crop."""
    import scipy.ndimage as ndi
    dates = sorted(p.stem for p in (CAL / "hh").glob("*.tif"))
    date = dates[0]
    db, valid = _cal_read(date)
    H, W = valid.shape
    # crop: the window with the most balanced lake / land mix (lake = smoothed HH < -16 dB), fully valid
    sm = torch.from_numpy(ndi.uniform_filter(db[0].numpy(), 15))
    lake = (sm < -16) & valid
    best, rc = -1, (0, 0)
    for r in range(0, H - size, 128):
        for c in range(0, W - size, 128):
            if float(valid[r:r + size, c:c + size].float().mean()) < 0.995:
                continue
            f = float(lake[r:r + size, c:c + size].float().mean())
            if min(f, 1 - f) > best:
                best, rc = min(f, 1 - f), (r, c)
    r, c = rc
    D = db[:, r:r + size, c:c + size]
    v = valid[r:r + size, c:c + size]
    lk = lake[r:r + size, c:c + size]
    b = ((D - CAL_DB0) / D_DB).clamp(1 / 255, 1)
    fp = ops.erode(v[None], 5)[0]
    xb = fill_invalid(b[None], fp[None])
    lake_in = ops.erode(lk[None], 31)[0] & fp
    land_in = ops.erode((~lk & v)[None], 31)[0] & fp
    prof = shore_profiles(lk.float(), fp, maxs=8.0, stride=4)
    print(f"calibrated {date}: crop rows {r}:{r + size} cols {c}:{c + size}, lake share {best:.2f}, "
          f"lake-interior px {int(lake_in.sum())}, shore profiles {len(prof[0])}")

    def run(name, xin):
        if name in BASELINES:
            return torch.where(fp[None, None], BASELINES[name](xin, fp), xin)
        return apply_model(name, xin, fp, [0])

    def score(y, ref=None):
        ydb = y[0] * D_DB + CAL_DB0
        p = 10 ** (ydb / 10)
        s0, w, aa, cc_ = logistic_edge_fit(torch.stack([sample_bilinear(y[0, 0], prof[0], prof[1])])[0], prof[2])
        good = (cc_ - aa) > 0.1
        o = {"enl_lake_hh": _local_enl(p[0], lake_in), "enl_land_hv": _local_enl(p[1], land_in),
             "std_db_lake_hh_7x7": float(((ops.box_mean((ydb[0] ** 2)[None, None], lake_in[None], 7)
                                           - ops.box_mean(ydb[0][None, None], lake_in[None], 7) ** 2)[0, 0]
                                          .clamp_min(0).sqrt())[lake_in].median()),
             "shore_width_10_90_px": float((2 * 1.2816 * w[good]).median()), "shore_n": int(good.sum()),
             "bias_db": [float((ydb[k] - D[k])[fp].mean()) for k in range(2)]}
        if ref is not None:
            o["psnr"] = 10 * math.log10(1 / float(((y - ref) ** 2)[:, :, fp].mean()))
        return o

    res = {"date": date, "dates_on_disk": dates, "crop": [r, r + size, c, c + size], "brightness": f"(dB - {CAL_DB0}) / {D_DB}"}
    names = ["none", "box3", "box5"] + [m for m in ("n2n_masked_S", "blindspot_S_s1", "blindspot_S_s3") if (MODELS / f"despeckle_{m}.pt").exists()]
    out_imgs = {}
    res["real"] = {}
    for nm in names:
        y = run(nm, xb)
        res["real"][nm] = score(y)
        out_imgs[nm] = y[0].clone()
        print(f"  real  {nm:22s} " + " ".join(f"{k}={v_:.3f}" if isinstance(v_, float) else f"{k}={v_}" for k, v_ in res["real"][nm].items()))
        del y
    # ENL-4 simulation: the 80 m image (ENL ~60-100) is the 'clean' truth, white 4-look log-speckle added
    gen = torch.Generator().manual_seed(SEED + 11)
    noisy = (xb + log_gamma_noise(xb.shape, 4, gen)).clamp(1 / 255, 1)
    res["enl4_sim"] = {}
    sim_imgs = {}
    for nm in ["none", "box3", "box5"] + [m for m in ("n2n_masked_S_enl4", "blindspot_S_s1_enl4", "blindspot_S_s3")
                                          if (MODELS / f"despeckle_{m}.pt").exists()]:
        y = run(nm, noisy)
        res["enl4_sim"][nm] = score(y, ref=xb)
        sim_imgs[nm] = y[0].clone()
        print(f"  ENL4  {nm:22s} psnr={res['enl4_sim'][nm]['psnr']:.2f} enl={res['enl4_sim'][nm]['enl_lake_hh']:.1f} "
              f"edge={res['enl4_sim'][nm]['shore_width_10_90_px']:.2f}")
        del y
    # figure
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    rr0, cc0 = prof[0][:, prof[0].shape[1] // 2], prof[1][:, prof[1].shape[1] // 2]
    k0 = int(((rr0 - size / 2) ** 2 + (cc0 - size / 2) ** 2).argmin())
    zr = int(min(max(float(rr0[k0]) - 128, 0), size - 256))
    zc = int(min(max(float(cc0[k0]) - 128, 0), size - 256))
    rows = [("80 m gamma0 (real)", out_imgs, [n for n in ("none", "box3", "blindspot_S_s3", "n2n_masked_S") if n in out_imgs]),
            ("simulated ENL 4", sim_imgs, [n for n in ("none", "box3", "blindspot_S_s1_enl4", "n2n_masked_S_enl4") if n in sim_imgs])]
    ncol = 5
    fig, ax = plt.subplots(2, ncol, figsize=(2.6 * ncol, 5.6), dpi=72)
    for i, (lab_, imgs, show) in enumerate(rows):
        for j in range(ncol):
            ax[i, j].set_xticks([])
            ax[i, j].set_yticks([])
        for j, nm in enumerate(show):
            ax[i, j].imshow(imgs[nm][0, zr:zr + 256, zc:zc + 256].numpy(), cmap="gray", vmin=0.2, vmax=0.95, interpolation="nearest")
            S = res["real" if i == 0 else "enl4_sim"][nm]
            extra = f", PSNR {S['psnr']:.1f}" if "psnr" in S else ""
            ax[i, j].set_title(f"{NICE.get(nm, nm)}\nENL lake {S['enl_lake_hh']:.0f}{extra}", fontsize=7)
        last = show[-2] if i == 0 else show[-2]
        ax[i, ncol - 1].imshow((imgs["none"][0, zr:zr + 256, zc:zc + 256] - imgs[last][0, zr:zr + 256, zc:zc + 256]).numpy(), cmap="RdBu",
                               vmin=-0.03 if i == 0 else -0.25, vmax=0.03 if i == 0 else 0.25, interpolation="nearest")
        ax[i, ncol - 1].set_title(f"residual: none - {NICE.get(last, last)}", fontsize=7)
        ax[i, 0].set_ylabel(lab_, fontsize=8)
    fig.suptitle(f"Tonle Sap, NISAR GCOV {date}, HH, 256 x 256 px of 80 m (~20 km); grey = (dB + 30) / 30", fontsize=9)
    _save(fig, "calibrated.png")
    p = OUT / "results.json"
    R = json.loads(p.read_text()) if p.exists() else {}
    R["calibrated"] = res
    p.write_text(json.dumps(R, indent=1, default=float))
    return res


# ============================================================================ CLI
def cmd_noise():
    areas = [a for a in list_areas() if a != HOLDOUT_AREA]
    sig_all, num_all = [], []
    acfs = []
    per_area = {}
    for a in areas:
        try:
            d = load_area(a)
        except Exception as e:
            print(f"  skip {a}: {e}")
            continue
        H, W = d["fp"].shape
        tr, _ = column_masks(a, H, W)
        x = d["x"].float()
        sig, num = noise_from_differences(x, d["v"], d["fp"], mask=tr)
        sig_all.append(torch.nan_to_num(sig, nan=0.0) * num)
        num_all.append(num)
        ac = noise_acf(x, d["v"], d["fp"] & tr)
        acfs.append(ac)
        per_area[a] = {"sigma_median": [float(torch.nanmedian(sig[c])) for c in range(2)],
                       "acf_lag1": [round(float((ac[c, 5, 6] + ac[c, 6, 5]) / 2), 3) for c in range(2)],
                       "acf_lag2": [round(float((ac[c, 5, 7] + ac[c, 7, 5]) / 2), 3) for c in range(2)]}
        print(a, per_area[a])
        del x
    num = torch.stack(num_all).sum(0)
    sig = torch.stack(sig_all).sum(0) / num.clamp_min(1)
    sig[num < 500] = float("nan")
    table = finish_table(sig)
    acf = torch.stack(acfs).mean(0)
    m = acf.shape[-1] // 2
    acf_lag = [[float((acf[c, m, m + k] + acf[c, m + k, m]) / 2) for k in range(m + 1)] for c in range(2)]
    res = {"sigma_table": table.tolist(), "levels": ((torch.arange(20) + 0.5) / 20).tolist(), "counts": num.tolist(),
           "acf": acf.tolist(), "acf_lag": acf_lag, "per_area": per_area}
    MODELS.mkdir(parents=True, exist_ok=True)
    (MODELS / "despeckle_noise.json").write_text(json.dumps(res, indent=1))
    print("sigma HH", [round(v, 4) for v in table[0].tolist()])
    print("sigma HV", [round(v, 4) for v in table[1].tolist()])
    print("acf HH", [round(v, 3) for v in acf_lag[0]], "HV", [round(v, 3) for v in acf_lag[1]])


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("noise")
    t = sub.add_parser("train")
    t.add_argument("--variant", required=True, choices=list(SIZES))
    t.add_argument("--size", default="S", choices=["S", "L"])
    t.add_argument("--epochs", type=int, default=None, help="1 epoch = 250 iterations")
    t.add_argument("--iters", type=int, default=3000)
    t.add_argument("--shift", type=int, default=None)
    t.add_argument("--enl", type=float, default=None)
    t.add_argument("--lr", type=float, default=1e-3)
    t.add_argument("--time-limit", type=float, default=600)
    e = sub.add_parser("evaluate")
    e.add_argument("--boot", type=int, default=300)
    e.add_argument("--areas", nargs="*", default=None)
    e.add_argument("--no-final", action="store_true")
    p = sub.add_parser("predict")
    p.add_argument("--area", required=True)
    p.add_argument("--variant", default="multitemporal_S")
    f = sub.add_parser("figures")
    sub.add_parser("calibrated")
    a = ap.parse_args(argv)
    if a.cmd == "noise":
        cmd_noise()
    elif a.cmd == "train":
        iters = a.epochs * 250 if a.epochs else a.iters
        train(a.variant, a.size, iters, shift=a.shift, enl=a.enl, lr=a.lr, time_limit=a.time_limit)
    elif a.cmd == "evaluate":
        evaluate(n_boot=a.boot, areas=a.areas, final=not a.no_final)
    elif a.cmd == "predict":
        predict(a.area, a.variant)
    elif a.cmd == "figures":
        R = json.loads((OUT / "results.json").read_text())
        figures(R, torch.load(MODELS / "despeckle_figdata.pt", weights_only=False))
    elif a.cmd == "calibrated":
        calibrated_glimpse()
    _ = f


if __name__ == "__main__":
    main()
