"""Naming wired into fillet, chamfer, shell and draft (I1.8): each blend names the faces it makes from
the history of its maker, so after an upstream extrude depth change the feature still acts on the
element with the same name (docs/reference/element-naming.md § 4.1 and the I1.8 subsection)."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape import kernel as K  # noqa: E402
from freeshape import naming as N  # noqa: E402
from freeshape.document import Document  # noqa: E402

from conftest import TOP, add, edge_refs, face_refs, normal_is, only_part, region_refs  # noqa: E402
from test_naming_core_features import add_sketch  # noqa: E402


def block():
    doc = Document()
    sid, _ = add_sketch(doc, 0, 0, 40, 20)
    ex = add(doc, "extrude", regions=region_refs(doc, sid), depth=10, op="new")
    return doc, ex


def top_edges(i):
    return i["type"] == int(K.GeomAbs_Line) and abs(i["c"][2] - 10) < 1e-6


def set_depth(doc, ex, depth):
    f = dict(ex)
    f["params"] = dict(ex["params"], depth=depth)
    doc.replace_feature(f)
    assert not doc.errors, doc.errors


def named_faces(doc):
    part = only_part(doc)
    named = doc.state.named(part.id)
    assert named is not None, "the part has no element map"
    out = {}
    for f in K.faces(part.shape):
        n = named.name_of(f)
        assert n, "a face has no name"
        out[n] = f
    assert len(out) == len(K.faces(part.shape)), "two faces share a name"
    assert not named.unnamed()
    return out


def _axis_aligned(face):
    info = K.face_info(face)
    n = info.get("n")
    return n is not None and abs(np.max(np.abs(n)) - 1) < 1e-6


def centre_z(face):
    return K.face_info(face)["c"][2]


@pytest.mark.parametrize("kind,params", [("fillet", {"radius": 2.0}), ("chamfer", {"distance": 2.0})])
def test_edge_blend_names_its_new_faces_and_survives_a_depth_change(kind, params):
    doc, ex = block()
    add(doc, kind, edges=edge_refs(only_part(doc), top_edges, expect=4), **params)
    before = named_faces(doc)
    assert len(before) == 10                       # six box faces and four rounds / bevels
    assert all(N.OP_FOR[kind] in n for n in before)  # every face is renamed by the blend (spec § 2)
    blend = [n for n, f in before.items() if not _axis_aligned(f)]
    assert len(blend) == 4, sorted(before)
    top = next(n for n, f in before.items() if abs(K.face_info(f).get("n", (0, 0, 0))[2] - 1) < 1e-6)
    z0 = {n: centre_z(before[n]) for n in blend + [top]}
    set_depth(doc, ex, 14.0)
    after = named_faces(doc)
    assert len(after) == len(before)
    assert set(after) == set(before)
    for n, z in z0.items():
        assert abs(centre_z(after[n]) - (z + 4.0)) < 1e-6, n     # the same face, moved up by the depth change


def test_shell_names_its_faces_and_the_removed_face_has_no_name():
    doc, ex = block()
    add(doc, "shell", faces=face_refs(only_part(doc), normal_is([0, 0, 1]), expect=1), thickness=2.0)
    before = named_faces(doc)
    assert len(before) == 11                       # five outer, five inner and the rim that replaces the top
    assert all(N.OP_FOR["shell"] in n for n in before)
    up = [n for n, f in before.items() if abs(K.face_info(f).get("n", (0, 0, 0))[2] - 1) < 1e-6]
    floor = next(n for n in up if abs(centre_z(before[n]) - 2.0) < 1e-6)
    z0 = centre_z(before[floor])
    set_depth(doc, ex, 14.0)
    after = named_faces(doc)
    assert set(after) == set(before) and len(after) == len(before)
    assert abs(centre_z(after[floor]) - z0) < 1e-6           # the inner floor does not move with the top


def test_draft_names_the_tilted_walls_and_survives_a_depth_change():
    doc, ex = block()
    sides = face_refs(only_part(doc), lambda i: abs(i.get("n", (0, 0, 1))[2]) < 1e-6, expect=4)
    add(doc, "draft", faces=sides, neutral=TOP, angle=5.0)
    before = named_faces(doc)
    assert len(before) == 6
    walls = [n for n, f in before.items() if abs(K.face_info(f)["n"][2]) > 1e-6 and 0 < abs(K.face_info(f)["n"][2]) < .5]
    assert len(walls) == 4
    z0 = {n: centre_z(before[n]) for n in walls}
    set_depth(doc, ex, 14.0)
    after = named_faces(doc)
    assert set(after) == set(before) and len(after) == len(before)
    for n, z in z0.items():
        assert abs(centre_z(after[n]) - (z + 2.0)) < 0.2, n     # the wall's centre rises by half the depth change


def test_blend_op_codes_are_freecads():
    assert N.OP_FOR["fillet"] == "FLT" and N.OP_FOR["chamfer"] == "CHF"
    assert N.OP_FOR["shell"] == "THK" and N.OP_FOR["draft"] == "DFT"
