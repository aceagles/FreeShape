"""I2.9: Sweep with Create = Thin. A thin sweep turns sketch curves into a thin face in the profile's plane
(K.thin_profile, as Thin extrude and Thin revolve do) and sweeps that face with the solid sweep kernel.
Thickness 1 is on the right of the curve's travel (outside a closed curve), thickness 2 on the other side."""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from conftest import FRONT, TOP, add, rect_lines, sketch_feature, try_add  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.document import PartStudioDoc  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402

BEND = "The wall is thicker than the path's tightest bend allows"
LENGTH = 10 * math.pi + 20            # the tangent path: arc (quarter turn, radius 10), line of 20, arc back


def ents(doc, sid, kind):
    return [i for i, e in doc.state.sketches[sid].sketch.entities.items() if e["type"] == kind]


def chain(doc, sid, kind):
    return [{"kind": "sketch_chain", "sketch": sid, "entity": ents(doc, sid, kind)[0]}]


def tangent_path(s):
    """Arc down (a quarter turn about (10, 0)), a line of 20, an arc up (about (30, 0)): tangent throughout. It
    starts at the origin heading along the sketch's y axis, square to the Top plane."""
    p0, p1, p2, p3 = s.add_point(0, 0), s.add_point(10, -10), s.add_point(30, -10), s.add_point(40, 0)
    s.add_arc(s.add_point(10, 0), p0, p1)
    s.add_line(p1, p2)
    s.add_arc(s.add_point(30, 0), p2, p3)


def l_path(s):
    a, b, c = s.add_point(0, 0), s.add_point(0, 40), s.add_point(30, 40)
    s.add_line(a, b)
    s.add_line(b, c)


def tight_path(s):
    """A quarter arc of radius 4 about (4, 0), then a line."""
    p0, p1, p2 = s.add_point(0, 0), s.add_point(4, -4), s.add_point(14, -4)
    s.add_arc(s.add_point(4, 0), p0, p1)
    s.add_line(p1, p2)


SHALLOW = (15.0, 4.0)                 # chord 30, rise 4


def arc_geometry():
    """(radius, half angle, centre x) of the shallow arc. Its centroid is put on the path (x = 0), so the swept
    volume is the strip's area times the path's own length (Pappus)."""
    half, rise = SHALLOW
    r = (half ** 2 + rise ** 2) / (2 * rise)
    a = math.asin(half / r)
    return r, a, -r * math.sin(a) / a


def shallow_arc(s):
    """An arc along y (out of the bend plane of the path) with chord 30 and rise 4 towards +x, its centroid on
    the path."""
    r, a, cx = arc_geometry()
    s.add_arc(s.add_point(cx, 0), s.add_point(cx + r * math.cos(a), -r * math.sin(a)),
              s.add_point(cx + r * math.cos(a), r * math.sin(a)))


def arc_length():
    r, a, _ = arc_geometry()
    return 2 * r * a


def thin_sweep(prof_build, path_build, kind="arc", path_kind="line", doc=None, **kw):
    doc = doc or PartStudioDoc()
    prof = sketch_feature(doc, prof_build, TOP)
    path = sketch_feature(doc, path_build, FRONT)
    kw.setdefault("op", "new")
    f, err = try_add(doc, "sweep", creation="thin", curves=chain(doc, prof, kind), path=chain(doc, path, path_kind), **kw)
    return doc, prof, path, f, err


def only(doc):
    (p,) = doc.state.parts.values()
    return p


def face_names(doc, pid):
    n = doc.state.named(pid)
    assert n is not None
    return sorted(n.names_of("F"))


def test_a_shallow_arc_swept_thin_mid_plane_has_the_volume_of_its_mid_surface_strip():
    doc, _, _, f, err = thin_sweep(shallow_arc, tangent_path, thickness1=2, mid_plane=True)
    assert not err, err
    p = only(doc)
    assert len(K.solids(p.shape)) == 1 and K.is_valid(p.shape)
    assert K.volume(p.shape) == pytest.approx(arc_length() * 2 * LENGTH, rel=0.03)


def test_the_profile_curve_lies_on_the_mid_surface_of_the_wall_at_the_path_start():
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.gp import gp_Pnt
    doc, _, _, f, err = thin_sweep(shallow_arc, tangent_path, thickness1=2, mid_plane=True)
    assert not err, err
    shape = only(doc).shape
    areas = sorted(((K.mass_props(fc)["area"], fc) for fc in K.faces(shape)), key=lambda t: -t[0])
    walls = [fc for a_, fc in areas if a_ > 200]            # an arc-line-arc path gives three faces per wall
    assert len(walls) == 6
    r, a, cx = arc_geometry()
    for t in np.linspace(-a, a, 7):
        vert = BRepBuilderAPI_MakeVertex(gp_Pnt(cx + r * math.cos(t), r * math.sin(t), 0)).Vertex()
        dists = [BRepExtrema_DistShapeShape(vert, w).Value() for w in walls]
        assert min(dists) == pytest.approx(1.0, abs=2e-3)
        assert sum(abs(d - 1.0) < 2e-3 for d in dists) >= 2         # a wall face of each side


def block(doc):
    sid = sketch_feature(doc, lambda s: rect_lines(s, -20, -20, 40, 40), TOP)
    from conftest import region_refs
    add(doc, "extrude", regions=region_refs(doc, sid), depth=20, end="symmetric", op="new")
    return K.volume(only(doc).shape)


@pytest.mark.parametrize("op", ["add", "remove"])
def test_add_merges_and_remove_cuts_a_thin_sweep_at_the_path_start(op):
    doc = PartStudioDoc()
    before = block(doc)
    _, _, _, f, err = thin_sweep(shallow_arc, tangent_path, doc=doc, thickness1=2, mid_plane=True, op=op)
    assert not err, err
    p = only(doc)
    assert len(K.solids(p.shape)) == 1
    if op == "add":
        assert K.volume(p.shape) > before + 1
    else:
        assert 1 < before - K.volume(p.shape) < arc_length() * 2 * LENGTH


def test_a_closed_circle_swept_thin_is_a_tube_with_the_inner_radius_hollow():
    doc, _, _, f, err = thin_sweep(lambda s: s.add_circle(s.add_point(0, 0), 3), tangent_path, kind="circle",
                                   thickness1=1, thickness2=0)
    assert not err, err
    p = only(doc)
    assert len(K.solids(p.shape)) == 1
    assert K.volume(p.shape) == pytest.approx(math.pi * (4 ** 2 - 3 ** 2) * LENGTH, rel=0.02)
    pl = K.std_plane("Front")
    mid = pl.origin + 20 * pl.xdir - 10 * pl.ydir                 # the middle of the path's straight run

    def inside(offset):
        from OCP.BRepClass3d import BRepClass3d_SolidClassifier
        from OCP.TopAbs import TopAbs_IN
        from OCP.gp import gp_Pnt
        q = mid + np.array([0, offset, 0])
        cls = BRepClass3d_SolidClassifier(K.solids(p.shape)[0], gp_Pnt(*map(float, q)), 1e-6)
        return cls.State() == TopAbs_IN
    assert inside(3.5) and not inside(2.0) and not inside(0.0) and not inside(4.5)


def test_a_wall_thicker_than_the_tightest_bend_is_the_named_error():
    doc, _, _, f, err = thin_sweep(lambda s: s.add_line(s.add_point(0, 0), s.add_point(0, 2)), tight_path,
                                   kind="line", thickness1=10, thickness2=0)
    assert err == BEND
    doc, _, _, f, err = thin_sweep(lambda s: s.add_line(s.add_point(0, 0), s.add_point(0, 2)), tight_path,
                                   kind="line", thickness1=1, thickness2=0)
    assert not err, err


def test_face_names_survive_a_change_of_the_path_sketch():
    def prof(s):
        p = [s.add_point(0, 0), s.add_point(0, 3), s.add_point(3, 3)]
        s.add_line(p[0], p[1])
        s.add_line(p[1], p[2])
    doc, _, path, f, err = thin_sweep(prof, l_path, kind="line", path_kind="line", thickness1=1)
    assert not err, err
    before = face_names(doc, only(doc).id)
    assert len(before) == len(set(before)) and len(before) >= 10
    sk = doc.state.sketches[path].sketch
    pid = next(i for i, xy in sk.points.items() if xy == [30.0, 40.0])
    g = next(x for x in doc.features if x["id"] == path)
    g["params"]["sketch"]["points"][pid] = [35.0, 40.0]
    doc.replace_feature(g)
    assert not doc.errors.get(path) and not doc.errors.get(f["id"])
    assert face_names(doc, only(doc).id) == before


def test_a_sweep_with_no_creation_key_is_unchanged():
    doc = PartStudioDoc()
    prof = sketch_feature(doc, lambda s: rect_lines(s, -2, -2, 4, 4), TOP)
    path = sketch_feature(doc, tangent_path, FRONT)
    from conftest import region_refs
    f = add(doc, "sweep", regions=region_refs(doc, prof), path=chain(doc, path, "line"), op="new")
    assert not ({"creation", "curves", "thickness1", "thickness2", "mid_plane", "flip_wall"} & set(f["params"]))
    assert K.volume(only(doc).shape) == pytest.approx(16 * LENGTH, rel=0.02)


def test_no_curves_is_an_error_for_a_thin_sweep():
    doc = PartStudioDoc()
    path = sketch_feature(doc, l_path, FRONT)
    f, err = try_add(doc, "sweep", creation="thin", path=chain(doc, path, "line"), op="new")
    assert err and "Select sketch curves" in err


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
        return w

    yield make
    for w in made:
        w.deleteLater()


def test_the_sweep_dialog_offers_thin_and_hides_thickness_2_and_flip_wall_with_mid_plane(make_win):
    from PySide6.QtWidgets import QToolButton
    doc = PartStudioDoc()
    sketch_feature(doc, shallow_arc, TOP)
    w = make_win(doc)
    w.start_feature("sweep")
    dlg = w.dialog
    btns = {}
    for spec, wd in dlg.rows:
        if spec["t"] == "ops" and spec["key"] == "creation":
            btns = {b.text(): b for b in wd.findChildren(QToolButton)}
    assert list(btns) == ["Solid", "Surface", "Thin"]
    dlg._set("creation", "thin")
    p = dlg.feature["params"]
    assert p["thickness1"] == 1.0 and p["thickness2"] == 0.0
    shown = lambda key: next(wd for spec, wd in dlg.rows if spec.get("key") == key).isVisibleTo(dlg)
    assert shown("thickness1") and shown("thickness2") and shown("flip_wall") and shown("mid_plane")
    dlg._set("mid_plane", True)
    assert shown("thickness1") and not shown("thickness2") and not shown("flip_wall")


def test_the_command_search_finds_sweep_by_thin_sweep():
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "freeshape", "ui", "mainwindow.py")).read()
    line = next(ln for ln in src.splitlines() if 'C("sweep", "Sweep"' in ln)
    assert "thin sweep" in line and "surface sweep" in line and "pipe" in line


# ---------------------------------------------------------------------------- the engine window

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


def test_a_thin_sweep_made_in_the_engine_window_is_a_wall(engine_win):
    from PySide6.QtWidgets import QApplication
    w = engine_win
    se = w.studio_engine
    doc = w.doc
    prof = sketch_feature(doc, shallow_arc, TOP)
    path = sketch_feature(doc, tangent_path, FRONT)
    assert se.settle(180)
    w.start_feature("sweep")
    dlg = w.dialog
    dlg._set("creation", "thin")
    QApplication.processEvents()
    dlg._set("op", "new")
    dlg.activate(dlg.fields["curves"])
    dlg.handle_pick(Pick("sketch_line", (prof, ents(doc, prof, "arc")[0])))
    dlg.activate(dlg.fields["path"])
    dlg.handle_pick(Pick("sketch_line", (path, ents(doc, path, "line")[0])))
    dlg._set("mid_plane", True)
    dlg._set("thickness1", 2)
    w.commit_dialog()
    assert se.settle(180)
    assert not doc.errors, doc.errors
    (p,) = doc.state.parts.values()
    assert len(K.solids(p.shape)) == 1
    assert K.volume(p.shape) == pytest.approx(arc_length() * 2 * LENGTH, rel=0.03)
    names = face_names(doc, p.id)
    assert len(names) == len(set(names)) and names, "the window-side part carries the worker's element names"
