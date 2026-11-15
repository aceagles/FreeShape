"""Ghosts, tool transparency and the view-only filter have owners (I5.3). Offscreen."""
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from freeshape.ui.history_ui import HistoryPanel  # noqa: E402
from test_assembly import box, make, src  # noqa: E402
from test_connector_frames import plate_studio  # noqa: E402
from test_studio_connectors import second_part  # noqa: E402
from test_visibility_keys import _window  # noqa: E402


def _asm():
    a = make({"block": box(10, 10, 10)})
    ids = [a.insert(src("block"), T=np.array([[1, 0, 0, 20 * k], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], float))
           for k in range(3)]
    return a, ids


@pytest.fixture
def awin(tmp_path, monkeypatch):
    w = _window(tmp_path, monkeypatch)
    a, w.ids = _asm()
    w._add_tab(a)
    QApplication.processEvents()
    assert w.doc is a and w.is_asm
    yield w
    w.hide()
    w.deleteLater()


@pytest.fixture
def swin(tmp_path, monkeypatch):
    w = _window(tmp_path, monkeypatch)
    doc, _ = plate_studio()
    w.pids = sorted(doc.state.parts) + [second_part(doc)]
    w._add_tab(doc)
    QApplication.processEvents()
    assert w.doc is doc
    w._show_state(doc.state)
    yield w
    w.hide()
    w.deleteLater()


def owner(w):
    return SimpleNamespace(main=w, _given=None, ghost_sets=None)


def give(o, mesh_tag):
    HistoryPanel._give(o, {"added": [(mesh_tag, (0, 1, 0, .5))], "removed": []})


def release(o):
    HistoryPanel._give(o, {"added": [], "removed": []})


def scene_ids(w):
    return {p[0] for p in w.viewport.parts}


def test_assembly_redraw_keeps_history_ghosts(awin):
    h = owner(awin)
    give(h, "g1")
    mine = awin.viewport.ghosts
    awin.asm.show_scene()
    assert awin.viewport.ghosts is mine


def test_second_owner_release_keeps_first_owners_list(awin):
    first, second = owner(awin), owner(awin)
    give(first, "a")
    mine = awin.viewport.ghosts
    give(second, "b")
    assert awin.viewport.ghosts is not mine
    give(first, "a2")
    mine = awin.viewport.ghosts
    release(second)
    assert awin.viewport.ghosts is mine


def test_assembly_interference_ghosts_cleared_only_when_still_its_own(awin):
    ui = awin.asm
    ui._interf = [("m", (1, 0, 0, .5))]
    awin.viewport.ghosts = ui._interf
    h = owner(awin)
    give(h, "g")
    mine = awin.viewport.ghosts
    ui._drop_interf()
    assert awin.viewport.ghosts is mine and ui._interf is None


def test_view_only_ids_in_assembly(awin):
    a = awin.doc
    undo, hidden = len(a._undo), [i.get("hidden") for i in a.instances]
    a.dirty = False
    assert scene_ids(awin) == set(awin.ids)
    awin.view_only_ids = {awin.ids[0], awin.ids[2]}
    awin.asm.show_scene()
    assert scene_ids(awin) == {awin.ids[0], awin.ids[2]}
    awin.view_only_ids = None
    awin.asm.show_scene()
    assert scene_ids(awin) == set(awin.ids)
    assert not a.dirty and len(a._undo) == undo and [i.get("hidden") for i in a.instances] == hidden


def test_view_only_ids_in_part_studio(swin):
    doc = swin.doc
    assert len(swin.pids) == 2
    before = set(doc.hidden_parts)
    doc.dirty = False
    swin.view_only_ids = {swin.pids[1]}
    swin._show_state(doc.state)
    assert scene_ids(swin) == {swin.pids[1]} and doc.hidden_parts == before
    swin.view_only_ids = None
    swin._show_state(doc.state)
    assert scene_ids(swin) == set(swin.pids) and doc.hidden_parts == before
    assert not doc.dirty


def test_tool_transparent_survives_redraw_and_tab_switch(awin):
    a = awin.doc
    awin._transparent[a] = {awin.ids[0]}
    awin.tool_transparent = {awin.ids[1], awin.ids[2]}
    awin._show_state(None)
    assert awin.viewport.transparent_ids == set(awin.ids)
    b, _ = _asm()
    awin._add_tab(b)
    QApplication.processEvents()
    assert awin.doc is b
    assert awin.viewport.transparent_ids == {awin.ids[1], awin.ids[2]}
    for i in range(awin.tabs.count()):
        if awin.docs.get(awin.tabs.tabData(i)) is a:
            awin.tabs.setCurrentIndex(i)
    QApplication.processEvents()
    assert awin.doc is a
    assert awin.viewport.transparent_ids == set(awin.ids)
    awin.tool_transparent = set()
    awin._show_state(None)
    assert awin.viewport.transparent_ids == {awin.ids[0]}
    assert awin._transparent[a] == {awin.ids[0]}


def test_fit_bounds_moves_target_into_box(awin):
    vp = awin.viewport
    lo, hi = np.array([100., 200, 300]), np.array([110., 210, 310])
    vp.fit_bounds(lo, hi, animate=False)
    assert np.all(vp.cam.target >= lo - 1e-6) and np.all(vp.cam.target <= hi + 1e-6)
