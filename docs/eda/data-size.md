# Data size audit and the optimiser

Everything the web apps load from `src/app/public/data/` (then shared by three app designs, `src/app`, `src/app2` and `src/app3`; only `src/app` remains since 15 November). Measured on 2026-11-14 on the 25 places then in the app (a 26th, qaidam-m56-2026, arrived during the work and was optimised by the same run). The baseline is a copy of the folder taken before the first run; `git` also holds it.

## Headline

* Data folder: **90.0 MB to 65.7 MB (-27 %)** with no change of file names, paths, pixel dimensions or georeferencing.
* Gallery / landing: **76 requests and 4.7 MB** (index + 25 manifests + 25 timeseries + 25 full overlays used as thumbnails) became **28 requests and 0.58 MB** (index, `summary.json`, 25 thumbnails) - 8x fewer bytes. With only `summary.json` (17 kB) and lazily loaded thumbnails it is 1 request before the first thumbnail.
* Open one place on its default layer and play all dates: mean 0.24 MB and max 1.21 MB (tonle-sap-cal) before; mean 0.12 MB and max 0.45 MB after (see the last table; the water layer is a class map, which is where lossless palette pays most).
* What is left is mostly radar speckle (hh, hv: 36 of 66 MB). It is noise and does not compress; the only further lever is fewer or smaller frames (app side or re-export).

## Tables (before = original export, after = optimised)

Total (images + JSON): 90.00 MB before, 65.71 MB after (27 % less). Image files: 687 before / 691 after (the after count includes a place added during the work).

### Per place (MB)

| place | before | after | saved |
|---|---:|---:|---:|
| tonle-sap | 9.48 | 6.58 | 31 % |
| tonle-sap-cal | 25.49 | 18.31 | 28 % |
| inner-niger | 5.10 | 3.52 | 31 % |
| poyang | 3.03 | 2.24 | 26 % |
| sevan | 0.74 | 0.58 | 21 % |
| bangladesh-haor | 3.39 | 2.46 | 28 % |
| okavango | 4.07 | 3.14 | 23 % |
| ararat | 1.10 | 0.90 | 18 % |
| urmia | 0.82 | 0.58 | 29 % |
| sindh-indus | 4.13 | 3.14 | 24 % |
| mekong-delta | 4.11 | 2.88 | 30 % |
| rondonia | 2.32 | 1.74 | 25 % |
| nile-delta | 4.16 | 2.88 | 31 % |
| sudd | 4.07 | 2.79 | 32 % |
| pantanal | 1.99 | 1.51 | 24 % |
| lake-chad | 4.82 | 3.48 | 28 % |
| tibet-m70-2026 | 0.59 | 0.51 | 14 % |
| venezuela-m72-2026 | 0.45 | 0.38 | 17 % |
| kumamoto-m68-2026 | 0.81 | 0.70 | 14 % |
| panama-m77-2026 | 0.19 | 0.16 | 14 % |
| chao-phraya-2026 | 8.09 | 5.68 | 30 % |
| langtang-collapse-2026 | 0.30 | 0.27 | 12 % |
| kilauea-ep55-2026 | 0.12 | 0.10 | 15 % |
| etna-valle-del-bove-2026 | 0.26 | 0.23 | 12 % |
| venezuela-m75-2026 | 0.36 | 0.30 | 16 % |
| qaidam-m56-2026 (new tonight, not in the baseline) | n/a | 0.68 | |

### Per layer type (all places)

| layer | files | before kB | after kB | before kB/MP | after kB/MP |
|---|---:|---:|---:|---:|---:|
| change | 136 | 27141 | 15131 | 299 kB/MP | 167 kB/MP |
| hh | 153 | 23632 | 20273 | 232 kB/MP | 199 kB/MP |
| hv | 153 | 22958 | 19674 | 225 kB/MP | 193 kB/MP |
| water | 153 | 4209 | 1360 | 41 kB/MP | 13 kB/MP |
| rgb | 17 | 2941 | 2440 | 263 kB/MP | 218 kB/MP |
| coherence | 8 | 1698 | 1758 | 265 kB/MP | 239 kB/MP |
| when | 17 | 1432 | 563 | 128 kB/MP | 50 kB/MP |
| classes | 17 | 1413 | 572 | 126 kB/MP | 51 kB/MP |
| fringes | 5 | 819 | 969 | 226 kB/MP | 212 kB/MP |
| coh | 9 | 417 | 352 | 419 kB/MP | 354 kB/MP |
| choreo | 1 | 295 | 245 | 248 kB/MP | 206 kB/MP |
| los_cm | 2 | 139 | 111 | 72 kB/MP | 58 kB/MP |
| sig | 3 | 134 | 57 | 93 kB/MP | 40 kB/MP |
| los_desc_cm | 3 | 107 | 142 | 63 kB/MP | 53 kB/MP |
| los_asc_cm | 3 | 102 | 133 | 60 kB/MP | 50 kB/MP |
| coh_change | 1 | 71 | 39 | 642 kB/MP | 357 kB/MP |
| arrival | 1 | 57 | 48 | 48 kB/MP | 40 kB/MP |
| east_cm | 1 | 47 | 36 | 116 kB/MP | 89 kB/MP |
| up_cm | 1 | 45 | 34 | 110 kB/MP | 85 kB/MP |
| coh_loss | 3 | 7 | 2 | 3 kB/MP | 1 kB/MP |

### Ten heaviest files before

| file | kB | px |
|---|---:|---|
| tonle-sap-cal/change/2026-09-23.webp | 1342 | 1565x1600 |
| tonle-sap-cal/change/2026-10-17.webp | 1269 | 1565x1600 |
| tonle-sap-cal/change/2026-09-11.webp | 1240 | 1565x1600 |
| tonle-sap-cal/change/2026-10-05.webp | 1192 | 1565x1600 |
| tonle-sap-cal/change/2026-08-30.webp | 1038 | 1565x1600 |
| tonle-sap-cal/change/2026-08-18.webp | 1035 | 1565x1600 |
| tonle-sap-cal/rgb.webp | 738 | 1565x1600 |
| tonle-sap-cal/hv/2026-09-23.webp | 698 | 1565x1600 |
| tonle-sap-cal/hh/2026-10-17.webp | 690 | 1565x1600 |
| tonle-sap-cal/hv/2026-10-17.webp | 689 | 1565x1600 |

### Ten heaviest files after

| file | kB | px |
|---|---:|---|
| tonle-sap-cal/change/2026-09-23.webp | 707 | 1565x1600 |
| tonle-sap-cal/change/2026-10-17.webp | 673 | 1565x1600 |
| tonle-sap-cal/change/2026-09-11.webp | 654 | 1565x1600 |
| tonle-sap-cal/change/2026-10-05.webp | 645 | 1565x1600 |
| tonle-sap-cal/rgb.webp | 606 | 1565x1600 |
| tonle-sap-cal/hv/2026-09-23.webp | 606 | 1565x1600 |
| tonle-sap-cal/hv/2026-10-17.webp | 600 | 1565x1600 |
| tonle-sap-cal/hv/2026-10-05.webp | 598 | 1565x1600 |
| tonle-sap-cal/hh/2026-10-17.webp | 595 | 1565x1600 |
| tonle-sap-cal/hv/2026-08-18.webp | 594 | 1565x1600 |

### Open one place on its default layer and play every date (requests, MB)

| place | layer | requests | before | after |
|---|---|---:|---:|---:|
| tonle-sap | water | 12 | 0.62 | 0.20 |
| tonle-sap-cal | water | 12 | 1.21 | 0.45 |
| inner-niger | water | 10 | 0.37 | 0.12 |
| poyang | water | 9 | 0.21 | 0.07 |
| sevan | water | 12 | 0.04 | 0.02 |
| bangladesh-haor | water | 12 | 0.38 | 0.13 |
| okavango | water | 11 | 0.02 | 0.01 |
| ararat | water | 12 | 0.02 | 0.01 |
| urmia | water | 11 | 0.11 | 0.04 |
| sindh-indus | water | 12 | 0.02 | 0.01 |
| mekong-delta | water | 12 | 0.31 | 0.10 |
| rondonia | water | 8 | 0.03 | 0.01 |
| nile-delta | water | 11 | 0.03 | 0.01 |
| sudd | water | 10 | 0.29 | 0.09 |
| pantanal | water | 10 | 0.04 | 0.02 |
| lake-chad | water | 12 | 0.21 | 0.07 |
| tibet-m70-2026 | fringes | 3 | 0.21 | 0.18 |
| venezuela-m72-2026 | fringes | 3 | 0.13 | 0.11 |
| kumamoto-m68-2026 | fringes | 3 | 0.34 | 0.29 |
| panama-m77-2026 | fringes | 3 | 0.06 | 0.06 |
| chao-phraya-2026 | water | 11 | 0.56 | 0.18 |
| langtang-collapse-2026 | coherence | 3 | 0.30 | 0.27 |
| kilauea-ep55-2026 | coherence | 3 | 0.11 | 0.10 |
| etna-valle-del-bove-2026 | coherence | 3 | 0.26 | 0.23 |
| venezuela-m75-2026 | fringes | 3 | 0.12 | 0.10 |

Image dimensions: the largest are 1565x1600 / 1600x1565 (tonle-sap-cal, tonle-sap, chao-phraya) and 1000x1100 for events; none exceeds the 1600 px long side the rule allows, so **nothing was downscaled** and no dimension changed (checked on all files).

## What the original export did

`export.py` writes every overlay as lossy WebP quality 80 (method 4), RGBA, alpha lossless. Consequences measured:

* Class maps (water, when, classes, sig, coh_loss) were lossy: edges blended between classes, thousands of off-palette colours (tonle-sap water: 6,261 unique colours for a 4-colour legend). The lossy file was also 3-5x larger than the same map as a lossless palette.
* `change` layers carry a graded alpha channel (about 180 levels, 22-45 % of pixels non-zero) stored losslessly: that alpha costs more than the colour.
* hh/hv/rgb/coherence/fringes were already near the noise floor at ~200-260 kB per megapixel.

## Encoding rule per layer type (and evidence)

Quality is judged against the file already on disk (generation 2 of a lossy image), PSNR/SSIM computed on RGB where alpha > 0.

| layer type | rule | evidence |
|---|---|---|
| class maps: any layer whose legend is `classes` (water, when, classes, sig, coh_loss) | snap every opaque pixel to the nearest legend colour, then lossless WebP (palette), method 6. For `sig` and `choreo` (the app makes grey pixels transparent by saturation) low-chroma pixels are kept grey, posterised to 16 levels. Alpha untouched. | sevan water 3154 B to 852 B, tonle-sap water 96 kB to 29 kB, tonle-sap-cal classes 336 kB to 152 kB. Verified on all 193 class maps: at least 99.8 % of opaque pixels are exactly a legend colour (the remainder is the intended grey background of sig), alpha-zero set identical. |
| grey radar (hh, hv), colour composites (rgb), ramps (coherence, coh, fringes, arrival), diverging (los_*, east/up, coh_change) | lossy WebP quality 74, method 6, alpha lossless | q80 to q72: -15 % (hh 171 to 141 kB, PSNR 36 dB, SSIM 0.979 vs the q80 file); q65: -25 % but SSIM 0.968/0.955 on fringes. 74 is the knee. Lossless was 2-3x larger. Measured over all files: PSNR min/mean hh 34.4/37.1, hv 35.2/37.4, rgb 33.2/35.2, coherence 34.1/35.6, fringes 30.7/32.0, los_cm 41.2/42.7 dB. |
| `change` (graded alpha) | quality 74, `alpha_quality=70`, method 4 | alpha_quality 100 to 70 halves the file (okavango 172 kB to 100 kB; tonle-sap-cal 1.25 MB to 0.69 MB); alpha error mean 0.8-1.8, max 9 of 255. Method 6 is 70x slower on graded alpha and saves 3 %, so method 4. |
| any file that would not get smaller | left as is | the optimiser never grows a file |

Rejected: palette-quantising or snapping grey radar images (adds banding in smooth ground); lossless for radar (3x larger); downscaling (no file exceeds 1600 px). `exact=True` is not used: RGB under alpha 0 is irrelevant and zeroing it did not help lossy files.

Visible effect: none expected at overlay scale (speckle detail is marginally smoothed at the lowest PSNR layers: `arrival` 27.7 dB, `change` min 26.5 dB, both small noisy-ramp files). Class maps become cleaner than before (no blended border colours). Alpha below ~8/255 may become 0 in `change` layers (ararat coh_change: 3,503 pixels with alpha at most 8 vanished; no transparent pixel became visible).

## Idempotence

Every optimised overlay carries a tiny XMP marker (46 bytes, ignored by browsers). The optimiser skips marked files, so re-running is free and does not compound loss; a fresh `export.py` rewrites the file without the marker and it is optimised again. Thumbnails and `summary.json` are rebuilt deterministically and only written when bytes change (`generated` keeps its value while the places are unchanged).

## Thumbnails

`thumbs/<place>.webp` (480 px wide, quality chosen as the highest of 82..35 that fits 25 kB; if even 35 does not, the image shrinks to 87 % then 75 % of the width) and `thumbs/<place>@2x.webp` (960 px, kept only if at most 70 kB). Total 25 thumbnails: 0.94 MB including 5 @2x files (the others do not fit 70 kB at 960 px or the source is narrower).

* Season places: last-date `water` layer over the last-date `hh` frame darkened to 28 % on a dark navy background (the radar gives the land context, the class colours stay exact).
* Events: `fringes` over dark navy, else `coh_loss` over `coherence`, else the first layer.

## `summary.json` (one request for landing page, globe and gallery)

Key order and rounding are stable. 17 kB for 26 places. `{ generated, categories, places }`:

```
generated   "2026-11-14T20:01Z"  (UTC, changes only when content changes)
categories  [{ id, label, n }]   display order: flood, lake, wetland, land, earthquake, volcano, landslide, then unknown ids Title-cased; empty ones omitted
places[]    {
  id, name,
  kind             "season" | "event"
  category         season: area.json "category" (flood|lake|wetland|land); event: event type; else "other"
  eventType        "earthquake" | "volcano" | "landslide" | ... | null
  source           "preview" | "gamma0" | null      (gamma0 = calibrated)
  centre           [lon, lat]   bounds centre (season) or epicentre/event point (event)
  bounds           [w, s, e, n]
  dates            { count, first, last }  season: acquisition dates; event: count 2, first = earliest "before", last = latest "after"
  defaultLayer     same rule as src/app/src/meta.js (fringes for events if present, water for seasons, else first layer)
  thumb            "thumbs/<id>.webp"       ("@2x" file exists only for some places: try thumbs/<id>@2x.webp as srcset and ignore a 404, or read the file list)
  headline         season: { value, unit, label, date, lo, hi } peak open water (km2) with its date and uncertainty band;
                   places where open water is negligible and a "%" fact exists (Ararat): last value of that % series plus from:{value,date}
                   event: { value (magnitude or null), unit ("Mw"|null), label, date (YYYY-MM-DD), p2p_cm (largest peak-to-peak over views or null), summary (string or null) }
  spark            season: the headline series (open water km2 per date, 1 decimal, null where missing); event: null
  change           season: { first, last, delta, pct, unit } of the same series; event: null
  searchText       lowercase space-separated words: name, id words, kind, source, "calibrated" if gamma0, category id and label words, event type and place
}
```

`category` is also written into every `manifest.json` (`manifest.category`) by `export.py`: area.json category for season places, the event type for events, `"other"` otherwise. The 25 existing manifests were patched in place from `areas/*/area.json` and the event type (same JSON formatting, one added key). `tests/test_pipeline.py` asserted an exact key set for the manifest; it now includes `category`.

## `export.py` hook

Two small changes. (1) Before writing the manifest: `manifest["category"] = (event type for events else cfg.get("category")) or "other"`. (2) In the `__main__` block only, after `export(sys.argv[1])`: `import optimize; optimize.run([sys.argv[1]])` inside try/except (an optimiser failure never fails the export). It is in `__main__` on purpose: `tests/test_pipeline.py` and `make_fixture.py` call `export()` directly and must not write thumbs or `summary.json` for the throw-away `demo` place. `scripts/build_area.sh` and `add_area.py` use the CLI, so they pick it up. Manual full run: `python scripts/ingest/optimize.py` (about 2 minutes warm, 15 on a loaded laptop for the first pass).

## JSON hygiene (left alone)

manifest + timeseries + index total about 0.5 MB of 65 MB, gzip by the CDN (below); the apps and `tests/test_export.py` read them as `indent=2` text written by `export.py` and `conftest` rewrites `index.json` with `indent=2`. Minifying would save about 60 % of 0.5 MB after gzip, not worth the churn. Not touched.

## GitHub Pages: what we get and do not get

* Text (HTML, JS, CSS, JSON) is served with gzip. Treat brotli as not available. WebP is already compressed, so images get nothing.
* No custom headers. Cache-Control is fixed at `max-age=600` with ETag/Last-Modified, so a returning visitor revalidates (304) every 10 minutes; hashed asset names do not get immutable caching. Plan on every file costing a request round trip on first visit.
* HTTP/2 or 3 multiplexing is on, so many small files are fine; large images are not parallel-limited.
* Limits: site 1 GB, soft 100 GB/month bandwidth, 100 MB per file on push (warning at 50 MB). Largest file now is 1.3 MB; no file is near 25 MB. Repo data is 66 MB plus 90 MB if the baseline stayed in git history: well inside limits, but each re-export of changed overlays adds a new blob to history, so avoid re-committing all 690 images repeatedly.

## Three most effective remaining ideas (app side)

1. Landing/gallery: fetch `summary.json` only (not 25 manifests and timeseries) and use `thumbs/<id>.webp` with `srcset` for @2x; `loading="lazy"`; fetch a place's manifest and timeseries when it is opened.
2. Playing dates: preload the next one or two frames of the active layer with `fetch(...).blob()` then `createImageBitmap` (already done for water/transparent layers), start the default layer first with `fetchpriority="high"` and fetch other layers (hv, change, rgb) only when selected; frames are 15-250 kB each, so the whole default layer arrives in well under a second on broadband.
3. Do the pixel work once: `withoutGrey` and `onlyClass` redraw each image to a canvas and re-encode to PNG. Class maps are now exactly the legend colours and palette-sized, so `onlyClass` can match on exact colour (tolerance 0) and sig/choreo grey is exactly 16 levels; consider making the choreo/sig layers pre-transparent at export instead (needs an export change, would cut the client canvas pass and the PNG round trip).

## Reproduce

`python scripts/ingest/optimize.py` (all) or `python scripts/ingest/optimize.py sevan ararat`. Tests: `python -m pytest tests/test_optimize.py -q`.
