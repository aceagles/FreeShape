"""Closed loft and a point as the first or last profile (I7.7). Both use the legacy maker
(BRepOffsetAPI_ThruSections): a closed loft adds the first profile again as the last and has no caps; a
point profile (a model vertex or a sketch point, `profiles[].kind` vertex or sketch) goes in with
AddVertex and has no cap at its end. Neither combines with end conditions or guides."""
import copy
import math
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from OCP.BRepAdaptor import BRepAdaptor_Surface  # noqa: E402
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeWire  # noqa: E402
from OCP.GeomAbs import GeomAbs_Plane  # noqa: E402
from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt  # noqa: E402

from conftest import FRONT, RIGHT, TOP, add, only_part, region_refs  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import sketchref  # noqa: E402
from freeshape.document import PartStudioDoc  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402
from test_naming_core_features import add_sketch, assert_all_named, widen  # noqa: E402
from test_naming_sweep_loft_import import add_sketch_on  # noqa: E402

R, r = 20.0, 3.0


def _ring_circle(theta):
    """A disc of radius r centred on the ring of radius R, in the plane through the ring's axis (z)."""
    c = gp_Pnt(R * math.cos(theta), R * math.sin(theta), 0)
    tangent = gp_Dir(-math.sin(theta), math.cos(theta), 0)
    edge = BRepBuilderAPI_MakeEdge(gp_Circ(gp_Ax2(c, tangent), r)).Edge()
    return BRepBuilderAPI_MakeFace(BRepBuilderAPI_MakeWire(edge).Wire()).Face()


def _ring():
    return [_ring_circle(k * math.pi / 2) for k in range(4)]


def _square(z=0.0, s=20.0):
    p = BRepBuilderAPI_MakePolygon()
    for x, y in ((-s / 2, -s / 2), (s / 2, -s / 2), (s / 2, s / 2), (-s / 2, s / 2)):
        p.Add(gp_Pnt(x, y, z))
    p.Close()
    return BRepBuilderAPI_MakeFace(p.Wire(), True).Face()


def _planar_caps(shape):
    return [f for f in K.faces(shape) if BRepAdaptor_Surface(f).GetType() == GeomAbs_Plane]


def _close(a, b, rel):
    assert abs(a - b) <= rel * max(abs(a), abs(b)), (a, b)


# --------------------------------------------------------------------------- kernel

def test_four_circles_closed_make_a_ring_without_caps():
    body = K.loft(_ring(), closed=True)
    assert len(K.solids(body)) == 1 and K.is_valid(body)
    assert _planar_caps(body) == []
    _close(K.volume(body), 2 * math.pi ** 2 * R * r * r, 0.02)


def test_closed_without_the_flag_keeps_its_caps():
    body = K.loft(_ring()[:3])
    assert len(_planar_caps(body)) == 2


@pytest.mark.parametrize("last", [True, False])
def test_a_square_ruled_to_a_point_is_a_pyramid(last):
    point = K.make_vertex((0, 0, 30))
    profs = [_square(), point] if last else [point, _square()]
    body = K.loft(profs, ruled=True)
    assert len(K.solids(body)) == 1 and K.is_valid(body)
    _close(K.volume(body), 20 * 20 * 30 / 3, 1e-6)
    assert len(K.faces(body)) == 5 and len(_planar_caps(body)) == 5
    assert sum(1 for f in K.faces(body) if abs(abs(K.face_info(f)["n"][2]) - 1) < 1e-9) == 1      # one cap


def test_a_smooth_loft_to_a_point_is_valid():
    body = K.loft([_square(), _square(15, 10), K.make_vertex((0, 0, 30))])
    assert len(K.solids(body)) == 1 and K.is_valid(body) and K.volume(body) > 0


def test_kernel_refusals():
    with pytest.raises(K.KernelError, match="at least three"):
        K.loft(_ring()[:2], closed=True)
    v = K.make_vertex((0, 0, 15))
    with pytest.raises(K.KernelError, match="first or the last"):
        K.loft([_square(), v, _square(30)])
    with pytest.raises(K.KernelError, match="not a point"):
        K.loft([v, K.make_vertex((0, 0, 3))])
    with pytest.raises(K.KernelError, match="point profile"):
        K.loft(_ring()[:3] + [v], closed=True)


# --------------------------------------------------------------------------- document

def _sketch_pt(doc, z):
    """A sketch of one point at (0, 0) on a plane z above Top; the loft's `sketch` profile reference."""
    pl = add(doc, "plane", type="offset", refs=[], ref=TOP, offset=z)
    s = Sketch()
    pid = s.add_point(0, 0)
    s.solve()
    f = doc.make_feature("sketch")
    f["params"].update(plane={"kind": "plane", "fid": pl["id"]}, sketch=s.to_dict())
    doc.add_feature(f)
    assert not doc.errors.get(f["id"]), doc.errors
    d = sketchref.describe(s.to_dict(), pid, "point")
    return {"sketch": f["id"], "entity": pid, "role": "point", "etype": d["etype"], "sig": d["sig"]}


def _pyramid(point_first=False, **extra):
    doc = PartStudioDoc()
    a, ids = add_sketch(doc, -10, -10, 20, 20)
    ref = _sketch_pt(doc, 30.0)
    profs = [{"kind": "region", "ref": region_refs(doc, a)[0]}, {"kind": "sketch", "ref": ref}]
    add(doc, "loft", op="new", ruled=True, profiles=profs[::-1] if point_first else profs, **extra)
    return doc, a, ids


def test_a_sketch_point_ends_a_loft():
    doc, _, _ = _pyramid()
    body = only_part(doc).shape
    _close(K.volume(body), 4000, 1e-6)
    assert len(K.faces(body)) == 5


def test_a_model_vertex_ends_a_loft():
    doc = PartStudioDoc()
    a, _ = add_sketch(doc, -10, -10, 20, 20)
    add(doc, "extrude", regions=region_refs(doc, a), depth=10, op="new")
    part = only_part(doc)
    top = next(i for i, v in enumerate(K.vertex_points(part.shape)) if abs(v[2] - 10) < 1e-6 and v[0] > 0 and v[1] > 0)
    ref = F.make_vertex_ref(part.id, K.vertex_points(part.shape)[top], part.shape, doc.state.named(part.id))
    b, _ = add_sketch_on(doc, TOP, 30, 30, 10, 10)
    add(doc, "loft", op="new", ruled=True, profiles=[{"kind": "region", "ref": region_refs(doc, b)[0]},
                                                      {"kind": "vertex", "ref": ref}])
    vols = sorted(K.volume(p.shape) for p in doc.state.parts.values())
    assert len(vols) == 2 and abs(vols[0] - 100 * 10 / 3) < 1e-4, vols      # apex 10 above the 10 x 10 square


def _ring_doc(**extra):
    """Four 6 x 6 squares on a ring of radius 20, each in a plane through the ring axis, in ring order."""
    doc = PartStudioDoc()
    secs = []
    for plane, x in ((FRONT, 17), (RIGHT, 17), (FRONT, -23), (RIGHT, -23)):
        secs.append(add_sketch_on(doc, plane, x, -3, 6, 6))
    refs = []
    for sid, _ in secs:
        ref = region_refs(doc, sid)[0]
        c = K.face_info(doc.state.sketches[sid].regions[0])["c"]
        refs.append((math.atan2(c[1], c[0]) % (2 * math.pi), ref))
    refs.sort(key=lambda t: t[0])
    return doc, secs, [{"kind": "region", "ref": ref} for _, ref in refs]


def _loft(doc, profiles, **p):
    f = doc.make_feature("loft")
    f["params"].update(op="new", profiles=profiles, **p)
    doc.add_feature(f)
    return f, doc.errors.get(f["id"])


def test_closed_loft_names_every_face_and_survives_a_profile_change():
    doc, secs, profiles = _ring_doc()
    f, err = _loft(doc, profiles, closed=True)
    assert not err, err
    part = only_part(doc)
    assert K.is_valid(part.shape) and len(K.solids(part.shape)) == 1 and _planar_caps(part.shape) == []
    named = assert_all_named(doc.state, part)
    before = {named.name_of(fc): K.face_info(fc)["c"] for fc in K.faces(part.shape)}
    assert len(before) == len(K.faces(part.shape))
    sid, ids = secs[0]
    widen(doc, sid, ids["width"], 8.0)
    part2 = only_part(doc)
    named2 = assert_all_named(doc.state, part2)
    assert set(before) == {named2.name_of(fc) for fc in K.faces(part2.shape)}


@pytest.mark.parametrize("point_first", [False, True])
def test_pyramid_names_every_face_and_survives_a_profile_change(point_first):
    doc, sid, ids = _pyramid(point_first)
    named = assert_all_named(doc.state, only_part(doc))
    names = {named.name_of(fc) for fc in K.faces(only_part(doc).shape)}
    assert len(names) == 5
    widen(doc, sid, ids["width"], 30.0)
    part = only_part(doc)
    named2 = assert_all_named(doc.state, part)
    assert names == {named2.name_of(fc) for fc in K.faces(part.shape)}
    _close(K.volume(part.shape), 30 * 20 * 30 / 3, 1e-6)


def test_refusals():
    doc, secs, profiles = _ring_doc()
    _, err = _loft(doc, profiles[:2], closed=True)
    assert err and "three" in err
    ref = _sketch_pt(doc, 30.0)
    mid = [profiles[0], {"kind": "sketch", "ref": ref}, profiles[1]]
    _, err = _loft(doc, mid, ruled=True)
    assert err and "first or the last" in err
    cond = {"cond": "normal", "mag": 1.0}
    for extra in ({"start": cond}, {"end": cond}, {"guides": [[{"kind": "sketch_chain", "sketch": "x", "entity": "y"}]]}):
        _, err = _loft(doc, profiles, closed=True, **extra)
        assert err and "cannot be combined" in err, (extra, err)
        _, err = _loft(doc, [profiles[0], {"kind": "sketch", "ref": ref}], **extra)
        assert err and "cannot be combined" in err, (extra, err)


def test_an_old_loft_takes_todays_path_and_equals_the_legacy_kernel_result():
    doc = PartStudioDoc()
    a, _ = add_sketch(doc, -10, -10, 20, 20)
    pl = add(doc, "plane", type="offset", refs=[], ref=TOP, offset=30.0)
    b, _ = add_sketch_on(doc, {"kind": "plane", "fid": pl["id"]}, -5, -5, 10, 10)
    f = add(doc, "loft", op="new", profiles=[{"kind": "region", "ref": region_refs(doc, a)[0]},
                                             {"kind": "region", "ref": region_refs(doc, b)[0]}])
    assert "closed" not in f["params"]
    want = K.loft([doc.state.sketches[a].regions[0], doc.state.sketches[b].regions[0]], False)
    got = only_part(doc).shape
    assert len(K.faces(got)) == len(K.faces(want)) == 6
    _close(K.volume(got), K.volume(want), 1e-12)
    assert sorted(round(x, 6) for x in (K.face_info(fc)["area"] for fc in K.faces(got))) == \
        sorted(round(x, 6) for x in (K.face_info(fc)["area"] for fc in K.faces(want)))


# --------------------------------------------------------------------------- dialog (offscreen window)

def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _row(d, key):
    return next(w for spec, w in d.rows if spec["key"] == key)


def _square_and_point_doc():
    doc = PartStudioDoc()
    a, _ = add_sketch(doc, -10, -10, 20, 20)
    ref = _sketch_pt(doc, 30.0)
    return doc, ("region", region_refs(doc, a)[0]), ref


def test_closed_tick_shows_with_three_profiles_and_greys_conditions_and_guides(tmp_path, monkeypatch):
    app = _qapp()
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    doc, secs, profiles = _ring_doc()
    w._add_tab(doc)
    w.resize(900, 700)
    w.show()
    app.processEvents()
    try:
        w.start_feature("loft")
        d = w.dialog
        tick = _row(d, "closed")
        items = [(p["kind"], p["ref"]) for p in profiles]
        d.fields["profiles"].toggle(items[0])
        d.fields["profiles"].toggle(items[1])
        assert not tick.isVisibleTo(d)
        d.fields["profiles"].toggle(items[2])
        assert tick.isVisibleTo(d) and tick.isEnabled()
        d._set("closed", True)
        for key in ("start_cond", "end_cond", "guides_on"):
            assert not _row(d, key).isEnabled() and "cannot be combined" in _row(d, key).toolTip(), key
        d._set("closed", False)
        assert _row(d, "start_cond").isEnabled() and "closed" not in d.feature["params"]
        d._set("start_cond", "normal")
        assert not tick.isEnabled() and "cannot be combined" in tick.toolTip()
        d._set("start_cond", "")
        d._set("closed", True)
        d.fields["profiles"].toggle(items[3])
        d.accept()
        loft = next(f for f in w.doc.features if f["type"] == "loft")
        assert loft["params"]["closed"] is True and "start" not in loft["params"] and "guides" not in loft["params"]
        assert not w.doc.errors, w.doc.errors
        assert _planar_caps(only_part(w.doc).shape) == []
    finally:
        w.deleteLater()


def test_a_point_pick_greys_conditions_and_a_plain_loft_saves_no_new_key(tmp_path, monkeypatch):
    from freeshape.ui.viewport import Pick
    app = _qapp()
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    doc, face, ref = _square_and_point_doc()
    w._add_tab(doc)
    w.resize(900, 700)
    w.show()
    app.processEvents()
    try:
        w.start_feature("loft")
        d = w.dialog
        d.fields["profiles"].toggle(face)
        d.handle_pick(Pick("sketch_point", (ref["sketch"], ref["entity"])))
        assert [it["kind"] for it in d.feature["params"]["profiles"]] == ["region", "sketch"]
        assert not _row(d, "start_cond").isEnabled() and not _row(d, "guides_on").isEnabled()
        d._set("ruled", True)
        d.accept()
        loft = next(f for f in w.doc.features if f["type"] == "loft")
        assert "closed" not in loft["params"] and not w.doc.errors, w.doc.errors
        _close(K.volume(only_part(w.doc).shape), 4000, 1e-6)
    finally:
        w.deleteLater()


def _engine_window(tmp_path, doc, name):
    from freeshape.ui import mainwindow as mw_mod
    from test_engine_picking import _Settings, _spawned
    os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")
    os.environ.setdefault("FREESHAPE_NO_HOME", "1")
    os.environ["FREESHAPE_DATA_DIR"] = str(tmp_path / "appdata")
    path = str(tmp_path / name)
    doc.save(path)
    app = _qapp()
    before = set(_spawned())
    w = mw_mod.MainWindow(engine=True)
    w.settings = _Settings()
    w.open_any(path)
    assert w.studio_engine.settle(180)
    return app, w, before


def _engine_cleanup(w, before):
    from freeshape.engine import process as P
    from test_engine_picking import _spawned
    w.studio_engine.shutdown()
    P.SPARES.clear()
    leftover = sorted(set(_spawned()) - before)
    import signal
    for p in leftover:
        try:
            os.kill(p, signal.SIGKILL)
        except OSError:
            pass
    assert not leftover, f"worker processes outlived the window: {leftover}"


def test_engine_mode_builds_a_loft_to_a_sketch_point_in_the_worker(tmp_path):
    """MainWindow(engine=True): the dialog's sketch-point pick resolves in the worker and the pyramid builds there."""
    from freeshape.ui.viewport import Pick
    doc, face, ref = _square_and_point_doc()
    app, w, before = _engine_window(tmp_path, doc, "point.fshape")
    try:
        eng = w.studio_engine
        assert eng.attached(w.doc) and not w.doc.errors, w.doc.errors
        w.start_feature("loft")
        d = w.dialog
        d.fields["profiles"].toggle(face)
        d.handle_pick(Pick("sketch_point", (ref["sketch"], ref["entity"])))
        d._set("ruled", True)
        d.accept()
        assert eng.settle(120)
        app.processEvents()
        d1 = w.doc
        loft = next(f for f in d1.features if f["type"] == "loft")
        assert loft["params"]["profiles"][1]["kind"] == "sketch"
        assert not d1.errors, d1.errors
        body = only_part(d1).shape
        assert K.is_valid(body) and len(K.faces(body)) == 5
        _close(K.volume(body), 4000, 1e-6)
    finally:
        _engine_cleanup(w, before)


def test_engine_mode_builds_a_closed_loft_in_the_worker(tmp_path):
    """MainWindow(engine=True): ticking Closed in the dialog and committing builds a cap-free solid in the worker."""
    doc, secs, profiles = _ring_doc()
    app, w, before = _engine_window(tmp_path, doc, "closed.fshape")
    try:
        eng = w.studio_engine
        assert eng.attached(w.doc) and not w.doc.errors, w.doc.errors
        w.start_feature("loft")
        d = w.dialog
        for p in profiles:
            d.fields["profiles"].toggle((p["kind"], p["ref"]))
        d._set("closed", True)
        d.accept()
        assert eng.settle(120)
        app.processEvents()
        d1 = w.doc
        loft = next(f for f in d1.features if f["type"] == "loft")
        assert loft["params"]["closed"] is True
        assert not d1.errors, d1.errors
        body = only_part(d1).shape
        assert K.is_valid(body) and len(K.solids(body)) == 1 and _planar_caps(body) == []
    finally:
        _engine_cleanup(w, before)
