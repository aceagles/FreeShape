"""#52: opening a Document is quick. On Windows the slow part was git (a console made for every call from the
windowed .exe); then the window's blank Part Studio took the warm worker the opened Document should have had.

Each test builds what it needs (CI runs every third test on its own)."""
import os

import pytest

from conftest import add, rect_lines, region_refs, sketch_feature
from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import gitstore as G
from freeshape.engine import process as P

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

CREATE_NO_WINDOW = 0x08000000


# ------------------------------------------------------------------ git

def test_git_runs_without_a_console_on_windows(monkeypatch):
    assert G._no_console("win32") == {"creationflags": CREATE_NO_WINDOW}
    assert G._no_console("linux") == {} and G._no_console("darwin") == {}
    seen = []

    class Done:
        returncode, stdout, stderr = 0, "", ""

    monkeypatch.setattr(G.subprocess, "run", lambda argv, **kw: seen.append(kw) or Done())
    monkeypatch.setattr(G, "_no_console", lambda: {"creationflags": CREATE_NO_WINDOW})
    G._run(["status"], None, False, True, True)
    assert seen[0]["creationflags"] == CREATE_NO_WINDOW


# ------------------------------------------------------------------ the window

@pytest.fixture
def window(tmp_path, monkeypatch):
    """make(engine) -> an offscreen MainWindow using only tmp_path; ended (with every worker) after the test."""
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    from PySide6.QtCore import QSettings
    from freeshape.ui import home as H
    from freeshape.ui import mainwindow as MW
    (tmp_path / "autosave").mkdir()
    (tmp_path / "thumbs").mkdir()
    monkeypatch.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(tmp_path / "autosave")))
    monkeypatch.setattr(MW.MainWindow, "autosave_dir", classmethod(lambda cls: str(tmp_path / "autosave")))
    monkeypatch.setattr(H, "thumbs_dir", lambda: str(tmp_path / "thumbs"))
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.Ok)
    made = []

    def make(engine):
        w = MW.MainWindow(engine=engine)
        w.settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
        made.append(w)
        return w

    yield make
    for w in made:
        if w.studio_engine is not None:
            w.studio_engine.shutdown()
    P.SPARES.clear()


def _document(tmp_path, name="Test Shapes"):
    """A Document like the one in #52: one empty Part Studio."""
    fd = DF.FolderDocument.create(str(tmp_path / name), name, registry=R.default_registry())
    fd.add_tab("studio", "Part Studio 1")
    fd.save()
    return fd.path


def test_opening_a_document_makes_few_git_calls(window, tmp_path):
    path = _document(tmp_path)
    w = window(False)
    with G.recording() as calls:
        assert w.open_document(path) is not None
    assert len(calls) <= 3, [c["argv"][7:] for c in calls]


@pytest.fixture
def pools(monkeypatch):
    """Every worker pool started, in order."""
    started = []
    real = P._new_pool

    def new_pool(ctx):
        pool = real(ctx)
        started.append(pool)
        return pool

    monkeypatch.setattr(P, "_new_pool", new_pool)
    return started


def _engine_tabs(w):
    eng = w.studio_engine._engine
    return {} if eng is None else dict(eng._tabs)


def test_the_blank_tab_takes_no_worker_and_the_document_takes_the_warm_one(window, tmp_path, pools):
    P.SPARES.clear()
    path = _document(tmp_path)
    w = window(True)
    assert w.studio_engine.settle(120)
    w.studio_engine._pool.submit(lambda: None).result()      # the prewarm has run on the engine thread
    assert len(pools) == 1, "start-up warms one worker, for the next tab opened"
    assert _engine_tabs(w) == {}, "the blank Part Studio has nothing to build"
    assert w.open_document(path) is not None
    assert w.studio_engine.settle(120)
    tabs = list(_engine_tabs(w).values())
    assert len(tabs) == 1 and tabs[0].pool is pools[0], "the Document's tab took the warm worker"
    assert len(pools) == 2, "and one more is warming for the tab after it"


def test_a_blank_tab_builds_in_a_worker_at_its_first_edit(window, pools):
    P.SPARES.clear()
    w = window(True)
    doc = w.doc
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 10, 10))
    assert w.studio_engine.settle(120)          # the sketch's regions come back from the worker
    add(doc, "extrude", regions=region_refs(doc, sid), depth=5.0, op="new")
    assert w.studio_engine.settle(120)
    assert len(_engine_tabs(w)) == 1
    assert len(w.studio_engine.displayed_parts(doc)) == 1


def test_prewarm_off_starts_no_worker(window, pools, monkeypatch):
    P.SPARES.clear()
    monkeypatch.setenv("FREESHAPE_PREWARM", "0")
    w = window(True)
    assert w.studio_engine.settle(120)
    w.studio_engine._pool.submit(lambda: None).result()
    assert pools == []
