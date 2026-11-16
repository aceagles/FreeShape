"""F16: the eye of a sketch, plane or axis row is `set_features_hidden`. It sets the Document's hidden_features view
setting (the desktop's set_features_hidden): the next outputs carry the flag, it is saved, and it is not an undo step."""
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
    return {f.id: f.hidden for f in e.outputs(tab).features}


def test_hides_and_shows_a_sketch_without_an_undo_step(studio):
    e, tab, _ = studio
    sk = next(f.id for f in e.outputs(tab).features if f.type == "sketch")
    assert hidden_of(e, tab)[sk] is False
    can_undo = e.outputs(tab).can_undo
    e.submit_edit(tab, {"op": "set_features_hidden", "ids": [sk], "value": True})
    assert hidden_of(e, tab)[sk] is True
    assert e.outputs(tab).can_undo == can_undo
    e.submit_edit(tab, {"op": "set_features_hidden", "ids": [sk], "value": False})
    assert hidden_of(e, tab)[sk] is False


def test_hidden_is_saved_in_hidden_features_and_a_standard_plane_is_accepted(studio, tmp_path):
    e, tab, _ = studio
    sk = next(f.id for f in e.outputs(tab).features if f.type == "sketch")
    e.submit_edit(tab, {"op": "set_features_hidden", "ids": [sk, "Top"], "value": True})
    out = str(tmp_path / "saved.fshape")
    e.submit_edit(tab, {"op": "save", "path": out})
    assert json.loads(bytes(e.tab_data(tab)).decode("utf-8"))["hidden_features"] == sorted([sk, "Top"])
    again = InProcessEngine()
    try:
        assert hidden_of(again, again.open_tab(out))[sk] is True
    finally:
        again.close()


def test_refused_for_an_unknown_id_a_feature_without_an_eye_or_a_bad_value(studio):
    e, tab, _ = studio
    sk = next(f.id for f in e.outputs(tab).features if f.type == "sketch")
    ex = next(f.id for f in e.outputs(tab).features if f.type == "extrude")
    for edit in ({"ids": ["nope"], "value": True}, {"ids": [ex], "value": True},
                 {"ids": [sk], "value": "yes"}, {"ids": sk, "value": True}):
        with pytest.raises(EngineError):
            e.submit_edit(tab, {"op": "set_features_hidden", **edit})
    assert not any(hidden_of(e, tab).values())
