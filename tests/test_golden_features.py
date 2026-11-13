"""Characterisation of every feature runner and its main options.

Each case builds a small model through the Document (the same path the UI uses), checks the analytic answer
where one exists, and then:
  * test_feature_golden   compares a fingerprint of every resulting part with tests/golden/features.json;
  * test_save_load_roundtrip saves the model, loads it back and checks the rebuild is identical.

These are the baseline for OpenCascade upgrades: a changed golden means the kernel now produces different
geometry or topology for the same feature tree.
"""
import base64
import functools
import math
import os
import tempfile

import numpy as np
import pytest

from conftest import (ANALYTIC, FREEFORM, FRONT, RIGHT, TOP, add, approx_rel, assert_golden, block_doc,
                      both, centre_near, edge_refs, face_refs, line_along, normal_is, only_part, rect_lines,
                      region_refs, shape_fingerprint, sketch_feature, state_fingerprint)
from freeshape import features as F
from freeshape import kernel as K
from freeshape.document import Document

PI = math.pi
CASES = {}


def case(tol=ANALYTIC):
    def deco(fn):
        CASES[fn.__name__] = (fn, tol)
        return fn
    return deco


def total_volume(doc):
    return sum(K.volume(p.shape) for p in doc.state.parts.values())


def vol(doc):
    return K.volume(only_part(doc).shape)


def bbox_of(doc):
    lo, hi = K.bbox(only_part(doc).shape)
    return np.concatenate([lo, hi])


def assert_box(doc, expect, tol=1e-6):
    """Loose OCCT bbox (K.bbox adds a gap of the tolerance), so compare to ~1e-6."""
    got = bbox_of(doc)
    assert np.allclose(got, expect, atol=tol), (got.tolist(), expect)


def circle_sketch(doc, cx, cy, r, plane=None):
    return sketch_feature(doc, lambda s: s.add_circle(s.add_point(cx, cy), r), plane)


def point_sketch(doc, pts, plane=None):
    def build(s):
        for x, y in pts:
            s.add_point_entity(s.add_point(x, y))
    return sketch_feature(doc, build, plane)


# =========================================================================== extrude

@case()
def extrude_blind():
    doc = block_doc(40, 20, 10)
    approx_rel(vol(doc), 8000)
    assert_box(doc, [0, 0, 0, 40, 20, 10])
    return doc


@case()
def extrude_symmetric():
    doc = Document()
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 40, 20))
    add(doc, "extrude", regions=region_refs(doc, sid), depth=10.0, end="symmetric", op="new")
    approx_rel(vol(doc), 8000)
    assert_box(doc, [0, 0, -5, 40, 20, 5])
    return doc


@case()
def extrude_second_direction():
    doc = Document()
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 40, 20))
    add(doc, "extrude", regions=region_refs(doc, sid), depth=10.0, second=True, depth2=4.0, op="new")
    approx_rel(vol(doc), 40 * 20 * 14)
    assert_box(doc, [0, 0, -4, 40, 20, 10])
    return doc


@case()
def extrude_flip():
    doc = Document()
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 40, 20))
    add(doc, "extrude", regions=region_refs(doc, sid), depth=10.0, flip=True, op="new")
    assert_box(doc, [0, 0, -10, 40, 20, 0])
    return doc


@case()
def extrude_front_plane():
    doc = block_doc(40, 20, 10, plane=FRONT)
    approx_rel(vol(doc), 8000)
    return doc


@case()
def extrude_face_add():
    doc = block_doc(40, 20, 10)
    top = face_refs(only_part(doc), normal_is([0, 0, 1]), expect=1)
    add(doc, "extrude", regions=[], faces=top, depth=5.0, op="add")
    approx_rel(vol(doc), 40 * 20 * 15)
    assert_box(doc, [0, 0, 0, 40, 20, 15])
    return doc


@case()
def extrude_cut_blind():
    doc = block_doc(40, 20, 10)
    sid = circle_sketch(doc, 20, 10, 5)
    add(doc, "extrude", regions=region_refs(doc, sid), depth=4.0, op="remove")
    approx_rel(vol(doc), 8000 - 25 * PI * 4)
    return doc


@case()
def extrude_cut_through():
    doc = block_doc(40, 20, 10)
    sid = circle_sketch(doc, 20, 10, 5)
    add(doc, "extrude", regions=region_refs(doc, sid), depth=1.0, end="through", op="remove")
    approx_rel(vol(doc), 8000 - 25 * PI * 10)
    return doc


@case()
def extrude_intersect():
    doc = block_doc(40, 20, 10)
    sid = circle_sketch(doc, 20, 10, 5)
    add(doc, "extrude", regions=region_refs(doc, sid), depth=10.0, op="intersect")
    approx_rel(vol(doc), 25 * PI * 10)
    return doc


@case()
def extrude_new_second_body():
    doc = block_doc(40, 20, 10)
    sid = sketch_feature(doc, lambda s: rect_lines(s, 60, 0, 10, 10))
    add(doc, "extrude", regions=region_refs(doc, sid), depth=5.0, op="new")
    assert len(doc.state.parts) == 2
    approx_rel(total_volume(doc), 8000 + 500)
    return doc


@case()
def extrude_region_with_hole():
    doc = Document()

    def build(s):
        rect_lines(s, 0, 0, 40, 20)
        s.add_circle(s.add_point(20, 10), 5)
    sid = sketch_feature(doc, build)
    ring = region_refs(doc, sid, lambda i: i["area"] > 500)
    assert len(ring) == 1
    add(doc, "extrude", regions=ring, depth=10.0, op="new")
    approx_rel(vol(doc), 8000 - 250 * PI)
    return doc


# =========================================================================== revolve

def _ring_sketch(doc, axis_line=False):
    def build(s):
        rect_lines(s, 20, 0, 10, 10)
        if axis_line:
            s.add_line(s.add_point(0, -5), s.add_point(0, 20), True)
    return sketch_feature(doc, build, FRONT)


@case()
def revolve_full():
    doc = Document()
    sid = _ring_sketch(doc)
    add(doc, "revolve", regions=region_refs(doc, sid), axis={"kind": "std", "name": "Z"}, end="full", op="new")
    approx_rel(vol(doc), PI * (30 ** 2 - 20 ** 2) * 10)
    return doc


@case()
def revolve_angle():
    doc = Document()
    sid = _ring_sketch(doc)
    add(doc, "revolve", regions=region_refs(doc, sid), axis={"kind": "std", "name": "Z"}, end="angle",
        angle=90.0, op="new")
    approx_rel(vol(doc), PI * (30 ** 2 - 20 ** 2) * 10 / 4)
    return doc


@case()
def revolve_symmetric():
    doc = Document()
    sid = _ring_sketch(doc)
    add(doc, "revolve", regions=region_refs(doc, sid), axis={"kind": "std", "name": "Z"}, end="symmetric",
        angle=90.0, op="new")
    approx_rel(vol(doc), PI * (30 ** 2 - 20 ** 2) * 10 / 4)
    return doc


@case()
def revolve_flip_angle():
    doc = Document()
    sid = _ring_sketch(doc)
    add(doc, "revolve", regions=region_refs(doc, sid), axis={"kind": "std", "name": "Z"}, end="angle",
        angle=90.0, flip=True, op="new")
    approx_rel(vol(doc), PI * (30 ** 2 - 20 ** 2) * 10 / 4)
    return doc


@case()
def revolve_sketch_line_axis():
    doc = Document()
    sid = _ring_sketch(doc, axis_line=True)
    sk = doc.state.sketches[sid].sketch
    line = next(k for k, e in sk.entities.items() if e["type"] == "line" and e.get("construction"))
    add(doc, "revolve", regions=region_refs(doc, sid), axis={"kind": "sketch_line", "sketch": sid, "line": line},
        end="full", op="new")
    approx_rel(vol(doc), PI * (30 ** 2 - 20 ** 2) * 10)
    return doc


# =========================================================================== fillet / chamfer

VERTICAL = line_along([0, 0, 1])


def _top_edges(i):
    return i["type"] == int(K.GeomAbs_Line) and abs(i["c"][2] - 10) < 1e-6


@case(FREEFORM)
def fillet_vertical_edges():
    doc = block_doc(40, 20, 10)
    add(doc, "fillet", edges=edge_refs(only_part(doc), VERTICAL, expect=4), radius=3.0)
    approx_rel(vol(doc), 8000 - 4 * (9 - 9 * PI / 4) * 10, rel=FREEFORM)
    return doc


@case(FREEFORM)
def fillet_top_loop():
    doc = block_doc(40, 20, 10)
    add(doc, "fillet", edges=edge_refs(only_part(doc), _top_edges, expect=4), radius=2.0)
    # the four top edges meet at mitred corners: a 2 mm layer whose plan is a 40x20 rectangle with the
    # quarter-round profile swept around it
    assert vol(doc) < 8000
    return doc


@case(FREEFORM)
def fillet_all_edges():
    doc = block_doc(40, 20, 10)
    add(doc, "fillet", edges=edge_refs(only_part(doc), lambda i: True, expect=12), radius=1.0)
    # all edges rounded = an inner box swept by a sphere (Minkowski sum)
    a, b, c, r = 38.0, 18.0, 8.0, 1.0
    exp = a * b * c + 2 * r * (a * b + b * c + a * c) + PI * r * r * (a + b + c) + 4 / 3 * PI * r ** 3
    approx_rel(vol(doc), exp, rel=FREEFORM)
    return doc


@case()
def chamfer_vertical_edges():
    doc = block_doc(40, 20, 10)
    add(doc, "chamfer", edges=edge_refs(only_part(doc), VERTICAL, expect=4), distance=2.0)
    approx_rel(vol(doc), 8000 - 4 * (2 * 2 / 2) * 10)
    return doc


@case()
def chamfer_top_loop():
    doc = block_doc(40, 20, 10)
    add(doc, "chamfer", edges=edge_refs(only_part(doc), _top_edges, expect=4), distance=1.0)
    # bottom 9 mm is the full box; the top 1 mm is a prismatoid from 40x20 to 38x18
    approx_rel(vol(doc), 40 * 20 * 9 + (800 + 4 * 39 * 19 + 38 * 18) / 6)
    return doc


@case()
def chamfer_all_edges():
    doc = block_doc(40, 20, 10)
    add(doc, "chamfer", edges=edge_refs(only_part(doc), lambda i: True, expect=12), distance=1.0)
    assert vol(doc) < 8000
    return doc


# =========================================================================== shell

@case()
def shell_inward_top():
    doc = block_doc(40, 20, 10)
    add(doc, "shell", faces=face_refs(only_part(doc), normal_is([0, 0, 1]), expect=1), thickness=2.0)
    approx_rel(vol(doc), 8000 - 36 * 16 * 8)
    assert_box(doc, [0, 0, 0, 40, 20, 10])
    return doc


@case()
def shell_outward_top():
    doc = block_doc(40, 20, 10)
    add(doc, "shell", faces=face_refs(only_part(doc), normal_is([0, 0, 1]), expect=1), thickness=2.0,
        outward=True)
    assert_box(doc, [-2, -2, -2, 42, 22, 10])
    return doc


@case()
def shell_two_faces():
    doc = block_doc(40, 20, 10)
    part = only_part(doc)
    faces = face_refs(part, normal_is([0, 0, 1]), 1) + face_refs(part, normal_is([1, 0, 0]), 1)
    add(doc, "shell", faces=faces, thickness=2.0)
    approx_rel(vol(doc), 8000 - 38 * 16 * 8)
    return doc


# =========================================================================== mirror

@case()
def mirror_touching_fuses():
    doc = block_doc(40, 20, 10)
    add(doc, "mirror", parts=list(doc.state.parts), plane=RIGHT, op="add")
    approx_rel(vol(doc), 16000)
    assert_box(doc, [-40, 0, 0, 40, 20, 10])
    return doc


@case()
def mirror_disjoint_new():
    doc = block_doc(40, 20, 10, x0=10)
    add(doc, "mirror", parts=list(doc.state.parts), plane=RIGHT, op="new")
    assert len(doc.state.parts) == 2
    los = sorted(round(float(K.bbox(p.shape)[0][0]), 4) for p in doc.state.parts.values())
    assert los == [-50.0, 10.0], los
    return doc


@case()
def mirror_about_face():
    doc = block_doc(40, 20, 10)
    side = face_refs(only_part(doc), normal_is([1, 0, 0]), expect=1)[0]
    add(doc, "mirror", parts=list(doc.state.parts), plane={"kind": "face", "face": side}, op="add")
    approx_rel(vol(doc), 16000)
    assert_box(doc, [0, 0, 0, 80, 20, 10])
    return doc


# =========================================================================== patterns

@case()
def linear_pattern_new():
    doc = block_doc(10, 10, 10)
    add(doc, "linear_pattern", parts=list(doc.state.parts), count=3, spacing=20.0, op="new")
    assert len(doc.state.parts) == 3
    approx_rel(total_volume(doc), 3000)
    return doc


@case()
def linear_pattern_add_touching():
    doc = block_doc(10, 10, 10)
    add(doc, "linear_pattern", parts=list(doc.state.parts), count=3, spacing=10.0, op="add")
    approx_rel(vol(doc), 3000)
    assert_box(doc, [0, 0, 0, 30, 10, 10])
    return doc


@case()
def linear_pattern_flip_y():
    doc = block_doc(10, 10, 10)
    add(doc, "linear_pattern", parts=list(doc.state.parts), direction={"kind": "std", "name": "Y"}, flip=True,
        count=2, spacing=15.0, op="new")
    los = sorted(round(float(K.bbox(p.shape)[0][1]), 4) for p in doc.state.parts.values())
    assert los == [-15.0, 0.0], los
    return doc


@case()
def circular_pattern_full():
    doc = block_doc(20, 10, 10, x0=20, y0=-5)
    add(doc, "circular_pattern", parts=list(doc.state.parts), count=6, angle=360.0, op="new")
    assert len(doc.state.parts) == 6
    angs = sorted(round(math.degrees(math.atan2(c[1], c[0])) % 360, 6)
                  for c in (K.mass_props(p.shape)["centroid"] for p in doc.state.parts.values()))
    assert angs == [0, 60, 120, 180, 240, 300], angs
    for p in doc.state.parts.values():
        c = K.mass_props(p.shape)["centroid"]
        approx_rel(math.hypot(c[0], c[1]), 30.0)
        approx_rel(K.volume(p.shape), 2000)
    return doc


@case()
def circular_pattern_partial():
    doc = block_doc(20, 10, 10, x0=20, y0=-5)
    add(doc, "circular_pattern", parts=list(doc.state.parts), count=3, angle=90.0, op="new")
    angs = sorted(round(math.degrees(math.atan2(c[1], c[0])) % 360, 6)
                  for c in (K.mass_props(p.shape)["centroid"] for p in doc.state.parts.values()))
    assert angs == [0, 45, 90], angs
    return doc


@case()
def circular_pattern_add_overlapping():
    # a bar through the axis, copied at 90 degrees: the copy crosses the original and fuses into a plus sign
    doc = block_doc(40, 10, 10, x0=-20, y0=-5)
    add(doc, "circular_pattern", parts=list(doc.state.parts), count=2, angle=90.0, op="add")
    approx_rel(vol(doc), 2 * 4000 - 1000)
    return doc


# =========================================================================== boolean

def _two_blocks():
    doc = block_doc(40, 20, 10)
    sid = sketch_feature(doc, lambda s: rect_lines(s, 30, 5, 20, 10))
    add(doc, "extrude", regions=region_refs(doc, sid), depth=10.0, op="new")
    ids = list(doc.state.parts)
    assert len(ids) == 2
    return doc, ids[0], ids[1]


@case()
def boolean_union():
    doc, a, b = _two_blocks()
    add(doc, "boolean", op="union", tools=[b], targets=[a])
    approx_rel(vol(doc), 8000 + 2000 - 1000)
    return doc


@case()
def boolean_subtract():
    doc, a, b = _two_blocks()
    add(doc, "boolean", op="subtract", tools=[b], targets=[a])
    approx_rel(vol(doc), 7000)
    return doc


@case()
def boolean_intersect():
    doc, a, b = _two_blocks()
    add(doc, "boolean", op="intersect", tools=[b], targets=[a])
    approx_rel(vol(doc), 1000)
    return doc


@case()
def boolean_subtract_keep_tools():
    doc, a, b = _two_blocks()
    add(doc, "boolean", op="subtract", tools=[b], targets=[a], keep_tools=True)
    vols = sorted(round(K.volume(p.shape), 6) for p in doc.state.parts.values())
    assert vols == [2000.0, 7000.0], vols
    return doc


# =========================================================================== hole

def _hole(**params):
    doc = block_doc(40, 20, 10)
    hs = point_sketch(doc, [(20, 10)])
    add(doc, "hole", sketch=hs, **params)
    return doc


def frustum(R, r, h):
    return PI * h / 3 * (R * R + R * r + r * r)


@case()
def hole_custom_through():
    doc = _hole(standard="custom", diameter=6.0, end="through", style="simple")
    approx_rel(vol(doc), 8000 - 9 * PI * 10)
    return doc


@case()
def hole_custom_blind_tip():
    doc = _hole(standard="custom", diameter=6.0, end="blind", depth=5.0, tip=True, style="simple")
    tip_h = 3 / math.tan(math.radians(59))
    approx_rel(vol(doc), 8000 - 9 * PI * 5 - 9 * PI * tip_h / 3)
    return doc


@case()
def hole_custom_blind_flat():
    doc = _hole(standard="custom", diameter=6.0, end="blind", depth=5.0, tip=False, style="simple")
    approx_rel(vol(doc), 8000 - 9 * PI * 5)
    return doc


@case()
def hole_clearance_fits():
    doc = block_doc(40, 20, 10)
    for x, fit in ((8, "close"), (20, "normal"), (32, "loose")):
        hs = point_sketch(doc, [(x, 10)])
        add(doc, "hole", sketch=hs, standard="clearance", size="M5", fit=fit, end="through", style="simple")
    removed = sum(PI * (d / 2) ** 2 * 10 for d in F.CLEARANCE["M5"])
    approx_rel(vol(doc), 8000 - removed)
    return doc


@case()
def hole_counterbore_m5():
    doc = _hole(standard="clearance", size="M5", fit="normal", end="through", style="counterbore")
    d, (cd, cdep) = 5.5, F.COUNTERBORE["M5"]
    approx_rel(vol(doc), 8000 - PI * (d / 2) ** 2 * 10 - PI * ((cd / 2) ** 2 - (d / 2) ** 2) * cdep)
    return doc


@case()
def hole_countersink_m4():
    doc = _hole(standard="clearance", size="M4", fit="normal", end="through", style="countersink")
    d, cs = 4.5, F.COUNTERSINK["M4"]
    h = (cs - d) / 2
    r = d / 2
    approx_rel(vol(doc), 8000 - PI * r * r * 10 - (frustum(cs / 2, r, h) - PI * r * r * h))
    return doc


@case()
def hole_custom_counterbore_blind():
    doc = _hole(standard="custom", diameter=4.0, end="blind", depth=6.0, tip=False, style="counterbore",
                counterbore_dia=8.0, counterbore_depth=2.0)
    approx_rel(vol(doc), 8000 - 4 * PI * 6 - (16 - 4) * PI * 2)
    return doc


@case()
def hole_tapped_drill():
    doc = _hole(standard="tapped", size="M6", end="through", style="simple")
    approx_rel(vol(doc), 8000 - PI * (F.TAP_DRILL["M6"] / 2) ** 2 * 10)
    return doc


@case()
def hole_from_sketch_circles():
    doc = block_doc(40, 20, 10)
    hs = circle_sketch(doc, 20, 10, 4)
    add(doc, "hole", sketch=hs, standard="sketch", end="through", style="simple")
    approx_rel(vol(doc), 8000 - 16 * PI * 10)
    return doc


@case(FREEFORM)
def hole_tapped_modelled_m5():
    return _hole(standard="tapped", size="M5", end="through", style="simple", model_thread=True)


# =========================================================================== variable

@case()
def variable_drives_depth():
    doc = Document()
    add(doc, "variable", name="h", expr="12")
    add(doc, "variable", name="h2", expr="#h / 2 + 1")
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 40, 20))
    add(doc, "extrude", regions=region_refs(doc, sid), depth="#h2 * 2", op="new")
    assert doc.state.vars == {"h": 12.0, "h2": 7.0}
    approx_rel(vol(doc), 40 * 20 * 14)
    return doc


# =========================================================================== sweep / loft

@case()
def sweep_straight():
    doc = Document()
    pid = circle_sketch(doc, 0, 0, 3)
    path = sketch_feature(doc, lambda s: s.add_line(s.add_point(0, 0), s.add_point(0, 40)), FRONT)
    line = next(k for k, e in doc.state.sketches[path].sketch.entities.items() if e["type"] == "line")
    add(doc, "sweep", regions=region_refs(doc, pid), path=[{"kind": "sketch_chain", "sketch": path, "entity": line}],
        op="new")
    approx_rel(vol(doc), 9 * PI * 40, rel=1e-6)
    return doc


@case(FREEFORM)
def sweep_bent_path():
    doc = Document()
    pid = circle_sketch(doc, 0, 0, 3)

    def build(s):
        a, b, c = s.add_point(0, 0), s.add_point(0, 40), s.add_point(30, 40)
        s.add_line(a, b)
        s.add_line(b, c)
    path = sketch_feature(doc, build, FRONT)
    line = next(k for k, e in doc.state.sketches[path].sketch.entities.items() if e["type"] == "line")
    add(doc, "sweep", regions=region_refs(doc, pid), path=[{"kind": "sketch_chain", "sketch": path, "entity": line}],
        op="new")
    return doc


def _loft_doc(top_build, ruled):
    doc = Document()
    a = sketch_feature(doc, lambda s: rect_lines(s, -10, -10, 20, 20))
    pl = add(doc, "plane", type="offset", refs=[], ref=TOP, offset=30.0)
    b = sketch_feature(doc, top_build, {"kind": "plane", "fid": pl["id"]})
    add(doc, "loft", profiles=[{"kind": "region", "ref": region_refs(doc, a)[0]},
                               {"kind": "region", "ref": region_refs(doc, b)[0]}], ruled=ruled, op="new")
    return doc


@case()
def loft_ruled_frustum():
    doc = _loft_doc(lambda s: rect_lines(s, -5, -5, 10, 10), ruled=True)
    approx_rel(vol(doc), 30 / 3 * (400 + 100 + 200))
    return doc


@case(FREEFORM)
def loft_square_to_circle():
    doc = _loft_doc(lambda s: s.add_circle(s.add_point(0, 0), 6), ruled=False)
    assert 400 * 30 * 0.3 < vol(doc) < 400 * 30
    return doc


# =========================================================================== draft / split / move

@case()
def draft_four_walls():
    doc = block_doc(40, 30, 20)
    sides = face_refs(only_part(doc), lambda i: i.get("n") is not None and abs(i["n"][2]) < 0.1, expect=4)
    add(doc, "draft", faces=sides, neutral=TOP, angle=5.0)
    t = 20 * math.tan(math.radians(5))
    a1, a2, am = 40 * 30, (40 - 2 * t) * (30 - 2 * t), (40 - t) * (30 - t)
    approx_rel(vol(doc), 20 / 6 * (a1 + 4 * am + a2))
    return doc


def _split(keep):
    doc = block_doc(40, 20, 10)
    pl = add(doc, "plane", type="offset", refs=[], ref=RIGHT, offset=10.0)
    add(doc, "split", parts=list(doc.state.parts), plane={"kind": "plane", "fid": pl["id"]}, keep=keep)
    return doc


@case()
def split_both():
    doc = _split("both")
    vols = sorted(round(K.volume(p.shape), 6) for p in doc.state.parts.values())
    assert vols == [2000.0, 6000.0], vols
    return doc


@case()
def split_keep_front():
    doc = _split("front")
    approx_rel(vol(doc), 6000)
    return doc


@case()
def split_keep_back():
    doc = _split("back")
    approx_rel(vol(doc), 2000)
    return doc


@case()
def move_translate_copy():
    doc = block_doc(10, 10, 10)
    add(doc, "move", parts=list(doc.state.parts), dx=50.0, dy=0.0, dz=5.0, rotate=0.0, copy=True)
    los = sorted(tuple(np.round(K.bbox(p.shape)[0], 4)) for p in doc.state.parts.values())
    assert los == [(0, 0, 0), (50, 0, 5)], los
    return doc


@case()
def move_rotate():
    doc = block_doc(10, 10, 10)
    add(doc, "move", parts=list(doc.state.parts), rotate=90.0, axis={"kind": "std", "name": "Z"}, copy=False)
    assert_box(doc, [-10, 0, 0, 0, 10, 10])
    return doc


@case()
def move_rotate_about_edge():
    doc = block_doc(10, 10, 10)
    part = only_part(doc)
    e = edge_refs(part, both(line_along([0, 0, 1]), centre_near([10, 10, 5])), expect=1)[0]
    add(doc, "move", parts=[part.id], rotate=180.0, axis={"kind": "edge", "edge": e})
    assert_box(doc, [10, 10, 0, 20, 20, 10])
    return doc


# =========================================================================== thread

@case(FREEFORM)
def thread_internal_auto():
    doc = _hole(standard="custom", diameter=5.0, end="through", style="simple")
    hole = face_refs(only_part(doc), lambda i: abs(i.get("r", 0) - 2.5) < 1e-6, expect=1)
    add(doc, "thread", faces=hole, size="auto")
    return doc


@case(FREEFORM)
def thread_external_length():
    doc = Document()
    sid = circle_sketch(doc, 0, 0, 3)
    add(doc, "extrude", regions=region_refs(doc, sid), depth=20.0, op="new")
    shaft = face_refs(only_part(doc), lambda i: abs(i.get("r", 0) - 3) < 1e-6, expect=1)
    add(doc, "thread", faces=shaft, size="M6", length_mode="length", length=12.0)
    assert vol(doc) < 9 * PI * 20
    return doc


@case(FREEFORM)
def thread_internal_left_hand_clearance():
    doc = _hole(standard="custom", diameter=5.0, end="through", style="simple")
    hole = face_refs(only_part(doc), lambda i: abs(i.get("r", 0) - 2.5) < 1e-6, expect=1)
    add(doc, "thread", faces=hole, size="M6", left_hand=True, clearance=0.1)
    return doc


# =========================================================================== sheet metal

def _sheet(thickness=1.5):
    doc = Document()
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 80, 50))
    add(doc, "sm_base", regions=region_refs(doc, sid), thickness=thickness, radius=1.5, k=0.44)
    return doc


@case()
def sm_base_flat():
    doc = _sheet()
    approx_rel(vol(doc), 80 * 50 * 1.5)
    return doc


def _front_top_edge(doc, t=1.5):
    part = only_part(doc)
    return edge_refs(part, lambda i: "p0" in i and abs(i["p0"][1]) < 1e-9 and abs(i["p1"][1]) < 1e-9
                     and abs(i["p0"][2] - t) < 1e-9, expect=1)


@case()
def sm_flange_outside_90():
    doc = _sheet()
    add(doc, "sm_flange", edges=_front_top_edge(doc), length=20.0)
    assert len(doc.state.sheet[only_part(doc).id].bends) == 1
    lo, hi = K.bbox(only_part(doc).shape)
    assert abs(hi[2] - 20) < 1e-6 and abs(lo[1] + 3.0) < 1e-6
    return doc


@case()
def sm_flange_inside_45_down():
    doc = _sheet()
    add(doc, "sm_flange", edges=_front_top_edge(doc), length=15.0, angle=45.0, position="inside", down=True)
    lo, hi = K.bbox(only_part(doc).shape)
    assert lo[2] < -5
    return doc


@case()
def sm_flange_own_radius():
    doc = _sheet()
    add(doc, "sm_flange", edges=_front_top_edge(doc), length=20.0, own_radius=True, radius=4.0)
    return doc


# =========================================================================== face tools

@case()
def delete_face_heal_hole():
    doc = _hole(standard="custom", diameter=6.0, end="through", style="simple")
    hole = face_refs(only_part(doc), lambda i: abs(i.get("r", 0) - 3) < 1e-6, expect=1)
    add(doc, "delete_face", faces=hole, mode="heal")
    approx_rel(vol(doc), 8000)
    return doc


@case()
def delete_face_heal_fillets():
    doc = block_doc(40, 20, 10)
    add(doc, "fillet", edges=edge_refs(only_part(doc), VERTICAL, expect=4), radius=3.0)
    rounds = face_refs(only_part(doc), lambda i: abs(i.get("r", 0) - 3) < 1e-6, expect=4)
    add(doc, "delete_face", faces=rounds, mode="heal")
    approx_rel(vol(doc), 8000)
    assert len(K.faces(only_part(doc).shape)) == 6
    return doc


@case()
def delete_face_cap():
    doc = _hole(standard="custom", diameter=6.0, end="blind", depth=5.0, tip=False, style="simple")
    floor = face_refs(only_part(doc), both(normal_is([0, 0, -1]), centre_near([20, 10, 5])), expect=1)
    add(doc, "delete_face", faces=floor, mode="cap")
    return doc


@case()
def delete_face_open():
    doc = block_doc(40, 20, 10)
    add(doc, "delete_face", faces=face_refs(only_part(doc), normal_is([0, 0, 1]), expect=1), mode="open")
    assert not K.solids(only_part(doc).shape)
    return doc


@case()
def move_face_out():
    doc = block_doc(40, 20, 10)
    add(doc, "move_face", faces=face_refs(only_part(doc), normal_is([0, 0, 1]), expect=1), distance=5.0)
    approx_rel(vol(doc), 40 * 20 * 15)
    return doc


@case()
def move_face_in_flip():
    doc = block_doc(40, 20, 10)
    add(doc, "move_face", faces=face_refs(only_part(doc), normal_is([1, 0, 0]), expect=1), distance=4.0, flip=True)
    approx_rel(vol(doc), 36 * 20 * 10)
    return doc


@case()
def offset_surface_top():
    doc = block_doc(40, 20, 10)
    add(doc, "offset_surface", faces=face_refs(only_part(doc), normal_is([0, 0, 1]), expect=1), distance=4.0)
    surf = [p for p in doc.state.parts.values() if not K.solids(p.shape)]
    assert len(surf) == 1
    approx_rel(K.face_info(K.faces(surf[0].shape)[0])["area"], 800)
    return doc


# =========================================================================== plane / import

@case()
def plane_offset_sketch():
    doc = Document()
    pl = add(doc, "plane", type="offset", refs=[], ref=TOP, offset=15.0)
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 40, 20), {"kind": "plane", "fid": pl["id"]})
    add(doc, "extrude", regions=region_refs(doc, sid), depth=10.0, op="new")
    assert_box(doc, [0, 0, 15, 40, 20, 25])
    return doc


@case()
def plane_on_face_sketch():
    doc = block_doc(40, 20, 10)
    top = face_refs(only_part(doc), normal_is([0, 0, 1]), expect=1)[0]
    pl = add(doc, "plane", type="auto", refs=[{"k": "face", "ref": top}], offset=5.0)
    sid = circle_sketch(doc, 20, 10, 5, {"kind": "plane", "fid": pl["id"]})
    add(doc, "extrude", regions=region_refs(doc, sid), depth=5.0, op="new")
    assert len(doc.state.parts) == 2
    return doc


@case()
def import_step_block():
    src = block_doc(40, 20, 10)
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "b.step")
    src.export(path)
    with open(path, "rb") as fh:
        data = base64.b64encode(fh.read()).decode()
    doc = Document()
    add(doc, "import", data=data, filename="b.step")
    approx_rel(vol(doc), 8000)
    return doc


# =========================================================================== the tests

def test_every_runner_is_characterised():
    used = {f["type"] for fn, _ in CASES.values() for f in _built(fn.__name__).features}
    # variable_studio_ref reads another tab, so it is characterised in test_variable_studio_ref.py
    # delete_part only removes parts, so it has no result to fingerprint: tests/test_delete_part.py
    # context restores a stored capture, so it is characterised in test_context_feature.py
    # derived reads another tab's package, so it is characterised in test_derived.py
    # Python features (py:*) are covered by their plugins' own tests, e.g. tests/test_gears_plugin.py
    left = {t for t in F.RUNNERS if not t.startswith("py:")} - used - {"variable_studio_ref", "delete_part",
                                                                     "mate_connector", "context", "derived"}
    assert left == set(), sorted(left)


@functools.lru_cache(maxsize=None)
def _built(name):
    return CASES[name][0]()


@pytest.mark.parametrize("name", sorted(CASES))
def test_feature_golden(name):
    doc = _built(name)
    assert not doc.errors, doc.errors
    for p in doc.state.parts.values():
        assert K.is_valid(p.shape), p.id
    assert_golden(f"features/{name}", state_fingerprint(doc), CASES[name][1])


@pytest.mark.parametrize("name", sorted(CASES))
def test_save_load_roundtrip(name, tmp_path):
    doc = _built(name)
    path = str(tmp_path / f"{name}.fshape")
    doc.save(path)
    back = Document.load(path)
    assert not back.errors, back.errors
    assert back.features == doc.features
    assert list(back.state.parts) == list(doc.state.parts)
    for pid, p in doc.state.parts.items():
        a, b = shape_fingerprint(p.shape), shape_fingerprint(back.state.parts[pid].shape)
        for k in ("solids", "shells", "faces", "edges", "vertices", "valid"):
            assert a[k] == b[k], (pid, k, a[k], b[k])
        for k in ("volume", "area"):
            approx_rel(b[k], a[k], rel=1e-9, abs_=1e-9)
        assert np.allclose(a["bbox"], b["bbox"], atol=1e-9)
