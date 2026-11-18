"""Repeated buffers and names go once (I2.4): 100 placed copies of one geometry give one BREP buffer, one mesh
buffer set and one name dict on the wire and in pickle, and every part decodes equal."""
import json
import os
import pickle
import struct
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: E402
from OCP.gp import gp_Trsf, gp_Vec  # noqa: E402
from OCP.TopLoc import TopLoc_Location  # noqa: E402

from freeshape.engine import InProcessEngine  # noqa: E402
from freeshape.engine import encode  # noqa: E402
from freeshape.engine.wire import from_wire, to_wire  # noqa: E402

COPIES = 100


def _header(data):
    (hlen,) = struct.unpack_from("<I", data, 4)
    return json.loads(data[8:8 + hlen])


def _slots(node, out):
    """Every (tag, off, len) of a bytes / ndarray node under `node`."""
    if isinstance(node, dict):
        if node.get("$") in ("bytes", "ndarray"):
            out.append((node["$"], node["off"], node["len"]))
        for v in node.values():
            _slots(v, out)
    elif isinstance(node, list):
        for v in node:
            _slots(v, out)
    return out


def _edit_header(data, fn):
    """`data` with its JSON header changed by fn(header)."""
    (hlen,) = struct.unpack_from("<I", data, 4)
    h = json.loads(data[8:8 + hlen])
    fn(h)
    new = json.dumps(h, separators=(",", ":")).encode()
    new += b" " * (-(8 + len(new)) % 8)
    return data[:4] + struct.pack("<I", len(new)) + new + data[8 + hlen:]


def _outputs(copies=COPIES):
    with InProcessEngine(placed=True) as e:
        tab = e.new_tab("studio")
        studio = e._tabs[tab]
        studio.sync()
        st = studio.doc.state
        base = BRepPrimAPI_MakeBox(1.0, 2.0, 3.0).Shape()
        for k in range(copies):
            t = gp_Trsf()
            t.SetTranslation(gp_Vec(10.0 * (k + 1), 0.0, 0.0))
            st.new_part(base.Located(TopLoc_Location(t)), "F1")
        return studio.outputs()


def _same(a, b):
    if isinstance(a, np.ndarray):
        return isinstance(b, np.ndarray) and a.dtype == b.dtype and np.array_equal(a, b)
    if hasattr(a, "__dataclass_fields__"):
        return type(a) is type(b) and all(_same(getattr(a, f), getattr(b, f)) for f in a.__dataclass_fields__)
    if isinstance(a, (list, tuple)):
        return type(a) is type(b) and len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    return a == b


def test_hundred_placed_copies_send_one_brep_buffer_and_one_mesh_buffer_set():
    out = _outputs()
    assert len(out.parts) == COPIES and all(p.base == out.parts[0].base for p in out.parts)
    data = to_wire(out)
    slots = _slots(_header(data)["root"], [])
    assert len({s for s in slots if s[0] == "bytes"}) == 1
    assert len({s for s in slots if s[0] == "ndarray"}) == 5 + len(out.parts[0].mesh.edge_polys)
    back = from_wire(data)
    assert len(back.parts) == COPIES
    for a, b in zip(out.parts, back.parts):
        assert _same(a, b)
    assert len(data) < 0.2 * len(to_wire(_outputs(1))) * COPIES        # far below one set per copy


def test_names_are_one_shared_dict_for_placed_copies_in_pickle_and_wire(monkeypatch):
    monkeypatch.setattr(encode, "element_names", lambda part: {      # a fresh, equal map for every part
        "face": {i: f"Face{i}" for i in range(1, 7)}, "edge": {i: f"Edge{i}" for i in range(1, 13)}, "vertex": {}})
    out = _outputs(10)
    assert len({id(p.names) for p in out.parts}) == 1
    assert out.parts[0].names["face"]
    data = to_wire(out)
    assert b'"$":"ref"' in data[8:8 + struct.unpack_from("<I", data, 4)[0]]
    back = from_wire(data)
    assert len({id(p.names) for p in back.parts}) == 1
    assert len({id(p.mesh) for p in back.parts}) == 1
    assert len(pickle.dumps(out, 4)) < 10 * len(pickle.dumps(out.parts[:1], 4))


def test_element_names_are_what_element_names_gives():
    out = _outputs(5)
    assert all(p.names == encode.element_names(None) for p in out.parts)    # an unnamed part has the empty map
    # the same content as the unshared map: kinds, 1-based indices and string names are untouched
    for p in out.parts:
        assert set(p.names) == {"face", "edge", "vertex"}
        assert all(isinstance(i, int) and i >= 1 and isinstance(n, str) for d in p.names.values() for i, n in d.items())
    assert _same(out.parts[0].names, from_wire(to_wire(out)).parts[0].names)


def test_encoder_names_equal_element_names_and_share_by_content():
    class Named:
        names = {("F", 1): "a", ("F", 2): "b", ("E", 1): "e"}

    box = BRepPrimAPI_MakeBox(1.0, 1.0, 1.0).Shape()

    class P:
        shape = box
        named = Named()
        named.shape = box
    enc = encode.Encoder()
    want = encode.element_names(P)
    assert want["face"] == {1: "a", 2: "b"} and want["edge"] == {1: "e"}
    a, b = enc.names(P), enc.names(P)
    assert a == want and a is b


def test_wire_reuses_a_buffer_for_the_same_bytes_or_array_object():
    blob = b"x" * 100
    arr = np.arange(10, dtype="<f4")
    obj = [blob, blob, arr, arr, bytes(bytearray(blob)), arr.copy()]
    data = to_wire(obj)
    slots = _slots(_header(data)["root"], [])
    assert slots[0] == slots[1] and slots[2] == slots[3]
    assert len({s[1] for s in slots}) == 4          # blob, arr, the equal-but-distinct bytes, the copy
    back = from_wire(data)
    assert back[0] == blob and back[1] == blob and np.array_equal(back[2], arr)
    back[2][0] = 9.0
    assert back[3][0] == 0.0                         # each node has its own array


def test_shared_dict_decodes_to_one_object_and_bad_refs_are_rejected():
    d = {"k": [1, 2]}
    back = from_wire(to_wire({"a": d, "b": d}))
    assert back == {"a": d, "b": d} and back["a"] is back["b"]
    good = to_wire({"a": d, "b": d})
    with pytest.raises(ValueError, match="ref"):
        from_wire(_edit_header(good, lambda h: h["root"]["b"].update(n=7)))
    with pytest.raises(ValueError, match="share"):
        from_wire(_edit_header(good, lambda h: h["root"]["b"].update({"$": "share", "n": 0, "v": 1})))
