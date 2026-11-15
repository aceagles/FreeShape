"""I2.3: geometry code that assumed a solid keeps surface bodies: split, piece side, section view, patterns,
saved copies and the engine's geometry hash. Also what happens to a surface part on the engine wire.

No feature makes a surface yet, so the shapes here are built by hand (as in test_surface_bodies.py)."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))

from OCP.BRep import BRep_Builder  # noqa: E402
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace  # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: E402
from OCP.TopoDS import TopoDS_Shell  # noqa: E402
from OCP.gp import gp_Pln, gp_Pnt, gp_Dir  # noqa: E402

from conftest import EXAMPLES_DIR  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402

# geometry_hash of a 10 x 10 x 10 box at the origin as it was before I2.3
BOX_HASH = "cabd8c922d572b326dcc99c35ee49f2ca84b14806d61aff981a14624e676b592"


def box(x=0.0, y=0.0, z=0.0, w=10.0, d=10.0, h=10.0):
    return BRepPrimAPI_MakeBox(gp_Pnt(x, y, z), w, d, h).Shape()


def shell_of(*faces):
    b = BRep_Builder()
    sh = TopoDS_Shell()
    b.MakeShell(sh)
    for f in faces:
        b.Add(sh, f)
    return sh


def front_face(shape):
    """The face of a box at y = min (normal -Y)."""
    return next(f for f in K.faces(shape) if abs(K.face_info(f).get("n", [0, 0, 0])[1] + 1) < 1e-9)


def sheet(x=0.0, w=10.0):
    """An open surface body: the front face of a box (w wide, 10 high), as a shell."""
    return shell_of(front_face(box(x, 0, 0, w, 10, 10)))


def area(shape):
    return K.mass_props(shape)["area"]


def plane_face(origin, normal, size=100.0):
    pl = gp_Pln(gp_Pnt(*origin), gp_Dir(*normal))
    return BRepBuilderAPI_MakeFace(pl, -size, size, -size, size).Face()


# ---------------------------------------------------------------------------- patterns and mirror

def test_linear_pattern_of_a_surface_part_makes_three_surface_parts():
    st = F.ModelState()
    pid = st.new_part(sheet(), "x", surface=True).id
    F.run_linear_pattern(st, {"id": "P", "params": {"parts": [pid], "count": 3, "spacing": 20,
                                                    "direction": {"kind": "std", "name": "X"}}})
    assert len(st.parts) == 3
    assert all(K.is_surface(p.shape) and p.name.startswith("Surface") for p in st.parts.values())
    xs = sorted(round(float(K.bbox(p.shape)[0][0]), 6) for p in st.parts.values())
    assert xs == [0.0, 20.0, 40.0]


def test_circular_pattern_of_a_surface_part_keeps_the_copies():
    st = F.ModelState()
    pid = st.new_part(sheet(x=20), "x", surface=True).id
    F.run_circular_pattern(st, {"id": "P", "params": {"parts": [pid], "count": 4, "angle": 360,
                                                      "axis": {"kind": "std", "name": "Z"}}})
    assert len(st.parts) == 4 and all(K.is_surface(p.shape) for p in st.parts.values())


def test_mirror_of_a_surface_part_makes_two_surface_parts():
    st = F.ModelState()
    pid = st.new_part(sheet(x=5), "x", surface=True).id
    F.run_mirror(st, {"id": "M", "params": {"parts": [pid], "plane": {"kind": "std", "name": "Right"},
                                            "op": "add"}})
    assert len(st.parts) == 2
    assert all(K.is_surface(p.shape) for p in st.parts.values())
    lo = sorted(round(float(K.bbox(p.shape)[0][0]), 6) for p in st.parts.values())
    assert lo == [-15.0, 5.0]


# ---------------------------------------------------------------------------- split

def test_split_with_on_a_solid_and_a_shell_returns_both_bodies():
    solid = box(0, 0, 0)
    surf = sheet(x=30)
    comp = K.compound([solid, surf])
    tool = plane_face((5, 0, 0), (1, 0, 0))              # cuts the solid at x = 5 (the sheet is at x 30..40)
    pieces = K.split_with(comp, [tool])
    assert len(K.solids(K.compound(pieces))) == 2
    assert len([p for p in pieces if K.is_surface(p)]) == 1


def test_split_with_cuts_a_shell_into_two_shells():
    surf = sheet(x=0, w=20)
    pieces, hist = K.split_with(surf, [plane_face((10, 0, 0), (1, 0, 0))], with_history=True)
    assert len(pieces) == 2 and all(K.is_surface(p) for p in pieces)
    assert abs(sum(area(p) for p in pieces) - 200.0) < 1e-6


def test_piece_side_gives_a_sign_for_a_shell_piece():
    surf = sheet(x=0, w=20)
    tool = plane_face((10, 0, 0), (1, 0, 0))
    pieces = K.split_with(surf, [tool])
    sides = sorted(K.piece_side(p, tool) for p in pieces)
    assert sides[0] < 0 < sides[1]
    # the piece on the + side is the one with larger x
    plus = max(pieces, key=lambda p: K.piece_side(p, tool))
    assert K.bbox(plus)[0][0] > 9.0


# ---------------------------------------------------------------------------- section view

def test_section_cut_keeps_the_surface_faces_on_the_kept_side():
    solid = box(0, 0, 0)
    surf = sheet(x=20, w=20)                              # x 20..40, 20 x 10 = 200 in area
    kept = K.section_cut(K.compound([solid, surf]), (30, 0, 0), (1, 0, 0))   # keep x < 30
    bs = K.bodies(kept)
    assert len(K.solids(kept)) == 1
    loose = [b for b in bs if K.is_surface(b)]
    assert len(loose) == 1 and abs(area(loose[0]) - 100.0) < 1e-6
    assert K.bbox(loose[0])[1][0] < 30 + 1e-6


def test_section_cut_drops_a_surface_wholly_on_the_cut_away_side():
    kept = K.section_cut(K.compound([box(0, 0, 0), sheet(x=50)]), (30, 0, 0), (1, 0, 0))
    assert not [b for b in K.bodies(kept) if K.is_surface(b)]


def test_section_cut_of_solids_alone_is_as_before():
    kept = K.section_cut(box(0, 0, 0), (5, 0, 0), (1, 0, 0))
    assert len(K.solids(kept)) == 1 and abs(K.volume(kept) - 500.0) < 1e-6


# ---------------------------------------------------------------------------- saved copy

def test_a_surface_part_is_saved_and_restored_as_a_copy():
    st = F.ModelState()
    st.new_part(sheet(), "F9", surface=True)
    rec = F.saved_copy_record(st, "F9", "key")
    assert rec is not None, "a feature whose result is one surface body is cached"
    entry, blobs = rec
    st2 = F.ModelState()
    F.restore_saved_copy(st2, {"id": "F9"}, entry, lambda k: blobs[k])
    part = st2.parts["F9/1"]
    assert K.is_surface(part.shape) and abs(area(part.shape) - 100.0) < 1e-6


def test_a_part_of_two_bodies_is_still_not_cached():
    st = F.ModelState()
    st.new_part(K.compound([sheet(), sheet(x=50)]), "F9", surface=True)
    assert F.saved_copy_record(st, "F9", "key") is None


# ---------------------------------------------------------------------------- geometry hash

def test_geometry_hash_of_a_solid_only_studio_is_unchanged():
    from freeshape.ui.partstudio_engine import geometry_hash
    assert geometry_hash(box()) == BOX_HASH
    assert geometry_hash(K.compound([box(), box(30, 0, 0)])) == geometry_hash(K.compound([box(), box(30, 0, 0)]))


def test_geometry_hash_changes_when_a_surface_area_changes():
    from freeshape.ui.partstudio_engine import geometry_hash
    a = geometry_hash(K.compound([box(), sheet(x=30, w=10)]))
    b = geometry_hash(K.compound([box(), sheet(x=30, w=12)]))
    assert a != b and a != geometry_hash(box())


def test_geometry_hash_tells_two_surfaces_of_equal_bbox_apart_by_face_count():
    from freeshape.ui.partstudio_engine import geometry_hash
    f1 = front_face(box(30, 0, 0))
    two = shell_of(f1, next(f for f in K.faces(box(30, 0, 0)) if abs(K.face_info(f).get("n", [0, 0, 0])[2] - 1) < 1e-9))
    assert geometry_hash(K.compound([box(), shell_of(f1)])) != geometry_hash(K.compound([box(), two]))


# ---------------------------------------------------------------------------- the engine wire

def _surface_outputs():
    """StudioOutputs from the in-process engine for a studio that also holds a surface part, as the worker
    would send it, after the wire round trip."""
    from freeshape.engine import InProcessEngine
    from freeshape.engine.wire import from_wire, to_wire
    with InProcessEngine() as e:
        tab = e.open_tab(os.path.join(EXAMPLES_DIR, "bracket.fshape"))
        st = e._tabs[tab].doc.state
        st.new_part(sheet(x=500), "S1", surface=True)
        st.new_part(sheet(x=600), "S2", surface=True)
        assert (st.part_counter, st.surface_counter) == (1, 2)
        out = e._tabs[tab].outputs()
        return from_wire(to_wire(out)), len(st.parts)


@pytest.fixture(scope="module")
def win():
    os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")
    os.environ.setdefault("FREESHAPE_NO_HOME", "1")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow(engine=True)
    w.settings = type("S", (dict,), {"value": lambda s, k, d=None: s.get(k, d),
                                     "setValue": lambda s, k, v: s.__setitem__(k, v)})()
    w.open_any(os.path.join(EXAMPLES_DIR, "bracket.fshape"))
    assert w.studio_engine.settle(180)
    yield w
    w.studio_engine.shutdown()
    from freeshape.engine import process as P
    P.SPARES.clear()


def test_a_surface_part_crosses_the_engine_wire_to_a_window_side_part(win):
    out, n = _surface_outputs()
    se = win.studio_engine
    doc = win.active_doc() if hasattr(win, "active_doc") else next(iter(se._recs.values())).doc
    rec = se._recs[id(doc)]
    assert len(out.parts) == n, "no part is filtered out of the outputs for having no solid"
    res = se._decode(rec, rec.want, out)
    se._apply(rec, res)
    surf = [p for p in doc.state.parts.values() if p.name.startswith("Surface")]
    assert sorted(p.name for p in surf) == ["Surface 1", "Surface 2"]
    assert all(K.is_surface(p.shape) for p in surf), "the window-side shape is the worker's shape, not a mesh"
    assert all(not K.is_surface(p.shape) for p in doc.state.parts.values() if p not in surf)
    assert doc.state.surface_counter == 0          # the window's state does not count: names come over the wire
    shown = [p for p in out.parts if p.name.startswith("Surface")]
    assert all(p.mesh is not None and len(p.mesh.tris) for p in shown)
    se._request(rec)                                  # put the studio back as the worker has it
    assert se.settle(120)
