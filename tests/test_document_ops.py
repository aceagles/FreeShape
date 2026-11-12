"""Document editing operations not covered elsewhere: suppress, rename, reorder, rollback insert."""
import math

from conftest import (FREEFORM, add, approx_rel, assert_golden, block_doc, edge_refs, line_along, only_part,
                      region_refs, sketch_feature, state_fingerprint)
from freeshape import kernel as K
from freeshape.document import Document

PI = math.pi


def _pocketed():
    """Block 40x20x10, a 4 mm deep r=5 pocket, then 4 vertical-edge fillets (r=3)."""
    doc = block_doc(40, 20, 10)
    sid = sketch_feature(doc, lambda s: s.add_circle(s.add_point(20, 10), 5))
    cut = add(doc, "extrude", regions=region_refs(doc, sid), depth=4.0, op="remove")
    fil = add(doc, "fillet", edges=edge_refs(only_part(doc), line_along([0, 0, 1]), expect=4), radius=3.0)
    return doc, sid, cut["id"], fil["id"]


FILLETED = 8000 - 4 * (9 - 9 * PI / 4) * 10
POCKET = 25 * PI * 4


def vol(doc):
    return K.volume(only_part(doc).shape)


def test_suppress_and_unsuppress():
    doc, _sid, cut, _fil = _pocketed()
    approx_rel(vol(doc), FILLETED - POCKET, rel=1e-4)
    doc.set_suppressed(cut, True)
    assert not doc.errors
    approx_rel(vol(doc), FILLETED, rel=1e-4)
    doc.set_suppressed(cut, False)
    approx_rel(vol(doc), FILLETED - POCKET, rel=1e-4)
    doc.undo()                                              # back to suppressed
    assert doc.feature(cut)["suppressed"] is True
    approx_rel(vol(doc), FILLETED, rel=1e-4)


def test_suppressed_survives_save_load(tmp_path):
    doc, _sid, cut, _fil = _pocketed()
    doc.set_suppressed(cut, True)
    path = str(tmp_path / "s.fshape")
    doc.save(path)
    back = Document.load(path)
    assert back.feature(cut)["suppressed"] is True and not back.errors
    approx_rel(vol(back), FILLETED, rel=1e-4)


def test_suppress_base_extrude_errors_downstream():
    doc, _sid, cut, fil = _pocketed()
    base = doc.features[1]["id"]
    doc.set_suppressed(base, True)
    assert not doc.state.parts
    assert set(doc.errors) == {cut, fil}, doc.errors
    doc.set_suppressed(base, False)
    assert not doc.errors


def test_rename_keeps_geometry_and_undoes(tmp_path):
    doc, _sid, cut, _fil = _pocketed()
    before = state_fingerprint(doc)
    old = doc.feature(cut)["name"]
    doc.rename_feature(cut, "Pocket")
    assert doc.feature(cut)["name"] == "Pocket"
    assert state_fingerprint(doc) == before
    path = str(tmp_path / "r.fshape")
    doc.save(path)
    assert Document.load(path).feature(cut)["name"] == "Pocket"
    doc.undo()
    assert doc.feature(cut)["name"] == old


def test_move_fillet_before_pocket_same_volume():
    doc, sid, cut, fil = _pocketed()
    doc.move_feature(fil, 2)
    assert [f["id"] for f in doc.features][2:] == [fil, sid, cut]
    assert not doc.errors, doc.errors
    approx_rel(vol(doc), FILLETED - POCKET, rel=1e-4)


def test_move_cut_above_its_sketch_errors_then_recovers():
    doc, sid, cut, fil = _pocketed()
    doc.move_feature(cut, 1)                                # before the block and its own sketch
    assert doc.errors.get(cut) == "Missing sketch", doc.errors
    doc.undo()
    assert [f["id"] for f in doc.features][2:] == [sid, cut, fil]
    assert not doc.errors


def test_move_past_end_clamps():
    doc, sid, cut, fil = _pocketed()
    doc.move_feature(sid, 99)
    assert doc.features[-1]["id"] == sid
    assert doc.errors.get(cut) == "Missing sketch"


def test_insert_at_rollback_bar():
    doc, sid, cut, fil = _pocketed()
    doc.set_rollback(2)                                     # only the block
    approx_rel(vol(doc), 8000)
    f = add(doc, "chamfer", edges=edge_refs(only_part(doc), line_along([0, 0, 1]), expect=4), distance=1.0)
    assert doc.features[2]["id"] == f["id"] and doc.rollback == 3
    approx_rel(vol(doc), 8000 - 4 * 0.5 * 10)
    doc.set_rollback(None)
    # The fillet's vertical edges were chamfered away. Today the resolver silently re-targets the fillet onto
    # one new edge per corner (no error, four r=3 faces) rather than reporting a missing reference.
    assert not doc.errors, doc.errors
    assert len([f for f in K.faces(only_part(doc).shape) if abs(K.face_info(f).get("r", 0) - 3) < 1e-9]) == 4
    assert_golden("document_ops/fillet_retargeted_after_upstream_chamfer", state_fingerprint(doc), FREEFORM)
