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
