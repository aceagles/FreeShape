"""FreeShape without git: a Document is made, edited, saved and reopened; only versions and Workspaces refuse,
with a message that says so; and a Document made that way gets its repository once git is there."""
import os

import pytest
from conftest import TOP, add, rect_lines, region_refs, sketch_feature

from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import gitstore as G


@pytest.fixture
def git(monkeypatch):
    """`git(False)` hides git from this process; `git(True)` brings it back."""
    real = os.environ["PATH"]

    def switch(present):
        monkeypatch.setenv("PATH", real if present else "/nonexistent")
        monkeypatch.setitem(G._state, "checked", False)
    yield switch
    G._state["checked"] = False


@pytest.fixture
def registry(tmp_path):
    return R.DocRegistry(str(tmp_path / "data" / "documents.json"))


def _part(fd):
    st = fd.add_tab("studio", "Studio")
    sid = sketch_feature(st.doc, lambda sk: rect_lines(sk, 0, 0, 20, 10), TOP)
    add(st.doc, "extrude", regions=region_refs(st.doc, sid), depth=5, op="new")
    return st


def test_a_document_is_made_saved_and_reopened_without_git(git, registry, tmp_path):
    git(False)
    path = str(tmp_path / "Doc")
    fd = DF.FolderDocument.create(path, "Doc", registry=registry)
    _part(fd)
    fd.save()
    assert not os.path.exists(os.path.join(path, ".git"))
    again = DF.FolderDocument.open_document(path, registry=registry)
    assert [t.name for t in again.tabs] == ["Studio"] and not again.errors
    assert again.tabs[0].doc.state.parts                      # the part rebuilt
    again.tabs[0].doc.dirty = True
    again.save()                                              # saving never needs git
    assert registry.describe(fd.id)["versions"] == []


def test_only_versions_and_workspaces_refuse_and_say_why(git, registry, tmp_path):
    git(False)
    fd = DF.FolderDocument.create(str(tmp_path / "Doc"), "Doc", registry=registry)
    _part(fd)
    fd.save()
    problem = fd.git_problem()
    assert "git is not installed" in problem and "everything else works without it" in problem
    for call in (fd.versions, fd.workspaces, fd.current_workspace, lambda: fd.create_version("v1"),
                 lambda: fd.create_workspace("try")):
        with pytest.raises(G.GitError, match="git is not installed"):
            call()


def test_a_repository_from_another_computer_opens_without_git(git, registry, tmp_path):
    git(True)
    path = str(tmp_path / "Doc")
    fd = DF.FolderDocument.create(path, "Doc", registry=registry)
    _part(fd)
    fd.create_version("v1", "first")
    fd.close()
    git(False)
    again = DF.FolderDocument.open_document(path, registry=registry)
    assert again.tabs[0].doc.state.parts and not again.errors
    again.tabs[0].doc.dirty = True
    again.save()
    assert "git is not installed" in again.git_problem()
    with pytest.raises(G.GitUnavailable):
        again.versions()
    assert registry.describe(fd.id)["versions"] == []         # listed, with no Versions to pick
    with pytest.raises(G.GitUnavailable):
        registry.describe(fd.id, version="v1")                # reading a Version does need git


def test_a_document_made_without_git_gets_versions_once_git_is_there(git, registry, tmp_path):
    git(False)
    path = str(tmp_path / "Doc")
    fd = DF.FolderDocument.create(path, "Doc", registry=registry)
    _part(fd)
    fd.save()
    fd.close()
    git(True)
    again = DF.FolderDocument.open_document(path, registry=registry)
    assert again.git_problem() is None
    assert again.versions() == [] and again.workspaces() == [G.DEFAULT_BRANCH]
    assert again.current_workspace() == G.DEFAULT_BRANCH
    assert not os.path.exists(os.path.join(path, ".git"))     # reading makes nothing
    again.create_version("v1", "first")
    assert os.path.isdir(os.path.join(path, ".git"))
    assert [v["name"] for v in again.versions()] == ["v1"]
    assert again.workspaces() == [G.DEFAULT_BRANCH]


def test_a_git_that_is_too_old_says_so(git, monkeypatch):
    git(True)

    class Old:
        returncode, stdout, stderr = 0, "git version 2.30.2\n", ""
    monkeypatch.setattr(G.subprocess, "run", lambda *a, **k: Old())
    problem = G.problem()
    assert "too old" in problem and "2.30.2" in problem and "2.38 or newer" in problem
