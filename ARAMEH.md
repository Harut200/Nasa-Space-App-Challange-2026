# Arameh — app notes and requests

Written Saturday 14 November, 16:00 (Yerevan). Deadline: **Sunday 15 November, 23:59**; we aim to submit by 21:00.

This collects everything about the web app that came out of the detection work today: what was changed in your code, what the data now contains, what is coming, and what we recommend. Nothing here is merged to `main` yet — it is all in pull request 3.

## 1. First: review pull request 3

**https://github.com/Harut200/Nasa-Space-App-Challange-2026/pull/3** (`detection` → `main`, draft)

It brings the detector your pipeline was waiting for (`scripts/detect/run.py`), real data for the app, and changes to your app code. Merging it to `main` is what deploys, so it needs your eyes first.

To try it:

```bash
git fetch && git checkout detection && git pull
cd src/app && npm install && npm run dev
```

### What was changed in your files

| File | Change | Why |
|---|---|---|
| `src/app/src/App.jsx` | New legend type for categorical layers; notice bar when data is a preview; header, side panel, phone layout; date label on the map; Play plus previous/next; status line for whole-season layers; placeholder when an area has no story | Real layers needed class legends; app had no visual design yet |
| `src/app/src/Charts.jsx` | Timeline is HTML buttons instead of SVG, with month labels and the outage labelled; chart reads its unit from the data | Readable on a phone and a projector; previews are not in dB |
| `src/app/src/MapView.jsx` | Map starts on a plain dark background and shows the radar image at once; basemap loads separately with a 4.5 s limit and slides in underneath; note shown if it fails | Before, the radar image waited for the online basemap. On bad Wi-Fi the map stayed blank |
| `src/app/src/index.css` | Full dark design in plain CSS, breakpoint at 900 px | — |
| `scripts/ingest/export.py` | Three lines: if `products/source.json` exists, its `credits` and `source` go into the manifest | Preview data must not be credited as calibrated. Output is unchanged without that file; your tests still pass |
| `areas/tonle-sap/area.json` | Box widened to `[103.4, 11.8, 105.2, 13.6]`, `pixel_m` 80 | The flood starts in the river plain south-east of the lake, outside the earlier box |

No libraries were added. The date slider was replaced by per-pass buttons; if you prefer the slider, say so.

### Additions to contract C (all backward compatible)

- `legend`: new type `{"type": "classes", "items": [{"color": "#2f6fde", "label": "Open water"}]}`.
- `timeseries.json`: optional top-level `"unit"` (`"dB"` or `"relative brightness (0–100, uncalibrated)"`).
- `manifest.json`: optional `"source"` (`"preview"` or `"gamma0"`). The preview notice shows only for `"preview"`, so it disappears by itself when calibrated data arrives.

If you agree, `plan/00-contracts.md` should be updated to say so.

## 2. What the data folder holds now

`src/app/public/data/`, about 10 MB: `tonle-sap`, `sevan`, `ararat`, `demo`.

Layers per area, in this order (the first is the default):

| id | Label | Dated | Legend |
|---|---|---|---|
| `hh` | HH brightness (preview) | yes | ramp |
| `water` | Water and flooded vegetation | yes | classes |
| `when` | When it changed for good | no | classes, one item per date |
| `classes` | Season classes | no | classes |
| `change` | Change since first pass (HH) | yes, from the 2nd date | diverging |
| `rgb` | Three-date composite | no | text |
| `hv` | HV brightness (preview) | yes | ramp |

Each area has story text, five or six spots, a time series per spot, and two facts per date (open water, flooded vegetation, in km²).

To regenerate after anything changes (for example Abel editing `areas/*/story.json`):

```bash
.venv/bin/python scripts/detect/run.py tonle-sap && .venv/bin/python scripts/ingest/export.py tonle-sap
```

Note: running `tests/test_export.py` or `tests/test_run.py` rewrites the `demo` area with the plain fixture. Re-run the two commands above for `demo` afterwards, or drop `demo` (see 4.1).

## 3. Please test by hand — not verified by anyone yet

Everything was checked with screenshots from headless Chrome. These were not:

- [ ] **Play**: watch the animation run through all ten dates.
- [ ] **Clicking**: pass buttons on the timeline, dots on the chart, spot markers.
- [ ] **Compare mode**: drag the divider with a mouse and with a finger.
- [ ] **Offline**: turn Wi-Fi off, reload, then change area, date and layer and toggle compare. The radar image should stay; a note should say the basemap is unavailable.
- [ ] **Slow basemap**: the case where the basemap arrives just before the 4.5 s limit, or while you are interacting.
- [ ] **A real phone**, and a **1280 × 720** window (projector).
- [ ] The `change`, `rgb` and `hv` layers, and the Ararat area — never screenshotted.
- [ ] Keyboard: arrow keys do not step through dates since the slider went. Worth adding back.

Known cosmetic issues: the label "Usually water, not today (wind?)" wraps to two lines in the narrow legend; the footer credits wrap on desktop; the two long labels in the season-classes legend are wordy.

## 4. Recommended changes, in order

### 4.1 Before submission (must)

1. **Remove `demo` from `index.json`** — the contract says so, and a synthetic area on a judged site looks unfinished.
2. **About dialog**: add the AI-use statement (required by the rules), and credits for every dataset used: NISAR via ASF DAAC; JRC Global Surface Water (Pekel et al. 2016, "Source: EC JRC/Google") if validation is mentioned; Copernicus DEM if the terrain mask ships.
3. **Repository Settings → Pages → Source → GitHub Actions**, then merge. Check the live URL on a phone.
4. `npm run preview` serves `dist/`, a copy made at build time. Rebuild after data changes.

### 4.2 Show numbers honestly (should — small, high value)

The detection work found that the water area depends strongly on one threshold. A bare "3,836.7 km²" claims a precision we do not have.

- **Show areas with their range**, for example "≈ 3,500 km² (2,000–4,800)". We will add optional `"lo"` and `"hi"` arrays next to `"values"` in each entry of `facts`. Please render them when present and fall back to the single value otherwise.
- **Round** to the nearest 10 or 100 km². No decimals.
- **Draw the area over time**: a small chart of open-water area per date with the range as a shaded band and the outage marked. At the moment only the current date's number is shown, so the main finding — almost no growth until August, then a steady rise — is invisible unless the user scrubs.

### 4.3 New layers coming from detection (should — needs small legend work)

These will arrive in `manifest.json` through the same contract. Most need nothing new from you; the ones that do are marked.

| Layer | What it is | Needs from the app |
|---|---|---|
| Significant change | Replaces "When it changed for good". Pixels whose change is statistically significant (1 % false-discovery rate), coloured by the date of the largest change | Nothing: `classes` legend. Suggested caption: "Significant radar change (1 % false-discovery rate)" |
| Number of changes | How many times each pixel changed | Nothing: `classes` legend |
| Water confidence | Probability that a pixel is water, 0–1 | Nothing if exported as a ramp. Better: use it as the opacity of the water layer |
| Flood arrival day | The day each place went under water, at day resolution, including an estimate inside the outage | **A ramp legend with dates as end labels** instead of numbers, and ideally a hatched marker for the outage range |
| Floodplain height from flood order | Relative height read from the order of flooding | Nothing: ramp |
| Choreography map | One image: colour = when it changed, strength = how much | **A colour-wheel or two-axis legend.** A static legend image supplied in the manifest would be the cheapest way: `{"type": "image", "src": "…"}` |

### 4.4 More places (should — affects layout)

Eight to twelve more places around the world are being added (floods, wetlands drying, forest loss, a shrinking lake). The row of area buttons in the header will not hold fifteen names.

- Replace the button row with a **dropdown or a grouped list** (by continent or by kind of change), or a small world map with markers as the start screen.
- **Load data per area on demand.** With many areas the data folder may reach 40–60 MB; the app should fetch only the selected area's images and preload that area's dates for smooth play.
- Keep the shareable link working for the new ids.

### 4.5 Polish you listed yourself (could)

- Cross-fade between frames (currently a hard cut). Respect reduced-motion.
- Satellite basemap toggle, once a provider's terms are checked.
- Favicon and social preview image — the "when it changed" map of Tonle Sap makes a good one.
- Shareable link could also carry compare mode and the right-hand date.

## 5. Known problems in the data you will see

- **False water around Lake Sevan and in the Ararat Valley** — dark mountain slopes and radar shadow classed as water. A terrain mask is being built to remove it. Until it lands, the water layer in the two Armenian areas is wrong away from the lake.
- **Tonle Sap preview** has a small dark mark near the top-left that is burned into the source image.
- **Slanted transparent edges** are the satellite's footprint, not a bug.
- **Everything is from preview images**: about 180 m per pixel, not calibrated. The app says so.

## 6. Pipeline notes

- `scripts/detect/run.py` exists now, so `scripts/build_area.sh` can run end to end. It has never been run on a real calibrated file: nobody on the team has a NASA Earthdata login yet. Your handoff note asked who runs the real extraction — still open. Whoever registers first (five minutes at https://urs.earthdata.nasa.gov/) unblocks it.
- `search.py` not filtering by acquisition mode: still true. The new `scripts/ingest/add_area.py` (in progress) picks a track and frame with one consistent mode automatically.
- A one-command way to add a place is being written: `scripts/ingest/add_area.py <id> --name "…" --bbox W S E N`.

## 7. Where to read more

On the `detection` branch:

- [README.md](https://github.com/Harut200/Nasa-Space-App-Challange-2026/blob/detection/README.md) — rewritten for judges
- [docs/ARCHITECTURE.md](https://github.com/Harut200/Nasa-Space-App-Challange-2026/blob/detection/docs/ARCHITECTURE.md) — how the detection parts fit together
- [docs/DETECTION.md](https://github.com/Harut200/Nasa-Space-App-Challange-2026/blob/detection/docs/DETECTION.md) — every formula, parameter and result
- [docs/eda/validation.md](https://github.com/Harut200/Nasa-Space-App-Challange-2026/blob/detection/docs/eda/validation.md) — check against 38 years of Landsat water maps
- [docs/design/screenshots/](https://github.com/Harut200/Nasa-Space-App-Challange-2026/tree/detection/docs/design/screenshots) — how the app looks now

If any change to your code is not what you want, say so and it will be reverted or redone your way. The app is yours.
