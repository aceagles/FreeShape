"""Headless link + file-format smoke: no MainWindow, no Qt at all.

Part of the cumulative smoke gate (see s00_baseline.py for the convention). Loads examples/hinge.fasm
and its Part Studio (examples/hinge_parts.fshape), resolves every assembly instance through the plain
`freeshape.links.LinkResolver` -- the same headless resolver MainWindow injects callbacks into -- and
round-trips all three file formats (.fshape, .fasm, .fdwg) through the migration chain, checking the
reloaded shapes still match the fingerprints frozen in tests/golden/file_versions.json.

    both fs_py scripts/smoke/s0_headless.py
"""
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from conftest import EXAMPLES_DIR, assert_golden, shape_fingerprint, state_fingerprint  # noqa: E402
from freeshape import links as L  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.assembly import AssemblyDoc  # noqa: E402
from freeshape.document import PartStudioDoc  # noqa: E402
from freeshape.drawing import DrawingDoc  # noqa: E402

HINGE_ASM = os.path.join(EXAMPLES_DIR, "hinge.fasm")
HINGE_STUDIO = os.path.join(EXAMPLES_DIR, "hinge_parts.fshape")
V1_FDWG = os.path.join(ROOT, "tests", "data", "versions", "v1.fdwg")


def _tmp_path(name):
    import tempfile
    return os.path.join(tempfile.mkdtemp(prefix="s0_headless_"), name)


def load_hinge_via_link_resolver():
    """examples/hinge.fasm and its studio, resolved through the core LinkResolver, no MainWindow."""
    resolver = L.LinkResolver()
    asm = AssemblyDoc.load(HINGE_ASM, resolver=resolver.resolve_source)
    assert not asm.errors, f"unresolved instances: {asm.errors}"
    assert asm.instances, "hinge.fasm has no instances"
    for inst in asm.instances:
        part = resolver.resolve_source(inst["source"])
        assert part is not None, f"{inst['id']} did not resolve"
        assert inst["id"] in asm.parts
    return asm


def round_trip_document():
    doc = PartStudioDoc.load(HINGE_STUDIO)
    assert not doc.errors, doc.errors
    before = state_fingerprint(doc)
    doc.save(_tmp_path("hinge_parts.fshape"))
    back = PartStudioDoc.load(doc.path)
    assert not back.errors, back.errors
    after = state_fingerprint(back)
    assert_golden("file_versions/hinge_parts", before)
    assert_golden("file_versions/hinge_parts_reloaded", after)


def round_trip_assembly():
    resolver = L.LinkResolver()
    asm = AssemblyDoc.load(HINGE_ASM, resolver=resolver.resolve_source)
    assert not asm.errors, asm.errors
    before = {iid: shape_fingerprint(asm.world_shape(iid)) for iid in asm.parts}
    asm.save(_tmp_path("hinge.fasm"))
    resolver2 = L.LinkResolver()
    back = AssemblyDoc.load(asm.path, resolver=resolver2.resolve_source)
    assert not back.errors, back.errors
    after = {iid: shape_fingerprint(back.world_shape(iid)) for iid in back.parts}
    assert_golden("file_versions/hinge_fasm", before)
    assert_golden("file_versions/hinge_fasm_reloaded", after)


def round_trip_drawing():
    g = DrawingDoc.load(V1_FDWG)
    assert g.shape is not None
    before = shape_fingerprint(g.shape)
    g.save(_tmp_path("v1.fdwg"))
    back = DrawingDoc.load(g.path)
    assert back.shape is not None
    after = shape_fingerprint(back.shape)
    assert_golden("file_versions/v1_fdwg", before)
    assert_golden("file_versions/v1_fdwg_reloaded", after)


def main():
    asm = load_hinge_via_link_resolver()
    round_trip_document()
    round_trip_assembly()
    round_trip_drawing()
    assert "PySide6" not in sys.modules
    print(f"S0 OK: instances={len(asm.instances)} 3 formats round-tripped ocp={os.environ.get('OCP', '?')}")


if __name__ == "__main__":
    main()
