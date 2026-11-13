"""Merge of Mates Parity (groups, mate values, limits, BOM, export) with Standard part instances: the new
Assembly features accept an instance whose source is {"kind": "standard", ...} and keep their data."""
import json

import numpy as np
import pytest

from freeshape import assembly as A
from freeshape import kernel as K
from freeshape.pyfeature import registry  # noqa: F401  (loads the Plugins)
from test_golden_mates import _asm, _down, _face, _src, _up

TYPE = "sp:fasteners/iso4762"
PARAMS = {"size": "M3", "length": 10}


def _build():
    a = _asm()
    plate = a.insert(_src("plate"))
    s1 = a.insert_standard_part(TYPE, PARAMS)
    s2 = a.insert_standard_part(TYPE, PARAMS)
    ca = a.connector(plate, "face", _face(a, plate, _up))
    cb = a.connector(s1, "face", _face(a, s1, _down))
    m = a.make_mate("revolute")
    m.update(a=ca, b=cb)
    a.add_mate(m)
    return a, plate, s1, s2, m


def test_standard_part_in_group_mate_limits_bom_export(tmp_path):
    a, plate, s1, s2, m = _build()
    assert not a.errors
    assert set(m["values"]) == {"rz"}
    gid = a.add_group([s1, s2])
    a.set_limits(m["id"], "rz", -30.0, 30.0)
    assert a.group_of(s2)["id"] == gid
    assert sum(r["qty"] for r in a.bom()) == 3
    a.interferences()
    out = str(tmp_path / "all.step")
    a.export(out)
    assert (tmp_path / "all.step").stat().st_size > 0

    path = str(tmp_path / "a.fasm")
    a.save(path)
    d = json.load(open(path))
    assert d["groups"][0]["instances"] == [s1, s2]
    assert d["mates"][0]["limits"]["rz"] == [-30.0, 30.0]
    b = A.AssemblyDoc.load(path, resolver=a.resolver)
    assert not b.errors
    assert b.groups == a.groups and b.mates[0]["limits"] == a.mates[0]["limits"]
    assert b.mates[0]["values"] == pytest.approx(a.mates[0]["values"], abs=1e-6)


def test_undo_and_delete_keep_groups_with_standard_parts():
    a, plate, s1, s2, m = _build()
    a.add_group([s1, s2])
    a.undo()
    assert a.groups == []
    a.redo()
    assert len(a.groups) == 1
    a.delete_instances([s2])
    assert a.groups == [] and a.instance(s1) is not None
    a.undo()
    assert len(a.groups) == 1 and a.instance(s2) is not None
    assert not np.isnan(np.array(a.instance(s2)["T"])).any()
