"""DXF export for cutting machines (freeshape/dxfexport.py): any sketch or flat face, written the way a profile
says (layers, colours, format). The K40 Whisperer profile is checked against what that program reads: blue (or a
layer with "engrave" in its name) is vector engrave, anything else is vector cut, and it reads no text."""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ezdxf  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402

from conftest import add, block_doc, only_part, sketch_feature  # noqa: E402
from freeshape import dxfexport as DX  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import sheetmetal as SM  # noqa: E402
from freeshape.dxf import DxfWriter  # noqa: E402
from freeshape.sketch import ORIGIN, Sketch  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402
from test_connector_frames import H, plate_studio  # noqa: E402

W, D = 60.0, 40.0


class _Settings(dict):
    def value(self, k, default=None):
        return self.get(k, default)

    def setValue(self, k, v):
        self[k] = v


def _every_role():
    g = DX.Geometry()
    g.line("cut", (0, 0), (10, 0))
    g.circle("cut", (5, 5), 2)
    g.arc("cut", (0, 0), 3, 0, 90)
    g.poly("engrave", [(1, 1), (4, 1), (4, 3), (1, 1)], closed=True)
    g.line("bend", (0, 2), (10, 2))
    g.text("note", (5, 2), "UP 90", 2.5, 0.0, "center", "bottom")
    g.poly("sheet", [(-5, -5), (20, -5), (20, 20), (-5, 20)], closed=True)
    return g


def _read(path):
    doc = ezdxf.readfile(str(path))
    return doc, list(doc.modelspace())


# =========================================================================== profiles
def test_profiles_round_trip_through_json_and_bad_json_is_dropped():
    mine = DX.K40_WHISPERER.copy("My laser")
    mine.roles["cut"].colour = 3
    mine.roles["engrave"].layer = "SCORE"
    back = DX.profiles_from_json(DX.profiles_to_json([DX.STANDARD, mine]))       # built-ins are not saved
    assert [p.name for p in back] == ["My laser"] and not back[0].builtin
    assert back[0].to_dict() == mine.to_dict()
    assert DX.find_profile("My laser", back).roles["cut"].colour == 3
    assert DX.find_profile("gone", back) is DX.STANDARD
    assert DX.profiles_from_json("{not json") == [] and DX.profiles_from_json(None) == []
    assert DX.profiles_from_json('{"a": 1}') == [] and DX.profiles_from_json('[3, "x"]') == []
    # a user profile cannot shadow a built-in one, and odd values fall back to sane ones
    odd = '[{"name": "Standard"}, {"name": "X", "format": "r99", "roles": {"cut": {"colour": 999, "linetype": "?"},' \
          ' "nonsense": {}}}]'
    (x,) = DX.profiles_from_json(odd)
    assert x.name == "X" and x.format == "r2010" and list(x.roles) == ["cut"]
    assert x.roles["cut"].colour == 7 and x.roles["cut"].linetype == "CONTINUOUS"


def test_standard_profile_keeps_the_layers_freeshape_has_always_written(tmp_path):
    counts = DX.write(_every_role(), str(tmp_path / "s.dxf"))
    assert counts == {"cut": 3, "engrave": 1, "bend": 1, "note": 1, "sheet": 1}
    doc, ents = _read(tmp_path / "s.dxf")
    assert {e.dxf.layer for e in ents} == {"CUT", "ENGRAVE", "BEND", "NOTES", "SHEET"}
    assert all(e.dxf.color == 256 for e in ents)                                 # colour by layer
    lay = doc.layers
    assert (lay.get("CUT").dxf.color, lay.get("BEND").dxf.color, lay.get("NOTES").dxf.color,
            lay.get("SHEET").dxf.color) == (7, 1, 3, 8)
    assert lay.get("BEND").dxf.linetype == "DASHED"
    assert {e.dxftype() for e in ents} == {"LINE", "CIRCLE", "ARC", "LWPOLYLINE", "TEXT"}
    assert [e.closed for e in ents if e.dxftype() == "LWPOLYLINE"] == [True, True]


def test_k40_whisperer_profile_is_red_cut_blue_engrave_and_nothing_it_would_cut_by_mistake(tmp_path):
    path = tmp_path / "k40.dxf"
    counts = DX.write(_every_role(), str(path), DX.K40_WHISPERER)
    assert counts == {"cut": 3, "engrave": 1, "bend": 1}                       # no label, no sheet outline
    _doc, ents = _read(path)
    assert len(ents) == 5
    assert {e.dxftype() for e in ents} <= {"LINE", "ARC", "CIRCLE", "POLYLINE"}  # what every reader knows
    cut = [e for e in ents if e.dxf.layer == "CUT"]
    eng = [e for e in ents if "ENGRAVE" in e.dxf.layer.upper()]
    assert len(cut) == 3 and len(eng) == 2 and len(cut) + len(eng) == len(ents)
    assert all(e.dxf.color == 1 for e in cut)                                    # red, on the entity itself
    assert all(e.dxf.color == 5 for e in eng)                                    # blue, on the entity itself
    assert _doc.layers.get("ENGRAVE").dxf.color == 5 and _doc.layers.get("CUT").dxf.color == 1
    (poly,) = [e for e in ents if e.dxftype() == "POLYLINE"]
    assert poly.is_closed and len(list(poly.vertices)) == 3
    text = path.read_text()
    assert "$INSUNITS" in text and "AC1009" in text and "TEXT\n" not in text.replace("$TEXTSTYLE", "")


def test_a_profile_that_leaves_everything_out_is_an_error_not_an_empty_file(tmp_path):
    g = DX.Geometry()
    g.text("note", (0, 0), "label", 2.5)
    g.poly("sheet", [(0, 0), (1, 0), (1, 1)], closed=True)
    with pytest.raises(DX.DxfExportError):
        DX.write(g, str(tmp_path / "none.dxf"), DX.K40_WHISPERER)
    assert not (tmp_path / "none.dxf").exists()


def test_r12_writer_is_unchanged_unless_asked(tmp_path):
    def save(path, **kw):
        w = DxfWriter()
        w.layer("A", 3)
        for k, v in kw.pop("attrs", {}).items():
            setattr(w, k, v)
        w.line("A", (0, 0), (1, 1))
        w.polyline("A", [(0, 0), (1, 0), (1, 1)], True)
        w.save(str(path), (0, 0), (1, 1), **kw)
        return path.read_text()

    plain = save(tmp_path / "a.dxf")
    assert "$INSUNITS" not in plain
    body = plain.split("ENTITIES")[1]
    assert " 62\n" not in body
    more = save(tmp_path / "b.dxf", insunits=4, attrs={"entity_colours": {"A": 3}})
    assert "$INSUNITS\n 70\n4\n" in more
    assert more.split("ENTITIES")[1].count("  8\nA\n 62\n3\n") == 6      # line + polyline, its 3 vertices, seqend
    assert more.replace("  9\n$INSUNITS\n 70\n4\n", "").replace(" 62\n3\n", "") == plain.replace(
        " 62\n3\n", "")                                                        # (the layer table has a 62 too)


# =========================================================================== geometry
def test_moved_keeps_arcs_right_under_a_turn_and_under_a_mirror():
    g = DX.Geometry()
    g.arc("cut", (1, 0), 2, 0, 90)
    g.line("cut", (0, 0), (1, 0))
    g.circle("cut", (1, 1), 0.5)
    g.poly("engrave", [(0, 0), (1, 0), (1, 1)], closed=True)
    turn = g.moved([[0, -1], [1, 0]], [10, 0])                                   # quarter turn, then along x
    kind = {k: d for _r, k, d in turn.items}
    assert np.allclose(kind["arc"][0], (10, 1)) and np.allclose(kind["arc"][2:], (90, 180))
    assert np.allclose(kind["line"], ((10, 0), (10, 1))) and np.allclose(kind["circle"][0], (9, 1))
    assert kind["poly"][1] is True
    mirror = g.moved([[1, 0], [0, -1]], [0, 0])                                  # y turned over
    c, r, a0, a1 = next(d for _r, k, d in mirror.items if k == "arc")
    assert np.allclose(c, (1, 0)) and r == 2
    assert np.isclose(a0 % 360, 270) and np.isclose(a1 % 360, 0)                 # still counter-clockwise
    mid = math.radians(a0 + ((a1 - a0) % 360) / 2)
    assert np.allclose((c[0] + r * math.cos(mid), c[1] + r * math.sin(mid)),
                       (1 + 2 * math.cos(math.radians(45)), -2 * math.sin(math.radians(45))))
    assert g.bounds() == ((-1.0, -2.0), (3.0, 2.0))


def test_combine_puts_parallel_sources_where_they_are_and_refuses_the_rest():
    base = K.Plane(np.zeros(3), np.array([1.0, 0, 0]), np.array([0, 0, 1.0]))
    turned = K.Plane(np.array([10.0, 5, 3]), np.array([0, 1.0, 0]), np.array([0, 0, 1.0]))
    under = K.Plane(np.array([0.0, 0, -2]), np.array([1.0, 0, 0]), np.array([0, 0, -1.0]))    # faces down
    side = K.Plane(np.zeros(3), np.array([0, 1.0, 0]), np.array([1.0, 0, 0]))
    a, b, c = DX.Geometry(), DX.Geometry(), DX.Geometry()
    a.line("cut", (0, 0), (1, 0))
    b.line("engrave", (0, 0), (1, 0))
    c.arc("cut", (0, 0), 1, 0, 90)
    out = DX.combine([(a, base), (b, turned), (c, under)])
    assert [r for r, _k, _d in out.items] == ["cut", "engrave", "cut"]
    assert np.allclose(out.items[0][2], ((0, 0), (1, 0)))
    assert np.allclose(out.items[1][2], ((10, 5), (10, 6)))          # turned's x is the model's y
    c0, r, a0, a1 = out.items[2][2]
    assert np.allclose(c0, (0, 0)) and np.isclose(a0 % 360, 270) and np.isclose(a1 % 360, 0)
    with pytest.raises(DX.DxfExportError, match="Item 2 is not parallel"):
        DX.combine([(a, base), (b, side)])


# =========================================================================== sources
def _sketch():
    s = Sketch()
    p1, p2, c = s.add_point(10, 0), s.add_point(10, 20), s.add_point(30, 30)
    ids = {"line": s.add_line(ORIGIN, p1), "circle": s.add_circle(c, 5.0)}
    a1, a2 = s.add_point(5, 0), s.add_point(0, 5)
    ids["arc"] = s.add_arc(ORIGIN, a1, a2)
    ids["construction"] = s.add_line(p1, p2, construction=True)
    ids["helper"] = s.add_line(ORIGIN, p2, construction=True)
    s.entities[ids["helper"]]["helper"] = True
    s.add_point_entity(s.add_point(50, 50))
    return s, ids


def test_sketch_geometry_keeps_curves_exact_and_leaves_construction_out_unless_given_a_role():
    s, _ids = _sketch()
    g = DX.sketch_geometry(s)
    kinds = {k: d for _r, k, d in g.items}
    assert sorted(k for _r, k, _d in g.items) == ["arc", "circle", "line"] and g.count("cut") == 3
    assert np.allclose(kinds["line"], ((0, 0), (10, 0)))
    assert np.allclose(kinds["circle"][0], (30, 30)) and kinds["circle"][1] == 5.0
    c, r, a0, a1 = kinds["arc"]
    assert np.allclose(c, (0, 0)) and np.isclose(r, 5) and np.isclose(a0, 0) and np.isclose(a1, 90)
    g = DX.sketch_geometry(s, "cut", construction_role="engrave")
    assert g.count("cut") == 3 and g.count("engrave") == 1                       # the helper line stays out
    (eng,) = [d for r, _k, d in g.items if r == "engrave"]
    assert np.allclose(eng, ((10, 0), (10, 20)))


def test_sketch_ellipse_spline_and_text_become_polylines():
    s = Sketch()
    c, a = s.add_point(0, 0), s.add_point(20, 0)
    s.add_ellipse(c, a, 8.0)
    s.add_spline([s.add_point(0, 30), s.add_point(10, 40), s.add_point(20, 30), s.add_point(30, 40)])
    g = DX.sketch_geometry(s, "engrave")
    (ell, ell_closed), (spl, spl_closed) = [d for _r, _k, d in g.items]
    assert ell_closed and not spl_closed and len(ell) >= 100
    r = np.array(ell)
    assert np.allclose((r[:, 0] / 20) ** 2 + (r[:, 1] / 8) ** 2, 1.0, atol=1e-6)     # on the ellipse
    assert np.allclose(spl[0], (0, 30)) and np.allclose(spl[-1], (30, 40))
    t = Sketch()
    t.add_text(t.add_point(5, 5), "A", size=10.0)
    g = DX.sketch_geometry(t)
    assert g.count() >= 2 and all(k == "poly" and d[1] for _r, k, d in g.items)      # the letter's outlines
    (lo, hi) = g.bounds()
    assert lo[0] >= 4.5 and lo[1] >= 4.5 and 3 < hi[1] - lo[1] < 12


def test_face_geometry_of_a_plate_face_with_a_hole():
    doc = block_doc(40, 20, 10)
    hid = sketch_feature(doc, lambda s: s.add_point_entity(s.add_point(6, 4)))
    add(doc, "hole", sketch=hid, standard="custom", diameter=4.0, end="through", style="simple")
    part = only_part(doc)
    top = next(f for f in K.faces(part.shape)
               if K.planar_face_plane(f) is not None and abs(K.face_info(f)["c"][2] - 10) < 1e-6)
    g = DX.face_geometry(top, "cut")
    kinds = sorted(k for _r, k, _d in g.items)
    assert kinds == ["circle", "line", "line", "line", "line"]
    (circle,) = [d for _r, k, d in g.items if k == "circle"]
    assert np.isclose(circle[1], 2.0)
    lengths = sorted(math.dist(*d) for _r, k, d in g.items if k == "line")
    assert np.allclose(lengths, [20, 20, 40, 40])
    lo, hi = g.bounds()
    assert np.allclose(np.subtract(hi, lo), (40, 20))
    side = next(f for f in K.faces(part.shape) if K.planar_face_plane(f) is None)       # the hole's wall
    with pytest.raises(DX.DxfExportError, match="curved"):
        DX.face_geometry(side)


def test_flat_pattern_takes_a_profile(tmp_path):
    from test_sheetmetal import base, edge
    shape, m = base()
    e = edge(m, m.panels[0], lambda a, b: abs(a[1]) < 1e-9 and abs(b[1]) < 1e-9)
    shape, m = SM.add_flange(shape, m, m.panels[0], *e, length=25, angle=90)
    fp = SM.flat_pattern(shape, m)
    n, nb = SM.flat_dxf(fp, str(tmp_path / "std.dxf"))
    _doc, ents = _read(tmp_path / "std.dxf")
    assert {e.dxf.layer for e in ents} == {"CUT", "BEND", "NOTES"} and nb == 1
    assert sum(e.dxf.layer == "CUT" for e in ents) == n
    assert SM.flat_dxf(fp, str(tmp_path / "k40.dxf"), profile=DX.K40_WHISPERER) == (n, nb)
    _doc, ents = _read(tmp_path / "k40.dxf")
    assert sorted({e.dxf.layer for e in ents}) == ["CUT", "ENGRAVE"]              # the bend line is engraved
    assert sum(e.dxf.layer == "ENGRAVE" for e in ents) == 1 and all(e.dxftype() != "TEXT" for e in ents)


# =========================================================================== the dialogs and the window
@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_export_dialog_offers_the_roles_the_profile_writes_and_remembers_the_profile(app):
    from freeshape.ui import export_dialog as ED
    st = _Settings()
    d = ED.DxfExportDialog(None, st, [("Sketch 1", "sketch"), ("Part 1 face", "face")])
    o = d.options()
    assert o["profile"] is DX.STANDARD and o["roles"] == ["cut", "cut"] and o["construction"] is None
    d.set_role(1, "engrave")
    d.set_construction("engrave")
    d.profile_combo.setCurrentText("K40 Whisperer")
    o = d.options()
    assert o["profile"] is DX.K40_WHISPERER and o["roles"] == ["cut", "engrave"] and o["construction"] == "engrave"
    d._accept()
    assert st[ED.DXF_PROFILE_KEY] == "K40 Whisperer"
    assert ED.DxfExportDialog(None, st, []).options() == {"profile": DX.K40_WHISPERER, "roles": [],
                                                          "construction": None}
    # a profile that writes no engraving does not offer it
    only_cut = DX.STANDARD.copy("Cut only")
    only_cut.roles["engrave"].skip = True
    st[ED.DXF_PROFILES_KEY] = DX.profiles_to_json([only_cut])
    d = ED.DxfExportDialog(None, st, [("Sketch 1", "sketch")])
    d.set_construction("engrave")
    d.profile_combo.setCurrentText("Cut only")
    assert [d.role_combos[0].itemData(i) for i in range(d.role_combos[0].count())] == ["cut"]
    assert d.options()["construction"] is None


def test_profile_editor_copies_a_built_in_profile_and_saves_the_copy(app):
    from freeshape.ui import export_dialog as ED
    st = _Settings()
    d = ED.DxfProfilesDialog(None, st, "K40 Whisperer")
    assert d.current() is DX.K40_WHISPERER and not d.editor.isEnabled() and not d.del_btn.isEnabled()
    p = d.duplicate()
    assert p.name == "K40 Whisperer copy" and d.current() is p and d.editor.isEnabled()
    d.name.setText("My laser")
    on, layer, colour, _lt = d.rows["cut"]
    colour.setCurrentIndex(colour.findData(3))
    layer.setText("OUTLINE")
    d.rows["bend"][0].setChecked(False)
    d.commit()
    (mine,) = ED.dxf_user_profiles(st)
    assert mine.name == "My laser" and mine.roles["cut"].colour == 3 and mine.roles["cut"].layer == "OUTLINE"
    assert mine.style("bend") is None and mine.format == "r12" and mine.entity_colour
    assert DX.K40_WHISPERER.roles["cut"].colour == 1 and DX.K40_WHISPERER.name == "K40 Whisperer"
    combo = ED.DxfProfileCombo(st)
    assert [combo.itemText(i) for i in range(combo.count())] == ["Standard", "K40 Whisperer", "My laser"]
    d2 = ED.DxfProfilesDialog(None, st, "My laser")
    d2.remove_profile()
    d2.commit()
    assert ED.dxf_user_profiles(st) == []


@pytest.fixture
def win(app, tmp_path, monkeypatch):
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    doc, ids = plate_studio(W, D)
    w._add_tab(doc)
    w.resize(900, 700)
    w.show()
    app.processEvents()
    assert w.doc is doc
    w.ids = ids
    yield w
    w.deleteLater()


def _top_pick(w):
    part = only_part(w.doc)
    i = next(i for i, f in enumerate(K.faces(part.shape))
             if K.planar_face_plane(f) is not None and abs(K.face_info(f)["c"][2] - H) < 1e-6
             and abs(K.planar_face_plane(f).normal[2]) > 0.9)
    return Pick("face", (part.id, i))


def test_window_exports_a_selected_sketch_and_face_in_one_dxf(win, tmp_path):
    sid = win.ids["sketch"]
    line = next(iter(win.doc.state.sketches[sid].sketch.entities))
    picks = [Pick("region", (sid, 0)), Pick("sketch_line", (sid, line)), _top_pick(win)]
    win.viewport.set_selection(picks)
    sources = win.dxf_sources()
    assert [s["kind"] for s in sources] == ["sketch", "face"]                    # the sketch once, whole
    assert sources[0]["label"] == win.doc.feature(sid)["name"]
    path = tmp_path / "plate.dxf"
    counts = win.write_dxf(sources, str(path), DX.K40_WHISPERER, ["engrave", "cut"])
    assert counts == {"engrave": 4, "cut": 4}
    _doc, ents = _read(path)
    assert sorted(e.dxf.color for e in ents) == [1] * 4 + [5] * 4
    for colour in (1, 5):                        # the face sits over the sketch it was extruded from
        pts = np.array([p for e in ents if e.dxf.color == colour for p in ((e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y))])
        assert np.allclose(pts.min(0), (0, 0), atol=1e-6) and np.allclose(pts.max(0), (W, D), atol=1e-6)
    assert win.dxf_sources([], [sid])[0]["kind"] == "sketch"                     # from the feature list
    assert win.dxf_sources([]) == []
    assert any(c.name == "Export DXF" for c in win._palette_commands())
    assert win._dxf_picks(picks[2]) == picks and win._is_flat_face(picks[2])
