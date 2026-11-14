# Detection architecture

Updated 14 November 2026, about 18:50 (Armenia time); first drafted the same day at 11:50, before most of the modules had results, and revised at 14:00 and in the evening (fusion v2 as the shipped water detector, 15 places, the calibrated-data path). This is the design that ties the separate detection modules into one system, and what happened when each module was built and measured. A part entered the fused system only if it passed an admission rule (section 5). Parts marked *evaluated only* were built and measured but are not in the app.

Formulas and every number in detail: [DETECTION.md](DETECTION.md). One-page summary: [RESULTS.md](RESULTS.md).

## 1. The idea in four sentences

Each radar pass is weak evidence about the state of each pixel: open water, vegetation standing in water, or dry land. Several independent "experts" read that evidence in different ways — physics rules, fitted probability distributions, statistical tests, the pixel graph, learned features — and each reports a probability, not a verdict. Three kinds of prior knowledge constrain the answer: terrain (water lies flat and low), time (states persist and follow a physical order), and space (neighbours agree). The system combines evidence and priors into one probability per pixel per date, and the products shown to the user — maps, dates of change, areas with error bars, the flood's arrival time — are read from that result or, where the result is known to be biased, from the expert that is not.

The last clause is the main lesson of the day (section 5): the first fused system (v1) was the best answer for lakes and mountains but shrank a rising floodplain. Fusion v2 changed the time model, added a floodplain zone found without any reference and added a self-calibrating detector as an expert. It is now the water source for all 15 places, and the choice per kind of terrain happens inside it.

## 2. Diagram

```mermaid
flowchart TD
    A["NISAR GCOV previews<br/>HH and HV, 10 dates, 180 m"] --> QC["Quality control<br/>score every pass and channel,<br/>neutralise a failed channel"]
    QC --> B["Conditioning<br/>align dates, mask edges,<br/>speckle filter, date normalisation,<br/>noise model"]

    T["Elevation model"] --> P1["Terrain prior<br/>flat and low enough for water"]

    B --> E1["Physics rules<br/>two-threshold water,<br/>two-state flooded vegetation"]
    P1 --> E1
    B --> E7["Seeded detector<br/>measures each date against<br/>the scene's own dark pixels"]
    B --> E2["Mixture model<br/>Gaussian mixtures and hidden Markov model"]
    B --> E3["Statistical change test<br/>omnibus test, false-discovery rate 1 %"]
    B --> E4["Pixel graph<br/>random walker, structure consistency"]
    B --> E5["Learned features<br/>U-Net v2, deep autoencoder"]
    E1 --> E6["Sub-pixel water fraction<br/>shoreline pixels"]

    E1 --> F["Fusion v2<br/>weighted product of probabilities"]
    E7 --> F
    E5 --> F
    E6 --> F
    P1 --> F
    F --> H["Time model<br/>Markov chain whose jump probability<br/>rises where the radar darkens"]
    FP["Floodplain zone<br/>flat, low, changing<br/>(no reference data)"] --> H
    H --> S["Space model<br/>3 x 3 smoothing"]
    S --> R["Water probability<br/>per pixel, per date"]
    R --> O1["Water map and area range<br/>all 15 places (shipped)"]

    E3 --> O2["Significant change map<br/>several changes per pixel"]
    E1 --> O4["Flood front<br/>arrival day, speed,<br/>through the outage"]
    O4 --> O5["Floodplain height<br/>from the order of flooding"]
    B --> O6["Choreography map<br/>moment, size, tempo"]

    HY["Independent flood estimate<br/>lake level + Landsat record<br/>(no radar)"] -.-> R
    V["Independent checks<br/>Landsat water record, lake area,<br/>altimeter, gauges, weather,<br/>elevation model, left-out dates"] -.-> SB["Scoreboard and admission rule"]
    E2 -.-> SB
    E4 -.-> SB
    R -.-> SB
    SB -.-> F

    RS["Small remote server<br/>reads windows of remote<br/>calibrated files"] -.-> CD["Calibrated data, 80 m and 30 m<br/>(evaluated only)"]
    CD -.-> DB["dB water rule<br/>Tonle Sap analysed"]
    DB -.-> SB
```

Solid arrows carry data; dotted arrows carry measurements, references and the calibrated-data path. Shipped: quality control, conditioning, terrain prior, physics rules (flooded vegetation, change point), the seeded detector, the U-Net, the deep autoencoder and the sub-pixel fraction as fusion v2 experts, the statistical change test (its own layer), the flood front and choreography layers. Evaluated only: the mixture model, the graph (offered to the fusion layer, not admitted), the learned despeckling filter, the physics autoencoder, the network trained on independent labels, interferometry (not drawn) and the calibrated-data detector. The calibrated path is: a small remote server opens the large remote files over the network, reads only each area's window and sends back a small result; the laptop writes it as contract-A GeoTIFFs ([`calibrated-data.md`](research/calibrated-data.md)). It is not connected to the app.

## 3. Layers

### Layer 0 — Quality control and conditioning (built, in the app)

**Quality control** ([`qc.py`](../scripts/detect/qc.py), [`qc.md`](eda/qc.md)) runs first: it scores every pass of every area against the other dates on stable land (gain, contrast, channel balance, across-track slope, leave-one-date-out departure and its spatial pattern, residual misregistration, metadata) and writes `qc.json`. A channel that fails is replaced by the per-pixel median of the other dates before detection, so it can create neither a fake step nor a fake gain. Result: 134 passes in 15 areas; 3 pass-channels fail, including Armenia's 6 October HV (Sevan: departure 4.6× usual, channel balance $z = -12$); no false alarm on the Tonle Sap flood dates. Neutralising that HV pass halves the HV noise of stable land at Ararat and removes 14–17 % of the spurious change points in Armenia. [`run.py`](../scripts/detect/run.py) applies it.

**Conditioning** aligns all dates to under 0.1 pixel, keeps only pixels seen on every date, removes the unreliable swath edge, filters speckle using all dates together, and removes per-date brightness drift using stable pixels ([DETECTION.md, section 2](DETECTION.md)). The **noise model** was built in the statistical study ([`stats-change.md`](eda/stats-change.md)): preview noise behaves like a logarithmic display stretch of gamma-distributed speckle; temporal equivalent number of looks 10–17 at Tonle Sap and Sevan.

Output: $L_{t,c}(i)$, the filtered log-like intensity of pixel $i$ on date $t$ in channel $c \in \{\text{HH}, \text{HV}\}$, and a validity mask.

### Layer 1 — Evidence experts (results)

Each expert $k$ maps the conditioned data to a probability over states $s \in \{\text{water}, \text{flooded vegetation}, \text{land}\}$ for every pixel and date, $p_k(s \mid L_t(i))$, or to evidence that a change happened between dates.

| Expert | Reads | Result | Fate |
|---|---|---|---|
| Physics rules | absolute darkness in HH and HV; for flooded vegetation, a rise of HH − HV above noise, or both channels falling next to open water | balanced accuracy 99.57–99.58 % on held-out anchors; flooded-vegetation rule rebuilt: threshold sensitivity 7.8× → 1.53×, Armenia false alarms 39 → ≤ 1.4 km² | **the floodplain water layer**; a fusion expert |
| Mixture model | shape of the HH–HV distribution, Gaussian mixtures fitted by EM, read as a sequence by a hidden Markov model | Sevan 1,235.3 km² on every date; Ararat 0 km² false water; Tonle Sap flood shrunk to 3,326 km² | per-date mixture **rejected** by the admission rule (Sevan accuracy −0.66 pp); the HMM water mask is the fallback for lakes |
| Statistical change test | ratio of intensities between dates, empirical null from stable land | false-alarm rate on held-out stable land 0.02 % / 0 % / 0 % against 5.8 % / 0.08 % / 0.03 % for the change point; several changes per pixel | **its own app layer** (`sig`); as a water expert **rejected** |
| Pixel graph | which pixels resemble each other | random walker: 0 km² of false water in Armenia with no elevation data, Sevan calm dates within 0.1 % of 1,242 km²; but the Tonle Sap flood grows only 1.2×; structure-consistency change exactly gain-invariant; spectral clustering follows geography, not water | random walker **rejected** (Tonle Sap accuracy −0.28 pp once time and terrain were in); spectral methods dropped |
| Learned features | spatial context | U-Net v2 follows the physics rules within ±4 %; EfficientNet-B4 autoencoder did not beat a mixture on the raw two channels (water IoU 0.21 against 0.65); the only open L-band model (ALOS-2) reached 0.91 balanced accuracy at a never-seen Lake Sevan against 0.99 for the physics rules | U-Net and deep autoencoder **admitted** as fusion experts; pretrained models documented only |
| Seeded (self-calibrating) detector | each date relative to the scene's own permanent-water seeds, no absolute brightness ([`crossscene.md`](eda/crossscene.md)) | Pantanal 0.500 → 0.994 and Lake Chad 0.503 → 0.976 balanced accuracy; 10 of 15 places work (fixed thresholds: 9); fails on the Nile delta, thin rivers (Mekong) and, held out, Rondônia | **admitted** as a fusion v2 expert (weight 0.37) and as the source of its per-scene switch |
| Sub-pixel water fraction | how dark a shoreline pixel is between pure water and nearby land | Sevan calm-date area 1,242.5 km² against the published 1,242 km² (hard mask −16 km²); shoreline placed to 52–63 m median from 180 m pixels | **admitted** (Sevan only) |

### Layer 2 — Priors (results)

| Prior | Statement | Source | Result |
|---|---|---|---|
| Terrain | open water needs flat, low ground and cannot sit in radar shadow | Copernicus DEM GLO-90, never the validation data | removes 70 % of the false water at Sevan and 90 % in the Ararat Valley, 0.04 % of the real lake lost; nothing removed at Tonle Sap; radar shadow removed none of it, slope and height above the local low did. **Admitted** (hard mask) |
| Permanence | pixels dark on almost every date are permanent water | the stack itself | built; used by the old flooded-vegetation rule and the sub-pixel fraction |
| Time | water tends to stay water | transition matrix learned by Baum–Welch on tune-strip pixels | **admitted**; fixes the Sevan rough-surface dip (66 → 0.8 km²); but learned on pixels that almost never change, so it resists late flooding (land → water 0.0013 per 12 days at Tonle Sap) |
| Space | neighbours share a state | a masked 3 × 3 mean of the posterior (the graph-weighted smoothing was not built) | **admitted** (small gains) |

### Layer 3 — Fusion (v2 shipped; v1 described for its failure)

Per pixel and date, experts and the terrain prior are combined by a weighted product (log-linear pooling), written in log-odds:

$$
\operatorname{logit} q_t(i) \;=\; b + \sum_{k} w_k\, f_k(i,t) \;-\; c\,\big[\pi_{\text{terrain}}(i) = 0\big]
$$

with $f_k$ the expert's log-odds (a probabilistic expert's own probability; for a hard mask, two values estimated from its true and false positive rates), $\pi_{\text{terrain}}$ the terrain mask, and weights $w_k \ge 0$ fitted by class-balanced, penalised logistic regression on tune-strip pixels whose state the 38-year Landsat record makes certain, never on the TEST strip. A weight of zero removes an expert. The hard terrain variant sets $q_t = 10^{-3}$ where water is impossible.

The fused evidence is the emission of a two-state hidden Markov chain over the ten dates; with transition matrix $A$ and the forward–backward recursions,

$$
\gamma_t(i) \;=\; P\big(S_t = \text{water} \;\big|\; q_1(i),\dots,q_T(i)\big),
$$

where the double-length step across the 27 July – 10 August outage uses $A^2$. Finally $\gamma$ is smoothed by a 3 × 3 mean inside the footprint.

**Fusion v2** ([`fusion2.md`](eda/fusion2.md); formulas in [DETECTION.md, section 3.20](DETECTION.md)) keeps this pooling and changes three things. (1) The Markov chain's transition matrix depends on an observed input: the chance of land turning into water is 0.33 % per 12 days normally but 65 % in an interval where the radar brightness falls into the water range inside the floodplain zone. (2) The floodplain zone is found without any reference (slope under 1°, height above the local low under 5 m, terrain allows water, brightness varying over the season: 33 % of the Tonle Sap footprint, 0 at Sevan and Ararat); inside it the strict threshold is relaxed from 0.12 to 0.19, a value fitted on the Tonle Sap tune strip to the independent flood estimate. (3) The seeded detector joins the experts (weight 0.37), and its mode switches the floodplain zone off for lake scenes. Three states were tried and rejected (the third, water under vegetation, is kept as an unvalidated separate layer). Output: $\gamma_t(i)$ per pixel and date in `data/processed/<area>/fusion2/products.pt`; the app range is the area at $\gamma > 0.75$ to the area at $\gamma > 0.25$.

Planned in the first draft of v1 but **not built**: three states (flooded vegetation is not fused), change evidence from the statistical test and the graph raising the transition probability between two dates, and graph-weighted spatial smoothing.

Output: $\gamma_t(i)$, a water probability per pixel and date, in `data/processed/<area>/fusion/products.pt`.

### Layer 4 — Products (results)

| Product | Definition | Read from | Result |
|---|---|---|---|
| Water map per date, all 15 places | $\gamma_t > 0.5$ of fusion v2, with the band $\gamma > 0.75$ to $\gamma > 0.25$ | fusion v2 (`"water_source": "fusion2"` in every `areas/*/area.json`, applied in `run.py`) | Sevan largest body 1,233 km² on every date, far false water 6.0 km² (Ararat 0.31); Tonle Sap 2,281 → 5,046 km² (17 Oct range 4,196–6,051) against an independent 5,421 (4,198–7,222), inside it on 7 of 10 passes; only 76 % of the water lies inside the independent map |
| Water map, strict detector (reference only) | physics rules with terrain mask | physics expert | Tonle Sap 2,234 → 3,837 km², a lower bound |
| Area with uncertainty | Monte Carlo over threshold, shoreline and speckle | statistical study | For the strict detector: Tonle Sap 17 October about 3,500 km² (5–95 %: 2,000–4,800); the tuning study: 3,800–5,900 km² plausible. The app range comes from fusion v2 instead (4,196–6,051 km²) |
| Change dates | the change point (one per pixel) and the significant-change map (any number per pixel, FDR 1 %) | physics rules; statistical test | flood wave dated: south-east plain 18 August, outer ring 23 September |
| Flood front | day each pixel went under water, interpolated between consecutive shorelines; front speed; the outage band with its ±12-day bound; skill measured by leaving one real pass out | physics water masks | 309 km² flooded across the outage at 12.9 km²/day, between 9.3 before and 13.6 after: no hidden surge; area error 82 km² against 161–178 km² for naive baselines |
| Floodplain height from flood order | earlier flooded means lower | flood front | against the elevation model ρ = 0.45 (0.25 without its flattened water); against 38-year Landsat water frequency ρ = −0.47 |
| Choreography map | per pixel, a fitted logistic step: moment, size and tempo, painted as one colour image | conditioned data | step moment agrees with the change point (ρ = 0.95); a REACTIV-style composite with a significance test |

### Layer 5 — Verification (results)

| Check | Independent of the detector? | Result |
|---|---|---|
| Synthetic scenes with known answers | yes | 22 test files, 129 test functions under [`tests/`](../tests/) ([DETECTION.md, section 7.1](DETECTION.md)) |
| Lake Sevan published area, 1,242 km² | partly: the water threshold was first tuned on it | sub-pixel 1,242.5 km²; HMM 1,235.3 km² every date |
| 38 years of Landsat water maps | yes | permanent-water IoU 0.94 (Sevan), 0.84 (Tonle Sap); the source of every anchor below |
| Tune on one strip, test on an untouched strip and another country | yes, but the TEST strip was looked at three times by the tuning | tuned thresholds rejected by the gate; old thresholds kept; transfer works from Sevan to Tonle Sap, not the other way |
| Flood extent expected from lake level and the Landsat record, no radar | yes, but a model; one fusion v2 threshold was fitted to it | Tonle Sap 17 Oct 5,421 km² (4,198–7,222); fusion v2 5,046 inside it ([`hydro.md`](eda/hydro.md), [`fusion2.md`](eda/fusion2.md)) |
| Independent 2026 data (altimeter, gauges, weather) | yes | area follows lake level, ρ = 0.98; two of our claims corrected ([`validation-2026.md`](eda/validation-2026.md)) |
| Elevation model against flood-order height | yes | ρ = 0.45, conservative 0.25 |
| Leave one radar pass out, predict its shoreline | yes | front best on area error; transformer about equal to a straight line in time |

## 4. Why a system and not a list of detectors

- **One answer per place.** Ten detectors give ten maps that disagree. The scoreboard puts them on one table, the fusion gives one probability for lakes and mountains, and the disagreement becomes the uncertainty.
- **Each weakness was covered by a different part.** Rough lake surfaces were a time problem (the Markov chain fixed the Sevan dip); mountain false water a terrain problem (the terrain mask) and a space problem (the graph); fragile flooded vegetation a definition problem (the rebuilt rule); hand-picked thresholds a tuning problem (the gate kept them, and showed where they are too strict).
- **Calibration-proof procedure.** Thresholds fitted on uncalibrated previews will not survive the move to calibrated data. The tuning procedure, the fitted mixtures, the transition matrices and the fusion weights are re-estimated from new data by the same code.
- **Every claim had a check that could have failed.** Two did fail (section 5 and [RESULTS.md](RESULTS.md)).

## 5. Admission rules, and the gap v1 had

For fusion v1, a module was added to the fused system only if, on the untouched TEST strip and on Lake Sevan (declared in code as `fusion.RULE` before any TEST number was read):

1. it did not lower balanced accuracy on always-water and never-water pixels by more than 0.1 percentage point in any area, and
2. it improved at least one named weakness by a measurable amount — mountain false water by ≥ 10 % and ≥ 1 km², the Sevan rough-surface dip by ≥ 10 km², or date-to-date flips on stable pixels by ≥ 10 % and ≥ 0.005 — and
3. it ran on a laptop in under 10 minutes per area.

Flooded-vegetation stability, listed in the first draft, could not be measured, because the fused model has two states.

**What the rule decided** (greedy, priors first; numbers from [`fusion.md`](eda/fusion.md), section 4):

| Addition | Admitted? | Key number |
|---|---|---|
| terrain, hard mask | **yes** | mountain false water 69.7 → 22.9 km² |
| terrain, soft (fitted penalty) | no | changed nothing at the 0.5 threshold |
| time, two-state Markov chain | **yes** | Sevan dip 65.7 → 0.8 km²; flips 0.0102 → 0.0011 |
| space, 3 × 3 smoothing | **yes** | mountain false water 12.6 → 10.6 km² |
| math HH score | no | Sevan accuracy −0.05 pp |
| statistical water confidence | no | Sevan accuracy −0.04 pp |
| GMM per date | no | Sevan accuracy −0.66 pp |
| graph random walker | no | Tonle Sap accuracy −0.28 pp (although mountain false water → 0) |
| U-Net v2 | **yes** | mountain false water 10.6 → 7.4 km² |
| flood-order monotone mask | no | Sevan accuracy −0.05 pp |
| deep autoencoder + GMM | **yes**, barely | mountain false water −1.7 km² |
| sub-pixel fraction (Sevan only) | **yes**, barely | mountain false water −1.7 km² |
| supervised network (JRC labels at other places) | no | −0.98 km², below the 1 km² bar |

Fused result against the physics rules with terrain mask: balanced accuracy at Tonle Sap 99.57 % → 99.98 % (intervals do not overlap); Sevan rough-surface dip 66 → 1 km²; far false water 39.1 → 9.3 km² (Sevan) and 2.6 → 0.4 km² (Ararat); flicker on stable pixels down 40-fold.

**The gap.** The rule measured lakes and mountains, not the floodplain, because no 2026 truth exists there. So the fused map passed every test while it lost about **700 km²** of the 17 October Tonle Sap flood (3,120 km² against 3,837 km², which the tuning study already calls a lower bound), and its rank agreement with the 38-year record in the floodplain fell from 0.50 to 0.33. The guard columns that showed this were watched but were not part of the rule. The cause is the time prior: learned on tune-strip pixels that almost never change, it pulls a late-flooding pixel with one or two dates of evidence back to land. The GMM + HMM and the random walker show the same loss, so it is a property of persistence models, not a correction.

**The choice made at 14:00**: fused map for lakes and mountains, physics rules with the terrain mask for floodplains.

**What fusion v2 did about it.** `RULE2` was declared before the TEST strip was read again. A change is admitted only if lakes and mountains keep v1's gains (balanced accuracy down by at most 0.1 pp, mountain false water up by at most max(1 km², 10 %), Sevan rough-date dip at most 5 km²), the floodplain does not degrade (altimeter correlation, rank consistency, area error against the independent estimate, IoU, dates in band), a hard guard holds (on every date at least 98 % of the strict detector's confident core stays water), and at least one named weakness improves. Result: the darkening-aware time model, the floodplain expert set, the relaxed threshold and the seeded detector were admitted; the stationary and per-interval time models, the regime as input and three states were rejected. v2 passes the guard (1.12) where v1 failed it (0.81). Limits: three amendments were written after first results, one (the scene switch) after looking at the other places; the floodplain threshold was fitted to the independent estimate, so the 8.5 % area error is partly in-sample; every floodplain criterion comes from one lake. The app therefore now uses one source everywhere ([DETECTION.md, section 4.5](DETECTION.md)).

**Lessons for the next rule**: measure every kind of terrain the product is shown for; make the guard columns part of the rule; keep the TEST strip unseen by earlier tuning; and treat admissions of 1–2 km² as noise.

Modules that failed stay in the repository as documented experiments. Reporting what did not help is part of the result.

## 6. Status

| Part | What was built | Verdict | In the app |
|---|---|---|---|
| Quality control per pass | more than twenty checks, a same-acquisition rule, channel neutralisation | flags Armenia 6 October HV; no false alarm on the flood | **yes**, before detection |
| Conditioning, physics rules, change point, class map, export | as in [DETECTION.md](DETECTION.md) sections 2–4 | baseline | **yes** |
| Terrain mask | Copernicus DEM, slope, height above local low, shadow | admitted | **yes**, every area that has a DEM |
| Flooded vegetation, rebuilt | two-state rule with self-calibration on null land | replaces the old rule (7.8× → 1.53×) | **yes** |
| Threshold tuning against Landsat | split, search, adoption gate | candidate rejected; old thresholds kept | knobs unchanged |
| Statistical change test | omnibus test, empirical null, FDR, uncertainty bands | stated false-alarm rate; area ranges | **yes**: `sig` layer and the area range in the chart |
| Gaussian mixtures + HMM | per-area EM, phantom slot for the outage | best on Sevan's dip; shrinks the flood; per-date version not admitted | no (evaluated only) |
| Graph methods | random walker, TV cut, structure-consistency change, spectral methods | best mountain false water; misses the flood; not admitted; spectral dropped | no |
| Sub-pixel water fraction | linear mixing at the shoreline | Sevan area 1,242.5 km²; admitted | as a fusion v2 expert |
| Scoreboard | every detector, same pixels, labels, split and intervals | used by the rule | – |
| Fusion v1 | log-linear pooling, two-state Markov chain, 3 × 3 smoothing | best for lakes and mountains; lost 19 % of the Tonle Sap flood | replaced by v2 |
| **Fusion v2** | darkening-aware time model, reference-free floodplain zone, relaxed threshold there, seeded detector as expert | Tonle Sap 17 Oct 5,046 km² against an independent 5,421; Sevan and Ararat at least as good as v1; floodplain location improves little | **yes**, the water source for all 15 places |
| Seeded (self-calibrating) detector | per-scene seeds, scene-relative thresholds | rescues Pantanal and Lake Chad; fails on the Nile delta and thin rivers | as a fusion v2 expert |
| Independent flood estimate | lake level and the Landsat record, no radar | Tonle Sap 17 Oct 5,421 km² (4,198–7,222) | reference only |
| Flood front, height from order, choreography | isotonic first-flood date, shape-based interpolation, logistic step fits | no hidden surge; height ρ 0.45 (conservative 0.25) | **yes**: `arrival` and `choreo` layers for Tonle Sap |
| U-Net v2, temporal autoencoder | trained on the physics labels; season embedding | copy the physics rules; U-Net admitted as an expert | through fusion v2 |
| EfficientNet-B4 autoencoder, temporal transformer | large pretrained encoder; masked-date transformer | did not beat a mixture / a straight line in time | no |
| Open pretrained radar models | survey; Microsoft ai4g flood network; ALOS-2 L-band model with a place hold-out | L-band pretraining transfers (0.91 vs 0.67 random) but stays below the physics rules (0.99) | no |
| Independent 2026 validation | altimeter, gauges, reanalysis, weather stations | area follows level (ρ 0.98); two claims corrected | wording of the stories |
| More places | `add_area.py`, one command | 15 places in the app, in Asia, Africa and South America (Armenia sits on the Europe–Asia boundary); Lake Chad and Pantanal, first dropped, returned after the seeded detector; the synthetic `demo` area is being removed | **yes** |
| Learned despeckling | three kinds of small network trained on the images themselves | no detection number improved beyond the ±0.003 test noise; current filter stays ([`despeckle.md`](eda/despeckle.md)) | no (evaluated only) |
| Physics autoencoder | encoder plus a fixed radar-mixing decoder | does not beat the sub-pixel method ([`unmix.md`](eda/unmix.md)) | no (evaluated only) |
| Network on independent labels | U-Net trained on the Landsat record, tested on unseen places | about equal to the math detector; below fusion v2 ([`supervised.md`](eda/supervised.md)) | no; cross-check only |
| Interferometry | coherence, phase, soil moisture | coherence shows the Ararat farming season; no ground motion above ±15 mm; soil moisture not useful ([`insar.md`](eda/insar.md)) | no (a candidate coherence layer exists as a file) |
| Calibrated data: extraction | a small remote server reads windows of the remote files; laptop writes GeoTIFFs | ten Tonle Sap dates (80 m) and eight each of the Sevan and Ararat dates (30 m) extracted | no |
| Calibrated data: detector | dB water rule tuned on one strip, tested on another | Tonle Sap 17 Oct 5,625 km², 5.9 % mean deviation from the independent estimate, balanced accuracy 0.995; the same thresholds give 0 km² at Sevan (higher noise floor in the Armenian mode); PROVISIONAL calibration ([`calibrated.md`](eda/calibrated.md)) | **no**, not yet wired in |

## 7. What would make this worth a global stage

Judges score impact, creativity, validity, relevance and presentation. A longer list of algorithms does not move any of those. What can, and where we stand:

- **One original result people remember.** *Exists*: a map of the day the flood arrived at each place, including the 24 days the satellite was blind, with a measured skill (hiding real passes) and the finding that there was no hidden surge; and a relative height map of the floodplain read from the order of flooding alone, checked against an elevation model it never saw. The prior-art check is done ([`novel.md`](eda/novel.md)): the waterline idea is about 30 years old; the chain and the order-as-height reading are, to our knowledge, a new combination, never "first".
- **Validity nobody else at the hackathon will have.** *Exists*: held-out testing with an adoption gate written in advance, one scoreboard for every detector, uncertainty bands on every area, independent 2026 checks against a NASA altimeter and river gauges, and a public list of our own errors — including the two claims the 2026 data corrected and the gap in our own admission rule.
- **Real NISAR science.** *Partly*: water under vegetation, which L-band sees and optical sensors and C-band radar miss, is now a stable layer whose July rise matches the river gauge. Calibrated data are extracted and analysed for Tonle Sap (the area uncertainty at the flood peak narrows from a factor 2.4 to 1.25), but they are not in the app, the vegetation area is still unvalidated, and the same thresholds fail in Armenia. Coherence adds a second line of evidence in the Ararat Valley.
- **A live app that works on a phone, with places on several continents.** *Partly*: 15 places in Asia, Africa and South America (Armenia on the Europe–Asia boundary) are exported for the app. **Deployment** and a phone check are not confirmed in any document we read.
- **A clear story.** One sentence ("NISAR saw the Tonle Sap rise first under the trees, then on open water, and our area follows NASA's altimeter at 0.98"), one animation (the arrival-day map), one number.

**Still missing**: the work is on the `eda` branch and not yet merged; the app is not yet deployed and its redesign is in progress; the demo video and project page are not started (a pitch deck file exists under `docs/pitch/`); the calibrated detector is not connected to the app and does not work in Armenia without its own thresholds. The largest remaining risk is not an algorithm.
