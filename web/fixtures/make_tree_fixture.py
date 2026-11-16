"""Record a Part Studio whose feature tree is in every state the client draws, in the engine wire format.

    both fs_py web/fixtures/make_tree_fixture.py            (writes the two files below, prints the expected file)
    both fs_py web/fixtures/make_tree_fixture.py --check    (rebuilds in memory, fails if the files differ)

The bracket example, then these edits on the in-process engine: rename F2 "Body", put F4 and F5 in a folder
"Holes", suppress the fillet F3, add a fillet F9 with no edges (it fails) at index 5, rename F1 and undo that
(so redo is available), and roll back to 7 features (F7 and F8 rolled back). Writes, next to this script:
    tree.outputs.fsw      the bytes GET .../outputs answers
    tree.expected.json    the feature rows, folders, rollback and undo / redo flags the Vitest test compares with
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

FSW = os.path.join(HERE, "tree.outputs.fsw")
EXPECTED = os.path.join(HERE, "tree.expected.json")

EDITS = [
    {"op": "rename_feature", "feature": "F2", "name": "Body"},
    {"op": "make_folder", "ids": ["F4", "F5"], "name": "Holes", "folder": "D1"},
    {"op": "set_suppressed", "feature": "F3", "value": True},
    {"op": "add_feature", "index": 5, "feature": {"id": "F9", "type": "fillet", "name": "Fillet 2",
                                                  "suppressed": False, "params": {"edges": [], "radius": 2.0}}},
    {"op": "rename_feature", "feature": "F1", "name": "Profile"},
    {"op": "undo"},
    {"op": "set_rollback", "index": 7},
]


def build():
    tmp = tempfile.mkdtemp(prefix="make_tree_fixture_")
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
            for edit in EDITS:
                eng.submit_edit(tab, edit)
            out = eng.outputs(tab)
        finally:
            eng.close()
        data = wire.to_wire(out)
        assert type(wire.from_wire(data)) is type(out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    expected = {
        "tab": out.tab,
        "features": [{"id": f.id, "type": f.type, "label": f.label, "name": f.name, "suppressed": f.suppressed,
                      "error": f.error, "folder": f.folder, "hidden": f.hidden} for f in out.features],
        "folders": [{"id": f.id, "name": f.name, "open": f.open, "suppressed": f.suppressed, "failed": f.failed}
                    for f in out.folders],
        "rollback": out.rollback,
        "can_undo": out.can_undo,
        "can_redo": out.can_redo,
        "errors": out.errors,
    }
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
