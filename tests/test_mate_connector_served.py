"""I10.4: the Mate connector feature in the Browser client, and the Assembly edit that adds an instance's origin
connector.

The feature dialog is driven the way the client drives it: the refs route turns a click (a face, with its click point)
into an item that carries its stamp, POST .../features/dialog opens the dialog, .../dialog/<id>/update sets the
element, the Owner part, Name and Flip, .../accept adds the feature. The last dialog test makes the same connector with
the desktop's own dialog in MainWindow(engine=True) and compares the saved tab file bytes. Each test builds its own
server on a temp copy of an example Document, so each passes alone by node id."""
import http.client
import json
import os
import shutil
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from freeshape import assembly as AS
from freeshape import dialog_schema as DSC
from freeshape import doc_lock
from freeshape import doc_registry as R
from freeshape import kernel as K
from freeshape import serve as SV
from freeshape.engine import process as P
from freeshape.engine import wire

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRACKET = os.path.join(ROOT, "examples", "documents", "bracket")
HINGE = os.path.join(ROOT, "examples", "documents", "hinge")
CLIENT = "client-mate-1"
Z = (0.0, 0.0, 1.0)


class Server:
    """A server holding a copy of an example Document folder (registered and opened)."""

    def __init__(self, tmp, source, locked=False):
        os.makedirs(tmp, exist_ok=True)
        registry = R.DocRegistry(str(tmp / "data" / R.REGISTRY_NAME))
        self.server = SV.make_server(None, 0, registry=registry, root=str(tmp))
        self.port = self.server.port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        assert self.server.wait_started(30)
        self.path = str(tmp / "Doc")
        shutil.copytree(source, self.path)
        with open(os.path.join(self.path, "document.json")) as fh:
            self.doc = json.load(fh)["id"]
        registry.register_created(self.doc, self.path)
        self.lock = doc_lock.acquire(self.path, "desktop") if locked else None     # another program holds it
        self.call("POST", f"/documents/{self.doc}/open")

    def close(self):
        try:
            SV.close_all(self.server, None)
        finally:
            if self.lock is not None:
                self.lock.release()
            P.SPARES.clear()

    def request(self, method, path, body=None):
        h = {"Host": f"127.0.0.1:{self.port}", "X-Client-Id": CLIENT}
        data = None
        if method == "POST":
            h["Content-Type"] = "application/json"
            data = json.dumps(body if body is not None else {}).encode()
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=180)
        try:
            c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for k, v in h.items():
                c.putheader(k, v)
            if data is not None:
                c.putheader("Content-Length", str(len(data)))
            c.endheaders(data)
            r = c.getresponse()
            payload = r.read()
            if r.getheader("Content-Type", "").startswith("application/json"):
                payload = json.loads(payload)
            return r.status, payload
        finally:
            c.close()

    def call(self, method, path, body=None, expect=200):
        status, payload = self.request(method, path, body)
        assert status == expect, (method, path, status, payload)
        return payload

    def tab(self, tab, tail):
        return f"/documents/{self.doc}/tabs/{tab}/{tail}"

    def outputs(self, tab="T1"):
        status, raw = self.request("GET", self.tab(tab, "outputs"))
        assert status == 200, raw
        return wire.from_wire(raw)

    def edit(self, tab, edit, expect=200):
        return self.call("POST", self.tab(tab, "edit"), edit, expect=expect)

    # -- the dialog
    def new(self, picks=None, tab="T1"):
        body = {"type": "mate_connector"} if picks is None else {"type": "mate_connector", "picks": picks}
        return self.call("POST", self.tab(tab, "features/dialog"), body, expect=201)

    def open(self, fid, tab="T1"):
        return self.call("POST", self.tab(tab, f"features/{fid}/dialog"), {}, expect=201)

    def update(self, did, values, tab="T1", expect=200):
        return self.call("POST", self.tab(tab, f"dialog/{did}/update"), {"values": values}, expect=expect)

    def accept(self, did, tab="T1"):
        return self.call("POST", self.tab(tab, f"dialog/{did}/accept"))

    def refs(self, did, pick, tab="T1"):
        """The refs route's answer for one pick of a field that wants an element, read against the dialog's model."""
        body = {"picks": [dict(pick, want=["element"])], "dialog": did}
        got = self.call("POST", self.tab(tab, "refs"), body)["items"][0]
        assert got["ok"], got
        return got

    def connectors(self, tab="T1"):
        return self.outputs(tab).connectors


@pytest.fixture
def bracket(tmp_path):
    s = Server(tmp_path, BRACKET)
    try:
        yield s
    finally:
        s.close()


def field(view, key):
    return next(f for f in view["description"]["fields"] if f["key"] == key)


def value(view, key):
    return view["values"][key]["value"]


def item_of(got):
    """The item a client sets an element field with: what the refs route answered, stamp included."""
    return {"kind": got["kind"], "ref": got["ref"], "stamp": got["stamp"]}


def shape_of(part):
    return K.shape_from_brep(part.brep)


def face_where(part, normal, top=True):
    """The 1-based index of the highest (or lowest) planar face of `part` that looks along `normal`."""
    hits = []
    for i, f in enumerate(K.faces(shape_of(part)), 1):
        info = K.face_info(f)
        if info.get("n") is not None and float(np.dot(info["n"], normal)) > 0.999:
            hits.append((info["c"][2], i))
    return (max if top else min)(hits)[1]


def round_faces(part):
    """1-based indices of the cylindrical faces of `part`."""
    return [i for i, f in enumerate(K.faces(shape_of(part)), 1) if K.face_info(f)["type"] == int(K.GeomAbs_Cylinder)]


def desktop_frame(part, kind, index, click):
    """The frame the desktop computes for a click on face `index` (1-based) of `part`."""
    return AS.connector_frame(shape_of(part), kind, index - 1, click)


# ---------------------------------------------------------------------------------------------------------------------


def test_types_reports_mate_connector_standard(bracket):
    types = {t["type"]: t for t in bracket.call("GET", bracket.tab("T1", "features/types"))["types"]}
    assert types["mate_connector"]["standard"] is True and types["mate_connector"]["can_start"] is True
    assert types["hole"]["standard"] is False            # the others that have a hand-built row are as before
    assert "mate_connector" not in DSC.custom_feature_types()


def test_the_dialog_describes_four_standard_rows_and_the_owner_choice_lists_the_parts(bracket):
    s = bracket
    (part,) = s.outputs().parts
    got = s.new()
    assert got["description"]["custom"] == [] and all(f["standard"] for f in got["description"]["fields"])
    assert [(f["key"], f["kind"]) for f in got["description"]["fields"]] == [
        ("name", "text"), ("element", "sel"), ("owner", "choice"), ("flip", "check")]
    assert field(got, "element")["want"] == ["element"] and field(got, "element")["multi"] is False
    assert "click" in field(got, "element")["label"] and "hover" not in field(got, "element")["label"]
    owner = field(got, "owner")
    assert owner["options"][:2] == [["", DSC.OWNER_SAME], [part.id, part.name]]       # then the sketches, as the desktop
    assert all(o[1].endswith("(sketch)") for o in owner["options"][2:]) and len(owner["options"]) > 2
    assert value(got, "owner") == "" and value(got, "flip") is False and value(got, "name") == ""
    up = s.update(got["dialog"], {"owner": part.id, "flip": True, "name": "Pin"})
    assert not up["errors"] and value(up, "owner") == part.id and value(up, "flip") is True
    assert value(up, "name") == "Pin"
    up = s.update(got["dialog"], {"owner": ""})
    assert value(up, "owner") == ""                      # back to the default: no owner chosen
    s.update(got["dialog"], {"owner": "nope"}, expect=400)
    s.update(got["dialog"], {"owner": 3}, expect=400)


def test_a_studio_with_no_parts_offers_only_the_default_owner(tmp_path):
    s = Server(tmp_path, BRACKET)
    try:
        s.edit("T1", {"op": "delete_features", "ids": [f"F{n}" for n in range(1, 9)]})
        assert not s.outputs().parts
        got = s.new()
        assert field(got, "owner")["options"] == [["", DSC.OWNER_SAME]] and value(got, "owner") == ""
    finally:
        s.close()


def test_a_top_face_pick_with_its_click_publishes_a_connector_on_that_face_where_the_desktop_puts_it(bracket):
    s = bracket
    (part,) = s.outputs().parts
    top = face_where(part, Z)
    info = K.face_info(K.faces(shape_of(part))[top - 1])
    click = [float(info["c"][0]) + 1.5, float(info["c"][1]) + 0.5, float(info["c"][2])]
    got = s.new()
    pick = {"part": part.id, "kind": "face", "index": top, "point": click}
    answer = s.refs(got["dialog"], pick)
    assert answer["kind"] == "face" and answer["stamp"]["click"] == click and answer["stamp"]["snap"] is None
    assert len(answer["stamp"]["x_ref"]) == 2
    up = s.update(got["dialog"], {"element": [item_of(answer)], "name": "Top"})
    assert not up["errors"] and len(up["fields"]["element"]) == 1
    s.accept(got["dialog"])
    (c,) = s.connectors()
    want = desktop_frame(part, "face", top, click)
    assert np.allclose(c.frame["o"], want["o"], atol=1e-6)
    assert abs(c.frame["o"][2] - float(info["c"][2])) < 1e-6          # on the top face
    assert np.allclose(c.frame["z"], want["z"], atol=1e-6) and c.name == "Top" and c.part == part.id
    (f,) = [f for f in s.outputs().features if f.type == "mate_connector"]
    assert f.error is None


def test_the_click_point_chooses_the_end_of_a_round_face(bracket):
    s = bracket
    (part,) = s.outputs().parts
    face = round_faces(part)[-1]
    p0, d, r, zmin, zmax, _hole = K.cylinder_face_frame(K.faces(shape_of(part))[face - 1])
    heights = []
    for frac in (0.1, 0.9):
        click = [float(c) for c in np.asarray(p0) + np.asarray(d) * (zmin + (zmax - zmin) * frac)
                 + np.array([r, 0.0, 0.0])]
        got = s.new()
        answer = s.refs(got["dialog"], {"part": part.id, "kind": "face", "index": face, "point": click})
        s.update(got["dialog"], {"element": [item_of(answer)]})
        s.accept(got["dialog"])
        want = desktop_frame(part, "face", face, click)
        c = s.connectors()[-1]
        assert np.allclose(c.frame["o"], want["o"], atol=1e-6)
        heights.append(c.frame["o"][2])
    assert abs(heights[0] - heights[1]) > 0.5            # two clicks, two places on the same face


def test_an_edge_pick_carries_a_stamp_without_a_click_and_a_pick_without_a_stamp_clears_the_old_one(bracket):
    s = bracket
    (part,) = s.outputs().parts
    top = face_where(part, Z)
    got = s.new()
    edge = next(i for i, e in enumerate(K.edges(shape_of(part)), 1) if "d" in K.edge_info(e))
    ans_edge = s.refs(got["dialog"], {"part": part.id, "kind": "edge", "index": edge})
    assert ans_edge["kind"] == "edge" and ans_edge["stamp"]["click"] is None
    ans_face = s.refs(got["dialog"], {"part": part.id, "kind": "face", "index": top, "point": [1.0, 2.0, 45.0]})
    s.update(got["dialog"], {"element": [item_of(ans_face)]})
    s.update(got["dialog"], {"element": [{"kind": "edge", "ref": ans_edge["ref"]}]})      # no stamp
    s.accept(got["dialog"])
    s.call("POST", f"/documents/{s.doc}/save")
    (stored,) = [f for f in _tab_text(s.path)["features"] if f["type"] == "mate_connector"]
    assert stored["params"]["kind"] == "edge" and stored["params"]["click"] is None     # the face's click did not stay
    assert stored["params"]["snap"] is None and stored["params"]["x_ref"] is None
    assert s.connectors()[0].part == part.id


def test_a_bad_stamp_is_refused_with_its_row_named(bracket):
    s = bracket
    (part,) = s.outputs().parts
    got = s.new()
    ans = s.refs(got["dialog"], {"part": part.id, "kind": "face", "index": face_where(part, Z)})
    for stamp in ({"click": [1, 2], "snap": None, "x_ref": None}, {"click": None, "snap": {"p": [0, 0, 0]},
                                                                   "x_ref": None},
                  {"click": None, "snap": None}, {"click": None, "snap": None, "x_ref": [float("inf"), 0]},
                  "no"):
        body = {"kind": ans["kind"], "ref": ans["ref"], "stamp": stamp}
        if isinstance(stamp, dict) and any(isinstance(v, list) and v and v[0] == float("inf") for v in stamp.values()):
            continue                                     # JSON cannot carry it
        r = s.update(got["dialog"], {"element": [body]}, expect=400)
        assert "stamp" in json.dumps(r)


def test_an_owner_other_than_the_picked_part_and_flip_are_stored_and_a_deleted_owner_shows_the_default(bracket):
    s = bracket
    (part,) = s.outputs().parts
    got = s.new([{"part": part.id, "kind": "face", "index": face_where(part, Z)}])      # started with a pick
    assert len(got["fields"]["element"]) == 1
    s.update(got["dialog"], {"owner": part.id, "flip": True})
    s.accept(got["dialog"])
    (c,) = s.connectors()
    assert c.part == part.id and c.frame["z"][2] < -0.99          # flipped: Z points down from the top face
    (f,) = [f for f in s.outputs().features if f.type == "mate_connector"]
    again = s.open(f.id)
    assert value(again, "owner") == part.id and value(again, "flip") is True
    assert field(again, "owner")["options"][1][0] == part.id


def test_an_origin_connector_edited_in_the_browser_says_select_and_keeps_its_owner(bracket):
    s = bracket
    (part,) = s.outputs().parts
    p = {"name": "Home", "ref": {"origin": True}, "kind": "origin", "click": None, "snap": None, "x_ref": None,
         "flip": False, "owner": part.id}
    s.edit("T1", {"op": "add_feature", "feature": {"id": "F90", "type": "mate_connector", "name": "Home",
                                                   "suppressed": False, "params": p}})
    got = s.open("F90")
    assert field(got, "owner")["options"][0] == ["", DSC.OWNER_PICK] and value(got, "owner") == part.id
    assert got["fields"]["element"] == []                # the origin is not an element


def test_new_connector_shows_in_the_outputs_at_once_and_one_undo_removes_it(bracket):
    s = bracket
    (part,) = s.outputs().parts
    got = s.new([{"part": part.id, "kind": "face", "index": face_where(part, Z)}])
    s.accept(got["dialog"])
    assert len(s.connectors()) == 1
    s.edit("T1", {"op": "undo"})
    assert s.connectors() == []


# ------------------------------------------------------------------------------------------------ saved bytes


def _desktop_connector(tmp_path, path, part, index, click, name):
    """Make the connector with the desktop's own dialog in MainWindow(engine=True), driven through its widgets, then
    save: the click is stamped as a mouse click on the face stamps it (`_stamp_connector`)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QMessageBox
    from freeshape.ui import dialogs as DL
    from freeshape.ui import home as H
    from freeshape.ui import mainwindow as MW
    mp = pytest.MonkeyPatch()
    root = tmp_path / "desk"
    for d in ("autosave", "thumbs", "appdata"):
        os.makedirs(root / d, exist_ok=True)
    mp.setenv("FREESHAPE_DATA_DIR", str(root / "appdata"))
    mp.setenv("FREESHAPE_NO_RECOVERY", "1")
    mp.setenv("FREESHAPE_NO_HOME", "1")
    app = QApplication.instance() or QApplication([])
    mp.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(root / "autosave")))
    mp.setattr(MW.MainWindow, "autosave_dir", classmethod(lambda cls: str(root / "autosave")))
    mp.setattr(H, "thumbs_dir", lambda: str(root / "thumbs"))
    for n in ("warning", "critical", "information"):
        mp.setattr(QMessageBox, n, lambda *a, **k: QMessageBox.Ok)
    w = MW.MainWindow(engine=True)
    try:
        w.settings = QSettings(str(root / "settings.ini"), QSettings.IniFormat)
        w.open_document(path)
        assert w.studio_engine.settle(240)
        w.start_feature("mate_connector")
        d = w.dialog
        pick = SimpleNamespace(kind="face", key=(part, index - 1))
        item = DL.pick_to_item(w.viewport, pick, {"element"}, d.base_state)
        d.fields["element"].items = [item]
        d._fields_changed()
        d._stamp_connector(item, pick, click, None)
        d._set("name", name)
        w.commit_dialog()
        assert w.studio_engine.settle(120)
        assert [f["type"] for f in w.doc.features][-1] == "mate_connector"
        app.processEvents()
        assert w.save()
        app.processEvents()
        return w.folder.path
    finally:
        w.studio_engine.shutdown()
        w.close()
        P.SPARES.clear()
        mp.undo()


def _plain_ref(x):
    """A stored reference without its Element name, numbers rounded to 1e-5."""
    if isinstance(x, dict):
        return {k: _plain_ref(v) for k, v in x.items() if k != "name"}
    if isinstance(x, list):
        return [_plain_ref(v) for v in x]
    return round(x, 5) if isinstance(x, float) else x


def _tab_text(path):
    with open(os.path.join(path, "document.json")) as fh:
        entry = next(t for t in json.load(fh)["tabs"] if t["id"] == "T1")
    with open(os.path.join(path, entry["file"])) as fh:
        return json.load(fh)


def test_a_connector_made_in_the_browser_saves_the_bytes_the_desktop_saves(tmp_path):
    web, desk = tmp_path / "web", tmp_path / "desk0"
    s = Server(web, BRACKET)
    shutil.copytree(BRACKET, str(desk))
    try:
        (part,) = s.outputs().parts
        top = face_where(part, Z)
        click = [4.0, 1.0, 45.0]
        got = s.new()
        ans = s.refs(got["dialog"], {"part": part.id, "kind": "face", "index": top, "point": click})
        s.update(got["dialog"], {"element": [item_of(ans)], "name": "Top pin"})
        s.accept(got["dialog"])
        s.call("POST", f"/documents/{s.doc}/save")
        web_path = s.path
    finally:
        s.close()
    desk_path = _desktop_connector(tmp_path, str(desk), part.id, top, click, "Top pin")
    made, want = _tab_text(web_path), _tab_text(desk_path)
    assert [f["type"] for f in made["features"]][-1] == "mate_connector"
    new = made["features"][-1]
    assert next(iter(new)) == "id" and new["params"]["click"] == click and new["params"]["x_ref"] is not None
    # the desktop's save also restamps the bracket's older features with element names; the new feature is the test
    # the reference inside the params is made from the shape the maker holds: the server's worker has the live one
    # and its Element name, the desktop's viewport the one that came over the wire (bounding boxes differ in the
    # 7th decimal) and, offscreen, no name map. Everything else is byte for byte: keys, their order, the stamp.
    got, ref = json.loads(json.dumps(new)), json.loads(json.dumps(want["features"][-1]))
    for f in (got, ref):
        f["params"]["ref"] = _plain_ref(f["params"]["ref"])
    assert json.dumps(got, indent=1) == json.dumps(ref, indent=1)
    assert list(new) == list(want["features"][-1]) and list(new["params"]) == list(want["features"][-1]["params"])


# ------------------------------------------------------------------------------------------------ the Assembly edit


def _instance(out, n=0):
    return out.instances[n]


def test_add_origin_connector_adds_one_at_the_part_origin_a_second_call_adds_nothing_and_undo_removes_it(tmp_path):
    s = Server(tmp_path, HINGE)
    try:
        before = s.outputs("T2")
        inst = _instance(before)
        assert inst.connectors == []
        s.edit("T2", {"op": "add_origin_connector", "instance": inst.id})
        out = s.outputs("T2")
        got = next(i for i in out.instances if i.id == inst.id)
        (c,) = got.connectors
        assert c.kind == "origin" and c.name == f"{inst.name} origin"
        assert np.allclose(c.frame["o"], [0, 0, 0]) and np.allclose(c.frame["z"], [0, 0, 1])
        T = np.asarray(got.T)
        assert np.allclose(c.world["o"], T[:3, 3], atol=1e-9)
        assert all(i.connectors == [] for i in out.instances if i.id != inst.id)
        s.edit("T2", {"op": "add_origin_connector", "instance": inst.id, "name": "Again"})       # nothing new
        again = next(i for i in s.outputs("T2").instances if i.id == inst.id)
        assert [(x.id, x.name) for x in again.connectors] == [(c.id, c.name)]
        s.edit("T2", {"op": "undo"})                      # the one step the first call made
        assert all(i.connectors == [] for i in s.outputs("T2").instances)
        s.edit("T2", {"op": "redo"})
        assert len(next(i for i in s.outputs("T2").instances if i.id == inst.id).connectors) == 1
    finally:
        s.close()


def test_add_origin_connector_takes_a_name_and_refuses_an_unknown_instance_changing_nothing(tmp_path):
    s = Server(tmp_path, HINGE)
    try:
        inst = _instance(s.outputs("T2"))
        before = s.outputs("T2").revision
        for bad in ({"instance": "I404"}, {"instance": 5}, {}, {"instance": inst.id, "name": ""},
                    {"instance": inst.id, "name": 7}):
            status, body = s.request("POST", s.tab("T2", "edit"), {"op": "add_origin_connector", **bad})
            assert 400 <= status < 500, (bad, status, body)
        out = s.outputs("T2")
        assert out.revision == before and all(i.connectors == [] for i in out.instances)
        s.edit("T2", {"op": "add_origin_connector", "instance": inst.id, "name": "Pivot"})
        (c,) = next(i for i in s.outputs("T2").instances if i.id == inst.id).connectors
        assert c.name == "Pivot"
    finally:
        s.close()


def test_add_origin_connector_is_refused_on_a_read_only_document(tmp_path):
    s = Server(tmp_path, HINGE, locked=True)
    try:
        assert s.call("GET", f"/documents/{s.doc}/tabs")["read_only"] is True
        inst = _instance(s.outputs("T2"))
        status, body = s.request("POST", s.tab("T2", "edit"), {"op": "add_origin_connector", "instance": inst.id})
        assert status == 423, (status, body)
        assert all(i.connectors == [] for i in s.outputs("T2").instances)
    finally:
        s.close()
