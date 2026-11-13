"""Tables and build recipes for four Standard part families taken from upstream FreeShape 2.0
(freeshape/stdparts.py at 355fc85): countersunk socket screws (ISO 10642), dowel pins (ISO 8734), deep groove
ball bearings and heat-set threaded inserts.

This folder has no plugin.json yet, so the registry does not load it. It holds what the port to StandardPart
classes needs: the size tables below are upstream's values, copied unchanged, and each `build_*` function is
upstream's recipe for that family. Still to add for the port: a plugin.json, one StandardPart class per family
(sizes, lengths, validate, connectors, designation, match_hole) and the categories they belong to.

Placement, as upstream built them:
* countersunk screw: axis along Z, the top of the head on z = 0 (flush in the part), shank going down (-Z);
* dowel pin, bearing, insert: axis along Z, sitting on z = 0, going up.
Threads are plain cylinders at the nominal diameter.
"""
from __future__ import annotations

import math

from freeshape import kernel as K

# --------------------------------------------------------------------------- size tables (mm)
# ISO 10642 countersunk socket screw: d -> (head diameter dk, socket s, socket depth t); 90° head
ISO10642 = {"M3": (6.72, 2.0, 1.1), "M4": (8.96, 2.5, 1.5), "M5": (11.2, 3.0, 1.9), "M6": (13.44, 4.0, 2.2),
            "M8": (17.92, 5.0, 3.0), "M10": (22.4, 6.0, 3.6), "M12": (26.88, 8.0, 4.3)}
# ISO 8734 dowel pin: diameters (mm)
ISO8734 = ["2", "2.5", "3", "4", "5", "6", "8", "10", "12"]
# deep-groove ball bearings: designation -> (bore d, outside D, width B)
BEARINGS = {"623": (3, 10, 4), "624": (4, 13, 5), "625": (5, 16, 5), "626": (6, 19, 6), "688": (8, 16, 5),
            "608": (8, 22, 7), "6800": (10, 19, 5), "6000": (10, 26, 8), "6001": (12, 28, 8), "6002": (15, 32, 9),
            "6003": (17, 35, 10), "6200": (10, 30, 9), "6201": (12, 32, 10), "6202": (15, 35, 11),
            "6203": (17, 40, 12), "6204": (20, 47, 14), "6205": (25, 52, 15)}
# heat-set threaded inserts for plastic (common sizes): d -> (outer diameter, length)
INSERTS = {"M2": (3.6, 3.0), "M2.5": (4.0, 4.0), "M3": (4.6, 4.0), "M4": (5.6, 5.0), "M5": (6.4, 6.0),
           "M6": (8.0, 8.0)}

SCREW_LENGTHS = [3, 4, 5, 6, 8, 10, 12, 14, 16, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 80, 90, 100, 110, 120]
PIN_LENGTHS = [4, 5, 6, 8, 10, 12, 14, 16, 18, 20, 24, 30, 35, 40, 45, 50, 60]


# family: (label, sizes, lengths or None, colour)
FAMILIES = {
    "iso10642": ("Countersunk socket screw — ISO 10642", list(ISO10642), SCREW_LENGTHS, (0.25, 0.26, 0.28)),
    "iso8734": ("Dowel pin — ISO 8734", ISO8734, PIN_LENGTHS, (0.70, 0.72, 0.75)),
    "bearing": ("Deep groove ball bearing", list(BEARINGS), None, (0.62, 0.64, 0.68)),
    "insert": ("Heat-set threaded insert", list(INSERTS), None, (0.80, 0.66, 0.36)),
}


def _d(size):
    return float(str(size).lstrip("M"))


def lengths_for(family, size):
    """Lengths that make sense for a screw / pin of this size (1.5x to 12x the diameter, within the standard
    range), or None for parts without a length."""
    lens = FAMILIES[family][2]
    if lens is None:
        return None
    d = _d(size)
    ok = [L for L in lens if 1.5 * d <= L <= max(12 * d, 20)]
    return ok or lens


def designation(family, size, length=None):
    """Upstream's name for a part of these families."""
    if family == "bearing":
        d, D, B = BEARINGS[size]
        return f"Bearing {size} ({d}×{D}×{B})"
    if family == "insert":
        return f"Heat-set insert {size}"
    if family == "iso8734":
        return f"ISO 8734 dowel Ø{size} × {length:g}"
    return f"ISO 10642 {size} × {length:g}" if length else f"ISO 10642 {size}"


# --------------------------------------------------------------------------- solids
def _prism(pts, z0, h):
    """A straight prism from a polygon in XY, from z0 up h."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Pnt, gp_Vec
    poly = BRepBuilderAPI_MakePolygon()
    for x, y in pts:
        poly.Add(gp_Pnt(float(x), float(y), float(z0)))
    poly.Close()
    face = BRepBuilderAPI_MakeFace(poly.Wire(), True).Face()
    return BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, float(h))).Shape()


def _hex_prism(af, h, z0=0.0, turn=0.0):
    """A hexagon prism, `af` across the flats, from z0 up h."""
    r = af / math.sqrt(3)                               # corner radius
    pts = [(r * math.cos(math.radians(60 * i + turn)), r * math.sin(math.radians(60 * i + turn)))
           for i in range(6)]
    return _prism(pts, z0, h)


def _cone(r0, r1, z0, h):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone
    from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
    return BRepPrimAPI_MakeCone(gp_Ax2(gp_Pnt(0, 0, z0), gp_Dir(0, 0, 1)), r0, r1, h).Shape()


Z = (0, 0, 1)


def build_iso10642(size, length):
    """Countersunk socket screw: a 90 degree head with its top on z = 0, hex socket, shank to z = -length."""
    d = _d(size)
    dk, s, t = ISO10642[size]
    k = (dk - d) / 2                                # 90° head
    head = _cone(d / 2, dk / 2, -k, k)
    shape = K.fuse(head, K.cylinder((0, 0, -length), Z, d / 2, length - k))
    return K.cut(shape, _hex_prism(s, t + 0.01, -t))


def build_iso8734(size, length):
    """Dowel pin: a plain cylinder from z = 0 up `length`."""
    return K.cylinder((0, 0, 0), Z, float(size) / 2, length)


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
