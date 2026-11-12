"""File versioning: FILE_VERSION is checked on load and the migration chain in freeshape/migrations.py is
applied in order, for every format (.fshape, .fasm, .fdwg)."""
import json
import os
import shutil

import pytest

from conftest import ANALYTIC, EXAMPLES_DIR, assert_golden, shape_fingerprint, state_fingerprint
from freeshape import assembly as A
from freeshape import drawing as DW
from freeshape import kernel as K
from freeshape import migrations as Mig
from freeshape.document import Document

VERSIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "versions")


def _hinge_resolver():
    studio = Document.load(os.path.join(EXAMPLES_DIR, "hinge_parts.fshape"))

    def resolve(src):
        if src.get("uid") != studio.uid:
            raise K.KernelError("Part Studio isn't open")
        p = studio.state.parts.get(src.get("part"))
        if p is None:
            raise K.KernelError("The part no longer exists")
        return p
    return studio, resolve


def _assembly_fingerprint(asm):
    return {iid: shape_fingerprint(asm.world_shape(iid)) for iid in asm.parts}


# =========================================================================== save / load round trips
@pytest.mark.parametrize("name", ["hinge_parts", "bracket"])
def test_studio_round_trip(name, tmp_path):
    doc = Document.load(os.path.join(EXAMPLES_DIR, f"{name}.fshape"))
    assert not doc.errors, doc.errors
    before = state_fingerprint(doc)
    path = str(tmp_path / f"{name}.fshape")
    doc.save(path)
    back = Document.load(path)
    assert not back.errors, back.errors
    after = state_fingerprint(back)
    assert_golden(f"file_versions/{name}", before)
    assert_golden(f"file_versions/{name}_reloaded", after)


def test_assembly_round_trip(tmp_path):
    _, resolve = _hinge_resolver()
    asm = A.AssemblyDoc.load(os.path.join(EXAMPLES_DIR, "hinge.fasm"), resolver=resolve)
    assert not asm.errors, asm.errors
    before = _assembly_fingerprint(asm)
    path = str(tmp_path / "hinge.fasm")
    asm.save(path)
    _, resolve2 = _hinge_resolver()
    back = A.AssemblyDoc.load(path, resolver=resolve2)
    assert not back.errors, back.errors
    after = _assembly_fingerprint(back)
    assert_golden("file_versions/hinge_fasm", before)
    assert_golden("file_versions/hinge_fasm_reloaded", after)


def test_drawing_round_trip(tmp_path):
    g = DW.DrawingDoc.load(os.path.join(VERSIONS_DIR, "v1.fdwg"))
    assert g.shape is not None
    before = shape_fingerprint(g.shape)
    path = str(tmp_path / "v1.fdwg")
    g.save(path)
    back = DW.DrawingDoc.load(path)
    assert back.shape is not None
    after = shape_fingerprint(back.shape)
    assert_golden("file_versions/v1_fdwg", before)
    assert_golden("file_versions/v1_fdwg_reloaded", after)


# =========================================================================== version gating
@pytest.mark.parametrize("fmt, fixture, loader", [
    ("document", os.path.join(EXAMPLES_DIR, "hinge_parts.fshape"), Document.load),
    ("assembly", os.path.join(EXAMPLES_DIR, "hinge.fasm"), A.AssemblyDoc.load),
    ("drawing", os.path.join(VERSIONS_DIR, "v1.fdwg"), DW.DrawingDoc.load),
])
def test_file_from_the_future_fails_to_load(fmt, fixture, loader, tmp_path):
    with open(fixture) as fh:
        d = json.load(fh)
    latest = Mig.latest_version(fmt)
    d["version"] = latest + 1
    path = str(tmp_path / os.path.basename(fixture))
    with open(path, "w") as fh:
        json.dump(d, fh)
    with pytest.raises(K.KernelError) as exc:
        loader(path)
    msg = str(exc.value)
    assert str(latest + 1) in msg and str(latest) in msg


# =========================================================================== migration chain
def test_migration_steps_run_once_in_registration_order(tmp_path):
    calls = []

    def step_to_2(d):
        assert d.get("version", 1) == 1
        calls.append("1->2")
        d = dict(d)
        d["_marker"] = d.get("_marker", 0) + 1
        return d

    def step_to_3(d):
        assert d.get("_marker") == 1          # proves step_to_2 already ran before this one
        calls.append("2->3")
        d = dict(d)
        d["_marker"] += 1
        return d

    Mig.register("document", step_to_2)
    Mig.register("document", step_to_3)
    try:
        assert Mig.latest_version("document") == 3
        path = str(tmp_path / "bracket.fshape")
        shutil.copy(os.path.join(EXAMPLES_DIR, "bracket.fshape"), path)
        doc = Document.load(path)
        assert calls == ["1->2", "2->3"]      # each step ran exactly once, in order
        resaved = str(tmp_path / "resaved.fshape")
        doc.save(resaved)
        with open(resaved) as fh:
            saved = json.load(fh)
        assert saved["version"] == 3           # the saved file carries the latest version
    finally:
        Mig._CHAINS["document"].clear()
