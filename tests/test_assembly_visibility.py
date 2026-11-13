"""The Assembly's visibility dropdown (the eye on the Instances header): a Part Studio's mate connectors are drawn
on their instances without a mate dialog open, and the dropdown hides and shows connectors, mates and sketches.
Offscreen."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from conftest import FRONT, add, only_part, sketch_feature  # noqa: E402
from test_connector_frames import plate_studio  # noqa: E402
from test_studio_connectors import connector_params, second_part  # noqa: E402


@pytest.fixture
def win(tmp_path, monkeypatch):
    """A plate with connector OnPlate, a second part that owns connector ForOther (placed on the plate's face),
    a layout sketch, and an Assembly holding both parts and the sketch."""
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    doc, _ = plate_studio()
    plate = only_part(doc).id
    params = connector_params(doc, name="OnPlate")
    other = second_part(doc)
    add(doc, "mate_connector", **params)
    add(doc, "mate_connector", **{**params, "name": "ForOther"}, owner=other)
    sid = sketch_feature(doc, lambda s: s.add_line(s.add_point(5, 5), s.add_point(25, 5), construction=True), FRONT)
    w._add_tab(doc)
    w.new_assembly()
    app.processEvents()
    w.ip = w.doc.insert(w.studio_link(doc, plate))
    w.io = w.doc.insert(w.studio_link(doc, other))
    w.isk = w.asm.insert_studio_sketch(doc.uid, sid)
    w.doc.solve()
    w.asm.show_scene()
    app.processEvents()
    w.app = app
    yield w
    w.deleteLater()


def test_studio_connectors_show_on_their_instances_without_a_dialog(win):
    a = win.doc
    assert [c["name"] for c in a.published_connectors(win.ip)] == ["OnPlate"]
    assert [c["name"] for c in a.published_connectors(win.io)] == ["ForOther"]      # the Owner part's instance
    assert win.asm.mate_dialog is None
    assert len(win.viewport.triads) == 2
    want = a.world_frame(a.named_connector(win.ip, a.published_connectors(win.ip)[0]["id"]))["o"]
    assert any(np.allclose(t["o"], want) for t in win.viewport.triads)


def test_dropdown_hides_and_shows_mate_connectors(win):
    act = win.asm.panel.vis_actions["connectors"]
    assert act.isChecked() and act.text() == "Mate connectors"
    act.trigger()
    assert win.viewport.triads == []
    act.trigger()
    assert len(win.viewport.triads) == 2


def test_hidden_instance_hides_its_connectors(win):
    win.doc.instance(win.io)["hidden"] = True
    win.asm.show_scene()
    assert len(win.viewport.triads) == 1


def test_dropdown_shows_every_mate_and_hides_sketches(win):
    a = win.doc
    m = a.make_mate("fastened")
    m["a"] = a.named_connector(win.ip, a.published_connectors(win.ip)[0]["id"])
    m["b"] = a.named_connector(win.io, a.published_connectors(win.io)[0]["id"])
    a.add_mate(m)
    win.asm.show_scene()
    acts = win.asm.panel.vis_actions
    assert not acts["mates"].isChecked() and len(win.viewport.triads) == 2      # no mate selected: connectors only
    acts["mates"].trigger()
    assert len(win.viewport.triads) == 4                                        # plus the mate's two ends
    assert acts["sketches"].isChecked() and [fid for fid, *_ in win.viewport.sketches] == [win.isk]
    acts["sketches"].trigger()
    assert win.viewport.sketches == []
