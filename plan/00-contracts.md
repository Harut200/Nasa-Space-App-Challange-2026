# Contracts between the branches

> This is the plan as written on 14 November 2026, kept as it was. What differs now: the branches no longer exist (merged into `main` on 15 November); the file formats below are the ones the pipeline still uses, and the app reads them from `src/app/public/data/`. `PLAN.md`, mentioned below, was removed on 15 November (git history only).

Three hand-offs. If your output matches the contract, the next person's code works without talking to you.

Shared conventions:

- `{area}` is a lowercase id with hyphens: `tonle-sap`, `rondonia`, `ararat`, and `demo` for the synthetic test area.
- `{date}` is the acquisition start date in UTC, `YYYY-MM-DD`.
- `{pol}` is `hh` or `hv`.

## Area configuration (inputs written by hand)

`areas/{area}/area.json`: owner: **backend**

```json
{
  "id": "tonle-sap",
  "name": "Tonle Sap, Cambodia",
  "bbox": [103.6, 12.5, 104.6, 13.4],
  "track": null,
  "frame": null,
  "direction": null,
  "pixel_m": 40
}
```

`bbox` is `[west, south, east, north]` in degrees. `track`, `frame`, `direction` are filled in after the coverage gate. Choose `pixel_m` so the longer side is 2,000–3,000 pixels (100 km at 40 m = 2,500).

`areas/{area}/story.json`: owner: **detection**

```json
{
  "story": {
    "see": "What you see on the map.",
    "physics": "What is physically happening.",
    "lband": "Why L-band radar shows it.",
    "people": "Why it matters to people."
  },
  "spots": [
    { "id": "lake", "label": "Open lake", "lngLat": [104.1, 12.9], "radius_m": 1000 }
  ]
}
```

## Contract A: backend → detection

```
data/processed/{area}/gamma0/{pol}/{date}.tif
```

- GeoTIFF, one band, `float32`, **linear power** (gamma-naught), not decibels. Detection filters in linear power, so do not convert.
- No-data is `NaN`. Zeros and negative values from the source are written as `NaN`.
- Every file of one area has **exactly the same grid**: same projection (the UTM zone of the source), same transform, same width and height. Detection may stack them with no resampling.
- Pixel size is `pixel_m` from `area.json`, made by **averaging** source pixels (this also reduces speckle).
- Compression: deflate.

## Contract B: detection → backend

```
data/processed/{area}/products/{layer}/{date}.tif     one per date
data/processed/{area}/products/{layer}.tif            for a layer with no date axis
data/processed/{area}/products/layers.json
data/processed/{area}/products/timeseries.json
```

- Each `.tif` is **already coloured**: four bands, `uint8`, red-green-blue-alpha, on the same grid as contract A. Alpha 0 means "show the basemap". Backend never touches pixel values; it only reprojects and encodes.
- `layers.json` lists the layers in display order:

```json
[
  { "id": "hh", "label": "HH backscatter", "dated": true,
    "legend": { "type": "ramp", "unit": "dB", "min": -25, "max": -5, "colors": ["#000000", "#ffffff"] } },
  { "id": "hv", "label": "HV backscatter", "dated": true,
    "legend": { "type": "ramp", "unit": "dB", "min": -30, "max": -10, "colors": ["#000000", "#ffffff"] } },
  { "id": "change", "label": "Change since first pass", "dated": true,
    "legend": { "type": "diverging", "unit": "dB", "min": -8, "max": 8,
                "colors": ["#2166ac", "#00000000", "#b2182b"],
                "labels": ["darker: new water, cleared forest", "brighter: flooded vegetation, growth"] } },
  { "id": "rgb", "label": "Three-date composite", "dated": false,
    "legend": { "type": "text", "text": "Red = early, green = middle, blue = late. Grey = no change." } }
]
```

- `timeseries.json`: the same shape the app receives (see contract C).

**The change layer for date D is "D compared with the first pass".** The first date therefore has no change image. This replaces "change between any two dates" in PLAN.md: any-pair change needs 45 images per area and a second date picker; cumulative change tells the seasonal story with 9. The swipe (feature 3) still compares any two dates.

## Contract C: backend → frontend

Everything under `src/app/public/data/`, fetched by the app relative to its base URL.

```
src/app/public/data/index.json
src/app/public/data/{area}/manifest.json
src/app/public/data/{area}/timeseries.json
src/app/public/data/{area}/{layer}/{date}.webp
src/app/public/data/{area}/{layer}.webp            for layers with "dates": null
```

`index.json`:

```json
{ "areas": ["tonle-sap", "rondonia", "ararat"] }
```

`manifest.json`:

```json
{
  "id": "tonle-sap",
  "name": "Tonle Sap, Cambodia",
  "bounds": [103.6, 12.5, 104.6, 13.4],
  "dates": ["2026-06-20", "2026-07-02", "2026-07-14"],
  "gaps": [["2026-07-27", "2026-08-10"]],
  "layers": [
    { "id": "hh", "label": "HH backscatter", "dates": ["2026-06-20", "2026-07-02", "2026-07-14"], "legend": { } },
    { "id": "change", "label": "Change since first pass", "dates": ["2026-07-02", "2026-07-14"], "legend": { } },
    { "id": "rgb", "label": "Three-date composite", "dates": null, "legend": { } }
  ],
  "story": { "see": "", "physics": "", "lband": "", "people": "" },
  "spots": [ { "id": "lake", "label": "Open lake", "lngLat": [104.1, 12.9] } ],
  "credits": "NISAR data: NASA/JPL-Caltech and ISRO, distributed by ASF DAAC. PROVISIONAL calibration."
}
```

- `bounds` is `[west, south, east, north]` in degrees: the exact edges of the images. All images of one area share these bounds.
- Images are in **Web Mercator (EPSG:3857), north up**, longer side at most 3,000 pixels (never above 4,096: phone graphics limit). WebP with transparency.
- `dates` is ascending. A layer's own `dates` may be shorter than the area's (the change layer skips the first). The app shows nothing for a layer on a date it lacks.
- `gaps` are date ranges with no data by instrument fault. The timeline shades them.
- `legend` is copied unchanged from `layers.json`.

`timeseries.json`:

```json
{
  "dates": ["2026-06-20", "2026-07-02", "2026-07-14"],
  "series": [
    { "spot": "lake", "hh": [-18.2, -21.0, null], "hv": [-27.5, -29.1, null] }
  ],
  "facts": [
    { "label": "Open water", "unit": "km²", "values": [2710, 3350, null] }
  ]
}
```

Values are decibels. `null` means no valid pixels. `facts` is optional; the app shows it only if present.

## The `demo` area

A synthetic area (a dark disc that grows over six dates) exists in both contract A and contract C form, generated by `scripts/ingest/make_fixture.py` (backend step B2). Frontend and detection build against it from hour 1 without waiting for real data. It is removed from `index.json` before submission.
