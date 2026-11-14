# Pretrained radar models on Hugging Face: what could help our NISAR flood and water maps

*Written 14 November 2026 for the team and the judges. Code: [`scripts/detect/foundation.py`](../../scripts/detect/foundation.py). Results: [`docs/eda/foundation/`](../eda/foundation/).*

## The short answer

* There is **one** openly licensed model pretrained on **L-band** radar: **`gsvrg/ALOS-2_FM`** (AIST Japan, MIT licence). It is a Swin-B encoder pretrained with SAR-W-SimMIM on ALOS-2 PALSAR-2 **HH** images of Japan. That is the same radar band and the same HH channel as NISAR, so it is the best physical fit. We wrote a loader for it in plain PyTorch, but we could **not download its 352 MB of weights**: on the event network a download ran at about 50 KB/s, so it would have taken about two hours. The partial download resumes with `foundation download alos2`.
* Almost everything else is pretrained on **Sentinel-1 C-band VV/VH** or on optical images. The NASA–IBM **Prithvi** flood model is **optical** (Sentinel-2 HLS bands), so it cannot read radar.
* We loaded and tested **`torchgeo/ai4g_flood`** (Microsoft AI for Good, MIT licence, 27 MB). It is a global Sentinel-1 flood model and it runs on our MacBook. Results are in the smoke-test section below.
* **Honest expectation:** on today's 8-bit previews a pretrained model is unlikely to beat our math detector. The previews are not radar measurements in decibels, and our own 118k-parameter U-Net already matches the detector. On calibrated NISAR γ⁰ in dB, the L-band model has a real chance of helping.

## 1. Our problem, and why most pretrained models do not fit directly

| | Our NISAR data | Typical public radar pretraining |
|---|---|---|
| Band | **L-band**, 24 cm wavelength | **C-band** (Sentinel-1), 5.6 cm |
| Polarisations | **HH, HV** | **VV, VH** |
| Pixel | ~180 m (previews); 20–80 m calibrated | 10 m |
| Values | 8-bit display brightness (previews); γ⁰ later | σ⁰/γ⁰ in **dB** |
| Labels | none of our own; JRC Global Surface Water for checking | some sets have flood labels |

**Why the band matters, in plain words.** Radar waves interact with things about their own size. C-band waves (5.6 cm) bounce off leaves and twigs at the top of the canopy. L-band waves (24 cm) pass through the canopy and reflect off trunks and the ground. Flooded forest therefore looks **very bright** at L-band, because of the water–trunk "double bounce", but only slightly brighter at C-band. Open calm water is dark at both bands, so **open-water features transfer reasonably well and flooded-vegetation features do not**. HH behaves roughly like VV (both co-polarised) and HV like VH (both cross-polarised), so the natural channel mapping is HH→VV and HV→VH. Absolute levels still differ by several dB, so the inputs must be re-normalised. At 180 m a pixel is 18× wider than a Sentinel-1 pixel: textures such as field edges, streets and river banks that a 10 m model learned do not exist in our images.

**Previews.** The preview brightness behaves like a logarithmic stretch of radar intensity covering roughly 30 dB (`docs/eda/stats-change.md`). We therefore map it to pseudo-decibels with a fixed slope of 30 dB per unit of brightness, plus an offset per scene and channel. The offset is set so that the median brightness of the math detector's open water lands on a typical Sentinel-1 water value (VV −22 dB, VH −28 dB). This is an assumption, and it uses the math detector's own water mask, so it is not independent of it.

Possible adaptations, from cheapest to most expensive:
1. Map channels (HH→VV slot, HV→VH slot) and re-normalise into the model's dB range. This works for any S1 model.
2. Replace or widen the first convolution or patch embedding. The ALOS-2 model takes 1 channel (HH); we widen it to 2 and start the HV weights at zero, so the model initially behaves exactly as pretrained.
3. Freeze the encoder, train a light head on our math-detector labels, then unfreeze the last stage.
4. Fine-tune first on a public labelled flood set (Sentinel-1), then adapt to our scenes. This needs a 2–14 GB download, which is unrealistic on this network tonight.

## 2. Candidate models (each checked on its Hugging Face page or API on 14 Nov 2026)

"Verified" means we checked the repository id, licence tag, file list and sizes through the Hugging Face API today and read the model card. Parameter counts marked ≈ are estimated from the file size or architecture; the card does not state them.

| Model (HF repo) | Licence | Architecture, params | Pretraining sensor / data | Input | Weights (size, format) | Plain PyTorch? | Fit to NISAR L-band HH/HV | Verified |
|---|---|---|---|---|---|---|---|---|
| [gsvrg/ALOS-2_FM](https://huggingface.co/gsvrg/ALOS-2_FM) (SAR-W-SimMIM, [code](https://github.com/gsvrg/SAR-W-SimMIM), [paper](https://arxiv.org/abs/2603.15119)) | MIT (+ JAXA data credit required) | Swin-B (embed 128, depths 2-2-18-2, window 8, abs. pos. emb.), ≈88 M | **ALOS-2 PALSAR-2 L-band HH**, Japan, SimMIM 800 epochs | 1 ch, 256², dB normalised (mean −10.43, std 5.80) | encoder 352 MB `.pth`; LULC-finetuned 1.1 GB | yes — our own Swin module in `foundation.py` (shape-tested, weights not loaded) | **best**: same band, HH; no HV; Japan only | yes |
| [torchgeo/ai4g_flood](https://huggingface.co/torchgeo/ai4g_flood) (Microsoft [ai4g-flood](https://github.com/microsoft/ai4g-flood), Nature Comms 2025) | MIT | U-Net + MobileNetV2, 6.6 M | Sentinel-1 RTC VV/VH, global floods, 10 years | 2 **binary change** channels (VV, VH "became dark" pre→post), 128² | 27 MB `.pth` (state dict) | **yes** (torchvision MobileNetV2 + our decoder) — **loaded, strict key match** | medium: rules are dB thresholds, transferable via channel mapping; floods only, not permanent water | yes |
| [Fabien-Ctrees/sentinel1-water-unet](https://huggingface.co/Fabien-Ctrees/sentinel1-water-unet) | CC BY 4.0 | plain U-Net, 34.6 M | Sentinel-1 VV/VH 10 m, Amazon, 59k labelled water patches | 4 ch (VV,VH,VV,VH) 8-bit from dB | 138 MB `.safetensors` | yes (simple U-Net; card documents layers) | medium: real **water labels** from elsewhere; C-band; Amazon only | yes |
| [wangyi111/SSL4EO-S12](https://huggingface.co/wangyi111/SSL4EO-S12) (`B2_vits16_mae_ep99_enc.pth`, `B2_rn50_moco_0099_backbone.pth`); safetensors copies in [BiliSakura/SSL4EO-S12-transformers](https://huggingface.co/BiliSakura/SSL4EO-S12-transformers) | CC BY 4.0 | ViT-S/16 ≈22 M, ResNet-50 ≈23 M, up to ViT-H | Sentinel-1 GRD VV/VH (B2), global SSL4EO-S12 | 2 ch, 224² | 86 MB (ViT-S enc), 112 MB (RN50) | yes (timm/torchvision layouts) | C-band SSL features, no labels | yes |
| [torchgeo/resnet50_sentinel1_all_moco](https://huggingface.co/torchgeo/resnet50_sentinel1_all_moco) | CC BY 4.0 | ResNet-50, ≈23 M | SSL4EO-S12 Sentinel-1 | 2 ch | 94 MB | yes (torchvision ResNet-50) | as above | yes |
| [wangyi111/softcon](https://huggingface.co/wangyi111/softcon) | CC BY 4.0 | ViT-S/14 or ViT-B/14 (DINOv2 init), RN50 | SSL4EO-S12 S1 (B2) with land-cover-guided contrast | 2 ch | 86 / 342 / 94 MB | yes | C-band; land-cover-aware features | yes |
| [torchgeo/decur](https://huggingface.co/torchgeo/decur) | Apache-2.0 | ResNet-50 | SSL4EO-S12 SAR + MS, decoupled multimodal | 2 ch (SAR model) | 94 MB | yes | C-band | yes |
| [wangyi111/FGMAE](https://huggingface.co/wangyi111/FGMAE) | CC BY 4.0 | ViT-S/B/L/H | SSL4EO-S12 S1 (B2), feature-guided MAE | 2 ch | 86 MB (ViT-S enc) up to 2.5 GB | yes | C-band | yes |
| [nasaharvest/galileo](https://huggingface.co/nasaharvest/galileo) | MIT | ViT nano/tiny/base | multimodal: S1 VV/VH, S2, ERA5, DEM, … | many token groups, months | nano 4 MB, tiny 23 MB, base 346 MB (`.pt`) | not quickly (complex multimodal tokenisation; we would have to re-implement it) | C-band; tiny download | yes |
| [allenai/satlas-pretrain](https://huggingface.co/allenai/satlas-pretrain) (`sentinel1_swinb_si.pth`) | repo Apache-2.0, **weights ODC-BY** | Swin-v2-B ≈88 M | Sentinel-1 VV/VH, supervised on many labels | 2 ch | 434 MB | yes (torchvision `swin_v2_b`) | C-band, supervised features include water | yes |
| [ibm-esa-geospatial/TerraMind-1.0-tiny](https://huggingface.co/ibm-esa-geospatial/TerraMind-1.0-tiny) / small / base | Apache-2.0 | any-to-any multimodal ViT | S1GRD, S1RTC, S2, DEM, LULC, NDVI (TerraMesh) | per-modality tokens | 212 MB / 504 MB / 1.5 GB | **no** — needs `terratorch` (not installed by design) | C-band | yes |
| [ibm-esa-geospatial/TerraMind-base-Flood](https://huggingface.co/ibm-esa-geospatial/TerraMind-base-Flood) | Apache-2.0 | TerraMind-base + temporal decoder | ImpactMesh-Flood (S1RTC+S2+DEM, 4 dates, EMS labels) | S2 12 ch + S1 2 ch + DEM | 673 MB | no (terratorch 1.2.1) | C-band flood labels, needs S2 | yes |
| [ibm-nasa-geospatial/Prithvi-EO-2.0-300M-TL-Sen1Floods11](https://huggingface.co/ibm-nasa-geospatial/Prithvi-EO-2.0-300M-TL-Sen1Floods11) (also 1.0-100M-sen1floods11) | Apache-2.0 | ViT-L ≈300 M (+ decoder) | **Sentinel-2/HLS optical** 6 bands | optical only | 1.28 GB `.pt` | no (terratorch) | **none — optical, cannot read radar** | yes |
| [ibm-nasa-geospatial/Prithvi-EO-2.0-300M](https://huggingface.co/ibm-nasa-geospatial/Prithvi-EO-2.0-300M) (also tiny-TL, 100M-TL, 600M) | Apache-2.0 | ViT ≈300 M | HLS optical | optical | 1.33 GB | partly | none for radar | yes |
| [ibm-granite/granite-geospatial-uki-flooddetection](https://huggingface.co/ibm-granite/granite-geospatial-uki-flooddetection) | Apache-2.0 | Prithvi-type | S1 + S2, UK/Ireland floods | S1+S2 | 1.13 GB `.ckpt` | no (terratorch) | needs optical | yes |
| [made-with-clay/Clay](https://huggingface.co/made-with-clay/Clay) v1.5 | Apache-2.0 | ViT MAE ≈630 M | S1, S2, Landsat, NAIP, … | dynamic | 5.2 GB `.ckpt` | no (too large; Lightning ckpt) | C-band | yes |
| [antofuller/CROMA](https://huggingface.co/antofuller/CROMA) | MIT | ViT-B / ViT-L, radar + optical | S1 + S2 (SSL4EO) | 2 ch radar branch | 778 MB / 2.7 GB | yes, but large | C-band | yes (card is empty; licence tag only) |
| [earthflow/DOFA](https://huggingface.co/earthflow/DOFA) | CC BY 4.0 | ViT-B/L with wavelength-conditioned patch embedding | multi-sensor including S1 | any bands with wavelengths | 448 MB+ | yes with their code | can be given the L-band wavelength (an extrapolation) | yes |
| [wangyi111/Copernicus-FM](https://huggingface.co/wangyi111/Copernicus-FM) | CC BY 4.0 | ViT-B, hypernetwork patch embedding | Copernicus S1/S2/S3/S5P | any | 558 MB | yes with their code | C-band; metadata-flexible | yes |
| [g-astruc/AnySat](https://huggingface.co/g-astruc/AnySat) | MIT | multi-resolution JEPA | many sensors incl. S1 | any | 504 MB | needs their code | C-band | yes |
| [waterdisappear/SARATR-X](https://huggingface.co/waterdisappear/SARATR-X) (and -v2) | **CC BY-NC 4.0** (v1, gated); v2 MIT but **manually gated** | HiViT-B | SAR target-recognition images (ships, aircraft) | 1 ch | 263 MB – 4 GB | needs mmdet/mmseg | poor: object chips, not land/water | yes |
| SAR-W-MixMAE (AIST, [arXiv 2601.15705](https://arxiv.org/abs/2601.15705)) | — | — | ALOS-2 | — | **no weights found on HF** | — | would be L-band | searched, not found |
| DINOv2/DINOv3, SAM/SAM 2, SegFormer | various | photo models | RGB photos | 3 ch | — | yes | weak: speckle statistics and dB brightness are nothing like photos; at 180 m there are few shapes to segment | **not re-checked today** |

**L-band search.** We searched the HF model hub for "ALOS", "PALSAR", "L-band", "NISAR", "UAVSAR" and "SAR", and the web for L-band, ALOS-2 and NISAR foundation models. Results: only `gsvrg/ALOS-2_FM` (HH, Japan). The AIST/JAXA national PALSAR-2 model and SAR-W-MixMAE are described in papers and press but have no public weights that we could find. No NISAR-pretrained model exists. The only NISAR item on HF is `WORLDSAR/nisar-outputs` (GSLC tiles, no labels, no licence tag).

## 3. Labelled public datasets

| Dataset | Sensor | Labels | Licence | Size | Usable tonight? |
|---|---|---|---|---|---|
| Sen1Floods11 ([blumenstiel/Sen1Floods11](https://huggingface.co/datasets/blumenstiel/Sen1Floods11), copy of [cloudtostreet](https://github.com/cloudtostreet/Sen1Floods11)) | S1 VV/VH + S2, 10 m | 446 hand-labelled 512² chips, water/flood, 11 events | original CC BY 4.0 (HF copy has no licence tag) | 1.77 GB (labelled subset) | ~10 h at 50 KB/s — no |
| Kuro Siwo ([orion-ai-lab/Kuro-Siwo-GeoTIFFs](https://huggingface.co/datasets/orion-ai-lab/Kuro-Siwo-GeoTIFFs), webdataset version too) | S1 GRD + SLC, 1 post + 2 pre dates, DEM | expert flood / permanent water, 43 events | CC BY 4.0 | tens of GB (not summed) | no |
| ImpactMesh-Flood ([ibm-esa-geospatial/ImpactMesh-Flood](https://huggingface.co/datasets/ibm-esa-geospatial/ImpactMesh-Flood)) | S1RTC + S2 + DEM, 4 dates | Copernicus EMS flood masks, 206 events, 24,578 samples | CC BY 4.0 | 73 GB total; S1RTC test split 2.1 GB | no |
| ai4g-flood-dataset ([ai-for-good-lab/ai4g-flood-dataset](https://huggingface.co/datasets/ai-for-good-lab/ai4g-flood-dataset)) | S1 | flood maps from the ai4g model | not verified (API timed out) | not verified | no |
| WorldFloods, S1GFloods, UrbanSARFloods, STURM-Flood, SentForFlood | S2 (WorldFloods), S1 (others) | flood masks | not verified today | — | no |
| L-band (ALOS-2/PALSAR, UAVSAR, NISAR) labelled water or flood sets | — | — | — | — | **none found.** The JAXA LULC labels used for ALOS-2_FM are not distributed |

So **no labelled set is realistic tonight**. Our labels remain the math detector (terrain-masked), and the independent check remains JRC Global Surface Water.

## 4. Ranking for our situation

| Rank | Option | L-band HH/HV fit | Licence | Download | Params | Plain PyTorch | Fine-tune time on M5 (est.) | What it adds over our detector | Risk |
|---|---|---|---|---|---|---|---|---|---|
| 1 | **gsvrg/ALOS-2_FM** Swin-B encoder + light head | **high** (L-band HH) | MIT + JAXA credit | 352 MB | ≈88 M | yes (our module) | frozen encoder, head only: ~5–10 min at 224², batch 4 | L-band texture and backscatter features from many scenes | weights not yet downloaded; Japan-only HH; key match unverified; 180 m previews ≠ ALOS-2 stripmap |
| 2 | **torchgeo/ai4g_flood** | medium (via dB rules) | MIT | 27 MB | 6.6 M | **yes, tested** | decoder only: 2–3 min | a flood-cleaning network trained on 10 years of global floods | input is a binary rule mask; ignores permanent water and flooded vegetation |
| 3 | Fabien-Ctrees/sentinel1-water-unet | medium | CC BY 4.0 | 138 MB | 34.6 M | yes (needs a U-Net rewrite) | decoder: ~10 min | **independent** water labels (Amazon) — a second opinion that is not our detector | C-band 10 m; Amazon only; 8-bit dB scaling must be imitated |
| 4 | SSL4EO-S12 S1 ViT-S/16 MAE or SoftCon ViT-S/14 | low–medium | CC BY 4.0 | 86 MB | 22 M | yes | head: ~5 min | generic C-band SAR features | no labels; patch 16 at 180 m is very coarse |
| 5 | Galileo nano/tiny | low–medium | MIT | 4–23 MB | ≈1–6 M | no (complex input) | — | multimodal (S1 + DEM + weather) priors | re-implementation cost |

Not practical here: TerraMind, Prithvi flood, granite flood and Clay (they need terratorch/Lightning, are optical, or exceed 1 GB). SARATR-X is non-commercial or gated and was trained on object chips.

## 5. Recipes

### Primary plan — L-band encoder (gsvrg/ALOS-2_FM)
1. Finish the download with `.venv/bin/python -m scripts.detect.foundation download alos2`. It resumes and needs a fast network, about 352 MB. Check the encoder with `load_alos2_encoder()`: `missing` and `unexpected` must be empty apart from the HV channel.
2. **Input:** HH in dB → `(dB + 10.43) / 5.80`. On previews, first convert to pseudo-dB (30 dB per unit brightness, water anchored at −22 dB). HV goes in as channel 2 through a zero-initialised patch-embedding slice. Use 224 or 256 px tiles (the absolute position embedding is fixed at 256²).
3. **Model:** freeze the encoder and train `LightFPNHead` (≈80k parameters, 3 classes). Use cross-entropy with ignore 255 and the class weights from the U-Net v2 run.
4. **Labels:** v2 `math_labels` from the terrain-masked math detector on Tonle Sap west 70 %, Sevan west two-thirds and Ararat. Train for 3 epochs × 100 steps with batch 4, then unfreeze the last Swin stage at lr 1e-5 for 2 more epochs.
5. **Validation:** IoU against the math labels on the Tonle Sap east 30 % and the Sevan east third. Agreement with JRC Global Surface Water through `scripts/detect/validate.py`, the same metric as the detector. Sevan's largest water body per date against 1,242 km² (the U-Net v2 mean absolute error is 12 km²). False water on slopes: water pixels outside the terrain `water_possible` mask.
6. **Chance it beats the math detector:** on previews **~10–15 %** — the inputs are not dB, the pixels are 180 m, and the labels come from the detector itself. On calibrated 20 m γ⁰ **~35–40 %**, mainly for flooded vegetation, where L-band pretraining knows double-bounce texture that thresholds miss. Anything it "wins" must be shown against JRC, not against the math labels.

### Fallback — ai4g flood network (tested; see below)
1. Take the reference date (first date) and each later date as pre/post. Convert both to pseudo-dB as above, with HH in the VV slot and HV in the VH slot. Apply the ai4g dB rules (2·dB+135 scaling; VV<100, VH<90, a drop >10, minima 75/70) to get two binary change channels.
2. Zero-shot: run the network and keep flood only where both rule channels fire (the official behaviour). This gives a **flood-extent** map, the change from the dry reference, not permanent water.
3. Fine-tune the decoder only (encoder frozen), then decoder plus the last MobileNet stage. The target is "math water now AND NOT math water on the reference date" (terrain-masked). Train on Tonle Sap west with 128² patches, batch 4, CE with weight 3 on flood, about 2 min each.
4. Validate on the Tonle Sap east hold-out (flood IoU against the math detector). At Sevan, check for false flood: the lake is permanent, so every km² of "flood" is an error.
5. **Chance it beats the math detector:** low on previews (≤10 %), because it only ever removes pixels from a threshold mask. On calibrated data ~20 %, as a de-noiser of the change map. Its value for the pitch is a **global, peer-reviewed flood model run on NISAR data**, not accuracy.

## 6. Smoke test and fine-tune results

Model: `torchgeo/ai4g_flood`. We re-implemented it in plain PyTorch (torchvision MobileNetV2 plus our own copy of the smp U-Net decoder) and loaded the weights with `torch.load(weights_only=True)`, without running any code from the model repository. The run used the Apple M5 GPU (MPS), one process, batch ≤ 4 and tiles ≤ 224 px. Labels are the **terrain-masked** math detector. Raw numbers are in [`docs/eda/foundation/smoke.json`](../eda/foundation/smoke.json) and [`finetune.json`](../eda/foundation/finetune.json).

**Smoke test** (`foundation smoke`, 35 s wall clock including loading the scene):

| | |
|---|---|
| weights | 26.8 MB, 374 tensors, **strict load: 0 missing, 0 unexpected keys** |
| parameters | 6,628,802 |
| load time | 3.4 s (includes the torchvision import) |
| forward, 1 × 2 × 224 × 224 on MPS | **38 ms** |
| output | [1, 2, 224, 224] (no flood / flood logits) |
| GPU memory (MPS driver) | 95 MB; peak process memory 1.5 GB (mostly the scene stack) |
| input | Tonle Sap, 23 Sep vs reference 19 Jun, centre 224² patch; pseudo-dB = 30·brightness − 22.1 (HH→VV slot), 30·brightness − 28.1 (HV→VH slot); rule channels fire on 12 % (VV) and 17 % (HV) of the patch |

**Fine-tune** (`foundation finetune --minutes 3 --control`, 6 min wall clock in total, 90 s training per variant). The target is new open water (math water now and not on the reference date). We trained on Tonle Sap west 70 % with 128² patches, batch 4, and CE with flood weight 3. Scores are **agreement with the math detector** on the Tonle Sap east 30 %, over dates 2–10. They are not accuracy.

| Variant | trainable params | steps | IoU (flood) | precision | recall | Sevan false "flood", max km² per date |
|---|---|---|---|---|---|---|
| ai4g rule mask alone (no network) | 0 | – | 0.389 | 0.51 | 0.63 | 2.7 |
| zero-shot, official gating | 0 | – | 0.390 | 0.51 | 0.63 | 2.6 |
| zero-shot, ungated | 0 | – | 0.124 | 0.12 | 0.99 | 59.6 |
| fine-tune, encoder frozen | 4.4 M | 935 | 0.712 | 0.75 | 0.94 | 6.6 |
| fine-tune, + last MobileNet stage | 6.1 M | 796 | **0.725** | 0.78 | 0.91 | 10.6 |
| same network, **random init**, same 90 s | 6.1 M | 781 | 0.635 | 0.66 | 0.94 | 10.2 |

Tonle Sap new-water area for the whole scene, last date (17–18 Oct): fine-tuned 1,662 km², zero-shot gated 1,519 km². The math detector's water grows from 2,234 km² (19 Jun) to about 3,800 km², so it has about 1,600 km² of new water. (The `math_new_water_km2` field in the JSON counts only labelled pixels, without the boundary band, so it reads low at 759 km².)

What this shows:
* **The pipeline works end to end on a MacBook**: a global, peer-reviewed Sentinel-1 flood network loads and runs on NISAR L-band previews in tens of milliseconds per tile.
* **Zero-shot it adds nothing**: with the official gating it equals its own threshold rule (0.390 vs 0.389). Ungated it marks almost everything as flood, because previews are not Sentinel-1 dB.
* **Fine-tuned it reproduces the math detector well** (IoU 0.71–0.73). The pretrained start beats a random start in the same 90 s (0.725 vs 0.635, one seed). The pretraining helps the network learn faster; it does not show that the network knows more. With longer training the gap may close.
* **At Lake Sevan** (a permanent lake, so new water there should be about 0), false flood stays small: ≤ 2.6 km² zero-shot and ≤ 10.6 km² fine-tuned, against a 1,242 km² lake. By design this model does not measure the lake itself, so it cannot be compared with the U-Net v2's 12 km² error on the lake area (`docs/eda/dl-notes.md` §0). It is a flood-change model, not a water model.
* The ALOS-2 L-band encoder was **not tested** (download blocked). Its loader and head pass shape tests only.

## 7. What to say in the pitch — and what not to

Say:
* "We checked the open radar foundation models on Hugging Face. Almost all were trained on Sentinel-1 C-band. The one L-band model (AIST ALOS-2) is our upgrade path for calibrated NISAR data."
* "We ran Microsoft's global Sentinel-1 flood network (MIT) on NISAR L-band, mapping HH→VV and HV→VH. It runs on a laptop in milliseconds per tile." Quote only the measured numbers below.
* "Our small U-Net learns from our physics-based detector. Pretrained models did not give it knowledge it lacked on preview images, because the bottleneck is input quality and labels, not network size."

Do not say:
* that any pretrained model is "more accurate" — we have no ground truth, and every score here is agreement with our own detector;
* that Prithvi maps our floods — the Prithvi flood model is optical (Sentinel-2) only;
* that we used an L-band foundation model — we built its loader but did not load its weights.

## 8. Attribution and licence obligations

* **ai4g-flood** (used): MIT. Keep the copyright notice: "Copyright (c) Microsoft Corporation". Cite Misra, A., White, K., Nsutezo, S. F., Straka III, W., Lavista, J. (2025), *Mapping global floods with 10 years of satellite radar data*, Nature Communications 16, 5762. Weights via `torchgeo/ai4g_flood`.
* **ALOS-2_FM** (if used): MIT (© 2026 AIST). The model card requires us to state that **the source data for the foundation model are ALOS-2, copyrighted by JAXA and provided under the JAXA–AIST agreement**. Cite Imamoglu, Caglayan, Kouyama, arXiv 2603.15119.
* **Fabien-Ctrees water U-Net** (if used): CC BY 4.0. Cite Wagner et al. 2024, Remote Sensing 16(6):1056.
* **SSL4EO-S12 / SoftCon / FG-MAE / Copernicus-FM / DOFA**: CC BY 4.0, which requires citing the papers. **Satlas** weights: ODC-BY, which requires attribution.
* Sentinel-1-derived weights contain no Copernicus imagery, so no extra notice is needed beyond the citations above.

## 9. Reproduce

```bash
.venv/bin/python -m scripts.detect.foundation download ai4g      # 27 MB (parallel ranged download, resumable)
.venv/bin/python -m scripts.detect.foundation smoke              # ~35 s
.venv/bin/python -m scripts.detect.foundation finetune --minutes 3 --control   # ~6 min; weights -> data/processed/models/foundation_ai4g_ft_*.pt
.venv/bin/python -m scripts.detect.foundation download alos2     # 352 MB, needs a fast network; ~37 MB already in data/external/hf/gsvrg_ALOS-2_FM/*.parts
.venv/bin/python -m pytest tests/test_foundation.py
```
No packages were installed for this work. `foundation.py` needs `torchvision` (0.29.2, already installed by another teammate's job), but nothing else beyond torch and numpy.

## Sources
[AIST ALOS-2 foundation model](https://www.aist.go.jp/aist_e/list/latest_research/2025/20251001/en20251001.html) · [SAR-W-SimMIM paper](https://arxiv.org/abs/2603.15119) · [SAR-W-MixMAE paper](https://arxiv.org/abs/2601.15705) · [ai4g-flood code](https://github.com/microsoft/ai4g-flood) · [Sen1Floods11 paper](https://openaccess.thecvf.com/content_CVPRW_2020/papers/w11/Bonafilia_Sen1Floods11_A_Georeferenced_Dataset_to_Train_and_Test_Deep_Learning_CVPRW_2020_paper.pdf) · [UrbanSARFloods](https://arxiv.org/html/2406.04111v1) · Hugging Face API (`/api/models/<repo>?blobs=true`, `/api/datasets/...`) queried 14 Nov 2026 for every repository in the tables.
