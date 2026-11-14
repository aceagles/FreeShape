"""The Component library: the index of a folder of STEP files (step_library), the thumbnails drawn for it
(step_thumb_worker, ThumbService) and the browser dialog (StepLibraryDialog)."""
import json
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import ui_helpers  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import step_library as L  # noqa: E402
from freeshape.ui import preview as PV  # noqa: E402
from freeshape.ui import step_library_ui as UI  # noqa: E402


def _app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def lib(tmp_path, monkeypatch):
    """A small library: a file two folders deep, a catalogued folder with a parts.json, and a broken file.
    Thumbnails go to a temporary folder, not the user's."""
    thumbs = tmp_path / "thumbs"
    thumbs.mkdir()
    monkeypatch.setattr("freeshape.ui.home.thumbs_dir", lambda: str(thumbs))
    root = tmp_path / "lib"
    (root / "motors" / "steppers").mkdir(parents=True)
    (root / "cat").mkdir()
    cyl = K.cylinder((0, 0, 0), (0, 0, 1), 5, 20)
    K.export_step([cyl], str(root / "motors" / "steppers" / "NEMA-17_Stepper.step"))
    K.export_step([cyl], str(root / "cat" / "sg90_micro_servo.step"))
    K.export_step([cyl], str(root / "cat" / "bearing_608zz.stp"))
    (root / "cat" / "broken.step").write_text("this is not a STEP file")
    (root / "cat" / "parts.json").write_text(json.dumps([
        {"id": "sg90_micro_servo", "name": "SG90 micro servo", "description": "9 g hobby servo.",
         "tags": ["servo", "9g"], "aliases": ["Tower Pro"], "category": "actuator"}]))
    (root / "notes.txt").write_text("not a part")
    return root


def _names(entries):
    return [e.name for e in entries]


# ------------------------------------------------------------------ the index
def test_scan_lists_step_files_only(lib):
    entries = L.scan(str(lib))
    assert sorted(e.rel for e in entries) == ["cat/bearing_608zz.stp", "cat/broken.step",
                                              "cat/sg90_micro_servo.step", "motors/steppers/NEMA-17_Stepper.step"]
    assert all(e.size > 0 and os.path.isabs(e.path) for e in entries)
    assert L.groups(entries) == ["cat", "motors"]


def test_scan_of_a_missing_folder_is_empty(tmp_path):
    assert L.scan(str(tmp_path / "nowhere")) == []


def test_catalogue_names_its_files_and_the_rest_use_the_filename(lib):
    by_rel = {e.rel: e for e in L.scan(str(lib))}
    servo = by_rel["cat/sg90_micro_servo.step"]
    assert servo.name == "SG90 micro servo"
    assert servo.description == "9 g hobby servo."
    assert servo.tags == ("servo", "9g")
    assert by_rel["cat/bearing_608zz.stp"].name == "bearing 608zz"
    assert by_rel["motors/steppers/NEMA-17_Stepper.step"].name == "NEMA 17 Stepper"


def test_a_broken_catalogue_is_ignored(lib):
    (lib / "cat" / "parts.json").write_text("{not json")
    assert "sg90 micro servo" in _names(L.scan(str(lib)))


def test_search_needs_every_word_and_reads_tags_aliases_and_folders(lib):
    entries = L.scan(str(lib))
    assert _names(L.search(entries, "nema 17")) == ["NEMA 17 Stepper"]
    assert _names(L.search(entries, "SERVO 9g")) == ["SG90 micro servo"]
    assert _names(L.search(entries, "tower")) == ["SG90 micro servo"]          # an alias
    assert _names(L.search(entries, "actuator")) == ["SG90 micro servo"]       # its category
    assert _names(L.search(entries, "steppers")) == ["NEMA 17 Stepper"]        # a folder
    assert L.search(entries, "servo nema") == []
    assert len(L.search(entries, "")) == 4
    assert _names(L.search(entries, "", group="motors")) == ["NEMA 17 Stepper"]


def test_a_match_in_the_name_comes_before_a_match_elsewhere(lib):
    (lib / "cat" / "parts.json").write_text(json.dumps([
        {"id": "sg90_micro_servo", "name": "SG90 micro servo", "tags": ["bearing"]}]))
    assert _names(L.search(L.scan(str(lib)), "bearing")) == ["bearing 608zz", "SG90 micro servo"]


# ------------------------------------------------------------------ thumbnails
def _drawn_pixels(path):
    img = QImage(path)
    assert (img.width(), img.height()) == (UI.THUMB_PX, UI.THUMB_PX)
    bg = PV.BACKGROUND.rgb()
    return sum(img.pixel(x, y) != bg for x in range(0, img.width(), 8) for y in range(0, img.height(), 8))


def test_render_draws_the_part(lib, tmp_path):
    _app()
    from freeshape.step_thumb_worker import render
    out = str(tmp_path / "one.png")
    render(str(lib / "cat" / "bearing_608zz.stp"), out, UI.THUMB_PX)
    assert _drawn_pixels(out) > 50


def test_render_solids_colours_each_triangle():
    _app()
    import numpy as np
    tris = np.array([[[0, 0, 0], [10, 0, 0], [0, 10, 0]], [[20, 0, 0], [30, 0, 0], [20, 10, 0]]], float)
    img = PV.render_solids(tris, np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]), 64)
    seen = {(c.red() > c.blue(), c.blue() > c.red()) for c in
            (img.pixelColor(x, y) for x in range(64) for y in range(64)) if c != PV.BACKGROUND}
    assert (True, False) in seen and (False, True) in seen


def test_a_file_of_wires_is_drawn_as_lines(tmp_path):
    _app()
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.gp import gp_Pnt
    from freeshape.step_thumb_worker import render
    src, out = str(tmp_path / "rod.step"), str(tmp_path / "rod.png")
    K.export_step([BRepBuilderAPI_MakeEdge(gp_Pnt(0, 0, 0), gp_Pnt(100, 0, 0)).Edge()], src)
    render(src, out, UI.THUMB_PX)
    assert _drawn_pixels(out) > 10


def test_a_black_part_keeps_its_shading(tmp_path):
    _app()
    import numpy as np
    from freeshape.step_thumb_worker import DARKEST, geometry
    src = str(tmp_path / "black.step")
    K.export_step_named([(K.cylinder((0, 0, 0), (0, 0, 1), 5, 20), np.eye(4), "black", (0.0, 0.0, 0.0))], src)
    _tris, cols, _lines = geometry(src)
    assert len(cols) and float(np.min(cols)) >= DARKEST


def test_thumbnail_file_changes_with_the_file(lib):
    p = str(lib / "cat" / "bearing_608zz.stp")
    before = UI.thumb_file(p)
    with open(p, "ab") as fh:
        fh.write(b"\n")
    assert UI.thumb_file(p) != before


def test_service_draws_in_a_background_process(lib, monkeypatch):
    app = _app()
    monkeypatch.delenv("FREESHAPE_NO_STEP_WORKER", raising=False)
    good, bad = str(lib / "cat" / "bearing_608zz.stp"), str(lib / "cat" / "broken.step")
    svc = UI.ThumbService(workers=1)
    done = []
    svc.ready.connect(done.append)
    try:
        svc.request(good)
        svc.request(bad)
        t0 = time.time()
        while len(done) < 2 and time.time() - t0 < 120:
            app.processEvents()
            time.sleep(0.02)
    finally:
        svc.shutdown()
    assert sorted(done) == sorted([good, bad])
    assert svc.cached(good) and _drawn_pixels(svc.cached(good)) > 50
    assert svc.failed(bad) and not svc.cached(bad)
    assert svc.stats == {"drawn": 1, "failed": 1}


# ------------------------------------------------------------------ the dialog
@pytest.fixture
def dialog(lib, monkeypatch):
    _app()
    monkeypatch.setenv("FREESHAPE_NO_STEP_WORKER", "1")
    calls = []
    d = UI.StepLibraryDialog(ui_helpers.Settings(), lambda path, into: calls.append((path, into)), folder=str(lib))
    d.calls = calls
    yield d
    d.close()


def test_dialog_lists_the_library_and_filters_as_you_search(dialog):
    assert dialog.model.rowCount() == 4
    assert dialog.count.text() == "4 of 4"
    dialog.search.setText("servo")
    dialog.refilter()
    assert [dialog.model.entry(dialog.model.index(i)).name for i in range(dialog.model.rowCount())] == [
        "SG90 micro servo"]
    assert dialog.title.text() == "SG90 micro servo"
    assert "9 g hobby servo." in dialog.info.text() and "Tags: servo, 9g" in dialog.info.text()
    dialog.search.setText("no such part")
    dialog.refilter()
    assert dialog.model.rowCount() == 0 and not dialog.insert_btn.isEnabled()
    assert dialog.info.text() == "Nothing matches."


def test_dialog_folder_filter(dialog):
    dialog.group.setCurrentIndex(dialog.group.findData("motors"))
    assert dialog.model.rowCount() == 1 and dialog.current().name == "NEMA 17 Stepper"


def test_tiles_get_thumbnails_only_when_asked_and_a_broken_file_keeps_the_placeholder(dialog):
    m, svc = dialog.model, dialog.service
    svc.pending.clear()
    placeholder = UI.placeholder_icon(UI.TILE_PX).cacheKey()
    rows = {m.entry(m.index(i)).rel: m.index(i) for i in range(m.rowCount())}
    good, bad = rows["cat/bearing_608zz.stp"], rows["cat/broken.step"]
    assert m.data(good, Qt.DecorationRole).cacheKey() == placeholder
    assert m.data(bad, Qt.DecorationRole).cacheKey() == placeholder
    assert len(svc.pending) == 2                      # only the two tiles that were painted
    changed = []
    m.dataChanged.connect(lambda a, _b, _r: changed.append(a.row()))
    svc.drain()
    assert sorted(changed) == sorted([good.row(), bad.row()])
    assert m.data(good, Qt.DecorationRole).cacheKey() != placeholder
    assert m.data(bad, Qt.DecorationRole).cacheKey() == placeholder
    assert svc.pending == []                          # the broken file is not queued again


def test_insert_and_open_in_new_tab_pass_the_chosen_file(dialog, lib):
    dialog.search.setText("nema")
    dialog.refilter()
    path = str(lib / "motors" / "steppers" / "NEMA-17_Stepper.step")
    dialog.insert_btn.click()
    dialog.new_tab_btn.click()
    dialog.view.doubleClicked.emit(dialog.view.currentIndex())
    assert dialog.calls == [(path, True), (path, False), (path, True)]


def test_change_of_folder_is_remembered(lib, tmp_path, monkeypatch):
    _app()
    monkeypatch.setenv("FREESHAPE_NO_STEP_WORKER", "1")
    monkeypatch.delenv("FREESHAPE_STEP_LIBRARY", raising=False)
    monkeypatch.setattr(UI, "DEFAULT_FOLDER", str(tmp_path / "absent"))
    settings = ui_helpers.Settings()
    assert UI.library_folder(settings) == ""
    d = UI.StepLibraryDialog(settings, lambda *_a: None)
    assert d.model.rowCount() == 0 and "No library folder" in d.folder_label.text()
    monkeypatch.setattr(UI.QFileDialog, "getExistingDirectory", lambda *_a, **_k: str(lib))
    d.choose_folder()
    assert d.model.rowCount() == 4 and UI.library_folder(settings) == str(lib)
    d.close()


# ------------------------------------------------------------------ in the main window
def test_main_window_inserts_a_library_part_into_the_assembly(lib, tmp_path, monkeypatch):
    monkeypatch.setenv("FREESHAPE_NO_STEP_WORKER", "1")
    w = ui_helpers.plate_assembly_window(tmp_path)
    try:
        before = len(w.doc.parts)
        w._insert_library_part(str(lib / "cat" / "bearing_608zz.stp"), True)
        assert len(w.doc.parts) == before + 1
    finally:
        ui_helpers.close_window(w)
