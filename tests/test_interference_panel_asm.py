"""The Interference detection panel in the Assembly (I5.4). Offscreen; nothing modal runs."""
import os
import re
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape.ui import interference_ui as IU  # noqa: E402
from freeshape.ui.history_ui import HistoryPanel  # noqa: E402
from test_assembly import box, make, src  # noqa: E402
from test_visibility_keys import _window  # noqa: E402

RESULT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "spikes",
                      "op2_interference_bench.result.txt")


def T(x):
    return np.array([[1, 0, 0, x], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], float)


def _win(tmp_path, monkeypatch, xs):
    def no_box(*a, **k):
        raise AssertionError("a QMessageBox was created")
    for n in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(QMessageBox, n, staticmethod(no_box))
    w = _window(tmp_path, monkeypatch)
    a = make({"block": box(10, 10, 10)})
    w.ids = [a.insert(src("block"), T=T(x)) for x in xs]
    w._add_tab(a)
    QApplication.processEvents()
    assert w.doc is a and w.is_asm
    return w


def _done(w):
    if w.asm.interf_panel is not None:
        w.asm.close_interference()
    w.hide()
    w.deleteLater()


@pytest.fixture
def awin(tmp_path, monkeypatch):
    w = _win(tmp_path, monkeypatch, (0, 5, 40))      # the first two overlap by 5 x 10 x 10
    yield w
    _done(w)


@pytest.fixture
def awin5(tmp_path, monkeypatch):
    w = _win(tmp_path, monkeypatch, (0, 2, 4, 6, 8))          # ten overlapping pairs
    yield w
    _done(w)


def open_panel(w):
    w.btn_interf.click()
    QApplication.processEvents()
    p = w.asm.interf_panel
    assert p is not None
    assert p.wait()
    return p


def test_open_fills_one_row_and_makes_no_message_box(awin):
    p = open_panel(awin)
    rows = p.rows()
    assert len(rows) == 1
    r = rows[0]
    assert {r["a"], r["b"]} == set(awin.ids[:2]) and r["kind"] == "overlap"
    assert r["volume"] == pytest.approx(500.0, rel=1e-3)
    assert awin.btn_interf.isChecked()


def test_activate_selects_pair_ghosts_camera_and_transparency(awin):
    p = open_panel(awin)
    p.animate = False
    p.activate(0)
    vp = awin.viewport
    assert set(awin.asm.selected_instance_ids()) == set(awin.ids[:2])
    assert vp.ghosts is p._given and len(vp.ghosts) == 1
    r = p.rows()[0]
    assert np.all(vp.cam.target >= r["lo"] - 1e-6) and np.all(vp.cam.target <= r["hi"] + 1e-6)
    assert awin.tool_transparent == set(awin.ids[:2])
    assert vp.transparent_ids >= set(awin.ids[:2])
    assert vp.feature_hl and vp.feature_hl["polys"]
    assert "5 x 10 x 10" in p.detail.text()


def test_transparency_survives_a_redraw_and_open_runs_constant_matches_the_result_file(awin):
    p = open_panel(awin)
    p.activate(0)
    awin.asm.show_scene()
    assert awin.viewport.transparent_ids >= set(awin.ids[:2])
    awin._show_state(None)
    assert awin.viewport.transparent_ids >= set(awin.ids[:2])
    text = open(RESULT).read()
    m = re.search(r"^DECISION .*open_runs_at_most=(\d+)", text, re.M)
    assert IU.OPEN_RUNS_AT_MOST == int(m.group(1))


def test_scope_of_two_separate_parts_gives_no_interference(awin):
    p = open_panel(awin)
    p.set_scope([awin.ids[0], awin.ids[2]])
    p.check()
    assert p.wait()
    assert p.rows() == []
    assert p.status.text() == "No interference: no parts overlap."
    assert "2 selected" in p.scope_lab.text()


def test_a_failed_pair_is_a_could_not_check_row(awin, monkeypatch):
    monkeypatch.setattr(IU, "ENGINE", "thread")      # K.common is patched in this process
    def boom(*a, **k):
        raise K.KernelError("boolean blew up")
    monkeypatch.setattr(K, "common", boom)
    p = open_panel(awin)
    rows = p.rows()
    assert len(rows) == 1 and rows[0]["kind"] == "failed" and "blew up" in rows[0]["note"]
    assert "1 pair could not be checked" in p.status.text()
    p.activate(0)                       # a failed row can be clicked: no overlap to draw
    assert p.vp.ghosts == [] and "Could not check" in p.detail.text()


def test_a_document_change_greys_the_rows_and_keeps_them(awin):
    p = open_panel(awin)
    awin.doc.set_fixed(awin.ids[2], True)
    QApplication.processEvents()
    assert p.stale and len(p.rows()) == 1
    assert "Out of date. Check again." in p.status.text()
    assert p.tree.topLevelItem(0).foreground(0) != p.tree.topLevelItem(0).foreground(0).__class__()
    p.check()
    assert p.wait() and not p.stale and len(p.rows()) == 1


def test_isolate_is_view_only_and_the_document_is_untouched(awin):
    a = awin.doc
    a.dirty = False
    undo, hidden = len(a._undo), [i.get("hidden") for i in a.instances]
    p = open_panel(awin)
    p.activate(0)
    p.iso.setChecked(True)
    assert awin.view_only_ids == set(awin.ids[:2])
    assert {x[0] for x in awin.viewport.parts} == set(awin.ids[:2])
    awin.asm.close_interference()
    assert awin.view_only_ids is None
    assert {x[0] for x in awin.viewport.parts} == set(awin.ids)
    assert not a.dirty and len(a._undo) == undo and [i.get("hidden") for i in a.instances] == hidden


def test_close_restores_transparency_ghosts_and_the_button(awin):
    awin.tool_transparent = {"shift-t"}
    h = type("H", (), {})()
    h.main, h._given, h.ghost_sets = awin, None, None
    HistoryPanel._give(h, {"added": [("m", (0, 1, 0, .5))], "removed": []})
    hist = awin.viewport.ghosts
    p = open_panel(awin)
    p.activate(0)
    assert awin.viewport.ghosts is p._given and p._given is not hist
    assert awin.tool_transparent == {"shift-t", *awin.ids[:2]}
    awin.asm.close_interference()
    assert awin.tool_transparent == {"shift-t"}
    assert awin.viewport.ghosts is hist
    assert not awin.btn_interf.isChecked() and awin.asm.interf_panel is None
    assert awin.viewport.feature_hl is None


def test_cancel_stops_a_slow_run_with_a_partial_list(awin5, monkeypatch):
    monkeypatch.setattr(IU, "ENGINE", "thread")      # K.common is patched in this process
    awin = awin5
    real = K.common

    def slow(*x, **k):
        time.sleep(0.15)
        return real(*x, **k)
    monkeypatch.setattr(K, "common", slow)
    monkeypatch.setattr(IU, "OPEN_RUNS_AT_MOST", 0)       # wait for the Check button
    p = open_panel_no_wait(awin)
    assert not p.running and p.rows() == []
    p.btn_check.click()
    assert p.running and p.btn_check.text() == "Cancel"
    t0 = time.time()
    while p._prog[0] < 1 and time.time() - t0 < 20:
        QApplication.processEvents()
        time.sleep(0.01)
    p.btn_check.click()
    assert p.wait()
    assert p.incomplete and "Incomplete" in p.status.text() and p.btn_check.text() == "Check"
    assert 0 < len(p.rows()) < 10


def open_panel_no_wait(w):
    w.btn_interf.click()
    QApplication.processEvents()
    return w.asm.interf_panel


def test_big_check_waits_for_the_button_and_exploded_view_refuses(awin, monkeypatch):
    monkeypatch.setattr(IU, "OPEN_RUNS_AT_MOST", 0)
    p = open_panel_no_wait(awin)
    assert not p.running and p.rows() == [] and "press Check" in p.status.text()
    awin.asm.explode_panel = object()
    p.check()
    assert not p.running and "exploded" in p.status.text()
    awin.asm.explode_panel = None


def test_ignore_and_copy_row(awin):
    p = open_panel(awin)
    it = p.tree.topLevelItem(0)
    p.copy_row(it)
    assert QApplication.clipboard().text().startswith("block <1>")
    p.ignore(it)
    assert p.rows() == [] and "1 ignored" in p.status.text()
