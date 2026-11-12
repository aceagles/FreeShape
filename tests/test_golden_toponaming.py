"""References surviving upstream edits (the "topological naming" behaviour).

Downstream features store geometric signatures of the faces / edges they use (features.make_ref) and re-find
them after every rebuild. Each case applies a fillet / chamfer / shell, then edits something earlier in the
tree and checks the blend still lands on the intended geometry. The before/after fingerprints are stored as
goldens: a kernel change that renumbers or reshapes faces shows up here first.
"""
import copy
import math

import numpy as np
import pytest

from conftest import (ANALYTIC, FREEFORM, add, approx_rel, assert_golden, both, centre_near, edge_refs,
                      face_refs, line_along, normal_is, only_part, pick_faces, region_refs, sketch_feature,
                      state_fingerprint)
from OCP.GeomAbs import GeomAbs_Torus
from freeshape import kernel as K
from freeshape.document import Document
from freeshape.sketch import ORIGIN, Sketch

PI = math.pi


def dim_block(w=40.0, d=20.0, h=10.0):
    """Block from a fully dimensioned rectangle sketch. Returns (doc, sketch id, extrude id, {name: constraint id})."""
    doc = Document()
    s = Sketch()
    p = [ORIGIN, s.add_point(w, 0), s.add_point(w, d), s.add_point(0, d)]
    L = [s.add_line(a, b) for a, b in zip(p, p[1:] + p[:1])]
    for line, t in zip(L, ["horizontal", "vertical", "horizontal", "vertical"]):
        s.add_constraint(t, line)
    dims = {"w": s.add_constraint("distance", L[0], value=w)["id"], "d": s.add_constraint("distance", L[1], value=d)["id"]}
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


def set_param(doc, fid, **params):
    f = copy.deepcopy(doc.feature(fid))
    f["params"].update(params)
    doc.replace_feature(f)


def record(name, before, doc, tol):
    assert_golden(f"toponaming/{name}", {"before": before, "after": state_fingerprint(doc),
                                         "errors": sorted(doc.errors.values())}, tol)


def round_faces(doc, r):
    return pick_faces(only_part(doc), lambda i: abs(i.get("r", 0) - r) < 1e-6)


def test_fillet_vertical_edges_survive_width_edit():
    doc, sid, _ex, dims = dim_block()
    add(doc, "fillet", edges=edge_refs(only_part(doc), line_along([0, 0, 1]), expect=4), radius=3.0)
    before = state_fingerprint(doc)
    set_dim(doc, sid, dims["w"], 60)
    assert not doc.errors, doc.errors
    assert len(round_faces(doc, 3)) == 4
    approx_rel(K.volume(only_part(doc).shape), 60 * 20 * 10 - 4 * (9 - 9 * PI / 4) * 10, rel=FREEFORM)
    record("fillet_vertical_width_60", before, doc, FREEFORM)


def test_fillet_right_side_edges_follow_the_right_side():
    """Only the two vertical edges at x = w are filleted; after widening they must still be at x = w."""
    doc, sid, _ex, dims = dim_block()
    right = both(line_along([0, 0, 1]), lambda i: abs(i["c"][0] - 40) < 1e-6)
    add(doc, "fillet", edges=edge_refs(only_part(doc), right, expect=2), radius=3.0)
    before = state_fingerprint(doc)
    for w in (100, 15):
        set_dim(doc, sid, dims["w"], w)
        assert not doc.errors, (w, doc.errors)
        xs = sorted(round(K.face_info(f)["c"][0], 3) for f in round_faces(doc, 3))
        assert len(xs) == 2 and all(x > w - 3 for x in xs), (w, xs)
    record("fillet_right_edges_width_100_then_15", before, doc, FREEFORM)


def _front_top_edge(w, h):
    return both(line_along([1, 0, 0]), centre_near([w / 2, 0, h]))


def _chamfer_face(doc):
    # match the 45 degree face by |normal|: face_info reports chamfer normals inward (see test_known_bugs.py)
    n = np.array([0, -1, 1]) / math.sqrt(2)
    return pick_faces(only_part(doc), lambda i: i.get("n") is not None and abs(float(np.dot(i["n"], n))) > 1 - 1e-6)


def test_chamfer_edge_survives_height_edit():
    doc, _sid, ex, _dims = dim_block()
    add(doc, "chamfer", edges=edge_refs(only_part(doc), _front_top_edge(40, 10), expect=1), distance=2.0)
    before = state_fingerprint(doc)
    set_param(doc, ex, depth=25.0)
    assert not doc.errors, doc.errors
    cf = _chamfer_face(doc)
    assert len(cf) == 1
    c = K.face_info(cf[0])["c"]
    assert np.allclose(c, [20, 1, 24], atol=1e-6), c
    approx_rel(K.volume(only_part(doc).shape), 40 * 20 * 25 - 0.5 * 2 * 2 * 40)
    record("chamfer_front_top_height_25", before, doc, ANALYTIC)


def test_chamfer_edge_survives_width_edit():
    doc, sid, _ex, dims = dim_block()
    add(doc, "chamfer", edges=edge_refs(only_part(doc), _front_top_edge(40, 10), expect=1), distance=2.0)
    before = state_fingerprint(doc)
    set_dim(doc, sid, dims["w"], 70)
    assert not doc.errors, doc.errors
    assert len(_chamfer_face(doc)) == 1
    approx_rel(K.volume(only_part(doc).shape), 70 * 20 * 10 - 0.5 * 2 * 2 * 70)
    record("chamfer_front_top_width_70", before, doc, ANALYTIC)


def test_shell_open_face_survives_height_and_depth_edit():
    doc, sid, ex, dims = dim_block()
    add(doc, "shell", faces=face_refs(only_part(doc), normal_is([0, 0, 1]), expect=1), thickness=2.0)
    before = state_fingerprint(doc)
    set_param(doc, ex, depth=30.0)
    set_dim(doc, sid, dims["d"], 35)
    assert not doc.errors, doc.errors
    approx_rel(K.volume(only_part(doc).shape), 40 * 35 * 30 - 36 * 31 * 28)
    top = pick_faces(only_part(doc), both(normal_is([0, 0, 1]), lambda i: abs(i["c"][2] - 30) < 1e-6))
    assert len(top) == 1                                            # only the rim is left on top
    approx_rel(K.face_info(top[0])["area"], 40 * 35 - 36 * 31)
    record("shell_top_height_30_depth_35", before, doc, ANALYTIC)


def test_fillet_on_hole_edge_follows_the_hole():
    doc, _sid, _ex, _dims = dim_block()
    hs = sketch_feature(doc, lambda s: s.add_point_entity(s.add_point(10, 10)))
    add(doc, "hole", sketch=hs, standard="custom", diameter=8.0, end="through", style="simple")
    rim = edge_refs(only_part(doc), lambda i: abs(i.get("r", 0) - 4) < 1e-6 and abs(i["c"][2] - 10) < 1e-6, expect=1)
    add(doc, "fillet", edges=rim, radius=1.0)
    before = state_fingerprint(doc)
    s = Sketch()
    s.add_point_entity(s.add_point(30, 10))
    s.solve()
    f = copy.deepcopy(doc.feature(hs))
    f["params"]["sketch"] = s.to_dict()
    doc.replace_feature(f)
    assert not doc.errors, doc.errors
    tor = pick_faces(only_part(doc), lambda i: i["type"] == int(GeomAbs_Torus))
    assert len(tor) == 1
    c = K.face_info(tor[0])["c"]
    assert abs(c[0] - 30) < 1e-6 and abs(c[1] - 10) < 1e-6 and c[2] > 9, c
    record("fillet_hole_rim_hole_moved", before, doc, FREEFORM)


def test_fillet_survives_feature_inserted_upstream():
    doc, _sid, _ex, _dims = dim_block()
    fil = add(doc, "fillet", edges=edge_refs(only_part(doc), line_along([0, 0, 1]), expect=4), radius=3.0)
    before = state_fingerprint(doc)
    s = Sketch()
    s.add_circle(s.add_point(20, 10), 5)
    s.solve()
    sk = doc.make_feature("sketch")
    sk["params"]["sketch"] = s.to_dict()
    doc.add_feature(sk, index=2)
    cut = doc.make_feature("extrude")
    cut["params"].update(regions=region_refs(doc, sk["id"]), depth=4.0, op="remove")
    doc.add_feature(cut, index=3)
    assert [f["id"] for f in doc.features][-1] == fil["id"]
    assert not doc.errors, doc.errors
    assert len(round_faces(doc, 3)) == 4
    approx_rel(K.volume(only_part(doc).shape), 8000 - 4 * (9 - 9 * PI / 4) * 10 - 25 * PI * 4, rel=FREEFORM)
    record("fillet_after_upstream_pocket", before, doc, FREEFORM)


def test_chamfer_on_suppressed_boss_baseline():
    """A chamfer on the top edge of a boss, then the boss is suppressed. The edge no longer exists: today the
    resolver either reports the missing reference or picks the nearest similar edge. Records which."""
    doc, _sid, _ex, _dims = dim_block()
    bs = sketch_feature(doc, lambda s: s.add_circle(s.add_point(20, 10), 5))
    boss = add(doc, "extrude", regions=region_refs(doc, bs), depth=15.0, op="add")
    top_rim = edge_refs(only_part(doc), lambda i: abs(i.get("r", 0) - 5) < 1e-6 and abs(i["c"][2] - 15) < 1e-6,
                        expect=1)
    ch = add(doc, "chamfer", edges=top_rim, distance=1.0)
    before = state_fingerprint(doc)
    doc.set_suppressed(boss["id"], True)
    outcome = "error" if ch["id"] in doc.errors else "resolved"
    assert_golden("toponaming/chamfer_suppressed_boss", {"outcome": outcome, "before": before,
                                                         "after": state_fingerprint(doc),
                                                         "errors": sorted(doc.errors.values())}, ANALYTIC)
    doc.set_suppressed(boss["id"], False)
    assert not doc.errors
    assert state_fingerprint(doc).keys() == before.keys()


@pytest.mark.parametrize("w", [30.0, 45.0, 80.0])
def test_bracket_fillet_survives_width_sweep(w):
    """The shipped bracket's tree with its first sketch dimension changed: every feature still resolves."""
    import os
    from conftest import EXAMPLES_DIR
    doc = Document.load(os.path.join(EXAMPLES_DIR, "bracket.fshape"))
    sk = doc.features[0]
    s = Sketch.from_dict(sk["params"]["sketch"])
    dims = [c for c in s.constraints if c["type"] in ("distance", "hdistance")]
    if not dims:
        pytest.skip("bracket sketch has no distance dimension")
    before = state_fingerprint(doc)
    set_dim(doc, sk["id"], dims[0]["id"], w)
    assert_golden(f"toponaming/bracket_first_dim_{w:g}", {"before": before, "after": state_fingerprint(doc),
                                                          "errors": sorted(doc.errors.values())}, FREEFORM)
