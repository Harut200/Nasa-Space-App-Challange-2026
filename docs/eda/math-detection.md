# Math-only change detection (no learning)

Code: `scripts/detect/ops.py` (functions), `scripts/detect/detect_math.py` (CLI),
`tests/test_detect.py` (synthetic test). Run:

    .venv/bin/python -m scripts.detect.detect_math tonle-sap   # or sevan, ararat
    .venv/bin/python -m pytest tests/test_detect.py

Outputs: `data/processed/<area>/math/products.pt` and `docs/eda/math/<area>_*.png`.
Everything numeric is PyTorch (filters = `conv2d`, morphology = `max_pool2d`), on the Apple GPU (MPS).

## Input: "log-like intensity"

All steps work on one tensor `L` [dates, 2 channels (HH, HV), H, W]:

* previews today: `L` = the 0..1 display brightness as is (unknown, probably monotonic stretch);
* calibrated data later: `L = 10·log10(γ⁰ power)` in dB. Speckle filtering happens before this
  step, on linear power. NaN no-data is masked out automatically.

All thresholds are in `KNOBS["preview"]` / `KNOBS["gamma0"]` at the top of `ops.py`.
The `gamma0` values are textbook L-band starting points and **have never been tested**.

## Steps (one or two sentences each)

0. **Co-registration (done by the shared loader, `stack.py`, `align=True`).** Every date is shifted onto
   the first by image correlation. Before this, dates were misaligned by up to 6.9 px (Ararat) and 5.5 px
   (Sevan); that produced a fake "shoreline ring" of change around Sevan and most of the 31 Aug
   "flooded vegetation" in Armenia. All numbers below are from the aligned stack.
1. **Footprint cleanup.** The previews draw some of the area outside the swath at the darkest value
   (1/255), which is the same value as calm water. Floor-valued pixels connected to the footprint edge
   are removed; then only pixels imaged on **every** date are kept (`common_footprint=True`, so per-date
   areas are comparable), and that footprint is shrunk by 8 px (`edge_erode_px=8`; the EDA found the
   outer ~8 px at Tonle Sap at about half brightness). Without this a 24 px strip on the east edge of
   Tonle Sap was "permanent water" and the moving south edge was "change".
2. **Boxcar mean (mask-aware).** `m = conv(x·mask, 1) / conv(mask, 1)` over a k×k window, so invalid
   pixels do not drag the mean to zero (`box=3`, about 540 m).
3. **Multi-temporal speckle filter (Quegan).** `J_t = m_t · (1/N) Σ_k x_k / m_k`: each date's local
   mean times the time-average of "pixel / local mean". This keeps detail that is the same on every
   date while averaging the noise. Speckle is multiplicative, so we always average before differencing.
3b. **Relative normalisation (optional, on by default).** If one image was stretched a bit brighter, that
   must not become "change". Pick stable pixels (lowest 20 % temporal std, not water), and per date
   multiply by `gain = mean(reference)/mean(date)` where reference = temporal median (`norm_mode="gain"`
   because the preview black point is fixed; for dB data an additive offset is used instead). A
   least-squares line was tried first and dropped: noise biases its slope towards 0 and turned water grey.
   Gains found: 0.96–1.03, i.e. no large stretch drift. Effect on results: see tables.
4. **Water per date (hysteresis).** Water = HH below `weak=0.12` **and** 8-connected to a pixel below
   `strong=0.06`, with HV < 0.35 (loose); then specks < 4 px removed. HH is used as the main cue
   because wind brightens the lake surface in HV much more than in HH on these previews.
5. **Permanent-water prior.** "Darkest-ever" composite = 2nd-darkest HH of the season per pixel
   (2nd, not 1st, so one odd date does not count). Inside it, a brightening is never called flooded
   vegetation.
6. **Flooded vegetation per date (double bounce).** `HH_t − HH_ref > 0.15` and `HV_t − HV_ref < 0.06`,
   not water today, not in the prior; ref = mean of the first 2 dates (mid/late June, dry-ish season).
7. **Change maps.** `change_first[t] = L_t − L_0` and consecutive `L_t − L_{t−1}`, per channel.
8. **Temporal statistics.** Per pixel: mean, std, CV = std/mean, 95th − 5th percentile (`temporal_stats`).
9. **"When did it change" (CUSUM-style change point).** For each split k: `step = mean(after) − mean(before)`,
   score = `|step|·sqrt(n1·n2/(n1+n2))` (both channels); keep the best k if
   `|step| ≥ 0.12`, the t statistic `|step| / (sd·sqrt(1/n1+1/n2)) ≥ 5`, and ≥ 80 % of the dates before
   and after stay on their own side of the midpoint ("departs for good"). Output: date index of the
   first date of the new level, and the signed step.
10. **Morphology.** erode/dilate/open/close with `max_pool2d`; small specks removed by connected-component
    labelling (iterative max-label propagation).
11. **Season class map** (first match wins): permanent water (water on ≥ 90 % of dates) → seasonal open
    water (≥ 2 dates) → flooded vegetation (≥ 2 dates) → other change (change point found) → stable.
12. **Wind flag.** "Usually water" (water on the first date, the last date and ≥ 70 % of dates) that is not
    water on date t is shown in orange as *wind-suspect*. It is only flagged, never filled in.

## How the knobs were chosen (honestly: from the data itself)

* `water_hh_weak=0.12`, `strong=0.06`: Lake Sevan is 1,242 km² in reality; a 0.10–0.12 HH threshold
  gives 1,190–1,240 km² on the previews. The same numbers are then used at Tonle Sap. Not independent.
  Cross-checks from the EDA: a flood-fill of dark pixels connected to Sevan gives 1,231–1,238 km² on calm
  dates (we get 1,235–1,261 on calm dates); at Tonle Sap a second Otsu split inside the dark class gives
  ≈0.24, i.e. our 0.12 is the stricter side: "dark land" between 0.12 and 0.24 is not called water.
* `fv_hh_rise=0.15`: picked between 0.12 and 0.20 using Sevan/Ararat (no flooded forest there) as a
  negative control. **Very sensitive**: Tonle Sap peak flooded-veg area is 1,083 / 611 / 139 km² at
  0.12 / 0.15 / 0.20. Treat flooded-veg areas as order-of-magnitude.
* CUSUM `step_min=0.12`, `t_min=5`: on the synthetic test with heavy (8-look) speckle, far-away pixels
  called "stable" go from 79.5 % (no t gate) to 97.9 % (t ≥ 5); at 30 looks 99.8 % → 99.95 %.

## Results (normalisation on; "no-norm" = same run without step 3)

Tonle Sap (km²; common footprint 36,036 km²)

| date | water | water no-norm | flooded veg | fl. veg no-norm | wind-suspect |
|---|---|---|---|---|---|
| 2026-06-19 | 2234 | 2232 | 1 | 1 | 0 |
| 2026-07-01 | 2297 | 2290 | 0 | 1 | 10 |
| 2026-07-13 | 2310 | 2310 | 39 | 35 | 6 |
| 2026-07-25 | 2421 | 2419 | 98 | 102 | 6 |
| *gap* | | | | | |
| 2026-08-18 | 2802 | 2798 | 262 | 224 | 74 |
| 2026-08-30 | 2954 | 2966 | 611 | 421 | 2 |
| 2026-09-11 | 3147 | 3162 | 509 | 302 | 15 |
| 2026-09-23 | 3522 | 3523 | 264 | 180 | 21 |
| 2026-10-05 | 3541 | 3522 | 185 | 166 | 16 |
| 2026-10-17 | 3837 | 3808 | 144 | 131 | 0 |

Classes: stable 27,272 · permanent water 2,173 · seasonal open water 1,778 · flooded veg 492 ·
other change 7,189 km².

Lake Sevan (km²; real lake 1,242 km²; windy dates per EDA: 31 Aug and 12 Sep)

| date | water | water no-norm | flooded veg | fl. veg no-norm | wind-suspect |
|---|---|---|---|---|---|
| 2026-06-20 | 1245 | 1244 | 0 | 0 | 0 |
| 2026-07-02 | 1235 | 1233 | 0 | 0 | 7 |
| 2026-07-14 | 1235 | 1232 | 0 | 0 | 6 |
| 2026-07-26 | 1284 | 1286 | 0 | 0 | 1 |
| 2026-08-19 | 1261 | 1256 | 0 | 1 | 2 |
| 2026-08-31 | 1236 | 1233 | 6 | 6 | 36 |
| 2026-09-12 | 1197 | 1197 | 0 | 0 | 67 |
| 2026-09-24 | 1279 | 1284 | 0 | 0 | 5 |
| 2026-10-06 | 1329 | 1329 | 0 | 0 | 0 |
| 2026-10-18 | 1304 | 1309 | 0 | 0 | 0 |

Classes: stable 3,138 · permanent water 1,204 · seasonal open water 125 · flooded veg 0 · other change 31 km².

Ararat valley: water 10–31 km² (small reservoirs/ponds; max on 6 Oct, the date with the anomalous HV
channel), flooded veg 0–32 km² (31 Aug); classes: stable 3,724, others ≤ 18 km² each.

Normalisation changes water area by < 1 % on every date, but flooded-veg area by up to ~70 %
(Tonle Sap 2026-09-11: 509 vs 302 km²), because flooded veg is a *difference* from June and a 1–2 %
gain shifts many borderline pixels.

## What the figures show (`docs/eda/math/`)

* `<area>_water_fv_grid.png`: each pass, blue = water, green = flooded veg, orange = wind-suspect.
  Tonle Sap: the lake plus a growing flood along the river south-east and the floodplain; green ring of
  flooded vegetation on the west/north-west shore, strongest late August–September.
* `<area>_change_date.png`: colour = first date of the new level. Tonle Sap: the lake itself is
  stable; changes ring outward from the lake, mostly dated 18 Aug → 23 Sep (the south-east river plain
  first, the outer north-west plain last, 23 Sep). Very little before the gap. This matches a late flood.
* `<area>_classes.png`: season classes with km² in the legend.
* `<area>_areas.png`: area per pass at true dates, outage shaded. Tonle Sap water rises from ~2,230 km²
  (June) to ~3,840 km² (17 Oct), most of it after mid-August; flooded veg peaks ~30 Aug then falls as
  water gets deeper (vegetation becomes open water).
* `<area>_triptych.png`: first vs last filtered HH and their difference (red = darker).

## Sevan and wind

Sevan's lake stays at the display floor in **HH** on every pass while HV rises in Sept–Oct (wind).
Because the water rule keys on HH (HV limit is loose), Sevan's water area stays 1,197–1,329 km²
(−4 % … +7 % around the real 1,242 km²) with no spurious shrinkage: **handled for these previews**.
The dips on the two windy dates (31 Aug: 36 km², 12 Sep: 67 km² wind-suspect, mostly the shallow
north-west bay) are flagged orange, not filled in. The late values (1,304–1,329 km² in October) are
slightly above the real area; the lake did rise in 2026 but by far less than a pixel, so read this as
±5 % noise, not a measurement. With calibrated dB data and stronger wind the HH rule may fail; then
the orange flag is the warning, not a fix.

## Limits (please read before quoting numbers)

* **Uncalibrated previews.** 8-bit display brightness with an unknown stretch, not backscatter. Water sits
  at the 1/255 floor; anything quantitative about brightness steps is in "display units".
* **180 m pixels.** A 1-pixel shoreline error on Sevan's ~250 km perimeter is ~45 km². Sevan's
  "seasonal open water" (125 km²) is mostly the north-west bay (windy dates) and dark fields south-west of
  the lake; it is **not** evidence that the lake changed (it rose by a sub-pixel amount).
* **Armenia 2026-10-06 has an anomalous HV channel** (EDA). Our water rule barely uses HV, but HV-based
  numbers on that date (normalisation gain, flooded-veg HV test, change steps in HV) should not be trusted.
* **Thresholds were picked from the same data** (Sevan's known area; Sevan/Ararat as a negative control).
  There is no ground truth and no independent validation. The gamma0 knobs are untested.
* **Flooded vegetation is fragile**: x4–8 change in area between thresholds 0.12 and 0.20; the reference is
  only the first two (June/early July) passes, which may already be partly wet at Tonle Sap.
* **31 Aug 2026 in Armenia**: before co-registration this date showed 99 km² (Sevan) / 190 km² (Ararat)
  of "flooded vegetation"; after alignment it is 6 / 32 km². The rest (Ararat) is likely wet soil or
  irrigation raising HH, not flooded forest.
* **"Other change" is broad** (Tonle Sap 7,189 km²): rice/crop cycles, partly flooded fields that are darker
  but not below the water threshold, soil moisture. It is a "something changed here" layer, not a label.
* **The August gap.** No passes 27 Jul–10 Aug; a change in that window is dated 18 Aug. Changes on the last
  date (17 Oct) have only one "after" sample and are unconfirmed.
* **Change point = one step per pixel.** A flood that comes and goes, or a gradual ramp, gets a single
  (mid-ramp) date.
* **Speck removal / hysteresis** use iterative propagation; fine at these sizes, slow for very large frames.
* **Synthetic test noise**: the test uses 30-look gamma speckle (18 % relative noise, about twice what we
  measured on the previews). At 8 looks (35 %) the "far pixels stable" check falls to 97.9 %; the
  method needs the averaging that the 180 m previews already provide.
