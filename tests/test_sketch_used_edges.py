"""A part edge used in a sketch follows the part: the Use tool stores a reference to the edge on the entity it
makes (`src`), and the sketch projects the edge again on every rebuild and when it is opened for editing."""
import copy
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from conftest import add, only_part, region_refs  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import sketch_ops as OPS  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch, ORIGIN  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402
from test_connector_frames import plate_studio, set_dim  # noqa: E402
from test_sketch_inference import _sketcher  # noqa: E402

W, D = 60.0, 40.0
TOP = {"kind": "std", "name": "Top"}


class _Viewport:
    def __init__(self, state):
        self.part_shapes = {pid: p.shape for pid, p in state.parts.items()}


def _edge_index(doc, pred):
    part = only_part(doc)
    seams = K.seam_edge_indices(part.shape)
    hits = [i for i, e in enumerate(K.edges(part.shape)) if i not in seams and pred(K.edge_info(e))]
    assert len(hits) == 1, hits
    return part.id, hits[0]


def _use(doc, pred, linked=True):
    """A sketcher on Top with the one edge matching `pred` used in it. Returns (sketcher, entity id)."""
    s = _sketcher()
    s.base_state = doc.state if linked else None
    eid = s._use_edge(_Viewport(doc.state), Pick("edge", _edge_index(doc, pred)))
    assert eid in s.sk.entities
    return s, eid


def _add_sketch(doc, sk):
    assert sk.solve()
    f = doc.make_feature("sketch")
    f["params"].update(plane=TOP, sketch=sk.to_dict())
    doc.add_feature(f)
    assert not doc.errors, doc.errors
    return f["id"]


def _built(doc, fid):
    return doc.state.sketches[fid].sketch


def _far_edge(i):
    """The plate's bottom edge along X at y = D."""
    return i["type"] == int(K.GeomAbs_Line) and abs(i["c"][2]) < 1e-6 and abs(abs(i["d"][0]) - 1) < 1e-6 \
        and i["c"][1] > 1.0


def _bottom_circle(i):
    return "r" in i and abs(i["c"][2]) < 1e-6


def _disc(r=30.0):
    """A cylinder of radius r from a dimensioned circle on Top. Returns (doc, sketch id, radius constraint id)."""
    doc = Document()
    s = Sketch()
    c = s.add_circle(ORIGIN, r)
    cid = s.add_constraint("radius", c, value=r)["id"]
    assert s.solve()
    f = doc.make_feature("sketch")
    f["params"]["sketch"] = s.to_dict()
    doc.add_feature(f)
    add(doc, "extrude", regions=region_refs(doc, f["id"]), depth=10.0, op="new")
    return doc, f["id"], cid


# --------------------------------------------------------------------------- the Use tool stores the reference

def test_use_stores_a_named_reference_to_the_edge():
    doc, _ids = plate_studio(W, D)
    s, eid = _use(doc, _far_edge)
    src = s.sk.entities[eid]["src"]
    assert src["name"] and src["part"] == only_part(doc).id
    _, edge = F.resolve(doc.state, src, "edge")
    assert np.allclose(K.edge_info(edge)["c"], [W / 2, D, 0])


def test_use_without_a_model_to_check_against_stores_no_reference():
    doc, _ids = plate_studio(W, D)
    s, eid = _use(doc, _far_edge, linked=False)
    assert "src" not in s.sk.entities[eid]


def test_the_radius_of_a_circle_that_follows_the_part_is_not_shown_as_a_dimension():
    doc, _sid, _cid = _disc(30.0)
    s, eid = _use(doc, _bottom_circle)
    radius = [c for c in s.sk.constraints if c["type"] == "radius" and c["refs"][0] == eid]
    assert len(radius) == 1 and radius[0] not in s._shown_dimensions()
    OPS.offset(s.sk, eid, 5.0)                    # the user's own dimensions are still shown
    assert [c["type"] for c in s._shown_dimensions()] == ["offset"]


def test_the_radius_of_a_fixed_copy_is_still_a_dimension():
    doc, _sid, _cid = _disc(30.0)
    s, eid = _use(doc, _bottom_circle, linked=False)
    assert [c["type"] for c in s._shown_dimensions()] == ["radius"]
    s2, eid2 = _use(doc, _bottom_circle)          # linked, then freed: the user owns it again
    centre = s2.sk.entities[eid2]["c"]
    s2.sk.constraints = [c for c in s2.sk.constraints if not (c["type"] == "fix" and c["refs"][0] == centre)]
    assert [c["type"] for c in s2._shown_dimensions()] == ["radius"]


# --------------------------------------------------------------------------- the rebuild projects it again

def test_a_used_circle_and_its_offset_follow_the_part():
    doc, sid, cid = _disc(30.0)
    s, eid = _use(doc, _bottom_circle)
    OPS.offset(s.sk, eid, 5.0)
    (inner,) = [k for k, e in s.sk.entities.items() if e["type"] == "circle" and k != eid]
    gap = s.sk.radius(eid) - s.sk.radius(inner)
    assert abs(abs(gap) - 5.0) < 1e-6
    fid = _add_sketch(doc, s.sk)

    set_dim(doc, sid, cid, 42.0)

    sk = _built(doc, fid)
    assert abs(sk.radius(eid) - 42.0) < 1e-6
    assert abs((sk.radius(eid) - sk.radius(inner)) - gap) < 1e-6
    assert any(c["type"] == "radius" and c["refs"][0] == eid and abs(c["value"] - 42.0) < 1e-6 for c in sk.constraints)
    # the stored feature is not rewritten: the rebuild works on a copy, as it does for variables
    assert abs(doc.feature(fid)["params"]["sketch"]["entities"][eid]["r"] - 30.0) < 1e-9


def test_a_used_line_follows_the_part_and_carries_what_hangs_off_it():
    doc, ids = plate_studio(W, D)
    s, eid = _use(doc, _far_edge)
    sk = s.sk
    q = sk.add_point(20.0, D)
    sk.add_line(ORIGIN, q)
    sk.add_constraint("point_on", q, eid)
    sk.add_constraint("hdistance", ORIGIN, q, value=20.0)
    fid = _add_sketch(doc, sk)
    ends = {k: tuple(np.round(_built(doc, fid).P(sk.entities[eid][k]), 6)) for k in ("p1", "p2")}

    set_dim(doc, ids["sketch"], ids["d"], 55.0)

    out = _built(doc, fid)
    for k in ("p1", "p2"):                       # each end stays the end it was, at the new y
        assert np.allclose(out.P(out.entities[eid][k]), [ends[k][0], 55.0], atol=1e-6)
    assert np.allclose(out.P(q), [20.0, 55.0], atol=1e-6)


def test_a_copy_without_a_reference_stays_where_it_was():
    doc, ids = plate_studio(W, D)
    s, eid = _use(doc, _far_edge, linked=False)
    fid = _add_sketch(doc, s.sk)
    set_dim(doc, ids["sketch"], ids["d"], 55.0)
    out = _built(doc, fid)
    assert np.allclose([out.P(out.entities[eid][k])[1] for k in ("p1", "p2")], D)


def test_a_used_line_the_user_has_freed_is_left_alone():
    doc, ids = plate_studio(W, D)
    s, eid = _use(doc, _far_edge)
    sk = s.sk
    p1 = sk.entities[eid]["p1"]
    sk.constraints = [c for c in sk.constraints if not (c["type"] == "fix" and c["refs"][0] == p1)]
    fid = _add_sketch(doc, sk)
    set_dim(doc, ids["sketch"], ids["d"], 55.0)
    out = _built(doc, fid)
    assert np.allclose([out.P(out.entities[eid][k])[1] for k in ("p1", "p2")], D)


def test_nothing_is_solved_again_when_the_edge_has_not_moved():
    doc, _ids = plate_studio(W, D)
    s, eid = _use(doc, _far_edge)
    sk = Sketch.from_dict(s.sk.to_dict())
    before = sk.to_dict()
    assert F.refresh_used_edges(doc.state, K.std_plane("Top"), sk) is False
    assert sk.to_dict() == before


# --------------------------------------------------------------------------- an edge that is gone

def _with_lost_edge(doc):
    s, eid = _use(doc, _far_edge)
    fid = _add_sketch(doc, s.sk)
    f = copy.deepcopy(doc.feature(fid))
    f["params"]["sketch"]["entities"][eid]["src"]["name"] += ";GONE"
    doc.replace_feature(f)
    return fid, eid


def test_a_used_edge_that_is_gone_is_an_error_naming_what_to_do():
    doc, _ids = plate_studio(W, D)
    fid, _eid = _with_lost_edge(doc)
    err = str(doc.errors.get(fid))
    assert "used in this sketch is gone" in err and "use the edge again" in err and ";GONE" in err


def test_the_lost_list_collects_instead_of_raising():
    doc, _ids = plate_studio(W, D)
    fid, eid = _with_lost_edge(doc)
    sk = Sketch.from_dict(doc.feature(fid)["params"]["sketch"])
    before = sk.to_dict()
    lost = []
    assert F.refresh_used_edges(doc.state_before(doc.feature_index(fid)), K.std_plane("Top"), sk, lost) is False
    assert lost == [eid] and sk.to_dict() == before


# --------------------------------------------------------------------------- the sketch editor (main window)

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


def test_the_editor_opens_on_the_edge_as_it_is_now_and_new_uses_are_linked(make_win):
    doc, ids = plate_studio(W, D)
    s, eid = _use(doc, _far_edge)
    fid = _add_sketch(doc, s.sk)
    set_dim(doc, ids["sketch"], ids["d"], 55.0)

    w = make_win(doc)
    w.start_sketch(fid)
    sk = w.sketcher.sk
    assert np.allclose([sk.P(sk.entities[eid][k])[1] for k in ("p1", "p2")], 55.0, atol=1e-6)

    # an edge used from the real viewport is linked too: the shapes it shows are the ones the names are on
    def near_edge(i):
        return i["type"] == int(K.GeomAbs_Line) and abs(i["c"][2]) < 1e-6 and abs(abs(i["d"][0]) - 1) < 1e-6 \
            and abs(i["c"][1]) < 1e-6
    pid, ei = _edge_index(doc, near_edge)
    assert pid in w.viewport.part_shapes
    new = w.sketcher._use_edge(w.viewport, Pick("edge", (pid, ei)))
    assert sk.entities[new].get("src", {}).get("name")


def test_the_editor_cuts_a_lost_edge_loose_so_the_sketch_can_be_repaired(make_win):
    doc, _ids = plate_studio(W, D)
    fid, eid = _with_lost_edge(doc)
    assert doc.errors.get(fid)
    w = make_win(doc)
    w.start_sketch(fid)
    assert "src" not in w.sketcher.sk.entities[eid]
    assert "no longer on the part" in w.status.currentMessage()
    assert not doc.preview(w.sk_ctx["feature"], w.sk_ctx["index"])[1]
