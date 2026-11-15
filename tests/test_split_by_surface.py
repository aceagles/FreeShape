"""I2.10: Split a part with a surface body, as drawn. A surface part is a tool kind of its own: used bounded,
never extended; it must pass completely through the part. keep_tools keeps it; a face tool can be trimmed."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from conftest import TOP, add, rect_lines, region_refs, sketch_feature, try_add  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.document import PartStudioDoc  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402

PASS_MSG = ("The surface must pass completely through the part - make it larger, or pick one of its faces "
            "to use the extended face")


def s_curve(s):
    s.add_spline([s.add_point(*q) for q in ((20, -5), (15, 5), (25, 15), (20, 25))])


def l_curve(s):
    p = [s.add_point(-5, 5), s.add_point(15, 5), s.add_point(15, 25)]
    s.add_line(p[0], p[1])
    s.add_line(p[1], p[2])


def curves(doc, sid):
    sk = doc.state.sketches[sid].sketch
    return [{"kind": "sketch_chain", "sketch": sid, "entity": i} for i, e in sk.entities.items()
            if e["type"] in ("line", "spline")][:1]


def block(doc, w=40.0, d=20.0, h=10.0, x0=0.0, y0=0.0):
    sid = sketch_feature(doc, lambda s: rect_lines(s, x0, y0, w, d), TOP)
    add(doc, "extrude", regions=region_refs(doc, sid), depth=h, op="new")


def surface_feature(doc, build=s_curve, **kw):
    sid = sketch_feature(doc, build, TOP)
    kw.setdefault("end", "symmetric")
    kw.setdefault("depth", 30)
    return add(doc, "extrude", creation="surface", curves=curves(doc, sid), op="new", **kw)


def part_ids(doc):
    solids = [p.id for p in doc.state.parts.values() if not K.is_surface(p.shape)]
    surfs = [p.id for p in doc.state.parts.values() if K.is_surface(p.shape)]
    return solids, surfs


def split(doc, surf, parts=None, **kw):
    solids, _ = part_ids(doc)
    tool = {"kind": "surface", "ref": surf, "both": kw.pop("both", True), "flip": kw.pop("flip", False)}
    return try_add(doc, "split", parts=parts or solids, tools=[tool], **kw)


def s_doc(**kw):
    doc = PartStudioDoc()
    block(doc)
    surface_feature(doc, **kw)
    return doc


def centroid(p):
    return K.mass_props(p.shape)["centroid"]


def test_surface_splits_a_block_into_two_parts_and_the_surface_goes():
    doc = s_doc()
    _, (surf,) = part_ids(doc)
    f, err = split(doc, surf)
    assert not err, err
    solids, surfs = part_ids(doc)
    assert len(solids) == 2 and surfs == []
    assert sum(K.volume(doc.state.parts[i].shape) for i in solids) == pytest.approx(8000, rel=1e-6)


def _vertex(p):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    return BRepBuilderAPI_MakeVertex(K._pnt(p)).Vertex()


def test_cut_face_is_the_surfaces_own_face():
    doc = s_doc()
    _, (surf,) = part_ids(doc)
    tool = doc.state.parts[surf].shape
    tface = K.faces(tool)[0]
    f, err = split(doc, surf)
    assert not err, err
    solids, _ = part_ids(doc)
    for i in solids:
        sh = doc.state.parts[i].shape
        cut = [fc for fc in K.faces(sh) if all(0 < c < m for c, m in zip(K.face_info(fc)["c"], (40, 20, 10)))]
        assert len(cut) == 1
        assert K.face_info(cut[0])["type"] == K.face_info(tface)["type"]
        for q in (K.face_info(cut[0])["c"],):
            assert K._dist(_vertex(q), tool) == pytest.approx(0, abs=1e-5)


def test_keep_tools_leaves_two_solids_and_the_surface():
    doc = s_doc()
    _, (surf,) = part_ids(doc)
    f, err = split(doc, surf, keep_tools=True)
    assert not err, err
    solids, surfs = part_ids(doc)
    assert len(solids) == 2 and surfs == [surf]


def test_a_short_surface_fails_and_leaves_the_part_whole():
    doc = PartStudioDoc()
    block(doc)
    surface_feature(doc, end="blind", depth=5)
    solids, (surf,) = part_ids(doc)
    before = K.volume(doc.state.parts[solids[0]].shape)
    f, err = split(doc, surf)
    assert err == PASS_MSG
    solids, surfs = part_ids(doc)
    assert len(solids) == 1 and surfs == [surf]
    assert K.volume(doc.state.parts[solids[0]].shape) == pytest.approx(before)


def test_a_surface_exactly_the_height_of_the_block_splits_it():
    doc = PartStudioDoc()
    block(doc)
    surface_feature(doc, end="blind", depth=10)
    _, (surf,) = part_ids(doc)
    f, err = split(doc, surf)
    assert not err, err
    assert len(part_ids(doc)[0]) == 2


def test_the_same_short_surface_as_a_face_tool_extends_unless_trimmed():
    for trim, ok in ((False, True), (True, False)):
        doc = PartStudioDoc()
        block(doc)
        surface_feature(doc, build=l_curve_one_wall, end="blind", depth=5)
        solids, (surf,) = part_ids(doc)
        face = F.make_ref(surf, K.faces(doc.state.parts[surf].shape)[0], "face", doc.state.parts[surf].shape,
                          doc.state.named(surf))
        tool = {"kind": "face", "ref": face, "both": True, "flip": False}
        if trim:
            tool["trim"] = True
        f, err = try_add(doc, "split", parts=solids, tools=[tool])
        if ok:
            assert not err, err
            assert len([p for p in doc.state.parts.values() if not K.is_surface(p.shape)]) == 2
        else:
            assert err == PASS_MSG
            assert len([p for p in doc.state.parts.values() if not K.is_surface(p.shape)]) == 1


def l_curve_one_wall(s):
    s.add_line(s.add_point(20, -5), s.add_point(20, 25))


def test_keep_one_side_follows_flip():
    kept = {}
    for flip in (False, True):
        doc = s_doc()
        _, (surf,) = part_ids(doc)
        tool = doc.state.parts[surf].shape
        f, err = split(doc, surf, both=False, flip=flip)
        assert not err, err
        solids, surfs = part_ids(doc)
        assert len(solids) == 1 and surfs == []
        kept[flip] = centroid(doc.state.parts[solids[0]])
    # the S-curve runs along +y, so its normal points along x: flip false keeps the side it points to
    assert kept[False][0] > 20 > kept[True][0] or kept[True][0] > 20 > kept[False][0]
    doc = s_doc()
    _, (surf,) = part_ids(doc)
    n = K._tool_normal_at(np.array([20.0, 10.0, 5.0]), doc.state.parts[surf].shape)[2]
    assert (kept[False][0] > 20) == (n[0] > 0)


def test_an_l_shaped_surface_splits_and_its_cut_faces_keep_their_names():
    def run(depth):
        doc = PartStudioDoc()
        block(doc)
        sid = sketch_feature(doc, l_curve, TOP)
        sk = doc.state.sketches[sid].sketch
        first = next(i for i, e in sk.entities.items() if e["type"] == "line")
        ex = add(doc, "extrude", creation="surface", curves=[{"kind": "sketch_chain", "sketch": sid, "entity": first}],
                 op="new", end="symmetric", depth=depth)
        _, (surf,) = part_ids(doc)
        f, err = split(doc, surf)
        return doc, ex, f, err

    doc, ex, f, err = run(30)
    assert not err, err
    solids, surfs = part_ids(doc)
    assert len(solids) == 2 and surfs == []

    def cut_names(doc):
        out = []
        for i in part_ids(doc)[0]:
            n = doc.state.named(i)
            for fc in K.faces(doc.state.parts[i].shape):
                if all(0 < c < m for c, m in zip(K.face_info(fc)["c"], (40, 20, 10))):
                    out.append(n.name_of(fc))
        return sorted(out, key=str)
    names = cut_names(doc)
    assert len(names) == 4 and all(names), names        # the two cut faces, once on each piece
    assert len(set(names)) == 4 or len(set(names)) == 2
    ex["params"]["depth"] = 50
    doc.replace_feature(ex)
    assert not doc.errors, doc.errors
    assert cut_names(doc) == names


def test_a_part_the_surface_misses_is_left_alone():
    doc = PartStudioDoc()
    block(doc)
    sid = sketch_feature(doc, lambda s: rect_lines(s, 100, 0, 40, 20), TOP)
    add(doc, "extrude", regions=region_refs(doc, sid), depth=10, op="new")
    surface_feature(doc)
    solids, (surf,) = part_ids(doc)
    far = next(i for i in solids if K.bbox(doc.state.parts[i].shape)[0][0] > 50)
    far_shape = doc.state.parts[far].shape
    f, err = split(doc, surf)
    assert not err, err
    solids, surfs = part_ids(doc)
    assert len(solids) == 3 and surfs == []
    assert doc.state.parts[far].shape.IsSame(far_shape)


def test_piece_side_of_a_two_face_tool_is_defined_by_its_first_face():
    doc = PartStudioDoc()
    block(doc)
    surface_feature(doc, build=l_curve)
    solids, (surf,) = part_ids(doc)
    tool = doc.state.parts[surf].shape
    pieces = K.split_with(doc.state.parts[solids[0]].shape, [tool])
    assert len(pieces) == 2
    sides = {K.piece_side(pc, tool) > 0 for pc in pieces}
    assert sides == {True, False}                       # one piece on each side, whichever face it touches


# ---------------------------------------------------------------------------- the dialog, in engine mode

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


def test_split_with_a_surface_part_in_the_engine_window(engine_win):
    w = engine_win
    se = w.studio_engine
    doc = w.doc
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 40, 20), TOP)
    assert se.settle(180)                        # the worker builds the sketch; the window then has it to pick
    add(doc, "extrude", regions=region_refs(doc, sid), depth=10, op="new")
    sid = sketch_feature(doc, s_curve, TOP)
    assert se.settle(180)
    add(doc, "extrude", creation="surface", curves=curves(doc, sid), op="new", end="symmetric", depth=30)
    assert se.settle(180)
    assert not doc.errors, doc.errors
    solids, (surf,) = part_ids(doc)
    w.start_feature("split")
    dlg = w.dialog
    assert "keep_tools" in dlg.widgets and "tools" in dlg.fields
    dlg.activate(dlg.fields["parts"])
    dlg.handle_pick(Pick("part", (solids[0],)))
    dlg.activate(dlg.fields["tools"])
    dlg.handle_pick(Pick("part", (surf,)))
    assert dlg.feature["params"]["tools"][0]["kind"] == "surface"
    w.commit_dialog()
    assert se.settle(180)
    assert not doc.errors, doc.errors
    solids, surfs = part_ids(doc)
    assert len(solids) == 2 and surfs == []
    assert sum(K.volume(doc.state.parts[i].shape) for i in solids) == pytest.approx(8000, rel=1e-6)


def test_dialog_offers_keep_tools_and_trim_and_a_plain_split_saves_no_new_key(engine_win):
    w = engine_win
    doc = w.doc
    solids, _ = part_ids(doc)
    w.start_feature("split")
    dlg = w.dialog
    assert "keep_tools" in dlg.widgets
    assert dlg.widgets["keep_tools"].text() == "Keep tools"
    p = dlg.feature["params"]
    assert "keep_tools" not in p
    dlg.fields["tools"].toggle(("plane", {"kind": "std", "name": "Right"}))
    dlg._fields_changed()
    p = dlg.feature["params"]
    assert set(p["tools"][0]) == {"kind", "ref", "both", "flip"}
    from PySide6.QtWidgets import QCheckBox
    dlg.fields["tools"].items = [("face", {"part": "x"})]
    # a face tool gets a Trim to face boundaries box
    p["tools"] = [{"kind": "face", "ref": {"part": "x"}, "both": True, "flip": False}]
    dlg._split_sig = None
    dlg._split_rows()
    assert any(c.text() == "Trim to face boundaries" for c in dlg._split_box.findChildren(QCheckBox))


def test_command_search_finds_split_by_the_new_keywords(engine_win):
    from freeshape.ui.palette import search_commands
    for words in ("split with surface", "cut with surface"):
        assert [c.name for c in search_commands(engine_win._palette_commands(), words)][:1] == ["Split"], words
