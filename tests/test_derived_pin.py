"""I1.11: a Derived pinned to a Version of its own Document. The source carries `version` and no `document`; it
is read from open_version(version), the Link graph holds no live edge to the source tab, and the cross-Document
machinery (link_status, update_link, cross_link_updates, UpdatesDialog) covers it. Following the Workspace is the
default: a source with no `version` is the live link as before."""
import os
import shutil

import pytest

from conftest import add, rect_lines, region_refs, sketch_feature, TOP
from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import kernel as K
from freeshape import links as L
from freeshape.engine import ProcessEngine
from freeshape.engine import process as P

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

V1, V2 = 20 * 20 * 10, 20 * 20 * 25


@pytest.fixture(scope="module", autouse=True)
def _no_spares_left():
    yield
    P.SPARES.clear()


def _vol(part):
    return round(K.volume(part.shape), 4)


class World:
    """One Document: Src (a 20 x 20 x 10 box, Version v1) and Use (a Derived of Src, pinned to v1). The Workspace
    Src is then 25 tall; `v2()` makes that a Version."""

    def __init__(self, tmp_path, pin="v1"):
        self.reg = R.DocRegistry(str(tmp_path / "data" / "documents.json"))
        self.fd = fd = DF.FolderDocument.create(str(tmp_path / "Doc"), name="Doc", registry=self.reg)
        self.src = fd.add_tab("studio", "Src")
        sid = sketch_feature(self.src.doc, lambda s: rect_lines(s, 0, 0, 20, 20), TOP)
        add(self.src.doc, "extrude", regions=region_refs(self.src.doc, sid), depth=10.0, op="new")
        self.pid = next(iter(self.src.doc.state.parts))
        self.use = fd.add_tab("studio", "Use")
        fd.create_version("v1", "first")
        self.f = f = self.use.doc.make_feature("derived")
        f["params"].update(source=L.studio_source(self.src), parts=[self.pid], sketches=[], connectors=[])
        if pin:
            f["params"]["source"]["version"] = pin
        self.use.doc.add_feature(f)
        self.edit_src(25.0)

    def edit_src(self, depth):
        ext = next(f for f in self.src.doc.features if f["type"] == "extrude")
        ext["params"]["depth"] = depth
        self.src.doc.replace_feature(ext)
        assert not self.src.doc.errors, self.src.doc.errors

    def v2(self):
        self.fd.create_version("v2", "taller")

    @property
    def derived_part(self):
        return self.use.doc.state.parts[f"{self.f['id']}/1"]

    def engine(self):
        self.fd.save()
        eng = ProcessEngine()
        eng.use_document(self.fd.id, self.reg)
        for t in (self.src, self.use):
            eng.open_tab(os.path.join(self.fd.path, t.file), tab=t.id)
        return eng


def _depth_edit(w, depth):
    ext = next(f for f in w.src.doc.features if f["type"] == "extrude")
    return {"op": "set_param", "feature": ext["id"], "key": "depth", "value": depth}


def _volumes(eng, tab):
    return sorted(round(K.volume(K.shape_from_brep(p.brep)), 4) for p in eng.outputs(tab).parts)


def test_a_pinned_derived_keeps_v1_after_the_workspace_edit_and_is_not_rebuilt(tmp_path):
    w = World(tmp_path)
    assert not w.use.doc.errors, w.use.doc.errors
    assert _vol(w.derived_part) == V1 != V2                   # v1's volume although the Workspace Src is 25 tall
    eng = w.engine()
    try:
        assert not eng.outputs(w.use.id).errors, eng.outputs(w.use.id).errors
        assert _volumes(eng, w.use.id) == [V1]
        assert (w.use.id, w.src.id) not in eng.link_edges()    # no live edge to the source
        before = eng.rebuild_counts().get(w.use.id, 0)
        eng.submit_edit(w.src.id, _depth_edit(w, 30.0))
        assert _volumes(eng, w.src.id) == [20 * 20 * 30]
        assert eng.rebuild_counts().get(w.use.id, 0) == before
        assert _volumes(eng, w.use.id) == [V1]
    finally:
        eng.close()


def test_link_status_reports_update_available_and_update_link_moves_to_v2(tmp_path):
    w = World(tmp_path)
    src = w.f["params"]["source"]
    st = w.fd.resolver.link_status(src)
    assert (st["pinned"], st["latest"], st["update_available"], st["error"]) == ("v1", "v1", False, None)
    w.v2()
    st = w.fd.resolver.link_status(src)
    assert st["pinned"] == "v1" and st["latest"] == "v2" and st["update_available"]
    assert _vol(w.derived_part) == V1
    assert [(u["pinned"], u["latest"], u["tab"], u["local"]) for u in L.cross_link_updates(w.fd)] == \
        [("v1", "v2", w.use.id, True)]
    assert w.fd.resolver.update_link(src) == "v2"
    w.use.doc.replace_feature(w.f)
    assert not w.use.doc.errors, w.use.doc.errors
    assert _vol(w.derived_part) == V2
    assert not w.fd.resolver.link_status(src)["update_available"] and L.cross_link_updates(w.fd) == []


def test_update_cross_links_repins_a_same_document_pin(tmp_path):
    w = World(tmp_path)
    w.v2()
    assert L.update_cross_links(w.fd, document=w.fd.id) == 1
    assert w.f["params"]["source"]["version"] == "v2" and not w.use.doc.errors
    assert _vol(w.derived_part) == V2
    assert L.update_cross_links(w.fd) == 0


def test_following_the_workspace_again_restores_the_live_edge(tmp_path):
    w = World(tmp_path)
    eng = w.engine()
    try:
        assert _volumes(eng, w.use.id) == [V1]
        live = L.studio_source(w.src)                           # no `version`: Follow workspace
        eng.submit_edit(w.use.id, {"op": "set_param", "feature": w.f["id"], "key": "source", "value": live})
        assert (w.use.id, w.src.id) in eng.link_edges()
        assert _volumes(eng, w.use.id) == [V2]                 # the Workspace Src as it stands
        before = eng.rebuild_counts().get(w.use.id, 0)
        eng.submit_edit(w.src.id, _depth_edit(w, 30.0))
        assert eng.rebuild_counts().get(w.use.id, 0) == before + 1
        assert _volumes(eng, w.use.id) == [20 * 20 * 30]
    finally:
        eng.close()


def test_a_pin_naming_a_version_that_does_not_exist_is_red_and_names_it(tmp_path):
    w = World(tmp_path, pin="v9")
    err = w.use.doc.errors[w.f["id"]]
    assert "v9" in err and "this Document" in err
    st = w.fd.resolver.link_status(w.f["params"]["source"])
    assert "v9" in st["error"] and not st["update_available"]
    eng = w.engine()
    try:
        assert "v9" in str(eng.outputs(w.use.id).errors)
    finally:
        eng.close()


def test_the_dependency_view_lists_a_pinned_derived_as_pinned(tmp_path):
    w = World(tmp_path)
    w.fd.save()
    view = L.dependency_view(w.fd, w.use.id)
    (use,) = view["uses"]
    assert use["kind"] == "Derived" and use["state"] == "pinned" and use["pinned"] == "v1"
    (by,) = L.dependency_view(w.fd, w.src.id)["used_by"]          # Src is read by Use, as a Version, not live
    assert by["tab"] == w.use.id and by["state"] == "pinned" and by["label"] == "pinned v1"


# ---------------------------------------------------------------- the dialog and UpdatesDialog (offscreen Qt)
@pytest.fixture
def make_window(tmp_path, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QMessageBox
    from freeshape.ui import home as H
    from freeshape.ui import mainwindow as MW
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(tmp_path / "autosave")))
    monkeypatch.setattr(MW.MainWindow, "autosave_dir", classmethod(lambda cls: str(tmp_path / "autosave")))
    os.makedirs(tmp_path / "autosave", exist_ok=True)
    monkeypatch.setattr(H, "thumbs_dir", lambda: str(tmp_path / "thumbs"))
    os.makedirs(tmp_path / "thumbs", exist_ok=True)
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.Ok)
    made = []

    def make():
        w = MW.MainWindow()
        w.settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
        app.processEvents()
        made.append(w)
        return w
    yield make
    for w in made:
        w.versions_ui.close_view()
        w.folder = None
        w.deleteLater()


def test_updates_dialog_lists_the_pinned_derived_and_update_repins_it(make_window, tmp_path):
    from freeshape.ui import versions_ui as V
    w = World(tmp_path)
    w.v2()
    w.fd.save()
    win = make_window()
    assert win.open_document(w.fd.path) is not None
    fd = win.folder
    use = next(t for t in fd.tabs if t.id == w.use.id)
    f = next(x for x in use.doc.features if x["type"] == "derived")
    dlg = V.UpdatesDialog(win.versions_ui)
    assert list(dlg.update_buttons) == [fd.id]
    assert dlg.update_buttons[fd.id].isEnabled()
    dlg.update_buttons[fd.id].click()
    assert f["params"]["source"]["version"] == "v2"
    assert L.cross_link_updates(fd) == []
    from PySide6.QtWidgets import QLabel
    assert any("up to date" in lb.text() for lb in dlg.findChildren(QLabel))


def test_the_dialog_offers_follow_workspace_or_a_version_for_this_document(make_window, tmp_path):
    w = World(tmp_path, pin=None)
    w.fd.save()
    win = make_window()
    assert win.open_document(w.fd.path) is not None
    fd = win.folder
    use = next(t for t in fd.tabs if t.id == w.use.id)
    win.tabs.setCurrentIndex(win._tab_index(use.doc))
    f = next(x for x in use.doc.features if x["type"] == "derived")
    win.start_feature("derived", edit_fid=f["id"])
    try:
        pk = win.dialog.widgets["document"]
        assert pk.is_local() and pk.version_combo.isEnabled()
        assert [pk.version_combo.itemText(i) for i in range(pk.version_combo.count())] == ["Follow workspace", "v1"]
        assert pk.local_pin() is None and not win.dialog.feature["params"]["source"].get("version")
        parts = list(win.dialog.feature["params"]["parts"])
        pk.version_combo.setCurrentIndex(pk.version_combo.findData("v1"))
        src = win.dialog.feature["params"]["source"]
        assert src["version"] == "v1" and "document" not in src and src["tab"] == w.src.id
        assert win.dialog.feature["params"]["parts"] == parts                    # the pin change keeps the parts
        pk.version_combo.setCurrentIndex(0)
        assert "version" not in win.dialog.feature["params"]["source"]
        pk.version_combo.setCurrentIndex(pk.version_combo.findData("v1"))
    finally:
        win.cancel_dialog()
    f["params"]["source"]["version"] = "v1"
    win.start_feature("derived", edit_fid=f["id"])                               # an existing pin reopens pinned
    try:
        pk = win.dialog.widgets["document"]
        assert pk.is_local() and pk.local_pin() == "v1"
        assert win.dialog.widgets["source"].currentData() == w.src.id
    finally:
        win.cancel_dialog()
