"""Shared helpers for tests that drive the real MainWindow offscreen (importable like conftest: `import ui_helpers`)."""
import os

PLATE_W, PLATE_D, PLATE_T = 100.0, 75.0, 10.0
M3_HOLE, M4_HOLE, TINY_HOLE = 3.4, 4.5, 1.0         # clearance diameters for M3 and M4; 1.0 matches no size


def plate_holes():
    """(x, y, diameter) of the 13 holes: 8 for M3, 4 for M4, one of 1.0 mm that matches no size."""
    xy = [(15 + 25 * i, 15 + 25 * j) for j in range(3) for i in range(4)]
    dias = [M3_HOLE] * 8 + [M4_HOLE] * 4
    return [(x, y, d) for (x, y), d in zip(xy, dias)] + [(92.0, 68.0, TINY_HOLE)]


def plate_studio():
    """A Part Studio Document holding one 100 x 75 x 10 plate with the 13 holes of plate_holes()."""
    from conftest import add, region_refs, sketch_feature
    from freeshape import kernel as K
    from freeshape.document import Document
    import numpy as np

    def build(s):
        pts = [s.add_point(0, 0), s.add_point(PLATE_W, 0), s.add_point(PLATE_W, PLATE_D), s.add_point(0, PLATE_D)]
        for a, b in zip(pts, pts[1:] + pts[:1]):
            s.add_line(a, b)
        for x, y, d in plate_holes():
            s.add_circle(s.add_point(x, y), d / 2)

    doc = Document()
    doc.name = "Plate"
    sid = sketch_feature(doc, build)
    areas = [K.face_info(r)["area"] for r in doc.state.sketches[sid].regions]
    big = region_refs(doc, sid, lambda i: abs(i["area"] - max(areas)) < 1e-6)
    add(doc, "extrude", regions=big, depth=PLATE_T, op="new")
    assert not doc.errors, doc.errors
    return doc


class Settings(dict):
    """A QSettings stand-in that keeps a test from touching the real settings on disk."""

    def value(self, k, default=None):
        return self.get(k, default)

    def setValue(self, k, v):
        self[k] = v


def plate_assembly_window(tmp_path):
    """Write the 13-hole plate Part Studio and an Assembly holding it, open the Assembly in an offscreen
    MainWindow (settings stand-in, no recovery) and return the window.

    `window.doc` is the AssemblyDoc with one plate instance; `window.asm` is the AssemblyController. The
    controller may already have opened its Insert panel (a new Assembly opens it after 50 ms when a Part Studio
    has parts), so use `window.asm.insert_panel` when it is not None; never call `toggle_insert` blindly, it
    closes an open panel. The caller owns the window: call `close_window(window)` when done."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")
    from PySide6.QtWidgets import QApplication
    from freeshape.assembly import AssemblyDoc
    studio = plate_studio()
    studio_path = str(tmp_path / "plate.fshape")
    studio.save(studio_path)
    pid = next(iter(studio.state.parts))
    asm = AssemblyDoc()
    asm.name = "Plate assembly"
    asm.resolver = lambda src: studio.state.parts[src["part"]]
    asm.insert({"kind": "studio", "uid": studio.uid, "part": pid, "path": studio_path, "studio_name": studio.name},
               T=None)
    asm_path = str(tmp_path / "plate.fasm")
    asm.save(asm_path)

    app = QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    w.settings = Settings()
    app.processEvents()
    w.open_any(asm_path)
    app.processEvents()
    assert w.doc is not None and w.doc.kind == "assembly", "the Assembly did not open"
    assert not w.doc.errors, w.doc.errors
    return w


def close_window(w):
    """Close a window from plate_assembly_window. A dirty document would make close() open a modal save dialog
    and block the test, so every document is marked clean first."""
    for d in w.docs.values():
        d.dirty = False
    w.close()
