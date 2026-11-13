"""3MF and OBJ export (I1.4)."""
import os
import sys
import zipfile
import xml.etree.ElementTree as ET

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape import assembly as A  # noqa: E402
from freeshape import meshexport as ME  # noqa: E402
from freeshape.features import Part  # noqa: E402

NS = {"m": ME.CORE_NS}


def _read(path):
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        root = ET.fromstring(z.read("3D/3dmodel.model"))
    return names, root


def _obj_mesh(obj):
    v = np.array([[float(q.get(a)) for a in "xyz"] for q in obj.findall(".//m:vertex", NS)])
    t = np.array([[int(q.get(a)) for a in ("v1", "v2", "v3")] for q in obj.findall(".//m:triangle", NS)])
    return v, t


def _volume(v, t):
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6)


def test_two_part_studio_3mf(tmp_path):
    from test_ux_core import _studio
    doc = _studio(2)
    p = str(tmp_path / "s.3mf")
    doc.export(p)
    names, root = _read(p)
    assert {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"} <= set(names)
    assert root.get("unit") == "millimeter"
    objs = root.findall(".//m:object", NS)
    parts = list(doc.state.parts.values())
    assert sorted(o.get("name") for o in objs) == sorted(q.name for q in parts)
    by_name = {o.get("name"): o for o in objs}
    for part in parts:
        mesh = K.tessellate(part.shape, 0.05, 0.3)
        v, t = _obj_mesh(by_name[part.name])
        assert len(v) == len(mesh.verts) and len(t) == len(mesh.tris)
    assert len(root.findall(".//m:build/m:item", NS)) == 2
    # colours go through a basematerials group
    bm = root.find(".//m:basematerials", NS)
    assert bm is not None and bm.find("m:base", NS).get("displaycolor").startswith("#")
    assert all(o.get("pid") == bm.get("id") for o in objs)


def test_winding_gives_positive_volume(tmp_path):
    from test_ux_core import _studio
    doc = _studio(1)
    p = str(tmp_path / "b.3mf")
    doc.export(p)
    _, root = _read(p)
    v, t = _obj_mesh(root.find(".//m:object", NS))
    assert _volume(v, t) == pytest.approx(1000.0, rel=1e-3)


def test_winding_curved_part_positive(tmp_path):
    cyl = K.cylinder((0, 0, 0), (0, 0, 1), 10, 20)
    cyl = K.fillet(cyl, K.edges(cyl), 2)
    m = K.tessellate(cyl, 0.05, 0.3, edges_too=False)
    assert _volume(m.verts.astype(float), m.tris.astype(np.int64)) > 0
    p = str(tmp_path / "c.3mf")
    ME.export_parts([(cyl, "cyl", (0.5, 0.5, 0.5))], p)
    _, root = _read(p)
    v, t = _obj_mesh(root.find(".//m:object", NS))
    assert _volume(v, t) == pytest.approx(np.pi * 100 * 20, rel=0.05)


def _asm_two_instances():
    a = A.AssemblyDoc()
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    shape = BRepPrimAPI_MakeBox(10, 20, 30).Shape()
    a.resolver = lambda src: Part("x", "blk", shape, (0.2, 0.4, 0.6), None)
    s = {"kind": "studio", "uid": "u1", "part": "blk"}
    T1 = np.eye(4)
    c, sn = np.cos(0.5), np.sin(0.5)
    T2 = np.array([[c, -sn, 0, 40], [sn, c, 0, 7], [0, 0, 1, 3], [0, 0, 0, 1.0]])
    a.insert(s, T1)
    a.insert(s, T2)
    return a, [T1, T2], shape


def test_assembly_one_object_two_items(tmp_path):
    a, Ts, shape = _asm_two_instances()
    p = str(tmp_path / "a.3mf")
    a.export(p)
    _, root = _read(p)
    assert len(root.findall(".//m:object", NS)) == 1
    items = root.findall(".//m:build/m:item", NS)
    assert len(items) == 2
    v, _t = _obj_mesh(root.find(".//m:object", NS))
    pt = np.array([1.0, 2.0, 3.0, 1.0])
    for it, T in zip(items, Ts):
        M = ME.parse_matrix_3mf(it.get("transform"))
        assert np.allclose(M, T, atol=1e-6)
        # row-vector convention: v' = [x y z 1] * 4x3 matrix
        nums = np.array([float(x) for x in it.get("transform").split()]).reshape(4, 3)
        assert np.allclose(np.append(pt[:3], 1) @ nums, (T @ pt)[:3], atol=1e-6)


def test_obj(tmp_path):
    from test_ux_core import _studio
    doc = _studio(2)
    p = str(tmp_path / "s.obj")
    doc.export(p)
    lines = open(p).read().splitlines()
    assert sum(1 for l in lines if l.startswith("o ")) == 2
    nv = sum(1 for l in lines if l.startswith("v "))
    faces = [list(map(int, l.split()[1:])) for l in lines if l.startswith("f ")]
    assert faces and all(1 <= i <= nv for f in faces for i in f)
    total = sum(len(K.tessellate(q.shape, 0.05, 0.3).verts) for q in doc.state.parts.values())
    assert nv == total


def test_obj_applies_transforms(tmp_path):
    a, Ts, shape = _asm_two_instances()
    p = str(tmp_path / "a.obj")
    a.export(p)
    lines = open(p).read().splitlines()
    vs = np.array([[float(x) for x in l.split()[1:]] for l in lines if l.startswith("v ")])
    n = len(vs) // 2
    m = K.tessellate(shape, 0.05, 0.3)
    exp = m.verts.astype(float) @ Ts[1][:3, :3].T + Ts[1][:3, 3]
    assert np.allclose(vs[n:], exp, atol=1e-4)


def test_options_change_resolution(tmp_path):
    cyl = K.cylinder((0, 0, 0), (0, 0, 1), 10, 20)
    cyl = K.fillet(cyl, K.edges(cyl), 2)
    counts = []
    for o in ({"defl": 0.2, "angle": 0.5}, {"defl": 0.05, "angle": 0.3}):
        p = str(tmp_path / f"{o['defl']}.3mf")
        ME.export_parts([(cyl, "c", (1, 1, 1))], p, **o)
        counts.append(len(_obj_mesh(_read(p)[1].find(".//m:object", NS))[1]))
    assert counts[0] < counts[1]


def test_mesh_dialog_has_no_ascii_choice():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from freeshape.ui.export_dialog import StlOptionsDialog
    assert StlOptionsDialog(mesh_kind="3MF").options() == {"defl": 0.05, "angle": 0.3}
    assert "ascii" in StlOptionsDialog().options()
