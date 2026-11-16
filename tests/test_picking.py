"""Picking a sketch's line where a closed area is wanted (extrude, loft …) uses the area it bounds."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape import kernel as K  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402


def test_line_picks_the_region_it_bounds():
    from freeshape.ui.dialogs import region_touching
    doc = Document()
    s = Sketch()
    p = [s.add_point(0, 0), s.add_point(60, 0), s.add_point(60, 40), s.add_point(0, 40)]
    lines = [s.add_line(a, b) for a, b in zip(p, p[1:] + p[:1])]
    hole = s.add_circle(s.add_point(20, 20), 5)
    open_line = s.add_line(s.add_point(80, 0), s.add_point(90, 10))
    s.solve()
    f = doc.make_feature("sketch")
    f["params"]["sketch"] = s.to_dict()
    doc.add_feature(f)
    sr = doc.state.sketches[f["id"]]
    area = lambda ri: K.face_info(sr.regions[ri])["area"]
    outer = region_touching(sr, lines[0])
    inner = region_touching(sr, hole)
    assert outer is not None and inner is not None and outer != inner
    assert abs(area(outer) - (60 * 40 - 3.141592653589793 * 25)) < 1e-3     # the plate around the hole
    assert abs(area(inner) - 3.141592653589793 * 25) < 1e-3                 # the hole's own disc
    assert region_touching(sr, open_line) is None                            # not part of a closed shape


def test_deleting_a_feature_shows_its_sketches_again():
    """Sketches are hidden when a feature uses them; deleting the feature brings them back (unless another feature
    still uses them)."""
    from freeshape import features as F
    doc = Document()
    s = Sketch()
    s.add_circle(s.add_point(0, 0), 10)
    s.solve()
    f = doc.make_feature("sketch")
    f["params"]["sketch"] = s.to_dict()
    doc.add_feature(f)
    sr = doc.state.sketches[f["id"]]
    exts = []
    for depth in (5, 8):
        e = doc.make_feature("extrude")
        e["params"].update(regions=[F.region_ref(sr, f["id"], sr.regions[0])], depth=depth, op="new")
        doc.add_feature(e)
        exts.append(e["id"])
    doc.hidden_features.add(f["id"])
    doc.delete_features({exts[0]})
    assert f["id"] in doc.hidden_features          # the other extrude still uses it
    doc.delete_features({exts[1]})
    assert f["id"] not in doc.hidden_features      # nothing uses it now: shown again
    doc.undo()
    assert f["id"] in doc.hidden_features          # undo puts it back as it was


def test_too_big_fillet_is_an_error_not_a_broken_part():
    """OpenCascade can 'succeed' with overlapping rounds; FreeShape must refuse and say what would fit."""
    import math
    import pytest
    cyl = K.cylinder((0, 0, 0), (0, 0, 1), 50, 80)
    ends = [e for e in K.edges(cyl) if abs(K.edge_info(e).get("len", 0) - 2 * math.pi * 50) < 1e-3]
    ok = K.fillet(cyl, ends, 30)
    assert K.is_valid(ok) and len(K.solids(ok)) == 1
    with pytest.raises(K.KernelError, match="largest that works here is about 39"):
        K.fillet(cyl, ends, 45)


def test_scope_survives_the_dialog_round_trip():
    """The Merge scope field's part ids go to params and back unchanged."""
    from freeshape.ui.dialogs import SCHEMAS, params_to_fields, fields_to_params
    for ftype in ("extrude", "revolve", "sweep", "loft"):
        assert any(s.get("key") == "scope" for s in SCHEMAS[ftype]), ftype
        p = {"op": "add", "scope": ["part2", "part3"]}
        fields = params_to_fields(ftype, p)
        assert fields["scope"] == [("part", "part2"), ("part", "part3")]
        out = {}
        fields_to_params(ftype, fields, out)
        assert out["scope"] == ["part2", "part3"]
        fields_to_params(ftype, {}, out)
        assert out["scope"] == []
    spec = next(s for s in SCHEMAS["extrude"] if s.get("key") == "scope")
    assert spec["visible"]({"op": "remove"}) and not spec["visible"]({"op": "new"})


# ---------------------------------------------------------------------- I4.3: freeshape/picking.py, Qt-free

import subprocess  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from conftest import EXAMPLES_DIR  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import picking as PK  # noqa: E402
from freeshape.engine import InProcessEngine  # noqa: E402
from freeshape.engine.encode import element_names  # noqa: E402

BRACKET = os.path.join(EXAMPLES_DIR, "bracket.fshape")


def _bracket():
    """(engine, tab, ModelState) of the bracket, built in this process. The state carries the engine's name maps
    the way the window's state does (`engine_names`)."""
    eng = InProcessEngine()
    tab = eng.open_tab(BRACKET)
    eng.outputs(tab)
    st = eng._get(tab).doc.state
    st.engine_names = {pid: element_names(SimpleNamespace(shape=p.shape, named=st.named(pid)))
                       for pid, p in st.parts.items()}
    return eng, tab, st


def _vp(st):
    """What a viewport shows, as a stand-in: part shapes and sketches, nothing else."""
    return SimpleNamespace(part_shapes={pid: p.shape for pid, p in st.parts.items()},
                           sketches=[(fid, sr, None) for fid, sr in st.sketches.items()])


def _desktop_and_new(st, kind, key, want):
    """The item the desktop's pick_to_item gives and the one picking.make_item gives, both as plain data."""
    old = PK.jsonable(pick_to_item(_vp(st), Pick(kind, key), set(want), st))
    new = PK.jsonable(PK.make_item({p: q.shape for p, q in st.parts.items()}, st.sketches, kind, key, set(want),
                                   st.named, getattr(st, "engine_names", None)))
    return old, new


from freeshape.ui.dialogs import pick_to_item  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402


def test_picking_imports_no_qt():
    code = ("import sys, freeshape.picking, freeshape.engine.executor; "
            "bad = [m for m in sys.modules if m.split('.')[0] in ('PySide6', 'PyQt5', 'PyQt6', 'shiboken6')]; "
            "print('QT:' + ','.join(bad))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                         cwd=os.path.join(os.path.dirname(__file__), ".."))
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("QT:"), out.stdout


def test_face_edge_and_vertex_picks_give_what_pick_to_item_gives():
    _eng, _tab, st = _bracket()
    (pid, part), = [(p, q) for p, q in st.parts.items()][:1]
    nf, ne, nv = len(K.faces(part.shape)), len(K.edges(part.shape)), len(K.vertex_points(part.shape))
    seen = set()
    for i in range(nf):
        for want in ({"face"}, {"edge"}, {"plane"}, {"axis"}, {"element"}, {"part"}, {"plane", "axis"}, {"vertex"}):
            old, new = _desktop_and_new(st, "face", (pid, i), want)
            assert old == new, (i, want)
            seen.add((old and old[0], tuple(sorted(want))))
    for i in range(ne):
        for want in ({"edge"}, {"axis"}, {"path"}, {"element"}, {"face"}):
            old, new = _desktop_and_new(st, "edge", (pid, i), want)
            assert old == new, (i, want)
            seen.add((old and old[0], tuple(sorted(want))))
    for i in range(nv):
        old, new = _desktop_and_new(st, "vertex", (pid, i), {"vertex"})
        assert old == new and old[0] == "vertex" and old[1]["name"], i
    assert _desktop_and_new(st, "part", (pid,), {"part"}) == (["part", pid], ["part", pid])
    # the branches really were reached: a face gives face / edges / plane / axis / part, an edge gives edge / axis
    kinds = {k for k, _ in seen if k}
    assert {"face", "edges", "plane", "axis", "part", "edge", "path"} <= kinds, kinds


def test_make_item_names_come_from_the_engine_name_map_by_one_based_index():
    _eng, _tab, st = _bracket()
    pid = next(iter(st.parts))
    for i in (0, 1, 5):
        _, ref = PK.make_item({pid: st.parts[pid].shape}, {}, "face", (pid, i), {"face"}, st.named, st.engine_names)
        assert ref["name"] == st.engine_names[pid]["face"][i + 1]
    # without the maps and without a state: a signature-only reference
    _, bare = PK.make_item({pid: st.parts[pid].shape}, {}, "face", (pid, 0), {"face"})
    assert "name" not in bare and bare["kind"] == "face"


def test_a_sketch_region_pick_gives_the_region_ref_region_touching_finds():
    _eng, _tab, st = _bracket()
    checked = 0
    for fid, sr in st.sketches.items():
        for ri in range(len(sr.regions)):       # a region picked directly
            old, new = _desktop_and_new(st, "region", (fid, ri), {"region"})
            assert old == new == ["region", PK.jsonable(F.region_ref(sr, fid, sr.regions[ri]))]
            checked += 1
        for eid, e in sr.sketch.entities.items():   # a line or circle picked where a region is wanted
            ri = PK.region_touching(sr, eid)
            old, new = _desktop_and_new(st, "sketch_line", (fid, eid), {"region"})
            assert old == new
            if ri is None:
                assert new is None
            else:
                assert new == ["region", PK.jsonable(F.region_ref(sr, fid, sr.regions[ri]))]
                checked += 1
    assert checked >= 3
    from freeshape.ui import dialogs
    assert dialogs.region_touching is PK.region_touching       # the desktop imports it from its new home


def _sketch_doc():
    """A Document with one sketch: a plate with a hole (circle), an open line and a point entity."""
    doc = Document()
    s = Sketch()
    p = [s.add_point(0, 0), s.add_point(60, 0), s.add_point(60, 40), s.add_point(0, 40)]
    lines = [s.add_line(a, b) for a, b in zip(p, p[1:] + p[:1])]
    hole = s.add_circle(s.add_point(20, 20), 5)
    open_line = s.add_line(s.add_point(80, 0), s.add_point(90, 10))
    pt = s.add_point_entity(s.add_point(30, 30))
    s.solve()
    f = doc.make_feature("sketch")
    f["params"]["sketch"] = s.to_dict()
    doc.add_feature(f)
    return doc, f["id"], {"line": lines[0], "hole": hole, "open": open_line, "point": pt}


def test_every_branch_of_the_conversion_that_depends_on_the_wanted_kinds():
    doc, fid, ids = _sketch_doc()
    st = doc.state
    sk = lambda kind, eid, want: _desktop_and_new(st, kind, (fid, eid), want)   # noqa: E731
    # a circle picked as an axis is a sketch_circle, a line a sketch_line
    for eid, want in ((ids["hole"], {"axis"}), (ids["line"], {"axis"})):
        old, new = sk("sketch_line", eid, want)
        assert old == new
    assert sk("sketch_line", ids["hole"], {"axis"})[1] == ["axis", {"kind": "sketch_circle", "sketch": fid,
                                                                    "entity": ids["hole"]}]
    assert sk("sketch_line", ids["line"], {"axis"})[1] == ["axis", {"kind": "sketch_line", "sketch": fid,
                                                                    "line": ids["line"]}]
    # etype is written only when the Plane tool asks too (axis_connector)
    plain = sk("sketch_line", ids["hole"], {"sketch_line"})
    plane = sk("sketch_line", ids["hole"], {"sketch_line", "axis_connector"})
    assert plain[0] == plain[1] and "etype" not in plain[1][1]
    assert plane[0] == plane[1] and plane[1][1]["etype"] == "circle"
    # a line that bounds nothing: no region, and a path is a chain
    assert sk("sketch_line", ids["open"], {"region"}) == (None, None)
    old, new = sk("sketch_line", ids["open"], {"path"})
    assert old == new == ["path", {"kind": "sketch_chain", "sketch": fid, "entity": ids["open"]}]
    # the hole's circle picks the hole, not the plate
    sr = st.sketches[fid]
    kind, ref = PK.make_item({}, st.sketches, "sketch_line", (fid, ids["hole"]), {"region"})
    assert ref["name"] == sr.region_names[sr.region_index(sr.regions[PK.region_touching(sr, ids["hole"])])]
    # a sketch point is the ADR 0011 "sketch" reference
    old, new = sk("sketch_point", ids["point"], {"sketch_point"})
    assert old == new and new[0] == "sketch" and new[1]["role"] == "point" and new[1]["sig"]["at"] == [30.0, 30.0]
    # planes, axes and connectors need no shape at all
    for kind, key, want in (("plane", ("std", "Top"), {"plane"}), ("plane", ("plane", "F9"), {"plane"}),
                            ("axis", ("F3",), {"axis_feature"}), ("connector", ("F4",), {"connector"}),
                            ("connector", ("F4",), {"axis"})):
        old, new = _desktop_and_new(st, kind, key, want)
        assert old == new and old is not None, (kind, want)
    # the wrong kind for the field: nothing
    assert sk("sketch_point", ids["point"], {"face"}) == (None, None)


def test_refusal_says_why():
    assert PK.refusal("vertex", {"edge"})[0] == "wrong_kind"
    assert "an edge is needed" in PK.refusal("vertex", {"edge"})[1]
    assert PK.refusal("face", {"plane"}, shape=object())[0] == "not_flat"
    assert PK.refusal("face", {"axis"}, shape=object())[0] == "no_axis"
    assert PK.refusal("sketch_line", {"region"})[0] == "no_region"
    assert PK.refusal("sketch_line", {"vertex"})[0] == "wrong_kind"


def test_parse_pick_names_the_field_and_is_one_based():
    ok = PK.parse_pick(0, {"part": "P", "kind": "face", "index": 1, "want": ["face", "face"]})
    assert ok == {"part": "P", "kind": "face", "index": 1, "point": None, "want": ["face"]}
    for bad, field in (({"part": "P", "kind": "face", "index": 0, "want": ["face"]}, "picks[3].index"),
                       ({"part": "P", "kind": "face", "index": True, "want": ["face"]}, "picks[3].index"),
                       ({"part": "P", "kind": "vertex", "index": 1.5, "want": ["vertex"]}, "picks[3].index"),
                       ({"part": "P", "kind": "face", "want": ["face"]}, "picks[3].index"),
                       ({"part": "P", "kind": "part", "index": 1, "want": ["part"]}, "picks[3].index"),
                       ({"part": "P", "kind": "blob", "index": 1, "want": ["face"]}, "picks[3].kind"),
                       ({"part": 5, "kind": "face", "index": 1, "want": ["face"]}, "picks[3].part"),
                       ({"part": "P", "kind": "face", "index": 1, "want": ["bogus"]}, "picks[3].want"),
                       ({"part": "P", "kind": "face", "index": 1, "want": []}, "picks[3].want"),
                       ({"part": "P", "kind": "face", "index": 1, "want": ["face"], "point": [1, 2]}, "picks[3].point"),
                       ({"part": "P", "kind": "face", "index": 1, "want": ["face"], "point": [1, 2, float("nan")]},
                        "picks[3].point"),
                       ({"part": "P", "kind": "face", "index": 1, "want": ["face"], "point": [1, 2, float("inf")]},
                        "picks[3].point"),
                       ({"part": "P", "kind": "face", "index": 1, "want": ["face"], "point": [1, 2, 1e300]},
                        "picks[3].point"),
                       ({"part": "P", "kind": "sketch_line", "want": ["axis"]}, "picks[3].entity"),
                       ({"part": "P", "kind": "face", "index": 1, "want": ["face"], "x": 1}, "picks[3].x"),
                       ("nope", "picks[3]")):
        with pytest.raises(ValueError) as e:
            PK.parse_pick(3, bad)
        assert str(e.value).startswith(field), (bad, str(e.value))
    for bad in (None, {}, [], [{}] * (PK.MAX_PICKS + 1)):
        with pytest.raises(ValueError):
            PK.parse_picks(bad)


# ---------------------------------------------------------------------- the desktop window, engine on

class _Settings(dict):
    def value(self, k, default=None):
        return self.get(k, default)

    def setValue(self, k, v):
        self[k] = v


def _approx_same(a, b, path="item"):
    """Equal as plain data, floats to 1e-9 (the window's shapes went through a BREP text round trip)."""
    if isinstance(a, dict):
        assert isinstance(b, dict) and a.keys() == b.keys(), path
        for k in a:
            _approx_same(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, (list, tuple)):
        assert len(a) == len(b), path
        for i, (x, y) in enumerate(zip(a, b)):
            _approx_same(x, y, f"{path}[{i}]")
    elif isinstance(a, float):
        assert a == pytest.approx(b, abs=1e-9, rel=1e-9), path
    else:
        assert a == b, path


def test_the_window_with_the_engine_on_and_the_engine_request_give_the_same_items():
    os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")
    os.environ.setdefault("FREESHAPE_NO_HOME", "1")
    from PySide6.QtWidgets import QApplication
    from freeshape.engine import process as P
    from freeshape.ui import mainwindow as mw_mod
    app = QApplication.instance() or QApplication([])
    assert app is not None
    w = mw_mod.MainWindow(engine=True)
    w.settings = _Settings()
    try:
        w.open_any(BRACKET)
        assert w.studio_engine.settle(180)
        doc, vp = w.doc, w.viewport
        assert doc.state.engine_names, "the window holds no engine name maps"
        pid = next(iter(doc.state.parts))
        shape = doc.state.parts[pid].shape
        picks, want_items = [], []
        for kind, n, wants in (("face", len(K.faces(shape)), (["face"], ["edge"], ["plane"], ["axis"], ["part"])),
                               ("edge", len(K.edges(shape)), (["edge"], ["axis"])),
                               ("vertex", len(K.vertex_points(shape)), (["vertex"],))):
            for i in range(n):
                for want in wants:
                    picks.append({"part": pid, "kind": kind, "index": i + 1, "want": want})
                    want_items.append(pick_to_item(vp, Pick(kind, (pid, i)), set(want), doc.state))
        got = w.studio_engine.in_engine(doc, lambda e, tab: e.refs(tab, picks))
        assert len(got) == len(want_items) > 40
        for p, g, item in zip(picks, got, want_items):
            if item is None:
                assert g["ok"] is False, p
                continue
            assert g["ok"] is True and g["kind"] == item[0], p
            _approx_same(PK.jsonable(item[1]), g["ref"], str(p))
    finally:
        w.studio_engine.shutdown()
        P.SPARES.clear()
