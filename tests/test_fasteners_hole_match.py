"""match_hole on the six fastener Standard parts (I3.2)."""
import pytest

from freeshape import kernel as K
from freeshape import pyfeature
from freeshape.features import CLEARANCE, TAP_DRILL
from freeshape.pyfeature import registry  # noqa: F401  (loads the bundled plugins)
from freeshape.plugins.fasteners import hole_match

METRIC = ("iso4014", "iso4017", "iso4762", "iso4032", "iso7089")


def part(tid):
    return pyfeature.standard_part(f"sp:fasteners/{tid}")()


@pytest.mark.parametrize("tid", METRIC)
def test_every_clearance_and_tap_drill_matches_its_size(tid):
    p = part(tid)
    offered = set(p.sizes())
    checked = 0
    for size in K.ISO_PITCH:
        if size not in CLEARANCE or size not in offered:
            continue
        for d in (*CLEARANCE[size], TAP_DRILL[size]):
            assert p.match_hole(d) == size, (tid, size, d)
            assert p.match_hole(d + 0.04) == size, (tid, size, d)
            checked += 1
    assert checked > 0


def test_iso4762_every_size_with_a_clearance_entry():
    p = part("iso4762")
    sizes = [s for s in K.ISO_PITCH if s in CLEARANCE]
    assert sizes and all(s in p.sizes() for s in sizes)
    for s in sizes:
        for d in (*CLEARANCE[s], TAP_DRILL[s]):
            assert p.match_hole(d) == s


def test_examples_and_misses():
    p = part("iso4762")
    assert p.match_hole(3.4) == "M3"
    assert p.match_hole(4.5) == "M4"
    assert p.match_hole(1.0) is None
    assert p.match_hole(7.7) is None
    assert p.match_hole(float("nan")) is None
    assert p.match_hole("3.4") is None


def test_tolerance_is_0_05():
    assert hole_match.match_metric(["M3"], 3.4 + 0.049) == "M3"
    assert hole_match.match_metric(["M3"], 3.4 + 0.06) is None


def test_clearance_beats_tap_drill():
    # M2 loose 2.6 and M3 tap drill 2.5: 2.55 is 0.05 from both (clearance wins even though equal distance);
    # 2.52 is closer to the M3 tap drill (0.02) than to M2 loose (0.08, out of tolerance) -> M3 by tap drill.
    assert hole_match.match_metric(["M2", "M3"], 2.55) == "M2"
    assert hole_match.match_metric(["M2", "M3"], 2.52) == "M3"
    # 2.58: M2 loose 2.6 (0.02) clearance, M3 tap drill 2.5 (0.08, out) -> M2
    assert hole_match.match_metric(["M2", "M3"], 2.58) == "M2"
    # a tap-drill-closer case: M2.5 loose 3.1 vs M3 tap? use synthetic: M4 tap 3.3 vs M3 close 3.2, d=3.28
    assert hole_match.match_metric(["M3", "M4"], 3.28) == "M4"   # M3 close 3.2 is 0.08 out; M4 tap 3.3 fits
    assert hole_match.match_metric(["M3", "M4"], 3.25) == "M3"   # clearance 0.05 beats tap 0.05


def test_clearance_tie_break():
    # M2.5 loose 3.1 and M3 close 3.2: 3.15 is 0.05 from both -> larger size
    assert hole_match.match_metric(["M2.5", "M3"], 3.15) == "M3"
    # 3.12 is closer to M2.5 loose (0.02)
    assert hole_match.match_metric(["M2.5", "M3"], 3.12) == "M2.5"
    assert hole_match.match_metric(["M3"], 3.3) is None     # 0.1 from close and normal
    # equal distance, normal class beats close/loose of another size: M4 close 4.3, M3 loose 3.6 are far; use
    # synthetic table values through M6 normal 6.6 vs M5 loose 5.8 -> no overlap. Order check on classes:
    assert hole_match._CLASS_RANK[1] < hole_match._CLASS_RANK[0] < hole_match._CLASS_RANK[2]


def test_only_offered_sizes_are_returned():
    assert hole_match.match_metric(["M4"], 3.4) is None
    assert hole_match.match_metric([], 3.4) is None


def test_representative_diameters():
    p = part("iso4762")
    got = {d: p.match_hole(d) for d in (3.4, 2.5, 4.5, 5.0, 6.6)}
    assert got == {3.4: "M3", 2.5: "M3", 4.5: "M4", 5.0: "M6", 6.6: "M6"}


def test_washer_and_nut_follow_the_same_rule():
    assert part("iso7089").match_hole(3.4) == "M3"
    assert part("iso4032").match_hole(2.5) == "M3"


def test_iso7049_acceptance():
    p = part("iso7049")
    assert p.match_hole(3.2) == "ST2.9"
    assert p.match_hole(2.3) == "ST2.2"
    assert p.match_hole(2.0) is None


def test_iso7049_range_and_pilot():
    p = part("iso7049")
    assert p.match_hole(2.9) == "ST2.9"             # ratio 1.0, inclusive
    assert p.match_hole(2.9 * 1.15) == "ST2.9"      # ratio 1.15, inclusive
    assert p.match_hole(2.9 * 1.16) is None
    assert p.match_hole(2.9 * 0.99) is None
    assert p.match_hole(2.5) == "ST2.2"             # 1.136 x 2.2; it is no pilot match for ST2.9 (0.86 x)
    assert p.match_hole(2.4) == "ST2.2"
    assert p.match_hole(2.2 * 0.99) is None
    assert p.match_hole(float("nan")) is None


def test_iso7049_overlap_picks_largest_nominal():
    nominal = {"ST2.9": 2.9, "ST3.3": 3.3}.get
    assert hole_match.match_tapping(["ST2.9", "ST3.3"], 3.3, nominal) == "ST3.3"
    assert hole_match.match_tapping(["ST2.9", "ST3.3"], 3.2, nominal) == "ST2.9"


def test_iso7049_nominal_is_the_generator_diameter():
    from freeshape.plugins.fasteners import fastener_base as fb
    p = part("iso7049")
    for s in p.sizes():
        assert p.match_hole(fb.dia(s, False)) is not None
