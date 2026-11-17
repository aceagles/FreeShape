"""I12.3: the Canvas in the desktop: the toolbar button and Document menu item, the generic dialog built from
SCHEMAS["canvas"], the 3D view drawing each canvas as a textured quad, and picking one as its feature.

Offscreen, `MainWindow(engine=True)` as the real app starts it. The image file dialog is replaced (`_pick_canvas_image`),
no modal box is opened (QMessageBox is recorded), and the window closes without a save prompt. The 3D view is blank when
captured without a display, so the drawing is checked on the draw list (a recording stand-in for OpenGL.GL, as
test_sketch_see_through_edges does) and on the list the viewport holds, not on pixels."""
import json
import os
import shutil

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

pytest.importorskip("PySide6.QtWidgets")
pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

from PySide6.QtCore import QPoint, QSettings, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QToolButton  # noqa: E402

from freeshape import dialog_schema as DSC  # noqa: E402
from freeshape import image_info as II  # noqa: E402
from freeshape.engine import process as P  # noqa: E402
from freeshape.ui import home as H  # noqa: E402
from freeshape.ui import mainwindow as MW  # noqa: E402
from freeshape.ui import viewport as VP  # noqa: E402
from test_context_alpha import GLRecorder  # noqa: E402
from test_engine_picking import _spawned  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRACKET = os.path.join(ROOT, "examples", "documents", "bracket")
SETTLE = 180


def _png(w, h, rgb=(200, 40, 40), dpi=None):
    """A whole w x h PNG of one colour (an RGB image, so its colour can be told)."""
    import struct
    import zlib
    from test_canvas_feature import _chunk
    out = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    if dpi is not None:
        ppm = int(round(dpi / 0.0254))
        out += _chunk(b"pHYs", struct.pack(">IIB", ppm, ppm, 1))
    row = b"\x00" + bytes(rgb) * w
    out += _chunk(b"IDAT", zlib.compress(row * h))
    return out + _chunk(b"IEND", b"")


@pytest.fixture
def window(tmp_path, monkeypatch):
    """A window in engine mode, and the list of message boxes it showed (none is modal here)."""
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(tmp_path / "autosave")))
    monkeypatch.setattr(MW.MainWindow, "autosave_dir", classmethod(lambda cls: str(tmp_path / "autosave")))
    os.makedirs(tmp_path / "autosave", exist_ok=True)
    monkeypatch.setattr(H, "thumbs_dir", lambda: str(tmp_path / "thumbs"))
    os.makedirs(tmp_path / "thumbs", exist_ok=True)
    shown = []
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, _n=name, **k: shown.append((_n, a[2])) or QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    before = set(_spawned())
    w = MW.MainWindow(engine=True)
    w.settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    app.processEvents()
    try:
        yield w, shown
    finally:
        for d in w.docs.values():
            d.dirty = False
        w.close()
        if w.studio_engine is not None:
            w.studio_engine.shutdown()
        P.SPARES.clear()
        for p in sorted(set(_spawned()) - before):
            try:
                os.kill(p, 9)
            except OSError:
                pass


def _open_bracket(w, tmp_path):
    src = str(tmp_path / "received" / "bracket")
    shutil.copytree(BRACKET, src)
    assert w.open_document(src) is not None and w.studio_engine.settle(SETTLE)
    QApplication.instance().processEvents()
    return src


def _document_menu(w):
    (menu,) = [b.menu() for b in w.findChildren(QToolButton) if b.objectName() == "menuButton" and b.menu()
               and "Open Document folder…" in [a.text() for a in b.menu().actions()]]
    return menu


def _settle(w):
    assert w.studio_engine.settle(SETTLE)
    QApplication.instance().processEvents()


def _insert(w, path):
    """Document > Insert canvas… with the file dialog answering `path`."""
    w._pick_canvas_image = lambda: path
    (action,) = [a for a in _document_menu(w).actions() if a.text() == "Insert canvas…"]
    action.trigger()


def _empty_studio(w):
    """A new empty Part Studio tab (no parts, so nothing stands in front of a canvas)."""
    w.new_doc()
    _settle(w)
    w.act_planes.setChecked(False)          # the default planes are picked too: leave them out of the clicks
    return w.doc


def _canvas_features(doc):
    return [f for f in doc.features if f["type"] == "canvas"]


def _type_number(dialog, key, text):
    ed = dialog.widgets[key]
    ed.setText(text)
    ed.textEdited.emit(text)


def _screen_of(vp, point):
    s = vp.cam.to_screen(np.asarray(point, float))
    return float(s[0]), float(s[1])


def _top_view(w):
    vp = w.viewport
    vp.resize(640, 480)
    vp.set_standard_view("Top", fit=False, animate=False)
    return vp


# =========================================================================== where it sits

def test_toolbar_button_follows_import_and_menu_item_follows_the_import_items(window):
    w, _shown = window
    tips = [b.toolTip() for b in w.findChildren(QToolButton)]
    i_import = next(i for i, t in enumerate(tips) if t.startswith("Import STEP (opens in a new tab)"))
    assert tips[i_import + 1].startswith("Canvas")
    assert tips[i_import + 2].startswith("Export STEP")
    texts = [a.text() for a in _document_menu(w).actions()]
    assert texts.index("Insert canvas…") == texts.index("Import STEP / BREP into this tab…") + 1
    assert texts.index("Insert canvas…") < texts.index("Component library…")


def test_the_feature_list_row_has_a_canvas_icon_and_the_icon_is_exported():
    from PySide6.QtGui import QGuiApplication
    QGuiApplication.instance() or QApplication([])
    from freeshape.ui import icons
    assert not icons.icon("canvas").pixmap(32, 32).toImage().isNull()
    import importlib.util
    spec = importlib.util.spec_from_file_location("export_icons", os.path.join(ROOT, "scripts", "web", "export_icons.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert "canvas" in mod.TOOLS
    svg = mod.render("canvas")
    assert svg.startswith(b"<?xml") and b"<svg" in svg


# =========================================================================== create

def test_creating_a_canvas_from_a_png_on_top_adds_one_feature_and_one_drawn_canvas(window, tmp_path):
    w, shown = window
    doc = _empty_studio(w)
    path = str(tmp_path / "photo.png")
    with open(path, "wb") as fh:
        fh.write(_png(200, 100))
    _insert(w, path)
    assert shown == [] and w.mode == "dialog"
    d = w.dialog
    assert d.ftype == "canvas" and d.is_new
    # the generic dialog, built from the schema: the same rows, in the same order
    assert [spec["key"] for spec, _row in d.rows] == [spec["key"] for spec in DSC.SCHEMAS["canvas"]]
    assert d.widgets["_info"].text() == "photo.png"
    assert d.feature["params"]["plane"] == {"kind": "std", "name": "Top"}
    w0, h0 = II.read_info(_png(200, 100)).default_size_mm()
    assert d.feature["params"]["width"] == pytest.approx(w0) and d.feature["params"]["height"] == pytest.approx(h0)
    d.accept()
    _settle(w)
    assert w.mode == "idle" and len(_canvas_features(doc)) == 1
    vp = w.viewport
    assert len(vp.canvases) == 1, (doc.errors, doc.state.canvases)
    c = vp.canvases[0]
    assert c["id"] == _canvas_features(doc)[0]["id"] and c["image"] is not None and c["image"].width() == 200
    assert c["normal"] == pytest.approx([0, 0, 1]) and c["opacity"] == 50.0
    assert c["width"] == pytest.approx(w0) and c["height"] == pytest.approx(h0)
    assert doc.errors == {}


def test_the_toolbar_button_asks_for_the_file_and_opens_the_dialog(window, tmp_path):
    w, _shown = window
    _empty_studio(w)
    path = str(tmp_path / "photo.png")
    with open(path, "wb") as fh:
        fh.write(_png(40, 40))
    w._pick_canvas_image = lambda: path
    (btn,) = [b for b in w.findChildren(QToolButton) if b.toolTip().startswith("Canvas —")]
    btn.click()
    assert w.mode == "dialog" and w.dialog.ftype == "canvas"
    w.cancel_dialog()
    assert w.mode == "idle" and not _canvas_features(w.doc)


def test_a_selected_flat_face_is_the_planes_preselection(window, tmp_path):
    w, _shown = window
    _open_bracket(w, tmp_path)
    vp = w.viewport
    pid = next(iter(vp.part_shapes))
    from freeshape import kernel as K
    faces = K.faces(vp.part_shapes[pid])
    top = max((i for i, f in enumerate(faces) if K.face_info(f).get("n") is not None),
              key=lambda i: (K.face_info(faces[i])["n"][2] > 0.99, K.face_info(faces[i])["c"][2]))
    vp.set_selection([VP.Pick("face", (pid, top))])
    path = str(tmp_path / "photo.png")
    with open(path, "wb") as fh:
        fh.write(_png(30, 30))
    _insert(w, path)
    plane = w.dialog.feature["params"]["plane"]
    assert plane["kind"] == "face" and set(plane) == {"kind", "face"}
    assert w.dialog.fields["plane_sel"].items
    w.cancel_dialog()


def test_a_curved_face_is_refused_with_the_servers_words_and_a_flat_one_replaces_top(window, tmp_path):
    from freeshape import kernel as K
    w, _shown = window
    _open_bracket(w, tmp_path)
    vp = w.viewport
    pid = next(iter(vp.part_shapes))
    faces = K.faces(vp.part_shapes[pid])
    curved = next(i for i, f in enumerate(faces) if K.face_info(f).get("n") is None)
    flat = next(i for i, f in enumerate(faces) if K.face_info(f).get("n") is not None)
    path = str(tmp_path / "photo.png")
    with open(path, "wb") as fh:
        fh.write(_png(30, 30))
    _insert(w, path)
    d = w.dialog
    d.activate(d.fields["plane_sel"])
    d.handle_pick(VP.Pick("face", (pid, curved)))
    assert w.status.currentMessage() == "That face is not flat"
    assert d.feature["params"]["plane"] == {"kind": "std", "name": "Top"}
    d.handle_pick(VP.Pick("face", (pid, flat)))
    assert d.feature["params"]["plane"]["kind"] == "face"
    w.cancel_dialog()


# =========================================================================== refusals

def test_the_server_s_refusals_apply_and_a_refused_image_is_never_decoded(window, tmp_path, monkeypatch):
    from test_serve_canvas import png_claiming
    from PySide6.QtGui import QImage
    w, shown = window
    _empty_studio(w)
    decoded = []
    real = QImage.fromData
    monkeypatch.setattr(QImage, "fromData", staticmethod(lambda *a, **k: decoded.append(a) or real(*a, **k)))
    cases = {}
    gif = tmp_path / "a.gif"
    gif.write_bytes(b"GIF89a" + b"\x00" * 20)
    cases[str(gif)] = "Only PNG and JPEG images can be used"
    huge = tmp_path / "huge.png"
    huge.write_bytes(png_claiming(8000, 8000))
    cases[str(huge)] = "at most 40 million pixels"
    cut = tmp_path / "cut.png"
    cut.write_bytes(_png(10, 10)[:-20])
    cases[str(cut)] = "cut short"
    for path, words in cases.items():
        shown.clear()
        _insert(w, path)
        assert w.mode == "idle" and not _canvas_features(w.doc), path
        assert len(shown) == 1 and words in shown[0][1], (path, shown)
    monkeypatch.setattr("freeshape.serve.MAX_IMAGE", 100)
    ok = tmp_path / "big.png"
    ok.write_bytes(_png(40, 40))
    shown.clear()
    _insert(w, str(ok))
    assert w.mode == "idle" and len(shown) == 1 and "The image is over" in shown[0][1]
    assert decoded == []                         # nothing the header check refused was decoded


# =========================================================================== draw

def _draw_calls(vp, monkeypatch):
    gl = GLRecorder()
    monkeypatch.setattr(VP, "GL", gl)
    vp._vbos = None
    vp._canvas_tex = {}
    vp._draw_scene()
    return gl.calls


def _viewport_with_canvas(opacity=50.0, flip_h=False, with_part=False):
    from PySide6.QtGui import QImage
    from conftest import block_doc, only_part
    QApplication.instance() or QApplication([])
    vp = VP.Viewport()
    parts = [("A", only_part(block_doc(20, 20, 10)).shape, (0.8, 0.2, 0.2))] if with_part else []
    vp.set_scene(parts, [], [])
    img = QImage.fromData(_png(8, 4))
    assert not img.isNull()
    xdir = [-1.0, 0, 0] if flip_h else [1.0, 0, 0]
    vp.set_canvases([{"id": "F9", "blob": "k1", "origin": [0, 0, 0], "xdir": xdir, "ydir": [0, 1.0, 0],
                      "normal": [0, 0, 1.0], "width": 40.0, "height": 20.0, "opacity": opacity, "selectable": True,
                      "image": img}])
    return vp


def test_a_canvas_is_a_textured_quad_after_the_parts_at_its_opacity_without_writing_depth(monkeypatch):
    vp = _viewport_with_canvas(opacity=50.0, with_part=True)
    calls = _draw_calls(vp, monkeypatch)
    names = [c[0] for c in calls]
    solids = max(i for i, c in enumerate(calls) if c[0] == "glDrawElements" and c[1] == "GL_TRIANGLES")
    quad = names.index("glBegin")
    assert quad > solids                                               # after the parts
    assert calls[quad] == ("glBegin", "GL_QUADS") and names[quad + 1:quad + 9].count("glVertex3f") == 4
    before = calls[solids:quad]
    mask = [c for c in before if c[0] == "glDepthMask"]
    assert mask[-1] == ("glDepthMask", "GL_FALSE")                    # no depth write while the quad is drawn
    assert ("glEnable", "GL_DEPTH_TEST") in calls[:quad]              # but the depth test: a part in front hides it
    assert ("glColor4f", 1.0, 1.0, 1.0, 0.5) in before                # the stored opacity is the alpha
    assert ("glBlendFuncSeparate", "GL_SRC_ALPHA", "GL_ONE_MINUS_SRC_ALPHA", "GL_ONE", "GL_ONE_MINUS_SRC_ALPHA") in before
    assert ("glEnable", "GL_TEXTURE_2D") in before
    after = calls[quad:]
    assert [c for c in after if c[0] == "glDepthMask"][0] == ("glDepthMask", "GL_TRUE")   # restored for the rest


def test_the_quad_follows_the_stored_frame_with_the_flips_applied(monkeypatch):
    vp = _viewport_with_canvas(flip_h=True)
    corners = VP.Viewport.canvas_corners(vp.canvases[0])
    (p0, uv0), (p1, uv1), (p2, uv2), (p3, uv3) = corners
    # the picture's left edge (u = 0) is at -hw along the drawn x direction, which a horizontal flip reverses
    assert list(p0) == pytest.approx([20, -10, 0]) and list(p1) == pytest.approx([-20, -10, 0])
    assert list(p2) == pytest.approx([-20, 10, 0]) and list(p3) == pytest.approx([20, 10, 0])
    assert [uv0, uv1, uv2, uv3] == [(0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0)]
    calls = _draw_calls(vp, monkeypatch)
    at = [i for i, c in enumerate(calls) if c[0] == "glTexCoord2f"]
    verts = [calls[i + 1][1:] for i in at]                       # each texture coordinate is followed by its corner
    assert verts == [(20.0, -10.0, 0.0), (-20.0, -10.0, 0.0), (-20.0, 10.0, 0.0), (20.0, 10.0, 0.0)]


def test_the_picture_uploaded_is_the_images_own_colour_and_the_alpha_is_the_opacity(monkeypatch):
    """What the quad blends: source alpha = opacity over the background, so a 50 percent canvas is the half way
    colour between the image and what is behind it (a GL blend, so it is shown here from the numbers it is given)."""
    vp = _viewport_with_canvas(opacity=50.0)
    calls = _draw_calls(vp, monkeypatch)
    (up,) = [c for c in calls if c[0] == "glTexImage2D"]
    pixels = up[-1]
    assert up[4:6] == (8, 4) and len(pixels) == 8 * 4 * 4
    assert tuple(pixels[:4]) == (200, 40, 40, 255)
    alpha = [c for c in calls if c[0] == "glColor4f" and c[1:4] == (1.0, 1.0, 1.0)][0][4]
    bg = np.array([255, 255, 255.0])
    blended = np.frombuffer(pixels[:3], dtype=np.uint8) * alpha + bg * (1 - alpha)
    assert blended == pytest.approx([227.5, 147.5, 147.5])


def test_a_canvas_with_no_readable_picture_is_not_drawn():
    vp = _viewport_with_canvas()
    vp.set_canvases([dict(vp.canvases[0], image=None)])
    assert vp.canvases == []


# =========================================================================== pick

def _top_look(vp):
    vp.resize(640, 480)
    vp.set_standard_view("Top", fit=False, animate=False)
    vp.cam.scale = 0.1                                   # 0.1 mm a pixel: the canvas (40 x 20 mm) is 400 x 200 px


def test_a_click_takes_the_canvas_as_its_feature_and_a_part_in_front_of_it_wins(monkeypatch):
    vp = _viewport_with_canvas()
    _top_look(vp)
    pk = vp.pick(*_screen_of(vp, (5, 5, 0)))
    assert pk == VP.Pick("canvas", ("F9",))
    assert vp.pick(*_screen_of(vp, (60, 5, 0))) is None                  # outside its rectangle
    # a part in front of it hides it where they overlap
    from freeshape import kernel as K
    from conftest import block_doc, only_part
    block = K.translate(only_part(block_doc(20, 40, 10)).shape, (-30, -20, 5))
    vp.set_scene([("A", block, (0.8, 0.2, 0.2))], [], [])
    canvases = [dict(c) for c in _viewport_with_canvas().canvases]
    vp.set_canvases(canvases)
    _top_look(vp)
    front = vp.pick(*_screen_of(vp, (-20, 0, 15)))
    assert front is not None and front.kind == "face"
    assert vp.pick(*_screen_of(vp, (10, 0, 0))) == VP.Pick("canvas", ("F9",))
    # a non-selectable canvas is never picked
    vp.set_canvases([dict(c, selectable=False) for c in canvases])
    assert vp.pick(*_screen_of(vp, (10, 0, 0))) is None
    # a dialog's field, a sketch or an explicit kind list never takes it
    vp.set_canvases(canvases)
    vp.select_filter = {"plane"}
    assert vp.pick(*_screen_of(vp, (10, 0, 0))) is None
    vp.select_filter = None
    assert vp.pick(*_screen_of(vp, (10, 0, 0)), kinds={"face", "plane"}) is None


def test_a_canvas_is_never_hovered(monkeypatch):
    vp = _viewport_with_canvas()
    _top_look(vp)
    vp._hover_xy = _screen_of(vp, (5, 5, 0))
    vp._do_hover()
    assert vp.hover is None


def test_clicking_a_canvas_selects_its_row_and_not_selectable_selects_nothing(window, tmp_path):
    w, _shown = window
    doc = _empty_studio(w)
    path = str(tmp_path / "photo.png")
    with open(path, "wb") as fh:
        fh.write(_png(200, 100))
    _insert(w, path)
    w.dialog.accept()
    _settle(w)
    (feat,) = _canvas_features(doc)
    vp = _top_view(w)
    vp.cam.scale = 0.1
    w.left.features.clearSelection()
    assert w.left.features.selected_feature_ids() == []
    x, y = _screen_of(vp, (3, 3, 0))
    assert vp.pick(x, y) == VP.Pick("canvas", (feat["id"],))
    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, QPoint(int(x), int(y)))
    QApplication.instance().processEvents()
    assert w.left.features.selected_feature_ids() == [feat["id"]]
    assert vp.selection == []                                           # a feature row, not a thing in the view
    # Selectable off: the same click selects nothing
    w.start_feature("canvas", feat["id"])
    w.dialog.widgets["selectable"].setChecked(False)
    w.dialog.accept()
    _settle(w)
    assert vp.canvases and vp.canvases[0]["selectable"] is False
    w.left.features.clearSelection()
    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, QPoint(int(x) + 20, int(y) + 20))
    QApplication.instance().processEvents()
    assert w.left.features.selected_feature_ids() == [] and vp.selection == []


# =========================================================================== edit

def test_the_dialog_for_an_existing_canvas_shows_the_schema_rows_and_opacity_redraws_it(window, tmp_path, monkeypatch):
    w, _shown = window
    doc = _empty_studio(w)
    path = str(tmp_path / "photo.png")
    with open(path, "wb") as fh:
        fh.write(_png(200, 100))
    _insert(w, path)
    w.dialog.accept()
    _settle(w)
    (feat,) = _canvas_features(doc)
    w.start_feature("canvas", feat["id"])
    d = w.dialog
    assert not d.is_new and w.mode == "dialog"
    assert [spec["key"] for spec, _row in d.rows] == [spec["key"] for spec in DSC.SCHEMAS["canvas"]]
    p = feat["params"]
    assert d.widgets["_info"].text() == "photo.png"
    assert d.widgets["width"].text() == f"{p['width']:g}" or float(d.widgets["width"].text()) == pytest.approx(p["width"], rel=1e-3)
    assert d.widgets["opacity"].text() == "50"
    assert d.widgets["keep_aspect"].isChecked() and d.widgets["selectable"].isChecked()
    assert not d.widgets["flip_h"].isChecked()
    assert w.viewport.canvases[0]["opacity"] == 50.0
    _type_number(d, "opacity", "25")
    d._do_preview()                                  # the preview the dialog schedules, now
    assert w.viewport.canvases[0]["opacity"] == 25.0
    calls = _draw_calls(w.viewport, monkeypatch)
    assert ("glColor4f", 1.0, 1.0, 1.0, 0.25) in calls
    _type_number(d, "opacity", "150")                # above the schema's maximum: refused, the opacity stays
    assert d.widgets["opacity"].property("invalid") == "true" and d.feature["params"]["opacity"] == 25.0
    d.widgets["flip_h"].setChecked(True)
    d._do_preview()
    assert w.viewport.canvases[0]["xdir"][0] == pytest.approx(-1.0)
    d.accept()
    _settle(w)
    assert doc.feature(feat["id"])["params"]["opacity"] == 25.0 and doc.feature(feat["id"])["params"]["flip_h"]
    assert w.viewport.canvases[0]["opacity"] == 25.0


def test_keep_aspect_ratio_couples_width_and_height_in_the_dialog(window, tmp_path):
    w, _shown = window
    _empty_studio(w)
    path = str(tmp_path / "photo.png")
    with open(path, "wb") as fh:
        fh.write(_png(200, 100))
    _insert(w, path)
    d = w.dialog
    _type_number(d, "width", "60")
    assert d.feature["params"]["width"] == 60.0 and d.feature["params"]["height"] == pytest.approx(30.0)
    assert float(d.widgets["height"].text()) == pytest.approx(30.0)
    w.cancel_dialog()


def test_hiding_the_canvas_with_its_eye_takes_it_out_of_the_view(window, tmp_path):
    w, _shown = window
    doc = _empty_studio(w)
    path = str(tmp_path / "photo.png")
    with open(path, "wb") as fh:
        fh.write(_png(20, 20))
    _insert(w, path)
    w.dialog.accept()
    _settle(w)
    (feat,) = _canvas_features(doc)
    assert len(w.viewport.canvases) == 1
    w._tree_eye(f"feature:{feat['id']}")
    QApplication.instance().processEvents()
    assert w.viewport.canvases == []
    w._tree_eye(f"feature:{feat['id']}")
    QApplication.instance().processEvents()
    assert len(w.viewport.canvases) == 1


# =========================================================================== saved bytes

def _reference_canvas(folder, data, **choices):
    """The canvas I12.1's own path saves for `choices` (test_serve_canvas._reference), found by its type."""
    from test_serve_canvas import _reference, _tab_features
    _reference(folder, data, **choices)
    return next(f for f in _tab_features(folder) if f["type"] == "canvas")


def test_a_canvas_made_in_the_desktop_dialog_saves_what_the_served_dialog_saves(window, tmp_path):
    data = _png(200, 100)
    w, _shown = window
    src = _open_bracket(w, tmp_path)
    path = str(tmp_path / "photo.png")
    with open(path, "wb") as fh:
        fh.write(data)
    _insert(w, path)
    d = w.dialog
    d.widgets["keep_aspect"].setChecked(False)
    _type_number(d, "width", "60")
    _type_number(d, "rotation", "30")
    _type_number(d, "opacity", "70")
    d.widgets["flip_h"].setChecked(True)
    _type_number(d, "x", "5")
    d.accept()
    _settle(w)
    assert w.doc.errors == {}
    assert w.save()
    # the same choices through I12.1's own path (the image inline), on a second copy of the Document
    ref = str(tmp_path / "ref")
    shutil.copytree(BRACKET, ref)
    want = _reference_canvas(ref, data, keep_aspect=False, width=60.0, rotation=30.0, opacity=70.0, flip_h=True, x=5.0)
    src = w.folder.path
    with open(os.path.join(src, "document.json")) as fh:
        entry = next(e for e in json.load(fh)["tabs"] if e["id"] == "T1")
    with open(os.path.join(src, entry["file"])) as fh:
        made = next(f for f in json.load(fh)["features"] if f["type"] == "canvas")
    assert made["type"] == "canvas"
    # the height is the image's own (the dialog starts with the default size); the feature fills a None the same way
    want["params"]["height"] = made["params"]["height"]
    assert json.dumps(made, indent=1) == json.dumps(want, indent=1)
