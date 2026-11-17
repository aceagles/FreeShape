"""I12.2: the Canvas dialog, image upload and calibration over HTTP.

Each test stands up its own server on a temp copy of the bracket Document and drives it the way the Browser client
does: POST .../images?name= with the image bytes, POST .../features/dialog {"type": "canvas", "params": {blob,
filename}}, .../dialog/<id>/update, .../calibrate, .../accept, GET .../images/<sha256>. Every test builds its own
server, so each passes alone by node id."""
import hashlib
import http.client
import json
import math
import os
import shutil
import struct
import threading
import zlib

import pytest

from conftest import EXAMPLES_DIR
from freeshape import blobstore
from freeshape import doc_lock
from freeshape import doc_registry as R
from freeshape import image_info as II
from freeshape import kernel as K
from freeshape import serve as SV
from freeshape.engine import process as P
from freeshape.engine import wire
from test_canvas_feature import GIF, add_canvas, jpeg, png

BRACKET = os.path.join(EXAMPLES_DIR, "documents", "bracket")
CLIENT = "client-canvas-1"
OTHER = "client-canvas-2"
MM = 25.4 / 96                       # an image with no resolution: 96 dpi


class Server:
    """A server holding a copy of the bracket Document (registered and opened)."""

    def __init__(self, tmp_path, locked=False):
        self.root = str(tmp_path / "Documents")
        os.makedirs(self.root)
        registry = R.DocRegistry(str(tmp_path / "data" / R.REGISTRY_NAME))
        self.server = SV.make_server(None, 0, registry=registry, root=self.root)
        self.port = self.server.port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        assert self.server.wait_started(30)
        self.path = os.path.join(self.root, "Doc")
        shutil.copytree(BRACKET, self.path)
        with open(os.path.join(self.path, "document.json")) as fh:
            self.doc = json.load(fh)["id"]
        registry.register_created(self.doc, self.path)
        self.lock = doc_lock.acquire(self.path, "desktop") if locked else None     # another program holds the Document
        self.call("POST", f"/documents/{self.doc}/open")

    def close(self):
        try:
            SV.close_all(self.server, None)
        finally:
            P.SPARES.clear()
            if self.lock is not None:
                self.lock.release()

    def request(self, method, path, body=None, client=CLIENT, ctype=None, headers=None, send=True):
        """`body` bytes are sent as ctype (default octet-stream), a dict as JSON. `send` False sends the headers of a
        body (Content-Length from `headers`) and none of it."""
        h = {"Host": f"127.0.0.1:{self.port}"}
        if client is not None:
            h["X-Client-Id"] = client
        data = None
        if isinstance(body, dict) or (method == "POST" and body is None):
            h["Content-Type"] = "application/json"
            data = json.dumps(body or {}).encode()
        elif body is not None:
            h["Content-Type"] = ctype or "application/octet-stream"
            data = body
        h.update(headers or {})
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=180)
        try:
            c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for k, v in h.items():
                c.putheader(k, v)
            if data is not None and "Content-Length" not in h:
                c.putheader("Content-Length", str(len(data)))
            c.endheaders(data if send else None)
            r = c.getresponse()
            payload = r.read()
            if r.getheader("Content-Type", "").startswith("application/json"):
                payload = json.loads(payload)
            return r.status, payload, r
        finally:
            c.close()

    def call(self, method, path, body=None, expect=200, **kw):
        status, payload, _ = self.request(method, path, body, **kw)
        assert status == expect, (method, path, status, payload)
        return payload

    def tab(self, tail, tab="T1"):
        return f"/documents/{self.doc}/tabs/{tab}/{tail}"

    def outputs(self, tab="T1"):
        status, raw, _ = self.request("GET", self.tab("outputs", tab))
        assert status == 200, raw
        return wire.from_wire(raw)

    def edit(self, edit, tab="T1"):
        return self.call("POST", self.tab("edit", tab), edit)

    def upload(self, data, name="photo.png", expect=200, **kw):
        return self.call("POST", self.tab("images?name=" + name), data, expect=expect, **kw)

    def image(self, key, **kw):
        return self.request("GET", self.tab(f"images/{key}"), **kw)

    def new(self, up, expect=201):
        return self.call("POST", self.tab("features/dialog"),
                         {"type": "canvas", "params": {"blob": up["blob"], "filename": up["filename"]}}, expect=expect)

    def update(self, did, values):
        return self.call("POST", self.tab(f"dialog/{did}/update"), {"values": values})

    def calibrate(self, did, body, expect=200):
        return self.call("POST", self.tab(f"dialog/{did}/calibrate"), body, expect=expect)

    def accept(self, did):
        return self.call("POST", self.tab(f"dialog/{did}/accept"))

    def canvases(self, tab="T1"):
        return self.outputs(tab).canvases


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    try:
        yield s
    finally:
        s.close()


def value(view, key):
    return view["values"][key]["value"]


def field(view, key):
    return next(f for f in view["description"]["fields"] if f["key"] == key)


def png_claiming(w, h):
    """A PNG whose header states w x h pixels and whose data is one pixel: nothing decodes it, so it costs nothing."""
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\x80")) + chunk(b"IEND", b""))


# ------------------------------------------------------------------------------------------------ upload, dialog


def test_upload_a_png_open_a_canvas_dialog_accept_calibrate_and_undo(srv):
    s = srv
    data = png(200, 100)
    parts = [p.id for p in s.outputs().parts]
    up = s.upload(data)
    assert up["blob"] == hashlib.sha256(data).hexdigest() and up["format"] == "png"
    assert up["pixels"] == [200, 100] and up["filename"] == "photo.png"
    assert up["width"] == pytest.approx(200 * MM) and up["height"] == pytest.approx(100 * MM)
    assert s.outputs().canvases == []                                  # an upload is not an edit

    got = s.new(up)
    assert got["type"] == "canvas" and got["feature"] is None
    assert field(got, "_info")["text"] == "photo.png"
    assert value(got, "width") == pytest.approx(up["width"]) and value(got, "height") == pytest.approx(up["height"])
    assert got["fields"]["plane_sel"][0]["kind"] == "plane" and got["fields"]["plane_sel"][0]["ref"] == \
        {"kind": "std", "name": "Top"}
    s.accept(got["dialog"])
    (c,) = s.canvases()
    assert c.blob == up["blob"] and c.filename == "photo.png" and c.pixels == [200, 100]
    assert (c.width, c.height) == pytest.approx((up["width"], up["height"]))
    assert c.normal == [0.0, 0.0, 1.0] and c.opacity == 50.0
    assert [p.id for p in s.outputs().parts] == parts                  # it changes no part
    (feat,) = [f for f in s.outputs().features if f.type == "canvas"]
    assert feat.error is None

    again = s.call("POST", s.tab(f"features/{feat.id}/dialog"), {}, expect=201)
    assert value(again, "width") == pytest.approx(up["width"])
    # a horizontal line of 20 mm (-10 .. 10 along the image's x) that really is 50 mm: everything grows by 2.5
    up2 = s.calibrate(again["dialog"], {"p0": [-10, 0], "p1": [10, 0], "distance": 50})
    assert not up2["errors"] and up2["preview"] is not None
    assert value(up2, "width") == pytest.approx(up["width"] * 2.5) and value(up2, "height") == \
        pytest.approx(up["height"] * 2.5)
    assert value(up2, "x") == 0 and value(up2, "y") == 0 and value(up2, "rotation") == 0
    s.accept(again["dialog"])
    (c,) = s.canvases()
    assert (c.width, c.height) == pytest.approx((up["width"] * 2.5, up["height"] * 2.5))
    s.edit({"op": "undo"})
    (c,) = s.canvases()
    assert (c.width, c.height) == pytest.approx((up["width"], up["height"]))        # one undo: the old size


def test_calibrate_with_centre_and_snap_changes_x_y_and_rotation(srv):
    s = srv
    up = s.upload(png(200, 100, dpi=25.4))                           # 1 pixel = 1 mm: 200 x 100 mm
    assert (up["width"], up["height"]) == pytest.approx((200, 100))
    got = s.new(up)
    # a line at 40 degrees from (0, 0) to (20 cos 40, 20 sin 40), really 40 mm long
    p1 = [20 * math.cos(math.radians(40)), 20 * math.sin(math.radians(40))]
    done = s.calibrate(got["dialog"], {"p0": [0, 0], "p1": p1, "distance": 40, "snap": True, "centre": True})
    assert value(done, "width") == pytest.approx(400) and value(done, "height") == pytest.approx(200)
    assert value(done, "rotation") == pytest.approx(5)                 # the line was at 40 degrees: it is on 45 now
    s.accept(got["dialog"])
    (c,) = s.canvases()
    # the line's midpoint is on the plane origin: the canvas centre is where the scaled midpoint was, backwards
    mid = (c.origin[0] + (p1[0] / 2 * 2) * c.xdir[0] + (p1[1] / 2 * 2) * c.ydir[0],
           c.origin[1] + (p1[0] / 2 * 2) * c.xdir[1] + (p1[1] / 2 * 2) * c.ydir[1])
    assert mid == pytest.approx((0, 0), abs=1e-6)


def test_a_calibration_the_dialog_cannot_make_is_a_400_and_changes_nothing(srv):
    s = srv
    got = s.new(s.upload(png(20, 20)))
    before = value(got, "width")
    for body in ({"p0": [1, 1], "p1": [1, 1], "distance": 10}, {"p0": [0, 0], "p1": [1, 0], "distance": 0},
                 {"p0": [0, 0], "p1": [1, 0], "distance": -3}, {"p0": [0, 0], "p1": [1, 0]},
                 {"p0": [0], "p1": [1, 0], "distance": 3}, {"p0": [0, 0], "p1": [1, 0], "distance": 3, "snap": 1},
                 {"p0": [0, 0], "p1": [1, 0], "distance": 3, "extra": 1}):
        s.calibrate(got["dialog"], body, expect=400)
    s.update(got["dialog"], {"keep_aspect": False})
    assert value(s.update(got["dialog"], {"x": 0}), "width") == pytest.approx(before)
    # not a Canvas dialog
    other = s.call("POST", s.tab("features/dialog"), {"type": "extrude"}, expect=201)
    s.calibrate(other["dialog"], {"p0": [0, 0], "p1": [1, 0], "distance": 3}, expect=400)


def test_the_types_route_reports_canvas_as_standard(srv):
    types = {t["type"]: t for t in srv.call("GET", srv.tab("features/types"))["types"]}
    assert types["canvas"]["standard"] is True and types["canvas"]["label"] == "Canvas"
    got = srv.new(srv.upload(png(20, 10)))
    assert got["description"]["custom"] == [] and all(f["standard"] for f in got["description"]["fields"])
    keys = [f["key"] for f in got["description"]["fields"]]
    assert keys == ["_info", "plane_sel", "width", "height", "x", "y", "rotation", "opacity", "keep_aspect",
                    "flip_h", "flip_v", "selectable"]
    f = field(got, "plane_sel")
    assert f["kind"] == "sel" and f["multi"] is False and "plane" in f["want"]
    assert (field(got, "width")["min"], field(got, "opacity")["min"], field(got, "opacity")["max"]) == (1e-6, 0, 100)
    assert field(got, "x")["kind"] == "num" and field(got, "x")["min"] is None
    assert field(got, "rotation")["unit"] == "deg"


def test_keep_aspect_ratio_couples_width_and_height(srv):
    s = srv
    got = s.new(s.upload(png(200, 100)))
    d = got["dialog"]
    assert value(got, "keep_aspect") is True
    up = s.update(d, {"width": 100})
    assert value(up, "width") == 100 and value(up, "height") == pytest.approx(50)
    up = s.update(d, {"height": 10})
    assert value(up, "height") == 10 and value(up, "width") == pytest.approx(20)
    assert up["values"]["width"]["text"] == "20"                         # the row shows the new number
    up = s.update(d, {"keep_aspect": False})
    up = s.update(d, {"width": 40})
    assert value(up, "width") == 40 and value(up, "height") == 10        # off: the other stays
    up = s.update(d, {"keep_aspect": True, "height": 20})
    assert value(up, "height") == 20 and value(up, "width") == pytest.approx(80)    # the ratio of 40 : 10


def test_a_size_typed_with_a_variable_also_couples(srv):
    s = srv
    got = s.new(s.upload(png(200, 100)))
    up = s.update(got["dialog"], {"width": "10 mm * 3"})
    assert value(up, "width") == pytest.approx(30) and value(up, "height") == pytest.approx(15)


def test_numbers_are_checked(srv):
    s = srv
    got = s.new(s.upload(png(20, 10)))
    d = got["dialog"]
    up = s.update(d, {"opacity": 101})
    assert up["errors"]["opacity"] == "the value must be at most 100" and value(up, "opacity") == 50
    up = s.update(d, {"opacity": 0})
    assert not up["errors"] and value(up, "opacity") == 0
    up = s.update(d, {"width": 0})
    assert "width" in up["errors"]
    up = s.update(d, {"x": -12.5, "y": 3, "rotation": "90 deg", "flip_h": True, "selectable": False})
    assert not up["errors"] and value(up, "x") == -12.5 and value(up, "flip_h") is True
    assert s.call("POST", s.tab(f"dialog/{d}/update"), {"values": {"blob": "x"}}, expect=400)


def test_a_flat_face_is_taken_and_a_curved_one_is_refused_with_a_message(srv):
    s = srv
    (part,) = s.outputs().parts
    shape = K.shape_from_brep(part.brep)
    faces = K.faces(shape)
    cyl = next(i for i, f in enumerate(faces, 1) if K.face_info(f)["type"] == int(K.GeomAbs_Cylinder))
    top = next(i for i, f in enumerate(faces, 1) if (K.face_info(f).get("n") or [0, 0, 0])[2] > 0.999)
    got = s.new(s.upload(png(40, 40)))
    pick = lambda i: {"part": part.id, "kind": "face", "index": i, "want": ["plane", "face"]}   # noqa: E731
    bad = s.call("POST", s.tab("refs"), {"picks": [dict(pick(cyl), want=field(got, "plane_sel")["want"])]})["items"][0]
    assert bad["ok"] is False and bad["code"] == "not_flat" and "not flat" in bad["reason"]
    item = s.call("POST", s.tab("refs"), {"picks": [dict(pick(top), want=field(got, "plane_sel")["want"])]})["items"][0]
    assert item["ok"] is True and item["kind"] == "plane"
    up = s.update(got["dialog"], {"plane_sel": [{"kind": item["kind"], "ref": item["ref"]}]})
    assert not up["errors"] and up["fields"]["plane_sel"][0]["kind"] == "plane"
    s.accept(got["dialog"])
    (c,) = s.canvases()
    (feat,) = [f for f in s.outputs().features if f.type == "canvas"]
    assert feat.error is None
    assert c.normal == pytest.approx([0, 0, 1]) and c.origin[2] == pytest.approx(K.bbox(shape)[1][2])   # on the top
    saved = s.call("POST", s.tab(f"features/{feat.id}/dialog"), {}, expect=201)
    plane = saved["fields"]["plane_sel"][0]
    assert plane["kind"] == "plane" and set(plane["ref"]) == {"kind", "face"}      # the face reference alone


# ------------------------------------------------------------------------------------------------ the routes


def test_other_images_and_big_bodies_are_refused_and_leave_nothing_behind(srv):
    s = srv
    for data, what in ((GIF, "GIF"), (b"", "empty"), (b"hello", "text"), (png(10, 10)[:-20], "cut short"),
                       (jpeg(10, 10)[:-2], "no end marker")):
        status, body, _ = s.request("POST", s.tab("images?name=x.png"), data)
        assert status == 415, (what, status, body)
        assert "error" in body and body["error"]
        key = hashlib.sha256(data).hexdigest()
        assert s.image(key)[0] == 404                                  # nothing held, nothing to serve
    big = 20 * 1024 * 1024 + 1024 * 1024                               # 21 MiB, said in the header; 413 before any is read
    status, body, _ = s.request("POST", s.tab("images?name=x.png"), b"x", headers={"Content-Length": str(big)}, send=False)
    assert status == 413 and "error" in body
    # over the pixel limit: the header says so, nothing is decoded
    over = png_claiming(8000, 8000)
    status, body, _ = s.request("POST", s.tab("images?name=x.png"), over)
    assert status == 413 and "million pixels" in body["error"]
    assert s.image(hashlib.sha256(over).hexdigest())[0] == 404
    # the limit itself is accepted
    ok = png_claiming(II.MAX_PIXELS // 5000, 5000)
    assert s.upload(ok)["pixels"] == [II.MAX_PIXELS // 5000, 5000]
    # the request itself
    assert s.request("POST", s.tab("images"), b"x", ctype="text/plain")[0] == 415
    assert s.request("POST", s.tab("images"), png(4, 4), headers={"Transfer-Encoding": "chunked"}, send=False)[0] in (
        400, 411)
    assert s.request("POST", s.tab("images"), png(4, 4), client=None)[0] == 400       # a client id is required
    assert s.request("POST", s.tab("images"), png(4, 4), headers={"Content-Length": "abc"}, send=False)[0] == 400
    assert s.outputs().canvases == []


def test_the_served_image_has_its_content_type_and_nosniff(srv):
    s = srv
    p, j = png(30, 20), jpeg(60, 40, dpi=300)
    up_p, up_j = s.upload(p), s.upload(j, name="..\\evil/./shot.JPG")
    assert up_j["format"] == "jpeg" and up_j["pixels"] == [60, 40] and up_j["filename"] == "shot.JPG"
    assert up_j["width"] == pytest.approx(60 * 25.4 / 300)
    for up, data, ctype in ((up_p, p, "image/png"), (up_j, j, "image/jpeg")):
        status, body, r = s.image(up["blob"])
        assert status == 200 and body == data
        assert r.getheader("Content-Type") == ctype and r.getheader("X-Content-Type-Options") == "nosniff"
    assert s.image(up_p["blob"], client=OTHER)[0] == 404              # another client did not upload it
    assert s.image(up_p["blob"], client=None)[0] == 404               # and no client: only a canvas's image is served


def test_an_image_of_a_canvas_is_served_to_anyone_and_an_unknown_key_is_404(srv):
    s = srv
    data = png(30, 20)
    up = s.upload(data)
    s.accept(s.new(up)["dialog"])
    for client in (CLIENT, OTHER, None):
        status, body, r = s.image(up["blob"], client=client)
        assert status == 200 and body == data and r.getheader("Content-Type") == "image/png"
    assert s.image("0" * 64)[0] == 404
    assert s.image(hashlib.sha256(b"never uploaded").hexdigest())[0] == 404
    # the key of a blob of the Document that no canvas names (the bracket's own tab folder has none; a feature's
    # stored build does): not served as an image either
    for blob in os.listdir(os.path.join(s.path, "T1", "blobs")) if os.path.isdir(os.path.join(s.path, "T1", "blobs")) \
            else []:
        if blob != up["blob"]:
            assert s.image(blob)[0] == 404
    assert s.image("zz")[0] == 404 and s.image("A" * 64)[0] == 404      # not a sha256 key: no such route
    assert s.request("POST", s.tab(f"images/{up['blob']}"), b"x")[0] == 405
    # only a Part Studio tab takes images
    asm = s.call("POST", f"/documents/{s.doc}/tabs", {"kind": "assembly", "name": "A"}, expect=201)["tab"]["id"]
    assert s.request("POST", s.tab("images", asm), png(4, 4))[0] == 400
    assert s.image(up["blob"])[0] == 200


def test_a_dialog_for_an_image_only_opens_for_this_clients_upload(srv):
    s = srv
    up = s.upload(png(20, 20))
    s.call("POST", s.tab("features/dialog"), {"type": "canvas", "params": {"blob": up["blob"], "filename": "a"}},
           expect=201, client=CLIENT)
    s.call("POST", s.tab("features/dialog"), {"type": "canvas", "params": {"blob": up["blob"], "filename": "a"}},
           expect=404, client=OTHER)
    s.call("POST", s.tab("features/dialog"), {"type": "canvas", "params": {"blob": "0" * 64, "filename": "a"}},
           expect=404)
    s.call("POST", s.tab("features/dialog"), {"type": "canvas", "params": {"blob": up["blob"]}}, expect=400)
    s.call("POST", s.tab("features/dialog"), {"type": "canvas", "params": {"blob": 3, "filename": "a"}}, expect=400)
    s.call("POST", s.tab("features/dialog"), {"type": "extrude", "params": {"blob": up["blob"], "filename": "a"}},
           expect=400)
    # a canvas with no image: the dialog opens, the engine refuses to build it, and its row says to upload one
    bare = s.call("POST", s.tab("features/dialog"), {"type": "canvas"}, expect=201)
    assert field(bare, "_info")["text"] == "No image: upload one first"


def test_a_read_only_document_takes_no_upload(tmp_path):
    s = Server(tmp_path, locked=True)
    try:
        assert s.call("GET", f"/documents/{s.doc}/tabs")["read_only"] is True
        status, body, _ = s.request("POST", s.tab("images?name=a.png"), png(4, 4))
        assert status == 423 and body["holder"]["program"] == "desktop"
    finally:
        s.close()


# ------------------------------------------------------------------------------------------------ saved bytes


def test_a_canvas_made_in_the_browser_saves_what_the_feature_saves_for_the_same_choices(tmp_path):
    """The served dialog and I12.1's own path (the image inline, the way a client adds it) write the same tab file."""
    data = png(200, 100)
    s = Server(tmp_path)
    try:
        got = s.new(s.upload(data, name="photo.png"))
        up = s.update(got["dialog"], {"keep_aspect": False, "width": 60, "rotation": 30, "opacity": 70,
                                      "flip_h": True, "x": 5})
        assert not up["errors"]
        s.accept(got["dialog"])
        s.call("POST", f"/documents/{s.doc}/save")
        path = s.path
    finally:
        s.close()
    ref = str(tmp_path / "ref")
    shutil.copytree(BRACKET, ref)
    made = _tab_features(path)[-1]
    want = _reference(ref, data, keep_aspect=False, width=60.0, rotation=30.0, opacity=70.0, flip_h=True, x=5.0)
    assert made["type"] == "canvas" and made["params"]["blob"] == hashlib.sha256(data).hexdigest()
    # the size the dialog had (the image's own) is what the feature fills in for a None height
    assert made["params"]["height"] == pytest.approx(want["params"]["height"])
    want["params"]["height"] = made["params"]["height"]
    assert json.dumps(made, indent=1) == json.dumps(want, indent=1)
    assert blobstore.store_for(_tab_file(path)).get(made["params"]["blob"]) == data


def _tab_file(folder, tab="T1"):
    with open(os.path.join(folder, "document.json")) as fh:
        entry = next(e for e in json.load(fh)["tabs"] if e["id"] == tab)
    return os.path.join(folder, entry["file"])


def _tab_features(folder, tab="T1"):
    with open(_tab_file(folder, tab)) as fh:
        return json.load(fh)["features"]


def _reference(folder, data, **choices):
    """The canvas I12.1 saves for `choices`: added to a copy of the bracket with the image inline (as the Import
    command adds a file), saved, and read back from the tab file."""
    import base64
    from freeshape import features as F
    from freeshape.docstore import FolderStore
    from freeshape.session import DocumentSession
    params = F.default_params("canvas")
    params.update(data=base64.b64encode(data).decode(), filename="photo.png", **choices)
    with DocumentSession(FolderStore(folder)) as sess:
        sess.submit_edit("T1", {"op": "add_feature", "feature": {"type": "canvas", "name": "Canvas 1",
                                                                  "suppressed": False, "params": params}})
        sess.save()
    return _tab_features(folder)[-1]
