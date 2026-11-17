"""memtrim.release_memory() hands freed heap memory back to the OS (#68): it runs on every CI OS without raising,
the engine worker calls it after an import-sized `outputs` but not after a small edit, and `--import-timing`
reports the steady memory of the window and the worker."""
import math
import os
import re
import signal
import sys

from freeshape import memtrim


def test_release_memory_returns_without_error():
    ran = memtrim.release_memory()
    assert ran in (True, False)
    if sys.platform.startswith("linux") or sys.platform in ("win32", "darwin"):
        assert ran, "the platform's release call was not found"
    assert memtrim.release_memory() == ran      # the second call reuses the function it found


def test_release_memory_is_off_with_the_switch(monkeypatch):
    monkeypatch.setenv("FREESHAPE_NO_MEMTRIM", "1")
    assert memtrim.release_memory() is False


def test_release_memory_swallows_a_failing_call(monkeypatch):
    def boom():
        raise OSError("no allocator here")
    monkeypatch.setattr(memtrim, "_fn", boom)
    assert memtrim.release_memory() is False


class _FakeWorker:
    def __init__(self):
        self.engine = self

    def install(self, upstream, key):
        pass

    def add_blobs(self, blobs):
        pass

    def call(self, op, arg):
        return op


def test_worker_releases_after_a_big_outputs_only(monkeypatch):
    from freeshape.engine import worker as W
    released = []
    monkeypatch.setattr(W, "_state", _FakeWorker())
    monkeypatch.setattr(W, "release_memory", lambda: released.append(1))
    monkeypatch.setattr(W, "_busy_s", 0.0)
    W.call("edit", {})
    W.call("outputs")
    assert released == [], "a small edit paid for a release"
    monkeypatch.setattr(W, "_busy_s", W.RELEASE_AFTER_S)      # as after an import's edits
    W.call("edit", {})
    assert released == [], "released before the outputs were read"
    W.call("outputs")
    assert released == [1]
    assert W._busy_s == 0.0
    W.call("outputs")
    assert released == [1]


def test_import_timing_reports_steady_memory(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from freeshape import import_timing
    from freeshape.engine import process as P
    from test_partstudio_engine import _children
    for name in ("FREESHAPE_DATA_DIR", "FREESHAPE_NO_RECOVERY"):       # run() sets these: put them back after
        monkeypatch.setenv(name, os.environ.get(name, ""))
    for name in ("warning", "critical", "information", "question"):     # and patches these
        monkeypatch.setattr(QMessageBox, name, getattr(QMessageBox, name))
    before = set(_children())
    out = tmp_path / "out.txt"
    try:
        rc = import_timing.run(str(out), "synthetic:10", steady_wait=0.2)
    finally:
        P.SPARES.clear()
        leftover = sorted(set(_children()) - before)
        for p in leftover:
            try:
                os.kill(p, signal.SIGKILL)
            except OSError:
                pass
    text = out.read_text()
    assert rc == 0, text
    names = {"window_steady_mb", "worker_steady_mb", "after_close_mb", "idle_mb"}
    nums = {k: float(v) for k, v in re.findall(r"(\w+_mb)=(\S+)", text) if k in names}
    assert set(nums) == names, text
    if sys.platform != "darwin":        # tree_mb reads /proc or the Windows process list; macOS has neither
        assert all(v > 0 and not math.isnan(v) for v in nums.values()), text
