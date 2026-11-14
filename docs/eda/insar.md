# Radar phase and coherence (GUNW) and soil moisture (SME2): what they add for our areas

    .venv/bin/python -m scripts.detect.insar fetch       # copy the reduced server windows to data/processed/<area>-insar|sm
    .venv/bin/python -m scripts.detect.insar coherence   # Ararat coherence (fig 2, 3)
    .venv/bin/python -m scripts.detect.insar motion      # Ararat ground motion + Sevan control (fig 5, 6, 7)
    .venv/bin/python -m scripts.detect.insar sm          # SME2 soil moisture (fig 8)
    .venv/bin/python -m scripts.detect.insar tonle       # Tonle Sap coherence vs the amplitude flood map (fig 9)
    .venv/bin/python -m scripts.detect.insar layer       # candidate app layer (GeoTIFF)
    .venv/bin/python -m pytest tests/test_insar.py -q    # 10 synthetic tests, < 1 s
    # server (Python 3.9, numpy + h5py + requests): scripts/ingest/remote_products.py extract job.json ... | reduce OUT

Code: `scripts/detect/insar.py` (analysis), `scripts/ingest/remote_products.py` (server: reads windows of remote
HDF5 files over HTTP range requests without downloading them, then shrinks them for the slow link). Every number
below is in `docs/eda/insar/results.json`; the product list is `catalogue.json`; the file layout is `anatomy.txt`.
Data (git-ignored): `data/processed/{ararat,sevan,tonle-sap}-insar/`, `data/processed/{ararat,sevan}-sm/`.

## Summary

| Question | Verdict |
|---|---|
| Do GUNW and SME2 exist for our areas? | **Yes.** Ararat: 47 GUNW over 6 track/frames, 54 SME2; Sevan 33 / 37; Tonle Sap 110 / 133. All PROVISIONAL V1. |
| Does coherence show farm change in the Ararat Valley that amplitude did not? | **Yes.** Valley-floor coherence is low in summer and recovers in autumn; foothills, Yerevan and the Sevan control barely move. Field-sized patches are visible at 40 m. |
| Is ground subsidence detectable in the Ararat Valley? | **Not detected.** All four targets are within noise. The detection limit for a 3-km area is ±15 mm (2σ) of line-of-sight change over 120 days. |
| Does SME2 soil moisture separate irrigated floor from dry foothills, or show the 31 Aug rain? | **No.** The floor reads drier than the foothills on every date. The rain step (+0.012 m³/m³) is no bigger than steps on dry dates. Changes move the whole region together. |
| Tonle Sap: does coherence see the flood onset? | **Confirms, cannot map alone.** Newly flooded pixels from the amplitude map have median coherence 0.12–0.22. But far more dry land also loses coherence in the wet season. |

## 1. What these products are (plain words)

NISAR measures the **phase** of the echo as well as its strength. Phase is the position within the 24.2 cm wave.
Take the same place on two dates 12 days apart and difference the phases: this is an **interferogram**. It gives
two things:

* **Coherence** $|\gamma|$, from 0 to 1, says how similar the echo was on the two dates. Rock, roads and buildings
  stay near 0.7–0.9. Water, growing crops, ploughing, harvest and irrigation scramble the echo and drop it towards
  0.1–0.3. Coherence is a **change detector** that works even when the echo strength (amplitude) is unchanged.
* **Unwrapped phase** $\phi$ (radians) measures how far the ground moved along the radar's line of sight (LOS):

$$d_\mathrm{LOS} = -\frac{\lambda}{4\pi}\,\phi ,\qquad \lambda = c/f_0 = 299\,792\,458 / 1.239\times10^{9}\ \mathrm{Hz} = 0.2420\ \mathrm{m}$$

  One full phase cycle ($2\pi$) is 121 mm of LOS change. Here $d_\mathrm{LOS}>0$ means motion **toward** the
  satellite (uplift-like), and subsidence is negative. The minus sign follows the ISCE3 convention: the
  interferogram is reference × conj(secondary), so positive phase is a range increase. The ASF GUNW page does not
  state the sign (see Limits). Our verdict is "no signal", which does not depend on the sign.

The products we used:

* **L2 GUNW** (geocoded unwrapped interferogram) is one file per 12-day pair, about 2.5 GB, in UTM. It holds
  unwrapped phase, coherence and connected components at 80 m, and a 20 m wrapped-interferogram coherence. It also
  holds an **ionosphere phase screen** with its uncertainty, which is *not* applied, and 3-D lookup cubes for
  troposphere, solid-earth tide, incidence angle and LOS vectors.
* **L3 SME2** (soil moisture) is one file per pass, about 100 MB, on EASE-Grid 2.0 at 200 m. It gives volumetric
  soil moisture (m³/m³) with uncertainty and quality flags. It is a **PROVISIONAL** product.

## 2. What exists for our areas

ASF search API, `dataset=NISAR`, `processingLevel` = GUNW, GOFF, RIFG, RUNW, ROFF, GSLC, SME2, intersecting each
area box, 2026-06-01 to 2026-11-15, run 14 Nov 2026. Full listing: `docs/eda/insar/catalogue.json`. Figure:
`fig1_pair_timeline.png`.

| Area | GUNW pairs (track/frame/direction: n) | SME2 | Other |
|---|---|---|---|
| Ararat Valley | **85/22/A: 9** (20 Jun→2 Jul … 6→18 Oct, 40 MHz); 122/68/D: 8; 13/22/A: 8; 13/21/A: 8; 21/69/D: 8; 21/68/D: 6 (mostly 20 MHz) | 54 (85/22/A: 10) | GOFF, RIFG, RUNW, ROFF: 9 each; GSLC 61 |
| Lake Sevan | 85/22/A: 9; 93/68/D: 9; 157/22/A: 9; 21/68/D: 6 | 37 | 27–28 each; GSLC 41 |
| Tonle Sap | 83/8/A: 18 (9 pairs × 2 bandwidths); 76/82/D: 18; 155/8/A: 17; 148/82/D: 16; others | 133 | GSLC 150 |

* Pairs are consecutive 12-day passes. One longer pair bridges the 27 Jul–10 Aug instrument outage (85/22:
  26 Jul→19 Aug, 24 days).
* GUNW files are 2.3–2.6 GB and SME2 files 0.03–0.14 GB. All are `NISAR_L2_GUNW_PROVISIONAL_V1` /
  `NISAR_L3_SME2_PROVISIONAL_V1`.

**What we extracted.** Windows were read remotely on the team server, about 330 s and 0.5 GB of range requests per
GUNW. We kept the area boxes plus 3 km.

* Track 85/22 ascending, all 9 pairs, for Ararat (80 m, plus the 40 m coherence over the floor near Masis) and for
  Sevan (control, reduced to 160 m).
* SME2 85/22, all 10 dates, for Ararat; 3 dates for Sevan.
* Track 122/68 descending, 8 pairs, for Ararat. It covers only the western 23 % of the window, west of 44.30 E, so
  it was not used.
* Tonle Sap 83/8, 5 coherence pairs around the flood onset, at 160 m.

Before transfer we shrank each window about 10×: coherence to uint8, phase to int16 steps of 0.02 rad (0.4 mm),
smooth screens block-averaged. A test checks this round trip. About 45 MB crossed the link in total.

## 3. Ararat Valley coherence: a change detector for the fields

**Regions** (no land-cover map at 80 m, so the rules below use the Copernicus GLO-90 DEM and boxes):

* valley floor (irrigated plain): DEM < 900 m, excluding the Yerevan box and the fish-farm box. 1,833 km².
* foothills / bedrock: DEM 1,200–2,200 m. 1,353 km².
* Yerevan (city): box 44.47–44.56 E, 40.15–40.21 N. 51 km².
* fish-farm cluster: box 44.53–44.68 E, 39.86–39.95 N. 128 km².
* control: Sevan basin land with DEM > 1,950 m and 90th-percentile coherence > 0.35.

**Mean 12-day coherence per pair (track 85, 80 m)** (`fig3_coherence_series.png`):

| pair | 06-20→07-02 | 07-02→07-14 | 07-14→07-26 | 07-26→08-19 (24 d) | 08-19→08-31 | 08-31→09-12 | 09-12→09-24 | 09-24→10-06 | 10-06→10-18 |
|---|---|---|---|---|---|---|---|---|---|
| valley floor | 0.360 | 0.360 | 0.383 | 0.345 | 0.393 | 0.410 | 0.461 | 0.444 | 0.443 |
| fish farms | 0.395 | 0.405 | 0.428 | 0.374 | 0.457 | 0.473 | 0.509 | 0.488 | 0.482 |
| foothills / bedrock | 0.515 | 0.520 | 0.520 | 0.507 | 0.507 | 0.507 | 0.582 | 0.536 | 0.554 |
| Yerevan | 0.674 | 0.683 | 0.701 | 0.671 | 0.649 | 0.655 | 0.742 | 0.728 | 0.721 |
| Sevan land (control) | 0.512 | 0.520 | 0.510 | 0.462 | 0.511 | 0.486 | 0.557 | 0.526 | 0.537 |
| **share of floor with $\lvert\gamma\rvert$ < 0.3** | **43 %** | **44 %** | 38 % | 48 % | 35 % | 31 % | **22 %** | **23 %** | **25 %** |
| share of foothills with $\lvert\gamma\rvert$ < 0.3 | 12 % | 12 % | 13 % | 15 % | 15 % | 16 % | 9 % | 13 % | 11 % |

Summer is the mean of the first three pairs (20 Jun–26 Jul); autumn is the mean of the last three (12 Sep–18 Oct).

* **valley floor:** summer 0.368, autumn 0.449, change **+0.081**
* **foothills:** summer 0.518, autumn 0.557, change +0.039
* **Yerevan:** summer 0.686, autumn 0.730, change +0.044
* **Sevan land:** change +0.026

Every class rises a little in autumn. That common part is weather and season: the 12–24 Sep pair is high
everywhere. The floor rises **about twice as much** as the rest. The share of the floor that is incoherent
(< 0.3) halves, from 43–44 % to 22–25 %, while the foothills stay at 9–16 %. This is the signature of active
fields: crops growing and irrigated in June–July decorrelate within 12 days, and stubble or bare autumn fields stay
coherent. Amplitude could not see this. The FDR amplitude change test (`docs/eda/stats-change.md`) found only
0.04 km² of significant change on the same floor.

**Is it fields, or noise?** (`fig2_ararat_coherence_maps.png`, bottom right)

* **Spatial structure.** At 40 m, take the autumn-minus-summer coherence map. Its correlation with itself shifted
  by 80 / 160 / 320 m is 0.26 / 0.16 / 0.10. A noise-only difference (pair 2 minus pair 1) gives 0.08 / 0.04 /
  0.02. The excess correlation over 80–320 m is the size of Ararat fields (1–10 ha). The zoom shows rectangular
  blocks.
* **Single pixels.** At 80 m, the patches flagged "lost coherence" in a pair (more than 0.25 below the pixel's own
  autumn level and below 0.45) have a median size of 1 pixel (0.64 ha) and a 90th percentile of 3.2 ha.
* **Area flagged per pair:** 185, 172, 112, 204 km² in the summer pairs against 23–41 km² from mid-September.

**Limits of this result.**

* Single fields are only partly resolved at 80 m. The pixel-level "field X was harvested on date Y" map is noisy;
  use the 40 m layer or areal statistics.
* "Fell from ≥ 0.5 to < 0.3 between consecutive pairs" covers only 0.1–1 % of the floor per step. The change is a
  season-long low, not a sharp one-date event that we can pin to harvest day.
* The region rule is elevation-based, so the floor also contains villages, saline land and fallow ground.

> **Verdict (coherence).** NISAR 12-day coherence shows the agricultural season in the Ararat Valley. In June–July
> 43–44 % of the valley floor is incoherent, against 12 % of the foothills. By late September–October this falls
> to 22–25 %, while foothills, Yerevan and the Sevan control change little. The change has field-sized spatial
> structure at 40 m. This is change that the amplitude previews at 180 m did not show.

## 4. Ground motion in the Ararat Valley

**Steps** (`run_motion`; per pair $k$, then stacked):

1. $\phi_k^{\mathrm{corr}} = \phi_k - \phi^{\mathrm{iono}}_k$, using the ionosphere screen in the file.
2. Mask pixels with $\lvert\gamma\rvert$ < 0.4, connected component 0, or outside the largest component.
3. Convert: $d_k = -\frac{\lambda}{4\pi}\phi_k^{\mathrm{corr}}$, in mm.
4. On *stable ground* (DEM > 1,000 m, or Yerevan), fit $d = a + b\,x + c\,y + e\,h$ ($h$ = height, which absorbs
   the stratified troposphere). Subtract it everywhere.
5. Reference: subtract the median over Yerevan.
6. Stack: $D = \sum_k d_k$ over the chain 20 Jun → 18 Oct, $T$ = 120 days. Pixels must be valid in ≥ 7 of 9 pairs.
7. Targets are 3-km disks on the floor around Masis, Aknalich (44.169 E, 40.144 N), the Ararat–Vedi fish farms and
   Artashat. Each target value is target minus reference.

**Noise analysis** (`fig6_noise_and_stability.png`):

* **Per-pair scatter on stable ground** after correction: **20, 29, 18, 24 mm** for the four June–July pairs and
  **7–9 mm** from mid-August. The summer pairs are dominated by the troposphere over a hot valley and by the
  ionosphere. The ionosphere screen alone spans about 4 rad (≈ 80 mm) across the window, and its stated 1σ
  uncertainty is 0.6–2.2 rad (12–42 mm).
* **Lake Sevan control**, same processing, at 1,900 m: 6–13 mm per pair.
* **Stack scatter on stable ground versus distance from the reference:** 6.8 mm (0–5 km), 10.2 mm (5–10 km),
  12–14 mm (10–45 km).
* **Detection limit for a 3-km target.** We placed 300 random 3-km disks on stable ground 5–45 km from Yerevan.
  The std of their mean is 7.5 mm, so the **2σ limit is ±15 mm of LOS change over 120 days**. The Sevan control
  gives ±24 mm.

**Targets** (mm over 120 days, target minus Yerevan; brackets = with the two other references, north hills and
south-east hills; range = leave-one-pair-out):

| target | main | other refs | leave-one-out range | consistent? |
|---|---|---|---|---|
| Masis | −3.3 | −10.0, −4.8 | −18 … +16 | no |
| Aknalich | −12.0 | −18.7, −13.5 | −48 … +31 | no |
| Ararat–Vedi fish farms | −3.2 | −9.8, −4.7 | −18 … +7 | no |
| Artashat | +0.1 | −6.6, −1.4 | −16 … +17 | no |
| *Sevan pseudo-targets (should be 0)* | −0.9, +10.4 | −26 … +10 | | no |

**Sensitivity checks (`results.json → variants`).** All target values stay between −17 and +7 mm, inside the
±15 mm band, under each of these:

* coherence threshold 0.3 / 0.4 / 0.5;
* no ionosphere correction;
* adding the troposphere and solid-earth-tide cubes;
* no height term;
* a quadratic ramp.

The troposphere and tide cubes made the per-pair scatter *worse*, 22–32 mm against 20–29 mm. Correlation of the
stack with height over stable ground is 0.01, so the height term works.

The cumulative series of 12-day steps (`fig7_ararat_timeseries.png`) jumps by tens of mm in the first, summer
pairs (Aknalich +32 mm in pair 1), then flattens.

**What is known.** Groundwater-driven subsidence in the Ararat Valley is being studied:

* AUA Acopian Center with Lund University: [project page](https://ace.aua.am/projects/groundwater-driven-land-subsidence-in-the-ararat-valley/).
* Lund master's theses using Sentinel-1 InSAR, 2018–2022: [Romano](https://lup.lub.lu.se/student-papers/search/publication/9170815) and [Hafen](https://lup.lub.lu.se/student-papers/search/publication/9178381). Hafen reports "a correlation between water level changes and local subsidence and uplifts".
* EGU 2026 abstract EGU26-3505, ascending and descending Sentinel-1.

None of the pages we could read gives rates in mm/yr. We did not find a peer-reviewed rate or a levelling survey.

> **Verdict (ground motion): not detected.** In four months of NISAR track-85 pairs, no target in the Ararat
> Valley moved by more than the noise: |D| ≤ 12 mm against a ±15 mm (2σ) limit for 3-km areas. No result
> survived dropping single pairs, and the Sevan control shows the same scatter. Equivalently, any subsidence
> over 20 Jun–18 Oct was smaller than about 15 mm in line of sight, i.e. below about 45 mm/yr if steady.
> **What a year would resolve.** The chain sum only feels the atmosphere of the first and last dates, so its
> error does not grow with time. A linear fit through about 30 dates over 12 months, at the autumn noise level
> (about 5 mm per date), gives σ ≈ 3–4 mm/yr. That would detect steady subsidence of **about 7–8 mm/yr or more**
> (2σ), more with both ascending (85, 13) and descending (21) tracks. Those tracks exist in the catalogue.

## 5. Soil moisture (SME2) over the valley

`fig8_soil_moisture.png`. Values are means over the same DEM regions, 200 m, PROVISIONAL.

| date | 06-20 | 07-02 | 07-14 | 07-26 | 08-19 | 08-31 | 09-12 | 09-24 | 10-06 | 10-18 |
|---|---|---|---|---|---|---|---|---|---|---|
| valley floor (< 900 m) | 0.202 | 0.201 | 0.142 | 0.153 | 0.162 | 0.173 | 0.133 | 0.135 | 0.169 | 0.171 |
| foothills (1,200–2,000 m) | 0.220 | 0.219 | 0.157 | 0.161 | 0.174 | 0.189 | 0.145 | 0.148 | 0.205 | 0.190 |

* **Irrigated floor versus dry foothills.** The floor is *drier* than the foothills on all 10 dates (by 0.01–0.035).
  At 200 m the product does not show an irrigation contrast.
* **31 Aug rain.** Gauges recorded 29 mm at Sevan and Hrazdan, but only 0.4 mm at Armavir in the western valley
  (`validation-2026.md`). The step from 19 to 31 Aug is +0.012 (floor), +0.015 (foothills), +0.013 (northern
  foothills) and +0.010 (Sevan land). Dry dates show steps just as large: +0.011 and +0.009. The largest steps
  are −0.06 on 14 Jul, −0.04 on 12 Sep and +0.034 / +0.057 on 6 Oct. All three are dry dates, and both regions
  move together. The 6 Oct jump falls on the pass whose HV channel is anomalous in the amplitude product. SME2
  uses HV, so this may be the same instrument or processing effect; that is our guess, not tested.
* The quality flags show only the DSG algorithm for this frame. Median stated uncertainty is 0.032 m³/m³, larger
  than most date-to-date steps.

> **Verdict (soil moisture).** At 200 m and in this PROVISIONAL version, SME2 does not separate the irrigated
> valley floor from the foothills and does not show the 31 Aug rain above the date-to-date noise. Its changes are
> region-wide. Do not use it in the pitch beyond "NISAR also makes a soil-moisture product; we checked it".

## 6. Tonle Sap: coherence across the flood onset

Track 83/8, 160 m, 5 pairs. Classes come from our amplitude water / flooded-vegetation map
(`fig9_tonle_coherence_vs_amplitude.png`).

| pair | newly open water: median coh | newly flooded veg.: median coh | dry land: median coh | amplitude new flood that lost coherence | coherence loss on amplitude-dry land |
|---|---|---|---|---|---|
| 13 Jul→25 Jul | 0.13 | 0.45 | 0.52 | 74 of 258 km² | 851 km² |
| 25 Jul→18 Aug | 0.12 | 0.15 | 0.39 | 466 of 757 km² | 6,276 km² |
| 18 Aug→30 Aug | 0.12 | 0.22 | 0.49 | 449 of 883 km² | 3,829 km² |

> **Verdict (Tonle Sap).** Coherence agrees with the amplitude flood map: areas that newly flooded have
> coherence 0.12–0.22, against 0.39–0.52 on dry land. But in the wet season 4–14× more dry land loses
> coherence (crops, rain, vegetation). On its own, coherence is a supporting check, not a flood map.

## 7. How to word it in the pitch

* **Say:** "Beyond radar brightness, NISAR's interferometric coherence shows the farming season in Armenia's Ararat
  Valley. In early summer almost half of the irrigated valley floor loses coherence within 12 days, against about
  one in eight hectares on the surrounding hills. By October this falls to a quarter, and the pattern has the size
  of fields. The brightness images at 180 m showed nothing there."
* **Say:** "We also tested NISAR for ground subsidence from groundwater pumping. Over four months nothing exceeds
  our ±15 mm detection limit. With a full year of NISAR passes the same method would detect about 8 mm per year."
* **Do not say:** "NISAR measured subsidence in the Ararat Valley", "we mapped harvest dates per field", or "NISAR
  soil moisture shows irrigation". None of these is supported.

## 8. Limits

* **PROVISIONAL products.** GUNW and SME2 are provisional V1; calibration and algorithms may change.
* **Ionosphere.** At L-band it is the largest term: about 80 mm across the window, with a stated 1σ of 12–42 mm. We
  used the screen in the file; summer pairs remain noisy.
* **Troposphere.** The summer convective troposphere over the valley gives 20–29 mm per pair. The weather-model
  cubes did not help.
* **Few pairs, short span.** 9 pairs and 120 days, one track. Descending 122/68 covers only the western quarter of
  the window; other tracks (13/22 A, 21/68–69 D) were not extracted.
* **Unwrapping.** We used the largest connected component and coherence ≥ 0.4. Errors of $2\pi$ (121 mm) inside
  a component would show as single-pair jumps; the leave-one-out test is designed to catch these.
* **Sign convention.** It is taken from the ISCE3 interferogram definition; the ASF GUNW guide does not state it.
  The verdict does not depend on it.
* **Resolution.** Coherence is 80 m (40 m in a 40 × 30 km zoom); single small fields are blurred. SME2 is 200 m.
* **Region rules** are DEM-based, not a land-cover map.
* **Rain.** Zvartnots METAR has no precipitation values, so the rain facts come from the SYNOP gauges in
  `validation-2026.md`.

## 9. Candidate app layer

`data/processed/ararat-insar/ararat_coherence_summer_autumn.tif` is a 3-band uint8 GeoTIFF, UTM 38N, 80 m, about
2 MB:

* band 1: summer mean coherence × 250;
* band 2: autumn mean coherence × 250;
* band 3: 125 + 250 × (autumn − summer);
* 255 = no data.

Band 3 is the one to show, with a blue colour for "became coherent", meaning active in summer and settled in
autumn. Nothing from ground motion or SME2 is worth a layer.

**Data credit:** NISAR L2 GUNW and L3 SME2 PROVISIONAL products, NASA/JPL-Caltech and ISRO, distributed by the ASF
DAAC. DEM: Copernicus GLO-90 (© DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018, provided under
COPERNICUS by the European Union and ESA). Weather: Armenian Hydromet SYNOP via Ogimet.
