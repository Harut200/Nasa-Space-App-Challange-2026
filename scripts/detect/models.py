"""Small PyTorch models for radar change detection.

TemporalAE  per-pixel recurrent autoencoder: season series -> 8-number embedding -> series.
UNetSmall   per-date segmentation: {open water, flooded vegetation, other land}.
kmeans      plain k-means written in torch (k-means++ init), runs on MPS/CPU.

Everything takes channel counts / sizes as arguments so it can be retrained when
calibrated, finer NISAR data replaces the previews.
"""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


def n_params(model: nn.Module) -> int:
    return sum(p.numel() for p in model.parameters())


class TemporalAE(nn.Module):
    """GRU encoder -> low-dim embedding -> time-conditioned MLP decoder.

    x:     [B, T, F]  per-date features (e.g. HH, HV, HH-HV, day offset, valid flag)
    t:     [B, T, 1]  day offset (normalised): the decoder is asked "what was the
                      value on this day?" so irregular time steps (August gap) are fine.
    returns (recon [B, T, n_out], z [B, emb])
    """

    def __init__(self, n_in: int = 5, n_out: int = 2, hidden: int = 48, emb: int = 8, encoder: str = "gru"):
        super().__init__()
        self.encoder = encoder
        if encoder == "gru":
            self.enc = nn.GRU(n_in, hidden, num_layers=1, batch_first=True, bidirectional=True)
        elif encoder == "lstm":
            self.enc = nn.LSTM(n_in, hidden, num_layers=1, batch_first=True, bidirectional=True)
        elif encoder == "conv":  # 1-D convolution over time, then mean+max pooling
            self.enc = nn.Sequential(nn.Conv1d(n_in, hidden, 3, padding=1), nn.GELU(),
                                     nn.Conv1d(hidden, hidden, 3, padding=1), nn.GELU())
        else:
            raise ValueError(encoder)
        self.to_z = nn.Linear(2 * hidden, emb)
        self.dec = nn.Sequential(
            nn.Linear(emb + 1, hidden), nn.GELU(),
            nn.Linear(hidden, hidden), nn.GELU(),
            nn.Linear(hidden, n_out),
        )

    def encode(self, x: torch.Tensor) -> torch.Tensor:
        if self.encoder == "conv":
            h = self.enc(x.transpose(1, 2))    # [B, hidden, T]
            return self.to_z(torch.cat([h.mean(-1), h.amax(-1)], dim=-1))
        _, h = self.enc(x)                     # h: [2, B, hidden]  (LSTM: (h, c))
        if isinstance(h, tuple):
            h = h[0]
        return self.to_z(torch.cat([h[0], h[1]], dim=-1))

    def decode(self, z: torch.Tensor, t: torch.Tensor) -> torch.Tensor:
        zt = torch.cat([z[:, None, :].expand(-1, t.shape[1], -1), t], dim=-1)
        return self.dec(zt)

    def forward(self, x: torch.Tensor, t: torch.Tensor):
        z = self.encode(x)
        return self.decode(z, t), z


def _block(cin: int, cout: int) -> nn.Sequential:
    return nn.Sequential(
        nn.Conv2d(cin, cout, 3, padding=1, bias=False), nn.BatchNorm2d(cout), nn.ReLU(inplace=True),
        nn.Conv2d(cout, cout, 3, padding=1, bias=False), nn.BatchNorm2d(cout), nn.ReLU(inplace=True),
    )


class UNetSmall(nn.Module):
    """Depth-3 U-Net (3 poolings). Input H, W should be a multiple of 8 (pad otherwise)."""

    def __init__(self, n_in: int = 4, n_classes: int = 3, base: int = 16, depth: int = 3):
        super().__init__()
        ch = [base * 2 ** i for i in range(depth + 1)]  # depth = number of poolings
        self.downs = nn.ModuleList([_block(n_in, ch[0])] + [_block(ch[i], ch[i + 1]) for i in range(depth)])
        self.ups = nn.ModuleList([nn.ConvTranspose2d(ch[i + 1], ch[i], 2, stride=2) for i in reversed(range(depth))])
        self.up_blocks = nn.ModuleList([_block(2 * ch[i], ch[i]) for i in reversed(range(depth))])
        self.head = nn.Conv2d(ch[0], n_classes, 1)
        self.depth = depth

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        skips = []
        for i, d in enumerate(self.downs):
            x = d(x)
            if i < self.depth:
                skips.append(x)
                x = F.max_pool2d(x, 2)
        for up, blk in zip(self.ups, self.up_blocks):
            x = up(x)
            x = blk(torch.cat([x, skips.pop()], dim=1))
        return self.head(x)

    @torch.no_grad()
    def predict_full(self, x: torch.Tensor) -> torch.Tensor:
        """x [B, C, H, W] of any size -> logits [B, K, H, W] (reflect-pads to a multiple of 2**depth)."""
        m = 2 ** self.depth
        h, w = x.shape[-2:]
        ph, pw = (-h) % m, (-w) % m
        xp = F.pad(x, (0, pw, 0, ph), mode="reflect") if (ph or pw) else x
        return self(xp)[..., :h, :w]


@torch.no_grad()
def kmeans(x: torch.Tensor, k: int, iters: int = 50, seed: int = 0, tol: float = 1e-5):
    """k-means with k-means++ init. x [N, D] -> (centroids [k, D], labels [N])."""
    g = torch.Generator(device="cpu").manual_seed(seed)
    n = x.shape[0]
    c = x[torch.randint(n, (1,), generator=g).item()][None]
    for _ in range(1, k):
        d2 = torch.cdist(x, c).min(dim=1).values ** 2
        p = (d2 / d2.sum()).cpu()
        c = torch.cat([c, x[torch.multinomial(p, 1, generator=g).item()][None]])
    for _ in range(iters):
        lab = torch.cdist(x, c).argmin(dim=1)
        new = torch.zeros_like(c).index_add_(0, lab, x)
        cnt = torch.bincount(lab, minlength=k).clamp_min(1).to(x.dtype)[:, None]
        new = new / cnt
        empty = torch.bincount(lab, minlength=k) == 0
        new[empty] = c[empty]
        shift = (new - c).abs().max().item()
        c = new
        if shift < tol:
            break
    return c, torch.cdist(x, c).argmin(dim=1)


def kmeans_assign(x: torch.Tensor, c: torch.Tensor, chunk: int = 262144) -> torch.Tensor:
    return torch.cat([torch.cdist(x[i:i + chunk], c).argmin(dim=1) for i in range(0, x.shape[0], chunk)])
