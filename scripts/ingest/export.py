"""Contract B -> contract C.  Usage: python scripts/ingest/export.py {area}"""
import json, shutil, sys
from pathlib import Path
import numpy as np
import rasterio
from PIL import Image
from rasterio.warp import Resampling, calculate_default_transform, reproject, transform

ROOT = Path(__file__).resolve().parents[2]
GAP = ["2026-07-27", "2026-08-10"]
CREDITS = "NISAR data: NASA/JPL-Caltech and ISRO, distributed by ASF DAAC. PROVISIONAL calibration."
MAX_SIDE = 3000


def load(p, default=None):
    return json.loads(p.read_text()) if p.exists() else default


def target_grid(src, max_side=MAX_SIDE):
    """One 3857 grid from the source grid; longer side capped at max_side."""
    t, w, h = calculate_default_transform(src.crs, "EPSG:3857", src.width, src.height, *src.bounds)
    if max(w, h) > max_side:
        s = max(w, h) / max_side
        t, w, h = calculate_default_transform(
            src.crs, "EPSG:3857", src.width, src.height, *src.bounds,
            resolution=(t.a * s, -t.e * s))
        w, h = min(w, max_side), min(h, max_side)
    return t, w, h


def to_webp(tif, grid, out):
    t, w, h = grid
    dst = np.zeros((4, h, w), np.uint8)
    with rasterio.open(tif) as src:
        for b in range(4):
            reproject(rasterio.band(src, b + 1), dst[b], dst_transform=t, dst_crs="EPSG:3857",
                      resampling=Resampling.nearest)
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(dst.transpose(1, 2, 0), "RGBA").save(out, "WEBP", quality=80)


def export(area, root=ROOT):
    prod = root / "data/processed" / area / "products"
    pub = root / "src/app/public/data"
    cfg = json.loads((root / "areas" / area / "area.json").read_text())
    story = load(root / "areas" / area / "story.json", {})
    layers_in = json.loads((prod / "layers.json").read_text())

    first = next(prod.glob("*/*.tif"), None) or next(prod.glob("*.tif"))
    with rasterio.open(first) as src:
        grid = target_grid(src, cfg.get("export_max_side", MAX_SIDE))  # area.json may cap the webp size (analysis unaffected)
    t, w, h = grid
    # degrees of the grid's outer pixel edges (not centres)
    (west, east), (south, north) = [
        transform("EPSG:3857", "EPSG:4326", [x0, x1], [y0, y1])[i] for i, (x0, x1, y0, y1) in
        [(0, (t.c, t.c + w * t.a, 0, 0)), (1, (0, 0, t.f + h * t.e, t.f))]]

    layers = []
    for L in layers_in:
        if L["dated"]:
            dates = sorted(p.stem for p in (prod / L["id"]).glob("*.tif"))
            for d in dates:
                to_webp(prod / L["id"] / f"{d}.tif", grid, pub / area / L["id"] / f"{d}.webp")
        else:
            dates = None
            to_webp(prod / f"{L['id']}.tif", grid, pub / area / f"{L['id']}.webp")
        layers.append({"id": L["id"], "label": L["label"], "dates": dates, "legend": L["legend"]})

    all_dates = sorted({d for L in layers for d in L["dates"] or []})
    gaps = [GAP] if all_dates and all_dates[0] <= GAP[0] and GAP[1] <= all_dates[-1] else []
    manifest = {
        "id": cfg["id"], "name": cfg["name"], "bounds": [west, south, east, north],
        "dates": all_dates, "gaps": gaps, "layers": layers,
        "story": story.get("story", {k: "" for k in ("see", "physics", "lband", "people")}),
        "spots": [{k: s[k] for k in ("id", "label", "lngLat")} for s in story.get("spots", [])],
        "credits": CREDITS,
    }
    src_info = load(prod / "source.json")  # written by scripts/detect/run.py: says preview or gamma0
    if src_info:
        manifest.update(credits=src_info.get("credits", CREDITS), source=src_info.get("source"))
    qc = load(root / "data/processed" / area / "qc.json")  # written by scripts/detect/qc.py
    if qc:
        manifest["quality"] = {p["date"]: {"quality": p["verdict"], "quality_note": p["reason"],
                                           "channels": p["fail_channels"] or p["warn_channels"]}
                               for p in qc["passes"] if p["date"] in all_dates and p["verdict"] != "ok"}
    events = load(root / "docs/eda/quake/events.json") or []  # written by scripts/detect/quake.py
    ev = next((e for e in events if e.get("id") == area), None)
    if ev:  # a single before/after pair, not a season: the app shows it as an event
        res = (load(root / "docs/eda/quake/results.json") or {}).get(area, {})
        views = []
        for key, v in ev.get("views", {}).items():
            a, b = v["co"]["pair"].split("_")
            num = res.get("views", {}).get(key, {}).get("variants", {})
            num = next(iter(num.values()), {}) if num else {}
            views.append({"id": key, "label": v.get("label", key), "track": v.get("track"),
                          "before": f"{a[:4]}-{a[4:6]}-{a[6:]}", "after": f"{b[:4]}-{b[4:6]}-{b[6:]}",
                          "p2p_cm": round(num["p2p_cm_0.5_99.5pct"]) if "p2p_cm_0.5_99.5pct" in num else None,
                          "control_p2p_cm": round(num["control_p2p_cm_0.5_99.5pct"]) if "control_p2p_cm_0.5_99.5pct" in num else None,
                          "control_before": "%s-%s-%s" % (v["control"]["pair"][:4], v["control"]["pair"][4:6], v["control"]["pair"][6:8]),
                          "control_after": "%s-%s-%s" % (v["control"]["pair"][9:13], v["control"]["pair"][13:15], v["control"]["pair"][15:17])})
        views.sort(key=lambda v: v["id"] != ev.get("primary_view"))  # the view the fringes layer shows comes first
        manifest["kind"] = "event"
        manifest["event"] = {"type": "earthquake", "magnitude": ev["mag"], "time": ev["time"], "depth_km": ev["depth_km"],
                             "lngLat": [ev["lon"], ev["lat"]], "place": ev.get("place"), "mechanism": ev.get("mechanism"),
                             "usgs_id": ev.get("usgs_id"), "radius_km": ev.get("radius_km"), "views": views}
        ba = load(prod / "before_after.json")  # written by quake.export_before: layer pairs for a before/after compare
        if ba is not None:
            manifest["event"]["before_after"] = ba["before_after"]
    other = load(root / "docs/eda/events/app_events.json") or []  # written by scripts/detect/events.py
    oe = next((e for e in other if e.get("id") == area), None)
    if oe:  # volcano, landslide and other ground events: a before/after pair, shown like an earthquake
        manifest["kind"] = "event"
        manifest["event"] = {"type": oe["type"], "title": oe.get("title"), "time": oe["time"], "time_note": oe.get("time_note"),
                             "lngLat": [oe["lon"], oe["lat"]], "place": oe.get("place"), "summary": oe.get("summary_number"),
                             "radius_km": oe.get("radius_km"), "source_url": oe.get("source_url"),
                             "views": [{k: v.get(k) for k in ("id", "label", "track", "before", "after", "p2p_cm", "control_p2p_cm", "control_before", "control_after")}
                                       for v in oe.get("views", []) if v.get("exported", True)]}
        if "before_after" in oe:  # layer pairs for a before/after compare (written by events.app_events)
            manifest["event"]["before_after"] = oe["before_after"]
    for key, field in (("default_layer", "defaultLayer"), ("default_date", "defaultDate"), ("headline", "headline"),
                       ("headline_note", "headlineNote"), ("caution", "caution")):
        if cfg.get(key):  # optional: which layer opens first, and whether the open-water number is this place's headline
            manifest[field] = cfg[key]
    manifest["category"] = (manifest["event"]["type"] if manifest.get("kind") == "event" else cfg.get("category")) or "other"
    ev = load(prod / "burn.json") or load(prod / "ev.json")  # written by scripts/detect/evidence.py (burn.py for fires)
    if ev:  # "how we found it": before / after / difference layers, the control and the inside / outside curves
        if ev.get("kind") == "burn":
            manifest["burn"] = {k: v for k, v in ev.items() if k not in ("steps", "layers", "series")}
        manifest["evidence"] = {"kind": ev.get("kind", "burn"), "before": ev["layers"]["before"], "after": ev["layers"]["after"],
                                "diff": ev["layers"]["diff"], "mask": ev["layers"]["mask"], "series": ev["series"],
                                "steps": ev["steps"], "stats": {k: v for k, v in ev.items() if k not in ("steps", "layers", "series")}}
    (pub / area / "manifest.json").write_text(json.dumps(manifest, indent=2))
    shutil.copy(prod / "timeseries.json", pub / area / "timeseries.json")
    idx = load(pub / "index.json", {"areas": []})
    if area not in idx["areas"]:
        base = area.removesuffix("-cal")  # a calibrated place sits right after its preview place
        at = idx["areas"].index(base) + 1 if area != base and base in idx["areas"] else len(idx["areas"])
        idx["areas"].insert(at, area)
    (pub / "index.json").write_text(json.dumps(idx, indent=2))


if __name__ == "__main__":
    export(sys.argv[1])
    try:  # shrink the new place's overlays, make its thumbnails, refresh summary.json (CLI only; tests call export() directly)
        import optimize
        optimize.run([sys.argv[1]])
    except Exception as e:  # the export itself is done; never fail it over optimisation
        print(f"optimize skipped: {e}", file=sys.stderr)
