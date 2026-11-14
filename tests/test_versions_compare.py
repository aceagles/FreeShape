"""I4.3: what changed between two Versions of a Document (or a Version and the open tabs), per Part Studio tab, read
through FolderDocument.open_version without touching the working folder; and the Compare Versions panel opened from
the Versions list, the toolbar and the palette. Headless (offscreen Qt)."""
import copy
import os
import shutil

import pytest

from conftest import TOP, add, edge_refs, line_along, only_part, rect_lines, region_refs, sketch_feature
from freeshape import gitstore as G
from freeshape import history as H

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

pytest.importorskip("PySide6.QtWidgets")
pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QToolButton  # noqa: E402

from freeshape.ui import home as HM  # noqa: E402
from freeshape.ui import mainwindow as MW  # noqa: E402
from freeshape.ui import versions_ui as V  # noqa: E402


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(tmp_path / "autosave")))
    os.makedirs(tmp_path / "autosave", exist_ok=True)
    monkeypatch.setattr(HM, "thumbs_dir", lambda: str(tmp_path / "thumbs"))
    os.makedirs(tmp_path / "thumbs", exist_ok=True)
    for name in ("warning", "critical", "information"):     # never block on a message box
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.Ok)
    w = MW.MainWindow()
    w.settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    monkeypatch.setattr(MW.MainWindow, "autosave_dir", classmethod(lambda cls: str(tmp_path / "autosave")))
    app.processEvents()
    yield w
    if w.versions_ui.compare_panel is not None:
        w.versions_ui.compare_panel.close_panel()
    w.versions_ui.close_view()
    w.folder = None
    w.deleteLater()


def _version(w, name):
    dlg = V.CreateVersionDialog(w.versions_ui)
    dlg.name_edit.setText(name)
    assert dlg.submit(), dlg.error_label.text()


def _extrude(studio):
    return next(f for f in studio.features if f["type"] == "extrude")


def _document_v1_v2(w, tmp_path):
    """A Document whose Part Studio is a 20 x 20 x 10 box at v1, and at v2 is 25 deep with a fillet on its
    vertical edges. Returns (folder, studio, fillet feature)."""
    path = str(tmp_path / "CompareDoc")
    w._pick_new_document_path = lambda: path
    w.home.newDocument.emit()
    fd = w.folder
    studio = fd.tabs[0].doc
    sid = sketch_feature(studio, lambda s: rect_lines(s, 0, 0, 20, 20), TOP)
    add(studio, "extrude", regions=region_refs(studio, sid), depth=10.0, op="new")
    _version(w, "v1")
    ext = _extrude(studio)
    ext["params"]["depth"] = 25.0
    studio.replace_feature(ext)
    fillet = add(studio, "fillet", edges=edge_refs(only_part(studio), line_along([0, 0, 1]), expect=4), radius=2.0)
    assert not studio.errors, studio.errors
    _version(w, "v2")
    return fd, studio, fillet


def _studio_row(res, studio_tab_id):
    return next(t for t in res["tabs"] if t["id"] == studio_tab_id)


def _changed(row, name):
    return next(c for c in row["features"] if c["name"] == name)


def test_v1_to_v2_lists_the_depth_change_and_the_added_fillet(window, tmp_path):
    fd, studio, fillet = _document_v1_v2(window, tmp_path)
    res = H.compare_versions(fd, "v1", "v2")
    assert (res["old_label"], res["new_label"]) == ("v1", "v2")
    row = _studio_row(res, fd.tabs[0].id)
    assert row["kind"] == "studio" and row["status"] == "changed"
    ext = _changed(row, _extrude(studio)["name"])
    assert ext["kind"] == "changed" and "Depth: 10 mm → 25 mm" in ext["lines"]
    added = [c for c in row["features"] if c["kind"] == "added"]
    assert [c["name"] for c in added] == [fillet["name"]] and added[0]["type"] == "fillet"
    assert not [c for c in row["features"] if c["kind"] == "removed"]
    # the two snapshots ride along for a 3D view; the older one is v1's
    assert [f["params"].get("depth") for f in row["old"]["features"] if f["type"] == "extrude"] == [10.0]
    html = H.document_html(res)
    assert "Depth: 10 mm → 25 mm" in html and fillet["name"] in html and "v1" in html and "v2" in html
    # reversed, the fillet is removed and the depth goes back
    back = _studio_row(H.compare_versions(fd, "v2", "v1"), fd.tabs[0].id)
    assert [c["name"] for c in back["features"] if c["kind"] == "removed"] == [fillet["name"]]
    assert "Depth: 25 mm → 10 mm" in _changed(back, _extrude(studio)["name"])["lines"]


def test_v2_to_now_lists_an_unsaved_edit(window, tmp_path):
    fd, studio, fillet = _document_v1_v2(window, tmp_path)
    assert _studio_row(H.compare_versions(fd, "v2"), fd.tabs[0].id)["status"] == "same"
    f = next(x for x in studio.features if x["id"] == fillet["id"])
    f["params"]["radius"] = 3.5
    studio.replace_feature(f)
    assert studio.dirty
    res = H.compare_versions(fd, "v2", H.NOW)
    assert res["new_label"] == "now"
    row = _studio_row(res, fd.tabs[0].id)
    assert row["status"] == "changed"
    assert _changed(row, fillet["name"])["lines"] == ["Radius: 2 mm → 3.5 mm"]


def test_comparing_leaves_the_working_folder_and_open_tabs_untouched(window, tmp_path):
    fd, studio, fillet = _document_v1_v2(window, tmp_path)
    ext = _extrude(studio)
    ext["params"]["depth"] = 40.0                       # an unsaved edit that must survive
    studio.replace_feature(ext)
    head, branch = G.head(fd.path), G.current_branch(fd.path)
    data = copy.deepcopy([t.doc.features for t in fd.tabs])
    tabs = [t.doc for t in fd.tabs]
    on_disk = {n: open(os.path.join(fd.path, n), "rb").read() for n in os.listdir(fd.path)
               if os.path.isfile(os.path.join(fd.path, n))}
    H.compare_versions(fd, "v1", "v2")
    H.compare_versions(fd, "v1", H.NOW)
    assert not G.has_changes(fd.path)                   # git status clean: nothing written, nothing checked out
    assert (G.head(fd.path), G.current_branch(fd.path)) == (head, branch)
    assert {n: open(os.path.join(fd.path, n), "rb").read() for n in on_disk} == on_disk
    assert [t.doc for t in fd.tabs] == tabs and [t.doc.features for t in fd.tabs] == data
    assert studio.dirty and _extrude(studio)["params"]["depth"] == 40.0


def test_other_tab_kinds_say_the_comparison_covers_part_studios(window, tmp_path):
    fd, studio, fillet = _document_v1_v2(window, tmp_path)
    asm = fd.add_tab("assembly", "Assembly 1")
    row = _studio_row(H.compare_versions(fd, "v2", H.NOW), asm.id)
    assert row["status"] == "not compared" and row["lines"] == [H.NOT_COMPARED]
    assert H.NOT_COMPARED in H.document_html(H.compare_versions(fd, "v2", H.NOW))


def test_panel_opens_from_the_versions_list_toolbar_and_palette(window, tmp_path):
    w = window
    fd, studio, fillet = _document_v1_v2(w, tmp_path)
    dlg = V.VersionsDialog(w.versions_ui)
    assert not dlg.compare_btn.isEnabled() or len(dlg.selected_names()) == 1
    for i in range(dlg.list.count()):
        dlg.list.item(i).setSelected(True)                # v2 and v1
    assert dlg.selected_names() == ["v2", "v1"] and dlg.compare_btn.isEnabled()
    dlg.compare_btn.click()
    panel = w.versions_ui.compare_panel
    assert panel is not None and not panel.isHidden() and dlg.result() == QDialog.Accepted
    assert panel.pair == ("v1", "v2")
    res = panel.compare()
    assert res is panel.result
    row = _studio_row(res, fd.tabs[0].id)
    assert "Depth: 10 mm → 25 mm" in _changed(row, _extrude(studio)["name"])["lines"]
    assert "Depth: 10 mm → 25 mm" in panel.text.toPlainText()
    assert fillet["name"] in panel.text.toPlainText()

    # an edit while comparing with now shows up once the listener runs
    panel.fill(["v2", H.NOW])
    assert panel.pair == ("v2", H.NOW)
    panel.compare()
    f = next(x for x in studio.features if x["id"] == fillet["id"])
    f["params"]["radius"] = 3.0
    studio.replace_feature(f)
    for _ in range(5):
        QApplication.processEvents()
    assert "Radius: 2 mm → 3 mm" in panel.text.toPlainText()

    # the toolbar's history button toggles the panel
    btn = next(b for b in w.findChildren(QToolButton) if b.property("icon_name") == "history")
    btn.click()
    assert w.versions_ui.compare_panel is None
    btn.click()
    assert w.versions_ui.compare_panel is not None
    w.versions_ui.compare_panel.close_panel()

    # the palette's Compare Versions command opens it too
    cmd = next(c for c in w._palette_commands() if c.name == "Compare Versions")
    assert cmd.disabled == ""
    cmd.action()
    assert w.versions_ui.compare_panel is not None


def test_palette_command_says_why_without_a_document(window):
    cmd = next(c for c in window._palette_commands() if c.name == "Compare Versions")
    assert window.folder is None and cmd.disabled
    assert window.versions_ui.compare() is None and window.versions_ui.compare_panel is None
