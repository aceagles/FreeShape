"""I2.6: Workspace merge -- merge-tree, a per-tab ours/theirs choice, document.json merged as a union by tab id,
and dangling-link validation of the merged tree before anything is committed.

Every case starts from version v1 of a Document with Part Studios Plate (T1) and Bracket (T2) and an Assembly Asm
(T3) holding one instance of Plate. Workspace "alt" is made from v1; "main" is the one merged into."""
import json
import os
import shutil
import subprocess

import pytest

from conftest import TOP, add, rect_lines, region_refs, sketch_feature, state_fingerprint
from freeshape import doc_folder as DF
from freeshape import doc_merge as DM
from freeshape import doc_registry as R
from freeshape import gitstore as G
from freeshape import links as L

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _box(studio, size, depth):
    sid = sketch_feature(studio, lambda s: rect_lines(s, 0, 0, size, size), TOP)
    add(studio, "extrude", regions=region_refs(studio, sid), depth=depth, op="new")
    assert not studio.errors, studio.errors


def _depth(doc, tid, depth):
    studio = doc.tab(tid).doc
    feature = next(f for f in studio.features if f["type"] == "extrude")
    feature["params"]["depth"] = depth
    studio.replace_feature(feature)
    assert not studio.errors, studio.errors
    studio.dirty = True


def _volume(doc, tid):
    return sum(round(f["volume"], 3) for f in state_fingerprint(doc.tab(tid).doc).values())


def _link(doc, asm_id, studio_id, name):
    studio = doc.tab(studio_id)
    asm = doc.tab(asm_id).doc
    asm.insert(L.studio_source(studio, next(iter(studio.doc.state.parts))), name=name)
    asm.refresh(solve=False, notify=False)
    asm.dirty = True


def _delete_tab(doc, tid):
    file = os.path.join(doc.path, doc.tab(tid).file)
    doc.remove_tab(tid)
    os.remove(file)


@pytest.fixture
def base(tmp_path):
    """The v1 Document, with Workspace "alt" made from v1 and "main" checked out."""
    registry = R.DocRegistry(str(tmp_path / "app" / "documents.json"))
    doc = DF.FolderDocument.create(str(tmp_path / "Doc"), registry=registry)
    plate = doc.add_tab("studio", "Plate")
    _box(plate.doc, 20.0, 10.0)
    bracket = doc.add_tab("studio", "Bracket")
    _box(bracket.doc, 10.0, 5.0)
    asm = doc.add_tab("assembly", "Asm")
    assert (plate.id, bracket.id, asm.id) == ("T1", "T2", "T3")
    _link(doc, "T3", "T1", "Plate")
    doc.save()
    doc.create_version("v1", "base")
    doc.create_workspace("alt", "v1", switch=False)
    return doc


def _on(doc, workspace, edit):
    """Make `edit(doc)` in `workspace` and save it (the next switch or merge commits it there)."""
    doc.switch_workspace(workspace)
    edit(doc)
    doc.save()


def _check_merged(doc):
    """Every tab file loads and rebuilds, no file holds a conflict marker, and the merged Document opens clean."""
    for root, dirs, files in os.walk(doc.path):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in files:
            with open(os.path.join(root, f), "rb") as fh:
                assert b"<<<<<<<" not in fh.read(), f
    fresh = DF.FolderDocument.open(doc.path)
    assert not fresh.errors and fresh.warnings == []
    for tab in fresh.tabs:
        with open(os.path.join(doc.path, tab.file)) as fh:
            assert json.load(fh)["_tab_id"] == tab.id
        if tab.kind == "studio":
            tab.doc.rebuild()
            assert not tab.doc.errors and tab.doc.state.parts
        elif tab.kind == "assembly":
            tab.doc.refresh(solve=False, notify=False)
            assert not tab.doc.errors, tab.doc.errors
    assert not G.has_changes(doc.path)
    parents = G.run(["rev-list", "--parents", "-n", "1", "HEAD"], doc.path).stdout.split()
    assert len(parents) == 3                       # a merge commit: HEAD and the other Workspace
    return fresh


# ---------------------------------------------------------------------- the matrix
def test_tab_changed_on_one_side_only_is_taken_without_asking(base):
    doc = base
    _on(doc, "alt", lambda d: _depth(d, "T1", 30.0))
    _on(doc, "main", lambda d: _depth(d, "T2", 8.0))
    p = doc.merge_preview("alt")
    assert p.conflicts == [] and p.clean
    assert {a["key"]: a["side"] for a in p.auto} == {"T1": "theirs", "T2": "ours"}
    assert doc.merge_workspace("alt")
    assert _volume(doc, "T1") == 20 * 20 * 30 and _volume(doc, "T2") == 10 * 10 * 8
    assert doc.current_workspace() == "main"
    _check_merged(doc)


@pytest.mark.parametrize("side,depth", [("ours", 15.0), ("theirs", 30.0)])
def test_tab_changed_on_both_sides_takes_the_chosen_side(base, side, depth):
    doc = base
    _on(doc, "alt", lambda d: _depth(d, "T1", 30.0))
    _on(doc, "main", lambda d: _depth(d, "T1", 15.0))
    p = doc.merge_preview("alt")                             # commits main's saved edit first, like a switch
    head = G.head(doc.path)
    assert [c.key for c in p.conflicts] == ["T1"] and "changed" in p.conflicts[0].detail
    assert "Plate.fshape" in p.git_conflicts                 # git's merge-tree found it
    assert not p.clean and "Plate" in p.blocking[0]
    with pytest.raises(DM.MergeRefused):                     # no choice made: nothing is committed
        doc.merge_workspace("alt")
    assert G.head(doc.path) == head
    assert doc.merge_workspace("alt", {"T1": side})
    assert _volume(doc, "T1") == 20 * 20 * depth
    _check_merged(doc)


def test_tab_added_on_each_side_keeps_both(base):
    """Both Workspaces add a tab and both get id T4: the theirs one is re-keyed, and Asm's link to it follows."""
    doc = base

    def alt(d):
        t = d.add_tab("studio", "AltNew")
        _box(t.doc, 6.0, 6.0)
        _link(d, "T3", t.id, "AltNew")

    def main(d):
        _box(d.add_tab("studio", "MainNew").doc, 4.0, 4.0)

    _on(doc, "alt", alt)
    _on(doc, "main", main)
    p = doc.merge_preview("alt")
    assert p.conflicts == [] and p.clean, p.blocking
    assert p.renamed == {"T4": "T5"}
    doc.merge_workspace("alt")
    names = {t.id: t.name for t in doc.tabs}
    assert names == {"T1": "Plate", "T2": "Bracket", "T3": "Asm", "T4": "MainNew", "T5": "AltNew"}
    assert _volume(doc, "T4") == 4 * 4 * 4 and _volume(doc, "T5") == 6 * 6 * 6
    asm = doc.tab("T3").doc
    assert [i["source"]["tab"] for i in asm.instances] == ["T1", "T5"]
    _check_merged(doc)


def test_tab_added_on_each_side_with_the_same_file_name(base):
    doc = base
    _on(doc, "alt", lambda d: _box(d.add_tab("studio", "Extra").doc, 6.0, 6.0))
    _on(doc, "main", lambda d: _box(d.add_tab("studio", "Extra").doc, 4.0, 4.0))
    p = doc.merge_preview("alt")
    assert p.conflicts == [] and p.clean, p.blocking
    doc.merge_workspace("alt")
    files = sorted(t.file for t in doc.tabs if t.name == "Extra")
    assert files == ["Extra 2.fshape", "Extra.fshape"]
    assert sorted(_volume(doc, t.id) for t in doc.tabs if t.name == "Extra") == [64, 216]
    _check_merged(doc)


def test_manifest_changed_on_both_sides_is_a_union(base):
    doc = base

    def rename(tid, name):
        def edit(d):
            d.tab(tid).name = d.tab(tid).doc.name = name
            d.tab(tid).doc.dirty = True
        return edit

    _on(doc, "alt", rename("T2", "Bracket alt"))
    _on(doc, "main", rename("T1", "Plate main"))
    p = doc.merge_preview("alt")
    assert "document.json" in p.git_conflicts                # git sees the manifest conflict ...
    assert p.conflicts == [] and p.clean                     # ... the union by tab id resolves it
    doc.merge_workspace("alt")
    assert [(t.id, t.name) for t in doc.tabs] == [("T1", "Plate main"), ("T2", "Bracket alt"), ("T3", "Asm")]
    _check_merged(doc)


def test_tab_deleted_on_one_side_while_the_other_links_it_is_refused(base):
    doc = base
    _on(doc, "alt", lambda d: _link(d, "T3", "T2", "Bracket"))
    _on(doc, "main", lambda d: _delete_tab(d, "T2"))
    p = doc.merge_preview("alt")
    head = G.head(doc.path)
    assert p.conflicts == []
    assert [(w["problem"], w["tab"], w["target"]) for w in p.warnings] == [("dangling-link", "T3", "T2")]
    assert "Asm" in p.problems[0] and "T2" in p.problems[0] and "Bracket" in p.problems[0]
    assert not p.clean
    with pytest.raises(DM.MergeRefused) as e:
        doc.merge_workspace("alt")
    assert "Asm" in str(e.value)
    assert G.head(doc.path) == head and doc.current_workspace() == "main" and not G.has_changes(doc.path)


def test_tab_changed_on_one_side_and_deleted_on_the_other_is_a_choice(base):
    doc = base
    _on(doc, "alt", lambda d: _depth(d, "T2", 7.0))
    _on(doc, "main", lambda d: _delete_tab(d, "T2"))
    p = doc.merge_preview("alt")
    assert [c.key for c in p.conflicts] == ["T2"] and "deleted" in p.conflicts[0].detail
    doc.merge_workspace("alt", {"T2": "theirs"})
    assert _volume(doc, "T2") == 10 * 10 * 7
    _check_merged(doc)


def test_nothing_to_merge(base):
    doc = base
    _on(doc, "main", lambda d: _depth(d, "T1", 12.0))
    p = doc.merge_preview("alt")                             # alt is still v1, already in main
    assert p.up_to_date and not p.clean
    assert doc.merge_workspace("alt") is None


# ---------------------------------------------------------------------- stock git
def plain(args, cwd, home):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "HOME": str(home),
                "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@t"})
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)


def test_stock_git_merge_leaves_a_whole_file_conflict(base, tmp_path):
    doc = base
    _on(doc, "alt", lambda d: _depth(d, "T1", 30.0))
    _on(doc, "main", lambda d: _depth(d, "T1", 15.0))
    G.commit_all(doc.path, "main edit")
    clone = tmp_path / "plain"
    r = plain(["clone", "--quiet", "--no-local", doc.path, str(clone)], str(tmp_path), tmp_path)
    assert r.returncode == 0, r.stderr
    r = plain(["merge", "origin/alt"], str(clone), tmp_path)
    assert r.returncode == 1, (r.stdout, r.stderr)
    status = plain(["status", "--porcelain"], str(clone), tmp_path).stdout.splitlines()
    assert "UU Plate.fshape" in status
    text = (clone / "Plate.fshape").read_text()
    assert "<<<<<<<" not in text and ">>>>>>>" not in text
    json.loads(text)                                          # still one whole file: ours


# ---------------------------------------------------------------------- the preview in Qt
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"


def test_preview_dialog_blocks_until_clean(base, tmp_path, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from freeshape.ui import home as H
    from freeshape.ui import mainwindow as MW
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(tmp_path / "autosave")))
    monkeypatch.setattr(H, "thumbs_dir", lambda: str(tmp_path / "thumbs"))
    os.makedirs(tmp_path / "autosave", exist_ok=True)
    os.makedirs(tmp_path / "thumbs", exist_ok=True)
    doc = base
    _on(doc, "alt", lambda d: (_depth(d, "T1", 30.0), _link(d, "T3", "T2", "Bracket")))
    _on(doc, "main", lambda d: (_depth(d, "T1", 15.0), _delete_tab(d, "T2")))
    w = MW.MainWindow()
    try:
        w.settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
        app.processEvents()
        p = doc.merge_preview("alt")
        dlg = w._merge_preview_dialog(p)
        assert not dlg.merge_button.isEnabled()
        assert "Plate" in dlg.problems_label.text() and "Asm" in dlg.problems_label.text()
        dlg.radios[("T1", "theirs")].setChecked(True)
        assert p.choices == {"T1": "theirs"}
        assert "Choose" not in dlg.problems_label.text() and "Asm" in dlg.problems_label.text()
        assert not dlg.merge_button.isEnabled()              # the dangling link still blocks it
        dlg.deleteLater()
    finally:
        w.deleteLater()
