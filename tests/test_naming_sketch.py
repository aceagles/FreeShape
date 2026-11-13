"""Sketch root naming (I1.5): a sketch's regions, their edges and vertices are named from the sketch
entity ids (docs/reference/element-naming.md § 4), and a region reference resolves by that name.

Policy under test (ADR 0003): a named reference resolves to the region of that name or is an error
naming the lost region; only a reference with no name uses the geometric `_region_score` matcher,
and the consuming feature is then flagged recovered.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import ORIGIN, Sketch  # noqa: E402

from conftest import TOP  # noqa: E402


# --------------------------------------------------------------------------- builders

def rect_with_circle(s: Sketch, w=40.0, h=20.0, cx=20.0, cy=10.0, r=5.0, constrained=False):
    """A w x h rectangle with its corner on the origin and a circle inside it. Returns
    ({"lines": [line ids], "circle": circle id, "width": the width constraint or None})."""
    pts = [ORIGIN, s.add_point(w, 0), s.add_point(w, h), s.add_point(0, h)]
    lines = [s.add_line(a, b) for a, b in zip(pts, pts[1:] + pts[:1])]
    centre = s.add_point(cx, cy)
    circle = s.add_circle(centre, r)
    width = None
    if constrained:
        for lid, t in zip(lines, ("horizontal", "vertical", "horizontal", "vertical")):
            s.add_constraint(t, lid)
        width = s.add_constraint("distance", lines[0], value=w)
        s.add_constraint("distance", lines[1], value=h)
        s.add_constraint("fix", centre, value=[cx, cy])
        s.add_constraint("radius", circle, value=r)
    return {"lines": lines, "circle": circle, "width": width}


def sketch_state(s: Sketch, fid="F1"):
    """A ModelState holding sketch `s` run as feature `fid` on the Top plane."""
    state = F.ModelState()
    F.run_sketch(state, {"id": fid, "type": "sketch", "params": {"plane": TOP, "sketch": s.to_dict()}})
    return state, state.sketches[fid]


def sketch_doc(build):
    """A Document with one sketch feature built by build(sketch); returns (doc, sketch fid, ids)."""
    doc = Document()
    s = Sketch()
    ids = build(s)
    s.solve()
    f = doc.make_feature("sketch")
    f["params"].update(plane=TOP, sketch=s.to_dict())
    doc.add_feature(f)
    assert not doc.errors.get(f["id"]), doc.errors
    return doc, f["id"], ids


def edit_sketch(doc, sid, change):
    """Apply change(sketch) to sketch feature `sid`, re-solve and rebuild."""
    f = dict(doc.feature(sid))
    s = Sketch.from_dict(f["params"]["sketch"])
    change(s)
    s.solve()
    f["params"] = dict(f["params"], sketch=s.to_dict())
    doc.replace_feature(f)


def region_by_area(sr, pred):
    hits = [i for i, r in enumerate(sr.regions) if pred(K.face_info(r)["area"])]
    assert len(hits) == 1, [K.face_info(r)["area"] for r in sr.regions]
    return hits[0]


def edge_names(sr):
    """Every name of an edge that bounds a region of the sketch."""
    out = set()
    for r in sr.regions:
        for e in K.edges(r):
            out.add(sr.named.name_of(e))
    return out


def root(name):
    """The id the name was first built from: the text in front of its first fragment."""
    return name.split(";", 1)[0]


# --------------------------------------------------------------------------- tests

def test_entity_ids_name_every_region_face_and_edge():
    s = Sketch()
    ids = rect_with_circle(s)
    _, sr = sketch_state(s)
    entity_ids = set(ids["lines"]) | {ids["circle"]}
    assert len(sr.regions) == 2
    assert len(sr.region_names) == len(sr.regions)
    names = []
    for r, rname in zip(sr.regions, sr.region_names):
        assert rname and rname == sr.named.name_of(r)
        assert root(rname) in entity_ids and ";SKT;:HF1" in rname
        names.append(rname)
        for e in K.edges(r):
            ename = sr.named.name_of(e)
            assert ename, "a region edge has no name"
            assert root(ename) in entity_ids
            names.append(ename)
    # unique within the sketch: one element, one name, and no name on two elements
    assert len(set(sr.named.names.values())) == len(sr.named.names)
    assert not sr.named.unnamed()
    # the ring is named from its outer wire only (the four lines), the disc from the circle
    ring = region_by_area(sr, lambda a: a > 500)
    assert {root(n) for n in [sr.region_names[ring]]} <= set(ids["lines"])
    for lid in ids["lines"]:
        assert f"{lid};SKT" in sr.region_names[ring]
    assert ids["circle"] not in sr.region_names[ring]
    assert root(sr.region_names[1 - ring]) == ids["circle"]


def test_dimension_change_keeps_region_and_edge_names():
    doc, sid, ids = sketch_doc(lambda s: rect_with_circle(s, constrained=True))
    sr = doc.state.sketches[sid]
    before_regions, before_edges = set(sr.region_names), edge_names(sr)
    ring = region_by_area(sr, lambda a: a > 500)
    ref = F.region_ref(doc.state, sid, sr.regions[ring])
    assert ref["name"] == sr.region_names[ring]
    ex = doc.make_feature("extrude")
    ex["params"].update(regions=[ref], depth=5.0, op="new")
    doc.add_feature(ex)
    assert not doc.errors.get(ex["id"]), doc.errors

    def widen(s):
        next(c for c in s.constraints if c["id"] == ids["width"]["id"])["value"] = 55.0
    edit_sketch(doc, sid, widen)
    assert not doc.errors, doc.errors
    sr2 = doc.state.sketches[sid]
    lo, hi = sr2.bb
    assert abs((hi[0] - lo[0]) - 55.0) < 1e-6, "the width dimension did not take"
    assert set(sr2.region_names) == before_regions
    assert edge_names(sr2) == before_edges
    face = F.resolve_regions(doc.state, [ref])[0]
    i = sr2.region_index(face)
    assert sr2.region_names[i] == ref["name"]
    assert abs(K.face_info(face)["area"] - (55 * 20 - 25 * 3.141592653589793)) < 1e-6
    # the same four lines bound it
    outer = {root(sr2.named.name_of(e)) for e in K.edges(face)} - {ids["circle"]}
    assert outer == set(ids["lines"])
    assert ex["id"] not in doc.state.recovered


def test_added_curve_keeps_names_while_region_order_moves():
    s = Sketch()
    rect_with_circle(s)
    _, sr = sketch_state(s)
    ring = region_by_area(sr, lambda a: a > 500)
    ref = F.region_ref(sr, "F1", sr.regions[ring])
    old_named = dict(zip(sr.region_names, (K.face_info(r)["area"] for r in sr.regions)))
    old_edges = edge_names(sr)
    moved = []
    # The Splitter lists regions in the order of the curves it is given, so a circle appended to the
    # entity list never moves the old regions, wherever it sits. Each spot is therefore also tried
    # with the new circle first in the entity order, which does move them.
    spots = ((-30, 10), (70, 10), (20, -30), (20, 50), (-30, -30), (90, 60))
    for (cx, cy), first in [(spot, first) for spot in spots for first in (False, True)]:
        s2 = s.copy()
        cid = s2.add_circle(s2.add_point(cx, cy), 4)
        if first:
            s2.entities = {cid: s2.entities.pop(cid), **s2.entities}
        state, sr2 = sketch_state(s2)
        assert len(sr2.regions) == 3
        for name, area in old_named.items():
            assert name in sr2.region_names, f"region {name} renamed by a circle at {(cx, cy)}"
            j = sr2.region_names.index(name)
            assert abs(K.face_info(sr2.regions[j])["area"] - area) < 1e-6
        assert old_edges <= edge_names(sr2)
        face = F.resolve_regions(state, [ref], "F2")[0]
        assert sr2.region_names[sr2.region_index(face)] == ref["name"]
        assert "F2" not in state.recovered
        moved.append(sr2.region_index(face) != ring)
    assert any(moved), "the new circle never moved the region in the list, so this proves nothing"


def test_degenerate_curve_is_skipped_without_shifting_ids():
    curves = [{"type": "line", "id": "l1", "p1": [0, 0], "p2": [10, 0]},
              {"type": "line", "id": "l2", "p1": [10, 0], "p2": [10, 0]},       # zero length
              {"type": "circle", "id": "c3", "c": [5, 5], "r": 0.0},           # zero radius
              {"type": "line", "id": "l4", "p1": [10, 0], "p2": [10, 10]},
              {"type": "line", "id": "l5", "p1": [10, 10], "p2": [0, 0]}]
    plane = K.std_plane("Top")
    pairs = K.make_sketch_edges(plane, curves, with_ids=True)
    assert [i for i, _ in pairs] == ["l1", "l4", "l5"]
    by_id = {c["id"]: c for c in curves}
    for eid, edge in pairs:
        ends = sorted(tuple(round(float(x), 9) for x in plane.to_local(p)) for p in K.vertex_points(edge))
        want = sorted(tuple(float(x) for x in by_id[eid][k]) for k in ("p1", "p2"))
        assert ends == want, eid
    assert len(K.make_sketch_edges(plane, curves)) == 3          # the plain form is unchanged

    # and through a sketch: the zero-length line names nothing, every region edge names its entity
    s = Sketch()
    ids = rect_with_circle(s)
    a = s.add_point(30, 5)
    b = s.add_point(30, 5)
    dead = s.add_line(a, b)
    _, sr = sketch_state(s)
    assert len(sr.regions) == 2
    for r in sr.regions:
        for e in K.edges(r):
            eid = root(sr.named.name_of(e))
            assert eid != dead
            ent = s.entities[eid]
            if ent["type"] == "line":
                want = sorted(tuple(s.points[ent[k]]) for k in ("p1", "p2"))
                got = sorted(tuple(round(float(x), 9) for x in sr.plane.to_local(p)) for p in K.vertex_points(e))
                assert got == want, eid
            else:
                assert eid == ids["circle"]


def test_deleting_a_bounding_entity_is_a_named_miss():
    doc, sid, ids = sketch_doc(lambda s: rect_with_circle(s))
    sr = doc.state.sketches[sid]
    disc = region_by_area(sr, lambda a: a < 100)
    ref = F.region_ref(doc.state, sid, sr.regions[disc])
    ex = doc.make_feature("extrude")
    ex["params"].update(regions=[ref], depth=5.0, op="new")
    doc.add_feature(ex)
    assert not doc.errors.get(ex["id"])
    edit_sketch(doc, sid, lambda s: s.delete([ids["circle"]]))
    assert doc.state.sketches[sid].regions, "the rectangle should still be a region"
    err = doc.errors.get(ex["id"])
    assert err and ref["name"] in err, err


def test_legacy_reference_resolves_by_signature_and_is_flagged():
    doc, sid, _ = sketch_doc(lambda s: rect_with_circle(s))
    sr = doc.state.sketches[sid]
    ring = region_by_area(sr, lambda a: a > 500)
    ref = F.region_ref(doc.state, sid, sr.regions[ring])
    ref.pop("name")
    ex = doc.make_feature("extrude")
    ex["params"].update(regions=[ref], depth=5.0, op="new")
    doc.add_feature(ex)
    assert not doc.errors.get(ex["id"]), doc.errors
    assert ex["id"] in doc.state.recovered
    part = next(iter(doc.state.parts.values()))
    assert abs(K.mass_props(part.shape)["volume"] - 5.0 * K.face_info(sr.regions[ring])["area"]) < 1e-6
    assert ex["id"] in doc.state.copy().recovered


def test_named_reference_never_calls_the_signature_matcher(monkeypatch):
    doc, sid, _ = sketch_doc(lambda s: rect_with_circle(s))
    sr = doc.state.sketches[sid]
    ref = F.region_ref(doc.state, sid, sr.regions[0])
    calls = []
    real = F._region_score
    monkeypatch.setattr(F, "_region_score", lambda *a: calls.append(a) or real(*a))
    ex = doc.make_feature("extrude")
    ex["params"].update(regions=[ref], depth=5.0, op="new")
    doc.add_feature(ex)
    assert not doc.errors.get(ex["id"]), doc.errors
    assert calls == []
    assert ex["id"] not in doc.state.recovered
    legacy = {k: v for k, v in ref.items() if k != "name"}
    F.resolve_regions(doc.state, [legacy])
    assert calls, "the spy is not wired to the matcher"


@pytest.mark.parametrize("bad", ["l 1", "l;1"])
def test_unnameable_entity_id_falls_back_to_signatures(bad):
    s = Sketch()
    rect_with_circle(s)
    s.entities[bad] = s.entities.pop(next(iter(s.entities)))
    _, sr = sketch_state(s)
    assert sr.regions and sr.region_names == [None] * len(sr.regions)
    assert "name" not in F.region_ref(sr, "F1", sr.regions[0])
