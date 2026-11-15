"""Record the bracket example's Part Studio outputs in the engine wire format, for the client's decoder tests.

    both fs_py web/fixtures/make_fixture.py            (writes the two files below, prints the numbers)
    both fs_py web/fixtures/make_fixture.py --check    (rebuilds in memory, fails if the files differ)

Writes, next to this script:
    bracket.outputs.fsw    the bytes GET /tabs/T1/outputs answers (session.outputs_wire)
    bracket.expected.json  the numbers the Vitest decoder test compares with: parts, triangles, edges, faces, and
                           three element names with the 1-based engine index and the 0-based client index.
The output is deterministic: the same bytes on every run on the same OCP build. The committed files come from
OCP 8.0 (the build the gate compares against); OCP 7.9 names a few things differently, so --check passes on 8.0 only.
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

TAB = "T1"
FSW = os.path.join(HERE, "bracket.outputs.fsw")
EXPECTED = os.path.join(HERE, "bracket.expected.json")


def build():
    tmp = tempfile.mkdtemp(prefix="make_fixture_")
    os.environ["FREESHAPE_DATA_DIR"] = os.path.join(tmp, "appdata")
    os.environ["XDG_DATA_HOME"] = os.path.join(tmp, "xdg")
    os.environ["HOME"] = os.path.join(tmp, "home")
    for d in ("appdata", "xdg", "home"):
        os.makedirs(os.path.join(tmp, d), exist_ok=True)
    folder = os.path.join(tmp, "bracket")
    shutil.copytree(os.path.join(ROOT, "examples", "documents", "bracket"), folder)
    try:
        from freeshape.engine import wire
        from freeshape.session import DocumentSession
        with DocumentSession(folder, spares=0) as session:
            out = session.outputs(TAB)
            data = session.outputs_wire(TAB)
        back = wire.from_wire(data)
        assert type(back) is type(out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    parts = out.parts
    expected = {
        "tab": out.tab,
        "kind": out.kind,
        "revision": out.revision,
        "parts": len(parts),
        "triangles": sum(len(p.mesh.tris) for p in parts if p.mesh is not None),
        "edges": sum(len(p.mesh.edge_polys) for p in parts if p.mesh is not None),
        "faces": sum(len(p.mesh.face_ranges) for p in parts if p.mesh is not None),
        "vertices": sum(len(p.mesh.vertices) for p in parts if p.mesh is not None),
        "sketches": len(out.sketches),
        "planes": len(out.planes),
        "axes": len(out.axes),
        "connectors": len(out.connectors),
        "names": [],
    }
    # three name lookups: the first named face, edge and vertex of the first part that has names.
    for pi, p in enumerate(parts):
        for kind in ("face", "edge", "vertex"):
            m = (p.names or {}).get(kind) or {}
            if m and len(expected["names"]) < 3 and not any(n["kind"] == kind for n in expected["names"]):
                i = min(m)
                expected["names"].append({"part": pi, "part_id": p.id, "kind": kind, "engine_index": int(i),
                                          "index": int(i) - 1, "name": m[i]})
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
