"""Surfaces to solids smoke (slice S3): Thicken, Enclose and Fill, in the real main window.

Gate script of the Onshape Parity 2 plan (helpers in topdown_common.py). The window is a MainWindow(engine=True), as the
app starts it: Part Studio tabs rebuild in a worker, and every commit waits (with a timeout) for the worker before the
built state is read. Each feature is made through its dialog (start_feature, the dialog's handle_pick and _set,
commit_dialog), never by writing params:

  * the fender route: an arc swept along an arc - line - arc path as a surface (Sweep, Create = Surface), then Thicken
    by 2; the solid's volume is the surface's area times 2 within 3 percent, and the surface part is used up;
  * Enclose on six square surfaces gives a cube; on five the feature fails with "The selection does not close a volume".
    The surface-pairing route is read from scripts/spikes/op2_surfacing.result.txt: with enclose=makervolume the squares
    overhang each other, with enclose=sew they meet edge to edge. The script prints which it used;
  * Fill across the open top of a box with Add: the part is a solid of the original volume. Fill with Tangency on one
    edge builds without an error;
  * each result is saved, reopened and rebuilt: the same parts with the same volume, faces and element names;
  * the live command search (the palette the window opens) finds Thicken, Enclose and Fill by "surface to solid",
    "stitch" and "patch";
  * a Shell and a Thicken in one Part Studio (both name their faces with op code THK): every face has a name, names are
    unique within a part and do not change after a dimension edit made in the dialog;
  * Enclose with a solid part as one wall: how many new parts appear and their volumes.

Not through the real UI: the squares for Enclose, the box with its top face removed, and the block and the Shell
of the naming check are built with the test helpers; a pick is a Pick object handed to the dialog (no screen click).

    both fs_py scripts/smoke/sy3_solidify.py
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

TMP = T.isolate("sy3_solidify_") if __name__ == "__main__" else None   # a worker imports this file again

from PySide6.QtWidgets import QApplication, QCheckBox  # noqa: E402
from conftest import (FRONT, TOP, add, face_refs, normal_is, only_part, rect_lines, region_refs,  # noqa: E402
                      sketch_feature)
from freeshape import kernel as K  # noqa: E402
from freeshape.document import PartStudioDoc  # noqa: E402
from freeshape.ui.panels import ROLE_KIND  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402
from test_enclose import A, cube_doc, square  # noqa: E402
from test_fill import BOX, box_with_open_top  # noqa: E402
from test_thin_sweep import LENGTH, arc_length, ents, shallow_arc, tangent_path  # noqa: E402

THICKEN_FAILS = "The thickness is larger than the surface's tightest curve allows"
ENCLOSE_FAILS = "The selection does not close a volume"
ENCLOSE_PLANES_ONLY = "Select the surfaces that surround a volume"
SPIKE_RESULT = os.path.join(ROOT, "scripts", "spikes", "op2_surfacing.result.txt")


def spike_enclose_route():
    """`makervolume` or `sew`: the enclose route the surfacing spike chose (its DECISION line)."""
    with open(SPIKE_RESULT) as fh:
        for line in fh:
            if line.startswith("DECISION"):
                return next(w.split("=")[1] for w in line.split() if w.startswith("enclose="))
    raise T.Fail(f"no DECISION line in {SPIKE_RESULT}")


def surfaces(doc):
    return [p for p in doc.state.parts.values() if K.is_surface(p.shape)]


def solids_of(doc):
    return [p for p in doc.state.parts.values() if not K.is_surface(p.shape)]


def area(shape):
    return sum(K.face_info(f)["area"] for f in K.faces(shape))


def one_part(doc, what):
    """The only part of `doc`, or a failure that names the check."""
    parts = list(doc.state.parts.values())
    T.check(len(parts) == 1, f"{what}: {len(parts)} parts {[p.name for p in parts]}, not 1")
    return parts[0]


def commit(w):
    """Commit the open dialog and wait (bounded) until the engine worker has answered and the window shows its result."""
    T.commit_open_dialog(w)
    T.settle(w, 180)
    T.check(not w.studio_engine.broken, f"the engine failed: {w.studio_engine.broken}")


def open_tab(app, w, doc):
    w._add_tab(doc)
    w.resize(900, 700)
    w.show()
    app.processEvents()
    T.settle(w, 180)
    T.check(w.doc is doc, "the Part Studio tab did not open")


def last_feature(doc, ftype):
    return [f for f in doc.features if f["type"] == ftype][-1]


def feature_ok(doc, ftype):
    f = last_feature(doc, ftype)
    T.check(doc.errors.get(f["id"]) is None, f"{ftype}: {doc.errors.get(f['id'])}")
    return f


def part_rows(w):
    """The part rows of the Parts list (the "Parts (n)" and "Surfaces (n)" header rows skipped), as names."""
    lst = w.left.parts
    return [lst.item(i).text() for i in range(lst.count()) if lst.item(i).data(ROLE_KIND) != "header"]


def signature(doc, pid):
    """What a part is, to compare a build in the window's worker with a rebuild of the saved file: volume, area, face
    count, bounding box and the sorted element names."""
    p = doc.state.parts[pid]
    lo, hi = K.bbox(p.shape)
    n = doc.state.named(pid)
    names = None if n is None else {k: sorted(n.names_of(k)) for k in ("F", "E", "V")}
    return (round(K.volume(p.shape), 4), round(area(p.shape), 3), len(K.faces(p.shape)),
            tuple(round(float(x), 4) for x in (*lo, *hi)), names)


def reopen_same(doc, label, tmp):
    """Save `doc`, load the file and rebuild it: the same parts with the same volume, area, faces, bounding box and element names."""
    path = os.path.join(tmp, label + ".fshape")
    doc.save(path)
    again = PartStudioDoc.load(path)
    again.rebuild()
    T.check(not again.errors, f"{label}: reopened with errors {again.errors}")
    T.check(set(again.state.parts) == set(doc.state.parts),
            f"{label}: reopened parts {sorted(again.state.parts)}, saved {sorted(doc.state.parts)}")
    for pid, p in doc.state.parts.items():
        T.check(signature(again, pid) == signature(doc, pid),
                f"{label}: part {pid} differs after reopening: {signature(again, pid)} vs saved {signature(doc, pid)}")
    return again


def palette_search(app, w, text):
    """Type `text` into the window's command search (the palette it opens) and return the names it lists."""
    w.open_command_search()
    pal = w._palette
    T.check(pal.isVisible(), "the command search did not open")
    pal.edit.setText(text)
    app.processEvents()
    names = [c.name for c in pal._shown]
    shown = [pal.list.item(i).text() for i in range(pal.list.count())]
    T.check(shown == names, f"the palette list {shown} differs from its result {names}")
    pal.close_palette()
    return names


def element_names(doc):
    """{part id: {letter: sorted names}}; fails when a face has no name or two faces of a part share one."""
    out = {}
    for pid in doc.state.parts:
        n = doc.state.named(pid)
        T.check(n is not None, f"part {pid} has no element names")
        T.check(not [k for k in n.unnamed() if k[0] == "F"], f"part {pid} has unnamed faces: {n.unnamed()}")
        faces = n.names_of("F")
        T.check(len(faces) == len(K.faces(doc.state.parts[pid].shape)), f"part {pid}: {len(faces)} face names")
        T.check(len(set(faces)) == len(faces), f"part {pid}: two faces share a name: {sorted(faces)}")
        out[pid] = {k: sorted(n.names_of(k)) for k in ("F", "E", "V")}
    return out


def run():
    app, w = T.new_engine_window(TMP)
    try:
        body(app, w)
    finally:
        T.close_engine_window(w)


def body(app, w):
    route = spike_enclose_route()
    T.check(route in ("makervolume", "sew"), f"unknown enclose route {route!r}")
    over = 0.1 if route == "makervolume" else 0.0
    T.say(f"[info] the surfacing spike chose enclose={route}: Enclose uses "
          + ("OVERHANGING squares (each 1.2 times as wide as the cube)" if over else "edge-to-edge squares"))

    # ---- the fender: an arc swept along an arc - line - arc path as a surface, then Thicken by 2 (I3.2)
    doc = PartStudioDoc()
    prof = sketch_feature(doc, shallow_arc, TOP)
    path = sketch_feature(doc, tangent_path, FRONT)
    T.check(not doc.errors, doc.errors)
    open_tab(app, w, doc)
    w.start_feature("sweep")
    T.check(w.dialog.ftype == "sweep", "the Sweep tool did not open")
    dlg = w.dialog
    dlg._set("creation", "surface")
    app.processEvents()
    dlg._set("op", "new")
    dlg.activate(dlg.fields["curves"])
    dlg.handle_pick(Pick("sketch_line", (prof, ents(doc, prof, "arc")[0])))
    dlg.activate(dlg.fields["path"])
    dlg.handle_pick(Pick("sketch_line", (path, ents(doc, path, "line")[0])))
    commit(w)
    feature_ok(doc, "sweep")
    sp = one_part(doc, "fender: the surface sweep")
    T.check(K.is_surface(sp.shape), "the surface sweep made no surface part")
    T.check(w.left.parts_header.text() == "Parts (0) · Surfaces (1)", f"the Parts header reads {w.left.parts_header.text()!r}")
    T.check(part_rows(w) == [sp.name] and sp.name.startswith("Surface"), f"the Parts list rows are {part_rows(w)}")
    fender_area = area(sp.shape)
    T.check(abs(fender_area - arc_length() * LENGTH) < 0.05 * arc_length() * LENGTH,
            f"fender surface area {fender_area:.1f}, profile times path length {arc_length() * LENGTH:.1f}")
    w.start_feature("thicken")
    T.check(w.dialog.ftype == "thicken", "the Thicken tool did not open")
    dlg = w.dialog
    dlg.handle_pick(Pick("part", (sp.id,)))
    T.check(dlg.feature["params"]["parts"] == [sp.id] and dlg.feature["params"]["op"] == "new",
            f"picking the surface part stored {dlg.feature['params']}")
    dlg._set("thickness1", 2.0)
    commit(w)
    feature_ok(doc, "thicken")
    fender = one_part(doc, "fender: after Thicken")
    T.check(not K.is_surface(fender.shape) and len(K.solids(fender.shape)) == 1 and K.is_valid(fender.shape),
            "the thickened fender is not one valid solid")
    T.check(abs(K.volume(fender.shape) - fender_area * 2) <= 0.03 * fender_area * 2,
            f"fender volume {K.volume(fender.shape):.1f}, area times 2 is {fender_area * 2:.1f}")
    T.check(not surfaces(doc) and sp.id not in doc.state.parts, "the surface part was not used up by Thicken")
    T.check(w.left.parts_header.text() == "Parts (1)" and len(part_rows(w)) == 1,
            f"after Thicken the Parts header reads {w.left.parts_header.text()!r}, rows {part_rows(w)}")
    T.say(f"[engine] fender: arc swept along an arc-line-arc path as a surface of {fender_area:.0f} mm2, Thicken 2 gives "
          f"one valid solid of {K.volume(fender.shape):.0f} mm3 (area x 2 = {fender_area * 2:.0f}), surface used up")
    reopen_same(doc, "fender", TMP)
    T.say("[engine] fender: saved, reopened and rebuilt to the same part, volume, faces and element names")

    # ---- Thicken that is too thick for the curve fails with its named message (I3.1, I3.2)
    d2 = PartStudioDoc()
    sk = sketch_feature(d2, lambda s: s.add_arc(s.add_point(0, 0), s.add_point(5, 0), s.add_point(-5, 0)), TOP)
    open_tab(app, w, d2)
    w.start_feature("extrude")
    dlg = w.dialog
    dlg._set("creation", "surface")
    app.processEvents()
    dlg._set("op", "new")
    dlg.activate(dlg.fields["curves"])
    dlg.handle_pick(Pick("sketch_line", (sk, ents(d2, sk, "arc")[0])))
    dlg._set("depth", 20.0)
    commit(w)
    feature_ok(d2, "extrude")
    tube = one_part(d2, "thicken too thick: the surface extrude")
    T.check(K.is_surface(tube.shape), "the surface extrude of an arc made no surface part")
    w.start_feature("thicken")
    dlg = w.dialog
    dlg.handle_pick(Pick("part", (tube.id,)))
    dlg._set("thickness1", 9.0)                                       # the half cylinder has radius 5
    commit(w)
    f = last_feature(d2, "thicken")
    T.check(d2.errors.get(f["id"]) == THICKEN_FAILS, f"Thicken of 9 on a radius 5 curve reported {d2.errors.get(f['id'])!r}")
    T.check(len(surfaces(d2)) == 1 and not solids_of(d2), "a failed Thicken changed the parts")
    T.say(f"[engine] thicken 9 on a curve of radius 5: the feature fails with \"{THICKEN_FAILS}\"; the surface stays")

    # ---- six squares enclose to a cube (I3.4)
    cube = cube_doc(over)
    open_tab(app, w, cube)
    sq = surfaces(cube)
    T.check(len(sq) == 6, f"{len(sq)} squares")
    T.check(len(part_rows(w)) == 6, f"the Parts list rows are {part_rows(w)}")
    w.start_feature("enclose")
    T.check(w.dialog.ftype == "enclose", "the Enclose tool did not open")
    dlg = w.dialog
    for p in sq:
        dlg.handle_pick(Pick("part", (p.id,)))
    T.check(len(dlg.feature["params"]["entities"]) == 6, f"entities {dlg.feature['params']['entities']}")
    commit(w)
    feature_ok(cube, "enclose")
    solid = one_part(cube, "enclose six squares")
    T.check(not surfaces(cube) and not K.is_surface(solid.shape), "the squares were not replaced by a solid")
    T.check(K.is_valid(solid.shape) and len(K.faces(solid.shape)) == 6, "the enclosed cube is not a valid 6 face solid")
    T.check(abs(K.volume(solid.shape) - A ** 3) < 1e-6 * A ** 3, f"cube volume {K.volume(solid.shape)}")
    T.check(w.left.parts_header.text() == "Parts (1)", f"the Parts header reads {w.left.parts_header.text()!r}")
    T.say(f"[engine] enclose ({route}, squares {'overhanging' if over else 'edge to edge'}): six surfaces make one valid "
          f"6 face solid of {K.volume(solid.shape):.0f} mm3, the surfaces are gone")
    reopen_same(cube, "cube", TMP)
    T.say("[engine] cube: saved, reopened and rebuilt to the same part, volume, faces and element names")

    # ---- five squares: refused with the named message
    five = cube_doc(over)
    open_tab(app, w, five)
    sq = surfaces(five)
    w.start_feature("enclose")
    dlg = w.dialog
    for p in sq[:5]:
        dlg.handle_pick(Pick("part", (p.id,)))
    commit(w)
    f = last_feature(five, "enclose")
    T.check(five.errors.get(f["id"]) == ENCLOSE_FAILS, f"Enclose of five squares reported {five.errors.get(f['id'])!r}")
    T.check(len(surfaces(five)) == 6 and not solids_of(five), "a refused Enclose changed the parts")
    T.say(f"[engine] enclose of five squares: refused with \"{ENCLOSE_FAILS}\"; the six surfaces stay")

    # ---- planes alone: refused with the other named message
    pl = PartStudioDoc()
    open_tab(app, w, pl)
    w.start_feature("enclose")
    dlg = w.dialog
    dlg.handle_pick(Pick("plane", ("std", "Right")))
    commit(w)
    f = last_feature(pl, "enclose")
    T.check(pl.errors.get(f["id"]) == ENCLOSE_PLANES_ONLY, f"Enclose of a plane alone reported {pl.errors.get(f['id'])!r}")
    T.say(f"[engine] enclose of a plane alone: \"{ENCLOSE_PLANES_ONLY}\"")

    # ---- the open top of a box is filled with Add and the part becomes a solid (I3.6)
    fdoc, _part, _rim = box_with_open_top()
    open_tab(app, w, fdoc)
    part = one_part(fdoc, "the box with its top removed")
    T.check(K.is_surface(part.shape) and len(K.faces(part.shape)) == 5, "the box with its top face removed is not a 5 face surface")
    w.start_feature("fill")
    T.check(w.dialog.ftype == "fill", "the Fill tool did not open")
    dlg = w.dialog
    for pk in rim_picks(w, part):
        dlg.handle_pick(pk)
    T.check(len(dlg.feature["params"]["edges"]) == 4, f"fill edges {dlg.feature['params']['edges']}")
    dlg._set("op", "add")
    commit(w)
    feature_ok(fdoc, "fill")
    closed = one_part(fdoc, "fill (Add) of the box")
    vol = BOX[0] * BOX[1] * BOX[2]
    T.check(not K.is_surface(closed.shape) and K.is_valid(closed.shape) and len(K.faces(closed.shape)) == 6,
            "the filled box is not a valid 6 face solid")
    T.check(abs(K.volume(closed.shape) - vol) < 1e-3, f"filled box volume {K.volume(closed.shape)}, wanted {vol}")
    T.say(f"[engine] fill (Add) across the open top of a box: one part, a valid solid of {K.volume(closed.shape):.0f} mm3 "
          f"(the original {vol:.0f})")
    reopen_same(fdoc, "filled_box", TMP)
    T.say("[engine] filled box: saved, reopened and rebuilt to the same part, volume, faces and element names")

    # ---- Fill with Tangency on one edge builds without an error (I3.6)
    tdoc, _part, _rim = box_with_open_top()
    open_tab(app, w, tdoc)
    part = one_part(tdoc, "the box for the Tangency fill")
    w.start_feature("fill")
    dlg = w.dialog
    for pk in rim_picks(w, part):
        dlg.handle_pick(pk)
    ticks = fill_ticks(dlg)
    T.check(len(ticks) == 4, f"{len(ticks)} Tangency boxes in the Fill dialog, not 4")
    ticks[0].setChecked(True)
    got = [e["continuity"] for e in dlg.feature["params"]["edges"]]
    T.check(got == ["tangency", "position", "position", "position"], f"continuity of the edges {got}")
    commit(w)
    feature_ok(tdoc, "fill")
    new = [q for q in surfaces(tdoc) if q.id != part.id and len(K.faces(q.shape)) == 1]
    T.check(len(new) == 1, f"the Fill made {len(new)} one-face surfaces, not 1")
    kind = K.face_info(K.faces(new[0].shape)[0])["type"]
    T.say(f"[engine] fill with Tangency on one edge: builds without an error, one face of area {area(new[0].shape):.0f} mm2 "
          f"({'a plane' if kind == int(K.GeomAbs_Plane) else 'a curved plate'})")

    # ---- the live command search (extra check 4)
    for text, name in (("surface to solid", "Thicken"), ("stitch", "Enclose"), ("patch", "Fill")):
        names = palette_search(app, w, text)
        T.check(name in names[:3], f"the command search for {text!r} lists {names[:5]}, not {name} near the top")
        T.say(f"[engine] command search {text!r}: {name} is result {names.index(name) + 1} of {len(names)} "
              f"(first: {names[0]})")

    # ---- a Shell and a Thicken in one Part Studio (extra check 1)
    shared_names(app, w)

    # ---- Enclose with a solid part as one wall (extra check 2)
    solid_wall(app, w, over)


def rim_picks(w, part):
    """The viewport picks of the four edges around the open top of the box (the window's own part shape)."""
    shape = w.doc.state.parts[part.id].shape
    w.viewport.part_shapes = {pid: p.shape for pid, p in w.doc.state.parts.items()}
    out = []
    for i, e in enumerate(K.edges(shape)):
        a = K.edge_info(e)
        if a["type"] == int(K.GeomAbs_Line) and abs(a["p0"][2] - BOX[2]) < 1e-6 and abs(a["p1"][2] - BOX[2]) < 1e-6:
            out.append(Pick("edge", (part.id, i)))
    T.check(len(out) == 4, f"{len(out)} rim edges, not 4")
    return out


def fill_ticks(dlg):
    """The Tangency check boxes of the rows now in the Fill dialog."""
    lay = dlg._fill_box.layout()
    return [b for i in range(lay.count()) for b in lay.itemAt(i).widget().findChildren(QCheckBox)]


def shared_names(app, w):
    """A block with a Shell (op code THK) and a flat surface thickened by Thicken (also THK) in one studio."""
    doc = PartStudioDoc()
    sb = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 40, 20), TOP)
    ext = add(doc, "extrude", regions=region_refs(doc, sb), depth=10, op="new")
    add(doc, "shell", faces=face_refs(only_part(doc), normal_is([0, 0, 1]), expect=1), thickness=2.0)
    line = sketch_feature(doc, lambda s: s.add_line(s.add_point(100, 0), s.add_point(120, 0)), TOP)
    sext = add(doc, "extrude", creation="surface", curves=[{"kind": "sketch_chain", "sketch": line,
                                                          "entity": ents(doc, line, "line")[0]}], depth=10, op="new")
    T.check(not doc.errors, doc.errors)
    open_tab(app, w, doc)
    T.check(len(surfaces(doc)) == 1, f"shell + thicken studio: {len(surfaces(doc))} surfaces, not 1")
    flat = surfaces(doc)[0]
    w.start_feature("thicken")
    dlg = w.dialog
    dlg.handle_pick(Pick("part", (flat.id,)))
    dlg._set("thickness1", 3.0)
    commit(w)
    feature_ok(doc, "thicken")
    feature_ok(doc, "shell")
    T.check(len(doc.state.parts) == 2 and not surfaces(doc), f"parts {list(doc.state.parts)}")
    before = element_names(doc)
    vols = sorted(round(K.volume(p.shape), 3) for p in doc.state.parts.values())
    T.check(vols == sorted([round(8000 - 36 * 16 * 8, 3), 600.0]), f"the shelled block and the thickened plate: {vols}")
    thk = {pid: sum("THK" in n for n in before[pid]["F"]) for pid in before}
    T.check(all(v > 0 for v in thk.values()), f"a part has no face named with op code THK: {thk}")
    both = [n for ns in before.values() for n in ns["F"]]
    across = len(both) - len(set(both))
    # an edit that does not touch the Thicken: the block gets 2 mm taller
    w.start_feature("extrude", edit_fid=ext["id"])
    w.dialog._set("depth", 12.0)
    commit(w)
    feature_ok(doc, "extrude")
    mid = element_names(doc)
    T.check(set(mid) == set(before), f"part ids {sorted(mid)} after the block edit, {sorted(before)} before")
    # an edit that does not touch the Shell: the surface gets 2 mm taller
    w.start_feature("extrude", edit_fid=sext["id"])
    w.dialog._set("depth", 12.0)
    commit(w)
    feature_ok(doc, "extrude")
    after = element_names(doc)
    T.check(after == before, "face, edge or vertex names changed after the dimension edits: "
            + "; ".join(f"{pid} {k}: {len(before[pid][k])} -> {len(after[pid][k])}" for pid in before for k in before[pid]
                        if before[pid][k] != after[pid][k]))
    T.check(mid == before, "names changed after the block edit")
    T.check(sorted(round(K.volume(p.shape), 3) for p in doc.state.parts.values())
            != vols, "the dimension edits did not change a volume")
    T.say(f"[engine] Shell + Thicken in one studio (both op code THK): {len(before)} parts, every face named, "
          f"{sum(len(v['F']) for v in before.values())} face names unique within each part, "
          f"{across} names shared between the two parts, face names with THK per part {sorted(thk.values())}; "
          "names unchanged after editing the block depth and the surface depth")


def solid_wall(app, w, over):
    """Enclose with a block as the floor and five square surfaces for the other walls."""
    doc = PartStudioDoc()
    lo, hi = -over * A, A + over * A
    for y in (0, A):
        square(doc, TOP, (lo, y), (hi, y), lo, hi)
    for x in (0, A):
        square(doc, TOP, (x, lo), (x, hi), lo, hi)
    square(doc, FRONT, (lo, A), (hi, A), lo, hi)                    # the roof; the floor is the block
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, A, A), TOP)
    add(doc, "extrude", regions=region_refs(doc, sid), depth=3.0, flip=True, op="new")
    T.check(not doc.errors, doc.errors)
    open_tab(app, w, doc)
    block = solids_of(doc)[0]
    T.check(abs(K.bbox(block.shape)[1][2]) < 1e-6, "the block is not under z = 0")
    before = set(doc.state.parts)
    T.check(len(before) == 6 and len(surfaces(doc)) == 5, f"{len(before)} parts, {len(surfaces(doc))} surfaces")
    w.start_feature("enclose")
    dlg = w.dialog
    for pid in doc.state.parts:
        dlg.handle_pick(Pick("part", (pid,)))
    commit(w)
    feature_ok(doc, "enclose")
    T.check(block.id in doc.state.parts and not surfaces(doc), "the block was deleted or a surface was left")
    new = [p for pid, p in doc.state.parts.items() if pid not in before]
    vols = sorted(round(K.volume(p.shape), 3) for p in new)
    T.check(any(abs(v - A ** 3) < 1e-3 for v in vols), f"no new part is the enclosed cube: new volumes {vols}")
    T.check(all(not K.is_surface(p.shape) and K.is_valid(p.shape) for p in new), "a new part is not a valid solid")
    block_vol = round(K.volume(block.shape), 3)
    T.say(f"[engine] Enclose with a solid as one wall (a 10 x 10 x 3 block as the floor + five squares): {len(new)} new "
          f"part(s) with volumes {vols} mm3; the block itself ({block_vol}) stays. "
          + ("A second new solid copies the block." if len(new) == 2 and block_vol in vols else
             "No copy of the block appears." if len(new) == 1 else "The new parts are listed above."))


if __name__ == "__main__":                      # the engine workers import this file: only the main process runs it
    T.run_smoke("SOLIDIFY SMOKE", TMP, run)
