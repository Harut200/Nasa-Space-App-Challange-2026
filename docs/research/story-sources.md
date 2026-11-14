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

## Sources for the revised stories

The [2026 validation](../eda/validation-2026.md) supersedes the earlier open questions about weather and Tonle Sap timing. Sentinel-6 altimetry shows a roughly 1.3 m rise between July 2 and 12 and a lake height peak around September 29. The river gauge at Prek Kdam peaked September 24. The 0.98 rank correlation is for the baseline open-water series and lake height across ten passes. The August 31 Sevan pass followed rain, and September 12 was dry with an overnight northwest breeze. These conditions are consistent with roughened water.

- [Prek Toal Ramsar information sheet](https://rsis.ramsar.org/RISapp/files/RISrep/KH2245RIS_1511_en.pdf) supports the approximately 21,300 ha site area and wetland context.
- Lake Sevan measured 1,900.70 m on August 31, 1,900.68 m on September 13 and 1,900.58 m on October 18 in the published gauge series. The readings use the gauge datum. [Armstat](https://www.armstat.am/file/article/sv_08_26r_550.pdf) · [September Hydromet](https://nt.am/en/news/359969/) · [October Hydromet](https://nt.am/en/news/362098/)
- The Water Committee reports 75.3 million cubic metres of actual 2026 irrigation withdrawal. [Water Committee](https://water.mtad.gov.am/en/articles/%D5%B6%D5%B8%D6%80%D5%B8%D6%82%D5%A9%D5%B5%D5%B8%D6%82%D5%B6%D5%B6%D5%A5%D6%80/water-intake-from-lake-sevan-for-irrigation-purposes-drops-to-its-lowest-level-in-65-years)
- USGS estimates that the area within the flowing-well boundary fell from 619 to 291 square kilometres from 1984 to 2016. [USGS groundwater study](https://pubs.usgs.gov/sir/2017/5163/sir20175163.pdf)

## Pin audit

Coordinates are longitude, latitude. This is a source and map cross-check. Pending imagery checks are noted explicitly, including checks of the full sampling circles and the radar footprint by date.

| Spot | Disposition |
|---|---|
| Tonle Sap lake-core | Keep [104.1, 12.8] as broad open-water control. Check the full circle against the persistent-water mask. |
| prek-toal | Keep provisionally [103.7, 13.1]. Ramsar confirms the western floodplain setting. Confirm the exact circle overlaps the reserve and flooded forest rather than open water. |
| chong-kneas | Keep provisionally [103.84, 13.27]. Confirm the landing or village target on dated imagery before using it as a site-specific measurement. |
| southeast-corridor | Keep [104.85, 12.0] as a broad floodplain sample. The saved change map shows the southeastern strip and a strong local transition, near the swath edge. Check valid pixels on all dates. |
| kampong-luong | Move to [104.2083, 12.5732] as the mapped Kampong Luong vicinity, about 36 km north of the original. Floating homes move with water level. This is a locality sample, separate from the MRC gauge. |
| upland-forest | Keep [104.01, 12.04] with the label Stable radar reference. EDA picked a relatively stable bright sample. Verify land cover before using it as a forest reference. |
| Sevan mid-lake | Keep [45.3, 40.35] as the broad lake control. Check the circle against an interior lake mask. |
| gavar-shore | Move to [45.1641, 40.4309], the EUWI+ lake shoreline station at the Gavaraget estuary. |
| martuni-shore | Move to [45.2963, 40.1762], the documented lake shore station in the Martuni area. |
| sevan-town | Keep [44.97, 40.55] as a town marker. Check mixed land cover in its 800 m radius before using it as an urban control. |
| north-lake | Move to [45.2484, 40.498], a documented Small Sevan open-water monitoring station. |
| Ararat masis-fields | Keep provisionally [44.43, 40.07]. Inspect the 1.2 km circle for mixed urban/agricultural land before treating it as a field control. |
| fish-ponds | Keep [44.6, 39.9] with a general valley label. The team's historical water check found pond/reservoir clusters elsewhere, including around [44.37, 40.05]. A specific pond sample still needs dated imagery and coverage confirmation. |
| vedi-vineyards | Keep [44.73, 39.91], near the mapped centre of Vedi, with a town and surroundings label. A vineyard-specific sampling circle still needs imagery verification. |
| aknalich | Move to [44.16889, 40.14444], mapped Lake Akna near Aknalich in Armavir, about 8 km north of the original. A 1 km circle samples surrounding land too. |
| yerevan | Keep [44.51, 40.18] as a general urban reference. Check time series before describing it as radiometrically stable. |

- [MRC gauge metadata](https://archive.iwlearn.net/mrcmekong.org/spatial/meta_html/WQmetadata.pdf) · [Kampong Luong place coordinates](https://www.geonames.org/search.html?country=KH&q=Kampong+Luong) · [Seasonal settlement movement study](https://www.fauna-flora.org/wp-content/uploads/2023/05/201712_Cambodian-Journal-of-Natural-History.pdf)
- [EUWI+ Lake Sevan monitoring station table](https://www.euwipluseast.eu/images/2021/06/PDF/AM-Report-Sevan_SW-Monitoring.pdf) · [Armenian regional reference map](https://env.am/storage/files/amar-2024.pdf)
- [Lake Akna place geometry](https://en.wikipedia.org/wiki/Lake_Akna_%28Armavir%29) · [Government account of Akna Lake](https://www.gov.am/en/news/item/8712/) · [Vedi map](https://en.wikipedia.org/wiki/Vedi)


## Current detector sources

- Tonle Sap's roughly 1.7× growth comes from the math run and the independent exploratory split, with different absolute extents. The figures in [stats-change.md](../eda/stats-change.md) include a threshold ensemble, and [novel.md](../eda/novel.md) defines the choreography colors. The reference to marked September 11–23 expansion refers to the observed passes.
- The rebuilt [vegetation score](../eda/floodveg.md) detects retained HH as HV falls and a second state where both channels darken. Use “possible” for both. Comparing those detections to later water is partly circular because later water already enters the second-state score.
- [GMM/HMM tests](../eda/gmm.md) gave 1,235.3 km² for Sevan's largest connected body on every pass. This is a temporal model result, separate from the individual-pass areas and the gauge heights.
- The [terrain experiment](../eda/terrain.md) reports 0.5–3.8 km² of mapped Ararat water across passes after screening. This range describes the study box and an experimental method. Historical reference disagreement and missed ponds limit interpretation.
- Keep the story's experimental results attributed by method. Compare the exported app assets with the version shown in the demo before using them as numerical evidence.


## Pantanal (story and spots)

- [Wikipedia: Pantanal](https://en.wikipedia.org/wiki/Pantanal) supports rainfall mostly November to March, June the driest month, the world's largest tropical wetland description, UNESCO World Heritage inscription in 2000, ranching on mostly private land and fishing. Secondary source; the primary UNESCO and Ramsar pages should be checked if time allows.
- [Wikipedia: Paraguay River](https://en.wikipedia.org/wiki/Paraguay_River) supports the flood peak at Corumbá being delayed 4 to 6 months compared with the rainy-season peak.
- [Wikipedia: Lake Mandioré](https://en.wikipedia.org/wiki/Lake_Mandior%C3%A9) supports coordinates -18.133, -57.550 and the Brazil-Bolivia border location.
- [Wikipedia: Cáceres Lagoon](https://en.wikipedia.org/wiki/Lake_C%C3%A1ceres) supports coordinates -18.933, -57.800 (stub article, no cited source; lies close to the southern box edge).
- Amolar on the Paraguay River (about -18.04, -57.49, Brazilian Navy fluviometric station list, https://www.marinha.mil.br/chm/estacoes-fluviometricas) was left out because it falls just north of the box (north edge -18.12).
- Corumbá (-19.009, -57.653) was left out because it falls just outside the box (south edge -19.0).
- Our own numbers: `data/processed/pantanal/products/timeseries.json` and `docs/eda/crossscene.md`, `docs/eda/fusion2.md`.

## Lake Chad (story and spots)

- [NASA Earth Observatory: Lake Chad](https://science.nasa.gov/earth/earth-observatory/lake-chad) supports rain mostly July to September, the Chari as the main inflow, shrinkage to less than a tenth of the 1960s area, and "lifeblood of more than 30 million people" in Chad, Cameroon, Nigeria and Niger.
- [FAO: Lake Chad basin chapter](https://www.fao.org/4/i2345e/i2345e06.pdf) supports the Chari and Logone having a single flood at the end of the rainy season (seen only via a search summary; the PDF was not opened).
- [Wikipedia: Chari River](https://en.wikipedia.org/wiki/Chari_River) supports the mouth at 12.978 N, 14.512 E and "90 percent of the water flowing into Lake Chad".
- [Wikipedia: Baga Sola](https://en.wikipedia.org/wiki/Baga_Sola) supports 13.536 N, 14.314 E and a lakeshore town with a fish market.
- [Wikipedia: Bol, Chad](https://en.wikipedia.org/wiki/Bol,_Chad) supports 13.46 N, 14.74 E and the July to September rains.
- [Wikipedia: Baga, Borno State](https://en.wikipedia.org/wiki/Baga,_Borno_State) supports 13.119 N, 13.857 E.
- Our own numbers: `data/processed/lake-chad/products/timeseries.json` and `docs/eda/crossscene.md`, `docs/eda/fusion2.md`.
