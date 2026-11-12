"""The shipped examples: load, rebuild and fingerprint every part (and every step of the feature tree), and
solve the hinge assembly and record where each instance ends up. Nothing here writes into examples/."""
import os

import numpy as np
import pytest

from conftest import (ANALYTIC, EXAMPLES_DIR, FREEFORM, assert_golden, matrix_list, shape_fingerprint,
                      state_fingerprint)
from freeshape import assembly as A
from freeshape import kernel as K
from freeshape.document import Document

STUDIOS = {"bracket": FREEFORM, "hinge_parts": ANALYTIC}      # the bracket has fillets


def _load(name):
    return Document.load(os.path.join(EXAMPLES_DIR, f"{name}.fshape"))


@pytest.mark.parametrize("name", sorted(STUDIOS))
def test_example_studio_rebuilds(name):
    doc = _load(name)
    assert not doc.errors, doc.errors
    assert doc.state.parts
    for p in doc.state.parts.values():
        assert K.is_valid(p.shape), p.id
    assert_golden(f"examples/{name}", state_fingerprint(doc), STUDIOS[name])


@pytest.mark.parametrize("name", sorted(STUDIOS))
def test_example_studio_every_step(name):
    """The model after each feature, so a changed golden points at the feature that changed."""
    doc = _load(name)
    steps = {}
    for n in range(1, len(doc.features) + 1):
        errs = {}
        st = doc.compute(doc.features, n, errs)
        f = doc.features[n - 1]
        steps[f"{n:02d}_{f['id']}_{f['type']}"] = {"errors": sorted(errs), "parts": state_fingerprint(st)}
    assert_golden(f"examples/{name}_steps", steps, STUDIOS[name])


def test_bracket_known_shape():
    doc = _load("bracket")
    assert [f["type"] for f in doc.features] == ["sketch", "extrude", "fillet", "sketch", "hole", "sketch",
                                                  "extrude", "chamfer"]
    assert len(doc.state.parts) == 1


def _hinge_resolver():
    studio = _load("hinge_parts")

    def resolve(src):
        if src.get("uid") != studio.uid:
            raise K.KernelError("Part Studio isn't open")
        p = studio.state.parts.get(src.get("part"))
        if p is None:
            raise K.KernelError("The part no longer exists")
        return p
    return studio, resolve


def _placements(asm):
    return {inst["id"]: matrix_list(inst["T"]) for inst in asm.instances}


def test_hinge_assembly_solves():
    studio, resolve = _hinge_resolver()
    asm = A.AssemblyDoc.load(os.path.join(EXAMPLES_DIR, "hinge.fasm"), resolver=resolve)
    assert not asm.errors, asm.errors
    stored = _placements(asm)
    asm.solve()
    assert not asm.errors, asm.errors
    solved = _placements(asm)
    # the file was saved solved: re-solving must not move anything
    for iid in stored:
        assert np.allclose(stored[iid], solved[iid], atol=1e-9), iid
    assert asm.dof == 1                                     # the arm swings about the pin
    dofs = {i["id"]: asm.instance_dof(i["id"]) for i in asm.instances}
    assert dofs == {"I1": 0, "I2": 1, "I3": 0}, dofs
    world = {iid: shape_fingerprint(asm.world_shape(iid)) for iid in asm.parts}
    assert_golden("examples/hinge_assembly", {"T": solved, "dof": asm.dof, "instance_dof": dofs,
                                              "world": world}, ANALYTIC)


def test_hinge_assembly_from_embedded_copies():
    """Without the Part Studio the assembly falls back to the parts saved inside it, in the same places."""
    _studio, resolve = _hinge_resolver()
    linked = A.AssemblyDoc.load(os.path.join(EXAMPLES_DIR, "hinge.fasm"), resolver=resolve)
    alone = A.AssemblyDoc.load(os.path.join(EXAMPLES_DIR, "hinge.fasm"))
    assert all("saved in this assembly" in v for v in alone.errors.values())
    alone.solve()
    for iid in linked.parts:
        a, b = shape_fingerprint(linked.world_shape(iid)), shape_fingerprint(alone.world_shape(iid))
        assert a["faces"] == b["faces"] and abs(a["volume"] - b["volume"]) < 1e-6 * a["volume"], iid
        assert np.allclose(a["bbox"], b["bbox"], atol=1e-6), iid


def test_hinge_arm_swings_about_pin():
    """Rotating the arm about its revolute keeps the mate and the pin/plate where they were."""
    _studio, resolve = _hinge_resolver()
    asm = A.AssemblyDoc.load(os.path.join(EXAMPLES_DIR, "hinge.fasm"), resolver=resolve)
    asm.solve()
    before = _placements(asm)
    rev = next(m for m in asm.mates if m["type"] == "revolute")
    fa = asm.world_frame(rev["a"])
    M = A.rot_about(fa["z"], np.radians(40), fa["o"])
    asm.move_instance("I2", M, checkpoint=False)
    assert not asm.errors, asm.errors
    after = _placements(asm)
    assert np.allclose(before["I1"], after["I1"]) and np.allclose(before["I3"], after["I3"], atol=1e-9)
    R = np.array(after["I2"])[:3, :3] @ np.array(before["I2"])[:3, :3].T
    ang = np.degrees(np.arccos(np.clip((np.trace(R) - 1) / 2, -1, 1)))
    assert abs(ang - 40) < 1e-6, ang
    assert_golden("examples/hinge_arm_40deg", {"T": after}, ANALYTIC)
