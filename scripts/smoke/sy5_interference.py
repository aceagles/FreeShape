"""Interference detection smoke (slice S5): the Interference detection panel in a Part Studio and in an Assembly,
in the real main window, with the check running in its helper process.

Gate script of the Onshape Parity 2 plan (helpers in topdown_common.py). The window is a MainWindow(engine=True), as
the app starts it. The Part Studio tab rebuilds in a worker: its features are added before the tab is opened and
every later edit waits (with a timeout) for the worker. The Assembly tab is built by the window itself (an engine
window only sends a tab that lives in a Document folder to the worker, and this Assembly does not). The check is
the real one: `interference_ui.ENGINE == "helper"`, so every run goes to `python -m freeshape.interference_worker`,
and every wait on it has a timeout.

  Part Studio, three parts (20x10x5 and 10x10x3 at (5,5) overlap by 150 mm3; the third is clear):
  1. the toolbar button opens the panel; one row, the two parts, 150 mm3; the palette finds it by "clash".
  2. activating the row selects both parts in the Parts list and the view, frames the overlap and writes the
     size line "10 x 5 x 3"; the Document is not dirty.
  3. an edit greys the rows ("Out of date. Check again."), Check again clears it; a hidden part is left out and
     counted in the footer; a one-part studio says "Interference needs two or more parts."; closing restores the view.
  Assembly, a plate with a hole, a fastened M3 screw and a block:
  4. Include standard content ticked: one "Expected (threads) (1)" row, not counted, no interference.
  5. the screw run into the block: one counted overlap row beside the thread row; the camera frames it, the pair
     is selected, ghosts and transparency are set.
  6. Include standard content unticked: no rows, "1 standard part not checked."
  7. one pair patched to fail in the helper: one "could not check" row, the footer counts it.
  8. a Document change greys the rows; Check again clears it; closing the panel removes its ghosts, restores the
     transparency and leaves the Document untouched.

Not through the real UI: the Part Studio's features and the Assembly's instances, standard screw, mate and block
move are made with the test helpers and the Assembly API; the panel is driven through its toolbar button and its
methods (rows, activate, check, wait), not by mouse clicks on its rows. The Context-body case is not covered here
(tests/test_context_engine_mode.py and tests/test_interference_panel_studio.py cover it).

    both fs_py scripts/smoke/sy5_interference.py
"""
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

import topdown_common as T  # noqa: E402

TMP = T.isolate("sy5_interference_") if __name__ == "__main__" else None   # a worker imports this file again
if TMP:
    os.makedirs(os.path.join(TMP, "user_plugins"))
    os.environ["FREESHAPE_PLUGIN_DIR"] = os.path.join(TMP, "user_plugins")

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

MODE = "engine"        # the window is MainWindow(engine=True), the app's own mode
LIMIT = 180.0          # seconds for any wait on the helper process
CALLS = []             # every helper command the panel launched


def say(text):
    T.say(f"ok [{MODE}] {text}")


def block(doc, x, y, w, h, depth, win=None):
    """A new part: a rectangle sketch extruded. On a tab the engine builds (`win` given) the sketch is settled first,
    so its regions are in the window's state."""
    from conftest import add, rect_lines, region_refs, sketch_feature
    sid = sketch_feature(doc, lambda s: rect_lines(s, x, y, w, h))
    if win is not None:
        T.settle(win, 120)
    add(doc, "extrude", regions=region_refs(doc, sid), depth=depth, op="new")


def studio(*blocks):
    from freeshape.document import PartStudioDoc
    doc = PartStudioDoc()
    for b in blocks:
        block(doc, *b)
    doc.dirty = False
    return doc


def wait(p, what):
    T.check(p.wait(LIMIT), f"{what}: the check did not finish in {LIMIT:.0f} s")
    QApplication.processEvents()


def spy_helper():
    """Record each helper command the panel starts (so the run provably went to the helper process)."""
    from freeshape.ui import interference_ui as IU
    real = IU._worker_command

    def spy(req):
        CALLS.append(req)
        return real(req)
    IU._worker_command = spy
    return real


def failing_helper(real):
    """A helper command whose process raises a kernel error on the first two booleans (the first pair, both
    attempts), then works."""
    from freeshape.ui import interference_ui as IU
    script = os.path.join(TMP, "failing_worker.py")
    with open(script, "w") as f:
        f.write(f"import sys\nsys.path.insert(0, {ROOT!r})\nfrom freeshape import kernel as K\n"
                "_real, _n = K.common, []\n"
                "def _common(*a, **k):\n"
                "    _n.append(1)\n"
                "    if len(_n) <= 2:\n"
                "        raise K.KernelError('smoke: boolean blew up')\n"
                "    return _real(*a, **k)\n"
                "K.common = _common\n"
                "from freeshape import interference_worker as W\nW.run(sys.argv[1])\n")
    IU._worker_command = lambda req: (CALLS.append(req), [sys.executable, script, req])[1]


def restore_helper(real):
    from freeshape.ui import interference_ui as IU
    IU._worker_command = real


def part_studio(w):
    from conftest import add
    from freeshape.ui.palette import search_commands
    # the 150 mm3 fixture, and a third part that touches neither
    doc = studio((0, 0, 20, 10, 5), (5, 5, 10, 10, 3), (100, 100, 5, 5, 5))
    w._add_tab(doc)
    T.settle(w, 120)
    T.check(not w.studio_engine.broken, f"the engine failed: {w.studio_engine.broken}")
    T.check(w.doc is doc and not w.is_asm and len(doc.state.parts) == 3, "the Part Studio did not build 3 parts")
    doc.dirty = False
    a, b, c = list(doc.state.parts)

    # 1. open from the toolbar
    T.check("Interference detection" in [x.name for x in w._palette_commands()], "the palette lacks the command")
    T.check([x.name for x in search_commands(w._palette_commands(), "clash")][:1] == ["Interference detection"],
            "searching 'clash' does not find Interference detection first")
    w.btn_pinterf.click()
    QApplication.processEvents()
    p = w.studio_interf
    T.check(p is not None and w.btn_pinterf.isChecked(), "the toolbar button did not open the panel")
    wait(p, "studio")
    rows = p.rows()
    T.check(len(rows) == 1, f"studio: expected one row, got {len(rows)}")
    r = rows[0]
    T.check({r["a"], r["b"]} == {a, b} and r["kind"] == "overlap", f"studio: wrong pair {r}")
    T.check(abs(r["volume"] - 150) < 0.15, f"studio: expected 150 mm3, got {r['volume']}")
    T.check(not p.include_std.isVisibleTo(p), "studio: Include standard content is shown in a Part Studio")
    T.check(CALLS, "studio: the check did not go to the helper process")
    from freeshape.ui import interference_ui as IU
    T.check(IU.ENGINE == "helper", f"the engine is {IU.ENGINE!r}, not the helper process")
    say(f"Part Studio: panel opens, 1 row ({r['a_name']} and {r['b_name']}), {r['volume']:.1f} mm3, "
        "palette finds it by 'clash', helper process used")

    # 2. activate
    p.animate = False
    p.activate(0)
    T.check(set(w.left.parts.selected_part_ids()) == {a, b}, "studio: the Parts list does not select the pair")
    T.check({pk.key[0] for pk in w.viewport.selection if pk.kind == "part"} == {a, b},
            "studio: the view does not select the pair")
    t = w.viewport.cam.target
    T.check(np.all(t >= r["lo"] - 1e-6) and np.all(t <= r["hi"] + 1e-6), f"studio: camera target {t} off the overlap")
    T.check("10 x 5 x 3" in p.detail.text(), f"studio: size line {p.detail.text()!r}")
    T.check(not doc.dirty, "studio: activating a row dirtied the Document")
    say(f"Part Studio: row selects both parts in list and view, frames the overlap, size line '{p.detail.text()}', "
        "Document not dirty")

    # 3. edit, hidden part, close
    block(doc, 200, 200, 5, 5, 5, win=w)
    T.settle(w, 120)
    T.check(not w.studio_engine.broken, f"the engine failed: {w.studio_engine.broken}")
    QApplication.processEvents()
    T.check(p.stale and len(p.rows()) == 1 and "Out of date. Check again." in p.status.text(),
            f"studio: edit did not grey the rows: {p.status.text()!r}")
    p.check()
    wait(p, "studio recheck")
    T.check(not p.stale and len(p.rows()) == 1, "studio: Check again did not clear the out-of-date state")
    say("Part Studio: an edit greys the rows ('Out of date. Check again.'), Check again clears it")
    doc.hidden_parts.add(c)
    p.check()
    wait(p, "studio hidden")
    T.check(len(p.rows()) == 1 and "1 part not checked: hidden or reference." in p.status.text(),
            f"studio: hidden part footer {p.status.text()!r}")
    doc.hidden_parts.discard(c)
    say("Part Studio: a hidden part is left out and counted ('1 part not checked: hidden or reference.')")
    w.close_studio_interference()
    QApplication.processEvents()
    T.check(w.studio_interf is None and not w.btn_pinterf.isChecked(), "studio: close left the panel or button on")
    T.check(w.view_only_ids is None and not w.tool_transparent, "studio: close left the view changed")
    say("Part Studio: closing the panel restores the button and the view")

    # a studio with one part
    one = studio((0, 0, 20, 10, 5))
    w._add_tab(one)
    T.settle(w, 120)
    w.btn_pinterf.click()
    QApplication.processEvents()
    p = w.studio_interf
    wait(p, "one part")
    T.check(p.rows() == [] and "Interference needs two or more parts." in p.status.text(),
            f"one part: footer {p.status.text()!r}")
    w.close_studio_interference()
    say("Part Studio: one part says 'Interference needs two or more parts.'")


def assembly(w, real_cmd):
    from test_assembly import box, src
    from test_assembly_standard_bom import screwed

    def Tx(x):
        M = np.eye(4)
        M[0, 3] = x
        return M

    # the block starts 500 mm away; the screw's tip is at (5..15, 3..13, -4..-1) in the plate's frame
    a, plate, screw, mate = screwed(1.25, lib={"block": box(10, 10, 3, at=(5, 3, -4))})
    blk = a.insert(src("block"), T=Tx(500))
    a.dirty = False
    w._add_tab(a)
    QApplication.processEvents()
    T.check(w.doc is a and w.is_asm and len(a.instances) == 3, "the Assembly did not open with 3 instances")
    before_ghosts, before_tr = w.viewport.ghosts, set(w.tool_transparent)

    def open_panel():
        w.btn_interf.click()
        QApplication.processEvents()
        p = w.asm.interf_panel
        T.check(p is not None and w.btn_interf.isChecked(), "the Assembly toolbar button did not open the panel")
        wait(p, "assembly")
        return p

    def pair(r):
        return frozenset((r["a"], r["b"]))

    # 4. the expected thread row
    p = open_panel()
    rows = p.rows()
    T.check([(r["kind"], pair(r)) for r in rows] == [("thread", frozenset((plate, screw)))],
            f"assembly: expected one thread row, got {[(r['kind'], r['a_name'], r['b_name']) for r in rows]}")
    T.check(p.include_std.isChecked() and not p.include_std.isHidden(), "assembly: Include standard content is not on")
    T.check(p._group is not None and "Expected (threads) (1)" in p._group.text(0), "assembly: no Expected (threads) group")
    T.check("No interference." in p.status.text() and "1 expected thread overlap not counted" in p.status.text(),
            f"assembly: footer {p.status.text()!r}")
    say("Assembly: Expected (threads) (1) row for the fastened screw, not counted ('No interference.')")

    # 5. run the screw into the block
    a.move_instance(blk, Tx(-500), solve=False)
    QApplication.processEvents()
    T.check(p.stale, "assembly: moving the block did not grey the rows")
    p.check()
    wait(p, "assembly recheck")
    rows = p.rows()
    T.check([(r["kind"], pair(r)) for r in rows] == [("overlap", frozenset((screw, blk))),
                                                     ("thread", frozenset((plate, screw)))],
            f"assembly: expected a counted overlap then the thread row, got {[(r['kind'], r['a_name']) for r in rows]}")
    T.check("1 interference." in p.status.text() and "1 expected thread overlap" in p.status.text(),
            f"assembly: footer {p.status.text()!r}")
    p.animate = False
    p.activate(0)
    r = rows[0]
    T.check(set(w.asm.selected_instance_ids()) == {screw, blk}, "assembly: activate did not select the pair")
    t = w.viewport.cam.target
    T.check(np.all(t >= r["lo"] - 1e-6) and np.all(t <= r["hi"] + 1e-6), f"assembly: camera target {t} off the overlap")
    T.check(w.viewport.ghosts is p._given and len(w.viewport.ghosts) >= 1, "assembly: no ghosts for the row")
    T.check(w.tool_transparent == before_tr | {screw, blk}, f"assembly: transparency {w.tool_transparent}")
    say(f"Assembly: screw run into the block gives a counted overlap row ({r['volume']:.1f} mm3) beside the thread row; "
        "pair selected, camera on the overlap, ghosts and transparency set")

    # 6. untick Include standard content
    p.include_std.setChecked(False)
    wait(p, "assembly unticked")
    T.check(p.rows() == [] and p.status.text().endswith("1 standard part not checked."),
            f"assembly: unticked footer {p.status.text()!r}")
    say("Assembly: Include standard content unticked leaves the screw out ('1 standard part not checked.')")

    # 7. a pair that fails in the helper
    w.asm.close_interference()
    failing_helper(real_cmd)
    try:
        p = open_panel()
        rows = p.rows()
        kinds = sorted(r["kind"] for r in rows)
        T.check(kinds == ["failed", "overlap"] or kinds == ["failed", "thread"],
                f"assembly: expected one failed row and one more, got {kinds}")
        bad = next(r for r in rows if r["kind"] == "failed")
        T.check("blew up" in bad["note"] and "1 pair could not be checked" in p.status.text(),
                f"assembly: failed row {bad['note']!r}, footer {p.status.text()!r}")
        p.activate(rows.index(bad))
        T.check("Could not check" in p.detail.text(), f"assembly: detail {p.detail.text()!r}")
    finally:
        restore_helper(real_cmd)
    say("Assembly: a pair that fails in the helper is a 'could not check' row and the footer counts it")

    # 8. change the Document, then close
    w.asm.close_interference()
    p = open_panel()
    T.check(not p.stale, "assembly: a new panel opens out of date")
    a.dirty = False
    a.set_fixed(plate, True)
    QApplication.processEvents()
    T.check(p.stale and p.rows() and "Out of date. Check again." in p.status.text(),
            f"assembly: a Document change did not grey the rows: {p.status.text()!r}")
    p.check()
    wait(p, "assembly after change")
    T.check(not p.stale, "assembly: Check again did not clear the out-of-date state")
    p.animate = False
    p.activate(0)
    undo = len(a._undo)
    w.asm.close_interference()
    QApplication.processEvents()
    T.check(w.asm.interf_panel is None and not w.btn_interf.isChecked(), "assembly: close left the panel or button on")
    T.check(w.viewport.ghosts is before_ghosts and w.viewport.feature_hl is None, "assembly: ghosts not removed")
    T.check(w.tool_transparent == before_tr, f"assembly: transparency not restored: {w.tool_transparent}")
    T.check(len(a._undo) == undo and w.view_only_ids is None, "assembly: closing changed the Document or the view")
    say("Assembly: a change greys the rows, Check again clears it; closing removes the ghosts and restores transparency")


def main():
    app, w = T.new_engine_window(TMP)
    try:
        real_cmd = spy_helper()
        part_studio(w)
        assembly(w, real_cmd)
    finally:
        T.close_engine_window(w)
    T.say("INTERFERENCE SMOKE OK")


if __name__ == "__main__":
    try:
        main()
    except T.Fail as e:
        print(f"INTERFERENCE SMOKE FAILED: {e}", file=sys.stderr, flush=True)
        sys.exit(1)
    except Exception:
        import traceback
        traceback.print_exc()
        print("INTERFERENCE SMOKE FAILED: unexpected error", file=sys.stderr, flush=True)
        sys.exit(1)
