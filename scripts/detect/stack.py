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

    @property
    def shape(self):
        return tuple(self.x.shape)

    def lonlat_to_rc(self, lon: float, lat: float) -> tuple[int, int]:
        w, s, e, n = self.bounds
        _, _, h, wd = self.x.shape
        return int((n - lat) / (n - s) * h), int((lon - w) / (e - w) * wd)

    def to(self, device) -> "Stack":
        return Stack(self.area, self.dates, self.x.to(device), self.valid.to(device),
                     self.bounds, self.km2_per_px, self.source)


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


def load_preview_stack(area_id: str, bbox: tuple[float, float, float, float] | None = None) -> Stack:
    """All preview dates of an area, resampled onto one north-up lon/lat grid."""
    area = load_area(area_id)
    w, s, e, n = bbox or area["bbox"]
    folder = ROOT / "data" / "raw" / "previews" / f"T{area['track']}_F{area['frame']}"
    passes = json.loads((folder / "meta.json").read_text())["passes"]

    grid = None
    xs, valids, dates = [], [], []
    for p in passes:
        rgb = np.asarray(Image.open(folder / f"{p['date']}.png").convert("RGB"))
        poly = _polygon(p["wkt"])
        a, b, c, d = _georef(rgb, poly)
        if grid is None:  # target grid: the first date's pixel size
            wd, h = round((e - w) / a), round((s - n) / c)
            lon = w + (torch.arange(wd) + 0.5) * (e - w) / wd
            lat = n - (torch.arange(h) + 0.5) * (n - s) / h
            lat_g, lon_g = torch.meshgrid(lat, lon, indexing="ij")
            mid = math.radians((s + n) / 2)
            km2 = ((e - w) / wd * 111.32 * math.cos(mid)) * ((n - s) / h * 110.57)
            grid = True
        src_h, src_w = rgb.shape[:2]
        col = (lon_g - b) / a - 0.5
        row = (lat_g - d) / c - 0.5
        sample = torch.stack([col / (src_w - 1) * 2 - 1, row / (src_h - 1) * 2 - 1], dim=-1)[None]
        img = torch.from_numpy(rgb[..., :2].copy()).permute(2, 0, 1)[None].float() / 255.0  # R=HH, G=HV
        out = F.grid_sample(img, sample, mode="bilinear", padding_mode="zeros", align_corners=True)[0]
        in_image = (sample.abs() <= 1).all(dim=-1)[0]
        xs.append(out)
        valids.append(_inside(poly, lon_g, lat_g) & in_image)
        dates.append(p["date"])

    return Stack(area, dates, torch.stack(xs), torch.stack(valids), (w, s, e, n), km2, "preview")


if __name__ == "__main__":
    import sys
    st = load_preview_stack(sys.argv[1] if len(sys.argv) > 1 else "tonle-sap")
    print(st.area["id"], st.shape, f"{st.km2_per_px:.4f} km2/px", "valid", f"{st.valid.float().mean():.3f}")
    for i, dte in enumerate(st.dates):
        v = st.valid[i]
        print(dte, "HH", f"{st.x[i, 0][v].mean():.3f}", "HV", f"{st.x[i, 1][v].mean():.3f}")
