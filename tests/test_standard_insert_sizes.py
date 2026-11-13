"""standard_insert.sizes_for (I3.2)."""
import pytest

from freeshape import standard_insert as si
from freeshape.pyfeature import registry  # noqa: F401


def tgt(i, d):
    return {"inst": "a", "edge": i, "diameter": d, "centre": (float(i), 0.0, 0.0)}


def test_groups_and_unmatched():
    ts = [tgt(i, 3.4) for i in range(8)] + [tgt(10 + i, 4.5) for i in range(4)] + [tgt(20, 1.0)]
    r = si.sizes_for("sp:fasteners/iso4762", ts)
    assert [(g["size"], len(g["targets"])) for g in r["groups"]] == [("M3", 8), ("M4", 4)]
    assert r["unmatched"] == [ts[-1]]
    assert r["groups"][0]["targets"] == ts[:8]


def test_group_order_is_first_appearance():
    ts = [tgt(0, 4.5), tgt(1, 3.4), tgt(2, 4.5), tgt(3, 7.7)]
    r = si.sizes_for("sp:fasteners/iso4762", ts)
    assert [g["size"] for g in r["groups"]] == ["M4", "M3"]
    assert [t["edge"] for t in r["groups"][0]["targets"]] == [0, 2]
    assert r["unmatched"] == [ts[3]]


def test_empty_and_unknown():
    assert si.sizes_for("sp:fasteners/iso4762", []) == {"groups": [], "unmatched": []}
    with pytest.raises(KeyError):
        si.sizes_for("sp:fasteners/nope", [])


def test_tapping_screw_groups():
    r = si.sizes_for("sp:fasteners/iso7049", [tgt(0, 3.2), tgt(1, 2.3), tgt(2, 2.0)])
    assert [g["size"] for g in r["groups"]] == ["ST2.9", "ST2.2"]
    assert len(r["unmatched"]) == 1
