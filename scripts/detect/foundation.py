"""Pretrained radar models from Hugging Face: download, adapt to NISAR previews, smoke test, short fine-tune.

Research and ranking: docs/research/hf-models.md. Results: docs/eda/foundation/.

Two models are wired up here, both re-implemented in plain PyTorch (no code from the model
repositories is executed; weights are loaded with torch.load(weights_only=True)):

1. ai4g-flood (Microsoft AI for Good, Misra et al. 2025, Nature Communications 16:5762), MIT.
   HF: torchgeo/ai4g_flood, 26.8 MB. U-Net with a MobileNetV2 encoder (torchvision layout,
   segmentation_models_pytorch decoder layout). Sentinel-1 C-band, VV + VH, pre- and post-event.
   Its 2 input channels are NOT backscatter: they are binary "became dark" change masks
   (one for VV, one for VH) from fixed thresholds on 2*dB+135 scaled backscatter; the network
   cleans that rule mask into a flood map (official inference then keeps flood only where
   both rule channels fire). This is the model the smoke test and the fine-tune run on,
   because it is the only useful radar model small enough to download over the ~50 KB/s link
   we had (see the research note).

2. SAR-W-SimMIM (AIST, gsvrg/ALOS-2_FM, MIT): Swin-B encoder pretrained on ALOS-2 PALSAR-2
   L-band HH, single channel, dB normalised with mean -10.4327, std 5.8041, 256x256 input.
   The best physical fit for NISAR (same band, same HH channel). The module below matches the
   original Swin key layout, but its weights (352 MB) could not be downloaded in time, so
   it is only shape-tested. `download alos2` resumes the partial download.

Channel mapping (preview -> Sentinel-1 style):
  NISAR HH -> S1 VV slot, NISAR HV -> S1 VH slot (co-pol -> co-pol, cross-pol -> cross-pol).
  Preview brightness behaves like a log stretch over ~30 dB (docs/eda/stats-change.md), so
  pseudo-dB = 30 * brightness + offset, the offset per scene and channel set so that the median
  brightness of the math detector's open water on the reference (first) date maps to typical S1
  water (VV -22 dB, VH -28 dB). The anchor uses the math detector's own water mask: the
  adaptation is not independent of it (documented limitation).

CLI (repo root):
  .venv/bin/python -m scripts.detect.foundation download [ai4g|alos2]
  .venv/bin/python -m scripts.detect.foundation smoke
  .venv/bin/python -m scripts.detect.foundation finetune --minutes 5
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import pathlib
import subprocess
import sys
import time

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

ROOT = pathlib.Path(__file__).resolve().parents[2]
HF_DIR = ROOT / "data" / "external" / "hf"
MODELS = ROOT / "data" / "processed" / "models"
DOCS = ROOT / "docs" / "eda" / "foundation"

WEIGHTS = {
    "ai4g": {"repo": "torchgeo/ai4g_flood", "file": "unet_mobilenetv2_sentinel1_ai4g_flood-d95df7aa.pth",
             "size": 26_801_051, "dir": "torchgeo_ai4g_flood"},
    "alos2": {"repo": "gsvrg/ALOS-2_FM", "file": "model_weights_simmim_pretrain/sar_w_simim_encoder.pth",
              "size": 351_966_554, "dir": "gsvrg_ALOS-2_FM"},
}

# ai4g-flood defaults (src/run_flood_detection_downloaded_images.py), in 2*dB+135 units
AI4G = {"vv_threshold": 100, "vh_threshold": 90, "delta_amplitude": 10, "vv_min_threshold": 75,
        "vh_min_threshold": 70, "input_size": 128}
# typical Sentinel-1 sigma0 (dB) used as anchors of the preview -> pseudo-dB map
S1_ANCHORS_DB = {"water": (-22.0, -28.0), "land": (-9.0, -16.0)}  # (VV, VH)
ALOS2_MEAN_DB, ALOS2_STD_DB = -10.4327, 5.8041


# ----------------------------------------------------------------------------- download
def weights_path(name: str) -> pathlib.Path:
    w = WEIGHTS[name]
    return HF_DIR / w["dir"] / pathlib.Path(w["file"]).name


def download(name: str, workers: int = 32, chunk: int = 1_000_000) -> pathlib.Path:
    """Parallel ranged download from https://huggingface.co/<repo>/resolve/main/<file>.

    The link here was throttled per connection (~10 KB/s each), so many ranges run at once.
    Resumable: finished chunks in <file>.parts/ are kept and skipped."""
    w = WEIGHTS[name]
    out = weights_path(name)
    if out.exists() and out.stat().st_size == w["size"]:
        return out
    url = f"https://huggingface.co/{w['repo']}/resolve/main/{w['file']}"
    parts = out.parent / (out.name + ".parts")
    parts.mkdir(parents=True, exist_ok=True)
    size = w["size"]
    # keep the chunking of an earlier partial run, if any (largest finished part = chunk size)
    old = [f for f in parts.iterdir() if f.is_file()]
    if len(old) > 1:
        chunk = max(f.stat().st_size for f in old)
    n = (size + chunk - 1) // chunk

    def get(i):
        s, e = i * chunk, min(size, (i + 1) * chunk) - 1
        f = parts / f"{i:05d}"
        for _ in range(8):
            if f.exists() and f.stat().st_size == e - s + 1:
                return
            subprocess.run(["curl", "-s", "-L", "-m", "900", "-r", f"{s}-{e}", "-o", str(f), url], check=False)
        raise RuntimeError(f"chunk {i} of {name} failed")

    t0 = time.time()
    with cf.ThreadPoolExecutor(workers) as ex:
        list(ex.map(get, range(n)))
    with open(out, "wb") as fo:
        for i in range(n):
            fo.write((parts / f"{i:05d}").read_bytes())
    print(f"downloaded {out} ({out.stat().st_size} B) in {time.time() - t0:.0f} s")
    return out


# ----------------------------------------------------------------------------- input adaptation
def preview_to_pseudo_db(x: torch.Tensor, water: torch.Tensor, slope_db: float = 30.0,
                         water_db=S1_ANCHORS_DB["water"]) -> tuple[torch.Tensor, list]:
    """x [T, 2, H, W] or [2, H, W] preview brightness 0..1 (HH, HV); water [H, W] bool (reference date).

    Preview brightness behaves like a log stretch spanning ~30 dB (docs/eda/stats-change.md), so
    dB = slope_db * brightness + offset, with the offset per channel chosen so that the median
    brightness of the math detector's open water maps to typical Sentinel-1 water
    (VV slot -22 dB for HH, VH slot -28 dB for HV). Returns pseudo-dB and [(gain, offset)] per channel."""
    out, coef = torch.empty_like(x), []
    for c in range(2):
        xc = x[..., c, :, :]
        ref = x[0, c] if x.dim() == 4 else xc
        o = water_db[c] - slope_db * ref[water].median().item()
        out[..., c, :, :] = slope_db * xc + o
        coef.append((slope_db, o))
    return out, coef


def db_scale(db: torch.Tensor) -> torch.Tensor:
    """ai4g-flood scaling: 2*dB + 135, clipped to 0..255."""
    return (db * 2 + 135).clamp(0, 255)


def ai4g_change(pre_db: torch.Tensor, post_db: torch.Tensor, p: dict = AI4G) -> torch.Tensor:
    """pre/post [..., 2, H, W] pseudo-dB (VV-slot, VH-slot) -> [..., 2, H, W] binary change channels,
    exactly the rule of ai4g-flood calculate_flood_change (on 2*dB+135 values)."""
    a, b = db_scale(pre_db), db_scale(post_db)
    thr = torch.tensor([p["vv_threshold"], p["vh_threshold"]], dtype=a.dtype, device=a.device).view(2, 1, 1)
    mn = torch.tensor([p["vv_min_threshold"], p["vh_min_threshold"]], dtype=a.dtype, device=a.device).view(2, 1, 1)
    ch = (b < thr) & (a > thr) & ((a - b) > p["delta_amplitude"])
    zero = ((b < mn) | (a < mn)).any(dim=-3, keepdim=True)
    return (ch & ~zero).to(a.dtype)


def alos2_normalise(hh_db: torch.Tensor) -> torch.Tensor:
    """ALOS-2 SAR-W-SimMIM input normalisation (dB)."""
    return (hh_db - ALOS2_MEAN_DB) / ALOS2_STD_DB


def adapt_in_channels(w: torch.Tensor, n_in: int, mode: str = "zero") -> torch.Tensor:
    """Adapt a first-layer weight [out, c_in, k, k] to n_in input channels.
    mode 'zero': keep the pretrained channels, new channels start at 0 (output unchanged at init);
    mode 'mean': every channel = mean of pretrained channels * c_in / n_in (keeps the activation scale
    for inputs that are copies of one another)."""
    o, c, kh, kw = w.shape
    if n_in == c:
        return w.clone()
    if mode == "zero":
        new = torch.zeros(o, n_in, kh, kw, dtype=w.dtype)
        k = min(c, n_in)
        new[:, :k] = w[:, :k]
        return new
    if mode == "mean":
        return w.mean(1, keepdim=True).repeat(1, n_in, 1, 1) * c / n_in
    raise ValueError(mode)


# ----------------------------------------------------------------------------- ai4g U-Net (MobileNetV2)
class _ConvBNReLU(nn.Sequential):
    def __init__(self, i, o):
        super().__init__(nn.Conv2d(i, o, 3, padding=1, bias=False), nn.BatchNorm2d(o), nn.ReLU(inplace=True))


class _DecoderBlock(nn.Module):
    def __init__(self, i, skip, o):
        super().__init__()
        self.conv1 = _ConvBNReLU(i + skip, o)
        self.conv2 = _ConvBNReLU(o, o)

    def forward(self, x, skip=None):
        x = F.interpolate(x, scale_factor=2, mode="nearest")
        if skip is not None:
            x = torch.cat([x, skip], 1)
        return self.conv2(self.conv1(x))


class _Decoder(nn.Module):
    def __init__(self, enc=(16, 24, 32, 96, 1280), dec=(256, 128, 64, 32, 16)):
        super().__init__()
        head, skips = enc[-1], list(enc[:-1][::-1]) + [0]
        ins = [head] + list(dec[:-1])
        self.blocks = nn.ModuleList([_DecoderBlock(i, s, o) for i, s, o in zip(ins, skips, dec)])

    def forward(self, feats):  # feats: strides 2, 4, 8, 16, 32
        x, skips = feats[-1], feats[:-1][::-1]
        for i, b in enumerate(self.blocks):
            x = b(x, skips[i] if i < len(skips) else None)
        return x


class AI4GFloodUNet(nn.Module):
    """segmentation_models_pytorch Unet(encoder_name='mobilenet_v2', in_channels=2, classes=2) re-implemented."""
    STAGES = ((0, 2), (2, 4), (4, 7), (7, 14), (14, 19))

    def __init__(self, in_channels: int = 2, classes: int = 2):
        super().__init__()
        from torchvision.models import mobilenet_v2
        enc = mobilenet_v2(weights=None)
        enc.features[0][0] = nn.Conv2d(in_channels, 32, 3, 2, 1, bias=False)
        self.encoder = nn.Module()
        self.encoder.features = enc.features
        self.decoder = _Decoder()
        self.segmentation_head = nn.Sequential(nn.Conv2d(16, classes, 3, padding=1))

    def features(self, x):
        out = []
        for a, b in self.STAGES:
            for m in self.encoder.features[a:b]:
                x = m(x)
            out.append(x)
        return out

    def forward(self, x):
        return self.segmentation_head(self.decoder(self.features(x)))


def load_ai4g(path: pathlib.Path | None = None) -> tuple[AI4GFloodUNet, dict]:
    sd = torch.load(path or weights_path("ai4g"), map_location="cpu", weights_only=True)
    m = AI4GFloodUNet()
    res = m.load_state_dict(sd, strict=True)
    return m, {"missing": list(res.missing_keys), "unexpected": list(res.unexpected_keys), "n_tensors": len(sd)}


# ----------------------------------------------------------------------------- ALOS-2 Swin (SAR-W-SimMIM)
def _win_part(x, w):
    B, H, W, C = x.shape
    return x.view(B, H // w, w, W // w, w, C).permute(0, 1, 3, 2, 4, 5).reshape(-1, w * w, C)


def _win_rev(x, w, H, W):
    B = x.shape[0] // ((H // w) * (W // w))
    return x.view(B, H // w, W // w, w, w, -1).permute(0, 1, 3, 2, 4, 5).reshape(B, H, W, -1)


class _WinAttn(nn.Module):
    def __init__(self, dim, w, heads):
        super().__init__()
        self.w, self.h = w, heads
        self.relative_position_bias_table = nn.Parameter(torch.zeros((2 * w - 1) ** 2, heads))
        c = torch.stack(torch.meshgrid(torch.arange(w), torch.arange(w), indexing="ij")).flatten(1)
        r = (c[:, :, None] - c[:, None, :]).permute(1, 2, 0) + (w - 1)
        self.register_buffer("relative_position_index", r[..., 0] * (2 * w - 1) + r[..., 1], persistent=False)
        self.qkv = nn.Linear(dim, dim * 3)
        self.proj = nn.Linear(dim, dim)

    def forward(self, x, mask=None):
        B, N, C = x.shape
        q, k, v = self.qkv(x).reshape(B, N, 3, self.h, C // self.h).permute(2, 0, 3, 1, 4)
        bias = self.relative_position_bias_table[self.relative_position_index.view(-1)].view(N, N, -1).permute(2, 0, 1)
        a = (q * (C // self.h) ** -0.5) @ k.transpose(-2, -1) + bias[None]
        if mask is not None:
            nW = mask.shape[0]
            a = (a.view(B // nW, nW, self.h, N, N) + mask[None, :, None]).view(B, self.h, N, N)
        return self.proj((a.softmax(-1) @ v).transpose(1, 2).reshape(B, N, C))


class _SwinBlock(nn.Module):
    def __init__(self, dim, heads, w, shift):
        super().__init__()
        self.w, self.shift = w, shift
        self.norm1, self.norm2 = nn.LayerNorm(dim), nn.LayerNorm(dim)
        self.attn = _WinAttn(dim, w, heads)
        self.mlp = nn.Sequential()
        self.mlp.fc1, self.mlp.act, self.mlp.fc2 = nn.Linear(dim, 4 * dim), nn.GELU(), nn.Linear(4 * dim, dim)

    def _mask(self, H, W, dev):
        m = torch.zeros(1, H, W, 1, device=dev)
        s, w, n = self.shift, self.w, 0
        for h in (slice(0, -w), slice(-w, -s), slice(-s, None)):
            for ww in (slice(0, -w), slice(-w, -s), slice(-s, None)):
                m[:, h, ww] = n
                n += 1
        mw = _win_part(m, w)[..., 0]
        d = mw[:, None] - mw[:, :, None]
        return d.masked_fill(d != 0, -100.0).masked_fill(d == 0, 0.0)

    def forward(self, x, H, W):
        B, L, C = x.shape
        y = self.norm1(x).view(B, H, W, C)
        w, s = min(self.w, H, W), (self.shift if min(H, W) > self.w else 0)
        if s:
            y = torch.roll(y, (-s, -s), (1, 2))
        y = self.attn(_win_part(y, w), self._mask(H, W, x.device) if s else None)
        y = _win_rev(y, w, H, W)
        if s:
            y = torch.roll(y, (s, s), (1, 2))
        x = x + y.reshape(B, L, C)
        return x + self.mlp(self.norm2(x))


class _Merge(nn.Module):
    def __init__(self, dim):
        super().__init__()
        self.norm, self.reduction = nn.LayerNorm(4 * dim), nn.Linear(4 * dim, 2 * dim, bias=False)

    def forward(self, x, H, W):
        B, L, C = x.shape
        x = x.view(B, H, W, C)
        x = torch.cat([x[:, 0::2, 0::2], x[:, 1::2, 0::2], x[:, 0::2, 1::2], x[:, 1::2, 1::2]], -1)
        return self.reduction(self.norm(x.view(B, -1, 4 * C)))


class SwinEncoder(nn.Module):
    """Original Microsoft Swin key layout (as in SAR-W-SimMIM models/swin_transformer.py), APE on,
    returns the 4 stage outputs as [B, C, H/4..H/32, ...] maps (before the next patch merging)."""

    def __init__(self, img=256, in_chans=1, embed=128, depths=(2, 2, 18, 2), heads=(4, 8, 16, 32), window=8):
        super().__init__()
        self.patch_embed = nn.Module()
        self.patch_embed.proj = nn.Conv2d(in_chans, embed, 4, 4)
        self.patch_embed.norm = nn.LayerNorm(embed)
        self.absolute_pos_embed = nn.Parameter(torch.zeros(1, (img // 4) ** 2, embed))
        self.layers = nn.ModuleList()
        res = img // 4
        for i, (d, h) in enumerate(zip(depths, heads)):
            L = nn.Module()
            dim = embed * 2 ** i
            w = min(window, res)  # as the original: window shrinks to the map size, no shift then
            L.blocks = nn.ModuleList([_SwinBlock(dim, h, w, 0 if j % 2 == 0 or res <= window else window // 2)
                                      for j in range(d)])
            L.downsample = _Merge(dim) if i < len(depths) - 1 else None
            self.layers.append(L)
            res //= 2
        self.norm = nn.LayerNorm(embed * 2 ** (len(depths) - 1))
        self.dims = [embed * 2 ** i for i in range(len(depths))]

    def forward(self, x):
        x = self.patch_embed.proj(x)
        B, C, H, W = x.shape
        x = self.patch_embed.norm(x.flatten(2).transpose(1, 2)) + self.absolute_pos_embed
        feats = []
        for i, L in enumerate(self.layers):
            for b in L.blocks:
                x = b(x, H, W)
            y = self.norm(x) if i == len(self.layers) - 1 else x
            feats.append(y.transpose(1, 2).reshape(B, -1, H, W))
            if L.downsample is not None:
                x = L.downsample(x, H, W)
                H, W = H // 2, W // 2
        return feats


def load_alos2_encoder(path: pathlib.Path | None = None, in_chans: int = 2, mode: str = "zero"):
    """Load sar_w_simim_encoder.pth into SwinEncoder; the 1-channel HH patch embedding is widened
    to `in_chans` (HH pretrained, HV starts at 0 with mode 'zero')."""
    sd = torch.load(path or weights_path("alos2"), map_location="cpu", weights_only=True)
    sd = sd.get("model", sd)
    sd = {k.removeprefix("encoder."): v for k, v in sd.items()}
    sd = {k: v for k, v in sd.items() if not k.endswith(("relative_position_index", "attn_mask"))}
    if "patch_embed.proj.weight" in sd:
        sd["patch_embed.proj.weight"] = adapt_in_channels(sd["patch_embed.proj.weight"], in_chans, mode)
    m = SwinEncoder(in_chans=in_chans)
    res = m.load_state_dict(sd, strict=False)
    return m, {"missing": list(res.missing_keys), "unexpected": list(res.unexpected_keys)}


class LightFPNHead(nn.Module):
    """Light segmentation head: 1x1 projections of multi-scale features, upsample-and-sum, 3x3 conv, classes.
    Output at the input resolution (stride of the finest feature given by `stride0`)."""

    def __init__(self, dims, classes=3, width=64, stride0=4):
        super().__init__()
        self.proj = nn.ModuleList([nn.Conv2d(d, width, 1) for d in dims])
        self.fuse = nn.Sequential(nn.Conv2d(width, width, 3, padding=1), nn.BatchNorm2d(width), nn.ReLU(inplace=True),
                                  nn.Conv2d(width, classes, 1))
        self.stride0 = stride0

    def forward(self, feats):
        size = feats[0].shape[-2:]
        y = sum(F.interpolate(p(f), size=size, mode="bilinear", align_corners=False) if i else p(f)
                for i, (p, f) in enumerate(zip(self.proj, feats)))
        y = self.fuse(y)
        return F.interpolate(y, scale_factor=self.stride0, mode="bilinear", align_corners=False)


# ----------------------------------------------------------------------------- data for ai4g
def _mem_mb(dev):
    if dev.type == "mps":
        return torch.mps.driver_allocated_memory() / 1e6
    return float("nan")


def scene_inputs(area: str) -> dict:
    """Preview stack + math labels -> ai4g rule-change inputs and flood-change targets per date.

    target[t] = math open water at t AND NOT math open water on the reference date (= new water,
    what ai4g-flood was trained to map); IGNORE where the v2 label is IGNORE at t or at the reference."""
    from scripts.detect.train import v2_scene
    from scripts.detect.weak_labels import IGNORE, OTHER, WATER
    from scripts.detect.terrain import cached_water_possible
    from scripts.detect.weak_labels import math_labels
    sc = v2_scene(area)
    st, mp = sc["st"], dict(sc["math"])
    valid = sc["valid"]
    ok = cached_water_possible(area, tuple(st.x.shape[-2:]))
    if ok is not None:  # terrain mask of the current math detector (water impossible on steep / shadowed slopes)
        mp["water"] = mp["water"] & ok
        mp["flooded_veg"] = mp["flooded_veg"] & ok
    lab = math_labels(mp["water"], mp["flooded_veg"], valid, mp["wind_suspect"], 1)
    water0 = mp["water"][0] & valid[0]
    db, coef = preview_to_pseudo_db(st.x, water0)
    T = st.x.shape[0]
    pre = db[0:1].expand(T, -1, -1, -1)
    change = ai4g_change(pre, db)
    change[0] = 0
    tgt = (mp["water"] & ~mp["water"][0:1]).to(torch.uint8)
    ign = (lab == IGNORE) | (lab[0:1] == IGNORE) | ~valid | ~valid[0:1]
    tgt[ign] = IGNORE
    return {"area": area, "st": st, "sc": sc, "x": change, "terrain_mask": ok is not None, "y": tgt, "valid": valid & valid[0:1],
            "coef": coef, "dates": st.dates, "split": sc["split"], "water": mp["water"], "lab": lab}


@torch.no_grad()
def predict_full(model, x: torch.Tensor, dev, gate: bool, tile: int = 224) -> torch.Tensor:
    """x [T, 2, H, W] -> flood class [T, H, W] (1 = flood). Padded to a multiple of `tile`, tiled (<= 224 px)."""
    model.eval()
    T, _, H, W = x.shape
    ph, pw = (-H) % tile, (-W) % tile
    xp = F.pad(x, (0, pw, 0, ph))
    out = torch.zeros(T, H + ph, W + pw, dtype=torch.uint8)
    for t in range(T):
        for r in range(0, H + ph, tile):
            for c in range(0, W + pw, tile):
                logits = model(xp[t:t + 1, :, r:r + tile, c:c + tile].to(dev))
                out[t, r:r + tile, c:c + tile] = logits.argmax(1)[0].to(torch.uint8).cpu()
    out = out[:, :H, :W]
    if gate:
        out &= (x[:, 0] > 0) & (x[:, 1] > 0)
    return out


def iou(pred: torch.Tensor, y: torch.Tensor, region: torch.Tensor) -> dict:
    from scripts.detect.weak_labels import IGNORE
    m = (y != IGNORE) & region
    p, t = (pred == 1) & m, (y == 1) & m
    inter, union = (p & t).sum().item(), (p | t).sum().item()
    return {"iou_flood": inter / union if union else float("nan"),
            "precision": inter / max(p.sum().item(), 1), "recall": inter / max(t.sum().item(), 1),
            "pred_px": p.sum().item(), "target_px": t.sum().item()}


def evaluate(pred: torch.Tensor, d: dict) -> dict:
    T, H, W = pred.shape
    km2 = d["st"].km2_per_px
    cols = torch.arange(W)[None, None].expand(T, H, W)
    region = cols >= d["split"] if d["split"] < W else torch.ones(T, H, W, dtype=torch.bool)
    out = iou(pred[1:], d["y"][1:], region[1:])  # skip the reference date (no change by construction)
    v = d["valid"]
    out["flood_km2"] = [round(((pred[t] == 1) & v[t]).sum().item() * km2, 1) for t in range(T)]
    out["math_new_water_km2"] = [round(((d["y"][t] == 1)).sum().item() * km2, 1) for t in range(T)]
    return out


# ----------------------------------------------------------------------------- commands
def cmd_download(a):
    for n in (a.which or ["ai4g"]):
        print(download(n))


def cmd_smoke(a):
    from scripts.detect.stack import device as auto_device
    dev = auto_device()
    res = {"device": str(dev)}
    t0 = time.time()
    path = download("ai4g")
    res["download_s"] = round(time.time() - t0, 1)
    t0 = time.time()
    model, info = load_ai4g(path)
    model = model.to(dev).eval()
    res["load_s"] = round(time.time() - t0, 2)
    res["load_info"] = {"n_tensors": info["n_tensors"], "missing": info["missing"], "unexpected": info["unexpected"]}
    res["params"] = sum(p.numel() for p in model.parameters())
    # real 2-channel patch: reference date vs. a flood-season date, 224 x 224 from the scene centre
    d = scene_inputs("tonle-sap")
    st = d["st"]
    t = d["dates"].index("2026-09-24") if "2026-09-24" in d["dates"] else len(d["dates"]) - 3
    H, W = st.x.shape[-2:]
    r0, c0 = H // 2 - 112, W // 2 - 112
    patch = d["x"][t:t + 1, :, r0:r0 + 224, c0:c0 + 224].contiguous()
    res["terrain_mask_used"] = d["terrain_mask"]
    res["patch"] = {"date": d["dates"][t], "ref_date": d["dates"][0], "rows": [r0, r0 + 224], "cols": [c0, c0 + 224],
                    "rule_fraction_vv_hv": [round(patch[0, 0].mean().item(), 4), round(patch[0, 1].mean().item(), 4)],
                    "pseudo_db_coef_hh_hv": [[round(g, 2), round(o, 2)] for g, o in d["coef"]]}
    with torch.no_grad():
        model(patch.to(dev))  # warm-up
        if dev.type == "mps":
            torch.mps.synchronize()
        t0 = time.time()
        for _ in range(5):
            y = model(patch.to(dev))
        if dev.type == "mps":
            torch.mps.synchronize()
    res["forward_ms_224"] = round((time.time() - t0) / 5 * 1000, 1)
    res["output_shape"] = list(y.shape)
    res["mps_driver_mem_mb"] = round(_mem_mb(dev), 1)
    p = y.argmax(1)[0].cpu()
    res["flood_fraction_raw"] = round((p == 1).float().mean().item(), 4)
    res["flood_fraction_gated"] = round(((p == 1) & (patch[0, 0].cpu() > 0) & (patch[0, 1].cpu() > 0)).float().mean().item(), 4)
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "smoke.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


def _train(model, d, dev, seconds, mode, lr=1e-3, P=128, B=4, seed=0):
    """Fine-tune on Tonle Sap west (cols < split), dates 1.. (date 0 is the reference)."""
    from scripts.detect.weak_labels import IGNORE
    g = torch.Generator().manual_seed(seed)
    for p in model.parameters():
        p.requires_grad = False
    groups = [model.decoder.parameters(), model.segmentation_head.parameters()]
    if mode == "last":
        groups.append(model.encoder.features[14:].parameters())
    params = [p for gr in groups for p in gr]
    for p in params:
        p.requires_grad = True
    opt = torch.optim.Adam(params, lr=lr)
    x, y = d["x"], d["y"].long()
    y[y == IGNORE] = -1
    T, _, H, W = x.shape
    split = d["split"]
    t0, steps, losses = time.time(), 0, []
    model.train()
    if mode != "last":
        model.encoder.eval()  # frozen BN statistics
    while time.time() - t0 < seconds:
        ts = torch.randint(1, T, (B,), generator=g)
        rs = torch.randint(0, H - P + 1, (B,), generator=g)
        cs = torch.randint(0, split - P + 1, (B,), generator=g)
        xb = torch.stack([x[t, :, r:r + P, c:c + P] for t, r, c in zip(ts, rs, cs)])
        yb = torch.stack([y[t, r:r + P, c:c + P] for t, r, c in zip(ts, rs, cs)])
        if torch.rand(1, generator=g).item() < 0.5:
            xb, yb = xb.flip(-1), yb.flip(-1)
        logits = model(xb.to(dev))
        w = torch.tensor([1.0, 3.0], device=dev)  # new water is the rare class
        loss = F.cross_entropy(logits, yb.to(dev), weight=w, ignore_index=-1)
        opt.zero_grad()
        loss.backward()
        opt.step()
        steps += 1
        losses.append(loss.item())
    return {"steps": steps, "seconds": round(time.time() - t0, 1), "loss_first": round(float(np.mean(losses[:10])), 4),
            "loss_last": round(float(np.mean(losses[-10:])), 4), "trainable_params": sum(p.numel() for p in params)}


def cmd_finetune(a):
    from scripts.detect.stack import device as auto_device
    dev = auto_device()
    path = download("ai4g")
    ts, sv = scene_inputs("tonle-sap"), scene_inputs("sevan")
    results = {"pseudo_db_coef": {"tonle-sap": ts["coef"], "sevan": sv["coef"]}, "runs": {}}
    budget = a.minutes * 60

    def run(name, model, gate):
        r = {"tonle_sap_east": evaluate(predict_full(model, ts["x"], dev, gate), ts)}
        ps = predict_full(model, sv["x"], dev, gate)
        r["sevan_all"] = evaluate(ps, sv)
        results["runs"][name] = r
        print(name, json.dumps({k: (v["iou_flood"] if isinstance(v, dict) else v) for k, v in r.items()}))
        return r

    # baselines: the rule mask alone (both channels), zero-shot model (official gating and raw)
    class Rule(nn.Module):
        def forward(self, x):
            f = ((x[:, 0] > 0) & (x[:, 1] > 0)).float()
            return torch.stack([1 - f, f], 1)
    run("rule_only", Rule().to(dev), False)
    m0, _ = load_ai4g(path)
    m0 = m0.to(dev)
    run("zeroshot_gated", m0, True)
    run("zeroshot_raw", m0, False)
    for mode in ("frozen", "last"):
        m, _ = load_ai4g(path)
        m = m.to(dev)
        tr = _train(m, ts, dev, budget / 2, mode)
        r = run(f"finetune_{mode}", m, False)
        r["train"] = tr
        MODELS.mkdir(parents=True, exist_ok=True)
        torch.save(m.state_dict(), MODELS / f"foundation_ai4g_ft_{mode}.pt")
    # random-init control with the same budget split (does the pretraining matter?)
    if a.control:
        m = AI4GFloodUNet().to(dev)
        tr = _train(m, ts, dev, budget / 2, "last")
        for p in m.parameters():
            p.requires_grad = True
        r = run("scratch_same_budget", m, False)
        r["train"] = tr
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "finetune.json").write_text(json.dumps(results, indent=1, default=float))
    print("wrote", DOCS / "finetune.json")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="foundation")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("download")
    p.add_argument("which", nargs="*", choices=list(WEIGHTS))
    p.set_defaults(fn=cmd_download)
    p = sub.add_parser("smoke")
    p.set_defaults(fn=cmd_smoke)
    p = sub.add_parser("finetune")
    p.add_argument("--minutes", type=float, default=5)
    p.add_argument("--control", action="store_true", help="also train a random-init model (extra budget/2)")
    p.set_defaults(fn=cmd_finetune)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main(sys.argv[1:])
