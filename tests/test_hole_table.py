"""Hole size tables (I1.10): M1.6 to M24, and the original M2-M12 rows left untouched."""
import pytest

from freeshape import kernel as K
from freeshape import features as F
from freeshape.document import Document
from freeshape.sketch import Sketch

NEW = ["M1.6", "M14", "M16", "M18", "M20", "M22", "M24"]
OLD = ["M2", "M2.5", "M3", "M4", "M5", "M6", "M8", "M10", "M12"]
NO_COUNTERSINK = ["M18", "M22", "M24"]   # ISO 10642 stops at M20: no standard value

# Where the values come from:
#   COUNTERBORE  FreeCAD Resources/Hole/iso4762.json (socket head: diameter, depth)
#   COUNTERSINK  FreeCAD iso7046.json (M1.6) and iso10642.json (M14, M16, M20); none above M20
#   CLEARANCE    ISO 273 (close, normal, loose); FreeCAD's own values are in FeatureHole.cpp, not in the gate image
#   TAP_DRILL    nominal minus the ISO 261 coarse pitch

# Copied from the tables as they stood before I1.10.
OLD_CLEARANCE = {"M2": (2.2, 2.4, 2.6), "M2.5": (2.7, 2.9, 3.1), "M3": (3.2, 3.4, 3.6), "M4": (4.3, 4.5, 4.8),
                 "M5": (5.3, 5.5, 5.8), "M6": (6.4, 6.6, 7.0), "M8": (8.4, 9.0, 10.0), "M10": (10.5, 11.0, 12.0),
                 "M12": (13.0, 13.5, 14.5)}
OLD_TAP = {"M2": 1.6, "M2.5": 2.05, "M3": 2.5, "M4": 3.3, "M5": 4.2, "M6": 5.0, "M8": 6.8, "M10": 8.5, "M12": 10.2}
OLD_CBORE = {"M2": (4.4, 2.2), "M2.5": (5.5, 2.7), "M3": (6.5, 3.3), "M4": (8.0, 4.4), "M5": (10.0, 5.4),
             "M6": (11.0, 6.4), "M8": (15.0, 8.6), "M10": (18.0, 10.8), "M12": (20.0, 13.0)}
OLD_CSINK = {"M2": 4.4, "M2.5": 5.5, "M3": 6.3, "M4": 9.4, "M5": 10.4, "M6": 12.6, "M8": 17.3, "M10": 20.0,
             "M12": 24.0}


def test_every_size_in_every_table():
    assert sorted(HS := F.HOLE_SIZES, key=lambda s: float(s[1:])) == HS      # dialog list is in order
    assert HS == ["M1.6"] + OLD + NEW[1:]
    for s in HS:
        for t in (F.CLEARANCE, F.TAP_DRILL, F.COUNTERBORE):
            assert s in t, s
        assert (s in F.COUNTERSINK) == (s not in NO_COUNTERSINK), s
        assert s in K.ISO_PITCH, s


def test_existing_rows_unchanged():
    for s in OLD:
        assert F.CLEARANCE[s] == OLD_CLEARANCE[s]
        assert F.TAP_DRILL[s] == OLD_TAP[s]
        assert F.COUNTERBORE[s] == OLD_CBORE[s]
        assert F.COUNTERSINK[s] == OLD_CSINK[s]


@pytest.mark.parametrize("size", NEW)
def test_new_row_is_sane(size):
    nominal = float(size[1:])
    close, normal, loose = F.CLEARANCE[size]
    assert nominal < close < normal < loose
    assert abs(F.TAP_DRILL[size] - (nominal - K.ISO_PITCH[size])) <= 0.15
    assert F.COUNTERBORE[size][0] > normal and F.COUNTERBORE[size][1] > 0
    if size not in NO_COUNTERSINK:
        assert F.COUNTERSINK[size] > normal


@pytest.mark.parametrize("size", NO_COUNTERSINK)
def test_no_countersink_raises(size):
    for std in ("clearance", "tapped"):
        with pytest.raises(K.KernelError, match=f"No standard countersink for {size}"):
            F.hole_dims({"standard": std, "size": size, "style": "countersink"})
    assert F.hole_dims({"standard": "clearance", "size": size, "style": "counterbore"})[1] == F.COUNTERBORE[size]


def test_m16_normal_clearance_hole_diameter():
    doc = Document()
    s = Sketch()
    p = [s.add_point(0, 0), s.add_point(60, 0), s.add_point(60, 40), s.add_point(0, 40)]
    for a, b in zip(p, p[1:] + p[:1]):
        s.add_line(a, b)
    s.solve()
    sk = doc.make_feature("sketch"); sk["params"].update(plane={"kind": "std", "name": "Top"}, sketch=s.to_dict())
    doc.add_feature(sk)
    ex = doc.make_feature("extrude")
    ex["params"].update(regions=[F.region_ref(doc.state, sk["id"], doc.state.sketches[sk["id"]].regions[0])],
                        depth=10.0, op="new")
    doc.add_feature(ex)
    pid = next(iter(doc.state.parts))
    hs = Sketch(); hs.add_point_entity(hs.add_point(30, 20)); hs.solve()
    hf = doc.make_feature("sketch"); hf["params"].update(plane={"kind": "std", "name": "Top"}, sketch=hs.to_dict())
    doc.add_feature(hf)
    h = doc.make_feature("hole")
    h["params"].update(sketch=hf["id"], standard="clearance", size="M16", fit="normal", end="through", style="simple")
    doc.add_feature(h)
    assert not doc.errors, doc.errors
    radii = [K.face_info(f).get("r", 0) for f in K.faces(doc.state.parts[pid].shape)]
    assert any(abs(r - F.CLEARANCE["M16"][1] / 2) < 1e-6 for r in radii), radii
