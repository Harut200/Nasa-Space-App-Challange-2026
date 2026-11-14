"""Mathematics-based (no learning) water / flooded-vegetation / change detection.

    .venv/bin/python -m scripts.detect.detect_math tonle-sap [--cpu]

Saves data/processed/<area>/math/products.pt and docs/eda/math/<area>_*.png.
Method note: docs/eda/math-detection.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import time

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402
from matplotlib.colors import BoundaryNorm, ListedColormap  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from scripts.detect import ops  # noqa: E402
from scripts.detect.stack import GAPS, ROOT, device, load_preview_stack  # noqa: E402

CLASS_COLORS = ["#d9d9d9", "#08306b", "#4292c6", "#41ab5d", "#e6550d"]


def _d(s):
    return dt.date.fromisoformat(s)


def _km2(mask, km2):
    return [round(float(m.sum()) * km2, 1) for m in mask]


def _extent(bounds):
    w, s, e, n = bounds
    return (w, e, s, n)


def _save(fig, path):
    fig.savefig(path, dpi=fig.dpi, bbox_inches="tight")
    plt.close(fig)
    kb = path.stat().st_size / 1024
    print(f"  wrote {path.relative_to(ROOT)} ({kb:.0f} KB)")


def _rgb(mask_bg, water, fv, wind=None):
    """Gray HH background, blue water, green flooded veg, orange wind-suspect."""
    g = np.clip(mask_bg, 0, 1)
    img = np.stack([g, g, g], -1) * 0.85
    if wind is not None:
        img[wind] = [1.0, 0.6, 0.1]
    img[water] = [0.1, 0.35, 0.9]
    img[fv] = [0.1, 0.8, 0.2]
    return img


def figures(area, st, out, prod, outdir):
    T = len(st.dates)
    ext = _extent(st.bounds)
    H, W = st.x.shape[-2:]
    scale = 6.0 / max(H, W)
    L = out["L"].cpu()
    valid = out["valid"].cpu()
    raw_hh = st.x[:, 0].numpy()
    # 1. small multiples
    ncol = 5
    nrow = int(np.ceil(T / ncol))
    fig, axs = plt.subplots(nrow, ncol, figsize=(ncol * W * scale * 0.55, nrow * H * scale * 0.55 + 0.6), dpi=75)
    for t, ax in enumerate(axs.flat):
        if t >= T:
            ax.axis("off")
            continue
        img = _rgb(raw_hh[t], prod["water"][t].numpy(), prod["flooded_veg"][t].numpy(),
                   out["wind_suspect"][t].cpu().numpy())
        img[~valid[t].numpy()] = 1.0
        ax.imshow(img, extent=ext, interpolation="nearest")
        ax.set_title(f"{st.dates[t]}\nW {prod['water_km2'][t]:.0f} / FV {prod['flooded_veg_km2'][t]:.0f} km²", fontsize=7)
        ax.set_xticks([]), ax.set_yticks([])
    fig.legend(handles=[Patch(color=(0.1, 0.35, 0.9), label="open water"),
                        Patch(color=(0.1, 0.8, 0.2), label="flooded vegetation"),
                        Patch(color=(1.0, 0.6, 0.1), label="usually water, not today (wind?)")],
               loc="lower center", ncol=3, fontsize=7, frameon=False)
    fig.suptitle(f"{area}: per-date water and flooded vegetation (math, uncalibrated previews)", fontsize=9)
    _save(fig, outdir / f"{area}_water_fv_grid.png")

    # 2. change-date map
    cd = prod["change_date"].numpy().astype(float)
    cd[cd < 0] = np.nan
    fig, ax = plt.subplots(figsize=(W * scale + 1.5, H * scale), dpi=100)
    ax.imshow(raw_hh[0], cmap="gray", extent=ext, vmin=0, vmax=1, alpha=0.6)
    cmap = plt.get_cmap("viridis", T - 1)
    norm = BoundaryNorm(np.arange(0.5, T + 0.5), cmap.N)
    im = ax.imshow(cd, cmap=cmap, norm=norm, extent=ext, interpolation="nearest")
    cb = fig.colorbar(im, ax=ax, ticks=range(1, T), fraction=0.04)
    cb.ax.set_yticklabels([st.dates[k] for k in range(1, T)], fontsize=7)
    cb.set_label("first date of the new level")
    ax.set_title(f"{area}: when did it change (CUSUM change point)", fontsize=9)
    _save(fig, outdir / f"{area}_change_date.png")

    # 3. class map
    fig, ax = plt.subplots(figsize=(W * scale + 2.5, H * scale), dpi=100)
    c = prod["classes"].numpy().astype(float)
    c[~valid.any(0).numpy()] = np.nan
    ax.imshow(c, cmap=ListedColormap(CLASS_COLORS), vmin=-0.5, vmax=4.5, extent=ext, interpolation="nearest")
    counts = [float((prod["classes"] == i).sum()) * st.km2_per_px for i in range(5)]
    ax.legend(handles=[Patch(color=CLASS_COLORS[i], label=f"{n} ({counts[i]:.0f} km²)")
                       for i, n in enumerate(ops.CLASS_NAMES)],
              loc="upper left", bbox_to_anchor=(1.01, 1), fontsize=7, frameon=False)
    ax.set_title(f"{area}: season class map", fontsize=9)
    _save(fig, outdir / f"{area}_classes.png")

    # 4. area curves
    days = [_d(s) for s in st.dates]
    fig, ax = plt.subplots(figsize=(7, 3.6), dpi=100)
    ax.plot(days, prod["water_km2"], "o-", color="#2171b5", label="open water (normalised)")
    ax.plot(days, prod["water_km2_nonorm"], "o--", color="#6baed6", label="open water (no normalisation)")
    ax.plot(days, prod["flooded_veg_km2"], "s-", color="#238b45", label="flooded vegetation")
    ax.plot(days, prod["wind_suspect_km2"], "^:", color="#e6550d", label="usually water, not today")
    for a, b in GAPS:
        ax.axvspan(_d(a), _d(b), color="0.85", label="no data (outage)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.set_ylabel("km²")
    ax.set_title(f"{area}: detected area per pass", fontsize=9)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7)
    _save(fig, outdir / f"{area}_areas.png")

    # 5. triptych
    d = (L[-1, 0] - L[0, 0]).numpy()
    both = (valid[0] & valid[-1]).numpy()
    d[~both] = np.nan
    fig, axs = plt.subplots(1, 3, figsize=(3 * W * scale * 0.7 + 1, H * scale * 0.7 + 0.5), dpi=90)
    for ax, img, title in [(axs[0], L[0, 0].numpy(), f"HH {st.dates[0]} (filtered)"),
                           (axs[1], L[-1, 0].numpy(), f"HH {st.dates[-1]} (filtered)")]:
        ax.imshow(img, cmap="gray", vmin=0, vmax=1 if st.source == "preview" else None, extent=ext)
        ax.set_title(title, fontsize=8)
    lim = float(np.nanpercentile(np.abs(d), 99)) or 1.0
    im = axs[2].imshow(d, cmap="RdBu", vmin=-lim, vmax=lim, extent=ext)
    axs[2].set_title("after - before (blue = brighter, red = darker)", fontsize=8)
    fig.colorbar(im, ax=axs[2], fraction=0.04)
    for ax in axs:
        ax.set_xticks([]), ax.set_yticks([])
    _save(fig, outdir / f"{area}_triptych.png")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("area")
    ap.add_argument("--cpu", action="store_true")
    args = ap.parse_args()
    t0 = time.time()
    st = load_preview_stack(args.area)
    dev = torch.device("cpu") if args.cpu else device()
    kn = ops.KNOBS[st.source]
    t1 = time.time()
    out = ops.run_pipeline(st.x, st.valid, st.source, kn, dev=dev)
    out_nn = ops.run_pipeline(st.x, st.valid, st.source, kn, dev=dev, normalise=False)
    if dev.type == "mps":
        torch.mps.synchronize()
    t2 = time.time()
    km2 = st.km2_per_px
    cpu = {k: (v.cpu() if torch.is_tensor(v) else v) for k, v in out.items()}
    prod = {
        "area": args.area, "dates": st.dates, "bounds": st.bounds, "km2_per_px": km2, "source": st.source,
        "water": cpu["water"], "flooded_veg": cpu["flooded_veg"], "prior_water": cpu["prior_water"],
        "wind_suspect": cpu["wind_suspect"], "valid": cpu["valid"],
        "change_first": cpu["change_first"].float(), "change_date": cpu["change_date"].to(torch.int16),
        "change_step": cpu["change_step"].float(), "classes": cpu["classes"].to(torch.uint8),
        "class_names": ops.CLASS_NAMES,
        "water_km2": torch.tensor(_km2(cpu["water"], km2)),
        "water_km2_nonorm": torch.tensor(_km2(out_nn["water"].cpu(), km2)),
        "flooded_veg_km2": torch.tensor(_km2(cpu["flooded_veg"], km2)),
        "flooded_veg_km2_nonorm": torch.tensor(_km2(out_nn["flooded_veg"].cpu(), km2)),
        "wind_suspect_km2": torch.tensor(_km2(cpu["wind_suspect"], km2)),
        "valid_km2": torch.tensor(_km2(cpu["valid"], km2)),
        "norm_gains": cpu["gains"], "norm_offsets": cpu["offsets"],
        "knobs": kn, "device": str(dev),
    }
    pdir = ROOT / "data" / "processed" / args.area / "math"
    pdir.mkdir(parents=True, exist_ok=True)
    torch.save(prod, pdir / "products.pt")
    fdir = ROOT / "docs" / "eda" / "math"
    fdir.mkdir(parents=True, exist_ok=True)

    print(f"{args.area}: {tuple(st.x.shape)} on {dev}, load {t1 - t0:.1f}s, pipeline x2 {t2 - t1:.1f}s")
    print(f"{'date':10} {'valid':>7} {'water':>7} {'w_nonorm':>8} {'fl.veg':>7} {'fv_nonorm':>9} {'wind?':>6}  gainHH gainHV")
    for t, d in enumerate(st.dates):
        g = prod["norm_gains"][t] if prod["norm_gains"] is not None else torch.ones(2)
        print(f"{d:10} {prod['valid_km2'][t]:7.0f} {prod['water_km2'][t]:7.0f} {prod['water_km2_nonorm'][t]:8.0f} "
              f"{prod['flooded_veg_km2'][t]:7.0f} {prod['flooded_veg_km2_nonorm'][t]:9.0f} "
              f"{prod['wind_suspect_km2'][t]:6.0f}  {g[0]:.3f}  {g[1]:.3f}")
    cls = prod["classes"]
    print("classes km2:", {n: round(float((cls == i).sum()) * km2) for i, n in enumerate(ops.CLASS_NAMES)})
    cd = prod["change_date"]
    hist = torch.bincount((cd[cd >= 0]).long(), minlength=len(st.dates))
    print("change-date px per date index:", hist.tolist())
    figures(args.area, st, out, prod, fdir)
    print(f"total {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
