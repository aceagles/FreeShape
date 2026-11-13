"""I2.3: a Derived whose source is a Part Studio of another Document, pinned to a Version. The Library is not
open: the consumer reads it through the registry at the pinned Version. A cross-Document source with no Version
is an error; one whose Document is not in the registry is red and names the id."""
import os
import shutil

import pytest

from conftest import add, rect_lines, region_refs, sketch_feature, TOP
from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import kernel as K
from freeshape import links as L

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _box(studio, w, depth):
    sid = sketch_feature(studio, lambda s: rect_lines(s, 0, 0, w, w), TOP)
    add(studio, "extrude", regions=region_refs(studio, sid), depth=depth, op="new")
    assert not studio.errors, studio.errors


class World:
    """Library (Part Studio Plate, a 20 x 20 x 10 box at v1, taller at v2) and Project with a Derived of it at v1."""

    def __init__(self, tmp_path):
        self.reg = R.DocRegistry(str(tmp_path / "data" / "documents.json"))
        lib = self.lib = DF.FolderDocument.create(str(tmp_path / "Library"), name="Library", registry=self.reg)
        self.plate = lib.add_tab("studio", "Plate")
        _box(self.plate.doc, 20.0, 10.0)
        self.pid = next(iter(self.plate.doc.state.parts))
        self.v1 = K.volume(self.plate.doc.state.parts[self.pid].shape)
        lib.create_version("v1", "first")
        ext = next(f for f in self.plate.doc.features if f["type"] == "extrude")
        ext["params"]["depth"] = 25.0
        self.plate.doc.replace_feature(ext)
        assert not self.plate.doc.errors
        self.v2 = K.volume(self.plate.doc.state.parts[self.pid].shape)
        lib.create_version("v2", "taller")
        self.proj = DF.FolderDocument.create(str(tmp_path / "Project"), name="Project", registry=self.reg)
        self.use = self.proj.add_tab("studio", "Use")

    def derive(self, version="v1", document=None):
        """Add the Derived without `conftest.add`'s raise on error: the red cases read doc.errors."""
        f = self.use.doc.make_feature("derived")
        f["params"].update(source=L.studio_source(self.plate, document=document or self.lib.id, version=version),
                           parts=[self.pid], sketches=[], connectors=[])
        self.use.doc.add_feature(f)
        return f


def test_derives_the_library_part_at_v1_with_the_library_not_open(tmp_path):
    w = World(tmp_path)
    f = w.derive("v1")
    assert not w.use.doc.errors, w.use.doc.errors
    part = w.use.doc.state.parts[f"{f['id']}/1"]
    assert abs(K.volume(part.shape) - w.v1) < 1e-6 and abs(w.v1 - w.v2) > 1


def test_the_pin_stays_at_v1_until_update_link_moves_it_to_v2(tmp_path):
    w = World(tmp_path)
    f = w.derive("v1")
    src = f["params"]["source"]
    st = w.proj.resolver.link_status(src)
    assert st["pinned"] == "v1" and st["latest"] == "v2" and st["update_available"]
    assert abs(K.volume(w.use.doc.state.parts[f"{f['id']}/1"].shape) - w.v1) < 1e-6
    assert w.proj.resolver.update_link(src) == "v2"
    w.use.doc.replace_feature(f)
    assert not w.use.doc.errors, w.use.doc.errors
    assert abs(K.volume(w.use.doc.state.parts[f"{f['id']}/1"].shape) - w.v2) < 1e-6
    assert not w.proj.resolver.link_status(src)["update_available"]


def test_no_version_is_an_error(tmp_path):
    w = World(tmp_path)
    f = w.derive(version=None)
    assert "has no Version pinned" in w.use.doc.errors[f["id"]]
    with pytest.raises(K.KernelError, match="has no Version pinned"):
        w.proj.resolver.resolve_studio(f["params"]["source"])


def test_a_document_not_in_the_registry_is_red_with_its_id(tmp_path):
    w = World(tmp_path)
    f = w.derive("v1", document="no-such-document-id")
    err = w.use.doc.errors[f["id"]]
    assert "Document not found" in err and "no-such-document-id" in err


def test_a_version_not_in_the_document_is_red(tmp_path):
    w = World(tmp_path)
    f = w.derive("v9")
    assert "Document not found" in w.use.doc.errors[f["id"]] and "v9" in w.use.doc.errors[f["id"]]


# ---------------------------------------------------------------- the dialog
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"


@pytest.fixture(scope="module")
def win(tmp_path_factory):
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QMessageBox
    from freeshape.engine import process as P
    from freeshape.ui import home as H
    from freeshape.ui import mainwindow as MW
    root = tmp_path_factory.mktemp("drvx")
    mp = pytest.MonkeyPatch()
    mp.setenv("FREESHAPE_DATA_DIR", str(root / "appdata"))
    app = QApplication.instance() or QApplication([])
    os.makedirs(root / "autosave", exist_ok=True)
    os.makedirs(root / "thumbs", exist_ok=True)
    mp.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(root / "autosave")))
    mp.setattr(MW.MainWindow, "autosave_dir", classmethod(lambda cls: str(root / "autosave")))
    mp.setattr(H, "thumbs_dir", lambda: str(root / "thumbs"))
    for name in ("warning", "critical", "information"):
        mp.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.Ok)
    reg = R.default_registry()
    lib = DF.FolderDocument.create(str(root / "Library"), name="Library", registry=reg)
    plate = lib.add_tab("studio", "Plate")
    _box(plate.doc, 20.0, 10.0)
    lib.create_version("v1", "first")
    proj = DF.FolderDocument.create(str(root / "Project"), name="Project", registry=reg)
    use = proj.add_tab("studio", "Use")
    proj.save()
    w = MW.MainWindow(engine=True)
    w.settings = QSettings(str(root / "settings.ini"), QSettings.IniFormat)
    w.open_document(proj.path)
    assert w.studio_engine.settle(240)
    w.use = w.folder.tab(use.id).doc
    w.lib, w.plate_tab = lib, plate
    w.tabs.setCurrentIndex(w._tab_index(w.use))
    w.studio_engine.settle(60)
    yield w
    w.studio_engine.shutdown()
    P.SPARES.clear()
    mp.undo()


def test_the_dialog_pins_a_version_before_it_lists_tabs_and_commits(win):
    from PySide6.QtCore import Qt
    w = win
    w.start_feature("derived")
    d = w.dialog
    pk = d.widgets["document"]
    assert pk.doc_combo.itemText(0) == "This Document" and pk.is_local() and d.ok_button.isEnabled()
    assert d.widgets["source"].findText("Plate") < 0                    # Library's tabs are not this Document's
    pk.doc_combo.setCurrentIndex(pk.doc_combo.findData(w.lib.id))       # choose Library: its Versions, no pin yet
    assert [pk.version_combo.itemData(i) for i in range(pk.version_combo.count())] == [None, "v1"]
    assert pk.selection() is None and not d.ok_button.isEnabled()
    assert d.widgets["source"].count() == 1                             # no tab list before a Version
    d.accept()
    assert w.dialog is d                                                # Enter did not commit it either
    pk.version_combo.setCurrentIndex(pk.version_combo.findData("v1"))
    combo = d.widgets["source"]
    assert [combo.itemText(i) for i in range(combo.count())] == ["— choose a Part Studio —", "Plate"]
    assert d.ok_button.isEnabled()
    combo.setCurrentIndex(combo.findData(w.plate_tab.id))
    parts = d.widgets["parts"]
    assert parts.count() == 1
    parts.item(0).setCheckState(Qt.Checked)
    src = d.feature["params"]["source"]
    assert src["document"] == w.lib.id and src["version"] == "v1" and d.feature["params"]["parts"]
    w.commit_dialog()
    assert w.studio_engine.settle(120)
    f = next(f for f in w.use.features if f["type"] == "derived")
    assert not w.use.errors.get(f["id"]) and len(w.use.state.parts) == 1
    w.start_feature("derived", edit_fid=f["id"])                        # editing reopens on Library at v1
    pk = w.dialog.widgets["document"]
    assert pk.selection() == (w.lib.id, "v1") and w.dialog.widgets["source"].currentData() == w.plate_tab.id
    w.cancel_dialog()
    w.use.undo()
    assert w.studio_engine.settle(120)
