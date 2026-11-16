"""Record a Part Studio preview that carries a drag arrow, in the engine wire format.

    both fs_py web/fixtures/make_preview_fixture.py            (writes the two files below, prints the expected file)
    both fs_py web/fixtures/make_preview_fixture.py --check    (rebuilds in memory, fails if the files differ)

The bracket example, then a preview of its extrude F2 at 12 mm, blind, not flipped (I6.3): the StudioOutputs of the
previewed model, whose `manipulators` hold the depth arrow. Writes, next to this script:
    preview.outputs.fsw     the bytes GET .../dialog/<did>/preview answers
    preview.expected.json   the arrow the Vitest test compares with
Deterministic on one OCP build; the committed files come from OCP 8.0 (--check passes on 8.0 only).
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

FSW = os.path.join(HERE, "preview.outputs.fsw")
EXPECTED = os.path.join(HERE, "preview.expected.json")


def build():
    tmp = tempfile.mkdtemp(prefix="make_preview_fixture_")
    os.environ["FREESHAPE_DATA_DIR"] = os.path.join(tmp, "appdata")
    os.environ["XDG_DATA_HOME"] = os.path.join(tmp, "xdg")
    os.environ["HOME"] = os.path.join(tmp, "home")
    for d in ("appdata", "xdg", "home"):
        os.makedirs(os.path.join(tmp, d), exist_ok=True)
    path = os.path.join(tmp, "bracket.fshape")
    shutil.copy(os.path.join(ROOT, "examples", "documents", "bracket", "bracket.fshape"), path)
    try:
        from freeshape.engine import InProcessEngine, wire
        eng = InProcessEngine()
        try:
            tab = eng.open_tab(path, tab="T1")
            params = dict(eng.feature(tab, "F2")["feature"]["params"], depth=12.0, end="blind", flip=False)
            got = eng.preview(tab, "F2", "extrude", params)
            assert got["ok"], got["error"]
            out = got["outputs"]
        finally:
            eng.close()
        data = wire.to_wire(out)
        assert type(wire.from_wire(data)) is type(out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    expected = {"tab": out.tab, "manipulators": out.manipulators}
    return data, expected


def main():
    data, expected = build()
    text = json.dumps(expected, indent=1, sort_keys=True) + "\n"
    if "--check" in sys.argv:
        with open(FSW, "rb") as fh:
            same = fh.read() == data
        with open(EXPECTED) as fh:
            same = same and fh.read() == text
        print("fixture matches" if same else "fixture DIFFERS")
        return 0 if same else 1
    with open(FSW, "wb") as fh:
        fh.write(data)
    with open(EXPECTED, "w") as fh:
        fh.write(text)
    print(f"{len(data)} bytes -> {FSW}")
    print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
