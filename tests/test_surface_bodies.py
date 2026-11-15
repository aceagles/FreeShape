"""I2.2: surface bodies (a part whose shape has faces and no solid): kernel helpers, merge rules and guards.

No feature makes a surface yet, so the shapes here are built by hand."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))

from OCP.BRep import BRep_Builder  # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: E402
from OCP.TopoDS import TopoDS_Shell  # noqa: E402
from OCP.gp import gp_Pnt  # noqa: E402

from conftest import block_doc, face_refs  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402


def box(x=0.0, y=0.0, z=0.0, w=10.0, d=10.0, h=10.0):
    return BRepPrimAPI_MakeBox(gp_Pnt(x, y, z), w, d, h).Shape()


def shell_of(*faces):
    b = BRep_Builder()
    sh = TopoDS_Shell()
    b.MakeShell(sh)
    for f in faces:
        b.Add(sh, f)
    return sh


def top_faces(shape):
    """Two faces of a box that share an edge, the top (z max) and the face at y = 0."""
    fs = K.faces(shape)
    top = max(fs, key=lambda f: K.face_info(f)["c"][2])
    front = next(f for f in fs if abs(K.face_info(f).get("n", [0, 0, 0])[1] + 1) < 1e-9)
    return top, front


def n_faces(shape):
    return len(K.faces(shape))


def surface_state(*shapes):
    st = F.ModelState()
    for i, s in enumerate(shapes):
        st.new_part(s, f"x{i}", surface=True)
    return st


def test_bodies_and_is_surface():
    top, front = top_faces(box(0, 0, 0))
    shell = shell_of(top, front)
    loose = top_faces(box(100, 0, 0))[0]
    solid = box(50, 0, 0)
    comp = K.compound([solid, shell, loose])
    bs = K.bodies(comp)
    assert len(bs) == 3
    assert len(K.solids(bs[0])) == 1
    assert [K.is_surface(b) for b in bs] == [False, True, True]
    assert n_faces(bs[1]) == 2 and n_faces(bs[2]) == 1
    assert K.is_surface(shell) and not K.is_surface(solid)
    assert not K.is_surface(K.compound([solid, shell]))          # holds a solid: not a surface body


@pytest.mark.parametrize("at", [(0, 0, 0), (500, -300, 70), (-40, 20, -9)])
def test_open_shell_volume_and_centroid(at):
    top, front = top_faces(box(*at))
    shell = shell_of(top, front)
    assert K.volume(shell) == 0.0
    mp = K.mass_props(shell)
    assert mp["volume"] == 0.0
    assert mp["area"] == pytest.approx(200.0, rel=1e-9)
    x, y, z = at
    want = np.array([x + 5, y + 5, z + 10]) * 0.5 + np.array([x + 5, y, z + 5]) * 0.5
    assert np.allclose(mp["centroid"], want, atol=1e-9)


def test_volume_of_mixed_shape_counts_solids_only():
    top, _ = top_faces(box(0, 0, 0))
    mixed = K.compound([box(30, 0, 0), top])
    assert K.volume(mixed) == pytest.approx(1000.0, rel=1e-9)
    assert K.mass_props(mixed)["volume"] == pytest.approx(1000.0, rel=1e-9)


def test_as_surface_sews_connected_faces():
    top, front = top_faces(box(0, 0, 0))
    far = top_faces(box(100, 0, 0))[0]
    out = K.as_surface([top, front, far])
    assert sorted(n_faces(s) for s in out) == [1, 2]


def test_new_surface_parts_are_numbered_apart():
    st = F.ModelState()
    top, front = top_faces(box())
    F.apply_body(st, shell_of(top), "new", "a")
    F.apply_body(st, shell_of(top_faces(box(50, 0, 0))[0]), "new", "b")
    assert [p.name for p in st.parts.values()] == ["Surface 1", "Surface 2"]
    F.apply_body(st, box(100, 0, 0), "new", "c")
    assert list(st.parts.values())[-1].name == "Part 1"
    assert st.part_counter == 1
    F.apply_body(st, shell_of(top_faces(box(200, 0, 0))[0]), "new", "d")
    assert list(st.parts.values())[-1].name == "Surface 3"


def test_new_splits_a_compound_into_one_part_each():
    st = F.ModelState()
    a, b = top_faces(box())[0], top_faces(box(50, 0, 0))[0]
    F.apply_body(st, K.compound([a, b]), "new", "a")
    assert [p.name for p in st.parts.values()] == ["Surface 1", "Surface 2"]


def test_add_sews_surfaces_that_share_an_edge():
    top, front = top_faces(box())
    st = surface_state(shell_of(top))
    pid = next(iter(st.parts))
    F.apply_body(st, shell_of(front), "add", "m")
    assert list(st.parts) == [pid]
    shape = st.parts[pid].shape
    assert n_faces(shape) == 2 and len(K.edges(shape)) == 7
    assert K.is_surface(shape) and st.parts[pid].name == "Surface 1"


def test_add_makes_a_new_part_when_nothing_touches():
    top, _ = top_faces(box())
    st = surface_state(shell_of(top))
    F.apply_body(st, shell_of(top_faces(box(80, 0, 0))[0]), "add", "m")
    assert [p.name for p in st.parts.values()] == ["Surface 1", "Surface 2"]


def test_add_joins_two_surface_parts_the_new_one_bridges():
    b = box()
    fs = K.faces(b)
    st = surface_state(shell_of(fs[0]), shell_of(fs[1]))
    ids = list(st.parts)
    bridge = next(f for f in fs[2:] if len(K.as_surface([fs[0], f])) == 1 and len(K.as_surface([fs[1], f])) == 1)
    F.apply_body(st, shell_of(bridge), "add", "m")
    assert list(st.parts) == [ids[0]]
    assert n_faces(st.parts[ids[0]].shape) == 3


@pytest.mark.parametrize("op", ["remove", "intersect"])
def test_remove_and_intersect_are_not_available_for_surfaces(op):
    top, _ = top_faces(box())
    st = surface_state(shell_of(top))
    before = dict(st.parts)
    with pytest.raises(K.KernelError, match="Remove and Intersect are not available for surfaces"):
        F.apply_body(st, shell_of(top_faces(box(0, 0, 0))[1]), op, "m")
    assert st.parts == before


def test_solid_add_leaves_a_surface_part_alone():
    top, _ = top_faces(box(0, 0, 0))
    st = F.ModelState()
    F.apply_body(st, box(0, 0, 0), "new", "a")
    F.apply_body(st, shell_of(top), "new", "s")       # a surface lying on the block's top face
    surf = st.parts["s/1"]
    F.apply_body(st, box(5, 0, 0), "add", "b")        # overlaps the block and touches the surface
    assert surf is st.parts["s/1"] or surf.shape.IsSame(st.parts["s/1"].shape)
    solid_parts = [p for p in st.parts.values() if K.solids(p.shape)]
    assert len(solid_parts) == 1
    assert K.volume(solid_parts[0].shape) == pytest.approx(1500.0, rel=1e-9)
    assert n_faces(st.parts["s/1"].shape) == 1 and K.is_surface(st.parts["s/1"].shape)


def test_a_solid_with_a_scoped_surface_target_does_not_merge_into_it():
    top, _ = top_faces(box())
    st = surface_state(shell_of(top))
    F.apply_body(st, box(2, 0, 0), "add", "b", scope=["x0/1"])
    assert K.is_surface(st.parts["x0/1"].shape) and n_faces(st.parts["x0/1"].shape) == 1
    assert len(st.parts) == 2


def _surface_and_block_state():
    st = F.ModelState()
    b = box()
    F.apply_body(st, shell_of(*K.faces(b)[:2]), "new", "s")
    return st, st.parts["s/1"]


def _run(st, ftype, **params):
    return F.RUNNERS[ftype](st, {"id": "g", "type": ftype, "params": params})


@pytest.mark.parametrize("ftype,kind,params", [
    ("fillet", "edge", {"radius": 1.0}),
    ("chamfer", "edge", {"distance": 1.0}),
    ("shell", "face", {"thickness": 1.0}),
    ("draft", "face", {"angle": 3.0}),
])
def test_edge_and_face_features_refuse_a_surface(ftype, kind, params):
    st, part = _surface_and_block_state()
    sub = K.edges(part.shape)[0] if kind == "edge" else K.faces(part.shape)[0]
    ref = F.make_ref(part.id, sub, kind, part.shape)
    before = dict(st.parts)
    with pytest.raises(K.KernelError, match=r"Surface 1 is a surface - this needs a solid part up front"):
        _run(st, ftype, **{("edges" if kind == "edge" else "faces"): [ref]}, **params)
    assert st.parts == before


@pytest.mark.parametrize("op", ["union", "subtract", "intersect"])
def test_boolean_refuses_a_surface(op):
    st, part = _surface_and_block_state()
    F.apply_body(st, box(100, 0, 0), "new", "c")
    before = dict(st.parts)
    with pytest.raises(K.KernelError, match="is a surface - this needs a solid part up front"):
        _run(st, "boolean", op=op, tools=["c/1"], targets=[part.id])
    assert st.parts == before


def test_hole_refuses_a_studio_of_surfaces():
    st, _ = _surface_and_block_state()
    before = dict(st.parts)
    with pytest.raises(K.KernelError, match="is a surface - this needs a solid part up front"):
        _run(st, "hole", sketch=None)
    assert st.parts == before


def test_offset_surface_result_keeps_its_part_name():
    doc = block_doc(40, 20, 10)
    block = next(iter(doc.state.parts.values()))
    top = face_refs(block, lambda i: i.get("n") and i["n"][2] > 0.99)
    f = doc.make_feature("offset_surface")
    f["params"].update(faces=top, distance=4.0)
    st, err = doc.preview(f, doc.insert_index())
    assert err is None, err
    names = [p.name for p in st.parts.values()]
    assert names == ["Part 1", "Part 2"] and st.surface_counter == 0


def test_surface_part_is_not_an_interference_body():
    from freeshape import interference

    class Doc:
        hidden_parts = set()

    d = Doc()
    d.state, _ = _surface_and_block_state()
    F.apply_body(d.state, box(100, 0, 0), "new", "c")
    assert [b.id for b in interference.studio_bodies(d)] == ["c/1"]
