"""I2.3: Home creates and opens folder Documents, tabs come from the manifest, recents / thumbnails / autosave are
keyed by Document id + tab id, and Add to Document copies loose files in with their links rewritten to tab ids.
Headless: a real MainWindow on the offscreen Qt platform."""
import json
import os
import shutil

import pytest

from conftest import EXAMPLES_DIR, FREEFORM, TOP, add, rect_lines, region_refs, sketch_feature

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

pytest.importorskip("PySide6")
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton  # noqa: E402

from freeshape import doc_folder as DF  # noqa: E402
from freeshape import links as L  # noqa: E402
from freeshape.assembly import AssemblyDoc  # noqa: E402
from freeshape.ui import home as H  # noqa: E402
from freeshape.ui import mainwindow as MW  # noqa: E402

HINGE_PARTS = os.path.join(EXAMPLES_DIR, "hinge_parts.fshape")
HINGE_ASM = os.path.join(EXAMPLES_DIR, "hinge.fasm")


@pytest.fixture
def make_window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(tmp_path / "autosave")))
    os.makedirs(tmp_path / "autosave", exist_ok=True)
    monkeypatch.setattr(H, "thumbs_dir", lambda: str(tmp_path / "thumbs"))
    os.makedirs(tmp_path / "thumbs", exist_ok=True)
    made = []

    def make():
        w = MW.MainWindow()
        w.settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
        monkeypatch.setattr(MW.MainWindow, "autosave_dir", classmethod(lambda cls: _dir(tmp_path, "pid")))
        app.processEvents()
        made.append(w)
        return w

    yield make
    for w in made:
        w.folder = None
        w.deleteLater()


def _dir(tmp_path, name):
    d = str(tmp_path / "autosave" / name)
    os.makedirs(d, exist_ok=True)
    return d


def _box(studio):
    sid = sketch_feature(studio, lambda s: rect_lines(s, 0, 0, 20, 20), TOP)
    add(studio, "extrude", regions=region_refs(studio, sid), depth=10.0, op="new")
    assert not studio.errors, studio.errors
    return next(iter(studio.state.parts))


def _tab_titles(w):
    return [w.tabs.tabText(i).rstrip(" •") for i in range(w.tabs.count())]


def _new_document_through_home(w, path):
    w._pick_new_document_path = lambda: path
    w.home.newDocument.emit()                      # the Home page's New Document action
    assert w.folder is not None and w.folder.path == path


def _resolved(asm):
    assert not asm.errors, asm.errors
    assert asm.instances and all(i["id"] in asm.parts for i in asm.instances)


def test_home_has_document_actions(make_window):
    w = make_window()
    texts = {b.text() for b in w.home.drop.findChildren(QPushButton)}
    assert {"New Document", "Open Document…"} <= texts


def test_create_add_hinge_reopen_restores_tabs_and_links(make_window, tmp_path):
    w = make_window()
    path = str(tmp_path / "Hinge Doc")
    _new_document_through_home(w, path)
    assert os.path.isfile(os.path.join(path, DF.MANIFEST_NAME))
    assert _tab_titles(w) == ["Part Studio 1"] and w.doc.kind == "studio"

    before = {p: open(p, "rb").read() for p in (HINGE_PARTS, HINGE_ASM)}
    added = w.add_to_document([HINGE_ASM, HINGE_PARTS])         # the assembly first: links convert once both are in
    assert [t.kind for t in added] == ["assembly", "studio"]
    assert len(w.folder.tabs) == 3 and w.tabs.count() == 3
    assert before == {p: open(p, "rb").read() for p in before}   # originals untouched
    asm = next(t for t in w.folder.tabs if t.kind == "assembly").doc
    assert len(asm.instances) == 3
    _resolved(asm)
    with open(os.path.join(path, added[0].file)) as fh:
        for inst in json.load(fh)["instances"]:
            assert "tab" in inst["source"] and not {"uid", "path", "relpath"} & set(inst["source"])
    assert not w.doc.dirty

    # recents: the card is keyed by Document id; its thumbnail by Document id + tab id
    w.tabs.setCurrentIndex(w._tab_index(asm))
    w.show_workspace()
    img = QImage(480, 300, QImage.Format_RGB32)               # the offscreen platform has no GL to render one
    img.fill(QColor("#3070c0"))
    w.viewport.render_thumbnail = lambda *a, **k: img
    w._update_thumbnail()
    w.show_home()
    entry = next(it for it in w.home._recent if os.path.abspath(it[0]) == os.path.abspath(path))
    assert entry[2]["id"] == w.folder.id and entry[2]["name"] == "Hinge Doc"
    assert entry[2]["tab"] == next(t.id for t in w.folder.tabs if t.doc is asm)
    assert w.home.grid.count() == 1 and w.home.grid.itemAt(0).widget().document["id"] == w.folder.id
    keyed = H.keyed_thumb_path(w.folder.id, entry[2]["tab"])
    assert os.path.dirname(keyed) == str(tmp_path / "thumbs")
    assert os.path.exists(keyed)                                 # keyed by Document id + tab id, not by path
    assert not os.path.exists(H.thumb_path(os.path.join(path, added[0].file)))
    assert w.home.grid.itemAt(0).widget().thumb.pixmap().width() > 0

    # close and reopen in a fresh window
    names = [t.name for t in w.folder.tabs]
    w2 = make_window()
    w2.home.openPath.emit(path)
    assert w2.folder is not None and w2.folder.id == w.folder.id
    assert sorted(_tab_titles(w2)) == sorted(names)
    asm2 = next(t for t in w2.folder.tabs if t.kind == "assembly").doc
    _resolved(asm2)
    assert len(asm2.parts) == 3
    assert w2.doc is asm2                                        # reopened on the tab last worked on


def test_ui_builds_tab_sources_in_a_document_and_uid_path_outside(make_window, tmp_path):
    w = make_window()
    _new_document_through_home(w, str(tmp_path / "Doc"))
    st = w.folder.tabs[0]
    pid = _box(st.doc)
    w.new_assembly()                                             # a tab made inside a Document joins it
    asm_tab = w.folder.tabs[-1]
    assert asm_tab.kind == "assembly" and w.doc is asm_tab.doc and w._folder_tab(w.doc) is asm_tab

    w.asm.insert_studio_parts(st.doc.uid, [pid])                 # the Insert panel's path
    src = asm_tab.doc.instances[0]["source"]
    assert src == L.studio_source(st, pid)
    assert not {"uid", "path"} & set(src)
    _resolved(asm_tab.doc)
    assert asm_tab.doc.resolver.__self__ is w.folder.resolver   # resolved through the folder resolver

    groups = {name: entries for name, _icon, entries in w.drawing_sources()}
    assert groups["Part Studio 1"][-1][1] == {**L.studio_source(st, pid), "part_name": "Part 1"} or \
        groups["Part Studio 1"][-1][1]["tab"] == st.id
    for _name, _icon, entries in w.drawing_sources():
        for _label, s in entries:
            assert s["tab"] in {t.id for t in w.folder.tabs} and not {"uid", "path"} & set(s)

    # outside a Document the loose-file uid + path behaviour is unchanged
    w3 = make_window()
    loose = w3.doc
    loose.path = str(tmp_path / "loose.fshape")
    s = w3.studio_link(loose, "P1")
    assert s["uid"] == loose.uid and s["path"] == loose.path and "tab" not in s
    assert w3.assembly_link(loose)["uid"] == loose.uid


def test_crash_with_unsaved_tab_offers_that_document_and_tab(make_window, tmp_path, monkeypatch):
    w = make_window()
    path = str(tmp_path / "Crash Doc")
    _new_document_through_home(w, path)
    w.new_doc()                                                  # a new tab, never saved
    new_tab = w.folder.tabs[-1]
    new_tab.doc.name = "Scratch"
    new_tab.name = "Scratch"
    _box(new_tab.doc)
    new_tab.doc.dirty = True
    assert not os.path.exists(os.path.join(path, new_tab.file))
    w.autosave()
    key = f"{w.folder.id}.{new_tab.id}.fshape"
    assert os.path.isfile(os.path.join(tmp_path / "autosave" / "pid", key))   # keyed by Document id + tab id
    # the process dies: its autosave folder is now owned by a dead pid
    dead = next(p for p in range(4_000_000, 4_100_000) if not MW.MainWindow._process_alive(p))
    os.rename(tmp_path / "autosave" / "pid", tmp_path / "autosave" / str(dead))

    seen = []
    monkeypatch.setattr(MW.QMessageBox, "question", staticmethod(lambda *a, **k: seen.append(a[2]) or QMessageBox.Yes))
    w2 = make_window()
    assert w2.folder is None
    w2.offer_recovery()
    assert len(seen) == 1 and "Scratch" in seen[0] and "Crash Doc" in seen[0] and "tab" in seen[0]
    assert w2.folder is not None and w2.folder.id == w.folder.id
    tab = w2.folder.tab(new_tab.id)
    assert tab is not None and tab.doc.dirty and tab.doc.features and tab.name == "Scratch"
    assert w2._doc_key(tab.doc) == (w2.folder.id, new_tab.id)
    assert w2.docs.get(id(tab.doc)) is tab.doc and "Scratch" in "".join(_tab_titles(w2))
    assert not os.path.exists(tmp_path / "autosave" / str(dead) / key)
    assert len(w2.folder.tabs) == 2


def test_add_to_document_leaves_original_and_loose_assembly_linked(make_window, tmp_path):
    loose = tmp_path / "loose"
    loose.mkdir()
    for p in (HINGE_PARTS, HINGE_ASM):
        shutil.copy(p, loose / os.path.basename(p))
    original = loose / "hinge_parts.fshape"
    before = original.read_bytes()

    w = make_window()
    _new_document_through_home(w, str(tmp_path / "Doc"))
    added = w.add_to_document([str(original)])
    assert [t.kind for t in added] == ["studio"] and added[0].doc.name == "hinge_parts"
    assert original.read_bytes() == before                       # bytes unchanged
    copy = tmp_path / "Doc" / added[0].file
    assert copy.exists() and json.load(open(copy))["_tab_id"] == added[0].id

    asm = AssemblyDoc.load(str(loose / "hinge.fasm"), resolver=L.LinkResolver().resolve_source)
    assert len(asm.instances) == 3
    for inst in asm.instances:                                   # still uid + path, still resolves
        assert inst["source"]["uid"] and inst["source"]["path"]
    asm.refresh(solve=False, notify=False)
    _resolved(asm)

    # the same studio added twice gets its own tab and file; unsupported files are refused
    again = w.add_to_document([str(original)])
    assert again[0].file != added[0].file
    with pytest.raises(Exception):
        w.folder.add_files([str(tmp_path / "x.step")])


def test_loose_open_and_save_unchanged_outside_a_document(make_window, tmp_path):
    w = make_window()
    w.open_any(HINGE_PARTS)
    assert w.folder is None and w._folder_tab(w.doc) is None and w.doc.path == HINGE_PARTS
    assert w._doc_key(w.doc) is None
    assert w.studio_link(w.doc, "F2:1")["path"] == HINGE_PARTS


def test_new_tab_saves_with_the_document_and_switching_documents_closes_the_first(make_window, tmp_path):
    w = make_window()
    a = str(tmp_path / "A")
    _new_document_through_home(w, a)
    w.new_doc()
    tab = w.folder.tabs[-1]
    _box(tab.doc)
    tab.doc.name = "Renamed"
    tab.doc.dirty = True
    assert w.save() is True                                       # Save writes the whole Document
    with open(os.path.join(a, DF.MANIFEST_NAME)) as fh:
        assert [t["name"] for t in json.load(fh)["tabs"]] == ["Part Studio 1", "Renamed"]
    assert os.path.exists(os.path.join(a, tab.file)) and not tab.doc.dirty

    first_id = w.folder.id
    b = str(tmp_path / "B")
    w.create_document(b)
    assert w.folder.id != first_id and w.folder.path == b
    assert _tab_titles(w) == ["Part Studio 1"] and w.tabs.count() == 1
    assert w.open_document(a).id == first_id and sorted(_tab_titles(w)) == ["Part Studio 1", "Renamed"]
