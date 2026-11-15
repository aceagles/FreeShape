"""I1.2: a round face, a sketch circle or a sketch arc as an axis (revolve, circular and linear pattern, move)."""
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from conftest import FRONT, TOP, add, edge_refs, face_refs, normal_is, rect_lines, region_refs, sketch_feature, try_add  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.ui.dialogs import item_geometry, item_label, pick_to_item  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402
from test_axis_use import _same_sig, _sig, make_win  # noqa: E402,F401  (make_win is a fixture)

CYL = lambda i: i["type"] == int(K.GeomAbs_Cylinder)  # noqa: E731


def _on_line(o, d, pt):
    r = np.asarray(pt, float) - np.asarray(o, float)
    return np.linalg.norm(r - (r @ d) * d) < 1e-6


def _pin_doc():
    """A cylinder of radius 5, height 10, standing on Top at (10, 20), and a small box beside it."""
    doc = Document()
    sid = sketch_feature(doc, lambda s: s.add_circle(s.add_point(10, 20), 5))
    add(doc, "extrude", regions=region_refs(doc, sid), depth=10, op="new")
    sb = sketch_feature(doc, lambda s: rect_lines(s, 30, 20, 3, 3))
    add(doc, "extrude", regions=region_refs(doc, sb), depth=3, op="new")
    return doc


def _pin(doc):
    return next(p for p in doc.state.parts.values() if abs(K.volume(p.shape) - np.pi * 25 * 10) < 1e-3)


def _face_axis_ref(doc):
    (r,) = face_refs(_pin(doc), CYL, expect=1)
    return {"kind": "face", "face": r}


def _edge_axis_ref(doc):
    part = _pin(doc)
    (r,) = edge_refs(part, lambda i: i["type"] == int(K.GeomAbs_Circle) and abs(i["center"][2] - 10) < 1e-6, expect=1)
    return {"kind": "edge", "edge": r}


def _box_id(doc):
    pin = _pin(doc)
    return next(i for i, p in doc.state.parts.items() if p is not pin)


def test_cylinder_face_axis_is_parallel_to_z_through_the_centre():
    doc = _pin_doc()
    o, d = F.resolve_axis(doc.state, _face_axis_ref(doc))
    assert np.linalg.norm(np.cross(d, [0, 0, 1])) < 1e-9
    assert abs(o[0] - 10) < 1e-9 and abs(o[1] - 20) < 1e-9


def test_cone_face_axis_equals_its_base_circle_edge():
    doc = Document()
    def tri(s):
        p = [s.add_point(0, 0), s.add_point(6, 0), s.add_point(0, 10)]
        for a, b in zip(p, p[1:] + p[:1]):
            s.add_line(a, b)
    sid = sketch_feature(doc, tri, FRONT)
    add(doc, "revolve", regions=region_refs(doc, sid), axis={"kind": "std", "name": "Z"}, end="full", op="new")
    part = next(iter(doc.state.parts.values()))
    (cone,) = face_refs(part, lambda i: i["type"] == int(K.GeomAbs_Cone), expect=1)
    (base,) = edge_refs(part, lambda i: i["type"] == int(K.GeomAbs_Circle) and abs(i["center"][2]) < 1e-6, expect=1)
    o1, d1 = F.resolve_axis(doc.state, {"kind": "face", "face": cone})
    o2, d2 = F.resolve_axis(doc.state, {"kind": "edge", "edge": base})
    assert np.linalg.norm(np.cross(d1, d2)) < 1e-9
    assert _on_line(o2, d2, o1) and _on_line(o1, d1, o2)


def test_circular_pattern_about_the_face_equals_the_edge():
    sigs = []
    for make in (_face_axis_ref, _edge_axis_ref):
        doc = _pin_doc()
        add(doc, "circular_pattern", parts=[_box_id(doc)], axis=make(doc), count=6, angle=360.0, op="new")
        sigs.append(_sig(list(doc.state.parts.values())))
    assert len(sigs[0]) == 7
    _same_sig(*sigs)


def _revolve_doc(axis_of):
    doc = _pin_doc()
    sid = sketch_feature(doc, lambda s: s.add_circle(s.add_point(25, 5), 2), FRONT)
    add(doc, "revolve", regions=region_refs(doc, sid), axis=axis_of(doc), end="full", op="new")
    return doc


def test_revolve_about_the_face_equals_the_axis_feature():
    def feature_axis(doc):
        add_f = add(doc, "axis", type="round_face", refs=[{"k": "face", "ref": _face_axis_ref(doc)["face"]}])
        return {"kind": "axis", "fid": add_f["id"]}
    a = _revolve_doc(_face_axis_ref)
    b = _revolve_doc(feature_axis)
    _same_sig(_sig(list(a.state.parts.values())), _sig(list(b.state.parts.values())))
    assert len(a.state.parts) == 3


def _solid_doc(build, depth=4):
    doc = Document()
    sid = sketch_feature(doc, build)
    add(doc, "extrude", regions=region_refs(doc, sid), depth=depth, op="new")
    return doc, next(iter(doc.state.parts.values()))


def _top_face_item(doc, part, vp=None):
    idx = next(i for i, f in enumerate(K.faces(part.shape)) if normal_is([0, 0, 1])(K.face_info(f)))
    vp = vp or SimpleNamespace(part_shapes={part.id: part.shape}, sketches=[])
    return pick_to_item(vp, Pick("face", (part.id, idx)), {"axis"}, doc.state)


def test_disc_and_annulus_give_an_axis_through_the_centre_and_other_flat_faces_none():
    disc, dpart = _solid_doc(lambda s: s.add_circle(s.add_point(3, 4), 5))
    kind, ref = _top_face_item(disc, dpart)
    o, d = F.resolve_axis(disc.state, ref)
    assert kind == "axis" and ref["kind"] == "face"
    assert np.allclose(o[:2], [3, 4]) and np.allclose(d, [0, 0, 1])      # the outward normal, through the centre

    def ring(s):
        c = s.add_point(-7, 2)
        s.add_circle(c, 6)
        s.add_circle(c, 3)
    annulus = Document()
    sid = sketch_feature(annulus, ring)
    add(annulus, "extrude", regions=region_refs(annulus, sid, lambda i: abs(i["area"] - np.pi * 27) < 1e-3),
        depth=4, op="new")
    apart = next(iter(annulus.state.parts.values()))
    kind, ref = _top_face_item(annulus, apart)
    o, d = F.resolve_axis(annulus.state, ref)
    assert np.allclose(o[:2], [-7, 2]) and np.allclose(d, [0, 0, 1])

    def dshape(s):                                  # half disc: an arc and its chord; the centroid is off the centre
        c, a, b = s.add_point(0, 0), s.add_point(5, 0), s.add_point(-5, 0)
        s.add_arc(c, a, b)
        s.add_line(b, a)
    dd, dpart = _solid_doc(dshape)
    assert _top_face_item(dd, dpart) is None

    rect, rpart = _solid_doc(lambda s: rect_lines(s, 0, 0, 8, 5))
    assert _top_face_item(rect, rpart) is None


def _circle_vp(doc, sid):
    sk = doc.state.sketches[sid]
    return SimpleNamespace(part_shapes={}, sketches=[(sid, SimpleNamespace(sketch=sk.sketch), None)])


def _torus_volume(R, r):
    return 2 * np.pi * R * np.pi * r * r


def test_sketch_circle_as_the_revolve_axis():
    doc = Document()
    sa = sketch_feature(doc, lambda s: s.add_circle(s.add_point(10, 0), 4), FRONT)   # Front: x, z in plane; normal Y
    eid = next(iter(doc.state.sketches[sa].sketch.entities))
    item = pick_to_item(_circle_vp(doc, sa), Pick("sketch_line", (sa, eid)), {"axis"}, doc.state)
    assert item == ("axis", {"kind": "sketch_circle", "sketch": sa, "entity": eid})      # was sketch_line: KeyError p1
    o, d = F.resolve_axis(doc.state, item[1])
    assert np.allclose(o, [10, 0, 0]) and np.linalg.norm(np.cross(d, [0, 1, 0])) < 1e-9
    sb = sketch_feature(doc, lambda s: s.add_circle(s.add_point(30, 5), 2), TOP)
    add(doc, "revolve", regions=region_refs(doc, sb), axis=item[1], end="full", op="new")
    (part,) = doc.state.parts.values()
    assert abs(K.volume(part.shape) - _torus_volume(20, 2)) < 1e-4 * _torus_volume(20, 2)
    assert item_label(doc, item) == "Sketch circle"
    kind, pts = item_geometry(doc.state, item)
    assert kind == "poly" and len(pts) == 2


def test_sketch_arc_axis_goes_through_the_centre_and_ellipse_is_rejected():
    doc = Document()

    def build(s):
        c = s.add_point(2, 3)
        s.add_arc(c, s.add_point(7, 3), s.add_point(2, 8))
        s.add_ellipse(s.add_point(0, 0), s.add_point(6, 0), 2)
    sid = sketch_feature(doc, build)
    ents = doc.state.sketches[sid].sketch.entities
    arc = next(i for i, e in ents.items() if e["type"] == "arc")
    ell = next(i for i, e in ents.items() if e["type"] == "ellipse")
    o, d = F.resolve_axis(doc.state, {"kind": "sketch_circle", "sketch": sid, "entity": arc})
    assert np.allclose(o, [2, 3, 0]) and np.allclose(d, [0, 0, 1])
    with pytest.raises(K.KernelError):
        F.resolve_axis(doc.state, {"kind": "sketch_circle", "sketch": sid, "entity": ell})


def test_saved_sketch_line_axis_on_an_arc_still_means_its_chord():
    def make(use_arc):
        doc = Document()
        if use_arc:
            def build(s):
                s.add_arc(s.add_point(0, 0), s.add_point(10, 0), s.add_point(0, 10))
        else:
            def build(s):
                s.add_line(s.add_point(10, 0), s.add_point(0, 10))
        sa = sketch_feature(doc, build)
        eid = next(iter(doc.state.sketches[sa].sketch.entities))
        sb = sketch_feature(doc, lambda s: s.add_circle(s.add_point(15, 15), 2), TOP)
        add(doc, "revolve", regions=region_refs(doc, sb), axis={"kind": "sketch_line", "sketch": sa, "line": eid},
            end="full", op="new")
        return doc
    a, b = make(True), make(False)
    _same_sig(_sig(list(a.state.parts.values())), _sig(list(b.state.parts.values())))
    (pa,) = a.state.parts.values()
    assert K.volume(pa.shape) > 100


def test_revolve_axis_normal_to_the_profile_plane_names_the_error():
    doc = Document()
    sid = sketch_feature(doc, lambda s: s.add_circle(s.add_point(10, 10), 2), TOP)
    _, err = try_add(doc, "revolve", regions=region_refs(doc, sid), axis={"kind": "std", "name": "Z"},
                     end="full", op="new")
    assert err == "The axis must not be perpendicular to the sketch plane"


def test_other_flat_face_raises_the_pick_message():
    doc, part = _solid_doc(lambda s: rect_lines(s, 0, 0, 8, 5))
    (r,) = face_refs(part, normal_is([0, 0, 1]), expect=1)
    with pytest.raises(K.KernelError, match="Pick a round face, an edge, a sketch line or an axis"):
        F.resolve_axis(doc.state, {"kind": "face", "face": r})


def test_labels_for_face_axes():
    doc = _pin_doc()
    ref = _face_axis_ref(doc)
    assert item_label(doc, ("axis", ref)) == "Cylinder axis"
    disc, part = _solid_doc(lambda s: s.add_circle(s.add_point(0, 0), 3))
    assert item_label(disc, _top_face_item(disc, part)) == "Face axis"


def test_axis_fields_offer_faces_but_a_path_field_does_not(make_win):
    doc = _pin_doc()
    w = make_win(doc)
    for ftype, key in (("revolve", "axis_sel"), ("circular_pattern", "axis_sel"), ("linear_pattern", "dir_sel"),
                       ("move", "axis_sel")):
        w.start_feature(ftype)
        dlg = w.dialog
        dlg.activate(dlg.fields[key])
        assert "face" in w.viewport.select_filter, ftype
        w.cancel_dialog()
    w.start_feature("sweep")
    dlg = w.dialog
    dlg.activate(dlg.fields["path"])
    assert "face" not in w.viewport.select_filter
    w.cancel_dialog()
