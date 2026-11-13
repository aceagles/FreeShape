"""Which size of a fastener Standard part fits a hole: the rules behind `match_hole` (headless).

Metric threaded types (ISO 4014, 4017, 4762, 4032, 7089) match a hole by the Hole feature's own tables,
`features.CLEARANCE` (close, normal, loose per ISO 273) and `features.TAP_DRILL`, within `TOL` mm. A size missing
from either table cannot match by that table; nothing is invented for it.

Rules for `match_metric`:
1. A clearance match beats a tap-drill match, even when the tap drill is closer.
2. Among clearance matches (two sizes' classes can overlap within TOL): the smallest absolute difference, then
   the normal class, then close, then loose, then the larger size.
3. Among tap-drill matches: the smallest absolute difference, then the larger size.

Tapping screws (ISO 7049) seat their head on the clearance hole of the top part: `match_tapping` matches a hole
of 1.0 to 1.15 times the nominal diameter (the number in the size name, as `fastener_base.dia`); a pilot hole
does not match. When a hole fits two ST sizes, the largest nominal diameter wins.
"""
from __future__ import annotations

from freeshape.features import CLEARANCE, TAP_DRILL

TOL = 0.05                  # mm, inclusive
RATIO_LO, RATIO_HI = 1.0, 1.15
_EPS = 1e-9
_CLASS_RANK = {1: 0, 0: 1, 2: 2}    # CLEARANCE tuple index (close, normal, loose) -> preference: normal first


def _diameter(diameter):
    if isinstance(diameter, bool) or not isinstance(diameter, (int, float)) or diameter != diameter:
        return None
    return float(diameter)


def match_metric(sizes, diameter):
    """The size in `sizes` whose clearance hole or tap drill is `diameter` within TOL mm, or None."""
    d = _diameter(diameter)
    if d is None:
        return None
    order = {s: i for i, s in enumerate(sizes)}
    clear = []
    for s in sizes:
        for k, v in enumerate(CLEARANCE.get(s, ())):
            diff = abs(v - d)
            if diff <= TOL + _EPS:
                clear.append((round(diff, 6), _CLASS_RANK[k], -_nominal(s), order[s], s))
    if clear:
        return min(clear)[-1]
    tap = [(round(abs(TAP_DRILL[s] - d), 6), -_nominal(s), order[s], s)
           for s in sizes if s in TAP_DRILL and abs(TAP_DRILL[s] - d) <= TOL + _EPS]
    return min(tap)[-1] if tap else None


def _nominal(size):
    """Numeric part of an "M3.5" style name, for ordering only."""
    try:
        return float(size.lstrip("M"))
    except ValueError:
        return 0.0


def match_tapping(sizes, diameter, nominal):
    """The size in `sizes` with 1.0 <= diameter / nominal(size) <= 1.15; the largest nominal wins. None if none."""
    d = _diameter(diameter)
    if d is None:
        return None
    best = None
    for s in sizes:
        n = nominal(s)
        if n > 0 and RATIO_LO - _EPS <= d / n <= RATIO_HI + _EPS and (best is None or n > best[0]):
            best = (n, s)
    return best[1] if best else None
