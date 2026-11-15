"""I7.9: Boundary surface, a named front door over the surface loft. It is a loft feature with creation
"surface" and the marker front "boundary_surface": the same runner, the same element names. U curves are the
loft's `curves`, V curves its `guides`. Only the label, icon, dialog and the wording of its errors differ."""
import copy
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
from OCP.BRepAdaptor import BRepAdaptor_CompCurve  # noqa: E402
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex  # noqa: E402
from OCP.BRepExtrema import BRepExtrema_DistShapeShape  # noqa: E402

from conftest import OCCT_MAJOR, add, try_add  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.document import PartStudioDoc  # noqa: E402
from test_loft_surface import (_result_has_i73, arc_sketch, chain, engine_win, face_names,  # noqa: E402,F401
                               guide_sketch, line_sketch, make_win, only)

needs_occt8 = pytest.mark.needs_occt8
FRONT = "boundary_surface"
MSG_79 = "Boundary surface with V curves needs OpenCascade 8.0 or newer"


def boundary(doc, u, v=None, **p):
    """A Boundary surface: a surface loft with the marker. `u` and `v` are lists of curve references."""
    extra = {"guides": [[c] for c in v]} if v is not None else {}
    return try_add(doc, "loft", op="new", creation="surface", front=FRONT, curves=u, **extra, **p)


def arcs(front):
    doc = PartStudioDoc()
    a, ea = arc_sketch(doc, 0)
    b, eb = arc_sketch(doc, 20, ((-10, 0), (0, 8), (10, 0)))
    u = [chain(a, ea), chain(b, eb)]
    f, err = try_add(doc, "loft", op="new", creation="surface", curves=u, **({"front": FRONT} if front else {}))
    assert not err, err
    return doc, f


def network(n_u=2, v_x0=10.0, u_half=10.0, n_v=2):
    """U lines at z = 0, 20 (and 10 with n_u == 3) and V arcs on the Front plane bowed out to 12 at z = 10."""
    doc = PartStudioDoc()
    u = []
    for z in (0, 20, 10)[:n_u]:
        half = u_half if z != 10 else 12.0
        s, e = line_sketch(doc, z, -half, half)
        u.append(chain(s, e))
    g, gids = guide_sketch(doc, x0=v_x0, n=n_v)
    return doc, u, [chain(g, e) for e in gids]


def dist_to_surface(shape, wire, n=50):
    ad = BRepAdaptor_CompCurve(wire)
    a, b = ad.FirstParameter(), ad.LastParameter()
    worst = 0.0
    for k in range(n):
        v = BRepBuilderAPI_MakeVertex(ad.Value(a + (b - a) * k / (n - 1))).Vertex()
        d = BRepExtrema_DistShapeShape(v, shape)
        assert d.IsDone()
        worst = max(worst, d.Value())
    return worst


# --------------------------------------------------------------------------- the same result as a surface loft

def test_u_curves_only_equal_the_surface_loft_of_the_same_curves():
    d1, f1 = arcs(False)
    d2, f2 = arcs(True)
    p1, p2 = only(d1), only(d2)
    assert len(K.faces(p1.shape)) == len(K.faces(p2.shape)) == 1
    a1, a2 = K.mass_props(p1.shape)["area"], K.mass_props(p2.shape)["area"]
    assert a1 == pytest.approx(a2, rel=1e-9)
    assert face_names(d1, p1.id) == face_names(d2, p2.id) and face_names(d2, p2.id)
    n1, n2 = d1.state.named(p1.id), d2.state.named(p2.id)
    assert sorted(n1.names_of("E")) == sorted(n2.names_of("E"))


def test_the_saved_feature_is_a_loft_with_the_marker_and_works_without_it():
    doc, f = arcs(True)
    assert f["type"] == "loft" and f["params"]["creation"] == "surface" and f["params"]["front"] == FRONT
    assert F.front_kind(f) == FRONT and F.door_kind(f) == FRONT
    area = K.mass_props(only(doc).shape)["area"]
    names = face_names(doc, only(doc).id)
    f2 = copy.deepcopy(f)
    del f2["params"]["front"]
    assert F.front_kind(f2) is None and F.door_kind(f2) == "loft"
    doc2, _ = arcs(True)
    doc2.features[-1]["params"].pop("front")
    doc2.replace_feature(doc2.features[-1])
    assert not doc2.errors
    assert K.mass_props(only(doc2).shape)["area"] == pytest.approx(area, rel=1e-12)
    assert face_names(doc2, only(doc2).id) == names


def test_front_marker_on_another_type_or_unknown_value_is_ignored():
    assert F.front_kind({"type": "extrude", "params": {"front": FRONT}}) is None
    assert F.front_kind({"type": "loft", "params": {"front": "something_else"}}) is None
    assert F.front_kind({"type": "loft", "params": {}}) is None


# --------------------------------------------------------------------------- networks (OpenCascade 8.0)

@needs_occt8
@pytest.mark.skipif(not _result_has_i73(), reason="I7.3 not built")
@pytest.mark.parametrize("n_u", [2, 3])
def test_a_network_gives_one_surface_through_every_input_curve(n_u):
    doc, u, v = network(n_u)
    f, err = boundary(doc, u, v)
    assert not err, err
    p = only(doc)
    assert K.is_surface(p.shape) and len(K.faces(p.shape)) == 1 and not K.solids(p.shape)
    for ref in u + v:
        (w,) = F._curve_wires(doc.state, [ref])
        assert dist_to_surface(p.shape, w) < 1e-4


# --------------------------------------------------------------------------- 7.9 and errors

def test_on_79_a_network_fails_with_the_boundary_wording_and_u_only_builds():
    if OCCT_MAJOR >= 8:
        pytest.skip("this is the OpenCascade 7.9 refusal")
    doc, u, v = network()
    f, err = boundary(doc, u, v)
    assert err == MSG_79
    doc2, _ = arcs(True)
    assert not doc2.errors and K.is_surface(only(doc2).shape)


def test_a_plain_loft_keeps_its_own_wording_for_the_same_failures():
    doc, u, v = network(n_v=1)
    f, err = try_add(doc, "loft", op="new", creation="surface", curves=u, guides=[[v[0]]])
    assert err and "Add a second guide" in err and "V curve" not in err


def test_one_v_curve_asks_for_a_second():
    doc, u, v = network(n_v=1)
    f, err = boundary(doc, u, v)
    assert err and "Add a second V curve" in err


@needs_occt8
def test_a_v_curve_that_misses_a_u_curve_by_half_a_millimetre_names_both():
    doc, u, v = network(u_half=9.5)
    f, err = boundary(doc, u, v)
    assert err and "V curve" in err and "U curve" in err and "does not touch" in err, err


# --------------------------------------------------------------------------- dialog, palette, list

def _pick(dlg, sk, ent):
    from freeshape.ui.viewport import Pick
    dlg.handle_pick(Pick("sketch_line", (sk, ent)))


def test_the_command_is_in_the_palette_and_found_by_its_keywords(make_win):
    from freeshape.ui.palette import search_commands
    w = make_win(PartStudioDoc())
    cmds = w._palette_commands()
    assert "Boundary surface" in [c.name for c in cmds]
    for q in ("gordon", "network surface", "uv surface", "curve network", "boundary surface"):
        assert "Boundary surface" in [c.name for c in search_commands(cmds, q)], q
    assert search_commands(cmds, "boundary surface")[0].name == "Boundary surface"
    assert "Loft" in [c.name for c in search_commands(cmds, "boundary surface")]


def test_the_dialog_keeps_pick_order_the_list_names_it_and_reopens_for_edit(make_win):
    doc = PartStudioDoc()
    a, ea = arc_sketch(doc, 0)
    b, eb = arc_sketch(doc, 20, ((-10, 0), (0, 8), (10, 0)))
    c, ec = arc_sketch(doc, 40, ((-10, 0), (0, 3), (10, 0)))
    w = make_win(doc)
    w.start_feature("boundary_surface")
    dlg = w.dialog
    assert dlg.ftype == "loft" and "curves" in dlg.fields and "guides" in dlg.fields
    assert "profiles" not in dlg.fields and "creation" not in dlg.widgets
    from freeshape.ui.dialogs import GuideListField
    assert isinstance(dlg.fields["curves"], GuideListField) and isinstance(dlg.fields["guides"], GuideListField)
    for sk, ent in ((c, ec), (a, ea), (b, eb)):
        _pick(dlg, sk, ent)
    assert [r["sketch"] for _k, r in dlg.fields["curves"].items] == [c, a, b]
    dlg._set("op", "new")
    w.commit_dialog()
    f = doc.features[-1]
    assert not doc.errors.get(f["id"]), doc.errors
    assert f["name"] == "Boundary surface 1" and f["type"] == "loft"
    assert f["params"]["front"] == FRONT and f["params"]["creation"] == "surface"
    assert [r["sketch"] for r in f["params"]["curves"]] == [c, a, b] and "guides" not in f["params"]
    row = next(i for i in range(w.left.features.count()) if w.left.features.item(i).text() == "Boundary surface 1")
    assert row >= 0
    w.edit_feature(f["id"])
    d2 = w.dialog
    assert [r["sketch"] for _k, r in d2.fields["curves"].items] == [c, a, b]
    assert d2.fields["guides"].items == []
    w.cancel_dialog()
    # removing the marker opens the feature as a plain Loft
    g = doc.feature(f["id"])
    g["params"].pop("front")
    w.edit_feature(f["id"])
    d3 = w.dialog
    assert "creation" in [s["key"] for s, _w in d3.rows if s.get("key")] and "profiles" in d3.fields
    assert [r["sketch"] for _k, r in d3.fields["curves"].items] == [c, a, b]
    w.cancel_dialog()


def test_ruled_is_greyed_out_with_v_curves_and_the_tooltip_states_the_limits(make_win):
    doc = PartStudioDoc()
    w = make_win(doc)
    w.start_feature("boundary_surface")
    dlg = w.dialog
    ruled = next(wd for s, wd in dlg.rows if s.get("key") == "ruled")
    assert ruled.isEnabled()
    tip = dlg.fields["curves"].toolTip() + dlg.fields["guides"].toolTip() + "".join(
        wd.toolTip() for s, wd in dlg.rows if s.get("key") in ("curves", "guides"))
    assert "Fill" in tip and "OpenCascade 8.0" in tip
    dlg.fields["guides"].items = [("path", chain("S", "e"))]
    dlg.fields["guides"].changed.emit()
    assert not ruled.isEnabled() and "remove the V curves" in ruled.toolTip()
    w.cancel_dialog()


@needs_occt8
@pytest.mark.skipif(not _result_has_i73(), reason="I7.3 not built")
def test_a_two_by_two_network_from_the_dialog_saves_u_and_v_in_order(make_win):
    doc, u, v = network()
    w = make_win(doc)
    w.start_feature("boundary_surface")
    dlg = w.dialog
    for ref in (u[1], u[0]):
        _pick(dlg, ref["sketch"], ref["entity"])
    dlg.activate(dlg.fields["guides"])
    for ref in (v[1], v[0]):
        _pick(dlg, ref["sketch"], ref["entity"])
    dlg._set("op", "new")
    w.commit_dialog()
    f = doc.features[-1]
    assert not doc.errors.get(f["id"]), doc.errors
    assert [r["entity"] for r in f["params"]["curves"]] == [u[1]["entity"], u[0]["entity"]]
    assert [g[0]["entity"] for g in f["params"]["guides"]] == [v[1]["entity"], v[0]["entity"]]
    w.edit_feature(f["id"])
    d2 = w.dialog
    assert [r["entity"] for _k, r in d2.fields["curves"].items] == [u[1]["entity"], u[0]["entity"]]
    assert [r["entity"] for _k, r in d2.fields["guides"].items] == [v[1]["entity"], v[0]["entity"]]
    w.cancel_dialog()


# --------------------------------------------------------------------------- the engine window

def test_a_boundary_surface_made_in_the_engine_window_is_a_surface_part(engine_win):
    w = engine_win
    se, doc = w.studio_engine, w.doc
    if OCCT_MAJOR >= 8 and _result_has_i73():
        u = []
        for z in (0, 20):
            s, e = line_sketch(doc, z)
            u.append(chain(s, e))
        g, gids = guide_sketch(doc)
        v = [chain(g, e) for e in gids]
    else:
        u, v = [], []
        for z, mid in ((0, 5), (20, 8)):
            s, e = arc_sketch(doc, z, ((-10, 0), (0, mid), (10, 0)))
            u.append(chain(s, e))
    assert se.settle(180)
    w.start_feature("boundary_surface")
    dlg = w.dialog
    dlg._set("op", "new")
    for ref in u:
        _pick(dlg, ref["sketch"], ref["entity"])
    if v:
        dlg.activate(dlg.fields["guides"])
        for ref in v:
            _pick(dlg, ref["sketch"], ref["entity"])
    w.commit_dialog()
    assert se.settle(180)
    assert not doc.errors, doc.errors
    parts = list(doc.state.parts.values())
    assert [p.name for p in parts] == ["Surface 1"]
    p = parts[0]
    assert K.is_surface(p.shape) and K.volume(p.shape) == 0.0 and len(K.faces(p.shape)) == 1
    f = doc.features[-1]
    assert f["name"] == "Boundary surface 1" and F.front_kind(f) == FRONT
    items = [w.left.features.item(i).text() for i in range(w.left.features.count())]
    assert "Boundary surface 1" in items
    w.edit_feature(f["id"])
    d2 = w.dialog
    assert [r["entity"] for _k, r in d2.fields["curves"].items] == [r["entity"] for r in u]
    if v:
        assert [r["entity"] for _k, r in d2.fields["guides"].items] == [r["entity"] for r in v]
    w.cancel_dialog()
    assert se.settle(180)
