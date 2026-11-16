"""I7.1: the engine's `sketch_frame` and `project_edge` requests (contract.Engine), which the Browser client's sketch
session needs and only the worker can answer, because the kernel shapes are there. Both are queries. Each test opens
its own engine, so each passes alone."""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from conftest import add, region_refs  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import picking as PK  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.engine import InProcessEngine, ProcessEngine  # noqa: E402
from freeshape.engine import process as P  # noqa: E402
from freeshape.engine.contract import BadPick, EngineError  # noqa: E402
from freeshape.sketch import Sketch, ORIGIN  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402
from test_engine_preview import _committed  # noqa: E402
from test_sketch_inference import _sketcher  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRACKET_FILE = os.path.join(ROOT, "examples", "documents", "bracket", "bracket.fshape")
TOP = {"kind": "std", "name": "Top"}
BOTH = [ProcessEngine, InProcessEngine]


@pytest.fixture
def engines():
    made = []

    def make(kind):
        eng = kind()
        made.append(eng)
        return eng
    try:
        yield make
    finally:
        for eng in made:
            eng.close()
        P.SPARES.clear()


def _local(path):
    """The same file built in this process, where the shapes can be read (a ProcessEngine's live in its worker)."""
    doc = Document.load(path)
    doc.state
    return doc


def _top_face_ref(path=BRACKET_FILE):
    """A stored reference to the bracket's top face (the planar face facing +Z at the greatest height)."""
    doc = _local(path)
    part = next(iter(doc.state.parts.values()))
    best = max((f for f in K.faces(part.shape) if K.face_info(f).get("n") and K.face_info(f)["n"][2] > 0.99),
               key=lambda f: K.face_info(f)["c"][2])
    return {"kind": "face", "face": F.make_ref(part.id, best, "face", part.shape, doc.state.named(part.id))}, \
        K.face_info(best)


def _disc_file(tmp_path):
    """A document holding one cylinder, so there is a cylindrical face to refuse."""
    doc = Document()
    s = Sketch()
    c = s.add_circle(ORIGIN, 30.0)
    s.add_constraint("radius", c, value=30.0)
    assert s.solve()
    f = doc.make_feature("sketch")
    f["params"]["sketch"] = s.to_dict()
    doc.add_feature(f)
    add(doc, "extrude", regions=region_refs(doc, f["id"]), depth=10.0, op="new")
    path = str(tmp_path / "disc.fshape")
    doc.save(path)
    return path


def _world(frame, uv, h=0.0):
    o, x, y, n = (np.array(frame[k], float) for k in ("origin", "xdir", "ydir", "normal"))
    return o + uv[0] * x + uv[1] * y + h * n


@pytest.mark.parametrize("kind", BOTH, ids=["process", "in_process"])
def test_the_top_plane_frame_is_the_world_frame_and_the_top_face_frame_lies_in_the_face(engines, kind):
    eng = engines(kind)
    tab = eng.open_tab(BRACKET_FILE)
    top = eng.sketch_frame(tab, TOP)
    assert np.allclose(top["origin"], [0, 0, 0]) and np.allclose(top["xdir"], [1, 0, 0])
    assert np.allclose(top["ydir"], [0, 1, 0]) and np.allclose(top["normal"], [0, 0, 1])
    ref, info = _top_face_ref()
    fr = eng.sketch_frame(tab, ref)
    assert np.allclose(fr["normal"], info["n"], atol=1e-9)
    n = np.array(fr["normal"])
    assert abs((np.array(fr["origin"]) - np.array(info["c"])) @ n) < 1e-9        # the origin is in the face's plane
    assert abs(np.array(fr["xdir"]) @ n) < 1e-9 and abs(np.array(fr["xdir"]) @ np.array(fr["ydir"])) < 1e-9
    assert np.allclose(np.cross(fr["xdir"], fr["ydir"]), n, atol=1e-9)


@pytest.mark.parametrize("kind", BOTH, ids=["process", "in_process"])
def test_frame_edges_are_polylines_in_the_plane_with_picks_the_refs_route_accepts(engines, kind):
    eng = engines(kind)
    tab = eng.open_tab(BRACKET_FILE)
    ref, info = _top_face_ref()
    fr = eng.sketch_frame(tab, ref)
    assert len(fr["edges"]) >= 10
    flat = [e for e in fr["edges"] if e["height"] < 1e-9]
    assert len(flat) == 4                                   # the top face's own four edges
    part = next(iter(eng.outputs(tab).parts))
    shape = K.shape_from_brep(part.brep)
    edges = K.edges(shape)
    for e in fr["edges"]:
        pk = PK.parse_pick(0, e["pick"])                    # a pick the refs route's parser takes
        assert pk["kind"] == "edge" and pk["index"] >= 1
        pts = np.array(e["points"], float)
        assert pts.ndim == 2 and pts.shape[1] == 2 and len(pts) >= 2
        # the polyline is the edge: its points, put back in the world, lie on the model's edge
        poly = np.asarray(K.discretize_edge(edges[pk["index"] - 1]), float)
        back = np.array([_world(fr, p) for p in pts])
        d = (poly - np.array(fr["origin"])) @ np.array(fr["normal"])
        assert abs(d).max() == pytest.approx(e["height"], abs=1e-9)
        if e["height"] < 1e-9:
            assert np.abs(back - poly).max() < 1e-9          # an edge in the plane: the points ARE the edge
            assert all(abs((q - np.array(fr["origin"])) @ np.array(fr["normal"])) < 1e-9 for q in poly)
    answers = eng.refs(tab, [{**e["pick"]} for e in fr["edges"]])
    assert all(a["ok"] for a in answers), [a for a in answers if not a["ok"]][:1]
    assert [a["kind"] for a in answers] == ["edge"] * len(answers)


@pytest.mark.parametrize("kind", BOTH, ids=["process", "in_process"])
def test_frame_of_an_existing_sketch_is_its_plane(engines, kind):
    eng = engines(kind)
    tab = eng.open_tab(BRACKET_FILE)
    fr = eng.sketch_frame(tab, "F1")                        # the profile, on Front
    ref = K.std_plane("Front")
    assert np.allclose(fr["origin"], ref.origin) and np.allclose(fr["normal"], ref.normal)
    assert np.allclose(fr["xdir"], ref.xdir)
    with pytest.raises(EngineError, match="not a sketch"):
        eng.sketch_frame(tab, "F2")
    with pytest.raises(EngineError):
        eng.sketch_frame(tab, "F99")


@pytest.mark.parametrize("kind", BOTH, ids=["process", "in_process"])
def test_a_cylindrical_face_is_refused_naming_the_face(engines, tmp_path, kind):
    eng = engines(kind)
    path = _disc_file(tmp_path)
    tab = eng.open_tab(path)
    doc = _local(path)
    part = next(iter(doc.state.parts.values()))
    side = next(f for f in K.faces(part.shape) if "r" in K.face_info(f))
    ref = {"kind": "face", "face": F.make_ref(part.id, side, "face", part.shape, doc.state.named(part.id))}
    name = ref["face"].get("name")
    assert name
    with pytest.raises(EngineError) as ei:
        eng.sketch_frame(tab, ref)
    assert "not planar" in str(ei.value) and name in str(ei.value)
    with pytest.raises(EngineError, match="not planar"):
        eng.project_edge(tab, ref, {"part": part.id, "kind": "edge", "index": 1})
    assert kind is InProcessEngine or eng.errored(tab) is None


def _desktop_use(path, pk, plane_name="Top"):
    """The desktop's Use tool on the same edge: the entity it adds and the reference it stores."""
    st = _local(path).state
    s = _sketcher()
    s.plane = K.std_plane(plane_name)
    s.base_state = st

    class _VP:
        part_shapes = {pid: p.shape for pid, p in st.parts.items()}
    eid = s._use_edge(_VP, Pick("edge", (pk["part"], pk["index"] - 1)))
    return s.sk, eid


@pytest.mark.parametrize("kind", BOTH, ids=["process", "in_process"])
def test_project_edge_gives_the_line_and_reference_the_desktop_use_tool_makes(engines, kind):
    eng = engines(kind)
    tab = eng.open_tab(BRACKET_FILE)
    fr = eng.sketch_frame(tab, TOP)
    part = next(iter(eng.outputs(tab).parts))
    edges = K.edges(K.shape_from_brep(part.brep))
    seen = 0
    for e in fr["edges"]:
        pk = e["pick"]
        info = K.edge_info(edges[pk["index"] - 1])
        if info.get("len", 0) < 1.0:
            continue
        try:
            got = eng.project_edge(tab, TOP, pk)
        except EngineError:
            continue
        sk, eid = _desktop_use(BRACKET_FILE, pk)
        if got["kind"] == "point":                          # an edge pointing at the plane: the desktop adds a point
            assert eid in sk.points and np.allclose(got["entity"]["p"], sk.P(eid), atol=1e-12)
            continue
        ent = sk.entities[eid]
        if got["kind"] == "line":
            assert got["entity"]["type"] == "line" and got["entity"]["fixed"] is True
            assert np.allclose(got["entity"]["a"], sk.P(ent["p1"]), atol=1e-12)
            assert np.allclose(got["entity"]["b"], sk.P(ent["p2"]), atol=1e-12)
            assert got["ref"] == ent.get("src")
            seen += 1
    assert seen >= 3
    # a straight edge in the plane: a line between its ends, with a named reference that finds the edge again
    flat = next(e for e in fr["edges"] if e["height"] < 1e-9 and len(e["points"]) == 2)
    got = eng.project_edge(tab, TOP, flat["pick"])
    assert got["kind"] == "line" and got["ref"]["name"]
    assert np.allclose(sorted([got["entity"]["a"], got["entity"]["b"]]), sorted(map(list, flat["points"])), atol=1e-9)


@pytest.mark.parametrize("kind", BOTH, ids=["process", "in_process"])
def test_project_edge_of_circles(engines, tmp_path, kind):
    eng = engines(kind)
    path = _disc_file(tmp_path)
    tab = eng.open_tab(path)
    fr = eng.sketch_frame(tab, TOP)
    part = next(iter(eng.outputs(tab).parts))
    edges = K.edges(K.shape_from_brep(part.brep))
    circles = [e for e in fr["edges"] if "r" in K.edge_info(edges[e["pick"]["index"] - 1])]
    assert circles
    got = eng.project_edge(tab, TOP, circles[0]["pick"])
    assert got["kind"] == "circle" and got["entity"]["r"] == pytest.approx(30.0)
    assert np.allclose(got["entity"]["c"], [0, 0], atol=1e-9)
    sk, eid = _desktop_use(path, circles[0]["pick"])
    assert sk.radius(eid) == pytest.approx(30.0) and got["ref"] == sk.entities[eid].get("src")
    # the same circle seen from the side is a straight line
    side = eng.project_edge(tab, {"kind": "std", "name": "Front"}, circles[0]["pick"])
    assert side["kind"] == "line"


def test_bad_picks_are_refused_naming_the_field(engines):
    eng = engines(ProcessEngine)
    tab = eng.open_tab(BRACKET_FILE)
    part = next(iter(eng.outputs(tab).parts)).id
    for pick in ({"part": part, "kind": "edge", "index": 0}, {"part": part, "kind": "edge", "index": 9999},
                 {"part": "nope", "kind": "edge", "index": 1}, {"part": part, "kind": "face", "index": 1},
                 {"part": part, "kind": "edge"}, "x"):
        with pytest.raises(BadPick):
            eng.project_edge(tab, TOP, pick)
    assert eng.errored(tab) is None


@pytest.mark.parametrize("kind", BOTH, ids=["process", "in_process"])
def test_the_requests_change_nothing(engines, kind):
    eng = engines(kind)
    tab = eng.open_tab(BRACKET_FILE)
    before = _committed(eng, tab)
    ref, _ = _top_face_ref()
    for plane in (TOP, ref, "F1"):
        fr = eng.sketch_frame(tab, plane)
        eng.project_edge(tab, plane, fr["edges"][0]["pick"])
    with pytest.raises(EngineError):
        eng.sketch_frame(tab, "F2")
    assert _committed(eng, tab) == before                    # revision, undo, saved bytes, rebuild counts, log


def test_sketch_ops_and_the_serve_path_import_no_qt():
    import subprocess
    code = ("import sys; import freeshape.sketch_ops, freeshape.sketch; "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('PySide6', 'OpenGL')))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT)
    assert out.stdout.strip() == "[]"


def test_the_desktop_in_engine_mode_uses_an_edge_through_the_moved_code(tmp_path, monkeypatch):
    """MainWindow(engine=True), as the real app starts: the Use tool on an edge of the plate gives the same line and
    the same named reference the engine's project_edge answers for that edge."""
    from test_connector_frames import plate_studio
    from test_sketch_used_edges import _edge_index, _far_edge
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    doc0, ids = plate_studio(60.0, 40.0)
    later = doc0.make_feature("sketch")                    # a sketch after the plate: its base model has the edges
    later["params"].update(plane=TOP, sketch=Sketch().to_dict())
    doc0.add_feature(later)
    path = str(tmp_path / "plate.fshape")
    doc0.save(path)
    pid, ei = _edge_index(doc0, _far_edge)
    w = MainWindow(engine=True)
    try:
        w.open_any(path)
        assert w.studio_engine.settle(180)
        doc = w.doc
        w.resize(900, 700)
        w.show()
        app.processEvents()
        w.start_sketch(later["id"])
        sk = w.sketcher.sk
        assert w.sketcher.base_state is not None          # start_sketch gave the sketcher the model before it

        class _VP:        # the offscreen viewport has no GL: show the shapes the sketcher's names are on
            part_shapes = {p_id: p.shape for p_id, p in w.sketcher.base_state.parts.items()}
        eid = w.sketcher._use_edge(_VP, Pick("edge", (pid, ei)))
        ent = sk.entities[eid]
        src = ent["src"]
        assert src["name"]
        eng = ProcessEngine()
        try:
            tab = eng.open_tab(path)
            got = eng.project_edge(tab, TOP, {"part": pid, "kind": "edge", "index": ei + 1})
        finally:
            eng.close()
        assert got["kind"] == "line" and got["ref"] == src
        assert np.allclose([got["entity"]["a"], got["entity"]["b"]],
                           [sk.P(ent["p1"]), sk.P(ent["p2"])], atol=1e-12)
        assert doc is w.doc
    finally:
        w.studio_engine.shutdown()
        P.SPARES.clear()
        w.deleteLater()
