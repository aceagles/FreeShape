"""I2.4: the Insert panel's Other Document entry. Choosing Library, a Version, a tab and a part inserts an instance
pinned to that Version (source carries `document` and `version`, no path); a newer Library Version shows up in
links.cross_link_updates and UpdatesDialog; the panel still lists the open Part Studios. Offscreen Qt."""
import os
import shutil

import pytest

from conftest import TOP, add, rect_lines, region_refs, sketch_feature
from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import links as L
from freeshape.engine.encode import Encoder

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

pytest.importorskip("PySide6.QtWidgets")
pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtGui import QAction  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from freeshape.ui import document_picker as DP  # noqa: E402
from freeshape.ui import home as H  # noqa: E402
from freeshape.ui import mainwindow as MW  # noqa: E402
from freeshape.ui import versions_ui as V  # noqa: E402


@pytest.fixture
def make_window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(tmp_path / "autosave")))
    os.makedirs(tmp_path / "autosave", exist_ok=True)
    monkeypatch.setattr(H, "thumbs_dir", lambda: str(tmp_path / "thumbs"))
    os.makedirs(tmp_path / "thumbs", exist_ok=True)
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.Ok)
    made = []

    def make():
        w = MW.MainWindow()
        w.settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
        monkeypatch.setattr(MW.MainWindow, "autosave_dir", classmethod(lambda cls: str(tmp_path / "autosave")))
        app.processEvents()
        made.append(w)
        return w

    yield make
    for w in made:
        w.versions_ui.close_view()
        w.folder = None
        w.deleteLater()


def _box(studio, depth):
    sid = sketch_feature(studio, lambda s: rect_lines(s, 0, 0, 20.0, 20.0), TOP)
    add(studio, "extrude", regions=region_refs(studio, sid), depth=depth, op="new")
    assert not studio.errors, studio.errors


def _hash(part):
    return Encoder(meshes=False).brep(part.shape)[1]


class World:
    def __init__(self, tmp_path):
        reg = R.default_registry()
        self.lib = DF.FolderDocument.create(str(tmp_path / "Library"), name="Library", registry=reg)
        self.st = self.lib.add_tab("studio", "Plate")
        _box(self.st.doc, 10.0)
        self.pid = next(iter(self.st.doc.state.parts))
        self.part_name = self.st.doc.state.parts[self.pid].name
        self.v1 = _hash(self.st.doc.state.parts[self.pid])
        self.lib.create_version("v1", "first")
        self.proj = DF.FolderDocument.create(str(tmp_path / "Project"), name="Project", registry=reg)
        self.mine = self.proj.tabs[0] if self.proj.tabs and self.proj.tabs[0].kind == "studio" \
            else self.proj.add_tab("studio", "Mine")
        _box(self.mine.doc, 3.0)
        self.asm = self.proj.add_tab("assembly", "Asm")
        self.proj.save()

    def v2(self):
        ext = next(f for f in self.st.doc.features if f["type"] == "extrude")
        ext["params"]["depth"] = 25.0
        self.st.doc.replace_feature(ext)
        assert not self.st.doc.errors
        self.lib.create_version("v2", "taller")


def _open_assembly(w, world):
    assert w.open_document(world.proj.path) is not None
    fd = w.folder
    asm = next(t for t in fd.tabs if t.kind == "assembly")
    w.tabs.setCurrentIndex(w._tab_index(asm.doc))
    assert w.doc is asm.doc
    return fd, asm


def _panel(w):
    w.asm.toggle_insert()
    assert w.asm.insert_panel is not None
    return w.asm.insert_panel


def test_insert_other_document_pins_the_version(make_window, tmp_path, monkeypatch):
    world = World(tmp_path)
    w = make_window()
    fd, asm = _open_assembly(w, world)
    panel = _panel(w)
    act = panel.findChild(QAction, "insert_other_document")
    assert act is not None

    def run(self):
        assert self.choose(world.lib.id, "v1", world.st.id, world.pid)
        return QDialog.Accepted
    monkeypatch.setattr(DP.OtherDocumentDialog, "exec", run)
    act.trigger()
    assert len(asm.doc.instances) == 1
    src = asm.doc.instances[0]["source"]
    assert src["document"] == world.lib.id and src["version"] == "v1"
    assert src["tab"] == world.st.id and src["part"] == world.pid and "path" not in src and "uid" not in src
    asm.doc.refresh(solve=False, notify=False)
    assert not asm.doc.errors, asm.doc.errors
    assert _hash(asm.doc.parts[asm.doc.instances[0]["id"]]) == world.v1


def test_dialog_rows_are_keyed_and_versions_listed(make_window, tmp_path):
    world = World(tmp_path)
    world.v2()
    w = make_window()
    fd, _asm = _open_assembly(w, world)
    dlg = DP.OtherDocumentDialog(fd)
    assert [dlg.picker.version_combo.itemText(i) for i in range(dlg.picker.version_combo.count())] == ["v2", "v1"]
    assert dlg.choose(world.lib.id, "v1", world.st.id, world.pid)
    row = dlg.tree.currentItem().data(0, DP.ROLE_ROW)
    assert {k: row[k] for k in ("document", "version", "tab", "part")} == \
        {"document": world.lib.id, "version": "v1", "tab": world.st.id, "part": world.pid}
    assert not dlg.choose(world.lib.id, "v9", world.st.id)


def test_update_appears_once_library_has_v2_and_updates_dialog_updates_it(make_window, tmp_path):
    world = World(tmp_path)
    w = make_window()
    fd, asm = _open_assembly(w, world)
    dlg = DP.OtherDocumentDialog(fd)
    assert dlg.choose(world.lib.id, "v1", world.st.id, world.pid)
    assert w.asm.insert_document_sources(dlg.sources()) == 1
    assert L.cross_link_updates(fd) == []
    world.v2()
    ups = L.cross_link_updates(fd)
    assert [(u["document"], u["pinned"], u["latest"]) for u in ups] == [(world.lib.id, "v1", "v2")]
    ud = V.UpdatesDialog(w.versions_ui)
    assert list(ud.update_buttons) == [world.lib.id]
    ud.update_buttons[world.lib.id].click()
    assert asm.doc.instances[0]["source"]["version"] == "v2"
    assert L.cross_link_updates(fd) == []


def test_insert_panel_still_lists_open_part_studios(make_window, tmp_path):
    world = World(tmp_path)
    w = make_window()
    _open_assembly(w, world)
    panel = _panel(w)
    tops = [panel.tree.topLevelItem(i) for i in range(panel.tree.topLevelItemCount())]
    assert [t.text(0) for t in tops] == [d.name for d in w.studio_docs()] and tops
    mine = tops[0]
    assert [mine.child(i).text(0) for i in range(mine.childCount())] == \
        [p.name for p in w.studio_docs()[0].state.parts.values()]
