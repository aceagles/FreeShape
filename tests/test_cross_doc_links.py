"""I2.7: a link to another Document stores {document id, tab id, part, version} and no path. The resolver finds the
Document through the registry only, reads the pinned Version read-only through the git wrapper, reports "update
available" when the other Document has a newer Version, and re-pins on one click. A received Document is imported
once; its sender's folder is never touched again except by an explicit re-import."""
import os
import shutil

import pytest

from conftest import (FREEFORM, TOP, add, assert_golden, rect_lines, region_refs, shape_fingerprint,
                      sketch_feature)
from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import gitstore as G
from freeshape import links as L

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _box(studio, w=20.0, depth=10.0, x0=0.0):
    sid = sketch_feature(studio, lambda s: rect_lines(s, x0, 0, w, w), TOP)
    add(studio, "extrude", regions=region_refs(studio, sid), depth=depth, op="new")
    assert not studio.errors, studio.errors


def _lib(tmp_path, reg):
    """Document A: one studio with one box, version v1."""
    a = DF.FolderDocument.create(str(tmp_path / "A"), name="A", registry=reg)
    st = a.add_tab("studio", "Plate")
    _box(st.doc)
    a.create_version("v1", "first")
    return a, st, next(iter(st.doc.state.parts))


def _make_v2(a, st):
    ext = next(f for f in st.doc.features if f["type"] == "extrude")
    ext["params"]["depth"] = 25.0          # the linked part itself changes, keeping its id
    st.doc.replace_feature(ext)
    assert not st.doc.errors, st.doc.errors
    a.create_version("v2", "bigger")


def _consumer(tmp_path, reg, a, st, pid, version="v1", name="B"):
    """Document B: an assembly with one instance linked to A's studio at `version`."""
    b = DF.FolderDocument.create(str(tmp_path / name), name=name, registry=reg)
    asm = b.add_tab("assembly", "Asm")
    asm.doc.insert(L.studio_source(st, pid, document=a.id, version=version), name="Plate")
    asm.doc.refresh(solve=False, notify=False)
    return b, asm


def _fp(asm):
    assert not asm.doc.errors, asm.doc.errors
    return {i["id"]: shape_fingerprint(asm.doc.parts[i["id"]].shape) for i in asm.doc.instances}


def _src(asm):
    return asm.doc.instances[0]["source"]


def test_pin_keeps_v1_geometry_reports_update_and_update_repins(tmp_path):
    reg = R.DocRegistry(str(tmp_path / "data" / "documents.json"))
    a, st, pid = _lib(tmp_path, reg)
    b, asm = _consumer(tmp_path, reg, a, st, pid)
    src = _src(asm)
    assert set(src) == {"kind", "tab", "part", "studio_name", "document", "version"}      # no path, no uid
    assert src["document"] == a.id and src["version"] == "v1"
    v1 = _fp(asm)
    assert_golden("cross_doc_links/v1_assembly", v1, FREEFORM)
    assert b.resolver.link_status(src)["update_available"] is False

    _make_v2(a, st)
    asm.doc.refresh(solve=False, notify=False)
    assert _fp(asm) == v1                                  # still v1 geometry after A moved on
    status = b.resolver.link_status(src)
    assert status["update_available"] is True and status["latest"] == "v2" and status["pinned"] == "v1"
    ups = L.cross_link_updates(b)
    assert [(u["document"], u["latest"], u["tab"]) for u in ups] == [(a.id, "v2", asm.id)]

    assert L.update_cross_links(b) == 1
    assert _src(asm)["version"] == "v2" and asm.doc.dirty
    assert b.resolver.link_status(_src(asm)) == {"document": a.id, "pinned": "v2", "latest": "v2",
                                                 "update_available": False, "error": None}
    assert L.cross_link_updates(b) == []
    v2 = _fp(asm)
    assert v2 != v1
    view = a.open_version("v2")
    expect = {pid: shape_fingerprint(p.shape) for pid, p in view.tab(st.id).doc.state.parts.items()}
    assert v2[asm.doc.instances[0]["id"]] == expect[pid]
    assert_golden("cross_doc_links/v2_assembly", v2, FREEFORM)
    assert L.update_cross_links(b) == 0                    # nothing left to update


def test_a_pin_survives_save_and_reopen_and_stores_no_path(tmp_path):
    reg = R.DocRegistry(str(tmp_path / "data" / "documents.json"))
    a, st, pid = _lib(tmp_path, reg)
    b, asm = _consumer(tmp_path, reg, a, st, pid)
    b.save()
    with open(os.path.join(b.path, asm.file)) as fh:
        text = fh.read()
    assert str(tmp_path) not in text                       # the link holds an id, never a folder
    again = DF.FolderDocument.open_document(b.path, registry=reg)
    assert again.resolver.registry is reg
    t = again.tab(asm.id)
    assert not t.doc.errors and _src(t)["version"] == "v1" and _src(t)["document"] == a.id


def test_a_pin_to_a_document_the_app_did_not_create_runs_only_the_one_clone(tmp_path):
    reg = R.DocRegistry(str(tmp_path / "data" / "documents.json"))
    a, st, pid = _lib(tmp_path, R.DocRegistry(str(tmp_path / "sender.json")))     # the app that made it is elsewhere
    src_folder = os.path.realpath(a.path)
    root = str(tmp_path / "managed")
    with G.recording() as calls:
        got = DF.FolderDocument.open_document(src_folder, registry=reg, managed_root=root)
        b, asm = _consumer(tmp_path, reg, got, got.tab(st.id), pid)
        assert not asm.doc.errors and b.resolver.link_status(_src(asm))["update_available"] is False
        _fp(asm)
    touching = [c for c in calls if c["cwd"] and os.path.realpath(c["cwd"]) == src_folder
                or src_folder in c["argv"]]
    assert len(touching) == 1 and touching[0]["argv"][-3:-2] == ["--"] and "--no-local" in touching[0]["argv"]
    assert touching[0]["cwd"] != src_folder                                   # a source argument only
    assert sum("clone" in c["argv"] for c in calls) == 1
    assert reg.trusted_path(a.id) == got.path and os.path.realpath(got.path) != src_folder


def test_a_received_document_sees_a_new_version_only_after_import_again(tmp_path):
    reg = R.DocRegistry(str(tmp_path / "data" / "documents.json"))
    a, st, pid = _lib(tmp_path, R.DocRegistry(str(tmp_path / "sender.json")))
    src_folder = os.path.realpath(a.path)
    root = str(tmp_path / "managed")
    got = DF.FolderDocument.open_document(src_folder, registry=reg, managed_root=root)
    b, asm = _consumer(tmp_path, reg, got, got.tab(st.id), pid)
    v1 = _fp(asm)

    _make_v2(a, st)                                        # the sender makes v2 in the source folder
    with G.recording() as calls:
        asm.doc.refresh(solve=False, notify=False)
        status = b.resolver.link_status(_src(asm))
        assert L.cross_link_updates(b) == []
        assert _fp(asm) == v1
    assert status["update_available"] is False and status["latest"] == "v1"
    assert not any(src_folder in c["argv"] or (c["cwd"] and os.path.realpath(c["cwd"]) == src_folder)
                   for c in calls)

    with G.recording() as calls:
        again = DF.FolderDocument.reimport_document(src_folder, registry=reg, managed_root=root)
    assert sum("clone" in c["argv"] for c in calls) == 1
    assert reg.trusted_path(a.id) == again.path and again.path != got.path and not os.path.exists(got.path)
    assert len(reg.records(a.id)) == 1
    status = b.resolver.link_status(_src(asm))
    assert status["update_available"] is True and status["latest"] == "v2"
    asm.doc.refresh(solve=False, notify=False)
    assert _fp(asm) == v1                                  # the pin still shows v1 until the user updates
    assert L.update_cross_links(b) == 1 and _src(asm)["version"] == "v2"
    assert _fp(asm) != v1


def test_a_link_to_an_unknown_document_id_is_unresolved_and_opens_nothing(tmp_path):
    reg = R.DocRegistry(str(tmp_path / "data" / "documents.json"))
    a, st, pid = _lib(tmp_path, reg)
    b, asm = _consumer(tmp_path, reg, a, st, pid)
    src = _src(asm)
    src["document"] = "no-such-document-id"
    b.resolver._others.clear()
    b.resolver._views.clear()
    with G.recording() as calls:
        asm.doc.refresh(solve=False, notify=False)
        st_ = b.resolver.link_status(src)
        with pytest.raises(Exception, match="no-such-document-id"):
            b.resolver.resolve_source(src)
    inst_id = asm.doc.instances[0]["id"]
    assert "no-such-document-id" in asm.doc.errors[inst_id]
    assert st_["update_available"] is False and "no-such-document-id" in st_["error"]
    assert calls == [] and not b.resolver._others and not b.resolver._views


def test_an_untrusted_registry_entry_is_unresolved_and_opens_nothing(tmp_path):
    reg = R.DocRegistry(str(tmp_path / "data" / "documents.json"))
    a, st, pid = _lib(tmp_path, reg)
    b, asm = _consumer(tmp_path, reg, a, st, pid)
    # a record that names a real folder but carries no trust marker (not created, not a clone of ours)
    reg.documents[a.id] = [{"path": os.path.realpath(a.path), "created": False, "source": None, "clone": None}]
    reg._save()
    assert reg.trusted_path(a.id) is None
    b.resolver._others.clear()
    b.resolver._views.clear()
    with G.recording() as calls:
        asm.doc.refresh(solve=False, notify=False)
        status = b.resolver.link_status(_src(asm))
    assert a.id in asm.doc.errors[asm.doc.instances[0]["id"]] and a.id in status["error"]
    assert status["update_available"] is False and calls == []
    assert not b.resolver._others and not b.resolver._views


def test_a_pin_to_a_version_that_does_not_exist_is_unresolved(tmp_path):
    reg = R.DocRegistry(str(tmp_path / "data" / "documents.json"))
    a, st, pid = _lib(tmp_path, reg)
    b, asm = _consumer(tmp_path, reg, a, st, pid)
    _src(asm)["version"] = "v9"
    asm.doc.refresh(solve=False, notify=False)
    assert "v9" in asm.doc.errors[asm.doc.instances[0]["id"]]
    status = b.resolver.link_status(_src(asm))
    assert "v9" in status["error"] and status["latest"] == "v1"
