"""I8.1: the Assembly outputs carry what the client's instance list and drawing need: each instance's name, part,
transform, hidden and fixed flags, and each mate's name, type and suppressed flag, through the wire format."""
import os

import numpy as np
import pytest

from conftest import EXAMPLES_DIR
from freeshape.engine import AssemblyOutputs, InProcessEngine, ProcessEngine, wire

HINGE_ASM = os.path.join(EXAMPLES_DIR, "hinge.fasm")


@pytest.fixture(params=[InProcessEngine, ProcessEngine], ids=["inprocess", "process"])
def engine_class(request):
    return request.param


def _outputs(engine_class, *edits):
    """The hinge Assembly's outputs after `edits`, as they come back from the wire (what the server sends)."""
    eng = engine_class()
    try:
        tab = eng.open_tab(HINGE_ASM)
        for e in edits:
            eng.submit_edit(tab, e)
        out = eng.outputs(tab)
    finally:
        eng.close()
    back = wire.from_wire(wire.to_wire(out))
    assert isinstance(back, AssemblyOutputs)
    return back


def test_hinge_outputs_list_instances_with_transforms_flags_and_mates(engine_class):
    out = _outputs(engine_class)
    assert [i.id for i in out.instances] == ["I1", "I2", "I3"]
    for inst in out.instances:
        assert inst.name
        assert np.asarray(inst.T).shape == (4, 4)
        assert inst.part in out.parts
        assert isinstance(inst.hidden, bool) and isinstance(inst.fixed, bool)
    assert [i.hidden for i in out.instances] == [False, False, False]
    assert sum(i.fixed for i in out.instances) >= 1                      # a hinge is grounded somewhere
    assert len(out.mates) == 2
    for m in out.mates:
        assert m.id and m.name and m.type
        assert m.suppressed is False
    assert all(m.a.instance in {"I1", "I2", "I3"} for m in out.mates)
    assert out.errors == {}
    # a pick in the Assembly names the element it hit: the parts carry the Element names of the studio they link to
    for part in out.parts.values():
        assert part.names["face"] and all(isinstance(n, str) and n for n in part.names["face"].values())


def test_hidden_and_fixed_flags_follow_the_edits(engine_class):
    before = _outputs(engine_class)
    target = next(i.id for i in before.instances if not i.fixed)
    out = _outputs(engine_class, {"op": "set_hidden", "instance": target, "value": True},
                   {"op": "set_fixed", "instance": target, "value": True})
    inst = next(i for i in out.instances if i.id == target)
    assert inst.hidden is True and inst.fixed is True
    assert [i.hidden for i in out.instances].count(True) == 1
