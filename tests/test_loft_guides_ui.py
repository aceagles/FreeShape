"""Loft guides (I7.4): the Loft dialog's "Guides and continuity" tick and its guide list (offscreen window).
Picks are delivered with handle_pick (nothing modal runs). Building a guided loft needs OpenCascade 8.0."""
import os
import signal
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from conftest import OCCT_MAJOR, TOP, add, only_part, region_refs  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.document import PartStudioDoc  # noqa: E402
from freeshape.ui import dialogs as D  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402
from test_loft_guides import REFUSED_79, _add_sketch, _guide_distance, _guide_sketch, _guides, _width_at  # noqa: E402
from test_naming_core_features import add_sketch  # noqa: E402
from test_naming_sweep_loft_import import add_sketch_on  # noqa: E402

needs_occt8 = pytest.mark.needs_occt8


def make_doc():
    """Two 20 x 20 squares 40 apart and a sketch of the two arc guides; no loft yet."""
    doc = PartStudioDoc()
    a, _ = add_sketch(doc, -10, -10, 20, 20)
    pl = add(doc, "plane", type="offset", refs=[], ref=TOP, offset=40.0)
    b, _ = add_sketch_on(doc, {"kind": "plane", "fid": pl["id"]}, -10, -10, 20, 20)
    s, g1, g2 = _guide_sketch()
    gid = _add_sketch(doc, s)
    profiles = [("region", region_refs(doc, a)[0]), ("region", region_refs(doc, b)[0])]
    return doc, profiles, gid, (g1, g2)


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app, tmp_path, monkeypatch):
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    doc, profiles, gid, ents = make_doc()
    w._add_tab(doc)
    w.resize(900, 700)
    w.show()
    app.processEvents()
    w.scene = (profiles, gid, ents)
    yield w
    w.deleteLater()


def open_loft(w, guides=True):
    profiles, gid, ents = w.scene
    w.start_feature("loft")
    d = w.dialog
    assert w.mode == "dialog" and d.ftype == "loft"
    for it in profiles:
        d.fields["profiles"].toggle(it)
    if guides:
        d._set("guides_on", True)
    return d


def pick_guide(d, gid, eid):
    d.activate(d.fields["guides"])
    d.handle_pick(Pick("sketch_line", (gid, eid)))


def shown(d, key):
    return d.fields[key].isVisibleTo(d)


def row_of(d, key):
    return next(w for spec, w in d.rows if spec["key"] == key)


def test_two_curve_picks_are_two_guides_and_the_loft_builds(win, app):
    _, gid, (g1, g2) = win.scene
    d = open_loft(win)
    pick_guide(d, gid, g1)
    pick_guide(d, gid, g2)
    p = d.feature["params"]
    assert p["guides"] == [[{"kind": "sketch_chain", "sketch": gid, "entity": g1}],
                           [{"kind": "sketch_chain", "sketch": gid, "entity": g2}]]
    d.accept()
    app.processEvents()
    loft = next(f for f in win.doc.features if f["type"] == "loft")
    assert len(loft["params"]["guides"]) == 2 and "guides_on" not in loft["params"]
    err = win.doc.errors.get(loft["id"])
    if OCCT_MAJOR < 8:
        assert err == REFUSED_79                       # the tick stays; the build error names the version
        return
    assert err is None, err
    body = only_part(win.doc).shape
    assert len(K.solids(body)) == 1 and K.is_valid(body) and len(K.faces(body)) == 6
    for g in _guides():
        assert _guide_distance(body, g) < 1e-4
    assert abs(_width_at(body, 20.0) - 28.0) < 1e-3


def test_removing_a_row_removes_that_guide(win):
    _, gid, (g1, g2) = win.scene
    d = open_loft(win)
    pick_guide(d, gid, g1)
    pick_guide(d, gid, g2)
    f = d.fields["guides"]
    f.list.itemWidget(f.list.item(0)).findChildren(D.QToolButton)[2].click()      # the remove button of row 1
    assert [g[0]["entity"] for g in d.feature["params"]["guides"]] == [g2]
    f._remove_row(0)
    assert "guides" not in d.feature["params"]


def test_reordering_rows_reorders_guides(win):
    _, gid, (g1, g2) = win.scene
    d = open_loft(win)
    pick_guide(d, gid, g1)
    pick_guide(d, gid, g2)
    f = d.fields["guides"]
    order = lambda: [g[0]["entity"] for g in d.feature["params"]["guides"]]      # noqa: E731
    assert order() == [g1, g2]
    f.list.itemWidget(f.list.item(0)).findChildren(D.QToolButton)[1].click()      # move row 1 down
    assert order() == [g2, g1]
    assert not f.move(0, -1) and not f.move(1, 1)
    assert f.move(1, -1)
    assert order() == [g1, g2]


def test_a_condition_and_guides_grey_each_other(win):
    _, gid, (g1, g2) = win.scene
    d = open_loft(win, guides=False)
    tick = row_of(d, "guides_on")
    assert tick.isEnabled()
    d._set("start_cond", "normal")
    assert not tick.isEnabled() and "cannot be combined" in tick.toolTip()
    d._set("start_cond", "")
    assert tick.isEnabled() and tick.toolTip() == ""
    d._set("guides_on", True)
    pick_guide(d, gid, g1)
    for key in ("start_cond", "end_cond"):
        row = row_of(d, key)
        assert not row.isEnabled() and "cannot be combined" in row.toolTip()
    d._set("guides_on", False)
    assert row_of(d, "start_cond").isEnabled() and row_of(d, "end_cond").isEnabled()
    assert "guides" not in d.feature["params"]           # an unticked list leaves no key


def test_ticking_ruled_hides_the_guide_fields(win):
    _, gid, (g1, g2) = win.scene
    d = open_loft(win)
    pick_guide(d, gid, g1)
    assert shown(d, "guides") and row_of(d, "guides_on").isVisibleTo(d)
    d._set("ruled", True)
    assert not shown(d, "guides") and not row_of(d, "guides_on").isVisibleTo(d)
    assert "guides" not in d.feature["params"]
    d._set("ruled", False)
    assert shown(d, "guides") and len(d.feature["params"]["guides"]) == 1


def test_a_loft_without_guides_writes_no_guides_key_and_takes_the_legacy_route(win, app):
    d = open_loft(win, guides=False)
    assert not shown(d, "guides")
    d.accept()
    app.processEvents()
    loft = next(f for f in win.doc.features if f["type"] == "loft")
    assert "guides" not in loft["params"] and "guides_on" not in loft["params"]
    assert win.doc.errors.get(loft["id"]) is None
    assert len(K.faces(only_part(win.doc).shape)) == 6
    # a tick with nothing picked is the same
    d = open_loft(win)
    d.accept()
    app.processEvents()
    assert all("guides" not in f["params"] for f in win.doc.features if f["type"] == "loft")


def test_dialog_labels_and_round_trip():
    labels = {s.get("label") for s in D.SCHEMAS["loft"]}
    assert "Guides and continuity" in labels
    old = {"profiles": [], "ruled": False, "op": "new", "scope": []}
    p = dict(old)
    D._loft_unfold(p)
    D.fields_to_params("loft", D.params_to_fields("loft", p), p)
    D._loft_fold(p, strip=True)
    assert p == old
    ref = {"kind": "sketch_chain", "sketch": "s1", "entity": "e1"}
    g = {"guides": [[ref], [ref, dict(ref, entity="e2")], []]}
    p = dict(old, **g)
    D._loft_unfold(p)
    assert p["guides_on"]
    D.fields_to_params("loft", D.params_to_fields("loft", p), p)
    D._loft_fold(p, strip=True)
    assert p["guides"] == [[ref], [ref, dict(ref, entity="e2")]]          # the empty guide is dropped


def test_command_search_finds_loft_by_guide_curves(app):
    from freeshape.ui.mainwindow import MainWindow
    from freeshape.ui.palette import search_commands
    w = MainWindow()
    app.processEvents()
    for q in ("guide curves", "guided loft"):
        assert "Loft" in [c.name for c in search_commands(w._palette_commands(), q)], q


def test_engine_mode_builds_a_guided_loft_in_the_worker(tmp_path):
    """The real app rebuilds in a worker (MainWindow(engine=True)): the dialog's two guide picks commit and the
    loft builds there (6 faces on 8.0, the refusal on 7.9). Also looks at the live preview with a condition set."""
    from freeshape.engine import process as P
    from freeshape.ui import mainwindow as mw_mod
    from test_engine_picking import _Settings, _spawned
    os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")
    os.environ.setdefault("FREESHAPE_NO_HOME", "1")
    os.environ["FREESHAPE_DATA_DIR"] = str(tmp_path / "appdata")
    doc, profiles, gid, (g1, g2) = make_doc()
    path = str(tmp_path / "guided.fshape")
    doc.save(path)
    app = QApplication.instance() or QApplication([])
    before = set(_spawned())
    w = mw_mod.MainWindow(engine=True)
    w.settings = _Settings()
    try:
        w.open_any(path)
        eng = w.studio_engine
        assert eng.settle(180)
        d0 = w.doc
        assert eng.attached(d0) and not d0.errors, d0.errors
        w.start_feature("loft")
        d = w.dialog
        for it in profiles:
            d.fields["profiles"].toggle(it)
        d._set("guides_on", True)
        pick_guide(d, gid, g1)
        pick_guide(d, gid, g2)
        assert len(d.feature["params"]["guides"]) == 2
        d.accept()
        assert eng.settle(120)
        app.processEvents()
        d1 = w.doc
        loft = next(f for f in d1.features if f["type"] == "loft")
        assert len(loft["params"]["guides"]) == 2
        if OCCT_MAJOR < 8:
            assert d1.errors.get(loft["id"]) == REFUSED_79, d1.errors
        else:
            assert not d1.errors, d1.errors
            body = only_part(d1).shape
            assert K.is_valid(body) and len(K.faces(body)) == 6
            assert abs(_width_at(body, 20.0) - 28.0) < 1e-3
    finally:
        w.studio_engine.shutdown()
        P.SPARES.clear()
        for p in sorted(set(_spawned()) - before):
            try:
                os.kill(p, signal.SIGKILL)
            except OSError:
                pass
