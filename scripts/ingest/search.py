"""Usage: search.py AREA. Writes data/raw/AREA/passes.json; with null track/frame/direction prints the coverage-gate table and exits 1."""
import json, sys
from collections import defaultdict
from pathlib import Path
import asf_search as asf

ROOT = Path(__file__).resolve().parents[2]
area = sys.argv[1]
a = json.loads((ROOT / "areas" / area / "area.json").read_text())
w, s, e, n = a["bbox"]
res = asf.search(
    dataset="NISAR", processingLevel="GCOV", dataMaturity="PROVISIONAL",
    intersectsWith=f"POLYGON(({w} {s}, {e} {s}, {e} {n}, {w} {n}, {w} {s}))",
    start="2026-06-17", end="2026-10-24",
)
P = lambda r: r.properties
day = lambda r: P(r)["startTime"][:10]  # startTime is UTC ("...Z")
mode = lambda r: f'{"/".join(P(r)["rangeBandwidth"] or [])} {"+".join(P(r)["mainBandPolarization"] or [])}'
key = lambda r: (P(r)["pathNumber"], P(r)["frameNumber"], P(r)["flightDirection"], mode(r))

if None in (a["track"], a["frame"], a["direction"]):
    g = defaultdict(list)
    for r in res:
        g[key(r)].append(day(r))
    print(f"{len(res)} granules")
    for k, d in sorted(g.items(), key=lambda x: -len(set(x[1]))):
        print(f"track={k[0]} frame={k[1]} dir={k[2]} mode={k[3]}: {len(set(d))} dates {sorted(set(d))}")
    sys.exit("area.json has null track/frame/direction: pick a group and fill them in")

keep = {}
for r in sorted(res, key=lambda r: P(r)["startTime"]):
    if (P(r)["pathNumber"], P(r)["frameNumber"], P(r)["flightDirection"].lower()) != (a["track"], a["frame"], a["direction"].lower()):
        continue
    d = day(r)
    if d in keep:
        print(f"dropped duplicate on {d}: {P(r)['sceneName']}")
        continue
    keep[d] = {"date": d, "url": P(r)["url"], "granule": P(r)["sceneName"]}
out = ROOT / "data" / "raw" / area / "passes.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(list(keep.values()), indent=1))
print(f"{len(keep)} passes -> {out}")
# ponytail: mode (bandwidth/pol) not filtered; check the table if a track mixes modes
