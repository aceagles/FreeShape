"""Part Studio files: atomic save and a format check on load."""
import json
import os

import pytest

from conftest import EXAMPLES_DIR
from freeshape import kernel as K
from freeshape.document import Document


def _studio(tmp_path):
    doc = Document.load(os.path.join(EXAMPLES_DIR, "hinge_parts.fshape"))
    path = str(tmp_path / "a.fshape")
    doc.save(path)
    return doc, path


def test_failed_replace_keeps_existing_file(tmp_path, monkeypatch):
    doc, path = _studio(tmp_path)
    before = open(path, "rb").read()
    doc.name = "changed"

    def boom(*a, **k):
        raise OSError("interrupted")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        doc.save(path)
    assert open(path, "rb").read() == before
    assert not os.path.exists(path + ".tmp")


def test_no_tmp_after_success(tmp_path):
    doc, path = _studio(tmp_path)
    assert os.path.exists(path)
    assert not os.path.exists(path + ".tmp")


def test_assembly_file_rejected(tmp_path):
    p = tmp_path / "x.fasm"
    p.write_text(json.dumps({"format": "freeshape-assembly", "version": 1, "features": []}))
    with pytest.raises(K.KernelError, match="Assembly"):
        Document.load(str(p))


def test_foreign_format_and_missing_features(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"format": "other", "features": []}))
    with pytest.raises(K.KernelError, match="not a FreeShape Part Studio|Not a FreeShape Part Studio"):
        Document.load(str(p))
    p.write_text(json.dumps({"name": "x"}))
    with pytest.raises(K.KernelError):
        Document.load(str(p))
    p.write_text(json.dumps({"format": "freeshape"}))
    with pytest.raises(K.KernelError):
        Document.load(str(p))


def test_missing_format_key_still_loads(tmp_path):
    doc, path = _studio(tmp_path)
    d = json.load(open(path))
    d.pop("format")
    p2 = tmp_path / "old.fshape"
    p2.write_text(json.dumps(d))
    assert Document.load(str(p2)).features == doc.features
