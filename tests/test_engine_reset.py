"""I3.8: a Workspace switch, a merge commit and opening a version read-only each change the files under every
open tab at once. After each, the window rebuilds the Link graph from the Document as it now is and starts every
tab's worker again (the process executor), so that no tab keeps showing the branch that was left.

One offscreen window serves the module. The Document: Part Studios Plate (T1) and Bracket (T2) and Assembly Asm
(T3) holding a part of each; version v1; Workspaces "alt" (Plate deeper, Bracket and its instance removed) and
"beta" (Bracket and its instance removed) made from v1; "main" has Plate edited. What a tab displays is read as
hashes of the shapes the window holds and compared with the hashes the headless in-process engine gives for the
files of the branch that was switched to."""
import os
import shutil
import signal

import pytest

from conftest import TOP, add, rect_lines, region_refs, sketch_feature
from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import links as L
from freeshape.engine import InProcessEngine
from freeshape.engine import process as P
from freeshape.ui.partstudio_engine import geometry_hash

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

pytest.importorskip("PySide6.QtWidgets")
pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402


def _children():
    me, out = os.getpid(), []
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                with open(f"/proc/{d}/stat") as fh:
                    rest = fh.read().rsplit(")", 1)[1].split()
                with open(f"/proc/{d}/cmdline", "rb") as fh:
                    cmd = fh.read()
                if int(rest[1]) == me and rest[0] != "Z" and b"spawn_main" in cmd:
                    out.append(int(d))
            except (OSError, ValueError, IndexError):
                pass
    return out


def _box(studio, size, depth):
    sid = sketch_feature(studio, lambda s: rect_lines(s, 0, 0, size, size), TOP)
    add(studio, "extrude", regions=region_refs(studio, sid), depth=depth, op="new")
    assert not studio.errors, studio.errors


def _depth(fd, tid, depth):
    studio = fd.tab(tid).doc
    f = next(f for f in studio.features if f["type"] == "extrude")
    studio.replace_feature({**f, "params": {**f["params"], "depth": depth}})
    studio.dirty = True


def _drop_bracket(fd):
    """Remove Bracket and its instance from the Assembly (and delete the tab's file)."""
    asm = fd.tab("T3").doc
    asm.delete_instances({i["id"] for i in asm.instances if i["source"].get("tab") == "T2"})
    asm.dirty = True
    file = os.path.join(fd.path, fd.tab("T2").file)
    fd.remove_tab("T2")
    os.remove(file)


def _on(fd, workspace, edit):
    fd.switch_workspace(workspace)
    edit(fd)
    fd.save()


def build_document(root):
    fd = DF.FolderDocument.create(str(root / "Reset"), registry=R.default_registry())
    plate, bracket = fd.add_tab("studio", "Plate"), fd.add_tab("studio", "Bracket")
    _box(plate.doc, 20.0, 10.0)
    _box(bracket.doc, 10.0, 5.0)
    asm = fd.add_tab("assembly", "Asm")
    assert (plate.id, bracket.id, asm.id) == ("T1", "T2", "T3")
    for tab in (plate, bracket):
        asm.doc.insert(L.studio_source(tab, next(iter(tab.doc.state.parts))), name=tab.name)
    asm.doc.refresh(solve=False, notify=False)
    fd.save()
    fd.create_version("v1", "base")
    fd.create_workspace("alt", "v1", switch=False)
    fd.create_workspace("beta", "v1", switch=False)
    _on(fd, "alt", lambda d: (_depth(d, "T1", 30.0), _drop_bracket(d)))
    _on(fd, "beta", _drop_bracket)
    _on(fd, "main", lambda d: _depth(d, "T1", 12.0))
    fd.switch_workspace("beta")                              # leaving "main" commits its saved edit there
    fd.switch_workspace("main")
    path = fd.path
    fd.close()
    return path


def headless(path, geometry=False):
    """{tab id: shown hashes} from the in-process engine over the files of the Document folder `path`
    (`geometry`: hash the shapes with geometry_hash)."""
    fd = DF.FolderDocument.open(path)
    eng = InProcessEngine()
    for t in sorted(fd.tabs, key=lambda t: t.kind != "studio"):
        eng.open_tab(os.path.join(path, t.file), tab=t.id)
    out = {}
    for t in fd.tabs:
        doc = eng._tabs[t.id].doc
        if geometry:
            out[t.id] = ({i["id"]: geometry_hash(doc.parts[i["id"]].shape) for i in doc.instances}
                         if t.kind == "assembly" else {p.id: geometry_hash(p.shape) for p in doc.state.parts.values()})
            continue
        o = eng.outputs(t.id)
        out[t.id] = {i.id: i.part for i in o.instances} if t.kind == "assembly" else {p.id: p.hash for p in o.parts}
    eng.close()
    return out


@pytest.fixture(scope="module")
def win(tmp_path_factory):
    root = tmp_path_factory.mktemp("reset")
    mp = pytest.MonkeyPatch()
    mp.setenv("FREESHAPE_DATA_DIR", str(root / "appdata"))
    app = QApplication.instance() or QApplication([])
    from PySide6.QtCore import QSettings
    from freeshape.ui import home as H
    from freeshape.ui import mainwindow as MW
    os.makedirs(root / "autosave", exist_ok=True)
    os.makedirs(root / "thumbs", exist_ok=True)
    mp.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(root / "autosave")))
    mp.setattr(MW.MainWindow, "autosave_dir", classmethod(lambda cls: str(root / "autosave")))
    mp.setattr(H, "thumbs_dir", lambda: str(root / "thumbs"))
    for name in ("warning", "critical", "information"):
        mp.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.Ok)
    path = build_document(root)
    before = set(_children())
    w = MW.MainWindow(engine=True)
    w.settings = QSettings(str(root / "settings.ini"), QSettings.IniFormat)
    w.open_document(path)
    assert w.studio_engine.settle(240)
    w.path = path
    yield w
    w.versions_ui.close_view()
    w.studio_engine.shutdown()
    P.SPARES.clear()
    left = sorted(set(_children()) - before)
    for p in left:
        try:
            os.kill(p, signal.SIGKILL)
        except OSError:
            pass
    mp.undo()
    assert not left, f"worker processes outlived the window: {left}"


def shown(w, doc):
    return w.studio_engine.live.displayed(doc)


def check_tabs(w, expect):
    """Every shown tab displays what the headless engine gives for the files now in the Document folder, and the
    Link graph names only tabs that exist there: exactly the edges the Document's links make."""
    assert w.studio_engine.settle(240) and not w.studio_engine.broken
    for tid, hashes in expect.items():
        assert shown(w, w.folder.tab(tid).doc) == hashes, tid
    ids = {t.id for t in w.folder.tabs}
    edges = set(w.studio_engine.live.link_edges(w.folder.tab("T3").doc))
    assert all(c in ids and u in ids for c, u in edges), edges
    want = {("T3", t) for t in ("T1", "T2") if t in ids}
    assert edges == want, edges


def test_a_workspace_switch_resets_the_tabs_and_the_link_graph(win):
    w = win
    main = headless(w.path)
    assert set(main) == {"T1", "T2", "T3"} and w.folder.current_workspace() == "main"
    check_tabs(w, main)

    assert w.switch_workspace("alt")
    alt = headless(w.path)
    assert set(alt) == {"T1", "T3"} and alt["T1"] != main["T1"]
    assert w.tabs.count() == 2
    check_tabs(w, alt)                                       # the edge from Asm to Bracket went with Bracket

    assert w.switch_workspace("main")
    check_tabs(w, headless(w.path))
    assert shown(w, w.folder.tab("T1").doc) == main["T1"]


def test_a_merge_resets_the_tabs_and_the_link_graph(win):
    w = win
    assert w.folder.current_workspace() == "main"
    assert w.merge_workspace("beta", choose=lambda preview: True)
    merged = headless(w.path)
    assert set(merged) == {"T1", "T3"}                       # Bracket was removed in beta
    check_tabs(w, merged)
    assert w.tabs.count() == 2


def test_opening_a_version_read_only_leaves_the_graph_and_the_live_tabs_right(win):
    w = win
    live = headless(w.path)
    view = w.versions_ui.open_view("v1")
    assert view is not None and w.studio_engine.settle(240)
    shown_docs = {t.id: t.doc for t in view.tabs if t.doc is not None}
    assert set(shown_docs) == {"T1", "T2", "T3"}
    v1 = headless(view.path, geometry=True)                   # the files of version v1, read from git
    for tid, hashes in v1.items():                            # (these tabs rebuild in the window: compare geometry)
        assert w.studio_engine.live.displayed(shown_docs[tid], geometry=True) == hashes, tid
    assert v1["T1"] != headless(w.path, geometry=True)["T1"]
    ids = {t.id for t in w.folder.tabs}
    edges = set(w.studio_engine.live.link_edges(w.folder.tab("T3").doc))
    assert edges == {("T3", t) for t in ("T1", "T2") if t in ids} and all(c in ids and u in ids for c, u in edges)
    for tid, hashes in live.items():
        assert shown(w, w.folder.tab(tid).doc) == hashes, tid     # the live tabs still show the live branch
    assert w.versions_ui.close_view()
