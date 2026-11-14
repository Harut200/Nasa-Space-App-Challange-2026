# Handoff — 15 November 2026, updated about 04:00 (Armenia time; first written 00:50)

Replaces the 22:00 handoff of 14 November. Results: [docs/RESULTS.md](docs/RESULTS.md). Design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Method: [docs/DETECTION.md](docs/DETECTION.md). Earthquakes: [docs/eda/quake.md](docs/eda/quake.md). File formats: [plan/00-contracts.md](plan/00-contracts.md).

## Current state

- **Water**: the water detector is fusion v2 (`"water_source": "fusion2"`); the app has 18 water and farmland places (floods 8, lakes 4, wetlands and deltas 4, farmland 2). Tonle Sap 17 Oct 5,046 km² (range 4,196–6,051) against an independent 5,421 km² (4,198–7,222); inside the range on 7 of 10 passes. Limits: threshold fitted to the same estimate; 76 % of its water inside the independent map ([fusion2.md](docs/eda/fusion2.md)).
- **Earthquakes**: fifteen checked against NISAR L2 GUNW pairs; six clear (Tibet M7.0 127 cm, Panama M7.7 133 / 85 cm, Venezuela M7.2 51 / 55 cm, Venezuela M7.5 48 cm descending, Kumamoto M6.8 38 cm, Qaidam M5.6 one compact patch, about +6 to +10 cm), one marginal (Huancayo M5.5), eight not detected (Flores, Mexico, Sanriku, Qinghai, Khuzdar, Redwood Valley, Turangi, Qaidam M5.3). Venezuela M7.2 and M7.5 cannot be separated; "NISAR sees M5 earthquakes" is not supported (of seven small events, 1 clear, 1 marginal, 5 nothing). No independent check; sign unconfirmed; PROVISIONAL products. Code `scripts/detect/quake.py`, tests `tests/test_quake.py`.
- **Other ground events** ([docs/eda/events.md](docs/eda/events.md)), as of 03:57 (round 3 runs until 07:30; counts from `docs/eda/events/events.json`, `results.json`, `app_events.json`): 43 candidates, 10 clear as loss of coherence (all in the app), 3 marginal, 2 not detected, 28 without a verdict field yet. Clear: Langtang Lirung rock-ice collapse (26 Aug, about 8.5 km²), Kilauea episode 55 (17 Oct, about 10.2 km²), Etna Valle del Bove lava field (Jul–Aug, about 2.5 km²), six wildfires (Thunder Bay 36, La Mierla, Burgohondo, Gironde, Niebla, Bald Range) and the Atacama snow and rain storms (about 6,000 km² in the box). Marginal: Brits, Kilauea 54, Ambrym. Tried with nothing (log): Klyuchevskoy, Krasheninnikov, Nyamulagira, Chillan, Semeru, Lewotolok, Fuego, Hanjia, Hurricane Isaias, Big Grass, and three floods by coherence. Gironde is clear by coherence but marginal by brightness; `results.json` has no verdict for it. Not claimed: lava vs ash vs debris vs burnt forest vs snow, volumes, damage or burnt area (write "lost coherence"), date within the pair, motion.
- **Disaster events from previews** ([docs/research/events-2026.md](docs/research/events-2026.md), state 00:50): six clear in the log (Chao Phraya, Mahanadi, Terai, Thunder Bay 36, La Mierla, Avila; all six are now places in the app), four marginal, the rest nothing. Chao Phraya: open water about 990 to about 1,930 km², 6 to 30 Sep; 82 % of the new water inside the JRC maximum extent.
- **How we found it** ([docs/eda/burn.md](docs/eda/burn.md)): before, after, difference and a control test for three fires (area of changed radar echo: Thunder Bay 36 1,875 km², La Mierla 397, Avila 760) and Okavango, Sindh-Indus, Nile delta, Poyang, Haor, Urmia. Negative: control not clean at Thunder Bay, Sindh, Poyang, Urmia; Sudd nothing; Gironde fades by brightness; Rondônia no clear clearing (taken out of the app, `in_app: false`). Code `scripts/detect/evidence.py`, `burn.py`, `evidence_places.py`, tests `tests/test_burn.py`. Never write "burnt area" or "damage".
- **Visibility audit** ([docs/eda/visibility-audit.md](docs/eda/visibility-audit.md)): 31 places, 12 show it, 10 weakly, 9 not (before the fixes, not repeated). Fixes live in `areas/*/area.json`: `default_layer`, `default_date`, `caution` (Inner Niger, Lake Chad, Urmia, Okavango, Sindh-Indus), `headline`, `headline_note`. Avila's water, HV and class layers are still blank (cause and fix in burn.md, not applied).
- **NASA OPERA comparison** ([docs/eda/opera.md](docs/eda/opera.md)): our open-water maps agree with OPERA DSWx-S1 on 97.5 % of pixels at Sevan and Tonle Sap (IoU 0.84, 20 place-dates); over 101 place-dates at 15 places 94 % of pixels but mean IoU 0.38. Wrong in our maps at Inner Niger (about four times OPERA's water), Lake Chad (about half) and Urmia (false water on a wet salt flat); weak at the deltas, Pantanal and Sudd. The 97–99 % balanced accuracies were measured only on certain pixels. Say "agree with"; never "better than NASA".
- **App**: three designs on the same data, 37 places in 9 classes (Floods 8, Lakes 4, Wetlands and deltas 4, Farmland 2, Wildfires 9, Earthquakes 6, Volcanoes 2, Landslides 1, Storms 1; `src/app/public/data/summary.json`): `src/app/` (A, satellite globe), `src/app2/` (B, story mode) and the merged `src/app3/` (story, a "How we found it" stepper per kind, before/after compare, caution labels). The front-end code lives on branch `front_harut` and is deliberately not merged to `main` yet; no live URL; no design is declared final here. The data contract gained `defaultDate`, `caution`, `evidence`, `burn` and `in_app` (`scripts/ingest/export.py`, `optimize.py`). Calibrated Tonle Sap is in as `tonle-sap-cal` (open water 2,340 to about 5,660 km² on 17 Oct; the study quotes 5,625 inside the preview footprint). Data folder 65.7 MB with 25 places and thumbnails ([data-size.md](docs/eda/data-size.md)); 81 MB with 37 (`du -sm`, 15 Nov). Brief for choosing: [docs/pitch/app-choice.md](docs/pitch/app-choice.md). The `demo` place is gone from the app data.
- **Tests**: 242 passed in 34 files, 15 Nov about 03:50 ([docs/evidence/pytest-full.txt](docs/evidence/pytest-full.txt)); the app's Node tests (`cd src/app && npm test`): 22, 21 pass, 1 fails (it expects no Storms class; `src/app/tests/app.test.js` line 115; not fixed).
- **Stories**: Pantanal and Lake Chad added; story text for ten places corrected against current numbers (Ararat open water is about 0.3 km²).
- **Pitch files** in `docs/pitch/`: deck (.pptx, .pdf), `FACTS.md`, `deck-audit.md`, `demo-script.md`. The deck predates fusion v2, the calibrated run and the earthquakes.
- **Server access**: scripts read `SERVER` and `SSH_KEY` from the environment; no defaults remain.

## In progress at 04:00

- Ararat Valley coherence layers in the app (not finished; do not quote numbers beyond [insar.md](docs/eda/insar.md)).
- The merged app `src/app3/` (branch `front_harut`).
- Event-hunt round 3, until 07:30 (counts above are "as of 03:57"; `docs/eda/events.md` does not yet list every candidate that `events.json` holds).

## Open, in order

1. **Decide which app design is submitted** (`src/app`, `src/app2` or `src/app3`); then fix what a click-through finds.
2. Commit the app, data and doc work. Several files are uncommitted.
3. **Merge `eda` into `main`** by pull request, not by force. Local `main` is behind `origin/main`.
4. **Turn on GitHub Pages** (Settings, Pages, Source: GitHub Actions). `.github/workflows/deploy.yml` builds `src/app` only; change it if another design is chosen. Nothing is deployed; no live URL exists.
5. **Demo video** (30 s or less) and **project page** not started. Script: [docs/pitch/demo-script.md](docs/pitch/demo-script.md).
6. Update the deck: add the earthquakes and ground events, the 37 places, the evidence layers, the OPERA comparison and the fusion-v2 numbers ([deck-audit.md](docs/pitch/deck-audit.md)).
7. Judging is at 15:00 on 15 Nov; submit before 23:59 on 15 Nov.

## Traps

- A full `pytest` run no longer leaves the `demo` place in `src/app/public/data`: `tests/conftest.py` removes it (confirmed after the evidence run).
- Previews on localhost die when the dev server restarts; restart them and reload.
- The `frontend` branch has invented weather events and a planets switch. They must not ship.
- Do not write a host name, user name, IP address or key path in any document.
- Every number in the docs comes from a file in the repo. Do not say we beat NASA or any model we did not test. Name the continents (Asia, Africa, South America, North America, Europe; Armenia on the Europe–Asia boundary), do not use a bare count. Do not present Inner Niger, Lake Chad or Pantanal as solved: see the OPERA comparison.
- Earthquake claims (also: no "NISAR sees M5 earthquakes"; Venezuela M7.2 and M7.5 not separable): see the "do not claim" list in [quake.md](docs/eda/quake.md): no damage or casualties, no fault plane or slip, no vertical or horizontal motion except Venezuela's approximate split, no validation against other data.
- No radar passes from 27 July to 10 August (instrument outage).
- Contradictions between notes are listed at the end of [DETECTION.md](docs/DETECTION.md) ("Notes on sources").

## Run

```bash
cd src/app  && npm install && npm run dev -- --port 5173                         # design A
cd src/app2 && ln -s ../app/node_modules node_modules && npx vite --port 5174    # design B
# src/app3 (branch front_harut): see its own package.json
.venv/bin/python -m pytest tests -q                  # 242 tests, about 80 seconds
.venv/bin/python -m scripts.detect.quake list        # earthquake events
```
