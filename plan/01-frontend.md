# Branch `frontend`

**You own:** `src/app/` (except `src/app/public/data/`, which backend writes) and `.github/workflows/`.
**You receive:** contract C in [00-contracts.md](00-contracts.md).
**You deliver:** the live site at `https://harut200.github.io/Nasa-Space-App-Challange-2026/`.

Stack: Vite + React + MapLibre GL JS. No router, no state library, no component kit, no chart library. Node 20.19 or newer.

The app is finished when a stranger can open the URL on a phone, press play, and understand what changed and why.

## Hours 0–1: empty map, live

**F1. Scaffold.**

```bash
git checkout frontend
git rm src/app/.gitkeep
cd src && npm create vite@latest app -- --template react
cd app && npm install && npm install maplibre-gl
```

**F2. Set the base path.** In `src/app/vite.config.js` add `base: '/Nasa-Space-App-Challange-2026/'` (the repository name, with both slashes). Build every data URL as `import.meta.env.BASE_URL + 'data/...'`. An absolute `/data/...` works on your laptop and breaks on Pages.

**F3. Show a map.** One full-screen MapLibre map with a dark basemap that needs no API key. Try the OpenFreeMap style `https://tiles.openfreemap.org/styles/dark` **(verify it loads; if not, use `.../styles/liberty`)**. Import `maplibre-gl/dist/maplibre-gl.css` or the map renders broken.

**F4. Deploy.** Add `.github/workflows/deploy.yml`:

```yaml
name: Deploy
on:
  push:
    branches: [main]
permissions:
  contents: read
  pages: write
  id-token: write
concurrency:
  group: pages
  cancel-in-progress: true
jobs:
  deploy:
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    defaults:
      run:
        working-directory: src/app
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: npm
          cache-dependency-path: src/app/package-lock.json
      - run: npm ci
      - run: npm run build
      - uses: actions/upload-pages-artifact@v3
        with:
          path: src/app/dist
      - id: deployment
        uses: actions/deploy-pages@v4
```

Merge to `main`. This needs hour-0 step 5 (Pages source = GitHub Actions) to be done.

**Done when:** the live URL shows the map on your phone.

## Hours 1–3: one overlay from a manifest

**F5. Load data.** Merge `main` to get the `demo` area. On start, fetch `data/index.json`, then every `data/{area}/manifest.json`. Keep three pieces of state: `area`, `dateIndex`, `layer`.

**F6. Area buttons.** One button per manifest. Click → `map.fitBounds([[west, south], [east, north]], { padding: 40 })`.

**F7. Overlay.** For the current area, one image source and one raster layer:

```js
const [w, s, e, n] = manifest.bounds
map.addSource('sar', {
  type: 'image',
  url: frameUrl,                                  // data/{area}/{layer}/{date}.webp
  coordinates: [[w, n], [e, n], [e, s], [w, s]],  // top-left, top-right, bottom-right, bottom-left
})
map.addLayer({ id: 'sar', type: 'raster', source: 'sar', paint: { 'raster-fade-duration': 0 } })
```

To change the frame: `map.getSource('sar').updateImage({ url: newUrl })`. For a layer with `dates: null`, the URL is `data/{area}/{layer}.webp`.

**Done when:** the demo disc sits on the map and area buttons fly between areas. At hour 3, the first real Tonle Sap date replaces it with no code change.

## Hours 3–7: timeline

**F8. Scrubber.** `<input type="range" min=0 max={dates.length-1}>` bound to `dateIndex`. It is keyboard-accessible for free. Show the selected date in words ("14 July 2026").

**F9. True-time strip.** Above the range, a thin SVG: one tick per pass placed by real date, and a shaded band for each entry of `manifest.gaps`, labelled "no data (instrument)". Passes are not evenly spaced and the July–August hole must be visible: this is part of our validity claim.

**F10. Play.** A play/pause button that advances `dateIndex` every 800 ms and loops. Before playing, preload every frame of the current layer (`new Image().src = url`) so playback does not flicker.

**F11. Smooth transition (only if F8–F10 are done).** Two image sources, A and B; load the next frame into the hidden one, then swap `raster-opacity`. Skip if short on time; a hard cut is acceptable.

**Done when:** pressing play on area 1 shows the lake grow, with the gap drawn.

## Hours 7–10: swipe, layers, story

**F12. Layer toggle.** One button per `manifest.layers` entry. If the chosen layer lacks the current date, show the nearest date it has and say so.

**F13. Legend.** Draw from `layer.legend`: `ramp` and `diverging` → a CSS gradient bar with min, max, unit and labels; `text` → the sentence.

**F14. Swipe compare.** A "Compare" mode with two dates. Use the `@maplibre/maplibre-gl-compare` plugin **(verify it works with the installed MapLibre version; allow 30 minutes)**. If it does not: two map divs stacked, the top one with `clip-path: inset(0 0 0 X%)`, cameras synced on `move`, and a range input controlling X.

**F15. Story panel.** Side panel on desktop, bottom sheet on phones. Four headed paragraphs from `manifest.story`, in order: *What you see*, *What is happening*, *Why radar shows it*, *Why it matters*. Credits line from `manifest.credits` at the bottom.

**Done when (hour-10 checkpoint):** on the live URL, area 1 has timeline, play, swipe, change layer with legend, and story text.

## Hours 10–12 and day 2, hours 0–3

**F16. Chart.** Fetch `timeseries.json`. Inline SVG, one `<polyline>` per spot for the active polarization, x placed by real date, a vertical line at the current date, gap band shaded. Clicking a point sets `dateIndex`. Skip `null` values (break the line). If `facts` exists, show the current value as a large number ("Open water: 3,350 km²").

**F17. Spots on the map.** A marker per `manifest.spots` entry, coloured like its chart line.

**F18. Optical basemap toggle.** A raster-tile satellite basemap for orientation **(verify the provider's terms and attribution before adding; if unclear, skip this feature)**.

**F19. Restorable link.** Write `#area=…&date=…&layer=…` on every state change with `URLSearchParams`; read it on load.

## Day 2, hours 3–5: polish, then freeze

**F20.** Loading indicator while manifests and the first frame load. A plain message if a fetch fails.
**F21.** Check at 375 px wide and on a projector (1280×720): nothing overlaps, text readable from the back of a room.
**F22.** Footer credits: NASA/JPL-Caltech, ISRO, ASF DAAC; basemap attribution; link to the repository.
**F23.** An "About" dialog: three sentences on what the app is, plus the data caveat (PROVISIONAL calibration).
**F24.** Page title, description meta tag, favicon, and a social preview image so the link looks right when pasted.

## After freeze

- Bug fixes only.
- Record a 60-second screen capture of the full flow as the backup for a failed venue network.
- Take the screenshots listed in [04-pitch-and-repo.md](04-pitch-and-repo.md).
- Confirm the app runs offline from a laptop: `npm run build && npm run preview`.

## Manual test before every merge to `main`

1. `npm run build` passes.
2. `npm run preview`: every area button, every layer, play, compare.
3. Browser console has no errors.
4. After merge: the Actions run is green and the live URL shows the change.
