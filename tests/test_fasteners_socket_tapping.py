"""I1.6: socket head cap screw (ISO 4762) and ISO 7049 tapping screw against the FreeCAD goldens."""
import json
import os

import numpy as np
import pytest

from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib

from freeshape import kernel as K
from freeshape import pyfeature
from freeshape.pyfeature import registry  # noqa: F401  (loads the bundled plugins)
from freeshape.plugins.fasteners import tables as tbl

GOLDENS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "fasteners", "freecad_goldens.json")
TYPES = ("ISO4762", "ISO7049-C", "ISO7049-F", "ISO7049-R")


def tight_bbox(shape):
    """The exact bounding box. K.bbox is the quick one: on a B-spline head surface it is up to 0.15 mm too
    high on OCP 8.0 and exact on 7.9, so the tapping screw is compared on the tight box, which is the same
    on both versions and within 0.001 mm of FreeCAD's."""
    b = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, b, False, False)
    return np.array(b.CornerMin().Coord()), np.array(b.CornerMax().Coord())


def part(tid, **kw):
    cls = pyfeature.standard_part(f"sp:fasteners/{tid}")
    assert cls is not None, pyfeature.problems()
    return cls(**kw)


def _entries():
    with open(GOLDENS) as f:
        data = json.load(f)["entries"]
    return [pytest.param(e, id=f"{t}-{e['diameter']}-{e['length']}") for t in TYPES for e in data[t]]


def part_and_params(entry):
    v = float(entry["length"])
    # the loader drops the brackets that mark a non-preferred size, "(M14)" -> "M14"
    params = {"size": entry["diameter"].strip("()"), "length": int(v) if v == int(v) else v}
    if entry["type"] == "ISO4762":
        return part("iso4762"), params
    params["point"] = entry["type"][-1]
    return part("iso7049"), params


@pytest.mark.parametrize("entry", _entries())
def test_matches_freecad_golden(entry):
    p, params = part_and_params(entry)
    shape = p.build(params)
    assert K.is_valid(shape)
    assert K.volume(shape) == pytest.approx(entry["volume"], rel=1e-3)
    # ISO 4762 has no B-spline: its quick box is the same on both OCP versions and equals FreeCAD's
    # (the tight box would be 0.12 to 4 mm smaller than FreeCAD's quick one on the torus head edge).
    lo, hi = K.bbox(shape) if entry["type"] == "ISO4762" else tight_bbox(shape)
    got = list(lo) + list(hi)
    for g, want in zip(got, entry["bbox"]):
        assert g == pytest.approx(want, abs=0.01)


def _every(p, **kw):
    for size in p.sizes(**kw):
        for length in p.lengths(size, **kw):
            params = {"size": size, "length": length, **kw}
            shape = p.build(params)
            assert K.is_valid(shape), params
            assert K.volume(shape) > 0


@pytest.mark.slow
def test_every_iso4762_size_and_length_builds():
    _every(part("iso4762"))


@pytest.mark.slow
@pytest.mark.parametrize("point", ("C", "F", "R"))
def test_every_iso7049_size_and_length_builds(point):
    _every(part("iso7049"), point=point)


@pytest.mark.parametrize("tid,params", (("iso4762", {"size": "M6", "length": 20}),
                                        ("iso7049", {"size": "ST4.8", "length": 19, "point": "C"})))
def test_screw_connectors(tid, params):
    p = part(tid)
    c = p.connectors(params)
    uh, tip = np.array(c["under_head"]["o"]), np.array(c["tip"]["o"])
    assert np.linalg.norm(uh - tip) == pytest.approx(params["length"], abs=1e-9)
    assert c["under_head"]["z"] == c["tip"]["z"] == [0.0, 0.0, -1.0]
    shape = p.build(params)
    assert K.material_at(shape, uh + np.array([0, 0, -1.0]))
    lo, _ = tight_bbox(shape)
    assert tip[2] == pytest.approx(lo[2], abs=1e-6)
    assert p.insert_connector == "under_head"


def test_designations_and_parameters():
    assert part("iso4762").designation({"size": "M3", "length": 10}) == "ISO 4762 M3 x 10"
    p = part("iso7049")
    assert p.designation({"size": "ST2.9", "length": 9.5, "point": "C"}) == "ISO 7049 ST2.9 x 9.5 C"
    assert p.designation({"size": "ST2.9", "length": 9.5}) == "ISO 7049 ST2.9 x 9.5 C"
    assert p.default_params()["point"] == "C" and p.default_params()["standard"] == "iso"
    assert p.validate({"size": "ST2.9", "length": 9.5})["point"] == "C"
    for params, key in (({"size": "ST2.9", "length": 9.5, "point": "X"}, "point"),
                        ({"size": "ST2.9", "length": 9.5, "standard": "din"}, "standard"),
                        ({"size": "ST2.9", "length": 9.5, "standard": "commercial"}, "size"),
                        ({"size": "ST2.2", "length": 9.5, "point": "R"}, "size"),    # R starts at ST3.5
                        ({"size": "ST2.9", "length": 10}, "length")):
        with pytest.raises(K.KernelError, match=key):
            p.validate(params)


def test_commercial_hidden_without_rows():
    p = part("iso7049")
    assert p.standards() == ["iso"] and p.sizes(standard="commercial") == []


# TEST DATA, not shipped: one invented commercial row.
_ROW = ('"M2.3",2.8,4.5,4.2,1.8,1.6,0.1,3.4,"1",2.2,1.3,0.9,0.4,1.7,1.5,1.8,"4.5;6;9.5"')


@pytest.fixture
def commercial_tables(tmp_path):
    src = os.path.join(tbl.COMMERCIAL_DIR, "tapping_screw.csv")
    with open(src, encoding="utf-8") as f:
        text = f.read().rstrip("\n") + "\n" + _ROW + "\n"
    folder = tmp_path / "commercial"
    folder.mkdir()
    (folder / "tapping_screw.csv").write_text(text, encoding="utf-8")
    return tbl.load_tables(commercial_dir=str(folder))


@pytest.mark.parametrize("point", ("C", "F", "R"))
def test_commercial_row_builds(commercial_tables, point):
    p = part("iso7049", tables=commercial_tables)
    assert p.standards() == ["iso", "commercial"]
    assert p.sizes(standard="commercial") == ["M2.3"]
    assert p.lengths("M2.3", standard="commercial") == [4.5, 6, 9.5]
    params = {"size": "M2.3", "length": 6, "point": point, "standard": "commercial"}
    shape = p.build(params)
    assert K.is_valid(shape)
    lo, hi = tight_bbox(shape)
    assert hi[0] - lo[0] == pytest.approx(4.5, abs=0.01)        # dk_max, the column makeISO7049 reads as D
    assert hi[1] - lo[1] == pytest.approx(4.5, abs=0.01)
    assert p.designation(params) == "Tapping screw M2.3 x 6 (commercial)"
    # no global state left behind
    assert part("iso7049").standards() == ["iso"]


def test_categories_and_no_registry_problems():
    for tid in ("iso4762", "iso7049"):
        cls = pyfeature.standard_part(f"sp:fasteners/{tid}")
        assert cls is not None and cls.category == "Screws", tid
    assert pyfeature.problems() == []
