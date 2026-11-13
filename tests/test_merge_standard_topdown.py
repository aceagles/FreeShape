"""Standard parts and Top-Down Design in one Assembly (the merge of master into standard-parts).

A Standard part instance has source {"kind": "standard", ...}: no Part Studio tab, no Document link, no element map.
Code that walks an Assembly's instances for Top-Down features must cope with one."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QPoint, QSettings  # noqa: E402
from PySide6.QtGui import QAction  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QPushButton  # noqa: E402

from conftest import block_doc  # noqa: E402
from freeshape import assembly as A  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import links as L  # noqa: E402
from freeshape import migrations as Mig  # noqa: E402
from freeshape.ui import home as H  # noqa: E402
from freeshape.ui import mainwindow as MW  # noqa: E402
from tests.test_assembly import face  # noqa: E402

SCREW = "sp:fasteners/iso4762"
PARAMS = {"size": "M3", "length": 10}


def test_file_versions_after_the_merge():
    """The chains after Parity Sweep (geometry to blobs: assembly 8, document 3, drawing 3) and the upstream 2.0
    merge (assembly 9: explode and patterns; document 4: upstream's keys and up-to ends)."""
    assert Mig.latest_version("assembly") == 9
    assert Mig.latest_version("document") == 4 and Mig.latest_version("drawing") == 3
    assert A.AssemblyDoc().to_json().count('"version": 9') == 1


@pytest.fixture
def rig(tmp_path, monkeypatch):
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
    path = str(tmp_path / "Doc")
    w._pick_new_document_path = lambda: path
    w.home.newDocument.emit()
    fd = w.folder
    plate = fd.tabs[0]
    plate.doc.__dict__.update(block_doc(40, 20, 10).__dict__)
    fd._attach(plate)
    asm = fd.add_tab("assembly", "Asm")
    a = asm.doc
    ip = a.insert(L.studio_source(plate, f"{plate.doc.features[1]['id']}/1"), name="Plate")
    isc = a.insert_standard_part(SCREW, PARAMS)
    a.refresh(solve=False, notify=False)
    w._add_tab(plate.doc)
    w._add_tab(asm.doc)
    app.processEvents()
    assert w.doc is asm.doc
    yield w, fd, plate, asm, ip, isc
    w.folder = None
    w.deleteLater()


def test_context_capture_with_a_standard_neighbour_and_subject(rig):
    w, fd, plate, asm, ip, isc = rig
    cap = A.capture_context(asm.doc, ip)
    n = next(x for x in cap["neighbours"] if x["id"] == isc)
    assert n["names"] == {} and n["brep"]
    cap = A.capture_context(asm.doc, isc)         # the screw as the subject: no crash, the plate is its neighbour
    assert [x["id"] for x in cap["neighbours"]] == [ip]


def test_edit_in_context_refuses_a_standard_part_and_menu_does_not_offer_it(rig, monkeypatch):
    w, fd, plate, asm, ip, isc = rig
    w.asm.selected_instance_ids = lambda: [isc]
    msg = w.asm.edit_in_context()
    assert msg and "Part Studio" in msg
    assert not [f for f in plate.doc.features if f["type"] == "context"]
    from tests.test_connector_at_origin import menu_actions
    seen = menu_actions(monkeypatch)
    w.asm.instance_menu(isc, QPoint(0, 0))
    acts = [x.text() for x in seen]
    assert "Edit in context" not in acts and "Edit standard part…" in acts
    seen.clear()
    w.asm.instance_menu(ip, QPoint(0, 0))
    acts = [x.text() for x in seen]
    assert "Edit in context" in acts and "Open Part Studio" in acts and "Edit standard part…" not in acts


def test_dependency_view_lists_no_link_for_a_standard_part(rig):
    w, fd, plate, asm, ip, isc = rig
    v = L.dependency_view(fd, asm.id)
    assert [(e["kind"], e["state"]) for e in v["uses"]] == [("Assembly instance", "live")]
    assert L.tab_sources("assembly", asm.doc)       # still listed for repair: repair_source ignores a standard one
    L.repair_links(fd.resolver, asm.doc)
    assert asm.doc.instance(isc)["source"]["kind"] == "standard"


def test_insert_panel_has_both_other_document_and_standard_parts(rig):
    w, fd, plate, asm, ip, isc = rig
    ctl = w.asm
    if ctl.insert_panel is None:
        ctl.toggle_insert()
    panel = ctl.insert_panel
    assert [panel.tabs.tabText(i) for i in range(panel.tabs.count())] == ["Parts", "Standard parts"]
    parts_page = panel.tabs.widget(0)
    labels = [b.text() for b in parts_page.findChildren(QPushButton)]
    assert {"Insert", "From file…", "Other Document…"} <= set(labels)
    assert panel.other_doc_action.objectName() == "insert_other_document"
    assert panel.standard.parentWidget() is panel.tabs.widget(1) or panel.tabs.widget(1).isAncestorOf(panel.standard)
    assert panel.tree.topLevelItemCount() >= 1


# ------------------------------------------------------------------ named connectors of both kinds
def _studio_connectors(iid):
    return [{"id": "F9", "name": "Pin", "frame": {"o": [1.0, 2.0, 3.0], "z": [0.0, 0.0, 1.0], "x": [1.0, 0.0, 0.0]}}]


def test_named_connector_lookup_dispatches_by_kind(rig):
    w, fd, plate, asm, ip, isc = rig
    a = asm.doc
    a.connector_resolver = lambda src: _studio_connectors(0) if src.get("kind") == "studio" else None
    std = a.named_connector(isc, "under_head")
    assert std["kind"] == "named" and std["name"] == "under_head" and "named" not in std
    pub = a.named_connector(ip, "F9")
    assert pub["kind"] == "connector" and pub["named"] == "F9" and pub["frame"]["o"] == [1.0, 2.0, 3.0]
    # a studio part has no Standard-part names; a Standard part has no published ids
    with pytest.raises(K.KernelError):
        a.named_connector(ip, "under_head")
    with pytest.raises(K.KernelError):
        a.named_connector(isc, "F9")
    assert a.published_connectors(isc) is None          # nothing published, nothing added
    # an explicit origin connector can be added to a Standard part and is found by its id
    cid = a.add_instance_origin_connector(isc)
    assert a.named_connector(isc, cid)["kind"] == "origin"
    assert a.named_connector(isc, "under_head")["kind"] == "named"


def test_mates_on_both_kinds_save_load_and_solve(rig, tmp_path):
    w, fd, plate, asm, ip, isc = rig
    a = asm.doc
    a.resolver = fd.resolver.resolve_source
    a.connector_resolver = lambda src: _studio_connectors(0) if src.get("kind") == "studio" else None
    a.set_fixed(ip, True)
    m = a.make_mate("fastened")
    m["a"], m["b"] = a.named_connector(ip, "F9"), a.named_connector(isc, "under_head")
    a.add_mate(m)
    a.refresh()
    assert a.errors == {}, a.errors
    assert np.allclose(a.world_frame(a.mates[0]["b"])["o"], [1.0, 2.0, 3.0], atol=1e-6)
    path = str(tmp_path / "mixed.fasm")
    a.save(path)
    b = A.AssemblyDoc.load(path, a.resolver)
    b.resolver = a.resolver
    b.connector_resolver = a.connector_resolver
    b.refresh()
    assert sorted((x["a"]["kind"], x["b"]["kind"]) for x in b.mates) == [("connector", "named")]
    assert b.errors == {}, b.errors
    assert np.allclose(b.world_frame(b.mates[0]["b"])["o"], [1.0, 2.0, 3.0], atol=1e-6)
    # the studio stops publishing F9: only that mate end turns red, the Standard part's end is unaffected
    b.connector_resolver = lambda src: []
    b.refresh()
    assert "F9" in b.errors[b.mates[0]["id"]] or "Pin" in b.errors[b.mates[0]["id"]]


def test_mate_connector_feature_on_a_chamfer_face_points_out():
    """Top-Down's Mate connector feature builds its frame with connector_frame, so a left-handed flat face
    (a chamfer) gets Z out of the part, not into it."""
    from conftest import add, both, centre_near, edge_refs, line_along, only_part
    from tests.test_known_bugs import _true_outward
    from tests.test_studio_connectors import connector_params
    doc = block_doc(40, 20, 10)
    add(doc, "chamfer", edges=edge_refs(only_part(doc), both(line_along([1, 0, 0]), centre_near([20, 0, 10])), expect=1),
        distance=2.0)
    shape = only_part(doc).shape
    idx = next(i for i, f in enumerate(K.faces(shape))
               if (n := K.face_info(f).get("n")) is not None and abs(abs(n[1]) - 2 ** -0.5) < 1e-6)
    f = add(doc, "mate_connector", **connector_params(doc, index=idx))
    z = np.array(doc.state.connectors[f["id"]]["frame"]["z"])
    assert np.dot(z, _true_outward(K.faces(shape)[idx])) > 0.99
