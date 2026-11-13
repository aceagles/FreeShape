# -*- coding: utf-8 -*-
###############################################################################
#  Cross recessed pan head tapping screw (ISO 7049 C, F, R), thread off.
#  Ported from FsFunctions/FSmakeSelfTappingScrew.py (makeISO7049) at workbench
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
"""ISO 7049 Standard part, and the same screw from commercial rows."""
from __future__ import annotations

import functools
import math

from freeshape import kernel as K

from . import fastener_base as fb
from . import geom
from .geom import FSFaceMaker, RevolveZ
from .tables import split_lengths, tables

POINTS = ("C", "F", "R")
STANDARDS = ("iso", "commercial")
COMMERCIAL_TABLE = "commercial_tapping_screw"
_HEAD_COLUMNS = 11      # da_max .. pene_min: the ISO 7049 dimension row


@functools.lru_cache(maxsize=64)
def _cross_recess(PH, m, h):
    """makeHCrossRecess(PH, m) moved up to z = h. It takes about 1.5 s and depends only on these three values,
    so the screws of one size share it (the Boolean cut leaves the cutter unchanged)."""
    recess = geom.makeHCrossRecess(PH, m, tables().fs_data()["iso4757def"])
    return geom.translate(recess, 0.0, 0.0, h)


def make_iso7049(point, dia, dim_row, thread_values, length):
    """makeISO7049 with fa.Thread False: a plain shank at the nominal diameter.

    `point` is "C", "F" or "R" (upstream's ISO7049-C/-F/-R), `dim_row` the ISO7049 row
    (da_max, dk_max, dk_min, k_max, k_min, r_min, r_f, PH, m_ref, pene_max, pene_min) and
    `thread_values` (P, d2, d3, rR), which upstream reads from iso1478def.
    The head's bearing face is at z = 0, the head top at z = k and the tip at z = -length.
    """
    SType = "ISO7049-" + point
    l = length
    P, d2, d3, rR = thread_values
    _, D, _, K_, _, r, _, PH, m, h, _ = dim_row

    ro = dia / 2.0  # outer thread radius
    sr = ro         # inner radius of screw section (no thread)

    # Sharpness of screw tip is 45 degrees; tip_length = sr / tan(alpha / 2)
    alpha = 45
    tip_length = sr / math.tan(math.radians(alpha / 2))
    if SType == "ISO7049-F":
        tip_length = sr - d3 / 2

    fm = FSFaceMaker()

    # 1) screw head
    fm.AddPoint(0, K_)
    fm.AddBSpline(D / 2, K_, D / 2, 0)

    # 2) rounding under screw head
    rr = r
    fm.AddPoint(ro + rr, 0)
    fm.AddArc2(+0, -rr, 90)

    # 4) tip shape
    if SType == "ISO7049-C":
        fm.AddPoint(sr, -l + tip_length)
        fm.AddPoint(0, -l)
    if SType == "ISO7049-F":
        fm.AddPoint(sr, -l + tip_length)
        fm.AddPoint(d3 / 2, -l)
        fm.AddPoint(0, -l)
    if SType == "ISO7049-R":
        fm.AddPoint(sr, -l + tip_length)
        fm.AddPoint(rR * math.cos(math.radians(alpha)), rR - l)
        fm.AddArc2(-rR * math.cos(math.radians(alpha)),
                   rR * math.sin(math.radians(alpha)), -alpha)

    screw = RevolveZ(fm.GetFace())

    # cross slot in screw head
    return geom._solid_of(K.cut(screw, _cross_recess(PH, m, h)))


class Iso7049(fb.ScrewPart):
    """ISO 7049 pan head tapping screw. Parameters: size, length, point (C, F, R) and standard (iso, commercial).

    `sizes()` and `lengths()` take the standard and the point as optional keywords, defaulting to the ISO set
    with point C, so the base-class calls `sizes()` and `lengths(size)` still work. ISO sizes depend on the
    point (the R table starts at ST3.5). `standards()` lists the standards that have sizes: "iso", plus
    "commercial" when a commercial row exists; the Insert panel shows the standard choice only when this has
    more than one entry. The commercial rows come from `tables` (a tables.Tables) when given, else from the
    shipped `data/commercial/`; the ISO sizes always come from the shipped tables.
    """
    id = "iso7049"
    label = "Pan head tapping screw ISO 7049"
    standard = "ISO 7049"

    def __init__(self, tables=None):
        self._tables = tables

    # ---- sources
    def _commercial(self):
        t = self._tables if self._tables is not None else tables()
        return t.get(COMMERCIAL_TABLE)

    def standards(self) -> list:
        """The standards that have at least one size, "iso" first."""
        return ["iso"] + (["commercial"] if self._commercial() is not None and self._commercial().rows else [])

    def sizes(self, standard="iso", point="C"):
        self._check_standard(standard)
        self._check_point(point)
        if standard == "commercial":
            t = self._commercial()
            return list(t.rows) if t is not None else []
        return fb.all_diams("ISO7049-" + point)

    def lengths(self, size, standard="iso", point="C"):
        fb._check_size(self.sizes(standard, point), size)
        if standard == "commercial":
            nominals = split_lengths(self._commercial().column(size, "lengths"))
        else:
            nominals = fb.all_lengths("ISO7049-" + point, size)
        out = []
        for s in sorted(nominals, key=fb.len_str_2_num):
            v = fb.len_str_2_num(s)
            out.append(int(v) if v == int(v) else v)
        return out

    @staticmethod
    def _check_standard(standard):
        if standard not in STANDARDS:
            raise K.KernelError(f"standard: {str(standard)[:40]!r} is not one of {', '.join(STANDARDS)}")

    @staticmethod
    def _check_point(point):
        if point not in POINTS:
            raise K.KernelError(f"point: {str(point)[:40]!r} is not one of {', '.join(POINTS)}")

    # ---- parameters
    def default_params(self):
        size = self.sizes()[0]
        return {"size": size, "length": self.lengths(size)[0], "point": "C", "standard": "iso"}

    def validate(self, params):
        standard = params.get("standard", "iso")
        point = params.get("point", "C")
        self._check_standard(standard)
        self._check_point(point)
        size = fb._check_size(self.sizes(standard, point), params.get("size"))
        length = fb._check_length(self.lengths(size, standard, point), params.get("length"))
        return {"size": size, "length": length, "point": point, "standard": standard}

    def build(self, params):
        p = self.validate(params)
        length = float(p["length"])
        if p["standard"] == "commercial":
            row = self._commercial().row_dict(p["size"])
            dim_row = self._commercial().rows[p["size"]][:_HEAD_COLUMNS]
            thread_values = (row["P"], row["d2"], row["d3"], row["rR"])
        else:
            key = "ISO7049-" + p["point"]
            dim_row = fb.dim_row(key, p["size"])
            data = tables().fs_data()["iso1478def"][p["size"]]
            thread_values = (data[0], data[3], data[5], data[8])   # P, d2_max, d3_max, r
        return make_iso7049(p["point"], fb.dia(p["size"], False), dim_row, thread_values, length)

    def build_solid(self, size, length):
        return self.build({"size": size, "length": length})

    def match_hole(self, diameter, point="C"):
        """The ISO size whose nominal diameter is 1.0 to 1.15 times smaller than `diameter` (the head's seat is
        the clearance hole in the top part); a pilot hole matches nothing. The largest nominal wins a tie."""
        from .hole_match import match_tapping
        return match_tapping(self.sizes("iso", point), diameter, lambda s: fb.dia(s, False))

    def designation(self, params):
        p = self.validate(params)
        if p["standard"] == "commercial":
            return f"Tapping screw {p['size']} x {p['length']} (commercial)"
        return f"{self.standard} {p['size']} x {p['length']} {p['point']}"
