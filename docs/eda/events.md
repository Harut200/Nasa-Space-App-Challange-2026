# Non-earthquake ground events seen by NISAR (17 Jun - 31 Oct 2026): volcanoes, landslides, a tailings failure

    export SERVER=user@host SSH_KEY=~/.ssh/key                       # only for launch/fetch, never stored in the repo
    .venv/bin/python -m scripts.detect.events list                   # events in docs/eda/events/events.json
    .venv/bin/python -m scripts.detect.events resolve EVENT          # pair dates -> GUNW granule names (ASF search)
    .venv/bin/python -m scripts.detect.events launch  EVENT [VIEW]   # job to the server (~/work4), starts detached
    .venv/bin/python -m scripts.detect.events fetch   EVENT [VIEW]   # copy the reduced windows (1-5 MB per pair)
    .venv/bin/python -m scripts.detect.events run     EVENT [VIEW]   # numbers + figures -> docs/eda/events/EVENT/, results.json
    .venv/bin/python -m scripts.detect.events export  EVENT          # app layers + areas/EVENT (clear events only)
    .venv/bin/python -m scripts.detect.events overview               # docs/eda/events/overview.png
    .venv/bin/python -m scripts.detect.events app_events             # docs/eda/events/app_events.json (clear events)
    .venv/bin/python -m pytest -q tests/test_events.py               # 8 synthetic tests

Code: `scripts/detect/events.py` (imports the maths of `scripts/detect/quake.py`, which it does not modify).
Config: `docs/eda/events/events.json` (one record per event: site, window, one entry per look direction with the event
pair and a control pair on the same track and frame). Numbers: `docs/eda/events/results.json`. Candidate list with
sources: `docs/eda/events/catalogue.json`. App hand-off: `docs/eda/events/app_events.json`. Data (git-ignored):
`data/processed/<event>-insar/`. All products are NISAR L2 GUNW, PROVISIONAL or urgent-response, 80 m.


## Summary

Question: besides the earthquakes, did NISAR's 12-day GUNW pairs capture other ground events between 17 June and 31 October
2026? Answer: **yes, three clear cases** (a rock-ice collapse and two volcanic eruptions), all as **loss of coherence with a sharp
outline** (the surface was rearranged, so the two passes cannot be compared there), not as fringes. No cm-scale ground
motion from a volcano or landslide stood out above the pair's own noise. Three more are marginal, two are not detected.

Rule (same as the earthquake study): **clear** = a spatially coherent signal at the right place, well above the control pair
processed identically; **marginal** = at the right place but not well above the background; **not detected** = nothing at
the place, or decorrelated. Coherence loss = pixels with coherence >= 0.45 in the control pair and < 0.35 (0.40 for
Langtang) in the event pair with a drop of at least 0.25 (0.30), kept if the connected patch has at least 12 pixels (20 for
Langtang and Etna) at 80 m (0.0064 km2 each). The background is read from the same map outside the site.

| Event (source) | Type, date | NISAR pair(s) (track/frame) and control | Verdict | What is seen |
|---|---|---|---|---|
| **Langtang Lirung rock-ice collapse** ([Wikipedia/USGS/GFZ](https://en.wikipedia.org/wiki/2026_Nepal%E2%80%93Tibet_floods), [ICIMOD](https://www.icimod.org/press-releases/second-major-himalayan-flood-in-seven-weeks-raises-new-questions-about-mountain-risk/)) | landslide, 26 Aug 02:52 UTC | asc 19-31 Aug (98/16), control 14-26 Jul; desc 16-28 Aug (48/74), control 11-23 Jul | **clear** | 8.5 km2 patch at 28.32 N 85.48 E (next largest 1.8 km2); control coherence at those pixels 0.65 -> 0.14; desc 5.0 km2 at the same place |
| **Kilauea episode 55** ([USGS HVO](https://www.usgs.gov/volcanoes/kilauea/recent-eruption)) | volcano, 17 Oct 13:19 UTC | asc 10-22 Oct (151/11), control 28 Sep-10 Oct; desc 17-29 Oct (72/79), control 5-17 Oct | **clear (asc only)** | 10.2 km2 connected patch in and south-west of Halemaumau (8.4 km2 within 4 km; background p95 for 4-km discs 0.44 km2); control 0.75 -> 0.18 |
| **Etna Valle del Bove lava field** ([Euronews](https://www.euronews.com/2026/08/13/etna-eruption-enters-seventh-day-in-phenomenon-unseen-since-the-1970s)) | volcano, from about 6 Aug (pair 23 Jul-16 Aug) | asc 23 Jul-16 Aug (43/21), control 29 Jun-23 Jul; desc 23 Jul-16 Aug (51/69), control 11-23 Jul | **clear (both looks)** | 2.5 km2 strip 5 km long east of the summit craters (asc), 1.2 + 1.0 km2 at the same place (desc); 0.02 % of the comparable ground elsewhere |
| Tailings dam failure near Brits ([Landslide Blog](https://eos.org/thelandslideblog/dikwena-tsf-1), [Daily Maverick](https://www.dailymaverick.co.za/article/2026-08-16-mining-sludge-surge-triggers-power-cuts-and-contamination-fears-after-tailings-dam-collapse)) | tailings, 13 Aug | asc 11-23 Aug (143/165), control 6-18 Jul; desc 21 Jul-14 Aug (21/101), control 9-21 Jul | marginal | 0.58 km2 patch 0.8 km from the published point, same pixel in both looks; field-sized background patches of 0.3-0.5 km2 exist |
| Kilauea episode 54 | volcano, 25 Aug 20:30 UTC | asc 23 Aug-4 Sep (151/11), control 4-16 Sep (after the event) | marginal | 0.3 km2 at the crater (background p95 0.1 km2); the crater floor is already dark in the control |
| Ambrym eruption ([Smithsonian/USGS weekly](https://volcano.si.edu/reports_weekly.cfm/volcano.cfm?vn=357070)) | volcano, from about 19 Sep | desc 18-30 Sep (1/97), control 6-18 Sep | marginal | 1.9 km2 patch in the caldera (3.5x the next), but similar fragments scatter over the island; mean coherence about 0.2 |
| Budhi Gandaki landslide ([Landslide Blog](https://eos.org/thelandslideblog/the-11-october-2026-landslide-and-debris-flow-flood-on-the-budhi-gandaki-river-in-gorkha-district-nepal)) | landslide + dam breach, 11 Oct | asc 6-18 Oct (98/16), control 24 Sep-6 Oct; desc 3-15 Oct (48/74), control 21 Sep-3 Oct | not detected | control mean coherence 0.24-0.30; no patch at the published place; no size published |
| Kali Gandaki landslide at Lete ([Landslide Blog](https://eos.org/thelandslideblog/kaligandaki-river-landslide-1)) | landslide + dam, 27 Sep | asc 19 Sep-1 Oct (26/16), control 14-26 Aug; desc 16-28 Sep (149/74), control 4-16 Sep | not detected (decorrelated) | 31-48 % of the comparable ground loses coherence everywhere in the window |
| Campi Flegrei, Svartsengi (Iceland), Kupreanof (Alaska) uplift | caldera or intrusion inflation, ongoing | not processed | not attempted | published rates are 10 mm/month (INGV), about 13 mm/month (IMO, 4 cm in 3 months), about 12 mm/month (AVO, 10 cm in 8 months): about 4 mm in 12 days, below single-pair noise |
| Krasheninnikov (Kamchatka), eruption since Aug 2025 | volcano, ongoing | not processed | not attempted | no start inside the window and no pre-eruption NISAR pair to serve as control |
| Iceland Reykjanes, Afar (Fentale, Erta Ale) | none found for 17 Jun-31 Oct 2026 | not processed | no event | Icelandic Met Office: no eruption in 2026 up to 10 Nov; the last Afar events found (Hayli Gubbi, 23 Nov 2025) precede the window |

Full list with source URLs and the facts used: `docs/eda/events/catalogue.json`. Numbers for every row: `docs/eda/events/results.json`.
Overview of the three clear events, same colour code: `docs/eda/events/overview.png`.

## What the products can and cannot say

* **Coherence loss** says the radar echo of that ground is no longer comparable between the two passes (new lava, fresh ash or
  debris, rearranged slope) and nothing else. It does not give volume, thickness, flow path, damage or the date inside the pair.
* **Fringes / line-of-sight motion** were computed for every event with a usable unwrapping (`fig_b_los_*.png` for Langtang
  and Kilauea). None is claimed. Langtang ascending: -11 to +4 cm (15 cm peak to peak, 0.5-99.5 percentile) within 20 km of the patch
  against 5.6 cm for its control: about 3 times the control, mostly one red spot east of the site, in steep terrain where atmosphere and
  layover are not separable from motion with one pair, so it is a lead, not a result. Langtang descending: a 1.4-1.5 m unwrapping jump
  in both event and control (141 vs 152 cm), meaningless. Kilauea ascending 9.7 cm against 15.9 cm for the control (descending 7.2 vs 12.0 cm).
* Pair durations are 12 days, except the Etna ascending pair and control (24 days), the Etna and Brits descending event pairs (24 days)
  and Kilauea episode 54 control. A longer pair has a lower baseline coherence; controls of equal length were used where one exists.
* Sign convention of the line of sight is unconfirmed (as in the earthquake study); only the exported layers use it.
* Products are PROVISIONAL or urgent-response; the L-band wavelength is 24.2-24.4 cm.

## Langtang Lirung rock-ice collapse, 26 August 2026 (clear)

**Published:** a seismic signal at 02:52 UTC on 26 Aug 2026 (Ms 5.2 USGS, Mw 5.7 GFZ), analysed as generated by the collapse
itself; epicentre area 28.3 N 85.5 E, source on the north face of Langtang Lirung at roughly 5,200 m (Wikipedia, citing USGS, GFZ and
D. Shugar's imagery work). Estimated 132 million m3 of rock and glacier ice (ICIMOD preliminary synthesis); source zone about 2,000 m
long with 1,150 m of drop (PreventionWeb). A NISAR paper (Remote Sensing 18(21), 3727) studies motion before the event 20 Jun-19 Aug.

**Measured (ascending 98/16, event 19-31 Aug, control 14-26 Jul, 20 km radius around 28.322 N 85.477 E):**

| Quantity | Ascending | Descending (48/74, 16-28 Aug; control 11-23 Jul) |
|---|---|---|
| Largest coherence-loss patch | **8.5 km2** at 28.322 N 85.477 E, 9.6 km long | 5.0 km2 at 28.326 N 85.484 E |
| Next largest patch in the 73 x 61 km window | 1.8 km2 | 2.5 km2 |
| Control coherence / event coherence on the loss pixels | 0.65 / 0.14 | 0.62 / 0.18 |
| Loss within 20 km (all patches) / outside | 10.5 km2; 1.2 % vs 0.26 % of comparable ground | 10.6 km2; 1.9 % vs 0.7 % |
| Mean coherence of the window, control / event | 0.58 / 0.59 | 0.47 / 0.51 |

The coherence image of the event pair also shows a thin dark line running from the patch about 40 km south-west down the valley
(`fig_a_coherence_asc.png`, middle panel); it is 1-3 pixels wide and does not pass the area rule, so it is not counted.

**Agreement:** the patch lies 3-4 km from the published epicentre area and on the north-side of the massif; the two independent
look directions locate it within 1 km of each other. Timing is only bracketed by the pair (collapse on 26 Aug, pair 19-31 Aug; a
control a month earlier is clean). We do not measure volume, the 2 km source length, or the flood path.
Figures: `docs/eda/events/langtang-collapse-2026/fig_a_coherence_{asc,desc}.png`, `fig_b_los_{asc,desc}.png`.
App layers: `data/processed/langtang-collapse-2026/products/` (`coherence`, `coh_loss`), `areas/langtang-collapse-2026/`.

**May say:** NISAR's L-band radar saw about 8 km2 of mountain surface change completely between two passes either side of the
collapse, at the place described by USGS/GFZ, with a clean control. **Do not claim:** deposit volume or area (steep terrain
foreshortens and layovers the north face), flood path, precursory motion (we did not process the earlier pairs), anything about
people.

## Kilauea summit eruption, episode 55, 17 October 2026 (clear, ascending)

**Published (USGS HVO episode table, preliminary):** episode 55 began 17 Oct 03:19 HST (13:19 UTC), paused 09:06, about 6 h, fountains up to
220 m, 7 million m3 of lava, overflows from the north, south and west vents before fountaining. Episode 54: 25 Aug, 150 m, 4.0 million m3;
episode 56: 5 Nov. Episode 51's lava covered about 50 % of the crater floor (Maui Now citing HVO). No coverage map was found.

**Measured:** ascending 10-22 Oct (151/11) holds only this episode; the control 28 Sep-10 Oct is quiet (the crater is already dark in it).
Within 4 km of the crater 8.4 km2 lost coherence (17.8 % of the comparable ground in the disc; 0.48 % elsewhere); the largest
connected patch is **10.2 km2**, 5.8 km long, centred 1.6 km south-west of the crater at 19.393 N 155.295 W (second largest 1.4 km2).
Control / event coherence on the lost pixels 0.75 / 0.18. Random 4-km discs elsewhere: median 0, 95th percentile 0.44 km2.
The descending pair (72/79, 17-29 Oct) is unusable for this: its window loses coherence everywhere (11 % of the comparable ground
outside the site as well as inside), the crater patch is the fifth largest. Line-of-sight motion within 4 km: 9.7 cm against 15.9 cm in
the control, so none is claimed.

**Episode 54 as a check (marginal):** asc 23 Aug-4 Sep, control 4-16 Sep (after the event, flagged `control_after_event`): 0.3 km2 at the crater,
above the background (95th percentile 0.1 km2), far smaller than episode 55. We do not know why; fountain height and volume differ only
by a factor of 1.5-2, the control differs, and tephra fall depends on the wind.

**Agreement:** location (the summit caldera), date (single-episode pair), size order are consistent; the south-west extension is where one
would expect tephra to be carried by the trade winds but we cannot tell lava from fallout. Figures:
`docs/eda/events/kilauea-ep55-2026/`, `kilauea-ep54-2026/`. App layers: `data/processed/kilauea-ep55-2026/products/`, `areas/kilauea-ep55-2026/`.

**May say:** the L-band radar picture of the summit changed completely over about 10 km2 in the pair that contains episode 55
and not in the quiet pair before it. **Do not claim:** lava area, thickness, volume, or that the south-west band is lava.

## Etna eruption, July-August 2026 (clear, both look directions)

**Published:** on 13 Aug 2026 the eruption was in its seventh day, with lava fountains at Voragine and several vents feeding a lava field in
the Valle del Bove (Euronews citing INGV, S. Gambino). Search summaries of INGV and local reports add an effusive phase from late June, a
paroxysm on 30 Jul, a flow 2.7 km long on 23 Jul and about 500,000 m3 in one flow (go-etna.com, vulkane.net; not primary).

**Measured:** ascending 23 Jul-16 Aug (43/21, 24 days) against 29 Jun-23 Jul (24 days): a connected patch of **2.45 km2**, 4.9 km long,
centred 2.8 km east of the summit craters at 15.029 E 37.751 N (next largest 0.3 km2); control 0.68 -> event 0.15. 2.9 km2 within 9 km
of the site and 3.2 km2 in the whole 40 x 44 km window (0.02 % of comparable ground outside the site; 4-km-scale background discs: 0).
Descending 23 Jul-16 Aug (51/69) against 11-23 Jul (12 days): two patches 1.18 and 0.96 km2 at 15.039 E / 15.013 E, 37.75 N (next 0.24 km2).
Older flows already incoherent before 23 Jul are not counted (they are not comparable in the control). **The pair spans the 30 Jul
paroxysm and the August eruption and we cannot separate them.**

**Agreement:** the place (east of the summit, towards the Valle del Bove rim) and a lava surface scrambling the echo agree with the
description; no map of the lava area was found to compare the 2.5 km2 with. Figures: `docs/eda/events/etna-valle-del-bove-2026/`.
App layers: `data/processed/etna-valle-del-bove-2026/products/`, `areas/etna-valle-del-bove-2026/`.

**May say:** about 2.5 km2 east of Etna's summit craters changed completely between 23 July and 16 August, seen from two look directions.
**Do not claim:** lava area (steep slopes hide parts; thin flows may keep coherence), date or sequence of flows, volume, damage.

## Before layer (15 Nov 2026)

Each clear event also exports `coherence_before`, the control pair's coherence ("Before the event: radar coherence (d1-d2)"), on the same grid with the same grey ramp and legend as `coherence`
(`events.export_before`; `python -m scripts.detect.events before EVENT` adds it without touching the other layers). It is exported only if the control pair is not flagged `control_after_event` and ends before the event
(kilauea-ep54 would not get one). What it shows: Langtang and Etna have an intact, textured surface in the control that the event pair loses along the outline of the collapse and the lava field; at Kilauea the crater floor
is already partly dark in the control (28 Sep-10 Oct; cause not examined) and the event pair adds the larger dark patch. Nothing needed a "noisy" label. `app_events.json` carries the pairing
(`before_after`) and each view's `control_before` / `control_after`; figures: `docs/eda/events/<id>/before_after.png` (Kilauea, Langtang).

## Marginal and not detected

* **Brits tailings failure, 13 Aug (marginal).** Failure point published as 25.652 S 27.852 E ("appears to have been", Landslide Blog); Daily
  Maverick and SANSA (Sentinel-2) report sludge spreading toward a neighbouring mine and power lines. Ascending 11-23 Aug (control 6-18 Jul):
  the largest patch is 0.58 km2, 1.84 km long, at 27.844 E 25.649 S (0.8 km from the published point; next 0.27 km2). Descending 21 Jul-14 Aug
  (24 days; control 9-21 Jul, 12 days): 0.50 km2 at the same pixel, but other 0.5 km2 patches elsewhere and far more background loss
  (95th percentile of 6-km discs 2.7 km2). The same position in two look directions is encouraging; the area is within field-scale
  agricultural background, hence marginal. Do not claim sludge extent.
* **Ambrym, from 19 Sep (marginal).** VMGD: a new eruption likely started on 19 Sep (Smithsonian/USGS weekly report). Descending 18-30 Sep (1/97)
  against 6-18 Sep: 1.9 km2 patch at 168.132 E 16.245 S in the caldera, 3.5 times the next, but fragments of similar size scatter over the
  forested island and the mean coherence of the window is 0.2.
* **Budhi Gandaki, 11 Oct (not detected).** The control pair (24 Sep-6 Oct asc, 21 Sep-3 Oct desc) has a mean coherence of 0.24 / 0.30
  (monsoon-end vegetation and wet soil); no patch at the published location (largest patches 0.7-1.4 km2 elsewhere). The landslide has no published size;
  it may be below what 80 m pixels resolve.
* **Kali Gandaki at Lete, 27 Sep (not detected, decorrelated).** Between 31 % and 48 % of the comparable ground loses coherence in the event pairs
  (rain or snow), so nothing stands out; the location is the blog author's speculation.
* **Uplift (Campi Flegrei, Svartsengi, Kupreanof):** published rates are about 10-13 mm per month, below single-pair noise
  (1-3 cm). They need a stack of pairs, not a pair.

## Method

1. Windows (40-70 km) cut remotely from each GUNW at 80 m on the server (`remote_products.py` copy in `~/work4`), unwrapped phase,
   coherence and connected components, reduced to 1-5 MB per pair.
2. Control pair on the same track and frame: 12 days where one exists, else the closest in length (Etna asc, Brits desc, Kilauea control after
   the event are flagged above).
3. Coherence loss as defined above; patches by 8-neighbour connectivity; centroid and long side from the pixel bounding box.
4. Background: coherence-loss fraction on comparable ground outside the site; area in random discs of the site's radius elsewhere
   (when the window allows); next-largest patches.
5. Line-of-sight motion by `quake.process` (far-field plane removed outside `far_km`), 0.5-99.5 percentile within the site radius, with the
   control's number. Not used as evidence anywhere.
6. App layers in EPSG:4326 (`coherence`, `coh_loss`; no `los_cm` or `fringes` because no motion is claimed); story texts are hand-written
   from the published facts above and our numbers.

## Limits

Steep terrain (Langtang): layover and shadow distort areas; mountains hide parts of the scene. Provisional products; pair length differs
for Etna and Brits (24 vs 12 days). Loss of coherence cannot tell lava from tephra, debris from snow melt, or the date inside the pair.
Thresholds (0.45 / 0.35 / 0.25 drop) were set on Langtang and Kilauea and applied unchanged elsewhere (Langtang uses 0.40 / 0.30 to follow
the thin debris track). Of the events found, only those with a NISAR pair spanning the date, a quiet control and bare ground gave a result.

*Data credit: NISAR L2 GUNW (PROVISIONAL and urgent response), NASA/JPL-Caltech and ISRO, distributed by the ASF DAAC. Event facts from the sources linked
above; all facts about an event are cited to those pages, which were read on 14 Nov 2026.*


## Round 3 (15 Nov 2026, in progress): pair-based events of more kinds

Interim log, kept up to date after each candidate. Same rule as above; fires use a lower and wider rule (control >= 0.35-0.5 and event < 0.25-0.45 with a drop of 0.15-0.3, 30 px, set per event in `events.json`).
Several event pairs can share one control (`data_view` in `events.json`); `before_after.png` is made by `python -m scripts.detect.events fig_ba EVENT`.

| Event (source) | Type, date | Pairs (track/frame) and control | Verdict | What is seen |
|---|---|---|---|---|
| **Thunder Bay 36 fire, Ontario** ([Wikipedia](https://en.wikipedia.org/wiki/Thunder_Bay_36_Fire)) | fire, started 12 Jul | desc 11-23 Jul (41/63) vs 29 Jun-11 Jul; asc 11-23 Jul (47/27) same control | **clear** | 1,540 km2 lost coherence in the 86 x 83 km window (largest connected 1,253 km2, cut by the window), 0.66 -> 0.13; asc sees 686 km2 where its swath covers; the same ground is coherent again on 21 Sep-3 Oct |
| **La Mierla fire, Guadalajara** ([The Local](https://www.thelocal.es/20260720/800-evacuated-as-spains-biggest-wildfire-of-the-year-rages-on)) | fire, started 16 Jul | desc 14-26 Jul (95/68) vs 2-14 Jul | **clear** | 149 km2 in the window, 87 km2 within 12 km (95th percentile of 98 equal discs 13.8 km2), largest patch 43.6 km2 next to La Mierla village; coherent again 31 Aug-12 Sep |
| Klyuchevskoy / Bezymianny eruption ([Watchers weekly, 13-19 Aug](https://watchers.news/2026/08/22/the-weekly-volcanic-activity-report-august-13-19-2026/)) | volcano, from 15-18 Aug | desc 17-29 Aug (60/60) vs 12-24 Jul; asc 11-23 Aug (152/30) vs 6-18 Jul | not detected | patches are drainage lines; the one patch in both looks (1.3 km2 at 55.96 N 160.80 E) is 13 km from the volcanoes and unexplained; the cones are dark in the control |
| Krasheninnikov lava field ([Watchers 22-28 Oct](https://watchers.news/2026/10/30/the-weekly-volcanic-activity-report-october-22-28-2026/)) | volcano, ongoing | asc 18-30 Aug (80/29) vs 13-25 Jul: 0.0 km2; asc 5-17 Oct vs 18-30 Aug: 120 km2 in the window | not detected | August pair: nothing; October pair: loss all over the window (19 % vs 4 % outside, background p95 45 km2) is the first snow, not an event |
| Nyamulagira lava flows ([Watchers 13-19 Aug](https://watchers.news/2026/08/22/the-weekly-volcanic-activity-report-august-13-19-2026/)) | volcano, Jul-Aug | asc 14-26 Aug (14/1) vs 27 Jun-9 Jul; desc 16-28 Aug (50/89) vs 17-29 Jun | not detected | 6.8 / 1.1 km2 in 8 km (background p95 7.3-7.6 km2); the patches differ between the looks and are in forest |
| Nevados de Chillan new lava flow ([Watchers 8-14 Oct](https://watchers.news/2026/10/16/the-weekly-volcanic-activity-report-october-8-14-2026/)) | volcano, from 29 Sep | asc 23 Sep-5 Oct (75/159) vs 11-23 Sep; desc 24 Sep-6 Oct (97/107) | not detected | the control pairs have mean coherence 0.24-0.27 (snow, wet ground): no comparable ground |
