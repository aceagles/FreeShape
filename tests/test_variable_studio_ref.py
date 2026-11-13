"""The Variable Studio reference feature ("variable_studio_ref") and the input hashes in the rebuild cache key.
A Part Studio reads a Variable Studio tab's evaluated variables, live (saved or not), through the engine, on
both executors: `inprocess` and `process` (one worker process per tab). Starting a worker costs `import OCP`,
so the engine tests below share one engine per test and open as few tabs as they can."""
import os

import pytest

from conftest import add, rect_lines, region_refs, sketch_feature
from freeshape import features as F
from freeshape import kernel as K
from freeshape.document import Document
from freeshape.engine import InProcessEngine, ProcessEngine
from freeshape.engine import process as P
from freeshape.variable_studio import VariableStudioDoc

ENGINES = {"inprocess": InProcessEngine, "process": ProcessEngine}
REF = "variable_studio_ref"


@pytest.fixture(scope="module", autouse=True)
def _no_spares_left():
    yield
    P.SPARES.clear()


@pytest.fixture(params=sorted(ENGINES))
def engine(request):
    e = ENGINES[request.param]()
    yield e
    e.close()


def ref_feature(fid, path):
    return {"id": fid, "type": REF, "name": "Variables", "suppressed": False,
            "params": {"source": {"kind": "variables", "path": path, "name": "V"}}}


def make_files(tmp_path, local_before=True):
    """V.fvars (w = 20 mm, h = #w * 2) and A.fshape: [x variable] [ref to V] [sketch] [extrude depth #w]."""
    v = VariableStudioDoc()
    v.add_row("w", "20 mm")
    v.add_row("h", "#w * 2")
    vpath = str(tmp_path / "V.fvars")
    v.save(vpath)
    a = Document()
    a.var_inputs = lambda src: {"w": 20.0, "h": 40.0}      # only to build the file: the engine resolves it for real
    add(a, "variable", name="x", expr="1")
    a.add_feature(ref_feature(a.new_id(), vpath))
    sid = sketch_feature(a, lambda s: rect_lines(s, 0, 0, 10, 10))
    add(a, "extrude", regions=region_refs(a, sid), depth="#w", op="new")
    apath = str(tmp_path / "A.fshape")
    a.save(apath)
    return vpath, apath


def height(engine, tab):
    out = engine.outputs(tab)
    assert not out.errors, out.errors
    lo, hi = K.bbox(K.shape_from_brep(out.parts[0].brep))
    return round(float(hi[2] - lo[2]), 6)


def set_w(value):
    return {"op": "set_row", "index": 0, "expression": value}


# =========================================================================== the feature
def test_reference_drives_the_extrude_and_a_later_local_variable_wins(engine, tmp_path):
    vpath, apath = make_files(tmp_path)
    v, a = engine.open_tab(vpath), engine.open_tab(apath)
    assert height(engine, a) == 20
    assert engine.outputs(a).variables["h"] == 40
    assert v in engine.outputs(a).consumes

    engine.submit_edit(v, set_w("35 mm"))
    assert height(engine, a) == 35

    local = {"id": "L1", "type": "variable", "name": "Local", "suppressed": False,
             "params": {"name": "w", "expr": "7"}}
    engine.submit_edit(a, {"op": "add_feature", "feature": local, "index": 2})    # after the reference
    assert height(engine, a) == 7
    engine.submit_edit(v, set_w("50 mm"))
    assert height(engine, a) == 7                       # the local w still wins
    assert engine.outputs(a).variables["h"] == 100      # the other variables still come through


def test_unsaved_upstream_edit_reaches_the_part_studio(engine, tmp_path):
    vpath, apath = make_files(tmp_path)
    v, a = engine.open_tab(vpath), engine.open_tab(apath)
    assert height(engine, a) == 20
    engine.submit_edit(v, set_w("44 mm"))               # V is never saved
    assert VariableStudioDoc.load(vpath).values()[0]["w"] == 20
    assert height(engine, a) == 44


# =========================================================================== the cache key
_RUNS = []


def _install_spy():
    _RUNS.clear()
    for ftype in ("variable", REF, "sketch", "extrude"):
        run = F.RUNNERS[ftype]
        if getattr(run, "spied", False):
            continue

        def spy(state, f, run=run):
            _RUNS.append(f["id"])
            return run(state, f)
        spy.spied, spy.real = True, run
        F.RUNNERS[ftype] = spy


def _take_runs():
    runs = list(_RUNS)
    _RUNS.clear()
    return runs


def _remove_spy():
    for ftype, run in list(F.RUNNERS.items()):
        if getattr(run, "spied", False):
            F.RUNNERS[ftype] = run.real


def in_tab(engine, tab, fn):
    return engine.run_in_worker(tab, fn) if isinstance(engine, ProcessEngine) else fn()


def test_a_changed_input_misses_from_the_reference_on_and_an_unchanged_one_hits(engine, tmp_path):
    vpath, apath = make_files(tmp_path)
    v, a = engine.open_tab(vpath), engine.open_tab(apath)
    ids = [f["id"] for f in Document.load(apath).features]       # [x variable, reference, sketch, extrude]
    try:
        engine.outputs(a)
        in_tab(engine, a, _install_spy)
        engine.submit_edit(v, set_w("20 mm"))            # same value, written differently ("20 mm" is "20mm")
        engine.submit_edit(v, {"op": "set_row", "index": 0, "expression": "20mm"})
        assert height(engine, a) == 20
        assert in_tab(engine, a, _take_runs) == []       # V's output did not change: every feature hits

        engine.submit_edit(v, set_w("30 mm"))
        assert height(engine, a) == 30
        assert in_tab(engine, a, _take_runs) == ids[1:]  # the variable before the reference still hits
    finally:
        in_tab(engine, a, _remove_spy)


# =========================================================================== no engine
def test_unresolved_reference_is_an_error_on_the_feature_and_later_features_still_build(tmp_path):
    vpath, apath = make_files(tmp_path)
    doc = Document.load(apath)
    ref = next(f for f in doc.features if f["type"] == REF)
    assert "Variable Studio not found" in doc.errors[ref["id"]]
    assert doc.errors[doc.features[-1]["id"]].startswith("Unknown variable #w")


def test_cache_key_hashes_the_consumed_inputs(tmp_path):
    _vpath, apath = make_files(tmp_path)
    doc = Document.load(apath)
    values = {"w": 20.0}
    doc.var_inputs = lambda src: dict(values)
    doc.rebuild()
    assert not doc.errors
    keys = set(doc._cache)
    doc.rebuild()
    assert set(doc._cache) == keys                       # nothing changed: the same keys, so all hits
    values["w"] = 25.0
    doc.rebuild()
    fresh = set(doc._cache) - keys
    assert len(fresh) == 3 and not doc.errors            # the reference, the sketch and the extrude
    lo, hi = K.bbox(next(iter(doc.state.parts.values())).shape)
    assert round(float(hi[2] - lo[2]), 6) == 25
