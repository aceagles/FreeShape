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
# Adapted for FreeShape from freecad.gears freecad/gears/bevelgear.py @ 4164073
# (BevelGear.generate_gear_shape) and freecad/gears/basegear.py @ 4164073 (make_bspline_wire),
# by the freecad.gears contributors; https://github.com/looooo/freecad.gears

"""Straight bevel gear geometry on OCP.

`bevel_gear(...)` builds the same solid as freecad.gears' BevelGear with `simple=False`, `beta=0`,
`reset_origin=True` and a `version` property (so `scale_0 = scale - height`, `scale_1 = scale`), where
`scale = module * num_teeth / 2 / tan(pitch_angle)` is the distance from the pitch-cone apex to the heel
along the axis. pygears' spherical-involute tooth (on the z=1 plane, apex at the origin) is repeated
round the axis, scaled to the toe (`scale_0`) and heel (`scale_1`), built as two closed wires of
interpolated B-splines and lofted into a solid. FreeCAD's reset_origin transform (mirror z, then move
by `scale_1`) is applied last.

Placement, for mating gears: the gear axis is +Z. The heel (large end) lies in the plane z=0, the toe
(small end) in the plane z=height, and the teeth taper towards +Z. The pitch-cone apex is on the axis at
(0, 0, module * num_teeth / 2 / tan(pitch_angle)), which `cone_limit(...)` returns. Two gears mesh when
their apexes coincide, their axes meet there at pitch_angle_1 + pitch_angle_2, and one is turned by half
a tooth pitch relative to the other. Tooth 1 is centred on +Y.

The heel is flat (perpendicular to the axis), not a back cone, so the tooth tips on the heel lie on the
tip cone at radius `scale * tan(pitch_angle + 2 sin(pitch_angle) / num_teeth)`. That is larger than the
textbook outside diameter m (z + 2 cos(pitch_angle)), which is measured on the back cone. `tip_diameter`
is the diameter actually reached on the heel; the pitch circle on the heel is exactly module * num_teeth.

Face labels come from maker history (spec docs/reference/element-naming.md section 3): each profile
edge is labelled as the wire is built, `BRepOffsetAPI_ThruSections.GeneratedFace(edge)` gives its lateral
face, `FirstShape()`/`LastShape()` are the toe/heel caps, and the transform's and bore cut's `Modified`
carry the labels through (plus the ReShape that turns flat B-spline strips into planes). Teeth are numbered 1..z counter-clockwise seen from +Z; per tooth the labels
are pygears' segments in order `t<i>_rootA`, `t<i>_flankA`, `t<i>_tip`, `t<i>_flankB`, `t<i>_rootB`
(`t<i>_flankA`, `t<i>_tip`, `t<i>_flankB` when pygears adds no root foot, `t<i>_seg<k>` otherwise),
then `t<i>_gap`, the root segment joining tooth i to tooth i+1. Plus `heel`, `toe` and `bore`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRep import BRep_Tool
from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire,
                                 BRepBuilderAPI_Transform)
from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
from OCP.BRepTools import BRepTools, BRepTools_ReShape
from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
from OCP.GeomAPI import GeomAPI_Interpolate
from OCP.GeomLib import GeomLib_IsPlanarSurface
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
try:
    from OCP.TColgp import TColgp_HArray1OfPnt
except ImportError:  # OCP >= 8 exposes NCollection templates directly (as kernel.py does for TopTools)
    from OCP.OCP.collections import HArray1_gp_Pnt as TColgp_HArray1OfPnt
from OCP.TopAbs import TopAbs_FACE

from freeshape import kernel as K

from .pygears._functions import rotation3D
from .pygears.bevel_tooth import BevelTooth


@dataclass
class GearShape:
    """A gear solid plus its main diameters (mm) and a label for every face."""
    solid: object                      # TopoDS_Solid
    pitch_diameter: float
    tip_diameter: float
    root_diameter: float
    face_labels: list = field(default_factory=list)   # [(TopoDS_Face, label)], every face exactly once


_TOOTH_LABELS = {5: ("rootA", "flankA", "tip", "flankB", "rootB"), 3: ("flankA", "tip", "flankB")}


def cone_limit(num_teeth, module, pitch_angle):
    """Distance (mm) from the heel to the pitch-cone apex, module * z / 2 / tan(pitch angle): the apex is at
    (0, 0, cone_limit(...)) and a height at or above it is rejected."""
    return module * num_teeth / 2 / np.tan(np.radians(pitch_angle))


def _tooth_profile(num_teeth, module, pitch_angle, pressure_angle, clearance, backlash, num_points):
    """pygears' tooth on the z=1 plane, fed exactly as BevelGear.generate_gear_shape feeds it."""
    gear = BevelTooth()
    gear.z = num_teeth
    gear.module = module
    gear.pressure_angle = (90 - pressure_angle) * np.pi / 180.0
    gear.pitch_angle = pitch_angle * np.pi / 180
    gear.backlash = backlash
    scale = cone_limit(num_teeth, module, pitch_angle)
    gear.clearance = clearance / scale
    gear._update()
    return gear, list(gear.points(num=num_points)), scale


def _labelled_segments(tooth, num_teeth):
    """FreeCAD's tooth-repeat loop (rotate by -2*pi/z, join with a two-point root segment), with labels.

    pygears turns each copy clockwise seen from +Z, so copy g is tooth 1 + (-g mod z) counter-clockwise."""
    def number(g):
        return 1 + (-g) % num_teeth

    names = _TOOTH_LABELS.get(len(tooth), tuple(f"seg{k}" for k in range(len(tooth))))
    rot = rotation3D(-2 * np.pi / num_teeth)
    out = [(seg, f"t1_{n}") for seg, n in zip(tooth, names)]
    first, rotated = tooth, tooth
    for g in range(1, num_teeth):
        rotated = list(map(rot, rotated))
        out.append((np.array([out[-1][0][-1], rotated[0][0]]), f"t{number(g)}_gap"))
        out += [(seg, f"t{number(g)}_{n}") for seg, n in zip(rotated, names)]
    out.append((np.array([out[-1][0][-1], first[0][0]]), "t1_gap"))
    return out


def _bspline_edge(points):
    arr = TColgp_HArray1OfPnt(1, len(points))
    for i, p in enumerate(points, 1):
        arr.SetValue(i, gp_Pnt(float(p[0]), float(p[1]), float(p[2])))
    interp = GeomAPI_Interpolate(arr, False, 1e-7)
    interp.Perform()
    if not interp.IsDone():
        raise K.KernelError("bevel gear: B-spline interpolation of a tooth segment failed")
    return BRepBuilderAPI_MakeEdge(interp.Curve()).Edge()


def _bspline_wire(segments, scale):
    """basegear.make_bspline_wire, keeping each wire edge with its label."""
    mw = BRepBuilderAPI_MakeWire()
    labelled = []
    for pts, label in segments:
        mw.Add(_bspline_edge(scale * np.asarray(pts)))
        if not mw.IsDone():
            raise K.KernelError(f"bevel gear: could not join segment {label} into the profile wire")
        labelled.append((mw.Edge(), label))
    if not mw.Wire().Closed():
        raise K.KernelError("bevel gear: the profile wire is not closed")
    return mw.Wire(), labelled


def _planar_strips_as_planes(shape, labels):
    """Rebuild the loft's flat strips (tips, roots, gaps: 2x2 bilinear B-splines) as plane faces.

    Not in FreeCAD. The geometry is unchanged (GeomLib_IsPlanarSurface at 1e-7), but OCC's adaptive volume
    integrator (`kernel.rough_volume`, run after every boolean) needs about a minute on the B-spline form
    of this solid and 0.1 s on the plane form. Labels follow BRepTools_ReShape.Value."""
    fmap = K.TopTools_IndexedMapOfShape()
    K.TopExp.MapShapes_s(shape, TopAbs_FACE, fmap)
    reshape = BRepTools_ReShape()
    for face, _ in labels:
        face = K._as_face(fmap.FindKey(fmap.FindIndex(face)))     # oriented as it sits in the shell
        surface = BRep_Tool.Surface_s(face)
        if BRepAdaptor_Surface(face).GetType() == GeomAbs_Plane:
            continue
        planar = GeomLib_IsPlanarSurface(surface, 1e-7)
        if planar.IsPlanar():
            maker = BRepBuilderAPI_MakeFace(planar.Plan(), BRepTools.OuterWire_s(face), True)
            if maker.IsDone():
                reshape.Replace(face, maker.Face())
    return reshape.Apply(shape), [(reshape.Value(f), label) for f, label in labels]


def bevel_gear(num_teeth, module, height, pitch_angle=45.0, pressure_angle=20.0, clearance=0.1,
               backlash=0.0, bore=0.0, num_points=20) -> GearShape:
    """Straight bevel gear (degrees, mm). See the module docstring for placement and face labels."""
    num_teeth = int(num_teeth)
    if num_teeth < 3:
        raise ValueError("a bevel gear needs at least 3 teeth")
    if module <= 0 or height <= 0:
        raise ValueError("module and height must be positive")
    if not 0 < pitch_angle < 90:
        raise ValueError("pitch angle must be between 0 and 90 degrees")
    if num_points < 3:
        raise ValueError("num_points must be at least 3")
    limit = cone_limit(num_teeth, module, pitch_angle)
    if height >= limit * (1 - 1e-12):          # tan(45 deg) rounds below 1
        raise ValueError(f"height must be smaller than {limit:.6g} mm, the cone limit "
                         f"module * teeth / 2 / tan(pitch angle) for this gear")

    gear, tooth, scale = _tooth_profile(num_teeth, module, pitch_angle, pressure_angle, clearance, backlash,
                                        num_points)
    scale_0, scale_1 = scale - height, scale
    unit_root = gear.r_f / gear.z_f
    if bore > 0 and bore >= 2 * unit_root * scale_0:
        raise ValueError(f"bore must be smaller than the root diameter at the toe, {2 * unit_root * scale_0:.6g} mm")

    segments = _labelled_segments(tooth, num_teeth)
    toe_wire, toe_edges = _bspline_wire(segments, scale_0)
    heel_wire, _ = _bspline_wire(segments, scale_1)

    loft = BRepOffsetAPI_ThruSections(True, False)
    loft.AddWire(toe_wire)
    loft.AddWire(heel_wire)
    loft.Build()
    if not loft.IsDone():
        raise K.KernelError("bevel gear: loft failed")
    labels = [(loft.FirstShape(), "toe"), (loft.LastShape(), "heel")]
    labels += [(loft.GeneratedFace(edge), label) for edge, label in toe_edges]

    # reset_origin: mirror z and move by scale_1, so the heel lies on z=0 and the apex on +Z
    mirror, move = gp_Trsf(), gp_Trsf()
    mirror.SetMirror(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)))
    move.SetTranslation(gp_Vec(0, 0, scale_1))
    move.Multiply(mirror)
    xf = BRepBuilderAPI_Transform(loft.Shape(), move, True)
    shape = xf.Shape()
    labels = [(K._as_face(xf.ModifiedShape(f)), label) for f, label in labels]
    shape, labels = _planar_strips_as_planes(shape, labels)

    if bore > 0:
        cyl = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, -1.0), gp_Dir(0, 0, 1)), bore / 2, height + 2.0).Shape()
        side = [f for f in K.faces(cyl) if BRepAdaptor_Surface(f).GetType() == GeomAbs_Cylinder]
        cut = BRepAlgoAPI_Cut(shape, cyl)
        if not cut.IsDone():
            raise K.KernelError("bevel gear: bore cut failed")
        mapped = []
        for f, label in labels + [(s, "bore") for s in side]:
            if cut.IsDeleted(f):
                continue
            mod = list(cut.Modified(f))
            mapped += [(m, label if k == 0 else f"{label}_{k}") for k, m in enumerate(mod)] or [(f, label)]
        shape, labels = cut.Shape(), mapped

    solids = K.solids(shape)
    if len(solids) != 1:
        raise K.KernelError(f"bevel gear: expected one solid, got {len(solids)}")
    solid = solids[0]

    fmap = K.TopTools_IndexedMapOfShape()
    K.TopExp.MapShapes_s(solid, TopAbs_FACE, fmap)
    by_index = {}
    for f, label in labels:
        i = fmap.FindIndex(f)
        if i == 0:
            continue                    # e.g. a bore-side face outside the solid
        if i in by_index:
            raise K.KernelError(f"bevel gear: face labelled both {by_index[i]} and {label}")
        by_index[i] = label
    if len(by_index) != fmap.Size():
        raise K.KernelError(f"bevel gear: {fmap.Size() - len(by_index)} faces have no history label")
    face_labels = [(K._as_face(fmap.FindKey(i)), by_index[i]) for i in range(1, fmap.Size() + 1)]

    tip_radius = max(float(np.hypot(p[0], p[1])) for seg in tooth for p in seg)
    return GearShape(
        solid=solid,
        pitch_diameter=module * num_teeth,
        tip_diameter=2 * tip_radius * scale_1,
        root_diameter=2 * unit_root * scale_1,
        face_labels=face_labels,
    )
