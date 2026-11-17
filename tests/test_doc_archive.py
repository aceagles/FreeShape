"""I11.4: a Document as one file, the `.fsdoc` archive (freeshape/doc_archive.py, ADR 0017). Headless.

Each test copies or builds its own Document and uses its own registry, so it passes alone."""
import hashlib
import io
import json
import os
import re
import shutil
import stat
import zipfile

import pytest
from conftest import state_fingerprint
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

from freeshape import doc_archive as A
from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import gitstore as G
from freeshape import kernel as K
from freeshape import step_import

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCUMENTS = os.path.join(ROOT, "examples", "documents")
REFERENCE = os.path.join(ROOT, "docs", "reference", "document-format.md")

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


# =========================================================================== helpers

def _registry(tmp_path):
    return R.DocRegistry(str(tmp_path / "registry.json"))


def _copy(tmp_path, name):
    dst = str(tmp_path / "src" / name)
    shutil.copytree(os.path.join(DOCUMENTS, name), dst)
    return dst


def _parent(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir(exist_ok=True)
    return str(parent)


def _summary(folder, registry=None):
    """[(tab id, kind, name, file, {part or instance id: volume})] of the Document at `folder`, as the desktop's
    document class opens it."""
    fd = DF.FolderDocument.open(folder, registry=registry)
    try:
        assert fd.errors == {}
        out = []
        for t in fd.tabs:
            if t.kind == "studio":
                vols = {p: f["volume"] for p, f in state_fingerprint(t.doc).items()}
            else:
                t.doc.refresh(solve=False, notify=False)
                vols = {}
                for inst in t.doc.instances:
                    shape = t.doc.world_shape(inst["id"])
                    vols[inst["id"]] = None if shape is None else K.volume(shape)
            assert vols and all(v is None or v > 0 for v in vols.values())
            out.append((t.id, t.kind, t.name, t.file, vols))
        return out
    finally:
        fd.close()


def _same(a, b):
    assert [x[:4] for x in a] == [x[:4] for x in b]
    for (*_, va), (*_, vb) in zip(a, b):
        assert va.keys() == vb.keys()
        for k in va:
            assert va[k] == pytest.approx(vb[k], rel=1e-9)


def _zip(entries, marker=True):
    """Zip bytes of `entries` [(name or ZipInfo, bytes)], after the marker unless `marker` is False."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if marker:
            z.writestr(A.MARKER_NAME, json.dumps({"format": A.FORMAT, "version": A.VERSION}))
        for name, data in entries:
            z.writestr(name, data)
    return buf.getvalue()


def _bracket_entries(tmp_path):
    """[(name, bytes)] of the bracket's archive, the marker left out."""
    with zipfile.ZipFile(io.BytesIO(A.pack(_copy(tmp_path, "bracket")))) as z:
        return [(n, z.read(n)) for n in z.namelist() if n != A.MARKER_NAME]


def _refused(tmp_path, data, match):
    """unpack refuses `data` with a message matching `match`, and leaves nothing in the parent or the registry."""
    parent, registry = _parent(tmp_path), _registry(tmp_path)
    with pytest.raises(A.ArchiveError, match=match):
        A.unpack(data, parent, "Received", registry=registry)
    assert os.listdir(parent) == []
    assert registry.list_documents() == []


# =========================================================================== round trips

@pytest.mark.parametrize("name", ["bracket", "hinge"])
def test_pack_then_unpack_opens_with_the_same_tabs_parts_and_volumes(tmp_path, name):
    src = _copy(tmp_path, name)
    parent, registry = _parent(tmp_path), _registry(tmp_path)
    folder = A.unpack(A.pack(src), parent, name, registry=registry)
    assert folder == os.path.join(os.path.realpath(parent), name)
    _same(_summary(folder, registry), _summary(src))
    # a new Document: a fresh id, a repository with one commit, registered as one the app made
    new_id = DF._peek_id(folder)
    assert new_id and new_id != DF._peek_id(src)
    assert G.is_repo(folder) and G.head(folder) is not None and not G.has_changes(folder)
    assert registry.is_trusted(new_id, folder)
    doc = DF.FolderDocument.open_document(folder, registry=registry)       # opens in place, no second import
    assert os.path.realpath(doc.path) == folder
    doc.close()


@pytest.mark.parametrize("name", ["bracket", "hinge"])
def test_packing_twice_gives_the_same_bytes(tmp_path, name):
    src = _copy(tmp_path, name)
    first = A.pack(src)
    os.utime(os.path.join(src, DF.MANIFEST_NAME), (1, 1))         # a newer time on disk changes nothing
    assert A.pack(src) == first
    with zipfile.ZipFile(io.BytesIO(first)) as z:
        names = z.namelist()
        assert names[0] == A.MARKER_NAME and names[1:] == sorted(names[1:])
        assert {i.date_time for i in z.infolist()} == {(1980, 1, 1, 0, 0, 0)}
        assert json.loads(z.read(A.MARKER_NAME)) == {"format": "freeshape-archive", "version": 1}


def test_hinge_carries_every_blob_beside_its_tab_files(tmp_path):
    src = _copy(tmp_path, "hinge")
    blobs = sorted(os.listdir(os.path.join(src, "blobs")))
    assert blobs
    with zipfile.ZipFile(io.BytesIO(A.pack(src))) as z:
        assert sorted(n for n in z.namelist() if n.startswith("blobs/")) == [f"blobs/{k}" for k in blobs]


def test_a_document_with_an_import_blob_round_trips_with_its_blob(tmp_path):
    registry = _registry(tmp_path)
    step = str(tmp_path / "box.step")
    K.export_step([BRepPrimAPI_MakeBox(20.0, 30.0, 40.0).Shape()], step)
    raw = open(step, "rb").read()
    key = hashlib.sha256(raw).hexdigest()
    src = str(tmp_path / "src" / "Imported")
    fd = DF.FolderDocument.create(src, "Imported", registry=registry)
    tab = fd.add_tab("studio", "Box")
    tab.doc.add_feature(step_import.import_feature("box.step", raw, tab.doc))
    assert tab.doc.errors == {}
    fd.save()
    fd.close()
    assert open(os.path.join(src, "blobs", key), "rb").read() == raw
    data = A.pack(src)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert z.read(f"blobs/{key}") == raw
    folder = A.unpack(data, _parent(tmp_path), "Imported", registry=registry)
    assert open(os.path.join(folder, "blobs", key), "rb").read() == raw
    (got,) = _summary(folder, registry)
    assert list(got[4].values()) == [pytest.approx(20.0 * 30.0 * 40.0, rel=1e-9)]
    _same([got], _summary(src, registry))


def test_dot_files_and_unnamed_files_are_left_out_and_gitattributes_is_kept(tmp_path):
    src = _copy(tmp_path, "bracket")
    G.init_repo(src)
    G.commit_all(src, "v")
    for name in (".freeshape-lock", ".freeshape-lock.x.tmp", ".hidden", ".DS_Store", "notes.txt", "stray.fshape"):
        with open(os.path.join(src, name), "w") as fh:
            fh.write("{}")
    os.makedirs(os.path.join(src, ".cache", "blobs"))
    with zipfile.ZipFile(io.BytesIO(A.pack(src))) as z:
        names = z.namelist()
    assert sorted(names) == sorted([A.MARKER_NAME, ".gitattributes", "document.json", "bracket.fshape"])
    assert not any(n.startswith(".git/") or n == ".git" for n in names)


def test_unpack_numbers_the_folder_when_the_name_is_taken(tmp_path):
    data = A.pack(_copy(tmp_path, "bracket"))
    parent, registry = _parent(tmp_path), _registry(tmp_path)
    os.mkdir(os.path.join(parent, "bracket"))
    a = A.unpack(data, parent, "bracket", registry=registry)
    b = A.unpack(data, parent, "bracket", registry=registry)
    assert [os.path.basename(a), os.path.basename(b)] == ["bracket 2", "bracket 3"]
    assert DF._peek_id(a) != DF._peek_id(b)
    assert len(registry.list_documents()) == 2


def test_pack_refuses_a_tab_file_that_is_a_symlink(tmp_path):
    src = _copy(tmp_path, "bracket")
    real = str(tmp_path / "elsewhere.fshape")
    shutil.move(os.path.join(src, "bracket.fshape"), real)
    os.symlink(real, os.path.join(src, "bracket.fshape"))
    with pytest.raises(K.KernelError, match="resolves outside the Document folder"):
        A.pack(src)
    os.remove(os.path.join(src, "bracket.fshape"))              # a symlink to a file inside the folder
    shutil.move(real, os.path.join(src, "real.fshape"))
    os.symlink(os.path.join(src, "real.fshape"), os.path.join(src, "bracket.fshape"))
    with pytest.raises(K.KernelError, match="refusing to open a symlink"):
        A.pack(src)


# =========================================================================== unpack refuses, and leaves nothing

def test_unpack_refuses_an_entry_with_a_dotdot_segment(tmp_path):
    _refused(tmp_path, _zip(_bracket_entries(tmp_path) + [("../x.fshape", b"{}")]), r"'\.\.' segment")


def test_unpack_refuses_an_absolute_entry(tmp_path):
    _refused(tmp_path, _zip(_bracket_entries(tmp_path) + [("/tmp/x.fshape", b"{}")]), "absolute path")


def test_unpack_refuses_a_symlink_entry(tmp_path):
    info = zipfile.ZipInfo("link.fshape")
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    _refused(tmp_path, _zip(_bracket_entries(tmp_path) + [(info, b"/etc/passwd")]), "symlink")


def test_unpack_refuses_an_archive_without_the_marker(tmp_path):
    _refused(tmp_path, _zip(_bracket_entries(tmp_path), marker=False), "no freeshape-archive.json")


def test_unpack_refuses_more_than_5000_entries(tmp_path):
    entries = _bracket_entries(tmp_path) + [(f"t{i}.fshape", b"") for i in range(A.MAX_ENTRIES)]
    assert len(entries) + 1 > A.MAX_ENTRIES
    _refused(tmp_path, _zip(entries), "over the limit of 5000")


def test_unpack_refuses_a_zip_bomb(tmp_path):
    """A 2 GiB run of zeros packs to about 2 MB; it is refused from the zip's directory, before any of it is
    unpacked."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(A.MARKER_NAME, json.dumps({"format": A.FORMAT, "version": A.VERSION}))
        for name, data in _bracket_entries(tmp_path):
            z.writestr(name, data)
        chunk = bytes(1 << 20)
        bomb = zipfile.ZipInfo("blobs/" + "0" * 64)
        bomb.compress_type = zipfile.ZIP_DEFLATED
        with z.open(bomb, "w", force_zip64=True) as fh:
            for _ in range(2048):
                fh.write(chunk)
    data = buf.getvalue()
    assert len(data) < 8 << 20
    _refused(tmp_path, data, "more than 1073741824 bytes")


def test_unpack_refuses_a_compression_ratio_over_200(tmp_path):
    _refused(tmp_path, _zip(_bracket_entries(tmp_path) + [("blobs/" + "0" * 64, bytes(8 << 20))]),
             "more than 200 times its packed size")


@pytest.mark.parametrize("name, match", [
    ("a\\b.fshape", "backslash"),
    ("C:/x.fshape", "drive"),
    ("notes.txt", "matches no file"),
    (".git/config", "matches no file"),
    ("sub/.hidden.fshape", "matches no file"),
    ("blobs/xyz", "matches no file"),
], ids=["backslash", "drive", "txt", "dotgit", "dotfile", "badblob"])
def test_unpack_refuses_a_name_that_matches_no_allowed_pattern(tmp_path, name, match):
    _refused(tmp_path, _zip(_bracket_entries(tmp_path) + [(name, b"{}")]), match)


def test_unpack_refuses_bytes_that_are_not_a_zip_and_a_marker_of_another_format(tmp_path):
    _refused(tmp_path, b"not a zip at all", "not a zip file")
    other = _zip([(n, d) for n, d in _bracket_entries(tmp_path)], marker=False)
    buf = io.BytesIO(other)
    with zipfile.ZipFile(buf, "a") as z:
        z.writestr(A.MARKER_NAME, json.dumps({"format": "something-else", "version": 1}))
    _refused(tmp_path, buf.getvalue(), "another format")


def test_unpack_refuses_a_manifest_naming_a_tab_file_the_archive_lacks(tmp_path):
    entries = [(n, d) for n, d in _bracket_entries(tmp_path) if n != "bracket.fshape"]
    _refused(tmp_path, _zip(entries), "missing")


def test_unpack_refuses_a_name_that_makes_no_folder(tmp_path):
    parent = _parent(tmp_path)
    with pytest.raises(A.ArchiveError, match="Document name"):
        A.unpack(A.pack(_copy(tmp_path, "bracket")), parent, "../..", registry=_registry(tmp_path))
    assert os.listdir(parent) == []


# =========================================================================== the reference

def test_the_reference_has_an_archive_section_naming_the_marker_keys_and_entries():
    text = open(REFERENCE, encoding="utf-8").read()
    m = re.search(r"^## Archive\n(.*?)(?=^## |\Z)", text, flags=re.M | re.S)
    assert m, "docs/reference/document-format.md has no Archive section"
    section = m.group(1)
    for name in (A.MARKER_NAME, "format", "version", A.FORMAT, ".gitattributes", "document.json", A.EXTENSION):
        assert f"`{name}`" in section, name
