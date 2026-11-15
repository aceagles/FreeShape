"""Fillets on swept and lofted bodies (I8.5). Each case runs in its own child process with a timeout, so a native
crash or a hang in OpenCascade is a failed assertion here and never ends or stalls the pytest process.
Corpus, rungs and the crash question: scripts/spikes/op2_fillet_corpus.py and its .result.txt.

A case that fails today is an xfail (strict) whose reason is the captured status text; when a later change makes
it build, the test turns into an unexpected pass and fails, which is the cue to remove it from XFAIL."""
import glob
import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts", "spikes"))

import op2_fillet_corpus as C  # noqa: E402

CHILD_TIMEOUT = 180

# (case, radius) -> status text the corpus captured for the plain fillet, same on both OCP builds
XFAIL = {
    ("loft_spline", 1.0): "not_done: the curved edge near (0.427, -0.085, 25.000): "
                          "OpenCascade could not build the round along this edge",
    ("loft_spline", 3.0): "not_done: the curved edge near (0.427, -0.085, 25.000): "
                          "the round could not be followed along this edge",
}

ROWS = [(case, r) for case, (_, nominal) in C.CASES.items() for r in nominal]
_cache = {}


def _run(case, radius):
    if (case, radius) not in _cache:
        _cache[(case, radius)] = C.run_child(case, "%.6g" % radius, "none", timeout=CHILD_TIMEOUT)
    return _cache[(case, radius)]


@pytest.fixture(scope="module", autouse=True)
def _no_core_files():
    yield
    for f in glob.glob(os.path.join(ROOT, "core*")):
        if os.path.isfile(f):
            os.remove(f)


def _param(case, r):
    marks = []
    if (case, r) in XFAIL:
        marks.append(pytest.mark.xfail(reason=XFAIL[(case, r)], strict=True))
    return pytest.param(case, r, id="%s-r%g" % (case, r), marks=marks)


@pytest.mark.parametrize("case,radius", [_param(c, r) for c, r in ROWS])
def test_case_builds_a_valid_single_solid(case, radius):
    res = _run(case, radius)
    assert res["exit"] == "0", res["status"]          # a signal or a hang is never an acceptable outcome
    assert res["built"], res["status"]
    assert res["valid"] and res["nsolids"] == res["nsolids_in"] == 1


def test_swept_outer_edge_at_1mm_removes_the_corner_volume_and_is_g1():
    res = _run("sweep_outer_bend20", 1.0)
    assert res["exit"] == "0" and res["built"], res["status"]
    assert res["valid"] and res["nsolids"] == 1
    expect = (1 - math.pi / 4) * 1.0 ** 2 * res["edge_length"]
    assert res["removed"] == pytest.approx(expect, rel=0.01)
    assert res["fillet_faces"] >= 1 and res["g1"] is not None
    assert res["g1"] < 1e-3                           # sampled at 20 points along each trim line


def test_square_to_circle_loft_rims_fillet_at_1mm():
    res = _run("loft_square_circle", 1.0)
    assert res["exit"] == "0" and res["built"], res["status"]
    assert res["valid"]


def test_the_corpus_has_no_crash_and_no_hang_in_the_cases_run_here():
    bad = {k: v["status"] for k, v in _cache.items() if v["exit"] != "0"}
    assert not bad, bad


def test_i8_6_record_matches_the_decision_lines():
    """I8.6 built nothing because the corpus asked for nothing: every build's DECISION says rescued_by=none
    crash_guard=not_needed, and the record says rungs=none crash_guard=adr with the ADR present."""
    with open(C.RESULT) as fh:
        lines = fh.read().splitlines()
    decisions = [ln for ln in lines if ln.startswith("DECISION fillet_corpus")]
    assert decisions
    for ln in decisions:
        assert "rescued_by=none" in ln and "crash_guard=not_needed" in ln, ln
    assert "I8.6 built: rungs=none crash_guard=adr" in lines
    assert glob.glob(os.path.join(ROOT, "docs", "adr", "*blends-run-in-process.md"))
