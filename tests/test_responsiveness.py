"""I3.6: the window stays responsive while the engine rebuilds. A 10 ms timer counts ticks while a Part Studio
edit is rebuilt by a worker that sleeps for 2 s; the largest gap between ticks must stay under 250 ms, and the
ticks must run for the whole rebuild (a blocked GUI thread would show one long gap, or no ticks at all)."""
import os
import signal
import time

import pytest

from conftest import EXAMPLES_DIR
from freeshape.engine import process as P

BRACKET = os.path.join(EXAMPLES_DIR, "bracket.fshape")
SLEEP_S = 2.0
TICK_MS = 10
MAX_GAP_S = 0.250

# run in the worker (ProcessEngine.run_in_worker): every rebuild of its Part Studio takes SLEEP_S longer
SLOW_REBUILD = f"""
import time, freeshape.document as d
_rebuild = d.Document.rebuild
def slow(self):
    time.sleep({SLEEP_S})
    _rebuild(self)
d.Document.rebuild = slow
"""


def _spawned():
    me, out = os.getpid(), []
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                with open(f"/proc/{d}/stat") as fh:
                    rest = fh.read().rsplit(")", 1)[1].split()
                with open(f"/proc/{d}/cmdline", "rb") as fh:
                    cmd = fh.read()
                if int(rest[1]) == me and rest[0] != "Z" and b"spawn_main" in cmd:
                    out.append(int(d))
            except (OSError, ValueError, IndexError):
                pass
    return set(out)


class _Settings(dict):
    def value(self, k, default=None):
        return self.get(k, default)

    def setValue(self, k, v):
        self[k] = v


class Probe:
    """Counts the ticks of a 10 ms timer and the longest gap between two of them."""

    def __init__(self):
        from PySide6.QtCore import QTimer
        self.ticks, self.last, self.gaps = 0, None, []
        self.timer = QTimer()
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self._tick)

    def _tick(self):
        now = time.perf_counter()
        if self.last is not None:
            self.gaps.append(now - self.last)
        self.last = now
        self.ticks += 1

    def start(self):
        self.ticks, self.last, self.gaps = 0, None, []
        self.timer.start()

    def stop(self):
        self.timer.stop()

    @property
    def largest_gap(self):
        return max(self.gaps, default=float("inf"))


def test_gui_thread_keeps_ticking_during_a_two_second_rebuild_in_the_worker():
    os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")
    os.environ.setdefault("FREESHAPE_NO_HOME", "1")
    import copy
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    before = _spawned()
    w = MainWindow(engine=True)
    w.settings = _Settings()
    try:
        w.open_any(BRACKET)
        eng, doc = w.studio_engine, w.doc
        assert eng.settle(180)
        shown = eng.displayed(doc)
        eng.in_engine(doc, lambda e, tab: e.run_in_worker(tab, exec, SLOW_REBUILD, {}))

        probe = Probe()
        f = copy.deepcopy(doc.feature("F2"))
        f["params"]["depth"] = 52.0
        probe.start()
        t0 = time.perf_counter()
        doc.replace_feature(f)
        assert not eng.idle(), "the edit was rebuilt before it returned"
        assert eng.settle(60)
        took = time.perf_counter() - t0
        probe.stop()

        print(f"\nrebuild {took:.2f}s  ticks {probe.ticks}  largest gap {probe.largest_gap * 1000:.0f} ms")
        assert took >= SLEEP_S, "the worker's 2 s sleep was not part of this edit"
        assert eng.displayed(doc) != shown, "the edit's result was not drawn"
        # a 10 ms timer ticks ~100 times a second; far fewer would mean the GUI thread was busy or blocked
        assert probe.ticks >= 0.5 * took * 1000 / TICK_MS, (probe.ticks, took)
        assert probe.largest_gap <= MAX_GAP_S, f"the window froze for {probe.largest_gap * 1000:.0f} ms"
    finally:
        w.studio_engine.shutdown()
        P.SPARES.clear()
        left = _spawned() - before
        for p in left:
            try:
                os.kill(p, signal.SIGKILL)
            except OSError:
                pass
        assert not left, f"worker processes outlived the window: {sorted(left)}"
