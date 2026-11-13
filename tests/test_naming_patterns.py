"""Naming wired into mirror, linear pattern and circular pattern (I1.10): every instance of a
pattern carries its instance index in its names, so the same source face has one distinct, stable
name per copy (docs/reference/element-naming.md § 7)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape import kernel as K  # noqa: E402

from conftest import add, block_doc  # noqa: E402


def top_name(doc, part):
    named = doc.state.named(part.id)
    assert named is not None, "the part has no element map"
    for f in K.faces(part.shape):
        info = K.face_info(f)
        if np.allclose(info.get("n", (9, 9, 9)), (0, 0, 1), atol=1e-6):
            return named.name_of(f)
    raise AssertionError("no top face")


def instances(doc):
    """{x of the part's centre: name of its top face}, one entry per part."""
    out = {}
    for p in doc.state.parts.values():
        x = round(K.face_info(K.faces(p.shape)[0])["c"][0], 3)
        out[x] = (p, top_name(doc, p))
    return out


def linear_doc(count):
    doc = block_doc(w=10.0, d=10.0, h=5.0)
    pid = next(iter(doc.state.parts))
    add(doc, "linear_pattern", parts=[pid], direction={"kind": "std", "name": "X"},
        count=count, spacing=30, op="add")
    assert not doc.errors, doc.errors
    return doc


def test_linear_pattern_gives_each_instance_its_own_name():
    doc = linear_doc(4)
    parts = list(doc.state.parts.values())
    assert len(parts) == 4
    names = [top_name(doc, p) for p in parts]
    assert len(set(names)) == 4, names
    for k in (1, 2, 3):
        assert sum(f"_{k}" in n for n in names) == 1, names


def test_linear_pattern_count_change_keeps_instance_names():
    four = sorted(n for _, n in instances(linear_doc(4)).values())
    five = sorted(n for _, n in instances(linear_doc(5)).values())
    assert len(five) == 5
    assert set(four) <= set(five), (four, five)


def test_circular_pattern_instances_are_distinct_and_stable():
    def build(count):
        doc = block_doc(w=10.0, d=10.0, h=5.0, x0=30.0)
        pid = next(iter(doc.state.parts))
        add(doc, "circular_pattern", parts=[pid], axis={"kind": "std", "name": "Z"},
            count=count, angle=360, op="add")
        assert not doc.errors, doc.errors
        return sorted(top_name(doc, p) for p in doc.state.parts.values())
    four, five = build(4), build(5)
    assert len(set(four)) == 4 and len(five) == 5
    # the step changes with the count, but a name is built from source and index only
    assert set(four) <= set(five), (four, five)


def test_mirror_names_differ_from_the_source():
    doc = block_doc(w=10.0, d=10.0, h=5.0, x0=20.0)
    pid = next(iter(doc.state.parts))
    src = top_name(doc, doc.state.parts[pid])
    add(doc, "mirror", parts=[pid], plane={"kind": "std", "name": "Right"}, op="new")
    assert not doc.errors, doc.errors
    parts = list(doc.state.parts.values())
    assert len(parts) == 2
    names = {top_name(doc, p) for p in parts}
    assert len(names) == 2 and src in names
    other = next(n for n in names if n != src)
    assert ";MIR" in other and ";MIR" not in src
