"""examples/make_bolted_bracket.py: the two-part example builds, and the hand-check recipe of the Standard parts
gate (washers, screws, nuts, M3 tapped holes, bulk edit, BOM, save and reopen) works on it through the API."""
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "examples"))

import make_bolted_bracket as EX
from freeshape import assembly as A
from freeshape import kernel as K
from freeshape.document import Document
from freeshape.pyfeature import standard_part
from freeshape.standard_insert import apply_insert, hole_targets, plan_insert

SCREW = "sp:fasteners/iso4762"
NUT = "sp:fasteners/iso4032"
WASHER = "sp:fasteners/iso7089"
TAPPING = "sp:fasteners/iso7049"
TOP = EX.PLATE_T + EX.BASE_T                # the bracket's top face once it sits on the plate
M4_LEN = 20
M4_LEN_EDITED = 25
M3_LEN = 6                                  # the bracket base is 6 mm thick: the tip ends on the plate's top face


@pytest.fixture(scope="module")
def folder(tmp_path_factory):
    d = tmp_path_factory.mktemp("bolted")
    EX.main(str(d))
    return d


def resolver_for(folder):
    studio = Document.load(str(folder / "bolted_bracket_parts.fshape"))
    assert not studio.errors, studio.errors
    return lambda src: studio.state.parts[src["part"]]


def load(folder, name="bolted_bracket.fasm"):
    return A.AssemblyDoc.load(str(folder / name), resolver=resolver_for(folder))


def by_name(a, name):
    (inst,) = [i for i in a.instances if i["source"].get("kind") != "standard" and i["name"].startswith(name)]
    return inst["id"]


def planar_face(a, iid, nz=None, nx=None, z=None, x=None):
    """Index of the planar face of an instance (own frame) with the given normal axis sign-free and centre."""
    for i, f in enumerate(K.faces(a.parts[iid].shape)):
        info = K.face_info(f)
        if "n" not in info:
            continue
        n, c = info["n"], info["c"]
        if nz and abs(abs(n[2]) - 1) < 1e-6 and abs(c[2] - z) < 1e-6:
            return i
        if nx and abs(abs(n[0]) - 1) < 1e-6 and abs(c[0] - x) < 1e-6:
            return i
    raise AssertionError("face not found")


def pick(iid, index):
    return {"inst": iid, "kind": "face", "index": index, "click": None}


def bracket_top(a):
    b = by_name(a, "Bracket")
    return pick(b, planar_face(a, b, nz=True, z=EX.BASE_T))


def plate_underside(a):
    p = by_name(a, "Plate")
    return pick(p, planar_face(a, p, nz=True, z=0.0))


def world(a, iid, name):
    return a.world_frame(a.named_connector(iid, name))


def top_face_of(a, iid):
    """The pick of a washer's or nut's top planar face (the highest one)."""
    infos = [(i, K.face_info(f)) for i, f in enumerate(K.faces(a.parts[iid].shape))]
    planar = [(i, fi) for i, fi in infos if "n" in fi]
    return pick(iid, max(planar, key=lambda t: t[1]["c"][2])[0])


def sizes(plan):
    return {g["size"]: len(g["targets"]) for g in plan["groups"]}


def std(a, type_id=None):
    return [i for i in a.instances if i["source"].get("kind") == "standard"
            and (type_id is None or i["source"]["type"] == type_id)]


def test_files_load_and_bracket_is_seated(folder):
    for name in ("bolted_bracket_parts.fshape", "bolted_bracket.fasm"):
        assert (folder / name).is_file()
    a = load(folder)
    assert [i["name"].split()[0] for i in a.instances] == ["Plate", "Bracket"]
    assert a.errors == {} and a.dof == 0 and len(a.mates) == 1 and a.mates[0]["type"] == "fastened"
    plate, bracket = by_name(a, "Plate"), by_name(a, "Bracket")
    assert np.allclose(a.T(plate), np.eye(4))
    (lo, hi), (blo, bhi) = K.bbox(a.world_shape(plate)), K.bbox(a.world_shape(bracket))
    assert np.allclose([lo, hi], [[-10, -25, 0], [70, 25, EX.PLATE_T]], atol=1e-4)
    assert np.allclose([blo[:2], bhi[:2]], [[0, -20], [60, 20]], atol=1e-4)
    assert blo[2] == pytest.approx(EX.PLATE_T, abs=1e-4) and bhi[2] == pytest.approx(EX.PLATE_T + 30, abs=1e-4)
    assert a.interferences() == []


def test_hole_sizes_come_from_the_standard_tables():
    assert EX.M4_HOLE == 4.5 and EX.M3_TAP == 2.5 and EX.UPRIGHT_HOLE == 2.5
    m4 = standard_part(SCREW)().lengths("M4")
    assert M4_LEN in m4 and M4_LEN_EDITED in m4 and 6 in standard_part(SCREW)().lengths("M3")


def test_bracket_top_face_gives_m4_and_m3_targets(folder):
    a = load(folder)
    targets = hole_targets(a, [bracket_top(a)])
    assert sorted(round(t["diameter"], 3) for t in targets) == [2.5, 2.5, 4.5, 4.5, 4.5, 4.5]
    for name in (SCREW, WASHER):
        plan = plan_insert(a, name, [bracket_top(a)], {})
        assert sizes(plan) == {"M3": 2, "M4": 4} and plan["unmatched"] == []
    # a tapping screw reads the same rims as ST4.2 (4.5 mm) and ST2.2 (2.5 mm, the tapped holes)
    assert sizes(plan_insert(a, TAPPING, [bracket_top(a)], {})) == {"ST4.2": 4, "ST2.2": 2}
    # the plate's underside shows only the four M4 rims
    plan = plan_insert(a, NUT, [plate_underside(a)], {})
    assert sizes(plan) == {"M4": 4} and plan["unmatched"] == []


def test_upright_face_has_the_single_2_5_mm_hole(folder):
    a = load(folder)
    b = by_name(a, "Bracket")
    for x in (EX.BRACKET_DX, EX.BRACKET_DX + 6):
        t = hole_targets(a, [pick(b, planar_face(a, b, nx=True, x=x))])
        assert [round(h["diameter"], 3) for h in t] == [2.5]
        assert np.allclose(t[0]["centre"][1:], [0, EX.PLATE_T + EX.UPRIGHT_HOLE_Z], atol=1e-6)
    plan = plan_insert(a, SCREW, [pick(b, planar_face(a, b, nx=True, x=EX.BRACKET_DX + 6))], {})
    assert sizes(plan) == {"M3": 1}


def fasten_all(a):
    """The hand-check recipe: washers on the bracket's top face, screws on the washers, nuts under the plate."""
    wplan = plan_insert(a, WASHER, [bracket_top(a)], {})
    wres = apply_insert(a, wplan, {}, {})
    picks = [top_face_of(a, w) for w in wres["instances"]]
    assert all(len(hole_targets(a, [p])) == 1 for p in picks)           # the bore rim, not the outer edge
    splan = plan_insert(a, SCREW, picks, {})
    sres = apply_insert(a, splan, {"M3": M3_LEN, "M4": M4_LEN}, {})
    nplan = plan_insert(a, NUT, [plate_underside(a)], {})
    nres = apply_insert(a, nplan, {}, {})
    return wplan, wres, splan, sres, nplan, nres


def test_washers_screws_and_nuts(folder):
    a = load(folder)
    wplan, wres, splan, sres, nplan, nres = fasten_all(a)
    assert len(wres["instances"]) == 6 and len(sres["instances"]) == 6 and len(nres["instances"]) == 4
    assert a.errors == {}
    # washers sit on the bracket's top face (z = 12), one per hole
    for w in wres["instances"]:
        assert world(a, w, "bottom")["o"][2] == pytest.approx(TOP, abs=1e-6)
    # every screw sits on its washer and its shank goes through the bracket and the plate (M4) / the bracket (M3)
    seen = set()
    for iid, t in zip(sres["instances"], [t for g in splan["groups"] for t in g["targets"]]):
        prm = a.instance(iid)["source"]["params"]
        washer_top = K.bbox(a.world_shape(t["inst"]))[1][2]
        head, tip = world(a, iid, "under_head"), world(a, iid, "tip")
        assert head["o"][2] == pytest.approx(washer_top, abs=1e-6) and washer_top > TOP + 0.1
        assert np.allclose(head["z"], [0, 0, -1], atol=1e-6)
        assert np.allclose(head["o"][:2], tip["o"][:2], atol=1e-6)
        if prm["size"] == "M4":
            assert tip["o"][2] < 0 and tip["o"][2] == pytest.approx(washer_top - M4_LEN, abs=1e-6)
            assert any(abs(head["o"][0] - x) < 1e-6 and abs(head["o"][1] - y) < 1e-6
                       for x in EX.M4_XS for y in EX.HOLE_YS)
        else:
            assert EX.PLATE_T < tip["o"][2] < TOP             # M3: ends inside the bracket, above the plate
            assert head["o"][0] == pytest.approx(EX.M3_X, abs=1e-6)
        seen.add(tuple(np.round(head["o"][:2], 6)))
    assert len(seen) == 6
    # the nuts hang from the plate's underside (z = 0), one under each M4 screw
    for n in nres["instances"]:
        b = world(a, n, "bottom")
        assert b["o"][2] == pytest.approx(0.0, abs=1e-6) and np.allclose(b["z"], [0, 0, 1], atol=1e-6)
        top = K.bbox(a.world_shape(n))[0][2]
        assert top < -1.0
    m4_tips = [world(a, i, "tip")["o"][2] for i in sres["instances"]
               if a.instance(i)["source"]["params"]["size"] == "M4"]
    nut_bottoms = [K.bbox(a.world_shape(n))[0][2] for n in nres["instances"]]
    assert max(nut_bottoms) > max(m4_tips)                       # the screw tip is past the nut
    print("interferences with washers, screws and nuts:",
          [(a.instance(x)["name"], a.instance(y)["name"], round(v, 2)) for x, y, v, _ in a.interferences()])


def test_m3_screws_on_the_face_selection_do_not_clash(folder):
    a = load(folder)
    plan = plan_insert(a, SCREW, [bracket_top(a)], {})
    assert plan["unmatched"] == [] and sizes(plan) == {"M3": 2, "M4": 4}
    m3 = next(g for g in plan["groups"] if g["size"] == "M3")
    assert M3_LEN in m3["lengths"]
    res = apply_insert(a, plan, {"M3": M3_LEN, "M4": M4_LEN}, {})
    assert a.errors == {} and len(res["instances"]) == 6
    m3_ids = [i for i in res["instances"] if a.instance(i)["source"]["params"]["size"] == "M3"]
    assert len(m3_ids) == 2
    for i in m3_ids:
        head, tip = world(a, i, "under_head"), world(a, i, "tip")
        assert head["o"][2] == pytest.approx(TOP, abs=1e-6) and head["o"][0] == pytest.approx(EX.M3_X, abs=1e-6)
        assert tip["o"][2] == pytest.approx(EX.PLATE_T, abs=1e-6)       # ends on the plate's top face
    clashes = a.interferences()
    assert not [c for c in clashes if set(c[:2]) & set(m3_ids)], clashes
    assert clashes == []                                              # nor do the M4 screws clash with the parts


def test_bulk_edit_m4_length_and_undo(folder):
    a = load(folder)
    wplan, wres, splan, sres, nplan, nres = fasten_all(a)
    m4 = [i for i in sres["instances"] if a.instance(i)["source"]["params"]["size"] == "M4"]
    before = {i: (world(a, i, "under_head")["o"].copy(), a.instance(i)["source"]["params"]["length"]) for i in m4}
    res = a.set_standard_params(m4, {"length": M4_LEN_EDITED})
    assert sorted(res["changed"]) == sorted(m4) and res["rejected"] == {} and a.errors == {}
    for i in m4:
        assert a.instance(i)["source"]["params"]["length"] == M4_LEN_EDITED
        head, tip = world(a, i, "under_head")["o"], world(a, i, "tip")["o"]
        assert np.allclose(head, before[i][0], atol=1e-6)                       # still on its washer
        assert tip[2] == pytest.approx(head[2] - M4_LEN_EDITED, abs=1e-6)
    a.undo()
    assert [a.instance(i)["source"]["params"]["length"] for i in m4] == [M4_LEN] * 4 and a.errors == {}
    assert all(np.allclose(world(a, i, "under_head")["o"], before[i][0], atol=1e-6) for i in m4)


def test_model_the_thread_on_one_screw(folder):
    a = load(folder)
    wplan, wres, splan, sres, nplan, nres = fasten_all(a)
    one = next(i for i in sres["instances"] if a.instance(i)["source"]["params"]["size"] == "M4")
    head = world(a, one, "under_head")["o"].copy()
    plain = K.volume(a.world_shape(one))
    res = a.set_standard_params([one], {"model_thread": True})
    assert res["changed"] == [one] and a.errors == {}
    assert np.allclose(world(a, one, "under_head")["o"], head, atol=1e-6)
    assert K.volume(a.world_shape(one)) != pytest.approx(plain, rel=1e-6)


def test_bom_and_save_reload(folder, tmp_path):
    a = load(folder)
    fasten_all(a)
    rows = a.bom()
    assert len({r["name"] for r in rows}) == len(rows)                        # each designation once
    qty = {r["name"]: r["qty"] for r in rows}
    print(qty)
    assert sum(qty.values()) == 2 + 6 + 6 + 4
    assert sorted(r["qty"] for r in rows) == sorted([1, 1, 2, 4, 2, 4, 4])
    (tmp_path / "bolted_bracket_parts.fshape").write_bytes((folder / "bolted_bracket_parts.fshape").read_bytes())
    a.save(str(tmp_path / "again.fasm"))
    b = load(tmp_path, "again.fasm")
    assert [i["id"] for i in b.instances] == [i["id"] for i in a.instances] and len(b.mates) == len(a.mates)
    for i in a.instances:
        assert np.allclose(b.T(i["id"]), a.T(i["id"]), atol=1e-6), i["name"]
    assert b.errors == {}
    assert {r["name"]: r["qty"] for r in b.bom()} == qty
