"""Groups UI (I4.3): the Group / ungroup actions, the panel's Groups list, selecting a group, moving one member."""
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape.ui.assembly_ui import AssemblyController  # noqa: E402
from test_assembly import box, make, src  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def lib():
    return {"block": box(10, 10, 10), "plate": box(40, 40, 5)}


def scene(lib):
    a = make(lib)
    ids = [a.insert(src("block"), T=np.array([[1, 0, 0, 20 * k], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], float))
           for k in range(3)]
    vp = MagicMock()
    vp.selection = []
    w = SimpleNamespace(doc=a, viewport=vp, mode="idle", status=MagicMock())
    ctl = AssemblyController(w)
    a.listeners.append(ctl.on_changed)
    ctl.on_changed()
    return a, ctl, ids


def test_group_action_adds_one_group_listed_in_panel(lib):
    a, ctl, ids = scene(lib)
    assert not ctl.can_group(ids[:1])
    ctl.panel.select_instances({ids[0], ids[1]})
    assert ctl.can_group()
    gid = ctl.group_selected()
    assert [g["id"] for g in a.groups] == [gid]
    assert ctl.panel.groups.count() == 1
    text = ctl.panel.groups.item(0).text()
    assert a.groups[0]["name"] in text and a.instance(ids[0])["name"] in text
    assert set(ctl.panel.selected_instances()) == {ids[0], ids[1]}
    # members are no longer groupable, and a third alone is not enough
    assert not ctl.can_group([ids[0], ids[2]])
    assert ctl.group_selected([ids[0], ids[2]]) is None
    assert len(a.groups) == 1


def test_ungroup_removes_group_and_undo_restores_it(lib):
    a, ctl, ids = scene(lib)
    gid = ctl.group_selected(ids[:2])
    ctl.ungroup(gid)
    assert a.groups == [] and ctl.panel.groups.count() == 0
    a.undo()
    ctl.on_changed()
    assert [g["id"] for g in a.groups] == [gid]
    assert ctl.panel.groups.count() == 1


def test_rename_group_and_undo(lib):
    a, ctl, ids = scene(lib)
    gid = ctl.group_selected(ids[:2])
    ctl.rename_group(gid, "Wheel")
    assert a.groups[0]["name"] == "Wheel" and "Wheel" in ctl.panel.groups.item(0).text()
    a.undo()
    assert a.groups[0]["name"] != "Wheel"


def test_selecting_a_group_selects_its_members(lib):
    a, ctl, ids = scene(lib)
    gid = ctl.group_selected(ids[1:])
    ctl.panel.select_instances(set())
    ctl.panel.groups.item(0).setSelected(True)
    ctl.panel.groupClicked.emit(gid)
    assert set(ctl.panel.selected_instances()) == set(ids[1:])


def test_apply_motion_on_one_member_moves_the_group_rigidly(lib):
    a, ctl, ids = scene(lib)
    ctl.group_selected(ids[:2])
    T0s = {i["id"]: np.array(i["T"], float) for i in a.instances}
    rel0 = np.linalg.inv(T0s[ids[0]]) @ T0s[ids[1]]
    ang = 0.7
    M = np.eye(4)
    M[:3, :3] = [[np.cos(ang), -np.sin(ang), 0], [np.sin(ang), np.cos(ang), 0], [0, 0, 1]]
    M[:3, 3] = [5, 7, 3]
    ctl.apply_motion(ids[0], M, T0s, [np.zeros(3)])
    T = {i: np.array(a.instance(i)["T"], float) for i in ids}
    assert np.abs(np.linalg.inv(T[ids[0]]) @ T[ids[1]] - rel0).max() < 1e-9
    assert np.abs(T[ids[0]] - M @ T0s[ids[0]]).max() > 1          # it did move
    assert np.allclose(T[ids[2]], T0s[ids[2]])                     # a non-member stayed put
