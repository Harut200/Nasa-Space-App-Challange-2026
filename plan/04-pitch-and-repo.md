# Pitch deck, project page and repository

Global judges will not meet us. They see three things: the project page, the demo (deck or video), and the repository. This file plans those three so that each of the five judging criteria is answered in plain sight.

## 1. Fill this in at hour 0

We could not load the official guides while planning (certificate error). Open them in a browser and write the answers here. The right-hand column is what earlier years required; treat it as a guess until confirmed.

| Question | 2026 answer | Earlier years (unconfirmed) |
|---|---|---|
| Submission deadline, local time | ______ | 23:59 Sunday, local time |
| Demo format | ______ | A public link to **either** a slide deck of at most 7 slides **or** a video of at most 30 seconds |
| Demo language | ______ | English |
| Project page fields and length limits | ______ | High-level summary; demo link; project link; detailed description; space agency data used; references; use of AI |
| AI-use rules | ______ | Must be disclosed; AI-generated images, video or audio must be visibly marked |
| Local pitch: when, how long, live demo allowed? | ______ | Ask the local event lead |

- Submission guide: https://www.spaceappschallenge.org/resources/project-submission-guide/
- Judging guide: https://www.spaceappschallenge.org/resources/judging-awards-guide

Sections 3 and 4 assume the 7-slide limit. If the real limit differs, adjust there.

## 2. What judges score, and where we show it

The five criteria are confirmed. The descriptions are our paraphrase — replace them with the official wording when you have it.

| Criterion | What it asks | Our answer | Where a judge sees it |
|---|---|---|---|
| **Impact** | Does it solve a real problem, for many people? | Floods, forests and food. One measured number per area | Findings slide; *Why it matters* paragraph in the app |
| **Creativity** | Is the approach new? | The season as a "dance" you play and scrub, with the physics beside the map | Title slide animation; the live app |
| **Validity** | Is it scientifically sound; does it work? | One calibration tier, never mixed. Data gap drawn, not hidden. Checked against optical imagery. Limits stated | Science slide; README methods; timeline gap in the app |
| **Relevance** | Does it answer this challenge? Is it complete? | NISAR is the challenge's own dataset; the app is live and public, not a mock-up | Live URL on every slide; challenge name on the title slide |
| **Presentation** | Is the story clear? | One sentence, one animation, one number | Everything below |

**The one sentence** (agree on it at day 2, hour 0; use it unchanged on the deck, the page and the README):

> *Dancing with the SARs lets anyone press play on a season of NISAR radar images and watch a lake, a forest and a valley change — and understand why radar can see it.*

## 3. The deck — 7 slides

Owner: ______ (one person holds the pen; others supply pieces). Build in Google Slides, 16:9, dark background to match the app. Rules: at most 25 words per slide outside the title, text no smaller than 24 pt, one idea per slide, live URL in the footer of every slide.

| # | Slide | Content | Criterion | Supplied by |
|---|---|---|---|---|
| 1 | **Title** | Project name, the one sentence, an animated image of the lake growing, team name, challenge name, live URL and QR code | Creativity | frontend (animation) |
| 2 | **The problem** | Radar sees through cloud and night, and NISAR now images nearly all land every 12 days — but a radar image means nothing to most people. One grey raw image as proof | Relevance | detection |
| 3 | **What we built** | One annotated screenshot: timeline, swipe, change layer, story panel. "Live now at …" | Relevance, Presentation | frontend |
| 4 | **How it works and why you can trust it** | Four-box pipeline (NISAR → cut and align → detect change → static web app). Three trust points: one calibration tier; gap shown; checked against optical. The side-by-side figure from D12 | Validity | detection + backend |
| 5 | **What the radar showed** | One measured number per area, large, each with a small before/after pair. Example form: "Tonle Sap open water: X → Y km², June to October" | Impact | detection |
| 6 | **Why it matters** | Who uses this: teachers, journalists, local communities. One line on the Armenia area as our home ground, with a photograph we took ourselves or own the rights to | Impact | all |
| 7 | **Next, and credits** | Two next steps (more areas; ground deformation). Data credits: NASA/JPL-Caltech, ISRO, ASF DAAC. Open source, MIT. AI-use statement. Team names. Links | Presentation | deck owner |

Checks before sharing:

- Sharing set to "anyone with the link can view". Open the link in a private browser window to prove it.
- Export a PDF to `docs/pitch/deck.pdf` and commit it.
- The deck makes sense with no speaker. A teammate's friend reads it cold and says back what the project does.
- Every number on slide 5 matches the app and the README exactly.
- Animated images do not play in a PDF. If judges may receive a PDF, slide 1 must also work as a still.

## 4. Video and live pitch

**30-second video** (if the format allows either, submit the deck and put the video on the project page and README).

| Seconds | Picture | Words |
|---|---|---|
| 0–5 | Raw grey radar image | "This is a radar image from NISAR. Hard to read." |
| 5–15 | Press play; lake grows; change layer on | "Press play, and a season appears: Tonle Sap floods from X to Y square kilometres." |
| 15–22 | Swipe on the Amazon area; story panel | "Swipe to compare. Read why radar sees it." |
| 22–30 | Armenia, then URL card | "Three places, one season, open to everyone. Dancing with the SARs." |

Record at 1920×1080 from the live URL. Captions on screen — many judges watch muted.

**Live local pitch** (if there is one): same seven beats, three minutes, one speaker, a second person driving the app. Rehearse twice with a timer. Have the backup screen capture open in another tab.

## 5. Project page text

Draft in `docs/pitch/project-page.md` so all three can edit; paste into the form at the end.

- **High-level summary** — the one sentence, then three more: what it does, what data, why it matters.
- **Detailed description** — headings in this order: What it does · How it works · Data and method · What we found · Limitations · What is next · Tools used. Reuse the README text; do not write it twice.
- **Space agency data** — NISAR L2 GCOV, PROVISIONAL, via ASF DAAC, with the collection name and date range. List any other source separately with its terms.
- **References** — every source behind a number in the story panels; libraries; basemap provider.
- **Use of AI** — state plainly which AI tools were used and for what (planning documents and code assistance, at minimum). No AI-generated imagery in the app or deck.

## 6. Repository

Owner of the README: ______ (one person, to avoid edit conflicts). Do this on day 2 after the freeze, on `main`, in small pull requests.

**README, top to bottom** — a judge gives it about 30 seconds, so the first screen must contain items 1–4:

1. Project name and the one sentence.
2. **Live demo** link, and the deck link.
3. An animated image of the app playing (under 5 MB, in `assets/images/`).
4. One line: "NASA Space Apps Challenge 2026 — Dancing with the SARs".
5. What you can do in the app (four bullets) with two screenshots.
6. How it works: the data-flow diagram from [README.md](README.md), then the methods and knob table from detection.
7. Data and credits: product, tier, date range, the gap, the credit line.
8. Limitations — the paragraph from detection.
9. Reproduce: prerequisites, Earthdata login, `pip install -r requirements.txt`, `scripts/build_area.sh tonle-sap`, `cd src/app && npm install && npm run dev`. Tested on a second laptop.
10. Team, licence, AI-use statement.

**Housekeeping checklist**

- [ ] Fix the current README: spelling ("chalenge", "Dancing With The SAR's"), the duplicated introduction, the layout list that no longer matches.
- [ ] `LICENSE` file (MIT) at the root.
- [ ] Repository "About" box: description = the one sentence; website = live URL; topics `nasa-space-apps`, `nisar`, `sar`, `remote-sensing`, `maplibre`.
- [ ] Social preview image uploaded (Settings → General).
- [ ] Remove folders that stayed empty (`src/api/`, `src/components/`, `src/lib/`, and any unused `assets/` or `docs/` subfolder). Empty scaffolding reads as unfinished.
- [ ] Delete the stale remote branch `docs/project-plan` after confirming nothing in it is needed.
- [ ] Notebooks: keep only ones that run top to bottom; clear large outputs.
- [ ] No credentials anywhere in history: search the repository for `password`, `netrc`, `token`.
- [ ] `demo` area removed from `index.json`.
- [ ] Repository is public; open it in a private window.
- [ ] Tag the submitted commit: `git tag v1.0-submission && git push --tags`. No pushes to `main` between submitting and the end of judging unless the live site is broken.

## 7. Assets to capture

Frontend owner, straight after the freeze, from the live URL:

| Asset | Used in |
|---|---|
| Animated image, lake growing, about 6 seconds, 800 px wide | Slide 1, README |
| Full-app screenshot with story panel open, 1920×1080 | Slide 3, README, social preview |
| Swipe screenshot, Amazon area | Slide 5, README |
| Before/after still pair for each area | Slide 5 |
| 60-second screen capture of the full flow | Backup for the live pitch |
| 30-second cut | Section 4 |

## 8. Day-2 timeline for this work

| Hour | Task | Who |
|---|---|---|
| 0 | Agree the one sentence. Deck owner creates the seven empty slides | all |
| 0–5 | Each person drops raw material into their slides as it appears (numbers, figures) | each branch owner |
| 5 | **Freeze.** Capture assets (section 7) | frontend |
| 5–6 | Slides 2, 4, 5 final. README methods and limitations | detection |
| 5–6 | README reproduce section tested on a second laptop. Housekeeping checklist | backend |
| 6–7 | Deck assembled; project page text drafted; video cut | deck owner + frontend |
| 7–7.5 | Cold read by an outsider. Fix what they misunderstood | all |
| 7.5–8 | Whole team reads the project page once, top to bottom. Every link opened in a private window | all |
| 8 | Submit. Tag the commit | all |
