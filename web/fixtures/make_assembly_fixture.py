"""Record the hinge Assembly's outputs in the engine wire format (card I8.1).

    both fs_py web/fixtures/make_assembly_fixture.py            (writes the two files below, prints the expected file)
    both fs_py web/fixtures/make_assembly_fixture.py --check    (rebuilds in memory, fails if the files differ)

examples/hinge.fasm (three instances of three parts, two mates), then set_hidden on I3. Writes, next to this script:
    hinge.assembly.fsw        the bytes GET .../outputs answers
    hinge.assembly.expected.json   the instance rows and mates the Vitest test compares with
Deterministic on one OCP build; the committed files come from OCP 8.0 (--check passes on 8.0 only).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

FSW = os.path.join(HERE, "hinge.assembly.fsw")
EXPECTED = os.path.join(HERE, "hinge.assembly.expected.json")
EDITS = [{"op": "set_hidden", "instance": "I3", "value": True}]


def build():
    from freeshape.engine import InProcessEngine, wire
    eng = InProcessEngine()
    try:
        tab = eng.open_tab(os.path.join(ROOT, "examples", "hinge.fasm"), tab="T2")
        for edit in EDITS:
            eng.submit_edit(tab, edit)
        out = eng.outputs(tab)
    finally:
        eng.close()
    data = wire.to_wire(out)
    assert type(wire.from_wire(data)) is type(out)
    expected = {
        "tab": out.tab,
        "dof": out.dof,
        "errors": out.errors,
        "instances": [{"id": i.id, "name": i.name, "part": i.part, "T": [[float(x) for x in r] for r in i.T],
                       "hidden": i.hidden, "fixed": i.fixed} for i in out.instances],
        "mates": [{"id": m.id, "name": m.name, "type": m.type, "suppressed": m.suppressed,
                   "a": m.a.instance, "b": m.b.instance} for m in out.mates],
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
