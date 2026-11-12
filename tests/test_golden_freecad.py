"""FreeCAD kernel-stress shapes (tests/data/freecad, licences in NOTICE.md): import, validity, topology
fingerprints, tessellation and a few modelling operations.

Goldens (tests/golden/freecad.json) were recorded on cadquery-ocp 7.9.3.1.1 and also pass on 8.0.1.
The default K.cut (which runs clean()) is deliberately not exercised here; the cut cases use check=True.
Known render bugs on these shapes are pinned in test_known_bugs.py.
"""
import base64

import numpy as np
import pytest

from conftest import add, assert_golden, shape_fingerprint
from freecad_shapes import (CASES, data_path, fillet_choice, fingerprint, half_box, load, mesh, open_mesh_edges,
                            referenced_normals)
from freeshape import kernel as K
from freeshape.document import Document


def _ids(cases):
    return [c.id for c in cases]


def _with(op):
    return [c for c in CASES if op in c.ops]


# ----------------------------------------------------------------------------- import / validity

@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_import_counts(case):
    shape = load(case)
    assert not shape.IsNull()
    assert len(K.solids(shape)) == case.solids
    fp = fingerprint(case)
    assert (fp["solids"], fp["shells"]) == (case.solids, case.shells)
    assert fp["faces"] > 0


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_validity_matches_expectation(case):
    assert K.is_valid(load(case)) is case.valid


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_fingerprint_golden(case):
    assert_golden(f"freecad/{case.id}", fingerprint(case), case.tol)


# ----------------------------------------------------------------------------- Schenkel through both STEP paths

def test_schenkel_import_step_with_colors():
    items = K.import_step_with_colors(data_path("step/Schenkel.stp"))
    assert [name for _, name, _, _ in items] == ["Schenkel"]
    assert K.is_valid(items[0][0])


def test_schenkel_part_studio_import_feature():
    with open(data_path("step/Schenkel.stp"), "rb") as fh:
        data = base64.b64encode(fh.read()).decode()
    doc = Document()
    add(doc, "import", data=data, filename="Schenkel.stp")
    parts = list(doc.state.parts.values())
    assert [p.name for p in parts] == ["Schenkel"]
    direct = fingerprint(next(c for c in CASES if c.id == "schenkel"))
    via_feature = shape_fingerprint(parts[0].shape)
    assert via_feature["faces"] == direct["faces"] == 409
    assert via_feature["valid"] and via_feature["volume"] == pytest.approx(direct["volume"], rel=1e-6)


# ----------------------------------------------------------------------------- render

@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_tessellation_is_sane(case):
    m = mesh(case)
    assert not np.isnan(m.verts).any() and not np.isnan(m.normals).any()
    if len(m.tris):
        assert np.abs(referenced_normals(m) - 1).max() < 1e-3
        assert m.tris.max() < len(m.verts)
    if not case.empty_face_ok:        # countersink_cone: see test_known_bugs.py
        empty = [i for i, (a, b) in enumerate(m.face_ranges) if a == b]
        assert not empty, f"faces with no triangles: {empty}"


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_triangle_count_is_stable(case):
    n = len(mesh(case).tris)
    assert abs(n - case.tris) <= 0.1 * case.tris, f"{n} triangles, stored {case.tris}"


@pytest.mark.parametrize("case", [c for c in CASES if c.solids and c.watertight], ids=lambda c: c.id)
def test_closed_solid_meshes_watertight(case):
    assert open_mesh_edges(mesh(case), load(case)) == 0      # cylinder7_v2 and cylinder_seam10: test_known_bugs.py


# ----------------------------------------------------------------------------- operations

@pytest.mark.parametrize("case", _with("cut"), ids=_ids(_with("cut")))
def test_half_box_cut_checked(case):
    shape = load(case)
    out = K.cut(shape, half_box(shape), check=True)
    assert K.is_valid(out) and K.solids(out)
    assert_golden(f"freecad/{case.id}__cut", shape_fingerprint(out), case.tol)


def test_unorientable_solid_fuses_with_a_distant_copy():
    case = next(c for c in CASES if c.id == "unorientable_solid")
    shape = load(case)
    out = K.fuse(shape, K.translate(shape, [1000, 0, 0]))
    assert len(K.solids(out)) == 2
    assert K.volume(out) == pytest.approx(2 * 87432.002, abs=0.1)


@pytest.mark.parametrize("case", _with("fillet"), ids=_ids(_with("fillet")))
def test_fillet_longest_convex_edge(case):
    shape = load(case)
    pick = fillet_choice(shape)
    assert pick is not None, "no straight convex edge between planar faces"
    edge, radius = pick
    out = K.fillet(shape, [edge], radius)
    assert K.is_valid(out)
    assert abs(K.rough_volume(out) - K.rough_volume(shape)) < 0.2 * K.rough_volume(shape)
