"""I5.2: the shared interference kernel (freeshape/interference.py) and its Assembly callers."""
import os
import re
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from freeshape import interference as I
from freeshape import kernel as K
from freeshape import pyfeature
from freeshape.features import Part
from freeshape.pyfeature import registry
from test_assembly import box, cyl, make, src
from test_assembly_standard_bom import plate_with_hole, screwed


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    user = tmp_path / "user_plugins"
    user.mkdir()
    monkeypatch.setenv("FREESHAPE_PLUGIN_DIR", str(user))
    before = {p.id for p in pyfeature.plugins()}
    problems_before = list(registry._PROBLEMS)
    yield
    registry._PROBLEMS[:] = problems_before
    for p in pyfeature.plugins():
        if p.id not in before:
            pyfeature.unregister_plugin(p.id)


def rot(axis, deg, at=(0, 0, 0)):
    ax = np.asarray(axis, float)
    ax /= np.linalg.norm(ax)
    t = np.radians(deg)
    Kx = np.array([[0, -ax[2], ax[1]], [ax[2], 0, -ax[0]], [-ax[1], ax[0], 0]])
    T = np.eye(4)
    T[:3, :3] = np.eye(3) + np.sin(t) * Kx + (1 - np.cos(t)) * Kx @ Kx
    T[:3, 3] = at
    return T


def tr(x, y, z):
    T = np.eye(4)
    T[:3, 3] = (x, y, z)
    return T


def B(id, shape, T=None, key="__auto__", standard=False):
    T = np.eye(4) if T is None else T
    return I.Body(id, K.located(shape, T), id if key == "__auto__" else key, T, standard)


def run(*bodies, **kw):
    return I.find_interferences(list(bodies), **kw)


def test_two_boxes_overlap_150():
    res = run(B("a", box(20, 10, 5)), B("b", box(10, 10, 5), tr(5, 5, 2)))
    assert len(res) == 1 and res[0].kind == "overlap"
    assert res[0].volume == pytest.approx(150, rel=1e-6)
    assert np.allclose(res[0].lo, (5, 5, 2)) and np.allclose(res[0].hi, (15, 10, 5))


@pytest.mark.parametrize("at", [(10, 0, 0), (10, 10, 0), (10, 10, 10)])
def test_touching_face_edge_vertex_is_not_a_clash(at):
    assert run(B("a", box(10, 10, 10)), B("b", box(10, 10, 10), tr(*at))) == []


def test_small_gap_is_not_a_clash():
    assert run(B("a", box(10, 10, 10)), B("b", box(10, 10, 10), tr(10.001, 0, 0))) == []


def test_big_plates_depth_floor():
    top = lambda z: B("b", box(1000, 1000, 5), tr(0, 0, z))
    assert run(B("a", box(1000, 1000, 5)), top(5 - 1e-7)) == []
    res = run(B("a", box(1000, 1000, 5)), top(5 - 0.01))
    assert len(res) == 1 and res[0].kind == "overlap"
    assert res[0].volume == pytest.approx(10000, rel=1e-4)


def test_cube_inside_solid_cube_is_contained():
    res = run(B("a", box(20, 20, 20)), B("b", box(5, 5, 5), tr(7, 7, 7)))
    assert len(res) == 1 and res[0].kind == "contained" and res[0].volume == pytest.approx(125, rel=1e-6)


def test_cube_inside_hollow_box_is_no_clash():
    hollow = K.cut(box(20, 20, 20), box(7, 7, 7, (6.5, 6.5, 6.5)))
    assert run(B("a", hollow), B("b", box(5, 5, 5), tr(7.5, 7.5, 7.5))) == []


def test_press_fit_and_exact_fit():
    hole = K.cut(box(30, 30, 20), cyl(5.0, 22, (15, 15, -1)))
    pin = lambda r: B("p", cyl(r, 20), tr(15, 15, 0))
    res = run(B("h", hole), pin(5.01))
    assert len(res) == 1 and res[0].volume == pytest.approx(6.29, rel=0.01)
    assert run(B("h", hole), pin(5.0)) == []


def test_failing_boolean_is_a_failed_row(monkeypatch):
    real = K.common

    def flaky(a, b, **kw):
        if K.bbox(b)[0][0] > 100:
            raise K.KernelError("boom")
        return real(a, b, **kw)
    monkeypatch.setattr(K, "common", flaky)
    res = run(B("a", box(20, 10, 5)), B("b", box(10, 10, 5), tr(5, 5, 2)),
              B("c", box(10, 10, 5), tr(110, 0, 0)), B("d", box(20, 10, 5), tr(105, 0, 0)))
    kinds = {frozenset((c.a, c.b)): c for c in res}
    assert kinds[frozenset("ab")].kind == "overlap"
    f = kinds[frozenset("cd")]
    assert f.kind == "failed" and "boom" in f.note and f.shape is None and f.volume == 0.0
    assert len(res) == 2


def test_failed_first_try_retries_with_check(monkeypatch):
    real = K.common
    calls = []

    def once(a, b, check=False, **kw):
        calls.append(check)
        if not check:
            raise K.KernelError("first")
        return real(a, b, check=check, **kw)
    monkeypatch.setattr(K, "common", once)
    res = run(B("a", box(20, 10, 5)), B("b", box(10, 10, 5), tr(5, 5, 2)))
    assert calls == [False, True] and res[0].kind == "overlap"


def test_identical_screws_cost_one_boolean(monkeypatch):
    hole = K.cut(box(20, 20, 10), cyl(2.0, 12, (10, 10, -1)))
    screw = cyl(2.1, 10)
    bodies = []
    for k in range(12):
        T = tr(100 * k, 0, 0)
        bodies.append(I.Body(f"h{k}", K.located(hole, T), "hole", T))
        T2 = tr(100 * k + 10, 10, 0)
        bodies.append(I.Body(f"s{k}", K.located(screw, T2), "screw", T2))
    real = K.common
    n = []
    monkeypatch.setattr(K, "common", lambda *a, **kw: (n.append(1), real(*a, **kw))[1])
    res = I.find_interferences(bodies)
    assert len(n) == 1 and len(res) == 12
    assert all(c.volume == pytest.approx(res[0].volume, rel=1e-9) for c in res)
    assert {frozenset((c.a, c.b)) for c in res} == {frozenset((f"h{k}", f"s{k}")) for k in range(12)}


def test_rotated_pair_same_volume():
    base = run(B("a", box(20, 10, 5)), B("b", box(10, 10, 5), tr(5, 5, 2)))[0].volume
    R = rot((1, 2, 3), 37, at=(3, -4, 5))
    res = run(B("a", box(20, 10, 5), R), B("b", box(10, 10, 5), R @ tr(5, 5, 2)))
    assert res[0].volume == pytest.approx(base, rel=1e-6)


def test_min_depth_matches_result_file():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "scripts", "spikes", "op2_interference_bench.result.txt")
    text = open(path).read()
    m = re.search(r"^DECISION .*\bmin_depth=([0-9.eE+-]+)", text, re.M)
    assert m and I.MIN_DEPTH == float(m.group(1))
    assert I.MIN_VOLUME == 1e-3


def _row(k):
    return B(f"r{k}", box(10, 10, 10), tr(5 * k, 0, 0))


def test_cancel_and_progress():
    bodies = [B("a", box(10, 10, 10))] + [_row(k) for k in range(1, 7)]
    ticks = []
    full = I.find_interferences(bodies, progress=lambda n, t: ticks.append((n, t)))
    assert not full.incomplete and ticks[-1][0] == ticks[-1][1] == full.pairs_total
    assert [n for n, _ in ticks] == sorted(n for n, _ in ticks)
    calls = []

    def cancelled():
        calls.append(1)
        return len(calls) > 3
    part = I.find_interferences(bodies, cancelled=cancelled)
    assert part.incomplete and part.pairs_checked == 3 and len(calls) == 4
    assert len(part) <= len(full)


def test_skip_pairs():
    a, b = B("a", box(20, 10, 5)), B("b", box(10, 10, 5), tr(5, 5, 2))
    assert run(a, b, skip_pairs={frozenset("ab")}) == []


def test_fastened_screw_is_thread_not_wrapper_row():
    a, p, s, m = screwed(1.25)
    assert a.interferences() == []
    skip, expected, left = a.pair_rules(True)
    assert skip == frozenset() and expected == {frozenset((p, s))} and left == 0
    res = I.find_interferences(a.interference_bodies(), expected_pairs=expected)
    assert [(c.kind, frozenset((c.a, c.b))) for c in res] == [("thread", frozenset((p, s)))]
    skip, expected, left = a.pair_rules(False)
    assert skip == {frozenset((p, s))} and expected == frozenset() and left == 1
    assert I.find_interferences(a.interference_bodies(), skip_pairs=skip) == []
    assert [b.id for b in a.interference_bodies({p})] == [p]


def test_assembly_wrapper_returns_old_tuples():
    a = make({"plate": box(20, 10, 5), "block": box(10, 10, 5)})
    p, b = a.insert(src("plate")), a.insert(src("block"))
    a.move_instance(b, tr(*(np.array([5, 5, 2.0]) - np.array(a.instance(b)["T"])[:3, 3])))
    (x,) = a.interferences()
    assert x[:2] == (p, b) and x[2] == pytest.approx(150, rel=1e-6) and not x[3].IsNull()


def test_part_studio_bodies():
    mk = lambda i, sh, ref=False: Part(i, i, sh, reference=ref)
    parts = {"a": mk("a", box(20, 10, 5)), "b": mk("b", K.located(box(10, 10, 5), tr(5, 5, 2))),
             "h": mk("h", K.located(box(10, 10, 5), tr(5, 5, 2))),
             "ctx": mk("ctx", K.located(box(10, 10, 5), tr(5, 5, 2)), ref=True)}
    doc = SimpleNamespace(state=SimpleNamespace(parts=parts), hidden_parts={"h"})
    bodies = I.studio_bodies(doc)
    assert [b.id for b in bodies] == ["a", "b"]
    res = I.find_interferences(bodies)
    assert len(res) == 1 and res[0].volume == pytest.approx(150, rel=1e-6)
