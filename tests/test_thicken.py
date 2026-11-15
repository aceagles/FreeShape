"""I3.1: Thicken a surface or a face into a solid (kernel K.thicken, runner run_thicken). No window is
involved: the feature has no dialog yet (I3.2), so these tests drive the Document directly."""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from conftest import TOP, add, block_doc, face_refs, normal_is, rect_lines, sketch_feature, try_add  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.document import PartStudioDoc  # noqa: E402


def curve_refs(sid, *entities):
    return [{"kind": "sketch_chain", "sketch": sid, "entity": e} for e in entities]


def lines_of(doc, sid):
    sk = doc.state.sketches[sid].sketch
    return [i for i, e in sk.entities.items() if e["type"] == "line"]


def surfaces(doc):
    return [p for p in doc.state.parts.values() if K.is_surface(p.shape)]


def solids_of(doc):
    return [p for p in doc.state.parts.values() if not K.is_surface(p.shape)]


def flat_doc(width=20.0, height=10.0):
    """One surface part: a width x height face in the XZ plane (a line extruded `height`)."""
    doc = PartStudioDoc()

    def build(s):
        a, b = s.add_point(0, 0), s.add_point(width, 0)
        s.add_line(a, b)
    sid = sketch_feature(doc, build, TOP)
    add(doc, "extrude", creation="surface", curves=curve_refs(sid, lines_of(doc, sid)[0]), depth=height, op="new")
    return doc


def arc_doc(r=10.0, length=20.0):
    """One surface part: a half cylinder of radius r (a half-circle arc extruded `length`)."""
    doc = PartStudioDoc()

    def build(s):
        c = s.add_point(0, 0)
        s.add_arc(c, s.add_point(r, 0), s.add_point(-r, 0))
    sid = sketch_feature(doc, build, TOP)
    sk = doc.state.sketches[sid].sketch
    arc = next(i for i, e in sk.entities.items() if e["type"] == "arc")
    add(doc, "extrude", creation="surface", curves=curve_refs(sid, arc), depth=length, op="new")
    return doc


def thicken(doc, **kw):
    (part,) = surfaces(doc)[:1]
    kw.setdefault("parts", [part.id])
    return try_add(doc, "thicken", **kw)


def only_solid(doc):
    (p,) = solids_of(doc)
    return p


def test_planar_face_makes_a_box():
    doc = flat_doc()
    f, err = thicken(doc, thickness1=3.0)
    assert not err, err
    p = only_solid(doc)
    assert K.is_valid(p.shape) and len(K.faces(p.shape)) == 6
    assert K.volume(p.shape) == pytest.approx(600.0, rel=1e-6)
    assert all(K.face_info(x)["type"] == int(K.GeomAbs_Plane) for x in K.faces(p.shape))


def test_second_thickness_puts_the_original_face_inside():
    doc = flat_doc()
    f, err = thicken(doc, thickness1=3.0, thickness2=1.0)
    assert not err, err
    p = only_solid(doc)
    assert K.volume(p.shape) == pytest.approx(800.0, rel=1e-6)
    lo, hi = K.bbox(p.shape)
    assert sorted([abs(lo[1]), abs(hi[1])]) == pytest.approx([1.0, 3.0])   # the face is the plane y = 0


def test_mid_plane_is_symmetric():
    doc = flat_doc()
    f, err = thicken(doc, thickness1=4.0, mid_plane=True)
    assert not err, err
    p = only_solid(doc)
    assert K.volume(p.shape) == pytest.approx(800.0, rel=1e-6)
    lo, hi = K.bbox(p.shape)
    assert lo[1] == pytest.approx(-2.0) and hi[1] == pytest.approx(2.0)


def test_flip_goes_to_the_other_side():
    a, b = flat_doc(), flat_doc()
    thicken(a, thickness1=3.0)
    thicken(b, thickness1=3.0, flip=True)
    la, ha = K.bbox(only_solid(a).shape)
    lb, hb = K.bbox(only_solid(b).shape)
    assert (la[1], ha[1]) == pytest.approx((-hb[1], -lb[1]))
    assert abs(la[1] + ha[1]) == pytest.approx(3.0)


def _outward_sign(doc):
    """+1 when a positive thickness1 grows the half cylinder away from its axis."""
    (part,) = surfaces(doc)
    sol = K.thicken(part.shape, 1.0, 0.0)
    lo, hi = K.bbox(sol)
    return 1 if max(abs(lo[1]), abs(hi[1])) > 10.5 else -1


def test_half_cylinder_outward_volume():
    doc = arc_doc()
    flip = _outward_sign(doc) < 0
    f, err = thicken(doc, thickness1=2.0, flip=flip)
    assert not err, err
    p = only_solid(doc)
    assert K.is_valid(p.shape)
    assert K.volume(p.shape) == pytest.approx(math.pi / 2 * (12 ** 2 - 10 ** 2) * 20, rel=1e-4)


def test_half_cylinder_inward_too_far_is_a_named_error_and_the_document_rebuilds():
    doc = arc_doc()
    flip = _outward_sign(doc) > 0       # inward
    f, err = thicken(doc, thickness1=12.0, flip=flip)
    assert err and "tightest curve" in err, err
    assert not solids_of(doc) and len(surfaces(doc)) == 1      # nothing was deleted or made
    doc.rebuild()
    assert doc.errors.get(f["id"]) == err
    f2 = dict(doc.feature(f["id"]))
    f2["params"] = dict(f2["params"], thickness1=3.0)
    doc.replace_feature(f2)
    assert not doc.errors, doc.errors
    assert K.volume(only_solid(doc).shape) == pytest.approx(math.pi / 2 * (10 ** 2 - 7 ** 2) * 20, rel=1e-4)


def l_doc():
    """Two faces sharing an edge, as one surface part: an L with 10 x 10 legs and 10 deep (a two-line
    chain extruded 10)."""
    doc = PartStudioDoc()

    def build(s):
        p = [s.add_point(0, 0), s.add_point(10, 0), s.add_point(10, 10)]
        s.add_line(p[0], p[1])
        s.add_line(p[1], p[2])
    sid = sketch_feature(doc, build, TOP)
    add(doc, "extrude", creation="surface", curves=curve_refs(sid, lines_of(doc, sid)[0]), depth=10, op="new")
    return doc


def test_two_face_l_makes_one_mitred_solid():
    doc = l_doc()
    (part,) = surfaces(doc)
    assert len(K.faces(part.shape)) == 2
    f, err = thicken(doc, thickness1=1.0)
    assert not err, err
    p = only_solid(doc)
    assert K.is_valid(p.shape) and len(K.solids(p.shape)) == 1
    assert len(K.faces(p.shape)) == 8
    # the outer leg length is 10 + 1 on the outside of the corner or 10 - 1 inside it: a mitre
    v = K.volume(p.shape)
    assert v == pytest.approx(10 * (20 * 1 + 1 * 1 * (1 if v > 200 else -1)), rel=1e-6)


def test_face_of_a_block_with_add_grows_the_part_by_area_times_thickness():
    doc = block_doc(40, 20, 10)
    part = next(iter(doc.state.parts.values()))
    v0 = K.volume(part.shape)
    top = face_refs(part, normal_is([0, 0, 1]), expect=1)
    f, err = try_add(doc, "thicken", faces=top, thickness1=3.0, op="add")
    assert not err, err
    assert len(doc.state.parts) == 1
    p = next(iter(doc.state.parts.values()))
    assert K.volume(p.shape) == pytest.approx(v0 + 40 * 20 * 3, rel=1e-6)
    assert p.id == part.id


def test_a_face_picked_from_a_solid_is_never_deleted_and_add_is_the_default_op():
    doc = block_doc(40, 20, 10)
    part = next(iter(doc.state.parts.values()))
    top = face_refs(part, normal_is([0, 0, 1]), expect=1)
    f = doc.make_feature("thicken")
    f["params"].update(faces=top, thickness1=3.0)
    f["params"].pop("op")
    doc.add_feature(f)
    assert not doc.errors, doc.errors
    assert part.id in doc.state.parts and len(doc.state.parts) == 1
    assert K.volume(doc.state.parts[part.id].shape) > 40 * 20 * 10


def test_keep_tools_off_deletes_the_surface_part_and_on_keeps_it():
    doc = flat_doc()
    n = len(doc.state.parts)
    thicken(doc, thickness1=3.0)
    assert len(doc.state.parts) == n                       # one surface gone, one solid made
    assert not surfaces(doc) and len(solids_of(doc)) == 1
    doc = flat_doc()
    thicken(doc, thickness1=3.0, keep_tools=True)
    assert len(surfaces(doc)) == 1 and len(solids_of(doc)) == 1
    assert len(doc.state.parts) == n + 1


def _top_name(doc):
    p = only_solid(doc)
    named = doc.state.named(p.id)
    far = max(K.faces(p.shape), key=lambda x: K.face_info(x)["c"][1])
    return named.name_of(far), K.face_info(far)


@pytest.mark.parametrize("make", [flat_doc, arc_doc])
def test_face_names_survive_a_change_of_thickness(make):
    doc = make()
    f, err = thicken(doc, thickness1=3.0, thickness2=1.0) if make is flat_doc else thicken(doc, thickness1=2.0)
    assert not err, err
    p = only_solid(doc)
    named = doc.state.named(p.id)
    assert named is not None and not named.unnamed()
    before = {named.name_of(x): K.face_info(x)["c"] for x in K.faces(p.shape)}
    assert len(before) == len(K.faces(p.shape)) and all(before)
    f2 = dict(doc.feature(f["id"]))
    f2["params"] = dict(f2["params"], thickness1=f2["params"]["thickness1"] + 1.5)
    doc.replace_feature(f2)
    assert not doc.errors, doc.errors
    p2 = only_solid(doc)
    named2 = doc.state.named(p2.id)
    after = {named2.name_of(x): K.face_info(x)["c"] for x in K.faces(p2.shape)}
    assert set(after) == set(before)
    moved = [k for k in before if np.linalg.norm(np.subtract(before[k], after[k])) > 1e-6]
    assert 1 <= len(moved) < len(before)     # the far wall moved; the original face and the side walls did not
