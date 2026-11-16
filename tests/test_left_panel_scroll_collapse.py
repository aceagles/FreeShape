"""The feature list keeps its scroll position across a refill and shows the newest features; the Parts and
Dependencies sections collapse, Dependencies starts closed, and both remember their state. Offscreen.

Every test builds its own list or window, so each passes when run alone (CI shards tests across processes)."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
from PySide6.QtCore import QSettings as _RealQSettings  # noqa: E402

from conftest import rect_lines, sketch_feature  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.ui import panels as P  # noqa: E402


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _doc(n):
    doc = Document()
    for i in range(n):
        sketch_feature(doc, lambda s, i=i: rect_lines(s, i * 20.0, 0, 10, 10))
    return doc


def _list(app):
    lst = P.FeatureList()
    lst.resize(220, 160)            # room for a few rows only, so the list scrolls
    lst.show()
    app.processEvents()
    return lst


def _sb(lst):
    return lst.verticalScrollBar()


def test_first_show_scrolls_to_the_newest_features(app):
    lst = _list(app)
    lst.populate(_doc(12))
    app.processEvents()
    assert _sb(lst).maximum() > 0, "the list must scroll for this test to mean anything"
    assert _sb(lst).value() == _sb(lst).maximum()


def test_a_refill_keeps_the_scroll_position(app):
    doc = _doc(12)
    lst = _list(app)
    lst.populate(doc)
    _sb(lst).setValue(2)
    lst.populate(doc)               # a rebuild, an edit or a filter refills the rows
    app.processEvents()
    assert _sb(lst).value() == 2


def test_a_refill_at_the_bottom_stays_at_the_bottom(app):
    doc = _doc(12)
    lst = _list(app)
    lst.populate(doc)
    lst.populate(doc)
    assert _sb(lst).value() == _sb(lst).maximum() > 0


def test_adding_a_feature_scrolls_to_it(app):
    doc = _doc(12)
    lst = _list(app)
    lst.populate(doc)
    _sb(lst).setValue(0)
    sketch_feature(doc, lambda s: rect_lines(s, 500.0, 0, 10, 10))
    lst.populate(doc)
    app.processEvents()
    assert _sb(lst).value() == _sb(lst).maximum() > 0


def test_switching_documents_brings_back_each_position(app):
    a, b = _doc(12), _doc(12)
    lst = _list(app)
    lst.populate(a)
    _sb(lst).setValue(1)
    lst.populate(b)                 # a tab switch: b was never shown, so it opens at the bottom
    assert _sb(lst).value() == _sb(lst).maximum()
    lst.populate(a)                 # back to a: where it was left
    assert _sb(lst).value() == 1


def test_dependencies_starts_closed_and_opens_on_a_click(app):
    d = P.DependenciesPanel()
    assert not d.is_open() and d.body.isHidden()
    d.header.click()
    assert d.is_open() and not d.body.isHidden()
    d.header.click()
    assert d.body.isHidden()


def test_a_closed_dependencies_header_still_gives_a_count(app):
    d = P.DependenciesPanel()
    e = {"tab": "t1", "name": "Part Studio 1", "kind": "Derived", "label": "Part 1", "state": "current"}
    d.populate({"uses": [e], "used_by": []})
    assert d.header.text() == "Dependencies (1)"
    d.populate(None)
    assert d.header.text() == "Dependencies"


def test_parts_collapse_and_the_header_text_stays_plain(app):
    lp = P.LeftPanel()
    lp.parts_header.setText("Parts (2)")
    assert not lp.parts.isHidden()
    lp.parts_header.click()
    assert lp.parts.isHidden() and lp.parts_header.text() == "Parts (2)"


def test_section_state_is_remembered(app, tmp_path, monkeypatch):
    from PySide6 import QtCore
    ini = str(tmp_path / "settings.ini")
    monkeypatch.setattr(QtCore, "QSettings", lambda *a: _RealQSettings(ini, _RealQSettings.IniFormat))
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    assert not w.dependencies.is_open() and w.left.parts_header.is_open()       # the defaults
    w.dependencies.header.click()
    w.left.parts_header.click()
    w.deleteLater()
    w2 = MainWindow()
    assert w2.dependencies.is_open() and not w2.left.parts_header.is_open()
    assert w2.left.parts.isHidden() and not w2.dependencies.body.isHidden()
    w2.deleteLater()
