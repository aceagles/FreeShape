"""Mate limits in the static solve (I5.1): an active set holds each violated freedom at its nearer bound."""
import json

import numpy as np
import pytest

from freeshape import assembly as A
from freeshape import migrations as M
from test_golden_mates import _face_mate, _pin_in_hole


def _turn(a, m, iid, deg):
    FA = a.world_frame(m["a"])
    a.move_instance(iid, A.rot_about(FA["z"], np.radians(deg), FA["o"]), checkpoint=False)


def _slide(a, m, iid, dist):
    FA = a.world_frame(m["a"])
    a.move_instance(iid, A.translation(np.asarray(FA["z"], float) * dist), checkpoint=False)


def _slider_at(tz):
    a, m, ip, ipin = _pin_in_hole("slider")
    _slide(a, m, ipin, tz - m["values"]["tz"])
    assert m["values"]["tz"] == pytest.approx(tz, abs=1e-6)
    return a, m, ip, ipin


def _measured(a, m):
    return A.mate_values(m, a.world_frame(m["a"]), a.world_frame(m["b"]))


def test_slider_above_max_moves_to_max():
    a, m, _ip, ipin = _slider_at(80.0)
    a.set_limits(m["id"], "tz", 0.0, 50.0)
    assert m["limits"] == {"tz": [0.0, 50.0]}
    assert m["values"]["tz"] == pytest.approx(50.0, abs=1e-6)
    assert _measured(a, m)["tz"] == pytest.approx(50.0, abs=1e-6)
    assert not a.errors and not a._conflicts
    assert a._limit_holds == {}


def test_slider_inside_range_does_not_move():
    a, m, _ip, ipin = _slider_at(30.0)
    a.solve()
    before = [list(map(list, i["T"])) for i in a.instances]
    a.set_limits(m["id"], "tz", 0.0, 50.0)
    assert [list(map(list, i["T"])) for i in a.instances] == before


def test_slider_below_min_moves_to_min():
    a, m, _ip, ipin = _slider_at(-20.0)
    a.set_limits(m["id"], "tz", 0.0, 50.0)
    assert m["values"]["tz"] == pytest.approx(0.0, abs=1e-6)


def test_revolute_past_two_turns_moves_to_720():
    a, m, _ip, ib = _face_mate("revolute")
    start = m["values"]["rz"]
    for _ in range(8):
        _turn(a, m, ib, 100.0)
    assert m["values"]["rz"] == pytest.approx(start + 800.0, abs=1e-6)
    a.set_limits(m["id"], "rz", start, start + 720.0)
    assert m["values"]["rz"] == pytest.approx(start + 720.0, abs=1e-6)
    assert _measured(a, m)["rz"] == pytest.approx(A._wrap_deg(start + 720.0), abs=1e-6)


def test_revolute_at_800_limited_0_to_720():
    a, m, _ip, ib = _face_mate("revolute")
    _turn(a, m, ib, 80.0 - m["values"]["rz"])
    m["values"]["rz"] = 800.0          # as if turned twice more by hand
    a.solve()
    assert m["values"]["rz"] == pytest.approx(800.0, abs=1e-6)
    a.set_limits(m["id"], "rz", 0.0, 720.0)
    assert m["values"]["rz"] == pytest.approx(720.0, abs=1e-6)
    assert _measured(a, m)["rz"] == pytest.approx(0.0, abs=1e-6)


def test_one_sided_min_leaves_value_above_untouched():
    a, m, _ip, ipin = _slider_at(80.0)
    before = [list(map(list, i["T"])) for i in a.instances]
    a.set_limits(m["id"], "tz", 10.0, None)
    assert m["limits"] == {"tz": [10.0, None]}
    assert [list(map(list, i["T"])) for i in a.instances] == before
    assert m["values"]["tz"] == pytest.approx(80.0, abs=1e-6)


def test_one_sided_max_only():
    a, m, _ip, ipin = _slider_at(80.0)
    a.set_limits(m["id"], "tz", None, 60.0)
    assert m["values"]["tz"] == pytest.approx(60.0, abs=1e-6)


def test_limit_holds_after_a_later_move():
    a, m, _ip, ipin = _slider_at(30.0)
    a.set_limits(m["id"], "tz", 0.0, 50.0)
    _slide(a, m, ipin, 40.0)            # tries to go to 70
    assert m["values"]["tz"] == pytest.approx(50.0, abs=1e-6)


def test_cylindrical_limits_two_freedoms():
    a, m, _ip, ipin = _pin_in_hole("cylindrical")
    _slide(a, m, ipin, 30.0 - m["values"]["tz"])
    _turn(a, m, ipin, 90.0 - m["values"]["rz"])
    a.set_limits(m["id"], "tz", 0.0, 20.0)
    a.set_limits(m["id"], "rz", -45.0, 45.0)
    assert m["values"]["tz"] == pytest.approx(20.0, abs=1e-6)
    assert m["values"]["rz"] == pytest.approx(45.0, abs=1e-6)


@pytest.mark.parametrize("mtype", ["ball", "fastened"])
def test_set_limits_on_mate_without_freedoms_raises(mtype):
    a, m, _ip, _ib = _face_mate(mtype)
    with pytest.raises(ValueError):
        a.set_limits(m["id"], "rz", 0.0, 10.0)
    assert "limits" not in m


def test_set_limits_unknown_freedom_raises():
    a, m, _ip, _ib = _face_mate("revolute")
    with pytest.raises(ValueError):
        a.set_limits(m["id"], "tz", 0.0, 10.0)


def test_set_limits_min_above_max_raises():
    a, m, _ip, _ib = _face_mate("revolute")
    with pytest.raises(ValueError):
        a.set_limits(m["id"], "rz", 10.0, 0.0)


def test_clearing_a_limit_and_undo():
    a, m, _ip, ipin = _slider_at(30.0)
    a.set_limits(m["id"], "tz", 0.0, 50.0)
    a.set_limits(m["id"], "tz", None, None)
    assert "limits" not in m
    a.undo()
    assert a.mate(m["id"])["limits"] == {"tz": [0.0, 50.0]}


def test_save_and_load_keep_limits(tmp_path):
    a, m, _ip, ib = _face_mate("revolute")
    a.set_limits(m["id"], "rz", -30.0, None)
    path = str(tmp_path / "t.fasm")
    a.save(path)
    with open(path) as fh:
        assert json.load(fh)["version"] == M.latest_version("assembly")
    b = A.AssemblyDoc.load(path)
    assert b.mates[0]["limits"] == {"rz": [-30.0, None]}


def test_version_4_file_loads_without_limits(tmp_path):
    a, m, _ip, ib = _face_mate("revolute")
    d = json.loads(a.to_json())
    d["version"] = 4
    path = str(tmp_path / "v4.fasm")
    with open(path, "w") as fh:
        json.dump(d, fh)
    b = A.AssemblyDoc.load(path)
    assert "limits" not in b.mates[0]
    assert M.latest_version("assembly") >= 5


def test_no_limits_never_sets_holds(monkeypatch):
    calls = []
    orig = A.AssemblyDoc._hold_residuals
    monkeypatch.setattr(A.AssemblyDoc, "_hold_residuals", lambda *a_: calls.append(1) or orig(*a_))
    a, m, _ip, ib = _face_mate("revolute")
    _turn(a, m, ib, 100.0)
    assert calls == []
    assert a._enforce_limits(a.mates) == {}
