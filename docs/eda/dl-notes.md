# Deep-learning notes: two small PyTorch models (no labels needed)

**Read this first:** we have **no ground truth**. Nothing below is "accuracy". The U-Net numbers measure how well the network **agrees with our own physics rules**. The autoencoder numbers measure how well it **reproduces its input**. Everything runs on uncalibrated 8-bit preview brightness at about 180 m per pixel.

Code: `scripts/detect/models.py` (models and k-means), `scripts/detect/weak_labels.py` (rules), `scripts/detect/train.py` (command-line tool).
Weights: `data/processed/models/`. Products: `data/processed/<area>/dl/products.pt`. Figures: `docs/eda/dl/`.

We did not build a region-proposal detector such as R-CNN. Those find boxed objects, and floods and lakes are not boxed objects, so we use per-pixel models instead.

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
