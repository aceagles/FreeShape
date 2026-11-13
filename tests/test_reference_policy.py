"""Reference format and policy (I1.12): a picked face, edge or vertex stores {name, signature}; a named
reference resolves by name or is an error that names the lost element; a reference with no name falls back
to the geometric matcher and its feature is flagged recovered (docs/reference/element-naming.md section 6,
ADR 0003).
"""
import copy
import json
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.ui.dialogs import pick_to_item  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402

from conftest import (TOP, add, block_doc, normal_is, only_part, rect_lines, region_refs,  # noqa: E402
                      sketch_feature, try_add)


# --------------------------------------------------------------------------- builders

def named_edge_refs(doc, pred, expect=None):
    """References to the edges of the only part picked the way the dialog does: with the part's names."""
    part = only_part(doc)
    named = doc.state.named(part.id)
    assert named is not None
    es = [e for i, e in enumerate(K.edges(part.shape)) if pred(K.edge_info(e))]
    if expect is not None:
        assert len(es) == expect
    return [F.make_ref(part.id, e, "edge", part.shape, named) for e in es]


def corner_edge(x, y):
    """Predicate: the vertical edge standing at (x, y)."""
    return lambda i: (i["type"] == int(K.GeomAbs_Line) and abs(abs(i["d"][2]) - 1) < 1e-6
                      and abs(i["c"][0] - x) < 1e-6 and abs(i["c"][1] - y) < 1e-6)


def insert_corner_cut(doc, before_fid, x0=30.0, y0=10.0, w=20.0, h=20.0):
    """A cut extrude removing the block's (40, 20) corner, inserted just before feature `before_fid`."""
    sid = sketch_feature(doc, lambda s: rect_lines(s, x0, y0, w, h), TOP)
    doc.move_feature(sid, doc.feature_index(before_fid))
    cut = doc.make_feature("extrude")
    cut["params"].update(regions=region_refs(doc, sid), depth=10.0, op="remove")
    doc.add_feature(cut, index=doc.feature_index(before_fid))
    return cut


def strip_names(x):
    """A deep copy of feature params with every reference's name dropped (what a v1 studio holds)."""
    if isinstance(x, dict):
        return {k: strip_names(v) for k, v in x.items()
                if not (k == "name" and isinstance(v, str) and ";" in v)}
    if isinstance(x, list):
        return [strip_names(v) for v in x]
    return x


# --------------------------------------------------------------------------- reference format

def test_make_ref_stores_name_and_signature_for_faces_and_edges():
    doc = block_doc()
    part = only_part(doc)
    named = doc.state.named(part.id)
    top = next(f for f in K.faces(part.shape) if normal_is((0, 0, 1))(K.face_info(f)))
    ref = F.make_ref(part.id, top, "face", part.shape, named)
    assert ref["name"] == named.name_of(top)
    assert ref["part"] == part.id and ref["kind"] == "face" and "area" in ref and "c" in ref and "bb" in ref
    edge = K.edges(part.shape)[0]
    eref = F.make_ref(part.id, edge, "edge", part.shape, named)
    assert eref["name"] == named.name_of(edge) and "len" in eref and eref["kind"] == "edge"
    assert F.resolve(doc.state, ref, "face")[1].IsSame(top)
    assert F.resolve(doc.state, eref, "edge")[1].IsSame(edge)


def test_make_ref_without_a_map_is_signature_only():
    doc = block_doc()
    part = only_part(doc)
    face = K.faces(part.shape)[0]
    assert "name" not in F.make_ref(part.id, face, "face", part.shape)
    assert "name" not in F.make_ref(part.id, face, "face", part.shape, None)


def test_vertex_reference_carries_a_name_and_resolves_by_it():
    doc = block_doc()
    part = only_part(doc)
    named = doc.state.named(part.id)
    pts = K.vertex_points(part.shape)
    ref = F.make_vertex_ref(part.id, pts[3], part.shape, named)
    assert ref["name"] and ref["kind"] == "vertex" and np.allclose(ref["c"], pts[3])
    assert "name" not in F.make_vertex_ref(part.id, pts[3], part.shape)
    assert np.allclose(F.resolve_point(doc.state, ref), pts[3])
    # the position is only a signature: a named vertex is found by name, wherever the stored c says
    moved = dict(ref, c=[999.0, 999.0, 999.0])
    assert np.allclose(F.resolve_point(doc.state, moved), pts[3])


def test_pick_to_item_adds_the_name_when_it_is_given_the_state():
    doc = block_doc()
    part = only_part(doc)

    class VP:
        part_shapes = {part.id: part.shape}

    fi = next(i for i, f in enumerate(K.faces(part.shape)) if normal_is((0, 0, 1))(K.face_info(f)))
    kind, ref = pick_to_item(VP, Pick("face", (part.id, fi)), {"face"}, doc.state)
    assert kind == "face" and ref["name"]
    _, bare = pick_to_item(VP, Pick("face", (part.id, fi)), {"face"})
    assert "name" not in bare
    kind, refs = pick_to_item(VP, Pick("face", (part.id, fi)), {"edge"}, doc.state)
    assert kind == "edges" and all(r["name"] for r in refs)
    kind, ref = pick_to_item(VP, Pick("vertex", (part.id, 0)), {"vertex"}, doc.state)
    assert ref["name"]


# --------------------------------------------------------------------------- policy

def test_named_reference_resolves_without_the_signature_matcher(monkeypatch):
    doc = block_doc()
    part = only_part(doc)
    refs = named_edge_refs(doc, corner_edge(40, 20), 1)
    calls = []
    real = F._score
    monkeypatch.setattr(F, "_score", lambda *a: calls.append(a) or real(*a))
    _, e = F.resolve(doc.state, refs[0], "edge")
    assert K.edge_info(e)["c"][0] == pytest.approx(40.0)
    assert calls == [] and not doc.state.recovered
    F.resolve(doc.state, {k: v for k, v in refs[0].items() if k != "name"}, "edge", fid="FX")
    assert calls and doc.state.recovered == {"FX"}
    assert part.id in doc.state.parts


def test_named_miss_is_an_error_naming_the_element_and_never_a_nearest_match():
    doc = block_doc()
    ref = named_edge_refs(doc, corner_edge(40, 20), 1)[0]
    lost = dict(ref, name=ref["name"] + ";:M9;LOST")        # a name no rebuild produced; signature intact
    with pytest.raises(K.KernelError) as ex:
        F.resolve(doc.state, lost, "edge", fid="FX")
    assert lost["name"] in str(ex.value)
    assert "FX" not in doc.state.recovered
    with pytest.raises(K.KernelError) as ex:
        F.resolve_point(doc.state, {"kind": "vertex", "part": ref["part"], "c": [0, 0, 0], "name": "V;nope"})
    assert "V;nope" in str(ex.value)


def test_consumed_edge_leaves_the_fillet_in_error_with_the_part_unfilleted():
    doc = block_doc()
    ref = named_edge_refs(doc, corner_edge(40, 20), 1)[0]
    fil = add(doc, "fillet", edges=[ref], radius=2.0)
    assert len(K.faces(only_part(doc).shape)) == 7          # block + the rounded corner
    insert_corner_cut(doc, fil["id"])
    err = doc.errors.get(fil["id"])
    assert err and ref["name"] in err, err
    assert list(doc.errors) == [fil["id"]]
    with_error = len(K.faces(only_part(doc).shape))
    doc.set_suppressed(fil["id"], True)
    assert not doc.errors
    assert with_error == len(K.faces(only_part(doc).shape)) == 8


def test_a_named_edge_survives_an_upstream_dimension_change_and_is_not_recovered():
    doc = Document()
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 40, 20), TOP)
    add(doc, "extrude", regions=region_refs(doc, sid), depth=10.0, op="new")
    ref = named_edge_refs(doc, corner_edge(40, 20), 1)[0]
    fil = add(doc, "fillet", edges=[ref], radius=2.0)
    assert fil["id"] not in doc.state.recovered
    ex = doc.features[1]
    f2 = dict(ex, params=dict(ex["params"], depth=15.0))
    doc.replace_feature(f2)
    assert not doc.errors, doc.errors
    assert fil["id"] not in doc.state.recovered
    assert len(K.faces(only_part(doc).shape)) == 7


# --------------------------------------------------------------------------- legacy

def test_legacy_references_rebuild_and_the_features_are_recovered(tmp_path):
    doc = block_doc()
    ref = named_edge_refs(doc, corner_edge(40, 20), 1)[0]
    fil = add(doc, "fillet", edges=[ref], radius=2.0)
    want_faces = len(K.faces(only_part(doc).shape))
    path = str(tmp_path / "studio.json")
    doc.save(path)
    with open(path) as fh:
        text = fh.read()
    d = json.loads(text)
    d["features"] = [dict(f, params=strip_names(f["params"])) for f in d["features"]]
    assert ref["name"] not in json.dumps(d)
    d["version"] = 2
    with open(path, "w") as fh:
        json.dump(d, fh)
    legacy = Document.load(path)
    assert not legacy.errors, legacy.errors
    assert fil["id"] in legacy.state.recovered
    assert len(K.faces(only_part(legacy).shape)) == want_faces
    # the sketch region reference of the extrude is legacy too (I1.5 policy) and is flagged the same way
    assert legacy.features[1]["id"] in legacy.state.recovered
    assert doc.features[0]["id"] not in legacy.state.recovered


def test_a_legacy_reference_built_without_a_map_is_recovered_on_rebuild():
    doc = block_doc()
    part = only_part(doc)
    refs = [F.make_ref(part.id, e, "edge", part.shape) for e in K.edges(part.shape)
            if corner_edge(40, 20)(K.edge_info(e))]
    fil = add(doc, "fillet", edges=refs, radius=2.0)
    assert fil["id"] in doc.state.recovered and not doc.errors
    assert fil["id"] in doc.state.copy().recovered


def test_runner_flag_does_not_leak_to_the_next_feature():
    doc = block_doc()
    part = only_part(doc)
    refs = [F.make_ref(part.id, e, "edge", part.shape) for e in K.edges(part.shape)
            if corner_edge(40, 20)(K.edge_info(e))]
    fil = add(doc, "fillet", edges=refs, radius=2.0)
    sid = sketch_feature(doc, lambda s: rect_lines(s, 100, 0, 5, 5), TOP)
    ex = add(doc, "extrude", regions=region_refs(doc, sid), depth=3.0, op="new")
    assert doc.state.recovered == {fil["id"]}
    assert ex["id"] not in doc.state.recovered


# --------------------------------------------------------------------------- feature list (offscreen Qt)

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_feature_list_marks_a_recovered_reference_and_shows_a_named_miss_as_an_error(qapp):
    from PySide6.QtCore import Qt
    from freeshape.ui import panels as P

    doc = block_doc()
    named_ref = named_edge_refs(doc, corner_edge(40, 20), 1)[0]
    part = only_part(doc)
    legacy_ref = F.make_ref(part.id, next(e for e in K.edges(part.shape) if corner_edge(0, 0)(K.edge_info(e))),
                            "edge", part.shape)
    lost = add(doc, "fillet", edges=[named_ref], radius=2.0)
    soft = add(doc, "fillet", edges=[legacy_ref], radius=1.0)
    insert_corner_cut(doc, lost["id"])

    lst = P.FeatureList()
    lst.populate(doc)

    def row(fid):
        return next(lst.item(i) for i in range(lst.count()) if lst.item(i).data(P.ROLE_ID) == fid)

    bad, warn, fine = row(lost["id"]), row(soft["id"]), row(doc.features[0]["id"])
    assert named_ref["name"] in doc.errors[lost["id"]]
    assert bad.foreground().color().name() == "#d93025" and bad.toolTip() == doc.errors[lost["id"]]
    assert soft["id"] in doc.state.recovered and soft["id"] not in doc.errors
    assert warn.toolTip() == P.RECOVERED_TIP and warn.foreground().color().name() == "#b06000"
    assert fine.toolTip() == "" and fine.foreground().color().name() != "#b06000"
    assert warn.icon().cacheKey() != fine.icon().cacheKey()
    assert warn.icon().pixmap(18, 18).toImage() != bad.icon().pixmap(18, 18).toImage()
