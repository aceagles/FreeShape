"""A Context captures Sketch instances (I1.5, ADR 0004): a sketch neighbour is stored and restored as a reference
body of edges and vertices; it is never Published output; a Context cannot be taken from a Sketch instance."""
import copy

import numpy as np
import pytest

from conftest import add, block_doc
from freeshape import assembly as A
from freeshape import document as D
from freeshape import kernel as K
from tests.test_sketch_instance import assembly_for, make_studio, part_src, sk_src


def rig():
    """A source studio (one part, one sketch with two lines and a free point), an Assembly with the part and the
    sketch inserted (sketch moved), and a holder studio with a Context captured from the part."""
    doc, sid, pid = make_studio(with_part=True)
    a, _ = assembly_for(doc)
    ip, isk = a.insert(part_src(doc, pid)), a.insert(sk_src(doc, sid))
    a.instance(isk)["T"] = A.translation([5, 6, 7]).tolist()
    a.refresh()
    holder = block_doc()
    cap = A.capture_context(a, ip)
    key = holder.store_context(cap)
    add(holder, "context", assembly=a.uid, instance=ip, capture=key)
    return doc, sid, a, ip, isk, holder, holder.features[-1]["id"], cap


def edge_count(shape):
    return len(K.edges(shape))


def test_capture_lists_the_sketch_as_a_neighbour():
    doc, sid, a, ip, isk, _, _, cap = rig()
    n = next(n for n in cap["neighbours"] if n["id"] == isk)
    assert n["hash"] and n["brep"] and n["names"] == {} and n["name"] == a.instance(isk)["name"]
    assert np.allclose(n["T"], np.linalg.inv(a.T(ip)) @ a.T(isk), atol=1e-9)
    assert not np.allclose(n["T"], np.eye(4))
    assert [x["id"] for x in cap["neighbours"]] == [isk]


def test_context_restores_a_reference_body_with_the_sketch_edges():
    doc, sid, a, ip, isk, holder, cid, cap = rig()
    assert not holder.errors, holder.errors
    part = holder.state.parts[f"{cid}/1"]
    assert part.reference is True
    assert edge_count(part.shape) == edge_count(a.parts[isk].shape) == 2
    assert [p.id for p in holder.visible_parts()] == [f"{holder.features[1]['id']}/1"]   # no extra part
    lo, hi = K.bbox(part.shape)
    want_lo, want_hi = K.bbox(K.located(a.parts[isk].shape, cap["neighbours"][0]["T"]))
    assert np.allclose([lo, hi], [want_lo, want_hi], atol=1e-6)


def test_context_status_follows_the_sketch():
    doc, sid, a, ip, isk, holder, cid, cap = rig()
    assert D.context_status(holder, cid, a) == "current"
    f = copy.deepcopy(doc.feature(sid))
    f["params"]["sketch"]["points"]["p3"] = [50.0, 0.0]
    doc.replace_feature(f)
    a.refresh()
    assert D.context_status(holder, cid, a) == "out of date"
    D.update_context(holder, cid, a)
    assert D.context_status(holder, cid, a) == "current"
    assert not holder.errors, holder.errors


def test_moving_the_sketch_instance_is_out_of_date():
    doc, sid, a, ip, isk, holder, cid, cap = rig()
    a.instance(isk)["T"] = A.translation([9, 6, 7]).tolist()
    a.refresh()
    assert D.context_status(holder, cid, a) == "out of date"


def test_context_from_a_sketch_instance_is_refused():
    doc, sid, a, ip, isk, holder, cid, cap = rig()
    with pytest.raises(K.KernelError, match="belongs to a part"):
        A.capture_context(a, isk)
