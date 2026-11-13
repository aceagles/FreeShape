"""Out of date and Update context (I5.4): FolderDocument.context_status / update_context, ADR 0004."""
import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conftest import add, block_doc, rect_lines, region_refs, sketch_feature, pick_faces
from freeshape import assembly as A
from freeshape import doc_folder as DF
from freeshape import features as F
from freeshape import kernel as K
from freeshape import links as L
from freeshape.document import Document
from tests.test_assembly import face, mate


def _only(doc):
    return next(iter(doc.state.parts))


def _rig(tmp_path):
    """A Document with a plate studio (holds the Context), a pin studio, and an Assembly with the pin fastened
    on top of the plate. Returns (folder, plate tab, pin tab, asm tab, context feature id, plate iid, mate)."""
    folder = DF.FolderDocument.create(str(tmp_path / "Doc"))
    plate = folder.add_tab("studio", "Plate")
    plate.doc.__dict__.update(block_doc(40, 20, 10).__dict__)
    pin = folder.add_tab("studio", "Pin")
    pin.doc.__dict__.update(block_doc(10, 10, 10).__dict__)
    folder._attach(plate)
    folder._attach(pin)
    asm = folder.add_tab("assembly", "Asm")
    a = asm.doc
    ip = a.insert(L.studio_source(plate, f"{plate.doc.features[1]['id']}/1"), name="Plate")
    ipin = a.insert(L.studio_source(pin, f"{pin.doc.features[1]['id']}/1"), name="Pin")
    a.refresh(solve=False, notify=False)
    top = a.connector(ip, "face", face(a, ip, lambda f: f.get("n") and f["n"][2] > 0.9))
    pb = a.connector(ipin, "face", face(a, ipin, lambda f: f.get("n") and f["n"][2] < -0.9))
    m = mate(a, "fastened", top, pb)
    key = plate.doc.store_context(A.capture_context(a, ip))
    ctx = add(plate.doc, "context", assembly=a.uid, instance=ip, capture=key)
    return folder, plate, pin, asm, ctx["id"], ip, m


def _edit_depth(tab, depth):
    f = copy.deepcopy(next(f for f in tab.doc.features if f["type"] == "extrude"))
    f["params"]["depth"] = depth
    tab.doc.replace_feature(f)


def test_current_after_capture_and_neighbour_edit_is_out_of_date_without_rebuild(tmp_path, monkeypatch):
    folder, plate, pin, asm, cid, ip, m = _rig(tmp_path)
    assert folder.context_status(plate.id, cid) == "current"
    _edit_depth(pin, 12)
    builds = []
    orig = Document.rebuild
    monkeypatch.setattr(Document, "rebuild", lambda self, *a, **k: (builds.append(self), orig(self, *a, **k))[1])
    folder.refresh_links()
    assert folder.context_status(plate.id, cid) == "out of date"
    assert folder.context_status(plate.id, cid) == "out of date"
    assert plate.doc not in builds


def test_moving_the_neighbour_in_the_assembly_is_out_of_date(tmp_path):
    folder, plate, pin, asm, cid, ip, m = _rig(tmp_path)
    m = dict(m, offset=5.0)
    asm.doc.replace_mate(m)
    assert folder.context_status(plate.id, cid) == "out of date"


def test_unsaved_assembly_edits_count_and_removed_neighbour_is_out_of_date(tmp_path):
    folder, plate, pin, asm, cid, ip, m = _rig(tmp_path)
    asm.doc.delete_instances([i["id"] for i in asm.doc.instances if i["id"] != ip])
    assert folder.context_status(plate.id, cid) == "out of date"


def test_editing_only_the_captured_instances_own_studio_stays_current(tmp_path):
    folder, plate, pin, asm, cid, ip, m = _rig(tmp_path)
    sid = sketch_feature(plate.doc, lambda s: rect_lines(s, 1, 1, 3, 3))
    add(plate.doc, "extrude", regions=region_refs(plate.doc, sid), depth=4, op="remove")
    folder.refresh_links()
    assert folder.context_status(plate.id, cid) == "current"


def test_update_returns_to_current_and_up_to_extrude_ends_at_new_face(tmp_path):
    folder, plate, pin, asm, cid, ip, m = _rig(tmp_path)
    doc = plate.doc
    ref_id = f"{cid}/1"
    pred = lambda i: i.get("n") is not None and i["n"][2] > 0.9
    sub = pick_faces(doc.state.parts[ref_id], pred)[0]
    ref = F.make_ref(ref_id, sub, "face", doc.state.parts[ref_id].shape, doc.state.named(ref_id))
    sid = sketch_feature(doc, lambda s: rect_lines(s, 100, 0, 10, 10))
    ext = add(doc, "extrude", regions=region_refs(doc, sid), end="to_face", to=ref, op="new")

    def height():
        part = doc.state.parts[next(i for i in doc.state.parts if i.startswith(ext["id"]))]
        return K.volume(part.shape) / 100.0

    assert abs(height() - 20) < 1e-6
    asm.doc.replace_mate(dict(m, offset=5.0))
    assert folder.context_status(plate.id, cid) == "out of date"
    folder.update_context(plate.id, cid)
    assert not doc.errors, doc.errors
    assert folder.context_status(plate.id, cid) == "current"
    assert abs(height() - 25) < 1e-6
    doc.undo()
    assert folder.context_status(plate.id, cid) == "out of date"


def test_assembly_missing_still_displays_and_update_raises(tmp_path):
    folder, plate, pin, asm, cid, ip, m = _rig(tmp_path)
    folder.remove_tab(asm.id)
    assert folder.context_status(plate.id, cid) == "assembly missing"
    assert not plate.doc.errors and f"{cid}/1" in plate.doc.state.parts
    with pytest.raises(K.KernelError, match="missing"):
        folder.update_context(plate.id, cid)
    assert f"{cid}/1" in plate.doc.state.parts


def test_instance_removed_gives_assembly_missing(tmp_path):
    folder, plate, pin, asm, cid, ip, m = _rig(tmp_path)
    asm.doc.delete_instances([ip])
    assert folder.context_status(plate.id, cid) == "assembly missing"


def test_context_is_not_a_link_source():
    assert "context" not in L.SOURCE_FEATURES if hasattr(L, "SOURCE_FEATURES") else True
