"""I1.8: the mate dialog picks Sketch instance geometry. A hover shows a snap dot per role of the sketch entity
under the pointer; a click on a dot (or a triad of a sketch-owned connector, even over empty space) fills the
connector field with a sketch end. Offscreen; real mouse events on the viewport, no menu or modal dialog."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from conftest import FRONT, add, rect_lines, region_refs, sketch_feature  # noqa: E402
from freeshape import assembly as A  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.ui.assembly_ui import ROLE_ID  # noqa: E402


def layout_sketch(s):
    """Two construction lines and a free point, none of them at the sketch origin (its connector sits there)."""
    a, b, c = s.add_point(20, 0), s.add_point(50, 0), s.add_point(50, 25)
    s.add_line(a, b, construction=True)
    s.add_line(b, c, construction=True)
    s.add_point_entity(s.add_point(30, 12))


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    doc = Document()
    sid = sketch_feature(doc, layout_sketch, FRONT)       # lines (0,0)-(30,0), (30,0)-(30,20); a free point (10,10)
    rid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 40, 40), FRONT)
    add(doc, "extrude", regions=region_refs(doc, rid), depth=20, op="new")
    add(doc, "mate_connector", name="Pin", ref={"origin": True}, owner=sid)      # a sketch-owned connector
    w._add_tab(doc)
    w.new_assembly()
    app.processEvents()
    pid = next(iter(doc.state.parts))
    w.studio, w.sid, w.pid, w.app = doc, sid, pid, app
    w.isk = w.asm.insert_studio_sketch(doc.uid, sid)
    w.ipart = w.doc.insert(w.studio_link(doc, pid))
    T = np.eye(4)
    T[:3, 3] = [100.0, 0.0, 0.0]                         # the block sits clear of the sketch
    w.doc.instance(w.ipart)["T"] = T.tolist()
    w.doc.solve()
    w.asm.show_scene()
    w.resize(1200, 800)
    w.show()
    app.processEvents()
    yield w
    w.deleteLater()


def sk_res(win):
    return next(sr for fid, sr, _m in win.viewport.sketches if fid == win.isk)


def eids(win, etype):
    return [e for e, v in sk_res(win).sketch.entities.items() if v["type"] == etype]


def world(win, eid, role):
    from freeshape import sketchref
    sr = sk_res(win)
    plane = {"origin": sr.plane.origin, "xdir": sr.plane.xdir, "normal": sr.plane.normal}
    return np.asarray(sketchref.frame(plane, sr.sketch.to_dict(), eid, role)["o"], float)


def screen(win, w):
    s = win.viewport.cam.to_screen(np.asarray(w, float))
    return float(s[0]), float(s[1])


def click(win, xy, dx=0.0, dy=0.0):
    """A real left click on the viewport at screen point xy (+ offset), delivered as mouse events."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    vp = win.viewport
    p = QPoint(int(round(xy[0] + dx)), int(round(xy[1] + dy)))
    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, p)
    win.app.processEvents()


def hover(win, xy):
    vp = win.viewport
    vp._hover_xy = (float(xy[0]), float(xy[1]))      # what mouseMoveEvent queues; run it now
    vp._do_hover()


def open_dialog(win):
    win.asm.start_mate("fastened")
    d = win.asm.mate_dialog
    assert d is not None
    return d


def test_baseline_what_the_dialog_does_on_a_sketch_instance(win):
    """Facts the report quotes: which pick kinds the dialog enables and what a hover gives."""
    d = open_dialog(win)
    assert {"face", "edge", "sketch_point", "sketch_line"} <= win.viewport.select_filter
    assert d.fa.text.text() == "Click to select"


def test_hover_on_a_free_point_shows_its_snap_dot(win):
    open_dialog(win)
    pt = eids(win, "point")[0]
    hover(win, screen(win, world(win, pt, "point")))
    vp = win.viewport
    assert [c["role"] for c in vp.snap_points] == ["point"]
    assert vp.snap_hi is not None and vp.snap_hi["role"] == "point"


def test_hover_on_a_line_shows_end1_mid_end2(win):
    open_dialog(win)
    ln = eids(win, "line")[0]
    a, b = world(win, ln, "end1"), world(win, ln, "end2")
    hover(win, screen(win, (a + b) / 2 + (b - a) * 0.15))       # on the line, off every dot
    roles = sorted(c["role"] for c in win.viewport.snap_points)
    assert roles == ["end1", "end2", "mid"]


def test_click_on_a_free_point_with_no_part_behind_fills_the_field(win):
    d = open_dialog(win)
    pt = eids(win, "point")[0]
    xy = screen(win, world(win, pt, "point"))
    hover(win, xy)
    click(win, xy)
    c = d.mate["a"]
    assert c and c["kind"] == "sketch" and c["role"] == "point" and c["entity"] == pt and c["inst"] == win.isk
    assert d.active is d.fb                                     # the second field is next
    assert d.fa.text.text() == f"{win.doc.instance(win.isk)['name']} point"
    assert any(np.allclose(t["o"], world(win, pt, "point"), atol=1e-6) for t in win.viewport.triads)


def test_click_near_a_line_midpoint_gives_mid_and_near_its_start_end1(win):
    d = open_dialog(win)
    ln = eids(win, "line")[0]
    for role, off, want in (("mid", (4, 3), "mid"), ("end1", (3, -4), "end1"), ("end2", (-3, 4), "end2")):
        d._clear("a")
        d.activate(d.fa)
        xy = screen(win, world(win, ln, role))
        hover(win, (xy[0] + off[0], xy[1] + off[1]))
        click(win, xy, *off)
        c = d.mate["a"]
        assert c and c["kind"] == "sketch" and c["entity"] == ln and c["role"] == want, (role, c)
    assert d.fa.text.text().endswith("end 2")


def test_click_on_a_sketch_owned_connector_triad_over_empty_space_picks_the_named_end(win):
    d = open_dialog(win)
    pcs = win.doc.published_connectors(win.isk)
    assert len(pcs) == 1 and pcs[0]["name"] == "Pin"
    fr = win.doc.world_frame(win.doc.named_connector(win.isk, pcs[0]["id"]))
    xy = screen(win, fr["o"])
    assert win.viewport.pick(*xy) is None                       # nothing at all under the triad
    click(win, xy)
    c = d.mate["a"]
    assert c and c["kind"] == "connector" and c["named"] == pcs[0]["id"] and c["inst"] == win.isk
    assert any(t.get("color") for t in win.viewport.triads)     # the teal triad is drawn


def test_a_part_face_and_a_sketch_point_complete_a_mate_that_solves(win):
    d = open_dialog(win)
    pt = eids(win, "point")[0]
    bb = win.doc.world_shape(win.ipart)
    from freeshape import kernel as K
    lo, hi = K.bbox(bb)
    c0 = screen(win, (lo + hi) / 2)
    xy = None
    for dx in range(-30, 31, 6):                            # a spot on the block that is a face, not an edge
        for dy in range(-30, 31, 6):
            pk = win.viewport.pick(c0[0] + dx, c0[1] + dy)
            if xy is None and pk is not None and pk.kind == "face" and pk.key[0] == win.ipart:
                xy = (c0[0] + dx, c0[1] + dy)
    assert xy is not None
    click(win, xy)
    assert d.mate["a"]["kind"] != "sketch" and d.mate["a"]["inst"] == win.ipart
    sxy = screen(win, world(win, pt, "point"))
    hover(win, sxy)
    click(win, sxy)
    assert d.mate["b"]["kind"] == "sketch"
    d.accept()
    win.app.processEvents()
    m = win.doc.mates[-1]
    assert not win.doc.errors.get(m["id"]), win.doc.errors
    assert np.allclose(win.doc.world_frame(m["a"])["o"], win.doc.world_frame(m["b"])["o"], atol=1e-4)


def test_the_mate_list_and_tooltip_name_a_sketch_end_and_a_lost_entity_is_red(win):
    d = open_dialog(win)
    pt = eids(win, "point")[0]
    d.mate["a"] = win.doc.sketch_end(win.isk, pt, "point")
    d.mate["b"] = win.doc.connector(win.ipart, "face", 0)
    d.accept()
    win.app.processEvents()
    lst = win.asm.panel.mates
    tip = lst.item(0).toolTip()
    name = win.doc.instance(win.isk)["name"]
    assert f"{name} point" in tip
    # the sketch loses the point: the mate goes red with its named error
    import copy
    f = copy.deepcopy(win.studio.feature(win.sid))
    ent = f["params"]["sketch"]["entities"]
    del ent[pt]
    win.studio.replace_feature(f)
    win.doc.refresh()
    win.asm.panel.populate(win.doc)
    mid = win.doc.mates[0]["id"]
    assert "Lost its sketch geometry" in win.doc.errors[mid]
    assert "Lost its sketch geometry" in lst.item(0).toolTip()
    assert lst.item(0).foreground().color().name() == "#d93025"

