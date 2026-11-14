"""I1.3: construction axes in the 3D view: listed in viewport.axes, drawn, picked, hidden. Offscreen main window."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from conftest import block_doc, only_part  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402


def _x_edge(i):
    return i["type"] == int(K.GeomAbs_Line) and abs(abs(i["d"][0]) - 1) < 1e-6 and i["c"][1] < 1 and i["c"][2] < 1


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    doc = block_doc(20, 10, 5)
    part = only_part(doc)
    e = next(e for e in K.edges(part.shape) if _x_edge(K.edge_info(e)))
    f = doc.make_feature("axis")
    f["params"]["refs"] = [{"k": "edge", "ref": F.make_ref(part.id, e, "edge", part.shape)}]
    doc.add_feature(f)
    assert doc.errors.get(f["id"]) is None
    w._add_tab(doc)
    w.resize(900, 700)
    w.show()
    app.processEvents()
    w.fid = f["id"]
    yield w
    w.deleteLater()


def _screen_of_axis(w, t=0.5):
    vp = w.viewport
    _key, o, d, span, _label = vp.axes[0]
    a, b = vp.axis_ends(o, d, span)
    s = vp.cam.to_screen(a + t * (b - a))
    return float(s[0]), float(s[1])


def test_axis_is_listed_and_picked(win):
    vp = win.viewport
    assert len(vp.axes) == 1
    key, _o, _d, _span, label = vp.axes[0]
    assert key == ("axis", win.fid) and label == win.doc.feature(win.fid)["name"]
    x, y = _screen_of_axis(win)
    assert vp.pick(x, y, kinds={"axis"}) == Pick("axis", (win.fid,))
    assert Pick("axis", (win.fid,)).label() == "Axis"
    assert vp.pick(x + 40, y + 40, kinds={"axis"}) is None


def test_hidden_axis_is_gone_and_filter_off_skips_it(win):
    vp = win.viewport
    x, y = _screen_of_axis(win, 0.02)        # in the drawn margin past the block, where no edge lies under it
    assert "axis" in vp.DEFAULT_KINDS and win.SELECT_DEFAULTS["axis"] is True
    assert vp.pick(x, y) == Pick("axis", (win.fid,))
    win.sel_actions["axis"].setChecked(False)
    assert vp.pick(x, y) != Pick("axis", (win.fid,))
    win.sel_actions["axis"].setChecked(True)
    assert vp.pick(x, y) == Pick("axis", (win.fid,))
    win.doc.toggle_feature_hidden(win.fid)
    win._show_state(win.doc.state)
    assert vp.axes == []
    assert vp.pick(x, y, kinds={"axis"}) is None


def test_feature_list_click_selects_and_highlights_axis(win):
    vp = win.viewport
    win._tree_plane_clicked("feature:" + win.fid)
    assert vp.selection == [Pick("axis", (win.fid,))]
    # the eye: the list row carries one, toggling it hides the axis
    from freeshape.ui.panels import ROLE_EYE, ROLE_ID
    lst = win.left.features
    row = next(lst.item(i) for i in range(lst.count()) if lst.item(i).data(ROLE_ID) == win.fid)
    assert row.data(ROLE_EYE) == "shown"
    win._tree_eye("feature:" + win.fid)
    win._show_state(win.doc.state)
    assert vp.axes == [] and vp.selection == []        # a hidden axis can no longer be selected
    # selecting the row outlines the axis, as it does a plane
    win._show_state(win.doc.state)
    win.doc.toggle_feature_hidden(win.fid)
    win._show_state(win.doc.state)
    faces, polys = win._feature_geometry(win.fid)
    assert len(polys) == 1 and len(polys[0]) == 2


def test_view_menu_offers_hide_axis(win, monkeypatch):
    from PySide6.QtWidgets import QMenu
    import freeshape.ui.mainwindow as mw
    seen, keep = [], []

    class RecMenu(QMenu):
        def exec(self, *a, **k):
            keep.append(self)
            seen.extend(self.actions())

    monkeypatch.setattr(mw, "QMenu", RecMenu)
    win._viewport_context(Pick("axis", (win.fid,)), win.pos())
    hide = next(a for a in seen if a.text() == "Hide axis")
    hide.trigger()
    assert win.fid in win.doc.hidden_features


def test_null_span_axis_has_finite_length(win):
    vp = win.viewport
    a, b = vp.axis_ends([1, 2, 3], [0, 0, 1], None)
    assert np.isfinite(a).all() and np.isfinite(b).all()
    assert np.linalg.norm(b - a) == pytest.approx(2 * vp.plane_half())
    a, b = vp.axis_ends([0, 0, 0], [1, 0, 0], [0, 20])
    assert np.linalg.norm(b - a) == pytest.approx(24.0)
    assert np.allclose((a + b) / 2, [10, 0, 0])
