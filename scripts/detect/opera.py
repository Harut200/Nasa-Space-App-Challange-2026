"""Compare our open-water maps with NASA's operational OPERA Dynamic Surface Water Extent (DSWx).

    .venv/bin/python -m scripts.detect.opera catalogue     # CMR search, no login; writes docs/eda/opera/catalogue.json
                                                           #   and data/raw/opera/plan.json (what to download)
    .venv/bin/python -m scripts.detect.opera compare       # reads downloaded WTR tiles, writes results.json + figures
    .venv/bin/python -m scripts.detect.opera compare tonle-sap sevan

Downloads need an Earthdata login that exists only on the team server (see docs/eda/opera.md). This file never
touches credentials; the server address and key are read from the environment (SERVER, SSH_KEY) by the fetch helper.

OPERA DSWx: NASA JPL / OPERA project, distributed by PO.DAAC. DSWx-S1 from Sentinel-1 C-band radar, DSWx-HLS from
Harmonized Landsat Sentinel-2 optical. 30 m, MGRS tiles. Class codes are read from the files (see `read_codes`), the
tables below are what the product specification and the files we downloaded say; the code checks the file tags.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data/raw/opera"
OUT = ROOT / "docs/eda/opera"
CMR = "https://cmr.earthdata.nasa.gov/search"
PRODUCTS = {"s1": "OPERA_L3_DSWX-S1_V1", "hls": "OPERA_L3_DSWX-HLS_V1", "ni": "OPERA_L3_DSWX-NI_V1"}
WINDOW_DAYS = 6
PRIORITY = ["tonle-sap", "sevan", "lake-chad", "inner-niger", "poyang", "urmia"]
OTHER_SEASON = ["bangladesh-haor", "okavango", "ararat", "sindh-indus", "mekong-delta", "rondonia", "nile-delta",
                "sudd", "pantanal"]

# ----------------------------------------------------------------------------- class codes
# Filled in from the downloaded files (WTR band tags / colour table); see docs/eda/opera.md section 2.
# DSWx-S1 WTR  : 0 not water, 1 open water, 2 and 3 (colour table: light blue / green) the two non-open water
#                classes (partial surface water, inundated vegetation), 250 and 251 masked (HAND / layover-shadow),
#                252-254 other masks, 255 no data.  The files carry only a colour table, no class names.
# DSWx-HLS WTR : 0 not water, 1 open water (high conf), 2 partial surface water (moderate conf), 3 reserved,
#                4 reserved, 252 snow/ice, 253 cloud/cloud shadow, 254 ocean masked, 255 no data
CODES = {
    "s1": {"open": (1,), "partial": (2, 3), "invalid": (250, 251, 252, 253, 254, 255), "land": (0,)},
    "hls": {"open": (1,), "partial": (2,), "invalid": (252, 253, 254, 255), "land": (0,)},
}


def classify(wtr: np.ndarray, product: str):
    """Return (open, partial, valid) boolean arrays from a WTR array using CODES[product]."""
    c = CODES[product]
    w = np.asarray(wtr)
    openw = np.isin(w, c["open"])
    part = np.isin(w, c["partial"])
    valid = ~np.isin(w, c["invalid"])
    # any code we have not listed is treated as invalid, never as land
    valid &= np.isin(w, c["open"] + c["partial"] + c["land"])
    return openw, part, valid


# ----------------------------------------------------------------------------- resampling
def block_fraction(open_mask, valid, factor):
    """Aggregate boolean 30 m masks into blocks of factor x factor.  Returns (water_fraction, valid_fraction):
    water_fraction = open / valid within the block (NaN where nothing valid), valid_fraction = valid / block size."""
    h, w = open_mask.shape
    H, W = h // factor, w // factor
    o = open_mask[:H * factor, :W * factor].reshape(H, factor, W, factor).sum((1, 3)).astype(np.float64)
    v = valid[:H * factor, :W * factor].reshape(H, factor, W, factor).sum((1, 3)).astype(np.float64)
    frac = np.where(v > 0, o / np.maximum(v, 1), np.nan)
    return frac, v / float(factor * factor)


def majority(frac, thr=0.5):
    """Coarse pixel is water if at least `thr` of its valid fine pixels are water."""
    return np.where(np.isnan(frac), False, frac >= thr)


# ----------------------------------------------------------------------------- metrics
def confusion_metrics(ours, ref, dom, km2):
    """Agreement of boolean `ours` with boolean reference `ref` inside the domain mask `dom`."""
    ours, ref, dom = (np.asarray(x, bool) for x in (ours, ref, dom))
    tp = float((ours & ref & dom).sum())
    fp = float((ours & ~ref & dom).sum())
    fn = float((~ours & ref & dom).sum())
    tn = float((~ours & ~ref & dom).sum())
    n = tp + fp + fn + tn
    rec = tp / (tp + fn) if tp + fn else float("nan")
    spec = tn / (tn + fp) if tn + fp else float("nan")
    return {
        "n_px": int(n), "tp_km2": tp * km2, "fp_km2": fp * km2, "fn_km2": fn * km2, "tn_km2": tn * km2,
        "agreement": (tp + tn) / n if n else float("nan"),
        "balanced_accuracy": float(np.nanmean([rec, spec])) if n else float("nan"),
        "precision": tp / (tp + fp) if tp + fp else float("nan"),
        "recall": rec,
        "iou": tp / (tp + fp + fn) if tp + fp + fn else float("nan"),
        "ours_km2": (tp + fp) * km2, "opera_km2": (tp + fn) * km2,
        "area_ratio": (tp + fp) / (tp + fn) if tp + fn else float("nan"),
    }


def block_bootstrap(ours, ref, dom, km2, block=32, n=200, seed=0):
    """95 % interval of IoU / agreement by resampling spatial blocks (block x block pixels) with replacement."""
    rng = np.random.default_rng(seed)
    H, W = ours.shape
    bh, bw = -(-H // block), -(-W // block)
    t = np.zeros((bh, bw, 4))
    for i in range(bh):
        for j in range(bw):
            s = (slice(i * block, (i + 1) * block), slice(j * block, (j + 1) * block))
            o, r, d = ours[s], ref[s], dom[s]
            t[i, j] = ((o & r & d).sum(), (o & ~r & d).sum(), (~o & r & d).sum(), (~o & ~r & d).sum())
    t = t.reshape(-1, 4)
    t = t[t.sum(1) > 0]
    iou, agr = [], []
    for _ in range(n):
        s = t[rng.integers(0, len(t), len(t))].sum(0)
        iou.append(s[0] / max(s[0] + s[1] + s[2], 1))
        agr.append((s[0] + s[3]) / max(s.sum(), 1))
    return {"iou_ci95": [float(np.percentile(iou, 2.5)), float(np.percentile(iou, 97.5))],
            "agreement_ci95": [float(np.percentile(agr, 2.5)), float(np.percentile(agr, 97.5))]}


# ----------------------------------------------------------------------------- CMR
def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "python-urllib"})
    return json.load(urllib.request.urlopen(req, timeout=90))


def parse_title(title):
    """OPERA_L3_DSWx-S1_T48PWA_20260610T225249Z_20260615T210107Z_S1A_30_v1.0 -> dict."""
    m = re.match(r"OPERA_L3_DSWx-(S1|HLS)_(T\w{5})_(\d{8}T\d{6})Z_(\d{8}T\d{6})Z_(\w+?)_30_v([\d.]+)", title)
    if not m:
        return None
    acq = dt.datetime.strptime(m.group(3), "%Y%m%dT%H%M%S")
    return {"product": m.group(1).lower(), "tile": m.group(2), "acq": acq.isoformat(), "sensor": m.group(5),
            "version": m.group(6)}


def search(short_name, bbox, start, end):
    out, page = [], 1
    while True:
        q = urllib.parse.urlencode({"short_name": short_name, "bounding_box": ",".join(map(str, bbox)),
                                    "temporal": f"{start}T00:00:00Z,{end}T23:59:59Z", "page_size": 200,
                                    "page_num": page})
        ents = _get_json(f"{CMR}/granules.json?{q}")["feed"]["entry"]
        out += ents
        if len(ents) < 200:
            return out
        page += 1


def collections():
    res = {}
    for kw in ("OPERA DSWx", "DSWX-NI", "NISAR DSWx", "NISAR surface water extent"):
        q = urllib.parse.urlencode({"keyword": kw, "page_size": 50})
        for e in _get_json(f"{CMR}/collections.json?{q}")["feed"]["entry"]:
            res[e["short_name"]] = {"version": e["version_id"], "title": e["title"], "id": e["id"]}
    return res


def wtr_url(entry):
    for l in entry["links"]:
        if l["href"].endswith("_WTR.tif") and l["href"].startswith("https") and "data#" in l["rel"]:
            return l["href"]
    return None


MAX_PER_TILE = 3


def pick(grans, pass_dates, k=MAX_PER_TILE):
    """For every pass date and tile, up to k granules within WINDOW_DAYS, nearest in time first (they are
    composited pixel by pixel: the nearest valid pixel wins)."""
    by_tile = {}
    for g in grans:
        by_tile.setdefault(g["tile"], []).append(g)
    res = {}
    for pd_ in pass_dates:
        t0 = dt.datetime.fromisoformat(pd_)
        sel = {}
        for tile, lst in by_tile.items():
            cand = []
            for g in lst:
                d = (dt.datetime.fromisoformat(g["acq"]) - t0).total_seconds() / 86400.0
                if abs(d) <= WINDOW_DAYS:
                    cand.append({**g, "dt_days": round(d, 2)})
            cand.sort(key=lambda g: abs(g["dt_days"]))
            if cand:
                sel[tile] = cand[:k]
        res[pd_] = sel
    return res


def area_info(aid):
    """Our bounds and pass dates (fusion2 products)."""
    import torch
    p = torch.load(ROOT / f"data/processed/{aid}/fusion2/products.pt", weights_only=False, map_location="cpu")
    return {"bounds": [float(x) for x in p["bounds"]], "dates": list(p["dates"])}


def cmd_catalogue(areas):
    OUT.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    cat = {"checked": dt.date.today().isoformat(), "collections": collections(), "window_days": WINDOW_DAYS,
           "areas": {}}
    cat["ni_found"] = [k for k in cat["collections"] if "DSWX-NI" in k.upper() or "DSWX_NI" in k.upper()]
    plan = {}
    for aid in areas:
        try:
            info = area_info(aid)
        except Exception as e:  # noqa: BLE001
            print(aid, "no fusion2 products:", e)
            continue
        b = info["bounds"]
        dates = info["dates"]
        entry = {"bbox": b, "pass_dates": dates}
        plan[aid] = {}
        for key in ("s1", "hls"):
            ents = search(
                PRODUCTS[key], b, (dt.date.fromisoformat(min(dates)) - dt.timedelta(days=7)).isoformat(),
                (dt.date.fromisoformat(max(dates)) + dt.timedelta(days=7)).isoformat())
            grans = []
            for e in ents:
                m = parse_title(e["title"])
                if not m:
                    continue
                m.update(url=wtr_url(e), cloud=float(e["cloud_cover"]) if e.get("cloud_cover") else None,
                         title=e["title"])
                grans.append(m)
            sel = pick(grans, dates)
            days = sorted({g["acq"][:10] for g in grans})
            entry[key] = {
                "n_granules": len(grans), "tiles": sorted({g["tile"] for g in grans}),
                "acquisition_days": days,
                "per_pass": {d: {"n_tiles": len(s), "tiles": sorted(s),
                                 "nearest_dt_days": sorted(v[0]["dt_days"] for v in s.values()),
                                 "n_granules_in_window": sum(len(v) for v in s.values())}
                             for d, s in sel.items()},
            }
            plan[aid][key] = {d: {t: [{"url": g["url"], "acq": g["acq"], "dt_days": g["dt_days"],
                                       "cloud": g["cloud"], "title": g["title"]} for g in v]
                                  for t, v in s.items()} for d, s in sel.items()} 
            entry[key]["n_granules_total_in_search"] = len(grans)
            print(aid, key, len(grans), "granules,", sum(1 for s in sel.values() if s), "of", len(dates),
                  "passes covered")
        cat["areas"][aid] = entry
    (OUT / "catalogue.json").write_text(json.dumps(cat, indent=1))
    (RAW / "plan.json").write_text(json.dumps(plan, indent=1))


def cmd_queue(max_cloud=70.0):
    """Write data/raw/opera/queue.txt: one WTR URL per line, in download priority order (nearest granule of every
    tile first, then the 2nd and 3rd nearest used to fill gaps)."""
    plan = json.loads((RAW / "plan.json").read_text())

    def urls(aid, key, which, rank):
        pds = sorted(plan[aid][key])
        pds = {"ends": [pds[0], pds[-1]], "mid": pds[1:-1], "all": pds}[which]
        out = []
        for d in pds:
            for t, lst in sorted(plan[aid][key][d].items()):
                if len(lst) > rank:
                    g = lst[rank]
                    if g["url"] and not (key == "hls" and g["cloud"] is not None and g["cloud"] > max_cloud):
                        out.append(g["url"])
        return out

    steps = []
    for rank in (0, 1, 2):
        steps += [("tonle-sap", "s1", "ends", rank), ("sevan", "s1", "ends", rank),
                  ("tonle-sap", "hls", "ends", rank), ("sevan", "hls", "ends", rank)]
        steps += [(a, "s1", "all", rank) for a in ("sevan", "tonle-sap", "lake-chad", "inner-niger", "poyang", "urmia")]
        if rank == 0:
            steps += [(a, "hls", "all", 0) for a in PRIORITY]
    steps += [(a, "s1", "all", 0) for a in OTHER_SEASON if a in plan]
    seen, q = set(), []
    for a, k, w, r in steps:
        if a in plan:
            for u in urls(a, k, w, r):
                if u not in seen:
                    seen.add(u)
                    q.append(u)
    (RAW / "queue.txt").write_text("\n".join(q) + "\n")
    print(len(q), "files queued")


# ----------------------------------------------------------------------------- our maps
def load_ours(aid, detector):
    """Return dict(crs, transform, shape, dates, water [T,H,W] bool, valid fn(t)->[H,W] bool, km2)."""
    import torch
    import rasterio
    from rasterio.transform import Affine, from_bounds
    if detector == "fusion2":
        p = torch.load(ROOT / f"data/processed/{aid}/fusion2/products.pt", weights_only=False, map_location="cpu")
        w = p["water"].numpy().astype(bool)
        T, H, W = w.shape
        b = [float(x) for x in p["bounds"]]
        v = p["valid"].numpy().astype(bool)
        return {"crs": "EPSG:4326", "transform": from_bounds(*b, W, H), "shape": (H, W), "dates": list(p["dates"]),
                "water": w, "valid": lambda t, v=v: v, "km2": float(p["km2_per_px"]), "grid": "lon/lat",
                "pixel_m": None}
    if detector == "cal":
        g = json.loads((ROOT / f"data/processed/{aid}/grid.json").read_text())
        d = torch.load(ROOT / f"data/processed/{aid}/det/water.pt", weights_only=False, map_location="cpu")
        w = d["water"].numpy().astype(bool)
        dates = list(d["dates"])

        def valid(t):
            with rasterio.open(ROOT / f"data/processed/{aid}/gamma0/hh/{dates[t]}.tif") as s:
                return np.isfinite(s.read(1))

        return {"crs": f"EPSG:{g['epsg']}", "transform": Affine(*g["transform"][:6]), "shape": (g["height"], g["width"]),
                "dates": dates, "water": w, "valid": valid, "km2": (g["pixel_m"] / 1000.0) ** 2, "grid": "utm",
                "pixel_m": g["pixel_m"]}
    raise ValueError(detector)


def tile_files(aid, key, date, plan):
    """Downloaded WTR files for this place/product/pass: dict tile -> [(path, dt_days)] nearest first."""
    out = {}
    for tile, lst in sorted(plan[aid][key].get(date, {}).items()):
        got = [(RAW / "files" / Path(g["url"]).name, g["dt_days"]) for g in lst if g["url"]]
        got = [x for x in got if x[0].exists()]
        if got:
            out[tile] = got
    return out


def composite_tile(paths_dt, product):
    """Pixel-wise composite of WTR rasters of one MGRS tile: nearest-in-time valid pixel wins.
    Returns (array, src_profile_info, sum_abs_dt, n_filled)."""
    import rasterio
    base, info, sdt, nfill = None, None, 0.0, 0
    for f, d in paths_dt:
        with rasterio.open(f) as src:
            a = src.read(1)
            if base is None:
                base, info = a.copy(), (src.crs, src.transform, src.bounds)
                _, _, v = classify(base, product)
                sdt += abs(d) * float(v.sum()); nfill += int(v.sum())
            else:
                if src.transform != info[1]:
                    continue
                _, _, vb = classify(base, product)
                _, _, vn = classify(a, product)
                take = ~vb & vn
                base[take] = a[take]
                sdt += abs(d) * float(take.sum()); nfill += int(take.sum())
    return base, info, sdt, nfill


def opera_on_grid(tiles, product, crs, transform, shape):
    """Mosaic WTR tiles onto our grid.  `tiles` = tile_files() output.  Returns (open_frac, openpart_frac,
    valid_frac, code_counts, mean_abs_dt) with fractions of the valid 30 m pixels in each cell that are open /
    open+partial water, and the share of the cell covered by valid OPERA pixels (area average of the valid flag)."""
    from rasterio.warp import Resampling, reproject, transform_bounds
    from rasterio.transform import array_bounds
    import rasterio
    H, W = shape
    sA = np.zeros((H, W), np.float32)
    sB = np.zeros((H, W), np.float32)
    sV = np.zeros((H, W), np.float32)
    dst_b = array_bounds(H, W, transform)
    seen, sdt, nfill = {}, 0.0, 0
    for tile, lst in tiles.items():
        with rasterio.open(lst[0][0]) as src:
            sb = transform_bounds(src.crs, crs, *src.bounds, densify_pts=21)
        if sb[0] > dst_b[2] or sb[2] < dst_b[0] or sb[1] > dst_b[3] or sb[3] < dst_b[1]:
            continue
        a, (scrs, stf, _), s_dt, n_f = composite_tile(lst, product)
        sdt += s_dt; nfill += n_f
        for u, c in zip(*np.unique(a, return_counts=True)):
            seen[int(u)] = seen.get(int(u), 0) + int(c)
        o, pt, v = classify(a, product)
        for arr, acc in ((o.astype(np.float32), sA), ((o | pt).astype(np.float32), sB), (v.astype(np.float32), sV)):
            out = np.zeros((H, W), np.float32)
            reproject(arr, out, src_transform=stf, src_crs=scrs, dst_transform=transform, dst_crs=crs,
                      resampling=Resampling.average)
            acc += out
    vf = np.clip(sV, 0, 1)
    of = np.where(sV > 1e-6, sA / np.maximum(sV, 1e-6), np.nan).astype(np.float32)
    bf = np.where(sV > 1e-6, sB / np.maximum(sV, 1e-6), np.nan).astype(np.float32)
    return of, bf, vf, seen, (sdt / nfill if nfill else float("nan"))


def soft_scores(ours, frac, dom):
    """Soft agreement of a boolean map with a water fraction: area-weighted IoU and mean absolute error."""
    o = ours[dom].astype(np.float64)
    f = np.nan_to_num(frac[dom]).astype(np.float64)
    return {"soft_iou": float(np.minimum(o, f).sum() / max(np.maximum(o, f).sum(), 1e-9)),
            "mae": float(np.abs(o - f).mean()) if o.size else float("nan"),
            "opera_soft_km2_factor": float(f.sum())}


def _f(x, nd=4):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), nd)


def metric_row(ours, frac, vfrac, our_valid, km2, thr=0.5, boot=True, min_valid=0.5):
    ref = majority(frac, thr)
    dom = our_valid & (vfrac >= min_valid) & ~np.isnan(frac)
    m = confusion_metrics(ours, ref, dom, km2)
    m.update(soft_scores(ours, frac, dom))
    m["soft_iou"] = m.pop("soft_iou")
    m["opera_soft_km2"] = m.pop("opera_soft_km2_factor") * km2
    if boot:
        m.update(block_bootstrap(ours, ref, dom, km2, block=max(8, int(round(5000 / (km2 ** 0.5 * 1000))))))
    return {k: (_f(v, 3) if isinstance(v, float) else ([_f(x) for x in v] if isinstance(v, list) else v))
            for k, v in m.items()}


def disagreement_diagnostics(ours, frac, vfrac, our_valid):
    """Where do the two maps differ?  Shares of the disagreeing area that sit within 1 or 2 cells of a water edge
    (resolution / mixed-pixel effect), and what OPERA's water fraction is under our water-only cells."""
    from scipy import ndimage as ndi
    ref = majority(frac)
    dom = our_valid & (vfrac >= 0.5) & ~np.isnan(frac)
    ours_only, ref_only = ours & ~ref & dom, ref & ~ours & dom
    edge = {}
    for k in (1, 2):
        st = np.ones((2 * k + 1, 2 * k + 1), bool)
        band = (ndi.binary_dilation(ref, st) & ~ndi.binary_erosion(ref, st)) | (
            ndi.binary_dilation(ours, st) & ~ndi.binary_erosion(ours, st))
        edge[k] = band
    nd = max(int((ours_only | ref_only).sum()), 1)
    f = np.nan_to_num(frac)
    no = max(int(ours_only.sum()), 1)
    nr = max(int(ref_only.sum()), 1)
    return {"ours_only_cells": int(ours_only.sum()), "opera_only_cells": int(ref_only.sum()),
            "disagree_within_1cell_of_edge_share": _f(float(((ours_only | ref_only) & edge[1]).sum()) / nd, 3),
            "disagree_within_2cell_of_edge_share": _f(float(((ours_only | ref_only) & edge[2]).sum()) / nd, 3),
            "ours_only_with_some_opera_water_share": _f(float((ours_only & (f > 0.05)).sum()) / no, 3),
            "ours_only_opera_dry_share": _f(float((ours_only & (f <= 0.05)).sum()) / no, 3),
            "opera_only_isolated_share": _f(float((ref_only & ~ndi.binary_dilation(ours, np.ones((5, 5), bool))).sum()) / nr, 3)}


def mean_of(rows, key):
    xs = [r[key] for r in rows if r.get(key) is not None]
    return float(np.mean(xs)) if xs else None


def summarise(rows):
    """rows: list of metric dicts (one per place-date)."""
    if not rows:
        return {"n": 0}
    out = {"n": len(rows)}
    for k in ("agreement", "balanced_accuracy", "precision", "recall", "iou", "soft_iou", "area_ratio"):
        out[k] = _f(mean_of(rows, k))
    tp = sum(r["tp_km2"] for r in rows); fp = sum(r["fp_km2"] for r in rows); fn = sum(r["fn_km2"] for r in rows)
    out["pooled_iou"] = _f(tp / max(tp + fp + fn, 1e-9))
    out["pooled_area_ratio"] = _f((tp + fp) / max(tp + fn, 1e-9))
    return out


def cmd_compare(areas, force=False):
    import torch
    plan = json.loads((RAW / "plan.json").read_text())
    res_path = OUT / "results.json"
    results = json.loads(res_path.read_text()) if res_path.exists() and not force else {}
    results.setdefault("places", {})
    results["codes"] = CODES
    results["rules"] = {"coarse_pixel_is_water_if_fraction_ge": 0.5, "opera_valid_share_min": 0.5,
                        "min_domain_share_of_our_valid": 0.3, "window_days": WINDOW_DAYS}
    seen_codes = results.setdefault("observed_codes", {})
    for aid in areas:
        for det in (["fusion2", "cal"] if aid == "tonle-sap" else ["fusion2"]):
            src_area = "tonle-sap-cal" if det == "cal" else aid
            if not (ROOT / f"data/processed/{src_area}").exists():
                continue
            try:
                ours = load_ours(src_area, det)
            except Exception as e:  # noqa: BLE001
                print("skip", src_area, det, e)
                continue
            hdom = None
            if aid == "tonle-sap" and det == "fusion2":
                hdom = torch.load(ROOT / "data/processed/tonle-sap/hydro/products.pt", weights_only=False,
                                  map_location="cpu")["domain"].numpy().astype(bool)
            cache = ROOT / f"data/processed/{src_area}/opera"
            cache.mkdir(parents=True, exist_ok=True)
            entry = results["places"].setdefault(aid, {}).setdefault(det, {})
            for key in ("s1", "hls"):
                per = entry.setdefault(key, {})
                for t, d in enumerate(ours["dates"]):
                    files = tile_files(aid, key, d, plan)
                    if not files:
                        continue
                    cf = cache / f"{key}_{d}.npz"
                    ngot = sum(len(v) for v in files.values())
                    cf_new = not (cf.exists() and per.get(d, {}).get("n_files") == ngot)
                    if not cf_new:
                        z = np.load(cf)
                        of = np.where(z["open"] == 255, np.nan, z["open"] / 100.0).astype(np.float32)
                        bf = np.where(z["openpart"] == 255, np.nan, z["openpart"] / 100.0).astype(np.float32)
                        vf = (z["valid"] / 100.0).astype(np.float32)
                    else:
                        of, bf, vf, sc, mdt = opera_on_grid(files, key, ours["crs"], ours["transform"], ours["shape"])
                        for c, n in sc.items():
                            seen_codes.setdefault(key, {})[str(c)] = seen_codes.get(key, {}).get(str(c), 0) + n
                        np.savez_compressed(cf, open=np.where(np.isnan(of), 255, np.round(of * 100)).astype(np.uint8),
                                            openpart=np.where(np.isnan(bf), 255, np.round(bf * 100)).astype(np.uint8),
                                            valid=np.round(vf * 100).astype(np.uint8))
                    o_map = ours["water"][t]
                    ov = ours["valid"](t) if callable(ours["valid"]) else ours["valid"]
                    covered = float(((vf >= 0.5) & ov).sum()) / max(float(ov.sum()), 1)
                    row = {"n_tiles": len(files), "n_files": ngot,
                           "mean_abs_dt_days": _f(mdt if cf_new else per.get(d, {}).get("mean_abs_dt_days"), 2),
                           "ours_valid_km2": _f(float(ov.sum()) * ours["km2"], 1),
                           "opera_coverage_of_ours": _f(covered, 3),
                           "ours_total_km2": _f(float((o_map & ov).sum()) * ours["km2"], 1),
                           "used": covered >= 0.3}
                    if covered >= 0.05:
                        row["open"] = metric_row(o_map, of, vf, ov, ours["km2"])
                        row["open_partial"] = metric_row(o_map, bf, vf, ov, ours["km2"], boot=False)
                        row["diagnostics"] = disagreement_diagnostics(o_map, of, vf, ov)
                        if hdom is not None:
                            hd = hdom & ov
                            hv = hd & (vf >= 0.5) & ~np.isnan(of)
                            ours_h = float((o_map & hdom).sum())
                            ours_c = float((o_map & hv).sum())
                            op_c = float((majority(of) & hv).sum())
                            opp_c = float((majority(bf) & hv).sum())
                            row["hydro_domain"] = {
                                "domain_km2": _f(float(hdom.sum()) * ours["km2"], 1),
                                "opera_coverage": _f(float(hv.sum()) / max(float(hd.sum()), 1), 3),
                                "ours_km2": _f(ours_h * ours["km2"], 1),
                                "ours_km2_in_opera_cells": _f(ours_c * ours["km2"], 1),
                                "opera_open_km2_in_opera_cells": _f(op_c * ours["km2"], 1),
                                "opera_openpart_km2_in_opera_cells": _f(opp_c * ours["km2"], 1),
                                # scaling OPERA to the whole frame by our own map's ratio in the cells OPERA covers
                                # (assumes the covered part is representative; unreliable when coverage is low)
                                "opera_open_km2_scaled": _f(ours_h * op_c / ours_c * ours["km2"], 1) if ours_c else None,
                                "opera_openpart_km2_scaled": _f(ours_h * opp_c / ours_c * ours["km2"], 1) if ours_c else None,
                            }
                    per[d] = row
                    print(aid, det, key, d, row.get("open", {}).get("iou"), "cov", row["opera_coverage_of_ours"])
            res_path.write_text(json.dumps(results, indent=1))
            del ours
    res_path.write_text(json.dumps(results, indent=1))
    return results


# ----------------------------------------------------------------------------- summary and figures
def build_summary(results):
    rows_all = {}
    summ = {}
    for aid, dets in results["places"].items():
        for det, prods in dets.items():
            for key, per in prods.items():
                used = {d: r for d, r in per.items() if r.get("used") and "open" in r}
                for var in ("open", "open_partial"):
                    rows = [r[var] for r in used.values()]
                    summ.setdefault(aid, {}).setdefault(det, {}).setdefault(key, {})[var] = summarise(rows)
                    rows_all.setdefault((det, key, var), []).extend(rows)
                s = summ[aid][det][key]
                s["dates_used"] = sorted(used)
                s["dates_skipped_low_coverage"] = sorted(d for d, r in per.items() if not r.get("used"))
                s["mean_abs_dt_days"] = _f(np.mean([r["mean_abs_dt_days"] for r in used.values()
                                                    if r.get("mean_abs_dt_days") is not None]), 2) if used else None
                s["mean_opera_coverage"] = _f(np.mean([r["opera_coverage_of_ours"] for r in used.values()]), 3) if used else None
                s["mean_bootstrap_iou_ci95"] = [
                    _f(np.mean([r["open"]["iou_ci95"][0] for r in used.values()]), 3),
                    _f(np.mean([r["open"]["iou_ci95"][1] for r in used.values()]), 3)] if used else None
    overall = {}
    rng = np.random.default_rng(0)
    for (det, key, var), rows in rows_all.items():
        o = summarise(rows)
        if len(rows) > 2:
            ious = np.array([r["iou"] for r in rows if r["iou"] is not None])
            agr = np.array([r["agreement"] for r in rows if r["agreement"] is not None])
            bi = [np.mean(rng.choice(ious, len(ious))) for _ in range(2000)]
            ba = [np.mean(rng.choice(agr, len(agr))) for _ in range(2000)]
            o["iou_ci95_over_place_dates"] = [_f(np.percentile(bi, 2.5), 3), _f(np.percentile(bi, 97.5), 3)]
            o["agreement_ci95_over_place_dates"] = [_f(np.percentile(ba, 2.5), 3), _f(np.percentile(ba, 97.5), 3)]
        overall.setdefault(det, {}).setdefault(key, {})[var] = o
    return summ, overall


COL = {"both": "#2b5fb5", "ours": "#f28e2b", "opera": "#c2185b", "land": "#f2f2f2", "nodata": "#8c8c8c"}


def agreement_image(aid, det, key, date):
    """RGB image: both water / ours only / OPERA only / neither / OPERA no data (or ours invalid)."""
    src_area = "tonle-sap-cal" if det == "cal" else aid
    ours = load_ours(src_area, det)
    t = ours["dates"].index(date)
    z = np.load(ROOT / f"data/processed/{src_area}/opera/{key}_{date}.npz")
    of = np.where(z["open"] == 255, np.nan, z["open"] / 100.0)
    vf = z["valid"] / 100.0
    ov = ours["valid"](t) if callable(ours["valid"]) else ours["valid"]
    o = ours["water"][t]
    ref = majority(of)
    dom = ov & (vf >= 0.5) & ~np.isnan(of)
    from matplotlib.colors import to_rgb
    img = np.zeros(o.shape + (3,))
    img[:] = to_rgb(COL["nodata"])
    img[dom] = to_rgb(COL["land"])
    img[dom & o & ref] = to_rgb(COL["both"])
    img[dom & o & ~ref] = to_rgb(COL["ours"])
    img[dom & ~o & ref] = to_rgb(COL["opera"])
    img[~ov] = (1, 1, 1)
    return img, ours["grid"]


def _legend(fig):
    from matplotlib.patches import Patch
    fig.legend(handles=[Patch(color=COL["both"], label="water in both"), Patch(color=COL["ours"], label="ours only"),
                       Patch(color=COL["opera"], label="OPERA only"), Patch(color=COL["land"], label="land in both"),
                       Patch(color=COL["nodata"], label="no OPERA data")],
               loc="lower center", ncol=5, fontsize=8, frameon=False)


def cmd_report():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    results = json.loads((OUT / "results.json").read_text())
    results["summary"], results["overall"] = build_summary(results)
    (OUT / "results.json").write_text(json.dumps(results, indent=1))
    pl = results["places"]

    def panel(ax, aid, det, key, date, title):
        img, grid = agreement_image(aid, det, key, date)
        ax.imshow(img, interpolation="nearest")
        ax.set_xticks([]); ax.set_yticks([])
        ax.set_title(title, fontsize=8)

    # figure 1: Tonle Sap, first and last pass, our app detector vs DSWx-S1
    dates = sorted(pl["tonle-sap"]["fusion2"]["s1"])
    first, last = dates[0], dates[-1]
    fig, axs = plt.subplots(1, 2, figsize=(10, 5.0))
    for ax, d in zip(axs, (first, last)):
        r = pl["tonle-sap"]["fusion2"]["s1"][d]["open"]
        panel(ax, "tonle-sap", "fusion2", "s1", d, f"Tonle Sap {d}: IoU {r['iou']:.2f}, "
              f"ours {r['ours_km2']:.0f} vs OPERA {r['opera_km2']:.0f} km2")
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    _legend(fig)
    fig.savefig(OUT / "fig1_tonle_sap_agreement.png", dpi=95)
    plt.close(fig)

    # figure 2: four other places, best case to worst case (date with the best OPERA coverage)
    fig, axs = plt.subplots(2, 2, figsize=(10, 9.6))
    for ax, aid in zip(axs.ravel(), ("sevan", "urmia", "lake-chad", "inner-niger")):
        per = {d: r for d, r in pl.get(aid, {}).get("fusion2", {}).get("s1", {}).items()
               if r.get("used") and "open" in r}
        if not per:
            ax.axis("off")
            continue
        d = sorted(per, key=lambda x: (-per[x]["opera_coverage_of_ours"], x))[0]
        r = per[d]["open"]
        panel(ax, aid, "fusion2", "s1", d, f"{aid} {d}: IoU {r['iou']:.2f}, ours {r['ours_km2']:.0f} vs "
              f"OPERA {r['opera_km2']:.0f} km2", )
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    _legend(fig)
    fig.savefig(OUT / "fig2_other_places_agreement.png", dpi=80)
    plt.close(fig)

    # figure 3: Tonle Sap area per date, ours vs OPERA vs the independent level-based estimate
    hydro = json.loads((ROOT / "docs/eda/hydro/results.json").read_text())
    pdates = hydro["pass_dates"]
    x = [dt.date.fromisoformat(d) for d in pdates]
    fig, ax = plt.subplots(figsize=(9, 4.8))
    ax.fill_between(x, hydro["expected_km2"]["lo"], hydro["expected_km2"]["hi"], color="#bbbbbb", alpha=0.5,
                    label="level + Landsat estimate, plausible range")
    ax.plot(x, hydro["expected_km2"]["central"], color="#555555", lw=2, label="level + Landsat estimate (hydro.md)")
    f2 = pl["tonle-sap"]["fusion2"]["s1"]
    ax.plot(x, [f2[d]["hydro_domain"]["ours_km2"] for d in pdates], "o-", color="#2b5fb5", label="ours, preview detector (app)")
    cal = pl["tonle-sap"].get("cal", {}).get("s1", {})
    if cal:
        ax.plot(x, [cal[d]["ours_total_km2"] for d in pdates if d in cal], "s--", color="#17a2b8",
                label="ours, calibrated 80 m")
    ax.plot(x, [f2[d]["hydro_domain"]["opera_open_km2_scaled"] for d in pdates], "^-", color="#c2185b",
            label="OPERA DSWx-S1 open water, scaled to the frame")
    low = [i for i, d in enumerate(pdates) if f2[d]["hydro_domain"]["opera_coverage"] < 0.6]
    ax.plot([x[i] for i in low], [f2[pdates[i]]["hydro_domain"]["opera_open_km2_scaled"] for i in low], "^",
            color="white", mec="#c2185b", ms=7, label="  hollow: OPERA covers < 60 % of the frame")
    ax.plot(x, [f2[d]["hydro_domain"]["opera_openpart_km2_scaled"] for d in pdates], "^:", color="#c2185b",
            label="OPERA DSWx-S1 open + partial/inundated veg., scaled")
    ax.set_ylabel("open-water area in the common frame, km2")
    ax.set_title("Tonle Sap: area per pass date (frame 36,036 km2)", fontsize=10)
    ax.legend(fontsize=7, loc="upper left")
    ax.grid(alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(OUT / "fig3_tonle_sap_area_per_date.png", dpi=100)
    plt.close(fig)

    # figure 4: IoU per place and product
    fig, ax = plt.subplots(figsize=(9, 4))
    labels, vals, cols = [], [], []
    for aid, dets in results["summary"].items():
        for det, prods in dets.items():
            for key, s in prods.items():
                if s["open"]["n"]:
                    labels.append(f"{aid}\n{det}\n{key.upper()}")
                    vals.append(s["open"]["iou"])
                    cols.append("#2b5fb5" if key == "s1" else "#6a3d9a")
    ax.bar(range(len(vals)), vals, color=cols)
    ax.set_xticks(range(len(vals)))
    ax.set_xticklabels(labels, fontsize=6)
    ax.set_ylabel("mean IoU of open water vs OPERA")
    ax.set_ylim(0, 1)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "fig4_iou_by_place.png", dpi=100)
    plt.close(fig)
    print(json.dumps(results["overall"], indent=1))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "catalogue"
    rest = sys.argv[2:]
    if cmd == "catalogue":
        cmd_catalogue(rest or PRIORITY + OTHER_SEASON)
    elif cmd == "compare":
        cmd_compare(rest or PRIORITY)
    elif cmd == "report":
        cmd_report()
    elif cmd == "queue":
        cmd_queue()
    else:
        raise SystemExit("unknown command " + cmd)
