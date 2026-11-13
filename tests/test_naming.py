"""Element names built from the kernel helpers' operation history (freeshape/naming.py).

The rules under test are the FreeCAD ones ported in docs/reference/element-naming.md; the section
numbers below are that document's. What these tests care about is the three properties a name has to
have for a reference to survive an edit:

* **complete** -- every face, edge and vertex of a result is named, because an unnamed element can
  never be referenced (§ 6: a name is never invented, so a gap stays a gap);
* **unique** -- no name points at two elements, the one invariant the element map must keep (§ 1);
* **stable** -- an element that an edit did not touch keeps the name it had, which is the whole
  point of naming elements rather than numbering them (§ 4).

Like tests/test_kernel_history.py these reach OCCT only through the kernel helpers, and they name
only what the helpers hand back with `with_history=True`.
"""
import os
import sys

import numpy as np
import pytest
from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from freeshape import kernel as K  # noqa: E402
from freeshape import naming as N  # noqa: E402

Z = np.array([0.0, 0.0, 1.0])


# --------------------------------------------------------------------------- models, built through the kernel

def _rect(u0, v0, u1, v1):
    pts = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
    return [{"type": "line", "p1": pts[i], "p2": pts[(i + 1) % 4]} for i in range(4)]


def _profile(*rects, plane=None, regions=1):
    prof = K.sketch_regions(plane or K.std_plane("Top"), [c for r in rects for c in r])
    assert len(prof) == regions
    return prof


def _sketch(prof, tag):
    """One seed map per sketch region: the starting names an operation builds on (§ 4)."""
    return [N.seed(f, tag=tag) for f in prof]


def _extruded(prof, depth, tag, sketch_tag="S1", **kw):
    shape, hist = K.extrude(prof, Z, depth, with_history=True, **kw)
    return N.name_result(shape, hist, _sketch(prof, sketch_tag), tag, N.OP_FOR["extrude"])


def _plate(x0=0.0, x1=20.0, depth=6.0, tag="F1", sketch_tag="S1"):
    return _extruded(_profile(_rect(x0, 0, x1, 10)), depth, tag, sketch_tag)


def _box_with_hole(depth=6.0, radius=2.0):
    """The reference scenario: a padded rectangle with a hole cut through it (§ 9, "face split by an
    inserted hole"). The pin is a primitive, so it is seeded rather than named from history."""
    plate = _plate(depth=depth)
    pin = K.cylinder(np.array([5.0, 5.0, -2.0]), Z, radius, depth + 4)
    tool = N.seed(pin, tag="P1", op=N.OP_MAKER)
    shape, hist = K.cut(plate.shape, pin, with_history=True)
    return plate, tool, N.name_result(shape, hist, [plate, tool], "F3", N.OP_FOR["cut"])


def _revolved(radius=5.0, tag="F1"):
    prof = _profile(_rect(2, 0, radius, 8), plane=K.std_plane("Front"))
    shape, hist = K.revolve(prof, (0, 0, 0), (0, 0, 1), 270.0, with_history=True)
    return N.name_result(shape, hist, _sketch(prof, "S1"), tag, N.OP_FOR["revolve"])


def _commoned(height=10.0):
    plate = _plate()
    pin = K.cylinder(np.array([5.0, 5.0, -2.0]), Z, 2.0, height)
    tool = N.seed(pin, tag="P1", op=N.OP_MAKER)
    shape, hist = K.common(plate.shape, pin, with_history=True)
    return N.name_result(shape, hist, [plate, tool], "F4", N.OP_FOR["common"])


# --------------------------------------------------------------------------- reading a map

def _faces(named, pred):
    """The named faces whose geometry matches, as [(name, face_info)]."""
    out = []
    for key in named.keys("F"):
        info = K.face_info(K._as_face(named.el.shape(key)))
        if pred(info):
            out.append((named.names.get(key), info))
    return out


def _one(named, pred):
    found = _faces(named, pred)
    assert len(found) == 1, [i["c"] for _, i in found]
    return found[0][0]


def _cylindrical(info):
    return info["type"] == GeomAbs_Cylinder


def _round_at(radius, tol=1e-7):
    return lambda i: i["type"] == GeomAbs_Cylinder and abs(i["r"] - radius) < tol


def _facing(axis, sign):
    return lambda i: i["type"] == GeomAbs_Plane and i["n"][axis] * sign > 0.9


def _at(axis, value, tol=1e-7):
    return lambda i: i["type"] == GeomAbs_Plane and abs(i["c"][axis] - value) < tol


def _tags(name):
    """The tag of every operation in a name, last first: § 2's walk back down the stack."""
    out = []
    while True:
        step = N.split_last(name)
        if step is None:
            return out
        out.append(step[2])
        name = step[0]


def _complete(named):
    """Every element named, and no name empty."""
    assert named.unnamed() == []
    assert all(named.names.values())


def _unique(named):
    for letter in N.KINDS:
        names = named.names_of(letter)
        assert len(set(names)) == len(names)
    assert len(set(named.names.values())) == len(named.names)


# --------------------------------------------------------------------------- the box with a hole

def test_box_with_hole_is_completely_and_uniquely_named():
    _, _, res = _box_with_hole()
    assert (len(res.keys("F")), len(res.keys("E")), len(res.keys("V"))) == (7, 15, 10)
    _complete(res)
    _unique(res)


def test_the_hole_wall_carries_the_cut_operation_tag():
    """The face the cut created is identified by the operation that made it, not by its index."""
    _, tool, res = _box_with_hole()
    wall = _one(res, _cylindrical)
    assert f"{N.TAG}F3:" in wall                      # the cut's feature id, as the last fragment
    assert wall.endswith(f"{N.MOD};{N.OP_CUT}{N.TAG}F3:7,F")
    assert N.split_last(wall)[0] == _one(tool, _cylindrical)      # built on the pin's own wall


def test_every_name_is_parseable_back_to_the_name_before_the_last_operation():
    """§ 2: the length field in the tag postfix makes the grammar readable backwards."""
    plate, _, res = _box_with_hole()
    before, op_text, tag, letter = N.split_last(_one(res, _facing(2, 1)))
    assert (tag, letter) == ("F3", "F")
    assert op_text == f"{N.MOD};{N.OP_CUT}"
    assert before == _one(plate, _facing(2, 1))       # the plate's own top face, before the cut
    assert N.split_last(before)[2] == "F1"            # the extrude underneath it


# --------------------------------------------------------------------------- rebuild and stability

def test_rebuilding_the_same_model_gives_the_same_names():
    first = _box_with_hole()[2]
    second = _box_with_hole()[2]
    assert first.names == second.names


def test_changing_the_extrude_depth_keeps_the_hole_and_top_face_names():
    """A dimension edit must not rename an element it did not change (§ 4). The top face is pinned
    by the parallel marker, the hole wall by the pin face it was cut with."""
    shallow = _box_with_hole(depth=6.0)[2]
    deep = _box_with_hole(depth=9.0)[2]
    _complete(deep)
    _unique(deep)
    assert _one(deep, _cylindrical) == _one(shallow, _cylindrical)
    assert _one(deep, _facing(2, 1)) == _one(shallow, _facing(2, 1))


def test_a_wider_hole_keeps_the_top_face_name():
    narrow = _box_with_hole(radius=2.0)[2]
    wide = _box_with_hole(radius=3.5)[2]
    assert _one(wide, _facing(2, 1)) == _one(narrow, _facing(2, 1))


# --------------------------------------------------------------------------- several profiles in one extrude

def _two_profiles(size=10.0, tag="F1"):
    """Two overlapping sketches extruded in one operation, which the kernel fuses together."""
    first = _profile(_rect(0, 0, 10, 10))
    second = _profile(_rect(5, 5, 5 + size, 5 + size))
    shape, hist = K.extrude(first + second, Z, 3.0, with_history=True)
    sources = [N.seed(first[0], tag="S1"), N.seed(second[0], tag="S2")]
    assert len(K.solids(shape)) == 1                  # so the fuse really ran
    return N.name_result(shape, hist, sources, tag, N.OP_FOR["extrude"])


def test_two_profiles_are_completely_and_uniquely_named():
    named = _two_profiles()
    _complete(named)
    _unique(named)


def test_a_face_of_the_first_profile_survives_a_change_to_the_second():
    small, large = _two_profiles(), _two_profiles(size=14.0)
    _complete(large)
    _unique(large)
    outer = _one(small, _at(0, 0.0))                  # the x=0 side face, swept from sketch S1
    assert outer == _one(large, _at(0, 0.0))
    kept = {n for n in small.names.values() if f"{N.TAG}S1:" in n}
    assert kept and kept <= set(large.names.values())


def test_three_regions_of_one_sketch_are_completely_and_uniquely_named():
    """Two overlapping rectangles in a single sketch make three regions; each is seeded with its own
    label, so the extrusion of all three is still uniquely named."""
    prof = _profile(_rect(0, 0, 10, 10), _rect(5, 5, 15, 15), regions=3)
    shape, hist = K.extrude(prof, Z, 3.0, with_history=True)
    sources = [N.seed(f, tag="S1", labels={("F", 1): f"r{i}"}) for i, f in enumerate(prof, 1)]
    named = N.name_result(shape, hist, sources, "F1", N.OP_FOR["extrude"])
    _complete(named)
    _unique(named)


# --------------------------------------------------------------------------- merge (§ 5)

def test_the_face_refine_merged_has_one_name_built_from_both_sources():
    """`clean` merges the two coplanar top faces of the fused plates into one. § 5: all the sources
    are sorted, the first supplies the name and the rest are appended as the parenthesised list, so
    the merged face has exactly one name and it is none of the source names."""
    left, right = _plate(0, 10, tag="F1", sketch_tag="S1"), _plate(10, 20, tag="F2", sketch_tag="S2")
    shape, hist = K.fuse(left.shape, right.shape, with_history=True)
    res = N.name_result(shape, hist, [left, right], "F9", N.OP_FOR["fuse"])
    _complete(res)
    _unique(res)
    assert len(K.faces(shape)) == 6                   # the seam is gone: one top face, not two

    tops = [_one(part, _facing(2, 1)) for part in (left, right)]
    merged = _one(res, _facing(2, 1))
    assert merged not in tops                         # a reference to either face is lost (§ 5)
    first, extra = sorted(tops)
    base, op_text, tag, letter = N.split_last(merged)
    assert (base, tag, letter) == (first, "F9", "F")  # the first source in sort order names it
    assert op_text.startswith(f"{N.MOD}(") and op_text.endswith(f");{N.OP_FUSE}")
    assert extra in op_text                           # the second is appended as an extra source
    assert N.DUP not in merged                        # no disambiguator was needed


# --------------------------------------------------------------------------- revolve and common

def test_revolve_is_completely_and_uniquely_named_and_stable():
    thin, thick = _revolved(), _revolved(radius=6.0)
    _complete(thin)
    _unique(thin)
    _complete(thick)
    _unique(thick)
    assert set(thin.names.values()) == set(thick.names.values())
    assert _one(thick, _round_at(6.0)) == _one(thin, _round_at(5.0))   # the swept outer wall


def test_common_is_completely_and_uniquely_named_and_stable():
    short, tall = _commoned(), _commoned(height=12.0)
    _complete(short)
    _unique(short)
    _complete(tall)
    _unique(tall)
    assert set(short.names.values()) == set(tall.names.values())
    assert f"{N.TAG}F4:" in _one(short, _cylindrical)


# --------------------------------------------------------------------------- what a map does not hold (§ 6)

def test_an_element_the_cut_removed_has_no_name_in_the_result():
    """§ 6: a deleted element simply has no name. The pin's far end is gone from the cut result, so
    its name is absent -- not re-pointed at some other face."""
    _, tool, res = _box_with_hole()
    far_end = _one(tool, _facing(2, 1))
    assert far_end not in res.names.values()
    assert res.shape_of(far_end) is None


def test_a_name_that_survived_resolves_back_to_its_element():
    _, _, res = _box_with_hole()
    for key in res.keys("F"):
        assert res.shape_of(res.names[key]).IsSame(res.el.shape(key))


# --------------------------------------------------------------------------- the grammar (§ 1)

def test_a_seed_name_carries_its_label_the_op_code_and_the_tag():
    prof = _profile(_rect(0, 0, 4, 4))
    sk = N.seed(prof[0], tag="S7", labels={("E", 1): "g12"})
    assert sk.names[("E", 1)] == f"g12;{N.OP_SKETCH}{N.TAG}S7:4,E"
    assert sk.names[("V", 2)] == f"v2;{N.OP_SKETCH}{N.TAG}S7:4,V"
    assert sk.names[("F", 1)].endswith(",F")
    _complete(sk)
    _unique(sk)


def test_a_tag_that_cannot_be_written_into_a_name_is_refused():
    prof = _profile(_rect(0, 0, 4, 4))
    for bad in ("", None, "F.1", "F 1", "F;1"):
        with pytest.raises(N.NamingError):
            N.seed(prof[0], tag=bad)
    with pytest.raises(N.NamingError):
        N.seed(prof[0], tag="S1", labels={("E", 1): "a.b"})


def test_one_feature_doing_several_steps_adds_one_level_per_tag():
    """§ 2: a feature that runs several kernel steps would otherwise append its own tag once per
    step, so only one extra level per tag is kept."""
    plate = _plate(tag="F1")
    pin = K.cylinder(np.array([5.0, 5.0, -2.0]), Z, 2.0, 10.0)
    step, hist = K.cut(plate.shape, pin, with_history=True)
    once = N.name_result(step, hist, [plate, N.seed(pin, tag="P1", op=N.OP_MAKER)], "F3", N.OP_CUT)
    tool = K.cylinder(np.array([15.0, 5.0, -2.0]), Z, 2.0, 10.0)
    step2, hist2 = K.cut(once.shape, tool, with_history=True)
    twice = N.name_result(step2, hist2, [once, N.seed(tool, tag="P2", op=N.OP_MAKER)], "F3", N.OP_CUT)
    third = K.cylinder(np.array([18.0, 8.0, -2.0]), Z, 0.5, 10.0)
    shape3, hist3 = K.cut(twice.shape, third, with_history=True)
    thrice = N.name_result(shape3, hist3, [twice, N.seed(third, tag="P3", op=N.OP_MAKER)],
                           "F3", N.OP_CUT)
    _complete(thrice)
    _unique(thrice)
    for name in thrice.names.values():
        assert _tags(name).count("F3") <= 2, name              # three cuts, two levels
    assert any(_tags(name).count("F3") == 2 for name in thrice.names.values())
