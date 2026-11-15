"""I2.5: the Parts list groups surface bodies under a Surfaces (n) header, and mass properties of a surface show an
area and no volume or mass.

No feature makes a surface yet, so a surface part is put into the built state by hand (as tests/test_surface_bodies.py
does). The engine-window test sends it through the wire the way the app does (to_wire / from_wire, then the
StudioEngine's own _decode and _apply). Offscreen."""
import os
import signal
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: E402
from OCP.gp import gp_Pnt  # noqa: E402

from conftest import block_doc  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.ui import panels as P  # noqa: E402


def sheet(w=30.0, d=20.0, at=(100.0, 0.0, 0.0)):
    """A one-face surface shape (area w * d): the top face of a box."""
    top = BRepPrimAPI_MakeBox(gp_Pnt(*at), w, d, 5.0).Shape()
    return max(K.faces(top), key=lambda f: K.face_info(f)["c"][2])


def rows(w):
    """[(kind, text, selectable, eye)] for each row of the Parts list."""
    lst = w.left.parts
    out = []
    for i in range(lst.count()):
        it = lst.item(i)
        out.append((it.data(P.ROLE_KIND), it.text(), bool(it.flags() & P.Qt.ItemIsSelectable), it.data(P.ROLE_EYE)))
    return out


def texts(w):
    return [r[1] for r in rows(w)]


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    doc = block_doc()
    w._add_tab(doc)
    app.processEvents()
    w.studio = doc
    yield w
    w.deleteLater()


def add_surface(w):
    doc = w.studio
    (sid,) = doc.state.parts
    surf = doc.state.new_part(sheet(), "S9", surface=True)
    w._populate_lists()
    return sid, surf.id


# ------------------------------------------------------------------------------------ grouping
def test_surfaces_group_follows_parts_group(win):
    assert texts(win) == ["Parts (1)", "Part 1"]            # no surface: no Surfaces header
    sid, surf = add_surface(win)
    assert texts(win) == ["Parts (1)", "Part 1", "Surfaces (1)", "Surface 1"]
    lst = win.left.parts
    assert [lst.item(i).data(P.ROLE_ID) for i in range(lst.count())] == [None, sid, None, surf]
    win.studio.notify()
    assert win.left.parts_header.text() == "Parts (1) · Surfaces (1)"


def test_only_surfaces_leaves_no_empty_parts_header(win):
    sid, surf = add_surface(win)
    win.studio.state.parts.pop(sid)
    win._populate_lists()
    assert texts(win) == ["Surfaces (1)", "Surface 1"]


def test_header_rows_are_not_selectable_and_have_no_eye(win):
    sid, surf = add_surface(win)
    lst = win.left.parts
    for kind, text, selectable, eye in rows(win):
        if kind == "header":
            assert not selectable and eye is None
    lst.selectAll()
    assert sorted(lst.selected_part_ids()) == sorted([sid, surf])
    lst.clearSelection()
    lst.item(0).setSelected(True)
    lst.item(2).setSelected(True)
    assert lst.selected_part_ids() == []
    # clicking a header, or where its eye would be, hides and selects nothing
    got = []
    lst.eyeClicked.connect(got.append)
    lst.partClicked.connect(lambda *a: got.append(a))
    lst.itemClicked.emit(lst.item(2))
    assert got == [] and lst._eye_hit(lst.visualItemRect(lst.item(2)).center()) is None


# ------------------------------------------------------------------------------------ hide, show, rename, delete
def test_hide_and_show_the_surface_row_follow_hidden_parts(win):
    sid, surf = add_surface(win)
    doc, lst = win.studio, win.left.parts
    lst.eyeClicked.emit(surf)
    assert surf in doc.hidden_parts and sid not in doc.hidden_parts
    assert rows(win)[3][3] == "hidden" and rows(win)[1][3] == "shown"
    assert surf not in {pid for pid, _m, _c in win.viewport.parts}
    lst.eyeClicked.emit(surf)
    assert surf not in doc.hidden_parts and rows(win)[3][3] == "shown"
    assert surf in {pid for pid, _m, _c in win.viewport.parts}


def test_rename_the_surface_row(win, monkeypatch):
    sid, surf = add_surface(win)
    from PySide6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Skin", True))
    win._rename_part(surf)
    assert win.studio.state.parts[surf].name == "Skin"
    assert texts(win) == ["Parts (1)", "Part 1", "Surfaces (1)", "Skin"]


def test_delete_part_works_on_the_surface_row(win):
    """The menu action opens the Delete part feature with the surface picked, and the feature removes it."""
    sid, surf = add_surface(win)
    started = []
    win.start_feature = lambda kind, preselect=(): started.append((kind, [pk.key[0] for pk in preselect]))
    win._delete_parts([surf])
    assert started == [("delete_part", [surf])]
    st = win.studio.state
    f = {"id": "D1", "type": "delete_part", "name": "Delete part", "suppressed": False,
         "params": {"parts": [surf]}}
    F.RUNNERS["delete_part"](st, F.prepare(f, st))
    assert list(st.parts) == [sid]
    win._populate_lists()
    assert texts(win) == ["Parts (1)", "Part 1"]


def test_context_menu_of_a_surface_row_has_the_part_actions(win, monkeypatch):
    sid, surf = add_surface(win)
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QMenu
    from freeshape.ui import mainwindow as MW
    seen = []

    class Menu(QMenu):                          # exec() never blocks
        def exec(self, *a):
            seen.extend(x.text() for x in self.actions() if x.text())

    monkeypatch.setattr(MW, "QMenu", Menu)
    win._part_context(surf, QPoint(0, 0))
    assert {"Rename…", "Hide", "Mass properties", "Delete part…"} <= set(seen)


# ------------------------------------------------------------------------------------ Context bodies
def test_a_context_body_keeps_its_marking_and_sits_under_parts(win):
    """A Context body is a `reference` part: it stays under Parts, even if its shape has no solid."""
    doc = win.studio
    (sid,) = doc.state.parts
    doc.state.parts["C1/x"] = F.Part("C1/x", "Plate", doc.state.parts[sid].shape, (0.5, 0.5, 0.5), None, True)
    win._populate_lists()
    assert texts(win) == ["Parts (2)", "Part 1", "Plate"]
    it = win.left.parts.item(2)
    assert it.font().italic() and it.toolTip().startswith("Context body of ")
    surf_ctx = doc.state.parts["C1/x"]
    surf_ctx.shape = sheet()                     # a Context body that is a surface still lists under Parts
    win._populate_lists()
    assert texts(win)[0] == "Parts (2)" and "Surfaces (1)" not in texts(win)
    assert win.left.parts.item(2).font().italic()


# ------------------------------------------------------------------------------------ mass properties
def test_mass_properties_of_a_surface_show_area_and_no_volume_or_mass(win, monkeypatch):
    sid, surf = add_surface(win)
    from PySide6.QtWidgets import QMessageBox
    shown = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self.text()) or 0)
    win._mass_props(surf)
    (text,) = shown
    assert "Surface 1" in text and "Surface area: 600" in text
    assert "Volume" not in text and "Mass" not in text and "g/cm" not in text
    assert "Centre of area" in text
    shown.clear()
    win._mass_props(sid)                         # a solid still reports volume and mass
    assert "Volume: 8" in shown[0] and "Mass (" in shown[0]


# ------------------------------------------------------------------------------------ engine window
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


def test_engine_window_lists_a_surface_under_surfaces_with_area_and_no_volume(tmp_path, monkeypatch):
    """MainWindow(engine=True): the outputs of an in-process engine whose worker state holds a surface part go through
    to_wire / from_wire, then the window's StudioEngine._decode and _apply, as a worker result does. The window's own
    ModelState keeps surface_counter 0, so nothing here reads counters."""
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("FREESHAPE_NO_RECOVERY", "1")
    monkeypatch.setenv("FREESHAPE_NO_HOME", "1")
    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication([])
    from freeshape.engine import InProcessEngine
    from freeshape.engine import process as EP
    from freeshape.engine.wire import from_wire, to_wire
    from freeshape.ui.mainwindow import MainWindow
    from freeshape.ui.partstudio_engine import snapshot
    path = str(tmp_path / "studio.fshape")
    block_doc().save(path)
    before = set(_children())
    w = MainWindow(engine=True)
    try:
        w.open_any(path)
        se = w.studio_engine
        assert se.settle(180)
        doc = w.doc
        assert texts(w) == ["Parts (1)", "Part 1"]
        with InProcessEngine() as e:
            tab = e.open_tab(path)
            wdoc = e._get(tab).doc
            wdoc.state.new_part(sheet(), "S9", surface=True)
            out = from_wire(to_wire(e.outputs(tab)))
        assert sorted(p.name for p in out.parts) == ["Part 1", "Surface 1"]
        rec = se._recs[id(doc)]
        res = se._decode(rec, snapshot(doc), out)
        se._apply(rec, res)
        app.processEvents()
        assert doc.state.surface_counter == 0                   # the window replays light features only
        assert texts(w) == ["Parts (1)", "Part 1", "Surfaces (1)", "Surface 1"]
        surf = next(p for p in doc.state.parts.values() if p.name == "Surface 1")
        assert K.is_surface(surf.shape)
        assert rows(w)[2][0] == "header" and not rows(w)[2][2]
        shown = []
        monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self.text()) or 0)
        w._mass_props(surf.id)
        assert "Surface area: 600" in shown[0] and "Volume" not in shown[0] and "Mass" not in shown[0]
        w.left.parts.eyeClicked.emit(surf.id)
        assert surf.id in doc.hidden_parts and rows(w)[3][3] == "hidden"
    finally:
        w.studio_engine.shutdown()
        EP.SPARES.clear()
        for p in set(_children()) - before:
            try:
                os.kill(p, signal.SIGKILL)
            except OSError:
                pass
        w.deleteLater()
