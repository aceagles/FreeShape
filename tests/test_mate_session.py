"""I10.5: the Qt-free mate dialog session (freeshape/mate_session.py) and its engine requests (contract.Engine
mate_dialog_open / _update / _result / _close, the add_mate and replace_mate edits). No Qt. Each test opens its own
copy of the hinge example's Assembly, so each passes alone by node id."""
import copy
import os

import numpy as np
import pytest

from conftest import EXAMPLES_DIR
from freeshape import assembly as A
from freeshape import mate_session as MS
from freeshape.engine import InProcessEngine, ProcessEngine
from freeshape.engine.contract import EngineError

HINGE_ASM = os.path.join(EXAMPLES_DIR, "hinge.fasm")
PLATE, ARM, PIN = "I1", "I2", "I3"
PLATE_PIN_EDGE, ARM_PIN_EDGE = 14, 13          # 0-based: the circular pin edges the hinge's Revolute 1 uses
PLATE_EDGE = {"instance": PLATE, "kind": "edge", "index": PLATE_PIN_EDGE + 1}     # the route's 1-based picks
ARM_EDGE = {"instance": ARM, "kind": "edge", "index": ARM_PIN_EDGE + 1}


def _hinge(engine_class=InProcessEngine, delete_mates=True):
    eng = engine_class()
    tab = eng.open_tab(HINGE_ASM)
    if delete_mates:
        eng.submit_edit(tab, {"op": "delete_mates", "ids": [m.id for m in eng.outputs(tab).mates]})
        away = np.eye(4)
        away[:3, 3] = (30.0, 10.0, 15.0)                  # the Arm off the pin, so a preview has to move it
        eng.submit_edit(tab, {"op": "move_instance", "instance": ARM, "T": away.tolist()})
    return eng, tab


def _doc(eng, tab):
    return eng._get(tab).doc


def _state(eng, tab):
    out = eng.outputs(tab)
    return ({i.id: np.array(i.T, float) for i in out.instances},
            [(m.id, m.type, m.name, dict(m.params)) for m in out.mates])


def _same_state(a, b):
    (pa, ma), (pb, mb) = a, b
    assert set(pa) == set(pb) and ma == mb
    for k in pa:
        assert np.allclose(pa[k], pb[k], atol=1e-9), k


def _on_one_axis(view):
    a, b = view["a"]["world"], view["b"]["world"]
    za, zb = np.array(a["z"]), np.array(b["z"])
    d = np.array(b["o"]) - np.array(a["o"])
    return abs(abs(za @ zb) - 1.0) < 1e-6 and np.linalg.norm(np.cross(d, za)) < 1e-6


@pytest.fixture(params=[InProcessEngine, ProcessEngine], ids=["inprocess", "process"])
def engine_class(request):
    return request.param


# ------------------------------------------------------------------ 1. preview, accept and one undo, cancel
def test_revolute_on_the_pin_edges_previews_them_on_one_axis_and_accept_is_one_undo_step(engine_class):
    eng, tab = _hinge(engine_class)
    try:
        before = _state(eng, tab)
        got = eng.mate_dialog_open(tab, "d1", {"type": "revolute", "picks": []})
        assert got["new"] and got["type"] == "revolute" and got["a"] is None and got["b"] is None
        got = eng.mate_dialog_update(tab, "d1", {"a": MS.parse_changes({"a": PLATE_EDGE})["a"],
                                                 "b": MS.parse_changes({"b": ARM_EDGE})["b"]})
        assert got["error"] is None, got["error"]
        assert got["a"]["instance"] == PLATE and got["a"]["index"] == PLATE_PIN_EDGE + 1
        assert got["a"]["what"] == "circular edge" and got["b"]["instance"] == ARM
        assert _on_one_axis(got)
        assert [v["dof"] for v in got["values"]] == ["rz"] and got["values"][0]["unit"] == "°"
        # the preview moved the Arm, and the committed Assembly did not move
        assert not np.allclose(np.array(got["placements"][ARM]), before[0][ARM])
        _same_state(_state(eng, tab), before)
        edit = eng.mate_dialog_result(tab, "d1")
        assert edit["op"] == "add_mate" and edit["mate"]["id"] == got["mate"]
        eng.submit_edit(tab, edit)
        eng.mate_dialog_close(tab, "d1")
        out = eng.outputs(tab)
        assert [(m.id, m.type) for m in out.mates] == [(got["mate"], "revolute")]
        assert np.allclose(np.array(next(i.T for i in out.instances if i.id == ARM)), got["placements"][ARM],
                           atol=1e-6)
        eng.submit_edit(tab, {"op": "undo"})
        _same_state(_state(eng, tab), before)
    finally:
        eng.close()


def test_cancel_leaves_the_instances_and_mates_as_they_were(engine_class):
    eng, tab = _hinge(engine_class)
    try:
        before = _state(eng, tab)
        eng.mate_dialog_open(tab, "d1", {"type": "revolute",
                                         "picks": MS.parse_picks([PLATE_EDGE, ARM_EDGE])})
        eng.mate_dialog_update(tab, "d1", {"offset": 5.0, "turn90": True})
        eng.mate_dialog_close(tab, "d1")
        _same_state(_state(eng, tab), before)
        with pytest.raises(EngineError, match="no mate dialog"):
            eng.mate_dialog_update(tab, "d1", {})
    finally:
        eng.close()


def test_the_session_alone_previews_restores_and_cancels():
    eng, tab = _hinge()
    try:
        doc = _doc(eng, tab)
        inst, mates = copy.deepcopy(doc.instances), copy.deepcopy(doc.mates)
        s = MS.MateSession.start(doc, "revolute")
        assert s.pick_element("a", PLATE, "edge", PLATE_PIN_EDGE) == "b"
        assert s.pick_element("b", ARM, "edge", ARM_PIN_EDGE) is None
        assert s.preview() == ""                         # the desktop's preview: the Assembly stays previewed
        assert doc.mate(s.mate["id"]) is s.mate and not np.allclose(doc.T(ARM), np.array(
            next(i for i in inst if i["id"] == ARM)["T"], float))
        s.cancel()
        assert doc.instances == inst and doc.mates == mates
    finally:
        eng.close()


# ------------------------------------------------------------------ 2. same-part refusal; type change
def test_a_second_side_on_the_first_sides_instance_is_refused_with_the_desktops_message():
    eng, tab = _hinge()
    try:
        s = MS.MateSession.start(_doc(eng, tab), "revolute")
        s.pick_element("a", PLATE, "edge", PLATE_PIN_EDGE)
        with pytest.raises(MS.MateSessionError) as e:
            s.pick_element("b", PLATE, "edge", PLATE_PIN_EDGE - 1)
        assert str(e.value) == "Pick the second connector on a different part." == MS.SAME_PART
        assert s.mate["b"] is None
        # through the engine: the update answers with the message and leaves the side empty
        eng.mate_dialog_open(tab, "d1", {"type": "revolute", "picks": MS.parse_picks([PLATE_EDGE])})
        same = dict(PLATE_EDGE, index=PLATE_PIN_EDGE)
        got = eng.mate_dialog_update(tab, "d1", MS.parse_changes({"b": same}))
        assert got["error"] == MS.SAME_PART and got["b"] is None and got["a"]["instance"] == PLATE
    finally:
        eng.close()


def test_changing_the_type_to_fastened_updates_the_preview_and_the_mate_values():
    eng, tab = _hinge()
    try:
        rev = eng.mate_dialog_open(tab, "d1", {"type": "revolute", "picks": MS.parse_picks([PLATE_EDGE, ARM_EDGE])})
        assert rev["name"] == "Revolute 1" and [v["dof"] for v in rev["values"]] == ["rz"]
        fas = eng.mate_dialog_update(tab, "d1", {"type": "fastened"})
        assert fas["type"] == "fastened" and fas["name"] == "Fastened 1" and fas["values"] == []
        assert fas["dof"] == rev["dof"] - 1                  # the Arm's turn about the pin is gone
        assert _on_one_axis(fas)
        turned = eng.mate_dialog_update(tab, "d1", {"turn90": True})
        assert turned["angle"] == 90.0
        assert not np.allclose(np.array(turned["placements"][ARM]), np.array(fas["placements"][ARM]), atol=1e-6)
    finally:
        eng.close()


# ------------------------------------------------------------------ 3. editing a stored mate
def test_editing_an_existing_mates_offset_and_accepting_replaces_it_in_place_keeping_its_id(engine_class):
    eng, tab = _hinge(engine_class, delete_mates=False)
    try:
        out = eng.outputs(tab)
        order = [m.id for m in out.mates]
        rev = next(m for m in out.mates if m.type == "revolute")
        arm0 = np.array(next(i.T for i in out.instances if i.id == ARM), float)
        got = eng.mate_dialog_open(tab, "d1", {"mate": rev.id})
        assert not got["new"] and got["mate"] == rev.id and got["name"] == rev.name
        got = eng.mate_dialog_update(tab, "d1", {"offset": 5.0})
        assert got["offset"] == 5.0 and got["error"] is None
        edit = eng.mate_dialog_result(tab, "d1")
        assert edit["op"] == "replace_mate" and edit["mate"]["id"] == rev.id
        eng.submit_edit(tab, edit)
        out = eng.outputs(tab)
        assert [m.id for m in out.mates] == order
        m = next(m for m in out.mates if m.id == rev.id)
        assert m.params["offset"] == 5.0 and m.name == rev.name and m.type == "revolute"
        arm1 = np.array(next(i.T for i in out.instances if i.id == ARM), float)
        assert abs(np.linalg.norm(arm1[:3, 3] - arm0[:3, 3]) - 5.0) < 1e-4      # moved 5 mm along the pin
        eng.submit_edit(tab, {"op": "undo"})
        assert next(m for m in eng.outputs(tab).mates if m.id == rev.id).params["offset"] == 0.0
    finally:
        eng.close()


# ------------------------------------------------------------------ 4. fewer than two parts
def test_with_fewer_than_two_instances_opening_fails_with_the_desktops_message():
    eng, tab = _hinge()
    try:
        eng.submit_edit(tab, {"op": "delete_instances", "ids": [ARM, PIN]})
        with pytest.raises(MS.MateSessionError, match="^Insert at least two parts before adding a mate$"):
            MS.MateSession.start(_doc(eng, tab), "revolute")
        with pytest.raises(EngineError, match="Insert at least two parts before adding a mate"):
            eng.mate_dialog_open(tab, "d1", {"type": "revolute", "picks": []})
    finally:
        eng.close()


# ------------------------------------------------------------------ the rest of the card
def test_a_side_from_an_instances_own_connector_goes_through_named_connector():
    eng, tab = _hinge()
    try:
        doc = _doc(eng, tab)
        cid = doc.add_instance_origin_connector(ARM)
        eng.mate_dialog_open(tab, "d1", {"type": "fastened", "picks": MS.parse_picks([PLATE_EDGE])})
        got = eng.mate_dialog_update(tab, "d1", MS.parse_changes({"b": {"instance": ARM, "connector": cid}}))
        assert got["error"] is None, got["error"]
        assert got["b"] == {**got["b"], "instance": ARM, "kind": "origin", "index": None, "connector": cid}
        edit = eng.mate_dialog_result(tab, "d1")
        assert edit["mate"]["b"]["named"] == cid
        bad = eng.mate_dialog_update(tab, "d1", MS.parse_changes({"b": {"instance": ARM, "connector": "nope"}}))
        assert bad["error"].startswith("Can't use that:") and bad["b"]["connector"] == cid
    finally:
        eng.close()


def test_accept_with_a_side_empty_is_refused_and_the_dialog_stays_open():
    eng, tab = _hinge()
    try:
        eng.mate_dialog_open(tab, "d1", {"type": "revolute", "picks": MS.parse_picks([PLATE_EDGE])})
        with pytest.raises(EngineError, match="Pick a mate connector on each of the two parts first."):
            eng.mate_dialog_result(tab, "d1")
        assert eng.mate_dialog_update(tab, "d1", {})["a"]["instance"] == PLATE
    finally:
        eng.close()


def test_add_mate_and_replace_mate_edits_check_what_they_are_given():
    eng, tab = _hinge()
    try:
        s = MS.MateSession.start(_doc(eng, tab), "revolute")
        s.pick_element("a", PLATE, "edge", PLATE_PIN_EDGE)
        s.pick_element("b", ARM, "edge", ARM_PIN_EDGE)
        m = s.result()
        for bad, msg in ((dict(m, type="glue"), "mate type"), (dict(m, b=None), "mate.b"),
                         (dict(m, b=dict(m["b"], inst=PLATE)), "different part"),
                         (dict(m, a=dict(m["a"], inst="I99")), "no instance")):
            with pytest.raises(EngineError, match=msg):
                eng.submit_edit(tab, {"op": "add_mate", "mate": bad})
        with pytest.raises(EngineError, match="no mate"):
            eng.submit_edit(tab, {"op": "replace_mate", "mate": m})
        assert eng.outputs(tab).mates == []
        eng.submit_edit(tab, {"op": "add_mate", "mate": m})
        with pytest.raises(EngineError, match="in use"):
            eng.submit_edit(tab, {"op": "add_mate", "mate": m})
    finally:
        eng.close()


def test_parse_changes_names_the_field_it_refuses():
    for body, msg in (({"colour": 1}, "colour: unknown field"), ({"type": "glue"}, "type:"),
                      ({"offset": "5"}, "offset:"), ({"flip": 1}, "flip:"), ({"a": {"instance": "I1"}}, "a.kind"),
                      ({"a": {"instance": "I1", "kind": "edge", "index": 0}}, "a.index"),
                      ({"b": {"instance": "I1", "connector": "C1", "kind": "edge"}}, "b.kind: unknown field"),
                      ({"name": ""}, "name:")):
        with pytest.raises(ValueError, match=msg):
            MS.parse_changes(body)
    assert MS.parse_changes({"a": PLATE_EDGE, "b": None, "angle": 30})["a"]["index"] == PLATE_PIN_EDGE
    with pytest.raises(ValueError, match="picks"):
        MS.parse_picks([PLATE_EDGE, ARM_EDGE, PLATE_EDGE])


def test_the_session_module_imports_no_qt():
    import ast
    src = open(MS.__file__, encoding="utf-8").read()
    names = {n.module or "" for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ImportFrom)}
    names |= {a.name for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Import) for a in n.names}
    assert not any("PySide" in x or x.startswith("ui") or ".ui" in x for x in names)
