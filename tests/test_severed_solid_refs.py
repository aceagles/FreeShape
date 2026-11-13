"""References on a solid that a cut severs into two parts (finding F1).

A 60 x 30 x 10 block is cut in two by a 31 mm through hole at its centre. `set_part_shape` keeps the block's part
id for one piece and gives the other `<id>.1`. A named reference stored against the block must find its element
on whichever piece now holds it, or end in an error that names the element when it is gone.

Everything is built through documents and feature runners, as tests/test_scenarios_naming.py does.
"""
from __future__ import annotations

import hashlib
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import naming as N  # noqa: E402

from conftest import add, both, normal_is, only_part, rect_lines, region_refs, sketch_feature  # noqa: E402
from test_scenarios_naming import (dim_block, face_plane, insert_before, insert_sketch_before, pad_on, ref_to,  # noqa: E402
                                   round_face, status, top_at, vertical_edge)

LEFT_END = both(normal_is([-1, 0, 0]), lambda i: abs(i["c"][0]) < 1e-6)
RIGHT_END = both(normal_is([1, 0, 0]), lambda i: abs(i["c"][0] - 60) < 1e-6)
HOLE = dict(standard="custom", diameter=31.0, end="through", style="simple")


def block():
    """The block and the plane on its top face that the severing hole is sketched on."""
    doc, _sid, _ex, _dims = dim_block(60.0, 30.0, 10.0)
    return doc, face_plane(doc, ref_to(doc, "face", top_at(10.0)))


def centre_point(s):
    s.add_point_entity(s.add_point(0, 0))


def sever_at_end(doc, plane):
    return add(doc, "hole", sketch=sketch_feature(doc, centre_point, plane), **HOLE)


def sever_before(doc, fid, plane):
    """The severing hole inserted at the position of feature `fid`, below everything from there on."""
    return insert_before(doc, fid, "hole", sketch=insert_sketch_before(doc, fid, centre_point, plane), **HOLE)


def pieces(doc):
    """(part id of the piece at x < 30, part id of the piece at x > 30) once the block is severed."""
    assert len(doc.state.parts) == 2, list(doc.state.parts)
    left = [pid for pid, p in doc.state.parts.items() if K.bbox(p.shape)[1][0] < 30]
    right = [pid for pid, p in doc.state.parts.items() if K.bbox(p.shape)[0][0] > 30]
    assert len(left) == 1 and len(right) == 1
    return left[0], right[0]


def rounds(doc, pid, r=2.0):
    return [f for f in K.faces(doc.state.parts[pid].shape) if round_face(r)(K.face_info(f))]


def fillet(doc, x, y):
    return add(doc, "fillet", edges=[ref_to(doc, "edge", vertical_edge(x, y))], radius=2.0)


# =========================================================================== the lookup itself

def test_split_gives_the_second_piece_a_suffixed_part_id():
    doc, plane = block()
    base = only_part(doc).id
    sever_at_end(doc, plane)
    assert not doc.errors, doc.errors
    assert sorted(doc.state.parts) == [base, base + ".1"]
    assert all(doc.state.named(pid) is not None for pid in doc.state.parts)


@pytest.mark.parametrize("kind,pred,side", [
    ("face", RIGHT_END, "right"),
    ("edge", vertical_edge(60, 0), "right"),
    ("face", LEFT_END, "left"),
    ("edge", vertical_edge(0, 30), "left"),
])
def test_stored_reference_finds_its_element_on_either_piece(kind, pred, side):
    """A face or edge the cut leaves alone keeps its name; the reference stored against the whole block finds it
    by that name on the piece that holds it, at the place it was."""
    doc, plane = block()
    ref = ref_to(doc, kind, pred)
    was = (K.face_info if kind == "face" else K.edge_info)(F.resolve(doc.state, ref, kind, fid="probe")[1])["c"]
    sever_at_end(doc, plane)
    left, right = pieces(doc)
    pid, sub = F.resolve(doc.state, ref, kind, fid="probe")
    assert pid == (right if side == "right" else left)
    assert doc.state.named(pid).name_of(sub) == ref["name"]
    assert np.allclose((K.face_info if kind == "face" else K.edge_info)(sub)["c"], was)
    assert "probe" not in doc.state.recovered


@pytest.mark.parametrize("corner,side", [((60.0, 0.0, 0.0), "right"), ((0.0, 30.0, 0.0), "left")])
def test_stored_vertex_reference_finds_its_vertex_on_either_piece(corner, side):
    doc, plane = block()
    p = only_part(doc)
    ref = F.make_vertex_ref(p.id, corner, p.shape, doc.state.named(p.id))
    assert ref.get("name")
    sever_at_end(doc, plane)
    assert np.allclose(F.resolve_point(doc.state, ref, fid="probe"), corner)
    assert "probe" not in doc.state.recovered


def test_reference_stored_against_the_split_off_piece_finds_an_element_on_the_first():
    """Which piece keeps the block's id is the kernel's order of solids, which an edit can swap. A reference that
    names the `.1` piece as its part still finds its element when that element is on the other piece."""
    doc, plane = block()
    sever_at_end(doc, plane)
    left, right = pieces(doc)
    first, second = sorted(doc.state.parts)
    on_first = LEFT_END if first == left else RIGHT_END
    ref = dict(ref_to(doc, "face", on_first, first), part=second)
    pid, sub = F.resolve(doc.state, ref, "face", fid="probe")
    assert pid == first and doc.state.named(pid).name_of(sub) == ref["name"]


def test_a_part_that_was_never_split_is_not_searched():
    """Two separate blocks: a name missing from the reference's own part is an error even though another part,
    which is not a piece of it, exists."""
    doc, _plane = block()
    other = sketch_feature(doc, lambda s: rect_lines(s, 100, 0, 20, 20))
    add(doc, "extrude", regions=region_refs(doc, other), depth=10.0, op="new")
    first, second = list(doc.state.parts)
    ref = dict(ref_to(doc, "face", both(normal_is([1, 0, 0]), lambda i: abs(i["c"][0] - 120) < 1e-6), second),
               part=first)
    with pytest.raises(K.KernelError) as err:
        F.resolve(doc.state, ref, "face", fid="probe")
    assert ref["name"] in str(err.value)


# =========================================================================== features below an inserted cut

def test_fillet_follows_its_edge_onto_the_split_off_piece():
    doc, plane = block()
    fil = fillet(doc, 60, 30)
    sever_before(doc, fil["id"], plane)
    assert not doc.errors, doc.errors
    assert status(doc, fil["id"]) == "ok"
    left, right = pieces(doc)
    assert len(rounds(doc, right)) == 1 and not rounds(doc, left)


def test_sketch_on_face_and_pad_follow_the_face_onto_the_split_off_piece():
    doc, plane = block()
    sid, pad = pad_on(doc, ref_to(doc, "face", RIGHT_END))
    sever_before(doc, sid, plane)
    assert not doc.errors, doc.errors
    assert status(doc, sid) == "ok" and status(doc, pad["id"]) == "ok"
    left, right = pieces(doc)
    assert np.isclose(K.bbox(doc.state.parts[right].shape)[1][0], 64.0)     # the pad stands on the x = 60 face
    assert np.isclose(K.bbox(doc.state.parts[left].shape)[1][0], 30 - np.sqrt(15.5 ** 2 - 15 ** 2))


def test_fillet_whose_edge_stays_on_the_first_piece_keeps_working():
    doc, plane = block()
    fil = fillet(doc, 0, 0)
    sever_before(doc, fil["id"], plane)
    assert not doc.errors, doc.errors
    assert status(doc, fil["id"]) == "ok"
    left, right = pieces(doc)
    assert len(rounds(doc, left)) == 1 and not rounds(doc, right)


def test_edge_swallowed_by_the_cut_is_an_error_that_names_it():
    """A chamfer on the rim of a small hole at the centre; the severing hole removes the small hole whole. The
    edge is gone from both pieces, so the chamfer is an error naming it and nothing is matched by position."""
    doc, plane = block()
    add(doc, "hole", sketch=sketch_feature(doc, centre_point, plane), standard="custom", diameter=5.0, end="blind",
        depth=4.0, style="simple", tip=False)
    rim = ref_to(doc, "edge", both(lambda i: abs(i.get("r", 0) - 2.5) < 1e-6, lambda i: abs(i["c"][2] - 10) < 1e-6))
    ch = add(doc, "chamfer", edges=[rim], distance=0.5)
    first_small = [f["id"] for f in doc.features if f["type"] == "sketch"][1]
    sever_before(doc, first_small, plane)
    # the small hole now finds nothing to cut, and the chamfer's edge does not exist
    assert status(doc, ch["id"]) == "error"
    assert rim["name"] in doc.errors[ch["id"]]
    assert not doc.state.recovered


def test_face_cut_into_one_piece_per_part_is_found_by_name_or_is_a_named_error():
    """The top face is cut in two, one piece on each part. The cut renames a face it modifies, so the stored
    name is on at most one piece: the reference finds that piece or is an error naming the face, never another
    face (the same rule tests/test_scenarios_naming.py holds a blind hole to)."""
    doc, plane = block()
    top = ref_to(doc, "face", top_at(10.0))
    sever_at_end(doc, plane)
    holders = [pid for pid in doc.state.parts if top["name"] in doc.state.named(pid).names.values()]
    assert len(holders) <= 1
    if holders:
        pid, sub = F.resolve(doc.state, top, "face", fid="probe")
        assert pid == holders[0] and top_at(10.0)(K.face_info(sub))
    else:
        with pytest.raises(K.KernelError) as err:
            F.resolve(doc.state, top, "face", fid="probe")
        assert top["name"] in str(err.value)


# =========================================================================== stamps shared by the pieces (ADR 0008)

@pytest.mark.parametrize("order", ["left_then_right", "right_then_left"])
def test_two_fillets_on_different_pieces_both_survive_the_split(order):
    """Two fillets, one at each end. The second one's stored edge name carries the first fillet's stamp, put on
    when the block was whole. After the split the first fillet runs on one piece only, so on the other piece
    (and, for the reverse order, on the first piece) no element has that name."""
    doc, plane = block()
    corners = [(0, 0), (60, 30)] if order == "left_then_right" else [(60, 30), (0, 0)]
    fils = [fillet(doc, *c) for c in corners]
    sever_before(doc, fils[0]["id"], plane)
    assert not doc.errors, doc.errors
    left, right = pieces(doc)
    assert len(rounds(doc, left)) == 1 and len(rounds(doc, right)) == 1


def all_names(doc, pid):
    return sorted(doc.state.named(pid).names.values())


def keyed(doc, pid):
    """{(kind, rounded geometry): name} of every face and edge of one part."""
    out = {}
    named = doc.state.named(pid)
    for letter, kind, info in (("F", "face", lambda x: K.face_info(K._as_face(x))),
                               ("E", "edge", lambda x: K.edge_info(K._as_edge(x)))):
        for sub, name in named.named(letter):
            i = info(sub)
            out[(kind, tuple(np.round(i["c"], 4)), round(i.get("area", i.get("len", 0.0)), 4))] = name
    return out


def test_features_below_the_cut_land_on_their_pieces():
    """A fillet at the left end, a chamfer at the right end and a pad on the right end face, all below the cut."""
    doc, plane = block()
    fil = fillet(doc, 0, 30)
    cha = add(doc, "chamfer", edges=[ref_to(doc, "edge", vertical_edge(60, 0))], distance=1.0)
    sid, pad = pad_on(doc, ref_to(doc, "face", RIGHT_END))
    sever_before(doc, fil["id"], plane)
    assert not doc.errors, doc.errors
    assert not doc.state.recovered
    left, right = pieces(doc)
    assert len(rounds(doc, left)) == 1 and not rounds(doc, right)
    assert np.isclose(K.bbox(doc.state.parts[right].shape)[1][0], 64.0)
    diagonal = [f for f in K.faces(doc.state.parts[right].shape) if normal_is([2 ** -0.5, -2 ** -0.5, 0])(K.face_info(f))]
    assert len(diagonal) == 1


# SHA-256 of the sorted element names of the block with a fillet at (0, 0) and a chamfer at (60, 30), built on
# the run branch before ADR 0008, with no severing cut. 38 names.
UNSEVERED_NAMES = (38, "9e9f4709759c2e3dcc7bc83400436bd6f83aac01d4235a937639ea00ded514b5")


def test_a_part_that_is_never_severed_keeps_every_name():
    doc, _plane = block()
    fillet(doc, 0, 0)
    add(doc, "chamfer", edges=[ref_to(doc, "edge", vertical_edge(60, 30))], distance=1.0)
    (pid,) = doc.state.parts
    names = all_names(doc, pid)
    assert (len(names), hashlib.sha256("\n".join(names).encode()).hexdigest()) == UNSEVERED_NAMES


def test_the_cut_changes_no_name_it_does_not_touch():
    """The invariant of ADR 0008: with the severing hole inserted below a fillet, every face and edge the hole
    leaves as it was carries the same name as in the model without the hole."""
    whole, _p = block()
    fillet(whole, 60, 30)
    (pid,) = whole.state.parts
    before = keyed(whole, pid)
    doc, plane = block()
    fil = fillet(doc, 60, 30)
    sever_before(doc, fil["id"], plane)
    assert not doc.errors, doc.errors
    after = {}
    for pid in doc.state.parts:
        after.update(keyed(doc, pid))
    # the fillet's own round face and edges are named from the top face the hole modifies, so they are the hole's
    far_corner = lambda k: k[1][0] > 57.9 and k[1][1] > 27.9          # noqa: E731
    same = [k for k in before if k in after and not far_corner(k)]
    assert len(same) >= 8, "most of the block is untouched by the hole"
    assert [(k, before[k], after[k]) for k in same if before[k] != after[k]] == []


def test_deleting_the_fillet_on_one_piece_only_removes_its_stamp_from_the_sibling():
    doc, plane = block()
    fil = fillet(doc, 0, 30)
    sever_before(doc, fil["id"], plane)
    left, right = pieces(doc)
    stamped = all_names(doc, right)
    tag = N._tag_text(fil["id"])
    assert all(split[2] == tag for split in map(N.split_last, stamped))      # the fillet is the last op on every name

    def peeled(name):
        before, _op, _tag, _letter = N.split_last(name)
        return before
    doc.delete_features([fil["id"]])
    assert not doc.errors, doc.errors
    left, right = pieces(doc)
    assert all_names(doc, right) == sorted(peeled(n) for n in stamped)


def test_a_feature_on_both_pieces_stamps_neither_twice():
    doc, plane = block()
    fil = fillet(doc, 0, 0)
    sever_before(doc, fil["id"], plane)
    two = add(doc, "fillet", edges=[ref_to(doc, "edge", vertical_edge(0, 30), pieces(doc)[0]),
                                    ref_to(doc, "edge", vertical_edge(60, 0), pieces(doc)[1])], radius=1.0)
    assert not doc.errors, doc.errors
    tag = N._tag_text(two["id"])
    for pid in doc.state.parts:
        for name in all_names(doc, pid):
            levels, rest = 0, name
            while N.split_last(rest) is not None and N.split_last(rest)[2] == tag:
                levels, rest = levels + 1, N.split_last(rest)[0]
            assert levels <= 2, name                  # section 2: one extra level per tag at most
            assert "FLT;FLT" not in name, name        # nor an operation text written on top of its own
    left, right = pieces(doc)
    assert len(rounds(doc, left, 1.0)) == 1 and len(rounds(doc, right, 1.0)) == 1
