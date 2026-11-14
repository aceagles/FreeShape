# -*- coding: utf-8 -*-
###############################################################################
#  Hex head bolt (ISO 4014) and hex head screw (ISO 4017), threads off.
#  Ported from FsFunctions/FSmakeHexHeadBolt.py (makeHexHeadBolt) at workbench
#  commit 375a73d9706a30072c132fb061e7636e8ed99f82.
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
"""ISO 4014 and ISO 4017 Standard parts."""
from __future__ import annotations

import math

from freeshape import kernel as K

from . import fastener_base as fb
from . import geom
from .geom import FSFaceMaker, RevolveZ, makeHexPrism, sqrt3


def make_hex_head_bolt(base_type, size, length):
    """makeHexHeadBolt with fa.Thread False: a plain shank at the nominal diameter.

    The head's bearing face is at z = 0, the head rises to z = k and the shank runs to z = -length.
    """
    dia = fb.dia(size, False)
    dim_table = fb.dim_row(base_type, size)
    if base_type == "ISO4017":
        P, c, dw, e, k, r, s = dim_table
        b = length
    elif base_type == "ISO4014":
        P, b1, b2, b3, c, dw, e, k, r, s = dim_table
        if length <= 125.0:
            b = b1
        elif length <= 200.0:
            b = b2
        else:
            b = b3
    else:
        raise NotImplementedError(f"Unknown fastener type: {base_type}")

    # needed for chamfer at head top
    cham = (e - s) * math.sin(math.radians(15))
    # lay out head profile
    fm = FSFaceMaker()
    fm.AddPoint(0.0, k)
    fm.AddPoint(s / 2.0, k)
    fm.AddPoint(s / sqrt3, k - cham)
    fm.AddPoint(s / sqrt3, c)
    fm.AddPoint(dw / 2.0, c)
    fm.AddPoint(dw / 2.0, 0.0)
    fm.AddPoint(dia / 2.0 + r, 0.0)
    fm.AddArc2(0.0, -r, 90)
    if length - r > b:  # partially threaded fastener
        fm.AddPoint(dia / 2, -1 * (length - b))   # (Thread is off)
    fm.AddPoint(dia / 2, -length + dia / 10)
    fm.AddPoint(dia * 4 / 10, -length)
    fm.AddPoint(0.0, -length)
    shape = RevolveZ(fm.GetFace())
    # create cutting tool for hexagon head
    extrude = makeHexPrism(s, k + length + 2)
    extrude = geom.translate(extrude, 0.0, 0.0, -length - 1)
    return geom._solid_of(K.common(shape, extrude))


def threaded_length(base_type, size, length):
    """How much of the shank at the tip is threaded, as makeHexHeadBolt decides it with fa.Thread True: the
    table's b (b1, b2 or b3 by length) for a partly threaded bolt, else length - r (up to the root fillet).
    ISO 4017 has b = length, so it is threaded all the way up to the fillet."""
    row = fb.dim_row(base_type, size)
    if base_type == "ISO4017":
        r, b = row[5], length
    else:
        r = row[8]
        b = row[1] if length <= 125.0 else row[2] if length <= 200.0 else row[3]
    return b if length - r > b else length - r


class Iso4014(fb.ThreadOption, fb.ScrewPart):
    id = "iso4014"
    label = "Hex head bolt ISO 4014"
    type_key = "ISO4014"
    standard = "ISO 4014"

    def build_solid(self, size, length):
        return make_hex_head_bolt(self.type_key, size, length)

    def add_thread(self, solid, size, length):
        return fb.screw_thread(solid, size, length, threaded_length(self.type_key, size, length))


class Iso4017(fb.ThreadOption, fb.ScrewPart):
    id = "iso4017"
    label = "Hex head screw ISO 4017"
    type_key = "ISO4017"
    standard = "ISO 4017"

    def build_solid(self, size, length):
        return make_hex_head_bolt(self.type_key, size, length)

    def add_thread(self, solid, size, length):
        return fb.screw_thread(solid, size, length, threaded_length(self.type_key, size, length))
