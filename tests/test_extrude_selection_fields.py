"""Extrude's pick fields (#23, #24): the regions field keeps the focus when it already holds a pick, a region inside
a body is picked through the body while that field is active, and each listed pick has its own remove button.
A 40 x 20 x 10 block with a second sketch on the Top plane (z = 0, under the block's top face), looked at from +z."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QToolButton  # noqa: E402

from conftest import block_doc, rect_lines, sketch_feature  # noqa: E402


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.camera import basis
    from freeshape.ui.mainwindow import MainWindow
    doc = block_doc(40, 20, 10)
    win_sid = sketch_feature(doc, lambda s: (rect_lines(s, 5, 5, 10, 5), rect_lines(s, 25, 5, 10, 5)))
    doc.rebuild()
    w = MainWindow()
    w._add_tab(doc)
    w.resize(1200, 800)
    w.show()
    app.processEvents()
    w.sid = win_sid
    w.viewport.cam.set_state((np.array([20.0, 10, 5]), basis((0, 0, 1), (0, 1, 0)), 0.1))
    w.viewport.select_filter = w.viewport.user_filter = None
    yield w
    if w.dialog is not None:
        w.cancel_dialog()
    w.deleteLater()


def at(win, *xyz):
    sx, sy, _z = win.viewport.cam.to_screen(np.array(xyz, float))
    return float(sx), float(sy)


def test_a_preselected_region_leaves_the_regions_field_active(win):
    from freeshape.ui.viewport import Pick
    win.start_feature("extrude", preselect=[Pick("region", (win.sid, 0))])
    d = win.dialog
    prof = d.fields["profiles"]
    assert len(prof.items) == 1
    assert d.fields["scope"].isVisibleTo(d)                 # Add: Merge scope is shown and empty
    assert d.active_field is prof, "the next click must add a region, not go to Merge scope"
    d.handle_pick(Pick("region", (win.sid, 1)))
    assert len(prof.items) == 2 and d.fields["scope"].items == []


def test_editing_an_extrude_leaves_its_regions_field_active(win):
    from freeshape.ui.viewport import Pick
    win.start_feature("extrude", preselect=[Pick("region", (win.sid, 0))])
    win.dialog._do_preview()
    win.commit_dialog()
    win.start_feature("extrude", edit_fid=win.doc.features[-1]["id"])
    assert win.dialog.active_field is win.dialog.fields["profiles"]


def test_a_filled_single_pick_field_still_hands_on(win):
    """Only lists that take several picks keep the focus; a single pick (Up to face) has nothing more to take."""
    from freeshape.ui.viewport import Pick
    win.start_feature("extrude", preselect=[Pick("region", (win.sid, 0))])
    d = win.dialog
    d.fields["profiles"].multi = False
    d.active_field = None
    d._sync_visibility()
    assert d.active_field is d.fields["scope"]


def test_regions_field_picks_a_region_behind_the_body(win):
    vp = win.viewport
    x, y = at(win, 10, 7.5, 0)
    assert vp.pick(x, y).kind == "face", "with no dialog the top face in front still wins"
    win.start_feature("extrude")
    d = win.dialog
    assert d.active_field is d.fields["profiles"]
    pk = vp.pick(x, y)
    assert pk.kind == "region", "a region under the top face wins while the regions field is active"
    d.activate(d.fields["scope"])                           # a field that wants parts does not see through
    assert vp.pick(x, y).kind == "part"


def test_each_listed_pick_has_its_own_remove_button(win):
    from freeshape.ui.viewport import Pick
    win.start_feature("extrude", preselect=[Pick("region", (win.sid, 0)), Pick("region", (win.sid, 1))])
    d = win.dialog
    prof = d.fields["profiles"]
    first, second = (tuple(it) for it in prof.items)
    prof.refresh(d.doc)
    buttons = [prof.list.itemWidget(prof.list.item(r)).findChild(QToolButton, "rowRemove")
               for r in range(prof.list.count())]
    assert len(buttons) == 2 and all(b is not None for b in buttons)
    assert prof.list.item(1).text() == "Sketch region"
    buttons[0].click()
    assert [tuple(it) for it in prof.items] == [second]
    assert len(d.feature["params"]["regions"]) == 1
    prof.refresh(d.doc)
    assert prof.list.count() == 1


def test_an_empty_field_has_no_remove_button(win):
    win.start_feature("extrude")
    prof = win.dialog.fields["profiles"]
    prof.refresh(win.doc)
    assert prof.list.count() == 1 and prof.list.itemWidget(prof.list.item(0)) is None
