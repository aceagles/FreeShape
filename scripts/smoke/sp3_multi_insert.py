"""SP3 smoke: one click on the Insert panel seats a screw in every hole of a selected face that fits a size.

It builds in a temporary folder a 100 x 75 x 10 plate Part Studio with eight 3.4 mm, four 4.5 mm and one 1.0 mm
through holes and an Assembly holding it, opens the Assembly in an offscreen MainWindow, selects the plate's top
face in the viewport and opens the Standard parts tab of the Insert panel. Through the panel's own widgets it
selects ISO 4762 in sp_tree, reads the rows of sp_sizes and the text of sp_skipped, sets the M4 length in the
row's drop-down and clicks sp_insert. It then checks the Assembly (12 Standard part instances, 12 fastened mates,
no mate errors, each under_head origin on a distinct hole centre, eight M3 and four M4 at the chosen length),
runs one undo and checks that no Standard part instance is left and the plate has not moved.

Prints "SP3 OK" and exits 0, or "SP3 FAILED: <first failure>" on stderr and exits 1.

    both fs_py scripts/smoke/sp3_multi_insert.py
"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")

import ui_helpers  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox, QPushButton, QTableWidget, QTreeWidget  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402

SCREW = "sp:fasteners/iso4762"
ROLE_TYPE = Qt.UserRole + 1              # the tree item data that holds the type id (standard_parts_ui.ROLE_TYPE)
TOL = 1e-6


class Failure(Exception):
    pass


def check(cond, msg):
    if not cond:
        raise Failure(msg)


def pump(app, n=3):
    for _ in range(n):
        app.processEvents()


def top_face_index(doc, iid):
    for i, f in enumerate(K.faces(doc.parts[iid].shape)):
        info = K.face_info(f)
        if "n" in info and info["n"][2] > 0.99 and abs(info["c"][2] - ui_helpers.PLATE_T) < TOL:
            return i
    raise Failure("the plate has no planar +Z face at its top")


def std_instances(doc):
    return [i for i in doc.instances if i["source"].get("kind") == "standard"]


def find(pane, cls, name):
    w = pane.findChild(cls, name)
    check(w is not None, f"no {cls.__name__} named {name} in the Standard parts pane")
    return w


def tree_item(tree, type_id):
    for i in range(tree.topLevelItemCount()):
        top = tree.topLevelItem(i)
        for k in range(top.childCount()):
            if top.child(k).data(0, ROLE_TYPE) == type_id:
                return top.child(k)
    raise Failure(f"sp_tree has no item for {type_id}")


def table_rows(table):
    """[(size, hole count, length combo)] read from the cells of sp_sizes."""
    out = []
    for r in range(table.rowCount()):
        cb = table.cellWidget(r, 2)
        check(isinstance(cb, QComboBox), f"sp_sizes row {r} has no length drop-down")
        out.append((table.item(r, 0).text(), int(table.item(r, 1).text()), cb))
    return out


def run(app, w, tmp_note):
    doc = w.doc
    check(len(doc.instances) == 1 and not std_instances(doc), "the Assembly does not start with only the plate")
    plate = doc.instances[0]["id"]
    plate_T = [list(r) for r in doc.instances[0]["T"]]
    n_mates = len(doc.mates)
    print("Assembly opened with the 13-hole plate (8 x 3.4 mm, 4 x 4.5 mm, 1 x 1.0 mm)")

    w.viewport.set_selection([Pick("face", (plate, top_face_index(doc, plate)))])
    pump(app)
    if w.asm.insert_panel is None:
        w.asm.toggle_insert()
        pump(app)
    panel = w.asm.insert_panel
    check(panel is not None, "the Insert panel did not open")
    panel.tabs.setCurrentIndex(1)
    pump(app)
    pane = panel.standard

    tree = find(pane, QTreeWidget, "sp_tree")
    table = find(pane, QTableWidget, "sp_sizes")
    insert_btn = find(pane, QPushButton, "sp_insert")
    tree.setCurrentItem(tree_item(tree, SCREW))
    pump(app)

    rows = table_rows(table)
    check([(s, n) for s, n, _cb in rows] == [("M3", 8), ("M4", 4)],
          f"sp_sizes shows {[(s, n) for s, n, _cb in rows]}, expected [('M3', 8), ('M4', 4)]")
    skipped = pane.findChild(type(pane.skipped), "sp_skipped")
    check(skipped is not None and not skipped.isHidden(), "sp_skipped is not shown")
    check(skipped.text() == "Skipped 1 hole (no size fits): 1.0 mm", f"sp_skipped reads {skipped.text()!r}")
    print(f"rows {[(s, n) for s, n, _cb in rows]}; {skipped.text()}")

    m4 = dict((s, cb) for s, _n, cb in rows)["M4"]
    offered = [m4.itemData(i) for i in range(m4.count())]
    check(len(offered) >= 2, f"the M4 length drop-down offers {offered}")
    chosen = offered[-1]
    m4.setCurrentIndex(m4.count() - 1)
    pump(app)
    m3_len = dict((s, cb) for s, _n, cb in table_rows(table))["M3"].currentData()
    check(dict((s, cb.currentData()) for s, _n, cb in table_rows(table))["M4"] == chosen,
          "the M4 length did not stay at the chosen value")
    print(f"M4 length set to {chosen}; M3 left at its default {m3_len}")

    check(insert_btn.isEnabled(), "sp_insert is disabled")
    insert_btn.click()
    pump(app)

    status = find(pane, type(pane.status), "sp_status").text()
    check(status == "Inserted 12 Socket head cap screw ISO 4762 in 12 holes", f"sp_status reads {status!r}")
    screws = std_instances(doc)
    check(len(screws) == 12 and len(doc.instances) == 13, f"{len(screws)} Standard part instances, expected 12")
    fastened = [m for m in doc.mates[n_mates:] if m.get("type") == "fastened"]
    check(len(fastened) == 12 and len(doc.mates) == n_mates + 12,
          f"{len(fastened)} new fastened mates ({len(doc.mates) - n_mates} new mates), expected 12")
    check(doc.errors == {}, f"mate errors after insert: {doc.errors}")
    check(w.viewport.selection == [], "the viewport selection was not cleared")

    holes = {"M3": [(x, y) for x, y, d in ui_helpers.plate_holes() if d == ui_helpers.M3_HOLE],
             "M4": [(x, y) for x, y, d in ui_helpers.plate_holes() if d == ui_helpers.M4_HOLE]}
    used = set()
    by_size = {"M3": [], "M4": []}
    for s in screws:
        p = s["source"]["params"]
        by_size[p["size"]].append(p)
        head = doc.world_frame(doc.named_connector(s["id"], "under_head"))
        near = [(k, xy) for k, xys in holes.items() for xy in xys
                if np.allclose(head["o"], [xy[0], xy[1], ui_helpers.PLATE_T], atol=TOL)]
        check(len(near) == 1, f"{s['name']} under_head at {np.round(head['o'], 6).tolist()} is on no hole centre")
        kind, xy = near[0]
        check(kind == p["size"], f"{p['size']} screw sits in a {kind} hole at {xy}")
        check(xy not in used, f"two screws share the hole at {xy}")
        used.add(xy)
    check(len(used) == 12, f"screws occupy {len(used)} distinct holes, expected 12")
    check(len(by_size["M3"]) == 8 and len(by_size["M4"]) == 4,
          f"{len(by_size['M3'])} M3 and {len(by_size['M4'])} M4 screws, expected 8 and 4")
    check({p["length"] for p in by_size["M4"]} == {chosen}, f"M4 lengths {[p['length'] for p in by_size['M4']]}")
    check({p["length"] for p in by_size["M3"]} == {m3_len}, f"M3 lengths {[p['length'] for p in by_size['M3']]}")
    print(f"{status}; 12 fastened mates, no errors; 8 M3 x {m3_len} and 4 M4 x {chosen} on 12 distinct hole centres")

    w.doc.undo()
    pump(app)
    check(not std_instances(doc) and len(doc.instances) == 1,
          f"after one undo {len(std_instances(doc))} Standard part instances remain ({len(doc.instances)} in all)")
    check(len(doc.mates) == n_mates, f"after one undo {len(doc.mates)} mates remain, expected {n_mates}")
    check(doc.errors == {}, f"mate errors after undo: {doc.errors}")
    check(doc.instances[0]["id"] == plate and np.allclose(doc.instances[0]["T"], plate_T, atol=TOL),
          "the plate moved or changed after the undo")
    print("one undo: 0 Standard part instances, plate unchanged")


def main():
    t0 = time.time()
    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="sp3_multi_insert_"))
    w = None
    try:
        w = ui_helpers.plate_assembly_window(tmp)
        run(app, w, tmp)
        print(f"run time {time.time() - t0:.1f} s")
        print("SP3 OK")
    except (Failure, AssertionError) as e:
        print(f"SP3 FAILED: {e}", file=sys.stderr)
        return 1
    finally:
        if w is not None:
            ui_helpers.close_window(w)
        pump(app)
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
