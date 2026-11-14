"""Add any place to the app with one command.

    .venv/bin/python scripts/ingest/add_area.py <id> --name "Name, Country" --bbox W S E N
        [--change-pol hh|hv] [--force] [--list] [--dry-run]

1. Searches ASF (no login) for every PROVISIONAL NISAR GCOV pass touching the box
   between 17 Jun and 24 Oct 2026 and groups them by track + frame + direction.
2. Picks the group whose footprint holds the whole box on every date with the most
   "Full" HH+HV passes in one bandwidth mode (at least MIN_DATES). If no group holds
   the whole box, shrinks the box to the largest sub-box inside the best group's
   footprint (short side at least MIN_SIDE degrees) and says so.
3. Writes areas/<id>/area.json, then runs fetch_previews -> detect/terrain (elevation
   mask, skipped with a warning if the DEM download fails) -> detect/run -> export and
   prints a summary. Re-running skips downloads that already exist.

--list     print the candidate table and stop
--dry-run  choose (and print the choice) but write nothing
--force    overwrite an existing areas/<id>/area.json with a fresh choice
"""
from __future__ import annotations

import argparse
import collections
import json
import shutil
import pathlib
import subprocess
import sys
import time
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
API = "https://api.daac.asf.alaska.edu/services/search/param"
UA = {"User-Agent": "sar-hackathon-client"}
START, END = "2026-06-17T00:00:00Z", "2026-10-24T00:00:00Z"
MIN_DATES, GOOD_DATES = 6, 10
MARGIN = 0.03        # degrees the box must stay inside the footprint edge
MIN_SIDE = 0.8       # degrees, shortest side allowed after shrinking
PROTECTED = {"tonle-sap", "sevan", "ararat", "demo"}
PY = sys.executable
PREFETCH_WORKERS = 3
TERRAIN_TIMEOUT = 900
SUSPECT_RATIO = 0.85     # preview smaller than this x median -> treated as corrupt


# ------------------------------- search -------------------------------------
def search(bbox):
    w, s, e, n = bbox
    params = {
        "dataset": "NISAR", "processingLevel": "GCOV", "dataMaturity": "PROVISIONAL",
        "intersectsWith": f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))",
        "start": START, "end": END, "maxResults": 2000, "output": "jsonlite",
    }
    req = urllib.request.Request(API + "?" + urllib.parse.urlencode(params), headers=UA)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.load(r)["results"]
        except OSError as err:  # timeouts, resets, HTTP 5xx
            print(f"search attempt {attempt + 1} failed: {err}", flush=True)
            time.sleep(5 * (attempt + 1))
    sys.exit("add_area: ASF search failed three times; try again later")


def parse_wkt(wkt):
    inner = wkt[wkt.index("((") + 2: wkt.index(")")]
    pts = [tuple(map(float, p.split())) for p in inner.split(",")]
    return pts[:-1] if pts[0] == pts[-1] else pts


def record(r):
    nisar = r.get("nisar") or {}
    pol = nisar.get("mainBandPolarization") or []
    bw = nisar.get("rangeBandwidth") or []
    return {
        "track": int(r["path"]), "frame": int(r["frame"]),
        "direction": str(r["flightDirection"]).lower(),
        "date": r["startTime"][:10], "poly": parse_wkt(r["wkt"]),
        "pol": "+".join(sorted(pol if isinstance(pol, list) else [pol])),
        "bw": "/".join(bw if isinstance(bw, list) else [str(bw)]),
        "coverage": nisar.get("frameCoverage", "Full"), "crid": nisar.get("crid") or "",
        "preview": next((u for u in r.get("browse") or [] if u.endswith("LATLON.png")), None),
    }


# ------------------------------- geometry -----------------------------------
def point_in_poly(x, y, poly):
    inside = False
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def box_inside(bbox, poly, margin=MARGIN):
    """All four corners of the box grown by `margin` inside the (convex) footprint."""
    w, s, e, n = bbox
    w, s, e, n = w - margin, s - margin, e + margin, n + margin
    return all(point_in_poly(x, y, poly) for x, y in ((w, s), (e, s), (e, n), (w, n)))


def _grid(bbox, step):
    w, s, e, n = bbox
    nx, ny = max(1, round((e - w) / step)), max(1, round((n - s) / step))
    dx, dy = (e - w) / nx, (n - s) / ny
    return nx, ny, dx, dy


def box_share(bbox, polys, step=0.02):
    """Share of the box inside every footprint in `polys` (sampled on a grid)."""
    w, s, e, n = bbox
    nx, ny, dx, dy = _grid(bbox, step)
    hit = sum(all(point_in_poly(w + (i + .5) * dx, s + (j + .5) * dy, p) for p in polys)
              for i in range(nx) for j in range(ny))
    return hit / (nx * ny)


def largest_subbox(bbox, polys, margin=MARGIN, step=0.02, min_side=MIN_SIDE):
    """Largest axis-aligned sub-box of `bbox` that lies (with margin) inside every polygon.

    Cells of `step` degrees are marked usable when their corners, grown by `margin`,
    are inside every footprint; the maximal rectangle of usable cells is found with
    the histogram method. Returns None if its short side is below `min_side`.
    """
    w, s, e, n = bbox
    nx, ny, dx, dy = _grid(bbox, step)
    ok = [[all(box_inside((w + i * dx, n - (j + 1) * dy, w + (i + 1) * dx, n - j * dy), p, margin)
               for p in polys)
           for i in range(nx)] for j in range(ny)]
    best, heights = None, [0] * nx
    for j in range(ny):
        heights = [heights[i] + 1 if ok[j][i] else 0 for i in range(nx)]
        stack = []
        for i in range(nx + 1):
            h = heights[i] if i < nx else 0
            start = i
            while stack and stack[-1][1] >= h:
                start, hh = stack.pop()
                width_deg, height_deg = (i - start) * dx, hh * dy
                if min(width_deg, height_deg) + 1e-9 < min_side:
                    continue
                area = width_deg * height_deg
                if best is None or area > best[0]:
                    best = (area, (w + start * dx, n - (j + 1) * dy, w + i * dx, n - (j + 1 - hh) * dy))
            stack.append((start, h))
    return None if best is None else tuple(round(v, 3) for v in best[1])


# ------------------------------- grouping -----------------------------------
def summarise(records, bbox):
    """Group passes by track/frame/direction and describe each group."""
    groups = collections.defaultdict(list)
    for r in records:
        groups[(r["track"], r["frame"], r["direction"])].append(r)
    out = []
    for (track, frame, direction), rs in groups.items():
        full = [r for r in rs if r["coverage"] == "Full" and r["pol"] == "HH+HV"]
        by_date = {}
        for r in sorted(full, key=lambda r: r["crid"]):
            by_date[r["date"]] = r          # highest crid wins
        modes = collections.Counter(r["bw"] for r in by_date.values())
        mode = modes.most_common(1)[0][0] if modes else ""
        good = sorted((r for r in by_date.values() if r["bw"] == mode), key=lambda r: r["date"])
        polys = [r["poly"] for r in good]
        inside = bool(good) and all(box_inside(bbox, p) for p in polys)
        all_dates = sorted({r["date"] for r in rs})
        out.append({
            "track": track, "frame": frame, "direction": direction,
            "dates": [r["date"] for r in good], "n": len(good), "mode": mode,
            "previews": {r["date"]: r["preview"] for r in good if r.get("preview")},
            "n_all_dates": len(all_dates), "hh_only_or_partial": len(all_dates) - len(by_date),
            "other_mode": len(by_date) - len(good),
            "inside": inside, "share": 1.0 if inside else (box_share(bbox, polys) if polys else 0.0),
            "polys": polys,
        })
    out.sort(key=rank, reverse=True)
    return out


def rank(g):
    return (g["inside"] and g["n"] >= MIN_DATES, g["n"] >= MIN_DATES, g["share"] >= 0.6 and g["n"],
            g["inside"], g["n"], g["share"])


def choose(groups, bbox):
    """-> (group, bbox_used, note) or (None, None, reason)."""
    ok = [g for g in groups if g["inside"] and g["n"] >= MIN_DATES]
    if ok:
        g = max(ok, key=lambda g: g["n"])
        note = "" if g["n"] >= GOOD_DATES else f"only {g['n']} dates (prefer {GOOD_DATES})"
        return g, tuple(bbox), note
    cands = sorted((g for g in groups if g["n"] >= MIN_DATES), key=lambda g: (g["share"], g["n"]), reverse=True)
    for g in cands:
        sub = largest_subbox(bbox, g["polys"])
        if sub:
            return g, sub, f"box shrunk from {list(bbox)} to {list(sub)} to fit the footprint on every date"
    return None, None, "no track/frame has the box (or a sub-box >= %.1f deg) inside on >= %d dates" % (MIN_SIDE, MIN_DATES)


def table(groups):
    lines = ["track frame dir   HH+HV-Full dates  mode      inside  share  (other mode / HH-only or Partial)"]
    for g in groups:
        lines.append(f"{g['track']:>5} {g['frame']:>5} {g['direction'][:4]:<5} {g['n']:>16}  {g['mode']:<9} "
                     f"{'yes' if g['inside'] else 'no':<6} {g['share']:>5.2f}  ({g['other_mode']} / {g['hh_only_or_partial']})")
    return "\n".join(lines)


# ------------------------------- pipeline -----------------------------------
def step(*args, tries=1):
    for attempt in range(tries):
        print("\n$", " ".join(map(str, args)), flush=True)
        rc = subprocess.run([PY, *map(str, args)], cwd=ROOT).returncode
        if rc == 0:
            return
        if attempt + 1 < tries:
            print(f"step failed (exit {rc}); retrying ({attempt + 2}/{tries}) - finished downloads are kept", flush=True)
            time.sleep(10 * (attempt + 1))
    if rc:
        sys.exit(f"add_area: step failed (exit {rc}): {' '.join(map(str, args))}")


def prefetch(area, previews, workers=PREFETCH_WORKERS):
    """Download the chosen dates' preview PNGs in parallel (fetch_previews.py is serial
    and has no retry; it skips files that already exist, so this only speeds it up)."""
    from concurrent.futures import ThreadPoolExecutor
    folder = ROOT / "data/raw/previews" / f"T{area['track']}_F{area['frame']}"
    folder.mkdir(parents=True, exist_ok=True)
    todo = {d: u for d, u in previews.items() if not (folder / f"{d}.png").exists()}
    print(f"prefetch: {len(previews) - len(todo)} previews present, {len(todo)} to download ({workers} at a time)",
          flush=True)

    def get(item):
        d, url = item
        for attempt in range(4):
            try:
                req = urllib.request.Request(url, headers=UA)
                with urllib.request.urlopen(req, timeout=900) as r:
                    data = r.read()
                tmp = folder / f".{d}.png.part"
                tmp.write_bytes(data)
                tmp.rename(folder / f"{d}.png")
                print(f"  got {d} ({len(data) // 1024} KB)", flush=True)
                return True
            except OSError as err:
                print(f"  {d}: attempt {attempt + 1} failed: {err}", flush=True)
                time.sleep(5 * (attempt + 1))
        return False

    with ThreadPoolExecutor(workers) as ex:
        ok = list(ex.map(get, todo.items()))
    if not all(ok):
        print("prefetch: some downloads failed; fetch_previews.py will try them again", flush=True)


def terrain(area_id, timeout=TERRAIN_TIMEOUT):
    """Build the elevation mask (scripts/detect/terrain.py) that run.py applies to open water.
    Optional: if the DEM download fails or stalls, carry on without it and say so."""
    if (ROOT / "data/processed" / area_id / "terrain.pt").exists():
        print("terrain mask already built", flush=True)
        return True
    print(f"\n$ -m scripts.detect.terrain {area_id}   (timeout {timeout}s)", flush=True)
    try:
        rc = subprocess.run([PY, "-m", "scripts.detect.terrain", area_id], cwd=ROOT, timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        rc = "timeout"
    if rc != 0:
        print(f"WARNING: terrain mask not built ({rc}); open water is NOT masked by elevation for {area_id}",
              flush=True)
        return False
    return True


def suspect_previews(area, ratio=SUSPECT_RATIO):
    """Dates whose preview PNG is much smaller than the median of the chosen dates.

    Every corrupt preview seen so far (striped garbage, flat grey, half-empty swath) was
    a file below ~0.85 x the median size, while good previews of one frame vary by about
    +-10 %. A heuristic, not a check of the pixels -- always look at the hh layer.
    """
    folder = ROOT / "data/raw/previews" / f"T{area['track']}_F{area['frame']}"
    sizes = {d: (folder / f"{d}.png").stat().st_size for d in area.get("dates", [])
             if (folder / f"{d}.png").exists()}
    if len(sizes) < 3:
        return []
    med = sorted(sizes.values())[len(sizes) // 2]
    return sorted(d for d, sz in sizes.items() if sz < ratio * med)


def filter_meta(area, keep_dates):
    """Keep only the chosen consistent-mode HH+HV dates in the shared preview meta.json."""
    folder = ROOT / "data/raw/previews" / f"T{area['track']}_F{area['frame']}"
    others = [p for p in (ROOT / "areas").glob("*/area.json")
              if p.parent.name != area["id"] and json.loads(p.read_text()).get("track") == area["track"]
              and json.loads(p.read_text()).get("frame") == area["frame"]]
    meta_p = folder / "meta.json"
    meta = json.loads(meta_p.read_text())
    before = len(meta["passes"])
    kept = [p for p in meta["passes"] if p["date"] in keep_dates]
    if others and len(kept) != before:
        print(f"note: {meta_p} is shared with {[p.parent.name for p in others]}; not filtering it")
        return
    meta["passes"] = kept
    meta_p.write_text(json.dumps(meta, indent=2))
    if len(kept) != before:
        print(f"kept {len(kept)} of {before} downloaded passes (dropped HH-only / other mode)")


def folder_mb(p):
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1e6


def summary(area_id, g):
    pub = ROOT / "src/app/public/data" / area_id
    man = json.loads((pub / "manifest.json").read_text())
    ts = json.loads((pub / "timeseries.json").read_text())
    from PIL import Image
    first = next(pub.glob("hh/*.webp"))
    size = Image.open(first).size
    print("\n=== summary:", area_id, "===")
    print(f"track {g['track']} frame {g['frame']} {g['direction']}, mode {g['mode']}")
    print("bounds", [round(v, 3) for v in man["bounds"]], "web image", f"{size[0]}x{size[1]} px")
    water = ts["facts"][0]["values"]
    for d, v in zip(ts["dates"], water):
        print(f"  {d}  open water {v:>9.1f} km2 (uncalibrated preview)")
    print(f"output folder {pub} : {folder_mb(pub):.1f} MB")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Add any place to the app with one command.")
    ap.add_argument("id")
    ap.add_argument("--name")
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("W", "S", "E", "N"))
    ap.add_argument("--change-pol", choices=["hh", "hv"])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep-all", action="store_true",
                    help="do not drop previews flagged by the corrupt-preview size heuristic")
    ap.add_argument("--exclude", nargs="+", default=[], metavar="YYYY-MM-DD",
                    help="drop these dates (e.g. a corrupted preview) and re-run")
    a = ap.parse_args(argv)

    area_p = ROOT / "areas" / a.id / "area.json"
    if a.id in PROTECTED and (a.force or not area_p.exists()):
        sys.exit(f"add_area: '{a.id}' is a hand-made area; not touching it")
    if area_p.exists() and not a.force and not a.list:
        area = json.loads(area_p.read_text())
        print(f"reusing {area_p} (pass --force to re-choose)")
        g = {"track": area["track"], "frame": area["frame"], "direction": area["direction"],
             "mode": area.get("mode", "?"), "dates": area.get("dates"), "previews": {}}
        folder = ROOT / "data/raw/previews" / f"T{area['track']}_F{area['frame']}"
        if area.get("dates") and not all((folder / f"{d}.png").exists() for d in area["dates"]):
            print("searching ASF again for the preview links ...", flush=True)
            for grp in summarise([record(r) for r in search(area["bbox"])], area["bbox"]):
                if (grp["track"], grp["frame"], grp["direction"]) == (g["track"], g["frame"], g["direction"]):
                    g["previews"] = {d: u for d, u in grp["previews"].items() if d in area["dates"]}
    else:
        if not a.bbox:
            sys.exit("add_area: --bbox W S E N is required")
        w, s, e, n = a.bbox
        if not (w < e and s < n):
            sys.exit("add_area: bbox must be W S E N with W < E and S < N")
        print(f"searching ASF for NISAR GCOV passes over {a.bbox} ...", flush=True)
        groups = summarise([record(r) for r in search(a.bbox)], a.bbox)
        if not groups:
            sys.exit("add_area: no NISAR GCOV passes intersect this box")
        print(table(groups))
        if a.list:
            return
        g, box, note = choose(groups, a.bbox)
        if g is None:
            sys.exit(f"add_area: {note}\n{table(groups)}")
        print(f"\nchosen: track {g['track']} frame {g['frame']} {g['direction']}, {g['n']} dates, mode {g['mode']}")
        if note:
            print("NOTE:", note)
        area = {"id": a.id, "name": a.name or a.id, "bbox": list(box), "track": g["track"],
                "frame": g["frame"], "direction": g["direction"], "pixel_m": 100}
        if a.change_pol:
            area["change_pol"] = a.change_pol
        area.update(mode=g["mode"], dates=g["dates"])
        if a.dry_run:
            print(json.dumps(area, indent=2))
            return
        area_p.parent.mkdir(parents=True, exist_ok=True)
        area_p.write_text(json.dumps(area, indent=2) + "\n")
        print("wrote", area_p)

    if g.get("previews"):
        prefetch(area, g["previews"])
    step("scripts/ingest/fetch_previews.py", a.id, tries=4)
    exclude = list(a.exclude)
    if area.get("dates") and not a.keep_all:
        bad = suspect_previews(area)
        if bad:
            print(f"WARNING: previews {bad} are much smaller than the others (corrupt-preview "
                  f"heuristic); excluding them. Use --keep-all to keep them. Look at the hh layer!")
        exclude += [d for d in bad if d not in exclude]
    if exclude and area.get("dates"):
        a.exclude = exclude
        area["dates"] = [d for d in area["dates"] if d not in a.exclude]
        area.setdefault("excluded_dates", [])
        area["excluded_dates"] = sorted(set(area["excluded_dates"]) | set(a.exclude))
        area_p.write_text(json.dumps(area, indent=2) + "\n")
        g["dates"] = area["dates"]
        print("excluded", a.exclude, "->", len(area["dates"]), "dates left")
        if len(area["dates"]) < MIN_DATES:
            sys.exit(f"add_area: only {len(area['dates'])} usable dates left (< {MIN_DATES})")
    if g.get("dates"):
        filter_meta(area, set(g["dates"]))
    terrain(a.id)
    # products are regenerated from scratch so a dropped date cannot linger in the export
    for stale in (ROOT / "data/processed" / a.id / "products", ROOT / "src/app/public/data" / a.id):
        if stale.exists():
            shutil.rmtree(stale)
    step("scripts/detect/run.py", a.id)
    step("scripts/ingest/export.py", a.id)
    summary(a.id, g)


if __name__ == "__main__":
    main()
