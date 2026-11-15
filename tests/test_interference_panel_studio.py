"""The Interference detection panel in a Part Studio (I5.6). Offscreen; nothing modal runs."""
import copy
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from conftest import add, rect_lines, region_refs, sketch_feature  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.ui.palette import search_commands  # noqa: E402
from test_visibility_keys import _window  # noqa: E402


def block(doc, x, y, w, h, depth):
    sid = sketch_feature(doc, lambda s: rect_lines(s, x, y, w, h))
    add(doc, "extrude", regions=region_refs(doc, sid), depth=depth, op="new")


def studio(*blocks):
    doc = Document()
    for b in blocks:
        block(doc, *b)
    doc.dirty = False
    return doc


# the I5.2 fixture: 20 x 10 x 5 and 10 x 10 x 3 at (5, 5): they share 10 x 5 x 3 = 150 mm3
A = (0, 0, 20, 10, 5)
B = (5, 5, 10, 10, 3)


def _win(tmp_path, monkeypatch, doc):
    def no_box(*a, **k):
        raise AssertionError("a QMessageBox was created")
    for n in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(QMessageBox, n, staticmethod(no_box))
    w = _window(tmp_path, monkeypatch)
    w._add_tab(doc)
    QApplication.processEvents()
    assert w.doc is doc and not w.is_asm
    return w


@pytest.fixture
def make_win(tmp_path, monkeypatch):
    made = []

    def mk(*blocks):
        doc = studio(*blocks)
        w = _win(tmp_path, monkeypatch, doc)
        made.append(w)
        return w
    yield mk
    for w in made:
        w.close_studio_interference()
        w.hide()
        w.deleteLater()


def open_panel(w):
    w.btn_pinterf.click()
    QApplication.processEvents()
    p = w.studio_interf
    assert p is not None
    assert p.wait()
    return p


def pids(w):
    return list(w.doc.state.parts)


def test_open_lists_one_row_with_both_part_names(make_win):
    w = make_win(A, B)
    p = open_panel(w)
    (r,) = p.rows()
    names = {pt.name for pt in w.doc.state.parts.values()}
    assert {r["a_name"], r["b_name"]} == names and r["kind"] == "overlap"
    assert r["volume"] == pytest.approx(150, rel=1e-3)
    assert w.btn_pinterf.isChecked()
    assert not p.include_std.isVisible() and not p.include_std.isVisibleTo(p)


def test_activate_selects_in_parts_list_and_view_and_frames_overlap(make_win):
    w = make_win(A, B)
    p = open_panel(w)
    p.animate = False
    p.activate(0)
    ids = set(pids(w))
    assert set(w.left.parts.selected_part_ids()) == ids
    assert {pk.key[0] for pk in w.viewport.selection if pk.kind == "part"} == ids
    r = p.rows()[0]
    t = w.viewport.cam.target
    assert np.all(t >= r["lo"] - 1e-6) and np.all(t <= r["hi"] + 1e-6)
    assert w.viewport.feature_hl and w.viewport.feature_hl["polys"]
    assert "10 x 5 x 3" in p.detail.text()


def test_hidden_and_context_parts_make_no_rows_and_are_counted(make_win):
    w = make_win(A, B, (6, 6, 4, 4, 2), (7, 1, 4, 4, 2))
    first, second, third, fourth = pids(w)
    w.doc.state.parts[third].reference = True            # a Context body overlapping both
    w.doc.hidden_parts.add(fourth)                         # a hidden part overlapping A
    p = open_panel(w)
    rows = p.rows()
    assert len(rows) == 1 and {rows[0]["a"], rows[0]["b"]} == {first, second}
    assert "2 parts not checked: hidden or reference" in p.status.text()
    assert not p.include_std.isVisibleTo(p)


def test_one_part_says_two_or_more(make_win):
    w = make_win(A)
    p = open_panel(w)
    assert p.rows() == []
    assert "Interference needs two or more parts" in p.status.text()
    assert "no parts overlap" not in p.status.text()


def test_editing_a_feature_greys_the_list(make_win):
    w = make_win(A, B)
    p = open_panel(w)
    block(w.doc, 100, 100, 5, 5, 5)
    QApplication.processEvents()
    assert p.stale and len(p.rows()) == 1
    assert "Out of date. Check again." in p.status.text()
    p.check()
    assert p.wait() and not p.stale and len(p.rows()) == 1


def test_a_changed_shape_is_not_answered_from_the_cache(make_win):
    w = make_win(A, B)
    p = open_panel(w)
    f = [f for f in w.doc.features if f["type"] == "extrude"][1]
    f = copy.deepcopy(f)
    f["params"]["depth"] = 1
    w.doc.replace_feature(f)
    QApplication.processEvents()
    p.check()
    assert p.wait()
    rows = p.rows()
    assert rows and rows[0]["volume"] == pytest.approx(50, rel=1e-3)


def test_nothing_dirty_and_hidden_parts_unchanged(make_win):
    w = make_win(A, B)
    w.doc.hidden_parts.add("nothing")                      # a marker: the set must come back unchanged
    before = set(w.doc.hidden_parts)
    p = open_panel(w)
    p.activate(0)
    p.iso.setChecked(True)
    QApplication.processEvents()
    assert w.view_only_ids == set(pids(w))
    p.iso.setChecked(False)
    w.close_studio_interference()
    QApplication.processEvents()
    assert w.studio_interf is None and not w.btn_pinterf.isChecked()
    assert w.view_only_ids is None and not w.tool_transparent
    assert not w.doc.dirty and w.doc.hidden_parts == before


def test_palette_lists_it_and_search_finds_it_by_collision(make_win):
    w = make_win(A, B)
    cmds = w._palette_commands()
    assert "Interference detection" in [c.name for c in cmds]
    assert [c.name for c in search_commands(cmds, "collision")][:1] == ["Interference detection"]
    cmd = next(c for c in cmds if c.name == "Interference detection")
    assert cmd.disabled == ""
    cmd.action()
    QApplication.processEvents()
    assert w.studio_interf is not None
    cmd.action()                                           # again: closes
    assert w.studio_interf is None


def test_does_nothing_while_a_sketch_is_open(make_win):
    w = make_win(A, B)
    w.mode = "sketch"
    w.toggle_studio_interference(True)
    assert w.studio_interf is None and not w.btn_pinterf.isChecked()
    w.mode = "dialog"
    cmd = next(c for c in w._palette_commands() if c.name == "Interference detection")
    assert cmd.disabled
    w.toggle_studio_interference(True)
    assert w.studio_interf is None
    w.mode = "idle"
