"""Previews and the dependency view smoke (slice S6).

Gate script of the Top-Down Design plan (helpers in topdown_common.py). A Document of Part Studios Skeleton, Base
and Lid, and an Assembly holding Lid fastened above Base; Lid is a Derived of Skeleton (live) and of Base pinned
to Version v1, which then gets a v2. A second Document, Library, offers a Version for the Other Document picker.
In the offscreen window (shown, so the preview fillers run on the event loop) the script checks:

  * the Derived dialog's parts, sketches and connectors lists hold one picture per row, none left blank;
  * the Insert panel and the Other Document picker do the same for their part rows;
  * opening both again draws no new preview;
  * the Dependencies panel and links.dependency_view list what Lid uses (Skeleton live, Base pinned v1 with v2
    available) and what uses it (the Assembly);
  * after "Edit in context" and an edit of Base, the Context shows as out of date;
  * a Document whose Skeleton tab is gone lists Skeleton as missing.

Viewport drawing cannot be checked offscreen (QOpenGLWidget is unsupported); the dialog itself is not run modally.

    both fs_py scripts/smoke/st6_polish.py
"""
import copy
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

import topdown_common as T  # noqa: E402

TMP = T.isolate("st6_polish_")

from conftest import block_doc  # noqa: E402
from freeshape import doc_folder as DF  # noqa: E402
from freeshape import doc_registry as R  # noqa: E402
from freeshape import links as L  # noqa: E402
from tests.test_assembly import face, mate  # noqa: E402
from tests.test_derived_placement import top_face_connector  # noqa: E402

GAP = 5.0


def part_id(tab):
    """The first part of a block_doc studio: its extrude's first output."""
    return f"{tab.doc.features[1]['id']}/1"


def build(path_doc, path_lib):
    """Save both Documents; returns (Doc path, Library id, Library tab id, Library part id)."""
    reg = R.default_registry()
    lib = DF.FolderDocument.create(path_lib, name="Library", registry=reg)
    plate = lib.add_tab("studio", "Plate")
    plate.doc.__dict__.update(block_doc(30, 30, 6).__dict__)
    lib._attach(plate)
    lib_part = part_id(plate)
    lib.create_version("v1", "first")
    lib.save()

    fd = DF.FolderDocument.create(path_doc, name="Doc", registry=reg)
    tabs = {}
    for name, dims in (("Skeleton", (30, 20, 4)), ("Base", (40, 20, 10)), ("Lid", (40, 20, 3))):
        t = tabs[name] = fd.add_tab("studio", name)
        t.doc.__dict__.update(block_doc(*dims).__dict__)
        fd._attach(t)
    fd.add_tab("studio", "Use")                       # an empty studio to open the Derived dialog on
    skel, base, lid = tabs["Skeleton"], tabs["Base"], tabs["Lid"]
    top_face_connector(skel.doc, part_id(skel), "Top")
    fd.create_version("v1", "first")
    f = base.doc.features
    ext = copy.deepcopy(next(x for x in f if x["type"] == "extrude"))
    ext["params"]["depth"] = 14.0
    base.doc.replace_feature(ext)
    fd.create_version("v2", "taller Base")

    for src, version in ((L.studio_source(skel), None), (L.studio_source(base), "v1")):
        d = lid.doc.make_feature("derived")
        d["params"].update(source=src, parts=[part_id(skel if version is None else base)], sketches=[], connectors=[])
        if version:
            d["params"]["source"]["version"] = version
        lid.doc.add_feature(d)
        T.check(not lid.doc.errors, lid.doc.errors)

    asm = fd.add_tab("assembly", "Assembly")
    a = asm.doc
    i_lid = a.insert(L.studio_source(lid, part_id(lid)), name="Lid")
    i_base = a.insert(L.studio_source(base, part_id(base)), name="Base")
    a.refresh(solve=False, notify=False)
    top = a.connector(i_base, "face", face(a, i_base, lambda x: x.get("n") and x["n"][2] > 0.9))
    bottom = a.connector(i_lid, "face", face(a, i_lid, lambda x: x.get("n") and x["n"][2] < -0.9))
    mate(a, "fastened", top, bottom, offset=GAP)
    T.check(not a.errors, a.errors)
    fd.save()
    return fd.path, lib.id, plate.id, lib_part


def icon_state(icon, size):
    """'blank' for a null or placeholder icon, 'flat' for a one-colour picture, else 'picture'."""
    from freeshape.ui import preview_rows as PR
    if icon.isNull() or icon.cacheKey() == PR.placeholder_icon(size).cacheKey():
        return "blank"
    img = icon.pixmap(size, size).toImage()
    if img.width() != size:
        return "blank"
    colours = {img.pixel(x, y) for x in range(0, img.width(), 3) for y in range(0, img.height(), 3)}
    return "picture" if len(colours) > 1 else "flat"


def list_icons(lst, size):
    return [icon_state(lst.item(i).icon(), size) for i in range(lst.count())]


def tree_icons(tree, size):
    """The part rows of a tree (children of the top-level studio rows); top-level rows carry no preview, and
    neither do the Insert panel's sketch rows, which keep the plain sketch icon."""
    from freeshape.ui.assembly_ui import ROLE_KIND
    out = []
    for i in range(tree.topLevelItemCount()):
        top = tree.topLevelItem(i)
        out += [icon_state(top.child(j).icon(0), size) for j in range(top.childCount())
                if not top.child(j).isDisabled()       # "(no parts yet)" is a note, not a part row
                and top.child(j).data(0, ROLE_KIND) != "sketch"]
    return out


def derived_dialog(w, app, skel):
    from freeshape.ui import preview_rows as PR
    w.start_feature("derived")
    d = w.dialog
    combo = d.widgets["source"]
    combo.setCurrentIndex(combo.findData(skel.id))
    lists = [d.widgets[k] for k in ("parts", "sketches", "connectors")]
    T.check(all(lst.count() == 1 for lst in lists), [lst.count() for lst in lists])
    T.st6_wait(app, lambda: "blank" not in sum((list_icons(lst, PR.DERIVED_SIZE) for lst in lists), []), "Derived previews")
    states = sum((list_icons(lst, PR.DERIVED_SIZE) for lst in lists), [])
    d._timer.stop()                           # else its preview fires after the tab switch (harmless traceback)
    w.cancel_dialog()
    return states


def picker_and_panel(w, app, fd, lib_id, lib_tab, lib_part):
    from freeshape.ui import document_picker as DP
    from freeshape.ui import preview_rows as PR
    panel = w.asm.insert_panel
    dlg = DP.OtherDocumentDialog(fd)
    T.check(dlg.choose(lib_id, "v1", lib_tab, lib_part), "the picker would not choose Library v1 Plate")
    dlg.show()
    both = lambda: tree_icons(panel.tree, PR.INSERT_SIZE) + tree_icons(dlg.tree, PR.INSERT_SIZE)   # noqa: E731
    T.check(both(), "no part rows in the Insert panel or the picker")
    T.st6_wait(app, lambda: "blank" not in both(), f"Insert and picker previews (panel "
               f"{tree_icons(panel.tree, PR.INSERT_SIZE)}, picker {tree_icons(dlg.tree, PR.INSERT_SIZE)}, "
               f"visible {panel.tree.isVisible()} {dlg.tree.isVisible()})", 5)
    states = both()
    dlg.close()
    return states


def run():
    path, lib_id, lib_tab, lib_part = build(os.path.join(TMP, "Doc"), os.path.join(TMP, "Library"))
    app, w = T.new_window(TMP)
    fd = w.open_document(path)
    T.check(fd is not None and not fd.errors, fd and fd.errors)
    w.show()                                  # the preview fillers draw only once their widget is shown
    skel, base, lid, asm = (next(t for t in fd.tabs if t.name == n) for n in ("Skeleton", "Base", "Lid", "Assembly"))
    use = next(t for t in fd.tabs if t.name == "Use")

    from freeshape.ui import preview_rows as PR

    # ---- Derived dialog previews (the dialog is shown through its parent, not run modally)
    w.tabs.setCurrentIndex(w._tab_index(use.doc))
    drawn0 = PR.stats["drawn"]
    states = derived_dialog(w, app, skel)
    T.check(states and all(s == "picture" for s in states), states)
    T.check(PR.stats["drawn"] > drawn0, "the Derived dialog drew no preview")
    n = len(states)
    T.say(f"derived dialog: {n} rows, {states.count('picture')} pictures, {states.count('blank')} blank")

    # ---- Insert panel and Other Document picker (part rows only: studio rows have no preview)
    w.tabs.setCurrentIndex(w._tab_index(asm.doc))
    w.asm.toggle_insert()
    T.check(w.asm.insert_panel is not None, "the Insert panel did not open")
    drawn1 = PR.stats["drawn"]
    states = picker_and_panel(w, app, fd, lib_id, lib_tab, lib_part)
    T.check(states and all(s == "picture" for s in states), states)
    T.check(PR.stats["drawn"] > drawn1, "the Insert panel and picker drew no preview")
    m = len(states)
    T.say(f"insert picker: {m} rows, {states.count('picture')} pictures, {states.count('blank')} blank")

    # ---- reopening draws nothing new
    drawn2 = PR.stats["drawn"]
    w.tabs.setCurrentIndex(w._tab_index(use.doc))
    again = derived_dialog(w, app, skel)
    w.tabs.setCurrentIndex(w._tab_index(asm.doc))
    if w.asm.insert_panel is None:                # leaving the Assembly tab closed the Insert panel
        w.asm.toggle_insert()
    again += picker_and_panel(w, app, fd, lib_id, lib_tab, lib_part)
    T.check(len(again) == n + m and all(s == "picture" for s in again), again)
    T.check(PR.stats["drawn"] == drawn2, f"{PR.stats['drawn'] - drawn2} new previews on the second open")
    T.say("second open: 0 new previews")

    # ---- dependencies of Lid: the view and the panel
    view = L.dependency_view(fd, lid.id)

    def shown(e):
        s = f"{e['name']} {e['state']}"
        if e["state"] in ("pinned", "update available"):
            s = f"{e['name']} pinned {e['pinned']}" + (f" update {e['latest']}" if e["state"] == "update available" else "")
        return s
    uses = ", ".join(shown(e) for e in view["uses"])
    used_by = ", ".join(e["name"] for e in view["used_by"])
    T.check(uses == "Skeleton live, Base pinned v1 update v2" and used_by == "Assembly", (uses, used_by))
    w.tabs.setCurrentIndex(w._tab_index(lid.doc))
    p_uses, p_used_by = w.dependencies.entries()
    T.check(len(p_uses) == 2 and "Skeleton" in p_uses[0] and "live" in p_uses[0], p_uses)
    T.check("Base" in p_uses[1] and "update available v2" in p_uses[1], p_uses)
    T.check(len(p_used_by) == 1 and "Assembly" in p_used_by[0], p_used_by)
    T.say(f"dependencies of Lid: uses {uses}; used by {used_by}")

    # ---- Edit in context, then edit Base: the Context shows as out of date
    a = asm.doc
    i_lid = a.instances[0]["id"]               # Lid was inserted first
    w.tabs.setCurrentIndex(w._tab_index(asm.doc))
    app.processEvents()
    w.asm.selected_instance_ids = lambda: [i_lid]
    T.run_action(w, "edit_in_context")
    T.check(w.doc is lid.doc, "Edit in context did not open Lid")
    cfid = next(f["id"] for f in lid.doc.features if f["type"] == "context")
    T.check(fd.context_status(lid.id, cfid) == "current", fd.context_status(lid.id, cfid))
    ctx = [e for e in L.dependency_view(fd, lid.id)["uses"] if e["kind"] == "Context"]
    T.check([(e["name"], e["state"]) for e in ctx] == [("Assembly", "current")], ctx)
    ext = copy.deepcopy(next(f for f in base.doc.features if f["type"] == "extrude"))
    ext["params"]["depth"] = 20.0
    base.doc.replace_feature(ext)
    T.check(not base.doc.errors, base.doc.errors)
    fd.refresh_links()
    app.processEvents()
    ctx = [e for e in L.dependency_view(fd, lid.id)["uses"] if e["kind"] == "Context"]
    T.check([(e["name"], e["state"]) for e in ctx] == [("Assembly", "out of date")], ctx)
    w.tabs.setCurrentIndex(w._tab_index(base.doc))       # the panel refreshes on a tab switch, as when a user edits Base
    w.tabs.setCurrentIndex(w._tab_index(lid.doc))        # (the edit above went through the Document API) and returns
    T.check(any("Context" in s and "out of date" in s for s in w.dependencies.entries()[0]), w.dependencies.entries())
    T.say("out-of-date context shown")

    # ---- a source tab that is gone shows as missing (on a copy opened from disk, as a stranger would)
    fd.save()
    skel_id, lid_id = skel.id, lid.id
    w.folder = None
    fd2 = DF.FolderDocument.open(path)
    fd2.remove_tab(skel_id)
    gone = next(e for e in L.dependency_view(fd2, lid_id)["uses"] if e["name"] == "Skeleton")
    T.check(gone["state"] == "missing" and gone["tab"] == skel_id, gone)
    T.say("missing source shown")


T.run_smoke("ST6", TMP, run)
