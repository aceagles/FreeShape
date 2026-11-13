"""I2.6: the checks run on every open, not only in the app's own merge. The manifest is reconciled against the folder
(a tab file not in document.json, an entry whose file is missing) and every link is validated; each problem is a
Document warning naming the tab, the Document still opens, and MainWindow shows the warnings. A merge done with plain
git never passes through the app's preview, so open is where its dangling link is caught."""
import os
import shutil

import pytest

from conftest import TOP, add, rect_lines, region_refs, sketch_feature
from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import gitstore as G
from freeshape import links as L
from test_merge import plain

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _box(studio, size=20.0, depth=10.0):
    sid = sketch_feature(studio, lambda s: rect_lines(s, 0, 0, size, size), TOP)
    add(studio, "extrude", regions=region_refs(studio, sid), depth=depth, op="new")


def _doc(tmp_path):
    registry = R.DocRegistry(str(tmp_path / "app" / "documents.json"))
    doc = DF.FolderDocument.create(str(tmp_path / "Doc"), registry=registry)
    _box(doc.add_tab("studio", "Plate").doc)
    _box(doc.add_tab("studio", "Bracket").doc, 10.0, 5.0)
    asm = doc.add_tab("assembly", "Asm")
    plate = doc.tab("T1")
    asm.doc.insert(L.studio_source(plate, next(iter(plate.doc.state.parts))), name="Plate")
    doc.save()
    return doc


def test_clean_document_has_no_warnings(tmp_path):
    doc = _doc(tmp_path)
    assert DF.FolderDocument.open(doc.path).warnings == []


def test_tab_file_missing_from_the_manifest_is_reported_on_open(tmp_path):
    doc = _doc(tmp_path)
    shutil.copy(os.path.join(doc.path, "Plate.fshape"), os.path.join(doc.path, "Stray.fshape"))
    opened = DF.FolderDocument.open(doc.path)
    assert [(w["problem"], w["target"]) for w in opened.warnings] == [("not-in-manifest", "Stray.fshape")]
    assert "Stray.fshape" in opened.warnings[0]["message"] and "document.json" in opened.warnings[0]["message"]
    assert [t.id for t in opened.tabs] == ["T1", "T2", "T3"] and not opened.errors


def test_manifest_entry_whose_file_is_missing_is_reported_on_open(tmp_path):
    doc = _doc(tmp_path)
    os.remove(os.path.join(doc.path, "Bracket.fshape"))
    opened = DF.FolderDocument.open(doc.path)                  # opens: it is not refused
    assert [(w["problem"], w["tab"]) for w in opened.warnings] == [("missing-file", "T2")]
    assert "Bracket" in opened.warnings[0]["message"] and "missing" in opened.warnings[0]["message"]
    assert opened.tab("T2").doc is None and opened.tab("T1").doc is not None


def _plain_clean_merge(tmp_path):
    """Side A (main) deletes Bracket (T2); side B (alt) changes only Asm so it links Bracket. Plain git merges it
    without a conflict. Returns the merged plain clone."""
    doc = _doc(tmp_path)
    doc.create_version("v1", "base")
    doc.create_workspace("alt", "v1")
    bracket = doc.tab("T2")
    doc.tab("T3").doc.insert(L.studio_source(bracket, next(iter(bracket.doc.state.parts))), name="Bracket")
    doc.tab("T3").doc.dirty = True
    doc.save()
    doc.switch_workspace("main")
    os.remove(os.path.join(doc.path, doc.tab("T2").file))
    doc.remove_tab("T2")
    doc.save()
    G.commit_all(doc.path, "delete Bracket")
    clone = tmp_path / "plain"
    r = plain(["clone", "--quiet", "--no-local", doc.path, str(clone)], str(tmp_path), tmp_path)
    assert r.returncode == 0, r.stderr
    r = plain(["merge", "--no-edit", "origin/alt"], str(clone), tmp_path)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "CONFLICT" not in r.stdout
    return clone


def test_plain_git_clean_merge_dangling_link_is_reported_on_open(tmp_path):
    clone = _plain_clean_merge(tmp_path)
    opened = DF.FolderDocument.open(str(clone))
    assert [(w["problem"], w["tab"], w["target"]) for w in opened.warnings] == [("dangling-link", "T3", "T2")]
    msg = opened.warnings[0]["message"]
    assert "Asm" in msg and "T2" in msg and "Bracket" in msg
    assert opened.tab("T3").doc is not None and not opened.errors


# ---------------------------------------------------------------------- Qt
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"


def test_mainwindow_shows_the_warning_for_the_plain_git_merge(tmp_path, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QMessageBox

    from freeshape.ui import home as H
    from freeshape.ui import mainwindow as MW
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(tmp_path / "autosave")))
    monkeypatch.setattr(H, "thumbs_dir", lambda: str(tmp_path / "thumbs"))
    monkeypatch.setattr(R, "managed_root", lambda: str(tmp_path / "managed"))
    monkeypatch.setattr(R, "default_registry", lambda: R.DocRegistry(str(tmp_path / "ui" / "documents.json")))
    os.makedirs(tmp_path / "autosave", exist_ok=True)
    os.makedirs(tmp_path / "thumbs", exist_ok=True)
    boxes = []
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: boxes.append(a) or QMessageBox.Ok)
    clone = _plain_clean_merge(tmp_path)
    w = MW.MainWindow()
    try:
        w.settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
        monkeypatch.setattr(MW.MainWindow, "autosave_dir", classmethod(lambda cls: str(tmp_path / "autosave")))
        app.processEvents()
        assert not w.doc_warnings.isVisibleTo(w)
        fd = w.open_document(str(clone))
        assert fd is not None and fd.warnings
        app.processEvents()
        assert w.doc_warnings.isVisibleTo(w)
        assert "Asm" in w.doc_warnings.text() and "Bracket" in w.doc_warnings.text()
        assert w.merge_btn.isVisibleTo(w)
        assert boxes == []                                    # a warning, not a refusal or a dialog
    finally:
        w.folder = None
        w.deleteLater()
