"""The element-naming gate (I1.15): the scenarios of docs/reference/element-naming.md section 9, built the way
the app builds a model -- Documents, sketches and feature runners -- and checked through the references the
features store.

Every scenario asserts the policy of ADR 0003: a named reference lands on the element of that name or the
feature ends in `error`; it never lands on a look-alike. A reference with no name is matched by signature and
the feature is marked `recovered`. The same scenarios run headless in scripts/smoke/s1_naming.py.

A feature's status is read the way the feature list reads it: in `doc.errors` it is `error`, in
`doc.state.recovered` it is `recovered`, else `ok`.
"""
import copy
import json
import os
import re
import shutil
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape import assembly as A  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import links as L  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.drawing import DrawingDoc  # noqa: E402
from freeshape.sketch import ORIGIN, Sketch  # noqa: E402

from OCP.GeomAbs import GeomAbs_Cone  # noqa: E402

from conftest import (EXAMPLES_DIR, FREEFORM, TOP, add, assert_golden, centre_near, both, line_along,  # noqa: E402
                      normal_is, only_part, rect_lines, region_refs, shape_fingerprint, sketch_feature,
                      state_fingerprint)

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "part_ids")
GOLDEN = "scenarios_naming"
RIGHT = {"kind": "std", "name": "Right"}


# =========================================================================== builders

def dim_block(w=40.0, d=20.0, h=10.0, x0=0.0):
    """A w x d x h block from a fully dimensioned rectangle whose first corner is fixed at (x0, 0).
    Returns (doc, sketch id, extrude id, {"w": id, "d": id} of the dimension constraints)."""
    doc = Document()
    s = Sketch()
    p0 = ORIGIN if x0 == 0 else s.add_point(x0, 0)
    p = [p0, s.add_point(x0 + w, 0), s.add_point(x0 + w, d), s.add_point(x0, d)]
    ln = [s.add_line(a, b) for a, b in zip(p, p[1:] + p[:1])]
    for line, t in zip(ln, ["horizontal", "vertical", "horizontal", "vertical"]):
        s.add_constraint(t, line)
    if x0 != 0:
        s.add_constraint("fix", p0, value=[x0, 0.0])
    dims = {"w": s.add_constraint("distance", ln[0], value=w)["id"],
            "d": s.add_constraint("distance", ln[1], value=d)["id"]}
    assert s.solve() and s.status["dof"] == 0
    f = doc.make_feature("sketch")
    f["params"]["sketch"] = s.to_dict()
    doc.add_feature(f)
    ex = add(doc, "extrude", regions=region_refs(doc, f["id"]), depth=h, op="new")
    return doc, f["id"], ex["id"], dims


def set_dim(doc, sid, cid, value):
    f = copy.deepcopy(doc.feature(sid))
    s = Sketch.from_dict(f["params"]["sketch"])
    for c in s.constraints:
        if c["id"] == cid:
            c["value"] = float(value)
    assert s.solve()
    f["params"]["sketch"] = s.to_dict()
    doc.replace_feature(f)


def edit_sketch(doc, sid, change):
    """Apply change(sketch) to the sketch feature `sid`, re-solve and rebuild."""
    f = copy.deepcopy(doc.feature(sid))
    s = Sketch.from_dict(f["params"]["sketch"])
    change(s)
    assert s.solve()
    f["params"]["sketch"] = s.to_dict()
    doc.replace_feature(f)


def set_param(doc, fid, **params):
    f = copy.deepcopy(doc.feature(fid))
    f["params"].update(params)
    doc.replace_feature(f)


def status(doc, fid):
    """The status of a feature: `error`, `recovered` or `ok`."""
    if fid in doc.errors:
        return "error"
    return "recovered" if fid in doc.state.recovered else "ok"


def part(doc, pid=None):
    return doc.state.parts[pid] if pid else only_part(doc)


def pick(doc, kind, pred, pid=None):
    """The one face / edge of the part (default: the only one) whose info satisfies `pred`."""
    p = part(doc, pid)
    subs = K.faces(p.shape) if kind == "face" else K.edges(p.shape)
    info = K.face_info if kind == "face" else K.edge_info
    hits = [s for s in subs if pred(info(s))]
    assert len(hits) == 1, f"{len(hits)} {kind}s match"
    return hits[0]


def ref_to(doc, kind, pred, pid=None):
    """A reference to the one face / edge matching `pred`, made the way the dialogs make one: with its name."""
    p = part(doc, pid)
    sub = pick(doc, kind, pred, pid)
    ref = F.make_ref(p.id, sub, kind, p.shape, doc.state.named(p.id))
    assert ref.get("name"), "the picked element has no name"
    return ref


def resolve(doc, ref, kind):
    """(part id, element) a stored reference finds now. Raises K.KernelError when its name is lost."""
    return F.resolve(doc.state, ref, kind, fid="probe")


def info_of(sub, kind):
    return K.face_info(sub) if kind == "face" else K.edge_info(sub)


def lands_on_its_name(doc, ref, kind):
    """Resolve `ref` by name and check the element found carries that same name; returns (part id, info)."""
    pid, sub = resolve(doc, ref, kind)
    assert doc.state.named(pid).name_of(sub) == ref["name"]
    return pid, info_of(sub, kind)


def face_plane(doc, ref, origin=None, xdir=None):
    """A sketch plane on the face `ref`, with the origin and x axis stored the way the dialog stores them.
    Pass `origin` and `xdir` for a face that exists only part-way down the feature list."""
    if origin is None:
        pl = K.planar_face_plane(resolve(doc, ref, "face")[1])
        origin, xdir = pl.origin, pl.xdir
    return {"kind": "face", "face": ref, "origin": list(map(float, origin)), "xdir": list(map(float, xdir))}


def pad_on(doc, face_ref, half=3.0, depth=4.0):
    """A sketch on the face (a square around the sketch origin) and an add extrude of it. Returns (sketch, pad)."""
    sid = sketch_feature(doc, lambda s: rect_lines(s, -half, -half, 2 * half, 2 * half), face_plane(doc, face_ref))
    pad = add(doc, "extrude", regions=region_refs(doc, sid), depth=depth, op="add")
    return sid, pad


def vertical_edge(x, y):
    return both(line_along([0, 0, 1]), lambda i: abs(i["c"][0] - x) < 1e-6 and abs(i["c"][1] - y) < 1e-6)


def top_at(z):
    return both(normal_is([0, 0, 1]), lambda i: abs(i["c"][2] - z) < 1e-6)


def insert_before(doc, fid, ftype, **params):
    """A feature added at the position of `fid`, pushing it and everything after it down."""
    f = doc.make_feature(ftype)
    f["params"].update(params)
    doc.add_feature(f, index=doc.feature_index(fid))
    return f


def insert_sketch_before(doc, fid, build, plane=None):
    """A sketch added at the position of `fid`. Its plane may name a face that exists only at that point of the
    feature list, so it is not checked against the end of the list the way `sketch_feature` checks."""
    s = Sketch()
    build(s)
    assert s.solve()
    return insert_before(doc, fid, "sketch", plane=plane or TOP, sketch=s.to_dict())["id"]


def named_part_state(doc, pid):
    return sorted(doc.state.named(pid).names.values())


def record(name, before, doc, tol=FREEFORM, **extra):
    assert_golden(f"{GOLDEN}/{name}", {"before": before, "after": state_fingerprint(doc), **extra}, tol)


# =========================================================================== section 9 scenarios
#
# A feature stores the reference it was given when it was made, so a reference is taken from the part at the
# point of the feature list where it is used. Fillets, chamfers and fuses re-encode the names of the faces they
# touch, so the references the assertions follow are taken from the finished part, before the edit.

def final_refs(doc, spec, pid=None):
    """{label: (kind, named reference)} from the finished part. `spec` is {label: (kind, predicate)}."""
    return {k: (kind, ref_to(doc, kind, pred, pid)) for k, (kind, pred) in spec.items()}


def places(doc, refs):
    """{label: (part id, centre)} of the elements the references find by name; each must carry its own name."""
    out = {}
    for k, (kind, ref) in refs.items():
        pid, info = lands_on_its_name(doc, ref, kind)
        out[k] = (pid, np.array(info["c"]))
    return out


def round_face(r):
    return lambda i: abs(i.get("r", 0) - r) < 1e-6


def test_sketch_dimension_change():
    """Section 9, "Sketch dimension change": a datum on the base sketch is edited under a downstream
    sketch-on-face and pad (testRectanglewithArcChangeinGlobalCenter, ...UpperRight). The dimension belongs to
    the sketch, not to a feature."""
    doc, sid, ex, dims = dim_block()
    pad_sid, pad = pad_on(doc, ref_to(doc, "face", top_at(10.0)))          # the downstream sketch-on-face and pad
    fil = add(doc, "fillet", edges=[ref_to(doc, "edge", vertical_edge(40, 20))], radius=2.0)
    refs = final_refs(doc, {
        "pad_top": ("face", top_at(14.0)),
        "corner_round": ("face", round_face(2.0)),
        "end_face": ("face", both(normal_is([-1, 0, 0]), lambda i: abs(i["c"][0]) < 1e-6)),
    })
    before = state_fingerprint(doc)
    where = places(doc, refs)
    assert not doc.errors and not doc.state.recovered

    set_dim(doc, sid, dims["w"], 60.0)                                     # the sketch dimension, 40 -> 60

    assert not doc.errors, doc.errors
    assert not doc.state.recovered
    for fid in (pad_sid, pad["id"], fil["id"]):
        assert status(doc, fid) == "ok"
    now = places(doc, refs)                                                # each name finds its element again
    assert np.allclose(now["pad_top"][1], where["pad_top"][1])             # the pad stayed where it was sketched
    assert np.allclose(now["end_face"][1], where["end_face"][1])
    assert now["corner_round"][1][0] > where["corner_round"][1][0] + 15    # the rounded corner followed its edge
    assert np.allclose(K.bbox(only_part(doc).shape)[1], [60, 20, 14])
    record("sketch_dimension_change", before, doc)


def _curve_doc():
    """A sketch of a circle (made first) and a 40 x 20 rectangle, the rectangle extruded 10. Returns
    (doc, sketch id, the rectangle's region reference)."""
    doc = Document()
    sid = sketch_feature(doc, lambda s: (s.add_circle(s.add_point(70, 10), 5), rect_lines(s, 0, 0, 40, 20)), TOP)
    block = region_refs(doc, sid, lambda i: abs(i["area"] - 800) < 1)
    assert len(block) == 1
    add(doc, "extrude", regions=block, depth=10.0, op="new")
    return doc, sid, block


def add_overlapping_circle(doc, sid):
    """The added curve: a circle overlapping the one at x = 70, which reorders the sketch's regions."""
    edit_sketch(doc, sid, lambda s: s.add_circle(s.add_point(74, 10), 5))


def test_sketch_curve_added_references_resolve_to_the_same_elements():
    """Section 9, "Sketch curve added" (testChangeSketch), the reference level: a fillet edge and a side face of
    the extrude are found by name after curves are added to the sketch under it, which reorders the sketch's
    regions. The elements are the same ones, under the same names."""
    doc, sid, block = _curve_doc()
    side = ref_to(doc, "face", normal_is([1, 0, 0]))
    edge = ref_to(doc, "edge", vertical_edge(0, 0))
    where = places(doc, {"side": ("face", side), "edge": ("edge", edge)})
    order_before = list(doc.state.sketches[sid].region_names)

    add_overlapping_circle(doc, sid)

    order_after = list(doc.state.sketches[sid].region_names)
    assert order_after.index(block[0]["name"]) != order_before.index(block[0]["name"])
    assert not doc.errors, doc.errors
    now = places(doc, {"side": ("face", side), "edge": ("edge", edge)})
    for k in ("side", "edge"):
        assert now[k][0] == where[k][0] and np.allclose(now[k][1], where[k][1])
    assert np.allclose(now["side"][1], [40, 10, 5]) and np.allclose(now["edge"][1], [0, 0, 5])


def test_sketch_curve_added():
    """Section 9, "Sketch curve added": curves are added to the base sketch of a pad that carries a
    sketch-on-face and a further feature (testChangeSketch). The added curve makes `sketch_regions` return its
    regions in a different order; the features that hold a fillet edge and a side face still find them."""
    doc, sid, block = _curve_doc()
    pad_sid, pad = pad_on(doc, ref_to(doc, "face", normal_is([1, 0, 0])))   # on the x = 40 side face
    fil = add(doc, "fillet", edges=[ref_to(doc, "edge", vertical_edge(0, 0))], radius=2.0)
    refs = final_refs(doc, {
        "corner_round": ("face", round_face(2.0)),
        "back_side": ("face", both(normal_is([0, 1, 0]), lambda i: abs(i["c"][1] - 20) < 1e-6)),
        "pad_end": ("face", both(normal_is([1, 0, 0]), lambda i: abs(i["c"][0] - 44) < 1e-6)),
    })
    before = state_fingerprint(doc)
    names_before = named_part_state(doc, only_part(doc).id)
    where = places(doc, refs)
    order_before = list(doc.state.sketches[sid].region_names)
    assert not doc.errors and not doc.state.recovered

    add_overlapping_circle(doc, sid)

    order_after = list(doc.state.sketches[sid].region_names)
    assert block[0]["name"] in order_before and block[0]["name"] in order_after
    assert order_after.index(block[0]["name"]) != order_before.index(block[0]["name"])     # the order did change
    assert not doc.errors, doc.errors
    assert not doc.state.recovered
    for fid in (fil["id"], pad_sid, pad["id"]):
        assert status(doc, fid) == "ok"
    now = places(doc, refs)
    for k in refs:
        assert now[k][0] == where[k][0] and np.allclose(now[k][1], where[k][1])
    assert named_part_state(doc, only_part(doc).id) == names_before
    record("sketch_curve_added", before, doc, regions_before=len(order_before), regions_after=len(order_after))


def test_face_split_by_inserted_hole():
    """Section 9, "Face split by an inserted hole": a hole is cut through a sketch attached to one face, below
    features that already exist, and splits that face (testPartDesignElementMapHole). The pieces get their own
    names; references on faces the hole leaves alone keep theirs."""
    doc, sid, ex, dims = dim_block(60.0, 30.0, 10.0)
    top_plane = face_plane(doc, ref_to(doc, "face", top_at(10.0)))         # the plane the hole will be sketched on
    pad_sid, pad = pad_on(doc, ref_to(doc, "face", normal_is([-1, 0, 0])))  # downstream: on the x = 0 face
    fil = add(doc, "fillet", edges=[ref_to(doc, "edge", vertical_edge(60, 30))], radius=2.0)
    refs = final_refs(doc, {
        "left": ("face", both(normal_is([-1, 0, 0]), lambda i: abs(i["c"][0]) < 1e-6)),
        "pad_end": ("face", both(normal_is([-1, 0, 0]), lambda i: abs(i["c"][0] + 4) < 1e-6)),
    })
    top = ref_to(doc, "face", top_at(10.0))
    corner_round = ref_to(doc, "face", round_face(2.0))                    # named from the top face it rounds into
    before = state_fingerprint(doc)
    where = places(doc, refs)
    assert len([f for f in K.faces(only_part(doc).shape) if top_at(10.0)(K.face_info(f))]) == 1
    assert not doc.errors and not doc.state.recovered

    hole_sid = insert_sketch_before(doc, pad_sid, lambda s: s.add_point_entity(s.add_point(0, 0)), top_plane)
    hole = insert_before(doc, pad_sid, "hole", sketch=hole_sid, standard="custom", diameter=31.0,
                         end="blind", depth=4.0, style="simple")                # a groove right across the top

    assert not doc.errors, doc.errors
    assert not doc.state.recovered
    p = only_part(doc)
    nm = doc.state.named(p.id)
    pieces = [f for f in K.faces(p.shape) if top_at(10.0)(K.face_info(f))]
    assert len(pieces) == 2                                                # the bore is wider than the block's top
    names = [nm.name_of(f) for f in pieces]
    assert all(names) and len(set(names)) == 2
    bore = [f for f in K.faces(p.shape) if round_face(15.5)(K.face_info(f))]
    assert len(bore) == 2 and all(nm.name_of(f) for f in bore)             # the bore wall, in two arcs
    assert len(K.faces(p.shape)) == len(K.faces(only_part(doc).shape)) and len(doc.state.parts) == 1
    for fid in (hole["id"], fil["id"], pad_sid, pad["id"]):
        assert status(doc, fid) == "ok"
    now = places(doc, refs)                                                # the untouched faces are where they were
    for k in refs:
        assert now[k][0] == where[k][0] and np.allclose(now[k][1], where[k][1])
    assert np.isclose(K.bbox(p.shape)[0][0], -4.0)                         # the pad still stands on the x = 0 face
    # the split face: its name is on one piece or on none, and a reference to it never lands elsewhere
    if top["name"] in names:
        _pid, sub = resolve(doc, top, "face")
        assert any(sub.IsSame(f) for f in pieces)
        outcome = "name kept by one piece"
    else:
        with pytest.raises(K.KernelError) as err:
            resolve(doc, top, "face")
        assert top["name"] in str(err.value)
        outcome = "reference lost"
    # the corner round carries the top face's name inside its own (the fillet face is built from the faces it
    # trims), so the split renames it: the reference is found under its name or is an error, never another face
    rounds = [f for f in K.faces(p.shape) if round_face(2.0)(K.face_info(f))]
    assert len(rounds) == 1
    if nm.name_of(rounds[0]) == corner_round["name"]:
        assert resolve(doc, corner_round, "face")[1].IsSame(rounds[0])
        round_outcome = "name kept"
    else:
        with pytest.raises(K.KernelError):
            resolve(doc, corner_round, "face")
        round_outcome = "reference lost"
    record("face_split_by_inserted_hole", before, doc, top_pieces=len(pieces), bore_faces=len(bore),
           top_outcome=outcome, round_outcome=round_outcome)
    assert outcome in ("name kept by one piece", "reference lost")


def test_faces_merged_by_an_edit():
    """Section 9, "Faces merged by an edit": an edit makes two faces coplanar and the refine pass fuses them
    into one. There is no FreeCAD test; written from section 5. A reference to a face that merges ends in
    `error` or lands on the merged face, never on any other face."""
    doc, sid, ex, dims = dim_block(40.0, 20.0, 10.0)
    wing_sid = sketch_feature(doc, lambda s: rect_lines(s, 40, 0, 20, 20), TOP)
    wing = add(doc, "extrude", regions=region_refs(doc, wing_sid), depth=6.0, op="add")
    wing_top = ref_to(doc, "face", top_at(6.0))
    on_wing_sid, on_wing = pad_on(doc, wing_top, half=2.0, depth=2.0)      # a sketch-on-face of the wing's top
    refs = final_refs(doc, {"left": ("face", both(normal_is([-1, 0, 0]), lambda i: abs(i["c"][0]) < 1e-6))})
    before = state_fingerprint(doc)
    where = places(doc, refs)
    assert not doc.errors and not doc.state.recovered
    assert len([f for f in K.faces(only_part(doc).shape) if top_at(10.0)(K.face_info(f))]) == 1   # the block's top

    set_param(doc, wing["id"], depth=10.0)                                 # the wing's top is now level with the block's

    p = only_part(doc)
    nm = doc.state.named(p.id)
    tops = [f for f in K.faces(p.shape) if top_at(10.0)(K.face_info(f))]
    assert len(tops) == 1 and K.face_info(tops[0])["area"] > 1100          # the two tops became one face
    assert nm.name_of(tops[0]) != wing_top["name"]                         # under a name that is neither source's alone
    assert np.allclose(places(doc, refs)["left"][1], where["left"][1])
    if status(doc, on_wing_sid) == "error":
        outcome = "error"
        assert wing_top["name"] in doc.errors[on_wing_sid]
        assert not doc.state.recovered
        with pytest.raises(K.KernelError):
            resolve(doc, wing_top, "face")
    else:
        outcome = "merged face"
        assert status(doc, on_wing_sid) == "ok"
        _pid, sub = resolve(doc, wing_top, "face")
        assert sub.IsSame(tops[0])                                         # and on no other face
    record("faces_merged_by_an_edit", before, doc, outcome=outcome)


def test_inserted_part_creating_feature():
    """Section 9, "Inserted part-creating feature": a feature is inserted below existing features, renumbering
    everything downstream (testBaseFeatureAttachmentSupport, testPadsOnBaseObject). Part ids are
    "<feature id>/<n>", so the old part keeps its id and its names."""
    doc, sid, ex, dims = dim_block()
    pid = only_part(doc).id
    fil = add(doc, "fillet", edges=[ref_to(doc, "edge", vertical_edge(40, 20))], radius=2.0)
    pad_sid, pad = pad_on(doc, ref_to(doc, "face", top_at(10.0)))
    refs = final_refs(doc, {
        "pad_top": ("face", top_at(14.0)),
        "corner_round": ("face", round_face(2.0)),
        "origin_edge": ("edge", vertical_edge(0, 0)),
    })
    before = state_fingerprint(doc)
    where = places(doc, refs)
    names_before = named_part_state(doc, pid)
    shape_before = shape_fingerprint(only_part(doc).shape)
    assert not doc.errors and not doc.state.recovered

    first = doc.features[0]["id"]
    new_sid = insert_sketch_before(doc, first, lambda s: rect_lines(s, 100, 0, 20, 20), TOP)
    new_ex = insert_before(doc, first, "extrude", regions=region_refs(doc, new_sid), depth=7.0, op="new")
    new_pid = f"{new_ex['id']}/1"

    assert not doc.errors, doc.errors
    assert doc.feature_index(new_sid) == 0 and doc.feature_index(new_ex["id"]) == 1 and doc.feature_index(sid) == 2
    assert sorted(doc.state.parts) == sorted([pid, new_pid])               # the old part's id is unchanged
    assert not doc.state.recovered
    for fid in (fil["id"], pad_sid, pad["id"]):
        assert status(doc, fid) == "ok"
    assert named_part_state(doc, pid) == names_before
    assert shape_fingerprint(doc.state.parts[pid].shape) == shape_before
    now = places(doc, refs)
    for k in refs:
        assert now[k][0] == pid and np.allclose(now[k][1], where[k][1])     # on the old part, not the new one
    assert not set(named_part_state(doc, new_pid)) & set(names_before)      # and no name is on both parts
    assert np.allclose(K.bbox(doc.state.parts[new_pid].shape)[1], [120, 20, 7])
    record("inserted_part_creating_feature", before, doc)


def test_consumed_edge():
    """Section 9, "Consumed edge": an edit removes an edge rather than reshaping it (testApplyFillet,
    testPD_TNPSketchPadSketchDelete). The feature that used the edge ends in `error`, naming the edge, and the
    part is left as it was before that feature."""
    doc, sid, ex, dims = dim_block()
    edge = ref_to(doc, "edge", vertical_edge(40, 20))
    fil = add(doc, "fillet", edges=[edge], radius=2.0)
    assert status(doc, fil["id"]) == "ok" and len(K.faces(only_part(doc).shape)) == 7
    before = state_fingerprint(doc)

    cut_sid = insert_sketch_before(doc, fil["id"], lambda s: rect_lines(s, 30, 10, 20, 20), TOP)
    cut = insert_before(doc, fil["id"], "extrude", regions=region_refs(doc, cut_sid), depth=10.0, op="remove")

    assert status(doc, cut["id"]) == "ok"
    assert status(doc, fil["id"]) == "error"
    assert edge["name"] in doc.errors[fil["id"]]
    assert list(doc.errors) == [fil["id"]]                                  # nothing else is blamed
    assert fil["id"] not in doc.state.recovered
    with pytest.raises(K.KernelError):                                      # the name finds nothing near the corner
        resolve(doc, edge, "edge")
    assert len(K.faces(only_part(doc).shape)) == 8                          # the block with its corner cut, no round
    record("consumed_edge", before, doc, errors=len(doc.errors))


def _twin_doc(chamfer):
    """A 60 x 30 x 10 block with two equal 4 mm holes cut by circles of one sketch, at x = 15 and x = 45.
    Returns (doc, circle sketch id, reference to the top rim of the left hole, same for the right hole,
    chamfer feature or None); with `chamfer` the left rim is chamfered."""
    doc, _sid, _ex, _d = dim_block(60.0, 30.0, 10.0)
    sid = sketch_feature(doc, lambda s: (s.add_circle(s.add_point(15, 15), 4), s.add_circle(s.add_point(45, 15), 4)), TOP)
    add(doc, "extrude", regions=region_refs(doc, sid), depth=10.0, op="remove")
    rim = both(lambda i: i["type"] == int(K.GeomAbs_Circle), lambda i: abs(i["c"][2] - 10) < 1e-6)
    left = ref_to(doc, "edge", both(rim, lambda i: i["c"][0] < 30))
    right = ref_to(doc, "edge", both(rim, lambda i: i["c"][0] > 30))
    cham = add(doc, "chamfer", edges=[left], distance=1.0) if chamfer else None
    return doc, sid, left, right, cham


def _swap_circles(s):
    """The circle that was on the left goes to the right, and the other way round."""
    (c1, c2) = [e for e in s.entities.values() if e["type"] == "circle"]
    s.points[c1["c"]], s.points[c2["c"]] = s.points[c2["c"]], s.points[c1["c"]]


def test_twin_edges():
    """Section 9, "Twin edges": two edges a geometric signature cannot tell apart, so only the name tells them
    apart (no FreeCAD test; ElementMap.cpp:735 is the duplicate-name machinery). An edit swaps the two circles;
    the signature matcher alone then picks the other edge."""
    doc, sid, left, right, _ = _twin_doc(chamfer=False)
    assert left["name"] != right["name"]
    twins = [K.edge_info(e) for e in K.edges(only_part(doc).shape)
             if K.edge_info(e)["type"] == int(K.GeomAbs_Circle) and abs(K.edge_info(e)["c"][2] - 10) < 1e-6]
    assert len(twins) == 2 and np.isclose(twins[0]["len"], twins[1]["len"]) and twins[0]["r"] == twins[1]["r"]
    assert left["type"] == right["type"] and np.isclose(left["len"], right["len"]) and left["r"] == right["r"]
    assert np.allclose(left["axis"], right["axis"])                         # same length, radius and direction
    before = state_fingerprint(doc)

    edit_sketch(doc, sid, _swap_circles)

    assert not doc.errors, doc.errors
    named_pid, named_edge = resolve(doc, left, "edge")
    assert doc.state.named(named_pid).name_of(named_edge) == left["name"]
    assert np.allclose(K.edge_info(named_edge)["c"], [45, 15, 10])          # the name followed its circle
    guess_pid, guess = F.resolve(doc.state, {k: v for k, v in left.items() if k != "name"}, "edge", fid="probe")
    assert np.allclose(K.edge_info(guess)["c"], [15, 15, 10])               # the signature matcher alone stays put
    assert not guess.IsSame(named_edge)
    assert doc.state.named(guess_pid).name_of(guess) == right["name"]       # and picks the look-alike
    assert doc.state.recovered == {"probe"}                                 # the nameless lookup is the recovered one
    record("twin_edges", before, doc)


def test_twin_edges_chamfer_follows_the_name():
    """Section 9, "Twin edges", through a feature: a chamfer on one of the two look-alike rims stays on that
    rim when the circles swap places. Not recovered, not on the other hole."""
    doc, sid, left, right, cham = _twin_doc(chamfer=True)
    cone = lambda i: i["type"] == int(GeomAbs_Cone)
    assert np.isclose(K.face_info(pick(doc, "face", cone))["c"][0], 15.0)  # the chamfer is on the left hole
    before = state_fingerprint(doc)

    edit_sketch(doc, sid, _swap_circles)

    assert not doc.errors, doc.errors
    assert status(doc, cham["id"]) == "ok" and not doc.state.recovered
    assert np.isclose(K.face_info(pick(doc, "face", cone))["c"][0], 45.0)   # the chamfer moved with its circle
    record("twin_edges_chamfer", before, doc)


def _mirror_doc():
    """A 40 x 20 x 10 block at x = 20..60 (dimensioned, first corner fixed) and its mirror across x = 0.
    Returns (doc, sketch id, width dimension id, seed part id, copy part id)."""
    doc, sid, ex, dims = dim_block(40.0, 20.0, 10.0, x0=20.0)
    seed_pid = only_part(doc).id
    mir = add(doc, "mirror", parts=[seed_pid], plane=RIGHT, op="new")
    copy_pid = f"{mir['id']}/1"
    assert sorted(doc.state.parts) == sorted([seed_pid, copy_pid])
    return doc, sid, dims["w"], seed_pid, copy_pid


def test_mirrored_copy():
    """Section 9, "Mirrored copy": a mirror instance's elements are told apart from their originals (no FreeCAD
    test; written from section 7). A reference on the copy resolves to the corresponding edge of the copy, not of
    the seed, when the seed's dimensions change."""
    doc, sid, w, seed_pid, copy_pid = _mirror_doc()
    edge = ref_to(doc, "edge", vertical_edge(-60, 20), copy_pid)            # the copy of the seed's (60, 20) edge
    seed_edge = ref_to(doc, "edge", vertical_edge(60, 20), seed_pid)
    assert edge["name"] != seed_edge["name"] and ";MIR" in edge["name"] and ";MIR" not in seed_edge["name"]
    assert edge["part"] == copy_pid and seed_edge["part"] == seed_pid
    before = state_fingerprint(doc)

    set_dim(doc, sid, w, 50.0)                                              # the seed grows to x = 70

    assert not doc.errors, doc.errors
    pid, info = lands_on_its_name(doc, edge, "edge")
    assert pid == copy_pid                                                  # on the copy, not on the seed
    assert np.allclose(info["c"][:2], [-70, 20])                            # the corresponding edge, at its new end
    pid, info = lands_on_its_name(doc, seed_edge, "edge")
    assert pid == seed_pid and np.allclose(info["c"][:2], [70, 20])
    record("mirrored_copy", before, doc)


def test_mirrored_copy_fillet_stays_on_the_copy():
    """Section 9, "Mirrored copy", through a feature: a fillet on an edge of the copy stays on the copy when the
    seed changes, and the seed is not rounded."""
    doc, sid, w, seed_pid, copy_pid = _mirror_doc()
    fil = add(doc, "fillet", edges=[ref_to(doc, "edge", vertical_edge(-60, 20), copy_pid)], radius=2.0)
    rounds = lambda pid: [f for f in K.faces(doc.state.parts[pid].shape) if round_face(2.0)(K.face_info(f))]
    assert len(rounds(copy_pid)) == 1 and not rounds(seed_pid)
    refs = final_refs(doc, {"round": ("face", round_face(2.0))}, copy_pid)
    before = state_fingerprint(doc)
    where = places(doc, refs)
    assert where["round"][1][0] < -55

    set_dim(doc, sid, w, 50.0)

    assert not doc.errors, doc.errors
    assert status(doc, fil["id"]) == "ok" and not doc.state.recovered
    now = places(doc, refs)
    assert now["round"][0] == copy_pid and now["round"][1][0] < -65         # the copy's round, at the copy's new end
    assert len(rounds(copy_pid)) == 1 and not rounds(seed_pid)              # never the seed's corner
    record("mirrored_copy_fillet", before, doc)


def _plate(fillet, hole):
    """A 60 x 40 x 10 plate with its four vertical edges filleted by name and a 4 mm through hole at `hole`,
    plus a 20 x 10 x 8 block as a second part. Returns (doc, {name: feature id}, plate part id, block part id)."""
    doc = Document()
    s = Sketch()
    p = [ORIGIN, s.add_point(60, 0), s.add_point(60, 40), s.add_point(0, 40)]
    ln = [s.add_line(a, b) for a, b in zip(p, p[1:] + p[:1])]
    for line, t in zip(ln, ["horizontal", "vertical", "horizontal", "vertical"]):
        s.add_constraint(t, line)
    s.add_constraint("distance", ln[0], value=60.0)
    s.add_constraint("distance", ln[1], value=40.0)
    assert s.solve() and s.status["dof"] == 0
    f = doc.make_feature("sketch")
    f["params"]["sketch"] = s.to_dict()
    doc.add_feature(f)
    ids = {"sketch": f["id"]}
    add(doc, "extrude", regions=region_refs(doc, f["id"]), depth=10.0, op="new")
    plate = only_part(doc).id
    edges = [ref_to(doc, "edge", vertical_edge(x, y)) for x in (0, 60) for y in (0, 40)]
    ids["fillet"] = add(doc, "fillet", edges=edges, radius=fillet)["id"]
    ids["hole_sketch"] = sketch_feature(doc, lambda sk: sk.add_point_entity(sk.add_point(*hole)))
    ids["hole"] = add(doc, "hole", sketch=ids["hole_sketch"], standard="custom", diameter=4.0,
                      end="through", style="simple")["id"]
    blk = sketch_feature(doc, lambda sk: rect_lines(sk, 100, 0, 20, 10), TOP)
    block = add(doc, "extrude", regions=region_refs(doc, blk), depth=8.0, op="new")
    return doc, ids, plate, f"{block['id']}/1"


def _move_hole(doc, hole_sketch, xy):
    def move(s):
        for pid in [k for k in s.points if k != ORIGIN]:
            s.points[pid] = [float(xy[0]), float(xy[1])]
    edit_sketch(doc, hole_sketch, move)


def test_hole_through_a_fillet():
    """Section 9, "Hole through a fillet": a hole is moved so it cuts into a filleted corner
    (testPartDesignTNPFillet, testPartDesignTNPChamfer). A fillet reference on an untouched corner keeps its
    name and its place."""
    doc, ids, plate, _block = _plate(5.0, (8.0, 8.0))
    corner = lambda x, y: both(round_face(5.0), lambda i: (i["c"][0] > 30) == (x > 30) and (i["c"][1] > 20) == (y > 20))
    refs = final_refs(doc, {"round_60_0": ("face", corner(60, 0)), "round_60_40": ("face", corner(60, 40)),
                            "round_0_40": ("face", corner(0, 40))}, plate)
    cut_round = ref_to(doc, "face", corner(0, 0), plate)
    before = state_fingerprint(doc)
    where = places(doc, refs)
    bore = lambda: K.face_info(pick(doc, "face", round_face(2.0), plate))["c"]
    bore_before = bore()
    assert not doc.errors and not doc.state.recovered

    _move_hole(doc, ids["hole_sketch"], (2.5, 2.5))                         # the bore now eats into the (0, 0) round

    assert not doc.errors, doc.errors
    assert not doc.state.recovered
    for fid in (ids["fillet"], ids["hole"]):
        assert status(doc, fid) == "ok"
    assert not np.allclose(bore(), bore_before)                             # the hole did move
    now = places(doc, refs)
    for k in refs:                                                          # untouched corners: same name, same place
        assert now[k][0] == plate and np.allclose(now[k][1], where[k][1])
    try:                                                                    # the cut corner: its name is kept or lost
        lands_on_its_name(doc, cut_round, "face")
    except K.KernelError as err:
        assert cut_round["name"] in str(err)
    record("hole_through_a_fillet", before, doc)


def _plate_assembly(doc, plate, block):
    """An assembly of the plate (fixed) and the block, resolved through the LinkResolver as the app does."""
    r = L.LinkResolver(find_open_studio=lambda uid, path: doc if uid == doc.uid else None)
    a = A.AssemblyDoc()
    a.resolver = r.resolve_source
    ip = a.insert({"kind": "studio", "uid": doc.uid, "part": plate})
    a.set_fixed(ip, True)
    ib = a.insert({"kind": "studio", "uid": doc.uid, "part": block})
    return a, ip, ib


def _flat_face(shape, up):
    """Index of the one face with normal +z (up = 1, at the plate's top) or -z (up = -1)."""
    hits = [i for i, f in enumerate(K.faces(shape))
            if (n := K.face_info(f).get("n")) is not None and n[2] * up > 0.9
            and (up < 0 or abs(K.face_info(f)["c"][2] - 10.0) < 1e-6)]
    assert len(hits) == 1, hits
    return hits[0]


def test_mate_connector_keeps_its_frame_through_the_hole_into_fillet_edit():
    """Section 9, "Hole through a fillet", with section 8.1: a mate connector on the plate's top face keeps its
    X axis when the hole is moved into a corner round, which changes the longest top edge. The mate is not
    `error`."""
    doc, ids, plate, block = _plate(5.0, (8.0, 8.0))
    a, ip, ib = _plate_assembly(doc, plate, block)
    m = a.make_mate("fastened")
    m["a"] = a.connector(ip, "face", _flat_face(a.parts[ip].shape, +1))
    m["b"] = a.connector(ib, "face", _flat_face(a.parts[ib].shape, -1))
    a.add_mate(m)
    a.solve()
    assert not a.errors, a.errors
    top_name = m["a"]["ref"].get("name")
    assert top_name, "the connector stores the name of its face"
    x_before = A.frame_from_json(m["a"]["frame"])["x"]
    block_turn = np.array(a.instance(ib)["T"], float)[:3, :3]
    shape_before = a.parts[ip].shape

    _move_hole(doc, ids["hole_sketch"], (2.5, 2.5))
    a.refresh()

    assert not doc.errors, doc.errors
    assert not a.parts[ip].shape.IsSame(shape_before)
    assert m["id"] not in a.errors and not a.errors, a.errors              # the mate is not `error`
    assert a.recovered_connectors() == []                                   # found by name, not by signature
    assert m["a"]["ref"]["name"] == top_name
    assert float(A.frame_from_json(m["a"]["frame"])["x"] @ x_before) >= 0.999
    assert np.allclose(np.array(a.instance(ib)["T"], float)[:3, :3], block_turn, atol=1e-9)    # the block did not turn
    # the edit does change the longest-edge answer, so the stored axis is what kept X
    assert float(A.connector_frame(a.parts[ip].shape, "face", m["a"]["index"], None)["x"] @ x_before) < 0.999


# =========================================================================== legacy references and unopened files

def _strip_names(x):
    """A deep copy of feature params with every reference's name dropped: what a studio saved before naming holds."""
    if isinstance(x, dict):
        return {k: _strip_names(v) for k, v in x.items() if not (k == "name" and isinstance(v, str) and ";" in v)}
    if isinstance(x, list):
        return [_strip_names(v) for v in x]
    return x


def _refs_in(x):
    """Every named reference (a dict with a `name` holding a ';') inside feature params."""
    if isinstance(x, dict):
        if isinstance(x.get("name"), str) and ";" in x["name"]:
            yield x
        for v in x.values():
            yield from _refs_in(v)
    elif isinstance(x, list):
        for v in x:
            yield from _refs_in(v)


def test_legacy_references_recover_with_a_marker(tmp_path):
    """Section 6 and ADR 0003 (the policy's other half; section 9 has no row for it): a studio saved before
    naming holds references without names. They are matched by signature, the features rebuild and are marked
    `recovered`; the same studio with its names is `ok`."""
    doc, sid, ex, dims = dim_block()
    fil = add(doc, "fillet", edges=[ref_to(doc, "edge", vertical_edge(40, 20))], radius=2.0)
    pad_sid, pad = pad_on(doc, ref_to(doc, "face", top_at(10.0)))
    before = state_fingerprint(doc)
    want = shape_fingerprint(only_part(doc).shape)
    names = {r["name"] for f in doc.features for r in _refs_in(f["params"])}
    assert names
    path = str(tmp_path / "studio.fshape")
    doc.save(path)
    with open(path) as fh:
        d = json.load(fh)
    d["features"] = [dict(f, params=_strip_names(f["params"])) for f in d["features"]]
    assert not any(n in json.dumps(d) for n in names)
    with open(path, "w") as fh:
        json.dump(d, fh)

    legacy = Document.load(path)

    assert not legacy.errors, legacy.errors
    assert status(legacy, fil["id"]) == "recovered"
    assert status(legacy, ex) == "recovered"                                # its region reference was nameless too
    assert status(legacy, pad_sid) == "recovered"                           # and the sketch's face reference
    assert status(legacy, sid) == "ok"
    assert shape_fingerprint(only_part(legacy).shape) == want               # the matcher found the same elements
    assert status(doc, fil["id"]) == "ok" and not doc.state.recovered
    set_dim(legacy, sid, dims["w"], 60.0)                                   # and it keeps recovering through an edit
    assert not legacy.errors, legacy.errors
    assert status(legacy, fil["id"]) == "recovered"
    assert np.allclose(K.bbox(only_part(legacy).shape)[1][:2], [60, 20])
    record("legacy_recovery", before, legacy, recovered=sorted(legacy.state.recovered))


def _copy_old_set(tmp_path):
    studio = str(tmp_path / "hinge_parts.fshape")
    shutil.copy(os.path.join(EXAMPLES_DIR, "hinge_parts.fshape"), studio)
    shutil.copy(os.path.join(EXAMPLES_DIR, "hinge.fasm"), str(tmp_path / "hinge.fasm"))
    shutil.copy(os.path.join(DATA, "arm_v1.fdwg"), str(tmp_path / "arm_v1.fdwg"))
    return studio


def _load_dependents(tmp_path):
    """The old assembly and the old drawing, loaded (not saved) through a LinkResolver."""
    r = L.LinkResolver()
    asm = A.AssemblyDoc.load(str(tmp_path / "hinge.fasm"), resolver=r.resolve_source)
    assert not asm.errors, asm.errors
    g = DrawingDoc.load(str(tmp_path / "arm_v1.fdwg"), resolver=r.resolve_drawing_source)
    assert g.error is None, g.error
    return asm, g


def test_unopened_dependents_bind_after_the_studio_alone_is_resaved(tmp_path):
    """I1.4, seen from outside the studio (section 9's inserted-feature row renumbers parts): an old studio is
    re-saved on its own while its old assembly and old drawing stay unopened and unsaved. Loaded afterwards,
    each binds the same parts."""
    studio = _copy_old_set(tmp_path)
    fasm, fdwg = tmp_path / "hinge.fasm", tmp_path / "arm_v1.fdwg"
    on_disk = {p.name: p.read_bytes() for p in (fasm, fdwg)}
    asm, g = _load_dependents(tmp_path)
    inst_before = {i["id"]: (i["source"]["part"], shape_fingerprint(asm.parts[i["id"]].shape)) for i in asm.instances}
    draw_before = shape_fingerprint(g.shape)
    assert inst_before and all(re.fullmatch(r"F\d+:\d+", pid) for pid, _ in inst_before.values())   # old-syntax ids

    doc = Document.load(studio)
    assert not doc.errors, doc.errors
    assert doc.part_aliases == {"F2:1": "F2/1", "F4:2": "F4/1", "F6:3": "F6/1"}
    doc.save()
    with open(studio) as fh:
        assert json.load(fh)["version"] == 3                                # only the studio was rewritten
    for p in (fasm, fdwg):
        assert p.read_bytes() == on_disk[p.name]                            # the dependents are byte for byte as before

    asm2, g2 = _load_dependents(tmp_path)
    inst_after = {i["id"]: (i["source"]["part"], shape_fingerprint(asm2.parts[i["id"]].shape)) for i in asm2.instances}
    assert inst_after == inst_before                                        # each instance binds the same part
    assert sorted(asm2.parts) == sorted(asm.parts)
    assert shape_fingerprint(g2.shape) == draw_before
    assert shape_fingerprint(g2.shape) == shape_fingerprint(doc.state.parts["F4/1"].shape)
    for old_pid, fp in inst_after.values():
        assert fp == shape_fingerprint(doc.state.parts[doc.part_aliases[old_pid]].shape)
    assert_golden(f"{GOLDEN}/unopened_dependents",
                  {"instances": {i: fp for i, (_p, fp) in inst_after.items()}, "drawing": shape_fingerprint(g2.shape)},
                  FREEFORM)


SCENARIOS = [
    test_sketch_dimension_change,
    test_sketch_curve_added_references_resolve_to_the_same_elements,
    test_sketch_curve_added,
    test_face_split_by_inserted_hole,
    test_faces_merged_by_an_edit,
    test_inserted_part_creating_feature,
    test_consumed_edge,
    test_twin_edges,
    test_twin_edges_chamfer_follows_the_name,
    test_mirrored_copy,
    test_mirrored_copy_fillet_stays_on_the_copy,
    test_hole_through_a_fillet,
    test_mate_connector_keeps_its_frame_through_the_hole_into_fillet_edit,
    test_legacy_references_recover_with_a_marker,
    test_unopened_dependents_bind_after_the_studio_alone_is_resaved,
]
