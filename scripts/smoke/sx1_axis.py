"""Construction axis smoke (slice S1): the Axis tool, its eight types, mate connectors as axes, use in other features,
the 3D view, sketch projection and the Document round trip, in the real main window.

Gate script of the Construction Axis plan (helpers in topdown_common.py). A Document with one Part Studio holds a
60 x 40 x 10 plate with a through hole and a circle sketch. In the offscreen window:

  * the Mate connector tool puts a connector on the plate's top face;
  * the Axis tool (start_feature, picks, commit) makes one axis of each of the eight types; each is checked against
    the geometry it comes from (direction, a point on it, the sign where the type fixes one);
  * a revolve about an axis, a circular pattern about an axis and one about the mate connector, each made through
    its own dialog;
  * the plate's width and depth dimensions change: the axes and the features built on them follow;
  * hiding an axis takes it out of viewport.axes and out of picking;
  * the Use tool projects an axis into a sketch as a fixed construction line, or a fixed point where it is square;
  * the Document is saved and reopened: same axes, same part hashes;
  * an example with no axis builds twice to the same part hashes, with no axes.

Real screen-position picks (viewport.pick at the screen point of the geometry, restricted to one kind): the near
bottom edge, the vertices, the top face, the mate connector, and every axis pick (revolve, pattern, hide). Pick
objects fed to the dialog: the far bottom edge (hidden behind the plate in the default view), the plane picks (Top,
Right) and the hole's cylinder face. The sketch click goes through Sketcher.mouse_press, the call the viewport makes.

    both fs_py scripts/smoke/sx1_axis.py
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

TMP = T.isolate("sx1_axis_")

import numpy as np  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from conftest import EXAMPLES_DIR, only_part, region_refs, sketch_feature  # noqa: E402
from freeshape import doc_folder as DF  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.document import PartStudioDoc  # noqa: E402
from freeshape.engine.encode import Encoder  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402
from test_connector_frames import H, plate_studio, set_dim  # noqa: E402
from test_studio_connectors import top_index  # noqa: E402

W, D0 = 60.0, 40.0
HOLE = (45.0, 10.0)
CLICK = np.array([12.0, 7.0, H])
TOP = {"kind": "std", "name": "Top"}
X, Y, Z = np.eye(3)


class Ev:
    """The mouse event the sketcher reads: no modifier keys."""

    def modifiers(self):
        return Qt.NoModifier


def hashes(doc):
    """{part id: BREP hash} of a Part Studio's parts."""
    return {pid: Encoder(meshes=False).brep(p.shape)[1] for pid, p in doc.state.parts.items()}


def on_line(o, d, p, tol=1e-6):
    """True when point `p` lies on the line through `o` along `d`."""
    r = np.asarray(p, float) - np.asarray(o, float)
    return float(np.linalg.norm(r - (r @ d) * d)) < tol


def parallel(d, v):
    return abs(abs(float(np.dot(d, v))) - 1) < 1e-9


def screen(w, p):
    s = w.viewport.cam.to_screen(np.asarray(p, float))
    return float(s[0]), float(s[1])


def real_pick(w, p, kinds, want, what):
    """Pick with the viewport at the screen position of world point `p`, among `kinds` only; it must give `want`."""
    x, y = screen(w, p)
    got = w.viewport.pick(x, y, kinds=set(kinds))
    T.check(got == want, f"{what}: a click at the screen position of {np.round(p, 2).tolist()} gave {got}, not {want}")
    return got


def axis_point(w, fid, t):
    """World point at fraction t along the drawn axis `fid`."""
    for key, o, d, span, _label in w.viewport.axes:
        if key == ("axis", fid):
            a, b = w.viewport.axis_ends(o, d, span)
            return a + t * (b - a)
    raise T.Fail(f"axis {fid} is not in viewport.axes")


def edge_index(shape, mid, d):
    for i, e in enumerate(K.edges(shape)):
        info = K.edge_info(e)
        if info["type"] == int(K.GeomAbs_Line) and parallel(info["d"], d) and np.linalg.norm(np.array(info["c"]) - mid) < 1e-6:
            return i
    raise T.Fail(f"no edge along {d} through {mid}")


def vertex_index(shape, p):
    return next(i for i, q in enumerate(K.vertex_points(shape)) if np.linalg.norm(np.asarray(q) - p) < 1e-6)


def cylinder_index(shape):
    return next(i for i, f in enumerate(K.faces(shape)) if K.face_info(f)["type"] == int(K.GeomAbs_Cylinder))


def rot_bbox(lo, hi, o, d, deg):
    """The bounding box of the box lo..hi turned `deg` degrees about the line through o along d."""
    a = np.radians(deg)
    pts = []
    for x in (lo[0], hi[0]):
        for y in (lo[1], hi[1]):
            for z in (lo[2], hi[2]):
                v = np.array([x, y, z], float) - o
                v = v * np.cos(a) + np.cross(d, v) * np.sin(a) + d * (d @ v) * (1 - np.cos(a))
                pts.append(v + o)
    pts = np.array(pts)
    return np.concatenate([pts.min(axis=0), pts.max(axis=0)])


def part_boxes(doc, skip=()):
    out = []
    for pid, p in doc.state.parts.items():
        if pid not in skip:
            lo, hi = K.bbox(p.shape)
            out.append(np.concatenate([np.asarray(lo, float), np.asarray(hi, float)]))
    return out


def has_box(boxes, want):
    return any(np.allclose(b, want, atol=1e-4) for b in boxes)


def run():
    app, w = T.new_window(TMP)

    # ---- the Document: one Part Studio, a plate with a through hole, a circle sketch to revolve
    fd = DF.FolderDocument.create(os.path.join(TMP, "Doc"), name="Doc")
    tab = fd.add_tab("studio", "Plate")
    doc0, ids = plate_studio(W, D0, hole=HOLE)
    ids["circle"] = sketch_feature(doc0, lambda s: s.add_circle(s.add_point(30, 20), 3), TOP)
    T.check(not doc0.errors, doc0.errors)
    tab.doc.__dict__.update(doc0.__dict__)
    fd._attach(tab)
    fd.save()
    path = fd.path
    fd = w.open_document(path)
    T.check(fd is not None and not fd.errors, fd and fd.errors)
    tab = fd.tab(tab.id)
    doc = tab.doc
    w.tabs.setCurrentIndex(w._tab_index(doc))
    w.resize(900, 700)
    w.show()
    app.processEvents()
    T.check(w.doc is doc, "the Part Studio tab did not open")
    T.check(not doc.errors, doc.errors)
    part = only_part(doc)
    pid = part.id
    D = D0

    # ---- the mate connector on the top face (Mate connector tool, a real click on the face)
    w.start_feature("mate_connector")
    T.check(w.dialog.ftype == "mate_connector", "the Mate connector tool did not open")
    top = Pick("face", (pid, top_index(part.shape)))
    real_pick(w, [20, 30, H], {"face"}, top, "the top face")
    w.dialog.handle_pick(top)
    T.commit_open_dialog(w)
    conn = [f for f in doc.features if f["type"] == "mate_connector"][-1]
    T.check(conn["id"] in doc.state.connectors, "the connector was not built")
    T.say("mate connector on the top face: built")

    # ---- the Axis tool: one axis of each type
    def make_axis(want_type, picks, click=None, flip=False, connector_axis=None):
        w.start_feature("axis")
        T.check(w.mode == "dialog" and w.dialog.ftype == "axis", "the Axis tool did not open")
        if click is not None:
            w.viewport.last_click = screen(w, click)        # where the viewport records the click that picks a face
        for pk in picks:
            w.dialog.handle_pick(pk)
        if flip:
            w.dialog._set("flip", True)
        if connector_axis:
            w.dialog._set("connector_axis", connector_axis)
        got = F.axis_type(w.dialog.feature["params"])
        T.check(got == want_type, f"the selection was read as type {got!r}, not {want_type!r}")
        T.commit_open_dialog(w)
        f = [f for f in doc.features if f["type"] == "axis"][-1]
        T.check(doc.errors.get(f["id"]) is None, f"{want_type}: {doc.errors.get(f['id'])}")
        T.check(f["id"] in doc.state.axes, f"{want_type}: no axis was built")
        return f["id"]

    sh = part.shape
    A = {}
    e_far = edge_index(sh, np.array([W / 2, D, 0]), X)
    pk = Pick("edge", (pid, e_far))
    T.check(w.viewport.pick(*screen(w, [W / 2, D, 0]), kinds={"edge"}) is None,
            "the far bottom edge is visible: feed it as a real pick")       # behind the plate in the default view
    A["edge"] = make_axis("edge", [pk])
    A["round_face"] = make_axis("round_face", [Pick("face", (pid, cylinder_index(sh)))])
    v0, v1 = (Pick("vertex", (pid, vertex_index(sh, p))) for p in ([0, 0, 0], [W, D, 0]))
    real_pick(w, [0, 0, 0], {"vertex"}, v0, "vertex (0, 0, 0)")
    real_pick(w, [W, D, 0], {"vertex"}, v1, f"vertex ({W:g}, {D:g}, 0)")
    A["two_points"] = make_axis("two_points", [v0, v1])
    A["two_planes"] = make_axis("two_planes", [Pick("plane", ("std", "Top")), Pick("plane", ("std", "Right"))])
    vt = Pick("vertex", (pid, vertex_index(sh, [W, D, H])))
    real_pick(w, [W, D, H], {"vertex"}, vt, f"vertex ({W:g}, {D:g}, {H:g})")
    A["point_normal"] = make_axis("point_normal", [vt, top])
    A["face_point"] = make_axis("face_point", [real_pick(w, CLICK, {"face"}, top, "the click on the top face")],
                                click=CLICK)
    vp_ = Pick("vertex", (pid, vertex_index(sh, [W, D, H])))
    near = Pick("edge", (pid, edge_index(sh, np.array([W / 2, 0, 0]), X)))
    real_pick(w, [W / 2, 0, 0], {"edge"}, near, "the near bottom edge")
    A["point_parallel"] = make_axis("point_parallel", [vp_, near])
    ctri = next(t for t in w.viewport.triads if t["id"] == conn["id"])
    cpick = real_pick(w, np.asarray(ctri["o"], float), {"connector"}, Pick("connector", (conn["id"],)), "the connector")
    A["connector"] = make_axis("connector", [cpick], connector_axis="x")
    T.check(len(set(A.values())) == 8, A)

    def verify(D):
        """Each axis against the geometry it comes from, for a plate of depth D. Returns a line per type."""
        ax = {k: doc.state.axes[v] for k, v in A.items()}
        o = lambda k: np.array(ax[k]["o"])   # noqa: E731
        d = lambda k: np.array(ax[k]["d"])   # noqa: E731
        T.check(not doc.errors, doc.errors)
        for k in ax:
            T.check(abs(np.linalg.norm(d(k)) - 1) < 1e-9, f"{k}: direction is not a unit vector")
        T.check(parallel(d("edge"), X) and on_line(o("edge"), d("edge"), [W / 2, D, 0]), f"edge: {ax['edge']}")
        T.check(parallel(d("round_face"), Z) and on_line(o("round_face"), d("round_face"), [*HOLE, 3.0]),
                f"round_face: {ax['round_face']}")
        want = np.array([W, D, 0.0]) / np.hypot(W, D)
        T.check(np.allclose(d("two_points"), want, atol=1e-9) and np.allclose(o("two_points"), 0, atol=1e-9),
                f"two_points: {ax['two_points']}, wanted {want}")
        T.check(parallel(d("two_planes"), Y) and abs(o("two_planes")[0]) < 1e-9 and abs(o("two_planes")[2]) < 1e-9,
                f"two_planes: {ax['two_planes']}")
        T.check(np.allclose(d("point_normal"), Z, atol=1e-9) and on_line(o("point_normal"), Z, [W, D, H]),
                f"point_normal: {ax['point_normal']}")
        T.check(np.allclose(d("face_point"), Z, atol=1e-9) and np.allclose(o("face_point"), CLICK, atol=1e-6),
                f"face_point: {ax['face_point']}")
        T.check(parallel(d("point_parallel"), X) and on_line(o("point_parallel"), d("point_parallel"), [W, D, H]),
                f"point_parallel: {ax['point_parallel']}")
        fr = doc.state.connectors[conn["id"]]["frame"]
        T.check(np.allclose(d("connector"), fr["x"], atol=1e-9) and np.allclose(o("connector"), fr["o"], atol=1e-9),
                f"connector: {ax['connector']}, the frame is {fr}")
        T.check(np.allclose(fr["z"], Z, atol=1e-9), "the connector on the top face does not stand up")
        return fr

    fr = verify(D)
    for k in A:
        T.say(f"axis type {k}: direction and position match the geometry")

    # ---- revolve a sketch about an axis (the far-edge axis, picked on the drawn line past the plate)
    before_rev = set(doc.state.parts)
    n0 = len(before_rev)
    w.start_feature("revolve")
    dlg = w.dialog
    dlg._set("op", "new")
    dlg.fields["profiles"].toggle(("region", region_refs(doc, ids["circle"])[0]))
    fld = dlg.fields["axis_sel"]
    dlg.activate(fld)
    ax_pick = Pick("axis", (A["edge"],))
    real_pick(w, axis_point(w, A["edge"], 0.03), {"axis"}, ax_pick, "the far-edge axis")
    dlg.handle_pick(ax_pick)
    T.check(fld.items == [("axis", {"kind": "axis", "fid": A["edge"]})], fld.items)
    T.commit_open_dialog(w)
    rv = [f for f in doc.features if f["type"] == "revolve"][-1]
    T.check(doc.errors.get(rv["id"]) is None, doc.errors.get(rv["id"]))
    T.check(len(doc.state.parts) == n0 + 1, "the revolve made no part")
    rev_id = next(iter(set(doc.state.parts) - before_rev))
    T.say(f"revolve about an axis: a torus of mean radius {D - 20:g} mm, volume {K.volume(doc.state.parts[rev_id].shape):.0f} mm3")

    # ---- circular patterns about an axis and about the mate connector
    def pattern(axis_pick):
        before = set(doc.state.parts)
        w.start_feature("circular_pattern")
        dlg = w.dialog
        dlg.fields["parts"].toggle(("part", pid))
        fld = dlg.fields["axis_sel"]
        dlg.activate(fld)
        dlg.handle_pick(axis_pick)
        T.check(len(fld.items) == 1 and fld.items[0][1]["kind"] in ("axis", "connector"), fld.items)
        dlg._set("count", 3)
        dlg._set("angle", 180.0)
        dlg._set("op", "new")
        T.commit_open_dialog(w)
        f = [f for f in doc.features if f["type"] == "circular_pattern"][-1]
        T.check(doc.errors.get(f["id"]) is None, doc.errors.get(f["id"]))
        new = set(doc.state.parts) - before
        T.check(len(new) == 2, f"the pattern added {len(new)} parts, not 2")
        return f, new

    def pattern_boxes_ok(new, o, d, what):
        lo, hi = K.bbox(doc.state.parts[pid].shape)
        boxes = part_boxes(doc, skip=set(doc.state.parts) - set(new))
        T.check(has_box(boxes, rot_bbox(lo, hi, o, d, 180)), f"{what}: no copy turned 180 degrees: {boxes}")
        T.check(has_box(boxes, rot_bbox(lo, hi, o, d, 90)) or has_box(boxes, rot_bbox(lo, hi, o, d, -90)),
                f"{what}: no copy turned 90 degrees: {boxes}")

    pf_axis, new_axis = pattern(ax_pick)
    pattern_boxes_ok(new_axis, np.array(doc.state.axes[A["edge"]]["o"]), np.array(doc.state.axes[A["edge"]]["d"]),
                     "pattern about the axis")
    T.say("circular pattern about an axis: copies at 90 and 180 degrees about the axis line")
    cref = real_pick(w, np.asarray(next(t for t in w.viewport.triads if t["id"] == conn["id"])["o"], float),
                     {"connector"}, Pick("connector", (conn["id"],)), "the connector")
    pf_conn, new_conn = pattern(cref)
    T.check(pf_conn["params"]["axis"] == {"kind": "connector", "fid": conn["id"], "axis": "z"}, pf_conn["params"]["axis"])
    pattern_boxes_ok(new_conn, np.array(fr["o"]), np.array(fr["z"]), "pattern about the connector")
    T.say("circular pattern about the mate connector: copies at 90 and 180 degrees about its Z axis")

    # ---- hide an axis: not listed, not picked
    hide = A["face_point"]
    spot = axis_point(w, hide, 0.9)
    real_pick(w, spot, {"axis"}, Pick("axis", (hide,)), "the face_point axis before hiding")
    T.check(any(a[0] == ("axis", hide) for a in w.viewport.axes), "the axis is not listed before hiding")
    w._tree_eye("feature:" + hide)
    w._show_state(doc.state)
    T.check(hide in doc.hidden_features, "the eye did not hide the axis")
    T.check(not any(a[0] == ("axis", hide) for a in w.viewport.axes), "a hidden axis is still in viewport.axes")
    x, y = screen(w, spot)
    T.check(w.viewport.pick(x, y, kinds={"axis"}) != Pick("axis", (hide,)), "a hidden axis can still be picked")
    T.check(w.viewport.pick(x, y) != Pick("axis", (hide,)), "a hidden axis can still be picked with every kind on")
    w._tree_eye("feature:" + hide)
    w._show_state(doc.state)
    T.check(any(a[0] == ("axis", hide) for a in w.viewport.axes), "the axis did not come back")
    T.say("hidden axis: gone from viewport.axes and from picking, and back when shown")

    # ---- project axes into a sketch with the Use tool
    w.start_sketch()
    T.check(w.mode == "sketch", "the sketch did not start")
    w.sk_ctx["feature"]["params"]["plane"] = TOP
    w._enter_sketcher(animate=False)
    s = w.sketcher
    vp = w.viewport
    s.set_tool("use")
    T.check(s.tool == "use", s.tool)
    diag = A["two_points"]
    mid = axis_point(w, diag, 0.5)                      # the plate's diagonal on the Top plane
    real_pick(w, mid, {"axis"}, Pick("axis", (diag,)), "the diagonal axis in the sketch")
    x, y = screen(w, mid)
    s.mouse_move(vp, x, y, Ev())
    s.mouse_press(vp, x, y, Ev())
    lines = [e for e in s.sk.entities.values() if e["type"] == "line"]
    T.check(len(lines) == 1 and lines[0].get("construction"), f"the diagonal gave {len(lines)} lines")
    ends = sorted((np.array(s.sk.P(lines[0][k]), float) for k in ("p1", "p2")), key=lambda p: p[0])
    dd = np.array([W, D]) / np.hypot(W, D)
    T.check(all(on_line([0, 0], dd, e) for e in ends), f"the projected line runs {ends}, off the diagonal")
    T.check(ends[0][0] <= 1e-6 and ends[1][0] >= W - 1e-6, f"the projected line runs {ends}, short of the plate")
    fixed = {c["refs"][0] for c in s.sk.constraints if c["type"] == "fix"}
    T.check({lines[0]["p1"], lines[0]["p2"]} <= fixed, "the projected line is not fixed")
    up = axis_point(w, A["face_point"], 0.9)            # square to the plane: it becomes a point
    x, y = screen(w, up)
    s.mouse_move(vp, x, y, Ev())
    s.mouse_press(vp, x, y, Ev())
    pts = [p for p, uv in s.sk.points.items() if abs(uv[0] - CLICK[0]) < 1e-6 and abs(uv[1] - CLICK[1]) < 1e-6]
    T.check(len(pts) == 1 and pts[0] in {c["refs"][0] for c in s.sk.constraints if c["type"] == "fix"},
            f"the vertical axis gave no fixed point at ({CLICK[0]:g}, {CLICK[1]:g}): {s.sk.points}")
    sk_id = w.sk_ctx["feature"]["id"]
    w.finish_sketch()
    T.check(doc.feature(sk_id) is not None and not doc.errors, doc.errors)
    T.say("Use tool: a diagonal axis became a fixed construction line, a square axis a fixed point")

    # ---- change the plate's depth: axes and the features built on them follow
    old_o = np.array(doc.state.axes[A["edge"]]["o"])
    D = 30.0
    set_dim(doc, ids["sketch"], ids["d"], D)
    T.check(not doc.errors, doc.errors)
    fr2 = verify(D)
    T.check(abs(doc.state.axes[A["edge"]]["o"][1] - D) < 1e-6 and abs(old_o[1] - D0) < 1e-6, "the edge axis did not move")
    T.check(abs(fr2["o"][1] - fr["o"][1]) > 1.0, "the connector did not move")
    rv_part = doc.state.parts[rev_id]
    T.check(abs(K.volume(rv_part.shape) - 2 * np.pi ** 2 * (D - 20) * 9.0) < 1.0,
            f"the revolve did not follow: {K.volume(rv_part.shape):.0f} mm3")
    pattern_boxes_ok(new_axis, np.array(doc.state.axes[A["edge"]]["o"]), np.array(doc.state.axes[A["edge"]]["d"]),
                     "pattern about the axis, after the edit")
    pattern_boxes_ok(new_conn, np.array(fr2["o"]), np.array(fr2["z"]), "pattern about the connector, after the edit")
    T.say(f"plate depth {D0:g} -> {D:g} mm: all eight axes, the revolve and both patterns followed")

    # ---- save the Document, reopen it
    T.check(w._save_document(), "the Document did not save")
    live = {k: doc.state.axes[v] for k, v in A.items()}
    live_h = hashes(doc)
    back = DF.FolderDocument.open_document(path)
    T.check(not back.errors, back.errors)
    bdoc = back.tab(tab.id).doc
    T.check(not bdoc.errors, bdoc.errors)
    T.check(set(bdoc.state.axes) == set(A.values()), f"reopened axes {sorted(bdoc.state.axes)}")
    for k, fid in A.items():
        a, b = live[k], bdoc.state.axes[fid]
        T.check(np.allclose(a["o"], b["o"], atol=1e-9) and np.allclose(a["d"], b["d"], atol=1e-9) and a["span"] == b["span"],
                f"{k}: reopened as {b}, was {a}")
    T.check(hashes(bdoc) == live_h, "the part hashes changed on reopening")
    T.say(f"save and reopen: {len(A)} axes and {len(live_h)} part hashes the same")

    # ---- a Part Studio with no axis builds as before
    ex = os.path.join(EXAMPLES_DIR, "bracket.fshape")
    one, two = PartStudioDoc.load(ex), PartStudioDoc.load(ex)
    for d_ in (one, two):
        d_.rebuild()
        T.check(not d_.errors, d_.errors)
    T.check(one.state.axes == {} and two.state.axes == {}, "an example with no axis has axes")
    T.check(hashes(one) and hashes(one) == hashes(two), "two builds of the example differ")
    T.say(f"example bracket.fshape: two builds, {len(hashes(one))} part hashes equal, no axes")


T.run_smoke("AXIS SMOKE", TMP, run)
