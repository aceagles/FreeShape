# ***************************************************************************
# *                                                                         *
# * This program is free software: you can redistribute it and/or modify    *
# * it under the terms of the GNU General Public License as published by    *
# * the Free Software Foundation, either version 3 of the License, or       *
# * (at your option) any later version.                                     *
# *                                                                         *
# * This program is distributed in the hope that it will be useful,         *
# * but WITHOUT ANY WARRANTY; without even the implied warranty of          *
# * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the           *
# * GNU General Public License for more details.                            *
# *                                                                         *
# * You should have received a copy of the GNU General Public License       *
# * along with this program.  If not, see <http://www.gnu.org/licenses/>.   *
# *                                                                         *
# ***************************************************************************
#
# Adapted for FreeShape from freecad.gears freecad/gears/involutegear.py @ 4164073
# (InvoluteGear.generate_gear_shape) and freecad/gears/basegear.py @ 4164073
# (points_to_wire, rotate_tooth, helical_extrusion), re-implemented on OCP.

"""Involute spur, helical and herringbone gear solids, with every face labelled.

`spur_gear` is headless: it takes numbers and returns a `GearShape` (one solid plus a label for each of its
faces). The tooth profile comes from `pygears.involute_tooth` unchanged; this module turns it into OCP
geometry the way freecad.gears does (interpolated segments, one wire for the whole gear, a prism or a
helical pipe shell) and keeps the maker history so that each side face can be traced to its profile segment.

Labels (they match `[A-Za-z0-9_+-]+`): `top`, `bottom`, `bore`, and for tooth i (1-based, counter-clockwise
seen from +Z, tooth 1 is the unrotated tooth) `t<i>_rootA`, `t<i>_flankA`, `t<i>_tip`, `t<i>_flankB`,
`t<i>_rootB` and `t<i>_gap` (the segment joining the tooth to the next). A tooth that pygears returns with
three segments (the involute starts above the root circle) has no root labels. Any other segment count
gets `t<i>_seg<k>`. In a double helix the faces of the lower half carry the suffix `_lo`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeSolid,
                                BRepBuilderAPI_MakeVertex,
                                BRepBuilderAPI_MakeWire, BRepBuilderAPI_Sewing, BRepBuilderAPI_Transform)
from OCP.BRepLib import BRepLib
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakePrism
from OCP.Geom import Geom_CylindricalSurface
from OCP.Geom2d import Geom2d_Line
from OCP.GeomAbs import GeomAbs_Cylinder
from OCP.GeomAPI import GeomAPI_Interpolate
from OCP.gp import gp_Ax2, gp_Ax3, gp_Dir, gp_Dir2d, gp_Pnt, gp_Pnt2d, gp_Trsf, gp_Vec
from OCP.GProp import GProp_GProps
from OCP.BRepGProp import BRepGProp
from OCP.TopAbs import TopAbs_SHELL
from OCP.TopExp import TopExp_Explorer

try:
    from OCP.TColgp import TColgp_HArray1OfPnt as _PntArray
except ImportError:  # OCP >= 8
    from OCP.OCP.collections import HArray1_gp_Pnt as _PntArray

from freeshape import kernel as K

from .pygears._functions import rotation
from .pygears.involute_tooth import InvoluteTooth

# numbered like FreeCAD's Part.Wire: segments of one tooth, in profile order (counter-clockwise)
_NAMES5 = ("rootA", "flankA", "tip", "flankB", "rootB")
_NAMES3 = ("flankA", "tip", "flankB")
_JOIN_EPS = 1e-9


@dataclass
class GearShape:
    """A gear solid with the geometry numbers and a label for every face."""
    solid: object                       # TopoDS_Solid, axis +Z, bottom face at z=0, centred on the origin
    pitch_diameter: float               # module * teeth / cos(helix angle)
    tip_diameter: float
    root_diameter: float
    face_labels: list = field(default_factory=list)   # [(TopoDS_Face, label)], each face of `solid` once


# --------------------------------------------------------------------------- profile (basegear.points_to_wire)

def _pnt(p) -> gp_Pnt:
    return gp_Pnt(float(p[0]), float(p[1]), 0.0)


def _segment_edge(pts, v1, v2):
    """A line for a 2-point segment, an interpolating B-spline for a longer one (as `points_to_wire`),
    between the shared vertices v1 and v2 (shared so the wire does not copy its edges)."""
    if len(pts) == 2:
        mk = BRepBuilderAPI_MakeEdge(v1, v2)
    else:
        arr = _PntArray(1, len(pts))
        for i, p in enumerate(pts):
            arr.SetValue(i + 1, _pnt(p))
        interp = GeomAPI_Interpolate(arr, False, 1e-7)
        interp.Perform()
        if not interp.IsDone():
            raise ValueError("gear profile: spline interpolation failed")
        mk = BRepBuilderAPI_MakeEdge(interp.Curve(), v1, v2)
    if not mk.IsDone():
        raise ValueError("gear profile: degenerate segment")
    return mk.Edge()


def _segment_names(count):
    if count == 5:
        return list(_NAMES5)
    if count == 3:
        return list(_NAMES3)
    return [f"seg{k}" for k in range(1, count + 1)]


def _profile(tooth, num_teeth):
    """The closed profile of the whole gear: [(edge, label)] in order, plus the wire.

    `tooth` is pygears' one-tooth segment list. FreeCAD rotates a two-tooth wire and chains copies of it;
    here each of the z teeth is rotated by k * pitch angle directly, followed by the gap segment to the
    next tooth, which gives the same closed loop with every edge labelled as it is made.
    """
    names = _segment_names(len(tooth))
    phi = 2.0 * math.pi / num_teeth
    rotated = [[rotation(k * phi)(np.asarray(s, float)) for s in tooth] for k in range(num_teeth + 1)]
    segments = []           # [(points, label)] around the whole gear
    for k in range(num_teeth):
        t = f"t{k + 1}"
        segments += [(seg, f"{t}_{name}") for seg, name in zip(rotated[k], names)]
        a, b = rotated[k][-1][-1], rotated[k + 1][0][0]
        if math.hypot(b[0] - a[0], b[1] - a[1]) > _JOIN_EPS:
            segments.append(([a, b], f"{t}_gap"))
    vertices = [BRepBuilderAPI_MakeVertex(_pnt(seg[0])).Vertex() for seg, _ in segments]
    labelled = [(_segment_edge(seg, vertices[i], vertices[(i + 1) % len(segments)]), label)
                for i, (seg, label) in enumerate(segments)]
    wire = BRepBuilderAPI_MakeWire()
    for e, _ in labelled:
        wire.Add(e)
    if not wire.IsDone():
        raise ValueError("gear profile: the tooth segments do not form a wire")
    return labelled, wire.Wire()


# --------------------------------------------------------------------------- helical sweep (basegear.helical_extrusion)

def _helix(pitch, height, radius, left_handed):
    """A helix on a cylinder about +Z starting at (radius, 0, 0): a line in the cylinder's (u, v) plane."""
    surf = Geom_CylindricalSurface(gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), radius)
    direction = gp_Dir2d(-2.0 * math.pi if left_handed else 2.0 * math.pi, pitch)
    line = Geom2d_Line(gp_Pnt2d(0.0, 0.0), direction)
    length = height / pitch * math.hypot(2.0 * math.pi, pitch)
    mk = BRepBuilderAPI_MakeEdge(line, surf, 0.0, length)
    edge = mk.Edge()
    BRepLib.BuildCurves3d_s(edge)
    return BRepBuilderAPI_MakeWire(edge).Wire()


def _moved(shape, rot_angle=0.0, dz=0.0):
    """The transform maker for a copy of `shape` rotated about +Z by rot_angle (rad), then lifted by dz."""
    trsf = gp_Trsf()
    trsf.SetRotation(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)).Axis(), rot_angle)
    lift = gp_Trsf()
    lift.SetTranslation(gp_Vec(0, 0, dz))
    return BRepBuilderAPI_Transform(shape, lift.Multiplied(trsf), True)


def _generated(maker, edge):
    return [s for s in maker.Generated(edge)]


def _sew(faces, tol=1e-6):
    """Sew faces into a shell and close it to a solid. Returns (solid, {id(face): [faces in the solid]})."""
    sew = BRepBuilderAPI_Sewing(tol)
    for f in faces:
        sew.Add(f)
    sew.Perform()
    sewn = sew.SewedShape()
    solid = BRepBuilderAPI_MakeSolid(_as_shell(sewn)).Solid()
    if _volume(solid) < 0:
        solid.Reverse()
    after = {}
    for f in faces:
        mods = [m for m in (sew.Modified(f),) if not m.IsNull()] if sew.IsModified(f) else [f]
        after[id(f)] = mods
    return solid, after


def _as_shell(shape):
    if shape.ShapeType() == TopAbs_SHELL:
        return K._as_shell(shape)
    exp = TopExp_Explorer(shape, TopAbs_SHELL)
    if not exp.More():
        raise ValueError("gear: sewing did not produce a shell")
    return K._as_shell(exp.Current())


def _volume(shape) -> float:
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return g.Mass()


def _helical_solid(face, labelled, wire, height, angle, double_helix):
    """Sweep `face` along a helix so the section twists by `angle` (rad) over `height`.

    Returns (solid, [(face, label)]) with the faces as they are in the solid.
    """
    pitch = height * 2.0 * math.pi / abs(angle)
    radius = 10.0           # any value: only the twist matters, as in FreeCAD
    left = angle < 0
    base_h = height / 2.0 if double_helix else height
    z0 = base_h if double_helix else 0.0
    spine = _helix(pitch, base_h, radius, left)
    if double_helix:
        start = _moved(face, 0.0, z0).Shape()
        moved_wire = _moved(wire, 0.0, z0)
        profile = K._as_wire(moved_wire.Shape())
        spine = K._as_wire(_moved(spine, 0.0, z0).Shape())
    else:
        start, profile = face, wire

    pipe = BRepOffsetAPI_MakePipeShell(spine)
    pipe.SetMode(gp_Dir(0, 0, 1))       # fixed Z binormal: sections stay in XY while the helix twists
    pipe.Add(profile, False, False)
    pipe.Build()
    if not pipe.IsDone():
        raise ValueError("gear: the helical sweep failed")

    # profile edges as they sit at the start of the sweep (moved copies for the double helix)
    if double_helix:
        mapped = [(moved_wire.ModifiedShape(e), label) for e, label in labelled]
    else:
        mapped = labelled
    parts = []              # [(face, label)]
    for e, label in mapped:
        gen = _generated(pipe, e)
        if len(gen) != 1:
            raise ValueError(f"gear: sweep made {len(gen)} faces for {label}")
        parts.append((K._as_face(gen[0]), label))

    twist = angle * (0.5 if double_helix else 1.0)
    top = K._as_face(_moved(start, twist, base_h).Shape())
    if double_helix:
        mirror = gp_Trsf()
        mirror.SetMirror(gp_Ax2(gp_Pnt(0, 0, height / 2.0), gp_Dir(0, 0, 1)))
        halves = [(top, "top")] + parts
        lower = []
        for f, label in halves:
            tr = BRepBuilderAPI_Transform(f, mirror, True)
            lower.append((K._as_face(tr.Shape()), "bottom" if label == "top" else label + "_lo"))
        all_parts = halves + lower
    else:
        all_parts = parts + [(top, "top"), (start, "bottom")]

    solid, after = _sew([f for f, _ in all_parts])
    out = []
    for f, label in all_parts:
        for m in after[id(f)]:
            out.append((K._as_face(m), label))
    return solid, out


# --------------------------------------------------------------------------- the gear

def _same(a, b) -> bool:
    return a.IsSame(b)


def _bore(solid, labelled, diameter, height):
    tool = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, -height / 2.0), gp_Dir(0, 0, 1)), diameter / 2.0,
                                    height * 2.0).Shape()
    cut = BRepAlgoAPI_Cut(solid, tool)
    if not cut.IsDone():
        raise ValueError("gear: cutting the bore failed")
    out = []
    for f, label in labelled:
        if cut.IsDeleted(f):
            continue
        mods = [K._as_face(m) for m in cut.Modified(f)]
        for m in (mods or [f]):
            out.append((m, label))
    for tf in K.faces(tool):
        if BRepAdaptor_Surface(tf).GetType() == GeomAbs_Cylinder:
            for m in cut.Modified(tf):
                out.append((K._as_face(m), "bore"))
    return cut.Shape(), out


def spur_gear(num_teeth, module, height, pressure_angle=20.0, helix_angle=0.0, double_helix=False,
              shift=0.0, clearance=0.25, head=0.0, backlash=0.0, undercut=False, bore=0.0,
              num_points=20) -> GearShape:
    """An involute gear solid; angles in degrees, lengths in mm. See the module docstring for the labels.

    As in FreeCAD's default (`properties_from_tool` off), `module` is the transverse module: a helical gear
    is the spur profile twisted along the axis, so its volume and pitch diameter (module * teeth) match the
    spur gear's. `helix_angle` sets the twist: the top face turns by height * tan(helix) * 2 / pitch diameter.
    """
    num_teeth = int(num_teeth)
    if num_teeth < 3:
        raise ValueError("a gear needs at least 3 teeth")
    if module <= 0 or height <= 0:
        raise ValueError("module and height must be positive")
    if num_points < 3:
        raise ValueError("num_points must be at least 3")
    if abs(helix_angle) >= 89.0:
        raise ValueError("helix angle must be below 89 degrees")

    beta = math.radians(helix_angle)
    gear = InvoluteTooth(m=module, num_teeth=num_teeth, pressure_angle=math.radians(pressure_angle),
                         clearance=clearance, shift=shift, beta=beta, undercut=bool(undercut),
                         backlash=backlash * 2.0 * 0.5, head=head, properties_from_tool=False)
    if bore < 0 or (bore > 0 and bore >= gear.df):
        raise ValueError("bore must be between 0 and the root diameter")

    tooth = gear.points(num=num_points)
    labelled, wire = _profile(tooth, num_teeth)
    face_mk = BRepBuilderAPI_MakeFace(wire, True)
    if not face_mk.IsDone():
        raise ValueError("gear: the profile is not a planar closed wire")
    face = face_mk.Face()

    if beta == 0.0:
        prism = BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, float(height)))
        solid = prism.Shape()
        parts = []
        for e, label in labelled:
            gen = _generated(prism, e)
            if len(gen) != 1:
                raise ValueError(f"gear: extrusion made {len(gen)} faces for {label}")
            parts.append((K._as_face(gen[0]), label))
        parts.append((K._as_face(prism.LastShape()), "top"))
        parts.append((K._as_face(prism.FirstShape()), "bottom"))
    else:
        twist = height * math.tan(beta) * 2.0 / gear.d
        solid, parts = _helical_solid(face, labelled, wire, float(height), twist, bool(double_helix))

    if bore > 0:
        solid, parts = _bore(solid, parts, float(bore), float(height))

    solids = K.solids(solid)
    if len(solids) != 1:
        raise ValueError("gear: the result is not a single solid")
    solid = solids[0]
    if not K.is_valid(solid):
        raise ValueError("gear: the result is not a valid solid")

    ordered = []
    for f in K.faces(solid):
        hits = [label for g, label in parts if _same(f, g)]
        if len(hits) != 1:
            raise ValueError(f"gear: a face of the solid has {len(hits)} labels")
        ordered.append((f, hits[0]))
    if len({lab for _, lab in ordered}) != len(ordered):
        raise ValueError("gear: face labels are not unique")
    return GearShape(solid, float(gear.dw), float(gear.da), float(gear.df), ordered)
