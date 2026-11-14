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


## Chao Phraya plain, Thailand, 2026 flood (chao-phraya-2026)

Opened 14 Nov 2026. Search summaries were not used as facts; the pages below were opened.

- [The Nation (Thailand), 3 Oct 2026: "Flood expert sees no 2011 repeat as 2026 Nakhon Sawan runoff totals 4.3bn m³"](https://www.nationthailand.com/news/general/40071810) supports: about 4.3 billion m³ of accumulated water through Nakhon Sawan (estimate 4 to 5 billion) against 15 billion in 2011; peak flow through Nakhon Sawan about 2,600 m³/s against 4,600 m³/s in 2011; heaviest impacts expected in Suphan Buri, Nakhon Pathom and Phra Nakhon Si Ayutthaya, particularly low-lying Sena, Bang Ban and Phak Hai; inner Bangkok at virtually no risk in the most likely scenario. Quoted opinion of one expert (Seree Supratid, Rangsit University), not an official finding.
- [Cambodianess / AFP photo story, 6 Oct 2026: "Deluge By Design: Thai Villages Flooded To Protect Capital"](https://cambodianess.com/article/deluge-by-design-thai-villages-flooded-to-protect-capital) supports: authorities deliberately let low-lying riverside communities in Ayutthaya (Bang Ban and nearby) flood as retention zones to protect Bangkok, the historic city and industrial zones; residents say the area has flooded six years in a row.
- [Khaosod English, 4 Oct 2026](https://www.khaosodenglish.com/news/2026/10/04/ayutthaya-riverside-areas-warned-of-10cm-water-rise/) and [The Nation, 4 Oct 2026](https://www.nationthailand.com/news/general/40071788): river levels at Ayutthaya still rising early October (headline and search snippet only; page bodies not read).
- Locations: [Wikipedia: Sena district](https://en.wikipedia.org/wiki/Sena_district) 14.327 N, 100.404 E; [Wikipedia: Bang Ban district](https://en.wikipedia.org/wiki/Bang_Ban_district) 14.374 N, 100.486 E; [Wikipedia: Pa Sak Jolasid Dam](https://en.wikipedia.org/wiki/Pa_Sak_Jolasid_Dam) dam at 14.861 N, 101.066 E (the reservoir lies north of it; the spot is the centre of the large water body in our map at 14.951 N, 101.058 E, identified by position only).
- Not used (no casualty or displacement figures appear in the story): DDPM/Nation counts of people affected.
- Our own numbers: `data/processed/chao-phraya-2026/products/timeseries.json`, `docs/eda/events2026/chao-phraya-2026.json` (JRC comparison), `docs/research/events-2026.md`.


## Mahanadi delta, Odisha, July 2026 flood (mahanadi-delta-2026)

Opened 15 Nov 2026.

- [Deccan Chronicle, 31 Jul 2026: "Mahanadi Swells, Flood Threat Looms Large In Odisha; Several Breaches Reported"](https://www.deccanchronicle.com/amp/nation/mahanadi-swells-flood-threat-looms-large-in-odisha-several-breaches-reported-1975582) supports: more than 9.5 lakh cusecs expected to pass Mundali in the next 24 to 48 hours; Hirakud 22 sluice gates open releasing nearly 5 lakh cusecs; Chhattisgarh opened 64 of 66 gates of the Kalama barrage; embankment breach at Jodum (Badamba block, Cuttack) submerging farmland; seven embankment breaches in Jajpur; Cuttack, Khordha, Puri, Jagatsinghpur and Kendrapara at risk.
- [The Pioneer, 31 Jul 2026](https://dailypioneer.com/news/slug-lite/mahanadi-swells-as-hirakud-dam-opens-14-gates--floods-affect-7-lakh-in-odisha-mahanadi-swells-as-hirakud-dam-opens-14-gates--floods-affect-7-lakh-in-odisha?year=2026) supports: floods affected 20 of Odisha's 30 districts; Hirakud reservoir at 623.52 ft against 630 ft full; water expected to take 36 to 40 hours to reach Mundali near Cuttack. (The gate count differs from Deccan Chronicle because the articles are from different hours on 31 Jul; the story uses the 22 gates figure and does not repeat the 14 gates figure.)
- [All India Radio / newsonair, 27 Jul 2026](https://newsonair.gov.in/odisha-reeling-under-heavy-rainfall-fresh-flooding-in-several-districts/) supports: a deep depression over the Bay of Bengal driving heavy rain over Odisha; IMD forecast of heavy to very heavy rain until 30 Jul.
- [Wikipedia: Cuttack](https://en.wikipedia.org/wiki/Cuttack) 20.524 N, 85.788 E, at the apex of the Mahanadi delta between the Kathajodi and Mahanadi.
- Spots other than Cuttack are centres of mapped change (checked on our map), not named places.
- Not used: people-affected and death counts. Recession on 1 Aug (search summary only, not opened).
- Our own numbers: `data/processed/mahanadi-delta-2026/products/timeseries.json`; `docs/eda/events2026/mahanadi-delta-2026.json` (JRC). The 9 Jul and 21 Jul previews are corrupt (striped, 0.69 and 0.52 of the median file size) and were excluded in `area.json`.


## Eastern Uttar Pradesh and the Nepal Terai, late September 2026 (up-terai-2026)

Opened 15 Nov 2026.

- [Wikipedia: 2026 North Indian Ocean cyclone season](https://en.wikipedia.org/wiki/2026_North_Indian_Ocean_cyclone_season) (secondary, still being edited) supports: system BOB 06, 21 to 27 September 2026, moved toward the Odisha and north Andhra coast and brought severe flooding to Uttar Pradesh and Nepal. Death figures on that page are not used.
- [All India Radio / newsonair, 29 Sep 2026: "Flood-like conditions persist in several Uttar Pradesh districts amid heavy rainfall"](https://newsonair.gov.in/flood-like-conditions-persist-in-several-uttar-pradesh-districts-amid-heavy-rainfall/) supports: Ghaghra above danger mark at Barabanki, Ayodhya and Turtipar (Ballia); Gandak above danger mark at Kushinagar; Ramganga at Shahjahanpur and Kuwano at Gonda; a 25 m embankment breach on the Narayani in Tamkuhi Raj (Kushinagar); causes heavy rain and water released from barrages. The page gives no rainfall figures.
- [Wikipedia: Gorakhpur](https://en.wikipedia.org/wiki/Gorakhpur) 26.764 N, 83.404 E, on the Rapti.
- Not used: death and displacement counts. The statement "heavy rain 25 to 27 Sep" in the story comes only from the Wikipedia date range of the system (21 to 27 Sep), not from a rainfall page.
- Not opened (search summaries only): rainfall of 66.5 mm against 7.6 mm normal (25 to 27 Sep), Rapti above danger mark at Gorakhpur.
- Our own numbers: `data/processed/up-terai-2026/products/timeseries.json`; `docs/eda/events2026/up-terai-2026.json` (JRC). The 9 Jul and 21 Jul previews are corrupt (0.44 and 0.63 of median file size) and were excluded in `area.json`. Note the two spots outside the Terai are centres of mapped change, not named places.


## Thunder Bay 36 fire, northwestern Ontario, July 2026 (thunder-bay-36-2026)

Opened 15 Nov 2026. This is a burn place, not a water place: the water layer is flat and unrelated. What we show is the change in HH preview brightness.

- [Wikipedia: Thunder Bay 36 Fire](https://en.wikipedia.org/wiki/Thunder_Bay_36_Fire) (secondary) supports: coordinates 50.68 N, 89.48 W; start 12 July 2026; formed when the Dryden 13 fire merged with nearby fires in Wabakimi Provincial Park; 314,000 hectares as of 29 July; out of control on 31 July.
- [Fort Frances Times / SNNewsWatch, 30 Jul 2026: "Thunder Bay 36 largest wildfire in Ontario history, by far"](https://fftimes.com/news/district-news/thunder-bay-36-largest-wildfire-in-ontario-history-by-far/) supports: 313,930 hectares as of Wednesday evening (29 Jul); peak "spanned 320,000 hectares"; previous Ontario record 196,974 hectares (Red Lake 12, 2025); near Whitesand and Armstrong, about 220 km north of Thunder Bay; 13 July evacuation of Namaygoosisagagun First Nation.
- [CP24 / Canadian Press, 11 Aug 2026](https://www.cp24.com/news/canada/2026/08/11/ontario-fire-crews-making-good-progress-despite-large-area-burned-minister-says/) supports: largest fire peaked at about 3,500 square kilometres north of Thunder Bay (the article does not name the fire); 14 communities evacuated at that date. Its 3,500 km2 is larger than the 313,930 hectares (3,139 km2) in the other source; the story quotes only the 314,000 hectares and "up to about 320,000".
- Our numbers: `docs/eda/events2026/thunder-bay-36-2026.json` and `.png`. HH brightness rise inside the burned area is about 0.12 against 0.01 to 0.05 at two unburned reference spots; no patch in the control pair 17 Jun to 11 Jul. The area of the bright patch (about 2,000 km2 at +0.07) is smaller than the published burnt area and is not an area estimate. HV did not drop (inside 25 km disc +0.05 on 23 Jul against -0.01 outside), so the story does not say "dropped".
- Spots are centres of the mapped patch and the published coordinate, not named places.


## La Mierla fire, Guadalajara, Spain, July 2026 (la-mierla-fire-2026)

Opened 15 Nov 2026. Burn place; the water layer is zero.

- [The Local Spain, 20 Jul 2026 (updated 23 Jul): "800 evacuated as Spain's biggest wildfire of the year rages on"](https://www.thelocal.es/20260720/800-evacuated-as-spains-biggest-wildfire-of-the-year-rages-on) supports: La Mierla forest fire, Guadalajara province; started 16 July 2026; over 32,000 hectares; second-largest wildfire in Spain this century, behind the 2025 Larouco-Quiroga-Oencia fire (37,765 ha per Copernicus); "stabilisation phase" on 23 July.
- [El Debate, 22 Jul 2026: before and after image from Copernicus](https://www.eldebate.com/espana/castilla-la-mancha/20260722/herida-32000-hectareas-vista-desde-espacio-brutal-antes-despues-mierla_442543_amp.html) supports: about 32,000 hectares (29,000 in a 21 Jul article); before image 13 July, after image 21 July; affected villages Riofrío del Llano, Cardeñosa, Santiuste, Rebollosa del Jadraque and Angón, residents beginning to return; woodland, crops and pasture before the fire.
- [elDiario.es, 30 Jul 2026](https://www.eldiario.es/castilla-la-mancha/provincias/guadalajara/incendio-mierla-compromete-gravemente-riqueza-ecologica-ornitologica-sierra-norte-guadalajara_1_13418409.amp.html) supports: nearly 33,000 hectares, about 95 % inside the Parque Natural de la Sierra Norte de Guadalajara (SEO/BirdLife).
- [Wikipedia: Riofrío del Llano](https://en.wikipedia.org/wiki/Riofr%C3%ADo_del_Llano) 41.138 N, 2.825 W; [Wikipedia: Angón](https://en.wikipedia.org/wiki/Ang%C3%B3n) about 41.07 N, 2.85 W. No coordinates for La Mierla itself were found.
- Our numbers: `docs/eda/events2026/la-mierla-fire-2026.json` and `.png`. HV drop inside the 9 km disc: 0.05, 0.08, 0.09 on 26 Jul, 31 Aug, 18 Oct, against about 0 outside and in the control interval; the disc is among the darkest 0.2 % (26 Jul, 31 Aug) and 1 % (18 Oct) of all equal discs. The disc was located from the data; the published villages lie 15 to 25 km to the east, so the independent check is weak. 7 Oct and 18 Oct previews carry QC warnings (widespread HH/HV difference).
- Not used: evacuation counts, casualty counts, the 13,000 to 14,400 hectare figure in one search result (does not match).


## Burgohondo / Navaluenga fire, Ávila, Spain, July 2026 (avila-burgohondo-fire-2026)

Opened 15 Nov 2026. Burn place; water layer is zero.

- [El Español, 26 Jul 2026](https://www.elespanol.com/castilla-y-leon/region/avila/20260726/incendio-burgohondo-hectareas-quemadas-mayor-historia-espana/1003744334616_0.html) supports: Burgohondo (Ávila) about 50,000 hectares per Minister Sara Aagesen; about 77,000 hectares across the incidents in operational situation 3 per Protección Civil; Madrid more than 25,000 and Toledo more than 2,000 hectares, managed as one fire because their fronts are connected; Aagesen's wording "el mayor incendio forestal de la historia reciente en nuestro país"; no residents had died. The article does not give the ignition date.
- [El Debate, 30 Jul 2026](https://www.eldebate.com/espana/castilla-y-leon/20260730/perimetro-incendio-burgohondo-avila-esta-casi-consolidado-poca-llama-viva-nula_445333.html) supports: 55,000 hectares (photo caption), perimeter "casi consolidado con poca llama viva o nula", Cecopi at Navaluenga.
- [Wikipedia: Navaluenga](https://en.wikipedia.org/wiki/Navaluenga) 40 deg 24' 35'' N, 4 deg 42' 29'' W (40.410, -4.708). [Wikipedia: Burgohondo](https://en.wikipedia.org/wiki/Burgohondo) in the Valle del Alberche (no coordinates obtained).
- Not opened (search summaries only): first alerts 24 July, 71 km perimeter and 9,000 ha on 24 July, 160 km black landscape. The story therefore gives no ignition date.
- Our numbers: `docs/eda/events2026/avila-burgohondo-fire-2026.json` and `.png`. The east-of-Navaluenga disc is the darkest HV disc in the frame (0.03 to 0.12 percentile) on 19 Sep, 1 Oct and 13 Oct against about 0 in the pre-fire control. The 7 Sep preview is corrupt and was excluded; the 26 Aug preview has a QC flag. The darkest discs were found in the data; the independent location check is the match with Navaluenga and the east-west direction of the fire.
