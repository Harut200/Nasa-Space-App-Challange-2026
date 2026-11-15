"""Fake contract C output, so the frontend can be watched without real NISAR data.
INVENTED pixels and numbers. Run `--clean` before submission.

    .venv/bin/python scripts/ingest/make_mock.py           write mock-lake and mock-bare
    .venv/bin/python scripts/ingest/make_mock.py --clean   remove them

mock-lake hits every UI path: 4 layers (ramp, diverging and text legends, one undated),
the instrument gap, a layer missing some dates, spots, chart with nulls, facts, story.
mock-bare hits the empty states: one date, no spots, no story, no timeseries.json.
"""
# ponytail: writes webp directly, skips GeoTIFF + reprojection; real path is make_fixture.py -> export.py
import json, shutil, sys
from pathlib import Path
import numpy as np
from PIL import Image

PUB = Path(__file__).resolve().parents[2] / "src/app/public/data"
CREDITS = "MOCK DATA, invented for interface testing. Not NISAR measurements."
DATES = ["2026-06-20", "2026-07-02", "2026-07-14", "2026-07-26", "2026-08-22", "2026-09-03", "2026-09-15", "2026-09-27", "2026-10-09"]
GAP = ["2026-07-27", "2026-08-10"]
BOUNDS = [103.6, 12.5, 104.6, 13.4]  # west, south, east, north
W, H = 600, 554
RANGES = {"hh": (-25, -5), "hv": (-30, -10)}
SPOTS = [{"id": "lake", "label": "Open lake", "lngLat": [104.1, 12.95]},
         {"id": "shore", "label": "Flooded shore", "lngLat": [104.3, 12.95]},
         {"id": "forest", "label": "Dry forest", "lngLat": [103.75, 13.25]}]


def ramp(lo, hi):
    return {"type": "ramp", "unit": "dB", "min": lo, "max": hi, "colors": ["#000000", "#ffffff"]}


def save(path, rgba):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba.astype("uint8"), "RGBA").save(path, "WEBP", quality=80)


def grey(db, lo, hi, alpha=255):
    v = ((db - lo) / (hi - lo) * 255).clip(0, 255)
    return np.dstack([v, v, v, np.full_like(v, alpha)])


def pixel(lng, lat):
    w, s, e, n = BOUNDS
    return int((n - lat) / (n - s) * H), int((lng - w) / (e - w) * W)


def write_index(ids):
    PUB.mkdir(parents=True, exist_ok=True)
    (PUB / "index.json").write_text(json.dumps({"areas": ids}, indent=2) + "\n")


def clean():
    for d in PUB.glob("mock-*"):
        shutil.rmtree(d)
    idx = json.loads((PUB / "index.json").read_text()) if (PUB / "index.json").exists() else {"areas": []}
    kept = [a for a in idx["areas"] if not a.startswith("mock-")]
    write_index(kept)
    return kept


def lake(rng):
    out = PUB / "mock-lake"
    rr, cc = np.mgrid[:H, :W]
    dist = np.hypot(rr - H / 2, cc - W / 2) + 25 * np.sin(cc / 40) * np.cos(rr / 55)  # wobbly shoreline
    radius = [60, 80, 105, 130, 190, 215, 225, 200, 170]  # the lake grows, then drains
    db = {pol: [np.where(dist < r, (lo + hi) / 2 - 9, (lo + hi) / 2 + 3) + rng.normal(0, 1.2, (H, W))
                for r in radius] for pol, (lo, hi) in RANGES.items()}
    for pol, (lo, hi) in RANGES.items():
        for d, a in zip(DATES, db[pol]):
            save(out / pol / f"{d}.webp", grey(a, lo, hi))

    # change since first pass; one date left out on purpose -> "No image for…, showing…" note
    change_dates = [d for d in DATES[1:] if d != DATES[5]]
    for d in change_dates:
        diff = db["hh"][DATES.index(d)] - db["hh"][0]
        img = np.zeros((H, W, 4))
        img[diff < -4] = (0x21, 0x66, 0xAC, 220)
        img[diff > 4] = (0xB2, 0x18, 0x2B, 220)
        save(out / "change" / f"{d}.webp", img)

    lo, hi = RANGES["hh"]
    rgb = np.dstack([grey(db["hh"][i], lo, hi)[..., 0] for i in (0, 4, 8)] + [np.full((H, W), 255)])
    save(out / "rgb.webp", rgb)

    def mean(pol, i, spot):  # 9x9 window around the spot
        r, c = pixel(*spot["lngLat"])
        return round(float(db[pol][i][r - 4:r + 5, c - 4:c + 5].mean()), 1)

    series = [{"spot": s["id"], **{pol: [mean(pol, i, s) for i in range(len(DATES))] for pol in RANGES}} for s in SPOTS]
    series[2]["hh"][3] = series[2]["hv"][3] = None  # no valid pixels: the line must break
    water = [round(float((dist < r).mean() * 100 * 100)) for r in radius]  # ~100 x 100 km box
    water[6] = None
    (out / "timeseries.json").write_text(json.dumps({
        "dates": DATES, "series": series,
        "facts": [{"label": "Open water", "unit": "km²", "values": water},
                  {"label": "Flooded since first pass", "unit": "km²", "values": [None] + [max(0, w - water[0]) if w else None for w in water[1:]]}],
    }, indent=2))
    (out / "manifest.json").write_text(json.dumps({
        "id": "mock-lake", "name": "Mock lake (invented)", "bounds": BOUNDS, "dates": DATES, "gaps": [GAP],
        "layers": [
            {"id": "hh", "label": "HH backscatter", "dates": DATES, "legend": ramp(*RANGES["hh"])},
            {"id": "hv", "label": "HV backscatter", "dates": DATES, "legend": ramp(*RANGES["hv"])},
            {"id": "change", "label": "Change since first pass", "dates": change_dates,
             "legend": {"type": "diverging", "unit": "dB", "min": -8, "max": 8, "colors": ["#2166ac", "#00000000", "#b2182b"],
                        "labels": ["darker: new water, cleared forest", "brighter: flooded vegetation, growth"]}},
            {"id": "rgb", "label": "Three-date composite", "dates": None,
             "legend": {"type": "text", "text": "Red = early, green = middle, blue = late. Grey = no change."}},
        ],
        "story": {"see": "A dark disc grows from June to mid-September, then shrinks. This is mock data.",
                  "physics": "Smooth open water reflects the radar pulse away from the satellite, so it looks dark.",
                  "lband": "L-band passes through cloud and thin vegetation, so the water edge stays visible in the wet season.",
                  "people": "The flooded area decides where people can fish and where they can plant rice."},
        "spots": SPOTS, "credits": CREDITS,
    }, indent=2))


def bare(rng):
    out = PUB / "mock-bare"
    d, b = DATES[0], [44.2, 39.9, 44.8, 40.4]
    save(out / "hh" / f"{d}.webp", grey(-12 + rng.normal(0, 3, (400, 400)), -25, -5, alpha=200))
    (out / "manifest.json").write_text(json.dumps({
        "id": "mock-bare", "name": "Mock bare (empty states)", "bounds": b, "dates": [d], "gaps": [],
        "layers": [{"id": "hh", "label": "HH backscatter", "dates": [d], "legend": ramp(-25, -5)}],
        "story": {"see": "", "physics": "", "lband": "", "people": ""}, "spots": [], "credits": CREDITS,
    }, indent=2))


if __name__ == "__main__":
    kept = clean()
    if "--clean" not in sys.argv:
        rng = np.random.default_rng(42)
        lake(rng)
        bare(rng)
        write_index(kept + ["mock-lake", "mock-bare"])
        # self-check: every image the manifests promise exists
        for a in ("mock-lake", "mock-bare"):
            m = json.loads((PUB / a / "manifest.json").read_text())
            for L in m["layers"]:
                for p in ([f"{L['id']}/{x}.webp" for x in L["dates"]] if L["dates"] else [f"{L['id']}.webp"]):
                    assert (PUB / a / p).exists(), p
    print("areas:", json.loads((PUB / "index.json").read_text())["areas"])
