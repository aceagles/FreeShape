"""I1.2: Standard part API and the `standard_parts` manifest entry."""
import json
import math
import os

import pytest

from freeshape import kernel as K
from freeshape import pyfeature
from freeshape.pyfeature import registry

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "pyplugins")
PEGS = os.path.join(DATA, "pegs")
TYPE = "sp:pegs/peg"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    user = tmp_path / "user_plugins"
    user.mkdir()
    monkeypatch.setenv("FREESHAPE_PLUGIN_DIR", str(user))
    before = {p.id for p in pyfeature.plugins()}
    problems_before = list(registry._PROBLEMS)
    yield user
    registry._PROBLEMS[:] = problems_before     # a test's bad plugins must not leak into later tests
    for p in pyfeature.plugins():
        if p.id not in before:
            pyfeature.unregister_plugin(p.id)


@pytest.fixture
def pegs():
    info = pyfeature.register_plugin(PEGS)
    assert info is not None, pyfeature.problems()
    return info


def manifest_plugin(tmp_path, pid, **extra):
    d = tmp_path / pid
    d.mkdir()
    m = {"id": pid, "name": pid, "version": "1.0.0", "licence": "GPL-3.0-or-later"}
    m.update(extra)
    (d / "plugin.json").write_text(json.dumps(m))
    return str(d)


def test_resolves_and_builds(pegs):
    cls = pyfeature.standard_part(TYPE)
    assert cls is not None and cls.id == "peg"
    assert pyfeature.is_standard_type(TYPE) and not pyfeature.is_standard_type("py:boxes/box")
    assert [t for t, _, _ in pyfeature.standard_parts() if t.startswith("sp:pegs/")] == [TYPE]
    assert [i for t, _, i in pyfeature.standard_parts() if t == TYPE] == [pegs]
    peg = cls()
    assert peg.sizes() == ["P3", "P5"] and peg.lengths("P5") == [10, 20]
    assert peg.match_hole(5.0) == "P5" and peg.match_hole(4.0) is None
    for size, r in (("P3", 1.5), ("P5", 2.5)):
        for length in (10, 20):
            params = {"size": size, "length": length}
            shape = peg.build(params)
            assert K.is_valid(shape)
            assert K.volume(shape) == pytest.approx(math.pi * r * r * length, abs=1e-6)
    assert peg.designation({"size": "P5", "length": 20}) == "Peg P5 x 20"


def test_connectors_one_length_apart(pegs):
    peg = pyfeature.standard_part(TYPE)()
    c = peg.connectors({"size": "P3", "length": 20})
    assert set(c) == {"bottom", "top"}
    assert c["top"]["o"][2] - c["bottom"]["o"][2] == pytest.approx(20.0)
    assert c["bottom"]["z"] == [0.0, 0.0, -1.0] and c["top"]["z"] == [0.0, 0.0, 1.0]


def test_validate_names_bad_key(pegs):
    peg = pyfeature.standard_part(TYPE)()
    with pytest.raises(K.KernelError, match="length"):
        peg.validate({"size": "P3", "length": 15})


def test_not_a_feature_type(pegs):
    assert TYPE not in [t for t, _, _ in pyfeature.feature_types()]
    assert registry.get(TYPE) is None
    from freeshape import features as F
    assert TYPE not in F.RUNNERS and TYPE not in F.FEATURE_LABELS


def test_standard_parts_only_manifest(tmp_path):
    d = manifest_plugin(tmp_path, "only", standard_parts=["peg:Peg"])
    for f in ("peg.py",):
        (tmp_path / "only" / f).write_text(open(os.path.join(PEGS, f)).read())
    info = pyfeature.register_plugin(d)
    assert info is not None, pyfeature.problems()
    assert pyfeature.standard_part("sp:only/peg") is not None
    assert info.types == ()


def test_empty_standard_parts_accepted(tmp_path):
    d = manifest_plugin(tmp_path, "nothing", standard_parts=[])
    assert pyfeature.register_plugin(d) is not None
    assert pyfeature.plugin("nothing") is not None


def test_no_features_no_standard_parts_rejected(tmp_path):
    d = manifest_plugin(tmp_path, "empty")
    assert pyfeature.register_plugin(d) is None
    assert any("features" in msg for _, msg in pyfeature.problems())


def test_missing_class_records_problem(tmp_path):
    d = manifest_plugin(tmp_path, "bad", standard_parts=["peg:Peg", "peg:Nope"])
    (tmp_path / "bad" / "peg.py").write_text(open(os.path.join(PEGS, "peg.py")).read())
    assert pyfeature.register_plugin(d) is None
    assert any("Nope" in msg for _, msg in pyfeature.problems())
    assert pyfeature.plugin("bad") is None
    assert pyfeature.standard_part("sp:bad/peg") is None


def test_unregister_removes_standard_parts(pegs):
    assert pyfeature.unregister_plugin("pegs") is True
    assert pyfeature.standard_part(TYPE) is None
    assert [t for t, _, _ in pyfeature.standard_parts() if t.startswith("sp:pegs/")] == []


def test_replacing_plugin_keeps_one_entry(pegs):
    assert pyfeature.register_plugin(PEGS) is not None
    assert [t for t, _, _ in pyfeature.standard_parts() if t.startswith("sp:pegs/")] == [TYPE]
