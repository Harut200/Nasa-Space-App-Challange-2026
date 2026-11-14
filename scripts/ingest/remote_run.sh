#!/usr/bin/env bash
# Run scripts/ingest/remote_extract.py on the team server and bring back small results.
#
#   scripts/ingest/remote_run.sh [all|start|status|fetch] AREA[:PIXEL][,AREA[:PIXEL]...] [extractor args]
#
#   all (default)  copy script + job to the server, start it (nohup), poll until done, fetch
#   start          copy + start only, return at once
#   status         show the tail of the server log and whether the job still runs
#   fetch          copy results back and convert to contract A GeoTIFFs
#
# Several areas on the same track/frame share one job (each file opened once), e.g.
#   scripts/ingest/remote_run.sh sevan,ararat:20 --dates 2026-06-20
# Results: data/processed/<area>-cal/{gamma0,nlooks}/{hh,hv}/<date>.tif, grid.json, summary.json
#          data/raw/<area>-cal/{npz/,passes.json,b4_report.txt,<job>.log}
# Server: set SERVER=user@host and SSH_KEY=/path/to/private/key in the environment (no defaults are kept here).
set -euo pipefail
MODE=all
case "${1:-}" in all|start|status|fetch) MODE=$1; shift;; esac
SPEC=${1:?usage: remote_run.sh [all|start|status|fetch] AREA[:PIXEL][,AREA...] [args]}; shift
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
PY="$ROOT/.venv/bin/python"
SERVER=${SERVER:?set SERVER=user@host}
KEY=${SSH_KEY:?set SSH_KEY=/path/to/private/key}
OPTS=(-o IdentitiesOnly=yes -i "$KEY" -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=20 -o LogLevel=ERROR)
JOB=${JOB:-$(echo "$SPEC" | sed 's/:[0-9]*//g; s/,/+/g')}
rsh() { ssh "${OPTS[@]}" "$SERVER" "$@"; }
STAGE="$ROOT/data/raw/_remote/$JOB"
mkdir -p "$STAGE"

if [[ $MODE == all || $MODE == start ]]; then
  "$PY" - "$ROOT" "$SPEC" "$JOB" > "$STAGE/job.json" <<'EOF'
import json, sys
root, spec, job = sys.argv[1:]
wins, key = [], None
for item in spec.split(","):
    name, _, px = item.partition(":")
    a = json.load(open(f"{root}/areas/{name}/area.json"))
    k = (a["track"], a["frame"], a["direction"])
    assert key in (None, k), f"{name} is on {k}, not {key}: run it as a separate job"
    key = k
    wins.append({"id": name, "bbox": a["bbox"], "pixel_m": int(px or a["pixel_m"])})
print(json.dumps({"id": job, "track": key[0], "frame": key[1], "direction": key[2], "windows": wins}, indent=1))
EOF
  if rsh "pgrep -f '[r]emote_extract.py' >/dev/null"; then
    echo "a remote_extract job is already running on the server; not starting another (one at a time)"; exit 1
  fi
  rsh "mkdir -p ~/work/jobs ~/work/logs ~/work/out"
  scp -q "${OPTS[@]}" "$ROOT/scripts/ingest/remote_extract.py" "$SERVER:work/remote_extract.py"
  scp -q "${OPTS[@]}" "$STAGE/job.json" "$SERVER:work/jobs/$JOB.json"
  ARGS=""; [[ $# -gt 0 ]] && ARGS=$(printf ' %q' "$@")
  rsh -n "cd ~/work; setsid nohup venv/bin/python -u remote_extract.py jobs/$JOB.json --out out/$JOB $ARGS >> logs/$JOB.log 2>&1 < /dev/null & echo started job $JOB pid \$!"
fi

if [[ $MODE == status ]]; then
  rsh "pgrep -af '[r]emote_extract.py' || echo 'not running'; tail -n 15 ~/work/logs/$JOB.log"
  exit 0
fi

if [[ $MODE == all ]]; then
  while rsh "pgrep -f '[r]emote_extract.py jobs/$JOB.json' >/dev/null"; do
    rsh "tail -n 1 ~/work/logs/$JOB.log" || true
    sleep 60
  done
  rsh "tail -n 5 ~/work/logs/$JOB.log"
fi

if [[ $MODE == all || $MODE == fetch ]]; then
  # Copy only files that are missing or changed in size; skip files written in the last minute
  # (a date still being extracted). The link is slow, so never copy a finished date twice.
  mkdir -p "$STAGE/out"
  rsh "cd ~/work/out/$JOB && find . -type f -mmin +1 -printf '%s\t%P\n'" > "$STAGE/remote.lst"
  while IFS=$'\t' read -r size path; do
    loc="$STAGE/out/$path"
    if [[ -f $loc && $(stat -f %z "$loc" 2>/dev/null || stat -c %s "$loc") == "$size" ]]; then continue; fi
    mkdir -p "$(dirname "$loc")"
    echo "  fetching $path ($((size / 1024)) KB)"
    scp -q "${OPTS[@]}" "$SERVER:work/out/$JOB/$path" "$loc.part" < /dev/null && mv "$loc.part" "$loc"
  done < "$STAGE/remote.lst"
  scp -q "${OPTS[@]}" "$SERVER:work/logs/$JOB.log" "$STAGE/" || true
  for d in "$STAGE"/out/*/; do
    w=$(basename "$d")
    raw="$ROOT/data/raw/$w-cal"; mkdir -p "$raw/npz"
    cp "$d"/*.npz "$d"/grid.json "$raw/npz/" 2>/dev/null || true
    cp "$d"/summary.json "$raw/npz/" 2>/dev/null || true
    for f in passes.json b4_report.txt; do [[ -f "$STAGE/out/$f" ]] && cp "$STAGE/out/$f" "$raw/"; done
    [[ -f "$STAGE/$JOB.log" ]] && cp "$STAGE/$JOB.log" "$raw/"
    "$PY" "$ROOT/scripts/ingest/remote_extract.py" --to-geotiff "$raw/npz" "$ROOT/data/processed/$w-cal"
  done
  du -sh "$ROOT"/data/processed/*-cal 2>/dev/null || true
fi
