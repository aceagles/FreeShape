"""I7.6: Sweep profile control. `control` is absent or empty (the profile follows the path, as always), `keep`
(a fixed trihedron from the profile's plane: every section stays parallel to the profile) or `lock` (a constant
binormal along `lock_dir`, an axis reference). Solid, surface and thin sweeps all take it."""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from conftest import FRONT, RIGHT, TOP, add, rect_lines, region_refs, sketch_feature, try_add  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.document import PartStudioDoc  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402

R = 40.0
Z = {"kind": "std", "name": "Z"}
NO_DIR = "Pick a direction for Lock profile direction"


def rect(s):
    rect_lines(s, -5, -3, 10, 6)


def quarter(s):
    """A quarter circle of radius 40 in the Front plane, from the origin heading down -z, ending heading +x."""
    s.add_arc(s.add_point(R, 0), s.add_point(0, 0), s.add_point(R, -R))


def ents(doc, sid, kind):
    return [i for i, e in doc.state.sketches[sid].sketch.entities.items() if e["type"] == kind]


def chain(doc, sid, kind):
    return {"kind": "sketch_chain", "sketch": sid, "entity": ents(doc, sid, kind)[0]}


def only(doc):
    (p,) = doc.state.parts.values()
    return p


def face_names(doc, pid):
    n = doc.state.named(pid)
    assert n is not None
    return sorted(n.names_of("F"))


def quarter_sweep(doc=None, **kw):
    doc = doc or PartStudioDoc()
    prof = sketch_feature(doc, rect, TOP)
    path = sketch_feature(doc, quarter, FRONT)
    kw.setdefault("op", "new")
    f, err = try_add(doc, "sweep", regions=region_refs(doc, prof), path=[chain(doc, path, "arc")], **kw)
    return doc, f, err


def cap_normals(shape):
    caps = [K.face_info(f) for f in K.faces(shape)]
    caps = [c for c in caps if "n" in c and abs(c["area"] - 60) < 1e-3]
    assert len(caps) == 2, caps
    caps.sort(key=lambda c: c["c"][2], reverse=True)         # the start cap is the one at z = 0
    return [np.array(c["n"]) for c in caps]


def angle_deg(a, b):
    return math.degrees(math.acos(max(-1, min(1, float(np.dot(a, b))))))


# ------------------------------------------------------------------------------------------- keep

def test_a_default_sweep_turns_the_end_cap_a_quarter_turn_and_keep_does_not():
    doc, f, err = quarter_sweep()
    assert not err, err
    a, b = cap_normals(only(doc).shape)
    assert angle_deg(a, b) == pytest.approx(90, abs=1e-6)
    doc, f, err = quarter_sweep(control="keep")
    assert not err, err
    a, b = cap_normals(only(doc).shape)
    assert abs(float(np.dot(a, b))) == pytest.approx(1, abs=1e-6)
    assert K.is_valid(only(doc).shape)


def test_a_sweep_with_no_control_key_is_what_it_was():
    base, f, err = quarter_sweep()
    assert not err, err
    empty, f2, err2 = quarter_sweep(control="")
    assert not err2, err2
    assert "control" not in f["params"]
    p0, p1 = only(base), only(empty)
    assert K.volume(p0.shape) == pytest.approx(60 * R * math.pi / 2, rel=1e-3)     # Pappus: area x path length
    assert len(K.faces(p0.shape)) == 6
    assert K.volume(p1.shape) == pytest.approx(K.volume(p0.shape), rel=1e-12)
    assert len(K.faces(p1.shape)) == len(K.faces(p0.shape))
    assert face_names(empty, p1.id) == face_names(base, p0.id)


def straight_sweep(**kw):
    doc = PartStudioDoc()
    prof = sketch_feature(doc, rect, TOP)
    path = sketch_feature(doc, lambda s: s.add_line(s.add_point(0, 0), s.add_point(0, -30)), FRONT)
    f, err = try_add(doc, "sweep", regions=region_refs(doc, prof), path=[chain(doc, path, "line")], op="new", **kw)
    assert not err, err
    return doc, only(doc)


def test_face_names_are_the_same_with_and_without_keep_on_a_straight_path():
    d0, p0 = straight_sweep()
    d1, p1 = straight_sweep(control="keep")
    assert K.volume(p1.shape) == pytest.approx(K.volume(p0.shape), rel=1e-9)
    assert face_names(d1, p1.id) == face_names(d0, p0.id) and len(face_names(d0, p0.id)) == 6


# ------------------------------------------------------------------------------------------- lock

def path_doc():
    """A tangent 3D path from two sketches: a quarter circle of radius 40 in the Top plane from the origin
    heading +x, then, in the plane x = 40 (an offset Right plane), a rising arc of radius 40 heading +y. The
    profile is in the Right plane at the origin, 10 along y and 6 along z."""
    doc = PartStudioDoc()
    prof = sketch_feature(doc, lambda s: rect_lines(s, -5, -3, 10, 6), RIGHT)
    a = sketch_feature(doc, lambda s: s.add_arc(s.add_point(0, R), s.add_point(0, 0), s.add_point(R, R)), TOP)
    pl = add(doc, "plane", type="offset", refs=[], ref=RIGHT, offset=R)
    t = math.radians(30)
    b = sketch_feature(doc, lambda s: s.add_arc(s.add_point(R, R), s.add_point(R, 0),
                                                s.add_point(R + R * math.sin(t), R - R * math.cos(t))),
                       {"kind": "plane", "fid": pl["id"]})
    return doc, prof, [chain(doc, a, "arc"), chain(doc, b, "arc")], pl


def stations():
    """(point, path tangent) at five stations on each arc."""
    out = []
    for t in np.radians([15, 30, 45, 60, 75]):
        out.append((np.array([R * math.sin(t), R - R * math.cos(t), 0.0]), np.array([math.cos(t), math.sin(t), 0])))
    for u in np.radians([6, 12, 18, 24, 29]):
        out.append((np.array([R, R + R * math.sin(u), R - R * math.cos(u)]), np.array([0, math.cos(u), math.sin(u)])))
    return out


def section_edges(shape, point, normal):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.gp import gp_Ax3, gp_Pln
    pl = BRepBuilderAPI_MakeFace(gp_Pln(gp_Ax3(K._pnt(point), K._dir(normal))), -100, 100, -100, 100).Face()
    sec = BRepAlgoAPI_Section(shape, pl)
    sec.Build()
    out = [np.array([K._vertex_xyz(v) for v in K._sub_shapes(e, K.TopAbs_VERTEX)])
           for e in K._sub_shapes(sec.Shape(), K.TopAbs_EDGE)]
    return [q for q in out if len(q) > 1]


def long_edge_dirs(shape, length=10.0):
    """The unit direction of each section edge `length` long at every station (two per station)."""
    out = []
    for p, n in stations():
        edges = section_edges(shape, p, n)
        long_ = [q for q in edges if abs(np.linalg.norm(q[-1] - q[0]) - length) < 0.05]
        assert len(long_) == 2, (p, [float(np.linalg.norm(q[-1] - q[0])) for q in edges])
        out += [(q[-1] - q[0]) / np.linalg.norm(q[-1] - q[0]) for q in long_]
    return out


def long_edge_angles_to_z(shape):
    """Degrees by which each long edge is off square to z."""
    return [90 - math.degrees(math.acos(min(1, abs(float(d[2]))))) for d in long_edge_dirs(shape)]


@pytest.mark.parametrize("creation", ["solid", "thin"])
def test_lock_z_keeps_the_long_edge_square_to_z_at_ten_stations(creation):
    doc, prof, path, _ = path_doc()
    if creation == "solid":
        extra = dict(regions=region_refs(doc, prof))
    else:
        extra = dict(creation="thin", curves=[chain(doc, prof, "line")], thickness1=1.0)
    f, err = try_add(doc, "sweep", path=path, op="new", control="lock", lock_dir=Z, **extra)
    assert not err, err
    shape = only(doc).shape
    assert K.is_valid(shape)
    if creation == "solid":
        assert max(abs(a) for a in long_edge_angles_to_z(shape)) < 0.1


def test_lock_with_no_direction_picked_is_refused():
    doc, prof, path, _ = path_doc()
    f, err = try_add(doc, "sweep", path=path, op="new", regions=region_refs(doc, prof), control="lock")
    assert err == NO_DIR
    f, err = try_add(doc, "sweep", path=path, op="new", regions=region_refs(doc, prof), control="lock", lock_dir={})
    assert err == NO_DIR


def test_a_sketch_line_picked_as_the_lock_direction_resolves():
    doc, prof, path, _ = path_doc()
    zl = sketch_feature(doc, lambda s: s.add_line(s.add_point(0, 0), s.add_point(0, 10)), FRONT)    # along world z
    ref = {"kind": "sketch_line", "sketch": zl, "line": ents(doc, zl, "line")[0]}
    f, err = try_add(doc, "sweep", path=path, op="new", regions=region_refs(doc, prof), control="lock", lock_dir=ref)
    assert not err, err
    assert max(abs(a) for a in long_edge_angles_to_z(only(doc).shape)) < 0.1


# ------------------------------------------------------------------------------------------- modes

def test_keep_and_lock_work_for_a_surface_sweep():
    for ctl in ({"control": "keep"}, {"control": "lock", "lock_dir": Z}):
        doc = PartStudioDoc()
        prof = sketch_feature(doc, rect, TOP)
        path = sketch_feature(doc, quarter, FRONT)
        f, err = try_add(doc, "sweep", creation="surface", curves=[chain(doc, prof, "line")],
                         path=[chain(doc, path, "arc")], op="new", **ctl)
        assert not err, (ctl, err)
        assert K.faces(only(doc).shape) and not K.solids(only(doc).shape)


def test_keep_works_for_a_thin_sweep_and_a_straight_path_matches_the_default():
    vols = []
    for ctl in ({}, {"control": "keep"}):
        doc = PartStudioDoc()
        prof = sketch_feature(doc, lambda s: s.add_line(s.add_point(-5, 0), s.add_point(5, 0)), TOP)
        path = sketch_feature(doc, lambda s: s.add_line(s.add_point(0, 0), s.add_point(0, -30)), FRONT)
        f, err = try_add(doc, "sweep", creation="thin", curves=[chain(doc, prof, "line")],
                         path=[chain(doc, path, "line")], op="new", thickness1=2.0, **ctl)
        assert not err, (ctl, err)
        vols.append(K.volume(only(doc).shape))
    assert vols[0] == pytest.approx(5 * 0 + 10 * 2 * 30, rel=1e-6) and vols[1] == pytest.approx(vols[0], rel=1e-9)


def test_an_unknown_control_is_refused():
    doc, f, err = quarter_sweep(control="twist")
    assert err == "Unknown profile control"


# ------------------------------------------------------------------------------------------- the window

@pytest.fixture
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


def test_the_dialog_offers_the_three_profile_controls_and_a_direction_only_for_lock(engine_win):
    from PySide6.QtWidgets import QApplication
    w = engine_win
    w.start_feature("sweep")
    dlg = w.dialog
    spec = next(s for s in dlg.schema if s["key"] == "control")
    assert spec["label"] == "Profile control"
    assert [lab for _, lab in spec["options"]] == ["None", "Keep profile orientation", "Lock profile direction"]
    dlg._set("control", "keep")
    QApplication.processEvents()
    dlg._sync_visibility()
    row = next(w for sp, w in dlg.rows if sp["key"] == "lock_dir")
    assert row.isHidden()
    dlg._set("control", "lock")
    dlg._sync_visibility()
    assert not row.isHidden()
    w.cancel_dialog()


def drive(w, control, lock_line=None):
    from PySide6.QtWidgets import QApplication
    se, doc = w.studio_engine, w.doc
    prof = sketch_feature(doc, rect, TOP)
    path = sketch_feature(doc, quarter, FRONT)
    zl = sketch_feature(doc, lambda s: s.add_line(s.add_point(0, 0), s.add_point(0, 10)), FRONT)
    assert se.settle(180)
    w.start_feature("sweep")
    dlg = w.dialog
    dlg._set("op", "new")
    dlg._set("control", control)
    QApplication.processEvents()
    dlg.activate(dlg.fields["profiles"])
    dlg.handle_pick(Pick("region", (prof, 0)))
    dlg.activate(dlg.fields["path"])
    dlg.handle_pick(Pick("sketch_line", (path, ents(doc, path, "arc")[0])))
    if lock_line:
        dlg.activate(dlg.fields["lock_dir"])
        dlg.handle_pick(Pick("sketch_line", (zl, ents(doc, zl, "line")[0])))
    w.commit_dialog()
    assert se.settle(180)
    return doc


def test_keep_made_in_the_engine_window_keeps_the_cap_parallel(engine_win):
    doc = drive(engine_win, "keep")
    assert not doc.errors, doc.errors
    a, b = cap_normals(only(doc).shape)
    assert abs(float(np.dot(a, b))) == pytest.approx(1, abs=1e-6)
    f = [x for x in doc.features if x["type"] == "sweep"][-1]
    assert f["params"]["control"] == "keep" and "lock_dir" not in f["params"]


def test_lock_made_in_the_engine_window_resolves_its_direction_in_the_worker(engine_win):
    w = engine_win
    doc = drive(w, "lock", lock_line=True)
    assert not doc.errors, doc.errors
    f = [x for x in doc.features if x["type"] == "sweep"][-1]
    assert f["params"]["control"] == "lock" and f["params"]["lock_dir"]["kind"] == "sketch_line"
    assert len(K.solids(doc.state.parts[max(doc.state.parts)].shape)) == 1


def test_command_search_finds_sweep_by_profile_orientation(engine_win):
    from freeshape.ui.palette import search_commands
    cmds = engine_win._palette_commands()
    assert "Sweep" in [c.name for c in search_commands(cmds, "profile orientation")][:3]
    for words in ("surface sweep", "thin sweep", "pipe"):          # the keywords already there still find it
        assert "Sweep" in [c.name for c in search_commands(cmds, words)][:3], words
