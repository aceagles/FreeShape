"""The Part Studio tab as an engine client (I3.6).

A Part Studio tab keeps its `Document` as the editing model (feature list, undo, save, hidden parts). With
the engine on, the Document no longer rebuilds on the GUI thread: its `rebuild` is replaced by a request to
the process executor (freeshape.engine.ProcessEngine; one worker process per tab). The request is the
difference between the tab's features and the ones the worker already holds, sent as engine edits; the
worker rebuilds, and the parts it returns (BREP + display mesh + element names) become the Document's
`state` and the viewport's meshes. The GUI thread only unpacks the result.

Threads. The engine is called from one thread only (a single-thread pool owned by `StudioEngine`); results
reach the GUI thread through a Qt signal. Edits made while a rebuild runs are coalesced: only the newest
tab content is sent next, and a result that is already out of date is not drawn.

What still runs on the GUI thread, because the engine contract has no output for it yet (I3.7): the cheap
non-solid features (sketch, plane, variable -- the 2D solver and region faces) are re-run against the parts
the engine returned, so the sketcher and the plane list keep working; and feature dialogs still build their
previews with `Document.compute`. Opening a file also builds it once in `Document.load`.
"""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import os
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PySide6.QtCore import QCoreApplication, QObject, Signal

from .. import features as F
from .. import kernel as K
from .. import links
from .. import naming as N
from .. import tabkinds as TK
from ..engine import LinkCycleError, ProcessEngine, TabErrored
from ..engine import process as engine_process

_LETTER = {"face": "F", "edge": "E", "vertex": "V"}
_EMPTY = {"features": [], "rollback": None}
_LIGHT = ("variable", "sketch", "plane")        # feature types re-run on the GUI thread (see the module doc)


# =========================================================================== pure helpers

def snapshot(doc):
    """What the engine needs of a Part Studio: its features and rollback bar, copied."""
    return {"features": copy.deepcopy(doc.features), "rollback": doc.rollback, "name": doc.name}


def plan_edits(have, want):
    """The engine edits that turn a tab holding `have` into one holding `want` (both {"features": [...],
    "rollback": n | None}): delete what is gone, replace what changed, add what is new or moved, then put the
    rollback bar where `want` has it."""
    cur = list(have["features"])
    want_ids = {f["id"] for f in want["features"]}
    edits, structure = [], False
    gone = [f["id"] for f in cur if f["id"] not in want_ids]
    if gone:
        edits.append({"op": "delete_features", "ids": gone})
        cur = [f for f in cur if f["id"] in want_ids]
        structure = True
    for i, f in enumerate(want["features"]):
        if i < len(cur) and cur[i]["id"] == f["id"]:
            if cur[i] != f:
                edits.append({"op": "replace_feature", "feature": copy.deepcopy(f)})
                cur[i] = f
            continue
        if any(c["id"] == f["id"] for c in cur):                   # moved: out and back in at its new place
            edits.append({"op": "delete_features", "ids": [f["id"]]})
            cur = [c for c in cur if c["id"] != f["id"]]
        edits.append({"op": "add_feature", "feature": copy.deepcopy(f), "index": i})
        cur.insert(i, f)
        structure = True
    if want["rollback"] != have["rollback"] or (structure and have["rollback"] is not None):
        edits.append({"op": "set_rollback", "index": want["rollback"]})
    return edits


def kernel_mesh(m, nfaces):
    """A K.Mesh from the engine's MeshOut: its triangles are grouped by face, so each face's range is a count."""
    tf = np.asarray(m.tri_face, np.int32) - 1
    ranges = [tuple(r) for r in getattr(m, "face_ranges", None) or ()]      # the engine's own ranges
    if len(ranges) != nfaces:                                              # an older mesh: count them
        counts = np.bincount(tf, minlength=nfaces) if len(tf) else np.zeros(nfaces, np.int64)
        ends = np.cumsum(counts)
        ranges = list(zip((ends - counts).tolist(), ends.tolist()))
    return K.Mesh(np.asarray(m.verts, np.float32), np.asarray(m.normals, np.float32),
                  np.asarray(m.tris, np.uint32), tf, ranges, [np.asarray(p, np.float32) for p in m.edge_polys],
                  np.asarray(m.vertices, np.float32))


def element_name(state, pid, kind, index):
    """The element name of the `index`-th (1-based, MapShapes order) face / edge / vertex of part `pid`, from
    the element-name map the engine returned for it (None: no map, or that element has no name)."""
    return ((getattr(state, "engine_names", None) or {}).get(pid) or {}).get(kind, {}).get(index)


def element_index(state, pid, kind, name):
    """The 1-based index of the element called `name` in part `pid` (None: no such name)."""
    for i, n in ((getattr(state, "engine_names", None) or {}).get(pid) or {}).get(kind, {}).items():
        if n == name:
            return i
    return None


def mesh_hash(m):
    """A content hash of a displayed mesh (K.Mesh or MeshOut), to tell whether the picture changed."""
    import hashlib
    h = hashlib.sha256()
    for a in (m.verts, m.tris):
        h.update(np.ascontiguousarray(a).tobytes())
    return h.hexdigest()


# =========================================================================== per-tab record

class _Rec:
    def __init__(self, doc, kind="studio"):
        self.doc = doc
        self.kind = kind             # "studio" | "variables" | "assembly" | "drawing" (the last three: see LiveTabs)
        self.want_tab = None         # the engine tab id to ask for: the manifest tab id of a Document tab
        self.sig = None              # assembly / drawing: the links its engine copy was opened with
        self.tab = None              # engine tab id (engine thread only)
        self.have = _EMPTY           # what the engine tab holds (engine thread only)
        self.cache = {}              # part hash -> (shape, K.Mesh) (engine thread only)
        self.want = None             # newest snapshot, not yet sent
        self.dirty = False
        self.busy = False
        self.alive = True
        self.shown = {}              # part id -> engine hash of the part on screen
        self.meshes = {}             # part id -> hash of the mesh on screen
        self.revision = None


class _Result:
    def __init__(self, snap, out, parts):
        self.snap, self.errors, self.variables, self.revision = snap, dict(out.errors), dict(out.variables), out.revision
        self.parts = parts           # [(PartOut, shape, K.Mesh)]
        self.connectors = list(getattr(out, "connectors", []))     # [MateConnectorOut]
        self.rebuilt = []            # tab ids the engine rebuilt because this tab's output changed (Link graph)
        self.extra = []              # [(rec, _Result)] for the Part Studios among them


# =========================================================================== the client

class StudioEngine(QObject):
    """Owns one ProcessEngine (created on first use, on the engine thread) and the engine tabs of the window's
    Part Studio tabs."""

    _finished = Signal(object, object)

    def __init__(self, window, spares=1):
        super().__init__(window)
        self.window = window
        self.spares = spares
        self._engine = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="studio-engine")
        self._recs = {}
        self.broken = None           # why the engine could not be used, once it could not
        self._closed = False
        self._finished.connect(self._on_finished)
        self.live = LiveTabs(self)   # Variable Studio / Assembly / Drawing tabs and the Link graph (I3.8)

    # ------------------------------------------------------------------ tabs
    def attach(self, doc):
        """Make this Part Studio an engine client: its rebuilds go to the engine from now on. Any other tab kind
        goes to `LiveTabs`."""
        if getattr(doc, "kind", "studio") != "studio":
            return self.live.attach(doc)
        if self._closed or self.broken or id(doc) in self._recs or getattr(doc, "read_only_version", None):
            return                   # (a read-only version has its own links: its tabs rebuild in the window)
        self.live.attach_upstream(doc)
        rec = self._recs[id(doc)] = _Rec(doc)
        rec.want_tab = self.live.tab_id(doc)
        doc.rebuild = lambda rec=rec: self._rebuild(rec)
        self._request(rec)

    def attached(self, doc):
        return id(doc) in self._recs

    def prune(self):
        """Close the engine tab of every Part Studio the window no longer has."""
        open_docs = {id(d) for d in self.window.docs.values()}
        for key in [k for k, r in self._recs.items() if k not in open_docs]:
            self._drop(self._recs.pop(key))

    def _drop(self, rec):
        rec.alive = False
        rec.doc.__dict__.pop("rebuild", None)
        self._pool.submit(self._close_tab, rec)

    def _close_tab(self, rec):
        if self._engine is not None and rec.tab is not None:
            try:
                self._engine.close_tab(rec.tab)
            except Exception:       # noqa: BLE001 -- already gone
                pass
            rec.tab = None

    # ------------------------------------------------------------------ requests
    def _rebuild(self, rec):
        """Replaces Document.rebuild: ask the engine; the Document's lists redraw now, its parts when they arrive."""
        self._request(rec)
        rec.doc.notify()

    def _request(self, rec):
        rec.want, rec.dirty = (snapshot(rec.doc) if rec.kind == "studio" else self.live.snapshot(rec)), True
        self._pump(rec)

    def _pump(self, rec):
        if rec.busy or not rec.dirty or not rec.alive or self._closed:
            return
        rec.busy, rec.dirty = True, False
        fut = self._pool.submit(self._job, rec, rec.want)
        fut.add_done_callback(lambda f, rec=rec: self._finished.emit(rec, f))

    def idle(self):
        return not any(r.busy or r.dirty for r in self._recs.values())

    def settle(self, timeout=120.0):
        """Run the Qt event loop until every request has come back; False if `timeout` s pass first."""
        end = time.monotonic() + timeout
        while not self.idle():
            if time.monotonic() > end:
                return False
            QCoreApplication.processEvents()
            time.sleep(0.005)
        QCoreApplication.processEvents()
        return True

    # ------------------------------------------------------------------ the engine thread
    def _get_engine(self):
        if self._engine is None:
            self._engine = ProcessEngine(spares=self.spares)
        return self._engine

    def _job(self, rec, snap):
        eng = self._get_engine()
        if rec.kind != "studio":
            return self.live.job(eng, rec, snap)
        rebuilt = []
        for attempt in (0, 1):
            try:
                if rec.tab is None:
                    refs = links.tab_sources("studio", snap)
                    rec.tab, rec.have = self.live.open_new(eng, "studio", rec, str(snap["name"]), refs), _EMPTY
                for edit in plan_edits(rec.have, snap):
                    self.live.submit(eng, rec.tab, edit, rebuilt)
                rec.have = snap
                out = eng.outputs(rec.tab)
                break
            except TabErrored:
                if attempt:
                    raise
                try:                # the worker died: start the tab again from what the window holds
                    eng.close_tab(rec.tab)
                except Exception:   # noqa: BLE001
                    pass
                rec.tab = None
        res = self._decode(rec, snap, out)
        res.rebuilt, res.extra = rebuilt, self.live.collect(eng, rec, rebuilt)
        return res

    def _decode(self, rec, snap, out):
        """The parts of a StudioOutputs as shapes and meshes (a part that did not change is not decoded again)."""
        parts, cache = [], {}
        for p in out.parts:
            hit = rec.cache.get(p.hash)
            if hit is None:
                shape = K.shape_from_brep(p.brep)
                hit = (shape, kernel_mesh(p.mesh, len(K.faces(shape))) if p.mesh is not None else None)
            cache[p.hash] = hit
            parts.append((p, hit[0], hit[1]))
        rec.cache = cache
        return _Result(snap, out, parts)

    def in_engine(self, doc, fn):
        """fn(engine, tab id) on the engine thread; its result. For tests and diagnostics."""
        rec = self._recs[id(doc)]
        return self._pool.submit(lambda: fn(self._get_engine(), rec.tab)).result()

    # ------------------------------------------------------------------ back on the GUI thread
    def _on_finished(self, rec, fut):
        rec.busy = False
        try:
            res = fut.result()
        except BaseException as e:      # noqa: BLE001 -- the engine failed: the tab goes back to rebuilding here
            if rec.kind != "studio":    # only this tab's engine copy is lost: it refreshes on every change instead
                self.live.mirror_failed(rec, e)
                return
            self._failed(rec, e)
            return
        applied = []
        if rec.kind == "studio" and rec.alive and not rec.dirty:   # else a newer request is on its way: skip drawing
            applied.append(self.live.apply(rec, res))
        self.live.finished(rec, res, applied)
        self._pump(rec)
        self.prune()

    def _failed(self, rec, exc):
        self.broken = f"{type(exc).__name__}: {exc}"
        msg = f"The engine failed ({self.broken}); this Part Studio now rebuilds in the window"
        recs, self._recs = list(self._recs.values()), {}
        for r in recs:
            self._drop(r)
        self.window.status.showMessage(msg)
        for r in recs:
            if r.kind == "studio":
                r.doc.rebuild()
        self.live.refresh_consumers([], [], force=True)

    def _apply(self, rec, res):
        doc = rec.doc
        st = F.ModelState()
        for p, shape, mesh in res.parts:
            fc = {i - 1: tuple(c) for i, c in p.face_colors.items()} if p.face_colors else None
            st.parts[p.id] = F.Part(p.id, p.name, shape, tuple(p.color), fc)
            names = {(_LETTER[k], i): n for k, d in p.names.items() for i, n in d.items()}
            if names:
                st.set_named(p.id, N.NamedShape(shape, names))
        st.engine_names = {p.id: p.names for p, *_ in res.parts}      # the engine's name maps, for picking
        st.connectors = {c.id: dict({"name": c.name, "part": c.part or None, "frame": c.frame},
                                    **({"sketch": c.sketch} if getattr(c, "sketch", "") else {}))
                         for c in res.connectors}
        st.vars = dict(res.variables)
        n = len(res.snap["features"]) if res.snap["rollback"] is None else min(res.snap["rollback"],
                                                                                 len(res.snap["features"]))
        for f in res.snap["features"][:n]:
            if f["type"] in _LIGHT and not f.get("suppressed"):
                try:
                    F.RUNNERS[f["type"]](st, F.prepare(f, st))
                except Exception:       # noqa: BLE001 -- the engine's `errors` already say why it failed
                    pass
        doc.state = st
        doc.errors = dict(res.errors)
        doc._apply_overrides(st)
        rec.shown = {p.id: p.hash for p, *_ in res.parts}
        rec.meshes = {}
        for p, shape, mesh in res.parts:
            if mesh is not None:
                self.window.viewport.seed_mesh(shape, mesh)
                rec.meshes[p.id] = mesh_hash(mesh)
        rec.revision = res.revision
        doc.notify()


    # ------------------------------------------------------------------ what is displayed
    def displayed(self, doc):
        """{part id: hash of its mesh} as last drawn for this tab (empty before the first result)."""
        return dict(self._recs[id(doc)].meshes)

    def displayed_parts(self, doc):
        """{part id: engine hash (of the part's BREP)} as last drawn for this tab."""
        return dict(self._recs[id(doc)].shown)

    # ------------------------------------------------------------------ end
    def shutdown(self):
        """End every engine tab and worker (and the warm spare); returns when they are gone. Safe to repeat."""
        if self._closed:
            return
        self._closed = True
        for rec in self._recs.values():
            rec.alive = False
            rec.doc.__dict__.pop("rebuild", None)
        self._recs.clear()

        def close():
            if self._engine is not None:
                self._engine.close()
            engine_process.SPARES.clear()

        self._pool.submit(close).result()
        self._pool.shutdown(wait=True)
        self.live.cleanup()


# =========================================================================== I3.8: the other tab kinds, and live updates

def plan_rows(have, want):
    """The Variable Studio edits that turn a tab holding rows `have` into one holding `want`: drop surplus rows
    from the end, rewrite the rows that differ, add the missing ones."""
    cur, edits = list(have), []
    while len(cur) > len(want):
        edits.append({"op": "remove_row", "index": len(cur) - 1})
        cur.pop()
    for i, row in enumerate(want):
        if i >= len(cur):
            edits.append({"op": "add_row", "name": row.get("name", ""), "expression": row.get("expression", "")})
        elif cur[i] != row:
            e = {"op": "set_row", "index": i}
            for k in ("name", "expression"):
                if cur[i].get(k) != row.get(k):
                    e[k] = row.get(k, "")
            edits.append(e)
    return edits


def geometry_hash(shape):
    """A hash of what a shape looks like -- solid count, volume, bounding box, to 1e-4 -- for shapes that are
    built again on each side (a Drawing's model is a compound made where it is drawn), so that their BREP text
    cannot be compared."""
    lo, hi = K.bbox(shape)
    key = [len(K.solids(shape)), round(float(K.volume(shape)), 4)] + [round(float(x), 4) for x in (*lo, *hi)]
    return hashlib.sha256(repr(key).encode()).hexdigest()


class _Done:
    """The result of a job on a Variable Studio, Assembly or Drawing tab."""

    def __init__(self, rebuilt, extra):
        self.rebuilt, self.extra = rebuilt, extra

class LiveTabs:
    """What `StudioEngine` adds in I3.8, so that the window's tabs are one Link graph.

    Every tab of an open Document -- Part Studio, Variable Studio, Assembly, Drawing -- has an engine tab named
    by its manifest tab id, which is also what the links inside the Document name. A Variable Studio is edited
    with row edits; an Assembly or Drawing is opened in the engine from a copy of the window's document and
    opened again when its links change (the engine only needs them: the window keeps the placements and mates).
    An edit to any tab goes to the engine, which rebuilds the tabs that consume it (`rebuild_dependents`, once
    each, upstream first); the job returns their ids, and `finished` then draws the Part Studios among them and
    refreshes the Assemblies and Drawings -- no tab switch needed. An Assembly or Drawing that is not a tab of
    a Document (a loose file) has no engine copy: it refreshes whenever a Part Studio's parts change.

    The engine thread runs `job`, `open_new`, `submit` and `collect`; everything else runs on the GUI thread."""

    def __init__(self, engine):
        self.engine = engine
        self._tmp = None
        self._files = itertools.count(1)
        self._ids = itertools.count(1)
        self._attaching = set()
        self._hashes = {}                    # id(shape) -> (shape, sha256 of its BREP text)
        self.cycle = None                    # the last LinkCycleError message, if an edit made one

    # ------------------------------------------------------------------ ids and links (GUI thread)
    def tab_id(self, doc):
        """The manifest tab id of a document that is a tab of the open Document, else None."""
        tab = self.engine.window._folder_tab(doc)
        return tab.id if tab is not None else None

    @staticmethod
    def sources(doc):
        """The link sources a tab consumes."""
        return links.tab_sources(getattr(doc, "kind", None), doc)

    def source_tabs(self, doc):
        out = []
        for s in self.sources(doc):
            if isinstance(s, dict) and s.get("tab") and not s.get("document") and not s.get("version") \
                    and s["tab"] not in out:
                out.append(s["tab"])
        return out

    def attach_upstream(self, doc):
        """Make the tabs `doc` consumes engine tabs first, so that they are open in the engine before it is."""
        w = self.engine.window
        if w.folder is None or id(doc) in self._attaching:
            return
        self._attaching.add(id(doc))
        try:
            for tid in self.source_tabs(doc):
                tab = w.folder.tab(tid)
                if tab is not None and tab.doc is not None and tab.doc is not doc:
                    self.engine.attach(tab.doc)
        finally:
            self._attaching.discard(id(doc))

    def attach(self, doc):
        """A Variable Studio, Assembly or Drawing tab of the open Document joins the engine."""
        eng = self.engine
        tid = self.tab_id(doc)
        if eng._closed or eng.broken or id(doc) in eng._recs or tid is None or getattr(doc, "read_only_version", None):
            return
        self.attach_upstream(doc)
        rec = eng._recs[id(doc)] = _Rec(doc, doc.kind)
        rec.want_tab = tid
        doc.listeners.append(lambda d=doc: self.changed(d))
        eng._request(rec)

    def changed(self, doc):
        """The window's document changed: send the engine what it needs of it."""
        eng = self.engine
        rec = eng._recs.get(id(doc))
        if rec is None or not rec.alive or eng._closed:
            return
        if rec.kind != "variables" and self.signature(rec) == (rec.want or {}).get("sig"):
            return                           # the links did not change: the engine copy is as good as it was
        eng._request(rec)

    def snapshot(self, rec):
        doc = rec.doc
        if rec.kind == "variables":
            return {"rows": [dict(r) for r in doc.rows], "name": doc.name}
        sig = self.signature(rec)
        snap = {"sig": sig, "name": doc.name}
        if sig != rec.sig or rec.tab is None:
            snap["json"] = doc.to_json(embed=False)
        return snap

    @staticmethod
    def signature(rec):
        doc = rec.doc
        if rec.kind == "assembly":
            return json.dumps([[i["id"], i["source"]] for i in doc.instances], sort_keys=True, default=str)
        return json.dumps(doc.source, sort_keys=True, default=str)

    def reset(self, old_docs):
        """The files under every tab changed at once (Workspace switch, merge): close the engine tabs of the old
        documents now, so that the new ones open under the same ids on a clean Link graph."""
        eng = self.engine
        for d in old_docs:
            rec = eng._recs.pop(id(d), None)
            if rec is not None:
                eng._drop(rec)

    # ------------------------------------------------------------------ the engine thread
    def bind_document(self, eng):
        """Tell the engine which Document its tabs belong to, so a source naming another one is a pinned input."""
        folder = self.engine.window.folder
        eng.use_document(getattr(folder, "id", None), getattr(folder, "registry", None))

    def open_new(self, eng, kind, rec, name, sources=()):
        self.bind_document(eng)
        tid = rec.want_tab or f"L{next(self._ids)}"
        if any(t[0] == tid for t in eng.tabs()):          # a tab of a document that was dropped meanwhile
            for other in list(self.engine._recs.values()):
                if other is not rec and other.tab == tid:
                    other.tab = None
            try:
                eng.close_tab(tid)
            except Exception:                              # noqa: BLE001
                pass
        return eng.new_tab(kind, name=name, tab=tid, sources=sources)

    def submit(self, eng, tab, edit, rebuilt):
        """eng.submit_edit, noting the tabs it rebuilt in `rebuilt`. A circular link is not fatal: the edit is
        applied and the cycle is reported."""
        try:
            eng.submit_edit(tab, edit)
        except LinkCycleError as e:
            self.cycle = str(e)
        for t in eng.rebuild_order():
            if t not in rebuilt:
                rebuilt.append(t)

    def job(self, eng, rec, snap):
        rebuilt = []
        for attempt in (0, 1):
            try:
                if rec.kind == "variables":
                    if rec.tab is None:
                        rec.tab, rec.have = self.open_new(eng, "variables", rec, str(snap["name"])), []
                    for edit in plan_rows(rec.have, snap["rows"]):
                        self.submit(eng, rec.tab, edit, rebuilt)
                    rec.have = snap["rows"]
                elif rec.tab is None or "json" in snap:
                    self._reopen(eng, rec, snap)
                break
            except TabErrored:
                if attempt:
                    raise
                try:
                    eng.close_tab(rec.tab)
                except Exception:                           # noqa: BLE001
                    pass
                rec.tab, rec.sig = None, None
                if rec.kind != "variables":
                    snap = dict(snap, json=rec.doc.to_json(embed=False))
        return _Done(rebuilt, self.collect(eng, rec, rebuilt))

    def _reopen(self, eng, rec, snap):
        """Open (again) the engine copy of an Assembly or Drawing from the window's document."""
        if rec.tab is not None:
            try:
                eng.close_tab(rec.tab)
            except Exception:                               # noqa: BLE001
                pass
            rec.tab = None
        if self._tmp is None:
            self._tmp = tempfile.mkdtemp(prefix="freeshape-live-")
        ext = TK.get(rec.kind).ext
        path = os.path.join(self._tmp, f"{rec.want_tab}-{next(self._files)}{ext}")
        with open(path, "w") as fh:
            fh.write(snap["json"])
        if any(t[0] == rec.want_tab for t in eng.tabs()):
            eng.close_tab(rec.want_tab)
        self.bind_document(eng)
        rec.tab = eng.open_tab(path, tab=rec.want_tab)
        rec.sig = snap["sig"]

    def collect(self, eng, rec, rebuilt):
        """[(rec, _Result)] for the Part Studios of the window that the engine rebuilt: their parts changed with
        the Variable Studio they read, without any edit of their own."""
        out = []
        for other in list(self.engine._recs.values()):
            if other is not rec and other.kind == "studio" and other.tab in rebuilt and other.tab is not None:
                out.append((other, self.engine._decode(other, other.have, eng.outputs(other.tab))))
        return out

    # ------------------------------------------------------------------ back on the GUI thread
    def apply(self, rec, res):
        """Draw a Part Studio's result; (tab id, first result?, did its parts change?)."""
        first, old = rec.revision is None, dict(rec.shown)
        if len(self._hashes) > 2048:
            self._hashes.clear()
        for p, shape, _mesh in res.parts:                   # the engine's own hash of each shape it handed over
            self._hashes[id(shape)] = (shape, p.hash)
        self.engine._apply(rec, res)
        return (rec.want_tab or rec.tab, first, rec.shown != old)

    def finished(self, rec, res, applied):
        eng = self.engine
        for other, res2 in getattr(res, "extra", ()):
            if other.alive and not other.dirty and id(other.doc) in eng._recs:
                applied.append(self.apply(other, res2))
        self.refresh_consumers(getattr(res, "rebuilt", ()), applied)

    def mirror_failed(self, rec, exc):
        eng = self.engine
        if eng._recs.get(id(rec.doc)) is rec:
            eng._recs.pop(id(rec.doc))
        eng._drop(rec)
        eng.window.status.showMessage(f"The engine could not follow “{rec.doc.name}” ({type(exc).__name__}: {exc}); "
                                      "it now refreshes whenever a Part Studio changes", 10000)
        self.refresh_consumers([], [], force=True)

    def refresh_consumers(self, rebuilt, applied, force=False):
        """Refresh the window's Assemblies and Drawings that the engine says are out of date: the tabs it
        rebuilt, and the tabs that consume a Part Studio that has just drawn its first result. A document that has
        no engine copy refreshes whenever a Part Studio's parts changed."""
        eng, w = self.engine, self.engine.window
        rebuilt = set(rebuilt)
        fresh = {tid for tid, first, _ in applied if first and tid}
        any_changed = force or any(changed or first for _, first, changed in applied)
        if not (rebuilt or fresh or any_changed):
            return
        docs = [d for d in list(w.docs.values()) if getattr(d, "kind", "") in ("assembly", "drawing")
                and not getattr(d, "read_only_version", None)]
        docs.sort(key=lambda d: d.kind != "assembly")       # a drawing of an assembly after the assembly
        for d in docs:
            tab = w._folder_tab(d)
            if tab is not None and id(d) in eng._recs:
                due = tab.id in rebuilt or any(t in fresh for t in self.source_tabs(d))
                if due and any(t in fresh for t in self.source_tabs(d)):
                    fresh.add(tab.id)
            else:
                due = any_changed
            if due:
                try:
                    d.refresh(notify=True)
                except Exception as e:                      # noqa: BLE001 -- the document shows its own error
                    w.status.showMessage(f"Could not refresh “{d.name}”: {e}", 8000)

    # ------------------------------------------------------------------ what is displayed
    def hash_of(self, shape):
        hit = self._hashes.get(id(shape))
        if hit is None or hit[0] is not shape:
            from ..engine.encode import brep_text
            hit = self._hashes[id(shape)] = (shape, hashlib.sha256(brep_text(shape)).hexdigest())
        return hit[1]

    # ------------------------------------------------------------------ what is displayed
    def displayed(self, doc, geometry=False):
        """What the window shows of a tab, as hashes of the shapes it holds: {part id: hash} for a Part Studio,
        {instance id: hash of its part} for an Assembly, `geometry_hash` of the model drawn for a Drawing, {name: value}
        for a Variable Studio. A shape the engine handed over has the engine's hash of it; any other (a read-only
        version's tabs rebuild in the window) is hashed here, which gives another value for the same shape unless
        `geometry` asks for `geometry_hash` throughout."""
        kind = getattr(doc, "kind", "studio")
        hash_of = geometry_hash if geometry else self.hash_of
        if kind == "assembly":
            return {i["id"]: hash_of(doc.parts[i["id"]].shape) for i in doc.instances if i["id"] in doc.parts}
        if kind == "drawing":
            return None if doc.shape is None else geometry_hash(doc.shape)
        if kind == "variables":
            return dict(doc.values()[0])
        return {p.id: hash_of(p.shape) for p in doc.state.parts.values() if not getattr(p, "reference", False)}

    def link_edges(self, doc):
        """[(consumer tab id, upstream tab id)] of the engine's Link graph, read on the engine thread."""
        return self.engine.in_engine(doc, lambda e, tab: e.link_edges())

    def cleanup(self):
        if self._tmp is not None:
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None
