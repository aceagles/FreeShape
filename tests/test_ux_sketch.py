"""Shift+E / Shift+W while sketching, and where file dialogs open."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from conftest import TOP, rect_lines, sketch_feature  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402
from freeshape.ui.filedirs import remember_dir, start_dir  # noqa: E402


class _Settings(dict):
    def value(self, k, default=None):
        return self.get(k, default)

    def setValue(self, k, v):
        self[k] = v


def test_regions_of_sketch_lists_every_region():
    from freeshape.ui.mainwindow import regions_of_sketch
    doc = Document()
    sid = sketch_feature(doc, lambda s: (rect_lines(s, 0, 0, 10, 10), rect_lines(s, 20, 0, 5, 5)))
    picks = regions_of_sketch(doc.state, sid)
    assert [p.key for p in picks] == [(sid, 0), (sid, 1)] and all(p.kind == "region" for p in picks)
    assert regions_of_sketch(doc.state, "nope") == []


def _window():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    w.settings = _Settings()
    app.processEvents()
    return w




def test_finish_and_start_extrude_preselects_regions():
    w = _window()
    w.start_sketch()
    ctx = w.sk_ctx
    ctx["feature"]["params"]["plane"] = TOP
    w._enter_sketcher(animate=False)
    s = w.sketcher.sk
    rect_lines(s, 0, 0, 10, 10)
    rect_lines(s, 20, 0, 5, 5)
    s.solve()
    fid = ctx["feature"]["id"]
    w.finish_sketch_and_start("extrude")
    assert w.mode == "dialog" and w.dialog.ftype == "extrude"
    regs = w.dialog.feature["params"]["regions"]
    assert len(regs) == 2 and {r["sketch"] for r in regs} == {fid}
    w.cancel_dialog()


def test_finish_and_start_revolve_and_empty_sketch():
    w = _window()
    w.start_sketch()
    w.sk_ctx["feature"]["params"]["plane"] = TOP
    w._enter_sketcher(animate=False)
    rect_lines(w.sketcher.sk, 0, 5, 10, 10)
    w.sketcher.sk.solve()
    w.finish_sketch_and_start("revolve")
    assert w.dialog.ftype == "revolve" and len(w.dialog.feature["params"]["regions"]) == 1
    assert not w.dialog.feature["params"].get("axis")
    w.cancel_dialog()
    # open lines only: no closed region, Extrude opens with nothing selected
    w.start_sketch()
    w.sk_ctx["feature"]["params"]["plane"] = TOP
    w._enter_sketcher(animate=False)
    s = w.sketcher.sk
    s.add_line(s.add_point(0, 0), s.add_point(5, 0))
    w.finish_sketch_and_start("extrude")
    assert w.dialog.ftype == "extrude" and w.dialog.feature["params"]["regions"] == []


def test_dialog_dir_remembered_then_document_then_desktop(tmp_path):
    s = _Settings()
    desk = tmp_path / "desk"
    desk.mkdir()
    docdir = tmp_path / "docs"
    docdir.mkdir()
    last = tmp_path / "last"
    last.mkdir()
    assert start_dir(s, "doc", None, desktop=str(desk)) == str(desk)
    assert start_dir(s, "doc", str(docdir / "a.fshape"), desktop=str(desk)) == str(docdir)
    remember_dir(s, "doc", str(last / "x.fshape"))
    assert start_dir(s, "doc", str(docdir / "a.fshape"), desktop=str(desk)) == str(last)
    assert start_dir(s, "export", str(docdir / "a.fshape"), desktop=str(desk)) == str(docdir)   # per kind
    last.rmdir()                                                   # a vanished folder is ignored
    assert start_dir(s, "doc", None, desktop=str(desk)) == str(desk)
    assert start_dir(s, "doc", None, desktop=str(tmp_path / "gone")) == os.path.expanduser("~")


def test_toggling_sketch_visibility_refreshes_open_feature_preview():
    w = _window()
    sid = sketch_feature(w.doc, lambda s: rect_lines(s, 0, 0, 10, 10))
    w.doc.rebuild()
    w.start_feature("extrude")
    assert w.mode == "dialog"
    calls = []
    w.dialog.schedule_preview = lambda: calls.append(1)
    w.doc.toggle_feature_hidden(sid)
    assert calls, "showing/hiding a sketch while a feature dialog is open must refresh its preview"
    w.cancel_dialog()


def test_sketch_view_policy():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from freeshape.ui.viewport import Viewport
    v = Viewport()
    assert v.sketch_view_policy() == (1.0, True, 1.0, False)
    v.sketcher = object()
    assert v.sketch_see_through
    fa, depth, ea, top = v.sketch_view_policy()
    assert fa == 0.35 and depth is False and 0 < ea < 1 and top
    v.sketch_see_through = False
    assert v.sketch_view_policy() == (1.0, True, 1.0, True)


def test_sketch_dialog_see_through_checkbox_persists():
    w = _window()
    w.start_sketch()
    assert w.dialog.see_through.isChecked() and w.viewport.sketch_see_through
    w.dialog.see_through.setChecked(False)
    assert w.viewport.sketch_see_through is False
    assert w.settings["sketch_see_through"] == "false"
    w.cancel_sketch()
    w.start_sketch()
    assert not w.dialog.see_through.isChecked() and w.viewport.sketch_see_through is False
    w.dialog.see_through.setChecked(True)
    assert w.settings["sketch_see_through"] == "true" and w.viewport.sketch_see_through
    w.cancel_sketch()


def test_editing_extrude_can_repick_region_after_clearing_it():
    """Editing an extrude whose sketch is hidden, clearing its region and clicking the region again must work:
    the sketch stays on screen for the life of the dialog and the click goes to the regions field, not to a
    hidden Merge scope field."""
    from freeshape.ui.mainwindow import regions_of_sketch
    from freeshape.ui.viewport import Pick
    w = _window()
    sid = sketch_feature(w.doc, lambda s: rect_lines(s, 0, 0, 10, 10))
    w.doc.rebuild()
    w.start_feature("extrude", preselect=regions_of_sketch(w.doc.state, sid))
    w.dialog._do_preview()
    w.commit_dialog()
    assert sid in w.doc.hidden_features                  # a consumed sketch is hidden, as in Onshape
    fid = w.doc.features[-1]["id"]
    assert not w.doc.errors.get(fid)

    w.start_feature("extrude", edit_fid=fid)
    d = w.dialog
    d._do_preview()
    prof = d.fields["profiles"]
    assert d.active_field is None or d.active_field.isVisibleTo(d), "a hidden field must never be the active one"
    prof.clear()
    d._do_preview()
    assert d.feature["params"]["regions"] == []
    assert sid in [f for f, _sr, _m in w.viewport.sketches], "the sketch must stay visible so it can be re-picked"
    assert d.active_field is prof, "clearing a field makes it the one that takes the next click"
    d.handle_pick(Pick("region", (sid, 0)))
    assert len(d.feature["params"]["regions"]) == 1
    w.cancel_dialog()
