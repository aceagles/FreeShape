# -*- coding: utf-8 -*-
###############################################################################
#  Plain washer (ISO 7089).
#  Ported from FsFunctions/FSmakeWasher.py (makeWasher) at workbench commit
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
"""ISO 7089 Standard part."""
from __future__ import annotations

from OCP.gp import gp_Pnt

from . import fastener_base as fb
from . import geom
from .geom import RevolveZ


def make_washer(base_type, size):
    """makeWasher for the ISO types: the revolved rectangle d1_min/2 .. d2_max/2 by 0 .. h."""
    d1_min, d2_max, h, _ = fb.dim_row(base_type, size)
    # Washer Points
    Pnt0 = gp_Pnt(d1_min / 2.0, 0.0, h)
    Pnt2 = gp_Pnt(d2_max / 2.0, 0.0, h)
    Pnt3 = gp_Pnt(d2_max / 2.0, 0.0, 0.0)
    Pnt4 = gp_Pnt(d1_min / 2.0, 0.0, 0.0)
    edge1 = geom.make_line(Pnt0, Pnt2)
    edge2 = geom.make_line(Pnt2, Pnt3)
    edge3 = geom.make_line(Pnt3, Pnt4)
    edge4 = geom.make_line(Pnt4, Pnt0)
    aWire = geom.make_wire([edge1, edge2, edge3, edge4])
    aFace = geom.make_face(aWire)
    return RevolveZ(aFace)


class Iso7089(fb.FlatPart):
    id = "iso7089"
    category = "Washers"
    label = "Plain washer ISO 7089"
    type_key = "ISO7089"
    standard = "ISO 7089"

    def thickness(self, size):
        return float(fb.dim_row(self.type_key, size)[2])     # h

    def build_solid(self, size):
        return make_washer(self.type_key, size)
