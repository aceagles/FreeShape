"""Relations in the assembly panel (I2.5): the list, error state, context-menu actions."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402

from test_relation_dialog import _ctl, _setup, qapp  # noqa: E402,F401


def _scene():
    a, (m1, m2) = _setup("revolute", "revolute")
    r = a.make_relation("gear")
    r["a"] = {"mate": m1["id"], "dof": "rz"}
    r["b"] = {"mate": m2["id"], "dof": "rz"}
    a.add_relation(r)
    ctl = _ctl(a)
    a.listeners.append(lambda: ctl.panel.populate(a))
    ctl.panel.populate(a)
    return a, ctl, r, m1, m2


def _red(it):
    return it.foreground().color() == QColor("#d93025")


def test_panel_lists_each_relation(qapp):
    a, ctl, r, m1, m2 = _scene()
    lst = ctl.panel.relations
    assert lst.count() == 1
    assert lst.item(0).text() == r["name"]
    assert ctl.panel.relation_header.text() == "Relations (1)"
    assert not _red(lst.item(0))


def test_error_relation_is_marked_with_tooltip(qapp):
    a, ctl, r, m1, m2 = _scene()
    a.errors[r["id"]] = "Can't be solved: test"
    ctl.panel.populate(a)
    it = ctl.panel.relations.item(0)
    assert _red(it) and "Can't be solved: test" in it.toolTip()


def test_delete_relation_then_undo_restores(qapp):
    a, ctl, r, m1, m2 = _scene()
    ctl.delete_relation(r["id"])
    assert a.relations == [] and ctl.panel.relations.count() == 0
    a.undo()
    assert [x["id"] for x in a.relations] == [r["id"]]
    assert ctl.panel.relations.count() == 1


def test_suppress_and_rename_undoable(qapp):
    a, ctl, r, m1, m2 = _scene()
    old = r["name"]
    ctl.suppress_relation(r["id"], True)
    assert a.relation(r["id"])["suppressed"]
    assert ctl.panel.relations.item(0).font().strikeOut()
    ctl.rename_relation(r["id"], "Drive")
    assert ctl.panel.relations.item(0).text() == "Drive"
    a.undo()
    assert a.relation(r["id"])["name"] == old
    a.undo()
    assert not a.relation(r["id"])["suppressed"]


def test_deleting_a_related_mate_keeps_relation_in_error(qapp):
    a, ctl, r, m1, m2 = _scene()
    a.delete_mates([m1["id"]])
    assert a.relation(r["id"]) is not None
    assert ctl.panel.relations.count() == 1
    it = ctl.panel.relations.item(0)
    assert r["id"] in a.errors and _red(it) and a.errors[r["id"]] in it.toolTip()


def test_select_relation_selects_its_mates(qapp):
    a, ctl, r, m1, m2 = _scene()
    ctl.select_relation(r["id"])
    assert set(ctl.panel.selected_mates()) == {m1["id"], m2["id"]}
