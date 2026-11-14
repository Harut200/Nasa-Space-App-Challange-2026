# Handoff — 14 November 2026

State of the work after the second session (frontend design). Read [plan/README.md](plan/README.md) and [plan/00-contracts.md](plan/00-contracts.md) first if you have not.

## Where things are

| Branch | State |
|---|---|
| `main` | Has the `backend` and `frontend` pull requests merged (the unstyled app). `origin/main` is one commit ahead of the local copy: `ABEL.md` |
| `backend` | Data pipeline scripts and tests. Pushed and merged |
| `frontend` | **The new design is here, uncommitted, on Arameh's laptop only.** See "Frontend" below |
| `detection` | Not started. 27 commits behind `main` |

## Next steps, in order

1. Commit the design work on `frontend` and push it.
2. Try it by hand in a real browser and on a phone (see "Not verified" under Frontend).
3. Replace the placeholder regions and weather events in [src/app/src/regions.js](src/app/src/regions.js). Arameh will supply the list.
4. Open a pull request from `frontend` to `main`. The deploy workflow runs on that push.
5. Someone with a NASA Earthdata login runs the first real extract (see "Not verified" under Backend).

## Backend (`backend` branch)

Set up: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`

| File | Does | Checked how |
|---|---|---|
| [scripts/ingest/search.py](scripts/ingest/search.py) | Finds NISAR passes for an area, writes `data/raw/{area}/passes.json`. With empty track/frame it prints the coverage table instead | Ran against the real ASF service |
| [scripts/ingest/extract.py](scripts/ingest/extract.py) | Cuts each pass to the area, writes contract A | Synthetic files only |
| [scripts/ingest/export.py](scripts/ingest/export.py) | Contract B → web images and manifest (contract C) | Synthetic files only |
| [scripts/ingest/make_fixture.py](scripts/ingest/make_fixture.py) | Generates the `demo` area | Runs in the tests |
| [scripts/build_area.sh](scripts/build_area.sh) | Runs the four steps in order | **Never run** — needs `scripts/detect/run.py` |

Run the checks:

```bash
.venv/bin/python tests/test_export.py
.venv/bin/python tests/test_pipeline.py --net    # --net also runs the real ASF search
.venv/bin/python scripts/ingest/extract.py --selftest
```

All three pass.

**Coverage gate result for Tonle Sap: passed.** Track 83, frame 8, ascending, 10 passes from 19 June to 17 October 2026, HH and HV, one acquisition mode, the box fits one frame. Saved in [areas/tonle-sap/area.json](areas/tonle-sap/area.json). Each source file is about 1.9 GB.

Real ASF property names, which the plan left open: `pathNumber` (track), `frameNumber`, `flightDirection` (upper case), `rangeBandwidth`, `mainBandPolarization`, `startTime`, `sceneName`, `url`.

### Not verified

- **`extract.py` has never opened a real NISAR file.** There is no Earthdata login on this laptop. It follows the file layout written in [plan/02-backend.md](plan/02-backend.md) step B4. The open points are listed in a comment at the top of the file: the type of `projection`, what no-data looks like, what `mask` means, whether the data is linear power. Do step B4 (open one file, print its tree) before trusting it.
- **Streaming over the network** is untested for the same reason.
- **`search.py` does not filter by acquisition mode.** The contract's `area.json` has no field for it. This is safe for track 83 and wrong for a track that mixes modes (track 76 does). Fixing it means adding a field to the contract, which needs all three owners to agree.

### Open question

Arameh said the real data is not their job. The plan gives `extract.py` to the backend owner, and detection cannot start on real data until someone runs it. The team needs to decide who does.

## Frontend (`frontend` branch)

```bash
cd src/app && npm install && npm run dev
```

Open `http://localhost:5173/Nasa-Space-App-Challange-2026/`. Needs Node 20.19 or newer. Stack: Vite, React, MapLibre GL JS 6. No other libraries; the globe is MapLibre's own globe projection.

`npx oxlint` and `npm run build` both pass.

### What the page is now

The design follows the Nothing X app: black and grey, hairline borders, cyan and red as the only accent colours, a serif for headings, a dot-matrix font for numbers.

1. **Hero (top, full screen):** an interactive globe. Above it, a row of region tabs. Clicking a tab or a pin on the globe flies in until the planet fills the screen and runs past its edges; the hero clips it, so it never covers the section below. A forecast panel (right side on desktop, bottom sheet on phones) lists the predicted weather events for the region. A switcher in the bottom-left swaps Earth for the Moon, Mars or Mercury.
2. **Radar record (below):** the app from the first session, restyled. Every feature still works: area buttons, layers and legend, timeline, play, compare, chart, story, About dialog, and the link that restores area, date and layer.

| File | Holds |
|---|---|
| [src/app/src/App.jsx](src/app/src/App.jsx) | All state, the hero overlay (tabs, forecast panel, planet switcher) and the radar section |
| [src/app/src/Globe.jsx](src/app/src/Globe.jsx) | The globe: map style, rotation, fly-in, pins, planet swap |
| [src/app/src/globe.css](src/app/src/globe.css) | Globe backdrop and pin styles |
| [src/app/src/planets.js](src/app/src/planets.js) | The planet list: name, tile address, tint, imagery credit |
| [src/app/src/regions.js](src/app/src/regions.js) | Regions and their weather events. **Data only — edit this to change the list** |
| [src/app/src/index.css](src/app/src/index.css) | Colour and font variables, and every other style |
| [src/app/src/MapView.jsx](src/app/src/MapView.jsx) | The radar map. Unchanged |
| [src/app/src/Charts.jsx](src/app/src/Charts.jsx) | Timeline strip and chart. Colours changed only |
| `src/app/public/fonts/` | Instrument Serif, Newsreader, Doto, JetBrains Mono. All SIL Open Font License, served from the repository |

### The weather events are invented

Every region and event in `regions.js` is a placeholder written to design against. The forecast panel shows a "SAMPLE DATA" tag for this reason. Do not remove the tag until real predictions replace the data. There is no forecast source or model behind this panel yet; the team has to decide where predictions come from.

A region is `{ id, name, lngLat: [lng, lat], events: [{ type, severity: 'high' | 'low', from, to, probability, summary }] }`. An optional `zoom` overrides the fly-in zoom, which is otherwise worked out from the screen size.

### How the globe behaves

- It rotates slowly when nothing is selected. Rotation stops for 4 seconds after any touch and stays off when the system asks for reduced motion.
- The mouse wheel zooms only after a click on the globe, and stops when the pointer leaves it. This keeps page scrolling free. Drag and pinch always work.
- Clicking empty globe, zooming far out, the close button, or the "Globe" tab returns to the whole-planet view.
- `App.jsx` tells the globe how many pixels the overlay covers on each side (`padding`), so the selected point lands in the middle of the free space. If you move or resize the overlay, change those numbers.

### Not verified

- **Only headless Chrome with software rendering has run this**, at 1280×720 and 375×812. No real graphics card, no Safari, no Firefox, no real phone.
- **Wheel zoom and touch gestures were never exercised** by a real mouse or finger.
- Text contrast was judged by eye, not measured.

### Known limits

- **The globe needs a network.** Earth tiles come from OpenFreeMap; Moon, Mars and Mercury come from OpenPlanetaryMap (NASA imagery, public domain). With no connection the globe is blank. This breaks the plan's "runs offline from a laptop" check for the hero. The radar map already had the same limit.
- The Armenian pins sit close together after the fly-in, because the cities are about 100 km apart. Look again once the real region list is in.
- Pins on the far side of the globe are invisible but still clickable.
- Venus and Jupiter are missing: no tile service was found for them.
- On phones the "no data (instrument)" label on the timeline is small.
- The demo area still has only HH and HV images, so the chart, story and facts are empty on screen. They are styled; they were checked with fake data fed in by the test script.
- Still left out from the first session: cross-fade between frames, the satellite basemap toggle, the social preview image.

### Traps already hit

- MapLibre 6 has no default export: `import * as maplibregl from 'maplibre-gl'`.
- The bundler moves MapLibre's worker file, so `MapView.jsx` and `Globe.jsx` import it with `?url` and call `setWorkerUrl`. Without that the map stays blank and the console says "Worker failed to load".
- **Do not set `position` on `.pin`.** MapLibre positions markers absolutely; overriding it puts the pins in page flow and each one lands further from its real place.
- `.maps .map { position: absolute; inset: 0 }` must stay, `.maps` needs a height, and the compare divider is an inline `clip-path` on the second map.
- The screenshot script used for checking is not in the repository.

## Detection (`detection` branch)

Not started. Inputs it can use today: the `demo` area in contract A form, made by `make_fixture.py`. Its outputs must match contract B. `tests/test_pipeline.py` contains a small grey stand-in for this step that shows the expected file shapes.
