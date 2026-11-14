# Dancing with the SARs

**Press play on a season of radar.** A web app that lets anyone watch the Earth's surface change through ten passes of NASA and ISRO's NISAR radar satellite, June to October 2026, for 15 places in Asia, Africa and South America, and read what is physically happening and why radar can see it. (Armenia sits on the Europe–Asia boundary.) The app is being redesigned; it is not deployed.

NASA Space Apps Challenge 2026 · challenge *Dancing with the SARs* · team: Arameh Haghverdian, Abel Babayan, Harutyun Kesablyan.

![Tonle Sap: when each place changed](docs/design/screenshots/tonle-sap-when-desktop.png)

## What it shows

| Place | What the radar shows |
|---|---|
| **Tonle Sap, Cambodia** | The monsoon flood spreading like a wave: the river plain south-east of the lake goes under in late August, the ring around the lake in September |
| **Lake Sevan, Armenia** | A lake that does not change — and how wind and rain can fool a naive method into thinking it shrank |
| **Ararat Valley, Armenia** | What radar at this resolution does not show: fields smaller than a pixel. (Interferometry does show the farming season there: [docs/eda/insar.md](docs/eda/insar.md)) |

Twelve more places use the same detector. Balanced accuracy of the water map against Landsat water pixels that never change, from [docs/eda/fusion2.md](docs/eda/fusion2.md), section 8 (an easy test: shorelines and floodplains are not in it):

| Place | Accuracy | Place | Accuracy |
|---|---|---|---|
| Poyang Lake, China | 99.9 % | Lake Chad | 99.3 % |
| Pantanal, Brazil | 99.98 % | Inner Niger Delta, Mali | 97.3 % |
| Lake Urmia, Iran | 97.0 % | The Sudd, South Sudan | 96.7 % |
| Nile Delta, Egypt | 71.4 % (do not quote a water area) | Mekong floodplain, Vietnam–Cambodia | 65.1 % (rivers narrower than a pixel) |
| Haor wetlands, Bangladesh; Okavango Delta, Botswana; Indus plain, Sindh; Rondônia, Brazil | no permanent water to score; only false water is measured | | |

For each place: a timeline of ten radar passes, a map of open water and flooded vegetation per date, a map of *when* each place changed, a before/after comparison, curves at named spots, and a four-part story.

## Honest status

State of 14 November 2026, about 18:50 (Armenia time). One-page summary of results: [docs/RESULTS.md](docs/RESULTS.md).

- **The app shows previews.** Everything in the app uses NISAR's free preview images: about 180 m per pixel and not calibrated. Patterns and timing are reliable; exact values and areas are not. The app says so on screen.
- **Calibrated data are extracted and analysed, but not in the app.** A small remote server reads windows of the large calibrated files, so nothing big is downloaded. All ten Tonle Sap dates (80 m) are analysed, and most Armenia dates (30 m) are extracted. On Tonle Sap a dB water rule gives 5,625 km² on 17 October, 5.9 % from the independent estimate on average (previews: 20 %), and its area moves by a factor 1.25 over a justified threshold range at the flood peak (previews: 2.39). The same thresholds find 0 km² at Lake Sevan, because the Armenian product has a noise floor 7–8 dB higher. The calibration is PROVISIONAL. Details: [docs/eda/calibrated.md](docs/eda/calibrated.md).
- **One water detector for every place (fusion v2).** It sizes the Tonle Sap flood at 5,046 km² on 17 October (range 4,196–6,051) against an independent estimate of 5,421 km² (4,198–7,222) built from the lake level and 38 years of Landsat water maps, with no radar; it falls inside that range on 7 of 10 passes (earlier detectors: 1 of 10). Limits: its floodplain threshold was fitted to the same estimate, only 76 % of its water lies inside the independent map, and the estimate is a model, not a measurement. Details: [docs/eda/fusion2.md](docs/eda/fusion2.md), [docs/eda/hydro.md](docs/eda/hydro.md).
- **Checked against independent sources.** At Lake Sevan our permanent water overlaps 38 years of Landsat water maps (JRC Global Surface Water) at 0.94, and at Tonle Sap our open-water area follows the lake level from a NASA satellite altimeter (rank correlation 0.98 over ten passes). The checks also corrected us: the lake began rising in early July, which radar saw as water under vegetation, and one of the two "windy" passes over Lake Sevan followed a night of rain. Details: [docs/eda/validation.md](docs/eda/validation.md), [docs/eda/validation-2026.md](docs/eda/validation-2026.md).
- **Interferometry.** Radar phase shows the farming season on the Ararat Valley floor (the share of incoherent ground falls from 43–44 % in June–July to 22–25 % from mid-September). We found no ground motion above ±15 mm in 120 days and the soil-moisture product did not help. Details: [docs/eda/insar.md](docs/eda/insar.md).
- **Tried, did not help**: learned despeckling, a physics autoencoder, a network trained on independent labels (a cross-check only), and other models, listed in [docs/RESULTS.md](docs/RESULTS.md).
- **No radar passes exist from 27 July to 10 August 2026** (instrument outage). The timeline shows the gap.
- **One season**, ten passes. The work is on the `eda` branch and is not merged to `main`.

## How it works

```
NISAR GCOV (ASF)  →  cut and align  →  detect  →  export  →  static web app
                     scripts/ingest    scripts/detect  scripts/ingest  src/app
```

Detection is mathematics on PyTorch tensors: speckle filtering across all dates, quality control of every pass, a terrain mask, a flooded-vegetation rule (HH rises while HV does not), a change-point test that dates each pixel's change, and a probability-based water detector (fusion v2) that combines several experts, including a detector that calibrates itself on each scene, with a time model that lets a pixel turn to water where the radar darkens. Small neural networks are among the experts or are evaluated as alternatives; none draws a layer on its own. For calibrated data, a separate path reads the large files on a small remote server and a dB detector runs on the result.

- Results at a glance: [docs/RESULTS.md](docs/RESULTS.md)
- System design and what is shipped or only evaluated: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Full design, formulas, parameters and results: [docs/DETECTION.md](docs/DETECTION.md)
- Lane write-ups: [fusion v2](docs/eda/fusion2.md), [independent flood estimate](docs/eda/hydro.md), [self-calibrating detector](docs/eda/crossscene.md), [calibrated data](docs/eda/calibrated.md) and [how it was extracted](docs/research/calibrated-data.md), [interferometry](docs/eda/insar.md), [despeckling](docs/eda/despeckle.md), [physics autoencoder](docs/eda/unmix.md), [network on independent labels](docs/eda/supervised.md)
- Exploratory analysis: [docs/eda/eda-findings.md](docs/eda/eda-findings.md); method note: [docs/eda/math-detection.md](docs/eda/math-detection.md); neural networks: [docs/eda/dl-notes.md](docs/eda/dl-notes.md)
- File formats between the stages: [plan/00-contracts.md](plan/00-contracts.md)

## Run it

Web app (Node 20.19 or newer):

```bash
cd src/app
npm install
npm run dev
```

Rebuild the detections from scratch (Python 3.12 or newer; no login needed for previews):

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-detect.txt

for area in tonle-sap sevan ararat; do   # or any id in areas/
  .venv/bin/python scripts/ingest/fetch_previews.py $area   # download preview images
  .venv/bin/python scripts/detect/run.py $area               # detect
  .venv/bin/python scripts/ingest/export.py $area            # write web images
done

.venv/bin/python -m pytest tests -q                          # checks
.venv/bin/python -m scripts.detect.validate                  # compare with Landsat water maps
```

`run.py` reads the fusion v2 products when they exist (`.venv/bin/python -m scripts.detect.fusion2 <area>`, a few seconds to 20 s per place) and falls back to the simple detector otherwise.

Train the networks (Apple GPU or CPU):

```bash
.venv/bin/python -m scripts.detect.train sweep --model unet --versions v2 --workers 3
```

## Repository

| Path | Holds |
|---|---|
| `src/app/` | Web app (Vite, React, MapLibre GL JS). `public/data/` holds the exported images |
| `scripts/ingest/` | Search, download, cut, export; the calibrated-data extractor that runs on a remote server |
| `scripts/detect/` | Loader, detection maths, detector entry point, fusion, validation, neural networks, calibrated-data detector |
| `areas/` | One folder per place: box, track and frame, story text, spots |
| `tests/` | Automated checks on synthetic scenes |
| `docs/` | Analysis, method, validation, research notes, screenshots, pitch files |
| `plan/` | Work plan and file contracts |

## Data and credits

- **NISAR GCOV preview images**: NASA/JPL-Caltech and ISRO, distributed by the Alaska Satellite Facility DAAC.
- **NISAR GCOV calibrated, GUNW and SME2 products** (PROVISIONAL): NASA/JPL-Caltech and ISRO, distributed by the ASF DAAC.
- **Global Surface Water** (validation, and labels for some evaluated networks): Pekel, Cottam, Gorelick, Belward (2016), *High-resolution mapping of global surface water and its long-term changes*, Nature 540, 418–422. Source: EC JRC/Google.
- **Basemap**: OpenFreeMap, OpenMapTiles, OpenStreetMap contributors.
- Sources for every figure quoted in the stories: [docs/research/story-sources.md](docs/research/story-sources.md).

## Use of AI

AI tools (Claude) were used for research, planning documents, code, analysis and first drafts of the story text. The team chose the challenge approach and the places, reviewed the results and edited the text.

## Licence

[MIT](LICENSE).
