"""Hover snap points for mate connectors: candidates, frames, refresh after part edits, the mate dialog."""
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape import assembly as A  # noqa: E402
from test_assembly import box, cyl, edge, face, make, near, src  # noqa: E402

R, T = 30.0, 10.0


def tabbed_plate(cx=0.0):
    """Round plate (circle centre at (cx, 0)) with a rectangular tab sticking out to +X."""
    return K.fuse(cyl(R, T, (cx, 0, 0)), box(25, 16, T, (cx + 20, -8, 0)))


@pytest.fixture
def lib():
    return {"tab": tabbed_plate()}


def top_face(a, iid):
    return face(a, iid, lambda f: f.get("n") and f["n"][2] > 0.9)


def cands(a, iid, kind, idx, typ):
    return [c for c in a.snap_points(iid, kind, idx) if c["type"] == typ]


def test_face_snaps_include_circle_centre_and_connector_sits_there(lib):
    a = make(lib)
    i = a.insert(src("tab"))
    f = top_face(a, i)
    cs = a.snap_points(i, "face", f)
    kinds = {c["type"] for c in cs}
    assert {"center", "vertex", "mid", "centroid"} <= kinds
    ctr = cands(a, i, "face", f, "center")[0]
    assert np.allclose(ctr["p"], [0, 0, T], atol=1e-6)
    cen = cands(a, i, "face", f, "centroid")[0]
    assert cen["p"][0] > 1.0                                    # the centroid is pulled towards the tab
    c = a.connector(i, "face", f, snap=ctr)
    fr = a.world_frame(c)
    assert np.allclose(fr["o"], [0, 0, T], atol=1e-6)
    assert np.allclose(fr["z"], [0, 0, 1], atol=1e-6)
    assert np.allclose(fr["x"], [1, 0, 0], atol=1e-6)
    old = a.world_frame(a.connector(i, "face", f))
    assert old["o"][0] > 1.0                                    # without a snap: the centroid, as before


def test_edge_midpoint_and_vertex_snaps(lib):
    a = make(lib)
    i = a.insert(src("tab"))
    f = top_face(a, i)
    # the tab's far end edge: x = 45, along Y, on the top face
    far = edge(a, i, lambda e: e.get("p0") is not None and near(e["p0"][0], 45) and near(e["p1"][0], 45)
               and near(e["p0"][2], T))
    mid = cands(a, i, "edge", far, "mid")[0]
    assert np.allclose(mid["p"], [45, 0, T], atol=1e-6)
    cm = a.connector(i, "edge", far, snap=mid)
    assert np.allclose(a.world_frame(cm)["o"], [45, 0, T], atol=1e-6)
    # the same midpoint is offered, and builds the same origin, from the face; Z is then the face normal
    fm = [c for c in cands(a, i, "face", f, "mid") if np.allclose(c["p"], [45, 0, T], atol=1e-6)]
    assert fm
    fr = a.world_frame(a.connector(i, "face", f, snap=fm[0]))
    assert np.allclose(fr["o"], [45, 0, T], atol=1e-6) and np.allclose(fr["z"], [0, 0, 1], atol=1e-6)
    # vertices of the edge
    vs = cands(a, i, "edge", far, "vertex")
    assert sorted(round(float(v["p"][1]), 6) for v in vs) == [-8.0, 8.0]
    cv = a.connector(i, "edge", far, snap=[v for v in vs if v["p"][1] > 0][0])
    assert np.allclose(a.world_frame(cv)["o"], [45, 8, T], atol=1e-6)
    # a vertex of the face
    fv = [c for c in cands(a, i, "face", f, "vertex") if np.allclose(c["p"], [45, -8, T], atol=1e-6)]
    assert fv and np.allclose(a.world_frame(a.connector(i, "face", f, snap=fv[0]))["o"], [45, -8, T], atol=1e-6)


def test_circle_edge_snaps_to_its_centre(lib):
    a = make(lib)
    i = a.insert(src("tab"))
    circ = edge(a, i, lambda e: near(e.get("r", 0), R) and near(e["center"][2], T))
    ctr = cands(a, i, "edge", circ, "center")
    assert len(ctr) == 1 and np.allclose(ctr[0]["p"], [0, 0, T], atol=1e-6)


def test_x_axis_does_not_depend_on_the_longest_edge(lib):
    a = make(lib)
    i = a.insert(src("tab"))
    f = top_face(a, i)
    ctr = cands(a, i, "face", f, "centroid")[0]
    c = a.connector(i, "face", f, snap=ctr)
    fr = a.world_frame(c)
    assert np.allclose(fr["x"], [1, 0, 0], atol=1e-6)
    # a taller tab changes which straight edge is longest: X stays put
    lib["tab"] = K.fuse(cyl(R, T, (0, 0, 0)), box(25, 50, T, (20, -25, 0)))
    a.refresh()
    f2 = top_face(a, i)
    c2 = a.connector(i, "face", f2, snap=cands(a, i, "face", f2, "center")[0])
    assert np.allclose(a.world_frame(c2)["x"], [1, 0, 0], atol=1e-6)


def test_snap_connector_follows_the_circle_after_a_part_edit(lib):
    a = make(lib)
    ip, ib = a.insert(src("tab")), a.insert(src("tab"))
    ctr = cands(a, ip, "face", top_face(a, ip), "center")[0]
    cp = a.connector(ip, "face", top_face(a, ip), snap=ctr)
    cb = a.connector(ib, "face", top_face(a, ib), snap=cands(a, ib, "face", top_face(a, ib), "center")[0])
    cb["frame"]["z"] = [0, 0, -1]
    m = a.make_mate("fastened")
    m["a"], m["b"] = cp, cb
    a.add_mate(m)
    assert "snap" in cp
    lib["tab"] = tabbed_plate(cx=7.0)                           # the circle moves 7 along X
    a.refresh()
    assert not a.errors, a.errors
    assert np.allclose(a.mate(m["id"])["a"]["frame"]["o"], [7, 0, T], atol=1e-5)


def test_old_connectors_without_snap_still_work(lib, tmp_path):
    a = make(lib)
    i = a.insert(src("tab"))
    f = top_face(a, i)
    c = a.connector(i, "face", f)
    assert "snap" not in c
    j = a.insert(src("tab"))
    d = a.connector(j, "face", top_face(a, j))
    m = a.make_mate("fastened")
    m["a"], m["b"] = c, d
    a.add_mate(m)
    lib["tab"] = tabbed_plate(cx=3.0)
    a.refresh()
    assert not a.errors, a.errors
    path = str(tmp_path / "old.fasm")
    a.save(path)
    b = A.AssemblyDoc.load(path)
    assert b.mate(m["id"])["a"]["kind"] == "face" and "snap" not in b.mate(m["id"])["a"]


def _dialog(a, vp):
    from freeshape.ui.assembly_ui import MateDialog
    ctl = SimpleNamespace(w=SimpleNamespace(viewport=vp), doc=a, update_triads=lambda: None)
    m = a.make_mate("fastened")
    return MateDialog(ctl, m, True, []), m


def test_dialog_hover_shows_dots_and_click_takes_the_snap(lib):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from freeshape.ui.viewport import Viewport
    from freeshape.ui.viewport import Pick
    a = make(lib)
    i, j = a.insert(src("tab")), a.insert(src("tab"))
    vp = Viewport()
    vp.cam.w, vp.cam.h, vp.cam.scale = 800, 600, 0.2
    dlg, m = _dialog(a, vp)
    vp.snap_provider = dlg.snap_candidates
    f = top_face(a, i)
    pick = Pick("face", (i, f))
    vp._update_snaps(pick, 0, 0)
    assert {c["type"] for c in vp.snap_points} >= {"center", "centroid", "vertex", "mid"}
    ctr = [c for c in vp.snap_points if c["type"] == "center"][0]
    sp = vp.cam.to_screen(np.asarray(ctr["pw"], float))
    vp._update_snaps(pick, sp[0] + 3, sp[1] + 2)
    assert vp.snap_hi is not None and vp.snap_hi["type"] == "center"
    vp.pick_handler = dlg.handle_pick
    vp.pick_handler(Pick("face", (i, f)))
    c = dlg.mate["a"]
    assert c is not None and c.get("snap", {}).get("type") == "center"
    assert np.allclose(a.world_frame(c)["o"], a.T(i)[:3, :3] @ [0, 0, T] + a.T(i)[:3, 3], atol=1e-6)
    assert vp.snap_hi is None
    # no dot near the cursor: the old behaviour (centroid of the face)
    vp._update_snaps(pick, 5000, 5000)
    assert vp.snap_hi is None
    dlg.activate(dlg.fb)
    vp.pick_handler(Pick("face", (j, top_face(a, j))))
    assert "snap" not in dlg.mate["b"]
