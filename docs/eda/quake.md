# Did NISAR capture earthquakes? Yes, four of eight tried: Tibet M7.0, Venezuela M7.2, Panama M7.7, Kumamoto M6.8

    export SERVER=user@host SSH_KEY=~/.ssh/key                     # only for the server steps, never stored in the repo
    .venv/bin/python -m scripts.detect.quake list                   # events in docs/eda/quake/events.json
    .venv/bin/python -m scripts.detect.quake launch EVENT VIEW      # copy the job to the server and start it (queue EVENT:VIEW ... for several)
    .venv/bin/python -m scripts.detect.quake fetch  EVENT VIEW      # copy the reduced server windows (about 3-11 MB per pair)
    .venv/bin/python -m scripts.detect.quake run    EVENT [VIEW]    # numbers + figures (docs/eda/quake/EVENT/), results.json
    .venv/bin/python -m scripts.detect.quake export EVENT           # app layers + areas/EVENT (clear events only; Tibet is frozen)
    .venv/bin/python -m scripts.detect.quake overview               # docs/eda/quake/overview.png
    .venv/bin/python -m pytest -q tests/test_quake.py               # 8 tests (maths, event config, two-look decomposition)

An event is one record in `docs/eda/quake/events.json` (USGS facts, window, one entry per look direction with its
co-seismic and control GUNW granule, radius, far-field radius, mask hints, verdict). Numbers: `docs/eda/quake/results.json`
(keyed by event id, then look direction). Candidates: `docs/eda/quake/catalogue.json` (now with a `quake_study` field per
event). Data (git-ignored): `data/processed/<event>-insar/<view>/`. Overview of the clear events at one fringe interval:
`docs/eda/quake/overview.png`.

## Verdicts (processed 14 Nov 2026; all products PROVISIONAL, sign convention unconfirmed)

Rule: **clear** = spatially coherent pattern at the epicentre, well above the control pair's noise, same processing and colour
scale; **marginal** = something above the noise but not coherent or not confirmed (shown in docs only); **not detected** =
decorrelated, offshore-only or atmosphere-dominated. Peak to peak = 0.5 to 99.5 percentile of LOS within the stated radius of
the USGS epicentre; control = the same statistic for a quiet pair on the same track and frame.

| Event (USGS id) | Look directions used (pair, track/frame) | Verdict | Peak to peak within radius (control) | Far-field std (control) | Reason |
|---|---|---|---|---|---|
| Tibet M7.0, 22 Oct (us6000u43g) | asc 19-31 Oct, 112/18 | **clear** | 127 cm within 45 km (4.7 cm) | 1.3 cm (0.7) | two-lobe butterfly, about ten fringes, control flat |
| Venezuela M7.2, 24 Jun (us6000t7zc) | asc 13-25 Jun, 162/7; desc 17-29 Jun, 54/83 | **clear, two look directions** | asc 50 cm, desc 55 cm within 60 km (11.6 / 10.2 cm) | 8.8 / 7.3 cm (2.9 / 2.3) | sign-flip line, mirrored between the two views (horizontal motion); M7.5 40 s later is in the same pairs |
| Panama M7.7, 9 Oct (us6000u18k) | desc 2-14 Oct, 40/85; asc 5-17 Oct, 76/5 | **clear, two look directions** | desc 133 cm, asc 85 cm within 60 km (21 / 15 cm) | 3.3 / 6.1 cm (3.5 / 1.5) | about twelve concentric fringes; only east and south of the epicentre is covered; 4 of 5 pixels near the epicentre are lost |
| Kumamoto M6.8, 28 Jul (us6000tgb9) | asc 21 Jul-14 Aug, 24/18 (24 d); asc 125/18 (36 d) as a partial check | **clear, one look direction** | 38 cm within 30 km (5.8 cm) | 3.7 cm (1.8) | red lobe NE of the epicentre, weaker blue lobe SW; the descending 36-day pair (3/72) is fully decorrelated |
| Flores Sea M7.8, 14 Aug (us6000tkt2) | asc 12-24 Aug, 168/173; desc 13-25 Aug, 3/93 | **not detected** | 12 cm / 11 cm (4.2 / 10.7 cm) | 2.0 / 12.3 cm (0.9 / 5.2) | epicentre in the sea; only land 20-60 km south is usable; smooth gradients the size of the control's atmosphere, not tied to the epicentre |
| Qinghai M5.8, 28 Jul (us6000tga9) | desc 24 Jul-17 Aug, 62/71; asc 18 Jul-11 Aug, 155/19 | **not detected** | 6.8 cm within 25 km (5.2 cm) | 4.5 cm (1.8) | expected few cm at most; ascending pair fully decorrelated; descending shows only atmosphere-sized gradients |
| Mexico M7.3 offshore Chiapas, 17 Jul (us7000t1bu) | asc 12-24 Jul, 62/9; desc 12-24 Jul, 55/81 | **not detected** | 5.8 / 12.8 cm within 60 km, under 1 % valid pixels (9.3 / 12.2 cm) | 1.7 / 3.4 cm (2.3 / 3.2) | epicentre 60 km offshore, coast about 55 km away, land within +/-3 cm (asc); desc no coherent pattern |
| Sanriku, Japan M6.9, 24 Jun (us6000t7zq) | asc 21 Jun-3 Jul, 110/22; desc 22 Jun-4 Jul, 118/68 | **not detected** | 20 / 5 cm within 50 km, 1.4 % valid pixels (8.3 / 6.0 cm) | 1.5 / 0.6 cm (2.2 / 2.4) | epicentre 35 km offshore and 34-45 km deep; coastal forest decorrelated; nothing above the control |

Not usable look directions (documented, not exported): Panama descending track 141 (27 Sep-9 Oct; mean coherence 0.20, four
small disconnected unwrapping components), Kumamoto descending track 3 (36 days across the instrument outage, no usable
pixels), Qinghai ascending track 155.
Tibet: no second look direction or later pair exists (ASF check 14 Nov 2026): the newest track-112 pair is the one used; the
descending track 91 pairs end on 18 Oct. Late-October scan: see "Search for further events".

Sensitivity of the clear events' peak to peak to the processing choices (far-field radius +/-15 km, quadric instead of plane,
coherence threshold 0.5): Tibet 119-138 cm; Venezuela 50-51 (asc), 53-55 (desc) cm; Panama 68-87 (asc), 107-165 (desc) cm;
Kumamoto 34-38 cm. Panama desc is the least stable because its far field (> 60 km) is a small strip, see its section.

## Tibet M7.0, 22 Oct 2026 (the first result, unchanged)

NISAR's 12-day pair of 19 to 31 October 2026 (track 112, frame 18, ascending, `NISAR_L2_UR_GUNW` urgent-response product,
PROVISIONAL) spans the USGS M7.0 event (us6000u43g, 22 Oct 14:33 UTC, 32.338 N 93.784 E, depth 17-18 km, normal faulting). It shows a
classic two-lobe butterfly of about ten fringes next to the epicentre. The pre-event pair on the same track and frame (7 to 19 Oct),
processed identically, is flat.

| Quantity | Co-seismic 19-31 Oct | Control 7-19 Oct |
|---|---|---|
| LOS change within 45 km of epicentre, 0.5 to 99.5 percentile | **-87 to +40 cm (127 cm peak to peak)** | 4.7 cm range, no pattern |
| Extreme single pixels (least reliable, next to the rupture) | -141 / +59 cm | max abs 6.8 cm |
| Far-field standard deviation (> 60 km, after plane removal) | 1.3 cm | 0.68 cm |
| Local noise (map minus its own 10 km mean, far field) | 0.48 cm | 0.50 cm |
| Area with abs(LOS) > 5 / 10 / 20 cm (window-limited, north part) | 3,330 / 1,485 / 585 km2 | 0 / 0 / 0 |
| Peak signal / control noise | about 130 | |

Figures: `fig_a_fringes.png`, `fig_b_los_cm.png`, `fig_c_control_cm.png`, `fig_d_profile.png`, `fig_e_iono_screen.png` (in
`docs/eda/quake/`; regenerated by the generalised script, same numbers, titles now generic). Method details and traps below.
A M5.9 aftershock (USGS us6000u4ty, 25 Oct, about 30 km from the mainshock) lies inside the pair.

### Tibet: does it agree with physics? (USGS moment tensor, `us` solution)

* Mw 7.0, scalar moment 3.4e19 N m, depth 17.2 km; nodal planes strike 175 / dip 57 / rake -119 and strike 40 / dip 43 /
  rake -54. Both are normal faults with some strike slip (the finite-fault product gives Mw 6.95).
* Slip of order 2 m over a fault of about 30 x 15 km (M0 = mu x area x slip with mu = 30 GPa) implies a vertical
  displacement of order 1 m and an LOS change of order 0.5-1 m on the hanging wall: our -0.9 to -1.4 m extremes next
  to the rupture and the 40-60 cm on the other side are of that order.
* Pattern: two lobes of opposite sign across a fault trace that runs about NNE-SSW through the decorrelated sliver
  just north-east of the epicentre. A normal fault gives opposite LOS signs on hanging wall and footwall. The USGS epicentre
  lies on the red (away from satellite) side, near the lobe edge.
* With our sign (positive = toward satellite), hanging-wall subsidence reads negative, and the red side is the
  side we expect to be the hanging wall for the west-dipping plane (strike 175). With the opposite sign, the second plane
  (strike 40, dipping south-east) would fit. **One look direction and an unconfirmed sign cannot choose between the planes.**
  The agreement we can claim is: normal-faulting-type two-lobe pattern, the right order of magnitude, centred on the epicentre.


### Ionosphere trap (important for anyone reusing this)

The `ionospherePhaseScreen` in the co-seismic product contains a compact dipole exactly at the epicentre (-19 to -5 rad
within 30 km, against a smooth background; `fig_e_iono_screen.png`). It is a leak of the earthquake displacement
into the split-spectrum estimate. Subtracting the screen changes the peak to peak from 127 to 138 cm and distorts the
near field. In the control pair the screen is smooth and makes no difference (far-field std 0.68 with, 0.69 without).
So for a strongly deforming scene: do not subtract the screen near the source.


## Venezuela M7.2, 24 June 2026: two look directions

USGS us6000t7zc: Mww 7.2, 22:04:33 UTC, 10.371 N 68.556 W, 20 km E of San Felipe, depth 11.5 km (moment-tensor depth),
M0 7.3e19 N m, strike slip (nodal planes strike/dip/rake 177/79/-16 and 270/74/-169). **Forty seconds later USGS
lists an M7.5** (us6000t7zp, 22:05:12, 10.596 N 67.221 W, 20 km W of Catia La Mar, depth 23.5 km, strike slip, finite-fault
peak slip 1.8 m), 146 km to the east. Both are inside every pair; the eastern third of the map (> 100 km east of the M7.2
epicentre) belongs mostly to the M7.5 and the two cannot be separated. The far field excludes 45 km around both.

* Ascending, track 162 frame 7: 13-25 Jun, control 1-13 Jun. Descending, track 54 frame 83: 17-29 Jun, control 5-17 Jun.
  Window 69.3-66.7 W, 9.9-10.95 N. Valid pixels 57 % within 60 km of the epicentre (vegetation, water and the Caribbean).
* LOS within 60 km (0.5 to 99.5 percentile): ascending -27 to +24 cm (50 cm), descending -34 to +21 cm (55 cm); controls 11.6 and
  10.2 cm (a smooth atmospheric ramp of about 10 cm with no sign-flipping line). Area with abs(LOS) > 5 / 10 / 20 cm: asc 6,830 /
  3,290 / 1,140 km2, desc 4,940 / 1,640 / 600 km2 (controls 2,310 / 109 / 0 and 490 / 9 / 0). Peak / control far-field std: 9 and 15.
* Pattern: a line of sign change running roughly east-west through the zone north of and east of the epicentre; the pattern is
  **mirrored between the two look directions** (ascending: blue north, red south of the line; descending the opposite). That is what
  mostly horizontal motion looks like from two opposite sides; vertical motion would give the same sign in both.
* Two-look decomposition (`decompose_east_up`, north component neglected, ascending/descending pairs 4 days apart): east
  component -46 to +37 cm, vertical -9 to +7 cm (0.5 to 99.5 percentile within 60 km, 54,600 overlapping pixels). **Approximate**: the
  pairs are not simultaneous, the unit vectors are window means, any north-south motion leaks in, and a wrong sign convention would
  flip both components. Layers `east_cm` and `up_cm` are exported with that label (`fig_e_decomposition.png`).
* Physics: M0 7.3e19 N m over a fault of about 60 x 15 km (mu = 30 GPa) means slip of order 2-3 m, so horizontal motion of
  order 1 m across the fault and LOS changes up to several tens of cm near it. We see tens of cm (extremes -33/+42 cm asc, -42/+30
  cm desc, percentiles 50 cm p2p), smaller than the naive near-fault estimate because the pixels at the fault are decorrelated (50 %
  of the pixels within 15 km of the epicentre are masked). Same order of magnitude; strike-slip pattern (antisymmetric across a line,
  mostly horizontal) agrees. We do not claim which nodal plane or which direction of slip.
* Figures: `docs/eda/quake/venezuela-m72-2026/fig_{a_fringes,b_los_cm,c_control_cm,d_profile}_{asc,desc}.png`, `fig_e_decomposition.png`.
* App: `data/processed/venezuela-m72-2026/products/` (`los_asc_cm`, `los_desc_cm`, `fringes` and `coherence` of the ascending pair,
  `east_cm`, `up_cm`), `areas/venezuela-m72-2026/`.

## Panama M7.7, 9 October 2026: two look directions

USGS us6000u18k: Mww 7.7, 17:56:05 UTC, 7.607 N 80.757 W, 10 km WSW of Pitaloza Arriba (Azuero peninsula), depth 13.5 km, M0 3.8e20 N m,
strike slip (nodal planes 25/75/-155 and 288/66/-17), finite-fault Mw 7.66 with peak slip about 11 m. Aftershocks in the pairs:
M6.6 (20:25, 7.68 N 81.45 W, 75 km west), M6.0 (10 Oct), M6.1 (12 Oct), M5.8 (9 Oct).

* Descending, track 40 frame 85: 2-14 Oct, control 20 Sep-2 Oct. Ascending, track 76 frame 5: 5-17 Oct, control 30 Aug-11 Sep (24 days;
  there is no earlier 12-day pair on that frame). Window 81.9-80.1 W, 7.0-8.2 N. Descending track 141 (27 Sep-9 Oct) was tried and is decorrelated.
* **Coverage is partial**: the ascending frame 5 ends right at the epicentre (nothing north); the descending frame starts 20 km west of it.
  The M6.6 and the western part of the rupture are not covered by either. Only 21 % (asc) and 27 % (desc) of the pixels within 60 km
  of the epicentre are valid (land coherence, ocean, steep gradients).
* LOS within 60 km: descending -10 to +124 cm (133 cm p2p, about twelve concentric fringes, extremes up to +153 cm), ascending -53 to
  +32 cm (85 cm; extremes -70/+40 cm). Controls: 21 cm (desc, a smooth ramp; one edge strip and a slanted unwrapping strip next to the swath
  edge are masked in both pairs, `mask_boxes`) and 15 cm (asc, a smooth ramp of about 10 cm NW). Far-field std in the co-seismic maps is
  3.3 / 6.1 cm because the far field (> 60 km) is only the edge of the frame and the strong signal extends beyond it: peak to peak
  is therefore uncertain by tens of cm (107-165 cm for desc across processing variants). Peak / control far-field std: 35 in both views.
* Opposite sign in the two views (asc red, desc blue) is again the signature of horizontal east-west motion, but the two frames
  cover different parts and the references are weak, so **no east/up decomposition is exported for Panama**; `decomp` numbers (east about
  +115 cm, up about +76 cm) were computed only as a check and are not trustworthy enough to show.
* Physics: USGS finite-fault slip of up to 11 m on a strike-slip fault predicts metre-scale LOS changes near the fault and many
  fringes; we see 1.3 m peak to peak in the covered area and a dense fringe field. Agrees in order of magnitude and in "very large,
  many fringes"; a four-quadrant strike-slip pattern cannot be checked because we only see one side. We do not claim which plane.
* Figures: `docs/eda/quake/panama-m77-2026/` (asc, desc). App: `data/processed/panama-m77-2026/products/` (`los_asc_cm`, `los_desc_cm`,
  `fringes` of the descending pair, `coherence`), `areas/panama-m77-2026/`.

## Kumamoto, Japan M6.8, 28 July 2026: one look direction

USGS us6000tgb9: Mww 6.8, 07:27:15 UTC, 32.618 N 130.660 E, depth 8 km (location) / 11.5 km (moment tensor), M0 1.7e19 N m, strike slip
(212/75/-164 and 118/74/-16), finite-fault Mw 6.75, peak slip 2.6 m.

* Ascending track 24 frame 18: 21 Jul-14 Aug (24 days, bridges the instrument outage of 27 Jul-10 Aug, so it also holds aftershock and any slow
  movement of those weeks); control 9-21 Jul (12 days, so the control is shorter than the co-seismic pair: its noise is a lower bound).
  Window 130.0-131.3 E, 32.0-33.2 N. 58 % of the window is above coherence 0.35 (mean 0.57), 49 % of the pixels within 30 km.
* LOS within 30 km: -27 to +11 cm (**38 cm** p2p); control 5.8 cm; far-field std 3.7 cm (control 1.8). Area with abs(LOS) > 5 / 10 / 20 cm:
  905 / 260 / 102 km2 (control 60 / 44 / 0). Peak / control far-field std 15. Extremes -37 / +26 cm.
* Pattern: a red lobe (away from the satellite) NE of the epicentre, a blue lobe SW, with the decorrelated ground at the epicentre in between.
  It is asymmetric (strong red, weak blue); we cannot say whether that is the rupture, the LOS geometry or masked pixels.
* Physics: a shallow Mw 6.8 strike-slip rupture with up to 2.6 m slip is expected to move the ground by tens of cm next to the fault: our
  tens of cm agree in order of magnitude; antisymmetric lobes agree in type.
* Second ascending pair (track 125, 16 Jul-21 Aug, 36 days): its frame covers only the west part (the epicentre is on its eastern edge). It
  shows the sign change across the epicentre, -14 to +10 cm (24 cm p2p, control 8.6 cm), consistent but incomplete; not exported. The
  descending track 3 pair (8 Jul-13 Aug, 36 days across the outage) has no usable pixels: vegetation decorrelation.
* Figures: `docs/eda/quake/kumamoto-m68-2026/fig_*_asc24.png` and `_asc125.png`. App: `data/processed/kumamoto-m68-2026/products/`, `areas/kumamoto-m68-2026/`.

## Not detected

**Flores Sea M7.8, 14 Aug 2026** (us6000tkt2, 21:58 UTC, 8.336 S 121.345 E, 66 km NNW of Ende, depth 23.5 km, M0 5.5e20, thrust, nodal planes 90/32/90 and
269/58/90, finite-fault peak slip 7 m). Pairs asc 12-24 Aug (168/173), desc 13-25 Aug (3/93); controls are post-event 17-29 Sep and 6-18 Sep (no
pre-event pair on those frames), so a late slow signal would be in the control. The epicentre is in the sea; the north coast of Flores
is 20-60 km to the south. Land LOS is within -10 to +3 cm (asc) and -3 to +9 cm (desc) in smooth gradients; control p2p 4.2 and 10.7 cm. The two views do
not agree, nothing is tied to the epicentre. USGS mechanism and size would predict tens of cm on the coast if the slip reached near it;
we see at most 10 cm, so either the rupture was too far offshore/deep for the coast to move much, or a long-wavelength signal was absorbed by
the plane we remove or hidden in the atmosphere. We cannot tell which. Not detected.

**Qinghai M5.8, 28 Jul 2026** (us6000tga9, 03:34 UTC, 35.43 N 99.57 E, depth 11.5 km, M0 5.9e17, strike slip). 24-day pairs only. A Mw 5.8 is expected to move
the ground by a few cm at most. Ascending pair (18 Jul-11 Aug, 155/19): fully decorrelated. Descending (24 Jul-17 Aug, 62/71): 87 % valid, LOS
-3 to +4 cm within 25 km vs control 5.2 cm, atmosphere-sized topography-correlated gradients, nothing at the epicentre. Not detected.

**Mexico M7.3 offshore Chiapas, 17 Jul 2026** (us7000t1bu, 14:48 UTC, 14.571 N 92.962 W, 60 km WSW of Puerto Madero, depth 15.5-22 km, M0 1.1e20, thrust,
nodal planes 123/64/98 and 286/27/75, finite-fault peak slip 2.9 m). Control 24 Jul-17 Aug (post-event). The ascending frame reaches the coast
about 55 km from the epicentre; only 1 % of pixels within 60 km are valid. LOS on land within +/-3 cm. Not detected (offshore): the first-pass
note "offshore" holds. The descending pair (55/81) has under 1 % valid pixels within 60 km and a control as noisy as the signal (12.8 vs 12.2 cm).

**Off Sanriku, Japan M6.9, 24 Jun 2026** (us6000t7zq, 22:30 UTC, 40.256 N 142.179 E, 35 km ENE of Noda, depth 34 km, USGS moment-tensor depth 45 km, M0 2.9e19, thrust
nodal planes 32/63/107 and 177/32/60). A rank-8 candidate of the first pass, tried at the end because the Iwate coast is about 35 km from the epicentre.
Asc 21 Jun-3 Jul (110/22), desc 22 Jun-4 Jul (118/68); controls are post-event 3-15 Jul and 4-16 Jul. Both pairs also contain the M6.0 and M5.8
aftershocks of 27 Jun and 1 Jul (35 km deep). Under 1.5 % of the pixels within 50 km are valid (forest, sea); LOS on the valid coast
-9 to +12 cm (asc) and -4 to +5 cm (desc), control 8 and 6 cm. A thrust 34-45 km deep, 35 km offshore is expected to move the coast by a few cm at most; consistent with seeing nothing. Not detected.

## Search for further events

* Catalogue re-scan: of the 50 first-pass events, those with an epicentre on land or within about 50 km of land and a spanning pair
  day were the eight above (Sanriku M6.9 was added last); all others are 100+ km offshore, deep (> 30 km) or on small islands (Vanuatu, Kuril, Aleutians, Kamchatka,
  New Caledonia, Kermadec, Papua New Guinea sea, Philippines Sarangani M6.3 offshore 24-day pair, Timor Sea): `quake_study` field in
  `catalogue.json`. Aftershocks of the studied events (Panama M6.6/6.1/6.0/5.8, Flores M6.1/5.8, Venezuela M7.5) are inside their mainshock pairs.
* USGS check for 22 Oct-14 Nov 2026, M >= 5.8, depth <= 40 km (curl, 14 Nov): 17 events after the mainshock. On land: the M5.9 Tibet aftershock (25 Oct, inside the
  Tibet pair). The rest are offshore (Vanuatu M6.8 on 24 Oct and its aftershocks, Bouvet, Easter Island, Solomon Islands M6.4 on 9 Nov, Papua
  New Guinea, Peru offshore, Mid-Atlantic Ridge, Japan trench M6.1); no NISAR GUNW was found over the Vanuatu and Solomon epicentres.
  Nothing new on land to process.
* Tibet: ASF catalogue check on 14 Nov shows no descending post-event pair and no later ascending pair (the last on track 112 ends 31 Oct), so
  no new layer was added to `tibet-m70-2026`.

## Method (generalised from the Tibet pass)

1. Window cut remotely from each GUNW on the team server (`remote_products.py`, unchanged) at 80 m: unwrapped phase, coherence and connected
   components only (the ionosphere screens were cut only for Tibet); about 2.5-10 min and 200-900 MB of range requests per pair depending on window size;
   reduced to 2-11 MB per pair.
2. Unwrapped phase; mask coherence >= 0.35 and the **largest connected component** (Tibet: component 1, same thing). Event-specific
   `mask_boxes` or the Tibet stripe mask where a straight low-coherence band carries a whole-cycle offset; the control gets the same mask.
3. **No ionosphere screen subtracted** (see the trap above). For events other than Tibet the screen was not even fetched.
4. Plane fitted only on the far field (outside `far_km` of every epicentre of the event, including the second Venezuela event), subtracted, far-field
   median set to zero. LOS cm = -wavelength / (4 pi) x phase x 100, wavelength 24.2-24.4 cm read from the file.
5. Statistics: p2p of the 0.5-99.5 percentiles within `radius_km`; far-field std; local noise; areas above 5/10/20 cm (window-limited, so minimums);
   control computed identically; variants (far-field radius, quadric, coherence 0.5).
6. LOS unit vectors and incidence angles come from the product cubes (window means). Two look directions: `decompose_east_up` solves the 2 x 2
   system for east and up on a common EPSG:4326 grid, north neglected.

## Limits (all events)

* PROVISIONAL (and some urgent-response) products; calibration and processing may change.
* LOS only. Where one look direction exists, uplift, east-west and north-south motion are mixed. Two look directions (Venezuela, Panama)
  still leave north-south motion unresolved; only Venezuela's east/up split is exported and it is approximate.
* **Sign convention unconfirmed** (ISCE3 definition, as in `docs/eda/insar.md`). A wrong sign flips every map and the east/up components.
* Near-fault pixels (< 10-15 km) are the most gradient-rich and the most decorrelated (35-65 % masked within 15 km); unwrapping errors of
  whole cycles (12 cm) cannot be excluded there; extreme pixel values are not results, percentiles are.
* The far field is not proven undeformed and for Panama it is only a strip; a long-wavelength signal can be absorbed by the plane that is removed
  (this may be what happened for the Flores land areas). Atmosphere gives smooth gradients of 5-20 cm in tropical and mountainous scenes (controls).
* Pairs of 12-36 days include aftershocks and early afterslip; the Kumamoto control (12 d) is shorter than its co-seismic pair (24 d); Flores controls are
  after the event; Panama's ascending control is 24 days.
* No independent cross-check (Sentinel-1/ALOS-2 interferogram, GNSS, USGS finite-fault forward model to LOS) was done for any event.
* Frames can end at the epicentre (Panama): the pattern is then only partly seen.
* Area figures are clipped by the window and the masks, so they are minimums.

## What may and may not be claimed

* **Say:** "NISAR's 12-day pair spanning the M7.0 Tibet earthquake of 22 October 2026 shows the ground moving by more than a metre (127 cm peak to peak
  along the radar line of sight within 45 km), as a two-lobe pattern of about ten fringes centred on the USGS epicentre. The same track a fortnight earlier is flat to under 1 cm."
* **Say:** "For the M7.2 Venezuela earthquake (24 June) and the M7.7 Panama earthquake (9 October) NISAR saw the ground move by tens of centimetres to over a
  metre along the line of sight from two look directions, in patterns that flip sign between the two views, as horizontal strike-slip motion would."
* **Say:** "The M6.8 Kumamoto earthquake (28 July) shows a two-lobe pattern of up to about 30 cm in a 24-day pair, from one look direction."
* **Say:** "These are provisional measurements; where we looked at earthquakes in the sea (Flores M7.8, Mexico M7.3) or too small to see (Qinghai M5.8) we report that
  nothing was detected."
* **Say (Venezuela only, with 'approximately'):** "Combining the two views gives roughly 40 cm of east-west motion and under 10 cm vertical."
* **Do not say:** that NISAR measured vertical or horizontal displacement for Tibet, Kumamoto or Panama (one view or no sound decomposition), or that the Venezuela
  split is precise; the fault plane, slip or rupture length of any event; damage, casualties or any impact on people; that our maps are validated against GNSS,
  Sentinel-1 or any other data; that the sign (blue = toward satellite) is confirmed; extreme pixel values as results; forecasts or precursors; that the M7.5 and M7.2
  Venezuela signals are separated; that the Panama pattern is complete (frames end at the epicentre); that NISAR "failed" at Flores or Mexico (the ground there
  was too far away or the signal was not above the noise: say so).
* **Do not** show the single-pair maps as a time series; the app has one static pair per event (`dated: false`).

## App layers (stage 3)

Same format as Tibet for every clear event: `data/processed/<event>/products/`: RGBA uint8 GeoTIFFs EPSG:4326, north up, 1000 px wide, about 80-150 m, alpha 0 outside valid data,
`layers.json` (all `dated: false`), `timeseries.json` (one date, empty `series` and `facts`), `source.json`; `areas/<event>/area.json` and `story.json` (four story parts, spots).

| Event | Folder(s) | Layers |
|---|---|---|
| Tibet | `data/processed/tibet-m70-2026/products/`, `areas/tibet-m70-2026/` (first pass, not touched) | `los_cm`, `fringes`, `coherence` |
| Venezuela M7.2 | `data/processed/venezuela-m72-2026/products/`, `areas/venezuela-m72-2026/` | `los_asc_cm`, `los_desc_cm`, `fringes` (asc), `coherence` (asc), `east_cm`, `up_cm` (approximate) |
| Panama M7.7 | `data/processed/panama-m77-2026/products/`, `areas/panama-m77-2026/` | `los_asc_cm`, `los_desc_cm`, `fringes` (desc), `coherence` (desc) |
| Kumamoto M6.8 | `data/processed/kumamoto-m68-2026/products/`, `areas/kumamoto-m68-2026/` | `los_cm`, `fringes`, `coherence` |

Things the lead must do or decide:

* run `scripts/ingest/export.py` for each new id (not run here); the areas have no dated layers: hide the series panel and the date compare.
* the legend for LOS layers uses `"type": "diverging"` with three explicit colours (as for Tibet); the colour limits differ per event (+/-40 cm Tibet and Venezuela,
  +/-60 cm Panama, +/-30 cm Kumamoto): the legend carries the numbers. Where an area has two LOS layers, `los_asc_cm` and `los_desc_cm` have the same scale.
* `area.json` for the two-view events lists the first look direction's track and frame and `"direction": "ascending and descending"`.
* `fringes.tif` and `coherence.tif` are 0.3-1.6 MB as GeoTIFF (Kumamoto the largest); the exporter converts to WebP.

## Server leftovers

`~/work2/job_quake*.json`, `~/work2/queue_quake.sh`, `~/work2/logs/quake_*.log`, `~/work2/out/quake/<event>/<view>/win/` (full-resolution `.npz`, 0.2-0.5 GB per
look direction pair set including `small2/`), the Tibet files from before. No process running when this was written (see the final report). Nothing outside `~/work2/`.

*Data credit: NISAR L2 GUNW (UR and PROVISIONAL V1), NASA/JPL-Caltech and ISRO, distributed by the ASF DAAC. Earthquake
parameters: U.S. Geological Survey.*
