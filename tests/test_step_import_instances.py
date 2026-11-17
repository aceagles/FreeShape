"""Importing an assembly STEP whose parts are used many times (an assembly's screws): each geometry is read,
named, meshed and sent to the window once, and every placed copy still comes out where, and as, it did when
each copy was handled on its own."""
import base64
import math
import os
import sys
import tempfile
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform  # noqa: E402
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet  # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: E402
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec  # noqa: E402
from OCP.STEPCAFControl import STEPCAFControl_Writer  # noqa: E402
from OCP.STEPControl import STEPControl_AsIs  # noqa: E402
from OCP.TCollection import TCollection_ExtendedString  # noqa: E402
from OCP.TDataStd import TDataStd_Name  # noqa: E402
from OCP.TDocStd import TDocStd_Document  # noqa: E402
from OCP.TopLoc import TopLoc_Location  # noqa: E402
from OCP.XCAFDoc import XCAFDoc_DocumentTool  # noqa: E402

from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import naming as N  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.engine.encode import Encoder  # noqa: E402

from conftest import add  # noqa: E402

COPIES = 12          # two sub-assemblies of six


def _part():
    """A block with filleted edges: enough faces, edges and vertices for the names and the mesh to matter."""
    box = BRepPrimAPI_MakeBox(12.0, 8.0, 5.0).Shape()
    mk = BRepFilletAPI_MakeFillet(box)
    for e in K.edges(box)[:4]:
        mk.Add(1.0, e)
    return mk.Shape()


def _loc(k):
    t = gp_Trsf()
    t.SetTranslation(gp_Vec(30.0 * k, 5.0 * (k % 3), 2.0 * k))
    r = gp_Trsf()
    r.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 0.4 * k + 0.1)
    t.Multiply(r)
    return TopLoc_Location(t)


@pytest.fixture(scope="module")
def step_path():
    doc = TDocStd_Document(TCollection_ExtendedString("t"))
    st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    part = st.AddShape(_part(), False)
    TDataStd_Name.Set_s(part, TCollection_ExtendedString("Bracket"))
    top = st.NewShape()
    for s in range(2):
        sub = st.NewShape()
        for k in range(COPIES // 2):
            st.AddComponent(sub, part, _loc(k))
        st.AddComponent(top, sub, _loc(s + 7))
    st.UpdateAssemblies()
    w = STEPCAFControl_Writer()
    w.SetNameMode(True)
    w.Transfer(doc, STEPControl_AsIs)
    path = os.path.join(tempfile.mkdtemp(), "asm.step")
    w.Write(path)
    return path


def _unplaced(shape):
    return shape.Located(TopLoc_Location())


def _meshed_in_place(shape):
    """The reference: the part's geometry copied into place (no shared geometry, no location) and meshed there,
    at the deflection of the unplaced part."""
    base = _unplaced(shape)
    lo, hi = K.bbox(base)
    baked = BRepBuilderAPI_Transform(base, shape.Location().Transformation(), True).Shape()
    return K.tessellate(baked, max(np.linalg.norm(hi - lo) * 0.001, 1e-3))


def _same_place(m, ref):
    assert np.allclose(m.verts.min(0), ref.verts.min(0), atol=1e-3)
    assert np.allclose(m.verts.max(0), ref.verts.max(0), atol=1e-3)
    assert np.allclose(m.vertices, ref.vertices, atol=1e-3)


def test_the_reader_works_a_repeated_part_out_once_and_shares_its_geometry(step_path):
    items = K._import_step_with_colors(step_path)
    assert len(items) == COPIES
    base = _unplaced(items[0][0])
    assert all(_unplaced(s).IsSame(base) for s, *_ in items)          # one geometry, placed twelve times
    assert {nm for _s, nm, *_ in items} == {"Bracket"}
    centres = {tuple(np.round((np.add(*K.bbox(s))) / 2, 3)) for s, *_ in items}
    assert len(centres) == COPIES                                      # each copy where the file puts it
    fcols = [fc for *_x, fc in items]
    assert all(a is not b for a, b in zip(fcols, fcols[1:]))           # no colour map shared between parts


def test_import_names_each_geometry_once_with_the_names_a_fresh_seed_gives(step_path, monkeypatch):
    with open(step_path, "rb") as fh:
        data = base64.b64encode(fh.read()).decode()
    K._STEP_CACHE.clear()
    calls = []
    seed = N.seed
    monkeypatch.setattr(N, "seed", lambda *a, **k: calls.append(1) or seed(*a, **k))
    doc = Document()
    add(doc, "import", data=data, filename="asm.step")
    parts = list(doc.state.parts.values())
    assert len(parts) == COPIES
    assert len(calls) == 1
    fid = doc.features[-1]["id"]
    for p in parts:
        named = doc.state.named(p.id)
        assert named.shape.IsSame(p.shape)
        assert named.names == seed(p.shape, fid, N.OP_FOR["import"]).names
        assert not named.unnamed()
    maps = [doc.state.named(p.id).names for p in parts]
    assert all(a is not b for a, b in zip(maps, maps[1:]))             # equal, never the same dict


def test_a_placed_part_is_meshed_once_and_lands_where_the_part_is(step_path):
    items = K._import_step_with_colors(step_path)
    bases = {}
    meshes = [K.tessellate(s, bases=bases) for s, *_ in items]
    assert len(bases) == 1
    for (s, *_), m in zip(items, meshes):
        _same_place(m, _meshed_in_place(s))
        assert np.allclose(np.linalg.norm(m.normals, axis=1), 1.0, atol=1e-4)
        assert len(m.face_ranges) == len(K.faces(s)) and len(m.edge_polys) == len(K.edges(s))
        assert len(m.tris) == len(meshes[0].tris)
        # each normal points out of the part: away from its centre, as the triangles' own normals do
        tri = m.verts[m.tris.astype(int)]
        fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        vn = m.normals[m.tris[:, 0].astype(int)]
        big = np.linalg.norm(fn, axis=1) > 1e-6
        assert np.mean(np.sum(fn[big] * vn[big], axis=1) > 0) > 0.95


def _parts(step_path):
    K._STEP_CACHE.clear()
    st = F.ModelState()
    items = K._import_step_with_colors(step_path)
    seeded = {}
    out = []
    for i, (s, nm, c, fc) in enumerate(items):
        p = F.Part(f"p{i}", nm or f"p{i}", s, (0.5, 0.5, 0.5), fc or None)
        p.named = F._import_names(s, "F1", seeded)
        out.append(p)
    return out


def test_the_window_engine_sends_one_geometry_and_a_placement_per_copy(step_path):
    parts = _parts(step_path)
    enc = Encoder(placed=True)
    outs = [enc.part(p, placed=True) for p in parts]
    assert all(o.brep is outs[0].brep and o.mesh is outs[0].mesh for o in outs)
    assert len({o.base for o in outs}) == 1
    assert len({o.hash for o in outs}) == COPIES
    assert all(o.loc is not None and len(o.names["face"]) == len(K.faces(p.shape)) for o, p in zip(outs, parts))


def test_other_engines_still_send_each_part_as_it_sits(step_path):
    parts = _parts(step_path)
    enc = Encoder()
    for p in parts[:3]:
        o = enc.part(p)
        assert o.loc is None and o.base == ""
        assert o.hash == enc.brep(p.shape)[1]
        assert np.allclose(K.bbox(K.shape_from_brep(o.brep))[0], K.bbox(p.shape)[0], atol=1e-6)


def test_the_window_decodes_placed_parts_where_the_engine_built_them(step_path):
    from freeshape.ui.partstudio_engine import StudioEngine
    parts = _parts(step_path)
    enc = Encoder(placed=True)
    out = SimpleNamespace(parts=[enc.part(p, placed=True) for p in parts], context=[], errors={}, variables={},
                          revision=1)
    owner = SimpleNamespace(_plugin_copies=lambda rec, snap: None)
    rec = SimpleNamespace(cache={}, have={})
    res = StudioEngine._decode(owner, rec, {}, out)
    assert len(res.parts) == COPIES
    first = _unplaced(res.parts[0][1])
    for (po, shape, mesh), p in zip(res.parts, parts):
        assert _unplaced(shape).IsSame(first)                          # read once in the window too
        assert np.allclose(K.bbox(shape)[0], K.bbox(p.shape)[0], atol=1e-6)
        assert np.allclose(K.bbox(shape)[1], K.bbox(p.shape)[1], atol=1e-6)
        _same_place(mesh, _meshed_in_place(p.shape))
        assert len(mesh.face_ranges) == len(K.faces(shape))
        assert not math.isnan(float(mesh.verts.sum()))
