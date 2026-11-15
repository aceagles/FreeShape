"""I5.7: the interference check in a helper process (freeshape/interference_worker.py, driven by
freeshape/ui/interference_ui.py). Every test that starts a helper has a timeout and kills it in a finally."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pytest

from freeshape import interference as I
from freeshape import interference_worker as W
from freeshape import kernel as K
from freeshape.ui import interference_ui as IU
from test_assembly import box, cyl
from test_interference_kernel import B, isolated, tr  # noqa: F401  (isolated: autouse fixture)

LIMIT = 180        # seconds; the box is shared and loaded


def fixtures():
    """The I5.2 situations in one list of bodies: overlap, contained, touching, press fit, identical screws,
    a thread pair (expected)."""
    hole = K.cut(box(20, 20, 10), cyl(2.0, 12, (10, 10, -1)))
    screw = cyl(2.1, 10)
    bodies = [
        B("a", box(20, 10, 5), tr(0, 500, 0)), B("b", box(10, 10, 5), tr(5, 505, 2)),          # overlap, 150
        B("big", box(20, 20, 20), tr(0, 1000, 0)), B("small", box(5, 5, 5), tr(7, 1007, 7)),   # contained
        B("t1", box(10, 10, 10), tr(0, 1500, 0)), B("t2", box(10, 10, 10), tr(10, 1500, 0)),   # touching
        B("th", hole, tr(0, 2000, 0), key="th"), B("ts", screw, tr(10, 2010, 0), key="ts"),    # thread (expected)
        B("ra", box(30, 8, 8), tr(0, 2500, 0)),
        B("rb", box(8, 30, 8), tr(10, 2495, 0)),                                               # a second overlap
    ]
    for k in range(12):
        T = tr(100 * k, 0, 0)
        bodies.append(I.Body(f"h{k}", K.located(hole, T), "hole", T))
        T2 = tr(100 * k + 10, 10, 0)
        bodies.append(I.Body(f"s{k}", K.located(screw, T2), "screw", T2))
    return bodies, frozenset({frozenset(("th", "ts"))})


def summary(res):
    return {frozenset((c.a, c.b)): (c.kind, c.volume) for c in res}


def start(bodies, expected=frozenset(), cancelled=None, progress=None):
    return IU._HelperRun(bodies, frozenset(), expected, progress, cancelled)


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        os.waitpid(pid, os.WNOHANG)          # a zombie child counts as gone
    except ChildProcessError:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def patched_command(tmp_path, body):
    """A worker command that runs `body` (code patching K) before the real worker."""
    script = tmp_path / "patched_worker.py"
    script.write_text(f"import sys\nsys.path.insert(0, {IU._ROOT!r})\nfrom freeshape import kernel as K\n" + body +
                      "\nfrom freeshape import interference_worker as W\nW.run(sys.argv[1])\n")
    return lambda req: [sys.executable, str(script), req]


def test_helper_returns_the_rows_of_the_in_process_check():
    bodies, expected = fixtures()
    want = I.find_interferences(bodies, expected_pairs=expected)
    h = start(bodies, expected)
    try:
        got = h.future.result(timeout=LIMIT)
    finally:
        h.kill()
    assert not got.incomplete and got.pairs_total == want.pairs_total and got.pairs_checked == want.pairs_total
    a, b = summary(got), summary(want)
    assert a.keys() == b.keys()
    for k in a:
        assert a[k][0] == b[k][0], k
        assert a[k][1] == pytest.approx(b[k][1], rel=1e-6), k
    kinds = {v[0] for v in a.values()}
    assert {"overlap", "contained", "thread"} <= kinds
    assert [c.volume for c in got] == sorted((c.volume for c in got), reverse=True)
    for c in got:                                      # the overlap comes back as a solid in world coordinates
        assert K.volume(c.shape) == pytest.approx(c.volume, rel=1e-6)
        lo, hi = K.bbox(c.shape)
        assert np.allclose(lo, c.lo, atol=1e-6) and np.allclose(hi, c.hi, atol=1e-6)


def test_launch_check_uses_the_helper_by_default_and_reports_progress():
    assert IU.ENGINE == "helper"
    bodies, expected = fixtures()
    seen = []
    f = IU.launch_check(None, bodies, frozenset(), expected, lambda n, t: seen.append((n, t)), None, {})
    try:
        res = f.result(timeout=LIMIT)
    finally:
        f.kill()
    assert seen[0][0] == 0 and seen[-1] == (res.pairs_total, res.pairs_total)
    assert [n for n, _ in seen] == sorted(n for n, _ in seen)


def test_identical_geometry_costs_one_boolean_in_the_worker(tmp_path, monkeypatch):
    """Ten boxes in a row, each overlapping the next by the same amount: nine pairs, one boolean."""
    box10 = box(10, 10, 10)
    bodies = []
    for k in range(10):
        T = tr(5 * k, 0, 0)
        bodies.append(I.Body(f"b{k}", K.located(box10, T), "box", T))
    folder = tmp_path / "req"
    folder.mkdir()
    (folder / "g0.brep").write_bytes(K.shape_to_brep(box10))
    req = {"bodies": [{"geom": 0, "keyed": True, "standard": False, "T": np.asarray(b.T).ravel().tolist()}
                      for b in bodies],
           "pairs": [[k, k + 1] for k in range(9)], "expected": [], "min_volume": I.MIN_VOLUME,
           "min_depth": I.MIN_DEPTH, "log": str(folder / "log.txt")}
    (folder / "req.json").write_text(json.dumps(req))
    real = K.common
    n = []
    monkeypatch.setattr(K, "common", lambda *a, **kw: (n.append(1), real(*a, **kw))[1])
    W.run(str(folder / "req.json"))
    lines = [json.loads(ln) for ln in (folder / "log.txt").read_text().splitlines()]
    assert len(n) == 1
    assert [e["kind"] for e in lines if e["t"] == "R"] == ["overlap"] * 9 and lines[-1] == {"t": "E"}


def test_cancel_during_a_slow_pair_returns_within_a_second_and_leaves_no_child(tmp_path, monkeypatch):
    marker = tmp_path / "in_the_pair"
    monkeypatch.setattr(IU, "_worker_command", patched_command(tmp_path, f"""
import time
_real = K.common
def _slow(*a, **k):
    open({str(marker)!r}, "w").close()
    time.sleep(120)
    return _real(*a, **k)
K.common = _slow
"""))
    bodies = [B("a", box(20, 10, 5)), B("b", box(10, 10, 5), tr(5, 5, 2)), B("c", box(10, 10, 5), tr(10, 5, 2))]
    cancel = []
    h = start(bodies, cancelled=lambda: bool(cancel))
    try:
        end = time.time() + LIMIT
        while not marker.exists() and time.time() < end:
            time.sleep(0.05)
        assert marker.exists(), "the helper never reached the pair"
        pid = h.proc.pid
        assert alive(pid)
        t0 = time.time()
        cancel.append(1)
        res = h.future.result(timeout=10)
        took = time.time() - t0
    finally:
        h.kill()
    assert took < 1.0, took
    assert res.incomplete and res.pairs_checked == 0 and list(res) == []
    assert not alive(pid)


def test_future_kill_also_cancels_and_leaves_no_child(tmp_path, monkeypatch):
    marker = tmp_path / "in_the_pair"
    monkeypatch.setattr(IU, "_worker_command", patched_command(tmp_path, f"""
import time
K.common = lambda *a, **k: (open({str(marker)!r}, "w").close(), time.sleep(120))
"""))
    bodies = [B("a", box(20, 10, 5)), B("b", box(10, 10, 5), tr(5, 5, 2))]
    f = IU.launch_check(None, bodies, frozenset(), frozenset(), None, None, {})
    try:
        end = time.time() + LIMIT
        while not marker.exists() and time.time() < end:
            time.sleep(0.05)
        assert marker.exists()
        f.kill()
        res = f.result(timeout=10)
    finally:
        f.kill()
    assert res.incomplete


def test_a_helper_killed_mid_pair_gives_one_could_not_check_row_and_the_rest_are_reported(tmp_path, monkeypatch):
    flag = tmp_path / "already_died"
    monkeypatch.setattr(IU, "_worker_command", patched_command(tmp_path, f"""
import os
_real = K.common
_n = []
def _die_once(*a, **k):
    _n.append(1)
    if len(_n) == 2 and not os.path.exists({str(flag)!r}):
        open({str(flag)!r}, "w").close()
        os._exit(7)                 # the second boolean of the first process
    return _real(*a, **k)
K.common = _die_once
"""))
    # four distinct overlapping pairs (different sizes, so no cache hit)
    bodies = []
    for k in range(4):
        bodies.append(B(f"p{k}", box(20 + k, 10, 5), tr(0, 100 * k, 0)))
        bodies.append(B(f"q{k}", box(10, 10 + k, 5), tr(5, 100 * k + 5, 2)))
    want = I.find_interferences(bodies)
    assert len(want) == 4
    h = start(bodies)
    try:
        got = h.future.result(timeout=LIMIT)
    finally:
        h.kill()
    assert flag.exists()
    failed = [c for c in got if c.kind == "failed"]
    assert len(failed) == 1 and failed[0].shape is None and "stopped" in failed[0].note
    assert not got.incomplete and got.pairs_checked == got.pairs_total == 4
    ok = summary([c for c in got if c.kind != "failed"])
    ref = summary(want)
    assert len(ok) == 3 and all(ok[k][0] == ref[k][0] and ok[k][1] == pytest.approx(ref[k][1], rel=1e-6) for k in ok)
    assert frozenset((failed[0].a, failed[0].b)) not in ok


def test_a_helper_that_cannot_start_falls_back_to_this_process(monkeypatch):
    monkeypatch.setattr(IU, "_worker_command", lambda req: ["/nonexistent/helper-binary", req])
    bodies = [B("a", box(20, 10, 5)), B("b", box(10, 10, 5), tr(5, 5, 2))]
    h = start(bodies)
    try:
        res = h.future.result(timeout=LIMIT)
    finally:
        h.kill()
    assert [(c.kind, round(c.volume, 6)) for c in res] == [("overlap", 150.0)]


def test_a_helper_that_dies_at_once_falls_back_to_this_process(tmp_path, monkeypatch):
    monkeypatch.setattr(IU, "_worker_command", lambda req: [sys.executable, "-c", "raise SystemExit(3)"])
    bodies = [B("a", box(20, 10, 5)), B("b", box(10, 10, 5), tr(5, 5, 2))]
    h = start(bodies)
    try:
        res = h.future.result(timeout=LIMIT)
    finally:
        h.kill()
    assert [(c.kind, round(c.volume, 6)) for c in res] == [("overlap", 150.0)] and res.pairs_checked == 1
