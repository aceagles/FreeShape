"""Tab engine smoke (slice S3): five tabs on the process executor, an offscreen MainWindow, one line per check.

Part of the cumulative smoke gate (see s00_baseline.py for the convention). A Document of Variable Studio V,
Part Studio A (extrude depth #w), Assembly C (two instances of A's part, a fastened mate between two named
faces), Drawing D of C, and an unrelated Part Studio E opens in a window running on the process executor
(`MainWindow(engine=True)`: one worker process per tab). The script then edits, kills and switches, and checks
what the claim of the slice says:

  * an edit to V that is not saved rebuilds V, A, C and D once each and E not at all; A's worker is handed V's
    new value by the parent and never opens V's file; C and D show the change with the assembly tab current;
  * the mate in C resolves its face by element name, not by signature, before and after an edit to A;
  * a rebuild that takes 2 s in A's worker does not stop the window's timers;
  * SIGKILL to A's worker errors A only; restart brings it back with an edit that was never saved;
  * a Workspace switch resets every tab to the target branch.

How the rebuild counters are read. The engine counts a rebuild two ways: `rebuild_count(tab)` counts rebuilds
the Link graph caused (a consumed output changed), and a tab's `revision` counts the edits applied to it. An edit
to V is one revision of V and one graph rebuild each of A, C and D, so a tab's rebuilds are the delta of both.

    both fs_py scripts/smoke/s3_engine.py

Entry points that start a ProcessEngine need the `__main__` guard: a worker imports this file again.
"""
import os
import sys
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

SLOW_S = 2.0
TICK_MS = 20
MAX_GAP_S = 0.25


# ------------------------------------------------------------------ run inside a tab's worker (by name)
def _spy_opens():
    """Record every path this worker opens from now on."""
    import builtins
    seen, real = [], builtins.open

    def spy(file, *a, **k):
        if isinstance(file, (str, bytes, os.PathLike)):
            seen.append(os.path.realpath(file))
        return real(file, *a, **k)

    builtins.open = spy
    from freeshape.engine import worker as W
    W.current().opened = seen


def _opened():
    from freeshape.engine import worker as W
    return list(W.current().opened)


def _slow_rebuild(seconds):
    """The tab's next rebuilds from a changed input take `seconds` longer."""
    from freeshape.engine import worker as W
    tab = W.current().engine._get(W.current().tab)
    real = tab.rebuild

    def slow():
        time.sleep(seconds)
        return real()

    tab.rebuild = slow


def _fast_rebuild():
    from freeshape.engine import worker as W
    W.current().engine._get(W.current().tab).__dict__.pop("rebuild", None)


# ------------------------------------------------------------------ the script
class Fail(Exception):
    pass


def check(cond, msg):
    if not cond:
        raise Fail(msg)


def say(msg):
    print(msg, flush=True)


def children():
    me, out = os.getpid(), []
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                with open(f"/proc/{d}/stat") as fh:
                    rest = fh.read().rsplit(")", 1)[1].split()
                with open(f"/proc/{d}/cmdline", "rb") as fh:
                    cmd = fh.read()
                if int(rest[1]) == me and rest[0] != "Z" and b"spawn_main" in cmd:
                    out.append(int(d))
            except (OSError, ValueError, IndexError):
                pass
    return out


def main():
    import shutil
    import signal
    import tempfile
    import traceback

    tmp = tempfile.mkdtemp(prefix="s3_engine_")
    os.environ["FREESHAPE_DATA_DIR"] = os.path.join(tmp, "appdata")
    os.environ["XDG_DATA_HOME"] = os.path.join(tmp, "xdg")
    os.environ["HOME"] = os.path.join(tmp, "home")
    for d in ("appdata", "xdg", "home", "autosave", "thumbs"):
        os.makedirs(os.path.join(tmp, d))
    before = set(children())
    win = []
    try:
        run(tmp, win)
    except BaseException as e:                         # noqa: BLE001 -- any failure fails the gate
        say(f"S3 FAILED: {e}")
        if not isinstance(e, Fail):
            traceback.print_exc()
        code = 1
    else:
        say("S3 OK")
        code = 0
    finally:
        for w in win:
            try:
                w.studio_engine.shutdown()
            except Exception:                          # noqa: BLE001
                pass
        from freeshape.engine import process as P
        P.SPARES.clear()
        for p in sorted(set(children()) - before):
            try:
                os.kill(p, signal.SIGKILL)
            except OSError:
                pass
        shutil.rmtree(tmp, ignore_errors=True)
    return code


def run(tmp, win):
    import shutil

    from PySide6.QtCore import QCoreApplication, QSettings, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from conftest import add, normal_is, rect_lines, region_refs, sketch_feature
    from freeshape import assembly as ASM
    from freeshape import doc_folder as DF
    from freeshape import doc_registry as R
    from freeshape import kernel as K
    from freeshape import links as L
    from freeshape.engine import InProcessEngine
    from freeshape.ui import home as H
    from freeshape.ui import mainwindow as MW
    from freeshape.ui.partstudio_engine import geometry_hash
    import signal

    check(shutil.which("git"), "needs git on PATH (Workspaces)")
    REF = "variable_studio_ref"

    # ---------------------------------------------------------------- the Document
    def studio(fd, name, vtab, depth):
        tab = fd.add_tab("studio", name)
        doc = tab.doc
        doc.var_inputs = lambda src: {"w": 20.0}           # only to build the file: the engine resolves V for real
        doc.add_feature({"id": doc.new_id(), "type": REF, "name": "Variables", "suppressed": False,
                         "params": {"source": {"kind": "variables", "tab": vtab.id, "name": vtab.name}}})
        sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 10, 10))
        add(doc, "extrude", regions=region_refs(doc, sid), depth=depth, op="new")
        doc.var_inputs = None
        return tab

    def set_extrude_depth(doc, depth):
        f = next(f for f in doc.features if f["type"] == "extrude")
        doc.replace_feature({**f, "params": {**f["params"], "depth": depth}})

    def build_document():
        fd = DF.FolderDocument.create(os.path.join(tmp, "Doc"), registry=R.default_registry())
        v = fd.add_tab("variables", "V")
        v.doc.add_row("w", "20 mm")
        a = studio(fd, "A", v, "#w")
        e = fd.add_tab("studio", "E")                       # reads nothing: unrelated to V, A, C and D
        sid = sketch_feature(e.doc, lambda s: rect_lines(s, 0, 0, 8, 8))
        add(e.doc, "extrude", regions=region_refs(e.doc, sid), depth=7.0, op="new")
        c = fd.add_tab("assembly", "C")
        pid = next(iter(a.doc.state.parts))
        for n in ("A1", "A2"):
            c.doc.insert(L.studio_source(a, pid), name=n)
        c.doc.refresh(solve=False, notify=False)
        d = fd.add_tab("drawing", "D")
        d.doc.source = L.assembly_source(c)
        d.doc.refresh(notify=False)
        fd.save()
        fd.create_version("v1", "base")
        fd.create_workspace("alt", "v1", switch=False)
        fd.switch_workspace("alt")                          # "alt": a different w and a different E, saved
        fd.tab(v.id).doc.set_row(0, expression="50 mm")
        set_extrude_depth(fd.tab(e.id).doc, 9.0)
        fd.tab(e.id).doc.dirty = True
        fd.save()
        fd.switch_workspace("main")
        path = fd.path
        ids = {"V": v.id, "A": a.id, "C": c.id, "D": d.id, "E": e.id}
        fd.close()
        return path, ids

    def headless(path, edits=(), assembly=None):
        """What each tab shows, from the in-process engine over the files in `path` after `edits`
        [(tab id, edit)]: {tab id: hashes}. A Variable Studio is its values, a Drawing a geometry hash.
        `assembly`: the window's unsaved Assembly C, written over C's file in a copy of the Document first
        (its mate has moved an instance, which a drawing of C shows)."""
        if assembly is not None:
            copy = os.path.join(tmp, "headless")
            shutil.rmtree(copy, ignore_errors=True)
            shutil.copytree(path, copy)
            with open(os.path.join(copy, DF.FolderDocument.open(path).tab(ids["C"]).file), "w") as fh:
                fh.write(assembly.to_json(embed=False))
            path = copy
        fd = DF.FolderDocument.open(path)
        eng = InProcessEngine()
        order = {"variables": 0, "studio": 1, "assembly": 2, "drawing": 3}
        for t in sorted(fd.tabs, key=lambda t: order[t.kind]):
            eng.open_tab(os.path.join(path, t.file), tab=t.id)
        for tab, edit in edits:
            eng.submit_edit(tab, edit)
        out = {}
        for t in fd.tabs:
            o = eng.outputs(t.id)
            if t.kind == "assembly":
                out[t.id] = {i.id: i.part for i in o.instances}
            elif t.kind == "drawing":
                out[t.id] = geometry_hash(eng._tabs[t.id].doc.shape)
            elif t.kind == "variables":
                out[t.id] = dict(o.variables)
            else:
                out[t.id] = {p.id: p.hash for p in o.parts}
        eng.close()
        return out

    path, ids = build_document()
    say(f"document: V A C D E built ({len(ids)} tabs), Workspaces main and alt")

    # ---------------------------------------------------------------- the window
    app = QApplication.instance() or QApplication([])      # noqa: F841 -- must stay alive
    MW.MainWindow.autosave_root = staticmethod(lambda: os.path.join(tmp, "autosave"))
    MW.MainWindow.autosave_dir = classmethod(lambda cls: os.path.join(tmp, "autosave"))
    H.thumbs_dir = lambda: os.path.join(tmp, "thumbs")
    for name in ("warning", "critical", "information"):
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))
    w = MW.MainWindow(engine=True)
    win.append(w)
    w.settings = QSettings(os.path.join(tmp, "settings.ini"), QSettings.IniFormat)
    w.open_document(path)
    se = w.studio_engine
    check(se.settle(240), "the window's tabs did not finish their first build")
    check(not se.broken, f"the engine failed: {se.broken}")
    check(w.tabs.count() == 5, f"{w.tabs.count()} tabs open, want 5")

    def doc_of(key):
        return w.folder.tab(ids[key]).doc

    def shown(key):
        return se.live.displayed(doc_of(key))

    def current(key):
        w.tabs.setCurrentIndex(w._tab_index(doc_of(key)))
        check(se.settle(60), "settle")

    def snap_counts():
        """{tab id: (graph rebuilds, revision)} read in the engine."""
        return se.in_engine(doc_of("V"), lambda e, tab: {
            t[0]: (e.rebuild_count(t[0]), e.outputs(t[0]).revision) for t in e.tabs()})

    def delta(a, b):
        return {k: (b[k][0] - a[k][0]) + (b[k][1] - a[k][1]) for k in a}

    def worker_of(key, fn, *args):
        return se.in_engine(doc_of(key), lambda e, tab: e.run_in_worker(tab, fn, *args))

    def a_bbox_height():
        out = se.in_engine(doc_of("A"), lambda e, tab: e.outputs(tab))
        lo, hi = K.bbox(K.shape_from_brep(out.parts[0].brep))
        return float(hi[2] - lo[2])

    # ---------------------------------------------------------------- 1. the graph and the first build
    edges = set(se.live.link_edges(doc_of("C")))
    want = {(ids["A"], ids["V"]), (ids["C"], ids["A"]), (ids["D"], ids["C"])}
    check(want <= edges, f"link graph {sorted(edges)} lacks {sorted(want - edges)}")
    check(not any(ids["E"] in e for e in edges), "E is in the link graph")
    first = headless(path)
    for key in "ACDE":
        check(shown(key) == first[ids[key]], f"{key} shows something other than the headless build")
    say("tabs: V A C D E open on the process executor, link graph A->V C->A D->C, E linked to nothing")

    # ---------------------------------------------------------------- 2. a named face mate in C
    asm = doc_of("C")
    resolved = []
    real_resolve = ASM.resolve_element

    def spy_resolve(shape, ref, kind, named=None):
        res = real_resolve(shape, ref, kind, named)
        resolved.append({"name": ref.get("name"), "had_map": named is not None, "recovered": res[1]})
        return res

    ASM.resolve_element = spy_resolve
    i1, i2 = [i["id"] for i in asm.instances]

    def face_with_normal(iid, n):
        return next(i for i, f in enumerate(K.faces(asm.parts[iid].shape)) if normal_is(n)(K.face_info(f)))

    m = asm.make_mate("fastened")
    m["a"] = asm.connector(i1, "face", face_with_normal(i1, (1, 0, 0)))
    m["b"] = asm.connector(i2, "face", face_with_normal(i2, (-1, 0, 0)))
    asm.add_mate(m)
    check(not asm.errors, f"C errors after the mate: {asm.errors}")
    check(m["a"]["ref"].get("name") and m["b"]["ref"].get("name"), "the mate's references carry no element name")
    check(asm._named(i1) is not None and asm._named(i2) is not None, "C was not handed A's element map")
    resolved.clear()
    for side, iid in (("a", i1), ("b", i2)):               # the stored reference, resolved as a refresh does
        idx, recovered = ASM.resolve_element(asm.parts[iid].shape, m[side]["ref"], "face", asm._named(iid))
        check(idx == m[side]["index"] and not recovered, f"mate side {side} did not resolve by name")
    check(asm.recovered_connectors() == [], f"signature-matched connectors: {asm.recovered_connectors()}")
    say(f"named mate resolved by name (before edit): names {m['a']['ref']['name']!r}, {m['b']['ref']['name']!r}")

    # ---------------------------------------------------------------- 3. one unsaved edit to V
    current("C")                                           # the assembly is the current tab throughout
    index, cur = w.tabs.currentIndex(), w.doc
    check(cur is asm, "the assembly tab is not current")
    v_file = os.path.join(path, w.folder.tab(ids["V"]).file)
    shown_before = {k: shown(k) for k in "ACDE"}
    worker_of("A", _spy_opens)
    counts0 = snap_counts()
    resolved.clear()
    doc_of("V").set_row(0, expression="30 mm")             # the person types in the table: not saved
    doc_of("V").notify()
    check(se.settle(120), "the edit to V did not settle")
    counts1 = snap_counts()
    by_window = list(resolved)                             # (the headless build below resolves too: not these)
    check(doc_of("V").dirty, "V is not dirty after an edit")
    with open(v_file) as fh:
        on_disk = fh.read()
    check("20 mm" in on_disk and "30 mm" not in on_disk, "V's file changed: the edit was saved")
    check(abs(a_bbox_height() - 30.0) < 1e-6, f"A's depth is {a_bbox_height()}, want 30 (the unsaved w)")
    say("A used unsaved V: depth #w = 30 mm in A's worker, V's file on disk still says 20 mm")
    opens = worker_of("A", _opened)
    v_opens = [p for p in opens if os.path.realpath(v_file) == p]
    check(not v_opens, f"A's worker opened V's file: {v_opens}")
    say(f"A worker file opens of V: {len(v_opens)}")
    d = delta(counts0, counts1)
    got = {k: d[ids[k]] for k in "VACDE"}
    check(got == {"V": 1, "A": 1, "C": 1, "D": 1, "E": 0}, f"rebuild counter deltas {got}")
    say("rebuild-counter deltas V=%d A=%d C=%d D=%d E=%d (graph rebuilds + edits; E untouched)"
        % tuple(got[k] for k in "VACDE"))
    check(w.tabs.currentIndex() == index and w.doc is cur, "the current tab changed")
    expect = headless(path, [(ids["V"], {"op": "set_row", "index": 0, "expression": "30 mm"})], asm)
    check(shown("A") != shown_before["A"], "A shows the old hashes")
    check(shown("C") != shown_before["C"] and shown("D") != shown_before["D"], "C or D did not change")
    check(shown("E") == shown_before["E"], "E changed")
    for key in "ACDE":
        check(shown(key) == expect[ids[key]], f"{key} differs from the headless build of the same edit")
    say("C and D changed with no tab switch (assembly tab current): hashes match the headless build")
    check(not asm.errors, f"C errors: {asm.errors}")
    check(by_window and all(r["had_map"] and r["name"] and not r["recovered"] for r in by_window),
          f"the refresh after V's edit matched by signature: {by_window}")

    # ---------------------------------------------------------------- 4. a slow rebuild, and the window's timers
    worker_of("A", _slow_rebuild, SLOW_S)
    ticks = []
    timer = QTimer()
    timer.setInterval(TICK_MS)
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start()
    QCoreApplication.processEvents()
    t0 = time.monotonic()
    ticks.append(t0)
    doc_of("V").set_row(0, expression="25 mm")
    doc_of("V").notify()
    check(se.settle(120), "the slow rebuild did not settle")
    t1 = time.monotonic()
    ticks.append(t1)
    timer.stop()
    worker_of("A", _fast_rebuild)
    ticks = sorted(ticks)
    gap = max(b - a for a, b in zip(ticks, ticks[1:]))
    n = len(ticks) - 2
    check(t1 - t0 >= SLOW_S, f"the rebuild took {t1 - t0:.2f} s, not {SLOW_S} s: nothing was slowed")
    check(n >= int((t1 - t0) / 0.1), f"only {n} timer ticks in {t1 - t0:.2f} s")
    check(gap <= MAX_GAP_S, f"largest gap between timer ticks {gap * 1000:.0f} ms > {MAX_GAP_S * 1000:.0f} ms")
    check(abs(a_bbox_height() - 25.0) < 1e-6, "the slow rebuild did not use V's new value")
    say(f"responsive: {n} timer ticks over a {t1 - t0:.2f} s rebuild, largest gap {gap * 1000:.0f} ms")

    # ---------------------------------------------------------------- 5. an edit to A: the mate follows by name
    a_doc = doc_of("A")
    resolved.clear()
    set_extrude_depth(a_doc, "#w + 5")                     # a committed feature dialog; not saved
    check(se.settle(120), "the edit to A did not settle")
    by_window = list(resolved)
    check(abs(a_bbox_height() - 30.0) < 1e-6, f"A is {a_bbox_height()} high after its edit, want 30")
    check(not asm.errors, f"C errors after the edit to A: {asm.errors}")
    check(len(by_window) >= 2, "C did not re-resolve its mate after A changed")
    check(all(r["had_map"] and r["name"] and not r["recovered"] for r in by_window),
          f"the mate was matched by signature after A's edit: {by_window}")
    check(asm.recovered_connectors() == [], f"signature-matched connectors: {asm.recovered_connectors()}")
    expect = headless(path, [(ids["V"], {"op": "set_row", "index": 0, "expression": "25 mm"}),
                             (ids["A"], {"op": "replace_feature", "feature":
                                         next(f for f in a_doc.features if f["type"] == "extrude")})],
                    asm)
    for key in "ACD":
        check(shown(key) == expect[ids[key]], f"{key} differs from the headless build after A's edit")
    ASM.resolve_element = real_resolve
    say("named mate resolved by name: before edit ok, after edit ok")

    # ---------------------------------------------------------------- 6. SIGKILL to A's worker
    pre = se.in_engine(a_doc, lambda e, tab: {p.id: p.hash for p in e.outputs(tab).parts})
    check(a_doc.dirty, "A is not dirty: its edit was saved")
    saved_a = headless(path)[ids["A"]]
    check(pre != saved_a, "A's pre-kill state is the saved one: no unsaved edit to recover")
    pid = se.in_engine(a_doc, lambda e, tab: e.pid(tab))
    os.kill(pid, signal.SIGKILL)
    err = None
    end = time.monotonic() + 30
    while err is None and time.monotonic() < end:
        err = se.in_engine(a_doc, lambda e, tab: e.errored(tab))
        time.sleep(0.05)
    check(err, "A's worker was killed and A is not errored")
    others = se.in_engine(a_doc, lambda e, tab: {t[0]: e.errored(t[0]) for t in e.tabs() if t[0] != tab})
    check(not any(others.values()), f"another tab errored: {others}")
    say(f"A errored: worker {pid} killed ({err}); the other tabs answer")
    post = se.in_engine(a_doc, lambda e, tab: {p.id: p.hash for p in e.restart(tab).parts})
    new_pid = se.in_engine(a_doc, lambda e, tab: e.pid(tab))
    check(new_pid != pid, "restart did not start a new worker")
    check(se.in_engine(a_doc, lambda e, tab: e.errored(tab)) is None, "A is still errored after restart")
    check(post == pre, "A's output hash after restart differs from its pre-kill value")
    check(abs(a_bbox_height() - 30.0) < 1e-6, "the restarted A lost the unsaved depth")
    check(se.settle(60), "settle")
    say(f"A restarted: new worker {new_pid}, output hash equal to pre-kill (unsaved edit kept)")

    # ---------------------------------------------------------------- 7. Workspace switch
    w.folder.save()                                        # a Workspace switch asks about unsaved tabs: none left
    for key in "VAE":
        check(not doc_of(key).dirty, f"{key} is still dirty")
    for target in ("alt", "main"):
        check(w.switch_workspace(target), f"switching to {target} was refused")
        check(se.settle(240) and not se.broken, f"the window did not settle on {target}")
        expect = headless(path)
        check(w.folder.current_workspace() == target, "the Workspace did not change")
        check(w.tabs.count() == 5, f"{w.tabs.count()} tabs after the switch")
        for key, tid in ids.items():
            got = se.live.displayed(w.folder.tab(tid).doc)
            check(got == expect[tid], f"{key} on {target} differs from the headless build of {target}")
        edges = set(se.live.link_edges(w.folder.tab(ids["C"]).doc))
        check(want <= edges, f"link graph after the switch: {sorted(edges)}")
        if target == "alt":
            check(expect[ids["V"]] == {"w": 50.0}, f"alt's V is {expect[ids['V']]}")
            check(expect[ids["A"]] != first[ids["A"]] and expect[ids["E"]] != first[ids["E"]],
                  "alt is not different from main: the switch proves nothing")
    say("workspace switch: hashes match target branch")


if __name__ == "__main__":
    sys.exit(main())
