"""Suppress an instance (I3.10): left out of the solve, the visible instances, the BOM, interference, export."""
import copy
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from freeshape import assembly as A
from tests.test_assembly import lib, make, src, face, mate  # noqa: F401  (lib is a fixture)


def _top(a, i):
    return a.connector(i, "face", face(a, i, lambda f: f.get("n") and f["n"][2] > 0.9))


def _bot(a, i):
    return a.connector(i, "face", face(a, i, lambda f: f.get("n") and f["n"][2] < -0.9))


def _scene(lib):
    """plate (fixed), block "mid" and pin each mated to the plate."""
    a = make(lib)
    ip = a.insert(src("plate"))
    im = a.insert(src("block"))
    ic = a.insert(src("arm"))
    m1 = mate(a, "planar", _top(a, ip), _bot(a, im))
    m2 = mate(a, "planar", _top(a, ip), _bot(a, ic))
    a.instance(im)["name"] = "MidBlock"
    return a, ip, im, ic, m1, m2


def _z(a, iid):
    return np.array(a.instance(iid)["T"], float)[2, 3]


def test_suppress_leaves_instance_out_everywhere(lib, tmp_path):
    a, ip, im, ic, m1, m2 = _scene(lib)
    assert {i["id"] for i in a.visible_instances()} == {ip, im, ic}
    assert sum(r["qty"] for r in a.bom()) == 3
    a.set_instance_suppressed(im, True)
    assert a.instance(im)["suppressed"] is True
    assert {i["id"] for i in a.visible_instances()} == {ip, ic}
    assert sum(r["qty"] for r in a.bom()) == 2
    assert all("MidBlock" not in r["instances"] for r in a.bom())
    assert all(im not in (x, y) for x, y, *_ in a.interferences())
    full, part = tmp_path / "a.step", tmp_path / "b.step"
    a.set_instance_suppressed(im, False)
    a.export(str(full))
    a.set_instance_suppressed(im, True)
    a.export(str(part))
    assert "MidBlock" in full.read_text(errors="ignore")
    assert "MidBlock" not in part.read_text(errors="ignore")


def test_mates_on_it_inactive_and_remaining_mate_solves(lib):
    a, ip, im, ic, m1, m2 = _scene(lib)
    a.instance(ic)["T"][2][3] += 30                    # knock the arm off its mate
    a.set_instance_suppressed(im, True)
    assert a.mate_inactive(m1) and not a.mate_inactive(m2)
    assert m1["id"] not in a.errors and m1["id"] not in a._conflicts
    assert m1 not in a._active_mates() and m2 in a._active_mates()
    assert abs(_z(a, ic) - 10) < 1e-6                  # the other mate pulled it back
    assert not a.errors
    assert a.instance_dof(im) == 0


def test_unsuppress_and_undo_restore(lib):
    a, ip, im, ic, m1, m2 = _scene(lib)
    z = _z(a, im)
    a.set_instance_suppressed(im, True)
    assert _z(a, im) == z                              # stays at its stored position
    a.set_instance_suppressed(im, False)
    assert not a.instance(im)["suppressed"]
    assert sum(r["qty"] for r in a.bom()) == 3
    assert not a.mate_inactive(m1) and abs(_z(a, im) - z) < 1e-6 and not a.errors
    a.set_instance_suppressed(im, True)
    a.undo()                                           # one undo step
    assert not a.instance(im).get("suppressed")
    assert sum(r["qty"] for r in a.bom()) == 3
    assert abs(_z(a, im) - z) < 1e-6 and a.instance_dof(im) == 3


def test_group_solves_without_suppressed_member(lib):
    a, ip, im, ic, m1, m2 = _scene(lib)
    i3 = a.insert(src("block"))
    a.add_group([im, i3])
    a.set_instance_suppressed(i3, True)
    a.instance(im)["T"][0][3] += 5
    a.solve()
    assert not a.errors
    a.set_instance_suppressed(im, True)               # whole group suppressed: nothing left to solve
    assert a.solve() in (True, False)


def test_relation_on_suppressed_instance_is_inert(lib):
    from scripts.smoke import mates_common as C
    a, (m1, m2) = C.mated_blocks("slider", "slider")
    r = C.relate(a, "linear", m1, m2, 1.0, "tz", "tz")
    assert a.solve() and not a.errors
    ib = m2["b"]["inst"]
    a.set_instance_suppressed(ib, True)
    assert a.mate_inactive(m2)
    assert a._relation_residuals(a._active_mates(), {}, {}, 1.0).size == 0
    assert m2["id"] not in a.errors
    assert a.drive_mate(m1["id"], "tz", 7.0)[1]        # m1 moves freely: the relation no longer drags m2
    a.set_instance_suppressed(ib, False)
    assert a.solve() and r["id"] not in a.errors


def test_drive_mate_on_suppressed_instance_fails_cleanly(lib):
    a = make(lib)
    ip, ia = a.insert(src("plate")), a.insert(src("arm"))
    m = mate(a, "planar", _top(a, ip), _bot(a, ia))
    a.set_instance_suppressed(ia, True)
    n = len(a._undo)
    val, ok = a.drive_mate(m["id"], "tx", 5.0)
    assert ok is False and len(a._undo) == n


def test_drag_of_suppressed_instance_does_nothing(lib):
    a, ip, im, ic, m1, m2 = _scene(lib)
    a.set_instance_suppressed(im, True)
    T = copy.deepcopy(a.instance(im)["T"])
    assert a.solve(drag={"inst": im, "local": np.zeros(3), "target": np.array([50., 0, 0]), "P": np.eye(3)[:2]}) is False
    assert a.instance(im)["T"] == T


def test_file_without_key_loads_and_solves_as_before(lib, tmp_path):
    a, ip, im, ic, m1, m2 = _scene(lib)
    assert all("suppressed" not in i for i in a.instances)
    path = str(tmp_path / "a.fasm")
    a.save(path)
    b = A.AssemblyDoc.load(path, resolver=a.resolver)
    assert all("suppressed" not in i for i in b.instances)
    assert b.solve() and not b.errors
    for i in a.instances:
        assert np.allclose(i["T"], b.instance(i["id"])["T"], atol=1e-9)
    assert b.visible_instances() and len(b.visible_instances()) == 3


def test_unsuppress_moves_nothing_through_a_relation():
    from scripts.smoke import mates_common as C
    a, (m1, m2) = C.mated_blocks("revolute", "revolute")
    r = C.relate(a, "gear", m1, m2, 2.0)
    assert a.solve() and not a.errors
    ia = m1["b"]["inst"]
    a.set_instance_suppressed(ia, True)
    assert a.drive_mate(m2["id"], "rz", 40.0)[1]       # the other side turns while the relation is inert
    before = {i["id"]: np.array(i["T"], float) for i in a.instances}
    a.set_instance_suppressed(ia, False)
    for i in a.instances:
        assert np.allclose(i["T"], before[i["id"]], atol=1e-6), i["id"]
    assert a.relation(r["id"])["phase"] is not None and r["id"] not in a.errors
    assert abs(m2["values"]["rz"] - 40.0) < 1e-4
    assert a.drive_mate(m1["id"], "rz", m1["values"]["rz"] + 10.0)[1]
    assert abs(m2["values"]["rz"] - 60.0) < 1e-3       # ratio 2 holds from the new pose


def test_drawing_of_the_assembly_leaves_suppressed_instance_out(lib):
    from freeshape import kernel as K
    from freeshape import links as L
    from freeshape.drawing import DrawingDoc
    a, ip, im, ic, m1, m2 = _scene(lib)
    r = L.LinkResolver()
    r.find_open_assembly = lambda uid, path=None: a
    g = DrawingDoc()
    g.source = {"kind": "assembly", "uid": a.uid, "name": a.name}
    g.resolver = r.resolve_drawing_source
    g.refresh(notify=False)
    v0 = K.volume(g.shape)
    blk = K.volume(a.parts[im].shape)
    a.set_instance_suppressed(im, True)
    g.refresh(notify=False)
    assert abs((v0 - K.volume(g.shape)) - blk) < 1e-3
    a.set_instance_suppressed(im, False)
    g.refresh(notify=False)
    assert abs(K.volume(g.shape) - v0) < 1e-3
