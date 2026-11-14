# Handoff — third session, 14 November 2026

Demo area removed from the web app; the link from backend to frontend tested. This adds to [HANDOFF.md](HANDOFF.md), which describes the state after the second session and is unchanged. Where the two disagree, this file is the newer one.

All of this is uncommitted on the `frontend` branch, on Arameh's laptop only.

## What changed

| File | Change |
|---|---|
| `src/app/public/data/demo/` | Deleted |
| [src/app/public/data/index.json](src/app/public/data/index.json) | Now `{"areas": []}`. The app shows "No study areas published yet." |
| [scripts/ingest/make_fixture.py](scripts/ingest/make_fixture.py) | No longer exports into the web app. `main(root)` writes contracts A and B under `root` only |
| [tests/test_export.py](tests/test_export.py) | Runs the fixture and the export in a temporary directory |
| [scripts/ingest/export.py](scripts/ingest/export.py) | A dated layer in `layers.json` with no images is left out of the manifest. Before, the app crashed on its empty `dates` |
| [src/app/src/data.js](src/app/src/data.js) | New. Holds `frame` and `nearest`, moved out of `App.jsx` so the tests can import them |
| [src/app/src/Charts.jsx](src/app/src/Charts.jsx) | The `<title>` of a chart point is now one string. React warned on every point |
| [src/app/package.json](src/app/package.json) | New script: `npm test` |

The `demo` area still exists as test input: `areas/demo/area.json` is in the repository, and `make_fixture.py` still makes contracts A and B for it. It is never published.

## New tests

| File | Checks |
|---|---|
| [tests/test_contract_c.py](tests/test_contract_c.py) | The shape of the manifest, images and `timeseries.json` against what the app reads. The demo is absent from the real app. The fixture does not write into the real app |
| [src/app/tests/frontend.test.js](src/app/tests/frontend.test.js) | `frame` and `nearest`, the chart and the timeline strip drawn from contract C data, no demo references in the source |
| [src/app/tests/connection.test.js](src/app/tests/connection.test.js) | Runs the real export, builds the real app into a temporary directory, serves it, and fetches every address the app requests under the GitHub Pages path |

Run them:

```bash
.venv/bin/python tests/test_contract_c.py
cd src/app && npm test
```

Result: `test_export`, `test_pipeline` and `test_contract_c` pass. `npm test`: 8 pass, 0 fail, 1 skipped. `npx oxlint` and `npm run build` pass.

## Not verified

- **No test has seen the page draw an area.** The headless Chrome check in `connection.test.js` is skipped: Chrome ran past 60 seconds and the area name never appeared. The cause was not found. To try again, remove `skip` from the third `test()` call.
- **The chart series and the facts are untested on backend output.** The fixture writes `series: []` and no `facts`. The app side was tested with hand-made data only.

## Two files changed by someone else

[src/app/src/MapView.jsx](src/app/src/MapView.jsx) (the map is built only when near the screen or when the browser is idle) and [src/app/vite.config.js](src/app/vite.config.js) (MapLibre in its own chunk) changed during the session. This session did not make those changes. All tests pass with them in. Review them before the commit.

## Left alone on purpose

The invented regions and events in `regions.js` and the "Sample data" tag. See "The weather events are invented" in [HANDOFF.md](HANDOFF.md).

## What HANDOFF.md now gets wrong

- It says `make_fixture.py` generates the `demo` area for the app. It now makes test input only.
- It says the demo area is on screen with HH and HV images. No area is published; the radar section shows "No study areas published yet."
- It says `MapView.jsx` is unchanged. See "Two files changed by someone else".
- Its list of checks lacks `tests/test_contract_c.py` and `npm test`.

## Next steps, in order

1. Review `MapView.jsx` and `vite.config.js`, then commit everything on `frontend` and push it.
2. Open a pull request from `frontend` to `main`. The deploy workflow runs on that push.
3. Someone with a NASA Earthdata login runs the first real extract and export.
4. When the first real area is exported, open the page in a browser and check that it draws.
