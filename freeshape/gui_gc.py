"""Python's cyclic garbage collector runs only on the GUI (main) thread once Qt is up.

Python starts a collection on whichever thread happens to allocate past the threshold, and frees every Qt
object in the garbage there. A shown top-level widget (a Viewport, say) must be destroyed on the GUI thread:
from any other thread `QWidget::~QWidget` closes its window, which posts to the GUI thread and waits for it to
answer. The engine's executor threads (and the UI's own worker threads) allocate all the time, so sooner or
later one of them collects a widget while the GUI thread is blocked waiting on that very executor -- an engine
call waiting for its worker. Neither moves again (issue #72: test shards hanging at random).

So `install()` turns automatic collection off, and `collect_if_due()` -- called on the GUI thread by a timer
and by the engine as it waits -- runs the collection Python would have run, by Python's own thresholds. Worker
processes are headless and keep the default. pyqtgraph's GarbageCollector does the same, for the same reason.
"""
from __future__ import annotations

import gc
import sys
import threading

INTERVAL_MS = 500            # how often the GUI-thread timer looks at the allocation counts

_installed = False
_timer = None


def _qt_app():
    """The running Q(Core)Application, or None -- without importing Qt into a process that has none."""
    core = sys.modules.get("PySide6.QtCore")
    return None if core is None else core.QCoreApplication.instance()


def installed():
    return _installed


def install():
    """Collect only on the main thread from now on. Idempotent; call it on the main thread. Once a Qt
    application exists, a timer on it keeps collecting while the event loop runs."""
    global _installed, _timer
    if threading.current_thread() is not threading.main_thread():
        return
    if not _installed:
        gc.disable()
        _installed = True
    app = _qt_app()
    if _timer is None and app is not None:
        from PySide6.QtCore import QTimer
        _timer = QTimer(app)
        _timer.setInterval(INTERVAL_MS)
        _timer.timeout.connect(collect_if_due)
        _timer.start()


def collect_if_due():
    """On the main thread, after `install()`: the collection Python would have started by now, if any.
    Returns the generation collected, or None."""
    if not _installed or threading.current_thread() is not threading.main_thread():
        return None
    t0, t1, t2 = gc.get_threshold()
    c0, c1, c2 = gc.get_count()
    if t0 <= 0 or c0 < t0:
        return None
    gen = 2 if (c1 >= t1 and c2 >= t2) else 1 if c1 >= t1 else 0
    gc.collect(gen)
    return gen


def guard():
    """For code that starts threads which may allocate while the GUI thread waits on them (the engine): once
    a Qt application exists, install the guard; then collect if a collection is due."""
    if (not _installed or _timer is None) and _qt_app() is not None:
        install()
    collect_if_due()
