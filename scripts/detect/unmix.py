"""Physics autoencoder: a neural ENCODER and a radar-scattering DECODER, trained without labels.

    .venv/bin/python -m scripts.detect.unmix prepare --area all        # cache inputs (ops pipeline) per area
    .venv/bin/python -m scripts.detect.unmix synthetic                  # synthetic-truth study (first!)
    .venv/bin/python -m scripts.detect.unmix train --area all [--variant full] [--minutes 8]
    .venv/bin/python -m scripts.detect.unmix predict --area tonle-sap [--variant full]
    .venv/bin/python -m scripts.detect.unmix evaluate                   # tables, figures, results.json
    .venv/bin/python -m scripts.detect.unmix ablate                     # short runs of each variant

Write-up: docs/eda/unmix.md.  Figures + numbers: docs/eda/unmix/.
Products: data/processed/<area>/unmix/products.pt.  Models: data/processed/models/unmix_<variant>.pt.

Model, for pixel i on date t and channel c in {HH, HV}, in POWER (pseudo-intensity I = 10**(D (b - 1) / 10)
from preview brightness b with an assumed stretch of D dB):

    I_t^c(i) = g_t^c * [ f_w,t(i) r_t^c E_w^c  +  f_v,t(i) G^c E_l^c(i)  +  f_l,t(i) E_l^c(i) ]
    f_w + f_v + f_l = 1, f >= 0  (softmax)

  E_w        global water end-member (learned, 2 numbers)
  r_t^c      per-scene, per-date water roughness >= 1 (wind brightens water without it becoming land)
  E_l(i)     per-pixel land end-member, constant in time (encoder output), never darker than a land floor
  G^c        double-bounce gains of flooded vegetation, G^HH >= 1 (+1..+6 dB), G^HV <= 1 (0..-6 dB) (learned, global)
  g_t^c      per-scene, per-date radiometric gain (mean zero in dB)
  f(i, t)    fractions (encoder output, per date)

The reconstruction is converted back to brightness (b = 1 + (10/D) log10 I, soft-clipped to the display range)
and compared with the observed brightness with a Huber loss (noise is roughly additive in brightness).
Integration:  unmix(L_or_x, valid, area=None) -> dict
"""
from __future__ import annotations

import argparse
import json
import math
import time

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from scripts.detect.stack import ROOT

DOCS = ROOT / "docs/eda/unmix"
MODELS = ROOT / "data/processed/models"
FLOOR = 1.0 / 255
CORE_AREAS = ["tonle-sap", "sevan", "ararat"]
WORLD_AREAS = ["bangladesh-haor", "inner-niger", "lake-chad", "mekong-delta", "pantanal", "sindh-indus", "sudd"]
SEVAN_KM2, SEVAN_JRC90_KM2 = 1242.0, 1239.7
TYPES = ["water", "flooded_veg", "land"]

DEFAULTS = {
    "db": 30.0,                 # assumed dB span of the preview stretch (b = 1 at the top, 0 = -D dB)
    "land_floor": (0.10, 0.03), # dry land is never darker than this (HH, HV brightness): the key prior
    "sigma": 0.04,              # brightness noise scale for the Huber loss (stats_change: 0.02-0.05)
    "w_ent": 0.15,              # sparsity: entropy of the fractions (most pixels are one thing)
    "w_tv": 0.40,               # temporal total variation of the fractions (L1: jumps allowed)
    "w_sp": 0.10,               # spatial TV, weighted by radar similarity of neighbours
    "w_land": 0.10,             # land end-member ~ pixel's bright-season appearance (2nd-highest date)
    "w_veg": 0.50,              # flooded vegetation needs vegetation (land HV not near the floor)
    "w_gain": 10.0,             # per-date gains have zero mean in dB (fixes the scale)
    "w_q": 0.05,                # per-pixel water brightness (dB) is cheap but not free
    "w_sparse": 1.0,            # 4 f_w (1 - f_w): water fraction near 0 or 1 unless the data insist
    "mix": "power",             # "power" (physics) or "log" (ablation: mix brightness linearly)
    "temporal": True,           # temporal blocks in the encoder (ablation: per-date only)
    "spatial": True,            # 3x3 convolutions (False: 1x1 = per-pixel network, no spatial context)
    "base": 16, "patch": 48, "batch": 8, "lr": 2e-3, "steps": 4000, "minutes": 8.0, "seed": 0,
}
VARIANTS = {
    "full": {},
    "no_tv": {"w_tv": 0.0},
    "no_entropy": {"w_ent": 0.0},
    "no_spatial_reg": {"w_sp": 0.0},
    "no_land_prior": {"w_land": 0.0},
    "no_sparse": {"w_sparse": 0.0},
    "no_floor": {"land_floor": (0.0, 0.0)},
    "log_mixing": {"mix": "log"},
    "no_temporal": {"temporal": False},
    "pixel_only": {"spatial": False},
    "db20": {"db": 20.0},
    "db40": {"db": 40.0},
    "lowq": {"q_max": 0.3, "w_q": 0.2},   # per-pixel water brightness only up to +3 dB, 4x its cost
}
AREA_WEIGHT = {"tonle-sap": 0.35, "sevan": 0.2, "ararat": 0.15}  # world areas share the rest


def cfg_of(variant="full", **over):
    c = dict(DEFAULTS)
    c.update(VARIANTS[variant])
    c.update(over)
    return c


def dev_default():
    return torch.device("mps" if torch.backends.mps.is_available() else "cpu")


def empty_cache():
    import gc
    gc.collect()
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()


# =============================================================================================
# brightness <-> power, soft display clip
# =============================================================================================
def to_power(b, db):
    return torch.pow(10.0, (b - 1.0) * db / 10.0)


def to_bright(I, db):
    return 1.0 + (10.0 / db) * torch.log10(I.clamp_min(1e-12))


def soft_clip(b, lo=FLOOR, hi=1.0, k=60.0):
    """Display range [lo, hi] with smooth corners (width ~1/k), so gradients survive near the floor/top."""
    b = lo + F.softplus(k * (b - lo)) / k
    return hi - F.softplus(k * (hi - b)) / k


def _inv_sig(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


# =============================================================================================
# physics: global parameters, scene parameters, decoder
# =============================================================================================
class Physics(nn.Module):
    """Global physical parameters, each squashed into a physically sensible range."""
    BW = (-0.06, 0.06)       # water end-member brightness: AT the display floor. Further below it, the soft clip
                             # is flat and roughness / per-pixel water brightness get no gradient (dead zone).
    GHH = (0.1, 0.6)         # log10 G_HH in [0.1, 0.6] -> +1..+6 dB (double bounce brightens HH)
    GHV = (-0.6, 0.0)        # log10 G_HV in [-0.6, 0] -> HV falls by 0..6 dB (floodveg.md: HV falls, HH stays
                             # bright). The first run allowed HV +5 dB and f_v became "brighter land" in general.

    def __init__(self, bw0=(0.0, 0.0), ghh_db=2.0, ghv_db=-3.0):
        super().__init__()
        lo, hi = self.BW
        self.bw_raw = nn.Parameter(torch.tensor([_inv_sig((v - lo) / (hi - lo)) for v in bw0]))
        self.ghh_raw = nn.Parameter(torch.tensor(_inv_sig((ghh_db / 10 - self.GHH[0]) / (self.GHH[1] - self.GHH[0]))))
        self.ghv_raw = nn.Parameter(torch.tensor(_inv_sig((ghv_db / 10 - self.GHV[0]) / (self.GHV[1] - self.GHV[0]))))

    def water_b(self):
        lo, hi = self.BW
        return lo + (hi - lo) * torch.sigmoid(self.bw_raw)

    def log10_g(self):
        lh = self.GHH[0] + (self.GHH[1] - self.GHH[0]) * torch.sigmoid(self.ghh_raw)
        lv = self.GHV[0] + (self.GHV[1] - self.GHV[0]) * torch.sigmoid(self.ghv_raw)
        return torch.stack([lh, lv])

    def summary(self):
        with torch.no_grad():
            return {"water_endmember_brightness": [round(float(v), 4) for v in self.water_b()],
                    "flooded_veg_gain_db": [round(float(10 * v), 2) for v in self.log10_g()]}


class Scene(nn.Module):
    """Per-scene, per-date parameters: water roughness r_t^c >= 1 and radiometric gain g_t^c."""
    R_MAX = 2.0     # log10 r in [0, 2] -> up to +20 dB
    G_MAX = 0.5     # residual gain within +-0.5 dB (ops already normalised the dates on stable pixels)

    def __init__(self, T):
        super().__init__()
        self.r_raw = nn.Parameter(torch.full((T, 2), _inv_sig(0.05)))
        self.g_raw = nn.Parameter(torch.zeros(T, 2))
        self.sw_raw = nn.Parameter(torch.full((2,), _inv_sig((0.05 - self.SW[0]) / (self.SW[1] - self.SW[0]))))
        self.nf = nn.Parameter(torch.zeros(2, 3))  # [:, 0] = learned offset (brightness) of the noise-floor map

    SW = (0.02, 0.15)   # extra brightness noise of open water in this scene (noise floor; Sevan HV is noisy)

    def water_sigma(self):
        lo, hi = self.SW
        return lo + (hi - lo) * torch.sigmoid(self.sw_raw)

    def log10_r(self):
        return self.R_MAX * torch.sigmoid(self.r_raw)

    def noise_b(self, floor):
        """Additive noise floor (NESZ) in brightness: the scene's floor MAP [B,2,H,W] (measured on its own open
        water, see noise_floor_map) + a learned per-channel offset."""
        return (floor + self.nf[:, 0].view(1, 2, 1, 1)).clamp(-0.3, 0.25)

    @torch.no_grad()
    def init_from_data(self, b, valid, db, bw=0.0, max_px=200_000):
        """Start the roughness at what the scene's surely-water pixels look like: pixels whose HH stays at the
        display floor on (almost) every date (2nd-brightest HH < 0.05). Their per-date median brightness above
        the noise-floor map gives log10 r = (b_med - b_w) * D / 10. Without this, a scene whose calm water has
        bright HV (Sevan: HV 0.12-0.2 at HH 0.01) splits into 'water' (HV at the floor) and 'land' (HV bright)
        and the roughness never moves (a self-reinforcing local minimum)."""
        st = pixel_stats(b, valid)
        pure = (st[4] < 0.05) & valid.all(0)
        n = int(pure.sum())
        if n < 2000:  # too little sure water (Ararat: 82 px): keep the defaults
            return n
        floor = noise_floor_map(b, valid, st)
        for t in range(b.shape[0]):
            for c in range(2):
                ex = float((b[t, c] - floor[c])[pure].median())
                lr = min(max((ex - bw) * db / 10.0 if ex > 0.03 else 0.02, 0.02), 0.95 * self.R_MAX)
                self.r_raw[t, c] = _inv_sig(lr / self.R_MAX)
        return n

    def gain_db(self):
        return self.G_MAX * torch.tanh(self.g_raw)

    def summary(self):
        with torch.no_grad():
            return {"water_roughness_db": (10 * self.log10_r()).tolist(), "gain_db": self.gain_db().tolist(),
                    "water_noise_sigma": self.water_sigma().tolist()}


def _box_mean(x, m, k):
    """Normalised box filter: mean of x over the pixels where m, in a k x k window. x [C,H,W], m [H,W]."""
    mf = m.float()[None, None]
    num = F.avg_pool2d((x * m)[None].float(), k, 1, k // 2, count_include_pad=False)[0]
    den = F.avg_pool2d(mf, k, 1, k // 2, count_include_pad=False)[0]
    return num / den.clamp_min(1e-6), den[0]


FLOOR_DEFAULT = -0.10


def noise_floor_map(b, valid, st=None, k=15):
    """The radar noise floor of the scene as a smooth MAP [2,H,W] in brightness, measured where it can be
    measured: dark pixels (2nd-brightest HH < 0.12) within ~15 px of surely-open water (HH at the display floor
    on almost every date).  Their season-median brightness, averaged in a k x k window, then filled outward
    with ever larger windows.  The noise floor of a SAR varies across the swath (NESZ); at Lake Sevan HV over
    open water is 0.05 in the south-east and 0.23 in the north-west bay.  A plane cannot follow that, and the
    model then called the whole bay 'dark land'.  Scenes with < 2000 surely-water pixels (Ararat) get a flat
    floor of FLOOR_DEFAULT (below the display floor: no noise).  Returned 0.02 below the measurement (the
    per-date roughness takes the rest)."""
    st = pixel_stats(b, valid) if st is None else st
    H, W = valid.shape[-2:]
    vall = valid.all(0)
    pure = (st[4] < 0.05) & vall
    if int(pure.sum()) < 2000:
        return torch.full((2, H, W), FLOOR_DEFAULT, device=b.device)
    near = F.max_pool2d(pure.float()[None, None], 31, 1, 15)[0, 0] > 0
    cand = near & (st[4] < 0.12) & vall
    val = st[2:4]
    out, den = _box_mean(val, cand, k)
    filled = den > 0.02
    for kk in (2 * k + 1, 4 * k + 1, 8 * k + 1):
        if bool(filled.all()):
            break
        o2, d2 = _box_mean(val, cand, kk)
        upd = ~filled & (d2 > 0.002)
        out = torch.where(upd[None], o2, out)
        filled = filled | upd
    med = val[:, cand].median(1).values.view(2, 1, 1)
    out = torch.where(filled[None], out, med)
    return (out - 0.02).clamp(-0.3, 0.25)


def decode(phys, scene, b_land, f, cfg, coords=None):
    # coords: the scene's noise-floor map [B,2,H,W] (brightness), or None (no noise floor)
    """Physics decoder.  b_land [B,2,H,W] land brightness, optionally [B,4,H,W] with channels 2:4 = log10 of a
    per-pixel water brightness factor q >= 1; f [B,T,3,H,W] fractions -> brightness [B,T,2,H,W]."""
    db = cfg["db"]
    lg = phys.log10_g().view(1, 1, 2, 1, 1)
    lr = scene.log10_r()[None, :, :, None, None]
    gdb = scene.gain_db()[None, :, :, None, None]
    bw = phys.water_b().view(1, 1, 2, 1, 1)
    bl = b_land[:, None, :2]
    lq = b_land[:, None, 2:4] if b_land.shape[1] == 4 else torch.zeros_like(bl)
    fw, fv, fl = f[:, :, 0:1], f[:, :, 1:2], f[:, :, 2:3]
    if cfg["mix"] == "power":
        Il = to_power(bl, db)
        I = fw * to_power(bw, db) * torch.pow(10.0, lr + lq) + fv * Il * torch.pow(10.0, lg) + fl * Il
        if coords is not None:  # additive noise floor of the scene, smooth across the swath (NESZ)
            I = I + to_power(scene.noise_b(coords), db)[:, None]
        b = to_bright(I * torch.pow(10.0, gdb / 10.0), db)
    else:  # ablation: linear mixing of the log-like brightness
        k = 10.0 / db
        b = fw * (bw + k * (lr + lq)) + fv * (bl + k * lg) + fl * bl + gdb / db
        if coords is not None:
            b = torch.maximum(b, scene.noise_b(coords)[:, None])
    return soft_clip(b)


# =============================================================================================
# encoder
# =============================================================================================
def _block(cin, cout, k):
    return nn.Sequential(nn.Conv2d(cin, cout, k, padding=k // 2), nn.GroupNorm(8, cout), nn.GELU(),
                         nn.Conv2d(cout, cout, k, padding=k // 2), nn.GroupNorm(8, cout), nn.GELU())


class UNet(nn.Module):
    """Small 2-level U-Net, applied to every date with shared weights."""

    def __init__(self, cin, base=32, spatial=True):
        super().__init__()
        k = 3 if spatial else 1
        self.spatial = spatial
        c1, c2, c3 = base, 3 * base, 6 * base   # wide where the resolution is low (cheap), thin at full res
        self.e1, self.e2, self.e3 = _block(cin, c1, k), _block(c1, c2, k), _block(c2, c3, k)
        self.u2 = nn.Conv2d(c3, c2, 1)
        self.d2 = _block(2 * c2, c2, k)
        self.u1 = nn.Conv2d(c2, c1, 1)
        self.d1 = _block(2 * c1, 2 * c1, k)

    def forward(self, x):
        if not self.spatial:  # per-pixel MLP (1x1 convs) at full resolution
            h1 = self.e1(x)
            h2 = self.e2(h1)
            h3 = self.e3(h2)
            return self.d1(torch.cat([self.u1(self.d2(torch.cat([self.u2(h3), h2], 1))), h1], 1))
        # (spatial=False: every conv is 1x1, so the network sees one pixel only)
        h1 = self.e1(x)
        h2 = self.e2(F.avg_pool2d(h1, 2))
        h3 = self.e3(F.avg_pool2d(h2, 2))
        d2 = self.d2(torch.cat([F.interpolate(self.u2(h3), size=h2.shape[-2:], mode="bilinear", align_corners=False), h2], 1))
        return self.d1(torch.cat([F.interpolate(self.u1(d2), size=h1.shape[-2:], mode="bilinear", align_corners=False), h1], 1))


class TBlock(nn.Module):
    """Temporal block per pixel: 1-D conv over dates (kernel 3) + the season mean broadcast to every date.
    (Written as a Conv2d over (date, pixel), which is much faster than Conv3d on the Apple GPU.)"""

    def __init__(self, C):
        super().__init__()
        self.c1 = nn.Conv2d(2 * C, C, (3, 1), padding=(1, 0))
        self.c2 = nn.Conv2d(C, C, 1)
        self.n = nn.GroupNorm(8, C)

    def forward(self, h, m):
        B, C, T, H, W = h.shape
        g = (h * m).sum(2, keepdim=True) / m.sum(2, keepdim=True).clamp_min(1)
        z = torch.cat([h, g.expand_as(h)], 1).reshape(B, 2 * C, T, H * W)
        z = self.c2(F.gelu(self.c1(z))).reshape(B, C, T, H, W)
        return F.gelu(self.n((h + z).reshape(B, C, T, H * W))).reshape(B, C, T, H, W)


N_STATS = 10


def pixel_stats(b, valid):
    """Per-pixel season statistics per channel: 2nd-lowest, median, 2nd-highest, std, mean of the brighter
    half of the dates ("bright-season level").  b [T,2,H,W] -> [10,H,W]."""
    T = b.shape[0]
    v = valid[:, None].expand_as(b)
    n = v.sum(0).clamp_min(1)
    lo = torch.where(v, b, torch.full_like(b, 9.0)).sort(0).values
    hi = torch.where(v, b, torch.full_like(b, -9.0)).sort(0, descending=True).values
    k = (n >= 3).long()  # second value only when there are >= 3 dates
    second_lo = torch.gather(lo, 0, k[None]).squeeze(0)
    second_hi = torch.gather(hi, 0, k[None]).squeeze(0)
    med = torch.gather(lo, 0, ((n - 1) // 2)[None]).squeeze(0)
    mean = (b * v).sum(0) / n
    sd = (((b - mean) ** 2) * v).sum(0).div(n).sqrt()
    nh = (n + 1) // 2
    rank = torch.arange(T, device=b.device).view(T, 1, 1, 1)
    upper = (torch.where(rank < nh[None], hi, torch.zeros_like(hi))).sum(0) / nh.clamp_min(1)
    out = torch.cat([second_lo, med, second_hi, sd, upper], 0)
    out = torch.where(n.repeat(5, 1, 1) > 0, out, torch.zeros_like(out))
    return out.clamp(-1, 2) * (valid.any(0)[None])


class Encoder(nn.Module):
    """Season U-Net (once per pixel, on the season statistics: spatial context of the whole stack)
    + a light per-date spatial CNN (shared over dates) + temporal blocks over the dates of each pixel.
    Heads: per-date fraction logits (softmax over the 3 types), and a time-constant land end-member."""

    def __init__(self, cfg):
        super().__init__()
        C = 2 * cfg["base"]
        k = 3 if cfg["spatial"] else 1
        self.cfg = cfg
        self.unet = UNet(N_STATS + 1, cfg["base"], cfg["spatial"])
        self.date = nn.Sequential(nn.Conv2d(3, C // 2, k, padding=k // 2), nn.GELU(),
                                  nn.Conv2d(C // 2, C, k, padding=k // 2), nn.GELU())
        self.mix = nn.Conv2d(2 * C, C, 1)
        self.tblocks = nn.ModuleList([TBlock(C), TBlock(C)]) if cfg["temporal"] else nn.ModuleList()
        self.frac = nn.Conv3d(C, 3, 1)  # 1x1x1: a per-pixel linear head
        self.land = nn.Sequential(nn.Conv2d(2 * C, C, 1), nn.GELU(), nn.Conv2d(C, 4, 1))
        nn.init.zeros_(self.frac.weight)
        with torch.no_grad():
            self.frac.bias.copy_(torch.tensor([-1.0, -1.0, 1.0]))  # start "mostly land"

    def forward(self, b, valid, stats):
        """b [B,T,2,H,W], valid [B,T,H,W], stats [B,10,H,W] -> fractions [B,T,3,H,W], b_land [B,2,H,W]."""
        B, T, _, H, W = b.shape
        m = valid[:, :, None].float()
        stats = stats[:, :N_STATS]
        hs = self.unet(torch.cat([stats, m.mean(1)], 1))                     # [B,C,H,W] season context
        hd = self.date(torch.cat([b * m, m], 2).reshape(B * T, 3, H, W))     # [B*T,C,H,W] this date
        C = hd.shape[1]
        h = self.mix(torch.cat([hd, hs[:, None].expand(B, T, C, H, W).reshape(B * T, C, H, W)], 1))
        h = F.gelu(h).reshape(B, T, C, H, W).permute(0, 2, 1, 3, 4)           # [B,C,T,H,W]
        mm = m.permute(0, 2, 1, 3, 4)
        for blk in self.tblocks:
            h = blk(h, mm)
        f = torch.softmax(self.frac(h), 1).permute(0, 2, 1, 3, 4)            # [B,T,3,H,W]
        pooled = (h * mm).sum(2) / mm.sum(2).clamp_min(1)
        lf = torch.tensor(self.cfg["land_floor"], device=b.device).view(1, 2, 1, 1)
        z = self.land(torch.cat([pooled, hs], 1))
        b_land = lf + (1.05 - lf) * torch.sigmoid(z[:, :2])
        lq = self.cfg.get("q_max", Q_MAX) * torch.sigmoid(z[:, 2:4] - 2.0)  # per-pixel water brightness, log10 q
        return f, torch.cat([b_land, lq], 1)


Q_MAX = 0.8  # per-pixel water brightness factor up to +8 dB (shallow / turbid / noise-floor water, e.g. Sevan HV)


class PhysAE(nn.Module):
    def __init__(self, cfg, scenes: dict):
        super().__init__()
        self.cfg = cfg
        self.enc = Encoder(cfg)
        self.phys = Physics()
        self.scenes = nn.ModuleDict({k.replace("-", "_"): Scene(T) for k, T in scenes.items()})

    def scene(self, name):
        return self.scenes[name.replace("-", "_")]

    def add_scene(self, name, T):
        self.scenes[name.replace("-", "_")] = Scene(T).to(next(self.parameters()).device)

    def forward(self, b, valid, stats, scene):
        f, bl = self.enc(b, valid, stats)
        return f, bl, decode(self.phys, self.scene(scene), bl, f, self.cfg, stats[:, N_STATS:N_STATS + 2])


def n_params(m):
    return sum(p.numel() for p in m.parameters())


# =============================================================================================
# loss
# =============================================================================================
def loss_terms(model, b, valid, stats, f, bl, bhat, scene, cfg):
    """All loss terms (each a scalar). b,bhat [B,T,2,H,W]; f [B,T,3,H,W]; bl [B,2,H,W]; stats [B,8,H,W]."""
    m = valid[:, :, None].float()
    nval = m.sum().clamp_min(1)
    # heteroscedastic noise: open water sits at the radar noise floor, where the preview brightness scatters
    # much more than on land (Sevan lake HV: 0.004-0.22 on one date). sigma^2 = sigma0^2 + f_w sigma_w^2, and
    # the log(sigma) term keeps the model from calling everything noisy water (Laplace-like NLL).
    sw = model.scene(scene).water_sigma().view(1, 1, 2, 1, 1)
    sig = torch.sqrt(cfg["sigma"] ** 2 + f[:, :, 0:1] * sw ** 2)
    r = (bhat - b) / sig
    rec = ((F.huber_loss(r, torch.zeros_like(r), reduction="none", delta=1.0) + torch.log(sig / cfg["sigma"])) * m).sum() / (2 * nval)
    ent = -(f * torch.log(f.clamp_min(1e-6))).sum(2, keepdim=True)
    ent = (ent * m).sum() / nval
    # temporal TV between consecutive valid dates
    pv = (m[:, 1:] * m[:, :-1])
    tv = ((f[:, 1:] - f[:, :-1]).abs().sum(2, keepdim=True) * pv).sum() / pv.sum().clamp_min(1)
    # spatial TV weighted by radar similarity of neighbouring pixels on the same date
    s2 = 2 * 0.05 ** 2
    wx = torch.exp(-((b[..., 1:] - b[..., :-1]) ** 2).sum(2, keepdim=True) / s2) * m[..., 1:] * m[..., :-1]
    wy = torch.exp(-((b[..., 1:, :] - b[..., :-1, :]) ** 2).sum(2, keepdim=True) / s2) * m[..., 1:, :] * m[..., :-1, :]
    sp = (((f[..., 1:] - f[..., :-1]).abs().sum(2, keepdim=True) * wx).sum()
          + ((f[..., 1:, :] - f[..., :-1, :]).abs().sum(2, keepdim=True) * wy).sum()) / (wx.sum() + wy.sum()).clamp_min(1)
    # land end-member ~ the pixel's bright-season level = mean of its brighter half of dates (>= land floor)
    lf = torch.tensor(cfg["land_floor"], device=b.device).view(1, 2, 1, 1)
    tgt = torch.maximum(stats[:, 8:10], lf)
    anyv = valid.any(1)[:, None].float()
    bl2 = bl[:, :2]
    land = (F.huber_loss((bl2 - tgt) / 0.05, torch.zeros_like(bl2), reduction="none") * anyv).sum() / (2 * anyv.sum()).clamp_min(1)
    # per-pixel water brightness costs a little where the pixel holds water (L1 in dB, x mean water fraction)
    fwm = (f[:, :, 0] * m[:, :, 0]).sum(1) / m[:, :, 0].sum(1).clamp_min(1)
    qpen = (fwm[:, None] * 10 * bl[:, 2:4]).mean() if bl.shape[1] == 4 else torch.zeros((), device=b.device)
    # flooded vegetation needs vegetation: penalise f_v where the land HV is dark (no canopy volume)
    veg = (f[:, :, 1] * F.relu(0.25 - bl[:, None, 1]) / 0.25 * m[:, :, 0]).sum() / nval
    gd = model.scene(scene).gain_db()
    gain = (gd.mean(0) ** 2).sum() + 0.1 * (gd ** 2).mean()
    # water is all-or-nothing unless the data say otherwise: 4 f_w (1 - f_w) (0 at f_w = 0 and 1, 1 at 0.5).
    # In power a small water fraction hardly changes the brightness, so without this "a slightly brighter land
    # + 30 % water" fits as well as "plain land"; and a pixel at the display floor fits "50 % water + 50 % land
    # at the land floor" as well as "100 % water". A one-sided L1 on f_w fixed the first and broke the second.
    fw = f[:, :, 0:1]
    sparse = (4 * fw * (1 - fw) * m).sum() / nval
    return {"rec": rec, "ent": ent, "tv": tv, "sp": sp, "land": land, "veg": veg, "gain": gain, "sparse": sparse,
            "q": qpen}


def total_loss(t, cfg, ent_scale=1.0):
    return (t["rec"] + ent_scale * (cfg["w_ent"] * t["ent"] + cfg["w_sparse"] * t["sparse"]) + cfg["w_tv"] * t["tv"] + cfg["w_sp"] * t["sp"]
            + cfg["w_land"] * t["land"] + cfg["w_veg"] * t["veg"] + cfg["w_gain"] * t["gain"] + cfg["w_q"] * t["q"])


# =============================================================================================
# data
# =============================================================================================
def input_path(area):
    return MODELS / f"unmix_inputs_{area}.pt"


def prepare(area, dev=None):
    """Run the shared preprocessing once and cache what the model and the evaluation need.

    b = the UNFILTERED preview brightness, with ops' footprint rules and ops' per-date gains applied
    (the 3x3 speckle filter would spread every shoreline over three pixels; the encoder denoises itself).
    """
    from scripts.detect import ops
    from scripts.detect.stack import load_preview_stack
    from scripts.detect.terrain import cached_water_possible
    dev = dev or dev_default()
    t0 = time.time()
    st = load_preview_stack(area)
    H, W = st.x.shape[-2:]
    wok = cached_water_possible(area, (H, W))
    out = ops.run_pipeline(st.x, st.valid, "preview", dev=dev, water_ok=wok)
    valid = out["valid"].cpu()
    x, _ = ops.clean_input(st.x, st.valid)
    g = out["gains"] if out["gains"] is not None else torch.ones(x.shape[0], 2)
    b = (x * g[:, :, None, None]).clamp(0, 1) * valid[:, None]
    d = {"area": area, "dates": st.dates, "bounds": st.bounds, "km2_per_px": st.km2_per_px,
         "b": b.half(), "valid": valid, "L": out["L"].cpu().half(),
         "water": out["water"].cpu(), "water_no_terrain": ops.water_masks(out["L"], out["valid"], out["knobs"]).cpu(),
         "flooded_veg_ops": out["flooded_veg"].cpu(), "prior_water": out["prior_water"].cpu(),
         "classes": out["classes"].cpu(), "water_ok": None if wok is None else wok.cpu(), "gains": g}
    del out, st, x
    empty_cache()
    MODELS.mkdir(parents=True, exist_ok=True)
    torch.save(d, input_path(area))
    print(f"  {area}: {tuple(b.shape)} in {time.time() - t0:.1f}s, terrain={'yes' if wok is not None else 'no'}")
    return d


def load_inputs(area):
    d = torch.load(input_path(area), weights_only=False)
    d["b"] = impute_bad_channels(area, d["b"].float(), d["valid"], d["dates"])
    return d


def qc_bad_channels(area, dates):
    """[(date index, channel index)] that quality control flags as faulty (data/processed/<area>/qc.json,
    summary.exclude_channels; Armenia's 6 October HV)."""
    p = ROOT / f"data/processed/{area}/qc.json"
    if not p.exists():
        return []
    q = json.loads(p.read_text()).get("summary", {}).get("exclude_channels", {}) or {}
    out = []
    for dt, chans in q.items():
        if dt in dates:
            out += [(dates.index(dt), {"HH": 0, "HV": 1}[c]) for c in chans if c in ("HH", "HV")]
    return out


def impute_bad_channels(area, b, valid, dates):
    """Replace a faulty channel-date by the pixel's median of that channel over its other valid dates (the
    model has one validity mask for both channels; the imputed value carries no information, so the HV
    roughness of that date is meaningless and is reported as such)."""
    bad = qc_bad_channels(area, list(dates))
    if not bad:
        return b
    b = b.clone()
    for t, c in bad:
        others = [u for u in range(b.shape[0]) if u != t]
        x = torch.where(valid[others], b[others, c], torch.full_like(b[others, c], float("nan")))
        med = torch.nanmedian(x, 0).values.nan_to_num(0.0)
        b[t, c] = torch.where(valid[t], med, b[t, c])
    return b


class AreaData:
    """CPU tensors of one area + random patch sampling."""

    def __init__(self, d, patch):
        self.name = d["area"]
        self.b = d["b"].float()
        self.valid = d["valid"]
        self.T = self.b.shape[0]
        H0, W0 = self.valid.shape[-2:]
        # season statistics (encoder input) + normalised position (decoder only: the noise floor surface)
        st = pixel_stats(self.b, self.valid)
        self.stats = torch.cat([st, noise_floor_map(self.b, self.valid, st)], 0)
        self.wok = d.get("water_ok")
        H, W = self.valid.shape[-2:]
        p = min(patch, H, W)
        self.p = p
        cov = F.avg_pool2d(self.valid.all(0)[None, None].float(), p, stride=max(p // 4, 1))[0, 0]
        ys, xs = torch.nonzero(cov >= 0.6, as_tuple=True)
        s = max(p // 4, 1)
        self.corners = torch.stack([ys * s, xs * s], 1)

    def sample(self, n, gen):
        idx = torch.randint(0, len(self.corners), (n,), generator=gen)
        out_b, out_v, out_s = [], [], []
        for i in idx.tolist():
            y, x = self.corners[i].tolist()
            sl = (slice(y, y + self.p), slice(x, x + self.p))
            bb, vv, ss = self.b[(slice(None), slice(None)) + sl], self.valid[(slice(None),) + sl], self.stats[(slice(None),) + sl]
            k = int(torch.randint(0, 8, (1,), generator=gen))
            if k & 1:
                bb, vv, ss = bb.flip(-1), vv.flip(-1), ss.flip(-1)
            if k & 2:
                bb, vv, ss = bb.flip(-2), vv.flip(-2), ss.flip(-2)
            if k & 4:
                bb, vv, ss = bb.transpose(-1, -2), vv.transpose(-1, -2), ss.transpose(-1, -2)
            out_b.append(bb), out_v.append(vv), out_s.append(ss)
        return torch.stack(out_b), torch.stack(out_v), torch.stack(out_s)


# =============================================================================================
# training
# =============================================================================================
def train_model(datas: list[AreaData], cfg, dev=None, log_every=200, verbose=True, model=None, weights=None):
    dev = dev or dev_default()
    torch.manual_seed(cfg["seed"])
    gen = torch.Generator().manual_seed(cfg["seed"])
    if model is None:
        model = PhysAE(cfg, {d.name: d.T for d in datas})
        for d in datas:
            model.scene(d.name).init_from_data(d.b, d.valid, cfg["db"])
    model = model.to(dev)
    enc_p = list(model.enc.parameters())
    opt = torch.optim.AdamW([{"params": enc_p, "lr": cfg["lr"], "weight_decay": 1e-4},
                             {"params": list(model.phys.parameters()) + list(model.scenes.parameters()),
                              "lr": 3e-2, "weight_decay": 0.0}])
    steps = cfg["steps"]
    base_lr = [cfg["lr"], 3e-2]
    budget = 60 * cfg["minutes"]

    def set_lr(step):
        # warm-up 3 %, then cosine; progress = max(step fraction, time fraction) so the schedule always
        # anneals inside the time budget whatever the GPU load
        prog = max(step / steps, (time.time() - t0) / budget)
        fac = min(1.0, prog / 0.03) if prog < 0.03 else 0.5 * (1 + math.cos(math.pi * min(1.0, prog)))
        for g, lr in zip(opt.param_groups, base_lr):
            g["lr"] = lr * max(fac, 0.02)
    if weights is None:
        rest = 1.0 - sum(AREA_WEIGHT.get(d.name, 0) for d in datas)
        n_world = sum(d.name not in AREA_WEIGHT for d in datas)
        weights = [AREA_WEIGHT.get(d.name, rest / max(n_world, 1)) for d in datas]
    wts = torch.tensor(weights, dtype=torch.float)
    wts = wts / wts.sum()
    hist = []
    t0 = time.time()
    model.train()
    for step in range(steps):
        set_lr(step)
        a = datas[int(torch.multinomial(wts, 1, generator=gen))]
        b, v, s = a.sample(cfg["batch"], gen)
        b, v, s = b.to(dev), v.to(dev), s.to(dev)
        f, bl, bhat = model(b, v, s, a.name)
        terms = loss_terms(model, b, v, s, f, bl, bhat, a.name, cfg)
        # entropy and the water all-or-nothing term are switched on gradually from 40 % to 80 % of the run:
        # from the start they trap rarely used fractions at zero (d(-f log f)/df -> +inf as f -> 0)
        prog = max(step / steps, (time.time() - t0) / budget)
        loss = total_loss(terms, cfg, ent_scale=min(1.0, max(0.0, (prog - 0.4) / 0.4)))
        opt.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
        if step % log_every == 0 or step == steps - 1:
            row = {"step": step, "area": a.name, "t": round(time.time() - t0, 1), "loss": round(float(loss.detach()), 4),
                   **{k: round(float(x.detach()), 4) for k, x in terms.items()}}
            hist.append(row)
            if verbose:
                print("   ", row, flush=True)
        if (time.time() - t0) > 60 * cfg["minutes"]:
            if verbose:
                print(f"    time budget reached at step {step}")
            break
    model.eval()
    return model, {"history": hist, "seconds": round(time.time() - t0, 1), "steps_done": step + 1}


def fit_scene(model, data: AreaData, steps=200, dev=None, batch=8, seed=0):
    """Test-time fit of the per-date scene parameters (roughness, gain) of a new scene, encoder frozen."""
    dev = dev or next(model.parameters()).device
    if data.name.replace("-", "_") not in model.scenes:
        model.add_scene(data.name, data.T)
        model.scene(data.name).init_from_data(data.b.to(dev), data.valid.to(dev), model.cfg["db"])
    sc = model.scene(data.name)
    opt = torch.optim.Adam(sc.parameters(), lr=3e-2)
    gen = torch.Generator().manual_seed(seed)
    for p in model.enc.parameters():
        p.requires_grad_(False)
    for p in model.phys.parameters():
        p.requires_grad_(False)
    for _ in range(steps):
        b, v, s = data.sample(batch, gen)
        b, v, s = b.to(dev), v.to(dev), s.to(dev)
        with torch.no_grad():
            f, bl = model.enc(b, v, s)
        bhat = decode(model.phys, sc, bl, f, model.cfg, s[:, N_STATS:N_STATS + 2])
        t = loss_terms(model, b, v, s, f, bl, bhat, data.name, model.cfg)
        loss = t["rec"] + model.cfg["w_gain"] * t["gain"]
        opt.zero_grad()
        loss.backward()
        opt.step()
    for p in model.parameters():
        p.requires_grad_(True)
    return model


def save_model(model, variant, info, tag=None):
    MODELS.mkdir(parents=True, exist_ok=True)
    p = MODELS / f"unmix_{tag or variant}.pt"
    torch.save({"cfg": model.cfg, "state": model.state_dict(), "scenes": {k: int(s.r_raw.shape[0]) for k, s in model.scenes.items()},
                "variant": variant, "info": info}, p)
    return p


def load_model(tag="full", dev=None):
    ck = torch.load(MODELS / f"unmix_{tag}.pt", weights_only=False)
    cfg = ck["cfg"]
    cfg["land_floor"] = tuple(cfg["land_floor"])
    m = PhysAE(cfg, {})
    for k, T in ck["scenes"].items():
        m.scenes[k] = Scene(T)
    m.load_state_dict(ck["state"])
    return m.to(dev or dev_default()).eval(), ck


# =============================================================================================
# inference
# =============================================================================================
@torch.no_grad()
def infer(model, data: AreaData, tile=256, margin=24, dev=None, recon=True):
    """Full-scene inference in overlapping tiles. Returns CPU tensors f [T,3,H,W], b_land [2,H,W], bhat."""
    dev = dev or next(model.parameters()).device
    T, _, H, W = data.b.shape
    f_out = torch.zeros(T, 3, H, W, dtype=torch.float16)
    bl_out = torch.zeros(4, H, W)
    bh_out = torch.zeros(T, 2, H, W, dtype=torch.float16) if recon else None
    step = tile - 2 * margin
    sc = model.scene(data.name)
    for y0 in range(0, H, step):
        for x0 in range(0, W, step):
            ya, xa = max(y0 - margin, 0), max(x0 - margin, 0)
            yb, xb = min(y0 + step + margin, H), min(x0 + step + margin, W)
            v = data.valid[:, ya:yb, xa:xb]
            if not bool(v.any()):
                continue
            b = data.b[:, :, ya:yb, xa:xb][None].to(dev)
            s = data.stats[:, ya:yb, xa:xb][None].to(dev)
            hh, ww = yb - ya, xb - xa
            ph, pw = (-hh) % 4, (-ww) % 4  # U-Net pools twice
            vv = v[None].to(dev)
            if ph or pw:
                b = F.pad(b.flatten(1, 2), (0, pw, 0, ph)).unflatten(1, (T, 2))
                s = F.pad(s, (0, pw, 0, ph))
                vv = F.pad(vv.float(), (0, pw, 0, ph)).bool()
            f, bl = model.enc(b, vv, s)
            oy, ox = y0 - ya, x0 - xa
            ye, xe = min(y0 + step, H), min(x0 + step, W)
            f_out[:, :, y0:ye, x0:xe] = f[0, :, :, oy:oy + ye - y0, ox:ox + xe - x0].half().cpu()
            bl_out[:, y0:ye, x0:xe] = bl[0, :, oy:oy + ye - y0, ox:ox + xe - x0].cpu()
            if recon:
                bh = decode(model.phys, sc, bl, f, model.cfg, s[:, N_STATS:N_STATS + 2])
                bh_out[:, :, y0:ye, x0:xe] = bh[0, :, :, oy:oy + ye - y0, ox:ox + xe - x0].half().cpu()
            del b, s, vv, f, bl
    vm = data.valid[:, None]
    f_out = f_out * vm
    return f_out, bl_out, bh_out


def prior_driven(stats, valid, cfg, noise=None, dark=0.30):
    """Pixels whose water fraction comes from the priors, not from the data: DARK (2nd-brightest HH < `dark`,
    but above the display floor) and FLAT in time (2nd-highest - 2nd-lowest within the scene's own noise level,
    in both channels).  For these, '100 % dark land' and 'part water + brighter land' fit the radar equally
    well; the land floor / land-prior decides.  Noise level = 1.25 x the scene median of that range (most
    pixels of a scene are stable land, so the median range is speckle), at least 0.06."""
    rng = stats[4:6] - stats[0:2]
    vany = valid.any(0)
    if noise is None:
        med = rng[:, vany].median(1).values if bool(vany.any()) else torch.full((2,), 0.05)
        noise = (1.25 * med).clamp_min(0.06).view(2, 1, 1)
    flat = (rng < noise).all(0)
    darkish = (stats[4] < dark) & (stats[4] > 0.03)
    return flat & darkish & vany


# =============================================================================================
# baselines
# =============================================================================================
def analytic_fraction(b, valid, cfg, bw=FLOOR, wok=None):
    """One-line two-end-member fraction in power, HH: f = clip((I_l - I)/(I_l - I_w), 0, 1), with the land
    end-member = the pixel's own 2nd-brightest HH date (floored at the land floor), water at the display floor."""
    st = pixel_stats(b, valid)
    bl = torch.clamp(st[4], min=cfg["land_floor"][0])
    Il, Iw = to_power(bl, cfg["db"]), to_power(torch.tensor(bw), cfg["db"])
    I = to_power(b[:, 0], cfg["db"])
    f = ((Il - I) / (Il - Iw).clamp_min(1e-9)).clamp(0, 1) * valid
    if wok is not None:
        f = f * wok
    return f


def classical_unmix(b, valid, model, scene, steps=250, dev=None, rows=256):
    """Per-pixel, per-date unmixing with FIXED end-members (no network, no context): land = the pixel's
    2nd-brightest date (floored), flooded veg = G * land, water = the model's E_w x roughness of the date.
    Fractions on the simplex (softmax) by Adam on the same Huber reconstruction loss.  Returns f [T,3,H,W]."""
    dev = dev or next(model.parameters()).device
    cfg = model.cfg
    T, _, H, W = b.shape
    st = pixel_stats(b, valid)
    lf = torch.tensor(cfg["land_floor"]).view(2, 1, 1)
    bl_all = torch.maximum(st[8:10], lf)
    sc = model.scene(scene)
    coords = noise_floor_map(b, valid, st)
    out = torch.zeros(T, 3, H, W, dtype=torch.float16)
    for y0 in range(0, H, rows):
        y1 = min(y0 + rows, H)
        bb = b[:, :, y0:y1].to(dev)[None]
        vv = valid[:, y0:y1].to(dev)[None, :, None].float()
        bl = bl_all[:, y0:y1].to(dev)[None]
        z = torch.zeros(1, T, 3, y1 - y0, W, device=dev)
        z[:, :, 2] = 1.0
        z.requires_grad_(True)
        opt = torch.optim.Adam([z], lr=0.1)
        for _ in range(steps):
            f = torch.softmax(z, 2)
            bh = decode(model.phys, sc, bl, f, cfg, coords[:, y0:y1].to(dev)[None])
            r = (bh - bb) / cfg["sigma"]
            loss = (F.huber_loss(r, torch.zeros_like(r), reduction="none") * vv).sum()
            opt.zero_grad()
            loss.backward()
            opt.step()
        out[:, :, y0:y1] = (torch.softmax(z, 2)[0] * valid[:, None, y0:y1].to(dev)).detach().half().cpu()
        del bb, vv, bl, z, opt
    for p in model.parameters():
        p.grad = None
    return out


# =============================================================================================
# synthetic truth
# =============================================================================================
def _smooth_noise(H, W, scale, gen):
    n = torch.randn(1, 1, H // scale + 3, W // scale + 3, generator=gen)
    n = F.interpolate(n, size=(H + 3 * scale, W + 3 * scale), mode="bicubic", align_corners=False)
    return n[0, 0, scale:scale + H, scale:scale + W]


def synth_scene(H=128, W=128, T=10, seed=0, db=30.0, looks=12, wind_date=5, wind_db=(5.0, 8.0),
                gain_sd_db=0.3, g_db=(3.0, -4.0), ss=8, normalise=True):
    """Known-truth scene. Returns dict(b [T,2,H,W], valid, f [T,3,H,W] truth, E_l [2,H,W], masks, params).

    Land: textured fields (HH ~0.45, HV ~0.33) with forest blocks (0.82, 0.85); a constant dark patch
    (0.18, 0.10) = dark land; a constant 'mixed' patch = 70 % water + 30 % field (same look as dark land,
    the identifiability case). Lake: a disc that grows (true sub-pixel fractions by 8x8 supersampling).
    Forest ring around the lake floods (double bounce) from date 3, then the growing lake submerges part
    of it. A marsh with smoothly varying partial water fraction from date 6. Wind on one date, per-date gain
    drift, multiplicative speckle (gamma, `looks`), display quantisation and clipping.
    """
    gen = torch.Generator().manual_seed(seed)
    yy, xx = torch.meshgrid(torch.arange(H).float(), torch.arange(W).float(), indexing="ij")
    tex = _smooth_noise(H, W, 8, gen)
    bl_hh = 0.45 + 0.07 * tex
    bl_hv = 0.33 + 0.06 * _smooth_noise(H, W, 8, gen)
    forest = (_smooth_noise(H, W, 16, gen) > 0.9)
    cy, cx = H * 0.45, W * 0.45
    rad0 = 0.16 * H
    radii = [rad0 + max(0, t - 2) * 0.02 * H for t in range(T)]
    ring_in, ring_out = radii[-1] - 0.06 * H, radii[-1] + 0.05 * H
    d = ((yy - cy) ** 2 + (xx - cx) ** 2).sqrt()
    ring = (d > rad0 + 1.5) & (d < ring_out)
    forest = forest | ring
    bl_hh = torch.where(forest, 0.80 + 0.03 * tex, bl_hh)
    bl_hv = torch.where(forest, 0.82 + 0.03 * tex, bl_hv)
    dark = (yy > H * 0.80) & (yy < H * 0.92) & (xx > W * 0.08) & (xx < W * 0.25)
    mixed = (yy > H * 0.80) & (yy < H * 0.92) & (xx > W * 0.32) & (xx < W * 0.49)
    marsh = (yy > H * 0.06) & (yy < H * 0.30) & (xx > W * 0.70) & (xx < W * 0.94)
    # true fractions
    sub = (torch.arange(ss).float() + 0.5) / ss - 0.5
    sy, sx = torch.meshgrid(sub, sub, indexing="ij")
    f = torch.zeros(T, 3, H, W)
    marsh_f = (0.5 + 0.35 * _smooth_noise(H, W, 6, gen)).clamp(0, 1)
    for t in range(T):
        dd = ((yy[..., None, None] + sy - cy) ** 2 + (xx[..., None, None] + sx - cx) ** 2).sqrt()
        fw = (dd < radii[t]).float().mean((-1, -2))
        fwm = torch.where(marsh & (t >= 6), marsh_f * min(1.0, (t - 5) / 3), torch.zeros_like(fw))
        fw = torch.maximum(fw, fwm)
        fw = torch.where(mixed, torch.full_like(fw, 0.7), fw)
        fv = torch.where(ring & (t >= 3), 1.0 - fw, torch.zeros_like(fw))
        f[t, 0], f[t, 1], f[t, 2] = fw, fv, 1 - fw - fv
    bl_hh = torch.where(dark, torch.full_like(bl_hh, 0.18), bl_hh)
    bl_hv = torch.where(dark, torch.full_like(bl_hv, 0.10), bl_hv)
    # make the mixed patch's land a field so it looks like dark land: 0.7 water + 0.3 field
    bl_hh = torch.where(mixed, torch.full_like(bl_hh, 0.45), bl_hh)
    bl_hv = torch.where(mixed, torch.full_like(bl_hv, 0.33), bl_hv)
    El = torch.stack([bl_hh, bl_hv])
    Il = to_power(El, db)
    G = torch.pow(10.0, torch.tensor(g_db) / 10).view(2, 1, 1)
    Iw0 = to_power(torch.tensor([-0.05, -0.05]), db).view(2, 1, 1)
    r_db = torch.zeros(T, 2)
    r_db[:, :] = torch.tensor([0.5, 0.5])
    if wind_date is not None:
        r_db[wind_date] = torch.tensor(wind_db)
    gain = torch.randn(T, 2, generator=gen) * gain_sd_db
    gain = gain - gain.mean(0)
    b = torch.zeros(T, 2, H, W)
    for t in range(T):
        I = f[t, 0] * Iw0 * torch.pow(10.0, r_db[t].view(2, 1, 1) / 10) + f[t, 1] * Il * G + f[t, 2] * Il
        I = I * torch.pow(10.0, gain[t].view(2, 1, 1) / 10)
        I = I * torch.distributions.Gamma(torch.full_like(I, float(looks)), torch.full_like(I, float(looks))).sample()
        b[t] = to_bright(I, db)
    b = (torch.round(b.clamp(FLOOR, 1) * 255) / 255).clamp(FLOOR, 1)
    valid = torch.ones(T, H, W, dtype=torch.bool)
    if normalise:  # the same relative radiometric normalisation as ops.run_pipeline (stable pixels, gain mode)
        from scripts.detect import ops
        med = b[:, 0].median(0).values
        b, _, _ = ops.normalise_dates(b, valid, 0.2, exclude=~(med >= 0.12), mode="gain")
        b = b.clamp(0, 1)
    return {"area": f"synthetic-{seed}", "b": b, "valid": valid, "f": f, "E_l": El, "dates": [f"d{t}" for t in range(T)],
            "masks": {"dark_land": dark, "mixed_constant": mixed, "marsh": marsh, "ring": ring, "forest": forest},
            "params": {"water_roughness_db": r_db.tolist(), "gain_db": gain.tolist(), "g_db": list(g_db), "wind_date": wind_date},
            "km2_per_px": 1.0}


def hard_threshold_synth(b, valid):
    """The math detector's open-water rule on the synthetic brightness (hysteresis 0.06 / 0.12, HV < 0.35)."""
    from scripts.detect import ops
    kn = dict(ops.KNOBS["preview"])
    return ops.water_masks(b, valid, kn).float()


def rmse(a, b, m=None):
    d = (a.float() - b.float()) ** 2
    if m is not None:
        d = d[m.expand_as(d)] if m.dim() < d.dim() else d[m]
    return float(d.mean().sqrt())


# =============================================================================================
# integration
# =============================================================================================
_MODEL_CACHE = {}


def unmix(L_or_x, valid, area=None, tag="full", dev=None, fit_steps=200, water_ok=None):
    """Integration entry.  L_or_x [T,2,H,W] preview brightness (unfiltered, gain-normalised is best; the
    filtered ops 'L' also works), valid [T,H,W].  If `area` is a scene the model was trained on, its learned
    roughness / gain are used; otherwise they are fitted in a few seconds with the encoder frozen.
    Returns dict f_water, f_fv, f_land [T,H,W] float16, land_endmember [2,H,W], water_roughness_db [T,2],
    recon_rmse, prior_driven [H,W] bool."""
    dev = dev or dev_default()
    if tag not in _MODEL_CACHE:
        _MODEL_CACHE[tag] = load_model(tag, dev)[0]
    model = _MODEL_CACHE[tag]
    name = area or "external"
    d = AreaData({"area": name, "b": L_or_x.float().cpu(), "valid": valid.cpu().bool()}, model.cfg["patch"])
    if name.replace("-", "_") not in model.scenes or model.scene(name).r_raw.shape[0] != d.T:
        if name.replace("-", "_") in model.scenes:
            del model.scenes[name.replace("-", "_")]
        fit_scene(model, d, steps=fit_steps, dev=dev)
    f, bl, bh = infer(model, d, dev=dev)
    if water_ok is not None:
        f = apply_terrain(f, water_ok)
    vm = d.valid[:, None]
    res = ((bh.float() - d.b) ** 2 * vm).sum() / (2 * vm.sum()).clamp_min(1)
    return {"f_water": f[:, 0], "f_fv": f[:, 1], "f_land": f[:, 2], "land_endmember": bl[:2],
            "water_brightness_db": 10 * bl[2:4],
            "water_roughness_db": (10 * model.scene(name).log10_r()).detach().cpu(),
            "recon_rmse": float(res.sqrt()), "prior_driven": prior_driven(d.stats, d.valid, model.cfg)}


def apply_terrain(f, water_ok):
    """Terrain prior at inference: standing water (open, or under a canopy) is impossible where water_possible is
    false; the water and flooded-vegetation shares go to land."""
    f = f.clone()
    bad = (~water_ok.bool())[None]
    z = torch.zeros_like(f[:, 0])
    fl = f[:, 2] + torch.where(bad, f[:, 0] + f[:, 1], z)
    f[:, 0] = torch.where(bad, z, f[:, 0])
    f[:, 1] = torch.where(bad, z, f[:, 1])
    f[:, 2] = fl
    return f


# =============================================================================================
# real-data evaluation
# =============================================================================================
def _r(x, nd=3):
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else round(float(x), nd)


def _ranks(x):
    x = x.double()
    o = torch.argsort(x)
    _, inv, cnt = torch.unique_consecutive(x[o], return_inverse=True, return_counts=True)
    avg = cnt.cumsum(0).double() - (cnt.double() - 1) / 2
    r = torch.empty_like(x)
    r[o] = avg[inv]
    return r


def pearson(a, b):
    a, b = a.double().flatten(), b.double().flatten()
    a, b = a - a.mean(), b - b.mean()
    return float((a * b).sum() / (a.norm() * b.norm()).clamp_min(1e-12))


def spearman(a, b):
    return pearson(_ranks(a.flatten()), _ranks(b.flatten()))


def jrc_ref(area, d):
    from scripts.detect import validate
    H, W = d["valid"].shape[-2:]
    ref, _ = validate.reference_on_grid(area, d["bounds"], H, W)
    return ref


def _dilate(m, k):
    from scripts.detect import ops
    return ops.dilate(m[None], k)[0]


def windy_idx(d):
    return [i for i, t in enumerate(d["dates"]) if t in ("2026-08-31", "2026-09-12")] if d["area"] == "sevan" else []


def method_metrics(area, d, ref, fw, ffv=None, dense=True):
    """Metrics of one method. fw, ffv [T,H,W] float (fractions or 0/1 masks)."""
    from scripts.detect import tune
    km2 = d["km2_per_px"]
    valid = d["valid"]
    T = valid.shape[0]
    fw = fw.float() * valid
    ffv = None if ffv is None else ffv.float() * valid
    dom = valid.all(0) & (ref["valid_frac"] >= 0.5) if ref is not None else valid.all(0)
    M = {"water_km2_frac": [_r(float(fw[t][dom].sum()) * km2, 1) for t in range(T)],
         "water_km2_ge05": [_r(float((fw[t] >= 0.5)[dom].sum()) * km2, 1) for t in range(T)]}
    if ffv is not None:
        M["fv_km2_frac"] = [_r(float(ffv[t][dom].sum()) * km2, 1) for t in range(T)]
    if ref is None:
        return M
    ext = ref["ext_frac"] >= 0.5
    M["water_mass_inside_jrc_max_extent"] = [_r(float(fw[t][dom & ext].sum()) / max(float(fw[t][dom].sum()), 1e-9), 4) for t in range(T)]
    near = _dilate((ref["ext_frac"] > 0) & dom, 7)
    M["water_km2_far_from_any_jrc_water"] = [_r(float(fw[t][dom & ~near].sum()) * km2, 1) for t in range(T)]
    if ffv is not None:
        M["fv_km2_far_from_any_jrc_water"] = [_r(float(ffv[t][dom & ~near].sum()) * km2, 1) for t in range(T)]
    fp = dom & ext & (ref["occ_ge90_frac"] < 0.5)
    if int(fp.sum()) > 100:
        M["floodplain_spearman_occurrence_vs_mean_fwater"] = _r(spearman(ref["occ_mean"][fp], fw.mean(0)[fp]), 3)
    # anchors on the TEST strip (tune.py labels and split, threshold the fraction at 0.5)
    lab = tune.build_labels(ref, valid.all(0))
    strip = tune.split_masks(*valid.shape[-2:], tune.SPLIT[area])["test"]
    perm, never = lab["perm"] & strip, lab["never"] & strip
    wet = fw >= 0.5
    rec = float(wet[:, perm].float().mean()) if int(perm.sum()) else float("nan")
    fpr = float(wet[:, never].float().mean()) if int(never.sum()) else float("nan")
    M["test_anchors"] = {"n_perm": int(perm.sum()), "n_never": int(never.sum()), "recall_perm": _r(rec, 4),
                         "fpr_never": _r(fpr, 4), "balanced_acc": _r(0.5 * (rec + 1 - fpr), 4) if int(perm.sum()) else None,
                         "false_km2_per_date_ge05": _r(fpr * int(never.sum()) * km2, 1),
                         "false_km2_per_date_frac": _r(float(fw[:, never].sum()) / T * km2, 1),
                         "mean_fwater_on_perm": _r(float(fw[:, perm].mean()), 4) if int(perm.sum()) else None}
    if area == "sevan":
        M.update(sevan_metrics(d, ref, fw, dom, dense))
    return M


def sevan_masks(d, ref):
    valid = d["valid"]
    dom = valid.all(0) & (ref["valid_frac"] >= 0.5)
    rf = ref["occ_mean"] / 100.0
    mixed = dom & (rf > 0.05) & (rf < 0.95)
    band = _dilate(mixed, 5) & dom
    lake = _dilate(ref["ext_frac"] > 0.5, 15) & dom
    # keep only the big lake's zone (Sevan): largest connected part of `lake`
    from scripts.detect import ops
    lab = ops.label_components(lake[None])[0]
    ids, cnt = torch.unique(lab[lab > 0], return_counts=True)
    lake = lab == ids[cnt.argmax()]
    return {"dom": dom, "ref_f": rf, "mixed": mixed, "band": band, "lake": lake}


def sevan_metrics(d, ref, fw, dom, dense=True):
    S = sevan_masks(d, ref)
    km2 = d["km2_per_px"]
    T = fw.shape[0]
    wi = windy_idx(d)
    calm = [t for t in range(T) if t not in wi]
    pred = fw[calm].mean(0)
    out = {}
    for nm in ("mixed", "band"):
        m = S[nm]
        p, r = pred[m], S["ref_f"][m]
        out[f"shore_{nm}"] = {"n": int(m.sum()), "rmse": _r(((p - r) ** 2).mean().sqrt(), 4), "bias": _r((p - r).mean(), 4),
                              "pearson": _r(pearson(p, r), 4), "mae": _r((p - r).abs().mean(), 4)}
        if wi:
            pw = fw[wi].mean(0)[m]
            out[f"shore_{nm}"]["rmse_windy_dates"] = _r(((pw - r) ** 2).mean().sqrt(), 4)
    # reliability on the band
    p, r = pred[S["band"]], S["ref_f"][S["band"]]
    edges = torch.linspace(0, 1, 11)
    idx = torch.bucketize(p, edges[1:-1])
    out["reliability_band"] = [{"bin": [_r(edges[i], 1), _r(edges[i + 1], 1)], "n": int((idx == i).sum()),
                                "mean_pred": _r(p[idx == i].mean(), 3) if int((idx == i).sum()) else None,
                                "mean_ref": _r(r[idx == i].mean(), 3) if int((idx == i).sum()) else None} for i in range(10)]
    out["lake_km2_per_date"] = [_r(float(fw[t][S["lake"]].sum()) * km2, 1) for t in range(T)]
    out["lake_km2_jrc_frac"] = _r(float(S["ref_f"][S["lake"]].sum()) * km2, 1)
    out["lake_error_pct_vs_1242"] = [_r(100 * (a - SEVAN_KM2) / SEVAN_KM2, 2) for a in out["lake_km2_per_date"]]
    out["false_water_km2_outside_lake_zone"] = [_r(float(fw[t][dom & ~S["lake"]].sum()) * km2, 1) for t in range(T)]
    return out


def sevan_protocol(d, methods: dict) -> dict:
    """The subpixel.py protocol exactly (so the numbers are head-to-head with docs/eda/subpixel.md): reference
    fraction = share of 30 m JRC pixels with occurrence >= 90 % (and, second, max extent); shoreline pixels =
    reference strictly between 0 and 1 within 5 px of the main lake; 8 calm dates pooled; lake area = sum of
    the fraction inside the detector's largest water body dilated by 2 px.  methods: name -> fw [T,H,W]."""
    from scripts.detect import ops
    from scripts.detect import subpixel as SP
    H, W = d["valid"].shape[-2:]
    refs = SP._ref("sevan", d["bounds"], H, W)
    vall = d["valid"].all(0)
    lab = ops.label_components((refs["occ90"] >= 0.5)[None])[0]
    cnt = torch.bincount(lab.flatten())
    cnt[0] = 0
    dom = ops.dilate((lab == cnt.argmax())[None], 11)[0] & vall
    shore = {k: (refs[k] > 0) & (refs[k] < 1) & dom for k in ("occ90", "ext")}
    T = d["valid"].shape[0]
    wi = windy_idx(d)
    calm = [t for t in range(T) if t not in wi]
    reg = SP.largest_body_region(d["water"], SP.KN["band_px"] + 1)
    km2 = d["km2_per_px"]
    out = {"n_shore_px": {k: int(m.sum()) for k, m in shore.items()}, "methods": {}}
    for nm, fw in methods.items():
        fw = fw.float() * d["valid"]
        o = {}
        for rk, m in shore.items():
            o[f"pooled_calm_{rk}"] = SP.pooled(fw, refs[rk], m, calm)
            o[f"windy_{rk}"] = SP.pooled(fw, refs[rk], m, wi) if wi else None
            o[f"calm_median_{rk}"] = SP.fmetrics(fw[calm].median(0).values, refs[rk], m)
        o["reliability_pooled_occ90"] = SP.reliability(torch.cat([fw[t][shore["occ90"]] for t in calm]),
                                                       torch.cat([refs["occ90"][shore["occ90"]]] * len(calm)),
                                                       torch.ones(int(shore["occ90"].sum()) * len(calm), dtype=torch.bool))
        a = [float((fw[t] * reg[t]).sum()) * km2 for t in range(T)]
        c = torch.tensor([a[t] for t in calm], dtype=torch.float64)
        o["lake_km2_per_date"] = [_r(x, 1) for x in a]
        o["lake_calm_mean"], o["lake_calm_sd"] = _r(c.mean(), 1), _r(c.std(), 1)
        o["lake_calm_err_vs_1242"] = _r(c.mean() - SEVAN_KM2, 1)
        o["lake_windy"] = [_r(a[t], 1) for t in wi]
        out["methods"][nm] = o
    return out


def tonle_protocol(d, methods: dict) -> dict:
    """Against the independent 2026 expected flood extent (hydro.py, no radar): summed fraction per date in the
    hydro domain, share of the fraction mass inside the expected set, recall of the expected set, IoU at 0.5."""
    p = ROOT / "data/processed/tonle-sap/hydro/products.pt"
    if not p.exists():
        return {}
    hy = torch.load(p, weights_only=False)
    if tuple(hy["domain"].shape) != tuple(d["valid"].shape[-2:]):
        return {"error": "grid mismatch"}
    dom = hy["domain"].bool() & d["valid"].all(0)
    km2 = d["km2_per_px"]
    E = hy["expected"].bool()
    out = {"expected_km2": [_r(x, 1) for x in hy["expected_km2"].tolist()],
           "expected_km2_lo": [_r(x, 1) for x in hy["expected_km2_lo"].tolist()],
           "expected_km2_hi": [_r(x, 1) for x in hy["expected_km2_hi"].tolist()],
           "expected_km2_in_eval_domain": [_r(float((E[t] & dom).sum()) * km2, 1) for t in range(E.shape[0])],
           "methods": {}}
    for nm, fw in methods.items():
        fw = fw.float() * d["valid"]
        o = {"km2": [], "mass_inside_expected": [], "recall_expected": [], "iou_ge05": []}
        for t in range(fw.shape[0]):
            f, e = fw[t] * dom, E[t] & dom
            tot = float(f.sum())
            o["km2"].append(_r(tot * km2, 1))
            o["mass_inside_expected"].append(_r(float(f[e].sum()) / max(tot, 1e-9), 3))
            o["recall_expected"].append(_r(float(f[e].mean()) if int(e.sum()) else float("nan"), 3))
            w = (f >= 0.5)
            o["iou_ge05"].append(_r(float((w & e).sum()) / max(float((w | e).sum()), 1), 3))
        out["methods"][nm] = o
    return out


def baselines(area, d, model, dev):
    """Fractions of the baselines on one area: (a) hard detector (+terrain), (b) analytic (subpixel.py if it
    runs, plus the one-line version), (c) classical per-pixel unmixing, (d) the rebuilt flooded-veg rule."""
    out = {"hard_detector": (d["water"].float(), None)}
    fvp = ROOT / f"data/processed/{area}/floodveg/products.pt"
    if fvp.exists():
        fv = torch.load(fvp, weights_only=False)
        if tuple(fv["flooded_veg"].shape) == tuple(d["valid"].shape):
            out["hard_detector"] = (d["water"].float(), fv["flooded_veg"].float())
            out["_fv_state"] = fv["fv_state"]
    b, valid = d["b"].float(), d["valid"]
    out["analytic_oneline"] = (analytic_fraction(b, valid, model.cfg, wok=d.get("water_ok")), None)
    try:
        from scripts.detect import subpixel
        r = subpixel.water_fraction(d["L"].float(), valid, d["water"], "preview", d["prior_water"], x_frac=b,
                                    km2_per_px=d["km2_per_px"])
        out["subpixel_py"] = (r["frac"].float(), None)
    except Exception as e:  # parallel work in progress; never depend on it
        print(f"   subpixel.py unavailable: {type(e).__name__}: {e}")
    t0 = time.time()
    fc = classical_unmix(b, valid, model, area, dev=dev)
    if d.get("water_ok") is not None:
        fc = apply_terrain(fc, d["water_ok"])
    out["classical_nnls"] = (fc[:, 0].float(), fc[:, 1].float())
    print(f"   classical unmixing {time.time() - t0:.1f}s")
    return out


def recon_quality(d, bh, f=None):
    b, v = d["b"].float(), d["valid"][:, None].float()
    res = (bh.float() - b) * v
    n = v.sum() * 2 / 2
    mse = float((res ** 2).sum() / (2 * v.sum()))
    mu = (b * v).sum((0, 2, 3), keepdim=True) / v.sum((0, 2, 3), keepdim=True)
    var_scene = float((((b - mu) * v) ** 2).sum() / (2 * v.sum()))
    mt = (b * v).sum(0, keepdim=True) / v.sum(0, keepdim=True).clamp_min(1)
    var_time = float((((b - mt) * v) ** 2).sum() / (2 * v.sum()))
    # temporal variance explained: compare the residual with what a per-pixel constant would leave
    return {"rmse": _r(math.sqrt(mse), 4), "explained_var_total": _r(1 - mse / var_scene, 4),
            "explained_var_temporal": _r(1 - mse / max(var_time, 1e-12), 4),
            "rmse_hh": _r(float(((res[:, 0] ** 2).sum() / v[:, 0].sum()).sqrt()), 4),
            "rmse_hv": _r(float(((res[:, 1] ** 2).sum() / v[:, 0].sum()).sqrt()), 4)}, (res ** 2).mean(1).sum(0).div(v[:, 0].sum(0).clamp_min(1)).sqrt()


def predict(area, tag="full", dev=None, save=True, model=None):
    dev = dev or dev_default()
    if model is None:
        model, _ = load_model(tag, dev)
    d = load_inputs(area)
    ad = AreaData(d, model.cfg["patch"])
    fitted = False
    if area.replace("-", "_") not in model.scenes:
        fit_scene(model, ad, dev=dev)
        fitted = True
    t0 = time.time()
    f_raw, bl, bh = infer(model, ad, dev=dev)
    secs = time.time() - t0
    f = apply_terrain(f_raw, d["water_ok"]) if d.get("water_ok") is not None else f_raw
    rq, rmap = recon_quality(d, bh)
    km2 = d["km2_per_px"]
    dom = d["valid"].all(0)
    sc = model.scene(area)
    P = {"f_water": f[:, 0].half(), "f_fv": f[:, 1].half(), "f_land": f[:, 2].half(),
         "f_water_no_terrain": f_raw[:, 0].half(), "land_endmember": bl[:2].half(),
         "water_brightness_db": (10 * bl[2:4]).half(),
         "water_roughness": (10 * sc.log10_r()).detach().cpu(), "gain_db": sc.gain_db().detach().cpu(),
         "global_params": model.phys.summary(), "water_km2_frac": f[:, 0].float()[:, dom].sum(1) * km2,
         "fv_km2_frac": f[:, 1].float()[:, dom].sum(1) * km2, "recon_rmse": rq["rmse"], "recon": rq,
         "recon_rmse_map": rmap.half(), "prior_driven": prior_driven(ad.stats, d["valid"], model.cfg),
         "dates": d["dates"], "bounds": d["bounds"], "km2_per_px": km2, "valid": d["valid"],
         "model": tag, "scene_fitted_at_test_time": fitted, "terrain_prior": d.get("water_ok") is not None,
         "units": {"water_roughness": "dB [T, (HH, HV)]", "land_endmember": "preview brightness (HH, HV)"}}
    if save:
        p = ROOT / f"data/processed/{area}/unmix"
        p.mkdir(parents=True, exist_ok=True)
        torch.save(P, p / "products.pt")
        print(f"  {area}: products saved ({secs:.1f}s inference)")
    P["_bh"] = bh
    P["_f_raw"] = f_raw
    P["_infer_seconds"] = secs
    return P, d, model


# =============================================================================================
# studies
# =============================================================================================
def _results():
    p = DOCS / "results.json"
    return json.loads(p.read_text()) if p.exists() else {}


def _save_results(key, val):
    DOCS.mkdir(parents=True, exist_ok=True)
    R = _results()
    R[key] = val
    (DOCS / "results.json").write_text(json.dumps(R, indent=1))


def synth_eval(s, f, fc=None):
    """RMSE per type and per identifiability case of a fraction estimate f [T,3,H,W] (or water-only [T,H,W])."""
    truth = s["f"]
    M = s["masks"]
    T = truth.shape[0]
    out = {}
    fw = f[:, 0] if f.dim() == 4 else f
    out["water_rmse"] = _r(rmse(fw, truth[:, 0]), 4)
    if f.dim() == 4:
        out["fv_rmse"] = _r(rmse(f[:, 1], truth[:, 1]), 4)
        out["land_rmse"] = _r(rmse(f[:, 2], truth[:, 2]), 4)
    edge = (truth[:, 0] > 0.02) & (truth[:, 0] < 0.98)
    out["water_rmse_partial_pixels"] = _r(rmse(fw, truth[:, 0], edge), 4)
    for k in ("dark_land", "mixed_constant", "marsh", "ring"):
        m = M[k][None].expand(T, -1, -1)
        out[f"water_mean_{k}"] = _r(float(fw[m].float().mean()), 3)
        out[f"water_true_{k}"] = _r(float(truth[:, 0][m].mean()), 3)
        out[f"water_rmse_{k}"] = _r(rmse(fw, truth[:, 0], m), 4)
    if f.dim() == 4:
        m = M["ring"][None].expand(T, -1, -1)
        out["fv_rmse_ring"] = _r(rmse(f[:, 1], truth[:, 1], m), 4)
    wd = s["params"]["wind_date"]
    if wd is not None:
        out["water_rmse_wind_date"] = _r(rmse(fw[wd], truth[wd, 0]), 4)
        lake = truth[wd, 0] > 0.99
        out["water_mean_lake_wind_date"] = _r(float(fw[wd][lake].float().mean()), 3)
    return out


def run_synthetic(minutes=5.0, dev=None, variants=("full",), short_minutes=2.0):
    dev = dev or dev_default()
    scenes = [synth_scene(seed=0), synth_scene(seed=1)]
    held = synth_scene(seed=2, wind_date=7)
    R = {"scene": {"H": 128, "W": 128, "T": 10, "looks": 12, "db": 30, "true_g_db": scenes[0]["params"]["g_db"],
                   "true_water_roughness_db_seed0": scenes[0]["params"]["water_roughness_db"],
                   "true_gain_db_seed0": scenes[0]["params"]["gain_db"]}, "methods": {}, "variants": {}}
    for var in variants:
        cfg = cfg_of(var, minutes=minutes if var == "full" else short_minutes, steps=3000)
        datas = [AreaData(s, cfg["patch"]) for s in scenes]
        model, info = train_model(datas, cfg, dev=dev, log_every=300)
        f0, bl0, _ = infer(model, datas[0], dev=dev)
        dh = AreaData(held, cfg["patch"])
        fit_scene(model, dh, dev=dev)
        fh, _, _ = infer(model, dh, dev=dev)
        R["variants"][var] = {"in_sample": synth_eval(scenes[0], f0.float()), "held_out": synth_eval(held, fh.float()),
                              "seconds": info["seconds"], "steps": info["steps_done"]}
        if var == "full":
            R["n_params"] = n_params(model)
            R["learned"] = {"global": model.phys.summary(), "scene0": model.scene(scenes[0]["area"]).summary(),
                            "held_out_scene": model.scene(held["area"]).summary()}
            R["land_endmember_rmse_in_sample"] = _r(rmse(bl0[:2], scenes[0]["E_l"]), 4)
            pdm = prior_driven(datas[0].stats, datas[0].valid, cfg)
            R["prior_driven_share"] = {k: _r(float(pdm[m].float().mean()), 3) for k, m in scenes[0]["masks"].items()}
            fc = classical_unmix(scenes[0]["b"], scenes[0]["valid"], model, scenes[0]["area"], dev=dev)
            hard = hard_threshold_synth(scenes[0]["b"], scenes[0]["valid"])
            an = analytic_fraction(scenes[0]["b"], scenes[0]["valid"], cfg)
            R["methods"] = {"physics_ae": R["variants"]["full"]["in_sample"], "classical_nnls": synth_eval(scenes[0], fc.float()),
                            "analytic_oneline": synth_eval(scenes[0], an), "hard_threshold": synth_eval(scenes[0], hard)}
            torch.save({"scene": scenes[0], "f_ae": f0, "f_cl": fc, "f_an": an, "f_hard": hard, "bl": bl0},
                       MODELS / "unmix_synthetic_eval.pt")
        print(var, json.dumps(R["variants"][var]["in_sample"]))
        del model
        empty_cache()
    old = _results().get("synthetic", {})
    if "full" not in variants and old:
        old.setdefault("variants", {}).update(R["variants"])
        R = old
    _save_results("synthetic", R)
    return R


def train_cli(area, variant, minutes, tag=None, dev=None):
    dev = dev or dev_default()
    areas = CORE_AREAS + [a for a in WORLD_AREAS if input_path(a).exists()] if area == "all" else (
        CORE_AREAS if area == "core" else [area])
    cfg = cfg_of(variant, minutes=minutes, steps=max(200, int(minutes * 60 / 0.12)))
    datas = [AreaData(load_inputs(a), cfg["patch"]) for a in areas]
    print(f"  training {variant} on {areas}")
    model, info = train_model(datas, cfg, dev=dev)
    info.update(areas=areas, n_params=n_params(model), device=str(dev))
    tag = tag or (variant if area in ("all",) else f"{variant}_{area}")
    save_model(model, variant, info, tag)
    print(f"  saved unmix_{tag}.pt: {info['seconds']} s, {info['steps_done']} steps, {info['n_params']} params")
    return model, info


def evaluate_area(area, tag="full", dev=None, with_baselines=True):
    dev = dev or dev_default()
    P, d, model = predict(area, tag, dev, save=(tag == "full"))
    ref = jrc_ref(area, d) if area in CORE_AREAS else None
    out = {"recon": P["recon"], "water_roughness_db": P["water_roughness"].tolist(), "gain_db": P["gain_db"].tolist(),
           "global_params": P["global_params"], "infer_seconds": _r(P["_infer_seconds"], 1),
           "dates": d["dates"], "km2_per_px": d["km2_per_px"], "terrain_prior": P["terrain_prior"]}
    pd = P["prior_driven"] & d["valid"].all(0)
    out["prior_driven_km2"] = _r(float(pd.sum()) * d["km2_per_px"], 1)
    out["prior_driven_water_km2_mean"] = _r(float(P["f_water"].float()[:, pd].sum(1).mean()) * d["km2_per_px"], 1)
    M = {"physics_ae": method_metrics(area, d, ref, P["f_water"].float(), P["f_fv"].float())}
    M["physics_ae_no_terrain"] = method_metrics(area, d, ref, P["f_water_no_terrain"].float(), P["f_fv"].float())
    keep = {"ae": P}
    if with_baselines:
        B = baselines(area, d, model, dev)
        fvstate = B.pop("_fv_state", None)
        for nm, (fw, ffv) in B.items():
            M[nm] = method_metrics(area, d, ref, fw, ffv)
        keep["B"] = B
        if fvstate is not None:
            out["floodveg_state_analysis"] = state_analysis(d, ref, P, fvstate)
            keep["fv_state"] = fvstate
    fws = {"physics_ae": P["f_water"].float(), "physics_ae_no_terrain": P["f_water_no_terrain"].float()}
    for nm, (fw, _) in keep.get("B", {}).items():
        fws[nm] = fw
    if area == "sevan":
        sp = ROOT / "data/processed/sevan/subpixel/products.pt"
        if sp.exists():
            S = torch.load(sp, weights_only=False)
            if tuple(S["frac"].shape) == tuple(d["valid"].shape):
                fws["subpixel_display"] = S["frac"].float()
                fws["subpixel_power30"] = S["frac_power30"].float()
        out["sevan_protocol"] = sevan_protocol(d, fws)
        keep["sevan_fws"] = fws
    if area == "tonle-sap":
        out["tonle_protocol"] = tonle_protocol(d, fws)
    out["methods"] = M
    return out, keep, d, ref, model


def state_analysis(d, ref, P, st):
    """What the physics autoencoder says on the rebuilt rule's state 1 (double bounce) and state 2 (both fall)."""
    km2 = d["km2_per_px"]
    out = {}
    fw, fv = P["f_water"].float(), P["f_fv"].float()
    occ = ref["occ_mean"][None].expand_as(fw) if ref is not None else None
    for s in (1, 2):
        m = (st == s) & d["valid"]
        n = int(m.sum())
        if not n:
            continue
        o = {"pixel_dates": n, "rule_km2_peak": _r(float(m.sum((1, 2)).max()) * km2, 1),
             "ae_mean_f_water": _r(fw[m].mean(), 3), "ae_mean_f_fv": _r(fv[m].mean(), 3),
             "ae_share_mostly_water": _r((fw[m] >= 0.5).float().mean(), 3),
             "ae_share_mostly_fv": _r((fv[m] >= 0.5).float().mean(), 3)}
        if occ is not None:
            o["jrc_mean_occurrence_pct"] = _r(occ[m].mean(), 1)
            wm, vm = m & (fw >= 0.5), m & (fv >= 0.5)
            o["jrc_occ_where_ae_says_water"] = _r(occ[wm].mean(), 1) if int(wm.sum()) else None
            o["jrc_occ_where_ae_says_fv"] = _r(occ[vm].mean(), 1) if int(vm.sum()) else None
        out[f"state{s}"] = o
    return out


def evaluate_all(tag="full", dev=None, figures=True):
    dev = dev or dev_default()
    R = {}
    store = {}
    for area in CORE_AREAS:
        t0 = time.time()
        out, keep, d, ref, model = evaluate_area(area, tag, dev)
        R[area] = out
        print(f"  {area} evaluated in {time.time() - t0:.0f}s")
        if figures:
            store[area] = _fig_payload(area, keep, d, ref)
        del keep, model
        empty_cache()
    # world areas: products + area curves only
    W = {}
    for area in WORLD_AREAS:
        if not input_path(area).exists():
            continue
        try:
            P, d, model = predict(area, tag, dev, save=(tag == "full"))
            W[area] = {"water_km2_frac": [_r(v, 1) for v in P["water_km2_frac"].tolist()],
                       "fv_km2_frac": [_r(v, 1) for v in P["fv_km2_frac"].tolist()],
                       "hard_water_km2": [_r(float(d["water"][t][d["valid"].all(0)].sum()) * d["km2_per_px"], 1) for t in range(len(d["dates"]))],
                       "dates": d["dates"], "recon": P["recon"],
                       "water_roughness_db": P["water_roughness"].tolist()}
            del P, model
            empty_cache()
        except Exception as e:
            W[area] = {"error": f"{type(e).__name__}: {e}"}
    R["world"] = W
    _save_results("real" if tag == "full" else f"real_{tag}", R)
    if figures:
        torch.save(store, MODELS / "unmix_fig_payload.pt")
        make_figures()
    return R


def _fig_payload(area, keep, d, ref):
    P = keep["ae"]
    out = {"dates": d["dates"], "bounds": d["bounds"], "km2": d["km2_per_px"], "valid": d["valid"].all(0)}
    if area == "tonle-sap":
        out["f"] = torch.stack([P["f_water"], P["f_fv"], P["f_land"]], 1)[[0, -1]]
        out["b"] = d["b"][[0, -1]]
        out["fv_state"] = keep.get("fv_state", None)
        out["fv_state"] = None if out["fv_state"] is None else out["fv_state"][[0, -1]]
    if area == "sevan":
        from scripts.detect import ops
        from scripts.detect import subpixel as SP
        H, W = d["valid"].shape[-2:]
        refs = SP._ref("sevan", d["bounds"], H, W)
        vall = d["valid"].all(0)
        lab = ops.label_components((refs["occ90"] >= 0.5)[None])[0]
        cnt = torch.bincount(lab.flatten())
        cnt[0] = 0
        dom = ops.dilate((lab == cnt.argmax())[None], 11)[0] & vall
        shore = (refs["occ90"] > 0) & (refs["occ90"] < 1) & dom
        wi = windy_idx(d)
        calm = [t for t in range(len(d["dates"])) if t not in wi]
        out["ref_f"], out["band"] = refs["occ90"], shore
        fws = keep.get("sevan_fws", {"physics_ae": P["f_water"].float()})
        out["pred"] = {nm: fw.float()[calm].mean(0) for nm, fw in fws.items()}
        out["pooled"] = {nm: torch.cat([fw.float()[t][shore] for t in calm]) for nm, fw in fws.items()}
        out["pooled_ref"] = torch.cat([refs["occ90"][shore]] * len(calm))
        out["b_mean"] = d["b"].float().mean(0)
    out["recon_rmse_map"] = P["recon_rmse_map"]
    return out


# =============================================================================================
# figures
# =============================================================================================
def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def _savefig(fig, name):
    DOCS.mkdir(parents=True, exist_ok=True)
    p = DOCS / name
    fig.savefig(p, dpi=110, bbox_inches="tight")
    _plt().close(fig)
    kb = p.stat().st_size / 1024
    if kb > 340:  # keep figures small
        from PIL import Image
        im = Image.open(p).convert("RGB")
        if kb > 700:
            im = im.resize((im.width * 3 // 4, im.height * 3 // 4))
        im.quantize(colors=96).save(p, optimize=True)
    print(f"   {name}: {p.stat().st_size / 1024:.0f} KB")


COL = {"subpixel_display": "#16a085", "subpixel_power30": "#7dcea0", "physics_ae": "#c0392b", "physics_ae_no_terrain": "#e6a09a", "hard_detector": "#34495e", "analytic_oneline": "#2e86c1",
       "subpixel_py": "#16a085", "classical_nnls": "#8e44ad"}
LABEL = {"subpixel_display": "subpixel.py, display mixing", "subpixel_power30": "subpixel.py, power 30 dB",
         "physics_ae": "physics autoencoder", "physics_ae_no_terrain": "physics AE, no terrain prior",
         "hard_detector": "hard detector (ops)", "analytic_oneline": "analytic one-line fraction",
         "subpixel_py": "analytic two-end-member (subpixel.py)", "classical_nnls": "classical per-pixel unmixing"}


def fig_diagram():
    plt = _plt()
    from matplotlib.patches import FancyBboxPatch
    fig, ax = plt.subplots(figsize=(11, 3.6))
    ax.axis("off")
    boxes = [(0.01, "radar stack\n10 dates x (HH, HV)\n+ season statistics", "#ecf0f1"),
             (0.20, "ENCODER (neural)\nshared U-Net per date\n+ temporal blocks\n(1-D conv over dates\n+ season mean)", "#fadbd8"),
             (0.42, "BOTTLENECK = physics\nf_water, f_flooded-veg, f_land\nper date (softmax)\n+ land end-member E_l\n(per pixel, constant)", "#fdebd0"),
             (0.64, "DECODER (fixed physics)\nI = g_t [f_w r_t E_w + f_v G E_l + f_l E_l]\nmixing in POWER,\nthen b = 1 + (10/D) log10 I", "#d6eaf8"),
             (0.86, "reconstructed\nbrightness\nHuber loss vs\nobserved", "#ecf0f1")]
    for x, t, c in boxes:
        ax.add_patch(FancyBboxPatch((x, 0.25), 0.15, 0.55, boxstyle="round,pad=0.01", fc=c, ec="#555"))
        ax.text(x + 0.075, 0.525, t, ha="center", va="center", fontsize=8.2)
    for x in (0.165, 0.385, 0.605, 0.825):
        ax.annotate("", xy=(x + 0.03, 0.525), xytext=(x, 0.525), arrowprops=dict(arrowstyle="->", lw=1.5))
    ax.text(0.715, 0.08, "learned physical scalars: E_w (water), G_HH +1..+6 dB, G_HV 0..-6 dB (double bounce),\n"
            "per scene and date: water roughness r_t >= 1 (wind), gain g_t (mean 0 dB)", ha="center", fontsize=8)
    ax.text(0.275, 0.08, "regularisers (no labels): sparsity, temporal TV, spatial TV weighted\nby radar similarity, "
            "land ~ bright-season level, land floor, veg needs canopy", ha="center", fontsize=8)
    ax.set_xlim(0, 1.02)
    ax.set_ylim(0, 0.9)
    ax.set_title("Physics autoencoder: a neural encoder, a radar-scattering decoder", fontsize=11)
    _savefig(fig, "01_model_diagram.png")


def _rgb(f):
    """fractions [3,H,W] -> RGB: water blue, flooded vegetation green, land tan."""
    cols = torch.tensor([[0.10, 0.35, 0.85], [0.10, 0.75, 0.25], [0.86, 0.80, 0.66]])
    return torch.einsum("khw,kc->hwc", f.float().clamp(0, 1), cols).clamp(0, 1).numpy()


def make_figures():
    plt = _plt()
    R = _results()
    fig_diagram()
    pay = torch.load(MODELS / "unmix_fig_payload.pt", weights_only=False) if (MODELS / "unmix_fig_payload.pt").exists() else {}
    real = R.get("real", {})
    # 02 Tonle Sap fraction composites
    if "tonle-sap" in pay:
        p = pay["tonle-sap"]
        w, s, e, n = p["bounds"]
        fig, axs = plt.subplots(1, 3, figsize=(15, 5.2))
        for j, (ti, nm) in enumerate([(0, p["dates"][0]), (1, p["dates"][-1])]):
            axs[j].imshow(_rgb(p["f"][ti] * p["valid"]), extent=(w, e, s, n))
            axs[j].set_title(f"Tonle Sap {nm}: water (blue) / flooded veg (green) / land (tan)", fontsize=9)
        b = p["b"][1].float()
        axs[2].imshow(torch.stack([b[0], b[1], b[0]], -1).clamp(0, 1).numpy(), extent=(w, e, s, n))
        axs[2].set_title(f"radar preview {p['dates'][-1]} (HH, HV, HH)", fontsize=9)
        _savefig(fig, "02_tonle_sap_fractions.png")
    # 03 Sevan shoreline (subpixel.py protocol: occurrence >= 90 % reference, shoreline pixels, 8 calm dates)
    spm = real.get("sevan", {}).get("sevan_protocol", {}).get("methods", {})
    if "sevan" in pay and "pooled" in pay["sevan"]:
        p = pay["sevan"]
        band = p["band"]
        meths = [m for m in ["physics_ae", "subpixel_display", "subpixel_power30", "classical_nnls", "hard_detector"] if m in p["pred"]]
        fig = plt.figure(figsize=(17, 7.6))
        sub = (slice(40, 170), slice(0, 160))  # north-west: Small Sevan and its bay
        ax = fig.add_subplot(2, 5, 1)
        ax.imshow(p["ref_f"][sub].numpy(), vmin=0, vmax=1, cmap="Blues")
        ax.set_title("JRC 30 m: share with occurrence >= 90 %", fontsize=8.5)
        for k, m in enumerate(meths[:4]):
            ax = fig.add_subplot(2, 5, 2 + k)
            ax.imshow(p["pred"][m][sub].numpy(), vmin=0, vmax=1, cmap="Blues")
            ax.set_title(LABEL.get(m, m) + " (calm mean)", fontsize=8.5)
        for k, m in enumerate(meths):
            ax = fig.add_subplot(2, 5, 6 + k)
            r, q = p["pooled_ref"].numpy(), p["pooled"][m].numpy()
            ax.hist2d(r, q, bins=20, range=[[0, 1], [0, 1]], cmap="magma_r", cmin=1)
            ax.plot([0, 1], [0, 1], "c--", lw=1)
            met = spm.get(m, {}).get("pooled_calm_occ90", {})
            ax.set_title(f"{LABEL.get(m, m)}\nRMSE {met.get('rmse')}, bias {met.get('bias')}, r {met.get('r')}", fontsize=8)
            ax.set_xlabel("reference fraction")
            if k == 0:
                ax.set_ylabel("predicted (8 calm dates)")
        for a_ in fig.axes[:5]:
            a_.set_xticks([]); a_.set_yticks([])
        fig.suptitle(f"Lake Sevan: water fraction on the {int(band.sum())} shoreline pixels vs the 30 m reference "
                     "(same protocol as subpixel.md)", fontsize=11)
        _savefig(fig, "03_sevan_shoreline.png")
    # 04 roughness per date, with the weather
    if real:
        fig, axs = plt.subplots(1, 2, figsize=(13, 4.0))
        notes = {"2026-08-31": "29 mm rain\nthe night before", "2026-09-12": "2-4 m/s\nbreeze"}
        for ax, area in zip(axs, ["sevan", "tonle-sap"]):
            r = real.get(area, {})
            if not r:
                continue
            rd = torch.tensor(r["water_roughness_db"])
            x = list(range(len(r["dates"])))
            ax.plot(x, rd[:, 0], "o-", label="HH")
            ax.plot(x, rd[:, 1], "s-", label="HV")
            ax.set_xticks(x)
            ax.set_xticklabels([t[5:] for t in r["dates"]], rotation=45, fontsize=8)
            ax.set_ylabel("learned water roughness r_t (dB)")
            ax.set_title(f"{area}: per-date water roughness", fontsize=9)
            if area == "sevan":
                top = float(rd.max())
                for t in windy_idx({"area": "sevan", "dates": r["dates"]}):
                    ax.axvspan(t - 0.4, t + 0.4, color="orange", alpha=0.2)
                    ax.text(t, top * 0.85, notes.get(r["dates"][t], ""), ha="center", fontsize=7)
                if "2026-10-06" in r["dates"]:
                    t = r["dates"].index("2026-10-06")
                    ax.text(t, top * 0.55, "HV faulty\n(imputed)", ha="center", fontsize=7, color="grey")
            ax.legend(fontsize=8)
        _savefig(fig, "04_water_roughness.png")
    # 05 area curves
    if real:
        fig, axs = plt.subplots(1, 3, figsize=(17, 4.4))
        r = real.get("tonle-sap", {})
        tp = r.get("tonle_protocol", {}) if r else {}
        if r and tp.get("methods"):
            x = list(range(len(r["dates"])))
            ax = axs[0]
            ax.fill_between(x, tp["expected_km2_lo"], tp["expected_km2_hi"], color="grey", alpha=0.25,
                            label="expected from lake level (hydro.md), range")
            ax.plot(x, tp["expected_km2"], "k--", lw=1.2, label="expected, central")
            for m in ("physics_ae", "hard_detector", "subpixel_py"):
                if m in tp["methods"]:
                    ax.plot(x, tp["methods"][m]["km2"], "o-", color=COL[m], label=LABEL[m], ms=3)
            ax.set_title("Tonle Sap open water (km²; fractions summed)", fontsize=9)
            ax.set_xticks(x); ax.set_xticklabels([t[5:] for t in r["dates"]], rotation=45, fontsize=7)
            ax.legend(fontsize=6.5)
            ax = axs[1]
            for m in ("physics_ae", "classical_nnls", "hard_detector"):
                mm = r["methods"].get(m, {})
                if mm.get("fv_km2_frac"):
                    ax.plot(x, mm["fv_km2_frac"], "o-", color=COL[m], label=LABEL[m] + (" (rebuilt rule)" if m == "hard_detector" else ""), ms=3)
            ax.axhspan(1600, 2500, color="green", alpha=0.1, label="rebuilt rule: peak range")
            ax.set_title("Tonle Sap flooded vegetation (km²)", fontsize=9)
            ax.set_xticks(x); ax.set_xticklabels([t[5:] for t in r["dates"]], rotation=45, fontsize=7)
            ax.legend(fontsize=6.5)
        r = real.get("sevan", {})
        if spm:
            x = list(range(len(r["dates"])))
            ax = axs[2]
            for m in ("physics_ae", "hard_detector", "subpixel_display", "subpixel_power30", "classical_nnls"):
                if m in spm:
                    ax.plot(x, spm[m]["lake_km2_per_date"], "o-", color=COL.get(m, "#999"), label=LABEL.get(m, m), ms=3)
            ax.axhline(SEVAN_KM2, color="k", ls="--", lw=1, label="published 1,242 km²")
            for t in windy_idx({"area": "sevan", "dates": r["dates"]}):
                ax.axvspan(t - 0.4, t + 0.4, color="orange", alpha=0.2)
            ax.set_title("Lake Sevan area (fractions summed over the lake + 2 px, km²)", fontsize=9)
            ax.set_xticks(x); ax.set_xticklabels([t[5:] for t in r["dates"]], rotation=45, fontsize=7)
            ax.legend(fontsize=6.5)
        _savefig(fig, "05_area_curves.png")
    # 06 synthetic
    sp = MODELS / "unmix_synthetic_eval.pt"
    if sp.exists():
        S = torch.load(sp, weights_only=False)
        sc = S["scene"]
        t = 8
        fig, axs = plt.subplots(2, 4, figsize=(15, 7.5))
        axs[0, 0].imshow(_rgb(sc["f"][t]))
        axs[0, 0].set_title(f"truth, date {t}", fontsize=9)
        axs[0, 1].imshow(_rgb(S["f_ae"][t]))
        axs[0, 1].set_title("physics autoencoder", fontsize=9)
        axs[0, 2].imshow(_rgb(S["f_cl"][t]))
        axs[0, 2].set_title("classical per-pixel unmixing", fontsize=9)
        bb = sc["b"][t]
        axs[0, 3].imshow(torch.stack([bb[0], bb[1], bb[0]], -1).numpy())
        axs[0, 3].set_title("simulated radar (HH, HV, HH)", fontsize=9)
        axs[1, 0].imshow(sc["f"][t, 0], vmin=0, vmax=1, cmap="Blues")
        axs[1, 0].set_title("true water fraction", fontsize=9)
        axs[1, 1].imshow(S["f_ae"][t, 0].float(), vmin=0, vmax=1, cmap="Blues")
        axs[1, 1].set_title("autoencoder water fraction", fontsize=9)
        axs[1, 2].imshow(S["f_hard"][t], vmin=0, vmax=1, cmap="Blues")
        axs[1, 2].set_title("hard threshold (0/1)", fontsize=9)
        syn = R.get("synthetic", {}).get("methods", {})
        ax = axs[1, 3]
        keys = ["water_rmse", "water_rmse_partial_pixels", "water_rmse_marsh", "water_rmse_mixed_constant", "water_rmse_wind_date"]
        for k, (m, mm) in enumerate(syn.items()):
            ax.bar([i + 0.2 * k for i in range(len(keys))], [mm.get(q) or 0 for q in keys], 0.2, label=m)
        ax.set_xticks([i + 0.3 for i in range(len(keys))])
        ax.set_xticklabels(["all", "partial px", "marsh", "constant\n70 % water", "wind date"], fontsize=7)
        ax.set_ylabel("water-fraction RMSE")
        ax.legend(fontsize=7)
        for a in axs.flat[:-1]:
            a.set_xticks([]); a.set_yticks([])
        fig.suptitle("Synthetic truth: growing lake, flooding forest ring, partly flooded marsh (top right), dark land and a "
                     "constant 70 %-water patch (bottom left), wind on one date", fontsize=10)
        _savefig(fig, "06_synthetic_truth.png")
    # 07 residual maps
    if pay:
        fig, axs = plt.subplots(1, 2, figsize=(13, 5.5))
        for ax, area in zip(axs, ["tonle-sap", "sevan"]):
            if area not in pay:
                continue
            p = pay[area]
            w, s, e, n = p["bounds"]
            im = ax.imshow(p["recon_rmse_map"].float().numpy(), vmin=0, vmax=0.12, cmap="inferno", extent=(w, e, s, n))
            rq = real.get(area, {}).get("recon", {})
            ax.set_title(f"{area}: per-pixel reconstruction RMSE (brightness)\nscene RMSE {rq.get('rmse')}, "
                         f"explained variance {rq.get('explained_var_total')} (temporal {rq.get('explained_var_temporal')})", fontsize=9)
            plt.colorbar(im, ax=ax, shrink=0.7)
        _savefig(fig, "07_residuals.png")
    # 08 ablation
    ab = R.get("ablation", {})
    if ab:
        names = list(ab.keys())
        keys = [(lambda v: (v.get("sevan_occ90") or {}).get("rmse"), "Sevan shoreline RMSE (lower better)"),
                (lambda v: v.get("sevan_lake_calm_km2"), "Sevan lake, calm mean km² (1,242)"),
                (lambda v: (v.get("tonle_km2") or [None])[-1], "Tonle Sap water 17 Oct km² (expected 5,421)"),
                (lambda v: v.get("tonle_false_km2_test_never"), "Tonle Sap false water on never-water anchors km²"),
                (lambda v: v.get("ararat_false_km2_mean"), "Ararat water km² (0)")]
        fig, axs = plt.subplots(1, len(keys), figsize=(18, 3.6))
        for ax, (fn, t) in zip(axs, keys):
            vals = [fn(ab[nm]) for nm in names]
            ax.barh(names, [v or 0 for v in vals], color=["#c0392b" if nm == "full" else "#95a5a6" for nm in names])
            for i, v in enumerate(vals):
                ax.text(v or 0, i, f" {v}", va="center", fontsize=7)
            if "1,242" in t:
                ax.axvline(SEVAN_KM2, color="k", ls="--", lw=1)
                ax.set_xlim(1000, 1300)
            if "5,421" in t:
                ax.axvline(5421, color="k", ls="--", lw=1)
            ax.set_title(t, fontsize=8.5)
            ax.tick_params(labelsize=7)
        fig.suptitle("Ablations: each variant trained 2.5 min on Tonle Sap + Sevan + Ararat (same budget, same seed)", fontsize=10)
        _savefig(fig, "08_ablation.png")


# =============================================================================================
# ablation
# =============================================================================================
def ablation_metrics(R_area):
    out = {}
    s = R_area.get("sevan", {}).get("methods", {}).get("physics_ae", {})
    t = R_area.get("tonle-sap", {}).get("methods", {}).get("physics_ae", {})
    a = R_area.get("ararat", {}).get("methods", {}).get("physics_ae", {})
    if s:
        out["sevan_band_rmse"] = s["shore_band"]["rmse"]
        out["sevan_band_bias"] = s["shore_band"]["bias"]
        out["sevan_lake_km2"] = s["lake_km2_per_date"]
        out["sevan_lake_err_pct_abs_mean"] = _r(np.mean(np.abs(s["lake_error_pct_vs_1242"])), 2)
        out["sevan_false_outside_km2_mean"] = _r(np.mean(s["false_water_km2_outside_lake_zone"]), 1)
    if t:
        out["tonle_water_km2_first_last"] = [t["water_km2_frac"][0], t["water_km2_frac"][-1]]
        out["tonle_fv_km2_peak"] = max(t.get("fv_km2_frac", [0]))
        out["tonle_spearman"] = t.get("floodplain_spearman_occurrence_vs_mean_fwater")
        out["tonle_test_bal_acc"] = t["test_anchors"]["balanced_acc"]
    if a:
        out["ararat_false_km2_mean"] = _r(np.mean(a["water_km2_frac"]), 1)
        out["ararat_fv_km2_mean"] = _r(np.mean(a.get("fv_km2_frac", [0])), 1)
    sp = R_area.get("sevan", {}).get("sevan_protocol", {}).get("methods", {}).get("physics_ae")
    if sp:
        out["sevan_occ90"] = sp["pooled_calm_occ90"]
        out["sevan_lake_calm_km2"] = sp["lake_calm_mean"]
        out["sevan_lake_windy_km2"] = sp["lake_windy"]
    tp = R_area.get("tonle-sap", {}).get("tonle_protocol", {}).get("methods", {}).get("physics_ae")
    if tp:
        out["tonle_km2"] = tp["km2"]
        out["tonle_mass_inside_expected_mean"] = _r(np.mean(tp["mass_inside_expected"]), 3)
    if t:
        out["tonle_false_km2_test_never"] = t["test_anchors"]["false_km2_per_date_frac"]
    if R_area.get("sevan"):
        out["sevan_roughness_db"] = [[_r(x, 2) for x in r] for r in R_area["sevan"]["water_roughness_db"]]
    out["recon_rmse"] = {k: R_area[k]["recon"]["rmse"] for k in CORE_AREAS if k in R_area}
    return out


def run_ablation(variants, minutes=2.5, dev=None):
    dev = dev or dev_default()
    R = _results().get("ablation", {})
    syn = _results().get("synthetic", {}).get("variants", {})
    for var in variants:
        t0 = time.time()
        model, info = train_cli("core", var, minutes, tag=f"abl_{var}", dev=dev)
        del model
        empty_cache()
        RA = {}
        for area in CORE_AREAS:
            out, keep, d, ref, m = evaluate_area(area, f"abl_{var}", dev, with_baselines=False)
            RA[area] = out
            del keep, m
            empty_cache()
        R[var] = ablation_metrics(RA)
        R[var]["train_seconds"] = info["seconds"]
        if var in syn:
            R[var]["synthetic_water_rmse"] = syn[var]["in_sample"]["water_rmse"]
            R[var]["synthetic_fv_rmse"] = syn[var]["in_sample"].get("fv_rmse")
        print(var, json.dumps(R[var]), f"{time.time() - t0:.0f}s", flush=True)
        _save_results("ablation", R)
    return R


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["prepare", "synthetic", "train", "predict", "evaluate", "ablate", "figures"])
    ap.add_argument("--area", default="all")
    ap.add_argument("--variant", default="full")
    ap.add_argument("--variants", default="")
    ap.add_argument("--minutes", type=float, default=8.0)
    ap.add_argument("--tag", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "prepare":
        for ar in (CORE_AREAS + WORLD_AREAS if a.area == "all" else [a.area]):
            try:
                prepare(ar)
            except Exception as e:
                print(f"  {ar}: not loaded ({type(e).__name__}: {e})")
            empty_cache()
    elif a.cmd == "synthetic":
        vs = a.variants.split(",") if a.variants else ["full"]
        run_synthetic(minutes=a.minutes, variants=vs, short_minutes=a.minutes)
    elif a.cmd == "train":
        train_cli(a.area, a.variant, a.minutes, a.tag)
    elif a.cmd == "predict":
        for ar in (CORE_AREAS + [w for w in WORLD_AREAS if input_path(w).exists()] if a.area == "all" else [a.area]):
            predict(ar, a.tag or a.variant)
            empty_cache()
    elif a.cmd == "evaluate":
        evaluate_all(a.tag or a.variant)
    elif a.cmd == "ablate":
        run_ablation(a.variants.split(","), minutes=a.minutes)
    elif a.cmd == "figures":
        make_figures()


if __name__ == "__main__":
    main()
