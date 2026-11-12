"""Binary STL export and per-part default colours."""
import base64
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape import features as F, kernel as K
from freeshape.document import Document
from freeshape.sketch import Sketch


def _studio(n=2):
    """n separate boxes (extrude op=new), side by side."""
    doc = Document()
    for i in range(n):
        s = Sketch()
        x = i * 30
        pts = [s.add_point(x, 0), s.add_point(x + 10, 0), s.add_point(x + 10, 10), s.add_point(x, 10)]
        for a, b in zip(pts, pts[1:] + pts[:1]):
            s.add_line(a, b)
        sk = doc.make_feature("sketch")
        sk["params"]["sketch"] = s.to_dict()
        doc.add_feature(sk)
        ex = doc.make_feature("extrude")
        ex["params"].update(regions=[F.region_ref(doc.state, sk["id"], doc.state.sketches[sk["id"]].regions[0])],
                            depth=10.0, op="new")
        doc.add_feature(ex)
    assert not doc.errors
    return doc


def _colors(doc):
    return [p.color for p in doc.state.parts.values()]


def test_stl_export_is_binary():
    doc = _studio(1)
    shape = next(iter(doc.state.parts.values())).shape
    path = os.path.join(tempfile.mkdtemp(), "box.stl")
    K.export_stl([shape], path)
    data = open(path, "rb").read()
    n = struct.unpack("<I", data[80:84])[0]
    assert n >= 12
    assert len(data) == 84 + 50 * n
    from OCP.StlAPI import StlAPI_Reader
    from OCP.TopoDS import TopoDS_Shape
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE
    back = TopoDS_Shape()
    assert StlAPI_Reader().Read(back, path)
    e, nf = TopExp_Explorer(back, TopAbs_FACE), 0
    while e.More():
        nf += 1
        e.Next()
    assert nf == n


def test_two_parts_get_different_colors():
    c = _colors(_studio(2))
    assert len(c) == 2 and c[0] != c[1]


def test_colors_deterministic_on_rebuild():
    doc = _studio(3)
    before = _colors(doc)
    assert len(set(before)) == 3
    doc.rebuild()
    assert _colors(doc) == before
    assert _colors(_studio(3)) == before


def test_step_import_keeps_color(monkeypatch):
    shape = next(iter(_studio(1).state.parts.values())).shape
    monkeypatch.setattr(K, "import_step_with_colors",
                        lambda path, progress=None: [(shape, "A", (0.1, 0.2, 0.3), {}), (shape, "B", None, {})])
    doc = Document()
    imp = doc.make_feature("import")
    imp["params"] = {"data": base64.b64encode(b"x").decode(), "filename": "a.step"}
    doc.add_feature(imp)
    assert not doc.errors
    a, b = doc.state.parts.values()
    assert a.color == (0.1, 0.2, 0.3)
    assert b.color == F.palette_color(2)


def test_override_wins():
    doc = _studio(2)
    pid = next(iter(doc.state.parts))
    doc.set_part_override(pid, color=(0.9, 0.1, 0.1))
    assert doc.state.parts[pid].color == (0.9, 0.1, 0.1)
