# Deck audit: Dancing-with-the-SARs.pptx

Audited Sat 14 Nov 2026, 18:31. Text taken from the slide XML and the speaker notes of `docs/pitch/Dancing-with-the-SARs.pptx` (7 slides, within the 7-slide limit). Images were not inspected, so claims inside pictures are unchecked. The deck predates the fusion-v2 app numbers and the calibrated run. Status on 15 November: the checks below are as of 14 November; the app is now `src/app` (the merged design) with 37 places, so references to the app's code and place count are marked where they changed.
Status key: correct / outdated / unsupported / overclaim. Facts in `docs/pitch/FACTS.md`.

## Slide 1: Dancing with the SARs (title)

| Statement | Status | Note and source |
|---|---|---|
| "TONLE SAP, CAMBODIA / NISAR FALSE-COLOR COMPOSITE" | correct | App layer `rgb` is a "Three-date composite" (`src/app/public/data/tonle-sap/manifest.json`). Say "three-date composite" to match the app. |
| "Watch Earth change through a season of NASA-ISRO radar." | correct | NISAR is NASA-ISRO (`README.md`). |
| Names: Harutyun Kesablyan, Arameh Haghverdian, Abel Babayan | correct | Matches `README.md`. |
| Notes: "browser app turns repeated radar imagery into an explorable record of change" | correct | |

## Slide 2: One lake, two passes

| Statement | Status | Note and source |
|---|---|---|
| 19 June and 17 October passes | correct | Manifest dates 2026-06-19 and 2026-10-17. |
| "NISAR can track a monsoon through clouds." | correct | Radar works through cloud (`docs/research/science-brief.md`, section 1). |
| Legend: detected open water, possible water under vegetation, partly submerged or mixed pixels | correct | Matches the app legend. |
| Notes: "Radar can collect data through clouds and at night." | correct | Same source. |
| Missing: the size of the flood | outdated (gap) | No number shown. Add 2,281 to 5,046 km² (`src/app/public/data/tonle-sap/timeseries.json`). |

## Slide 3: The prototype

| Statement | Status | Note and source |
|---|---|---|
| Play: advance through radar passes on a timeline | correct | App feature. |
| Compare: swipe across two observations | correct | before/after compare in the app (`src/app/src/Explore.jsx`; on 14 November it was `CompareMap` in design A's `MapView.jsx`, since removed). |
| Explore: see where persistent change first appeared | correct | `when` layer. |
| Notes: "timeline displays the NISAR outage rather than inventing observations" | correct | Manifest gap 27 Jul to 10 Aug. |
| Notes: "choose a map marker to read its local story" | correct | App spots and story. |
| Missing: 15 places in the app | outdated (gap) | On 14 November `src/app/public/data/index.json` listed 15; it now lists 37 (`summary.json`). The deck shows 3. |

## Slide 4: From radar brightness to change dates

| Statement | Status | Note and source |
|---|---|---|
| "Align the passes, reduce speckle, then date persistent changes." | outdated | Detection is now a fusion of several experts with a time model (`docs/eda/fusion2.md`). The sentence is true but incomplete. |
| "Uncalibrated previews, ~180 m per pixel, acquisition gaps shown" | correct | `README.md`; manifest `quality`. |
| Notes: "We align two radar polarizations..." | correct | `docs/DETECTION.md` (per README). |
| Missing: calibrated 80 m re-run | outdated | `docs/eda/calibrated.md`. |

## Slide 5: Two independent checks

| Statement | Status | Note and source |
|---|---|---|
| "0.98 rank correlation, radar water area vs Sentinel-6A lake height (10 passes)" | correct | Spearman 0.976 (`docs/eda/validation-2026.md`). Still holds for the app's detector (0.976, `docs/eda/fusion2.md`). |
| "98.9 % of mapped water on 17 October lay within Landsat's historical water extent" | outdated | 98.9 % was for the older detector's 3,837 km² (`docs/eda/validation.md`, table). The app's map (5,046 km²) gives 97.0 % (`docs/eda/fusion2.md`, "Late-season water inside the historical maximum extent"). |
| Label "FLOOD LOCATION" for that figure | overclaim | Inside a 38-year maximum extent is a plausibility check, not location accuracy. Only 76 % of the app's water lies inside the independent flood map (`docs/RESULTS.md`, Limits). The slide caption half-admits this. |
| Notes: "lake rose sharply in early July, when our maps first showed a growing signal consistent with inundated vegetation" | correct | `docs/RESULTS.md`, "What independent data corrected". |
| Notes: "historical extent... does not establish the exact 2026 shoreline" | correct | Honest. |
| Missing: flood size check (5,046 vs 5,421 km²) | outdated (gap) | Strongest number we have, not in the deck. |

## Slide 6: What the previews can resolve

| Statement | Status | Note and source |
|---|---|---|
| "Rough water can brighten the radar return." | correct | `docs/eda/validation-2026.md`. |
| "Many fields are smaller than a preview pixel." | outdated | True, but coherence at 40-80 m shows the farming season (`docs/eda/insar.md`, section 3). The deck says only what fails. |
| Notes: "weather data supported rain on one date without establishing a single cause for both" | correct | 31 Aug rain night, 12 Sep steady breeze, `docs/eda/validation-2026.md`. |
| Notes: "intended use is education and public explanation: teachers... reporters..." | unsupported | No user study or source in the repo. Phrase as intent, not as demand. |
| Notes: "compare a strong flood signature with more ambiguous local examples" | correct | |
| Missing: Sevan measured at about 1,240 km² vs published 1,242 | outdated (gap) | `src/app/public/data/sevan/timeseries.json`. |

## Slide 7: Explore a season of change

| Statement | Status | Note and source |
|---|---|---|
| "Next, use calibrated scenes to check flood boundaries and map ground motion." | outdated / overclaim | Calibrated Tonle Sap is already done (`docs/eda/calibrated.md`). Ground motion was tested at Ararat and not detected above +/-15 mm over 120 days (`docs/eda/insar.md`). "Map ground motion" suggests it works. |
| "VIEW SOURCE CODE / MIT LICENSE" | correct | `README.md`; LICENSE file exists. |
| Credits: NISAR NASA/JPL-Caltech, ISRO, ASF DAAC; JRC Landsat; NASA/USDA G-REALM | correct | `README.md`; `docs/eda/validation-2026.md`. |
| "AI assisted research, planning, code and writing." | correct | `README.md` says the same. Notes also add analysis; fine. |
| Notes: Open demo link `https://harut200.github.io/Nasa-Space-App-Challange-2026/` | unverified | Not tested from here. Test in a private window. |

## Change these first (most damaging first)

1. **Slide 5, location figure.** Replace "98.9 % of mapped water on 17 October lay within Landsat's historical water extent" with: "On 17 October the app maps 5,046 km² of open water (range 4,196-6,051). An estimate built without radar, from lake level and 38 years of Landsat, gives 5,421 km² (range 4,198-7,222)." Label: "FLOOD SIZE" not "FLOOD LOCATION". Caption: "Size is known to about 20 %. The outline is less certain."
2. **Slide 7, next steps.** Replace with: "Calibrated 80 m data already fits the independent flood estimate within 5.9 % at Tonle Sap. Next: add it to the app and test more places. Ground motion: not detected above 15 mm over four months; a full year of NISAR would test about 8 mm per year."
3. **Slide 6, Ararat.** Replace "Many fields are smaller than a preview pixel." with: "Ararat Valley: brightness shows no farming. Radar coherence does: in June-July 43-44 % of the valley floor loses coherence within 12 days, in October 22-25 %." (Add: "not in the app yet".)
4. **Slide 4, method.** Replace the one-line method with: "Align passes, reduce speckle, combine several detectors with a time model, date each change. Previews: 180 m, uncalibrated. Calibrated 80 m re-run on Tonle Sap."
5. **Add what independent data corrected** (slide 5 or 6, one line): "The lake rose from early July, not late. Radar saw that rise as water under vegetation. We corrected our story." (`docs/RESULTS.md`.)
6. **Slide 6 notes:** change "The intended use is education..." to "We built it for teachers and reporters; we have not tested it with users."
7. Slide 3: add "15 places" next to the three screens.
8. Fix the notes on slide 1 to say "three-date composite".
