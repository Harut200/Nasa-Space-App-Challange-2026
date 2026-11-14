# Data Sources and Access

For Harutyun. Coverage figures come from the ASF search service on 14 November 2026; file layout and code come from ASF's NISAR Cookbook notebooks and the NISAR Data User Guide. Nothing here has been run against a real file yet — the first task is to confirm it.

## 1. What exists

NISAR L-band, 12-day repeat, free. Product: Level-2 **GCOV** — terrain-corrected backscatter on a map grid, HDF5, one file per frame of about 240 × 240 km.

| Collection | Acquired | Use |
|---|---|---|
| `NISAR_L2_GCOV_PROVISIONAL_V1` | 17 Jun – 18 Oct 2026 | **Our stack.** Calibrated |
| `NISAR_L2_GCOV_V1` (validated) | from 21 Oct 2026 | One or two passes per track so far |
| `NISAR_L2_GCOV_BETA_V1` | Oct 2025 – Jan 2026 | Uncalibrated. Do not difference against the others |
| `NISAR_UR_L2` | — | Urgent-response duplicates. Exclude |

No acquisitions between 27 July and 10 August 2026 (instrument gap). On every track the pass after late July is 17–19 August.

## 2. Coverage by place

All five places have ten provisional passes on at least one track and frame.

| Place | Track / frame / direction | Provisional dates (2026) | Channels | File size |
|---|---|---|---|---|
| **Armenia** (valley and Sevan in one frame) | 85 / 22 / ascending | 06-20, 07-02, 07-14, 07-26, 08-19, 08-31, 09-12, 09-24, 10-06, 10-18 | HH+HV on all | 7.4–7.6 GB |
| Ararat Valley, lighter but partial | 13 / 22 / ascending | 9 dates | HH+HV | 2.0 GB — frame cuts off the east of the valley box |
| Sevan, opposite look direction, partial | 93 / 68 / descending | same 10 dates as 85 / 22 | HH+HV | about 7.4 GB — frame cuts off the west of the lake box |
| **Tonle Sap** | 83 / 8 / ascending | 06-19, 07-01, 07-13, 07-25, 08-18, 08-30, 09-11, 09-23, 10-05, 10-17 | HH+HV on all | about 1.9 GB |
| Tonle Sap, alternative | 148 / 82 / descending | 06-24 … 10-10 (9 provisional) | some HH only | about 1.7 GB |
| **Rondônia** | 60 / 173 / ascending | 10 dates, 06-18 … 10-16 | HH+HV except 09-22 | about 1.9 GB |
| Rondônia, alternative | 68 / 94 / descending | 10 dates | alternates HH only and HH+HV | about 1.9 GB |
| Bangladesh haor | 69 / 14 / ascending | 06-18, 06-30, 07-12, 07-24, 08-17, 08-29, 09-10, 09-22, 10-04, 10-16 | HH+HV on all | about 6.8 GB |

Boxes checked against frame footprints: the Tonle Sap box (103.6–104.6°E, 12.5–13.4°N) lies fully inside 83 / 8; the Ararat Valley box (44.0–44.8°E, 39.8–40.3°N) and the Sevan box (44.9–45.7°E, 40.1–40.7°N) both lie fully inside 85 / 22. Rondônia: 60 / 173 spans about 63.2–60.4°W, 10.4–7.8°S and 68 / 94 about 64.2–61.5°W, 12.3–9.8°S; choose the box inside one of them.

Notes:

- **Tonle Sap has two files per date** on the same track and frame: one marked Full coverage (narrower bandwidth) and one marked Partial (wider bandwidth, starts about 28 seconds later). Use the Full ones only, so every date has the same mode.
- Armenia track 85 / 22 also has eight beta passes (23 Oct 2025 – 15 Jan 2026) and a validated pass on 30 Oct 2026. Showing them as extra frames is possible; computing change across tiers is not.
- Larger files are the wider-bandwidth mode, which should mean finer pixels. Pixel spacing is not in the search results; read `xCoordinateSpacing` from the file.

## 3. Searching

No login needed to search.

Plain HTTP:

```
https://api.daac.asf.alaska.edu/services/search/param
  ?dataset=NISAR&processingLevel=GCOV
  &dataMaturity=PROVISIONAL&relativeOrbit=85&frame=22&flightDirection=ASCENDING
  &intersectsWith=POLYGON((44.0 39.8,45.7 39.8,45.7 40.7,44.0 40.7,44.0 39.8))
  &start=2026-06-17T00:00:00Z&end=2026-10-24T00:00:00Z
  &output=jsonlite
```

Python (from the ASF cookbook):

```python
import asf_search as asf

opts = asf.ASFSearchOptions(
    dataset=["NISAR"],
    processingLevel=["GCOV"],
    productionConfiguration=["PR"],     # excludes urgent-response copies
    dataMaturity="PROVISIONAL",
    relativeOrbit=85, frame=22, flightDirection="ASCENDING",
    intersectsWith=wkt, start=start, end=end, maxResults=250,
)
results = asf.search(opts=opts)
urls = results.find_urls(extension=".h5", directAccess=False,
                         pattern=r"^(?!.*QA_STATS).*")
```

Useful fields per result: `path` (track), `frame`, `flightDirection`, `startTime`, `wkt` (footprint), `downloadUrl`, `browse[]`, and under `nisar`: `crid`, `mainBandPolarization`, `rangeBandwidth`, `frameCoverage`. If a date appears twice with different `crid`, keep the higher one.

## 4. Preview images — no login

Each result's `browse[]` list includes ready-made pictures: `…LATLON.png` (about 4 MB, already in latitude/longitude), a thumbnail, and per-channel versions (`…NATIVE_A_HHHH.png`, `…NATIVE_A_HVHV.png`). They download without credentials. With the footprint from `wkt` they can be placed on a map directly.

Use: give Arameh real imagery in the first hour, and keep as a fallback layer. They are not calibrated values, so no charts or change maps from them.

NASA Worldview also carries NISAR GCOV colour mosaics (daily and 12-day) — handy for a quick look at any place, not tested as a map layer for our app.

## 5. Inside a GCOV file

```
/science/LSAR/identification/                       listOfFrequencies, track, frame, times
/science/LSAR/GCOV/grids/frequencyA/HHHH            backscatter, linear power, float32
/science/LSAR/GCOV/grids/frequencyA/HVHV
/science/LSAR/GCOV/grids/frequencyA/xCoordinates    1-D, metres
/science/LSAR/GCOV/grids/frequencyA/yCoordinates    1-D, metres, north to south
/science/LSAR/GCOV/grids/frequencyA/xCoordinateSpacing, yCoordinateSpacing
/science/LSAR/GCOV/grids/frequencyA/projection      EPSG code (UTM zone), also in attrs['epsg_code']
/science/LSAR/GCOV/grids/frequencyA/mask            valid / invalid pixels
/science/LSAR/GCOV/grids/frequencyA/numberOfLooks
/science/LSAR/GCOV/grids/frequencyA/rtcGammaToSigmaFactor
/science/LSAR/GCOV/metadata/…                       incidence angle and geometry cubes
```

- `frequencyA` is the main band; `frequencyB` is a narrow side band we ignore.
- Values are gamma-naught in linear power; fill value is NaN. Decibels: `10 * np.log10(x.where(x > 0))`.
- Which channels exist depends on the pass. Check before assuming HV.
- Meaning of the individual mask values was not found; inspect on the first file.

## 6. Reading without downloading whole files

Needs an Earthdata bearer token (Earthdata Login profile → Generate Token). Keep it in an environment variable or `.env`, never in the repository.

```python
import fsspec, xarray as xr, rioxarray

fs = fsspec.filesystem("http",
        headers={"Authorization": f"Bearer {token}"},
        cache_type="background", block_size=8 * 1024 * 1024)

with fs.open(url, "rb") as f:
    ds = xr.open_dataset(f, engine="h5netcdf",
                         group="/science/LSAR/GCOV/grids/frequencyA",
                         phony_dims="access", decode_timedelta=False, chunks="auto")
    epsg = ds.projection.attrs["epsg_code"].item()
    hh = ds.HHHH.rio.write_crs(epsg)
    window = hh.rio.clip_box(minx=44.0, miny=39.8, maxx=44.8, maxy=40.3, crs="EPSG:4326")
    data = window.load()          # must happen inside the with block
```

- ASF notes that reading values over the network is slow and suggests saving each cut-out locally. Save every window (GeoTIFF or Zarr) the moment it is read.
- Direct S3 access is faster but works only from inside AWS us-west-2.
- **Measure one window before committing to this route.**

Alternatives if streaming is too slow:

1. **NASA Harmony** — returns a chosen layer as a cloud-optimised GeoTIFF, optionally cut to a box and reprojected. Needs the login. Untested by us.
2. **Download** — Armenia is ten files of 7.5 GB; Tonle Sap ten of 1.9 GB. Leave running overnight.
3. **Lighter track** — track 13 / 22 at 2 GB per file covers only the western part of the Ararat Valley and none of Sevan; the box would have to shrink to about 44.0–44.5°E.

The team's step-by-step pipeline in `plan/02-backend.md` reads windows with `h5py` directly rather than xarray; both work on the same paths.

## 7. Methods worth copying from the ASF cookbook

Repository: `github.com/ASFOpenSARlab/NISAR_Cookbook`, folder `notebooks/`.

- **Time series** (`NISAR_GCOV_Time_Series_Example`): cut a box, average in linear power, then convert to dB; animate with display limits at the 5th and 95th percentiles. Their example removed two faulty provisional scenes — look at every pass.
- **Change detection** (`NISAR_GCOV_SARChangeDetectionMethods`), on HV:
  - pair of dates: `10 * log10(later / earlier)`, flag below −2 dB for losses;
  - whole series, per pixel: 95th minus 5th percentile, standard deviation, or coefficient of variation (standard deviation ÷ mean); flag the top 5 % of pixels.
- Search-and-download and search-and-stream notebooks hold the code in sections 3 and 6.

## 8. Environment

Python 3.14 on macOS (Apple silicon) has ready-made builds for everything needed: `asf_search`, `xarray`, `h5netcdf`, `h5py`, `rioxarray`, `rasterio`, `fsspec`, `aiohttp`, `dask`, `numpy`, `scipy`, `matplotlib`, `pyproj`, `shapely`, `Pillow`.

Avoid: the `GDAL` Python package (needs compiling; use `rasterio`), `isce3` and `polsartools` (not needed for GCOV).

A `.venv` folder already exists in the project folder from an interrupted setup; it is ignored by git. Reuse it or delete it.

## 9. Other radar and context data

Partner-agency sources have their own terms of use; list them separately in the submission.

| Source | Band | Note |
|---|---|---|
| Sentinel-1 (ESA) | C | Through ASF; record back to 2014. Good for an L-band versus C-band comparison |
| SAOCOM (CONAE, Argentina) | L | Free registration; link on the challenge Resources tab |
| RADARSAT Constellation, RADARSAT-1 (CSA, Canada) | C | Links on the challenge Resources tab; RADARSAT-1 archive from 1995 is open |

Context: Sentinel-2 optical imagery, ESA WorldCover, JRC Global Surface Water, OpenStreetMap.

## 10. Links

- NISAR Data User Guide: https://nisar-docs.asf.alaska.edu/ (pages `/availability-overview`, `/products-overview`, `/asf-search`, `/earthaccess`, `/harmony`, `/worldview`, `/aws-s3-access`)
- ASF search keywords: https://docs.asf.alaska.edu/api/keywords/
- ASF Vertex: https://search.asf.alaska.edu/
- Earthdata Login: https://urs.earthdata.nasa.gov/
- NISAR Cookbook: https://asfopensarlab.github.io/NISAR_Cookbook/

Credit line: NISAR data — NASA/JPL-Caltech and ISRO, distributed by the Alaska Satellite Facility DAAC.
