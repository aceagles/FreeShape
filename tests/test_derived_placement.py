"""I4.1: a Derived placed by mate connector. params["placement"] = {"location": connector id above the Derived,
"base": "origin" | a connector id of the source}. Everything the Derived owns moves by the rigid transform that takes
the base frame onto the location frame, names carried through."""
import copy

import numpy as np

from conftest import add, line_along, region_refs, rect_lines, shape_fingerprint, sketch_feature
from freeshape import assembly as A
from freeshape import features as F
from freeshape import kernel as K
from freeshape.sketch import Sketch
from test_derived import box_studio, derive, make_folder, set_width


def top_face_connector(doc, pid, name="C"):
    part = doc.state.parts[pid]
    i = next(i for i, f in enumerate(K.faces(part.shape))
             if (K.face_info(f).get("n") is not None and K.face_info(f)["n"][2] > 0.9
                 and abs(K.face_info(f)["c"][2] - K.bbox(part.shape)[1][2]) < 1e-6))
    ref = A._ref_signature(part.shape, "face", i, doc.state.named(pid))
    fr = A.connector_frame(part.shape, "face", i)
    return add(doc, "mate_connector", name=name, ref=ref, kind="face", x_ref=A.connector_x_ref(fr), flip=False)


def side_face_connector(doc, pid, axis, sign, name="S"):
    part = doc.state.parts[pid]
    lo, hi = K.bbox(part.shape)
    target = hi[axis] if sign > 0 else lo[axis]
    i = next(i for i, f in enumerate(K.faces(part.shape))
             if (K.face_info(f).get("n") is not None and K.face_info(f)["n"][axis] * sign > 0.9
                 and abs(K.face_info(f)["c"][axis] - target) < 1e-6))
    ref = A._ref_signature(part.shape, "face", i, doc.state.named(pid))
    fr = A.connector_frame(part.shape, "face", i)
    return add(doc, "mate_connector", name=name, ref=ref, kind="face", x_ref=A.connector_x_ref(fr), flip=False)


def local_box(tab, x0=100, y0=100, w=8, h=6, depth=4):
    sid = sketch_feature(tab.doc, lambda s: rect_lines(s, x0, y0, w, h))
    return add(tab.doc, "extrude", regions=region_refs(tab.doc, sid), depth=depth, op="new")


def src_with_connector(fd):
    src, sid = box_studio(fd)
    c = top_face_connector(src.doc, "F2/1", "Top")
    return src, sid, c["id"]


def centre(shape):
    lo, hi = K.bbox(shape)
    return (np.array(lo) + np.array(hi)) / 2


def frame_of(c):
    fr = c["frame"]
    return [np.array(fr[k], float) for k in ("o", "z", "x")]


def test_base_origin_moves_the_part_by_the_location_origin(tmp_path):
    fd = make_folder(tmp_path)
    src, sid = box_studio(fd)
    use = fd.add_tab("studio", "Use")
    ex = local_box(use)
    loc = top_face_connector(use.doc, ex["id"] + "/1")
    f = derive(use, src)
    before = shape_fingerprint(use.doc.state.parts[f"{f['id']}/1"].shape)
    # the Derived sits below the connector: move it down by editing the list
    use.doc.features.remove(f)
    use.doc.features.append(f)
    f["params"]["placement"] = {"location": loc["id"], "base": "origin"}
    use.doc.rebuild()
    assert not use.doc.errors, use.doc.errors
    part = use.doc.state.parts[f"{f['id']}/1"]
    after = shape_fingerprint(part.shape)
    fr = use.doc.state.connectors[loc["id"]]["frame"]
    M = A.frame_matrix(fr)                                   # base is the origin frame: the move is the location
    o = np.array(fr["o"])
    com = M[:3, :3] @ np.array(before["com"]) + M[:3, 3]
    assert np.allclose(after["com"], com, atol=1e-6)
    lo, hi = np.array(before["bbox"][:3]), np.array(before["bbox"][3:])
    assert np.allclose(centre(part.shape), M[:3, :3] @ ((lo + hi) / 2) + M[:3, 3], atol=1e-6)
    assert abs(after["volume"] - before["volume"]) < 1e-6
    sk = use.doc.state.sketches[f"{f['id']}:{sid}"]
    assert np.allclose(sk.plane.origin, o, atol=1e-6)
    assert len(sk.regions) == 1 and sk.region_names[0] and sk.region_names[0].endswith(f";DRV;:H{f['id']}:4,F")
    ex2 = add(use.doc, "extrude", regions=region_refs(use.doc, f"{f['id']}:{sid}"), depth=2, op="new")
    assert not use.doc.errors, use.doc.errors
    assert abs(K.volume(use.doc.state.parts[f"{ex2['id']}/1"].shape) - 400) < 1e-6


def test_source_connector_as_base_lands_on_the_location(tmp_path):
    fd = make_folder(tmp_path)
    src, sid, cid = src_with_connector(fd)
    use = fd.add_tab("studio", "Use")
    ex = local_box(use)
    loc = side_face_connector(use.doc, ex["id"] + "/1", 0, 1)       # +X face: a turned frame
    f = derive(use, src)
    use.doc.features.remove(f)
    use.doc.features.append(f)
    f["params"]["placement"] = {"location": loc["id"], "base": cid}
    use.doc.rebuild()
    assert not use.doc.errors, use.doc.errors
    part = use.doc.state.parts[f"{f['id']}/1"]
    want = frame_of(use.doc.state.connectors[loc["id"]])
    got_src = frame_of(src.doc.state.connectors[cid])
    # push the source connector through the same move by reading the placed box: its top face centre
    # now sits on the location origin, with its normal along the location Z
    moved_top = min(K.faces(part.shape), key=lambda fc: np.linalg.norm(np.array(K.face_info(fc)["c"]) - want[0]))
    info = K.face_info(moved_top)
    assert np.linalg.norm(np.array(info["c"]) - want[0]) < 1e-6
    assert abs(np.dot(info["n"], want[1])) > 0.999999
    assert np.dot(got_src[1], [0, 0, 1]) > 0.99      # the source top connector pointed up


def test_a_derived_connector_moves_with_the_derived(tmp_path):
    fd = make_folder(tmp_path)
    src, sid, cid = src_with_connector(fd)
    use = fd.add_tab("studio", "Use")
    ex = local_box(use)
    loc = side_face_connector(use.doc, ex["id"] + "/1", 0, 1)
    f = derive(use, src)
    fid = f["id"]
    use.doc.features.remove(f)
    use.doc.features.append(f)
    f["params"]["placement"] = {"location": loc["id"], "base": cid}
    # a connector that arrives with the derived part (I3.6) is any entry of state.connectors on a part `<fid>/k`;
    # put one in by wrapping the runner so the entry exists when the placement runs
    pkg_frame = src.doc.state.connectors[cid]["frame"]
    real = F.RUNNERS["derived"]

    def with_connector(state, feat):
        real_place = F._place_derived

        def place(state_, fid_, trsf):
            state_.connectors["D1"] = {"name": "in", "part": f"{fid_}/1", "frame": copy.deepcopy(pkg_frame)}
            return real_place(state_, fid_, trsf)
        F._place_derived = place
        try:
            return real(state, feat)
        finally:
            F._place_derived = real_place
    F.RUNNERS["derived"] = with_connector
    try:
        use.doc.rebuild()
    finally:
        F.RUNNERS["derived"] = real
    assert not use.doc.errors, use.doc.errors
    o, z, x = frame_of(use.doc.state.connectors["D1"])
    want = frame_of(use.doc.state.connectors[loc["id"]])
    assert np.linalg.norm(o - want[0]) < 1e-6
    assert np.dot(z, want[1]) > 0.999999 and np.dot(x, want[2]) > 0.999999


def test_moving_the_location_moves_the_derived_without_the_source_rebuilding(tmp_path):
    fd = make_folder(tmp_path)
    src, sid = box_studio(fd)
    use = fd.add_tab("studio", "Use")
    sk = sketch_feature(use.doc, lambda s: rect_lines(s, 100, 100, 8, 6))
    ex = add(use.doc, "extrude", regions=region_refs(use.doc, sk), depth=4, op="new")
    loc = top_face_connector(use.doc, ex["id"] + "/1")
    f = derive(use, src)
    use.doc.features.remove(f)
    use.doc.features.append(f)
    f["params"]["placement"] = {"location": loc["id"], "base": "origin"}
    use.doc.rebuild()
    pid = f"{f['id']}/1"
    z0 = centre(use.doc.state.parts[pid].shape)[2]
    src_state = src.doc.state
    use.doc.features[1]["params"]["depth"] = 7
    use.doc.rebuild()
    assert not use.doc.errors, use.doc.errors
    assert abs(centre(use.doc.state.parts[pid].shape)[2] - z0 - 3) < 1e-6
    assert src.doc.state is src_state                      # the source was not rebuilt


def test_a_fillet_picked_before_placement_resolves_after(tmp_path):
    fd = make_folder(tmp_path)
    src, sid = box_studio(fd)
    use = fd.add_tab("studio", "Use")
    sk = sketch_feature(use.doc, lambda s: rect_lines(s, 100, 100, 8, 6))
    ex = add(use.doc, "extrude", regions=region_refs(use.doc, sk), depth=4, op="new")
    loc = top_face_connector(use.doc, ex["id"] + "/1")
    f = derive(use, src)
    use.doc.features.remove(f)
    use.doc.features.append(f)
    pid = f"{f['id']}/1"
    use.doc.rebuild()
    part = use.doc.state.parts[pid]
    vertical = [e for e in K.edges(part.shape) if line_along((0, 0, 1))(K.edge_info(e))]
    edges = [F.make_ref(pid, vertical[0], "edge", part.shape, use.doc.state.named(pid))]
    fil = add(use.doc, "fillet", edges=edges, radius=1)
    vol0 = K.volume(use.doc.state.parts[pid].shape)
    f["params"]["placement"] = {"location": loc["id"], "base": "origin"}
    use.doc.rebuild()
    assert not use.doc.errors and fil["id"] not in use.doc.state.recovered
    vol1 = K.volume(use.doc.state.parts[pid].shape)
    assert abs(vol1 - vol0) < 1e-6
    assert use.doc.state.named(pid) is not None and use.doc.state.named(pid).names
    use.doc.features[1]["params"]["depth"] = 9               # the location moves
    use.doc.rebuild()
    assert not use.doc.errors and fil["id"] not in use.doc.state.recovered
    assert abs(K.volume(use.doc.state.parts[pid].shape) - vol0) < 1e-6


def test_no_placement_key_builds_as_before(tmp_path):
    fd = make_folder(tmp_path)
    src, sid = box_studio(fd)
    use = fd.add_tab("studio", "Use")
    f = derive(use, src)
    assert "placement" not in f["params"]
    part = use.doc.state.parts[f"{f['id']}/1"]
    origin = src.doc.state.parts["F2/1"]
    assert shape_fingerprint(part.shape) == shape_fingerprint(origin.shape)
    assert part.shape.IsSame(origin.shape)                  # not even moved: the very same shape
    from freeshape import naming as N
    from freeshape.engine.encode import named_elements
    expect = N.name_derived(origin.shape, named_elements(src.doc.state.named("F2/1")), f["id"])
    assert use.doc.state.named(part.id).names == expect.names


def test_a_location_below_the_derived_is_a_red_feature(tmp_path):
    fd = make_folder(tmp_path)
    src, sid = box_studio(fd)
    use = fd.add_tab("studio", "Use")
    ex = local_box(use)
    f = derive(use, src)
    loc = top_face_connector(use.doc, ex["id"] + "/1")          # added after the Derived: below it
    f["params"]["placement"] = {"location": loc["id"], "base": "origin"}
    use.doc.rebuild()
    assert f["id"] in use.doc.errors and "below" in use.doc.errors[f["id"]]
    f["params"]["placement"] = {"location": "NOPE", "base": "origin"}
    use.doc.rebuild()
    assert "not defined above" in use.doc.errors[f["id"]]
