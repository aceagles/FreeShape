"""F25: one relation of each kind per pair in the served sketch session (SketchSession.constrain). Asking again for a
relation the sketch holds changes nothing and answers "already"; a mixed selection adds only the new relations; a
relation the solver skipped is refused like a conflicting one. Each test builds what it needs, so each passes alone."""
import pytest

from freeshape import sketch_ops as OPS
from freeshape.sketch_session import SketchSession
from tests.test_sketch_constraints_served import _session, _world


def _count(s, tool, refs=None):
    return sum(1 for c in s.sk.constraints if c["type"] == tool and (refs is None or sorted(c["refs"]) == sorted(refs)))


def test_the_same_relation_twice_is_a_no_op_that_answers_already():
    sk, n = _world()
    s = _session(sk)
    first = s.constrain({"tool": "parallel", "picks": [n["slant"], n["flat"]]})
    assert first["accepted"] is True and len(first["made"]) == 1 and "already" not in first
    depth, view, rev = len(s.undo_stack), s.view(), s.sk.to_dict()
    again = s.constrain({"tool": "parallel", "picks": [n["flat"], n["slant"]]})      # other order: same pair
    assert again == {"accepted": True, "already": True, "kind": "parallel", "made": [], "conflicts": []}
    assert len(s.undo_stack) == depth and s.view() == view and s.sk.to_dict() == rev
    assert _count(s, "parallel") == 1


def test_a_mixed_selection_adds_only_the_new_relations():
    sk, n = _world()
    s = _session(sk)
    s.constrain({"tool": "horizontal", "picks": [n["flat"]]})
    depth = len(s.undo_stack)
    out = s.constrain({"tool": "horizontal", "picks": [n["flat"], n["slant"]]})
    assert out["accepted"] is True and len(out["made"]) == 1 and "already" not in out
    assert _count(s, "horizontal", [n["flat"]]) == 1 and _count(s, "horizontal", [n["slant"]]) == 1
    assert len(s.undo_stack) == depth + 1


def test_a_relation_the_solver_skipped_is_rolled_back_and_refused(monkeypatch):
    sk, n = _world()
    s = _session(sk)
    # a pair the solver has no equation for: a tangent of two lines
    monkeypatch.setattr(OPS, "constraints_for", lambda sk_, tool, picks: [("tangent", [n["slant"], n["flat"]], None)])
    redo = [s.sk.to_dict()]
    s.redo_stack[:] = redo                             # a pending redo must survive a refused step
    depth, view = len(s.undo_stack), s.view()
    out = s.constrain({"tool": "tangent", "picks": [n["slant"], n["flat"]]})
    assert out["accepted"] is False and out["made"] == [] and len(out["conflicts"]) == 1
    assert len(s.undo_stack) == depth and s.view() == view and s.redo_stack == redo
    assert _count(s, "tangent") == 0


def test_a_circle_then_line_tangent_stores_the_line_first_and_solves():
    sk, n = _world()
    s = _session(sk)
    out = s.constrain({"tool": "tangent", "picks": [n["c2"], n["up"]]})
    assert out["accepted"] is True and len(out["made"]) == 1
    c = s.sk.constraints[-1]
    assert c["type"] == "tangent" and c["refs"] == [n["up"], n["c2"]]
    assert s._status["ok"] and c["id"] not in s.sk.status["skipped"]
    assert s.constrain({"tool": "tangent", "picks": [n["up"], n["c2"]]})["already"] is True
