"""Features-mode patterns and mirror name the faces each copy adds (I1.2): the copy's index is in the tag
(`<feature id>_<k>`, `_<k1>_<k2>` for a second direction), as a whole-part pattern's copies are, so a
reference to a face of copy 3 still means copy 3 after the count changes
(docs/reference/element-naming.md, I1.10)."""
import os
import sys

from OCP.GeomAbs import GeomAbs_Cone  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape import kernel as K  # noqa: E402

from conftest import add, block_doc, edge_refs, only_part, region_refs, sketch_feature  # noqa: E402

RIGHT = {"kind": "std", "name": "Right"}


def hole_doc(x, y, w=200.0, d=60.0, x0=0.0, y0=0.0):
    """A plate with one through hole at (x, y)."""
    doc = block_doc(w=w, d=d, h=10.0, x0=x0, y0=y0)
    sid = sketch_feature(doc, lambda s: s.add_circle(s.add_point(x, y), 3.0))
    hole = add(doc, "extrude", regions=region_refs(doc, sid), end="through", op="remove")
    return doc, hole


def wall_names(doc):
    """{(x, y) of a hole wall's centre: its name}: one entry per cylindrical face of the part."""
    part = only_part(doc)
    named = doc.state.named(part.id)
    assert named is not None, "the part has no element map"
    out = {}
    for f in K.faces(part.shape):
        info = K.face_info(f)
        if info["type"] == int(K.GeomAbs_Cylinder):
            name = named.name_of(f)
            assert name, "a hole wall has no name"
            out[(round(info["c"][0], 1), round(info["c"][1], 1))] = name
    return out


def test_linear_features_pattern_tags_each_copy():
    doc, hole = hole_doc(10, 30)
    add(doc, "linear_pattern", type="features", features=[hole["id"]], direction={"kind": "std", "name": "X"},
        count=4, spacing=40)
    walls = wall_names(doc)
    assert sorted(x for x, _ in walls) == [10.0, 50.0, 90.0, 130.0], walls
    assert len(set(walls.values())) == 4, walls
    for k in (1, 2, 3):
        assert walls[(10.0 + 40 * k, 30.0)].count(f"_{k}") >= 1, walls


def test_linear_features_pattern_second_direction_tags_both_indices():
    doc, hole = hole_doc(10, 10)
    add(doc, "linear_pattern", type="features", features=[hole["id"]], direction={"kind": "std", "name": "X"},
        count=3, spacing=40, count2=2, dir2={"kind": "std", "name": "Y"}, spacing2=30, dir2_on=True)
    walls = wall_names(doc)
    assert len(walls) == 6 and len(set(walls.values())) == 6, walls
    for k1 in range(3):
        for k2 in range(2):
            if (k1, k2) != (0, 0):
                assert f"_{k1}_{k2}" in walls[(10.0 + 40 * k1, 10.0 + 30 * k2)], (k1, k2, walls)


def test_chamfer_on_copy_three_survives_a_count_change():
    doc, hole = hole_doc(10, 30, w=260.0)
    pat = add(doc, "linear_pattern", type="features", features=[hole["id"]],
              direction={"kind": "std", "name": "X"}, count=4, spacing=40)
    part = only_part(doc)
    add(doc, "chamfer", distance=0.5, edges=edge_refs(
        part, lambda i: i["type"] == int(K.GeomAbs_Circle) and abs(i["c"][2] - 10) < 1e-6
        and abs(i["c"][0] - 130) < 1e-6, expect=1))
    assert not doc.errors, doc.errors

    def cones():
        return sorted(round(K.face_info(f)["c"][0], 1) for f in K.faces(only_part(doc).shape)
                      if K.face_info(f)["type"] == int(GeomAbs_Cone))

    assert cones() == [130.0]
    doc.replace_feature({**pat, "params": {**pat["params"], "count": 6}})
    assert not doc.errors, doc.errors
    assert cones() == [130.0]
    assert len(wall_names(doc)) == 6


def test_circular_features_pattern_tags_each_copy():
    doc, hole = hole_doc(30, 0, w=100, d=100, x0=-50, y0=-50)
    add(doc, "circular_pattern", type="features", features=[hole["id"]], axis={"kind": "std", "name": "Z"},
        count=4, angle=360)
    walls = wall_names(doc)
    assert len(walls) == 4 and len(set(walls.values())) == 4, walls
    for k in (1, 2, 3):
        assert sum(f"_{k}" in n for n in walls.values()) >= 1, walls


def test_mirror_features_tags_the_copy():
    doc, hole = hole_doc(20, 30, w=100, d=60, x0=-50)
    add(doc, "mirror", type="features", features=[hole["id"]], plane=RIGHT)
    walls = wall_names(doc)
    assert sorted(x for x, _ in walls) == [-20.0, 20.0], walls
    a, b = walls[(20.0, 30.0)], walls[(-20.0, 30.0)]
    assert a != b and "MIR" in b and "MIR" not in a, walls
