"""Import smoke (slice S4): insert an SVG and a DXF into a sketch, end to end in a real MainWindow.

Gate script of the SVG and DXF import plan. The window is the real one, offscreen, in a sketch on the Top plane;
the window is MainWindow(engine=True), as the app starts it, so the extrude rebuilds in a worker that the script waits for;
the file dialog and the options dialog's exec are stubbed (a real modal dialog blocks offscreen), the dialog's own
widgets are driven the way a person would (Fit width, the confirm tick). Files are generated here, in a temp folder.

  1. A three-subpath Bezier logo, width="60mm" with a viewBox of 0 0 200 200, inserted: 3 regions, and the
     bounding box is the stated size (36 x 24 mm: 120 x 80 user units at 0.3 mm each).
  2. The sketch is finished and the outline region extruded 5: the part's volume is that region's area times 5.
  3. A DXF in inches ($INSUNITS 1), a 2 in square with a 1 in round hole, arrives 25.4 times larger (50.8 mm square)
     with 2 regions (the ring and the hole's disc).
  4. A path that ends 0.0005 from its start closes (no open ends); one that ends 0.5 short reports "2 open ends".
  5. One undo removes an insert and the sketch is empty.
  6. A file above the confirm threshold: OK stays disabled until the confirm tick is ticked; nothing is inserted
     while the dialog is cancelled.

Not through the real UI: the sketch is started with start_sketch and the plane set in the feature; the file is
passed to insert_file instead of chosen in the file dialog.

    both fs_py scripts/smoke/sy4_import.py
"""
import io
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

import topdown_common as T  # noqa: E402

TMP = T.isolate("sy4_import_")

import numpy as np  # noqa: E402
from freeshape import kernel as K  # noqa: E402

TOL = 0.05
WINDOWS = []
MODE = "engine"        # every window is MainWindow(engine=True), the app's own mode


def svg(body, attrs):
    return f'<svg xmlns="http://www.w3.org/2000/svg" {attrs}>{body}</svg>'


def loop(cx, cy, rx, ry):
    """A closed path of four cubics round an ellipse-like blob."""
    k = 0.5523
    return (f"M{cx + rx} {cy}C{cx + rx} {cy + k * ry} {cx + k * rx} {cy + ry} {cx} {cy + ry}"
            f"C{cx - k * rx} {cy + ry} {cx - rx} {cy + k * ry} {cx - rx} {cy}"
            f"C{cx - rx} {cy - k * ry} {cx - k * rx} {cy - ry} {cx} {cy - ry}"
            f"C{cx + k * rx} {cy - ry} {cx + rx} {cy - k * ry} {cx + rx} {cy}Z")


LOGO = svg(f'<path d="{loop(100, 100, 60, 40)}{loop(80, 100, 12, 10)}{loop(120, 100, 10, 15)}"/>',
           'width="60mm" height="60mm" viewBox="0 0 200 200"')
MM = 'width="100mm" height="100mm" viewBox="0 0 100 100"'


def dxf_square_with_hole():
    import ezdxf
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 1                      # inches
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (2, 0), (2, 2), (0, 2)], close=True)
    msp.add_circle((1, 1), 0.5)
    f = io.StringIO()
    doc.write(f)
    return f.getvalue()


def zigzag(n):
    pts = " ".join(f"{i},{(i % 2) * 5}" for i in range(n + 1))
    return svg(f'<polyline points="{pts}"/>', 'width="10000mm" height="100mm" viewBox="0 0 10000 100"')


def write(name, text):
    p = os.path.join(TMP, name)
    with open(p, "w") as f:
        f.write(text)
    return p


class Settings(dict):
    def value(self, k, default=None):
        return self.get(k, default)

    def setValue(self, k, v):
        self[k] = v


def sketching():
    """A new window in a new sketch on the Top plane. Returns (window, sketcher)."""
    from PySide6.QtWidgets import QApplication
    from conftest import TOP
    app, w = T.new_engine_window(TMP)
    w.settings = Settings()
    app.processEvents()
    w.start_sketch()
    w.sk_ctx["feature"]["params"]["plane"] = TOP
    w._enter_sketcher(animate=False)
    s = w.sketcher
    s.pick_import_file = lambda: T.check(False, "the file dialog opened")
    WINDOWS.append(w)
    return w, s


def insert(s, path, tweak=None, accept=True):
    """insert_file with the options dialog stubbed: `tweak(dlg)` edits it, then it is accepted (when OK is enabled)."""
    seen = {}

    def run(dlg):
        if tweak:
            tweak(dlg)
        seen["points"], seen["ok"] = dlg.points, dlg.ok.isEnabled()
        return accept and dlg.ok.isEnabled()
    s.exec_import_dialog = run
    s.insert_file(path)
    return seen


def box(sk):
    a = np.array([sk.P(p) for p in sk.points if p != "origin"])
    return a.min(axis=0), a.max(axis=0)


def regions(sk):
    return K.sketch_regions(K.std_plane("Top"), sk.curves())


def main():
    from conftest import add, region_refs

    # 1. the Bezier logo, 60 mm wide through its viewBox
    w, s = sketching()
    insert(s, write("logo.svg", LOGO))
    T.check(s.sk.entities, "logo: nothing was inserted")
    T.check(len(regions(s.sk)) == 3, f"logo: expected 3 regions, got {len(regions(s.sk))}")
    T.say(f"ok [{MODE}] logo makes 3 regions")
    lo, hi = box(s.sk)
    size = hi - lo
    T.check(abs(size[0] - 36) < TOL and abs(size[1] - 24) < TOL,
            f"logo size from width and viewBox: expected 36 x 24 mm, got {size[0]:.3f} x {size[1]:.3f}")
    T.say(f"ok [{MODE}] logo bounding box is {size[0]:.2f} x {size[1]:.2f} mm (viewBox honoured)")

    # 2. finish and extrude the outline 5
    w.finish_sketch()
    T.settle(w)
    sid = w.doc.features[-1]["id"]
    areas = [K.face_info(r)["area"] for r in w.doc.state.sketches[sid].regions]
    big = max(areas)
    refs = region_refs(w.doc, sid, lambda fi: fi["area"] == big)
    T.check(len(refs) == 1, f"extrude: expected one outline region, got {len(refs)}")
    f = add(w.doc, "extrude", regions=refs, depth=5, op="new")
    T.settle(w)
    T.check(not w.doc.errors.get(f["id"]) and w.doc.state.parts, "extrude: the outline did not extrude")
    vol = sum(K.volume(p.shape) for p in w.doc.state.parts.values())
    T.check(abs(vol - big * 5) < 1e-3 * big * 5,
            f"extrude volume: expected region area {big:.3f} x 5 = {big * 5:.3f}, got {vol:.3f}")
    T.say(f"ok [{MODE}] extruded outline volume {vol:.1f} mm3 = region area x 5")

    # 3. a DXF in inches
    w, s = sketching()
    insert(s, write("plate.dxf", dxf_square_with_hole()))
    lo, hi = box(s.sk)
    size = hi - lo
    T.check(abs(size[0] - 50.8) < TOL and abs(size[1] - 50.8) < TOL,
            f"dxf inches: expected 50.8 x 50.8 mm, got {size[0]:.3f} x {size[1]:.3f}")
    T.say(f"ok [{MODE}] dxf in inches arrives 25.4 times larger (50.8 mm square)")
    T.check(len(regions(s.sk)) == 2, f"dxf: expected 2 regions, got {len(regions(s.sk))}")
    T.say(f"ok [{MODE}] dxf square with a round hole makes 2 regions")

    # 4. near-closed and open paths
    w, s = sketching()
    insert(s, write("near.svg", svg('<path d="M0 0 L10 0 L10 10 L0 10 L0 0.0005"/>', MM)))
    T.check(s.sk.entities and not s.import_ends and "0 open ends" in s.import_status,
            f"near-closed path: expected it to close, status {s.import_status!r}")
    T.check(len(regions(s.sk)) == 1, f"near-closed path: expected 1 region, got {len(regions(s.sk))}")
    T.say(f"ok [{MODE}] near-closed path closes (0 open ends, 1 region)")
    w, s = sketching()
    insert(s, write("open.svg", svg('<path d="M0 0 L10 0 L10 10 L0 10 L0 0.5"/>', MM)))
    T.check("2 open ends" in s.import_status and len(s.import_ends) == 2,
            f"larger gap: expected 2 open ends, status {s.import_status!r}")
    T.check(w.status.currentMessage() == s.import_status, "larger gap: the status bar does not show the report")
    T.say(f"ok [{MODE}] larger gap reports 2 open ends")

    # 5. one undo removes the insert
    w, s = sketching()
    insert(s, write("logo2.svg", LOGO))
    T.check(len(s.undo_stack) == 1, f"undo: expected one undo step, got {len(s.undo_stack)}")
    s.undo()
    T.check(not s.sk.entities, "undo: the sketch is not empty after one undo")
    T.say(f"ok [{MODE}] one undo empties the sketch")

    # 6. the size guard
    from freeshape.ui import import_dialog as ID
    w, s = sketching()
    state = {}

    def big_file(d):
        state["points"], state["ok_before"], state["hidden"] = d.points, d.ok.isEnabled(), d.confirm.isHidden()
        d.confirm.setChecked(True)
        state["ok_after"] = d.ok.isEnabled()
    insert(s, write("big.svg", zigzag(ID.IMPORT_CONFIRM_POINTS + 100)), big_file, accept=False)
    T.check(state["points"] > ID.IMPORT_CONFIRM_POINTS and not state["hidden"],
            f"guard: expected more than {ID.IMPORT_CONFIRM_POINTS} points and the confirm tick shown, got {state}")
    T.check(not state["ok_before"], "guard: OK is enabled above the confirm threshold before the tick")
    T.check(state["ok_after"], "guard: OK stays disabled after the confirm tick")
    T.check(not s.sk.entities, "guard: a cancelled big insert added entities")
    T.say(f"ok [{MODE}] guard: {state['points']} points disable OK until confirmed, cancel adds nothing")
    insert(s, os.path.join(TMP, "big.svg"), lambda d: d.confirm.setChecked(True))
    T.check(s.sk.entities, "guard: the confirmed big insert added nothing")
    T.say(f"ok [{MODE}] guard: ticking confirm lets the big file in")

    for w in WINDOWS:
        T.close_engine_window(w)
    T.say("IMPORT SMOKE OK")


if __name__ == "__main__":
    try:
        main()
    except T.Fail as e:
        print(f"IMPORT SMOKE FAILED: {e}", file=sys.stderr, flush=True)
        sys.exit(1)
    except Exception:
        import traceback
        traceback.print_exc()
        print("IMPORT SMOKE FAILED: unexpected error", file=sys.stderr, flush=True)
        sys.exit(1)
