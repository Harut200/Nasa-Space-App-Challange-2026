"""events.py -- non-earthquake ground events (volcano, landslide, ...) seen by NISAR L2 GUNW pairs.

    export SERVER=user@host SSH_KEY=~/.ssh/key            # only for launch/fetch (never stored in the repo)
    .venv/bin/python -m scripts.detect.events list
    .venv/bin/python -m scripts.detect.events resolve EVENT           # fill granule names from the ASF search (pair dates -> granule)
    .venv/bin/python -m scripts.detect.events launch  EVENT [VIEW]    # upload the job, start extraction on the server (~/work4)
    .venv/bin/python -m scripts.detect.events fetch   EVENT [VIEW]    # copy the reduced windows
    .venv/bin/python -m scripts.detect.events run     EVENT [VIEW]    # numbers + figures docs/eda/events/<EVENT>/ + results.json
    .venv/bin/python -m scripts.detect.events export  EVENT           # app layers + areas/<EVENT> (clear events only)
    .venv/bin/python -m pytest -q tests/test_events.py

Reuses the maths of scripts/detect/quake.py by import (phase to LOS, far-field ramp, decode, process). New here: the
coherence-loss map with a control pair (for lava, ash and slope failures, where the surface is destroyed and no phase
survives) and a config/export path that is not tied to USGS earthquake fields.
Config: docs/eda/events/events.json (a list of dicts; see `docs/eda/events.md`). Pure numpy/scipy here, no torch.
"""
import json
import math
import os
import subprocess
import sys

import numpy as np

from scripts.detect import quake as Q
from scripts.detect.quake import (disc_mask, largest_component, local_noise, lonlat_xy, phase_to_los_cm,
                                  rewrap)

ROOT = Q.ROOT
DOCS = os.path.join(ROOT, "docs", "eda", "events")
EVENTS_JSON = os.path.join(DOCS, "events.json")
WORK = "~/work4"
MON = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
       "November", "December"]


# ============================================================ maths (unit-tested)
def coherence_loss(coh_event, coh_control, hi=0.45, lo=0.25, valid=None, drop=None):
    """Pixels that were coherent before and are not now: control >= hi AND event < lo (both finite).
    With `drop` set the rule is control >= hi AND control - event >= drop AND event < lo (use a higher `lo`, e.g. 0.4,
    to follow thin, partly coherent features such as a debris-flow track).
    Returns (loss mask, comparable mask = control >= hi and both finite)."""
    ce, cc = np.asarray(coh_event, float), np.asarray(coh_control, float)
    comp = np.isfinite(ce) & np.isfinite(cc) & (cc >= hi)
    if valid is not None:
        comp &= valid
    m = comp & (ce < lo)
    if drop is not None:
        m &= (cc - ce) >= drop
    return m, comp


def min_cluster(mask, min_px):
    """Drop connected clusters (8-neighbour) smaller than min_px pixels (speckle-sized dropouts)."""
    from scipy.ndimage import label
    lab, n = label(mask, structure=np.ones((3, 3)))
    if n == 0:
        return mask
    sizes = np.bincount(lab.ravel())
    keep = sizes >= min_px
    keep[0] = False
    return keep[lab]


def loss_stats(coh_event, coh_control, X, Y, centre, radius_m, pix_km2, hi=0.45, lo=0.25, min_px=12, valid=None, drop=None):
    """Coherence-loss area (km2) inside `radius_m` of `centre`, the control's and event's mean coherence at those same pixels,
    and the same fraction measured outside the radius (the background rate of 'loss' for this pair)."""
    loss, comp = coherence_loss(coh_event, coh_control, hi, lo, valid, drop)
    loss = min_cluster(loss, min_px)
    inside = disc_mask(X, Y, centre[0], centre[1], radius_m)
    out = {"loss_area_km2": float(loss[inside].sum() * pix_km2),
           "comparable_area_km2": float(comp[inside].sum() * pix_km2),
           "loss_fraction_inside": float(loss[inside].sum() / max(comp[inside].sum(), 1)),
           "loss_fraction_outside": float(loss[~inside].sum() / max(comp[~inside].sum(), 1)),
           "comparable_px_inside": int(comp[inside].sum()),
           "loss_area_km2_whole_window": float(loss.sum() * pix_km2)}
    sel = loss & inside
    if sel.any():
        out["mean_coh_control_at_loss_px"] = float(np.nanmean(np.asarray(coh_control)[sel]))
        out["mean_coh_event_at_loss_px"] = float(np.nanmean(np.asarray(coh_event)[sel]))
    return out, loss, comp


def top_clusters(loss, X, Y, epsg, pix_km2, n=5):
    """The n largest connected clusters of a loss mask: area, centroid (lon, lat) and longest bounding-box side (km)."""
    from scipy.ndimage import label
    from rasterio.warp import transform
    lab, k = label(loss, structure=np.ones((3, 3)))
    if k == 0:
        return []
    sizes = np.bincount(lab.ravel())[1:]
    out = []
    dx = abs(X[0, 1] - X[0, 0]) / 1e3
    for i in np.argsort(sizes)[::-1][:n]:
        yy, xx = np.nonzero(lab == i + 1)
        lo, la = transform(f"EPSG:{epsg}", "EPSG:4326", [float(X[yy, xx].mean())], [float(Y[yy, xx].mean())])
        out.append({"area_km2": float(sizes[i] * pix_km2), "lon": round(lo[0], 4), "lat": round(la[0], 4),
                    "max_side_km": float(max(yy.max() - yy.min(), xx.max() - xx.min()) * dx)})
    return out


# ============================================================ config
def load_events():
    with open(EVENTS_JSON) as f:
        return json.load(f)


def save_events(evs):
    with open(EVENTS_JSON, "w") as f:
        json.dump(evs, f, indent=1)


def get_event(eid):
    for e in load_events():
        if e["id"] == eid:
            return e
    raise SystemExit(f"unknown event {eid!r}; see `list`")


def proc_dir(ev):
    return os.path.join(ROOT, "data", "processed", f"{ev['id']}-insar")


def view_dir(ev, view):
    return os.path.join(proc_dir(ev), view)


def fig_dir(ev):
    return os.path.join(DOCS, ev["id"])


def app_dir(ev):
    return os.path.join(ROOT, "data", "processed", ev["id"], "products")


def views_of(ev, only=None):
    return {k: v for k, v in ev["views"].items() if only in (None, k)}


def find_granule(lon, lat, track, frame, d1, d2):
    """Granule name of the GUNW pair (reference d1, secondary d2 as YYYYMMDD) on track/frame over a point; prefers the
    PROVISIONAL (P05023) product, then the urgent-response one."""
    import asf_search as asf
    from datetime import datetime, timedelta
    day = lambda a: datetime.strptime(a, "%Y%m%d")
    res = asf.search(dataset="NISAR", processingLevel="GUNW", intersectsWith=f"POINT({lon} {lat})",
                     start=day(d1).strftime("%Y-%m-%d"), end=(day(d2) + timedelta(days=2)).strftime("%Y-%m-%d"), maxResults=200)
    cands = []
    for r in res:
        n = r.properties["sceneName"]
        p = n.split("_")
        if int(p[5]) == track and int(p[7]) == frame and p[11][:8] == d1 and p[13][:8] == d2:
            cands.append(((0 if "_P05023_" in n else 1, 0 if "_PR_" in n else 1), n))
    if not cands:
        raise SystemExit(f"no GUNW for track {track} frame {frame} {d1}-{d2}")
    return sorted(cands)[0][1]


def resolve(ev):
    for k, v in ev["views"].items():
        for w in ("co", "control"):
            d1, d2 = v[w]["pair"].split("_")
            v[w]["granule"] = find_granule(ev["lon"], ev["lat"], v["track"], v["frame"], d1, d2)
            print(ev["id"], k, w, v[w]["granule"][:60])
    evs = load_events()
    save_events([ev if e["id"] == ev["id"] else e for e in evs])


def job_for(ev, view):
    v = ev["views"][view]
    return {"out": f"{WORK}/out/{ev['id']}/{view}",
            "windows": [{"id": "win", "bbox": ev["window"], "fine": False}],
            "gunw_fine": False, "cache_mb": 12, "hmin": ev["heights"][0], "hmax": ev["heights"][1],
            "layers": ["unwrappedPhase", "coherenceMagnitude", "connectedComponents"],
            "products": [{"id": v[k]["pair"], "url": Q.granule_url(v[k]["granule"]), "kind": "GUNW"}
                         for k in ("co", "control")]}


# ============================================================ server (address and key from the environment)
def launch(ev, view):
    """Upload the job and start the extraction detached on the server through ~/work4/runjob.sh, which waits until fewer
    than two of our extractions run and 150 MB are available."""
    srv, opts = Q._ssh_base()
    name = f"job_{ev['id']}_{view}.json"
    log = f"{WORK}/logs/{ev['id']}_{view}.log"
    cmd = (f"mkdir -p {WORK}/logs; cat > {WORK}/{name}; "
           f"(setsid nohup {WORK}/runjob.sh /home/opc/work4/{name} >> {log} 2>&1 < /dev/null &); sleep 1; echo started")
    r = subprocess.run(["ssh"] + opts + [srv, cmd], input=json.dumps(job_for(ev, view)), text=True, capture_output=True)
    print(ev["id"], view, r.stdout.strip()[-40:], r.returncode)


def fetch(ev, view):
    srv, opts = Q._ssh_base()
    d = view_dir(ev, view)
    os.makedirs(d, exist_ok=True)
    cmd = ["scp", "-q"] + opts + [f"{srv}:{WORK}/out/{ev['id']}/{view}/win/small2/GUNW_*.npz", d + "/"]
    print(f"fetch {ev['id']} {view}:", subprocess.run(cmd).returncode)


# ============================================================ loading
def load_pair(ev, view, which):
    pair = ev["views"][view][which]["pair"]
    z = np.load(os.path.join(view_dir(ev, view), f"GUNW_{pair}.npz"), allow_pickle=False)
    shape = (len(z["y"]), len(z["x"]))
    P = {"x": z["x"], "y": z["y"], "epsg": int(z["epsg"]), "pair": pair}
    for k in ("unwrappedPhase", "coherenceMagnitude"):
        P[k] = Q.decode(z, k, shape)
    P["connectedComponents"] = Q.decode(z, "connectedComponents", shape)
    for k in z.files:
        if k.startswith("cube_") or k.startswith("meta_"):
            P[k] = z[k][()] if z[k].ndim == 0 else Q.decode(z, k)
    P["wavelength"] = Q.C_LIGHT / float(P.get("meta_centerFrequency", 1.239e9))
    P["X"], P["Y"] = np.meshgrid(P["x"], P["y"])
    return P


def same_grid(a, b):
    return a["x"].shape == b["x"].shape and a["y"].shape == b["y"].shape and np.allclose(a["x"], b["x"]) \
        and np.allclose(a["y"], b["y"])


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def _save(ev, fig, name, dpi=80):
    d = fig_dir(ev)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name)
    fig.savefig(p, dpi=dpi, bbox_inches="tight")
    kb = os.path.getsize(p) // 1024
    if kb > 380:
        fig.savefig(p, dpi=55, bbox_inches="tight")
        kb = os.path.getsize(p) // 1024
    print("wrote", p, kb, "KB")


# ============================================================ analysis
def analyse_view(ev, view):
    """Deformation numbers (like quake.analyse_view, any event centre) and the coherence-loss numbers with the control."""
    plt = _plt()
    from matplotlib.colors import TwoSlopeNorm
    v = ev["views"][view]
    Pc, Pt = load_pair(ev, view, "co"), load_pair(ev, view, "control")
    if not same_grid(Pc, Pt):
        raise SystemExit("event and control windows are on different grids")
    ex, ey = lonlat_xy(Pc["epsg"], ev["lon"], ev["lat"])
    radius, R = ev.get("radius_km", 10), ev.get("far_km", 3 * ev.get("radius_km", 10))
    gmin = ev.get("gmin", 0.35)
    keep = np.ones(Pc["X"].shape, bool)
    for w, s, e, n in ev.get("mask_boxes", []) + v.get("mask_boxes", []):
        x0, y0 = lonlat_xy(Pc["epsg"], w, s); x1, y1 = lonlat_xy(Pc["epsg"], e, n)
        keep &= ~((Pc["X"] >= min(x0, x1)) & (Pc["X"] <= max(x0, x1)) & (Pc["Y"] >= min(y0, y1)) & (Pc["Y"] <= max(y0, y1)))
    pix_km2 = abs((Pc["x"][1] - Pc["x"][0]) * (Pc["y"][1] - Pc["y"][0])) / 1e6
    out = {"event": ev["title"], "view": view, "track": v["track"], "frame": v["frame"], "direction": v["direction"],
           "event_pair": v["co"]["pair"], "control_pair": v["control"]["pair"], "wavelength_m": Pc["wavelength"],
           "radius_km": radius, "far_km": R, "pixel_km2": pix_km2}
    coh_c, coh_t = Pc["coherenceMagnitude"], Pt["coherenceMagnitude"]
    inside = disc_mask(Pc["X"], Pc["Y"], ex, ey, radius * 1e3)
    out["mean_coh_event_within_radius"] = float(np.nanmean(coh_c[inside]))
    out["mean_coh_control_within_radius"] = float(np.nanmean(coh_t[inside]))
    out["mean_coh_event_whole_window"] = float(np.nanmean(coh_c))
    out["mean_coh_control_whole_window"] = float(np.nanmean(coh_t))
    # ---- coherence loss (always computed)
    ls, loss, comp = loss_stats(coh_c, coh_t, Pc["X"], Pc["Y"], (ex, ey), radius * 1e3, pix_km2,
                                ev.get("loss_hi", 0.45), ev.get("loss_lo", 0.25), ev.get("min_px", 12), keep, ev.get("loss_drop"))
    out["coherence_loss"] = ls
    out["largest_loss_clusters"] = top_clusters(loss, Pc["X"], Pc["Y"], Pc["epsg"], pix_km2)
    # the same statistic measured in rings elsewhere gives the background; report the 95th percentile of 20 discs of equal size
    rng = np.random.default_rng(1)
    bg = []
    H, W = coh_c.shape
    rp = radius * 1e3 / abs(Pc["x"][1] - Pc["x"][0])
    for _ in range(300):
        cy, cx = rng.integers(0, H), rng.integers(0, W)
        cxm, cym = Pc["x"][cx], Pc["y"][cy]
        if np.hypot(cxm - ex, cym - ey) < 2.2 * radius * 1e3:
            continue
        d = disc_mask(Pc["X"], Pc["Y"], cxm, cym, radius * 1e3)
        if d.sum() < 0.8 * math.pi * rp * rp or comp[d].sum() < 0.3 * d.sum():
            continue
        bg.append(float(loss[d].sum() * pix_km2))
    out["background_loss_area_km2_discs_of_same_size"] = {"n": len(bg), "median": float(np.median(bg)) if bg else None,
                                                          "p95": float(np.percentile(bg, 95)) if bg else None,
                                                          "max": float(max(bg)) if bg else None}
    # ---- deformation
    if ev.get("deformation", True):
        r, ok, far, coef = Q.process(Pc, keep, [(ex, ey)], ev.get("component"), r_km=R, gmin=gmin)
        rt, okt, fart, _ = Q.process(Pt, keep, [(ex, ey)], ev.get("component"), r_km=R, gmin=gmin)
        near = ok & inside
        nt = okt & inside
        out["valid_pixels_within_radius"] = int(near.sum())
        out["valid_fraction_within_radius"] = float(near.sum() / inside.sum())
        if near.sum() >= 50:
            lo, hi = np.nanpercentile(r[near], [0.5, 99.5])
            out["p2p_cm_0.5_99.5pct"] = float(hi - lo); out["p0.5_cm"] = float(lo); out["p99.5_cm"] = float(hi)
            out["min_cm"] = float(np.nanmin(r[near])); out["max_cm"] = float(np.nanmax(r[near]))
            out["far_std_cm"] = float(np.nanstd(r[far])) if far.sum() > 50 else None
            out["control_far_std_cm"] = float(np.nanstd(rt[fart])) if fart.sum() > 50 else None
            if nt.sum() >= 50:
                out["control_p2p_cm_0.5_99.5pct"] = float(np.diff(np.nanpercentile(rt[nt], [0.5, 99.5]))[0])
            out["local_noise_cm_event_far"] = local_noise(r, far)
            out["local_noise_cm_control_far"] = local_noise(rt, fart)
        out["unwrapping_components_event"] = int(len(np.unique(Pc["connectedComponents"][inside & (Pc["connectedComponents"] > 0)])))
    else:
        r = rt = None
    out["incidence_deg_mean"] = float(np.nanmean(Pc["cube_incidenceAngle"])) if "cube_incidenceAngle" in Pc else None
    # ---- figures
    ext = ((Pc["x"][0] - ex) / 1e3, (Pc["x"][-1] - ex) / 1e3, (Pc["y"][-1] - ey) / 1e3, (Pc["y"][0] - ey) / 1e3)
    d1, d2 = v["co"]["pair"].split("_"), v["control"]["pair"].split("_")
    dt = lambda a: f"{a[6:]}/{a[4:6]}"
    span_c, span_t = f"{dt(d1[0])}-{dt(d1[1])}", f"{dt(d2[0])}-{dt(d2[1])}"
    ttl = f"{ev['title']} ({v['label']} track {v['track']}, frame {v['frame']})"
    fig, axs = plt.subplots(1, 3, figsize=(13.5, 4.8))
    for ax, c, t in ((axs[0], coh_t, f"coherence, control {span_t}"), (axs[1], coh_c, f"coherence, event pair {span_c}")):
        im = ax.imshow(c, extent=ext, cmap="gray", vmin=0, vmax=1)
        ax.plot(0, 0, marker="*", ms=12, mfc="yellow", mec="k"); ax.set_title(t, fontsize=9)
        ax.set_xlabel("km east of the site", fontsize=8)
    ax = axs[2]
    ax.imshow(coh_t, extent=ext, cmap="gray", vmin=0, vmax=1, alpha=0.6)
    from scipy.ndimage import binary_dilation
    ov = np.ma.masked_where(~binary_dilation(loss, iterations=2), np.ones_like(coh_c))  # dilated for display only
    ax.imshow(ov, extent=ext, cmap="autumn", vmin=0, vmax=1)
    ax.plot(0, 0, marker="*", ms=12, mfc="yellow", mec="k")
    th = np.linspace(0, 2 * math.pi, 200)
    ax.plot(radius * np.cos(th), radius * np.sin(th), "c--", lw=0.8)
    ax.set_title(f"lost coherence (control >= {ev.get('loss_hi', 0.45)} and now < {ev.get('loss_lo', 0.25)}), red\n"
                 f"{ls['loss_area_km2']:.1f} km2 within {radius:g} km", fontsize=9)
    fig.suptitle(ttl, fontsize=9)
    _save(ev, fig, f"fig_a_coherence_{view}.png"); plt.close(fig)
    if r is not None and ev.get("deformation", True):
        lim = float(ev.get("los_lim_cm", 20)); fcm = float(ev.get("fringe_cm", 6))
        cmap = plt.get_cmap("RdBu").copy(); cmap.set_bad("none")
        fig, axs = plt.subplots(1, 3, figsize=(13.5, 4.8))
        im = axs[0].imshow(np.ma.masked_invalid(r), extent=ext, cmap=cmap, norm=TwoSlopeNorm(vmin=-lim, vcenter=0, vmax=lim))
        axs[0].set_title(f"LOS displacement {span_c} (cm; + = toward satellite, sign unconfirmed)", fontsize=8)
        plt.colorbar(im, ax=axs[0], shrink=0.8)
        im = axs[1].imshow(np.ma.masked_invalid(np.where(ok, rewrap(np.nan_to_num(r), fcm), np.nan)), extent=ext, cmap="twilight", vmin=0, vmax=1, interpolation="nearest")
        axs[1].set_title(f"fringes, one cycle = {fcm:g} cm", fontsize=8)
        im = axs[2].imshow(np.ma.masked_invalid(rt), extent=ext, cmap=cmap, norm=TwoSlopeNorm(vmin=-lim, vcenter=0, vmax=lim))
        axs[2].set_title(f"control {span_t}, same scale (far-field std {np.nanstd(rt[fart]) if fart.sum() > 50 else float('nan'):.1f} cm)", fontsize=8)
        for a in axs:
            a.plot(0, 0, marker="*", ms=11, mfc="yellow", mec="k"); a.set_xlabel("km east of the site", fontsize=8)
            a.plot(radius * np.cos(th), radius * np.sin(th), "k--", lw=0.6)
        fig.suptitle(ttl, fontsize=9)
        _save(ev, fig, f"fig_b_los_{view}.png"); plt.close(fig)
    arrays = {"los_cm": None if r is None else r.astype(np.float32), "ctl_cm": None if rt is None else rt.astype(np.float32),
              "coh": coh_c.astype(np.float32), "coh_ctl": coh_t.astype(np.float32), "loss": loss, "x": Pc["x"], "y": Pc["y"],
              "epsg": Pc["epsg"], "ex": ex, "ey": ey}
    np.savez_compressed(os.path.join(view_dir(ev, view), "products_cm.npz"),
                        **{k: v_ for k, v_ in arrays.items() if v_ is not None})
    return out, arrays


def run(ev, only=None):
    p = os.path.join(DOCS, "results.json")
    allr = json.load(open(p)) if os.path.exists(p) else {}
    res = allr.get(ev["id"], {"event_id": ev["id"], "views": {}})
    res["verdict"] = ev.get("verdict")
    for view in views_of(ev, only):
        out, _ = analyse_view(ev, view)
        res["views"][view] = out
        print(json.dumps(out, indent=1))
    allr[ev["id"]] = res
    json.dump(allr, open(p, "w"), indent=1)


# ============================================================ app export (same shape as scripts/detect/quake.py)
def _grid(ev):
    return Q._grid({"app_bbox": ev.get("app_bbox", ev["window"]), "window": ev["window"]})


def to_grid(ev, arr, x, y, epsg):
    return Q.to_app_grid({"app_bbox": ev.get("app_bbox", ev["window"]), "window": ev["window"]}, arr, x, y, epsg)


def export_products(ev):
    import rasterio
    from rasterio.transform import from_bounds
    import matplotlib
    cm = matplotlib.colormaps
    if ev.get("verdict") != "clear":
        raise SystemExit(f"{ev['id']}: verdict {ev.get('verdict')!r}; only 'clear' events are exported")
    views = ev.get("export_views", list(ev["views"]))
    prim = ev.get("primary_view", views[0])
    lim, fcm = float(ev.get("los_lim_cm", 20)), float(ev.get("fringe_cm", 6))
    (W, S, E, N), w, h = _grid(ev)
    D = {k: np.load(os.path.join(view_dir(ev, k), "products_cm.npz")) for k in views}
    z = D[prim]

    def rgba(v, cmap, vmin, vmax, finite):
        t = np.clip((v - vmin) / (vmax - vmin), 0, 1)
        c = (cm[cmap](np.nan_to_num(t)) * 255).astype(np.uint8)
        c[..., 3] = np.where(finite, 255, 0)
        return c
    layers = {}
    if ev.get("export_deformation", True):
        for k in views:
            if "los_cm" not in D[k].files:
                continue
            g = to_grid(ev, D[k]["los_cm"], D[k]["x"], D[k]["y"], int(D[k]["epsg"]))
            name = "los_cm" if len(views) == 1 else "los_%s_cm" % ("asc" if ev["views"][k]["direction"] == "A" else "desc")
            layers[name] = rgba(g, "RdBu", -lim, lim, np.isfinite(g))
            if k == prim:
                layers["fringes"] = rgba(rewrap(np.nan_to_num(g), fcm), "twilight", 0, 1, np.isfinite(g))
    coh = to_grid(ev, z["coh"], z["x"], z["y"], int(z["epsg"]))
    layers["coherence"] = rgba(coh, "gray", 0, 1, np.isfinite(coh))
    if ev.get("export_loss", True):
        lg = to_grid(ev, z["loss"].astype(np.float32), z["x"], z["y"], int(z["epsg"]))
        c = np.zeros((h, w, 4), np.uint8)
        c[..., 0], c[..., 1], c[..., 2] = 255, 90, 0
        c[..., 3] = np.where(np.nan_to_num(lg) > 0.5, 210, 0)
        layers["coh_loss"] = c
    app = app_dir(ev)
    os.makedirs(app, exist_ok=True)
    tr = from_bounds(W, S, E, N, w, h)
    for name, arr in layers.items():
        with rasterio.open(os.path.join(app, name + ".tif"), "w", driver="GTiff", height=h, width=w, count=4, dtype="uint8",
                           crs="EPSG:4326", transform=tr, compress="deflate", photometric="RGB", alpha="YES") as f:
            f.write(np.moveaxis(arr, -1, 0))
        print("wrote", name, os.path.getsize(os.path.join(app, name + ".tif")) // 1024, "KB")
    tw = [Q._hex(cm["twilight"](v)) for v in np.linspace(0, 1, 9)]
    L = []
    for name in layers:
        if name.startswith("los"):
            lab = "Ground motion along the line of sight (cm)" if name == "los_cm" else \
                f"Ground motion along the line of sight, {'ascending' if 'asc' in name else 'descending'} pass (cm)"
            L.append({"id": name, "label": lab, "dated": False, "legend": {
                "type": "diverging", "unit": "cm", "min": -lim, "max": lim,
                "colors": [Q._hex(cm["RdBu"](0.0)), "#f7f7f7", Q._hex(cm["RdBu"](1.0))],
                "labels": ["red: ground moved away from the satellite",
                           "blue: ground moved toward the satellite (sign convention not confirmed by the product guide)"]}})
    if "fringes" in layers:
        L.append({"id": "fringes", "label": f"Fringes (one colour cycle = {fcm:g} cm)", "dated": False,
                  "legend": {"type": "ramp", "unit": f"one colour cycle = {fcm:g} cm of line-of-sight change", "min": 0, "max": fcm, "colors": tw}})
    L.append({"id": "coherence", "label": "Radar coherence (how comparable the two passes are)", "dated": False,
              "legend": {"type": "ramp", "unit": "coherence 0-1", "min": 0, "max": 1, "colors": ["#000000", "#ffffff"]}})
    if "coh_loss" in layers:
        L.append({"id": "coh_loss", "label": "Where the radar picture changed completely between the passes", "dated": False,
                  "legend": {"type": "classes", "items": [
                      {"color": "#ff5a00", "label": "was comparable before (control pair), not comparable in the event pair"}]}})
    json.dump(L, open(os.path.join(app, "layers.json"), "w"), indent=2)
    last = max(ev["views"][k]["co"]["pair"].split("_")[1] for k in views)
    json.dump({"dates": [f"{last[:4]}-{last[4:6]}-{last[6:]}"], "unit": "cm", "series": [], "facts": []},
              open(os.path.join(app, "timeseries.json"), "w"), indent=2)
    json.dump({"event": ev["title"], "source_url": ev.get("source_url"),
               "views": {k: {"pair": ev["views"][k]["co"]["pair"], "track": ev["views"][k]["track"], "frame": ev["views"][k]["frame"],
                             "direction": ev["views"][k]["label"], "control_pair": ev["views"][k]["control"]["pair"]} for k in views},
               "product": "NISAR_L2 GUNW (PROVISIONAL / urgent response)", "script": "scripts/detect/events.py"},
              open(os.path.join(app, "source.json"), "w"), indent=2)
    print("exported", app)
    export_before(ev)


def before_pairing(ev, views=None):
    """Pairing block for the manifest: coherence_before vs coherence (primary view), or [] if the control is not a true before."""
    views = views or ev.get("export_views", list(ev["views"]))
    prim = ev.get("primary_view", views[0])
    if not Q.control_is_before(ev, prim):
        return []
    v = ev["views"][prim]
    c1, c2 = v["control"]["pair"].split("_"); e1, e2 = v["co"]["pair"].split("_")
    return [{"view": prim, "before": "coherence_before", "after": "coherence", "label": "Radar coherence",
             "before_dates": [Q._iso(c1), Q._iso(c2)], "after_dates": [Q._iso(e1), Q._iso(e2)]}]


def export_before(ev):
    """coherence_before: the CONTROL pair's coherence on the same grid with the same grey ramp and legend as `coherence`.
    Nothing is exported if the control pair is flagged control_after_event or is not before the event. Safe to re-run."""
    views = ev.get("export_views", list(ev["views"]))
    prim = ev.get("primary_view", views[0])
    app = app_dir(ev)
    if not before_pairing(ev, views):
        print(f"{ev['id']}: {prim}: no before layer (control pair is not before the event)")
        Q.append_layers(app, [])
        return
    z = np.load(os.path.join(view_dir(ev, prim), "products_cm.npz"))
    import matplotlib
    cm = matplotlib.colormaps
    g = to_grid(ev, z["coh_ctl"], z["x"], z["y"], int(z["epsg"]))
    c = (cm["gray"](np.clip(np.nan_to_num(g), 0, 1)) * 255).astype(np.uint8)
    c[..., 3] = np.where(np.isfinite(g), 255, 0)
    Q.write_rgba_tifs({"app_bbox": ev.get("app_bbox", ev["window"]), "window": ev["window"]}, app, {"coherence_before": c})
    d1, d2 = ev["views"][prim]["control"]["pair"].split("_")
    L = {x["id"]: x for x in json.load(open(os.path.join(app, "layers.json")))}
    import copy
    tag = " (noisy)" if ev.get("before_noisy", {}).get(prim) else ""
    Q.append_layers(app, [{"id": "coherence_before", "dated": False, "legend": copy.deepcopy(L["coherence"]["legend"]),
                           "label": f"Before the event: radar coherence ({Q._short(d1)}\u2013{Q._short(d2)}){tag}"}])
    print("before layer", ev["id"], prim, d1, d2)


def write_area(ev):
    """areas/<id>/area.json + story.json from the hand-written `story` and `spots` of the config."""
    views = ev.get("export_views", list(ev["views"]))
    pv = ev["views"][ev.get("primary_view", views[0])]
    area = {"id": ev["id"], "name": ev["title"], "bbox": ev.get("app_bbox", ev["window"]), "track": pv["track"],
            "frame": pv["frame"], "direction": "ascending and descending" if len(views) > 1 else pv["label"], "pixel_m": 80}
    d = os.path.join(ROOT, "areas", ev["id"])
    os.makedirs(d, exist_ok=True)
    json.dump(area, open(os.path.join(d, "area.json"), "w"), indent=2)
    spots = [{"id": "site", "label": ev.get("site_label", "Site"), "lngLat": [ev["lon"], ev["lat"]], "radius_m": 3000}] + ev.get("extra_spots", [])
    json.dump({"story": ev["story"], "spots": spots}, open(os.path.join(d, "story.json"), "w"), indent=2)
    print("wrote", d)


def overview():
    """One row per clear event: event-pair coherence with the lost-coherence
    patches in red, zoomed to 12 km around the largest cluster; the control's coherence on the left."""
    plt = _plt()
    from scipy.ndimage import binary_dilation
    evs = [e for e in load_events() if e.get("verdict") == "clear"]
    fig, axs = plt.subplots(len(evs), 2, figsize=(7.4, 3.6 * len(evs)), squeeze=False)
    for row, ev in zip(axs, evs):
        k = ev.get("primary_view", list(ev["views"])[0])
        z = np.load(os.path.join(view_dir(ev, k), "products_cm.npz"))
        res = json.load(open(os.path.join(DOCS, "results.json")))[ev["id"]]["views"][k]
        c = res["largest_loss_clusters"][0] if res.get("largest_loss_clusters") else {"lon": ev["lon"], "lat": ev["lat"]}
        cx, cy = lonlat_xy(int(z["epsg"]), c["lon"], c["lat"])
        h = 12e3
        sel_x = (z["x"] >= cx - h) & (z["x"] <= cx + h)
        sel_y = (z["y"] >= cy - h) & (z["y"] <= cy + h)
        ext = ((z["x"][sel_x][0] - cx) / 1e3, (z["x"][sel_x][-1] - cx) / 1e3, (z["y"][sel_y][-1] - cy) / 1e3, (z["y"][sel_y][0] - cy) / 1e3)
        sub = lambda a: a[np.ix_(sel_y, sel_x)]
        d1, d2 = ev["views"][k]["co"]["pair"], ev["views"][k]["control"]["pair"]
        row[0].imshow(sub(z["coh_ctl"]), extent=ext, cmap="gray", vmin=0, vmax=1)
        row[0].set_title(f"{ev['title'].split(',')[0]}\ncontrol pair {d2}", fontsize=7)
        row[1].imshow(sub(z["coh"]), extent=ext, cmap="gray", vmin=0, vmax=1)
        ov = np.ma.masked_where(~binary_dilation(sub(z["loss"]), iterations=1), np.ones(sub(z["coh"]).shape))
        row[1].imshow(ov, extent=ext, cmap="autumn", vmin=0, vmax=1, alpha=0.85)
        row[1].set_title(f"event pair {d1}: red = lost coherence ({ev.get('verdict')}), {res['largest_loss_clusters'][0]['area_km2']:.1f} km2 largest patch", fontsize=7)
        for a in row:
            a.tick_params(labelsize=6)
    fig.suptitle("NISAR coherence: where the radar picture changed completely (24 km boxes)", fontsize=9)
    _save({"id": ""}, fig, "overview.png", dpi=75)


def app_events():
    """docs/eda/events/app_events.json: one record per clear event with what the app exporter needs."""
    res = json.load(open(os.path.join(DOCS, "results.json")))
    out = []
    iso = lambda a: f"{a[:4]}-{a[4:6]}-{a[6:]}"
    for ev in load_events():
        if ev.get("verdict") != "clear":
            continue
        views = ev.get("export_views", list(ev["views"]))
        prim = ev.get("primary_view", views[0])
        r = res[ev["id"]]["views"][prim]
        if r.get("largest_loss_clusters") and not ev.get("export_deformation", True):
            area = r["largest_loss_clusters"][0]["area_km2"]
            summary = ev.get("summary_number") or f"\u2248 {area:.1f} km\u00b2 of surface changed completely"
        else:
            summary = ev["summary_number"]
        vs = []
        for k in ev["views"]:
            v = ev["views"][k]
            d1, d2 = v["co"]["pair"].split("_")
            vr = res[ev["id"]]["views"].get(k, {})
            vs.append({"id": k, "label": v["label"], "track": v["track"], "before": iso(d1), "after": iso(d2),
                       "p2p_cm": vr.get("p2p_cm_0.5_99.5pct") if ev.get("export_deformation", True) else None,
                       "control_p2p_cm": vr.get("control_p2p_cm_0.5_99.5pct") if ev.get("export_deformation", True) else None,
                       "control_before": iso(v["control"]["pair"].split("_")[0]), "control_after": iso(v["control"]["pair"].split("_")[1]),
                       "control_is_before": bool(Q.control_is_before(ev, k)),
                       "exported": k in views})
        out.append({"id": ev["id"], "type": ev["type"].split(" (")[0].split(" +")[0], "title": ev["title"], "time": ev["time"],
                    "lat": ev["lat"], "lon": ev["lon"], "place": ev["place"], "summary_number": summary,
                    "radius_km": ev.get("radius_km"), "views": vs, "source_url": ev["source_url"],
                    "before_after": before_pairing(ev, views),
                    **({"time_note": ev["time_note"]} if ev.get("time_note") else {})})
    json.dump(out, open(os.path.join(DOCS, "app_events.json"), "w"), indent=1, ensure_ascii=False)
    print("wrote app_events.json", [o["id"] for o in out])


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "list":
        for e in load_events():
            print(e["id"], e.get("type"), e.get("verdict"), list(e["views"]))
    elif cmd == "resolve":
        resolve(get_event(argv[2]))
    elif cmd in ("launch", "fetch"):
        ev = get_event(argv[2]); only = argv[3] if len(argv) > 3 else None
        for k in views_of(ev, only):
            (launch if cmd == "launch" else fetch)(ev, k)
    elif cmd == "run":
        run(get_event(argv[2]), argv[3] if len(argv) > 3 else None)
    elif cmd == "export":
        ev = get_event(argv[2]); export_products(ev); write_area(ev)
    elif cmd == "before":  # only the before layer; existing layers and areas/ are not touched
        export_before(get_event(argv[2]))
    elif cmd == "overview":
        overview()
    elif cmd == "app_events":
        app_events()
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
