"""Fillet failures say where and why (I8.4): the reason text, the failing edges carried on the error, the
pre-checks and the window's highlight. Route and inspection methods: scripts/spikes/op2_fillet.result.txt."""
import logging
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeWedge  # noqa: E402
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt  # noqa: E402

from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402

from conftest import TOP, add, block_doc, edge_refs, only_part, region_refs, try_add  # noqa: E402


def _top(i):
    return i["type"] == int(K.GeomAbs_Line) and abs(i["c"][2] - 10) < 1e-6


def _edges_at(shape, z, curved=None):
    out = []
    for e in K.edges(shape):
        i = K.edge_info(e)
        if abs(i["c"][2] - z) < 1e-6 and (curved is None or (i["type"] != int(K.GeomAbs_Line)) == curved):
            out.append(e)
    return out


def _is_subset(refs, edges):
    return all(any(np.linalg.norm(np.array(r["c"]) - np.array(K.edge_info(e)["c"])) < 1e-6 for e in edges)
               for r in refs)


def _cylinder_doc():
    doc = Document()
    s = Sketch()
    s.add_circle(s.add_point(0, 0), 10)
    s.solve()
    f = doc.make_feature("sketch")
    f["params"].update(plane=TOP, sketch=s.to_dict())
    doc.add_feature(f)
    add(doc, "extrude", regions=region_refs(doc, f["id"]), depth=20, op="new")
    return doc


# ------------------------------------------------------------------------------------------ reasons

def test_oversized_constant_fillet_still_says_the_largest_that_works():
    doc = block_doc(40, 30, 10)
    _, err = try_add(doc, "fillet", edges=edge_refs(only_part(doc), _top, expect=4), radius=15)
    assert err and "the largest that works here is about" in err, err


def test_concave_cylinder_names_the_edge_and_offers_a_smaller_radius_only_if_one_works():
    box = BRepPrimAPI_MakeBox(40, 30, 10).Shape()
    body = K.cut(box, K.cylinder((20, 15, 4), (0, 0, 1), 3, 10))      # a blind hole of radius 3
    floor = _edges_at(body, 4)
    assert len(floor) == 1
    with pytest.raises(K.KernelError) as ei:
        K.fillet(body, floor, 5.0)
    msg = str(ei.value)
    assert "the curved edge near (" in msg, msg                          # it names the edge
    works = K.fillet(body, floor, 2.0) is not None                       # 2 mm does build here
    assert works and "the largest that works here is about" in msg, msg
    assert len(ei.value.failed_edges) == 1
    # where nothing smaller works the message gives the reason and no radius
    why = {}
    err = K._fillet_failure(body, floor, why, False, "")
    assert "largest that works" not in str(err) and "too large" not in str(err)


def test_reason_texts_for_each_status():
    from OCP.ChFiDS import ChFiDS_StartsolFailure, ChFiDS_TwistedSurface, ChFiDS_WalkingFailure
    assert "does not fit at the start of this edge" in K._status_text(ChFiDS_StartsolFailure)
    assert "fold over itself" in K._status_text(ChFiDS_TwistedSurface)
    assert "could not be followed along this edge" in K._status_text(ChFiDS_WalkingFailure)


def test_oversized_constant_fillet_reports_the_start_failure_and_the_edge():
    box = BRepPrimAPI_MakeBox(40, 30, 10).Shape()
    with pytest.raises(K.KernelError) as ei:
        K.fillet(box, _edges_at(box, 10), 15.0)
    assert "does not fit at the start of this edge" in str(ei.value)
    assert ei.value.failed_edges


# ------------------------------------------------------------------------------------------ pre-checks

def test_seam_alone_gives_a_seam_message_naming_the_other_edges():
    doc = _cylinder_doc()
    part = only_part(doc)
    seam = [e for i, e in enumerate(K.edges(part.shape)) if i in K.seam_edge_indices(part.shape)]
    assert len(seam) == 1
    refs = [F.make_ref(part.id, seam[0], "edge", part.shape, doc.state.named(part.id))]
    _, err = try_add(doc, "fillet", edges=refs, radius=2)
    assert err and "seam" in err and "can be rounded are" in err and "curved edge near" in err, err


def test_face_pick_fillets_the_rims_and_warns_about_the_seam(caplog):
    doc = _cylinder_doc()
    part = only_part(doc)
    side = [f for f in K.faces(part.shape) if K.face_info(f)["type"] == int(K.GeomAbs_Cylinder)]
    assert len(side) == 1
    refs = [F.make_ref(part.id, e, "edge", part.shape, doc.state.named(part.id)) for e in K.edges(side[0])]
    assert len(refs) == 3                                              # two rims and the seam
    v0 = K.volume(part.shape)
    with caplog.at_level(logging.WARNING):
        add(doc, "fillet", edges=refs, radius=2)
    warns = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("seam" in w for w in warns), warns
    assert K.volume(only_part(doc).shape) < v0
    assert len(K.faces(only_part(doc).shape)) == 5                    # side, two ends and two rounds


def test_smooth_edge_is_skipped_with_a_warning():
    doc = block_doc(40, 30, 10)
    add(doc, "fillet", edges=edge_refs(only_part(doc), _top, expect=4), radius=2)
    part = only_part(doc)
    smooth = [e for e in K.edges(part.shape) if len(K.edge_faces(part.shape, e)) == 2 and
              K.fillet_prechecks(part.shape, [e, _edges_at(part.shape, 0)[0]])[1]
              and "already smooth" in " ".join(K.fillet_prechecks(part.shape, [e, _edges_at(part.shape, 0)[0]])[1])]
    assert smooth, "the rounded block has smooth edges"
    keep, warns = K.fillet_prechecks(part.shape, [smooth[0], _edges_at(part.shape, 0)[0]])
    assert len(keep) == 1 and any("already smooth" in w for w in warns)
    with pytest.raises(K.KernelError, match="already smooth"):
        K.fillet_prechecks(part.shape, [smooth[0]])


def _pyramid():
    w = BRepPrimAPI_MakeWedge(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 20, 20, 20, 10, 10, 10, 10).Shape()
    return w, [e for e in K.edges(w) if abs(K.edge_info(e)["len"] - 24.4949) < 1e-3]


def test_four_edges_at_one_vertex_name_the_vertex():
    w, apex = _pyramid()
    assert len(apex) == 4
    keep, warns = K.fillet_prechecks(w, apex[:3])           # a warning, never a rejection
    assert keep == apex[:3] and any("edges meet at the vertex" in x for x in warns)
    with pytest.raises(K.KernelError) as ei:
        K.fillet(w, apex[:3], 100.0)
    msg = str(ei.value)
    m = re.search(r"4 edges meet at the vertex \(([\d.]+), ([\d.]+), ([\d.]+)\); OpenCascade cannot end a round "
                  r"where four or more edges meet", msg)
    assert m, msg
    assert (10, 20, 10) == tuple(round(float(x)) for x in m.groups())


# ------------------------------------------------------------------------------------------ references

def test_error_carries_failing_edge_references_subset_of_selected_or_propagated():
    doc = block_doc(40, 30, 10)
    sel = edge_refs(only_part(doc), _top, expect=4)
    f, err = try_add(doc, "fillet", edges=sel, radius=15)
    assert err
    refs = doc.error_refs[f["id"]]
    part = only_part(doc)
    assert refs and all(r["kind"] == "edge" and r["part"] == part.id for r in refs)
    assert _is_subset(refs, K.edges(part.shape))
    assert _is_subset(refs, [e for e in K.edges(part.shape) if _top(K.edge_info(e))])


def test_error_refs_hold_the_corner_vertex():
    doc = block_doc(40, 30, 10)
    st = doc.state
    part = only_part(st)
    err = K.KernelError("x")
    err.failed_vertices = [(0.0, 0.0, 0.0)]
    F._attach_failure_refs(err, st, part.id, part)
    assert err.refs[0]["kind"] == "vertex" and err.refs[0]["c"] == [0.0, 0.0, 0.0]


def test_a_diagnostics_error_does_not_hide_the_real_one(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("diagnostics broke")
    monkeypatch.setattr(K, "_diagnose_blend", boom)
    monkeypatch.setattr(K, "_crowded_corners", boom)
    box = BRepPrimAPI_MakeBox(40, 30, 10).Shape()
    with pytest.raises(K.KernelError, match="the largest that works here is about"):
        K.fillet(box, _edges_at(box, 10), 15.0)


def test_diagnostics_skip_a_contour_index_out_of_range():
    class Mk:
        def NbContours(self): return 1
        def NbFaultyContours(self): return 1
        def FaultyContour(self, i): return 7
        def NbEdges(self, ic): raise AssertionError("must not be called")
        def NbFaultyVertices(self): return 0
        def HasResult(self): return False
    d = K._diagnose_blend(Mk())
    assert d["edges"] == [] and d["reasons"] == []


def test_a_successful_build_runs_no_diagnostics(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("diagnostics ran on a success")
    monkeypatch.setattr(K, "_diagnose_blend", boom)
    box = BRepPrimAPI_MakeBox(40, 30, 10).Shape()
    assert K.fillet(box, _edges_at(box, 10), 2.0) is not None


# ------------------------------------------------------------------------------------------ the window

@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    w.resize(900, 700)
    w.show()
    app.processEvents()
    yield w
    w.deleteLater()


def test_window_highlight_list_holds_the_failing_edges_until_fixed(win):
    doc = block_doc(40, 30, 10)
    f, err = try_add(doc, "fillet", edges=edge_refs(only_part(doc), _top, expect=4), radius=15)
    assert err
    win._add_tab(doc)
    win._on_doc_changed()
    refs = win.error_hl_refs()
    assert refs and _is_subset(refs, K.edges(only_part(doc).shape))
    assert win.viewport.feature_hl and win.viewport.feature_hl["edges"]
    fixed = dict(f)
    fixed["params"] = dict(f["params"], radius=2)
    doc.replace_feature(fixed)
    assert not doc.errors
    win._on_doc_changed()
    assert win.error_hl_refs() == []
    assert not (win.viewport.feature_hl or {}).get("edges")


# ------------------------------------------------------------------------------------------ nothing new rejected

def test_golden_and_naming_fillets_build_without_warning_or_rejection(monkeypatch, caplog):
    import test_golden_features as G
    import test_naming_blends as NB
    seen = []
    real = K.fillet_prechecks

    def spy(shape, es):
        keep, warns = real(shape, es)
        seen.append((len(es), len(keep), warns))
        return keep, warns
    monkeypatch.setattr(K, "fillet_prechecks", spy)
    with caplog.at_level(logging.WARNING):
        for build in (G.fillet_vertical_edges, G.fillet_top_loop, G.fillet_all_edges, G.delete_face_heal_fillets):
            build()
        doc, _ = NB.block()
        add(doc, "fillet", edges=edge_refs(only_part(doc), NB.top_edges, expect=4), radius=2.0)
    assert len(seen) >= 5
    assert all(n == k and not w for n, k, w in seen), seen
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING and "fillet" in r.getMessage()]


def test_error_refs_cross_the_engine_wire():
    from freeshape.engine import wire
    from freeshape.engine.contract import StudioOutputs, plain
    doc = block_doc(40, 30, 10)
    f, err = try_add(doc, "fillet", edges=edge_refs(only_part(doc), _top, expect=4), radius=15)
    assert err and type(doc.errors[f["id"]]) is str
    out = StudioOutputs(tab="t", kind="studio", name="n", revision=1, errors=plain(dict(doc.errors)), consumes=(),
                        error_refs=plain(dict(doc.error_refs)))
    back = wire.from_wire(wire.to_wire(out))
    assert back.error_refs[f["id"]] and back.error_refs[f["id"]][0]["kind"] == "edge"
