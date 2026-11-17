"""Record the hinge Assembly's outputs with mate connectors on its instances (card I10.3).

    both fs_py web/fixtures/make_connectors_fixture.py            (writes the file below)
    both fs_py web/fixtures/make_connectors_fixture.py --check    (rebuilds in memory, fails if the file differs)

examples/documents/hinge: its Part Studio T1 gets a Mate connector on the top face of the Base plate (feature F90, "Top"),
its Assembly T2 an origin connector on the Arm's instance. Writes hinge.connectors.assembly.fsw, the bytes GET .../outputs
answers for T2, where the Base plate's instance offers "Top" and the Arm's offers its origin connector.
Deterministic on one OCP build; the committed file comes from OCP 8.0 (--check passes on 8.0 only).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

FSW = os.path.join(HERE, "hinge.connectors.assembly.fsw")
FOLDER = os.path.join(ROOT, "examples", "documents", "hinge")


def build():
    from freeshape import assembly as A
    from freeshape import kernel as K
    from freeshape.engine import InProcessEngine, wire
    eng = InProcessEngine()
    try:
        studio = eng.open_tab(os.path.join(FOLDER, "hinge_parts.fshape"), tab="T1")
        asm = eng.open_tab(os.path.join(FOLDER, "hinge.fasm"), tab="T2")
        plate = next(p for p in eng.outputs(studio).parts if p.name == "Base plate")
        doc = eng._get(studio).doc
        shape = doc.state.parts[plate.id].shape
        top = max(range(len(K.faces(shape))), key=lambda i: (K.face_info(K.faces(shape)[i]).get("n") or [0, 0, 0])[2])
        ref = A._ref_signature(shape, "face", top, doc.state.named(plate.id))
        params = {"name": "Top", "ref": ref, "kind": "face", "x_ref": A.connector_x_ref(A.connector_frame(shape, "face", top)),
                  "flip": False, "owner": plate.id}
        eng.submit_edit(studio, {"op": "add_feature", "feature": {"id": "F90", "type": "mate_connector", "name": "Top",
                                                                  "suppressed": False, "params": params}})
        arm = next(i for i in eng.outputs(asm).instances if i.source.get("part") != plate.id and not i.hidden)
        eng._get(asm).doc.add_instance_origin_connector(arm.id)
        out = eng.outputs(asm)
    finally:
        eng.close()
    data = wire.to_wire(out)
    assert type(wire.from_wire(data)) is type(out)
    return data


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
