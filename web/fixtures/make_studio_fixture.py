"""Record the hinge Part Studio's outputs in the engine wire format (card I10.1: the Insert panel lists its parts).

    both fs_py web/fixtures/make_studio_fixture.py            (writes hinge.studio.fsw)
    both fs_py web/fixtures/make_studio_fixture.py --check    (rebuilds in memory, fails if the file differs)

examples/documents/hinge's Part Studio tab T1 (Base plate, Arm, Pin). Deterministic on one OCP build; the committed file
comes from OCP 8.0 (--check passes on 8.0 only).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

FSW = os.path.join(HERE, "hinge.studio.fsw")


def build():
    from freeshape.engine import InProcessEngine, wire
    eng = InProcessEngine()
    try:
        tab = eng.open_tab(os.path.join(ROOT, "examples", "documents", "hinge", "hinge_parts.fshape"), tab="T1")
        out = eng.outputs(tab)
    finally:
        eng.close()
    return wire.to_wire(out)


def main():
    data = build()
    if "--check" in sys.argv:
        with open(FSW, "rb") as fh:
            same = fh.read() == data
        print("fixture matches" if same else "fixture DIFFERS")
        return 0 if same else 1
    with open(FSW, "wb") as fh:
        fh.write(data)
    print(f"wrote {FSW} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
