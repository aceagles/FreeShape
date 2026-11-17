"""I10.3: an Assembly's outputs list the mate connectors each instance offers (InstanceOut.connectors): what its Part
Studio publishes and the origin connector added to the instance, each frame placed by the instance's transform, through
the wire format. Each test builds its own engine, so each passes alone."""
import os
import shutil

import numpy as np
import pytest

from freeshape.engine import InProcessEngine, ProcessEngine, wire

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HINGE = os.path.join(ROOT, "examples", "documents", "hinge")


@pytest.fixture(params=[InProcessEngine, ProcessEngine], ids=["inprocess", "process"])
def engine_class(request):
    return request.param


def _open(engine_class, tmp_path):
    """An engine with the hinge Document's studio (T1) and assembly (T2) tabs, from a copy of the example."""
    folder = str(tmp_path / "Hinge")
    shutil.copytree(HINGE, folder)
    eng = engine_class()
    studio = eng.open_tab(os.path.join(folder, "hinge_parts.fshape"), tab="T1")
    asm = eng.open_tab(os.path.join(folder, "hinge.fasm"), tab="T2")
    return eng, studio, asm


def _connector(eng, studio, owner, fid="F90", name="Pin", o=(3.0, 4.0, 5.0)):
    """A Mate connector owned by part `owner`, added to the studio by an edit (as tests/test_serve_derived_dialog)."""
    p = {"name": name, "ref": {"origin": True}, "kind": "face", "click": None, "snap": None, "x_ref": None,
         "flip": False, "owner": owner}
    eng.submit_edit(studio, {"op": "add_feature", "feature": {"id": fid, "type": "mate_connector", "name": name,
                                                              "suppressed": False, "params": p}})
    return fid


def _wired(eng, tab):
    return wire.from_wire(wire.to_wire(eng.outputs(tab)))


def test_an_instance_offers_its_studios_connector_placed_by_its_transform(engine_class, tmp_path):
    eng, studio, asm = _open(engine_class, tmp_path)
    try:
        before = _wired(eng, asm)
        inst = before.instances[0]
        assert inst.connectors == []                      # the hinge's studio has no connector yet
        part_id = inst.source["part"]
        fid = _connector(eng, studio, part_id)
        moved = np.eye(4)
        moved[:3, 3] = [10.0, 20.0, 30.0]
        eng.submit_edit(asm, {"op": "set_fixed", "instance": inst.id, "value": True})
        eng.submit_edit(asm, {"op": "move_instance", "instance": inst.id, "T": moved.tolist(), "solve": False})
        out = _wired(eng, asm)
        got = next(i for i in out.instances if i.id == inst.id)
        assert [c.id for c in got.connectors] == [fid]
        c = got.connectors[0]
        assert c.name == "Pin" and c.kind == "connector"
        assert set(c.frame) == {"o", "z", "x"} and set(c.world) == {"o", "z", "x"}
        T = np.asarray(got.T)
        o = np.asarray(c.frame["o"])
        assert np.allclose(np.asarray(c.world["o"]), T[:3, :3] @ o + T[:3, 3], atol=1e-9)
        assert np.allclose(np.asarray(c.world["z"]), T[:3, :3] @ np.asarray(c.frame["z"]), atol=1e-9)
        assert np.allclose(np.asarray(c.world["x"]), T[:3, :3] @ np.asarray(c.frame["x"]), atol=1e-9)
        others = [i for i in out.instances if i.id != inst.id and i.source.get("part") != part_id]
        assert all(i.connectors == [] for i in others)    # only the instances of that part offer it
    finally:
        eng.close()


def test_an_origin_connector_added_to_an_instance_is_listed_too(tmp_path):
    eng, studio, asm = InProcessEngine(), None, None
    folder = str(tmp_path / "Hinge")
    shutil.copytree(HINGE, folder)
    try:
        eng.open_tab(os.path.join(folder, "hinge_parts.fshape"), tab="T1")
        asm = eng.open_tab(os.path.join(folder, "hinge.fasm"), tab="T2")
        inst = eng.outputs(asm).instances[0]
        cid = eng._get(asm).doc.add_instance_origin_connector(inst.id)
        out = wire.from_wire(wire.to_wire(eng.outputs(asm)))
        got = next(i for i in out.instances if i.id == inst.id)
        assert [(c.id, c.kind) for c in got.connectors] == [(cid, "origin")]
        c = got.connectors[0]
        assert c.name.endswith("origin")
        T = np.asarray(got.T)
        assert np.allclose(c.frame["o"], [0, 0, 0]) and np.allclose(c.frame["z"], [0, 0, 1])
        assert np.allclose(c.world["o"], T[:3, 3], atol=1e-9)
        assert np.allclose(c.world["z"], T[:3, 2], atol=1e-9) and np.allclose(c.world["x"], T[:3, 0], atol=1e-9)
    finally:
        eng.close()


def test_the_refs_route_takes_a_connector_pick_for_a_field_that_wants_one(engine_class, tmp_path):
    """I10.3: the view picks a Part Studio's mate connector by its feature id (`part`), no index; the Axis tool's
    `connector` field keeps the connector, the Plane tool's `axis_connector` field takes its Z axis."""
    from freeshape.engine import EngineError
    eng, studio, asm = _open(engine_class, tmp_path)
    try:
        fid = _connector(eng, studio, eng.outputs(studio).parts[0].id)
        got = eng.refs(studio, [{"part": fid, "kind": "connector", "want": ["connector"]}])[0]
        assert got["ok"] and got["kind"] == "connector" and got["ref"] == {"fid": fid}
        got = eng.refs(studio, [{"part": fid, "kind": "connector", "want": ["axis_connector", "face"]}])[0]
        assert got["ok"] and got["kind"] == "axis" and got["ref"] == {"kind": "connector", "fid": fid, "axis": "z"}
        got = eng.refs(studio, [{"part": fid, "kind": "connector", "want": ["face"]}])[0]
        assert got["ok"] is False and got["code"] == "wrong_kind"
        with pytest.raises(EngineError):
            eng.refs(studio, [{"part": "F404", "kind": "connector", "want": ["connector"]}])
        with pytest.raises(EngineError):
            eng.refs(studio, [{"part": fid, "kind": "connector", "index": 1, "want": ["connector"]}])
    finally:
        eng.close()
