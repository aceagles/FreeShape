"""Groups (I4.1): grouped instances are one rigid body in the solver."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from freeshape import assembly as A
from tests.test_assembly import lib, make, src, face, mate  # noqa: F401  (lib is a fixture)


def _top_bottom(a, ip, ib):
    top = a.connector(ip, "face", face(a, ip, lambda f: f.get("n") and f["n"][2] > 0.9))
    bot = a.connector(ib, "face", face(a, ib, lambda f: f.get("n") and f["n"][2] < -0.9))
    return top, bot


def _top_z(a, iid):
    from freeshape import kernel as K
    return K.bbox(a.world_shape(iid))[0][2]


def _rel(a, i, j):
    return np.linalg.inv(np.array(a.instance(i)["T"])) @ np.array(a.instance(j)["T"])


def _scene(lib):
    a = make(lib)
    ip, i1, i2 = a.insert(src("plate")), a.insert(src("block")), a.insert(src("block"))
    top, bot = _top_bottom(a, ip, i1)
    m = mate(a, "planar", top, bot)
    gid = a.add_group([i1, i2])
    return a, ip, i1, i2, gid, m


def test_group_keeps_relative_transform_through_static_and_drag_solves(lib):
    a = make(lib)
    ip, i1, i2 = a.insert(src("plate")), a.insert(src("block")), a.insert(src("block"))
    a.add_group([i1, i2])
    rel0 = _rel(a, i1, i2)
    top, bot = _top_bottom(a, ip, i1)
    m = a.make_mate("planar")                          # appended directly: place_for_mate snaps one instance
    m["a"], m["b"] = top, bot                          # (I4.3 makes it group-aware); the solver is under test here
    a.mates.append(m)
    assert a.solve()                                   # static solve: the whole group comes down with i1
    assert abs(np.array(a.instance(i1)["T"])[2, 3] - np.array(a.instance(i2)["T"])[2, 3]) < 1e-9
    assert abs(_top_z(a, i1) - 10) < 1e-6
    assert not a.errors
    assert np.allclose(_rel(a, i1, i2), rel0, atol=1e-9)
    P = np.array([[1, 0, 0], [0, 1, 0.0]])
    for dragged in (i1, i2):
        for k in range(1, 5):
            T = np.array(a.instance(dragged)["T"])
            local = np.array([10, 5, 4.0])
            p = T[:3, :3] @ local + T[:3, 3]
            a.solve(drag={"inst": dragged, "local": local, "target": p + [7.0 * k, -3.0 * k, 0], "P": P}, iters=8)
            assert np.allclose(_rel(a, i1, i2), rel0, atol=1e-9)


def test_dragged_group_member_moves_the_group(lib):
    a, ip, i1, i2, gid, m = _scene(lib)
    before = np.array(a.instance(i2)["T"])[:3, 3].copy()
    local = np.array([10, 5, 4.0])
    T = np.array(a.instance(i2)["T"])
    p = T[:3, :3] @ local + T[:3, 3]
    a.solve(drag={"inst": i2, "local": local, "target": p + [20.0, 0, 0], "P": np.array([[1, 0, 0], [0, 1, 0.0]])},
            iters=8)
    assert np.linalg.norm(np.array(a.instance(i2)["T"])[:3, 3] - before) > 5


def test_group_with_fixed_member_is_fixed(lib):
    a = make(lib)
    ip, i1 = a.insert(src("plate")), a.insert(src("block"))
    a.add_group([ip, i1])
    before = {i["id"]: list(map(list, i["T"])) for i in a.instances}
    a.solve()
    assert {i["id"]: list(map(list, i["T"])) for i in a.instances} == before


def test_mate_inside_a_group_is_left_out_with_the_group_name(lib):
    a, ip, i1, i2, gid, m = _scene(lib)
    top, bot = _top_bottom(a, i1, i2)
    m2 = mate(a, "fastened", top, bot)
    assert gid in {g["id"] for g in a.groups}
    assert a.groups[0]["name"] in a.errors[m2["id"]]
    assert m["id"] not in a.errors
    assert a.solve()


def test_instance_in_two_groups_raises(lib):
    a, ip, i1, i2, gid, m = _scene(lib)
    with pytest.raises(ValueError):
        a.add_group([i2, ip])
    assert len(a.groups) == 1


def test_delete_group_undo_and_save_load(lib, tmp_path):
    a, ip, i1, i2, gid, m = _scene(lib)
    a.delete_group(gid)
    assert a.groups == []
    a.undo()
    assert [g["instances"] for g in a.groups] == [[i1, i2]]
    p = str(tmp_path / "g.fasm")
    a.save(p)
    b = A.AssemblyDoc.load(p, resolver=a.resolver)
    assert b.groups == a.groups
    assert b.new_id("G") not in {g["id"] for g in b.groups}


def test_deleting_instances_shrinks_and_removes_groups(lib):
    a, ip, i1, i2, gid, m = _scene(lib)
    a.delete_instances([i2])
    assert a.groups == []                               # one member left: the group goes
    a.undo()
    assert len(a.groups) == 1


def test_old_file_loads_with_no_groups(lib, tmp_path):
    a = make(lib)
    a.insert(src("plate"))
    import json
    d = json.loads(a.to_json())
    d.pop("groups")
    d["version"] = 2
    p = tmp_path / "old.fasm"
    p.write_text(json.dumps(d))
    assert A.AssemblyDoc.load(str(p), resolver=a.resolver).groups == []


def test_no_groups_solves_as_before(lib):
    a = make(lib)
    ip, i1 = a.insert(src("plate")), a.insert(src("block"))
    top, bot = _top_bottom(a, ip, i1)
    mate(a, "fastened", top, bot)
    assert np.allclose(np.array(a.instance(i1)["T"])[2, 3], 10, atol=1e-6)
