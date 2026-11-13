"""Suppress in the Assembly UI (I3.11); hide, isolate and show-all are undoable and mark the Assembly changed."""
import os
import sys
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QPoint  # noqa: E402
from PySide6 import QtWidgets  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu, QWidget  # noqa: E402

from freeshape.ui.assembly_ui import AssemblyController  # noqa: E402
from test_assembly import box, face, make, mate, src  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def lib():
    return {"block": box(10, 10, 10), "plate": box(40, 40, 5)}


class Win(QWidget):
    def __init__(self, doc, vp):
        super().__init__()
        self.doc, self.viewport, self.mode, self.status = doc, vp, "idle", MagicMock()


def scene(lib):
    a = make(lib)
    ids = [a.insert(src("block"), T=np.array([[1, 0, 0, 20 * k], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], float))
           for k in range(3)]
    vp = MagicMock()
    vp.selection = []
    w = Win(a, vp)
    ctl = AssemblyController(w)
    ctl._win = w
    a.listeners.append(ctl.on_changed)
    ctl.on_changed()
    return a, ctl, ids


def shown(ctl):
    return [p[0] for p in ctl.vp.set_scene.call_args[0][0]]


def choose(monkeypatch, ctl, iid, text):
    """Open the instance menu with exec() stubbed, trigger the action called `text`; return all action texts."""
    seen = []

    class FakeMenu(QMenu):
        def exec(self, *_a):
            seen.extend(a.text() for a in self.actions())
            for act in self.actions():
                if act.text() == text:
                    act.trigger()

    monkeypatch.setattr(QtWidgets, "QMenu", FakeMenu)
    ctl.instance_menu(iid, QPoint(0, 0))
    return seen


def test_menu_suppress_greys_row_and_leaves_viewport(lib, monkeypatch):
    a, ctl, ids = scene(lib)
    assert ids[1] in shown(ctl)
    texts = choose(monkeypatch, ctl, ids[1], "Suppress")
    assert "Suppress" in texts
    assert a.instance_suppressed(ids[1])
    assert ids[1] not in shown(ctl) and ids[0] in shown(ctl)
    item = ctl.panel.instances.item(1)
    assert ctl.panel.instances.count() == 3
    assert item.font().strikeOut()
    assert item.foreground().color().name() == "#9aa3ae"
    texts = choose(monkeypatch, ctl, ids[1], "Unsuppress")
    assert "Unsuppress" in texts and "Suppress" not in texts
    assert not a.instance_suppressed(ids[1])
    assert ids[1] in shown(ctl)
    assert not ctl.panel.instances.item(1).font().strikeOut()


def test_suppressed_row_differs_from_hidden_row(lib):
    a, ctl, ids = scene(lib)
    a.set_hidden(ids[0], True)
    a.set_instance_suppressed(ids[1], True)
    hid, sup, plain = (ctl.panel.instances.item(i) for i in range(3))
    assert (hid.font(), hid.foreground()) != (sup.font(), sup.foreground())
    assert sup.font().strikeOut() and not hid.font().strikeOut() and not plain.font().strikeOut()
    assert "Suppressed" in sup.toolTip() and "Hidden" in hid.toolTip()


def test_mate_on_suppressed_instance_reads_inactive_not_error(lib):
    a = make(lib)
    ip = a.insert(src("plate"))
    ib = a.insert(src("block"))
    top = a.connector(ip, "face", face(a, ip, lambda f: f.get("n") and f["n"][2] > 0.9))
    bot = a.connector(ib, "face", face(a, ib, lambda f: f.get("n") and f["n"][2] < -0.9))
    mate(a, "planar", top, bot)
    ctl = AssemblyController(Win(a, MagicMock()))
    a.listeners.append(ctl.on_changed)
    a.set_instance_suppressed(ib, True)
    it = ctl.panel.mates.item(0)
    assert it.font().strikeOut()
    assert "Inactive" in it.toolTip()


def test_hide_undo_shows_again_and_marks_changed(lib, monkeypatch):
    a, ctl, ids = scene(lib)
    a.dirty = False
    choose(monkeypatch, ctl, ids[0], "Hide")
    assert a.dirty and ids[0] not in shown(ctl)
    a.undo()
    assert not a.instance(ids[0])["hidden"]
    ctl.on_changed()
    assert ids[0] in shown(ctl)


def test_isolate_undo_restores_every_hidden_state(lib, monkeypatch):
    a, ctl, ids = scene(lib)
    a.set_hidden(ids[2], True)               # a mixed starting state
    a.dirty = False
    choose(monkeypatch, ctl, ids[0], "Isolate")
    assert a.dirty
    assert [bool(i["hidden"]) for i in a.instances] == [False, True, True]
    a.undo()
    assert [bool(i["hidden"]) for i in a.instances] == [False, False, True]
    a.redo()
    assert [bool(i["hidden"]) for i in a.instances] == [False, True, True]


def test_show_all_is_one_undo_step_and_marks_changed(lib):
    a, ctl, ids = scene(lib)
    a.set_hidden(ids[0], True)
    a.set_hidden(ids[1], True)
    a.dirty = False
    ctl.show_all()
    assert a.dirty and not any(i["hidden"] for i in a.instances)
    a.undo()
    assert [bool(i["hidden"]) for i in a.instances] == [True, True, False]
