"""Loft guides (I7.3): the network route, a Gordon surface through the profiles and the guides
(GeomFill_Gordon, bound only in the OCP 8.0 build; spike I7.1, scripts/spikes/op2_loft.result.txt).
Guided cases are marked needs_occt8 and skip on 7.9; on 7.9 a guided loft is refused by name.
"""
import math
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from OCP.BRepAlgoAPI import BRepAlgoAPI_Section  # noqa: E402
from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace,  # noqa: E402
                                BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeVertex)
from OCP.BRepExtrema import BRepExtrema_DistShapeShape  # noqa: E402
from OCP.GC import GC_MakeArcOfCircle  # noqa: E402
from OCP.gp import gp_Dir, gp_Pln, gp_Pnt  # noqa: E402

from conftest import FRONT, OCCT_MAJOR, TOP, add, only_part, region_refs, try_add  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import migrations as M  # noqa: E402
from freeshape.document import PartStudioDoc  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402
from test_naming_core_features import add_sketch, assert_all_named  # noqa: E402
from test_naming_sweep_loft_import import add_sketch_on  # noqa: E402

needs_occt8 = pytest.mark.needs_occt8
REFUSED_79 = "Loft guides need OpenCascade 8.0 or newer"


def _decision():
    path = os.path.join(os.path.dirname(__file__), "..", "scripts", "spikes", "op2_loft.result.txt")
    lines = [ln for ln in open(path).read().splitlines() if ln.startswith("DECISION")]
    return dict(t.split("=", 1) for t in lines[-1].split()[2:])     # the last build in the file: 8.0


ONE_GUIDE = _decision()["one_guide"]


# --------------------------------------------------------------------------- kernel shapes

def _square(z, s=20.0):
    p = BRepBuilderAPI_MakePolygon()
    for x, y in ((-s / 2, -s / 2), (s / 2, -s / 2), (s / 2, s / 2), (-s / 2, s / 2)):
        p.Add(gp_Pnt(x, y, z))
    p.Close()
    return BRepBuilderAPI_MakeFace(p.Wire(), True).Face()


def _arc(a, b, c):
    return K.make_wire([BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(gp_Pnt(*a), gp_Pnt(*b), gp_Pnt(*c)).Value()).Edge()])


def _guides(mid=14.0, x0=10.0):
    return [_arc((x0, 0, 0), (mid, 0, 20), (10, 0, 40)), _arc((-10, 0, 0), (-mid, 0, 20), (-10, 0, 40))]


def _shell(body):
    return K.compound(K.faces(body))


def _guide_distance(body, wire, n=50):
    """The largest distance from n points along the guide wire to the loft's faces."""
    from OCP.BRepAdaptor import BRepAdaptor_CompCurve
    ad = BRepAdaptor_CompCurve(wire)
    a, b = ad.FirstParameter(), ad.LastParameter()
    shell, worst = _shell(body), 0.0
    for k in range(n):
        v = BRepBuilderAPI_MakeVertex(ad.Value(a + (b - a) * k / (n - 1))).Vertex()
        d = BRepExtrema_DistShapeShape(v, shell)
        assert d.IsDone()
        worst = max(worst, d.Value())
    return worst


def _flat_z(f):
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane
    ad = BRepAdaptor_Surface(f)
    return ad.GetType() == GeomAbs_Plane and abs(abs(ad.Plane().Axis().Direction().Z()) - 1) < 1e-9


def _width_at(body, z):
    sec = BRepAlgoAPI_Section(body, gp_Pln(gp_Pnt(0, 0, z), gp_Dir(0, 0, 1)))
    sec.Build()
    lo, hi = K.bbox(sec.Shape())
    return float(hi[0] - lo[0])


@needs_occt8
def test_two_bowed_guides_pass_through_both_guides_with_six_faces():
    guides = _guides()
    body = K.loft_network([_square(0), _square(40)], guides)
    assert len(K.solids(body)) == 1 and K.is_valid(body)
    assert len(K.faces(body)) == 6
    for g in guides:
        assert _guide_distance(body, g) < 1e-4
    assert abs(_width_at(body, 20.0) - 28.0) < 1e-3
    caps = [f for f in K.faces(body) if _flat_z(f)]
    assert len(caps) == 2 and all(abs(K.face_info(c)["area"] - 400) < 1e-6 for c in caps)


@needs_occt8
def test_a_guide_off_the_profile_names_the_guide_and_the_profile():
    with pytest.raises(K.KernelError, match="Guide 1 does not touch profile 1"):
        K.loft_network([_square(0), _square(40)], _guides(x0=10.5))


@needs_occt8
def test_one_guide_follows_the_spike_decision():
    g = _guides()[0]
    if ONE_GUIDE in ("duplicate", "isocurve"):
        body = K.loft_network([_square(0), _square(40)], [g])
        assert len(K.solids(body)) == 1 and K.is_valid(body) and len(K.faces(body)) == 6
        assert _guide_distance(body, g) < 1e-4
        assert abs(_width_at(body, 20.0) - 24.0) < 1e-3       # one side bows out by 4, the other stays
    else:
        with pytest.raises(K.KernelError, match="Add a second guide"):
            K.loft_network([_square(0), _square(40)], [g])


@needs_occt8
def test_guides_through_profile_corners_and_circle_profiles():
    corner = [_arc((10, 10, 0), (13, 13, 20), (10, 10, 40)), _arc((-10, -10, 0), (-13, -13, 20), (-10, -10, 40))]
    body = K.loft_network([_square(0), _square(40)], corner)
    assert K.is_valid(body) and len(K.faces(body)) == 6
    assert max(_guide_distance(body, g) for g in corner) < 1e-4
    from test_loft_holes import _disc
    body = K.loft_network([_disc(0, 10), _disc(40, 10)], _guides())
    assert K.is_valid(body) and len(K.faces(body)) == 3
    assert max(_guide_distance(body, g) for g in _guides()) < 1e-4


@needs_occt8
def test_a_guide_with_a_sharp_corner_is_refused():
    sharp = K.make_wire([BRepBuilderAPI_MakeEdge(gp_Pnt(10, 0, 0), gp_Pnt(14, 0, 20)).Edge(),
                         BRepBuilderAPI_MakeEdge(gp_Pnt(14, 0, 20), gp_Pnt(10, 0, 40)).Edge()])
    with pytest.raises(K.KernelError, match="Guide 1 has a sharp corner"):
        K.loft_network([_square(0), _square(40)], [sharp, _guides()[1]])


@needs_occt8
def test_holes_are_lofted_unguided():
    from test_loft_holes import _disc
    body = K.loft_network([_disc(0, 10, [(0, 0, 3)]), _disc(40, 10, [(0, 0, 3)])], _guides())
    assert len(K.solids(body)) == 1 and K.is_valid(body) and len(K.faces(body)) == 4


# --------------------------------------------------------------------------- feature

def _arc3(s, p, q, r):
    """A sketch arc from p through q to r (sketch arcs run anticlockwise from p1 to p2)."""
    (ax, ay), (bx, by), (cx, cy) = p, q, r
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    ang = [math.atan2(y - uy, x - ux) for x, y in (p, q, r)]
    ccw = (ang[1] - ang[0]) % (2 * math.pi) < (ang[2] - ang[0]) % (2 * math.pi)
    a, b = (p, r) if ccw else (r, p)
    return s.add_arc(s.add_point(ux, uy), s.add_point(*a), s.add_point(*b))


def _guide_sketch(mid=14.0, x0=10.0, sharp=False):
    """The two guides on the Front plane (sketch x, y = world x, z)."""
    s = Sketch()
    if sharp:
        m = s.add_point(mid, 20)
        g1 = s.add_line(s.add_point(x0, 0), m)
        s.add_line(m, s.add_point(10, 40))
    else:
        g1 = _arc3(s, (x0, 0), (mid, 20), (10, 40))
    g2 = _arc3(s, (-10, 0), (-mid, 20), (-10, 40))
    return s, g1, g2


def _add_sketch(doc, s):
    s.solve()
    f = doc.make_feature("sketch")
    f["params"].update(plane=FRONT, sketch=s.to_dict())
    doc.add_feature(f)
    assert not doc.errors.get(f["id"]), doc.errors
    return f["id"]


def _doc(guide=None, one=False, sketch_only=False, **params):
    doc = PartStudioDoc()
    a, _ = add_sketch(doc, -10, -10, 20, 20)
    pl = add(doc, "plane", type="offset", refs=[], ref=TOP, offset=40.0)
    b, _ = add_sketch_on(doc, {"kind": "plane", "fid": pl["id"]}, -10, -10, 20, 20)
    gid = None
    if guide is not None:
        s, g1, g2 = _guide_sketch(**guide)
        gid = _add_sketch(doc, s)
        refs = [[{"kind": "sketch_chain", "sketch": gid, "entity": e}] for e in ((g1,) if one else (g1, g2))]
        if not sketch_only:
            params["guides"] = refs
    profiles = [{"kind": "region", "ref": region_refs(doc, a)[0]}, {"kind": "region", "ref": region_refs(doc, b)[0]}]
    f, err = try_add(doc, "loft", op="new", profiles=profiles, **params)
    return doc, gid, f, err


def _names(doc):
    part = only_part(doc)
    named = assert_all_named(doc.state, part)
    return {named.name_of(f) for f in K.faces(part.shape)}


def test_the_file_version_holds_the_guides_key():
    assert M.latest_version("document") >= 5


def test_a_guided_loft_is_refused_on_79_and_builds_on_80():
    doc, _, f, err = _doc(guide={})
    if OCCT_MAJOR < 8:
        assert err == REFUSED_79
    else:
        assert err is None
        assert len(K.faces(only_part(doc).shape)) == 6


def test_kernel_refuses_guides_on_79_without_crashing():
    if OCCT_MAJOR >= 8:
        pytest.skip("8.0 binds GeomFill_Gordon")
    with pytest.raises(K.KernelError, match=REFUSED_79):
        K.loft_network([_square(0), _square(40)], _guides())
    with pytest.raises(K.KernelError, match=REFUSED_79):
        K.network_surface([], [])


def test_empty_guides_take_the_legacy_route():
    plain, _, _, err0 = _doc()
    empty, _, _, err1 = _doc(guides=[])
    assert err0 is None and err1 is None
    a, b = only_part(plain).shape, only_part(empty).shape
    assert K.volume(a) == K.volume(b)
    assert len(K.faces(a)) == len(K.faces(b))
    assert _names(plain) == _names(empty)


@needs_occt8
def test_legacy_skinning_and_network_routes_share_face_names():
    # The same features in each, so the names differ only if the route changes them.
    legacy, _, _, e0 = _doc(guide={}, sketch_only=True)
    skin, _, _, e1 = _doc(guide={}, sketch_only=True, start={"cond": "normal", "mag": 1.0})
    net, _, _, e2 = _doc(guide={})
    assert (e0, e1, e2) == (None, None, None)
    assert len(_names(net)) == 6
    assert _names(legacy) == _names(skin) == _names(net)


@needs_occt8
def test_the_plus_x_face_keeps_its_name_when_the_guide_bows_further():
    doc, gid, _, err = _doc(guide={})
    assert err is None
    part = only_part(doc)
    named = assert_all_named(doc.state, part)
    plus_x = max(K.faces(part.shape), key=lambda f: K.face_info(f)["c"][0])
    name, x0 = named.name_of(plus_x), K.face_info(plus_x)["c"][0]
    s, _g1, _g2 = _guide_sketch(mid=16.0)
    f = dict(doc.feature(gid))
    f["params"] = dict(f["params"], sketch=s.to_dict())
    doc.replace_feature(f)
    assert not doc.errors, doc.errors
    named2 = assert_all_named(doc.state, only_part(doc))
    moved = named2.shape_of(name)
    assert moved is not None
    assert K.face_info(K._as_face(moved))["c"][0] > x0 + 0.5


@needs_occt8
def test_guides_with_an_end_condition_are_refused():
    _, _, _, err = _doc(guide={}, start={"cond": "normal", "mag": 1.0})
    assert err == "Guides and end conditions cannot be combined yet"


@needs_occt8
def test_feature_refuses_a_sharp_guide_and_a_guide_off_the_profile():
    _, _, _, err = _doc(guide={"sharp": True})
    assert err and "Guide 1 has a sharp corner" in err
    _, _, _, err = _doc(guide={"x0": 10.5})
    assert err == "Guide 1 does not touch profile 1"


@needs_occt8
def test_one_guide_feature():
    doc, _, _, err = _doc(guide={}, one=True)
    if ONE_GUIDE in ("duplicate", "isocurve"):
        assert err is None and len(K.faces(only_part(doc).shape)) == 6
    else:
        assert err and "Add a second guide" in err
