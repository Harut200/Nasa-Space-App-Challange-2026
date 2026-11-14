# Abel — your tasks

## Update, Saturday 14 Nov, 22:00 — what changed since midday, and what is asked of you now

Deadline unchanged: **Sunday 15 November, 23:59**. Read this section first. Everything below it is history; the parts that no longer hold are listed here.

### What is superseded in the sections below

| Older section | Status |
|---|---|
| "Update, Saturday 12:50" (reply to your review, numbers for the demo) | History. The current numbers are in [docs/RESULTS.md](docs/RESULTS.md). The app now shows **fusion v2**: Tonle Sap 17 Oct 5,046 km² (range 4,196–6,051), not "about 3,500 km² (2,000–4,800)" |
| "Where things stand", three lines of data | Out of date: there are 19 places now (15 water, 4 earthquakes), and Ararat's open water is about 0.3 km², not "0.5 to 3.8" |
| Tasks 1 to 4 of Saturday | 1 and 4 done or taken over; 2 answered by [validation-2026.md](docs/eda/validation-2026.md); 3 (story text) is replaced by the review below |
| Tasks 5 and 6 (demo, project page) | Still open; see "Asked of you now" |

### What changed that matters to you

1. **Story text was corrected against the current numbers** (commit "Correct story text that contradicted the current maps", 18:35). Please review each change; they are yours to overrule.

| Place | What changed, and why |
|---|---|
| Ararat | Open water "about 0.5 to 3.8 km²" became "well under one square kilometre (about 0.3) on every pass" (the fused map, `src/app/public/data/ararat/timeseries.json`). The 6 October cross-polarised channel is now "probably an instrument or processing effect rather than rain" (rain was ruled out, [validation-2026.md](docs/eda/validation-2026.md)). Coherence is mentioned: 43–44 % of the valley floor incoherent in June–July against 22–25 % from mid-September ([insar.md](docs/eda/insar.md)); "not yet a layer in this app". The ponds are "mostly too small for our 180 m pixels" |
| Bangladesh haor | "Wind roughens it" became "looks rougher, possibly from wind" (no wind data for that place). "Flooded vegetation" became "possible water under vegetation" |
| Inner Niger | Added the mapped open-water growth (about 1,200 km² on 24 June to about 3,100 km² on 10 October) in place of "biggest jump between 28 September and 10 October"; "possible water under vegetation" |
| Mekong delta | Added the mapped open water (about 150 km² in June to about 2,000 km² by 15 October); "part of the flood shows only as got darker" (was "most of") |
| Nile delta | "Our open-water layer is empty" became "shows only about 210 km², unchanged on every date" |
| Okavango | "Scattered pools" became "finds no open water here"; "rarely calls it open water" became "does not call it open water" |
| Poyang | Added the mapped open-water area (about 3,300 km² in early July, about 3,200 km² in October); "possible water under vegetation" |
| Sevan | "Roughly 1,235 km² … time-series model" became "about 1,240 km² of open water on all ten passes", the number the app shows (published 1,242) |
| Sindh–Indus | Added that the thin Indus channel is not picked up at this resolution |
| Sudd | Added the mapped open water (peak about 2,000 km² on 19 August, about 1,900 km² on 18 October) and "the weakest of our open-water maps" |

2. **New stories to review: Pantanal and Lake Chad** (`areas/pantanal/story.json`, `areas/lake-chad/story.json`). They were first dropped and came back after the self-calibrating detector rescued them (balanced accuracy 50 % to 99 %, [crossscene.md](docs/eda/crossscene.md)). Check the plain-language wording and the one sourced number per paragraph.
3. **Earthquake stories to review** (`areas/tibet-m70-2026`, `venezuela-m72-2026`, `panama-m77-2026`, `kumamoto-m68-2026`, `story.json` in each). Check them against the "do not claim" list in [docs/eda/quake.md](docs/eda/quake.md): no damage, casualties or effect on people; no fault plane, slip or rupture length; no vertical or horizontal motion except Venezuela's approximate east/up split; not validated against GNSS or other satellites; sign convention unconfirmed; products PROVISIONAL. Numbers: Tibet M7.0 127 cm, Panama M7.7 133 / 85 cm, Venezuela M7.2 51 / 55 cm, Kumamoto M6.8 38 cm. Four earthquakes not detected (Flores, Mexico, Sanriku, Qinghai) are reported as such.
4. **Pitch material exists**: [docs/pitch/FACTS.md](docs/pitch/FACTS.md) (every claim with its source, say / do not say, twelve likely judge questions), [docs/pitch/deck-audit.md](docs/pitch/deck-audit.md) (the deck, slide by slide: correct, outdated, unsupported), [docs/pitch/demo-script.md](docs/pitch/demo-script.md) (30 s and 3 min shots, now with a Tibet shot). The deck predates fusion v2, the calibrated run and the earthquakes.
5. **Two app designs** exist; the team is choosing one tonight ([docs/pitch/app-choice.md](docs/pitch/app-choice.md)). Story text appears differently in each (a side sheet in A, six scenes in B), so keep paragraphs short.

### Asked of you now, in order

1. Review the corrected stories (table above) and the Pantanal and Lake Chad stories. Edit `areas/*/story.json` directly and push, or send the text to Harutyun.
2. Review the four earthquake stories against the do-not-claim list.
3. Read `FACTS.md` and the deck audit; tell Harutyun which deck lines to change. Do not reintroduce "arrived late", "wind" for Sevan, or "four continents".
4. Demo (30 s video or seven slides) and project-page text (summary, NASA data list including NISAR L2 GUNW for the earthquakes, other sources including the USGS Earthquake Hazards Program, AI-use statement) by tomorrow; see the older tasks 5 and 6 below.

---

Written Saturday 14 November, 11:20 (Yerevan). Deadline: **Sunday 15 November, 23:59**. We aim to submit by 21:00.

You and Harutyun own detection: turning radar images into findings, and making sure what we say is true. Harutyun has the code side running. Your side is the science, the checking, and the words. No coding is needed for any task below.

Everything linked here lives on the `eda` branch until it is merged to `main`:

```bash
git fetch && git checkout eda && git pull
```

## Update, Saturday 12:50 — reply to your review, and what changed since

Your review (`docs/eda/detection-review.tex`, pushed 12:43) is merged. Read this section first; the task list below is adjusted by it.

### Your review: checked, and it holds

Every number in it was recomputed from the detector's output. No errors.

| Your statement | Check |
|---|---|
| Water rule: seeds HH < 0.06, candidates HH < 0.12, HV < 0.35, grown by connection, small pieces removed | Matches the code |
| Change statistic: 5 and 5 observations with a 0.20 step give 0.316 | Correct |
| Pixel area 0.0324 km² | Correct (the grid gives 0.0327) |
| One-pixel shoreline error on a 250 km shore ≈ 45 km² | Correct; the validation found the same figure |
| A 0.12 m level change moves the waterline 6.9 m on a 1° bank, 69 m on a 0.1° bank | Correct |
| Open water 2,234 → 3,837 km², about +72 % | Correct |
| 25 Jul → 18 Aug: 381 km² in 24 days; 11 → 23 Sep: 375 km² in 12 days | Correct |
| 98–99 % of late-season Tonle Sap water inside the historical maximum extent; Sevan overlap 0.94 | Correct |

Your recommendations are adopted:

- **"Possible flooded vegetation"** in the legend — yes; nothing independent confirms that class. The app label will change.
- **One detector output for story, map and chart** — yes. Mixing methods is what produced two water areas a factor of two apart.
- **"Marked expansion between 11 and 23 September"** rather than a daily rate — yes; rates inside an interval are unknown.
- Change-date colours mean "first observation of a new brightness level", and rain, crops and mixed pixels can cause one too — agreed, and worth a sentence in the story.

### What changed after the version you reviewed

You reviewed the detector as it stood around 11:30. Four parts of your text are now out of date. Current numbers and method are in [docs/DETECTION.md](https://github.com/Harut200/Nasa-Space-App-Challange-2026/blob/eda/docs/DETECTION.md) and the notes listed here.

| Your section | Then | Now | Read |
|---|---|---|---|
| Possible flooded vegetation | HH rise > 0.15 with HV rise < 0.06. Peak 611 km²; 139–1,083 km² depending on the cutoff | Rule rebuilt. The signature in this data is **HV falling while HH stays bright** — "HH up, HV flat" occurs on 0.4 % of shoreline pixels. Peak about 2,100 km² on 30 August (range 1,600–2,500). Threshold sensitivity 1.3× instead of 7.8×. Two states on the map: water under vegetation, and "going under" (submerging canopy or a pixel partly open water — the two cannot be told apart) | `docs/eda/floodveg.md` |
| "About 97 % of Ararat's water lies outside the reference" | 10–31 km² of false water per date | A terrain mask from a public elevation model now removes water where the ground is too steep or too high above the local valley floor. Ararat is down to about 1 km²; false water around Sevan fell by 70 %. The cause was moderately sloping uplands, not radar shadow | `docs/eda/terrain.md` |
| First observed change | One change date per pixel, thresholds set by eye | A statistical test with the false-discovery rate held at 1 % now runs alongside it. False alarms on stable land: 0.02 % against 5.8 %. It allows several changes per pixel | `docs/eda/stats-change.md` |
| Numbers for the demo | 3,837 km² on 17 October | Quote it with its range: **about 3,500 km² (2,000–4,800)**. Nearly all of the range comes from the choice of threshold, almost none from noise. A held-out test showed our threshold is strict, so 3,837 is a lower bound; plausible late-season water is 3,800–5,900 km². The growth factor, about 1.7×, is the safe statement | `docs/eda/stats-change.md`, `docs/eda/tuning.md` |

Also new, and not in your review:

- **Lake Sevan on windy dates.** A model that follows each pixel through time keeps the lake at 1,235 km² on all ten dates (published: 1,242). One rough date cannot overrule nine calm ones. `docs/eda/gmm.md`
- **Held-out accuracy.** On regions never used for tuning, the detector finds 99.2 % (Tonle Sap) and 99.8 % (Sevan) of permanent water and calls 0.01 % of never-water land water at Tonle Sap. `docs/eda/tuning.md`
- **Choreography map.** One image of Tonle Sap where colour is *when* each place changed and strength is *how much*: green close to the lake (mid-August), blue further out (September), purple furthest (October). This is the picture for slide one. `docs/eda/novel/tonle-sap_choreography.png`
- **Flood arrival day, and the outage.** Arrival interpolated between consecutive shorelines and tested by hiding real passes. Inside the 27 July – 10 August outage each place is known only to within ±12 days — but the flooding rate during it (12.9 km² per day) sits between the rate before (9.3) and after (13.6), so there was **no hidden surge**. `docs/eda/novel.md`
- **Height from the order of flooding.** "Flooded earlier means lower" agrees moderately with an elevation model it never saw (rank correlation 0.45; 0.25 on the stricter test) and with 38 years of Landsat water history (−0.47). Build on the "waterline method" (1996); do not call it invented. Suggested wording and a what-we-claim box are in `docs/eda/novel.md`.
- **Your proposed check** — what share of the 30 August vegetation mask is open water by 23 September — has a relative now: 31 % of pixels that became open water were flagged as flooded vegetation on an earlier pass. The exact fraction you defined has not been computed; it is a good one to ask for.

### Correction, 13:15 — independent 2026 data changed two of our claims

Task 2 (the four facts) has been answered from weather stations, reanalysis, the Mekong River Commission gauge feed and satellite altimetry. Full write-up: `docs/eda/validation-2026.md`. Two things we had been saying are wrong or too strong, and the story text has been corrected to match. **Do not reintroduce the old wording when you edit.**

| We said | The data says | Verdict |
|---|---|---|
| "The Tonle Sap flood arrived late; the biggest jump was 11–23 September" | The lake began rising in early July: the Prek Kdam gauge rose 2.76 m in 12 days from 5 July, the satellite altimeter +1.3 m from 2 to 12 July. Our open water barely moved then, but water under vegetation went from 1 to 462 km². The lake peaked about 29 September at a normal height. Level gains were largest in July and across the outage, small for 11→23 September | **Contradicted as worded.** Radar saw the early rise as water under vegetation, the later rise as open water |
| "Wind roughened Lake Sevan on 31 August and 12 September" | No source shows more than about 3 m/s at pass time on either date. 31 August followed a night of rain: 29.1 mm at the Sevan shore station in the 48 hours before. 12 September was dry with a steady 2–4 m/s breeze overnight | **Undecided.** Say "roughened by rain and wind", not "wind" |
| "6 October in Armenia: rain, or an instrument effect" | 0.0 mm at the Sevan and Gavar stations in the 48 hours before; at most 1 mm elsewhere | **Rain contradicted.** Instrument or processing effect remains the likely cause |

One result went our way, and it is the strongest validation we have:

- **Tonle Sap open-water area against the lake level from a NASA satellite altimeter: rank correlation 0.98 over the ten passes** (Pearson 0.96). Against the Prek Kdam river gauge: 0.72.

Also answered: Lake Sevan fell 0.23 m from 18 July to 7 October (altimeter, agreeing with the Armenian gauge to about 2 cm). It did not rise during our pass window.

Something only you can judge: the lake fell after late September while our open-water class kept growing to 17 October (+296 km²). Open water plus water under vegetation fell slightly over the same period. Is that vegetation drowning and becoming open water, or something else?

### Where your task list stands

| Task | Status |
|---|---|
| 4. Judge the detection | **Done.** Thank you |
| 1. Check map spots | Your last line mentions "five moved centers", but no changed coordinates were in the push. **Please push your edits to `areas/*/story.json`**; the charts are recomputed as soon as they arrive |
| 2. Four facts (wind, rain, Sevan level, Mekong gauges) | **Mostly taken off you.** Wind and rain at pass times, and lake and river levels, are being fetched automatically from weather reanalysis and satellite altimetry. You will get the result to sanity-check. If you already found Sevan levels for August–October or Mekong gauge readings, push them |
| 3. Make the story text yours | Open. Now the most useful thing you can do today |
| 5. Demo | Open, due Sunday 17:00. Start from the choreography map |
| 6. Project page text | Open, due Sunday 19:00 |

### What to do next, in order

1. `git pull` on `eda`. The detector changed a lot since your copy.
2. Push the moved spot coordinates.
3. Story text for the three places (task 3), using the current findings above.
4. If time remains today: bring `detection-review.tex` up to the current numbers — the four rows in the table above. It would make a strong appendix for judges as a PDF. If you would rather not, say so and Harutyun will update it from your text.
5. Tomorrow: demo, then project page.

---

## Where things stand

Done already, so you do not redo it:

- **Data.** Ten radar passes each (19 June – 18 October 2026) for Tonle Sap, Lake Sevan and the Ararat Valley. So far only the free preview images: about 180 m per pixel, not calibrated. Patterns and timing can be trusted; exact values and areas cannot.
- **Analysis.** [docs/eda/eda-findings.md](https://github.com/Harut200/Nasa-Space-App-Challange-2026/blob/eda/docs/eda/eda-findings.md) — what the images show, with figures.
- **Detection.** [docs/eda/math-detection.md](https://github.com/Harut200/Nasa-Space-App-Challange-2026/blob/eda/docs/eda/math-detection.md) — how water, flooded vegetation and "when did it change" are computed, and the limits.
- **Story drafts.** `areas/tonle-sap/story.json`, `areas/sevan/story.json`, `areas/ararat/story.json` — four short paragraphs and five or six map spots per place. Drafted with AI; they need you.
- **Background reading.** [docs/research/science-brief.md](docs/research/science-brief.md), with sources and confidence marks.

What the data says, in three lines:

- **Tonle Sap:** almost no change until late July, then the flood spreads like a wave — river plain south-east of the lake first (late August), the ring around the lake in September, biggest jump between 11 and 23 September.
- **Lake Sevan:** measures the same on all ten dates, within a few percent of its true 1,242 km². On 31 August and 12 September part of the lake looks like land; we believe that is wind.
- **Ararat Valley:** nothing visible at this resolution. Fields are smaller than a pixel.

## Today (Saturday)

### 1. Check every map spot — 45 minutes, do this first

The spots in the three `story.json` files were placed from approximate coordinates. Open each in Google Maps satellite view (paste `latitude, longitude` — note the files store longitude first) and confirm it sits on what its label says. Fix the numbers in the file where it does not.

Least certain:

| Area | Spot | Stored as [lon, lat] | Should be on |
|---|---|---|---|
| Tonle Sap | `southeast-corridor` | [104.85, 12.0] | Floodplain between the lake and Phnom Penh |
| Tonle Sap | `kampong-luong` | [104.2, 12.25] | Lake shore at Kampong Luong |
| Tonle Sap | `prek-toal` | [103.7, 13.1] | Flooded forest, north-west shore |
| Sevan | `north-lake` | [45.4, 40.5] | Open water, northern basin |
| Sevan | `gavar-shore`, `martuni-shore` | [45.13, 40.35], [45.3, 40.14] | Shallow shoreline, half water and half land |
| Ararat | `fish-ponds` | [44.6, 39.9] | Actual pond rectangles near Ararat–Vedi |
| Ararat | `aknalich` | [44.18, 40.07] | Lake Aknalich |
| Ararat | `vedi-vineyards` | [44.73, 39.91] | Vineyards |

### 2. Find four facts the research could not — 1 hour

Each one either confirms or breaks something we plan to say.

| # | Question | Where to look | Why it matters |
|---|---|---|---|
| a | Was it windy at Lake Sevan around 06:00 on **31 August** and **12 September** 2026? | Hydromet Center (armmonitoring.am), any weather archive for Sevan, Gavar or Martuni | Our "wind, not shrinkage" claim rests on this |
| b | Did it rain in the Ararat Valley or around Sevan on **4–6 October** 2026? | Same | The 6 October pass looks different from its neighbours; rain is one explanation, an instrument fault is the other |
| c | Lake Sevan level in **August, September, October** 2026 | Hydromet Center, EcoLur weekly reports (ecolur.org) | We have readings only up to 19 July (1900.81 m) |
| d | Tonle Sap water level through the 2026 season: when did it start rising, when did it peak? | Mekong River Commission (mrcmekong.org), gauges at Kampong Luong and Prek Kdam | Tests our finding that the flood came late, with the big jump in mid-September |

Write what you find, with the link, in `docs/research/story-sources.md`. "Could not find it" is a valid answer — write that too.

### 3. Make the story text yours — 1 hour

Read the three stories as a judge would. Rewrite anything that is wrong, vague or sounds like a machine. Rules: four paragraphs per place, 60 words each at most, plain language, one sourced number in the second and fourth paragraphs.

Check in particular:

- "More than a million people depend on this lake" — confirm against the FAO pages cited.
- "The main reservoir for Armenia's farms" — no figure behind this phrase. Find one or soften it.
- Sevan's area is cited to Wikipedia. Find a better source.
- "The flood arrived late" — keep only if task 2d supports it.
- NISAR and L-band are used without explanation. Decide whether to spell them out.
- The Armenian stories are our home ground. Add what only someone from here would know.

### 4. Judge the detection — 30 minutes

Look at these and tell Harutyun whether they make physical sense. You are the check on the code.

- "When did it change" map for Tonle Sap: [tonle-sap_change_date.png](https://github.com/Harut200/Nasa-Space-App-Challange-2026/blob/eda/docs/eda/math/tonle-sap_change_date.png). Does a flood spreading from the south-east outward match how the Tonle Sap fills?
- Water and flooded vegetation per date: [tonle-sap_water_fv_grid.png](https://github.com/Harut200/Nasa-Space-App-Challange-2026/blob/eda/docs/eda/math/tonle-sap_water_fv_grid.png). Flooded vegetation peaks at the end of August and then shrinks while open water keeps growing. We read that as plants going under as the water deepens. Is that believable?
- One thing we got wrong and corrected: we expected the forest ring to get brighter when flooded. It got darker in both channels. Worth one honest sentence in the story.

## Tomorrow (Sunday)

### 5. Demo — by 17:00

Either a video of **30 seconds or less** or a deck of **7 slides or fewer**. Over the limit and we are not judged globally. Public link, no login, English. Outline and slide-by-slide plan: [plan/04-pitch-and-repo.md](plan/04-pitch-and-repo.md). Rules: [docs/research/submission-rules.md](docs/research/submission-rules.md).

### 6. Project page text — by 19:00

Summary, detailed description, list of NASA data used, list of every other source, and the AI-use statement. For the last one: AI tools (Claude) were used for research, planning documents, code, and first drafts of the story text; the team chose the places, checked the results and edited the text. Say that plainly.

## Also, any time today

- **NASA Earthdata login** — register at https://urs.earthdata.nasa.gov/ (free, five minutes). Nobody on the team has one yet, and without it we cannot open calibrated full-resolution data.
- **Team registration** — confirm you are joined to our team on the Space Apps site, on the same event as Harutyun and Arameh.

## How to hand work back

Edit the files on the `eda` branch, commit, push. Or send the corrected text and coordinates to Harutyun in any form. If something in the data looks wrong to you, say so early — that is the most useful thing you can do today.
