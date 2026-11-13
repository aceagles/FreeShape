"""Context smoke (slice S5): edit a Lid in the context of an Assembly with a Base, then update, outlive and reopen it.

Gate script of the Top-Down Design plan (helpers in topdown_common.py). A Document of Part Studios Base and Lid
and an Assembly with Lid fastened 5 mm above Base. In the offscreen window "Edit in context" (the
edit_in_context action) adds a Context feature to Lid; the script then checks the claim of the slice:

  * Base shows in Lid at its mated position, to within 1e-6 mm, and an extrude can stop at one of its faces;
  * editing Base leaves Lid as it was and marks the Context out of date; editing Lid leaves it current;
  * the update_context command rebuilds Lid against the new Base;
  * with the Assembly deleted the Context still shows Base, and says "assembly missing";
  * the published parts of Lid hold no Context body; a reopened Lid restores Base from its blobs, with Base
    and the Assembly not even present, and builds Base's studio no times.

There is no run_action / run_command in the app: the script triggers the QAction and runs the palette command.

    both fs_py scripts/smoke/st5_context.py
"""
import os
import shutil
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

import topdown_common as T  # noqa: E402

TMP = T.isolate("st5_context_")

import numpy as np  # noqa: E402
from conftest import TOP, add, block_doc, pick_faces, rect_lines, region_refs, shape_fingerprint, sketch_feature  # noqa: E402
from freeshape import doc_folder as DF  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import links as L  # noqa: E402
from freeshape.document import PartStudioDoc  # noqa: E402
from tests.test_assembly import face, mate  # noqa: E402

GAP = 5.0


def contexts(studio):
    return [f for f in studio.features if f["type"] == "context"]


def bbox(shape):
    lo, hi = K.bbox(shape)
    return np.array([lo, hi])


def set_depth(studio, depth):
    """Set the depth of the studio's first extrude, as an edit."""
    import copy
    f = copy.deepcopy(next(f for f in studio.features if f["type"] == "extrude"))
    f["params"]["depth"] = depth
    studio.replace_feature(f)
    T.check(not studio.errors, studio.errors)


def run():
    app, w = T.new_window(TMP)
    fd = DF.FolderDocument.create(os.path.join(TMP, "Doc"), name="Doc")
    lid = fd.add_tab("studio", "Lid")
    lid.doc.__dict__.update(block_doc(40, 20, 3).__dict__)
    base = fd.add_tab("studio", "Base")
    base.doc.__dict__.update(block_doc(40, 20, 10).__dict__)
    fd._attach(lid)
    fd._attach(base)
    asm = fd.add_tab("assembly", "Asm")
    a = asm.doc
    i_lid = a.insert(L.studio_source(lid, f"{lid.doc.features[1]['id']}/1"), name="Lid")
    i_base = a.insert(L.studio_source(base, f"{base.doc.features[1]['id']}/1"), name="Base")
    a.refresh(solve=False, notify=False)
    top = a.connector(i_base, "face", face(a, i_base, lambda f: f.get("n") and f["n"][2] > 0.9))
    bottom = a.connector(i_lid, "face", face(a, i_lid, lambda f: f.get("n") and f["n"][2] < -0.9))
    mate(a, "fastened", top, bottom, offset=GAP)
    T.check(not a.errors, a.errors)
    fd.save()
    path = fd.path
    fd = w.open_document(path)              # the way a user opens it: the window adopts every tab
    T.check(fd is not None and not fd.errors, fd and fd.errors)
    lid, base, asm = (fd.tab(t.id) for t in (lid, base, asm))
    a = asm.doc
    i_lid, i_base = (i["id"] for i in a.instances)         # inserted in this order
    w.tabs.setCurrentIndex(w._tab_index(asm.doc))
    app.processEvents()
    w.asm.selected_instance_ids = lambda: [i_lid]

    # ---- Edit in context: Lid, with Base shown where the Assembly mates it
    T.run_action(w, "edit_in_context")
    T.check(w.doc is lid.doc and len(contexts(lid.doc)) == 1, "Edit in context did not open Lid with one Context")
    cfid = contexts(lid.doc)[0]["id"]
    ref_id = f"{cfid}/1"
    ref = lid.doc.state.parts[ref_id]
    T.check(ref.reference, "the Context body is not a reference part")
    want = K.located(a.world_shape(i_base), np.linalg.inv(a.T(i_lid)))
    err = float(np.abs(bbox(ref.shape) - bbox(want)).max())
    T.check(err < 1e-6, f"Base off by {err:g} mm")
    T.check(abs(bbox(ref.shape)[1][2] - (-GAP)) < 1e-6, bbox(ref.shape))      # Base top is GAP below Lid's bottom
    T.say(f"context: Base shown at mated position, with the bounding-box error under 1e-6 mm (measured {err:.1e})")

    # ---- an extrude up to a Context face (a pin from Lid's bottom down to Base's top)
    pid = ref_id
    sub = pick_faces(lid.doc.state.parts[pid], lambda i: i.get("n") is not None and i["n"][2] > 0.9)
    T.check(sub, "no top face on the Context body")
    to = F.make_ref(pid, sub[0], "face", lid.doc.state.parts[pid].shape, lid.doc.state.named(pid))
    sid = sketch_feature(lid.doc, lambda s: rect_lines(s, 5, 5, 6, 6), TOP)
    pin = add(lid.doc, "extrude", regions=region_refs(lid.doc, sid), end="to_face", to=to, flip=True, op="new")

    def pin_volume():
        parts = [p for i, p in lid.doc.state.parts.items() if i.startswith(pin["id"])]
        T.check(len(parts) == 1, f"pin parts {list(lid.doc.state.parts)}")
        return K.volume(parts[0].shape)

    v1 = pin_volume()
    T.check(abs(v1 - 36 * GAP) < 1e-6 * 36 * GAP, f"pin volume {v1}, want {36 * GAP}")
    T.say(f"up to Context face: built (pin volume {v1:g} mm3 = 6 x 6 x {GAP:g})")

    # ---- Lid edited: the Context stays current
    status = lambda: fd.context_status(lid.id, cfid)           # noqa: E731
    T.check(status() == "current", status())
    set_depth(lid.doc, 4.0)
    fd.refresh_links()
    T.check(status() == "current", status())
    T.say("Lid edited: context still current")

    # ---- Base edited: Lid is as it was, the Context is out of date
    before = {k: shape_fingerprint(p.shape) for k, p in lid.doc.state.parts.items()}
    set_depth(base.doc, 14.0)
    fd.refresh_links()
    app.processEvents()
    after = {k: shape_fingerprint(p.shape) for k, p in lid.doc.state.parts.items()}
    T.check(before == after, "Lid's parts changed when Base was edited")
    T.check(status() == "out of date", status())
    T.say("Base edited: Lid unchanged, context out of date")

    # ---- update context: Lid rebuilt against the new Base (the pin is now 1 mm long)
    w.tabs.setCurrentIndex(w._tab_index(lid.doc))
    T.run_command(w, "update_context")
    T.check(status() == "current", status())
    T.check(not lid.doc.errors, lid.doc.errors)
    v2 = pin_volume()
    T.check(abs(v2 - 36 * (GAP - 4.0)) < 1e-6 * 36, f"pin volume after update {v2}, want {36 * (GAP - 4.0)}")
    T.say(f"update context: Lid rebuilt against new Base (pin volume {v1:g} -> {v2:g} mm3)")

    # ---- save, then work on the files as a stranger opening the Document would
    fd.save()
    path, lid_id, asm_id, lid_file = fd.path, lid.id, asm.id, lid.file
    lid_fp = {k: shape_fingerprint(p.shape) for k, p in lid.doc.state.parts.items()}
    w.folder = None

    fd2 = DF.FolderDocument.open(path)
    lid2 = fd2.tab(lid_id)
    fid2 = contexts(lid2.doc)[0]["id"]
    T.check(fd2.context_status(lid_id, fid2) == "current", "reopened Context not current")
    fd2.remove_tab(asm_id)
    T.check(fd2.context_status(lid_id, fid2) == "assembly missing", fd2.context_status(lid_id, fid2))
    lid2.doc.rebuild()
    T.check(not lid2.doc.errors and f"{fid2}/1" in lid2.doc.state.parts and lid2.doc.state.parts[f"{fid2}/1"].reference,
            lid2.doc.errors)
    T.say("assembly deleted: context still displayed (status: assembly missing)")

    # ---- published parts hold no Context body
    from freeshape.engine import InProcessEngine
    eng = InProcessEngine()
    tab = eng.open_tab(os.path.join(path, lid_file))
    published = [p.id for p in eng.outputs(tab).parts]
    T.check(published and not any(i.startswith(cfid + "/") for i in published), published)
    T.check(any(i.startswith(pin["id"]) for i in published), published)
    eng.close()
    T.say(f"published parts: no Context body ({len(published)} parts published, none from the Context)")

    # ---- reopen Lid alone: Base and the Assembly are gone from the folder, so only the blobs can restore Base
    copy = os.path.join(TMP, "copy")
    shutil.copytree(path, copy, ignore=shutil.ignore_patterns(".git"))
    for tid in (asm_id, base.id):
        os.remove(os.path.join(copy, fd.tab(tid).file))
    builds = []
    orig = PartStudioDoc.compute
    PartStudioDoc.compute = lambda self, *a, **k: (builds.append(self.name), orig(self, *a, **k))[1]
    try:
        again = PartStudioDoc.load(os.path.join(copy, lid_file))
    finally:
        PartStudioDoc.compute = orig
    T.check(not again.errors, again.errors)
    got = {k: shape_fingerprint(p.shape) for k, p in again.state.parts.items()}
    T.check(got == lid_fp and again.state.parts[f"{cfid}/1"].reference, "reopened Lid differs from the saved one")
    n_base = sum(1 for n in builds if n == "Base")
    T.check(n_base == 0 and builds, f"builds {builds}")
    T.say(f"reopened: restored from blobs, Base built {n_base} times")


T.run_smoke("ST5", TMP, run)
