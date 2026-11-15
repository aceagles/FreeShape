"""I3.4: Enclose dialog, toolbar and command (offscreen; nothing modal runs). Fixtures from tests/test_enclose.py.
The spike chose enclose=makervolume, so a plane pick is accepted by the Entities field."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
from conftest import TOP, add, sketch_feature  # noqa: E402
from test_enclose import A, cube_doc, curve_refs, lines_of, solids_of, surfaces  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape.document import PartStudioDoc  # noqa: E402
from freeshape.ui.palette import search_commands  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    app.processEvents()
    yield w
    w.deleteLater()


def start(win, **kw):
    if kw:
        win.start_feature("enclose", **kw)
    else:
        next(c for c in win._palette_commands() if c.name == "Enclose").action()
    assert win.mode == "dialog" and win.dialog.ftype == "enclose"
    return win.dialog


def c_prism(doc, settle=lambda: None):
    """An open C-shaped surface 10 x 5 x 7 (open on the x = 0 side); returns (part, top plane fid, base plane fid)."""
    def build(s):
        p = [s.add_point(0, 0), s.add_point(10, 0), s.add_point(10, 5), s.add_point(0, 5)]
        for a, b in zip(p, p[1:]):
            s.add_line(a, b)
    sid = sketch_feature(doc, build, TOP)
    settle()                                            # an engine window builds the sketch in its worker
    add(doc, "extrude", creation="surface", curves=curve_refs(sid, lines_of(doc, sid)[0]), depth=7, op="new")
    settle()
    (c,) = surfaces(doc)
    lo, hi = K.bbox(c.shape)
    top = add(doc, "plane", type="offset", refs=[{"k": "plane", "ref": TOP}], offset=float(hi[2]))
    base = add(doc, "plane", type="offset", refs=[{"k": "plane", "ref": TOP}], offset=float(lo[2]))
    return c, top["id"], base["id"]


def test_enclose_command_stores_picks_and_builds_the_cube(win):
    doc = cube_doc()                                   # I3.3's first fixture: six squares make a cube
    win._add_tab(doc)
    sps = surfaces(doc)
    d = start(win)
    for sp in sps:
        d.handle_pick(Pick("part", (sp.id,)))
    p = d.feature["params"]
    assert p["entities"] == [{"kind": "part", "ref": sp.id} for sp in sps]
    assert p["op"] == "new" and p["keep_tools"] is False
    win.commit_dialog()
    f = [f for f in doc.features if f["type"] == "enclose"][-1]
    assert doc.errors.get(f["id"]) is None
    assert not surfaces(doc)
    (s,) = solids_of(doc)
    assert K.is_valid(s.shape) and len(K.faces(s.shape)) == 6
    assert K.volume(s.shape) == pytest.approx(A ** 3, rel=1e-6)


def test_keep_tools_keeps_the_surfaces(win):
    doc = cube_doc()
    win._add_tab(doc)
    d = start(win)
    for sp in surfaces(doc):
        d.fields["entities"].toggle(("part", sp.id))
    d._set("keep_tools", True)
    win.commit_dialog()
    assert len(surfaces(doc)) == 6 and len(solids_of(doc)) == 1


def test_a_plane_pick_is_accepted_and_stored(win):
    doc = PartStudioDoc()
    win._add_tab(doc)
    c, top, base = c_prism(doc)
    d = start(win)
    d.handle_pick(Pick("part", (c.id,)))
    d.handle_pick(Pick("plane", ("plane", top)))
    d.handle_pick(Pick("plane", ("plane", base)))
    d.handle_pick(Pick("plane", ("std", "Right")))
    assert d.feature["params"]["entities"] == [
        {"kind": "part", "ref": c.id},
        {"kind": "plane", "ref": {"kind": "plane", "fid": top}},
        {"kind": "plane", "ref": {"kind": "plane", "fid": base}},
        {"kind": "plane", "ref": {"kind": "std", "name": "Right"}}]
    win.commit_dialog()
    assert not doc.errors, doc.errors
    (s,) = solids_of(doc)
    assert K.volume(s.shape) == pytest.approx(10 * 5 * 7, rel=1e-6)


def test_planes_alone_show_the_named_error(win):
    doc = PartStudioDoc()
    win._add_tab(doc)
    d = start(win)
    d.handle_pick(Pick("plane", ("std", "Right")))
    assert d.feature["params"]["entities"] == [{"kind": "plane", "ref": {"kind": "std", "name": "Right"}}]
    win.commit_dialog()
    f = [f for f in doc.features if f["type"] == "enclose"]
    assert not f or doc.errors.get(f[-1]["id"]) == "Select the surfaces that surround a volume"


def test_scope_shows_only_for_add(win):
    doc = cube_doc()
    win._add_tab(doc)
    d = start(win)
    d._set("op", "new")
    assert not d.fields["scope"].isVisibleTo(d)
    d._set("op", "add")
    assert d.fields["scope"].isVisibleTo(d)


def test_enclose_edit_rebuilds_with_the_new_value(win):
    doc = cube_doc()
    win._add_tab(doc)
    ents = [{"kind": "part", "ref": p.id} for p in surfaces(doc)]
    f = add(doc, "enclose", entities=ents, keep_tools=False)
    assert len(solids_of(doc)) == 1 and not surfaces(doc)
    win.start_feature("enclose", edit_fid=f["id"])
    assert len(win.dialog.fields["entities"].items) == 6
    win.dialog._set("keep_tools", True)
    win.commit_dialog()
    assert doc.errors.get(f["id"]) is None
    assert len(surfaces(doc)) == 6 and len(solids_of(doc)) == 1


def test_enclose_found_by_search_in_part_studio_not_in_assembly(win):
    assert "Enclose" in {c.name for c in win._palette_commands()}
    for q in ("enclose", "solid from surfaces", "sew", "stitch", "knit"):
        assert "Enclose" in [c.name for c in search_commands(win._palette_commands(), q)][:3], q
    assert search_commands(win._palette_commands(), "stitch")[0].name == "Enclose"
    from test_assembly import make
    win._add_tab(make({}))
    assert "Enclose" not in {c.name for c in win._palette_commands()}


def test_enclose_has_a_toolbar_button_with_an_icon(win):
    from PySide6.QtWidgets import QToolButton
    btns = [b for b in win.findChildren(QToolButton) if b.property("icon_name") == "enclose"]
    assert len(btns) == 1 and not btns[0].icon().isNull()


# ---------------------------------------------------------------------------- the engine wire

@pytest.fixture(scope="module")
def engine_win():
    os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")
    os.environ.setdefault("FREESHAPE_NO_HOME", "1")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow(engine=True)
    w.settings = type("S", (dict,), {"value": lambda s, k, d=None: s.get(k, d),
                                     "setValue": lambda s, k, v: s.__setitem__(k, v)})()
    assert w.studio_engine.settle(180)
    yield w
    w.studio_engine.shutdown()
    from freeshape.engine import process as P
    P.SPARES.clear()


def test_enclose_made_in_the_engine_window_builds_a_solid(engine_win):
    w = engine_win
    se = w.studio_engine
    doc = w.doc
    c, top, base = c_prism(doc, lambda: se.settle(180))  # an open C surface closed by two feature planes and Right
    assert se.settle(180)
    c = next(iter(doc.state.parts.values()))
    assert K.is_surface(c.shape)
    w.start_feature("enclose")
    dlg = w.dialog
    dlg.handle_pick(Pick("part", (c.id,)))
    dlg.handle_pick(Pick("plane", ("plane", top)))
    dlg.handle_pick(Pick("plane", ("plane", base)))
    dlg.handle_pick(Pick("plane", ("std", "Right")))
    assert len(dlg.feature["params"]["entities"]) == 4
    dlg._do_preview()                                    # the preview builds in the worker without an error
    assert se.settle(180)
    assert dlg.err.isHidden(), dlg.err.text()
    w.commit_dialog()
    assert se.settle(180)
    assert not doc.errors, doc.errors
    parts = list(doc.state.parts.values())
    assert len(parts) == 1 and not K.is_surface(parts[0].shape)
    assert K.is_valid(parts[0].shape)
    assert K.volume(parts[0].shape) == pytest.approx(10 * 5 * 7, rel=1e-6)
