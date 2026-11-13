"""Tables and build recipes for two Standard part families taken from upstream FreeShape 2.0
(freeshape/stdparts.py at 355fc85): deep groove ball bearings and heat-set threaded inserts. The countersunk
screws (ISO 10642) and dowel pins (ISO 8734) it also held are now Standard parts in the fasteners plugin.

This folder has no plugin.json yet, so the registry does not load it. It holds what the port to StandardPart
classes needs: the size tables below are upstream's values, copied unchanged, and each `build_*` function is
upstream's recipe for that family. Still to add for the port: a plugin.json, one StandardPart class per family
(sizes, lengths, validate, connectors, designation, match_hole) and the categories they belong to.

Placement, as upstream built them: axis along Z, sitting on z = 0, going up.
Threads are plain cylinders at the nominal diameter.
"""
from __future__ import annotations

from freeshape import kernel as K

# --------------------------------------------------------------------------- size tables (mm)
# deep-groove ball bearings: designation -> (bore d, outside D, width B)
BEARINGS = {"623": (3, 10, 4), "624": (4, 13, 5), "625": (5, 16, 5), "626": (6, 19, 6), "688": (8, 16, 5),
            "608": (8, 22, 7), "6800": (10, 19, 5), "6000": (10, 26, 8), "6001": (12, 28, 8), "6002": (15, 32, 9),
            "6003": (17, 35, 10), "6200": (10, 30, 9), "6201": (12, 32, 10), "6202": (15, 35, 11),
            "6203": (17, 40, 12), "6204": (20, 47, 14), "6205": (25, 52, 15)}
# heat-set threaded inserts for plastic (common sizes): d -> (outer diameter, length)
INSERTS = {"M2": (3.6, 3.0), "M2.5": (4.0, 4.0), "M3": (4.6, 4.0), "M4": (5.6, 5.0), "M5": (6.4, 6.0),
           "M6": (8.0, 8.0)}


# family: (label, sizes, lengths or None, colour); neither family has a length
FAMILIES = {
    "bearing": ("Deep groove ball bearing", list(BEARINGS), None, (0.62, 0.64, 0.68)),
    "insert": ("Heat-set threaded insert", list(INSERTS), None, (0.80, 0.66, 0.36)),
}


def _d(size):
    return float(str(size).lstrip("M"))


def designation(family, size):
    """Upstream's name for a part of these families."""
    if family == "bearing":
        d, D, B = BEARINGS[size]
        return f"Bearing {size} ({d}×{D}×{B})"
    if family == "insert":
        return f"Heat-set insert {size}"
    raise KeyError(family)


# --------------------------------------------------------------------------- solids
Z = (0, 0, 1)


def build_bearing(size):
    """Deep groove ball bearing: the ring, with the shields between the rings set in a little on both sides."""
    d, D, B = BEARINGS[size]
    ring = K.cut(K.cylinder((0, 0, 0), Z, D / 2, B), K.cylinder((0, 0, -1), Z, d / 2, B + 2))
    rin, rout = d / 2 + (D - d) * 0.2, D / 2 - (D - d) * 0.16
    dep = min(0.3, B * 0.05)
    for z0 in (-1.0, B - dep):
        groove = K.cut(K.cylinder((0, 0, z0), Z, rout, dep + 1), K.cylinder((0, 0, z0 - 1), Z, rin, dep + 3))
        ring = K.cut(ring, groove)
    return ring


def build_insert(size):
    """Heat-set threaded insert: a bored cylinder with two knurl bands."""
    d = _d(size)
    od, ln = INSERTS[size]
    body = K.cylinder((0, 0, 0), Z, od / 2, ln)
    for z0 in (ln * 0.25, ln * 0.6):                # knurl bands
        band = K.cut(K.cylinder((0, 0, z0), Z, od / 2 + 1, ln * 0.12),
                     K.cylinder((0, 0, z0 - 1), Z, od / 2 - 0.25, ln * 0.12 + 2))
        body = K.cut(body, band)
    return K.cut(body, K.cylinder((0, 0, -1), Z, d / 2, ln + 2))
