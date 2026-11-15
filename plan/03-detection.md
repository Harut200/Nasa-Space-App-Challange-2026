# Branch `detection`

> This is the plan as written on 14 November 2026, kept as it was. What differs now: the `detection` branch (and `detect-eda`, `detect-math`, `detect-dl`) was merged into `main` and deleted on 15 November. What was built and measured is in `docs/DETECTION.md`, `docs/RESULTS.md` and `docs/EVIDENCE.md`.

**You own:** `scripts/detect/`, `areas/*/story.json`, `notebooks/`, `tests/test_detect.py`.
**You receive:** contract A: linear-power GeoTIFFs on one grid ([00-contracts.md](00-contracts.md)).
**You deliver:** contract B: coloured GeoTIFFs, `layers.json`, `timeseries.json`; and the story text.

You decide everything about pixel values: filtering, scaling, colours, thresholds. Backend only moves and reprojects your output. You are also the team's answer to the **Validity** judging criterion.

Libraries: `numpy`, `scipy`, `rasterio`. Entry point: `python scripts/detect/run.py {area}`.

Every number marked **knob** below is a named constant at the top of the file. Real data will need tuning; do not bury them.

## Hours 0–1: set up and read

**D1.** `git checkout detection`; install from `requirements.txt` once backend merges it (B1).
**D2.** While waiting: read two NISAR application white papers for area 1 (flooding, wetlands) and one StoryMap; note three facts with sources for the story. Links are in [docs/research/data-sources.md](../docs/research/data-sources.md).
**D3.** Draft `areas/tonle-sap/story.json` with placeholder text and two or three spots (open lake, flooded forest, dry land).

## Hours 1–3: the whole chain on the demo area

Build against `data/processed/demo/gamma0/` (merge `main` after "fixture is on main").

**D4. Load.** Read every date of both polarizations into arrays `[date, row, col]`. Assert all share one shape and transform; fail loudly if not (that is a contract A violation: tell backend).

**D5. Speckle filter.** In **linear power**, before any decibel conversion: a 5×5 mean (`scipy.ndimage.uniform_filter`), ignoring `NaN` (filter the zero-filled data and the valid-pixel mask separately, then divide). Window size is a **knob**. Averaging decibels instead of power biases dark areas; do not do it.

> Upgrade if time allows: a multi-temporal filter keeps edges sharper. Only after the hour-10 checkpoint.

**D6. Decibels.** `db = 10 * log10(filtered)`.

**D7. Grey layers `hh`, `hv`.** Scale to 0–255: HH −25 to −5 dB, HV −30 to −10 dB (**knobs**; set from the histogram of the first real file). Alpha 0 where `NaN`. Write contract B.

**D8. Change layer.** For each date after the first: `diff = db[date] − db[first]`. Use **HH for flood areas, HV for forest areas**: a per-area setting, because open water shows best in HH and clearing shows best in HV.

- `|diff| < 3 dB` → fully transparent (**knob**; this is what stops speckle from painting the map).
- Negative → blue `#2166ac`, positive → red `#b2182b`, opacity rising with `|diff|` up to full at 8 dB (**knob**).
- Remove isolated specks: drop connected groups smaller than about 10 pixels (`scipy.ndimage.label`) (**knob**).

**D9. `layers.json`.** Exactly the structure in contract B, with the real ranges you used.

**D10. One check: `tests/test_detect.py`.** Run on the demo area and assert: inside the grown disc the change image is blue and opaque; far from the disc it is transparent in at least 99% of pixels. If the second assertion fails, speckle is leaking through.

**Done when:** `run.py demo` writes all of contract B and the test passes. Merge to `main` so backend can develop export against it.

## Hours 3–7: real data, area 1

**D11.** Run on the first real Tonle Sap dates as they arrive. In a notebook, plot the histogram of HH and HV in decibels and one image of each; set the D7 ranges.

**D12. Sanity check against reality (30 minutes, write the result down).** Open the same place in an optical image or OpenStreetMap. Confirm: the permanent lake is dark in HH; the known shoreline matches; flooded forest at the lake edge is *brighter* than dry forest. If any is false, stop and find out why before producing more layers. Save one side-by-side figure to `assets/images/`: it goes on the science slide.

**D13. Time series.** For each spot in `story.json`: mean of **linear power** inside a circle of `radius_m`, then convert the mean to decibels. One value per date per polarization; `null` if under half the circle is valid. Write `timeseries.json`.

**D14. Tune the change layer on real data.** Look at a stable region (city, permanent forest): it must be almost entirely transparent. If not, raise the threshold or the filter window. Note the final values for the README.

**Done when (hour 7):** `hh`, `hv`, `change` and `timeseries.json` exist for all dates of area 1.

## Hours 7–10: composite, story

**D15. Three-date composite `rgb`.** Earliest date → red, middle → green, latest → blue, each scaled as in D7, one polarization per area as in D8. Not dated (`"dated": false`), written to `products/rgb.tif`. Grey means unchanged; colour says when it changed.

**D16. Final story text for area 1** in `story.json`. Four short paragraphs, each at most 60 words, plain language, no acronym without its meaning:

1. *What you see*: describe the picture ("the dark shape grows…").
2. *What is happening*: the real-world process, with one number and its source.
3. *Why radar shows it*: one mechanism (smooth water reflects the signal away; flooded trunks bounce it back).
4. *Why it matters*: who depends on it, with one number and its source.

Keep a list of the sources; they go on the project page.

**Done when (hour-10 checkpoint):** change layer, composite and story for area 1 are on the live URL.

## Hours 10–12 and day 2, hours 0–3

**D17. A headline number per area** (optional `facts` in `timeseries.json`). Area 1: open-water area per date: count pixels with filtered HH below about −18 dB (**knob**, set from the D11 histogram where the lake and land peaks separate), times pixel area, in km². Say honestly what it is: open water only; flooded forest is bright and not counted; wind can roughen water and hide it. One real number ("the lake grew from X to Y km²") is worth more to judges than another layer.

**D18. Area 2 (Amazon frontier).** Change on **HV**; negative change is clearing. Sanity check as D12 against an optical image from the same months. Headline number: newly cleared hectares (HV drop over 3 dB, connected groups over 1 hectare). `story.json` for area 2.

**D19. HH/HV colour composite (Should).** Red = HH, green = HV, blue = HH minus HV in decibels, as a dated layer. Only if D17 and D18 are done.

**D20. Area 3 (Ararat Valley and Lake Sevan).** `hh`, `hv`, `change`, time series for a few fields and the shoreline. `story.json` carries the personal context: this is our home ground.

## Day 2, hours 3–5: before freeze

- Final knob values written in a short table in the README methods section.
- One paragraph of limitations: PROVISIONAL calibration, the July–August gap, change measured against the first pass only, thresholds chosen by eye.
- Every story paragraph proofread by a second team member.

## After freeze

You own the science slide and the findings slide of the deck, see [04-pitch-and-repo.md](04-pitch-and-repo.md).

## Stretch (only if the hour-10 checkpoint was met early)

Ground deformation from one GUNW interferogram pair. Not planned in detail on purpose.
