# -*- coding: utf-8 -*-
###############################################################################
#  Hexagon socket countersunk head screw (ISO 10642), thread off.
#  Ported from FsFunctions/FSmakeCountersunkHeadScrew.py (makeCountersunkHeadScrew,
#  the ISO10642 branch) at workbench commit 375a73d9706a30072c132fb061e7636e8ed99f82.
#
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
"""ISO 10642 Standard part."""
from __future__ import annotations

import math

from freeshape import kernel as K

from . import fastener_base as fb
from . import geom, hole_match
from .geom import FSFaceMaker, RevolveZ


def make_countersunk_head_screw(base_type, size, length):
    """makeCountersunkHeadScrew for ISO10642 with fa.Thread False: a plain shank at the nominal diameter.

    The head top is at z = 0 and the shank runs to z = -length. The head's 90 degree cone would reach the
    theoretical diameter dk_theo at z = 0 if it were not cut off at the mean head diameter dk_mean.
    """
    if base_type != "ISO10642":
        raise NotImplementedError(f"Unknown fastener type: {base_type}")
    length = float(length)
    dia = fb.dia(size, False)
    csk_angle = math.radians(90)
    P, b, dk_theo, dk_mean, _, _, _, _, r, s_mean, t, _ = fb.dim_row(base_type, size)
    recess = geom.makeHexRecess(s_mean, t, True)
    head_flat_ht = (dk_theo - dk_mean) / 2 / math.tan(csk_angle / 2)
    sharp_corner_ht = -1 * (head_flat_ht + (dk_mean - dia) / (2 * math.tan(csk_angle / 2)))
    fillet_start_ht = sharp_corner_ht - r * math.tan(csk_angle / 4)

    fm = FSFaceMaker()
    fm.AddPoint(0.0, -length)
    fm.AddPoint(dia * 4 / 10, -length)
    fm.AddPoint(dia / 2, -length + dia / 10)
    if length + fillet_start_ht > b:  # partially threaded fastener
        fm.AddPoint(dia / 2, -length + b)     # (Thread is off)
    fm.AddPoint(dia / 2, fillet_start_ht)
    fm.AddArc2(r, 0.0, -math.degrees(csk_angle / 2))
    fm.AddPoint(dk_mean / 2, -head_flat_ht)
    fm.AddPoint(dk_mean / 2, 0.0)
    fm.AddPoint(0.0, 0.0)
    shape = RevolveZ(fm.GetFace())
    return geom._solid_of(K.cut(shape, recess))


class Iso10642(fb.ScrewPart):
    id = "iso10642"
    label = "Countersunk socket screw ISO 10642"
    type_key = "ISO10642"
    standard = "ISO 10642"

    def build_solid(self, size, length):
        return make_countersunk_head_screw(self.type_key, size, length)

    def match_hole(self, diameter):
        """The size whose countersink is `diameter` wide at the surface, within 0.05 mm (see hole_match). The
        candidates are the Hole feature's own countersink diameter (`features.COUNTERSINK`) and the standard's
        theoretical head diameter dk_theo. The under_head connector sits in that surface plane."""
        from freeshape.features import COUNTERSINK
        return hole_match.match_nearest(
            self.sizes(), diameter,
            lambda s: [v for v in (COUNTERSINK.get(s), fb.dim_row(self.type_key, s)[2]) if v is not None],
            fb.dia)
