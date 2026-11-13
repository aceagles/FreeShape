"""The Mate connector feature of a Part Studio (I3.1): "mate_connector" finds its face or edge by name, builds
the frame with assembly.connector_frame + apply_x_ref and stores it in ModelState.connectors."""
import copy

import numpy as np
import pytest

from conftest import add, only_part, try_add, shape_fingerprint
from freeshape.engine import InProcessEngine, ProcessEngine
from freeshape.engine.linkgraph import lean_package, live_package, package_digest
from freeshape import assembly as A
from freeshape import features as F
from freeshape import kernel as K
from test_connector_frames import H, move_hole, plate_studio


def top_index(shape):
    out = [i for i, f in enumerate(K.faces(shape))
           if (n := K.face_info(f).get("n")) and n[2] > 0.9 and abs(K.face_info(f)["c"][2] - H) < 1e-6]
    assert len(out) == 1, out
    return out[0]


def connector_params(doc, name="C1", flip=False, index=None):
    """Params of a connector on the studio's top face, made the way the UI would: name-bearing reference plus the
    stored X reference of the frame it had when made."""
    part = only_part(doc)
    i = top_index(part.shape) if index is None else index
    ref = A._ref_signature(part.shape, "face", i, doc.state.named(part.id))
    assert ref.get("name")
    fr = A.connector_frame(part.shape, "face", i)
    return {"name": name, "ref": ref, "kind": "face", "x_ref": A.connector_x_ref(fr), "flip": flip}


def resolved_top(doc, ref):
    pid, sub = F.resolve(doc.state, ref, "face")
    return K.face_info(sub)


def test_connector_on_top_face_origin_z_and_x():
    doc, _ = plate_studio()
    p = connector_params(doc)
    f = add(doc, "mate_connector", **p)
    c = doc.state.connectors[f["id"]]
    top = K.face_info(K.faces(only_part(doc).shape)[top_index(only_part(doc).shape)])
    assert c["name"] == "C1" and c["part"] == only_part(doc).id
    fr = c["frame"]
    assert np.allclose(fr["o"], top["c"], atol=1e-9)
    assert np.allclose(fr["z"], [0, 0, 1], atol=1e-9)
    expected = A.apply_x_ref(A.connector_frame(only_part(doc).shape, "face", top_index(only_part(doc).shape)),
                             p["x_ref"])["x"]
    assert np.allclose(fr["x"], expected, atol=1e-9)
    assert abs(abs(fr["x"][0]) - 1) < 1e-9                         # along the 60 mm edges
    # an x_ref of [0, 1] fixes X along the plane's own Y instead
    p2 = dict(p, x_ref=[0.0, 1.0])
    f2 = add(doc, "mate_connector", **p2)
    x2 = np.array(doc.state.connectors[f2["id"]]["frame"]["x"])
    assert abs(abs(x2[1]) - 1) < 1e-9 and abs(x2 @ np.array(fr["z"])) < 1e-9
    # flip reverses Z
    f3 = add(doc, "mate_connector", **dict(p, flip=True))
    assert np.allclose(doc.state.connectors[f3["id"]]["frame"]["z"], [0, 0, -1], atol=1e-9)


def test_hole_into_corner_fillet_keeps_connector_frame():
    """The spike's failing case: moving the hole to 2.5 mm from two edges cuts the corner fillet and changes
    which top edge is longest; X must not turn round."""
    doc, ids = plate_studio(fillet=5.0, hole=(8.0, 8.0))
    p = connector_params(doc)
    f = add(doc, "mate_connector", **p)
    before = copy.deepcopy(doc.state.connectors[f["id"]]["frame"])
    shape_before = only_part(doc).shape

    move_hole(doc, ids["hole"], (2.5, 2.5))
    assert not doc.errors, doc.errors
    assert not only_part(doc).shape.IsSame(shape_before)
    now = doc.state.connectors[f["id"]]["frame"]
    face = resolved_top(doc, p["ref"])
    assert np.linalg.norm(np.array(now["o"]) - np.array(face["c"])) < 1e-6
    assert float(np.array(now["x"]) @ np.array(before["x"])) > 0.999
    assert float(np.array(now["z"]) @ np.array(before["z"])) > 0.999
    # the longest-edge rule alone would have turned X round
    part = only_part(doc)
    pid, sub = F.resolve(doc.state, p["ref"], "face")
    i = next(k for k, s in enumerate(K.faces(part.shape)) if s.IsSame(sub))
    assert float(A.connector_frame(part.shape, "face", i)["x"] @ np.array(before["x"])) < 0.999


def test_deleting_the_faces_feature_turns_connector_red():
    doc, ids = plate_studio(fillet=5.0)
    fillet = next(f for f in doc.features if f["type"] == "fillet")
    part = only_part(doc)
    # a face the fillet made: a cylinder face of the rounded corner
    idx = next(i for i, f in enumerate(K.faces(part.shape)) if "n" not in K.face_info(f))
    f = add(doc, "mate_connector", **connector_params(doc, index=idx))
    add(doc, "mate_connector", **connector_params(doc, name="Top"))
    assert f["id"] in doc.state.connectors
    doc.delete_features({fillet["id"]})
    assert f["id"] in doc.errors and "gone" in doc.errors[f["id"]]
    assert f["id"] not in doc.state.connectors
    other = [x for x in doc.features if x["type"] == "mate_connector" and x["id"] != f["id"]][0]
    # the rest of the studio still builds (the plate, the top face connector)
    assert only_part(doc) is not None
    assert other["id"] in doc.state.connectors or other["id"] in doc.errors
    assert not [e for k, e in doc.errors.items() if k not in (f["id"], other["id"])]


def test_origin_connector_and_missing_owner():
    doc, _ = plate_studio()
    pid = only_part(doc).id
    f = add(doc, "mate_connector", name="O", ref={"origin": True}, owner=pid)
    c = doc.state.connectors[f["id"]]
    assert c["part"] == pid
    assert np.allclose(c["frame"]["o"], [0, 0, 0]) and np.allclose(c["frame"]["z"], [0, 0, 1])
    assert np.allclose(c["frame"]["x"], [1, 0, 0])
    g, err = try_add(doc, "mate_connector", name="O2", ref={"origin": True}, owner="nope/1")
    assert err and "nope/1" in err
    assert g["id"] not in doc.state.connectors
    g, err = try_add(doc, "mate_connector", name="O3", ref={"origin": True})
    assert err


def test_empty_reference_is_red():
    doc, _ = plate_studio()
    f, err = try_add(doc, "mate_connector", name="none")
    assert err and f["id"] not in doc.state.connectors


def test_studio_without_connectors_builds_same_parts():
    """Adding connector features changes no part: the parts hash the same with and without them."""
    plain, _ = plate_studio(fillet=5.0, hole=(8.0, 8.0))
    with_c, _ = plate_studio(fillet=5.0, hole=(8.0, 8.0))
    add(with_c, "mate_connector", **connector_params(with_c))
    add(with_c, "mate_connector", name="O", ref={"origin": True}, owner=only_part(with_c).id)
    assert plain.state.connectors == {}
    assert len(with_c.state.connectors) == 2
    assert shape_fingerprint(only_part(plain).shape) == shape_fingerprint(only_part(with_c).shape)
    assert [p.id for p in plain.state.parts.values()] == [p.id for p in with_c.state.parts.values()]


# --- I3.2: a Part Studio publishes its mate connectors ---------------------------------------------------------

ENGINES = {"inprocess": InProcessEngine, "process": ProcessEngine}


@pytest.fixture(params=sorted(ENGINES))
def engine(request):
    e = ENGINES[request.param]()
    yield e
    e.close()


def _published_studio(tmp_path):
    doc, ids = plate_studio()
    f = add(doc, "mate_connector", **connector_params(doc))
    path = str(tmp_path / "studio.fshape")
    doc.save(path)
    return doc, ids, f["id"], path


def _digest(engine, tab):
    if isinstance(engine, InProcessEngine):
        pkg = live_package("studio", engine._get(tab).doc, engine.encoder)
    else:
        pkg = lean_package(engine._lean(engine._tabs[tab], ())[1])
    return package_digest("studio", pkg, ())


def test_studio_publishes_its_connector(engine, tmp_path):
    doc, ids, cid, path = _published_studio(tmp_path)
    tab = engine.open_tab(path)
    out = engine.outputs(tab)
    assert [c.id for c in out.connectors] == [cid]
    c, want = out.connectors[0], doc.state.connectors[cid]
    assert c.name == "C1" and c.part == want["part"] and len(c.hash) == 64
    for k in ("o", "z", "x"):
        assert np.allclose(c.frame[k], want["frame"][k], atol=1e-9)


def test_studio_without_connectors_publishes_none(engine, tmp_path):
    doc, ids = plate_studio()
    path = str(tmp_path / "plain.fshape")
    doc.save(path)
    tab = engine.open_tab(path)
    assert engine.outputs(tab).connectors == []


def test_moving_the_connector_changes_the_digest_but_a_sketch_rename_does_not(engine, tmp_path):
    doc, ids, cid, path = _published_studio(tmp_path)
    tab = engine.open_tab(path)
    before = _digest(engine, tab)
    f = copy.deepcopy(doc.feature(ids["sketch"]))
    f["name"] = "renamed"
    engine.submit_edit(tab, {"op": "replace_feature", "feature": f})
    engine.outputs(tab)
    assert _digest(engine, tab) == before
    f = copy.deepcopy(doc.feature(cid))
    f["params"]["flip"] = True                      # reverses Z: the published frame moves
    engine.submit_edit(tab, {"op": "replace_feature", "feature": f})
    out = engine.outputs(tab)
    assert np.allclose(out.connectors[0].frame["z"], [0, 0, -1], atol=1e-9)
    assert _digest(engine, tab) != before


def second_part(doc):
    """Add a second, separate part beside the plate; returns its part id."""
    from conftest import TOP, rect_lines, region_refs, sketch_feature
    before = set(doc.state.parts)
    sid = sketch_feature(doc, lambda s: rect_lines(s, 100, 0, 10, 10), TOP)
    add(doc, "extrude", regions=region_refs(doc, sid), depth=5, op="new")
    (pid,) = set(doc.state.parts) - before
    return pid


def test_owner_part_can_differ_from_the_picked_faces_part():
    """Like Onshape's Owner part: the frame sits on the picked face, the connector belongs to the chosen part."""
    doc, _ = plate_studio()
    plate = only_part(doc).id
    params = connector_params(doc, name="OnPlate")
    other = second_part(doc)
    own = add(doc, "mate_connector", **params)
    moved = add(doc, "mate_connector", **{**params, "name": "ForOther"}, owner=other)
    a, b = doc.state.connectors[own["id"]], doc.state.connectors[moved["id"]]
    assert a["part"] == plate and b["part"] == other
    for k in ("o", "z", "x"):
        assert np.allclose(a["frame"][k], b["frame"][k]), k      # same frame, only the owner differs
    assert not doc.errors


def test_owner_part_that_is_gone_is_an_error_naming_it():
    doc, _ = plate_studio()
    params = connector_params(doc)
    f, err = try_add(doc, "mate_connector", **params, owner="nope/1")
    assert err and "nope/1" in err
    assert f["id"] not in doc.state.connectors
