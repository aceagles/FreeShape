"""I1.2: Viewport.pick_all lists everything under the cursor, front to back, including what other geometry hides.
A 20 x 10 x 5 box in an offscreen viewport, looked at square on from above (+z): the top face is the front, the
bottom face the back."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402


@pytest.fixture
def vp():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from freeshape.ui.camera import basis
    from freeshape.ui.viewport import Viewport
    doc = Document()
    s = Sketch()
    p = [s.add_point(-10, -5), s.add_point(10, -5), s.add_point(10, 5), s.add_point(-10, 5)]
    for a, b in zip(p, p[1:] + p[:1]):
        s.add_line(a, b)
    s.solve()
    f = doc.make_feature("sketch")
    f["params"]["sketch"] = s.to_dict()
    doc.add_feature(f)
    sr = doc.state.sketches[f["id"]]
    e = doc.make_feature("extrude")
    e["params"].update(regions=[F.region_ref(sr, f["id"], sr.regions[0])], depth=5, op="new")
    doc.add_feature(e)
    part = next(iter(doc.state.parts.values()))
    v = Viewport()
    mesh = K.tessellate(part.shape)
    v.parts = [(part.id, mesh, None)]
    v._part_boxes = {part.id: (mesh.verts.min(0), mesh.verts.max(0))}
    v.cam.set_state((np.array([0.0, 0, 2.5]), basis((0, 0, 1), (0, 1, 0)), 0.1))
    v.select_filter = v.user_filter = None
    return v


def at(vp, *xyz):
    sx, sy, _z = vp.cam.to_screen(np.array(xyz, float))
    return float(sx), float(sy)


def test_face_middle_lists_the_front_face_then_the_back_face(vp):
    x, y = at(vp, 0, 0, 5)
    hits = vp.pick_all(x, y, kinds={"face"})
    assert [h.kind for h in hits] == ["face", "face"]
    assert hits[0] == vp.pick(x, y, kinds={"face"})
    assert hits[0] != hits[1]


def test_front_edge_is_listed_first_and_the_back_edge_behind_it_second(vp):
    x, y = at(vp, 0, -5, 5)
    hits = vp.pick_all(x, y, kinds={"edge"})
    assert len(hits) == 2 and all(h.kind == "edge" for h in hits)
    mesh = vp.parts[0][1]
    assert [float(mesh.edge_polys[h.key[1]][0][2]) for h in hits] == [5.0, 0.0]     # z of the edge: top, then bottom


def test_first_element_equals_pick_at_five_positions_and_empty_space_is_empty(vp):
    spots = {"face middle": (0, 2, 5), "edge": (3, -4.9, 5), "corner": (9.9, -4.9, 5), "empty": (40, 40, 5),
             "hidden back corner": (-9.9, 4.9, 0)}
    for kinds in (None, {"face", "edge", "vertex"}):
        for name, w in spots.items():
            x, y = at(vp, *w)
            one, many = vp.pick(x, y, kinds), vp.pick_all(x, y, kinds)
            if name == "empty":
                assert one is None and many == []
            else:
                assert one is not None, name
                assert many[0] == one, (name, kinds)


def test_corner_with_vertices_allowed_lists_the_front_vertex_first(vp):
    x, y = at(vp, 10, -5, 5)
    hits = vp.pick_all(x, y, kinds={"vertex"})
    mesh = vp.parts[0][1]
    assert [float(mesh.vertices[h.key[1]][2]) for h in hits] == [5.0, 0.0]


def test_section_removing_the_front_half_drops_the_removed_face(vp):
    x, y = at(vp, 0, 0, 5)
    both = vp.pick_all(x, y, kinds={"face"})
    top = vp.pick(x, y, kinds={"face"})
    vp.set_section(K.Plane(np.array([0.0, 0, 2.5]), np.array([1.0, 0, 0]), np.array([0.0, 0, 1])))
    kept = vp.pick_all(x, y, kinds={"face"})
    assert top in both and top not in kept and len(kept) == 1


def test_section_drops_the_removed_edge_too(vp):
    x, y = at(vp, 0, -5, 5)
    front, back = vp.pick_all(x, y, kinds={"edge"})
    vp.set_section(K.Plane(np.array([0.0, 0, 2.5]), np.array([1.0, 0, 0]), np.array([0.0, 0, 1])))
    assert vp.pick_all(x, y, kinds={"edge"}) == [back]


def test_edge_only_returns_no_face_and_nothing_twice(vp):
    for w in ((0, -5, 5), (10, -5, 5), (0, 0, 5)):
        for kinds in ({"edge"}, None, {"face", "edge", "vertex"}):
            hits = vp.pick_all(*at(vp, *w), kinds=kinds)
            assert len(hits) == len(set(hits))
            if kinds == {"edge"}:
                assert all(h.kind == "edge" for h in hits)


def test_the_selection_filter_applies_when_kinds_is_none(vp):
    vp.user_filter = {"edge"}
    assert {h.kind for h in vp.pick_all(*at(vp, 0, -5, 5))} == {"edge"}
