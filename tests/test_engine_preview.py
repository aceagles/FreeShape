"""I5.4: the engine's `preview` request (contract.Engine.preview): a Part Studio rebuilt with a feature's proposed
params, as the desktop's dialog preview does, without committing anything. Each test opens its own engine, so each
passes alone."""
import json
import os
import shutil
import time

import pytest

from freeshape import kernel as K
from freeshape.engine import InProcessEngine, ProcessEngine
from freeshape.engine import process as P
from freeshape.engine import worker as W
from freeshape.engine.contract import EngineError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRACKET = os.path.join(ROOT, "examples", "documents", "bracket")
BRACKET_FILE = os.path.join(BRACKET, "bracket.fshape")
PROFILE_AREA = 594.0          # the bracket's profile (F1), which its extrude (F2) sweeps 40 mm


def _volume(out):
    return sum(K.volume(K.shape_from_brep(p.brep)) for p in out.parts)


def _params(eng, tab, **change):
    """The bracket extrude's stored params with `change` applied."""
    p = dict(eng.feature(tab, "F2")["feature"]["params"])
    p.update(change)
    return p


def _committed(eng, tab):
    out = eng.outputs(tab)
    state = {"revision": out.revision, "undo": (out.can_undo, out.can_redo), "volume": round(_volume(out), 3),
             "hashes": [p.hash for p in out.parts], "errors": dict(out.errors), "rebuilds": eng.rebuild_counts(),
             "data": eng.tab_data(tab)}
    if isinstance(eng, ProcessEngine):
        t = eng._get(tab)
        state["log"] = json.dumps(t.edits, sort_keys=True)
        state["parent_revision"] = t.revision
    return state


@pytest.fixture
def engines():
    made = []

    def make(kind):
        eng = kind()
        made.append(eng)
        return eng
    try:
        yield make
    finally:
        for eng in made:
            eng.close()
        P.SPARES.clear()


def _hold(gate, limit=300.0):
    """Run in the worker: keep it busy until the test creates `gate` (bounded, so a broken test cannot hang it)."""
    end = time.monotonic() + limit
    while not os.path.exists(gate) and time.monotonic() < end:
        time.sleep(0.01)
    return os.path.exists(gate)


def _preview_rebuilds(tab):
    """Run in the worker: how many previews its Part Studio has built."""
    return W.current().engine._get(tab).previews


@pytest.mark.parametrize("kind", [ProcessEngine, InProcessEngine], ids=["process", "in_process"])
def test_a_preview_at_twice_the_depth_has_twice_the_volume_and_commits_nothing(engines, kind):
    eng = engines(kind)
    tab = eng.open_tab(BRACKET_FILE)
    before = _committed(eng, tab)
    once = eng.preview(tab, "F2", "extrude", _params(eng, tab, depth=40.0))
    twice = eng.preview(tab, "F2", "extrude", _params(eng, tab, depth=80.0))
    assert (once["preview"], twice["preview"]) == (1, 2)
    assert once["ok"] and twice["ok"] and once["error"] is None and twice["feature"] == "F2"
    # the preview stops at the feature it edits, as the desktop's: one extruded part, the profile swept
    assert _volume(once["outputs"]) == pytest.approx(PROFILE_AREA * 40, rel=1e-6)
    assert _volume(twice["outputs"]) == pytest.approx(2 * _volume(once["outputs"]), rel=1e-6)
    assert twice["outputs"].revision == before["revision"]
    after = _committed(eng, tab)
    assert after == before                       # same part, same revision, same undo, same bytes, no rebuild
    assert _volume(eng.outputs(tab)) == pytest.approx(before["volume"], abs=1e-3)


def test_after_a_preview_undo_is_unchanged_and_a_save_writes_the_committed_params(engines, tmp_path):
    folder = str(tmp_path / "Bracket")
    shutil.copytree(BRACKET, folder)
    path = os.path.join(folder, "bracket.fshape")
    eng = engines(ProcessEngine)
    tab = eng.open_tab(path)
    eng.submit_edit(tab, {"op": "rename_feature", "feature": "F2", "name": "Boss"})    # something to undo
    before = _committed(eng, tab)
    assert before["undo"] == (True, False)
    got = eng.preview(tab, "F2", "extrude", _params(eng, tab, depth=80.0))
    assert got["ok"]
    assert _committed(eng, tab) == before
    eng.submit_edit(tab, {"op": "save"})
    with open(path) as fh:
        saved = {f["id"]: f for f in json.load(fh)["features"]}
    assert saved["F2"]["params"]["depth"] == 40.0 and saved["F2"]["name"] == "Boss"
    eng.submit_edit(tab, {"op": "undo"})          # the undo step is the rename, not the preview
    out = eng.outputs(tab)
    assert next(f for f in out.features if f.id == "F2").name == "Extrude 1"
    assert (out.can_undo, out.can_redo) == (False, True)


def test_a_preview_that_cannot_build_names_the_feature_and_the_worker_lives(engines):
    eng = engines(ProcessEngine)
    tab = eng.open_tab(BRACKET_FILE)
    pid = eng.pid(tab)
    before = _committed(eng, tab)
    bad = eng.preview(tab, "F2", "extrude", _params(eng, tab, regions=[], faces=[]))
    assert bad["ok"] is False and bad["error"].startswith("Extrude 1: ")
    assert bad["outputs"] is not None and bad["outputs"].errors == {"F2": bad["error"][len("Extrude 1: "):]}
    worse = eng.preview(tab, "F2", "extrude", _params(eng, tab, depth="not a number"))
    assert worse["ok"] is False and worse["error"].startswith("Extrude 1: ")
    assert eng.errored(tab) is None and eng.pid(tab) == pid
    good = eng.preview(tab, "F2", "extrude", _params(eng, tab, depth=80.0))
    assert good["ok"] and good["preview"] == 3
    assert _volume(good["outputs"]) == pytest.approx(PROFILE_AREA * 80, rel=1e-6)
    assert _committed(eng, tab) == before
    # a request that names nothing to preview is refused, as `feature` refuses it
    for fid, ftype, params in (("F99", "extrude", {}), ("F2", "fillet", {}), ("F2", "extrude", "x")):
        with pytest.raises(EngineError):
            eng.preview(tab, fid, ftype, params)
    assert eng.pid(tab) == pid


def test_three_previews_back_to_back_build_at_most_twice_and_the_last_one_answers(engines, tmp_path):
    eng = engines(ProcessEngine)
    tab = eng.open_tab(BRACKET_FILE)
    params = {d: _params(eng, tab, depth=float(d)) for d in (50, 60, 80)}
    gate = str(tmp_path / "go")
    held = eng._get(tab).pool.submit(_hold, gate)      # the worker is busy until the test says so
    n1, f1 = eng.submit_preview(tab, "F2", "extrude", params[50])
    n2, f2 = eng.submit_preview(tab, "F2", "extrude", params[60])
    n3, f3 = eng.submit_preview(tab, "F2", "extrude", params[80])
    assert (n1, n2, n3) == (1, 2, 3)
    # the second never started: the third replaced it while the first was still with the worker
    assert f2.done() and f2.result() == {"preview": 2, "feature": "F2", "ok": False, "outputs": None,
                                         "error": None, "superseded": True}
    assert not f1.done() and not f3.done()
    open(gate, "w").close()
    assert held.result(timeout=120) is True
    last = f3.result(timeout=120)
    assert last["preview"] == 3 and last["ok"]
    assert _volume(last["outputs"]) == pytest.approx(PROFILE_AREA * 80, rel=1e-6)
    first = f1.result(timeout=120)
    assert first["preview"] == 1 and _volume(first["outputs"]) == pytest.approx(PROFILE_AREA * 50, rel=1e-6)
    assert eng.run_in_worker(tab, _preview_rebuilds, tab) == 2
    assert eng.preview(tab, "F2", "extrude", params[60])["preview"] == 4      # the queue is free again


def test_a_new_feature_is_previewed_where_a_new_feature_goes(engines):
    eng = engines(ProcessEngine)
    tab = eng.open_tab(BRACKET_FILE)
    before = _committed(eng, tab)
    got = eng.preview(tab, None, "extrude", _params(eng, tab, depth=10.0, op="new"))
    assert got["ok"] and got["feature"] is None
    assert len(got["outputs"].parts) == len(eng.outputs(tab).parts) + 1
    assert _committed(eng, tab) == before


def test_only_a_part_studio_previews(engines):
    eng = engines(ProcessEngine)
    asm = eng.new_tab("assembly")
    with pytest.raises(EngineError, match="Part Studio"):
        eng.preview(asm, None, "extrude", {})
    with InProcessEngine() as local:
        asm = local.new_tab("assembly")
        with pytest.raises(EngineError, match="Part Studio"):
            local.preview(asm, None, "extrude", {})
