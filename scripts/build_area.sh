#!/usr/bin/env bash
# Whole pipeline for one area: scripts/build_area.sh tonle-sap
set -e
cd "$(dirname "$0")/.."
python scripts/ingest/search.py "$1"
python scripts/ingest/extract.py "$1"
python scripts/detect/run.py "$1"
python scripts/ingest/export.py "$1"
