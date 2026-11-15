# Handoff — 15 November 2026, updated about 04:00 (Armenia time; first written 00:50)

Replaces the 22:00 handoff of 14 November. Results: [docs/RESULTS.md](docs/RESULTS.md). Design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Method: [docs/DETECTION.md](docs/DETECTION.md). Earthquakes: [docs/eda/quake.md](docs/eda/quake.md). File formats: [plan/00-contracts.md](plan/00-contracts.md).

## Current state

- **Water**: the water detector is fusion v2 (`"water_source": "fusion2"`); the app has 18 water and farmland places (floods 8, lakes 4, wetlands and deltas 4, farmland 2). Tonle Sap 17 Oct 5,046 km² (range 4,196–6,051) against an independent 5,421 km² (4,198–7,222); inside the range on 7 of 10 passes. Limits: threshold fitted to the same estimate; 76 % of its water inside the independent map ([fusion2.md](docs/eda/fusion2.md)).
- **Earthquakes**: fifteen checked against NISAR L2 GUNW pairs; six clear (Tibet M7.0 127 cm, Panama M7.7 133 / 85 cm, Venezuela M7.2 51 / 55 cm, Venezuela M7.5 48 cm descending, Kumamoto M6.8 38 cm, Qaidam M5.6 one compact patch, about +6 to +10 cm), one marginal (Huancayo M5.5), eight not detected (Flores, Mexico, Sanriku, Qinghai, Khuzdar, Redwood Valley, Turangi, Qaidam M5.3). Venezuela M7.2 and M7.5 cannot be separated; "NISAR sees M5 earthquakes" is not supported (of seven small events, 1 clear, 1 marginal, 5 nothing). No independent check; sign unconfirmed; PROVISIONAL products. Code `scripts/detect/quake.py`, tests `tests/test_quake.py`.
- **Other ground events** ([docs/eda/events.md](docs/eda/events.md)): Round 3 ended at about 06:20 on 15 November (final commit `6e3330c`). Final state (`events.json`, `results.json`, `app_events.json`, `events.md`): `events.json` holds 53 configurations (42 of them added in round 3, per `events.md`), all with results in `results.json`: 10 clear (all 10 are places in the app), 5 marginal (Brits, Kilauea 54, Ambrym, Sakurajima and Sheveluch, which was exported as clear and withdrawn, see below), 31 not detected (one of them decorrelated), 4 entries that support or repeat another event (the Atacama coast, Taltal and east boxes, and Etna's later pairs), 2 fire signals not exported (Hautes Fagnes, Rowe Creek) and 1 not attributed (the Panama earthquake landslides). Clear: Langtang (about 8.5 km²), Kilauea 55 (about 10.2 km²), Etna (about 2.5 km²), six wildfires by lost coherence and the Atacama rain storm (shown on the ascending pair of 16–28 Aug, about 1,004 km², 0.82 → 0.62; the descending pair of about 5,950 km² is text only). Sheveluch was withdrawn (elevation test: first snow); `loss_by_elevation` is in `scripts/detect/events.py` with a test (13 event tests). Not detected (`events.json`): Budhi Gandaki and Kali Gandaki landslides, Klyuchevskoy, Krasheninnikov, Nyamulagira, Chillán, Lewotolok, Stromboli, Semeru, Fuego, Great Sitkin, Bezymianny, Etna June, Kilauea episode 53, Santa María, the Hanjia landslide, Hurricane Isaias, Typhoon Noul, the Polo remnants (Arizona, New Mexico, Baja California Sur, Sonora), the Hotan rain, the Big Grass fire, the Gold Ridge mine, the Colombia M7.4 and the two Alaska M5.6 earthquakes, and the Mahanadi, Terai and Chao Phraya floods by coherence. Gironde is clear by coherence but marginal by brightness. New do-not-claim items (`events.md`): a 24- or 36-day event pair compared with a 12-day control compares only approximately; an autumn loss of coherence in mountains needs an elevation test before it is called volcanic; the cause of the Atacama loss (rain, snow, mud) is not separable; Hautes Fagnes is a clear signal not exported because the fire class is capped, and Rowe Creek's patch is cut by the window edge. Not claimed: lava vs ash vs debris vs burnt forest vs snow, volumes, damage or burnt area (write "lost coherence"), date within the pair, motion.
- **Disaster events from previews** ([docs/research/events-2026.md](docs/research/events-2026.md), state 00:50): six clear in the log (Chao Phraya, Mahanadi, Terai, Thunder Bay 36, La Mierla, Avila; all six are now places in the app), four marginal, the rest nothing. Chao Phraya: open water about 990 to about 1,930 km², 6 to 30 Sep; 82 % of the new water inside the JRC maximum extent.
- **How we found it** ([docs/eda/burn.md](docs/eda/burn.md)): before, after, difference and a control test for three fires (area of changed radar echo: Thunder Bay 36 1,875 km², La Mierla 397, Avila 760) and Okavango, Sindh-Indus, Nile delta, Poyang, Haor, Urmia. Negative: control not clean at Thunder Bay, Sindh, Poyang, Urmia; Sudd nothing; Gironde fades by brightness; Rondônia no clear clearing (taken out of the app, `in_app: false`). Code `scripts/detect/evidence.py`, `burn.py`, `evidence_places.py`, tests `tests/test_burn.py`. Never write "burnt area" or "damage".
- **Visibility audit** ([docs/eda/visibility-audit.md](docs/eda/visibility-audit.md)): 31 places, 12 show it, 10 weakly, 9 not (before the fixes, not repeated). Fixes live in `areas/*/area.json`: `default_layer`, `default_date`, `caution` (Inner Niger, Lake Chad, Urmia, Okavango, Sindh-Indus), `headline`, `headline_note`. Avila's water, HV and class layers are still blank (cause and fix in burn.md, not applied).
- **NASA OPERA comparison** ([docs/eda/opera.md](docs/eda/opera.md)): our open-water maps agree with OPERA DSWx-S1 on 97.5 % of pixels at Sevan and Tonle Sap (IoU 0.84, 20 place-dates); over 101 place-dates at 15 places 94 % of pixels but mean IoU 0.38. Wrong in our maps at Inner Niger (about four times OPERA's water), Lake Chad (about half) and Urmia (false water on a wet salt flat); weak at the deltas, Pantanal and Sudd. The 97–99 % balanced accuracies were measured only on certain pixels. Say "agree with"; never "better than NASA".
- **App**: three designs on the same data, 37 places in 9 classes (Floods 8, Lakes 4, Wetlands and deltas 4, Farmland 2, Wildfires 9, Earthquakes 6, Volcanoes 2, Landslides 1, Storms 1; `src/app/public/data/summary.json`): `src/app/` (A, satellite globe), `src/app2/` (B, story mode) and the merged `src/app3/` (story, a "How we found it" stepper per kind, before/after compare, caution labels). The front-end code lives on branch `front_harut` and is deliberately not merged to `main` yet; no live URL; no design is declared final here. The data contract gained `defaultDate`, `caution`, `evidence`, `burn` and `in_app` (`scripts/ingest/export.py`, `optimize.py`). Calibrated Tonle Sap is in as `tonle-sap-cal` (open water 2,340 to about 5,660 km² on 17 Oct; the study quotes 5,625 inside the preview footprint). Data folder 65.7 MB with 25 places and thumbnails ([data-size.md](docs/eda/data-size.md)); 81 MB with 37 (`du -sm`, 15 Nov). Brief for choosing: [docs/pitch/app-choice.md](docs/pitch/app-choice.md). The `demo` place is gone from the app data.
- **Tests**: 243 passed in 34 files, 15 November about 06:20 (242 at 03:50, 223 at 00:46) ([docs/evidence/pytest-full.txt](docs/evidence/pytest-full.txt)); the app's Node tests (`cd src/app && npm test`): 22, 22 pass.
- **Stories**: Pantanal and Lake Chad added; story text for ten places corrected against current numbers (Ararat open water is about 0.3 km²).
- **Pitch files** in `docs/pitch/`: deck (.pptx, .pdf), `FACTS.md`, `deck-audit.md`, `demo-script.md`. The deck predates fusion v2, the calibrated run and the earthquakes.
- **Server access**: scripts read `SERVER` and `SSH_KEY` from the environment; no defaults remain.

## Still open at 06:30

- Ararat Valley coherence layers in the app (not finished; do not quote numbers beyond [insar.md](docs/eda/insar.md)).
- The merged app `src/app3/` (branch `front_harut`).
- Event-hunt round 3 has ended (about 06:20; commit `6e3330c`); `docs/eda/events.md` has the final log.

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
.venv/bin/python -m pytest tests -q                  # 243 tests, about 80 seconds
.venv/bin/python -m scripts.detect.quake list        # earthquake events
```
