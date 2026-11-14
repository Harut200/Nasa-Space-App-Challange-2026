# Story sources and verification

Text in `areas/*/story.json` was drafted with AI assistance (Claude) and needs human review by Abel before submission. The competition requires AI use to be disclosed.

| Area | Sentence or number | Source | Mark |
|---|---|---|---|
| Tonle Sap | Lake grows from about 2,500 km² to 10,000-16,000 km² | https://hess.copernicus.org/articles/26/609/2022/ | [S] |
| Tonle Sap | More than a million people depend on the lake | https://www.fao.org/3/ad070e/ad070e0a.htm (brief cites 1-2 million [S]) | [S] |
| Tonle Sap | About 63 kg of fish and aquatic foods per person per year, second to rice | https://www.fao.org/3/W8514E/W8514E04.htm | [S] |
| Tonle Sap | Flood arrived late; spread pattern; outage 27 July-10 August | Our own images (eda-findings); late-flood reports are single-source [1] | own |
| Tonle Sap | Flooded forest is the fishery nursery | science-brief section 3 (no separate link) | [S]/[T] |
| Sevan | Rose about 59 cm January to mid-July 2026 | https://www.ecolur.org/en/news/sevan/16413/ , https://armenpress.am/en/article/1253868 | [S] |
| Sevan | About 1,242 km² | https://en.wikipedia.org/wiki/Lake_Sevan | [S] |
| Sevan | 2026 irrigation release limit 143.6 million m³ | https://www.ecolur.org/en/news/sevan/16450/ | [S] |
| Sevan | Wind patches 31 Aug and 12 Sep; area stable within a few percent | Our own analysis (math-detection) | own |
| Sevan | Radar cannot see algal blooms | science-brief section 1 | [S] (general) |
| Ararat | Artesian pressure down about two-thirds since early 1980s | https://iah.org/news/as-armenian-fish-farming-expands-a-pristine-aquifer-is-drying-up | [S] |
| Ararat | More than 200 fish farms | https://e360.yale.edu/features/armenia-fish-farms-groundwater-agriculture | [S] |
| Ararat | Wheat harvest June-July, irrigated plots, fish ponds (expectation only) | science-brief section 2 | [1]/[T] |

## Abel must verify before submission

1. All spot coordinates on a satellite basemap, especially: Tonle Sap upland forest [104.7, 13.5] (chosen by guess near the north edge, may not be forest), South-east corridor, Kampong Luong (near box edge); Sevan North basin [45.4, 40.5]; Ararat Aknalich [44.18, 40.07] and the fish-pond and vineyard points.
2. "More than a million people" (brief says 1-2 million) and the FAO sources are the right pages.
3. "Main reservoir for Armenia's farms" for Sevan: brief says it feeds valley irrigation; no share figure was found, none is stated.
4. "Flood arrived late in our images" matches the final dated frames.
5. Ararat "about 180 m" pixel size and "planned next" wording match the real plan.
6. The 59 cm rise is the 1 Jan to 19 Jul 2026 figure; the text says "mid-July".
7. Wikipedia as source for Sevan area; consider a better one.

## Corrections after the 2026 validation (14 November, 13:15)

Independent 2026 data (weather stations, reanalysis, river gauges, satellite altimetry) changed three statements in the story text. Details and every number: [docs/eda/validation-2026.md](../eda/validation-2026.md).

| Area | Was | Now | Evidence |
|---|---|---|---|
| Tonle Sap | "The flood arrived late in our images"; "the biggest jump came between 11 and 23 September" | The lake was already rising in early July; radar saw it first as water under vegetation. Open water spread later | Prek Kdam gauge rose 2.76 m in 12 days from 5 July (Mekong River Commission flood forecasting feed); altimeter lake level +1.3 m from 2 to 12 July (NASA GSFC Global Water Monitor, G-REALM, Sentinel-6). Level gains between passes were largest in July and across the outage, small for 11→23 September |
| Tonle Sap | "No radar passes exist from 27 July to 10 August, so the flood's first days are hidden" | Our open-water area follows the altimeter lake level closely across the ten passes | Rank correlation 0.98, Pearson 0.96, n = 10 |
| Lake Sevan | "Wind roughened the water" on 31 August and 12 September | Rain on the first night, a steady breeze on the second | Sevan shore station (SYNOP 37717, via Ogimet): 29.1 mm in the 48 h before the 31 August pass; 12 September dry with 2–4 m/s north-west wind overnight. No source shows more than about 3 m/s at pass time on either date, so "wind" alone is not supported |

Still open for a human check: the Tonle Sap lake peaked on about 29 September at a normal height (altimeter), while our open-water class kept growing to 17 October. The story does not claim a late or weak flood any more; do not reintroduce that wording.
