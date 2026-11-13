"""I2.10: the Versions GUI: Create version, the Versions list, the read-only view, the update-available badge,
Import again, and everything disabled when the Document has no repository. Headless (offscreen Qt)."""
import os
import shutil

import pytest

from conftest import TOP, add, rect_lines, region_refs, sketch_feature, state_fingerprint
from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import gitstore as G
from freeshape import links as L

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

pytest.importorskip("PySide6.QtWidgets")
pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from freeshape.ui import home as H  # noqa: E402
from freeshape.ui import mainwindow as MW  # noqa: E402
from freeshape.ui import versions_ui as V  # noqa: E402


@pytest.fixture
def make_window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(tmp_path / "autosave")))
    os.makedirs(tmp_path / "autosave", exist_ok=True)
    monkeypatch.setattr(H, "thumbs_dir", lambda: str(tmp_path / "thumbs"))
    os.makedirs(tmp_path / "thumbs", exist_ok=True)
    for name in ("warning", "critical", "information"):     # never block on a message box
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.Ok)
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
        w.versions_ui.close_view()
        w.folder = None
        w.deleteLater()


def _box(studio, size=20.0, depth=10.0):
    sid = sketch_feature(studio, lambda s: rect_lines(s, 0, 0, size, size), TOP)
    add(studio, "extrude", regions=region_refs(studio, sid), depth=depth, op="new")
    assert not studio.errors, studio.errors


def _volumes(studio):
    return sorted(f["volume"] for f in state_fingerprint(studio).values())


def _new_ui_doc(w, tmp_path):
    path = str(tmp_path / "UiDoc")
    w._pick_new_document_path = lambda: path
    w.home.newDocument.emit()
    _box(w.folder.tabs[0].doc)
    return w.folder


def _create(w, name, note=""):
    dlg = V.CreateVersionDialog(w.versions_ui)
    dlg.name_edit.setText(name)
    dlg.note_edit.setText(note)
    return dlg, dlg.submit()


def test_create_version_through_dialog_and_refusals(make_window, tmp_path):
    w = make_window()
    fd = _new_ui_doc(w, tmp_path)
    assert w.versions_ui.create_btn.isEnabled() and fd.unsaved_tabs()
    dlg, ok = _create(w, "v1", "first box")
    assert ok and dlg.created == "v1" and not fd.unsaved_tabs()       # unsaved tabs were saved first
    assert [(v["name"], v["note"]) for v in fd.versions()] == [("v1", "first box")]
    head = G.head(fd.path)
    for bad in ("v1", "../x", "-x"):
        dlg, ok = _create(w, bad, "again")
        assert not ok and dlg.error_label.text() and dlg.created is None
    assert [v["name"] for v in fd.versions()] == ["v1"] and G.head(fd.path) == head
    dlg, ok = _create(w, "  ")
    assert not ok and dlg.error_label.text()


def test_open_version_read_only_and_back(make_window, tmp_path, monkeypatch):
    w = make_window()
    fd = _new_ui_doc(w, tmp_path)
    assert _create(w, "v1")[1]
    v1 = _volumes(fd.tabs[0].doc)
    studio = fd.tabs[0].doc
    ext = next(f for f in studio.features if f["type"] == "extrude")
    ext["params"]["depth"] = 30.0
    studio.replace_feature(ext)
    assert w._save_document()
    assert _create(w, "v2")[1]
    v2 = _volumes(fd.tabs[0].doc)
    assert v1 != v2

    dlg = V.VersionsDialog(w.versions_ui)
    assert [dlg.list.item(i).data(256) for i in range(dlg.list.count())] == ["v2", "v1"]     # newest first
    dlg.list.setCurrentRow(1)
    dlg.open_selected()
    assert w.versions_ui.view is not None
    assert "read-only" in w.doc.name and "v1" in w.doc.name
    assert _volumes(w.doc) == v1                                      # the shown geometry is v1's
    boxes = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: boxes.append(a[2]) or QMessageBox.Ok)
    assert w.save() is False and w.save_as() is False
    assert boxes and "read-only" in boxes[0]
    assert w.versions_ui.back_btn.isVisibleTo(w) or w.versions_ui.back_btn.isEnabled()

    w.versions_ui.close_view()
    assert w.versions_ui.view is None and w.tabs.count() == 1
    assert w.doc is fd.tabs[0].doc and _volumes(w.doc) == v2          # the live Document still shows v2
    assert not any("read-only" in w.tabs.tabText(i) for i in range(w.tabs.count()))


def test_new_workspace_from_version(make_window, tmp_path, monkeypatch):
    w = make_window()
    fd = _new_ui_doc(w, tmp_path)
    assert _create(w, "v1")[1]
    monkeypatch.setattr(V.QInputDialog, "getText", staticmethod(lambda *a, **k: ("alt", True)))
    dlg = V.VersionsDialog(w.versions_ui)
    dlg.new_workspace_selected()
    assert fd.current_workspace() == "alt" and w.workspace_combo.currentText() == "alt"


def _make_b_pinned_to_a(tmp_path):
    reg = R.default_registry()
    a = DF.FolderDocument.create(str(tmp_path / "A"), name="Lib", registry=reg)
    st = a.add_tab("studio", "Plate")
    _box(st.doc)
    a.create_version("v1", "first")
    pid = next(iter(st.doc.state.parts))
    b = DF.FolderDocument.create(str(tmp_path / "B"), name="B", registry=reg)
    asm = b.add_tab("assembly", "Asm")
    asm.doc.insert(L.studio_source(st, pid, document=a.id, version="v1"), name="Plate")
    asm.doc.refresh(solve=False, notify=False)
    b.save()
    b.create_version("b1")
    ext = next(f for f in st.doc.features if f["type"] == "extrude")
    ext["params"]["depth"] = 25.0
    st.doc.replace_feature(ext)
    a.create_version("v2", "bigger")
    return a, b


def test_update_badge_and_update(make_window, tmp_path):
    w = make_window()
    a, b = _make_b_pinned_to_a(tmp_path)
    assert w.open_document(b.path) is not None
    ui = w.versions_ui
    assert ui.update_count() == 1 and ui.updates_btn.text() == "Updates (1)" and ui.updates_btn.isEnabled()
    assert ui.updates_btn.isVisibleTo(w)
    dlg = V.UpdatesDialog(ui)
    assert list(dlg.update_buttons) == [a.id]
    dlg.update_buttons[a.id].click()
    src = w.folder.tabs[0].doc.instances[0]["source"]
    assert src["version"] == "v2"
    assert ui.update_count() == 0 and not ui.updates_btn.isVisibleTo(w)
    assert w.folder.tabs[0].doc.dirty and w.folder.unsaved_tabs()


def test_git_error_disables_every_action(make_window, tmp_path):
    w = make_window()
    fd = _new_ui_doc(w, tmp_path)
    fd.git_error = "git is not installed"
    w._refresh_workspaces()
    ui = w.versions_ui
    for b in (ui.create_btn, ui.versions_btn, ui.updates_btn):
        assert b.isVisibleTo(w) and not b.isEnabled() and "git is not installed" in b.toolTip()
    dlg = V.VersionsDialog(ui)
    for b in (dlg.open_btn, dlg.workspace_btn):
        assert not b.isEnabled() and "git is not installed" in b.toolTip()
    assert w.create_version_dialog() is False and w.versions_dialog() is None


def test_import_again_offered_only_for_imported_documents(make_window, tmp_path, monkeypatch):
    w = make_window()
    fd = _new_ui_doc(w, tmp_path)
    assert _create(w, "v1")[1]
    assert not V.VersionsDialog(w.versions_ui).import_btn.isVisibleTo(w) and w.versions_ui.import_source() is None
    # a Document received from another machine is imported, and offers Import again
    other = R.DocRegistry(str(tmp_path / "builder.json"))
    src = DF.FolderDocument.create(str(tmp_path / "Received"), registry=other)
    _box(src.add_tab("studio", "Plate").doc)
    src.save()
    src.create_version("v1", "sent")
    got = w.open_document(str(tmp_path / "Received"))
    assert got is not None and w.versions_ui.import_source() == os.path.realpath(str(tmp_path / "Received"))
    dlg = V.VersionsDialog(w.versions_ui)
    dlg.show()
    assert dlg.import_btn.isVisibleTo(dlg) and dlg.import_btn.isEnabled()
    src.create_version("v2", "more")
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.Yes)
    dlg.import_again()
    assert asked and "Received" in asked[0]
    assert [v["name"] for v in w.folder.versions()] == ["v1", "v2"]


def test_no_process_api_in_versions_ui():
    src = open(V.__file__).read()
    assert "subprocess" not in src and "gitstore.run" not in src
