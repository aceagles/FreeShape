"""freeshape/links.py: headless link resolution -- no PySide6, no QApplication. MainWindow only injects
open-tab lookups and (for studio_by_uid) switches tabs; everything here exercises the plain fallback, which
loads straight from the file on disk."""
import copy
import os

from conftest import EXAMPLES_DIR, FREEFORM, assert_golden, shape_fingerprint
from freeshape import links as L
from freeshape.assembly import AssemblyDoc
from freeshape.document import Document

HINGE_ASM = os.path.join(EXAMPLES_DIR, "hinge.fasm")
HINGE_STUDIO = os.path.join(EXAMPLES_DIR, "hinge_parts.fshape")


def load_hinge(resolver):
    a = AssemblyDoc.load(HINGE_ASM, resolver=resolver.resolve_source)
    assert not a.errors, a.errors
    return a


def test_resolve_source_every_instance():
    """Every instance of hinge.fasm resolves against hinge_parts.fshape, loaded straight from its path."""
    r = L.LinkResolver()
    a = load_hinge(r)
    assert len(a.instances) == 3
    assert all(i["id"] in a.parts for i in a.instances)
    assert_golden("link_resolver/hinge_instances",
                  {i["id"]: shape_fingerprint(a.parts[i["id"]].shape) for i in a.instances}, FREEFORM)


def test_resolve_source_does_not_mutate_the_source():
    r = L.LinkResolver()
    a = load_hinge(r)
    src = a.instances[0]["source"]
    before = copy.deepcopy(src)
    assert r.resolve_source(src) is not None
    assert src == before


def test_resolve_drawing_source_by_studio_uid():
    r = L.LinkResolver()
    studio = r.studio_by_uid(None, HINGE_STUDIO, load=True)
    assert studio is not None
    src = {"kind": "studio", "uid": studio.uid, "path": os.path.abspath(HINGE_STUDIO), "part": None}
    before = copy.deepcopy(src)
    shape = r.resolve_drawing_source(src)
    assert shape is not None
    assert src == before
    assert_golden("link_resolver/studio_drawing_source", shape_fingerprint(shape), FREEFORM)


def test_resolve_drawing_source_by_assembly_uid():
    r = L.LinkResolver()
    a = load_hinge(r)
    src = {"kind": "assembly", "uid": a.uid, "path": os.path.abspath(HINGE_ASM), "name": a.name}
    before = copy.deepcopy(src)
    shape = r.resolve_drawing_source(src)
    assert shape is not None
    assert src == before
    assert_golden("link_resolver/assembly_drawing_source", shape_fingerprint(shape), FREEFORM)


def test_resolving_a_stale_link_still_does_not_mutate_the_source():
    """A stale uid, resolved only via the path fallback, is still not written back by resolving itself."""
    r = L.LinkResolver()
    a = load_hinge(r)
    src = dict(a.instances[0]["source"])
    src["uid"] = "stale-uid-does-not-exist"
    before = copy.deepcopy(src)
    assert r.resolve_source(src) is not None
    assert src == before


def test_stale_uid_is_rewritten_by_the_save_time_step():
    r = L.LinkResolver()
    a = load_hinge(r)
    real_uid = a.instances[0]["source"]["uid"]
    src = dict(a.instances[0]["source"])
    src["uid"] = "stale-uid-does-not-exist"
    L.repair_source(r, src)
    assert src["uid"] == real_uid


def test_stale_path_is_rewritten_by_the_save_time_step_when_the_studio_is_open():
    """A moved Part Studio can only be found again if something (a tab in MainWindow; here, the injected
    lookup) still has it open -- loading straight from the stale path on disk can't recover a new location."""
    real_studio = Document.load(HINGE_STUDIO)
    r = L.LinkResolver(find_open_studio=lambda uid, path: real_studio if uid == real_studio.uid else None)
    a = load_hinge(r)
    src = dict(a.instances[0]["source"])
    src["path"] = "/nonexistent/old/path.fshape"
    L.repair_source(r, src)
    assert src["path"] == os.path.abspath(HINGE_STUDIO)


def test_repair_links_fixes_every_instance_of_an_assembly():
    r = L.LinkResolver()
    a = load_hinge(r)
    for inst in a.instances:
        inst["source"]["studio_name"] = "stale name"
    L.repair_links(r, a)
    assert all(inst["source"]["studio_name"] == "Hinge parts" for inst in a.instances)
