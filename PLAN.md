# Project Plan — "Dancing with the SARs"

NASA Space Apps Challenge 2026 · hackathon window **14–15 November 2026** (two days; judging starts 16 November).

**What we ship:** a public, open-source web app where anyone can scrub through a season of NISAR L-band radar images and watch one piece of Earth's surface change, with the physics explained in plain language next to the map.

**Step-by-step work plan, split into three branches (frontend, backend, detection) plus pitch and repository:** [plan/](plan/README.md). Where the two differ, `plan/` is newer.

Facts in this plan were checked against the ASF NISAR Data User Guide on 14 November 2026. Items we could not check are marked **(verify)**.

---

## 1. Three decisions that shape everything

1. **Seasonal rhythms, not news events.** We pick places where change is guaranteed every year between June and October (flood pulse, dry-season forest clearing, crop cycle). No dependence on a disaster happening to fall inside the data window.
2. **One consistent data stack.** NISAR data comes in three calibration tiers that should not be mixed (see section 3). We use the PROVISIONAL tier, 17 June – 23 October 2026: about ten passes per track at the 12-day repeat, enough for a real time series.
3. **No backend, no tile server.** Python turns each radar pass into one small image per study area. The app is static files on GitHub Pages. Nothing can go down during judging.

## 2. Study areas

Finish the first one end to end before starting the second.

| Order | Area | What dances | Why radar sees it |
|---|---|---|---|
| 1 (must) | **Tonle Sap, Cambodia** — or Bangladesh haor wetlands as substitute | Lake and flooded forest expand several-fold from June to October | Open water is smooth and returns almost nothing (dark). Flooded forest is the opposite: L-band passes through the canopy and bounces between water and trunks (very bright in HH) |
| 2 (should) | **Amazon deforestation frontier** (Rondônia or southern Pará) | New clearings appear through the July–October dry season | HV measures scattering from branches and trunks. Remove the forest and HV drops sharply |
| 3 (should, short) | **Ararat Valley and Lake Sevan, Armenia** | Fields green up and are harvested; Sevan shoreline shifts | Crop structure and soil moisture change HV and HH field by field. Our home ground — the personal story the challenge asks for |
| Stretch | Ground deformation from a GUNW interferogram | Subsidence or an earthquake | Phase difference between two passes measures centimetre-scale motion |

**Coverage gate (first hour):** an area stays only if ASF Vertex shows at least six PROVISIONAL GCOV passes on the same track and frame, in the same acquisition mode. If area 1 fails the gate, swap to its substitute; do not spend more than 30 minutes deciding.

## 3. Data — what exists today

Full notes and links: [docs/research/data-sources.md](docs/research/data-sources.md).

| Tier | Acquired | Use for us |
|---|---|---|
| VALIDATED | from 21 Oct 2026 (released 6 Nov) | Only two or three passes so far. Skip, or use as a final "latest" frame with a caveat |
| **PROVISIONAL** | **17 Jun – 23 Oct 2026** | **Our stack.** Calibrated, validated at limited sites |
| BETA | 17 Oct 2025 – 20 Jan 2026, up to 8 passes per track | Not calibrated. Avoid; never difference against other tiers |

- **Hole in the record:** no data at all from 27 July to 10 August 2026 (instrument gap). The timeline in the app must show this as a gap, not interpolate across it.
- **Product:** Level-2 **GCOV** — terrain-corrected backscatter, already on a map grid, HDF5 format, 10/20/80 m pixel spacing depending on mode. Polarizations typically HH and HV over land.
- **Optional extras:** GUNW (80 m unwrapped interferogram) for the stretch goal; SME2 soil moisture (200 m, provisional) as a context layer for Armenia.
- **Access:** free NASA Earthdata Login, then the `asf_search` Python package or ASF Vertex. Every team member creates the login in the first ten minutes.
- **Declared fallback:** if NISAR passes are too few somewhere, add Sentinel-1 (C-band) and present the L-band versus C-band difference as a finding. The challenge explicitly invites other sensors.

## 4. What the app does

| # | Feature | Level |
|---|---|---|
| 1 | Map with study-area buttons that fly to each place | Must |
| 2 | Timeline scrubber plus play button stepping through the passes, with the July–August gap drawn | Must |
| 3 | Before/after swipe between any two dates | Must |
| 4 | Change layer: what got brighter, what got darker between two dates | Must |
| 5 | Story panel per area: what you see → what is physically happening → why L-band shows it → why it matters to people | Must |
| 6 | HH / HV / colour-composite toggle | Should |
| 7 | Chart of average backscatter over time for a few named spots (lake, forest, field) | Should |
| 8 | Optical satellite basemap toggle for orientation | Should |
| 9 | Link that restores area, date and layer | Could |

Not building: user-drawn areas, live processing, accounts, global coverage, our own interferometry.

## 5. Pipeline (Python, run once per area)

```
scripts/
  01_search.py      # asf_search → list of GCOV passes for one area; writes manifest draft
  02_extract.py     # open each HDF5, cut the area, HH + HV → dB GeoTIFF on a common grid
  03_products.py    # speckle filter, log-ratio change, 3-date RGB composite, HH/HV composite
  04_export.py      # reproject to Web Mercator, colour, write WebP + bounds per date
  05_timeseries.py  # mean dB per named polygon per date → JSON
```

- Layout inside a GCOV file is expected to be `/science/LSAR/GCOV/grids/frequencyA/HHHH` and `.../HVHV`, with x/y coordinates and a projection dataset alongside **(verify on the first real file with `h5py` before writing anything else)**.
- Prefer cutting the area out while streaming (xarray + fsspec over HTTPS) so we never store whole granules. If streaming is slow from our network, download two files, time it, then decide.
- Backscatter display: convert power to decibels with `10·log10`, show roughly −25 to −5 dB in grey.
- Change between two dates: difference of the dB images. Blue for darker (new water, cleared forest), red for brighter (flooded vegetation, growth), transparent near zero. Smooth before differencing — speckle otherwise fills the map with false change.
- Three-date composite: earliest pass in red, middle in green, latest in blue. Grey means nothing changed; colour tells you when it changed.
- Output is one image per date per layer (about 2,000–3,000 pixels wide), placed on the map by its four corners. Must be exported in Web Mercator or it will not line up.
- Libraries: `asf_search`, `h5py`, `xarray`, `rioxarray`, `numpy`, `scipy`, `Pillow`. Work in `notebooks/` until a step works once, then move it to `scripts/`.

**Contract between pipeline and app** — agree in the first hour, then both sides work independently:

```
src/app/public/data/{area}/manifest.json
  { name, bounds, dates: [...], gaps: [[from, to]], layers: ["hh","hv","rgb","change"],
    story: {...}, spots: [{ id, label, lngLat }] }
src/app/public/data/{area}/{layer}/{YYYY-MM-DD}.webp
src/app/public/data/{area}/timeseries.json
```

## 6. App stack

Vite + React + MapLibre GL JS in `src/app/`. Each date is an image source; animation is cross-fading opacity between preloaded images. Swipe uses two synced maps. One small chart library. Dark interface so the radar images carry the page. Deploy by GitHub Actions to GitHub Pages on every push to `main`.

## 7. Schedule

Hours are from the moment hacking starts. The submission deadline is normally end of day Sunday local time **(verify with the local event lead and work back from it)**.

**Day 1**

| Hours | Data person(s) | App person(s) | Story person |
|---|---|---|---|
| 0–1 | Earthdata logins. Coverage gate for area 1 in Vertex | Vite app with a map, deployed live to Pages | Read NISAR white papers and StoryMaps for area 1 |
| 1–3 | Open one GCOV file, confirm layout, produce one dB image for area 1 | Load a manifest with placeholder images; fly-to; one overlay | Draft story for area 1 |
| 3–7 | Full date stack for area 1, all passes on a common grid | Timeline scrubber, play, gap marker | Pick named spots for the chart; find optical context |
| 7–10 | Change layer and RGB composite for area 1 | Swipe compare; layer toggle | Story text into manifest |
| **10** | **Checkpoint: area 1 works on the live URL with real data. If not, everyone fixes this before anything else.** | | |
| 10–12 | Coverage gate and first images for area 2 | Story panel layout, legend, colour scales | Draft story for area 2 |

**Day 2**

| Hours | Data | App | Story |
|---|---|---|---|
| 0–3 | Area 2 complete; time-series JSON for both | Chart; mobile and projector check | Armenia story, personal context |
| 3–5 | Area 3 (small extent, fewer layers is fine) | Polish, loading states, credits footer | Slides or video script |
| **5** | **Feature freeze. Nothing new after this.** | | |
| 5–8 | README, licence, data credits, how to reproduce | Bug fixes only; record backup screen capture | Record demo, finish slides, write project page |
| 8–9 | Submit with an hour to spare. Whole team reads the project page once before sending. | | |

Stretch work (deformation layer, shareable links) happens only if the hour-10 checkpoint on day 1 was met early.

## 8. Risks

| Risk | What we do |
|---|---|
| Too few same-track passes over an area | Coverage gate in hour one; named substitute; Sentinel-1 as declared supplement |
| GCOV files are large or slow to stream | Time one file first. Cut to the study area immediately. Keep extents near 100 km |
| File layout differs from what we expect | First task after login is opening one file and printing its tree |
| Passes in different modes or resolutions | Filter the search to one mode; resample everything to one grid in step 02 |
| Speckle makes the change layer noisy | Filter before differencing; mask small differences |
| Venue network fails during demo | App also runs from a laptop; backup recording made at feature freeze |
| Scope creep | The Must / Should / Could column decides. Area 1 complete beats three areas half done |

## 9. Submission checklist

- [ ] Read the official guide first thing: https://www.spaceappschallenge.org/resources/project-submission-guide/ — exact demo format, length limits and deadline **(verify; we could not load it while planning)**
- [ ] Team registered, all members joined the team and this challenge (team size limit reported as six — verify)
- [ ] Project page: summary, live demo link, public repository link
- [ ] Demo (slides or short video in the format the guide requires)
- [ ] List of NASA data and resources used; partner-agency data listed separately with its terms
- [ ] Any use of AI tools disclosed if the guide asks for it **(verify)**
- [ ] `LICENSE` (MIT) in the repository; README with screenshots and steps to reproduce
- [ ] Credits in the app footer: NASA/JPL-Caltech, ISRO, ASF DAAC

Judged on impact, creativity, validity, relevance and presentation. Our answers: floods, forests and food are things people depend on; the "dance" timeline is the creative hook; calibrated data from a single tier with the gap shown honestly is the validity; NISAR is the challenge's own dataset; the live URL and story panel are the presentation.
