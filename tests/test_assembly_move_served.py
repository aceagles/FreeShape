"""I8.2: a drag by a Drag handle is three Assembly edits: move_begin, move_to (absolute against the placements
move_begin remembered; latest wins) and move_end (closes the one undo step the drag made)."""
import os

import numpy as np
import pytest

from conftest import EXAMPLES_DIR
from freeshape.engine import InProcessEngine, ProcessEngine

HINGE_ASM = os.path.join(EXAMPLES_DIR, "hinge.fasm")


def _tx(x=0.0, y=0.0, z=0.0):
    T = np.eye(4)
    T[:3, 3] = (x, y, z)
    return T.tolist()


def _rz(deg, about=(0.0, 0.0, 0.0)):
    c, s = np.cos(np.radians(deg)), np.sin(np.radians(deg))
    R = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = np.asarray(about) - R @ np.asarray(about)
    return T.tolist()


def _placements(eng, tab):
    return {i.id: np.array(i.T, float) for i in eng.outputs(tab).instances}


def _free(eng, tab):
    return next(i.id for i in eng.outputs(tab).instances if not i.fixed)


def _drag(eng, tab, iid, moves):
    eng.submit_edit(tab, {"op": "move_begin", "instance": iid})
    for T in moves:
        eng.submit_edit(tab, {"op": "move_to", "T": T})
    eng.submit_edit(tab, {"op": "move_end"})


def _alone(engine_class):
    eng = engine_class()
    tab = eng.open_tab(HINGE_ASM)
    return eng, tab


@pytest.fixture(params=[InProcessEngine, ProcessEngine], ids=["inprocess", "process"])
def engine_class(request):
    return request.param


def test_drag_leaves_the_instance_at_the_last_transform_and_one_undo_returns_it(engine_class):
    eng, tab = _alone(engine_class)
    try:
        iid = _free(eng, tab)
        start = _placements(eng, tab)
        _drag(eng, tab, iid, [_rz(10), _rz(25), _rz(40)])
        end = _placements(eng, tab)
        assert not np.allclose(end[iid], start[iid])
        eng.submit_edit(tab, {"op": "undo"})
        back = _placements(eng, tab)
        for k in start:
            assert np.allclose(back[k], start[k], atol=1e-9), k
    finally:
        eng.close()


def test_skipping_the_middle_move_gives_the_same_placement(engine_class):
    eng, tab = _alone(engine_class)
    try:
        iid = _free(eng, tab)
        _drag(eng, tab, iid, [_rz(10), _rz(25), _rz(40)])
        full = _placements(eng, tab)
        eng.submit_edit(tab, {"op": "undo"})
        _drag(eng, tab, iid, [_rz(10), _rz(40)])
        skipped = _placements(eng, tab)
        for k in full:
            assert np.allclose(full[k], skipped[k], atol=1e-6), k
    finally:
        eng.close()


def test_a_mated_instance_follows_the_dragged_one(engine_class):
    eng, tab = _alone(engine_class)
    try:
        # nothing grounded: the leaf I2 (revolute to I1, and I3 fastened to I1) drags its mates along
        eng.submit_edit(tab, {"op": "set_fixed", "instance": "I1", "value": False})
        start = _placements(eng, tab)
        _drag(eng, tab, "I2", [_tx(0, 0, 5)])
        end = _placements(eng, tab)
        for k in ("I2", "I1", "I3"):
            assert not np.allclose(end[k], start[k]), k
        assert np.allclose(end["I2"][:3, 3] - start["I2"][:3, 3], (0, 0, 5), atol=0.05)
    finally:
        eng.close()


def test_move_to_without_begin_is_an_error(engine_class):
    from freeshape.engine import EngineError
    eng, tab = _alone(engine_class)
    try:
        with pytest.raises(EngineError):
            eng.submit_edit(tab, {"op": "move_to", "T": _tx(1)})
    finally:
        eng.close()


def test_a_drag_that_moved_nothing_leaves_no_undo_step(engine_class):
    eng, tab = _alone(engine_class)
    try:
        eng.submit_edit(tab, {"op": "set_hidden", "instance": "I2", "value": True})
        _drag(eng, tab, "I2", [])                     # begin and end with no move_to
        _drag(eng, tab, "I2", [_tx(0, 0, 0)])         # a move_to that changes nothing
        eng.submit_edit(tab, {"op": "undo"})
        assert next(i for i in eng.outputs(tab).instances if i.id == "I2").hidden is False   # the undo took the hide back
    finally:
        eng.close()


def test_move_to_after_move_end_is_an_error(engine_class):
    from freeshape.engine import EngineError
    eng, tab = _alone(engine_class)
    try:
        _drag(eng, tab, "I2", [_rz(5)])
        with pytest.raises(EngineError):
            eng.submit_edit(tab, {"op": "move_to", "T": _rz(10)})
    finally:
        eng.close()


def _pin_axis(eng, tab):
    """Origin and direction (world) of the Revolute mate's axis on the unmodified hinge."""
    m = next(m for m in eng.outputs(tab).mates if m.type == "revolute")
    w = m.a.world
    return np.array(w["o"], float), np.array(w["z"], float)


def _turn_about(axis_o, axis_z, deg):
    a = axis_z / np.linalg.norm(axis_z)
    K_ = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    t = np.radians(deg)
    R = np.eye(3) + np.sin(t) * K_ + (1 - np.cos(t)) * (K_ @ K_)
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = axis_o - R @ axis_o
    return T.tolist()


def test_turning_the_arm_about_the_pin_leaves_the_fixed_base_and_the_pin_alone(engine_class):
    eng, tab = _alone(engine_class)
    try:
        out = eng.outputs(tab)
        assert next(i for i in out.instances if i.id == "I1").fixed is True
        start = _placements(eng, tab)
        o, z = _pin_axis(eng, tab)
        _drag(eng, tab, "I2", [_turn_about(o, z, 20), _turn_about(o, z, 35)])
        end = _placements(eng, tab)
        assert np.allclose(end["I1"], start["I1"], atol=1e-9)
        assert np.allclose(end["I3"], start["I3"], atol=1e-6)
        # the arm turned by 35 degrees about the pin axis: its rotation part is that rotation times where it began
        want = np.array(_turn_about(o, z, 35)) @ start["I2"]
        assert np.allclose(end["I2"], want, atol=1e-3)
    finally:
        eng.close()


def test_sliding_the_arm_which_the_revolute_forbids_leaves_the_base_alone(engine_class):
    eng, tab = _alone(engine_class)
    try:
        start = _placements(eng, tab)
        _drag(eng, tab, "I2", [_tx(30, 0, 0)])
        end = _placements(eng, tab)
        assert np.allclose(end["I1"], start["I1"], atol=1e-9)
        assert np.allclose(end["I3"], start["I3"], atol=1e-6)
        assert np.allclose(end["I2"][:3, :3], start["I2"][:3, :3], atol=1e-6)     # the mate keeps it on the pin
    finally:
        eng.close()
