"""I6.5: hide one Context instance, or the whole Context (hidden_features), and the keys that show it again.
Menus are built through functions the tests call; none is exec'd. Offscreen."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
from PySide6.QtWidgets import QMenu  # noqa: E402

from conftest import add, rect_lines, region_refs, sketch_feature  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.ui import panels as P  # noqa: E402
from freeshape.ui.palette import search_commands  # noqa: E402
from test_context_ids import _bar_top, _reopen  # noqa: E402
from test_context_ui import make_rig  # noqa: E402


@pytest.fixture
def rig(tmp_path, monkeypatch):
    r = make_rig(tmp_path, monkeypatch)
    r.w.asm.edit_in_context()
    r.doc = r.plate.doc
    r.cid = next(f["id"] for f in r.doc.features if f["type"] == "context")
    assert r.w.doc is r.doc
    if not r.doc.listeners:          # make_rig replaced the Plate's __dict__, which dropped the window's listener
        r.doc.listeners.append(lambda: r.w._doc_changed_from(r.doc))
        r.doc.notify()
    yield r
    r.w.folder = None
    r.w.deleteLater()


def body(r, iid, cid=None):
    return f"{cid or r.cid}/{iid}"


def scene(r):
    return {pid for pid, _m, _c in r.w.viewport.parts}


def test_hide_context_removes_every_body_and_show_brings_them_back(rig):
    r = rig
    bodies = {body(r, i) for i in (r.ia, r.ib, r.ic)}
    assert bodies <= scene(r)
    m = QMenu()
    r.w._add_context_hide_action(m, r.cid)
    assert [a.text() for a in m.actions()] == ["Hide context"]
    m.actions()[0].trigger()
    assert r.cid in r.doc.hidden_features
    assert not (bodies & scene(r)) and scene(r)            # the edited part stays
    m = QMenu()
    r.w._add_context_hide_action(m, r.cid)
    assert [a.text() for a in m.actions()] == ["Show context"]
    m.actions()[0].trigger()
    assert bodies <= scene(r)


def test_whole_context_keeps_the_hand_hidden_neighbour(rig):
    r = rig
    b = body(r, r.ib)
    r.doc.set_parts_hidden([b], True)
    assert b not in scene(r)
    r.w.set_context_hidden([r.cid], True)
    assert not ({body(r, i) for i in (r.ia, r.ib, r.ic)} & scene(r))
    r.w.set_context_hidden([r.cid], False)
    assert r.doc.hidden_parts == {b}
    assert b not in scene(r) and {body(r, r.ia), body(r, r.ic)} <= scene(r)


def test_instance_menu_item_hides_only_that_instance(rig):
    r = rig
    m = QMenu()
    ids = r.w._add_context_instance_actions(m, [body(r, r.ia)])
    assert ids == [body(r, r.ia)] and [a.text() for a in m.actions()] == ["Hide context instance"]
    m.actions()[0].trigger()
    assert r.doc.hidden_parts == {body(r, r.ia)}
    assert body(r, r.ia) not in scene(r) and {body(r, r.ib), body(r, r.ic)} <= scene(r)
    m = QMenu()
    assert r.w._add_context_instance_actions(m, [next(p for p in r.doc.state.parts if not p.startswith(r.cid))]) == []
    assert not m.actions()


def test_positional_context_hides_too(rig):
    r = rig
    r.doc.feature(r.cid)["params"].pop("ids")
    r.doc.rebuild()
    pos = [p for p in r.doc.state.parts if p.startswith(r.cid + "/")]
    assert sorted(pos) == sorted(f"{r.cid}/{k}" for k in (1, 2, 3))
    r.w.set_context_hidden([r.cid], True)
    assert not (set(pos) & scene(r))
    r.w.set_context_hidden([r.cid], False)
    r.w._add_context_instance_actions(QMenu(), [pos[0]])
    r.w.hide_context_instances([pos[0]])
    assert pos[0] not in scene(r) and set(pos[1:]) <= scene(r)


def test_shift_y_shows_a_hidden_context_and_every_instance(rig):
    r = rig
    r.doc.set_parts_hidden([body(r, r.ia)], True)
    r.w.set_context_hidden([r.cid], True)
    r.w.vis_command("show_all")
    assert r.cid not in r.doc.hidden_features and not r.doc.hidden_parts
    assert {body(r, i) for i in (r.ia, r.ib, r.ic)} <= scene(r)


def test_hiding_is_view_only(rig):
    r = rig
    status = r.w.folder.context_status(r.plate.id, r.cid)
    errs = dict(r.doc.errors)
    sid = sketch_feature(r.doc, lambda s: rect_lines(s, 2, 2, 2, 2))
    pid = body(r, r.ib)
    part = r.doc.state.parts[pid]
    bottom = min(K.faces(part.shape), key=lambda f: K.interior_point(f)[2])
    ref = F.make_ref(pid, bottom, "face", part.shape, named=r.doc.state.named(pid))
    eid = add(r.doc, "extrude", regions=region_refs(r.doc, sid), end="up_to_face",
              up_to={"kind": "face", "ref": ref}, op="new")["id"]
    top = _bar_top(r.doc, eid)
    r.w.set_context_hidden([r.cid], True)
    r.doc.hidden_parts = {pid}
    r.doc.rebuild()
    assert r.w.folder.context_status(r.plate.id, r.cid) == status == "current"
    assert r.doc.errors == errs == {} and abs(_bar_top(r.doc, eid) - top) < 1e-9


def test_two_contexts_hide_one_leaves_the_other(rig):
    r = rig
    p = dict(r.doc.feature(r.cid)["params"])
    cid2 = add(r.doc, "context", **p)["id"]
    assert cid2 != r.cid and body(r, r.ia, cid2) in scene(r) and body(r, r.ia) in scene(r)
    r.w.set_context_hidden([r.cid], True)
    assert body(r, r.ia, cid2) in scene(r) and body(r, r.ia) not in scene(r)


def test_shift_t_on_a_context_body_does_nothing_and_says_so(rig):
    r = rig
    before = set(r.w.viewport.transparent_ids)
    r.w.viewport.hover = None
    from freeshape.ui.viewport import Pick
    r.w.viewport.set_selection([Pick("part", (body(r, r.ia),))])
    r.w.vis_command("transparent")
    assert set(r.w.viewport.transparent_ids) == before
    assert not r.w._transparent.get(r.doc)
    assert "transparency slider" in r.w.status.currentMessage()


def test_hidden_context_survives_save_and_reopen_and_has_an_undo_step(rig):
    r = rig
    r.w.set_context_hidden([r.cid], True)
    _again, tab = _reopen(r.fd, r.plate.id)
    assert r.cid in tab.doc.hidden_features
    r.doc.undo()
    assert r.cid not in r.doc.hidden_features
    r.doc.redo()
    assert r.cid in r.doc.hidden_features


def test_dialog_reference_to_a_hidden_context_face_keeps_its_body_shown(rig):
    r = rig
    pid = body(r, r.ib)
    part = r.doc.state.parts[pid]
    face = K.faces(part.shape)[0]
    ref = F.make_ref(pid, face, "face", part.shape, named=r.doc.state.named(pid))
    r.w.set_context_hidden([r.cid], True)
    assert pid not in scene(r)
    assert pid in r.w._referenced_part_ids({"up_to": {"kind": "face", "ref": ref}})
    feat = r.doc.make_feature("extrude")
    feat["params"]["up_to"] = {"kind": "face", "ref": ref}
    r.w.show_preview(feat, r.doc.insert_index(), [])
    assert pid in scene(r) and body(r, r.ia) not in scene(r)


def test_parts_list_marks_context_bodies(rig):
    r = rig
    lst = r.w.left.parts
    items = {lst.item(i).data(P.ROLE_ID): lst.item(i) for i in range(lst.count())}
    it = items[body(r, r.ia)]
    assert it.font().italic() and r.doc.feature(r.cid)["name"] in it.toolTip()
    own = items[next(p for p in items if not p.startswith(r.cid))]
    assert not own.font().italic()


def test_command_search_finds_hide_context(rig):
    cmds = rig.w._palette_commands()
    for text in ("hide context instances", "ghost", "hide context"):
        assert "Hide context" in [c.name for c in search_commands(cmds, text)], text
    next(c for c in cmds if c.name == "Hide context").action()
    assert rig.cid in rig.doc.hidden_features
