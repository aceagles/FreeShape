"""Group (slice S4 gate): grouped instances move as one body. Two grouped blocks keep their relative transform
through a drag solve and through a mate added to one of them, a free group reports 6 DOF, a mate between two
members of one group is an error, and the group survives save/load and ungroup + undo.

    both fs_py scripts/smoke/sm4_group.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402

import mates_common as C  # noqa: E402
from freeshape import assembly as A  # noqa: E402


def rel(a, i, j):
    return np.linalg.inv(np.array(a.instance(i)["T"], float)) @ np.array(a.instance(j)["T"], float)


def scene():
    a, ip, i1, i2 = C.plate_and_two_blocks()
    gid = a.add_group([i1, i2])
    return a, ip, i1, i2, gid


def check_free_group_six_dof():
    a, _ip, i1, i2, _gid = scene()
    a.solve()
    assert a.dof == 6, a.dof
    assert a.instance_dof(i1) == 6 and a.instance_dof(i2) == 6, (a.instance_dof(i1), a.instance_dof(i2))


def check_drag_keeps_relative_transform():
    a, _ip, i1, i2, _gid = scene()
    rel0 = rel(a, i1, i2)
    P = np.array([[1, 0, 0], [0, 1, 0.0]])
    local = np.array([10, 5, 4.0])
    for dragged in (i1, i2):
        for k in range(1, 5):
            T = np.array(a.instance(dragged)["T"], float)
            p = T[:3, :3] @ local + T[:3, 3]
            a.solve(drag={"inst": dragged, "local": local, "target": p + [7.0 * k, -3.0 * k, 0], "P": P}, iters=8)
            assert np.allclose(rel(a, i1, i2), rel0, atol=1e-9), (dragged, k)
    T1 = np.array(a.instance(i1)["T"], float)
    assert np.linalg.norm(T1[:3, 3]) > 5, "drag did not move the group"


def check_mate_keeps_relative_transform():
    a, ip, i1, i2, _gid = scene()
    for iid, dx in ((i1, 500.0), (i2, 560.0)):             # far from the plate: the mate must carry both
        T = np.array(a.instance(iid)["T"], float)
        T[:3, 3] += [dx, 300.0, 80.0]
        a.instance(iid)["T"] = T.tolist()
    rel0 = rel(a, i1, i2)
    m = C.face_mate(a, "planar", ip, i1)
    assert not a.errors, a.errors
    assert m["id"] not in a.errors
    assert np.allclose(rel(a, i1, i2), rel0, atol=1e-9), rel(a, i1, i2) - rel0
    assert abs(C.point_world(a, i1, (0, 0, 0))[2] - 10) < 1e-6, C.point_world(a, i1, (0, 0, 0))
    assert a.dof == 3, a.dof


def check_mate_inside_group_is_error():
    a, _ip, i1, i2, gid = scene()
    ca = a.connector(i1, "face", C.face(a, i1, C.up))
    cb = a.connector(i2, "face", C.face(a, i2, C.down))
    m = C.add_mate(a, "fastened", ca, cb)
    assert m["id"] in a.errors, a.errors
    assert a.groups[0]["name"] in a.errors[m["id"]], a.errors[m["id"]]
    assert gid in {g["id"] for g in a.groups}


def check_save_load_and_ungroup_undo():
    a, _ip, i1, i2, _gid = scene()
    back = C.save_load(a)
    assert back.groups == a.groups and [g["instances"] for g in back.groups] == [[i1, i2]], back.groups
    gid = a.groups[0]["id"]
    a.delete_group(gid)
    assert a.groups == [] and a.group_of(i1) is None
    a.undo()
    assert [g["instances"] for g in a.groups] == [[i1, i2]], a.groups
    assert a.group_of(i1) is not None and a.group_of(i1) == a.group_of(i2)
    assert C.save_load(a).groups == a.groups


def main():
    check_free_group_six_dof()
    check_drag_keeps_relative_transform()
    check_mate_keeps_relative_transform()
    check_mate_inside_group_is_error()
    check_save_load_and_ungroup_undo()
    print(f"SM4 OK: free group 6 DOF, drag and mate keep relative transform, in-group mate error, save/load, ungroup+undo ocp={os.environ.get('OCP', '?')}")


if __name__ == "__main__":
    main()
