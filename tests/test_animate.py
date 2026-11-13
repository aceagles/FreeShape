"""Animate model: animate_values, begin_animate, animate_frame, end_animate (I6.1)."""
import json

import pytest

from freeshape import assembly as A
from test_relation_types import _rel, _setup


def test_values_single_and_reciprocate():
    assert A.animate_values(0, 720, 9, "single") == [0, 90, 180, 270, 360, 450, 540, 630, 720]
    r = A.animate_values(0, 720, 9, "reciprocate")
    assert len(r) == 17 and r[0] == 0 and r[-1] == 0 and r[8] == 720 and r[9] == 630
    assert A.animate_values(0, 720, 9, "loop") == A.animate_values(0, 720, 9, "single")


def test_values_errors():
    with pytest.raises(ValueError):
        A.animate_values(0, 10, 1, "single")
    with pytest.raises(ValueError):
        A.animate_values(0, 36001, 5, "single")
    with pytest.raises(ValueError):
        A.animate_values(0, 10, 5, "bounce")


def test_gear_follows_each_frame():
    a, m1, m2 = _setup("revolute", "revolute")
    _rel(a, "gear", m1, "rz", m2, "rz", 0.5)
    a.begin_animate()
    for v in A.animate_values(0, 360, 5, "single"):
        assert a.animate_frame(m1["id"], "rz", v) == pytest.approx(v, abs=1e-6)
        assert m2["values"]["rz"] == pytest.approx(v / 2, abs=1e-6)
    a.end_animate()


def test_end_restores_exactly_without_undo():
    a, m1, m2 = _setup("revolute", "revolute")
    r = _rel(a, "gear", m1, "rz", m2, "rz", 0.5)
    phase = r["phase"]
    before = (json.dumps(a.instances), json.dumps(a.mates))
    undo, redo = list(a._undo), list(a._redo)
    calls = []
    a.listeners.append(lambda: calls.append(1))
    a.begin_animate()
    a.begin_animate()          # does not lose the first snapshot
    a.animate_frame(m1["id"], "rz", 200.0)
    assert json.dumps(a.instances) != before[0]
    a.end_animate()
    assert (json.dumps(a.instances), json.dumps(a.mates)) == before
    assert a._undo == undo and a._redo == redo
    assert r["phase"] == phase and calls
    n = len(calls)
    a.end_animate()            # no-op
    assert len(calls) == n


def test_frame_without_begin_is_restorable():
    a, m1, _m2 = _setup("revolute", "revolute")
    before = json.dumps(a.instances)
    a.animate_frame(m1["id"], "rz", 90.0)
    a.end_animate()
    assert json.dumps(a.instances) == before
    with pytest.raises(ValueError):
        a.animate_frame(m1["id"], "tz", 1.0)
