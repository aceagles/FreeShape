"""I3.7: picking and highlighting in an engine-backed Part Studio tab go through element names.

pick -> 1-based sub-shape index on the returned part (TopExp.MapShapes order) -> name from the part's
element-name map (what the engine returned); highlight -> name -> index -> that face's triangle range in the
mesh the engine returned. One window (one worker) serves the module; each test puts the model back."""
import copy
import os
import signal

import numpy as np
import pytest

from conftest import EXAMPLES_DIR
from freeshape import features as F
from freeshape import kernel as K
from freeshape.engine import InProcessEngine
from freeshape.engine import process as P
from freeshape.ui import mainwindow as mw_mod
from freeshape.ui.dialogs import pick_to_item
from freeshape.ui.partstudio_engine import element_index, element_name
from freeshape.ui.viewport import Pick

BRACKET = os.path.join(EXAMPLES_DIR, "bracket.fshape")


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
    before = set(_spawned())
    w = mw_mod.MainWindow(engine=True)
    w.settings = _Settings()
    try:
        w.open_any(BRACKET)
        assert w.studio_engine.settle(180)
        yield w
    finally:
        w.studio_engine.shutdown()
        P.SPARES.clear()
        leftover = sorted(set(_spawned()) - before)
        for p in leftover:
            try:
                os.kill(p, signal.SIGKILL)
            except OSError:
                pass
        assert not leftover, f"worker processes outlived the window: {leftover}"


@pytest.fixture(scope="module")
def reference():
    """The in-process executor's outputs for the same file: {part id: PartOut}."""
    with InProcessEngine() as eng:
        tab = eng.open_tab(BRACKET)
        return {p.id: p for p in eng.outputs(tab).parts}


def _pick_at_face(win, kind="face", skip=0):
    """A real viewport pick (ray cast through the displayed mesh) at the centre of a displayed triangle."""
    vp = win.viewport
    pid, m, _c = vp.parts[0]
    seen = 0
    for ti in range(0, len(m.tris), max(1, len(m.tris) // 40)):
        c = m.verts[m.tris[ti]].mean(axis=0)
        x, y, _z = vp.cam.to_screen(np.asarray([c], float))[0]
        pk = vp.pick(float(x), float(y), kinds={kind})
        if pk is not None and pk.kind == kind:
            if seen == skip:
                return pk
            seen += 1
    pytest.fail("no pick found")


def test_picked_face_name_equals_the_in_process_name_at_that_index(win, reference):
    doc, vp = win.doc, win.viewport
    for skip in range(3):
        pk = _pick_at_face(win, "face", skip)
        pid, fi = pk.key
        assert doc.state.engine_names[pid]["face"], "the tab holds no engine name map"
        kind, ref = pick_to_item(vp, pk, {"face"}, doc.state)
        want = reference[pid].names["face"][fi + 1]
        assert ref["name"] == want == element_name(doc.state, pid, "face", fi + 1)
        assert element_index(doc.state, pid, "face", want) == fi + 1
        # the index rule: the picked face is the (fi + 1)-th in MapShapes order of the returned part
        assert K.face_info(K.faces(doc.state.parts[pid].shape)[fi])["area"] == pytest.approx(
            K.face_info(K.faces(K.shape_from_brep(reference[pid].brep))[fi])["area"])
    for kind, names in (("edge", "edge"), ("vertex", "vertex")):
        pk = vp.pick(*[float(v) for v in _screen_of(vp, kind)], kinds={kind})
        assert pk is not None and pk.kind == kind
        item = pick_to_item(vp, pk, {kind}, doc.state)
        assert item[1]["name"] == reference[pk.key[0]].names[names][pk.key[1] + 1]


def _screen_of(vp, kind):
    pid, m, _c = vp.parts[0]
    p = m.vertices[0] if kind == "vertex" else np.asarray(m.edge_polys[0]).mean(axis=0)
    return vp.cam.to_screen(np.asarray([p], float))[0][:2]


def test_reference_from_a_pick_survives_an_upstream_dimension_edit(win):
    eng, doc, vp = win.studio_engine, win.doc, win.viewport
    pk = _pick_at_face(win, "face", 1)
    kind, ref = pick_to_item(vp, pk, {"face"}, doc.state)
    name = ref["name"]
    f = copy.deepcopy(doc.feature("F2"))
    f["params"]["depth"] = 55.0
    doc.replace_feature(f)
    try:
        assert eng.settle(120) and not doc.errors
        pid, sub = F.resolve(doc.state, ref, "face")            # by name: a miss would raise
        assert doc.state.named(pid).name_of(sub) == name
        i = element_index(doc.state, pid, "face", name)
        assert i is not None and K.faces(doc.state.parts[pid].shape)[i - 1].IsSame(sub)
    finally:
        doc.undo()
        assert eng.settle(120)


def test_highlight_by_name_draws_the_meshs_triangle_range_of_that_face(win):
    eng, doc, vp = win.studio_engine, win.doc, win.viewport
    out = eng.in_engine(doc, lambda e, tab: e.outputs(tab)).parts
    part = {p.id: p for p in out}
    pk = _pick_at_face(win, "face", 2)
    pid, fi = pk.key
    name = element_name(doc.state, pid, "face", fi + 1)
    win.viewport.set_selection([])
    got = win.highlight_element(name)
    assert got == Pick("face", (pid, fi))
    rng = part[pid].mesh.face_ranges[fi]
    assert rng[1] > rng[0], "the face has no triangles"
    assert vp.highlighted_triangles() == [(pid, rng[0], rng[1])]
    tf = part[pid].mesh.tri_face
    assert (tf[rng[0]:rng[1]] == fi + 1).all() and (tf == fi + 1).sum() == rng[1] - rng[0]
    assert win.highlight_element("no;such;name") is None
    hov = win.highlight_element(name, hover=True)
    vp.set_selection([])
    assert vp.highlighted_triangles() == [(pid, rng[0], rng[1])]
    vp.hover = None


def test_plane_picked_on_a_face_carries_the_tabs_state(win, monkeypatch):
    doc, vp = win.doc, win.viewport
    seen = []
    real = mw_mod.pick_to_item

    def spy(vp_, pk_, want, state=None):
        item = real(vp_, pk_, want, state)
        seen.append((state, item))
        return item
    monkeypatch.setattr(mw_mod, "pick_to_item", spy)
    for skip in range(8):
        pk = _pick_at_face(win, "face", skip)
        if K.planar_face_plane(K.faces(doc.state.parts[pk.key[0]].shape)[pk.key[1]]) is not None:
            break
    vp.set_selection([pk])
    seen.clear()
    win._normal_to_sketch()
    vp.set_selection([])
    assert seen and seen[0][0] is doc.state
    assert seen[0][1][1]["face"]["name"], "a plane picked on a face lost its element name"
