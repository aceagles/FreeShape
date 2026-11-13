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


def run_action(w, name):
    """Trigger the window's QAction with objectName `name`, as a menu click would."""
    from PySide6.QtGui import QAction
    act = w.findChild(QAction, name)
    check(act is not None, f"no action named {name!r}")
    act.trigger()


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


# --- added for SS1 (I1.9): the layout sketch Part Studio ---
def layout_sketch(s):
    """Two construction lines (20,0)-(50,0) and (50,0)-(50,25) and a free point at (30,12), none at the sketch origin."""
    a, b, c = s.add_point(20, 0), s.add_point(50, 0), s.add_point(50, 25)
    s.add_line(a, b, construction=True)
    s.add_line(b, c, construction=True)
    s.add_point_entity(s.add_point(30, 12))


def layout_studio():
    """A Part Studio Document holding the layout sketch (on the Front plane) and a 40 x 40 x 20 block.
    Returns (document, sketch feature id, block part id)."""
    from conftest import FRONT, add, rect_lines, region_refs, sketch_feature
    from freeshape.document import Document
    doc = Document()
    sid = sketch_feature(doc, layout_sketch, FRONT)
    rid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 40, 40), FRONT)
    add(doc, "extrude", regions=region_refs(doc, rid), depth=20, op="new")
    check(not doc.errors, doc.errors)
    return doc, sid, next(iter(doc.state.parts))
