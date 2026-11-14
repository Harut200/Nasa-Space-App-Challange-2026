"""Exploratory data analysis of the NISAR preview stacks.

    .venv/bin/python -m scripts.detect.eda            # all three areas
    .venv/bin/python -m scripts.detect.eda sevan      # one area (some figures need all)

Writes docs/eda/figures/*.png and docs/eda/stats.json. All numbers are computed
with PyTorch tensors; numpy is used only to hand arrays to matplotlib.

IMPORTANT: preview values are 8-bit display brightness, not calibrated
backscatter. Relative patterns and timing are meaningful, absolute values are not.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import sys
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402
import torch.nn.functional as F  # noqa: E402
from matplotlib.colors import LogNorm  # noqa: E402

from scripts.detect.stack import ROOT, load_preview_stack  # noqa: E402

OUT = ROOT / "docs" / "eda"
FIG = OUT / "figures"
AREAS = ["tonle-sap", "sevan", "ararat"]
TITLE = {"tonle-sap": "Tonle Sap", "sevan": "Lake Sevan", "ararat": "Ararat Valley"}
GAP = (dt.date(2026, 7, 27), dt.date(2026, 8, 10))
COL = ["#1b6ca8", "#d1495b", "#2e8b57", "#edae49", "#6a4c93", "#444444", "#00798c"]
SEVAN_TRUE_KM2 = 1242.0
STATS: dict = {}

plt.rcParams.update({"figure.dpi": 110, "savefig.dpi": 110, "font.size": 9, "axes.grid": True,
                     "grid.alpha": 0.25, "axes.titlesize": 10, "figure.facecolor": "white"})


# ----------------------------------------------------------------- helpers
def D(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def f(x, nd=4):
    return round(float(x), nd)


def fl(t, nd=4):
    return [round(float(v), nd) for v in t]


def save(fig, name, dpi=110):
    fig.savefig(FIG / name, bbox_inches="tight", dpi=dpi)
    plt.close(fig)
    kb = (FIG / name).stat().st_size / 1024
    print(f"  figure {name} {kb:.0f} KB")


def time_axis(ax, dates, label="Date of radar pass (2026)"):
    ax.axvspan(GAP[0], GAP[1], color="#bbbbbb", alpha=0.5, lw=0)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.xaxis.set_minor_locator(mdates.DayLocator(interval=12))
    ax.set_xlim(D(dates[0]) - dt.timedelta(days=4), D(dates[-1]) + dt.timedelta(days=4))
    ax.set_xlabel(label)
    ax.text(GAP[0] + (GAP[1] - GAP[0]) / 2, ax.get_ylim()[1], "gap", ha="center", va="top", fontsize=7, color="#555")


def otsu(v: torch.Tensor, nbins: int = 256) -> float:
    """Otsu threshold of values in 0..1 (between-class variance maximum)."""
    h = torch.histc(v.double(), bins=nbins, min=0.0, max=1.0)
    p = h / h.sum()
    centers = (torch.arange(nbins, dtype=torch.float64) + 0.5) / nbins
    w0 = torch.cumsum(p, 0)
    mu = torch.cumsum(p * centers, 0)
    sb = (mu[-1] * w0 - mu) ** 2 / (w0 * (1 - w0) + 1e-12)
    return float((int(torch.argmax(sb)) + 1) / nbins)


def otsu2(v: torch.Tensor):
    """Two-level Otsu: first split dark from bright, then split the dark class again.

    Returns (t1, t2): t2 < t1 separates the darkest class (open-water proxy) from
    the intermediate class (dark land, wet fields, ...).
    """
    t1 = otsu(v)
    low = v[v < t1]
    t2 = otsu(low / t1) * t1
    return t1, t2


def box_mean(x: torch.Tensor, k: int) -> torch.Tensor:
    """k x k moving mean of [..., H, W] (same size, edge-replicated)."""
    sh = x.shape
    y = x.reshape(-1, 1, *sh[-2:])
    y = F.avg_pool2d(F.pad(y, (k // 2,) * 4, mode="replicate"), k, stride=1)
    return y.reshape(sh)


def components_from_seed(mask: torch.Tensor, seed: tuple[int, int]) -> torch.Tensor:
    """Connected region (4-neighbour) of `mask` containing `seed`, via repeated dilation."""
    reg = torch.zeros_like(mask)
    reg[seed] = True
    reg &= mask
    for _ in range(2000):
        new = (F.max_pool2d(reg[None, None].float(), 3, 1, 1)[0, 0] > 0) & mask
        if int((new != reg).sum()) == 0:
            break
        reg = new
    return reg


def quantiles(v: torch.Tensor, qs):
    if v.numel() > 4_000_000:  # torch.quantile limit; subsample deterministically
        v = v[:: int(np.ceil(v.numel() / 4_000_000))]
    return torch.quantile(v.float(), torch.tensor(qs, dtype=torch.float32))


def common_valid(st):
    return st.valid.all(0)


def spot_window(st, lon, lat, k=5):
    r, c = st.lonlat_to_rc(lon, lat)
    h, w = st.x.shape[-2:]
    r = min(max(r, k // 2), h - 1 - k // 2)
    c = min(max(c, k // 2), w - 1 - k // 2)
    return r, c


def window_mean(st, r, c, k=5):
    sl = (slice(None), slice(None), slice(r - k // 2, r + k // 2 + 1), slice(c - k // 2, c + k // 2 + 1))
    v = st.valid[:, r - k // 2:r + k // 2 + 1, c - k // 2:c + k // 2 + 1].float().mean()
    return st.x[sl].mean((2, 3)), float(v)  # [T,2]


def rc_to_lonlat(st, r, c):
    w, s, e, n = st.bounds
    h, wd = st.x.shape[-2:]
    return w + (c + 0.5) / wd * (e - w), n - (r + 0.5) / h * (n - s)


# ----------------------------------------------------------------- 1 distributions
def fig_distributions(sts):
    fig, axs = plt.subplots(3, 2, figsize=(10, 9), sharex="col")
    out = {}
    qs = [0.05, 0.25, 0.5, 0.75, 0.95]
    for i, a in enumerate(AREAS):
        st = sts[a]
        out[a] = {}
        for ch, name in enumerate(["HH", "HV"]):
            q = torch.stack([quantiles(st.x[t, ch][st.valid[t]], qs) for t in range(len(st.dates))])  # [T,5]
            ax = axs[i, ch]
            d = [D(s) for s in st.dates]
            ax.fill_between(d, q[:, 0], q[:, 4], color=COL[ch], alpha=0.15, label="5-95 %")
            ax.fill_between(d, q[:, 1], q[:, 3], color=COL[ch], alpha=0.35, label="25-75 %")
            ax.plot(d, q[:, 2], "o-", color=COL[ch], label="median")
            ax.set_title(f"{TITLE[a]}: {name} brightness quantiles per pass")
            ax.set_ylabel("brightness (0-1, display units)")
            ax.set_ylim(0, 1)
            if i == 2:
                time_axis(ax, st.dates)
            else:
                ax.axvspan(GAP[0], GAP[1], color="#bbbbbb", alpha=0.5, lw=0)
            if i == 0 and ch == 0:
                ax.legend(fontsize=7, loc="upper right")
            out[a][name] = {"dates": st.dates, "q05_25_50_75_95": [fl(r) for r in q]}
    fig.tight_layout()
    save(fig, "01_distribution_per_date.png")
    STATS["distribution_quantiles"] = out


# ----------------------------------------------------------------- 2 stable pixels / drift
def fig_stability(sts):
    fig, axs = plt.subplots(3, 2, figsize=(10, 8.5), sharex="col")
    out = {}
    for i, a in enumerate(AREAS):
        st = sts[a]
        cv = common_valid(st)
        b = st.x.mean(1)  # [T,H,W]
        mean_t = b.mean(0)
        std_t = b.std(0)
        lo, hi = torch.quantile(mean_t[cv][:: max(1, int(cv.sum()) // 2_000_000)], torch.tensor([0.5, 0.95]))
        cand = cv & (mean_t > lo) & (mean_t < hi)  # mid-to-bright pixels
        cut = torch.quantile(std_t[cand][:: max(1, int(cand.sum()) // 2_000_000)], 0.05)
        stable = cand & (std_t <= cut)
        ref = st.x[:, :, stable].median(0).values if False else None
        out[a] = {"n_stable_px": int(stable.sum()), "mean_brightness_range_selected": [f(lo), f(hi)], "std_cutoff": f(cut)}
        # leave-one-out check: pick stable pixels without date t, measure median at t
        T = len(st.dates)
        loo = []
        for t in range(T):
            keep = [k for k in range(T) if k != t]
            s2 = b[keep].std(0)
            cut2 = torch.quantile(s2[cand][:: max(1, int(cand.sum()) // 2_000_000)], 0.05)
            sel = cand & (s2 <= cut2)
            loo.append([float(st.x[t, 0][sel].median()), float(st.x[t, 1][sel].median())])
        loo = torch.tensor(loo)
        for ch, name in enumerate(["HH", "HV"]):
            med = torch.stack([st.x[t, ch][stable].median() for t in range(T)])
            allmed = torch.stack([st.x[t, ch][st.valid[t]].median() for t in range(T)])
            ax = axs[i, ch]
            d = [D(s) for s in st.dates]
            ax.plot(d, med, "o-", color=COL[ch], label="stable pixels (chosen using all dates)")
            ax.plot(d, loo[:, ch], "s--", color=COL[ch], alpha=0.6, label="stable pixels (chosen without that date)")
            ax.plot(d, allmed, "^:", color="#555", label="all valid pixels")
            ax.set_title(f"{TITLE[a]}: {name} median of radiometrically stable pixels")
            ax.set_ylabel("median brightness")
            if i == 2:
                time_axis(ax, st.dates)
            else:
                ax.axvspan(GAP[0], GAP[1], color="#bbbbbb", alpha=0.5, lw=0)
            if i == 0 and ch == 0:
                ax.legend(fontsize=6.5)
            out[a][name] = {
                "stable_median_per_date": fl(med), "stable_median_leave_date_out": fl(loo[:, ch]),
                "all_pixels_median_per_date": fl(allmed),
                "stable_range_max_minus_min": f(med.max() - med.min()),
                "stable_range_loo": f(loo[:, ch].max() - loo[:, ch].min()),
                "stable_std_across_dates": f(med.std()), "stable_mean": f(med.mean()),
                "stable_range_pct_of_mean": f(100 * (med.max() - med.min()) / med.mean(), 2),
                "stable_range_loo_pct_of_mean": f(100 * (loo[:, ch].max() - loo[:, ch].min()) / med.mean(), 2),
            }
        # per-date linear gain/offset of stable pixels vs temporal-median reference image
        ref = st.x[:, :, :, :].median(0).values  # [2,H,W]
        gains = []
        for t in range(T):
            row = []
            for ch in range(2):
                xv, yv = ref[ch][stable], st.x[t, ch][stable]
                xm, ym = xv.mean(), yv.mean()
                g = ((xv - xm) * (yv - ym)).mean() / ((xv - xm) ** 2).mean()
                row.append([f(g), f(ym - g * xm)])
            gains.append(row)
        out[a]["gain_offset_vs_median_image_HH_HV"] = gains
        out[a]["dates"] = st.dates
    fig.tight_layout()
    save(fig, "02_stable_pixel_drift.png")
    STATS["stability"] = out


# ----------------------------------------------------------------- 3 temporal maps
def fig_maps(st):
    a = st.area["id"]
    cv = common_valid(st)
    fig, axs = plt.subplots(2, 3, figsize=(11, 7.5 if a != "tonle-sap" else 7))
    out = {}
    w, s, e, n = st.bounds
    for ch, name in enumerate(["HH", "HV"]):
        x = st.x[:, ch]
        m, sd = x.mean(0), x.std(0)
        cvar = sd / (m + 0.02)
        for k, (arr, ttl, cm, vm) in enumerate([(m, "mean over 10 passes", "gray", (0, 1)),
                                                 (sd, "standard deviation (how much it moves)", "magma", (0, float(torch.quantile(sd[cv][::7], 0.99)))),
                                                 (cvar, "coefficient of variation (std / mean)", "viridis", (0, float(torch.quantile(cvar[cv][::7], 0.99))))]):
            ax = axs[ch, k]
            img = arr.clone()
            img[~cv] = float("nan")
            im = ax.imshow(img.numpy(), cmap=cm, vmin=vm[0], vmax=vm[1], extent=(w, e, s, n), aspect="auto")
            ax.set_title(f"{name}: {ttl}", fontsize=8)
            ax.set_xlabel("longitude")
            ax.set_ylabel("latitude")
            ax.grid(False)
            fig.colorbar(im, ax=ax, fraction=0.046)
        out[name] = {"mean_of_mean": f(m[cv].mean()), "mean_std": f(sd[cv].mean()), "p99_std": f(torch.quantile(sd[cv][::7], 0.99)),
                     "mean_cv": f(cvar[cv].mean()), "share_px_std_gt_0.10": f((sd[cv] > 0.10).float().mean())}
    # smooth gradients in temporal std: mean std by quarter of the map (north to south, west to east)
    h_, w_ = cv.shape
    for ch, name in enumerate(["HH", "HV"]):
        sd = st.x[:, ch].std(0)
        out[name]["temporal_std_by_row_quarter_north_to_south"] = [f(sd[i * h_ // 4:(i + 1) * h_ // 4][cv[i * h_ // 4:(i + 1) * h_ // 4]].mean()) for i in range(4)]
        out[name]["temporal_std_by_col_quarter_west_to_east"] = [f(sd[:, i * w_ // 4:(i + 1) * w_ // 4][cv[:, i * w_ // 4:(i + 1) * w_ // 4]].mean()) for i in range(4)]
    fig.suptitle(f"{TITLE[a]}: temporal mean, standard deviation and variation (pixels valid on all dates)")
    fig.tight_layout()
    save(fig, f"03_temporal_maps_{a}.png", dpi=58)
    STATS.setdefault("temporal_maps", {})[a] = out


# ----------------------------------------------------------------- 4 joint histogram
def joint_hist(st, t, nb=64):
    v = st.valid[t]
    hh = (st.x[t, 0][v] * (nb - 1e-3)).long()
    hv = (st.x[t, 1][v] * (nb - 1e-3)).long()
    return torch.bincount(hh * nb + hv, minlength=nb * nb).reshape(nb, nb).float()  # [hh, hv]


def fig_joint(st):
    h0, h1 = joint_hist(st, 0), joint_hist(st, len(st.dates) - 1)
    fig, axs = plt.subplots(1, 3, figsize=(12, 4.2))
    for ax, h, ttl in [(axs[0], h0, f"first pass {st.dates[0]}"), (axs[1], h1, f"last pass {st.dates[-1]}")]:
        im = ax.imshow((h.T).numpy() + 1, origin="lower", extent=(0, 1, 0, 1), norm=LogNorm(vmin=1, vmax=float(max(h0.max(), h1.max()))), cmap="viridis")
        ax.set_title(f"Tonle Sap, {ttl}")
        ax.set_xlabel("HH brightness")
        ax.set_ylabel("HV brightness")
        ax.grid(False)
        fig.colorbar(im, ax=ax, fraction=0.046, label="pixels (log)")
    d = (h1 - h0) / h0.sum() * 1e4  # change in pixels per 10,000 (valid counts can differ)
    lim = float(d.abs().quantile(0.995))
    im = axs[2].imshow(d.T.numpy(), origin="lower", extent=(0, 1, 0, 1), cmap="RdBu_r", vmin=-lim, vmax=lim)
    axs[2].set_title("last minus first (red = more pixels now)")
    axs[2].set_xlabel("HH brightness")
    axs[2].set_ylabel("HV brightness")
    axs[2].grid(False)
    fig.colorbar(im, ax=axs[2], fraction=0.046, label="per 10,000 valid px")
    fig.tight_layout()
    save(fig, "04_joint_hh_hv_tonle_sap.png")
    # cluster summaries by simple boxes in (HH,HV) brightness; numbers are indicative only
    boxes = {"dark water-like (HH<0.15 & HV<0.15)": lambda hh, hv: (hh < 0.15) & (hv < 0.15),
             "HH-dominant bright (HH-HV>0.20, HH>0.5)": lambda hh, hv: (hh - hv > 0.20) & (hh > 0.5),
             "bright both = forest-like (HH>0.5 & HV>0.5, |HH-HV|<=0.2)": lambda hh, hv: (hh > 0.5) & (hv > 0.5) & ((hh - hv).abs() <= 0.2),
             "mid brightness (rest)": None}
    res = {}
    for t in (0, len(st.dates) - 1):
        v = st.valid[t]
        hh, hv = st.x[t, 0][v], st.x[t, 1][v]
        used = torch.zeros_like(hh, dtype=torch.bool)
        r = {}
        for k, fn in boxes.items():
            m = fn(hh, hv) if fn else ~used
            used |= m
            r[k] = f(m.float().mean())
        r["valid_px"] = int(v.sum())
        r["mean_HH_minus_HV"] = f((hh - hv).mean())
        res[st.dates[t]] = r
    STATS["joint_hist_tonle_sap"] = res
    # forest ring: pixels that were forest-like (HH>0.5, HV>0.5, not saturated) on the first date, followed through time
    cv = common_valid(st)
    ring = cv & (st.x[0, 0] > 0.5) & (st.x[0, 1] > 0.5) & (st.x[0, 0] < 0.95) & (st.x[0, 1] < 0.95)
    fr = {"n_px": int(ring.sum()), "dates": st.dates, "HH_mean": [], "HV_mean": [], "HH_minus_HV_mean": [], "share_HH_dominant_gt_0.2": [], "share_dark_both_lt_0.25": []}
    for t in range(len(st.dates)):
        hh, hv = st.x[t, 0][ring], st.x[t, 1][ring]
        fr["HH_mean"].append(f(hh.mean())); fr["HV_mean"].append(f(hv.mean())); fr["HH_minus_HV_mean"].append(f((hh - hv).mean()))
        fr["share_HH_dominant_gt_0.2"].append(f(((hh - hv) > 0.2).float().mean())); fr["share_dark_both_lt_0.25"].append(f(((hh < 0.25) & (hv < 0.25)).float().mean()))
    STATS["forest_ring_tonle_sap"] = fr


# ----------------------------------------------------------------- 5/6 water
def water_series(st, thr, common):
    b = st.x.mean(1)
    return [float(((b[t] < thr) & common).sum()) * st.km2_per_px for t in range(len(st.dates))]


def fig_water(sts):
    out = {}
    fig, axs = plt.subplots(1, 3, figsize=(13, 4))
    for i, a in enumerate(["tonle-sap", "sevan"]):
        st = sts[a]
        b = st.x.mean(1)
        cv = common_valid(st)
        vals = b[st.valid]
        t1, thr = otsu2(vals[:: max(1, vals.numel() // 6_000_000)])
        # also per-date Otsu to see whether the thresholds move
        per_date = [[f(z) for z in otsu2(b[t][st.valid[t]][:: max(1, int(st.valid[t].sum()) // 1_000_000)])] for t in range(len(st.dates))]
        ser = {k: water_series(st, thr * k, cv) for k in (0.8, 1.0, 1.2)}
        ser1 = water_series(st, t1, cv)
        valid_km2 = [float(st.valid[t].sum()) * st.km2_per_px for t in range(len(st.dates))]
        out[a] = {"otsu_threshold_pooled": f(thr), "otsu_level1_threshold": f(t1), "level1_dark_class_km2": fl(ser1, 1),
                  "otsu_per_date_level1_level2": per_date, "common_valid_km2": f(float(cv.sum()) * st.km2_per_px, 1),
                  "valid_km2_per_date": fl(valid_km2, 1),
                  "water_km2": {f"{k:.1f}x": fl(v, 1) for k, v in ser.items()}, "dates": st.dates}
        ax = axs[i]
        d = [D(s) for s in st.dates]
        ax.fill_between(d, ser[0.8], ser[1.2], color=COL[0], alpha=0.25, label="threshold -20 % to +20 %")
        ax.plot(d, ser[1.0], "o-", color=COL[0], label=f"water: 2nd-level Otsu {thr:.3f}")
        ax.plot(d, ser1, "s:", color="#777", label=f"all dark pixels: 1st-level Otsu {t1:.3f}")
        if a == "sevan":
            r0, c0 = st.lonlat_to_rc(45.30, 40.35)
            conn = {k: [float(components_from_seed((b[t] < thr * k) & st.valid[t], (r0, c0)).sum()) * st.km2_per_px for t in range(len(st.dates))]
                    for k in (0.8, 1.0, 1.2)}
            out[a]["lake_connected_km2"] = {f"{k:.1f}x": fl(v, 1) for k, v in conn.items()}
            ax.fill_between(d, conn[0.8], conn[1.2], color=COL[2], alpha=0.25)
            ax.plot(d, conn[1.0], "o-", color=COL[2], label="dark pixels connected to the lake (flood fill)")
            ax.axhline(SEVAN_TRUE_KM2, color=COL[1], ls="--", label="true area 1,242 km2")
        ax.set_title(f"{TITLE[a]}: open-water proxy area")
        ax.set_ylabel("area, km2")
        ax.legend(fontsize=7)
        time_axis(ax, st.dates)
        ax2 = axs[2]
        ax2.hist(vals[:: max(1, vals.numel() // 3_000_000)].numpy(), bins=100, range=(0, 1), histtype="step", color=COL[i], density=True, label=TITLE[a])
        ax2.axvline(thr, color=COL[i], ls="--")
        ax2.axvline(t1, color=COL[i], ls=":")
    axs[2].set_title("Brightness histogram (mean of HH and HV), all dates;\ndashed = water threshold, dotted = dark/bright threshold")
    axs[2].set_xlabel("brightness")
    axs[2].set_ylabel("density")
    axs[2].legend(fontsize=7)
    fig.tight_layout()
    save(fig, "05_water_area.png")
    STATS["water"] = out
    return out


def sevan_lake(st, thr):
    """Lake analysis: connected dark region per date, a fixed lake mask, brightness as wind evidence."""
    cv = common_valid(st)
    b = st.x.mean(1)
    r0, c0 = st.lonlat_to_rc(45.30, 40.35)
    dark = b < thr
    comp = []
    for t in range(len(st.dates)):
        reg = components_from_seed(dark[t] & st.valid[t], (r0, c0))
        comp.append(reg)
    areas = [float(r.sum()) * st.km2_per_px for r in comp]
    # fixed lake mask: pixels in the connected dark region on the three calmest-looking early dates, eroded 2 px off the shore
    early = torch.stack(comp[:4]).all(0)
    inner = ~(F.max_pool2d((~early)[None, None].float(), 5, 1, 2)[0, 0] > 0)  # erode by 2 px
    inner &= cv
    # brightness within fixed lake mask
    hh = torch.stack([st.x[t, 0][inner].mean() for t in range(len(st.dates))])
    hv = torch.stack([st.x[t, 1][inner].mean() for t in range(len(st.dates))])
    hh_sd = torch.stack([st.x[t, 0][inner].std() for t in range(len(st.dates))])
    brightfrac = [f((b[t][inner] > thr).float().mean()) for t in range(len(st.dates))]
    p90 = torch.stack([torch.quantile(st.x[t, 0][inner], 0.9) for t in range(len(st.dates))])
    # lake pixel count vs bbox
    res = {"seed_rc": [r0, c0], "connected_dark_area_km2_per_date": fl(areas, 1),
           "fixed_lake_mask_px": int(inner.sum()), "fixed_lake_mask_km2": f(float(inner.sum()) * st.km2_per_px, 1),
           "early_dark_union_core_km2": f(float(early.sum()) * st.km2_per_px, 1),
           "lake_mean_HH": fl(hh), "lake_mean_HV": fl(hv), "lake_HH_spatial_std": fl(hh_sd), "lake_HH_p90": fl(p90),
           "share_lake_px_brighter_than_otsu": brightfrac, "dates": st.dates,
           "bbox_clips_lake": bool(early[0].any() or early[-1].any() or early[:, 0].any() or early[:, -1].any())}
    return res, inner, comp


def fig_sevan_wind(st, thr):
    res, inner, comp = sevan_lake(st, thr)
    STATS["sevan_lake"] = res
    fig = plt.figure(figsize=(12, 7))
    gs = fig.add_gridspec(2, 4, hspace=0.55)
    ax = fig.add_subplot(gs[0, :2])
    d = [D(s) for s in st.dates]
    ax.plot(d, res["lake_mean_HH"], "o-", color=COL[0], label="HH")
    ax.plot(d, res["lake_mean_HV"], "o-", color=COL[2], label="HV")
    ax.set_title("Lake Sevan interior: mean brightness (fixed lake mask)")
    ax.set_ylabel("brightness")
    ax.legend(fontsize=7)
    time_axis(ax, st.dates)
    ax = fig.add_subplot(gs[0, 2:])
    ax.plot(d, res["share_lake_px_brighter_than_otsu"], "o-", color=COL[1])
    ax.set_title("Share of lake-interior pixels brighter than the water threshold")
    ax.set_ylabel("share of pixels")
    time_axis(ax, st.dates)
    w, s, e, n = st.bounds
    for k, t in enumerate([0, 5, 8, 9]):
        ax = fig.add_subplot(gs[1, k])
        img = st.x[t, 0].clone()
        img[~inner] = float("nan")
        im = ax.imshow(img.numpy(), cmap="viridis", vmin=0, vmax=0.3, extent=(w, e, s, n), aspect="auto")
        ax.set_title(f"HH over lake, {st.dates[t]}", fontsize=8)
        ax.grid(False)
        ax.set_xlabel("longitude")
        if k == 0:
            ax.set_ylabel("latitude")
    fig.colorbar(im, ax=fig.axes[-4:], fraction=0.02, label="HH brightness (clipped at 0.3)")
    save(fig, "06_sevan_lake_brightness.png")


# ----------------------------------------------------------------- 7 flooded vegetation
def fig_flood(st, thr_water):
    cv = common_valid(st)
    T = len(st.dates)
    # per-date radiometric offset from stable pixels (median shift vs first date)
    b = st.x.mean(1)
    mean_t, std_t = b.mean(0), b.std(0)
    cand = cv & (mean_t > torch.quantile(mean_t[cv][::7], 0.5))
    stable = cand & (std_t <= torch.quantile(std_t[cand][::7], 0.05))
    off = torch.stack([(st.x[t][:, stable] - st.x[0][:, stable]).median(1).values for t in range(T)])  # [T,2]
    base_land = cv & (st.x[0].mean(0) > thr_water)
    def run(dhh_min, dhv_max):
        res = []
        for t in range(T):
            dhh = box_mean(st.x[t, 0] - st.x[0, 0], 3) - off[t, 0]
            dhv = box_mean(st.x[t, 1] - st.x[0, 1], 3) - off[t, 1]
            m = base_land & (dhh > dhh_min) & (dhv < dhv_max)
            res.append(m)
        return res
    main = run(0.10, 0.05)
    sens = {f"dHH>{a:.2f}, dHV<{b_:.2f}": fl([float(m.sum()) * st.km2_per_px for m in run(a, b_)], 1)
            for a, b_ in [(0.07, 0.05), (0.15, 0.05), (0.10, 0.03), (0.10, 0.08)]}
    area = [float(m.sum()) * st.km2_per_px for m in main]
    # noise floor: how many pixels flagged between two consecutive pre-gap passes (HH rise relative to date 0) is main[1]
    STATS["flooded_vegetation"] = {"rule": "HH rose by >0.10 vs first date (3x3 mean, after removing per-date stable-pixel offset) while HV rose by <0.05; pixel not water-like on first date",
                                    "base_land_km2": f(float(base_land.sum()) * st.km2_per_px, 1), "area_km2_per_date": fl(area, 1),
                                    "per_date_offsets_vs_first_HH_HV": [fl(o) for o in off], "sensitivity": sens, "dates": st.dates,
                                    "share_of_base_land_last_date": f(float(main[-1].sum()) / float(base_land.sum()))}
    fig, axs = plt.subplots(1, 3, figsize=(14, 4.6))
    d = [D(s) for s in st.dates]
    ax = axs[0]
    lo = [min(v) for v in zip(*sens.values())]
    hi = [max(v) for v in zip(*sens.values())]
    ax.fill_between(d, lo, hi, color=COL[2], alpha=0.25, label="range over other thresholds")
    ax.plot(d, area, "o-", color=COL[2], label="main rule")
    ax.set_title("Tonle Sap: land pixels where HH rose but HV did not")
    ax.set_ylabel("area, km2")
    ax.legend(fontsize=7)
    time_axis(ax, st.dates)
    w, s, e, n = st.bounds
    for ax, t in [(axs[1], 6), (axs[2], T - 1)]:
        img = torch.zeros(*cv.shape, 3)
        g = (b[0] * 0.8).clamp(0, 1)
        img[:] = g[..., None]
        img[main[t]] = torch.tensor([0.1, 0.8, 0.2])
        img[~cv] = 1.0
        ax.imshow(img.numpy(), extent=(w, e, s, n), aspect="auto")
        ax.set_title(f"flagged pixels (green) on {st.dates[t]}", fontsize=9)
        ax.set_xlabel("longitude")
        ax.set_ylabel("latitude")
        ax.grid(False)
    fig.tight_layout()
    save(fig, "07_flooded_vegetation_proxy.png", dpi=75)
    STATS["flooded_vegetation"]["map_dates"] = [st.dates[6], st.dates[-1]]


# ----------------------------------------------------------------- 8 spots
SPOTS = {
    "tonle-sap": {"Mid-lake open water": (104.1, 12.8), "Prek Toal flooded forest": (103.7, 13.1), "Chong Kneas": (103.84, 13.27),
                  "Flood corridor to Phnom Penh": (104.85, 12.0)},
    "sevan": {"Mid-lake": (45.30, 40.35), "Gavar shore": (45.13, 40.35), "Martuni shore": (45.30, 40.14), "Sevan town": (44.95, 40.55)},
    "ararat": {"Masis fields": (44.43, 40.07), "Fish ponds Ararat-Vedi": (44.60, 39.90), "Vedi vineyards": (44.73, 39.91),
               "Yerevan urban reference": (44.51, 40.18)},
}


def stable_forest_spot(st):
    """5x5 window in the south-west quarter: bright (above p70) with the lowest temporal std."""
    cv = common_valid(st)
    b = st.x.mean(1)
    m5, s5 = box_mean(b.mean(0), 5), box_mean(b.std(0), 5)
    unsat = (box_mean(st.x[:, 0].mean(0), 5) < 0.93) & (box_mean(st.x[:, 1].mean(0), 5) < 0.93)  # skip pixels pinned near white
    h, w = cv.shape
    reg = torch.zeros_like(cv)
    reg[h // 2:, : w // 2] = True
    reg &= cv & unsat & (F.avg_pool2d(cv[None, None].float(), 5, 1, 2)[0, 0] > 0.999)
    q = torch.quantile(m5[reg][::5], 0.7)
    score = torch.where(reg & (m5 > q), s5, torch.full_like(s5, 9.0))
    idx = int(torch.argmin(score))
    return idx // w, idx % w


def fig_spots(sts):
    out = {}
    for a in AREAS:
        st = sts[a]
        spots = dict(SPOTS[a])
        info = {}
        fig, axs = plt.subplots(1, 2, figsize=(12, 4))
        d = [D(s) for s in st.dates]
        lst = []
        for name, (lon, lat) in spots.items():
            r, c = spot_window(st, lon, lat)
            lst.append((name, r, c, lon, lat))
        if a == "tonle-sap":
            r, c = stable_forest_spot(st)
            lo, la = rc_to_lonlat(st, r, c)
            lst.append(("Upland forest reference (auto-picked, SW, not saturated)", r, c, lo, la))
        for k, (name, r, c, lon, lat) in enumerate(lst):
            ts, vfrac = window_mean(st, r, c)
            info[name] = {"requested_lonlat": [lon, lat], "row_col": [r, c], "window_valid_fraction": f(vfrac, 3),
                          "HH": fl(ts[:, 0]), "HV": fl(ts[:, 1])}
            for ch in range(2):
                axs[ch].plot(d, ts[:, ch], "o-", color=COL[k % len(COL)], label=name, ms=4)
        for ch, nm in enumerate(["HH", "HV"]):
            axs[ch].set_title(f"{TITLE[a]}: {nm} at named spots (5x5 pixel mean)")
            axs[ch].set_ylabel("brightness")
            time_axis(axs[ch], st.dates)
        axs[0].legend(fontsize=6.5)
        fig.tight_layout()
        save(fig, f"08_spots_{a}.png")
        info["_dates"] = st.dates
        out[a] = info
    STATS["spots"] = out


# ----------------------------------------------------------------- 9 Armenia checks
def departures(x, cv, groups, dates):
    """Per-date departure from the mean of the two neighbouring passes, by pixel group, plus a stretch test for 2026-10-06."""
    T = x.shape[0]
    res = {}
    for gname, g in groups.items():
        rows = []
        for t in range(1, T - 1):
            dd = (x[t] - 0.5 * (x[t - 1] + x[t + 1]))[:, g]
            rows.append({"date": dates[t], "mean_abs_HH": f(dd[0].abs().mean()), "mean_abs_HV": f(dd[1].abs().mean()),
                         "mean_signed_HH": f(dd[0].mean()), "mean_signed_HV": f(dd[1].mean())})
        res[gname] = rows
    res["mean_abs_diff_between_passes_matrix_HH_HV_avg"] = [[f((x[i] - x[j])[:, cv].abs().mean()) for j in range(T)] for i in range(T)]
    t = dates.index("2026-10-06")
    nb = 0.5 * (x[t - 1] + x[t + 1])
    reg = []
    for ch in range(2):
        xv, yv = nb[ch][cv], x[t, ch][cv]
        xm, ym = xv.mean(), yv.mean()
        g_ = ((xv - xm) * (yv - ym)).mean() / ((xv - xm) ** 2).mean()
        r_ = ((xv - xm) * (yv - ym)).mean() / (xv.std(unbiased=False) * yv.std(unbiased=False))
        reg.append({"slope": f(g_), "offset": f(ym - g_ * xm), "corr": f(r_)})
    res["oct06_vs_neighbour_mean_regression_HH_HV"] = reg
    qs = [0.05, 0.25, 0.5, 0.75, 0.95]
    res["quantiles_HH_HV_oct06_vs_neigh_mean"] = {"oct06": [fl(quantiles(x[t, ch][cv], qs)) for ch in range(2)],
                                                   "neighbours_mean": [fl(quantiles(nb[ch][cv], qs)) for ch in range(2)]}
    return res


def armenia_checks(sts, thr_sevan, regs):
    out = {}
    for a in ["sevan", "ararat"]:
        st = sts[a]
        cv = common_valid(st)
        b = st.x.mean(1)
        water = (b.mean(0) < thr_sevan) & cv
        groups = {"whole": cv, "land": ~water & cv, "water": water}
        groups = {k: g for k, g in groups.items() if int(g.sum()) > 0}
        res = departures(st.x, cv, groups, st.dates)
        res["registered"] = departures(regs[a][0], cv, groups, st.dates)
        res["share_water_like_px"] = f(water.float().mean())
        out[a] = res
    # figure (d): bar chart of mean abs departure whole box, both areas
    fig, axs = plt.subplots(1, 3, figsize=(13, 3.8))
    for k, a in enumerate(["sevan", "ararat"]):
        rows = out[a]["whole"]
        rr = out[a]["registered"]["whole"]
        d = [D(r["date"]) for r in rows]
        axs[k].plot(d, [r["mean_abs_HH"] for r in rows], "o--", color=COL[0], alpha=0.55, label="HH as loaded")
        axs[k].plot(d, [r["mean_abs_HV"] for r in rows], "o--", color=COL[2], alpha=0.55, label="HV as loaded")
        axs[k].plot(d, [r["mean_abs_HH"] for r in rr], "s-", color=COL[0], label="HH after re-aligning")
        axs[k].plot(d, [r["mean_abs_HV"] for r in rr], "s-", color=COL[2], label="HV after re-aligning")
        axs[k].set_title(f"{TITLE[a]}: how far each pass departs from\nthe average of its two neighbours")
        axs[k].set_ylabel("mean absolute difference (brightness)")
        axs[k].legend(fontsize=7)
        time_axis(axs[k], [rows[0]["date"], rows[-1]["date"]])
    # (e) longitude profile over the large extent
    big = load_preview_stack("sevan", bbox=(43.6, 39.6, 46.0, 40.9))
    cvb = common_valid(big)
    w, s, e, n = big.bounds
    wd = big.x.shape[-1]
    lon = w + (torch.arange(wd) + 0.5) * (e - w) / wd
    cnt = cvb.float().sum(0)
    ok = cnt > 0.25 * cvb.shape[0]
    prof = {}
    for ch, nm in enumerate(["HH", "HV"]):
        m = (big.x[:, ch].mean(0) * cvb).sum(0) / (cvb.float().sum(0) + 1e-9)
        prof[nm] = m
    # per-date HV profile, and water-only / land-only profile
    bb = big.x.mean(1).mean(0)
    wat = (bb < thr_sevan) & cvb
    lnd = (bb >= thr_sevan) & cvb
    def colmean(ch, mask):
        return (big.x[:, ch].mean(0) * mask).sum(0) / (mask.float().sum(0) + 1e-9), mask.float().sum(0)
    hv_l, n_l = colmean(1, lnd)
    hv_w, n_w = colmean(1, wat)
    hh_l, _ = colmean(0, lnd)
    ax = axs[2]
    ax.plot(lon[ok], prof["HH"][ok], color=COL[0], label="HH, all valid px")
    ax.plot(lon[ok], prof["HV"][ok], color=COL[2], label="HV, all valid px")
    okl = n_l > 50
    ax.plot(lon[okl], hv_l[okl], color=COL[2], ls="--", label="HV, land only")
    okw = n_w > 50
    ax.plot(lon[okw], hv_w[okw], color="k", ls=":", label="HV, dark (water-like) px only")
    ax.set_title("Mean brightness by longitude, full Armenia frame\n(mean of all 10 passes)")
    ax.set_xlabel("longitude")
    ax.set_ylabel("mean brightness")
    ax.legend(fontsize=6.5)
    fig.tight_layout()
    save(fig, "09_armenia_checks.png")
    # numbers for (e): mean HV/HH in 0.1 degree longitude bins
    bins = []
    for lo in np.arange(43.6, 46.0, 0.2):
        m = ok & (lon >= lo) & (lon < lo + 0.2)
        if int(m.sum()) == 0:
            continue
        mm = big.valid[:, :, m]
        bins.append({"lon_from": round(float(lo), 2), "HH": f(big.x[:, 0][:, :, m][mm].mean()), "HV": f(big.x[:, 1][:, :, m][mm].mean()),
                     "HV_land_only": f(hv_l[m][n_l[m] > 0].mean()) if int((n_l[m] > 0).sum()) else None,
                     "HV_dark_only": f(hv_w[m][n_w[m] > 0].mean()) if int((n_w[m] > 0).sum()) else None})
    # per-date HV profile summary (west third vs east third)
    L = int(ok.sum())
    idx = torch.nonzero(ok).squeeze(1)
    westc, eastc = idx[: L // 3], idx[-(L // 3):]
    pd = []
    for t in range(len(big.dates)):
        v = big.valid[t]
        def mean_cols(ch, cols):
            sel = v[:, cols]
            return float(big.x[t, ch][:, cols][sel].mean())
        pd.append({"date": big.dates[t], "HV_west_third": f(mean_cols(1, westc)), "HV_east_third": f(mean_cols(1, eastc)),
                   "HH_west_third": f(mean_cols(0, westc)), "HH_east_third": f(mean_cols(0, eastc))})
    # temporal std per column (does the gradient move with time?)
    out["longitude_profile_0.2deg_bins"] = bins
    out["west_vs_east_third_per_date"] = pd
    out["big_extent"] = {"shape": list(big.x.shape), "bounds": list(big.bounds), "lon_range_with_data": [f(lon[ok][0], 3), f(lon[ok][-1], 3)],
                         "valid_share": f(float(cvb.float().mean()), 3)}
    STATS["armenia"] = out
    return big


# ----------------------------------------------------------------- 10 QC
def shift_estimate(a: torch.Tensor, b: torch.Tensor, mask: torch.Tensor):
    """Sub-pixel shift (dy, dx) such that b(y, x) ~ a(y - dy, x - dx), by FFT cross-correlation on high-passed images."""
    def prep(x):
        x = x - box_mean(x, 15)
        x = x * mask
        return x * torch.hann_window(x.shape[0], periodic=False)[:, None] * torch.hann_window(x.shape[1], periodic=False)[None]
    A, B = torch.fft.rfft2(prep(a)), torch.fft.rfft2(prep(b))
    cc = torch.fft.irfft2(B * A.conj(), s=a.shape)
    idx = int(torch.argmax(cc))
    h, w = cc.shape
    py, px = idx // w, idx % w
    def par(m1, m0, p1):  # parabolic peak interpolation
        den = m1 - 2 * m0 + p1
        return 0.0 if abs(float(den)) < 1e-12 else float(0.5 * (m1 - p1) / den)
    dy = par(cc[(py - 1) % h, px], cc[py, px], cc[(py + 1) % h, px])
    dx = par(cc[py, (px - 1) % w], cc[py, px], cc[py, (px + 1) % w])
    sy = (py + dy) if py + dy < h / 2 else (py + dy - h)
    sx = (px + dx) if px + dx < w / 2 else (px + dx - w)
    return sy, sx, float(cc[py, px] / (cc.abs().mean() + 1e-12))


def align_stack(st):
    """Return x [T,2,H,W] with each date translated onto the first date (shift from FFT correlation) and the shifts."""
    cv = common_valid(st).float()
    b = st.x.mean(1)
    T, _, H, W = st.x.shape
    ys, xs = torch.meshgrid(torch.arange(H, dtype=torch.float32), torch.arange(W, dtype=torch.float32), indexing="ij")
    out, shifts = [st.x[0]], [(0.0, 0.0)]
    for t in range(1, T):
        sy, sx, _ = shift_estimate(b[0], b[t], cv)
        grid = torch.stack([(xs + sx) / (W - 1) * 2 - 1, (ys + sy) / (H - 1) * 2 - 1], -1)[None]
        out.append(F.grid_sample(st.x[t][None], grid, mode="bilinear", padding_mode="border", align_corners=True)[0])
        shifts.append((sy, sx))
    xa = torch.stack(out)
    resid = [shift_estimate(b[0], xa[t].mean(0), cv)[:2] for t in range(T)]
    return xa, shifts, resid


def fig_qc(sts):
    out = {}
    fig, axs = plt.subplots(1, 3, figsize=(13, 3.8))
    for k, a in enumerate(AREAS):
        st = sts[a]
        T = len(st.dates)
        cv = common_valid(st)
        res = {"valid_share_per_date": fl(st.valid.float().mean((1, 2))), "common_valid_share": f(cv.float().mean())}
        sat = []
        for t in range(T):
            v = st.valid[t]
            row = {}
            for ch, nm in enumerate(["HH", "HV"]):
                x = st.x[t, ch][v]
                row[f"{nm}_share_lt_0.004"] = f((x < 0.004).float().mean(), 5)
                row[f"{nm}_share_gt_0.996"] = f((x > 0.996).float().mean(), 5)
            sat.append(row)
        res["saturation_per_date"] = sat
        # edge ring: brightness within 1-3, 4-8 px of the common-valid boundary vs interior
        er = cv.clone()
        rings = {}
        prev = cv
        dist = torch.zeros(cv.shape)
        cur = cv.clone()
        for r in range(1, 31):
            cur = F.max_pool2d((~cur)[None, None].float(), 3, 1, 1)[0, 0] == 0  # erosion by 1 px
            dist += cur.float()
        # dist = number of erosion steps survived = distance from boundary (px) within valid
        for nm, lo, hi in [("edge 0-2 px", 0, 3), ("edge 3-8 px", 3, 9), ("interior (>30 px)", 30, 31)]:
            m = cv & (dist >= lo) & (dist < hi) if hi < 31 else cv & (dist >= 30)
            if int(m.sum()) == 0:
                continue
            rings[nm] = {"px": int(m.sum()), "HH_mean": f(st.x[:, 0].mean(0)[m].mean()), "HV_mean": f(st.x[:, 1].mean(0)[m].mean()),
                         "HH_temporal_std": f(st.x[:, 0].std(0)[m].mean()), "HV_temporal_std": f(st.x[:, 1].std(0)[m].mean())}
        res["edge_rings"] = rings
        # misregistration vs first date on high-passed mean brightness
        mask = cv.float()
        b = st.x.mean(1)
        shifts = []
        for t in range(1, T):
            sy, sx, sharp = shift_estimate(b[0], b[t], mask)
            shifts.append({"date": st.dates[t], "dy_px": f(sy, 2), "dx_px": f(sx, 2), "peak_to_mean": f(sharp, 1)})
        res["shift_vs_first_date"] = shifts
        res["km2_per_px"] = f(st.km2_per_px, 5)
        out[a] = res
        d = [D(s) for s in st.dates]
        axs[0].plot(d, st.valid.float().mean((1, 2)), "o-", color=COL[k], label=TITLE[a])
        axs[1].plot([D(s["date"]) for s in shifts], [s["dy_px"] for s in shifts], "o-", color=COL[k], label=f"{TITLE[a]} north-south")
        axs[1].plot([D(s["date"]) for s in shifts], [s["dx_px"] for s in shifts], "s--", color=COL[k], label=f"{TITLE[a]} east-west")
        axs[2].plot(d, [r["HH_share_gt_0.996"] * 100 for r in sat], "o-", color=COL[k], label=f"{TITLE[a]} HH at max")
        axs[2].plot(d, [r["HV_share_lt_0.004"] * 100 for r in sat], "s--", color=COL[k], label=f"{TITLE[a]} HV at zero")
    axs[0].set_title("Share of the map with valid data, per pass")
    axs[0].set_ylabel("share")
    axs[1].set_title("Image shift vs first pass (pixels, ~180 m)")
    axs[1].set_ylabel("shift, pixels")
    axs[2].set_title("Pixels stuck at 0 or 1 (saturated)")
    axs[2].set_ylabel("percent of valid pixels")
    for ax in axs:
        ax.legend(fontsize=6)
        time_axis(ax, sts["sevan"].dates)
    fig.tight_layout()
    save(fig, "10_quality_checks.png")
    STATS["quality"] = out


# ----------------------------------------------------------------- main
def main():
    t0 = time.time()
    FIG.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(max(1, torch.get_num_threads()))
    sts = {a: load_preview_stack(a) for a in AREAS}
    for a in AREAS:
        print(a, tuple(sts[a].x.shape), f"{time.time() - t0:.0f}s")
    STATS["note"] = ("Stability and temporal-map analyses use dates re-aligned to the first date; everything else uses the loader output. Preview values are 8-bit display brightness (0-1), not calibrated backscatter. Relative patterns and timing are meaningful; absolute values are not.")
    STATS["areas"] = {a: {"dates": sts[a].dates, "shape_TCHW": list(sts[a].x.shape), "km2_per_px": f(sts[a].km2_per_px, 5), "bounds": list(sts[a].bounds)} for a in AREAS}

    print("1 distributions"); fig_distributions(sts)
    regs = {a: align_stack(sts[a]) for a in AREAS}
    STATS["registration"] = {a: {"estimated_shift_dy_dx_px_vs_first_date": [[f(y, 2), f(x_, 2)] for y, x_ in regs[a][1]],
                                 "residual_after_alignment_dy_dx_px": [[f(y, 2), f(x_, 2)] for y, x_ in regs[a][2]],
                                 "dates": sts[a].dates} for a in AREAS}
    ali = {a: dataclasses.replace(sts[a], x=regs[a][0]) for a in AREAS}  # dates re-aligned to the first date
    print("2 stability"); fig_stability(ali)
    print("3 maps")
    for a in AREAS:
        fig_maps(ali[a])
    print("4 joint"); fig_joint(sts["tonle-sap"])
    print("5 water"); w = fig_water(sts)
    print("6 sevan lake"); fig_sevan_wind(sts["sevan"], w["sevan"]["otsu_threshold_pooled"])
    print("7 flooded veg"); fig_flood(sts["tonle-sap"], w["tonle-sap"]["otsu_threshold_pooled"])
    print("8 spots"); fig_spots(sts)
    print("9 armenia"); armenia_checks(sts, w["sevan"]["otsu_threshold_pooled"], regs)
    print("10 qc"); fig_qc(sts)
    STATS["runtime_seconds"] = round(time.time() - t0, 1)
    (OUT / "stats.json").write_text(json.dumps(STATS, indent=1))
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
