# -*- coding: utf-8 -*-
###############################################################################
#  Socket head cap screw (ISO 4762), thread off.
#  Ported from FsFunctions/FSmakeCylinderHeadScrew.py (makeCylinderHeadScrew) at workbench
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
"""ISO 4762 Standard part."""
from __future__ import annotations

from freeshape import kernel as K

from . import fastener_base as fb
from . import geom
from .geom import FSFaceMaker, RevolveZ


def make_cylinder_head_screw(base_type, size, length):
    """makeCylinderHeadScrew for ISO4762 with fa.Thread False: a plain shank at the nominal diameter.

    The head's bearing face is at z = 0, the head top at z = k and the shank runs to z = -length.
    """
    if base_type != "ISO4762":
        raise NotImplementedError(f"Unknown fastener type: {base_type}")
    dia = fb.dia(size, False)
    P, b, dk_max, da, ds_mean, e, lf, k, r, s_mean, t, v, dw, w = fb.dim_row(base_type, size)
    recess = geom.makeHexRecess(s_mean, t, True)

    fm = FSFaceMaker()
    fm.AddPoint(0.0, k)
    fm.AddPoint(dk_max / 2 - v, k)
    fm.AddArc2(0.0, -v, -90)
    fm.AddPoint(dk_max / 2, (dk_max - dw) / 2)
    fm.AddPoint(dw / 2, 0.0)
    fm.AddPoint(dia / 2 + r, 0.0)
    fm.AddArc2(0.0, -r, 90)
    if length - r > b:  # partially threaded fastener
        fm.AddPoint(dia / 2, -1 * (length - b))   # (Thread is off)
    fm.AddPoint(dia / 2, -length + dia / 10)
    fm.AddPoint(dia * 4 / 10, -length)
    fm.AddPoint(0.0, -length)
    shape = RevolveZ(fm.GetFace())
    recess = geom.translate(recess, 0.0, 0.0, k)
    return geom._solid_of(K.cut(shape, recess))


class Iso4762(fb.ScrewPart):
    id = "iso4762"
    label = "Socket head cap screw ISO 4762"
    type_key = "ISO4762"
    standard = "ISO 4762"

    def build_solid(self, size, length):
        return make_cylinder_head_screw(self.type_key, size, length)
