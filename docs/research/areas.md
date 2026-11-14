# Places in the app: how they were chosen, what the radar shows, sources

All places use the free NISAR GCOV **preview** images (uncalibrated, about 180 m per pixel),
17 June to 24 October 2026, with the instrument outage 27 July to 10 August. Water areas in the
app's chart come from uncalibrated previews and must not be quoted as measurements.

## Add a new place (one command)

```
.venv/bin/python scripts/ingest/add_area.py <id> --name "Name, Country" --bbox W S E N [--change-pol hh|hv]
```

What it does: searches ASF (no login) for every PROVISIONAL GCOV pass over the box, groups them by
track/frame/direction, keeps "Full" HH+HV passes in the most common bandwidth mode (highest `crid`
per date), picks the group whose footprint contains the whole box (plus 0.03 deg margin) on every
date with the most dates (at least 6). If none contains the box it shrinks the box to the largest
sub-box inside every footprint (short side at least 0.8 deg) and says so. Then: writes
`areas/<id>/area.json`, downloads previews (3 in parallel, retries), runs `fetch_previews.py`,
drops previews that are much smaller than the others (corrupt-preview heuristic, < 0.85 x median
size), builds the terrain mask (`scripts.detect.terrain`; skipped with a warning if the elevation
download fails), runs `detect/run.py` and `export.py`, and prints a summary.

Options: `--list` (candidate table only), `--dry-run` (choose, write nothing), `--force`
(re-choose for an existing id), `--exclude YYYY-MM-DD ...` (drop dates you have seen are bad),
`--keep-all` (do not drop size-flagged previews). Re-running an existing id reuses its
`area.json` and skips downloads that exist. After adding: LOOK at the `hh` layer for every date,
write `areas/<id>/story.json` (story + spots), then re-run `.venv/bin/python scripts/detect/run.py <id>`
and `.venv/bin/python scripts/ingest/export.py <id>` so the chart has the spots, and place the id in
`src/app/public/data/index.json` (export appends it at the end, after `demo`).

Tests: `.venv/bin/python -m pytest tests/test_add_area.py`.

## Candidates

Box = box actually used (after shrinking). Dates = HH+HV "Full" dates in one mode, after removing
corrupt previews. Order below = order in `index.json`.

| id | Name | Box (W S E N) | Track / frame / dir | Dates | Mode | Kept? | What the radar shows | Known artefacts |
|---|---|---|---|---|---|---|---|---|
| inner-niger | Inner Niger Delta, Mali | -5.0 14.02 -3.74 15.58 (shrunk) | 145 / 9 / asc | 8 (24 Jun–10 Oct) | 20+5 | Kept (strong) | Dry floodplain in June; dark water spreads Aug–Sep; largest jump 28 Sep→10 Oct; still rising at the end | 6 Jul preview corrupt (striped) → removed; dark linear dunes in the north-west flagged as water on some dates |
| poyang | Poyang Lake, China | 115.7 28.5 117.0 29.66 (shrunk) | 140 / 16 / asc | 7 (5 Jul–9 Oct) | 20+5 | Kept (strong) | One wide lake in July; from August the western/southern lake bed drains, channels and pools emerge; drained edges marked flooded vegetation | 23 Jun and 22 Aug previews corrupt → removed (23 Jun also broke the georeferencing of the whole stack) |
| bangladesh-haor | Haor wetlands, Bangladesh | 90.6 24.2 91.5 25.28 (shrunk) | 69 / 14 / asc | 10 (18 Jun–16 Oct) | 40+5 | Kept | Haor basin flooded from the first date; western margins drain and green up late Sep–Oct; slow shrinking of open water | 30 Jun wind-roughened (orange "usually water"); a bright rectangular processing block on 12 Jul |
| okavango | Okavango Delta, Botswana | 22.48 -20.0 23.8 -18.6 (shrunk) | 172 / 168 / asc | 9 (26 Jun–12 Oct) | 20+5 | Kept | Floodplains brighten from July in the upper delta, the brightening moves down to the east/south by Sep ("when" layer is a clean gradient) | Open-water layer finds only scattered pools (flooded grass is grey, not black); drying vs plant growth not separable |
| urmia | Lake Urmia, Iran | 45.0 37.5 45.98 38.3 (shrunk, north half) | 85 / 21 / asc | 9 (20 Jun–18 Oct) | 20+5 | Kept | Open lake black all season; western/northern shores brighten Aug–Sep as the edge retreats | Wet salt flats likely included in "water"; 18 Oct brightening south of causeway could be wind (single date); bright square artefact on 2 Jul |
| sindh-indus | Indus plain, Sindh, Pakistan | 67.92 26.0 69.0 27.52 (shrunk) | 41 / 15 / asc | 10 (17 Jun–15 Oct) | 40+5 | Kept (agriculture) | No 2026 flood; rice fields west of the Indus darken Jul–Aug (flooded paddies) then become very bright Sep–Oct | Dark sand desert east of the river correctly not called water; river channel thin at 180 m |
| mekong-delta | Mekong floodplain, Vietnam–Cambodia | 105.3 10.2 106.2 11.5 (shrunk) | 47 / 83 / desc | 10 (17 Jun–15 Oct) | 40+5 | Kept (moderate) | From mid-Aug the plain north of the Mekong darkens field by field, mostly Sep–Oct; a few patches become open water | Most flooding is "got darker", not open water (shallow, diked fields); 21 Sep dip in water area |
| rondonia | Rondônia forest frontier, Brazil | -62.8 -10.06 -61.42 -8.8 (shrunk) | 60 / 173 / asc | 6 (30 Jun, 17 Aug–16 Oct) | 20+5 | Kept (forest; HV) | HV shows the "fishbone" of old clearings clearly; a few small forest blocks drop in HV late Aug–early Oct | 18 Jun, 12 Jul, 24 Jul previews corrupt → removed; only 6 dates; "when" layer marks most cleared land at 17 Aug (step from the single June date, probably dry-season pasture change), so read the change layer instead |
| nile-delta | Nile Delta, Egypt | 30.3 30.4 31.88 31.52 (shrunk) | 7 / 73 / desc | 9 (26 Jun–12 Oct) | 40+5 | Kept (agriculture, weaker) | Northern delta brightens from late June, peaks 13–25 Aug, falls back mid–late Sep (summer crop then harvest) | Open-water layer empty: lagoons (Burullus) and sea are grey, not black, in these previews |
| sudd | The Sudd, South Sudan | 30.0 6.5 31.42 7.78 (shrunk) | 86 / 5 / asc | 8 (20 Jun–18 Oct) | 20+5 | Kept (weakest) | Permanent swamp lakes stable; south-western floodplain pools fill mid-Aug; western grassland brightens then darkens | 6 Oct preview radiometrically wrong (water bright, land dark) → removed; dense swamp vegetation is bright white and not classed; noisy western plain |
| lake-chad | Lake Chad | 13.46 12.6 14.9 13.6 (shrunk) | 101 / 8 / asc | 10 | 40+5 | **Dropped** | Open southern pool is dark grey but NOT detected as water; dune troughs flagged as water on 20 Aug; change is dominated by crop/grass growth on the southern plains | Water layer would mislead |
| pantanal | Pantanal, Brazil | -58.0 -19.0 -56.66 -18.12 (shrunk) | 132 / 168 / asc | 8 (after removing corrupt 5 Jul) | 20+5 | **Dropped** | Large black lakes on the Bolivian border are NOT detected as water (the preview's brightness stretch puts them above the fixed thresholds, checked with and without the terrain mask); change shows lake margins drying | Water layer would mislead; 5 Jul preview flat grey; square artefact on the west edge |

Not tried (time): California Central Valley, a reservoir, Arctic/ice.

Corrupt previews: every bad preview seen (striped garbage, flat grey, half swath, inverted
brightness) was a PNG noticeably smaller than its siblings, which is why `add_area.py` now drops
those by size. This is a heuristic; the Sudd 6 Oct preview (1950 KB vs ~3850 KB) would be caught,
but a corrupt preview of normal size would not. Always look at the `hh` layer.

## Sources for facts used in the stories (opened 14 Nov 2026)

| Area | Claim | Source |
|---|---|---|
| bangladesh-haor | Haors are bowl-shaped backswamps in NE Bangladesh; flood about July–November; boro rice on exposed land in the dry season; early flash floods damage boro; support fisheries | https://en.wikipedia.org/wiki/Haor |
| bangladesh-haor | 13 July 2026: Kushiyara above danger level at Markuli (Sunamganj); FFWC warned flooding could worsen in Sylhet and Sunamganj | https://watchers.news/2026/07/13/floods-and-landslides-kill-51-affect-more-than-1-million-in-bangladesh/ (the 51 deaths / 1 million affected are mostly Chattogram division, so not used for the haor story) |
| inner-niger | Ecosystem services sustain two million people; at peak the flood covers an area the size of Belgium | https://www.wetlands.org/blog/sustaining-malis-inner-niger-delta/ |
| inner-niger | Rain in Guinea swells the Niger; western and southern edges not flooded until early to mid-October; dry season = network of lakes and channels | https://www.wetlands.org/blog/sustaining-malis-inner-niger-delta/ , https://en.wikipedia.org/wiki/Inner_Niger_Delta |
| mekong-delta | About 19 million people (2022 est.); 2011 rice output 54.8% of Vietnam's total | https://en.wikipedia.org/wiki/Mekong_Delta |
| sindh-indus | 55.7 million people (2023 census); province mostly arid except the irrigated Indus Valley | https://en.wikipedia.org/wiki/Sindh |
| sudd | Wet season may extend over 130,000 km²; levels fluctuate up to 1.5 m; Nuer and Dinka pastoralists; livestock and rain-fed agriculture | https://en.wikipedia.org/wiki/Sudd |
| okavango | Water from summer rainfall in the Angola highlands; delta in a Kalahari trough; 1,000th UNESCO World Heritage site (22 June 2014); flood-recession (molapo) farming; fishing, hunting, farming peoples | https://en.wikipedia.org/wiki/Okavango_Delta |
| urmia | Once ~6,000 km²; shrunk to 10% of former size by late 2017; causes drought, river dams, groundwater pumping; NASA imagery Sept 2025 "almost entirely dried up"; 2018 planting to slow salt-carrying wind | https://en.wikipedia.org/wiki/Lake_Urmia |
| poyang | Largest freshwater lake in China; flows into the Yangtze; ~200 km² in early 2012 (drought, Three Gorges); habitat for half a million migratory birds; up to 90% of Siberian cranes winter there | https://en.wikipedia.org/wiki/Poyang_Lake |
| rondonia | By 2003 around 70,000 km² of rainforest cleared; one of the states that deforests the most | https://en.wikipedia.org/wiki/Rond%C3%B4nia |
| nile-delta | Egypt's rice crop is constrained to the Nile Delta; typically harvested late August to mid-September; most rice irrigated with agricultural drainage water | https://ipad.fas.usda.gov/highlights/2016/01/Egypt/Index.htm |

Statements describing what our layers show are from our own outputs (previews), not external sources.
General radar physics statements (calm water dark, double bounce, HV volume scattering) follow
`docs/DETECTION.md` and `docs/research/science-brief.md`.

## A teammate must verify before submission

1. Every spot position on a satellite basemap. Spots were chosen from our own radar layers
   (pixels that are always water, change at a given time, or stay stable), not from a map. Labels
   that name places are the riskiest: Sunamganj town [91.395, 25.066], Nanchang [115.9, 28.68],
   Lake Burullus [30.9, 31.45], Eslami island [45.55, 37.85], Plain of Reeds [105.9, 10.75],
   "Cambodian side" [105.54, 11.097], Okavango "papyrus channel" [22.6, -19.0] and "sandveld"
   [23.6, -18.75], Sudd "dense swamp vegetation" [30.933, 7.32], Sindh "sand desert" [68.757, 26.571],
   Rondônia "intact forest" [-61.6, -9.85] and the three "forest block" spots (are they real new
   clearings? compare with a 2026 optical image or DETER/PRODES alerts).
2. Every sourced fact in the table above (open each link; Wikipedia figures in particular).
3. Interpretations that are ours and could be wrong: Sindh/Nile "rice" brightening (could be other
   crops); Okavango brightening = drying (could be plant growth); Urmia shore brightening = retreat
   (could be salt crust changes); Mekong "darker" = shallow flooding.
4. Names: "Rondônia forest frontier" box lies around Machadinho d'Oeste (not named in the app).

## AI disclosure

The `add_area.py` tool, the place selection, and the story text in `areas/*/story.json` for the
places above were drafted with AI assistance (Claude). The competition requires this to be
disclosed. Facts were taken only from the sources listed; human review is still required.
