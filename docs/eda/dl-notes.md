# Deep-learning notes: two small PyTorch models (no labels needed)

**Read this first:** we have **no ground truth**. Nothing below is "accuracy". The U-Net numbers measure how well the network **agrees with another algorithm** (v2: the math detector; v1: our loose physics rules). The autoencoder numbers measure how well it **reproduces its input**. Everything runs on uncalibrated 8-bit preview brightness at about 180 m per pixel.

Code: `scripts/detect/models.py` (models and k-means), `scripts/detect/weak_labels.py` (v1 rules and v2 `math_labels`), `scripts/detect/train.py` (command-line tool).
Weights: `data/processed/models/`. Products: `data/processed/<area>/dl/products.pt`. Figures: `docs/eda/dl/`.

We did not build a region-proposal detector such as R-CNN. Those find boxed objects, and floods and lakes are not boxed objects, so we use per-pixel models instead.

## 0. U-Net v2 (current default): trained on the math detector's labels

### Why v2
The v1 U-Net (sections 1–4 below) was trained on loose rule labels with ~60–80 % "not sure" pixels and scored 0.97–1.0 agreement with those rules, which only proved it copied them. Where the rules were unsure it guessed badly: Tonle Sap 17 Oct = 10,149 km² open water (math detector: 3,837) and 6,098 km² flooded vegetation (math: 144); Lake Sevan (true 1,242 km²) = 1,500–2,300 km² water because dark slopes and radar shadow look like water.

### What changed
1. **Labels** (`weak_labels.math_labels`): per date, from `ops.run_pipeline` (the math detector, `docs/eda/math-detection.md`): water → open water, flooded veg → flooded vegetation, **everything else valid → other land** (so dark slopes, shadow and dry dark fields are labelled as land). Ignore (255) only on a **1-px band on both sides of every class boundary**, on the math detector's **wind-suspect** pixels and on invalid / swath-edge pixels. Ignored share: 4.9 % Tonle Sap, 5.6 % Sevan, 1.4 % Ararat (v1: 58–78 %). Training now contains 59,594 (Tonle Sap), 13,617 (Sevan) and 10,792 (Ararat) "other land" pixel-dates darker than HH 0.15. Labels are cached in `data/processed/<area>/dl/math_labels.pt` (delete it to recompute; 3 s).
2. **Scenes**: Tonle Sap west 70 % (east 30 % held out, same split as v1) + Lake Sevan west two-thirds (east third held out) + all of Ararat Valley. Patches are drawn 50 % Tonle Sap / 25 % Sevan / 25 % Ararat so the small Armenian scenes are not drowned out.
3. **Inputs**: this date HH, HV + reference date (first date) HH, HV as in v1, optionally + 3 **season channels**: per-pixel minimum, mean and standard deviation of HH over the valid dates (`--temporal`). 4 or 7 input channels.
4. **Optional noise augmentation** (`--aug-noise`): half of the training patches get gamma speckle (2–16 looks) and a gain error (±10 % global, ±3 % per date).
5. **Evaluation** per class (IoU + area error) on both hold-outs, Sevan area per date (all water and the largest connected water body), ignore-band / wind-suspect behaviour, and a speckle/gain robustness test against the math detector (`robust`).

### Commands (from the repo root; times measured on the M5, GPU shared with other jobs)
```bash
.venv/bin/python -m scripts.detect.train sweep --model unet --versions v2 --workers 3   # 6 v2 versions, 252 s total; writes unet_v2_best.txt
.venv/bin/python -m scripts.detect.train unet --version v2_t_d3                         # one version alone: ~105 s training, ~2 min total
.venv/bin/python -m scripts.detect.train robust --versions v2_t_d3,v2_t_d2,v2_t_d2_aug  # robustness test, 50 s
.venv/bin/python -m scripts.detect.train figs_v2                                        # comparison maps + area curves, 10 s
.venv/bin/python -m scripts.detect.train predict --area tonle-sap                       # uses best v2 by default, 16 s
.venv/bin/python -m scripts.detect.train predict --area sevan                           # same weights, 4 s
.venv/bin/python -m scripts.detect.train predict --area sevan --version v1 --weights-area tonle-sap   # old rules-trained net
```
Ad-hoc v2 run: `unet --v2 --temporal --depth 2 --scenes tonle-sap,sevan,ararat --aug-noise --band-px 1`. v2 versions use 8 epochs × 100 steps × 16 patches of 128 px. `predict` reads the best version from `data/processed/models/unet_v2_best.txt` and writes `data/processed/<area>/dl/products.pt` with the same keys as before plus `math_water_km2`, `math_flooded_veg_km2`, `water_largest_body_km2`, `unet_version`. The old v1 commands and the old v1 sweep (`sweep --model unet` without `--versions`) are unchanged.

### Leaderboard (`docs/eda/dl/sweep_unet_v2.md`, 3 workers, 252 s, no crashes)
Numbers are **agreement with the math detector on the held-out parts, not accuracy**. Score = mean of Tonle Sap mean IoU (3 classes) and Sevan mean IoU (water, other land; Sevan has ~no flooded vegetation).

| rank | version | params | train s (3 parallel) | Tonle Sap IoU water / fl-veg / other | Sevan IoU water / other | score | Sevan largest body, mean abs. error vs 1,242 km² |
|---|---|---|---|---|---|---|---|
| 1 | **v2_t_d3** (season channels, depth 3) | 483,347 | 105 | 0.822 / 0.332 / 0.996 | 0.999 / 1.000 | **0.858** | 12 |
| 2 | v2_t_d2_tsonly (Tonle Sap only) | 117,971 | 101 | 0.817 / 0.313 / 0.996 | 0.999 / 1.000 | 0.854 | 5 |
| 3 | v2_t_d2 | 117,971 | 97 | 0.797 / 0.247 / 0.995 | 0.999 / 1.000 | 0.839 | 4 |
| 4 | v2_not_d2_aug (no season ch., noise aug.) | 117,539 | 119 | 0.848 / 0.185 / 0.995 | 0.997 / 0.999 | 0.837 | 17 |
| 5 | v2_t_d2_aug | 117,971 | 122 | 0.845 / 0.158 / 0.994 | 0.999 / 1.000 | 0.832 | 11 |
| 6 | v2_not_d2 (no season channels) | 117,539 | 92 | 0.665 / 0.339 / 0.992 | 0.996 / 0.999 | 0.832 | 4 |

What the sweep says:
- **Season channels help open water** at Tonle Sap (IoU 0.665 → 0.797 at depth 2, 0.822 at depth 3). Noise augmentation gives the same water gain but lowers flooded-vegetation agreement.
- **Depth 3 vs 2**: small gain (0.84 → 0.86 score) for 4× the parameters. The 118k depth-2 model is a fine alternative.
- **Armenian scenes barely matter**: the Tonle-Sap-only model is #2 and gets Sevan right (largest body within 5 km² of 1,242 on average) without ever seeing Sevan. The fix for Sevan came from the **labels** (dense "other land" on dark ground) and the season channels, not from extra scenes.
- **Flooded vegetation stays poor** (IoU 0.16–0.34 against the math detector). Both methods find only a few hundred km² and disagree on where.

Held-out area error of the best model (mean per date, network vs math, same pixels): Tonle Sap east open water −5.8 %, flooded vegetation +18 %, other +0.1 %; Sevan east open water −1.3 %, other +0.4 %.

### Lake Sevan, km² per date (true lake area 1,242 km²)
v2 = best model `v2_t_d3`. Note: the west two-thirds of Sevan were in the v2 training set (the Tonle-Sap-only model gives nearly identical numbers, see leaderboard). Math values are on the same valid pixels.

| date | math all water | math largest body | v2 all water | v2 largest body | v1 all water |
|---|---|---|---|---|---|
| 2026-06-20 | 1245 | 1230 | 1245 | 1233 | 1493 |
| 2026-07-02 | 1235 | 1215 | 1247 | 1232 | 1661 |
| 2026-07-14 | 1235 | 1213 | 1246 | 1234 | 1585 |
| 2026-07-26 | 1284 | 1230 | 1265 | 1236 | 1973 |
| 2026-08-19 | 1261 | 1226 | 1251 | 1232 | 1705 |
| 2026-08-31 (windy) | 1236 | 1186 | 1255 | 1229 | 1865 |
| 2026-09-12 (windy) | 1197 | 1133 | 1235 | 1207 | 2076 |
| 2026-09-24 | 1279 | 1221 | 1269 | 1232 | 2311 |
| 2026-10-06 | 1329 | 1234 | 1283 | 1233 | 2278 |
| 2026-10-18 | 1304 | 1231 | 1275 | 1233 | 2246 |

Largest body: math 1,133–1,234 km², v2 1,207–1,236 km² (true 1,242; mean abs. error math 30 km², v2 12 km²). All water: math 1,197–1,329, v2 1,235–1,283. The network's lake is steadier on the windy dates. A 1-px shoreline error is ~45 km² here, so both are within the pixel-size uncertainty. Remember the math thresholds were themselves tuned so Sevan comes out near 1,242 km², so this anchor is not independent of the labels.

### Tonle Sap, km² per date (whole scene, same valid pixels)
| date | math water | v2 water | math flooded veg | v2 flooded veg | v1 water | v1 flooded veg |
|---|---|---|---|---|---|---|
| 2026-06-19 | 2234 | 2245 | 1 | 0 | 2793 | 0 |
| 2026-07-01 | 2297 | 2286 | 0 | 12 | 3802 | 2362 |
| 2026-07-13 | 2310 | 2334 | 39 | 0 | 4925 | 1505 |
| 2026-07-25 | 2421 | 2418 | 99 | 114 | 4288 | 3914 |
| 2026-08-18 | 2803 | 2875 | 263 | 578 | 6819 | 4379 |
| 2026-08-30 | 2954 | 2958 | 611 | 1221 | 7430 | 4820 |
| 2026-09-11 | 3147 | 3168 | 509 | 1069 | 7469 | 4923 |
| 2026-09-23 | 3522 | 3493 | 264 | 433 | 10724 | 4080 |
| 2026-10-05 | 3541 | 3492 | 185 | 368 | 10075 | 5710 |
| 2026-10-17 | 3837 | 3693 | 144 | 347 | 10192 | 6139 |

Open water now follows the math detector within ±4 % on every date (v1 was 1.3–2.9× higher). Flooded vegetation is still about **2× the math detector** in August–October; treat it as unreliable.

### What the network does where the labels are ignored (held-out parts)
| | Tonle Sap east | Sevan east | Ararat (training scene) |
|---|---|---|---|
| boundary band → water / fl-veg / other | 0.31 / 0.03 / 0.66 (agrees with math mask 76 %) | 0.37 / 0.00 / 0.63 (82 %) | 0.16 / 0.04 / 0.80 (71 %) |
| wind-suspect pixels → water / other | 863 px: 0.40 / 0.60 | 45 px: 0.49 / 0.51 | 31 px: 0.16 / 0.84 |

The band is filled sensibly (shoreline pixels split between water and land). On wind-suspect pixels (usually water, not water today per math) the network calls 40–49 % of them water: it partly "repairs" wind-roughened lake, but not reliably. There are few such pixels, so this barely moves km².

### Robustness test (`docs/eda/dl/robust_v2.md`, `v2_robustness.png`)
Held-out inputs (whole stack) were perturbed with multiplicative gamma speckle (16, 8, 4, 2 looks; clipped to 0..1) or a global gain error (±10 %), and both the math detector (re-run on the perturbed stack) and the networks were compared (a) with the clean-input math labels and (b) with **their own clean output** (the fair stability measure; (a) favours the math detector, which scores 1.0 on clean input by construction).

Open-water IoU, Tonle Sap east, self-consistency (perturbed vs own clean output):

| condition | math | v2_t_d3 | v2_t_d2 | v2_t_d2_aug |
|---|---|---|---|---|
| speckle 16 looks | 0.870 | **0.902** | 0.873 | 0.888 |
| speckle 8 looks | 0.823 | **0.851** | 0.797 | 0.831 |
| speckle 4 looks | 0.753 | **0.776** | 0.688 | 0.745 |
| speckle 2 looks | **0.666** | 0.649 | 0.547 | 0.627 |
| gain −10 % | 0.848 | 0.842 | 0.872 | **0.897** |
| gain +10 % | 0.856 | 0.851 | 0.884 | **0.894** |
| 4 looks + gain +10 % | 0.774 | 0.776 | 0.757 | **0.791** |

Sevan open water: everyone stays ≥ 0.95; the best network is slightly steadier (2 looks: 0.972 vs math 0.962; other-land 0.991 vs 0.953). Flooded vegetation collapses for **both** methods under speckle (math 0.11 at 16 looks; network 0.21, noise-augmented network 0.53): it is a small, noise-sensitive class.

**Honest reading:** the network is **about as stable as the math detector, not clearly more**. The best model is a few IoU points steadier for mild-to-moderate speckle and equal under gain errors; the noise-augmented model is the steadiest under gain errors and for flooded vegetation, but agrees less with the math detector on clean data. The math detector has its own multi-temporal speckle filter, so the network has little to add here. Note that a ±10 % gain on every date is **not** removed by the math detector's per-date normalisation (it is relative between dates), which is why both drop to ~0.85.

### What is and is not validated (v2)
- **Not validated against truth**: still nothing, except the one anchor (Sevan 1,242 km²), and that anchor was also used to tune the math thresholds that make the labels.
- **Checked**: v2 agrees with the math detector on unseen ground (water IoU 0.82 Tonle Sap, 0.999 Sevan); open-water km² match the math detector within ±4 % per date; Sevan dark slopes are no longer water (v1 error of +250 to +1,070 km² is gone); stability under synthetic noise is comparable to the math detector.
- **Not checked**: flooded-vegetation extent (network ~2× math, IoU 0.33); any real speckle or calibration error (our noise is synthetic, on display brightness, not on power); scenes other than these three.

### Verdict
**Show the math detector in the app as the main product; do not show the v2 network as a separate source of numbers.** v2 is now trustworthy in the sense that it no longer invents water (it reproduces the math detector's open water within a few percent everywhere, and Sevan comes out at 1,207–1,236 km²), but that is because it learned the math detector; it adds no independent evidence. Its only measurable extras are a slightly steadier Sevan outline on windy dates and slightly better stability under moderate speckle, which are too small to justify a second, harder-to-explain layer. Its flooded vegetation disagrees with the math detector by 2× and should not be shown. If the team wants to mention deep learning, show the v1 → v2 comparison figures (`v2_compare_*.png`, `v2_area_vs_date.png`) as a lesson in "labels matter", and keep the network as a ready-to-retrain pipeline for when calibrated NISAR data and real reference masks (e.g. JRC / Sentinel-1) exist.

### v2 limitations
- Labels are another algorithm's output; held-out IoU measures agreement with it, and v2 inherits its mistakes (thresholds tuned on Sevan, flooded vegetation defined vs the first two dates).
- Sevan west two-thirds and all of Ararat are in the training set, so the Sevan whole-lake numbers are partly in-sample (the Tonle-Sap-only model, which never saw Armenia, gives the same picture).
- Model selection used the held-out parts (no separate test split); with 6 versions the selection bias is small but non-zero.
- One seed per version; differences below ~0.02 IoU between versions are probably noise.
- Robustness noise is synthetic, applied to display brightness and clipped to 0..1, so calm water at the floor (1/255) is barely affected by multiplicative noise: this makes water look easier than in real data.
- The math pipeline uses only pixels valid on all dates (common footprint); network predictions outside it are unscored (products still contain them).
- Flooded-vegetation IoU on Sevan is undefined/0 (no such class there) and was left out of the score.
- Training times were measured with 3 runs (and other agents' jobs) sharing the GPU.

Figures: `docs/eda/dl/v2_compare_tonle-sap.png` and `v2_compare_sevan.png` (input / v1 rules / math / v1 network / v2 network), `v2_area_vs_date.png` (math vs v2 vs v1, with Sevan's true-area line), `v2_robustness.png`.

---
**Sections 1–8 below describe v1 (rules-trained U-Net and the autoencoder) and are kept for reference.** The autoencoder is unchanged and still used by `predict`.

## 1. The two models in plain words

### Temporal autoencoder (`TemporalAE`, 19,546 parameters)
Each pixel has 10 dates. For each date it gets 5 numbers: HH, HV, HH−HV, the day offset, and a valid flag. We use a 3×3-smoothed stack so speckle does not dominate.
- A bidirectional **GRU** reads the pixel's season and compresses it into **8 numbers** (the "embedding").
- A small MLP decoder gets those 8 numbers plus a day offset and must rebuild HH and HV for that day. Because it is told the real day, the 25-day August outage is handled correctly.
- The loss is mean squared error over valid dates only. During training we also hide 10 % of dates at random, so the model has to use the whole season rather than one date.
- Training uses no labels at all.

The command produces three things:
- **Embedding map:** `products.pt["embedding"]`, size [8, H, W].
- **k-means clusters:** k = 8, k-means++ written in torch, run on the embeddings. This gives a "what this pixel did over the season" class map (`traj_class`).
- **Reconstruction error:** `recon_error`. A high value means the pixel's season is unusual compared with what the model learned.

### Small U-Net (`UNetSmall`, 482,915 parameters, base width 16, 3 poolings)
For each date it labels every pixel as **open water / flooded vegetation / other land**. Its input has 4 channels: this date's HH and HV plus HH and HV from the dry-season reference date (19 June, the first date).

It is trained by **weak supervision** on labels from `weak_labels.py`. The rules work on the 3×3-smoothed stack, and every threshold is computed from the area's own data:

| class | rule | Tonle Sap value |
|---|---|---|
| open water | HH < 0.8·g_HH **and** HV < 0.8·g_HV, where g is a second-level Otsu threshold (Otsu applied below the first Otsu, which finds the water/land gap) | HH < 0.20, HV < 0.20 (g ≈ 0.25) |
| flooded vegetation | ΔHH vs reference > 97th percentile of ΔHH (at least +0.08), **and** ΔHV < 0.5·ΔHH, **and** HH > 1.3·g_HH (not dark) | ΔHH > 0.18, HH > 0.33 |
| other land | not dark (HH > 1.3·g_HH) **and** \|ΔHH\| and \|ΔHV\| both inside the calmest 35 % | \|ΔHH\| < 0.033, \|ΔHV\| < 0.023 |
| not sure (ignored) | everything else, invalid pixels, the swath-edge rim and 8 px next to it | 58–78 % of pixels on dates after the reference |

Training details:
- Cross-entropy with `ignore_index=255`.
- Class weights are 1/sqrt(frequency): water 0.53, flooded vegetation 2.09, other 0.38.
- Random flips and rot90. Batches of 16 patches of 128 px, 120 steps per epoch, 10 epochs, AdamW with a one-cycle schedule.
- The reference date makes up 87 % of the "other" labels, so it is drawn less often.
- **Spatial hold-out:** training patches come only from the western 70 % of columns. Validation is the eastern 30 %, over all 10 dates.

## 2. Retrain on a MacBook (Apple M5, MPS). Times measured on this machine

Run from the repo root:

```bash
.venv/bin/python -m scripts.detect.train ae      --area tonle-sap          # 6 epochs: ~1.2 s/epoch, 7.5 s training, 19 s total
.venv/bin/python -m scripts.detect.train unet    --area tonle-sap          # 10 epochs: ~6.6 s/epoch, 66 s training, 73 s total
.venv/bin/python -m scripts.detect.train predict --area tonle-sap          # 9.5 s, writes products.pt + 2 figures
.venv/bin/python -m scripts.detect.train predict --area sevan --weights-area tonle-sap   # transfer check, 2 s
```

Common flags: `--epochs N`, `--device mps|cpu`, `--seed 0`.
- `ae` also takes `--pixels --batch --hidden --emb --k --encoder gru|lstm|conv`.
- `unet` also takes `--steps --batch --patch --base --depth --no-ref --no-class-weights`.
- `predict` takes `--version NAME` (U-Net version) and `--ae-version NAME`.

Every op used is supported on MPS (GRU, LSTM, conv, cdist), so `PYTORCH_ENABLE_MPS_FALLBACK` is **not** needed. Each run also writes its metrics to `data/processed/models/runs/<model>_<area>[_<version>].json`.

### Trying several versions in parallel (`sweep`)
The versions live in the `VERSIONS` dict near the top of `scripts/detect/train.py`. Edit that dict to add or change a version.

```bash
.venv/bin/python -m scripts.detect.train sweep --model unet --area tonle-sap --workers 3   # 6 versions, 525 s (8m45s) total
.venv/bin/python -m scripts.detect.train sweep --model ae   --area tonle-sap --workers 3   # 6 versions, 83 s total
# options: --epochs N  --versions a,b  --device cpu
```

How a sweep runs:
- Each version is its own subprocess sharing the Apple GPU, with at most `--workers` running at once.
- Every version uses the same seed and the same west/east split.
- One version crashing does not stop the others. Its log is in `data/processed/models/runs/<model>_<area>_<version>.log`.
- Weights go to `data/processed/models/<model>_<area>_<version>.pt`. The leaderboard goes to `docs/eda/dl/sweep_<model>_<area>.md`.
- Three parallel MPS runs did not crash. Each run gets slower (U-Net about 200 s instead of 75 s), but the total is still faster than running them one after another.
- If the Mac runs short of memory, use `--workers 2`.

To use a version's weights: `.venv/bin/python -m scripts.detect.train predict --area tonle-sap --version base16_d2 --ae-version gru_e4_k6`.

## 3. Results on Tonle Sap

### Training
| model | params | device | epochs | train time | final train loss | held-out loss |
|---|---|---|---|---|---|---|
| TemporalAE (GRU, emb 8) | 19,546 | mps | 6 | 7.5 s | MSE 0.00619 | MSE 0.00635 (eastern-30 % pixels) |
| UNetSmall (base 16, depth 3) | 482,915 | mps | 10 | 65.9 s | CE 0.0066 | CE 0.0059 (eastern 30 %) |

Figures: `ae_training_tonle-sap.png`, `unet_training_tonle-sap.png`.

### U-Net held-out agreement with the RULES (eastern 30 %, labelled pixels only; not truth)
| open water IoU | flooded-veg IoU | other IoU | pixel agreement |
|---|---|---|---|
| 0.997 | 0.970 | 0.998 | 0.999 |

These numbers are close to 1 because the rules are a simple function of the same input, so the network can learn them almost exactly. They show that the training worked and that the result carries over to unseen ground. They say nothing about whether the rules are correct.

**What the network adds** is that it labels the "not sure" pixels. Across the whole image, ignored pixels became 12.8 % water, 15.7 % flooded vegetation and 71.5 % other. In the eastern hold-out the split was 14.8 / 18.5 / 66.7 %. Its masks are also smoother and free of speckle. See `unet_rules_vs_net_tonle-sap.png`: input, rules with white for not sure, and the network.

### Area per date (U-Net, km², 0.0327 km² per pixel)
| date | open water | flooded veg | rules: water | rules: flooded veg |
|---|---|---|---|---|
| 2026-06-19 | 2793 | 0 | 2531 | 0 |
| 2026-07-01 | 3802 | 2362 | 2658 | 68 |
| 2026-07-13 | 4925 | 1505 | 2729 | 149 |
| 2026-07-25 | 4288 | 3914 | 2790 | 348 |
| 2026-08-18 | 6819 | 4379 | 3641 | 384 |
| 2026-08-30 | 7430 | 4820 | 3818 | 446 |
| 2026-09-11 | 7469 | 4923 | 4242 | 431 |
| 2026-09-23 | 10724 | 4080 | 5587 | 230 |
| 2026-10-05 | 10075 | 5710 | 5501 | 390 |
| 2026-10-17 | 10192 | 6139 | 5614 | 182 |

Open water rises steadily from June to October, about 2.8k to 10k km² by the network and 2.5k to 5.6k km² by the confident rules. The growth spreads along the Tonle Sap river and the southeast floodplain (`unet_seg_dates_tonle-sap.png`, `area_vs_date_tonle-sap.png`). The trend matches the flood season, but the **absolute km² are not validated**.

Flooded vegetation from the network is **much larger** than from the rules: a few thousand km² against a few hundred. Much of it is upland field patches in the north (possibly flooded rice paddies, where double bounce is real, or simply growing crops). Treat these flooded-vegetation km² as an **upper bound**. The no-class-weights version in the sweep only reduces this a little.

### Trajectory clusters (`ae_clusters_tonle-sap.png`; mean HH/HV curve ± 1 sd at true dates, August gap shaded)
| id | share | behaviour | our reading |
|---|---|---|---|
| 0 | 6 % | HH≈HV≈0.02 every date | permanent open water (the lake) |
| 1 | 10 % | HH 0.36 → 0.19, drops after mid-September | **flooded late**: floodplain that became open water in Sept–Oct (the late, weak 2026 flood) |
| 2 | 15 % | HH 0.43 → 0.35, mild late drop | wetter fields and grassland on the floodplain edge |
| 3–5 | 14 % each | flat at HH 0.5 / 0.6 / 0.73 | stable land at three brightness levels (cropland, shrub) |
| 6 | 12 % | flat, HH≈0.83, high HV | forest / built-up |
| 7 | 14 % | HH≈HV≈0.9, flat | dense forest / highest-brightness land (south-west uplands) |

Clusters 3 to 7 differ mainly in **brightness level**, not in seasonal behaviour. That is because most land does not change over the season. Only clusters 0, 1 and 2 describe change.

The **reconstruction-error map** (`ae_recon_error_tonle-sap.png`) is brightest in the ring of flooded forest and shrub around the lake and along the river into the southeast floodplain. Those are exactly the places where the season behaved unusually, which matches the physics.

### Sweep leaderboards (run once on tonle-sap, 3 workers in parallel, no crashes)
U-Net (`docs/eda/dl/sweep_unet_tonle-sap.md`, total 525 s). Mean IoU is agreement with the rules on the eastern hold-out:

| version | params | wall s | IoU water / flooded veg / other | mean IoU | not-sure → water / fl-veg / other |
|---|---|---|---|---|---|
| no_weights | 482,915 | 210 | 0.999 / 0.989 / 0.999 | 0.996 | 0.16 / 0.17 / 0.67 |
| base16_d2 | 117,539 | 176 | 0.998 / 0.988 / 0.999 | 0.995 | 0.15 / 0.23 / 0.62 |
| base16_d3 (default) | 482,915 | 197 | 0.999 / 0.981 / 0.999 | 0.993 | 0.14 / 0.19 / 0.67 |
| base32_d3 | 1,927,363 | 391 | 0.998 / 0.981 / 0.999 | 0.993 | 0.14 / 0.22 / 0.64 |
| no_ref | 482,627 | 209 | 0.999 / 0.111 / 0.858 | 0.656 | 0.09 / 0.11 / 0.80 |
| base32_d2_noref | 466,947 | 141 | 1.000 / 0.093 / 0.816 | 0.636 | 0.08 / 0.14 / 0.78 |

The main lesson: **the dry-season reference channels are essential** for flooded vegetation, because flooded vegetation is defined as a change. Width and depth barely matter. The 117k-parameter depth-2 model is as good as the others.

Autoencoder (`docs/eda/dl/sweep_ae_tonle-sap.md`, total 83 s). Lower held-out reconstruction MSE only means the embedding keeps more of the series:

| version | params | wall s | held-out MSE |
|---|---|---|---|
| gru_e4_k6 | 18,966 | 33 | 0.00603 |
| gru_e16_k8 | 20,706 | 37 | 0.00607 |
| gru_e8_k8 (default) | 19,546 | 45 | 0.00637 |
| gru_e8_k6 | 19,546 | 37 | 0.00637 |
| conv_e8_k8 | 11,434 | 34 | 0.00666 |
| lstm_e8_k8 | 24,826 | 45 | 0.00708 |

All versions are within about 15 % of each other. The season of a pixel compresses easily, so 4 numbers are enough. k does not change the reconstruction. Choose k by looking at the cluster figure.

The sweeps ran **before** the 8-px swath-edge mask was added, and partly while `stack.py` gained co-registration (Tonle Sap moved by 0.5 px or less). The default models above were retrained afterwards with both changes. Rerun the sweeps for exactly matching numbers.

## 4. Transfer check: Lake Sevan with the Tonle Sap weights, unchanged (data co-registered)
The true lake area is 1,242 km². 31 Aug and 12 Sep are the windy dates. In the table, "ref water" means the pixels the rules confidently call water on calm 20 June: 686 km², roughly the darker half of the lake.

| date | U-Net water km² | U-Net keeps ref water | rules keep ref water | U-Net water outside ref | U-Net flooded veg km² |
|---|---|---|---|---|---|
| 2026-06-20 | 1493 | 1.000 | 1.000 | 807 | 0 |
| 2026-07-02 | 1661 | 1.000 | 0.975 | 975 | 15 |
| 2026-07-14 | 1585 | 1.000 | 0.976 | 899 | 38 |
| 2026-07-26 | 1973 | 1.000 | 0.935 | 1287 | 35 |
| 2026-08-19 | 1705 | 1.000 | 0.825 | 1019 | 80 |
| 2026-08-31 | 1865 | 1.000 | 0.710 | 1179 | **662** |
| 2026-09-12 | 2076 | 1.000 | 0.407 | 1390 | 54 |
| 2026-09-24 | 2311 | 1.000 | 0.302 | 1626 | 38 |
| 2026-10-06 | 2278 | 1.000 | 0.394 | 1593 | 59 |
| 2026-10-18 | 2246 | 1.000 | 0.438 | 1561 | 50 |

What this shows:
- **Good:** the network does **not** lose the lake on windy dates. It keeps 100 % of the calm-date core, while the threshold rules lose up to 70 %. Spatial context ("this is the middle of a big dark body") beats the per-pixel brightness test.
- **Bad:** it calls 800 to 1,600 km² of mountain slopes "water". These are dark radar-shadow and foreslope areas in a scene with different contrast. On 31 Aug it also calls 662 km² of hillsides "flooded vegetation", because the whole scene brightened.
- **So: Sevan km² from this U-Net are not usable as they are.** Use the lake mask or connected component (the other team's flood-fill gives 1,231 to 1,238 km² on calm dates), or retrain on Armenia with a slope/shadow mask.

## 5. What is and is not validated
- **Validated:** nothing against truth.
- **Checked:**
  - training converges and carries over across a spatial hold-out (agreement with the rules);
  - outputs match the physics by eye: lake dark, flood spreading along the river, error map highlighting the flood ring, Sevan lake kept on windy dates;
  - 3 parallel MPS runs work.
- **Not validated:**
  - any km²;
  - flooded-vegetation extent (likely overestimated, and includes paddies and crops);
  - cluster names (they are our interpretation of mean curves);
  - behaviour on other areas (the Sevan test shows terrain false positives).

## 6. When calibrated (γ⁰, finer) data arrives
1. `load_preview_stack` will return `source="gamma0"`. Convert to dB and give the model fixed physical scaling: replace `(x-0.5)/0.25` in `unet_inputs` with something like `(dB+15)/7`.
2. Re-derive the weak-label thresholds. They are percentile and Otsu based, so they adapt automatically, but check the printed values. In dB, water is around −20 dB or lower.
3. Models take channel counts as arguments (`TemporalAE(n_in=..)`, `UNetSmall(n_in=..)`). Adding a channel such as a DEM slope or HH/HV ratio means editing `ae_features` / `unet_inputs` only.
4. Finer pixels (about 20 to 30 m) mean bigger images. Keep 128 to 256 px patches. Use `predict_full` tiled, or downsample, if MPS memory runs out at the full scene. Retraining should still take minutes.
5. Remove the swath-edge hack in `load_stack` (the 1/255 rim and the 8-px edge), which is specific to the previews.

## 7. If there is spare time (most valuable first)
1. **Check against JRC Global Surface Water** (occurrence and seasonality) or **Sentinel-1 water masks** for the same dates. That gives the first real IoU and an honest km² error bar.
2. Add a **terrain shadow/layover mask** (Copernicus DEM slope) as an ignore region or extra input. This fixes the Sevan false positives.
3. For lakes, report **area of the connected component** containing a seed point, not all water pixels.
4. Train the U-Net on Tonle Sap and Sevan together, with the reference date chosen per area.
5. Separate seasonal behaviour from brightness in the autoencoder (per-pixel mean-subtracted curves as extra input), so the clusters split by behaviour rather than brightness level.
6. Use the reconstruction error as a change alert: top 1 % pixels as polygons.

## 8. Limitations and what did not work
- **Inputs:** previews are display-stretched 8-bit images. Brightness is not comparable between scenes, which is why transfer to Sevan has false positives.
- **Swath edge:** the swath edge is padded with value 1/255, which looks just like water. We remove it by flood-filling from the invalid area (at most 40 px) and also drop 8 px next to the swath edge. Rivers that touch the edge may lose a few pixels.
- **U-Net hold-out IoU is about 0.97 to 1.0:** that is near-trivial (it learns the rules). It is not evidence of correctness.
- **Flooded vegetation:**
  - The network labels many times more than the rules.
  - It is only defined as change from 19 June. Vegetation already flooded on that date is invisible to it.
  - Pixels where the reference date is invalid are set to "no data".
- **Clusters:** 5 of the 8 clusters are brightness levels of stable land, not distinct behaviours.
- **Autoencoder hold-out:** the AE hold-out is the eastern 30 % of pixels, but the AE (and k-means) are applied to the whole image afterwards. That is normal for self-supervised use.
- **Cluster names:** the automatic names in the figure come from simple curve rules (`describe_cluster`). Use the table above for the reading.
- **Sweep consistency:** sweep numbers come from a slightly older data path (no edge mask, partly pre-alignment). See section 3.
- **Model size:** `base32_d3` (1.93M parameters) is over the "under 1M parameters" target. The default stays at base 16.
