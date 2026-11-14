"""I1.7: Assembly UI for Sketch instances. The Insert panel lists a Part Studio's sketches under its parts and
double-click inserts a Sketch instance; show_scene hands the viewport its sketch lines and points; the instance
menu has no live Mass properties for it; the BOM leaves it out. Offscreen; no real menu or dialog is opened."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from conftest import FRONT, add, rect_lines, region_refs, sketch_feature  # noqa: E402
from freeshape import assembly as A  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.ui.assembly_ui import ROLE_ID, ROLE_KIND  # noqa: E402
from test_sketch_instance import layout_sketch  # noqa: E402


STUDIO_UID = [None]


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    doc = Document()
    s1 = sketch_feature(doc, layout_sketch, FRONT)             # 2 construction lines + a free point
    s2 = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 10, 10), FRONT)
    add(doc, "extrude", regions=region_refs(doc, s2), depth=5, op="new")
    w._add_tab(doc)
    w.new_assembly()
    app.processEvents()
    assert isinstance(w.doc, A.AssemblyDoc)
    w.studio, w.sk_ids = doc, (s1, s2)
    STUDIO_UID[0] = doc.uid
    yield w
    w.deleteLater()


def rows(panel, kind):
    out = []
    top = next(panel.tree.topLevelItem(i) for i in range(panel.tree.topLevelItemCount())
               if panel.tree.topLevelItem(i).data(0, ROLE_ID) == (STUDIO_UID[0], None))
    for i in range(top.childCount()):
        it = top.child(i)
        if it.data(0, ROLE_KIND) == kind:
            out.append(it)
    return out


def sketch_instances(asm):
    return [i for i in asm.instances if asm.is_sketch_instance(i["id"])]


def test_insert_panel_lists_sketches_and_activating_one_inserts_it(win):
    win.asm.toggle_insert()
    panel = win.asm.insert_panel
    assert len(rows(panel, "part")) == 1
    sk_rows = rows(panel, "sketch")
    assert len(sk_rows) == 2
    assert [r.text(0) for r in sk_rows] == [win.studio.feature(s)["name"] for s in win.sk_ids]
    n_undo = len(win.doc._undo)
    panel._insert_item(sk_rows[0])                              # what a double-click calls
    insts = sketch_instances(win.doc)
    assert len(insts) == 1 and win.doc.instances[0] is insts[0]
    assert A.is_sketch_source(insts[0]["source"]) and insts[0]["source"]["sketch"] == win.sk_ids[0]
    assert len(win.doc._undo) == n_undo + 1                     # one checkpoint
    win.doc.undo()
    assert win.doc.instances == []
    win.asm.close_insert()


def test_insert_studio_sketch_is_a_controller_command(win):
    iid = win.asm.insert_studio_sketch(win.studio.uid, win.sk_ids[0])
    assert win.doc.is_sketch_instance(iid)
    assert win.asm.insert_studio_sketch(win.studio.uid, "nope") is None
    assert win.asm.insert_studio_sketch("no-such-uid", win.sk_ids[0]) is None
    assert len(win.doc.instances) == 1


def test_parts_path_draws_nothing_for_an_edges_only_shape(win):
    """What show_scene's part path did before this card: an instance with edges and no faces is skipped."""
    iid = win.asm.insert_studio_sketch(win.studio.uid, win.sk_ids[0])
    vp = win.asm.vp
    vp.set_scene([(iid, win.doc.parts[iid].shape, (0.5, 0.5, 0.5))], [], [])
    assert vp._batch["nedge"] == 0 and vp._batch["ntri"] == 0 and iid not in vp._batch["ranges"]


def test_show_scene_hands_the_viewport_the_sketch_and_hiding_removes_it(win):
    ctl, vp, doc = win.asm, win.asm.vp, win.doc
    pid = next(iter(win.studio.state.parts))
    doc.insert(win.studio_link(win.studio, pid))
    iid = ctl.insert_studio_sketch(win.studio.uid, win.sk_ids[0])
    ctl.show_scene()
    assert [fid for fid, _sr, _m in vp.sketches] == [iid]
    _fid, sr, meshes = vp.sketches[0]
    assert meshes == [] and len(sr.sketch.entities) == 3
    lines = [e for e in sr.sketch.entities.values() if e["type"] == "line"]
    assert len(lines) == 2 and all(e.get("construction") for e in lines)    # the viewport draws these dashed
    assert [pid_ for pid_, _m, _c in vp.parts] == [doc.instances[0]["id"]]     # the part is still drawn as a part
    lo, hi = vp.model_bounds()
    assert hi[0] >= 30 - 1e-6                                   # the lines count in the view bounds
    # it follows its instance transform: the plane origin moves by T
    doc.instance(iid)["T"] = A.translation([0, 0, 100]).tolist()
    ctl.show_scene()
    assert np.allclose(vp.sketches[0][1].plane.origin, [0, 0, 100], atol=1e-9)
    doc.set_hidden(iid, True)
    ctl.show_scene()
    assert vp.sketches == []
    doc.set_hidden(iid, False)
    ctl.show_scene()
    assert len(vp.sketches) == 1
    doc.set_fixed(iid, not doc.instance(iid).get("fixed"))      # the fixed toggle is the ordinary one
    ctl.show_scene()
    assert len(vp.sketches) == 1


def test_whole_studio_insert_adds_parts_only_and_bom_skips_the_sketch(win):
    from freeshape.ui.assembly_ui import BomDialog
    win.asm.insert_studio_parts(win.studio.uid, None)
    assert len(win.doc.instances) == 1 and sketch_instances(win.doc) == []
    win.asm.insert_studio_sketch(win.studio.uid, win.sk_ids[0])
    assert len(sketch_instances(win.doc)) == 1
    d = BomDialog(win, win.doc)                                 # a table; no exec()
    assert len(d.rows) == 1 and d.rows[0]["qty"] == 1
    assert all("ketch" not in r["name"] for r in d.rows)
    d.deleteLater()


def test_instance_menu_mass_properties_is_disabled_for_a_sketch(win, monkeypatch):
    from PySide6 import QtWidgets
    from PySide6.QtWidgets import QMenu
    seen, keep = [], []

    class RecMenu(QMenu):
        def exec(self, *a, **k):
            keep.append(self)
            seen.extend(self.actions())

        def popup(self, *a, **k):
            self.exec()

    monkeypatch.setattr(QtWidgets, "QMenu", RecMenu)           # instance_menu imports it inside the handler
    ctl = win.asm
    pid = next(iter(win.studio.state.parts))
    part_iid = win.doc.insert(win.studio_link(win.studio, pid))
    sk_iid = ctl.insert_studio_sketch(win.studio.uid, win.sk_ids[0])
    ctl.instance_menu(sk_iid, None)
    mp = [a for a in seen if a.objectName() == "mass_properties"]
    assert len(mp) == 1 and not mp[0].isEnabled()
    seen.clear()
    ctl.instance_menu(part_iid, None)
    mp = [a for a in seen if a.objectName() == "mass_properties"]
    assert len(mp) == 1 and mp[0].isEnabled()
