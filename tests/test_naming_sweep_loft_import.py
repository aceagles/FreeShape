"""Naming wired into sweep, loft and import (I1.7): every face of the result has a name, a chosen face
keeps its name when an upstream dimension changes, and an imported STEP file is seeded the same way
every time (docs/reference/element-naming.md § 4, § 4.1, I1.7; ADR 0003).
"""
import base64
import os
import sys
import tempfile

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import naming as N  # noqa: E402
from freeshape.document import Document  # noqa: E402

from conftest import FRONT, TOP, add, only_part, region_refs, sketch_feature  # noqa: E402
from test_naming_core_features import add_sketch, assert_all_named, face_where, widen  # noqa: E402


def _centre(named, name):
    return K.face_info(K._as_face(named.shape_of(name)))["c"]


# --------------------------------------------------------------------------- sweep

def _sweep_doc(length=40.0):
    doc = Document()
    sid, ids = add_sketch(doc, 0, 0, 10, 6)

    def build(s):
        s.add_line(s.add_point(0, 0), s.add_point(0, length))
    path = sketch_feature(doc, build, FRONT)
    line = next(k for k, e in doc.state.sketches[path].sketch.entities.items() if e["type"] == "line")
    add(doc, "sweep", regions=region_refs(doc, sid), op="new",
        path=[{"kind": "sketch_chain", "sketch": path, "entity": line}])
    return doc, sid, ids


def test_sweep_names_every_face_and_survives_a_dimension_change():
    doc, sid, ids = _sweep_doc()
    part = only_part(doc)
    named = assert_all_named(doc.state, part)
    end = named.name_of(face_where(part, (1, 0, 0)))
    start = named.name_of(face_where(part, (0, 0, -1)))
    finish = named.name_of(face_where(part, (0, 0, 1)))
    assert len({end, start, finish}) == 3
    widen(doc, sid, ids["width"], 25.0)
    named2 = assert_all_named(doc.state, only_part(doc))
    assert abs(_centre(named2, end)[0] - 25.0) < 1e-6
    assert named2.shape_of(start) is not None and named2.shape_of(finish) is not None


def test_sweep_with_a_hole_names_the_tunnel_walls():
    doc = Document()
    sid = sketch_feature(doc, lambda s: (s.add_circle(s.add_point(0, 0), 8), s.add_circle(s.add_point(0, 0), 3)))
    path = sketch_feature(doc, lambda s: s.add_line(s.add_point(0, 0), s.add_point(0, 30)), FRONT)
    line = next(k for k, e in doc.state.sketches[path].sketch.entities.items() if e["type"] == "line")
    ring = [r for r in doc.state.sketches[sid].regions if K.face_info(r).get("area", 0) > 100]
    refs = [F.region_ref(doc.state, sid, r) for r in ring]
    assert len(refs) == 1
    add(doc, "sweep", regions=refs, op="new",
        path=[{"kind": "sketch_chain", "sketch": path, "entity": line}])
    part = only_part(doc)
    assert_all_named(doc.state, part)
    assert len(K.faces(part.shape)) == 4      # outer wall, tunnel wall, two ends


# --------------------------------------------------------------------------- loft

def _loft_doc(ruled=True):
    doc = Document()
    a, ids = add_sketch(doc, -10, -10, 20, 20)
    pl = add(doc, "plane", type="offset", refs=[], ref=TOP, offset=30.0)
    b, _ = add_sketch_on(doc, {"kind": "plane", "fid": pl["id"]}, -10, -10, 20, 20)
    add(doc, "loft", ruled=ruled, op="new",
        profiles=[{"kind": "region", "ref": region_refs(doc, a)[0]},
                  {"kind": "region", "ref": region_refs(doc, b)[0]}])
    return doc, a, ids


def add_sketch_on(doc, plane, x0, y0, w, h):
    from test_naming_core_features import rect
    from freeshape.sketch import Sketch
    s = Sketch()
    ids = rect(s, x0, y0, w, h)
    s.solve()
    f = doc.make_feature("sketch")
    f["params"].update(plane=plane, sketch=s.to_dict())
    doc.add_feature(f)
    assert not doc.errors.get(f["id"]), doc.errors
    return f["id"], ids


@pytest.mark.parametrize("ruled", [True, False])
def test_loft_names_every_face_and_survives_a_dimension_change(ruled):
    doc, sid, ids = _loft_doc(ruled)
    part = only_part(doc)
    named = assert_all_named(doc.state, part)
    bottom = named.name_of(face_where(part, (0, 0, -1)))
    top = named.name_of(face_where(part, (0, 0, 1)))
    side = named.name_of(face_where(part, (1, 0, 0))) if ruled else None
    assert len({bottom, top}) == 2
    widen(doc, sid, ids["width"], 30.0)
    named2 = assert_all_named(doc.state, only_part(doc))
    assert named2.shape_of(bottom) is not None and named2.shape_of(top) is not None
    assert abs(_centre(named2, top)[2] - 30.0) < 1e-6
    if side:
        assert named2.shape_of(side) is not None


# --------------------------------------------------------------------------- import

def _step_data():
    doc = Document()
    sid, _ = add_sketch(doc, 0, 0, 30, 20)
    add(doc, "extrude", regions=region_refs(doc, sid), depth=8, op="new")
    path = os.path.join(tempfile.mkdtemp(), "block.step")
    K.export_step([only_part(doc).shape], path)
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode()


def _import_names(data):
    K._STEP_CACHE.clear()        # read the file again rather than reuse the cached shapes
    doc = Document()
    add(doc, "import", data=data, filename="block.step")
    part = only_part(doc)
    named = assert_all_named(doc.state, part)
    return [(K.face_info(f)["c"], named.name_of(f)) for f in K.faces(part.shape)], named


def test_import_seeds_the_same_names_every_time():
    data = _step_data()
    first, named = _import_names(data)
    second, _ = _import_names(data)
    assert len(first) == 6
    assert [n for _, n in first] == [n for _, n in second]
    for (c1, _), (c2, _) in zip(first, second):
        assert np.allclose(c1, c2)
    assert len({n for _, n in first}) == 6
    assert all(";" in n and "IMP" in n for _, n in first)
    assert not named.unnamed()
