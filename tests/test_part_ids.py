"""I1.4: part ids are <feature id>/<output index>; a v1 studio is migrated with a persisted old -> new alias
table, and old-syntax ids in unopened .fasm / .fdwg files keep binding the same part until they are saved."""
import json
import os
import re
import shutil

from conftest import (EXAMPLES_DIR, FREEFORM, TOP, add, assert_golden, region_refs, shape_fingerprint,
                      sketch_feature, rect_lines, state_fingerprint)
from freeshape import links as L
from freeshape import migrations as Mig
from freeshape.assembly import AssemblyDoc
from freeshape.document import Document
from freeshape.drawing import DrawingDoc

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "part_ids")
OLD_ID = re.compile(r"\bF\d+:\d+")
HINGE_ALIASES = {"F2:1": "F2/1", "F4:2": "F4/1", "F6:3": "F6/1"}


def _two_block_doc():
    """Sketch + new-part extrude, twice (blocks at x=0 and x=100). Returns (doc, id of the second extrude)."""
    doc = Document()
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 20, 20), TOP)
    add(doc, "extrude", regions=region_refs(doc, sid), depth=10.0, op="new")
    sid2 = sketch_feature(doc, lambda s: rect_lines(s, 100, 0, 20, 20), TOP)
    f2 = add(doc, "extrude", regions=region_refs(doc, sid2), depth=10.0, op="new")
    return doc, f2["id"]


def test_part_id_is_feature_id_and_output_index():
    doc, fid = _two_block_doc()
    first = doc.features[1]["id"]
    assert sorted(doc.state.parts) == sorted([f"{first}/1", f"{fid}/1"])


def test_inserting_an_upstream_extrude_keeps_later_part_ids():
    doc, fid = _two_block_doc()
    before = state_fingerprint(doc)
    assert f"{fid}/1" in before
    sid = sketch_feature(doc, lambda s: rect_lines(s, 200, 0, 20, 20), TOP)    # appended, then moved up front
    doc.move_feature(sid, 0)
    new = doc.make_feature("extrude")
    new["params"].update(regions=region_refs(doc, sid), depth=10.0, op="new")
    doc.add_feature(new, index=1)
    assert not doc.errors, doc.errors
    after = state_fingerprint(doc)
    assert f"{new['id']}/1" in after and len(after) == len(before) + 1
    for pid, fp in before.items():                       # every earlier part kept its id and its shape
        assert pid in after
        assert after[pid]["volume"] == fp["volume"]


def test_v1_studio_migration_builds_alias_table(tmp_path):
    path = str(tmp_path / "hinge_parts.fshape")
    shutil.copy(os.path.join(EXAMPLES_DIR, "hinge_parts.fshape"), path)
    doc = Document.load(path)
    assert not doc.errors, doc.errors
    assert doc.part_aliases == HINGE_ALIASES
    assert sorted(doc.state.parts) == sorted(HINGE_ALIASES.values())
    assert set(doc.part_overrides) == set(HINGE_ALIASES.values())      # the studio's own references moved too
    assert doc.state.parts["F4:2"] is doc.state.parts["F4/1"]           # a stale lookup still finds the part


def _resave_studio_alone(tmp_path):
    studio = str(tmp_path / "hinge_parts.fshape")
    shutil.copy(os.path.join(EXAMPLES_DIR, "hinge_parts.fshape"), studio)
    shutil.copy(os.path.join(EXAMPLES_DIR, "hinge.fasm"), str(tmp_path / "hinge.fasm"))
    shutil.copy(os.path.join(DATA, "arm_v1.fdwg"), str(tmp_path / "arm_v1.fdwg"))
    return studio


def _bound_fingerprints(tmp_path):
    r = L.LinkResolver()
    asm = AssemblyDoc.load(str(tmp_path / "hinge.fasm"), resolver=r.resolve_source)
    assert not asm.errors, asm.errors
    g = DrawingDoc.load(str(tmp_path / "arm_v1.fdwg"), resolver=r.resolve_drawing_source)
    assert g.error is None, g.error
    return r, asm, g, ({i["id"]: shape_fingerprint(asm.parts[i["id"]].shape) for i in asm.instances},
                       shape_fingerprint(g.shape))


def test_old_ids_in_unopened_fasm_and_fdwg_bind_after_studio_resave(tmp_path):
    studio = _resave_studio_alone(tmp_path)
    _, _, _, (inst_before, view_before) = _bound_fingerprints(tmp_path)
    assert_golden("link_resolver/hinge_instances", inst_before, FREEFORM)

    doc = Document.load(studio)                     # re-save the studio alone; the .fasm/.fdwg stay v1
    doc.save()
    for name in ("hinge.fasm", "arm_v1.fdwg"):
        with open(tmp_path / name) as fh:
            assert json.load(fh)["version"] == 1

    _, _, _, (inst_after, view_after) = _bound_fingerprints(tmp_path)
    assert inst_after == inst_before
    assert view_after == view_before
    assert view_after == shape_fingerprint(doc.state.parts["F4/1"].shape)


def test_resaved_studio_holds_alias_table_and_dependents_write_new_ids(tmp_path):
    studio = _resave_studio_alone(tmp_path)
    Document.load(studio).save()
    with open(studio) as fh:
        saved = json.load(fh)
    assert saved["version"] == Mig.latest_version("document") == 3
    assert saved["part_aliases"] == HINGE_ALIASES
    assert not OLD_ID.search(json.dumps({k: v for k, v in saved.items() if k != "part_aliases"}))

    r, asm, g, _ = _bound_fingerprints(tmp_path)
    L.repair_links(r, asm)
    L.repair_links(r, g)
    asm.save()
    g.save()
    for name, fmt in (("hinge.fasm", "assembly"), ("arm_v1.fdwg", "drawing")):
        text = (tmp_path / name).read_text()
        assert json.loads(text)["version"] == Mig.latest_version(fmt)
        assert not OLD_ID.search(text), name
    with open(tmp_path / "hinge.fasm") as fh:
        parts = {i["source"]["part"] for i in json.load(fh)["instances"]}
    assert parts == set(HINGE_ALIASES.values())
    with open(tmp_path / "arm_v1.fdwg") as fh:
        assert json.load(fh)["source"]["part"] == "F4/1"
