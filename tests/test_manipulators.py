"""I6.3: the drag arrows of a feature dialog as plain data (dialog_schema.manipulators), what the desktop dialog
reports for the same feature, and the engine preview that carries them to the Browser client. Each test builds its
own state, so each passes alone."""
import os

import numpy as np
import pytest

from freeshape import dialog_schema as DSC
from freeshape import kernel as K
from freeshape.document import PartStudioDoc
from freeshape.engine import InProcessEngine, ProcessEngine, wire
from freeshape.engine import process as P

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRACKET_FILE = os.path.join(ROOT, "examples", "documents", "bracket", "bracket.fshape")


def _bracket():
    """The bracket Document, its extrude F2 and the model before F2."""
    doc = PartStudioDoc.load(BRACKET_FILE)
    f2 = doc.feature("F2")
    return doc, f2, doc.state_before(doc.features.index(f2))


def _extrude_params(doc, f2, **change):
    return dict(f2["params"], **change)


def test_a_10_mm_extrude_has_its_arrow_at_the_profile_centroid_along_the_sketch_normal():
    doc, f2, st = _bracket()
    (m,) = DSC.manipulators("extrude", _extrude_params(doc, f2, depth=10.0, end="blind", op="new"), st)
    prof = DSC.F._profiles(st, f2["params"])
    infos = [K.face_info(f) for f in prof]
    area = sum(i["area"] for i in infos)
    centroid = sum(np.array(i["c"]) * i["area"] for i in infos) / area
    normal = DSC.F._profile_normal(st, f2["params"], prof)
    assert m["field"] == "depth" and m["value"] == 10.0 and m["min"] is None and m["snap"] == "view"
    assert np.allclose(m["origin"], centroid) and np.allclose(m["dir"], normal)
    assert abs(np.linalg.norm(m["dir"]) - 1) < 1e-9 and m["label"] == "10 mm"


def test_the_pure_function_equals_the_desktop_method():
    from test_nav import _window
    had = os.environ.get("FREESHAPE_NO_PLUGINS")              # _window sets it for the whole process: put it back
    app, w, vp = _window()
    doc, f2, st = _bracket()
    w._add_tab(doc)
    app.processEvents()
    try:
        w.start_feature("extrude", "F2")
        d = w.dialog
        d._set_quiet("depth", 10.0)
        d._set_quiet("end", "blind")
        d._set_quiet("flip", False)
        (arrow,) = d.manipulators()
        (m,) = DSC.manipulators("extrude", d.feature["params"], d.base_state)
        assert np.allclose(arrow["origin"], m["origin"]) and np.allclose(arrow["dir"], m["dir"])
        assert arrow["value"] == m["value"] and arrow["min"] == m["min"] and arrow["label"] == m["label"]
        assert arrow["value"] == 10.0
        w.viewport.manipulators = [arrow]
        arrow["on_drag"](-7.0)                                   # dragged back past the profile: flipped, 7 deep
        assert d.feature["params"]["depth"] == 7.0 and d.feature["params"]["flip"] is True
        assert w.viewport.manipulators[0]["label"] == "7 mm"
        w.cancel_dialog()
    finally:
        w.close()
        w.deleteLater()
        if had is None:
            os.environ.pop("FREESHAPE_NO_PLUGINS", None)


def test_flip_symmetric_and_through_all():
    doc, f2, st = _bracket()
    base = _extrude_params(doc, f2, depth=8.0, op="new", end="blind")
    (m,) = DSC.manipulators("extrude", dict(base, flip=True), st)
    assert m["value"] == -8.0
    (m,) = DSC.manipulators("extrude", dict(base, end="symmetric"), st)
    assert m["value"] == 4.0 and m["symmetric"] and m["min"] == 1e-3
    assert DSC.manipulators("extrude", dict(base, end="through"), st) == []
    assert DSC.manipulators("fillet", {"edges": [], "radius": 2.0}, st) == []


def test_an_offset_plane_has_an_arrow_on_its_base_plane():
    doc, f2, st = _bracket()
    (m,) = DSC.manipulators("plane", {"type": "offset", "ref": {"kind": "std", "name": "Front"}, "offset": 15.0}, st)
    assert m["field"] == "offset" and m["value"] == 15.0 and m["label"] == "15 mm"
    assert np.allclose(m["dir"], K.std_plane("Front").normal)


def test_the_result_is_plain_data_and_the_module_stays_free_of_qt():
    import json
    doc, f2, st = _bracket()
    got = DSC.manipulators("extrude", _extrude_params(doc, f2), st)
    json.dumps(got)
    assert all(type(x) is float for x in got[0]["origin"] + got[0]["dir"])


@pytest.mark.parametrize("kind", [ProcessEngine, InProcessEngine], ids=["process", "in_process"])
def test_an_extrude_preview_carries_one_arrow_naming_the_depth_field(kind):
    eng = kind()
    try:
        tab = eng.open_tab(BRACKET_FILE)
        params = dict(eng.feature(tab, "F2")["feature"]["params"], depth=12.0, end="blind", flip=False)
        got = eng.preview(tab, "F2", "extrude", params)
        assert got["ok"]
        (m,) = got["outputs"].manipulators
        assert m["field"] == "depth" and m["value"] == 12.0
        assert eng.outputs(tab).manipulators == []               # only a preview carries them
        back = wire.from_wire(wire.to_wire(got["outputs"]))
        assert back.manipulators == got["outputs"].manipulators
    finally:
        eng.close()
        P.SPARES.clear()


def test_a_new_extrude_preview_has_an_arrow_and_a_fillet_preview_has_none():
    eng = InProcessEngine()
    try:
        tab = eng.open_tab(BRACKET_FILE)
        params = dict(eng.feature(tab, "F2")["feature"]["params"], depth=10.0, op="new")
        new = eng.preview(tab, None, "extrude", params)
        assert new["ok"] and [m["field"] for m in new["outputs"].manipulators] == ["depth"]
        fillet = eng.feature(tab, "F3")["feature"]
        assert fillet["type"] == "fillet"
        got = eng.preview(tab, "F3", "fillet", dict(fillet["params"]))
        assert got["ok"] and got["outputs"].manipulators == []
    finally:
        eng.close()
