"""File version 5 (card I1.1): the Part Studio chain takes one pass-through step, `_onshape_parity_2`. A version 4
file loads through it unchanged, a version 1 file still builds to the same hashes, and a build without the step
refuses a version 5 file."""
import json
import os

import pytest

from conftest import EXAMPLES_DIR, assert_golden, state_fingerprint, v4_studio_json
from freeshape import kernel as K
from freeshape import migrations as Mig
from freeshape.document import Document


def _without_step(monkeypatch):
    steps = [s for s in Mig._CHAINS["document"] if s.__name__ != "_onshape_parity_2"]
    monkeypatch.setitem(Mig._CHAINS, "document", steps)


def _write(tmp_path, name, data):
    path = str(tmp_path / name)
    with open(path, "w") as fh:
        json.dump(data, fh)
    return path


def test_latest_version_is_5():
    assert Mig.latest_version("document") == 5


def test_new_part_studio_saves_as_version_5(tmp_path):
    assert json.loads(Document().to_json())["version"] == 5
    path = str(tmp_path / "new.fshape")
    Document().save(path)
    with open(path) as fh:
        assert json.load(fh)["version"] == 5


def test_version_4_studio_loads_and_resaves_as_version_5(tmp_path):
    v4 = v4_studio_json()
    assert v4["version"] == 4
    path = _write(tmp_path, "v4.fshape", v4)
    doc = Document.load(path)
    assert not doc.errors, doc.errors
    saved = json.loads(doc.to_json())
    assert saved["version"] == 5
    assert {k: v for k, v in saved.items() if k != "version"} == {k: v for k, v in v4.items() if k != "version"}


def test_version_4_studio_builds_the_same_with_or_without_the_step(tmp_path, monkeypatch):
    path = _write(tmp_path, "v4.fshape", v4_studio_json())
    with_step = Document.load(path)
    assert not with_step.errors, with_step.errors
    _without_step(monkeypatch)
    without = Document.load(path)
    assert not without.errors, without.errors
    got = state_fingerprint(with_step)
    assert got and list(got) == list(state_fingerprint(without))
    assert got == state_fingerprint(without)


def test_version_1_example_builds_to_the_base_hashes():
    doc = Document.load(os.path.join(EXAMPLES_DIR, "bracket.fshape"))
    assert not doc.errors, doc.errors
    assert_golden("file_versions/bracket", state_fingerprint(doc))


def test_version_6_is_refused(tmp_path):
    data = json.loads(Document().to_json())
    data["version"] = 6
    with pytest.raises(K.KernelError) as exc:
        Document.load(_write(tmp_path, "v6.fshape", data))
    assert "only supports up to version 5" in str(exc.value)


def test_version_5_is_refused_without_the_step(tmp_path, monkeypatch):
    path = _write(tmp_path, "v5.fshape", json.loads(Document().to_json()))
    _without_step(monkeypatch)
    with pytest.raises(K.KernelError) as exc:
        Document.load(path)
    assert "only supports up to version 4" in str(exc.value)
