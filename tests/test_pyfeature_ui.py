"""I4.8: Python features in the dialog schema, command search, toolbar, icons and feature list.

Plugins come from I4.1's tests/data/pyplugins/boxes, copied to a temp dir where an icon or a version change is
needed. FREESHAPE_PLUGIN_DIR points at an empty temp dir, so the real user plugin folder is never read.
"""
import os
import shutil
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from freeshape import pyfeature  # noqa: E402
from freeshape.ui import dialogs, pyfeature_ui  # noqa: E402

BOXES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "pyplugins", "boxes")
SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"><rect x="4" y="4" width="24" height="24" '
       'fill="#c00"/></svg>')


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    user = tmp_path / "user_plugins"
    user.mkdir()
    monkeypatch.setenv("FREESHAPE_PLUGIN_DIR", str(user))
    before = {p.id for p in pyfeature.plugins()}
    yield
    for p in pyfeature.plugins():
        if p.id not in before:
            pyfeature.unregister_plugin(p.id)
    from freeshape.ui.icons import icon
    icon.cache_clear()


@pytest.fixture
def boxes():
    info = pyfeature.register_plugin(BOXES)
    assert info is not None, pyfeature.problems()
    pyfeature_ui.install()
    return info


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _window():
    app = _app()
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    app.processEvents()
    return w


def test_schema_for_box(boxes):
    rows = pyfeature_ui.schema_for("py:boxes/box")
    assert [(r["t"], r["key"], r["unit"], r["min"]) for r in rows] == [
        ("num", "w", "mm", 0.001), ("num", "d", "mm", 0.001), ("num", "h", "mm", 0.001)]
    assert dialogs.SCHEMAS["py:boxes/box"] == rows


def test_schema_for_every_kind(boxes):
    rows = {r["key"]: r for r in pyfeature_ui.schema_for("py:boxes/row")}
    assert (rows["count"]["t"], rows["count"]["unit"], rows["count"]["min"]) == ("num", "", 1)
    assert rows["size"]["t"] == "choice" and rows["size"]["options"][0] == ("small", "Small (5 mm)")
    assert rows["gap"]["t"] == "check"
    assert rows["pitch"]["visible"]({"gap": False}) is False and rows["pitch"]["visible"]({"gap": True}) is True
    assert pyfeature_ui.schema_for("py:nope/none") == []


def test_angle_float_units(tmp_path):
    src = tmp_path / "ang"
    src.mkdir()
    (src / "plugin.json").write_text('{"id":"ang","name":"Ang","version":"1","licence":"MIT","features":["m:A"]}')
    (src / "m.py").write_text(
        "from freeshape.pyfeature.api import Feature, Angle, Float\n"
        "class A(Feature):\n id='a'\n label='Alpha beta'\n description='d'\n"
        " params=[Angle('t','Turn',10.0,min=0), Float('f','Factor',1.5)]\n"
        " def build(self,p,i): raise NotImplementedError\n")
    assert pyfeature.register_plugin(str(src)) is not None, pyfeature.problems()
    rows = pyfeature_ui.schema_for("py:ang/a")
    assert [(r["unit"], r["min"]) for r in rows] == [("deg", 0), ("", None)]
    assert pyfeature_ui.fallback_text("py:ang/a") == "AB"


def test_palette_and_toolbar(boxes):
    w = _window()
    cmds = {c.key: c for c in w._palette_commands()}
    c = cmds["py:boxes/box"]
    assert c.group == "Python features" and c.name == "Box" and "boxes" in c.keywords.lower()
    assert "py:boxes/row" in cmds
    from PySide6.QtWidgets import QToolButton
    btn = w.findChild(QToolButton, "py_plugin_boxes")
    assert btn is not None and not btn.icon().isNull()
    assert [a.text() for a in btn.menu().actions()] == ["Box", "Box row"]
    btn.menu().actions()[0].trigger()
    assert w.mode == "dialog" and w.dialog.ftype == "py:boxes/box"
    w.cancel_dialog()


def test_palette_finds_by_plugin_name(boxes):
    from freeshape.ui.palette import search_commands as search
    w = _window()
    names = [c.name for c in search(w._palette_commands(), "boxes")]
    assert "Box" in names


def test_dialog_edit_and_accept(boxes):
    w = _window()
    w.start_feature("py:boxes/box")
    d = w.dialog
    assert [s["key"] for s in d.schema] == ["w", "d", "h"]
    ed = d.widgets["w"]
    ed.setText("12")
    ed.textEdited.emit("12")
    d.accept()
    f = w.doc.features[-1]
    assert f["name"] == "Box 1" and f["params"]["w"] == 12.0
    assert w.doc.errors.get(f["id"]) is None
    assert any(p.id.startswith(f["id"]) for p in w.doc.state.parts.values())
    # edit again: values come back
    w.start_feature("py:boxes/box", edit_fid=f["id"])
    assert w.dialog.widgets["w"].text() == "12"
    w.cancel_dialog()


def test_dialog_expression_in_integer_field(boxes):
    w = _window()
    var = w.doc.make_feature("variable")
    var["params"].update(name="n", expr="3")
    w.doc.add_feature(var, 0)
    w.start_feature("py:boxes/row")
    ed = w.dialog.widgets["count"]
    ed.setText("#n")
    ed.textEdited.emit("#n")
    assert w.dialog.feature["params"]["count"] == "#n"
    w.dialog.accept()
    f = w.doc.features[-1]
    assert w.doc.errors.get(f["id"]) is None
    assert len([p for p in w.doc.state.parts.values() if p.id.startswith(f["id"])]) == 3


def test_notice_amber_in_feature_list(boxes):
    w = _window()
    f = w.doc.make_feature("py:boxes/box")
    f["plugin"] = {"id": "boxes", "version": "0.9.0"}
    w.doc.add_feature(f, 0)
    w._populate_lists()
    lst = w.left.features
    items = [lst.item(i) for i in range(lst.count()) if lst.item(i).text() == f["name"]]
    assert len(items) == 1
    assert items[0].foreground().color().name() == "#b26a00"
    assert items[0].toolTip() == pyfeature.notices(w.doc.features)[f["id"]]
    assert "0.9.0" in items[0].toolTip()


def test_current_version_has_no_notice(boxes):
    w = _window()
    f = w.doc.make_feature("py:boxes/box")
    w.doc.add_feature(f, 0)
    w._populate_lists()
    lst = w.left.features
    it = next(lst.item(i) for i in range(lst.count()) if lst.item(i).text() == f["name"])
    assert it.toolTip() == "" and it.foreground().color().name() != "#b26a00"


def _icon_plugin(tmp_path, svg):
    dst = tmp_path / "boxes_svg"
    shutil.copytree(BOXES, dst)
    if svg is not None:
        (dst / "box.svg").write_text(svg)
    info = pyfeature.register_plugin(str(dst))
    assert info is not None, pyfeature.problems()
    return info


def _declare_icon(monkeypatch, name):
    monkeypatch.setattr(pyfeature.get("py:boxes/box"), "icon", name, raising=False)
    from freeshape.ui.icons import icon
    icon.cache_clear()


def _centre_colour(ic):
    return ic.pixmap(32, 32).toImage().pixelColor(16, 16)


def test_icon_without_svg_is_fallback(boxes):
    _app()
    from freeshape.ui.icons import icon
    ic = icon("py:boxes/box")
    assert not ic.isNull() and pyfeature_ui.fallback_text("py:boxes/box") == "BO"
    assert pyfeature_ui.initials("Spur gear") == "SG"


def test_icon_with_svg(tmp_path, monkeypatch):
    _app()
    _icon_plugin(tmp_path, SVG)
    _declare_icon(monkeypatch, "box.svg")
    from freeshape.ui.icons import icon
    ic = icon("py:boxes/box")
    assert not ic.isNull()
    c = _centre_colour(ic)
    assert (c.red(), c.green(), c.blue()) == (204, 0, 0)


@pytest.mark.parametrize("svg,name", [("not svg at all", "box.svg"), (None, "box.svg"), (SVG, "../box.svg"),
                                       (SVG, "")])
def test_icon_broken_svg_falls_back(tmp_path, monkeypatch, svg, name):
    _app()
    _icon_plugin(tmp_path, svg)
    _declare_icon(monkeypatch, name)
    from freeshape.ui.icons import icon
    ic = icon("py:boxes/box")
    assert not ic.isNull()
    c = _centre_colour(ic)
    assert (c.red(), c.green(), c.blue()) != (204, 0, 0)
