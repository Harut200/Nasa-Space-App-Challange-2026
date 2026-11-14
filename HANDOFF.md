# Handoff — 14 November 2026, about 18:50 (Armenia time)

Replaces the first-session handoff. Results: [docs/RESULTS.md](docs/RESULTS.md). Design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Full method: [docs/DETECTION.md](docs/DETECTION.md). Contracts between stages: [plan/00-contracts.md](plan/00-contracts.md).

## Done

- **Pipeline**: ingest, detect, export, web app, 15 places (`areas/*`, `src/app/public/data/index.json`). All 15 use `"water_source": "fusion2"`; `scripts/detect/run.py` reads `data/processed/<area>/fusion2/products.pt` and falls back to the math detector if it is missing.
- **Fusion v2** (water source): Tonle Sap 17 Oct 5,046 km² (range 4,196–6,051) against an independent estimate of 5,421 km² (4,198–7,222); inside the range on 7 of 10 passes. Limits: threshold fitted to the same estimate; 76 % of its water inside the independent map. [fusion2.md](docs/eda/fusion2.md), [hydro.md](docs/eda/hydro.md).
- **Evaluated, not shipped**: seeded detector (used inside fusion v2), learned despeckling, physics autoencoder, network on independent labels, interferometry. Verdicts in RESULTS.md.
- **Calibrated data**: extraction through a small remote server (`scripts/ingest/remote_extract.py`, `remote_run.sh`, [calibrated-data.md](docs/research/calibrated-data.md)). Ten Tonle Sap dates (80 m) analysed; Sevan and Ararat have 8 of 10 dates (30 m). Detector done: 17 Oct 5,625 km², 5.9 % mean deviation from the independent estimate. Same thresholds give 0 km² at Sevan. Not in the app. [calibrated.md](docs/eda/calibrated.md).
- **Docs** updated this evening: README, RESULTS, ARCHITECTURE, DETECTION.
- **Pitch files** exist in `docs/pitch/` (deck as .pptx and .pdf, fact sheet, deck audit, demo script).

## Running or in progress

- **App redesign** (`src/app/`, `src/app2/`): uncommitted work in the tree; the `demo` area is being removed (its files show as deleted).
- Nothing heavy is meant to be running. Check with `ps` before starting a new heavy job; one at a time (see the concurrency note in the team memory: about four heavy agents at most).

## Open, in order

1. Finish and check the redesigned app at desktop and phone width; confirm every place loads and the range display shows.
2. Commit the app work. Commit the doc updates.
3. **Merge `eda` to `main`.** Local `main` is 14 commits behind `origin/main`; the app branches (`backend`, `frontend`) are on `origin` but not merged. Resolve by pull request, not by force.
4. **GitHub Pages**: repository owner sets Settings, Pages, Source, GitHub Actions. `.github/workflows/deploy.yml` deploys on a push to `main`. Nothing is deployed today; no live URL exists.
5. **Demo video and project page** not started. Deck draft in `docs/pitch/`; check its numbers against RESULTS.md (the deck was drafted before fusion v2 and the calibrated result).
6. Wire the calibrated detector into `run.py` only if time allows. The code changes are in [calibrated.md](docs/eda/calibrated.md), section 11. Armenia needs its own thresholds first.
7. Finish the Sevan and Ararat calibrated dates and check the 6 October HV anomaly there.

## Know before you change anything

- Every number in the docs comes from a file in the repo; keep it so. Do not say we beat NASA or any model we did not test.
- Places: Asia, Africa and South America (Armenia is on the Europe–Asia boundary). Do not write "four continents".
- Do not write a server host name, user name, key path or token into any document. `scripts/ingest/remote_run.sh` has defaults for them; set `SERVER` and `SSH_KEY` in the environment and consider removing the defaults before the repository is shown to anyone.
- Previews are uncalibrated 180 m images; the calibration of the real files is PROVISIONAL.
- No radar passes from 27 July to 10 August (instrument outage).
- Contradictions found between notes are listed at the end of [DETECTION.md](docs/DETECTION.md) ("Notes on sources").

## Run

```bash
cd src/app && npm install && npm run dev                       # app, Node 20.19+
.venv/bin/python -m pytest tests -q                            # checks (not run for this handoff)
.venv/bin/python -m scripts.detect.fusion2 <area>              # rebuild one place's water source
.venv/bin/python scripts/detect/run.py <area> && .venv/bin/python scripts/ingest/export.py <area>
```
