"""Naming wired into the solid features (I1.6): extrude, revolve, boolean and apply_body give every
face of a part a name built from the history of the feature, and the name survives an upstream edit
(docs/reference/element-naming.md § 4, § 5; ADR 0003).
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import naming as N  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402

from conftest import TOP, add, only_part, region_refs  # noqa: E402


# --------------------------------------------------------------------------- builders

def rect(s: Sketch, x0, y0, w, h):
    """A fully constrained w x h rectangle at (x0, y0). Returns {"lines": ids, "width": constraint id}."""
    p = [s.add_point(x0, y0), s.add_point(x0 + w, y0), s.add_point(x0 + w, y0 + h), s.add_point(x0, y0 + h)]
    lines = [s.add_line(a, b) for a, b in zip(p, p[1:] + p[:1])]
    for lid, t in zip(lines, ("horizontal", "vertical", "horizontal", "vertical")):
        s.add_constraint(t, lid)
    s.add_constraint("fix", p[0], value=[x0, y0])
    width = s.add_constraint("distance", lines[0], value=w)
    s.add_constraint("distance", lines[1], value=h)
    return {"lines": lines, "width": width}


def add_sketch(doc, x0, y0, w, h):
    s = Sketch()
    ids = rect(s, x0, y0, w, h)
    s.solve()
    f = doc.make_feature("sketch")
    f["params"].update(plane=TOP, sketch=s.to_dict())
    doc.add_feature(f)
    assert not doc.errors.get(f["id"]), doc.errors
    return f["id"], ids


def widen(doc, sid, width_id, value):
    f = dict(doc.feature(sid))
    s = Sketch.from_dict(f["params"]["sketch"])
    next(c for c in s.constraints if c["id"] == width_id["id"])["value"] = value
    s.solve()
    f["params"] = dict(f["params"], sketch=s.to_dict())
    doc.replace_feature(f)
    assert not doc.errors, doc.errors


def face_where(part, normal, along=None, at=None):
    """The one face of `part` whose normal is `normal` (and, if given, whose centre is `at` on axis `along`)."""
    hits = []
    for f in K.faces(part.shape):
        info = K.face_info(f)
        if np.allclose(info.get("n", (9, 9, 9)), normal, atol=1e-6) and (
                at is None or abs(info["c"][along] - at) < 1e-6):
            hits.append(f)
    assert len(hits) == 1, len(hits)
    return hits[0]


def assert_all_named(state, part):
    named = state.named(part.id)
    assert named is not None, "the part has no element map"
    for f in K.faces(part.shape):
        assert named.name_of(f), "a face has no name"
    for kind in (N.FACE, N.EDGE, N.VERTEX):
        assert len(named.names_of(kind)) == len(set(named.names_of(kind)))
    assert not named.unnamed()
    return named


# --------------------------------------------------------------------------- extrude

def test_extrude_names_every_face_and_survives_a_dimension_change():
    doc = Document()
    sid, ids = add_sketch(doc, 0, 0, 40, 20)
    add(doc, "extrude", regions=region_refs(doc, sid), depth=10, op="new")
    part = only_part(doc)
    named = assert_all_named(doc.state, part)
    end = named.name_of(face_where(part, (1, 0, 0)))
    top = named.name_of(face_where(part, (0, 0, 1)))
    bottom = named.name_of(face_where(part, (0, 0, -1)))
    assert len({end, top, bottom}) == 3
    widen(doc, sid, ids["width"], 55.0)
    part2 = only_part(doc)
    named2 = assert_all_named(doc.state, part2)
    moved = named2.shape_of(end)
    assert moved is not None and abs(K.face_info(K._as_face(moved))["c"][0] - 55.0) < 1e-6
    assert named2.shape_of(top) is not None and named2.shape_of(bottom) is not None


def test_side_face_names_hold_the_sketch_entity_id_and_survive_a_sketch_edit():
    doc = Document()
    sid, ids = add_sketch(doc, 0, 0, 40, 20)
    add(doc, "extrude", regions=region_refs(doc, sid), depth=10, op="new")
    part = only_part(doc)
    named = assert_all_named(doc.state, part)
    side = {}
    for n in [(0, -1, 0), (1, 0, 0), (0, 1, 0), (-1, 0, 0)]:
        name = named.name_of(face_where(part, n))
        owners = [lid for lid in ids["lines"] if f"{lid};SKT" in name]
        assert len(owners) == 1, name
        assert ";:G" in name, "a side face is generated from its sketch edge"
        side[n] = (owners[0], name)
    assert {v[0] for v in side.values()} == set(ids["lines"])
    widen(doc, sid, ids["width"], 55.0)
    named2 = assert_all_named(doc.state, only_part(doc))
    for n, (lid, name) in side.items():
        face = named2.shape_of(name)
        assert face is not None, f"side face {name} lost its name"
        assert np.allclose(K.face_info(K._as_face(face)).get("n", (9, 9, 9)), n, atol=1e-6)


# --------------------------------------------------------------------------- revolve

def test_revolve_names_every_face_and_survives_a_dimension_change():
    doc = Document()
    sid, ids = add_sketch(doc, 0, 5, 40, 10)
    add(doc, "revolve", regions=region_refs(doc, sid), axis={"kind": "std", "name": "X"}, end="full", op="new")
    part = only_part(doc)
    named = assert_all_named(doc.state, part)
    assert len(K.faces(part.shape)) == 4
    end = next(named.name_of(f) for f in K.faces(part.shape) if abs(K.face_info(f)["c"][0] - 40.0) < 1e-6)
    widen(doc, sid, ids["width"], 55.0)
    named2 = assert_all_named(doc.state, only_part(doc))
    face = named2.shape_of(end)
    assert face is not None and abs(K.face_info(K._as_face(face))["c"][0] - 55.0) < 1e-6


# --------------------------------------------------------------------------- boolean

def two_blocks():
    doc = Document()
    sa, ids = add_sketch(doc, 0, 0, 40, 20)
    a = add(doc, "extrude", regions=region_refs(doc, sa), depth=10, op="new")
    sb, _ = add_sketch(doc, 0, 0, 20, 20)
    b = add(doc, "extrude", regions=region_refs(doc, sb), depth=20, op="new")
    return doc, sa, ids, f"{a['id']}/1", f"{b['id']}/1"


@pytest.mark.parametrize("op", ["union", "subtract", "intersect"])
def test_boolean_names_every_face_and_survives_a_dimension_change(op):
    doc, sa, ids, a, b = two_blocks()
    add(doc, "boolean", op=op, tools=[b], targets=[a])
    part = doc.state.parts[b if op == "union" else a]       # a union keeps the first id of tools + targets
    named = assert_all_named(doc.state, part)
    face_a = face_where(part, (0, -1, 0))                  # the y = 0 face both blocks share
    name = named.name_of(face_a)
    widen(doc, sa, ids["width"], 55.0)
    part2 = doc.state.parts[part.id]
    named2 = assert_all_named(doc.state, part2)
    face = named2.shape_of(name)
    assert face is not None, f"{op}: the face {name} lost its name"
    assert np.allclose(K.face_info(K._as_face(face)).get("n", (9, 9, 9)), (0, -1, 0), atol=1e-6)


# --------------------------------------------------------------------------- apply_body

def test_second_extrude_keeps_the_names_of_the_untouched_faces():
    doc = Document()
    sa, _ = add_sketch(doc, 0, 0, 40, 20)
    add(doc, "extrude", regions=region_refs(doc, sa), depth=10, op="new")
    part = only_part(doc)
    named = assert_all_named(doc.state, part)
    untouched = {n: named.name_of(face_where(part, n)) for n in [(-1, 0, 0), (0, -1, 0), (0, 1, 0)]}
    sb, _ = add_sketch(doc, 30, 5, 30, 10)
    add(doc, "extrude", regions=region_refs(doc, sb), depth=10, op="add")
    part2 = only_part(doc)
    assert part2.id == part.id
    named2 = assert_all_named(doc.state, part2)
    for n, name in untouched.items():
        face = named2.shape_of(name)
        assert face is not None, f"{n}: the name {name} is gone"
        assert np.allclose(K.face_info(K._as_face(face)).get("n", (9, 9, 9)), n, atol=1e-6)
    assert named2.name_of(face_where(part2, (1, 0, 0), 0, 60.0))      # the new end face is named too


def test_cut_extrude_names_the_walls_it_makes_and_keeps_the_rest():
    doc = Document()
    sa, _ = add_sketch(doc, 0, 0, 40, 20)
    add(doc, "extrude", regions=region_refs(doc, sa), depth=10, op="new")
    part = only_part(doc)
    named = assert_all_named(doc.state, part)
    end = named.name_of(face_where(part, (1, 0, 0)))
    sb, _ = add_sketch(doc, 10, 5, 10, 10)
    add(doc, "extrude", regions=region_refs(doc, sb), depth=10, op="remove")
    part2 = only_part(doc)
    named2 = assert_all_named(doc.state, part2)
    assert named2.shape_of(end) is not None
    assert len(K.faces(part2.shape)) == 6 + 4     # a through pocket: four new walls


def test_map_is_copied_with_the_state_and_dropped_with_the_part():
    doc = Document()
    sa, _ = add_sketch(doc, 0, 0, 40, 20)
    add(doc, "extrude", regions=region_refs(doc, sa), depth=10, op="new")
    state = doc.state
    pid = only_part(doc).id
    assert state.copy().named(pid) is state.named(pid)
    copy = state.copy()
    copy.parts.pop(pid)
    assert pid not in copy.copy().names
    assert state.named(pid) is not None
    # a part whose shape was replaced without a new map has no names, rather than stale ones
    copy2 = state.copy()
    other = F.Part(pid, "x", K.compound([]) if False else K.extrude(
        [state.sketches[sa].regions[0]], np.array([0, 0, 1.0]), 3.0), (1, 1, 1))
    copy2.parts[pid] = other
    assert copy2.named(pid) is None


def test_name_feature_is_the_one_entry_point():
    state = F.ModelState()
    sk = Sketch()
    ids = rect(sk, 0, 0, 10, 10)
    sk.solve()
    F.run_sketch(state, {"id": "F1", "type": "sketch", "params": {"plane": TOP, "sketch": sk.to_dict()}})
    sr = state.sketches["F1"]
    body, hist = K.extrude(sr.regions, np.array([0, 0, 1.0]), 5.0, with_history=True)
    named = F.name_feature(state, "F2", [sr.named], body, hist, "extrude", part="F2/1")
    assert not named.unnamed()
    assert state.names["F2/1"] is named
    assert all(n.split(";")[0] in ids["lines"] or ";:" in n for n in named.names_of(N.FACE))
