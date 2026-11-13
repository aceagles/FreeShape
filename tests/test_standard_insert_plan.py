"""plan_insert / apply_insert: planning and applying a multi-insert of Standard parts (headless)."""
import os
import sys
import time

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir

from freeshape import kernel as K
from freeshape import assembly as A
from freeshape.features import Part
from freeshape.standard_insert import plan_insert, apply_insert, hole_targets, _default_length, _nominal_diameter

SCREW = "sp:fasteners/iso4762"
TAPPING = "sp:fasteners/iso7049"
NUT = "sp:fasteners/iso4032"
WASHER = "sp:fasteners/iso7089"
T = 5.0                                     # plate thickness: the top face is z = T


def cyl(r, h, at):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*at), gp_Dir(0, 0, 1)), r, h).Shape()


def holes():
    """(x, y, diameter): eight 3.4, four 4.5 and one 1.0 mm hole, in a 200 x 50 plate."""
    out = [(12 + 20 * i, 12, 3.4) for i in range(8)]
    out += [(12 + 20 * i, 38, 4.5) for i in range(4)]
    out.append((172, 38, 1.0))
    return out


def plate():
    b = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 200, 50, T).Shape()
    for x, y, d in holes():
        b = K.cut(b, cyl(d / 2, T + 2, (x, y, -1)))
    return b


def doc():
    a = A.AssemblyDoc()
    a.resolver = lambda src: Part("x", "plate", plate(), (0.7, 0.7, 0.7), None)
    p = a.insert({"kind": "studio", "uid": "u", "part": "plate"})
    return a, p


def top_face(a, iid):
    for i, f in enumerate(K.faces(a.parts[iid].shape)):
        info = K.face_info(f)
        if "n" in info and info["n"][2] > 0.99 and abs(info["c"][2] - T) < 1e-6:
            return i
    raise AssertionError("no top face")


def top_pick(a, iid):
    return {"inst": iid, "kind": "face", "index": top_face(a, iid), "click": None}


def world(a, iid, name):
    return a.world_frame(a.named_connector(iid, name))


def count_solves(a, monkeypatch):
    calls = []
    orig = a.solve

    def solve(*args, **kw):
        calls.append(1)
        return orig(*args, **kw)
    monkeypatch.setattr(a, "solve", solve)
    return calls


@pytest.fixture(scope="module")
def planned():
    a, p = doc()
    return a, p, plan_insert(a, SCREW, [top_pick(a, p)], {})


def test_plan_groups_lengths_and_unmatched(planned):
    a, p, plan = planned
    assert plan["type"] == SCREW
    assert [(g["size"], len(g["targets"])) for g in plan["groups"]] == [("M3", 8), ("M4", 4)]
    assert [t["diameter"] for t in plan["unmatched"]] == pytest.approx([1.0])
    for g, dia in zip(plan["groups"], (3, 4)):
        assert g["lengths"] and g["lengths"] == sorted(g["lengths"])
        assert g["default_length"] in g["lengths"] and g["default_length"] >= 1.5 * dia
        shorter = [x for x in g["lengths"] if x < g["default_length"]]
        assert all(x < 1.5 * dia for x in shorter)             # the shortest long-enough one
    assert plan["groups"][0]["default_length"] == 5 and plan["groups"][1]["default_length"] == 6


def test_plan_without_lengths_and_empty_and_unknown():
    a, p = doc()
    pl = plan_insert(a, WASHER, [top_pick(a, p)], {})
    assert [g["size"] for g in pl["groups"]] == ["M3", "M4"]
    assert all(g["lengths"] == [] and g["default_length"] is None for g in pl["groups"])
    empty = plan_insert(a, SCREW, [], {})
    assert empty["groups"] == [] and empty["unmatched"] == []
    with pytest.raises(KeyError):
        plan_insert(a, "sp:fasteners/nosuch", [top_pick(a, p)], {})


def test_default_length_rule():
    assert _default_length([], 3) is None
    assert _default_length([4, 5, 6, 8], 3) == 5            # 4 < 4.5 <= 5
    assert _default_length([4, 5, 6, 8], 4) == 6            # 6 >= 6 exactly
    assert _default_length([4, 5], 6) == 5                  # none long enough: the longest
    assert _default_length([4, 5], None) == 4

    class P:
        pass
    assert _nominal_diameter(P(), "M3.5") == 3.5 and _nominal_diameter(P(), "x") is None
    P.nominal_diameter = lambda self, s: 9
    assert _nominal_diameter(P(), "M3") == 9


def test_apply_12_screws_seated_mates_one_solve_one_undo(monkeypatch):
    a, p = doc()
    plan = plan_insert(a, SCREW, [top_pick(a, p)], {})
    solves = count_solves(a, monkeypatch)
    t0 = time.perf_counter()
    res = apply_insert(a, plan, {"M3": 10, "M4": 12}, {})
    wall = time.perf_counter() - t0
    print(f"apply_insert of 12 screws: {wall:.2f} s")
    assert len(solves) == 1
    assert len(res["instances"]) == 12 and len(res["mates"]) == 12
    assert [m["id"] for m in a.mates] == res["mates"]
    assert all(m["type"] == "fastened" and not m["flip"] for m in a.mates) and a.errors == {}
    by_target = [(t, g) for g in plan["groups"] for t in g["targets"]]
    for iid, (t, g) in zip(res["instances"], by_target):
        prm = a.instance(iid)["source"]["params"]
        assert prm["size"] == g["size"] and prm["length"] == {"M3": 10, "M4": 12}[g["size"]]
        head, tip = world(a, iid, "under_head"), world(a, iid, "tip")
        assert np.allclose(head["o"], t["centre"], atol=1e-6)                 # on the rim centre
        assert np.allclose(head["z"], [0, 0, -1], atol=1e-6)                  # shank points into the plate
        assert np.allclose(tip["o"][:2], t["centre"][:2], atol=1e-6) and tip["o"][2] < T   # tip in the hole
    ids = [i["id"] for i in a.instances]
    a.undo()
    assert [i["id"] for i in a.instances] == [p] and a.mates == []
    a.redo()
    assert [i["id"] for i in a.instances] == ids and [m["id"] for m in a.mates] == res["mates"]
    head = world(a, res["instances"][0], "under_head")["o"]
    assert np.allclose(head, by_target[0][0]["centre"], atol=1e-6)


def test_default_length_is_used_when_not_chosen():
    a, p = doc()
    plan = plan_insert(a, SCREW, [top_pick(a, p)], {})
    res = apply_insert(a, plan, {}, {})
    lens = {a.instance(i)["source"]["params"]["size"]: a.instance(i)["source"]["params"]["length"]
            for i in res["instances"]}
    assert lens == {g["size"]: g["default_length"] for g in plan["groups"]}


def test_bad_length_changes_nothing():
    a, p = doc()
    depth = len(a._undo)
    plan = plan_insert(a, SCREW, [top_pick(a, p)], {})
    with pytest.raises(K.KernelError):
        apply_insert(a, plan, {"M3": 10, "M4": 999}, {})
    assert [i["id"] for i in a.instances] == [p] and a.mates == [] and len(a._undo) == depth


def test_empty_plan_does_nothing():
    a, p = doc()
    depth = len(a._undo)
    res = apply_insert(a, plan_insert(a, SCREW, [], {}), {}, {})
    assert res == {"instances": [], "mates": []}
    assert [i["id"] for i in a.instances] == [p] and len(a._undo) == depth


@pytest.mark.parametrize("type_id", [NUT, WASHER])
def test_nut_and_washer_seat_by_bottom(type_id):
    a, p = doc()
    plan = plan_insert(a, type_id, [top_pick(a, p)], {})
    res = apply_insert(a, plan, {}, {})
    assert len(res["instances"]) == 12 and a.errors == {}
    by_target = [t for g in plan["groups"] for t in g["targets"]]
    for iid, t in zip(res["instances"], by_target):
        b = world(a, iid, "bottom")
        assert np.allclose(b["o"], t["centre"], atol=1e-6) and np.allclose(b["z"], [0, 0, -1], atol=1e-6)
        assert "length" not in a.instance(iid)["source"]["params"]
    a.undo()
    assert len(a.instances) == 1


def test_washers_then_screws_on_the_washers_inner_rims():
    a, p = doc()
    wplan = plan_insert(a, WASHER, [top_pick(a, p)], {})
    wres = apply_insert(a, wplan, {}, {})
    picks = []
    for w in wres["instances"]:
        fs = K.faces(a.parts[w].shape)
        infos = [K.face_info(f) for f in fs]
        ups = [i for i, fi in enumerate(infos) if "n" in fi]          # planar faces (the sign of n is unreliable)
        top = max(ups, key=lambda i: infos[i]["c"][2])               # the washer's top face
        picks.append({"inst": w, "kind": "face", "index": top, "click": None})
    assert all(len(hole_targets(a, [pk])) == 1 for pk in picks)        # the bore rim, not the outer edge
    plan = plan_insert(a, SCREW, picks, {})
    assert sum(len(g["targets"]) for g in plan["groups"]) == 12 and plan["unmatched"] == []
    res = apply_insert(a, plan, {}, {})
    assert len(res["instances"]) == 12 and a.errors == {}
    seen = []
    for iid, t in zip(res["instances"], [t for g in plan["groups"] for t in g["targets"]]):
        w = t["inst"]
        top_z = K.bbox(a.world_shape(w))[1][2]
        hf = world(a, iid, "under_head")
        head = hf["o"]
        assert np.allclose(hf["z"], [0, 0, -1], atol=1e-6)             # the shank runs down through the washer
        assert head[2] == pytest.approx(top_z, abs=1e-6) and top_z > T + 0.1
        assert np.allclose(head, t["centre"], atol=1e-6)
        lo, hi = K.bbox(a.world_shape(iid))                            # direction: head above the washer, shank down through it
        assert lo[2] < top_z - 1.0 and hi[2] > top_z + 0.5
        seen.append((w, tuple(np.round(head, 6))))
    print("screw under_head origins on washers:", seen[0], seen[-1])
    assert len({s[0] for s in seen}) == 12                              # one screw per washer


def test_base_params_reach_every_instance():
    a, p = doc()
    plan = plan_insert(a, SCREW, [top_pick(a, p)], {"model_thread": True})
    res = apply_insert(a, plan, {}, {"model_thread": True})
    assert all(a.instance(i)["source"]["params"]["model_thread"] is True for i in res["instances"])
    assert a.errors == {}
    b, q = doc()
    tplan = plan_insert(b, TAPPING, [top_pick(b, q)], {"point": "F"})
    tres = apply_insert(b, tplan, {}, {"point": "F"})
    assert tres["instances"] and all(b.instance(i)["source"]["params"]["point"] == "F" for i in tres["instances"])
