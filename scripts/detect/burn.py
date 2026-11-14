"""Burn-scar evidence for the fire places: layers burn_before / burn_after / burn_diff / burn_scar, an inside / outside
series and a `burn` block, built by scripts/detect/evidence.py (read its docstring for the method).

    .venv/bin/python -m scripts.detect.burn <area> [--no-mask] [--dry]

Channel and sign come from the documented evidence (docs/research/events-2026.md), not from a search. Sources:
docs/research/story-sources.md. Method, numbers and caveats: docs/eda/burn.md.
"""
import sys

from scripts.detect.evidence import main

CONFIG = {
    "thunder-bay-36-2026": {   # HH rose; appears between 11 and 23 Jul, persists. Fire started 12 Jul 2026 (Wikipedia)
        "kind": "burn", "channel": "hh", "sign": +1, "ignition": "2026-07-12", "ref_spot": "published-fire",
        "excluded": ["2026-09-21"],   # frame-wide dip on this pass (the unburned ring drops too)
        "published": {"burnt_km2": 3139.3, "text": "about 314,000 ha by 29-30 Jul (Wikipedia, Fort Frances Times)"},
        "disc_km": (3.0, 25.0), "ring_km": (3.0, 12.0), "min_km2": 50.0},
    "la-mierla-fire-2026": {   # HV darker 0.05-0.09; fire started 16 Jul 2026 (The Local, El Debate)
        "kind": "burn", "channel": "hv", "sign": -1, "ignition": "2026-07-16", "ref_spot": "scar-core",
        "excluded": ["2026-10-06"],   # whole frame jumps on this pass (ring +0.09 too), QC warn on both channels
        "published": {"burnt_km2": 320.0, "text": "about 32,000 ha (The Local 20 Jul, elDiario.es 30 Jul)"}},
    "avila-burgohondo-fire-2026": {   # HV dark band east of Navaluenga; no opened source gives the start date
        "kind": "burn", "channel": "hv", "sign": -1, "ignition": None, "ignition_between": ["2026-07-21", "2026-09-19"],
        "ref_spot": "scar-east-of-navaluenga", "excluded": ["2026-08-26"], "disc_km": (3.0, 15.0), "min_km2": 30.0,
        "published": {"burnt_km2": 550.0, "text": "about 50,000-55,000 ha on 26-30 Jul (El Espanol, El Debate)"}},
    "gironde-megafire-2026": {  # re-examination only; no water-pipeline products to add layers to
        "kind": "burn", "channel": "hv", "sign": -1, "ignition": "2026-07-22", "ref_lonlat": [-1.02, 44.85],
        "published": {"burnt_km2": 420.0, "text": "about 42,000 ha by 30 Jul (Paris Match Belgium)"}},
}

if __name__ == "__main__":
    main(sys.argv[1:], CONFIG)
