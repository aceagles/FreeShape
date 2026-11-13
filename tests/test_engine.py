"""freeshape/engine: the tab engine's `submit_edit(tab, edit)` -> `outputs(tab)` contract. Every test that
drives an engine runs on both executors: `inprocess` (every tab in this process) and `process` (one worker
process per tab, process.py). Outputs must be plain picklable data (ADR 0002) and part element indices must
survive a BREP round trip. Crash, restart and lifecycle tests of the process executor: test_engine_process.py."""
import dataclasses
import os
import pickle
import shutil

import numpy as np
import pytest
from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Torus

from conftest import (ANALYTIC, EXAMPLES_DIR, ROOT, add, assert_golden, block_doc, edge_refs, only_part,
                      shape_fingerprint, sketch_feature)
from freeshape import kernel as K
from freeshape import naming as N
from freeshape.engine import (AssemblyOutputs, DrawingOutputs, EngineError, InProcessEngine, PartOut,
                              ProcessEngine, StudioOutputs)
from freeshape.engine.encode import Encoder

HINGE_ASM = os.path.join(EXAMPLES_DIR, "hinge.fasm")
HINGE_STUDIO = os.path.join(EXAMPLES_DIR, "hinge_parts.fshape")
DEPTH = {"op": "set_param", "feature": "F2", "key": "depth"}       # the base plate's extrude


def depth(v):
    return dict(DEPTH, value=v)


def hashes(out):
    return {p.id: p.hash for p in out.parts}


ENGINES = {"inprocess": InProcessEngine, "process": ProcessEngine}


@pytest.fixture(params=sorted(ENGINES))
def make_engine(request):
    """make_engine() -> a new engine of the parametrised executor; every one is closed (its workers ended)
    when the test ends, passed or failed."""
    made = []

    def make():
        made.append(ENGINES[request.param]())
        return made[-1]

    yield make
    for e in made:
        e.close()


def _tab_of(engine, kind):
    tabs = [t for t, k, _n, _p in engine.tabs() if k == kind]
    assert len(tabs) == 1, engine.tabs()
    return tabs[0]


# =========================================================================== plain data

_LEAVES = (str, int, float, bool, bytes, type(None))


def foreign_objects(x, path="out"):
    """Every object in an output that is not plain data (dataclass / dict / list / tuple / scalar / numeric
    numpy array), as (path, type name). OCP objects are the ones this is really looking for."""
    bad = []
    if type(x).__module__.split(".")[0] == "OCP":
        return [(path, f"{type(x).__module__}.{type(x).__qualname__}")]
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        if type(x).__module__ != "freeshape.engine.contract":
            bad.append((path, type(x).__qualname__))
        for f in dataclasses.fields(x):
            bad += foreign_objects(getattr(x, f.name), f"{path}.{f.name}")
    elif isinstance(x, dict):
        for k, v in x.items():
            bad += foreign_objects(k, f"{path}[key {k!r}]") + foreign_objects(v, f"{path}[{k!r}]")
    elif isinstance(x, (list, tuple)):
        for i, v in enumerate(x):
            bad += foreign_objects(v, f"{path}[{i}]")
    elif isinstance(x, np.ndarray):
        if x.dtype.kind not in "biuf":
            bad.append((path, f"ndarray of {x.dtype}"))
    elif not isinstance(x, _LEAVES):
        bad.append((path, f"{type(x).__module__}.{type(x).__qualname__}"))
    return bad


def same(a, b, path="out"):
    """Deep equality that understands numpy arrays and dataclasses."""
    assert type(a) is type(b), path
    if dataclasses.is_dataclass(a):
        for f in dataclasses.fields(a):
            same(getattr(a, f.name), getattr(b, f.name), f"{path}.{f.name}")
    elif isinstance(a, dict):
        assert list(a) == list(b), path
        for k in a:
            same(a[k], b[k], f"{path}[{k!r}]")
    elif isinstance(a, (list, tuple)):
        assert len(a) == len(b), path
        for i, (u, v) in enumerate(zip(a, b)):
            same(u, v, f"{path}[{i}]")
    elif isinstance(a, np.ndarray):
        assert a.dtype == b.dtype and np.array_equal(a, b), path
    else:
        assert a == b, path


def assert_plain_and_pickles(out):
    assert foreign_objects(out) == []
    same(out, pickle.loads(pickle.dumps(out)))


# =========================================================================== Part Studio tabs

def test_studio_edit_returns_parts_as_brep_and_hash(make_engine):
    e = make_engine()
    tab = e.open_tab(HINGE_STUDIO)
    assert e.submit_edit(tab, depth(12)) == 1
    out = e.outputs(tab)
    assert isinstance(out, StudioOutputs) and out.kind == "studio" and out.revision == 1
    assert len(out.parts) == 3 and not out.errors
    for p in out.parts:
        assert isinstance(p, PartOut)
        assert isinstance(p.brep, bytes) and p.brep
        assert isinstance(p.hash, str) and len(p.hash) == 64
        assert p.names["face"] and len(p.names["face"]) == len(K.faces(K.shape_from_brep(p.brep)))   # the engine returns the element map (I3.7)
        assert p.mesh is not None and len(p.mesh.tris) and p.mesh.tri_face.min() >= 1
    # the bytes are the geometry: rebuilt from them, the parts match the recorded values
    assert_golden("engine/hinge_parts_depth_12",
                  {p.id: shape_fingerprint(K.shape_from_brep(p.brep)) for p in out.parts}, ANALYTIC)
    assert {s.id for s in out.sketches} == {"F1", "F3", "F5"}
    assert all(s.regions and len(s.plane["normal"]) == 3 for s in out.sketches)


def test_same_edit_gives_same_hash_and_a_dimension_change_does_not(make_engine):
    e = make_engine()
    tab = e.open_tab(HINGE_STUDIO)
    e.submit_edit(tab, depth(12))
    first = hashes(e.outputs(tab))
    e.submit_edit(tab, depth(12))
    assert hashes(e.outputs(tab)) == first
    # a fresh engine building the same edit from the file writes the very same BREP text (in process: the
    # two executors must agree)
    e2 = InProcessEngine()
    t2 = e2.open_tab(HINGE_STUDIO)
    e2.submit_edit(t2, depth(12))
    assert hashes(e2.outputs(t2)) == first
    e.submit_edit(tab, depth(14))
    changed = hashes(e.outputs(tab))
    plate = "F2/1"
    assert changed[plate] != first[plate]
    assert {k: v for k, v in changed.items() if k != plate} == {k: v for k, v in first.items() if k != plate}
    e.submit_edit(tab, depth(12))
    assert hashes(e.outputs(tab)) == first


def test_meshing_does_not_change_the_hash():
    """The hash is of BREP without triangulation, so meshing a part (which attaches triangles to its faces
    in place) before or after hashing it makes no difference."""
    doc = block_doc()
    p = only_part(doc)
    bare = Encoder(meshes=False).part(p).hash
    meshed = Encoder(meshes=True).part(p)
    assert meshed.mesh is not None and meshed.hash == bare == Encoder(meshes=False).part(p).hash


# =========================================================================== plain, picklable outputs

def test_every_output_is_plain_data_and_survives_pickle(make_engine):
    e = make_engine()
    asm = e.open_tab(HINGE_ASM)
    studio = _tab_of(e, "studio")
    dwg = e.new_tab("drawing")
    e.submit_edit(dwg, {"op": "set_source", "source": {"kind": "assembly", "path": HINGE_ASM}})
    e.submit_edit(dwg, {"op": "auto_layout"})
    for tab in (studio, asm, dwg):
        assert_plain_and_pickles(e.outputs(tab))


def test_the_walker_finds_an_ocp_object():
    out = InProcessEngine()
    tab = out.open_tab(HINGE_STUDIO)
    o = out.outputs(tab)
    o.parts[0].names["face"][1] = K.faces(K.shape_from_brep(o.parts[0].brep))[0]
    assert [p for p, _t in foreign_objects(o)] == ["out.parts[0].names['face'][1]"]


def test_engine_has_no_qt():
    hits = []
    for base, _dirs, files in os.walk(os.path.join(ROOT, "freeshape", "engine")):
        for name in files:
            if name.endswith(".py"):
                with open(os.path.join(base, name)) as fh:
                    if "PySide6" in fh.read():
                        hits.append(name)
    assert hits == []


# =========================================================================== assembly tabs and cross-tab inputs

def test_assembly_tab_returns_instance_outputs(make_engine):
    e = make_engine()
    asm = e.open_tab(HINGE_ASM)
    out = e.outputs(asm)
    assert isinstance(out, AssemblyOutputs) and not out.errors
    assert [i.id for i in out.instances] == ["I1", "I2", "I3"]
    for inst in out.instances:
        assert inst.part in out.parts
        assert np.asarray(inst.T).shape == (4, 4)
    assert len(out.parts) == 3
    assert len(out.mates) == 2
    m = out.mates[0]
    assert (m.a.instance, m.a.kind, m.a.index) == ("I1", "edge", 14)       # stored 0-based 13, output 1-based
    assert np.allclose(m.a.world["o"], [15, 20, 0])
    # the studio the instances link to was opened by the engine as a tab of its own, and consumed from there
    studio = _tab_of(e, "studio")
    assert out.consumes == (studio,)
    plate = next(p for p in e.outputs(studio).parts if p.id == "F2/1")
    assert out.instances[0].part == plate.hash


def test_assembly_sees_an_unsaved_upstream_edit(make_engine):
    e = make_engine()
    asm = e.open_tab(HINGE_ASM)
    studio = _tab_of(e, "studio")
    with open(HINGE_STUDIO) as fh:
        on_disk = fh.read()
    before = e.outputs(asm)
    plate_before = before.parts[before.instances[0].part]
    e.submit_edit(studio, depth(12))                                       # edited, never saved
    after = e.outputs(asm)
    plate_after = after.parts[after.instances[0].part]
    assert plate_after.hash != plate_before.hash
    assert plate_after.hash == next(p.hash for p in e.outputs(studio).parts if p.id == "F2/1")
    lo, hi = K.bbox(K.shape_from_brep(plate_after.brep))
    assert hi[2] - lo[2] == pytest.approx(12)
    # the untouched parts are the same, and nothing was written to disk
    assert after.instances[1].part == before.instances[1].part
    with open(HINGE_STUDIO) as fh:
        assert fh.read() == on_disk


def test_assembly_opened_after_the_studio_uses_the_open_tab(make_engine, tmp_path):
    """Already-open upstream tab wins over the file on disk, even when it was opened first."""
    e = make_engine()
    studio = e.open_tab(HINGE_STUDIO, tab="hinge-parts")
    e.submit_edit(studio, depth(12))
    asm = e.open_tab(HINGE_ASM)
    out = e.outputs(asm)
    assert out.consumes == ("hinge-parts",)
    assert len([t for t in e.tabs() if t[1] == "studio"]) == 1
    lo, hi = K.bbox(K.shape_from_brep(out.parts[out.instances[0].part].brep))
    assert hi[2] - lo[2] == pytest.approx(12)


def test_assembly_edits_apply_and_undo(make_engine):
    e = make_engine()
    asm = e.open_tab(HINGE_ASM)
    assert e.submit_edit(asm, {"op": "set_hidden", "instance": "I2", "value": True}) == 1
    assert [i.hidden for i in e.outputs(asm).instances] == [False, True, False]
    fixed = [i.fixed for i in e.outputs(asm).instances]
    e.submit_edit(asm, {"op": "set_fixed", "instance": "I2", "value": not fixed[1]})
    assert e.outputs(asm).instances[1].fixed is (not fixed[1])
    e.submit_edit(asm, {"op": "undo"})                     # hiding is view state, not undoable; fixing is
    out = e.outputs(asm)
    assert [i.fixed for i in out.instances] == fixed and out.instances[1].hidden and out.revision == 3
    with pytest.raises(EngineError):
        e.submit_edit(asm, {"op": "set_hidden", "instance": "I99", "value": True})


def test_drawing_follows_its_studio(make_engine):
    e = make_engine()
    studio = e.open_tab(HINGE_STUDIO)
    dwg = e.new_tab("drawing")
    e.submit_edit(dwg, {"op": "set_source", "source": {"kind": "studio", "path": HINGE_STUDIO, "part": "F2/1"}})
    e.submit_edit(dwg, {"op": "auto_layout"})
    out = e.outputs(dwg)
    assert isinstance(out, DrawingOutputs) and not out.errors and out.consumes == (studio,)
    assert out.views and set(out.curves) == {v["id"] for v in out.views}
    assert out.model == next(p.hash for p in e.outputs(studio).parts if p.id == "F2/1")
    e.submit_edit(studio, depth(12))
    assert e.outputs(dwg).model == next(p.hash for p in e.outputs(studio).parts if p.id == "F2/1")


def test_bad_edits_raise(make_engine):
    e = make_engine()
    tab = e.open_tab(HINGE_STUDIO)
    with pytest.raises(EngineError):
        e.submit_edit(tab, {"op": "move_instance"})
    with pytest.raises(EngineError):
        e.submit_edit(tab, {"op": "set_param", "feature": "F99", "key": "depth", "value": 1})
    with pytest.raises(EngineError):
        e.outputs("no-such-tab")
    assert e.outputs(tab).revision == 0


def test_save_writes_the_edit(make_engine, tmp_path):
    path = str(tmp_path / "parts.fshape")
    shutil.copy(HINGE_STUDIO, path)
    e = make_engine()
    tab = e.open_tab(path)
    e.submit_edit(tab, depth(12))
    e.submit_edit(tab, {"op": "save"})
    e2 = InProcessEngine()
    assert hashes(e2.outputs(e2.open_tab(path))) == hashes(e.outputs(tab))


# =========================================================================== element indices across BREP

def _hole_and_fillet_doc():
    doc = block_doc(40.0, 20.0, 10.0)
    hs = sketch_feature(doc, lambda s: s.add_point_entity(s.add_point(10, 10)))
    add(doc, "hole", sketch=hs, standard="custom", diameter=8.0, end="through", style="simple")
    rim = edge_refs(only_part(doc), lambda i: abs(i.get("r", 0) - 4) < 1e-6 and abs(i["c"][2] - 10) < 1e-6,
                    expect=1)
    add(doc, "fillet", edges=rim, radius=1.0)
    return doc


def _props(sub, kind):
    info = K.face_info(sub) if kind == "face" else K.edge_info(sub)
    return info["area" if kind == "face" else "len"], np.asarray(info["c"])


def test_element_indices_survive_a_brep_round_trip(make_engine, tmp_path):
    doc = _hole_and_fillet_doc()
    path = str(tmp_path / "holed.fshape")
    doc.save(path)
    e = make_engine()
    out = e.outputs(e.open_tab(path))
    assert len(out.parts) == 1 and not out.errors
    before = only_part(doc).shape
    after = K.shape_from_brep(out.parts[0].brep)
    kinds = {"face": K.faces, "edge": K.edges}
    types = {K.face_info(f)["type"] for f in K.faces(before)}
    assert int(GeomAbs_Cylinder) in types and int(GeomAbs_Torus) in types    # the hole and its fillet
    for kind, subs in kinds.items():
        a, b = subs(before), subs(after)
        assert len(a) == len(b), kind
        for i, (x, y) in enumerate(zip(a, b), start=1):
            (mx, cx), (my, cy) = _props(x, kind), _props(y, kind)
            assert my == pytest.approx(mx, rel=1e-9, abs=1e-9), (kind, i)
            assert np.allclose(cy, cx, atol=1e-9), (kind, i)
    va, vb = np.array(K._vertices_np(before)), np.array(K._vertices_np(after))
    assert va.shape == vb.shape and np.allclose(va, vb, atol=1e-9)


def test_a_name_map_addresses_the_same_geometry_after_the_round_trip():
    doc = _hole_and_fillet_doc()
    part = only_part(doc)
    part.named = N.seed(part.shape, "T1")                # any map of this shape will do
    out = Encoder(meshes=False).part(part)
    assert len(out.names["face"]) == len(K.faces(part.shape))
    assert len(out.names["edge"]) == len(K.edges(part.shape))
    after = K.shape_from_brep(out.brep)
    named = N.NamedShape(after, {(k[0].upper(), i): n for k in ("face", "edge", "vertex")
                                 for i, n in out.names[k].items()})
    for kind, cast, subs in (("face", K._as_face, K.faces), ("edge", K._as_edge, K.edges)):
        for i, name in out.names[kind].items():
            want = _props(cast(part.named.shape_of(name)), kind)
            got = _props(cast(named.shape_of(name)), kind)
            assert got[0] == pytest.approx(want[0], rel=1e-9, abs=1e-9), (kind, i)
            assert np.allclose(got[1], want[1], atol=1e-9), (kind, i)
            assert named.shape_of(name).IsSame(subs(after)[i - 1])


def test_a_name_map_of_another_shape_is_dropped():
    doc = _hole_and_fillet_doc()
    part = only_part(doc)
    part.named = N.seed(only_part(block_doc()).shape, "T1")
    assert Encoder(meshes=False).part(part).names == {"face": {}, "edge": {}, "vertex": {}}
