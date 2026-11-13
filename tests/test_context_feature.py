"""The "context" feature: a stored capture (assembly.capture_context, ADR 0004) restored as reference bodies."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conftest import add, block_doc, state_fingerprint
from freeshape import assembly as A
from freeshape import blobstore
from freeshape import kernel as K
from freeshape import naming as N
from freeshape.document import Document
from tests.test_assembly import box, cyl, face, make, mate, src


def capture(named=True):
    """The capture of the plate in a plate-and-pin Assembly, and the pin's world shape and the plate's pose."""
    lib = {"plate": box(60, 40, 10), "pin": cyl(4, 30)}
    a = make(lib)
    ip, ipin = a.insert(src("plate")), a.insert(src("pin"))
    top = a.connector(ip, "face", face(a, ip, lambda f: f.get("n") and f["n"][2] > 0.9))
    pb = a.connector(ipin, "face", face(a, ipin, lambda f: f.get("n") and f["n"][2] < -0.9))
    mate(a, "fastened", top, pb)
    if named:
        a.named_resolver = lambda s: N.seed(a.parts[ipin].shape, "F9") if s["part"] == "pin" else None
    return A.capture_context(a, ip), a.world_shape(ipin), a.T(ip)


def studio_with_context(cap):
    doc = block_doc()
    key = doc.store_context(cap)
    add(doc, "context", assembly=cap["assembly"], instance=cap["instance"], capture=key)
    return doc, key


def bb(shape):
    lo, hi = K.bbox(shape)
    return np.array([lo, hi])


def test_builds_neighbour_at_captured_position_without_assembly():
    cap, pin_world, plate_T = capture()
    doc, _ = studio_with_context(cap)         # the capture is plain data: nothing here can reach the Assembly
    fid = doc.features[-1]["id"]
    part = doc.state.parts[f"{fid}/1"]
    assert part.reference is True and part.name == cap["neighbours"][0]["name"]
    want = bb(K.located(pin_world, np.linalg.inv(plate_T)))
    assert np.allclose(bb(part.shape), want, atol=1e-6)
    assert not doc.state.parts[f"{doc.features[1]['id']}/1"].reference


def test_save_reopen_same_hash_and_source_not_rebuilt(tmp_path, monkeypatch):
    cap, _, _ = capture()
    source = block_doc(10, 10, 10)                 # the neighbour's source studio
    doc, _ = studio_with_context(cap)
    fid = doc.features[-1]["id"]
    before = state_fingerprint(doc)
    path = str(tmp_path / "ctx.json")
    doc.save(path)
    builds = []
    orig = Document.compute
    monkeypatch.setattr(Document, "compute", lambda self, *a, **k: (builds.append(self), orig(self, *a, **k))[1])
    del doc
    again = Document.load(path)
    assert not again.errors, again.errors
    assert state_fingerprint(again)[f"{fid}/1"] == before[f"{fid}/1"]
    assert source not in builds and all(b is again for b in builds)


def test_every_element_has_a_ctx_name():
    for named in (True, False):
        cap, _, _ = capture(named)
        doc, _ = studio_with_context(cap)
        fid = doc.features[-1]["id"]
        nm = doc.state.named(f"{fid}/1")
        assert nm is not None and nm.unnamed() == []
        for key in nm.keys():
            name = nm.names[key]
            assert f";{N.OP_CONTEXT}" in name and f"{N.TAG}{fid}" in name, name
            assert N.split_last(name)[1] == f";{N.OP_CONTEXT}" and N.split_last(name)[2] == fid
        assert len(set(nm.names.values())) == len(nm.names)


def test_missing_capture_blob_is_red_and_rest_builds(tmp_path):
    cap, _, _ = capture()
    doc, key = studio_with_context(cap)
    add(doc, "variable", name="w", expr="3")
    path = str(tmp_path / "ctx.json")
    doc.save(path)
    os.remove(tmp_path / "blobs" / key)
    again = Document.load(path)
    cid = next(f["id"] for f in again.features if f["type"] == "context")
    assert cid in again.errors and "capture" in again.errors[cid]
    assert len(again.errors) == 1
    assert again.state.vars["w"] == 3 and f"{again.features[1]['id']}/1" in again.state.parts


def test_capture_is_small():
    cap, _, _ = capture()
    doc, key = studio_with_context(cap)
    total = sum(len(b) for b in doc._blob_bytes.values())
    assert 0 < total < 5 * 2 ** 20


def test_not_a_link_and_no_part_studio_change():
    from freeshape import links          # links.SOURCE_FEATURES does not exist yet (S1); links names no context
    assert "context" not in open(links.__file__).read()
    plain = block_doc()
    withctx, _ = studio_with_context(capture()[0])
    first = f"{plain.features[1]['id']}/1"
    assert state_fingerprint(plain)[first] == state_fingerprint(withctx)[first]
