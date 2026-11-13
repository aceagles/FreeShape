"""I1.10: a Version opened read-only resolves its own links: a studio's Variable Studio reference and every
SOURCE_FEATURES feature (Derived, once I1.7 lands) read the same Version, and a cross-Document source inside a
Version view resolves through the registry. Headless, and through an offscreen window."""
import os
import shutil

import pytest

from conftest import (TOP, add, rect_lines, region_refs, shape_fingerprint, sketch_feature, state_fingerprint)
from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import features as F
from freeshape import kernel as K
from freeshape import links as L

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

REF = "variable_studio_ref"
PROBE = "probe_view_link"


@pytest.fixture
def probe(monkeypatch):
    """A test-only SOURCE_FEATURES type that keeps the volume of the first part it is handed."""
    monkeypatch.setitem(L.SOURCE_FEATURES, PROBE, "source")

    def run(state, f):
        pkg = f["params"].get("resolved")
        state.vars["probe_seen"] = None if pkg is None else sorted(p.hash for p in pkg.parts.values())
    monkeypatch.setitem(F.RUNNERS, PROBE, run)
    return PROBE


def _reg(tmp_path):
    return R.DocRegistry(str(tmp_path / "data" / "documents.json"))


def _box(studio, w=10.0, depth=5.0):
    sid = sketch_feature(studio, lambda s: rect_lines(s, 0, 0, w, w), TOP)
    add(studio, "extrude", regions=region_refs(studio, sid), depth=depth, op="new")
    assert not studio.errors, studio.errors


def _doc(tmp_path, reg):
    """Document with a Variable Studio (w=20), a source studio box(10) and a consumer studio that reads both."""
    fd = DF.FolderDocument.create(str(tmp_path / "Doc"), name="Doc", registry=reg)
    var = fd.add_tab("variables", "Vars")
    var.doc.add_row("w", "20 mm")
    src = fd.add_tab("studio", "Src")
    _box(src.doc, 10)
    use = fd.add_tab("studio", "Use")
    add(use.doc, REF, source={"kind": "variables", "tab": var.id, "name": "Vars"})
    sid = sketch_feature(use.doc, lambda s: rect_lines(s, 0, 0, 10, 10), TOP)
    add(use.doc, "extrude", regions=region_refs(use.doc, sid), depth="#w", op="new")
    assert not use.doc.errors, use.doc.errors
    return fd, var, src, use


def _add_probe(use, src, kind):
    use.doc.add_feature({"id": use.doc.new_id(), "type": kind, "name": "Probe", "suppressed": False,
                         "params": {"source": L.studio_source(src, part="*")}})
    return use.doc.state.vars["probe_seen"]


def _edit_workspace(fd, var, src):
    var.doc.set_row(0, expression="50 mm")
    ext = next(f for f in src.doc.features if f["type"] == "extrude")
    ext["params"]["depth"] = 30.0
    src.doc.replace_feature(ext)
    fd.refresh_links()
    fd.save()


def test_a_version_view_reads_its_own_variables_and_sources(tmp_path, probe):
    fd, var, src, use = _doc(tmp_path, _reg(tmp_path))
    v1_seen = _add_probe(use, src, probe)
    v1_vol = _vol(use.doc)
    fd.save()
    fd.create_version("v1")
    _edit_workspace(fd, var, src)
    live = fd.tab(use.id).doc
    assert live.state.vars["w"] == 50 and _vol(live) != v1_vol

    view = fd.open_version("v1")
    vdoc = view.tab(use.id).doc
    assert vdoc.state.vars["w"] == 20 and _vol(vdoc) == v1_vol
    assert vdoc.state.vars["probe_seen"] == v1_seen != live.state.vars["probe_seen"]
    assert vdoc.link_inputs is not None and view.resolver.folder is view
    # the Workspace keeps the edited values
    assert live.state.vars["w"] == 50
    view.close()


def test_a_version_view_resolves_a_derived_from_its_own_source(tmp_path):
    """I1.11: the Derived feature itself (not the probe) reads the Version's source tab, not the Workspace's."""
    fd, var, src, use = _doc(tmp_path, _reg(tmp_path))
    d = add(use.doc, "derived", source=L.studio_source(src), parts=[next(iter(src.doc.state.parts))],
            sketches=[], connectors=[])
    assert not use.doc.errors, use.doc.errors
    part = f"{d['id']}/1"
    v1_vol = round(K.volume(use.doc.state.parts[part].shape), 4)
    fd.save()
    fd.create_version("v1")
    _edit_workspace(fd, var, src)
    assert round(K.volume(fd.tab(use.id).doc.state.parts[part].shape), 4) != v1_vol
    view = fd.open_version("v1")
    vdoc = view.tab(use.id).doc
    assert not vdoc.errors, vdoc.errors
    assert round(K.volume(vdoc.state.parts[part].shape), 4) == v1_vol
    view.close()


def _vol(studio):
    return sorted(round(f["volume"]) for f in state_fingerprint(studio).values())


def test_a_cross_document_instance_resolves_inside_a_version_view(tmp_path):
    reg = _reg(tmp_path)
    a = DF.FolderDocument.create(str(tmp_path / "A"), name="A", registry=reg)
    st = a.add_tab("studio", "Plate")
    _box(st.doc, 20, 10)
    a.create_version("v1")
    pid = next(iter(st.doc.state.parts))
    b = DF.FolderDocument.create(str(tmp_path / "B"), name="B", registry=reg)
    asm = b.add_tab("assembly", "Asm")
    asm.doc.insert(L.studio_source(st, pid, document=a.id, version="v1"), name="Plate")
    asm.doc.refresh(solve=False, notify=False)
    assert not asm.doc.errors, asm.doc.errors
    want = shape_fingerprint(asm.doc.parts[asm.doc.instances[0]["id"]].shape)
    b.save()
    b.create_version("v1")
    view = b.open_version("v1")
    vasm = view.tab(asm.id).doc
    assert not vasm.errors, vasm.errors
    assert shape_fingerprint(vasm.parts[vasm.instances[0]["id"]].shape) == want
    view.close()
    assert view.registry is reg


def test_open_view_in_a_window_shows_the_versions_studio(tmp_path):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QMessageBox
    from freeshape.ui import mainwindow as MW
    app = QApplication.instance() or QApplication([])
    os.environ["FREESHAPE_DATA_DIR"] = str(tmp_path / "appdata")
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
    MW.MainWindow.autosave_root = staticmethod(lambda: str(tmp_path / "autosave"))
    os.makedirs(tmp_path / "autosave", exist_ok=True)
    fd, var, src, use = _doc(tmp_path, _reg(tmp_path))
    fd.save()
    fd.create_version("v1")
    v1_hash = {pid: f["hash"] for pid, f in state_fingerprint(use.doc).items()} if False else _vol(use.doc)
    w = MW.MainWindow()
    w.settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    try:
        w.folder = fd
        w._add_tab(use.doc)
        _edit_workspace(fd, var, src)
        edited = _vol(use.doc)
        assert edited != v1_hash
        view = w.versions_ui.open_view("v1")
        assert view is not None
        app.processEvents()
        shown = w.versions_ui.view["fd"].tab(use.id).doc
        assert shown is not use.doc and "read-only" in shown.name
        assert _vol(shown) == v1_hash and shown.state.vars["w"] == 20
        assert _vol(use.doc) == edited and use.doc.state.vars["w"] == 50    # the Workspace tab is untouched
    finally:
        w.versions_ui.close_view()
        w.folder = None
        w.deleteLater()
