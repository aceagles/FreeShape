"""Sketch-owned mate connectors (I1.3): a mate_connector whose ref is sketch geometry or whose owner is a sketch."""
import copy
import hashlib
import json

import numpy as np
import pytest

from conftest import add, try_add
from freeshape import kernel as K
from freeshape import links as L
from freeshape import sketchref
from freeshape.document import Document
from freeshape.engine.encode import connector_out
from test_sketch_instance import FRONT, make_studio, part_src, sk_src


def sketch_ref(doc, sid, eid="l1", role="mid"):
    res = doc.state.sketches[sid]
    sk = res.sketch.to_dict()
    d = sketchref.describe(sk, eid, role)
    return {"sketch": sid, "entity": eid, "role": role, "etype": d["etype"], "sig": d["sig"]}


def first_line(doc, sid):
    ents = doc.state.sketches[sid].sketch.to_dict()["entities"]
    return next(e for e, v in ents.items() if v["type"] == "line")


def expected(doc, sid, eid, role):
    res = doc.state.sketches[sid]
    pl = res.plane
    return sketchref.frame({"origin": pl.origin, "xdir": pl.xdir, "normal": pl.normal}, res.sketch.to_dict(), eid, role)


def test_connector_on_line_midpoint_matches_sketchref_frame_and_flip():
    doc, sid, _ = make_studio()
    eid = first_line(doc, sid)
    ref = sketch_ref(doc, sid, eid)
    f = add(doc, "mate_connector", name="M", ref=ref)
    c = doc.state.connectors[f["id"]]
    ex = expected(doc, sid, eid, "mid")
    assert c["sketch"] == sid and c["part"] is None
    for k in ("o", "z", "x"):
        assert np.allclose(c["frame"][k], ex[k], atol=1e-9)
    g = add(doc, "mate_connector", name="N", ref=ref, flip=True)
    cf = doc.state.connectors[g["id"]]
    assert np.allclose(cf["frame"]["z"], -np.array(ex["z"]), atol=1e-9)
    assert np.allclose(cf["frame"]["x"], ex["x"], atol=1e-9)


def test_origin_connector_owned_by_a_sketch():
    doc, sid, _ = make_studio()
    f = add(doc, "mate_connector", name="O", ref={"origin": True}, owner=sid)
    c = doc.state.connectors[f["id"]]
    assert c["frame"] == {"o": [0.0, 0.0, 0.0], "z": [0.0, 0.0, 1.0], "x": [1.0, 0.0, 0.0]}
    assert c["sketch"] == sid and c["part"] is None
    out = connector_out(f["id"], c)
    assert out.sketch == sid and out.part == ""


def test_resolve_connectors_filters_by_sketch_or_part():
    doc, sid, pid = make_studio(with_part=True)
    sid2 = next(f["id"] for f in doc.features if f["type"] == "sketch" and f["id"] != sid)
    a = add(doc, "mate_connector", name="A", ref=sketch_ref(doc, sid, first_line(doc, sid)))
    b = add(doc, "mate_connector", name="B", ref={"origin": True}, owner=sid2)
    c = add(doc, "mate_connector", name="P", ref={"origin": True}, owner=pid)
    r = L.LinkResolver(find_open_studio=lambda uid, path: doc)
    assert [x["id"] for x in r.resolve_connectors(sk_src(doc, sid))] == [a["id"]]
    assert [x["id"] for x in r.resolve_connectors(sk_src(doc, sid2))] == [b["id"]]
    assert [x["id"] for x in r.resolve_connectors(part_src(doc, pid))] == [c["id"]]
    pkg = r.resolve_studio(sk_src(doc, sid))
    assert pkg.connectors[a["id"]]["sketch"] == sid and pkg.connectors[c["id"]]["sketch"] == ""


def test_moving_the_line_moves_the_frame_and_deleting_it_is_a_named_error():
    doc, sid, _ = make_studio()
    eid = first_line(doc, sid)
    f = add(doc, "mate_connector", name="M", ref=sketch_ref(doc, sid, eid))
    before = doc.state.connectors[f["id"]]
    h0, o0 = connector_out(f["id"], before).hash, list(before["frame"]["o"])
    sf = copy.deepcopy(doc.feature(sid))
    sk = sf["params"]["sketch"]
    p2 = sk["entities"][eid]["p2"]
    sk["points"][p2] = [sk["points"][p2][0] + 20.0, sk["points"][p2][1]]
    doc.replace_feature(sf)
    after = doc.state.connectors[f["id"]]
    assert not np.allclose(after["frame"]["o"], o0)
    assert connector_out(f["id"], after).hash != h0
    sf = copy.deepcopy(doc.feature(sid))
    del sf["params"]["sketch"]["entities"][eid]
    doc.replace_feature(sf)
    assert eid in doc.errors.get(f["id"], "")
    assert f["id"] not in doc.state.connectors


def test_part_owned_connector_hash_is_unchanged():
    doc, sid, pid = make_studio(with_part=True)
    f = add(doc, "mate_connector", name="P", ref={"origin": True}, owner=pid)
    c = doc.state.connectors[f["id"]]
    assert "sketch" not in c
    old_body = {"name": "P", "part": str(pid), "frame": {k: [float(v) for v in c["frame"][k]] for k in ("o", "z", "x")}}
    old = hashlib.sha256(json.dumps(old_body, sort_keys=True).encode()).hexdigest()
    assert connector_out(f["id"], c).hash == old
