# -*- coding: utf-8 -*-
###############################################################################
#  Port to OCP of the shared profile and recess helpers of the FreeCAD
#  Fasteners workbench, from FastenerBase.py, screw_maker.py and FSutils.py
#  at workbench commit 375a73d9706a30072c132fb061e7636e8ed99f82.
#
#  Copyright 2015 Shai Seger <shaise at gmail dot com>
#  Copyright (c) 2013, 2014, 2015 Ulrich Brammer <ulrich1a[at]users.sourceforge.net>
#  BSP modifications (c) 2025-2026 Andrey Bekhterev <info at bekhterev dot in>
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
"""Profile builder and recess cutters shared by the fastener generators.

Shapes are OCP ``TopoDS_Shape`` objects; points are ``gp_Pnt``. The module needs
only OCP, ``math`` and ``freeshape.kernel``. Table lookups (``DiaList``,
``iso4757def``, ``iso10664def``) are passed in by the caller. The thread cutters
of the workbench are not ported here.
"""
from __future__ import annotations

import math

from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakePrism, BRepPrimAPI_MakeRevol
from OCP.GC import GC_MakeArcOfCircle
from OCP.Geom import Geom_BSplineCurve
try:
    from OCP.TColStd import TColStd_Array1OfInteger as _IntArray, TColStd_Array1OfReal as _RealArray
    from OCP.TColgp import TColgp_Array1OfPnt as _PntArray
except ImportError:  # OCP >= 8 exposes NCollection templates directly
    from OCP.OCP.collections import Array1_int as _IntArray, Array1_double as _RealArray, Array1_gp_Pnt as _PntArray
from OCP.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pnt, gp_Vec

from freeshape import kernel

# some common constants (screw_maker.py)
sqrt2 = math.sqrt(2.0)
sqrt3 = math.sqrt(3.0)
cos30 = sqrt3 / 2.0
tan15 = 2.0 - sqrt3
tan20 = math.tan(math.radians(20))
sin110oversin55 = math.sin(math.radians(110)) / math.sin(math.radians(55))
sin15 = math.sin(math.radians(15))
cos15 = math.cos(math.radians(15))


# ---------------------------------------------------------------- Part.* stand-ins

def make_line(p, q):
    """Stand-in for Part.makeLine(p, q): a straight edge between two gp_Pnt."""
    return BRepBuilderAPI_MakeEdge(p, q).Edge()


def make_arc(p1, pm, p2):
    """Stand-in for Part.Arc(p1, pm, p2).toShape(): the circular arc through three points."""
    mk = GC_MakeArcOfCircle(p1, pm, p2)
    if not mk.IsDone():
        raise ValueError("arc through the three points is not defined")
    return BRepBuilderAPI_MakeEdge(mk.Value()).Edge()


def make_control_bspline(pts):
    """Stand-in for Part.BSplineCurve(pts).toShape().

    FreeCAD 1.0.2 builds this curve with the points as control POLES, not as an
    interpolation: degree min(3, n - 1), clamped, uniform interior knots on [0, 1]
    (checked against a FreeCAD 1.0.2 run for n = 2..8).
    """
    n = len(pts)
    if n < 2:
        raise ValueError("a B-spline needs at least two points")
    degree = min(3, n - 1)
    nspans = n - degree
    poles = _PntArray(1, n)
    for i, p in enumerate(pts):
        poles.SetValue(i + 1, p)
    knots = _RealArray(1, nspans + 1)
    mults = _IntArray(1, nspans + 1)
    for i in range(nspans + 1):
        knots.SetValue(i + 1, i / nspans)
        mults.SetValue(i + 1, degree + 1 if i in (0, nspans) else 1)
    return BRepBuilderAPI_MakeEdge(Geom_BSplineCurve(poles, knots, mults, degree)).Edge()


def make_wire(edges):
    """Stand-in for Part.Wire(edges): the edges joined in the order given."""
    mk = BRepBuilderAPI_MakeWire()
    for e in edges:
        mk.Add(e)
    if not mk.IsDone():
        raise ValueError("edges do not join into a wire")
    return mk.Wire()


def make_face(wire):
    """Stand-in for Part.Face(wire): the planar face bounded by a closed wire."""
    mk = BRepBuilderAPI_MakeFace(wire, True)
    if not mk.IsDone():
        raise ValueError("wire does not bound a planar face")
    return mk.Face()


def make_cone(r1, r2, height, pnt=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0)):
    """Stand-in for Part.makeCone(r1, r2, height, pnt, dir, 360)."""
    ax = gp_Ax2(gp_Pnt(*pnt), gp_Dir(*direction))
    return BRepPrimAPI_MakeCone(ax, r1, r2, height).Shape()


def make_cylinder(radius, height, pnt=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0)):
    """Stand-in for Part.makeCylinder(radius, height, pnt, dir)."""
    ax = gp_Ax2(gp_Pnt(*pnt), gp_Dir(*direction))
    return BRepPrimAPI_MakeCylinder(ax, radius, height).Shape()


def translate(shape, x, y, z):
    """Stand-in for shape.translate(Vector(x, y, z)); returns the moved shape."""
    return kernel.translate(shape, (x, y, z))


def rotate(shape, base, axis, angle_deg):
    """Stand-in for shape.rotate(base, axis, angle); returns the rotated shape."""
    return kernel.rotate(shape, base, axis, angle_deg)


def _solid_of(shape):
    """Stand-in for Part.Solid(shape): the single solid of a result."""
    sol = kernel.solids(shape)
    return sol[0] if len(sol) == 1 else shape


# ---------------------------------------------------------------- FSutils.py

def parseInch(txt: str) -> float:
    """FSutils.parseInch: an inch measurement, fractions allowed, returned in mm."""
    clean_txt = ""
    for c in txt:
        if c.isdecimal() or c in ".-":
            clean_txt += c
        else:
            clean_txt += " "
    parts = [float(x) for x in clean_txt.split()]
    if len(parts) == 1:
        return parts[0] * 25.4
    elif len(parts) == 2:
        return (parts[0] / parts[1]) * 25.4
    elif len(parts) == 3:
        return (parts[0] + parts[1] / parts[2]) * 25.4
    else:
        raise ValueError(f"Invalid inch format: '{txt}'")


def parseLength(txt: str) -> float:
    """FSutils.parseLength: the first number in a length string ("M8", "10mm", "1in", "K60")."""
    if "in" in txt:
        return parseInch(txt)
    clean_txt = ""
    leftover_txt = ""
    for c in txt:
        if c.isdecimal() or c in ".-":
            clean_txt += c
            leftover_txt += " "
        else:
            clean_txt += " "
            leftover_txt += c
    parts = clean_txt.split()
    if len(parts) > 0:
        res = float(parts[0])
        if leftover_txt.rstrip() == "K":
            res /= 10.0
        return res
    raise ValueError(f"Invalid float format: '{txt}'")


# ---------------------------------------------------------------- FastenerBase.py

def DiaStr2Num(DiaStr: str, dia_list) -> float:
    """FastenerBase.DiaStr2Num: a diameter string as a number.

    ``dia_list`` is the ``DiaList`` table (name -> row whose first cell is the diameter).
    """
    if DiaStr in dia_list:
        # named diameters, e.g. "#6", "G1/4"
        return float(dia_list[DiaStr][0])
    # metric/inch format, e.g. "M6", "ST 6.3", "1 1/4in"
    return parseLength(DiaStr)


class FSFaceMaker:
    """FastenerBase.FSFaceMaker: build a face point by point on the XZ plane.

    Attributes: ``edges`` (list of TopoDS_Edge), ``firstPoint`` and ``lastPoint`` (gp_Pnt).
    """

    def __init__(self):
        self.Reset()

    def Reset(self):
        """Clear the edges and the first point."""
        self.edges = []
        self.firstPoint = None

    def AddPoint(self, x, z):
        """Add the point (x, z), with a line from the last point to it."""
        curPoint = gp_Pnt(x, 0, z)
        if self.firstPoint is None:
            self.firstPoint = curPoint
        else:
            self.edges.append(make_line(self.lastPoint, curPoint))
        self.lastPoint = curPoint

    def AddPointRelative(self, dx, dz):
        """Add a point relative to the last point, with a line to it."""
        if self.firstPoint is None:
            raise ValueError("FSFaceMaker.AddPointRelative: A start point has to be set previous")
        curPoint = gp_Pnt(self.lastPoint.X() + dx, 0, self.lastPoint.Z() + dz)
        self.edges.append(make_line(self.lastPoint, curPoint))
        self.lastPoint = curPoint

    def StartPoint(self, x, z):
        """Reset and set the starting point."""
        self.Reset()
        self.AddPoint(x, z)

    def AddArc(self, x1, z1, x2, z2):
        """Add an arc from the last point through (x1, z1) to (x2, z2)."""
        midPoint = gp_Pnt(x1, 0, z1)
        endPoint = gp_Pnt(x2, 0, z2)
        self.edges.append(make_arc(self.lastPoint, midPoint, endPoint))
        self.lastPoint = endPoint

    def AddArc2(self, xc, zc, a):
        """Add an arc from the last point, centre (xc, zc) relative to it, sweeping a degrees."""
        a = math.radians(a)
        xac = self.lastPoint.X() + xc
        zac = self.lastPoint.Z() + zc
        sa = math.atan2(-zc, -xc)
        r = math.sqrt(xc * xc + zc * zc)
        sa += a / 2.0
        x1 = xac + r * math.cos(sa)
        z1 = zac + r * math.sin(sa)
        sa += a / 2.0
        x2 = xac + r * math.cos(sa)
        z2 = zac + r * math.sin(sa)
        self.AddArc(x1, z1, x2, z2)

    def AddBSpline(self, *args):
        """Add a B-spline from the last point, with (x1, z1) ... (xn, zn) as control points.

        Upstream builds ``Part.BSplineCurve(pts)``, whose points are control poles; the
        curve passes through the first and last point only. See make_control_bspline.
        """
        n = len(args)
        if n < 4 or (n & 1) == 1:
            raise ValueError("FSFaceMaker.AddBSpline: invalid num of args, must be even number >= 4")
        pt = self.lastPoint
        pts = [pt]
        for i in range(0, n, 2):
            pt = gp_Pnt(args[i], 0, args[i + 1])
            pts.append(pt)
        self.edges.append(make_control_bspline(pts))
        self.lastPoint = pt

    def AddPoints(self, *args):
        """Add points (2 values), AddArc2 arcs (3 values) or AddArc arcs (4 values)."""
        for arg in args:
            if len(arg) == 2:
                self.AddPoint(arg[0], arg[1])
            elif len(arg) == 3:
                self.AddArc2(arg[0], arg[1], arg[2])
            elif len(arg) == 4:
                self.AddArc(arg[0], arg[1], arg[2], arg[3])

    def GetWire(self):
        """The wire of the edges so far."""
        return make_wire(self.edges)

    def GetClosedWire(self):
        """The wire closed with a line from the last point to the first."""
        self.edges.append(make_line(self.lastPoint, self.firstPoint))
        return make_wire(self.edges)

    def GetFace(self):
        """The closed wire as a planar face."""
        return make_face(self.GetClosedWire())


# ---------------------------------------------------------------- screw_maker.py

def RevolveZ(profile, angle=360):
    """Screw.RevolveZ: the revolution of ``profile`` around the Z axis through ``angle`` degrees."""
    ax = gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))
    if abs(angle) >= 360 - 1e-9:
        mk = BRepPrimAPI_MakeRevol(profile, ax)
    else:
        mk = BRepPrimAPI_MakeRevol(profile, ax, math.radians(angle))
    mk.Build()
    if not mk.IsDone():
        raise ValueError("revolve failed")
    return mk.Shape()


def makeHexPrism(width: float, height: float):
    """Screw.makeHexPrism: a regular hexagonal prism, ``width`` across flats, ``height`` along +Z."""
    polygon = []
    for i in range(7):
        # upstream rotates the vector by 60 degrees each step; the 7th point closes the polygon
        ang = math.radians(60.0 * i)
        r = width / sqrt3
        polygon.append(gp_Pnt(r * math.cos(ang), r * math.sin(ang), 0.0))
    mk = BRepBuilderAPI_MakeWire()
    for i in range(6):
        mk.Add(make_line(polygon[i], polygon[i + 1]))
    hexagon = make_face(mk.Wire())
    prism = BRepPrimAPI_MakePrism(hexagon, gp_Vec(0.0, 0.0, height))
    prism.Build()
    return prism.Shape()


def makeHexRecess(width: float, depth: float, chamfer: bool):
    """Screw.makeHexRecess: an internal hexagon ('Allen') recess cutter.

    ``width`` is across flats, ``depth`` the usable depth; the shape is taller than
    ``depth`` because of the tapered point. ``chamfer`` adds a 45 degree top chamfer.
    """
    prism = makeHexPrism(width, 3.0 * depth)
    prism = rotate(prism, (0.0, 0.0, depth / 2), (1.0, 0.0, 0.0), 180)
    cone1 = make_cone(
        (3 * depth + width) / sqrt3 + depth * sqrt3,
        width * 0.08,
        2 * depth + width / 3 - 0.08 * width * sqrt3 / 3,
        (0.0, 0.0, depth),
        (0.0, 0.0, -1.0),
    )
    recess = kernel.common(prism, cone1)
    if chamfer:
        r_3 = width * 0.49
        r_2 = 1.005 * width / sqrt3
        r_1 = depth + r_2  # math.tan(math.radians(45.)) = 1.
        h_1 = r_2 - r_3
        cone2 = make_cone(r_1, r_3, depth + h_1, (0.0, 0.0, depth), (0.0, 0.0, -1.0))
        recess = kernel.fuse(recess, cone2)
    return _solid_of(recess)


def makeHCrossRecess(CrossType: str, m: float, iso4757def):
    """Screw.makeHCrossRecess: a type H cross recess, oriented along Z, outer diameter ``m`` at Z=0.

    ``CrossType`` is "0".."4"; ``iso4757def`` is that table (type -> b, e_mean, g, f_mean,
    r, t1, alpha, beta).
    """
    b, e_mean, g, f_mean, r, t1, alpha, beta = (float(v) for v in iso4757def[CrossType])

    rad265 = math.radians(26.5)
    rad28 = math.radians(28.0)
    tg = (m - g) / 2.0 / math.tan(rad265)  # depth at radius of g
    t_tot = tg + g / 2.0 * math.tan(rad28)  # total depth

    hm = m / 4.0
    hmc = m / 2.0
    rmax = m / 2.0 + hm * math.tan(rad265)

    fm = FSFaceMaker()
    fm.AddPoints((0.0, hm), (rmax, hm), (g / 2.0, -tg), (0.0, -t_tot))
    aFace = fm.GetFace()
    cross = RevolveZ(aFace)

    # we need to cut 4 corners out of the above shape.
    # The angles 92 degrees and alpha are defined on a plane which has
    # an angle of beta against our coordinate system; use the projected angles.
    rad_alpha = math.radians(alpha / 2.0)
    rad92 = math.radians(92.0 / 2.0)
    rad_beta = math.radians(beta)

    rad_alpha_p = math.atan(math.tan(rad_alpha) / math.cos(rad_beta))
    rad92_p = math.atan(math.tan(rad92) / math.cos(rad_beta))

    tb = tg + (g - b) / 2.0 * math.tan(rad28)  # depth at dimension b
    rbtop = b / 2.0 + (hmc + tb) * math.tan(rad_beta)  # radius of b-corner at hm
    rbtot = b / 2.0 - (t_tot - tb) * math.tan(rad_beta)  # radius of b-corner at t_tot

    # delta between corner b and corner e in x direction
    dre = e_mean / 2.0 / math.tan(rad_alpha_p)

    dx = m / 2.0 * math.cos(rad92_p)
    dy = m / 2.0 * math.sin(rad92_p)

    PntC0 = gp_Pnt(rbtop, 0.0, hmc)
    PntC1 = gp_Pnt(rbtot, 0.0, -t_tot)
    PntC3 = gp_Pnt(rbtot + dre, +e_mean / 2.0, -t_tot)
    PntC5 = gp_Pnt(rbtot + dre, -e_mean / 2.0, -t_tot)
    PntC7 = gp_Pnt(rbtot + dre + 2.0 * dx, +e_mean + 2.0 * dy, -t_tot)
    PntC9 = gp_Pnt(rbtot + dre + 2.0 * dx, -e_mean - 2.0 * dy, -t_tot)

    pts = [PntC1, PntC3, PntC7, PntC9, PntC5, PntC1]
    wire_t_tot = make_wire([make_line(pts[i], pts[i + 1]) for i in range(5)])
    spine = make_wire([make_line(PntC0, PntC1)])
    # Wire.makePipeShell([profile], makeSolid=True, isFrenet=False)
    pipe = BRepOffsetAPI_MakePipeShell(spine)
    pipe.SetMode(False)
    pipe.Add(wire_t_tot)
    if not pipe.IsReady():
        raise ValueError("shape is not ready to build")
    pipe.Build()
    pipe.MakeSolid()
    corner = pipe.Shape()
    for i in range(4):
        cross = kernel.cut(cross, rotate(corner, (0, 0, 0), (0, 0, 1), 90 * i))
    return _solid_of(cross)


def makeHexalobularRecess(drive_size: str, depth: float, chamfer: bool, iso10664def):
    """Screw.makeHexalobularRecess: an ISO 10664 hexalobular recess cutter.

    ``iso10664def`` is that table (drive size -> A, B, Re). Used by the cylinder head
    screw generator; the card did not list it.
    """
    A, B, Re = (float(v) for v in iso10664def[drive_size])
    Ri = -((B + sqrt3 * (2. * Re - A)) * B + (A - 4. * Re) * A) / \
        (4. * B - 2. * sqrt3 * A + (4. * sqrt3 - 8.) * Re)
    beta = math.acos(A / (4 * Ri + 4 * Re) - (2 * Re) /
                     (4 * Ri + 4 * Re)) - math.pi / 6
    Re_x = A / 2.0 - Re + Re * math.sin(beta)
    Re_y = Re * math.cos(beta)
    Ri_y = B / 4.0
    Ri_x = sqrt3 * B / 4.0

    def rot60(p):
        c, s = math.cos(math.radians(60.0)), math.sin(math.radians(60.0))
        return gp_Pnt(c * p.X() - s * p.Y(), s * p.X() + c * p.Y(), p.Z())

    hexlobWireList = []

    PntRe0 = gp_Pnt(Re_x, -Re_y, depth)
    PntRe1 = gp_Pnt(A / 2.0, 0.0, depth)
    PntRe2 = gp_Pnt(Re_x, Re_y, depth)
    hexlobWireList.append(make_arc(PntRe0, PntRe1, PntRe2))

    PntRi = gp_Pnt(Ri_x, Ri_y, depth)
    PntRi2 = rot60(PntRe0)
    hexlobWireList.append(make_arc(PntRe2, PntRi, PntRi2))

    for i in range(5):
        PntRe1 = rot60(PntRe1)
        PntRe2 = rot60(PntRe2)
        hexlobWireList.append(make_arc(PntRi2, PntRe1, PntRe2))
        PntRi = rot60(PntRi)
        PntRi2 = rot60(PntRi2)
        if i == 5:  # never true upstream either
            edge1 = make_arc(PntRe2, PntRi, PntRe0)
        else:
            edge1 = make_arc(PntRe2, PntRi, PntRi2)
        hexlobWireList.append(edge1)
    face = make_face(make_wire(hexlobWireList))

    # extrude in z to create the cutting tool for the screw-head-face
    mk = BRepPrimAPI_MakePrism(face, gp_Vec(0.0, 0.0, -3 * depth))
    mk.Build()
    prism = mk.Shape()
    # add chamfers to surfaces
    width = sqrt3 * A / 2
    cone1 = make_cone(
        (3 * depth + width) / sqrt3 + depth * sqrt3,
        width * 0.08,
        2 * depth + width / 3 - 0.08 * width * sqrt3 / 3,
        (0.0, 0.0, depth),
        (0.0, 0.0, -1.0),
    )
    recess = kernel.common(prism, cone1)
    if chamfer:
        # 18 degree chamfer at top of recess cutter
        cone2 = make_cone(
            0.505 * A + depth / math.tan(math.radians(18)),
            0.49 * B,
            depth + (0.505 * A - 0.49 * B) * math.tan(math.radians(18)),
            (0.0, 0.0, depth),
            (0.0, 0.0, -1.0),
        )
        recess = kernel.fuse(recess, cone2)  # kernel.fuse also removes splitters
    return _solid_of(recess)


def getDia(ThreadDiam, isNut: bool, dia_list, print_mode: bool = False,
           nut_scale=(1.0, 0.0), screw_scale=(1.0, 0.0)) -> float:
    """Screw.getDia: a numeric diameter from "1/4in", "#6", "M6", "ST 6.3" or a number.

    ``dia_list`` is the ``DiaList`` table. The upstream instance fields map to
    ``print_mode`` (sm3DPrintMode), ``nut_scale`` (smNutThrScaleA, B) and
    ``screw_scale`` (smScrewThrScaleA, B); with ``print_mode`` off they are unused.
    """
    if isinstance(ThreadDiam, str):
        dia = DiaStr2Num(ThreadDiam, dia_list)
    else:
        dia = ThreadDiam
    if print_mode:
        a, b = nut_scale if isNut else screw_scale
        dia = a * dia + b
    return dia
