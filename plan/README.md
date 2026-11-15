# Work plan: three branches

> This is the plan as written on 14 November 2026, kept as it was. What differs now: the work was done on the branches named here and on others (`eda`, `detect-eda`, `detect-math`, `detect-dl`, `front_harut`, `docs/project-plan`); all were merged into `main` on 15 November and deleted, so `main` is the only branch. `PLAN.md`, `ABEL.md`, `ARAMEH.md` and `HANDOFF.md` were removed from the repository on 15 November (git history only).

Step-by-step version of PLAN.md (the first plan, removed from the repository on 15 November; it is in the git history). PLAN.md says *what* and *why*; this directory says *who does what, in which order, and how to know it is done*.

| File | Read it if you are |
|---|---|
| [00-contracts.md](00-contracts.md) | Everyone. The file formats the branches hand to each other. Read first |
| [01-frontend.md](01-frontend.md) | Owner of branch `frontend` |
| [02-backend.md](02-backend.md) | Owner of branch `backend` |
| [03-detection.md](03-detection.md) | Owner of branch `detection` |
| [04-pitch-and-repo.md](04-pitch-and-repo.md) | Everyone, from day 2. Deck, project page, repository polish |

## The three branches

| Branch | Owner | Job in one line | Only touches |
|---|---|---|---|
| `frontend` | ______ | The web app and its deployment | `src/app/` (except `src/app/public/data/`), `.github/workflows/` |
| `backend` | ______ | Get NISAR data in, get web images out | `scripts/ingest/`, `scripts/build_area.sh`, `areas/*/area.json`, `requirements.txt`, `src/app/public/data/`, `tests/test_export.py` |
| `detection` | ______ | Turn radar numbers into pictures and findings | `scripts/detect/`, `areas/*/story.json`, `notebooks/`, `tests/test_detect.py` |

**"Backend" here means the data pipeline, not a server.** PLAN.md decision 3 (static files only, nothing that can go down during judging) still holds. `src/api/` stays empty.

Data flows in a straight line. Each arrow is a format defined in [00-contracts.md](00-contracts.md):

```
NISAR GCOV (ASF)
   │  backend: search, cut, regrid
   ▼
data/processed/{area}/gamma0/…tif        ── contract A
   │  detection: filter, change, composite, colour, time series
   ▼
data/processed/{area}/products/…tif      ── contract B
   │  backend: reproject to Web Mercator, encode
   ▼
src/app/public/data/{area}/…webp + json  ── contract C
   │  frontend: map, timeline, swipe, story
   ▼
GitHub Pages
```

Because nobody edits a path owned by another branch, merges cannot conflict. If you need a change in someone else's path, ask them.

## Hour 0: together, before splitting (45 minutes)

1. Everyone: create a NASA Earthdata Login at https://urs.earthdata.nasa.gov/ and put it in `~/.netrc` (see [02-backend.md](02-backend.md) step B1).
2. Everyone: open the submission guide in a browser and write the deadline, demo format and limits into [04-pitch-and-repo.md](04-pitch-and-repo.md) section 1. We could not load that page while planning.
3. Everyone: read [00-contracts.md](00-contracts.md) aloud once. Change it now if anyone disagrees; after this it changes only when all three agree.
4. Decide whether to fix the repository name typo ("Challange") **now or never**: renaming later changes the live URL.
5. Repository owner: confirm the repository is public, then Settings → Pages → Source → **GitHub Actions**.
6. Write names in the Owner column above, commit, then create the branches:

```bash
git checkout main && git pull
for b in frontend backend detection; do git branch $b && git push -u origin $b; done
```

## Working rules

- Merge your branch into `main` by pull request **every time something works**, not at the end. `main` deploys to the live URL on every push.
- Run `git merge origin/main` into your branch at every checkpoint below, and whenever someone says "it's on main".
- `data/raw/` and `data/processed/` are not in git. To get data another person produced, pull `main` and run their script, or copy the folder from the shared drive.
- `src/app/public/data/` **is** in git: it is the product. Only the backend owner commits there.
- Never commit Earthdata credentials. `.netrc` lives in your home directory, not in the repository.
- Commit messages say what changed ("Add timeline scrubber"), because judges read the history.

## Checkpoints

Hours count from the start of hacking. These match PLAN.md section 7.

| Hour | What must be true | Who delivers |
|---|---|---|
| 1 | Coverage gate passed for area 1 (or substitute chosen). Synthetic `demo` area on `main`. Empty map live on Pages | backend, backend, frontend |
| 3 | One real NISAR date of area 1 visible on the live URL | all three, via contracts A → B → C |
| 7 | All dates of area 1 on the live URL with timeline and play | backend + frontend |
| **10** | **Area 1 complete on the live URL: timeline, swipe, change layer, story. If not, everyone stops and fixes this** | all |
| 12 | Area 2 passed the coverage gate, first images exist | backend + detection |
| Day 2, 3 | Area 2 complete, chart working | all |
| **Day 2, 5** | **Feature freeze.** After this: bug fixes, deck, README only | all |
| Day 2, 8 | Submitted, one hour before the deadline | all |

## Sources checked while writing this plan (14 November 2026)

- ASF NISAR Data User Guide: search parameters and calibration tiers: https://nisar-docs.asf.alaska.edu/asf-search, https://nisar-docs.asf.alaska.edu/availability-overview
- GCOV file layout as shown by real files: https://docs.maap-project.org/en/hub/science/NISAR/NISAR_access.html and the ASF NISAR Cookbook
- Not loaded (certificate error from our machine): the Space Apps submission guide and judging guide. Everything about them in this plan is marked **(verify)**.
