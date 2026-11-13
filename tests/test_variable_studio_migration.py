"""I2.3: Variable Studios load through the "variables" migration chain; a tab file stamped with another tab's id
draws a Document warning."""
import json
import re

import pytest

from freeshape import doc_folder as DF
from freeshape import kernel as K
from freeshape import migrations as M
from freeshape.assembly import AssemblyDoc
from freeshape.variable_studio import VariableStudioDoc


def _saved(tmp_path):
    vs = VariableStudioDoc()
    vs.name = "Dims"
    vs.rows = [{"name": "w", "expression": "20 mm"}, {"name": "h", "expression": "#w * 2"}]
    path = str(tmp_path / "dims.fvars")
    vs.save(path)
    return path


def test_saved_variable_studio_reloads(tmp_path):
    path = _saved(tmp_path)
    back = VariableStudioDoc.load(path)
    assert back.name == "Dims"
    assert back.rows == [{"name": "w", "expression": "20 mm"}, {"name": "h", "expression": "#w * 2"}]


def test_latest_version_equals_version_written(tmp_path):
    path = _saved(tmp_path)
    assert json.load(open(path))["version"] == M.latest_version("variables") == 1


def test_file_written_before_the_chain_loads_unchanged(tmp_path):
    path = tmp_path / "old.fvars"
    path.write_text(json.dumps({"format": "freeshape-variables", "version": 1, "name": "Old",
                                "rows": [{"name": "a", "expression": "1"}]}))
    assert VariableStudioDoc.load(str(path)).rows == [{"name": "a", "expression": "1"}]


def test_too_new_file_is_refused_like_a_too_new_assembly(tmp_path):
    path = _saved(tmp_path)
    d = json.load(open(path))
    d["version"] = M.latest_version("variables") + 1
    json.dump(d, open(path, "w"))
    with pytest.raises(K.KernelError) as var_err:
        VariableStudioDoc.load(path)
    asm = tmp_path / "x.fasm"
    asm.write_text(json.dumps({"format": "freeshape-assembly", "version": M.latest_version("assembly") + 1}))
    with pytest.raises(K.KernelError) as asm_err:
        AssemblyDoc.load(str(asm))

    def shape(err, p):
        return re.sub(r"\d+", "N", str(err.value).replace(p, "<file>"))
    assert shape(var_err, path) == shape(asm_err, str(asm))
    assert str(var_err.value) == (f"{path} was saved with file version {d['version']}, but this build of FreeShape "
                                  f"only supports up to version {M.latest_version('variables')}. Update FreeShape "
                                  f"to open it.")


def test_registered_step_runs_on_load_and_is_written_back(tmp_path):
    path = _saved(tmp_path)
    steps = M._CHAINS["variables"]

    def add_note(d):
        d["rows"] = d["rows"] + [{"name": "migrated", "expression": "1"}]
        return d
    M.register("variables", add_note)
    try:
        assert M.latest_version("variables") == 2
        back = VariableStudioDoc.load(path)             # the file on disk is version 1
        assert [r["name"] for r in back.rows] == ["w", "h", "migrated"]
        back.save(path)
        assert json.load(open(path))["version"] == 2
    finally:
        steps.remove(add_note)
    assert M.latest_version("variables") == 1


def test_tab_file_stamped_with_another_tab_warns_naming_both(tmp_path):
    doc = DF.FolderDocument.create(str(tmp_path / "Doc"))
    a = doc.add_tab("variables")
    b = doc.add_tab("variables")
    doc.save()
    path = tmp_path / "Doc" / b.file
    d = json.load(open(path))
    d["_tab_id"] = a.id
    json.dump(d, open(path, "w"))
    back = DF.FolderDocument.open(doc.path)
    hits = [w for w in back.warnings if w["problem"] == "tab-id-mismatch"]
    assert len(hits) == 1
    assert hits[0]["tab"] == b.id
    assert a.id in hits[0]["message"] and b.id in hits[0]["message"]
    clean = DF.FolderDocument.create(str(tmp_path / "Clean"))
    clean.add_tab("variables")
    clean.save()
    assert not [w for w in DF.FolderDocument.open(clean.path).warnings if w["problem"] == "tab-id-mismatch"]
