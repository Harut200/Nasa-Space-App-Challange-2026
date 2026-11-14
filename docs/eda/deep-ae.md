# A large pretrained encoder (EfficientNet-B4) + Gaussian mixture: does it help?

Code: [`scripts/detect/deep_ae.py`](../../scripts/detect/deep_ae.py). Tests: [`tests/test_deep_ae.py`](../../tests/test_deep_ae.py).
Numbers: [`deep_ae/results.json`](deep_ae/results.json). Figures: [`deep_ae/`](deep_ae/).

**Short answer: no.** On our data (one season of uncalibrated 180 m preview images, two channels, no labels) the ImageNet-pretrained EfficientNet-B4 autoencoder does not beat a Gaussian mixture on the two raw numbers HH and HV. It does not beat its own randomly initialised twin either. It costs 316,805 parameters and 1–2.5 minutes of training, against zero for the no-network baseline. Do not show it in the app. In the pitch it is worth one sentence: "we tried a big pretrained network, and the plain mathematics was as good or better on this data".

## 1. What was built, and why

**The idea we tested.** Take a large convolutional network pretrained on ImageNet photos and use its features as a self-supervised autoencoder. Then cluster each pixel's learned features with a Gaussian mixture model (GMM), name the clusters, and use the mixture likelihood as an anomaly score.

**Encoder.** We use torchvision's `efficientnet_b4`, but only its stem and MBConv stages 1–3 (`features[0:4]`). These give stride 8 and 56 channels. The input is first upsampled 2× (bilinear), so one embedding cell covers 4 × 4 of our 180 m pixels (720 m). We did not use the full B4 (19.3 M parameters, stride 32): one cell would then be 16 × 16 pixels, about 3 km, wider than most of the channels and floodplain features we care about. The truncated encoder has 269,362 parameters, and the whole autoencoder has 316,805 (321,813 with the skip connection).

**Pretrained weights.** `torchvision 0.29.2` was installed; it left torch at 2.15.0. The full 78 MB ImageNet file could not be downloaded, because the hackathon network ran at about 1–17 KB/s. So we fetched only the 214 tensors of `features[0:4]` (278,111 numbers) from the official `download.pytorch.org` file. This used HTTP range requests that read the file's zip index. The tensors were saved as `data/processed/models/deep_ae_b4_imagenet_stages0-3.pt`, and `b4_features()` loads them. If the full file is in the torch hub cache, torchvision is used as normal. The values look like real trained weights (for example, batch-norm running variances in the thousands), but we did not checksum them against the full file.

**Input to the 3-channel first convolution**, two options:
* (a) `rgb`: R = HH, G = HV, B = HH − HV + 0.5, normalised like an ImageNet photo.
* (b) `adapter` (default): a learnable 1 × 1 convolution that maps 8 channels to 3. The 8 channels are this date's HH and HV, plus the season minimum, mean and standard deviation of HH and of HV. It starts as R = HH, G = HV, B = HH − HV.

**Decoder.** It is light: a 3 × 3 convolution from the 16-channel bottleneck, then two rounds of (bilinear ×2 upsample + 3 × 3 convolution), then a head. We used bilinear upsampling instead of transposed convolutions, to avoid checkerboard artefacts. The `skip` variant also concatenates the stride-2 stem features, which are at full native resolution, before the head. That lets the network bypass the bottleneck, which is why it is a separate variant.

**Self-supervised loss: masked reconstruction (primary).** Random 16 × 16-pixel blocks (40 %) of the input are set to 0. The network reconstructs all input channels. Loss = 0.5 × MSE over all valid pixels + MSE over the hidden valid pixels. Why this loss:
* Plain reconstruction through a 16-channel bottleneck at 1/16 of the pixel count is mostly compression of local brightness.
* Masking forces the network to use spatial context, which is the only thing a CNN can add over a per-pixel method.
* Predicting date t+1 from date t was rejected because of the 23-day outage gap and the irregular spacing.

**Embedding.** The 16-channel bottleneck, upsampled bilinearly to full resolution, gives a dense per-pixel map `[T, 16, H, W]`. It is computed in 384-px tiles with a 32-px overlap and stored on the CPU as float16.

**Training** (identical for every variant):
* Tonle Sap, random 128-px patches from the western 70 % of columns, random date; patches with < 50 % valid pixels are skipped.
* Random 90° rotations and flips.
* Batch 8; 6 epochs × 50 steps; AdamW.
* Learning rates: 1e-3 for the decoder and adapter, 2e-4 for the pretrained encoder (1e-3 when randomly initialised); one-cycle schedule; seed 0.
* Frozen variant: the encoder weights and its batch-norm statistics are fixed; the adapter and decoder train.
* Validation: 24 fixed patches from the eastern 30 %.

**Gaussian mixture (own EM in torch):**
* Features are standardised, then PCA-whitened, keeping 99 % of the variance (at most 16 dimensions).
* Full-covariance EM in float64 with + 1e-3·I regularisation, k-means++ initialisation, best of 3 restarts.
* k is chosen by BIC over 3…10, on 60,000 pixel-dates sampled from the western 70 %.
* The same mixture is applied to every date, which gives a cluster map per date, posteriors, and an anomaly score = −log mixture likelihood.
* Clusters are ordered dark → bright by mean HH.

A cluster is called **"water"** if at least 50 % of its western pixel-dates are open water for the math detector. The eastern hold-out is never used for naming. This one rule is applied identically to every method.

**Comparison target.** The math detector with the terrain mask: `ops.run_pipeline(..., water_ok=terrain.cached_water_possible(area, shape))`, cached in `data/processed/models/deep_ae_math_<area>.pt`.

**Methods compared** (same seeds, same split, same GMM code):

| Method | Per-pixel features fed to the GMM |
|---|---|
| (i) B4 pretrained, fine-tuned (`b4_pre_ft`, the main model) | 16-dim embedding |
| (ii) B4 pretrained, frozen encoder (`b4_pre_frozen`) | 16-dim embedding |
| (iii) B4 random init (`b4_rand_ft`) | 16-dim embedding |
| extra: B4 pretrained, RGB input (`b4_pre_ft_rgb`) | 16-dim embedding |
| extra: B4 pretrained, with skip (`b4_pre_ft_skip`) | 16-dim embedding |
| (iv) existing `TemporalAE` (19,546 params, `ae_tonle-sap.pt`) | 8-dim season embedding + this date's 3 × 3-smoothed HH, HV (the AE alone has no per-date information) |
| (v-a) no network: raw features | the same 8 numbers as the adapter input, 3 × 3-smoothed |
| (v-b) no network: date HH/HV only | this date's 3 × 3-smoothed HH and HV |

## 2. Results

No ground truth exists, so every score below is agreement with something else:

* **ARI / NMI:** adjusted Rand index and normalised mutual information. They measure agreement between the cluster map and the math detector's 3 classes (open water, flooded vegetation, other) on the Tonle Sap **east hold-out**, over all dates. 0 = chance, 1 = identical partition.
* **IoU water best / union:** IoU of math water with the single best cluster, and with the union of the clusters designated "water".
* **JRC purity:** independent check against JRC Global Surface Water (Landsat 1984–2021), whole Tonle Sap scene. The share of water-cluster pixel-dates on reference anchors that fall on permanent water (occurrence ≥ 90 %) rather than on never-water (occurrence 0 and ≥ 3 px from any water).
* **JRC ρ:** Spearman rank correlation between long-term occurrence and how often a pixel is in a water cluster.
* **Seed ARI:** mean ARI between the seed-0 cluster map and maps from GMM seeds 1 and 2 (the network weights are fixed).
* **Sevan:** the Tonle Sap model and mixture applied unchanged. "Body MAE" is the mean absolute error of the largest water body against the published 1,242 km², over all 10 dates. "Far-false" is the mean km² per date of water-cluster pixels ≥ 3 px from any JRC water.

| Method | k (BIC) | ARI | NMI | IoU water best / union | IoU fl-veg best | JRC purity | JRC ρ | seed ARI | Sevan body MAE km² | Sevan far-false km²/date | Params | Train s | Full-scene inference s | Weights MB |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| (i) B4 pretrained, fine-tuned | 10 | 0.012 | 0.042 | 0.204 / 0.019 | 0.006 | 0.998 | 0.466 | 0.49 | 401 | 6.9 | 316,805 | 72 | 23.4 | 1.38 |
| (ii) B4 pretrained, frozen | 10 | 0.012 | 0.039 | 0.177 / 0.006 | 0.007 | 0.999 | 0.460 | 0.59 | 513 | 5.0 | 316,805 (47,443 trained) | 55 | 13.8 | 1.38 |
| (iii) B4 random init | 10 | 0.006 | 0.047 | 0.142 / 0.142 | 0.021 | 0.999 | 0.534 | 0.52 | **82** | 123.7 | 316,805 | 151 | 22.4 | 1.38 |
| B4 pretrained, RGB input | 10 | −0.000 | 0.022 | 0.116 / 0.009 | 0.005 | 0.996 | 0.457 | 0.50 | 886 | 4.6 | 316,613 | 53 | 12.6 | 1.38 |
| B4 pretrained, with skip | 10 | 0.020 | 0.039 | 0.211 / 0.014 | 0.006 | 0.995 | 0.460 | 0.53 | 450 | 4.2 | 321,813 | 83 | 13.1 | 1.40 |
| (iv) TemporalAE + date HH/HV | 10 | −0.002 | 0.045 | 0.082 / 0.038 | 0.036 | 1.000 | 0.462 | **0.99** | 1,118 | 2.0 | 19,546 | 8 | 7.7 | 0.09 |
| (v-a) No network: raw 8 features | 10 | **0.031** | 0.082 | 0.296 / 0.008 | **0.077** | 1.000 | 0.445 | 0.64 | 1,175 | 0.1 | 0 | 0 | 1.8 | 0 |
| (v-b) No network: date HH/HV only | 10 | 0.024 | **0.101** | **0.593 / 0.650** | 0.018 | 0.992 | **0.632** | 0.54 | 204 | 89.9 | 0 | 0 | 2.0 | 0 |
| *math detector (for context)* | – | – | – | – | – | 0.997 | 0.608 | – | 30 | 16.0 | 0 | 0 | – | – |

Training and inference times are on the M5 GPU (MPS) while five other agents were using the same GPU, so they are only indicative. "Inference" means building the full-scene features (10 dates of 1105 × 1078 px) plus assigning the GMM. Fitting the GMM with the BIC search (8 values of k × 3 restarts) took a further 15–70 s per method on the CPU. The whole `compare` run took 624 s.

**What the table says:**

* **All ARIs are near 0.** None of the unsupervised partitions resembles the math detector's 3-class map. With 10 clusters, about 90 % "other land" and BIC always choosing the maximum k = 10, the clusters spend their capacity on shades of land, not on water against land. That is a property of unsupervised clustering here, not a bug, and it hits every method.
* **For open water, the simplest method wins clearly.** A GMM on the two raw numbers HH and HV gives a water union IoU of 0.65 and JRC ρ = 0.63, close to the math detector's 0.61. The best B4 variant gets 0.21 and ρ = 0.47. The B4 embeddings mix the bright-dark structure with texture. On the cluster maps ([`cluster_maps.png`](deep_ae/cluster_maps.png)) the pretrained variants look speckled, and the lake is not one clean cluster.
* **Pretraining did not help.** Random-init B4 reaches a *lower* held-out reconstruction loss (0.457 vs 0.507) and a higher JRC ρ (0.53 vs 0.47). Frozen ImageNet features are about as good as fine-tuned ones, and the photo-style RGB input is the worst variant.
* **Skip connections** changed almost nothing: the reconstruction got slightly better and the embedding did not.
* **All water clusters are pure** (JRC purity ≥ 0.99), because each method's darkest clusters sit on the permanent lake. Purity does not separate the methods; recall and ρ do.
* **TemporalAE + date** has by far the most stable clusters (seed ARI 0.99), because its whitened space is only 3-dimensional. But its "water" clusters are season-type clusters (the permanent lake), so they miss most per-date water.

### Lake Sevan transfer (fitted on Tonle Sap, applied unchanged)

Largest water-cluster body, km² (published lake area 1,242 km²; 31 Aug and 12 Sep are windy dates):

| Date | math | B4 ft | B4 frozen | B4 rand | B4 RGB | B4 skip | TempAE | raw 8 | raw HH/HV |
|---|---|---|---|---|---|---|---|---|---|
| 06-20 | 1,230 | 859 | 780 | 1,303 | 451 | 828 | 149 | 76 | 1,168 |
| 07-02 | 1,215 | 828 | 745 | 1,278 | 295 | 796 | 142 | 76 | 1,208 |
| 07-14 | 1,213 | 874 | 773 | 1,290 | 418 | 841 | 149 | 77 | 1,202 |
| 07-26 | 1,230 | 900 | 789 | 1,339 | 373 | 844 | 142 | 77 | 1,164 |
| 08-19 | 1,226 | 863 | 729 | 1,321 | 455 | 819 | 140 | 77 | 1,129 |
| **08-31** | 1,186 | 642 | 630 | 1,220 | 159 | 552 | 129 | 76 | 1,203 |
| **09-12** | 1,133 | 733 | 615 | 1,155 | 222 | 692 | 92 | 53 | 977 |
| 09-24 | 1,221 | 887 | 711 | 1,358 | 469 | 826 | 77 | 32 | 739 |
| 10-06 | 1,234 | 928 | 754 | 1,410 | 342 | 892 | 128 | 75 | 721 |
| 10-18 | 1,231 | 896 | 763 | 1,344 | 376 | 835 | 93 | 50 | 872 |
| far-false km²/date | 16.0 | 6.9 | 5.0 | 123.7 | 4.6 | 4.2 | 2.0 | 0.1 | 89.9 |

Sevan's lake is slightly brighter than the Tonle Sap lake in these previews, so the Tonle Sap "water" clusters are too strict there:

* The pretrained B4 variants find 600–930 km² and lose the most on the windy dates.
* Random-init B4 gets the area nearly right (82 km² mean error) but adds 124 km² per date of false water on dry land.
* Raw HH/HV is good until September, then loses the lake and adds false water.
* The season-type methods (TemporalAE, raw 8 features) only recognise the Tonle-Sap-style permanent lake and find almost nothing at Sevan.
* None comes close to the terrain-masked math detector: 30 km² mean error and 16 km²/date of false water.

### Linear probe (representation quality with a few labels)

Each method's per-pixel features are frozen. A multinomial logistic regression (torch, L-BFGS, weight decay 1e-3) is trained on n math-labelled **west** pixel-dates (n/3 per class), with a Bayes prior correction for the class balance. It is scored on 200,000 **east** pixel-dates. Values are the mean over 3 seeds.

| Method | mIoU, 100 labels | 1,000 | 10,000 | water IoU, 10,000 |
|---|---|---|---|---|
| B4 pretrained, fine-tuned | 0.364 | 0.414 | 0.417 | 0.288 |
| B4 pretrained, frozen | 0.396 | 0.421 | 0.422 | 0.304 |
| B4 random init | 0.426 | 0.436 | 0.422 | 0.287 |
| B4 pretrained, RGB | 0.351 | 0.379 | 0.378 | 0.172 |
| B4 pretrained, skip | 0.370 | 0.415 | 0.417 | 0.286 |
| TemporalAE + date | 0.414 | 0.411 | 0.409 | 0.255 |
| raw 8 features | 0.431 | 0.468 | 0.470 | 0.434 |
| raw HH/HV | **0.495** | **0.486** | **0.491** | **0.492** |

Two things stand out:

* **The pretrained encoder does not need fewer labels.** With 100 labels it is the *worst* non-RGB method. Two numbers per pixel give the best probe at every label budget.
* **The probe saturates by about 1,000 labels for every method.** The limit is the shift between west and east (western water is the very dark lake at HH ≈ 0.02; eastern water is shallower floodplain at HH ≈ 0.06). A single hand-set threshold on smoothed HH and HV reaches IoU 0.75 against the math water in the east.

### Figures

* [`training_curves.png`](deep_ae/training_curves.png): loss per variant. Every variant falls to about 0.35–0.40 × the "predict the mean" loss on held-out patches. The encoders do use their input.
* [`cluster_maps.png`](deep_ae/cluster_maps.png): 23 September, south-east Tonle Sap, all methods next to the math detector.
* [`cluster_curves.png`](deep_ae/cluster_curves.png): mean HH (solid) and HV (dotted) per date and cluster, with an automatic name, for B4 fine-tuned and raw features.
* [`sevan_transfer.png`](deep_ae/sevan_transfer.png): the Sevan table as plots.
* [`comparison_bars.png`](deep_ae/comparison_bars.png): the main scores as bars.
* [`linear_probe.png`](deep_ae/linear_probe.png): label efficiency.
* [`anomaly_map.png`](deep_ae/anomaly_map.png): season-maximum −log likelihood for B4 fine-tuned. At Tonle Sap it highlights the lake shore and the river channels. At Sevan it highlights bright mountain slopes, and a faint regular grid is visible (see limitations).

## 3. Verdict

The large pretrained encoder does **not** beat the small models or the no-network GMM on this data.

* **Water mapping:** the best B4 variant reaches about one third of the agreement of a GMM on raw HH and HV (water IoU 0.21 vs 0.65; JRC rank correlation 0.47 vs 0.63).
* **Pretraining:** ImageNet pretraining does not help: random init is as good or better on every score except far-false water at Sevan.
* **Cost:** about 16× the parameters of the TemporalAE, 1–2.5 min of training, and 13–23 s of full-scene inference, against 2 s for the raw GMM.

**Why, in terms of the data:**

* **One scene and one season.** About 12 M pixel-dates, but highly redundant ones.
* **180 m pixels.** Rivers and fields are 1–5 pixels wide, so the textures and shapes that ImageNet filters encode do not exist at this scale. What the filters do pick up is speckle and preview-compression texture.
* **Two channels of uncalibrated display brightness.** Water is defined almost completely by "dark in both channels", which is a 2-number rule.
* **No labels.** Self-supervised reconstruction rewards modelling every bit of land texture, not the water/land distinction we care about.
* **Photo-pretrained filters.** They expect RGB natural-image statistics. In the RGB variant this was the worst choice.

**When it would pay off:**

* Calibrated γ⁰ at 10–20 m (NISAR GCOV full resolution), where field boundaries, levees and flooded-forest edges are many pixels wide.
* Many scenes and seasons, so the encoder learns radar-specific features (a radar-pretrained backbone, for example from Sentinel-1 self-supervision, would be the right start).
* Real labels: then a pretrained encoder is a good start for a supervised segmentation head.

**Recommendation:** keep the math detector in the app. Do not show this model in the app. In the pitch, say in one line that we tried it and why it did not help, as evidence that we tested the bigger-model idea instead of assuming it.

## 4. Commands and timings (M5, 24 GB, MPS, GPU shared with other jobs)

```
.venv/bin/python -m scripts.detect.deep_ae train --area tonle-sap --variant b4_pre_ft      # 72 s
.venv/bin/python -m scripts.detect.deep_ae train --variant b4_pre_frozen                   # 55 s
.venv/bin/python -m scripts.detect.deep_ae train --variant b4_rand_ft                      # 151 s
.venv/bin/python -m scripts.detect.deep_ae train --variant b4_pre_ft_rgb                   # 53 s
.venv/bin/python -m scripts.detect.deep_ae train --variant b4_pre_ft_skip                  # 83 s
.venv/bin/python -m scripts.detect.deep_ae sweep --jobs 1        # all missing variants as subprocesses (--jobs 2 for parallel)
.venv/bin/python -m scripts.detect.deep_ae compare               # whole table + figures, 624 s
.venv/bin/python -m scripts.detect.deep_ae compare --methods "B4 ft,raw HH/HV"   # subset
.venv/bin/python -m scripts.detect.deep_ae predict --area sevan  # 2 s (Tonle Sap 14 s)
.venv/bin/python -m pytest -q tests/test_deep_ae.py              # 10 tests, about 15 s, no download
```

Variants live in `VARIANTS` at the top of `deep_ae.py`. Products are written to `data/processed/<area>/deep_ae/products.pt`:

* `dates`, `bounds`, `km2_per_px`
* `cluster` [T, H, W] uint8 (255 = invalid)
* `p_cluster` (posterior of the assigned cluster), `p_water` (summed water-cluster posterior)
* `cluster_names`, `water_clusters`
* `anomaly` [T, H, W] (−log likelihood)
* `valid`, `water_km2` [T]

## 5. Limitations

* **Partial pretrained weights.** Only the first 4 stages of the B4 ImageNet weights were obtained, by range requests. They were not checksummed, and the full 19 M-parameter network was never run. Results for deeper or full B4 are untested.
* **Short training.** Each variant had 300 steps of 8 patches, about 1 min. Longer training might change the embeddings, but the frozen-vs-fine-tuned and pretrained-vs-random gaps were small and pointed the wrong way for pretraining.
* **One training seed per variant.** "Seed ARI" measures only GMM restarts, not network retraining.
* **BIC always chose the maximum k = 10.** With 60,000 samples BIC keeps rewarding more components. A larger k range was not tried.
* **Water-cluster naming uses the math detector's labels from the west.** It is not fully label-free, and the ≥ 50 % rule is one arbitrary choice applied to all methods.
* **ARI/NMI and the probe measure agreement with the math detector, not truth.** The JRC reference is 1984–2021 Landsat, not 2026.
* **A small embedding cell.** The embedding cell is 4 × 4 native pixels. The bilinear upsampling of a stride-4 map leaves a faint periodic grid, visible in the Sevan anomaly map. A different upsampling (or stride 2) was not tried for lack of time.
* **TemporalAE comparison.** The TemporalAE row uses its existing weights (trained earlier with its own recipe), plus this date's HH and HV, so it is not a pure "AE only" score.
* **Probe test design.** The probe uses class-balanced training sets with a prior correction. A west → east distribution shift limits every method.
* **Timings are indicative only.** All of them were taken with the GPU shared with other agents.
* **Terrain mask.** The comparison target is the terrain-masked math detector, computed freshly by `run_pipeline(..., water_ok=...)`. Tonle Sap and Sevan both have a cached terrain mask.
