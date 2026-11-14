# Handoff — 14 November 2026, about 22:00 (Armenia time)

Replaces the 18:50 handoff. Results: [docs/RESULTS.md](docs/RESULTS.md). Design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Method: [docs/DETECTION.md](docs/DETECTION.md). Earthquakes: [docs/eda/quake.md](docs/eda/quake.md). File formats: [plan/00-contracts.md](plan/00-contracts.md).

## Current state

- **Water**: 15 places, all `"water_source": "fusion2"`. Tonle Sap 17 Oct 5,046 km² (range 4,196–6,051) against an independent 5,421 km² (4,198–7,222); inside the range on 7 of 10 passes. Limits: threshold fitted to the same estimate; 76 % of its water inside the independent map ([fusion2.md](docs/eda/fusion2.md)).
- **Earthquakes**: eight checked against NISAR L2 GUNW pairs; four clearly seen (Tibet M7.0 127 cm, Panama M7.7 133 / 85 cm, Venezuela M7.2 51 / 55 cm, Kumamoto M6.8 38 cm), four not detected (Flores, Mexico, Sanriku, Qinghai). No independent check; sign unconfirmed; PROVISIONAL products. Code `scripts/detect/quake.py`, tests `tests/test_quake.py`.
- **App**: two finished, tested designs on the same data, 19 places (15 water, 4 earthquakes): `src/app/` (globe, compare, reveal) and `src/app2/` (story mode). Brief for choosing: [docs/pitch/app-choice.md](docs/pitch/app-choice.md) (it still says 15 places). The `demo` place is gone from the app data.
- **Stories**: Pantanal and Lake Chad added; story text for ten places corrected against current numbers (Ararat open water is about 0.3 km²).
- **Pitch files** in `docs/pitch/`: deck (.pptx, .pdf), `FACTS.md`, `deck-audit.md`, `demo-script.md`. The deck predates fusion v2, the calibrated run and the earthquakes.
- **Server access**: scripts read `SERVER` and `SSH_KEY` from the environment; no defaults remain.

## In progress at 22:00

- Calibrated Tonle Sap as its own place in the app (`tonle-sap-cal`).
- Ararat Valley coherence layers in the app.
- Neither is finished; do not quote numbers for them beyond [calibrated.md](docs/eda/calibrated.md) and [insar.md](docs/eda/insar.md).

## Open, in order

1. **Choose the app design** tonight (A or B); then fix what a click-through finds. Design B has not been tried by hand.
2. Commit the app, data and doc work. Several files are uncommitted.
3. **Merge `detection` into `main`** by pull request, not by force. Local `main` is behind `origin/main`.
4. **Turn on GitHub Pages** (Settings, Pages, Source: GitHub Actions). `.github/workflows/deploy.yml` builds `src/app` only; change it if design B is chosen. Nothing is deployed; no live URL exists.
5. **Demo video** (30 s or less) and **project page** not started. Script: [docs/pitch/demo-script.md](docs/pitch/demo-script.md).
6. Update the deck: add the earthquakes, the 19 places and the fusion-v2 numbers ([deck-audit.md](docs/pitch/deck-audit.md)).
7. Submit before 23:59 on 15 Nov.

## Traps

- A full `pytest` run re-creates the `demo` place in `src/app/public/data` (and its `index.json` entry). Remove it again before building.
- Previews on localhost die when the dev server restarts; restart them and reload.
- The `frontend` branch has invented weather events and a planets switch. They must not ship.
- Do not write a host name, user name, IP address or key path in any document.
- Every number in the docs comes from a file in the repo. Do not say we beat NASA or any model we did not test. Say "Asia, Africa and South America", not "four continents".
- Earthquake claims: see the "do not claim" list in [quake.md](docs/eda/quake.md): no damage or casualties, no fault plane or slip, no vertical or horizontal motion except Venezuela's approximate split, no validation against other data.
- No radar passes from 27 July to 10 August (instrument outage).
- Contradictions between notes are listed at the end of [DETECTION.md](docs/DETECTION.md) ("Notes on sources").

## Run

```bash
cd src/app  && npm install && npm run dev -- --port 5173                         # design A
cd src/app2 && ln -s ../app/node_modules node_modules && npx vite --port 5174    # design B
.venv/bin/python -m pytest tests -q                  # re-creates the demo place, see Traps
.venv/bin/python -m scripts.detect.quake list        # earthquake events
```
