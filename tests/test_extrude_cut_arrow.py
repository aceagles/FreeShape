"""A Remove or Intersect extrude goes into the part, and the dialog's drag arrow and red ghost show it going there:
the runner records the way each extrude went (`ModelState.extrude_dirs`) and the dialog reads that back."""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
from conftest import add, block_doc, face_refs, normal_is, only_part, region_refs, sketch_feature  # noqa: E402

from freeshape import kernel as K, features as F  # noqa: E402

R, DEPTH = 3.0, 5.0
HOLE = math.pi * R * R * DEPTH


def _block_under_top():
    """A 20 x 20 x 10 block below the Top plane (z -10..0), and a circle sketched on Top: on the block's top face,
    with the sketch normal (+Z) pointing out of the part."""
    doc = block_doc(w=20, d=20, h=10)
    ext = doc.features[-1]
    doc.replace_feature(dict(ext, params=dict(ext["params"], flip=True)))
    sid = sketch_feature(doc, lambda s: s.add_circle(s.add_point(10, 10), R))
    return doc, sid


def _dir(doc, fid):
    return tuple(round(c, 6) for c in doc.state.extrude_dirs[fid])


def test_a_blind_cut_from_a_sketch_on_the_top_face_records_into_the_part():
    doc, sid = _block_under_top()
    before = K.volume(only_part(doc).shape)
    f = add(doc, "extrude", regions=region_refs(doc, sid), op="remove", depth=DEPTH)
    assert not doc.errors.get(f["id"])
    assert abs(before - K.volume(only_part(doc).shape) - HOLE) < 1e-3
    assert _dir(doc, f["id"]) == (0.0, 0.0, -1.0)


def test_a_cut_with_both_ways_into_the_part_follows_opposite_direction():
    doc = block_doc(w=20, d=20, h=10)               # z -10..10: the circle on Top is half way up
    ext = doc.features[-1]
    doc.replace_feature(dict(ext, params=dict(ext["params"], end="symmetric", depth=20)))
    sid = sketch_feature(doc, lambda s: s.add_circle(s.add_point(10, 10), R))
    for flip, want in ((False, 1.0), (True, -1.0)):
        f = add(doc, "extrude", regions=region_refs(doc, sid), op="remove", depth=2, flip=flip)
        assert not doc.errors.get(f["id"]) and _dir(doc, f["id"]) == (0.0, 0.0, want)


def test_a_cut_from_a_face_records_into_the_part():
    doc = block_doc(w=20, d=20, h=10)
    top = face_refs(only_part(doc), normal_is((0, 0, 1)), expect=1)
    f = add(doc, "extrude", faces=top, op="remove", depth=4)
    assert not doc.errors.get(f["id"]) and _dir(doc, f["id"]) == (0.0, 0.0, -1.0)


def test_removed_material_is_the_hole():
    doc, sid = _block_under_top()
    base = doc.state.copy()
    f = add(doc, "extrude", regions=region_refs(doc, sid), op="remove", depth=DEPTH)
    gone = F.removed_material(base, doc.state)
    assert not doc.errors.get(f["id"]) and abs(K.volume(gone) - HOLE) < 1e-3
    lo, hi = K.bbox(gone)
    assert hi[2] <= 1e-6 and lo[2] >= -DEPTH - 1e-6       # inside the block, below its top face
    assert F.removed_material(doc.state, doc.state) is None


def test_the_dialog_arrow_and_ghost_point_into_the_part(tmp_path):
    from test_nav import _window
    from freeshape.ui.viewport import Pick
    doc, sid = _block_under_top()
    app, w, vp = _window()
    w._add_tab(doc)
    app.processEvents()
    w.start_feature("extrude", preselect=[Pick("region", (sid, 0))])
    d = w.dialog
    d.feature["params"].update(op="remove", depth=DEPTH)
    d._do_preview()
    (arrow,) = vp.manipulators
    assert np.allclose(arrow["dir"], (0, 0, -1)) and arrow["value"] == DEPTH
    assert len(vp.ghosts) == 1
    mesh, rgba = vp.ghosts[0]
    assert rgba[0] > rgba[1] and len(mesh.tris) > 0                 # red
    zs = np.asarray(mesh.verts)[:, 2]
    assert zs.max() <= 1e-6 and zs.min() >= -DEPTH - 1e-3

    arrow["on_drag"](-4.0)                     # dragged back past the start: the other way from the arrow
    assert d.feature["params"]["flip"] is True and d.feature["params"]["depth"] == 4.0

    d.feature["params"].update(op="add", flip=False)
    d._do_preview()
    assert vp.ghosts == []                     # only a cut has a ghost
    d.feature["params"].update(op="remove")
    d._do_preview()
    assert vp.ghosts
    w.cancel_dialog()
    assert vp.ghosts == [] and vp.manipulators == []
