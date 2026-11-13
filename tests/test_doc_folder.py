"""The folder-level Document: document.json create/open/save, stable tab ids, and refusing untrusted
manifest content (path escapes and symlinks)."""
import json
import os

import pytest

from freeshape import doc_folder as DF
from freeshape import kernel as K


def _make_three_tab_doc(tmp_path):
    doc = DF.FolderDocument.create(str(tmp_path / "MyDoc"))
    doc.add_tab("studio")
    doc.add_tab("assembly")
    doc.add_tab("drawing")
    doc.save()
    return doc


# =========================================================================== create / open / save
def test_create_writes_manifest_with_three_tabs(tmp_path):
    doc = _make_three_tab_doc(tmp_path)
    manifest_path = os.path.join(doc.path, DF.MANIFEST_NAME)
    with open(manifest_path) as fh:
        data = json.load(fh)
    assert set(data.keys()) == {"format", "version", "id", "name", "tabs"}
    assert data["format"] == DF.FORMAT
    assert len(data["tabs"]) == 3
    ids = [t["id"] for t in data["tabs"]]
    assert len(set(ids)) == 3
    for t in data["tabs"]:
        assert os.path.exists(os.path.join(doc.path, t["file"]))


def test_reopen_preserves_tab_ids(tmp_path):
    doc = _make_three_tab_doc(tmp_path)
    original_ids = {t.id for t in doc.tabs}
    back = DF.FolderDocument.open(doc.path)
    assert not back.errors, back.errors
    assert {t.id for t in back.tabs} == original_ids
    assert back.id == doc.id
    for t in back.tabs:
        assert t.doc is not None


def test_save_with_one_dirty_tab_rewrites_only_that_file(tmp_path):
    doc = _make_three_tab_doc(tmp_path)
    back = DF.FolderDocument.open(doc.path)
    paths = [os.path.join(back.path, t.file) for t in back.tabs]
    mtimes_before = [os.path.getmtime(p) for p in paths]

    back.tabs[1].doc.dirty = True
    back.save()

    mtimes_after = [os.path.getmtime(p) for p in paths]
    assert mtimes_after[1] != mtimes_before[1]
    assert mtimes_after[0] == mtimes_before[0]
    assert mtimes_after[2] == mtimes_before[2]


def test_rename_studio_tab_does_not_change_its_id(tmp_path):
    doc = DF.FolderDocument.create(str(tmp_path / "MyDoc"))
    tab = doc.add_tab("studio")
    doc.save()
    tid = tab.id

    tab.file = "Renamed.fshape"
    os.rename(os.path.join(doc.path, "Part Studio 1.fshape"), os.path.join(doc.path, tab.file))
    tab.doc.dirty = True
    doc.save()

    back = DF.FolderDocument.open(doc.path)
    assert back.tabs[0].id == tid
    assert back.tabs[0].file == "Renamed.fshape"


# =========================================================================== untrusted manifest: path escapes
def _write_manifest(folder, tabs):
    with open(os.path.join(folder, DF.MANIFEST_NAME), "w") as fh:
        json.dump({"format": DF.FORMAT, "version": 1, "id": "docid", "name": "Doc", "tabs": tabs}, fh)


def test_dotdot_escape_is_refused_and_target_never_opened(tmp_path, monkeypatch):
    folder = tmp_path / "Doc"
    folder.mkdir()
    outside = tmp_path / "outside.fshape"
    from freeshape.document import Document
    Document().save(str(outside))                          # a *valid* studio sitting at the target
    _write_manifest(str(folder), [{"id": "T1", "kind": "studio", "file": "../outside.fshape", "name": "X"}])

    calls = []
    monkeypatch.setitem(DF.LOADERS, "studio", lambda p: calls.append(p))

    back = DF.FolderDocument.open(str(folder))
    assert not calls                                        # never opened
    assert back.tabs[0].doc is None
    assert "T1" in back.errors
    assert "T1" in back.errors["T1"]


def test_absolute_path_escape_is_refused_and_target_never_opened(tmp_path, monkeypatch):
    folder = tmp_path / "Doc"
    folder.mkdir()
    outside = tmp_path / "abs.fshape"
    from freeshape.document import Document
    Document().save(str(outside))
    _write_manifest(str(folder), [{"id": "T1", "kind": "studio", "file": str(outside), "name": "X"}])

    calls = []
    monkeypatch.setitem(DF.LOADERS, "studio", lambda p: calls.append(p))

    back = DF.FolderDocument.open(str(folder))
    assert not calls
    assert back.tabs[0].doc is None
    assert "T1" in back.errors


# =========================================================================== untrusted manifest: symlinks
@pytest.mark.skipif(not hasattr(os, "symlink"), reason="platform has no symlink support")
def test_symlinked_tab_file_is_refused(tmp_path, monkeypatch):
    folder = tmp_path / "Doc"
    folder.mkdir()
    outside = tmp_path / "secret.fshape"
    from freeshape.document import Document
    Document().save(str(outside))
    os.symlink(str(outside), str(folder / "Link.fshape"))
    _write_manifest(str(folder), [{"id": "T1", "kind": "studio", "file": "Link.fshape", "name": "X"}])

    calls = []
    monkeypatch.setitem(DF.LOADERS, "studio", lambda p: calls.append(p))

    back = DF.FolderDocument.open(str(folder))
    assert not calls
    assert back.tabs[0].doc is None
    assert "T1" in back.errors


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="platform has no symlink support")
def test_symlinked_manifest_is_refused(tmp_path):
    folder = tmp_path / "Doc"
    folder.mkdir()
    real_manifest = tmp_path / "real_document.json"
    _write_manifest(str(tmp_path), [])
    os.rename(str(tmp_path / DF.MANIFEST_NAME), str(real_manifest))
    os.symlink(str(real_manifest), str(folder / DF.MANIFEST_NAME))

    with pytest.raises(K.KernelError, match="document.json"):
        DF.FolderDocument.open(str(folder))


# =========================================================================== write safety
@pytest.mark.skipif(not hasattr(os, "symlink"), reason="platform has no symlink support")
def test_save_through_a_symlinked_tab_file_does_not_touch_the_outside_file(tmp_path):
    doc = DF.FolderDocument.create(str(tmp_path / "MyDoc"))
    tab = doc.add_tab("studio")
    doc.save()

    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside-bytes")
    tab_path = os.path.join(doc.path, tab.file)
    os.remove(tab_path)
    os.symlink(str(outside), tab_path)

    tab.doc.dirty = True
    doc.save()

    assert outside.read_bytes() == b"outside-bytes"
    assert not os.path.islink(tab_path)
    leftovers = [f for f in os.listdir(doc.path) if f.endswith(".tmp")]
    assert leftovers == []
