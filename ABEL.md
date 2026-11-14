# Abel — your tasks

Written Saturday 14 November, 11:20 (Yerevan). Deadline: **Sunday 15 November, 23:59**. We aim to submit by 21:00.

You and Harutyun own detection: turning radar images into findings, and making sure what we say is true. Harutyun has the code side running. Your side is the science, the checking, and the words. No coding is needed for any task below.

Everything linked here lives on the `eda` branch until it is merged to `main`:

```bash
git fetch && git checkout eda && git pull
```

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
