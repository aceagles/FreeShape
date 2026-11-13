"""2.0 stage 3 in this fork: exploded views and instance patterns from upstream, on our Standard parts, groups,
suppression and relations. Upstream's own standard parts (`std` sources) and relations were not taken: ours are
covered by tests/test_assembly_standard*.py, tests/test_fasteners_*.py and tests/test_relations*.py. The two
part families only upstream had that are still to port (bearings, heat-set inserts) wait in
freeshape/addons/hardware/shapes.py; their recipes are checked here. Countersunk screws and dowel pins are
Standard parts now: tests/test_fasteners_countersunk_pins.py."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: E402

from freeshape import kernel as K, assembly as A  # noqa: E402
from freeshape import pyfeature  # noqa: E402,F401  (loads the bundled plugins)
from freeshape.addons.hardware import shapes as HW  # noqa: E402
from freeshape.features import Part  # noqa: E402


def T(x, y, z):
    M = np.eye(4)
    M[:3, 3] = (x, y, z)
    return M


# ------------------------------------------------------------------ the two families waiting for their port
def _build(fam, size):
    return HW.build_bearing(size) if fam == "bearing" else HW.build_insert(size)


def test_every_hardware_recipe_builds_one_valid_solid():
    n = 0
    for fam, (_label, sizes, _lens, _col) in HW.FAMILIES.items():
        for size in sizes:
            shp = _build(fam, size)
            assert K.is_valid(shp) and len(K.solids(shp)) == 1, (fam, size)
            n += 1
    assert n == 23


def test_hardware_sizes_and_placement():
    d, D, B = HW.BEARINGS["608"]
    lo, hi = K.bbox(HW.build_bearing("608"))
    assert abs(hi[0] - D / 2) < 1e-5 and abs(lo[2]) < 1e-5 and abs(hi[2] - B) < 1e-5
    assert HW.designation("bearing", "608") == "Bearing 608 (8×22×7)"


# ------------------------------------------------------------------ exploded view
def _bolted_plate():
    plate = K.cut(BRepPrimAPI_MakeBox(60, 40, 8).Shape(), K.cylinder((30, 20, -1), (0, 0, 1), 3.2, 10))
    a = A.AssemblyDoc()
    a.resolver = lambda src: Part("x", "Plate", plate, (0.6, 0.7, 0.8), None)
    p = a.insert({"kind": "studio", "uid": "u", "part": "plate"}, T(0, 0, 0), checkpoint=False)
    s = a.insert_standard_part("sp:fasteners/iso4762", {"size": "M3", "length": 16}, T(30, 20, 8))
    w = a.insert_standard_part("sp:fasteners/iso7089", {"size": "M3"}, T(30, 20, -0.5))
    n = a.insert_standard_part("sp:fasteners/iso4032", {"size": "M3"}, T(30, 20, -2.9))
    for iid, pos in ((s, (30, 20, 8)), (w, (30, 20, -0.5)), (n, (30, 20, -2.9))):
        a.instance(iid)["T"] = T(*pos).tolist()
    assert not a.errors
    return a, p, s, w, n


def test_auto_explode_separates_a_bolted_stack(tmp_path):
    a, p, s, w, n = _bolted_plate()
    assert a._has_head(a.instance(s)) and not a._has_head(a.instance(w)) and not a._has_head(a.instance(p))
    assert a.auto_explode() == 3

    def box(iid):
        return K.bbox(K.located(a.parts[iid].shape, a.display_T(iid, exploded=True)))
    plate_lo, plate_hi = box(p)
    assert box(s)[0][2] > plate_hi[2]                           # the screw comes right out of its hole, upwards
    assert box(w)[1][2] < plate_lo[2]                           # washer below the plate
    assert box(n)[1][2] < box(w)[0][2]                          # nut below the washer
    assert np.allclose(a.T(s)[:3, 3], [30, 20, 8])              # real positions untouched
    half = a.display_T(s, exploded=True, amount=0.5)[2, 3]
    assert abs(half - (8 + a.explode_offsets()[s][2] / 2)) < 1e-9
    a.save(str(tmp_path / "e.fasm"))
    assert len(A.AssemblyDoc.load(str(tmp_path / "e.fasm")).explode["steps"]) == 3
    a.delete_instances([n])
    assert all(n not in st["ids"] for st in a.explode["steps"])


def test_explode_steps_undo_and_are_saved_only_when_used(tmp_path):
    a, p, s, w, n = _bolted_plate()
    a.save(str(tmp_path / "plain.fasm"))
    with open(tmp_path / "plain.fasm") as fh:
        text = fh.read()
    assert '"explode"' not in text and '"patterns"' not in text      # an unused view adds nothing to the file
    a.add_explode_step([s], (0, 0, 1), 25.0)
    assert np.allclose(a.display_T(s, exploded=True)[:3, 3], [30, 20, 33])
    a.undo()
    assert a.explode["steps"] == []
    a.redo()
    assert len(a.explode["steps"]) == 1


# ------------------------------------------------------------------ patterns
def _plate_and_nut():
    lib = {"base": BRepPrimAPI_MakeBox(100, 100, 5).Shape(), "nut": K.cylinder((0, 0, 0), (0, 0, 1), 6, 10)}
    c = A.AssemblyDoc()
    c.resolver = lambda src: Part("x", src["part"], lib[src["part"]], (0.7, 0.7, 0.7), None)
    base = c.insert({"kind": "studio", "uid": "u", "part": "base"}, T(0, 0, 0), checkpoint=False)
    bolt = c.insert({"kind": "studio", "uid": "u", "part": "nut"}, T(30, 0, 5), checkpoint=False)
    return c, base, bolt


def test_instance_patterns_follow_their_seed_and_edit(tmp_path):
    c, _base, bolt = _plate_and_nut()
    pat = c.add_pattern("circular", [bolt], 6, angle=360, axis={"kind": "world", "name": "Z"})
    copies = [i for row in pat["copies"] for i in row]
    assert len(copies) == 5 and all(c.instance(i)["fixed"] for i in copies)
    assert all(abs(np.hypot(c.T(i)[0, 3], c.T(i)[1, 3]) - 30) < 1e-9 for i in copies)
    c.move_instance(bolt, T(10, 0, 0))
    assert all(abs(np.hypot(c.T(i)[0, 3], c.T(i)[1, 3]) - 40) < 1e-9 for i in copies)
    c.set_fixed(copies[0], False)
    assert c.instance(copies[0])["fixed"]                         # a copy stays placed by its pattern
    c.edit_pattern(pat["id"], count=4)
    assert len(c.instances) == 2 + 3
    lp = c.add_pattern("linear", [bolt], 3, spacing=15, axis={"kind": "world", "name": "Y"})
    assert np.allclose(c.T(lp["copies"][1][0])[:3, 3] - c.T(bolt)[:3, 3], [0, 30, 0])
    c.save(str(tmp_path / "p.fasm"))
    d = A.AssemblyDoc.load(str(tmp_path / "p.fasm"))
    assert len(d.patterns) == 2 and len(d.instances) == len(c.instances)
    assert d.new_id("P") not in {p_["id"] for p_ in d.patterns}   # the id counter saw the patterns
    c.delete_instances([bolt])                                    # the seed takes its copies with it
    assert len(c.instances) == 1 and not c.patterns


def test_pattern_copies_with_suppress_groups_undo_and_sketches():
    c, base, bolt = _plate_and_nut()
    pat = c.add_pattern("linear", [bolt], 3, spacing=15, axis={"kind": "world", "name": "Y"})
    copies = [i for row in pat["copies"] for i in row]
    assert c.pattern_copies(bolt) == copies and c.pattern(pat["id"]) is pat
    # a copy is placed by its pattern, so it cannot join a group
    with pytest.raises(ValueError):
        c.add_group([base, copies[0]])
    # the copies follow their seed in and out of suppression
    c.set_instance_suppressed(bolt, True)
    assert all(c.instance(i).get("suppressed") for i in copies)
    assert [i["id"] for i in c.visible_instances()] == [base]
    c.set_instance_suppressed(bolt, False)
    assert not any(c.instance(i).get("suppressed") for i in copies)
    # one undo step takes the pattern and its copies away, redo brings them back with their parts
    c.undo()
    c.undo()
    c.undo()
    assert not c.patterns and len(c.instances) == 2
    c.redo()
    assert len(c.patterns) == 1 and len(c.instances) == 4 and all(i in c.parts for i in copies)
    # a pattern listed with the mates is deleted through delete_mates, copies and all
    c.delete_mates([pat["id"]])
    assert not c.patterns and len(c.instances) == 2
    # a Sketch instance is not a part and cannot be patterned
    sk = c.insert({"kind": "studio", "uid": "u", "part": "nut"}, T(60, 0, 5), checkpoint=False)
    c.instance(sk)["source"] = {"kind": "sketch", "uid": "u", "sketch": "F1"}
    with pytest.raises(K.KernelError):
        c.add_pattern("linear", [sk], 2)
