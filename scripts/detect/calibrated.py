"""The detector on CALIBRATED NISAR GCOV (linear-power gamma-naught, HH + HV), thresholds in decibels.

    .venv/bin/python -m scripts.detect.calibrated tonle-sap-cal --step all     # look, water, fv, change, res
    .venv/bin/python -m scripts.detect.calibrated tonle-sap-cal --step look
    .venv/bin/python -m scripts.detect.calibrated sevan-cal --step all
    .venv/bin/python -m scripts.detect.calibrated --synthetic                   # self-check on a synthetic scene

Inputs (contract A, written by scripts/ingest/remote_extract.py): data/processed/<area>-cal/gamma0/{hh,hv}/<date>.tif
(float32 linear power, NaN no-data, one UTM grid), nlooks/{hh,hv}/<date>.tif (valid 20 m samples per cell).
Outputs: data/processed/<area>-cal/det/products.pt, docs/eda/calibrated_det/*.png + results.json,
docs/eda/calibrated_det/gamma0_knobs.json. Write-up: docs/eda/calibrated.md.

Nothing here edits ops / floodveg / stats_change / tune: they are imported and called with dB knobs. Two
pieces hard-code preview units and are re-implemented here (see `fv_db` and `reference_on_cal_grid`).
All numerics in PyTorch on the CPU (one date at a time where a module would copy the whole stack).
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import gc
import json
import math
import pathlib
import subprocess
import time

import numpy as np
import torch
import torch.nn.functional as F

from scripts.detect import floodveg, ops, stats_change as sc
from scripts.detect.stack import ROOT

OUT = ROOT / "docs/eda/calibrated_det"
LN10 = math.log(10.0)

# ======================================================================================= knobs
# Calibrated knob set = ops.KNOBS["gamma0"] with the changes below. Values marked "synthetic" were
# fixed on the synthetic scene (tests/test_calibrated.py); the water thresholds are overwritten by the
# tuning on real data (step "water") and written to docs/eda/calibrated_det/gamma0_knobs.json.
CAL_KNOBS = dict(ops.KNOBS["gamma0"])
CAL_KNOBS.update({
    "box": 3,              # 3x3 at 80 m = 240 m (Quegan multi-temporal on top); 5 and 7 blur shores and rings more
    "open_k": 0,           # opening 3 deletes every water body narrower than 3 px (240 m rivers): off
    "min_blob_px": 9,      # 9 px = 0.06 km2 at 80 m (preview used 4 px = 0.13 km2)
    "edge_erode_px": 2,
    "fv_hh_rise": 3.0,     # simple ops rule kept for comparison; the main rule is fv_db (self-calibrated)
    "fv_hv_rise_max": 1.5,
    "change_step_min": 3.0,
    "refine_px": 2,        # shoreline refinement band (px) on the unfiltered HH, see refine_shore
    "untested": True,
})

# Flooded vegetation in dB: floodveg.FV_KNOBS translated. Brightness floors -> dB floors; distances scale
# with the pixel size (preview 180 m); tau = softness of the sigmoid in dB.
FV_DB = {
    "db_rise_min": 1.0,    # dB: HH-HV must rise by > max(1 dB, 3 sigma of dry land)
    "db_k_sigma": 3.0,
    "db_hh_drop_max": 1.0,  # dB: HH may not fall by more than 1 dB (double bounce keeps HH bright)
    "db_hh_min": -14.0,    # dB: HH itself not dark (a partly-water pixel is darker)
    "abs_margin": 0.5,     # dB above the 95 % of dry land with the same HV
    "sub_drop": 1.5,       # dB: both channels fall ('going under')
    "tau": 0.3,            # dB
    "town_std_max": 0.5,   # dB temporal std of a town
}
FV_DIST_180 = {"null_dist_px": 20, "near_px": 10, "decay_px": 5.0, "max_px": 30, "abs_dist_px": 2,
               "sub_dist_px": 1, "min_blob_px": 4}
HV_BINS_DB = (-36.0, -6.0)   # cond_quantile bins HV in 0..1 for previews; here -36..-6 dB


def fv_knobs(pixel_m: float) -> dict:
    s = 180.0 / pixel_m
    k = dict(FV_DB)
    for name, v in FV_DIST_180.items():
        k[name] = max(1, int(round(v * s))) if name != "decay_px" else v * s
    k["min_blob_px"] = max(4, int(round(FV_DIST_180["min_blob_px"] * s * s)))
    k["abs_dist_px"] = max(2, int(round(2 * s)))   # 'on the open-water edge' = within ~360 m
    k["sub_dist_px"] = max(1, int(round(1 * s)))
    return k


def knobs_for(pixel_m: float, **over) -> dict:
    k = dict(CAL_KNOBS)
    k.update(over)
    return k


# ======================================================================================= units
def db(x: torch.Tensor) -> torch.Tensor:
    return 10.0 * torch.log10(x.clamp_min(1e-10))


def lin(d) -> torch.Tensor:
    return torch.pow(10.0, torch.as_tensor(d, dtype=torch.float64) / 10.0)


def db_bias(L: float) -> float:
    """E[10 log10(I/mu)] for gamma(L) speckle: (10/ln10) (psi(L) - ln L) (negative). Why we filter in power."""
    return float(10.0 / LN10 * (torch.special.digamma(torch.tensor(float(L), dtype=torch.float64)) - math.log(L)))


def db_std(L: float) -> float:
    """Std of 10 log10 I for gamma(L) speckle: (10/ln10) sqrt(psi1(L))."""
    return float(10.0 / LN10 * math.sqrt(float(torch.special.polygamma(1, torch.tensor(float(L), dtype=torch.float64)))))


# ======================================================================================= synthetic scene
# Class means (HH, HV) in dB, science brief (literature-typical, [T]); used ONLY for the synthetic scene.
CLASSES = {
    "water": (-23.0, -29.0), "rough_water": (-14.0, -25.0), "forest": (-7.5, -13.5),
    "flooded_forest": (-2.5, -13.0), "cropland": (-11.0, -17.0), "bare": (-14.0, -22.0),
    "urban": (-3.0, -11.0), "shrub": (-9.5, -16.0),
}


def synth_scene(H=240, W=240, T=8, enl=16.0, seed=0, texture_db=0.5):
    """Linear-power gamma-naught stack with multiplicative gamma(enl) speckle (mean 1).

    Layout: forest/shrub background with a static 0.5 dB log-normal texture; a lake that grows (radius
    22 -> 50 px); a forest ring 52-62 px from the centre floods from date 3 (HH +5 dB, HV +0.5 dB: double
    bounce) and goes back at the last date; a cropland block harvested at date 4 (cropland -> bare); a
    town; rough water on half the lake on date 2; NaN margins (left 5 columns + a slanted bottom edge).
    Returns x [T,2,H,W] float32, valid [T,H,W], truth dict.
    """
    g = torch.Generator().manual_seed(seed)
    rr, cc = torch.meshgrid(torch.arange(H, dtype=torch.float32), torch.arange(W, dtype=torch.float32), indexing="ij")
    cy, cx = H * 0.36, W * 0.36
    dist = torch.hypot(rr - cy, cc - cx)
    base = torch.zeros(H, W, dtype=torch.long)  # 0 forest, 1 shrub
    base[(rr + cc) % 40 < 14] = 1
    tex = torch.exp(torch.randn(2, H, W, generator=g) * texture_db * LN10 / 10)  # static texture (not speckle)
    radii = torch.linspace(22, 50, T)
    water = torch.stack([dist < r for r in radii])
    ring = (dist >= 52) & (dist < 62)
    fv = torch.zeros(T, H, W, dtype=torch.bool)
    for t in range(3, T - 1):
        fv[t] = ring & ~water[t]
    field = (rr > H * 0.80) & (cc > W * 0.55) & (cc < W * 0.80) & (rr < H * 0.92)
    town = (rr < H * 0.10) & (cc > W * 0.85)
    hv_mean = torch.empty(T, 2, H, W)
    cls_name = {0: "forest", 1: "shrub"}
    for t in range(T):
        m = torch.empty(2, H, W)
        for k, nm in cls_name.items():
            for c in range(2):
                m[c][base == k] = CLASSES[nm][c]
        for c in range(2):
            m[c][field] = CLASSES["cropland" if t < 4 else "bare"][c]
            m[c][town] = CLASSES["urban"][c]
            m[c][fv[t]] = CLASSES["flooded_forest"][c]
            m[c][water[t]] = CLASSES["water"][c]
            if t == 2:
                m[c][water[t] & (cc > cx)] = CLASSES["rough_water"][c]
        hv_mean[t] = m
    mu = torch.pow(10.0, hv_mean / 10)
    land = ~water  # texture only on land
    mu = torch.where(land[:, None], mu * tex[None], mu)
    speck = torch._standard_gamma(torch.full((T, 2, H, W), float(enl)), generator=g) / enl
    x = (mu * speck).float()
    nod = torch.zeros(H, W, dtype=torch.bool)
    nod[:, :5] = True
    nod |= rr > H - 3 - 0.08 * cc
    x[:, :, nod] = float("nan")
    valid = torch.isfinite(x).all(1)
    changed = torch.zeros(H, W, dtype=torch.bool)
    for t in range(1, T):
        changed |= (hv_mean[t] != hv_mean[t - 1]).any(0)
    changed &= ~nod
    truth = {"water": water & ~nod, "fv": fv & ~nod, "changed": changed, "field": field & ~nod, "ring": ring & ~nod,
             "town": town, "mean_db": hv_mean, "nodata": nod, "enl": enl}
    return x, valid, truth


# ======================================================================================= loading
class Grid:
    def __init__(self, crs, transform, h, w):
        self.crs, self.transform, self.h, self.w = crs, transform, h, w

    @property
    def pixel_m(self):
        return abs(self.transform.a)

    @property
    def km2(self):
        return abs(self.transform.a * self.transform.e) / 1e6

    def lonlat_to_rc(self, lon, lat):
        from rasterio.warp import transform as wt
        (x,), (y,) = wt("EPSG:4326", self.crs, [lon], [lat])
        c, r = ~self.transform * (x, y)
        return int(r), int(c)

    def dist_m(self, lon, lat):
        from rasterio.warp import transform as wt
        (sx,), (sy,) = wt("EPSG:4326", self.crs, [lon], [lat])
        t = self.transform
        yy = t.f + (torch.arange(self.h, dtype=torch.float64) + 0.5) * t.e
        xx = t.c + (torch.arange(self.w, dtype=torch.float64) + 0.5) * t.a
        return torch.hypot(yy[:, None] - sy, xx[None] - sx)

    def bounds_lonlat(self):
        from rasterio.warp import transform_bounds
        t = self.transform
        return transform_bounds(self.crs, "EPSG:4326", t.c, t.f + self.h * t.e, t.c + self.w * t.a, t.f)


def cal_dir(area: str) -> pathlib.Path:
    return ROOT / "data/processed" / area


def cal_dates(area: str) -> list[str]:
    b = cal_dir(area) / "gamma0"
    hh = sorted(p.stem for p in (b / "hh").glob("*.tif"))
    hv = set(p.stem for p in (b / "hv").glob("*.tif"))
    return [d for d in hh if d in hv]


def load_cal(area: str, window=None, dates=None, with_nlooks=False):
    """Contract-A stack. window = (row0, col0, h, w) or None. Returns dates, x [T,2,h,w] float32 linear power
    (NaN no-data; zero/negative -> NaN), valid [T,h,w], Grid (of the window), nlooks [T,2,h,w] or None."""
    import rasterio
    from rasterio.windows import Window
    dates = dates or cal_dates(area)
    b = cal_dir(area) / "gamma0"
    grid, xs, ns = None, [], []
    for d in dates:
        pair, npair = [], []
        for pol in ("hh", "hv"):
            with rasterio.open(b / pol / f"{d}.tif") as r:
                win = Window(window[1], window[0], window[3], window[2]) if window else None
                a = r.read(1, window=win).astype(np.float32)
                if grid is None:
                    tr = r.window_transform(win) if win else r.transform
                    grid = Grid(r.crs, tr, a.shape[0], a.shape[1])
                    ref_full = (r.crs, tuple(r.transform), r.height, r.width)
                assert (r.crs, tuple(r.transform), r.height, r.width) == ref_full, f"{pol}/{d}: other grid"
                pair.append(a)
            if with_nlooks:
                p = cal_dir(area) / "nlooks" / pol / f"{d}.tif"
                if p.exists():
                    with rasterio.open(p) as r:
                        npair.append(r.read(1, window=win).astype(np.float32))
        xs.append(torch.from_numpy(np.stack(pair)))
        if with_nlooks and len(npair) == 2:
            ns.append(torch.from_numpy(np.stack(npair)))
    x = torch.stack(xs)
    x[~(x > 0)] = float("nan")
    valid = torch.isfinite(x).all(1)
    nl = torch.stack(ns) if ns and len(ns) == len(dates) else None
    return dates, x, valid, grid, nl


def mem_free_pct() -> int:
    try:
        out = subprocess.run(["memory_pressure"], capture_output=True, text=True, timeout=20).stdout
        return int(out.strip().splitlines()[-1].split(":")[-1].strip().rstrip("%"))
    except Exception:  # noqa: BLE001
        return -1


def check_mem(label=""):
    gc.collect()
    p = mem_free_pct()
    print(f"  [mem] {label}: {p}% free", flush=True)
    if 0 <= p < 15:
        raise MemoryError(f"memory pressure too high ({p}% free) before {label}")
    return p


# ======================================================================================= regridding
def reproject_to(arr: np.ndarray, src_transform, src_crs, grid: Grid, resampling="average", src_nodata=None,
                 dst_dtype=np.float32):
    """Any raster onto the calibrated grid (rasterio.warp.reproject)."""
    from rasterio.enums import Resampling
    from rasterio.warp import reproject
    dst = np.full((grid.h, grid.w), np.nan if np.dtype(dst_dtype).kind == "f" else 0, dst_dtype)
    reproject(arr, dst, src_transform=src_transform, src_crs=src_crs, dst_transform=grid.transform,
              dst_crs=grid.crs, resampling=getattr(Resampling, resampling), src_nodata=src_nodata,
              dst_nodata=np.nan if np.dtype(dst_dtype).kind == "f" else 0)
    return dst


def preview_transform(bounds, H, W):
    from rasterio.transform import from_bounds
    w, s, e, n = bounds
    return from_bounds(w, s, e, n, W, H)


def from_preview(t: torch.Tensor, bounds, grid: Grid, resampling="nearest") -> torch.Tensor:
    """[H,W] or [T,H,W] tensor on the preview lon/lat grid -> calibrated grid (float; NaN outside)."""
    t = t.float()
    squeeze = t.dim() == 2
    t3 = t[None] if squeeze else t
    tr = preview_transform(bounds, t3.shape[1], t3.shape[2])
    out = [torch.from_numpy(reproject_to(a.numpy(), tr, "EPSG:4326", grid, resampling)) for a in t3]
    o = torch.stack(out)
    return o[0] if squeeze else o


def reference_on_cal_grid(src_area: str, grid: Grid) -> dict:
    """validate.reference_on_grid for a projected grid: the same JRC GSW fractions, area-averaged from the
    cached 30 m clips (data/external/gsw/<src_area>_*.tif) with rasterio 'average' resampling."""
    import rasterio
    ref = {}
    gdir = ROOT / "data/external/gsw"

    def frac(product, fn):
        with rasterio.open(gdir / f"{src_area}_{product}.tif") as r:
            a = r.read(1)
            tr, crs = r.transform, r.crs
        ok = a != 255
        vals = {}
        vfrac = reproject_to(ok.astype(np.float32), tr, crs, grid)
        for k, f in fn(a).items():
            num = reproject_to((f & ok).astype(np.float32) if f.dtype == bool else np.where(ok, f, 0).astype(np.float32),
                               tr, crs, grid)
            vals[k] = torch.from_numpy(np.nan_to_num(num / np.maximum(vfrac, 1e-6), nan=0.0)).float()
        return torch.from_numpy(np.nan_to_num(vfrac, nan=0.0)).float(), vals
    vf, v = frac("occurrence", lambda a: {"occ_mean": a.astype(np.float32), "occ_ge75_frac": a >= 75,
                                         "occ_ge90_frac": a >= 90, "occ_ge95_frac": a >= 95, "occ_any_frac": a > 0})
    ref["valid_frac"] = vf
    ref.update(v)
    _, v = frac("extent", lambda a: {"ext_frac": a == 1})
    ref.update(v)
    _, v = frac("seasonality", lambda a: {"seas12_frac": a == 12})
    ref.update(v)
    return ref


# ======================================================================================= filtering
def filtered_db(x: torch.Tensor, valid2: torch.Tensor, box: int, quegan: bool = True,
                dtype=torch.float32) -> torch.Tensor:
    """Speckle filter in LINEAR POWER (Quegan multi-temporal or box), then dB. x [T,2,H,W] (NaN ok),
    valid2 [H,W] common footprint. Done per channel to keep the peak memory at ~4 copies of one channel."""
    T, C, H, W = x.shape
    v = valid2[None].expand(T, H, W)
    out = torch.empty(T, C, H, W, dtype=dtype)
    for c in range(C):
        xc = torch.nan_to_num(x[:, c:c + 1], nan=0.0) * valid2
        f = ops.quegan_filter(xc, v, box) if quegan else ops.box_mean(xc, v, box)
        out[:, c] = (db(f[:, 0]) * valid2).to(dtype)
        del f, xc
    return out


def footprint(valid: torch.Tensor, kn: dict) -> torch.Tensor:
    """ops.run_pipeline footprint rules: common to all dates, eroded by edge_erode_px. -> [H,W]."""
    v = valid.all(0)
    if kn.get("edge_erode_px", 0) > 0:
        v = ops.erode(v[None], 2 * kn["edge_erode_px"] + 1)[0]
    return v


# ======================================================================================= detection rules (dB)
def water_db(L: torch.Tensor, valid2: torch.Tensor, kn: dict, water_ok=None) -> torch.Tensor:
    """Open water per date, dB: hysteresis on s = (1-hv_w) HH + hv_w HV (strong/weak), HV < water_hv_max,
    blobs >= min_blob_px. Exactly ops.water_masks with open_k = 0 (tune.water_fast does the labelling)."""
    from scripts.detect.tune import water_fast
    w = water_fast(L.float(), valid2, kn["water_hh_strong"], kn["water_hh_weak"], kn["water_hv_max"],
                   kn.get("hv_w", 0.0), kn["min_blob_px"], None)
    if water_ok is not None:
        w = w & water_ok[None]
    return w


def refine_shore(water: torch.Tensor, hh_raw_db: torch.Tensor, valid2: torch.Tensor, thr: float, px: int = 2):
    """Undo the shoreline shrink of power-domain filtering. A k x k mean in POWER is dominated by a bright
    neighbour (forest is ~35x brighter than calm water), so the filtered map loses ~k//2 px of every shore.
    Inside a px-wide band outside the filtered water, a pixel joins if its UNFILTERED HH (dB) is below thr
    and it is 8-connected (through such pixels) to the water. water [T,H,W], hh_raw_db [T,H,W]."""
    if px <= 0:
        return water
    band = ops.dilate(water, 2 * px + 1) & valid2[None]
    allowed = water | (band & (hh_raw_db < thr))
    return ops.reconstruct(water, allowed, max_iter=4 * px + 8)


@contextlib.contextmanager
def _db_cond_quantile(lo=HV_BINS_DB[0], hi=HV_BINS_DB[1]):
    """floodveg.cond_quantile bins HV assuming 0..1 brightness; rebind it to bin dB HV over [lo, hi]."""
    orig = floodveg.cond_quantile

    def cq(df, hv, null, q, nb=20, sub=4):
        return orig(df, ((hv - lo) / (hi - lo)).clamp(0, 0.999999), null, q, nb, sub)
    floodveg.cond_quantile = cq
    try:
        yield
    finally:
        floodveg.cond_quantile = orig


def fv_db(L: torch.Tensor, valid: torch.Tensor, water: torch.Tensor, pixel_m: float, water_ok=None, **over) -> dict:
    """Rebuilt flooded-vegetation rule (floodveg.detect_flooded_veg) with dB floors and pixel-scaled distances.
    L [T,2,H,W] dB, valid [T,H,W], water [T,H,W]."""
    kn = fv_knobs(pixel_m)
    kn.update(over)
    with _db_cond_quantile():
        return floodveg.detect_flooded_veg(L, valid, water, kn=kn,
                                           terrain={"water_possible": water_ok} if water_ok is not None else None)


def ratio_db(L: torch.Tensor) -> torch.Tensor:
    """HH/HV ratio in dB = HH_dB - HV_dB (a physical quantity on calibrated data)."""
    return L[:, 0] - L[:, 1]


# ======================================================================================= statistics
def enl_estimates(I: torch.Tensor, mask2: torch.Tensor, k: int = 7) -> dict:
    """Spatial ENL (k x k windows, quantile-matched, per date pooled) and temporal ENL on mask2 [H,W]; I [T,H,W]."""
    T = I.shape[0]
    m = mask2[None].expand(T, -1, -1)
    return {"spatial": sc.enl_spatial(torch.nan_to_num(I.double(), nan=1.0), m, k=k),
            "temporal": sc.enl_temporal(torch.nan_to_num(I.double(), nan=1.0), mask2)}


def omnibus_strips(I: torch.Tensor, n, valid2: torch.Tensor, rows: int = 256):
    """Omnibus p and Z over all dates, in row strips. I [T,C,H,W] linear power. -> p, Z [H,W] float64."""
    T, C, H, W = I.shape
    p = torch.ones(H, W, dtype=torch.float64)
    Z = torch.zeros(H, W, dtype=torch.float64)
    for r0 in range(0, H, rows):
        sl = I[:, :, r0:r0 + rows].to(torch.float64).clamp_min(1e-10)
        pp, zz = sc.omnibus_pvalue(torch.nan_to_num(sl, nan=1.0), n)
        p[r0:r0 + rows], Z[r0:r0 + rows] = pp, zz
    p[~valid2] = 1.0
    Z[~valid2] = 0.0
    return p, Z


def sequential_strips(I: torch.Tensor, n, gate0: torch.Tensor, alpha: float, rows: int = 192) -> dict:
    T, C, H, W = I.shape
    keys = ("n_changes", "first", "largest", "direction", "zmax")
    out = None
    for r0 in range(0, H, rows):
        sl = torch.nan_to_num(I[:, :, r0:r0 + rows].to(torch.float64).clamp_min(1e-10), nan=1.0)
        s = sc.sequential_changes(sl, n, gate0[r0:r0 + rows], alpha)
        if out is None:
            out = {k: torch.empty((H, W), dtype=s[k].dtype) for k in keys}
            out["change"] = torch.zeros((T, H, W), dtype=torch.bool)
        for k in keys:
            out[k][r0:r0 + rows] = s[k]
        out["change"][:, r0:r0 + rows] = s["change"]
        del s, sl
    return out


def fa_rate(p: torch.Tensor, mask: torch.Tensor, levels=(0.1, 0.05, 0.01, 0.001)) -> dict:
    v = p[mask]
    return {str(a): float((v < a).double().mean()) if v.numel() else float("nan") for a in levels}


# ======================================================================================= small helpers
def _r(x, nd=3):
    if x is None:
        return None
    if isinstance(x, (list, tuple)):
        return [_r(v, nd) for v in x]
    if isinstance(x, dict):
        return {k: _r(v, nd) for k, v in x.items()}
    if torch.is_tensor(x):
        x = x.item() if x.numel() == 1 else x.tolist()
        return _r(x, nd)
    if isinstance(x, float):
        return round(x, nd) if math.isfinite(x) else None
    return x


def quant(v: torch.Tensor, qs) -> list:
    v = v.flatten().float()
    v = v[torch.isfinite(v)]
    if v.numel() == 0:
        return [None] * len(qs)
    v = v[:: max(1, v.numel() // 4_000_000)]
    return torch.quantile(v, torch.tensor(qs, dtype=v.dtype)).tolist()


def spearman(a, b):
    from scripts.detect.tune import avg_rank, wpearson
    a, b = torch.tensor(a, dtype=torch.float64), torch.tensor(b, dtype=torch.float64)
    return wpearson(avg_rank(a), avg_rank(b)), wpearson(a, b)


def results_path():
    return OUT / "results.json"


def load_results() -> dict:
    p = results_path()
    return json.loads(p.read_text()) if p.exists() else {}


def save_results(area: str, key: str, val):
    OUT.mkdir(parents=True, exist_ok=True)
    R = load_results()
    R.setdefault(area, {})[key] = _r(val, 4)
    R["generated"] = dt.datetime.now().isoformat(timespec="seconds")
    results_path().write_text(json.dumps(R, indent=1, ensure_ascii=False))


def savefig(fig, name):
    import matplotlib.pyplot as plt
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    for dpi in (110, 90, 72, 60):
        fig.savefig(p, dpi=dpi, bbox_inches="tight")
        if p.stat().st_size < 390_000:
            break
    plt.close(fig)
    print("  figure", p.relative_to(ROOT), f"{p.stat().st_size / 1e3:.0f} kB")
    return str(p.relative_to(ROOT))


# ======================================================================================= the chain
def detect(x: torch.Tensor, valid: torch.Tensor, kn: dict, pixel_m: float, water_ok=None, fv=True,
           verbose=False) -> dict:
    """Calibrated chain (memory-aware stand-in for ops.run_pipeline on large grids): common footprint ->
    Quegan filter in power -> dB -> water (hysteresis, dB) -> shoreline refinement on unfiltered HH ->
    flooded vegetation (fv_db). x [T,2,H,W] linear power (NaN no-data)."""
    t0 = time.time()
    v2 = footprint(valid, kn)
    L = filtered_db(x, v2, kn["box"], kn["quegan"])
    if verbose:
        print(f"  filtered {tuple(L.shape)} in {time.time() - t0:.0f} s", flush=True)
    w = water_db(L, v2, kn, water_ok)
    if kn.get("refine_px", 0):
        raw = torch.stack([db(torch.nan_to_num(x[t, 0], nan=1e-10)) for t in range(x.shape[0])])
        w = refine_shore(w, raw, v2, kn.get("refine_thr") or kn["water_hh_weak"], kn["refine_px"])
        if water_ok is not None:
            w = w & water_ok[None]
        del raw
    if verbose:
        print(f"  water done {time.time() - t0:.0f} s", flush=True)
    out = {"L": L, "valid2": v2, "water": w}
    if fv:
        T = x.shape[0]
        fvp = fv_db(L, v2[None].expand(T, -1, -1), w, pixel_m, water_ok)
        out.update({k: fvp[k] for k in ("flooded_veg", "fv_state", "fv_score", "noise_sigma_df", "town",
                                        "first_fv_date")})
        if verbose:
            print(f"  flooded vegetation done {time.time() - t0:.0f} s", flush=True)
    return out


# ======================================================================================= synthetic self-check
def _acc(w, tw, dates=None):
    if dates is not None:
        w, tw = w[dates], tw[dates]
    return {"iou": float((w & tw).sum() / (w | tw).sum().clamp_min(1)),
            "recall": float((w & tw).sum() / tw.sum().clamp_min(1)),
            "precision": float((w & tw).sum() / w.sum().clamp_min(1))}


def synthetic_check(enls=(4, 16, 64), seed=0, verbose=True) -> dict:
    """The calibrated chain on synthetic scenes at several ENL. Water accuracy excludes the rough-water date
    (date 2, reported separately). Also: the UNTESTED ops.KNOBS['gamma0'] through ops.run_pipeline, the
    tuned knobs through ops.run_pipeline (no shoreline refinement), and the gamma-null calibration."""
    res = {}
    for enl in enls:
        x, valid, tr = synth_scene(enl=float(enl), seed=seed)
        T = x.shape[0]
        calm = [t for t in range(T) if t != 2]
        r = {}
        for name, kn in (("ops_untested", dict(ops.KNOBS["gamma0"])), ("ops_cal", knobs_for(80.0))):
            o = ops.run_pipeline(x, valid, "gamma0", kn=kn, dev=torch.device("cpu"))
            v = o["valid"]
            f = o["flooded_veg"] & v
            r[name] = {"water": _acc(o["water"] & v, tr["water"] & v, calm),
                       "fv_simple": _acc(f, tr["fv"] & v)}
        kn = knobs_for(80.0)
        o = detect(x, valid, kn, 80.0)
        v = o["valid2"][None].expand(T, -1, -1)
        r["cal"] = {"water": _acc(o["water"] & v, tr["water"] & v, calm),
                    "water_rough_date_recall": _acc(o["water"] & v, tr["water"] & v, [2])["recall"],
                    "fv": _acc(o["flooded_veg"] & v, tr["fv"] & v),
                    "fv_noise_sigma_db": float(o["noise_sigma_df"][-1])}
        # statistics on the raw (unfiltered) intensities with the TRUE number of looks
        I = torch.nan_to_num(x.double(), nan=1.0)
        v2 = valid.all(0)
        p, Z = sc.omnibus_pvalue(I, [float(enl)] * 2)
        stable = v2 & ~tr["changed"]
        rej, pstar = sc.bh(p, v2, 0.01)
        e = enl_estimates(x[:, 0], stable & ~tr["water"].any(0) & ~tr["town"], k=7)
        ew = enl_estimates(x[:, 0], tr["water"].all(0) & v2, k=7)
        r["stats"] = {"fa_stable": fa_rate(p, stable), "bh_power": float(rej[v2 & tr["changed"]].double().mean()),
                      "bh_fdr": float((rej & ~tr["changed"]).sum() / rej.sum().clamp_min(1)),
                      "enl_land_spatial": e["spatial"], "enl_land_temporal": e["temporal"],
                      "enl_water_spatial": ew["spatial"], "enl_water_temporal": ew["temporal"]}
        r["db_bias_of_averaging_in_db"] = db_bias(enl)
        r["db_std_single_pixel"] = db_std(enl)
        res[str(enl)] = r
        if verbose:
            print(f"ENL {enl}: {json.dumps(_r(r, 4))}", flush=True)
    return res


# ======================================================================================= real data: context
def src_area(area: str) -> str:
    return area[:-4] if area.endswith("-cal") else area


def det_dir(area: str) -> pathlib.Path:
    d = cal_dir(area) / "det"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cached(area: str, name: str, fn):
    p = det_dir(area) / f"{name}.pt"
    if p.exists():
        return torch.load(p, weights_only=False)
    v = fn()
    torch.save(v, p)
    return v


class Area:
    """Everything about one calibrated area: stack, grid, JRC labels and anchors on the calibrated grid."""

    def __init__(self, area: str, window=None, dates=None):
        self.area, self.src = area, src_area(area)
        check_mem("load")
        self.dates, self.x, self.valid_in, self.grid, self.nlooks = load_cal(area, window, dates, with_nlooks=True)
        self.T = len(self.dates)
        self.km2 = self.grid.km2
        self.pixel_m = self.grid.pixel_m
        self.kn = knobs_for(self.pixel_m)
        self.v2 = footprint(self.valid_in, self.kn)
        tag = "" if window is None else "_w" + "_".join(map(str, window))
        self.ref = cached(area, "ref" + tag, lambda: reference_on_cal_grid(self.src, self.grid))
        from scripts.detect.tune import build_labels, LABEL_RULE
        self.lab = build_labels(self.ref, self.v2, **LABEL_RULE)
        self.water_ok = self._terrain(tag)
        self.prev_dom = self._preview_domain(tag)
        story = ROOT / "areas" / self.src / "story.json"
        self.spots = json.loads(story.read_text()).get("spots", []) if story.exists() else []
        print(f"{area}: {self.T} dates {self.dates[0]}..{self.dates[-1]}, grid {self.grid.h}x{self.grid.w} "
              f"@ {self.pixel_m:.0f} m, footprint {float(self.v2.sum()) * self.km2:,.0f} km2, "
              f"labels perm {int(self.lab['perm'].sum())} never {int(self.lab['never'].sum())} px", flush=True)

    def _preview_products(self):
        p = ROOT / "data/processed" / self.src / "math/products.pt"
        return torch.load(p, weights_only=False, map_location="cpu") if p.exists() else None

    def _preview_domain(self, tag):
        def f():
            d = self._preview_products()
            if d is None:
                return None
            return from_preview(d["valid"].all(0).float(), d["bounds"], self.grid) > 0.5
        return cached(self.area, "prevdom" + tag, f)

    def _terrain(self, tag):
        def f():
            p = ROOT / "data/processed" / self.src / "terrain.pt"
            if not p.exists():
                return None
            t = torch.load(p, weights_only=False, map_location="cpu")
            return from_preview(t["water_possible"].float(), t["bounds"], self.grid) > 0.5
        m = cached(self.area, "terrain" + tag, f)
        if m is None:
            return None
        return m | ~torch.isfinite(m.float())  # outside the preview grid: no constraint

    def spot_mask(self, sid, radius=None):
        for s in self.spots:
            if s["id"] == sid:
                return self.grid.dist_m(*s["lngLat"]) <= (radius or s.get("radius_m", 1000))
        return None

    def classes(self, Ldb_med=None):
        """Anchor classes on the calibrated grid (masks [H,W]):
        lake core  = JRC permanent water (eroded 2 px more);
        forest     = JRC never-water land in the top 15 % of dry-season HV (bright volume scatterers), > 10 px from ever-water;
        open land  = JRC never-water land in the 15-45 % band of dry-season HV (cropland, grass, bare);
        ring       = Prek Toal flooded-forest spot (story), for Tonle Sap;
        floodplain = JRC occurrence 1-50 % (seasonally flooded)."""
        v2 = self.v2
        hv0 = db(self.x[0, 1]) if Ldb_med is None else Ldb_med
        never = self.lab["never"]
        far = never & ~ops.dilate((self.ref["occ_any_frac"] > 0)[None], 21)[0]
        q = quant(hv0[far & v2 & torch.isfinite(hv0)], [0.15, 0.45, 0.85])
        out = {"lake core": ops.erode(self.lab["perm"][None], 5)[0] & v2,
               "forest": far & v2 & (hv0 >= q[2]),
               "open land": far & v2 & (hv0 >= q[0]) & (hv0 < q[1]),
               "floodplain": v2 & (self.ref["occ_mean"] >= 1) & (self.ref["occ_mean"] < 50)}
        ring = self.spot_mask("prek-toal", 3000)
        if ring is not None:
            out["flooded-forest ring (Prek Toal)"] = ring & v2
        return out


# ======================================================================================= step: look
def step_look(A: Area) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    T, H, W = A.T, A.grid.h, A.grid.w
    v2 = A.v2
    hvmed = torch.stack([db(A.x[t, 1]) for t in range(min(T, 2))]).nanmedian(0).values  # dry-season HV
    C = A.classes(hvmed)
    res = {"dates": A.dates, "grid": {"crs": str(A.grid.crs), "pixel_m": A.pixel_m, "H": H, "W": W,
                                      "transform": list(A.grid.transform)[:6]},
           "footprint_km2": float(v2.sum()) * A.km2, "class_px": {k: int(m.sum()) for k, m in C.items()}}
    # ---- per-date levels: median of dB, and dB of the mean power, per class
    lv = {k: {"hh_median_db": [], "hv_median_db": [], "hh_meanpower_db": [], "hv_meanpower_db": [],
              "hh_p5_p95": [], "hv_p5_p95": []} for k in C}
    for t in range(T):
        d = db(A.x[t])
        for k, m in C.items():
            mm = m & A.valid_in[t]
            for c, nm in ((0, "hh"), (1, "hv")):
                v = d[c][mm]
                lv[k][f"{nm}_median_db"].append(float(v.median()) if v.numel() else None)
                lv[k][f"{nm}_meanpower_db"].append(float(db(A.x[t, c][mm].double().mean())) if v.numel() else None)
                lv[k][f"{nm}_p5_p95"].append(quant(v, [0.05, 0.95]))
    res["levels"] = lv
    spots = {}
    for s in A.spots:
        m = A.spot_mask(s["id"]) & v2
        spots[s["id"]] = {"hh_db": [float(db(A.x[t, 0][m].double().mean())) for t in range(T)],
                          "hv_db": [float(db(A.x[t, 1][m].double().mean())) for t in range(T)]}
    res["spots"] = spots
    # ---- noise floor: lowest stable values over calm water (lake core) and over the whole scene
    lake = C["lake core"]
    res["noise_floor"] = {
        "lake_hv_p1_p5_p50_per_date": [quant(db(A.x[t, 1])[lake & A.valid_in[t]], [0.01, 0.05, 0.5]) for t in range(T)],
        "lake_hh_p1_p5_p50_per_date": [quant(db(A.x[t, 0])[lake & A.valid_in[t]], [0.01, 0.05, 0.5]) for t in range(T)],
        "scene_hv_p01_p1": [quant(db(A.x[t, 1])[A.valid_in[t]], [0.001, 0.01]) for t in range(T)],
        "scene_hh_p01_p1": [quant(db(A.x[t, 0])[A.valid_in[t]], [0.001, 0.01]) for t in range(T)]}
    # 5x5-averaged HV minimum over the lake (speckle averaged away): the noise-equivalent level
    nel = []
    for t in range(T):
        f = ops.box_mean(torch.nan_to_num(A.x[t:t + 1, 1:2], nan=0.0), (lake & A.valid_in[t])[None], 5)[0, 0]
        ok = ops.erode((lake & A.valid_in[t])[None], 5)[0]
        nel.append({"hv": quant(db(f[ok]), [0.01, 0.1, 0.5]),
                    "hh": quant(db(ops.box_mean(torch.nan_to_num(A.x[t:t + 1, 0:1], nan=0.0),
                                                (lake & A.valid_in[t])[None], 5)[0, 0][ok]), [0.01, 0.1, 0.5])})
    res["noise_floor"]["lake_5x5_p1_p10_p50"] = nel
    # ---- incidence-angle trend: forest brightness vs column (range direction for a near-polar ascending pass)
    forest = C["forest"]
    cols = torch.arange(W)[None].expand(H, W)
    bins = torch.linspace(0, W, 21).long()
    trend = {"col_km": [], "hh": [], "hv": [], "n": []}
    d0 = db(A.x[0])
    for a, b in zip(bins[:-1], bins[1:]):
        m = forest & (cols >= a) & (cols < b) & A.valid_in[0]
        if m.sum() < 200:
            continue
        trend["col_km"].append(float((a + b) / 2) * A.pixel_m / 1000)
        trend["hh"].append(float(d0[0][m].median()))
        trend["hv"].append(float(d0[1][m].median()))
        trend["n"].append(int(m.sum()))
    if len(trend["col_km"]) >= 3:
        for c in ("hh", "hv"):
            xk = torch.tensor(trend["col_km"], dtype=torch.float64)
            yk = torch.tensor(trend[c], dtype=torch.float64)
            sl = float(((xk - xk.mean()) * (yk - yk.mean())).sum() / ((xk - xk.mean()) ** 2).sum())
            trend[f"{c}_slope_db_per_100km"] = sl * 100
    res["incidence_trend_forest"] = trend
    # ---- ENL: spatial on lake core and forest (k=7), vs the 20 m sample count
    enl = {"per_date_spatial_lake_hh": [], "per_date_spatial_lake_hv": [], "per_date_spatial_forest_hh": [],
           "per_date_spatial_forest_hv": []}
    for t in range(T):
        for k, m in (("lake", lake), ("forest", forest)):
            for c, nm in ((0, "hh"), (1, "hv")):
                mm = (m & A.valid_in[t])[None]
                enl[f"per_date_spatial_{k}_{nm}"].append(
                    sc.enl_spatial(torch.nan_to_num(A.x[t:t + 1, c].double(), nan=1.0), mm, k=7))
    if T >= 4:
        for k, m in (("lake", lake), ("forest", forest)):
            for c, nm in ((0, "hh"), (1, "hv")):
                enl[f"temporal_{k}_{nm}"] = sc.enl_temporal(torch.nan_to_num(A.x[:, c].double(), nan=1.0),
                                                            m & A.valid_in.all(0))
    if A.nlooks is not None:
        enl["nlooks_samples_median"] = float(A.nlooks[0, 0][v2].median())
        enl["nlooks_share_full"] = float((A.nlooks[0, 0][v2] >= A.nlooks[0, 0][v2].max()).double().mean())
    res["enl"] = enl
    # ---- stability between dates over forest (dB of the mean power) and simple QC
    if T >= 2:
        s = {}
        for c, nm in ((0, "hh"), (1, "hv")):
            lvl = torch.tensor(lv["forest"][f"{nm}_meanpower_db"], dtype=torch.float64)
            s[nm] = {"per_date_minus_median_db": (lvl - lvl.median()).tolist(),
                     "std_db": float(lvl.std()), "range_db": float(lvl.max() - lvl.min())}
        res["stability_forest"] = s
        qc = []
        for t in range(T):
            flags = []
            for nm in ("hh", "hv"):
                dev_ = s[nm]["per_date_minus_median_db"][t]
                if abs(dev_) > 1.0:
                    flags.append(f"{nm.upper()} forest level off by {dev_:+.2f} dB")
            vf = float((A.valid_in[t] & A.valid_in.any(0)).sum() / A.valid_in.any(0).sum())
            if vf < 0.9:
                flags.append(f"valid share {vf:.2f}")
            qc.append({"date": A.dates[t], "valid_share": vf, "flags": flags})
        res["qc"] = qc
    # ---- figures
    fig_quicklooks(A)
    fig_hist(A, C)
    save_results(A.area, "look", res)
    return res


def fig_quicklooks(A: Area, step=4):
    import matplotlib.pyplot as plt
    T = A.T
    ncol = min(T, 5)
    nrow = 3 if T <= 5 else 4
    picks = list(range(T)) if T <= 5 else [0, 2, 4, 6, 9][:5] if T >= 10 else list(range(0, T, max(1, T // 5)))[:5]
    fig, axs = plt.subplots(3, len(picks), figsize=(3.0 * len(picks), 9.2), squeeze=False)
    for j, t in enumerate(picks):
        x = A.x[t, :, ::step, ::step]
        hh, hv = db(x[0]).numpy(), db(x[1]).numpy()
        axs[0, j].imshow(hh, cmap="gray", vmin=-25, vmax=-3)
        axs[1, j].imshow(hv, cmap="gray", vmin=-30, vmax=-8)
        r = (hh - hv)
        rgb = np.stack([np.clip((hh + 25) / 22, 0, 1), np.clip((hv + 30) / 22, 0, 1), np.clip((r - 2) / 10, 0, 1)], -1)
        rgb[~np.isfinite(hh)] = 1
        axs[2, j].imshow(np.nan_to_num(rgb, nan=1.0))
        axs[0, j].set_title(A.dates[t], fontsize=9)
        for i in range(3):
            axs[i, j].set_xticks([]); axs[i, j].set_yticks([])
    axs[0, 0].set_ylabel("HH dB (-25..-3)")
    axs[1, 0].set_ylabel("HV dB (-30..-8)")
    axs[2, 0].set_ylabel("R=HH G=HV B=HH/HV ratio")
    fig.suptitle(f"{A.area}: calibrated gamma-naught, {A.pixel_m:.0f} m (shown at {A.pixel_m * step:.0f} m)", fontsize=11)
    savefig(fig, f"{A.area}_quicklooks.png")


def fig_hist(A: Area, C: dict):
    import matplotlib.pyplot as plt
    fig, axs = plt.subplots(1, 2, figsize=(11, 3.8))
    edges = torch.linspace(-35, 5, 161)
    cols = {"lake core": "#1f4e9c", "forest": "#2b7a3d", "open land": "#b8860b", "floodplain": "#7fb3d5",
            "flooded-forest ring (Prek Toal)": "#9b59b6"}
    for c, nm in ((0, "HH"), (1, "HV")):
        ax = axs[c]
        d = db(A.x[0, c])
        allv = d[A.v2 & torch.isfinite(d)]
        h = torch.histc(allv, 160, -35, 5)
        ax.fill_between(((edges[:-1] + edges[1:]) / 2).numpy(), (h / h.sum()).numpy(), color="#ccc", label="whole footprint")
        for k, m in C.items():
            v = d[m & torch.isfinite(d)]
            if v.numel() < 100:
                continue
            hk = torch.histc(v, 160, -35, 5)
            ax.plot(((edges[:-1] + edges[1:]) / 2).numpy(), (hk / hk.sum()).numpy(), color=cols.get(k, "k"), lw=1.3,
                    label=f"{k}: median {float(v.median()):.1f} dB")
        lit = {"HH": [(-25, -20, "calm water (lit.)"), (-9, -6, "forest (lit.)")],
               "HV": [(-15, -12, "forest (lit.)")]}[nm]
        for a, b, lab in lit:
            ax.axvspan(a, b, color="#f4d03f", alpha=0.25)
            ax.text((a + b) / 2, ax.get_ylim()[1] * 0.92, lab, ha="center", fontsize=7)
        ax.set_xlabel(f"{nm} gamma-naught (dB), single pixel, {A.dates[0]}")
        ax.set_ylabel("share of pixels per 0.25 dB")
        ax.legend(fontsize=7, loc="upper left")
    fig.tight_layout()
    savefig(fig, f"{A.area}_hist_db.png")


# ======================================================================================= step: water
SPLIT_CAL = {"tonle-sap": {"tune": (0.0, 0.42), "val": (0.42, 0.50), "test": (0.50, 1.0)},
             "sevan": {"tune": (0.0, 0.45), "val": (0.45, 0.55), "test": (0.55, 1.0)},
             "ararat": {"test": (0.0, 1.0)}}
HV_CHOICES = [-28.0, -26.0, -25.0, -24.0, -22.0, -20.0, 99.0]


FPR_MAX = 2e-4
# the physically anchored rule (from the measured distributions, no search): strong = 1 dB below the calm-lake
# median (-19.5 dB), weak = the floodplain histogram valley / never-wet-land limit (-17 dB), HV at the noise
# floor + 2 dB (lake HV -25.8 dB, NESZ ~ -27 dB), 3x3 Quegan, 2-px shoreline refinement.
PHYS = {"water_hh_strong": -20.0, "water_hh_weak": -17.0, "water_hv_max": -24.0, "hv_w": 0.0, "box": 3,
        "min_blob_px": 9, "refine_px": 2}


class WaterCtx:
    """Filtered dB stacks (float16, cached per box) + labels + regions, for the tune/val/TEST search."""

    def __init__(self, A: Area, boxes=(1, 3)):
        from scripts.detect.tune import Region, split_masks
        self.A = A
        self.Lc = {}
        for b in boxes:
            check_mem(f"filter box {b}")
            self.Lc[b] = filtered_db(A.x, A.v2, b, quegan=b > 1, dtype=torch.float16)
        self.raw = torch.stack([db(torch.nan_to_num(A.x[t, 0], nan=1e-10)) for t in range(A.T)]).half()
        H, W = A.grid.h, A.grid.w
        spec = SPLIT_CAL.get(A.src, {"test": (0.0, 1.0)})
        self.masks = split_masks(H, W, spec)
        self.masks["all"] = torch.ones(H, W, dtype=torch.bool)
        self.cols = {}
        for k, m in self.masks.items():
            cc = m.any(0).nonzero()[:, 0]
            self.cols[k] = (int(cc.min()), int(cc.max()) + 1)
        self.reg = {}
        for k, m in self.masks.items():
            c0, c1 = self.cols[k]
            lab = {kk: vv[:, c0:c1] for kk, vv in A.lab.items()}
            self.reg[k] = Region(lab, m[:, c0:c1], A.ref["occ_mean"][:, c0:c1], A.km2, A.T)

    def water(self, s, part="all"):
        c0, c1 = self.cols[part]
        L = self.Lc[s["box"]][:, :, :, c0:c1].float()
        v2 = self.A.v2[:, c0:c1]
        w = water_db(L, v2, s, None if self.A.water_ok is None else self.A.water_ok[:, c0:c1])
        if s.get("refine_px", 0):
            w = refine_shore(w, self.raw[:, :, c0:c1].float(), v2, s["water_hh_weak"], s["refine_px"])
            if self.A.water_ok is not None:
                w = w & self.A.water_ok[None, :, c0:c1]
        return w & v2[None]

    def score(self, s, part):
        from scripts.detect.tune import objective
        m = self.reg[part].metrics(self.water(s, part))
        J = objective([m])
        # hard false-alarm budget: the preview detector keeps never-wet land at ~0.005 %; a rule above
        # FPR_MAX (0.02 %) buys rank correlation with floodplain occurrence by flooding dry land -> rejected
        if m["fpr_never"] > FPR_MAX:
            J -= 1.0 + 100.0 * (m["fpr_never"] - FPR_MAX)
        return J, m


def sample_db(rng, around=None, scale=1.0):
    # strong is kept in [-21, -15] dB: a seed level below the calm-lake median (-19.5 dB) would leave a windy
    # date with no seeds at all (the first run picked strong -21.7 dB: perfect on 19 June, fragile elsewhere).
    if around is None:
        st = float(rng.uniform(-21.0, -15.0))
        s = {"water_hh_strong": st, "water_hh_weak": st + float(rng.uniform(0.0, 5.0)),
             "water_hv_max": float(rng.choice(HV_CHOICES)), "hv_w": float(rng.choice([0.0, 0.0, 0.3, 0.5])),
             "box": int(rng.choice([1, 3])), "min_blob_px": int(rng.choice([1, 4, 9, 20])),
             "refine_px": int(rng.choice([0, 2]))}
    else:
        s = dict(around)
        s["water_hh_strong"] = float(np.clip(s["water_hh_strong"] + rng.normal(0, 0.8 * scale), -21, -15))
        s["water_hh_weak"] = float(np.clip(s["water_hh_weak"] + rng.normal(0, 0.8 * scale), -26, -8))
        if rng.random() < 0.2:
            s["water_hv_max"] = float(rng.choice(HV_CHOICES))
        if rng.random() < 0.15:
            s["box"] = int(rng.choice([1, 3]))
        if rng.random() < 0.15:
            s["min_blob_px"] = int(rng.choice([1, 4, 9, 20]))
        if rng.random() < 0.15:
            s["refine_px"] = int(rng.choice([0, 2]))
    s["water_hh_weak"] = max(s["water_hh_weak"], s["water_hh_strong"])
    for k in ("water_hh_strong", "water_hh_weak"):
        s[k] = round(s[k], 2)
    return s


def water_km2(w, mask2, km2):
    return [float((w[t] & mask2).sum()) * km2 for t in range(w.shape[0])]


def external_series(src: str) -> dict:
    """Altimeter level per pass, preview open-water and flooded-veg series, hydro expected extents."""
    out = {}
    p = ROOT / "docs/eda/validation2026/results.json"
    if p.exists() and src == "tonle-sap":
        r = json.loads(p.read_text())["test3_tonle_sap"]
        out["dates"] = r["pass_dates"]
        out["altimeter_m"] = r["altimetry_egm2008_at_pass_m"]
        out["preview_open_km2"] = r["open_water_km2"]
        out["preview_fv_km2"] = r["flooded_veg_km2"]
    p = ROOT / "docs/eda/hydro/results.json"
    if p.exists() and src == "tonle-sap":
        r = json.loads(p.read_text())
        out["hydro_expected_km2"] = r["expected_km2"]
        cal = r["calibration"]["ratio (strong = weak/2)"]
        out["preview_curve"] = {"weak": cal["thresholds_weak"], "area_km2": cal["area_km2"]}
    if src == "tonle-sap":
        # fusion v2 = the app's water source since 14 Nov ~16:50 (preview-based, per date, from the lead)
        out["fusion2_km2"] = [2281.0, 2355.0, 2418.0, 2537.0, 3152.0, 3461.0, 3818.0, 4436.0, 4904.0, 5046.0]
    return out


def step_water(A: Area, n_eval=240, seed=20261114) -> dict:
    t0 = time.time()
    ctx = WaterCtx(A)
    rng = np.random.default_rng(seed)
    base = {"water_hh_strong": CAL_KNOBS["water_hh_strong"], "water_hh_weak": CAL_KNOBS["water_hh_weak"],
            "water_hv_max": CAL_KNOBS["water_hv_max"], "hv_w": 0.0, "box": 3, "min_blob_px": 9, "refine_px": 0}
    first = [base, dict(base, water_hh_strong=-18.0, water_hh_weak=-15.0), dict(PHYS)]
    hist = []
    n_rand = int(n_eval * 0.65)
    for i in range(n_eval):
        if i < len(first):
            s = first[i]
        elif i < n_rand:
            s = sample_db(rng)
        else:
            top = sorted(hist, key=lambda h: -h["J"])[:5]
            s = sample_db(rng, top[int(rng.integers(0, len(top)))]["s"], 1.0 - 0.7 * (i - n_rand) / max(n_eval - n_rand, 1))
        J, m = ctx.score(s, "tune" if "tune" in ctx.reg else "test")
        hist.append({"s": s, "J": J, "m": m})
        if i % 40 == 0:
            print(f"  eval {i}: best J {max(h['J'] for h in hist):.4f} ({time.time() - t0:.0f} s)", flush=True)
    top = sorted(hist, key=lambda h: -h["J"])[:20]
    if "val" in ctx.reg:
        for h in top:
            h["Jval"], h["mval"] = ctx.score(h["s"], "val")
        best = max(top, key=lambda h: h["Jval"])
    else:
        best = top[0]
    s = best["s"]
    print("  best knobs:", s, flush=True)
    # TEST once, with block-bootstrap intervals; same for the untested knobs and the brief's -18/-15 guess
    gen = torch.Generator().manual_seed(seed)
    test = {}
    for name, ss in (("tuned", s), ("physical", PHYS), ("untested_gamma0", base), ("guess_-18_-15", first[1])):
        w = ctx.water(ss, "test")
        test[name] = {"point": ctx.reg["test"].metrics(w), "ci95": ctx.reg["test"].bootstrap(w, 100, gen)}
    # whole-scene series
    w_all = ctx.water(s, "all")
    dom = A.prev_dom if A.prev_dom is not None else A.v2
    series = {"footprint_km2": water_km2(w_all, A.v2, A.km2), "in_preview_footprint_km2": water_km2(w_all, dom & A.v2, A.km2)}
    w_phys = ctx.water(PHYS, "all")
    series["physical_rule_km2"] = water_km2(w_phys, dom & A.v2, A.km2)
    torch.save({"water": w_phys, "knobs": PHYS, "dates": A.dates}, det_dir(A.area) / "water_physical.pt")
    del w_phys
    # physically defensible threshold interval, from the measured distributions (filtered, chosen box)
    L = ctx.Lc[s["box"]]
    lake = ops.erode(A.lab["perm"][None], 5)[0] & A.v2
    never = A.lab["never"] & A.v2
    flood_dom = A.v2 & ((A.ref["occ_any_frac"] > 0) | (A.ref["ext_frac"] > 0))
    calm = []
    for t in range(A.T):
        calm.append({"lake_p50": quant(L[t, 0][lake].float(), [0.5])[0], "lake_p95": quant(L[t, 0][lake].float(), [0.95])[0],
                     "lake_p99": quant(L[t, 0][lake].float(), [0.99])[0],
                     "never_p001": quant(L[t, 0][never].float(), [0.001])[0], "never_p01": quant(L[t, 0][never].float(), [0.01])[0],
                     "never_p05": quant(L[t, 0][never].float(), [0.05])[0],
                     "valley_db": hist_valley(L[t, 0][flood_dom].float())})
    lo_thr = float(np.median([c["lake_p95"] for c in calm]))   # weak >= this keeps 95 % of calm lake on a typical date
    hi_thr = float(np.median([c["never_p01"] for c in calm]))   # weak <= this: <= 1 % of never-wet land below it (per pixel)
    # threshold sweep. 'hyst': strong = weak - 3 dB (a factor 2 in power; the analogue of the preview's
    # strong = weak/2); 'single': one threshold (strong = weak). Strong is NOT held at the tuned value: a seed
    # level far below the lake median makes the area jump from 0 to the full lake (percolation), which is a
    # property of that knob, not of the data.
    sweep = []
    never_km2 = float(never.sum()) * A.km2
    grid_w = sorted(set(np.round(np.arange(-22.0, -9.9, 1.0), 2).tolist()) | set(np.round(np.arange(-19.0, -13.9, 0.5), 2).tolist()))
    for wk in grid_w:
        row = {"weak": float(wk)}
        for nm, st in (("hyst", wk - 3.0), ("single", wk)):
            if nm == "single" and not (-19.0 <= wk <= -14.0):
                continue
            ss = dict(s, water_hh_weak=float(wk), water_hh_strong=float(st))
            ww = ctx.water(ss, "all")
            row[f"{nm}_km2"] = water_km2(ww, dom & A.v2, A.km2)
            row[f"{nm}_fp_never_km2"] = water_km2(ww, never, A.km2)
            del ww
        sweep.append(row)
    ok_h = [r["weak"] for r in sweep if max(r["hyst_fp_never_km2"]) <= 1e-4 * never_km2]
    hi_thr_h = max(ok_h) if ok_h else hi_thr   # loosest weak whose hysteresis map keeps never-wet land <= 0.01 %

    def rng_in(lo, hi, key):
        rows = [r for r in sweep if lo - 1e-6 <= r["weak"] <= hi + 1e-6]
        if not rows:
            return None
        rows = [r for r in rows if key in r]
        if not rows:
            return None
        a = np.array([r[key] for r in rows])
        return {"weak_values": [r["weak"] for r in rows], "min_km2": a.min(0).tolist(), "max_km2": a.max(0).tolist(),
                "max_over_min": (a.max(0) / np.maximum(a.min(0), 1e-6)).tolist()}
    sens = {"strict_interval_db": [lo_thr, hi_thr], "hysteresis_safe_interval_db": [lo_thr, hi_thr_h],
            "strict_hyst": rng_in(lo_thr, hi_thr, "hyst_km2"), "safe_hyst": rng_in(lo_thr, hi_thr_h, "hyst_km2"),
            "strict_single": rng_in(lo_thr, hi_thr, "single_km2"), "safe_single": rng_in(lo_thr, hi_thr_h, "single_km2")}
    # per-date data-defined threshold: the floodplain histogram valley between the water and land modes
    valley_km2 = []
    for t in range(A.T):
        v = calm[t]["valley_db"]
        if v is None:
            valley_km2.append(None)
            continue
        ss = dict(s, water_hh_weak=float(v), water_hh_strong=float(v - 3.0))
        Lt = ctx.Lc[s["box"]][t:t + 1].float()
        wt = water_db(Lt, A.v2, ss, A.water_ok)
        valley_km2.append(float((wt[0] & dom & A.v2).sum()) * A.km2)
        del wt, Lt
    sens["valley_threshold_km2"] = valley_km2
    # preview sensitivity on the same dates (hydro calibration curve, strong = weak/2), Sevan-compatible weak
    ext = external_series(A.src)
    idx = [ext["dates"].index(d) if d in ext.get("dates", []) else None for d in A.dates]
    if "preview_curve" in ext:
        pc = ext["preview_curve"]
        sel = [i for i, wk in enumerate(pc["weak"]) if 0.04 - 1e-9 <= wk <= 0.17 + 1e-9]
        a = np.array([pc["area_km2"][i] for i in sel])
        sens["preview_weak_0.04_0.17"] = {"min_km2": [float(a[:, j].min()) if j is not None else None for j in idx],
                                          "max_km2": [float(a[:, j].max()) if j is not None else None for j in idx]}
    # the self-calibrating seeded detector (crossscene), on the same filtered dB stack, no tuning at all
    from scripts.detect import crossscene as cs
    check_mem("seeded detector")
    Lf = ctx.Lc[s["box"]].float()
    vT = A.v2[None].expand(A.T, -1, -1).contiguous()
    c0, c1 = ctx.cols["test"]
    seeded = {}
    # 'default': the cross-scene knobs exactly as calibrated on preview logits (s_weak 0.635, s_strong 0.447);
    # 'db': the same detector with its two scene-relative levels re-derived for dB (see SEEDED_DB)
    variants = (("default", None), ("db", SEEDED_DB)) if SEEDED_ON else ()
    for nm, kn_s in variants:
        rs = cs.detect_water_adaptive(Lf, vT, A.water_ok, method="seeded", source="gamma0", kn=kn_s)
        ws = rs["water"] & A.v2[None]
        seeded[nm] = {"knobs": kn_s, "has_water": rs["has_water"],
                      "test": {"point": ctx.reg["test"].metrics(ws[:, :, c0:c1]),
                               "ci95": ctx.reg["test"].bootstrap(ws[:, :, c0:c1], 100, gen)},
                      "km2": water_km2(ws, dom & A.v2, A.km2),
                      "equiv_hh_thresholds_db": [seeded_equiv_db(rs["diag"], th) for th in rs["thresholds"]],
                      "diag": {k: v for k, v in rs["diag"].items() if k in ("seed_px", "seed_level", "seed_spread", "land_level",
                                                                               "land_spread", "sep", "s_valley", "chan_median", "chan_iqr")},
                      "feature": (rs["diag"].get("mode") or {}).get("feature")}
        if nm == "db":
            torch.save({"water": ws, "dates": A.dates}, det_dir(A.area) / "water_seeded.pt")
        del rs, ws
    del Lf
    corr = {}
    pairs = [(i, j) for i, j in enumerate(idx) if j is not None]
    if len(pairs) >= 3 and "altimeter_m" in ext:
        alt = [ext["altimeter_m"][j] for _, j in pairs]
        corr["n_dates"] = len(pairs)
        corr["calibrated_vs_altimeter"] = dict(zip(("spearman", "pearson"), spearman([series["in_preview_footprint_km2"][i] for i, _ in pairs], alt)))
        corr["physical_vs_altimeter"] = dict(zip(("spearman", "pearson"), spearman([series["physical_rule_km2"][i] for i, _ in pairs], alt)))
        if "db" in seeded:
            corr["seeded_db_vs_altimeter"] = dict(zip(("spearman", "pearson"), spearman([seeded["db"]["km2"][i] for i, _ in pairs], alt)))
        if "fusion2_km2" in ext:
            corr["fusion2_vs_altimeter_same_dates"] = dict(zip(("spearman", "pearson"), spearman([ext["fusion2_km2"][j] for _, j in pairs], alt)))
        corr["preview_vs_altimeter_same_dates"] = dict(zip(("spearman", "pearson"), spearman([ext["preview_open_km2"][j] for _, j in pairs], alt)))
    aligned = {"altimeter_m": [ext["altimeter_m"][j] if j is not None else None for j in idx] if "altimeter_m" in ext else None,
               "preview_open_km2": [ext["preview_open_km2"][j] if j is not None else None for j in idx] if "preview_open_km2" in ext else None,
               "preview_fv_km2": [ext["preview_fv_km2"][j] if j is not None else None for j in idx] if "preview_fv_km2" in ext else None,
               "fusion2_km2": [ext["fusion2_km2"][j] if j is not None else None for j in idx] if "fusion2_km2" in ext else None,
               "hydro_expected_km2": {k: [ext["hydro_expected_km2"][k][j] if j is not None else None for j in idx]
                                      for k in ("central", "lo", "hi")} if "hydro_expected_km2" in ext else None}
    res = {"best_knobs": s, "n_eval": n_eval, "dates": A.dates,
           "top5_tune": [{"s": h["s"], "J": h["J"], "Jval": h.get("Jval")} for h in top[:5]],
           "tune_metrics": best["m"], "val_metrics": best.get("mval"), "test": test, "series": series,
           "levels_filtered": calm, "sensitivity": sens, "sweep": sweep, "seeded": seeded,
           "external_aligned": aligned, "corr": corr, "seconds": time.time() - t0}
    save_results(A.area, "water", res)
    torch.save({"water": w_all, "knobs": s, "dates": A.dates}, det_dir(A.area) / "water.pt")
    fig_separation(A, ctx, s)
    fig_area_curves(A, res)
    return res


# The seeded detector works in scene-relative units s = (f - seed level)/(land level - seed level). Its knobs were
# calibrated on preview LOGITS, where the clipped black lake squeezes the dark end; in dB the same s_weak 0.635
# lands at about -11 dB HH (far into land). Re-derived for dB from the 19 June distributions: seed level
# (calm lake, mean feature) and land q25 are ~10 dB apart, and the never-wet-land limit of the weak threshold is
# ~2.5 dB above the lake median -> s_weak 0.25; strong 0.1 (1 dB above the lake median).
SEEDED_ON = True   # --no-seeded: skip it (crossscene's CPU reconstruction takes > 15 min on 10 x 6.4 Mpx)
SEEDED_DB = {"s_weak": 0.25, "s_strong": 0.10, "s_seed_keep": 0.45}


def seeded_equiv_db(diag: dict, th) -> dict | None:
    """crossscene._equiv converts to preview brightness; for gamma0 the feature is affine in dB: HH dB =
    median_HH + IQR_HH * f / sum(w)."""
    if th is None or not diag.get("mode"):
        return None
    w = diag["mode"]["w"]
    out = {}
    for nm, sv in (("strong", th["s_strong"]), ("weak", th["s_weak"])):
        f = th["a_seed"] + sv * (th["b_land"] - th["a_seed"])
        z = f / float(sum(w))
        out[nm] = diag["chan_median"][0] + diag["chan_iqr"][0] * z
    return out


def hist_valley(v: torch.Tensor, lo=-24.0, hi=-4.0, bin_db=0.25, sigma_db=0.5):
    """Deepest minimum of the smoothed dB histogram between the water mode (< -15 dB) and the land mode,
    searched in [lo, hi]. The data-defined threshold for one date (None if there is no water mode)."""
    v = v[torch.isfinite(v)].float()
    if v.numel() < 1000:
        return None
    nb = int((hi - lo) / bin_db)
    h = torch.histc(v.clamp(lo, hi - 1e-4), nb, lo, hi).double()
    r = int(4 * sigma_db / bin_db)
    k = torch.arange(-r, r + 1).double()
    ker = torch.exp(-0.5 * (k * bin_db / sigma_db) ** 2)
    hs = F.conv1d(F.pad(h[None, None], (r, r), mode="replicate"), (ker / ker.sum())[None, None])[0, 0]
    c = lo + (torch.arange(nb) + 0.5) * bin_db
    wi = int(hs[c < -15].argmax())                      # water peak
    li = wi + 1 + int(hs[wi + 1:].argmax()) if wi + 1 < nb else None
    if li is None or li - wi < 4:
        return None
    vi = wi + int(hs[wi:li + 1].argmin())
    if hs[vi] > 0.8 * min(hs[wi], hs[li]):              # no real valley
        return None
    return float(c[vi])


def fig_separation(A: Area, ctx: WaterCtx, s: dict, t: int = 0):
    """Water vs land separation: preview display brightness (its own grid) vs calibrated dB (filtered)."""
    import matplotlib.pyplot as plt
    from scripts.detect.tune import build_labels, LABEL_RULE
    out = {}
    fig, axs = plt.subplots(1, 2, figsize=(11, 3.6))
    # calibrated
    L = ctx.Lc[s["box"]][t, 0].float()
    lake = A.lab["perm"] & A.v2
    never = A.lab["never"] & A.v2
    flood = A.v2 & (A.ref["occ_mean"] >= 1) & (A.ref["occ_mean"] < 90)
    panels = [("calibrated HH, dB (%dx%d Quegan, 80 m)" % (s["box"], s["box"]), L, lake, never, flood, (-30, 0), A.km2)]
    # preview
    try:
        from scripts.detect.stack import load_preview_stack
        from scripts.detect.validate import reference_on_grid
        st = load_preview_stack(A.src)
        P = ops.quegan_filter(st.x, st.valid, 3)[t, 0]
        H, W = P.shape
        ref, _ = reference_on_grid(A.src, st.bounds, H, W)
        lab = build_labels(ref, st.valid.all(0), **LABEL_RULE)
        flood_p = st.valid.all(0) & (ref["occ_mean"] >= 1) & (ref["occ_mean"] < 90)
        panels.insert(0, ("preview HH display brightness (3x3 Quegan, 180 m)", P, lab["perm"], lab["never"], flood_p, (0, 1.0), st.km2_per_px))
    except Exception as e:  # noqa: BLE001
        print("  preview unavailable:", e)
    for ax, (title, V, wm, lm, fm, (lo, hi), km2) in zip(axs, panels):
        edges = torch.linspace(lo, hi, 121)
        cen = ((edges[:-1] + edges[1:]) / 2).numpy()
        hs = {}
        for nm, m, col in (("permanent water (JRC)", wm, "#1f4e9c"), ("never water (JRC)", lm, "#b8860b"),
                           ("floodplain, occurrence 1-90 %", fm, "#7fb3d5")):
            v = V[m].float().clamp(lo, hi - 1e-6)
            h = torch.histc(v, 120, lo, hi)
            hs[nm] = h / h.sum()
            ax.plot(cen, hs[nm].numpy(), color=col, lw=1.4, label=nm)
        wv, lv = V[wm].float(), V[lm].float()
        mw, ml = float(wv.median()), float(lv.median())
        sw = float((wv - mw).abs().median() * 1.4826)
        sl = float((lv - ml).abs().median() * 1.4826)
        dprime = abs(ml - mw) / math.sqrt((sw ** 2 + sl ** 2) / 2)
        ovl = float(torch.minimum(hs["permanent water (JRC)"], hs["never water (JRC)"]).sum())
        # what matters for threshold sensitivity: how many FLOODPLAIN pixels sit between the two classes
        # (above 95 % of permanent water and below 5 % of never-water land) -- the 'ambiguous band'
        wq95 = quant(wv, [0.95])[0]
        lq05 = quant(lv, [0.05])[0]
        fv_ = V[fm].float()
        amb = float(((fv_ > wq95) & (fv_ < lq05)).double().mean()) if lq05 > wq95 else 0.0
        hf = hs["floodplain, occurrence 1-90 %"]
        i_w, i_l = int(((wq95 - lo) / (hi - lo)) * 120), int(((lq05 - lo) / (hi - lo)) * 120)
        i_w, i_l = max(0, min(i_w, 119)), max(0, min(i_l, 119))
        valley = float(hf[min(i_w, i_l):max(i_w, i_l) + 1].min() / hf.max()) if i_l > i_w else None
        out["preview" if "preview" in title else "calibrated"] = {"water_median": mw, "land_median": ml,
                                                                  "water_robust_sd": sw, "land_robust_sd": sl,
                                                                  "d_prime": dprime, "histogram_overlap": ovl,
                                                                  "water_p95": wq95, "land_p05": lq05,
                                                                  "floodplain_share_in_ambiguous_band": amb,
                                                                  "floodplain_valley_over_peak": valley}
        ax.axvspan(min(wq95, lq05), max(wq95, lq05), color="#f4d03f", alpha=0.2)
        title = title + f"\nfloodplain pixels between the classes: {amb * 100:.0f} %"
        ax.set_title(f"{title}\nseparation d' = {dprime:.1f}, overlap {ovl * 100:.2f} %", fontsize=9)
        ax.set_ylabel("share of class")
        ax.legend(fontsize=7)
    axs[-1].axvline(s["water_hh_weak"], color="k", ls="--", lw=0.8)
    axs[-1].axvline(s["water_hh_strong"], color="k", ls=":", lw=0.8)
    fig.tight_layout()
    savefig(fig, f"{A.area}_separation.png")
    save_results(A.area, "separation", out)
    return out


def fig_area_curves(A: Area, res: dict):
    """(a) calibrated open-water area vs the dB threshold, per date; (b) the same for previews (brightness);
    (c) the series with the threshold ranges, the seeded detector, previews and the independent expectation."""
    import matplotlib.pyplot as plt
    sens, ex = res["sensitivity"], res["external_aligned"]
    T = A.T
    cm = plt.get_cmap("viridis", max(T, 2))
    fig, axs = plt.subplots(1, 3, figsize=(16, 4.6))
    ax = axs[0]
    wk = [r["weak"] for r in res["sweep"]]
    lo, hi = sens["strict_interval_db"]
    hs = sens["hysteresis_safe_interval_db"][1]
    ax.axvspan(lo, hi, color="#2f6fde", alpha=0.18, label=f"strict interval [{lo:.1f}, {hi:.1f}] dB")
    if hs > hi:
        ax.axvspan(hi, hs, color="#2f6fde", alpha=0.07, label=f"up to never-wet-land limit {hs:.1f} dB")
    for t in range(T):
        ax.plot(wk, [r["hyst_km2"][t] for r in res["sweep"]], color=cm(t), lw=1.4, label=A.dates[t])
    ax.set_xlabel("weak threshold, filtered HH (dB); strong = weak - 3 dB")
    ax.set_ylabel("open water km² (preview footprint)")
    ax.set_title("Calibrated: area vs threshold", fontsize=10)
    ax.set_ylim(0, 8000)
    ax.legend(fontsize=7, loc="upper left")
    ax = axs[1]
    ext = external_series(A.src)
    if "preview_curve" in ext:
        pc = ext["preview_curve"]
        ax.axvspan(0.04, 0.17, color="#888", alpha=0.18, label="Sevan-compatible weak 0.04-0.17")
        for t, d in enumerate(A.dates):
            if d in ext["dates"]:
                j = ext["dates"].index(d)
                ax.plot(pc["weak"], [a[j] for a in pc["area_km2"]], color=cm(t), lw=1.4, label=d)
        ax.set_xlabel("weak threshold, preview HH brightness (0-1); strong = weak/2")
        ax.set_title("Previews: area vs threshold (hydro calibration curve)", fontsize=10)
        ax.set_ylim(0, 8000)
        ax.legend(fontsize=7, loc="upper left")
    ax = axs[2]
    xs = np.arange(T)
    s = sens.get("safe_hyst") or sens.get("strict_hyst")
    if s:
        ax.fill_between(xs, s["min_km2"], s["max_km2"], color="#2f6fde", alpha=0.15, step="mid",
                        label="calibrated, threshold range (to never-wet limit)")
        for t in range(T):
            ax.plot([t, t], [s["min_km2"][t], s["max_km2"][t]], color="#2f6fde", lw=6, alpha=0.35)
    ax.plot(xs, res["series"]["in_preview_footprint_km2"], "o-", color="#2f6fde", label="calibrated, tuned dB rule")
    if "physical_rule_km2" in res["series"]:
        ax.plot(xs, res["series"]["physical_rule_km2"], "D-", color="#8e44ad", ms=4, label="calibrated, physical rule (-20/-17 dB)")
    if "db" in res["seeded"]:
        ax.plot(xs, res["seeded"]["db"]["km2"], "^-", color="#16a085", label="calibrated, seeded (dB levels)")
        ax.plot(xs, res["seeded"]["default"]["km2"], "v:", color="#16a085", alpha=0.6, label="calibrated, seeded (preview knobs)")
    vk = sens.get("valley_threshold_km2")
    if vk and any(v is not None for v in vk):
        ax.plot(xs, [np.nan if v is None else v for v in vk], "x", color="k", label="calibrated, histogram-valley threshold")
    if ex.get("preview_open_km2"):
        pv = [np.nan if v is None else v for v in ex["preview_open_km2"]]
        ax.plot(xs, pv, "s--", color="#888", label="preview detector")
        if ex.get("fusion2_km2"):
            ax.plot(xs, [np.nan if v is None else v for v in ex["fusion2_km2"]], "P--", color="#e67e22", ms=5,
                    label="fusion v2 (app, previews)")
        pr = sens.get("preview_weak_0.04_0.17")
        if pr:
            for t in range(T):
                if pr["min_km2"][t] is not None:
                    ax.plot([t + 0.15, t + 0.15], [pr["min_km2"][t], pr["max_km2"][t]], color="#888", lw=6, alpha=0.35)
    if ex.get("hydro_expected_km2"):
        e = ex["hydro_expected_km2"]
        c = np.array([np.nan if v is None else v for v in e["central"]])
        l_ = np.array([np.nan if v is None else v for v in e["lo"]])
        h_ = np.array([np.nan if v is None else v for v in e["hi"]])
        ax.errorbar(xs - 0.15, c, yerr=[c - l_, h_ - c], fmt="d", color="#c0392b", ms=5, lw=1, capsize=3,
                    label="expected, altimeter + Landsat (no radar)")
    ax.set_xticks(xs)
    ax.set_xticklabels([d[5:] for d in A.dates], fontsize=8)
    ax.set_ylabel("open water km²")
    ax.set_title("Series (bars: threshold sensitivity; grey = previews)", fontsize=10)
    ax.legend(fontsize=7, loc="upper left")
    fig.tight_layout()
    savefig(fig, f"{A.area}_area_curves.png")


# ======================================================================================= step: flooded vegetation
def best_water_knobs(A: Area) -> dict:
    p = det_dir(A.area) / "water.pt"
    s = torch.load(p, weights_only=False)["knobs"] if p.exists() else {}
    return knobs_for(A.pixel_m, **s)


def step_fv(A: Area) -> dict:
    t0 = time.time()
    kn = best_water_knobs(A)
    check_mem("fv detect")
    o = detect(A.x, A.valid_in, kn, A.pixel_m, A.water_ok, fv=True, verbose=True)
    L, w, v2 = o["L"], o["water"], o["valid2"]
    T = A.T
    dom = (A.prev_dom if A.prev_dom is not None else v2) & v2
    km = lambda m: water_km2(m, dom, A.km2)
    fv = o["flooded_veg"]
    res = {"knobs": fv_knobs(A.pixel_m), "water_knobs": kn, "fv_km2": km(fv), "going_under_km2": km(fv & (o["fv_state"] == 2)),
           "double_bounce_km2": km(fv & (o["fv_state"] == 1)), "noise_sigma_df_db": o["noise_sigma_df"].tolist(),
           "open_water_km2": km(w)}
    # simple ops rule in dB (HH rise > 3 dB, HV rise < 1.5 dB), for comparison
    prior = (w.sum(0).float() / T >= 0.9)
    simple = ops.flooded_veg_masks(L, v2[None].expand(T, -1, -1), w, prior, dict(kn, open_k=0, min_blob_px=kn["min_blob_px"]))
    res["simple_rule_km2"] = km(simple)
    del simple
    # threshold sensitivity of the rebuilt rule (peak area and per-date areas)
    sens = []
    vT = v2[None].expand(T, -1, -1)
    for name, over in (("rise floor 0.5 dB", {"db_rise_min": 0.5}), ("rise floor 2 dB", {"db_rise_min": 2.0}),
                       ("2 sigma", {"db_k_sigma": 2.0}), ("4 sigma", {"db_k_sigma": 4.0}),
                       ("HH may fall 2 dB", {"db_hh_drop_max": 2.0}), ("HH floor -12 dB", {"db_hh_min": -12.0}),
                       ("context x0.5", {k: v * 0.5 for k, v in fv_knobs(A.pixel_m).items() if k in ("near_px", "max_px", "decay_px")}),
                       ("context x2", {k: v * 2 for k, v in fv_knobs(A.pixel_m).items() if k in ("near_px", "max_px", "decay_px")})):
        over = {k: (int(round(v)) if k in ("near_px", "max_px") else v) for k, v in over.items()}
        f2 = fv_db(L, vT, w, A.pixel_m, A.water_ok, **over)["flooded_veg"]
        sens.append({"variant": name, "km2": km(f2)})
        del f2
        gc.collect()
    res["sensitivity"] = sens
    peaks = [max(s["km2"]) for s in sens] + [max(res["fv_km2"])]
    res["peak_range_km2"] = [min(peaks), max(peaks)]
    res["peak_ratio_max_over_min"] = max(peaks) / max(min(peaks), 1e-6)
    # ratio (HH/HV in dB) early vs each date, double-bounce map
    r = ratio_db(L)
    nref = 2
    r0 = r[:nref].mean(0)
    hh0, hv0 = L[:nref, 0].mean(0), L[:nref, 1].mean(0)
    near = ops.dilate(w.any(0)[None], 2 * int(round(30 * 180 / A.pixel_m)) + 1)[0] & v2 & ~(w.sum(0) >= 0.9 * T)
    db_map = []
    for t in range(T):
        dbm = ((L[t, 0] - hh0) > -0.5) & ((L[t, 1] - hv0) < -1.0) & ((r[t] - r0) > 2.0) & near & ~w[t]
        db_map.append(float((dbm & dom).sum()) * A.km2)
    res["double_bounce_signature_km2"] = db_map
    res["ratio_db_levels"] = {"near_lake_median_per_date": [float(r[t][near].median()) for t in range(T)],
                              "forest_ring_median_per_date": [float(r[t][A.spot_mask("prek-toal", 3000) & v2].median())
                                                              for t in range(T)] if A.spot_mask("prek-toal") is not None else None}
    # compare with the preview rebuilt rule (location + timing)
    cmp = {}
    pf = ROOT / "data/processed" / A.src / "floodveg/products.pt"
    if pf.exists():
        P = torch.load(pf, weights_only=False, map_location="cpu")
        pe = from_preview(P["ever_flooded_veg"].float(), P["bounds"], A.grid) > 0.5
        ce = fv.any(0) & dom
        cmp = {"preview_fv_km2": [float(v) for v in P["fv_km2"]],
               "preview_ever_km2": float((pe & dom).sum()) * A.km2, "calibrated_ever_km2": float(ce.sum()) * A.km2,
               "overlap_km2": float((pe & ce).sum()) * A.km2,
               "share_of_calibrated_inside_preview_ever_dilated_2px": float((ce & ops.dilate(pe[None], 5)[0]).sum() / ce.sum().clamp_min(1)),
               "share_inside_jrc_max_extent": float((ce & (A.ref["ext_frac"] > 0)).sum() / ce.sum().clamp_min(1))}
    res["vs_preview"] = cmp
    res["seconds"] = time.time() - t0
    save_results(A.area, "fv", res)
    torch.save({"flooded_veg": fv, "fv_state": o["fv_state"], "ratio_db": r.half(), "water": w, "valid2": v2,
                "dates": A.dates}, det_dir(A.area) / "fv.pt")
    fig_fv(A, L, w, fv, o["fv_state"], r, res)
    return res


def _crop_box(A: Area, lon, lat, half_km):
    r, c = A.grid.lonlat_to_rc(lon, lat)
    h = int(half_km * 1000 / A.pixel_m)
    return max(r - h, 0), min(r + h, A.grid.h), max(c - h, 0), min(c + h, A.grid.w)


def fig_fv(A, L, w, fv, state, r, res, step=3):
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    T = A.T
    peak = int(np.argmax(res["fv_km2"]))
    early = 0
    fig, axs = plt.subplots(2, 3, figsize=(14, 9.5))
    sl = (slice(None, None, step), slice(None, None, step))
    for j, t in enumerate((early, 3 if T > 3 else early, peak)):
        ax = axs[0, j]
        ax.imshow(r[t][sl].float().numpy(), cmap="magma", vmin=0, vmax=14)
        ax.set_title(f"HH/HV ratio (dB) {A.dates[t]}", fontsize=9)
    ax = axs[1, 0]
    im = ax.imshow((r[peak] - r[:2].mean(0))[sl].float().numpy(), cmap="RdBu_r", vmin=-6, vmax=6)
    ax.set_title(f"ratio change {A.dates[peak]} minus dry-season mean (dB)", fontsize=9)
    plt.colorbar(im, ax=ax, fraction=0.04)
    for j, t in enumerate((3 if T > 3 else 0, peak)):
        ax = axs[1, 1 + j]
        img = torch.zeros(L.shape[-2:], dtype=torch.uint8)
        img[fv[t] & (state[t] == 1)] = 2
        img[fv[t] & (state[t] == 2)] = 3
        img[w[t]] = 1
        img[~A.v2] = 4
        ax.imshow(img[sl].numpy(), cmap=ListedColormap(["#f2f2f2", "#2f6fde", "#35b04a", "#7fd3c4", "#ffffff"]),
                  vmin=0, vmax=4, interpolation="nearest")
        ax.set_title(f"{A.dates[t]}: open water (blue), double bounce (green), going under (teal)\n"
                     f"flooded vegetation {res['fv_km2'][t]:,.0f} km²", fontsize=9)
    for a in axs.flat:
        a.set_xticks([]); a.set_yticks([])
    fig.tight_layout()
    savefig(fig, f"{A.area}_flooded_veg.png")


# ======================================================================================= step: change statistics
def step_change(A: Area, q: float = 0.01) -> dict:
    """Omnibus + sequential likelihood-ratio tests (Conradsen et al. 2016) on the UNFILTERED 80 m intensities with
    the THEORETICAL gamma null and the measured ENL; BH false-discovery rate q over the footprint."""
    t0 = time.time()
    T, v2 = A.T, A.v2
    hvmed = torch.stack([db(A.x[t, 1]) for t in range(min(T, 2))]).nanmedian(0).values
    C = A.classes(hvmed)
    lake = C["lake core"]
    # ENL per channel: spatial, quantile matched, on the lake core (texture-free), median over dates
    n = []
    for c in range(2):
        e = [sc.enl_spatial(torch.nan_to_num(A.x[t:t + 1, c].double(), nan=1.0), (lake & A.valid_in[t])[None], k=7)
             for t in range(T)]
        n.append(float(np.median(e)))
    enl_forest = [sc.enl_temporal(torch.nan_to_num(A.x[:, c].double(), nan=1.0), C["forest"] & v2) for c in range(2)]
    print(f"  ENL (lake, spatial) HH {n[0]:.1f} HV {n[1]:.1f}; temporal on forest HH {enl_forest[0]:.1f} HV {enl_forest[1]:.1f}",
          flush=True)
    check_mem("omnibus")
    p, Z = omnibus_strips(A.x, n, v2)
    rej, pstar = sc.bh(p, v2, q)
    # calibration: share of 'stable' pixels with p < alpha (no fitting: theoretical chi2 null)
    forest = C["forest"] & v2
    cal_ = {"forest_theory_enl": fa_rate(p, forest), "lake_core": fa_rate(p, lake)}
    # forest with the per-pixel temporal-ENL-matched null (how far the data are from pure speckle)
    p_f, _ = omnibus_strips(A.x, enl_forest, v2)
    cal_["forest_temporal_enl"] = fa_rate(p_f, forest)
    del p_f
    # single date pairs: two consecutive dates on forest with the exact F test (HH only), per pair
    pairs = []
    for t in range(1, T):
        pp = sc.pair_pvalue_exact(torch.nan_to_num(A.x[t, 0][forest].double(), nan=1.0),
                                  torch.nan_to_num(A.x[t - 1, 0][forest].double(), nan=1.0), n[0])
        pairs.append(float((pp < 0.01).double().mean()))
    cal_["forest_pairwise_hh_fa_at_0.01"] = pairs
    check_mem("sequential")
    seq = sequential_strips(A.x, n, rej, q)
    nchg = seq["n_changes"]
    dom = (A.prev_dom if A.prev_dom is not None else v2) & v2
    res = {"enl_used": n, "enl_forest_temporal": enl_forest, "bh_pstar": pstar,
           "changed_km2": float((rej & dom).sum()) * A.km2, "footprint_km2": float(dom.sum()) * A.km2,
           "changed_share": float((rej & dom).sum() / dom.sum()),
           "n_changes_km2": {str(k): float(((nchg == k) & dom).sum()) * A.km2 for k in range(0, 6)},
           "n_changes_5plus_km2": float(((nchg >= 5) & dom).sum()) * A.km2,
           "largest_change_km2_by_date": {A.dates[k]: float(((seq["largest"] == k) & rej & dom).sum()) * A.km2 for k in range(1, T)},
           "calibration_fa": cal_}
    # compare with the preview statistical map
    ps = ROOT / "data/processed" / A.src / "stats/products.pt"
    if ps.exists():
        P = torch.load(ps, weights_only=False, map_location="cpu")
        if "changed_fdr" in P:
            pc = from_preview(P["changed_fdr"].float(), P["bounds"], A.grid) > 0.5
            pl = from_preview(P["largest_change"].float(), P["bounds"], A.grid, "nearest")
            both = pc & rej & dom
            res["vs_preview"] = {"preview_changed_km2": float((pc & dom).sum()) * A.km2,
                                 "both_km2": float(both.sum()) * A.km2,
                                 "calibrated_only_km2": float((rej & ~pc & dom).sum()) * A.km2,
                                 "preview_only_km2": float((pc & ~rej & dom).sum()) * A.km2,
                                 "same_largest_date_share": float((pl[both] == seq["largest"][both].float()).double().mean())
                                 if both.any() else None}
    res["seconds"] = time.time() - t0
    save_results(A.area, "change", res)
    torch.save({"changed_fdr": rej, "largest_change": seq["largest"], "n_changes": nchg, "first_change": seq["first"],
                "direction": seq["direction"], "p_omnibus": p.float(), "enl": n, "dates": A.dates},
               det_dir(A.area) / "change.pt")
    fig_change(A, p, forest, lake, rej, seq, res)
    return res


def fig_change(A, p, forest, lake, rej, seq, res, step=3):
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    fig, axs = plt.subplots(1, 3, figsize=(15, 4.8))
    ax = axs[0]
    al = np.logspace(-4, 0, 40)
    for nm, m, col in (("upland forest", forest, "#2b7a3d"), ("lake core", lake, "#1f4e9c")):
        v = p[m]
        v = v[:: max(1, v.numel() // 2_000_000)]
        obs = [float((v < a).double().mean()) for a in al]
        ax.loglog(al, np.maximum(obs, 1e-6), color=col, label=f"{nm} (theoretical gamma null, ENL {res['enl_used'][0]:.0f}/{res['enl_used'][1]:.0f})")
    ax.loglog(al, al, "k--", lw=0.8, label="nominal")
    ax.set_xlabel("nominal false-alarm rate α (omnibus test, all dates)")
    ax.set_ylabel("observed share with p < α")
    ax.legend(fontsize=7)
    ax.set_title("Is the test calibrated on stable targets?", fontsize=9)
    T = A.T
    cols = plt.get_cmap("viridis", T - 1)
    img = np.full(rej.shape, -1, np.int16)
    big = seq["largest"].numpy()
    img[rej.numpy()] = big[rej.numpy()]
    cm = ListedColormap(["#f0f0f0"] + [cols(i) for i in range(T - 1)])
    axs[1].imshow(img[::step, ::step] + 1, cmap=cm, vmin=0, vmax=T - 1, interpolation="nearest")
    axs[1].set_title(f"Significant change (FDR 1 %): date of the largest change\n{res['changed_km2']:,.0f} km² "
                     f"({res['changed_share'] * 100:.1f} % of the footprint)", fontsize=9)
    nc = seq["n_changes"].numpy().astype(float)
    nc[~A.v2.numpy()] = np.nan
    im = axs[2].imshow(nc[::step, ::step], cmap="magma_r", vmin=0, vmax=5, interpolation="nearest")
    axs[2].set_title("Number of significant changes per pixel", fontsize=9)
    plt.colorbar(im, ax=axs[2], fraction=0.04)
    for a in axs[1:]:
        a.set_xticks([]); a.set_yticks([])
    fig.tight_layout()
    savefig(fig, f"{A.area}_change.png")


# ======================================================================================= step: resolution
CROPS = {"tonle-sap": [("Prek Toal shore", 103.72, 13.12, 8), ("south-east river corridor", 104.85, 12.0, 8),
                       ("Kampong Chhnang town and river", 104.67, 12.25, 6)],
         "sevan": [("Gavar shore", 45.16, 40.43, 4), ("Sevan town and peninsula", 44.97, 40.55, 4)],
         "ararat": [("Masis fields", 44.43, 40.07, 4), ("Ararat-Vedi fish ponds", 44.6, 39.9, 4)]}


def _components(m: torch.Tensor):
    from scipy import ndimage
    lab, n = ndimage.label(m.numpy(), structure=np.ones((3, 3), bool))
    cnt = np.bincount(lab.ravel())[1:] if n else np.array([])
    return n, cnt


def _perim_km(m: torch.Tensor, px_km: float) -> float:
    m8 = m.to(torch.int8)
    return float((m8[1:] != m8[:-1]).sum() + (m8[:, 1:] != m8[:, :-1]).sum()) * px_km


def step_res(A: Area, t: int | None = None) -> dict:
    """Preview (180 m) vs calibrated (80 m) on the same crops: water bodies, smallest body, shoreline length,
    edge sharpness (10-90 % rise distance of HH across the lake shore)."""
    import matplotlib.pyplot as plt
    from scripts.detect.stack import load_preview_stack
    wp = det_dir(A.area) / "water.pt"
    if not wp.exists():
        print("  run --step water first"); return {}
    W = torch.load(wp, weights_only=False)["water"]
    t = A.T - 1 if t is None else t
    P = torch.load(ROOT / "data/processed" / A.src / "math/products.pt", weights_only=False, map_location="cpu")
    pi = P["dates"].index(A.dates[t]) if A.dates[t] in P["dates"] else t
    st = load_preview_stack(A.src)
    pw = P["water"][pi]
    pkm = P["km2_per_px"]
    crops = CROPS.get(A.src, [])
    res = {"date": A.dates[t], "crops": {}}
    fig, axs = plt.subplots(len(crops), 4, figsize=(14, 3.6 * len(crops)), squeeze=False)
    from rasterio.transform import rowcol
    ptr = preview_transform(P["bounds"], pw.shape[0], pw.shape[1])
    for i, (name, lon, lat, half) in enumerate(crops):
        r0, r1, c0, c1 = _crop_box(A, lon, lat, half)
        cw = W[t, r0:r1, c0:c1]
        chh = db(A.x[t, 0, r0:r1, c0:c1])
        pr, pc = rowcol(ptr, lon, lat)
        h = int(half * 1000 / math.sqrt(pkm * 1e6))
        pr0, pr1, pc0, pc1 = max(pr - h, 0), pr + h, max(pc - h, 0), pc + h
        pwc = pw[pr0:pr1, pc0:pc1]
        phh = st.x[pi, 0, pr0:pr1, pc0:pc1]
        nc, cc = _components(cw)
        npc, pcc = _components(pwc)
        res["crops"][name] = {
            "calibrated": {"water_km2": float(cw.sum()) * A.km2, "n_bodies": int(nc),
                           "smallest_body_km2": float(cc.min()) * A.km2 if nc else None,
                           "bodies_under_0.1km2": int((cc * A.km2 < 0.1).sum()) if nc else 0,
                           "shoreline_km": _perim_km(cw, A.pixel_m / 1000)},
            "preview": {"water_km2": float(pwc.sum()) * pkm, "n_bodies": int(npc),
                        "smallest_body_km2": float(pcc.min()) * pkm if npc else None,
                        "bodies_under_0.1km2": int((pcc * pkm < 0.1).sum()) if npc else 0,
                        "shoreline_km": _perim_km(pwc, math.sqrt(pkm))}}
        axs[i, 0].imshow(phh.numpy(), cmap="gray", vmin=0, vmax=0.7)
        axs[i, 0].set_title(f"{name}: preview HH, 180 m", fontsize=9)
        axs[i, 1].imshow(chh.numpy(), cmap="gray", vmin=-25, vmax=-3)
        axs[i, 1].set_title("calibrated HH dB, 80 m", fontsize=9)
        axs[i, 2].imshow(pwc.numpy(), cmap="Blues", vmin=0, vmax=1.3, interpolation="nearest")
        rp = res["crops"][name]["preview"]
        axs[i, 2].set_title(f"preview water: {rp['n_bodies']} bodies, shore {rp['shoreline_km']:.0f} km", fontsize=9)
        axs[i, 3].imshow(cw.numpy(), cmap="Blues", vmin=0, vmax=1.3, interpolation="nearest")
        rc = res["crops"][name]["calibrated"]
        axs[i, 3].set_title(f"calibrated water: {rc['n_bodies']} bodies, shore {rc['shoreline_km']:.0f} km", fontsize=9)
        for a in axs[i]:
            a.set_xticks([]); a.set_yticks([])
    fig.suptitle(f"Same places, same date ({A.dates[t]}): preview vs calibrated", fontsize=11)
    fig.tight_layout()
    savefig(fig, f"{A.area}_resolution.png")
    # whole-scene: smallest bodies, total shoreline, inside the preview footprint
    dom = (A.prev_dom if A.prev_dom is not None else A.v2) & A.v2
    n_c, cc = _components(W[t] & dom)
    n_p, pcc = _components(pw & P["valid"][pi])
    res["scene"] = {"calibrated": {"n_bodies": int(n_c), "bodies_under_0.1km2": int((cc * A.km2 < 0.1).sum()),
                                   "shoreline_km": _perim_km(W[t] & dom, A.pixel_m / 1000)},
                    "preview": {"n_bodies": int(n_p), "bodies_under_0.1km2": int((pcc * pkm < 0.1).sum()),
                                "shoreline_km": _perim_km(pw & P["valid"][pi], math.sqrt(pkm))}}
    # edge sharpness: HH profile across the lake shore (distance from the JRC permanent-lake edge)
    perm = A.lab["perm"] | (A.ref["occ_ge90_frac"] > 0.5)
    res["edge"] = edge_width(db(A.x[t, 0]), perm & A.v2, A.v2, A.pixel_m)
    save_results(A.area, "resolution", res)
    return res


def edge_width(hh_db, lake, valid, pixel_m, K=8):
    """Median HH (dB) at signed distance d px from the lake edge (d<0 inside) and the 10-90 % rise width in metres."""
    din = floodveg.dist_px(~lake[None] & valid[None], K)[0]
    dout = floodveg.dist_px(lake[None], K)[0]
    prof = {}
    for d in range(-K, K + 1):
        m = valid & ((din == -d) & lake if d < 0 else (dout == d) & ~lake if d > 0 else torch.zeros_like(lake))
        if d == 0 or m.sum() < 100:
            continue
        prof[d] = float(hh_db[m].median())
    ks = sorted(prof)
    v = np.array([prof[k] for k in ks])
    lo, hi = v[:3].mean(), v[-3:].mean()
    f = (v - lo) / max(hi - lo, 1e-6)
    x10 = float(np.interp(0.1, f, ks)) if np.all(np.diff(f) > -0.2) else None
    x90 = float(np.interp(0.9, f, ks)) if np.all(np.diff(f) > -0.2) else None
    return {"profile_db": {str(k): prof[k] for k in ks},
            "rise_10_90_m": (x90 - x10) * pixel_m if x10 is not None and x90 is not None else None}


# ======================================================================================= step: lake (Sevan)
LAKE_TARGET_KM2 = {"sevan": 1242.0}


def step_lake(A: Area, weaks=(-18.0, -17.5, -17.0, -16.5, -16.0, -15.5, -15.0)) -> dict:
    """Lake area per date with the Tonle Sap knobs unchanged (one dB rule for both terrains?). Lake = the water
    components that touch JRC permanent water; false water = water on never-wet land. Also lake HH/HV levels
    per date (the preview showed an HV anomaly on 6 October at Sevan) and the area across weak thresholds."""
    import matplotlib.pyplot as plt
    kn = load_gamma0_knobs()
    kn["min_blob_px"] = max(1, int(round(kn["min_blob_px"] * (80.0 / A.pixel_m) ** 2)))  # same area in km2
    kn["refine_px"] = int(round(kn.get("refine_px", 0) * 80.0 / A.pixel_m))
    check_mem("lake filter")
    L = filtered_db(A.x, A.v2, kn["box"], kn["quegan"])
    raw = torch.stack([db(torch.nan_to_num(A.x[t, 0], nan=1e-10)) for t in range(A.T)])
    perm, never = A.lab["perm"], A.lab["never"] & A.v2
    core = ops.erode(perm[None], 5)[0] & A.v2

    def run(k):
        w = water_db(L, A.v2, k, A.water_ok)
        if k.get("refine_px", 0):
            w = refine_shore(w, raw, A.v2, k["water_hh_weak"], k["refine_px"])
            if A.water_ok is not None:
                w = w & A.water_ok[None]
        lake = ops.reconstruct(w & perm[None], w)
        return w, lake
    w, lake = run(kn)
    res = {"dates": A.dates, "knobs": kn, "target_km2": LAKE_TARGET_KM2.get(A.src),
           "lake_km2": water_km2(lake, A.v2, A.km2), "all_water_km2": water_km2(w, A.v2, A.km2),
           "false_water_never_km2": water_km2(w, never, A.km2),
           "lake_hh_median_db": [float(L[t, 0][core].median()) for t in range(A.T)],
           "lake_hv_median_db": [float(L[t, 1][core].median()) for t in range(A.T)],
           "lake_hh_p95_db": [quant(L[t, 0][core], [0.95])[0] for t in range(A.T)],
           "land_hh_p01_db": [quant(L[t, 0][never], [0.01])[0] for t in range(A.T)]}
    sw = []
    for wk in weaks:
        _, lk = run(dict(kn, water_hh_weak=float(wk), water_hh_strong=float(min(kn["water_hh_strong"], wk - 3.0))))
        sw.append({"weak": wk, "lake_km2": water_km2(lk, A.v2, A.km2)})
    res["sweep"] = sw
    # diagnostic: HH only (no HV guard), thresholds near this scene's own lake level (NOT a transferable rule)
    sw2 = []
    for wk in (-15.0, -14.5, -14.0, -13.5, -13.0):
        wq, lk2 = run(dict(kn, water_hh_weak=wk, water_hh_strong=wk - 1.5, water_hv_max=99.0))
        sw2.append({"weak": wk, "strong": wk - 1.5, "lake_km2": water_km2(lk2, A.v2, A.km2),
                    "false_water_never_km2": water_km2(wq, never, A.km2)})
        del wq, lk2
    res["sweep_hh_only_scene_levels"] = sw2
    res["scene_hv_land_median_db"] = [float(L[t, 1][never].median()) for t in range(A.T)]
    # scene-relative alternative: the seeded detector with the dB levels (no absolute threshold at all)
    from scripts.detect import crossscene as cs
    check_mem("lake seeded")
    rs = cs.detect_water_adaptive(L, A.v2[None].expand(A.T, -1, -1).contiguous(), A.water_ok, method="seeded",
                                  source="gamma0", kn=SEEDED_DB)
    ws = rs["water"] & A.v2[None]
    lk = ops.reconstruct(ws & perm[None], ws)
    res["seeded_db"] = {"lake_km2": water_km2(lk, A.v2, A.km2), "all_water_km2": water_km2(ws, A.v2, A.km2),
                        "false_water_never_km2": water_km2(ws, never, A.km2),
                        "equiv_hh_thresholds_db": [seeded_equiv_db(rs["diag"], th) for th in rs["thresholds"]],
                        "feature": (rs["diag"].get("mode") or {}).get("feature")}
    del rs, ws, lk
    save_results(A.area, "lake", res)
    fig, axs = plt.subplots(1, 2, figsize=(12, 4.6))
    t = int(np.argmax(res["lake_km2"]))
    img = np.where(A.v2.numpy(), np.clip((L[t, 0].numpy() + 25) / 22, 0, 1), 1.0)
    axs[0].imshow(img, cmap="gray", vmin=0, vmax=1)
    axs[0].contour(lake[t].numpy().astype(float), levels=[0.5], colors="#2f6fde", linewidths=0.6)
    axs[0].set_title(f"{A.area} {A.dates[t]}: HH dB with the lake outline ({res['lake_km2'][t]:,.0f} km²)", fontsize=9)
    axs[0].set_xticks([]); axs[0].set_yticks([])
    ax = axs[1]
    for r in sw:
        ax.plot(range(A.T), r["lake_km2"], lw=0.8, alpha=0.6, label=f"weak {r['weak']:.1f} dB")
    ax.plot(range(A.T), res["lake_km2"], "o-", color="k", label="recommended knobs")
    ax.plot(range(A.T), res["seeded_db"]["lake_km2"], "^-", color="#16a085", label="seeded, scene-relative (SEEDED_DB)")
    if res["target_km2"]:
        ax.axhspan(res["target_km2"] - 107, res["target_km2"] + 107, color="#c0392b", alpha=0.12, label="published 1,242 ± 107 km²")
    ax.set_xticks(range(A.T)); ax.set_xticklabels([d[5:] for d in A.dates], fontsize=8)
    ax.set_ylabel("lake area km²")
    ax.legend(fontsize=7)
    fig.tight_layout()
    savefig(fig, f"{A.area}_lake.png")
    return res


# ======================================================================================= knob file
ARMENIA_NOTE = ("These water thresholds are for the 20+5 MHz GCOV mode (Tonle Sap, noise floor ~-27 dB HV). The "
                "40+5 MHz Armenia product has a ~7-8 dB higher floor: Lake Sevan sits at HH -15.3 / HV -17 dB and these "
                "knobs find 0 km2. Sevan-level HH-only diagnostic: strong -15.5 / weak -14.0, HV guard off -> "
                "1,210-1,239 km2 (target 1,242) with 26-60 km2/date false water. Use mode-specific knobs.")


def write_knobs(area: str):
    """gamma0_knobs.json: the recommended calibrated knobs with a reason each. The water thresholds are the
    tuned values (search under a 0.02 % never-wet false-alarm budget) when they fall inside the physically
    defensible interval; otherwise the physical rule PHYS."""
    R = load_results().get(area, {})
    W = R.get("water", {})
    wk = W.get("best_knobs", {})
    ch = R.get("change", {})
    lo, hi = (W.get("sensitivity", {}) or {}).get("strict_interval_db", [-17.75, -15.4])
    use_tuned = bool(wk) and lo - 0.5 <= wk.get("water_hh_weak", 0) <= hi and wk.get("water_hh_strong", 0) <= -19.0
    src = wk if use_tuned else PHYS
    kn = dict(CAL_KNOBS)
    kn.update({k: v for k, v in src.items() if k in ("water_hh_strong", "water_hh_weak", "water_hv_max", "box",
                                                    "min_blob_px", "hv_w", "refine_px")})
    kn["open_k"] = 0
    kn["normalise"] = False
    kn["untested"] = False
    why = {
        "water_hh_strong": "seed: about 1 dB below the median of calm lake water (-19.5 dB HH, 3x3 filtered, 19 Jun); "
                           "lower values leave windy dates without seeds",
        "water_hh_weak": f"growth limit: inside [{lo:.1f}, {hi:.1f}] dB = [95 % of calm lake, 1 % of never-wet land]; "
                         "the floodplain histogram valley sits at -17 dB on calm dates",
        "water_hv_max": "HV of the lake sits at the noise floor (-25.8 dB, NESZ about -27 dB); -24 dB keeps wind-roughened "
                        "water (HV barely changes) and rejects dark fields (HV -16 dB)",
        "box": "3x3 Quegan at 80 m (240 m); the data already average 16 x ~9.5 looks (measured ENL 60-100); 7 blurs shores",
        "quegan": "multi-temporal filter in linear power (unbiased); never average in dB",
        "min_blob_px": "9 px = 0.06 km2 at 80 m",
        "refine_px": "2-px shoreline refinement on the unfiltered HH: power-domain filtering shrinks shores by ~1 px",
        "open_k": "0: an opening of 3 deletes every channel narrower than 240 m",
        "normalise": "False: stable upland forest varies by 0.16 dB (HH) / 0.09 dB (HV) std between dates",
        "edge_erode_px": "2 px of swath edge removed",
        "fv_hh_rise": "simple ops rule only (kept for comparison); the app should use fv_db (FV_KNOBS_db)",
    }
    doc = {"generated": dt.datetime.now().isoformat(timespec="seconds"), "from_area": area,
           "dates_used": W.get("dates"), "water_rule_source": "tuned (budgeted search)" if use_tuned else "physical rule PHYS",
           "KNOBS_gamma0": kn, "reasons": why,
           "water_test_strip": (W.get("test", {}).get("tuned" if use_tuned else "physical", {}) or {}).get("point"),
           "FV_KNOBS_db": fv_knobs(80.0), "FV_KNOBS_db_note": "distances in px for 80 m; calibrated.fv_knobs(pixel_m) scales them",
           "HV_BINS_DB": list(HV_BINS_DB),
           "SEEDED_DB": SEEDED_DB,
           "mode_note": ARMENIA_NOTE,
           "stats_change": {"enl_used": ch.get("enl_used"), "enl_note": "spatial ENL on the calm lake core, per channel",
                            "null": "theoretical chi2 at the measured ENL; NOT at nominal level over 10 dates on stable forest (17 % at alpha 0.01; pairs ~1 %): add a minimum effect size, see calibrated.md section 7b"},
           "DISPLAY_gamma0": {"grey": {"hh": [-25, -3], "hv": [-30, -8]}, "change": {"thr": 1.5, "full": 6.0}}}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "gamma0_knobs.json").write_text(json.dumps(_r(doc, 3), indent=1))
    print("  wrote", (OUT / "gamma0_knobs.json").relative_to(ROOT))


# ======================================================================================= run.py entry point
def load_gamma0_knobs(path=None) -> dict:
    """The recommended calibrated knob set (docs/eda/calibrated_det/gamma0_knobs.json), else CAL_KNOBS."""
    p = pathlib.Path(path) if path else OUT / "gamma0_knobs.json"
    kn = dict(CAL_KNOBS)
    if p.exists():
        kn.update(json.loads(p.read_text())["KNOBS_gamma0"])
    return kn


def run_pipeline_gamma0(x: torch.Tensor, valid: torch.Tensor, pixel_m: float, water_ok=None, kn=None,
                        dev=None) -> dict:
    """Drop-in for ops.run_pipeline(x, valid, "gamma0") in scripts/detect/run.py. Same output keys.

    = ops.run_pipeline with the calibrated knobs (filter, CUSUM change, classes), then
      water        <- hysteresis in dB + shoreline refinement on the unfiltered HH (refine_shore),
      flooded_veg  <- the rebuilt rule in dB (fv_db), fv_state added,
      wind_suspect and classes recomputed.
    Run it on the CPU for a full 80 m stack (ops copies the stack several times)."""
    kn = dict(kn or load_gamma0_knobs())
    kn["open_k"] = 0
    dev = dev or torch.device("cpu")
    out = ops.run_pipeline(x, valid, "gamma0", kn=kn, dev=dev, water_ok=water_ok)
    v = out["valid"]
    T = v.shape[0]
    w = out["water"]
    if kn.get("refine_px", 0):
        raw = torch.stack([db(torch.nan_to_num(x[t, 0].to(dev), nan=1e-10)) for t in range(T)])
        w = refine_shore(w, raw, v.all(0), kn["water_hh_weak"], kn["refine_px"]) & v
        del raw
        if water_ok is not None:
            w = w & water_ok.to(w.device)[None]
    out["water"] = w
    fvp = fv_db(out["L"], v, w, pixel_m, water_ok)
    out["flooded_veg"] = fvp["flooded_veg"].to(w.device)
    out["fv_state"] = fvp["fv_state"].to(w.device)
    out["wind_suspect"] = ops.wind_suspect(w, v, kn)
    out["classes"] = ops.classify(w, out["flooded_veg"], v, out["change_date"], kn)
    out["knobs"] = kn
    return out


def terrain_on_grid(src: str, grid) -> torch.Tensor | None:
    """The preview-grid terrain mask (data/processed/<src>/terrain.pt, 'water_possible') on a calibrated grid
    (nearest); True outside the preview grid. grid: anything with crs, transform, h, w."""
    p = ROOT / "data/processed" / src / "terrain.pt"
    if not p.exists():
        return None
    t = torch.load(p, weights_only=False, map_location="cpu")
    g = Grid(grid.crs, grid.transform, grid.h, grid.w)
    m = from_preview(t["water_possible"].float(), t["bounds"], g)
    return (m > 0.5) | ~torch.isfinite(m)


# ======================================================================================= CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("area", nargs="?", default="tonle-sap-cal")
    ap.add_argument("--step", default="all", choices=["look", "water", "sep", "fv", "change", "res", "knobs", "lake", "all"])
    ap.add_argument("--evals", type=int, default=240)
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--no-seeded", action="store_true", help="water step: skip the crossscene seeded detector")
    a = ap.parse_args(argv)
    torch.set_num_threads(max(1, torch.get_num_threads()))
    global SEEDED_ON
    SEEDED_ON = not a.no_seeded
    if a.synthetic:
        save_results("synthetic", "check", synthetic_check())
        return
    A = Area(a.area)
    steps = ["look", "water", "fv", "change", "res", "knobs"] if a.step == "all" else [a.step]
    for s in steps:
        print(f"== {a.area}: {s}", flush=True)
        if s == "look":
            step_look(A)
        elif s == "water":
            step_water(A, a.evals)
        elif s == "sep":
            kn = best_water_knobs(A)
            fig_separation(A, WaterCtx(A, boxes=(kn["box"],)), kn)
        elif s == "fv":
            step_fv(A)
        elif s == "lake":
            step_lake(A)
        elif s == "change":
            step_change(A)
        elif s == "res":
            step_res(A)
        else:
            write_knobs(a.area)
        gc.collect()


if __name__ == "__main__":
    main()
