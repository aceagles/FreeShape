"""Reference shots of the desktop program, for judging the client's Desktop mode beside it (card I9.13).

Starts the desktop MainWindow of this checkout under the Qt offscreen platform at 1440x900 and 1920x1080, opens a copy
of an example Document, drives the window into named states and saves a grab of it to
web/shots/desktop/<state>-<w>x<h>.png (not committed). scripts/web/shots.sh runs this first and web/e2e/shots.run.ts
puts the shot left of the client's Desktop-mode state that names it.

    python scripts/web/desktop_shots.py                      every state, both sizes
    python scripts/web/desktop_shots.py desktop-studio       only the named states
    python scripts/web/desktop_shots.py --sizes 1440x900 desktop-menu-view

The desktop is reached only through this script: it imports freeshape.ui.mainwindow from this checkout. It never
opens the public preview of the desktop or any URL.

A menu, a context menu and a message box are windows of their own, and offscreen they cannot be placed outside the
platform's small screen, so each is grabbed as a widget and pasted onto the window grab where the desktop opens it
(a menu under its button, the context menu at the click, the Keyboard shortcuts list centred). The shortcuts list is
a modal message box: it is grabbed from a timer while it is open, then closed.

The 3D view is a QOpenGLWidget. Whether it grabs as the model or as a blank (one flat colour) depends on the
platform's GL; the report line of each state says which it was ("3D view: drawn" or "3D view: blank"). Blank or not,
the shot is kept: the chrome (top bar, toolbars, left column, tab strip, dialogs, menus) is what the shots are for.

A state whose steps fail is reported and skipped, the others are still captured. Exit non-zero only when a state could
not be captured. Each state starts from a fresh window on a fresh copy of the Document.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import traceback

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")

SIZES = [(1440, 900), (1920, 1080)]
OUT = os.path.join(ROOT, "web", "shots", "desktop")
EXAMPLES = os.path.join(ROOT, "examples", "documents")


class _Settings(dict):
    """A QSettings stand-in that keeps a run from touching the real settings on disk."""

    def value(self, k, default=None):
        return self.get(k, default)

    def setValue(self, k, v):
        self[k] = v


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _pump(n=6):
    app = _app()
    for _ in range(n):
        app.processEvents()


def open_window(document: str, width: int, height: int, workdir: str, tab_kind: str | None = None):
    """A desktop MainWindow (engine on, as the real program starts it) of width x height showing a copy of the
    example Document `document` ("bracket" or "hinge"), its engine settled. `tab_kind` picks the tab to show."""
    _app()
    from freeshape.ui.mainwindow import MainWindow
    copy = os.path.join(workdir, document)
    shutil.copytree(os.path.join(EXAMPLES, document), copy)
    w = MainWindow(engine=True)
    w.settings = _Settings()
    w.resize(width, height)
    w.move(0, 0)
    w.show()
    _pump()
    if w.open_document(copy) is None:
        raise RuntimeError(f"the {document} Document did not open")
    if tab_kind is not None:
        for i in range(w.tabs.count()):
            d = w.docs.get(w.tabs.tabData(i))
            if d is not None and d.kind == tab_kind:
                w.tabs.setCurrentIndex(i)
                break
        else:
            raise RuntimeError(f"the {document} Document has no {tab_kind} tab")
    for _ in range(3):
        if not w.studio_engine.settle(180):
            raise RuntimeError("the engine did not answer")
        _pump()
    w.resize(width, height)
    _pump()
    return w


def close_window(w):
    """Close without the save question and stop the engine."""
    try:
        for d in list(w.docs.values()):
            d.dirty = False
        w._leave_modes()
    except Exception:
        pass
    try:
        w.studio_engine.shutdown()
    except Exception:
        pass
    w.close()
    _pump()
    w.deleteLater()
    _pump()


def grab(w):
    """The window as a QImage."""
    _pump()
    return w.grab().toImage()


def paste(base, image, at):
    """Draw `image` (a grabbed popup) onto the window grab at `at` (a QPoint in window coordinates)."""
    from PySide6.QtGui import QPainter
    p = QPainter(base)
    p.drawImage(at, image)
    p.end()
    return base


def _flat(img) -> bool:
    """True when every sampled pixel of the image is one colour."""
    img = img.convertToFormat(img.Format.Format_RGB32)
    first = img.pixel(0, 0)
    for y in range(0, img.height(), 2):
        for x in range(0, img.width(), 2):
            if img.pixel(x, y) != first:
                return False
    return True


def top_flat(image, rows=44) -> bool:
    """True when the top `rows` pixel rows of the image are one colour."""
    return _flat(image.copy(0, 0, image.width(), rows))


def view_is_blank(w) -> bool:
    """True when the 3D view's own grab is one colour (the GL view did not draw)."""
    _pump()
    return _flat(w.viewport.grab().toImage())


# ---------------------------------------------------------------- states: each returns the QImage to save
def _menu_shot(w, text: str):
    """The menu of the top bar's button called `text`, drawn under the button on the window grab."""
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QToolButton
    btn = next(b for b in w.findChildren(QToolButton) if b.text().startswith(text) and b.menu() is not None)
    base = grab(w)
    m = btn.menu()
    m.aboutToShow.emit()
    m.ensurePolished()
    m.resize(m.sizeHint())
    return paste(base, m.grab().toImage(), w.mapFromGlobal(btn.mapToGlobal(QPoint(0, btn.height()))))


def st_studio(w):
    return grab(w)


def st_menu_document(w):
    return _menu_shot(w, "Document")


def st_menu_view(w):
    return _menu_shot(w, "View")


def st_menu_select(w):
    return _menu_shot(w, "Select")


def st_dialog(w):
    w.edit_feature(next(f["id"] for f in w.doc.features if f["type"] == "extrude"))
    _pump(12)
    if w.mode != "dialog":
        raise RuntimeError("the Extrude dialog did not open")
    return grab(w)


def st_sketch(w):
    w.edit_feature(next(f["id"] for f in w.doc.features if f["type"] == "sketch"))
    _pump(12)
    if w.mode != "sketch":
        raise RuntimeError("the sketch did not open")
    return grab(w)


def st_assembly(w):
    return grab(w)


def _find_face(vp):
    """A (pick, x, y) of a face of the model, searching outwards from the middle of the 3D view."""
    cx, cy = vp.width() // 2, vp.height() // 2
    for r in range(0, min(cx, cy), 10):
        for dx in range(-r, r + 1, 10):
            for dy in (-r, r) if abs(dx) != r else range(-r, r + 1, 10):
                hit = vp.pick(cx + dx, cy + dy)
                if hit is not None and hit.kind == "face":
                    return hit, cx + dx, cy + dy
    raise RuntimeError("no face found to put the context menu on")


def st_context(w):
    """The viewport context menu on a face, drawn where the desktop opens it: at the click."""
    from PySide6.QtCore import QPoint, QTimer
    from PySide6.QtWidgets import QApplication
    vp = w.viewport
    pk, x, y = _find_face(vp)
    vp.set_selection([pk])
    base = grab(w)
    gpos = vp.mapToGlobal(QPoint(x, y))
    got = {}

    def snap():
        m = QApplication.activePopupWidget()
        if m is not None:
            got["img"] = m.grab().toImage()
            m.close()

    QTimer.singleShot(400, snap)
    w._viewport_context(pk, gpos)          # QMenu.exec: returns when snap() closes the menu
    if "img" not in got:
        raise RuntimeError("the context menu did not open")
    return paste(base, got["img"], w.mapFromGlobal(gpos))


def st_palette(w):
    w.open_command_search()
    _pump(12)
    return grab(w)


def st_shortcuts(w):
    """The Keyboard shortcuts list: a modal message box, grabbed from a timer while it is open."""
    from PySide6.QtCore import QPoint, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox
    base = grab(w)
    got = {}

    def snap():
        box = next((b for b in QApplication.topLevelWidgets() if isinstance(b, QMessageBox) and b.isVisible()), None)
        if box is not None:
            got["img"] = box.grab().toImage()
            got["size"] = box.size()
            box.close()

    QTimer.singleShot(400, snap)
    w.show_shortcuts()                      # modal: returns when snap() closes the box
    if "img" not in got:
        raise RuntimeError("the shortcuts list did not open")
    s = got["size"]
    return paste(base, got["img"], QPoint(max(0, (w.width() - s.width()) // 2), max(0, (w.height() - s.height()) // 2)))


# name -> (example Document, tab kind to show, steps)
STATES = {
    "desktop-studio": ("bracket", None, st_studio),
    "desktop-menu-document": ("bracket", None, st_menu_document),
    "desktop-menu-view": ("bracket", None, st_menu_view),
    "desktop-menu-select": ("bracket", None, st_menu_select),
    "desktop-dialog": ("bracket", None, st_dialog),
    "desktop-sketch": ("bracket", None, st_sketch),
    "desktop-assembly": ("hinge", "assembly", st_assembly),
    "desktop-context": ("bracket", None, st_context),
    "desktop-palette": ("bracket", None, st_palette),
    "desktop-shortcuts": ("bracket", None, st_shortcuts),
}


def capture_state(name: str, width: int, height: int, out_dir: str = OUT) -> str:
    """Capture one state at one size and write <out_dir>/<name>-<w>x<h>.png. Returns the report line."""
    document, tab_kind, steps = STATES[name]
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{name}-{width}x{height}.png")
    with tempfile.TemporaryDirectory(prefix="desktop-shots-") as work:
        w = open_window(document, width, height, work, tab_kind)
        try:
            blank = view_is_blank(w)        # before the steps: a palette or a selection draws over the view
            image = steps(w)
            if not image.save(path):
                raise RuntimeError(f"could not write {path}")
        finally:
            close_window(w)
    return f"wrote {os.path.relpath(path, ROOT)} ({image.width()}x{image.height()}, 3D view: {'blank' if blank else 'drawn'})"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("states", nargs="*", help="state names (default: all)")
    ap.add_argument("--sizes", default=",".join(f"{w}x{h}" for w, h in SIZES), help="comma separated WxH")
    ap.add_argument("--out", default=OUT, help="output directory")
    a = ap.parse_args(argv)
    names = a.states or list(STATES)
    unknown = [n for n in names if n not in STATES]
    if unknown:
        print(f"unknown state(s): {', '.join(unknown)}; known: {', '.join(STATES)}", file=sys.stderr)
        return 2
    sizes = [tuple(int(v) for v in s.split("x")) for s in a.sizes.split(",")]
    failed = []
    for name in names:
        for width, height in sizes:
            try:
                print(capture_state(name, width, height, a.out), flush=True)
            except Exception as e:      # a failed state is reported and skipped
                traceback.print_exc()
                failed.append(f"{name}-{width}x{height}: {e}")
    for f in failed:
        print(f"NOT CAPTURED {f}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
