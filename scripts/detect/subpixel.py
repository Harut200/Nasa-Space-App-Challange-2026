"""Sub-pixel water fraction at the shoreline: linear mixing of radar intensity, checked against 30 m truth.

    .venv/bin/python -m scripts.detect.subpixel            # sevan, tonle-sap, ararat (one after another)
    .venv/bin/python -m scripts.detect.subpixel sevan      # one area

Every ~180 m pixel on a shoreline is part water, part land. The detector (ops.water_masks) calls it all
water or all land. Here each shoreline pixel gets a water fraction f in [0, 1] from a two-end-member linear
mixing model in the (pseudo-)power domain:

    I = f * I_w + (1 - f) * I_l    ->    f = clip((I_l - I) / (I_l - I_w), 0, 1)

I_w = pure-water intensity of the date (local mean over the eroded interior of the detected water),
I_l = local land intensity (trimmed mean of confident land in a ring around the pixel, other shoreline
pixels excluded). Preview brightness b is turned into a pseudo-intensity I = 10**(D*b/10) with an assumed
stretch D (dB) as in stats_change.pseudo_intensity; gamma0 input is already in dB.

Integration:  water_fraction(L, valid, water, source="preview", prior_water=None) -> dict
Validation:   JRC Global Surface Water at 30 m, area-weighted onto our grid (validate.reference_on_grid).
Outputs:      data/processed/<area>/subpixel/products.pt, docs/eda/subpixel/{*.png, results.json}.
Write-up:     docs/eda/subpixel.md.
"""
from __future__ import annotations

import json
import math
import sys
import time

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402
import torch.nn.functional as F  # noqa: E402

from scripts.detect import ops  # noqa: E402
from scripts.detect.stack import ROOT  # noqa: E402

DT = torch.float32
FLOOR = 1.5 / 255           # preview display floor (as stats_change.FLOOR)
DB_RANGE = 30.0             # assumed dB span of the preview stretch (stats_change.PREVIEW_DB_RANGE)
SEVAN_KM2 = 1242.0          # published
SEVAN_JRC90_KM2 = 1239.7    # JRC occurrence >= 90 %, area-weighted (docs/eda/validation.md)
SEVAN_WINDY = (5, 6)        # 31 Aug, 12 Sep 2026
OUT = ROOT / "docs/eda/subpixel"

# knobs of the fraction estimator (not tuned on the reference; see docs/eda/subpixel.md)
KN = {
    "band_px": 1,        # shoreline band = pixels within band_px of the detected water edge (both sides)
    "land_r": 5,         # land end-member: ring of radius land_r px (11 x 11 window) ...
    "land_gap": 2,       # ... only pixels more than band_px + land_gap - 1 px from the detected water
    "land_trim": 0.2,    # trimmed mean: drop the lowest and highest 20 % of the ring
    "land_min": 8,       # at least 8 land pixels, else widen to land_r2
    "land_r2": 10,
    "water_erode": 2,    # pure water: detected water eroded by 2 px (5 x 5) ...
    "water_win": 31,     # ... local mean in a 31 x 31 window (wind is patchy), global median as fallback
    "min_contrast": 0.1,     # land - water must exceed 0.1 in log-like units (= 3 dB at a 30 dB stretch;
                             # gamma0: 3 dB), else the pixel keeps its hard value
}


# ====================================================================== domain
def to_power(b: torch.Tensor, source: str = "preview", db_range: float = DB_RANGE) -> torch.Tensor:
    """Pseudo-intensity (linear power up to a constant) from log-like input.

    preview: b = display brightness 0..1, assumed b ~ (dB - dB_min)/db_range -> I = 10**(db_range*b/10)
             (stats_change.pseudo_intensity; b clamped at FLOOR/3).
    gamma0 : L is 10*log10(power) -> I = 10**(L/10).
    log    : no conversion (mixing done, wrongly, on the log-like values; for comparison only).
    """
    if source == "preview":
        return torch.pow(10.0, db_range * b.to(DT).clamp_min(FLOOR / 3) / 10.0)
    if source == "gamma0":
        return torch.pow(10.0, b.to(DT) / 10.0)
    if source == "log":
        return b.to(DT)
    raise ValueError(source)


# ====================================================================== geometry helpers
def shoreline_band(water: torch.Tensor, valid: torch.Tensor, k: int = 1) -> torch.Tensor:
    """[T,H,W] bool: valid pixels within k px (8-neighbourhood) of the water/land edge, both sides."""
    kk = 2 * k + 1
    return ops.dilate(water, kk) & ~ops.erode(water, kk) & valid


def _offsets(r: int, r_in: int = 0):
    d = torch.arange(-r, r + 1)
    dy, dx = torch.meshgrid(d, d, indexing="ij")
    keep = torch.maximum(dy.abs(), dx.abs()) >= r_in
    return dy[keep], dx[keep]


def _gather(img: torch.Tensor, rr: torch.Tensor, cc: torch.Tensor, dy, dx):
    """Values of img [H,W] at (rr+dy, cc+dx) -> [N, K]; out-of-frame -> nan."""
    H, W = img.shape
    r = rr[:, None] + dy[None]
    c = cc[:, None] + dx[None]
    ok = (r >= 0) & (r < H) & (c >= 0) & (c < W)
    v = img[r.clamp(0, H - 1), c.clamp(0, W - 1)]
    return torch.where(ok, v, torch.full_like(v, float("nan")))


def _trimmed_mean(v: torch.Tensor, trim: float):
    """Row-wise trimmed mean of [N,K] ignoring nan. Returns (mean [N], count [N])."""
    n = (~torch.isnan(v)).sum(1)
    s = torch.where(torch.isnan(v), torch.full_like(v, float("inf")), v).sort(1).values
    k = torch.arange(v.shape[1])[None]
    lo = (n.float() * trim).floor().long()[:, None]
    hi = (n[:, None] - lo)
    sel = (k >= lo) & (k < hi)
    s = torch.where(sel, s, torch.zeros_like(s))
    m = s.sum(1) / sel.sum(1).clamp_min(1)
    return torch.where(n > 0, m, torch.full_like(m, float("nan"))), n


# ====================================================================== end-members
def water_endmember(I: torch.Tensor, water: torch.Tensor, valid: torch.Tensor, kn=KN):
    """Pure-water intensity I_w [H,W] for one date: mean of I over the detected water eroded by
    `water_erode` px, locally (water_win box) where enough interior water is near, else the global
    median of that interior. Mean (not median) because mixing is linear in the MEAN power."""
    core = ops.erode(water[None], 2 * kn["water_erode"] + 1)[0] & valid
    if core.sum() < 20:
        core = water & valid
    if core.sum() == 0:
        return torch.full_like(I, float("nan")), float("nan")
    g = float(I[core].median())
    k = kn["water_win"]
    w = torch.ones(1, 1, k, k, dtype=DT)
    num = F.conv2d((I * core)[None, None], w, padding=k // 2)[0, 0]
    den = F.conv2d(core.to(DT)[None, None], w, padding=k // 2)[0, 0]
    loc = torch.where(den >= 25, num / den.clamp_min(1), torch.full_like(I, g))
    return loc, g


def land_endmember(I: torch.Tensor, water: torch.Tensor, valid: torch.Tensor, rr, cc,
                   prior_water=None, kn=KN):
    """Local land intensity I_l [N] at band pixels (rr, cc): trimmed mean of I over confident land
    (valid, farther than band_px + land_gap - 1 px from detected water, not prior water) in a
    (2 land_r + 1)^2 window; widened to land_r2 where fewer than land_min land pixels were found."""
    k = 2 * (kn["band_px"] + kn["land_gap"]) - 1
    land = valid & ~ops.dilate(water[None], k)[0]
    if prior_water is not None:
        land = land & ~prior_water
    Iland = torch.where(land, I, torch.full_like(I, float("nan")))
    out, n = _trimmed_mean(_gather(Iland, rr, cc, *_offsets(kn["land_r"])), kn["land_trim"])
    few = n < kn["land_min"]
    if few.any():
        o2, n2 = _trimmed_mean(_gather(Iland, rr[few], cc[few], *_offsets(kn["land_r2"])), kn["land_trim"])
        o2 = torch.where(n2 >= kn["land_min"], o2, torch.full_like(o2, float("nan")))
        out[few] = o2
    return out




# ====================================================================== fraction
def _loglike_contrast_ok(Il, Iw, domain, source, db_range, kn):
    """Land must be brighter than water by min_contrast in log-like units (0.1 preview, 3 dB gamma0)."""
    ok = torch.isfinite(Il) & torch.isfinite(Iw)
    if domain == "power":
        span = db_range if source == "preview" else 1.0
        mc = kn["min_contrast"] if source == "preview" else 3.0
        return ok & (Il > Iw * 10 ** (span * mc / 10))
    mc = kn["min_contrast"] if source == "preview" else 3.0
    return ok & (Il - Iw > mc)


def fraction_date(X: torch.Tensor, water: torch.Tensor, valid: torch.Tensor, prior_water=None,
                  source="preview", domain="power", db_range=DB_RANGE, kn=KN):
    """Water fraction for one date and one channel.

    X [H,W] log-like values (preview brightness or dB). Returns dict with f [H,W] (1 inside the detected
    water, 0 outside, mixing model in the band), band [H,W], the band pixel indices rr, cc, the unclipped
    estimate raw, ok (enough land/water contrast) and sig2 = mean squared error of the unclipped estimator
    on the ring of pixels one step beyond the band on both sides, where the truth is 1 (water side) or 0
    (land side) without any reference data -- used to weight the channels.
    """
    I = (to_power(X, source, db_range) if domain == "power" else X.to(DT)) * valid
    band = shoreline_band(water[None], valid[None], kn["band_px"])[0]
    k = kn["band_px"]
    ring_in = ops.erode(water[None], 2 * k + 1)[0] & ~ops.erode(water[None], 2 * k + 3)[0] & valid
    ring_out = ops.dilate(water[None], 2 * k + 3)[0] & ~ops.dilate(water[None], 2 * k + 1)[0] & valid
    if prior_water is not None:
        ring_out = ring_out & ~prior_water
    sel = band | ring_in | ring_out
    rr, cc = torch.nonzero(sel, as_tuple=True)
    Iw_map, _ = water_endmember(I, water, valid, kn)
    Iw = Iw_map[rr, cc]
    Il = land_endmember(I, water, valid, rr, cc, prior_water, kn)
    ok = _loglike_contrast_ok(Il, Iw, domain, source, db_range, kn)
    raw = (Il - I[rr, cc]) / (Il - Iw).clamp_min(1e-9)
    hard = water[rr, cc].to(DT)
    inb = band[rr, cc]
    e = (raw - hard)[ok & ~inb]
    sig2 = float((e ** 2).mean()) if e.numel() > 10 else float("nan")
    rr, cc, raw, ok, hard = rr[inb], cc[inb], raw[inb], ok[inb], hard[inb]
    f = water.to(DT) * valid
    f[rr, cc] = torch.where(ok, raw.clamp(0, 1), hard)
    return {"f": f, "band": band, "rr": rr, "cc": cc, "raw": torch.where(ok, raw, hard), "ok": ok,
            "sig2": sig2}


def water_fraction(L: torch.Tensor, valid: torch.Tensor, water: torch.Tensor, source: str = "preview",
                   prior_water=None, x_frac=None, db_range: float = DB_RANGE, kn=None,
                   km2_per_px: float | None = None, domain: str = "auto", channels: str = "combined") -> dict:
    """Sub-pixel water fraction for a whole stack (integration entry point).

    L [T,2,H,W] log-like (ops.run_pipeline 'L'; ch0 HH, ch1 HV), valid [T,H,W], water [T,H,W] (detected
    open water), prior_water [H,W] optional (kept out of the land end-member).
    x_frac [T,2,H,W] optional: log-like values used for the FRACTION (default L). The CLI passes the
    unfiltered, gain-normalised preview (results are the same to 0.01 at Sevan: the Quegan filter keeps
    a stable shoreline sharp).
    domain: "power" = mix in (pseudo-)power I = 10**(D b / 10) (physically right for calibrated data);
            "display" = mix in the log-like values themselves (preview brightness / dB);
            "auto" (default) = "display" for previews, "power" for gamma0 -- see docs/eda/subpixel.md:
            at Lake Sevan the previews behave as if they were averaged AFTER the display stretch.
    channels: "HH", "HV" or "combined" = minimum-variance weights w_c ~ 1/sigma_c^2 from each channel's
            own error on pixels of known class next to the band (no reference data used).

    Returns frac [T,H,W] float16, band [T,H,W] bool, area_km2_frac [T], area_km2_hard [T] (pixel counts
    if km2_per_px is None), weights [T,2], sigma2 [T,2], and the settings.
    """
    kn = dict(KN, **(kn or {}))
    if domain == "auto":
        domain = "display" if source == "preview" else "power"
    X = (L if x_frac is None else x_frac).to(DT).cpu()
    valid, water = valid.cpu(), water.cpu() & valid.cpu()
    pw = prior_water.cpu() if prior_water is not None else None
    T, _, H, W = X.shape
    fr = torch.zeros(T, H, W, dtype=DT)
    band = torch.zeros(T, H, W, dtype=torch.bool)
    wts = torch.zeros(T, 2)
    s2 = torch.full((T, 2), float("nan"))
    chans = {"HH": [0], "HV": [1]}.get(channels, [0, 1])
    for t in range(T):
        parts = {c: fraction_date(X[t, c], water[t], valid[t], pw, source, domain, db_range, kn) for c in chans}
        p0 = parts[chans[0]]
        band[t] = p0["band"]
        if len(chans) == 1:
            fr[t] = p0["f"]
            wts[t, chans[0]] = 1.0
            s2[t, chans[0]] = p0["sig2"]
            continue
        sg = [parts[0]["sig2"], parts[1]["sig2"]]
        inv = [1.0 / v if math.isfinite(v) and v > 0 else 0.0 for v in sg]
        w = [v / sum(inv) for v in inv] if sum(inv) > 0 else [1.0, 0.0]
        wts[t], s2[t] = torch.tensor(w), torch.tensor(sg)
        o0, o1 = parts[0]["ok"], parts[1]["ok"]
        raw = torch.where(o0 & o1, w[0] * parts[0]["raw"] + w[1] * parts[1]["raw"],
                          torch.where(o0, parts[0]["raw"], parts[1]["raw"]))
        f = water[t].to(DT) * valid[t]
        f[p0["rr"], p0["cc"]] = raw.clamp(0, 1)
        fr[t] = f
        del parts
    k = km2_per_px if km2_per_px is not None else 1.0
    return {"frac": fr.half(), "band": band, "area_km2_frac": fr.sum((1, 2)).double() * k,
            "area_km2_hard": water.sum((1, 2)).double() * k, "weights": wts, "sigma2": s2,
            "db_range": db_range, "domain": domain, "channels": channels, "knobs": kn}


# ====================================================================== alternatives
def half_pixel(water: torch.Tensor, valid: torch.Tensor) -> torch.Tensor:
    """(b) every pixel on either side of a 4-neighbour water/land edge counts 0.5, others 0/1."""
    w = water.to(DT) * valid
    p = F.pad(w[:, None], (1, 1, 1, 1), mode="replicate")[:, 0]
    nb = torch.stack([p[:, :-2, 1:-1], p[:, 2:, 1:-1], p[:, 1:-1, :-2], p[:, 1:-1, 2:]])
    edge = ((nb != w[None]).any(0)) & valid
    return torch.where(edge, torch.full_like(w, 0.5), w)


def logistic_conf(L_hh: torch.Tensor, valid: torch.Tensor, thr: float = 0.12, scale: float = 0.035):
    """(c) a probability, not a fraction: Phi((thr - L_hh)/scale) -- the form of
    stats_change.water_confidence; used only when data/processed/<area>/stats/products.pt is missing."""
    return torch.special.ndtr((thr - L_hh.to(DT)) / scale) * valid


def largest_body_region(water: torch.Tensor, k: int) -> torch.Tensor:
    """[T,H,W]: the largest 8-connected water body of each date, dilated by k px (its shoreline band)."""
    lab = ops.label_components(water)
    reg = torch.zeros_like(water)
    for t in range(water.shape[0]):
        c = torch.bincount(lab[t].flatten())
        c[0] = 0
        if c.max() > 0:
            reg[t] = lab[t] == c.argmax()
    return ops.dilate(reg, 2 * k + 1)


# ====================================================================== sub-pixel shoreline
def contour_points(f: torch.Tensor, mask: torch.Tensor, level: float = 0.5) -> torch.Tensor:
    """Points (row, col) in pixel-centre coordinates where f crosses `level` between 4-neighbours,
    by linear interpolation along rows and columns. For a 0/1 mask this is the midpoint of every pixel
    edge between water and land, i.e. the hard mask's staircase."""
    f = f.to(DT)
    pts = []
    a, b = f[:, :-1], f[:, 1:]
    ok = mask[:, :-1] & mask[:, 1:] & ((a - level) * (b - level) < 0)
    i, j = torch.nonzero(ok, as_tuple=True)
    pts.append(torch.stack([i.to(DT), j + (level - a[i, j]) / (b[i, j] - a[i, j])], 1))
    a, b = f[:-1], f[1:]
    ok = mask[:-1] & mask[1:] & ((a - level) * (b - level) < 0)
    i, j = torch.nonzero(ok, as_tuple=True)
    pts.append(torch.stack([i + (level - a[i, j]) / (b[i, j] - a[i, j]), j.to(DT)], 1))
    return torch.cat(pts)


class Shore30:
    """Signed distance (m, + inside water) to the JRC 30 m shoreline of the largest water body.

    Built from the cached 30 m clip (validate.fetch_clip): water = occurrence >= 90 % or maximum extent.
    The Euclidean distance transform is scipy's (reference preparation only)."""

    def __init__(self, area, bounds, H, W, product="occurrence"):
        from scipy import ndimage

        from scripts.detect.validate import NODATA, RES, fetch_clip
        arr, self.w0, self.n0, _ = fetch_clip(area, product, bounds)
        wet = ((arr >= 90) if product == "occurrence" else (arr == 1)) & (arr != NODATA)
        lab, _ = ndimage.label(wet, structure=np.ones((3, 3)))
        sz = np.bincount(lab.ravel())
        sz[0] = 0
        self.lake = lab == sz.argmax()
        lat0 = (bounds[1] + bounds[3]) / 2
        self.dy, self.dx = RES * 110570.0, RES * 111320.0 * math.cos(math.radians(lat0))
        din = ndimage.distance_transform_edt(self.lake, sampling=(self.dy, self.dx))
        dout = ndimage.distance_transform_edt(~self.lake, sampling=(self.dy, self.dx))
        self.sd = torch.from_numpy(np.where(self.lake, din - self.dy / 2, -dout + self.dy / 2).astype(np.float32))
        self.RES, self.bounds, self.H, self.W = RES, bounds, H, W
        w, s, e, n = bounds
        self.dlon, self.dlat = (e - w) / W, (n - s) / H
        self.px_m = (self.dlat * 110570.0, self.dlon * 111320.0 * math.cos(math.radians(lat0)))

    def lonlat(self, pts, shift=(0.0, 0.0)):
        w, _, _, n = self.bounds
        return w + (pts[:, 1] + 0.5 + shift[1]) * self.dlon, n - (pts[:, 0] + 0.5 + shift[0]) * self.dlat

    def signed(self, pts, shift=(0.0, 0.0)):
        """Bilinear interpolation of the 30 m signed-distance field at the points."""
        lon, lat = self.lonlat(pts, shift)
        u = ((lon - self.w0) / self.RES - 0.5).to(torch.float64)
        v = ((self.n0 - lat) / self.RES - 0.5).to(torch.float64)
        h, w = self.sd.shape
        u0 = u.floor().clamp(0, w - 2)
        v0 = v.floor().clamp(0, h - 2)
        a, b = (u - u0).clamp(0, 1), (v - v0).clamp(0, 1)
        i, j = v0.long(), u0.long()
        s = self.sd.to(torch.float64)
        val = (s[i, j] * (1 - a) * (1 - b) + s[i, j + 1] * a * (1 - b) + s[i + 1, j] * (1 - a) * b
               + s[i + 1, j + 1] * a * b)
        return val.to(DT)


def shoreline_distance(f, mask, shore: Shore30, shift=(0.0, 0.0), far_m=1000.0) -> dict:
    """Distance of the f = 0.5 line (or a hard mask's staircase) to the 30 m reference shoreline."""
    d = shore.signed(contour_points(f, mask), shift)
    near = d.abs() < far_m
    dn = d[near]
    if dn.numel() == 0:
        return {"n": 0}
    return {"n": int(d.numel()), "share_within_1km": round(float(near.float().mean()), 3),
            "median_abs_m": round(float(dn.abs().median()), 1), "mean_abs_m": round(float(dn.abs().mean()), 1),
            "rms_m": round(float(dn.pow(2).mean().sqrt()), 1), "median_signed_m": round(float(dn.median()), 1),
            "p90_abs_m": round(float(torch.quantile(dn.abs(), 0.9)), 1)}


# ====================================================================== evaluation helpers
def _r(x, nd=4):
    x = float(x)
    return round(x, nd) if math.isfinite(x) else None


def fmetrics(f: torch.Tensor, ref: torch.Tensor, m: torch.Tensor) -> dict:
    a, b = f.to(DT)[m], ref.to(DT)[m]
    if a.numel() < 3:
        return {"n": int(a.numel())}
    d = a - b
    r = torch.corrcoef(torch.stack([a, b]))[0, 1] if a.std() > 0 else torch.tensor(float("nan"))
    return {"n": int(a.numel()), "rmse": _r(d.pow(2).mean().sqrt()), "bias": _r(d.mean()), "r": _r(r),
            "mae": _r(d.abs().mean())}


def pooled(F_, ref, m, dates):
    return fmetrics(torch.cat([F_[t].to(DT)[m] for t in dates]), torch.cat([ref[m]] * len(dates)),
                    torch.ones(m.sum() * len(dates), dtype=torch.bool))


def reliability(f, ref, m, nb=10):
    a, b = f.to(DT)[m], ref.to(DT)[m]
    idx = (a * nb).floor().clamp(0, nb - 1).long()
    out = []
    for k in range(nb):
        s = idx == k
        out.append({"lo": k / nb, "n": int(s.sum()), "mean_est": _r(a[s].mean()) if s.any() else None,
                    "mean_ref": _r(b[s].mean()) if s.any() else None})
    return out


def _load_conf(area, shape):
    p = ROOT / "data/processed" / area / "stats/products.pt"
    if not p.exists():
        return None
    d = torch.load(p, weights_only=False)
    c = d.get("water_conf")
    if c is None or tuple(c.shape[-2:]) != tuple(shape):
        return None
    return c.float()


def _setup(area, dev=torch.device("cpu")):
    """Load the stack, run the detector exactly as run.py does (terrain mask if built), return pieces."""
    from scripts.detect.stack import load_preview_stack
    from scripts.detect.terrain import cached_water_possible
    st = load_preview_stack(area)
    wok = cached_water_possible(area, tuple(st.x.shape[-2:]))
    out = ops.run_pipeline(st.x, st.valid, "preview", dev=dev, water_ok=wok)
    v = out["valid"].cpu()
    # unfiltered, gain-normalised brightness for the fraction (same gains as the detector applied)
    xr = (st.x * out["gains"][:, :, None, None].float()) * v[:, None]
    return st, wok, out, xr


def _ref(area, bounds, H, W):
    from scripts.detect.validate import reference_on_grid
    ref, _ = reference_on_grid(area, bounds, H, W)
    return {"occ90": ref["occ_ge90_frac"].float(), "ext": ref["ext_frac"].float(), "occ_mean": ref["occ_mean"].float()}


def _shifted_ref(area, bounds, H, W, dy, dx, product="occurrence"):
    """Reference fraction (occ >= 90 or extent) sampled on our grid moved by (dy, dx) pixels
    (dy > 0 = south, dx > 0 = east)."""
    from scripts.detect.validate import NODATA, fetch_clip, resample
    arr, w0, n0, _ = fetch_clip(area, product, bounds)
    w, s, e, n = bounds
    dlon, dlat = (e - w) / W, (n - s) / H
    b = (w + dx * dlon, s - dy * dlat, e + dx * dlon, n - dy * dlat)
    _, agg, a = resample(arr, w0, n0, b, H, W)
    return agg(((a >= 90) if product == "occurrence" else (a == 1)) & (a != NODATA)).float()


# ====================================================================== Lake Sevan: the decisive test
def evaluate_sevan(verbose=True):
    t0 = time.time()
    area = "sevan"
    st, wok, out, xr = _setup(area)
    km2 = st.km2_per_px
    T, _, H, W = st.x.shape
    v, water, L, prior = out["valid"].cpu(), out["water"].cpu(), out["L"].cpu(), out["prior_water"].cpu()
    del st.x
    dates = st.dates
    calm = [t for t in range(T) if t not in SEVAN_WINDY]
    refs = _ref(area, st.bounds, H, W)
    vall = v.all(0)
    lab = ops.label_components((refs["occ90"] >= 0.5)[None])[0]
    cnt = torch.bincount(lab.flatten())
    cnt[0] = 0
    dom = ops.dilate((lab == cnt.argmax())[None], 11)[0] & vall   # main lake + 5 px (~0.9 km)
    shore = {k: (r > 0) & (r < 1) & dom for k, r in refs.items() if k != "occ_mean"}

    # ---------------------------------------------------------------- methods
    M = {"hard": water.to(DT), "half": half_pixel(water, v)}
    conf = _load_conf(area, (H, W))
    M["logistic"] = conf if conf is not None else logistic_conf(L[:, 0], v)
    runs = {}
    for dom_, D in (("display", None), ("power", 10.0), ("power", 20.0), ("power", 30.0), ("power", 40.0)):
        for ch in ("HH", "HV", "combined") if D in (None, 30.0) else ("combined",):
            key = f"mix_{dom_}{'' if D is None else int(D)}_{ch}"
            r = water_fraction(L, v, water, "preview", prior, x_frac=xr, db_range=D or DB_RANGE, domain=dom_,
                               channels=ch, km2_per_px=km2)
            runs[key] = r
            M[key] = r["frac"].float()
    r = water_fraction(L, v, water, "preview", prior, x_frac=None, domain="display", km2_per_px=km2)
    M["mix_display_combined_filteredL"] = r["frac"].float()
    r = water_fraction(L, v, water, "preview", prior, x_frac=None, domain="power", km2_per_px=km2)
    M["mix_power30_combined_filteredL"] = r["frac"].float()
    if verbose:
        print(f"  fractions done {time.time() - t0:.0f}s")

    # temporal ("super-resolved") versions: median over the eight calm dates
    MED = {k: M[k][calm].median(0).values for k in
           ("hard", "half", "logistic", "mix_display_combined", "mix_power30_combined")}

    # ---------------------------------------------------------------- per-pixel fraction vs 30 m truth
    res = {"area": area, "dates": dates, "calm_dates": [dates[t] for t in calm],
           "windy_dates": [dates[t] for t in SEVAN_WINDY], "km2_per_px": km2,
           "pixel_m": None, "n_shore_px": {k: int(m.sum()) for k, m in shore.items()},
           "weights_combined_display": runs["mix_display_combined"]["weights"].tolist(),
           "weights_combined_power30": runs["mix_power30_combined"]["weights"].tolist()}
    pix = {}
    for rk, m in shore.items():
        pix[rk] = {}
        for k, f in M.items():
            pix[rk][k] = {"pooled_calm": pooled(f, refs[rk], m, calm),
                          "per_date": [fmetrics(f[t], refs[rk], m) for t in range(T)]}
        for k, f in MED.items():
            pix[rk]["calm_median_" + k] = {"pooled_calm": fmetrics(f, refs[rk], m)}
    res["pixel"] = pix
    res["reliability"] = {k: reliability(MED[k] if k in MED else M[k][0], refs["occ90"], shore["occ90"])
                          for k in ("hard", "half", "logistic", "mix_display_combined", "mix_power30_combined")}
    res["reliability_pooled"] = {
        k: reliability(torch.cat([M[k][t][shore["occ90"]] for t in calm]),
                       torch.cat([refs["occ90"][shore["occ90"]]] * len(calm)),
                       torch.ones(int(shore["occ90"].sum()) * len(calm), dtype=torch.bool))
        for k in ("hard", "half", "logistic", "mix_display_combined", "mix_power30_combined")}

    # ---------------------------------------------------------------- lake area per date
    reg = largest_body_region(water, KN["band_px"] + 1)
    areas = {k: ((f * reg).sum((1, 2)) * km2).tolist() for k, f in M.items()}
    reg_u = reg[calm].any(0)
    areas_med = {k: float((f * reg_u).sum() * km2) for k, f in MED.items()}

    def summ(a):
        c = torch.tensor([a[t] for t in calm], dtype=DT)
        return {"calm_mean": _r(c.mean(), 1), "calm_sd": _r(c.std(), 1),
                "calm_range": _r(c.max() - c.min(), 1),
                "err_vs_1242_mean": _r(c.mean() - SEVAN_KM2, 1), "err_vs_jrc90_mean": _r(c.mean() - SEVAN_JRC90_KM2, 1),
                "rms_err_vs_1242": _r((c - SEVAN_KM2).pow(2).mean().sqrt(), 1),
                "windy": [_r(a[t], 1) for t in SEVAN_WINDY],
                "windy_err_vs_1242": [_r(a[t] - SEVAN_KM2, 1) for t in SEVAN_WINDY]}
    res["lake_area"] = {"per_date_km2": {k: [_r(x, 1) for x in a] for k, a in areas.items()},
                        "summary": {k: summ(a) for k, a in areas.items()},
                        "calm_median_map_km2": {k: _r(a, 1) for k, a in areas_med.items()},
                        "truth_km2": {"published": SEVAN_KM2, "jrc_occ90": SEVAN_JRC90_KM2,
                                      "jrc_occ90_in_domain": _r(float((refs['occ90'] * dom).sum() * km2), 1),
                                      "jrc_max_extent_in_domain": _r(float((refs['ext'] * dom).sum() * km2), 1)}}
    if verbose:
        print(f"  pixel + area done {time.time() - t0:.0f}s")

    # ---------------------------------------------------------------- sub-pixel shoreline position
    shores = {"occ90": Shore30(area, st.bounds, H, W, "occurrence"), "ext": Shore30(area, st.bounds, H, W, "extent")}
    res["pixel_m"] = [round(x, 1) for x in shores["occ90"].px_m]
    cont = {}
    for rk, sh in shores.items():
        cont[rk] = {}
        # (the half-pixel rule has no 0.5 crossing by construction: its edge pixels ARE 0.5)
        for k in ("hard", "mix_display_combined", "mix_power30_combined", "logistic"):
            cont[rk][k] = {"per_date": [shoreline_distance(M[k][t], dom, sh) for t in range(T)],
                           "calm_median_map": shoreline_distance(MED[k], dom, sh)}
            pdm = [c["median_abs_m"] for i, c in enumerate(cont[rk][k]["per_date"]) if i in calm and c["n"]]
            pds = [c["median_signed_m"] for i, c in enumerate(cont[rk][k]["per_date"]) if i in calm and c["n"]]
            cont[rk][k]["calm_mean_of_median_abs_m"] = _r(sum(pdm) / len(pdm), 1)
            cont[rk][k]["calm_mean_of_median_signed_m"] = _r(sum(pds) / len(pds), 1)
        for D in (10, 20, 40):
            k = f"mix_power{D}_combined"
            cont[rk][k] = {"calm_median_map": shoreline_distance(M[k][calm].median(0).values, dom, sh)}
    res["shoreline_distance"] = cont

    # ---------------------------------------------------------------- stretch sensitivity
    sens = {}
    for k in ("mix_display_combined", "mix_power10_combined", "mix_power20_combined", "mix_power30_combined",
              "mix_power40_combined"):
        med = M[k][calm].median(0).values
        sens[k] = {"pixel_pooled_occ90": pix["occ90"][k]["pooled_calm"],
                   "pixel_pooled_ext": pix["ext"][k]["pooled_calm"],
                   "pixel_calm_median_occ90": fmetrics(med, refs["occ90"], shore["occ90"]),
                   "area": res["lake_area"]["summary"][k],
                   "shore_calm_median_occ90": cont["occ90"][k]["calm_median_map"]}
    res["stretch_sensitivity"] = sens

    # ---------------------------------------------------------------- misregistration test
    S0 = ops.dilate(shore["occ90"][None], 3)[0] & dom
    shifts = [-1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0]
    keys = ("hard", "mix_display_combined", "mix_power30_combined")
    grid = {k: torch.zeros(len(shifts), len(shifts)) for k in keys}
    for i, dy in enumerate(shifts):
        for j, dx in enumerate(shifts):
            rs = _shifted_ref(area, st.bounds, H, W, dy, dx)
            for k in keys:
                grid[k][i, j] = fmetrics(MED[k], rs, S0)["rmse"]
    mis = {"shifts_px": shifts, "fixed_set_px": int(S0.sum())}
    for k in keys:
        g = grid[k]
        i, j = divmod(int(g.argmin()), len(shifts))
        best = (shifts[i], shifts[j])
        rs = _shifted_ref(area, st.bounds, H, W, *best)
        mis[k] = {"rmse_grid": [[_r(x) for x in row] for row in g.tolist()],
                  "rmse_at_0": _r(g[4, 4]), "best_shift_rows_cols_px": best, "rmse_at_best": _r(g[i, j]),
                  "metrics_at_0": fmetrics(MED[k], refs["occ90"], S0), "metrics_at_best": fmetrics(MED[k], rs, S0),
                  "shore_at_best": shoreline_distance(MED[k], dom, shores["occ90"], shift=best)}
    res["misregistration"] = mis
    res["runtime_s"] = round(time.time() - t0, 1)
    figs = {"M": M, "MED": MED, "refs": refs, "shore": shore, "dom": dom, "water": water, "areas": areas,
            "shores": shores, "calm": calm, "st": st, "grid": grid, "valid": v}
    prod = {"frac": runs["mix_display_combined"]["frac"], "band": runs["mix_display_combined"]["band"],
            "frac_power30": runs["mix_power30_combined"]["frac"],
            "area_km2_frac": runs["mix_display_combined"]["area_km2_frac"],
            "area_km2_frac_power30": runs["mix_power30_combined"]["area_km2_frac"],
            "area_km2_hard": runs["mix_display_combined"]["area_km2_hard"],
            "lake_area_km2": {k: areas[k] for k in ("hard", "half", "mix_display_combined", "mix_power30_combined")},
            "frac_calm_median": MED["mix_display_combined"].half(),
            "weights": runs["mix_display_combined"]["weights"], "domain": "display", "dates": dates,
            "km2_per_px": km2, "bounds": st.bounds}
    return res, figs, prod


# ====================================================================== Tonle Sap and Ararat
def evaluate_floodplain(area="tonle-sap", sweep_dates=(0, 4, 9), verbose=True):
    """Hard vs fractional open-water area per date; first-date check against the permanent-water edge;
    shoreline-uncertainty band recomputed with fractional boundary pixels."""
    from scripts.detect import stats_change as sc
    t0 = time.time()
    st, wok, out, xr = _setup(area)
    for k in ("change_first", "change_consec", "flooded_veg", "change_step", "change_date", "classes"):
        out.pop(k, None)
    km2 = st.km2_per_px
    px_km = math.sqrt(km2)
    T, _, H, W = st.x.shape
    del st.x
    v, water, L, prior, kn = out["valid"].cpu(), out["water"].cpu(), out["L"].cpu(), out["prior_water"].cpu(), out["knobs"]
    dates = st.dates
    runs = {}
    for name, dom_, D in (("display", "display", DB_RANGE), ("power30", "power", 30.0),
                          ("power20", "power", 20.0), ("power40", "power", 40.0)):
        runs[name] = water_fraction(L, v, water, "preview", prior, x_frac=xr, db_range=D, domain=dom_, km2_per_px=km2)
        if verbose:
            print(f"  {name} done {time.time() - t0:.0f}s")
    hard_a = (water.sum((1, 2)).double() * km2)
    half_a = half_pixel(water, v).sum((1, 2)).double() * km2
    res = {"area": area, "dates": dates, "km2_per_px": km2,
           "terrain_mask": wok is not None,
           "area_km2": {"hard": [_r(x, 1) for x in hard_a.tolist()], "half": [_r(x, 1) for x in half_a.tolist()]}}
    for k, r in runs.items():
        res["area_km2"][k] = [_r(x, 1) for x in r["area_km2_frac"].tolist()]
    band_px = runs["display"]["band"].sum((1, 2))
    res["band_km2"] = [_r(x * km2, 1) for x in band_px.tolist()]
    fr = runs["display"]["frac"].float()
    bnd = runs["display"]["band"]
    res["partial_pixels"] = {
        "band_hard_km2": [_r(float((water[t] & bnd[t]).sum()) * km2, 1) for t in range(T)],
        "band_frac_km2": [_r(float((fr[t] * bnd[t]).sum()) * km2, 1) for t in range(T)],
        "band_land_side_frac_km2": [_r(float((fr[t] * (bnd[t] & ~water[t])).sum()) * km2, 1) for t in range(T)],
        "band_water_side_deficit_km2": [_r(float(((1 - fr[t]) * (bnd[t] & water[t])).sum()) * km2, 1) for t in range(T)],
    }
    res["weights_display"] = runs["display"]["weights"].tolist()
    # ---------------- first date: permanent-water edges of the reference
    out_ref = None
    try:
        refs = _ref(area, st.bounds, H, W)
        r = refs["occ90"]
        m = (r > 0) & (r < 1) & v[0] & ops.dilate((r >= 0.5)[None], 5)[0]
        conf = _load_conf(area, (H, W))
        M0 = {"hard": water[0].to(DT), "half": half_pixel(water[:1], v[:1])[0],
              "logistic": conf[0] if conf is not None else logistic_conf(L[0, 0], v[0])}
        for k, rr in runs.items():
            M0["mix_" + k] = rr["frac"][0].float()
        res["first_date_check"] = {"date": dates[0], "n_px": int(m.sum()),
                                   "occ90": {k: fmetrics(f, r, m) for k, f in M0.items()},
                                   "reliability_display": reliability(M0["mix_display"], r, m),
                                   "reliability_hard": reliability(M0["hard"], r, m)}
        sh = Shore30(area, st.bounds, H, W, "occurrence")
        # distance only near the reference permanent lake (the largest occ >= 90 body)
        lake_grid = (r >= 0.5)
        lab = ops.label_components(lake_grid[None])[0]
        c = torch.bincount(lab.flatten())
        c[0] = 0
        near = ops.dilate((lab == c.argmax())[None], 11)[0] & v[0]
        res["first_date_check"]["shoreline_distance_occ90"] = {
            k: shoreline_distance(M0[k], near, sh) for k in ("hard", "mix_display", "mix_power30")}
        out_ref = (r, m)
        del sh
    except Exception as e:  # reference not cached: say so, do not fail the products
        res["first_date_check"] = {"error": repr(e)}
    if verbose:
        print(f"  first-date check done {time.time() - t0:.0f}s")
    # ---------------- shoreline uncertainty band with fractional boundary pixels
    thr = torch.linspace(0.045, 0.160, 9).tolist()
    sd = list(sweep_dates)
    A = {"binary": torch.zeros(len(thr), len(sd)), "display": torch.zeros(len(thr), len(sd)),
         "power30": torch.zeros(len(thr), len(sd))}
    P = torch.zeros(len(thr), len(sd))
    for i, w_ in enumerate(thr):
        wm = sc._water_with(L[sd], v[sd], kn, w_)
        if wok is not None:
            wm = wm & wok
        A["binary"][i] = wm.sum((1, 2)).double() * km2
        P[i] = sc.perimeter_km(wm.cpu(), px_km).float()
        for name, dom_ in (("display", "display"), ("power30", "power")):
            rr_ = water_fraction(L[sd], v[sd], wm, "preview", prior, x_frac=xr[sd], domain=dom_, km2_per_px=km2)
            A[name][i] = rr_["area_km2_frac"].float()
        if verbose:
            print(f"  threshold {w_:.3f} done {time.time() - t0:.0f}s")

    def q(a):
        return (torch.quantile(a.double(), 0.95, dim=0) - torch.quantile(a.double(), 0.05, dim=0))
    base_i = min(range(len(thr)), key=lambda i: abs(thr[i] - kn["water_hh_weak"]))
    perim = sc.perimeter_km(water[sd].cpu(), px_km)
    model_spread = torch.stack([runs[k]["area_km2_frac"][sd].float() for k in runs]).double()
    res["uncertainty"] = {
        "dates": [dates[t] for t in sd], "thresholds": thr,
        "area_km2": {k: [[_r(x, 1) for x in row] for row in a.tolist()] for k, a in A.items()},
        "threshold_5_95_width_km2": {k: [_r(x, 1) for x in q(a).tolist()] for k, a in A.items()},
        "threshold_full_range_km2": {k: [_r(x, 1) for x in (a.max(0).values - a.min(0).values).tolist()]
                                     for k, a in A.items()},
        "binary_boundary_term_0.9Ps_km2": [_r(x, 1) for x in (0.9 * perim * px_km).tolist()],
        "fractional_model_spread_km2 (display, power 20/30/40 dB; max - min)":
            [_r(x, 1) for x in (model_spread.max(0).values - model_spread.min(0).values).tolist()],
        "perimeter_km": [_r(x, 1) for x in perim.tolist()],
        "baseline_threshold": thr[base_i]}
    res["runtime_s"] = round(time.time() - t0, 1)
    figs = {"runs_area": {k: r["area_km2_frac"].tolist() for k, r in runs.items()}, "hard": hard_a.tolist(),
            "half": half_a.tolist(), "A": A, "sd": sd, "dates": dates, "ref": out_ref}
    prod = {"frac": runs["display"]["frac"], "band": runs["display"]["band"],
            "frac_power30": runs["power30"]["frac"],
            "area_km2_frac": runs["display"]["area_km2_frac"], "area_km2_frac_power30": runs["power30"]["area_km2_frac"],
            "area_km2_hard": hard_a, "weights": runs["display"]["weights"], "domain": "display", "dates": dates,
            "km2_per_px": km2, "bounds": st.bounds}
    # zoom material for the figure (first date, a window on the lake's north-west shore)
    figs["zoom"] = {"frac": runs["display"]["frac"][[0, T - 1]].float(), "water": water[[0, T - 1]],
                    "occ90": out_ref[0] if out_ref else None}
    del runs
    return res, figs, prod


def evaluate_control(area="ararat"):
    """Control area (essentially no water): hard vs fractional area; any fraction here is false water."""
    st, wok, out, xr = _setup(area)
    km2 = st.km2_per_px
    del st.x
    v, water, L, prior = out["valid"].cpu(), out["water"].cpu(), out["L"].cpu(), out["prior_water"].cpu()
    r = water_fraction(L, v, water, "preview", prior, x_frac=xr, km2_per_px=km2)
    r30 = water_fraction(L, v, water, "preview", prior, x_frac=xr, domain="power", km2_per_px=km2)
    res = {"area": area, "dates": st.dates, "terrain_mask": wok is not None,
           "area_km2": {"hard": [_r(x, 1) for x in r["area_km2_hard"].tolist()],
                        "display": [_r(x, 1) for x in r["area_km2_frac"].tolist()],
                        "power30": [_r(x, 1) for x in r30["area_km2_frac"].tolist()]}}
    prod = {"frac": r["frac"], "band": r["band"], "frac_power30": r30["frac"], "area_km2_frac": r["area_km2_frac"],
            "area_km2_frac_power30": r30["area_km2_frac"], "area_km2_hard": r["area_km2_hard"],
            "weights": r["weights"], "domain": "display", "dates": st.dates, "km2_per_px": km2, "bounds": st.bounds}
    return res, {}, prod


# ====================================================================== figures
def _save(fig, name):
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    fig.savefig(p, dpi=110, bbox_inches="tight")
    plt.close(fig)
    print(f"  wrote {p.relative_to(ROOT)} ({p.stat().st_size // 1024} KB)")


COL = {"hard": "#444444", "half": "#9a9a9a", "logistic": "#c77c1e", "mix_display_combined": "#1f6fb4",
       "mix_power30_combined": "#c0392b", "jrc": "#2a9d55"}
LBL = {"hard": "(a) hard mask", "half": "(b) half-pixel rule", "logistic": "(c) logistic probability",
       "mix_display_combined": "(d) mixing, display domain", "mix_power30_combined": "(d') mixing, power (30 dB)"}


def _zoom_window(shore, dom, size=44):
    """A window of `size` px centred on the stretch of shoreline with the most reference shoreline pixels."""
    m = shore.float()[None, None]
    s = F.avg_pool2d(m, size, stride=4)[0, 0]
    i, j = divmod(int(s.argmax()), s.shape[1])
    return i * 4, j * 4, size


def figures_sevan(res, figs):
    M, MED, refs, shore, dom, calm = figs["M"], figs["MED"], figs["refs"], figs["shore"], figs["dom"], figs["calm"]
    st, sh30 = figs["st"], figs["shores"]["occ90"]
    dates = res["dates"]
    t_show = 7  # 24 Sep, calm
    H, W = refs["occ90"].shape
    w, s, e, n = st.bounds
    dlon, dlat = (e - w) / W, (n - s) / H
    # pick the zoom on the southern shore (where the shore is long and gentle)
    r0, c0, sz = _zoom_window(shore["occ90"] & (torch.arange(H)[:, None] > H * 0.5), dom)
    ext = [w + c0 * dlon, w + (c0 + sz) * dlon, n - (r0 + sz) * dlat, n - r0 * dlat]
    # 30 m reference clip for the same window
    lake30 = sh30.lake
    j0 = int((ext[0] - sh30.w0) / sh30.RES)
    j1 = int((ext[1] - sh30.w0) / sh30.RES)
    i0 = int((sh30.n0 - ext[3]) / sh30.RES)
    i1 = int((sh30.n0 - ext[2]) / sh30.RES)
    sub30 = lake30[i0:i1, j0:j1]
    ext30 = [sh30.w0 + j0 * sh30.RES, sh30.w0 + j1 * sh30.RES, sh30.n0 - i1 * sh30.RES, sh30.n0 - i0 * sh30.RES]
    win = (slice(r0, r0 + sz), slice(c0, c0 + sz))
    fig, ax = plt.subplots(1, 4, figsize=(15, 4.3))
    panels = [(sub30.astype(float), ext30, "JRC 30 m water (occurrence ≥ 90 %)"),
              (refs["occ90"][win].numpy(), ext, "JRC fraction on our 180 m grid"),
              (M["hard"][t_show][win].numpy(), ext, f"(a) hard mask, {dates[t_show]}"),
              (M["mix_display_combined"][t_show][win].numpy(), ext, f"(d) mixing fraction, {dates[t_show]}")]
    yy, xx = np.mgrid[0:sub30.shape[0], 0:sub30.shape[1]]
    for a, (img, ex, title) in zip(ax, panels):
        im = a.imshow(img, extent=ex, cmap="Blues", vmin=0, vmax=1, interpolation="nearest")
        a.contour(ext30[0] + (xx + 0.5) * sh30.RES, ext30[3] - (yy + 0.5) * sh30.RES, sub30.astype(float),
                  levels=[0.5], colors=COL["jrc"], linewidths=0.8)
        a.set_title(title, fontsize=9)
        a.set_xlabel("longitude (°E)", fontsize=8)
        a.tick_params(labelsize=7)
    ax[0].set_ylabel("latitude (°N)", fontsize=8)
    fig.colorbar(im, ax=ax, fraction=0.012, pad=0.01, label="water fraction")
    fig.suptitle("Lake Sevan, southern shore (~8 km wide). Green line: JRC 30 m shoreline in every panel", fontsize=10)
    _save(fig, "sevan_zoom.png")

    # ---- scatter
    m = shore["occ90"]
    fig, ax = plt.subplots(1, 3, figsize=(13, 4.2))
    for a, (k, f, title) in zip(ax, [("mix_display_combined", torch.cat([M["mix_display_combined"][t][m] for t in calm]), "(d) mixing, display domain, 8 calm dates"),
                                     ("mix_power30_combined", torch.cat([M["mix_power30_combined"][t][m] for t in calm]), "(d') mixing, power 30 dB, 8 calm dates"),
                                     ("med", MED["mix_display_combined"][m], "(e) median of (d) over the 8 calm dates")]):
        rr = refs["occ90"][m] if k == "med" else torch.cat([refs["occ90"][m]] * len(calm))
        a.hist2d(rr.numpy(), f.float().numpy(), bins=20, range=[[0, 1], [0, 1]], cmap="Greys", cmin=1,
                 norm=matplotlib.colors.LogNorm())
        a.plot([0, 1], [0, 1], color=COL["jrc"], lw=1.2, label="1:1")
        mm = fmetrics(f, rr, torch.ones_like(f, dtype=torch.bool))
        a.set_title(f"{title}\nRMSE {mm['rmse']:.2f}, bias {mm['bias']:+.2f}, r {mm['r']:.2f}", fontsize=9)
        a.set_xlabel("JRC water fraction of the pixel (occurrence ≥ 90 %)", fontsize=8)
        a.set_ylabel("estimated fraction", fontsize=8)
        a.legend(fontsize=7, loc="upper left")
    fig.suptitle(f"Lake Sevan shoreline pixels (0 < JRC fraction < 1, n = {int(m.sum())} per date); "
                 "grey = pixel count (log scale)", fontsize=10, y=1.04)
    _save(fig, "sevan_scatter.png")

    # ---- reliability (pooled over calm dates), both references
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.4))
    for a, rk in zip(ax, ("occ90", "ext")):
        # all pixels within 2 px of the reference shoreline (0 and 1 included, so the curve is not
        # squeezed by selecting on the reference)
        mk = ops.dilate(shore[rk][None], 5)[0] & dom
        for k in ("hard", "half", "logistic", "mix_display_combined", "mix_power30_combined"):
            f = torch.cat([M[k][t][mk] for t in calm])
            rr = torch.cat([refs[rk][mk]] * len(calm))
            rel = reliability(f, rr, torch.ones_like(f, dtype=torch.bool))
            x = [b["mean_est"] for b in rel if b["n"] >= 30]
            y = [b["mean_ref"] for b in rel if b["n"] >= 30]
            a.plot(x, y, "o-", color=COL[k], label=LBL[k], ms=4, lw=1.3)
        a.plot([0, 1], [0, 1], ":", color="k", lw=1)
        a.set_xlim(-0.02, 1.02)
        a.set_ylim(-0.02, 1.02)
        a.set_xlabel("estimated fraction (bin mean; bins with ≥ 30 pixels)", fontsize=8)
        a.set_ylabel("mean JRC fraction in the bin", fontsize=8)
        a.set_title({"occ90": "reference: occurrence ≥ 90 % (1984–2021)",
                     "ext": "reference: maximum extent (1984–2021)"}[rk], fontsize=9)
    ax[0].legend(fontsize=7, loc="upper left")
    fig.suptitle("Reliability at the Lake Sevan shoreline (all pixels within 2 px of the JRC shoreline, 8 calm "
                 "dates):\non the dotted line an estimate of f means that, on average, f of the pixel is water",
                 fontsize=10, y=1.04)
    _save(fig, "sevan_reliability.png")

    # ---- area per date
    fig, ax = plt.subplots(figsize=(9.5, 4.3))
    x = np.arange(len(dates))
    for k in ("hard", "half", "logistic", "mix_display_combined", "mix_power30_combined"):
        ax.plot(x, res["lake_area"]["per_date_km2"][k], "o-", color=COL[k], label=LBL[k], ms=4)
    tr = res["lake_area"]["truth_km2"]
    ax.axhline(SEVAN_KM2, color=COL["jrc"], lw=1.4, label="published 1,242 km²")
    ax.axhline(SEVAN_JRC90_KM2, color=COL["jrc"], lw=1, ls="--", label="JRC occurrence ≥ 90 %: 1,239.7 km²")
    ax.axhline(tr["jrc_max_extent_in_domain"], color=COL["jrc"], lw=1, ls=":",
               label=f"JRC maximum extent: {tr['jrc_max_extent_in_domain']:,.0f} km²")
    for t in SEVAN_WINDY:
        ax.axvspan(t - 0.4, t + 0.4, color="#f2d7a0", alpha=0.6, lw=0)
    ax.text(5.5, ax.get_ylim()[0] + 3, "windy", ha="center", fontsize=8)
    ax.set_xticks(x, [d[5:] for d in dates], fontsize=8)
    ax.set_ylabel("lake area, km² (largest water body + band)", fontsize=8)
    ax.legend(fontsize=7, ncol=2, loc="lower left")
    ax.set_title("Lake Sevan area per pass by method (2026)", fontsize=10)
    _save(fig, "sevan_area.png")

    # ---- sub-pixel shoreline
    fig, ax = plt.subplots(1, 2, figsize=(12, 5), gridspec_kw={"width_ratios": [1.15, 1]})
    a = ax[0]
    sub = (slice(r0, r0 + sz), slice(c0, c0 + sz))
    a.imshow(MED["hard"][sub].numpy(), extent=ext, cmap="Greys", vmin=0, vmax=3, interpolation="nearest")
    a.contour(ext30[0] + (xx + 0.5) * sh30.RES, ext30[3] - (yy + 0.5) * sh30.RES, sub30.astype(float), levels=[0.5],
              colors=COL["jrc"], linewidths=1.2)
    win_mask = torch.zeros_like(dom)
    win_mask[sub] = True
    for k, mkk in (("hard", "s"), ("mix_display_combined", "o")):
        p = contour_points(MED[k], dom & win_mask)
        lon, lat = sh30.lonlat(p)
        a.plot(lon.numpy(), lat.numpy(), mkk, ms=2.2, color=COL[k], ls="none",
               label="hard mask edge (pixel-edge midpoints)" if k == "hard" else "f = 0.5 line of the fraction")
    a.plot([], [], color=COL["jrc"], label="JRC 30 m shoreline")
    a.legend(fontsize=7, loc="lower right")
    a.set_title("calm-date median maps; grey = hard mask water", fontsize=9)
    a.tick_params(labelsize=7)
    a = ax[1]
    bins = np.arange(-400, 401, 20)
    for k in ("hard", "mix_display_combined", "mix_power30_combined"):
        d = sh30.signed(contour_points(MED[k], dom))
        d = d[d.abs() < 1000]
        a.hist(d.numpy(), bins=bins, histtype="step", lw=1.4, color=COL[k], density=True,
               label=f"{LBL[k]}: median |d| {float(d.abs().median()):.0f} m, median d {float(d.median()):+.0f} m")
    a.axvline(0, color=COL["jrc"])
    a.set_xlabel("signed distance to the JRC 30 m shoreline, m (+ = inside the water)", fontsize=8)
    a.set_ylabel("density", fontsize=8)
    a.legend(fontsize=7, loc="upper left")
    a.set_title("whole lake, calm-date median maps (points within 1 km)", fontsize=9)
    _save(fig, "sevan_shoreline.png")

    # ---- stretch sensitivity
    sens = res["stretch_sensitivity"]
    keys = ["mix_display_combined", "mix_power10_combined", "mix_power20_combined", "mix_power30_combined",
            "mix_power40_combined"]
    labels = ["display\n(D → 0)", "power\n10 dB", "power\n20 dB", "power\n30 dB", "power\n40 dB"]
    fig, ax = plt.subplots(1, 3, figsize=(13, 3.8))
    xx_ = np.arange(len(keys))
    for rk, ls in (("occ90", "-"), ("ext", "--")):
        ax[0].plot(xx_, [sens[k][f"pixel_pooled_{rk}"]["bias"] for k in keys], "o" + ls, color="k",
                   label=f"bias vs {'occ ≥ 90 %' if rk == 'occ90' else 'max extent'}")
        ax[1].plot(xx_, [sens[k][f"pixel_pooled_{rk}"]["r"] for k in keys], "o" + ls, color="k",
                   label=f"vs {'occ ≥ 90 %' if rk == 'occ90' else 'max extent'}")
    ax[0].axhline(0, color="#999", lw=0.8)
    ax[0].set_title("shoreline-pixel bias of f", fontsize=9)
    ax[1].set_title("correlation with the JRC fraction", fontsize=9)
    ax[2].plot(xx_, [sens[k]["area"]["calm_mean"] for k in keys], "o-", color="k", label="calm-date mean")
    ax[2].errorbar(xx_, [sens[k]["area"]["calm_mean"] for k in keys], yerr=[sens[k]["area"]["calm_sd"] for k in keys],
                   color="k", ls="none", capsize=3)
    ax[2].axhline(SEVAN_KM2, color=COL["jrc"], lw=1.2, label="1,242")
    ax[2].axhline(res["lake_area"]["truth_km2"]["jrc_max_extent_in_domain"], color=COL["jrc"], ls=":", lw=1,
                  label="JRC max extent")
    ax[2].axhline(res["lake_area"]["summary"]["hard"]["calm_mean"], color=COL["hard"], ls="--", lw=1, label="hard mask")
    ax[2].set_title("lake area, km² (± sd over 8 calm dates)", fontsize=9)
    for a in ax:
        a.set_xticks(xx_, labels, fontsize=7)
        a.legend(fontsize=7)
    fig.suptitle("Sensitivity to the assumed preview stretch D (brightness b → intensity 10^(D·b/10))", fontsize=10)
    _save(fig, "sevan_stretch.png")


def figures_floodplain(res, figs):
    dates = res["dates"]
    x = np.arange(len(dates))
    fig, ax = plt.subplots(figsize=(9.5, 4.5))
    ax.plot(x, figs["hard"], "o-", color=COL["hard"], label="(a) hard mask", ms=4)
    ax.plot(x, figs["runs_area"]["display"], "o-", color=COL["mix_display_combined"], label="(d) fractional, display domain", ms=4)
    ax.plot(x, figs["runs_area"]["power30"], "o-", color=COL["mix_power30_combined"], label="(d') fractional, power 30 dB", ms=4)
    A, sd = figs["A"], figs["sd"]
    for k, c, off in (("binary", COL["hard"], -0.12), ("display", COL["mix_display_combined"], 0.0),
                      ("power30", COL["mix_power30_combined"], 0.12)):
        lo = A[k].min(0).values.numpy()
        hi = A[k].max(0).values.numpy()
        ax.vlines(np.array(sd) + off, lo, hi, color=c, lw=4, alpha=0.45)
    ax.vlines([], [], [], color="#777", lw=4, alpha=0.45, label="bars: range over weak thresholds 0.045–0.160")
    ax.set_xticks(x, [d[5:] for d in dates], fontsize=8)
    ax.set_ylabel("open water, km²", fontsize=8)
    ax.legend(fontsize=7, loc="upper left")
    ax.set_title("Tonle Sap open water per pass: hard vs fractional boundary pixels (2026)", fontsize=10)
    _save(fig, "tonle-sap_area.png")


# ====================================================================== CLI
def save_products(area, prod):
    d = ROOT / "data/processed" / area / "subpixel"
    d.mkdir(parents=True, exist_ok=True)
    torch.save(prod, d / "products.pt")
    print(f"  wrote {(d / 'products.pt').relative_to(ROOT)}")


def _write_results(area, res):
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "results.json"
    allr = json.loads(p.read_text()) if p.exists() else {}
    allr[area] = res
    allr["settings"] = {"knobs": KN, "db_range_assumed": DB_RANGE, "default_domain_preview": "display",
                        "default_domain_gamma0": "power", "input_for_fraction": "unfiltered, gain-normalised preview"}
    p.write_text(json.dumps(allr, indent=1, default=str))
    print(f"  wrote {p.relative_to(ROOT)}")


def main(argv=None):
    torch.set_num_threads(4)
    areas = (argv if argv is not None else sys.argv[1:]) or ["sevan", "tonle-sap", "ararat"]
    for area in areas:
        print(area)
        if area == "sevan":
            res, figs, prod = evaluate_sevan()
            figures_sevan(res, figs)
        elif area == "ararat":
            res, figs, prod = evaluate_control(area)
        else:
            res, figs, prod = evaluate_floodplain(area)
            figures_floodplain(res, figs)
        save_products(area, prod)
        _write_results(area, res)
        del res, figs, prod


if __name__ == "__main__":
    main()
