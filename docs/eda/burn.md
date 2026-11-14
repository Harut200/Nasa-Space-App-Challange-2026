# Burn scars and other changes the water layers do not show: before / after / difference evidence

Code: `scripts/detect/evidence.py` (shared), `scripts/detect/burn.py` (fire config, layers `burn_*`),
`scripts/detect/evidence_places.py` (other places, layers `ev_*`). Tests: `tests/test_burn.py`.
Run: `.venv/bin/python -m scripts.detect.burn <fire-id>` / `.venv/bin/python -m scripts.detect.evidence <id>`
(`--fig` figure only, `--dry` numbers only, `--no-mask`), then `scripts/ingest/export.py <id>`.
Figures: `docs/eda/burn/<id>.png` (fires), `docs/eda/evidence/<id>.png` (others): before | after | difference with the patch
outlined (orange) and the reference position (cyan +) | mean brightness inside the patch against a ring around it, per pass.

## Method (all on the uncalibrated ~180 m previews)

1. Channel and sign come from the documented evidence (`docs/research/events-2026.md`, each `story.json`), not from a search.
2. Passes that are QC-failed on the channel, corrupt or named in the config are made invalid **before** any filter (a first
   version let a corrupt pass leak into the others through the multi-temporal filter; a test now covers it). Speckle: 3x3
   boxcar, then per-date gain normalisation on stable pixels. The Quegan multi-temporal filter is off: on a planted 0.10 step it
   returned 0.067, i.e. it understates a permanent change.
3. BEFORE / AFTER = per-pixel median over the valid passes of the two date groups (fires: passes before the ignition / after it,
   the interval holding the ignition left out). DIFF = AFTER - BEFORE, 9x9 boxcar (5x5 for Rondonia).
4. CONTROL = the same difference between two halves of the BEFORE group. Noise level = Gaussian 99.9 % level from the control's
   median and MAD (median + 3.09 x 1.4826 MAD) of the same-signed change; a plain 99.9th percentile was tried first and is
   inflated by heavy tails (it made Thunder Bay undetectable). The threshold is that level (floor 0.02).
5. Patch = connected region of signed DIFF above the threshold, at least `min_km2`; the patch nearest the reference position
   (fires: the published fire position; others: a story spot), or all patches for places with many (`all_patches`).
6. Numbers: area (**area of changed radar echo, not a burnt-area / deforestation / harvest measurement**); mean change over the
   core of the patch (eroded by the smoothing radius, which otherwise dilutes the mean); control mean on the same pixels and the
   share of those pixels above threshold; rank = share of equal-size discs elsewhere in the frame (centre farther than a radius
   from the patch) that are at least as strong (0.0 = none of N); persistence per later pass; threshold sensitivity 0.5x/1x/2x.
7. Units: brightness 0..1 of the preview. dB-like = 30 x brightness **assumes** a ~30 dB stretch; unverified, so headlines use
   brightness. A darker/brighter echo is consistent with the named cause but could equally be harvest or other clearing, snow,
   soil moisture, rain, a radiometric difference between passes or a corrupt pass.

## Fires

| place | channel, sign | before | after (used) | area at 0.5x / 1x / 2x threshold (km²) | mean change (core) | control mean (frac. above thr.) | rank | persistence | by eye |
|---|---|---|---|---|---|---|---|---|---|
| thunder-bay-36-2026 | HH, + | 17 Jun-11 Jul | 23 Jul, 16 Aug, 9 Sep, 3 Oct, 15 Oct (21 Sep left out: frame-wide dip) | 5,346 / 1,875 / 0 (published 3,139) | +0.12 | -0.003 (8 %) | none of 129k discs as strong | above threshold on 4 of 5; not on 16 Aug (QC warn, ring dips too) | yes, diffuse; the outlined patch is the strongest core, other red areas exist |
| la-mierla-fire-2026 | HV, - | 20 Jun-14 Jul | 26 Jul-18 Oct (6 Oct left out: the whole frame jumps, ring +0.09) | 766 / 397 / 193 (published ~320) | -0.13 | +0.004 (0 %) | none of 275k | every used pass | yes, obvious |
| avila-burgohondo-fire-2026 | HV, - | 27 Jun-21 Jul | 19 Sep, 1 Oct (26 Aug, 13 Oct QC fail, 7 Sep corrupt) | 2,435 / 760 / 519 (published 500-550) | -0.09 | +0.002 (0 %) | none of 228k | both passes | yes, obvious; patch centroid is 12 km from the Navaluenga spot |
| gironde-megafire-2026 (not added) | HV, - | 23 Jun-17 Jul | 10 Aug-21 Oct | 430 / 232 / 40 | -0.24 | -0.033 | - | change on 10 Aug +0.06 and 21 Oct +0.12 (below threshold 0.135), peak +0.32 on 27 Sep | **not clear: it fades, control already darker; no products to add layers to** |

Caveats: the Ontario area is strongly threshold dependent (0 at 2x); no independent location check beyond the published fire
position (3-12 km from the patch centroid) and the published area; the 13 Oct Avila pass that the earlier note used is QC-failed
on both channels and is left out here; Avila's own water layers were blank (see below).

## Other places (layers `ev_*`)

| place | story and choice | before | after | area 0.5x/1x/2x | mean change | control | by eye / default |
|---|---|---|---|---|---|---|---|
| okavango | HH brightens down the fans | 26 Jun, 8 Jul | 12 Oct (30 Sep QC fail) | 6,973 / 5,065 / 2,986 | +0.21 | +0.01 (4 %) | obvious; `ev_diff` opens. One later pass only |
| sindh-indus | rice fields: dark HH in flood, bright later | 17 Jun-11 Jul | 21 Sep-15 Oct | 2,944 / 2,146 / 1,458 | +0.26 | +0.007 (9 %, not clean) | obvious; `ev_diff` opens. Curve shows the Aug dip then the rise |
| nile-delta | north brightens to a peak 13-25 Aug, then returns | 26 Jun, 8 Jul | 13, 25 Aug | 11,140 / 5,734 / 738 | +0.21 | +0.04 (4 %) | obvious; curve shows rise and return; default left as set by the team (`change`) |
| poyang | drained lake bed brightens | 5, 17 Jul | 27 Sep, 9 Oct | 5,343 / 3,152 / 1,030 | +0.18 | +0.016 (11 %, not clean) | obvious; default left (`change`) |
| bangladesh-haor | western margin brightens late Sep/Oct | 18 Jun-12 Jul | 4, 16 Oct | 5,203 / 1,611 / 92 | +0.22 | -0.01 (0 %) | clear on the margin; default left (`classes`) |
| urmia | shores brighten | 20 Jun-14 Jul | 19 Aug-12 Sep | 924 / 297 / 67 | +0.15 | +0.02 (16 %, not clean) | clear on the shore ring; default left; control not clean |
| rondonia | HV drop of new clearing blocks | 30 Jun, 17, 29 Aug | 4, 16 Oct | 3,225 / 869 / 144 | -0.10 | +0.05 (6 %) | **many small dark patches inside the old cleared land; cannot be told from burning, regrowth or moisture; mask not exported; story reworded, no new-clearing claim** |
| sudd | plain brightens in Aug | 20 Jun-14 Jul | 19, 31 Aug | none above noise (threshold 0.18) | - | - | not added |
| pantanal | steady dry season: no change described | | | | | | skipped |

Not done: up-terai-2026, mahanadi-delta-2026 (water-gain masks).

## Avila's blank layers: cause

`ops.run_pipeline` keeps pixels imaged on **every** date (`valid.all(0)`). `run.py` applies `qc.apply_qc`, which sets `valid = False`
on a whole date that failed QC in every channel (Avila: 26 Aug and 13 Oct), so the intersection is empty and every water-set layer
is transparent. Fix (one line, `scripts/detect/ops.py` in `run_pipeline`, not applied here, outside the allowed files):
`used = valid.flatten(1).any(1); valid = valid & valid[used].all(0, keepdim=True)`, then `python -m scripts.detect.run avila-burgohondo-fire-2026`
and re-run `burn` and `export`. The `burn_*` layers do not depend on it (they use the stack directly), so Avila opens on `burn_diff`.
