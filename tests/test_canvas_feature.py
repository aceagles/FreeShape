"""I12.1: the Canvas feature in the engine. A PNG or JPEG image placed on a plane or a planar face, kept in the
file as a blob, published as a CanvasOut in StudioOutputs.canvases; it changes no part.

The frames of three cases are the golden file tests/golden/canvas.json, written once by this card. Every test
builds its own model and passes alone."""
import base64
import hashlib
import json
import math
import os
import shutil
import struct
import zlib

import numpy as np
import pytest

from conftest import EXAMPLES_DIR, FRONT, add, assert_golden, block_doc, normal_is, face_refs, only_part
from freeshape import blobstore
from freeshape import features as F
from freeshape import image_info as II
from freeshape import kernel as K
from freeshape.document import Document
from freeshape.engine import InProcessEngine, StudioOutputs, contract, wire

BRACKET_DIR = os.path.join(EXAMPLES_DIR, "documents", "bracket")


# =========================================================================== images

def _chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def png(w, h, dpi=None):
    """A whole w x h grey PNG; with `dpi`, a pHYs chunk stating that resolution."""
    out = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
    if dpi is not None:
        ppm = int(round(dpi / 0.0254))
        out += _chunk(b"pHYs", struct.pack(">IIB", ppm, ppm, 1))
    out += _chunk(b"IDAT", zlib.compress(b"".join(b"\x00" + b"\x80" * w for _ in range(h))))
    return out + _chunk(b"IEND", b"")


def jpeg(w, h, dpi=None):
    """The marker segments of a w x h baseline JPEG (SOI, JFIF APP0, SOF0, EOI): all image_info reads."""
    units, d = (1, int(dpi)) if dpi else (0, 1)
    app0 = b"JFIF\x00\x01\x01" + struct.pack(">BHHBB", units, d, d, 0, 0)
    sof = struct.pack(">BHHB", 8, h, w, 1) + b"\x01\x11\x00"
    return (b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", len(app0) + 2) + app0
            + b"\xff\xc0" + struct.pack(">H", len(sof) + 2) + sof + b"\xff\xd9")


GIF = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00" \
      b"\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
SVG = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>'


def add_canvas(doc, data, filename="photo.png", **params):
    """Add a canvas with its image inline, as a client adds it; returns the feature."""
    f = doc.make_feature("canvas")
    f["params"].update(data=base64.b64encode(data).decode(), filename=filename, **params)
    doc.add_feature(f)
    return f


def frame(c):
    """The placement values of a canvas entry (ModelState.canvases) or CanvasOut, rounded for comparison."""
    get = c.get if isinstance(c, dict) else lambda k: getattr(c, k)
    return {k: [round(float(v), 9) + 0.0 for v in get(k)] for k in ("origin", "xdir", "ydir", "normal")} | \
        {k: round(float(get(k)), 9) for k in ("width", "height", "opacity")} | \
        {k: bool(get(k)) for k in ("flip_h", "flip_v", "selectable")}


def close(a, b, tol=1e-6):
    return np.allclose(np.asarray(a, float), np.asarray(b, float), atol=tol)


def bracket(tmp_path):
    shutil.copytree(BRACKET_DIR, tmp_path / "bracket")
    doc = Document.load(str(tmp_path / "bracket" / "bracket.fshape"))
    assert not doc.errors, doc.errors
    return doc


def face_plane_ref(doc, pick):
    """{kind: face, face: ref} of the bracket's planar face that `pick` (face_info -> sort key) ranks highest."""
    part = only_part(doc)
    best = max((f for f in K.faces(part.shape) if K.face_info(f).get("n") is not None), key=lambda f: pick(K.face_info(f)))
    return {"kind": "face", "face": F.make_ref(part.id, best, "face", part.shape, doc.state.named(part.id))}, \
        K.face_info(best)


def top_face(i):
    return (i["n"][2] > 0.99, i["c"][2])


def end_face(i):            # the extrude's end cap facing +Y (the profile is on Front, extruded both ways)
    return (i["n"][1] > 0.99, i["c"][1])


# =========================================================================== image_info

def test_image_info_reads_png_and_jpeg():
    info = II.read_info(png(200, 100))
    assert (info.format, info.width, info.height, info.dpi_x) == ("png", 200, 100, None)
    assert close(info.default_size_mm(), (200 * 25.4 / 96, 100 * 25.4 / 96))
    info = II.read_info(png(300, 150, dpi=300))
    assert close((info.dpi_x, info.dpi_y), (300, 300), 0.01)
    assert close(info.default_size_mm(), (25.4, 12.7), 1e-3)
    info = II.read_info(jpeg(640, 480, dpi=72))
    assert (info.format, info.width, info.height) == ("jpeg", 640, 480)
    assert close(info.default_size_mm(), (640 * 25.4 / 72, 480 * 25.4 / 72))
    assert close(II.read_info(jpeg(96, 48)).default_size_mm(), (25.4, 12.7))


@pytest.mark.parametrize("data, words", [
    (GIF, "GIF"), (SVG, "SVG"), (png(20, 10)[:40], "cut short"), (png(20, 10)[:-12], "cut short"),
    (jpeg(20, 10)[:-2], "cut short"), (b"", "empty"), (b"hello", "PNG and JPEG")])
def test_image_info_refuses_other_files(data, words):
    with pytest.raises(II.ImageError, match=words):
        II.read_info(data)


# =========================================================================== placement

def test_png_on_top_with_defaults():
    doc = Document()
    f = add_canvas(doc, png(200, 100))
    assert not doc.errors, doc.errors
    c = doc.state.canvases[f["id"]]
    assert math.isclose(c["width"], 52.917, abs_tol=5e-4) and math.isclose(c["height"], 26.458, abs_tol=5e-4)
    assert close(c["origin"], [0, 0, 0]) and close(c["xdir"], [1, 0, 0]) and close(c["ydir"], [0, 1, 0])
    assert close(c["normal"], [0, 0, 1])
    assert (c["opacity"], c["flip_h"], c["flip_v"], c["selectable"]) == (50.0, False, False, True)
    assert c["pixels"] == [200, 100] and c["name"] == "Canvas 1" and c["filename"] == "photo.png"
    p = doc.feature(f["id"])["params"]
    assert p["plane"] == {"kind": "std", "name": "Top"} and p["keep_aspect"] is True
    assert math.isclose(p["width"], 200 * 25.4 / 96) and math.isclose(p["height"], 100 * 25.4 / 96)


def test_size_follows_the_image_resolution_and_aspect():
    doc = Document()
    a = add_canvas(doc, png(300, 150, dpi=300))
    b = add_canvas(doc, png(200, 100), width=100.0)                     # height follows at 2:1
    c = add_canvas(doc, png(200, 100), height=10.0, keep_aspect=False)  # width stays the image's own
    assert not doc.errors, doc.errors
    st = doc.state.canvases
    assert close((st[a["id"]]["width"], st[a["id"]]["height"]), (25.4, 12.7), 1e-3)
    assert close((st[b["id"]]["width"], st[b["id"]]["height"]), (100.0, 50.0))
    assert close((st[c["id"]]["width"], st[c["id"]]["height"]), (200 * 25.4 / 96, 10.0))


def test_rotation_flip_and_offset_give_the_frame():
    doc = Document()
    f = add_canvas(doc, png(200, 100), rotation=90.0, x=10.0, y=5.0)
    c = doc.state.canvases[f["id"]]
    assert close(c["origin"], [10, 5, 0]) and close(c["xdir"], [0, 1, 0]) and close(c["ydir"], [-1, 0, 0])
    assert close(c["normal"], [0, 0, 1])
    g = add_canvas(doc, png(200, 100), rotation=90.0, x=10.0, y=5.0, flip_h=True)
    c = doc.state.canvases[g["id"]]
    assert close(c["xdir"], [0, -1, 0]) and close(c["ydir"], [-1, 0, 0]) and c["flip_h"] and not c["flip_v"]
    h = add_canvas(doc, png(200, 100), flip_v=True, plane=FRONT, x=3.0, y=4.0, opacity=80, selectable=False)
    c = doc.state.canvases[h["id"]]
    # Front: x along +X, normal -Y, so its y axis is +Z
    assert close(c["origin"], [3, 0, 4]) and close(c["xdir"], [1, 0, 0]) and close(c["ydir"], [0, 0, -1])
    assert close(c["normal"], [0, -1, 0]) and c["opacity"] == 80.0 and c["selectable"] is False
    assert not doc.errors, doc.errors


def test_bad_values_are_errors():
    doc = Document()
    for params, words in (({"width": 0.0}, "above 0"), ({"opacity": 120}, "0 to 100"),
                          ({"rotation": "#nope"}, "Unknown variable")):
        f = add_canvas(doc, png(20, 10), **params)
        assert words in doc.errors.get(f["id"], ""), (params, doc.errors)


def test_expressions_use_variables():
    doc = Document()
    add(doc, "variable", name="w", expr="40")
    f = add_canvas(doc, png(20, 10), width="#w * 2", rotation="90 deg")
    assert not doc.errors, doc.errors
    c = doc.state.canvases[f["id"]]
    assert math.isclose(c["width"], 80.0) and close(c["xdir"], [0, 1, 0])


@pytest.mark.parametrize("data, words", [(GIF, "GIF"), (SVG, "SVG"), (png(200, 100)[:60], "cut short")])
def test_other_images_are_refused_with_a_message(data, words):
    doc = Document()
    f = add_canvas(doc, data, filename="x")
    err = doc.errors.get(f["id"]) or ""
    assert words in err and ("PNG and JPEG" in err or "PNG" in err), err
    assert f["id"] not in doc.state.canvases


# =========================================================================== on a face

def test_on_the_bracket_top_face_it_lies_on_that_face(tmp_path):
    doc = bracket(tmp_path)
    ref, info = face_plane_ref(doc, top_face)
    f = add_canvas(doc, png(200, 100), plane=ref)
    assert not doc.errors, doc.errors
    c = doc.state.canvases[f["id"]]
    assert close(c["normal"], [0, 0, 1])
    assert close(c["origin"], info["c"], 1e-6)                # the face centroid, on the face's plane
    assert abs(np.dot(np.asarray(c["origin"]) - info["c"], [0, 0, 1])) < 1e-9


def test_changing_the_extrude_depth_moves_it_with_the_face(tmp_path):
    doc = bracket(tmp_path)
    ref, info = face_plane_ref(doc, end_face)
    f = add_canvas(doc, png(200, 100), plane=ref)
    before = doc.state.canvases[f["id"]]["origin"]
    assert math.isclose(before[1], info["c"][1], abs_tol=1e-6)
    ext = json.loads(json.dumps(doc.feature("F2")))
    ext["params"]["depth"] = float(ext["params"]["depth"]) + 20.0     # symmetric: each end moves out 10 mm
    doc.replace_feature(ext)
    assert f["id"] not in doc.errors, doc.errors
    after = doc.state.canvases[f["id"]]["origin"]
    assert math.isclose(after[1] - before[1], 10.0, abs_tol=1e-6)
    assert close([after[0], after[2]], [before[0], before[2]])
    assert close(doc.state.canvases[f["id"]]["normal"], [0, 1, 0])


def test_a_lost_face_is_the_feature_error(tmp_path):
    doc = bracket(tmp_path)
    ref, _ = face_plane_ref(doc, top_face)
    f = add_canvas(doc, png(200, 100), plane=ref)
    assert not doc.errors
    doc.delete_features(["F2"])                         # the extrude that made the face
    assert "The face this feature uses is gone" in (doc.errors.get(f["id"]) or ""), doc.errors
    assert f["id"] not in doc.state.canvases


# =========================================================================== the file

def _volumes(doc):
    return {pid: round(K.volume(p.shape), 6) for pid, p in doc.state.parts.items()}


def test_saved_as_a_blob_and_reopened(tmp_path):
    doc = block_doc()
    vols = _volumes(doc)
    data = png(200, 100)
    top, _ = face_plane_ref(doc, top_face)
    f = add_canvas(doc, data, plane=top, rotation=30.0, x=2.0)
    assert not doc.errors, doc.errors
    assert _volumes(doc) == vols and len(doc.state.parts) == 1         # the canvas changes no part
    assert "data" not in doc.feature(f["id"])["params"]                 # held as a blob before it first built
    built = frame(doc.state.canvases[f["id"]])

    path = str(tmp_path / "s.fshape")
    doc.save(path)
    with open(path) as fh:
        saved = next(x for x in json.load(fh)["features"] if x["id"] == f["id"])
    key = hashlib.sha256(data).hexdigest()
    assert saved["params"]["blob"] == key and "data" not in saved["params"]
    assert blobstore.store_for(path).get(key) == data
    assert os.path.exists(os.path.join(os.path.dirname(path), "blobs", key))

    again = Document.load(path)
    assert not again.errors, again.errors
    assert frame(again.state.canvases[f["id"]]) == built
    assert _volumes(again) == vols


def test_outputs_carry_the_canvas(tmp_path):
    doc = block_doc()
    f = add_canvas(doc, png(200, 100))
    doc.toggle_feature_hidden(f["id"])
    path = str(tmp_path / "s.fshape")
    doc.save(path)
    engine = InProcessEngine()
    try:
        out = engine.outputs(engine.open_tab(path))
    finally:
        engine.close()
    assert not out.errors, out.errors
    assert [type(c) for c in out.canvases] == [contract.CanvasOut]
    c = out.canvases[0]
    assert (c.id, c.name, c.blob, c.hidden) == (f["id"], "Canvas 1", hashlib.sha256(png(200, 100)).hexdigest(), True)
    assert math.isclose(c.width, 200 * 25.4 / 96) and len(out.parts) == 1


# =========================================================================== wire

def _studio_outputs(canvases):
    return StudioOutputs(tab="T1", kind="studio", name="S", revision=1, errors={}, consumes=(), canvases=canvases)


def test_wire_round_trip_of_a_canvas():
    c = contract.CanvasOut(id="F3", name="Canvas 1", blob="ab" * 32, origin=[1.0, 2.0, 3.0], xdir=[0.0, 1.0, 0.0],
                           ydir=[-1.0, 0.0, 0.0], normal=[0.0, 0.0, 1.0], width=52.9, height=26.4, opacity=40.0,
                           flip_h=True, flip_v=False, selectable=False, hidden=True, filename="a.png", pixels=[200, 100])
    back = wire.from_wire(wire.to_wire(_studio_outputs([c])))
    assert back == _studio_outputs([c]) and isinstance(back.canvases[0], contract.CanvasOut)
    assert wire.REGISTRY["CanvasOut"] is contract.CanvasOut


def test_a_payload_without_canvases_decodes_with_an_empty_list():
    payload = wire.to_wire(_studio_outputs([]))
    (hlen,) = struct.unpack_from("<I", payload, 4)
    header = json.loads(payload[8:8 + hlen])
    start = 8 + hlen + (-(8 + hlen) % 8)
    del header["root"]["f"]["canvases"]                  # as an older engine wrote it
    text = json.dumps(header).encode()
    text += b" " * (-(8 + len(text)) % 8)
    old = wire.MAGIC + struct.pack("<I", len(text)) + text + payload[start:]
    back = wire.from_wire(old)
    assert isinstance(back, StudioOutputs) and back.canvases == []


# =========================================================================== goldens (tests/golden/canvas.json)

def _golden_top_default():
    doc = Document()
    f = add_canvas(doc, png(200, 100))
    return doc, f


def _golden_front_turned_flipped():
    doc = Document()
    f = add_canvas(doc, png(640, 480), plane=FRONT, rotation=30.0, x=12.5, y=-4.0, flip_h=True, flip_v=True,
                   opacity=75.0, width=120.0)
    return doc, f


def _golden_block_top_face():
    doc = block_doc(w=40.0, d=20.0, h=10.0, x0=5.0, y0=-3.0)
    top = face_refs(only_part(doc), normal_is([0, 0, 1]), expect=1)[0]
    f = add_canvas(doc, jpeg(300, 200, dpi=150), plane={"kind": "face", "face": top}, rotation=-45.0, x=1.0, y=2.0)
    return doc, f


GOLDEN_CASES = {"top_default": _golden_top_default, "front_turned_flipped": _golden_front_turned_flipped,
                "block_top_face": _golden_block_top_face}


@pytest.mark.parametrize("name", sorted(GOLDEN_CASES))
def test_canvas_frame_golden(name):
    doc, f = GOLDEN_CASES[name]()
    assert not doc.errors, doc.errors
    assert_golden(f"canvas/{name}", frame(doc.state.canvases[f["id"]]))
