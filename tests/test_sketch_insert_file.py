"""Insert DXF or SVG into a sketch (card I4.5): the sketch command, its options dialog and the entity guard.
Offscreen window; the file dialog and the options dialog's exec are stubbed, nothing modal opens."""
import io
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

from conftest import add, region_refs  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.ui import import_dialog as ID  # noqa: E402
from test_sketch_tool_keys import _sketching  # noqa: E402
from test_svgimport import _loop, svg  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
LOGO = svg(f'<path d="{_loop(100, 100, 60, 40)}{_loop(80, 100, 12, 10)}{_loop(120, 100, 10, 15)}"/>')
RECT = svg('<rect x="200" y="300" width="100" height="60"/>')
OPEN = svg('<path d="M0 0 L10 0 L10 10 L0 10 L0 0.5"/>')


def _file(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def _insert(s, path, tweak=None, accept=True):
    """Run insert_file with the options dialog stubbed: `tweak(dlg)` edits it, then it is accepted."""
    def run(dlg):
        if tweak:
            tweak(dlg)
        return accept and dlg.ok.isEnabled()
    s.exec_import_dialog = run
    s.insert_file(path)


def _box(sk):
    a = np.array([sk.P(p) for p in sk.points if p != "origin"])
    return a.min(axis=0), a.max(axis=0)


def _regions(sk):
    return K.sketch_regions(K.std_plane("Top"), sk.curves())


def test_logo_adds_entities_selects_them_and_makes_three_regions(tmp_path):
    w, s, vp = _sketching()
    _insert(s, _file(tmp_path, "logo.svg", LOGO))
    assert s.sk.entities and sorted(s.selection) == sorted(s.sk.entities)
    assert len(_regions(s.sk)) == 3
    assert "Inserted" in s.import_status and w.status.currentMessage() == s.import_status


def test_one_undo_removes_everything_redo_restores_and_no_constraints(tmp_path):
    w, s, vp = _sketching()
    _insert(s, _file(tmp_path, "logo.svg", LOGO))
    n, depth = len(s.sk.entities), len(s.undo_stack)
    assert depth == 1 and not s.sk.constraints
    s.undo()
    assert not s.sk.entities
    s.redo()
    assert len(s.sk.entities) == n and not s.sk.constraints


def test_fit_width_scale_and_file_origin(tmp_path):
    w, s, vp = _sketching()
    path = _file(tmp_path, "r.svg", RECT)
    _insert(s, path, lambda d: d.fit.setValue(50))
    lo, hi = _box(s.sk)
    assert hi[0] - lo[0] == pytest.approx(50) and lo[0] == pytest.approx(-25)

    w, s, vp = _sketching()
    _insert(s, path, lambda d: d.scale.setValue(2))
    lo, hi = _box(s.sk)
    assert hi[0] - lo[0] == pytest.approx(200)

    w, s, vp = _sketching()
    _insert(s, path, lambda d: d.use_origin.setChecked(True))
    lo, hi = _box(s.sk)
    assert lo[0] == pytest.approx(200) and hi[0] == pytest.approx(300)


def _dxf_two_layers():
    import ezdxf
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    doc.layers.add("B")
    msp = doc.modelspace()
    msp.add_circle((0, 0), 5, dxfattribs={"layer": "0"})
    msp.add_circle((20, 0), 3, dxfattribs={"layer": "B"})
    f = io.StringIO()
    doc.write(f)
    return f.getvalue()


def _untick(dlg, name):
    for it in dlg.layer_items():
        if it.data(Qt.UserRole) == name:
            it.setCheckState(Qt.Unchecked)


def test_unticked_dxf_layer_is_left_out_or_made_construction(tmp_path):
    path = _file(tmp_path, "a.dxf", _dxf_two_layers())
    w, s, vp = _sketching()
    _insert(s, path, lambda d: _untick(d, "B"))
    assert [e["type"] for e in s.sk.entities.values()] == ["circle"]
    w, s, vp = _sketching()

    def construction(d):
        _untick(d, "B")
        d.as_construction.setChecked(True)
    _insert(s, path, construction)
    flags = sorted(bool(e.get("construction")) for e in s.sk.entities.values())
    assert flags == [False, True]


def _zigzag(n):
    pts = " ".join(f"{i},{(i % 2) * 5}" for i in range(n + 1))
    return svg(f'<polyline points="{pts}"/>', 'width="10000mm" height="100mm" viewBox="0 0 10000 100"')


def test_guard_confirm_gates_ok_and_warn_shows_text(tmp_path):
    seen = {}
    w, s, vp = _sketching()

    def big(d):
        seen["big"] = (d.points, d.ok.isEnabled(), d.confirm.isHidden())
        d.confirm.setChecked(True)
        seen["ticked"] = d.ok.isEnabled()
    _insert(s, _file(tmp_path, "big.svg", _zigzag(ID.IMPORT_CONFIRM_POINTS + 100)), big, accept=False)
    pts, ok, hidden = seen["big"]
    assert pts > ID.IMPORT_CONFIRM_POINTS and not ok and not hidden and seen["ticked"]
    assert not s.sk.entities

    def mid(d):
        seen["mid"] = (d.warning.text(), d.ok.isEnabled(), d.confirm.isHidden())
    mid_n = (ID.IMPORT_WARN_POINTS + ID.IMPORT_CONFIRM_POINTS) // 2
    _insert(s, _file(tmp_path, "mid.svg", _zigzag(mid_n)), mid)
    text, ok, hidden = seen["mid"]
    assert "points" in text and ok and hidden
    assert s.sk.entities


def test_constants_match_the_benchmark_result_file():
    with open(os.path.join(ROOT, "scripts", "spikes", "op2_import_bench.result.txt")) as f:
        m = re.search(r"^DECISION import warn=(\d+) confirm=(\d+)", f.read(), re.M)
    assert (ID.IMPORT_WARN_POINTS, ID.IMPORT_CONFIRM_POINTS) == (int(m[1]), int(m[2]))


def test_near_closed_path_reports_two_open_ends(tmp_path):
    w, s, vp = _sketching()
    _insert(s, _file(tmp_path, "o.svg", OPEN))
    assert "2 open ends" in s.import_status and len(s.import_ends) == 2
    s.undo()
    assert not s.import_ends


def test_malformed_and_unknown_files_add_nothing(tmp_path):
    w, s, vp = _sketching()
    s.exec_import_dialog = lambda d: pytest.fail("no dialog for an unreadable file")
    s.insert_file(_file(tmp_path, "bad.svg", "<svg><path"))
    assert "Not a readable SVG" in s.import_status
    s.insert_file(_file(tmp_path, "bad.dxf", "not a dxf at all"))
    assert "Not a readable DXF" in s.import_status
    s.insert_file(_file(tmp_path, "pic.png", "x"))
    assert ".png" in s.import_status
    assert not s.sk.entities and not s.undo_stack


def test_dwg_gives_its_message(tmp_path):
    w, s, vp = _sketching()
    s.insert_file(_file(tmp_path, "plan.dwg", "AC1027"))
    assert s.import_status == "DWG is not supported - save it as DXF"
    assert not s.sk.entities


def test_finished_sketch_extrudes_to_a_solid(tmp_path):
    w, s, vp = _sketching()
    _insert(s, _file(tmp_path, "logo.svg", LOGO))
    w.finish_sketch()
    sid = w.doc.features[-1]["id"]
    big = max(K.face_info(r)["area"] for r in w.doc.state.sketches[sid].regions)
    refs = region_refs(w.doc, sid, lambda fi: fi["area"] == big)
    f = add(w.doc, "extrude", regions=refs, depth=5, op="new")
    assert not w.doc.errors.get(f["id"]) and w.doc.state.parts


def test_command_search_finds_it_only_while_sketching():
    from freeshape.ui.palette import search_commands
    w, s, vp = _sketching()
    cmds = w._palette_commands()
    for q in ("insert dxf or dwg", "logo", "import svg", "dwg"):
        assert "Insert DXF or SVG" in [c.name for c in search_commands(cmds, q)], q
    w.finish_sketch()
    assert "Insert DXF or SVG" not in [c.name for c in w._palette_commands()]
