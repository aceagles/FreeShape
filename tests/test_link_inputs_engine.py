"""I1.6: a studio's published output reaches a consuming studio's worker. CrossTabInputs.studio is the
Document.link_inputs of every studio tab the engine opens; in a process worker it answers from the package the
parent sent (the worker never opens the source's file). The digest is the one LinkResolver.resolve_studio gives."""
import os

import pytest

from conftest import EXAMPLES_DIR
from freeshape import kernel as K
from freeshape import links as L
from freeshape.document import Document
from freeshape.engine import InProcessEngine, ProcessEngine
from freeshape.engine import process as P
from freeshape.engine import worker as W

HINGE_STUDIO = os.path.join(EXAMPLES_DIR, "hinge_parts.fshape")
DEPTH = {"op": "set_param", "feature": "F2", "key": "depth"}       # hinge_parts: the base plate's extrude


def read_package(src):
    """Run in the consumer's worker: what Document.link_inputs(src) gives there."""
    pkg = W.current().doc.link_inputs(src)
    return pkg.digest, sorted(p.name for p in pkg.parts.values()), sorted(pkg.sketches), pkg.digest_for("F2/1")


def _consume(engine, studio):
    """A new studio tab that holds a link to `studio` (as a consumer's meta would tell the parent)."""
    src = {"kind": "studio", "tab": studio, "part": None, "studio_name": "Src"}
    return engine.new_tab("studio", "Consumer", sources=[src]), src


@pytest.fixture
def engine():
    e = ProcessEngine()
    yield e
    e.close()
    P.SPARES.clear()


def test_in_process_link_inputs_digest_equals_the_headless_resolver():
    e = InProcessEngine()
    studio = e.open_tab(HINGE_STUDIO)
    consumer, src = _consume(e, studio)
    pkg = e._get(consumer).doc.link_inputs(src)
    headless = Document.load(HINGE_STUDIO)
    want = L.LinkResolver(find_open_studio=lambda uid, path: headless).resolve_studio(src)
    assert pkg.digest == want.digest and pkg.parts and pkg.sketches
    assert pkg.digest_for("F2/1") == want.digest_for("F2/1")
    assert list(pkg.parts) == list(want.parts) and list(pkg.sketches) == list(want.sketches)
    assert e._get(studio).doc.link_inputs is not None            # the source tab itself gets one too
    with pytest.raises(K.KernelError):
        e._get(consumer).doc.link_inputs({"kind": "studio", "tab": "nope", "studio_name": "Gone"})


def test_a_worker_reads_the_source_package_the_parent_sent_and_sees_unsaved_edits(engine):
    ref = InProcessEngine()
    rs = ref.open_tab(HINGE_STUDIO)
    rc, rsrc = _consume(ref, rs)
    studio = engine.open_tab(HINGE_STUDIO)
    consumer, src = _consume(engine, studio)
    ref_pkg = ref._get(rc).doc.link_inputs(rsrc)

    digest, names, sketches, plate = engine.run_in_worker(consumer, read_package, src)
    assert digest == ref_pkg.digest and plate == ref_pkg.digest_for("F2/1")
    assert names == sorted(p.name for p in ref_pkg.parts.values()) and names
    assert sketches == sorted(ref_pkg.sketches) and sketches

    # an edit nobody saved: the same call in the worker returns the new digest
    ref.submit_edit(rs, dict(DEPTH, value=12))
    engine.submit_edit(studio, dict(DEPTH, value=12))
    new_digest, _, _, new_plate = engine.run_in_worker(consumer, read_package, src)
    new_ref = ref._get(rc).doc.link_inputs(rsrc)
    assert new_digest != digest and new_plate != plate
    assert new_digest == new_ref.digest and new_plate == new_ref.digest_for("F2/1")
