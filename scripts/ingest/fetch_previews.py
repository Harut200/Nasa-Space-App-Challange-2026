"""Download the free NISAR GCOV preview images for an area. No login needed.

    python scripts/ingest/fetch_previews.py tonle-sap

Writes data/raw/previews/T{track}_F{frame}/{date}.png and meta.json.
Previews are about 150 m per pixel and are NOT calibrated: use them for
pictures and exploration, not for published numbers.
"""
import json
import pathlib
import sys
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
API = "https://api.daac.asf.alaska.edu/services/search/param"
UA = {"User-Agent": "sar-hackathon-client"}


def search(area):
    w, s, e, n = area["bbox"]
    params = {
        "dataset": "NISAR",
        "processingLevel": "GCOV",
        "dataMaturity": "PROVISIONAL",
        "relativeOrbit": area["track"],
        "frame": area["frame"],
        "flightDirection": area["direction"].upper(),
        "intersectsWith": f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))",
        "start": "2026-06-17T00:00:00Z",
        "end": "2026-10-24T00:00:00Z",
        "maxResults": 250,
        "output": "jsonlite",
    }
    req = urllib.request.Request(API + "?" + urllib.parse.urlencode(params), headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)["results"]


def main(area_id):
    area = json.loads((ROOT / "areas" / area_id / "area.json").read_text())
    out = ROOT / "data" / "raw" / "previews" / f"T{area['track']}_F{area['frame']}"
    out.mkdir(parents=True, exist_ok=True)

    passes = {}
    for r in sorted(search(area), key=lambda r: r["startTime"]):
        nisar = r.get("nisar") or {}
        if nisar.get("frameCoverage", "Full") != "Full":
            continue
        latlon = [u for u in r.get("browse") or [] if u.endswith("LATLON.png")]
        if not latlon:
            continue
        date = r["startTime"][:10]
        # A later record for the same date is a newer processing version.
        passes[date] = {
            "date": date,
            "granule": r["granuleName"],
            "startTime": r["startTime"],
            "wkt": r["wkt"],
            "crid": nisar.get("crid"),
            "polarization": nisar.get("mainBandPolarization"),
            "bandwidth": nisar.get("rangeBandwidth"),
            "preview": latlon[0],
        }

    for date, p in passes.items():
        target = out / f"{date}.png"
        if not target.exists():
            req = urllib.request.Request(p["preview"], headers=UA)
            with urllib.request.urlopen(req, timeout=300) as r:
                target.write_bytes(r.read())
        print(date, p["polarization"], p["bandwidth"], target.stat().st_size // 1024, "KB")

    (out / "meta.json").write_text(json.dumps(
        {"track": area["track"], "frame": area["frame"], "passes": list(passes.values())}, indent=2))
    print(f"{len(passes)} passes -> {out}")


if __name__ == "__main__":
    main(sys.argv[1])
