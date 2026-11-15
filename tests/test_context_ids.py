"""Context bodies are identified by Assembly instance, not by position (I6.1, ADR 0004).

A Context made by Edit in context has params["ids"] == "instance": its bodies are `<fid>/<instance id>`, so a
hidden flag, a part override and a feature reference stay on the same Assembly instance when Update context
recaptures after an earlier instance was deleted. A Context without the key keeps `<fid>/<k>` by position."""
import json
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conftest import add, block_doc, rect_lines, region_refs, sketch_feature, shape_fingerprint
from freeshape import assembly as A
from freeshape import blobstore as B
from freeshape import doc_folder as DF
from freeshape import features as F
from freeshape import kernel as K
from freeshape import links as L
from freeshape import naming as N
from freeshape.pyfeature import host
from tests.test_assembly import box, make, src


def _at(x, y, z):
    T = np.eye(4)
    T[:3, 3] = (x, y, z)
    return T


def _folder(tmp_path, same, ids="instance"):
    """A Document: a Plate studio holding a Context of the Plate instance, and an Assembly of the Plate and three
    neighbours stacked over it, A at z 20, B at z 40 (a Pin, bottom face at z 40) and C (a Cap) at z 60. With
    `same` A is a second instance of the Pin part, else a Cap. `ids` is the Context's ids param (None: absent).
    Returns (folder, plate tab, asm tab, context id, {"A", "B", "C"} -> instance id)."""
    folder = DF.FolderDocument.create(str(tmp_path / "Doc"))
    plate = folder.add_tab("studio", "Plate")
    plate.doc.__dict__.update(block_doc(40, 20, 10).__dict__)
    pin = folder.add_tab("studio", "Pin")
    pin.doc.__dict__.update(block_doc(10, 10, 10).__dict__)
    cap = folder.add_tab("studio", "Cap")
    cap.doc.__dict__.update(block_doc(6, 6, 4).__dict__)
    for t in (plate, pin, cap):
        folder._attach(t)
    asm = folder.add_tab("assembly", "Asm")
    a = asm.doc

    def source(tab):
        return L.studio_source(tab, f"{tab.doc.features[1]['id']}/1")

    ip = a.insert(source(plate), T=np.eye(4), name="Plate")
    iids = {"A": a.insert(source(pin if same else cap), T=_at(0, 0, 20), name="A"),
            "B": a.insert(source(pin), T=_at(0, 0, 40), name="B"),
            "C": a.insert(source(cap), T=_at(20, 0, 60), name="C")}
    a.refresh(solve=False, notify=False)
    key = plate.doc.store_context(A.capture_context(a, ip))
    params = dict(assembly=a.uid, instance=ip, capture=key, **({"ids": ids} if ids is not None else {}))
    ctx = add(plate.doc, "context", **params)
    return folder, plate, asm, ctx["id"], iids


def _zmin(doc, pid):
    return float(K.bbox(doc.state.parts[pid].shape)[0][2])


def _delete_a_and_update(folder, plate, asm, cid, iids):
    asm.doc.delete_instances([iids["A"]])
    assert folder.context_status(plate.id, cid) == "out of date"
    folder.update_context(plate.id, cid)
    assert folder.context_status(plate.id, cid) == "current"
    assert not plate.doc.errors, plate.doc.errors


def _reopen(folder, tab_id):
    folder.save()
    again = DF.FolderDocument.open(folder.path)
    return again, again.tab(tab_id)


# =========================================================================== Edit in context writes ids

@pytest.fixture
def window(tmp_path, monkeypatch):
    from tests.test_context_ui import make_rig
    r = make_rig(tmp_path, monkeypatch)
    yield r
    r.w.folder = None
    r.w.deleteLater()


def test_edit_in_context_writes_ids_instance_and_names_bodies_by_instance(window):
    r = window
    assert r.w.asm.edit_in_context() is None
    ctx = [f for f in r.plate.doc.features if f["type"] == "context"]
    assert len(ctx) == 1 and ctx[0]["params"]["ids"] == "instance"
    fid = ctx[0]["id"]
    want = {f"{fid}/{iid}" for iid in (r.ia, r.ib, r.ic)}
    assert {pid for pid, p in r.plate.doc.state.parts.items() if p.reference} == want
    assert all(r.plate.doc.state.parts[f"{fid}/{i}"].name == r.asm.doc.instance(i)["name"] for i in (r.ia, r.ib, r.ic))
    assert want <= r.w.viewport.reference_ids              # I6.4's pick rule reads the 7th scene item


# =========================================================================== identity across Update context

@pytest.mark.parametrize("same", [False, True], ids=["different_parts", "same_part"])
def test_hidden_b_stays_hidden_after_a_is_deleted(tmp_path, same):
    folder, plate, asm, cid, iids = _folder(tmp_path, same)
    b, c = f"{cid}/{iids['B']}", f"{cid}/{iids['C']}"
    assert {f"{cid}/{iids[k]}" for k in "ABC"} <= set(plate.doc.state.parts)
    plate.doc.hidden_parts = {b}
    _delete_a_and_update(folder, plate, asm, cid, iids)

    def check(doc):
        assert f"{cid}/{iids['A']}" not in doc.state.parts
        assert doc.hidden_parts == {b}
        assert abs(_zmin(doc, b) - 40) < 1e-6 and abs(_zmin(doc, c) - 60) < 1e-6    # B is still B, C shown
        assert c in doc.state.parts and c not in doc.hidden_parts

    check(plate.doc)
    plate.doc.undo()                                      # back to the capture that has A
    assert f"{cid}/{iids['A']}" in plate.doc.state.parts and plate.doc.hidden_parts == {b}
    assert abs(_zmin(plate.doc, b) - 40) < 1e-6
    plate.doc.redo()
    check(plate.doc)
    _again, tab = _reopen(folder, plate.id)
    check(tab.doc)


def _extrude_up_to_b(doc, cid, iid_b):
    """A 2 x 2 bar from the Top plane up to the bottom face of B, by a named reference."""
    pid = f"{cid}/{iid_b}"
    part = doc.state.parts[pid]
    bottom = min(K.faces(part.shape), key=lambda f: K.interior_point(f)[2])
    ref = F.make_ref(pid, bottom, "face", part.shape, named=doc.state.named(pid))
    assert "name" in ref
    sid = sketch_feature(doc, lambda s: rect_lines(s, 2, 2, 2, 2))
    return add(doc, "extrude", regions=region_refs(doc, sid), end="up_to_face",
               up_to={"kind": "face", "ref": ref}, op="new")["id"]


def _bar_top(doc, eid):
    pid = next(p for p in doc.state.parts if p.startswith(eid + "/"))
    return float(K.bbox(doc.state.parts[pid].shape)[1][2])


@pytest.mark.parametrize("same", [False, True], ids=["different_parts", "same_part"])
def test_extrude_up_to_b_still_ends_on_b(tmp_path, same):
    folder, plate, asm, cid, iids = _folder(tmp_path, same)
    eid = _extrude_up_to_b(plate.doc, cid, iids["B"])
    assert abs(_bar_top(plate.doc, eid) - 40) < 1e-6
    _delete_a_and_update(folder, plate, asm, cid, iids)
    assert not plate.doc.errors and eid not in plate.doc.state.recovered     # found by name, not by signature
    assert abs(_bar_top(plate.doc, eid) - 40) < 1e-6      # on B, not on A's old place (z 20) nor C (z 60)
    _again, tab = _reopen(folder, plate.id)
    assert not tab.doc.errors and abs(_bar_top(tab.doc, eid) - 40) < 1e-6


@pytest.mark.parametrize("same", [False, True], ids=["different_parts", "same_part"])
def test_colour_override_on_b_stays_on_b(tmp_path, same):
    folder, plate, asm, cid, iids = _folder(tmp_path, same)
    b = f"{cid}/{iids['B']}"
    plate.doc.set_part_colors({b: (0.9, 0.1, 0.1)})
    _delete_a_and_update(folder, plate, asm, cid, iids)
    assert set(plate.doc.part_overrides) == {b}
    assert tuple(plate.doc.state.parts[b].color) == (0.9, 0.1, 0.1) and abs(_zmin(plate.doc, b) - 40) < 1e-6
    c = f"{cid}/{iids['C']}"
    assert tuple(plate.doc.state.parts[c].color) != (0.9, 0.1, 0.1)
    _again, tab = _reopen(folder, plate.id)
    assert tuple(tab.doc.state.parts[b].color) == (0.9, 0.1, 0.1) and abs(_zmin(tab.doc, b) - 40) < 1e-6


# =========================================================================== old Contexts unchanged

def _expected_bodies(doc, cid):
    """[(shape fingerprint, element names)] per neighbour in capture order, derived from the stored blobs the way
    run_context has always restored them (located BREP, captured names plus the CTX fragment)."""
    f = doc.feature(cid)
    listing = F.context_listing(doc._blob_get, f["params"]["capture"])
    out = []
    for n in listing["neighbours"]:
        shape = K.located(B.shape_from_brep(doc._blob_get(n["brep"])), np.asarray(n["T"], float))
        named = N.NamedShape(shape, {})
        host._load_names(doc._blob_get(n["names"]), named)
        label = "".join(c for c in n["id"] if c.isalnum() or c == "_") + "_"
        out.append((n["name"], shape_fingerprint(shape), dict(N.name_context(shape, named.names, cid, label).names)))
    return out


def test_context_without_ids_keeps_positional_bodies(tmp_path):
    folder, plate, asm, cid, iids = _folder(tmp_path, True, ids=None)
    doc = plate.doc
    assert "ids" not in doc.feature(cid)["params"]
    refs = [pid for pid, p in doc.state.parts.items() if p.reference]
    assert refs == [f"{cid}/1", f"{cid}/2", f"{cid}/3"]
    for pid, (name, fp, names) in zip(refs, _expected_bodies(doc, cid)):
        assert doc.state.parts[pid].name == name
        assert shape_fingerprint(doc.state.parts[pid].shape) == fp
        assert dict(doc.state.named(pid).names) == names
    assert [abs(_zmin(doc, p)) for p in refs] == pytest.approx([20, 40, 60])
    _again, tab = _reopen(folder, plate.id)               # reopening converts nothing (that is I6.2's job)
    assert "ids" not in tab.doc.feature(cid)["params"]
    assert [pid for pid, p in tab.doc.state.parts.items() if p.reference] == refs


def test_ids_instance_bodies_match_positional_ones_except_for_the_id(tmp_path):
    folder, plate, asm, cid, iids = _folder(tmp_path, True)
    doc = plate.doc
    for k, (name, fp, names) in zip("ABC", _expected_bodies(doc, cid)):
        pid = f"{cid}/{iids[k]}"
        assert doc.state.parts[pid].name == name and doc.state.parts[pid].reference
        assert shape_fingerprint(doc.state.parts[pid].shape) == fp
        assert dict(doc.state.named(pid).names) == names


def test_unknown_ids_setting_is_an_error(tmp_path):
    folder, plate, asm, cid, iids = _folder(tmp_path, False)
    f = dict(plate.doc.feature(cid))
    f["params"] = dict(f["params"], ids="sideways")
    plate.doc.replace_feature(f)
    assert "unknown ids setting" in plate.doc.errors.get(cid, "")


# =========================================================================== severed id parse

def test_named_lookup_parses_a_severed_instance_id():
    state = F.ModelState()
    for pid, at in (("F7/I30", (0, 0, 0)), ("F7/I3.1", (20, 0, 0)), ("F7/I3.3", (40, 0, 0))):
        shape = box(5, 5, 5, at)
        state.parts[pid] = F.Part(pid, pid, shape)
        state.set_named(pid, N.seed(shape, "F9"))     # every piece carries the same names
    sub, name = next(iter(state.named("F7/I3.1").named(N.FACE)))
    state.set_named("F7/I3.3", N.NamedShape(state.parts["F7/I3.3"].shape, {}))   # lost its names
    hit = F._named_lookup(state, {"part": "F7/I3.3", "name": name}, N.FACE)
    assert hit is not None and hit[0] == "F7/I3.1"       # a sibling piece of I3, never the unrelated I30


# =========================================================================== Assembly instance ids are never reused

def test_deleting_the_newest_instance_does_not_free_its_id(tmp_path):
    lib = {"a": box(10, 10, 10)}
    a = make(lib)
    ids = [a.insert(src("a")) for _ in range(3)]
    assert ids == ["I1", "I2", "I3"]
    assert "id_counter" not in json.loads(a.to_json())     # nothing freed: the file is written as before
    a.delete_instances(["I3"])
    assert json.loads(a.to_json())["id_counter"] == 3
    path = str(tmp_path / "a.fasm")
    a.save(path)
    b = A.AssemblyDoc.load(path, resolver=a.resolver)
    assert b.insert(src("a")) == "I4"
    old = json.loads(a.to_json())
    old.pop("id_counter")                                  # a file from before I6.1 reads as it always did
    c = A.AssemblyDoc.from_data(json.dumps(old).encode(), resolver=a.resolver)
    assert c.insert(src("a")) == "I3"
