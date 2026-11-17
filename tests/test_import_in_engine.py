"""Importing a STEP file with the engine on: the parts are built in the tab's worker only, never on the window's
thread, and the window never waits on a worker for ever -- Cancel ends a stuck worker at once, and an engine that
fails during an import says so instead of quietly building the import in the window."""
import os
import signal
import sys
import threading
import time

import pytest

from freeshape import blobstore
from freeshape import features as F
from freeshape.engine import process as P
from freeshape.engine.process import TabErrored

from test_step_import_instances import COPIES, step_path  # noqa: F401 -- the module's STEP fixture
from test_partstudio_engine import _Settings, _children


def _window():
    os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")
    os.environ.setdefault("FREESHAPE_NO_HOME", "1")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow(engine=True)
    w.settings = _Settings()
    return w


def _close(w, before):
    w.studio_engine.shutdown()
    P.SPARES.clear()
    leftover = sorted(set(_children()) - before)
    for p in leftover:
        try:
            os.kill(p, signal.SIGKILL)
        except OSError:
            pass
    assert not leftover, f"worker processes outlived the window: {leftover}"


@pytest.fixture(scope="module")
def win():
    before = set(_children())
    w = _window()
    yield w
    _close(w, before)


def test_an_import_builds_in_the_worker_and_never_in_the_window(win, step_path, monkeypatch):  # noqa: F811
    from freeshape.ui import mainwindow as MW
    here, warned = [], []
    monkeypatch.setattr(F, "run_import", lambda *a: here.append(a))
    monkeypatch.setitem(F.RUNNERS, "import", F.run_import)
    monkeypatch.setattr(MW.QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    win.import_step_file(step_path)
    eng, doc = win.studio_engine, win.doc
    assert eng.settle(180)
    assert not warned and not doc.errors, (warned, doc.errors)
    assert not here                                   # the window's thread built nothing
    assert len(doc.state.parts) == COPIES
    assert len(eng.displayed(doc)) == COPIES          # every placed copy drawn from the worker's meshes


def _padded(src, dst, size):
    """`src` with a comment block that takes the file past `size` bytes: the same parts, a bigger file."""
    with open(src, "rb") as fh:
        head, rest = fh.read().split(b"\n", 1)
    line = b"x" * 1023 + b"\n"
    with open(dst, "wb") as fh:
        fh.write(head + b"\n/*\n" + line * (size // len(line) + 1) + b"*/\n" + rest)
    return dst


def test_an_import_over_the_blob_size_limit_still_reaches_the_worker(win, step_path, tmp_path, monkeypatch):  # noqa: F811
    from freeshape.ui import mainwindow as MW
    big = _padded(step_path, str(tmp_path / "big.step"), blobstore.DEFAULT_MAX_BYTES + 2 ** 20)
    warned = []
    monkeypatch.setattr(MW.QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    win.import_step_file(big)
    eng, doc = win.studio_engine, win.doc
    assert eng.settle(180)
    assert not warned and not doc.errors and not eng.broken, (warned, doc.errors)
    assert len(doc.state.parts) == COPIES


def test_cancel_ends_a_stuck_worker_and_the_tab_builds_again(win, step_path):  # noqa: F811
    eng = win.studio_engine
    if win.doc is None or not win.doc.features:
        win.import_step_file(step_path)
        assert eng.settle(180)
    doc = win.doc
    shown = eng.displayed(doc)
    tab = eng.in_engine(doc, lambda e, t: t)
    pid = eng.in_engine(doc, lambda e, t: e.pid(t))
    stuck = eng._pool.submit(lambda: eng._engine.run_in_worker(tab, time.sleep, 120))   # a worker that won't answer
    time.sleep(0.5)
    doc.rebuild()                                     # a build now queues behind it
    t0 = time.monotonic()
    assert eng.wait(doc, cancelled=lambda: True) is False
    assert eng.settle(120)
    assert time.monotonic() - t0 < 60                 # ended, not waited out
    with pytest.raises(TabErrored):
        stuck.result(5)
    assert eng.in_engine(doc, lambda e, t: e.pid(t)) != pid
    assert eng.displayed(doc) == shown and not eng.broken


def test_abort_wakes_a_caller_waiting_on_a_stuck_worker():
    eng = P.ProcessEngine(spares=0)
    pid = None
    try:
        tab = eng.new_tab("studio", name="t")
        pid = eng.pid(tab)
        got = []
        th = threading.Thread(target=lambda: got.append(_catch(lambda: eng.run_in_worker(tab, time.sleep, 120))))
        th.start()
        time.sleep(1.0)
        t0 = time.monotonic()
        eng.abort(tab)
        th.join(30)
        assert not th.is_alive() and time.monotonic() - t0 < 30
        assert isinstance(got[0], TabErrored)
    finally:
        eng.close()
        if pid:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


def _catch(fn):
    try:
        return fn()
    except BaseException as e:      # noqa: BLE001
        return e


def test_an_engine_failure_during_an_import_says_so_and_builds_nothing_in_the_window(step_path, monkeypatch):  # noqa: F811
    before = set(_children())
    w = _window()
    try:
        from freeshape.ui import mainwindow as MW
        warned, here = [], []
        monkeypatch.setattr(MW.QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
        monkeypatch.setattr(F, "run_import", lambda *a: here.append(a))
        monkeypatch.setitem(F.RUNNERS, "import", F.run_import)
        eng = w.studio_engine

        def broken(*a, **k):
            raise RuntimeError("worker gone")
        monkeypatch.setattr(eng, "_decode", broken)
        w.import_step_file(step_path)
        assert eng.broken
        assert warned and "engine failed" in warned[0]
        assert not any(f["type"] == "import" for f in w.doc.features)
        assert not here
    finally:
        _close(w, before)


def test_a_saved_import_over_the_blob_size_limit_opens_again(step_path, tmp_path):  # noqa: F811
    import base64
    from freeshape.document import Document
    from conftest import add
    big = _padded(step_path, str(tmp_path / "big.step"), blobstore.DEFAULT_MAX_BYTES + 2 ** 20)
    doc = Document()
    with open(big, "rb") as fh:
        add(doc, "import", data=base64.b64encode(fh.read()).decode("ascii"), filename="big.step")
    path = str(tmp_path / "a.fsp")
    doc.save(path)
    again = Document.load(path)
    again.rebuild()
    assert not again.errors, again.errors
    assert len(again.state.parts) == COPIES


@pytest.mark.skipif(sys.platform == "darwin", reason="no CPU-time reader on macOS: the watchdog is off there")
def test_a_worker_that_stops_working_is_ended_and_the_call_says_so():
    eng = P.ProcessEngine(spares=0, stall_s=4.0)
    pid = None
    try:
        tab = eng.new_tab("studio", name="t")
        pid = eng.pid(tab)
        t0 = time.monotonic()
        with pytest.raises(TabErrored, match="stopped answering"):
            eng.run_in_worker(tab, time.sleep, 120)          # alive, but doing nothing
        assert time.monotonic() - t0 < 30
    finally:
        eng.close()
        if pid:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


def test_a_busy_worker_is_not_ended_however_long_it_takes(monkeypatch):
    monkeypatch.setattr(P, "WATCH_EVERY_S", 0.5)
    eng = P.ProcessEngine(spares=0, stall_s=2.0)
    try:
        tab = eng.new_tab("studio", name="t")
        assert eng.run_in_worker(tab, _spin, 5.0) > 0       # five seconds of work, past the 2 s stall limit
    finally:
        eng.close()


@pytest.mark.skipif(sys.platform == "darwin", reason="no CPU-time reader on macOS: the watchdog is off there")
def test_the_cpu_time_of_this_process_can_be_read():
    assert P.cpu_seconds(os.getpid()) is not None


def _spin(seconds):
    end, n = time.monotonic() + seconds, 0
    while time.monotonic() < end:
        n += 1
    return n
