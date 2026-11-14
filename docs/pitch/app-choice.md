# Which app do we present? (decide Saturday night)

Two finished designs are on branch `eda` (also `detection`). Same real data, 19 places (15 seasons of water, 4 earthquakes). Pick one tonight; all remaining effort then goes into that one.

## Try both (about 3 minutes)

```bash
git fetch && git checkout eda && git pull
cd src/app  && npm install && npm run dev -- --port 5173   # A: natural map
# second terminal:
cd src/app2 && ln -s ../app/node_modules node_modules && npx vite --port 5174   # B: story mode
```

Open `http://localhost:5173/Nasa-Space-App-Challange-2026/` and `http://localhost:5174/Nasa-Space-App-Challange-2026/`.

## A — Natural map (`src/app`)

Real satellite globe (NASA Blue Marble, Sentinel-2 detail). Dots are the 19 places. Click, fly in, press Play: the flood spreads over real terrain. Floating glass panels. Big number that follows the date. Drag-to-compare two dates.

- Strong: the first five seconds; anyone understands it without a word; compare view is the best single image we have.
- Weak: story text is tucked in a side sheet; timeline is plain dots.
- State: tried by hand in Chrome; compare bug found and fixed. Phone layout and two extra themes not re-checked.
- Try: `#/tonle-sap?layer=water&date=2026-10-17&compare&split=50`

## B — Story mode (`src/app2`)

"A season, performed." Opening with the flood already playing. Six scenes per place driven by arrow keys. Timeline is the water curve itself, with the uncertainty band and the outage as a gap. Gallery of all places with sparklines. "How sure are we?" sheet.

- Strong: built for presenting live (arrow keys walk the judges through); shows the evidence (curve, range) on screen; honesty is designed in.
- Weak: darker, less immediate than a real Earth; basemap blotchy when zoomed in.
- State: finished, but not yet tried by hand. Keyboard, compare drag, Lake Sevan and Ararat need a click-through.
- Try: `#/tonle-sap?mode=story&scene=1`, keys: arrows, Space, `M`, `C`, `P`, `?`

## Recommendation

Present **A**. It is the one already tested by hand, and it wins the first impression. Then move two things from B into A if time allows, in this order: (1) the chart timeline with the uncertainty band, (2) the six-scene story mode with arrow keys. If B survives a click-through tonight without bugs and the team prefers presenting with arrow keys, B is a sound choice too; do not try to ship both.

## After the choice (same for either)

1. Fix what the click-through finds.
2. Merge `detection` into `main`; turn on Pages (Settings → Pages → Source: GitHub Actions).
3. Record the 30-second video from the live app (`docs/pitch/demo-script.md`).
4. Put the live URL in the deck.
