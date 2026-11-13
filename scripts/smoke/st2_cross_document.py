"""Cross-Document sources smoke (slice S2): use Library's parts and variables from Project at a pinned Version.

Gate script of the Top-Down Design plan (helpers in topdown_common.py). Library (a Variable Studio Dims with w and t,
a Part Studio Parts with Plate and Rod, Version v1) and Project (Part Studios Mine and Use, an Assembly Asm) are
saved and registered in a temp registry; only Project is opened, in a window whose tabs build in worker processes.
Library's Parts and Project's Mine are both tab T1. The script then, as a person would:

  * Derives Plate from Library v1 in Use (the Derived dialog, run_command "derived");
  * inserts Plate from Library v1 in Asm (the picker, run_action "insert_other_document");
  * references Library v1's variables in Use, ticking w only (run_command "variable_studio_ref");
  * checks the Derived's volume, the instance's hash and the variables, with Library closed and no worker having
    opened a file of Library; and that a Library tab id is not read as Project's tab of the same id;
  * is refused each of the three without a Version;
  * makes Library v2, counts the three updates, checks all three still on v1, runs the Updates dialog's Update and
    checks all three on v2;
  * adds a local w after the reference and checks the local one is in force and the referenced one marked.

The window's engine starts worker processes that import this file again, hence the `__main__` guard.

    both fs_py scripts/smoke/st2_cross_document.py
"""
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

import topdown_common as T  # noqa: E402

REF = "variable_studio_ref"


def main():
    TMP = T.isolate("st2_cross_document_")
    T.run_smoke("ST2", TMP, lambda: run(TMP))


def run(TMP):
    from PySide6.QtCore import Qt
    from freeshape import links as L
    from freeshape.ui import versions_ui as V
    from freeshape.ui import document_picker as DP

    world = T.TwoDocuments(TMP)
    lib_id = world.lib_id
    plate_vol, plate_hash = world.v1[next(iter(world.v1))]
    T.check(world.mine_tab == world.parts_tab, "the two tab ids were meant to collide")
    mine_vol, mine_hash = world.mine[next(iter(world.mine))]
    T.check(abs(mine_vol - plate_vol) > 1 and mine_hash != plate_hash, "Mine must differ from Plate")

    app, w = T.new_engine_window(TMP)
    try:
        fd = w.open_document(world.proj_path)
        T.check(fd is not None and not fd.errors, fd and fd.errors)
        T.check(w.studio_engine.settle(240), "the engine did not settle on Project")
        use, asm = fd.tab(world.use_tab).doc, fd.tab(world.asm_tab).doc
        settle = lambda: T.check(w.studio_engine.settle(120), "the engine did not settle")     # noqa: E731
        select = lambda doc: (w.tabs.setCurrentIndex(w._tab_index(doc)), app.processEvents())  # noqa: E731
        T.spy_all_workers(w)

        def derived():
            return next(f for f in use.features if f["type"] == "derived")

        def derived_volume():
            from freeshape import kernel as K
            f = derived()
            T.check(not use.errors.get(f["id"]), use.errors)
            parts = [p for i, p in use.state.parts.items() if i.startswith(f["id"] + "/")]
            T.check(len(parts) == 1, f"derived parts {list(use.state.parts)}")
            return K.volume(parts[0].shape)

        def instance_hash():
            out = w.studio_engine.in_engine(asm, lambda eng, tab: eng.outputs(tab))
            T.check(not out.errors, out.errors)
            T.check(len(out.instances) == 1, out.instances)
            return out.instances[0].part

        # ---- Derived from Library v1: the dialog refuses until a Version is pinned
        select(use)
        T.run_command(w, "derived")
        d = w.dialog
        pk = d.widgets["document"]
        T.check(pk.is_local() and d.ok_button.isEnabled(), "the Derived dialog does not start on This Document")
        pk.doc_combo.setCurrentIndex(pk.doc_combo.findData(lib_id))
        T.check(pk.selection() is None and not d.ok_button.isEnabled(), "Library chosen with no Version is accepted")
        d.accept()
        T.check(w.dialog is d, "the Derived dialog committed with no Version")
        pk.version_combo.setCurrentIndex(pk.version_combo.findData("v1"))
        combo = d.widgets["source"]
        combo.setCurrentIndex(combo.findData(world.parts_tab))
        parts = d.widgets["parts"]
        row = next((i for i in range(parts.count()) if parts.item(i).text().startswith(world.plate_name)), None)
        T.check(row is not None, [parts.item(i).text() for i in range(parts.count())])
        parts.item(row).setCheckState(Qt.Checked)
        src = d.feature["params"]["source"]
        T.check(src["document"] == lib_id and src["version"] == "v1" and src["tab"] == world.parts_tab, src)
        w.commit_dialog()
        settle()
        v_derived = derived_volume()
        T.check(abs(v_derived - plate_vol) < 1e-6 * plate_vol, f"derived volume {v_derived}, Library's {plate_vol}")
        T.check(abs(v_derived - mine_vol) > 1, "the Derived read Project's Mine, not Library's Parts")

        # ---- inserted from Library v1: Asm, the picker
        select(asm)
        w.asm.toggle_insert()
        T.check(w.asm.insert_panel is not None, "no insert panel")
        pick = DP.OtherDocumentDialog(fd)
        T.check(not pick.choose(lib_id, None, world.parts_tab, world.plate), "the picker chose a row with no Version")
        pick.deleteLater()
        T.run_action(w, "insert_other_document",
                     dict(document=lib_id, version="v1", tab=world.parts_tab, part=world.plate))
        settle()
        T.check(len(asm.instances) == 1, asm.instances)
        isrc = asm.instances[0]["source"]
        T.check(isrc["document"] == lib_id and isrc["version"] == "v1" and isrc["tab"] == world.parts_tab
                and "path" not in isrc, isrc)
        h_inst = instance_hash()
        T.check(h_inst == plate_hash, "the inserted part is not Library's Plate at v1")
        T.check(h_inst != mine_hash, "the inserted part is Project's Mine")

        # ---- variables from Library v1: w only
        select(use)
        T.run_command(w, REF)
        d = w.dialog
        pk = d.widgets["document"]
        T.check(pk.is_local() and d.ok_button.isEnabled(), "the reference dialog does not start on This Document")
        pk.doc_combo.setCurrentIndex(pk.doc_combo.findData(lib_id))
        T.check(not d.ok_button.isEnabled() and d.feature["params"]["source"] == {}, "no Version, yet committable")
        d.accept()
        T.check(w.dialog is d, "the reference dialog committed with no Version")
        pk.version_combo.setCurrentIndex(pk.version_combo.findData("v1"))
        lst = d.widgets["names"]
        T.check([lst.item(i).data(Qt.UserRole) for i in range(lst.count())] == ["w", "t"],
                "the dialog does not list Library's w and t")
        lst.item(1).setCheckState(Qt.Unchecked)
        T.check(d.feature["params"]["names"] == ["w"], d.feature["params"])
        rsrc = d.feature["params"]["source"]
        T.check(rsrc["document"] == lib_id and rsrc["version"] == "v1" and rsrc["tab"] == world.dims_tab, rsrc)
        w.commit_dialog()
        settle()
        ref = next(f for f in use.features if f["type"] == REF)
        T.check(not use.errors.get(ref["id"]), use.errors)
        T.check(use.state.vars == {"w": 20.0}, use.state.vars)

        # ---- Library closed, no worker opened its files; a tab id is Library's, not Project's
        T.check(all(os.path.realpath(t.doc.path or "") != os.path.realpath(world.lib_path)
                    for t in fd.tabs if getattr(t.doc, "path", None)) and fd.id != lib_id and fd.id == world.proj_id,
                "Library is open in the window")
        T.check(not any(t.id == lib_id for t in w.folder.tabs), "Library tabs in the window")
        T.spy_all_workers(w)                                  # a worker restarted by an edit lost its spy
        T.refresh_workers(w, [asm])                      # so the Assembly worker resolves its link again, spied
        settle()
        opens_1 = T.worker_opens(w)
        spied = {n for n, p in opens_1 if p == os.devnull}
        T.check({"Use", "Asm"} <= spied, f"the open spy is not in the workers of Use and Asm: {spied}")
        opens_1 = [(n, p) for n, p in opens_1 if p != os.devnull]
        lib_real = os.path.realpath(world.lib_path)
        bad = [(n, p) for n, p in opens_1 if os.path.realpath(p).startswith(lib_real + os.sep)]
        T.check(not bad, f"workers opened Library's files: {bad}")
        T.check(src["tab"] == world.mine_tab == "T1" and isrc["tab"] == "T1", (src, isrc))
        T.say(f"derived from Library v1: volume matches ({v_derived:g} mm3), with Library closed and 0 opens of "
              f"Library's files in any worker ({len(opens_1)} other opens seen, in {len(spied)} workers)")
        T.say(f"inserted from Library v1: hash matches, with Library closed and 0 opens of Library's files in any "
              f"worker")
        T.say("variables from Library v1: w only (w = 20 mm, no t), with Library closed and 0 opens of Library's "
              "files in any worker")
        T.say(f"same tab id: resolved to Library, not Project (T1 in both; Derived volume {v_derived:g} is Library's "
              f"Plate, not Mine's {mine_vol:g}; instance hash is Plate's)")

        # ---- no Version: refused, at the dialogs (above) and in the engine
        from freeshape import links as links_mod
        probe = use.make_feature("derived")
        probe["params"].update(source=links_mod.studio_source(fd.tab(world.use_tab), document=lib_id, version=None),
                               parts=[], sketches=[], connectors=[])
        probe["params"]["source"].update(tab=world.parts_tab)
        use.add_feature(probe)
        settle()
        T.check("has no Version pinned" in str(use.errors.get(probe["id"])), use.errors)
        use.undo()
        settle()
        T.check(probe["id"] not in use.errors, use.errors)
        T.say("no Version: refused (the Derived and reference dialogs will not commit, the picker will not choose, "
              "and a Derived with no Version is red: has no Version pinned)")

        # ---- Library v2: three updates available, still on v1 until Update
        world.make_v2()
        sources = L.cross_sources(fd)
        T.check(len(sources) == 3, [s for _t, s in sources])
        status = [fd.resolver.link_status(s) for _t, s in sources]
        n_up = sum(1 for s in status if s["update_available"])
        T.check(n_up == 3 and all(s["pinned"] == "v1" and s["latest"] == "v2" for s in status), status)
        ud = V.UpdatesDialog(w.versions_ui)
        T.check(list(ud.update_buttons) == [lib_id], list(ud.update_buttons))
        T.check(all(s["version"] == "v1" for _t, s in sources), "a pin moved before Update")
        T.check(abs(derived_volume() - plate_vol) < 1e-6 * plate_vol and instance_hash() == plate_hash
                and use.state.vars == {"w": 20.0}, "something left v1 before Update")

        ud.update_buttons[lib_id].click()
        settle()
        T.check(all(s["version"] == "v2" for _t, s in L.cross_sources(fd)), "a pin is not on v2 after Update")
        T.check(not [1 for _t, s in L.cross_sources(fd) if fd.resolver.link_status(s)["update_available"]],
                "updates still available")
        v2_vol, v2_hash = world.v2
        T.check(abs(derived_volume() - v2_vol) < 1e-6 * v2_vol and abs(v2_vol - plate_vol) > 1,
                f"derived volume {derived_volume()} after Update, want {v2_vol}")
        T.check(instance_hash() == v2_hash != plate_hash, "the inserted part is not Plate v2")
        T.check(use.state.vars == {"w": 35.0}, use.state.vars)
        ud.deleteLater()
        T.say(f"updates available: {n_up} after Library v2, still on v1 before Update and on v2 after (Derived volume "
              f"{plate_vol:g} -> {v2_vol:g} mm3, instance hash Plate v1 -> v2, w 20 -> 35 mm)")
        T.spy_all_workers(w)
        T.refresh_workers(w, [asm])
        settle()
        opens_2 = T.worker_opens(w)
        bad = [(n, p) for n, p in opens_2 if os.path.realpath(p).startswith(lib_real + os.sep)]
        T.check(not bad, f"workers opened Library's files after Update: {bad}")

        # ---- a local w after the reference: the local one is in force, the referenced one is marked
        select(use)
        local = {"id": use.new_id(), "type": "variable", "name": "w", "suppressed": False,
                 "params": {"name": "w", "expr": "7"}}
        use.add_feature(local)
        settle()
        T.check(use.state.vars["w"] == 7.0, use.state.vars)
        table = [r for r in use.variable_table() if r["name"] == "w"]
        T.check([(r["feature"], r["value"], r["overridden"]) for r in table] ==
                [(ref["id"], 35.0, True), (local["id"], 7.0, False)], table)
        w._populate_lists()
        panel = w.defined_vars
        names = [panel.item(r, 0).text() for r in range(panel.rowCount())]
        T.check(names == ["#w", "#w"], names)
        T.check([panel.overridden(r) for r in range(panel.rowCount())] == [True, False],
                "the panel does not mark the referenced w overridden")
        T.say("override: local w in force, referenced w marked (local 7 mm wins over Library's 35 mm; the "
              "panel marks the referenced row)")
    finally:
        w.studio_engine.shutdown()
        from freeshape.engine import process as P
        P.SPARES.clear()


if __name__ == "__main__":
    main()
