# A transformer over time: filling the 24-day outage

Code: [`scripts/detect/ttransformer.py`](../../scripts/detect/ttransformer.py) · tests: [`tests/test_ttransformer.py`](../../tests/test_ttransformer.py) · figures: [`docs/eda/ttransformer/`](ttransformer/) · raw numbers: `docs/eda/ttransformer/loo_*.json`, `gap_*.json`.

## The idea in plain words

Every pixel has ten radar values in 2026, one per pass (HH and HV). We hide one or two of those dates and teach a small network to guess the hidden values from the rest. No labels are used: the hidden values themselves are the answer key ("masked-date modelling", like BERT's masked words, but the words are dates). Time is fed in as the actual day of year, not as "position 1, 2, 3…", so the network knows that 25 July and 18 August are 24 days apart, and it can be asked about a day on which no pass exists, such as 6 August 2026, inside the instrument outage (no data 27 July to 10 August).

Then we check it honestly: hide a pass we *do* have, predict it, and compare with the real image, against simple rules such as "draw a straight line in time between the two neighbouring passes" (linear interpolation).

**Verdict first.** The transformer beats linear interpolation by a small margin on pixel values (RMSE skill +15 % when the model has seen the other passes' dates elsewhere in the scene, about +2 % in the stricter test that matches the outage), but **not** on the water map: the shoreline drawn from its predictions is slightly worse than the shoreline from linear interpolation (IoU 0.40 vs 0.42). Its 6 August estimate is plausible and bounded, but it is not more trustworthy than a straight line between 25 July and 18 August. Show it in the app only as a clearly labelled "model estimate", if at all.

## Data the model sees

The detector's own filtered values: `ops.run_pipeline` (Quegan multi-temporal speckle filter, 3×3 box, per-date gain normalisation, edge erosion, common footprint, and now the terrain mask `terrain.cached_water_possible`) gives the log-like intensity $L_t$ (preview display brightness, 0..1, **not** calibrated backscatter). Pixels valid on all ten dates only. Tonle Sap, 1105 × 1078 px at ~180 m.

Spatial split as in `train.py`: training pixels from the **western 70 %** of columns, all metrics on the **eastern 30 %**.

## Architecture

For a pixel, token $t$ (one per date) has features

$$f_t = [\,HH_t,\ HV_t,\ HH_t-HV_t,\ \bar{HH}_t,\ \bar{HV}_t,\ s_{HH,t},\ s_{HV,t}\,]$$

where $\bar{\cdot}$ and $s$ are the mean and standard deviation of $L$ over the 5 × 5 neighbourhood (variant `ctx5`; variant `base` uses the first three only). Features are standardised with training-set means.

**Continuous-time encoding.** With $d$ the day of year,

$$\tau(d) = \big[\sin(2\pi d/P_i),\ \cos(2\pi d/P_i)\big]_{P_i\in\{6,12,24,48,96,192,384\}} \oplus \tfrac{d-170}{120},$$

passed through a 2-layer MLP to $\mathbb{R}^{48}$ and added to the token embedding. Because $\tau$ is a function of the real day, unequal gaps are represented exactly and any day can be queried.

**Hidden dates.** A hidden (or asked-for) date's token is replaced by a learned mask vector $m$ (plus its $\tau(d)$). In the final variant `ctx5res` the hidden token also receives the **linear interpolation** $\hat f^{\,lin}_t$ of its nearest *visible* neighbours and the length of the hole, and the output is a correction to it:

$$\mu_t = \hat{L}^{lin}_t + W_\mu h_t,\qquad \log\sigma_t^2 = W_\sigma h_t,$$

where $h_t$ is the transformer's output for token $t$ (3 pre-norm encoder layers, $d_{model}=48$, 4 heads, feed-forward 96, dropout 0.05). The interpolation uses visible tokens only, so nothing about the hidden value leaks in (tested).

**Loss: Gaussian negative log-likelihood on hidden dates only.**

$$\mathcal{L} = \frac{1}{|\mathcal{H}|}\sum_{t\in\mathcal{H}}\sum_{c\in\{HH,HV\}} \tfrac12\Big(\log\sigma_{t,c}^2 + \frac{(y_{t,c}-\mu_{t,c})^2}{\sigma_{t,c}^2}\Big)$$

$\mathcal{H}$ = hidden dates; $y$ = true value. The variance head lets the model report its own uncertainty $\sigma$.

**Masking scheme.** Per training pixel: 50 % one random date hidden, 25 % two random dates, 25 % **two adjacent dates** (so a hole twice the normal length is in the training distribution, e.g. 30 Aug + 11 Sep predicted from 18 Aug and 23 Sep). Note that the outage itself looks like the *single*-hidden case: hiding 30 Aug leaves 18 Aug and 11 Sep, 24 days apart, with the query in the middle, which is exactly 25 Jul / 6 Aug / 18 Aug.

| variant | features | parameters | epochs | training time (MPS, shared GPU) | east hold-out MSE, one hidden interior date (linear interp. = 0.00277) |
|---|---|---|---|---|---|
| base | HH, HV, HH−HV | 60,532 | 4 | 69 s (11–23 s/epoch) | 0.00301 |
| ctx5 | + 5×5 mean / sd | 60,724 | 8 | 132 s (15–20 s/epoch) | 0.00262 |
| **ctx5res** | + 5×5, residual on linear interpolation | **61,156** | 8 | **160 s** (14–24 s/epoch) | **0.00233** |

300,000 training pixel series (west), batch 2048, AdamW (lr 2e-3, one-cycle, weight decay 1e-4), seed 0, Apple M5 GPU (MPS) shared with other jobs. `base` and `ctx5` were not trained for the same number of epochs, so the context effect is not cleanly isolated; the residual change made the clearest difference (without it the model's water came out too bright and the shoreline IoU dropped to 0.34). Figure: [`training_curve.png`](ttransformer/training_curve.png).

## Evaluation protocol

For every interior pass $k$ (1 Jul … 5 Oct) and, separately, every adjacent interior pair (double gap):

1. **Re-run the detector's pre-processing without the hidden pass(es).** The speckle filter and the normalisation mix dates, so the full-stack values would leak a little of the hidden pass into its neighbours.
2. Predict the hidden pass from the others with: the **transformer**; **persistence** (previous pass); **next** pass; **linear** interpolation in time between the nearest kept passes; **mean of the other passes**; the **GRU `TemporalAE`** (`train.py`, hidden date's features and valid flag zeroed; it was trained with random date dropout, so it can fill a date); and, for the water test, the **flood-front interpolation** from `novel.py` (built from the other passes' water masks, same left-out dates).
3. Truth = the detector's $L_k$ and water mask from the full stack. Metrics on east-30 % pixels: RMSE (both channels), MAE per channel, the same on **changing pixels** (detector season class = seasonal open water, flooded vegetation or other lasting change), skill $=1-\mathrm{MSE}_{model}/\mathrm{MSE}_{linear}$.
4. **Shoreline test.** The predicted image goes through *exactly* the detector's water rule `ops.water_masks` (HH hysteresis: seed HH < 0.06, grow through HH < 0.12, HV < 0.35, 8-connected, specks < 4 px removed) and the terrain mask, on the whole scene; scored on the east 30 %: IoU, missed and false km², mean boundary distance (symmetric mean distance between the two boundaries).

Two training regimes:
- **transductive** (main tables): one model trained on all ten passes of the *west* pixels; evaluated on *east* pixels. The model never saw the east pixels, but it has seen what happens *on that date* in the west.
- **strict** (`--strict`): the model is retrained for each case **without the hidden pass(es) anywhere** (200k pixels, 4 epochs, ~40 s each). This is the situation of the real outage, where no pass exists anywhere. Run for 25 Jul, 18 Aug, 30 Aug, 11 Sep and the pair (18 Aug, 30 Aug) only, for time.

## Results (Tonle Sap, east 30 % hold-out)

### Pixel values: RMSE (skill vs linear interpolation in brackets), transductive, `ctx5res`

| test | pixels | transformer | linear | persistence | next | mean of others |
|---|---|---|---|---|---|---|
| one date hidden | all | **0.0492 (+0.15)** | 0.0533 | 0.0728 (−0.86) | 0.0718 (−0.81) | 0.0822 (−1.38) |
| one date hidden | changing | **0.0605 (+0.12)** | 0.0646 | 0.0949 (−1.16) | 0.0936 (−1.10) | 0.1225 (−2.60) |
| double gap | all | **0.0569 (+0.14)** | 0.0616 | 0.0850 (−0.91) | 0.0854 (−0.92) | 0.0879 (−1.04) |
| double gap | changing | **0.0723 (+0.12)** | 0.0773 | 0.1172 (−1.30) | 0.1159 (−1.25) | 0.1318 (−1.91) |

GRU `TemporalAE` (scored in its own value space, 3×3-smoothed raw brightness, against linear interpolation in that space): MSE 0.00725 vs 0.00311 (skill **−1.33**) for one hidden date, 0.00808 vs 0.00403 (−1.00) for the double gap. It is a season summariser, not a gap filler.

Without the residual (variant `ctx5`): skill +0.03 (all) and −0.05 (changing) for one date, +0.08 / +0.02 for the double gap.

**Strict test** (retrained without the hidden pass), four single dates + one pair:

| test | pixels | transformer | linear | persistence | next |
|---|---|---|---|---|---|
| one date (25 Jul, 18 Aug, 30 Aug, 11 Sep) | all | 0.0538 (+0.02) | 0.0544 | 0.0754 | 0.0823 |
| one date | changing | 0.0717 (+0.04) | 0.0733 | 0.1101 | 0.1158 |
| pair 18 Aug + 30 Aug | all | 0.0616 (−0.02) | 0.0612 | 0.1150 | 0.0665 |
| pair | changing | 0.0881 (0.00) | 0.0882 | 0.1840 | 0.0950 |

On the same four single dates the transductive model has skill +0.09, so most of its advantage came from having seen those dates in the west half. **For an unobserved pass the transformer is about as good as linear interpolation, no better.** (Strict models also trained on less data, 200k pixels × 4 epochs, so part of the drop is budget.)

The noise floor: speckle and display noise in the previews is about 0.02–0.05 in brightness (`docs/eda/stats-change.md`); all errors here (0.04–0.07) are close to it.

### Per date (one date hidden, transductive)

| hidden date | hole (days) | RMSE transformer | RMSE linear | RMSE persistence | changing: transformer / linear | water IoU transformer / linear / front | water km² (whole scene) true / transformer / linear |
|---|---|---|---|---|---|---|---|
| 1 Jul | 24 | 0.0467 | 0.0478 | 0.0573 | 0.0495 / 0.0490 | 0.627 / 0.638 / 0.505 | 2297 / 2211 / 2226 |
| 13 Jul | 24 | 0.0471 | 0.0529 | 0.0592 | 0.0495 / 0.0551 | 0.577 / 0.556 / 0.529 | 2310 / 2275 / 2262 |
| 25 Jul | 36 | 0.0579 | 0.0634 | 0.0630 | 0.0809 / 0.0904 | 0.623 / 0.598 / 0.630 | 2421 / 2277 / 2332 |
| 18 Aug | 36 | 0.0568 | 0.0577 | 0.1091 | 0.0812 / 0.0823 | 0.206 / 0.206 / 0.204 | 2803 / 2479 / 2502 |
| 30 Aug | 24 | 0.0439 | 0.0434 | 0.0624 | 0.0562 / 0.0553 | 0.468 / 0.506 / 0.428 | 2954 / 2739 / 2801 |
| 11 Sep | 24 | 0.0477 | 0.0512 | 0.0547 | 0.0598 / 0.0591 | 0.244 / 0.464 / 0.392 | 3147 / 2686 / 2991 |
| 23 Sep | 24 | 0.0504 | 0.0647 | 0.0944 | 0.0504 / 0.0665 | 0.267 / 0.163 / 0.222 | 3522 / 3364 / 3061 |
| 5 Oct | 24 | 0.0403 | 0.0407 | 0.0627 | 0.0443 / 0.0448 | 0.217 / 0.251 / 0.260 | 3541 / 3331 / 3509 |

The double-gap table per date is in `loo_tonle-sap_ctx5res.json`; figure [`loo_error_per_date.png`](ttransformer/loo_error_per_date.png).

### Shoreline test (east 30 %, mean over dates)

| test | method | IoU | missed km² | false km² | mean boundary distance (km) | abs. area error km² |
|---|---|---|---|---|---|---|
| one date | transformer (transductive) | 0.404 | 171 | 67 | 1.33 | 104 |
| one date | **linear interpolation** | **0.423** | 160 | 76 | **1.21** | 93 |
| one date | persistence | 0.395 | 154 | 100 | 1.21 | 88 |
| one date | next | 0.362 | 128 | 196 | 1.37 | 100 |
| one date | mean of others | 0.236 | 235 | 10 | 2.41 | 225 |
| one date | flood front (`novel.py`) | 0.396 | 166 | 67 | 1.40 | 102 |
| double gap | transformer | 0.353 | 175 | 58 | 1.60 | 118 |
| double gap | **linear interpolation** | **0.379** | 171 | 65 | **1.41** | 107 |
| double gap | persistence | 0.338 | 174 | 64 | 1.43 | 113 |
| double gap | next | 0.305 | 126 | 235 | 1.53 | 129 |
| double gap | mean of others | 0.222 | 234 | 13 | 2.42 | 221 |
| double gap | flood front (`novel.py`) | 0.355 | 178 | 52 | 1.52 | 126 |
| one date, strict (4 dates) | transformer | 0.398 | 133 | 34 | 1.58 | 99 |
| one date, strict (4 dates) | linear | 0.443 | 118 | 43 | 1.11 | 75 |

IoUs are low for every method because the east 30 % holds only a little water (most of the lake is in the west) and much of it is thin, noisy seasonal water. **Every** interpolating method under-counts water (the water rule needs very dark seed pixels, and any averaging lifts the darkest pixels): the transformer by 50–460 km², linear interpolation by 20–460 km² over the whole scene. The flood-front numbers come from `novel.py` as it was on disk at run time (it was still being developed).

Example: [`example_hidden_date.png`](ttransformer/example_hidden_date.png) (30 Aug hidden; east strip; difference map in a diverging scale ±0.15).

### Is the predicted uncertainty calibrated?

On east pixels, one hidden date: **69 % of errors lie within ±1σ (ideal 68 %) and 88 % within ±1.645σ (ideal 90 %)**; mean predicted σ 0.041 vs actual RMSE 0.049. Strict test: 79 % / 91 %. The reliability plot ([`reliability.png`](ttransformer/reliability.png)) shows that σ ranks pixels well (actual error rises with σ along the diagonal), with a slight under-statement for the most confident pixels. So **per pixel, yes, roughly calibrated**. But the errors are spatially correlated, and turning per-pixel σ into an area range is not solved: shifting every pixel by ±1.645σ together gives a range far too wide (it contained the true area on 100 % of left-out dates, but it spans ~2,100–4,800 km²); sampling pixels independently gives a range far too narrow (±4 km²).

## The outage: 30 July, 6 August, 12 August 2026

`predict-gap` queries the model at those days with all ten passes visible (eleven tokens: ten observations + one query; the query sits 5–12 days from its observed neighbours, so the hole is the familiar 24-day one).

| day | raw transformer estimate | anchored estimate* | linear interp. of the images | ±1.645σ shared shift (not useful) | independent-pixel sampling 5–95 % |
|---|---|---|---|---|---|
| 25 Jul (observed) | detector: 2,421 | | | | |
| 30 Jul | 2,331 km² | 2,504 km² | 2,426 km² | 2,133–2,887 | 2,367–2,371 |
| **6 Aug** | **2,516 km²** | **2,737 km²** | 2,474 km² | 2,240–3,635 | 2,589–2,596 |
| 12 Aug | 2,520 km² | 2,781 km² | 2,556 km² | 2,037–3,615 | 2,580–2,590 |
| 18 Aug (observed) | detector: 2,803 | | | | |

\*The same model queried *at* 25 Jul and 18 Aug gives 2,282 and 2,501 km² instead of the observed 2,421 and 2,803 (the low bias seen in the leave-one-out test). "Anchored" removes that offset, blended linearly in time between the two ends: $A^{anch}(d)=A(d)+(1-w)\,e_a+w\,e_b$, $w=(d-d_a)/(d_b-d_a)$, $e=A^{obs}-A^{model}$ at each end.

**Where:** all the estimated new water on 6 August (125 km² newly water vs 25 Jul) lies inside the zone that is newly water on 18 August (501 km² in total); under 4 km² falls outside it. It sits on the lake's north-west rim and as scattered patches on the south-east river plain, i.e. the model puts about a quarter of the 25 Jul → 18 Aug growth before 6 August and the rest after. [`outage_tonle-sap.png`](ttransformer/outage_tonle-sap.png), [`area_curve_tonle-sap.png`](ttransformer/area_curve_tonle-sap.png).

**What this is:** a model estimate of an unobserved pass, bounded by the observations on both sides, with its skill measured by the leave-one-out tests above (strict: about equal to linear interpolation). It is **not** an observation. The model cannot know about anything that left no trace in the passes on either side: if the river plain flooded suddenly on 3 August, nothing in the data says so, and the model will spread the change smoothly instead.

### Null controls (Tonle Sap weights applied unchanged)

| area | neighbours (26 Jul / 19 Aug) | estimate 31 Jul / 7 Aug / 13 Aug | mean abs. difference from linear interpolation of the images |
|---|---|---|---|
| Sevan (lake, no change) | 1,253 / 1,241 km² | 1,234 / 1,244 / 1,229 km² | 0.010–0.017 |
| Ararat (nothing changes) | 0.58 / 0.68 km² | 0.34 / 0.37 / 0.37 km² | 0.010–0.012 |

Predicted change ≈ 0 in both: the model does not invent events. Products: `data/processed/{sevan,ararat}/ttransformer/products.pt`.

## What the attention learned

[`attention.png`](ttransformer/attention.png): mean attention (all layers and heads, 20k east pixels) from the hidden-date query to every date. It is broad (weights 0.08–0.15 where uniform would be 0.10) and favours the mid-season passes 18 Aug – 11 Sep whatever date is hidden, not specifically the two neighbours. That is expected for this variant: the neighbours' interpolation is already handed to the query token, so attention is used to gather "what kind of pixel is this over the season" (does it flood, when) to correct the straight line. Embedding clusters were not done (time).

## Verdict

1. On pixel values the transformer beats linear interpolation by 12–15 % in MSE when it has seen the hidden date elsewhere in the scene, but only by ~2 % (and not at all for the double gap) when the date is unobserved everywhere, which is the outage situation.
2. On the water map it is slightly worse than linear interpolation (IoU 0.40 vs 0.42, boundary 1.33 vs 1.21 km) and about equal to the `novel.py` flood front (0.40); none of the methods is good at the east-side shoreline (IoU 0.2–0.6 per date).
3. The 6 August estimate (2,516 km² raw, 2,737 km² anchored; linear interpolation of the two areas gives 2,612 km²) is plausible and well-behaved on the null controls, but it is not more trustworthy than a straight line between the two passes; in the app show at most a dashed "estimated" segment across the outage, with the observed passes as the only hard points.

## Commands and timings (Apple M5, MPS, GPU shared with other jobs)

```bash
.venv/bin/python -m scripts.detect.ttransformer train --area tonle-sap --variant ctx5res --epochs 8   # 160 s
.venv/bin/python -m scripts.detect.ttransformer evaluate --area tonle-sap --variant ctx5res --no-ae   # ~7 min (15 cases, each re-runs the pipeline)
.venv/bin/python -m scripts.detect.ttransformer evaluate --area tonle-sap --variant ctx5res --no-ae --strict \
    --strict-pixels 200000 --strict-epochs 4 --only "3;4;5;6;4,5"                                   # ~8 min
.venv/bin/python -m scripts.detect.ttransformer predict-gap --area tonle-sap --variant ctx5res --draws 10   # ~1.5 min
.venv/bin/python -m scripts.detect.ttransformer predict-gap --area sevan --variant ctx5res --draws 5        # uses tonle-sap weights
.venv/bin/python -m scripts.detect.ttransformer predict-gap --area ararat --variant ctx5res --draws 5
.venv/bin/python -m scripts.detect.ttransformer figures --variant ctx5res
.venv/bin/python -m pytest -q tests/test_ttransformer.py                                              # 8 tests, ~20 s CPU
```

Other variants: `--variant base|ctx5|novar|small|baseres` (dict `VARIANTS` at the top). The first `evaluate` (no `--no-ae`) also scores the GRU autoencoder and caches baseline metrics in `data/processed/models/ttransformer_cache_<area>.pt`.

Outputs: weights `data/processed/models/ttransformer_tonle-sap_<variant>.pt`; `data/processed/<area>/ttransformer/products.pt` with `dates`, `bounds`, `km2_per_px`, `gap_days`, `pred_gap` [3,2,H,W] float16, `pred_gap_sd` [3,2,H,W] float16, `water_gap` [3,H,W] bool, `water_gap_km2`, `water_gap_km2_lo/hi` (the ±1.645σ shared-shift range, see the caveat above), `loo_metrics`, `area_curve` (every 2 days across the gap).

## Limits

- **Ten dates.** One season, one hidden date at a time; the per-date numbers rest on single images, and the strict test covers only four dates and one pair.
- **Transductive vs strict.** The headline +15 % needs the hidden date to have been seen elsewhere; for a truly unobserved pass the gain is ~2 %.
- **Uncalibrated previews**: 8-bit display brightness, a log-like stretch, ~180 m pixels, noise floor 0.02–0.05. All thresholds are the detector's preview thresholds.
- **A model cannot know about an event that left no trace** in the passes on either side (a sudden surge and retreat inside the outage would be invisible). Predictions regress towards smooth transitions between the neighbours.
- **Water area is biased low** for every interpolator (the darkest pixels get lifted above the 0.06 seed threshold); the anchored correction is a fix of the symptom, not of the cause. A model that predicts water probability directly would be the next step.
- **Area uncertainty is not solved**: per-pixel σ is roughly calibrated, but no spatial error correlation is modelled, so the two area ranges given are too wide (shared shift) and too narrow (independent sampling).
- The query for the outage uses eleven tokens (ten observed + one query) while training used ten slots with one or two masked; transformers handle this, but it is a small distribution shift.
- The comparison with the flood front used `novel.py` while it was still being changed; the front numbers moved between two of our runs (IoU 0.43 → 0.40), so treat that comparison as provisional.
- Spatial context (5×5) and the variance head were not isolated in a clean ablation (variants trained for different epochs); embeddings/clusters were not done.
