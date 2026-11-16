"""I3.3: the eye of the Parts list is `set_part_override` with `hidden`. It sets the Document's hidden-parts view
setting (the desktop's toggle_part_hidden): the next outputs carry the flag, it is saved, and it is not an undo step."""
import json

import pytest

from conftest import block_doc
from freeshape.engine import EngineError, InProcessEngine, ProcessEngine

ENGINES = {"inprocess": InProcessEngine, "process": ProcessEngine}


@pytest.fixture(params=sorted(ENGINES))
def studio(request, tmp_path):
    doc = block_doc()
    path = str(tmp_path / "t.fshape")
    doc.save(path)
    e = ENGINES[request.param]()
    yield e, e.open_tab(path), path
    e.close()


def hidden_of(e, tab):
    return [p.hidden for p in e.outputs(tab).parts]


def test_hidden_flag_hides_and_shows_a_part(studio):
    e, tab, _ = studio
    pid = e.outputs(tab).parts[0].id
    assert hidden_of(e, tab) == [False]
    e.submit_edit(tab, {"op": "set_part_override", "part": pid, "hidden": True})
    assert hidden_of(e, tab) == [True]
    e.submit_edit(tab, {"op": "set_part_override", "part": pid, "hidden": False})
    assert hidden_of(e, tab) == [False]


def test_hidden_goes_with_a_name_and_a_colour_in_one_edit(studio):
    e, tab, _ = studio
    pid = e.outputs(tab).parts[0].id
    e.submit_edit(tab, {"op": "set_part_override", "part": pid, "name": "Lid", "color": [1, 0, 0], "hidden": True})
    p = e.outputs(tab).parts[0]
    assert (p.name, p.hidden) == ("Lid", True)
    assert tuple(p.color) == pytest.approx((1, 0, 0))


def test_hidden_is_saved_with_the_tab(studio, tmp_path):
    e, tab, _ = studio
    pid = e.outputs(tab).parts[0].id
    e.submit_edit(tab, {"op": "set_part_override", "part": pid, "hidden": True})
    out = str(tmp_path / "saved.fshape")
    e.submit_edit(tab, {"op": "save", "path": out})
    assert json.loads(bytes(e.tab_data(tab)).decode("utf-8"))["hidden"] == [pid]
    again = InProcessEngine()
    try:
        assert hidden_of(again, again.open_tab(out)) == [True]
    finally:
        again.close()


def test_hidden_is_refused_for_an_unknown_part_or_a_non_boolean(studio):
    e, tab, _ = studio
    pid = e.outputs(tab).parts[0].id
    with pytest.raises(EngineError):
        e.submit_edit(tab, {"op": "set_part_override", "part": "nope", "hidden": True})
    with pytest.raises(EngineError):
        e.submit_edit(tab, {"op": "set_part_override", "part": pid, "hidden": "yes"})
    assert hidden_of(e, tab) == [False]
