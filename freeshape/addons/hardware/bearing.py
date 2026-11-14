"""Deep groove ball bearings as a Standard part.

Sizes and dimensions are FreeShape 2.0's (see tables.py: not checked against a standard). The solid is upstream's
recipe: the ring with a shield groove set in on both sides. Placement as upstream built it: axis along Z, sitting on
z = 0 and going up to the width B.
"""
from __future__ import annotations

from freeshape import kernel as K

from . import base
from .tables import tables

Z = (0, 0, 1)


def dims(size):
    """(bore d, outside D, width B) in mm of a designation such as "608"."""
    d, D, B = tables()["UpstreamBearingdef"].rows[size]
    return d, D, B


def build_bearing(size):
    """Deep groove ball bearing: the ring, with the shields between the rings set in a little on both sides."""
    d, D, B = dims(size)
    ring = K.cut(K.cylinder((0, 0, 0), Z, D / 2, B), K.cylinder((0, 0, -1), Z, d / 2, B + 2))
    rin, rout = d / 2 + (D - d) * 0.2, D / 2 - (D - d) * 0.16
    dep = min(0.3, B * 0.05)
    for z0 in (-1.0, B - dep):
        groove = K.cut(K.cylinder((0, 0, z0), Z, rout, dep + 1), K.cylinder((0, 0, z0 - 1), Z, rin, dep + 3))
        ring = K.cut(ring, groove)
    return ring


class DeepGrooveBearing(base.SizedPart):
    id = "deep_groove"
    label = "Deep groove ball bearing"
    category = "Bearings"
    insert_connector = "seat"

    def sizes(self):
        return list(tables()["UpstreamBearingdef"].rows)

    def build_solid(self, size):
        return build_bearing(size)

    def connectors(self, params):
        """bore: on the axis at mid-width, Z along the axis toward +Z (a shaft's connector fastened to it puts the
        shaft through the bore). seat: on the axis at the top face of the outer ring, Z into the bearing (-Z); a
        housing hole's rim fastened to it seats the ring in the hole, as a screw's under_head does."""
        _d, _D, B = dims(self.validate(params)["size"])
        return {"bore": base.frame((0, 0, B / 2), (0, 0, 1)),
                "seat": base.frame((0, 0, B), (0, 0, -1))}

    def designation(self, params):
        size = self.validate(params)["size"]
        d, D, B = dims(size)
        return f"Bearing {size} ({d:g}×{D:g}×{B:g})"

    def hole_diameters(self, size):
        """The housing bore: the outside diameter D."""
        return [float(dims(size)[1])]

    def tie_break(self, size):
        """Two bearings with the same outside diameter (625 and 688): the larger bore wins."""
        return float(dims(size)[0])
