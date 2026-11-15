"""I7.8: Loft with Create = Surface. Open or closed sketch curves (the `curves` list, in pick order) are
lofted into a surface body with no caps and no solid. Without guides ThruSections(solid=False) builds it; with
two or more guides one Gordon face comes from K.network_surface (OpenCascade 8.0 only). End conditions are
refused. The loft dialog's Create field, the command search keywords and the engine window are tested here too."""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex  # noqa: E402
from OCP.BRepExtrema import BRepExtrema_DistShapeShape  # noqa: E402
from OCP.TopExp import TopExp_Explorer  # noqa: E402
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE  # noqa: E402
from OCP.gp import gp_Pnt  # noqa: E402

from conftest import FRONT, OCCT_MAJOR, RIGHT, TOP, add, region_refs, sketch_feature, try_add  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.document import PartStudioDoc  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402
from test_loft_guides import _arc3, REFUSED_79  # noqa: E402
from test_naming_core_features import add_sketch  # noqa: E402

needs_occt8 = pytest.mark.needs_occt8
NO_COND = "A surface loft takes no end conditions"


def _result_has_i73():
    path = os.path.join(os.path.dirname(__file__), "..", "scripts", "spikes", "op2_loft.result.txt")
    return "I7.3 not built" not in open(path).read()


# --------------------------------------------------------------------------- builders

def offset_plane(doc, z):
    pl = add(doc, "plane", type="offset", refs=[], ref=TOP, offset=z)
    return {"kind": "plane", "fid": pl["id"]}


def arc_sketch(doc, z, pts=((-10, 0), (0, 5), (10, 0)), plane=None):
    """A sketch with one open arc through `pts` on the plane z above Top (or `plane`); returns (sketch id, arc id)."""
    plane = plane or (TOP if not z else offset_plane(doc, z))
    ids = []
    sid = sketch_feature(doc, lambda s: ids.append(_arc3(s, *pts)), plane)
    return sid, ids[0]


def line_sketch(doc, z, x0=-10.0, x1=10.0, y=0.0):
    ids = []
    sid = sketch_feature(doc, lambda s: ids.append(s.add_line(s.add_point(x0, y), s.add_point(x1, y))),
                         TOP if not z else offset_plane(doc, z))
    return sid, ids[0]


def circle_sketch(doc, z, r):
    ids = []
    sid = sketch_feature(doc, lambda s: ids.append(s.add_circle(s.add_point(0, 0), r)),
                         TOP if not z else offset_plane(doc, z))
    return sid, ids[0]


def chain(sid, eid):
    return {"kind": "sketch_chain", "sketch": sid, "entity": eid}


def loft_surface(doc, curves, **p):
    return try_add(doc, "loft", op="new", creation="surface", curves=curves, **p)


def only(doc):
    (p,) = doc.state.parts.values()
    return p


def free_edges(shape):
    """The edges that bound one face only (a seam edge counts twice in its face and is not free)."""
    count = {}
    for fc in K.faces(shape):
        ex = TopExp_Explorer(fc, TopAbs_EDGE)
        while ex.More():
            e = ex.Current()
            key = next((i for i, c in enumerate(K.edges(shape)) if c.IsSame(e)), None)
            count[key] = count.get(key, 0) + 1
            ex.Next()
    all_e = K.edges(shape)
    return [all_e[k] for k, n in count.items() if n == 1]


def boundary_loops(shape):
    """The number of closed loops the free edges make."""
    es = free_edges(shape)
    return len(K.chain_wires(es)) if es else 0


def face_names(doc, pid):
    n = doc.state.named(pid)
    assert n is not None
    return sorted(n.names_of("F"))


def two_arcs_doc(**p):
    doc = PartStudioDoc()
    a, ea = arc_sketch(doc, 0)
    b, eb = arc_sketch(doc, 20, ((-10, 0), (0, 8), (10, 0)))
    f, err = loft_surface(doc, [chain(a, ea), chain(b, eb)], **p)
    return doc, (a, b), f, err


# --------------------------------------------------------------------------- the legacy route

def test_two_open_arcs_make_one_face_with_a_four_edge_boundary_and_no_solid():
    doc, _, _, err = two_arcs_doc()
    assert not err, err
    p = only(doc)
    assert p.name == "Surface 1" and K.is_surface(p.shape) and not K.solids(p.shape)
    assert len(K.faces(p.shape)) == 1
    assert len(K.edges(p.shape)) == 4 and boundary_loops(p.shape) == 1 and len(free_edges(p.shape)) == 4


def test_three_ruled_open_sections_make_two_faces():
    doc = PartStudioDoc()
    secs = [arc_sketch(doc, z, ((-10, 0), (0, 5 + z / 10), (10, 0))) for z in (0, 15, 30)]
    f, err = loft_surface(doc, [chain(*s) for s in secs], ruled=True)
    assert not err, err
    p = only(doc)
    assert K.is_surface(p.shape) and len(K.faces(p.shape)) == 2
    assert len(free_edges(p.shape)) == 6 and boundary_loops(p.shape) == 1


def test_two_closed_circles_make_an_open_ended_tube():
    doc = PartStudioDoc()
    a, b = circle_sketch(doc, 0, 10), circle_sketch(doc, 20, 6)
    f, err = loft_surface(doc, [chain(*a), chain(*b)])
    assert not err, err
    p = only(doc)
    assert K.is_surface(p.shape) and not K.solids(p.shape)
    assert boundary_loops(p.shape) == 2 and len(free_edges(p.shape)) == 2
    assert all(K.face_info(fc)["type"] != int(K.GeomAbs_Plane) for fc in K.faces(p.shape))   # no flat cap
    assert K.mass_props(p.shape)["area"] > 2 * math.pi * 8 * 20 * 0.9


def test_a_surface_loft_follows_the_pick_order_and_either_curve_direction():
    doc = PartStudioDoc()
    a = arc_sketch(doc, 0)
    b = arc_sketch(doc, 20, ((10, 0), (0, 8), (-10, 0)))          # drawn the other way round
    c = line_sketch(doc, 40)
    f, err = loft_surface(doc, [chain(*a), chain(*b), chain(*c)])
    assert not err, err
    p = only(doc)
    assert len(K.faces(p.shape)) == 1 or len(K.faces(p.shape)) == 2
    lo, hi = K.bbox(p.shape)
    assert hi[2] == pytest.approx(40, abs=1e-5) and lo[2] == pytest.approx(0, abs=1e-5)
    assert boundary_loops(p.shape) == 1


def test_a_closed_surface_loft_of_four_arcs_round_a_ring_is_a_band_with_two_boundary_loops():
    doc = PartStudioDoc()
    secs = []
    for plane, x in ((FRONT, 17), (RIGHT, 17), (FRONT, -23), (RIGHT, -23)):
        ids = []
        sid = sketch_feature(doc, lambda s, x=x: ids.append(_arc3(s, (x - 3, -3), (x, 3), (x + 3, -3))), plane)
        secs.append((sid, ids[0]))
    ring = [chain(*s) for s in secs]
    _, err = loft_surface(doc, ring[:2], closed=True)
    assert err and "three" in err
    f, err = loft_surface(doc, ring, closed=True)
    assert not err, err
    p = only(doc)
    assert K.is_surface(p.shape) and K.is_valid(p.shape) and boundary_loops(p.shape) == 2


def test_a_point_profile_is_not_pickable_as_a_curve():
    doc = PartStudioDoc()
    a = arc_sketch(doc, 0)
    f, err = loft_surface(doc, [chain(*a)])
    assert err and "at least two profiles" in err


# --------------------------------------------------------------------------- guides

def guide_sketch(doc, x0=10.0, mid=12.0, n=2):
    """Guides on the Front plane (sketch x, y = world x, z), each a three-point arc from z = 0 to z = 20."""
    ids = []

    def build(s):
        ids.append(_arc3(s, (x0, 0), (mid, 10), (x0, 20)))
        if n > 1:
            ids.append(_arc3(s, (-x0, 0), (-mid, 10), (-x0, 20)))
    sid = sketch_feature(doc, build, FRONT)
    return sid, ids


def guided_doc(n=2, **p):
    doc = PartStudioDoc()
    a, ea = line_sketch(doc, 0)
    b, eb = line_sketch(doc, 20)
    g, gids = guide_sketch(doc, n=n)
    guides = [[chain(g, e)] for e in gids]
    f, err = loft_surface(doc, [chain(a, ea), chain(b, eb)], guides=guides, **p)
    return doc, guides, f, err


@needs_occt8
@pytest.mark.skipif(not _result_has_i73(), reason="I7.3 not built")
def test_two_profiles_and_two_guides_give_one_surface_through_all_four_curves():
    doc, guides, f, err = guided_doc()
    assert not err, err
    p = only(doc)
    assert K.is_surface(p.shape) and len(K.faces(p.shape)) == 1 and not K.solids(p.shape)
    assert not [fc for fc in K.faces(p.shape) if K.face_info(fc)["type"] == int(K.GeomAbs_Plane)]
    state = doc.state
    wires = [F._path_wire(state, g) for g in guides]
    wires += [F._curve_wires(state, [chain(s, e)])[0] for s, e in ((f["params"]["curves"][0]["sketch"],
                                                                  f["params"]["curves"][0]["entity"]),
                                                                 (f["params"]["curves"][1]["sketch"],
                                                                  f["params"]["curves"][1]["entity"]))]
    from OCP.BRepAdaptor import BRepAdaptor_CompCurve
    for w in wires:
        ad = BRepAdaptor_CompCurve(w)
        a, b = ad.FirstParameter(), ad.LastParameter()
        for k in range(50):
            v = BRepBuilderAPI_MakeVertex(ad.Value(a + (b - a) * k / 49)).Vertex()
            d = BRepExtrema_DistShapeShape(v, p.shape)
            assert d.IsDone() and d.Value() < 1e-4
    lo, hi = K.bbox(p.shape)
    assert hi[2] == pytest.approx(20, abs=1e-6) and hi[0] > 11.9           # the bowed guides are in the surface


@needs_occt8
@pytest.mark.skipif(not _result_has_i73(), reason="I7.3 not built")
def test_a_guided_surface_loft_with_a_two_segment_first_profile_is_named():
    doc = PartStudioDoc()
    ids = []
    def two_lines(s):
        mid = s.add_point(0, 2)
        ids.extend([s.add_line(s.add_point(-10, 0), mid), s.add_line(mid, s.add_point(10, 0))])
    a = sketch_feature(doc, two_lines, TOP)
    b, eb = line_sketch(doc, 20)
    g, gids = guide_sketch(doc)
    f, err = loft_surface(doc, [chain(a, ids[0]), chain(b, eb)], guides=[[chain(g, e)] for e in gids])
    assert not err, err
    p = only(doc)
    assert K.is_surface(p.shape) and len(K.faces(p.shape)) == 1
    assert len(face_names(doc, p.id)) == 1 and not doc.state.named(p.id).unnamed()


def test_the_guided_surface_loft_is_refused_on_79():
    if OCCT_MAJOR >= 8:
        pytest.skip("this is the OpenCascade 7.9 refusal")
    _, _, _, err = guided_doc()
    assert err and REFUSED_79 in err


def test_one_guide_alone_asks_for_a_second():
    _, _, _, err = guided_doc(n=1)
    assert err and "Add a second guide" in err


def test_guides_with_ruled_or_closed_are_refused():
    _, _, _, err = guided_doc(ruled=True)
    assert err and "ruled" in err
    _, _, _, err = guided_doc(closed=True)
    assert err and "cannot be combined" in err


# --------------------------------------------------------------------------- refusals

@pytest.mark.parametrize("end", ["start", "end"])
def test_a_surface_loft_with_an_end_condition_raises_the_named_error(end):
    _, _, _, err = two_arcs_doc(**{end: {"cond": "normal", "mag": 1.0}})
    assert err == NO_COND


def test_remove_and_intersect_are_refused():
    for op in ("remove", "intersect"):
        doc = PartStudioDoc()
        a, ea = arc_sketch(doc, 0)
        b, eb = arc_sketch(doc, 20)
        _, err = try_add(doc, "loft", op=op, creation="surface", curves=[chain(a, ea), chain(b, eb)])
        assert err and "not available for surfaces" in err


def test_no_curves_is_an_error():
    doc = PartStudioDoc()
    _, err = try_add(doc, "loft", op="new", creation="surface", curves=[])
    assert err and "Select sketch curves" in err


def test_two_surface_lofts_sharing_an_edge_add_into_one_part():
    doc = PartStudioDoc()
    a, ea = arc_sketch(doc, 0)
    b, eb = arc_sketch(doc, 20)
    c, ec = arc_sketch(doc, 40, ((-10, 0), (0, 3), (10, 0)))
    _, e1 = loft_surface(doc, [chain(a, ea), chain(b, eb)])
    f2 = doc.make_feature("loft")
    f2["params"].update(op="add", creation="surface", curves=[chain(b, eb), chain(c, ec)])
    doc.add_feature(f2)
    assert not e1 and not doc.errors.get(f2["id"]), doc.errors
    p = only(doc)
    assert K.is_surface(p.shape) and len(K.faces(p.shape)) == 2 and boundary_loops(p.shape) == 1


# --------------------------------------------------------------------------- names and the old path

def test_face_names_survive_a_profile_sketch_change():
    doc = PartStudioDoc()
    secs = [arc_sketch(doc, z) for z in (0, 15, 30)]
    f, err = loft_surface(doc, [chain(*s) for s in secs], ruled=True)
    assert not err, err
    pid = next(iter(doc.state.parts))
    before = face_names(doc, pid)
    assert len(before) == 2 and len(set(before)) == 2
    sid = secs[0][0]
    sk = dict(doc.feature(sid))
    s = Sketch.from_dict(sk["params"]["sketch"])
    for pt in s.points.values():
        pt[0] *= 1.5
    s.solve()
    sk["params"] = dict(sk["params"], sketch=s.to_dict())
    doc.replace_feature(sk)
    assert not doc.errors, doc.errors
    p = only(doc)
    assert face_names(doc, p.id) == before
    assert K.bbox(p.shape)[1][0] == pytest.approx(15, abs=1e-6)


def test_face_names_survive_on_the_unruled_route_with_one_arc():
    doc, (a, b), f, err = two_arcs_doc()
    assert not err, err
    before = face_names(doc, only(doc).id)
    assert len(before) == 1
    sk = dict(doc.feature(a))
    s = Sketch.from_dict(sk["params"]["sketch"])
    for pt in s.points.values():
        pt[1] += 1.0
    s.solve()
    sk["params"] = dict(sk["params"], sketch=s.to_dict())
    doc.replace_feature(sk)
    assert not doc.errors, doc.errors
    assert face_names(doc, only(doc).id) == before


def _solid_loft_doc(**extra):
    doc = PartStudioDoc()
    a, _ = add_sketch(doc, -10, -10, 20, 20)
    from test_naming_sweep_loft_import import add_sketch_on
    b, _ = add_sketch_on(doc, offset_plane(doc, 30), -5, -5, 10, 10)
    f = add(doc, "loft", op="new", profiles=[{"kind": "region", "ref": region_refs(doc, a)[0]},
                                             {"kind": "region", "ref": region_refs(doc, b)[0]}], **extra)
    return doc, f


def test_a_loft_with_no_creation_key_builds_as_before():
    doc, f = _solid_loft_doc()
    assert "creation" not in f["params"] and "curves" not in f["params"]
    p = only(doc)
    want = K.loft([doc.state.sketches[s].regions[0] for s in doc.state.sketches])
    assert p.name == "Part 1" and len(K.faces(p.shape)) == len(K.faces(want)) == 6
    assert K.volume(p.shape) == pytest.approx(K.volume(want), rel=1e-12)
    doc2, _ = _solid_loft_doc(creation="solid")
    assert K.volume(only(doc2).shape) == pytest.approx(K.volume(p.shape), rel=1e-12)
    assert face_names(doc2, only(doc2).id) == face_names(doc, p.id)


def test_thru_sections_default_keeps_the_solid_call():
    doc, _ = _solid_loft_doc()
    faces = [doc.state.sketches[s].regions[0] for s in doc.state.sketches]
    from OCP.BRepTools import BRepTools
    w = [BRepTools.OuterWire_s(fc) for fc in faces]
    assert K.solids(K._thru_sections(w, False).Shape())
    assert not K.solids(K._thru_sections(w, False, solid=False).Shape())


# --------------------------------------------------------------------------- dialog and search

@pytest.fixture
def make_win(tmp_path, monkeypatch):
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    made = []

    def make(doc):
        w = MainWindow()
        made.append(w)
        w._add_tab(doc)
        w.resize(900, 700)
        w.show()
        app.processEvents()
        assert w.doc is doc
        return w

    yield make
    for w in made:
        w.deleteLater()


def _segments(dlg, key):
    from PySide6.QtWidgets import QToolButton
    for spec, w in dlg.rows:
        if spec["t"] == "ops" and spec["key"] == key and w.isVisibleTo(dlg):
            return {b.text(): b for b in w.findChildren(QToolButton)}
    return {}


def _row(dlg, key):
    return next(w for spec, w in dlg.rows if spec.get("key") == key)


def test_the_loft_command_is_found_by_the_surface_keywords_and_the_old_ones(make_win):
    from freeshape.ui.palette import search_commands
    w = make_win(PartStudioDoc())
    cmds = w._palette_commands()
    for q in ("boundary surface", "ruled surface", "surface loft", "guided loft", "end condition",
              "tangent", "guide curves"):
        assert "Loft" in [c.name for c in search_commands(cmds, q)], q


def test_the_dialog_shows_create_and_curve_picking_and_greys_the_conditions(make_win):
    from freeshape.ui.viewport import Pick
    doc = PartStudioDoc()
    a, ea = arc_sketch(doc, 0)
    b, eb = arc_sketch(doc, 20)
    w = make_win(doc)
    w.start_feature("loft")
    dlg = w.dialog
    assert list(_segments(dlg, "creation")) == ["Solid", "Surface"]
    assert list(_segments(dlg, "op")) == ["New", "Add", "Remove", "Intersect"]
    assert dlg.fields["profiles"].isVisibleTo(dlg) and not dlg.fields["curves"].isVisibleTo(dlg)
    assert _row(dlg, "start_cond").isEnabled()
    dlg._set("start_cond", "normal")
    dlg._set("op", "remove")
    _segments(dlg, "creation")["Surface"].click()
    p = dlg.feature["params"]
    assert p["creation"] == "surface" and p["op"] == "add"
    assert list(_segments(dlg, "op")) == ["New", "Add"]
    assert not dlg.fields["profiles"].isVisibleTo(dlg) and dlg.fields["curves"].isVisibleTo(dlg)
    assert dlg.active_field is dlg.fields["curves"]
    for k in ("start_cond", "end_cond"):
        row = _row(dlg, k)
        assert not row.isEnabled() and "surface loft takes no end conditions" in row.toolTip()
    assert _row(dlg, "guides_on").isEnabled()
    dlg.handle_pick(Pick("sketch_line", (a, ea)))
    dlg.handle_pick(Pick("sketch_line", (b, eb)))
    assert [r["sketch"] for _k, r in dlg.fields["curves"].items] == [a, b]
    dlg._set("op", "new")
    w.commit_dialog()
    f = doc.features[-1]
    assert not doc.errors.get(f["id"]), doc.errors
    assert f["params"]["creation"] == "surface" and len(f["params"]["curves"]) == 2
    assert "start" not in f["params"] and "end" not in f["params"]
    assert only(doc).name == "Surface 1" and K.is_surface(only(doc).shape)
    _segments(dlg, "creation")        # the dialog is closed: nothing more to check there


def test_a_solid_loft_from_the_dialog_saves_neither_new_key(make_win):
    doc = PartStudioDoc()
    a, _ = add_sketch(doc, -10, -10, 20, 20)
    w = make_win(doc)
    w.start_feature("loft")
    dlg = w.dialog
    dlg.fields["profiles"].toggle(("region", region_refs(doc, a)[0]))
    dlg._set("op", "new")
    dlg._set("creation", "surface")
    dlg._set("creation", "solid")
    from freeshape.ui import dialogs as D
    p = dict(dlg.feature["params"])
    D._loft_fold(p, strip=True)
    assert "curves" not in p


# --------------------------------------------------------------------------- the engine wire

@pytest.fixture(scope="module")
def engine_win():
    os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")
    os.environ.setdefault("FREESHAPE_NO_HOME", "1")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow(engine=True)
    w.settings = type("S", (dict,), {"value": lambda s, k, d=None: s.get(k, d),
                                     "setValue": lambda s, k, v: s.__setitem__(k, v)})()
    assert w.studio_engine.settle(180)
    yield w
    w.studio_engine.shutdown()
    from freeshape.engine import process as P
    P.SPARES.clear()


def test_a_surface_loft_made_in_the_engine_window_is_a_surface_part(engine_win):
    from freeshape.ui.viewport import Pick
    w = engine_win
    se, doc = w.studio_engine, w.doc
    a, ea = arc_sketch(doc, 0)
    b, eb = arc_sketch(doc, 20)
    assert se.settle(180)
    w.start_feature("loft")
    dlg = w.dialog
    dlg._set("creation", "surface")
    dlg._set("op", "new")
    dlg.handle_pick(Pick("sketch_line", (a, ea)))
    dlg.handle_pick(Pick("sketch_line", (b, eb)))
    w.commit_dialog()
    assert se.settle(180)
    assert not doc.errors, doc.errors
    parts = list(doc.state.parts.values())
    assert [p.name for p in parts] == ["Surface 1"]
    p = parts[0]
    assert K.is_surface(p.shape) and K.volume(p.shape) == 0.0 and len(K.faces(p.shape)) == 1
