"""I2.7: a Variable Studio reference whose source is a Variable Studio of another Document, pinned to a Version.
The Library is not open: the consumer reads it through the registry at the pinned Version. A cross-Document
reference with no Version is a red feature saying so."""
import shutil

import pytest

from freeshape import doc_folder as DF
from freeshape import doc_registry as R

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

REF = "variable_studio_ref"


def var_source(tab, document=None, version=None):
    src = {"kind": "variables", "tab": tab.id, "name": tab.name}
    if document is not None:
        src.update(document=document, version=version)
    return src


class World:
    """Library (Variable Studio Dims: w = 20 mm, t = 3 mm at v1; w = 35 mm at v2) and Project with a Part Studio."""

    def __init__(self, tmp_path):
        self.reg = R.DocRegistry(str(tmp_path / "data" / "documents.json"))
        lib = self.lib = DF.FolderDocument.create(str(tmp_path / "Library"), name="Library", registry=self.reg)
        self.dims = lib.add_tab("variables", "Dims")
        self.dims.doc.add_row("w", "20 mm")
        self.dims.doc.add_row("t", "3 mm")
        lib.create_version("v1", "first")
        self.dims.doc.set_row(0, expression="35 mm")
        lib.create_version("v2", "wider")
        self.proj = DF.FolderDocument.create(str(tmp_path / "Project"), name="Project", registry=self.reg)
        self.use = self.proj.add_tab("studio", "Use")

    def ref(self, version="v1", document=None, names=None):
        """Add the reference without `conftest.add`'s raise on error: the red cases read doc.errors."""
        f = self.use.doc.make_feature(REF)
        f["params"]["source"] = var_source(self.dims, document=document or self.lib.id, version=version)
        if names is not None:
            f["params"]["names"] = names
        self.use.doc.add_feature(f)
        return f


def test_uses_the_librarys_w_at_v1_with_the_library_closed(tmp_path):
    w = World(tmp_path)
    w.ref("v1")
    assert not w.use.doc.errors, w.use.doc.errors
    assert w.use.doc.state.vars == {"w": 20.0, "t": 3.0}


def test_the_pin_stays_at_v1_until_update_link_moves_it_to_v2(tmp_path):
    w = World(tmp_path)
    f = w.ref("v1")
    src = f["params"]["source"]
    st = w.proj.resolver.link_status(src)
    assert st["pinned"] == "v1" and st["latest"] == "v2" and st["update_available"]
    assert w.use.doc.state.vars["w"] == 20.0
    assert w.proj.resolver.update_link(src) == "v2"
    w.use.doc.replace_feature(f)
    assert not w.use.doc.errors, w.use.doc.errors
    assert w.use.doc.state.vars["w"] == 35.0
    assert not w.proj.resolver.link_status(src)["update_available"]


def test_link_status_and_updates_cover_the_reference(tmp_path):
    from freeshape import links as L
    w = World(tmp_path)
    f = w.ref("v1")
    assert L.SOURCE_FEATURES[REF] == "source" and L.feature_source(f) is f["params"]["source"]
    assert w.proj.resolver.link_status(f["params"]["source"])["update_available"]


def test_no_version_is_a_red_feature_saying_so(tmp_path):
    w = World(tmp_path)
    f = w.ref(version=None)
    assert "has no Version pinned" in w.use.doc.errors[f["id"]]
    assert w.proj.resolver.resolve_variables(f["params"]["source"]) is None     # the feature says why, not the resolver


def test_a_document_not_in_the_registry_is_red_and_names_it(tmp_path):
    w = World(tmp_path)
    f = w.ref("v1", document="no-such-document-id")
    assert "no-such-document-id" in w.use.doc.errors[f["id"]]


def test_subset_names_work_on_a_cross_document_reference(tmp_path):
    w = World(tmp_path)
    f = w.ref("v1", names=["w"])
    assert not w.use.doc.errors, w.use.doc.errors
    assert w.use.doc.state.vars == {"w": 20.0}
    f2 = w.ref("v1", names=["nope"])
    assert "no variable #nope" in w.use.doc.errors[f2["id"]]
    assert w.use.doc.variable_table()[0] == {"name": "w", "value": 20.0, "feature": f["id"], "overridden": False}


# ---------------------------------------------------------------- the dialog, on the process engine
import os  # noqa: E402

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
    root = tmp_path_factory.mktemp("vrx")
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
    dims = lib.add_tab("variables", "Dims")
    dims.doc.add_row("w", "20 mm")
    dims.doc.add_row("t", "3 mm")
    lib.create_version("v1", "first")
    proj = DF.FolderDocument.create(str(root / "Project"), name="Project", registry=reg)
    local = proj.add_tab("variables", "Local")
    local.doc.add_row("q", "1 mm")
    use = proj.add_tab("studio", "Use")
    proj.save()
    w = MW.MainWindow(engine=True)
    w.settings = QSettings(str(root / "settings.ini"), QSettings.IniFormat)
    w.open_document(proj.path)
    assert w.studio_engine.settle(240)
    w.use = w.folder.tab(use.id).doc
    w.lib, w.dims_tab, w.local_tab = lib, dims, local
    w.tabs.setCurrentIndex(w._tab_index(w.use))
    w.studio_engine.settle(60)
    yield w
    w.studio_engine.shutdown()
    P.SPARES.clear()
    mp.undo()


def test_the_dialog_pins_a_version_before_it_lists_studios_and_the_engine_resolves_it(win):
    from PySide6.QtCore import Qt
    w = win
    w.start_feature(REF)
    d = w.dialog
    pk = d.widgets["document"]
    assert pk.is_local() and d.ok_button.isEnabled()
    assert [d.widgets["source"].itemText(i) for i in range(d.widgets["source"].count())] == ["Local"]
    pk.doc_combo.setCurrentIndex(pk.doc_combo.findData(w.lib.id))       # choose Library: its Versions, no pin yet
    assert [pk.version_combo.itemData(i) for i in range(pk.version_combo.count())] == [None, "v1"]
    assert not d.ok_button.isEnabled() and d.feature["params"]["source"] == {}
    d.accept()
    assert w.dialog is d                                                # Enter did not commit it either
    pk.version_combo.setCurrentIndex(pk.version_combo.findData("v1"))
    combo = d.widgets["source"]
    assert [combo.itemText(i) for i in range(combo.count())] == ["Dims"] and d.ok_button.isEnabled()
    src = d.feature["params"]["source"]
    assert src["document"] == w.lib.id and src["version"] == "v1" and src["tab"] == w.dims_tab.id
    lst = d.widgets["names"]                                            # the Library's names, subset by ticking
    assert [lst.item(i).data(Qt.UserRole) for i in range(lst.count())] == ["w", "t"]
    lst.item(1).setCheckState(Qt.Unchecked)
    assert d.feature["params"]["names"] == ["w"]
    pk.version_combo.setCurrentIndex(0)                                 # back to no Version: names are cleared
    assert "names" not in d.feature["params"] and not d.ok_button.isEnabled()
    pk.version_combo.setCurrentIndex(pk.version_combo.findData("v1"))
    lst.item(1).setCheckState(Qt.Unchecked)
    w.commit_dialog()
    assert w.studio_engine.settle(120)
    f = next(f for f in w.use.features if f["type"] == REF)
    assert not w.use.errors.get(f["id"]), w.use.errors
    assert w.use.state.vars == {"w": 20.0}                              # read by the worker from the pinned package
    w.start_feature(REF, edit_fid=f["id"])                              # editing reopens on Library at v1
    pk = w.dialog.widgets["document"]
    assert pk.selection() == (w.lib.id, "v1") and w.dialog.widgets["source"].currentData() == w.dims_tab.id
    w.cancel_dialog()
    w.use.undo()
    assert w.studio_engine.settle(120)


def test_a_same_document_reference_stays_unpinned(win):
    w = win
    w.start_feature(REF)
    d = w.dialog
    src = d.feature["params"]["source"]
    assert src["tab"] == w.local_tab.id and "document" not in src and "version" not in src
    w.commit_dialog()
    assert w.studio_engine.settle(120)
    f = next(f for f in w.use.features if f["type"] == REF)
    assert w.use.state.vars == {"q": 1.0} and "version" not in f["params"]["source"]
    w.use.undo()
    assert w.studio_engine.settle(120)
