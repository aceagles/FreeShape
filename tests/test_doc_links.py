"""I2.2: inside a folder Document, links are stored by tab id and resolved through the manifest; legacy uid + path
links are converted once on open; a legacy path outside the Document folder is never read."""
import json
import os
import shutil

import pytest

from conftest import (EXAMPLES_DIR, FREEFORM, TOP, add, assert_golden, rect_lines, region_refs, shape_fingerprint,
                      sketch_feature)
from freeshape import doc_folder as DF
from freeshape import links as L
from freeshape.assembly import AssemblyDoc
from freeshape.document import Document
from freeshape.drawing import DrawingDoc

HINGE_ALIASES = {"F2:1": "F2/1", "F4:2": "F4/1", "F6:3": "F6/1"}


def _box_studio(studio):
    sid = sketch_feature(studio, lambda s: rect_lines(s, 0, 0, 20, 20), TOP)
    add(studio, "extrude", regions=region_refs(studio, sid), depth=10.0, op="new")
    assert not studio.errors, studio.errors
    return next(iter(studio.state.parts))


def _write_manifest(folder, tabs):
    with open(folder / DF.MANIFEST_NAME, "w") as fh:
        json.dump({"format": DF.FORMAT, "version": DF.FORMAT_VERSION, "id": "D1", "name": "Doc", "tabs": tabs}, fh)


def _hinge_document(tmp_path):
    """A Document folder holding the v1 hinge examples (uid + path links, old part ids)."""
    folder = tmp_path / "Hinge"
    folder.mkdir()
    for name in ("hinge_parts.fshape", "hinge.fasm"):
        shutil.copy(os.path.join(EXAMPLES_DIR, name), folder / name)
    _write_manifest(folder, [{"id": "T1", "kind": "studio", "file": "hinge_parts.fshape", "name": "Hinge parts"},
                             {"id": "T2", "kind": "assembly", "file": "hinge.fasm", "name": "Hinge"}])
    return folder


def _linked_document(tmp_path, save_studio=True):
    doc = DF.FolderDocument.create(str(tmp_path / "Doc"))
    st = doc.add_tab("studio", "Plate")
    pid = _box_studio(st.doc)
    asm = doc.add_tab("assembly", "Asm")
    for _ in range(2):
        asm.doc.insert(L.studio_source(st, pid), name="Plate")
    asm.doc.refresh(solve=False, notify=False)
    if save_studio:
        doc.save()
    return doc, st, asm, pid


def test_v1_assembly_migrates_to_tab_ids_in_one_save(tmp_path):
    folder = _hinge_document(tmp_path)
    doc = DF.FolderDocument.open(str(folder))
    assert not doc.errors, doc.errors
    asm = doc.tab("T2").doc
    assert not asm.errors, asm.errors
    assert len(asm.parts) == 3
    doc.save()
    with open(folder / "hinge.fasm") as fh:
        saved = json.load(fh)
    for inst in saved["instances"]:
        src = inst["source"]
        assert src["tab"] == "T1" and src["kind"] == "studio"
        assert not {"uid", "path", "relpath"} & set(src)
    assert {i["source"]["part"] for i in saved["instances"]} == set(HINGE_ALIASES.values())
    back = DF.FolderDocument.open(str(folder))
    assert not back.tab("T2").doc.errors
    assert_golden("doc_links/hinge_in_document",
                  {i["id"]: shape_fingerprint(back.tab("T2").doc.parts[i["id"]].shape)
                   for i in back.tab("T2").doc.instances}, FREEFORM)


def test_renamed_studio_file_still_resolves(tmp_path):
    doc, st, asm, pid = _linked_document(tmp_path)
    os.rename(os.path.join(doc.path, st.file), os.path.join(doc.path, "Renamed.fshape"))
    st.file = "Renamed.fshape"
    doc.save()
    back = DF.FolderDocument.open(doc.path)
    assert not back.errors, back.errors
    a = back.tab(asm.id).doc
    assert not a.errors, a.errors
    assert len(a.parts) == 2 and all(i["id"] in a.parts for i in a.instances)


def test_link_to_never_saved_studio_carries_tab_id_and_no_path(tmp_path):
    doc, st, asm, pid = _linked_document(tmp_path, save_studio=False)
    assert st.doc.path is None
    for inst in asm.doc.instances:
        src = inst["source"]
        assert src["tab"] == st.id
        assert "path" not in src and "uid" not in src
    assert not asm.doc.errors, asm.doc.errors
    assert len(asm.doc.parts) == 2
    doc.save()                                            # the studio is saved for the first time
    asm.doc.refresh(solve=False, notify=False)
    assert not asm.doc.errors
    os.rename(os.path.join(doc.path, st.file), os.path.join(doc.path, "Moved.fshape"))
    st.file = "Moved.fshape"
    doc.save()
    back = DF.FolderDocument.open(doc.path)
    a = back.tab(asm.id).doc
    assert not a.errors, a.errors
    assert len(a.parts) == 2
    L.repair_links(back.resolver, a)
    assert all("path" not in i["source"] and i["source"]["tab"] == st.id for i in a.instances)


def test_removed_source_tab_is_named_in_the_error(tmp_path):
    doc, st, asm, pid = _linked_document(tmp_path)
    drawing = doc.add_tab("drawing", "Dwg")
    drawing.doc.source = L.studio_source(st, pid)
    doc.remove_tab(st.id)
    asm.doc.parts.clear()
    asm.doc._fallback.clear()
    for inst in asm.doc.instances:
        inst.pop("_brep_blob", None)
    doc.refresh_links()
    assert len(asm.doc.errors) == 2
    for msg in asm.doc.errors.values():
        assert st.id in msg and "no longer in the Document" in msg
    assert asm.doc.parts == {}
    drawing.doc.refresh(notify=False)
    assert st.id in drawing.doc.error


def test_drawing_source_resolves_by_tab_id(tmp_path):
    doc, st, asm, pid = _linked_document(tmp_path)
    shape = doc.resolver.resolve_drawing_source(L.studio_source(st, pid))
    assert shape is st.doc.state.parts[pid].shape
    assert doc.resolver.resolve_drawing_source(L.assembly_source(asm)) is not None


def test_loose_hinge_example_still_resolves_by_uid_and_path():
    r = L.LinkResolver()
    a = AssemblyDoc.load(os.path.join(EXAMPLES_DIR, "hinge.fasm"), resolver=r.resolve_source)
    assert not a.errors, a.errors
    assert len(a.parts) == 3
    assert all("uid" in i["source"] and "tab" not in i["source"] for i in a.instances)


def test_legacy_link_outside_the_folder_is_unresolved_and_never_opened(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    shutil.copy(os.path.join(EXAMPLES_DIR, "hinge_parts.fshape"), outside / "secret.fshape")
    doc = DF.FolderDocument.create(str(tmp_path / "Doc"))
    asm = doc.add_tab("assembly", "Asm")
    target = str(outside / "secret.fshape")
    src = {"kind": "studio", "uid": "00000000000000000000000000000000", "part": "F2/1", "path": target,
           "studio_name": "Secret"}
    asm.doc.instances.append({"id": "I1", "name": "Leak", "source": src, "T": [[1, 0, 0, 0], [0, 1, 0, 0],
                                                                                [0, 0, 1, 0], [0, 0, 0, 1]]})
    opened = []
    real_load = doc.resolver._load_document
    doc.resolver._load_document = lambda p: (opened.append(p), real_load(p))[1]
    real_open = open
    seen = []

    def spy(file, *a, **k):
        seen.append(str(file))
        return real_open(file, *a, **k)

    import builtins
    builtins.open, saved = spy, builtins.open
    try:
        asm.doc.refresh(solve=False, notify=False)
        with pytest.raises(Exception) as e:
            doc.resolver.resolve_source(src)
    finally:
        builtins.open = saved
    assert target in asm.doc.errors["I1"]
    assert target in str(e.value)
    assert not opened
    assert not any("secret.fshape" in f for f in seen)
    assert "I1" not in asm.doc.parts
    # a relative escape is refused the same way
    src2 = dict(src, path="../outside/secret.fshape")
    with pytest.raises(Exception, match="outside the Document folder"):
        doc.resolver.resolve_source(src2)
