"""I1.6: a Part Studio mate connector is accepted wherever a feature asks for an axis (revolve, circular pattern,
linear pattern, move / rotate) and as a line in the plane feature. Offscreen main window."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from conftest import add, only_part, region_refs, sketch_feature  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.ui.dialogs import item_geometry, item_label  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402
from test_connector_frames import H, plate_studio, set_dim  # noqa: E402
from test_studio_connectors import connector_params  # noqa: E402

W, D = 60.0, 40.0
TOP = {"kind": "std", "name": "Top"}
CENTRE = np.array([W / 2, D / 2, H])        # the connector on the top face sits at the face centre


def _studio():
    """A plate, a circle sketch to revolve and a mate connector on the plate's top face."""
    doc, ids = plate_studio(W, D)
    ids["circle"] = sketch_feature(doc, lambda s: s.add_circle(s.add_point(45, 5), 3), {"kind": "std", "name": "Front"})
    c = add(doc, "mate_connector", **connector_params(doc, "Top"))
    ids["conn"] = c["id"]
    return doc, ids, {"kind": "connector", "fid": c["id"], "axis": "z"}


def _plate_id(doc):
    return next(iter(doc.state.parts))


def _extra(doc):
    plate = _plate_id(doc)
    return [p for pid, p in doc.state.parts.items() if pid != plate]


def _sig(parts):
    rows = []
    for p in parts:
        lo, hi = K.bbox(p.shape)
        rows.append([K.volume(p.shape), *np.asarray(lo, float), *np.asarray(hi, float)])
    return np.array(sorted(rows, key=lambda r: tuple(round(v, 3) for v in r)))


def _revolve(doc, ids, ref):
    return add(doc, "revolve", regions=region_refs(doc, ids["circle"]), axis=ref, end="full", op="new")


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


def test_resolve_and_label_and_geometry():
    doc, ids, ref = _studio()
    o, d = F.resolve_axis(doc.state, ref)
    assert np.allclose(o, CENTRE) and np.allclose(d, [0, 0, 1])
    name = doc.feature(ids["conn"])["name"]
    assert item_label(doc, ("axis", ref)) == f"{name} Z"
    kind, pts = item_geometry(doc.state, ("axis", ref))
    pts = np.asarray(pts)
    assert kind == "poly" and np.allclose(pts[0], CENTRE) and np.allclose(pts[1][:2], CENTRE[:2]) and pts[1][2] > H


def test_circular_pattern_dialog_takes_a_connector_pick(make_win):
    doc, ids, ref = _studio()
    # the same pattern about an axis feature standing on the connector (the reference result)
    base, bids, bref = _studio()
    af = add(base, "axis", type="connector", refs=[{"k": "connector", "ref": {"fid": bids["conn"]}}])
    add(base, "circular_pattern", parts=[_plate_id(base)], axis={"kind": "axis", "fid": af["id"]}, count=3,
        angle=180.0, op="new")
    want = _sig(base.state.parts.values())

    w = make_win(doc)
    w.start_feature("circular_pattern")
    dlg = w.dialog
    dlg.fields["parts"].toggle(("part", _plate_id(doc)))
    fld = dlg.fields["axis_sel"]
    dlg.activate(fld)
    assert "connector" in w.viewport.select_filter
    dlg.handle_pick(Pick("connector", (ids["conn"],)))
    assert fld.items == [("axis", ref)]
    name = doc.feature(ids["conn"])["name"]
    assert item_label(doc, fld.items[0]) == f"{name} Z" and fld.list.item(0).text() == f"{name} Z"
    dlg._set("count", 3)
    dlg._set("angle", 180.0)
    dlg._set("op", "new")
    w.commit_dialog()
    (pf,) = [f for f in doc.features if f["type"] == "circular_pattern"]
    assert doc.errors.get(pf["id"]) is None, doc.errors
    assert pf["params"]["axis"] == ref
    got = _sig(doc.state.parts.values())
    assert len(got) == 3
    assert got.shape == want.shape and np.allclose(got, want, rtol=1e-6, atol=1e-5)
    # turned about the vertical through the face centre: the 90 degree copy spans x 10..50, y -10..50
    assert any(np.allclose(r[1:], [10, -10, 0, 50, 50, H], atol=1e-4) for r in got)


def test_other_axis_fields_offer_the_connector_pick(make_win):
    doc, ids, ref = _studio()
    w = make_win(doc)
    for ftype, key in (("revolve", "axis_sel"), ("linear_pattern", "dir_sel"), ("linear_pattern", "dir2_sel"),
                       ("move", "axis_sel")):
        w.start_feature(ftype)
        dlg = w.dialog
        dlg.activate(dlg.fields[key])
        assert "connector" in w.viewport.select_filter, (ftype, key)
        dlg.handle_pick(Pick("connector", (ids["conn"],)))
        assert dlg.fields[key].items == [("axis", ref)], (ftype, key)
        w.cancel_dialog()
    w.start_feature("plane")
    dlg = w.dialog
    dlg.activate(dlg.fields["refs_sel"])
    assert "connector" in w.viewport.select_filter
    dlg.handle_pick(Pick("connector", (ids["conn"],)))
    assert dlg.fields["refs_sel"].items == [("axis", ref)]
    w.cancel_dialog()


def test_a_field_that_wants_no_axis_ignores_a_connector_pick(make_win):
    doc, ids, _ = _studio()
    w = make_win(doc)
    w.start_feature("fillet")
    dlg = w.dialog
    dlg.activate(dlg.fields["edges"])
    assert "connector" not in w.viewport.select_filter
    dlg.handle_pick(Pick("connector", (ids["conn"],)))
    assert dlg.fields["edges"].items == []
    w.cancel_dialog()


def test_revolve_about_a_connector_follows_it_and_reports_it_missing():
    doc, ids, ref = _studio()
    rv = _revolve(doc, ids, ref)
    plate = _plate_id(doc)
    (before,) = _sig(_extra(doc))
    # the circle (r 3, on the Front plane) sits 15 from the connector's vertical axis: a torus of that mean radius
    assert abs(before[0] - 2 * np.pi ** 2 * 15.0 * 9.0) < 1e-3 * before[0]
    set_dim(doc, ids["sketch"], ids["w"], 50.0)       # the face centre moves to x = 25
    assert doc.errors.get(rv["id"]) is None, doc.errors
    assert abs(doc.state.connectors[ids["conn"]]["frame"]["o"][0] - 25.0) < 1e-6
    (after,) = _sig(_extra(doc))
    assert abs(after[0] - 2 * np.pi ** 2 * 20.0 * 9.0) < 1e-3 * after[0]          # mean radius 15 -> 20
    assert plate in doc.state.parts
    doc.delete_features([ids["conn"]])
    assert doc.errors.get(rv["id"]) == "Missing mate connector"


def test_plane_line_angle_on_a_connector_contains_its_z_axis():
    doc, ids, ref = _studio()
    f = doc.make_feature("plane")
    f["params"].update(type="line_angle", refs=[{"k": "axis", "ref": ref}], angle=30.0)
    st, err = doc.preview(f, doc.insert_index())
    assert err is None, err
    pl = st.planes[f["id"]]
    for t in (0.0, 7.0, 25.0):
        assert abs(float((CENTRE + [0, 0, t] - pl.origin) @ pl.normal)) < 1e-6
    f["params"]["type"] = "auto"
    st, err = doc.preview(f, doc.insert_index())
    assert err is None and st.planes[f["id"]] is not None


def test_a_click_on_a_connector_with_no_dialog_selects_nothing(make_win):
    doc, ids, _ = _studio()
    w = make_win(doc)
    vp = w.viewport
    assert vp.pick_handler is None
    vp.click_pick(Pick("connector", (ids["conn"],)))
    assert vp.selection == []
