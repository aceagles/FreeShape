"""I5.2: what a feature dialog does with its starting picks (apply_preselection) and when a field changes
(on_field_set), in the Qt-free `freeshape.dialog_schema`. The desktop dialog calls both; the parity tests compare
them with a FeatureDialog opened on the same picks. Each test builds its own window and closes it."""
import os
import subprocess
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

from freeshape import features as F  # noqa: E402
from freeshape.dialog_schema import (  # noqa: E402
    apply_preselection, on_field_set, params_to_fields, _schema_for)
from test_naming_blends import block  # noqa: E402


def server_state(state):
    """What the server's session state offers apply_preselection (no viewport)."""
    return SimpleNamespace(part_shapes={pid: p.shape for pid, p in state.parts.items()}, sketches=state.sketches,
                           parts=state.parts, named=state.named, engine_names=getattr(state, "engine_names", None))


def desktop_fields(tmp_path, monkeypatch, ftype, picks_of, make_doc=None):
    """Open `ftype` in an offscreen window on a block with `picks_of(doc)` selected; return what the dialog holds."""
    monkeypatch.setenv("FREESHAPE_NO_RECOVERY", "1")
    monkeypatch.setenv("FREESHAPE_NO_HOME", "1")
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    from PySide6.QtWidgets import QApplication
    from freeshape.ui.mainwindow import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    try:
        doc = make_doc() if make_doc else block()[0]
        w._add_tab(doc)
        w.resize(900, 700)
        w.show()
        app.processEvents()
        picks = picks_of(doc)
        w.start_feature(ftype, preselect=picks)
        d = w.dialog
        assert w.mode == "dialog" and d.ftype == ftype
        got = {k: list(f.items) for k, f in d.fields.items()}
        feature_params = dict(d.feature["params"])
        feat = doc.make_feature(ftype)
        base = doc.state_before(d.index)
        w.cancel_dialog()
        return doc, feat, base, picks, got, feature_params
    finally:
        w.deleteLater()
        app.processEvents()


def region_pick(doc):
    from freeshape.ui.viewport import Pick
    return Pick("region", (doc.features[0]["id"], 0))


def face_picks(doc, n=2):
    from freeshape.ui.viewport import Pick
    pid = next(iter(doc.state.parts))
    return [Pick("face", (pid, i)) for i in range(n)]


def edge_picks(doc, n=2):
    from freeshape.ui.viewport import Pick
    pid = next(iter(doc.state.parts))
    return [Pick("edge", (pid, i)) for i in range(n)]


def server_fields(feat, base, picks):
    ftype = feat["type"]
    start = params_to_fields(ftype, feat["params"])
    sels = {s["key"] for s in _schema_for(ftype, feat["params"]) if s["t"] == "sel"}
    start = {k: v for k, v in start.items() if k in sels}
    for k in sels:
        start.setdefault(k, [])
    return apply_preselection(ftype, start, picks, server_state(base), feat["params"])


def parity(tmp_path, monkeypatch, ftype, picks_of):
    doc, feat, base, picks, got, _ = desktop_fields(tmp_path, monkeypatch, ftype, picks_of)
    res = server_fields(feat, base, picks)
    assert any(got.values()), "the picks filled nothing: the test would compare empty fields"
    assert res.fields == got
    return res


def test_preselection_extrude_matches_the_dialog(tmp_path, monkeypatch):
    res = parity(tmp_path, monkeypatch, "extrude", lambda d: [region_pick(d)])
    assert res.fields["profiles"][0][0] == "region"


def test_preselection_fillet_matches_the_dialog(tmp_path, monkeypatch):
    res = parity(tmp_path, monkeypatch, "fillet", lambda d: edge_picks(d, 2))
    assert len(res.fields["edges"]) == 2


def test_preselection_fillet_face_gives_all_its_edges(tmp_path, monkeypatch):
    res = parity(tmp_path, monkeypatch, "fillet", lambda d: face_picks(d, 1))
    assert len(res.fields["edges"]) == 4


def test_preselection_mirror_matches_the_dialog(tmp_path, monkeypatch):
    parity(tmp_path, monkeypatch, "mirror", lambda d: face_picks(d, 2))


def test_preselection_linear_pattern_matches_the_dialog(tmp_path, monkeypatch):
    from freeshape.ui.viewport import Pick
    res = parity(tmp_path, monkeypatch, "linear_pattern",
                 lambda d: [Pick("part", (next(iter(d.state.parts)),))] + edge_picks(d, 1))
    assert res.fields["parts"] and res.fields["dir_sel"]


def test_preselection_mate_connector_matches_the_dialog_and_stamps(tmp_path, monkeypatch):
    doc, feat, base, picks, got, params = desktop_fields(tmp_path, monkeypatch, "mate_connector",
                                                         lambda d: face_picks(d, 1))
    res = server_fields(feat, base, picks)
    assert res.fields == got and any(got.values())
    assert res.params["x_ref"] is not None and res.params["click"] is None and res.params["snap"] is None
    assert res.params["x_ref"] == params["x_ref"]


def circle_doc():
    """A Document of two sketches: a rectangle with no circle or point, then a sketch with a circle."""
    from conftest import sketch_feature, rect_lines
    from freeshape.document import Document
    doc = Document()
    sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 40, 20))
    sketch_feature(doc, lambda s: s.add_circle(s.add_point(10, 10), 3))
    return doc


def test_preselection_sketch_pick_chooses_the_sketch_of_a_hole(tmp_path, monkeypatch):
    """A click on a sketch's region chooses it for a Hole only if it has a circle or a point."""
    from freeshape.ui.viewport import Pick
    circle_sid = lambda d: d.features[1]["id"]
    doc, feat, base, picks, got, params = desktop_fields(
        tmp_path, monkeypatch, "hole", lambda d: [Pick("region", (circle_sid(d), 0))], circle_doc)
    res = server_fields(feat, base, picks)
    assert res.params == {"sketch": circle_sid(doc)} and params["sketch"] == circle_sid(doc)
    assert res.fields == got
    rect = [Pick("region", (doc.features[0]["id"], 0))]
    assert server_fields(feat, base, rect).params == {}


def test_preselection_does_not_change_its_arguments():
    doc, _ = block()
    from freeshape.ui.viewport import Pick
    fields = {"profiles": [], "curves": []}
    before = {k: list(v) for k, v in fields.items()}
    res = apply_preselection("extrude", fields, [Pick("region", (doc.features[0]["id"], 0))], server_state(doc.state),
                             F.default_params("extrude"))
    assert fields == before and res.fields["profiles"]


def test_preselection_takes_plain_pairs():
    doc, _ = block()
    state = server_state(doc.state)
    res = apply_preselection("extrude", {"profiles": []}, [("region", (doc.features[0]["id"], 0))], state,
                             F.default_params("extrude"))
    assert res.fields["profiles"] and res.fields["profiles"][0][0] == "region"


# ---------------------------------------------------------------------------------------------- on_field_set

def fields_of(ftype, params=None):
    p = params if params is not None else F.default_params(ftype)
    out = {s["key"]: [] for s in _schema_for(ftype, p) if s["t"] == "sel"}
    return out, p


def test_end_up_to_face_activates_its_pick_field():
    fields, p = fields_of("extrude")
    got = on_field_set("extrude", "end", "up_to_face", fields, p)
    assert got.active == "upto_face" and got.params["end"] == "up_to_face"
    assert on_field_set("extrude", "end", "up_to_vertex", fields, p).active == "upto_vertex"
    assert on_field_set("extrude", "end", "up_to_part", fields, p).active == "upto_part"
    assert on_field_set("extrude", "end", "blind", fields, p).active is None
    assert "end" not in p or p["end"] != "up_to_face"          # the argument is not changed


def test_world_axis_clears_a_picked_axis():
    fields, p = fields_of("revolve")
    assert "axis_sel" in fields
    fields["axis_sel"] = [("axis", {"kind": "face", "face": {"part": "p", "sig": 1}})]
    got = on_field_set("revolve", "axis_std", "y", fields, p)
    assert got.fields["axis_sel"] == [] and fields["axis_sel"]
    assert got.params["axis_std"] == "y"


@pytest.mark.parametrize("ftype", ["mirror", "linear_pattern", "circular_pattern"])
def test_pattern_type_swaps_features_and_parts_fields(ftype):
    fields, p = fields_of(ftype)
    assert {"features", "parts"} <= set(fields)
    assert on_field_set(ftype, "type", "features", fields, p).active == "features"
    assert on_field_set(ftype, "type", "parts", fields, p).active == "parts"


def test_thin_seeds_thickness_and_activates_curves():
    fields, p = fields_of("extrude")
    got = on_field_set("extrude", "creation", "thin", fields, p)
    assert got.active == "curves" and got.params["thickness1"] == 1.0 and got.params["thickness2"] == 0.0


def test_surface_limits_the_operation_and_draft_seeds_its_angle():
    fields, p = fields_of("extrude")
    p = dict(p, op="remove")
    assert on_field_set("extrude", "creation", "surface", fields, p).params["op"] == "add"
    assert on_field_set("extrude", "draft", True, fields, p).params["draft_angle"] == 5.0


def test_dialog_set_calls_on_field_set(tmp_path, monkeypatch):
    """The desktop dialog follows on_field_set: Thin seeds the wall rows, a world axis clears the picked axis."""
    monkeypatch.setenv("FREESHAPE_NO_RECOVERY", "1")
    monkeypatch.setenv("FREESHAPE_NO_HOME", "1")
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    from PySide6.QtWidgets import QApplication
    from freeshape.ui.mainwindow import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    try:
        doc, _ = block()
        w._add_tab(doc)
        w.show()
        app.processEvents()
        w.start_feature("extrude", preselect=[])
        d = w.dialog
        d._set("creation", "thin")
        app.processEvents()
        assert d.feature["params"]["thickness1"] == 1.0
        assert d.widgets["thickness1"].text() != ""
        d._set("end", "up_to_face")
        app.processEvents()
        assert d.active_field is d.fields["upto_face"]
        w.cancel_dialog()
    finally:
        w.deleteLater()
        app.processEvents()


def test_dialog_schema_still_imports_no_qt():
    code = ("import sys, freeshape.dialog_schema; "
            "bad = [m for m in sys.modules if m.split('.')[0] in ('PySide6', 'PyQt5', 'PyQt6', 'shiboken6')]; "
            "print('QT:' + ','.join(bad))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                         cwd=os.path.join(os.path.dirname(__file__), ".."))
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("QT:"), out.stdout


def test_preselection_in_an_engine_window_matches_the_dialog(tmp_path, monkeypatch):
    """The real desktop window runs MainWindow(engine=True): a Fillet started on two picked edges holds the same
    edge references, as apply_preselection gives for the same picks."""
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QMessageBox
    from conftest import add, rect_lines, region_refs, sketch_feature
    from freeshape import doc_folder as DF
    from freeshape import doc_registry as R
    from freeshape.engine import process as P
    from freeshape.ui import home as H
    from freeshape.ui import mainwindow as MW
    from freeshape.ui.viewport import Pick
    monkeypatch.setenv("FREESHAPE_NO_RECOVERY", "1")
    monkeypatch.setenv("FREESHAPE_NO_HOME", "1")
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    for d in ("autosave", "thumbs"):
        os.makedirs(tmp_path / d, exist_ok=True)
    monkeypatch.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(tmp_path / "autosave")))
    monkeypatch.setattr(MW.MainWindow, "autosave_dir", classmethod(lambda cls: str(tmp_path / "autosave")))
    monkeypatch.setattr(H, "thumbs_dir", lambda: str(tmp_path / "thumbs"))
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.Ok)
    fd = DF.FolderDocument.create(str(tmp_path / "Doc"), registry=R.default_registry())
    tab = fd.add_tab("studio", "Plate")
    sid = sketch_feature(tab.doc, lambda s: rect_lines(s, 0, 0, 40, 20))
    add(tab.doc, "extrude", regions=region_refs(tab.doc, sid), depth=10, op="new")
    fd.save()
    app = QApplication.instance() or QApplication([])
    w = MW.MainWindow(engine=True)
    try:
        w.settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
        w.open_document(fd.path)
        assert w.studio_engine.settle(240)
        doc = w.folder.tab(tab.id).doc
        if w._tab_index(doc) < 0:
            w._add_tab(doc)
        w.tabs.setCurrentIndex(w._tab_index(doc))
        app.processEvents()
        assert w.doc is doc
        pid = next(iter(w.viewport.part_shapes))
        picks = [Pick("edge", (pid, 0)), Pick("edge", (pid, 1))]
        w.start_feature("fillet", preselect=picks)
        d = w.dialog
        assert w.mode == "dialog" and d.ftype == "fillet"
        got = {k: list(f.items) for k, f in d.fields.items()}
        state = SimpleNamespace(part_shapes=dict(w.viewport.part_shapes), sketches={}, parts=d.base_state.parts,
                                named=d.base_state.named, engine_names=getattr(d.base_state, "engine_names", None))
        res = apply_preselection("fillet", {"edges": []}, picks, state, F.default_params("fillet"))
        assert len(got["edges"]) == 2 and res.fields["edges"] == got["edges"]
        w.cancel_dialog()
    finally:
        w.studio_engine.shutdown()
        P.SPARES.clear()
        w.deleteLater()
        app.processEvents()
