"""I2.4: a Document is a git repository. Saving never commits; Create version = one commit + one annotated tag
carrying the note; a version opens read-only with every tab loaded and every link resolved at that commit; a
Document the app created opens in place while a copy of its folder is imported; bad names never reach git."""
import json
import os
import shutil

import pytest

from conftest import (FREEFORM, TOP, add, assert_golden, rect_lines, region_refs, shape_fingerprint,
                      sketch_feature)
from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import gitstore as G
from freeshape import kernel as K
from freeshape import links as L

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _box(studio, w=20.0, depth=10.0, x0=0.0):
    sid = sketch_feature(studio, lambda s: rect_lines(s, x0, 0, w, w), TOP)
    add(studio, "extrude", regions=region_refs(studio, sid), depth=depth, op="new")
    assert not studio.errors, studio.errors


def _commit_count(path):
    return int(G.run(["rev-list", "--count", "HEAD"], path).stdout)


def _new_document(tmp_path, name="Doc"):
    reg = R.DocRegistry(str(tmp_path / "data" / "documents.json"))
    doc = DF.FolderDocument.create(str(tmp_path / name), registry=reg)
    st = doc.add_tab("studio", "Plate")
    _box(st.doc)
    pid = next(iter(st.doc.state.parts))
    asm = doc.add_tab("assembly", "Asm")
    for _ in range(2):
        asm.doc.insert(L.studio_source(st, pid), name="Plate")
    asm.doc.refresh(solve=False, notify=False)
    return reg, doc, st, asm


def _fingerprints(view):
    studio = next(t for t in view.tabs if t.kind == "studio").doc
    asm = next(t for t in view.tabs if t.kind == "assembly").doc
    assert not view.errors and not asm.errors, (view.errors, asm.errors)
    return {"studio": {pid: shape_fingerprint(p.shape) for pid, p in studio.state.parts.items()},
            "assembly": {i["id"]: shape_fingerprint(asm.parts[i["id"]].shape) for i in asm.instances}}


def test_save_never_commits_and_create_version_makes_one_commit_and_one_annotated_tag(tmp_path):
    reg, doc, st, asm = _new_document(tmp_path)
    doc.save()
    assert G.head(doc.path) is None                       # saving made no commit
    doc.save()
    assert G.head(doc.path) is None and doc.versions() == []
    v = doc.create_version("v1", "first cut")
    assert _commit_count(doc.path) == 1
    assert [t["name"] for t in doc.versions()] == ["v1"]
    assert G.run(["cat-file", "-t", "refs/tags/v1"], doc.path).stdout.strip() == "tag"   # annotated, not lightweight
    assert v["note"] == "first cut" and v["commit"] == G.head(doc.path)
    st.doc.dirty = True
    doc.save()
    assert _commit_count(doc.path) == 1                   # still one commit after another save
    doc.create_version("v2", "second")
    assert _commit_count(doc.path) == 2
    assert [t["name"] for t in doc.versions()] == ["v1", "v2"]
    assert [t["note"] for t in doc.versions()] == ["first cut", "second"]


def test_gitattributes_marks_manifest_and_every_tab_pattern_no_merge(tmp_path):
    doc = DF.FolderDocument.create(str(tmp_path / "Doc"), registry=R.DocRegistry(str(tmp_path / "r.json")))
    with open(os.path.join(doc.path, ".gitattributes")) as fh:
        lines = fh.read().split("\n")
    assert "document.json -merge" in lines
    for ext in DF.EXTENSIONS.values():
        assert f"*{ext} -merge" in lines
    out = G.run(["check-attr", "merge", "--", "document.json", "a.fshape", "b.fasm", "c.fdwg", "d.fvars"],
                doc.path).stdout
    assert out.count("merge: unset") == 5, out


def test_open_version_is_read_only_and_matches_the_version(tmp_path):
    reg, doc, st, asm = _new_document(tmp_path)
    doc.create_version("v1", "two plates")
    v1_expected = _fingerprints(doc)
    assert_golden("git_versions/v1_studio", v1_expected["studio"], FREEFORM)
    assert_golden("git_versions/v1_assembly", v1_expected["assembly"], FREEFORM)
    # edit the studio and the assembly, then v2
    _box(st.doc, w=30.0, depth=25.0, x0=50.0)
    pid2 = [p for p in st.doc.state.parts][-1]
    asm.doc.insert(L.studio_source(st, pid2), name="Big")
    asm.doc.refresh(solve=False, notify=False)
    doc.create_version("v2", "added a big plate")
    v2_now = _fingerprints(doc)
    assert v2_now != v1_expected and len(v2_now["assembly"]) == 3

    view = doc.open_version("v1")
    assert view.read_only and view.version["name"] == "v1"
    got = _fingerprints(view)
    assert got["studio"].keys() == v1_expected["studio"].keys()
    assert_golden("git_versions/v1_studio", got["studio"], FREEFORM)
    assert_golden("git_versions/v1_assembly", got["assembly"], FREEFORM)
    assert len(view.tab(asm.id).doc.instances) == 2

    with pytest.raises(K.KernelError, match="read-only"):
        view.save()
    with pytest.raises(K.KernelError, match="read-only"):
        view.add_tab("studio")
    with pytest.raises(K.KernelError, match="read-only"):
        view.remove_tab(st.id)
    with pytest.raises(K.KernelError, match="read-only"):
        view.create_version("v3")
    scratch = view._scratch
    view.close()
    assert not os.path.exists(scratch)
    # the live Document is untouched by opening a version
    assert len(doc.tab(asm.id).doc.instances) == 3 and not doc.read_only
    with pytest.raises(K.KernelError):
        doc.open_version("nope")


def test_created_document_opens_in_place_and_a_copy_is_imported(tmp_path):
    reg, doc, st, asm = _new_document(tmp_path)
    doc.save()
    root = tmp_path / "managed"
    with G.recording() as calls:
        again = DF.FolderDocument.open_document(doc.path, registry=reg, managed_root=str(root))
    assert os.path.realpath(again.path) == os.path.realpath(doc.path)
    assert not any("clone" in c["argv"] for c in calls) and not root.exists()
    assert again.trusted and reg.find(doc.id) == os.path.realpath(doc.path)
    # the trust marker is in the registry, not in the folder
    assert not any("registry" in f or "documents.json" in f for f in os.listdir(doc.path))
    assert not os.path.commonpath([os.path.realpath(doc.path), reg.path]) == os.path.realpath(doc.path)

    copy = tmp_path / "elsewhere" / "Doc copy"
    shutil.copytree(doc.path, copy)
    with G.recording() as calls:
        imported = DF.FolderDocument.open_document(str(copy), registry=reg, managed_root=str(root))
    assert sum("clone" in c["argv"] for c in calls) == 1
    assert os.path.dirname(imported.path) == os.path.realpath(root)
    assert os.path.realpath(imported.path) != os.path.realpath(copy)
    assert imported.id == doc.id and [t.id for t in imported.tabs] == [t.id for t in doc.tabs]
    rec = [r for r in reg.records(doc.id) if r["clone"]]
    assert rec and rec[0]["source"] == os.path.realpath(copy) and rec[0]["created"] is False
    assert reg.find(doc.id) == os.path.realpath(doc.path)        # the created folder stays the one found by id
    # opening the same copy again reuses the clone; reopening the clone itself opens it in place
    with G.recording() as calls:
        DF.FolderDocument.open_document(str(copy), registry=reg, managed_root=str(root))
        DF.FolderDocument.open_document(imported.path, registry=reg, managed_root=str(root))
    assert not any("clone" in c["argv"] for c in calls)


def test_a_received_folder_without_a_repository_is_imported_by_copy(tmp_path):
    reg, doc, st, asm = _new_document(tmp_path)
    doc.save()
    plain = tmp_path / "plain"
    shutil.copytree(doc.path, plain, ignore=shutil.ignore_patterns(".git"))
    got = DF.FolderDocument.open_document(str(plain), registry=reg, managed_root=str(tmp_path / "m"))
    assert os.path.dirname(got.path) == os.path.realpath(tmp_path / "m") and G.head(got.path)
    assert not got.errors


@pytest.mark.parametrize("bad", ["../x", "-x", "--force", "a..b", "x y", "", "  ", "/abs", ".hidden", "a\\b",
                                 "a~b", "x.lock"])
def test_bad_version_names_are_rejected(tmp_path, bad):
    reg, doc, st, asm = _new_document(tmp_path)
    with pytest.raises(K.KernelError):
        doc.create_version(bad, "n")
    assert G.head(doc.path) is None and doc.versions() == []


@pytest.mark.parametrize("bad", ["../x", "-x"])
def test_path_like_and_option_like_names_are_rejected_before_git_runs(tmp_path, bad):
    reg, doc, st, asm = _new_document(tmp_path)
    with G.recording() as calls:
        with pytest.raises(G.GitError):
            doc.create_version(bad, "n")
        with pytest.raises(G.GitError):
            doc.open_version(bad)
    assert calls == []


def test_a_version_needs_a_trusted_document(tmp_path):
    reg, doc, st, asm = _new_document(tmp_path)
    doc.save()
    raw = DF.FolderDocument.open(doc.path)               # the low-level loader makes no trust decision
    with G.recording() as calls:
        for call in (lambda: raw.create_version("v1"), raw.versions, lambda: raw.open_version("v1"),
                     lambda: raw.push(str(tmp_path))):
            with pytest.raises(K.KernelError, match="open_document"):
                call()
    assert calls == []


def test_git_older_than_2_38_is_refused(monkeypatch):
    class R_:
        returncode, stdout, stderr = 0, "git version 2.37.1\n", ""
    monkeypatch.setitem(G._state, "checked", False)
    monkeypatch.setattr(G.subprocess, "run", lambda *a, **k: R_())
    with pytest.raises(G.GitUnavailable):
        G.run(["status"])
