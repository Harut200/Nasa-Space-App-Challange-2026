# Branch `backend`

> This is the plan as written on 14 November 2026, kept as it was. What differs now: the `backend` branch was merged into `main` and deleted on 15 November. The pipeline grew beyond this plan (calibrated data, earthquake and event paths, evidence layers); see `docs/ARCHITECTURE.md`. `PLAN.md`, mentioned below, was removed on 15 November (git history only).

**You own:** `scripts/ingest/`, `scripts/build_area.sh`, `areas/*/area.json`, `requirements.txt`, `src/app/public/data/`, `tests/test_export.py`.
**You deliver:** contract A to detection and contract C to frontend ([00-contracts.md](00-contracts.md)).
**You receive:** contract B from detection.

"Backend" is a batch pipeline run on your laptop. There is no server.

Libraries: `asf_search`, `earthaccess`, `h5py`, `numpy`, `rasterio`, `Pillow`. PLAN.md also lists xarray and rioxarray; h5py plus rasterio do the same job with fewer moving parts, so leave them out unless a cookbook notebook you copy needs them.

## Hours 0–1: access, fixture, coverage gate

**B1. Environment and login.**

```bash
git checkout backend
python3 -m venv .venv && source .venv/bin/activate
pip install asf_search earthaccess h5py numpy scipy rasterio Pillow
pip freeze > requirements.txt
```

Add `.venv/` and `dist/` to `.gitignore`. Create `~/.netrc` (not in the repository):

```
machine urs.earthdata.nasa.gov login YOUR_USER password YOUR_PASSWORD
```

then `chmod 600 ~/.netrc`. Merge `requirements.txt` to `main` straight away so the others can install.

**B2. Fixture (30 minutes, unblocks both other branches).** Write `scripts/ingest/make_fixture.py`: six dates, a 500×500 grid in a UTM zone, bright noisy background with a dark disc whose radius grows. It writes the `demo` area twice:

- contract A form under `data/processed/demo/gamma0/` (plus `areas/demo/area.json`);
- contract C form under `src/app/public/data/demo/` with a `manifest.json`, grey WebPs, and `index.json` listing `demo`.

Commit the contract C output and merge to `main`. Tell the team "fixture is on main".

**B3. Coverage gate for area 1.** In ASF Vertex (https://search.asf.alaska.edu/), draw the Tonle Sap box, dataset NISAR, product GCOV, and look for one track and frame with at least six PROVISIONAL passes in one acquisition mode. Then confirm in code: these parameters are confirmed by the ASF guide:

```python
import asf_search as asf
results = asf.search(
    dataset='NISAR', processingLevel='GCOV', dataMaturity='PROVISIONAL',
    intersectsWith='POLYGON((103.6 12.5, 104.6 12.5, 104.6 13.4, 103.6 13.4, 103.6 12.5))',
    start='2026-06-17', end='2026-10-24',
)
print(len(results)); print(results[0].properties)
```

Read the printed properties to find the exact names for track, frame, flight direction, bandwidth and polarization **(not confirmed: do not guess them)**, then group results by track + frame + direction and count.

Fewer than six in every group → switch to the Bangladesh haor substitute. Hard limit: 30 minutes on this decision.

**Done when:** `areas/tonle-sap/area.json` has `track`, `frame`, `direction` filled in, and the team knows the list of dates.

## Hours 1–3: one real date, end to end

**B4. Open one file and print its tree.** Before writing anything else. Stream it rather than downloading:

```python
import earthaccess, h5py
earthaccess.login()                                   # reads ~/.netrc
fs = earthaccess.get_fsspec_https_session()
f = h5py.File(fs.open(url, mode='rb', block_size=8 * 1024 * 1024, cache_type='blockcache'), 'r')
g = f['/science/LSAR/GCOV/grids/frequencyA']
print(list(g)); print(g['HHHH'].shape, g['HHHH'].chunks, g['xCoordinateSpacing'][()])
```

Expected in that group, as seen in real files: `HHHH`, `HVHV`, `mask`, `numberOfLooks`, `rtcGammaToSigmaFactor`, `projection`, `xCoordinates`, `yCoordinates`, `xCoordinateSpacing`, `yCoordinateSpacing`, `listOfPolarizations`. Check and write down:

- `int(g['projection'][()])` is the EPSG code of the grid.
- `xCoordinates` / `yCoordinates` are one-dimensional **pixel centres**. Is `yCoordinates` descending (north first)?
- The pixel spacing. Do not assume 20 m: one sample file is about 37,000 pixels on a side.
- What no-data looks like in `HHHH` (NaN, zero) and what the `mask` values mean **(not confirmed)**.
- If the first download asks you to accept a data-use agreement, every team member must accept it too.

**B5. `scripts/ingest/extract.py {area}` for one date.**

1. Convert `bbox` to the file's projection; pad by 2 km.
2. Find the row and column range in `yCoordinates` / `xCoordinates` and read only that window: `g['HHHH'][r0:r1, c0:c1]`. **Time this read.** Over about three minutes per polarization → download the files overnight-style in the background instead, or shrink the box.
3. Set non-finite and ≤ 0 values to `NaN`.
4. Build the source transform from pixel centres: left edge = `x[c0] − spacing/2`, top edge = `y[r0] + spacing/2`. Getting this half-pixel wrong shifts every image.
5. Define the area's target grid once (snap the padded box to multiples of `pixel_m`) and save it to `data/processed/{area}/grid.json`. Every later date reuses it.
6. Resample onto the target grid with `rasterio.warp.reproject(..., resampling=Resampling.average)` and write contract A.

**B6. `scripts/ingest/export.py {area}`.** Runs on any contract B folder: develop it against the demo area as soon as detection's first output exists (or colour the fixture grey yourself to start).

1. For each product GeoTIFF: reproject to EPSG:3857 with **nearest** resampling (the pixels are already coloured; other methods smear colours into transparent edges). Use one target grid per area so all images share bounds; cap the longer side at 3,000 pixels.
2. Save with Pillow as WebP, quality 80, keeping alpha.
3. Convert the target grid's corners to degrees → `bounds`.
4. Assemble `manifest.json` from `area.json`, `story.json`, `layers.json` and the dates found on disk. Add the instrument gap `["2026-07-27", "2026-08-10"]` to `gaps` when it falls inside the date range. Copy `timeseries.json`.
5. Rewrite `index.json`.

**B7. One check: `tests/test_export.py`.** Run export on the demo area, then assert that the disc centre, whose longitude and latitude you know from the fixture, lands on the matching pixel of the WebP within one pixel, and that the manifest has every field of contract C. This is the test that catches misaligned overlays.

**Done when (hour-3 checkpoint):** one real Tonle Sap date is on the live URL and lines up with the lake shoreline on the basemap.

## Hours 3–7: the full stack for area 1

**B8. `scripts/ingest/search.py {area}`.** The B3 query filtered to the chosen track, frame, direction and mode; writes `data/raw/{area}/passes.json` (date, URL, granule name). Two granules on the same date → keep one and log it.

**B9. Loop extract over all passes.** Skip dates already on disk so a failed run resumes. Log one line per date with read time and the share of valid pixels; a date under 80% valid goes in the log and is reported to detection.

**B10. `scripts/build_area.sh {area}`**: the whole pipeline in order:

```bash
set -e
python scripts/ingest/search.py "$1"
python scripts/ingest/extract.py "$1"
python scripts/detect/run.py "$1"
python scripts/ingest/export.py "$1"
```

**B11. Hand-off.** Merge to `main`. Copy `data/processed/tonle-sap/gamma0/` to the shared drive for detection (expect a few hundred megabytes), or have them run B8–B9 themselves.

**Done when (hour 7):** all dates of area 1 are on the live URL.

## Hours 7–12

- Re-run export whenever detection merges new products. Check total size of `src/app/public/data/{area}/` stays under about 15 MB; lower WebP quality or image size if not.
- **B12. Area 2 (Amazon frontier).** Coverage gate (B3) → `areas/rondonia/area.json` → `build_area.sh rondonia`. Pick the box together with detection: it must contain clearings that appear between July and October.
- If an area fails the gate and has no substitute: the declared fallback is Sentinel-1 through ASF HyP3. Decide with the team before spending time on it.

## Day 2

- Hours 0–3: area 2 complete on the live URL. Area 3 (Ararat Valley and Lake Sevan): small box, same script.
- Hours 3–5: remove `demo` from `index.json`. Final `build_area.sh` for every area from a clean `data/processed/` to prove the pipeline reproduces.
- After freeze: write the "Reproduce" section of the README ([04-pitch-and-repo.md](04-pitch-and-repo.md)) by following your own steps on a teammate's laptop.

## Known risks on this branch

| Risk | Response |
|---|---|
| Windowed streaming is slow on the venue network | Time it in B5. Fall back to downloading whole files to an external disk, two at a time |
| Passes of one track come in different modes or pixel spacings | B5 step 6 resamples everything to one grid; filter the search to one mode where possible |
| Area spans two frames | Move or shrink the box to fit one frame. Do not mosaic |
| Overlay is offset from the basemap | B7. Usual causes: pixel-centre versus pixel-edge, or exporting in degrees instead of Web Mercator |
