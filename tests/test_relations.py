"""Relations tie the Mate value of one freedom to another with a stored phase (I2.1, ADR 0005)."""
import json

import numpy as np
import pytest

from freeshape import assembly as A
from freeshape import migrations as M
from test_golden_mates import _asm, _down, _face, _residual, _src, _up


def _plus_x(i):
    return i.get("n") is not None and i["n"][0] > 0.9


def _two_sliders():
    """A fixed plate with block 1 sliding along its top face normal and block 2 along its +X face normal."""
    a = _asm()
    ip, ib1, ib2 = a.insert(_src("plate")), a.insert(_src("block")), a.insert(_src("block"))
    m1 = a.make_mate("slider")
    m1.update(a=a.connector(ip, "face", _face(a, ip, _up)), b=a.connector(ib1, "face", _face(a, ib1, _down)))
    a.add_mate(m1)
    m2 = a.make_mate("slider")
    m2.update(a=a.connector(ip, "face", _face(a, ip, _plus_x)), b=a.connector(ib2, "face", _face(a, ib2, _down)))
    a.add_mate(m2)
    return a, m1, m2, ip, ib1, ib2


def _linear(a, m1, m2, ratio=2.0, reverse=False):
    r = a.make_relation("linear")
    r.update(a={"mate": m1["id"], "dof": "tz"}, b={"mate": m2["id"], "dof": "tz"}, ratio=ratio, reverse=reverse)
    a.add_relation(r)
    return r


def _measured(a, m):
    return A.mate_values(m, a.world_frame(m["a"]), a.world_frame(m["b"]))["tz"]


def _poses(a):
    return {i["id"]: [list(row) for row in i["T"]] for i in a.instances}


def _at(a, m1, m2, v1, v2):
    assert a.drive_mate(m1["id"], "tz", v1)[1] and a.drive_mate(m2["id"], "tz", v2)[1]


@pytest.mark.parametrize("reverse,want", [(False, 20.0), (True, -20.0)])
def test_linear_ratio_two(reverse, want):
    a, m1, m2, *_ = _two_sliders()
    _at(a, m1, m2, 0.0, 0.0)
    r = _linear(a, m1, m2, 2.0, reverse)
    assert r["phase"] == pytest.approx(0.0, abs=1e-9)
    reached, ok = a.drive_mate(m1["id"], "tz", 10.0)
    assert ok and reached == pytest.approx(10.0, abs=1e-6)
    assert m2["values"]["tz"] == pytest.approx(want, abs=1e-6)
    assert _measured(a, m2) == pytest.approx(want, abs=1e-6)
    assert _residual(a, m1) < 1e-6 and _residual(a, m2) < 1e-6


def test_adding_captures_phase_and_moves_nothing():
    a, m1, m2, *_ = _two_sliders()
    _at(a, m1, m2, 5.0, 3.0)
    before = _poses(a)
    r = _linear(a, m1, m2, 2.0)
    assert _poses(a) == before
    assert r["phase"] == pytest.approx(-7.0, abs=1e-6)
    assert a.drive_mate(m1["id"], "tz", 10.0)[1]
    assert m2["values"]["tz"] == pytest.approx(13.0, abs=1e-6)
    assert _measured(a, m2) == pytest.approx(13.0, abs=1e-6)


def test_replace_and_unsuppress_recapture_phase_and_move_nothing():
    a, m1, m2, *_ = _two_sliders()
    _at(a, m1, m2, 5.0, 3.0)
    r = _linear(a, m1, m2, 2.0)
    a.set_relation_suppressed(r["id"], True)
    assert a.drive_mate(m1["id"], "tz", 8.0)[1]
    assert m2["values"]["tz"] == pytest.approx(3.0, abs=1e-6)      # suppressed: no coupling
    before = _poses(a)
    a.set_relation_suppressed(r["id"], False)
    assert _poses(a) == before and r["phase"] == pytest.approx(3.0 - 16.0, abs=1e-6)
    r2 = dict(r, ratio=0.5)
    a.replace_relation(r2)
    assert _poses(a) == before and a.relation(r["id"])["phase"] == pytest.approx(3.0 - 4.0, abs=1e-6)
    assert a.drive_mate(m1["id"], "tz", 12.0)[1]
    assert m2["values"]["tz"] == pytest.approx(5.0, abs=1e-6)


def test_drag_second_slider_moves_first():
    a, m1, m2, _ip, ib1, ib2 = _two_sliders()
    _at(a, m1, m2, 5.0, 3.0)
    _linear(a, m1, m2, 2.0)
    T1 = np.array(a.instance(ib1)["T"], float)
    T2 = np.array(a.instance(ib2)["T"], float)
    lp = np.array([10.0, 5.0, 4.0])
    tp = T2[:3, :3] @ lp + T2[:3, 3] + 6.0 * a.world_frame(m2["a"])["z"]
    a.solve(drag={"inst": ib2, "points": [(lp, tp)]})
    assert m2["values"]["tz"] == pytest.approx(9.0, abs=1e-4)
    assert m1["values"]["tz"] == pytest.approx(8.0, abs=1e-4)
    assert not np.allclose(a.instance(ib1)["T"], T1, atol=1e-3)
    assert _measured(a, m2) - (2.0 * _measured(a, m1) - 7.0) == pytest.approx(0.0, abs=1e-6)


def test_conflict_loop_runs_with_a_relation():
    a, m1, m2, ip, ib1, _ib2 = _two_sliders()
    _at(a, m1, m2, 5.0, 3.0)
    r = _linear(a, m1, m2, 2.0)
    m3 = a.make_mate("slider")      # block 1's bottom can't face both the plate's top and its +X side
    m3.update(a=a.connector(ip, "face", _face(a, ip, _plus_x)), b=a.connector(ib1, "face", _face(a, ib1, _down)))
    a.add_mate(m3)
    assert m3["id"] in a._conflicts and a.relations == [r]
    # subsets that hold only one of the relation's mates leave the relation out
    assert a._relation_residuals([m1], {i["id"]: np.array(i["T"]) for i in a.instances}, {}, 1.0).size == 0
    a.solve()                       # runs the one-at-a-time loop again with the relation in place
    assert m3["id"] in a._conflicts and m1["id"] not in a._conflicts and m2["id"] not in a._conflicts
    assert m2["values"]["tz"] - (2.0 * m1["values"]["tz"] + r["phase"]) == pytest.approx(0.0, abs=1e-6)


def test_delete_then_undo_restores_relation():
    a, m1, m2, *_ = _two_sliders()
    r = _linear(a, m1, m2, 2.0)
    a.delete_relation(r["id"])
    assert a.relations == []
    a.undo()
    assert [x["id"] for x in a.relations] == [r["id"]]
    assert a.relations[0]["phase"] == pytest.approx(r["phase"])


def test_ids_stay_unique_after_save_and_load(tmp_path):
    a, m1, m2, *_ = _two_sliders()
    r = _linear(a, m1, m2, 2.0)
    path = str(tmp_path / "rel.fasm")
    a.save(path)
    b = A.AssemblyDoc.load(path)
    assert b.relations == a.relations
    used = {x["id"] for x in b.instances + b.mates + b.groups + b.relations}
    r2 = b.make_relation("linear")
    assert r2["id"] not in used and r2["id"] != r["id"]
    r2.update(a={"mate": m2["id"], "dof": "tz"}, b={"mate": m1["id"], "dof": "tz"})
    b.add_relation(r2)
    assert len({x["id"] for x in b.relations}) == 2


@pytest.mark.parametrize("version", [5, 4])
def test_previous_version_file_loads_with_no_relations(tmp_path, version):
    """A version-5 file (mate limits, no relations) and an older one through the whole chain both load
    with an empty relations list, keeping their limits."""
    a, m1, *_ = _two_sliders()
    a.set_limits(m1["id"], "tz", -50.0, 50.0)
    d = json.loads(a.to_json())
    assert d["version"] == M.latest_version("assembly") == 7 and d["relations"] == []
    d["version"] = version
    d.pop("relations")
    path = str(tmp_path / "old.fasm")
    with open(path, "w") as fh:
        json.dump(d, fh)
    b = A.AssemblyDoc.load(path)
    assert b.relations == [] and b.mates[0]["limits"] == {"tz": [-50.0, 50.0]}


def test_empty_relations_solve_exactly_as_without_the_hook(monkeypatch):
    a, m1, m2, *_ = _two_sliders()
    _at(a, m1, m2, 5.0, 3.0)
    with_hook = _poses(a)
    monkeypatch.setattr(A.AssemblyDoc, "_relation_residuals", lambda self, *args: np.zeros(0))
    b, n1, n2, *_ = _two_sliders()
    _at(b, n1, n2, 5.0, 3.0)
    assert _poses(b) == with_hook


def test_deleting_a_mate_leaves_its_relation_inert_and_undo_restores_it():
    a, m1, m2, *_ = _two_sliders()
    _at(a, m1, m2, 5.0, 3.0)
    r = _linear(a, m1, m2, 2.0)
    a.delete_mates([m2["id"]])
    assert [x["id"] for x in a.relations] == [r["id"]] and a.relations[0]["phase"] is None
    assert a.drive_mate(m1["id"], "tz", 10.0)[1]          # the inert relation adds no residual
    a.undo()
    a.undo()
    assert a.mate(m2["id"]) is not None
    assert a.relations[0]["phase"] == pytest.approx(-7.0, abs=1e-6)


def test_relation_with_an_active_limit_hold():
    a, m1, m2, _ip, ib1, _ib2 = _two_sliders()
    _at(a, m1, m2, 5.0, 3.0)
    r = _linear(a, m1, m2, 2.0)
    a.set_limits(m2["id"], "tz", None, 15.0)
    a.move_instance(ib1, A.translation(8.0 * a.world_frame(m1["a"])["z"]))   # wants 13 -> 19 on slider 2
    assert not a._conflicts
    assert m2["values"]["tz"] == pytest.approx(15.0, abs=1e-5)
    assert m1["values"]["tz"] == pytest.approx(11.0, abs=1e-5)
    assert m2["values"]["tz"] - (2.0 * m1["values"]["tz"] + r["phase"]) == pytest.approx(0.0, abs=1e-6)


def test_invalid_relations_are_refused():
    a, m1, m2, *_ = _two_sliders()
    n = len(a._undo)
    bad = [
        dict(type="nope", a={"mate": m1["id"], "dof": "tz"}, b={"mate": m2["id"], "dof": "tz"}),
        dict(a={"mate": "M999", "dof": "tz"}, b={"mate": m2["id"], "dof": "tz"}),
        dict(a={"mate": m1["id"], "dof": "rz"}, b={"mate": m2["id"], "dof": "tz"}),
        dict(a={"mate": m1["id"], "dof": "tz"}, b={"mate": m1["id"], "dof": "tz"}),
        dict(a={"mate": m1["id"], "dof": "tz"}, b={"mate": m2["id"], "dof": "tz"}, ratio=0.0),
    ]
    for kw in bad:
        r = a.make_relation("linear")
        r.update(kw)
        with pytest.raises(ValueError):
            a.add_relation(r)
    assert a.relations == [] and len(a._undo) == n
