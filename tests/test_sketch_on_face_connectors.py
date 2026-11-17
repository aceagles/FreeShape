"""A mate connector placed on sketch geometry drawn on a part's face belongs to that part (issue: a connector on a
sketch point on Part 2's face never reached the Assembly). It is offered by the part's instances, drawn on them,
listed under them in the Instances list, pickable for a mate, and follows Part Studio edits. Offscreen."""
import copy
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from conftest import add, block_doc, centre_near, face_refs, normal_is, only_part, sketch_feature, both  # noqa: E402
from freeshape import links as L  # noqa: E402
from freeshape import sketchref  # noqa: E402

TOP_Z = 10.0


def face_sketch_studio():
    """A 40 x 20 x 10 block with a sketch on its top face holding one point, and a connector on that point.
    Returns (doc, part id, sketch id, connector feature)."""
    doc = block_doc()
    part = only_part(doc)
    top = face_refs(part, both(normal_is((0, 0, 1)), centre_near((20, 10, TOP_Z))), expect=1)[0]
    sid = sketch_feature(doc, lambda s: s.add_point(5, 5), {"kind": "face", "face": top})
    sk = doc.state.sketches[sid].sketch.to_dict()
    pt = next(iter(sk["points"]))
    d = sketchref.describe(sk, pt, "point")
    ref = {"sketch": sid, "entity": pt, "role": "point", "etype": d["etype"], "sig": d["sig"]}
    f = add(doc, "mate_connector", ref=ref, kind="sketch")
    return doc, part.id, sid, f


def test_connector_on_a_face_sketch_belongs_to_the_face_part():
    doc, pid, sid, f = face_sketch_studio()
    c = doc.state.connectors[f["id"]]
    assert c["part"] == pid and "sketch" not in c
    assert np.isclose(c["frame"]["o"][2], TOP_Z)


def test_the_part_source_publishes_it_and_the_sketch_source_does_not():
    doc, pid, sid, f = face_sketch_studio()
    r = L.LinkResolver(find_open_studio=lambda uid, path: doc)
    part_src = {"kind": "studio", "uid": doc.uid, "part": pid}
    assert [x["id"] for x in r.resolve_connectors(part_src)] == [f["id"]]
    assert r.resolve_connectors({"kind": "sketch", "uid": doc.uid, "sketch": sid}) == []


def test_an_explicit_sketch_owner_still_wins():
    doc, pid, sid, f = face_sketch_studio()
    g = copy.deepcopy(f)
    g["params"]["owner"] = sid
    doc.replace_feature(g)
    c = doc.state.connectors[f["id"]]
    assert c["part"] is None and c["sketch"] == sid


def test_it_follows_the_face_when_the_part_changes():
    doc, pid, sid, f = face_sketch_studio()
    ext = copy.deepcopy(next(x for x in doc.features if x["type"] == "extrude"))
    ext["params"]["depth"] = 25.0
    doc.replace_feature(ext)
    assert not doc.errors.get(f["id"])
    c = doc.state.connectors[f["id"]]
    assert c["part"] == pid and np.isclose(c["frame"]["o"][2], 25.0)


@pytest.fixture
def win(tmp_path, monkeypatch):
    """The block studio above with its part inserted twice into an Assembly (the copy moved along X)."""
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    doc, pid, _sid, f = face_sketch_studio()
    w._add_tab(doc)
    w.new_assembly()
    app.processEvents()
    w.i1 = w.doc.insert(w.studio_link(doc, pid))
    w.i2 = w.doc.insert(w.studio_link(doc, pid))
    w.doc.instance(w.i2)["T"] = [[1, 0, 0, 100.0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    w.doc.notify()
    w.asm.show_scene()
    app.processEvents()
    w.app, w.cid, w.studio = app, f["id"], doc
    yield w
    w.deleteLater()


def _rows(panel):
    lst = panel.instances
    return [lst.item(i) for i in range(lst.count())]


def test_drawn_on_each_instance(win):
    a = win.doc
    assert [c["id"] for c in a.published_connectors(win.i1)] == [win.cid]
    want = [a.world_frame(a.named_connector(i, win.cid))["o"] for i in (win.i1, win.i2)]
    assert len(win.viewport.triads) == 2
    for o in want:
        assert any(np.allclose(t["o"], o) for t in win.viewport.triads)


def test_listed_under_its_instance_in_a_dropdown(win):
    from freeshape.ui.assembly_ui import CONN_OPEN, CONN_SHUT, ROLE_CONN
    panel = win.asm.panel
    rows = _rows(panel)
    assert len(rows) == 2 and all(r.text().startswith(CONN_SHUT) for r in rows)     # collapsed at first
    panel.toggle_connectors(win.i1)
    rows = _rows(panel)
    assert len(rows) == 3 and rows[0].text().startswith(CONN_OPEN)
    assert rows[1].data(ROLE_CONN) == (win.i1, win.cid) and "Mate connector 1" in rows[1].text()
    rows[1].setSelected(True)
    assert panel.selected_instances() == []                 # a connector row is not an instance
    panel.toggle_connectors(win.i1)
    assert len(_rows(panel)) == 2


def test_a_connector_row_picks_a_mate_end(win):
    from freeshape.ui.assembly_ui import ROLE_CONN
    panel = win.asm.panel
    win.asm.start_mate("fastened")
    win.app.processEvents()
    dlg = win.asm.mate_dialog
    assert dlg is not None
    panel.toggle_connectors(win.i1)
    panel.toggle_connectors(win.i2)
    conn_rows = [r for r in _rows(panel) if r.data(ROLE_CONN)]
    assert [r.data(ROLE_CONN)[0] for r in conn_rows] == [win.i1, win.i2]
    for r in conn_rows:
        panel.instances.itemClicked.emit(r)
    m = dlg.mate
    assert (m["a"]["inst"], m["a"]["named"]) == (win.i1, win.cid)
    assert (m["b"]["inst"], m["b"]["named"]) == (win.i2, win.cid)


def test_k_toggle_hides_them(win):
    win.asm.panel.vis_actions["connectors"].trigger()
    assert win.viewport.triads == []
