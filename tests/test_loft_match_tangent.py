"""Loft end condition Match tangent (I7.5): the loft leaves a profile continuing its adjacent faces, built
by Hermite skinning (scripts/spikes/op2_loft.result.txt: skin=hermite on both OCP builds). Part A is a cone
frustum, radius 20 at z 0 to radius 15 at z 20; the loft runs from its top face to a circle of radius 25
at z 50, so with no condition it turns atan(5/20) + atan(10/30) = 32.47 degrees at the start edge.
"""
import math
import os
import signal
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from OCP.BRep import BRep_Tool  # noqa: E402
from OCP.BRepAdaptor import BRepAdaptor_Surface  # noqa: E402
from OCP.GeomAbs import GeomAbs_Cone, GeomAbs_Plane  # noqa: E402
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf  # noqa: E402
from OCP.GeomLProp import GeomLProp_SLProps  # noqa: E402
from OCP.gp import gp_Pnt  # noqa: E402

from conftest import FRONT, TOP, add, region_refs  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.document import PartStudioDoc  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402
from test_loft_end_conditions import _volume  # noqa: E402
from test_loft_holes import _disc  # noqa: E402
from test_naming_core_features import assert_all_named  # noqa: E402

TOUCH = "Pick faces that touch the profile"
NO_CONDITION = math.degrees(math.atan(5 / 20) + math.atan(10 / 30))      # 32.47


# --------------------------------------------------------------------------- the model

def _sketch(doc, plane, draw):
    s = Sketch()
    draw(s)
    s.solve()
    f = doc.make_feature("sketch")
    f["params"].update(plane=plane, sketch=s.to_dict())
    doc.add_feature(f)
    assert not doc.errors.get(f["id"]), doc.errors
    return f["id"]


def _trapezoid(s):
    p = [s.add_point(0, 0), s.add_point(20, 0), s.add_point(15, 20), s.add_point(0, 20)]
    for a, b in zip(p, p[1:] + p[:1]):
        s.add_line(a, b)


def _base_doc():
    """Part A (the frustum, revolved from a Front sketch about Z) and the r 25 circle sketch at z 50."""
    doc = PartStudioDoc()
    sid = _sketch(doc, FRONT, _trapezoid)
    add(doc, "revolve", regions=region_refs(doc, sid), axis={"kind": "std", "name": "Z"}, end="full", op="new")
    pl = add(doc, "plane", type="offset", refs=[], ref=TOP, offset=50.0)
    cid = _sketch(doc, {"kind": "plane", "fid": pl["id"]}, lambda s: s.add_circle(s.add_point(0, 0), 25.0))
    (pid, part), = doc.state.parts.items()
    return doc, pid, cid


def _face_of(part, kind, z=None):
    for f in K.faces(part.shape):
        ad = BRepAdaptor_Surface(f)
        if ad.GetType() == kind and (z is None or abs(K.face_info(f)["c"][2] - z) < 1e-6):
            return f
    raise AssertionError(f"no face {kind} at z {z}")


def _refs(doc, pid):
    part = doc.state.parts[pid]
    named = doc.state.named(pid)
    ref = lambda f: F.make_ref(pid, f, "face", part.shape, named)       # noqa: E731
    return {"top": ref(_face_of(part, GeomAbs_Plane, 20.0)), "bottom": ref(_face_of(part, GeomAbs_Plane, 0.0)),
            "wall": ref(_face_of(part, GeomAbs_Cone))}


def _loft_params(doc, pid, cid, start=None):
    p = {"op": "new", "profiles": [{"kind": "face", "ref": _refs(doc, pid)["top"]},
                                   {"kind": "region", "ref": region_refs(doc, cid)[0]}]}
    if start is not None:
        p["start"] = start
    return p


def _lofted(faces=None, cond=True):
    """The document with the loft; `faces` names the adjacent faces picked (None: left empty)."""
    doc, pid, cid = _base_doc()
    start = None
    if cond:
        start = {"cond": "match_tangent", "mag": 1.0}
        if faces:
            refs = _refs(doc, pid)
            start["faces"] = [refs[k] for k in faces]
    add(doc, "loft", **_loft_params(doc, pid, cid, start))
    return doc, pid


def _loft_part(state, frustum):
    others = [p for pid, p in state.parts.items() if pid != frustum]
    assert len(others) == 1, list(state.parts)
    return others[0]


def _normal(face, p):
    surf = BRep_Tool.Surface_s(face)
    u, v = GeomAPI_ProjectPointOnSurf(gp_Pnt(*p), surf).LowerDistanceParameters()
    n = GeomLProp_SLProps(surf, u, v, 1, 1e-9).Normal()
    return np.array([n.X(), n.Y(), n.Z()])


def _start_angles(loft_shape, wall):
    """At 16 points round the start edge (r 15, z 20): the angle in degrees between the loft-face normal
    and the cone-face normal."""
    side = [f for f in K.faces(loft_shape) if BRepAdaptor_Surface(f).GetType() != GeomAbs_Plane]
    out = []
    for k in range(16):
        a = 2 * math.pi * (k + 0.5) / 16
        p = (15 * math.cos(a), 15 * math.sin(a), 20.0)
        face = min(side, key=lambda f: K._point_face_distance(p, f))
        c = abs(float(_normal(face, p) @ _normal(wall, p)))
        out.append(math.degrees(math.acos(min(1.0, c))))
    return out


def _wall_of(state, pid):
    return _face_of(state.parts[pid], GeomAbs_Cone)


# --------------------------------------------------------------------------- the acceptance criteria

def test_match_tangent_continues_the_cone_wall():
    doc, pid = _lofted(["wall"])
    body = _loft_part(doc.state, pid).shape
    assert len(K.solids(body)) == 1 and K.is_valid(body) and len(K.faces(body)) == 3
    angles = _start_angles(body, _wall_of(doc.state, pid))
    assert max(angles) < 0.5, angles


def test_without_the_condition_the_loft_turns_32_5_degrees():
    doc, pid = _lofted(cond=False)
    angles = _start_angles(_loft_part(doc.state, pid).shape, _wall_of(doc.state, pid))
    assert all(abs(a - 32.5) < 1.0 for a in angles), angles
    assert abs(np.mean(angles) - NO_CONDITION) < 0.05, angles


def test_empty_adjacent_faces_take_the_cone_wall():
    picked, pid = _lofted(["wall"])
    empty, pid2 = _lofted(None)
    a, b = _loft_part(picked.state, pid).shape, _loft_part(empty.state, pid2).shape
    assert max(_start_angles(b, _wall_of(empty.state, pid2))) < 0.5
    assert len(K.faces(a)) == len(K.faces(b))
    assert abs(_volume(a) - _volume(b)) <= 1e-9 * _volume(a)
    assert np.allclose(K.bbox(a)[0], K.bbox(b)[0], atol=1e-9) and np.allclose(K.bbox(a)[1], K.bbox(b)[1], atol=1e-9)


def test_a_face_that_does_not_touch_the_profile_is_refused():
    with pytest.raises(AssertionError, match=TOUCH):
        _lofted(["bottom"])
    with pytest.raises(AssertionError, match=TOUCH):
        _lofted(["wall", "bottom"])


def test_face_names_equal_those_of_the_loft_with_no_condition():
    def names(doc, pid):
        part = _loft_part(doc.state, pid)
        named = assert_all_named(doc.state, part)
        return {named.name_of(f) for f in K.faces(part.shape)}
    plain, cond = names(*_lofted(cond=False)), names(*_lofted(["wall"]))
    assert len(plain) == 3 and cond == plain


# --------------------------------------------------------------------------- kernel

def _frustum(r0=20.0, r1=15.0, hole=None):
    body = K.cone((0, 0, 0), (0, 0, 1), r0, r1, 20.0)
    if hole:
        body = K.cut(body, K.cylinder((0, 0, -1), (0, 0, 1), hole, 22.0))
    top = next(f for f in K.faces(body) if BRepAdaptor_Surface(f).GetType() == GeomAbs_Plane
               and abs(K.face_info(f)["c"][2] - 20) < 1e-9)
    wall = next(f for f in K.faces(body) if BRepAdaptor_Surface(f).GetType() == GeomAbs_Cone)
    return body, top, wall


def test_kernel_match_tangent_at_both_ends():
    _body, top, wall = _frustum()
    upper = _frustum()[0]
    from OCP.gp import gp_Trsf, gp_Ax1, gp_Dir
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    t = gp_Trsf()                                      # a second frustum upside down above the first, top at z 50
    t.SetRotation(gp_Ax1(gp_Pnt(0, 0, 35), gp_Dir(1, 0, 0)), math.pi)
    moved = BRepBuilderAPI_Transform(upper, t, True).Shape()
    end = next(f for f in K.faces(moved) if BRepAdaptor_Surface(f).GetType() == GeomAbs_Plane
               and abs(K.face_info(f)["c"][2] - 50) < 1e-9)
    end_wall = next(f for f in K.faces(moved) if BRepAdaptor_Surface(f).GetType() == GeomAbs_Cone)
    c0 = {"cond": "match_tangent", "mag": 1.0, "faces": [wall]}
    c1 = {"cond": "match_tangent", "mag": 1.0, "faces": [end_wall]}
    body = K.loft_skin([top, end], c0, c1)
    assert len(K.solids(body)) == 1 and K.is_valid(body)
    side = next(f for f in K.faces(body) if BRepAdaptor_Surface(f).GetType() != GeomAbs_Plane)
    for z, w in ((20.0, wall), (50.0, end_wall)):
        for k in range(16):
            a = 2 * math.pi * (k + 0.5) / 16
            p = (15 * math.cos(a), 15 * math.sin(a), z)
            c = abs(float(_normal(side, p) @ _normal(w, p)))
            assert math.degrees(math.acos(min(1.0, c))) < 0.5, (z, k)


def test_kernel_square_profile_matches_its_four_walls():
    """A pyramid frustum's top face: four adjacent faces, one per profile edge, and corners in between."""
    from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
    from test_loft_end_conditions import _square
    mk = BRepOffsetAPI_ThruSections(True, True, 1e-6)
    from OCP.BRepTools import BRepTools
    for f in (_square(0, 0, 30.0), _square(0, 15, 20.0)):
        mk.AddWire(BRepTools.OuterWire_s(f))
    mk.Build()
    frustum = mk.Shape()
    top = next(f for f in K.faces(frustum) if abs(K.face_info(f).get("c", (0, 0, 0))[2] - 15) < 1e-9
               and BRepAdaptor_Surface(f).GetType() == GeomAbs_Plane)
    walls = [f for f in K.faces(frustum) if not f.IsSame(top)
             and abs(abs(K.face_info(f).get("n", (0, 0, 0))[2]) - 1) > 1e-6]
    assert len(walls) == 4
    body = K.loft_skin([top, _square(0, 45, 30.0)], {"cond": "match_tangent", "mag": 1.0, "faces": walls}, None)
    assert len(K.solids(body)) == 1 and K.is_valid(body) and len(K.faces(body)) == 6
    sides = [f for f in K.faces(body) if abs(abs(K.face_info(f).get("n", (0, 0, 0))[2]) - 1) > 1e-6
             or BRepAdaptor_Surface(f).GetType() != GeomAbs_Plane]
    worst = 0.0
    for w in walls:
        c = np.asarray(K.face_info(w)["c"], float)
        out = np.array([c[0], c[1], 0.0]) / np.linalg.norm(c[:2])
        along = np.cross(out, (0, 0, 1))
        for s in np.linspace(-0.4, 0.4, 5):             # points on the top edge this wall shares
            p = tuple(out * 10.0 + along * s * 20.0 + np.array([0, 0, 15.0]))
            face = min(sides, key=lambda f: K._point_face_distance(p, f))
            n = abs(float(_normal(face, p) @ _normal(w, p)))
            worst = max(worst, math.degrees(math.acos(min(1.0, n))))
    assert worst < 0.5, worst


def test_kernel_holes_take_no_condition_under_match_tangent():
    _body, top, wall = _frustum(hole=5.0)
    end = _disc(50, 25, [(0, 0, 5)])
    c = {"cond": "match_tangent", "mag": 1.0, "faces": [wall]}
    body = K.loft_skin([top, end], c, None)
    outer = K.loft_skin([_frustum()[1], _disc(50, 25)], {"cond": "match_tangent", "mag": 1.0,
                                                            "faces": [_frustum()[2]]}, None)
    inner = K.loft([_disc(20, 5), _disc(50, 5)])
    assert len(K.solids(body)) == 1 and K.is_valid(body)
    assert abs(_volume(body) - (_volume(outer) - _volume(inner))) <= 1e-6 * _volume(body)


def test_kernel_refusals():
    _b, top, wall = _frustum()
    _b2, _top2, far = _frustum(30.0, 28.0)
    with pytest.raises(K.KernelError, match=TOUCH):
        K.loft_skin([top, _disc(50, 25)], {"cond": "match_tangent", "faces": [far]}, None)
    with pytest.raises(K.KernelError, match="Match tangent needs the adjacent faces"):
        K.loft_skin([top, _disc(50, 25)], {"cond": "match_tangent", "faces": []}, None)
    assert K.LOFT_MATCH_ERR == "Could not match the adjacent faces smoothly"


# --------------------------------------------------------------------------- dialog

def test_dialog_choice_field_and_round_trip():
    from freeshape.ui import dialogs as D
    assert ("match_tangent", "Match tangent") in D.LOFT_END_CHOICES
    specs = {s["key"]: s for s in D.SCHEMAS["loft"]}
    for end in ("start", "end"):
        sp = specs[f"{end}_faces"]
        assert sp["label"] == "Adjacent faces" and sp["want"] == {"face"}
        assert sp["visible"]({f"{end}_cond": "match_tangent"})
        assert not sp["visible"]({f"{end}_cond": "tangent"}) and not sp["visible"]({f"{end}_cond": ""})
        assert not sp["visible"]({f"{end}_cond": "match_tangent", "ruled": True})
        assert not sp["enabled"]({"guides_on": True}) and "cannot be combined" in sp["why"]
        assert specs[f"{end}_cond"]["options"] == D.LOFT_END_CHOICES
    assert not specs["guides_on"]["enabled"]({"start_cond": "match_tangent"})
    ref = {"part": "p1", "name": "F1"}
    old = {"profiles": [], "ruled": False, "op": "new", "scope": [],
           "start": {"cond": "match_tangent", "mag": 2.0, "faces": [ref]},
           "end": {"cond": "match_tangent", "mag": 1.0}}
    p = {**old, "start": dict(old["start"]), "end": dict(old["end"])}
    D._loft_unfold(p)
    fields = D.params_to_fields("loft", p)
    assert fields["start_faces"] == [("face", ref)] and fields["end_faces"] == []
    D.fields_to_params("loft", fields, p)
    D._loft_fold(p, strip=True)
    assert p == old                                    # empty Adjacent faces: no faces key
    p = dict(old, start={"cond": "tangent", "mag": 1.0, "faces": [ref]})
    D._loft_unfold(p)
    D.fields_to_params("loft", D.params_to_fields("loft", p), p)
    D._loft_fold(p, strip=True)
    assert p["start"] == {"cond": "tangent", "mag": 1.0}           # faces belong to Match tangent only


def test_guides_and_match_tangent_are_refused_together():
    doc, pid, cid = _base_doc()
    p = _loft_params(doc, pid, cid, {"cond": "match_tangent", "mag": 1.0})
    p["guides"] = [[{"kind": "sketch_chain", "sketch": cid, "entity": "nope"}]]
    with pytest.raises(AssertionError, match="Guides and end conditions cannot be combined yet"):
        add(doc, "loft", **p)


# --------------------------------------------------------------------------- engine mode

def test_engine_mode_dialog_builds_match_tangent_in_the_worker(tmp_path):
    """The real app (MainWindow(engine=True)): the Loft dialog with Match tangent, first with Adjacent faces
    left empty, then with the cone wall picked, then with a face off the profile. Each commit is built in the
    worker, which resolves the face references there: the angle holds, and the far face is refused by name."""
    from PySide6.QtWidgets import QApplication
    from freeshape.engine import process as P
    from freeshape.ui import mainwindow as mw_mod
    from test_engine_picking import _Settings, _spawned
    os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")
    os.environ.setdefault("FREESHAPE_NO_HOME", "1")
    os.environ["FREESHAPE_DATA_DIR"] = str(tmp_path / "appdata")
    doc, pid, cid = _base_doc()
    refs = _refs(doc, pid)
    region = region_refs(doc, cid)[0]
    path = str(tmp_path / "match.fshape")
    doc.save(path)
    app = QApplication.instance() or QApplication([])
    before = set(_spawned())
    w = mw_mod.MainWindow(engine=True)
    w.settings = _Settings()
    try:
        w.open_any(path)
        eng = w.studio_engine
        assert eng.settle(180)
        assert eng.attached(w.doc) and not w.doc.errors, w.doc.errors

        def commit(picks, replace=None):
            w.start_feature("loft") if replace is None else w.edit_feature(replace)
            d = w.dialog
            assert w.mode == "dialog" and d.ftype == "loft"
            if replace is None:
                d.fields["profiles"].toggle(("face", refs["top"]))
                d.fields["profiles"].toggle(("region", region))
                d._set("op", "new")
                assert not d.fields["start_faces"].isVisibleTo(d)
                d._set("start_cond", "match_tangent")
            assert d.fields["start_faces"].isVisibleTo(d)
            for it in list(d.fields["start_faces"].items):
                d.fields["start_faces"].toggle(it)
            for k in picks:
                d.fields["start_faces"].toggle(("face", refs[k]))
            d.accept()
            assert eng.settle(120)
            app.processEvents()
            return next(f for f in w.doc.features if f["type"] == "loft")

        loft = commit([])
        assert loft["params"]["start"] == {"cond": "match_tangent", "mag": 1.0}
        assert not w.doc.errors, w.doc.errors
        st = w.doc.state
        empty_angles = _start_angles(_loft_part(st, pid).shape, _wall_of(st, pid))
        assert max(empty_angles) < 0.5, empty_angles

        loft = commit(["wall"], loft["id"])
        assert loft["params"]["start"]["faces"] == [refs["wall"]] and refs["wall"].get("name")
        assert not w.doc.errors, w.doc.errors
        st = w.doc.state
        angles = _start_angles(_loft_part(st, pid).shape, _wall_of(st, pid))
        assert max(angles) < 0.5, angles

        loft = commit(["bottom"], loft["id"])
        assert w.doc.errors.get(loft["id"]) == TOUCH, w.doc.errors
    finally:
        w.studio_engine.shutdown()
        P.SPARES.clear()
        leftover = sorted(set(_spawned()) - before)
        for p in leftover:
            try:
                os.kill(p, signal.SIGKILL)
            except OSError:
                pass
        assert not leftover, f"worker processes outlived the window: {leftover}"
