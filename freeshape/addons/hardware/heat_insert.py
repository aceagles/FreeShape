# -*- coding: utf-8 -*-
###############################################################################
#  Heat-set threaded insert (IUT), thread off.
#  Ported from FsFunctions/FSmakeHeatInsert.py (iutMakeFace, makeHeatInsert) at
#  workbench commit 375a73d9706a30072c132fb061e7636e8ed99f82.
#
#  Copyright (c) 2022 Shai Seger <shaise at gmail>
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
"""Heat-set threaded insert as a Standard part.

Dimensions are the workbench's IUTHeatInsertdef table (columns D, Length, ExtDia, C, S1, S2). The workbench draws the
insert from a revolved profile with two knurled bands, `Length` long, its top face at z = 0 and the insert running
toward -Z. With the thread off the bore is a plain hole at the minor diameter (makeHeatInsert's `iD`), and the knurl
cutters, which only the threaded model uses, are left out.
"""
from __future__ import annotations

import math

from freeshape.addons.fasteners import geom
from freeshape.addons.fasteners.geom import FSFaceMaker, RevolveZ

from . import base
from .tables import tables


def dim_row(size) -> dict:
    """The IUTHeatInsertdef row of `size`: D, Length, ExtDia, C, S1, S2."""
    return tables()["IUTHeatInsertdef"].row_dict(size)


def nominal_diameter(size) -> float:
    return float(size.lstrip("M"))


def minor_diameter(size) -> float:
    """Screw.GetInnerThreadMinDiameter: dia - 5H/4 + 0.001, H = P cos 30, P the ISO 262 coarse pitch."""
    pitch = tables()["ISO262def"].column(size, "P_coarse")
    return nominal_diameter(size) - pitch * geom.cos30 * 5.0 / 4.0 + 0.001


def make_heat_insert(size):
    """makeHeatInsert with fa.Thread False, at the table's length and external diameter."""
    row = dim_row(size)
    A, E, C, s1, s2 = row["Length"], row["ExtDia"], row["C"], row["S1"], row["S2"]
    d = minor_diameter(size) / 2
    e = E / 2
    c = C / 2                      # (extDiam - E + C) / 2 with extDiam = E
    c1 = c - d / 10
    ch = c / 3
    k1 = -(A - ch - s1 - s2) / 2
    k2 = k1 - s1
    k4 = k1 * 2 - s1
    k3 = k4 + (e - c1)
    k5 = k4 - s2
    fm = FSFaceMaker()
    fm.AddPoints((d, 0), (e, 0), (e, k1), (c, k1), (c, k2), (e, k2), (e, k3), (c1, k4), (c1, k5), (c, k5),
                 (c, -A), (d, -A))
    return geom._solid_of(RevolveZ(fm.GetFace()))


class HeatSetInsert(base.SizedPart):
    id = "heat_insert"
    label = "Heat-set threaded insert"
    category = "Inserts"
    insert_connector = "top"

    def sizes(self):
        return list(tables()["IUTHeatInsertdef"].rows)

    def build_solid(self, size):
        return make_heat_insert(size)

    def connectors(self, params):
        """top: on the axis at the top face, Z toward the tip (-Z), as a pin's top."""
        self.validate(params)
        return {"top": base.frame((0, 0, 0), (0, 0, -1))}

    def designation(self, params):
        return f"Heat-set insert {self.validate(params)['size']}"

    def hole_diameters(self, size):
        """The installation hole: the table's D column."""
        return [float(dim_row(size)["D"])]

    def tie_break(self, size):
        return nominal_diameter(size)
