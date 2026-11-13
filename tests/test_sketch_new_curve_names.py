"""I2.2: element names for the three curve types of upstream 2.0 (spline, ellipse, text).

A sketch curve names its edge by its entity id (naming.name_sketch, docs/reference/element-naming.md § 4). A text
entity gives one edge per glyph outline, labelled "<id>_<n>" in the order the text shape lists its edges. These
tests extrude each curve and read the names off the part, the way the app does.

Text outlines come from the installed font. With no font at all text_shape raises, make_sketch_edges drops the
entity and the sketch has no text regions, so nothing is named wrongly. With a substitute font the outlines, and so
the glyph labels, are the substitute's.
"""
import copy
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import sketchref as S  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402

from conftest import TOP, add, region_refs, sketch_feature  # noqa: E402

SPLINE_PTS = [(0, 0), (10, 5), (20, 0), (10, -8)]
LABEL = re.compile(r"(?:^|[;(|])((?:s|e|t)\d+(?:_\d+)?)(?:v\d+)?;SKT")
POSITIONAL = re.compile(r"(?:^|[;(|])n\d+(?:v\d+)?;")


def build_curves(s):
    ids = {}
    ids["spline"] = s.add_spline([s.add_point(x, y) for x, y in SPLINE_PTS], closed=True)
    ids["ellipse"] = s.add_ellipse(s.add_point(50, 0), s.add_point(60, 0), 5)
    ids["text"] = s.add_text(s.add_point(0, 30), "Hi", size=10)
    return ids


def curves_doc(build=build_curves):
    """A Document with one sketch of `build(sketch)` and one extrude per region. Returns (doc, sketch id, ids)."""
    doc = Document()
    ids = {}
    sid = sketch_feature(doc, lambda s: ids.update(build(s) or {}), TOP)
    for r in region_refs(doc, sid):
        add(doc, "extrude", regions=[r], depth=5.0, op="new")
    return doc, sid, ids


def element_names(doc, pid):
    nm = doc.state.named(pid)
    assert nm is not None, f"part {pid} has no element map"
    return [n for _s, n in nm.named("F")], [n for _s, n in nm.named("E")], nm


def labels_of(names):
    return {m for n in names for m in LABEL.findall(n)}


def part_names(doc):
    return {pid: element_names(doc, pid) for pid in doc.state.parts}


def test_extruded_curves_are_named_from_their_entity_ids():
    doc, sid, ids = curves_doc()
    sk = doc.state.sketches[sid]
    assert sk.named is not None, "the sketch could not name its regions"
    assert all(sk.region_names)
    # spline, ellipse, "H" and the stem and dot of "i": five regions, five parts
    assert len(doc.state.parts) == 5
    seen = set()
    for pid, part in doc.state.parts.items():
        face_names, edge_names, nm = element_names(doc, pid)
        assert len(face_names) == len(K.faces(part.shape)), pid          # every face is named
        assert len(edge_names) == len(K.edges(part.shape)), pid
        for n in face_names + edge_names:
            assert not POSITIONAL.search(n), f"{pid}: {n} is named by position"
        labels = labels_of(face_names)
        assert labels, f"{pid}: no face name carries an entity id"
        for lab in labels:
            assert lab.split("_")[0] in ids.values(), f"{pid}: {lab} is not an entity of the sketch"
        seen |= {lab.split("_")[0] for lab in labels}
    assert seen == set(ids.values())
    # the curved side faces of the spline and the ellipse come from their own edge
    for kind in ("spline", "ellipse"):
        side = [pid for pid in doc.state.parts if labels_of(element_names(doc, pid)[0]) == {ids[kind]}]
        assert len(side) == 1, kind


def test_text_glyph_labels_differ_per_letter_and_repeat_on_a_second_build():
    def text_only(s):
        return {"text": s.add_text(s.add_point(0, 0), "Hi", size=10)}

    def plane_pairs():
        K._FONT_CACHE.clear()                                  # a build from scratch, as after a restart
        s = Sketch()
        tid = text_only(s)["text"]
        pairs = K.make_sketch_edges(K.std_plane("Top"), s.curves(), with_ids=True)
        return tid, [(i, tuple(np.round(K.edge_info(e)["c"], 6))) for i, e in pairs]

    tid, first = plane_pairs()
    for _ in range(5):                                         # the font builder's own order varies between builds
        assert plane_pairs()[1] == first                       # same labels on the same outlines, same order
    labels = [i for i, _ in first]
    assert len(labels) > 2 and len(set(labels)) == len(labels)
    assert labels == [f"{tid}_{n}" for n in range(len(labels))]

    # through the model: "H" and "i" are different parts and no glyph label is shared
    def names():
        K._FONT_CACHE.clear()
        doc, _sid, _ids = curves_doc(text_only)
        return sorted((pid, tuple(sorted(f)), tuple(sorted(e))) for pid, (f, e, _nm) in part_names(doc).items())

    a = names()
    for _ in range(3):
        assert names() == a
    per_part = [labels_of(f) for _pid, f, _e in a]
    assert len(per_part) == 3                                  # H, the stem of i, the dot of i
    assert all(per_part)
    for i, x in enumerate(per_part):
        for y in per_part[i + 1:]:
            assert not x & y
    assert len({n for _pid, f, _e in a for n in f}) == sum(len(f) for _pid, f, _e in a)


def edit_sketch_first(doc, sid, add_entity):
    """Add an entity that sorts before every existing one in the sketch's entity table."""
    f = copy.deepcopy(doc.feature(sid))
    s = Sketch.from_dict(f["params"]["sketch"])
    old = dict(s.entities)
    s.entities = {}
    add_entity(s)
    s.entities.update(old)
    assert s.solve()
    f["params"]["sketch"] = s.to_dict()
    doc.replace_feature(f)


def test_fillet_on_an_ellipse_edge_survives_an_entity_added_before_it():
    def ellipse_with_circle(s):
        s.add_circle(s.add_point(100, 0), 4)                   # regions: the circle first, so ids and order shift
        return {"ellipse": s.add_ellipse(s.add_point(50, 0), s.add_point(60, 0), 5)}

    doc = Document()
    ids = {}
    sid = sketch_feature(doc, lambda s: ids.update(ellipse_with_circle(s)), TOP)
    ell = next(r for r in region_refs(doc, sid, lambda i: abs(i["area"] - np.pi * 10 * 5) < 1))
    add(doc, "extrude", regions=[ell], depth=5.0, op="new")
    pid = next(iter(doc.state.parts))
    part = doc.state.parts[pid]
    top = [e for e in K.edges(part.shape)
           if K.edge_info(e)["type"] != "line" and abs(K.edge_info(e)["c"][2] - 5.0) < 1e-6]
    assert len(top) == 1
    ref = F.make_ref(pid, top[0], "edge", part.shape, doc.state.named(pid))
    assert ref.get("name") and ids["ellipse"] in ref["name"]      # the edge is named from the ellipse's id
    fil = add(doc, "fillet", edges=[ref], radius=1.0)
    assert not doc.errors and not doc.state.recovered
    assert fil["params"]["edges"][0]["name"] == ref["name"]
    part = doc.state.parts[pid]
    nm = doc.state.named(pid)
    # the rounded face, named from the ellipse edge it was made from, is the element the edit must not lose
    rounds = [f for f in K.faces(part.shape) if ids["ellipse"] in (nm.name_of(f) or "") and "FLT" in nm.name_of(f)]
    assert rounds                                                 # the ellipse's two vertices split the round
    round_refs = [F.make_ref(pid, f, "face", part.shape, nm) for f in rounds]
    assert all(r.get("name") for r in round_refs) and len({r["name"] for r in round_refs}) == len(rounds)
    vol = K.volume(part.shape)

    edit_sketch_first(doc, sid, lambda s: s.add_circle(s.add_point(30, 0), 4))

    assert not doc.errors, doc.errors
    assert not doc.state.recovered
    assert [f["id"] for f in doc.features if f["type"] == "fillet"] == [fil["id"]]
    for old, r in zip(rounds, round_refs):
        pid2, sub = F.resolve(doc.state, r, "face", fid="probe")
        assert pid2 == pid
        assert doc.state.named(pid).name_of(sub) == r["name"]
        assert np.allclose(K.face_info(sub)["c"], K.face_info(old)["c"], atol=1e-6)
    assert abs(K.volume(doc.state.parts[pid].shape) - vol) < 1e-6


PLANES = [{"origin": [0, 0, 0], "xdir": [1, 0, 0], "normal": [0, 0, 1]},
          {"origin": [3, 4, 5], "xdir": [0, 1, 0], "normal": [1, 0, 0]}]


def to_world(plane, uv):
    o, x, n = (np.array(plane[k], float) for k in ("origin", "xdir", "normal"))
    return o + uv[0] * x + uv[1] * np.cross(n, x)


@pytest.mark.parametrize("plane", PLANES)
def test_new_sketch_reference_roles_resolve_to_their_points(plane):
    s = Sketch()
    pts = [s.add_point(x, y) for x, y in SPLINE_PTS]
    sp = s.add_spline(pts)
    closed = s.add_spline(pts, closed=True)
    ell = s.add_ellipse(s.add_point(50, 2), s.add_point(60, 2), 5)
    txt = s.add_text(s.add_point(7, 30), "Hi", size=10)
    d = s.to_dict()

    assert S.roles(d, sp) == ["end1", "end2"]
    assert S.roles(d, ell) == ["centre"]
    assert S.roles(d, txt) == ["point"]
    want = [(sp, "end1", SPLINE_PTS[0]), (sp, "end2", SPLINE_PTS[-1]), (closed, "end1", SPLINE_PTS[0]),
            (closed, "end2", SPLINE_PTS[-1]), (ell, "centre", (50, 2)), (txt, "point", (7, 30))]
    for eid, role, uv in want:
        f = S.frame(plane, d, eid, role)
        assert np.allclose(f["o"], to_world(plane, uv)), (eid, role)
        assert np.allclose(f["z"], plane["normal"]) and np.allclose(f["x"], plane["xdir"])
        assert S.describe(d, eid, role) == {"etype": d["entities"][eid]["type"], "sig": {"at": [float(uv[0]), float(uv[1])]}}
        S.check(d, {"entity": eid, "etype": d["entities"][eid]["type"]})
    for eid, role in ((ell, "end1"), (sp, "centre"), (txt, "end2"), (sp, "mid")):
        with pytest.raises(K.KernelError):
            S.frame(plane, d, eid, role)


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_search_tools_lists_spline_ellipse_and_text_while_sketching(app):
    from PySide6.QtWidgets import QApplication
    from freeshape.ui.mainwindow import MainWindow
    from freeshape.ui.palette import search_commands
    from freeshape.ui.viewport import Pick
    w = MainWindow()
    w.viewport.set_selection([Pick("plane", ("std", "Front"))])
    w.start_sketch()
    assert w.mode == "sketch"
    cmds = w._palette_commands()
    got = [c.name for c in cmds]
    for n in ("Spline", "Ellipse", "Text"):
        assert n in got, n
    for query, name in (("spline", "Spline"), ("ellipse", "Ellipse"), ("oval", "Ellipse"), ("text", "Text"),
                        ("font", "Text")):
        assert search_commands(cmds, query)[0].name == name, query
    for name, tool in (("Spline", "spline"), ("Ellipse", "ellipse"), ("Text", "text")):
        next(c for c in cmds if c.name == name).action()
        QApplication.processEvents()
        assert w.sketcher.tool == tool, name
