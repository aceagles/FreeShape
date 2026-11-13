"""Mate ends on a Sketch instance (I1.4): sketch geometry by entity id (ADR 0011) and the sketch's own connectors."""
import copy

import numpy as np

from conftest import add
from freeshape import assembly as A
from freeshape import kernel as K
from test_sketch_connectors import sketch_ref
from test_sketch_instance import assembly_for, make_studio, part_src, sk_src


def ents(doc, sid, etype):
    return [e for e, v in doc.feature(sid)["params"]["sketch"]["entities"].items() if v["type"] == etype]


def edit(doc, sid, fn):
    f = copy.deepcopy(doc.feature(sid))
    fn(f["params"]["sketch"])
    doc.replace_feature(f)
    assert not doc.errors.get(sid), doc.errors


def face_of_block(a, ib):
    return a.connector(ib, "face", 0)


def mate(a, kind, ea, eb, **kw):
    m = a.make_mate(kind)
    m["a"], m["b"] = ea, eb
    m.update(kw)
    a.add_mate(m)
    return m


def setup():
    """Sketch instance (fixed) and a block part fastened to the layout point."""
    doc, sid, pid = make_studio(with_part=True)
    a, _ = assembly_for(doc)
    isk = a.insert(sk_src(doc, sid))
    ib = a.insert(part_src(doc, pid))
    pt = ents(doc, sid, "point")[0]
    m = mate(a, "fastened", a.sketch_end(isk, pt, "point"), face_of_block(a, ib))
    return doc, sid, a, isk, ib, pt, m


def pos(a, c):
    return a.world_frame(c)["o"]


def test_block_fastened_to_a_sketch_point_follows_the_point():
    doc, sid, a, isk, ib, pt, m = setup()
    assert not a.errors, a.errors
    assert m["a"]["kind"] == "sketch" and m["a"]["etype"] == "point" and m["a"]["entity"] == pt
    assert np.allclose(pos(a, m["a"]), pos(a, m["b"]), atol=1e-6)
    before = pos(a, m["b"]).copy()
    pid = doc.feature(sid)["params"]["sketch"]["entities"][pt]["p"]
    edit(doc, sid, lambda s: s["points"].__setitem__(pid, [s["points"][pid][0] + 7.0, s["points"][pid][1]]))
    a.refresh()
    assert not a.errors, a.errors
    moved = pos(a, m["b"]) - before
    plane = doc.state.sketches[sid].plane
    assert np.isclose(np.linalg.norm(moved), 7.0, atol=1e-6)
    assert np.allclose(moved, 7.0 * np.asarray(plane.xdir), atol=1e-6)
    assert np.allclose(pos(a, m["a"]), pos(a, m["b"]), atol=1e-6)


def test_revolute_on_a_line_mid_has_one_dof_about_the_plane_normal_and_takes_a_value():
    doc, sid, pid = make_studio(with_part=True)
    a, _ = assembly_for(doc)
    isk = a.insert(sk_src(doc, sid))
    ib = a.insert(part_src(doc, pid))
    ln = ents(doc, sid, "line")[0]
    m = mate(a, "revolute", a.sketch_end(isk, ln, "mid"), face_of_block(a, ib))
    assert not a.errors, a.errors
    assert a.instance_dof(ib) == 1
    n = np.asarray(doc.state.sketches[sid].plane.normal)
    assert np.allclose(np.abs(a.world_frame(m["a"])["z"]), np.abs(n), atol=1e-9)
    assert np.allclose(pos(a, m["a"]), pos(a, m["b"]), atol=1e-6)
    m["offset"] = 4.0                                  # the revolute's value: distance along the axis
    a.replace_mate(m)
    assert not a.errors, a.errors
    d = pos(a, m["b"]) - pos(a, m["a"])
    assert np.isclose(abs(d @ n), 4.0, atol=1e-6) and a.instance_dof(ib) == 1


def test_deleting_the_mated_entity_is_a_named_error_and_restoring_clears_it():
    doc, sid, pid = make_studio(with_part=True)
    a, _ = assembly_for(doc)
    isk = a.insert(sk_src(doc, sid))
    ib = a.insert(part_src(doc, pid))
    l1, l2 = ents(doc, sid, "line")[:2]
    m1 = mate(a, "revolute", a.sketch_end(isk, l1, "mid"), face_of_block(a, ib))
    ib2 = a.insert(part_src(doc, pid))
    m2 = mate(a, "fastened", a.sketch_end(isk, l2, "end1"), face_of_block(a, ib2))
    saved = copy.deepcopy(doc.feature(sid)["params"]["sketch"]["entities"][l1])
    edit(doc, sid, lambda s: s["entities"].pop(l1))
    a.refresh()
    assert l1 in a.errors.get(m1["id"], "") and "no longer exists" in a.errors[m1["id"]]
    assert m1 in a.mates and m2["id"] not in a.errors
    assert np.allclose(pos(a, m2["a"]), pos(a, m2["b"]), atol=1e-6)
    edit(doc, sid, lambda s: s["entities"].__setitem__(l1, saved))
    a.refresh()
    assert m1["id"] not in a.errors and not a.errors, a.errors
    assert np.allclose(pos(a, m1["a"]), pos(a, m1["b"]), atol=1e-6)


def test_retyped_entity_is_a_named_error():
    doc, sid, a, isk, ib, pt, m = setup()
    ln = ents(doc, sid, "line")[0]
    m2 = mate(a, "fastened", a.sketch_end(isk, ln, "end1"), face_of_block(a, a.insert(part_src(doc, a.instance(ib)["source"]["part"]))))
    edit(doc, sid, lambda s: s["entities"].__setitem__(ln, {"type": "point", "p": "p1", "construction": False}))
    a.refresh()
    assert "now a point" in a.errors[m2["id"]] and ln in a.errors[m2["id"]]


def test_block_fastened_to_a_sketch_owned_connector_follows_the_line():
    doc, sid, pid = make_studio(with_part=True)
    ln = ents(doc, sid, "line")[0]
    f = add(doc, "mate_connector", name="Hinge", ref=sketch_ref(doc, sid, ln, "mid"))
    a, _ = assembly_for(doc)
    isk = a.insert(sk_src(doc, sid))
    ib = a.insert(part_src(doc, pid))
    end = a.named_connector(isk, f["id"])
    assert end["named"] == f["id"] and end["inst"] == isk
    m = mate(a, "fastened", end, face_of_block(a, ib))
    assert not a.errors, a.errors
    assert np.allclose(pos(a, m["a"]), pos(a, m["b"]), atol=1e-6)
    before = pos(a, m["b"]).copy()
    p2 = doc.feature(sid)["params"]["sketch"]["entities"][ln]["p2"]
    edit(doc, sid, lambda s: s["points"].__setitem__(p2, [s["points"][p2][0] + 10.0, s["points"][p2][1]]))
    a.refresh()
    assert not a.errors, a.errors
    assert np.isclose(np.linalg.norm(pos(a, m["b"]) - before), 5.0, atol=1e-6)     # the midpoint moved by half
    assert np.allclose(pos(a, m["a"]), pos(a, m["b"]), atol=1e-6)


def test_sketch_origin_connector_pins_a_second_sketch_instance():
    doc, sid, pid = make_studio(with_part=True)
    o = add(doc, "mate_connector", name="Pin", ref={"origin": True}, owner=sid)
    a, _ = assembly_for(doc)
    ip = a.insert(part_src(doc, pid))
    cid = a.add_instance_origin_connector(ip)
    isk = a.insert(sk_src(doc, sid))
    assert a.instance_dof(isk) == 6
    assert any(c["id"] == o["id"] for c in a.published_connectors(isk))
    mate(a, "fastened", a.named_connector(ip, cid), a.named_connector(isk, o["id"]))
    assert not a.errors, a.errors
    assert a.instance_dof(isk) == 0


def test_save_reopen_keeps_both_ends_and_two_sketch_instances_mate():
    doc, sid, pid = make_studio(with_part=True)
    ln = ents(doc, sid, "line")[0]
    f = add(doc, "mate_connector", name="Hinge", ref=sketch_ref(doc, sid, ln, "mid"))
    a, _ = assembly_for(doc)
    i1 = a.insert(sk_src(doc, sid))
    i2 = a.insert(sk_src(doc, sid))
    ib = a.insert(part_src(doc, pid))
    m1 = mate(a, "fastened", a.sketch_end(i1, ln, "end2"), a.sketch_end(i2, ln, "end1"))
    m2 = mate(a, "fastened", a.named_connector(i1, f["id"]), face_of_block(a, ib))
    assert not a.errors, a.errors
    assert a.instance_dof(i2) == 0
    import json, os, tempfile
    a.path = os.path.join(tempfile.mkdtemp(), "x.fasm")
    a.save()
    d = json.load(open(a.path))
    assert d["mates"][0]["a"]["kind"] == "sketch" and d["mates"][1]["a"]["named"] == f["id"]
    b = A.AssemblyDoc.load(a.path, resolver=a.resolver)
    b.refresh()
    assert not b.errors, b.errors
    assert b.mates[0]["a"]["entity"] == ln and b.mates[0]["a"]["role"] == "end2"
    assert b.mates[0]["b"]["kind"] == "sketch" and b.mates[1]["a"]["named"] == f["id"]
    assert np.allclose(b.T(i2), a.T(i2), atol=1e-6) and np.allclose(b.T(ib), a.T(ib), atol=1e-6)


def test_sketch_end_rejects_non_sketch_instances_and_bad_roles():
    doc, sid, pid = make_studio(with_part=True)
    a, _ = assembly_for(doc)
    isk = a.insert(sk_src(doc, sid))
    ip = a.insert(part_src(doc, pid))
    ln = ents(doc, sid, "line")[0]
    for args in ((ip, ln, "mid"), (isk, "nope", "mid"), (isk, ln, "centre")):
        try:
            a.sketch_end(*args)
        except K.KernelError:
            continue
        raise AssertionError(args)


def test_delete_instance_drops_its_sketch_mates():
    doc, sid, a, isk, ib, pt, m = setup()
    a.delete_instances([isk])
    assert a.mates == []
