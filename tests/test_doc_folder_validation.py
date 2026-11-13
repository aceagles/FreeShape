"""I2.1: one damaged tab or manifest does not stop a Document opening; a damaged manifest is refused with a
message that names the fault; unknown manifest keys survive a save; a tab in `errors` keeps its file on save."""
import json
import os

import pytest

from freeshape import doc_folder as DF
from freeshape import kernel as K


def _four_tab_doc(tmp_path):
    doc = DF.FolderDocument.create(str(tmp_path / "Doc"))
    for kind in ("studio", "studio", "assembly", "drawing"):
        doc.add_tab(kind)
    doc.save()
    return doc


def _manifest(path):
    with open(os.path.join(path, DF.MANIFEST_NAME)) as fh:
        return json.load(fh)


def _write(path, data):
    with open(os.path.join(path, DF.MANIFEST_NAME), "w") as fh:
        fh.write(data if isinstance(data, str) else json.dumps(data))


def test_truncated_tab_file_leaves_the_others_open(tmp_path):
    doc = _four_tab_doc(tmp_path)
    bad = doc.tabs[3]
    full = os.path.join(doc.path, bad.file)
    with open(full) as fh:
        text = fh.read()
    with open(full, "w") as fh:
        fh.write(text[: len(text) // 2])
    opened = DF.FolderDocument.open(doc.path)
    assert [t.doc is not None for t in opened.tabs] == [True, True, True, False]
    assert list(opened.errors) == [bad.id]
    assert opened.errors[bad.id].strip()
    assert any(bad.id in w["message"] for w in opened.warnings)


def test_tab_file_missing_a_key_is_recorded(tmp_path):
    doc = _four_tab_doc(tmp_path)
    full = os.path.join(doc.path, doc.tabs[1].file)
    with open(full, "w") as fh:
        fh.write("[]")
    opened = DF.FolderDocument.open(doc.path)
    assert doc.tabs[1].id in opened.errors
    assert sum(t.doc is not None for t in opened.tabs) == 3


def _faults():
    ok = {"id": "T1", "kind": "studio", "file": "a.fshape", "name": "A"}
    base = {"format": DF.FORMAT, "version": 1, "id": "x", "name": "N"}
    return [
        ("not valid JSON", "{nope", "not valid JSON"),
        ("top level", [1, 2], "top level"),
        ("tabs list", {**base, "tabs": {"a": 1}}, "tabs must be a list"),
        ("entry object", {**base, "tabs": [ok, "T2"]}, "must be an object"),
        ("empty id", {**base, "tabs": [{**ok, "id": ""}]}, "empty or non-string id"),
        ("non-string id", {**base, "tabs": [{**ok, "id": 7}]}, "empty or non-string id"),
        ("duplicate", {**base, "tabs": [ok, {**ok, "file": "b.fshape"}]}, "duplicate tab id"),
        ("version", {**base, "version": "1", "tabs": []}, "version must be an integer"),
    ]


@pytest.mark.parametrize("name,content,fragment", _faults(), ids=[f[0] for f in _faults()])
def test_manifest_fault_raises_kernel_error(tmp_path, name, content, fragment):
    folder = tmp_path / "Doc"
    folder.mkdir()
    _write(str(folder), content)
    with pytest.raises(K.KernelError, match=fragment):
        DF.FolderDocument.open(str(folder))


def test_unknown_keys_survive_open_and_save(tmp_path):
    doc = _four_tab_doc(tmp_path)
    data = _manifest(doc.path)
    data["future_top"] = {"a": [1]}
    data["tabs"][2]["future_tab"] = "keep"
    _write(doc.path, data)
    opened = DF.FolderDocument.open(doc.path)
    opened.name = "Renamed"
    opened.save()
    out = _manifest(doc.path)
    assert out["future_top"] == {"a": [1]}
    assert out["tabs"][2]["future_tab"] == "keep"
    assert "future_tab" not in out["tabs"][0]
    assert out["name"] == "Renamed"


def test_save_keeps_the_file_of_a_tab_in_errors(tmp_path):
    doc = _four_tab_doc(tmp_path)
    bad = doc.tabs[0]
    full = os.path.join(doc.path, bad.file)
    with open(full, "w") as fh:
        fh.write("{broken")
    opened = DF.FolderDocument.open(doc.path)
    assert bad.id in opened.errors
    opened.tabs[1].doc.dirty = True
    opened.save()
    with open(full) as fh:
        assert fh.read() == "{broken"
    assert any(t["id"] == bad.id for t in _manifest(doc.path)["tabs"])
