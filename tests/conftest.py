"""Shared test helpers and the golden-value mechanism.

Golden values
-------------
`assert_golden("group/key", value, rel_tol)` compares `value` (a dict / list / number tree, usually from
`shape_fingerprint`) with the copy stored in tests/golden/<group>.json:

* ints and bools must match exactly (topology counts, DOF);
* floats must match to `rel_tol` relative, with an absolute floor of `rel_tol * scale` so values that should
  be zero (a centre of mass on an axis) don't demand impossible precision. `scale` defaults to the
  largest "diag" (bounding-box diagonal) found in the expected value, or 1.

Run `pytest tests --update-golden` to (re)write the stored values from the current build. Goldens were
recorded on cadquery-ocp 7.9.3.1.1 (OCCT 7.9.3); each file records the version in "_meta".

`pytest tests --golden-diff-report out.json` additionally lists every numeric deviation from the stored
values at zero tolerance (largest relative first), to see how far a new kernel moved within tolerance.

Helpers here are importable from test modules (`from conftest import ...`): pytest puts tests/ on sys.path.
"""
from __future__ import annotations

import importlib.metadata
import json
import math
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from OCP.Bnd import Bnd_Box  # noqa: E402
from OCP.BRepBndLib import BRepBndLib  # noqa: E402
from OCP.BRepGProp import BRepGProp  # noqa: E402
from OCP.GProp import GProp_GProps  # noqa: E402
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SHELL, TopAbs_SOLID, TopAbs_VERTEX  # noqa: E402
from OCP.TopExp import TopExp  # noqa: E402

from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import ORIGIN, Sketch  # noqa: E402

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")
EXAMPLES_DIR = os.path.join(ROOT, "examples")

OCP_VERSION = importlib.metadata.version("cadquery-ocp")
OCCT_MAJOR = int(OCP_VERSION.split(".")[0])

# tolerance classes for assert_golden
ANALYTIC = 1e-6      # planes, cylinders, cones: exact up to integration noise
FREEFORM = 1e-4      # fillets, lofts, sweeps, threads, anything spline-derived

TOP = {"kind": "std", "name": "Top"}
FRONT = {"kind": "std", "name": "Front"}
RIGHT = {"kind": "std", "name": "Right"}


# =========================================================================== model builders

def rect_lines(s: Sketch, x0, y0, w, h):
    """Four lines (unconstrained) forming a w x h rectangle with its corner at (x0, y0)."""
    p = [s.add_point(x0, y0), s.add_point(x0 + w, y0), s.add_point(x0 + w, y0 + h), s.add_point(x0, y0 + h)]
    return [s.add_line(a, b) for a, b in zip(p, p[1:] + p[:1])]


def sketch_feature(doc: Document, build, plane=None):
    """Add a sketch feature built by build(sketch); returns the feature id. Raises if it errors."""
    s = Sketch()
    build(s)
    s.solve()
    f = doc.make_feature("sketch")
    f["params"].update(plane=plane or TOP, sketch=s.to_dict())
    doc.add_feature(f)
    if doc.errors.get(f["id"]):
        raise AssertionError(f"sketch: {doc.errors[f['id']]}")
    return f["id"]


def region_refs(doc: Document, sid, pick=None):
    """Region refs of a sketch; `pick(face_info) -> bool` filters them."""
    out = []
    for r in doc.state.sketches[sid].regions:
        if pick is None or pick(K.face_info(r)):
            out.append(F.region_ref(doc.state, sid, r))
    return out


def add(doc: Document, ftype, **params):
    """Add a feature with the given params; raises AssertionError if the rebuild reports an error for it."""
    f = doc.make_feature(ftype)
    f["params"].update(params)
    doc.add_feature(f)
    err = doc.errors.get(f["id"])
    if err:
        raise AssertionError(f"{ftype}: {err}")
    return f


def try_add(doc: Document, ftype, **params):
    """Add a feature; returns (feature, error message or None)."""
    f = doc.make_feature(ftype)
    f["params"].update(params)
    doc.add_feature(f)
    return f, doc.errors.get(f["id"])


def block_doc(w=40.0, d=20.0, h=10.0, x0=0.0, y0=0.0, plane=None):
    """A Document holding one w x d x h block extruded from a sketch rectangle at (x0, y0) on `plane`."""
    doc = Document()
    sid = sketch_feature(doc, lambda s: rect_lines(s, x0, y0, w, d), plane)
    add(doc, "extrude", regions=region_refs(doc, sid), depth=h, op="new")
    return doc


def only_part(doc_or_state):
    st = doc_or_state.state if isinstance(doc_or_state, Document) else doc_or_state
    assert len(st.parts) == 1, list(st.parts)
    return next(iter(st.parts.values()))


def pick_faces(part, pred):
    return [f for f in K.faces(part.shape) if pred(K.face_info(f))]


def pick_edges(part, pred, skip_seams=True):
    seams = K.seam_edge_indices(part.shape) if skip_seams else set()
    return [e for i, e in enumerate(K.edges(part.shape)) if i not in seams and pred(K.edge_info(e))]


def face_refs(part, pred, expect=None):
    fs = pick_faces(part, pred)
    if expect is not None:
        assert len(fs) == expect, f"expected {expect} faces, found {len(fs)}"
    assert fs, "no face matched"
    return [F.make_ref(part.id, f, "face", part.shape) for f in fs]


def edge_refs(part, pred, expect=None):
    es = pick_edges(part, pred)
    if expect is not None:
        assert len(es) == expect, f"expected {expect} edges, found {len(es)}"
    assert es, "no edge matched"
    return [F.make_ref(part.id, e, "edge", part.shape) for e in es]


# common predicates on face_info / edge_info dicts
def normal_is(v, tol=1e-6):
    v = np.asarray(v, float)
    return lambda i: i.get("n") is not None and float(np.dot(i["n"], v)) > 1 - tol


def line_along(axis, tol=1e-6):
    a = np.asarray(axis, float)
    return lambda i: i["type"] == int(K.GeomAbs_Line) and abs(abs(float(np.dot(i["d"], a))) - 1) < tol


def centre_near(c, tol=1e-6):
    c = np.asarray(c, float)
    return lambda i: float(np.linalg.norm(np.asarray(i["c"]) - c)) < tol


def both(*preds):
    return lambda i: all(p(i) for p in preds)


# =========================================================================== fingerprints

def _count(shape, kind):
    m = K.TopTools_IndexedMapOfShape()      # the kernel picks the OCP 7 / OCP 8 spelling
    TopExp.MapShapes_s(shape, kind, m)
    return m.Size()


def shape_fingerprint(shape):
    """Volume, surface area, centre of mass, tight bounding box and topology counts of a shape."""
    g = GProp_GProps()
    if K._has_freeform(shape):
        # spline faces (threads, lofts): the adaptive integrator, at the accuracy the kernel itself uses
        BRepGProp.VolumePropertiesGK_s(shape, g, 1e-4, False, False, True)
    else:
        # analytic faces: the plain integrator is exact here and fast. (GK at 1e-6 with the centre of mass
        # takes ~27 s on a block with one countersink cone.)
        BRepGProp.VolumeProperties_s(shape, g)
    volume = g.Mass()
    com = g.CentreOfMass()
    sa = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, sa)
    if abs(volume) < 1e-12:                       # surface bodies: centre of the area instead
        com = sa.CentreOfMass()
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    lo, hi = box.CornerMin(), box.CornerMax()
    lo = [lo.X(), lo.Y(), lo.Z()]
    hi = [hi.X(), hi.Y(), hi.Z()]
    return {
        "volume": volume,
        "area": sa.Mass(),
        "com": [com.X(), com.Y(), com.Z()],
        "bbox": lo + hi,
        "diag": math.dist(lo, hi),
        "solids": _count(shape, TopAbs_SOLID),
        "shells": _count(shape, TopAbs_SHELL),
        "faces": _count(shape, TopAbs_FACE),
        "edges": _count(shape, TopAbs_EDGE),
        "vertices": _count(shape, TopAbs_VERTEX),
        "valid": bool(K.is_valid(shape)),
    }


def state_fingerprint(doc_or_state):
    """{part id: shape_fingerprint} for every part of a Document / ModelState."""
    st = doc_or_state.state if isinstance(doc_or_state, Document) else doc_or_state
    return {pid: shape_fingerprint(p.shape) for pid, p in st.parts.items()}


def matrix_list(M):
    return [[float(x) for x in row] for row in np.asarray(M, float)]


# =========================================================================== golden storage

_GOLDEN_CACHE: dict = {}
_GOLDEN_DIRTY: set = set()
_UPDATE = {"on": False}
_REPORT = {"path": None, "rows": []}


def pytest_addoption(parser):
    parser.addoption("--update-golden", action="store_true", default=False,
                     help="rewrite tests/golden/*.json from the current build instead of comparing")
    parser.addoption("--golden-diff-report", default=None, metavar="PATH",
                     help="write every numeric deviation from the goldens (at zero tolerance) to PATH as JSON")


def pytest_configure(config):
    _UPDATE["on"] = bool(config.getoption("--update-golden"))
    _REPORT["path"] = config.getoption("--golden-diff-report")
    config.addinivalue_line("markers", "golden: compares against stored golden values")
    config.addinivalue_line("markers", "slow: over ~1.5 s; skipped by `-m 'not slow'` while iterating, always run at landing")


# Tests measured over ~1.5 s (pytest --durations, OCP 8.0, 2026-11-12). Kept as one list so the
# fast/full split is visible in one place; re-measure with `--durations=25` when the suite changes.
SLOW_TESTS = {
    "test_golden_features.py::test_every_runner_is_characterised",
    "test_golden_freecad.py::test_fillet_longest_convex_edge[schenkel]",
    "test_golden_freecad.py::test_import_counts[hole_puzzle]",
    "test_golden_freecad.py::test_half_box_cut_checked[hole_puzzle]",
    "test_threads.py::test_tapped_through_and_blind",
    "test_threads.py::test_left_hand_and_clearance",
    "test_threads.py::test_hole_and_thread_features",
    "test_threads.py::test_thread_whole_length_of_plain_rod",
    "test_golden_features.py::test_save_load_roundtrip[thread_internal_auto]",
    "test_golden_features.py::test_save_load_roundtrip[hole_tapped_modelled_m5]",
    "test_golden_features.py::test_save_load_roundtrip[thread_internal_left_hand_clearance]",
    "test_golden_features.py::test_save_load_roundtrip[thread_external_length]",
}


def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.nodeid.split("tests/", 1)[-1] in SLOW_TESTS:
            item.add_marker(pytest.mark.slow)


def pytest_sessionfinish(session, exitstatus):
    if _REPORT["path"]:
        rows = sorted(_REPORT["rows"], key=lambda r: -(r["rel"] or 0))
        with open(_REPORT["path"], "w") as fh:
            json.dump({"cadquery-ocp": OCP_VERSION, "deviations": rows}, fh, indent=1)
    for group in sorted(_GOLDEN_DIRTY):
        data = _GOLDEN_CACHE[group]
        data["_meta"] = {"cadquery-ocp": OCP_VERSION,
                         "note": "written by `pytest tests --update-golden`; see tests/conftest.py"}
        os.makedirs(GOLDEN_DIR, exist_ok=True)
        with open(os.path.join(GOLDEN_DIR, f"{group}.json"), "w") as fh:
            json.dump(data, fh, indent=1, sort_keys=True)
            fh.write("\n")
    _GOLDEN_DIRTY.clear()


def _load_group(group):
    if group not in _GOLDEN_CACHE:
        path = os.path.join(GOLDEN_DIR, f"{group}.json")
        if os.path.exists(path):
            with open(path) as fh:
                _GOLDEN_CACHE[group] = json.load(fh)
        else:
            _GOLDEN_CACHE[group] = {}
    return _GOLDEN_CACHE[group]


def _plain(x):
    """JSON-safe copy (numpy arrays/scalars to lists/floats, tuples to lists)."""
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, np.ndarray)):
        return [_plain(v) for v in x]
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return float(x)
    return x


def _max_diag(x):
    if isinstance(x, dict):
        own = [float(x["diag"])] if isinstance(x.get("diag"), (int, float)) else []
        return max(own + [_max_diag(v) for v in x.values()] + [0.0])
    if isinstance(x, list):
        return max([_max_diag(v) for v in x] + [0.0])
    return 0.0


def golden_diffs(expected, actual, rel_tol, abs_tol, path=""):
    """List of (path, expected, actual, delta) where actual differs from expected."""
    out = []
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [(path, expected, actual, None)]
        for k in sorted(set(expected) | set(actual)):
            if k not in expected or k not in actual:
                out.append((f"{path}/{k}", expected.get(k, "<missing>"), actual.get(k, "<missing>"), None))
            else:
                out += golden_diffs(expected[k], actual[k], rel_tol, abs_tol, f"{path}/{k}")
        return out
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return [(path, expected, actual, None)]
        for i, (e, a) in enumerate(zip(expected, actual)):
            out += golden_diffs(e, a, rel_tol, abs_tol, f"{path}[{i}]")
        return out
    if isinstance(expected, bool) or isinstance(expected, int) and isinstance(actual, int):
        return [] if expected == actual else [(path, expected, actual, None)]
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        d = abs(float(actual) - float(expected))
        if d <= max(rel_tol * abs(float(expected)), abs_tol):
            return []
        return [(path, expected, actual, d)]
    return [] if expected == actual else [(path, expected, actual, None)]


def assert_golden(name, value, rel_tol=ANALYTIC, abs_tol=None):
    """Compare `value` with the stored golden "group/key"; with --update-golden, store it instead."""
    group, key = name.split("/", 1)
    data = _load_group(group)
    actual = _plain(value)
    if _UPDATE["on"]:
        data[key] = actual
        _GOLDEN_DIRTY.add(group)
        return
    if key not in data:
        pytest.fail(f"no golden value for {name!r} — run `pytest tests --update-golden` to record it")
    expected = data[key]
    if abs_tol is None:
        abs_tol = rel_tol * max(1.0, _max_diag(expected))
    if _REPORT["path"]:
        for p, e, a, d in golden_diffs(expected, actual, 0.0, 0.0):
            rel = d / abs(e) if d is not None and isinstance(e, (int, float)) and e else None
            _REPORT["rows"].append({"name": name, "path": p, "expected": e, "actual": a, "abs": d, "rel": rel,
                                    "over_tol": d is None or d > max(rel_tol * abs(float(e)), abs_tol)})
    diffs = golden_diffs(expected, actual, rel_tol, abs_tol)
    if diffs:
        lines = [f"golden mismatch for {name} (rel_tol={rel_tol:g}, abs_tol={abs_tol:g}, ocp {OCP_VERSION}):"]
        for p, e, a, d in diffs[:40]:
            rel = f" rel={d / abs(e):.3g}" if d is not None and isinstance(e, (int, float)) and e else ""
            lines.append(f"  {p or '/'}: expected {e!r} got {a!r}" + (f" (Δ={d:.3g}{rel})" if d is not None else ""))
        if len(diffs) > 40:
            lines.append(f"  ... and {len(diffs) - 40} more")
        pytest.fail("\n".join(lines))


def approx_rel(got, exp, rel=1e-6, abs_=1e-9):
    assert abs(got - exp) <= max(rel * abs(exp), abs_), f"{got} != {exp} (Δ={got - exp:.3g}, rel {rel:g})"


# --------------------------------------------------------------------------- autosave isolation
# Every MainWindow starts a 60 s autosave timer. Tests that build a window and never close it leave it alive in
# the process, and a test that patches MainWindow.autosave_dir / autosave_root (tests/test_mainwindow_store.py)
# redirects every live window, so a leaked dirty window can fire mid-test and drop a stray file into that
# test's folder (seen on CI as an extra "<uuid>.fshape"). Stop the timer of every window still alive after
# each test. No test relies on the timer firing: they call `autosave()` directly.
def stop_leaked_autosave_timers():
    mw = sys.modules.get("freeshape.ui.mainwindow")     # not imported: no window can exist, and don't pull in Qt
    qw = sys.modules.get("PySide6.QtWidgets")
    if mw is None or qw is None or qw.QApplication.instance() is None:
        return
    for w in qw.QApplication.topLevelWidgets():
        if isinstance(w, mw.MainWindow):
            w._autosave_timer.stop()


@pytest.fixture(autouse=True)
def _stop_leaked_autosave_timers():
    yield
    stop_leaked_autosave_timers()


__all__ = [n for n in dir() if not n.startswith("_")]
