"""Naming wired into sheet metal (I1.11): the base, a flange and the flat pattern name their faces, and a name
survives a change of the panel size (docs/reference/element-naming.md, "I1.11 Sheet metal")."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape import kernel as K  # noqa: E402
from freeshape import naming as N  # noqa: E402
from freeshape import sheetmetal as SM  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402

from conftest import TOP, add, edge_refs, only_part, region_refs  # noqa: E402

T = 1.5


def sheet_doc(w=80.0, h=50.0):
    """A w x h sheet from a fully constrained rectangle; returns (doc, sketch id, width constraint id)."""
    doc = Document()
    s = Sketch()
    p = [s.add_point(0, 0), s.add_point(w, 0), s.add_point(w, h), s.add_point(0, h)]
    lines = [s.add_line(a, b) for a, b in zip(p, p[1:] + p[:1])]
    for lid, t in zip(lines, ("horizontal", "vertical", "horizontal", "vertical")):
        s.add_constraint(t, lid)
    s.add_constraint("fix", p[0], value=[0, 0])
    width = s.add_constraint("distance", lines[0], value=w)
    s.add_constraint("distance", lines[1], value=h)
    s.solve()
    f = doc.make_feature("sketch")
    f["params"].update(plane=TOP, sketch=s.to_dict())
    doc.add_feature(f)
    assert not doc.errors, doc.errors
    add(doc, "sm_base", regions=region_refs(doc, f["id"]), thickness=T, radius=1.5, k=0.44)
    return doc, f["id"], width


def widen(doc, sid, width_id, value):
    f = dict(doc.feature(sid))
    s = Sketch.from_dict(f["params"]["sketch"])
    next(c for c in s.constraints if c["id"] == width_id["id"])["value"] = value
    s.solve()
    f["params"] = dict(f["params"], sketch=s.to_dict())
    doc.replace_feature(f)
    assert not doc.errors, doc.errors


def right_edge(doc):
    """The top edge on the right-hand side of the sheet (along y, at the largest x)."""
    part = only_part(doc)
    hi = round(float(K.bbox(part.shape)[1][0]), 6)
    return edge_refs(part, lambda i: "p0" in i and abs(i["p0"][0] - hi) < 1e-9 and abs(i["p1"][0] - hi) < 1e-9
                     and abs(i["p0"][2] - T) < 1e-9, expect=1)


def assert_all_named(named, shape):
    assert named is not None, "no element map"
    for f in K.faces(shape):
        assert named.name_of(f), "a face has no name"
    for kind in (N.FACE, N.EDGE):
        names = named.names_of(kind)
        assert len(names) == len(set(names))
    return named


def edge_at(shape, x=None, y=None, z=None):
    out = []
    for e in K.edges(shape):
        i = K.edge_info(e)
        if "p0" not in i:
            continue
        c = (np.asarray(i["p0"]) + np.asarray(i["p1"])) / 2
        if all(v is None or abs(c[k] - v) < 1e-6 for k, v in enumerate((x, y, z))):
            out.append(e)
    assert len(out) == 1, len(out)
    return out[0]


def face_at(shape, normal, z):
    out = [f for f in K.faces(shape) if np.allclose(K.face_info(f).get("n", (9, 9, 9)), normal, atol=1e-6)
           and abs(K.face_info(f)["c"][2] - z) < 1e-6]
    assert len(out) == 1, len(out)
    return out[0]


# --------------------------------------------------------------------------- base

def test_base_names_every_face_like_an_extrude_and_keeps_them_when_resized():
    doc, sid, width = sheet_doc()
    part = only_part(doc)
    named = assert_all_named(doc.state.named(part.id), part.shape)
    sides = [named.name_of(f) for f in K.faces(part.shape) if abs(K.face_info(f)["n"][2]) < 1e-6]
    assert len(sides) == 4 and all(";SKT" in n and ";:G" in n for n in sides)
    right = named.name_of(face_at_x(part.shape, 80.0))
    widen(doc, sid, width, 100.0)
    part2 = only_part(doc)
    named2 = assert_all_named(doc.state.named(part2.id), part2.shape)
    moved = named2.shape_of(right)
    assert moved is not None and abs(K.face_info(K._as_face(moved))["c"][0] - 100.0) < 1e-6


def face_at_x(shape, x):
    out = [f for f in K.faces(shape) if abs(K.face_info(f)["c"][0] - x) < 1e-6 and abs(K.face_info(f)["n"][0]) > 0.5]
    assert len(out) == 1
    return out[0]


# --------------------------------------------------------------------------- flange

def test_flange_names_its_faces_and_keeps_the_attachment_edge_name_when_the_panel_is_resized():
    doc, sid, width = sheet_doc()
    base_names = set(doc.state.named(only_part(doc).id).names.values())
    add(doc, "sm_flange", edges=right_edge(doc), length=20.0)
    part = only_part(doc)
    named = assert_all_named(doc.state.named(part.id), part.shape)
    # the sheet's own faces keep the exact names the base gave them
    assert base_names & set(named.names.values())
    assert not named.unnamed()
    attach = named.name_of(edge_at(part.shape, x=80.0, y=25.0, z=T))
    top_face = named.name_of(face_at(part.shape, (0, 0, 1), T))
    widen(doc, sid, width, 100.0)
    part2 = only_part(doc)
    named2 = assert_all_named(doc.state.named(part2.id), part2.shape)
    e2 = named2.shape_of(attach)
    assert e2 is not None, "the attachment edge lost its name"
    assert np.allclose(np.asarray(K.edge_info(K._as_edge(e2))["p0"])[[0, 2]], [100.0, T], atol=1e-6)
    f2 = named2.shape_of(top_face)
    assert f2 is not None and abs(K.face_info(K._as_face(f2))["c"][2] - T) < 1e-6
    assert len(doc.state.sheet[part2.id].bends) == 1


# --------------------------------------------------------------------------- flat pattern

def flat(doc):
    part = only_part(doc)
    return SM.flat_pattern(part.shape, doc.state.sheet[part.id], doc.state.named(part.id))


def test_flat_pattern_faces_take_names_from_their_folded_faces_and_keep_them_when_resized():
    doc, sid, width = sheet_doc()
    add(doc, "sm_flange", edges=right_edge(doc), length=20.0)
    part = only_part(doc)
    fp = flat(doc)
    assert fp.named is not None and fp.named.shape.IsSame(fp.solid())
    folded = doc.state.named(part.id)
    bottom_flat = fp.named.name_of(face_at(fp.solid(), (0, 0, -1), 0.0))
    assert bottom_flat and f";{N.OP_FOR['flat_pattern']};:H{SM.FLAT_TAG}" in bottom_flat, bottom_flat
    # the folded counterpart's name is the base of the flat name
    folded_bottom = folded.name_of(face_at(part.shape, (0, 0, -1), 0.0))
    assert folded_bottom and bottom_flat.startswith(folded_bottom.split(";")[0])
    assert_all_named(fp.named, fp.solid())
    widen(doc, sid, width, 100.0)
    fp2 = flat(doc)
    again = fp2.named.shape_of(bottom_flat)
    assert again is not None, "the flat pattern face lost its name"
    lo, hi = K.bbox(K._as_face(again))
    lo0, hi0 = K.bbox(K._as_face(fp.named.shape_of(bottom_flat)))
    assert abs((hi[0] - lo[0]) - (hi0[0] - lo0[0]) - 20.0) < 1e-6


def test_flat_pattern_without_names_is_unchanged():
    doc, sid, width = sheet_doc()
    add(doc, "sm_flange", edges=right_edge(doc), length=20.0)
    part = only_part(doc)
    fp = SM.flat_pattern(part.shape, doc.state.sheet[part.id])
    assert fp.named is None
    with_names = flat(doc)
    assert abs(K.volume(fp.solid()) - K.volume(with_names.solid())) < 1e-6
