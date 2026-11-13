"""A mate end on a Part Studio part's own published mate connector (I3.4): {"inst", "named": <connector id>}.
The frame is the published one, re-read on every refresh; a connector no longer published turns the mate red."""
import json
import os
import types

import numpy as np
import pytest

from conftest import add, only_part
from freeshape import assembly as A
from freeshape import features as F
from freeshape import kernel as K
from freeshape import links as L
from test_connector_frames import H, assembly, bottom_face, move_hole, plate_studio, src, top_face
from test_studio_connectors import connector_params

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Pnt


def own(studio):
    """connector_resolver for a one-studio assembly: what links.resolve_connectors answers."""
    def fn(source):
        if source["part"] != "plate":
            return None
        return [{"id": fid, "name": c["name"], "frame": {k: list(c["frame"][k]) for k in ("o", "z", "x")}}
                for fid, c in studio.state.connectors.items()]
    return fn


def bottom_params(doc, name="B1"):
    part = only_part(doc)
    i = next(i for i, f in enumerate(K.faces(part.shape)) if (K.face_info(f).get("n") or [0, 0, 0])[2] < -0.9)
    return connector_params(doc, name=name, index=i)


def side_edge_params(doc, name="Side"):
    """A connector on the top edge along Y at x = 0: its midpoint does not move when the hole cuts a corner."""
    part = only_part(doc)
    for i, e in enumerate(K.edges(part.shape)):
        info = K.edge_info(e)
        if (info["type"] == int(K.GeomAbs_Line) and abs(abs(info["d"][1]) - 1) < 1e-6
                and abs(info["c"][0]) < 1e-6 and abs(info["c"][2] - H) < 1e-6):
            ref = A._ref_signature(part.shape, "edge", i, doc.state.named(part.id))
            fr = A.connector_frame(part.shape, "edge", i)
            return {"name": name, "ref": ref, "kind": "edge", "x_ref": A.connector_x_ref(fr), "flip": False}
    raise AssertionError("edge not found")


def setup(fillet=None, hole=None, edge=False):
    studio, ids = plate_studio(fillet=fillet, hole=hole)
    f = add(studio, "mate_connector", **(side_edge_params(studio) if edge
                                         else connector_params(studio, name="Top")))
    block = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 20, 10, 8).Shape()
    a = assembly(studio, {"block": block})
    a.connector_resolver = own(studio)
    ip = a.insert(src("plate"))
    a.set_fixed(ip, True)
    ib = a.insert(src("block"))
    m = a.make_mate("fastened")
    m["a"] = a.named_connector(ip, f["id"])
    m["b"] = a.connector(ib, "face", bottom_face(block))
    a.add_mate(m)
    return studio, ids, f, a, ip, ib, m


def test_fastened_mate_on_a_studio_owned_connector_solves_and_round_trips(tmp_path):
    studio, ids, f, a, ip, ib, m = setup()
    a.solve()
    assert not a.errors, a.errors
    end = m["a"]
    assert end["named"] == f["id"] and end["name"] == "Top"
    top = studio.state.connectors[f["id"]]["frame"]
    assert np.allclose(end["frame"]["o"], top["o"], atol=1e-9)
    # the block's base lands on the connector (z of the plate top)
    assert abs(a.T(ib)[2, 3] - H) < 1e-6
    a.path = str(tmp_path / "x.fasm")
    a.save()
    d = json.load(open(a.path))
    assert d["mates"][0]["a"]["named"] == f["id"]
    b = A.AssemblyDoc.load(a.path, resolver=a.resolver)
    assert b.mates[0]["a"]["named"] == f["id"] and b.mates[0]["a"]["name"] == "Top"
    b.connector_resolver = own(studio)
    b.refresh()
    assert not b.errors, b.errors
    assert np.allclose(b.T(ib), a.T(ib), atol=1e-6)


def test_hole_into_fillet_keeps_the_mate_green_and_the_instance_still():
    studio, ids, f, a, ip, ib, m = setup(fillet=5.0, hole=(8.0, 8.0), edge=True)
    a.solve()
    assert not a.errors, a.errors
    before = np.array(a.T(ib))
    move_hole(studio, ids["hole"], (2.5, 2.5))
    a.refresh()
    assert not a.errors, a.errors
    assert np.abs(np.array(a.T(ib)) - before).max() < 1e-6


def test_connector_moved_in_the_studio_moves_the_mated_instance():
    studio, ids, f, a, ip, ib, m = setup()
    a.solve()
    assert abs(a.T(ib)[2, 3] - H) < 1e-6
    ex = studio.features[1]
    studio.replace_feature(dict(ex, params=dict(ex["params"], depth=25.0)))
    assert not studio.errors, studio.errors
    a.refresh()
    assert not a.errors, a.errors
    assert abs(a.T(ib)[2, 3] - 25.0) < 1e-6


def test_deleting_the_connector_turns_the_mate_red_naming_it():
    studio, ids, f, a, ip, ib, m = setup()
    a.solve()
    studio.delete_features({f["id"]})
    a.refresh()
    msg = a.errors.get(m["id"], "")
    assert "Top" in msg and "no longer publishes" in msg
    # a studio that cannot be asked keeps the last frame and stays green
    studio2, _, f2, a2, _, _, m2 = setup()
    a2.connector_resolver = lambda source: None
    a2.refresh()
    assert not a2.errors


def test_no_named_ends_solves_as_before():
    studio, ids = plate_studio()
    block = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 20, 10, 8).Shape()

    def build(with_resolver):
        a = assembly(studio, {"block": block})
        if with_resolver:
            a.connector_resolver = own(studio)
        ip = a.insert(src("plate"))
        a.set_fixed(ip, True)
        ib = a.insert(src("block"))
        m = a.make_mate("fastened")
        m["a"] = a.connector(ip, "face", top_face(a.parts[ip].shape))
        m["b"] = a.connector(ib, "face", bottom_face(block))
        a.add_mate(m)
        a.solve()
        assert not a.errors
        return np.array(a.T(ib))
    assert np.array_equal(build(False), build(True))
    assert not any("named" in e for e in json.loads(assembly(studio).to_json())["mates"])


def test_link_resolver_publishes_the_studios_connectors():
    studio, ids = plate_studio()
    f = add(studio, "mate_connector", **connector_params(studio, name="Top"))
    pid = only_part(studio).id
    r = L.LinkResolver(find_open_studio=lambda uid, path: studio if uid == studio.uid else None)
    got = r.resolve_connectors({"kind": "studio", "uid": studio.uid, "part": pid})
    assert [c["id"] for c in got] == [f["id"]] and got[0]["name"] == "Top"
    assert r.resolve_connectors({"kind": "studio", "uid": "nope", "part": pid}) is None
    a = A.AssemblyDoc()
    a.resolver = r.resolve_source                      # connector_resolver left unset: found on the resolver
    ip = a.insert({"kind": "studio", "uid": studio.uid, "part": pid})
    assert a.named_connector(ip, f["id"])["named"] == f["id"]


def test_connector_pick_lists_the_instances_own_connectors():
    from PySide6.QtWidgets import QApplication, QWidget
    from freeshape.ui.assembly_ui import MateDialog
    app = QApplication.instance() or QApplication([])
    studio, ids, f, a, ip, ib, m = setup()

    class VP(QWidget):
        select_filter = None
        last_click = None
        cam = None

        def clear_selection(self):
            pass

        def clear_snaps(self):
            pass

    vp = VP()
    ctl = types.SimpleNamespace(w=types.SimpleNamespace(viewport=vp), doc=a, update_triads=lambda: None)
    d = MateDialog(ctl, a.make_mate("fastened"), True)
    got = d.own_connectors()
    assert [(i, c["id"]) for i, c, _ in got] == [(ip, f["id"])]
    assert np.allclose(got[0][2]["o"], studio.state.connectors[f["id"]]["frame"]["o"])
    d.activate(d.fa)
    d.pick_named(ip, f["id"])
    assert d.mate["a"]["named"] == f["id"]
    assert "Top" in d.fa.text.text()
