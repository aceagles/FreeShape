# -*- coding: utf-8 -*-
###############################################################################
#  Dowel pin (ISO 8734).
#  Ported from FsFunctions/FSmakeDowelPin.py (makeDowelPin) at workbench commit
#  375a73d9706a30072c132fb061e7636e8ed99f82.
#
#  Copyright (c) 2023 Alex Neufeld <alex.d.neufeld@gmail.com>
#
#  This program is free software; you can redistribute it and/or modify it
#  under the terms of the GNU Lesser General Public License (LGPL) as
#  published by the Free Software Foundation; either version 2 of the
#  License, or (at your option) any later version.
#
#  This software is distributed in the hope that it will be useful, but
#  WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Library
#  General Public License for more details.
###############################################################################
"""ISO 8734 Standard part."""
from __future__ import annotations

from . import fastener_base as fb
from . import geom, hole_match
from .geom import FSFaceMaker, RevolveZ


def make_dowel_pin(base_type, size, length):
    """makeDowelPin for ISO8734: a cylinder with a 15 degree chamfer c at both ends, from z = 0 down to z = -length."""
    if base_type != "ISO8734":
        raise NotImplementedError(f"Unknown fastener type: {base_type}")
    length = float(length)
    dia, cham = fb.dim_row(base_type, size)
    cham_d = geom.tan15 * cham
    fm = FSFaceMaker()
    fm.AddPoint(0.0, 0.0)
    fm.AddPoint(dia / 2 - cham_d, 0.0)
    fm.AddPoint(dia / 2, -cham)
    fm.AddPoint(dia / 2, cham - length)
    fm.AddPoint(dia / 2 - cham_d, -length)
    fm.AddPoint(0.0, -length)
    return geom._solid_of(RevolveZ(fm.GetFace()))


class Iso8734(fb.PinPart):
    id = "iso8734"
    label = "Dowel pin ISO 8734"
    type_key = "ISO8734"
    standard = "ISO 8734"

    def build_solid(self, size, length):
        return make_dowel_pin(self.type_key, size, length)

    def nominal_diameter(self, size):
        return float(fb.dim_row(self.type_key, fb._check_size(self.sizes(), size))[0])

    def match_hole(self, diameter):
        """The size whose diameter is `diameter` within 0.05 mm (see hole_match), or None."""
        return hole_match.match_nearest(self.sizes(), diameter, lambda s: [self.nominal_diameter(s)],
                                        self.nominal_diameter)
