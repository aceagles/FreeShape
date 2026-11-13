"""I1.5: an axis feature is accepted wherever a feature asks for an axis (revolve, circular pattern, linear pattern,
move / rotate) and where the plane feature takes a straight edge (Line + angle, Point + edge normal)."""
import copy
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from conftest import add, both, centre_near, edge_refs, line_along, only_part, region_refs, sketch_feature  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.ui.dialogs import item_geometry, item_label  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402
from test_connector_frames import H, plate_studio, set_dim  # noqa: E402

W, D = 60.0, 40.0
TOP = {"kind": "std", "name": "Top"}
X = np.array([1.0, 0, 0])


def _edge_ref(doc, y):
    """The plate's bottom edge along X at the given y."""
    part = only_part(doc)
    (r,) = edge_refs(part, both(line_along(X), centre_near([W / 2, y, 0])), expect=1)
    return r


def _studio(use, y=0):
    """A plate, a circle sketch to revolve, and (use='axis') an axis feature on the y = 0 edge. Returns
    (doc, ids, axis ref): the axis ref is {kind: axis, fid} or {kind: edge, edge} as asked."""
    doc, ids = plate_studio(W, D)
    ids["circle"] = sketch_feature(doc, lambda s: s.add_circle(s.add_point(30, 20), 3), TOP)
    e = _edge_ref(doc, y)
    if use == "axis":
        af = add(doc, "axis", refs=[{"k": "edge", "ref": e}])
        ids["axis"] = af["id"]
        return doc, ids, {"kind": "axis", "fid": af["id"]}
    if use == "sketch_line":
        return doc, ids, {"kind": "sketch_line", "sketch": ids["sketch"], "line": _first_line(doc, ids)}
    return doc, ids, {"kind": "edge", "edge": e}


def _first_line(doc, ids):
    sk = doc.state.sketches[ids["sketch"]].sketch
    return next(i for i, e in sk.entities.items() if e["type"] == "line")


def _extra_parts(doc, plate_id):
    return [p for pid, p in doc.state.parts.items() if pid != plate_id]


def _sig(parts):
    """Volumes and bounding boxes, sorted, as numbers to compare."""
    rows = []
    for p in parts:
        lo, hi = K.bbox(p.shape)
        rows.append([K.volume(p.shape), *np.asarray(lo, float), *np.asarray(hi, float)])
    return np.array(sorted(rows, key=lambda r: tuple(round(v, 3) for v in r)))


def _same_sig(a, b):
    assert a.shape == b.shape and len(a) > 0
    assert np.allclose(a, b, rtol=1e-6, atol=1e-5), (a, b)


def _revolve(doc, ids, ref):
    return add(doc, "revolve", regions=region_refs(doc, ids["circle"]), axis=ref, end="full", op="new")


def _plate_id(doc):
    return next(iter(doc.state.parts))


# --------------------------------------------------------------------------- the dialog (main window)

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


def test_revolve_dialog_takes_an_axis_pick(make_win):
    doc, ids, ref = _studio("axis")
    base, bids, eref = _studio("edge")
    pid = _plate_id(doc)
    _revolve(base, bids, eref)
    want = _sig(_extra_parts(base, _plate_id(base)))

    w = make_win(doc)
    w.start_feature("revolve")
    dlg = w.dialog
    assert dlg.ftype == "revolve"
    dlg._set("op", "new")
    dlg.fields["profiles"].toggle(("region", region_refs(doc, ids["circle"])[0]))
    fld = dlg.fields["axis_sel"]
    dlg.activate(fld)
    assert "axis" in w.viewport.select_filter
    dlg.handle_pick(Pick("axis", (ids["axis"],)))
    assert fld.items == [("axis", ref)]
    name = doc.feature(ids["axis"])["name"]
    assert item_label(doc, fld.items[0]) == name
    assert fld.list.item(0).text() == name
    kind, pts = item_geometry(doc.state, fld.items[0])
    assert kind == "poly" and len(pts) == 2 and np.allclose(np.asarray(pts)[:, 1:], 0)
    w.commit_dialog()
    (rv,) = [f for f in doc.features if f["type"] == "revolve"]
    assert doc.errors.get(rv["id"]) is None
    assert rv["params"]["axis"] == ref
    _same_sig(_sig(_extra_parts(doc, pid)), want)


def test_other_axis_fields_offer_the_axis_pick(make_win):
    doc, ids, ref = _studio("axis")
    w = make_win(doc)
    for ftype, key in (("circular_pattern", "axis_sel"), ("linear_pattern", "dir_sel"), ("move", "axis_sel")):
        w.start_feature(ftype)
        dlg = w.dialog
        dlg.activate(dlg.fields[key])
        assert "axis" in w.viewport.select_filter, ftype
        dlg.handle_pick(Pick("axis", (ids["axis"],)))
        assert dlg.fields[key].items == [("axis", ref)], ftype
        w.cancel_dialog()
    w.start_feature("plane")
    dlg = w.dialog
    dlg.activate(dlg.fields["refs_sel"])
    assert "axis" in w.viewport.select_filter
    dlg.handle_pick(Pick("axis", (ids["axis"],)))
    assert [k for k, _ in dlg.fields["refs_sel"].items] == ["axis"]
    w.cancel_dialog()


def test_other_fields_do_not_pick_an_axis_unasked(make_win):
    doc, ids, _ = _studio("axis")
    w = make_win(doc)
    w.start_feature("fillet")
    dlg = w.dialog
    dlg.activate(dlg.fields["edges"])
    assert "axis" not in w.viewport.select_filter
    dlg.handle_pick(Pick("axis", (ids["axis"],)))
    assert dlg.fields["edges"].items == []
    w.cancel_dialog()


# --------------------------------------------------------------------------- patterns and move

def test_circular_pattern_about_an_axis_equals_the_edge():
    results = []
    for use in ("axis", "edge"):
        doc, ids, ref = _studio(use)
        add(doc, "circular_pattern", parts=[_plate_id(doc)], axis=ref, count=3, angle=180.0, op="new")
        results.append(_sig(doc.state.parts.values()))
    assert len(results[0]) == 3
    _same_sig(*results)


def test_linear_pattern_along_an_axis_equals_the_edge():
    results = []
    for use in ("axis", "edge"):
        doc, ids, ref = _studio(use)
        pid = _plate_id(doc)
        add(doc, "linear_pattern", parts=[pid], direction=ref, count=3, spacing=70.0, op="new")
        results.append(_sig(doc.state.parts.values()))
    assert len(results[0]) == 3
    _same_sig(*results)
    # the second direction takes an axis too
    out = []
    for use in ("axis", "edge"):
        doc, ids, ref = _studio(use)
        add(doc, "linear_pattern", parts=[_plate_id(doc)], direction={"kind": "std", "name": "Y"}, count=2,
            spacing=50.0, dir2=ref, dir2_on=True, count2=2, spacing2=80.0, op="new")
        out.append(_sig(doc.state.parts.values()))
    assert len(out[0]) == 4
    _same_sig(*out)


def test_rotate_about_an_axis_equals_the_edge():
    results = []
    for use in ("axis", "edge"):
        doc, ids, ref = _studio(use)
        add(doc, "move", parts=[_plate_id(doc)], rotate=90.0, axis=ref, copy=False)
        results.append(_sig(doc.state.parts.values()))
    _same_sig(*results)
    assert results[0][0][6] > D - 1           # the plate did turn: its y extent now runs up the z axis


# --------------------------------------------------------------------------- the plane feature

def _plane(doc, **params):
    f = doc.make_feature("plane")
    f["params"].update(params)
    st, err = doc.preview(f, doc.insert_index())
    assert err is None, err
    return st.planes[f["id"]]


def _vref(doc, pos):
    part = only_part(doc)
    return {"k": "vertex", "ref": F.make_vertex_ref(part.id, pos, part.shape)}


def _same_plane(a, b):
    assert abs(abs(float(a.normal @ b.normal)) - 1) < 1e-6
    assert abs(float((a.origin - b.origin) @ b.normal)) < 1e-6


def test_axis_is_a_line_for_the_plane_feature():
    assert F.plane_role("axis", {"kind": "axis", "fid": "x"}) == "line"
    axis = {"k": "axis", "ref": {"kind": "axis", "fid": "x"}}
    assert F.plane_type({"type": "auto", "refs": [axis]}) == "line_angle"
    pt = {"k": "vertex", "ref": {}}
    assert F.plane_type({"type": "auto", "refs": [axis, pt]}) == "point_normal"


def test_plane_line_angle_on_an_axis_equals_the_edge():
    doc, ids, aref = _studio("axis")
    eref = {"k": "edge", "ref": _edge_ref(doc, 0)}
    for ang in (30.0, 90.0):
        on_axis = _plane(doc, type="line_angle", refs=[{"k": "axis", "ref": aref}], angle=ang)
        on_edge = _plane(doc, type="line_angle", refs=[eref], angle=ang)
        _same_plane(on_axis, on_edge)
    auto = _plane(doc, type="auto", refs=[{"k": "axis", "ref": aref}], angle=30.0)
    _same_plane(auto, _plane(doc, type="line_angle", refs=[eref], angle=30.0))


def test_plane_point_normal_on_an_axis_equals_the_edge():
    doc, ids, aref = _studio("axis")
    eref = {"k": "edge", "ref": _edge_ref(doc, 0)}
    v = _vref(doc, (20.0, D, H))
    on_axis = _plane(doc, type="point_normal", refs=[{"k": "axis", "ref": aref}, v])
    on_edge = _plane(doc, type="point_normal", refs=[eref, v])
    _same_plane(on_axis, on_edge)
    assert abs(abs(float(on_axis.normal @ X)) - 1) < 1e-6


# --------------------------------------------------------------------------- rebuild and missing axes

def test_moving_the_axis_source_moves_the_revolve_and_suppressing_it_reports_missing_axis():
    doc, ids, ref = _studio("axis", y=D)                  # the axis lies on the far edge, y = 40
    rv = _revolve(doc, ids, ref)
    plate = _plate_id(doc)
    (before,) = _sig(_extra_parts(doc, plate))
    assert abs(doc.state.axes[ids["axis"]]["o"][1] - D) < 1e-6
    set_dim(doc, ids["sketch"], ids["d"], 30.0)            # the far edge moves to y = 30
    assert doc.errors.get(rv["id"]) is None, doc.errors
    assert abs(doc.state.axes[ids["axis"]]["o"][1] - 30.0) < 1e-6
    (after,) = _sig(_extra_parts(doc, plate))
    assert abs(before[0] - after[0]) > 1.0                 # a torus of mean radius 20 became one of 10
    assert (after[5] - after[2]) < (before[5] - before[2]) - 1.0     # and its y extent shrank
    f = copy.deepcopy(doc.feature(ids["axis"]))
    f["suppressed"] = True
    doc.replace_feature(f)
    assert doc.errors.get(rv["id"]) == "Missing axis"


def test_an_axis_that_does_not_exist_yet_reports_missing_axis():
    doc, ids, _ = _studio("edge")
    later = {"kind": "axis", "fid": "not-yet"}
    f = doc.make_feature("revolve")
    f["params"].update(regions=region_refs(doc, ids["circle"]), axis=later, end="full", op="new")
    doc.add_feature(f)
    assert doc.errors.get(f["id"]) == "Missing axis"
    pf = doc.make_feature("plane")
    pf["params"].update(type="line_angle", refs=[{"k": "axis", "ref": later}], angle=45.0)
    assert doc.preview(pf, doc.insert_index())[1] == "Missing axis"


# --------------------------------------------------------------------------- unchanged for edges and sketch lines

def test_revolve_about_an_edge_and_a_sketch_line_build_as_before():
    # the circle (r = 3) at (30, 20) revolved about the y = 0 edge: a torus, V = 2 pi^2 R r^2
    doc, ids, ref = _studio("edge")
    _revolve(doc, ids, ref)
    (p,) = _extra_parts(doc, _plate_id(doc))
    assert abs(K.volume(p.shape) - 2 * np.pi ** 2 * 20.0 * 9.0) < 1e-3 * 2 * np.pi ** 2 * 20.0 * 9.0
    # a sketch line: the plate sketch's first side (y = 0 as well) gives the same solid
    doc2, ids2, ref2 = _studio("sketch_line")
    _revolve(doc2, ids2, ref2)
    (q,) = _extra_parts(doc2, _plate_id(doc2))
    assert abs(K.volume(q.shape) - K.volume(p.shape)) < 1e-6 * K.volume(p.shape)
    assert F.resolve_axis(doc2.state, ref2)[1].tolist() in ([1.0, 0, 0], [-1.0, 0, 0], [1.0, 0.0, 0.0])
