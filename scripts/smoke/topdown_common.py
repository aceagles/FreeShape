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
