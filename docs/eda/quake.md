# Did NISAR capture an earthquake? Yes: the M7.0 Tibet earthquake of 22 Oct 2026

    SERVER=... SSH_KEY=... .venv/bin/python -m scripts.detect.quake fetch    # copy reduced server windows
    .venv/bin/python -m scripts.detect.quake run                             # numbers + figures (docs/eda/quake/)
    .venv/bin/python -m scripts.detect.quake export                          # app layers (stage 3)
    .venv/bin/python -m pytest -q tests/test_quake.py                        # 5 synthetic tests

Numbers: `docs/eda/quake/results.json`. Candidate list: `docs/eda/quake/catalogue.json`. Data (git-ignored):
`data/processed/tibet-m70-2026-insar/`.

## Verdict

**Detected.** NISAR's 12-day pair of 19 to 31 October 2026 (track 112, frame 18, ascending, `NISAR_L2_UR_GUNW`
urgent-response product, PROVISIONAL) spans the USGS M7.0 event (us6000u43g, 22 Oct 14:33 UTC, 32.338 N 93.784 E,
depth 17-18 km, normal faulting). It shows a classic two-lobe "butterfly" of about ten fringes next to the epicentre.
The pre-event pair on the same track and frame (7 to 19 Oct), processed identically, is flat.

| Quantity | Co-seismic 19-31 Oct | Control 7-19 Oct |
|---|---|---|
| LOS change within 45 km of epicentre, 0.5 to 99.5 percentile | **-87 to +40 cm (127 cm peak to peak)** | 4.7 cm range, no pattern |
| Extreme single pixels (least reliable, next to the rupture) | -141 / +59 cm | max abs 6.8 cm |
| Far-field standard deviation (> 60 km, after plane removal) | 1.3 cm | 0.68 cm |
| Local noise (map minus its own 10 km mean, far field) | 0.48 cm | 0.50 cm |
| Area with abs(LOS) > 5 / 10 / 20 cm (window-limited, north part) | 3,300 / 1,500 / 580 km² | not computed (max abs 6.8 cm, scattered) |
| Peak signal / control noise | about 130 | |

Sensitivity of the peak-to-peak (126-127 cm) to the choices: far-field radius 45 or 75 km (126, 127), quadric instead of
plane (126), coherence threshold 0.5 instead of 0.35 (119). All within 7 %.

Figures (`docs/eda/quake/`): `fig_a_fringes.png` (re-wrapped, one colour cycle = 10 cm, over coherence, epicentre
starred), `fig_b_los_cm.png` (LOS cm, diverging), `fig_c_control_cm.png` (control, same scale), `fig_d_profile.png`
(profile along azimuth 130 deg through the epicentre: about -37 cm at the epicentre, a step of about 50 cm within
10 km to +10 cm on the other side), `fig_e_iono_screen.png` (see "Ionosphere trap").

## Stage 1: the candidates (USGS M >= 5.8, depth <= 40 km, 17 Jun to 22 Oct 2026; ASF GUNW query at each epicentre)

50 events; 4 are mid-ocean ridge or remote ocean and were dropped. Full list with every spanning pair:
`catalogue.json`. "Spanning" = reference date <= event date <= secondary date. NISAR made no acquisitions 27 Jul to 10 Aug.

| Rank | Event | M, depth | Setting | Shortest spanning GUNW (track/frame, dir) | Verdict |
|---|---|---|---|---|---|
| **1** | us6000u43g, 189 km ENE of Nagqu, Tibet, 22 Oct | 7.0, 18 km, normal | on land, arid plateau, high coherence | **19-31 Oct, 112/18 A (12 d)**; control 7-19 Oct same track | **processed, detected** |
| 2 | us6000tgb9, Kumamoto, Japan, 28 Jul | 6.8, 8 km, strike-slip | on land, vegetated | 21 Jul-14 Aug, 26/18 A (24 d, bridges the outage); 8 Jul-13 Aug 25/72 D (36 d) | strong signal, long pair, vegetation: not tried |
| 3 | us6000t7zc, E of San Felipe, Venezuela, 24 Jun | 7.2, 20 km, strike-slip | on land, humid | 13-25 Jun 22/7 A; 17-29 Jun 23/83 D (12 d each) | good second case, not tried (a M7.5 offshore the same minute) |
| 4 | us6000u18k, Pitaloza Arriba, Panama, 9 Oct | 7.7, 10-13 km | Pacific coast, probably partly offshore | 2-14 Oct 32/85 D; 5-17 Oct 32/5 A (12 d) | not tried; aftershocks M6.6, 6.1, 6.0 |
| 5 | us6000ti6x, SW of Sarangani, Philippines, 5 Aug | 6.3, 10 km | offshore | 22 Jul-15 Aug (24 d) | marginal, offshore |
| 6 | us6000tkt2, 66 km NNW of Ende, Indonesia, 14 Aug | 7.8, 10 km | sea north of Flores | 12-24 Aug 27/173 A; 13-25 Aug 28/93 D | mostly offshore |
| 7 | us7000t1bu / t1cc, SW of Puerto Madero, Mexico, 17 Jul | 7.3 (22 km), 6.4 | offshore | 12-24 Jul | offshore |
| 8 | us6000t7zq, ENE of Noda, Japan, 24 Jun | 6.9, 34 km | offshore | 21 Jun-3 Jul | offshore, deep |
| - | Vanuatu, Kamchatka, Kermadec, Atka, PNG, New Caledonia events | 5.8-6.7 | offshore / islands | several | not on land; no ground signal expected |

Reasons the others fail or are weaker: offshore epicentres (signal over land is small), pair length of 24-36 days with
vegetation (Kumamoto), or only partly known. The Tibet case was chosen because the plateau is dry and sparse (coherence
0.58 on average), the pair is 12 days, and a control pair exists on the same track and frame.

## Method (how the number was made)

1. Window 93.0-94.6 E, 31.7-33.0 N cut remotely from the two GUNW files on the team server (about 4 min each, 260 MB of
   range requests), reduced to 10 MB per pair (`scripts/ingest/remote_products.py`, unchanged).
2. Unwrapped phase, mask: coherence >= 0.35, connected component 1 (one component covers 98.6 % of the window).
3. **No ionosphere screen subtracted** (see trap below).
4. A straight low-coherence band south of the epicentre (SW-NE, about 10 km wide) has an unwrapping offset of whole
   cycles across it. Everything south of the band plus 16 km, and the SW corner where its edge leaks, is masked
   (27 % of the window). The control pair gets the same mask.
5. Plane fitted only where the pair is valid and more than 60 km from the epicentre, subtracted, and the far-field median
   set to zero. LOS cm = -wavelength / (4 pi) x phase x 100, wavelength 24.39 cm (1.229 GHz, read from the file).
6. LOS geometry from the product: mean incidence angle 42.9 deg; LOS unit vector (east, north) = (0.66, 0.18), pointing
   from satellite toward ground. The satellite is therefore to the west of the target, looking east.

### Ionosphere trap (important for anyone reusing this)

The `ionospherePhaseScreen` in the co-seismic product contains a compact dipole exactly at the epicentre (-19 to -5 rad
within 30 km, against a smooth background; `fig_e_iono_screen.png`). It is a leak of the earthquake displacement
into the split-spectrum estimate. Subtracting the screen changes the peak to peak from 127 to 138 cm and distorts the
near field. In the control pair the screen is smooth and makes no difference (far-field std 0.68 with, 0.69 without).
So for a strongly deforming scene: do not subtract the screen near the source.

## Does it agree with physics? (USGS moment tensor, `us` solution)

* Mw 7.0, scalar moment 3.4e19 N m, depth 17.2 km; nodal planes strike 175 / dip 57 / rake -119 and strike 40 / dip 43 /
  rake -54. Both are normal faults with some strike slip (the finite-fault product gives Mw 6.95).
* Slip of order 2 m over a fault of about 30 x 15 km (M0 = mu x area x slip with mu = 30 GPa) implies a vertical
  displacement of order 1 m and an LOS change of order 0.5-1 m on the hanging wall: our -0.9 to -1.4 m extremes next
  to the rupture and the 40-60 cm on the other side are of that order.
* Pattern: two lobes of opposite sign across a fault trace that runs about NNE-SSW through the decorrelated sliver
  just north-east of the epicentre. A normal fault gives opposite LOS signs on hanging wall and footwall. The USGS epicentre
  lies on the red (away from satellite) side, near the lobe edge.
* With our sign (positive = toward satellite), hanging-wall subsidence reads negative, and the red side is the
  side we expect to be the hanging wall for the west-dipping plane (strike 175). With the opposite sign, the second plane
  (strike 40, dipping south-east) would fit. **One look direction and an unconfirmed sign cannot choose between the planes.**
  The agreement we can claim is: normal-faulting-type two-lobe pattern, the right order of magnitude, centred on the epicentre.

## Limits

* PROVISIONAL, urgent-response (UR) product; calibration and processing may change.
* One look direction (ascending only: the descending track had no post-event pair yet); LOS only, uplift, east-west
  and north-south motion are mixed.
* **Sign convention unconfirmed.** Taken from the ISCE3 interferogram definition (as in `docs/eda/insar.md`).
* Near-fault pixels (within about 10 km of the rupture) have the highest gradients: unwrapping errors of whole cycles
  (12.2 cm) cannot be excluded there, and part of the rupture zone is decorrelated (27 % masked within 15 km). The
  extreme values (-141 cm) are the least reliable; the percentile figures are safer.
* The far field is not proven to be undeformed: a broad halo of about +5 to +10 cm remains at 30-50 km, partly real
  deformation, partly possible atmosphere/ionosphere. We removed only a plane. The far std of 1.3 cm is an upper
  limit on noise only where the map is flat.
* A cross-check against an independent source (a Sentinel-1 or ALOS-2 interferogram, GNSS, or a USGS/other
  finite-fault model forward-modelled to LOS) was not done.
* Pair spans 19 to 31 October: includes any aftershock deformation up to 31 October.
* Area figures are clipped by the window and the masked band, so they are minimums.

## What may and may not be claimed

* **Say:** "NISAR's 12-day pair spanning the M7.0 Tibet earthquake of 22 October 2026 shows the ground moving by more
  than a metre (127 cm peak to peak along the radar line of sight within 45 km), as a two-lobe pattern of about ten
  fringes centred on the USGS epicentre. The same track a fortnight earlier is flat to under 1 cm."
* **Say:** "This is a provisional, one-direction measurement. It is consistent in pattern and size with the USGS normal-faulting
  mechanism."
* **Do not say:** that NISAR measured vertical or horizontal displacement; the fault plane or slip; damage, casualties
  or any impact on people; that our maps are validated against GNSS or Sentinel-1; that the sign (blue = toward
  satellite) is confirmed; the extreme pixel values as results; that this is a forecast or detection of precursors.
* **Do not** show the single-pair map as a time series; the app has one static pair here.

## App layers (stage 3)

`data/processed/tibet-m70-2026/products/`: `los_cm.tif`, `fringes.tif`, `coherence.tif` (RGBA uint8, EPSG:4326,
1000 x 813 px, bbox 93.0-94.6 E, 31.7-33.0 N, about 150 m, alpha 0 outside valid data), `layers.json` (all three
`dated: false`), `timeseries.json` (one date, 2026-10-31, empty `series` and `facts`), `source.json`.
`areas/tibet-m70-2026/area.json` and `story.json` follow the existing shape (four story parts, four spots).
Things the lead must do or decide:

* run `scripts/ingest/export.py` for the new id (not run here); the area has no dated layers, so `dates` in the manifest
  has one entry (2026-10-31) and the timeline/swipe/time-series features do not apply; hide the series panel
  (`series` is empty) and the date compare for this place.
* the legend for `los_cm` uses `"type": "diverging"` with three explicit colours (red, near-white, blue) because the
  middle is opaque in the image; the contract's example has a transparent middle.
* `water_source` and the other area keys of the existing areas are not present.
* `fringes.tif` and `coherence.tif` are larger (1.1-1.3 MB as GeoTIFF) than `los_cm.tif` (0.5 MB); the exporter converts
  to WebP.

## Server leftovers

`~/work2/job_quake.json`, `~/work2/quake/anat_ur.txt`, `~/work2/out/quake_tibet/` (123 MB: full-resolution `.npz` of
both windows plus `small2/`), `~/work2/logs/quake.log`. No process running. Nothing outside `~/work2/`.

*Data credit: NISAR L2 GUNW (UR and PROVISIONAL V1), NASA/JPL-Caltech and ISRO, distributed by the ASF DAAC. Earthquake
parameters: U.S. Geological Survey.*
