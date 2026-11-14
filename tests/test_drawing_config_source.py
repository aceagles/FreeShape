"""I1.6: the drawing source picker lists one group per configuration of a Part Studio (labelled with the
configuration's name, as the Assembly insert panel does), carries the choice as `config` on the drawing's source, and
the drawing shows that configuration's model, also after save and reload. A Part Studio with no configurations is
listed once, as before. Offscreen Qt."""
import os

import numpy as np
import pytest

from conftest import TOP, add, rect_lines, region_refs, sketch_feature
from freeshape import kernel as K
from freeshape.drawing import DrawingDoc

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from freeshape.ui import drawing_ui as DU  # noqa: E402
from freeshape.ui import home as H  # noqa: E402
from freeshape.ui import mainwindow as MW  # noqa: E402

ROWS = [{"name": "Short", "vars": {"len": "10"}}, {"name": "Long", "vars": {"len": "25"}}]


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(tmp_path / "autosave")))
    monkeypatch.setattr(MW.MainWindow, "autosave_dir", classmethod(lambda cls: str(tmp_path / "autosave")))
    os.makedirs(tmp_path / "autosave", exist_ok=True)
    monkeypatch.setattr(H, "thumbs_dir", lambda: str(tmp_path / "thumbs"))
    os.makedirs(tmp_path / "thumbs", exist_ok=True)
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.Ok)
    w = MW.MainWindow()
    w.settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    app.processEvents()
    yield w
    for d in w.docs.values():                       # a dirty tab would ask to save on close
        d.dirty = False
    w.close()
    app.processEvents()
    w.deleteLater()


def _spacer(studio, configured):
    add(studio, "variable", name="len", expr="40")
    sid = sketch_feature(studio, lambda s: rect_lines(s, 0, 0, 20, 10), TOP)
    add(studio, "extrude", regions=region_refs(studio, sid), depth="#len", op="new")
    if configured:
        studio.set_configs(ROWS, 0)
    assert not studio.errors, studio.errors


def _height(shape):
    lo, hi = K.bbox(shape)
    return float(np.round(hi[2] - lo[2], 3))


def _pick(monkeypatch, label):
    """Drive the real New drawing dialog: select the row called `label` and accept."""
    seen = []

    def run(dlg):
        top = [dlg.tree.topLevelItem(i) for i in range(dlg.tree.topLevelItemCount())]
        seen.append([t.text(0) for t in top])
        item = next(t for t in top if t.text(0) == label).child(0)
        dlg.tree.setCurrentItem(item)
        return QDialog.Accepted

    monkeypatch.setattr(DU.NewDrawingDialog, "exec", run)
    return seen


def test_picker_lists_one_group_per_configuration(window):
    _spacer(window.doc, True)
    groups = window.drawing_sources()
    assert [g[0] for g in groups] == [f"{window.doc.name} · Short", f"{window.doc.name} · Long"]
    for (_, _, entries), row in zip(groups, ROWS):
        assert [s["config"] for _, s in entries] == [row["name"]] * len(entries)


def test_picker_lists_a_studio_without_configurations_once(window):
    _spacer(window.doc, False)
    groups = window.drawing_sources()
    assert [g[0] for g in groups] == [window.doc.name]
    assert all("config" not in s for _, s in groups[0][2])


def test_drawing_of_configuration_b_shows_b_and_keeps_it_after_reload(window, monkeypatch, tmp_path):
    studio = window.doc
    _spacer(studio, True)                                   # "Short" is active
    assert _height(studio.state.parts[next(iter(studio.state.parts))].shape) == 10.0
    seen = _pick(monkeypatch, f"{studio.name} · Long")
    window.new_drawing()
    assert seen == [[f"{studio.name} · Short", f"{studio.name} · Long"]]
    g = window.doc
    assert g.kind == "drawing" and g.source["config"] == "Long"
    assert g.name.endswith(" · Long")
    assert _height(g.shape) == 25.0                         # configuration B, not the active one

    path = str(tmp_path / "long.fsdrawing")
    g.save(path)
    g2 = DrawingDoc.load(path, resolver=window._resolve_drawing_source)
    assert g2.source["config"] == "Long"
    g2.refresh()
    assert g2.error is None and _height(g2.shape) == 25.0

    studio.set_active_config("Long")                        # switching the active row does not move a drawing
    studio.set_active_config("Short")
    g2.refresh()
    assert _height(g2.shape) == 25.0


def test_source_without_config_resolves_the_active_configuration(window):
    studio = window.doc
    _spacer(studio, True)
    src = window.studio_link(studio, next(iter(studio.state.parts)))
    assert "config" not in src and _height(window._resolve_drawing_source(src)) == 10.0
    studio.set_active_config("Long")
    assert _height(window._resolve_drawing_source(src)) == 25.0
