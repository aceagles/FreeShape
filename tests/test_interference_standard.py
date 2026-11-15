"""I5.5: "Include standard content" in the Interference panel, and expected thread overlaps. Offscreen; nothing modal."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape import pyfeature  # noqa: E402
from freeshape.pyfeature import registry  # noqa: E402
from freeshape.ui import interference_ui as IU  # noqa: E402
from test_assembly import box, src  # noqa: E402
from test_assembly_standard_bom import screwed  # noqa: E402
from test_visibility_keys import _window  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    user = tmp_path / "user_plugins"
    user.mkdir()
    monkeypatch.setenv("FREESHAPE_PLUGIN_DIR", str(user))
    before = {p.id for p in pyfeature.plugins()}
    problems_before = list(registry._PROBLEMS)
    yield
    registry._PROBLEMS[:] = problems_before
    for p in pyfeature.plugins():
        if p.id not in before:
            pyfeature.unregister_plugin(p.id)


def _win(tmp_path, monkeypatch, lib=None):
    def no_box(*a, **k):
        raise AssertionError("a QMessageBox was created")
    for n in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(QMessageBox, n, staticmethod(no_box))
    w = _window(tmp_path, monkeypatch)
    a, p, s, m = screwed(1.25, lib=lib)
    w.block = a.insert(src("block"), T=np.eye(4)) if lib else None     # inserted before the tab swaps the resolver
    w._add_tab(a)
    QApplication.processEvents()
    assert w.doc is a and w.is_asm
    w.a, w.p, w.s, w.m = a, p, s, m
    return w


@pytest.fixture
def win(tmp_path, monkeypatch):
    w = _win(tmp_path, monkeypatch)
    yield w
    _done(w)


def _done(w):
    if w.asm.interf_panel is not None:
        w.asm.close_interference()
    w.hide()
    w.deleteLater()


def open_panel(w):
    w.btn_interf.click()
    QApplication.processEvents()
    p = w.asm.interf_panel
    assert p is not None and p.wait()
    return p


def pair(r):
    return frozenset((r["a"], r["b"]))


def test_default_on_fastened_screw_is_one_expected_row_and_not_counted(win):
    p = open_panel(win)
    assert p.include_std.isChecked() and not p.include_std.isHidden()
    rows = p.rows()
    assert [(r["kind"], pair(r)) for r in rows] == [("thread", frozenset((win.p, win.s)))]
    assert p._group is not None and not p._group.isExpanded() and p._group.childCount() == 1
    assert "Expected (threads)" in p._group.text(0)
    assert "No interference." in p.status.text() and "1 expected thread overlap not counted" in p.status.text()
    assert not [r for r in rows if r["kind"] != "thread"]


def test_suppressed_mate_makes_it_a_counted_overlap(win):
    win.a.set_suppressed(win.m["id"], True)
    QApplication.processEvents()
    p = open_panel(win)
    rows = p.rows()
    assert [(r["kind"], pair(r)) for r in rows] == [("overlap", frozenset((win.p, win.s)))]
    assert p._group is None
    assert "1 interference." in p.status.text()


def test_tip_in_second_block_is_counted_and_plate_is_expected(tmp_path, monkeypatch):
    w = _win(tmp_path, monkeypatch, lib={"block": box(10, 10, 3, at=(5, 3, -4))})
    try:
        b = w.block
        p = open_panel(w)
        rows = p.rows()
        assert [(r["kind"], pair(r)) for r in rows] == [
            ("overlap", frozenset((w.s, b))), ("thread", frozenset((w.p, w.s)))]
        assert "1 interference." in p.status.text() and "1 expected thread overlap" in p.status.text()
        p.activate(1)                                    # an expected row can be opened
        assert p.detail.text().endswith("Thread fit (expected).")
    finally:
        _done(w)


def test_unticked_leaves_standard_out_and_says_how_many(win):
    p = open_panel(win)
    p.include_std.setChecked(False)
    assert p.wait()
    assert p.rows() == []
    assert p.status.text().endswith("1 standard part not checked.")
    assert "1 standard parts" not in p.status.text()
    win.a.insert_standard_part("sp:fasteners/iso4762", {"size": "M3", "length": 10})
    QApplication.processEvents()
    p.check()
    assert p.wait()
    assert "2 standard parts not checked." in p.status.text()


def test_checkbox_hidden_for_a_host_without_standard_content(win):
    class StudioHost(IU.AssemblyHost):
        has_standard = False

        def pair_rules(self, include_standard):
            raise AssertionError("a Part Studio has no standard content")

    p = IU.InterferencePanel(StudioHost(win.asm))
    try:
        assert p.wait() and p.include_std.isHidden()
    finally:
        p.shutdown()


def test_toggle_reruns_and_reuses_the_cache(win, monkeypatch):
    from freeshape.ui import interference_ui as IU
    monkeypatch.setattr(IU, "ENGINE", "thread")      # K.common is patched in this process
    calls = []
    real = K.common
    monkeypatch.setattr(K, "common", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    p = open_panel(win)
    n = len(calls)
    assert n >= 1
    p.include_std.setChecked(False)
    assert p.wait() and p.rows() == []
    p.include_std.setChecked(True)
    assert p.wait()
    assert [r["kind"] for r in p.rows()] == ["thread"]
    assert len(calls) == n
