"""I1.3: a Derived feature keeps a saved copy of what it built, as blobs, and shows it when its source is missing.
A fillet that named a face of the derived part still resolves on the copy; a present source ignores the copy."""
import json
import os

from conftest import add, line_along, rect_lines, region_refs, sketch_feature
from freeshape import blobstore
from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import features as F
from freeshape import kernel as K
from freeshape import links as L


def _folder(tmp_path):
    return DF.FolderDocument.create(str(tmp_path / "Doc"), registry=R.default_registry())


def _world(tmp_path, fillet=False):
    """A Document with Part Studio Src (a 20 x 10 x 5 box) and Use, which derives it (and fillets a vertical edge
    of the derived part when asked), saved."""
    fd = _folder(tmp_path)
    src = fd.add_tab("studio", "Src")
    sid = sketch_feature(src.doc, lambda s: rect_lines(s, 0, 0, 20, 10))
    add(src.doc, "extrude", regions=region_refs(src.doc, sid), depth=5, op="new")
    use = fd.add_tab("studio", "Use")
    der = add(use.doc, "derived", source=L.studio_source(src), parts=["F2/1"], sketches=[], connectors=[])
    pid = f"{der['id']}/1"
    fil = None
    if fillet:
        part = use.doc.state.parts[pid]
        vertical = [e for e in K.edges(part.shape) if line_along((0, 0, 1))(K.edge_info(e))]
        edges = [F.make_ref(pid, vertical[0], "edge", part.shape, use.doc.state.named(pid))]
        fil = add(use.doc, "fillet", edges=edges, radius=1)
    fd.save()
    return fd, src, use, der, pid, fil


def _blob_files(fd, use):
    root = os.path.join(os.path.dirname(use.doc.path), "blobs")
    return set(os.listdir(root)) if os.path.isdir(root) else set()


def _reopen_without_source(fd, src, use_id):
    fd.remove_tab(src.id)
    fd.save()
    path = fd.path
    fd.close()
    return DF.FolderDocument.open(path, registry=R.default_registry())


def test_the_derived_part_stays_when_its_source_is_removed(tmp_path):
    fd, src, use, der, pid, _ = _world(tmp_path)
    vol = K.volume(use.doc.state.parts[pid].shape)
    assert use.doc.notices().get(der["id"]) is None
    fd2 = _reopen_without_source(fd, src, use.id)
    doc = fd2.tab(use.id).doc
    assert not doc.errors, doc.errors
    assert abs(K.volume(doc.state.parts[pid].shape) - vol) < 1e-9
    note = doc.notices()[der["id"]]
    assert "Src" in note and "missing" in note and "stale" in note


def test_a_fillet_on_a_face_of_the_derived_part_resolves_on_the_copy(tmp_path):
    fd, src, use, der, pid, fil = _world(tmp_path, fillet=True)
    vol = K.volume(use.doc.state.parts[pid].shape)
    fd2 = _reopen_without_source(fd, src, use.id)
    doc = fd2.tab(use.id).doc
    assert not doc.errors, doc.errors
    assert fil["id"] not in doc.state.recovered
    assert abs(K.volume(doc.state.parts[pid].shape) - vol) < 1e-9
    assert vol < 20 * 10 * 5 - 1e-6        # the fillet took material, so the edge was found


def test_the_copy_is_blobs_under_sha256_keys_and_nothing_inline(tmp_path):
    fd, src, use, der, pid, _ = _world(tmp_path)
    data = json.loads(open(use.doc.path).read())
    entry = data["python_builds"][der["id"]]
    keys = [rec[k] for rec in entry["parts"] for k in ("brep", "names")]
    assert len(keys) == 2 and set(keys) <= _blob_files(fd, use)
    for k in keys:
        assert len(k) == 64 and blobstore.key_of(open(os.path.join(os.path.dirname(use.doc.path), "blobs", k), "rb").read()) == k
    text = open(use.doc.path).read()
    assert "BREP" not in text and "CASCADE" not in text and len(text) < 3000


def test_saving_twice_with_no_change_adds_no_blob(tmp_path):
    fd, src, use, der, pid, _ = _world(tmp_path)
    before = _blob_files(fd, use)
    text = open(use.doc.path).read()
    use.doc.dirty = True
    fd.save()
    assert _blob_files(fd, use) == before and open(use.doc.path).read() == text
    path = fd.path
    fd.close()
    fd2 = DF.FolderDocument.open(path, registry=R.default_registry())
    fd2.tab(use.id).doc.dirty = True
    fd2.save()
    assert _blob_files(fd2, fd2.tab(use.id)) == before


def test_with_the_source_present_the_copy_is_ignored_and_refreshed(tmp_path):
    fd, src, use, der, pid, _ = _world(tmp_path)
    old = _blob_files(fd, use)
    # a copy that disagrees with the source: the live source wins, and the next save writes the live parts
    from freeshape.sketch import Sketch
    s = Sketch()
    rect_lines(s, 0, 0, 30, 10)
    s.solve()
    src.doc.features[0]["params"]["sketch"] = s.to_dict()
    src.doc.rebuild()
    fd.refresh_links()
    assert use.doc.notices().get(der["id"]) is None
    assert K.volume(use.doc.state.parts[pid].shape) > 20 * 10 * 5 * 1.4
    use.doc.dirty = True
    fd.save()
    new = _blob_files(fd, use)
    assert new - old, "the refreshed copy is written"
    entry = json.loads(open(use.doc.path).read())["python_builds"][der["id"]]
    assert entry["parts"][0]["brep"] in new


def test_a_copy_made_for_another_source_configuration_is_not_shown(tmp_path):
    fd, src, use, der, pid, _ = _world(tmp_path)
    fd2 = _reopen_without_source(fd, src, use.id)
    doc = fd2.tab(use.id).doc
    assert not doc.errors and pid in doc.state.parts           # the copy of the source as it was named
    f = doc.feature(der["id"])
    f["params"]["source"] = dict(f["params"]["source"], config="Wide")     # now another configuration is wanted
    doc.rebuild()
    assert der["id"] in doc.errors and pid not in doc.state.parts
