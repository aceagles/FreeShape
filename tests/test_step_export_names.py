"""Part Studio STEP export keeps part names and colours."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape import kernel as K
from test_ux_core import _studio


def _export(doc, tmp_path):
    path = str(tmp_path / "out.step")
    mode = doc.export(path)
    assert mode == "named", "flat STEP fallback was taken"
    return K.import_step_with_colors(path)


def test_two_parts_keep_names_and_colours(tmp_path):
    doc = _studio(2)
    a, b = list(doc.state.parts)
    doc.set_part_override(a, name="Base", color=(1.0, 0.0, 0.0))
    doc.set_part_override(b, name="Lid", color=(0.0, 0.0, 1.0))
    got = _export(doc, tmp_path)
    assert len(got) == 2
    by = {n: c for _s, n, c, _f in got}
    assert sorted(by) == ["Base", "Lid"]
    for name, want in (("Base", (1, 0, 0)), ("Lid", (0, 0, 1))):
        assert by[name] is not None and all(abs(x - y) <= 1 / 255 for x, y in zip(by[name], want))


def test_single_part_keeps_name(tmp_path):
    doc = _studio(1)
    doc.set_part_override(next(iter(doc.state.parts)), name="Solo")
    got = _export(doc, tmp_path)
    assert [n for _s, n, _c, _f in got] == ["Solo"]


def test_flat_fallback_is_reported(tmp_path, monkeypatch):
    doc = _studio(1)
    monkeypatch.setattr(K, "trsf_from_matrix", lambda M: 1 / 0)
    assert doc.export(str(tmp_path / "f.step")) == "flat"
