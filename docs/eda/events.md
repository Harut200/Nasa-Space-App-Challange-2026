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
2026? Answer: **yes, three clear cases in round 1** (a rock-ice collapse and two volcanic eruptions; round 3, below, adds six wildfire scars and one desert storm), all as **loss of coherence with a sharp
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


## Round 3 (15 Nov 2026): pair-based events of more kinds

Same pair method (`scripts/detect/events.py`, GUNW pairs, control pair on the same track and frame ending before the event, background from random discs and from the ground outside the site). New in the script, existing functions and the three round-1 events unchanged:
views of one event can share downloaded windows (`data_view`, each pair is fetched once and skipped when already local), `insar_id` reuses another study's windows, `app_views` selects the looks listed in `app_events.json`, `fig_ba EVENT` writes `before_after.png`,
`overview` writes the figures under 400 KB. Thresholds are per event in `events.json`: volcanoes keep the round-1 rule (control >= 0.45, event < 0.35, drop >= 0.25); fires and the desert storm use control >= 0.5 (0.35 for Thunder Bay), event < 0.45 (0.25), drop >= 0.3 (0.15), minimum 30 pixels, because a burn lowers coherence to about 0.3, not to zero (a synthetic test covers this).
About 45 candidates were looked up and 40 processed (list below); **seven are clear and exported** (type, pairs, numbers in the table), all with a quiet control pair that ends before the event.

What the fire results have in common, and why they can be read as events: in every case the pair that contains the ignition shows a sharp-edged, irregular patch of lost coherence, the control pair ending before the ignition has none, the pair after the fire shows none (the burned ground is stable again,
coherence recovers within weeks: Thunder Bay 21 Sep-3 Oct, La Mierla 31 Aug-12 Sep, Burgohondo 19-31 Aug, Gironde 22 Aug-3 Sep, Niebla 14-26 Aug), the patch is at the published place, and where two look directions exist they agree. Rain or dew on the ground cannot be excluded by coherence alone;
the Thunder Bay null pair (29 Jun-11 Jul against 17-29 Jun, same recipe) finds 12 km2 in the whole 86 x 83 km window, against 1,540 km2 for the pair containing the fire. Pairs of 24 or 36 days against 12-day controls (Gironde asc, Niebla, Bald Range, Burgohondo desc) are flagged in the stories.

### Cumulative candidate table, round 3 (round 1: table at the top)

| Event (source) | Type, date | Pairs (track/frame) and control | Verdict | What is seen |
|---|---|---|---|---|
| **Thunder Bay 36 fire, Ontario** ([Wikipedia](https://en.wikipedia.org/wiki/Thunder_Bay_36_Fire)) | fire, 12 Jul | desc 11-23 Jul (41/63) and asc 11-23 Jul (47/27) vs 29 Jun-11 Jul | **clear** | 1,540 km2 in the 86 x 83 km window (desc; largest connected 1,253 km2, cut by the window), 0.66 -> 0.13; asc 971 km2 where its swath reaches; autumn pair coherent again; null pair 12 km2 |
| **La Mierla fire, Guadalajara** ([The Local](https://www.thelocal.es/20260720/800-evacuated-as-spains-biggest-wildfire-of-the-year-rages-on)) | fire, 16 Jul | desc 14-26 Jul (95/68) vs 2-14 Jul; asc 7-19 Jul (159/22) vs 25 Jun-7 Jul | **clear** | desc 149 km2 in the window (87 within 12 km, background p95 13.8 km2 of 98 discs), largest 43.6 km2 beside La Mierla village; asc 56 km2 within 12 km (background 0.5) at the same band |
| **Burgohondo fire, Avila** ([eldiario.es](https://www.eldiario.es/133_cc79d0), [El Espanol](https://www.elespanol.com/castilla-y-leon/region/avila/20260726/incendio-burgohondo-hectareas-quemadas-mayor-historia-espana/1003744334616_0.html)) | fire, declared 22 Jul | asc 14-26 Jul (87/22) vs 2-14 Jul; desc 21 Jul-26 Aug (23/68, 36 d) vs 9-21 Jul | **clear** | asc 216 km2 in the window (137 within 15 km vs p95 50 km2), largest 99 km2, 0.81 -> 0.26; desc 196 km2 within 15 km, largest 198 km2; 19-31 Aug pair nothing |
| **Gironde megafire** ([Paris Match/AFP](https://www.parismatch.be/actualites/monde/2026/07/30/megafeu-en-gironde-lincendie-est-stabilise-et-la-meteo-favorable-PI3SAONZT5ELNDYIA5JBABKJAU/)) | fire, 22 Jul | asc 17 Jul-10 Aug (130/24, 24 d) vs 5-17 Jul; desc 14-26 Jul (95/66) vs 2-14 Jul | **clear** | asc 334 km2 in the window, one patch 261 km2 (p95 of 39 discs 40 km2), 0.62 -> 0.16; desc 31 km2 within 12 km (background 0.2) |
| **Niebla fire, Huelva/Seville** ([Infobae](https://www.infobae.com/espana/2026/08/13/el-incendio-de-niebla-huelva-pierde-fuerza-tras-arrasar-31000-hectareas-y-dejar-a-700-vecinos-desalojados/)) | fire, about 6 Aug | desc 23/70 and asc 15/21, both 21 Jul-14 Aug (24 d) vs 9-21 Jul | **clear** | desc 218 km2 in the window, asc 240 km2 (largest 113 km2, background p95 6.6); 14-26 Aug pair 0.6 km2; the straight north edge sits at 37.57-37.61 N in both looks (ground feature, cause not identified) |
| **Bald Range fire, British Columbia** ([Watchers](https://watchers.news/2026/08/09/b-c-declares-provincial-emergency-as-out-of-control-bald-range-wildfire-grows-to-over-10-000-ha-25-000-acres/)) | fire, 7 Aug | asc 25 Jul-18 Aug (77/27, 24 d) vs 13-25 Jul; desc 20 Jul-13 Aug (172/63, 24 d) vs 8-20 Jul | **clear** | asc one patch of 134 km2 (background p95 0.66 km2), 0.78 -> 0.25; desc 45 km2 at the same place |
| **Atacama Desert rain and snow storms, Chile** ([Meteored](https://www.meteored.cl/noticias/actualidad/el-norte-de-chile-recibio-lluvias-historicas-aluvion-deja-enterrada-parte-de-tocopilla.html), [NASA Earth Observatory](https://science.nasa.gov/earth/earth-observatory/rare-widespread-snow-in-the-atacama-desert/)) | storm, rain 17-18 Aug, snow storms in August | desc 26 Jul-19 Aug (97/100, 24 d) vs 2-14 Jul; asc 16-28 Aug (46/166) vs 11-23 Jul; asc 23 Jul-16 Aug vs 11-23 Jul | **clear** | box of 115 x 110 km west of the Salar: mean coherence 0.76 -> 0.41, 5,952 km2 lost (window-limited); next desc pair 19-31 Aug still 0.48; asc 0.82 -> 0.62, 1,004 km2; the asc pair ending 16 Aug (before the rain) shows nothing; a second box inland of Taltal (300 km south, desc 26 Jul-19 Aug) 0.59 -> 0.38, 1,293 km2 lost; box around Tocopilla (desc 26 Jul-19 Aug) 0.45 -> 0.24, 876 km2 within 30 km (72 % of the comparable ground, the loss stops at the crest of the coastal range), asc 16-28 Aug only 0.43 -> 0.38, 92 km2 |
| Hautes Fagnes fire, Belgium ([Paris Match Belgium](https://www.parismatch.be/actualites/environnement/2026/08/16/incendie-dans-les-fagnes-3000-hectares-ravages-bart-de-wever-salue-le-travail-remarquable-des-equipes-mobilisees-FWOBHOF3UVHH3PAEPEF3SCHGEU/)) | fire, 14 Aug | asc 11-23 Aug (144/27) vs 6-18 Jul; desc 24 Jul-17 Aug (66/63, 24 d) vs 12-24 Jul | clear signal, **not exported** (fire class capped by the team) | desc one patch 25.5 km2 at 50.526 N 6.103 E, asc 45 km2 at 50.517 N 6.083 E (reports 2,700-3,000 ha); the wet August lowers coherence everywhere (asc 0.43 vs 0.58) and the small window fits only two random discs |
| Rowe Creek Complex, Oregon ([KPTV](https://www.kptv.com/2026/08/02/thousands-firefighters-battle-growing-rowe-creek-complex-central-oregon/)) | fire, 15 Jul | asc 15-27 Jul (106/24), desc 13-25 Jul (71/66) | signal at the window edge, not exported | 137 km2 (asc) and 116 km2 (desc) at 44.57 N 120.49 W, the south-west corner of the window, so cut; the disc around the published place shows nothing |
| Big Grass fire, Oregon/Idaho | fire, 23 Jul | asc 20 Jul-13 Aug (5/23), desc 23 Jul-16 Aug (42/67) | not detected | 0.2 km2 in the disc in both looks (sagebrush rangeland) |
| Klyuchevskoy / Bezymianny ([Watchers 13-19 Aug](https://watchers.news/2026/08/22/the-weekly-volcanic-activity-report-august-13-19-2026/)) | volcano, from 15-18 Aug | desc 17-29 Aug (60/60) vs 12-24 Jul; asc 11-23 Aug (152/30) vs 6-18 Jul | not detected | patches are drainage lines; the one patch in both looks (1.3 km2 at 55.96 N 160.80 E) is 13 km from the volcanoes and unexplained |
| Krasheninnikov lava field ([Watchers 22-28 Oct](https://watchers.news/2026/10/30/the-weekly-volcanic-activity-report-october-22-28-2026/)) | volcano, ongoing | asc 18-30 Aug (80/29) vs 13-25 Jul: 0.0 km2; asc 5-17 Oct vs 18-30 Aug: 120 km2 in the window | not detected | August pair nothing; October pair loses coherence all over the window (19 % vs 4 % outside, background p95 45 km2): first snow, not an event |
| Nyamulagira lava flows ([Watchers 13-19 Aug](https://watchers.news/2026/08/22/the-weekly-volcanic-activity-report-august-13-19-2026/)) | volcano, Jul-Aug | asc 14-26 Aug (14/1) vs 27 Jun-9 Jul; desc 16-28 Aug (50/89) vs 17-29 Jun | not detected | 6.8 / 1.1 km2 in 8 km (background p95 7.3-7.6 km2); patches differ between the looks, in forest |
| Nevados de Chillan new lava flow ([Watchers 8-14 Oct](https://watchers.news/2026/10/16/the-weekly-volcanic-activity-report-october-8-14-2026/)) | volcano, from 29 Sep | asc 23 Sep-5 Oct (75/159), desc 24 Sep-6 Oct (97/107) | not detected | controls have mean coherence 0.24-0.27 (snow, wet ground): no comparable ground |
| Semeru PDC, 18 Jul ([Watchers 16-22 Jul](https://watchers.news/2026/07/25/the-weekly-volcanic-activity-report-july-16-22-2026/)); Lewotolok flow, 18 Aug; Fuego PDCs, 3-5 Aug | volcano | see `events.json` | not detected | forest: 28-98 km2 of comparable ground, no patch above background |
| Stromboli overflow, 5-6 Oct; Great Sitkin dome growth | volcano | asc and desc 3-15 Oct (43/21, 51/69); desc 29 Aug-10 Sep (59/62) | not detected | window coherence 0.11 (Stromboli: mostly sea), 28 km2 comparable (Great Sitkin) |
| Etna later pairs ([Watchers 13-19 Aug](https://watchers.news/2026/08/22/the-weekly-volcanic-activity-report-august-13-19-2026/)) | volcano, 16-28 Aug | asc 16-28 Aug vs 23 Jul-16 Aug: 0.0 km2 new; desc 16-28 Aug vs 11-23 Jul: the same two patches as before (0.74 + 0.68 km2 at 15.013-15.038 E 37.75 N) | consistent with round 1 | no new decorrelation after 16 Aug; the lava field is in place by 16 Aug (absent 11-23 Jul, present in both 23 Jul-16 Aug and 16-28 Aug); the first (June) pair of the east-flank flow (17-29 Jun, 3 m flank slip reported) shows nothing against a control that is itself after the eruption |
| Sheveluch lava domes | volcano, ongoing | asc 11-23 Sep (80/30) vs 1-13 Jul; desc 10-22 Sep (60/60) vs 30 Jun-12 Jul | marginal, not exported | 6.3 km2 within 6 km (background 0.0, outside 0.4 %), largest 2.8 km2 at 56.634 N 161.269 E, 5 km west of the dome; desc 1.6 km2 at the same place; only 41-48 km2 of comparable ground, place not documented |
| Sakurajima PDCs, 3-10 Aug ([Watchers 6-12 Aug](https://watchers.news/2026/08/15/the-weekly-volcanic-activity-report-august-6-12-2026/)) | volcano | asc 21 Jul-14 Aug (24/18) vs 9-21 Jul | marginal | 2.25 km2 on the south-west and west flank of the island, nothing elsewhere in the window (background 0.0), one look (the descending pair 20 Jul-13 Aug has no valid pixels), 61 km2 comparable ground |
| Kilauea episode 53, 12-13 Aug | volcano | asc 11-23 Aug (151/11) vs 18 Jul-11 Aug (24 d, holds episode 52) | not detected | only 37 km2 of comparable ground in 4 km, 4.0 km2 (background p95 35 km2) |
| Brits tailings: second look | tailings, 13 Aug | asc 23 Aug-4 Sep (143/165) vs 6-18 Jul | stays marginal | the 0.58 km2 patch of 11-23 Aug is not in the later pair (0.52 km2 in 4 km, patches elsewhere); no persistence |
| Ambrym: second look direction | volcano, from 19 Sep | asc 19 Sep-1 Oct (22/169) vs 7-19 Sep | stays marginal | 1.7 km2 in the caldera at 168.131 E 16.228 S, within 2 km of the descending patch, but the largest patch (23.6 km2 at 168.27 E 16.31 S) is elsewhere and the window coherence is 0.20 |
| Hanjia rock-slope failure, 17 Jul ([Landslide Blog](https://eos.org/thelandslideblog/17-july-2026-landslide-at-hanjia-1)) | landslide | asc 9-21 Jul (25/16), desc 13-25 Jul (76/74) | not detected | 51-60 km2 of comparable ground, no patch; the blog gives no size |
| Panama earthquake landslides ([Landslide Blog](https://eos.org/thelandslideblog/9-october-2026-panama-earthquake)) | landslide cluster, 9 Oct | the earthquake study's windows: asc 5-17 Oct (76/5), desc 2-14 Oct (40/85), asc 30 Sep-12 Oct (4/5) | not attributed | 750 km2 / 228 km2 within 60 km of the epicentre (34 % / 12 % of comparable ground vs 5 % / 3 % outside), largest patch 417 km2: this is the whole epicentral zone (strong ground motion, aftershocks, 2,351 mapped landslides in forest) and cannot be split into landslides; not claimed |
| Hurricane Isaias, Florida, 10 Oct; Typhoon Noul, Guangdong, 25-26 Jul; Hurricane Polo, Arizona, New Mexico, Baja California Sur, 28-29 Sep ([weather.com](https://weather.com/2026/10/02/forecast/regional/hurricane-polo-southwest-plains-flash-flooding)) | storm | see `events.json` | not detected | Isaias: 177 km2 in the disc against 137 km2 for a null pair; Noul: loss everywhere (wet season, 21 % inside vs 27 % outside); Polo Arizona 7-9 km2, New Mexico 0-1.6 km2 (a control pair has coherence 0.07), Baja 5-12 km2 (0.3 % inside vs 2.5-6.8 % outside, one 76 km2 patch in one look only) |
| Santa Maria (Santiaguito) dome and PDCs, 7-12 Oct ([Watchers 8-14 Oct](https://watchers.news/2026/10/16/the-weekly-volcanic-activity-report-october-8-14-2026/)) | volcano | asc and desc 4-16 Oct (62/9, 55/81) vs 22 Sep-4 Oct | not detected | 17-18 km2 of comparable ground, no patch above background |
| Gold Ridge open-pit ground collapse, 27 Jul ([InDepth Solomons](https://indepthsolomons.com.sb/gold-ridge-pit-collapse-sparks-emergency-response/)) | mine | asc 18 Jul-11 Aug (152/173, 24 d), desc 19 Jul-12 Aug (160/93, 24 d) | not detected | tropical, 23-29 km2 of comparable ground; size of the collapse not published |
| Hurricane Polo, Sonora (second landfall, 29 Sep) | storm | desc 27 Sep-9 Oct (128/74), asc 23 Sep-5 Oct (77/16) | not detected | 0.7 % / 2.8 % of comparable ground inside against 0.3 % / 1.7 % outside |
| Atacama coast and Taltal boxes ([Meteored](https://www.meteored.cl/noticias/actualidad/el-norte-de-chile-recibio-lluvias-historicas-aluvion-deja-enterrada-parte-de-tocopilla.html)) | storm (same event as the clear Atacama row) | desc 26 Jul-19 Aug (97/100, 97/101) | supports the clear row, not exported | see the Atacama row |
| Mahanadi, Terai and Chao Phraya floods | flood | asc 21 Jul-26 Aug (26/12), asc 19 Sep-13 Oct (26/15), desc 18 Sep-12 Oct (4/81) | not detected | monsoon: Mahanadi window coherence 0.07-0.08 in control and event; Terai 7 % inside vs 9 % outside, Chao Phraya 16 % vs 16 % |

### The seven clear events

* **Thunder Bay 36 fire, 12 July (fire; Ontario, Canada).** Wikipedia: started 12 Jul 2026 in Wabakimi Provincial Park, 314,000 ha as of 29 Jul, largest in Ontario's recorded history. Descending 11-23 Jul against 29 Jun-11 Jul (the control ends the day before ignition): 1,540 km2 of lost coherence in the 86 x 83 km window, one connected patch of 1,253 km2 at 50.65 N 89.29 W (14 km from the published point, window-limited), control 0.66 -> event 0.13;
  ascending 971 km2 (686 km2 patch). Autumn pair (21 Sep-3 Oct) 0.70 mean coherence, no loss. Pitch sentence: "A forest fire the size of a small country, drawn by the radar as a hole in the picture that was not there the fortnight before." Files: `areas/thunder-bay-36-coh-2026/`, `data/processed/thunder-bay-36-coh-2026/products/`, `docs/eda/events/thunder-bay-36-coh-2026/`.
* **La Mierla fire, 16 July (fire; Guadalajara, Spain).** The Local: began 16 Jul, over 32,000 ha, second biggest in Spain this century, largest in Castilla-La Mancha. Numbers in the table; the band runs NNE from La Mierla village (Wikipedia 40.94 N 3.24 W); both looks agree. Pitch: "Spain's second-biggest fire this century as a dark band beside the village it is named after." Files under `la-mierla-coh-2026`.
* **Burgohondo fire, 22 July (fire; Avila, Spain).** eldiario.es: declared Wednesday 22 Jul in Burgohondo; El Espanol: about 50,000 ha on 26 Jul, "the largest forest fire in recent history in our country", managed with fronts in Madrid and Toledo. Numbers in the table; both looks agree on the place east of Navaluenga (40.41 N 4.71 W). Pitch: "The largest forest fire in Spain's recent history, a 100 km2 hole in the L-band picture." Files under `burgohondo-coh-2026`.
* **Gironde megafire, 22 July (fire; France).** AFP via Paris Match Belgium: 42,000 ha on 30 Jul; a search summary of AFP/Reporterre dates the start 22 Jul at Saumos. The 24-day ascending pair holds the whole fire (flagged); desc 12-day pair sees 31 km2 at the same forest. Pitch: "A 42,000-hectare pine-forest fire, 260 km2 of it in one patch on the radar." Files under `gironde-coh-2026`.
* **Niebla fire, about 6 August (fire; Huelva and Seville, Spain).** Infobae: declared "last Thursday" (probably 6 Aug), more than 31,000 ha, perimeter about 120 km on 13 Aug. Two looks agree. Pitch: "A sharp-edged block of burned hill country north-east of Niebla, seen from two sides." Files under `niebla-fire-2026`. The 25 km straight north edge: see the table (ground feature, same latitude in both looks).
* **Bald Range fire, 7 August (fire; British Columbia, Canada).** Watchers: first reported on the evening of 7 Aug, about 10,341 ha on 9 Aug, state of emergency. asc one compact patch of 134 km2 (background p95 0.66 km2), desc 45 km2 at the same place. Pitch: "The cleanest of the fire scars: one 134 km2 patch against nothing else in the whole map." Files under `bald-range-fire-2026`.
* **Atacama Desert storms (storm; Chile; new continent and kind).** Meteored: on 17-18 Aug a cut-off low gave more than 37 mm in two days at Tocopilla and 33 mm at Taltal, record daily rain at Arica and Iquique, and a mudflow in Tocopilla on 18 Aug; NASA Earth Observatory: rare snow in August from the Andes to near the coast. In the 115 x 110 km box of desert west of the Salar de Atacama the mean coherence fell from 0.76 (dry control 2-14 Jul) to 0.41 in the pair 26 Jul-19 Aug, 5,952 km2 lost
  (largest connected patch 3,806 km2, cut by the box); ascending 16-28 Aug 0.82 -> 0.62; the ascending pair that ends on 16 Aug shows nothing, so the change is bracketed to 16-19 Aug. We cannot separate wet soil, snow and flood deposits. One alternative control (14-26 Jul) is itself dark over part of the desert (unexplained), so the control used is 2-14 Jul. Timeline of the box (`docs/eda/events/atacama-west-2026/timeline.png`): 0.83 (20 Jun-2 Jul), 0.76 (2-14 Jul, control), 0.41 (26 Jul-19 Aug), 0.48 (19-31 Aug), 0.77 (31 Aug-12 Sep), 0.80 (12-24 Sep), 0.77 (24 Sep-6 Oct): the loss lasted about three weeks. Pitch: "After the rain of 17-18 August the hyper-arid Atacama stopped repeating itself: a desert the size of a country lost its radar fingerprint in three days." Files under `atacama-west-2026`.

### Lessons

* Fires work for this method (forest, shrub and heath, dry or temperate) as **change inside the pair that contains the ignition**, not as a persistent scar. The control must end before the ignition and the pair must contain it; a pair that starts after the fire shows nothing (all five post-fire pairs tried).
* Sagebrush rangeland (Big Grass) shows nothing; a window must be centred on the fire (Rowe Creek's patch sits at the window corner).
* Tropical and monsoon vegetation (Semeru, Lewotolok, Fuego, Nyamulagira, Hanjia, floods in India and Thailand) and snow (Chillan, Krasheninnikov in October) leave no comparable ground or a background as large as any event.
* Weather events in hyper-arid ground are visible (Atacama); in semi-arid ground with shrubs not (Arizona, New Mexico, Baja California Sur).

## What round 3 cannot say (additions to the lists above)

**May say:** NISAR's radar saw about 130-1,500 km2 of forest and scrub change completely during the pair that holds each of six named wildfires, at the published place, with a quiet control pair; and it saw about 6,000 km2 of the Atacama Desert lose its coherence in the three days around the 17-18 August rain.
**Do not claim:** burned area (only ground that was coherent before is counted, windows cut some patches), burn severity, fire dates or spread inside a pair, that rain or dew is excluded (it is not), that the Atacama loss is snow, rain or mud (not separable), anything about people or damage, volumes, lava vs ash, that a pair of 24 or 36 days compares exactly with a 12-day control (flagged), that the straight north edge of the Niebla patch is a fire edge (it is a ground feature of unknown cause).

## Limits of round 3

Windows are 40-110 km, so large fires are cut (Thunder Bay, Atacama, Rowe Creek). One control per view; no second control except the Thunder Bay null pair. Pair lengths differ (24 and 36 days for some event pairs). GUNW products are PROVISIONAL. The ascending/descending looks of one fire do not always cover the same ground. NISAR has no GUNW at Petermann Glacier (81 N), none before 17 Jun (the Palu M6.7 and Qaidam M6.3 earthquakes of 16 Jun fall before the first pair), and none spanning the Hotan (Taklamakan) record rain of 19 Jun.
No glacier, dam, open-pit or coastal-change event with a source inside the window and a pair across it was found (searched: surges, calving, outburst floods, dam failures, mine collapses; see the "ran out of time" list); the earthquakes with M >= 6 on land that were not tried are deep (Colombia M7.4 at 116 km, Peru M6.7 at 99 km, Sumatra M6.9 at 175 km) or offshore, so `docs/eda/quake.md` is unchanged.

*Data credit: NISAR L2 GUNW (PROVISIONAL and urgent response), NASA/JPL-Caltech and ISRO, distributed by the ASF DAAC. Event facts from the pages linked above, opened on 14-15 Nov 2026 (search-result summaries are marked as such).*
