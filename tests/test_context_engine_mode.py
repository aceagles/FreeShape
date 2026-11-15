"""F3: the Context in engine mode (the default of the real app). The worker builds the Context bodies; they must reach
the window as reference parts, so the Context features of S6 (select through, range and alpha, Parts list) work there
too. Every other S6 test runs with engine=False. Offscreen; waits on the worker with a timeout."""
import os
import signal
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")
os.environ.setdefault("FREESHAPE_NO_HOME", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from freeshape.engine import process as P  # noqa: E402
from freeshape.ui import mainwindow as MW  # noqa: E402
from freeshape.ui import panels as PN  # noqa: E402
from test_context_ui import make_rig  # noqa: E402
from test_engine_picking import _spawned  # noqa: E402
from test_select_other_studio import at  # noqa: E402

SETTLE = 180


@pytest.fixture
def rig(tmp_path, monkeypatch):
    class EngineWindow(MW.MainWindow):
        def __init__(self, *a, **k):
            super().__init__(*a, engine=True)
    monkeypatch.setattr(MW, "MainWindow", EngineWindow)
    before = set(_spawned())
    r = make_rig(tmp_path, monkeypatch)
    w = r.w
    assert w.studio_engine is not None
    try:
        assert w.studio_engine.settle(SETTLE)
        w.asm.edit_in_context()
        r.doc = r.plate.doc
        r.cid = next(f["id"] for f in r.doc.features if f["type"] == "context")
        assert w.doc is r.doc
        if not r.doc.listeners:
            r.doc.listeners.append(lambda: w._doc_changed_from(r.doc))
            r.doc.notify()
        assert w.studio_engine.settle(SETTLE)
        QApplication.processEvents()
        yield r
    finally:
        w.folder = None
        w.studio_engine.shutdown()
        P.SPARES.clear()
        for p in sorted(set(_spawned()) - before):
            try:
                os.kill(p, signal.SIGKILL)
            except OSError:
                pass


def bodies(r):
    return {f"{r.cid}/{i}" for i in (r.ia, r.ib, r.ic)}


def test_context_bodies_are_reference_parts_in_the_window(rig):
    r = rig
    parts = r.doc.state.parts
    assert bodies(r) <= set(parts)
    assert all(parts[b].reference for b in bodies(r))
    assert [p.id for p in parts.values() if not p.reference] and not any(
        parts[i].reference for i in parts if i not in bodies(r))


def test_a_context_body_is_in_reference_ids(rig):
    vp = rig.w.viewport
    assert bodies(rig) <= vp.reference_ids
    assert not (vp.reference_ids - bodies(rig))


def test_select_through_skips_a_context_body_in_engine_mode(rig):
    w, vp = rig.w, rig.w.viewport
    w.resize(1200, 800)
    w.show()
    QApplication.processEvents()
    # look straight down on the Plate (40 x 20 x 10) so Pin B (at z 40, above it) is between the camera and its top
    import numpy as np
    from freeshape.ui.camera import basis
    vp.cam.set_state((np.array([20.0, 10, 5]), basis((0, 0, 1), (0, 1, 0)), 0.1))
    vp.select_filter = vp.user_filter = None
    own = {p.id for p in rig.doc.state.parts.values() if not p.reference}
    x, y = at(w, 5, 5, 10)
    w.set_select_through_context(False, save=False)
    off = vp.pick(x, y, kinds={"face"})
    w.set_select_through_context(True, save=False)
    on = vp.pick(x, y, kinds={"face"})
    assert vp.pick_through_ids == bodies(rig)
    assert on is not None and on.key[0] in own
    assert off is not None and off.key[0] in bodies(rig)     # without it the Context body in front wins


def test_context_range_and_alpha_apply_in_engine_mode(rig):
    vp = rig.w.viewport
    b = vp._batch
    assert b["nctx"] > b["nsolid"]
    ctx_ranges = [vp._batch["ranges"][i] for i in bodies(rig) if i in vp._batch["ranges"]]
    assert ctx_ranges and all(b["nsolid"] <= a < b["nctx"] for a, *_rest in ctx_ranges)


def test_parts_list_marks_context_bodies_in_engine_mode(rig):
    lst = rig.w.left.parts
    seen = {}
    for i in range(lst.count()):
        it = lst.item(i)
        seen[it.data(PN.ROLE_ID)] = it
    assert bodies(rig) <= set(seen)
    for b in bodies(rig):
        assert seen[b].font().italic() and "Context body" in seen[b].toolTip()
    own = [it for pid, it in seen.items() if pid not in bodies(rig)]
    assert own and not any(it.font().italic() for it in own)
