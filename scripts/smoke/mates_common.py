"""Shared builders for the Mates smoke gates (sm1_mate_values.py, sm2 ... sm6).

Not a smoke script itself: its name does not start with "s", so scripts/smoke/run_all.sh's s*.py glob skips it.
Each sm script imports it after putting the repo root and tests/ on sys.path. Assemblies are built from OCC
primitives the way tests/test_golden_mates.py does: a fixed plate with a hole, an arm, a block, a pin. No Qt here.
"""
import json
import os
import tempfile

import numpy as np
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

from freeshape import assembly as A
from freeshape import kernel as K
from freeshape.features import Part


def box(x, y, z, at=(0, 0, 0)):
    return BRepPrimAPI_MakeBox(gp_Pnt(*at), x, y, z).Shape()


def cyl(r, h, at=(0, 0, 0)):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*at), gp_Dir(0, 0, 1)), r, h).Shape()


LIB = {
    "plate": K.cut(box(60, 40, 10), cyl(4, 12, (15, 20, -1))),
    "block": box(20, 10, 8),
    "pin": cyl(4, 30),
    "arm": K.cut(box(50, 12, 6), cyl(4, 8, (6, 6, -1))),
}
ARM_TIP = (50.0, 6.0, 3.0)      # a point at the far end of the arm, in the arm's own coordinates


def resolver(src):
    return Part("x", src["part"], LIB[src["part"]], (0.7, 0.7, 0.7), None)


def new_assembly():
    a = A.AssemblyDoc()
    a.resolver = resolver
    return a


def src(name):
    return {"kind": "studio", "uid": "u1", "part": name}


def face(a, iid, pred):
    return next(i for i, f in enumerate(K.faces(a.parts[iid].shape)) if pred(K.face_info(f)))


def edge(a, iid, pred):
    return next(i for i, e in enumerate(K.edges(a.parts[iid].shape)) if pred(K.edge_info(e)))


def up(i):
    return i.get("n") is not None and i["n"][2] > 0.9


def down(i):
    return i.get("n") is not None and i["n"][2] < -0.9


def hole_edge(z):
    return lambda e: abs(e.get("r", 0) - 4) < 1e-6 and abs(e["center"][2] - z) < 1e-6


def face_mate(a, mtype, fixed_iid, free_iid, **kw):
    """Mate the top face of `fixed_iid` to the bottom face of `free_iid`. Returns the mate."""
    ca = a.connector(fixed_iid, "face", face(a, fixed_iid, up))
    cb = a.connector(free_iid, "face", face(a, free_iid, down))
    m = a.make_mate(mtype)
    m.update(a=ca, b=cb, **kw)
    a.add_mate(m)
    return m


def add_body(a, part, T=None):
    """Insert one more instance of LIB[part] (placed beside the others unless T is given). Returns its id."""
    return a.insert(src(part), T=T)


def add_mate(a, mtype, ca, cb, **kw):
    """Add a mate between two connectors (from a.connector). Returns it."""
    m = a.make_mate(mtype)
    m.update(a=ca, b=cb, **kw)
    a.add_mate(m)
    return m


def hinge():
    """A fixed plate with an arm on a revolute mate (turns about the plate's Z). Returns (asm, mate, plate, arm)."""
    a = new_assembly()
    ip, ia = a.insert(src("plate")), a.insert(src("arm"))
    return a, face_mate(a, "revolute", ip, ia), ip, ia


def slider():
    """A fixed plate with a block on a slider mate. Returns (asm, mate, plate, block)."""
    a = new_assembly()
    ip, ib = a.insert(src("plate")), a.insert(src("block"))
    return a, face_mate(a, "slider", ip, ib), ip, ib


def cylindrical():
    """A fixed plate with a block on a cylindrical mate (slides and turns about Z). Returns (asm, mate, plate, block)."""
    a = new_assembly()
    ip, ib = a.insert(src("plate")), a.insert(src("block"))
    return a, face_mate(a, "cylindrical", ip, ib), ip, ib


def pin_in_hole(mtype="revolute", **kw):
    """The pin standing in the plate's hole. Returns (asm, mate, plate, pin)."""
    a = new_assembly()
    ip, ipin = a.insert(src("plate")), a.insert(src("pin"))
    ca = a.connector(ip, "edge", edge(a, ip, hole_edge(0)))
    cb = a.connector(ipin, "edge", edge(a, ipin, hole_edge(0)))
    m = a.make_mate(mtype)
    m.update(a=ca, b=cb, flip=True, **kw)
    a.add_mate(m)
    return a, m, ip, ipin


def plate_and_two_blocks():
    """A fixed plate and two free blocks, nothing mated. Returns (asm, plate, block1, block2)."""
    a = new_assembly()
    return a, a.insert(src("plate")), a.insert(src("block")), a.insert(src("block"))


def point_world(a, iid, p):
    """Where point `p` of instance `iid` (its own coordinates) is in the world."""
    T = np.asarray(a.T(iid), float)
    return T[:3, :3] @ np.asarray(p, float) + T[:3, 3]


def temp_path(name="asm.fasm"):
    return os.path.join(tempfile.mkdtemp(prefix="mates_smoke_"), name)


def save_load(a, name="asm.fasm"):
    """Save to a temp dir and load back. Returns the reloaded assembly."""
    p = temp_path(name)
    a.save(p)
    return A.AssemblyDoc.load(p, resolver=resolver)


def write_previous_version(a, strip=("values", "limits"), version=None):
    """Save `a` to a temp file as the format looked before Mate values existed: the JSON is rewritten to
    the version just before the values step (the version A._upgrade_mate_values upgrades from), with
    the given keys removed from every mate. Returns the path."""
    from freeshape import migrations as M
    if version is None:
        version = M._CHAINS["assembly"].index(A._upgrade_mate_values) + 1      # step i upgrades version i+1 -> i+2
    p = temp_path("old.fasm")
    a.save(p)
    with open(p) as fh:
        d = json.load(fh)
    d["version"] = version
    for m in d["mates"]:
        for k in strip:
            m.pop(k, None)
    with open(p, "w") as fh:
        json.dump(d, fh)
    return p


def plus_x(i):
    return i.get("n") is not None and i["n"][0] > 0.9


def mated_blocks(*types):
    """A fixed plate with one block per mate type, each on its own mate (top face, then +X face, alternating),
    every mate at value zero. Returns (asm, [mates])."""
    a = new_assembly()
    ip = a.insert(src("plate"))
    mates = []
    for k, t in enumerate(types):
        ib = a.insert(src("block"))
        ca = a.connector(ip, "face", face(a, ip, up if k % 2 == 0 else plus_x))
        cb = a.connector(ib, "face", face(a, ib, down))
        m = add_mate(a, t, ca, cb)
        for d in A.MATE_VALUE_DOFS[t]:
            assert a.drive_mate(m["id"], d, 0.0)[1]
        mates.append(m)
    return a, mates


def relate(a, rtype, m1, m2, ratio, dof1="rz", dof2="rz", reverse=False):
    """Add a relation between a dof of m1 and a dof of m2 (m2 = m1 for a screw). Returns it."""
    r = a.make_relation(rtype)
    r.update(a={"mate": m1["id"], "dof": dof1}, b={"mate": m2["id"], "dof": dof2}, ratio=ratio, reverse=reverse)
    a.add_relation(r)
    return r
