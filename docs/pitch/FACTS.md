# Pitch facts: Dancing with the SARs

Prepared Sat 14 Nov 2026, 18:31 (Armenia time) for the 15:00 pitch and the 30 s video. Read time: two minutes.
Every number has a source path (relative to the repo root). Where the app and a study differ, the app number is used on screen and the study number is named.

## 1. Headline claims, in order of impact

| # | Claim in plain words | Number | Checked against | Source |
|---|---|---|---|---|
| 1 | We sized the 2026 Tonle Sap flood to within about 8 %, and the radar tracks it pass by pass. | Open water 2,281 km² (19 Jun) to 5,046 km² (17 Oct); range on 17 Oct 4,196–6,051 km². Inside the independent range on 7 of 10 passes. Mean area error 8.5 % (older detectors 20–25 %). | An estimate built without radar: lake level from the NASA/USDA altimeter plus 38 years of Landsat water maps (5,421 km², range 4,198–7,222 on 17 Oct). | `src/app/public/data/tonle-sap/timeseries.json`; `docs/eda/fusion2.md`; `docs/eda/hydro.md`; `docs/RESULTS.md` |
| 2 | On calibrated NISAR data the same season fits the independent estimate even better. | Ten dates at 80 m: 2,340 to 5,625 km² on 17 Oct; mean deviation 5.9 % (app detector 8.5 %); rank correlation with lake level 1.00 (Pearson 0.94); threshold sensitivity at the flood peak x1.25 (preview rule x2.39). | Same independent estimate and the Sentinel-6 lake level. | `docs/eda/calibrated.md`; `docs/eda/calibrated_det/results.json` |
| 3 | Our water area rises and falls with the real lake level. | Rank correlation 0.98 (0.976), Pearson 0.96, ten passes. | NASA/USDA G-REALM lake height (Sentinel-6A altimeter). | `docs/eda/validation-2026.md` (claim 3a) |
| 4 | The radar saw the July rise as water under vegetation. | 1 to 213 to 462 km² in July while the Prek Kdam river gauge rose 2.76 m in 12 days. | Mekong River Commission gauge; altimeter (+1.3 m, 2 to 12 July). | `docs/RESULTS.md`; `docs/eda/validation-2026.md` |
| 5 | The change map has a stated, measured false-alarm rate. | 0.02 % of held-out stable land flagged at Tonle Sap, 0 % at Sevan and Ararat; false-discovery rate set at 1 %. | Stable land not used to build the test. | `docs/RESULTS.md`; `docs/eda/stats-change.md` |
| 6 | The water detector is right where the answer is known. | Balanced accuracy 99.6 % at Tonle Sap and Lake Sevan; 0.005 % false alarms on never-water land at Tonle Sap. | Landsat pixels that are always or never water, on a strip held out from tuning. | `docs/eda/scoreboard.md`; `docs/RESULTS.md` |
| 7 | Lake Sevan does not change, and we measure it. | About 1,240 km² on all ten passes in the app (1,239.4–1,239.5); published area 1,242. | Published lake area; 30 m Landsat shoreline. | `src/app/public/data/sevan/timeseries.json`; `docs/RESULTS.md` (1,242.5 and 1,235 km² in the sub-pixel and time-model studies) |
| 8 | The flood front can be dated through the 24-day outage (27 Jul to 10 Aug). | 309 km² flooded between the 25 Jul and 18 Aug passes, 12.9 km²/day, between the rates before (9.3) and after (13.6). Hiding a real pass and predicting it: error 82 km² against 161 km² for "nothing changed". | Leave-one-pass-out test on our own passes. | `docs/RESULTS.md`; `docs/eda/novel.md` |
| 9 | The method works in other places, not only where we built it. | 15 places (Asia, Africa, South America; Armenia on the Europe-Asia boundary). Pantanal and Lake Chad rescued (balanced accuracy 50 % to 99 %), Inner Niger 50 % to 97 %. Narrow channels stay weak (Mekong delta 65 %, Nile delta 71 %). | Landsat always-water and never-water pixels in each place. | `docs/eda/crossscene.md`; `docs/RESULTS.md` |
| 10 | In Armenia's Ararat Valley, brightness shows nothing but interferometric coherence shows the farming season. | Valley floor losing coherence within 12 days: 43-44 % of the floor in June-July, 22-25 % in late Sept-Oct; hills 9-16 %. Mean floor coherence 0.368 (summer) to 0.449 (autumn). Not in the app yet. | Hills, Yerevan and Sevan land as controls. | `docs/eda/insar.md` section 3; figure `docs/eda/insar/fig3_coherence_series.png` |

## 2. Say / do not say

**Say**
- "We checked every headline number against data our detector never used: Landsat, a NASA altimeter, river gauges, weather stations."
- "Independent data corrected our own story three times." (a) The flood did not arrive late. The lake rose sharply from early July and peaked 24-29 Sept. Our radar saw the July rise as water under vegetation, and our open water kept growing after the lake started to fall. (b) The two "windy" Sevan dates were not simply wind: 31 Aug followed a rain night (29 mm in 48 h). We now say "rough-surface dates". (c) Rain did not cause the odd HV channel on 6 Oct in Armenia (at most 1 mm). Source: `docs/RESULTS.md`, `docs/eda/validation-2026.md`.
- "The app shows maps made from free 180 m preview images. We re-ran Tonle Sap on calibrated 80 m data and it fits better." (`docs/eda/calibrated.md`)
- "Water size is known to about plus or minus 20 %."
- "Coherence shows the farming season in the Ararat Valley." / "Ground motion: nothing above our +/-15 mm limit over four months."
- "Radar sees through cloud and at night." (`docs/research/science-brief.md`, section 1)
- "AI was used for research, planning, code, analysis and first drafts, and we disclose it." (`README.md`)

**Do not say**
- Our models beat NASA's, or any model we did not test. We tested a few public networks (see Q on deep learning) and none beat our fusion; that is all we can say.
- "Four continents." Say "Asia, Africa and South America; Armenia sits on the Europe-Asia boundary".
- That subsidence was measured. It was not detected (limit +/-15 mm over 120 days).
- That per-field harvest dates were mapped. The change is a season-long low, not a one-day event.
- That soil moisture shows irrigation. It does not (`docs/eda/insar.md`, summary table).
- Preview-image areas are calibrated measurements. The app numbers come from uncalibrated 8-bit previews.
- That the flood "arrived late".
- That the calibrated result works everywhere. It is one place (Tonle Sap) so far.
- That 98.9 % of our water is "correct". It is inside the 1984-2021 Landsat maximum extent, which is a plausibility check (the current app map: 97.0 %, `docs/eda/fusion2.md`).

## 3. Likely judge questions

1. **Is this calibrated data?** The app shows maps built from NISAR's free 180 m preview images, which are display brightness, not backscatter, and the app says so. We also re-ran Tonle Sap on calibrated 80 m data (provisional calibration, ten dates): 5,625 km² on 17 Oct, within 5.9 % of the independent estimate on average. It is not in the app yet and the same thresholds do not work at Lake Sevan (different radar mode, higher noise floor). (`docs/eda/calibrated.md`, section 9)
2. **How do you know the flood size?** We do not know it exactly. We have an independent estimate from the altimeter lake level and 38 years of Landsat (5,421 km² on 17 Oct, range 4,198-7,222); our 5,046 km² (range 4,196-6,051) falls inside it. The detector's floodplain threshold was tuned to that same estimate, so the 8.5 % error is partly in-sample. (`docs/RESULTS.md`, Limits)
3. **Why not deep learning?** We tried and measured it. A large pretrained network scored IoU 0.21 against 0.65 for a Gaussian mixture; a flood network from Microsoft matched its own threshold rule; the ALOS-2 L-band model scored 0.91 at Sevan against 0.99; learned speckle filters changed nothing beyond noise. A network trained on Landsat labels matched the maths on unseen places but did not beat the fusion we use. (`docs/RESULTS.md`, "What we tried that did not help"; `docs/eda/supervised.md`)
4. **What is new here?** A season of NISAR radar made explorable by anyone, with every number checked against independent data and with the corrections shown. Also a detector that sets its own thresholds per scene, and dated flood fronts through the outage. We do not claim a new radar method beyond this. (`docs/RESULTS.md`)
5. **What would you do with a year of NISAR?** Run the calibrated pipeline for all places, and repeat the ground-motion test: our four-month limit is +/-15 mm, which would be about 8 mm per year with a full year. (`docs/eda/insar.md`, section 7)
6. **How was AI used?** Claude helped with research, planning documents, code, analysis and first drafts of text. The team chose the challenge approach and the places, reviewed the results and edited the text; the repository labels this. (`README.md`, "Use of AI")
7. **How do you know it is not just wind or rain?** At Sevan we checked weather data: 31 Aug followed a rain night, 12 Sep had a steady 2-4 m/s breeze, and neither shows strong wind. So we call them "rough-surface dates" and our final detector keeps the lake at about 1,240 km² on both. (`docs/eda/validation-2026.md`)
8. **What about the outage?** No passes exist from 27 Jul to 10 Aug 2026. The app shows the gap and does not invent a pass; a hidden-pass test says our front dating beats "nothing changed" (82 against 161 km²). (`docs/eda/novel.md`)
9. **Can radar see ground motion or irrigation?** Not in our data. Ground motion at Ararat: nothing above +/-15 mm. Soil moisture: the product did not separate irrigated land from dry hills. Coherence did show the farming season. (`docs/eda/insar.md`)
10. **Who is this for?** Teachers, reporters and anyone curious about a region: a dated, plain-language view of one radar season. (deck notes, slides 6-7; no user study was done.)

## 4. Limits, in four lines

1. The app uses uncalibrated 180 m previews; calibrated results exist for Tonle Sap only, are provisional, and are not in the app.
2. Flood size is known to about plus or minus 20 %, its outline less well (only 76 % of our water lies inside the independent flood map); no 2026 ground truth exists for the floodplain.
3. Water under vegetation (about 1,600-2,500 km² at the 30 Aug peak) is not validated.
4. One season, ten passes, a 24-day outage; no ground motion detected; Ararat coherence is not in the app.
(`docs/RESULTS.md`, Limits; `docs/eda/calibrated.md`; `docs/eda/insar.md`)

## 5. Space Apps judging criteria (scored 1-5 each)

Source: `docs/research/submission-rules.md` (paraphrase of the official pages; reread the originals).

- **Impact:** The Tonle Sap flood, which connects the lake with forests where fish feed (app story text, `src/app/public/data/tonle-sap/manifest.json`), becomes something a teacher or reporter can show in 30 seconds. (No user numbers; do not claim reach.)
- **Creativity:** The dance metaphor made literal: each pixel's date of change as one colour image, flood order as a rough height map, the front dated through the outage.
- **Validity:** Every headline is checked against independent data (Landsat, altimeter, gauges, weather), corrections are published, limits are stated, and the false-alarm rate is measured.
- **Relevance:** It answers "Dancing with the SARs" directly: NISAR L-band passes, June-October 2026, surface change shown in an app.
- **Presentation:** A 30 s video and a 7-slide deck with the same story: play the flood, show Sevan standing still, state the limits.

Other required items to check before submitting (same file): demo link public with no login; NASA data listed; licence file (MIT, in the repo); AI use disclosed; team names and challenge on screen; "Submit for Judging" pressed before 23:59 on 15 Nov.
