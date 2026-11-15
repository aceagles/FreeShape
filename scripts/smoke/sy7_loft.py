"""Loft conditions, guides, Boundary surface and sweep control smoke (slice S7), in the real main window.

Gate script of the Onshape Parity 2 plan (helpers in topdown_common.py). The window is a MainWindow(engine=True), as
the app starts it: Part Studio tabs rebuild in a worker, and every commit waits (with a timeout) for the worker before
the built state is read. Each feature is made through its dialog (start_feature, the dialog's handle_pick, toggle and
_set, commit_dialog), never by writing params. Nothing modal is opened. What it checks:

  * Normal to profile at both ends of the two offset 20 x 20 squares: the loft leaves each profile square to z
    (0.1 degree with skin=hermite, skin_angle_deg plus 0.5 with skin=ghost; the script prints which), against the
    26.57 degrees of the plain loft;
  * the loft dialog's preview changes with the end condition (the shape shown differs from the plain loft's);
  * Match tangent on a cone frustum's top face (Adjacent faces empty, then the wall picked): under 0.5 degree to the
    cone wall, against 32.5 degrees with no condition;
  * guides: on OpenCascade 8.0 two bowed guides give one valid solid of 6 faces through both guides; on 7.9 the
    refusal text. Guides and end conditions grey each other out. The script reads
    scripts/spikes/op2_loft.result.txt and the OCC version and prints which branch it ran;
  * Loft as a surface, and Boundary surface from two U curves alone equals it (area, faces, element names); then
    on 8.0 two V curves are added and all four curves lie on the surface (on 7.9 the refusal text); its messages;
    the feature list row reads "Boundary surface 1" and the dialog reopens with the U picks in pick order;
  * a closed loft of four circles on a ring (Closed tick): one valid solid, no planar cap, volume near 3553;
  * a loft from a square to a sketch point: 5 faces, volume 4000;
  * Sweep Keep profile orientation ends parallel to its start; Lock profile direction is shown to CHANGE a sweep
    along a path that pitches and turns (the default sweep's long edge leans off square to z, Lock keeps it square);
  * the golden loft_square_to_circle case equals its golden volume;
  * every result is saved, reopened and rebuilt: the same parts with the same volume, area, faces, bounding box and
    element names;
  * the live command search finds Loft by "guide curves", "surface loft" and "ruled surface", Sweep by "profile
    orientation", and ranks Boundary surface first for "boundary surface" (also "gordon" and "network surface").

Not through the real UI: the sketches and the cone frustum are built with the test helpers; a pick is a Pick object
or a field toggle handed to the dialog (no screen click).

    both fs_py scripts/smoke/sy7_loft.py
"""
import json
import math
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

import topdown_common as T  # noqa: E402

TMP = T.isolate("sy7_loft_") if __name__ == "__main__" else None   # a worker imports this file again

import numpy as np  # noqa: E402
from conftest import FRONT, OCCT_MAJOR, OCP_VERSION, RIGHT, TOP, add, rect_lines, region_refs, sketch_feature  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.document import PartStudioDoc  # noqa: E402
from freeshape.engine.encode import Encoder  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402
from test_boundary_surface import dist_to_surface, network  # noqa: E402
from test_loft_closed_vertex import _planar_caps, _sketch_pt  # noqa: E402
from test_loft_end_conditions import _end_normals, _section, _side_faces, _volume  # noqa: E402
from test_loft_guides import REFUSED_79, _guide_distance, _guides, _width_at  # noqa: E402
from test_loft_guides_ui import make_doc as guided_doc  # noqa: E402
from test_loft_match_tangent import _base_doc, _loft_part, _refs, _start_angles, _wall_of  # noqa: E402
from test_loft_surface import arc_sketch, face_names  # noqa: E402
from test_naming_core_features import add_sketch  # noqa: E402
from test_naming_sweep_loft_import import add_sketch_on  # noqa: E402
from test_sweep_control import angle_deg, cap_normals, ents, quarter, rect, section_edges  # noqa: E402

SPIKE_RESULT = os.path.join(ROOT, "scripts", "spikes", "op2_loft.result.txt")
GOLDEN_FEATURES = os.path.join(ROOT, "tests", "golden", "features.json")
MSG_BOUNDARY_79 = "Boundary surface with V curves needs OpenCascade 8.0 or newer"
Z = np.array([0.0, 0.0, 1.0])
NORMAL = "normal"
R = 40.0                                    # radius of the sweep path's arc
HASH_DIFFS = []                             # parts whose BREP hash differs between the worker build and a headless rebuild


def say(msg):
    T.say(f"ok [engine] {msg}")


# ------------------------------------------------------------------------------------------------ the spike's branch
def spike_decision():
    """(the DECISION key=value pairs for the running OCP build, whether the file says I7.3 was not built)."""
    with open(SPIKE_RESULT) as fh:
        text = fh.read()
    build, found = None, {}
    for ln in text.splitlines():
        if ln.startswith("#####"):
            build = ln.split()[-1]
        elif ln.startswith("DECISION") and build is not None:
            found[build] = dict(t.split("=", 1) for t in ln.split() if "=" in t)
    T.check(found, f"no DECISION line in {SPIKE_RESULT}")
    key = next((b for b in found if OCP_VERSION in b), None) or next(b for b in found if f"ocp{OCCT_MAJOR}." in b)
    return found[key], "I7.3 not built" in text


# ------------------------------------------------------------------------------------------------ window helpers
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


def start(w, ftype):
    w.start_feature(ftype)
    T.check(w.dialog is not None and w.mode == "dialog", f"the {ftype} tool did not open a dialog")
    return w.dialog


def last_feature(doc, ftype):
    return [f for f in doc.features if f["type"] == ftype][-1]


def feature_ok(doc, ftype):
    f = last_feature(doc, ftype)
    T.check(doc.errors.get(f["id"]) is None, f"{ftype}: {doc.errors.get(f['id'])}")
    return f


def feature_error(doc, ftype):
    return doc.errors.get(last_feature(doc, ftype)["id"])


def one_part(doc, what):
    parts = list(doc.state.parts.values())
    T.check(len(parts) == 1, f"{what}: {len(parts)} parts {[p.name for p in parts]}, not 1")
    return parts[0]


def area(shape):
    return sum(K.face_info(f)["area"] for f in K.faces(shape))


def region(doc, sid):
    return ("region", region_refs(doc, sid)[0])


def new_loft(w, profiles, **fields):
    """The Loft dialog with `profiles` (field items) toggled in and `fields` set, not yet committed."""
    d = start(w, "loft")
    for it in profiles:
        d.fields["profiles"].toggle(it)
    d._set("op", "new")
    for k, v in fields.items():
        d._set(k, v)
    return d


def pick_curves(d, field, picks):
    d.activate(d.fields[field])
    for sk, ent in picks:
        d.handle_pick(Pick("sketch_line", (sk, ent)))


def refs_of(items):
    return [(r["sketch"], r["entity"]) for r in items]


def feature_rows(w):
    return [w.left.features.item(i).text() for i in range(w.left.features.count())]


def signature(doc, pid):
    """What a part is, to compare a build in the window's worker with a rebuild of the saved file: volume, area, face
    count, bounding box and the sorted element names."""
    p = doc.state.parts[pid]
    lo, hi = K.bbox(p.shape)
    n = doc.state.named(pid)
    names = None if n is None else {k: sorted(n.names_of(k)) for k in ("F", "E", "V")}
    return (round(0.0 if K.is_surface(p.shape) else _volume(p.shape), 3), round(area(p.shape), 2),
            len(K.faces(p.shape)), tuple(round(float(x), 3) for x in (*lo, *hi)), names)


def reopen_same(doc, label, tmp):
    """Save `doc`, load the file and rebuild it: the same parts with the same volume, area, faces, bounding box and
    element names. A BREP hash that differs while all of that matches is printed, not failed."""
    path = os.path.join(tmp, label + ".fshape")
    doc.save(path)
    again = PartStudioDoc.load(path)
    again.rebuild()
    T.check(not again.errors, f"{label}: reopened with errors {again.errors}")
    T.check(set(again.state.parts) == set(doc.state.parts),
            f"{label}: reopened parts {sorted(again.state.parts)}, saved {sorted(doc.state.parts)}")
    for pid, p in doc.state.parts.items():
        a, b = signature(again, pid), signature(doc, pid)
        T.check(a == b, f"{label}: part {pid} differs after reopening: {a} vs saved {b}")
        h1 = Encoder(meshes=False).brep(again.state.parts[pid].shape)[1]
        h2 = Encoder(meshes=False).brep(p.shape)[1]
        if h1 != h2:
            HASH_DIFFS.append(f"{label}/{pid} {h2[:10]} vs {h1[:10]}")
    say(f"{label}: saved, reopened and rebuilt to the same parts, volume, area, faces, bounding box and element names")


def palette_search(app, w, text):
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


# ------------------------------------------------------------------------------------------------ the offset squares
def squares_doc():
    """Two 20 x 20 squares, the second 15 along x and 30 up (the 26.57 degree case)."""
    doc = PartStudioDoc()
    a, _ = add_sketch(doc, -10, -10, 20, 20)
    pl = add(doc, "plane", type="offset", refs=[], ref=TOP, offset=30.0)
    b, _ = add_sketch_on(doc, {"kind": "plane", "fid": pl["id"]}, 5, -10, 20, 20)
    return doc, a, b


def leaning_deg(body):
    """The largest angle (degrees) by which a side face's normal at the profile edges is off square to z."""
    ns = [n for f in _side_faces(body) for n in _end_normals(f)]
    T.check(ns, "no side-face normals at the profile edges")
    return max(math.degrees(math.asin(min(1.0, abs(float(n @ Z))))) for n in ns)


def normal_to_profile(app, w, skin, skin_angle, tmp):
    limit = 0.1 if skin == "hermite" else skin_angle + 0.5
    T.say(f"[info] Normal to profile check: skin={skin} skin_angle_deg={skin_angle:.4f}, so the limit is {limit} degree")
    doc, a, b = squares_doc()
    open_tab(app, w, doc)
    new_loft(w, [region(doc, a), region(doc, b)])
    commit(w)
    feature_ok(doc, "loft")
    plain = one_part(doc, "plain loft").shape
    lean = leaning_deg(plain)
    T.check(abs(lean - math.degrees(math.atan(15 / 30))) < 0.01,
            f"plain loft: expected the x faces to lean 26.57 degrees from square, got {lean:.3f}")
    say(f"plain loft of the two offset squares leaves each profile {lean:.2f} degrees off square")
    w.edit_feature(last_feature(doc, "loft")["id"])
    d = w.dialog
    T.check(d is not None and d.ftype == "loft", "the loft did not reopen for edit")
    for end in ("start", "end"):
        d._set(f"{end}_cond", NORMAL)
    commit(w)
    feature_ok(doc, "loft")
    params = last_feature(doc, "loft")["params"]
    T.check(params.get("start", {}).get("cond") == NORMAL and params.get("end", {}).get("cond") == NORMAL,
            f"Normal to profile: expected start and end saved as normal, got {params.get('start')} {params.get('end')}")
    body = one_part(doc, "Normal to profile loft").shape
    T.check(len(K.solids(body)) == 1 and K.is_valid(body) and len(K.faces(body)) == 6,
            f"Normal to profile: expected one valid solid of 6 faces, got {len(K.solids(body))} solids, "
            f"valid={K.is_valid(body)}, {len(K.faces(body))} faces")
    got = leaning_deg(body)
    T.check(got < limit, f"Normal to profile leaves each profile square: expected under {limit} degree off square "
                         f"(plain loft {lean:.2f}), got {got:.4f}")
    say(f"Normal to profile at both ends: the loft leaves each profile {got:.5f} degrees off square "
        f"(limit {limit}; the plain loft {lean:.2f})")
    reopen_same(doc, "normal_loft", tmp)
    return doc, a, b


def preview_differs(app, w, doc, a, b):
    """The loft dialog's preview in the engine window: plain, then Normal to profile at the start."""
    doc.delete_features([last_feature(doc, "loft")["id"]])
    T.settle(w, 180)
    T.check(not doc.state.parts, f"expected no part after deleting the loft, got {len(doc.state.parts)}")
    d = new_loft(w, [region(doc, a), region(doc, b)])

    def shown_body():
        d._timer.stop()
        d._do_preview()
        app.processEvents()
        T.check(not d.err.isVisibleTo(d), f"the preview shows an error: {d.err.text()}")
        parts = list(w._shown[1].parts.values())
        T.check(len(parts) == 1, f"the preview shows {len(parts)} parts, not 1")
        return parts[0].shape

    plain = shown_body()
    d._set("start_cond", NORMAL)
    cond = shown_body()
    xs = [K.face_info(_section(s, 7.5))["c"][0] for s in (plain, cond)]
    vols = [_volume(s) for s in (plain, cond)]
    T.check(abs(xs[0] - xs[1]) > 0.1, f"preview: with Normal to profile at the start expected the section at z=7.5 to "
                                      f"move off the plain preview's, got centres {xs} (volumes {vols})")
    say(f"loft dialog preview: plain section centre x={xs[0]:.3f} at z=7.5, volume {vols[0]:.2f}; with Normal to "
        f"profile at the start x={xs[1]:.3f}, volume {vols[1]:.2f}")
    w.cancel_dialog()
    T.settle(w, 180)


# ------------------------------------------------------------------------------------------------ match tangent
def match_tangent(app, w, tmp):
    doc, pid, cid = _base_doc()
    open_tab(app, w, doc)
    refs = _refs(doc, pid)
    new_loft(w, [("face", refs["top"]), ("region", region_refs(doc, cid)[0])])
    commit(w)
    feature_ok(doc, "loft")
    plain = _start_angles(_loft_part(doc.state, pid).shape, _wall_of(doc.state, pid))
    T.check(all(abs(x - 32.5) < 1.0 for x in plain),
            f"cone frustum without a condition: expected about 32.5 degrees to the wall, got max {max(plain):.2f}")
    mean = sum(plain) / len(plain)
    say(f"cone frustum loft with no condition: {mean:.2f} degrees to the cone wall")
    fid = last_feature(doc, "loft")["id"]
    for picks in ([], ["wall"]):
        w.edit_feature(fid)
        d = w.dialog
        T.check(d is not None and d.ftype == "loft", "the loft did not reopen for edit")
        d._set("start_cond", "match_tangent")
        T.check(d.fields["start_faces"].isVisibleTo(d), "Adjacent faces is not shown for Match tangent")
        for it in list(d.fields["start_faces"].items):
            d.fields["start_faces"].toggle(it)
        for k in picks:
            d.fields["start_faces"].toggle(("face", refs[k]))
        commit(w)
        feature_ok(doc, "loft")
        part = _loft_part(doc.state, pid)
        T.check(len(K.solids(part.shape)) == 1 and K.is_valid(part.shape), "Match tangent: expected one valid solid")
        angles = _start_angles(part.shape, _wall_of(doc.state, pid))
        what = "picked: the wall" if picks else "empty"
        T.check(max(angles) < 0.5, f"Match tangent (Adjacent faces {what}): expected under 0.5 degree to the cone "
                                   f"wall, got {max(angles):.3f}")
        say(f"Match tangent with Adjacent faces {what}: the loft continues the cone wall within {max(angles):.4f} "
            f"degrees (no condition: {mean:.2f})")
    reopen_same(doc, "match_tangent", tmp)


# ------------------------------------------------------------------------------------------------ guides
def guides(app, w, dec, i73_missing, tmp):
    built = OCCT_MAJOR >= 8 and dec.get("gordon80") == "ok" and not i73_missing
    T.say(f"[info] guides branch: OCP {OCP_VERSION}; spike DECISION gordon80={dec.get('gordon80')} "
          f"one_guide={dec.get('one_guide')}; I7.3 {'not built' if i73_missing else 'built'}; this run checks "
          + ("guides: built" if built else "guides: refused with the OpenCascade 8.0 message"))
    doc, profiles, gid, (g1, g2) = guided_doc()
    open_tab(app, w, doc)
    d = new_loft(w, list(profiles), guides_on=True)
    pick_curves(d, "guides", [(gid, g1), (gid, g2)])
    n = len(d.feature["params"].get("guides", []))
    T.check(n == 2, f"guides: expected 2 guide rows in the dialog, got {n}")
    for key in ("start_cond", "end_cond"):
        row = next(wd for spec, wd in d.rows if spec["key"] == key)
        T.check(not row.isEnabled() and "cannot be combined" in row.toolTip(),
                f"guides and end conditions: expected the {key} row greyed out with 'cannot be combined', got "
                f"enabled={row.isEnabled()} tip={row.toolTip()!r}")
    say("guides and end conditions grey each other out in the dialog (cannot be combined)")
    commit(w)
    err = feature_error(doc, "loft")
    if not built:
        T.check(err == REFUSED_79, f"guides on OCP {OCP_VERSION}: expected {REFUSED_79!r}, got {err!r}")
        say(f"guides: refused on OpenCascade {OCCT_MAJOR}: \"{err}\"")
        return
    T.check(err is None, f"guides: the guided loft failed: {err}")
    body = one_part(doc, "guided loft").shape
    T.check(len(K.solids(body)) == 1 and K.is_valid(body) and len(K.faces(body)) == 6,
            f"guides: expected one valid solid of 6 faces, got {len(K.solids(body))} solids, valid={K.is_valid(body)}, "
            f"{len(K.faces(body))} faces")
    dist = max(_guide_distance(body, g) for g in _guides())
    T.check(dist < 1e-4, f"guides: expected the loft within 1e-4 mm of both guides, got {dist:.2e}")
    width = _width_at(body, 20.0)
    T.check(abs(width - 28.0) < 1e-3, f"guides: expected the mid section 28 wide, got {width:.4f}")
    say(f"guides: built; two bowed guides give one valid solid of 6 faces, guide distance {dist:.1e} mm, "
        f"mid section {width:.3f} wide")
    reopen_same(doc, "guided_loft", tmp)


# ------------------------------------------------------------------------------------------------ boundary surface
def boundary_network(app, w, doc, u, v):
    open_tab(app, w, doc)
    d = start(w, "boundary_surface")
    d._set("op", "new")
    pick_curves(d, "curves", refs_of(u))
    pick_curves(d, "guides", refs_of(v))
    commit(w)
    return feature_error(doc, "loft")


def surface_and_boundary(app, w, network_built, tmp):
    docs, parts = [], []
    for door in ("loft", "boundary_surface"):
        doc = PartStudioDoc()
        a, ea = arc_sketch(doc, 0)
        b, eb = arc_sketch(doc, 20, ((-10, 0), (0, 8), (10, 0)))
        open_tab(app, w, doc)
        d = start(w, door)
        d._set("op", "new")
        if door == "loft":
            d._set("creation", "surface")
            app.processEvents()
        pick_curves(d, "curves", [(a, ea), (b, eb)])
        commit(w)
        feature_ok(doc, "loft")
        p = one_part(doc, f"{door} of two arcs")
        T.check(K.is_surface(p.shape) and not K.solids(p.shape) and len(K.faces(p.shape)) == 1,
                f"{door}: expected one face as a surface body, got {len(K.faces(p.shape))} faces, "
                f"surface={K.is_surface(p.shape)}")
        docs.append(doc)
        parts.append(p)
    s, bs = parts
    a1, a2 = area(s.shape), area(bs.shape)
    T.check(abs(a1 - a2) <= 1e-9 * a1, f"Boundary surface from U curves alone: expected the surface loft's area "
                                       f"{a1:.6f}, got {a2:.6f}")
    n1, n2 = face_names(docs[0], s.id), face_names(docs[1], bs.id)
    T.check(n1 == n2 and n1, f"Boundary surface from U curves alone: expected face names {n1}, got {n2}")
    T.check(F.front_kind(last_feature(docs[1], "loft")) == "boundary_surface",
            "the Boundary surface feature is not marked as one")
    say(f"Loft as a surface and Boundary surface from the same two U curves agree: area {a1:.4f}, 1 face, same face names")
    reopen_same(docs[0], "surface_loft", tmp)
    reopen_same(docs[1], "boundary_u_only", tmp)

    # U picks in order, the feature list row, and reopening the dialog
    doc = PartStudioDoc()
    a, ea = arc_sketch(doc, 0)
    b, eb = arc_sketch(doc, 20, ((-10, 0), (0, 8), (10, 0)))
    c, ec = arc_sketch(doc, 40, ((-10, 0), (0, 3), (10, 0)))
    open_tab(app, w, doc)
    d = start(w, "boundary_surface")
    d._set("op", "new")
    pick_curves(d, "curves", [(c, ec), (a, ea), (b, eb)])
    commit(w)
    f = feature_ok(doc, "loft")
    T.check(f["name"] == "Boundary surface 1", f"expected the feature named 'Boundary surface 1', got {f['name']!r}")
    rows = feature_rows(w)
    T.check("Boundary surface 1" in rows, f"expected a feature list row 'Boundary surface 1', got {rows}")
    w.edit_feature(f["id"])
    d2 = w.dialog
    T.check(d2 is not None, "the Boundary surface did not reopen for edit")
    got = [r["sketch"] for _k, r in d2.fields["curves"].items]
    T.check(got == [c, a, b], f"Boundary surface dialog reopened with U picks {got}, expected {[c, a, b]} in pick order")
    w.cancel_dialog()
    T.settle(w, 180)
    say("Boundary surface row in the feature list reads \"Boundary surface 1\"; its dialog reopens with the three U "
        "picks in pick order")
    reopen_same(doc, "boundary_three_u", tmp)

    # one V curve; the 7.9 refusal or the 8.0 network; a V curve that misses
    doc, u, v = network(n_v=1)
    err = boundary_network(app, w, doc, u, v)
    T.check(err is not None and "Add a second V curve" in err, f"one V curve: expected 'Add a second V curve', got {err!r}")
    say(f"Boundary surface with one V curve: \"{err}\"")

    doc, u, v = network()
    err = boundary_network(app, w, doc, u, v)
    if not network_built:
        T.check(err == MSG_BOUNDARY_79, f"Boundary surface with V curves on OCP {OCP_VERSION}: expected "
                                        f"{MSG_BOUNDARY_79!r}, got {err!r}")
        say(f"boundary surface network: refused on OpenCascade {OCCT_MAJOR}: \"{err}\"")
        return
    T.check(err is None, f"boundary surface network: failed with {err!r}")
    p = one_part(doc, "Boundary surface network")
    T.check(K.is_surface(p.shape) and len(K.faces(p.shape)) == 1 and not K.solids(p.shape),
            f"boundary surface network: expected one surface face, got {len(K.faces(p.shape))} faces")
    worst = 0.0
    for ref in u + v:
        (wire,) = F._curve_wires(doc.state, [ref])
        worst = max(worst, dist_to_surface(p.shape, wire))
    T.check(worst < 1e-4, f"boundary surface network: expected all four curves within 1e-4 mm, got {worst:.2e}")
    say(f"boundary surface network: built; two U and two V curves all lie on the one surface face "
        f"(worst distance {worst:.1e} mm)")
    reopen_same(doc, "boundary_network", tmp)
    doc, u, v = network(u_half=9.5)
    err = boundary_network(app, w, doc, u, v)
    T.check(err is not None and "V curve" in err and "U curve" in err and "does not touch" in err,
            f"a V curve missing a U curve: expected 'V curve n does not touch U curve m', got {err!r}")
    say(f"Boundary surface with a V curve half a millimetre off a U curve: \"{err}\"")


# ------------------------------------------------------------------------------------------------ closed, point
def closed_and_point(app, w, tmp):
    big, small = 20.0, 3.0
    doc = PartStudioDoc()
    secs = []
    for plane, x in ((FRONT, big), (RIGHT, big), (FRONT, -big), (RIGHT, -big)):
        sid = sketch_feature(doc, lambda s, x=x: s.add_circle(s.add_point(x, 0), small), plane)
        c = K.face_info(doc.state.sketches[sid].regions[0])["c"]
        secs.append((math.atan2(c[1], c[0]) % (2 * math.pi), sid))
    secs.sort()
    open_tab(app, w, doc)
    d = new_loft(w, [region(doc, sid) for _a, sid in secs])
    tick = next(wd for spec, wd in d.rows if spec["key"] == "closed")
    T.check(tick.isVisibleTo(d), "the Closed tick is not shown with four profiles")
    d._set("closed", True)
    commit(w)
    f = feature_ok(doc, "loft")
    T.check(f["params"].get("closed") is True, f"expected closed saved as True, got {f['params'].get('closed')}")
    body = one_part(doc, "closed loft").shape
    T.check(len(K.solids(body)) == 1 and K.is_valid(body),
            f"closed loft: expected one valid solid, got {len(K.solids(body))} solids, valid={K.is_valid(body)}")
    T.check(not _planar_caps(body), f"closed loft: expected no planar cap faces, got {len(_planar_caps(body))}")
    want = 2 * math.pi ** 2 * big * small * small
    got = _volume(body)
    T.check(abs(got - want) <= 0.02 * want, f"closed loft of four circles: expected volume {want:.0f} within 2 percent, "
                                            f"got {got:.1f} ({100 * (got - want) / want:+.2f} percent)")
    say(f"closed loft of four circles on a ring: one valid solid, no planar cap, volume {got:.1f} against "
        f"{want:.0f} ({100 * (got - want) / want:+.2f} percent; limit 2)")
    reopen_same(doc, "closed_loft", tmp)

    doc = PartStudioDoc()
    a, _ = add_sketch(doc, -10, -10, 20, 20)
    ref = _sketch_pt(doc, 30.0)
    open_tab(app, w, doc)
    d = new_loft(w, [region(doc, a)], ruled=True)
    d.handle_pick(Pick("sketch_point", (ref["sketch"], ref["entity"])))
    kinds = [it["kind"] for it in d.feature["params"]["profiles"]]
    T.check(kinds == ["region", "sketch"], f"square to a sketch point: expected profile kinds ['region', 'sketch'], "
                                           f"got {kinds}")
    commit(w)
    feature_ok(doc, "loft")
    body = one_part(doc, "square to a point").shape
    vol = _volume(body)
    T.check(len(K.faces(body)) == 5 and abs(vol - 4000) < 1e-3 and K.is_valid(body),
            f"square to a sketch point: expected 5 faces and volume 4000, got {len(K.faces(body))} faces, "
            f"volume {vol:.4f}, valid={K.is_valid(body)}")
    say(f"loft from a 20 x 20 square to the sketch point (0, 0, 30), ruled: 5 faces, volume {vol:.3f} (4000)")
    reopen_same(doc, "square_to_point", tmp)


# ------------------------------------------------------------------------------------------------ sweep control
def sweep_dialog(app, w, doc, control, prof=None, path_picks=None, lock_pick=None, fid=None):
    """A new sweep (`prof` region, `path_picks`) or the sweep `fid` reopened, with Profile control `control`."""
    if fid is None:
        d = start(w, "sweep")
        d._set("op", "new")
    else:
        w.edit_feature(fid)
        d = w.dialog
        T.check(d is not None and d.ftype == "sweep", "the sweep did not reopen for edit")
    d._set("control", control)
    app.processEvents()
    d._sync_visibility()
    if fid is None:
        d.activate(d.fields["profiles"])
        d.handle_pick(Pick("region", (prof, 0)))
        pick_curves(d, "path", path_picks)
    if lock_pick:
        pick_curves(d, "lock_dir", [lock_pick])
    commit(w)
    return feature_ok(doc, "sweep")


def keep_orientation(app, w, tmp):
    doc = PartStudioDoc()
    prof = sketch_feature(doc, rect, TOP)
    path = sketch_feature(doc, quarter, FRONT)
    open_tab(app, w, doc)
    f = sweep_dialog(app, w, doc, "", prof, [(path, ents(doc, path, "arc")[0])])
    a, b = cap_normals(one_part(doc, "default sweep").shape)
    turn = angle_deg(a, b)
    T.check(abs(turn - 90) < 1e-4, f"default sweep: expected the end cap a quarter turn from the start, got {turn:.3f}")
    sweep_dialog(app, w, doc, "keep", fid=f["id"])
    T.check(last_feature(doc, "sweep")["params"].get("control") == "keep", "Keep profile orientation was not saved")
    shape = one_part(doc, "Keep sweep").shape
    a, b = cap_normals(shape)
    par = abs(float(np.dot(a, b)))
    T.check(K.is_valid(shape) and abs(par - 1) < 1e-6,
            f"Keep profile orientation: expected the end cap parallel to the start (|dot| 1), got {par:.8f}")
    say(f"sweep with Keep profile orientation: the end cap is parallel to the start (|dot| {par:.8f}); "
        f"the default sweep turned it {turn:.1f} degrees")
    reopen_same(doc, "sweep_keep", tmp)


def tilted_path_doc():
    """A path that pitches and turns: a straight line along +x in Top, then an arc of radius 40 in a plane through
    the X axis tilted 45 degrees out of Top, leaving the line tangent. The profile is in the Right plane at the
    origin, 10 along y and 6 along z. Returns the document, the sketch ids and the stations (point, path tangent,
    horizontal part of the tangent). The default sweep keeps its profile square to the path, so it is cut by the plane
    normal to the tangent; Lock keeps the profile in a plane that holds the lock direction (a vertical plane here), so
    it is cut by the plane normal to the horizontal tangent. Each cut is the profile's own plane at that station."""
    doc = PartStudioDoc()
    prof = sketch_feature(doc, rect, RIGHT)
    line = sketch_feature(doc, lambda s: s.add_line(s.add_point(0, 0), s.add_point(R, 0)), TOP)
    pl = add(doc, "plane", type="line_angle", refs=[{"k": "axis", "ref": {"kind": "std", "name": "X"}}], angle=45.0)
    plane = F.resolve_plane(doc.state, {"kind": "plane", "fid": pl["id"]})
    end = math.radians(70)
    arc = sketch_feature(doc, lambda s: s.add_arc(s.add_point(R, R), s.add_point(R, 0),
                                                  s.add_point(R + R * math.sin(end), R - R * math.cos(end))),
                         {"kind": "plane", "fid": pl["id"]})
    zl = sketch_feature(doc, lambda s: s.add_line(s.add_point(0, 0), s.add_point(0, 10)), FRONT)    # along world z
    stations = []
    for deg in (20, 35, 50, 65):
        t = math.radians(deg)
        x, y = R + R * math.sin(t), R - R * math.cos(t)
        p = np.asarray(plane.to_world(x, y), float)
        q = np.asarray(plane.to_world(x + math.cos(t), y + math.sin(t)), float)
        h = (q - p) * np.array([1.0, 1.0, 0.0])
        stations.append((p, (q - p) / np.linalg.norm(q - p), h / np.linalg.norm(h)))
    return doc, prof, line, arc, zl, stations


def long_edges_off_square(shape, stations, locked):
    """Per station, the largest angle (degrees) by which a 10-long profile edge is off square to z. `locked` cuts by
    the vertical plane of a Lock sweep's profile, otherwise by the plane square to the path."""
    out = []
    for p, tangent, heading in stations:
        n = heading if locked else tangent
        edges = section_edges(shape, p, n)
        long_ = [q for q in edges if abs(np.linalg.norm(q[-1] - q[0]) - 10.0) < 0.1]
        T.check(len(long_) == 2, f"sweep section at {np.round(p, 2).tolist()}: expected two edges 10 mm long (within 0.1), got lengths "
                                 f"{[round(float(np.linalg.norm(q[-1] - q[0])), 3) for q in edges]}")
        dz = [abs(float((q[-1] - q[0])[2] / np.linalg.norm(q[-1] - q[0]))) for q in long_]
        out.append(max(math.degrees(math.asin(min(1.0, x))) for x in dz))
    return out


def lock_direction(app, w, tmp):
    doc, prof, line, arc, zl, stations = tilted_path_doc()
    T.check(not doc.errors, doc.errors)
    open_tab(app, w, doc)
    picks = [(line, ents(doc, line, "line")[0]), (arc, ents(doc, arc, "arc")[0])]
    f = sweep_dialog(app, w, doc, "", prof, picks)
    default = long_edges_off_square(one_part(doc, "default sweep").shape, stations, False)
    T.check(max(default) > 5.0, f"Lock profile direction: the default sweep does not discriminate; expected its long "
                                f"edge more than 5 degrees off square to z at some station, got {default}")
    sweep_dialog(app, w, doc, "lock", lock_pick=(zl, ents(doc, zl, "line")[0]), fid=f["id"])
    p = last_feature(doc, "sweep")["params"]
    T.check(p.get("control") == "lock" and p.get("lock_dir", {}).get("kind") == "sketch_line",
            f"Lock profile direction: expected control lock and a sketch line direction saved, got {p.get('control')} "
            f"{p.get('lock_dir')}")
    shape = one_part(doc, "Lock sweep").shape
    T.check(K.is_valid(shape) and len(K.solids(shape)) == 1, "Lock sweep: expected one valid solid")
    locked = long_edges_off_square(shape, stations, True)
    T.check(max(locked) < 0.1, f"Lock profile direction: expected the long edge square to z within 0.1 degree at every "
                               f"station, got {[round(x, 4) for x in locked]}")
    say(f"Lock profile direction changes the sweep: along a path that pitches and turns the default sweep's long edge "
        f"is up to {max(default):.2f} degrees off square to z (per station {[round(x, 2) for x in default]}); with "
        f"Lock along z it is up to {max(locked):.4f} degrees (per station {[round(x, 4) for x in locked]})")
    reopen_same(doc, "sweep_lock", tmp)


# ------------------------------------------------------------------------------------------------ golden, search
def golden_loft(app, w):
    doc = PartStudioDoc()
    a = sketch_feature(doc, lambda s: rect_lines(s, -10, -10, 20, 20))
    pl = add(doc, "plane", type="offset", refs=[], ref=TOP, offset=30.0)
    b = sketch_feature(doc, lambda s: s.add_circle(s.add_point(0, 0), 6), {"kind": "plane", "fid": pl["id"]})
    open_tab(app, w, doc)
    new_loft(w, [region(doc, a), region(doc, b)])
    commit(w)
    feature_ok(doc, "loft")
    body = one_part(doc, "loft_square_to_circle").shape
    with open(GOLDEN_FEATURES) as fh:
        golden = json.load(fh)["loft_square_to_circle"]
    want = next(iter(golden.values()))["volume"]
    got = K.volume(body)
    T.check(abs(got - want) <= 1e-4 * abs(want), f"golden loft_square_to_circle: expected volume {want:.4f} (golden, "
                                                 f"1e-4), got {got:.4f}")
    say(f"golden loft_square_to_circle built through the dialog: volume {got:.4f} equals the golden {want:.4f}")


def command_search(app, w):
    for text, name in [("guide curves", "Loft"), ("surface loft", "Loft"), ("ruled surface", "Loft"),
                       ("profile orientation", "Sweep"), ("gordon", "Boundary surface"),
                       ("network surface", "Boundary surface")]:
        names = palette_search(app, w, text)
        T.check(name in names, f"command search {text!r}: expected {name}, got {names}")
        say(f"command search {text!r}: {name} is result {names.index(name) + 1} of {len(names)}")
    names = palette_search(app, w, "boundary surface")
    T.check(names and names[0] == "Boundary surface", f"command search 'boundary surface': expected Boundary surface "
                                                      f"first, got {names[:4]}")
    say(f"command search 'boundary surface': Boundary surface is result 1 of {len(names)}")


# ------------------------------------------------------------------------------------------------ run
def run():
    app, w = T.new_engine_window(TMP)
    try:
        body(app, w)
    finally:
        T.close_engine_window(w)


def body(app, w):
    dec, i73_missing = spike_decision()
    skin, skin_angle = dec.get("skin"), float(dec.get("skin_angle_deg", "nan"))
    T.check(skin in ("hermite", "ghost"), f"unknown loft skin {skin!r} in {SPIKE_RESULT}")
    T.say(f"[info] mode: MainWindow(engine=True) (the app's own); OCP {OCP_VERSION}; spike DECISION for this build: "
          f"{' '.join(f'{k}={v}' for k, v in dec.items())}")
    network_built = OCCT_MAJOR >= 8 and dec.get("gordon80") == "ok" and not i73_missing

    doc, a, b = normal_to_profile(app, w, skin, skin_angle, TMP)
    preview_differs(app, w, doc, a, b)
    match_tangent(app, w, TMP)
    guides(app, w, dec, i73_missing, TMP)
    surface_and_boundary(app, w, network_built, TMP)
    closed_and_point(app, w, TMP)
    keep_orientation(app, w, TMP)
    lock_direction(app, w, TMP)
    golden_loft(app, w)
    command_search(app, w)
    if HASH_DIFFS:
        T.say(f"[info] {len(HASH_DIFFS)} saved parts have a different BREP hash in the worker build and the headless "
              f"rebuild (worker vs headless) while volume, area, faces, bounding box and element names match: "
              + "; ".join(HASH_DIFFS))


if __name__ == "__main__":                      # the engine workers import this file: only the main process runs it
    T.run_smoke("LOFT SMOKE", TMP, run)
