"""Helpers shared by the Top-Down Design gate smokes (scripts/smoke/st<n>_*.py). Not a smoke itself.

Each gate script imports this module after putting the repository root and tests/ on sys.path. Nothing here
knows about a particular slice. Call `isolate()` before importing anything from freeshape, so no real user
folder is touched.
"""
import os
import shutil
import sys
import tempfile
import traceback


class Fail(Exception):
    """A smoke check failed; the message says what was expected."""


def check(cond, msg):
    """Raise Fail(msg) unless `cond` is truthy."""
    if not cond:
        raise Fail(msg)


def say(msg):
    """Print one result line now (unbuffered, so the order survives a crash)."""
    print(msg, flush=True)


def isolate(prefix):
    """Make a temp directory, point FREESHAPE_DATA_DIR, XDG_DATA_HOME and HOME into it, and select the offscreen
    Qt platform. Returns the directory."""
    tmp = tempfile.mkdtemp(prefix=prefix)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["FREESHAPE_DATA_DIR"] = os.path.join(tmp, "appdata")
    os.environ["XDG_DATA_HOME"] = os.path.join(tmp, "xdg")
    os.environ["HOME"] = os.path.join(tmp, "home")
    for d in ("appdata", "xdg", "home", "autosave", "thumbs"):
        os.makedirs(os.path.join(tmp, d))
    return tmp


def new_window(tmp):
    """An offscreen MainWindow with autosave and thumbnails inside `tmp` and message boxes answered silently.
    Returns (QApplication, MainWindow); keep the QApplication alive."""
    from PySide6.QtWidgets import QApplication, QMessageBox
    from freeshape.ui import home as H
    from freeshape.ui import mainwindow as MW
    app = QApplication.instance() or QApplication([])
    MW.MainWindow.autosave_root = staticmethod(lambda: os.path.join(tmp, "autosave"))
    MW.MainWindow.autosave_dir = classmethod(lambda cls: os.path.join(tmp, "autosave"))
    H.thumbs_dir = lambda: os.path.join(tmp, "thumbs")
    for name in ("warning", "critical", "information"):
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))
    return app, MW.MainWindow()


def run_action(w, name, values=None):
    """Trigger the window's QAction with objectName `name`, as a menu click would. `values`, when given, answers the
    dialog the action opens: the keyword arguments of OtherDocumentDialog.choose (document, version, tab, part),
    for "insert_other_document"; the dialog is then accepted without being shown."""
    from PySide6.QtGui import QAction
    act = w.findChild(QAction, name)
    check(act is not None, f"no action named {name!r}")
    if values is None:
        act.trigger()
        return
    from PySide6.QtWidgets import QDialog
    from freeshape.ui import document_picker as DP
    real = DP.OtherDocumentDialog.exec
    chosen = []

    def answer(self):
        chosen.append(self.choose(**values))
        return QDialog.Accepted if chosen[-1] else QDialog.Rejected
    DP.OtherDocumentDialog.exec = answer
    try:
        act.trigger()
    finally:
        DP.OtherDocumentDialog.exec = real
    check(chosen == [True], f"the picker could not choose {values}")


def run_command(w, key):
    """Run the command-palette command with key `key` on the current tab."""
    cmds = {c.key: c for c in w._palette_commands()}
    check(key in cmds, f"no palette command {key!r}")
    cmds[key].action()


def run_smoke(tag, tmp, body):
    """Run `body()`; print `<tag> OK` and return, or print `<tag> FAILED: why` and exit 1. Removes `tmp` either way."""
    try:
        body()
    except BaseException as e:                      # noqa: BLE001 -- any failure fails the gate
        say(f"{tag} FAILED: {e}")
        if not isinstance(e, Fail):
            traceback.print_exc()
        shutil.rmtree(tmp, ignore_errors=True)
        sys.exit(1)
    say(f"{tag} OK")
    shutil.rmtree(tmp, ignore_errors=True)


# --- added for ST3 (I3.7) ---
def run_menu_action(open_menu, name, modules=()):
    """Open a context menu with `open_menu()`, find its action with objectName `name` and trigger it, as a click would.
    A real QMenu.exec blocks forever offscreen, so each module in `modules` (the ones that look QMenu up by name)
    and QtWidgets get a recording QMenu whose exec/popup return at once; they are restored afterwards."""
    from PySide6 import QtWidgets
    from PySide6.QtWidgets import QMenu
    seen, keep = [], []

    class RecMenu(QMenu):
        def exec(self, *a, **k):
            keep.append(self)
            seen.extend(self.actions())

        def exec_(self, *a, **k):
            return self.exec(*a, **k)

        def popup(self, *a, **k):
            keep.append(self)
            seen.extend(self.actions())

    targets = [QtWidgets, *modules]
    old = [getattr(m, "QMenu", None) for m in targets]
    for m in targets:
        m.QMenu = RecMenu
    try:
        open_menu()
    finally:
        for m, o in zip(targets, old):
            m.QMenu = o
    act = next((a for a in seen if a.objectName() == name), None)
    check(act is not None, f"the menu has no action named {name!r}")
    act.trigger()
    return act


# --- added for ST2 (I2.8) ---
_OPENED = []


def install_open_spy():
    """In a worker process: record every path passed to the builtin open(). Idempotent."""
    import builtins
    if getattr(builtins.open, "_td_spy", False):
        return
    real = builtins.open

    def spy(file, *a, **k):
        _OPENED.append(os.fspath(file) if isinstance(file, (str, bytes, os.PathLike)) else file)
        return real(file, *a, **k)
    spy._td_spy = True
    builtins.open = spy
    open(os.devnull).close()                  # a control: the spy must record this one


def opens_seen():
    """In a worker process: the paths install_open_spy recorded."""
    return list(_OPENED)


def spy_all_workers(w):
    """Install the open spy in every worker of the window's tabs that has one."""
    for tab in w.folder.tabs:
        rec = w.studio_engine._recs.get(id(tab.doc))
        if rec is not None and rec.tab is not None:
            w.studio_engine.in_engine(tab.doc, lambda eng, t: eng.run_in_worker(t, install_open_spy))


def worker_opens(w):
    """Every path opened so far in each worker the spy is in: a list of (tab name, path). Each worker's list starts
    with the spy's control open of os.devnull, so a worker without the spy shows no entries at all."""
    out = []
    for tab in w.folder.tabs:
        rec = w.studio_engine._recs.get(id(tab.doc))
        if rec is not None and rec.tab is not None:
            seen = w.studio_engine.in_engine(tab.doc, lambda eng, t: eng.run_in_worker(t, opens_seen))
            out += [(tab.name, p) for p in seen if isinstance(p, str)]
    return out


class TwoDocuments:
    """Library and Project, saved under `root` and registered in the temp registry (FREESHAPE_DATA_DIR, so the
    window finds them too). Library: Variable Studio Dims (w = 20 mm, t = 3 mm), Part Studio Parts (Plate, a 20 x 20 x
    10 box, and Rod, a 6 x 6 x 30 box) and Version v1. Project: Part Studios Mine (a 7 x 7 x 3 box) and Use (empty)
    and Assembly Asm (empty). Library's Parts and Project's Mine are both tab T1: the ids collide on purpose.
    Nothing is held open: `make_v2()` reopens Library from its folder."""

    def __init__(self, root):
        from conftest import TOP, add, rect_lines, region_refs, sketch_feature
        from freeshape import doc_folder as DF
        from freeshape import doc_registry as R
        from freeshape import kernel as K
        from freeshape.engine.encode import Encoder
        self.reg = R.default_registry()
        self.lib_path = os.path.join(root, "Library")
        self.proj_path = os.path.join(root, "Project")

        def box(studio, x, w, depth):
            sid = sketch_feature(studio, lambda s: rect_lines(s, x, 0, x + w, w), TOP)
            add(studio, "extrude", regions=region_refs(studio, sid), depth=depth, op="new")
            check(not studio.errors, studio.errors)

        def parts(studio):
            return {p.name: (K.volume(p.shape), Encoder(meshes=False).brep(p.shape)[1])
                    for p in studio.state.parts.values()}
        lib = DF.FolderDocument.create(self.lib_path, name="Library", registry=self.reg)
        plates = lib.add_tab("studio", "Parts")
        box(plates.doc, 0, 20.0, 10.0)
        box(plates.doc, 30, 6.0, 30.0)
        dims = lib.add_tab("variables", "Dims")
        dims.doc.add_row("w", "20 mm")
        dims.doc.add_row("t", "3 mm")
        self.lib_id, self.parts_tab, self.dims_tab = lib.id, plates.id, dims.id
        self.v1 = parts(plates.doc)
        check(len(self.v1) == 2, self.v1)
        self.plate = next(iter(plates.doc.state.parts))                  # the first extrude: Plate
        self.plate_name = plates.doc.state.parts[self.plate].name
        lib.create_version("v1", "first")
        lib.save()
        proj = DF.FolderDocument.create(self.proj_path, name="Project", registry=self.reg)
        mine = proj.add_tab("studio", "Mine")
        box(mine.doc, 0, 7.0, 3.0)
        use = proj.add_tab("studio", "Use")
        asm = proj.add_tab("assembly", "Asm")
        self.proj_id, self.mine_tab, self.use_tab, self.asm_tab = proj.id, mine.id, use.id, asm.id
        self.mine = parts(mine.doc)
        proj.save()
        self.v2 = None

    def make_v2(self):
        """Library v2: Plate 25 mm tall, w = 35 mm. Reopens Library from its folder, as its author would."""
        from freeshape import doc_folder as DF
        from freeshape import kernel as K
        from freeshape.engine.encode import Encoder
        lib = DF.FolderDocument.open_document(self.lib_path, registry=self.reg)
        plates, dims = lib.tab(self.parts_tab), lib.tab(self.dims_tab)
        ext = next(f for f in plates.doc.features if f["type"] == "extrude")
        ext["params"]["depth"] = 25.0
        plates.doc.replace_feature(ext)
        check(not plates.doc.errors, plates.doc.errors)
        dims.doc.set_row(0, expression="35 mm")
        part = plates.doc.state.parts[self.plate]
        self.v2 = (K.volume(part.shape), Encoder(meshes=False).brep(part.shape)[1])
        lib.create_version("v2", "taller, wider")
        lib.save()


def refresh_workers(w, docs):
    """Make the workers of `docs` resolve their links again, as the worker's own refresh edit does."""
    for doc in docs:
        w.studio_engine.in_engine(doc, lambda eng, t: eng.submit_edit(t, {"op": "refresh"}))


# --- added for ST1 (I1.12) ---
def new_engine_window(tmp):
    """Like `new_window`, but a `MainWindow(engine=True)`: one worker process per tab. Returns (QApplication,
    MainWindow). End with `close_engine_window(w)`, or the worker processes keep the run from exiting."""
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QMessageBox
    from freeshape.ui import home as H
    from freeshape.ui import mainwindow as MW
    app = QApplication.instance() or QApplication([])
    MW.MainWindow.autosave_root = staticmethod(lambda: os.path.join(tmp, "autosave"))
    MW.MainWindow.autosave_dir = classmethod(lambda cls: os.path.join(tmp, "autosave"))
    H.thumbs_dir = lambda: os.path.join(tmp, "thumbs")
    for name in ("warning", "critical", "information"):
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))
    w = MW.MainWindow(engine=True)
    w.settings = QSettings(os.path.join(tmp, "settings.ini"), QSettings.IniFormat)
    return app, w


def close_engine_window(w):
    """Stop the window's engine and drop the spare workers."""
    from freeshape.engine import process as P
    w.studio_engine.shutdown()
    P.SPARES.clear()


def settle(w, timeout=120):
    """Wait until the window's engine has answered every request."""
    check(w.studio_engine.settle(timeout), f"the engine did not settle in {timeout} s")


def set_dialog_values(dialog, values):
    """Set the open FeatureDialog's rows by key, in the order given. A combo takes the item data to choose; a list
    takes the ids to tick (others are unticked); a check box a bool; a line edit text; the Derived dialog's
    "document" picker takes the Version of this Document to pin to (None: Follow workspace)."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QCheckBox, QComboBox, QLineEdit, QListWidget
    from freeshape.ui.document_picker import DocumentVersionPicker
    for key, val in values.items():
        wd = dialog.widgets.get(key)
        check(wd is not None, f"the dialog has no row {key!r}: {sorted(dialog.widgets)}")
        if isinstance(wd, DocumentVersionPicker):
            wd.set_local_pin(val)
        elif isinstance(wd, QComboBox):
            i = wd.findData(val)
            check(i >= 0, f"row {key!r} offers no {val!r}")
            wd.setCurrentIndex(i)
        elif isinstance(wd, QListWidget):
            ids = [wd.item(i).data(Qt.UserRole) for i in range(wd.count())]
            check(all(v in ids for v in val), f"row {key!r} lists {ids}, not {list(val)}")
            for i in range(wd.count()):
                wd.item(i).setCheckState(Qt.Checked if ids[i] in val else Qt.Unchecked)
        elif isinstance(wd, QCheckBox):
            wd.setChecked(bool(val))
        elif isinstance(wd, QLineEdit):
            wd.setText(str(val))
            wd.editingFinished.emit()
        else:
            raise Fail(f"cannot set row {key!r} of type {type(wd).__name__}")


def commit_open_dialog(w):
    """Accept the window's open dialog: stop its preview timer first (it can fire after a tab switch), then
    `commit_dialog`."""
    check(w.dialog is not None, "no dialog is open")
    t = getattr(w.dialog, "_timer", None)
    if t is not None:
        t.stop()
    w.commit_dialog()


def run_command_with(w, key, values):
    """Run the command-palette command `key`, set the dialog it opens by row key (`set_dialog_values`) and commit."""
    run_command(w, key)
    check(w.dialog is not None, f"command {key!r} opened no dialog")
    set_dialog_values(w.dialog, values)
    commit_open_dialog(w)


# --- added for ST6 (I6.4) ---
def st6_wait(app, done, what, seconds=30.0):
    """Run the Qt event loop until `done()` is true, as the user's wait would; fail after `seconds`."""
    import time
    end = time.monotonic() + seconds
    while not done():
        check(time.monotonic() < end, f"timed out waiting for {what}")
        app.processEvents()
        time.sleep(0.01)
