# Commit timeline

Generated 15 Nov 2026 from `git log --format='%h %ad %s' --date=format:'%d %b %H:%M'`, grouped by hour (Armenia time, oldest first). 128 commits on the branch `eda` at that time as of about 06:30 on 15 Nov. The last 24 of them, from 00:47 to 06:17 on 15 Nov, were added to this log at about 04:00 and 06:30. `main` had 174 commits at 13:38 on 15 Nov, after the branches were merged and deleted; later commits are not listed here. Account handles and remote names in merge subjects are removed or replaced by `<remote>`. Work after the newest commit is not committed yet (see EVIDENCE.md, known limits).

## 14 Nov 09:00 (4 commits)

| Time | Commit | What landed |
|---|---|---|
| 09:01 | `2c3bdd6` | initial commit |
| 09:03 | `77991da` | second commit |
| 09:05 | `43e5298` | thirdcommit |
| 09:52 | `8cd9fb7` | Remove merge conflict markers from README |

## 14 Nov 10:00 (15 commits)

| Time | Commit | What landed |
|---|---|---|
| 10:00 | `810bd0e` | Add project plan and data source notes for Dancing with the SARs |
| 10:01 | `eda9373` | Merge branch 'main' of <remote> |
| 10:17 | `04ee935` | plans made |
| 10:24 | `f366df7` | Add Python requirements and ignore venv |
| 10:24 | `8facb62` | Add demo fixture, web export and overlay alignment test |
| 10:24 | `c663592` | Add ASF pass search and Tonle Sap track 83 frame 8 config |
| 10:24 | `1de9a40` | Add GCOV window extract and build_area pipeline script |
| 10:25 | `b94b9f7` | Add verified coverage, science brief and submission rules; align PLAN.md with plan/ |
| 10:25 | `f9bf51f` | Merge branch 'origin/main' |
| 10:33 | `e4a1aac` | Add end-to-end pipeline test on synthetic GCOV files |
| 10:39 | `bb65dd1` | Add area configs, preview downloader and shared PyTorch stack loader |
| 10:49 | `4adcadb` | Add functional web app: map, timeline, layers, compare, chart, story |
| 10:53 | `4a9a40e` | Add EDA of preview stacks and align dates in the shared loader |
| 10:54 | `27ffa86` | Add math-based water, flooded-vegetation and change-date detection in PyTorch, with tests |
| 10:57 | `8926d24` | Math detection: common footprint, wider edge mask, results on aligned stack |

## 14 Nov 11:00 (22 commits)

| Time | Commit | What landed |
|---|---|---|
| 11:03 | `97ddec1` | Add handoff notes after first session |
| 11:05 | `0844220` | Merge branch 'main' of <remote> into backend |
| 11:06 | `c7e1123` | Add PyTorch temporal autoencoder, small U-Net, weak labels and parallel sweep runner |
| 11:06 | `77e1c16` | Merge pull request #1 from backend |
| 11:06 | `ca95de0` | Merge pull request #2 from frontend [skip ci] |
| 11:08 | `be6572c` | Merge branch 'origin/main' into backend |
| 11:13 | `63c5ad7` | Merge branch 'origin/frontend' into eda |
| 11:16 | `f59111d` | Add story text, spots and sources for Tonle Sap, Sevan and Ararat |
| 11:20 | `163b81f` | Add ABEL.md with Abel's task list |
| 11:28 | `fb754cc` | Add detector entry point writing contract B, and real preview detections for the app |
| 11:30 | `014365e` | Web app: dark design, class legends, preview notice, HTML timeline, faster overlay load |
| 11:31 | `4f5e2ab` | U-Net v2: train on math-detector labels with season channels, robustness test, v2 sweep |
| 11:32 | `fda171b` | Validate water maps against JRC Global Surface Water |
| 11:38 | `91c5c3c` | Add MIT licence and project README |
| 11:38 | `3c6a2a2` | Merge branch 'origin/main' into eda |
| 11:39 | `d64f805` | Web app: radar overlay renders without the basemap, with fallback note |
| 11:41 | `8c955c3` | Add full detection design document: formulas, parameters, results, limits |
| 11:52 | `813190f` | Add detection architecture draft: experts, priors, fusion, products, admission rule |
| 11:53 | `8f7a78e` | Add statistical change test with calibrated null, FDR control and uncertainty bands on water area |
| 11:56 | `6dcd26b` | Add ARAMEH.md with app notes, requests and recommendations |
| 11:57 | `f538176` | Add threshold tuning with held-out test against JRC reference; current knobs kept |
| 11:57 | `3ba30bb` | Merge branch 'origin/main' into eda |

## 14 Nov 12:00 (17 commits)

| Time | Commit | What landed |
|---|---|---|
| 12:08 | `7629804` | Add terrain mask from Copernicus DEM and apply it to open water in the detector |
| 12:18 | `2378327` | Correct the time stamps in ABEL.md and ARAMEH.md |
| 12:18 | `ea35eee` | Correct time stamps in notes, record torchvision, refresh demo area |
| 12:18 | `ad5c857` | Merge branch 'origin/backend' into eda |
| 12:28 | `894bbd6` | Rebuild flooded-vegetation detector: self-calibrated HH-HV rise, submerging state, water-distance context |
| 12:31 | `df0cb00` | Use the rebuilt flooded-vegetation detector in the app layers, with a separate going-under state |
| 12:31 | `4863910` | Research open pretrained radar models; smoke test and short fine-tune of a Sentinel-1 flood network |
| 12:37 | `73b1018` | Add Gaussian mixture and hidden Markov detector with calibrated probabilities |
| 12:43 | `960f5f6` | Add flood-front arrival time, height from flood order with DEM check, and choreography map |
| 12:43 | `d6f80ac` | Add LaTeX radar detection review |
| 12:47 | `89a1333` | ABEL.md: reply to the detection review, current numbers, adjusted task list |
| 12:47 | `1d230eb` | Label the flooded-vegetation class as possible, as the science review recommends |
| 12:50 | `da88be4` | Add graph-based water refinement, structure-consistency change detection and spectral analysis |
| 12:52 | `f3493f9` | Add EfficientNet-B4 autoencoder with GMM and its comparison against small and no-network baselines |
| 12:57 | `6dd49c2` | Validate 2026 claims against weather stations, reanalysis, river gauges and satellite altimetry |
| 12:58 | `8776264` | ABEL.md: corrections from the independent 2026 validation |
| 12:58 | `9448fbf` | Correct story text after 2026 validation: early lake rise seen as water under vegetation; rain and wind at Sevan |

## 14 Nov 13:00 (8 commits)

| Time | Commit | What landed |
|---|---|---|
| 13:06 | `88f9c92` | Add temporal transformer with masked-date training and outage estimate, evaluated against interpolation baselines |
| 13:17 | `0918c28` | Test the ALOS-2 L-band pretrained model with place hold-out; verified downloader |
| 13:17 | `53653b6` | Add one-command area tool and ten world places with stories, spots and sources |
| 13:17 | `28d9cac` | Add sub-pixel water fraction validated against the 30 m reference at Lake Sevan |
| 13:17 | `63b8c11` | Add common scoreboard for all detectors and the fusion layer with admission rule |
| 13:17 | `330cb16` | Add automatic quality control per pass for every area |
| 13:51 | `8ea6aca` | App: fused water for Sevan and Ararat, quality control, significance, arrival and choreography layers, area ranges, pass quality notes |
| 13:59 | `8f436bb` | Bring detection and architecture documents up to date; add one-page results summary; legend says rain or wind |

## 14 Nov 14:00 (6 commits)

| Time | Commit | What landed |
|---|---|---|
| 14:08 | `cb9145d` | Update area stories, map pins, and detection appendix |
| 14:09 | `d44b274` | Add independent 2026 flood-extent estimate from lake level and long-term water record; calibrate the floodplain threshold |
| 14:09 | `c79b008` | Merge branch 'origin/eda' into eda |
| 14:11 | `7e3352a` | Regenerate app data with Abel's story text and moved spots |
| 14:18 | `e875dce` | Add remote extraction of calibrated NISAR GCOV windows; first calibrated Tonle Sap date |
| 14:46 | `824d98a` | Add per-scene self-calibrating water detection evaluated on fifteen places; fetch only new calibrated files |

## 14 Nov 15:00 (1 commits)

| Time | Commit | What landed |
|---|---|---|
| 15:10 | `cbf42eb` | Add learned despeckling study: evaluated against the current filter, which stays |

## 14 Nov 16:00 (1 commits)

| Time | Commit | What landed |
|---|---|---|
| 16:55 | `e05f211` | Add fusion v2 (one water detector for lakes, mountains and floodplains) and use it for all fifteen places; add Pantanal and Lake Chad to the app |

## 14 Nov 17:00 (4 commits)

| Time | Commit | What landed |
|---|---|---|
| 17:06 | `63212ea` | Show a probability-based range on the fused open-water area |
| 17:52 | `b917170` | Update the results summary with fusion v2, fifteen places and the despeckling study |
| 17:56 | `821f742` | Add interferometry study: coherence shows the Ararat Valley farming season; no ground motion above 15 mm; soil moisture product not useful |
| 17:58 | `72a308c` | Add physics-decoder autoencoder study: does not beat the existing sub-pixel method; not used in the app |

## 14 Nov 18:00 (6 commits)

| Time | Commit | What landed |
|---|---|---|
| 18:02 | `671ab26` | Add network trained on independent labels: matches the math detector on unseen places, does not beat fusion v2; kept as a cross-check |
| 18:32 | `a47dd7e` | Add detector for calibrated NISAR data: dB thresholds, ten-date Tonle Sap series within 5.9 % of the independent estimate; Sevan needs mode-specific thresholds |
| 18:35 | `249365e` | Correct story text that contradicted the current maps and numbers in ten places |
| 18:35 | `d11184d` | Add pitch fact sheet, deck audit and demo shot list |
| 18:40 | `42f36ee` | Update method, architecture, README and handoff for the evening results; add Pantanal and Lake Chad stories; take server address defaults out of scripts |
| 18:58 | `f1940dd` | Add second app design (story mode, chart timeline, gallery) in src/app2 |

## 14 Nov 19:00 (5 commits)

| Time | Commit | What landed |
|---|---|---|
| 19:12 | `4ec76fa` | Rebuild the app: natural-imagery globe landing with fly-in, glass panels, headline number, working date compare, deep links, three themes; remove the demo place |
| 19:19 | `8c9f45c` | Add a short brief for choosing between the two app designs |
| 19:34 | `6976dda` | Add earthquake study: NISAR pair spanning the M7.0 Tibet earthquake of 22 Oct 2026 shows about 127 cm of line-of-sight displacement; control pair flat |
| 19:47 | `6076288` | Story-mode app: fix crash when switching to a place with fewer passes, deep-link date loss, compare handle reach, phone top bar, and smaller issues |
| 19:52 | `2d0f7a5` | Natural-map app: fix phone dot card, Play after the run ends, zero-water headline, in-tab links, About controls |

## 14 Nov 20:00 (3 commits)

| Time | Commit | What landed |
|---|---|---|
| 20:46 | `aba3295` | Add three more earthquakes seen by NISAR (Venezuela, Panama, Kumamoto), four tried and not detected; export earthquake places to the app data |
| 20:50 | `a31f1d0` | Story-mode app: earthquake places (story scenes, explore, gallery section, caveats); list the displayed view first |
| 20:54 | `5fb5048` | Natural-map app: earthquake places (globe dots, event view with before/after bar, reveal against satellite imagery, drawer groups) |

## 14 Nov 21:00 (3 commits)

| Time | Commit | What landed |
|---|---|---|
| 21:57 | `756746e` | Add Ararat Valley coherence layers and the share of valley floor changing between passes to the app data |
| 21:59 | `c60e50f` | Document the earthquake results, the two app designs and the current state; update teammate notes |
| 21:59 | `b346b02` | Correct the place count in the app-choice brief |

## 14 Nov 22:00 (3 commits)

| Time | Commit | What landed |
|---|---|---|
| 22:21 | `2357b4d` | Natural-map app polish: globe dot picking, earthquake card hierarchy, glass legibility, motion and focus states, compare kept in the link |
| 22:27 | `17fcaf9` | Add Tonle Sap on calibrated 80 m data as a place in the app; run calibrated areas through the detector entry point; keep Ararat coherence layers on re-run |
| 22:48 | `2c5f730` | Story-mode app polish: opening composition, Ararat leads with coherence, calibrated place notices, chart and compare legibility, sharper imagery when zoomed in |

## 14 Nov 23:00 (4 commits)

| Time | Commit | What landed |
|---|---|---|
| 23:16 | `3e0a8ef` | Add the 2026 Chao Phraya flood as a place; log six other disaster events tried and not shown |
| 23:32 | `cc1de9e` | Compare our water maps with NASA's OPERA DSWx products at 15 places: strong agreement at Sevan and Tonle Sap, weak in vegetated wetlands and on dark dry ground |
| 23:35 | `f046df2` | Add three ground events seen as loss of radar coherence (Langtang collapse, Kilauea episode 55, Etna lava field); five others tried and not shown |
| 23:57 | `3f5303f` | Give every season place a category (flood, lake, wetland, land) |

## 15 Nov 00:00 (2 commits)

| Time | Commit | What landed |
|---|---|---|
| 00:01 | `4ef5342` | Earthquakes, second round: Venezuela M7.5 and Qaidam M5.6 clear; eight more tried and not shown |
| 00:30 | `91bf844` | Shrink the app data by a quarter with exact-colour class maps; add thumbnails, a one-file summary and a category per place |

## 15 Nov 00:00, commits after 91bf844 (3 commits)

| Time | Commit | What landed |
|---|---|---|
| 00:47 | `fdb2561` | Add the evidence record: full test output, every experiment with verdict and links, negative results, limits, reproduce commands |
| 00:52 | `b69161b` | Bring the summaries in line with their sources: six earthquakes of fifteen, ground events, the NASA product comparison with its failures, 26 places by class |
| 00:58 | `5ae48d5` | Add five more 2026 events: Mahanadi and Terai floods, three wildfire scars (Ontario, Spain); a Wildfires class; places can name their opening layer |

## 15 Nov 01:00, commits after 91bf844 (6 commits)

| Time | Commit | What landed |
|---|---|---|
| 01:06 | `ab735c6` | Add the project and team logos; list Harutyun first in the licence; keep the settings of the events tried and not shown |
| 01:15 | `af4e1f2` | Show the Constellation team logo and name in the README |
| 01:22 | `09a4e82` | Use the correct Constellation logo; add true before-the-event layers and pairing for all nine events |
| 01:44 | `421704a` | Audit whether each of the 31 places shows its story on the opening view: 12 show it, 10 weakly, 9 do not |
| 01:52 | `f7d6856` | Add the Thunder Bay 36 fire seen by lost radar coherence (11-23 July pair against a control pair ending before ignition) |
| 01:57 | `1066663` | Show how each change was found: before, after, difference and outline layers with a control test for three fires and seven other places; La Mierla fire by lost coherence; opening layers, dates and caution labels from the visibility audit |

## 15 Nov 02:00, commits after 91bf844 (5 commits)

| Time | Commit | What landed |
|---|---|---|
| 02:05 | `374794e` | Add the Burgohondo fire seen by lost radar coherence, with a control pair before the fire |
| 02:15 | `052ff42` | Add the Gironde forest fire seen by lost coherence; second look direction for La Mierla and a null-pair check for Thunder Bay |
| 02:27 | `5ec11ad` | Add the Niebla (Spain) and Bald Range (British Columbia) fires seen by lost coherence; record the volcano and flood candidates that showed nothing |
| 02:30 | `4b0036c` | Niebla fire: the straight northern edge appears at the same latitude on both look directions, so it is on the ground, not an image seam |
| 02:57 | `47608f1` | Rename the land class to Farmland and take Rondonia out of the app: no layer shows new clearing; kept as a tried place |

## 15 Nov 03:00, commits after 91bf844 (2 commits)

| Time | Commit | What landed |
|---|---|---|
| 03:12 | `3c2a0c7` | Use of AI: state the team's roles (research, data analysis, detection design, app design) and the assistant role of AI tools |
| 03:20 | `13a1f4e` | Add the Atacama snow and rain storms of August 2026 as a Storms class (lost coherence against a dry control pair); record the volcano, landslide and cyclone candidates that showed nothing |

## 15 Nov 03:00, commits after 13a1f4e (1 commits)

| Time | Commit | What landed |
|---|---|---|
| 03:57 | `ad390ae` | Bring the summaries in line with their sources: 37 places in nine classes, the visibility audit, the evidence layers, round 3 of the event hunt as of 03:57, 242 tests |

## 15 Nov 04:00, commits after 13a1f4e (4 commits)

| Time | Commit | What landed |
|---|---|---|
| 04:28 | `b0f5c3d` | Atacama storm: rain dates sourced, the change bracketed to 16-19 August by a pair that ends before it, recovery within about three weeks; record more candidates that showed nothing |
| 04:48 | `3985174` | Add the Sheveluch explosive eruptions of late September 2026 (Kamchatka): lost coherence around the lava domes on both look directions; snow not excluded |
| 04:54 | `e3c13f5` | Withdraw Sheveluch from the app: the lost coherence follows elevation, not distance from the domes, and does not recover, so autumn snow explains it; kept as a marginal tried event |
| 04:55 | `78f1394` | Langtang passes the elevation test: the lost coherence does not rise with height and is local at fixed height |

## 15 Nov 05:00, commits after 13a1f4e (2 commits)

| Time | Commit | What landed |
|---|---|---|
| 05:00 | `950eed7` | Elevation test as a reusable check (loss_by_elevation) with a synthetic test; the neighbouring Klyuchevskoy group confirms the snow reading at Sheveluch |
| 05:24 | `5802ee5` | Atacama storm: show the clean single-storm pair (16-28 August, about 1,000 km2) instead of the longer pair that mixes several weather episodes |

## 15 Nov 06:00, commits after 13a1f4e (1 commits)

| Time | Commit | What landed |
|---|---|---|
| 06:17 | `6e3330c` | Round 3 of the event hunt: final log of 42 configurations, clear, marginal and not detected, with the do-not-claim list |

