# Probabilistic detector: Gaussian mixtures + hidden Markov model

Code: [`scripts/detect/gmm.py`](../../scripts/detect/gmm.py) · tests: [`tests/test_gmm.py`](../../tests/test_gmm.py) ·
numbers: [`gmm/results.json`](gmm/results.json) · products: `data/processed/<area>/gmm/products.pt` ·
run: `.venv/bin/python -m scripts.detect.gmm [area ...] [--pooled]`.

**In one paragraph.** We replace thresholds picked by eye with two small Gaussian mixtures fitted by
expectation–maximisation (EM). One describes what a pixel looks like on a date: HH and HV brightness. The other
describes how it changed since the dry season. Each component is assigned to "open water", "can be flooded
vegetation" or "land" by one written rule. A hidden Markov model (HMM) then reads each pixel's ten dates as one
sequence. It uses the mixtures as emission densities and learns the transition probabilities from the data
(Baum–Welch). The output is a probability per pixel and date, not a yes/no answer. Every number below comes from
`gmm/results.json`. The JRC Global Surface Water maps were used only to measure the model, never to fit it.

**Verdict.**
* The HMM fixes the wind problem at Lake Sevan: 1,235 km² largest body on every date, including both windy dates,
  against 1,242 km².
* It removes all false water in the Ararat Valley, with or without the terrain mask.
* It gives honest per-date probabilities. The per-date mixture is well calibrated. The HMM is sharper but
  over-confident in its rare uncertain cases.
* It does **not** beat the current detector at Tonle Sap. Open water on 17 October is 3,326 km² against 3,837 km²,
  and the tuning study already calls 3,837 km² a lower bound. Permanent-water IoU against JRC is the same.
* Its flooded-vegetation layer is larger and less specific than the old one: 3,800 km² on any date, 70 % within
  one pixel of JRC water.

Recommendation (section 7): use it for Sevan/Armenia and for probabilities in the app. Do not use it for the
Tonle Sap headline area.

---

## 1. Inputs and features

The input is $L_{t,c}(p)$, the output of `ops.run_pipeline`: preview brightness in $[0,1]$ after the multi-temporal
speckle filter and per-date gain normalisation. Here $t=1..10$ is the date, $c\in\{HH,HV\}$ the channel and $p$ the
pixel. Only pixels valid on all dates are used.

**Why not Gaussians on the raw brightness.** Calm water sits on the 8-bit display floor. 5 % of Tonle Sap and 22 %
of Sevan pixel-dates have HH < 0.02, and the 1st–10th percentiles at Sevan are all 0.0034–0.0038. That is a point
mass, which a Gaussian cannot model: EM would either spend a component with near-zero variance on it, or smear water
into land. Saturated bright pixels pile up at 1 in the same way. The statistics study
([`stats-change.md`](stats-change.md)) found that preview brightness behaves like a logarithmic (dB-like) stretch of
the intensity. Brightness is therefore already a log quantity, where Gaussian classes are the standard
approximation; what remains to fix is the two clipped ends. We use a **logit with an offset**:

$$a_{t,c}(p)=\log\frac{L_{t,c}(p)+\varepsilon}{1-L_{t,c}(p)+\varepsilon},\qquad \varepsilon=0.01 .$$

In the middle of the range this is close to linear. It maps the floor to −4.3 and saturation to +4.6, so each pile-up
becomes a narrow but finite Gaussian (component 0 at Tonle Sap: sd 0.04). Covariance regularisation keeps that
Gaussian from collapsing. We considered a floor-censored component but did not use it: the logit already makes the
floor a small separate cluster, and a censored likelihood would not fit in the time available.

**Appearance features** $\mathbf a_t=(a_{t,HH},a_{t,HV})$.
**Change features** $\mathbf c_t=\mathbf a_t-\bar{\mathbf a}_{\text{ref}}$, where
$\bar{\mathbf a}_{\text{ref}}$ is the mean of the first two dates (the dry-season reference of the current detector).
$e^{c_{t,HH}}$ is the factor by which the HH odds changed.

We also tried a single 4-D mixture on $(\mathbf a,\mathbf c)$ and rejected it. Depending on the seed, the "newly
flooded" pixels went into a land component, and the Tonle Sap water area came out flat at about 1,970 km² on all
dates. HH−HV as a third feature is a linear combination of the two logits, so it would make the covariance singular.

## 2. Gaussian mixtures by EM (`fit_gmm`)

$$p(\mathbf x)=\sum_{k=1}^{K}w_k\,\mathcal N(\mathbf x\mid\boldsymbol\mu_k,\boldsymbol\Sigma_k),\qquad
\log\mathcal N=-\tfrac12\big[D\log2\pi+\log|\boldsymbol\Sigma_k|+(\mathbf x-\boldsymbol\mu_k)^\top\boldsymbol\Sigma_k^{-1}(\mathbf x-\boldsymbol\mu_k)\big].$$

* **E-step**, with a weight $s_n$ per sample:
  $r_{nk}=\operatorname{softmax}_k\big(\log w_k+\log\mathcal N(\mathbf x_n\mid k)\big)$.
  This is computed with log-sum-exp, so densities are never exponentiated.
* **M-step**:
  * $N_k=\sum_n s_n r_{nk}$
  * $w_k=N_k/\sum_j N_j$
  * $\boldsymbol\mu_k=\sum_n s_n r_{nk}\mathbf x_n/N_k$
  * $\boldsymbol\Sigma_k=\sum_n s_n r_{nk}(\mathbf x_n-\boldsymbol\mu_k)(\mathbf x_n-\boldsymbol\mu_k)^\top/N_k+\lambda\mathbf I$, with $\lambda=10^{-3}$
* **Initialisation**: k-means++ seeding plus 10 weighted Lloyd iterations.
* **Restarts**: 3 restarts with fixed seeds; the one with the highest log-likelihood is kept.
* **Stopping**: when the mean log-likelihood improves by less than $10^{-6}$.
* **Fitting set**: 100,000 random valid pixel-dates per area, fitted in float64 on the CPU. Posteriors for every
  pixel are then evaluated on the Apple GPU (MPS) in float32, through the inverse Cholesky factor.
* **Model size**: $\mathrm{BIC}=-2\log\mathcal L+P\log N$ and $\mathrm{AIC}=-2\log\mathcal L+2P$, with
  $P=(K-1)+KD+KD(D+1)/2$.

**Choosing K** ([`gmm/bic.png`](gmm/bic.png)). Each curve below is fitted on 30,000 samples.
* For appearance, BIC keeps falling up to K = 8 in every area. With 10⁵ samples BIC rewards any extra structure,
  for example quantisation steps and swath texture, so it cannot pick K on its own.
* For change, BIC flattens from K ≈ 4. Its minimum is at K = 6 at Sevan and at K = 7 at Tonle Sap.
* We use **K = 6 for both mixtures**. It is the knee of both curves, it is the smallest K at which the Tonle Sap
  change mixture separates a double-bounce component, and it keeps the fit under ~30 s per area.

**Seed stability** (3 single-start refits, components matched by mean).
* Change mixtures are stable: the largest matched mean shift is 0.1–0.7 logit and the log-likelihoods agree to 0.002.
* Appearance mixtures at Tonle Sap and Sevan have more than one local optimum. One of three seeds found a worse
  solution, with a log-likelihood 0.08 nats lower and a component that moved by 6.5 logit. This is why we keep the
  best of 3 restarts. Ararat is stable (shift < 0.2).

### What the components are (per-area fits; full list in `results.json`)

The appearance components at Tonle Sap and how the rule labels them:

| Tonle Sap appearance | weight | mean HH | mean HV | rule says | where on the map / reading |
|---|---|---|---|---|---|
| 0 | 0.041 | 0.004 | 0.004 | open water | calm lake at the display floor (sd 0.04 logit: the "point mass") |
| 1 | 0.033 | 0.032 | 0.017 | open water | rough or shallow water, flooded fields, lake edge |
| 2 | 0.340 | 0.48 | 0.35 | land | cropland / grass |
| 3 | 0.289 | 0.54 | 0.40 | land | cropland, slightly brighter |
| 4 | 0.192 | 0.82 | 0.86 | land | forest (HV ≈ HH: volume scattering) |
| 5 | 0.104 | 0.94 | 0.84 | land | very bright HH > HV: towns, flooded forest, double bounce |

The change components at Tonle Sap, as factors on the odds relative to the dry-season reference:

| Tonle Sap change | weight | HH factor | HV factor | rule says |
|---|---|---|---|---|
| 0 | 0.020 | ×0.23 | ×0.14 | other: both channels collapse (land → open water) |
| 1 | 0.167 | ×0.62 | ×0.63 | other: darkening (wetting, harvest, canopy submerging) |
| 2 | 0.130 | ×0.67 | ×0.85 | other: HH falls more than HV |
| 3 | 0.551 | ×1.01 | ×1.03 | other: no change |
| 4 | 0.098 | ×1.51 | ×1.40 | other: general brightening |
| 5 | 0.034 | **×3.99** | ×1.23 | **flooded-vegetation change**: HH up ×4 while HV barely moves (double bounce) |

At Sevan the water components sit at HH 0.004–0.04, but **HV is 0.10–0.22**. The lake surface's HV is not at the
floor, so the water rule has to allow HV up to 0.25. Sevan's mixtures contain no flooded-vegetation component.
Ararat has no dark component at all: its darkest component has mean HH 0.24. The whole method rests on that fact
there.

## 3. From components to states: one rule

A rule on the component **means** (`RULE` in the code), never on pixels:

* **Open-water appearance**: mean HH ≤ 0.08 and mean HV ≤ 0.25 (specular, dark).
* **Flooded-vegetation change**: the HH odds at least doubled, $\mu_{c,HH}\ge\ln2$, and HV rose by at least
  0.25 nats less, $\mu_{c,HV}\le\mu_{c,HH}-0.25$.
* **Bright appearance**: not water and mean HH ≥ 0.25. This is the appearance flooded vegetation must have.

The state emission densities, a naive-Bayes product of the two mixtures with each restricted to its component
subset (sub-mixture weights renormalised):

$$
\begin{aligned}
b_{\text{water}}(\mathbf a,\mathbf c)&=p(\mathbf a\mid\text{water comps})\;p(\mathbf c)\\
b_{\text{fv}}(\mathbf a,\mathbf c)&=p(\mathbf a\mid\text{bright comps})\;p(\mathbf c\mid\text{fv comps})\;\big(1-P(\text{water}\mid\bar{\mathbf a}_{\text{ref}})\big)\\
b_{\text{land}}(\mathbf a,\mathbf c)&=p(\mathbf a\mid\text{non-water comps})\;p(\mathbf c\mid\text{non-fv comps})
\end{aligned}
$$

Each density is then mixed with a small "looks like anything" term, so that no state has zero density anywhere:

$$b_s\leftarrow(1-\eta)\,b_s+\eta\,p(\mathbf a)p(\mathbf c),\qquad \eta=0.05 .$$

That leak is what allows a lake pixel to look like land on one windy pass.

The factor $1-P(\text{water}\mid\bar{\mathbf a}_{\text{ref}})$ says that double bounce needs vegetation standing on
land: a lake that brightens is wind, not flooded forest. This replaces the old "not in the permanent-water prior"
clause. Without it, the synthetic windy lake went to flooded vegetation.

**Per-date posterior (no time).** $P(s\mid\mathbf x_t)\propto\pi^{\text{mix}}_s\,b_s(\mathbf x_t)$, where
$\pi^{\text{mix}}$ is the share of each state implied by the mixture weights. This gives `p_water_date` and
`p_fv_date`.

**Equivalent threshold.** With no change since the reference, $P(\text{water})=0.5$ is reached at:

| Area | HH where P(water) = 0.5, for HV = HH/2 | for HV = HH |
|---|---|---|
| Tonle Sap | 0.102 | 0.044 |
| Sevan | 0.042 | 0.041 |

That lies between the hysteresis values 0.06/0.12 and far below the global Otsu values on HH (0.53 at Tonle Sap,
0.39 at Sevan). Otsu splits land types, not water from land. The mixture's boundary depends on HV and adapts per
area.

## 4. Hidden Markov model over the ten dates (`fit_hmm`, `forward_backward`, `viterbi`)

The states are $S=\{$open water, flooded vegetation, land$\}$. A 4-state variant splits land into dark and bright.
The parameters are the initial distribution $\pi_i=P(s_1=i)$ and the 12-day transition matrix
$A_{ij}=P(s_{t+1}=j\mid s_t=i)$.

**Unequal step.** The passes of 25 July and 18 August are 24 days apart. We insert an **unobserved phantom slot**
there, with emission $\log b=0$. The chain therefore makes two 12-day steps across the gap, so the transition across
it is exactly $A^2$, and Baum–Welch stays an exact EM. The test `test_phantom_slot_equals_squared_transition` checks
this against $A\,A$. Invalid dates also get $\log b=0$, meaning they carry no evidence.

**Forward–backward** in log space, vectorised over pixels:

$$\log\alpha_{t}(j)=\log b_j(\mathbf x_t)+\operatorname{LSE}_i\big[\log\alpha_{t-1}(i)+\log A_{ij}\big],\qquad
\log\beta_{t}(i)=\operatorname{LSE}_j\big[\log A_{ij}+\log b_j(\mathbf x_{t+1})+\log\beta_{t+1}(j)\big]$$

The smoothed posterior is $\gamma_t(i)=\alpha_t(i)\beta_t(i)/P(\mathbf x)$, and
$\xi_t(i,j)\propto\alpha_t(i)A_{ij}b_j(\mathbf x_{t+1})\beta_{t+1}(j)$.

**Baum–Welch M-step.** $\pi_i\propto\sum_p\gamma_1(i)+\alpha^\pi_i$ and
$A_{ij}\propto\sum_{p,t}\xi_t(i,j)+\alpha^A_{ij}$. The $\alpha$ are optional Dirichlet pseudo-counts. The template
favours persistence and land → flooded vegetation → water, scaled to 1 % of the pixel count. Baum–Welch runs on
200,000 random pixels. Smoothing and Viterbi then run on every pixel in chunks of 250,000, in float32, with outputs
stored as float16/uint8.

**Viterbi** gives the most likely path. From it we derive `first_water` (first date in the water state),
`first_fv`, `n_transitions` and `path_dry_fv_water` (land, then later flooded vegetation, then later water).

**Learned transition matrix, Tonle Sap, per 12 days, no prior** ([`gmm/reliability_transitions.png`](gmm/reliability_transitions.png)):

| from \ to | water | flooded veg | land |
|---|---|---|---|
| water | **0.9975** | 0.0006 | 0.0019 |
| flooded veg | 0.0016 | **0.8987** | 0.0998 |
| land | 0.0035 | 0.0140 | **0.9825** |

$\pi$ = (0.064, 0.000, 0.936).

Reading it:
* Open water almost never leaves.
* Each pass, 1.4 % of land becomes flooded vegetation and 0.35 % becomes open water.
* Flooded vegetation stays flooded vegetation for about 10 passes on average (1/(1−0.899)).
* When flooded vegetation does change, it goes back to land (10 %), **not on to open water** (0.16 %).

So the "dry → bright → dark" sequence is rare in these data: 8 km² follow it, 99.6 % of them inside the JRC maximum
extent, centred near 104.69 E, 12.67 N on the south-east floodplain (red dots in
[`gmm/first_water_tonle_sap.png`](gmm/first_water_tonle_sap.png)). Most flooded vegetation is the seasonally flooded
forest ring around the lake, which brightens in HH from July and stays bright.

**With the Dirichlet prior** the matrix barely moves: flooded veg → water 0.0077, FV persistence 0.893. Areas change
by less than 1 %, and the dry → FV → water path grows from 8 to 22.5 km². The data dominate; the prior is not doing
the work.

At Sevan, $A_{\text{water,water}}=1.000$ and $A_{\text{land,land}}=1.000$, and there is no flooded vegetation. The
lake's state never changes, which is the right answer.

**4 states** (dark land / bright land). The log-likelihood is better by 2.2 nats per pixel at Tonle Sap for 7 extra
transition parameters, so BIC clearly prefers it. The outputs barely change: water is 2–3 % lower and peak flooded
vegetation is 3,097 km² instead of 3,207 km². We keep 3 states for the products and use the 4-state model as a check.

**Spatial (Potts / mean-field) smoothing** was not done: there was no time. The HMM's temporal smoothing already
removes most speckle-level flicker (see the Viterbi maps).

## 5. Results against the current detector

"Current" means `ops.run_pipeline` **with** the terrain mask `water_ok` (`terrain.cached_water_possible`). For the
HMM, the terrain mask enters as a prior: the water emission is multiplied by $10^{-6}$ where water is impossible. For
the per-date mixture it is a post-hoc cut. Both variants are reported. All areas are inside the JRC-valid footprint.

### 5.1 Lake Sevan (published area 1,242 km²; windy on 31 Aug and 12 Sep)

| Date | current + terrain: all water | current: largest body | per-date mixture: all | per-date: largest | HMM: all | HMM: largest |
|---|---|---|---|---|---|---|
| 20 Jun | 1239.0 | 1229.7 | 1239.0 | 1229.5 | 1261.1 | 1235.3 |
| 2 Jul | 1226.9 | 1215.5 | 1235.1 | 1224.0 | 1261.1 | 1235.3 |
| 14 Jul | 1228.3 | 1218.3 | 1239.3 | 1229.1 | 1261.1 | 1235.3 |
| 26 Jul | 1252.8 | 1230.5 | 1246.3 | 1225.3 | 1261.1 | 1235.3 |
| 19 Aug | 1240.6 | 1226.1 | 1249.3 | 1231.6 | 1261.2 | 1235.3 |
| **31 Aug (windy)** | 1210.5 | 1186.5 | 1188.2 | 1157.7 | **1261.4** | **1235.3** |
| **12 Sep (windy)** | 1176.4 | 1133.8 | 1234.3 | 1201.3 | **1261.4** | **1235.3** |
| 24 Sep | 1244.8 | 1221.9 | 1262.0 | 1233.3 | 1261.5 | 1235.3 |
| 6 Oct | 1251.6 | 1234.1 | 1248.3 | 1233.5 | 1261.5 | 1235.3 |
| 18 Oct | 1255.2 | 1230.8 | 1259.7 | 1232.4 | 1261.5 | 1235.3 |

The HMM's largest body is 1,235.3 km² on every date, −0.5 % from the published area. On the windy dates it keeps the
lake: see [`gmm/sevan_windy.png`](gmm/sevan_windy.png), where the per-date mixture punches a 60 km² hole and the
current detector loses the north-western bay.

False water on land, meaning km² outside the JRC maximum extent and more than 3 px from any JRC water:

| | current + terrain | current, no terrain | per-date + terrain | per-date, no terrain | HMM + terrain | HMM, no terrain |
|---|---|---|---|---|---|---|
| per date (range) | 8.8–22.4 | 14.7–92.6 | 8.4–26.1 | 13.4–42.5 | 22.8–23.0 | 36.1–37.5 |
| any of last 3 dates | 31.6 | 121.7 | 30.9 | 59.4 | **23.0** | 37.5 |

Permanent water (water on ≥ 90 % of dates) against JRC occurrence ≥ 90 %:
* current IoU 0.945
* per-date mixture 0.965
* HMM 0.962 (precision 0.972, recall 0.989)

Without the terrain mask, the HMM has a third of the old detector's late-season false water. With it, the HMM is
level with or better than the masked old detector. Unlike the old detector, the HMM's false water does not grow
through the season. It is a constant ~23 km² of dark slopes that look like water on every date. A time model
cannot remove that; only terrain can.

### 5.2 Ararat Valley (no real open water)

| | current + terrain | current, no terrain | per-date mixture | HMM (with or without terrain) |
|---|---|---|---|---|
| water km² per date | 0.5–3.8 | 10.0–30.6 | **0** | **0** |
| flooded veg km², peak | 31.9 | 31.9 | 0 | 0 |

The Ararat mixtures contain no dark component, so the water state has no support. This is a strength (no
threshold to trip over) and a caveat: a real small pond would also be missed, because the model only knows the
classes present in the fitting sample.

### 5.3 Tonle Sap

Open water, km² per date. The terrain mask removes nothing here (flat floodplain).

| Date | current | per-date mixture | HMM |
|---|---|---|---|
| 19 Jun | 2234 | 2253 | 2289 |
| 1 Jul | 2297 | 2290 | 2300 |
| 13 Jul | 2310 | 2300 | 2331 |
| 25 Jul | 2421 | 2298 | 2395 |
| 18 Aug | 2803 | 2549 | 2717 |
| 30 Aug | 2954 | 2658 | 2869 |
| 11 Sep | 3147 | 2738 | 3009 |
| 23 Sep | 3522 | 3035 | 3267 |
| 5 Oct | 3541 | 2944 | 3311 |
| 17 Oct | **3837** | 3049 | **3326** |

* Growth 19 Jun → 17 Oct: current ×1.72, HMM ×1.45.
* Share of 17 Oct water inside the JRC maximum extent: current 98.9 %, HMM 99.4 %.
* The tuning study ([`tuning.md`](tuning.md)) finds the current detector is already a **lower bound** (plausible
  3,800–5,900 km² on 17 Oct). The mixture is therefore *more* conservative still. Its two water components (the floor
  and HH ≈ 0.03) are narrow, so partly flooded or wind-roughened floodplain pixels with HH 0.05–0.12 go to land. This
  is the main thing the method does **not** improve.

Permanent water against JRC, with the same definitions as `validate.py`:

| Reference | current IoU / P / R | per-date mixture IoU / P / R | HMM IoU / P / R |
|---|---|---|---|
| occurrence ≥ 75 % | 0.837 / 0.994 / 0.842 | 0.806 / 0.998 / 0.808 | 0.836 / 0.976 / 0.853 |
| occurrence ≥ 90 % | 0.634 / 0.685 / 0.896 | 0.644 / 0.705 / 0.882 | 0.630 / 0.673 / 0.908 |
| seasonality = 12 (2021) | 0.897 / 0.982 / 0.912 | 0.868 / 0.989 / 0.877 | 0.890 / 0.963 / 0.922 |

**Rank consistency.** In the ambiguous floodplain (inside the JRC maximum extent, occurrence < 90 %), the Spearman
correlation between JRC long-term occurrence and the number of dates called water is 0.519 for current, 0.458 for
the per-date mixture and 0.490 for the HMM (0.456 using the HMM's mean probability). No improvement.

**Flood timing** ([`gmm/first_water_tonle_sap.png`](gmm/first_water_tonle_sap.png)). This compares the HMM's first
water date with the current CUSUM change date, on the 848 km² where the HMM finds newly flooded water and CUSUM finds
a change:
* identical date: 55 %
* within one pass: 84 %
* Pearson correlation: 0.79
* the HMM is later by 0.67 passes on average

The HMM's first-water area by date:

| First water on | km² |
|---|---|
| 13 Jul | 34 |
| 25 Jul | 92 |
| 18 Aug (after the gap) | 323 |
| 30 Aug | 176 |
| 11 Sep | 139 |
| 23 Sep | 255 |
| 5 Oct | 27 |

The CUSUM map covers far more area because it dates any persistent change, not only the change to open water.

### 5.4 Flooded vegetation

| | current rule | per-date mixture | HMM |
|---|---|---|---|
| Tonle Sap, peak km² (date) | 611 (30 Aug) | 1,678 (30 Aug) | 3,207 (11 Sep) |
| Tonle Sap, any date km² | 1,461 | 2,963 | 3,810 |
| share within 1 px of JRC water | 0.88 | 0.76 | 0.70 |
| Sevan, any date km² | 7.9 | 0 | 0 |
| Ararat, any date km² (all false) | 38.9 | 0 | 0 |

**Sensitivity of the peak area at Tonle Sap.** For the current rule we vary the HH rise threshold; for the two
probabilistic methods we vary the probability threshold.

| Method | Threshold values | Peak km² at each | Ratio |
|---|---|---|---|
| current rule, HH rise | 0.10 / 0.12 / 0.15 / 0.20 | 1,542 / 1,083 / 611 / 139 | **×11** |
| per-date mixture, P(fv) | 0.3 / 0.5 / 0.7 / 0.9 | 2,178 / 1,727 / 1,377 / 753 | ×2.9 |
| HMM, P(fv) | 0.3 / 0.5 / 0.7 / 0.9 | 3,725 / 3,291 / 2,896 / 2,339 | **×1.6** |

Reading these tables:
* The probabilistic layer is far **less fragile** to its threshold.
* It has **no false alarms in Armenia**, against 39 km² for the old rule.
* It is much **larger** at Tonle Sap and slightly less concentrated near JRC water. It is "HH odds × 4 with HV flat
  on a bright pixel that was not water", which is the physics of double bounce. But the change component is broad
  (sd 0.9 logit), and the HMM's persistence (0.90 per pass) carries a pixel through dates where the evidence is
  weak.
* Without ground truth we cannot say whether 600 or 3,200 km² is closer to the truth. The flooded forest ring around
  the lake is expected to be thousands of km² at high water, so the old value may be too small, but this is not
  proven.

### 5.5 Calibration ([`gmm/reliability_transitions.png`](gmm/reliability_transitions.png))

The anchors are JRC permanent water (≥ 99 % of the pixel has occurrence ≥ 90 %, eroded by 2 px) against JRC never
water (no water ever, more than 3 px from any). The shoreline is excluded by construction. Up to 100,000 anchor
pixels per area × 10 dates, all areas pooled:

| | Brier score | accuracy at 0.5 | calibration of the middle bins |
|---|---|---|---|
| per-date mixture | 0.0026 | 0.9970 | good: observed share within 0.12 of predicted in every bin; slightly under-confident (P 0.35 → observed 0.45) |
| HMM | 0.0022 | 0.9978 | **over-confident**: the 1,285 anchor pixel-dates with P between 0.1 and 0.9 are water only 6–19 % of the time |

99.95 % of HMM probabilities are below 0.1 or above 0.9, so the overall score is excellent. But an HMM probability
of 0.5 means "probably not water", and a naive-Bayes product over ten dates is known to be over-confident. For a
probability layer in the app, the **per-date mixture probability is the honest one**.

### 5.6 One model for all areas, and transfer

These are held-out mean log-likelihoods of the appearance features (30,000 fresh samples; higher is better), with the
resulting HMM water areas.

**Tonle Sap**
* Own model: held-out log-likelihood −2.30.
* Pooled model (fitted on all three areas): −2.93. Water is 2,065 → 2,010 km², so **the flood disappears**.

**Sevan**
* Own model: −2.67.
* Pooled model: −2.98. Largest body 821–1,004 km², which **loses a quarter of the lake**.
* Tonle Sap model: −5.68, a poor density fit. Yet the largest body is 1,235 km² and permanent IoU is 0.962. On the
  other hand it creates 61 km² of false flooded vegetation, and late false water is 32 km² against 23 km² with the
  own model.

**Ararat**
* Own model: −2.05.
* Pooled model: −2.58.
* Tonle Sap model: −5.89, with 4.3 km² of false water.

**Per-area fits are clearly better.** Pooling merges Sevan's dark-HV water with Tonle Sap's water into components
that fit neither. A model fitted on Tonle Sap still classifies Sevan's lake correctly, because calm water at the floor
looks the same everywhere, but its land and flooded-vegetation classes do not transfer. The **procedure** transfers;
the **fitted parameters** do not.

## 6. Run times (Apple M5, MPS; measured while other jobs shared the machine)

| Area | detect total | mixtures (CPU float64 EM, 3 restarts × 2 mixtures) | Baum–Welch | smoothing + Viterbi |
|---|---|---|---|---|
| Tonle Sap, 1.19 M px | 121 s | 82 s | 3 s | 11 s |
| Sevan | 26 s | 23 s | 1 s | 0.7 s |
| Ararat | 17 s | 14 s | 2 s | 1 s |

The full evaluation run with `--pooled` took 9.7 min: variants, BIC curves, seed refits, pooled and transfer fits,
and figures. Tests run in about 10 s.

## 7. Recommendation

1. **Sevan and the Armenian areas: use the HMM water mask.** It removes the wind dropouts (largest body 1,235 km² on
   every date), gives zero false water at Ararat, and has the lowest late-season false water at Sevan. Keep the
   terrain mask: it is still needed for the ~23 km² of slopes that look dark on every date.
2. **Tonle Sap open-water area: keep the current hysteresis mask for the headline numbers.** The mixture is more
   conservative than a detector that is already a lower bound. The HMM can be shown alongside it as a "high
   confidence" core.
3. **Show probabilities in the app**, using `p_water_date` (per-date mixture), which is calibrated. The HMM's `p_water`
   is sharper but over-confident in its rare uncertain cases.
4. **Flooded vegetation**: the HMM layer is the more robust choice, with ×1.6 threshold sensitivity against ×11 and
   no false alarms in Armenia. Present it as "possible flooded vegetation (double-bounce signature)" with its
   probability, not as an area claim. Its extent at Tonle Sap is unverified and 2–5 times the old one.
5. **Do not pool** mixtures across areas. Fit per area; it takes seconds.

Integration (for the lead):

```python
from scripts.detect import gmm, ops
from scripts.detect.terrain import cached_water_possible
wok = cached_water_possible(area, (H, W))
out = ops.run_pipeline(x, valid, "preview", water_ok=wok)
g = gmm.detect(out["L"], out["valid"], dates=dates, water_ok=wok)
# g["p_water_date"], g["p_water"], g["water"], g["flooded_veg"], g["p_fv"], g["viterbi"], g["first_water"], g["hmm"].A
```

The products are already written to `data/processed/<area>/gmm/products.pt` by the CLI.

## 8. Limits (blunt)

* **Gaussians on uncalibrated display brightness.** The logit is a pragmatic transform, not a physical model. Water
  is a point mass at the floor that the transform only turns into a very narrow Gaussian.
* **Components are not classes.** The state assignment is a rule on component means, with thresholds (0.08 / 0.25 /
  ln 2 / 0.25) we chose. That is fewer and more interpretable knobs than before, but they are still knobs. A component
  can mix several surfaces: Tonle Sap's component 5 mixes towns and flooded forest.
* **Unsupervised.** Nothing tells EM which structure matters. BIC cannot choose K. Appearance fits have local optima
  that depend on the seed (best of 3 is used). Per-area fits differ, and pooled fits are worse.
* **Naive-Bayes emissions.** Appearance and change are treated as independent given the state, and dates as
  independent given the state path. This produces over-confident HMM probabilities.
* **Ten dates, one homogeneous transition matrix.** A rising-then-falling season is modelled with the same 12-day
  $A$ throughout. The flood onset falls in the 24-day outage, which is handled exactly with $A^2$ but is still
  unobserved.
* **Under-detection of shallow or rough floodplain water** at Tonle Sap (3,326 vs ≥ 3,837 km² late season).
* **Flooded vegetation is unverified.** There is no reference layer, the area is 2–5 times the old rule, and only 70 %
  of it lies within 1 px of JRC water.
* **The change features depend on the first two dates being dry.** Pixels already flooded on 19 Jun / 1 Jul have
  the wrong reference.
* **No spatial model.** The Potts / mean-field step was not implemented.
* **JRC is 1984–2021 Landsat, not 2026.** It measures consistency, not truth, in a single season.
* **The fitted parameters will not transfer to calibrated γ⁰ (dB) data. The procedure will**: log-intensity
  features, EM mixtures, a component → state rule, and an HMM with a phantom slot for gaps. The rule thresholds would
  need re-deriving in dB.

## Figures (`docs/eda/gmm/`)

| File | Shows |
|---|---|
| `mixtures_tonle-sap.png`, `mixtures_sevan.png`, `mixtures_ararat.png` | pixel density with the fitted component ellipses (appearance and change) |
| `bic.png` | BIC against K |
| `p_water_tonle_sap.png` | HMM P(water) on 19 Jun and 17 Oct, and the per-date mixture on 17 Oct |
| `viterbi_tonle_sap.png` | Viterbi state maps for all ten dates |
| `first_water_tonle_sap.png` | HMM first-water date against the CUSUM change date, with the dry → FV → water pixels |
| `sevan_windy.png` | 31 Aug: current detector, per-date mixture and HMM |
| `areas.png` | areas against date for all methods, with the outage and Sevan's 1,242 km² |
| `reliability_transitions.png` | reliability curve and the learned transition matrices |
