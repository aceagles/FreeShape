"""Headless Document-structure smoke (slice S4, gate I4.10): no MainWindow, no Qt, no network.

Part of the cumulative smoke gate (see s00_baseline.py for the convention). In a temporary directory it builds its own
Documents and walks the slice claim one clause per step: a Document with all four tab kinds is created, saved, closed
and reopened on an in-memory store with the write guard recording no write; the same script on a folder store gives
byte-identical tab data; a fifth tab kind needs one registry entry to be listed, loaded and saved; every shipped example
Assembly and Drawing loads to the geometry hash recorded before the sidecar migration and saves without inline base64;
a link source naming another Document never matches a local tab with the same id (in-process engine); and
`docs/reference/document-format.md` names every key the code writes. Setup and expected values come from tests/
(test_memory_store, test_tabkinds, test_sidecar_migration, test_link_source_document_id, test_format_reference), so
the smoke and the tests cannot drift apart. Ends by asserting PySide6 was never imported.

One line per step, ending `ok`; a failed step prints why and the script exits 1 with no `SQ4 OK`.

    both fs_py scripts/smoke/sq4_structure.py
"""
import os
import re
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

TMP = tempfile.mkdtemp(prefix="sq4_structure_")
os.environ["FREESHAPE_DATA_DIR"] = os.path.join(TMP, "appdata")     # nothing may touch a real user folder
os.environ["XDG_DATA_HOME"] = os.path.join(TMP, "xdg")
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["FREESHAPE_PLUGIN_DIR"] = os.path.join(TMP, "plugins")
for _d in ("appdata", "xdg", "home", "plugins"):
    os.makedirs(os.path.join(TMP, _d))

import pytest  # noqa: E402

import test_format_reference as TFR  # noqa: E402
import test_link_source_document_id as TLS  # noqa: E402
import test_memory_store as TMS  # noqa: E402
import test_sidecar_migration as TSM  # noqa: E402
import test_tabkinds as TTK  # noqa: E402
from freeshape import doc_folder as DF  # noqa: E402
from freeshape import docstore as DS  # noqa: E402
from freeshape import migrations as M  # noqa: E402
from freeshape import pyfeature  # noqa: E402
from freeshape import tabkinds as TK  # noqa: E402
from freeshape.engine import InProcessEngine  # noqa: E402
from freeshape.engine import executor as EX  # noqa: E402
from freeshape.engine import process as P  # noqa: E402


# ---------------------------------------------------------------------- helpers
class Fail(Exception):
    pass


def check(cond, msg):
    if not cond:
        raise Fail(msg)


STEPS = []


def step(name):
    """Run a step function; print `name ... ok`, or the reason and exit 1."""
    def deco(fn):
        try:
            fn()
        except BaseException as e:                   # noqa: BLE001 -- any failure fails the gate (asserts included)
            print(f"{name} ... FAILED: {e!r}")
            if not isinstance(e, Fail):
                traceback.print_exc()
            _cleanup()
            sys.exit(1)
        STEPS.append(name)
        print(f"{name} ... ok", flush=True)
        return fn
    return deco


def _cleanup():
    P.SPARES.clear()
    shutil.rmtree(TMP, ignore_errors=True)


def tmp(name):
    d = Path(TMP) / name
    d.mkdir()
    return d


def unwrap(fixture):
    """The plain function behind a pytest fixture, so a smoke can run it without pytest's runner."""
    fn = getattr(fixture, "__wrapped__", None)
    if fn is None and hasattr(fixture, "_get_wrapped_function"):
        fn = fixture._get_wrapped_function()
    check(callable(fn), "cannot unwrap fixture")
    return fn


def run_no_writes(body):
    """Call body(tmp_path) with the tests' write guard installed (refuses any write under tmp_path and the cwd)."""
    mp = pytest.MonkeyPatch()
    path = tmp("guarded")
    try:
        gen = unwrap(TMS.no_writes)(mp, path)
        if gen is not None and hasattr(gen, "__next__"):          # a generator fixture
            next(gen)
        return body(path)
    finally:
        mp.undo()


# ---------------------------------------------------------------------- the claim, one clause per step
@step("in-memory Document: four tab kinds saved, closed, reopened, no write")
def _():
    def body(path):
        store = DS.MemoryStore()
        fd = DF.FolderDocument.create_on(store, name="doc-mem")
        check(fd.path is None and fd.store is store, "the Document is not on the memory store")
        st, pid = TMS._build(fd)
        check(sorted(t.kind for t in fd.tabs) == sorted(["studio", "variables", "assembly", "drawing"]),
              [t.kind for t in fd.tabs])
        want = TMS._hash(st.doc.state.parts[pid])
        fd.save()
        check(not fd.unsaved_tabs(), "tabs left unsaved")
        ids = [t.id for t in fd.tabs]
        check(sorted(store.list_tabs()) == sorted(ids), (store.list_tabs(), ids))
        del fd, st                                                # closed
        back = DF.FolderDocument.open_on(store)
        check(not back.errors and not back.warnings, (back.errors, back.warnings))
        check([t.id for t in back.tabs] == ids and back.path is None, "tabs changed across reopen")
        inst = back.tab("T3").doc.instances[0]
        check(TMS._hash(back.tab("T2").doc.state.parts[pid]) == want, "studio geometry changed")
        check(TMS._hash(back.tab("T3").doc.parts[inst["id"]]) == want, "instance geometry differs from the studio's")
        check(back.tab("T4").doc.shape is not None and not back.tab("T4").doc.error, "Drawing did not load")
        # the guard refuses any write under the temp folder and the cwd; prove it is live, then it stayed silent
        try:
            open(path / "x", "w")
        except AssertionError:
            pass
        else:
            raise Fail("the write guard did not refuse a write")
        check(not os.listdir(path) and not os.path.exists(os.path.join(os.getcwd(), "T1.fshape")), "a file was written")
    run_no_writes(body)


@step("folder store gives byte-identical tab data to the memory store")
def _():
    path = tmp("bytes")
    mem = DS.MemoryStore()
    fm = DF.FolderDocument.create_on(mem)
    TMS._build(fm)
    fm.save()
    folder = DS.FolderStore(str(path / "doc"))
    ff = DF.FolderDocument.create_on(folder)
    TMS._build(ff)
    ff.save()
    check([t.file for t in fm.tabs] == [t.file for t in ff.tabs], "file names differ")
    a, b = TMS._tab_data(mem, fm), TMS._tab_data(folder, ff)
    check(len(a) == 4 and all(a[k] for k in a), "tab data is empty")
    check(a == b, "tab data differs between the stores")
    check(mem.read_manifest() == folder.read_manifest(), "manifest differs between the stores")
    for t in ff.tabs:                                              # and what is on disk is that data
        with open(os.path.join(ff.path or str(path / "doc"), t.file), "rb") as fh:
            check(fh.read() == b[t.id], f"{t.file} on disk differs from the store's data")


@step("fifth tab kind: one registry entry lists, loads and saves it")
def _():
    path = tmp("fifth")
    before = TK.kinds()
    check("stub" not in before, before)
    TK.register(TK.TabKind("stub", ".fstub", "freeshape-stub", None, TTK._Stub.load, TTK._Stub, "Stub", "Stubs"))
    try:
        check(TK.kinds() == before + ["stub"] and "stub" in EX.KINDS and DF.EXTENSIONS["stub"] == ".fstub",
              "the kind is not listed")
        fd = DF.FolderDocument.create(str(path / "D"))
        tab = fd.add_tab("stub", "Widget")
        tab.doc.note = "hello"
        fd.save()
        check(os.path.exists(os.path.join(fd.path, "Widget.fstub")), "the stub tab file was not saved")
        back = DF.FolderDocument.open(fd.path)
        check(not back.errors, back.errors)
        check([(t.kind, t.doc.note) for t in back.tabs] == [("stub", "hello")], "the stub tab did not load")
    finally:
        TK.unregister("stub")
    check(TK.kinds() == before and "stub" not in DF.EXTENSIONS and "stub" not in EX.KINDS, "the kind is still listed")


@step("example Assemblies: same geometry hash as before, no inline base64 after save")
def _():
    for rel in sorted(TSM.BEFORE):
        want = TSM.BEFORE[rel]
        src = os.path.join(TSM.REPO, rel)
        check(TSM.A.AssemblyDoc.load(src).parts and TSM._asm_hash(TSM.A.AssemblyDoc.load(src)) == want["fallback"],
              f"{rel}: fallback geometry hash changed")
        TSM.test_example_assembly_loads_to_the_same_geometry_and_saves_without_base64(rel, tmp("asm_" + Path(rel).stem))
    TSM.test_a_resolved_example_saves_copies_of_its_live_parts(tmp("asm_resolved"))
    check(len(TSM.BEFORE) >= 2, "expected the two shipped example Assemblies")


@step("shipped Drawing: same geometry hash as before, no inline base64 after save")
def _():
    for rel in sorted(TSM.BEFORE_DRAWINGS):
        want = TSM.BEFORE_DRAWINGS[rel]
        g = TSM.DW.DrawingDoc.load(os.path.join(TSM.REPO, rel))
        check(TSM._hash(g.shape) == want["shape"], f"{rel}: geometry hash changed")
        TSM.test_shipped_drawing_loads_to_the_same_geometry_and_saves_without_base64(rel, tmp("dwg_" + Path(rel).stem))
    check(TSM.BEFORE_DRAWINGS, "no recorded Drawing")


@step("link source naming another Document never matches a local tab")
def _():
    world = TLS.World(tmp("link"))
    try:
        check(world.a_hash != world.b_hash and world.a_st.id == world.b_st.id == "T1", "the two T1s are not distinct")
        eng, tabs = world.engine("inprocess", [world.b_st, world.asm])
        try:
            out = eng.outputs(tabs[world.asm.id])
            check(not out.errors, out.errors)
            got = [i.part for i in out.instances]
            check(got == [world.a_hash], f"instance shows {got}, want the other Document's part {world.a_hash}")
            check(got != [world.b_hash], "instance shows the local tab's part")
        finally:
            eng.close()
        eng = InProcessEngine()
        try:
            eng.open_tab(world.path(world.b_st), tab="T1")
            from freeshape.engine.inputs import external_id
            check(eng.provide("studio", tab=external_id(world.cross())) is None,
                  "provide returned a local tab for another Document")
            check(eng.provide("studio", tab="T1") is not None, "provide lost the local tab")
        finally:
            eng.close()
    finally:
        P.SPARES.clear()


@step("document-format.md names every key the code writes")
def _():
    saved = {p.id for p in pyfeature.plugins()}
    try:
        TFR.test_manifest_and_tab_entry_keys_are_documented(tmp("fmt_manifest"))
        TFR.test_part_studio_keys_are_documented(tmp("fmt_studio"))
        TFR.test_assembly_keys_are_documented(tmp("fmt_asm"))
        TFR.test_drawing_keys_are_documented(tmp("fmt_dwg"))
        TFR.test_variable_studio_keys_are_documented(tmp("fmt_vars"))
        for fmt in sorted(M._CHAINS):
            TFR.test_every_registered_step_is_listed(fmt)
    finally:
        for p in pyfeature.plugins():
            if p.id not in saved:
                pyfeature.unregister_plugin(p.id)
    check(re.search(r"`_tab_id`", TFR._sections()["Key stamped into every Tab file"]), "_tab_id not documented")


@step("headless: PySide6 never imported")
def _():
    check("PySide6" not in sys.modules, "PySide6 was imported")


print(f"SQ4 OK: {len(STEPS)} steps")
_cleanup()
