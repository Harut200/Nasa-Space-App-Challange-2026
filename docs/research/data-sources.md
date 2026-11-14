# Data Sources and Access Notes

Checked against the ASF NISAR Data User Guide on 14 November 2026. Items marked **(verify)** were not confirmed.

## NISAR

NASA–ISRO Synthetic Aperture Radar. L-band (about 24 cm wavelength: passes through vegetation canopy, sensitive to structure, standing water and soil moisture). 12-day repeat. All science data free and open. S-band data is distributed separately by ISRO (Bhoonidhi) and is only a small sample set; we do not use it.

### Where to get it

| Tool | Link | Use |
|---|---|---|
| NISAR Data User Guide | https://nisar-docs.asf.alaska.edu/ | Start here. Sections: `/availability-overview`, `/products-overview`, `/access-overview`, `/asf-search`, `/earthaccess`, `/tutorials-overview` |
| ASF Vertex | https://search.asf.alaska.edu/#/ | Fastest way to see which passes cover an area |
| Earthdata Search | https://search.earthdata.nasa.gov/search | Alternative search |
| Earthdata Login | https://urs.earthdata.nasa.gov/ | Free account, required for every download. Put credentials in `~/.netrc` or `.env` (both are outside version control) |
| `asf_search` | `pip install asf_search` | Scripted search and download |
| NISAR Cookbook | https://asfopensarlab.github.io/NISAR_Cookbook/ | ASF notebooks: search and download GCOV, stream GCOV into xarray, open HDF5 in Python, GCOV time-series example, SAR change-detection methods. Copy from these before writing our own. Individual notebook addresses changed recently — navigate from the front page |

### Calibration tiers

| Tier | Acquired | Released | Notes |
|---|---|---|---|
| VALIDATED | from 21 Oct 2026 | 6 Nov 2026 | Levels 0–2. Fully calibrated. Collection names carry no label |
| PROVISIONAL | 17 Jun – 23 Oct 2026 | 20 Jul 2026 | Levels 0–3. Calibrated; validated at limited sites. **This is our stack** |
| BETA | 17 Oct 2025 – 20 Jan 2026 | 27 Feb 2026 | Levels 1–3, up to 8 passes per track. Not calibrated, known issues |

- Do not difference images from different tiers: processing changes between them look like surface change.
- Permanent data gap: 27 July 2026 22:03 UTC to 10 August 2026 00:55 UTC.
- Earlier data will be reprocessed to VALIDATED during 2027.

### Products

| Product | Level | What it is | Spacing | For us |
|---|---|---|---|---|
| **GCOV** | 2 | Terrain-corrected backscatter (polarimetric covariance), on a map grid. Includes mask, number of looks, gamma-to-sigma factor | 10, 20 or 80 m | **Primary** |
| GUNW | 2 | Geocoded unwrapped interferogram, with coherence and ionosphere layers | 80 m (wrapped layer 20 m) | Stretch: deformation |
| SME2 | 3 | Soil moisture on EASE-Grid 2.0, three estimates plus uncertainty. Provisional only | 200 m | Optional context for Armenia |
| GSLC | 2 | Geocoded complex images | 5–40 m | Skip |
| GOFF | 2 | Pixel offsets, mainly ice motion | 80 m | Skip unless we add a glacier |
| RSLC, RIFG, RUNW, ROFF | 1 | Radar-geometry products, not map-ready | — | Skip |

All HDF5. Level-2 projection varies by location (UTM or polar stereographic), so reproject before putting anything on a web map.

### Reading GCOV

- Expected paths **(verify on a real file)**: `/science/LSAR/GCOV/grids/frequencyA/HHHH`, `.../HVHV`, with `xCoordinates`, `yCoordinates` and `projection` in the same group. Metadata such as incidence angle sits under `/science/LSAR/GCOV/metadata/radarGrid/`.
- Open with `h5py`, or with xarray: `xr.open_dataset(path, engine="h5netcdf", group="/science/LSAR/GCOV/grids/frequencyA", chunks={})`. The file is hierarchical, so xarray always needs the `group` argument.
- Files can be streamed over HTTPS with fsspec and Earthdata credentials, reading only the window we need. Direct S3 access works only from AWS us-west-2.
- Values are linear power. Decibels: `10 * np.log10(x)`. Mask zeros and invalid pixels first.
- HH is strongest for surface and double-bounce scattering (water edges, flooded vegetation, buildings). HV responds to volume scattering (forest structure, biomass, crops).

## Other radar we may add

Non-U.S.-government sources carry their own terms of use; record them in the README if used.

| Source | Band | Notes |
|---|---|---|
| Sentinel-1 (ESA) | C | Through ASF Vertex and `asf_search`; ready-made terrain-corrected products on demand from ASF HyP3. Record back to 2014 gives the "before NISAR" baseline |
| SAOCOM 1A/1B (CONAE, Argentina) | L | Same band as NISAR. Free registration ("ONE CLICK" profile); link on the challenge Resources tab |
| RADARSAT Constellation Mission (CSA, Canada) | C | Link on the challenge Resources tab |
| RADARSAT-1 (CSA) | C | Open-licensed archive from 1995; long historical baseline |

## Context layers

- Optical imagery: Sentinel-2 or Landsat (Earthdata Search, Copernicus Browser) for a familiar view beside the radar.
- OpenStreetMap for water bodies and place names; ESA WorldCover for land cover; WorldPop for population in the impact story.

## Reading list

- Introduction to SAR: https://www.earthdata.nasa.gov/learn/earth-observation-data-basics/sar
- Introduction to remote sensing: https://earthdata.nasa.gov/learn/earth-observation-data-basics/remote-sensing
- ARSET trainings (includes a 2026 NISAR series, English and Spanish): https://www.earthdata.nasa.gov/data/projects/arset
- NISAR StoryMaps (floods, wildfire, subsidence, oil slicks, glaciers — models for our story panel): https://science.nasa.gov/mission/nisar/arcgis-storymaps/
- NISAR applications white papers (two-page briefs; source for impact statements): https://science.nasa.gov/mission/nisar/societal-benefits/
- UAVSAR postcards (examples of L-band colour composites): https://uavsar.jpl.nasa.gov/education/postcards.html
- CSA tutorial on SAR with Python and QGIS: link on the challenge Resources tab

## Credit line

NISAR data: NASA/JPL-Caltech and ISRO, distributed by the Alaska Satellite Facility DAAC. Place in the app footer, README and demo.
