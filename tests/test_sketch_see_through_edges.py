"""#22: with "See through solids while sketching" on, faces write no depth, so edges on the far side of a solid drew
over the near face. Before the edge batch the opaque solids now write depth (colour off), and the depth is cleared
after it so the rest of the scene stays see-through. The GL module is a recording stub (as test_context_alpha)."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from conftest import block_doc, only_part  # noqa: E402
from test_context_alpha import GLRecorder  # noqa: E402


class _Sketcher:
    """Just enough of a sketcher for _draw_scene."""
    fid = None
    show_regions = False

    def draw_gl(self, vp):
        pass


def _viewport(sketching, see_through=True):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from freeshape.ui.viewport import Viewport
    vp = Viewport()
    vp.set_scene([("A", only_part(block_doc(20, 20, 10)).shape, (0.8, 0.2, 0.2))], [], [])
    vp.sketch_see_through = see_through
    if sketching:
        vp.sketcher = _Sketcher()
    return vp


def test_see_through_hides_edges_behind_the_near_face(monkeypatch):
    vp = _viewport(sketching=True)
    b = vp._batch
    assert b["nsolid"] and b["nedge"]
    from freeshape.ui import viewport as vpmod
    gl = GLRecorder()
    monkeypatch.setattr(vpmod, "GL", gl)
    vp._vbos = None
    vp._draw_scene()
    calls = gl.calls
    edges = calls.index(("glDrawArrays", "GL_LINES", 0, b["nedge"]))
    before = calls[:edges]
    # the depth-only pass: colour off, depth writes on, every opaque triangle, then colour back on
    off = max(i for i, c in enumerate(before) if c == ("glColorMask",) + ("GL_FALSE",) * 4)
    on = max(i for i, c in enumerate(before) if c == ("glColorMask",) + ("GL_TRUE",) * 4)
    assert off < on
    assert any(c[:3] == ("glDrawElements", "GL_TRIANGLES", b["nsolid"] * 3) for c in calls[off:on])
    masks = [c for c in before[:off] if c[0] == "glDepthMask"]
    assert masks and masks[-1] == ("glDepthMask", "GL_TRUE")     # depth writes are back on for that pass
    assert ("glEnable", "GL_DEPTH_TEST") in before
    # cleared straight after the edges, so later overlays are not hidden by the solids
    after = calls[edges + 1:]
    nxt = next(i for i, c in enumerate(after) if c[0] in ("glClear", "glDrawArrays", "glDrawElements", "glBegin"))
    assert after[nxt] == ("glClear", "GL_DEPTH_BUFFER_BIT")


def test_no_depth_pass_when_solids_are_opaque(monkeypatch):
    for vp in (_viewport(sketching=False), _viewport(sketching=True, see_through=False)):
        from freeshape.ui import viewport as vpmod
        gl = GLRecorder()
        monkeypatch.setattr(vpmod, "GL", gl)
        vp._vbos = None
        vp._draw_scene()
        assert not any(c[0] == "glColorMask" for c in gl.calls)
        assert ("glClear", "GL_DEPTH_BUFFER_BIT") not in gl.calls
