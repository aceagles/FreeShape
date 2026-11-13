"""I2.4: plugin_version marker on Standard part instances."""
import json
import os

import pytest

from freeshape import assembly as A
from freeshape import kernel as K
from freeshape import pyfeature
from freeshape.pyfeature import registry

TYPE = "sp:fasteners/iso4762"
PARAMS = {"size": "M3", "length": 10}


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


def set_version(monkeypatch, version, pid="fasteners"):
    info = registry.plugin(pid)
    monkeypatch.setitem(registry._PLUGINS, pid, type(info)(**{**info.__dict__, "version": version}))


def stored_versions(path):
    return [i["source"].get("plugin_version") for i in json.load(open(path))["instances"]]


@pytest.fixture
def old_file(tmp_path, monkeypatch):
    """A file saved with the Plugin at 1.0.0, two Standard parts; Plugin then moves to 2.0.0."""
    set_version(monkeypatch, "1.0.0")
    asm = A.AssemblyDoc()
    a = asm.insert_standard_part(TYPE, PARAMS)
    b = asm.insert_standard_part(TYPE, {"size": "M4", "length": 16})
    path = str(tmp_path / "v.fasm")
    asm.save(path)
    set_version(monkeypatch, "2.0.0")
    return path, a, b


def test_loaded_old_version_gives_one_notice_each_and_builds(old_file, monkeypatch):
    path, a, b = old_file
    cls = pyfeature.standard_part(TYPE)
    built = []
    orig = cls.build
    monkeypatch.setattr(cls, "build", lambda self, p: built.append(dict(p)) or orig(self, p))
    asm = A.AssemblyDoc.load(path)
    assert asm.errors == {}
    assert built, "rebuilt with the installed code"
    assert K.volume(asm.parts[a].shape) > 0
    n = asm.standard_notices()
    assert set(n) == {a, b}
    assert n[a] == {"stored": "1.0.0", "installed": "2.0.0",
                    "text": "made with fasteners 1.0.0, rebuilt with 2.0.0"}


def test_save_without_acknowledging_keeps_stored_version(old_file, tmp_path):
    path, a, b = old_file
    asm = A.AssemblyDoc.load(path)
    out = str(tmp_path / "again.fasm")
    asm.save(out)
    assert stored_versions(out) == ["1.0.0", "1.0.0"]
    assert set(asm.standard_notices()) == {a, b}


def test_acknowledge_all_then_undo(old_file, tmp_path):
    path, a, b = old_file
    asm = A.AssemblyDoc.load(path)
    assert asm.acknowledge_standard_versions() == [a, b]
    assert asm.standard_notices() == {}
    out = str(tmp_path / "ack.fasm")
    asm.save(out)
    assert stored_versions(out) == ["2.0.0", "2.0.0"]
    asm.undo()
    assert set(asm.standard_notices()) == {a, b}
    assert [asm.instance(i)["source"]["plugin_version"] for i in (a, b)] == ["1.0.0", "1.0.0"]
    assert not asm.can_undo()


def test_acknowledge_subset_leaves_others_marked(old_file):
    path, a, b = old_file
    asm = A.AssemblyDoc.load(path)
    assert asm.acknowledge_standard_versions([b]) == [b]
    assert set(asm.standard_notices()) == {a}
    assert asm.instance(b)["source"]["plugin_version"] == "2.0.0"


def test_acknowledge_ignores_unmarked_and_unknown_ids(old_file):
    path, a, b = old_file
    asm = A.AssemblyDoc.load(path)
    asm.acknowledge_standard_versions([a])
    depth = len(asm._undo)
    assert asm.acknowledge_standard_versions([a, "nope", 99]) == []
    assert len(asm._undo) == depth, "nothing changed, no checkpoint"
    assert asm.acknowledge_standard_versions([]) == []
    assert set(asm.standard_notices()) == {b}


def test_equal_versions_give_no_notices():
    asm = A.AssemblyDoc()
    asm.insert_standard_part(TYPE, PARAMS)
    assert asm.standard_notices() == {}
    assert asm.acknowledge_standard_versions() == []


def test_non_standard_instance_never_gives_notice(tmp_path):
    asm = A.AssemblyDoc()
    iid = asm.insert_standard_part(TYPE, PARAMS)
    inst = asm.instance(iid)
    other = {"id": "x1", "name": "x", "source": {"kind": "step", "plugin_version": "0.1"}, "T": inst["T"]}
    asm.instances.append(other)
    assert asm.standard_notices() == {}
    assert asm.acknowledge_standard_versions(["x1"]) == []
    assert other["source"]["plugin_version"] == "0.1"


def test_missing_plugin_is_unresolved_without_version_notice(tmp_path):
    src = {"kind": "standard", "type": "sp:nosuch/thing", "plugin": "nosuch", "plugin_version": "2.0.0",
           "params": {}}
    asm = A.AssemblyDoc()
    asm.instances.append({"id": "n1", "name": "n", "source": src, "T": A.translation([0, 0, 0]).tolist()})
    asm.refresh()
    assert "n1" in asm.errors
    assert asm.standard_notices() == {}
    assert asm.acknowledge_standard_versions() == []
    assert src["plugin_version"] == "2.0.0"
