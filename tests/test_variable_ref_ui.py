"""I2.6: the Variable Studio reference in the window -- its dialog, its command and the override mark.

One offscreen window with the process executor serves the module: a Document with Variable Studio V (w = 20 mm,
t = 3 mm) and Part Studio A holding a Variable `w = 7`. The reference is added through `start_feature` and the
dialog's own widgets, as a person would."""
import os
import signal

import pytest

from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape.engine import process as P

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

REF = "variable_studio_ref"


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
    root = tmp_path_factory.mktemp("refui")
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
    v = fd.add_tab("variables", "V")
    v.doc.add_row("w", "20 mm")
    v.doc.add_row("t", "3 mm")
    a = fd.add_tab("studio", "A")
    fd.save()
    before = set(_children())
    w = MW.MainWindow(engine=True)
    from PySide6.QtCore import QSettings
    w.settings = QSettings(str(root / "settings.ini"), QSettings.IniFormat)
    w.open_document(fd.path)
    assert w.studio_engine.settle(240)
    w.a = w.folder.tab(a.id).doc
    w.v_tab = w.folder.tab(v.id)
    w.tabs.setCurrentIndex(w._tab_index(w.a))
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


def _ref_features(w):
    return [f for f in w.a.features if f["type"] == REF]


def _add_ref(w, untick=()):
    """Open the dialog on the current Part Studio, untick `untick` (names) and commit it."""
    w.start_feature(REF)
    d = w.dialog
    assert d is not None and d.ftype == REF
    lst = d.widgets["names"]
    for i in range(lst.count()):
        if lst.item(i).data(Qt.UserRole) in untick:
            lst.item(i).setCheckState(Qt.Unchecked)
    w.commit_dialog()
    assert w.studio_engine.settle(120)
    return _ref_features(w)[-1]


def test_dialog_creates_a_reference_to_the_chosen_studio_and_its_names_resolve(win):
    w = win
    w.start_feature(REF)
    d = w.dialog
    combo = d.widgets["source"]
    assert [combo.itemText(i) for i in range(combo.count())] == ["V"]
    assert combo.currentData() == w.v_tab.id
    w.commit_dialog()
    assert w.studio_engine.settle(120)
    f = _ref_features(w)[0]
    assert f["params"]["source"]["tab"] == w.v_tab.id and "names" not in f["params"]
    assert not w.a.errors.get(f["id"])
    assert w.a.state.vars["w"] == 20.0 and w.a.state.vars["t"] == 3.0       # #w resolves
    w.a.undo()
    assert w.studio_engine.settle(120)


def test_the_command_search_has_the_command(win):
    cmds = win._palette_commands()
    c = next(c for c in cmds if c.key == REF)
    assert c.name == "Variable Studio reference"


def test_the_dialog_lists_the_studios_variables_and_unticking_one_drops_it(win):
    w = win
    w.start_feature(REF)
    d = w.dialog
    lst, all_cb = d.widgets["names"], d.widgets["names_all"]
    assert [lst.item(i).data(Qt.UserRole) for i in range(lst.count())] == ["w", "t"]
    assert all_cb.isChecked() and "names" not in d.feature["params"]
    lst.item(1).setCheckState(Qt.Unchecked)                  # untick t
    assert d.feature["params"]["names"] == ["w"] and not all_cb.isChecked()
    w.commit_dialog()
    assert w.studio_engine.settle(120)
    f = _ref_features(w)[0]
    assert f["params"]["names"] == ["w"]
    assert w.a.state.vars == {"w": 20.0}

    # editing it again: the unticked name is still unticked, and All variables removes the names key
    w.start_feature(REF, f["id"])
    d = w.dialog
    lst = d.widgets["names"]
    assert [lst.item(i).checkState() for i in range(lst.count())] == [Qt.Checked, Qt.Unchecked]
    d.widgets["names_all"].setChecked(True)
    assert "names" not in d.feature["params"]
    w.commit_dialog()
    assert w.studio_engine.settle(120)
    assert "names" not in _ref_features(w)[0]["params"]
    assert w.a.state.vars["t"] == 3.0
    w.a.undo()
    w.a.undo()
    assert w.studio_engine.settle(120)


def test_the_variables_panel_marks_the_overridden_row_only(win):
    w = win
    _add_ref(w)
    local = {"id": w.a.new_id(), "type": "variable", "name": "w", "suppressed": False,
             "params": {"name": "w", "expr": "7"}}
    w.a.add_feature(local)                                   # a later definition of w overrides the reference's
    assert w.studio_engine.settle(120)
    w._populate_lists()
    panel = w.defined_vars
    names = [panel.item(r, 0).text() for r in range(panel.rowCount())]
    assert names == ["#w", "#t", "#w"]
    marks = [panel.overridden(r) for r in range(panel.rowCount())]
    assert marks == [True, False, False]                     # the reference's w is overridden; t and the local w are in force
    assert panel.item(0, 2).text() == "overridden" and panel.item(2, 2).text() != "overridden"
    assert w.a.state.vars["w"] == 7
