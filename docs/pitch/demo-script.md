# Demo script: 30 s recording and 3 min live walkthrough

Prepared Sat 14 Nov 2026, 18:31; earthquake shots added at about 22:00. Numbers are the ones the app shows (`src/app/public/data/<place>/timeseries.json`). Deep link form: `#/<place>?layer=<layer-id>&date=<YYYY-MM-DD>` (`src/app/src/meta.js`). Prefix with the app address from the deck: `https://harut200.github.io/Nasa-Space-App-Challange-2026/` (not tested from here). Layer ids: `water`, `when`, `hh`, `hv`, `sig`, `change`, `rgb`, `classes` (`src/app/public/data/*/manifest.json`).

Two app designs exist on the same data (`src/app/`, design A; `src/app2/`, design B) and the team is choosing one; use only the links of the chosen design. Neither is deployed yet, so no live address is printed here: prefix the links below with the address from the deck once it exists. Links below were not run by the writer.

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
| 1 | 0:00-0:20 | Globe with 15 places | Slow turn, then click Tonle Sap | "Fifteen places, in Asia, Africa and South America, plus Armenia on the Europe-Asia boundary. We start where the flood is." | `#/` |
| 2 | 0:20-0:50 | Tonle Sap, `rgb` (three-date composite) | Show the composite, then switch to water | "Each image is a free NISAR preview: 180 metres a pixel, not calibrated. We say so on screen." | `#/tonle-sap?layer=rgb` |
| 3 | 0:50-1:30 | `water`, play 19 Jun to 25 Jul (2,281 to 2,537 km²), pause at the gap, continue to 18 Aug (3,152 km²) | Play; pause on the outage 27 Jul to 10 Aug | "Between the 25 July and 18 August passes about 309 square kilometres flooded, at roughly 13 a day. We tested this by hiding a real pass: our estimate missed by 82, 'nothing changed' by 161." | `#/tonle-sap?layer=water&date=2026-07-25` then `&date=2026-08-18` |
| 4 | 1:30-1:50 | `water`, 17 Oct, 5,046 km² (4,196-6,051) | Open the curves or number panel | "An estimate from lake level and 38 years of Landsat says 5,421 square kilometres, range 4,198 to 7,222. We are inside it on 7 of 10 passes." | `#/tonle-sap?layer=water&date=2026-10-17` |
| 5 | 1:50-2:05 | `when` (when it changed for good) | Show the colour-by-date map | "This map gives each pixel the date it changed for good. Every pixel has its own dance." | `#/tonle-sap?layer=when` |
| 6 | 2:05-2:25 | Lake Sevan, `water`, 31 Aug then 12 Sep, about 1,240 km² | Click both dates | "Sevan is the control. A naive method thinks it shrank on rough days. Ours holds at about 1,240; the published area is 1,242." | `#/sevan?layer=water&date=2026-08-31` |
| 7 | 2:25-2:50 | Ararat Valley, `hh`, any date: brightness is flat; then slide (see section C) | Show the flat curves, then switch to the slide | "In Ararat, brightness shows no farming. Radar coherence does: in June and July 43 percent of the valley floor loses coherence in 12 days, in October about 23. This layer is not in the app yet." | `#/ararat?layer=hh&date=2026-07-14` |
| 8 | 2:50-3:00 | Limits slide or end card | Static | "Limits: uncalibrated previews, flood size good to about 20 percent, one season. A calibrated run on Tonle Sap fits better, within 5.9 percent." | none |

Shot 7: Ararat spot curves are flat across the season, except 31 Aug on `masis-fields` HH (72.8 against about 66 on other dates, `src/app/public/data/ararat/timeseries.json`). If someone asks, say the 31 Aug rise falls on the rain night seen around Yerevan (`docs/eda/validation-2026.md`) and that we did not test the cause at Ararat.

## B2. The earthquake shot (Tibet M7.0, 22 Oct 2026)

Numbers from `docs/eda/quake.md`: 127 cm peak to peak along the radar line of sight within 45 km; the same-track pair from before (7 to 19 Oct) is 5 cm; one look direction; one colour cycle in the fringe layer is 10 cm. Sources and limits: `docs/pitch/FACTS.md`, claim 11.

| Design | Link | On screen | Spoken (about 10 s; in the 3-minute walkthrough it can replace shot 7) |
|---|---|---|---|
| A, natural map | `#/tibet-m70-2026?layer=fringes` | Fringe bands around the epicentre on the satellite globe; use the reveal or before/after bar to compare with the imagery | "On 22 October a magnitude 7.0 earthquake hit Tibet. NISAR's pair spanning it shows the ground moved by about 127 centimetres along the line of sight: each colour band is 10 centimetres. The pair from before is flat." |
| B, story mode | `#/tibet-m70-2026?mode=story&scene=2` | Story scene 2 of the Tibet place (arrow keys step through the scenes) | Same sentence; then press the right arrow once for the next scene |

Say, in this order: what it is (a pair of passes across the earthquake), what we see (127 cm, control 5 cm), what we do not know (no independent check; sign unconfirmed; provisional product). Do not say damage, casualties, fault plane, or that it is validated. If asked about the four events not seen, see `FACTS.md`, question 12.

## C. The coherence shot (not in the app)

The coherence layer is a candidate only (`docs/eda/insar.md`, section 9). Do not point to a link for it. Slide-only alternative:

- **Best figure: `docs/eda/insar/fig3_coherence_series.png`.** One chart, five lines: the valley floor sits lowest in summer and rises in autumn, while the hills and the Sevan control stay nearly flat. It is readable in five seconds. Caveat: the rise is modest (0.368 to 0.449, `docs/eda/insar.md`) and the chart's "wheat harvest (Jun-Jul)" shading is an annotation we did not source. Crop it out or say nothing about wheat.
- Second choice, to show field-sized patterns: `docs/eda/insar/fig2_ararat_coherence_maps.png` (not opened by me; described in `docs/eda/insar.md` as the maps and autumn-minus-summer zoom).
- Do not use: `fig5_ararat_displacement.png` (ground motion, no detection), `fig8_soil_moisture.png` (did not separate irrigated land).

Caption for the slide: "Ararat Valley, NISAR 12-day coherence. Brightness previews showed nothing here."

## D. Things not to say on camera

Subsidence measured; harvest dates per field; soil moisture shows irrigation; four continents; our models beat NASA's; preview areas are calibrated; earthquake damage or casualties; fault plane or slip; that the earthquake maps are validated against other data. See `docs/pitch/FACTS.md`.
