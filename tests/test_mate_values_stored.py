"""Mate values are stored on each mate, unwrapped after every solve, and filled in for old files (I1.2)."""
import json

import numpy as np
import pytest

from freeshape import assembly as A
from freeshape import migrations as M
from test_golden_mates import _asm, _face_mate, _pin_in_hole


def _turn(a, m, iid, deg):
    FA = a.world_frame(m["a"])
    a.move_instance(iid, A.rot_about(FA["z"], np.radians(deg), FA["o"]), checkpoint=False)


def _wrapped(a, m):
    return A.mate_values(m, a.world_frame(m["a"]), a.world_frame(m["b"]))


def test_make_mate_starts_empty():
    assert _asm().make_mate("revolute")["values"] == {}


def test_add_mate_sets_wrapped_measure():
    a, m, _ip, _ib = _face_mate("planar", offset=2.0)
    assert set(m["values"]) == {"tx", "ty", "rz"}
    for k, v in _wrapped(a, m).items():
        assert m["values"][k] == pytest.approx(v, abs=1e-9)


def test_revolute_turns_accumulate_past_a_full_turn():
    a, m, _ip, ib = _face_mate("revolute")
    start = m["values"]["rz"]
    for _ in range(5):
        _turn(a, m, ib, 100.0)
    assert m["values"]["rz"] - start == pytest.approx(500.0, abs=1e-6)
    assert _wrapped(a, m)["rz"] == pytest.approx(A._wrap_deg(start + 500.0), abs=1e-6)


def test_negative_turns_accumulate():
    a, m, _ip, ib = _face_mate("revolute")
    start = m["values"]["rz"]
    for _ in range(4):
        _turn(a, m, ib, -100.0)
    assert m["values"]["rz"] - start == pytest.approx(-400.0, abs=1e-6)


def test_cylindrical_keeps_translation_and_turns():
    a, m, ipl, ipin = _pin_in_hole("cylindrical")
    start = dict(m["values"])
    for _ in range(4):
        _turn(a, m, ipin, 100.0)
    assert m["values"]["rz"] - start["rz"] == pytest.approx(400.0, abs=1e-6)
    assert m["values"]["tz"] == pytest.approx(start["tz"], abs=1e-6)


def test_fastened_and_ball_have_no_values():
    for t in ("fastened", "ball"):
        assert _face_mate(t)[1]["values"] == {}


def test_save_load_round_trip(tmp_path):
    a, m, _ip, ib = _face_mate("revolute")
    for _ in range(4):
        _turn(a, m, ib, 100.0)
    path = str(tmp_path / "t.fasm")
    a.save(path)
    b = A.AssemblyDoc.load(path)
    assert b.mates[0]["values"] == m["values"]
    assert abs(m["values"]["rz"]) > 180.0


def test_previous_version_file_loads_with_measured_values(tmp_path):
    a, m, _ip, ib = _face_mate("revolute")
    _turn(a, m, ib, 70.0)
    d = json.loads(a.to_json())
    assert d["version"] == M.latest_version("assembly")
    d["version"] = d["version"] - 1
    for mm in d["mates"]:
        mm.pop("values")
    path = str(tmp_path / "old.fasm")
    with open(path, "w") as fh:
        json.dump(d, fh)
    b = A.AssemblyDoc.load(path)
    assert b.mates[0]["values"]["rz"] == pytest.approx(_wrapped(b, b.mates[0])["rz"], abs=1e-9)
    assert b.mates[0]["values"]["rz"] == pytest.approx(m["values"]["rz"], abs=1e-6)


def test_replace_mate_with_changed_flip_resets_values():
    a, m, _ip, ib = _face_mate("revolute")
    for _ in range(4):
        _turn(a, m, ib, 100.0)
    assert abs(m["values"]["rz"]) > 180.0
    m2 = dict(m, flip=True)
    a.replace_mate(m2)
    got = a.mate(m["id"])
    assert abs(got["values"]["rz"]) <= 180.0
    assert got["values"]["rz"] == pytest.approx(_wrapped(a, got)["rz"], abs=1e-9)


def test_a_single_move_updates_values():
    a, m, _ip, ib = _face_mate("revolute")
    start = m["values"]["rz"]
    _turn(a, m, ib, 90.0)
    assert m["values"]["rz"] - start == pytest.approx(90.0, abs=1e-6)


def _old_file(tmp_path, a, version, groups):
    d = json.loads(a.to_json())
    d["version"] = version
    for mm in d["mates"]:
        mm.pop("values")
    if not groups:
        d.pop("groups", None)
    path = str(tmp_path / f"v{version}.fasm")
    with open(path, "w") as fh:
        json.dump(d, fh)
    return path


@pytest.mark.parametrize("version,groups", [(2, False), (3, True)])
def test_version_2_and_3_files_load_through_the_chain(tmp_path, version, groups):
    a, m, _ip, ib = _face_mate("revolute")
    _turn(a, m, ib, 70.0)
    b = A.AssemblyDoc.load(_old_file(tmp_path, a, version, groups))
    assert b.groups == []
    assert b.mates[0]["values"]["rz"] == pytest.approx(_wrapped(b, b.mates[0])["rz"], abs=1e-9)
    assert b.mates[0]["values"]["rz"] == pytest.approx(m["values"]["rz"], abs=1e-6)


def test_values_measured_with_a_grouped_instance():
    a, m, ip, ib = _face_mate("revolute")
    third = a.insert({"kind": "studio", "uid": "u1", "part": "pin"})
    a.add_group([ib, third])
    start = m["values"]["rz"]
    for _ in range(4):
        _turn(a, m, ib, 100.0)
    assert m["values"]["rz"] - start == pytest.approx(400.0, abs=1e-6)
    assert a.group_of(third) is not None
