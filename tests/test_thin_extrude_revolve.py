"""I2.8: Extrude and Revolve with Create = Thin. A thin feature turns sketch curves (or the outer loop of a region)
into a thin face in the sketch plane, then runs the solid path on it, so every end type, operation and up-to
end works. Thickness 1 is on the right of the curve's travel (outside a closed curve), thickness 2 on the other
side."""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from conftest import TOP, add, rect_lines, region_refs, sketch_feature, try_add  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.document import PartStudioDoc  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402

AXIS_X = {"kind": "std", "name": "X"}
TOO_THICK = "The thickness is larger than this shape allows"


def l_shape(s, a=20.0, b=20.0):
    """An open L of two lines: (0,0) -> (a,0) -> (a,b). Its travel is along the drawing."""
    p = [s.add_point(0, 0), s.add_point(a, 0), s.add_point(a, b)]
    return [s.add_line(p[0], p[1]), s.add_line(p[1], p[2])]


def one_line(s, x0=0.0, y0=0.0, x1=20.0, y1=0.0):
    return s.add_line(s.add_point(x0, y0), s.add_point(x1, y1))


def circle(s, r=10.0):
    return s.add_circle(s.add_point(0, 0), r)


def quarter_arc(s, r=3.0):
    """An arc about the origin from (r, 0) to (0, r), counter-clockwise: its centre is left of the travel."""
    return s.add_arc(s.add_point(0, 0), s.add_point(r, 0), s.add_point(0, r))


def ents(doc, sid, kind):
    return [i for i, e in doc.state.sketches[sid].sketch.entities.items() if e["type"] == kind]


def curves(doc, sid, kind="line"):
    return [{"kind": "sketch_chain", "sketch": sid, "entity": ents(doc, sid, kind)[0]}]


def thin(build, ftype="extrude", kind="line", **kw):
    """(doc, sketch id, feature, error) of a thin feature on the first `kind` curve of a Top-plane sketch."""
    doc = PartStudioDoc()
    sid = sketch_feature(doc, build, TOP)
    kw.setdefault("op", "new")
    if ftype == "extrude":
        kw.setdefault("depth", 10)
    else:
        kw.setdefault("axis", AXIS_X)
    f, err = try_add(doc, ftype, creation="thin", curves=curves(doc, sid, kind), **kw)
    return doc, sid, f, err


def only(doc):
    (p,) = doc.state.parts.values()
    return p


def polygon_area(pts):
    x, y = np.array(pts, float).T
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y)))


def face_names(doc, pid):
    n = doc.state.named(pid)
    assert n is not None
    return sorted(n.names_of("F"))


# ---------------------------------------------------------------------------- thin extrude

def test_an_open_l_gives_one_solid_of_eight_faces_with_the_volume_of_its_offset_polygon():
    doc, _, f, err = thin(l_shape, thickness1=2, thickness2=0)
    assert not err, err
    p = only(doc)
    assert len(K.solids(p.shape)) == 1 and len(K.faces(p.shape)) == 8
    # thickness 1 is on the right of the travel (0,0) -> (20,0) -> (20,20): the outer side of the corner
    outer = [(0, 0), (20, 0), (20, 20), (22, 20), (22, -2), (0, -2)]
    inner = [(0, 0), (20, 0), (20, 20), (18, 20), (18, 2), (0, 2)]
    assert K.volume(p.shape) == pytest.approx(polygon_area(outer) * 10) == pytest.approx(840)
    assert polygon_area(inner) * 10 == pytest.approx(760)
    lo, hi = K.bbox(p.shape)
    assert lo[1] == pytest.approx(-2) and hi[0] == pytest.approx(22)


def test_thickness_2_alone_is_on_the_left_of_the_travel():
    doc, _, f, err = thin(l_shape, thickness1=0, thickness2=2)
    assert not err, err
    assert K.volume(only(doc).shape) == pytest.approx(760)
    lo, hi = K.bbox(only(doc).shape)
    assert hi[0] == pytest.approx(20) and lo[1] == pytest.approx(0, abs=1e-5)


def test_mid_plane_puts_half_the_thickness_each_side_so_every_vertex_is_1_from_both_walls():
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.gp import gp_Pnt
    doc, _, f, err = thin(l_shape, thickness1=2, mid_plane=True)
    assert not err, err
    shape = only(doc).shape
    assert K.volume(shape) == pytest.approx(800)               # 40 long * 2 wide, the corner neither grows nor loses
    walls = [fc for fc in K.faces(shape)
             if abs(float(K.face_info(fc)["n"][2])) < 1e-6 and K.mass_props(fc)["area"] > 21]
    assert len(walls) == 4                                      # two per side, the 2 wide end caps left out
    for v in [(0, 0), (20, 0), (20, 20)]:
        vert = BRepBuilderAPI_MakeVertex(gp_Pnt(v[0], v[1], 5)).Vertex()
        dists = []
        for w in walls:
            d = BRepExtrema_DistShapeShape(vert, w)
            dists.append(d.Value())
        assert min(dists) == pytest.approx(1.0, abs=1e-6)
        assert sum(abs(d - 1.0) < 1e-6 for d in dists) >= 2     # one wall face of each side
    f["params"]["flip_wall"] = True                             # nothing to flip when it is centred
    doc.replace_feature(f)
    assert not doc.errors.get(f["id"]) and K.volume(only(doc).shape) == pytest.approx(800)


def test_a_circle_with_thickness_1_each_side_is_a_tube():
    doc, _, f, err = thin(circle, kind="circle", thickness1=1, thickness2=1, depth=5)
    assert not err, err
    p = only(doc)
    assert K.volume(p.shape) == pytest.approx(math.pi * (11 ** 2 - 9 ** 2) * 5, rel=1e-4)
    assert len(K.faces(p.shape)) == 4


def test_thickness_1_of_a_closed_curve_is_outside_whichever_way_it_was_drawn():
    def clockwise(s):
        p = [s.add_point(0, 0), s.add_point(0, 10), s.add_point(20, 10), s.add_point(20, 0)]
        return [s.add_line(a, b) for a, b in zip(p, p[1:] + p[:1])]
    for build in (lambda s: rect_lines(s, 0, 0, 20, 10), clockwise):
        doc, _, f, err = thin(build, thickness1=2, thickness2=0)
        assert not err, err
        assert K.volume(only(doc).shape) == pytest.approx((24 * 14 - 20 * 10) * 10)
        lo, hi = K.bbox(only(doc).shape)
        assert lo[0] == pytest.approx(-2) and hi[1] == pytest.approx(12)


def test_flip_wall_moves_the_bounding_box_by_exactly_the_thickness_difference():
    doc, _, f, err = thin(one_line, thickness1=3, thickness2=1)
    assert not err, err
    lo, hi = K.bbox(only(doc).shape)
    assert (lo[1], hi[1]) == (pytest.approx(-3), pytest.approx(1))
    f["params"]["flip_wall"] = True
    doc.replace_feature(f)
    assert not doc.errors.get(f["id"])
    lo2, hi2 = K.bbox(only(doc).shape)
    assert (lo2[1], hi2[1]) == (pytest.approx(-1), pytest.approx(3))
    assert lo2[1] - lo[1] == pytest.approx(3 - 1)
    assert K.volume(only(doc).shape) == pytest.approx(20 * 4 * 10)


def test_a_wall_on_one_straight_line_needs_no_offset_plane():
    for t1, t2 in ((2, 0), (0, 2), (1, 1)):
        doc, _, f, err = thin(one_line, thickness1=t1, thickness2=t2)
        assert not err, err
        assert K.volume(only(doc).shape) == pytest.approx(20 * 2 * 10)
        assert len(K.faces(only(doc).shape)) == 6


def test_thicknesses_accept_expressions():
    doc, _, f, err = thin(l_shape, thickness1="1 + 1", thickness2=0)
    assert not err, err
    assert K.volume(only(doc).shape) == pytest.approx(840)


def test_a_thickness_larger_than_the_shape_allows_is_the_named_error_and_does_not_crash():
    doc, _, f, err = thin(quarter_arc, kind="arc", thickness1=0, thickness2=5)
    assert err == TOO_THICK and not doc.state.parts
    doc, _, f, err = thin(circle, kind="circle", thickness1=0, thickness2=12, depth=5)
    assert err == TOO_THICK
    doc, _, f, err = thin(circle, kind="circle", thickness1=0, thickness2=10, depth=5)
    assert err == TOO_THICK
    doc, _, f, err = thin(quarter_arc, kind="arc", thickness1=5, thickness2=0)      # the outside of an arc is fine
    assert not err, err


def test_a_closed_loop_offset_to_nothing_is_the_named_error():
    doc, _, f, err = thin(lambda s: rect_lines(s, 0, 0, 20, 10), thickness1=0, thickness2=6)
    assert err == TOO_THICK


def test_zero_thickness_is_an_error():
    doc, _, f, err = thin(l_shape, thickness1=0, thickness2=0)
    assert err == "The thickness must be more than zero"


def test_a_region_uses_its_outer_loop_and_names_its_side_faces():
    doc = PartStudioDoc()

    def build(s):
        rect_lines(s, 0, 0, 20, 10)
        rect_lines(s, 5, 3, 4, 4)                                # a hole in the region: ignored by a thin feature
    sid = sketch_feature(doc, build, TOP)
    big = region_refs(doc, sid, lambda i: i["area"] > 100)
    f, err = try_add(doc, "extrude", creation="thin", regions=big, thickness1=1, thickness2=1, depth=5, op="new")
    assert not err, err
    p = only(doc)
    assert K.volume(p.shape) == pytest.approx((22 * 12 - 18 * 8) * 5)
    names = face_names(doc, p.id)
    assert len(names) == len(set(names)) == 10                    # 4 outside + 4 inside + 2 ends


def test_a_planar_face_picked_in_thin_mode_uses_its_outer_loop():
    doc = PartStudioDoc()
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 20, 10), TOP)
    add(doc, "extrude", regions=region_refs(doc, sid), depth=10, op="new")
    pid = next(iter(doc.state.parts))
    shape = doc.state.parts[pid].shape
    top = next(fc for fc in K.faces(shape) if K.face_info(fc)["n"][2] > 0.9)
    ref = F.make_ref(pid, top, "face", shape, doc.state.named(pid))
    f, err = try_add(doc, "extrude", creation="thin", faces=[ref], thickness1=0, thickness2=1, depth=4, op="add")
    assert not err, err
    assert K.volume(only(doc).shape) == pytest.approx(2000 + (20 * 10 - 18 * 8) * 4)


# ---------------------------------------------------------------------------- every end type and operation

def block_with_slot_sketch(doc):
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 40, 20), TOP)
    add(doc, "extrude", regions=region_refs(doc, sid), depth=10, op="new")
    return sketch_feature(doc, lambda s: one_line(s, 5, 10, 35, 10), TOP)


def test_thin_remove_through_a_block_cuts_a_slot_of_the_walls_width():
    doc = PartStudioDoc()
    sid = block_with_slot_sketch(doc)
    f, err = try_add(doc, "extrude", creation="thin", curves=curves(doc, sid), thickness1=2, thickness2=0,
                     end="through", op="remove")
    assert not err, err
    assert K.volume(only(doc).shape) == pytest.approx(40 * 20 * 10 - 30 * 2 * 10)
    assert len(K.solids(only(doc).shape)) == 1


def test_thin_remove_blind_goes_up_from_the_sketch_like_a_sketch_cut():
    doc = PartStudioDoc()
    sid = block_with_slot_sketch(doc)
    f, err = try_add(doc, "extrude", creation="thin", curves=curves(doc, sid), thickness1=1, thickness2=1,
                     depth=4, end="blind", op="remove", flip=True)
    assert not err, err
    assert K.volume(only(doc).shape) in (pytest.approx(8000), pytest.approx(8000 - 30 * 2 * 4))


def test_thin_with_an_up_to_face_end_ends_on_the_face():
    doc = PartStudioDoc()
    sid = sketch_feature(doc, lambda s: rect_lines(s, 30, 30, 10, 10), TOP)
    add(doc, "extrude", regions=region_refs(doc, sid), depth=15, op="new")
    pid = next(iter(doc.state.parts))
    shape = doc.state.parts[pid].shape
    top = next(fc for fc in K.faces(shape) if K.face_info(fc)["n"][2] > 0.9)
    ref = F.make_ref(pid, top, "face", shape, doc.state.named(pid))
    sid2 = sketch_feature(doc, l_shape, TOP)
    f, err = try_add(doc, "extrude", creation="thin", curves=curves(doc, sid2), thickness1=2, thickness2=0,
                     end="up_to_face", up_to={"kind": "face", "ref": ref}, op="new")
    assert not err, err
    wall = [p for p in doc.state.parts.values() if p.id != pid][0]
    lo, hi = K.bbox(wall.shape)
    assert hi[2] == pytest.approx(15) and K.volume(wall.shape) == pytest.approx(840 * 1.5)


def test_thin_with_up_to_next_trims_to_the_part_in_the_way():
    doc = PartStudioDoc()
    sid = sketch_feature(doc, lambda s: rect_lines(s, -5, -5, 60, 60), TOP)
    add(doc, "extrude", regions=region_refs(doc, sid), depth=12, op="new", flip=True)     # a slab below z = 0...
    sid2 = sketch_feature(doc, l_shape, TOP)
    f, err = try_add(doc, "extrude", creation="thin", curves=curves(doc, sid2), thickness1=2, thickness2=0,
                     end="up_to_next", flip=True, op="new")
    assert not err, err
    assert len(doc.state.parts) == 2


def test_thin_with_a_draft_runs_the_solid_draft_path():
    doc, _, f, err = thin(l_shape, thickness1=2, thickness2=0, draft=True, draft_angle=3.0)
    assert not err, err
    assert len(K.solids(only(doc).shape)) == 1
    assert K.volume(only(doc).shape) != pytest.approx(840)


def test_thin_symmetric_straddles_the_sketch_plane():
    doc, _, f, err = thin(l_shape, thickness1=2, thickness2=0, end="symmetric", depth=10)
    assert not err, err
    lo, hi = K.bbox(only(doc).shape)
    assert lo[2] == pytest.approx(-5) and hi[2] == pytest.approx(5)


# ---------------------------------------------------------------------------- thin revolve

def test_a_thin_revolve_of_an_open_line_parallel_to_the_axis_is_a_tube_matching_pappus():
    doc, _, f, err = thin(lambda s: one_line(s, 0, 10, 20, 10), ftype="revolve", thickness1=1, thickness2=1)
    assert not err, err
    p = only(doc)
    area, radius = 20 * 2, 10                                     # the strip's area and its centroid's distance
    assert K.volume(p.shape) == pytest.approx(area * 2 * math.pi * radius, rel=1e-4)
    assert K.volume(p.shape) == pytest.approx(math.pi * (11 ** 2 - 9 ** 2) * 20, rel=1e-4)


def test_a_thin_revolve_with_an_angle_and_a_side_wall():
    doc, _, f, err = thin(lambda s: one_line(s, 0, 10, 20, 10), ftype="revolve", thickness1=2, thickness2=0,
                          end="one", angle=90)
    assert not err, err
    # thickness 1 is on the right of +x travel, i.e. towards -y: the strip is y in [8, 10]
    assert K.volume(only(doc).shape) == pytest.approx(20 * 2 * (2 * math.pi * 9) / 4, rel=1e-4)


def test_a_thin_revolve_that_is_too_thick_is_the_named_error():
    doc, _, f, err = thin(quarter_arc, ftype="revolve", kind="arc", thickness1=0, thickness2=5)
    assert err == TOO_THICK


def test_a_thin_revolve_with_an_up_to_end_is_not_refused():
    doc, _, f, err = thin(lambda s: one_line(s, 0, 10, 20, 10), ftype="revolve", thickness1=1, thickness2=1,
                          end="up_to_vertex", up_to={"kind": "vertex", "ref": None})
    assert err != "A thin revolve is not available yet"          # it runs the solid path (and then needs a vertex)


# ---------------------------------------------------------------------------- naming and compatibility

def sketch_with(doc, sid, build):
    s = Sketch()
    build(s)
    s.solve()
    sf = next(x for x in doc.features if x["id"] == sid)
    sf["params"]["sketch"] = s.to_dict()
    doc.replace_feature(sf)
    assert not doc.errors.get(sid)


def test_side_face_names_are_unchanged_after_the_sketch_is_edited():
    doc, sid, f, err = thin(l_shape, thickness1=2, thickness2=0)
    assert not err, err
    before = face_names(doc, only(doc).id)
    assert len(before) == len(set(before)) == 8
    sketch_with(doc, sid, lambda s: l_shape(s, 30, 25))
    assert not doc.errors.get(f["id"])
    assert K.volume(only(doc).shape) == pytest.approx(((30 + 25) * 2 + 4) * 10)
    assert face_names(doc, only(doc).id) == before
    f["params"]["depth"] = 17
    doc.replace_feature(f)
    assert face_names(doc, only(doc).id) == before


def test_thin_revolve_names_are_unchanged_after_the_sketch_is_edited():
    doc, sid, f, err = thin(lambda s: one_line(s, 0, 10, 20, 10), ftype="revolve", thickness1=1, thickness2=1)
    assert not err, err
    before = face_names(doc, only(doc).id)
    sketch_with(doc, sid, lambda s: one_line(s, 0, 10, 25, 10))
    assert not doc.errors.get(f["id"])
    assert face_names(doc, only(doc).id) == before


def test_an_extrude_with_no_creation_key_is_unchanged():
    doc = PartStudioDoc()
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 20, 10), TOP)
    f = add(doc, "extrude", regions=region_refs(doc, sid), depth=10, op="new")
    assert not ({"creation", "curves", "thickness1", "thickness2", "mid_plane", "flip_wall"} & set(f["params"]))
    p = only(doc)
    assert K.volume(p.shape) == pytest.approx(2000) and len(K.faces(p.shape)) == 6 and p.name == "Part 1"


def test_thin_profile_returns_the_face_the_history_and_the_cap_edges():
    from freeshape.kernel import std_plane
    pl = std_plane("Top")
    sk = Sketch()
    one_line(sk)
    open_w = K.chain_wires(K.make_sketch_edges(pl, sk.curves()))[0]
    face, hist, caps = K.thin_profile(pl, open_w, 2, 1)
    assert len(caps) == 2 and K.mass_props(face)["area"] == pytest.approx(60)
    assert all(len(list(hist.Generated(e))) + len(list(hist.Modified(e))) == 2 for e in K.edges(open_w))      # one image per side
    with pytest.raises(K.KernelError, match="more than zero"):
        K.thin_profile(pl, open_w, 0, 0)


# ---------------------------------------------------------------------------- the dialog and the command search

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


def _segment_buttons(dlg, key):
    from PySide6.QtWidgets import QToolButton
    for spec, w in dlg.rows:
        if spec["t"] == "ops" and spec["key"] == key and w.isVisibleTo(dlg):
            return {b.text(): b for b in w.findChildren(QToolButton)}
    return {}


def _visible_labels(dlg):
    return [spec["label"] for spec, w in dlg.rows if spec.get("label") and w.isVisibleTo(dlg)]


@pytest.mark.parametrize("ftype", ["extrude", "revolve"])
def test_the_dialog_offers_thin_with_onshapes_field_names(make_win, ftype):
    from PySide6.QtWidgets import QApplication
    doc = PartStudioDoc()
    sid = sketch_feature(doc, l_shape, TOP)
    w = make_win(doc)
    w.start_feature(ftype)
    dlg = w.dialog
    assert list(_segment_buttons(dlg, "creation")) == ["Solid", "Surface", "Thin"]
    assert "Thickness 1" not in _visible_labels(dlg)
    _segment_buttons(dlg, "creation")["Thin"].click()
    QApplication.processEvents()
    assert dlg.feature["params"]["creation"] == "thin"
    assert dlg.feature["params"]["thickness1"] == 1.0 and dlg.feature["params"]["thickness2"] == 0.0
    shown = _visible_labels(dlg)
    for label in ("Mid plane", "Thickness 1", "Thickness 2", "Flip wall"):
        assert label in shown
    assert list(_segment_buttons(dlg, "op")) == ["New", "Add", "Remove", "Intersect"]
    dlg._set("mid_plane", True)
    shown = _visible_labels(dlg)
    assert "Thickness 1" in shown and "Thickness 2" not in shown and "Flip wall" not in shown
    _segment_buttons(dlg, "creation")["Solid"].click()
    assert "Thickness 1" not in _visible_labels(dlg)


def test_the_dialog_commits_a_thin_extrude(make_win):
    from PySide6.QtWidgets import QApplication
    doc = PartStudioDoc()
    sid = sketch_feature(doc, l_shape, TOP)
    w = make_win(doc)
    w.start_feature("extrude")
    dlg = w.dialog
    dlg._set("creation", "thin")
    QApplication.processEvents()
    assert dlg.active_field is dlg.fields["curves"]
    dlg._set("op", "new")
    dlg.handle_pick(Pick("sketch_line", (sid, ents(doc, sid, "line")[0])))
    dlg._set("thickness1", 2)
    dlg._set("depth", 10)
    w.commit_dialog()
    f = doc.features[-1]
    assert not doc.errors.get(f["id"]), doc.errors
    assert f["params"]["creation"] == "thin" and K.volume(only(doc).shape) == pytest.approx(840)


def test_the_command_search_finds_the_thin_and_surface_commands(make_win):
    from freeshape.ui.palette import search_commands
    w = make_win(PartStudioDoc())
    cmds = w._palette_commands()
    for text, name in (("thin extrude", "Extrude"), ("thin revolve", "Revolve"), ("surface revolve", "Revolve"),
                       ("surface sweep", "Sweep"), ("surface extrude", "Extrude")):
        assert name in [c.name for c in search_commands(cmds, text)], text
    assert {"Extrude", "Revolve"} <= {c.name for c in search_commands(cmds, "wall")}


# ---------------------------------------------------------------------------- the engine wire

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


def test_a_thin_extrude_made_in_the_engine_window_is_a_wall(engine_win):
    from PySide6.QtWidgets import QApplication
    w = engine_win
    se = w.studio_engine
    doc = w.doc
    sid = sketch_feature(doc, l_shape, TOP)
    assert se.settle(180)
    w.start_feature("extrude")
    dlg = w.dialog
    dlg._set("creation", "thin")
    QApplication.processEvents()
    dlg._set("op", "new")
    dlg.handle_pick(Pick("sketch_line", (sid, ents(doc, sid, "line")[0])))
    dlg._set("thickness1", 2)
    dlg._set("depth", 10)
    w.commit_dialog()
    assert se.settle(180)
    assert not doc.errors, doc.errors
    parts = list(doc.state.parts.values())
    assert len(parts) == 1
    p = parts[0]
    assert len(K.solids(p.shape)) == 1 and len(K.faces(p.shape)) == 8
    assert K.volume(p.shape) == pytest.approx(840)
    names = face_names(doc, p.id)
    assert len(names) == len(set(names)) == 8, "the window-side part carries the worker's element names"


def test_a_thin_revolve_made_in_the_engine_window_is_a_tube(engine_win):
    from PySide6.QtWidgets import QApplication
    w = engine_win
    se = w.studio_engine
    doc = w.doc
    sid = sketch_feature(doc, lambda s: one_line(s, 0, 10, 20, 10), TOP)
    assert se.settle(180)
    before = set(doc.state.parts)
    w.start_feature("revolve")
    dlg = w.dialog
    dlg._set("creation", "thin")
    QApplication.processEvents()
    dlg._set("op", "new")
    dlg.handle_pick(Pick("sketch_line", (sid, ents(doc, sid, "line")[0])))
    dlg._set("axis_std", "X")
    dlg._set("mid_plane", True)
    dlg._set("thickness1", 2)
    w.commit_dialog()
    assert se.settle(180)
    assert not doc.errors, doc.errors
    (pid,) = set(doc.state.parts) - before
    assert K.volume(doc.state.parts[pid].shape) == pytest.approx(math.pi * (11 ** 2 - 9 ** 2) * 20, rel=1e-4)
