"""Linear pattern with a second direction (I3.3): a grid of count x count2 instances, the seed is
instance (0, 0); names carry both indices; one direction behaves exactly as before."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape import kernel as K  # noqa: E402

from conftest import add, block_doc  # noqa: E402

X = {"kind": "std", "name": "X"}
Y = {"kind": "std", "name": "Y"}


def grid_doc(op="new", spacing=30, spacing2=40, count=3, count2=2):
    doc = block_doc(w=10.0, d=10.0, h=5.0)
    pid = next(iter(doc.state.parts))
    f = add(doc, "linear_pattern", parts=[pid], direction=X, count=count, spacing=spacing,
            dir2=Y, count2=count2, spacing2=spacing2, dir2_on=True, op=op)
    return doc, f


def centres(doc):
    out = set()
    for p in doc.state.parts.values():
        c = K.face_info(K.faces(p.shape)[0])["c"]
        out.add((round(c[0]), round(c[1])))
    return out


def top_names(doc):
    names = []
    for p in doc.state.parts.values():
        nm = doc.state.named(p.id)
        for f in K.faces(p.shape):
            if np.allclose(K.face_info(f).get("n", (9, 9, 9)), (0, 0, 1), atol=1e-6):
                names.append(nm.name_of(f))
    return names


def test_grid_of_six_bodies():
    doc, _ = grid_doc()
    assert not doc.errors, doc.errors
    assert len(doc.state.parts) == 6
    cs = centres(doc)
    assert len(cs) == 6
    xs = sorted({c[0] for c in cs})
    ys = sorted({c[1] for c in cs})
    assert [b - xs[0] for b in xs] == [0, 30, 60]
    assert [b - ys[0] for b in ys] == [0, 40]


def test_grid_merged_volume():
    doc, _ = grid_doc(op="add")
    assert not doc.errors, doc.errors
    total = sum(K.volume(p.shape) for p in doc.state.parts.values())
    assert abs(total - 6 * 10 * 10 * 5) < 1e-3


def test_names_unique_across_grid():
    doc, _ = grid_doc()
    names = top_names(doc)
    assert len(names) == 6 and len(set(names)) == 6, names
    assert any("_1_1" in n for n in names), names


def test_one_direction_names_unchanged():
    base = block_doc(w=10.0, d=10.0, h=5.0)
    pid = next(iter(base.state.parts))
    add(base, "linear_pattern", parts=[pid], direction=X, count=3, spacing=30, op="new")
    doc = block_doc(w=10.0, d=10.0, h=5.0)
    pid = next(iter(doc.state.parts))
    add(doc, "linear_pattern", parts=[pid], direction=X, count=3, spacing=30, op="new",
        dir2=Y, count2=1, spacing2=40, dir2_on=True)
    assert sorted(top_names(base)) == sorted(top_names(doc))


def test_reference_on_grid_instance_survives_spacing_change():
    doc, f = grid_doc()
    target = None
    for p in doc.state.parts.values():
        c = K.face_info(K.faces(p.shape)[0])["c"]
        if round(c[0]) >= 55 and round(c[1]) >= 35:      # instance (2, 1)
            target = p
    assert target is not None
    nm = doc.state.named(target.id)
    name = next(nm.name_of(fc) for fc in K.faces(target.shape)
                if np.allclose(K.face_info(fc).get("n", (9, 9, 9)), (0, 0, 1), atol=1e-6))
    assert "_2_1" in name, name
    sp, sp2 = 30, 40
    for key, val in (("spacing", 45), ("spacing2", 55)):
        f["params"][key] = val
        sp, sp2 = (val, sp2) if key == "spacing" else (sp, val)
        doc.rebuild()
        assert not doc.errors, doc.errors
        found = []
        for p in doc.state.parts.values():
            n2 = doc.state.named(p.id)
            for fc in K.faces(p.shape):
                if n2.name_of(fc) == name:
                    found.append(K.face_info(fc)["c"])
        assert len(found) == 1, (key, found)
        assert abs(found[0][0] - (5 + 2 * sp)) < 1e-3 and abs(found[0][1] - (5 + sp2)) < 1e-3, found
