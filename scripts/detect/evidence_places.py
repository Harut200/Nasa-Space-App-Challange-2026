"""Config of the non-fire places that get before / after / difference evidence layers (ev_*), see evidence.py.

Channel, sign and date groups are read from each place's story (areas/<id>/story.json, docs/research/areas.md), not searched.
"""
CONFIG = {
    "rondonia": {   # story: standing forest bright in HV, clearing drops sharply between late Aug and early Oct
        "kind": "ev", "channel": "hv", "sign": -1,
        "before": ["2026-06-30", "2026-08-17", "2026-08-29"], "after": ["2026-10-04", "2026-10-16"],
        "ref_spot": "new-clearing-west", "all_patches": True, "export_mask": False, "set_default": False, "set_headline_note": False, "smooth": 5, "min_km2": 2.0, "disc_km": (1.0, 5.0),
        "ring_km": (0.5, 3.0), "thr_floor": 0.03},
    "sindh-indus": {   # story: paddies flooded (dark HH) in Jul-Aug, then very bright in Sep-Oct; compare before flooding
        "kind": "ev", "channel": "hh", "sign": +1,
        "before": ["2026-06-17", "2026-06-29", "2026-07-11"], "after": ["2026-09-21", "2026-10-03", "2026-10-15"],
        "ref_spot": "rice-west", "area_set": {"caution": "Our water map holds no open water here, while NASA's OPERA water product maps 20–200 km². Read this place for the land change, not for water.", "headline": "none"}, "all_patches": True, "min_km2": 5.0, "disc_km": (3.0, 10.0), "ring_km": (1.0, 5.0)},
    "okavango": {   # story: HH brightens in the upper delta from July and the brightening moves down the fans
        "kind": "ev", "channel": "hh", "sign": +1,
        "before": ["2026-06-26", "2026-07-08"], "after": ["2026-09-30", "2026-10-12"],
        "ref_spot": "upper-floodplain", "area_set": {"caution": "Our water map holds no open water here, while NASA's OPERA water product maps 20–200 km². Read this place for the land change, not for water.", "headline": "none"}, "all_patches": True, "min_km2": 10.0, "disc_km": (3.0, 15.0), "ring_km": (2.0, 8.0)},
    "nile-delta": {   # story: northern delta HH brightens from late June, peaks 13-25 Aug, falls back by late Sep
        "kind": "ev", "channel": "hh", "sign": +1, "before": ["2026-06-26", "2026-07-08"], "after": ["2026-08-13", "2026-08-25"],
        "ref_spot": "north-fields", "all_patches": True, "min_km2": 20.0, "disc_km": (3.0, 15.0), "ring_km": (2.0, 8.0),
        "set_default": False, "set_headline_note": False},
    "sudd": {   # story: the grassland plain brightens with new growth in August, darker by October
        "kind": "ev", "channel": "hh", "sign": +1, "before": ["2026-06-20", "2026-07-02", "2026-07-14"],
        "after": ["2026-08-19", "2026-08-31"], "ref_spot": "western-plain", "all_patches": True, "min_km2": 20.0,
        "disc_km": (3.0, 15.0), "ring_km": (2.0, 8.0), "set_default": False},
    "poyang": {   # story: the lake draws back Aug-Sep and the drained bed turns brighter; open-water area barely changes
        "kind": "ev", "channel": "hh", "sign": +1, "before": ["2026-07-05", "2026-07-17"], "after": ["2026-09-27", "2026-10-09"],
        "ref_spot": "western-lake-bed", "all_patches": True, "min_km2": 20.0, "disc_km": (3.0, 15.0), "ring_km": (2.0, 8.0),
        "set_default": False, "set_headline_note": False},
    "bangladesh-haor": {   # story: central haor stays dark; the western margin brightens late Sep / Oct as water drains
        "kind": "ev", "channel": "hh", "sign": +1, "before": ["2026-06-18", "2026-06-30", "2026-07-12"],
        "after": ["2026-10-04", "2026-10-16"], "ref_spot": "western-margin", "all_patches": True, "min_km2": 20.0,
        "disc_km": (3.0, 15.0), "ring_km": (2.0, 8.0), "set_default": False, "set_headline_note": False},
    "urmia": {   # story: shores brighten from late July as the water edge pulls back
        "kind": "ev", "channel": "hh", "sign": +1, "before": ["2026-06-20", "2026-07-02", "2026-07-14"],
        "after": ["2026-08-19", "2026-08-31", "2026-09-12"], "ref_spot": "west-shore", "all_patches": True, "min_km2": 10.0,
        "disc_km": (3.0, 15.0), "ring_km": (1.0, 5.0), "set_default": False, "set_headline_note": False},
}
