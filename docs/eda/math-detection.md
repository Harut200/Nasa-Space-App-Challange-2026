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

1. **Footprint cleanup.** The previews draw some of the area outside the swath at the darkest value
   (1/255), which is the same value as calm water. Floor-valued pixels connected to the footprint edge
   are removed, and the footprint is then shrunk by 2 px (`nodata_floor=0.01`, `edge_erode_px=2`).
   Without this a 24 px strip on the east edge of Tonle Sap was classed as "permanent water".
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
   Gains found: 0.94–1.04, i.e. no large stretch drift. Effect on results: see tables.
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
* `fv_hh_rise=0.15`: picked between 0.12 and 0.20 using Sevan/Ararat (no flooded forest there) as a
  negative control. **Very sensitive**: Tonle Sap peak flooded-veg area is 1,084 / 625 / 156 km² at
  0.12 / 0.15 / 0.20. Treat flooded-veg areas as order-of-magnitude.
* CUSUM `step_min=0.12`, `t_min=5`: on the synthetic test with heavy (8-look) speckle, far-away pixels
  called "stable" go from 79.5 % (no t gate) to 97.9 % (t ≥ 5); at 30 looks 99.8 % → 99.95 %.

## Results (normalisation on; "no-norm" = same run without step 3)

Tonle Sap (km²; valid footprint ≈ 36,300–37,700 km²)

| date | water | water no-norm | flooded veg | fl. veg no-norm | wind-suspect |
|---|---|---|---|---|---|
| 2026-06-19 | 2238 | 2235 | 1 | 1 | 0 |
| 2026-07-01 | 2299 | 2293 | 1 | 1 | 13 |
| 2026-07-13 | 2313 | 2312 | 34 | 35 | 4 |
| 2026-07-25 | 2425 | 2419 | 101 | 104 | 6 |
| *gap* | | | | | |
| 2026-08-18 | 2834 | 2832 | 295 | 227 | 73 |
| 2026-08-30 | 2984 | 2997 | 625 | 413 | 3 |
| 2026-09-11 | 3217 | 3235 | 544 | 303 | 15 |
| 2026-09-23 | 3554 | 3557 | 278 | 177 | 23 |
| 2026-10-05 | 3551 | 3530 | 210 | 175 | 20 |
| 2026-10-17 | 3870 | 3841 | 161 | 130 | 0 |

Classes: stable 27,394 · permanent water 2,180 · seasonal open water 1,830 · flooded veg 514 ·
other change 6,986 km² (of which 5,125 km² got darker, 1,861 brighter).

Lake Sevan (km²; real lake 1,242 km²)

| date | water | water no-norm | flooded veg | fl. veg no-norm | wind-suspect |
|---|---|---|---|---|---|
| 2026-06-20 | 1238 | 1236 | 0 | 0 | 0 |
| 2026-07-02 | 1225 | 1223 | 0 | 0 | 5 |
| 2026-07-14 | 1222 | 1218 | 1 | 2 | 5 |
| 2026-07-26 | 1270 | 1273 | 0 | 0 | 1 |
| 2026-08-19 | 1252 | 1246 | 1 | 2 | 2 |
| 2026-08-31 | 1221 | 1212 | 99 | 95 | 83 |
| 2026-09-12 | 1183 | 1181 | 7 | 4 | 66 |
| 2026-09-24 | 1260 | 1264 | 30 | 13 | 22 |
| 2026-10-06 | 1311 | 1312 | 1 | 0 | 1 |
| 2026-10-18 | 1284 | 1294 | 1 | 0 | 0 |

Ararat valley: water 7–26 km² (small reservoirs/ponds), flooded veg 0–190 km² with a single spike on
2026-08-31 (same date as Sevan's spike, see limits); classes: stable 3,703, other classes ≤ 33 km² each.

Normalisation changes water area by < 1 % on every date, but flooded-veg area by up to ~80 %
(Tonle Sap 2026-09-11: 544 vs 303 km²), because flooded veg is a *difference* from June and a 1–2 %
gain shifts many borderline pixels.

## What the figures show (`docs/eda/math/`)

* `<area>_water_fv_grid.png`: each pass, blue = water, green = flooded veg, orange = wind-suspect.
  Tonle Sap: the lake plus a growing flood along the river south-east and the floodplain; green ring of
  flooded vegetation on the west/north-west shore, strongest late August–September.
* `<area>_change_date.png`: colour = first date of the new level. Tonle Sap: the lake itself is
  stable; changes ring outward from the lake, mostly dated 18 Aug → 23 Sep (the south-east river plain
  first, the outer north-west plain last, 23 Sep). Very little before the gap. This matches a late flood.
* `<area>_classes.png`: season classes with km² in the legend.
* `<area>_areas.png`: area per pass at true dates, outage shaded. Tonle Sap water rises from ~2,240 km²
  (June) to ~3,870 km² (17 Oct), most of it after mid-August; flooded veg peaks ~30 Aug then falls as
  water gets deeper (vegetation becomes open water).
* `<area>_triptych.png`: first vs last filtered HH and their difference (red = darker).

## Sevan and wind

Sevan's lake stays at the display floor in **HH** on every pass while HV rises up to 0.16 in Sept–Oct
(wind). Because the water rule keys on HH (HV limit is loose), Sevan's water area stays 1,183–1,311 km²
(−5 % … +6 % around the real 1,242 km²) with no spurious shrinkage: **handled for these previews**.
The residual dips (31 Aug, 12 Sep) are flagged orange: a 1-pixel ring along the north shore (looks like a
1-pixel geolocation shift of that preview) and the shallow north-west bay. At Tonle Sap the lake also
brightens a little in October, still well below the threshold. With calibrated dB data and a stronger
wind the HH rule may fail; then the orange flag is the warning, not a fix.

## Limits (please read before quoting numbers)

* **Uncalibrated previews.** 8-bit display brightness with an unknown stretch, not backscatter. Water sits
  at the 1/255 floor; anything quantitative about brightness steps is in "display units".
* **180 m pixels.** A 1-pixel shoreline error on Sevan's ~250 km perimeter is ~45 km². Sevan's
  "seasonal open water" (177 km²) is mostly this shoreline ring plus dark fields south-west of the lake;
  it is **not** evidence that the lake changed (it actually rose by a sub-pixel amount).
* **Thresholds were picked from the same data** (Sevan's known area; Sevan/Ararat as a negative control).
  There is no ground truth and no independent validation. The gamma0 knobs are untested.
* **Flooded vegetation is fragile**: x4 change in area between thresholds 0.12 and 0.20; the reference is
  only the first two (June/early July) passes, which may already be partly wet at Tonle Sap.
* **31 Aug 2026 spike** in Armenia (Sevan 99 km², Ararat 190 km² "flooded veg") is most likely rain-wet
  soil or irrigation raising HH, or a stretch change — not flooded forest. The class map needs ≥ 2 dates,
  which removes most of it, but the per-date number is inflated.
* **"Other change" is broad** (Tonle Sap 6,986 km²): rice/crop cycles, partly flooded fields that are darker
  but not below the water threshold, soil moisture. It is a "something changed here" layer, not a label.
* **The August gap.** No passes 27 Jul–10 Aug; a change in that window is dated 18 Aug. Changes on the last
  date (17 Oct) have only one "after" sample and are unconfirmed.
* **Change point = one step per pixel.** A flood that comes and goes, or a gradual ramp, gets a single
  (mid-ramp) date.
* **Speck removal / hysteresis** use iterative propagation; fine at these sizes, slow for very large frames.
* **Synthetic test noise**: the test uses 30-look gamma speckle (18 % relative noise, about twice what we
  measured on the previews). At 8 looks (35 %) the "far pixels stable" check falls to 97.9 %; the
  method needs the averaging that the 180 m previews already provide.
