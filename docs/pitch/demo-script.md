# Demo script: 30 s recording and 3 min live walkthrough

Prepared Sat 14 Nov 2026, 18:31; earthquake shots added at about 22:00; updated Sun 15 Nov, 00:50 (place count, OPERA answer, limits). Judged at 15:00 on 15 Nov. Numbers are the ones the app shows (`src/app/public/data/<place>/timeseries.json`). Deep link form: `#/<place>?layer=<layer-id>&date=<YYYY-MM-DD>` (parsed in `src/app/src/route.js`, which also accepts `mode=story|explore`, `scene=N`, `cmp=1`, `how=1`). Prefix with the app address: the GitHub Pages address that `.github/workflows/deploy.yml` publishes (not tested from here). Layer ids: `water`, `when`, `hh`, `hv`, `sig`, `change`, `rgb`, `classes` (`src/app/public/data/*/manifest.json`).

The app is `src/app/` (decided on 15 November; it was developed as `src/app3/`, the merged app). Two earlier designs, A (satellite globe) and B (story mode), were removed on 15 November and are in git history only. This script was written when all three existed, so some links below were written for A or B; check each link in the app before the recording. Links below were not run by the writer.

Before recording: rehearse each link once, because I could not run the app. Check the on-screen headline number matches the one below and that the date given exists for that place.

Rules from `docs/research/submission-rules.md`: video 30 s or less, public link, English, team name, member names and challenge shown, nobody under 18 on screen.

## A. 30-second screen recording

| # | Time | On screen | What happens | Spoken (one sentence) | Link |
|---|---|---|---|---|---|
| 1 | 0-3 s | Title card (not the app): "Dancing with the SARs", NASA Space Apps 2026, Arameh Haghverdian, Abel Babayan, Harutyun Kesablyan | Static | "We turned ten passes of NISAR radar into a season you can play." | none |
| 2 | 3-7 s | Tonle Sap, layer `water`, 19 Jun: open water 2,281 km² | Page loads at the first date | "This is Tonle Sap in Cambodia, in June." | `#/tonle-sap?layer=water&date=2026-06-19` |
| 3 | 7-16 s | Same layer; 25 Jul (2,537 km²), then 18 Aug (3,152 km²) with the grey outage 27 Jul to 10 Aug visible on the timeline | Press play, or click 25 Jul then 18 Aug. Pause on the gap. | "The flood grows pass by pass, and the timeline shows where the satellite was off, we did not invent a pass." | `#/tonle-sap?layer=water&date=2026-07-25` then `#/tonle-sap?layer=water&date=2026-08-18` |
| 4 | 16-21 s | Tonle Sap, 17 Oct: 5,046 km² (range 4,196-6,051) | Number counts up | "By October, open water is 5,046 square kilometres, checked against an estimate that uses no radar." | `#/tonle-sap?layer=water&date=2026-10-17` |
| 5 | 21-26 s | Lake Sevan, `water`, 31 Aug: about 1,240 km² | Click the 31 Aug date, then 12 Sep to show the number stays | "Lake Sevan does not change: about 1,240 square kilometres on every pass, the published figure is 1,242." | `#/sevan?layer=water&date=2026-08-31` |
| 6 | 26-30 s | End card: app address, repository link, "Uncalibrated 180 m previews. Checked against Landsat, a NASA altimeter, gauges." | Static | "Previews, checked against independent data, open source." | none |

Note on shot 5: at Sevan the dates are 20 Jun, 2 Jul, 14 Jul, 26 Jul, 19 Aug, 31 Aug, 12 Sep, 24 Sep, 6 Oct, 18 Oct (different from Tonle Sap). 31 Aug and 12 Sep were the "rough-surface" dates (`docs/RESULTS.md`).

Total spoken: about 75 words. Read it aloud once with a timer; cut shot 6 speech first if over 30 s.

## B. 3-minute live walkthrough

| # | Time | On screen | What happens | Spoken | Link |
|---|---|---|---|---|---|
| 1 | 0:00-0:20 | Globe with the places (37 in the data: floods 8, lakes 4, wetlands and deltas 4, farmland 2, wildfires 9, earthquakes 6, volcanoes 2, landslide 1, storm 1) | Slow turn, then click Tonle Sap | "Thirty-seven places on five continents: Asia, Africa, South America, North America and Europe. We start where the flood is." | `#/` |
| 2 | 0:20-0:50 | Tonle Sap, `rgb` (three-date composite) | Show the composite, then switch to water | "Each image is a free NISAR preview: 180 metres a pixel, not calibrated. We say so on screen." | `#/tonle-sap?layer=rgb` |
| 3 | 0:50-1:30 | `water`, play 19 Jun to 25 Jul (2,281 to 2,537 km²), pause at the gap, continue to 18 Aug (3,152 km²) | Play; pause on the outage 27 Jul to 10 Aug | "Between the 25 July and 18 August passes about 309 square kilometres flooded, at roughly 13 a day. We tested this by hiding a real pass: our estimate missed by 82, 'nothing changed' by 161." | `#/tonle-sap?layer=water&date=2026-07-25` then `&date=2026-08-18` |
| 4 | 1:30-1:50 | `water`, 17 Oct, 5,046 km² (4,196-6,051) | Open the curves or number panel | "An estimate from lake level and 38 years of Landsat says 5,421 square kilometres, range 4,198 to 7,222. We are inside it on 7 of 10 passes." | `#/tonle-sap?layer=water&date=2026-10-17` |
| 5 | 1:50-2:05 | `when` (when it changed for good) | Show the colour-by-date map | "This map gives each pixel the date it changed for good. Every pixel has its own dance." | `#/tonle-sap?layer=when` |
| 6 | 2:05-2:25 | Lake Sevan, `water`, 31 Aug then 12 Sep, about 1,240 km² | Click both dates | "Sevan is the control. A naive method thinks it shrank on rough days. Ours holds at about 1,240; the published area is 1,242." | `#/sevan?layer=water&date=2026-08-31` |
| 7 | 2:25-2:50 | Ararat Valley, `hh`, any date: brightness is flat; then slide (see section C) | Show the flat curves, then switch to the slide | "In Ararat, brightness shows no farming. Radar coherence does: in June and July 43 percent of the valley floor loses coherence in 12 days, in October about 23. This layer is not in the app yet." | `#/ararat?layer=hh&date=2026-07-14` |
| 8 | 2:50-3:00 | Limits slide or end card | Static | "Limits: uncalibrated previews, flood size good to about 20 percent, one season. A calibrated run on Tonle Sap fits better, within 5.9 percent. Against NASA's own water product we agree at Sevan and Tonle Sap, and fail at three places." | none |

Shot 7: Ararat spot curves are flat across the season, except 31 Aug on `masis-fields` HH (72.8 against about 66 on other dates, `src/app/public/data/ararat/timeseries.json`). If someone asks, say the 31 Aug rise falls on the rain night seen around Yerevan (`docs/eda/validation-2026.md`) and that we did not test the cause at Ararat.

## B2. The earthquake shot (Tibet M7.0, 22 Oct 2026)

Numbers from `docs/eda/quake.md`: 127 cm peak to peak along the radar line of sight within 45 km; the same-track pair from before (7 to 19 Oct) is 5 cm; one look direction; one colour cycle in the fringe layer is 10 cm. Sources and limits: `docs/pitch/FACTS.md`, claim 11.

| Version when written | Link | On screen | Spoken (about 10 s; in the 3-minute walkthrough it can replace shot 7) |
|---|---|---|---|
| A (removed) | `#/tibet-m70-2026?layer=fringes` | Fringe bands around the epicentre on the satellite globe; use the reveal or before/after bar to compare with the imagery | "On 22 October a magnitude 7.0 earthquake hit Tibet. NISAR's pair spanning it shows the ground moved by about 127 centimetres along the line of sight: each colour band is 10 centimetres. The pair from before is flat." |
| B (removed; its link form still works in the app) | `#/tibet-m70-2026?mode=story&scene=2` | Story scene 2 of the Tibet place (arrow keys step through the scenes) | Same sentence; then press the right arrow once for the next scene |

Say, in this order: what it is (a pair of passes across the earthquake), what we see (127 cm, control 5 cm), what we do not know (no independent check; sign unconfirmed; provisional product). Do not say damage, casualties, fault plane, or that it is validated. Totals if asked: fifteen earthquakes tried, six clear (Tibet, Panama, Venezuela M7.2 and M7.5, which cannot be separated, Kumamoto, Qaidam M5.6), one marginal, eight not detected; "NISAR sees M5 earthquakes" is not supported (of seven small events 1 clear, 1 marginal, 5 nothing). If asked about the events not seen, see `FACTS.md`, question 12.

## B3. Other ground events (optional shot)

Three clear, each as loss of coherence with a sharp outline (not fringes): Langtang Lirung rock-ice collapse (Nepal, 26 Aug, about 8.5 km²), Kilauea episode 55 (Hawaii, 17 Oct, about 10.2 km²), Etna Valle del Bove lava field (Italy, Jul-Aug, about 2.5 km²); three marginal, two not detected (`docs/eda/events.md`, `FACTS.md` claim 13). Say: "The ground there was rearranged, so the two passes cannot be compared." Do not say lava, ash or debris, volumes, the date inside the pair, or motion.

## B4. Judge answers to have ready

- **Does it agree with NASA's own water product?** At Lake Sevan and Tonle Sap our maps agree with NASA's OPERA DSWx-S1 product on 97.5 % of pixels (IoU 0.84). Over 101 place-dates at 15 places the mean IoU is only 0.38; this is agreement between two products, not accuracy, and we do not say we are better (`docs/eda/opera.md`).
- **Where does it fail?** Three places where the comparison with NASA's product shows our map is wrong: Inner Niger (we map about four times OPERA's open water; 98 % of our extra water is dry in OPERA), Lake Chad (we find about half) and Lake Urmia (wet salt flat called water). The deltas, the Pantanal and the Sudd are weak. Our earlier 97-99 % accuracies at some of these places were measured only on pixels where the answer is certain.
- **Did you try other 2026 events?** A Thailand flood (Chao Phraya: open water about 990 to about 1,930 km², 6 to 30 Sep, 82 % of the new water inside the Landsat maximum extent) is in the app; the event hunt has ended: the final log lists 10 clear events, 5 marginal and 31 not detected, of 53 configurations (`docs/eda/events.md`); Sheveluch was exported and then withdrawn (first snow, not an eruption signal); only the clear events are in the app.

## C. The coherence shot (Ararat)

The app data holds Ararat coherence images (`src/app/public/data/ararat/coh/`, `coh_change.webp`), but the finding is a candidate only (`docs/eda/insar.md`, section 9). Check what the app shows before pointing to a link for it. Slide-only alternative:

- **Best figure: `docs/eda/insar/fig3_coherence_series.png`.** One chart, five lines: the valley floor sits lowest in summer and rises in autumn, while the hills and the Sevan control stay nearly flat. It is readable in five seconds. Caveat: the rise is modest (0.368 to 0.449, `docs/eda/insar.md`) and the chart's "wheat harvest (Jun-Jul)" shading is an annotation we did not source. Crop it out or say nothing about wheat.
- Second choice, to show field-sized patterns: `docs/eda/insar/fig2_ararat_coherence_maps.png` (not opened by me; described in `docs/eda/insar.md` as the maps and autumn-minus-summer zoom).
- Do not use: `fig5_ararat_displacement.png` (ground motion, no detection), `fig8_soil_moisture.png` (did not separate irrigated land).

Caption for the slide: "Ararat Valley, NISAR 12-day coherence. Brightness previews showed nothing here."

## D. Things not to say on camera

Subsidence measured; harvest dates per field; soil moisture shows irrigation; a bare count of continents (name them); Inner Niger, Lake Chad or Pantanal as solved; our models or maps beat or are better than NASA's; NISAR sees M5 earthquakes in general; lava, ash or debris for the ground events; preview areas are calibrated; earthquake damage or casualties; fault plane or slip; that the earthquake maps are validated against other data. See `docs/pitch/FACTS.md`.
