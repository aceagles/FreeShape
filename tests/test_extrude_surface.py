"""I2.6: Extrude with Create = Surface. Sketch curves are extruded into a surface body: one shell per
connected chain, a face per curve, no solid. Remove, Intersect, the up-to ends and draft are refused."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from conftest import TOP, add, rect_lines, region_refs, sketch_feature, try_add  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.document import PartStudioDoc  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402

LEG = [10.0, 5.0, 10.0]                  # an open three-segment polyline in the Top plane, 25 long


def polyline(s, x0=0.0, y0=0.0, legs=LEG):
    """Segments along +x, +y, +x; returns the line ids."""
    pts = [(x0, y0), (x0 + legs[0], y0), (x0 + legs[0], y0 + legs[1]), (x0 + legs[0] + legs[2], y0 + legs[1])]
    ids = [s.add_point(*q) for q in pts]
    return [s.add_line(a, b) for a, b in zip(ids, ids[1:])]


def curve_refs(sid, *entities):
    return [{"kind": "sketch_chain", "sketch": sid, "entity": e} for e in entities]


def first_line(doc, sid):
    sk = doc.state.sketches[sid].sketch
    return next(i for i, e in sk.entities.items() if e["type"] == "line")


def line_ids(doc, sid):
    sk = doc.state.sketches[sid].sketch
    return [i for i, e in sk.entities.items() if e["type"] == "line"]


def area(shape):
    return K.mass_props(shape)["area"]


def only(doc):
    (p,) = doc.state.parts.values()
    return p


def surface_doc(build=polyline, plane=TOP, **kw):
    doc = PartStudioDoc()
    sid = sketch_feature(doc, build, plane)
    f, err = try_add(doc, "extrude", creation="surface", curves=curve_refs(sid, first_line(doc, sid)),
                     depth=kw.pop("depth", 10), op=kw.pop("op", "new"), **kw)
    return doc, sid, f, err


def test_open_polyline_extruded_as_a_surface():
    doc, sid, f, err = surface_doc()
    assert not err, err
    p = only(doc)
    assert p.name == "Surface 1"
    assert K.is_surface(p.shape) and not K.solids(p.shape)
    assert len(K.faces(p.shape)) == 3 and len(K.edges(p.shape)) == 10
    assert area(p.shape) == pytest.approx(sum(LEG) * 10)
    assert K.volume(p.shape) == 0.0


def test_a_closed_rectangle_of_curves_is_one_open_shell():
    from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
    doc = PartStudioDoc()
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 20, 10), TOP)
    f, err = try_add(doc, "extrude", creation="surface", curves=curve_refs(sid, *line_ids(doc, sid)),
                     depth=10, op="new")
    assert not err, err
    p = only(doc)
    assert K.is_surface(p.shape) and len(K.faces(p.shape)) == 4
    assert len(K.bodies(p.shape)) == 1
    fb = ShapeAnalysis_FreeBounds(p.shape)
    closed = fb.GetClosedWires()
    n = 0
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_WIRE
    ex = TopExp_Explorer(closed, TopAbs_WIRE)
    while ex.More():
        n += 1
        ex.Next()
    assert n == 2, "the rectangle's top and bottom are the two free boundaries"
    assert area(p.shape) == pytest.approx(60 * 10)


def test_picking_one_curve_of_a_chain_takes_the_chain_and_a_repeat_is_used_once():
    doc = PartStudioDoc()
    sid = sketch_feature(doc, polyline, TOP)
    a, b, c = line_ids(doc, sid)
    f, err = try_add(doc, "extrude", creation="surface", curves=curve_refs(sid, a, c, b), depth=10, op="new")
    assert not err, err
    assert len(K.faces(only(doc).shape)) == 3


def test_two_chains_make_two_shells_and_two_parts():
    def build(s):
        polyline(s)
        polyline(s, 0, 30)
    doc = PartStudioDoc()
    sid = sketch_feature(doc, build, TOP)
    lines = line_ids(doc, sid)
    f, err = try_add(doc, "extrude", creation="surface", curves=curve_refs(sid, lines[0], lines[3]),
                     depth=10, op="new")
    assert not err, err
    assert sorted(p.name for p in doc.state.parts.values()) == ["Surface 1", "Surface 2"]


def test_symmetric_puts_half_each_side_of_the_sketch_plane():
    doc, _, f, err = surface_doc(end="symmetric", depth=10)
    assert not err, err
    lo, hi = K.bbox(only(doc).shape)
    assert lo[2] == pytest.approx(-5) and hi[2] == pytest.approx(5)
    assert area(only(doc).shape) == pytest.approx(25 * 10)


def test_second_direction_and_through_all():
    doc, _, f, err = surface_doc(end="blind", depth=10, second=True, depth2=4)
    assert not err, err
    lo, hi = K.bbox(only(doc).shape)
    assert lo[2] == pytest.approx(-4) and hi[2] == pytest.approx(10)
    doc = PartStudioDoc()
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 40, 40), TOP)
    add(doc, "extrude", regions=region_refs(doc, sid), depth=30, op="new")
    sid2 = sketch_feature(doc, lambda s: polyline(s, 5, 5), TOP)
    f, err = try_add(doc, "extrude", creation="surface", curves=curve_refs(sid2, first_line(doc, sid2)),
                     end="through", op="new")
    assert not err, err
    surf = next(p for p in doc.state.parts.values() if K.is_surface(p.shape))
    lo, hi = K.bbox(surf.shape)
    assert hi[2] - lo[2] >= 30 and hi[2] >= 30 - 1e-6, "spans the 30 high part"


def test_two_surface_extrudes_sharing_an_edge_add_into_one_part():
    def build(s):
        a = s.add_point(0, 0)
        b = s.add_point(10, 0)
        c = s.add_point(10, 10)
        s.add_line(a, b)
        s.add_line(b, c)
    doc = PartStudioDoc()
    sid = sketch_feature(doc, build, TOP)
    l1, l2 = line_ids(doc, sid)
    # one line per extrude: the sketch chain would take both, so each is a sketch of its own
    doc = PartStudioDoc()
    s1 = sketch_feature(doc, lambda s: s.add_line(s.add_point(0, 0), s.add_point(10, 0)), TOP)
    s2 = sketch_feature(doc, lambda s: s.add_line(s.add_point(10, 0), s.add_point(10, 10)), TOP)
    add(doc, "extrude", creation="surface", curves=curve_refs(s1, first_line(doc, s1)), depth=10, op="new")
    f, err = try_add(doc, "extrude", creation="surface", curves=curve_refs(s2, first_line(doc, s2)),
                     depth=10, op="add")
    assert not err, err
    p = only(doc)
    assert p.name == "Surface 1"
    assert len(K.faces(p.shape)) == 2 and len(K.edges(p.shape)) == 7


@pytest.mark.parametrize("op", ["remove", "intersect"])
def test_remove_and_intersect_are_refused(op):
    doc, _, f, err = surface_doc(op=op)
    assert err == "Remove and Intersect are not available for surfaces"
    assert not doc.state.parts


@pytest.mark.parametrize("end", ["up_to_next", "up_to_face", "up_to_part", "up_to_vertex", "to_face"])
def test_an_up_to_end_is_refused(end):
    doc, _, f, err = surface_doc(end=end)
    assert err == "A surface extrude needs a Blind, Symmetric or Through all end"


def test_draft_and_model_edges_are_refused():
    doc, _, f, err = surface_doc(draft=True, draft_angle=5.0)
    assert err == "Draft is not available for a surface extrude"
    doc = PartStudioDoc()
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 20, 10), TOP)
    add(doc, "extrude", regions=region_refs(doc, sid), depth=10, op="new")
    pid = next(iter(doc.state.parts))
    e = F.make_ref(pid, K.edges(doc.state.parts[pid].shape)[0], "edge", doc.state.parts[pid].shape,
                   doc.state.named(pid))
    f, err = try_add(doc, "extrude", creation="surface", curves=[{"kind": "edge", "edge": e}], depth=5, op="new")
    assert err and "direction" in err


def test_no_curves_is_an_error_and_thin_is_not_built_yet():
    doc, _, f, err = surface_doc()
    f2, err2 = try_add(doc, "extrude", creation="surface", curves=[], depth=10, op="new")
    assert err2 == "Select sketch curves to extrude"
    f3, err3 = try_add(doc, "extrude", creation="thin", depth=10, op="new")
    assert err3 == "A thin extrude is not available yet"


def face_names(doc, pid):
    n = doc.state.named(pid)
    assert n is not None
    return sorted(n.names_of("F"))


def test_face_names_survive_a_depth_change():
    doc, _, f, err = surface_doc(depth=10)
    assert not err, err
    pid = next(iter(doc.state.parts))
    before = face_names(doc, pid)
    assert len(before) == 3 and len(set(before)) == 3
    f["params"]["depth"] = 17
    doc.replace_feature(f)
    assert not doc.errors.get(f["id"])
    assert face_names(doc, next(iter(doc.state.parts))) == before
    assert area(only(doc).shape) == pytest.approx(25 * 17)


def test_an_extrude_with_no_creation_key_builds_as_before():
    doc = PartStudioDoc()
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 20, 10), TOP)
    f = add(doc, "extrude", regions=region_refs(doc, sid), depth=10, op="new")
    assert "creation" not in f["params"] and "curves" not in f["params"]
    p = only(doc)
    assert K.volume(p.shape) == pytest.approx(2000) and len(K.faces(p.shape)) == 6 and p.name == "Part 1"
    solid_names = face_names(doc, p.id)
    doc2 = PartStudioDoc()
    sid2 = sketch_feature(doc2, lambda s: rect_lines(s, 0, 0, 20, 10), TOP)
    add(doc2, "extrude", creation="solid", regions=region_refs(doc2, sid2), depth=10, op="new")
    assert K.volume(only(doc2).shape) == pytest.approx(2000)
    assert face_names(doc2, only(doc2).id) == solid_names


def test_the_extrude_command_is_found_by_surface_extrude(make_win):
    from freeshape.ui.palette import search_commands
    w = make_win(PartStudioDoc())
    found = search_commands(w._palette_commands(), "surface extrude")
    assert "Extrude" in [c.name for c in found]


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
    """{label: button} of the visible segmented row that sets `key`."""
    from PySide6.QtWidgets import QToolButton
    for spec, w in dlg.rows:
        if spec["t"] == "ops" and spec["key"] == key and w.isVisibleTo(dlg):
            return {b.text(): b for b in w.findChildren(QToolButton)}
    return {}


def test_dialog_surface_takes_a_curve_pick_and_limits_the_operation(make_win):
    doc = PartStudioDoc()
    sid = sketch_feature(doc, polyline, TOP)
    w = make_win(doc)
    w.start_feature("extrude")
    dlg = w.dialog
    assert list(_segment_buttons(dlg, "creation")) == ["Solid", "Surface"]
    assert list(_segment_buttons(dlg, "op")) == ["New", "Add", "Remove", "Intersect"]
    # Solid: an open line is not a region, so the pick is refused
    lid = first_line(doc, sid)
    dlg.handle_pick(Pick("sketch_line", (sid, lid)))
    assert dlg.fields["profiles"].items == [] and dlg.fields["curves"].items == []
    dlg._set("op", "remove")
    _segment_buttons(dlg, "creation")["Surface"].click()
    assert dlg.feature["params"]["creation"] == "surface"
    assert list(_segment_buttons(dlg, "op")) == ["New", "Add"]
    assert dlg.feature["params"]["op"] == "add"
    assert not dlg.fields["profiles"].isVisibleTo(dlg) and dlg.fields["curves"].isVisibleTo(dlg)
    assert dlg.active_field is dlg.fields["curves"]
    dlg.handle_pick(Pick("sketch_line", (sid, lid)))
    assert dlg.fields["curves"].items == [("path", {"kind": "sketch_chain", "sketch": sid, "entity": lid})]
    dlg._set("op", "new")
    dlg._set("depth", 10)
    w.commit_dialog()
    f = doc.features[-1]
    assert not doc.errors.get(f["id"]), doc.errors
    assert f["params"]["creation"] == "surface" and len(f["params"]["curves"]) == 1
    p = only(doc)
    assert p.name == "Surface 1" and area(p.shape) == pytest.approx(250)


def test_dialog_solid_saves_no_curves_key(make_win):
    doc = PartStudioDoc()
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 20, 10), TOP)
    w = make_win(doc)
    w.start_feature("extrude")
    dlg = w.dialog
    dlg.fields["profiles"].toggle(("region", region_refs(doc, sid)[0]))
    dlg._set("op", "new")
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


def test_a_surface_extrude_made_in_the_engine_window_is_a_surface_part(engine_win):
    w = engine_win
    se = w.studio_engine
    doc = w.doc
    sid = sketch_feature(doc, polyline, TOP)
    assert se.settle(180)                        # the worker builds the sketch; the window then has it to pick
    w.start_feature("extrude")
    dlg = w.dialog
    dlg._set("creation", "surface")
    dlg._set("op", "new")
    dlg.handle_pick(Pick("sketch_line", (sid, first_line(doc, sid))))
    dlg._set("depth", 10)
    w.commit_dialog()
    assert se.settle(180)
    assert not doc.errors, doc.errors
    parts = list(doc.state.parts.values())
    assert [p.name for p in parts] == ["Surface 1"]
    p = parts[0]
    assert K.is_surface(p.shape), "the window-side shape is a surface, rebuilt from the worker's BREP"
    assert area(p.shape) == pytest.approx(250)
    assert K.volume(p.shape) == 0.0
    assert len(K.faces(p.shape)) == 3
