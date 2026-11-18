"""Cyclic garbage is collected on the GUI (main) thread only (`freeshape.gui_gc`, issue #72).

Freed on a worker thread, a shown top-level widget waits in its destructor for the GUI thread to close its
window. If the GUI thread is itself waiting on that worker -- an engine call waiting on its pool, whose threads
allocate and so trigger collections -- neither moves again."""
import gc
import os
import threading
import weakref

import pytest

from freeshape import gui_gc


class _Cycle:
    pass


def _cyclic_garbage(payload=None):
    """An object kept alive only by a reference cycle; returns a weakref to it."""
    a, b = _Cycle(), _Cycle()
    a.other, b.other, a.payload = b, a, payload
    return weakref.ref(a)


def _allocate_on_a_worker_thread(kept, n=200_000):
    """Allocate far past the collection threshold on another thread, keeping it all in `kept` (freed objects
    would take back their count); True if it finished within 30 s."""
    def churn():
        for i in range(n):
            kept.append([i])            # containers: each one counts towards a collection
    th = threading.Thread(target=churn, name="engine-like worker", daemon=True)
    th.start()
    th.join(30)
    return not th.is_alive()


def test_a_worker_thread_never_collects_and_the_main_thread_does():
    gui_gc.install()
    gc.collect()
    ref = _cyclic_garbage()
    assert ref() is not None
    kept = []
    assert _allocate_on_a_worker_thread(kept)
    assert ref() is not None, "a worker thread ran a collection"
    assert gui_gc.collect_if_due() is not None          # the worker's allocations made one due, here
    kept.clear()
    gc.collect()
    assert ref() is None


def test_collect_if_due_does_nothing_off_the_main_thread_or_below_the_threshold():
    gui_gc.install()
    gc.collect()
    assert gui_gc.collect_if_due() is None              # nothing allocated since
    out = []
    th = threading.Thread(target=lambda: out.append(gui_gc.collect_if_due()))
    th.start()
    th.join()
    assert out == [None]


@pytest.mark.skipif(not os.environ.get("QT_QPA_PLATFORM") and not os.environ.get("DISPLAY"),
                    reason="needs a Qt platform (offscreen in the harness)")
def test_a_shown_viewport_in_garbage_does_not_stop_a_worker_thread():
    from PySide6.QtWidgets import QApplication

    from freeshape.ui.viewport import Viewport
    app = QApplication.instance() or QApplication([])
    gui_gc.guard()
    v = Viewport()
    v.resize(120, 90)
    v.show()
    app.processEvents()
    ref = _cyclic_garbage(v)             # the widget is held only by garbage, as a closed window's can be
    del v
    assert _allocate_on_a_worker_thread([]), "a worker thread is stuck freeing the Viewport"
    gc.collect()                         # here, on the GUI thread, it closes and goes
    app.processEvents()
    assert ref() is None
