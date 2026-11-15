# Science Brief

For Abel. Gathered 14 November 2026 from web sources. Confidence marks:

- **[S]** sourced: a link is given and was read or seen in search results
- **[1]** single source, or a source that could not be opened: check before putting it on screen
- **[T]** typical value from the radar literature, approximate, not verified against a paper
- **[?]** not found for 2026

Coordinates are approximate. Check every spot on a satellite basemap before giving it to Harutyun.

---

## 1. How L-band radar sees a surface

NISAR sends 24 cm microwaves and measures what comes back. Brightness depends on roughness, structure and water content, not colour. It works at night and through cloud. Passes are at about 06:00 and 18:00 local time, every 12 days.

| Surface | HH | HV | Why |
|---|---|---|---|
| Calm open water | Very dark (about −20 to −25 dB) | At the noise floor | Smooth surface reflects the pulse away |
| Wind-roughened water | Brighter, up to about −10 to −15 dB | Slightly brighter | Waves scatter some energy back |
| Forest | About −6 to −9 dB | About −12 to −15 dB | Scattering among branches and trunks |
| Flooded forest or reeds | Bright: 3–8 dB above dry forest | Little change | Pulse bounces off water, then trunk, straight back |
| Cleared forest, pasture | 1–4 dB lower | 3–6 dB lower | The volume that scattered is gone |
| Growing crop | Rises | Rises with biomass | More plant material |
| Harvested field | Drops | Drops sharply | Stubble or bare soil |
| Wet bare soil | A few dB brighter than dry | Small change | Water raises reflectivity |
| Buildings, embankments, boats | Very bright points | - | Corner reflections |

All values **[T]**. Do not print dB numbers from this table as facts; print what Harutyun measures.

**Which channel for what.** HH for water edges and flooding under vegetation. HV for forest loss and biomass.

**L-band versus C-band (Sentinel-1).** C-band's 5.6 cm waves stop at the top of a canopy: it maps open water well but misses flooding under trees and saturates over forest. L-band passes through. Worth one sentence, or a side-by-side if time allows.

**False signals: things that change brightness without the ground changing**

- Speckle: grainy noise in every radar image. Average over an area; never read single pixels.
- Rain or irrigation just before a pass: wet soil is brighter.
- Wind on lakes: a rough lake can look like land.
- Different tracks view from different angles. Compare only within one track.
- Mountain slopes: stretched bright faces and black shadows. Mask them; keep spots on flat ground.
- Upper-atmosphere effects at low latitudes on evening passes (Cambodia, Amazon): streaks, or HH leaking into HV.
- Radio interference: bright stripes.

**What radar does not show.** Algal blooms. Water quality. Changes smaller than a pixel. Saying so is part of the answer the challenge asks for.

---

## 2. Armenia: Ararat Valley and Lake Sevan

One satellite frame covers both. Ten passes: 20 June – 18 October 2026, with nothing between 26 July and 19 August.

### What changes, June to October

- **Wheat**: harvest June–July in the valley; fields go from standing crop to stubble **[1]** (general crop calendar; harvest reported from July in the south, https://www.azatutyun.am/a/24650170.html).
- **Fruit**: apricot and cherry mid-May to July, then peach, plum, grape (late August–October), apple, pear **[S]** (https://jam-news.net/workplace-an-orchard/). Orchard and vineyard canopy stays put; expect flat curves.
- **Vegetables and melons**: irrigated July–October; no sourced calendar found **[?]**.
- **Fish ponds**: permanent water, dark rectangles with bright banks.
- **Sevan**: level rises with snowmelt to early summer, then falls with irrigation releases.

### 2026 in particular

- Spring rainfall about 198 mm, 131 % of normal **[S]** (https://armenpress.am/hy/article/1252865/amp). Reservoirs nearly full in June **[S]** (https://www.ecolur.org/en/news/water/16427/).
- **Sevan level** **[S]** (https://www.ecolur.org/en/news/sevan/16413/, https://armenpress.am/en/article/1253868):

  | Date | Level (m) | Note |
  |---|---|---|
  | 1 Jan | 1900.22 | |
  | 4 Jun | 1900.60 | 16 cm below same date 2025 |
  | 21 Jun | 1900.72 | equal to 2025 |
  | 7 Jul | 1900.78 | +10 cm on 2025 |
  | 19 Jul | 1900.81 | +16 cm on 2025; +59 cm since January |

- June's rise described by the Hydromet Center as rarely seen, the level as second-highest since records began in 1927 **[1]**.
- Irrigation release limit for 2026: 143.6 million m³; legal ceiling 170 million m³; restoration target 1903.5 m **[S]** (https://www.ecolur.org/en/news/sevan/16450/).
- **Level for August–October: [?]** Get it from the Hydromet Center (https://armmonitoring.am/) or EcoLur's weekly reports.
- No 2026 drought report for the valley, and no 2026 bloom report, were found. **Do not write a drought story for 2026.**

### What radar should and should not show

- Wheat blocks dropping in HV and HH around the first three passes.
- Irrigated plots flickering bright after watering.
- Fish ponds dark throughout; a drained or abandoned pond would brighten.
- Orchards and vineyards steady.
- Sevan: dark, with wind patches. A level change of a few tens of centimetres moves the waterline only on the gentlest shores: likely near or below one pixel. Expect little; that is a finding.
- Blooms: invisible to radar. Optical satellites are the tool for that.

### Why it matters

- Artesian aquifer pressure in the valley has fallen by about two-thirds since the early 1980s **[S]** (https://iah.org/news/as-armenian-fish-farming-expands-a-pristine-aquifer-is-drying-up); artesian zone area down about 30 % **[S]** (https://evnreport.com/?p=14655).
- More than 200 fish farms **[S]** (https://e360.yale.edu/features/armenia-fish-farms-groundwater-agriculture). Water-use figures differ between sources (1.7 billion m³ abstraction in 2013 with 65 % to fish farms; about 681 million m³ a year for fish farms in a 2017 registry). Pick one and cite it.
- Sevan: about 1,242 km², about 33 km³ today against about 58 km³ before Soviet-era drawdown of roughly 20 m **[S]** (https://en.wikipedia.org/wiki/Lake_Sevan). It feeds irrigation in the valley: the link between the two halves of the story.
- IMF flagged growing water scarcity and irrigation losses in 2026 **[S]** (https://www.arka.am/en/news/economy/imf-has-highlighted-growing-water-resource-scarcity-and-infrastructure-problems-in-armenia-/).
- A "share of Armenia's fresh water" figure for Sevan was not found. Do not state one.

### Candidate spots

| Spot | Approx. lat, lon | Purpose |
|---|---|---|
| Mid-Sevan open water | 40.35, 45.30 | Control: should stay dark; shows wind |
| Shallow shore near Gavar / Lichk | 40.35, 45.13 | Shoreline |
| Martuni shore | 40.14, 45.30 | Shoreline |
| Sevan town / peninsula | 40.55–40.57, 44.95–44.99 | Landmark; built-up bright target |
| Fields near Masis | 40.07, 44.43 | Wheat and vegetables |
| Armavir orchards and fields | 40.15, 43.90 | May fall outside the cut-out: check |
| Lake Aknalich / Metsamor | 40.07, 44.15–44.20 | Groundwater-fed lake |
| Fish ponds, Ararat–Vedi | 39.90, 44.60 | Dark rectangles |
| Vineyards near Vedi | 39.91, 44.73 | Steady reference |

### Future uses of NISAR here

Crop and irrigation mapping every 12 days; the NISAR soil-moisture product; ground sinking over the depleted aquifer, measured by comparing the phase of two passes; a long shoreline record for Sevan; pond area monitoring.

---

## 3. Tonle Sap, Cambodia

Ten passes: 19 June – 17 October 2026, nothing between 25 July and 18 August.

### What changes

- Lake grows from about 2,500 km² in the dry season to somewhere between 10,000 and 16,000 km² at peak, depth from about 1 m to 7–10 m **[S]** (https://hess.copernicus.org/articles/26/609/2022/). Sources differ on the peak; give a range.
- The Tonle Sap River reverses and flows into the lake as the Mekong rises (around June–July), the lake usually peaks late September to mid-October, then drains **[T]**: typical calendar; no month-by-month table found.
- Wet-season levels in 2010–2019 were 1.05 m lower than in 1996–2009, about 20 % less lake area **[S]** (same paper).

### 2026

- Early season within the normal range; mid-June slightly below normal **[1]** (Stimson Mekong Dam Monitor, https://stimson.org/app-support/sea-mdm/home-en/).
- 3 August: Kratie 1.63 m below average, flood season about six weeks late, inflow to the lake about a third of normal, attributed to El Niño and upstream dams **[1]**.
- 19 August: disaster authority told provinces to prepare for rising water **[S]** (https://cambodianess.com/article/cambodian-authorities-told-to-heighten-flood-preparedness-as-water-levels-rise). October reports of further rises **[1]**.
- **Peak date and height: [?]** Get gauge readings for Kampong Luong and Prek Kdam from the Mekong River Commission (https://www.mrcmekong.org/) before calling the flood weak or strong.

### What radar should show

- Open water spreading: dark area grows.
- Flooded forest ring around the lake brightening strongly in HH from August, HV nearly unchanged. This is the signature image.
- Rice paddies: dark when flooded, brightening as plants grow.
- Floating villages and fishing structures: bright points.
- Beware wind on the open lake.

### Why it matters

- Fish catch estimates 150,000–550,000 tonnes a year; official 2020 figure 413,200 t **[S]** (https://www.fao.org/3/ad070e/ad070e0a.htm).
- Between 1 and 2 million people depend on the lake **[S]**.
- Fish and other aquatic foods: about 63 kg per person per year, second only to rice; 37 % of dietary iron **[S]** (https://www.fao.org/3/W8514E/W8514E04.htm).
- A smaller flood means less flooded forest, which is the nursery for the fishery.

### Candidate spots

| Spot | Approx. lat, lon |
|---|---|
| Prek Toal flooded forest | 13.1, 103.7 |
| Chong Kneas floating village | 13.27, 103.84 |
| Mid-lake open water (control) | 12.8, 104.1 |
| Kampong Luong | 12.2–12.3, 104.2: near the edge of the cut-out, check |
| Floodplain rice, Kampong Thom side | choose on basemap |

### Future uses

Flood-pulse tracking under forest every 12 days; effect of dams and dry years on peak extent; fisheries planning; water-level change inside flooded vegetation from phase comparison.

---

## 4. Rondônia, Brazil (only if time)

### What changes

Dry season June–October. Felling peaks mid-year, the cut wood dries, burning follows August–October, pasture after the first rains **[T]**.

### 2026

- Clearing alerts, August 2025 – July 2026: 2,874 km² across the Amazon, down 36.9 %; Rondônia 114 km², down 41 % **[S]** (https://agenciabrasil.ebc.com.br/en/meio-ambiente/noticia/2026-08/amazon-deforestation-falls-3687-over-past-year, https://news.mongabay.com/2026/08/amazon-deforestation-alerts-fall-to-lowest-level-since-2013-brazilian-data-show/).
- Fires: September hotspots up 74 % on 2025 **[1]**; first half of October up 62 %, drought, smoke over Manaus **[S]** (https://en.mercopress.com/2026/10/21/amazon-fires-rise-62-in-october-as-deforestation-falls-to-a-record-low).
- A second figure, 3,355 km², appears in one article and is probably the separate annual estimate. The two measure different things; do not mix them.

### What radar should show

HV falling 3–6 dB where forest is removed; HH falling less and often earlier **[T]** (Japanese ALOS-2 alert system: https://www.eorc.jaxa.jp/ALOS/en/kyoto/jan2017_kc23/pdf/2-07b_KC23_JJ-FAST_Watanabe.pdf). Burn scars are weak in L-band; fire data from NASA FIRMS shows them better. Rain on a fresh clearing can hide the drop.

### Where to look

Pick this year's clearings from Brazil's alert map (https://terrabrasilis.dpi.inpe.br/) rather than town centres. Candidate districts: Jaci-Paraná (−9.25, −64.4), edge of Karipuna Indigenous Land (−9.4, −63.6). Fire locations: https://firms.modaps.eosdis.nasa.gov/.

---

## 5. Bangladesh haor (reserve)

Pre-monsoon flash floods in April–May 2026 damaged the rice harvest; a monsoon flood on 13 July affected more than 1.1 million people with over 50 deaths **[S]** (https://watchers.news/2026/07/13/floods-and-landslides-kill-51-affect-more-than-1-million-in-bangladesh/, https://reliefweb.int/report/bangladesh/bangladesh-red-crescent-society-situation-report-1-northeastern-haor-region-flash-flood-30-april-2026). Passes on 12 and 24 July bracket the event; then the data gap. Files are large (7 GB). Spots: Tanguar Haor (25.15, 91.05), Sunamganj town (25.07, 91.40).

---

## 6. Open data to check the radar against

| For | Source |
|---|---|
| Sevan levels, weather | https://armmonitoring.am/ ; weekly reports on https://www.ecolur.org/ |
| Mekong and Tonle Sap gauges | https://www.mrcmekong.org/ |
| Long-term water extent | https://global-surface-water.appspot.com/ |
| Land cover | https://esa-worldcover.org/ |
| Amazon clearing and fire | https://terrabrasilis.dpi.inpe.br/ , https://firms.modaps.eosdis.nasa.gov/ |
| Optical imagery | Sentinel-2 via Copernicus Browser |

## 7. Reading

- NISAR applications and white papers: https://science.nasa.gov/mission/nisar/applications , https://science.nasa.gov/mission/nisar/societal-benefits ; list of white-paper PDFs at https://nisar-docs.asf.alaska.edu/documents/
- NASA ARSET NISAR training, 2026: https://www.earthdata.nasa.gov/s3fs-public/2026-06/arset-2026-nisar-part1.pdf
- SAR Handbook (NASA SERVIR, 2019): wetlands and forest chapters; search by title
- Mapping inundation with L-band: Chapman et al. 2015, https://www.mdpi.com/2072-4292/7/5/5415
- Crop time series with airborne L-band: https://www.research.lancs.ac.uk/portal/en/publications/full-year-crop-monitoring-and-separability-assessment-with-fullypolarimetric-lband-uavsar(54066e52-8de5-45f9-8022-1e5faabd8b77).html

## 8. Still to find

1. Sevan level, August–October 2026
2. Tonle Sap peak date and height, 2026
3. A sourced vegetable calendar for the Ararat Valley
4. One agreed figure for fish-farm water use
5. Exact coordinates for every spot
