"""The Fasteners plugin's dimension tables and their loader."""
import filecmp
import os

import pytest

from freeshape import pyfeature
from freeshape.addons.fasteners import tables as T
from freeshape.addons.fasteners.geom import DiaStr2Num

UPSTREAM = os.path.join(os.path.dirname(__file__), "data", "fasteners", "upstream", "FsData")


def _upstream_cell(csv_name, key, title):
    """Read a cell straight from an upstream CSV without using the loader under test."""
    import csv
    with open(os.path.join(UPSTREAM, csv_name), newline="") as fp:
        rows = [r for r in csv.reader(fp, skipinitialspace=True)]
    head = next(r for r in rows if r and r[0] == "Dia")
    row = next(r for r in rows if r and r[0] == key)
    return float(row[head.index(title)])


def test_iso4762_m3_head_diameter_matches_upstream_csv():
    t = T.tables()["ISO4762def"]
    assert "M3" in t.rows
    assert t.column("M3", "dk_max") == _upstream_cell("iso4762def.csv", "M3", "dk_max") == 5.5
    assert list(t.rows)[:3] == ["M1.6", "M2", "M2.5"]                      # file order
    assert T.tables().fs_data()["ISO4762def"]["M3"][0] == 0.5              # positional row: P first


def test_multi_name_table_reachable_under_each_name():
    tb = T.tables()
    c, f = tb["ISO7049-Cdef"], tb["ISO7049-Fdef"]
    assert c is f and "ST2.2" in c.rows
    assert tb["ISO7049-Rdef"] is not c
    assert tb["ISO7049-Clength"] is tb["ISO7049-Rlength"]
    assert tb["ISO7049-Crange"] is tb["ISO7049-Frange"] is tb["ISO7049-Rrange"]
    assert c.titles[0] == "da_max" and c.key_title == "Dia"


def test_bracketed_size_loads_under_bare_name():
    t = T.tables()["ISO4014def"]
    assert "M3.5" in t.rows and "(M3.5)" not in t.rows
    assert "M3.5" in t.nonpreferred and "M3" not in t.nonpreferred


def test_file_without_label_line_takes_file_stem_and_types():
    tb = T.tables()
    for name in ("DiaList", "iso1478def", "iso4757def"):
        assert name in tb
    assert tb["DiaList"].rows["#6"] == (3.5052,)
    assert isinstance(tb["ISO68-1def"].rows["0.5"][0], float) and isinstance(next(iter(tb["ISO68-1def"].rows)), str)
    assert tb["ISO7049-Cdef"].column("ST2.9", "PH") == "1"                 # quoted cell stays a string
    assert tb["iso1478def"].column("ST1.5", "r") == ""                     # empty cell
    assert DiaStr2Num("#6", tb["DiaList"].rows) == 3.5052                  # feeds geom.py unchanged


def test_every_first_release_table_loads():
    tb = T.tables()
    for stem in ("ISO4014", "ISO4017", "ISO4762"):
        for suffix in ("def", "length", "range"):
            assert tb[stem + suffix].rows
    for name in ("ISO4032def", "ISO7089def", "ISO68-1def", "ISO68-2def", "iso1478def", "iso4757def", "DiaList"):
        assert tb[name].rows
    for s in "CFR":
        assert tb[f"ISO7049-{s}def"].rows and tb[f"ISO7049-{s}length"].rows and tb[f"ISO7049-{s}range"].rows


def test_vendored_copies_are_identical_to_upstream():
    names = sorted(os.listdir(UPSTREAM))
    assert names == sorted(f for f in os.listdir(T.DATA_DIR) if f.endswith(".csv"))
    match, mismatch, errors = filecmp.cmpfiles(UPSTREAM, T.DATA_DIR, names, shallow=False)
    assert not mismatch and not errors


def test_blank_line_ends_a_table(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text('"A"\n"K","v"\n"a",1\n\n"B"\n"K","w"\n"b",2\n')
    a, b = T.parse_csv(str(p))
    assert list(a.rows) == ["a"] and list(b.rows) == ["b"]
    p.write_text('"A"\n"K","v"\n"a",1\n\n"c",3\n')
    with pytest.raises(ValueError):
        T.parse_csv(str(p))


def test_commercial_csv_is_merged_under_standard_commercial(tmp_path):
    (tmp_path / "x.csv").write_text(
        '"commercial_test"\n"Dia","P","lengths"\n"ST3.0",1.2,"6.5;9.5"\n')
    tb = T.load_tables(commercial_dir=str(tmp_path))
    t = tb.standard("commercial")["commercial_test"]
    assert t.standard == "commercial" and list(t.rows) == ["ST3.0"]
    assert T.split_lengths(t.column("ST3.0", "lengths")) == ["6.5", "9.5"]
    assert "commercial_test" not in tb.standard("workbench")
    assert tb["ISO4762def"].standard == "workbench"


def test_commercial_name_clash_is_rejected(tmp_path):
    (tmp_path / "x.csv").write_text('"ISO4762def"\n"Dia","P"\n"M3",1\n')
    with pytest.raises(ValueError):
        T.load_tables(commercial_dir=str(tmp_path))


def test_shipped_commercial_file_is_header_only():
    com = T.tables().standard("commercial")
    assert list(com) == ["commercial_tapping_screw"]
    t = com["commercial_tapping_screw"]
    assert not t.rows
    assert t.titles[-5:] == ("P", "d2", "d3", "rR", "lengths")
    assert t.titles[:-5] == T.tables()["ISO7049-Cdef"].titles


def test_plugin_loads_without_problems():
    info = pyfeature.plugin("fasteners")
    assert info is not None and info.source == "bundled" and info.licence == "LGPL-2.1-or-later"
    assert [p for p in pyfeature.problems() if "fasteners" in p[0]] == []
    assert pyfeature.problems() == []


def test_third_party_notice_names_workbench():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    text = open(os.path.join(root, "THIRD-PARTY-NOTICES.txt"), encoding="utf-8").read()
    assert "FreeCAD_FastenersWB" in text and "LGPL" in text and "https://github.com/shaise/FreeCAD_FastenersWB" in text
