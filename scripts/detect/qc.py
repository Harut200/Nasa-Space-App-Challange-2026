"""Automatic quality control of every radar pass of every area.

    .venv/bin/python -m scripts.detect.qc                 # all areas with previews
    .venv/bin/python -m scripts.detect.qc sevan ararat    # some areas
    .venv/bin/python -m scripts.detect.qc --no-align sevan  # co-registration off (shows misregistration)
    .venv/bin/python -m scripts.detect.qc --report        # all areas + verification runs + figures
                                                          #   -> docs/eda/qc/*.png, docs/eda/qc/results.json

Writes data/processed/<area>/qc.json (only for the default align=True run).

Every pass gets a set of interpretable checks. Each check has a value, a
threshold (warn / fail), a verdict ("ok" | "warn" | "fail" | "info") and, for the
checks that compare a date with the other dates of the same area, a robust
z-score

    z_t = (v_t - median_{s != t} v_s) / max(1.4826 * MAD_{s != t} v_s, floor)

(leave-one-date-out median and MAD; `floor` is a per-check minimum spread in
natural units so that ten very similar dates do not make a tiny wobble look
huge). warn at |z| >= Z_WARN, fail at |z| >= Z_FAIL. With only ten dates the
MAD itself is uncertain by roughly +-30 %, so z-scores are rough; the
thresholds are deliberately wide and every fail is also an effect of a
practically meaningful size (z * floor).

All numerics are PyTorch on the CPU. One area at a time; tensors freed between areas.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gc
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import torch  # noqa: E402
import torch.nn.functional as F  # noqa: E402

from scripts.detect import ops  # noqa: E402

CH = ("HH", "HV")
OUTAGE = ("2026-07-27", "2026-08-10")
CADENCE_D = 12

# ----------------------------------------------------------------------------
# Thresholds. z-based checks: warn |z| >= Z_WARN, fail |z| >= Z_FAIL, spread floor per check.
# ----------------------------------------------------------------------------
Z_WARN, Z_FAIL = 4.0, 8.0
FLOORS = {
    "contrast": 0.03,    # slope of date vs other dates' median -> warn >= 0.12, fail >= 0.24
    "gain": 0.01,        # log(gain): 1 %   -> warn >= ~4 %, fail >= ~8 % brightness change of stable land
    "balance": 0.005,    # HH-HV brightness -> warn >= 0.02, fail >= 0.04
    "slope": 0.006,      # brightness change across the box (across-track slope x box width) -> warn >= 0.024, fail >= 0.048
    "loo": 0.15,         # x median(A): the leave-one-out departure ratio -> warn >= 1.6x, fail >= 2.2x
    "noise": 0.01,       # local coefficient of variation in homogeneous windows
    "floor": 0.003,      # share of pixels at the display floor (0.3 percentage point)
    "ceiling": 0.003,
    "stripes": 1.0,      # profile peak z
    "coverage": 0.01,
}
GEO = {"warn_px": 0.75, "fail_px": 1.5}      # residual misregistration (absolute; 1 px ~ 180 m)
PSR_MIN = 6.0                               # correlation peak-to-sidelobe ratio below this = estimate unreliable
TOD = {"warn_s": 120, "fail_s": 1800}       # time of day differs from the area's median
COVER = {"box_warn": 0.95, "box_fail": 0.80, "loss_warn": 0.02, "loss_fail": 0.10}
EDGE_ERODE_PX = ops.KNOBS["preview"]["edge_erode_px"]
STABLE_Q = 0.25          # stable pixels: lowest 25 % of leave-one-out temporal std, in both channels
LAND_HH = ops.KNOBS["preview"]["water_hh_weak"]   # temporal median HH below this = water, excluded
SAT_LO, SAT_HI = 0.03, 0.97                        # median brightness outside this = saturated, excluded
NB = 6                    # NB x NB blocks for the spatial pattern of a departure
PENALTY = {"warn": 6, "fail": 25}
RADIOMETRIC = ("gain", "contrast", "slope", "loo", "noise", "balance")  # a fail here needs a second indicator
CAP_WARN = {"floor", "ceiling", "stripes", "noise", "edge_width", "crid", "time_of_day", "cadence", "applied_shift", "peak"}


# ------------------------------- small helpers -------------------------------
def robust_z(v: torch.Tensor, floor: float, local: bool = False) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """Leave-one-out robust z per entry of a 1-D tensor. Returns (z, median_others, scale_others).

    local=True additionally requires the date to depart from its own neighbours in time
    (z_local = (v_t - mean(v_{t-1}, v_{t+1})) / scale): z = sign(z_global) * min(|z_global|,
    z_local in the same direction). A smooth seasonal drift (several consecutive dates moving
    together) then does not count as a bad pass; an isolated jump does."""
    v = v.double()
    T = v.numel()
    z, med, sc = torch.zeros(T, dtype=torch.float64), torch.zeros(T, dtype=torch.float64), torch.zeros(T, dtype=torch.float64)
    for t in range(T):
        o = torch.cat([v[:t], v[t + 1:]])
        o = o[torch.isfinite(o)]
        if o.numel() < 2 or not math.isfinite(float(v[t])):
            z[t], med[t], sc[t] = float("nan"), float("nan"), float("nan")
            continue
        m = o.median()
        s = max(1.4826 * float((o - m).abs().median()), floor)
        zg = float((v[t] - m) / s)
        if local:
            nb = [float(v[i]) for i in (t - 1, t + 1) if 0 <= i < T and math.isfinite(float(v[i]))]
            if nb:
                zl = (float(v[t]) - sum(nb) / len(nb)) / s
                zg = math.copysign(min(abs(zg), max(0.0, zl * math.copysign(1, zg))), zg)
        z[t], med[t], sc[t] = zg, m, s
    return z.float(), med.float(), sc.float()


def z_verdict(z: float, cap_warn: bool = False) -> str:
    if not math.isfinite(z):
        return "info"
    a = abs(z)
    v = "fail" if a >= Z_FAIL else "warn" if a >= Z_WARN else "ok"
    return "warn" if (cap_warn and v == "fail") else v


def r(x, n=4):
    if x is None:
        return None
    x = float(x)
    return None if not math.isfinite(x) else round(x, n)


def check(value, verdict, unit="", warn=None, fail=None, z=None, ref=None, channel=None, **extra):
    d = {"value": value if isinstance(value, (str, list, dict)) or value is None else r(value),
         "unit": unit, "threshold": {"warn": warn, "fail": fail}, "verdict": verdict}
    if z is not None:
        d["z"] = r(z, 2)
    if ref is not None:
        d["usual"] = r(ref)
    if channel is not None:
        d["channel"] = channel
    d.update(extra)
    return d


def masked_median(a: torch.Tensor, m: torch.Tensor) -> float:
    v = a[m]
    return float(v.median()) if v.numel() else float("nan")


def subsample_quantile(v: torch.Tensor, q: float) -> float:
    v = v.flatten().float()
    if v.numel() == 0:
        return float("nan")
    return float(torch.quantile(v[:: max(1, v.numel() // 200000)], q))


# ------------------------------- geometry ------------------------------------
def track_axes(polygon, bounds, shape, pixel_km):
    """Across-track (u) and along-track (v) coordinates in km for every pixel, plus a description.

    The along-track direction is the footprint edge closest to north-south (NISAR is a
    near-polar orbit); across-track is perpendicular to it, pointing east-ish. Without a
    footprint (synthetic stacks) u = columns, v = rows."""
    H, W = shape
    rr = torch.arange(H, dtype=torch.float32)[:, None].expand(H, W)
    cc = torch.arange(W, dtype=torch.float32)[None, :].expand(H, W)
    if polygon is None or bounds is None:
        return (cc - W / 2) * pixel_km, (rr - H / 2) * pixel_km, "columns (no footprint)"
    w, s, e, n = bounds
    lat0 = (s + n) / 2
    kx, ky = 111.32 * math.cos(math.radians(lat0)), 110.57
    best = None
    for i in range(len(polygon)):
        (x1, y1), (x2, y2) = polygon[i], polygon[(i + 1) % len(polygon)]
        dx, dy = (x2 - x1) * kx, (y2 - y1) * ky
        L = math.hypot(dx, dy)
        if L == 0:
            continue
        ns = abs(dy) / L
        if best is None or ns > best[0]:
            best = (ns, dx / L, dy / L)
    _, ax, ay = best
    if ay < 0:
        ax, ay = -ax, -ay
    ux, uy = ay, -ax          # perpendicular, east-ish
    lon = w + (cc + 0.5) / W * (e - w)
    lat = n - (rr + 0.5) / H * (n - s)
    X, Y = (lon - (w + e) / 2) * kx, (lat - lat0) * ky
    heading = math.degrees(math.atan2(ax, ay))
    return X * ux + Y * uy, X * ax + Y * ay, f"heading {heading:.1f} deg; across-track axis points to azimuth {heading + 90:.0f} deg"


# ------------------------------- registration --------------------------------
def xcorr_shift(a: torch.Tensor, b: torch.Tensor, mask: torch.Tensor) -> tuple[float, float, float]:
    """Sub-pixel (rows, cols) with b(r, c) ~ a(r - rows, c - cols) and the peak-to-sidelobe ratio.

    Same estimator as stack._shift (local-mean removal, Hann taper, FFT cross-correlation,
    parabolic peak) plus PSR = (peak - mean(cc away from the peak)) / std(cc away from the peak).
    A weak peak (PSR below PSR_MIN) means the shift estimate itself is unreliable, e.g. a
    scene that is mostly calm water with nothing to lock onto."""
    def prep(x):
        local = F.avg_pool2d(x[None, None], 15, 1, 7, count_include_pad=False)[0, 0]
        x = x - local
        sel = x[mask > 0]
        if sel.numel() > 100:
            s = 4 * 1.4826 * float((sel - sel.median()).abs().median()) + 1e-6
            x = x.clamp(-s, s)
        x = x * mask
        return x * torch.hann_window(x.shape[0], periodic=False)[:, None] * torch.hann_window(x.shape[1], periodic=False)[None]

    cc = torch.fft.irfft2(torch.fft.rfft2(prep(b)) * torch.fft.rfft2(prep(a)).conj(), s=a.shape)
    h, w = cc.shape
    py, px = divmod(int(cc.argmax()), w)

    def peak(m1, m0, p1):
        den = float(m1 - 2 * m0 + p1)
        return 0.0 if abs(den) < 1e-12 else float(0.5 * (m1 - p1) / den)

    sy = py + peak(cc[(py - 1) % h, px], cc[py, px], cc[(py + 1) % h, px])
    sx = px + peak(cc[py, (px - 1) % w], cc[py, px], cc[py, (px + 1) % w])
    side = torch.ones_like(cc, dtype=torch.bool)
    for dy in range(-5, 6):
        side[(py + dy) % h, [(px + dx) % w for dx in range(-5, 6)]] = False
    sl = cc[side]
    psr = float((cc[py, px] - sl.mean()) / sl.std().clamp_min(1e-12))
    return (sy if sy < h / 2 else sy - h), (sx if sx < w / 2 else sx - w), psr


# ------------------------------- fixed marks (raw image coordinates) ---------
def fixed_marks(raw_u8: torch.Tensor, lit: torch.Tensor, scale: float = 255.0, origin=(0, 0),
                const_levels: float = 1.0, min_px: int = 4, min_contrast: float = 0.08):
    """Blobs that sit at identical raw-image coordinates on every date and do not vary.

    raw_u8 [T, 2, H, W] in raw (not re-gridded) image coordinates, integer display levels
    (scale 255) or 0..1 floats (scale 1). A real radar pixel always varies between passes
    (speckle, moisture), so a group of >= min_px connected pixels whose value changes by at
    most `const_levels` display steps over ALL dates, that is neither at the display floor
    (calm water) nor at the ceiling (saturation), and that differs from its surroundings by
    >= min_contrast, is a burned-in mark (text, logo, processing artefact)."""
    x = raw_u8.float()
    lo, hi = x.amin(0), x.amax(0)
    mean = x.mean(0)
    step = 1.0 if scale == 255.0 else 1.0 / 255.0
    const = ((hi - lo) <= const_levels * step + 1e-6).all(0) & lit
    m = mean.mean(0) / scale
    # not near the display floor or ceiling in EITHER channel: saturated targets (calm water at the
    # floor, bright cities near white) are also constant, but they are real ground, not a mark
    mc = mean / scale
    const &= ((mc > 3.5 / 255) & (mc < 242.0 / 255)).all(0)
    marks = []
    if int(const.sum()) < min_px:
        return marks
    lab = ops.label_components(const[None])[0]
    ids, counts = torch.unique(lab[lab > 0], return_counts=True)
    for i, n in zip(ids.tolist(), counts.tolist()):
        if n < min_px:
            continue
        blob = lab == i
        ring = ops.dilate(blob[None], 9)[0] & ~ops.dilate(blob[None], 3)[0] & lit
        if int(ring.sum()) < 4:
            continue
        inside, around = float(m[blob].mean()), float(m[ring].mean())
        if abs(inside - around) < min_contrast:
            continue
        rows, cols = torch.nonzero(blob, as_tuple=True)
        marks.append({"raw_row": int(rows.float().mean()) + origin[0], "raw_col": int(cols.float().mean()) + origin[1],
                      "pixels": int(n), "brightness": r(inside, 3), "surroundings": r(around, 3),
                      "kind": "dark" if inside < around else "bright"})
        if len(marks) >= 20:
            break
    return sorted(marks, key=lambda d: -d["pixels"])


def raw_crop(area: dict, meta_passes: list, folder: pathlib.Path, margin: int = 20):
    """The area box cut out of every raw preview at the SAME raw pixel window (from the first date's
    georeferencing). Returns uint8 [T, 2, h, w] (HH, HV), the all-dates-lit mask and the window origin."""
    from PIL import Image
    from scripts.detect.stack import _georef, _polygon
    w, s, e, n = area["bbox"]
    out, lit, origin = [], None, None
    for p in meta_passes:
        rgb = np.asarray(Image.open(folder / f"{p['date']}.png").convert("RGB"))
        if origin is None:
            a, b, c, d = _georef(rgb, _polygon(p["wkt"]))
            c0, c1 = sorted([(w - b) / a, (e - b) / a])
            r0, r1 = sorted([(n - d) / c, (s - d) / c])
            H, W = rgb.shape[:2]
            r0, r1 = max(0, int(r0) - margin), min(H, int(math.ceil(r1)) + margin)
            c0, c1 = max(0, int(c0) - margin), min(W, int(math.ceil(c1)) + margin)
            origin = (r0, c0, r1, c1)
        r0, c0, r1, c1 = origin
        sub = rgb[r0:r1, c0:c1]
        if sub.shape[:2] != (r1 - r0, c1 - c0):  # a smaller image: pad
            pad = np.zeros((r1 - r0, c1 - c0, 3), np.uint8)
            pad[:sub.shape[0], :sub.shape[1]] = sub
            sub = pad
        li = torch.from_numpy(sub.max(axis=2) > 0)
        lit = li if lit is None else lit & li
        out.append(torch.from_numpy(sub[..., :2].copy()).permute(2, 0, 1))
        del rgb
    lit = ops.erode(lit[None], 21)[0] & lit  # stay 10 px away from the swath edge
    # erode() treats outside-frame as valid; also drop a 10-px border of the crop
    lit[:10], lit[-10:], lit[:, :10], lit[:, -10:] = False, False, False, False
    return torch.stack(out), lit, origin[:2]


# ------------------------------- metadata ------------------------------------
def metadata_checks(dates, meta_passes, area_cfg):
    """Per date: polarisation, bandwidth mode, crid, time of day, cadence."""
    out = {d: {} for d in dates}
    if not meta_passes:
        return out, {}
    byd = {p["date"]: p for p in meta_passes}
    from collections import Counter
    bw = {d: "+".join(byd[d].get("bandwidth") or ["?"]) for d in dates if d in byd}
    crid = {d: byd[d].get("crid") or "?" for d in dates if d in byd}
    maj_bw = Counter(bw.values()).most_common(1)[0][0] if bw else None
    maj_crid = Counter(crid.values()).most_common(1)[0][0] if crid else None
    sod = {}
    for d in dates:
        st = (byd.get(d) or {}).get("startTime")
        if st:
            t = dt.datetime.fromisoformat(st.replace("Z", "+00:00"))
            sod[d] = t.hour * 3600 + t.minute * 60 + t.second
    med_sod = float(np.median(list(sod.values()))) if sod else None
    excluded = set(area_cfg.get("excluded_dates", []) or [])
    for i, d in enumerate(dates):
        p = byd.get(d)
        if p is None:
            continue
        pol = p.get("polarization") or []
        miss = [c for c in CH if c not in pol]
        out[d]["polarisation"] = check(pol, "fail" if miss else "ok", warn=None, fail="HH and HV both present",
                                       missing=miss, channel=miss[0] if len(miss) == 1 else None)
        out[d]["mode"] = check(bw[d], "ok" if bw[d] == maj_bw else "fail", fail=f"!= area mode {maj_bw}")
        out[d]["crid"] = check(crid[d], "ok" if crid[d] == maj_crid else "warn", warn=f"!= majority {maj_crid}")
        if d in sod:
            dev = abs(sod[d] - med_sod)
            dev = min(dev, 86400 - dev)
            out[d]["time_of_day"] = check(dev, "fail" if dev >= TOD["fail_s"] else "warn" if dev >= TOD["warn_s"] else "ok",
                                          unit="s from the area's median start time", warn=TOD["warn_s"], fail=TOD["fail_s"],
                                          start=p.get("startTime"))
        if i > 0:
            d0, d1 = dt.date.fromisoformat(dates[i - 1]), dt.date.fromisoformat(d)
            gap = (d1 - d0).days
            n_miss = round(gap / CADENCE_D) - 1
            expected = [(d0 + dt.timedelta(days=CADENCE_D * k)).isoformat() for k in range(1, n_miss + 1)]
            if gap % CADENCE_D:
                lab, v = f"irregular gap of {gap} d (not a multiple of {CADENCE_D})", "warn"
            elif n_miss == 0:
                lab, v = "on cadence", "ok"
            elif all(OUTAGE[0] <= e <= OUTAGE[1] for e in expected):
                lab, v = f"outage: {n_miss} pass(es) missing in the instrument outage {OUTAGE[0]}..{OUTAGE[1]}", "info"
            elif all(e in excluded or OUTAGE[0] <= e <= OUTAGE[1] for e in expected):
                ex = [e for e in expected if e in excluded]
                n_out = n_miss - len(ex)
                lab, v = (f"{len(ex)} pass(es) excluded by the area config ({', '.join(ex)})"
                          + (f" and {n_out} in the instrument outage" if n_out else "")), "info"
            else:
                miss = [e for e in expected if e not in excluded and not (OUTAGE[0] <= e <= OUTAGE[1])]
                lab, v = f"{len(miss)} pass(es) missing, not explained by the outage or the area config ({', '.join(miss)})", "warn"
            out[d]["cadence"] = check(gap, v, unit="days since previous pass", warn=f"!= {CADENCE_D}", label=lab,
                                      expected_missing=expected)
    return out, {"mode": maj_bw, "crid": maj_crid}


# ------------------------------- the QC itself -------------------------------
def run_qc(x: torch.Tensor, valid: torch.Tensor, dates: list[str], *, meta_passes=None, area_cfg=None,
           shifts=None, polygon=None, bounds=None, pixel_km: float = 0.18, raw=None, raw_lit=None,
           raw_scale: float = 1.0, raw_origin=(0, 0)) -> dict:
    """All checks for one stack. x [T, 2, H, W] brightness 0..1, valid [T, H, W]."""
    area_cfg = area_cfg or {}
    x = torch.nan_to_num(x.float().cpu(), nan=0.0)
    valid = valid.cpu()
    T, C, H, W = x.shape
    px_m = pixel_km * 1000
    meta, meta_sum = metadata_checks(dates, meta_passes or [], area_cfg)

    # ---- masks
    v = valid & ~ops.nodata_margin(x, valid, ops.KNOBS["preview"]["nodata_floor"])
    common = v.all(0)
    interior = ops.erode(common[None], 2 * EDGE_ERODE_PX + 1)[0]
    # HV present? (data-side test for HH-only passes drawn as grey, or an empty HV channel)
    hv_missing = []
    for t in range(T):
        m = v[t]
        n = max(int(m.sum()), 1)
        same = float(((x[t, 0] - x[t, 1]).abs() <= 1.5 / 255)[m].sum()) / n
        empty = float((x[t, 1] <= 1.5 / 255)[m].sum()) / n
        hv_missing.append(same > 0.98 or empty > 0.98)
    avail = torch.ones(T, C, dtype=torch.bool)
    for t, d in enumerate(dates):
        miss = set(meta.get(d, {}).get("polarisation", {}).get("missing", []))
        if hv_missing[t]:
            miss.add("HV")
        for c, name in enumerate(CH):
            if name in miss:
                avail[t, c] = False

    xs = ops.box_mean(x, v, 3)          # 3x3 multi-look: speckle down, same footprint
    med_all = xs.median(0).values       # [2, H, W]
    land = interior & (med_all[0] >= LAND_HH) & (med_all > SAT_LO).all(0) & (med_all < SAT_HI).all(0)
    n_land = int(land.sum())

    u_km, v_km, axis_note = track_axes(polygon, bounds, (H, W), pixel_km)
    u_extent = float(u_km[land].max() - u_km[land].min()) if n_land else 1.0   # km across track covered by land

    # leave-one-out temporal std and median, from sums and the sorted stack
    S1, S2 = xs.sum(0), (xs * xs).sum(0)
    srt = xs.sort(0).values
    k = (T - 2) // 2
    blocks_r = torch.linspace(0, H, NB + 1).long()
    blocks_c = torch.linspace(0, W, NB + 1).long()

    per = {name: torch.full((T, C), float("nan")) for name in
           ("loo", "bias", "gain", "contrast", "slope", "noise", "floor", "ceiling", "stripes")}
    balance = torch.full((T,), float("nan"))
    block_med = torch.full((T, C, NB, NB), float("nan"))
    block_u = torch.zeros(NB, NB)
    for i in range(NB):
        for j in range(NB):
            sl = (slice(int(blocks_r[i]), int(blocks_r[i + 1])), slice(int(blocks_c[j]), int(blocks_c[j + 1])))
            block_u[i, j] = u_km[sl].mean()
    stable_n = []
    profiles = []  # across-track profiles of the departure (for the report)
    ubins = None
    if n_land:
        uq = u_km[land]
        ubins = torch.linspace(float(uq.min()), float(uq.max()), 21)

    # homogeneous windows for the noise check (from the temporal median image)
    m5 = ops.box_mean(x, v, 5)
    sq5 = ops.box_mean(x * x, v, 5)
    cv = ((sq5 - m5 * m5).clamp_min(0).sqrt() / m5.clamp_min(1e-3))   # [T,2,H,W]
    del sq5
    cv_med = cv.median(0).values
    homog = torch.zeros(C, H, W, dtype=torch.bool)
    for c in range(C):
        cand = land & (m5.median(0).values[c] >= 0.1)
        if int(cand.sum()) > 50:
            homog[c] = cand & (cv_med[c] <= subsample_quantile(cv_med[c][cand], 0.2))
    del m5, cv_med

    for t in range(T):
        sdl = ((S2 - xs[t] ** 2) - (S1 - xs[t]) ** 2 / (T - 1)).clamp_min(0).div(T - 2).sqrt()
        rank = (xs < xs[t]).sum(0)
        loo_med = torch.where(rank <= k, srt[min(k + 1, T - 1)], srt[k])
        stab = land.clone()
        for c in range(C):
            if n_land > 50:
                stab &= sdl[c] <= subsample_quantile(sdl[c][land], STABLE_Q)
        stable_n.append(int(stab.sum()))
        d = xs[t] - loo_med                                     # departure from the others' median
        keep_both = stab.clone()
        for c in range(C):
            if stab.sum() < 30:
                continue
            dc = d[c][stab]
            per["loo"][t, c] = dc.abs().median()
            per["bias"][t, c] = dc.median()
            # trimmed set: drop pixels whose departure is extreme around the median departure. This is
            # gain-invariant (a frame-wide offset moves the median too) but removes the few stable-on-
            # other-dates pixels that really changed on this date (e.g. the newest ring of a flood).
            keep = (dc - dc.median()).abs() <= 3 * 1.4826 * (dc - dc.median()).abs().median() + 1e-6
            kmap = torch.zeros_like(stab)
            kmap[stab] = keep
            keep_both &= kmap
            per["gain"][t, c] = math.log(max(masked_median(xs[t, c], kmap), 1e-4) / max(masked_median(loo_med[c], kmap), 1e-4))
            # contrast: least-squares slope of this date on the others' median over trimmed stable land
            # (real change in a few pixels has high leverage). Regression dilution makes it < 1 on noisy
            # channels, about the same on every date, so the z-score across dates is still fair.
            rx, ry = loo_med[c][stab][keep], xs[t, c][stab][keep]
            if rx.numel() > 30:
                rxc = rx - rx.mean()
                per["contrast"][t, c] = float((rxc * (ry - ry.mean())).sum() / (rxc * rxc).sum().clamp_min(1e-9))
            uu, yy = u_km[kmap], xs[t, c][kmap]
            uc = uu - uu.mean()
            per["slope"][t, c] = float((uc * (yy - yy.mean())).sum() / (uc * uc).sum().clamp_min(1e-9)) * u_extent
            if homog[c].any():
                per["noise"][t, c] = cv[t, c][homog[c]].median()
            vt = interior
            per["floor"][t, c] = float((x[t, c][vt] <= 1.5 / 255).float().mean())
            per["ceiling"][t, c] = float((x[t, c][vt] >= 253.5 / 255).float().mean())
            # stripes: high-pass image, mean per along-track and per across-track line (1-px bins)
            hp = (x[t, c] - ops.box_mean(x[t:t + 1, c:c + 1], v[t:t + 1], 9)[0, 0])
            best = 0.0
            for coord in (v_km, u_km):
                b = ((coord - coord[interior].min()) / pixel_km).round().long()[interior]
                nb = int(b.max()) + 1
                cnt = torch.bincount(b, minlength=nb).float()
                sm = torch.bincount(b, weights=hp[interior].double(), minlength=nb).float()
                ok = cnt >= 20
                if int(ok.sum()) < 10:
                    continue
                p = sm[ok] / cnt[ok]
                mad = 1.4826 * float((p - p.median()).abs().median())
                best = max(best, float((p - p.median()).abs().max()) / max(mad, 1e-6))
            per["stripes"][t, c] = best
            # spatial pattern of the departure: block medians
            for i in range(NB):
                for j in range(NB):
                    sl = (slice(int(blocks_r[i]), int(blocks_r[i + 1])), slice(int(blocks_c[j]), int(blocks_c[j + 1])))
                    mm = stab[sl]
                    if int(mm.sum()) >= 15:
                        block_med[t, c, i, j] = d[c][sl][mm].median()
            if ubins is not None:
                bi = torch.bucketize(uu, ubins[1:-1])
                cnt = torch.bincount(bi, minlength=20).float()
                sm = torch.bincount(bi, weights=d[c][kmap].double(), minlength=20).float()
                profiles.append({"t": t, "c": c, "u_km": ((ubins[:-1] + ubins[1:]) / 2).tolist(),
                                 "dep": torch.where(cnt >= 10, sm / cnt.clamp_min(1), torch.full_like(cnt, float("nan"))).tolist(),
                                 "level": [float(z) for z in torch.bincount(bi, weights=yy.double(), minlength=20).float()
                                           / cnt.clamp_min(1)]})
        if keep_both.sum() >= 30:
            balance[t] = (xs[t, 0] - xs[t, 1])[keep_both].median()
        del sdl, rank, loo_med, d, stab
    del cv, srt, S1, S2

    # ---- geolocation: residual of each date against the mean of the others
    geo = []
    img = x.mean(1)
    # residual shift of each date against the mean of the other dates (high-pass clipped at 4 MAD
    # inside xcorr_shift so a few strong edges, e.g. a flood front, cannot dominate)
    xm = xs.mean(1)
    m1, m2 = xm.sum(0), (xm * xm).sum(0)
    tot = img.sum(0)
    refs = []
    for t in range(T):
        # three estimates, keep the sharpest correlation peak:
        #  (a) every interior pixel, as the loader's own co-registration does (best when the other
        #      dates are themselves misaligned: their edges are then "unstable" but still informative);
        #  (b) only pixels stable on the OTHER dates (a moving flood front cannot drag the estimate);
        #  (c) (b) without this date's own big departures (a new flood ring present only today).
        ref = (tot - img[t]) / (T - 1)
        refs.append(ref)
        best = xcorr_shift(ref, img[t], interior.float())
        tsd = ((m2 - xm[t] ** 2) - (m1 - xm[t]) ** 2 / (T - 1)).clamp_min(0).div(max(T - 2, 1)).sqrt()
        gm = interior & (tsd <= subsample_quantile(tsd[interior], 0.5)) if int(interior.sum()) > 100 else interior
        dep = (img[t] - ref).abs()
        dsel = dep[gm]
        big = dep > float(dsel.median()) + 3 * 1.4826 * float((dsel - dsel.median()).abs().median())
        big = ops.dilate(big[None], 5)[0]
        for m in (gm, gm & ~big):
            alt = xcorr_shift(ref, img[t], m.float())
            if alt[2] > best[2]:
                best = alt
        geo.append(best)
    del img, tot, xm, m1, m2, refs
    psr_all = torch.tensor([g[2] for g in geo])
    psr_thr = max(PSR_MIN, 0.3 * float(psr_all.median()))

    # ---- robust z per check
    Z = {}
    for name in ("gain", "contrast", "slope", "noise", "floor", "ceiling", "stripes"):
        Z[name] = [robust_z(per[name][:, c], FLOORS[name], local=True) for c in range(C)]
    loo_z = []
    for c in range(C):
        a = per["loo"][:, c]
        med = float(a[torch.isfinite(a)].median()) if torch.isfinite(a).any() else float("nan")
        loo_z.append(robust_z(a, FLOORS["loo"] * med if math.isfinite(med) else 1.0))
    bal_z = robust_z(balance, FLOORS["balance"], local=True)

    # ---- spatial pattern per date and channel
    def pattern(t, c):
        m = block_med[t, c]
        ok = torch.isfinite(m)
        if int(ok.sum()) < 6:
            return "none", {}
        others = [block_med[s, c][torch.isfinite(block_med[s, c])].abs().median() for s in range(T)
                  if s != t and torch.isfinite(block_med[s, c]).any()]
        sref = max(float(torch.stack(others).median()) if others else 0.0, 0.003)
        mv, uv = m[ok], block_u[ok]
        mu = float(mv.median())
        uc = uv - uv.mean()
        beta = float((uc * (mv - mv.mean())).sum() / (uc * uc).sum().clamp_min(1e-9))
        fit = mv.mean() + beta * uc
        res = mv - fit
        r2 = 1 - float(res.var() / mv.var().clamp_min(1e-12))
        amp = abs(beta) * float(uv.max() - uv.min())
        frac_same = float(((torch.sign(mv) == math.copysign(1, mu)) & (mv.abs() > 2 * sref)).float().mean())
        frac_dep = float((mv.abs() > 3 * sref).float().mean())
        info = {"blocks": int(ok.sum()), "median_block": r(mu), "usual_block": r(sref), "gradient_amplitude": r(amp),
                "gradient_r2": r(r2, 2), "share_blocks_departing": r(frac_dep, 2)}
        if amp > 4 * sref and r2 >= 0.5 and amp > 1.5 * abs(mu):
            return "gradient", info
        if abs(mu) > 3 * sref and frac_same >= 0.7:
            return "uniform", info
        if amp > 4 * sref and r2 >= 0.5:
            return "gradient", info
        if frac_dep >= 0.5:
            return "widespread", info
        if frac_dep > 0:
            return "patchy", info
        return "none", info

    # ---- edge dimming width (footprint edge only; the box edge is not an edge)
    edge = []
    for t in range(T):
        cur = v[t].clone()
        rings = []
        bright = x[t].mean(0)
        if bool(cur.all()):
            edge.append(None)
            continue
        for dd in range(1, 41):
            nxt = ops.erode(cur[None], 3)[0]
            rings.append(cur & ~nxt)
            cur = nxt
        inner = cur
        if int(inner.sum()) < 100:
            edge.append(None)
            continue
        ref = float(bright[inner].median())
        width = 0
        for dd, ring in enumerate(rings[:30], start=1):
            if int(ring.sum()) >= 20 and float(bright[ring].median()) < 0.85 * ref:
                width = dd
        edge.append(width)

    # ---- assemble per date
    ew = [e for e in edge if e is not None]
    edge_med = float(np.median(ew)) if ew else None
    passes = []
    for t, d in enumerate(dates):
        chk = dict(meta.get(d, {}))
        # coverage
        box = float(valid[t].float().mean())
        others_all = torch.stack([valid[s] for s in range(T) if s != t]).all(0) if T > 1 else valid[t]
        loss = float((others_all & ~valid[t]).sum()) / max(int(others_all.sum()), 1)
        medbox = float(valid.float().mean((1, 2)).median())
        rel = box / max(medbox, 1e-6)
        chk["coverage_box"] = check(box, "fail" if rel < COVER["box_fail"] else "warn" if rel < COVER["box_warn"] else "ok",
                                    unit="share of area box inside the footprint", ref=medbox,
                                    warn=f"< {COVER['box_warn']} x usual", fail=f"< {COVER['box_fail']} x usual")
        chk["coverage_common_loss"] = check(loss, "fail" if loss >= COVER["loss_fail"] else "warn" if loss >= COVER["loss_warn"] else "ok",
                                            unit="share of the other dates' common footprint this date does not cover",
                                            warn=COVER["loss_warn"], fail=COVER["loss_fail"],
                                            common_share_of_box=r(float(common.float().mean())))
        # geolocation
        if shifts is not None:
            sy, sx = shifts[t]
            chk["applied_shift"] = check(math.hypot(sy, sx), "info", unit="px",
                                         rows=r(sy, 2), cols=r(sx, 2), metres=r(math.hypot(sy, sx) * px_m, 0),
                                         note="correction applied by co-registration (onto the first date)")
        dy, dx, psr = geo[t]
        res = math.hypot(dy, dx)
        weak = psr < psr_thr
        gv = "fail" if res >= GEO["fail_px"] else "warn" if res >= GEO["warn_px"] else "ok"
        if weak and gv != "ok":
            gv = "info"  # no reliable correlation peak: do not claim a misregistration
        chk["residual_shift"] = check(res, gv, unit="px vs mean of the other dates", warn=GEO["warn_px"], fail=GEO["fail_px"],
                                      rows=r(dy, 2), cols=r(dx, 2), metres=r(res * px_m, 0))
        chk["peak"] = check(psr, "warn" if weak else "ok", unit="peak-to-sidelobe ratio",
                            warn=f"< {psr_thr:.1f} (max({PSR_MIN}, 0.3 x area median))",
                            note="weak peak: shift estimate unreliable" if weak else "")
        # radiometry and time outliers
        bal_attr = None
        for c, name in enumerate(CH):
            if not avail[t, c]:
                chk[f"missing_{name}"] = check(True, "fail", fail="channel present", channel=name,
                                               note=f"{name} channel missing (single-polarisation pass)")
                continue
            z, med, sc = Z["gain"][c]
            chk[f"gain_{name}"] = check(math.exp(per["gain"][t, c]), z_verdict(float(z[t])), unit="x stable-land brightness of the other dates' median",
                                        z=float(z[t]), ref=math.exp(float(med[t])) if math.isfinite(float(med[t])) else None,
                                        warn=f"|z|>={Z_WARN}", fail=f"|z|>={Z_FAIL}", channel=name)
            z, med, sc = Z["contrast"][c]
            chk[f"contrast_{name}"] = check(per["contrast"][t, c], z_verdict(float(z[t])),
                                            unit="slope of this date on the other dates' median (stable land, trimmed)",
                                            z=float(z[t]), ref=float(med[t]), warn=f"|z|>={Z_WARN}", fail=f"|z|>={Z_FAIL}", channel=name)
            z, med, sc = Z["slope"][c]
            chk[f"slope_{name}"] = check(per["slope"][t, c], z_verdict(float(z[t])), unit=f"brightness change across the box ({u_extent:.0f} km across track)",
                                         z=float(z[t]), ref=float(med[t]), warn=f"|z|>={Z_WARN}", fail=f"|z|>={Z_FAIL}", channel=name)
            for nm, unit in (("noise", "local coefficient of variation, homogeneous stable windows"),
                             ("floor", "share of pixels at the display floor"), ("ceiling", "share of pixels at the display ceiling"),
                             ("stripes", "max robust z of the line-mean of the high-pass image")):
                z, med, sc = Z[nm][c]
                chk[f"{nm}_{name}"] = check(per[nm][t, c], z_verdict(float(z[t]), cap_warn=True), unit=unit, z=float(z[t]),
                                            ref=float(med[t]), warn=f"|z|>={Z_WARN}", fail="(warn only)", channel=name)
            z, med, sc = loo_z[c]
            pat, pinfo = pattern(t, c)
            ratio = float(per["loo"][t, c]) / float(med[t]) if math.isfinite(float(med[t])) and float(med[t]) > 0 else float("nan")
            zz = float(z[t])
            if pat in ("uniform", "gradient"):
                vv = z_verdict(zz)
                note = f"whole frame departs ({pat})"
            elif pat == "patchy":
                vv = "ok"
                note = ("part of the scene changed: looks like real change, not flagged"
                        if abs(zz) >= Z_WARN or (pinfo.get("share_blocks_departing") or 0) >= 0.15 else "")
            elif pat == "widespread":
                vv = "warn" if abs(zz) >= Z_WARN else "ok"
                note = "most of the frame departs, without one common offset (contrast change, weather or noise)" if vv == "warn" else ""
            else:
                vv = "warn" if abs(zz) >= Z_WARN else "ok"
                note = "differs everywhere without a frame-wide offset: misregistration or extra noise" if vv == "warn" else ""
            chk[f"loo_{name}"] = check(per["loo"][t, c], vv, unit="median |date - median of other dates| over stable land",
                                       z=zz, ref=float(med[t]), warn=f"|z|>={Z_WARN} and uniform/gradient",
                                       fail=f"|z|>={Z_FAIL} and uniform/gradient", channel=name, ratio=r(ratio, 2),
                                       pattern=pat, pattern_detail=pinfo, signed_median=r(per["bias"][t, c]), note=note)
        # channel balance (attributed to the channel that departs more)
        zb = float(bal_z[0][t])
        if math.isfinite(zb) and bool(avail[t].all()):
            zs = [abs(float(loo_z[c][0][t])) + abs(float(Z["slope"][c][0][t])) + abs(float(Z["gain"][c][0][t]))
                  + abs(float(Z["contrast"][c][0][t]))
                  if avail[t, c] else 0.0 for c in range(C)]
            zs = [z if math.isfinite(z) else 0.0 for z in zs]
            bal_attr = CH[int(np.argmax(zs))] if max(zs) > 1.5 * min(zs) else "both"
            chk["balance"] = check(balance[t], z_verdict(zb), unit="median HH-HV over stable land", z=zb,
                                   ref=float(bal_z[1][t]), warn=f"|z|>={Z_WARN}", fail=f"|z|>={Z_FAIL}",
                                   channel=bal_attr)
        ew_lim = max(EDGE_ERODE_PX, edge_med + 3) if edge_med is not None else EDGE_ERODE_PX
        chk["edge_width"] = check(edge[t], "info" if edge[t] is None else ("warn" if edge[t] > ew_lim else "ok"),
                                  unit="px of dimmed swath edge (< 0.85 x interior)",
                                  warn=f"> max({EDGE_ERODE_PX} = detector's edge trim, area median + 3)",
                                  note="no swath edge inside the box" if edge[t] is None else "")
        passes.append({"date": d, "checks": chk})

    # ---- corroboration: one radiometric indicator alone cannot exclude a channel
    for p in passes:
        for c in CH:
            names = [k for k, ck in p["checks"].items() if k.split("_")[0] in RADIOMETRIC
                     and (ck.get("channel") == c or (k == "balance" and ck.get("channel") in (c, "both")))]
            flagged = [k for k in names if p["checks"][k]["verdict"] in ("warn", "fail")]
            if len(flagged) == 1 and p["checks"][flagged[0]]["verdict"] == "fail":
                ck = p["checks"][flagged[0]]
                ck["verdict"] = "warn"
                ck["note"] = (ck.get("note", "") + "; " if ck.get("note") else "") + "single indicator: demoted from fail to warn"

    # ---- verdicts, scores and reasons
    for t, p in enumerate(passes):
        fail_ch, warn_ch, phrases_f, phrases_w, notes = set(), set(), [], [], []
        score = 100.0
        for name, ck in p["checks"].items():
            vv = ck["verdict"]
            base = name.split("_")[0] if name not in ("time_of_day", "coverage_box", "coverage_common_loss", "applied_shift", "residual_shift", "edge_width") else name
            if vv == "fail" and base in CAP_WARN:
                vv = ck["verdict"] = "warn"
            if vv in PENALTY:
                score -= PENALTY[vv]
            if vv not in ("warn", "fail"):
                if ck.get("note") and "real change" in ck.get("note", ""):
                    notes.append(f"{ck.get('channel', '')}: {ck['note']}")
                continue
            ch = ck.get("channel")
            chans = {ch} if ch in CH else set(CH)
            (fail_ch if vv == "fail" else warn_ch).update(chans)
            (phrases_f if vv == "fail" else phrases_w).append(describe(name, ck, p["checks"]))
        if "fail" in [p["checks"].get(k, {}).get("verdict") for k in ("mode", "coverage_box", "coverage_common_loss", "residual_shift", "time_of_day")]:
            fail_ch = set(CH)
        warn_ch -= fail_ch
        verdict = "fail" if fail_ch else "warn" if warn_ch else "ok"
        if verdict == "ok":
            reason = "all checks within the usual range" + ("; " + "; ".join(notes) if notes else "")
        else:
            seen, ph = set(), []
            for s in phrases_f + phrases_w:
                if s not in seen:
                    seen.add(s)
                    ph.append(s)
            reason = "; ".join(ph[:3])
            other = [c for c in CH if c not in fail_ch | warn_ch]
            if fail_ch and len(fail_ch) == 1 and other:
                reason += f" while {other[0]} is normal"
            if any(p["checks"].get(f"loo_{c}", {}).get("pattern") in ("uniform", "gradient")
                   and p["checks"].get(f"loo_{c}", {}).get("verdict") == "fail" for c in CH):
                reason += " — likely instrument or processing effect"
        p.update(score=int(max(0, round(score))), verdict=verdict, fail_channels=sorted(fail_ch, key=CH.index),
                 warn_channels=sorted(warn_ch, key=CH.index), reason=reason,
                 own={"verdict": verdict, "fail_channels": sorted(fail_ch, key=CH.index),
                      "warn_channels": sorted(warn_ch, key=CH.index), "reason": reason, "score": int(max(0, round(score)))})

    if raw is None:  # synthetic / already-raw stacks: the stack itself is in image coordinates
        raw, raw_lit, raw_scale, raw_origin = x, interior, 1.0, (0, 0)
    marks = fixed_marks(raw, raw_lit if raw_lit is not None else torch.ones(raw.shape[-2:], dtype=torch.bool),
                        scale=raw_scale, origin=raw_origin)
    summary = {
        "exclude_dates": [p["date"] for p in passes if p["verdict"] == "fail" and len(p["fail_channels"]) == C],
        "exclude_channels": {p["date"]: p["fail_channels"] for p in passes if p["verdict"] == "fail" and len(p["fail_channels"]) < C},
        "warn_dates": {p["date"]: p["reason"] for p in passes if p["verdict"] == "warn"},
        "fixed_marks": marks,
        "edge_width_px": [e for e in edge],
        "mode": meta_sum.get("mode"), "crid": meta_sum.get("crid"),
        "stable_pixels_per_date": stable_n, "land_pixels": n_land,
        "across_track_axis": axis_note,
        "psr_threshold": r(psr_thr, 2),
        "notes": [],
    }
    if edge_med is not None and edge_med > EDGE_ERODE_PX:
        summary["notes"].append(f"swath-edge dimming up to {max(e for e in edge if e is not None)} px, wider than the detector's {EDGE_ERODE_PX}-px edge trim")
    if marks:
        summary["notes"].append(f"{len(marks)} fixed mark(s) at identical raw-image coordinates on every date (mask them)")
    cad = [p["checks"].get("cadence", {}) for p in passes]
    for p, c in zip(passes, cad):
        if c.get("verdict") in ("warn", "info") and c.get("label") and "on cadence" not in c["label"]:  # noqa: E501
            summary["notes"].append(f"before {p['date']}: {c['label']}")
    return {"dates": dates, "passes": passes, "summary": summary, "profiles": profiles,
            "series": {k: v.tolist() for k, v in per.items()} | {"balance": balance.tolist(),
                                                                "geo": [list(g) for g in geo]}}


def describe(name: str, ck: dict, allc: dict) -> str:
    """One plain-English phrase for a failed / warned check."""
    ch = ck.get("channel", "")
    if name.startswith("loo_"):
        return (f"{ch} channel {ck.get('ratio', float('nan')):.1f}x more different from the other dates than usual "
                f"({ck.get('pattern')} across the frame)")
    if name.startswith("gain_"):
        return f"{ch} stable-land brightness {100 * (ck['value'] - 1):+.0f}% vs the other dates"
    if name.startswith("contrast_"):
        return f"{ch} contrast {ck['value']:.2f}x the other dates' (usual {ck['usual']:.2f}x)"
    if name == "balance":
        return f"HH-HV over stable land {ck['value']:+.3f} vs usual {ck['usual']:+.3f} (departure in {ch})"
    if name.startswith("slope_"):
        return f"{ch} brightness change across the swath {ck['value']:+.3f} vs usual {ck['usual']:+.3f}"
    if name.startswith("noise_"):
        return f"{ch} local noise {ck['value']:.3f} vs usual {ck['usual']:.3f}"
    if name.startswith("floor_") or name.startswith("ceiling_"):
        return f"{ch} {100 * ck['value']:.1f}% of pixels at the display {name.split('_')[0]} vs usual {100 * ck['usual']:.1f}%"
    if name.startswith("stripes_"):
        return f"{ch} line artefact (stripe z {ck['value']:.0f} vs usual {ck['usual']:.0f}; RFI?)"
    if name.startswith("missing_"):
        return ck.get("note", "channel missing")
    if name == "residual_shift":
        return f"misregistered by {ck['value']:.1f} px (~{ck['metres']:.0f} m) against the other dates"
    if name == "peak":
        return "registration peak weak: shift estimate unreliable"
    if name == "polarisation":
        return f"missing polarisation {','.join(ck.get('missing', []))}"
    if name == "mode":
        return f"different acquisition mode {ck['value']} ({ck['threshold']['fail']})"
    if name == "crid":
        return f"different processing version {ck['value']}"
    if name == "time_of_day":
        return f"acquired {ck['value']:.0f} s away from the usual time"
    if name == "cadence":
        return ck.get("label", "irregular cadence")
    if name == "coverage_box":
        return f"footprint covers only {100 * ck['value']:.0f}% of the box"
    if name == "coverage_common_loss":
        return f"shrinks the common footprint by {100 * ck['value']:.1f}%"
    if name == "edge_width":
        return f"dim swath edge {ck['value']} px wide"
    return name


# ------------------------------- area driver ---------------------------------
def areas_with_previews() -> list[str]:
    out = []
    for p in sorted((ROOT / "areas").glob("*/area.json")):
        a = json.loads(p.read_text())
        if a.get("track") is None:
            continue
        f = ROOT / "data/raw/previews" / f"T{a['track']}_F{a['frame']}" / "meta.json"
        if f.exists():
            out.append(a["id"])
    return out


def qc_area(area_id: str, align: bool = True, write: bool = True, keep_profiles: bool = False) -> dict:
    from scripts.detect.stack import _polygon, load_preview_stack
    st = load_preview_stack(area_id, align=align)
    folder = ROOT / "data/raw/previews" / f"T{st.area['track']}_F{st.area['frame']}"
    passes = json.loads((folder / "meta.json").read_text())["passes"]
    w, s, e, n = st.bounds
    H, W = st.x.shape[-2:]
    pixel_km = math.sqrt(st.km2_per_px)
    raw, lit, origin = raw_crop(st.area, passes, folder)
    res = run_qc(st.x, st.valid, st.dates, meta_passes=passes, area_cfg=st.area,
                 shifts=st.shifts if align else None, polygon=_polygon(passes[0]["wkt"]), bounds=st.bounds,
                 pixel_km=pixel_km, raw=raw, raw_lit=lit, raw_scale=255.0, raw_origin=origin)
    del raw, lit
    out ={"area": area_id, "name": st.area.get("name"), "generated": dt.datetime.now().isoformat(timespec="seconds"),
           "source": st.source, "aligned": align, "grid": [int(H), int(W)], "pixel_m": r(pixel_km * 1000, 0),
           "dates": st.dates,
           "method": {"z": "leave-one-date-out robust z = (v - median of other dates) / max(1.4826 MAD, floor)",
                      "z_warn": Z_WARN, "z_fail": Z_FAIL, "floors": FLOORS, "geo_px": GEO, "psr_min": PSR_MIN,
                      "time_of_day_s": TOD, "coverage": COVER, "stable_quantile": STABLE_Q, "land_hh_min": LAND_HH,
                      "blocks": NB, "penalties": PENALTY,
                      "caveat": "ten dates per area: z-scores are rough (MAD uncertain by about +-30 %)"},
           "summary": res["summary"],
           "passes": res["passes"]}
    if write:
        p = ROOT / "data/processed" / area_id / "qc.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    if keep_profiles:
        out["_profiles"] = res["profiles"]
        out["_series"] = res["series"]
    del st
    gc.collect()
    return out


def summarise(q: dict) -> None:
    """(Re)build the area summary's exclude lists from the per-pass verdicts."""
    s = q["summary"]
    s["exclude_dates"] = [p["date"] for p in q["passes"] if p["verdict"] == "fail" and len(p["fail_channels"]) == len(CH)]
    s["exclude_channels"] = {p["date"]: p["fail_channels"] for p in q["passes"]
                             if p["verdict"] == "fail" and len(p["fail_channels"]) < len(CH)}
    s["warn_dates"] = {p["date"]: p["reason"] for p in q["passes"] if p["verdict"] == "warn"}


def consolidate_frames(area_ids: list[str] | None = None, write: bool = True) -> dict:
    """Areas cut from the same track/frame share each acquisition, so an instrument or
    processing fault of a pass is not a property of one crop. Using each area's OWN evidence
    (p["own"]): if channel c of a pass fails in a sibling area, it becomes
      fail  here if this area itself warned on c,
      warn  here if this area found nothing on c (the fault may be local to the other crop).
    Idempotent: always starts again from p["own"]. Returns {area: qc}."""
    qcs = {}
    for a in area_ids or areas_with_previews():
        q = load_qc(a)
        if q:
            qcs[a] = q
    from scripts.detect.stack import load_area
    groups = {}
    for a in qcs:
        cfg = load_area(a)
        groups.setdefault((cfg.get("track"), cfg.get("frame")), []).append(a)
    for a, q in qcs.items():
        for p in q["passes"]:
            own = p.get("own") or {k: p[k] for k in ("verdict", "fail_channels", "warn_channels", "reason", "score")}
            p.update(verdict=own["verdict"], fail_channels=list(own["fail_channels"]),
                     warn_channels=list(own["warn_channels"]), reason=own["reason"], score=own["score"], own=own)
            p["checks"].pop("same_pass", None)
    for key, members in groups.items():
        if len(members) < 2:
            continue
        for a in members:
            q = qcs[a]
            for p in q["passes"]:
                hits = {}
                for b in members:
                    if b == a:
                        continue
                    pb = next((x for x in qcs[b]["passes"] if x["date"] == p["date"]), None)
                    for c in (pb or {}).get("own", {}).get("fail_channels", []):
                        hits.setdefault(c, []).append(b)
                if not hits:
                    continue
                fail_ch, warn_ch, extra = set(p["fail_channels"]), set(p["warn_channels"]), []
                for c, bs in hits.items():
                    if c in fail_ch:
                        continue
                    if c in warn_ch:
                        fail_ch.add(c)
                        warn_ch.discard(c)
                        extra.append(f"{c} also fails in {', '.join(bs)} (same acquisition) and departs here too -> fail")
                        p["score"] = max(0, p["score"] - PENALTY["fail"] + PENALTY["warn"])
                    else:
                        warn_ch.add(c)
                        extra.append(f"{c} fails in {', '.join(bs)} (same acquisition); not seen in this crop")
                        p["score"] = max(0, p["score"] - PENALTY["warn"])
                p["checks"]["same_pass"] = check(hits, "fail" if fail_ch - set(p["own"]["fail_channels"]) else "warn",
                                                 unit="channels failing in sibling areas of the same track/frame",
                                                 note="; ".join(extra))
                p["fail_channels"] = sorted(fail_ch, key=CH.index)
                p["warn_channels"] = sorted(warn_ch - fail_ch, key=CH.index)
                p["verdict"] = "fail" if fail_ch else "warn" if p["warn_channels"] else "ok"
                base = p["own"]["reason"] if p["own"]["verdict"] != "ok" else ""
                p["reason"] = "; ".join(x for x in [base] + extra if x)
                if p["verdict"] == "fail" and len(fail_ch) == 1 and len(CH) > 1:
                    other = [c for c in CH if c not in fail_ch and c not in p["warn_channels"]]
                    if other and "is normal" not in p["reason"]:
                        p["reason"] += f"; {other[0]} is normal"
            summarise(q)
    if write:
        for a, q in qcs.items():
            (ROOT / "data/processed" / a / "qc.json").write_text(json.dumps(q, indent=1, ensure_ascii=False))
    return qcs


def table(q: dict) -> str:
    lines = [f"== {q['area']} ({'aligned' if q['aligned'] else 'NOT aligned'}) mode={q['summary'].get('mode')}"]
    for p in q["passes"]:
        ch = p["checks"]
        sh = ch.get("residual_shift", {}).get("value")
        lines.append(f"  {p['date']}  {p['verdict']:<4} {p['score']:>3}  "
                     f"looZ HH {ch.get('loo_HH', {}).get('z', float('nan')):>6} HV {ch.get('loo_HV', {}).get('z', float('nan')):>6}  "
                     f"balZ {ch.get('balance', {}).get('z', float('nan')):>6}  shift {sh:>5}px  {p['reason'][:120]}")
    s = q["summary"]
    lines.append(f"  exclude dates {s['exclude_dates']}  exclude channels {s['exclude_channels']}  marks {len(s['fixed_marks'])}")
    for nn in s["notes"]:
        lines.append(f"  note: {nn}")
    return "\n".join(lines)


# ------------------------------- helpers for run.py --------------------------
def load_qc(area_id: str) -> dict | None:
    p = ROOT / "data/processed" / area_id / "qc.json"
    return json.loads(p.read_text()) if p.exists() else None


def apply_qc(x: torch.Tensor, valid: torch.Tensor, dates: list[str], qc: dict | None):
    """Detection input with failed passes neutralised (image layers should keep the raw pass).

    * a date that failed in every channel -> valid = False on that date (drops out of the
      normalisation, the change point and the per-date masks);
    * a date that failed in one channel -> that channel is replaced by the per-pixel median
      of the other dates, so it carries no information (no fake step, no fake gain),
      while the good channel of the same date is still used.
    Returns (x, valid, {date: [channels]} actually neutralised)."""
    if not qc:
        return x, valid, {}
    x, valid = x.clone(), valid.clone()
    idx = {d: i for i, d in enumerate(dates)}
    done = {}
    s = qc.get("summary", {})
    for d in s.get("exclude_dates", []):
        if d in idx:
            valid[idx[d]] = False
            done[d] = list(CH)
    for d, chans in s.get("exclude_channels", {}).items():
        if d not in idx or d in done:
            continue
        t = idx[d]
        others = [i for i in range(len(dates)) if i != t]
        for name in chans:
            c = CH.index(name)
            x[t, c] = x[others, c].median(0).values
        done[d] = chans
    return x, valid, done


def manifest_quality(qc: dict | None) -> dict:
    """{date: {"quality": ok|warn|fail, "quality_note": str}} for the app manifest."""
    if not qc:
        return {}
    return {p["date"]: {"quality": p["verdict"], "quality_note": p["reason"]} for p in qc["passes"]}


# ------------------------------- report --------------------------------------
def effect_of_exclusion(area_id: str, date: str) -> dict:
    """Change statistics with the flagged pass kept, neutralised as apply_qc does, and dropped."""
    from scripts.detect.stack import load_preview_stack
    from scripts.detect.terrain import cached_water_possible
    st = load_preview_stack(area_id)
    qc = load_qc(area_id)
    water_ok = cached_water_possible(area_id, tuple(st.x.shape[-2:]))
    t = st.dates.index(date)
    km2 = st.km2_per_px
    variants = {}
    for name in ("kept", "qc_neutralised", "date_dropped"):
        if name == "kept":
            x, v, dates = st.x, st.valid, st.dates
        elif name == "qc_neutralised":
            x, v, _ = apply_qc(st.x, st.valid, st.dates, qc)
            dates = st.dates
        else:
            keep = [i for i in range(len(st.dates)) if i != t]
            x, v, dates = st.x[keep], st.valid[keep], [st.dates[i] for i in keep]
        out = ops.run_pipeline(x, v, "preview", dev=torch.device("cpu"), water_ok=water_ok)
        L, val = out["L"], out["valid"]
        allv = val.all(0)
        land = allv & ~(out["water"] & val).any(0)
        sd = ops.temporal_stats(L, val)["std"]
        # stable land: land pixels without a change point
        stable = land & (out["change_date"] < 0)
        cd = out["change_date"]
        by_date = {dates[k]: r(float((cd == k).sum()) * km2, 1) for k in range(1, len(dates))}
        variants[name] = {
            "dates": len(dates),
            "change_point_km2": r(float((cd >= 0).sum()) * km2, 1),
            "change_point_km2_by_date": by_date,
            "hv_temporal_sd_stable_land": r(float(sd[1][stable].mean()), 4),
            "hh_temporal_sd_stable_land": r(float(sd[0][stable].mean()), 4),
            "hv_temporal_sd_all_land": r(float(sd[1][land].mean()), 4),
            "hv_gains": [r(g, 3) for g in out["gains"][:, 1].tolist()] if out["gains"] is not None else None,
            "open_water_km2_on_flagged_date": r(float((out["water"][t] & val[t]).sum()) * km2, 1) if name != "date_dropped" else None,
            "flooded_veg_km2_total": r(float((out["flooded_veg"] & val).any(0).sum()) * km2, 1),
        }
        del out, L, val, sd
        gc.collect()
    del st
    gc.collect()
    return variants


def make_figures(all_q: dict, noalign: dict, effects: dict, profiles: dict, outdir: pathlib.Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    outdir.mkdir(parents=True, exist_ok=True)
    VC = {"ok": 0, "info": 0, "warn": 1, "fail": 2}
    cmap = ListedColormap(["#d9efd5", "#f6c85f", "#d1495b"])
    rows = ["mode", "polarisation", "crid", "time_of_day", "cadence", "coverage_box", "coverage_common_loss",
            "residual_shift", "peak", "gain_HH", "gain_HV", "contrast_HH", "contrast_HV", "balance", "slope_HH", "slope_HV", "loo_HH", "loo_HV",
            "noise_HH", "noise_HV", "floor_HH", "floor_HV", "ceiling_HH", "ceiling_HV", "stripes_HH", "stripes_HV",
            "edge_width"]
    names = list(all_q)
    ncol = 4
    nrow = math.ceil(len(names) / ncol)
    fig, axes = plt.subplots(nrow, ncol, figsize=(4.1 * ncol, 5.6 * nrow))
    for ax in np.atleast_1d(axes).ravel()[len(names):]:
        ax.axis("off")
    for ax, a in zip(np.atleast_1d(axes).ravel(), names):
        q = all_q[a]
        M = np.zeros((len(rows) + 1, len(q["passes"])))
        for j, p in enumerate(q["passes"]):
            for i, rname in enumerate(rows):
                M[i, j] = VC.get(p["checks"].get(rname, {}).get("verdict", "ok"), 0)
            M[-1, j] = VC[p["verdict"]]
        ax.imshow(M, cmap=cmap, vmin=0, vmax=2, aspect="auto", interpolation="nearest")
        ax.axhline(len(rows) - 0.5, color="k", lw=1)
        ax.set_xticks(range(len(q["passes"])))
        ax.set_xticklabels([p["date"][5:] + f"\n{p['score']}" for p in q["passes"]], fontsize=6)
        ax.set_yticks(range(len(rows) + 1))
        ax.set_yticklabels(rows + ["VERDICT"], fontsize=6)
        ax.set_title(a, fontsize=9)
    fig.suptitle("Pass quality per area: rows = checks, columns = dates (score under the date). green ok, yellow warn, red fail", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.98))
    fig.savefig(outdir / "01_dashboard.png", dpi=72)
    plt.close(fig)

    # 2. Armenia channel balance and leave-one-out ratios
    fig, axes = plt.subplots(2, 2, figsize=(11, 6.5))
    for col, a in enumerate(("sevan", "ararat")):
        if a not in all_q:
            continue
        q = all_q[a]
        ds = [p["date"][5:] for p in q["passes"]]
        bal = [p["checks"]["balance"]["value"] for p in q["passes"]]
        ax = axes[0, col]
        ax.plot(ds, bal, "o-", color="#444")
        for j, p in enumerate(q["passes"]):
            if p["checks"]["balance"]["verdict"] != "ok":
                ax.plot(ds[j], bal[j], "o", ms=12, mfc="none", mec="#d1495b", mew=2)
        ax.set_title(f"{a}: HH-HV over stable land per date", fontsize=9)
        ax.tick_params(axis="x", labelsize=7, rotation=45)
        ax = axes[1, col]
        for c, colr in zip(CH, ("#1f77b4", "#2ca02c")):
            ax.plot(ds, [p["checks"][f"loo_{c}"].get("ratio") for p in q["passes"]], "o-", color=colr, label=c)
        ax.axhline(1 + Z_WARN * FLOORS["loo"], ls="--", color="#f6c85f", lw=1, label="warn (ratio floor)")
        ax.axhline(1 + Z_FAIL * FLOORS["loo"], ls="--", color="#d1495b", lw=1, label="fail (ratio floor)")
        ax.set_title(f"{a}: departure from the other dates / usual departure", fontsize=9)
        ax.tick_params(axis="x", labelsize=7, rotation=45)
        ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(outdir / "02_armenia_channel_balance.png", dpi=80)
    plt.close(fig)

    # 3. across-track profiles of the departure per date, HV and HH, Ararat + Sevan + Tonle Sap
    keys = [a for a in ("sevan", "ararat", "tonle-sap") if a in profiles]
    fig, axes = plt.subplots(2, len(keys), figsize=(4.5 * len(keys), 6.5), squeeze=False)
    for j, a in enumerate(keys):
        q = all_q[a]
        for i, c in enumerate(CH):
            ax = axes[i, j]
            for pr in profiles[a]:
                if pr["c"] != i:
                    continue
                p = q["passes"][pr["t"]]
                bad = p["checks"].get(f"loo_{c}", {}).get("verdict") in ("warn", "fail") or p["checks"].get(f"slope_{c}", {}).get("verdict") in ("warn", "fail")
                ax.plot(pr["u_km"], pr["dep"], color="#d1495b" if bad else "#999", lw=2 if bad else 0.8,
                        label=p["date"][5:] if bad else None)
            ax.axhline(0, color="k", lw=0.5)
            ax.set_title(f"{a} {c}: date minus median of other dates", fontsize=9)
            ax.set_xlabel("across-track distance (km)", fontsize=8)
            if any(l.get_label()[0] != "_" for l in ax.get_lines()):
                ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(outdir / "03_across_track_profiles.png", dpi=80)
    plt.close(fig)

    # 4. shift before / after co-registration
    keys = [a for a in ("sevan", "ararat", "tonle-sap") if a in noalign]
    fig, axes = plt.subplots(1, len(keys), figsize=(4.6 * len(keys), 3.6), squeeze=False)
    for ax, a in zip(axes[0], keys):
        ds = [p["date"][5:] for p in all_q[a]["passes"]]
        before = [p["checks"]["residual_shift"]["value"] for p in noalign[a]["passes"]]
        after = [p["checks"]["residual_shift"]["value"] for p in all_q[a]["passes"]]
        xx = np.arange(len(ds))
        ax.bar(xx - 0.2, before, 0.4, color="#d1495b", label="co-registration off")
        ax.bar(xx + 0.2, after, 0.4, color="#2a9d8f", label="co-registration on")
        ax.axhline(GEO["warn_px"], ls="--", color="#f6c85f", lw=1)
        ax.axhline(GEO["fail_px"], ls="--", color="#d1495b", lw=1)
        ax.set_xticks(xx)
        ax.set_xticklabels(ds, rotation=60, fontsize=7)
        ax.set_ylabel("shift vs other dates (px, ~180 m)", fontsize=8)
        ax.set_title(a, fontsize=9)
        ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(outdir / "04_shift_before_after.png", dpi=80)
    plt.close(fig)

    # 5. effect of the exclusion
    keys = list(effects)
    if keys:
        fig, axes = plt.subplots(1, len(keys), figsize=(6 * len(keys), 3.8), squeeze=False)
        for ax, a in zip(axes[0], keys):
            ev = effects[a]["variants"]
            ds = list(ev["kept"]["change_point_km2_by_date"])
            xx = np.arange(len(ds))
            for k, (nm, colr) in enumerate((("kept", "#d1495b"), ("qc_neutralised", "#2a9d8f"), ("date_dropped", "#888"))):
                vals = [ev[nm]["change_point_km2_by_date"].get(d, 0) or 0 for d in ds]
                ax.bar(xx + (k - 1) * 0.27, vals, 0.27, color=colr, label=f"{nm}: total {ev[nm]['change_point_km2']} km²")
            ax.set_xticks(xx)
            ax.set_xticklabels([d[5:] for d in ds], rotation=60, fontsize=7)
            ax.set_ylabel("km² changing for good on this date", fontsize=8)
            ax.set_title(f"{a}: change-point area with {effects[a]['date']} kept / HV neutralised / dropped", fontsize=8)
            ax.legend(fontsize=7)
        fig.tight_layout()
        fig.savefig(outdir / "05_effect_of_exclusion.png", dpi=80)
        plt.close(fig)


def report(areas: list[str]):
    outdir = ROOT / "docs/eda/qc"
    all_q, profiles, noalign, effects = {}, {}, {}, {}
    for a in areas:
        q = qc_area(a, align=True, write=True, keep_profiles=True)
        profiles[a] = q.pop("_profiles")
        q.pop("_series")
        all_q[a] = q
        gc.collect()
    qcs = consolidate_frames(None)
    for a in all_q:
        all_q[a] = qcs[a]
        print(table(qcs[a]), flush=True)
    for a in ("sevan", "ararat", "tonle-sap"):
        if a in areas:
            q = qc_area(a, align=False, write=False)
            noalign[a] = q
            print(table(q), flush=True)
            gc.collect()
    for a in ("sevan", "ararat"):
        if a in all_q:
            s = all_q[a]["summary"]
            flagged = list(s["exclude_channels"]) + s["exclude_dates"]
            date = "2026-10-06" if "2026-10-06" in flagged else (flagged[0] if flagged else None)
            if date:
                effects[a] = {"date": date, "variants": effect_of_exclusion(a, date)}
                print(a, json.dumps(effects[a])[:600], flush=True)
    make_figures(all_q, noalign, effects, profiles, outdir)
    slim = lambda q: {"summary": q["summary"],
                      "passes": [{"date": p["date"], "verdict": p["verdict"], "score": p["score"], "fail_channels": p["fail_channels"],
                                  "warn_channels": p["warn_channels"], "reason": p["reason"],
                                  "key": {k: {kk: p["checks"][k].get(kk) for kk in ("value", "z", "ratio", "pattern", "verdict", "rows", "cols", "metres")
                                              if p["checks"][k].get(kk) is not None}
                                          for k in ("loo_HH", "loo_HV", "balance", "gain_HH", "gain_HV", "contrast_HH", "contrast_HV", "slope_HH", "slope_HV",
                                                    "residual_shift", "applied_shift", "peak") if k in p["checks"]}}
                                 for p in q["passes"]]}
    res = {"generated": dt.datetime.now().isoformat(timespec="seconds"),
           "aligned": {a: slim(q) for a, q in all_q.items()},
           "not_aligned": {a: slim(q) for a, q in noalign.items()},
           "effect_of_exclusion": effects}
    (outdir / "results.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))
    print("wrote", outdir)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("areas", nargs="*")
    ap.add_argument("--report", action="store_true", help="also verification runs, figures and results.json")
    ap.add_argument("--no-align", action="store_true", help="co-registration off (does not write qc.json)")
    a = ap.parse_args(argv)
    torch.set_num_threads(max(1, min(4, torch.get_num_threads())))
    found = areas_with_previews()
    areas = a.areas or found
    skipped = [x for x in areas if x not in found]
    for x in skipped:
        print(f"skip {x}: no previews (meta.json) found")
    areas = [x for x in areas if x in found]
    if a.report:
        report(areas)
        return
    for x in areas:
        q = qc_area(x, align=not a.no_align, write=not a.no_align)
        if a.no_align:
            print(table(q), flush=True)
        gc.collect()
    if not a.no_align:
        qcs = consolidate_frames(None)
        for x in areas:
            print(table(qcs[x]), flush=True)


if __name__ == "__main__":
    main()
