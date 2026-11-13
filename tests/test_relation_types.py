"""Gear, rack and pinion and screw relations are table rows with unit conversion, no new solver code (I2.2)."""
import pytest

from freeshape import assembly as A
from test_golden_mates import _asm, _down, _face, _src, _up


def _plus_x(i):
    return i.get("n") is not None and i["n"][0] > 0.9


def _mate(a, mtype, ip, face_pick):
    ib = a.insert(_src("block"))
    m = a.make_mate(mtype)
    m.update(a=a.connector(ip, "face", _face(a, ip, face_pick)), b=a.connector(ib, "face", _face(a, ib, _down)))
    a.add_mate(m)
    return m


def _rel(a, rtype, m1, d1, m2, d2, ratio):
    r = a.make_relation(rtype)
    r.update(a={"mate": m1["id"], "dof": d1}, b={"mate": m2["id"], "dof": d2}, ratio=ratio)
    a.add_relation(r)
    return r


def _setup(t1, t2):
    a = _asm()
    ip = a.insert(_src("plate"))
    m1 = _mate(a, t1, ip, _up)
    m2 = _mate(a, t2, ip, _plus_x)
    for m, d in ((m1, "rz" if t1 == "revolute" else "tz"), (m2, "rz" if t2 == "revolute" else "tz")):
        assert a.drive_mate(m["id"], d, 0.0)[1]
    return a, m1, m2


def test_gear_ratio_half():
    a, m1, m2 = _setup("revolute", "revolute")
    _rel(a, "gear", m1, "rz", m2, "rz", 0.5)
    assert a.drive_mate(m1["id"], "rz", 720.0)[1]
    assert m2["values"]["rz"] == pytest.approx(360.0, abs=1e-6)


def test_rack_and_pinion_40_mm_per_rev():
    a, m1, m2 = _setup("revolute", "slider")
    _rel(a, "rack_pinion", m1, "rz", m2, "tz", 40.0)
    assert a.drive_mate(m1["id"], "rz", 360.0)[1]
    assert m2["values"]["tz"] == pytest.approx(40.0, abs=1e-6)
    assert a.drive_mate(m2["id"], "tz", 20.0)[1]
    assert m1["values"]["rz"] == pytest.approx(180.0, abs=1e-6)


def test_screw_on_one_cylindrical_mate():
    a = _asm()
    ip = a.insert(_src("plate"))
    m = _mate(a, "cylindrical", ip, _up)
    assert a.drive_mate(m["id"], "rz", 0.0)[1] and a.drive_mate(m["id"], "tz", 0.0)[1]
    _rel(a, "screw", m, "rz", m, "tz", 2.0)
    assert a.drive_mate(m["id"], "rz", 1080.0)[1]
    assert m["values"]["tz"] == pytest.approx(6.0, abs=1e-6)


def test_wrong_kinds_raise_value_error():
    a, m1, m2 = _setup("slider", "slider")
    r = a.make_relation("gear")
    r.update(a={"mate": m1["id"], "dof": "tz"}, b={"mate": m2["id"], "dof": "tz"})
    with pytest.raises(ValueError, match="rotation"):
        a.add_relation(r)
    b, rv, rv2 = _setup("revolute", "revolute")
    r = b.make_relation("screw")
    r.update(a={"mate": rv["id"], "dof": "rz"}, b={"mate": rv["id"], "dof": "tz"})
    with pytest.raises(ValueError, match="freedom"):
        b.add_relation(r)
    r.update(b={"mate": rv2["id"], "dof": "rz"})
    with pytest.raises(ValueError):
        b.add_relation(r)
    assert a.relations == [] and b.relations == []


def test_table_rows_carry_what_a_dialog_needs():
    for rtype, row in A.RELATION_TYPES.items():
        assert row["label"] and row["help"] and row["ratio_unit"]
        assert row["a"] in A.DOF_KINDS and row["b"] in A.DOF_KINDS
    assert A.RELATION_TYPES["rack_pinion"]["ratio_unit"] == "mm/rev"
    assert A.RELATION_TYPES["screw"]["same_mate"] and not A.RELATION_TYPES["gear"].get("same_mate")
