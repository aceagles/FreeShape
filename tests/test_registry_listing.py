"""I2.1: the registry lists the trusted Documents, and describes one's tabs at the Workspace or at a Version
(read through git, no checkout) plus its Versions newest first."""
import shutil

import pytest

from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import gitstore as G

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _reg(tmp_path):
    return R.DocRegistry(str(tmp_path / "data" / "documents.json"))


def _make(tmp_path, reg, folder, name):
    doc = DF.FolderDocument.create(str(tmp_path / folder), name=name, registry=reg)
    doc.add_tab("studio", "Plate")
    doc.save()
    return doc


def _version(doc, name):
    G.commit_all(doc.path, name)
    G.tag(doc.path, name, name)


def test_lists_two_documents_with_names_and_describes_tabs(tmp_path):
    reg = _reg(tmp_path)
    a = _make(tmp_path, reg, "a", "Alpha")
    b = _make(tmp_path, reg, "b", "Beta")
    listed = reg.list_documents()
    assert [(d["name"], d["missing"]) for d in listed] == [("Alpha", False), ("Beta", False)]
    assert {d["id"] for d in listed} == {a.id, b.id}
    d = reg.describe(a.id)
    assert [(t["kind"], t["name"]) for t in d["tabs"]] == [("studio", "Plate")]
    assert all(t["id"] for t in d["tabs"])
    assert d["versions"] == []


def test_describe_version_reads_the_tabs_at_that_version(tmp_path):
    reg = _reg(tmp_path)
    doc = _make(tmp_path, reg, "a", "Alpha")
    _version(doc, "v1")
    doc.add_tab("assembly", "Asm")
    doc.save()
    _version(doc, "v2")
    doc.add_tab("studio", "Later")
    doc.save()                                   # uncommitted on the Workspace
    head_before = G.head(doc.path)
    d1 = reg.describe(doc.id, "v1")
    assert [t["name"] for t in d1["tabs"]] == ["Plate"]
    assert [t["name"] for t in reg.describe(doc.id, "v2")["tabs"]] == ["Plate", "Asm"]
    now = reg.describe(doc.id)
    assert [t["name"] for t in now["tabs"]] == ["Plate", "Asm", "Later"]
    assert now["versions"] == ["v2", "v1"]
    assert G.head(doc.path) == head_before and G.has_changes(doc.path)   # nothing was checked out
    with pytest.raises(R.K.KernelError):
        reg.describe(doc.id, "nope")


def test_missing_folder_is_listed_and_describe_raises(tmp_path):
    reg = _reg(tmp_path)
    doc = _make(tmp_path, reg, "a", "Alpha")
    shutil.rmtree(doc.path)
    (row,) = reg.list_documents()
    assert row["id"] == doc.id and row["missing"] is True
    with pytest.raises(R.DocumentMissing, match="folder is missing"):
        reg.describe(doc.id)


def test_untrusted_folder_is_never_listed(tmp_path):
    reg = _reg(tmp_path)
    _make(tmp_path, reg, "a", "Alpha")
    (tmp_path / "data" / "documents.json").write_text(
        '{"version":1,"documents":{"x":[{"path":"%s","created":false,"source":null,"clone":null}]}}'
        % (tmp_path / "a"))
    other = DF.FolderDocument.create(str(tmp_path / "loose"), name="Loose", registry=_reg(tmp_path / "scratch"))
    reg2 = R.DocRegistry(reg.path)
    assert [d["id"] for d in reg2.list_documents()] == []      # "x" is registered but not trusted
    assert other.id not in [d["id"] for d in reg2.list_documents()]
    with pytest.raises(R.DocumentMissing):
        reg2.describe("x")
