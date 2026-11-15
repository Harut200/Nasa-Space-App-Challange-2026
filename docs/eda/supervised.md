# A water network trained on independent labels, tested on places it never saw

Code: `scripts/detect/supervised.py` · tests: `tests/test_supervised.py` · numbers: `docs/eda/supervised/results.json` ·
figures: `docs/eda/supervised/` · products: `data/processed/<area>/supervised/products.pt`.

## Short answer

* **Does it beat the math detector with terrain mask on places it never saw?** Mostly yes, by a little. On Lake Sevan
  (never seen in training) balanced accuracy is 0.994 against 0.989, and the lake area per date is much steadier (mean
  error 4 km² against 32 km²; on the rain night of 31 Aug and the breezy 12 Sep it reports 1,239 km² both times, where
  the math detector drops to 1,187 and 1,134 km²). But it puts **3× more false water on the land around the lake**
  (99 km² against 31 km², any of the last three dates). On Tonle Sap it is equal or slightly better (0.9985 against 0.995).
* **Does it beat the Markov detector, fusion v1 and fusion v2 (the app's current source)?** **No.** Fusion v2 has higher
  balanced accuracy on Sevan (0.9988 against 0.994), 17× less false water on the land (6 against 99 km²), and on the Tonle
  Sap floodplain it tracks the independent expected extent far better (7 of 10 dates inside the expected range against 1).
* **Should the app use it?** **No, not as the water source.** It is a useful independent check (a second opinion that never
  saw our detector's output), its sub-pixel fraction head is the best sub-pixel estimate we have, and its windy-date lake
  area is the steadiest of all methods, but it does not earn a place in `run.py` ahead of fusion v2.

## Why this network is different from every earlier one

All four earlier networks (U-Net v1/v2, deep autoencoder, time transformer) learned from **our own math detector's
labels**, so at best they copied it, mistakes included. This one learns from the **JRC Global Surface Water** record
(38 years of Landsat, 30 m, a different sensor and team), aggregated to our 180 m grid:

* **water** = the long-term record says permanent water (≥ 90 % of the cell has occurrence ≥ 90 % and is water in all 12
  months of 2021), minus a shoreline band;
* **land** = no water ever recorded within 3 pixels;
* **ignored** = everything else (shorelines, seasonal floodplain), except the weak land target below.

A lake that does not change is "water" on every date, including the windy and rainy dates on which radar loses part of
it; a dark mountain slope is "land" on every date. The test is always on a **place** the network never saw.

## Data

Ten areas, all ~10 preview dates (HH and HV display brightness, ~180 m), Jun–Oct 2026: tonle-sap, sevan, ararat,
bangladesh-haor, inner-niger, lake-chad, mekong-delta, pantanal, sindh-indus, sudd. Labelled water pixels: Sevan 33,181,
Tonle Sap 30,655, Lake Chad 23,050, Pantanal 2,302, Inner Niger 701, Sudd 664, Mekong 437; Ararat, Bangladesh haor and
Sindh have no permanent water (land labels only). Per-pass quality verdicts (`qc.json`) are honoured: Sevan's and Ararat's
6 Oct pass (faulty HV) is dropped from training and from scoring of **every** method; in the products that date is
predicted with the failed channel replaced by its per-pixel median over the good dates (flagged `qc_imputed_dates`).

Main split: **train on 8 places, test on Sevan and Tonle Sap** (neither seen, not even for early stopping: validation
is the east 15 % strip of each training place).

## Model and training (this machine: Apple M5, 24 GB, MPS)

* Residual U-Net, width 32, depth 3, **2,057,858 parameters**, two heads (water probability; sub-pixel water fraction).
* Inputs (reported variant `jrc`): log of the median-normalised **HH** on the date, season min/mean/max/std of HH, HH of
  the previous and next date, slope and relative height (7 channels). Every channel comes from radar or the DEM; none
  from our detector.
* Augmentation: synthetic floods/recessions pasted from real permanent water of the same scene, synthetic wind on
  permanent water (label stays water), gains, speckle, flips/rotations.
* Loss: class-balanced cross-entropy on water/land labels + fraction regression on stable shorelines + a **weak land
  target** (weight 1) on ignored pixels where JRC saw water less than 10 % of the time in 1984–2021.
* 500 iterations × 16 patches of 128 px, early stopping on the validation strips: **150 s** of training (best at
  iteration 300); evaluation with 200 block-bootstrap resamples and the logistic-regression baseline ≈ 2 min more.

```
.venv/bin/python -m scripts.detect.supervised build                        # caches (done once, ~5 min for 10 areas)
.venv/bin/python -m scripts.detect.supervised train --holdout sevan,tonle-sap --products   # ~4.5 min
.venv/bin/python -m scripts.detect.supervised rescore                      # re-score against current baselines, ~2 min
.venv/bin/python -m scripts.detect.supervised loao --redo --boot 50 --only sevan,lake-chad # ~3.5 min per place
.venv/bin/python -m scripts.detect.supervised ablate --variants jrc_noseason,jrc_noneigh   # ~3 min per variant
.venv/bin/python -m scripts.detect.supervised figures
.venv/bin/python -m pytest -q tests/test_supervised.py                     # 6 tests, ~25 s
```

## Held-out table (network trained on 8 other places; same pixels and dates for every method)

Anchors = JRC permanent-water pixels (recall) and never-water pixels (false-alarm rate), all kept dates. Brackets: 95 %
block-bootstrap interval. "False far water" = water more than 3 px from any water JRC ever recorded, on any of the last
three dates. Methods marked * were fitted or tuned using Sevan and/or Tonle Sap themselves (fusion v1 fit areas include
both; the math detector's knobs and the terrain mask were tuned there), so for them this is **not** a held-out test.

### Lake Sevan (1,242 km²)

| method | recall | false-alarm rate | balanced acc. [95 %] | false far water km² | largest body: mean abs. error vs 1,242 km² | 31 Aug / 12 Sep km² |
|---|---|---|---|---|---|---|
| **this network (never saw Sevan)** | 0.9998 | 0.0121 | **0.9938** [0.9917, 0.9955] | 98.6 | **4.2** | 1,239 / 1,239 |
| math detector * | 0.9834 | 0.0129 | 0.9852 [0.9777, 0.9902] | 91.5 | 31.7 | 1,187 / 1,134 |
| math + terrain mask * | 0.9834 | 0.0054 | 0.9890 [0.9828, 0.9936] | 30.9 | 31.7 | 1,187 / 1,134 |
| seeded self-calibrating (crossscene) | 0.9767 | 0.0000 | 0.9884 [0.9831, 0.9945] | 0.0 | 42.8 | 1,229 / 1,107 |
| hidden Markov model * | 1.0000 | 0.0079 | 0.9961 [0.9923, 0.9984] | 23.0 | 6.7 | 1,235 / 1,235 |
| fusion v1 * | 0.9998 | 0.0027 | 0.9985 [0.9971, 0.9996] | 8.1 | 10.1 | 1,231 / 1,231 |
| **fusion v2 (app)** * | 0.9997 | 0.0020 | **0.9988** [0.9978, 0.9997] | 5.9 | 8.6 | 1,234 / 1,234 |
| U-Net v2 (learned the math detector) * | 0.9978 | 0.0082 | 0.9948 | 54.2 | 12.3 | 1,229 / 1,208 |
| per-pixel logistic regression, same inputs, same labels | 0.9997 | 0.0024 | 0.9987 [0.9974, 0.9996] | 7.0 | 9.9 | 1,232 / 1,232 |

Sub-pixel fraction on Sevan's stable shoreline pixels (106 px): fraction-head RMSE **0.113** (bias +0.025), against 0.39
for the hard mask. The analytic sub-pixel method reported RMSE 0.354 in `subpixel.md` on its own pixel set (not the same
pixels; the comparison is indicative only). The pretrained L-band encoder (frozen backbone, same labels) reached 0.912 on
held-out Sevan with 74 km² false far water: this purpose-built network is clearly better than that (0.994).

### Tonle Sap

| method | balanced acc. [95 %] | false far water km² | km² 19 Jun → 17 Oct | dates inside expected range | IoU with expected set | mean abs. error vs expected km² |
|---|---|---|---|---|---|---|
| expected (altimeter + Landsat, no radar) | | | 2,458 → 5,421 (17 Oct range 4,198–7,222) | | | |
| **this network (never saw Tonle Sap)** | **0.9985** | 3.1 | 2,494 → 3,784 | 1 | 0.625 | 748 |
| math (+ terrain) * | 0.9952 | 0.2 | 2,234 → 3,837 | 1 | 0.651 | 900 |
| seeded self-calibrating | 0.9965 | 0.5 | 2,301 → 4,389 | 3 | 0.655 | 713 |
| hidden Markov model * | 0.9974 | 0.6 | 2,289 → 3,326 | 1 | 0.647 | 1,025 |
| fusion v1 * | 0.9983 | 0.0 | 2,207 → 3,120 | 1 | 0.656 | 1,141 |
| **fusion v2 (app)** * | 0.9976 | 5.3 | 2,281 → 5,046 | **7** | **0.666** | **366** |
| per-pixel logistic regression | 0.9930 | 97.1 | 5,511 → 5,529 | 3 | 0.396 | 1,769 |

Rank agreement of the number of wet dates with long-term occurrence on the floodplain: network 0.43, math 0.52,
fusion v2 0.54 (higher is better).

## The floodplain overcount: diagnosis and fix

The `supervised` product the hydrology lane saw said **7,123 km²** of open water on 19 Jun (expected 2,458). That came
from the first model (HH + HV inputs, no weak target). Diagnosis on Tonle Sap:

* 94 % of the excess lay in the **ignored** zone, almost all of it (622 of 790 km² with the 9-place model) on pixels JRC saw
  as water **less than 10 % of the time** in 38 years: never supervised, so the network extrapolated there.
* Those pixels are moderately dark (HH ≈ 0.46 × scene median, HV ≈ 0.34), the same appearance as the Lake Chad and
  Pantanal "permanent water" it learned from (HH 0.29–0.41 × median, often vegetated or wind-roughened). Not flooded
  vegetation: the Markov detector flags only 0–104 km² of it as flooded vegetation.
* The **same mistake** made Sevan fail (balanced accuracy 0.75–0.87, 700–800 km² false far water): the surrounding steppe
  has an HV **darker than the lake** (HV of the calm lake sits on the preview noise floor, 0.64 × median; the flat land
  is 0.41 × median), so a network that leaned on HV called flat land water. Slopes were not the problem (false-alarm rate
  44 % on slopes < 2°, 2 % on slopes > 20°).

Fixes tried (all trained on the same 8 places, 500 iterations, scored on Sevan and Tonle Sap; single seed):

| variant | Sevan BA | Sevan false far km² | Tonle BA | Tonle 19 Jun km² (expected 2,458) |
|---|---|---|---|---|
| original inputs (HH + HV) | 0.749 | 725 | 0.985 | 7,845 |
| + random smooth HV gain field (augmentation) | 0.759 | 698 | 0.803 | 3,890 |
| + weak land target (occurrence < 5 %, weight 0.2) | 0.713 | 593 | 0.988 | 4,561 |
| **HH only** | 0.857 | 92 | 0.826 | 3,204 |
| HH only + weak target (5 %, 0.2) | 0.966 | 175 | 0.994 | 4,562 |
| same, second seed | 0.978 | 190 | 0.996 | 3,632 |
| HH only + weak target (5 %, 0.2) + terrain channels | 0.985 | 156 | 0.995 | 4,246 |
| **HH only + terrain + weak target (10 %, weight 1) = `jrc`, reported** | **0.994** | **99** | **0.9985** | **2,494** |

Is the weak target still independent of our detector? **Yes**: it uses only the JRC long-term occurrence (Landsat), no
radar product of ours. Its cost: it says "almost-never-water pixels are land", which is wrong in a record flood year. That
is likely part of why the network, like the math detector, under-reports the October peak (3,784 km² against 5,421
expected; most of the missing water is flooded forest/shrub that is not open water in radar either). Before/after on
Tonle Sap 19 Jun: **7,123 → 2,494 km²** (expected 2,458); 17 Oct: 7,708 → 3,784 km² (expected 5,421, lower bound 4,198).

**Honest caveat on selection:** the recipe was chosen after seeing these Sevan/Tonle Sap scores, so the main table is
somewhat optimistic for the network. The leave-one-place-out runs below on places not used for that choice are the
check.

## Leave-one-place-out (trained on the 9 other places)

Only two places were re-run with the reported recipe (time); 50 bootstrap resamples. The other eight LOAO rows in
`results.json` → `loao` are from the earlier HH + HV recipe (variant `fast`) and are kept only for the record.

| held-out place | method | recall | false-alarm rate | balanced acc. | false far water km² | other |
|---|---|---|---|---|---|---|
| Sevan (trained on 9 incl. Tonle Sap) | **this network** | 0.977 | 0.0028 | 0.987 | 15.7 | lake MAE 51 km²; 31 Aug / 12 Sep 1,193 / 1,152 km² |
| | math + terrain | 0.983 | 0.0054 | 0.989 | 30.9 | MAE 32 |
| | seeded self-calibrating | 0.977 | 0.0000 | 0.988 | 0.0 | MAE 43 |
| | fusion v2 | 0.9997 | 0.0020 | **0.9988** | 5.9 | MAE 9 |
| | logistic regression | 0.9999 | 0.0025 | 0.9987 | 7.5 | MAE 8 |
| Lake Chad (trained on 9) | **this network** | 0.701 | 0.0016 | **0.850** | 10.1 | fraction RMSE 0.50 |
| | math (+ terrain) | 0.007 | 0.0008 | 0.503 | 5.4 | the fixed-threshold detector misses the lake |
| | seeded self-calibrating | 0.984 | 0.0000 | 0.992 | 0.0 | |
| | fusion v2 | 0.987 | 0.00002 | **0.994** | 0.1 | |
| | logistic regression | 0.999 | 0.026 | 0.986 | 134.5 | |

Two things stand out. (1) Adding Tonle Sap to the training places **moved** the Sevan trade-off rather than improving it:
far less false land water (16 against 99 km²) but the lake is lost on the breezy date (1,152 km²), so the network's
"steady lake on rough dates" is not a stable property of the recipe. (2) On Lake Chad, a place with a bright, partly
vegetated lake unlike most training lakes, the network finds only 70 % of the permanent water; the self-calibrating
seeded detector and fusion v2 find 98–99 %. The network beats the plain math detector there, but not the detectors the
app uses.

## Ablations of the reported recipe and learning curve

Single-seed runs on the main split differ by up to ±0.01 balanced accuracy and by ±1,000 km² on the Tonle Sap low-water
area between seeds (see the two seeds above), so only large differences mean anything.

* Season-statistics and temporal-neighbour channels (earlier HH + HV model): removing the season channels **raised** Sevan
  from 0.67 to 0.82 and removing the neighbour channels changed little on Sevan (0.70) but **dropped** Tonle Sap to 0.76.
  Not re-run for the HH-only recipe (out of time).
* Terrain channels: +0.02 on Sevan (0.966/0.978 → 0.985), little change elsewhere.
* Model size, and the learning curve by number of training places (1, 2, 4, 8): **not run**: the machine time went to
  diagnosing and fixing the two failures above. What we can say: with the earlier HH + HV recipe, 8 places (no Tonle Sap)
  gave 0.75–0.87 on Sevan and 9 places (adding Tonle Sap) gave 0.915; with the reported recipe 8 places gave 0.994 and 9
  gave 0.987: adding a place shifts the errors around rather than steadily reducing them.

## Figures

* `training_curves.png`: loss and validation balanced accuracy of the reported model.
* `sevan_windy.png`: Sevan on 12 Sep (breeze): radar, math, math + terrain, Markov model, this network, reference.
* `sevan_area.png`: Sevan largest water body per date for all methods vs 1,242 km².
* `sevan_slope_false_water.png`: false water on the land around Sevan per method.
* `tonle_sap_area.png`: Tonle Sap open water per date for all methods with the expected range.
* `reliability.png`: reliability of the predicted probability on held-out anchors.
* `loao_heatmap.png`: leave-one-place-out.

## Verdict

It is the first network in this project that did not just copy our detector, and on places it never saw it is about as
good as the math detector with terrain mask: better on the lake on rough-surface dates, worse on false water around the
lake. It loses to fusion v2 (the app's source) on every summary number except the windy-date lake area, and a simple
per-pixel logistic regression on the same inputs and labels is as good or better on Sevan, and on unseen Lake Chad
it finds only 70 % of the lake (fusion v2: 99 %), so the spatial network adds
little beyond its HH time-series features. The app should keep fusion v2; this product is worth keeping only as an
independent cross-check and for its sub-pixel fraction head.

## Limits

* Labels and test anchors come from the **same reference type** (JRC) at different places; agreement with JRC is not
  agreement with 2026 truth. The reference ends in 2021.
* Only **unambiguous** pixels are labelled and tested (deep lake interiors, land far from any water). Shorelines and
  seasonal floodplains, where the hard decisions are, are not in the anchors; there only the hydrology comparison
  (one place) speaks.
* Inputs are **uncalibrated previews** (display brightness); HV had to be dropped because its noise floor varies by
  scene and across the swath. A calibrated product would likely change the picture.
* Few water-rich training places (Lake Chad, Pantanal, Mekong, Inner Niger, Sudd); three have no permanent water.
* The recipe (HH only, weak target 10 %/weight 1) was chosen while looking at the Sevan/Tonle Sap scores; single seeds;
  the learning curve and model-size ablation were not run.
* The Ararat product (`data/processed/ararat/supervised/products.pt`) is still from the earlier `fast` leave-one-out model;
  only Sevan and Tonle Sap were regenerated with the reported model. Re-run with
  `loao --redo --only ararat` then `predict --area ararat --variant jrc`.
* The earlier scoreboard numbers for `supervised` (72 % on Sevan, 554 km² lake error) were for an early 500-iteration
  HH + HV model; the products in `data/processed/{sevan,tonle-sap}/supervised/` are now from the reported model.
* Several baselines (math knobs, terrain mask, fusion v1 fit areas) used Sevan and Tonle Sap in their tuning, which
  favours them in this table.
