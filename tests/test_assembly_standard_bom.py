"""I2.3: bill of materials, STEP export and interference for Standard parts."""
import numpy as np
import pytest

from freeshape import assembly as A
from freeshape import kernel as K
from freeshape import pyfeature
from freeshape.pyfeature import registry
from test_assembly import box, cyl, edge, face, make, mate, src

SCREW = "sp:fasteners/iso4762"
T = 5.0
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


def plate_with_hole(hole_r):
    return K.cut(box(30, 20, T), cyl(hole_r, T + 2, (CX, CY, -1)))


def rim_edge(a, iid):
    return edge(a, iid, lambda e: e.get("center") is not None and abs(e["center"][2] - T) < 1e-6
                and abs(e["center"][0] - CX) < 1e-6)


def screwed(hole_r, lib=None, **params):
    """A plate with a hole of radius hole_r, and an M3 x 10 screw fastened to its top rim."""
    a = make({"plate": plate_with_hole(hole_r), **(lib or {})})
    p = a.insert(src("plate"))
    s = a.insert_standard_part(SCREW, {"size": "M3", "length": 10, **params})
    rim = a.connector(p, "edge", rim_edge(a, p))
    m = mate(a, "fastened", rim, a.named_connector(s, "under_head"))
    assert a.errors == {}
    return a, p, s, m


def pair_ids(res):
    return {frozenset((r[0], r[1])) for r in res}


def test_bom_groups_by_size_and_length():
    a = make({})
    for _ in range(8):
        a.insert_standard_part(SCREW, {"size": "M3", "length": 10})
    for _ in range(4):
        a.insert_standard_part(SCREW, {"size": "M4", "length": 12})
    rows = a.bom()
    assert [(r["name"], r["qty"], r["source"]) for r in rows] == [
        ("ISO 4762 M3 x 10", 8, "Standard part"), ("ISO 4762 M4 x 12", 4, "Standard part")]
    assert all(len(r["instances"]) == r["qty"] and r["volume"] > 0 for r in rows)


def test_bom_ignores_model_thread():
    a = make({})
    a.insert_standard_part(SCREW, {"size": "M3", "length": 10, "model_thread": False})
    a.insert_standard_part(SCREW, {"size": "M3", "length": 10, "model_thread": True})
    a.insert_standard_part(SCREW, {"size": "M3", "length": 12, "model_thread": True})
    rows = a.bom()
    assert [(r["name"], r["qty"]) for r in rows] == [("ISO 4762 M3 x 10", 2), ("ISO 4762 M3 x 12", 1)]


def test_screw_in_tap_drill_hole_has_no_pair_with_plate():
    a, p, s, m = screwed(1.25)                              # 2.5 mm hole, smaller than the M3 shank
    assert a.interferences() == []


def test_unmated_screw_overlapping_plate_reports_pair():
    a = make({"plate": plate_with_hole(1.25)})
    p = a.insert(src("plate"))
    s = a.insert_standard_part(SCREW, {"size": "M3", "length": 10}, T=A.translation([15, 10, 2.5]))
    assert pair_ids(a.interferences()) == {frozenset((p, s))}


def test_suppressing_the_mate_brings_the_pair_back():
    a, p, s, m = screwed(1.25)
    a.set_suppressed(m["id"], True)
    assert pair_ids(a.interferences()) == {frozenset((p, s))}
    a.set_suppressed(m["id"], False)
    assert a.interferences() == []


def test_mated_screw_still_clashes_with_unmated_block():
    a, p, s, m = screwed(1.25, lib={"blk": box(6, 6, 4)})
    blk = a.insert(src("blk"), T=A.translation([CX - 3, CY - 3, T + 0.5]))     # overlaps the screw head
    assert pair_ids(a.interferences()) == {frozenset((s, blk))}


def test_ordinary_parts_with_fastened_mate_still_checked():
    a = make({"plate": box(10, 10, 5), "block": box(10, 10, 5)})
    p, b = a.insert(src("plate")), a.insert(src("block"))
    top = lambda iid: a.connector(iid, "face", face(a, iid, lambda f: f.get("n") and f["n"][2] > 0.9))
    mate(a, "fastened", top(p), top(b), flip=True)         # flipped, the block lands inside the plate
    assert a.errors == {}
    assert pair_ids(a.interferences()) == {frozenset((p, b))}


def test_two_overlapping_plates_report_pair():
    a = make({"plate": box(20, 10, 5), "block": box(10, 10, 5)})
    p, b = a.insert(src("plate")), a.insert(src("block"))
    a.move_instance(b, A.translation(-np.array(a.instance(b)["T"])[:3, 3] + [5, 5, 2]))
    assert pair_ids(a.interferences()) == {frozenset((p, b))}


def test_step_export_round_trip(tmp_path):
    a = make({"plate": box(30, 20, T)})
    a.insert(src("plate"))
    for k in range(3):
        a.insert_standard_part(SCREW, {"size": "M3", "length": 10}, T=A.translation([5 + 8 * k, 10, T]))
    a.insert_standard_part(SCREW, {"size": "M4", "length": 12}, T=A.translation([5, 3, T]))
    path = str(tmp_path / "s.step")
    a.export(path)
    got = K.import_step_with_colors(path)
    vols = [K.volume(a.world_shape(i["id"])) for i in a.instances]
    assert len(got) == len(a.instances) == 5
    assert sum(K.volume(s) for s, _n, _c, _f in got) == pytest.approx(sum(vols), rel=1e-3)
    assert sorted(n for _s, n, _c, _f in got) == sorted(
        ["plate"] + ["ISO 4762 M3 x 10"] * 3 + ["ISO 4762 M4 x 12"])
