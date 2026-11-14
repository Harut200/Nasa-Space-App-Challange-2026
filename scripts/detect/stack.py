"""Load a time stack of radar images for one area as PyTorch tensors.

Shared by EDA, math detection and the neural models, so all three see the
same pixels on the same grid.

    from scripts.detect.stack import load_preview_stack
    st = load_preview_stack("tonle-sap")
    st.x      # [T, 2, H, W] float32, channel 0 = HH, channel 1 = HV
    st.valid  # [T, H, W] bool

Source today: the free ASF preview images (see scripts/ingest/fetch_previews.py).
Their values are display brightness in 0..1, NOT calibrated backscatter, at
about 180 m per pixel. In the previews red = blue = HH and green = HV.
"""
from __future__ import annotations

import json
import math
import pathlib
import re
from dataclasses import dataclass

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHANNELS = ("HH", "HV")
GAPS = [("2026-07-27", "2026-08-10")]  # instrument outage, no data exists


@dataclass
class Stack:
    area: dict
    dates: list[str]
    x: torch.Tensor          # [T, 2, H, W], 0..1 for previews
    valid: torch.Tensor      # [T, H, W] bool
    bounds: tuple[float, float, float, float]  # west, south, east, north (pixel edges)
    km2_per_px: float
    source: str              # "preview" (uncalibrated) or "gamma0"
    shifts: list | None = None  # per date (rows, cols) correction applied to line it up with the first date

    @property
    def shape(self):
        return tuple(self.x.shape)

    def lonlat_to_rc(self, lon: float, lat: float) -> tuple[int, int]:
        w, s, e, n = self.bounds
        _, _, h, wd = self.x.shape
        return int((n - lat) / (n - s) * h), int((lon - w) / (e - w) * wd)

    def to(self, device) -> "Stack":
        return Stack(self.area, self.dates, self.x.to(device), self.valid.to(device),
                     self.bounds, self.km2_per_px, self.source, self.shifts)


def device() -> torch.device:
    """Apple GPU when available, otherwise CPU."""
    return torch.device("mps" if torch.backends.mps.is_available() else "cpu")


def load_area(area_id: str) -> dict:
    return json.loads((ROOT / "areas" / area_id / "area.json").read_text())


def _polygon(wkt: str) -> list[tuple[float, float]]:
    pts = [tuple(map(float, p.split())) for p in re.search(r"\(\(([^)]*)\)", wkt).group(1).split(",")]
    return pts[:-1] if pts[0] == pts[-1] else pts


def _georef(rgb: np.ndarray, poly: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    """Fit lon = a*col + b, lat = c*row + d from the image's four footprint corners.

    The preview is north-up with a transparent margin; the outermost lit pixels
    are the west, east, north and south corners of the footprint polygon.
    """
    lit = rgb.max(axis=2) > 0
    rows, cols = np.nonzero(lit)
    c_w, c_e, r_n, r_s = cols.min(), cols.max(), rows.min(), rows.max()
    lon_w, lon_e = min(p[0] for p in poly), max(p[0] for p in poly)
    lat_s, lat_n = min(p[1] for p in poly), max(p[1] for p in poly)
    a = (lon_e - lon_w) / (c_e - c_w)
    c = (lat_s - lat_n) / (r_s - r_n)
    return a, lon_w - a * c_w, c, lat_n - c * r_n


def _inside(poly: list[tuple[float, float]], lon: torch.Tensor, lat: torch.Tensor) -> torch.Tensor:
    inside = torch.zeros_like(lon, dtype=torch.bool)
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
        if y1 == y2:
            continue
        crosses = ((y1 > lat) != (y2 > lat)) & (lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1)
        inside ^= crosses
    return inside


def _shift(a: torch.Tensor, b: torch.Tensor, mask: torch.Tensor) -> tuple[float, float]:
    """Sub-pixel (rows, cols) such that b(r, c) ~ a(r - rows, c - cols): peak of the
    FFT cross-correlation of the two images after removing their local mean."""
    def prep(x):
        local = F.avg_pool2d(x[None, None], 15, 1, 7, count_include_pad=False)[0, 0]
        x = (x - local) * mask
        return x * torch.hann_window(x.shape[0], periodic=False)[:, None] * torch.hann_window(x.shape[1], periodic=False)[None]

    cc = torch.fft.irfft2(torch.fft.rfft2(prep(b)) * torch.fft.rfft2(prep(a)).conj(), s=a.shape)
    h, w = cc.shape
    py, px = divmod(int(cc.argmax()), w)

    def peak(m1, m0, p1):
        den = float(m1 - 2 * m0 + p1)
        return 0.0 if abs(den) < 1e-12 else float(0.5 * (m1 - p1) / den)

    sy = py + peak(cc[(py - 1) % h, px], cc[py, px], cc[(py + 1) % h, px])
    sx = px + peak(cc[py, (px - 1) % w], cc[py, px], cc[py, (px + 1) % w])
    return (sy if sy < h / 2 else sy - h), (sx if sx < w / 2 else sx - w)


def load_preview_stack(area_id: str, bbox: tuple[float, float, float, float] | None = None,
                       align: bool = True) -> Stack:
    """All preview dates of an area, resampled onto one north-up lon/lat grid.

    The corner-based georeferencing of a preview is only good to a few pixels, so
    with align=True every date is then shifted onto the first date by image
    correlation (residual below about 0.1 pixel).
    """
    area = load_area(area_id)
    w, s, e, n = bbox or area["bbox"]
    folder = ROOT / "data" / "raw" / "previews" / f"T{area['track']}_F{area['frame']}"
    passes = json.loads((folder / "meta.json").read_text())["passes"]

    images = []
    for p in passes:
        rgb = np.asarray(Image.open(folder / f"{p['date']}.png").convert("RGB"))
        poly = _polygon(p["wkt"])
        images.append((rgb, poly, _georef(rgb, poly)))

    a0, _, c0, _ = images[0][2]  # target grid: the first date's pixel size
    wd, h = round((e - w) / a0), round((s - n) / c0)
    dlon, dlat = (e - w) / wd, (s - n) / h
    lon = w + (torch.arange(wd) + 0.5) * dlon
    lat = n + (torch.arange(h) + 0.5) * dlat
    lat_g, lon_g = torch.meshgrid(lat, lon, indexing="ij")
    km2 = (dlon * 111.32 * math.cos(math.radians((s + n) / 2))) * (-dlat * 110.57)

    def resample(rgb, poly, geo, rows=0.0, cols=0.0):
        a, b, c, d = geo
        lo, la = lon_g + cols * dlon, lat_g + rows * dlat
        src_h, src_w = rgb.shape[:2]
        sample = torch.stack([((lo - b) / a - 0.5) / (src_w - 1) * 2 - 1,
                              ((la - d) / c - 0.5) / (src_h - 1) * 2 - 1], dim=-1)[None]
        img = torch.from_numpy(rgb[..., :2].copy()).permute(2, 0, 1)[None].float() / 255.0  # R=HH, G=HV
        out = F.grid_sample(img, sample, mode="bilinear", padding_mode="zeros", align_corners=True)[0]
        return out, _inside(poly, lo, la) & (sample.abs() <= 1).all(dim=-1)[0]

    first = [resample(*im) for im in images]
    shifts = [(0.0, 0.0)] * len(images)
    if align:
        common = torch.stack([v for _, v in first]).all(0).float()
        ref = first[0][0].mean(0)
        shifts = [(0.0, 0.0)] + [_shift(ref, x.mean(0), common) for x, _ in first[1:]]
        first = [resample(*im, rows=sy, cols=sx) for im, (sy, sx) in zip(images, shifts)]

    return Stack(area, [p["date"] for p in passes], torch.stack([x for x, _ in first]),
                 torch.stack([v for _, v in first]), (w, s, e, n), km2, "preview", shifts)


if __name__ == "__main__":
    import sys
    st = load_preview_stack(sys.argv[1] if len(sys.argv) > 1 else "tonle-sap")
    print(st.area["id"], st.shape, f"{st.km2_per_px:.4f} km2/px", "valid", f"{st.valid.float().mean():.3f}")
    for i, dte in enumerate(st.dates):
        v = st.valid[i]
        print(dte, "HH", f"{st.x[i, 0][v].mean():.3f}", "HV", f"{st.x[i, 1][v].mean():.3f}")
