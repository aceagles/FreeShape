"""I2.5: Workspaces (git branches of a Document): create from a version, switch, the dirty guard, the top-bar
switcher, and the UI's use of `open_document` (surfacing `git_error` without a dialog)."""
import os
import shutil

import pytest

from conftest import TOP, add, assert_golden, rect_lines, region_refs, sketch_feature, state_fingerprint
from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import gitstore as G
from freeshape import kernel as K

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


@pytest.fixture
def registry(tmp_path):
    return R.DocRegistry(str(tmp_path / "app" / "documents.json"))


def _box(studio, size=20.0, depth=10.0):
    sid = sketch_feature(studio, lambda s: rect_lines(s, 0, 0, size, size), TOP)
    add(studio, "extrude", regions=region_refs(studio, sid), depth=depth, op="new")
    assert not studio.errors, studio.errors


def _doc_with_v1(tmp_path, registry):
    doc = DF.FolderDocument.create(str(tmp_path / "Doc"), registry=registry)
    tab = doc.add_tab("studio", "Plate")
    _box(tab.doc)
    doc.save()
    doc.create_version("v1", "first box")
    return doc, tab.id


def _volumes(doc, tab_id):
    return {p: f["volume"] for p, f in state_fingerprint(doc.tab(tab_id).doc).items()}


def _disk(path):
    out = {}
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in files:
            full = os.path.join(root, f)
            with open(full, "rb") as fh:
                out[os.path.relpath(full, path)] = fh.read()
    return out


def test_create_from_v1_edit_switch_back_restores_original(tmp_path, registry):
    doc, tid = _doc_with_v1(tmp_path, registry)
    original = _volumes(doc, tid)
    assert_golden("workspaces/original_box", state_fingerprint(doc.tab(tid).doc))
    assert doc.workspaces() == ["main"] and doc.current_workspace() == "main"

    doc.create_workspace("thick", "v1")
    assert doc.workspaces() == ["main", "thick"] and doc.current_workspace() == "thick"
    assert _volumes(doc, tid) == original

    studio = doc.tab(tid).doc
    feature = next(f for f in studio.features if f["type"] == "extrude")
    feature["params"]["depth"] = 30.0
    studio.replace_feature(feature)
    assert not studio.errors, studio.errors
    thick = _volumes(doc, tid)
    assert thick != original                      # the edit changed the geometry in this Workspace
    doc.save()

    doc.switch_workspace("main")
    assert doc.current_workspace() == "main"
    assert _volumes(doc, tid) == original         # main still has the original geometry, reloaded from disk
    doc.switch_workspace("thick")
    assert _volumes(doc, tid) == thick            # the saved edit stayed with its Workspace
    assert G.list_tags(doc.path)[0]["name"] == "v1"


def test_switch_with_unsaved_tab_raises_and_changes_nothing(tmp_path, registry):
    doc, tid = _doc_with_v1(tmp_path, registry)
    doc.create_workspace("other", "v1")
    doc.switch_workspace("main")
    doc.tab(tid).doc.dirty = True
    before, head = _disk(doc.path), G.head(doc.path)
    with pytest.raises(DF.DirtyWorkspaceError) as e:
        doc.switch_workspace("other")
    assert e.value.tabs == ["Plate"]
    with pytest.raises(DF.DirtyWorkspaceError):
        doc.create_workspace("third", "v1")
    assert doc.current_workspace() == "main" and G.head(doc.path) == head
    assert _disk(doc.path) == before
    assert doc.workspaces() == ["main", "other"]  # the refused create left no branch
    assert doc.tab(tid).doc.dirty                 # and the unsaved tab is still the one in memory


def test_dirty_save_and_discard_choices(tmp_path, registry):
    doc, tid = _doc_with_v1(tmp_path, registry)
    doc.create_workspace("other", "v1", switch=False)
    doc.tab(tid).doc.name = "Renamed"
    doc.tab(tid).name = "Renamed"
    doc.tab(tid).doc.dirty = True
    doc.switch_workspace("other", dirty="save")
    assert doc.tab(tid).name == "Plate" and not doc.unsaved_tabs()       # v1's manifest, nothing unsaved
    doc.switch_workspace("main")                  # "save" kept the edit, committed in the Workspace it was made in
    assert doc.tab(tid).name == "Renamed"
    assert not G.has_changes(doc.path)
    doc.tab(tid).doc.dirty = True
    doc.switch_workspace("other", dirty="discard")
    assert doc.current_workspace() == "other" and not doc.tab(tid).doc.dirty


def test_workspace_names_and_errors(tmp_path, registry):
    doc, _ = _doc_with_v1(tmp_path, registry)
    for bad in ("", "-x", "a..b", "a b~", "../x", ".hid"):
        with pytest.raises(G.GitError):
            doc.create_workspace(bad, "v1")
    with pytest.raises(G.GitError):
        doc.create_workspace("main", "v1")
    with pytest.raises(G.GitError):
        doc.create_workspace("w", "nope")
    with pytest.raises(G.GitError):
        doc.switch_workspace("nope")
    view = doc.open_version("v1")
    with pytest.raises(K.KernelError):
        view.create_workspace("w", "v1")


def test_untrusted_open_refuses_workspaces(tmp_path, registry):
    doc, _ = _doc_with_v1(tmp_path, registry)
    raw = DF.FolderDocument.open(doc.path)
    with pytest.raises(K.KernelError):
        raw.create_workspace("w", "v1")
    with pytest.raises(K.KernelError):
        raw.switch_workspace("main")


# ---------------------------------------------------------------------- Qt
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from freeshape.ui import home as H  # noqa: E402
from freeshape.ui import mainwindow as MW  # noqa: E402


@pytest.fixture
def make_window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(tmp_path / "autosave")))
    os.makedirs(tmp_path / "autosave", exist_ok=True)
    monkeypatch.setattr(H, "thumbs_dir", lambda: str(tmp_path / "thumbs"))
    os.makedirs(tmp_path / "thumbs", exist_ok=True)
    made = []

    def make():
        w = MW.MainWindow()
        w.settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
        monkeypatch.setattr(MW.MainWindow, "autosave_dir", classmethod(lambda cls: str(tmp_path / "autosave")))
        app.processEvents()
        made.append(w)
        return w

    yield make
    for w in made:
        w.folder = None
        w.deleteLater()


def _items(combo):
    return [combo.itemText(i) for i in range(combo.count())]


def _ui_doc(w, tmp_path):
    """A Document with a box and version v1, opened in the window through the UI's own open path."""
    path = str(tmp_path / "UiDoc")
    w._pick_new_document_path = lambda: path
    w.home.newDocument.emit()
    _box(w.folder.tab(w.folder.tabs[0].id).doc)
    assert w._save_document()
    w.folder.create_version("v1", "first")
    w._refresh_workspaces()
    return w.folder


def test_switcher_lists_workspaces_and_switch_reloads_tabs(make_window, tmp_path):
    w = make_window()
    assert not w.workspace_combo.isVisibleTo(w)             # no Document, no switcher
    fd = _ui_doc(w, tmp_path)
    assert w.workspace_combo.isVisibleTo(w) or w.workspace_combo.isEnabled()
    assert _items(w.workspace_combo) == ["main"]
    assert w.create_workspace("alt", "v1")
    assert _items(w.workspace_combo) == ["main", "alt"] and w.workspace_combo.currentText() == "alt"
    assert fd.current_workspace() == "alt" and w.tabs.count() == 1
    assert w.doc is fd.tabs[0].doc                          # the tab shows the reloaded Document
    w.workspace_combo.setCurrentIndex(0)                    # choose main in the switcher
    assert fd.current_workspace() == "main" and w.doc is fd.tabs[0].doc and w.tabs.count() == 1


def test_dirty_tab_prompts_and_cancel_keeps_workspace(make_window, tmp_path, monkeypatch):
    w = make_window()
    fd = _ui_doc(w, tmp_path)
    assert w.create_workspace("alt", "v1")
    seen = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: seen.append(a[2]) or QMessageBox.Cancel)
    w.doc.dirty = True
    before = _disk(fd.path)
    w.workspace_combo.setCurrentIndex(0)                    # try to go to main
    assert len(seen) == 1 and "before switching to Workspace" in seen[0]
    assert fd.current_workspace() == "alt" and w.workspace_combo.currentText() == "alt"
    assert w.doc.dirty and _disk(fd.path) == before
    # Discard goes through, Save saves first
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Discard)
    w.workspace_combo.setCurrentIndex(0)
    assert fd.current_workspace() == "main" and not w.doc.dirty
    w.doc.dirty = True
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Save)
    w.workspace_combo.setCurrentIndex(1)
    assert fd.current_workspace() == "alt" and not w.doc.dirty


def test_ui_opens_through_open_document_and_imports_a_received_one(make_window, tmp_path, monkeypatch):
    w = make_window()
    # a Document made on "another machine" (its own registry) is imported, not opened in place
    other = R.DocRegistry(str(tmp_path / "builder.json"))
    src = DF.FolderDocument.create(str(tmp_path / "Received"), registry=other)
    _box(src.add_tab("studio", "Plate").doc)
    src.save()
    src.create_version("v1", "sent")
    calls = []
    real = DF.FolderDocument.open_document.__func__
    monkeypatch.setattr(DF.FolderDocument, "open_document",
                        classmethod(lambda cls, *a, **k: calls.append(a) or real(cls, *a, **k)))
    fd = w.open_document(str(tmp_path / "Received"))
    assert calls and fd is not None
    assert os.path.realpath(fd.path) != os.path.realpath(str(tmp_path / "Received"))
    assert fd.trusted and fd.versions()[0]["name"] == "v1"      # versions work: it was opened the safe way
    assert w.create_workspace("alt", "v1") and fd.current_workspace() == "alt"


def test_git_error_is_shown_in_the_status_bar_without_a_dialog(make_window, tmp_path, monkeypatch):
    def broken(path):
        raise G.GitUnavailable("git is not installed")
    monkeypatch.setattr(G, "init_repo", broken)
    boxes = []
    for name in ("warning", "critical", "information", "question"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: boxes.append(a) or QMessageBox.Cancel)
    w = make_window()
    path = str(tmp_path / "NoGit")
    w._pick_new_document_path = lambda: path
    w.home.newDocument.emit()
    assert w.folder is not None and w.folder.git_error
    assert "git is not installed" in w.status.currentMessage()
    assert not w.workspace_combo.isEnabled() and "git is not installed" in w.workspace_combo.toolTip()
    assert boxes == []
