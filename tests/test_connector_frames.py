"""Mate connector frames keep their X axis through upstream edits (I1.14).

A connector on a flat face picks X along the face's longest straight edge when it is made, then stores X against
the plane's own axes (`x_ref`) and rebuilds it from there. An edit that changes which edge is longest must not
turn X round. Connectors from files saved before `x_ref` existed take X from the longest-edge rule once, on load,
and keep it from then on.
"""
import copy
import json
import os

import numpy as np
import pytest

from conftest import EXAMPLES_DIR, add, edge_refs, line_along, only_part, region_refs, sketch_feature
from freeshape import assembly as A
from freeshape import kernel as K
from freeshape.document import Document
from freeshape.features import Part
from freeshape.sketch import ORIGIN, Sketch

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Pnt

H = 10.0


# =========================================================================== part studio helpers
def plate_studio(w=60.0, d=40.0, fillet=None, hole=None):
    """A w x d x H plate from a dimensioned rectangle, optionally with filleted corners and a 4 mm through hole.
    Returns (doc, {name: id}) with the sketch / hole-sketch ids and the dimension constraint ids."""
    doc = Document()
    s = Sketch()
    p = [ORIGIN, s.add_point(w, 0), s.add_point(w, d), s.add_point(0, d)]
    L = [s.add_line(a, b) for a, b in zip(p, p[1:] + p[:1])]
    for line, t in zip(L, ["horizontal", "vertical", "horizontal", "vertical"]):
        s.add_constraint(t, line)
    ids = {"w": s.add_constraint("distance", L[0], value=w)["id"],
           "d": s.add_constraint("distance", L[1], value=d)["id"]}
    assert s.solve() and s.status["dof"] == 0
    f = doc.make_feature("sketch")
    f["params"]["sketch"] = s.to_dict()
    doc.add_feature(f)
    ids["sketch"] = f["id"]
    add(doc, "extrude", regions=region_refs(doc, f["id"]), depth=H, op="new")
    if fillet:
        add(doc, "fillet", edges=edge_refs(only_part(doc), line_along([0, 0, 1]), expect=4), radius=fillet)
    if hole is not None:
        ids["hole"] = sketch_feature(doc, lambda sk: sk.add_point_entity(sk.add_point(*hole)))
        add(doc, "hole", sketch=ids["hole"], standard="custom", diameter=4.0, end="through", style="simple")
    return doc, ids


def set_dim(doc, sid, cid, value):
    f = copy.deepcopy(doc.feature(sid))
    s = Sketch.from_dict(f["params"]["sketch"])
    for c in s.constraints:
        if c["id"] == cid:
            c["value"] = float(value)
    assert s.solve()
    f["params"]["sketch"] = s.to_dict()
    doc.replace_feature(f)
    assert not doc.errors, doc.errors


def move_hole(doc, hid, xy):
    s = Sketch()
    s.add_point_entity(s.add_point(*xy))
    s.solve()
    f = copy.deepcopy(doc.feature(hid))
    f["params"]["sketch"] = s.to_dict()
    doc.replace_feature(f)
    assert not doc.errors, doc.errors


# =========================================================================== assembly helpers
def assembly(studio, extra=None):
    """An assembly whose "plate" source resolves to the studio's only part (live, so edits show on refresh)."""
    extra = extra or {}
    a = A.AssemblyDoc()

    def resolve(src):
        if src["part"] in extra:
            return Part("x", src["part"], extra[src["part"]], (0.7, 0.7, 0.7), None)
        return only_part(studio)
    a.resolver = resolve
    return a


def src(name):
    return {"kind": "studio", "uid": "u1", "part": name}


def top_face(shape):
    out = [i for i, f in enumerate(K.faces(shape))
           if (n := K.face_info(f).get("n")) and n[2] > 0.9 and abs(K.face_info(f)["c"][2] - H) < 1e-6]
    assert len(out) == 1, out
    return out[0]


def bottom_face(shape):
    out = [i for i, f in enumerate(K.faces(shape)) if (n := K.face_info(f).get("n")) and n[2] < -0.9]
    assert len(out) == 1, out
    return out[0]


def longest_edge_x(shape, c):
    """X by the longest-edge rule, i.e. the frame a connector got before X references were stored."""
    return A.connector_frame(shape, c["kind"], c["index"], c.get("click"))["x"]


def frame_of(c):
    return A.frame_from_json(c["frame"])


# =========================================================================== edits that change the longest edge
def test_hole_into_corner_fillet_keeps_connector_x():
    """The Derived spike failure: a hole moved so it cuts into a corner fillet changed which top edge is longest
    and turned X round. The stored X reference keeps it; the mate holds and the mated block does not turn."""
    studio, ids = plate_studio(fillet=5.0, hole=(8.0, 8.0))
    block = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 20, 10, 8).Shape()
    a = assembly(studio, {"block": block})
    ip = a.insert(src("plate"))
    a.set_fixed(ip, True)
    ib = a.insert(src("block"))
    m = a.make_mate("fastened")
    m["a"] = a.connector(ip, "face", top_face(a.parts[ip].shape))
    m["b"] = a.connector(ib, "face", bottom_face(block))
    a.add_mate(m)
    a.solve()
    assert not a.errors, a.errors
    x_before = frame_of(m["a"])["x"]
    R_before = np.array(a.instance(ib)["T"], float)[:3, :3]

    move_hole(studio, ids["hole"], (2.5, 2.5))
    shp = a.parts[ip].shape
    a.refresh()
    new = a.parts[ip].shape
    assert not new.IsSame(shp)
    # the edit does change the longest-edge answer, so the test exercises the failure
    assert float(longest_edge_x(new, m["a"]) @ x_before) < 0.999
    assert float(frame_of(m["a"])["x"] @ x_before) >= 0.999
    assert m["id"] not in a.errors, a.errors
    assert not a.errors, a.errors
    assert np.allclose(np.array(a.instance(ib)["T"], float)[:3, :3], R_before, atol=1e-9)


def test_resize_changes_longest_edge_x_follows_stored_reference():
    """60 x 40 -> 30 x 40: the 40 mm edges become the longest, but X stays where it was stored."""
    studio, ids = plate_studio()
    a = assembly(studio)
    ip = a.insert(src("plate"))
    c = a.connector(ip, "face", top_face(a.parts[ip].shape))
    m = a.make_mate("fastened")
    m["a"], m["b"] = c, copy.deepcopy(c)
    a.mates.append(m)
    x_before = frame_of(c)["x"]
    assert abs(x_before[0]) > 0.999                          # along the 60 mm edges

    set_dim(studio, ids["sketch"], ids["w"], 30.0)
    a.refresh(solve=False)
    c = a.mates[0]["a"]
    assert float(np.abs(longest_edge_x(a.parts[ip].shape, c)[1])) > 0.999   # old rule now picks a 40 mm edge
    assert float(frame_of(c)["x"] @ x_before) >= 0.999


def test_x_reference_round_trips_through_save(tmp_path):
    studio, ids = plate_studio()
    a = assembly(studio)
    ip = a.insert(src("plate"))
    c = a.connector(ip, "face", top_face(a.parts[ip].shape))
    m = a.make_mate("fastened")
    m["a"], m["b"] = c, copy.deepcopy(c)
    a.mates.append(m)
    path = str(tmp_path / "plate.fasm")
    a.save(path)
    saved = json.load(open(path))["mates"][0]["a"]
    assert np.allclose(saved["x_ref"], c["x_ref"])
    set_dim(studio, ids["sketch"], ids["w"], 30.0)
    back = A.AssemblyDoc.load(path, resolver=a.resolver)
    assert float(frame_of(back.mates[0]["a"])["x"] @ frame_of(c)["x"]) >= 0.999


# =========================================================================== files saved before X references
def _hinge_resolver():
    studio = Document.load(os.path.join(EXAMPLES_DIR, "hinge_parts.fshape"))

    def resolve(s):
        if s.get("uid") != studio.uid:
            raise K.KernelError("Part Studio isn't open")
        return studio.state.parts[s["part"]]
    return resolve


def _connectors(asm):
    return [(m["id"], side, m[side]) for m in asm.mates for side in ("a", "b")]


def _same_frame(f1, f2):
    return all(np.allclose(f1[k], f2[k], atol=1e-9) for k in ("o", "z", "x"))


def test_hinge_without_x_reference_keeps_longest_edge_frames(tmp_path):
    src_path = os.path.join(EXAMPLES_DIR, "hinge.fasm")
    raw = json.load(open(src_path))
    assert all("x_ref" not in m[s] for m in raw["mates"] for s in ("a", "b"))
    asm = A.AssemblyDoc.load(src_path, resolver=_hinge_resolver())
    assert not asm.errors, asm.errors
    frames = {}
    for mid, side, c in _connectors(asm):
        shp = asm.parts[c["inst"]].shape
        rule = A.connector_frame(shp, c["kind"], c["index"], c.get("click"))
        assert _same_frame(frame_of(c), rule), (mid, side)
        frames[(mid, side)] = frame_of(c)

    path = str(tmp_path / "hinge.fasm")
    asm.save(path)
    saved = json.load(open(path))
    assert all(len(m[s]["x_ref"]) == 2 for m in saved["mates"] for s in ("a", "b"))
    back = A.AssemblyDoc.load(path, resolver=_hinge_resolver())
    assert not back.errors, back.errors
    for mid, side, c in _connectors(back):
        assert "x_ref" in c
        assert _same_frame(frame_of(c), frames[(mid, side)]), (mid, side)


def test_flat_face_connector_without_x_reference_adopts_longest_edge_once(tmp_path):
    """An old file's flat-face connector takes X from the longest edge on first load, then keeps it."""
    studio, ids = plate_studio()
    a = assembly(studio)
    ip = a.insert(src("plate"))
    c = a.connector(ip, "face", top_face(a.parts[ip].shape))
    m = a.make_mate("fastened")
    m["a"], m["b"] = c, copy.deepcopy(c)
    a.mates.append(m)
    path = str(tmp_path / "old.fasm")
    a.save(path)
    d = json.load(open(path))
    for s in ("a", "b"):
        d["mates"][0][s].pop("x_ref")
    json.dump(d, open(path, "w"))

    back = A.AssemblyDoc.load(path, resolver=a.resolver)
    c1 = back.mates[0]["a"]
    assert _same_frame(frame_of(c1), A.connector_frame(back.parts[ip].shape, "face", c1["index"]))
    assert c1.get("x_ref") is not None
    x0 = frame_of(c1)["x"]
    set_dim(studio, ids["sketch"], ids["w"], 30.0)
    back.refresh(solve=False)
    assert float(frame_of(back.mates[0]["a"])["x"] @ x0) >= 0.999


@pytest.mark.parametrize("x_ref", [None, "junk", [0.0, 0.0], [1.0]])
def test_bad_x_reference_falls_back_to_computed_frame(x_ref):
    fr = A.connector_frame(BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 60, 40, 10).Shape(), "face", 5)
    assert A.apply_x_ref(fr, x_ref) is fr
