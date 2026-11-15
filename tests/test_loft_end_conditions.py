"""Loft end conditions (I7.2): Normal to profile and Tangent to profile, with a signed magnitude, built
by Hermite skinning (the route spike I7.1 chose; scripts/spikes/op2_loft.result.txt). A loft with no
condition keeps the legacy ThruSections route, and both routes give the same face names.
"""
import math
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface  # noqa: E402
from OCP.BRepAlgoAPI import BRepAlgoAPI_Section  # noqa: E402
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon  # noqa: E402
from OCP.BRep import BRep_Tool  # noqa: E402
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf  # noqa: E402
from OCP.GeomAbs import GeomAbs_Plane  # noqa: E402
from OCP.GeomLProp import GeomLProp_SLProps  # noqa: E402
from OCP.gp import gp_Dir, gp_Pln, gp_Pnt  # noqa: E402

from conftest import TOP, add, only_part, region_refs  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.document import PartStudioDoc  # noqa: E402
from test_loft_holes import _disc  # noqa: E402
from test_naming_core_features import add_sketch, assert_all_named, widen  # noqa: E402
from test_naming_sweep_loft_import import add_sketch_on  # noqa: E402

Z = np.array([0.0, 0.0, 1.0])
NORMAL = {"cond": "normal", "mag": 1.0}
TANGENT = {"cond": "tangent", "mag": 1.0}


def _result_skin():
    """The skinning method and angle scripts/spikes/op2_loft.result.txt decided on."""
    path = os.path.join(os.path.dirname(__file__), "..", "scripts", "spikes", "op2_loft.result.txt")
    line = next(ln for ln in open(path).read().splitlines() if ln.startswith("DECISION"))
    kv = dict(t.split("=", 1) for t in line.split()[2:])
    return kv["skin"], float(kv["skin_angle_deg"])


SKIN, SKIN_ANGLE = _result_skin()


def _square(cx, z, s=20.0):
    p = BRepBuilderAPI_MakePolygon()
    for x, y in ((cx - s / 2, -s / 2), (cx + s / 2, -s / 2), (cx + s / 2, s / 2), (cx - s / 2, s / 2)):
        p.Add(gp_Pnt(x, y, z))
    p.Close()
    return BRepBuilderAPI_MakeFace(p.Wire(), True).Face()


def _squares():
    return [_square(0, 0), _square(15, 30)]


def _circles():
    return [_disc(0, 10), _disc(30, 10)]


def _is_plane(f):
    return BRepAdaptor_Surface(f).GetType() == GeomAbs_Plane


def _caps(shape):
    return [f for f in K.faces(shape) if _is_plane(f) and abs(abs(K.face_info(f)["n"][2]) - 1) < 1e-9]


def _end_normals(face, n_per_edge=8):
    """Unit normals of `face` at n points on each of its edges that lie in z = 0 or z = 30."""
    surf = BRep_Tool.Surface_s(face)
    out = []
    for e in K.edges(face):
        ad = BRepAdaptor_Curve(e)
        a, b = ad.FirstParameter(), ad.LastParameter()
        pa, pb = ad.Value(a), ad.Value(b)
        if not any(abs(pa.Z() - z) < 1e-6 and abs(pb.Z() - z) < 1e-6 for z in (0.0, 30.0)):
            continue
        for k in range(n_per_edge):
            p = ad.Value(a + (b - a) * (k + 0.5) / n_per_edge)
            pr = GeomAPI_ProjectPointOnSurf(p, surf)
            u, v = pr.LowerDistanceParameters()
            props = GeomLProp_SLProps(surf, u, v, 1, 1e-9)
            n = props.Normal()
            out.append(np.array([n.X(), n.Y(), n.Z()]))
    return out


def _side_faces(shape):
    caps = _caps(shape)
    return [f for f in K.faces(shape) if not any(f.IsSame(c) for c in caps)]


def _section(shape, z):
    """The face of `shape`'s cross-section at height z."""
    sec = BRepAlgoAPI_Section(shape, gp_Pln(gp_Pnt(0, 0, z), gp_Dir(0, 0, 1)))
    sec.Build()
    wire = K.make_wire(K.edges(sec.Shape()))
    return BRepBuilderAPI_MakeFace(wire, True).Face()


def _volume(shape):
    """Volume integrated to 1e-9: the default integration is only good to about 1e-3 on the skinned
    B-spline faces, far coarser than the geometry."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g, 1e-9)
    return g.Mass()


def _close(a, b, rel=1e-6):
    assert abs(a - b) <= rel * max(abs(a), abs(b)), (a, b)


# --------------------------------------------------------------------------- kernel

def test_normal_to_profile_leaves_both_squares_square_to_z():
    body = K.loft_skin(_squares(), NORMAL, NORMAL)
    assert len(K.solids(body)) == 1 and K.is_valid(body)
    assert len(K.faces(body)) == 6
    caps = _caps(body)
    assert len(caps) == 2 and all(abs(K.face_info(c)["area"] - 400) < 1e-6 for c in caps)
    limit = math.sin(math.radians(0.1 if SKIN == "hermite" else SKIN_ANGLE + 0.5))
    normals = [n for f in _side_faces(body) for n in _end_normals(f)]
    assert len(normals) == 4 * 2 * 8
    assert max(abs(float(n @ Z)) for n in normals) < limit


def test_without_a_condition_the_x_faces_lean_26_57_degrees():
    body = K.loft(_squares())
    lean = []
    for f in _side_faces(body):
        ns = _end_normals(f)
        if abs(ns[0][0]) > 0.5:                   # the two faces facing x
            lean.append(max(math.degrees(math.asin(min(1.0, abs(float(n @ Z))))) for n in ns))
    assert len(lean) == 2
    for a in lean:
        assert abs(a - math.degrees(math.atan(15 / 30))) < 0.01, a


def test_magnitude_holds_the_loft_over_the_start_longer():
    xs = []
    for m in (0.5, 1.0, 2.0):
        c = {"cond": "normal", "mag": m}
        body = K.loft_skin(_squares(), c, c)
        xs.append(K.face_info(_section(body, 7.5))["c"][0])
        assert abs(K.face_info(_section(body, 15.0))["c"][0] - 7.5) < 1e-6
    assert xs[0] > xs[1] > xs[2], xs


@pytest.mark.parametrize("mag", [1.0, -1.0])
def test_tangent_to_profile_bulges_out_or_in(mag):
    c = {"cond": "tangent", "mag": mag}
    body = K.loft_skin(_circles(), c, c)
    assert len(K.solids(body)) == 1 and K.is_valid(body)
    limit = math.cos(math.radians(0.5 if SKIN == "hermite" else SKIN_ANGLE + 0.5))
    normals = [n for f in _side_faces(body) for n in _end_normals(f)]
    assert normals and min(abs(float(n @ Z)) for n in normals) > limit
    r = math.sqrt(K.face_info(_section(body, 15.0))["area"] / math.pi)
    assert (r > 10.0) if mag > 0 else (r < 10.0), r


def test_holes_take_the_outer_condition():
    body = K.loft_skin([_disc(0, 20, [(0, 0, 10)]), _disc(25, 15, [(0, 0, 5)])], NORMAL, NORMAL)
    outer = K.loft_skin([_disc(0, 20), _disc(25, 15)], NORMAL, NORMAL)
    inner = K.loft_skin([_disc(0, 10), _disc(25, 5)], NORMAL, NORMAL)
    assert len(K.solids(body)) == 1 and K.is_valid(body)
    _close(_volume(body), _volume(outer) - _volume(inner))
    assert len(K.faces(body)) == 4


def test_one_end_only_and_three_profiles():
    body = K.loft_skin(_squares(), NORMAL, None)
    assert len(K.solids(body)) == 1 and K.is_valid(body) and len(K.faces(body)) == 6
    body = K.loft_skin([_square(0, 0), _square(5, 15, 14), _square(15, 30)], TANGENT, NORMAL)
    assert len(K.solids(body)) == 1 and K.is_valid(body) and len(K.faces(body)) == 6


# --------------------------------------------------------------------------- feature

def _doc(**cond):
    doc = PartStudioDoc()
    a, ids = add_sketch(doc, -10, -10, 20, 20)
    pl = add(doc, "plane", type="offset", refs=[], ref=TOP, offset=30.0)
    b, _ = add_sketch_on(doc, {"kind": "plane", "fid": pl["id"]}, 5, -10, 20, 20)
    add(doc, "loft", op="new", profiles=[{"kind": "region", "ref": region_refs(doc, a)[0]},
                                         {"kind": "region", "ref": region_refs(doc, b)[0]}], **cond)
    return doc, a, ids


def _names(doc):
    part = only_part(doc)
    named = assert_all_named(doc.state, part)
    return {named.name_of(f) for f in K.faces(part.shape)}


def test_legacy_and_skinning_give_the_same_face_names():
    legacy, _, _ = _doc()
    skin, _, _ = _doc(start=NORMAL, end=NORMAL)
    assert len(_names(skin)) == 6
    assert _names(legacy) == _names(skin)


@pytest.mark.parametrize("cond", [{"start": {"cond": ""}}, {"start": {}, "end": {"cond": "", "mag": 2}}])
def test_an_empty_condition_is_the_legacy_route(cond):
    old, _, _ = _doc()
    new, _, _ = _doc(**cond)
    a, b = only_part(old).shape, only_part(new).shape
    assert K.volume(a) == K.volume(b)
    assert len(K.faces(a)) == len(K.faces(b))
    assert _names(old) == _names(new)


def test_a_dialog_round_trip_keeps_an_old_loft_on_the_legacy_route():
    from freeshape.ui import dialogs as D
    old = {"profiles": [], "ruled": False, "op": "new", "scope": []}
    p = dict(old)
    D._loft_unfold(p)
    D.fields_to_params("loft", D.params_to_fields("loft", p), p)
    D._loft_fold(p, strip=True)
    assert p == old
    p = dict(old, start={"cond": ""})
    D._loft_unfold(p)
    p["end_cond"], p["end_mag"] = "tangent", -2.0
    D._loft_fold(p, strip=True)
    assert "start" not in p and p["end"] == {"cond": "tangent", "mag": -2.0}
    assert F._loft_end(None, p.get("start")) is None
    p = dict(old, ruled=True, start={"cond": "normal", "mag": 1.0})
    D._loft_unfold(p)
    D._loft_fold(p, strip=True)
    assert "start" not in p                   # a ruled loft hides the conditions and drops them


def test_dialog_labels():
    from freeshape.ui import dialogs as D
    labels = {s.get("label") for s in D.SCHEMAS["loft"]}
    assert {"Start profile condition", "End profile condition", "Magnitude"} <= labels
    assert [lab for _v, lab in D.LOFT_CONDITIONS] == ["None", "Normal to profile", "Tangent to profile"]


def test_a_ruled_loft_with_an_end_condition_is_refused():
    with pytest.raises(AssertionError, match="ruled"):
        _doc(ruled=True, start=NORMAL)


def test_magnitude_may_be_an_expression():
    doc, _, _ = _doc(start={"cond": "normal", "mag": "2*1"}, end=NORMAL)
    assert K.is_valid(only_part(doc).shape)


def test_a_fillet_on_a_profile_edge_survives_a_profile_change():
    doc, sid, ids = _doc(start=NORMAL, end=NORMAL)
    part = only_part(doc)
    edge = next(e for e in K.edges(part.shape)       # the start profile's edge at x = -10
                if np.allclose(K.edge_info(e)["c"], (-10, 0, 0), atol=1e-6))
    add(doc, "fillet", edges=[F.make_ref(part.id, edge, "edge", part.shape)], radius=1.0)
    faces_before = len(K.faces(only_part(doc).shape))
    widen(doc, sid, ids["width"], 24.0)
    assert not doc.errors, doc.errors
    assert len(K.faces(only_part(doc).shape)) == faces_before


def test_command_search_finds_loft_by_end_condition():
    from PySide6.QtWidgets import QApplication
    from freeshape.ui.mainwindow import MainWindow
    from freeshape.ui.palette import search_commands
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    app.processEvents()
    for q in ("end condition", "loft tangent"):
        assert "Loft" in [c.name for c in search_commands(w._palette_commands(), q)], q


def test_engine_mode_builds_a_conditioned_loft_in_the_worker(tmp_path):
    """The real app rebuilds in a worker (MainWindow(engine=True)): a saved loft with end conditions opens
    to the same solid as in process, and a changed magnitude committed as the dialog commits it rebuilds."""
    import copy
    import signal
    from PySide6.QtWidgets import QApplication
    from freeshape.engine import process as P
    from freeshape.ui import mainwindow as mw_mod
    from test_engine_picking import _Settings, _spawned
    os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")
    os.environ.setdefault("FREESHAPE_NO_HOME", "1")
    doc, _, _ = _doc(start=NORMAL, end={"cond": "tangent", "mag": 0.5})
    want = _volume(only_part(doc).shape)
    path = str(tmp_path / "loft.fshape")
    doc.save(path)
    app = QApplication.instance() or QApplication([])
    before = set(_spawned())
    w = mw_mod.MainWindow(engine=True)
    w.settings = _Settings()
    try:
        w.open_any(path)
        eng = w.studio_engine
        assert eng.settle(180)
        d = w.doc
        assert eng.attached(d) and eng.displayed(d) and not d.errors, d.errors
        _close(_volume(only_part(d).shape), want)
        loft = copy.deepcopy(next(f for f in d.features if f["type"] == "loft"))
        loft["params"]["end"] = {"cond": "tangent", "mag": 2.0}
        d.replace_feature(loft)
        assert eng.settle(120)
        assert not d.errors, d.errors
        assert abs(_volume(only_part(d).shape) - want) > 1.0
        app.processEvents()
    finally:
        w.studio_engine.shutdown()
        P.SPARES.clear()
        leftover = sorted(set(_spawned()) - before)
        for p in leftover:
            try:
                os.kill(p, signal.SIGKILL)
            except OSError:
                pass
        assert not leftover, f"worker processes outlived the window: {leftover}"
