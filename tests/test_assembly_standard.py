"""I2.1: Standard part instances in the Assembly (source kind "standard")."""
import json
import os

import numpy as np
import pytest

from conftest import EXAMPLES_DIR
from freeshape import assembly as A
from freeshape import kernel as K
from freeshape import migrations as Mig
from freeshape import pyfeature
from freeshape.document import Document
from freeshape.pyfeature import registry

TYPE = "sp:fasteners/iso4762"
PARAMS = {"size": "M3", "length": 10}
PEGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "pyplugins", "pegs")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    user = tmp_path / "user_plugins"
    user.mkdir()
    monkeypatch.setenv("FREESHAPE_PLUGIN_DIR", str(user))
    before = {p.id for p in pyfeature.plugins()}
    problems_before = list(registry._PROBLEMS)
    yield
    registry._PROBLEMS[:] = problems_before
    for p in pyfeature.plugins():
        if p.id not in before:
            pyfeature.unregister_plugin(p.id)


def count_builds(monkeypatch, typ=TYPE):
    cls = pyfeature.standard_part(typ)
    calls = []
    orig = cls.build

    def build(self, params):
        calls.append(dict(params))
        return orig(self, params)
    monkeypatch.setattr(cls, "build", build)
    return calls


def test_insert_volume_matches_generator():
    asm = A.AssemblyDoc()
    iid = asm.insert_standard_part(TYPE, PARAMS)
    cls = pyfeature.standard_part(TYPE)
    expect = K.volume(cls().build(cls().validate(PARAMS)))
    assert K.volume(asm.parts[iid].shape) == pytest.approx(expect, rel=1e-9)
    inst = asm.instance(iid)
    assert inst["source"] == {"kind": "standard", "type": TYPE, "plugin": "fasteners",
                              "plugin_version": registry.plugin("fasteners").version,
                              "params": cls().validate(PARAMS)}
    assert inst["name"].startswith(cls().designation(cls().validate(PARAMS)))
    assert asm.errors == {}


def test_fifty_inserts_build_once(monkeypatch):
    calls = count_builds(monkeypatch)
    asm = A.AssemblyDoc()
    ids = [asm.insert_standard_part(TYPE, PARAMS) for _ in range(50)]
    assert len(calls) == 1
    assert len({id(asm.parts[i].shape) for i in ids}) == 1
    asm.refresh()
    assert len(calls) == 1
    asm.insert_standard_part(TYPE, {"size": "M3", "length": 12})
    assert len(calls) == 2


def test_source_key():
    asm = A.AssemblyDoc()
    a = asm.insert_standard_part(TYPE, {"size": "M3", "length": 10})
    b = asm.insert_standard_part(TYPE, {"length": 10, "size": "M3"})
    c = asm.insert_standard_part(TYPE, {"size": "M3", "length": 12})
    ka, kb, kc = (asm._source_key(asm.instance(i)["source"]) for i in (a, b, c))
    assert ka == kb != kc and ka[:2] == ("standard", TYPE)


def test_round_trip_stores_no_shape(tmp_path):
    asm = A.AssemblyDoc()
    a = asm.insert_standard_part(TYPE, PARAMS)
    b = asm.insert_standard_part(TYPE, {"size": "M4", "length": 16},
                                 T=A.translation([30, 5, 0]))
    path = str(tmp_path / "s.fasm")
    asm.save(path)
    d = json.load(open(path))
    assert d["version"] == Mig.latest_version("assembly")
    assert "blobs" not in d
    for inst in d["instances"]:
        assert "_brep_blob" not in inst
    assert "_brep" not in open(path).read()
    back = A.AssemblyDoc.load(path)
    assert back.errors == {}
    for i in (a, b):
        assert np.allclose(back.T(i), asm.T(i))
        assert K.volume(back.parts[i].shape) == pytest.approx(K.volume(asm.parts[i].shape), rel=1e-9)
        assert back.instance(i)["source"] == asm.instance(i)["source"]


def test_stale_brep_is_dropped_on_save(tmp_path):
    asm = A.AssemblyDoc()
    iid = asm.insert_standard_part(TYPE, PARAMS)
    asm.instance(iid)["_brep_blob"] = "0" * 64
    assert "_brep_blob" not in json.loads(asm.to_json())["instances"][0]


def test_unregistered_type_loads_unresolved_and_saves_unchanged(tmp_path):
    src = {"kind": "standard", "type": "sp:nosuch/thing", "plugin": "nosuch", "plugin_version": "2.0.0",
           "params": {"size": "X", "length": 3}}
    d = {"format": A.FILE_FORMAT, "version": Mig.latest_version("assembly"), "name": "A", "uid": "u",
         "instances": [{"id": "I1", "name": "Thing <1>", "source": src, "T": np.eye(4).tolist(),
                        "fixed": False, "hidden": False}], "mates": [], "blobs": {}}
    path = str(tmp_path / "x.fasm")
    json.dump(d, open(path, "w"))
    asm = A.AssemblyDoc.load(path)
    assert "I1" not in asm.parts
    assert "nosuch" in asm.errors["I1"] and "sp:nosuch/thing" in asm.errors["I1"]
    assert len(asm.instances) == 1
    out = str(tmp_path / "y.fasm")
    asm.save(out)
    assert json.load(open(out))["instances"][0]["source"] == src


@pytest.mark.parametrize("params", [{"size": "M3", "length": 11}, {"size": "M99", "length": 10}, {}])
def test_failing_validate_is_unresolved(tmp_path, params):
    src = {"kind": "standard", "type": TYPE, "plugin": "fasteners", "plugin_version": "1.0.0", "params": params}
    asm = A.AssemblyDoc()
    asm.instances.append({"id": "I1", "name": "S <1>", "source": src, "T": np.eye(4).tolist(),
                          "fixed": False, "hidden": False})
    asm.refresh(solve=False, notify=False)
    assert "I1" not in asm.parts
    assert "fasteners" in asm.errors["I1"] and TYPE in asm.errors["I1"]
    assert json.loads(asm.to_json())["instances"][0]["source"] == src


def test_failing_build_is_unresolved(monkeypatch):
    cls = pyfeature.standard_part(TYPE)

    def boom(self, params):
        raise RuntimeError("kernel exploded")
    monkeypatch.setattr(cls, "build", boom)
    src = {"kind": "standard", "type": TYPE, "plugin": "fasteners", "plugin_version": "1.0.0",
           "params": dict(PARAMS)}
    asm = A.AssemblyDoc()
    asm.instances.append({"id": "I1", "name": "S <1>", "source": src, "T": np.eye(4).tolist(),
                          "fixed": False, "hidden": False})
    asm.refresh(solve=False, notify=False)
    assert "I1" not in asm.parts and "kernel exploded" in asm.errors["I1"]
    assert TYPE in asm.errors["I1"]
    with pytest.raises(K.KernelError, match="fasteners"):
        asm.insert_standard_part(TYPE, PARAMS)
    assert asm.instances[0]["source"] == src


def test_insert_unknown_type_raises():
    asm = A.AssemblyDoc()
    with pytest.raises(K.KernelError, match="nosuch"):
        asm.insert_standard_part("sp:nosuch/thing", {})
    assert asm.instances == []


def test_removed_plugin_keeps_last_shape_and_names_it():
    info = pyfeature.register_plugin(PEGS)
    assert info is not None, pyfeature.problems()
    asm = A.AssemblyDoc()
    iid = asm.insert_standard_part("sp:pegs/peg", {"size": "P3", "length": 10})
    shape = asm.parts[iid].shape
    pyfeature.unregister_plugin("pegs")
    asm.refresh()
    assert asm.parts[iid].shape is shape
    assert "pegs" in asm.errors[iid] and "sp:pegs/peg" in asm.errors[iid]
    assert asm.instance(iid)["source"]["type"] == "sp:pegs/peg"


def test_cache_key_includes_plugin_version(monkeypatch):
    calls = count_builds(monkeypatch)
    asm = A.AssemblyDoc()
    asm.insert_standard_part(TYPE, PARAMS)
    info = registry.plugin("fasteners")
    monkeypatch.setitem(registry._PLUGINS, "fasteners", type(info)(**{**info.__dict__, "version": "9.9.9"}))
    asm.insert_standard_part(TYPE, PARAMS)
    assert len(calls) == 2


@pytest.mark.parametrize("name", ["hinge.fasm"])
def test_old_file_still_loads_and_solves(name, tmp_path):
    studio = Document.load(os.path.join(EXAMPLES_DIR, "hinge_parts.fshape"))

    def resolve(src):
        p = studio.state.parts.get(src.get("part"))
        if src.get("uid") != studio.uid or p is None:
            raise K.KernelError("Part Studio isn't open")
        return p
    path = os.path.join(EXAMPLES_DIR, name)
    old = json.load(open(path))
    asm = A.AssemblyDoc.load(path, resolver=resolve)
    assert asm.errors == {}
    assert old.get("version", 1) < Mig.latest_version("assembly")
    out = str(tmp_path / name)
    asm.save(out)
    back = A.AssemblyDoc.load(out, resolver=resolve)
    for inst in old["instances"]:
        assert np.allclose(back.T(inst["id"]), asm.T(inst["id"]))
        assert "standard" not in json.dumps(back.instance(inst["id"])["source"])


def test_version_3_standard_part_file_loads_through_the_merged_chain(tmp_path):
    """A file saved before Mates Parity was merged says version 3, holds a Standard part and has no groups or
    relations. It runs master's steps 3->4..5->6, the marker step 6->7 and the blob step 7->8."""
    src = {"kind": "standard", "type": TYPE, "plugin": "fasteners", "plugin_version": "1.0.0", "params": PARAMS}
    d = {"format": A.FILE_FORMAT, "version": 3, "name": "A", "uid": "u",
         "instances": [{"id": "I1", "name": "S <1>", "source": src, "T": np.eye(4).tolist(),
                        "fixed": False, "hidden": False}], "mates": [], "blobs": {}}
    path = str(tmp_path / "v3.fasm")
    json.dump(d, open(path, "w"))
    assert Mig.latest_version("assembly") == 9
    asm = A.AssemblyDoc.load(path)
    assert "I1" in asm.parts and not asm.errors
    assert asm.instance("I1")["source"] == src
    assert asm.groups == [] and asm.relations == []
    out = str(tmp_path / "v8.fasm")
    asm.save(out)
    saved = json.load(open(out))
    assert saved["version"] == 9 and saved["groups"] == [] and saved["relations"] == []
