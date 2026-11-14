# Statistical change detection with a stated false-alarm rate

Code: `scripts/detect/stats_change.py` · tests: `tests/test_stats_change.py` · figures: `docs/eda/stats/`
· outputs: `data/processed/<area>/stats/products.pt`. Run:

    .venv/bin/python -m scripts.detect.stats_change            # tonle-sap, sevan, ararat (~80 s)
    .venv/bin/python -m pytest tests/test_stats_change.py      # 10 tests, ~7 s

Everything numeric is PyTorch (float64 on the CPU for the statistics; `torch.special.gammainc/gammaincc`
for χ², our own vectorised continued fraction for the incomplete beta function, checked against scipy).
The water detector itself is **imported** from `ops.py` (`run_pipeline`, `water_masks`, `quegan_filter`);
nothing there was changed.

## 1. Noise model

**Calibrated data (γ⁰, linear power).** An $L$-look intensity is gamma distributed:
$I \sim \Gamma(\text{shape}=L,\ \text{scale}=\mu/L)$, so $E[I]=\mu$ and $\mathrm{Var}[I]=\mu^2/L$ (speckle is
multiplicative). $L$ is the *equivalent number of looks* (ENL).

**ENL estimator (quantile matching).** In each window with $N$ samples, $\widehat{CV^2}=s^2/m^2$ ($m$ = mean,
$s^2$ = unbiased variance). For pure speckle $\widehat{CV^2}$ scatters around $1/L$; texture and real change only
make it larger. We therefore take a **low quantile** $q=0.25$ of $\widehat{CV^2}$ over all windows and find the
$L$ for which simulated pure-gamma windows of the same size have the same $q$-quantile (bisection on $\log L$,
40,000 simulated windows). This avoids the small-sample bias of the classic $\overline{m^2/s^2}$ and is robust
to textured windows. Two versions:

* *spatial*: non-overlapping $7\times7$ windows per date ($N=49$) – the textbook ENL;
* *temporal*: the $T=10$ dates of one pixel ($N=10$) – the noise the change tests actually see.

Test (iv) recovers $L=4, 10, 30$ within 15 % with both versions.

**Previews (8-bit display brightness $b\in[0,1]$).** Figure `<area>_noise_vs_level.png` plots the temporal
std of each pixel (and the spatial $5\times5$ std) against its level, using low percentiles (least real change).
Finding: **the noise is not proportional to the level** (dotted line). At Sevan the temporal std is flat at
≈0.02–0.03 brightness from 0.2 to 0.9; at Tonle Sap it falls from ≈0.05 (0.4) to ≈0.03 (0.9) – the peak near
0.2 is the flood itself, i.e. real change. Additive noise in brightness is what a **logarithmic (dB-like)
display stretch** of multiplicative speckle produces; the fall-off near white suggests the stretch also
compresses the top. So we model $b \approx (\text{dB}-\text{dB}_{\min})/D$ and use the pseudo-intensity
$$I = 10^{D\,b/10},\qquad D = 30\ \text{dB (assumed)}.$$
$D$ is a nuisance: the fitted ENL scales like $1/D^2$ and the test statistic like $L D^2\sum(b_i-\bar b)^2$, so
the calibrated test does not depend on $D$ to first order. Water at the display floor ($b=1/255$) breaks
everything (clipped, zero variance); such pixels are excluded from all fits and their statistic is ≈0.
**The 8-bit stretch breaks the gamma model**, so the null distribution is calibrated empirically (section 3).

ENL found (both channels; "effective" = from the empirical null, section 3):

| area | spatial HH / HV | temporal HH / HV | effective HH / HV |
|---|---|---|---|
| Tonle Sap | 7.1 / 7.3 | 10.2 / 17.0 | 9.4 / 15.3 |
| Sevan | 3.0 / 3.7 | 16.8 / 17.5 | 15.6 / 17.9 |
| Ararat | 4.0 / 17.9 | 53.3 / 60.0 | 48.3 / 56.9 |

The spatial ENL is low because 180 m windows are textured (fields, relief), not because the speckle is
strong; the temporal and effective ENL agree within 10 %, and are what the tests use.

## 2. Change tests

Symbols: $k$ dates, intensities $I_1..I_k$ of one pixel and one channel, ENL $n$.

**Pairwise test (two dates).** $H_0: E[I_1]=E[I_2]$. Likelihood ratio
$Q = 4^{n}\,(I_1 I_2)^{n}/(I_1+I_2)^{2n}$. Under $H_0$ the ratio $r=I_1/I_2 \sim F(2n,2n)$ exactly, with
CDF $F(r)=I_{r/(1+r)}(n,n)$ (regularised incomplete beta). $Q$ is a symmetric function of $\ln r$, so the
LR test is the equal-tailed F test: $p = 2\min(F(r), 1-F(r))$. Test (v) matches `scipy.stats.f` to 1e-7.

**Omnibus test (Conradsen, Nielsen & Skriver 2016, IEEE TGRS 54(5), single-polarisation case $p=1$).**
$H_0$: all $k$ dates have the same mean.
$$\ln Q = n\Big[k\ln k + \sum_{i=1}^{k}\ln I_i - k\ln\sum_{i=1}^{k} I_i\Big] \le 0,$$
$$\rho = 1-\frac{1}{6(k-1)}\Big(\frac{k}{n}-\frac{1}{nk}\Big),\qquad \omega_2=-\tfrac14\big(1-\tfrac1\rho\big)^2,$$
$$P(-2\rho\ln Q\le z)\approx P(\chi^2_{f}\le z)+\omega_2\big[P(\chi^2_{f+4}\le z)-P(\chi^2_f\le z)\big],\quad f=k-1.$$

**HH and HV jointly** as independent channels: $Z=\sum_c -2\rho_c\ln Q_c$, $f=2(k-1)$, $\omega_2$ summed.
The full polarimetric (complex Wishart) version is **not** used: the previews and the planned γ⁰ products give
only the two diagonal terms (HH, HV intensities), no HH·HV* cross-product, so the covariance matrix is not
available. Independence of HH and HV is an assumption (they are correlated in practice, which fattens the
tail of $Z$ – one more reason for the empirical null).

**Sequential factorisation – when, and more than once.** $Q=\prod_{j=2}^{k}R_j$ with
$$\ln R_j = n\big[j\ln j-(j-1)\ln(j-1)+(j-1)\ln S_{j-1}+\ln I_j-j\ln S_j\big],\quad S_j=\sum_{i\le j}I_i,$$
$\rho_j = 1-\frac{1}{6n}\big(1+\frac{1}{j(j-1)}\big)$, $-2\rho_j\ln R_j\approx\chi^2_1$ per channel. $R_j$ tests
"date $j$ equals the pooled earlier dates, given those are equal". Procedure per pixel: segment start $s=0$;
for $j=s+1..k$: if the omnibus test over dates $s..k$ is significant and $R_j$ (within the segment) is
significant at level $\alpha$, record a change at $j$ and restart the segment at $s=j$. This yields the number
of changes, the first change, the date of the largest $R_j$, and its direction per channel
(sign of $\ln(I_j/\bar I_{s..j-1})$).

**Multiple testing.** Benjamini–Hochberg over all pixels of the footprint on the omnibus $p$-values:
sort $p_{(1)}\le\dots\le p_{(m)}$, reject all $p\le p^*=\max\{p_{(i)}: p_{(i)}\le iq/m\}$, $q=0.01$
(expected share of false discoveries among the flagged pixels ≤ 1 %). The dating step ($R_j$ and the gates of
later segments) is run only inside BH-significant pixels at $\alpha=q=0.01$ per test (conditional on the pixel
being flagged; not separately FDR-controlled).

## 3. Empirical null – what it fixes

Figure `<area>_null_fit.png`: quantiles of $Z$ on stable proxy pixels against $\chi^2_{18}$.

1. **Speckle null (effective ENL).** Fit $n$ per channel so that the *central body* (quantiles 10–50 %) of
   $-2\rho\ln Q$ over all non-floor pixels matches $\chi^2_{k-1}$ (Efron 2004, "empirical null": fit the null to
   the centre of the data, since change only makes the statistic larger; needs ≥ 50 % unchanged pixels).
   This fixes the unknown ENL / display stretch. It does **not** fix the shape: the upper tail is far heavier
   than χ² even on stable land (Tonle Sap bright-HV land: 99th percentile 75 vs 34.8 for χ²; Sevan 76; Ararat
   299). Real but uninteresting variation (phenology, soil moisture, HH–HV correlation, the stretch) is not
   speckle. Under this null 37 % (Tonle Sap), 16 % (Sevan) and 38 % (Ararat) of the land is "changed" and the
   held-out stable land is flagged at 4.6 %, 5.9 % and 40 % – **far above the nominal rate**.
2. **Stable-land null (main map).** Reference = bright-HV land (top 15 % of mean HV) more than 10 px from any
   water ever mapped – chosen by *level and location*, not by the statistic – split in a 32-px checkerboard:
   one half gives the null sample, the other half is held out to measure the false-alarm rate. $p$-values:
   $p=(1+\#\{Z_{\text{ref}}\ge z\})/(N+1)$, and above the 95th percentile $u$ an exponential tail
   (peaks-over-threshold) $p=0.05\,e^{-(z-u)/\beta}$, $\beta$ = mean excess, so that BH can go below $1/N$.
   $H_0$ now reads "**no more change than typical stable land shows**". Permuting dates cannot make a null
   for $Q$ (it is permutation invariant), so it was not used.

The textbook null (spatial ENL with the χ² formula) is wrong in both directions: too conservative where
texture makes the spatial ENL small (Sevan: 0.01 % of stable pixels at $p<0.01$), too liberal where it is not
(Ararat: 14 %).

## 4. Results (q = 0.01, previews, common footprint)

| area | footprint km² | changed, stable-land null (main) | changed, speckle null | BH $p^*$ (main) | CUSUM "changed" |
|---|---|---|---|---|---|
| Tonle Sap | 36,036 | **5,619 km² (15.6 %)** | 13,246 km² (36.8 %) | 1.6e-3 | 8,950 km² |
| Sevan | 4,499 | **4 km² (0.1 %)** | 722 km² (16.1 %) | 8.2e-6 | 39 km² |
| Ararat | 3,769 | **0 km²** | 1,443 km² (38.3 %) | 5.9e-7 | 17 km² |

Synthetic γ⁰ check (`demo`, 1 dB log-normal noise ≈ ENL 19, a disc growing from 20 to 120 px radius):
spatial / temporal / effective ENL 18.6 / 16.9 / 16.9; changed 442 km² against a true changed area of
π(120²−20²)·0.01 = 440 km²; held-out background flagged 0.21 %.

Number of changes per pixel, Tonle Sap (main map): 0: 30,416 · 1: 1,383 · 2: 2,200 · 3: 1,394 · 4: 474 ·
5+: 135 km². **Most changed pixels change more than once** (e.g. flooded → deeper → back, or crop cycles),
which a single CUSUM change point cannot express.

**False-alarm rate on stable targets** (share of pixels flagged; held-out half of the bright-HV land; lake
core = water on all dates, eroded 5 px):

| target | stats, stable-land null | stats, speckle null | textbook χ² (spatial ENL) at the same $p^*$ | HH only | CUSUM |
|---|---|---|---|---|---|
| Tonle Sap stable land (1,780 km²) | **0.02 %** | 4.6 % | 1.3 % | 2.0 % | 5.8 % |
| Tonle Sap lake core (1,342 km²) | 0 % | 0 % | 0 % | 0 % | 0 % |
| Sevan stable land (134 km²) | **0 %** | 5.9 % | 0 % | 0.6 % | 0.08 % |
| Sevan lake core (709 km²) | 0 % | 0.07 % | 0 % | 0 % | 0.41 % |
| Ararat stable land (252 km²) | **0 %** | 40 % | 13 % | 6.6 % | 0.03 % |

**Agreement with CUSUM at Tonle Sap.** Changed/unchanged agree on 84.8 % of the footprint: both 4,542 km²,
stats-only 1,077, CUSUM-only 4,408 km². Where both flag a change, the *first* statistical change equals the
CUSUM date on 37 % of pixels (65 % within ±1 pass), and the date of the *largest* statistical change equals the
CUSUM date on 71 % – the CUSUM finds the biggest step, the sequential test finds every significant step,
the first of which is often a small early-season one (July). Figure `tonle-sap_first_change_vs_cusum.png`:
CUSUM-only pixels are mostly the outer north-west and north-east plain with modest, gradual darkening
(23 Sep–5 Oct in CUSUM colours); there the change is not larger than what the stable-land reference shows,
so the stats test declines to call it. CUSUM's own false-alarm rate on stable land (5.8 %) says many of
those are not trustworthy either; neither map is ground truth there.

**The known flood wave (Tonle Sap seasonal-water pixels, reference timing = first date mapped as water):**

| region | pixels | detected (stats / CUSUM) | a detected change within ±1 pass of the first-water date (stats / CUSUM) |
|---|---|---|---|
| south-east plain (lon > 104.5, lat < 12.5) | 17,970 | 71 % / 71 % | 53 % / 57 % |
| lake ring (rest) | 36,481 | 76 % / 75 % | 64 % / 59 % |

First-water dates peak on 18 Aug (south-east) and 18 Aug–23 Sep (ring); CUSUM dates peak on 18 Aug; the
statistical *first* change peaks on 18 Aug but with a long early tail (Jul), because pre-flood changes in the
same pixels are also significant. Use `largest_change` (not `first_change`) to date the flood arrival.

**Open water per pass with uncertainty** (km²; median and 5–95 % of the combined Monte Carlo; "now" = the
current single-threshold pipeline; widths are the 5–95 % width of each component alone):

| date | Tonle Sap now | Tonle Sap median (5–95 %) | thr / boundary / speckle | Sevan now | Sevan median (5–95 %) | thr / boundary / speckle |
|---|---|---|---|---|---|---|
| Jun 19/20 | 2,234 | 2,210 (2,069–2,366) | 195 / 167 / 6 | 1,245 | 1,225 (1,125–1,297) | 138 / 69 / 3 |
| Jul 1/2 | 2,297 | 2,261 (2,079–2,469) | 263 / 207 / 9 | 1,235 | 1,207 (1,073–1,309) | 196 / 83 / 4 |
| Jul 13/14 | 2,310 | 2,277 (2,100–2,481) | 258 / 204 / 8 | 1,235 | 1,211 (1,112–1,297) | 146 / 76 / 3 |
| Jul 25/26 | 2,421 | 2,373 (2,152–2,618) | 334 / 227 / 10 | 1,284 | 1,262 (1,159–1,387) | 175 / 102 / 5 |
| Aug 18/19 | 2,803 | 2,631 (1,959–3,278) | 1,041 / 487 / 11 | 1,261 | 1,231 (1,108–1,339) | 185 / 93 / 4 |
| Aug 30/31 | 2,954 | 2,818 (2,258–3,369) | 825 / 501 / 10 | 1,236 | 1,180 (800–1,345) | 479 / 107 / 6 |
| Sep 11/12 | 3,147 | 2,957 (2,170–3,706) | 1,179 / 634 / 14 | 1,197 | 1,124 (901–1,337) | 361 / 143 / 6 |
| Sep 23/24 | 3,522 | 3,234 (2,249–4,369) | 1,692 / 779 / 13 | 1,279 | 1,256 (1,142–1,376) | 182 / 105 / 5 |
| Oct 5/6 | 3,541 | 3,264 (2,140–4,422) | 1,867 / 774 / 16 | 1,329 | 1,301 (1,174–1,454) | 214 / 124 / 4 |
| Oct 17/18 | 3,837 | 3,455 (1,967–4,769) | 2,264 / 956 / 15 | 1,304 | 1,273 (1,139–1,419) | 220 / 117 / 6 |

Seasonal open water: Tonle Sap 1,778 now → 1,683 (837–2,769) km²; Sevan 125 → 168 (75–282) km².

**The threshold dominates every date**, then the boundary; speckle is negligible (≤ 16 km²) because the 180 m
previews are already heavily averaged and the Quegan filter averages 10 dates more. The honest headline is
therefore "Tonle Sap open water on 17 Oct ≈ 3,500 km² (5–95 %: 2,000–4,800)", not "3,837 km²". The band is
wide because the threshold interval is wide: every weak threshold from 0.045 to 0.160 keeps Sevan's calm-date
area within ±1 shoreline pixel (±107 km²) of 1,242 km² – Sevan has a steep, clean shore and cannot pin the
threshold down; Tonle Sap's floodplain has a lot of land at intermediate brightness, so the same interval
moves its area by up to ±1,100 km². The independent estimate of about twice the area (eda-findings, Otsu
split ≈ 0.24) lies outside this interval, i.e. it is a different *definition* (it counts "dark land"), not a
noise effect. The Tonle Sap median is below the current value because 0.12 sits in the upper half of the
interval.

### Uncertainty formulas

* (a) threshold: weak HH threshold $t\sim U[t_{lo},t_{hi}]$ (9 values; strong threshold kept at $t/2$ as in
  KNOBS), $[t_{lo},t_{hi}]$ = all $t$ with $|\bar A_{\text{Sevan, calm}}(t)-1242|\le P_{\text{Sevan}}\cdot s$
  ($P$ perimeter, $s$ pixel side, windy dates 31 Aug / 12 Sep excluded). Same interval used for every area.
  For γ⁰ (no Sevan calibration yet): KNOB ± 2 dB, untested.
* (b) boundary: $\pm P\,s/2$ ($P$ = water/non-water edge length per date), uniform and fully correlated along
  the shore (conservative).
* (c) speckle: parametric bootstrap, 12 replicates: signal $\hat x$ = Quegan-filtered input; previews
  $x^*=\mathrm{round}_{8\text{-bit}}(\hat x + \sigma_b\varepsilon)$ with $\sigma_b = 10\sqrt{\psi_1(n_{\text{eff}})}/(D\ln 10)$
  (the brightness sd implied by the calibrated ENL; $\psi_1$ = trigamma); γ⁰: $x^*=\hat x\cdot G/n$,
  $G\sim\Gamma(n,1)$; then filter → normalise → `ops.water_masks`.
* Combination: $A = A_{\text{thr}}[i] + (A_{\text{boot}}[j]-\overline{A_{\text{boot}}}) + U\cdot P s/2$,
  $U\sim U(-1,1)$, 4,000 draws, clamped at 0; report median and 5–95 %.

## 5. Per-pixel water confidence

$$P(\text{water}) = \Phi\!\left(\frac{t - L_{HH}}{\sqrt{\sigma_L^2+\sigma_t^2}}\right)\in[0,1]$$
$L_{HH}$ = filtered, normalised HH of that date; $t$ = weak threshold (0.12); $\sigma_L$ = per-pixel sd of
$L_{HH}$ across the bootstrap replicates; $\sigma_t=(t_{hi}-t_{lo})/\sqrt{12}$ (threshold uncertainty);
$\Phi$ = standard normal CDF. 0.5 = on the threshold; use $|2P-1|$ as opacity. It ignores the hysteresis
connectivity and the HV test of the detector. Figure `sevan_water_confidence.png` (12 Sep, windy): the open
lake is ≈1, the wind-roughened north-west bay drops to 0.2–0.6, i.e. exactly where the mask is unreliable.

## 6. Recommendation for the app

* **Change map:** show the statistical map (`changed_fdr`, coloured by `largest_change`, optionally
  `n_changes`) instead of the CUSUM map. It has a stated meaning ("≤ 1 % of flagged pixels expected to be
  false relative to stable land"), a measured false-alarm rate of ≈0 % on held-out stable land at all three
  sites (CUSUM: 5.8 % at Tonle Sap), finds multiple changes, and detects the flood wave as often as CUSUM.
  Caption it "significant radar change (FDR 1 %)", and date it with `largest_change`, not `first_change`.
* **Numbers:** quote every water area with its band, e.g. "≈3,500 km² (2,000–4,800)". Do not quote 3,837 km².
* **Confidence layer:** `water_conf` as opacity.

## 7. Limits (blunt)

* Previews are 8-bit display images with an unknown stretch. The gamma model does not hold; the log-stretch
  reading of the noise curve is an inference. All $p$-values rest on the empirical null.
* The stable-land null depends on the reference pixels being stable. At Ararat the bright-HV reference
  includes the southern band where HV varies for instrument reasons (EDA), so its null is very broad and
  **nothing** at Ararat is significant – real changes (irrigation) may be missed. At Sevan the reference is
  small (≈134 km² held out). The map's meaning is "change larger than typical land", and the speckle-null map
  (`changed_fdr_speckle`) is the opposite, liberal bracket.
* HH and HV are treated as independent; they are not.
* FDR is controlled for "pixel changed at all". The dates (`first_change`, `largest_change`, `n_changes`)
  come from per-date tests at α = 0.01 inside flagged pixels and are not FDR-controlled. A change that builds
  up over several passes is dated late (the single-date jump is not significant).
* Power: with ENL 16 a +4.8 dB step is found in 100 % of pixels (right date 97 %), but with ENL 8 a +3 dB step
  is found in only 31 % (tests). Pixel-level tests at 180 m are weak for subtle changes.
* Adjacent pixels are not independent (bilinear resampling, Quegan filter for water); BH is valid under
  positive dependence, but the effective number of tests is smaller than the pixel count.
* The water threshold interval comes from one lake (Sevan) and is transferred to Tonle Sap; its width, not
  noise, dominates every area band. The boundary term treats the whole shore as one correlated error.
  Speckle bootstrap uses white noise; real preview noise is spatially correlated, so the speckle component is
  if anything underestimated (it is ≤ 16 km² anyway).
* The August gap (27 Jul–10 Aug): a change there is dated 18 Aug. Changes on the last pass have one sample.
* γ⁰ path: implemented and exercised by the unit tests' gamma model, but the ±2 dB threshold range and the
  stable-land reference were never run on real calibrated data.

## Integration (for the lead)

```python
from scripts.detect import stats_change as sc
p = sc.analyse(area, q=0.01, n_boot=12)       # dict; CPU tensors + metadata (keys below)
sc.save_products(p); sc.figures(p)
# lower-level:
sc.pseudo_intensity(x, source)                # [T,2,H,W] -> intensity for the tests
sc.calibrate_neff(I, mask)                    # -> ([n_HH, n_HV], diag)
sc.omnibus_pvalue(I, n)                       # -> (p [H,W], Z [H,W])
sc.empirical_null_p(Z, Z[ref_mask])           # -> p [H,W]
sc.bh(p, mask, q)                             # -> (reject [H,W] bool, p*)
sc.sequential_changes(I, n, gate, alpha)      # -> change [T,H,W], n_changes, first, largest, direction
sc.pair_pvalue_exact(I1, I2, n)               # exact two-date test
sc.water_uncertainty(x, out, source, km2, n, t_lo, t_hi)   # out = ops.run_pipeline(...)
sc.water_confidence(L_hh, sd_L, t, sd_t)
```

`products.pt` keys: `dates, p_omnibus, changed_fdr, n_changes, first_change, largest_change, direction,
direction_ch, change_events [T,H,W], p_pair_first_last, p_pair_first_last_ch, water_conf [T,H,W] (float16),
water_km2_median/lo/hi/baseline [T], seasonal_km2, enl, fdr_q, bh_p_threshold, null_kind,
changed_fdr_speckle, p_omnibus_speckle, p_omnibus_theory, null_fit, false_alarm, cusum_compare, flood_wave,
noise_curve, sevan_interval`. Index convention: −1 = no change; dates index into `dates`.
