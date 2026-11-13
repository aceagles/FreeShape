"""I1.5: hex bolt, hex screw, hex nut and plain washer generators against the FreeCAD goldens."""
import json
import os

import numpy as np
import pytest

from freeshape import kernel as K
from freeshape import pyfeature
from freeshape.pyfeature import registry  # noqa: F401  (loads the bundled plugins)

GOLDENS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "fasteners", "freecad_goldens.json")
TYPES = ("ISO4014", "ISO4017", "ISO4032", "ISO7089")
SCREWS = ("ISO4014", "ISO4017")


def part(type_key):
    cls = pyfeature.standard_part(f"sp:fasteners/{type_key.lower()}")
    assert cls is not None, pyfeature.problems()
    return cls()


def _entries():
    with open(GOLDENS) as f:
        data = json.load(f)["entries"]
    return [pytest.param(e, id=f"{t}-{e['diameter']}-{e['length']}") for t in TYPES for e in data[t]]


def params_of(entry):
    p = {"size": entry["diameter"]}
    if entry["length"] is not None:
        v = float(entry["length"])
        p["length"] = int(v) if v == int(v) else v
    return p


@pytest.mark.parametrize("entry", _entries())
def test_matches_freecad_golden(entry):
    shape = part(entry["type"]).build(params_of(entry))
    assert K.is_valid(shape)
    assert K.volume(shape) == pytest.approx(entry["volume"], rel=1e-3)
    lo, hi = K.bbox(shape)
    got = list(lo) + list(hi)
    for g, want in zip(got, entry["bbox"]):
        assert g == pytest.approx(want, abs=0.01)


@pytest.mark.slow
@pytest.mark.parametrize("type_key", TYPES)
def test_every_size_and_length_builds(type_key):
    p = part(type_key)
    for size in p.sizes():
        for length in (p.lengths(size) or [None]):
            params = {"size": size} if length is None else {"size": size, "length": length}
            shape = p.build(params)
            assert K.is_valid(shape), (type_key, params)
            assert K.volume(shape) > 0


@pytest.mark.parametrize("type_key", SCREWS)
def test_bolt_connectors(type_key):
    p = part(type_key)
    size = "M6"
    length = p.lengths(size)[3]
    params = {"size": size, "length": length}
    c = p.connectors(params)
    uh, tip = np.array(c["under_head"]["o"]), np.array(c["tip"]["o"])
    assert np.linalg.norm(uh - tip) == pytest.approx(length, abs=1e-9)
    assert np.allclose(c["under_head"]["z"], c["tip"]["z"])
    z = np.array(c["under_head"]["z"])
    shape = p.build(params)
    assert K.material_at(shape, uh + 1.0 * z)
    lo, hi = K.bbox(shape)
    assert tip[2] == pytest.approx(lo[2], abs=1e-6)         # the tip is the end of the shank
    assert p.insert_connector == "under_head"
    assert p.designation(params) == f"ISO {type_key[3:]} M6 x {length}"


@pytest.mark.parametrize("type_key,thick_col", (("ISO4032", 5), ("ISO7089", 2)))
def test_flat_connectors(type_key, thick_col):
    from freeshape.addons.fasteners import fastener_base as fb
    p = part(type_key)
    for size in ("M3", "M6", "M20"):
        c = p.connectors({"size": size})
        t = float(fb.dim_row(type_key, size)[thick_col])
        assert np.linalg.norm(np.array(c["top"]["o"]) - np.array(c["bottom"]["o"])) == pytest.approx(t, abs=1e-9)
        assert c["bottom"]["z"] == [0.0, 0.0, -1.0] and c["top"]["z"] == [0.0, 0.0, 1.0]
        lo, hi = K.bbox(p.build({"size": size}))
        assert hi[2] - lo[2] == pytest.approx(t, abs=1e-6)
    assert p.insert_connector == "bottom" and p.lengths("M6") == []
    assert p.designation({"size": "M6"}) == f"ISO {type_key[3:]} M6"


def test_shared_rules_follow_screwmaker():
    from freeshape.addons.fasteners import fastener_base as fb
    assert fb.all_diams("ISO4017")[:3] == ["M1.6", "M2", "M2.5"]
    assert fb.all_lengths("ISO4014", "M1.6") == ["12", "16"]          # range table limits the length table
    assert fb.nominal_lengths("ISO4017", "M1.6")[0] == 2
    assert fb.thread_length("ISO4017", "M6") == 10.0                  # no thr_len column: the default


def test_validate_names_bad_key():
    p = part("ISO4017")
    for params, key in (({"size": "M99", "length": 20}, "size"), ({"size": "M6", "length": 21}, "length"),
                        ({"size": "M6", "length": True}, "length")):
        with pytest.raises(K.KernelError, match=key):
            p.validate(params)
    with pytest.raises(K.KernelError, match="size"):
        part("ISO7089").validate({"size": "M99"})


def test_categories_and_no_registry_problems():
    for tid, cat in (("iso4014", "Screws"), ("iso4017", "Screws"), ("iso4032", "Nuts"), ("iso7089", "Washers")):
        cls = pyfeature.standard_part(f"sp:fasteners/{tid}")
        assert cls is not None and cls.category == cat, tid
    assert pyfeature.problems() == []
