"""I3.6: the Part Studio tab as an engine client (freeshape/ui/partstudio_engine.py): edits go to the process
executor, the meshes it returns are what the tab draws, and no worker outlives the window.

One window (and so one worker per tab) serves the whole module; each test puts the model back as it found it."""
import copy
import os
import signal
import pytest

from conftest import EXAMPLES_DIR
from freeshape import kernel as K
from freeshape.engine import process as P
from freeshape.ui.partstudio_engine import kernel_mesh, plan_edits

BRACKET = os.path.join(EXAMPLES_DIR, "bracket.fshape")


def _cmdline(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return fh.read()
    except OSError:
        return b""


def _children():
    me, out = os.getpid(), []
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                with open(f"/proc/{d}/stat") as fh:
                    rest = fh.read().rsplit(")", 1)[1].split()
                if int(rest[1]) == me and rest[0] != "Z" and b"spawn_main" in _cmdline(d):
                    out.append(int(d))     # a spawned worker (not the multiprocessing resource tracker)
            except (OSError, ValueError, IndexError):
                pass
    return out


class _Settings(dict):
    def value(self, k, default=None):
        return self.get(k, default)

    def setValue(self, k, v):
        self[k] = v


@pytest.fixture(scope="module")
def win():
    os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")
    os.environ.setdefault("FREESHAPE_NO_HOME", "1")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    before = set(_children())
    w = MainWindow(engine=True)
    w.settings = _Settings()
    w.open_any(BRACKET)
    assert w.studio_engine.settle(180)
    w.spawned = lambda: sorted(set(_children()) - before)
    yield w
    w.studio_engine.shutdown()
    P.SPARES.clear()
    leftover = w.spawned()
    for p in leftover:
        try:
            os.kill(p, signal.SIGKILL)
        except OSError:
            pass
    assert not leftover, f"worker processes outlived the window: {leftover}"


def _depth(doc, value):
    f = copy.deepcopy(doc.feature("F2"))
    f["params"]["depth"] = value
    return f


def _volume(doc):
    return sum(K.volume(p.shape) for p in doc.state.parts.values())


# =========================================================================== plan_edits (pure)

def _f(i, **p):
    return {"id": f"F{i}", "type": "extrude", "name": f"E{i}", "suppressed": False, "params": p}


def test_plan_edits_replace_add_delete_move_and_rollback():
    a, b, c = _f(1, d=1), _f(2, d=2), _f(3, d=3)
    have = {"features": [a, b, c], "rollback": None}
    assert plan_edits(have, have) == []
    b2 = _f(2, d=9)
    assert plan_edits(have, {"features": [a, b2, c], "rollback": None}) == [{"op": "replace_feature", "feature": b2}]
    assert plan_edits(have, {"features": [a, c], "rollback": None}) == [{"op": "delete_features", "ids": ["F2"]}]
    n = _f(4)
    edits = plan_edits(have, {"features": [a, n, b, c], "rollback": 3})
    assert [e["op"] for e in edits][-1] == "set_rollback" and edits[-1]["index"] == 3
    assert any(e["op"] == "add_feature" and e["feature"]["id"] == "F4" and e["index"] == 1 for e in edits)
    moved = plan_edits(have, {"features": [b, a, c], "rollback": None})
    assert moved[0]["op"] == "delete_features"
    # replaying the plan on a model of the engine's rules reaches the wanted list
    def run(have, edits):
        cur = list(have["features"])
        for e in edits:
            if e["op"] == "delete_features":
                cur = [f for f in cur if f["id"] not in e["ids"]]
            elif e["op"] == "replace_feature":
                cur = [e["feature"] if f["id"] == e["feature"]["id"] else f for f in cur]
            elif e["op"] == "add_feature":
                cur.insert(e["index"], e["feature"])
        return cur
    for want in ([b, a, c], [c, b, a], [a], [], [n, a, b, c], [a, b2, c, n]):
        assert run(have, plan_edits(have, {"features": want, "rollback": None})) == want
    assert plan_edits({"features": [a], "rollback": 1}, {"features": [a], "rollback": None}) == [
        {"op": "set_rollback", "index": None}]


# =========================================================================== through the window

def test_tab_is_an_engine_client_and_draws_the_workers_mesh(win):
    eng, doc = win.studio_engine, win.doc
    assert eng.attached(doc) and "rebuild" in doc.__dict__
    assert win.spawned(), "no worker process: the tab is not rebuilding in the engine"
    assert eng.displayed(doc) and set(eng.displayed_parts(doc)) == set(doc.state.parts)
    # what the viewport holds is the mesh the worker made: its triangles, not a copy meshed here
    shown = {pid: m for pid, m, _ in win.viewport.parts}
    assert shown
    for pid, h in eng.displayed(doc).items():
        from freeshape.ui.partstudio_engine import mesh_hash
        assert mesh_hash(shown[pid]) == h


def test_editing_a_dimension_changes_the_displayed_mesh_hash(win, monkeypatch):
    eng, doc = win.studio_engine, win.doc
    before, parts_before, vol = eng.displayed(doc), eng.displayed_parts(doc), _volume(doc)
    assert before

    def no_local_rebuild(*a, **k):
        raise AssertionError("the edit was rebuilt on the GUI thread")
    monkeypatch.setattr(type(doc), "compute", no_local_rebuild)
    doc.replace_feature(_depth(doc, 55.0))             # what a committed feature dialog does
    assert eng.settle(120)
    monkeypatch.undo()
    after = eng.displayed(doc)
    assert after and after != before
    assert eng.displayed_parts(doc) != parts_before
    assert _volume(doc) > vol and not doc.errors
    from freeshape.ui.partstudio_engine import mesh_hash
    assert {pid: mesh_hash(m) for pid, m, _ in win.viewport.parts} == after
    doc.undo()                                          # back to the file's own model
    assert eng.settle(120)
    assert eng.displayed(doc) == before and eng.displayed_parts(doc) == parts_before


def test_feature_edits_add_rollback_and_delete_reach_the_engine(win):
    eng, doc = win.studio_engine, win.doc
    n = len(doc.features)
    base = eng.displayed(doc)
    doc.set_rollback(2)                                 # only the profile and the first extrude
    assert eng.settle(120)
    assert eng.displayed(doc) != base
    doc.set_rollback(None)
    assert eng.settle(120)
    assert eng.displayed(doc) == base
    doc.delete_features({"F8"})                         # the chamfer
    assert eng.settle(120)
    assert eng.displayed(doc) != base and len(doc.features) == n - 1
    doc.undo()
    assert eng.settle(120)
    assert eng.displayed(doc) == base and len(doc.features) == n


def test_a_killed_worker_is_replaced_and_the_edit_still_lands(win):
    eng, doc = win.studio_engine, win.doc
    base = eng.displayed(doc)
    pid = eng.in_engine(doc, lambda e, tab: e.pid(tab))
    os.kill(pid, signal.SIGKILL)
    doc.replace_feature(_depth(doc, 48.0))
    assert eng.settle(180)
    assert eng.displayed(doc) != base and not doc.errors
    assert eng.in_engine(doc, lambda e, tab: e.pid(tab)) != pid
    doc.undo()
    assert eng.settle(120)
    assert eng.displayed(doc) == base


def test_kernel_mesh_face_ranges_cover_each_faces_triangles(win):
    doc = win.doc
    out = win.studio_engine.in_engine(doc, lambda e, tab: e.outputs(tab)).parts[0]
    nfaces = len(K.faces(K.shape_from_brep(out.brep)))
    m = kernel_mesh(out.mesh, nfaces)
    assert len(m.face_ranges) == nfaces and m.face_ranges[-1][1] == len(m.tris)
    for i, (a, b) in enumerate(m.face_ranges):
        assert (m.tri_face[a:b] == i).all()
