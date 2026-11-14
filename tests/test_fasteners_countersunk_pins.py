"""I3.1: countersunk socket screws (ISO 10642) and dowel pins (ISO 8734) as Standard parts of the fasteners plugin.

Where each test's numbers come from:
* FreeCAD Fasteners file: `freeshape/addons/fasteners/data/iso10642def.csv` and `iso8734def.csv`, unmodified copies
  of FsData/ in the workbench at commit 375a73d9706a30072c132fb061e7636e8ed99f82.
* Upstream's table: the size lists and head diameters FreeShape 2.0 offered (freeshape/stdparts.py at 355fc85),
  copied below as UPSTREAM_*. They were in freeshape/addons/hardware/shapes.py until this port moved them here.
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import math  # noqa: E402

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from OCP.BRepAdaptor import BRepAdaptor_Surface  # noqa: E402
from OCP.BRepBndLib import BRepBndLib  # noqa: E402
from OCP.Bnd import Bnd_Box  # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeCone  # noqa: E402
from OCP.GeomAbs import GeomAbs_Cone  # noqa: E402
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt  # noqa: E402

from freeshape import assembly as A  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import pyfeature  # noqa: E402
from freeshape.addons.fasteners import fastener_base as fb  # noqa: E402
from freeshape.addons.fasteners import tables as tbl  # noqa: E402
from freeshape.features import COUNTERSINK, Part  # noqa: E402
from freeshape.standard_insert import apply_insert, plan_insert  # noqa: E402
from freeshape.ui.standard_parts_ui import StandardPartsPane  # noqa: E402

CSK = "sp:fasteners/iso10642"
PIN = "sp:fasteners/iso8734"

# Upstream's ISO 10642 table (M3 to M12): size -> head diameter dk (mm), 90 degree head.
UPSTREAM_CSK_DK = {"M3": 6.72, "M4": 8.96, "M5": 11.2, "M6": 13.44, "M8": 17.92, "M10": 22.4, "M12": 26.88}
# Upstream's ISO 8734 diameters (mm).
UPSTREAM_PINS = [2, 2.5, 3, 4, 5, 6, 8, 10, 12]

T = 6.0                                   # plate thickness: the top face is z = T
CX, CY = 20.0, 15.0


def part(type_id):
    cls = pyfeature.standard_part(type_id)
    assert cls is not None, pyfeature.problems()
    return cls()


def tight_bbox(shape):
    b = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, b, False, False)
    return np.array(b.CornerMin().Coord()), np.array(b.CornerMax().Coord())


def pin_size(d):
    return f"{d:g} mm"


def csk_cases():
    out = []
    for size in UPSTREAM_CSK_DK:
        lens = part(CSK).lengths(size)
        out += [pytest.param(size, lens[0], id=f"{size}x{lens[0]}"), pytest.param(size, lens[-1], id=f"{size}x{lens[-1]}")]
    return out


def pin_cases():
    out = []
    for d in UPSTREAM_PINS:
        lens = part(PIN).lengths(pin_size(d))
        out += [pytest.param(d, lens[0], id=f"d{d:g}x{lens[0]}"), pytest.param(d, lens[-1], id=f"d{d:g}x{lens[-1]}")]
    return out


# ------------------------------------------------------------------ sizes and geometry
def test_sizes_include_every_upstream_size():
    """Sizes: FreeCAD Fasteners file (iso10642def.csv, iso8734def.csv); the sizes to include: upstream's table."""
    assert set(UPSTREAM_CSK_DK) <= set(part(CSK).sizes())
    assert {pin_size(d) for d in UPSTREAM_PINS} <= set(part(PIN).sizes())
    assert pyfeature.problems() == []


@pytest.mark.parametrize("size,length", csk_cases())
def test_countersunk_screw_matches_the_tables(size, length):
    """Head values: FreeCAD Fasteners file iso10642def.csv (dk_theo, csh_diam); dk also against upstream's table."""
    p = part(CSK)
    shape = p.build({"size": size, "length": length})
    assert K.is_valid(shape) and len(K.solids(shape)) == 1
    row = tbl.tables()["ISO10642def"].row_dict(size)
    assert row["dk_theo"] == pytest.approx(UPSTREAM_CSK_DK[size], abs=1e-6)          # same number in both sources
    lo, hi = tight_bbox(shape)
    assert hi[2] == pytest.approx(0.0, abs=1e-6) and lo[2] == pytest.approx(-length, abs=1e-6)
    assert hi[0] == pytest.approx(row["csh_diam"] / 2, abs=1e-6)       # head diameter as the workbench draws it
    # the cone, extended to the head top, reaches the theoretical diameter dk_theo, at 90 degrees
    cones = []
    for f in K.faces(shape):
        ad = BRepAdaptor_Surface(f)
        if ad.GetType() == GeomAbs_Cone:
            cones.append((tight_bbox(f)[1][0], ad.Cone()))
    # a cone's radius at height z is |z - apex z| * tan(semi-angle); the head cone is the one as wide as the head
    head = [c for w, c in cones if w == pytest.approx(row["csh_diam"] / 2, abs=1e-6)]
    assert len(head) == 1
    head = head[0]
    assert abs(head.SemiAngle()) == pytest.approx(math.pi / 4, abs=1e-9)
    radius_at_top = abs(head.Apex().Z()) * math.tan(abs(head.SemiAngle()))
    assert 2 * radius_at_top == pytest.approx(row["dk_theo"], abs=1e-6)


@pytest.mark.parametrize("d,length", pin_cases())
def test_dowel_pin_matches_the_table(d, length):
    """Diameter and chamfer: FreeCAD Fasteners file iso8734def.csv; diameters also against upstream's list."""
    p = part(PIN)
    size = pin_size(d)
    shape = p.build({"size": size, "length": length})
    assert K.is_valid(shape) and len(K.solids(shape)) == 1
    row = tbl.tables()["ISO8734def"].row_dict(size)
    assert row["d"] == pytest.approx(d, abs=1e-6)
    lo, hi = tight_bbox(shape)
    assert hi[0] == pytest.approx(d / 2, abs=1e-6) and lo[0] == pytest.approx(-d / 2, abs=1e-6)
    assert hi[2] == pytest.approx(0.0, abs=1e-6) and lo[2] == pytest.approx(-length, abs=1e-6)
    cham = row["c"]
    full = math.pi * (d / 2) ** 2 * length
    assert K.volume(shape) < full and K.volume(shape) > full - 2 * cham * math.pi * (d / 2) ** 2   # two chamfers


def test_validation_names_the_bad_key():
    """Rules: the FreeCAD length and range tables in iso10642def.csv and iso8734def.csv."""
    for tid, size, bad in ((CSK, "M6", 7), (PIN, "6 mm", 7)):
        p = part(tid)
        with pytest.raises(K.KernelError, match="length"):
            p.validate({"size": size, "length": bad})
        with pytest.raises(K.KernelError, match="size"):
            p.validate({"size": "M99", "length": 10})
    assert part(CSK).lengths("M3") == [8, 10, 12, 16, 20, 25, 30]       # Min_L 8, Max_L 30 in ISO10642range
    assert part(PIN).lengths("2 mm") == [5, 6, 8, 10, 12, 14, 16, 18, 20]


def test_categories_and_designation():
    """Designation style: as the other fasteners ('ISO 4762 M3 x 10')."""
    assert part(CSK).category == "Screws" and part(PIN).category == "Pins"
    assert part(CSK).designation({"size": "M5", "length": 16}) == "ISO 10642 M5 x 16"
    assert part(PIN).designation({"size": "4 mm", "length": 20}) == "ISO 8734 4 mm x 20"


# ------------------------------------------------------------------ connectors and insertion
def test_connectors():
    """Frames: as ScrewPart (ISO 4762): Z points toward the tip, the head top / pin top end is at z = 0."""
    c = part(CSK).connectors({"size": "M5", "length": 16})
    assert set(c) == {"under_head", "tip"} and part(CSK).insert_connector == "under_head"
    assert c["under_head"]["o"] == [0, 0, 0] and c["under_head"]["z"] == [0, 0, -1]
    assert c["tip"]["o"] == [0, 0, -16] and c["tip"]["z"] == [0, 0, -1]
    c = part(PIN).connectors({"size": "4 mm", "length": 20})
    assert set(c) == {"top", "tip"} and part(PIN).insert_connector == "top"
    assert c["top"]["o"] == [0, 0, 0] and c["tip"]["o"] == [0, 0, -20]


def csk_plate(size, rim):
    """A plate with a clearance bore for `size` (Hole feature table, normal fit) and a 90 degree countersink of
    diameter `rim` at the top face."""
    from freeshape.features import CLEARANCE
    bore = CLEARANCE[size][1]
    plate = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 40, 30, T).Shape()
    plate = K.cut(plate, BRepPrimAPI_MakeCylinderAt(bore / 2, T + 2, (CX, CY, -1)))
    h = (rim - bore) / 2
    cone = BRepPrimAPI_MakeCone(gp_Ax2(gp_Pnt(CX, CY, T - h), gp_Dir(0, 0, 1)), bore / 2, rim / 2, h).Shape()
    ext = BRepPrimAPI_MakeCylinderAt(rim / 2, 1.0, (CX, CY, T))
    return K.cut(plate, K.fuse(cone, ext))


def BRepPrimAPI_MakeCylinderAt(r, h, at):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*at), gp_Dir(0, 0, 1)), r, h).Shape()


def plate_doc(shape):
    a = A.AssemblyDoc()
    a.resolver = lambda s: Part("x", "plate", shape, (0.7, 0.7, 0.7), None)
    p = a.insert({"kind": "studio", "uid": "u", "part": "plate"})
    return a, p


def top_pick(a, iid):
    for i, f in enumerate(K.faces(a.parts[iid].shape)):
        info = K.face_info(f)
        if "n" in info and info["n"][2] > 0.99 and abs(info["c"][2] - T) < 1e-6:
            return {"inst": iid, "kind": "face", "index": i, "click": None}
    raise AssertionError("no top face")


def world(a, iid, name):
    return a.world_frame(a.named_connector(iid, name))


@pytest.mark.parametrize("size", ["M3", "M5", "M6", "M8"])
@pytest.mark.parametrize("rim_from", ["hole_feature", "standard"])
def test_countersunk_screw_inserts_into_a_matching_countersunk_hole(size, rim_from):
    """Hole rim: the Hole feature's COUNTERSINK table, or dk_theo from the FreeCAD file iso10642def.csv."""
    rim = COUNTERSINK[size] if rim_from == "hole_feature" else UPSTREAM_CSK_DK[size]
    a, p = plate_doc(csk_plate(size, rim))
    plan = plan_insert(a, CSK, [top_pick(a, p)], {})
    assert [(g["size"], len(g["targets"])) for g in plan["groups"]] == [(size, 1)] and plan["unmatched"] == []
    g = plan["groups"][0]
    assert g["lengths"] and g["default_length"] in g["lengths"]
    res = apply_insert(a, plan, {size: g["lengths"][-1]}, {})
    assert len(res["instances"]) == 1 and len(res["mates"]) == 1 and a.errors == {}
    iid = res["instances"][0]
    head = world(a, iid, "under_head")
    assert np.allclose(head["o"], [CX, CY, T], atol=1e-6) and np.allclose(head["z"], [0, 0, -1], atol=1e-6)
    lo, hi = tight_bbox(a.world_shape(iid))
    assert hi[2] == pytest.approx(T, abs=1e-6) and lo[2] < T                    # head top flush, shank in the hole


def test_countersunk_screw_does_not_match_a_plain_or_unrelated_hole():
    """Rims: the Hole feature's COUNTERSINK table and dk_theo from iso10642def.csv."""
    p = part(CSK)
    assert p.match_hole(6.3) == "M3" and p.match_hole(6.72) == "M3" and p.match_hole(6.75) == "M3"
    assert p.match_hole(3.4) is None and p.match_hole(7.5) is None and p.match_hole(None) is None


def pin_plate(d):
    plate = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 40, 30, T).Shape()
    return K.cut(plate, BRepPrimAPI_MakeCylinderAt(d / 2, T + 2, (CX, CY, -1)))


@pytest.mark.parametrize("d", UPSTREAM_PINS)
def test_dowel_pin_matches_a_hole_of_its_diameter(d):
    """Diameters: FreeCAD Fasteners file iso8734def.csv (also upstream's list)."""
    a, p = plate_doc(pin_plate(d))
    plan = plan_insert(a, PIN, [top_pick(a, p)], {})
    assert [(g["size"], len(g["targets"])) for g in plan["groups"]] == [(pin_size(d), 1)]
    g = plan["groups"][0]
    assert g["default_length"] in g["lengths"] and g["default_length"] >= 1.5 * d
    res = apply_insert(a, plan, {}, {})
    assert len(res["mates"]) == 1 and a.errors == {}
    iid = res["instances"][0]
    top = world(a, iid, "top")
    assert np.allclose(top["o"], [CX, CY, T], atol=1e-6)
    lo, hi = tight_bbox(a.world_shape(iid))
    assert hi[2] == pytest.approx(T, abs=1e-6) and lo[2] == pytest.approx(T - g["default_length"], abs=1e-6)


def test_dowel_pin_hole_tolerance():
    """Diameters: FreeCAD Fasteners file iso8734def.csv; tolerance: hole_match.TOL."""
    p = part(PIN)
    assert p.match_hole(4.0) == "4 mm" and p.match_hole(4.04) == "4 mm" and p.match_hole(3.96) == "4 mm"
    assert p.match_hole(4.2) is None and p.match_hole(float("nan")) is None and p.match_hole("4") is None


# ------------------------------------------------------------------ the pane and the bill of materials
def test_both_are_listed_in_the_standard_parts_pane():
    """Types: the plugin manifest (plugin.json) through the registry."""
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    items = StandardPartsPane(A.AssemblyDoc()).type_items()
    assert CSK in items["Screws"] and PIN in items["Pins"]


def test_both_appear_in_the_bill_of_materials_with_a_designation():
    """Designations: the style of the other fasteners; the sizes are rows of the FreeCAD files."""
    a = A.AssemblyDoc()
    for _ in range(3):
        a.insert_standard_part(CSK, {"size": "M5", "length": 16})
    a.insert_standard_part(PIN, {"size": "4 mm", "length": 20})
    rows = a.bom()
    assert [(r["name"], r["qty"], r["source"]) for r in rows] == [
        ("ISO 10642 M5 x 16", 3, "Standard part"), ("ISO 8734 4 mm x 20", 1, "Standard part")]
    assert all(r["volume"] and r["volume"] > 0 for r in rows)


def test_mass_properties_of_the_screw_do_not_take_seconds():
    """The hex recess left the mean of the vertices on a symmetry plane, and the adaptive integrator took 5 s on
    this screw (kernel._gk_shape). The bound is far above the measured 0.01 s and below the 5 s it took."""
    import time
    shape = part(CSK).build({"size": "M4", "length": 12})
    t0 = time.time()
    mp = K.mass_props(shape)
    assert time.time() - t0 < 2.5
    assert mp["volume"] == pytest.approx(190.966417, rel=1e-6)
    assert mp["centroid"][2] == pytest.approx(-4.845897, abs=1e-5)
