"""STL export options: resolution, ASCII or binary (I1.3)."""
import os
import struct
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import pytest  # noqa: E402

from freeshape import kernel as K  # noqa: E402


def _filleted_cylinder():
    cyl = K.cylinder((0, 0, 0), (0, 0, 1), 10, 20)
    return K.fillet(cyl, K.edges(cyl), 2)


def _tris(path):
    return struct.unpack("<I", open(path, "rb").read()[80:84])[0]


def test_coarse_has_fewer_triangles_than_fine(tmp_path):
    coarse, fine = str(tmp_path / "c.stl"), str(tmp_path / "f.stl")
    K.export_stl([_filleted_cylinder()], coarse, defl=0.2, angle=0.5)
    K.export_stl([_filleted_cylinder()], fine, defl=0.05, angle=0.3)
    assert 0 < _tris(coarse) < _tris(fine)


def test_ascii_and_binary(tmp_path):
    a, b = str(tmp_path / "a.stl"), str(tmp_path / "b.stl")
    K.export_stl([_filleted_cylinder()], a, ascii=True)
    K.export_stl([_filleted_cylinder()], b)
    assert open(a, "rb").read(5) == b"solid"
    data = open(b, "rb").read()
    assert len(data) == 84 + 50 * _tris(b)


def test_default_equals_explicit_fine(tmp_path):
    d, f = str(tmp_path / "d.stl"), str(tmp_path / "f.stl")
    K.export_stl([_filleted_cylinder()], d)
    K.export_stl([_filleted_cylinder()], f, defl=0.05, angle=0.3, ascii=False)
    assert _tris(d) == _tris(f)


def test_document_export_passes_options(tmp_path):
    from test_ux_core import _studio
    doc = _studio(1)
    coarse = str(tmp_path / "c.stl")
    fine = str(tmp_path / "f.stl")
    ascii_ = str(tmp_path / "a.stl")
    doc.export(fine)
    doc.export(coarse, options={"defl": 0.2, "angle": 0.5})
    doc.export(ascii_, options={"ascii": True})
    assert _tris(coarse) == _tris(fine)          # a box has the same mesh at any resolution
    assert open(ascii_, "rb").read(5) == b"solid"
    assert open(fine, "rb").read(5) != b"solid" or len(open(fine, "rb").read()) == 84 + 50 * _tris(fine)


def test_assembly_export_passes_options(tmp_path, monkeypatch):
    from freeshape.assembly import AssemblyDoc
    seen = {}
    monkeypatch.setattr(K, "export_stl", lambda shapes, path, **kw: seen.update(kw))
    a = AssemblyDoc.__new__(AssemblyDoc)
    a.instances = [{"id": "i1"}]
    a.parts = {"i1": object()}
    a.world_shape = lambda iid: "shape"
    a.export("x.stl", options={"defl": 0.2, "angle": 0.5, "ascii": True})
    assert seen == {"defl": 0.2, "angle": 0.5, "ascii": True}
    seen.clear()
    a.export("x.stl")
    assert seen == {}


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_dialog_values(qapp):
    from freeshape.ui.export_dialog import StlOptionsDialog
    d = StlOptionsDialog()
    assert d.options() == {"defl": 0.05, "angle": 0.3, "ascii": False}
    for name, (dv, av) in {"Coarse": (0.2, 0.5), "Medium": (0.1, 0.4), "Fine": (0.05, 0.3)}.items():
        d.resolution.setCurrentText(name)
        assert d.options() == {"defl": dv, "angle": av, "ascii": False}
    d.resolution.setCurrentText("Custom")
    d.defl_edit.setText("0.7")
    d.angle_edit.setText("0.9")
    d.format.setCurrentText("ASCII")
    assert d.options() == {"defl": 0.7, "angle": 0.9, "ascii": True}


def test_dialog_remembers_last_choice(qapp, tmp_path):
    from PySide6.QtCore import QSettings
    from freeshape.ui.export_dialog import StlOptionsDialog
    s = QSettings(str(tmp_path / "x.ini"), QSettings.IniFormat)
    d = StlOptionsDialog(settings=s)
    d.resolution.setCurrentText("Coarse")
    d.format.setCurrentText("ASCII")
    d._accept()
    d2 = StlOptionsDialog(settings=s)
    assert d2.options() == {"defl": 0.2, "angle": 0.5, "ascii": True}
