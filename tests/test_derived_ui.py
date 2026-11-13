"""I1.9: the Derived dialog, toolbar button and command in the window.

One offscreen window with the process executor serves the module: a Document with Part Studio Src (a box part and
its sketch), Part Studio Spare (never shown in a window tab) and Part Studio Use, the current tab. The dialog is
driven through its own widgets, as a person would."""
import os
import signal

import pytest

from conftest import add, rect_lines, region_refs, sketch_feature
from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape.engine import process as P

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QToolButton  # noqa: E402

LOOSE = "Add this Part Studio to a Document to use Derived for a loose .fshape."


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


@pytest.fixture(scope="module")
def win(tmp_path_factory):
    root = tmp_path_factory.mktemp("drvui")
    mp = pytest.MonkeyPatch()
    mp.setenv("FREESHAPE_DATA_DIR", str(root / "appdata"))
    app = QApplication.instance() or QApplication([])
    from freeshape.ui import home as H
    from freeshape.ui import mainwindow as MW
    os.makedirs(root / "autosave", exist_ok=True)
    os.makedirs(root / "thumbs", exist_ok=True)
    mp.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(root / "autosave")))
    mp.setattr(MW.MainWindow, "autosave_dir", classmethod(lambda cls: str(root / "autosave")))
    mp.setattr(H, "thumbs_dir", lambda: str(root / "thumbs"))
    for name in ("warning", "critical", "information"):
        mp.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.Ok)
    fd = DF.FolderDocument.create(str(root / "Doc"), registry=R.default_registry())
    src = fd.add_tab("studio", "Src")
    sid = sketch_feature(src.doc, lambda s: rect_lines(s, 0, 0, 20, 10))
    add(src.doc, "extrude", regions=region_refs(src.doc, sid), depth=5, op="new")
    spare = fd.add_tab("studio", "Spare")
    sketch_feature(spare.doc, lambda s: rect_lines(s, 0, 0, 5, 5))
    use = fd.add_tab("studio", "Use")
    fd.save()
    before = set(_children())
    w = MW.MainWindow(engine=True)
    from PySide6.QtCore import QSettings
    w.settings = QSettings(str(root / "settings.ini"), QSettings.IniFormat)
    w.open_document(fd.path)
    assert w.studio_engine.settle(240)
    w.use = w.folder.tab(use.id).doc
    w.src_tab, w.spare_tab, w.use_tab = w.folder.tab(src.id), w.folder.tab(spare.id), w.folder.tab(use.id)
    w.sid = sid
    w.tabs.setCurrentIndex(w._tab_index(w.use))
    w.studio_engine.settle(60)
    yield w
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


def _derived(w):
    return [f for f in w.use.features if f["type"] == "derived"]


def _choose(w, tab):
    combo = w.dialog.widgets["source"]
    combo.setCurrentIndex(combo.findData(tab.id))
    return combo


def _ticks(lst):
    return [(lst.item(i).data(Qt.UserRole), lst.item(i).checkState() == Qt.Checked) for i in range(lst.count())]


def _tick_all(lst):
    for i in range(lst.count()):
        lst.item(i).setCheckState(Qt.Checked)


def _sketches(w):
    """The derived sketches of the current studio. The engine hands the window parts only, so build in-process."""
    return w.use.state_before(len(w.use.features)).sketches


def _undo_all(w):
    while _derived(w):
        w.use.undo()
    assert w.studio_engine.settle(120)


def test_the_dialog_brings_in_a_part_and_a_sketch(win):
    w = win
    w.start_feature("derived")
    d = w.dialog
    assert d is not None and d.ftype == "derived"
    _choose(w, w.src_tab)
    parts, sketches = d.widgets["parts"], d.widgets["sketches"]
    assert [i for i, _ in _ticks(parts)] == ["F2/1"] and [i for i, _ in _ticks(sketches)] == [w.sid]
    _tick_all(parts)
    _tick_all(sketches)
    assert d.feature["params"]["parts"] == ["F2/1"] and d.feature["params"]["sketches"] == [w.sid]
    w.commit_dialog()
    assert w.studio_engine.settle(120)
    f = _derived(w)[0]
    assert f["params"]["source"]["tab"] == w.src_tab.id
    assert not w.use.errors.get(f["id"])
    assert list(w.use.state.parts) == [f"{f['id']}/1"] and f"{f['id']}:{w.sid}" in _sketches(w)
    assert w.left.parts.count() == 1                    # the part list shows it
    _undo_all(w)


def test_the_source_list_has_an_unopened_tab_and_not_the_current_one(win):
    w = win
    w.start_feature("derived")
    combo = w.dialog.widgets["source"]
    names = [combo.itemText(i) for i in range(combo.count()) if combo.itemData(i)]
    assert names == ["Src", "Spare"] and "Use" not in names
    assert combo.itemData(combo.findText("Spare")) == w.spare_tab.id
    w.cancel_dialog()


def test_changing_the_source_clears_the_ticks(win):
    w = win
    w.start_feature("derived")
    d = w.dialog
    _choose(w, w.src_tab)
    _tick_all(d.widgets["parts"])
    _tick_all(d.widgets["sketches"])
    assert d.feature["params"]["parts"] and d.feature["params"]["sketches"]
    _choose(w, w.spare_tab)
    assert d.feature["params"]["parts"] == [] and d.feature["params"]["sketches"] == []
    assert d.feature["params"]["source"]["tab"] == w.spare_tab.id
    assert d.widgets["parts"].count() == 0 and [i for i, _ in _ticks(d.widgets["sketches"])] == ["F1"]
    assert not any(on for _i, on in _ticks(d.widgets["sketches"]))
    w.cancel_dialog()


def test_the_three_tick_lists_are_tabs_whose_titles_count_the_ticks(win):
    w = win
    w.start_feature("derived")
    d = w.dialog
    tabs = d.widgets["derived_tabs"]
    titles = lambda: [tabs.tabText(i) for i in range(tabs.count())]
    assert titles() == ["Parts", "Sketches", "Mate connectors"] and tabs.currentIndex() == 0
    for i, key in enumerate(("parts", "sketches", "connectors")):
        assert tabs.widget(i).isAncestorOf(d.widgets[key])
    _choose(w, w.src_tab)
    _tick_all(d.widgets["parts"])
    _tick_all(d.widgets["sketches"])
    assert titles() == ["Parts (1)", "Sketches (1)", "Mate connectors"]
    d.widgets["parts"].item(0).setCheckState(Qt.Unchecked)
    assert titles() == ["Parts", "Sketches (1)", "Mate connectors"]
    _choose(w, w.spare_tab)                                          # another source: no ticks, so no counts
    assert titles() == ["Parts", "Sketches", "Mate connectors"]
    w.cancel_dialog()


def test_editing_opens_on_the_first_tab_with_a_tick(win):
    w = win
    w.start_feature("derived")
    _choose(w, w.src_tab)
    _tick_all(w.dialog.widgets["sketches"])
    w.commit_dialog()
    assert w.studio_engine.settle(120)
    w.start_feature("derived", _derived(w)[0]["id"])
    tabs = w.dialog.widgets["derived_tabs"]
    assert tabs.currentIndex() == 1 and tabs.tabText(1) == "Sketches (1)"
    w.cancel_dialog()
    _undo_all(w)


def test_the_delete_key_on_a_selected_part_opens_delete_part_with_it_picked(win):
    from freeshape.ui.viewport import Pick
    w = win
    w.start_feature("derived")
    _choose(w, w.src_tab)
    _tick_all(w.dialog.widgets["parts"])
    w.commit_dialog()
    assert w.studio_engine.settle(120)
    pid = f"{_derived(w)[0]['id']}/1"
    assert pid in w.doc.state.parts
    w.left.features.clearSelection()
    w.viewport.set_selection([Pick("part", (pid,))])
    w._delete_key()
    assert w.dialog is not None and w.dialog.ftype == "delete_part"
    assert [it[1] for it in w.dialog.fields["parts"].items] == [pid]
    w.cancel_dialog()
    _undo_all(w)


def test_the_command_search_has_a_command_and_the_toolbar_a_button(win):
    w = win
    c = next(c for c in w._palette_commands() if c.key == "derived")
    assert c.name == "Derived" and c.disabled == ""
    assert any(b.property("icon_name") == "derived" for b in w.findChildren(QToolButton))


def test_a_loose_studio_has_the_command_disabled(win, monkeypatch):
    w = win
    monkeypatch.setattr(w, "folder", None)
    c = next(c for c in w._palette_commands() if c.key == "derived")
    assert c.disabled == LOOSE
    w._start_derived()
    assert w.dialog is None and LOOSE in w.status.currentMessage()


def test_editing_the_feature_shows_its_ticks_and_unticking_the_sketch_removes_it(win):
    w = win
    w.start_feature("derived")
    _choose(w, w.src_tab)
    _tick_all(w.dialog.widgets["parts"])
    _tick_all(w.dialog.widgets["sketches"])
    w.commit_dialog()
    assert w.studio_engine.settle(120)
    f = _derived(w)[0]
    key = f"{f['id']}:{w.sid}"
    assert key in _sketches(w)
    w.start_feature("derived", f["id"])
    d = w.dialog
    assert d.widgets["source"].currentData() == w.src_tab.id
    assert _ticks(d.widgets["parts"]) == [("F2/1", True)] and _ticks(d.widgets["sketches"]) == [(w.sid, True)]
    d.widgets["sketches"].item(0).setCheckState(Qt.Unchecked)
    w.commit_dialog()
    assert w.studio_engine.settle(120)
    assert _derived(w)[0]["params"]["sketches"] == []
    assert key not in _sketches(w) and list(w.use.state.parts) == [f"{f['id']}/1"]
    _undo_all(w)


def test_a_red_derived_shows_its_message_in_the_feature_list_tooltip(win):
    w = win
    w.start_feature("derived")
    _choose(w, w.src_tab)
    _tick_all(w.dialog.widgets["parts"])
    w.commit_dialog()
    assert w.studio_engine.settle(120)
    f = _derived(w)[0]
    red = dict(f, params=dict(f["params"], parts=["F9/9"]))     # a part the source does not publish: the Derived is red
    w.use.replace_feature(red)
    assert w.studio_engine.settle(120)
    w._populate_lists()
    msg = w.use.errors[f["id"]]
    assert "no longer published" in msg
    lst = w.left.features
    tips = [lst.item(i).toolTip() for i in range(lst.count())]
    assert msg in tips
    _undo_all(w)


def _source_connector(w, name="Top"):
    from test_derived_placement import top_face_connector
    cid = top_face_connector(w.src_tab.doc, "F2/1", name)["id"]
    assert w.studio_engine.settle(120)
    return cid


def _drop_source_connector(w, cid):
    w.src_tab.doc.delete_features([cid])


def test_the_connector_list_refuses_a_connector_without_its_part(win):
    w = win
    cid = _source_connector(w)
    try:
        w.start_feature("derived")
        d = w.dialog
        _choose(w, w.src_tab)
        conns = d.widgets["connectors"]
        assert [i for i, _ in _ticks(conns)] == [cid] and conns.item(0).text().startswith("Top")
        conns.item(0).setCheckState(Qt.Checked)                      # its part is not ticked: refused
        assert _ticks(conns) == [(cid, False)] and d.feature["params"]["connectors"] == []
        assert "Tick part F2/1 first" in d._derived_note.text()
        _tick_all(d.widgets["parts"])
        conns.item(0).setCheckState(Qt.Checked)
        assert d.feature["params"]["connectors"] == [cid]
        d.widgets["parts"].item(0).setCheckState(Qt.Unchecked)       # unticking the part unticks its connector
        assert _ticks(conns) == [(cid, False)] and d.feature["params"]["connectors"] == []
        _tick_all(d.widgets["parts"])
        conns.item(0).setCheckState(Qt.Checked)
        _choose(w, w.spare_tab)                                      # another source clears the connector ticks
        assert d.feature["params"]["connectors"] == [] and conns.count() == 0
        _choose(w, w.src_tab)
        _tick_all(d.widgets["parts"])
        conns.item(0).setCheckState(Qt.Checked)
        w.commit_dialog()
        assert w.studio_engine.settle(120)
        f = _derived(w)[0]
        assert not w.use.errors.get(f["id"]) and f["params"]["connectors"] == [cid]
        assert f"{f['id']}:{cid}" in w.use.state.connectors
        _undo_all(w)
    finally:
        _drop_source_connector(w, cid)
        w.studio_engine.settle(120)


# ---- I4.2: Location and Placement base

def _sync(w):
    """Wait until the engine has answered every edit and the window has applied the answer (I4.2 tests: the Document
    API returns before the rebuild arrives, and the viewport triads and lists follow it on the event loop)."""
    assert w.studio_engine.settle(120)
    QApplication.processEvents()
    assert w.studio_engine.settle(120)


def _commit(w):
    """Commit the open dialog. Its 90 ms preview timer is stopped first: MainWindow._close_dialog leaves it running,
    and when it fires after the commit it redraws the viewport from the state before the new feature (stale triads)."""
    w.dialog._timer.stop()
    w.commit_dialog()


def _in_process(w):
    """The current studio's state built in-process: the engine hands the window parts only, no sketches."""
    return w.use.state_before(len(w.use.features))


def _local_connector(w, name="Spot", below=None):
    """A part and a Mate connector on its top face in the current studio; returns (box id, connector id).
    With `below` (a box id) only the connector is added, on that box."""
    from freeshape import assembly as A
    from freeshape import features as F
    from freeshape import kernel as K
    if below is None:
        sid = sketch_feature(w.use, lambda s: rect_lines(s, 200, 200, 8, 6))
        st = _in_process(w)
        refs = [F.region_ref(st, sid, r) for r in st.sketches[sid].regions]
        box = add(w.use, "extrude", regions=refs, depth=4, op="new")["id"]
    else:
        box = below
    st = _in_process(w)
    pid = next(p for p in st.parts if p.startswith(f"{box}/"))
    part = st.parts[pid]
    top = K.bbox(part.shape)[1][2]
    i = next(i for i, fc in enumerate(K.faces(part.shape))
             if (K.face_info(fc).get("n") is not None and K.face_info(fc)["n"][2] > 0.9
                 and abs(K.face_info(fc)["c"][2] - top) < 1e-6))
    fr = A.connector_frame(part.shape, "face", i)
    c = add(w.use, "mate_connector", name=name, ref=A._ref_signature(part.shape, "face", i, st.named(pid)),
            kind="face", x_ref=A.connector_x_ref(fr), flip=False)
    _sync(w)
    return box, c["id"]


def _bbox(shape):
    from freeshape import kernel as K
    lo, hi = K.bbox(shape)
    return [round(float(v), 6) for v in (*lo, *hi)]


def _combo_data(cb):
    return [cb.itemData(i) for i in range(cb.count())]


def _cleanup_local(w, *fids):
    w.use.delete_features([f for f in fids if f])
    _sync(w)


def test_location_and_base_are_written_to_placement_and_preview_equals_committed(win):
    w = win
    cid = _source_connector(w)
    box, loc = _local_connector(w)
    try:
        w.start_feature("derived")
        d = w.dialog
        _choose(w, w.src_tab)
        _tick_all(d.widgets["parts"])
        d.widgets["connectors"].item(0).setCheckState(Qt.Checked)
        assert "placement" not in d.feature["params"] and not d.widgets["base"].isEnabled()
        assert _combo_data(d.widgets["base"]) == ["origin", cid]          # Base origin + the ticked connector
        d.widgets["location"].setCurrentIndex(d.widgets["location"].findData(loc))
        assert d.feature["params"]["placement"] == {"location": loc, "base": "origin"}
        d.widgets["base"].setCurrentIndex(d.widgets["base"].findData(cid))
        assert d.feature["params"]["placement"] == {"location": loc, "base": cid}
        d._do_preview()
        assert not d.err.isVisible()
        shown = {pid: _bbox(s) for pid, s in w.viewport.part_shapes.items() if pid.startswith(d.feature["id"] + "/")}
        assert shown
        _commit(w)
        assert w.studio_engine.settle(120)
        f = _derived(w)[0]
        assert not w.use.errors.get(f["id"]) and f["params"]["placement"] == {"location": loc, "base": cid}
        done = {pid: _bbox(p.shape) for pid, p in w.use.state.parts.items() if pid.startswith(f["id"] + "/")}
        assert done == shown
        # base connector (top of the 20x10x5 source box) on the location (top of the local box at 200,200, z=4):
        # the part sits with its top at z=4 and its centre over the local box, not where the source has it
        (bb,) = done.values()
        assert bb[5] == pytest.approx(4.0) and bb[0] < 200 < bb[3] and bb != _bbox(w.src_tab.doc.state.parts["F2/1"].shape)
        _undo_all(w)
    finally:
        _cleanup_local(w, loc, box)
        _drop_source_connector(w, cid)
        w.studio_engine.settle(120)


def test_the_location_list_offers_only_connectors_above_the_derived(win):
    w = win
    box, above = _local_connector(w, "Above")
    try:
        w.start_feature("derived")
        _choose(w, w.src_tab)
        _tick_all(w.dialog.widgets["parts"])
        _commit(w)
        assert w.studio_engine.settle(120)
        fid = _derived(w)[0]["id"]
        _, below = _local_connector(w, "Below", below=box)
        _sync(w)
        w.start_feature("derived", edit_fid=fid)
        loc = w.dialog.widgets["location"]
        assert _combo_data(loc) == [None, above]
        assert above in {t["id"] for t in w.viewport.triads} and below in {t["id"] for t in w.viewport.triads}
        assert [k for k, _ in w.dialog.overlays() if k[0] == "placement_loc"] == [("placement_loc", above)]
        w.cancel_dialog()
        _cleanup_local(w, below)
        _undo_all(w)
    finally:
        _cleanup_local(w, above, box)


def test_a_click_on_a_connector_sets_the_location_and_clearing_it_removes_placement(win):
    from freeshape.ui.viewport import Pick
    w = win
    box, loc = _local_connector(w)
    try:
        w.start_feature("derived")
        d = w.dialog
        _choose(w, w.src_tab)
        _tick_all(d.widgets["parts"])
        d.handle_pick(Pick("overlay", ("placement_loc", loc)))          # the viewport pick of the connector's axes
        assert d.feature["params"]["placement"]["location"] == loc
        _commit(w)
        assert w.studio_engine.settle(120)
        f = _derived(w)[0]
        assert f["params"]["placement"]["location"] == loc
        w.start_feature("derived", edit_fid=f["id"])                    # editing shows it again
        d = w.dialog
        assert d.widgets["location"].currentData() == loc
        d.widgets["location"].setCurrentIndex(0)                        # clear it
        assert "placement" not in d.feature["params"]
        _commit(w)
        assert w.studio_engine.settle(120)
        assert "placement" not in _derived(w)[0]["params"]
        _undo_all(w)
    finally:
        _cleanup_local(w, loc, box)


def test_unticking_the_base_connector_drops_the_base(win):
    w = win
    cid = _source_connector(w)
    box, loc = _local_connector(w)
    try:
        w.start_feature("derived")
        d = w.dialog
        _choose(w, w.src_tab)
        _tick_all(d.widgets["parts"])
        d.widgets["connectors"].item(0).setCheckState(Qt.Checked)
        d.widgets["location"].setCurrentIndex(d.widgets["location"].findData(loc))
        d.widgets["base"].setCurrentIndex(d.widgets["base"].findData(cid))
        d.widgets["connectors"].item(0).setCheckState(Qt.Unchecked)
        assert _combo_data(d.widgets["base"]) == ["origin"]
        assert d.feature["params"]["placement"] == {"location": loc, "base": "origin"}
        w.cancel_dialog()
    finally:
        _cleanup_local(w, loc, box)
        _drop_source_connector(w, cid)
        w.studio_engine.settle(120)


# --------------------------------------------------------------------------- previews (I6.2)
def _icon_rows(lst):
    from freeshape.ui import preview_rows as PR
    ph = PR.placeholder_icon(PR.DERIVED_SIZE).cacheKey()
    return [(lst.item(i).icon().isNull(), lst.item(i).icon().pixmap(PR.DERIVED_SIZE, PR.DERIVED_SIZE).size().width(),
             lst.item(i).icon().cacheKey() == ph) for i in range(lst.count())]


def _open_derived_on_src(w):
    w.start_feature("derived")
    d = w.dialog
    _choose(w, w.src_tab)
    return d


def test_previews_every_row_of_the_three_lists_has_an_icon_of_the_preview_size(win):
    from freeshape.ui import preview_rows as PR
    w = win
    cid = _source_connector(w)
    try:
        d = _open_derived_on_src(w)
        for key in ("parts", "sketches", "connectors"):
            assert d.widgets[key].count() == 1
            assert d.widgets[key].iconSize().width() == PR.DERIVED_SIZE
            assert _icon_rows(d.widgets[key]) == [(False, PR.DERIVED_SIZE, True)]      # placeholder until drawn
        d.drain_previews()
        for key in ("parts", "sketches", "connectors"):
            assert _icon_rows(d.widgets[key]) == [(False, PR.DERIVED_SIZE, False)], key
        w.cancel_dialog()
    finally:
        _drop_source_connector(w, cid)
        w.studio_engine.settle(120)


def test_previews_are_drawn_when_the_list_is_shown_without_blocking(win):
    from freeshape.ui import preview_rows as PR
    w = win
    d = _open_derived_on_src(w)
    lst = d.widgets["parts"]
    w.show()                    # the dialog is a child of the window: its lists are visible only with it
    try:
        assert lst.isVisible()
        for _ in range(50):
            QApplication.processEvents()
            if not _icon_rows(lst)[0][2]:
                break
        assert _icon_rows(lst) == [(False, PR.DERIVED_SIZE, False)]
    finally:
        w.cancel_dialog()
        w.hide()


def test_previews_second_open_draws_nothing_new(win):
    from freeshape.ui import preview_rows as PR
    w = win
    d = _open_derived_on_src(w)
    d.drain_previews()
    w.cancel_dialog()
    drawn = PR.stats["drawn"]
    d = _open_derived_on_src(w)
    d.drain_previews()
    assert PR.stats["drawn"] == drawn
    for key in ("parts", "sketches"):
        assert _icon_rows(d.widgets[key]) == [(False, PR.DERIVED_SIZE, False)]
    w.cancel_dialog()


def test_previews_a_source_in_error_shows_placeholders_and_the_error_and_the_dialog_opens(win, monkeypatch):
    import dataclasses
    from freeshape.ui import preview_rows as PR
    w = win
    d = _open_derived_on_src(w)
    pkg, _ = d._derived_package()
    bad = dataclasses.replace(pkg, error="Fillet: no edges")
    monkeypatch.setattr(d, "_derived_package", lambda: (bad, ""))
    before = PR.stats["drawn"]
    for fill in d._derived_fills:
        fill()
    d.drain_previews()
    assert PR.stats["drawn"] == before
    for key in ("parts", "sketches"):
        lst = d.widgets[key]
        assert lst.count() == 1 and _icon_rows(lst) == [(False, PR.DERIVED_SIZE, True)]
        assert "Fillet: no edges" in lst.item(0).toolTip()
    assert "Fillet: no edges" in d._derived_note.text()
    assert d.isEnabled()
    w.cancel_dialog()
