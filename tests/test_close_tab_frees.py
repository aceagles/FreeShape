"""Closing a Part Studio tab frees what it held (#63, step 0): once the tab is closed and gc has run, nothing in the
window keeps its PartStudioDoc or its Parts alive -- not the last state drawn (`MainWindow._shown`), the viewport's
mesh caches, nor the kernel's module caches. Driven with the engine on, as the app runs."""
import gc
import os
import signal
import types
import weakref

import pytest

from freeshape.engine import process as P

from test_step_import_instances import COPIES, step_path  # noqa: F401 -- the module's STEP fixture
from test_partstudio_engine import BRACKET, _Settings, _children


def _window():
    os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")
    os.environ.setdefault("FREESHAPE_NO_HOME", "1")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow(engine=True)
    w.settings = _Settings()
    return w


def _shut(w, before):
    w.studio_engine.shutdown()
    P.SPARES.clear()
    for p in sorted(set(_children()) - before):
        try:
            os.kill(p, signal.SIGKILL)
        except OSError:
            pass


def _pump(seconds=0.5):
    import time
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        time.sleep(0.02)


def _label(holder, target):
    """How `holder` refers to `target`, for a failure message."""
    if isinstance(holder, dict):
        for k, v in holder.items():
            if v is target:
                return f"dict[{k!r:.60}]"
            if k is target:
                return "dict key"
        return "dict"
    if isinstance(holder, tuple) and len(holder) <= 4:
        return "(" + ", ".join(type(x).__name__ for x in holder) + ")"
    if isinstance(holder, (list, tuple, set, frozenset)):
        return f"{type(holder).__name__}(len {len(holder)})"
    if isinstance(holder, types.FunctionType):
        return f"function {holder.__qualname__}"
    if isinstance(holder, types.CellType):
        fns = [f for f in gc.get_objects() if isinstance(f, types.FunctionType)
               and any(c is holder for c in (f.__closure__ or ()))]
        return f"closure cell of {fns[0].__module__}.{fns[0].__qualname__}" if fns else "closure cell"
    return f"{type(holder).__module__}.{type(holder).__qualname__}"


def _holds(c, obj, depth=3):
    """True if untracked container `c` holds `obj`, directly or through untracked containers."""
    if depth == 0:
        return False
    items = list(c.keys()) + list(c.values()) if isinstance(c, dict) else c
    for x in items:
        if x is obj:
            return True
        if type(x) in (tuple, dict, list) and not gc.is_tracked(x) and _holds(x, obj, depth - 1):
            return True
    return False


def _referrers(obj):
    """gc.get_referrers, plus the tracked objects that hold `obj` through containers gc does not track: CPython
    stops tracking a tuple or dict of untracked objects (a cache's (shape, mesh), or a dict of them), which
    hides it from get_referrers."""
    out = list(gc.get_referrers(obj))
    if gc.is_tracked(obj):
        return out
    for o in gc.get_objects():
        for r in gc.get_referents(o):
            if type(r) in (tuple, dict, list) and not gc.is_tracked(r) and _holds(r, obj):
                out.append(o)
                break
    return out


def _chains(target, depth=10, budget=150, limit=6):
    """Up to `limit` chains of referrers from `target` back to a module or a class: what keeps it alive. Each
    gc.get_referrers call scans the whole heap, so the search stops after `budget` of them."""
    roots, seen, out, leaves = (types.ModuleType, type), {id(target)}, [], []
    objs, paths = [target], [_label(target, None)]      # the search's own lists, skipped as referrers
    for _ in range(depth):
        nobjs, npaths = [], []
        for k in range(len(objs)):          # (not zip: its (obj, path) tuples would be referrers too)
            obj, path = objs[k], paths[k]
            if budget <= 0:
                return out or ["(search budget spent; partial chains)"] + npaths[:5]
            budget -= 1
            found = False
            for r in _referrers(obj):
                if r is objs or r is nobjs or id(r) in seen:
                    continue
                found = True
                if isinstance(r, types.FrameType):
                    leaves.append(path + f" <- frame of {r.f_code.co_qualname} ({r.f_code.co_filename}:{r.f_lineno})")
                    continue
                seen.add(id(r))
                p = path + " <- " + _label(r, obj)
                if isinstance(r, roots) or (isinstance(r, dict) and "__name__" in r and "__file__" in r):
                    out.append(p)
                    if len(out) >= limit:
                        return out
                    continue
                nobjs.append(r)
                npaths.append(p)
            if not found:
                leaves.append(path + " <- (nothing gc can see: C++)")
            del obj
        objs, paths = nobjs[:40], npaths[:40]
        if not objs:
            break
    return out or ["(no chain to a module; deepest holders:)"] + leaves[-limit:] + paths[:limit]


def _assert_dead(ref, what):
    gc.collect()
    obj = ref()
    if obj is None:
        return
    chains = _chains(obj)
    del obj
    pytest.fail(f"{what} is still alive after its tab closed:\n  " + "\n  ".join(chains))


def _close_and_check(w, doc_key=None):
    """Close a Part Studio tab (the current one, or the one under `doc_key`) and check that its document, a part,
    the part's shape and the mesh drawn for it die. The caller holds no reference to any of them."""
    doc = w.docs[doc_key] if doc_key is not None else w.doc
    i = w._tab_index(doc)
    assert i >= 0
    part = next(iter(doc.state.parts.values()))
    hit = w.viewport._mesh_cache.get(("part", hash(part.shape)))
    assert hit is not None, "the part was never drawn"
    refs = {"the PartStudioDoc": weakref.ref(doc), "a Part": weakref.ref(part),
            "the Part's shape": weakref.ref(part.shape), "the Part's mesh": weakref.ref(hit[1].verts)}
    del part, doc, hit
    w.close_tab(i)
    w.studio_engine.settle(60)
    _pump()
    for what, ref in refs.items():
        _assert_dead(ref, what)


@pytest.fixture()
def win(monkeypatch):
    from freeshape.ui import mainwindow as MW
    for name in ("question", "warning", "information", "critical"):     # never a modal box offscreen
        monkeypatch.setattr(MW.QMessageBox, name, staticmethod(lambda *a, **k: MW.QMessageBox.Discard))
    before = set(_children())
    w = _window()
    yield w
    _shut(w, before)


def test_closing_an_imported_studio_frees_its_document_and_parts(win, step_path):  # noqa: F811
    win.import_step_file(step_path)
    assert win.studio_engine.settle(180)
    assert len(win.doc.state.parts) == COPIES
    win.viewport.update()
    _pump()
    _close_and_check(win)


def test_closing_a_modelled_studio_frees_its_document_and_parts(win):
    win.open_any(BRACKET)
    assert win.studio_engine.settle(180)
    assert win.doc.state.parts, win.doc.errors
    from freeshape import kernel as K
    assert K.volume(next(iter(win.doc.state.parts.values())).shape) > 0     # puts the shape in K._VOLUME_CACHE
    win.viewport.update()
    _pump()
    _close_and_check(win)


def test_closing_a_studio_while_an_assembly_is_shown_next_frees_it(win):
    """The window's last drawn studio state (`_shown`) is not redrawn when an Assembly becomes current."""
    win.open_any(BRACKET)
    assert win.studio_engine.settle(180)
    key = win.tabs.tabData(win.tabs.currentIndex())
    win.viewport.update()
    _pump()
    win.new_assembly()
    _pump()
    assert win.is_asm
    _close_and_check(win, key)
    assert win.is_asm
