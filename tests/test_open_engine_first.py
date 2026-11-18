"""#64 (I2.2): with the engine on, opening a Part Studio attaches its tab to the engine before anything builds, so
the window never builds it: `PartStudioDoc.from_data(build=False)` for a window that attaches at once (File >
Open, insert from file, autosave recovery, a Document and a recovered Document tab). With the engine off, or once
it has failed, the window builds the tab itself as before. The first feature preview after the reopen of an
import runs the import in the window from its stored build, never from the STEP file."""
import os
import signal

import pytest

from freeshape import features as F
from freeshape.document import PartStudioDoc

from test_import_stored_build import PARTS, _step_key, _window, step_file, step_reads  # noqa: F401 (fixtures)

pytest.importorskip("PySide6.QtWidgets")


def _imports_here(monkeypatch):
    """Every run of the import feature in this (the window's) process."""
    calls = []
    real = F.RUNNERS["import"]
    monkeypatch.setitem(F.RUNNERS, "import", lambda st, f: calls.append(1) or real(st, f))
    return calls


def _quiet(monkeypatch):
    from freeshape.ui import mainwindow as MW
    warned = []
    for name in ("warning", "critical"):
        monkeypatch.setattr(MW.QMessageBox, name, lambda *a, **k: warned.append(a[2]))
    return warned


def _end(w, before):
    from freeshape.engine import process as P
    from test_partstudio_engine import _children
    if w.studio_engine is not None:
        w.studio_engine.shutdown()
    P.SPARES.clear()
    for p in sorted(set(_children()) - before):
        try:
            os.kill(p, signal.SIGKILL)
        except OSError:
            pass


def _saved_import(w, step_file, folder):
    """Import `step_file` in `w`, save it to `folder`/s.fshape and close its tab; the path."""
    eng = w.studio_engine
    w.import_step_file(step_file)
    assert eng is None or eng.settle(180)
    doc = w.doc
    assert len(doc.state.parts) == PARTS and not doc.errors, doc.errors
    path = os.path.join(str(folder), "s.fshape")
    doc.path = path
    assert w.save()
    w.close_tab(w._tab_index(doc))
    assert eng is None or eng.settle(60)
    return path


def test_engine_mode_reopen_never_builds_in_the_window_before_the_engine_answers(step_file, tmp_path, monkeypatch,
                                                                                 step_reads):
    from test_partstudio_engine import _children
    before = set(_children())
    warned = _quiet(monkeypatch)
    w = _window()
    try:
        eng = w.studio_engine
        path = _saved_import(w, step_file, tmp_path)
        here = _imports_here(monkeypatch)
        del step_reads[:]
        seen = []                                   # (run_import calls, parts held) when the first result lands
        real_apply = eng._apply
        monkeypatch.setattr(eng, "_apply", lambda rec, res: seen.append(
            (len(here), len(rec.doc.state.parts), dict(rec.doc._import_built))) or real_apply(rec, res))
        w.open_file(path)
        doc = w.doc
        assert doc.path == path and eng.attached(doc)
        # open_file has returned: the tab is the engine's, and the window holds no part and ran no import
        assert not doc.state.parts and not here and not step_reads and not doc._import_built
        assert eng.settle(180)
        assert seen and seen[0] == (0, 0, {})       # nothing built here before the engine's first result
        assert len(doc.state.parts) == PARTS and len(eng.displayed(doc)) == PARTS   # the worker's parts, drawn
        assert not here and not step_reads and not warned and not doc.errors, (warned, doc.errors)
    finally:
        _end(w, before)


def test_the_first_preview_after_reopen_reads_the_stored_build_not_the_step(step_file, tmp_path, monkeypatch,
                                                                             step_reads):
    from test_partstudio_engine import _children
    before = set(_children())
    warned = _quiet(monkeypatch)
    w = _window()
    try:
        eng = w.studio_engine
        path = _saved_import(w, step_file, tmp_path)
        fid = None
        w.open_file(path)
        assert eng.settle(180)
        doc = w.doc
        fid = next(f["id"] for f in doc.features if f["type"] == "import")
        assert os.path.exists(os.path.join(str(tmp_path), "blobs", _step_key(path, fid)))  # the STEP is there to read
        here = _imports_here(monkeypatch)
        del step_reads[:]
        plane = doc.make_feature("plane")              # what a feature dialog's first preview computes
        st, _err = doc.preview(plane, len(doc.features))
        assert here                                    # the preview ran the import in the window...
        assert not step_reads                          # ...from its stored build: the STEP file was not read
        assert len([p for p in st.parts.values() if not p.reference]) == PARTS
        assert not warned and not doc.errors, (warned, doc.errors)
    finally:
        _end(w, before)


def test_engine_off_opens_and_builds_in_the_window(step_file, tmp_path, monkeypatch):
    os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")
    os.environ.setdefault("FREESHAPE_NO_HOME", "1")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    from test_partstudio_engine import _Settings
    warned = _quiet(monkeypatch)
    w = MainWindow(engine=False)
    w.settings = _Settings()
    path = _saved_import(w, step_file, tmp_path)
    here = _imports_here(monkeypatch)
    w.open_file(path)
    doc = w.doc
    assert doc.path == path and here                  # built here, during open_file
    assert len(doc.state.parts) == PARTS and not warned and not doc.errors


def test_an_engine_that_fails_after_the_open_hands_the_build_back_to_the_window(step_file, tmp_path, monkeypatch):
    from test_partstudio_engine import _children
    before = set(_children())
    warned = _quiet(monkeypatch)
    w = _window()
    try:
        eng = w.studio_engine
        path = _saved_import(w, step_file, tmp_path)
        w.open_file(path)
        doc = w.doc
        assert eng.attached(doc) and not doc.state.parts
        eng._failed(eng._recs[id(doc)], RuntimeError("worker lost"))      # what a dead engine reports
        assert not eng.attached(doc)
        assert len(doc.state.parts) == PARTS and not doc.errors      # rebuilt here at once
        assert not warned
        # a file opened while the engine is broken builds here too
        w.close_tab(w._tab_index(doc))
        w.open_file(path)
        again = w.doc
        assert again is not doc and not eng.attached(again) and len(again.state.parts) == PARTS
    finally:
        _end(w, before)


def test_part_studio_from_data_build_false_leaves_it_unbuilt(step_file, tmp_path):
    from test_import_stored_build import _imported
    _doc, path, _fid = _imported(step_file, tmp_path)
    cold = PartStudioDoc.load(path, build=False)
    assert not cold.state.parts and not cold._import_built
    cold.rebuild()
    assert len(cold.state.parts) == PARTS and not cold.errors


def test_engine_mode_document_open_builds_no_studio_in_the_window(tmp_path, monkeypatch):
    """A Document opened with the engine on: its Part Studios are left for the engine, and its Assembly and Drawing
    are not refreshed against their empty state (no 'not found' fallback); the engine's results fill them in."""
    from PySide6.QtWidgets import QApplication, QMessageBox
    from freeshape.document import PartStudioDoc as PSD
    from freeshape.ui import home as H
    from freeshape.ui import mainwindow as MW
    from test_live_updates import build_document
    from test_partstudio_engine import _children
    QApplication.instance() or QApplication([])
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    os.makedirs(tmp_path / "autosave", exist_ok=True)
    os.makedirs(tmp_path / "thumbs", exist_ok=True)
    monkeypatch.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(tmp_path / "autosave")))
    monkeypatch.setattr(MW.MainWindow, "autosave_dir", classmethod(lambda cls: str(tmp_path / "autosave")))
    monkeypatch.setattr(H, "thumbs_dir", lambda: str(tmp_path / "thumbs"))
    warned = []
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: warned.append(a[2:3]) or QMessageBox.Ok)
    path, ids = build_document(tmp_path)
    before = set(_children())
    w = MW.MainWindow(engine=True)
    from PySide6.QtCore import QSettings
    w.settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    built = []
    real = PSD.rebuild
    monkeypatch.setattr(PSD, "rebuild", lambda self: built.append(self.name) or real(self))
    try:
        eng = w.studio_engine
        w.open_document(path)
        studios = [w.folder.tab(ids[k]).doc for k in "AB"]
        asm = w.folder.tab(ids["C"]).doc
        assert not built                                     # no Part Studio built in the window
        assert all(eng.attached(d) and not d.state.parts for d in studios)
        assert not asm.errors                                # not refreshed against the empty studios
        assert eng.settle(240)
        assert not built and all(len(d.state.parts) == 1 for d in studios)
        assert len(asm.parts) == 2 and not asm.errors, asm.errors    # refreshed once the studios answered
        assert not warned
    finally:
        _end(w, before)
