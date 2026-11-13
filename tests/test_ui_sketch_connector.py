"""I1.6: the Part Studio Mate connector tool on a sketch. Picking a sketch point, line or circle writes the ref
I1.3 accepts (role: a line's nearest of end1, mid, end2 to the click); the origin can be owned by a sketch.
Drives the registered command and the dialog offscreen."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from conftest import FRONT, add, rect_lines, sketch_feature  # noqa: E402
from freeshape import sketchref  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402
from test_studio_connectors import top_index  # noqa: E402


def layout(s):
    a, b, c = s.add_point(0, 0), s.add_point(30, 0), s.add_point(30, 20)
    s.add_line(a, b, construction=True)
    s.add_line(b, c, construction=True)
    s.add_point_entity(s.add_point(10, 10))
    s.add_circle(s.add_point(10, 10), 4.0)


def make_doc():
    doc = Document()
    sid = sketch_feature(doc, layout, FRONT)
    rid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 10, 10), FRONT)
    from conftest import region_refs
    add(doc, "extrude", regions=region_refs(doc, rid), depth=5, op="new")
    doc.hidden_features.add(sid)            # a consumed or hidden sketch: the tool must show it anyway
    return doc, sid


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    doc, sid = make_doc()
    w._add_tab(doc)
    app.processEvents()
    w.sid = sid
    yield w
    w.deleteLater()


def ents(win, etype):
    return [e for e, v in win.doc.state.sketches[win.sid].sketch.entities.items() if v["type"] == etype]


def frame_of(win, eid, role):
    res = win.doc.state.sketches[win.sid]
    plane = {"origin": res.plane.origin, "xdir": res.plane.xdir, "normal": res.plane.normal}
    return sketchref.frame(plane, res.sketch.to_dict(), eid, role)


def click_at(win, world):
    vp = win.viewport
    s = vp.cam.to_screen(np.asarray(world, float))
    vp.last_click = (float(s[0]), float(s[1]))


def open_tool(win):
    win.start_feature("mate_connector")
    assert win.mode == "dialog" and win.dialog.ftype == "mate_connector"
    win.dialog._do_preview()
    return win.dialog


def line_pick(win, which=0):
    eid = ents(win, "line")[which]
    return eid, Pick("sketch_line", (win.sid, eid))


def committed(win):
    win.commit_dialog()
    return [f for f in win.doc.features if f["type"] == "mate_connector"][-1]


def test_hidden_sketch_is_shown_while_the_tool_is_open(win):
    assert win.sid not in [fid for fid, _, _ in win.viewport.sketches]
    open_tool(win)
    assert win.sid in [fid for fid, _, _ in win.viewport.sketches]
    assert {"sketch_line", "sketch_point", "face", "edge"} <= win.viewport.select_filter


def test_pick_near_a_lines_middle_gives_mid(win):
    d = open_tool(win)
    eid, pk = line_pick(win)
    click_at(win, frame_of(win, eid, "mid")["o"])
    d.handle_pick(pk)
    ref = d.feature["params"]["ref"]
    desc = sketchref.describe(win.doc.state.sketches[win.sid].sketch.to_dict(), eid, "mid")
    assert ref == {"sketch": win.sid, "entity": eid, "role": "mid", "etype": "line", "sig": desc["sig"]}
    f = committed(win)
    assert win.doc.errors.get(f["id"]) is None
    c = win.doc.state.connectors[f["id"]]
    assert c["sketch"] == win.sid and c["part"] is None
    ex = frame_of(win, eid, "mid")
    for k in ("o", "z", "x"):
        assert np.allclose(c["frame"][k], ex[k], atol=1e-9)
    assert len(win.viewport.triads) == 1 and win.mode == "idle"


def test_pick_near_the_first_end_gives_end1_and_the_far_end_end2(win):
    eid, pk = line_pick(win)
    d = open_tool(win)
    o1, o2, om = (np.array(frame_of(win, eid, r)["o"]) for r in ("end1", "end2", "mid"))
    click_at(win, o1 + (om - o1) * 0.2)
    d.handle_pick(pk)
    assert d.feature["params"]["ref"]["role"] == "end1"
    click_at(win, o2 + (om - o2) * 0.2)
    d.handle_pick(pk)
    assert d.feature["params"]["ref"]["role"] == "end2"
    f = committed(win)
    assert np.allclose(win.doc.state.connectors[f["id"]]["frame"]["o"], o2)


def test_circle_gives_centre_and_point_gives_point(win):
    d = open_tool(win)
    cid = ents(win, "circle")[0]
    d.handle_pick(Pick("sketch_line", (win.sid, cid)))
    assert d.feature["params"]["ref"]["role"] == "centre" and d.feature["params"]["ref"]["etype"] == "circle"
    pid = ents(win, "point")[0]
    d.handle_pick(Pick("sketch_point", (win.sid, pid)))
    r = d.feature["params"]["ref"]
    assert r["entity"] == pid and r["role"] == "point"
    f = committed(win)
    assert win.doc.errors.get(f["id"]) is None
    assert np.allclose(win.doc.state.connectors[f["id"]]["frame"]["o"], frame_of(win, pid, "point")["o"])


def test_snap_points_on_a_hovered_line_and_a_snap_click_wins(win):
    d = open_tool(win)
    eid, pk = line_pick(win)
    snaps = d.snap_candidates(pk)
    assert [s["role"] for s in snaps] == ["end1", "end2", "mid"]
    assert {s["type"] for s in snaps} == {"vertex", "mid"}
    vp = win.viewport
    end2 = next(s for s in snaps if s["role"] == "end2")
    click_at(win, frame_of(win, eid, "mid")["o"])        # the click is at the middle, the highlighted dot is end 2
    vp.snap_base, vp.snap_hi = pk, end2
    d.handle_pick(Pick("sketch_line", (win.sid, ents(win, "line")[1])))
    r = d.feature["params"]["ref"]
    assert r["entity"] == eid and r["role"] == "end2"


def test_viewport_picks_a_sketch_point_and_a_line(win):
    open_tool(win)
    vp = win.viewport
    pid = ents(win, "point")[0]
    res = win.doc.state.sketches[win.sid]
    s = vp.cam.to_screen(np.asarray(res.plane.to_world(*res.sketch.points[res.sketch.entities[pid]["p"]]), float))
    assert vp.pick(float(s[0]), float(s[1])) == Pick("sketch_point", (win.sid, pid))
    eid = ents(win, "line")[0]
    s = vp.cam.to_screen(np.asarray(frame_of(win, eid, "mid")["o"], float))
    pk = vp.pick(float(s[0]), float(s[1]))
    assert pk is not None and pk.kind == "sketch_line" and pk.key[0] == win.sid


def test_origin_owner_list_has_parts_then_sketches(win):
    win.mate_connector_at_origin()
    d = win.dialog
    cb = d.widgets["owner"]
    labels = [cb.itemText(i) for i in range(cb.count())]
    name = win.doc.feature(win.sid)["name"]
    assert labels[0] == "— select —" and labels[1] == win.doc.state.parts[cb.itemData(1)].name
    assert f"{name} (sketch)" in labels and len(labels) == 4        # the part, then both sketches
    assert [cb.itemData(i) in win.doc.state.parts for i in range(1, 4)] == [True, False, False]
    cb.setCurrentIndex(cb.findData(win.sid))
    assert d.feature["params"]["owner"] == win.sid
    f = committed(win)
    assert win.doc.errors.get(f["id"]) is None
    c = win.doc.state.connectors[f["id"]]
    assert c["sketch"] == win.sid and c["part"] is None
    assert c["frame"] == {"o": [0.0, 0.0, 0.0], "z": [0.0, 0.0, 1.0], "x": [1.0, 0.0, 0.0]}


def test_origin_without_an_owner_is_refused(win):
    win.mate_connector_at_origin()
    d = win.dialog
    d.widgets["owner"].setCurrentIndex(0)
    d.feature["params"]["owner"] = None
    d.accept()
    assert win.mode == "dialog" and "Pick the part or sketch" in d.err.text()


def test_face_and_edge_picks_still_make_part_owned_connectors(win):
    from conftest import only_part
    part = only_part(win.doc)
    d = open_tool(win)
    d.handle_pick(Pick("face", (part.id, top_index(part.shape))))
    f = committed(win)
    assert win.doc.state.connectors[f["id"]]["part"] == part.id
    assert "sketch" not in win.doc.state.connectors[f["id"]]
    d = open_tool(win)
    d.handle_pick(Pick("edge", (part.id, 0)))
    g = committed(win)
    assert g["params"]["kind"] == "edge" and win.doc.state.connectors[g["id"]]["part"] == part.id
