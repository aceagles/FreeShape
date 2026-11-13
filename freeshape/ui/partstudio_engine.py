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
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PySide6.QtCore import QCoreApplication, QObject, Signal

from .. import features as F
from .. import kernel as K
from .. import naming as N
from ..engine import ProcessEngine, TabErrored
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
    counts = np.bincount(tf, minlength=nfaces) if len(tf) else np.zeros(nfaces, np.int64)
    ends = np.cumsum(counts)
    ranges = list(zip((ends - counts).tolist(), ends.tolist()))
    return K.Mesh(np.asarray(m.verts, np.float32), np.asarray(m.normals, np.float32),
                  np.asarray(m.tris, np.uint32), tf, ranges, [np.asarray(p, np.float32) for p in m.edge_polys],
                  np.asarray(m.vertices, np.float32))


def mesh_hash(m):
    """A content hash of a displayed mesh (K.Mesh or MeshOut), to tell whether the picture changed."""
    import hashlib
    h = hashlib.sha256()
    for a in (m.verts, m.tris):
        h.update(np.ascontiguousarray(a).tobytes())
    return h.hexdigest()


# =========================================================================== per-tab record

class _Rec:
    def __init__(self, doc):
        self.doc = doc
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

    # ------------------------------------------------------------------ tabs
    def attach(self, doc):
        """Make this Part Studio an engine client: its rebuilds go to the engine from now on."""
        if self._closed or self.broken or id(doc) in self._recs:
            return
        rec = self._recs[id(doc)] = _Rec(doc)
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
        rec.want, rec.dirty = snapshot(rec.doc), True
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
        for attempt in (0, 1):
            try:
                if rec.tab is None:
                    rec.tab, rec.have = eng.new_tab("studio", name=str(snap["name"])), _EMPTY
                for edit in plan_edits(rec.have, snap):
                    eng.submit_edit(rec.tab, edit)
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
            self._failed(rec, e)
            return
        if rec.alive and not rec.dirty:     # a newer request is already on its way: skip drawing this one
            self._apply(rec, res)
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
            r.doc.rebuild()

    def _apply(self, rec, res):
        doc = rec.doc
        st = F.ModelState()
        for p, shape, mesh in res.parts:
            fc = {i - 1: tuple(c) for i, c in p.face_colors.items()} if p.face_colors else None
            st.parts[p.id] = F.Part(p.id, p.name, shape, tuple(p.color), fc)
            names = {(_LETTER[k], i): n for k, d in p.names.items() for i, n in d.items()}
            if names:
                st.set_named(p.id, N.NamedShape(shape, names))
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
