"""Assembly connectors and drawing dimensions use the reference policy (I1.13, ADR 0003,
docs/reference/element-naming.md section 6.4): they store {name, signature}, find their element by name,
report a lost name as an error that names it, and flag a signature-only match as recovered."""
import json
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: E402

from freeshape import assembly as A  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import links as L  # noqa: E402
from freeshape.drawing import DIRECTIONS, DrawingDoc  # noqa: E402
from freeshape.features import Part  # noqa: E402

from conftest import (TOP, add, assert_golden, block_doc, normal_is, only_part, rect_lines,  # noqa: E402
                      region_refs, sketch_feature)

GROUND = BRepPrimAPI_MakeBox(100.0, 60.0, 5.0).Shape()


# --------------------------------------------------------------------------- builders

def set_depth(doc, depth):
    ex = doc.features[1]
    doc.replace_feature(dict(ex, params=dict(ex["params"], depth=depth)))
    assert not doc.errors, doc.errors


def cut_corner(doc, x0=30.0, y0=10.0, w=20.0, h=20.0):
    """Remove the block's (40, 20) corner with a cut extrude, appended after the extrude."""
    sid = sketch_feature(doc, lambda s: rect_lines(s, x0, y0, w, h), TOP)
    add(doc, "extrude", regions=region_refs(doc, sid), depth=10.0, op="remove")


def top_face(shape):
    return next(i for i, f in enumerate(K.faces(shape)) if normal_is((0, 0, 1))(K.face_info(f)))


def corner_edge_index(shape, x=40.0, y=20.0):
    for i, e in enumerate(K.edges(shape)):
        info = K.edge_info(e)
        if (info["type"] == int(K.GeomAbs_Line) and abs(abs(info["d"][2]) - 1) < 1e-6
                and abs(info["c"][0] - x) < 1e-6 and abs(info["c"][1] - y) < 1e-6):
            return i
    raise AssertionError("corner edge not found")


def assembly_over(doc):
    """An assembly of the studio's block and a plain ground box; the block's element map comes through
    `named_resolver`, as the link resolver supplies it in the app."""
    a = A.AssemblyDoc()
    a.resolver = lambda src: only_part(doc) if src["part"] == "blk" else Part("g", "ground", GROUND)
    a.named_resolver = lambda src: doc.state.named(only_part(doc).id) if src["part"] == "blk" else None
    ig = a.insert({"kind": "studio", "uid": "u1", "part": "gnd"})
    ib = a.insert({"kind": "studio", "uid": "u1", "part": "blk"})
    return a, ig, ib


def fasten(a, ig, ib, kind_b, index_b):
    ca = a.connector(ig, "face", top_face(a.parts[ig].shape))
    cb = a.connector(ib, kind_b, index_b)
    m = a.make_mate("fastened")
    m["a"], m["b"] = ca, cb
    a.add_mate(m)
    return m


# --------------------------------------------------------------------------- assembly connectors

def test_connector_stores_name_and_signature():
    doc = block_doc()
    a, ig, ib = assembly_over(doc)
    top = top_face(a.parts[ib].shape)
    c = a.connector(ib, "face", top)
    nm = doc.state.named(only_part(doc).id)
    assert c["ref"]["name"] == nm.name_of(K.faces(a.parts[ib].shape)[top])
    assert c["ref"]["kind"] == "face" and "area" in c["ref"] and "bb" in c["ref"]
    assert "name" not in a.connector(ig, "face", 0)["ref"]          # a part with no element map: signature only
    assert "part" not in c["ref"]


def test_mate_on_a_named_face_survives_an_upstream_dimension_change():
    doc = block_doc()
    a, ig, ib = assembly_over(doc)
    m = fasten(a, ig, ib, "face", top_face(a.parts[ib].shape))
    assert not a.errors
    set_depth(doc, 15.0)
    a.refresh()
    assert not a.errors, a.errors
    assert a.recovered_connectors() == []
    cb = m["b"]
    shp = a.parts[ib].shape
    assert cb["index"] == top_face(shp)
    assert np.allclose(cb["frame"]["o"][2], 15.0) and np.allclose(cb["frame"]["z"], [0, 0, 1])
    assert_golden("connector_refs/mate_after_depth_change", {
        "frame": cb["frame"], "T": a.instance(ib)["T"]})


def test_named_connector_is_found_by_name_even_when_the_signature_points_elsewhere():
    doc = block_doc()
    a, ig, ib = assembly_over(doc)
    m = fasten(a, ig, ib, "face", top_face(a.parts[ib].shape))
    m["b"]["ref"] = dict(m["b"]["ref"], c=[0.0, 0.0, 0.0], area=1.0, n=[1.0, 0.0, 0.0])
    set_depth(doc, 15.0)
    a.refresh()
    assert not a.errors, a.errors
    assert m["b"]["index"] == top_face(a.parts[ib].shape)


def test_mate_whose_edge_is_consumed_reports_an_error_naming_it():
    doc = block_doc()
    a, ig, ib = assembly_over(doc)
    m = fasten(a, ig, ib, "edge", corner_edge_index(a.parts[ib].shape))
    name = m["b"]["ref"]["name"]
    cut_corner(doc)
    a.refresh()
    err = a.errors.get(m["id"])
    assert err and name in err, err


def test_mate_whose_face_is_consumed_reports_an_error_naming_it():
    doc = block_doc()
    a, ig, ib = assembly_over(doc)
    side = next(i for i, f in enumerate(K.faces(a.parts[ib].shape)) if normal_is((1, 0, 0))(K.face_info(f)))
    m = fasten(a, ig, ib, "face", side)
    name = m["b"]["ref"]["name"]
    cut_corner(doc, 30.0, -5.0, 20.0, 30.0)                                   # removes the whole x = 40 end
    a.refresh()
    err = a.errors.get(m["id"])
    assert err and name in err, err


def test_legacy_connector_is_matched_by_signature_and_marked_recovered():
    doc = block_doc()
    a, ig, ib = assembly_over(doc)
    m = fasten(a, ig, ib, "face", top_face(a.parts[ib].shape))
    m["b"]["ref"].pop("name")                                                  # what a v1 assembly holds
    set_depth(doc, 15.0)
    a.refresh()
    assert not a.errors, a.errors
    assert a.recovered_connectors() == [(m["id"], "b")]
    assert m["b"]["index"] == top_face(a.parts[ib].shape)
    assert "recovered" not in json.dumps(a.to_json(embed=False))               # a marker, not a stored claim
    set_depth(doc, 20.0)
    m["b"]["ref"] = dict(m["b"]["ref"], name="nobody;:H1")                    # named again: the marker clears...
    a.refresh()
    assert a.errors.get(m["id"]) and "nobody;:H1" in a.errors[m["id"]]         # ...and the lost name is an error


def test_part_without_an_element_map_keeps_matching_by_signature_unflagged():
    a = A.AssemblyDoc()
    a.resolver = lambda src: Part("g", "ground", GROUND)
    i1 = a.insert({"kind": "studio", "uid": "u1", "part": "gnd"})
    i2 = a.insert({"kind": "studio", "uid": "u1", "part": "gnd"})
    m = fasten(a, i1, i2, "face", top_face(GROUND))
    a.refresh()
    assert not a.errors and a.recovered_connectors() == []


def test_link_resolver_hands_the_element_map_to_the_assembly():
    doc = block_doc()
    pid = only_part(doc).id
    r = L.LinkResolver(find_open_studio=lambda uid, path: doc if uid == doc.uid else None)
    a = A.AssemblyDoc()
    a.resolver = r.resolve_source                       # named_resolver stays unset: found on the resolver
    ib = a.insert({"kind": "studio", "uid": doc.uid, "part": pid})
    c = a.connector(ib, "face", top_face(a.parts[ib].shape))
    assert c["ref"].get("name")
    assert r.resolve_named({"kind": "studio", "uid": doc.uid, "part": pid}) is doc.state.named(pid)
    assert r.resolve_named({"kind": "studio", "uid": "nope", "part": pid}) is None


def test_refind_is_gone():
    assert not hasattr(A, "_refind")


# --------------------------------------------------------------------------- drawing dimensions

def drawing_over(doc):
    g = DrawingDoc()
    g.source = {"kind": "studio", "part": "blk"}
    g.resolver = lambda src: only_part(doc).shape
    g.named_resolver = lambda src: doc.state.named(only_part(doc).id)
    g.refresh(notify=False)
    return g


def vertex_end(g, x, y, z):
    verts, _ = g._snapdata()
    i = next(i for i, v in enumerate(verts) if np.allclose(v, (x, y, z)))
    return {"kind": "vertex", "p": [float(c) for c in verts[i]], "i": i, "n": len(verts)}


def length_dim(g, ends):
    v = g.add_view(*DIRECTIONS["Front"], (100, 150), checkpoint=False)
    return g.add_annot({"type": "lin", "view": v["id"], "a": ends[0], "b": ends[1], "orient": "h",
                        "pos": [100, 120]})


def test_dimension_end_stores_the_name_of_its_vertex():
    doc = block_doc()
    g = drawing_over(doc)
    d = length_dim(g, [vertex_end(g, 0, 0, 0), vertex_end(g, 40, 20, 10)])
    nm = doc.state.named(only_part(doc).id)
    assert d["a"]["name"] and d["b"]["name"] and d["a"]["name"] != d["b"]["name"]
    assert d["b"]["name"] in nm.names_of("V") and d["b"]["nkind"] == "vertex"


def test_dimension_follows_a_named_vertex_across_an_upstream_dimension_change():
    doc = block_doc()
    g = drawing_over(doc)
    d = length_dim(g, [vertex_end(g, 0, 0, 0), vertex_end(g, 40, 20, 10)])
    d["b"]["i"] = (d["b"]["i"] + 1) % d["b"]["n"]          # a stale index must not matter to a named end
    set_depth(doc, 15.0)
    g.refresh(notify=False)
    assert np.allclose(d["b"]["p"], (40, 20, 15)), d["b"]
    assert not d["b"].get("lost") and g.recovered_dimensions() == [] and g.dimension_errors() == {}


def test_dimension_whose_vertex_is_consumed_is_lost_with_an_error_naming_it():
    doc = block_doc()
    g = drawing_over(doc)
    d = length_dim(g, [vertex_end(g, 0, 0, 0), vertex_end(g, 40, 20, 10)])
    name = d["b"]["name"]
    cut_corner(doc)
    g.refresh(notify=False)
    assert d["b"].get("lost") and not d["a"].get("lost")
    assert name in g.dimension_errors()[d["id"]]


def drilled(doc, x):
    """(shape, element map) of the block with a hole of r = 3 at (x, 10), named the way a boolean names it."""
    from freeshape import naming as N
    part = only_part(doc)
    tool = K.cylinder((x, 10, -1), (0, 0, 1), 3.0, 12.0)
    shape, hist = K.cut(part.shape, tool, with_history=True)
    return shape, N.name_result(shape, hist, [doc.state.named(part.id), N.seed(tool, "HOLE")], "HOLE", N.OP_CUT)


def test_circle_end_follows_its_hole_by_name_and_is_lost_when_the_hole_is_filled():
    doc = block_doc()
    cur = {}
    cur["shape"], cur["named"] = drilled(doc, 20.0)
    g = DrawingDoc()
    g.source = {"kind": "studio", "part": "blk"}
    g.resolver = lambda src: cur["shape"]
    g.named_resolver = lambda src: cur["named"]
    g.refresh(notify=False)
    _verts, circles = g._snapdata()
    ci = next(i for i, c in enumerate(circles) if not c[4] and abs(c[0][2] - 10) < 1e-6)
    v = g.add_view(*DIRECTIONS["Top"], (100, 150), checkpoint=False)
    d = g.add_annot({"type": "dia", "view": v["id"], "pos": [140, 150],
                     "a": {"kind": "centre", "p": [float(x) for x in circles[ci][0]], "r": circles[ci][2],
                           "i": ci, "n": len(circles)}})
    assert d["a"]["name"] and d["a"]["nkind"] == "edge"
    # an upstream change moves the same hole: same name, new place
    cur["shape"], cur["named"] = drilled(doc, 25.0)
    g.refresh(notify=False)
    assert not d["a"].get("lost") and g.dimension_errors() == {}, g.dimension_errors()
    assert np.allclose(d["a"]["p"][:2], (25, 10)) and g.recovered_dimensions() == []
    # the hole is gone: the dimension is lost, never attached to some other circle
    cur["shape"], cur["named"] = only_part(doc).shape, doc.state.named(only_part(doc).id)
    g.refresh(notify=False)
    assert d["a"].get("lost") and d["a"]["name"] in g.dimension_errors()[d["id"]]


def test_legacy_dimension_is_matched_by_position_and_marked_recovered():
    doc = block_doc()
    g = drawing_over(doc)
    d = length_dim(g, [vertex_end(g, 0, 0, 0), vertex_end(g, 40, 20, 10)])
    for k in ("a", "b"):
        d[k].pop("name")
        d[k].pop("nkind")
    set_depth(doc, 15.0)
    g.refresh(notify=False)
    assert np.allclose(d["b"]["p"], (40, 20, 15)) and not d["b"].get("lost")
    assert g.recovered_dimensions() == [d["id"]]
    assert "recovered" not in g.to_json(embed=False)


def test_drawing_of_a_model_without_an_element_map_is_not_flagged():
    shape = BRepPrimAPI_MakeBox(40.0, 20.0, 10.0).Shape()
    g = DrawingDoc()
    g.source = {"kind": "studio"}
    g.resolver = lambda src: shape
    g.refresh(notify=False)
    d = length_dim(g, [vertex_end(g, 0, 0, 0), vertex_end(g, 40, 20, 10)])
    assert "name" not in d["a"]
    g.shape = None
    g.refresh(notify=False)
    assert g.recovered_dimensions() == [] and not d["a"].get("lost")
