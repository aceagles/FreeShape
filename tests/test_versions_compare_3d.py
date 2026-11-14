"""I4.4: the 3D added-and-removed comparison between two Versions: history.compare_geometry (the material added and
removed in a Part Studio tab) and the Compare Versions panel's "Show in 3D" toggle, which builds it off the GUI
thread and hands the Viewport two ghost sets (added green, removed red). Headless (offscreen Qt)."""
import threading
import time

import pytest

from conftest import add, rect_lines, region_refs, sketch_feature
from freeshape import history as H
from freeshape import kernel as K
from test_versions_compare import _version, window  # noqa: F401  (the window fixture)

from PySide6.QtWidgets import QApplication  # noqa: E402

HOLE = 6 * 6 * 20.0          # a 6 x 6 mm hole through a 20 mm block


def _block_and_holed(w, tmp_path):
    """A Document whose Part Studio is a 20 mm block at "solid" and has a 6 x 6 mm hole through it at "holed"."""
    path = str(tmp_path / "Compare3D")
    w._pick_new_document_path = lambda: path
    w.home.newDocument.emit()
    fd = w.folder
    studio = fd.tabs[0].doc
    sid = sketch_feature(studio, lambda s: rect_lines(s, 0, 0, 20, 20))
    add(studio, "extrude", regions=region_refs(studio, sid), depth=20.0, op="new")
    _version(w, "solid")
    sid = sketch_feature(studio, lambda s: rect_lines(s, 7, 7, 6, 6))
    add(studio, "extrude", regions=region_refs(studio, sid), depth=20.0, op="remove")
    assert not studio.errors, studio.errors
    _version(w, "holed")
    return fd, studio


def _volume(shapes):
    return sum(K.volume(s) for s in shapes)


def _wait(panel, pred, seconds=60):
    end = time.time() + seconds
    while time.time() < end and not pred():
        QApplication.processEvents()
        time.sleep(0.01)
    assert pred()


def _compared(panel):
    """Let the panel's own (deferred) comparison of the selected pair finish."""
    panel.result = None
    _wait(panel, lambda: panel.result is not None)


def _built(panel):
    return panel._future is None and not panel._poll.isActive()


def test_hole_is_removed_material_and_nothing_is_added(window, tmp_path):
    fd, studio = _block_and_holed(window, tmp_path)
    geo = H.compare_geometry(fd, fd.tabs[0].id, "solid", "holed")
    assert geo["added"] == [] and geo["meshes"]["added"] == []
    assert _volume(geo["removed"]) == pytest.approx(HOLE, rel=1e-6)
    assert len(geo["meshes"]["removed"]) == len(geo["removed"]) and len(geo["meshes"]["removed"][0].tris) > 0


def test_reverse_comparison_reports_the_same_volume_as_added(window, tmp_path):
    fd, studio = _block_and_holed(window, tmp_path)
    geo = H.compare_geometry(fd, fd.tabs[0].id, "holed", "solid")
    assert geo["removed"] == []
    assert _volume(geo["added"]) == pytest.approx(HOLE, rel=1e-6)


def test_now_side_uses_the_open_tab_including_unsaved_edits(window, tmp_path):
    fd, studio = _block_and_holed(window, tmp_path)
    sid = sketch_feature(studio, lambda s: rect_lines(s, 1, 1, 2, 2))
    add(studio, "extrude", regions=region_refs(studio, sid), depth=20.0, op="remove")
    geo = H.compare_geometry(fd, fd.tabs[0].id, "holed", H.NOW)
    assert geo["added"] == [] and _volume(geo["removed"]) == pytest.approx(2 * 2 * 20.0, rel=1e-6)


def test_panel_hands_the_viewport_added_and_removed_ghosts_and_clears_them_on_close(window, tmp_path):
    w = window
    fd, studio = _block_and_holed(w, tmp_path)
    vp = w.viewport
    panel = w.versions_ui.compare(["solid", "holed"])
    assert panel.pair == ("solid", "holed") and panel.show3d.isChecked() is False
    _compared(panel)
    assert vp.ghosts == []                       # nothing until the toggle is on
    panel.show3d.setChecked(True)
    assert "Building" in panel.status.text()     # the busy state while it builds
    _wait(panel, lambda: _built(panel))
    assert panel.ghost_sets["added"] == []
    assert len(panel.ghost_sets["removed"]) == 1
    (mesh, rgba), = vp.ghosts
    assert (mesh, rgba) == panel.ghost_sets["removed"][0]
    assert rgba == H.GHOST_COLORS["removed"] and len(mesh.tris) > 0
    assert "1 removed" in panel.status.text()

    # holed against now, after the hole feature was deleted in the open tab (unsaved): the hole is added
    panel.fill(["holed", H.NOW])
    _compared(panel)
    _wait(panel, lambda: _built(panel))
    assert panel.ghost_sets["added"] == [] and len(panel.ghost_sets["removed"]) == 0      # no change yet
    studio.delete_features([studio.features[-1]["id"], studio.features[-2]["id"]])
    _wait(panel, lambda: _built(panel) and panel.ghost_sets["added"])
    assert panel.ghost_sets["removed"] == []
    (mesh, rgba), = vp.ghosts
    assert rgba == H.GHOST_COLORS["added"] and H.GHOST_COLORS["added"] != H.GHOST_COLORS["removed"]

    # unchecking the toggle takes them off; closing the panel does too
    panel.show3d.setChecked(False)
    assert vp.ghosts == [] and panel.ghost_sets == {"added": [], "removed": []}
    panel.show3d.setChecked(True)
    _wait(panel, lambda: _built(panel) and vp.ghosts)
    panel.close_panel()
    assert vp.ghosts == [] and w.versions_ui.compare_panel is None


def test_the_build_runs_off_the_gui_thread(window, tmp_path, monkeypatch):
    w = window
    fd, studio = _block_and_holed(w, tmp_path)
    seen = []
    real = H.compare_geometry

    def spy(*a, **k):
        seen.append(threading.current_thread())
        return real(*a, **k)
    monkeypatch.setattr(H, "compare_geometry", spy)
    panel = w.versions_ui.compare(["solid", "holed"])
    _compared(panel)
    panel.show3d.setChecked(True)
    _wait(panel, lambda: _built(panel))
    assert len(seen) == 1
    assert seen[0] is not threading.main_thread() and seen[0].name.startswith("compare3d")
    assert w.viewport.ghosts
    panel.close_panel()


def test_closing_the_panel_mid_build_drops_the_result(window, tmp_path, monkeypatch):
    w = window
    fd, studio = _block_and_holed(w, tmp_path)
    gate, started = threading.Event(), threading.Event()
    real = H.compare_geometry

    def slow(*a, **k):
        started.set()
        gate.wait(30)
        return real(*a, **k)
    monkeypatch.setattr(H, "compare_geometry", slow)
    panel = w.versions_ui.compare(["solid", "holed"])
    _compared(panel)
    panel.show3d.setChecked(True)
    assert started.wait(30) and not panel._future.done()
    future = panel._future
    panel.close_panel()
    gate.set()
    _wait(panel, future.done)
    for _ in range(20):
        QApplication.processEvents()
        time.sleep(0.01)
    assert w.viewport.ghosts == []


def test_a_tab_that_did_not_change_says_so_instead_of_drawing(window, tmp_path):
    w = window
    fd, studio = _block_and_holed(w, tmp_path)
    panel = w.versions_ui.compare(["holed"])        # holed against now: no change
    _compared(panel)
    panel.show3d.setChecked(True)
    assert w.viewport.ghosts == [] and "changed" in panel.status.text()
    panel.close_panel()
