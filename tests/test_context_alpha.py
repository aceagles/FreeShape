"""I6.6: the Context has its own translucent range in the batch and its own alpha. Scene: an opaque block "A", a Context
plate "CTX" and a Shift+T block "T". The GL module is replaced by a recording stub to see what the draw path hands it."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402

from conftest import block_doc, only_part  # noqa: E402
from freeshape import kernel as K  # noqa: E402


class GLRecorder:
    """Stands in for OpenGL.GL: constants are their own names, every call is recorded and returns None."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if name.startswith("GL_"):
            return name

        def call(*args):
            self.calls.append((name,) + args)
        return call


def _shape(x, z, dx=20, dy=20, dz=10):
    return K.translate(only_part(block_doc(dx, dy, dz)).shape, (x, 0, z))


def _rig(tmp_path, monkeypatch, context=True, shift_t=True, ini=None):
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    if ini is not None:
        real = QSettings
        monkeypatch.setattr("PySide6.QtCore.QSettings", lambda *a: real(str(ini), real.IniFormat))
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    w._add_tab(block_doc(10, 10, 10))
    vp = w.viewport
    # the order in vp.parts is deliberately not the batch order: T, CTX, A
    parts = [("T", _shape(30, 0), (0.2, 0.8, 0.2), None, None, None, False),
             ("CTX", _shape(60, 0), (0.6, 0.6, 0.9), None, None, None, True),
             ("A", _shape(0, 0), (0.8, 0.2, 0.2), None, None, None, False)]
    if not context:
        parts = [p for p in parts if p[0] != "CTX"]
    vp.set_scene(parts, [], [], [])
    if shift_t:
        vp.set_transparent({"T"})
    return w


def _draw(vp, monkeypatch):
    from freeshape.ui import viewport as vpmod
    gl = GLRecorder()
    monkeypatch.setattr(vpmod, "GL", gl)
    vp._vbos = None
    vp._draw_scene()
    return gl.calls


def _face_draws(calls):
    """(alpha handed to glBlendColor just before the draw, triangle count) for each face draw."""
    out, alpha = [], None
    for c in calls:
        if c[0] == "glBlendColor":
            alpha = c[4]
        elif c[0] == "glDrawElements" and c[1] == "GL_TRIANGLES":
            out.append((alpha, c[2] // 3))
    return out


def test_two_translucent_ranges_name_the_right_parts(tmp_path, monkeypatch):
    w = _rig(tmp_path, monkeypatch)
    b = w.viewport._batch
    r = b["ranges"]
    assert 0 == r["A"][0] < b["nsolid"] == r["CTX"][0] < b["nctx"] == r["T"][0] < b["ntri"]
    w.deleteLater()


def test_alpha_change_keeps_the_batch_and_reaches_the_blend(tmp_path, monkeypatch):
    w = _rig(tmp_path, monkeypatch)
    vp = w.viewport
    assert vp.context_alpha == 0.3
    v0 = vp._batch_version
    b = vp._batch
    w.set_context_alpha(0.6)
    assert vp._batch_version == v0 and vp._batch is b
    draws = _face_draws(_draw(vp, monkeypatch))
    nctx_tris = b["nctx"] - b["nsolid"]
    assert (0.6, nctx_tris) in draws
    assert (vp.REFERENCE_FACE_ALPHA, b["ntri"] - b["nctx"]) in draws
    assert vp.REFERENCE_FACE_ALPHA == 0.3 and len(draws) == 3
    assert vp._batch_version == v0
    w.deleteLater()


def test_alpha_zero_skips_context_faces_but_not_edges(tmp_path, monkeypatch):
    w = _rig(tmp_path, monkeypatch)
    vp = w.viewport
    w.set_context_alpha(0)
    b = vp._batch
    calls = _draw(vp, monkeypatch)
    draws = _face_draws(calls)
    assert len(draws) == 2                      # opaque + Shift+T; the Context range is gone
    starts = [b["ntri"] - len(c[4]) for c in calls if c[0] == "glDrawElements"]
    assert b["nsolid"] not in starts and 0 in starts and b["nctx"] in starts
    assert ("glDrawArrays", "GL_LINES", 0, b["nedge"]) in calls
    w.deleteLater()


def test_no_context_body_draws_as_before(tmp_path, monkeypatch):
    w = _rig(tmp_path, monkeypatch, context=False)
    vp = w.viewport
    b = vp._batch
    assert b["nctx"] == b["nsolid"] and b["nsolid"] < b["ntri"]
    calls = _draw(vp, monkeypatch)
    # the base sequence: opaque draw, then one translucent draw at the constant alpha with no depth write
    seq = [c for c in calls if c[0] in ("glBlendColor", "glDrawElements") or c == ("glDepthMask", "GL_FALSE")]
    i = [n for n, c in enumerate(seq) if c[0] == "glDrawElements"]
    assert len(i) == 2
    assert seq[i[0]][2] == b["nsolid"] * 3 and seq[i[1]][2] == (b["ntri"] - b["nsolid"]) * 3
    assert seq[i[1] - 2:i[1]] == [("glBlendColor", 0.0, 0.0, 0.0, 0.3), ("glDepthMask", "GL_FALSE")]
    # and with neither Context nor Shift+T: one draw, no translucent state
    w.viewport.set_transparent(set())
    b = w.viewport._batch
    assert b["nsolid"] == b["nctx"] == b["ntri"]
    assert len(_face_draws(_draw(w.viewport, monkeypatch))) == 1
    w.deleteLater()


def test_setting_survives_a_new_window_and_is_not_in_the_document(tmp_path, monkeypatch):
    ini = tmp_path / "settings.ini"
    w = _rig(tmp_path, monkeypatch, ini=ini)
    w.set_context_alpha(0.6)
    assert w.settings.value("view/context_alpha") == 0.6 or float(w.settings.value("view/context_alpha")) == 0.6
    w.settings.sync()
    w.deleteLater()
    w2 = _rig(tmp_path, monkeypatch, ini=ini)
    assert w2.viewport.context_alpha == pytest.approx(0.6)
    assert "context_alpha" not in w2.doc.to_json() if isinstance(w2.doc.to_json(), str) else \
        "context_alpha" not in repr(w2.doc.to_json())
    w2.deleteLater()
