"""I2.7: Revolve and Sweep with Create = Surface. Sketch curves are revolved or swept into a surface body (a
shell with a face per curve, no solid). Remove, Intersect and the up-to ends are refused. Also the solid sweep
along a tangent arc-line-arc path, which had no test."""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

from conftest import FRONT, TOP, add, rect_lines, region_refs, sketch_feature, try_add  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.document import PartStudioDoc  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402

AXIS_X = {"kind": "std", "name": "X"}


def curve_refs(sid, *entities):
    return [{"kind": "sketch_chain", "sketch": sid, "entity": e} for e in entities]


def ents(doc, sid, kind):
    sk = doc.state.sketches[sid].sketch
    return [i for i, e in sk.entities.items() if e["type"] == kind]


def only(doc):
    (p,) = doc.state.parts.values()
    return p


def area(shape):
    return K.mass_props(shape)["area"]


def face_names(doc, pid):
    n = doc.state.named(pid)
    assert n is not None
    return sorted(n.names_of("F"))


def line_at_radius(s, r=10.0, length=20.0):
    return s.add_line(s.add_point(0, r), s.add_point(length, r))


def polyline(s, pts):
    ids = [s.add_point(*q) for q in pts]
    return [s.add_line(a, b) for a, b in zip(ids, ids[1:])]


def arc_off_axis(s):
    """A quarter arc about (10, 20), radius 5, clear of the X axis."""
    return s.add_arc(s.add_point(10, 20), s.add_point(15, 20), s.add_point(10, 25))


def surface_revolve(build, **kw):
    doc = PartStudioDoc()
    sid = sketch_feature(doc, build, TOP)
    kind = ents(doc, sid, "line") or ents(doc, sid, "arc")
    f, err = try_add(doc, "revolve", creation="surface", curves=curve_refs(sid, kind[0]), axis=AXIS_X,
                     op=kw.pop("op", "new"), **kw)
    return doc, sid, f, err


# ---------------------------------------------------------------------------- revolve

def test_an_open_arc_revolved_through_360_is_one_face_closed_in_u_and_no_solid():
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    doc, _, f, err = surface_revolve(arc_off_axis, end="full")
    assert not err, err
    p = only(doc)
    assert p.name == "Surface 1"
    assert K.is_surface(p.shape) and not K.solids(p.shape) and K.volume(p.shape) == 0.0
    (face,) = K.faces(p.shape)
    assert BRepAdaptor_Surface(face).IsUClosed()
    area_exp = 2 * math.pi * (5 * math.pi / 2) * (20 + 10 / math.pi)     # Pappus: arc length x centroid path; the centroid is 2r/pi above the centre
    assert area(p.shape) == pytest.approx(area_exp, rel=1e-4)


def test_a_line_parallel_to_the_axis_revolved_180_is_half_a_cylinder():
    doc, _, f, err = surface_revolve(line_at_radius, end="one", angle=180)
    assert not err, err
    p = only(doc)
    assert K.is_surface(p.shape) and len(K.faces(p.shape)) == 1
    assert area(p.shape) == pytest.approx(math.pi * 10 * 20, rel=1e-4)


def test_symmetric_90_degrees_is_symmetric_about_the_sketch_plane():
    doc, _, f, err = surface_revolve(line_at_radius, end="symmetric", angle=90)
    assert not err, err
    lo, hi = K.bbox(only(doc).shape)
    assert lo[2] == pytest.approx(-hi[2], abs=1e-6) and hi[2] == pytest.approx(10 * math.sin(math.radians(45)), abs=1e-4)
    assert area(only(doc).shape) == pytest.approx(math.pi / 2 * 10 * 20, rel=1e-4)


def test_a_closed_circle_revolved_is_a_closed_torus_shell():
    doc = PartStudioDoc()
    sid = sketch_feature(doc, lambda s: s.add_circle(s.add_point(0, 20), 5), TOP)
    f, err = try_add(doc, "revolve", creation="surface", curves=curve_refs(sid, ents(doc, sid, "circle")[0]),
                     axis=AXIS_X, op="new", end="full")
    assert not err, err
    assert K.is_surface(only(doc).shape)
    assert area(only(doc).shape) == pytest.approx(4 * math.pi ** 2 * 20 * 5, rel=1e-4)


@pytest.mark.parametrize("op", ["remove", "intersect"])
@pytest.mark.parametrize("ftype", ["revolve", "sweep"])
def test_remove_and_intersect_are_refused(ftype, op):
    doc = PartStudioDoc()
    sid = sketch_feature(doc, line_at_radius, TOP)
    f, err = try_add(doc, ftype, creation="surface", curves=curve_refs(sid, ents(doc, sid, "line")[0]),
                     axis=AXIS_X, op=op)
    assert err and "Remove and Intersect are not available for surfaces" in err


@pytest.mark.parametrize("end", ["up_to_face", "up_to_vertex", "up_to_part", "up_to_next"])
def test_a_surface_revolve_refuses_an_up_to_end(end):
    doc, _, f, err = surface_revolve(line_at_radius, end=end)
    assert err and "Full, One direction or Symmetric" in err


@pytest.mark.parametrize("ftype", ["revolve", "sweep"])
def test_no_curves_is_an_error_for_a_surface_revolve_and_sweep(ftype):
    doc = PartStudioDoc()
    sid = sketch_feature(doc, line_at_radius, TOP)
    f, err = try_add(doc, ftype, creation="surface", curves=[], axis=AXIS_X, op="new")
    assert err and "Select sketch curves" in err


def test_the_axis_perpendicular_to_the_sketch_plane_is_refused():
    doc = PartStudioDoc()
    sid = sketch_feature(doc, line_at_radius, TOP)
    f, err = try_add(doc, "revolve", creation="surface", curves=curve_refs(sid, ents(doc, sid, "line")[0]),
                     axis={"kind": "std", "name": "Z"}, op="new")
    assert err and "perpendicular" in err


# ---------------------------------------------------------------------------- sweep

def l_path(s):
    a, b, c = s.add_point(0, 0), s.add_point(0, 40), s.add_point(30, 40)
    s.add_line(a, b)
    s.add_line(b, c)


def path_ref(doc, sid):
    return [{"kind": "sketch_chain", "sketch": sid, "entity": ents(doc, sid, "line")[0]}]


def test_an_open_arc_swept_along_an_l_path_is_one_shell_with_edges_times_segments_faces():
    doc = PartStudioDoc()
    prof = sketch_feature(doc, lambda s: s.add_arc(s.add_point(0, 0), s.add_point(3, 0), s.add_point(0, 3)), TOP)
    path = sketch_feature(doc, l_path, FRONT)
    f, err = try_add(doc, "sweep", creation="surface", curves=curve_refs(prof, ents(doc, prof, "arc")[0]),
                     path=path_ref(doc, path), op="new")
    assert not err, err
    p = only(doc)
    assert p.name == "Surface 1"
    assert K.is_surface(p.shape) and not K.solids(p.shape) and len(K.bodies(p.shape)) == 1
    assert len(K.faces(p.shape)) == 1 * 2
    assert area(p.shape) == pytest.approx((3 * math.pi / 2) * 70, rel=0.06)   # the mitred corner loses a little


def test_a_closed_profile_swept_as_a_surface_has_no_end_caps():
    doc = PartStudioDoc()
    prof = sketch_feature(doc, lambda s: s.add_circle(s.add_point(0, 0), 3), TOP)
    path = sketch_feature(doc, l_path, FRONT)
    f, err = try_add(doc, "sweep", creation="surface", curves=curve_refs(prof, ents(doc, prof, "circle")[0]),
                     path=path_ref(doc, path), op="new")
    assert not err, err
    p = only(doc)
    assert K.is_surface(p.shape) and len(K.faces(p.shape)) == 2
    assert area(p.shape) == pytest.approx(2 * math.pi * 3 * 70, rel=0.02)


def tangent_path(s):
    """Arc (down, a quarter turn about (10, 0)), a line of 20, an arc (up, about (30, 0)): tangent throughout."""
    p0, p1, p2, p3 = s.add_point(0, 0), s.add_point(10, -10), s.add_point(30, -10), s.add_point(40, 0)
    s.add_arc(s.add_point(10, 0), p0, p1)
    s.add_line(p1, p2)
    s.add_arc(s.add_point(30, 0), p2, p3)


def test_a_solid_sweep_along_a_tangent_arc_line_arc_path_has_volume_profile_area_times_length():
    doc = PartStudioDoc()
    prof = sketch_feature(doc, lambda s: rect_lines(s, -2, -2, 4, 4), TOP)
    path = sketch_feature(doc, tangent_path, FRONT)
    length = 5 * math.pi + 20 + 5 * math.pi
    ref = [{"kind": "sketch_chain", "sketch": path, "entity": ents(doc, path, "line")[0]}]
    f = add(doc, "sweep", regions=region_refs(doc, prof), path=ref, op="new")
    assert "creation" not in f["params"]
    p = only(doc)
    assert K.solids(p.shape)
    assert K.volume(p.shape) == pytest.approx(16 * length, rel=0.02)


def test_a_sweep_and_a_revolve_with_no_creation_key_build_as_before():
    def solid_pair(**extra):
        doc = PartStudioDoc()
        prof = sketch_feature(doc, lambda s: rect_lines(s, 10, -2, 4, 4), TOP)
        rev = add(doc, "revolve", regions=region_refs(doc, prof), axis={"kind": "std", "name": "Y"}, end="full",
                  op="new", **extra)
        sprof = sketch_feature(doc, lambda s: rect_lines(s, -2, -2, 4, 4), TOP)
        path = sketch_feature(doc, l_path, FRONT)
        swp = add(doc, "sweep", regions=region_refs(doc, sprof), path=path_ref(doc, path), op="new", **extra)
        return doc, rev, swp
    doc, rev, swp = solid_pair()
    assert "creation" not in rev["params"] and "curves" not in swp["params"]
    parts = list(doc.state.parts.values())
    assert len(parts) == 2 and all(p.name.startswith("Part") for p in parts)
    vols = [K.volume(p.shape) for p in parts]
    doc2, _, _ = solid_pair(creation="solid")
    assert [K.volume(p.shape) for p in doc2.state.parts.values()] == pytest.approx(vols)
    assert [face_names(doc2, p.id) for p in doc2.state.parts.values()] == [face_names(doc, p.id) for p in parts]
    assert vols[0] == pytest.approx(2 * math.pi * 12 * 16, rel=1e-3)


# ---------------------------------------------------------------------------- names

def _move_point(doc, sid, pid, xy):
    f = next(x for x in doc.features if x["id"] == sid)
    f["params"]["sketch"]["points"][pid] = list(xy)
    doc.replace_feature(f)
    assert not doc.errors.get(sid)


def test_face_names_of_a_surface_revolve_survive_a_sketch_change():
    doc = PartStudioDoc()
    sid = sketch_feature(doc, lambda s: polyline(s, [(0, 10), (20, 10), (20, 15)]), TOP)
    f, err = try_add(doc, "revolve", creation="surface", curves=curve_refs(sid, ents(doc, sid, "line")[0]),
                     axis=AXIS_X, op="new", end="one", angle=120)
    assert not err, err
    before = face_names(doc, only(doc).id)
    assert len(before) == 2 and len(set(before)) == 2
    sk = doc.state.sketches[sid].sketch
    far = next(i for i, e in sk.entities.items() if e["type"] == "line" and sk.points[e["p2"]] == [20.0, 10.0])
    _move_point(doc, sid, sk.entities[far]["p2"], (25, 12))
    assert face_names(doc, only(doc).id) == before


def test_face_names_of_a_surface_sweep_survive_a_sketch_change():
    doc = PartStudioDoc()
    prof = sketch_feature(doc, lambda s: polyline(s, [(0, 0), (3, 0), (3, 3)]), TOP)
    path = sketch_feature(doc, l_path, FRONT)
    f, err = try_add(doc, "sweep", creation="surface", curves=curve_refs(prof, ents(doc, prof, "line")[0]),
                     path=path_ref(doc, path), op="new")
    assert not err, err
    before = face_names(doc, only(doc).id)
    assert len(before) == 4 and len(set(before)) == 4
    sk = doc.state.sketches[prof].sketch
    tip = next(e["p2"] for e in sk.entities.values() if e["type"] == "line" and sk.points[e["p2"]] == [3.0, 3.0])
    _move_point(doc, prof, tip, (4, 4))
    assert face_names(doc, only(doc).id) == before


# ---------------------------------------------------------------------------- the dialog

@pytest.fixture
def make_win(tmp_path, monkeypatch):
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    made = []

    def make(doc):
        w = MainWindow()
        made.append(w)
        w._add_tab(doc)
        w.resize(900, 700)
        w.show()
        app.processEvents()
        assert w.doc is doc
        return w

    yield make
    for w in made:
        w.deleteLater()


def _segment_buttons(dlg, key):
    from PySide6.QtWidgets import QToolButton
    for spec, w in dlg.rows:
        if spec["t"] == "ops" and spec["key"] == key and w.isVisibleTo(dlg):
            return {b.text(): b for b in w.findChildren(QToolButton)}
    return {}


@pytest.mark.parametrize("ftype", ["revolve", "sweep"])
def test_dialog_surface_swaps_the_profile_field_and_limits_the_operation(make_win, ftype):
    doc = PartStudioDoc()
    sid = sketch_feature(doc, line_at_radius, TOP)
    w = make_win(doc)
    w.start_feature(ftype)
    dlg = w.dialog
    assert list(_segment_buttons(dlg, "creation")) == ["Solid", "Surface", "Thin"]
    assert list(_segment_buttons(dlg, "op")) == ["New", "Add", "Remove", "Intersect"]
    dlg._set("op", "remove")
    _segment_buttons(dlg, "creation")["Surface"].click()
    assert dlg.feature["params"]["creation"] == "surface" and dlg.feature["params"]["op"] == "add"
    assert list(_segment_buttons(dlg, "op")) == ["New", "Add"]
    assert not dlg.fields["profiles"].isVisibleTo(dlg) and dlg.fields["curves"].isVisibleTo(dlg)
    _segment_buttons(dlg, "creation")["Solid"].click()
    assert "curves" not in dlg.feature["params"] and dlg.fields["profiles"].isVisibleTo(dlg)


def test_dialog_solid_revolve_saves_no_creation_or_curves_key(make_win):
    doc = PartStudioDoc()
    sid = sketch_feature(doc, lambda s: rect_lines(s, 10, 0, 4, 4), TOP)
    w = make_win(doc)
    w.start_feature("revolve")
    dlg = w.dialog
    dlg.fields["profiles"].toggle(("region", region_refs(doc, sid)[0]))
    p = dlg.feature["params"]
    assert "curves" not in p and "creation" not in p


# ---------------------------------------------------------------------------- the engine wire

@pytest.fixture(scope="module")
def engine_win():
    os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")
    os.environ.setdefault("FREESHAPE_NO_HOME", "1")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow(engine=True)
    w.settings = type("S", (dict,), {"value": lambda s, k, d=None: s.get(k, d),
                                     "setValue": lambda s, k, v: s.__setitem__(k, v)})()
    assert w.studio_engine.settle(180)
    yield w
    w.studio_engine.shutdown()
    from freeshape.engine import process as P
    P.SPARES.clear()


def test_a_surface_revolve_and_sweep_made_in_the_engine_window_are_surface_parts(engine_win):
    w = engine_win
    se = w.studio_engine
    doc = w.doc
    sid = sketch_feature(doc, line_at_radius, TOP)
    prof = sketch_feature(doc, lambda s: s.add_arc(s.add_point(0, 0), s.add_point(3, 0), s.add_point(0, 3)), TOP)
    path = sketch_feature(doc, l_path, FRONT)
    assert se.settle(180)
    w.start_feature("revolve")
    dlg = w.dialog
    dlg._set("creation", "surface")
    dlg._set("op", "new")
    dlg.handle_pick(Pick("sketch_line", (sid, ents(doc, sid, "line")[0])))
    dlg._set("axis_std", "X")
    dlg._set("end", "one")
    dlg._set("angle", 180)
    w.commit_dialog()
    assert se.settle(180)
    assert not doc.errors, doc.errors
    (p,) = doc.state.parts.values()
    assert p.name == "Surface 1" and K.is_surface(p.shape)
    assert area(p.shape) == pytest.approx(math.pi * 10 * 20, rel=1e-4)
    w.start_feature("sweep")
    dlg = w.dialog
    dlg._set("creation", "surface")
    dlg._set("op", "new")
    dlg.handle_pick(Pick("sketch_line", (prof, ents(doc, prof, "arc")[0])))
    dlg.activate(dlg.fields["path"])
    dlg.handle_pick(Pick("sketch_line", (path, ents(doc, path, "line")[0])))
    w.commit_dialog()
    assert se.settle(180)
    assert not doc.errors, doc.errors
    parts = list(doc.state.parts.values())
    assert [q.name for q in parts] == ["Surface 1", "Surface 2"]
    assert all(K.is_surface(q.shape) for q in parts)
    assert len(K.faces(parts[1].shape)) == 2
