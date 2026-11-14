# Project Plan — "Dancing with the SARs"

NASA Space Apps Challenge 2026 · **deadline Sunday 15 November, 23:59 local time** · updated Saturday 14 November, 10:45 (Yerevan).

**What we ship:** a public, open-source web app where anyone can scrub through a season of NISAR L-band radar images and watch one piece of Earth's surface change, with the physics explained in plain language next to the map.

**Step-by-step work plan, split into three branches (frontend, backend, detection) plus pitch and repository:** [plan/](plan/README.md). Where the two differ, `plan/` is newer.

This file holds what `plan/` does not: the decisions still open, and the facts verified on 14 November that fill the blanks `plan/` left. Background material:

- [docs/research/data-sources.md](docs/research/data-sources.md) — coverage, search queries, file layout, access code
- [docs/research/science-brief.md](docs/research/science-brief.md) — per place: what changes, what radar shows, 2026 figures, sources
- [docs/research/submission-rules.md](docs/research/submission-rules.md) — deadline, demo format, required fields, judging

---

## 1. Who owns which branch

| Branch in `plan/` | Owner | Plan file |
|---|---|---|
| `frontend` — the web app | Arameh | [plan/01-frontend.md](plan/01-frontend.md) |
| `backend` — NISAR data in, web images out | Harutyun | [plan/02-backend.md](plan/02-backend.md) |
| `detection` — radar numbers to pictures, findings and story | Abel | [plan/03-detection.md](plan/03-detection.md) |

Write these names into the Owner column of [plan/README.md](plan/README.md). Note that `detection` includes Python (filtering, colouring, time series) as well as the story text; if Abel wants to stay on science and writing, Harutyun takes `scripts/detect/` and Abel keeps `areas/*/story.json`, the sanity checks and the deck.

## 2. Decisions still open

**Order of places.** `plan/` runs Tonle Sap → Rondônia → Armenia. All three passed the coverage gate (section 3), so the order is now a choice, not a constraint.

Suggested change: **Tonle Sap → Armenia → Rondônia.** Keep Tonle Sap first: its files are a quarter the size (1.9 GB against 7.5 GB) and its signal is the strongest, which protects the hour-10 checkpoint. Move Armenia ahead of Rondônia: it is the story only this team can tell, one satellite frame covers both the Ararat Valley and Lake Sevan, and it fits the Local Impact and Best Storytelling awards. Because Armenia's files are large, start fetching them in the background as soon as the pipeline works on Tonle Sap.

**Event.** No 2026 local event in Armenia was found. If that is right, the team is in the Universal Event: deadline in the team owner's time zone, at least two members, all three joined to the same team on the site. Confirm on the Space Apps locations page.

**Repository name typo and project name** — as listed in [plan/README.md](plan/README.md), hour 0.

## 3. Coverage gate — passed

Checked against the ASF catalogue on 14 November. Every place has ten calibrated (PROVISIONAL) passes on one track and frame. Step B3 in [plan/02-backend.md](plan/02-backend.md) can be skipped; fill `area.json` from this table.

| Area id | Track | Frame | Direction | Dates (2026) | Channels | File size | Box inside frame |
|---|---|---|---|---|---|---|---|
| `tonle-sap` | 83 | 8 | ascending | 06-19, 07-01, 07-13, 07-25, 08-18, 08-30, 09-11, 09-23, 10-05, 10-17 | HH+HV | 1.9 GB | Yes — the box in `plan/00-contracts.md` fits |
| `ararat` (valley and Sevan) | 85 | 22 | ascending | 06-20, 07-02, 07-14, 07-26, 08-19, 08-31, 09-12, 09-24, 10-06, 10-18 | HH+HV | 7.5 GB | Yes — valley (44.0–44.8°E, 39.8–40.3°N) and Sevan (44.9–45.7°E, 40.1–40.7°N) both fit |
| `rondonia` | 60 | 173 | ascending | ten dates 06-18 … 10-16 | HH+HV except 09-22 | 1.9 GB | Box not chosen. Frame spans about 63.2–60.4°W, 10.4–7.8°S |

Things the search showed that `plan/` could not know:

- **Tonle Sap has two files per date** on this track and frame: "Full" coverage and "Partial" coverage in a different mode. Keep only Full (`frameCoverage` field).
- **Field names in search results** (B3 marks them unconfirmed): `path` is the track, `frame`, `flightDirection`, `startTime`, `wkt` is the footprint; under `nisar`: `mainBandPolarization`, `rangeBandwidth`, `frameCoverage`, `crid`. Search filters: `relativeOrbit`, `frame`, `flightDirection`, `dataMaturity`.
- Add `productionConfiguration=["PR"]` to the search; without it urgent-response duplicates can appear.
- Sevan in two areas or one: the valley and the lake are about 150 km apart corner to corner. Two area ids from the same files (`ararat`, `sevan`) keep each image within the 3,000-pixel limit at a useful resolution.
- Other tracks tested for Armenia and Rondônia do not contain the boxes fully. Stay on the ones above.

## 4. Other blanks in `plan/`, now answered

| Where | Question | Answer |
|---|---|---|
| plan/04 §1 | Deadline | Sunday 15 November, 23:59, local time of the event (Universal Event: team owner's time zone). Resubmission allowed; last one counts |
| plan/04 §1 | Demo format | Video of 30 seconds or less, or deck of 7 slides or fewer. Public link, no login. Over the limit = no global judging |
| plan/04 §1 | Language | English; other-language video needs English subtitles |
| plan/04 §1 | Fields | Name, summary, demo link, final project link, detailed description, AI use, NASA data, partner data and references. Length limits not found |
| plan/04 §1 | AI rules | Allowed as an aid, not to come up with the solution idea. Must be disclosed; AI-made images and video watermarked; no NASA logos in AI-made content |
| plan/04 | Licence | Open-source licence file required in the repository; otherwise NASA applies Apache 2.0. Every source cited |
| plan/02 B1 | Python 3.14 | All listed libraries have ready-made builds for macOS on Apple silicon. Do not install the `GDAL` package; `rasterio` covers it |
| plan/02 B4 | File layout | Matches what B4 expects. Projection is an EPSG code; values are linear power with NaN for no data; `yCoordinates` runs north to south. Meaning of `mask` values still unknown |
| plan/02 B4 | Login method | `~/.netrc` works; a bearer token from the Earthdata profile page also works |
| plan/02 B5 | If streaming is slow | Besides downloading: NASA Harmony returns a cut-out as GeoTIFF (needs login, untested) |
| plan/03 D12 | Bad scenes | ASF's own example dropped two faulty provisional scenes. Look at every date |

These came through a mirror of the official site and are paraphrase. One person should read the submission guide in a browser and confirm; details in [submission-rules.md](docs/research/submission-rules.md).

**Shortcut for the hour-1 and hour-3 checkpoints.** Every pass has a ready-made preview picture that downloads with no login (`browse` field in the search results; about 4 MB, already in latitude and longitude). Real radar imagery can be in the app before any file has been opened. Not calibrated — pictures only, no charts or change maps.

## 5. Clock times for the checkpoints

`plan/` counts hours from the start. With a start at 10:45 Saturday and the deadline confirmed:

| Checkpoint in `plan/` | Clock time (Yerevan) |
|---|---|
| Hour 0 — decisions, logins, owners | Sat 10:45–11:30 |
| Hour 1 — demo area on `main`, empty map live | Sat 12:30 |
| Hour 3 — one real date on the live URL | Sat 14:30 |
| Hour 7 — all dates of area 1 | Sat 18:30 |
| **Hour 10 — area 1 complete** | **Sat 21:30** |
| Hour 12 — area 2 started | Sat 23:30 |
| Day 2, hour 0 | Sun 09:00 |
| Day 2, hour 3 — area 2 complete | Sun 12:00 |
| **Day 2, hour 5 — feature freeze** | **Sun 14:00** |
| Submit | Sun 21:00, leaving three hours in reserve |

## 6. Science notes that change the stories

From [science-brief.md](docs/research/science-brief.md); single-source items are marked there.

- **Armenia 2026 was a wet year.** Spring rain about 131 % of normal; Sevan rose 59 cm from January to mid-July, reported as the second-highest level on record. The story is a lake that rose and the valley it waters, not drought. Sevan readings for August–October still need finding.
- **Radar cannot see algal blooms**, and a shoreline shift from a half-metre rise may be smaller than a pixel. Showing what radar does not see is part of the answer the challenge asks for.
- **Tonle Sap 2026**: flood reported late and weak in August, rising in October. Evidence is mixed; get gauge readings before describing it.
- **Amazon 2026**: clearing alerts at the lowest level in a decade while fires rose sharply in September and October.

## 7. AI use

Space Apps allows AI tools as an aid and requires disclosure. Research for this file and the three documents under `docs/research/` was gathered with Claude. The choice of concept and places is the team's. Record AI assistance as the work goes, so the disclosure on the project page is accurate.
