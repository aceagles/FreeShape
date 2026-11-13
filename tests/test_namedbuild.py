"""The naming builder a Python feature builds through (freeshape/namedbuild.py).

Every result must be completely and uniquely named, and a name must survive a parameter change that
does not remove its element -- the same three properties tests/test_naming.py checks for the
built-in features. Section numbers are docs/reference/element-naming.md's.
"""
import math
import os
import sys

import numpy as np
import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.GeomAbs import GeomAbs_Cylinder
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from freeshape import kernel as K  # noqa: E402
from freeshape import naming as N  # noqa: E402
from freeshape import namedbuild as NB  # noqa: E402

Z = np.array([0.0, 0.0, 1.0])

# The extruded 20 x 10 rectangle of `_rect_solid`, edges a (y=0), b (x=x1), c (y=10), d (x=0).
RECT_FACE_NAMES = [
    "a;PYF;:HF1:4,E;:G;XTR;:HF1:7,F",
    "b;PYF;:HF1:4,E;:G;XTR;:HF1:7,F",
    "c;PYF;:HF1:4,E;:G;XTR;:HF1:7,F",
    "d;PYF;:HF1:4,E;:G;XTR;:HF1:7,F",
    "f1;PYF;:HF1:4,F;:G0;XTR;:HF1:8,F",     # the top: generated parallel to the profile (§ 4)
    "f1;PYF;:HF1:4,F;XTR;:HF1:4,F",         # the bottom: the profile face itself, carried forward
]


# --------------------------------------------------------------------------- helpers

def _rect_face(x1=20.0, y1=10.0):
    curves = [{"type": "line", "p1": p, "p2": q} for p, q in
              (((0, 0), (x1, 0)), ((x1, 0), (x1, y1)), ((x1, y1), (0, y1)), ((0, y1), (0, 0)))]
    faces = K.sketch_regions(K.std_plane("Top"), curves)
    assert len(faces) == 1
    return faces[0]


def _rect_edge_label(x1, y1):
    """Labels a, b, c, d by where the edge lies -- what a script knows about the edges it drew."""
    def label(edge):
        c = K.edge_info(K._as_edge(edge))["c"]
        if abs(c[1]) < 1e-7:
            return "a"
        if abs(c[0] - x1) < 1e-7:
            return "b"
        if abs(c[1] - y1) < 1e-7:
            return "c"
        if abs(c[0]) < 1e-7:
            return "d"
        return None
    return label


def _rect_solid(depth=10.0, x1=20.0, y1=10.0, tag="F1"):
    b = NB.Builder(tag)
    prof = b.profile(_rect_face(x1, y1), _rect_edge_label(x1, y1))
    return b, b.extrude(prof, Z, depth)


def _complete_and_unique(named):
    assert named.unnamed() == []
    values = list(named.names.values())
    assert len(values) == len(set(values))


def _face_names(named):
    return sorted(named.names_of(N.FACE))


def _face_by_name(named, name):
    face = named.shape_of(name)
    assert face is not None, name
    return K.face_info(K._as_face(face))


# --------------------------------------------------------------------------- extrude a labelled profile

def test_profile_extrude_names_every_face_from_the_edge_labels():
    _, solid = _rect_solid()
    _complete_and_unique(solid)
    assert len(solid.names_of(N.FACE)) == 6
    side_b = [n for n in solid.names_of(N.FACE) if n.startswith("b;")]
    assert len(side_b) == 1
    info = _face_by_name(solid, side_b[0])
    assert info["n"][0] > 0.9 and abs(info["c"][0] - 20.0) < 1e-7    # the face swept from edge b


def test_extrude_face_names_are_the_literal_list():
    """Literal, so the names cannot drift between OCP 7.9 and 8.0 unnoticed."""
    _, solid = _rect_solid()
    assert _face_names(solid) == RECT_FACE_NAMES


def test_extrude_names_survive_a_depth_change():
    _, short = _rect_solid(depth=10.0)
    _, tall = _rect_solid(depth=17.0)
    assert _face_names(short) == _face_names(tall)
    for name in short.names_of(N.FACE):
        a, b = _face_by_name(short, name), _face_by_name(tall, name)
        assert a["type"] == b["type"]


def test_revolve_names_side_faces_from_edge_labels():
    b = NB.Builder("F2")
    face = K.sketch_regions(K.std_plane("Front"), [
        {"type": "line", "p1": p, "p2": q} for p, q in
        (((2, 0), (5, 0)), ((5, 0), (5, 8)), ((5, 8), (2, 8)), ((2, 8), (2, 0)))])[0]

    def label(edge):
        c = K.edge_info(K._as_edge(edge))["c"]
        if abs(c[0] - 5) < 1e-7:
            return "outer"
        if abs(c[0] - 2) < 1e-7:
            return "inner"
        return "cap_lo" if abs(c[2]) < 1e-7 else "cap_hi"
    solid = b.revolve(b.profile(face, label), (0, 0, 0), (0, 0, 1), 270.0)
    _complete_and_unique(solid)
    outer = [n for n in solid.names_of(N.FACE) if n.startswith("outer;")]
    assert len(outer) == 1
    info = _face_by_name(solid, outer[0])
    assert info["type"] == GeomAbs_Cylinder and abs(info["r"] - 5) < 1e-7


# --------------------------------------------------------------------------- labelled

def test_labelled_box_names_edges_from_faces_and_every_vertex():
    mk = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 4.0, 5.0, 6.0)
    shape = mk.Shape()
    faces = [(mk.LeftFace(), "xlo"), (mk.RightFace(), "xhi"), (mk.FrontFace(), "ylo"),
             (mk.BackFace(), "yhi"), (mk.BottomFace(), "zlo"), (mk.TopFace(), "zhi")]
    named = NB.Builder("F5").labelled(shape, face_labels=faces)
    _complete_and_unique(named)
    face_set = {lab for _, lab in faces}
    edges = named.names_of(N.EDGE)
    assert len(edges) == 12
    pairs = set()
    for name in edges:
        label = name.split(";", 1)[0]
        x, y = label.split("+")
        assert x < y and {x, y} <= face_set
        assert x[0] != y[0]              # a box edge joins two faces of different axes
        pairs.add((x, y))
    assert len(pairs) == 12
    assert len(named.names_of(N.VERTEX)) == 8
    # a vertex is named from its three edges' labels
    for name in named.names_of(N.VERTEX):
        assert name.split(";", 1)[0].count("+") == 5


def test_labelled_edge_on_named_edge_shape():
    """Edges labelled explicitly are kept; a vertex takes the labels of its edges."""
    face = _rect_face()
    named = NB.Builder("F1").labelled(face, face_labels=[(face, "plate")],
                                      edge_labels=_rect_edge_label(20.0, 10.0))
    _complete_and_unique(named)
    assert sorted(n.split(";", 1)[0] for n in named.names_of(N.EDGE)) == ["a", "b", "c", "d"]
    assert sorted(n.split(";", 1)[0] for n in named.names_of(N.VERTEX)) == ["a+b", "a+d", "b+c", "c+d"]


def test_labelled_falls_back_to_index_names_and_partial_faces_do_not_name_edges():
    mk = BRepPrimAPI_MakeBox(4.0, 5.0, 6.0)
    named = NB.Builder("F1").labelled(mk.Shape(), face_labels=[(mk.TopFace(), "top")])
    _complete_and_unique(named)
    for key in named.keys(N.EDGE):
        assert named.names[key].startswith(f"e{key[1]};PYF;")      # one face unlabelled each
    assert any(n.startswith("top;") for n in named.names_of(N.FACE))


def test_duplicate_labels_are_disambiguated():
    mk = BRepPrimAPI_MakeBox(4.0, 5.0, 6.0)
    named = NB.Builder("F1").labelled(mk.Shape(), face_labels=lambda f: "same")
    _complete_and_unique(named)
    assert sum(";D" in n for n in named.names_of(N.FACE)) == 5


def test_pairs_not_in_the_shape_are_skipped():
    mk = BRepPrimAPI_MakeBox(4.0, 5.0, 6.0)
    other = BRepPrimAPI_MakeBox(1.0, 1.0, 1.0)
    named = NB.Builder("F1").labelled(mk.Shape(), face_labels=[(other.TopFace(), "stray")])
    assert not any("stray" in n for n in named.names.values())


@pytest.mark.parametrize("bad", ["tooth3.left", "has space", "a;b", "", "x#1", 7])
def test_bad_label_raises_naming_error(bad):
    mk = BRepPrimAPI_MakeBox(4.0, 5.0, 6.0)
    with pytest.raises(N.NamingError):
        NB.Builder("F1").labelled(mk.Shape(), face_labels=[(mk.TopFace(), bad)])
    with pytest.raises(N.NamingError):
        NB.Builder("F1").labelled(mk.Shape(), face_labels=lambda f: bad)


def test_bad_primitive_label_and_key_raise():
    b = NB.Builder("F1")
    with pytest.raises(N.NamingError):
        b.cylinder(2.0, 5.0, labels={"side": "bo re"})
    with pytest.raises(N.NamingError):
        b.box(1, 1, 1, labels={"lid": "x"})


def test_profile_rejects_a_non_planar_face():
    cyl = NB.Builder("F1").cylinder(2.0, 5.0)
    side = [s for s, n in cyl.named(N.FACE) if n.startswith("side;")][0]
    with pytest.raises(K.KernelError):
        NB.Builder("F1").profile(side, None)


# --------------------------------------------------------------------------- primitives

def test_box_faces_take_their_keys_by_direction():
    box = NB.Builder("F1").box(2, 3, 4, origin=(1, 1, 1), labels={"top": "lid"})
    _complete_and_unique(box)
    expect = {"left": (0, -1), "right": (0, 1), "front": (1, -1), "back": (1, 1),
              "bottom": (2, -1), "lid": (2, 1)}
    for label, (axis, sign) in expect.items():
        names = [n for n in box.names_of(N.FACE) if n.startswith(label + ";")]
        assert len(names) == 1, label
        assert _face_by_name(box, names[0])["n"][axis] * sign > 0.9, label


def test_cylinder_side_top_bottom():
    cyl = NB.Builder("F1").cylinder(2.0, 5.0, origin=(1, 2, 3), axis=(0, 0, 1),
                                    labels={"side": "bore"})
    _complete_and_unique(cyl)
    labels = sorted(n.split(";", 1)[0] for n in cyl.names_of(N.FACE))
    assert labels == ["bore", "bottom", "top"]
    top = [n for n in cyl.names_of(N.FACE) if n.startswith("top;")][0]
    assert abs(_face_by_name(cyl, top)["c"][2] - 8.0) < 1e-7


def _plate_with_bore(grow=0.0):
    b, plate = _rect_solid(depth=6.0, x1=20.0 + grow, y1=10.0 + grow)
    pin = b.cylinder(2.0, 10.0, origin=(5, 5, -2), axis=(0, 0, 1), labels={"side": "bore"})
    return b.cut(plate, pin)


def test_cut_bore_names_the_hole_and_survives_profile_growth():
    small, big = _plate_with_bore(0.0), _plate_with_bore(5.0)
    for named in (small, big):
        _complete_and_unique(named)
    holes = []
    for named in (small, big):
        found = [n for s, n in named.named(N.FACE)
                 if K.face_info(K._as_face(s))["type"] == GeomAbs_Cylinder]
        assert len(found) == 1
        assert "bore" in found[0]
        holes.append(found[0])
    assert holes[0] == holes[1]


def test_fuse_and_common_name_completely():
    b = NB.Builder("F1")
    one = b.box(10, 10, 10)
    two = b.box(10, 10, 10, origin=(5, 5, 5), labels={k: k + "2" for k in NB.BOX_FACES})
    for named in (b.fuse(one, two), b.common(one, two)):
        _complete_and_unique(named)


# --------------------------------------------------------------------------- transformed

def test_transformed_rotation_keeps_every_name_on_the_moved_face():
    b, solid = _rect_solid()
    t = gp_Trsf()
    t.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), math.radians(90))
    moved = b.transformed(solid, t)
    _complete_and_unique(moved)
    assert sorted(moved.names.values()) == sorted(solid.names.values())
    rot = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
    for name in solid.names_of(N.FACE):
        a, m = _face_by_name(solid, name), _face_by_name(moved, name)
        assert abs(a["area"] - m["area"]) < 1e-7
        assert np.allclose(rot @ np.array(a["c"]), m["c"], atol=1e-7)


def test_transformed_rejects_scale():
    b = NB.Builder("F1")
    t = gp_Trsf()
    t.SetScale(gp_Pnt(0, 0, 0), 2.0)
    with pytest.raises(N.NamingError):
        b.transformed(b.box(1, 1, 1), t)


def test_builder_rejects_a_bad_tag():
    with pytest.raises(N.NamingError):
        NB.Builder("F.1")


def test_module_is_headless():
    with open(NB.__file__) as fh:
        assert "PySide6" not in fh.read()
