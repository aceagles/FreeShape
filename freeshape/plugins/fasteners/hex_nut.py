# -*- coding: utf-8 -*-
###############################################################################
#  Hex nut (ISO 4032), threads off.
#  Ported from FsFunctions/FSmakeHexNut.py (makeHexNut) at workbench commit
#  375a73d9706a30072c132fb061e7636e8ed99f82.
#
#  Copyright 2015 Shai Seger <shaise at gmail dot com>
#  Copyright (c) 2013, 2014, 2015 Ulrich Brammer <ulrich1a[at]users.sourceforge.net>
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 2 of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software
#  Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
#  MA 02110-1301, USA.
###############################################################################
"""ISO 4032 Standard part."""
from __future__ import annotations

import math

from freeshape import kernel as K

from . import fastener_base as fb
from . import geom
from .geom import FSFaceMaker, RevolveZ, cos30, makeHexPrism, sqrt3


def make_hex_nut(base_type, size):
    """makeHexNut with fa.Thread False: a plain bore. The nut spans z = 0 .. m."""
    dia = fb.dia(size, True)
    P, _, da, _, _, m, _, s = fb.dim_row(base_type, size)
    da = geom.getDia(da, True, fb.tables().fs_data()["DiaList"])
    # needed for chamfer at nut top
    cham = s * (sqrt3 / 3 - 1 / 2) * math.tan(math.radians(22.5))
    H = P * cos30
    cham_i_delta = da / 2.0 - (dia / 2.0 - H * 5.0 / 8.0)
    cham_i = cham_i_delta * math.tan(math.radians(15.0))
    # layout the nut profile, then create a revolved solid
    fm = FSFaceMaker()
    fm.AddPoint(dia / 2.0 - H * 5.0 / 8.0, m - cham_i)
    fm.AddPoint(da / 2.0, m)
    fm.AddPoint(s / 2.0, m)
    fm.AddPoint(s / sqrt3, m - cham)
    fm.AddPoint(s / sqrt3, cham)
    fm.AddPoint(s / 2.0, 0.0)
    fm.AddPoint(da / 2.0, 0.0)
    fm.AddPoint(dia / 2.0 - H * 5.0 / 8.0, 0.0 + cham_i)
    head = RevolveZ(fm.GetFace())
    # create cutting tool for hexagon head
    extrude = makeHexPrism(s, m)
    return geom._solid_of(K.common(head, extrude))


class Iso4032(fb.FlatPart):
    id = "iso4032"
    category = "Nuts"
    label = "Hex nut ISO 4032"
    type_key = "ISO4032"
    standard = "ISO 4032"

    def thickness(self, size):
        return float(fb.dim_row(self.type_key, size)[5])     # m

    def build_solid(self, size):
        return make_hex_nut(self.type_key, size)
