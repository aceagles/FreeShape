"""freeshape/engine/wire.py: engine outputs as length-prefixed JSON plus raw buffers, no pickle. Round trips
of real outputs (Part Studio, Assembly, Drawing, Variable Studio) compare type identity as well as value;
malformed payloads are ValueError, unsupported objects TypeError; every field of every contract dataclass
must be a type `to_wire` can carry."""
import ast
import dataclasses
import json
import math
import os
import struct
import types
import typing

import numpy as np
import pytest

from conftest import EXAMPLES_DIR, block_doc, only_part
from freeshape.engine import InProcessEngine, MeshOut, contract, wire
from freeshape.engine.encode import mesh_out
from freeshape.engine.wire import from_wire, mesh_json, to_wire
from freeshape.variable_studio import VariableStudioDoc

HINGE_ASM = os.path.join(EXAMPLES_DIR, "hinge.fasm")
HINGE_STUDIO = os.path.join(EXAMPLES_DIR, "hinge_parts.fshape")


# =========================================================================== exact comparison

def same(a, b, path="out"):
    """Raise AssertionError unless a and b have the same types (exactly) and equal values, all the way down."""
    assert type(a) is type(b), f"{path}: {type(a).__name__} != {type(b).__name__}"
    if isinstance(a, np.ndarray):
        assert a.dtype == b.dtype and a.shape == b.shape, f"{path}: {a.dtype}{a.shape} != {b.dtype}{b.shape}"
        assert a.tobytes() == b.tobytes() or np.array_equal(a, b, equal_nan=a.dtype.kind == "f"), path
    elif dataclasses.is_dataclass(a):
        for f in dataclasses.fields(a):
            same(getattr(a, f.name), getattr(b, f.name), f"{path}.{f.name}")
    elif isinstance(a, dict):
        assert list(a) == list(b), f"{path}: keys {list(a)} != {list(b)}"
        for k in a:
            same(k, next(k2 for k2 in b if k2 == k), f"{path} key")
            same(a[k], b[k], f"{path}[{k!r}]")
    elif isinstance(a, (list, tuple)):
        assert len(a) == len(b), f"{path}: length {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            same(x, y, f"{path}[{i}]")
    elif isinstance(a, (set, frozenset)):
        assert a == b, path
        assert sorted(map(repr, a)) == sorted(map(repr, b)), path
    elif isinstance(a, (float, np.floating)) and a != a:
        assert b != b, path
    else:
        assert a == b, f"{path}: {a!r} != {b!r}"
        if isinstance(a, float):
            assert math.copysign(1, a) == math.copysign(1, b), path


def round_trip(x):
    out = from_wire(to_wire(x))
    same(x, out)
    return out


# =========================================================================== real outputs

@pytest.fixture(scope="module")
def outputs(tmp_path_factory):
    """One of each kind, from the in-process engine: {kind: outputs}."""
    tmp = tmp_path_factory.mktemp("wire")
    v = VariableStudioDoc()
    v.add_row("w", "20 mm")
    v.add_row("h", "w * 2")
    vpath = str(tmp / "V.fvars")
    v.save(vpath)
    with InProcessEngine() as e:
        studio = e.open_tab(HINGE_STUDIO)
        asm = e.open_tab(HINGE_ASM)
        var = e.open_tab(vpath)
        dwg = e.new_tab("drawing")
        e.submit_edit(dwg, {"op": "set_source", "source": {"kind": "studio", "path": HINGE_STUDIO, "part": "F2/1"}})
        e.submit_edit(dwg, {"op": "auto_layout"})
        yield {"studio": e.outputs(studio), "assembly": e.outputs(asm), "variables": e.outputs(var),
               "drawing": e.outputs(dwg)}


@pytest.mark.parametrize("kind", ["studio", "assembly", "drawing", "variables"])
def test_real_outputs_round_trip_exactly(outputs, kind):
    out = outputs[kind]
    back = round_trip(out)
    assert type(back) is type(out)


def test_the_outputs_are_not_trivial(outputs):
    st, asm = outputs["studio"], outputs["assembly"]
    assert st.parts and st.parts[0].mesh is not None and isinstance(st.parts[0].brep, bytes)
    assert asm.mates and asm.instances and asm.parts
    assert all(type(k) is str for k in asm.parts)
    assert type(asm.consumes) is tuple
    assert outputs["variables"].variables


def test_integer_dict_keys_and_tuples_survive_in_a_real_part(outputs):
    part = outputs["studio"].parts[0]
    assert part.names and all(type(i) is int for kind in part.names.values() for i in kind)
    back = round_trip(part)
    assert any(back.names[k] for k in back.names)
    assert all(type(i) is int for kind in back.names.values() for i in kind)
    assert type(back.color) is tuple and type(back.mesh.face_ranges[0]) is tuple


def test_payload_is_not_a_pickle_and_wire_does_not_import_pickle(outputs):
    data = to_wire(outputs["studio"])
    assert data[:4] == b"FSW1" and data[0] != 0x80
    assert b"\x80\x04" not in data[:8] and b"pickle" not in data[: 8 + struct.unpack_from("<I", data, 4)[0]]
    with open(wire.__file__) as fh:
        tree = ast.parse(fh.read())
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not imported & {"pickle", "cPickle", "_pickle", "marshal", "shelve", "importlib"}


def test_layout_is_documented_and_aligned(outputs):
    data = to_wire(outputs["studio"])
    (hlen,) = struct.unpack_from("<I", data, 4)
    start = 8 + hlen
    assert start % 8 == 0
    header = json.loads(data[8:start])
    assert header["version"] == 1 and len(data) == start + header["buffers_size"]

    def offsets(n):
        if isinstance(n, dict):
            if "off" in n:
                yield n["off"]
            for v in n.values():
                yield from offsets(v)
        elif isinstance(n, list):
            for v in n:
                yield from offsets(v)
    assert all(o % 8 == 0 for o in offsets(header["root"]))


# =========================================================================== types

def test_scalars_containers_and_special_floats():
    nan = float("nan")
    value = {
        "none": None, "t": True, "f": False, "i": -7, "big": 2 ** 70, "neg": -(2 ** 53) - 1, "edge": 2 ** 53,
        "pi": math.pi, "negzero": -0.0, "nan": nan, "inf": float("inf"), "ninf": float("-inf"), "s": "é☃\"$",
        "t2": ((1, 2), (), ((3,),)), "list": [1, [2, (3,)]], "set": {1, 2}, "fs": frozenset({(1, 2), 3}),
        "ints": {1: "a", -2: ("b",)}, "tupkeys": {(1, 2): [1.5], (3,): None}, "mixed": {1: 1, "a": 2, True: 3},
        "dollar": {"$": 1, "a": 2}, "empty": {}, "emptyl": [], "bytes": b"\x00\xffabc", "nobytes": b"",
    }
    back = round_trip(value)
    assert math.isnan(back["nan"]) and back["inf"] == float("inf") and back["ninf"] == float("-inf")
    assert type(back["ints"]) is dict and all(type(k) is int for k in back["ints"])


def test_numpy_arrays_keep_dtype_shape_and_values():
    rng = np.random.default_rng(1)
    for name, dt in wire.DTYPES.items():
        a = (rng.random((3, 4)) * 100).astype(dt)
        round_trip(a)
    round_trip(np.zeros((0, 3), np.float32))
    round_trip(np.zeros((0,), np.uint32))
    round_trip(np.array(5.0, np.float64))                         # 0-d
    round_trip(np.array([np.nan, np.inf, -np.inf, -0.0], np.float32))
    round_trip(np.array([True, False]))
    round_trip(np.arange(24, dtype=np.int32).reshape(2, 3, 4))
    f = np.asfortranarray(np.arange(12, dtype=np.float32).reshape(3, 4))
    back = round_trip(f)
    assert back.flags.c_contiguous and back.flags.writeable
    round_trip(np.arange(20.0)[::3])                              # strided view
    round_trip(np.arange(12.0).reshape(3, 4).T)


def test_big_endian_arrays_are_sent_little_endian():
    a = np.arange(6, dtype=">f4").reshape(2, 3)
    back = from_wire(to_wire(a))
    assert back.dtype == np.dtype("<f4") and np.array_equal(back, a)


def test_numpy_scalars_come_back_as_numpy_scalars():
    for v in (np.float32(1.5), np.float64(2.5), np.int32(-4), np.uint8(200), np.bool_(True), np.int64(2 ** 60),
              np.uint64(2 ** 64 - 1), np.float32("nan"), np.float64("-inf")):
        round_trip(v)


def test_dataclasses_with_defaults_and_none():
    c = contract
    mesh = MeshOut(verts=np.zeros((0, 3), np.float32), normals=np.zeros((0, 3), np.float32),
                   tris=np.zeros((0, 3), np.uint32), tri_face=np.zeros(0, np.int32), edge_polys=[],
                   vertices=np.zeros((0, 3), np.float32))
    part = c.PartOut(id="p", name="n", color=(0.5, 0.5, 0.5), hidden=False, brep=b"x", hash="h", names={},
                     mesh=None, face_colors={1: (1.0, 0.0, 0.0), 3: (0.0, 1.0, 0.0)})
    round_trip(part)
    round_trip(dataclasses.replace(part, mesh=mesh, face_colors=None))
    round_trip(c.MateConnectorOut(id="C1", name="Origin", part="p", frame={"o": [0, 0, 0], "z": [0, 0, 1],
                                                                             "x": [1, 0, 0]}, hash="a"))
    round_trip(c.SketchOut(id="s", name="S", plane={"origin": [0, 0, 0]}, sketch={"entities": []}, regions=[
        {"c": [0.0, 1.0], "area": float("nan"), "loops": [[1, 2]], "edges": [3]}]))
    round_trip(c.VariablesOutputs(tab="v", kind="variables", name="V", revision=3, errors={"0": "bad"},
                                  consumes=(), variables={"a": float("inf")}, rows=[{"name": "a", "expression": "1/0"}]))
    conn = c.ConnectorOut(instance="I1", kind="face", index=None, frame={}, world={})
    round_trip(c.MateOut(id="M", name="m", type="fastened", suppressed=False, a=conn, b=conn, params={"flip": True}))
    round_trip(c.InstanceOut(id="I", name="i", part=None, T=[[1.0] * 4] * 4, hidden=False, fixed=True,
                             color=(1, 2, 3), source={}))
    round_trip(c.DrawingOutputs(tab="d", kind="drawing", name="D", revision=0, errors={}, consumes=("a", "b")))


# =========================================================================== errors

def test_unsupported_objects_raise_type_error_naming_the_type():
    class Foo:
        pass

    import enum

    class Color(enum.IntEnum):
        RED = 1

    class MyInt(int):
        pass

    for obj, name in ((Foo(), "Foo"), (object(), "object"), (1 + 2j, "complex"), (Color.RED, "Color"),
                      (MyInt(3), "MyInt"), (bytearray(b"ab"), "bytearray"), (memoryview(b"ab"), "memoryview"),
                      (np.array(["a"]), "dtype"), (np.array([1], dtype=object), "dtype"),
                      (np.complex64(1), "complex64"), (np.matrix([[1]]), "matrix"), (lambda: 1, "function"),
                      ({"k": Foo()}, "Foo"), ([Foo()], "Foo"), (slice(1), "slice"), (range(3), "range")):
        with pytest.raises(TypeError) as ei:
            to_wire(obj)
        assert name in str(ei.value), (obj, str(ei.value))


def test_a_dataclass_outside_the_contract_is_a_type_error():
    @dataclasses.dataclass
    class Mine:
        x: int = 1

    with pytest.raises(TypeError, match="Mine"):
        to_wire(Mine())


def test_a_cycle_is_a_value_error_not_a_crash():
    a = []
    a.append(a)
    with pytest.raises(ValueError, match="deeper"):
        to_wire(a)


def _parts(data):
    (hlen,) = struct.unpack_from("<I", data, 4)
    return json.loads(data[8:8 + hlen]), 8 + hlen


def _rebuild(header, data, start):
    """Re-pack `header` (a dict) in front of the buffers of `data`, with a correct prefix."""
    raw = json.dumps(header).encode()
    raw += b" " * (-(8 + len(raw)) % 8)
    return b"FSW1" + struct.pack("<I", len(raw)) + raw + data[start:]


def _forge(root, buffers=b"", declared=None):
    header = {"version": 1, "buffers_size": len(buffers) if declared is None else declared, "root": root}
    return _rebuild(header, b"\0" * 8 + buffers, 8)


ARR = {"$": "ndarray", "dtype": "<f4", "shape": [2], "off": 0, "len": 8}


def test_forged_payloads_are_value_errors():
    good = to_wire({"a": np.arange(4, dtype=np.float32), "b": b"hello"})
    assert from_wire(good)["b"] == b"hello"
    hdr, start = _parts(good)
    cases = {
        "truncated tail": good[:-1], "truncated header": good[:12], "empty": b"", "short": b"FSW",
        "bad magic": b"XXXX" + good[4:], "header length past end": good[:4] + struct.pack("<I", 10 ** 6) + good[8:],
        "extra bytes": good + b"\0", "not bytes": "FSW1",
        "not utf8": b"FSW1" + struct.pack("<I", 8) + b"\xff\xfe\xfd\xfc\xfb\xfa\xf9\xf8",
        "not json": b"FSW1" + struct.pack("<I", 8) + b"{not jsn",
        "no root": _rebuild({"version": 1, "buffers_size": 0}, b"", 0),
        "bad version": _rebuild({"version": 2, "buffers_size": 0, "root": None}, b"", 0),
        "header not object": _rebuild([1], b"", 0),
        "slice past end": _forge({"$": "bytes", "off": 4, "len": 100}, b"abcdefgh"),
        "slice off past end": _forge({"$": "bytes", "off": 1000, "len": 0}, b"abcdefgh"),
        "negative offset": _forge({"$": "bytes", "off": -1, "len": 2}, b"abcdefgh"),
        "negative length": _forge({"$": "bytes", "off": 4, "len": -1}, b"abcdefgh"),
        "float offset": _forge({"$": "bytes", "off": 0.0, "len": 2}, b"abcdefgh"),
        "bool offset": _forge({"$": "bytes", "off": True, "len": 2}, b"abcdefgh"),
        "missing length": _forge({"$": "bytes", "off": 0}, b"abcdefgh"),
        "array past end": _forge(dict(ARR, len=8, off=4), b"\0" * 8),
        "shape mismatch": _forge(dict(ARR, shape=[3]), b"\0" * 8),
        "shape negative": _forge(dict(ARR, shape=[-2]), b"\0" * 8),
        "shape huge": _forge(dict(ARR, shape=[2 ** 62, 2 ** 62]), b"\0" * 8),
        "shape too many dims": _forge(dict(ARR, shape=[1] * 20, len=4), b"\0" * 8),
        "shape not list": _forge(dict(ARR, shape=2), b"\0" * 8),
        "object dtype": _forge(dict(ARR, dtype="|O"), b"\0" * 8),
        "str dtype": _forge(dict(ARR, dtype="<U4"), b"\0" * 8),
        "big-endian dtype": _forge(dict(ARR, dtype=">f4"), b"\0" * 8),
        "dtype missing": _forge({k: v for k, v in ARR.items() if k != "dtype"}, b"\0" * 8),
        "unknown tag": _forge({"$": "pickle", "v": "x"}),
        "tag not a string": _forge({"$": 5}),
        "unregistered dataclass": _forge({"$": "dc", "t": "Foo", "f": {}}),
        "dotted class": _forge({"$": "dc", "t": "os.system", "f": {}}),
        "dunder class": _forge({"$": "dc", "t": "__import__", "f": {}}),
        "dataclass extra field": _forge({"$": "dc", "t": "MateOut", "f": {"zzz": 1}}),
        "dataclass missing field": _forge({"$": "dc", "t": "MateOut", "f": {"id": "x"}}),
        "dataclass fields not dict": _forge({"$": "dc", "t": "MateOut", "f": []}),
        "tuple not list": _forge({"$": "tuple", "v": 3}),
        "unhashable key": _forge({"$": "dict", "items": [[[1], 2]]}),
        "unhashable set member": _forge({"$": "set", "v": [[1]]}),
        "bad dict items": _forge({"$": "dict", "items": [[1]]}),
        "bad float tag": _forge({"$": "float", "v": "oops"}),
        "bad int tag": _forge({"$": "int", "v": "12x"}),
        "int tag not str": _forge({"$": "int", "v": 12}),
        "bare NaN": _forge(None).replace(b"null", b"NaN"),
        "bare Infinity": _forge(None).replace(b"null", b"Infinity"),
        "overflowing number": _forge(None).replace(b"null", b"1e999"),
        "scalar dtype bad": _forge({"$": "npscalar", "dtype": "|O", "v": 1}),
        "scalar bad value": _forge({"$": "npscalar", "dtype": "|u1", "v": 3000}),
        "deep nesting": _forge(json.loads("[" * (wire.MAX_DEPTH + 5) + "]" * (wire.MAX_DEPTH + 5))),
        "very deep json": _forge(None).replace(b"null", b"[" * 100000 + b"]" * 100000),
    }
    for label, payload in cases.items():
        with pytest.raises(ValueError):
            from_wire(payload)
            pytest.fail(f"{label}: no error")
    # a header rewritten to claim more buffer bytes than the payload holds is truncation
    bad = _rebuild(dict(hdr, buffers_size=hdr["buffers_size"] + 8), good, start)
    with pytest.raises(ValueError, match="truncated"):
        from_wire(bad)


def test_error_messages_name_the_problem():
    for payload, text in ((_forge({"$": "pickle"}), "unknown tag"),
                          (_forge({"$": "dc", "t": "Foo", "f": {}}), "registry"),
                          (_forge({"$": "bytes", "off": 4, "len": 100}, b"abcdefgh"), "outside"),
                          (to_wire(b"abc")[:-3], "truncated"),
                          (_forge(dict(ARR, dtype="|O"), b"\0" * 8), "dtype"),
                          (_forge(dict(ARR, shape=[3]), b"\0" * 8), "shape")):
        with pytest.raises(ValueError, match=text):
            from_wire(payload)


def test_from_wire_accepts_bytes_like_input():
    data = to_wire({"a": np.arange(3)})
    for wrap in (bytes, bytearray, memoryview):
        assert from_wire(wrap(data))["a"].tolist() == [0, 1, 2]


def test_decoded_arrays_are_writable_and_independent_of_the_payload():
    data = bytearray(to_wire(np.arange(4, dtype=np.int32)))
    a = from_wire(data)
    a[0] = 99
    data[-1] = 7
    assert a.tolist() == [99, 1, 2, 3]


# =========================================================================== the registry and the contract

def _contract_dataclasses():
    return [o for o in vars(contract).values()
            if isinstance(o, type) and dataclasses.is_dataclass(o) and o.__module__ == contract.__name__]


# Python types a field may be declared as; what to_wire carries.
_LEAF = {str, int, float, bool, bytes, type(None), tuple, list, dict, set, frozenset, np.ndarray}


def _carried(tp):
    origin = typing.get_origin(tp)
    if origin in (typing.Union, types.UnionType):
        return all(_carried(a) for a in typing.get_args(tp))
    if origin is not None:                       # tuple[int, ...], list[str], dict[str, int]
        return _carried(origin) and all(_carried(a) for a in typing.get_args(tp) if a is not Ellipsis)
    if tp is typing.Any:
        return True
    return tp in _LEAF or tp in wire.REGISTRY.values()


def test_every_contract_dataclass_is_registered_under_its_own_name():
    found = _contract_dataclasses()
    assert len(found) >= 12
    assert {c.__name__ for c in found} == set(wire.REGISTRY)
    assert all(wire.REGISTRY[c.__name__] is c for c in found)


def test_every_field_of_every_contract_dataclass_is_a_type_to_wire_carries():
    bad = []
    for cls in _contract_dataclasses():
        hints = typing.get_type_hints(cls, vars(contract))
        for f in dataclasses.fields(cls):
            if not _carried(hints[f.name]):
                bad.append(f"{cls.__name__}.{f.name}: {hints[f.name]}")
    assert not bad, "to_wire cannot carry: " + ", ".join(bad)


def _walk(x, path, seen):
    """Every value in an output: record its type, and fail on a dataclass outside the registry."""
    t = type(x)
    seen.add(t)
    if dataclasses.is_dataclass(x):
        assert t in wire.REGISTRY.values(), f"{path}: {t.__name__} is not a contract dataclass"
        for f in dataclasses.fields(x):
            _walk(getattr(x, f.name), f"{path}.{f.name}", seen)
    elif isinstance(x, dict):
        for k, v in x.items():
            _walk(k, f"{path} key", seen)
            _walk(v, f"{path}[{k!r}]", seen)
    elif isinstance(x, (list, tuple, set, frozenset)):
        for i, v in enumerate(x):
            _walk(v, f"{path}[{i}]", seen)


def test_every_value_in_real_outputs_is_a_carried_type(outputs):
    seen = set()
    for kind, out in outputs.items():
        _walk(out, kind, seen)
    carried = _LEAF | set(wire.REGISTRY.values())
    assert seen <= carried, f"observed types to_wire cannot carry: {seen - carried}"
    assert {np.ndarray, bytes, tuple, dict, list, int, float, str, bool, type(None)} <= seen


# =========================================================================== mesh_json

def test_mesh_json_of_a_box():
    m = mesh_out(only_part(block_doc(40, 20, 10)).shape)
    j = mesh_json(m)
    nv, nt = len(m.verts), len(m.tris)
    assert set(j) == {"positions", "normals", "indices", "face_ids", "face_ranges", "edges", "vertices"}
    assert len(j["positions"]) == 3 * nv and len(j["normals"]) == 3 * nv
    assert len(j["indices"]) == 3 * nt and len(j["face_ids"]) == nt
    assert max(j["indices"]) < nv and min(j["indices"]) >= 0
    assert len(j["face_ranges"]) == 6
    covered = [t for a, b in j["face_ranges"] for t in range(a, b)]
    assert sorted(covered) == list(range(nt))                      # every triangle, once
    for i, (a, b) in enumerate(j["face_ranges"], start=1):
        assert all(j["face_ids"][t] == i for t in range(a, b))
    assert len(j["edges"]) == len(m.edge_polys) == 12
    assert all(len(p) % 3 == 0 and len(p) >= 6 for p in j["edges"])
    assert len(j["vertices"]) == 3 * len(m.vertices) == 24
    json.dumps(j, allow_nan=False)                                  # plain JSON, all Python types
    assert all(type(v) is float for v in j["positions"]) and all(type(v) is int for v in j["indices"])
    xs = j["positions"][0::3]
    assert min(xs) == pytest.approx(0) and max(xs) == pytest.approx(40)


def test_mesh_json_survives_the_wire(outputs):
    m = outputs["studio"].parts[0].mesh
    assert mesh_json(from_wire(to_wire(m))) == mesh_json(m)
