"""I3.2: deep groove ball bearings and heat-set threaded inserts as Standard parts of the hardware plugin.

Where each test's numbers come from:
* Bearings: upstream's table, `freeshape/addons/hardware/data/bearings.csv` (FreeShape 2.0, freeshape/stdparts.py at
  355fc85). The FreeCAD Fasteners workbench has no bearing table and no standard was consulted, so the test copies
  the 17 rows below as UPSTREAM_BEARINGS: it checks the plugin keeps upstream's values, not that they are right.
* Heat-set inserts: FreeCAD Fasteners file `freeshape/addons/hardware/data/HeatInserts.csv` (table IUTHeatInsertdef),
  an unmodified copy of FsData/HeatInserts.csv at workbench commit 375a73d9706a30072c132fb061e7636e8ed99f82. Upstream's
  six sizes (UPSTREAM_INSERT_SIZES) must all be offered; upstream's outer diameters and lengths differ from the
  workbench's and are not kept (M3: 4.6 x 4.0 against the workbench's 4.55 x 5.74).
"""
import filecmp
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from OCP.BRepBndLib import BRepBndLib  # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder  # noqa: E402
from OCP.Bnd import Bnd_Box  # noqa: E402
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt  # noqa: E402

from freeshape import assembly as A  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import pyfeature  # noqa: E402
from freeshape.addons.hardware import heat_insert  # noqa: E402
from freeshape.addons.hardware import tables as hwt  # noqa: E402
from freeshape.features import Part  # noqa: E402
from freeshape.standard_insert import apply_insert, plan_insert  # noqa: E402
from freeshape.ui.standard_parts_ui import StandardPartsPane  # noqa: E402

BEARING = "sp:hardware/deep_groove"
INSERT = "sp:hardware/heat_insert"

# Upstream's deep groove ball bearings (FreeShape 2.0, stdparts.py at 355fc85): designation -> (bore, outside, width) mm.
UPSTREAM_BEARINGS = {
    "623": (3, 10, 4), "624": (4, 13, 5), "625": (5, 16, 5), "626": (6, 19, 6), "688": (8, 16, 5),
    "608": (8, 22, 7), "6800": (10, 19, 5), "6000": (10, 26, 8), "6001": (12, 28, 8), "6002": (15, 32, 9),
    "6003": (17, 35, 10), "6200": (10, 30, 9), "6201": (12, 32, 10), "6202": (15, 35, 11),
    "6203": (17, 40, 12), "6204": (20, 47, 14), "6205": (25, 52, 15)}
# Upstream's heat-set insert sizes. Its outer diameters and lengths (M3: 4.6 x 4.0) are replaced by the workbench's.
UPSTREAM_INSERT_SIZES = ["M2", "M2.5", "M3", "M4", "M5", "M6"]

T = 8.0                                   # plate thickness: the top face is z = T
CX, CY = 50.0, 40.0


def part(type_id):
    cls = pyfeature.standard_part(type_id)
    assert cls is not None, pyfeature.problems()
    return cls()


def tight_bbox(shape):
    b = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, b, False, False)
    return np.array(b.CornerMin().Coord()), np.array(b.CornerMax().Coord())


def cyl_at(r, h, at):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*at), gp_Dir(0, 0, 1)), r, h).Shape()


# ------------------------------------------------------------------ sizes and geometry
def test_the_plugin_loads_and_offers_every_upstream_size():
    """Sizes: upstream's bearing table; the heat-set inserts' sizes are upstream's, their rows the workbench's."""
    assert pyfeature.problems() == []
    assert part(BEARING).sizes() == list(UPSTREAM_BEARINGS) and len(part(BEARING).sizes()) == 17
    assert set(UPSTREAM_INSERT_SIZES) <= set(part(INSERT).sizes())
    assert part(BEARING).lengths("608") == [] and part(INSERT).lengths("M3") == []


@pytest.mark.parametrize("size", list(UPSTREAM_BEARINGS))
def test_bearing_matches_the_table(size):
    """Bore, outside diameter, width: upstream's bearing table (bearings.csv), 1e-6."""
    d, D, B = UPSTREAM_BEARINGS[size]
    row = hwt.tables()["UpstreamBearingdef"].row_dict(size)
    assert (row["d"], row["D"], row["B"]) == (d, D, B)
    shape = part(BEARING).build({"size": size})
    assert K.is_valid(shape) and len(K.solids(shape)) == 1
    lo, hi = tight_bbox(shape)
    assert hi[0] == pytest.approx(D / 2, abs=1e-6) and lo[0] == pytest.approx(-D / 2, abs=1e-6)
    assert lo[2] == pytest.approx(0.0, abs=1e-6) and hi[2] == pytest.approx(B, abs=1e-6)
    radii = {round(K.face_info(f)["r"], 6) for f in K.faces(shape) if "r" in K.face_info(f)}
    assert round(d / 2, 6) in radii and round(D / 2, 6) in radii            # the bore and the outer ring


@pytest.mark.parametrize("size", UPSTREAM_INSERT_SIZES)
def test_insert_matches_the_workbench_table(size):
    """Length and external diameter: FreeCAD Fasteners file HeatInserts.csv (Length, ExtDia), 1e-6."""
    row = hwt.tables()["IUTHeatInsertdef"].row_dict(size)
    shape = part(INSERT).build({"size": size})
    assert K.is_valid(shape) and len(K.solids(shape)) == 1
    lo, hi = tight_bbox(shape)
    assert hi[0] == pytest.approx(row["ExtDia"] / 2, abs=1e-6) and lo[0] == pytest.approx(-row["ExtDia"] / 2, abs=1e-6)
    assert hi[2] == pytest.approx(0.0, abs=1e-6) and lo[2] == pytest.approx(-row["Length"], abs=1e-6)


def test_insert_has_a_bore_and_every_workbench_size_builds():
    """Bore: the minor diameter of the thread, from ISO262def.csv (coarse pitch), as makeHeatInsert computes it."""
    for size in part(INSERT).sizes():
        shape = part(INSERT).build({"size": size})
        assert K.is_valid(shape) and len(K.solids(shape)) == 1, size
        d = heat_insert.minor_diameter(size)
        assert 0 < d < heat_insert.nominal_diameter(size)
        assert any(abs(K.face_info(f).get("r", -1) - d / 2) < 1e-6 for f in K.faces(shape)), size
    assert part(INSERT).sizes() == list(hwt.tables()["IUTHeatInsertdef"].rows)


def test_vendored_workbench_tables_are_identical_to_the_workbench():
    """HeatInserts.csv and iso262def.csv are byte copies of FsData/ (kept in tests/data/hardware/upstream/FsData)."""
    up = os.path.join(os.path.dirname(__file__), "data", "hardware", "upstream", "FsData")
    names = sorted(os.listdir(up))
    assert names == ["HeatInserts.csv", "iso262def.csv"]
    match, mismatch, errors = filecmp.cmpfiles(up, hwt.DATA_DIR, names, shallow=False)
    assert not mismatch and not errors


def test_validation_names_the_bad_key():
    for tid in (BEARING, INSERT):
        with pytest.raises(K.KernelError, match="size"):
            part(tid).validate({"size": "nope"})
        with pytest.raises(K.KernelError, match="size"):
            part(tid).validate({})
    assert part(BEARING).validate({"size": "608"}) == {"size": "608"}


# ------------------------------------------------------------------ connectors, designation, categories
def test_connectors():
    """bore: on the axis at mid-width. seat: on the axis at the top face. insert top: on the axis at z = 0."""
    c = part(BEARING).connectors({"size": "608"})
    assert set(c) == {"bore", "seat"} and part(BEARING).insert_connector == "seat"
    assert c["bore"]["o"] == [0, 0, 3.5] and c["bore"]["z"] == [0, 0, 1]
    assert c["seat"]["o"] == [0, 0, 7] and c["seat"]["z"] == [0, 0, -1]
    c = part(INSERT).connectors({"size": "M3"})
    assert set(c) == {"top"} and part(INSERT).insert_connector == "top"
    assert c["top"]["o"] == [0, 0, 0] and c["top"]["z"] == [0, 0, -1]


def test_categories_and_designation():
    """Designation: upstream's text for both families."""
    assert part(BEARING).category == "Bearings" and part(INSERT).category == "Inserts"
    assert part(BEARING).designation({"size": "608"}) == "Bearing 608 (8×22×7)"
    assert part(INSERT).designation({"size": "M3"}) == "Heat-set insert M3"


# ------------------------------------------------------------------ mates and hole matching
def plate_doc(shape):
    a = A.AssemblyDoc()
    a.resolver = lambda s: Part("x", s["part"], shape if s["part"] == "plate" else s["shape"], (0.7, 0.7, 0.7), None)
    p = a.insert({"kind": "studio", "uid": "u", "part": "plate"})
    return a, p


def top_pick(a, iid):
    for i, f in enumerate(K.faces(a.parts[iid].shape)):
        info = K.face_info(f)
        if "n" in info and info["n"][2] > 0.99 and abs(info["c"][2] - T) < 1e-6:
            return {"inst": iid, "kind": "face", "index": i, "click": None}
    raise AssertionError("no top face")


def hole_plate(d):
    plate = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 100, 80, T).Shape()
    return K.cut(plate, cyl_at(d / 2, T + 2, (CX, CY, -1)))


def world(a, iid, name):
    return a.world_frame(a.named_connector(iid, name))


def test_bearing_bore_mates_to_a_shaft_with_a_fastened_mate():
    """Mate: the existing fastened mate between the bearing's bore connector and a shaft's circular edge."""
    shaft = cyl_at(4.0, 30.0, (CX, CY, 0))
    a = A.AssemblyDoc()
    a.resolver = lambda s: Part("x", s["part"], shaft, (0.7, 0.7, 0.7), None)
    ishaft = a.insert({"kind": "studio", "uid": "u", "part": "shaft"})
    ib = a.insert_standard_part(BEARING, {"size": "608"})
    edge = next(i for i, e in enumerate(K.edges(a.parts[ishaft].shape))
                if abs(K.edge_info(e).get("r", 0) - 4.0) < 1e-6 and abs(K.edge_info(e)["center"][2] - 30.0) < 1e-6)
    end = a.connector(ishaft, "edge", edge)
    m = a.make_mate("fastened")
    m["a"], m["b"] = end, a.named_connector(ib, "bore")
    a.add_mate(m)
    assert a.errors == {}
    bore = world(a, ib, "bore")
    assert np.allclose(bore["o"], [CX, CY, 30.0], atol=1e-6) and abs(abs(bore["z"][2]) - 1) < 1e-9
    lo, hi = tight_bbox(a.world_shape(ib))
    assert np.allclose((lo[:2] + hi[:2]) / 2, [CX, CY], atol=1e-6)           # the bore sits on the shaft axis
    assert (lo[2] + hi[2]) / 2 == pytest.approx(30.0, abs=1e-6) and hi[2] - lo[2] == pytest.approx(7.0, abs=1e-6)


@pytest.mark.parametrize("size", list(UPSTREAM_BEARINGS))
def test_bearing_seats_in_a_housing_hole_of_its_outside_diameter(size):
    """Hole: the outside diameter D of upstream's bearing table. 625 and 688 share D = 16 and the larger bore wins."""
    D = UPSTREAM_BEARINGS[size][1]
    expect = {"625": "688"}.get(size, size)
    same = [s for s, v in UPSTREAM_BEARINGS.items() if v[1] == D]
    if len(same) > 1:
        expect = max(same, key=lambda s: UPSTREAM_BEARINGS[s][0])
    assert part(BEARING).match_hole(D) == expect
    a, p = plate_doc(hole_plate(D))
    plan = plan_insert(a, BEARING, [top_pick(a, p)], {})
    assert [(g["size"], len(g["targets"])) for g in plan["groups"]] == [(expect, 1)] and plan["unmatched"] == []
    res = apply_insert(a, plan, {}, {})
    assert len(res["mates"]) == 1 and a.errors == {}
    seat = world(a, res["instances"][0], "seat")
    assert np.allclose(seat["o"], [CX, CY, T], atol=1e-6)
    lo, hi = tight_bbox(a.world_shape(res["instances"][0]))
    assert hi[2] == pytest.approx(T, abs=1e-6) and lo[2] == pytest.approx(T - UPSTREAM_BEARINGS[expect][2], abs=1e-6)


@pytest.mark.parametrize("size", UPSTREAM_INSERT_SIZES)
def test_insert_matches_a_hole_of_its_installation_diameter(size):
    """Hole: the D column of HeatInserts.csv. M2.5 and M3 share D = 4.00 and the larger size (M3) wins."""
    row = hwt.tables()["IUTHeatInsertdef"].row_dict(size)
    expect = "M3" if size == "M2.5" else size
    assert part(INSERT).match_hole(row["D"]) == expect
    a, p = plate_doc(hole_plate(row["D"]))
    plan = plan_insert(a, INSERT, [top_pick(a, p)], {})
    assert [(g["size"], len(g["targets"])) for g in plan["groups"]] == [(expect, 1)] and plan["unmatched"] == []
    res = apply_insert(a, plan, {}, {})
    assert len(res["mates"]) == 1 and a.errors == {}
    iid = res["instances"][0]
    assert np.allclose(world(a, iid, "top")["o"], [CX, CY, T], atol=1e-6)
    lo, hi = tight_bbox(a.world_shape(iid))
    length = hwt.tables()["IUTHeatInsertdef"].column(expect, "Length")
    assert hi[2] == pytest.approx(T, abs=1e-6) and lo[2] == pytest.approx(T - length, abs=1e-6)


def test_hole_tolerance_and_bad_input():
    """Tolerance: hole_match.TOL (0.05 mm)."""
    assert part(BEARING).match_hole(22.04) == "608" and part(BEARING).match_hole(22.2) is None
    assert part(INSERT).match_hole(4.8) == "M3.5" and part(INSERT).match_hole(4.4) is None
    for p in (part(BEARING), part(INSERT)):
        assert p.match_hole(None) is None and p.match_hole(float("nan")) is None and p.match_hole("4") is None


# ------------------------------------------------------------------ registry, pane, bill of materials, packaging
def test_both_register_through_the_plugin_registry_and_show_in_the_pane():
    """Types: the plugin manifest (plugin.json) through the registry."""
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    info = pyfeature.registry.plugin("hardware")
    assert info is not None and info.name == "Hardware"
    assert {BEARING, INSERT} <= {t for t, _cls, _info in pyfeature.registry.standard_parts()}
    items = StandardPartsPane(A.AssemblyDoc()).type_items()
    assert BEARING in items["Bearings"] and INSERT in items["Inserts"]


def test_both_appear_in_the_bill_of_materials_with_a_designation():
    a = A.AssemblyDoc()
    for _ in range(2):
        a.insert_standard_part(BEARING, {"size": "608"})
    a.insert_standard_part(INSERT, {"size": "M3"})
    rows = a.bom()
    assert [(r["name"], r["qty"], r["source"]) for r in rows] == [
        ("Bearing 608 (8×22×7)", 2, "Standard part"), ("Heat-set insert M3", 1, "Standard part")]
    assert all(r["volume"] and r["volume"] > 0 for r in rows)


def test_nothing_parked_remains_in_the_hardware_addon():
    """After this card addons/hardware holds plugin classes: no shapes.py, no importer of one."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert not os.path.exists(os.path.join(root, "freeshape", "addons", "hardware", "shapes.py"))
    hits = []
    for base_dir in ("freeshape", "tests"):
        for dp, _dn, fns in os.walk(os.path.join(root, base_dir)):
            for fn in fns:
                if fn.endswith(".py") and fn != "test_hardware_addon.py":
                    with open(os.path.join(dp, fn), encoding="utf-8") as fh:
                        if "hardware.shapes" in fh.read() or "hardware import shapes" in fh.read():
                            hits.append(fn)
    assert hits == []


def test_package_data_ships_the_plugin_and_its_tables():
    """pyproject.toml's package data globs match the hardware plugin.json, NOTICE.md and every data file."""
    import fnmatch
    import tomllib
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "pyproject.toml"), "rb") as fh:
        globs = tomllib.load(fh)["tool"]["setuptools"]["package-data"]["freeshape"]
    base_dir = os.path.join(root, "freeshape")
    shipped = [os.path.join("addons", "hardware", "plugin.json"), os.path.join("addons", "hardware", "NOTICE.md")]
    shipped += [os.path.join("addons", "hardware", "data", f) for f in os.listdir(hwt.DATA_DIR)]
    assert len(shipped) == 5
    for rel in shipped:
        assert os.path.exists(os.path.join(base_dir, rel))
        rel_posix = rel.replace(os.sep, "/")
        # setuptools globs: "**" spans directories; "*" does not cross "/"
        assert any(fnmatch.fnmatch(rel_posix, g.replace("**/", "*/").replace("**", "*")) and
                   g.count("/") <= rel_posix.count("/") + 1 for g in globs), rel
