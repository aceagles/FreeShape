"""I6.3: links.dependency_view, the Dependencies panel and the state mark on feature rows."""
import os
import shutil

import pytest

from conftest import TOP, add, rect_lines, region_refs, sketch_feature
from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import features as F
from freeshape import links as L
from freeshape import assembly as A
from tests.test_assembly import face, mate

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

PROBE = "probe_dep_view"


@pytest.fixture
def probe(monkeypatch):
    """A test-only SOURCE_FEATURES type, standing in for the Derived feature."""
    monkeypatch.setitem(L.SOURCE_FEATURES, PROBE, "source")
    monkeypatch.setitem(F.RUNNERS, PROBE, lambda state, f: None)
    return PROBE


def _box(studio, w=10.0, depth=5.0):
    sid = sketch_feature(studio, lambda s: rect_lines(s, 0, 0, w, w), TOP)
    add(studio, "extrude", regions=region_refs(studio, sid), depth=depth, op="new")
    assert not studio.errors, studio.errors


def _link_feature(tab, src, kind=PROBE):
    tab.doc.add_feature({"id": tab.doc.new_id(), "type": kind, "name": "Link", "suppressed": False,
                         "params": {"source": src}})
    return tab.doc.features[-1]["id"]


def _rig(tmp_path, reg=None):
    """Library Document A (Base, versions v1 and v2). Document B: Skeleton, Lid (uses Skeleton and Base@v1),
    and an Assembly holding Lid."""
    reg = reg or R.DocRegistry(str(tmp_path / "data" / "documents.json"))
    a = DF.FolderDocument.create(str(tmp_path / "A"), name="Lib", registry=reg)
    base = a.add_tab("studio", "Base")
    _box(base.doc)
    a.create_version("v1")
    b = DF.FolderDocument.create(str(tmp_path / "B"), name="B", registry=reg)
    skel = b.add_tab("studio", "Skeleton")
    _box(skel.doc)
    lid = b.add_tab("studio", "Lid")
    _box(lid.doc)
    f_skel = _link_feature(lid, L.studio_source(skel, part="*"))
    f_base = _link_feature(lid, L.studio_source(base, part="*", document=a.id, version="v1"))
    asm = b.add_tab("assembly", "Asm")
    asm.doc.insert(L.studio_source(lid, f"{lid.doc.features[1]['id']}/1"), name="Lid")
    asm.doc.refresh(solve=False, notify=False)
    ext = next(f for f in base.doc.features if f["type"] == "extrude")
    ext["params"]["depth"] = 9.0
    base.doc.replace_feature(ext)
    a.create_version("v2")
    return a, b, base, skel, lid, asm, f_skel, f_base


def _row(entries, name):
    return next(e for e in entries if e["name"] == name)


def test_lid_uses_and_used_by(tmp_path, probe):
    a, b, base, skel, lid, asm, f_skel, f_base = _rig(tmp_path)
    v = L.dependency_view(b, lid.id)
    assert [(e["name"], e["kind"], e["state"]) for e in v["uses"]] == [
        ("Skeleton", "probe_dep_view", "live"), ("Base", "probe_dep_view", "update available")]
    e = _row(v["uses"], "Base")
    assert (e["document"], e["document_name"], e["pinned"], e["latest"]) == (a.id, "Lib", "v1", "v2")
    assert e["label"] == "update available v2" and e["tab"] == base.id and e["feature"] == f_base
    assert _row(v["uses"], "Skeleton")["label"] == "live"
    assert [(e["name"], e["kind"], e["state"]) for e in v["used_by"]] == [("Asm", L.ASSEMBLY_INSTANCE, "live")]
    assert L.dependency_view(b, skel.id)["used_by"][0]["name"] == "Lid"


def test_kind_label_table(tmp_path, probe, monkeypatch):
    a, b, base, skel, lid, asm, *_ = _rig(tmp_path)
    monkeypatch.setitem(L.SOURCE_FEATURE_LABELS, PROBE, "Derived")
    assert {e["kind"] for e in L.dependency_view(b, lid.id)["uses"]} == {"Derived"}


def test_variable_reference_drawing_and_pinned(tmp_path, probe):
    a, b, base, *_ = _rig(tmp_path)
    var = b.add_tab("variables", "Vars")
    st = b.add_tab("studio", "Use")
    add(st.doc, "variable_studio_ref", source={"kind": "variables", "tab": var.id, "name": "Vars"})
    e = L.dependency_view(b, st.id)["uses"][0]
    assert (e["kind"], e["state"]) == ("Variable Studio reference", "live")
    # Asm pins Base@v1 of Lib: pinned, update available; the Lib side lists nothing from B
    asm = b.add_tab("assembly", "Asm2")
    asm.doc.insert(L.studio_source(base, next(iter(base.doc.state.parts)), document=a.id, version="v2"), name="Base")
    e = L.dependency_view(b, asm.id)["uses"][0]
    assert (e["state"], e["label"], e["document_name"], e["pinned"]) == ("pinned", "pinned v2", "Lib", "v2")
    assert L.dependency_view(a, base.id)["used_by"] == []


def test_removed_tab_is_missing(tmp_path, probe):
    a, b, base, skel, lid, *_ = _rig(tmp_path)
    b.remove_tab(skel.id)
    e = _row(L.dependency_view(b, lid.id)["uses"], "Skeleton")
    assert e["state"] == "missing" and e["tab"] == skel.id


def test_context_state_and_not_a_source(tmp_path):
    from tests.test_context_status import _rig as ctx_rig, _edit_depth
    folder, plate, pin, asm, cid, ip, m = ctx_rig(tmp_path)
    assert "context" not in L.SOURCE_FEATURES
    e = L.dependency_view(folder, plate.id)["uses"][0]
    assert (e["kind"], e["name"], e["state"], e["feature"]) == ("Context", "Asm", "current", cid)
    assert [x["name"] for x in L.dependency_view(folder, asm.id)["used_by"]] == ["Plate"]
    _edit_depth(pin, 12)
    folder.refresh_links()
    assert L.dependency_view(folder, plate.id)["uses"][0]["state"] == "out of date"
    folder.remove_tab(asm.id)
    e = L.dependency_view(folder, plate.id)["uses"][0]
    assert e["state"] == "assembly missing" and e["name"] == "?"


# ------------------------------------------------------------------ offscreen window
pytest.importorskip("PySide6.QtWidgets")
from tests.test_versions_ui import make_window  # noqa: E402,F401
from freeshape.ui import panels as P  # noqa: E402


def test_panel_lists_entries_and_click_switches_tab(make_window, tmp_path, probe):
    w = make_window()
    a, b, base, skel, lid, asm, f_skel, f_base = _rig(tmp_path, R.default_registry())
    b.save()
    assert w.open_document(b.path) is not None
    lid_tab = w.folder.tab(lid.id)
    w.tabs.setCurrentIndex(w._tab_index(lid_tab.doc))
    uses, used_by = w.dependencies.entries()
    assert len(uses) == 2 and "Skeleton" in uses[0] and "live" in uses[0]
    assert "Lib / Base" in uses[1] and "update available v2" in uses[1]
    assert len(used_by) == 1 and "Asm" in used_by[0] and L.ASSEMBLY_INSTANCE in used_by[0]
    w.dependencies.uses_list.itemClicked.emit(w.dependencies.uses_list.item(1))     # another Document: no switch
    assert w.doc is lid_tab.doc
    w.dependencies.uses_list.itemClicked.emit(w.dependencies.uses_list.item(0))
    assert w.doc is w.folder.tab(skel.id).doc
    w.tabs.setCurrentIndex(w._tab_index(lid_tab.doc))
    w.dependencies.used_by_list.itemClicked.emit(w.dependencies.used_by_list.item(0))
    assert w.doc is w.folder.tab(asm.id).doc


def test_feature_row_carries_the_mark(make_window, tmp_path, probe):
    w = make_window()
    a, b, base, skel, lid, asm, f_skel, f_base = _rig(tmp_path, R.default_registry())
    b.save()
    w.open_document(b.path)
    w.tabs.setCurrentIndex(w._tab_index(w.folder.tab(lid.id).doc))
    rows = {w.left.features.item(i).data(P.ROLE_ID): w.left.features.item(i) for i in range(w.left.features.count())}
    assert rows[f_base].data(P.ROLE_MARK) == "update available v2" and "update available v2" in rows[f_base].text()
    assert rows[f_skel].data(P.ROLE_MARK) == "live"
    assert rows[lid.doc.features[0]["id"]].data(P.ROLE_MARK) is None
