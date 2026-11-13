"""I2.2: named mate connectors on Standard parts, and set_standard_params."""
import json

import numpy as np
import pytest

from freeshape import assembly as A
from freeshape import kernel as K
from freeshape import pyfeature
from freeshape.features import Part
from freeshape.pyfeature import registry
from test_assembly import box, cyl, edge, face, make, mate, src

SCREW = "sp:fasteners/iso4762"
NUT = "sp:fasteners/iso4032"
T = 5.0                                    # plate thickness; its top surface is z = T
HOLE_R = 1.7                               # a 3.4 mm through hole
CX, CY = 10.0, 8.0


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    user = tmp_path / "user_plugins"
    user.mkdir()
    monkeypatch.setenv("FREESHAPE_PLUGIN_DIR", str(user))
    before = {p.id for p in pyfeature.plugins()}
    problems_before = list(registry._PROBLEMS)
    yield
    registry._PROBLEMS[:] = problems_before
    for p in pyfeature.plugins():
        if p.id not in before:
            pyfeature.unregister_plugin(p.id)


def plate_with_hole():
    return K.cut(box(30, 20, T), cyl(HOLE_R, T + 2, (CX, CY, -1)))


def rim_edge(a, iid):
    """The hole's circular edge on the top surface."""
    return edge(a, iid, lambda e: e.get("center") is not None and abs(e["center"][2] - T) < 1e-6
                and abs(e["center"][0] - CX) < 1e-6)


def fastened(length=10, flip=False):
    """A plate (fixed) and a screw fastened by under_head to the hole's top rim."""
    a = make({"plate": plate_with_hole()})
    p = a.insert(src("plate"))
    s = a.insert_standard_part(SCREW, {"size": "M3", "length": length})
    rim = a.connector(p, "edge", rim_edge(a, p))
    m = mate(a, "fastened", rim, a.named_connector(s, "under_head"), flip=flip)
    return a, p, s, m


def world(a, iid, name):
    return a.world_frame(a.named_connector(iid, name))


def assert_seated(a, p, s, m, length):
    assert m["id"] not in a.errors and a.errors == {}
    head, tip = world(a, s, "under_head"), world(a, s, "tip")
    assert np.allclose(head["o"], [CX, CY, T], atol=1e-6)
    assert np.allclose(tip["o"], [CX, CY, T - length], atol=1e-6)
    assert np.allclose(head["z"], [0, 0, -1], atol=1e-6)


def test_unflipped_fastened_seats_head_on_surface_shank_in_hole():
    a, p, s, m = fastened()
    rim = a.world_frame(m["a"])
    uh = a.named_connector(s, "under_head")["frame"]
    print("rim frame", m["a"]["frame"], "under_head frame", uh)
    head, tip = world(a, s, "under_head"), world(a, s, "tip")
    print("solved T", a.T(s).round(6).tolist(), "head", head["o"], "tip", tip["o"])
    assert np.allclose(rim["z"], [0, 0, 1], atol=1e-6)                 # rim Z points out of the plate
    assert np.allclose(head["o"], [CX, CY, T], atol=1e-6)               # (a) on the rim centre
    assert tip["o"][2] < T                                              # (b) tip below the top surface
    assert np.hypot(tip["o"][0] - CX, tip["o"][1] - CY) < HOLE_R        # ... inside the hole
    lo, hi = K.bbox(a.world_shape(s))
    assert hi[2] > T + 1 and lo[2] < T                                  # (c) head above the surface
    assert K.volume(K.common(a.world_shape(s), box(30, 20, 20, (0, 0, T)))) > 1.0   # the head is above z = T
    assert K.volume(K.common(a.world_shape(s), a.world_shape(p))) < 1e-3   # shank clears the hole wall


def test_named_connector_shape_and_save_reload(tmp_path):
    a, p, s, m = fastened()
    c = m["b"]
    assert set(c) == {"inst", "kind", "name", "frame"} and c["kind"] == "named" and c["name"] == "under_head"
    path = str(tmp_path / "n.fasm")
    a.save(path)
    d = json.load(open(path))
    end = d["mates"][0]["b"]
    assert end["kind"] == "named" and "index" not in end and "ref" not in end
    b = A.AssemblyDoc.load(path, resolver=a.resolver)
    b.refresh()
    assert b.errors == {}
    assert b.mates[0]["b"]["kind"] == "named" and "index" not in b.mates[0]["b"]
    assert np.allclose(world(b, s, "under_head")["o"], [CX, CY, T], atol=1e-6)


def test_unknown_name_raises():
    a, p, s, m = fastened()
    with pytest.raises(K.KernelError, match="nosuch"):
        a.named_connector(s, "nosuch")
    with pytest.raises(K.KernelError):
        a.named_connector(p, "under_head")                              # a studio part offers no names yet


def test_length_change_keeps_seat_and_undo_restores():
    a, p, s, m = fastened(10)
    assert_seated(a, p, s, m, 10)
    before = json.dumps(a.instance(s)["source"]["params"], sort_keys=True), a.T(s).copy()
    shape10 = a.parts[s].shape
    tip10 = world(a, s, "tip")["o"].copy()
    res = a.set_standard_params([s], {"length": 16})
    assert res == {"changed": [s], "rejected": {}}
    assert a.instance(s)["source"]["params"]["length"] == 16
    assert_seated(a, p, s, m, 16)
    assert world(a, s, "tip")["o"][2] == pytest.approx(tip10[2] - 6, abs=1e-6)
    a.undo()
    assert json.dumps(a.instance(s)["source"]["params"], sort_keys=True) == before[0]
    assert np.allclose(a.T(s), before[1], atol=1e-9)
    assert a.parts[s].shape.IsSame(shape10)
    assert_seated(a, p, s, m, 10)
    assert a.instance(s)["source"]["params"]["length"] == 10
    a.undo()                                                            # the next undo is the mate, not a 2nd step
    assert a.mates == []


def test_rejected_change_leaves_instance_untouched_and_is_one_undo_step():
    a = A.AssemblyDoc()
    n1 = a.insert_standard_part(NUT, {"size": "M3"})
    sc = a.insert_standard_part(SCREW, {"size": "M3", "length": 10})
    n2 = a.insert_standard_part(NUT, {"size": "M4"})
    old = {i: json.dumps(a.instance(i), sort_keys=True) for i in (n1, sc, n2)}
    bad = "M60"                                  # the nut type offers it, the socket-head screw type does not
    assert bad in registry.standard_part(NUT)().sizes()
    assert bad not in registry.standard_part(SCREW)().sizes()
    res = a.set_standard_params([n1, sc, n2], {"size": bad})
    assert res["changed"] == [n1, n2] and list(res["rejected"]) == [sc]
    assert isinstance(res["rejected"][sc], str) and res["rejected"][sc]
    assert json.dumps(a.instance(sc), sort_keys=True) == old[sc]
    for i in (n1, n2):
        assert a.instance(i)["source"]["params"]["size"] == bad
    a.undo()
    for i in (n1, sc, n2):
        assert json.dumps(a.instance(i), sort_keys=True) == old[i]
    a.undo()                                     # the undo above was the single step of the call
    assert [i["id"] for i in a.instances] == [n1, sc]


def test_unoffered_length_is_rejected_others_change():
    a = A.AssemblyDoc()
    s1 = a.insert_standard_part(SCREW, {"size": "M3", "length": 10})
    s2 = a.insert_standard_part(SCREW, {"size": "M3", "length": 12})
    res = a.set_standard_params([s1, s2], {"length": 999})
    assert set(res["rejected"]) == {s1, s2} and res["changed"] == []
    assert not a.can_undo() or a.instance(s1)["source"]["params"]["length"] == 10
    res = a.set_standard_params([s1, s2], {"length": 16})
    assert res["changed"] == [s1, s2]


def test_size_change_moves_connectors():
    a = A.AssemblyDoc()
    n = a.insert_standard_part(NUT, {"size": "M3"})
    c = a.named_connector(n, "top")
    top3 = c["frame"]["o"][2]
    a.set_standard_params([n], {"size": "M5"})
    # the stored frame of the same name follows the new size
    assert a.connector_by_name(n, "top")["o"][2] > top3 + 0.5
    s = a.insert_standard_part(SCREW, {"size": "M3", "length": 10})
    tip = a.connector_by_name(s, "tip")["o"]
    a.set_standard_params([s], {"size": "M4", "length": 20})
    assert a.connector_by_name(s, "tip")["o"][2] == pytest.approx(-20)
    assert tip[2] == pytest.approx(-10)


def test_mated_named_frame_is_rederived_on_size_change():
    a = A.AssemblyDoc()
    n = a.insert_standard_part(NUT, {"size": "M3"})
    m = a.make_mate("fastened")
    base = a.insert_standard_part(NUT, {"size": "M3"})
    m["a"], m["b"] = a.named_connector(base, "top"), a.named_connector(n, "bottom")
    a.add_mate(m)
    old = a.mates[0]["a"]["frame"]["o"][2]
    a.set_standard_params([base], {"size": "M6"})
    assert a.mates[0]["a"]["frame"]["o"][2] > old + 0.5
    assert a.errors == {}
    # the nut sits on the new top
    assert world(a, n, "bottom")["o"][2] == pytest.approx(world(a, base, "top")["o"][2], abs=1e-6)


def test_removed_name_gives_lost_connector_error(monkeypatch):
    a = A.AssemblyDoc()
    base = a.insert_standard_part(NUT, {"size": "M3"})
    n = a.insert_standard_part(NUT, {"size": "M3"})
    m = a.make_mate("fastened")
    m["a"], m["b"] = a.named_connector(base, "top"), a.named_connector(n, "bottom")
    a.add_mate(m)
    cls = registry.standard_part(NUT)
    orig = cls.connectors
    monkeypatch.setattr(cls, "connectors", lambda self, p: {k: v for k, v in orig(self, p).items() if k != "top"})
    a.instance(base)["source"]["params"] = {"size": "M4"}               # a change that rebuilds the part
    a.refresh()
    assert "Lost its connector" in a.errors[m["id"]] and "top" in a.errors[m["id"]]
