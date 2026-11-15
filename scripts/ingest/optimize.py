"""Post-export optimiser for the app's overlay data.  Works in place on src/app/public/data/.

    python scripts/ingest/optimize.py [place ...]      # default: every place in index.json

What it does (rules and evidence: docs/eda/data-size.md):
  1. class maps (legend.type == "classes"): snap opaque pixels to the exact legend colours, save lossless WebP (palette).
  2. every other overlay (ramp / diverging / text): lossy WebP, QUALITY, method 6, alpha kept lossless.
  3. thumbs/<place>.webp (480 px) and thumbs/<place>@2x.webp (960 px), small galleries never load full overlays.
  4. summary.json: everything a landing page / globe / gallery needs for all places in one small file.
Idempotent: an optimised file carries an XMP marker and is skipped; thumbnails and summary are rebuilt deterministically.
Safe to re-run after any export (export.py rewrites files without the marker, those get optimised again).
"""
import io
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "src/app/public/data"
MARK = b'<x:xmpmeta xmlns:x="adobe:ns:meta/" opt="1"/>'
QUALITY = 74            # lossy overlays (hh, hv, change, rgb, coherence, fringes, los_*, ...)
ALPHA_Q = 70            # graded alpha (change layers): max error 9/255, halves those files
GREY_BG_LAYERS = ("sig", "choreo")   # the app turns grey pixels of these maps transparent; keep them grey
GREY_CHROMA = 24       # max-min of RGB below which a pixel is grey background, not a class
SNAP_TOL = 40           # RGB distance within which an opaque pixel counts as "a legend colour" in a class map
MAX_SIDE = 1600         # never upscale; only larger files are downscaled
THUMB_W, THUMB2_W = 480, 960
THUMB_MAX, THUMB2_MAX = 25_000, 70_000
BG = (11, 18, 28)       # dark thumbnail background


# ---------------------------------------------------------------- helpers
def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def is_marked(path):
    try:
        with Image.open(path) as im:
            return MARK in (im.info.get("xmp") or b"")
    except Exception:
        return False


def encode(im, **kw):
    b = io.BytesIO()
    im.save(b, "WEBP", xmp=MARK, **kw)
    return b.getvalue()


def legend_colours(layer):
    lg = layer.get("legend") or {}
    if lg.get("type") != "classes":
        return None
    cols = [hex_rgb(i["color"]) for i in lg.get("items", []) if str(i.get("color", "")).startswith("#")]
    return cols or None


def snap_to_legend(rgba, cols, grey_bg=False):
    """Nearest legend colour for every opaque pixel (lossy exports leave blends at class borders).  Where `grey_bg`, grey, low-chroma
    pixels (the 'no significant change' background of sig/choreo maps, which the app makes transparent by saturation)
    are not legend colours: they are posterised to 16 grey levels instead, unless the legend itself has greys.
    Returns (new rgba, fraction of the coloured opaque pixels that were already within SNAP_TOL of a legend colour)."""
    out = rgba.copy()
    op = out[..., 3] > 0
    px = out[op][:, :3].astype(np.int32)
    pal = np.array(cols, np.int32)
    legend_grey = bool(((pal.max(1) - pal.min(1)) < GREY_CHROMA).any())
    grey = (px.max(1) - px.min(1)) < GREY_CHROMA if (grey_bg and not legend_grey) else np.zeros(len(px), bool)
    d = ((px[:, None, :] - pal[None, :, :]) ** 2).sum(-1)
    k = d.argmin(1)
    near = d[np.arange(len(px)), k] <= SNAP_TOL ** 2
    snapped = pal[k]
    g = (px.mean(1).astype(np.int32) // 16 * 16 + 8)[:, None].repeat(3, 1)
    px = np.where(grey[:, None], g, snapped)
    out[op, :3] = px
    out[~op, :3] = 0                      # invisible pixels: constant colour compresses best
    colour = ~grey
    return out, (float(near[colour].mean()) if colour.any() else 1.0)


def downscale(im, cls):
    w, h = im.size
    if max(w, h) <= MAX_SIDE:
        return im
    s = MAX_SIDE / max(w, h)
    return im.resize((round(w * s), round(h * s)), Image.NEAREST if cls else Image.LANCZOS)


def optimise_file(path, layer):
    """Re-encode one overlay. Returns (bytes_before, bytes_after)."""
    before = path.stat().st_size
    if is_marked(path):
        return before, before
    im = Image.open(path).convert("RGBA")
    cols = legend_colours(layer)
    new = None
    if cols:
        im = downscale(im, True)
        arr, frac = snap_to_legend(np.array(im), cols, layer.get("id") in GREY_BG_LAYERS)
        if frac >= 0.4:                   # a real class map: lossless palette
            im2 = Image.fromarray(arr, "RGBA")
            new = encode(im2, lossless=True, quality=100, method=6)
            if len(new) > before:         # grey-background maps (sig, choreo-like) can be larger lossless
                new = None
    if new is None:
        im = downscale(im, False)
        if np.array(im)[..., 3].min() == 255:
            im = im.convert("RGB")
        al = np.array(im)[..., 3] if im.mode == "RGBA" else None
        graded = al is not None and len(np.unique(al)) > 2      # method 6 is 70x slower on graded alpha, saves 3 %
        new = encode(im, quality=QUALITY, method=4 if graded else 6, alpha_quality=ALPHA_Q if graded else 100)
    if len(new) >= before:                # never make a file bigger
        return before, before
    path.write_bytes(new)
    return before, len(new)


def layer_files(pub, place, manifest):
    for L in manifest["layers"]:
        if L.get("dates"):
            for d in L["dates"]:
                yield L, pub / place / L["id"] / f"{d}.webp"
        else:
            yield L, pub / place / f"{L['id']}.webp"


# ---------------------------------------------------------------- thumbnails
def _open(p):
    return Image.open(p).convert("RGBA") if p.exists() else None


def _over(bg_rgb, *layers, size=None):
    base = Image.new("RGBA", size or layers[0].size, bg_rgb + (255,))
    for L in layers:
        if L.size != base.size:
            L = L.resize(base.size, Image.LANCZOS)
        base = Image.alpha_composite(base, L)
    return base.convert("RGB")


def _last(pub, place, L):
    return pub / place / L["id"] / f"{L['dates'][-1]}.webp" if L.get("dates") else pub / place / f"{L['id']}.webp"


def thumb_source(pub, place, m):
    """The most telling picture for a place, as an RGB image."""
    by = {L["id"]: L for L in m["layers"]}
    if m.get("kind") == "event":
        if "fringes" in by:
            return _over(BG, _open(_last(pub, place, by["fringes"])))
        if "coh_loss" in by and "coherence" in by:
            co = _open(_last(pub, place, by["coherence"]))
            return _over(BG, co, _open(_last(pub, place, by["coh_loss"])))
        L = m["layers"][0]
        return _over(BG, _open(_last(pub, place, L)))
    if "water" in by and by["water"].get("dates"):
        w = _open(_last(pub, place, by["water"]))
        under = None
        if "hh" in by and by["hh"].get("dates"):
            h = _open(_last(pub, place, by["hh"]))
            if h is not None:
                a = np.array(h.convert("RGB"), np.float32) * 0.28
                under = Image.fromarray(a.astype(np.uint8)).convert("RGBA")
                under.putalpha(h.getchannel("A"))
        return _over(BG, *(x for x in (under, w) if x is not None))
    for key in ("rgb", "classes"):
        if key in by and not by[key].get("dates"):
            return _over(BG, _open(_last(pub, place, by[key])))
    L = next((L for L in m["layers"]), None)
    return _over(BG, _open(_last(pub, place, L)))


def fit_webp(img, width, limit, floor=0.75):
    """Highest quality (<= 82) that fits `limit` bytes; if none does at `width`, shrink to `floor` x width.
    Never returns None: the smallest attempt is used when nothing fits."""
    best = None
    for w in (width, round(width * 0.87), round(width * floor)):
        im = img.resize((w, round(img.height * w / img.width)), Image.LANCZOS) if img.width > w else img
        for q in (82, 74, 66, 58, 50, 42, 35):
            b = io.BytesIO()
            im.save(b, "WEBP", quality=q, method=6)
            if best is None or len(b.getvalue()) < len(best):
                best = b.getvalue()
            if len(b.getvalue()) <= limit:
                return b.getvalue()
        if img.width <= w:
            break
    return best


def write_if_changed(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists() or p.read_bytes() != data:
        p.write_bytes(data)


def make_thumbs(pub, place, m):
    src = thumb_source(pub, place, m)
    t1 = fit_webp(src, THUMB_W, THUMB_MAX)
    write_if_changed(pub / "thumbs" / f"{place}.webp", t1)
    p2 = pub / "thumbs" / f"{place}@2x.webp"
    t2 = fit_webp(src, THUMB2_W, THUMB2_MAX, floor=1.0) if src.width >= THUMB2_W else None
    if t2 is not None and len(t2) > THUMB2_MAX:
        t2 = None
    if t2 is None:
        p2.unlink(missing_ok=True)
    else:
        write_if_changed(p2, t2)
    return len(t1), (len(t2) if t2 else 0)


# ---------------------------------------------------------------- summary.json
def _r(v, n=1):
    return None if v is None else round(float(v), n)


def _series_headline(ts):
    """(fact, index) that describes a season place: peak open water; for places where open water is negligible
    and a % fact exists (Ararat), that % series first to last."""
    facts = ts.get("facts") or []
    if not facts:
        return None
    water = next((f for f in facts if f["label"].lower().startswith("open water")), facts[0])
    pct = next((f for f in facts if f.get("unit") == "%"), None)
    vals = [v for v in water["values"] if v is not None]
    if pct and (not vals or max(vals) < 1.0):
        return pct, "pct"
    return water, "peak"


CATEGORIES = [("flood", "Floods"), ("lake", "Lakes"), ("wetland", "Wetlands and deltas"), ("land", "Farmland"), ("fire", "Wildfires"),
              ("earthquake", "Earthquakes"), ("volcano", "Volcanoes"), ("landslide", "Landslides"), ("storm", "Storms")]
CAT_LABEL = dict(CATEGORIES)


def cat_label(c):
    return CAT_LABEL.get(c) or c.replace("_", " ").replace("-", " ").title()


def summarise(place, m, ts):
    ev = m.get("event")
    kind = "event" if m.get("kind") == "event" else "season"
    w, s, e, n = m["bounds"]
    centre = [round((w + e) / 2, 4), round((s + n) / 2, 4)]
    layers = m["layers"]
    ids = [L["id"] for L in layers]
    cat = m.get("category") or (ev["type"] if ev else None) or "other"
    out = {"id": place, "name": m["name"], "kind": kind, "category": cat, "eventType": ev["type"] if ev else None,
           "source": m.get("source"), "centre": centre, "bounds": [round(x, 4) for x in m["bounds"]]}
    if m.get("caution"):  # where an independent product contradicts our map: the app must say so
        out["caution"] = m["caution"]
    words = [m["name"], place.replace("-", " "), kind, m.get("source") or "", cat_label(cat), cat]
    if kind == "event":
        views = ev.get("views") or []
        before = [v["before"] for v in views if v.get("before")]
        after = [v["after"] for v in views if v.get("after")]
        out["dates"] = {"count": 2, "first": min(before) if before else None, "last": max(after) if after else None}
        out["defaultLayer"] = "fringes" if "fringes" in ids else ids[0]
        p2p = [v["p2p_cm"] for v in views if v.get("p2p_cm") is not None]
        out["headline"] = {"value": ev.get("magnitude"), "unit": "Mw" if ev.get("magnitude") is not None else None,
                           "label": ev.get("title") or (f"{ev['type']} at {ev.get('place')}"),
                           "date": (ev.get("time") or "")[:10] or None,
                           "p2p_cm": max(p2p) if p2p else None, "summary": ev.get("summary")}
        out["centre"] = [round(ev["lngLat"][0], 4), round(ev["lngLat"][1], 4)] if ev.get("lngLat") else centre
        out["spark"], out["change"] = None, None
        words += [ev["type"], ev.get("place") or ""]
    else:
        d = m["dates"]
        out["dates"] = {"count": len(d), "first": d[0] if d else None, "last": d[-1] if d else None}
        want = m.get("defaultLayer")
        out["defaultLayer"] = want if want in ids else ("water" if "water" in ids else ids[0])
        # a place whose story is not about water (a burn scar) must not be summarised by its open-water area
        fh = None if m.get("headline") == "none" else _series_headline(ts)
        out["note"] = m.get("headlineNote")
        out["defaultDate"] = m.get("defaultDate") if m.get("defaultDate") in d else None
        out["headline"], out["spark"], out["change"] = None, None, None
        if fh:
            f, mode = fh
            vals, dates = f["values"], ts["dates"]
            ok = [i for i, v in enumerate(vals) if v is not None]
            if ok:
                i = max(ok, key=lambda j: vals[j]) if mode == "peak" else ok[-1]
                h = {"value": _r(vals[i]), "unit": f["unit"], "label": f["label"], "date": dates[i],
                     "lo": _r(f["lo"][i], 0) if f.get("lo") else None, "hi": _r(f["hi"][i], 0) if f.get("hi") else None}
                if mode == "pct":
                    h["from"] = {"value": _r(vals[ok[0]]), "date": dates[ok[0]]}
                    h["label"] = f"{f['label']} (first to last date)"
                out["headline"] = h
                a, b = vals[ok[0]], vals[ok[-1]]
                out["spark"] = [_r(v) for v in vals]
                out["change"] = {"first": _r(a), "last": _r(b), "delta": _r(b - a, 2),
                                 "pct": _r((b - a) / a * 100, 1) if a else None, "unit": f["unit"]}
        words += ["calibrated" if m.get("source") == "gamma0" else "", "season water"]
    out["thumb"] = f"thumbs/{place}.webp"
    out["searchText"] = " ".join(sorted({w2 for w in words for w2 in w.lower().replace(",", " ").replace("-", " ").split() if w2 and len(w2) > 1}))
    return out


def write_summary(pub=DATA):
    import datetime
    idx = json.loads((pub / "index.json").read_text())["areas"]
    places = []
    for p in idx:
        mp = pub / p / "manifest.json"
        if not mp.exists():
            continue
        tp = pub / p / "timeseries.json"
        places.append(summarise(p, json.loads(mp.read_text()), json.loads(tp.read_text()) if tp.exists() else {}))
    old = {}
    sp = pub / "summary.json"
    if sp.exists():
        try:
            old = json.loads(sp.read_text())
        except Exception:
            pass
    gen = old.get("generated")
    if old.get("places") != places or not gen:    # keep the stamp stable when nothing changed
        gen = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    counts = {}
    for pl in places:
        counts[pl["category"]] = counts.get(pl["category"], 0) + 1
    order = [c for c, _ in CATEGORIES] + sorted(c for c in counts if c not in CAT_LABEL)
    cats = [{"id": c, "label": cat_label(c), "n": counts[c]} for c in order if counts.get(c)]
    sp.write_text(json.dumps({"generated": gen, "categories": cats, "places": places}, separators=(",", ":"), ensure_ascii=False))
    return sp.stat().st_size


# ---------------------------------------------------------------- driver
def optimise_place(place, pub=DATA, thumbs=True):
    m = json.loads((pub / place / "manifest.json").read_text())
    b = a = 0
    for L, p in layer_files(pub, place, m):
        if p.exists():
            x, y = optimise_file(p, L)
            b, a = b + x, a + y
    tb = make_thumbs(pub, place, m) if thumbs else (0, 0)
    return b, a, tb


def run(places=None, pub=DATA, verbose=True):
    allp = json.loads((pub / "index.json").read_text())["areas"]
    todo = places or allp
    tb = ta = 0
    for p in todo:
        if not (pub / p / "manifest.json").exists():
            print(f"skip {p}: no manifest", file=sys.stderr)
            continue
        b, a, th = optimise_place(p, pub)
        tb, ta = tb + b, ta + a
        if verbose:
            print(f"{p:28s} {b / 1e6:7.2f} MB -> {a / 1e6:7.2f} MB  ({(1 - a / b) * 100 if b else 0:4.0f} % less)  thumb {th[0] / 1e3:.1f}/{th[1] / 1e3:.1f} kB", flush=True)
    s = write_summary(pub)
    if verbose:
        print(f"TOTAL overlays {tb / 1e6:.2f} MB -> {ta / 1e6:.2f} MB; summary.json {s / 1e3:.1f} kB")
    return tb, ta


if __name__ == "__main__":
    run(sys.argv[1:] or None)
