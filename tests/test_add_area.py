"""Selection logic and geometry of scripts/ingest/add_area.py on synthetic search results (no network)."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.ingest import add_area as A  # noqa: E402

SQUARE = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]
# a slanted quadrilateral like a real footprint
SLANT = [(0.3, 0.0), (2.3, 0.3), (2.0, 2.3), (0.0, 2.0)]


def wkt(poly):
    pts = poly + [poly[0]]
    return "POLYGON ((" + ", ".join(f"{x} {y}" for x, y in pts) + "))"


def raw(track, frame, date, poly, pol=("HH", "HV"), bw="20+5", cov="Full", crid="P05023", d="ASCENDING"):
    return {"path": track, "frame": frame, "flightDirection": d, "startTime": f"{date}T10:00:00Z",
            "wkt": wkt(poly), "nisar": {"mainBandPolarization": list(pol), "rangeBandwidth": [bw],
                                        "frameCoverage": cov, "crid": crid}}


DATES = [f"2026-{m:02d}-{d:02d}" for m, d in
         [(6, 19), (7, 1), (7, 13), (7, 25), (8, 18), (8, 30), (9, 11), (9, 23), (10, 5), (10, 17)]]


def test_point_in_poly_and_box_inside():
    assert A.point_in_poly(1, 1, SQUARE)
    assert not A.point_in_poly(3, 1, SQUARE)
    assert A.box_inside((0.5, 0.5, 1.5, 1.5), SQUARE)
    assert not A.box_inside((0.5, 0.5, 1.99, 1.5), SQUARE)        # margin pushes it out
    assert A.box_inside((0.5, 0.5, 1.99, 1.5), SQUARE, margin=0)
    assert not A.box_inside((0.05, 0.05, 1.0, 1.0), SLANT)        # corner outside slanted edge


def test_parse_wkt_drops_closing_point():
    assert A.parse_wkt(wkt(SQUARE)) == SQUARE


def test_box_share():
    assert A.box_share((0, 0, 1, 1), [SQUARE]) == 1.0
    assert abs(A.box_share((1, 0, 3, 2), [SQUARE]) - 0.5) < 0.02


def test_largest_subbox_fits_and_respects_min_side():
    sub = A.largest_subbox((1.0, 0.5, 3.0, 1.5), [SQUARE], min_side=0.5)
    assert sub is not None
    w, s, e, n = sub
    assert A.box_inside(sub, SQUARE)
    assert e - w > 0.85 and abs(w - 1.0) < 1e-6           # keeps the part that is inside
    assert A.largest_subbox((1.9, 0.5, 3.0, 1.5), [SQUARE], min_side=0.8) is None


def test_largest_subbox_inside_every_date():
    shifted = [(x + 0.4, y) for x, y in SQUARE]
    sub = A.largest_subbox((0.0, 0.0, 2.4, 2.0), [SQUARE, shifted], min_side=0.5)
    assert sub and A.box_inside(sub, SQUARE) and A.box_inside(sub, shifted)


def test_grouping_counts_full_dual_pol_single_mode():
    res = [raw(10, 5, d, SQUARE) for d in DATES[:8]]
    res += [raw(10, 5, DATES[8], SQUARE, pol=("HH",))]                 # HH-only: not counted
    res += [raw(10, 5, DATES[0], SQUARE, cov="Partial", bw="40+5")]    # partial twin: ignored
    res += [raw(10, 5, DATES[9], SQUARE, bw="40+5")]                   # other mode: not counted
    res += [raw(10, 5, DATES[1], SQUARE, crid="P05024")]               # reprocessed duplicate
    g = A.summarise([A.record(r) for r in res], (0.5, 0.5, 1.5, 1.5))
    assert len(g) == 1
    g = g[0]
    assert g["n"] == 8 and g["mode"] == "20+5" and g["inside"]
    assert g["dates"] == DATES[:8]
    assert g["other_mode"] == 1 and g["hh_only_or_partial"] == 1


def test_choose_prefers_inside_then_most_dates():
    box = (0.5, 0.5, 1.5, 1.5)
    res = [raw(1, 1, d, SQUARE) for d in DATES[:7]]                     # inside, 7 dates
    res += [raw(2, 2, d, SQUARE) for d in DATES]                        # inside, 10 dates
    res += [raw(3, 3, d, [(x + 1.2, y) for x, y in SQUARE]) for d in DATES]  # 10 dates, not inside
    res += [raw(4, 4, d, SQUARE) for d in DATES[:5]]                    # too few dates
    g, used, note = A.choose(A.summarise([A.record(r) for r in res], box), box)
    assert (g["track"], g["frame"]) == (2, 2) and used == box and note == ""


def test_choose_shrinks_when_nothing_contains_box():
    box = (1.0, 0.5, 3.0, 1.5)
    res = [raw(7, 9, d, SQUARE) for d in DATES]
    g, used, note = A.choose(A.summarise([A.record(r) for r in res], box), box)
    assert g["track"] == 7 and "shrunk" in note
    assert A.box_inside(used, SQUARE) and used[2] - used[0] >= A.MIN_SIDE


def test_choose_fails_cleanly():
    box = (5.0, 5.0, 6.0, 6.0)
    res = [raw(7, 9, d, SQUARE) for d in DATES]
    g, used, note = A.choose(A.summarise([A.record(r) for r in res], box), box)
    assert g is None and "no track/frame" in note


def test_suspect_previews_flags_small_files(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "ROOT", tmp_path)
    folder = tmp_path / "data/raw/previews/T1_F2"
    folder.mkdir(parents=True)
    sizes = {DATES[0]: 4000, DATES[1]: 4300, DATES[2]: 2100, DATES[3]: 4100, DATES[4]: 3300}
    for d, n in sizes.items():
        (folder / f"{d}.png").write_bytes(b"x" * n)
    area = {"track": 1, "frame": 2, "dates": list(sizes)}
    assert A.suspect_previews(area) == sorted([DATES[2], DATES[4]])
