"""Naming wired into hole, thread, split, move, the face tools and offset surface (I1.9): every face of
the result has a name, and the name of a chosen face survives an upstream dimension change
(docs/reference/element-naming.md § 4.1 and § I1.9; ADR 0003).
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from OCP.GeomAbs import GeomAbs_Cone, GeomAbs_Plane  # noqa: E402

from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import naming as N  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402

from conftest import RIGHT, TOP, add, only_part, region_refs, sketch_feature  # noqa: E402


# --------------------------------------------------------------------------- builders

def block(w=40.0, d=20.0, h=10.0):
    """A w x d x h block from a fully constrained rectangle. Returns (doc, sketch id, extrude id, width id)."""
    s = Sketch()
    p = [s.add_point(0, 0), s.add_point(w, 0), s.add_point(w, d), s.add_point(0, d)]
    lines = [s.add_line(a, b) for a, b in zip(p, p[1:] + p[:1])]
    for lid, t in zip(lines, ("horizontal", "vertical", "horizontal", "vertical")):
        s.add_constraint(t, lid)
    s.add_constraint("fix", p[0], value=[0, 0])
    width = s.add_constraint("distance", lines[0], value=w)
    s.add_constraint("distance", lines[1], value=d)
    s.solve()
    doc = Document()
    f = doc.make_feature("sketch")
    f["params"].update(plane=TOP, sketch=s.to_dict())
    doc.add_feature(f)
    ex = add(doc, "extrude", regions=region_refs(doc, f["id"]), depth=h, op="new")
    return doc, f["id"], ex["id"], width["id"]


def widen(doc, sid, width_id, value):
    f = dict(doc.feature(sid))
    s = Sketch.from_dict(f["params"]["sketch"])
    next(c for c in s.constraints if c["id"] == width_id)["value"] = value
    s.solve()
    f["params"] = dict(f["params"], sketch=s.to_dict())
    doc.replace_feature(f)
    assert not doc.errors, doc.errors


def set_depth(doc, fid, depth):
    f = dict(doc.feature(fid))
    f["params"] = dict(f["params"], depth=depth)
    doc.replace_feature(f)
    assert not doc.errors, doc.errors


def point_sketch(doc, x, y):
    return sketch_feature(doc, lambda s: s.add_point_entity(s.add_point(x, y)))


def faces_where(part, pred):
    return [f for f in K.faces(part.shape) if pred(K.face_info(f))]


def one_face(part, pred):
    hits = faces_where(part, pred)
    assert len(hits) == 1, len(hits)
    return hits[0]


def facing(n, axis=None, value=None):
    """A predicate: a planar face with normal `n`, and, if given, its centre's coordinate `axis` equal to `value`."""
    def pred(i):
        return (i.get("n") is not None and np.allclose(i["n"], n, atol=1e-6)
                and (axis is None or abs(i["c"][axis] - value) < 1e-6))
    return pred


def round_face(r):
    return lambda i: abs(i.get("r", 0) - r) < 1e-6


def is_cone(i):
    return i["type"] == int(GeomAbs_Cone)


def assert_all_named(state, part):
    named = state.named(part.id)
    assert named is not None, "the part has no element map"
    for f in K.faces(part.shape):
        assert named.name_of(f), "a face has no name"
    for kind in (N.FACE, N.EDGE, N.VERTEX):
        assert len(named.names_of(kind)) == len(set(named.names_of(kind))), f"a {kind} name is used twice"
    assert not named.unnamed()
    return named


def name_of(state, part, pred):
    return assert_all_named(state, part).name_of(one_face(part, pred))


def follows(state, part, name, pred):
    """The face `name` names in the rebuilt part satisfies `pred`."""
    face = assert_all_named(state, part).shape_of(name)
    assert face is not None, f"the name {name} is gone"
    assert pred(K.face_info(K._as_face(face))), name


# --------------------------------------------------------------------------- hole

def test_simple_hole_names_its_wall_and_keeps_it_when_the_block_widens():
    doc, sid, _ex, width = block()
    hs = point_sketch(doc, 10, 10)
    add(doc, "hole", sketch=hs, standard="custom", diameter=6.0, end="through", style="simple")
    part = only_part(doc)
    wall = name_of(doc.state, part, round_face(3))
    assert doc.state.named(part.id).name_of(one_face(part, round_face(3))) == wall
    assert ";" in wall and ";:" in wall, wall
    end = name_of(doc.state, part, facing((1, 0, 0)))
    assert len(K.faces(part.shape)) == 7
    widen(doc, sid, width, 55.0)
    part2 = only_part(doc)
    follows(doc.state, part2, wall, round_face(3))
    follows(doc.state, part2, end, lambda i: abs(i["c"][0] - 55.0) < 1e-6)


def test_hole_wall_names_carry_the_feature_id_of_the_tool():
    doc, _sid, _ex, _w = block()
    hs = point_sketch(doc, 10, 10)
    hole = add(doc, "hole", sketch=hs, standard="custom", diameter=6.0, end="through", style="simple")
    part = only_part(doc)
    wall = name_of(doc.state, part, round_face(3))
    assert f"{N._tag_text(hole['id'])}" in wall, (wall, hole["id"])


def test_counterbore_hole_names_both_walls_and_the_step_floor():
    doc, sid, _ex, width = block()
    hs = point_sketch(doc, 10, 10)
    add(doc, "hole", sketch=hs, standard="custom", diameter=5.0, end="through", style="counterbore",
        counterbore_dia=10.0, counterbore_depth=3.0)
    part = only_part(doc)
    bore = name_of(doc.state, part, round_face(2.5))
    cbore = name_of(doc.state, part, round_face(5.0))
    floor = name_of(doc.state, part, lambda i: i.get("n") is not None and abs(abs(i["n"][2]) - 1) < 1e-6
                    and i["area"] < 60)
    assert len({bore, cbore, floor}) == 3
    widen(doc, sid, width, 55.0)
    part2 = only_part(doc)
    follows(doc.state, part2, bore, round_face(2.5))
    follows(doc.state, part2, cbore, round_face(5.0))
    follows(doc.state, part2, floor, lambda i: i["area"] < 60)


def test_blind_hole_names_the_drill_point_and_two_holes_do_not_share_names():
    doc, sid, _ex, width = block()
    hs = sketch_feature(doc, lambda s: (s.add_point_entity(s.add_point(10, 10)),
                                        s.add_point_entity(s.add_point(25, 10))))
    add(doc, "hole", sketch=hs, standard="custom", diameter=6.0, end="blind", depth=6.0, tip=True,
        style="simple")
    part = only_part(doc)
    named = assert_all_named(doc.state, part)
    cones = faces_where(part, is_cone)
    assert len(cones) == 2
    cone_names = [named.name_of(c) for c in cones]
    assert len(set(cone_names)) == 2
    widen(doc, sid, width, 55.0)
    part2 = only_part(doc)
    for n in cone_names:
        follows(doc.state, part2, n, is_cone)


def test_modelled_tapped_hole_names_the_thread_flanks():
    doc, sid, _ex, width = block()
    hs = point_sketch(doc, 10, 10)
    add(doc, "hole", sketch=hs, standard="tapped", size="M5", end="through", model_thread=True)
    part = only_part(doc)
    named = assert_all_named(doc.state, part)
    top = name_of(doc.state, part, facing((0, 0, 1), 2, 10.0))
    flanks = [f for f in K.faces(part.shape) if K.face_info(f)["type"] != int(GeomAbs_Plane)
              and not round_face(2.5)(K.face_info(f)) and abs(K.face_info(f)["c"][0] - 10) < 3]
    assert flanks
    names = [named.name_of(f) for f in flanks]
    widen(doc, sid, width, 55.0)
    part2 = only_part(doc)
    follows(doc.state, part2, top, facing((0, 0, 1), 2, 10.0))
    named2 = assert_all_named(doc.state, part2)
    assert all(named2.shape_of(n) is not None for n in names)


# --------------------------------------------------------------------------- thread

def test_internal_thread_names_every_face_and_keeps_the_end_face():
    doc, sid, _ex, width = block()
    hs = point_sketch(doc, 10, 10)
    add(doc, "hole", sketch=hs, standard="custom", diameter=5.0, end="through", style="simple")
    part = only_part(doc)
    end = name_of(doc.state, part, facing((1, 0, 0)))
    wall = one_face(part, round_face(2.5))
    add(doc, "thread", faces=[F.make_ref(part.id, wall, "face", part.shape)], size="M6")
    part2 = only_part(doc)
    named = assert_all_named(doc.state, part2)
    assert len(K.faces(part2.shape)) > len(K.faces(part.shape))
    widen(doc, sid, width, 55.0)
    part3 = only_part(doc)
    follows(doc.state, part3, end, lambda i: abs(i["c"][0] - 55.0) < 1e-6)
    assert len(named.names_of(N.FACE)) == len(K.faces(part2.shape))


def test_external_thread_names_the_shaft_ends_and_survives_a_depth_change():
    doc = Document()
    sid = sketch_feature(doc, lambda s: s.add_circle(s.add_point(0, 0), 3))
    ex = add(doc, "extrude", regions=region_refs(doc, sid), depth=20.0, op="new")
    part = only_part(doc)
    bottom = name_of(doc.state, part, facing((0, 0, -1)))
    shaft = one_face(part, round_face(3))
    add(doc, "thread", faces=[F.make_ref(part.id, shaft, "face", part.shape)], size="M6",
        length_mode="length", length=12.0)
    part2 = only_part(doc)
    named = assert_all_named(doc.state, part2)
    assert len(K.faces(part2.shape)) > 3
    # the rod replaces the shaft's open end: its end face is the part's new top, named from the rod's tool
    top_face = one_face(part2, facing((0, 0, 1), 2, 20.0))
    top = named.name_of(top_face)
    assert top.startswith("s1t1"), top
    set_depth(doc, ex["id"], 30.0)
    part3 = only_part(doc)
    follows(doc.state, part3, bottom, facing((0, 0, -1)))
    follows(doc.state, part3, top, facing((0, 0, 1), 2, 30.0))


# --------------------------------------------------------------------------- split

def split_doc(keep):
    doc, sid, _ex, width = block()
    pl = add(doc, "plane", type="offset", refs=[], ref=RIGHT, offset=10.0)
    add(doc, "split", parts=list(doc.state.parts), plane={"kind": "plane", "fid": pl["id"]}, keep=keep)
    return doc, sid, width


def test_split_names_both_pieces_and_keeps_the_far_end_name():
    doc, sid, width = split_doc("both")
    assert len(doc.state.parts) == 2
    big = max(doc.state.parts.values(), key=lambda p: K.volume(p.shape))
    small = min(doc.state.parts.values(), key=lambda p: K.volume(p.shape))
    nb, ns = assert_all_named(doc.state, big), assert_all_named(doc.state, small)
    cut_big = nb.name_of(one_face(big, facing((-1, 0, 0), 0, 10.0)))
    cut_small = ns.name_of(one_face(small, facing((1, 0, 0), 0, 10.0)))
    assert cut_big == cut_small      # one face of the splitter's result, shared by the two solids
    front_big = nb.name_of(one_face(big, facing((0, -1, 0))))
    front_small = ns.name_of(one_face(small, facing((0, -1, 0))))
    # the y = 0 face was cut in two: both pieces are modified from it, so the second takes the `;D` fragment
    assert front_big != front_small
    short, long_ = sorted((front_big, front_small), key=len)
    assert long_.startswith(short) and ";D1;" in long_, (front_big, front_small)
    end = nb.name_of(one_face(big, facing((1, 0, 0), 0, 40.0)))
    assert end in nb.names.values() and end not in ns.names.values()
    widen(doc, sid, width, 55.0)
    big2 = max(doc.state.parts.values(), key=lambda p: K.volume(p.shape))
    follows(doc.state, big2, end, lambda i: abs(i["c"][0] - 55.0) < 1e-6)
    follows(doc.state, big2, cut_big, lambda i: abs(i["c"][0] - 10.0) < 1e-6)


def test_split_keep_one_side_names_the_remaining_piece():
    doc, sid, width = split_doc("front")
    part = only_part(doc)
    named = assert_all_named(doc.state, part)
    assert len(K.faces(part.shape)) == 6
    top = named.name_of(one_face(part, facing((0, 0, 1))))
    widen(doc, sid, width, 55.0)
    follows(doc.state, only_part(doc), top, facing((0, 0, 1)))


# --------------------------------------------------------------------------- move

def test_move_renames_every_face_under_the_move_and_keeps_the_end_face():
    doc, sid, _ex, width = block()
    part = only_part(doc)
    before = name_of(doc.state, part, facing((1, 0, 0)))
    add(doc, "move", parts=[part.id], dx=5.0, dy=0.0, dz=0.0, rotate=0.0, copy=False)
    part2 = only_part(doc)
    named = assert_all_named(doc.state, part2)
    end = named.name_of(one_face(part2, facing((1, 0, 0))))
    assert end != before and "XFM" in end and end.startswith(before.split(";")[0]), (before, end)
    widen(doc, sid, width, 55.0)
    follows(doc.state, only_part(doc), end, lambda i: abs(i["c"][0] - 60.0) < 1e-6)


def test_move_copy_names_the_copy_apart_from_the_original():
    doc, sid, _ex, width = block()
    part = only_part(doc)
    orig = name_of(doc.state, part, facing((1, 0, 0)))
    add(doc, "move", parts=[part.id], dx=0.0, dy=40.0, dz=0.0, rotate=0.0, copy=True)
    assert len(doc.state.parts) == 2
    a, b = [doc.state.parts[k] for k in sorted(doc.state.parts)]
    na, nb = assert_all_named(doc.state, a), assert_all_named(doc.state, b)
    assert na.name_of(one_face(a, facing((1, 0, 0)))) == orig
    copy_name = nb.name_of(one_face(b, facing((1, 0, 0))))
    assert copy_name != orig and "XFM" in copy_name
    widen(doc, sid, width, 55.0)
    b2 = doc.state.parts[b.id]
    follows(doc.state, b2, copy_name, lambda i: abs(i["c"][0] - 55.0) < 1e-6 and abs(i["c"][1] - 50.0) < 1e-6)


# --------------------------------------------------------------------------- face tools

def test_move_face_names_the_moved_face_and_the_new_walls():
    doc, sid, _ex, width = block()
    part = only_part(doc)
    top = one_face(part, facing((0, 0, 1)))
    add(doc, "move_face", faces=[F.make_ref(part.id, top, "face", part.shape)], distance=4.0)
    part2 = only_part(doc)
    named = assert_all_named(doc.state, part2)
    moved = named.name_of(one_face(part2, facing((0, 0, 1), 2, 14.0)))
    side = named.name_of(one_face(part2, facing((1, 0, 0))))
    assert len(K.faces(part2.shape)) == 6
    widen(doc, sid, width, 55.0)
    part3 = only_part(doc)
    follows(doc.state, part3, moved, facing((0, 0, 1), 2, 14.0))
    follows(doc.state, part3, side, lambda i: abs(i["c"][0] - 55.0) < 1e-6)


def test_move_face_pull_keeps_the_names_of_the_other_faces():
    doc, sid, _ex, width = block()
    part = only_part(doc)
    bottom = name_of(doc.state, part, facing((0, 0, -1)))
    top = one_face(part, facing((0, 0, 1)))
    add(doc, "move_face", faces=[F.make_ref(part.id, top, "face", part.shape)], distance=3.0, flip=True)
    part2 = only_part(doc)
    named = assert_all_named(doc.state, part2)
    assert named.shape_of(bottom) is not None
    moved = named.name_of(one_face(part2, facing((0, 0, 1), 2, 7.0)))
    widen(doc, sid, width, 55.0)
    follows(doc.state, only_part(doc), moved, facing((0, 0, 1), 2, 7.0))


def test_delete_face_heal_fills_the_hole_and_keeps_the_block_names():
    doc, sid, _ex, width = block()
    hs = point_sketch(doc, 10, 10)
    add(doc, "hole", sketch=hs, standard="custom", diameter=6.0, end="through", style="simple")
    part = only_part(doc)
    bottom = name_of(doc.state, part, facing((0, 0, -1)))
    end = name_of(doc.state, part, facing((1, 0, 0)))
    wall = one_face(part, round_face(3))
    add(doc, "delete_face", faces=[F.make_ref(part.id, wall, "face", part.shape)], mode="heal")
    part2 = only_part(doc)
    named = assert_all_named(doc.state, part2)
    assert len(K.faces(part2.shape)) == 6
    # defeaturing rebuilds every face, so a face keeps its name as the stem of the new one
    for old in (bottom, end):
        assert any(n.startswith(old) for n in named.names_of(N.FACE)), old
    follows(doc.state, part2, next(n for n in named.names_of(N.FACE) if n.startswith(end)),
            facing((1, 0, 0), 0, 40.0))
    widen(doc, sid, width, 55.0)
    follows(doc.state, only_part(doc), next(n for n in doc.state.named(part2.id).names_of(N.FACE)
                                            if n.startswith(end)), lambda i: abs(i["c"][0] - 55.0) < 1e-6)


def test_delete_face_cap_names_the_cap_face():
    doc, sid, _ex, width = block()
    hs = point_sketch(doc, 10, 10)
    add(doc, "hole", sketch=hs, standard="custom", diameter=6.0, end="through", style="simple")
    part = only_part(doc)
    end = name_of(doc.state, part, facing((1, 0, 0)))
    wall = one_face(part, round_face(3))
    add(doc, "delete_face", faces=[F.make_ref(part.id, wall, "face", part.shape)], mode="cap")
    part2 = only_part(doc)
    assert_all_named(doc.state, part2)
    widen(doc, sid, width, 55.0)
    follows(doc.state, only_part(doc), end, lambda i: abs(i["c"][0] - 55.0) < 1e-6)


# --------------------------------------------------------------------------- offset surface

def test_offset_surface_names_the_new_face_from_the_picked_face():
    doc, sid, _ex, width = block()
    part = only_part(doc)
    top = one_face(part, facing((0, 0, 1)))
    src = name_of(doc.state, part, facing((0, 0, 1)))
    add(doc, "offset_surface", faces=[F.make_ref(part.id, top, "face", part.shape)], distance=4.0)
    assert len(doc.state.parts) == 2
    sheet = next(p for p in doc.state.parts.values() if p.id != part.id)
    named = doc.state.named(sheet.id)
    assert named is not None and not named.unnamed()
    face = K.faces(sheet.shape)[0]
    name = named.name_of(face)
    assert name.startswith(src) and "OFS" in name, (src, name)
    widen(doc, sid, width, 55.0)
    sheet2 = doc.state.parts[sheet.id]
    follows(doc.state, sheet2, name, lambda i: abs(i["c"][2] - 14.0) < 1e-6 and abs(i["area"] - 55 * 20) < 1e-3)
