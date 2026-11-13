"""A Variable Studio reference can take a subset of the Studio's names, and Document.variable_table() tracks
which definitions a later variable or reference overrides."""
import pytest

from conftest import add
from freeshape import features as F
from freeshape.document import Document
from freeshape.engine import InProcessEngine, ProcessEngine
from freeshape.engine import process as P
from freeshape.variable_studio import VariableStudioDoc

REF = "variable_studio_ref"


@pytest.fixture(scope="module", autouse=True)
def _no_spares_left():
    yield
    P.SPARES.clear()


def ref(fid, names=None, path="V.fvars"):
    p = {"source": {"kind": "variables", "path": path, "name": "V"}}
    if names is not None:
        p["names"] = names
    return {"id": fid, "type": REF, "name": "Variables", "suppressed": False, "params": p}


def var(fid, name, expr):
    return {"id": fid, "type": "variable", "name": name, "suppressed": False, "params": {"name": name, "expr": expr}}


def doc_with(feats, studio=None):
    d = Document()
    studio = {"w": 20.0, "t": 3.0} if studio is None else studio
    d.var_inputs = lambda src: None if studio is None else dict(studio)
    for f in feats:
        d.add_feature(f)
    return d


def test_subset_brings_only_the_listed_names():
    d = doc_with([ref("R", ["w"])])
    assert d.state.vars == {"w": 20.0}
    assert not d.errors


def test_unlisted_name_is_unknown_in_a_sketch_expression():
    d = doc_with([ref("R", ["w"]), var("X", "z", "#t")])
    assert d.errors["X"] == "Unknown variable #t"


def test_no_names_key_brings_everything_and_file_is_unchanged(tmp_path):
    d = doc_with([ref("R")])
    assert d.state.vars == {"w": 20.0, "t": 3.0}
    assert "names" not in d.feature("R")["params"]
    one, two = str(tmp_path / "one.fshape"), str(tmp_path / "two.fshape")
    d.save(one)
    again = Document.load(one)
    again.save(two)
    assert open(one, "rb").read() == open(two, "rb").read()


def test_variable_table_marks_overridden_and_later_value_wins():
    d = doc_with([ref("R", ["w"]), var("L", "w", "7")])
    assert d.state.vars["w"] == 7
    assert d.variable_table() == [
        {"name": "w", "value": 20.0, "feature": "R", "overridden": True},
        {"name": "w", "value": 7.0, "feature": "L", "overridden": False}]


def test_variable_table_reference_overrides_earlier_variable_and_skips_suppressed():
    d = doc_with([var("L", "t", "1"), ref("R", ["t"]), var("S", "t", "9")])
    d.feature("S")["suppressed"] = True
    d.rebuild()
    rows = d.variable_table()
    assert [(r["feature"], r["overridden"]) for r in rows] == [("L", True), ("R", False)]
    assert d.state.vars["t"] == 3.0


def test_ticked_name_missing_from_the_studio_is_a_red_reference():
    d = doc_with([ref("R", ["w", "gone"])])
    assert "#gone" in d.errors["R"]


def _studio(tmp_path):
    v = VariableStudioDoc()
    v.add_row("w", "20 mm")
    v.add_row("t", "3 mm")
    path = str(tmp_path / "V.fvars")
    v.save(path)
    a = Document()
    a.var_inputs = lambda src: {"w": 20.0, "t": 3.0}
    a.add_feature(ref("R", ["w"], path))
    a.add_feature(var("OK", "k", "#w"))
    a.add_feature(var("BAD", "m", "#t"))
    apath = str(tmp_path / "A.fshape")
    a.save(apath)
    return path, apath


@pytest.mark.parametrize("cls", [InProcessEngine, ProcessEngine])
def test_subset_follows_an_unsaved_studio_edit_on_both_executors(cls, tmp_path):
    vpath, apath = _studio(tmp_path)
    e = cls()
    try:
        v, a = e.open_tab(vpath), e.open_tab(apath)
        out = e.outputs(a)
        assert out.variables.get("k") == 20 and "t" not in out.variables
        assert "Unknown variable #t" in out.errors["BAD"]
        e.submit_edit(v, {"op": "set_row", "index": 0, "expression": "44 mm"})
        out = e.outputs(a)
        assert out.variables["k"] == 44 and "t" not in out.variables
        assert "Unknown variable #t" in out.errors["BAD"]
    finally:
        e.close()
