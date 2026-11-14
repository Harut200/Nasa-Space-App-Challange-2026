# Handoff — 14 November 2026

State of the work after the first session. Read [plan/README.md](plan/README.md) and [plan/00-contracts.md](plan/00-contracts.md) first if you have not.

## Where things are

| Branch | State | On GitHub? |
|---|---|---|
| `main` | Plans only. No code | Yes |
| `backend` | Data pipeline scripts and tests. 5 commits | Branch exists, **commits not pushed** |
| `frontend` | Working app with no visual design. Contains all `backend` commits plus 1 | Branch exists, **commits not pushed** |
| `detection` | Empty, same as `main` | Yes |

Nothing is merged to `main`, so nothing is deployed. The commits exist only on Arameh's laptop until someone pushes them.

## Next steps, in order

1. Push `backend` and `frontend`.
2. Merge `backend` to `main` by pull request. This puts the `demo` area on `main`, which unblocks the other two branches.
3. Repository owner: Settings → Pages → Source → **GitHub Actions**. Then merge `frontend` to `main`; the deploy workflow runs on that push.
4. Start the visual design (see "Frontend" below).
5. Someone with a NASA Earthdata login runs the first real extract (see "Not verified").

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

Needs Node 20.19 or newer. Stack: Vite, React, MapLibre GL JS 6. No other libraries.

| File | Holds |
|---|---|
| [src/app/src/App.jsx](src/app/src/App.jsx) | All state and every control |
| [src/app/src/MapView.jsx](src/app/src/MapView.jsx) | One map with one radar image and the spot markers |
| [src/app/src/Charts.jsx](src/app/src/Charts.jsx) | Timeline strip and backscatter chart, both inline SVG |
| [src/app/src/index.css](src/app/src/index.css) | Seven lines, layout only |
| [.github/workflows/deploy.yml](.github/workflows/deploy.yml) | Deploy to GitHub Pages on push to `main` |

Working: area buttons, timeline slider, play and pause, the instrument gap drawn on the timeline, layer buttons with legend, compare mode (two synced maps with a divider), chart, spot markers, story panel, About dialog, credits, and a link that restores area, date and layer.

Checked by driving the built app in headless Chrome through every feature, at desktop size and at 375 px wide, with no console errors. That script is not in the repository.

Left out on purpose: cross-fade between frames (it cuts), the satellite basemap toggle (the provider's terms are not checked), favicon and social preview image.

### For whoever does the design

All styling goes in `index.css`; there is nothing to undo. Hooks already in the markup: `header`, `nav`, `main`, `footer`, `section[aria-label="…"]`, `.maps`, `.map`, `.strip`, `.chart`, `.legend-bar`, and `[aria-pressed='true']` on the active button.

Three things must survive:

- `.maps .map { position: absolute; inset: 0 }` — MapLibre's own stylesheet overrides a weaker selector and the map collapses to zero height.
- `.maps` needs a height.
- The compare divider is an inline `clip-path` on the second map.

The plan asks for a dark interface, the story as a side panel on desktop and a bottom sheet on phones, and text readable on a 1280×720 projector.

**The demo area only has HH and HV images.** Story text, chart, spots, the change layer and the composite layer are empty until detection delivers them, so those panels show nothing in the app today. To design them, add a richer fake area to `make_fixture.py` first.

### Two traps already hit

- MapLibre 6 has no default export: `import * as maplibregl from 'maplibre-gl'`.
- The bundler moves MapLibre's worker file, so `MapView.jsx` imports it with `?url` and calls `setWorkerUrl`. Without that the map stays blank and the console says "Worker failed to load".

## Detection (`detection` branch)

Not started. Inputs it can use today: the `demo` area in contract A form, made by `make_fixture.py`. Its outputs must match contract B. `tests/test_pipeline.py` contains a small grey stand-in for this step that shows the expected file shapes.
