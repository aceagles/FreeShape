"""The tab strip says which Document its tabs belong to: the open Document's name sits at the left, its tabs come
straight after it, and any other open tab (a loose file) follows them with a "Loose file" badge. Nothing is closed
to get there. Headless: a real MainWindow on the offscreen Qt platform."""
import os
import shutil

import pytest

from conftest import EXAMPLES_DIR, TOP, add, rect_lines, region_refs, sketch_feature

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

pytest.importorskip("PySide6")
from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QTabBar  # noqa: E402

from freeshape.ui import home as H  # noqa: E402
from freeshape.ui import mainwindow as MW  # noqa: E402

HINGE_PARTS = os.path.join(EXAMPLES_DIR, "hinge_parts.fshape")


@pytest.fixture
def make_window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(tmp_path / "autosave")))
    os.makedirs(tmp_path / "autosave", exist_ok=True)
    monkeypatch.setattr(H, "thumbs_dir", lambda: str(tmp_path / "thumbs"))
    os.makedirs(tmp_path / "thumbs", exist_ok=True)
    made = []

    def make():
        w = MW.MainWindow()
        w.settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
        monkeypatch.setattr(MW.MainWindow, "autosave_dir", classmethod(lambda cls: _dir(tmp_path, "pid")))
        app.processEvents()
        made.append(w)
        return w

    yield make
    for w in made:
        w.folder = None
        w.deleteLater()


def _dir(tmp_path, name):
    d = str(tmp_path / "autosave" / name)
    os.makedirs(d, exist_ok=True)
    return d


def _box(studio):
    sid = sketch_feature(studio, lambda s: rect_lines(s, 0, 0, 20, 20), TOP)
    add(studio, "extrude", regions=region_refs(studio, sid), depth=10.0, op="new")
    assert not studio.errors, studio.errors


def _strip(w):
    """What the tab strip shows, left to right: the Document label (None when hidden), then (tab name, badged as a
    loose file?) per tab."""
    label = None if w.tab_document.isHidden() else w.tab_document.text()
    return label, [(w.tabs.tabText(i).rstrip(" •"), w.tabs.is_loose(i)) for i in range(w.tabs.count())]


def _open_seat_hook(w, tmp_path):
    """A loose file with a model in it, opened as a tab the way Home opens one."""
    path = tmp_path / "seat hook.fshape"
    shutil.copy(HINGE_PARTS, path)
    w.open_any(str(path))
    assert w.doc.path == str(path) and w._folder_tab(w.doc) is None
    return w.doc


def test_loose_files_alone_are_not_marked(make_window, tmp_path):
    w = make_window()
    hook = _open_seat_hook(w, tmp_path)
    w.new_doc()
    assert _strip(w) == (None, [("seat hook", False), (w.doc.name, False)])
    assert hook in w.docs.values()


def test_one_document_shows_its_name_and_its_tabs_unmarked(make_window, tmp_path):
    w = make_window()
    assert w.create_document(str(tmp_path / "Bracket")) is not None
    w.new_doc()
    w.new_assembly()
    label, tabs = _strip(w)
    assert label == "Bracket"
    assert [name for name, _ in tabs] == [t.name for t in w.folder.tabs] and len(tabs) == 3
    assert not any(loose for _, loose in tabs)
    assert "Bracket" in w.tabs.tabToolTip(0)


def test_new_document_keeps_the_open_loose_file_marked_and_after_its_own_tabs(make_window, tmp_path):
    w = make_window()
    hook = _open_seat_hook(w, tmp_path)
    features = len(hook.features)
    assert w.create_document(str(tmp_path / "Bracket")) is not None

    # the loose file is still open, after the Document's tab, and says what it is
    assert _strip(w) == ("Bracket", [("Part Studio 1", False), ("seat hook", True)])
    assert hook in w.docs.values() and len(hook.features) == features and not hook.dirty
    i = w._tab_index(hook)
    assert "Loose file" in w.tabs.tabToolTip(i) and "Bracket" in w.tabs.tabToolTip(i)
    assert w.doc is w.folder.tabs[0].doc                         # the new Document's tab is the one in front

    # a tab made in the Document joins the Document's tabs, not the far end past the loose file
    w.new_doc()
    assert w._folder_tab(w.doc) is not None
    label, tabs = _strip(w)
    assert [loose for _, loose in tabs] == [False, False, True] and tabs[-1][0] == "seat hook"
    assert w.tabs.currentIndex() == 1 and w.docs[w.tabs.tabData(1)] is w.doc

    # the loose tab still works as a tab: switch to it, and a tab made from there stays loose
    w.tabs.setCurrentIndex(w._tab_index(hook))
    assert w.doc is hook
    w.new_doc()
    assert _strip(w)[1][-2:] == [("seat hook", True), (w.doc.name, True)]

    # a Document opened while a loose file is already open goes in front of it too
    w2 = make_window()
    hook2 = _open_seat_hook(w2, tmp_path)
    w2.open_any(str(tmp_path / "Bracket"))
    label, tabs = _strip(w2)
    assert label == "Bracket" and tabs[-1] == ("seat hook", True) and not any(loose for _, loose in tabs[:-1])
    assert len(tabs) == len(w2.folder.tabs) + 1 and hook2 in w2.docs.values()


def test_second_document_replaces_the_first_and_the_loose_file_stays(make_window, tmp_path):
    """Only one Document is open at a time: opening another closes the first one's tabs (it asks first about
    unsaved ones). The strip is relabelled, and a loose file rides through both."""
    w = make_window()
    hook = _open_seat_hook(w, tmp_path)
    assert w.create_document(str(tmp_path / "Bracket")) is not None
    w.new_doc()
    assert w.save()
    assert w.create_document(str(tmp_path / "Clamp")) is not None
    assert _strip(w) == ("Clamp", [("Part Studio 1", False), ("seat hook", True)])
    assert hook in w.docs.values()

    w.open_any(str(tmp_path / "Bracket"))
    label, tabs = _strip(w)
    assert label == "Bracket" and len(tabs) == 3 and tabs[-1] == ("seat hook", True)
    assert [loose for _, loose in tabs] == [False, False, True]

    # with the Document closed only loose files are left, so nothing is marked
    assert w._close_document()
    assert _strip(w) == (None, [("seat hook", False)])
    assert hook in w.docs.values()


def test_recovered_session_sorts_tabs_into_their_document(make_window, tmp_path, monkeypatch):
    w = make_window()
    assert w.create_document(str(tmp_path / "Bracket")) is not None
    w.new_doc()                                                  # a Document tab, never saved
    scratch = w.folder.tabs[-1]
    scratch.doc.name = scratch.name = "Scratch"
    _box(scratch.doc)
    scratch.doc.dirty = True
    w._add_tab(w._new_document("seat hook"))                     # a loose file, never saved
    hook = w.doc
    assert w._folder_tab(hook) is None
    _box(hook)
    hook.dirty = True
    w.autosave()
    # the process dies: its autosave folder is now owned by a dead pid
    dead = next(p for p in range(4_000_000, 4_100_000) if not MW.MainWindow._process_alive(p))
    os.rename(tmp_path / "autosave" / "pid", tmp_path / "autosave" / str(dead))

    monkeypatch.setattr(MW.QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    w2 = make_window()
    w2.offer_recovery()
    label, tabs = _strip(w2)
    assert label == "Bracket"
    assert tabs == [("Part Studio 1", False), ("Scratch", False), ("seat hook (recovered)", True)]
    recovered = w2.docs[w2.tabs.tabData(2)]
    assert recovered.features and recovered.dirty and w2._folder_tab(recovered) is None
    assert w2.folder.tab(scratch.id).doc.features                # both pieces of unsaved work came back


def test_loose_badge_does_not_swallow_clicks(make_window, tmp_path):
    w = make_window()
    hook = _open_seat_hook(w, tmp_path)
    assert w.create_document(str(tmp_path / "Bracket")) is not None
    badge = w.tabs.tabButton(w._tab_index(hook), QTabBar.LeftSide)
    assert badge.text() == "Loose file"
    assert badge.testAttribute(Qt.WA_TransparentForMouseEvents)  # a click on it selects (or closes) the tab
