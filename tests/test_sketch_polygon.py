"""Sketch polygon tool: the sketch_ops.polygon builder and the sketcher's Polygon tool."""
import math
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from conftest import sketch_feature  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import sketch_ops as OPS  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402
from test_sketch_inference import VP, _Ev, _sketcher  # noqa: E402


def _len(sk, lid):
    e = sk.entities[lid]
    return float(np.linalg.norm(sk.P(e["p1"]) - sk.P(e["p2"])))


def test_inscribed_six_sides():
    sk = Sketch()
    lines, circ = OPS.polygon(sk, (0, 0), (10, 0), 6)
    assert len(lines) == 6 and sum(e["type"] == "line" for e in sk.entities.values()) == 6
    assert sk.entities[circ]["construction"] and sum(e["type"] == "circle" for e in sk.entities.values()) == 1
    assert sk.status["ok"]
    L = [_len(sk, i) for i in lines]
    assert max(L) - min(L) < 1e-6
    c, r = sk.P(sk.entities[circ]["c"]), sk.radius(circ)
    corners = {sk.entities[i]["p1"] for i in lines}
    assert len(corners) == 6      # one point per corner, shared by two lines
    for pid in corners:
        assert abs(np.linalg.norm(sk.P(pid) - c) - r) < 1e-6


def test_zero_dof_with_diameter_fixed_centre_and_horizontal_side():
    for mode in ("inscribed", "circumscribed"):
        sk = Sketch()
        lines, circ = OPS.polygon(sk, (3, 2), (13, 4), 6, mode)
        assert sk.status["dof"] == 4, mode      # centre (2), size, rotation
        sk.add_constraint("fix", sk.entities[circ]["c"], value=(3, 2))
        sk.add_constraint("diameter", circ, value=20)
        sk.add_constraint("horizontal", lines[0])
        assert sk.solve() and sk.status["dof"] == 0, mode


@pytest.mark.parametrize("mode", ["inscribed", "circumscribed"])
@pytest.mark.parametrize("n", [3, 4, 7, 30])
def test_zero_dof_for_any_side_count(n, mode):
    sk = Sketch()
    t = time.time()
    lines, circ = OPS.polygon(sk, (0, 0), (5, 1), n, mode)
    sk.add_constraint("fix", sk.entities[circ]["c"], value=(0, 0))
    sk.add_constraint("diameter", circ, value=10)
    sk.add_constraint("horizontal", lines[0])
    assert sk.solve() and sk.status["dof"] == 0
    print(f"{mode} n={n}: build + dimensioned solve {time.time() - t:.2f}s")


def test_circumscribed_tangent_to_every_side():
    sk = Sketch()
    lines, circ = OPS.polygon(sk, (0, 0), (0, 10), 5, "circumscribed")
    assert sk.status["ok"]
    c, r = sk.P(sk.entities[circ]["c"]), sk.radius(circ)
    for lid in lines:
        a, b = sk.P(sk.entities[lid]["p1"]), sk.P(sk.entities[lid]["p2"])
        d = b - a
        dist = abs(d[0] * (c - a)[1] - d[1] * (c - a)[0]) / np.linalg.norm(d)
        assert abs(dist - r) < 1e-6
    L = [_len(sk, i) for i in lines]
    assert max(L) - min(L) < 1e-6
    assert abs(L[0] - 2 * r * math.tan(math.pi / 5)) < 1e-6


def test_extrudes_to_eight_faces():
    doc = Document()
    sid = sketch_feature(doc, lambda s: OPS.polygon(s, (0, 0), (10, 0), 6))
    regs = doc.state.sketches[sid].regions
    assert len(regs) == 1
    ex = doc.make_feature("extrude")
    ex["params"].update(regions=[F.region_ref(doc.state, sid, regs[0])], depth=5.0)
    doc.add_feature(ex)
    assert not doc.errors.get(ex["id"])
    part = next(iter(doc.state.parts.values()))
    assert len(K.faces(part.shape)) == 8
    assert abs(K.volume(part.shape) - 1.5 * math.sqrt(3) * 100 * 5) < 1e-3


@pytest.mark.parametrize("n", [0, 2, 31, 6.5])
def test_side_count_out_of_range_refused(n):
    sk = Sketch()
    with pytest.raises(ValueError):
        OPS.polygon(sk, (0, 0), (10, 0), n)
    assert not sk.entities


def test_degenerate_and_bad_mode_refused():
    with pytest.raises(ValueError):
        OPS.polygon(Sketch(), (1, 1), (1, 1), 6)
    with pytest.raises(ValueError):
        OPS.polygon(Sketch(), (0, 0), (1, 0), 6, "nope")


def test_limits_solve():
    for n in (3, 30):
        sk = Sketch()
        t = time.time()
        lines, _ = OPS.polygon(sk, (0, 0), (10, 0), n)
        dt = time.time() - t
        print(f"polygon n={n}: {dt:.2f}s")
        assert sk.status["ok"] and dt < 5
        L = [_len(sk, i) for i in lines]
        assert max(L) - min(L) < 1e-6


class _QtVP(QWidget):
    """The test viewport stub, as a widget so the sides editor has a parent."""
    cam = VP.cam
    ray_plane = staticmethod(VP.ray_plane)
    status_text = ""


def test_tool_two_clicks_then_sides_prompt():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    vp = _QtVP()
    s = _sketcher()
    s.set_tool("polygon")
    s.mouse_move(vp, 400, 300, _Ev())
    s.mouse_press(vp, 400, 300, _Ev())
    s.mouse_move(vp, 500, 300, _Ev())
    s.mouse_press(vp, 500, 300, _Ev())
    assert s._editor is not None and s._prompt_label.startswith("Polygon sides")
    assert sum(e["type"] == "line" for e in s.sk.entities.values()) == 6      # live preview, default 6
    s._editor.setText("8")
    s._editor.textEdited.emit("8")
    assert sum(e["type"] == "line" for e in s.sk.entities.values()) == 8
    s._editor.setText("31")
    s._editor.textEdited.emit("31")
    assert s._prompt_label.startswith("⚠") and not any(e["type"] == "line" for e in s.sk.entities.values())
    s._editor.setText("5")
    s._editor.textEdited.emit("5")
    s._editor.returnPressed.emit()
    assert sum(e["type"] == "line" for e in s.sk.entities.values()) == 5
    assert s.polygon_sides == 5
