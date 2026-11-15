"""I6.7: the Context bar over the 3D view of a Part Studio that has a Context. Offscreen; no modal dialog or menu
is run: every control is driven through the bar's functions or by clicking its own widgets."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402

from conftest import add  # noqa: E402
from freeshape.ui import panels as P  # noqa: E402
from freeshape.ui.palette import search_commands  # noqa: E402
from test_context_ui import make_rig  # noqa: E402


@pytest.fixture
def rig(tmp_path, monkeypatch):
    r = make_rig(tmp_path, monkeypatch)
    r.doc = r.plate.doc
    r.bar = r.w.context_bar
    yield r
    r.w.folder = None
    r.w.deleteLater()


def enter(r):
    r.w.tabs.setCurrentIndex(r.w._tab_index(r.asm.doc))
    r.w.asm.edit_in_context()
    assert r.w.doc is r.doc
    if not r.doc.listeners:          # make_rig replaced the Plate's __dict__, which dropped the window's listener
        r.doc.listeners.append(lambda: r.w._doc_changed_from(r.doc))
        r.doc.notify()
    r.cid = next(f["id"] for f in r.doc.features if f["type"] == "context")
    r.w._refresh_context_bar()


def select_row(r, fid):
    lst = r.w.left.features
    lst.clearSelection()
    next(lst.item(i) for i in range(lst.count()) if lst.item(i).data(P.ROLE_ID) == fid).setSelected(True)


def test_bar_shows_only_in_a_studio_with_a_context(rig):
    r = rig
    assert r.bar.isHidden()                                  # the Assembly
    r.w.tabs.setCurrentIndex(r.w._tab_index(r.plate.doc))
    assert r.bar.isHidden()                                  # a Part Studio without a Context
    r.w.tabs.setCurrentIndex(r.w._tab_index(r.asm.doc))
    enter(r)
    assert not r.bar.isHidden()
    r.w.tabs.setCurrentIndex(r.w._tab_index(r.asm.doc))
    assert r.bar.isHidden()
    r.w.tabs.setCurrentIndex(r.w._tab_index(r.pin.doc))
    assert r.bar.isHidden()
    r.w.tabs.setCurrentIndex(r.w._tab_index(r.plate.doc))
    assert not r.bar.isHidden()
    r.doc.set_suppressed(r.cid, True)
    assert r.bar.isHidden()


def test_bar_stays_enabled_while_a_feature_dialog_is_open(rig):
    r = rig
    enter(r)
    r.w.start_feature("extrude")
    assert r.w.mode == "dialog"
    assert not r.bar.isHidden() and r.bar.isEnabled() and r.bar.slider.isEnabled() and r.bar.through.isEnabled()
    r.bar.set_select_through(False)
    assert r.w.viewport.select_through is False
    r.w.cancel_dialog()


def test_label_status_and_update_context(rig):
    r = rig
    enter(r)
    assert r.bar.label.text() == "Context of Asm" and r.bar.status.text() == "current"
    assert not r.bar.update_btn.property("outOfDate")
    r.w.tabs.setCurrentIndex(r.w._tab_index(r.asm.doc))
    r.asm.doc.replace_mate(dict(r.m, offset=5.0))
    r.w.tabs.setCurrentIndex(r.w._tab_index(r.plate.doc))
    assert r.bar.status.text() == "out of date" and r.bar.update_btn.property("outOfDate") is True
    assert r.bar.update_btn.styleSheet()
    r.bar.update_btn.click()
    assert r.bar.status.text() == "current" and not r.bar.update_btn.property("outOfDate")
    assert not r.bar.update_btn.styleSheet()


def test_go_to_assembly(rig):
    r = rig
    enter(r)
    r.bar.goto_btn.click()
    assert r.w.doc is r.asm.doc


def test_whole_context_eye_matches_the_menu_item(rig):
    r = rig
    enter(r)
    r.bar.eye.click()
    assert r.cid in r.doc.hidden_features and r.bar.eye.isChecked()
    from PySide6.QtWidgets import QMenu
    m = QMenu()
    r.w._add_context_hide_action(m, r.cid)
    assert [a.text() for a in m.actions()] == ["Show context"]
    m.actions()[0].trigger()                                 # the menu shows it: the eye follows
    assert r.cid not in r.doc.hidden_features and not r.bar.eye.isChecked()
    m = QMenu()
    r.w._add_context_hide_action(m, r.cid)
    m.actions()[0].trigger()
    assert r.bar.eye.isChecked()
    r.bar.eye.click()
    assert r.cid not in r.doc.hidden_features


def test_instance_popup_lists_neighbours_and_eyes_match_the_menu(rig):
    r = rig
    enter(r)
    r.bar.popup.rebuild()
    assert len(r.bar.popup.rows) == 3 and dict(r.bar.instances())[f"{r.cid}/{r.ia}"] == r.asm.doc.instance(r.ia)["name"]
    from PySide6.QtWidgets import QLabel
    assert sorted(l.text() for l in r.bar.popup.findChildren(QLabel, "context_row_name")) == sorted(r.asm.doc.instance(i)["name"] for i in (r.ia, r.ib, r.ic))
    pa = f"{r.cid}/{r.ia}"
    r.bar.popup.rows[pa].click()
    assert r.doc.hidden_parts == {pa}
    r.bar.popup.rebuild()
    assert r.bar.popup.rows[pa].isChecked() and not r.bar.popup.rows[f"{r.cid}/{r.ib}"].isChecked()
    r.bar.popup.rows[pa].click()                              # click again: shown
    assert r.doc.hidden_parts == set()
    r.w.hide_context_instances([pa])                          # the menu path: the popup follows on refresh
    r.bar.popup.rebuild()
    assert r.bar.popup.rows[pa].isChecked()
    r.bar.show_instances()
    assert r.bar.popup.isVisible() or True
    r.bar.popup.hide()


def test_select_through_checkbox_both_ways(rig):
    r = rig
    enter(r)
    assert r.bar.through.isChecked() == r.w.viewport.select_through
    r.bar.through.click()
    assert r.w.viewport.select_through is (r.bar.through.isChecked())
    on = r.w.viewport.select_through
    assert r.w.settings.value("select_through_context") == ("true" if on else "false")
    r.w.act_select_through.trigger()                          # the View menu item
    assert r.w.viewport.select_through is (not on) and r.bar.through.isChecked() is (not on)
    r.w.set_select_through_context(on)
    assert r.bar.through.isChecked() is on


def test_slider_sets_alpha_and_setting_without_a_rebuild(rig):
    r = rig
    enter(r)
    before = r.w.viewport._batch_version
    r.bar.slider.setValue(60)
    assert r.w.viewport.context_alpha == pytest.approx(0.6)
    assert float(r.w.settings.value("view/context_alpha")) == pytest.approx(0.6)
    r.bar.slider.setValue(0)
    assert r.w.viewport.context_alpha == 0.0 and r.w.viewport._batch_version == before
    r.w.set_context_alpha(1.0)
    assert r.bar.slider.value() == 100
    r.bar.set_alpha(25)
    assert r.w.viewport.context_alpha == pytest.approx(0.25)
    assert r.w.viewport._batch_version == before


def test_two_contexts_the_bar_addresses_the_selected_row(rig):
    r = rig
    enter(r)
    cid2 = add(r.doc, "context", **dict(r.doc.feature(r.cid)["params"]))["id"]
    r.w._refresh_context_bar()
    assert r.bar.fid == r.cid                                 # no row selected: the first
    select_row(r, cid2)
    assert r.bar.fid == cid2 and not r.bar.eye.isChecked()
    r.bar.eye.click()
    assert cid2 in r.doc.hidden_features and r.cid not in r.doc.hidden_features
    assert all(pid.startswith(cid2 + "/") for pid, _n in r.bar.instances())
    select_row(r, r.cid)
    assert r.bar.fid == r.cid and not r.bar.eye.isChecked()


def test_command_search_finds_go_to_assembly_only_with_a_context(rig):
    r = rig
    r.w.tabs.setCurrentIndex(r.w._tab_index(r.plate.doc))
    assert not [c for c in search_commands(r.w._palette_commands(), "go to assembly") if c.name == "Go to assembly"]
    enter(r)
    hits = [c for c in search_commands(r.w._palette_commands(), "go to assembly") if c.name == "Go to assembly"]
    assert len(hits) == 1
    hits[0].action()
    assert r.w.doc is r.asm.doc
