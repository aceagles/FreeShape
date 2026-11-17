"""Record the bracket's Part Studio outputs with one Canvas on it (card I12.5), and the PNG that canvas shows.

    both fs_py web/fixtures/make_canvas_fixture.py            (writes the two files below)
    both fs_py web/fixtures/make_canvas_fixture.py --check    (rebuilds in memory, fails if a file differs)

Writes, next to this script and in web/e2e/fixtures:
    bracket.canvas.outputs.fsw   the bytes GET .../outputs answers for the bracket after a Canvas "photo.png" was added to it:
                                 on Top, 40 x 20 mm, at x 5 / y -3, turned 90 degrees, flipped horizontally, 30 % opaque
                                 (so a decoder test sees a frame that is not the plane's own), with its image as a blob key.
    ../e2e/fixtures/canvas.png   the 64 x 32 picture: red, with a 6 x 6 blue square in its bottom left corner. Built here from
                                 struct and zlib alone (no image library), so the bytes are the same on every run and machine.
Deterministic on one OCP build; the committed fsw comes from OCP 8.0 (--check passes on 8.0 only). The PNG does not depend on OCP.
bracket.outputs.fsw itself was recorded before the Canvas (I12.1) existed and so has no `canvases` field: `decodeOutputs` reads such
a payload as having none. `make_fixture.py --check` prints "fixture DIFFERS" for that reason alone (see the card's notes).
"""
import base64
import os
import shutil
import struct
import sys
import tempfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

FSW = os.path.join(HERE, "bracket.canvas.outputs.fsw")
PNG = os.path.join(ROOT, "web", "e2e", "fixtures", "canvas.png")
TAB = "T1"


def png_bytes():
    w, h = 64, 32
    rows = []
    for y in range(h):
        row = bytearray()
        for x in range(w):
            row += bytes((0, 0, 255)) if (x < 6 and y >= h - 6) else bytes((255, 0, 0))
        rows.append(b"\x00" + bytes(row))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def build():
    from freeshape.document import Document
    from freeshape.engine import InProcessEngine, wire
    tmp = tempfile.mkdtemp(prefix="make_canvas_fixture_")
    os.environ["FREESHAPE_DATA_DIR"] = os.path.join(tmp, "appdata")
    os.environ["XDG_DATA_HOME"] = os.path.join(tmp, "xdg")
    os.environ["HOME"] = os.path.join(tmp, "home")
    for d in ("appdata", "xdg", "home"):
        os.makedirs(os.path.join(tmp, d), exist_ok=True)
    folder = os.path.join(tmp, "bracket")
    shutil.copytree(os.path.join(ROOT, "examples", "documents", "bracket"), folder)
    path = os.path.join(folder, "bracket.fshape")
    try:
        doc = Document.load(path)
        assert not doc.errors, doc.errors
        f = doc.make_feature("canvas")
        f["params"].update(data=base64.b64encode(png_bytes()).decode(), filename="photo.png", width=40.0, height=20.0,
                           x=5.0, y=-3.0, rotation=90.0, opacity=30.0, flip_h=True)
        doc.add_feature(f)
        doc.save(path)
        eng = InProcessEngine()
        try:
            out = eng.outputs(eng.open_tab(path, tab=TAB))
        finally:
            eng.close()
        assert not out.errors, out.errors
        assert len(out.canvases) == 1 and out.canvases[0].blob
        data = wire.to_wire(out)
        assert type(wire.from_wire(data)) is type(out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return data


def main():
    data, png = build(), png_bytes()
    if "--check" in sys.argv:
        with open(FSW, "rb") as fh:
            same = fh.read() == data
        with open(PNG, "rb") as fh:
            same = same and fh.read() == png
        print("fixture matches" if same else "fixture DIFFERS")
        return 0 if same else 1
    with open(FSW, "wb") as fh:
        fh.write(data)
    with open(PNG, "wb") as fh:
        fh.write(png)
    print(f"wrote {FSW} ({len(data)} bytes) and {PNG} ({len(png)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
