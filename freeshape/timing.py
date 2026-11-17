"""Stage timings for opening a Document, printed when FREESHAPE_TIMING=1 (issue #52).

    [timing]   0.412 s  +0.031  open: document loaded (thread MainThread)

The first number is seconds since the last `begin` (the click), the second since the previous mark. Off by
default, and then each call costs one flag check. `marks()` keeps the lines too, for the --open-timing run.
"""
import os
import sys
import threading
import time

ON = os.environ.get("FREESHAPE_TIMING", "") not in ("", "0")
_t0 = time.perf_counter()
_last = _t0
_marks = []
_lock = threading.Lock()


def enable(on=True):
    global ON
    ON = on


def begin(stage):
    """Start the clock again (a click) and mark `stage` at 0."""
    global _t0, _last
    if not ON:
        return
    with _lock:
        _t0 = _last = time.perf_counter()
    mark(stage)


def mark(stage):
    global _last
    if not ON:
        return
    now = time.perf_counter()
    with _lock:
        line = (f"[timing] {now - _t0:8.3f} s  +{now - _last:.3f}  {stage} "
                f"(thread {threading.current_thread().name})")
        _last = now
        _marks.append((now - _t0, stage))
    if sys.stderr is not None:          # a windowed .exe has no stderr
        try:
            print(line, file=sys.stderr, flush=True)
        except (OSError, ValueError):
            pass


def since_begin():
    return time.perf_counter() - _t0


def marks():
    with _lock:
        return list(_marks)
