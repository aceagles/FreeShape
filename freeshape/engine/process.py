"""The process executor: the engine contract with one worker process per tab (ADR 0002).

Each tab runs in its own `concurrent.futures.ProcessPoolExecutor(max_workers=1)` with the "spawn" start
method (the only one a packaged Windows / macOS build has); its first call (worker.load) loads the tab. The
standard pool supervises the child: if it dies, the pool breaks and every pending future raises
`BrokenProcessPool`, which marks the tab errored (`TabErrored`) -- the other tabs, in other processes, carry
on. `restart(tab)` builds a new pool and brings the tab back.

The parent holds the authoritative state of every tab: the bytes it was opened from (`open_tab_data`; `open_tab`
reads them from the file once, at open), the blobs those bytes name (read from the caller's blob lookup and sent
with the load), and every edit applied since, saved or not. A worker builds its tab from those bytes and never
reads the tab file. A restart replays the log onto the bytes -- not onto the file, which may be stale or
rewritten -- so a crash loses no edit, however long ago the last save was.

The parent also owns the Link graph. A tab that consumes others (an assembly, a drawing) gets, with each call,
the outputs of the tabs its links name -- parts as BREP bytes with their hashes and element-name maps, a
studio's variables, an assembly's placements -- matched to open tabs by the in-process rule (tab id, uid, then file
path, opening a linked file as a tab of its own if no tab has it). They are sent only when they changed.
Each hand-over is recorded as an edge in `links.graph` with a digest of what was handed; after `submit_edit`
the tabs that consume the edited one are rebuilt, each once, in topological order, if their digest changed
(`rebuild_dependents`). `submit` alone does not do that: call `rebuild_dependents` after it. The
worker never resolves a link from disk: it has no view of the other open tabs, so the file would miss their
unsaved edits.

Starting a worker costs a Python start plus `import OCP` -- seconds, against milliseconds for an edit. So a
spare worker is kept warm (`Spares`): a pool already started, its initializer run and OCP imported, holding
no tab. Opening or restarting a tab takes the spare and starts the next one in the background.

Threads: call the engine from one thread. `submit` returns a Future that completes on the pool's own thread;
that callback only updates the tab's own record.
"""
from __future__ import annotations

import copy
import itertools
import multiprocessing
import multiprocessing.connection
import os
import threading
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

from .. import blobstore
from .. import links
from .. import migrations as M
from .. import tabkinds as TK
from . import worker as W
from .contract import Engine, EngineError, TabErrored
from .encode import Encoder
from .executor import KINDS, fetch_blobs, file_kind, put_blobs, read_data
from .inputs import cross_package, external_id
from .linkgraph import key_configs, lean_package, link_key, package_digest, propagate

STOP_GRACE_S = 3.0           # how long a closed tab's worker gets to finish before it is killed


def _new_pool(ctx):
    """A single-worker pool whose process is started now (it imports the engine while nobody waits)."""
    pool = ProcessPoolExecutor(max_workers=1, mp_context=ctx, initializer=W.initialize, initargs=(os.getpid(),))
    pool.submit(W.probe)
    return pool


class Spares:
    """Warm workers holding no tab yet, shared by every ProcessEngine of this process. Each ends with this
    process (the pool's exit handler) or when it goes away (worker._watch_parent)."""

    def __init__(self):
        self._ready = []
        self._lock = threading.Lock()

    def take(self, ctx, keep=1):
        """A started pool for a new tab; then tops the spares back up to `keep`."""
        pool = None
        with self._lock:
            while self._ready and pool is None:
                cand = self._ready.pop(0)
                if getattr(cand, "_broken", False):
                    cand.shutdown(wait=False, cancel_futures=True)
                else:
                    pool = cand
        if pool is None:
            pool = _new_pool(ctx)
        self.fill(ctx, keep)
        return pool

    def fill(self, ctx, keep=1):
        with self._lock:
            while len(self._ready) < keep:
                self._ready.append(_new_pool(ctx))

    def clear(self):
        """End every spare (wait=True: their processes are gone when this returns)."""
        with self._lock:
            ready, self._ready = self._ready, []
        for pool in ready:
            pool.shutdown(wait=True, cancel_futures=True)


SPARES = Spares()


class _Tab:
    """The parent's record of one tab."""

    def __init__(self, kind, path, data, name):
        self.tab = None
        self.kind = kind
        self.path = path
        self.data = data         # the file's bytes at open (None for a new tab): what the edit log replays onto
        self.name = name
        self.uid = None
        self.edits = []          # every edit applied since `data`, saves included -- the unsaved state
        self.revision = 0
        self.sources = []        # every link source this tab has held, so its inputs go with each call
        self.pool = None
        self.pid = None
        self.error = None        # why the worker stopped, while the tab is errored
        self.epoch = 0           # bumps on every (re)start
        self.wants = {}          # upstream tab id -> link keys it reads, as of the last `_upstream`
        self.sent = None         # key of the upstream outputs the worker holds
        self.lean = {}           # configurations -> (state key, package): this tab's outputs as an input of other tabs
        self.open_name = None    # the name open_tab_data was given (None: the data's own)
        self.lookup = None       # the blob lookup open_tab_data was given
        self.blobs = {}          # {key: bytes} the worker holds: sent from `lookup`, or returned by it (`tab_data`)
        self.lock = threading.Lock()

    def take_meta(self, meta):
        with self.lock:
            self.name, self.path, self.uid = meta["name"], meta["path"], meta["uid"]
            self.revision, self.pid = meta["revision"], meta["pid"]
            for src in meta["sources"]:
                if src not in self.sources:
                    self.sources.append(src)


def _file_sources(kind, d, path):
    """The link sources an Assembly or Drawing file holds, read without loading it (so its inputs can go with the load)."""
    if kind == "variables":
        return []
    try:
        d = M.migrate(TK.get(kind).chain, copy.deepcopy(d), path)
    except Exception:            # noqa: BLE001 -- the worker's own load reports a bad file
        return []
    out = []
    for src in links.tab_sources(kind, d):
        src = dict(src)
        if path is not None and src.get("relpath") and not (src.get("path") and os.path.exists(src["path"])):
            # as the loaders do
            cand = os.path.normpath(os.path.join(os.path.dirname(path), src["relpath"]))
            if os.path.exists(cand):
                src["path"] = cand
        out.append(src)
    return out


def _wait_exit(proc, timeout):
    try:
        return bool(multiprocessing.connection.wait([proc.sentinel], timeout))
    except (ValueError, OSError):       # already joined and closed
        return True


class ProcessEngine(Engine):
    """`spares`: how many warm workers to keep ready for the next tab (0: start each worker on demand)."""

    def __init__(self, meshes=True, spares=1):
        self.meshes = meshes
        self.spares = spares
        self._ctx = multiprocessing.get_context("spawn")
        self.links = links.LinkResolver()      # the Link graph: edges only, nothing is resolved through it
        self.document_id = None                # the Document these tabs belong to (`use_document`)
        self.cross = None                      # resolves a source naming another Document, pinned to a Version
        self._pinned = {}                      # (Document, folder path, Version, tab, kind) -> (id, package, state)
        self._pin_enc = Encoder(meshes=False)
        self.by_tab_id = False                 # a tab was opened by data with no path: tab-id sources match by id only
        self._tabs: dict[str, _Tab] = {}
        self._ids = itertools.count(1)

    # ------------------------------------------------------------------ workers
    def _start(self, t, upstream, key):
        spec = {"tab": t.tab, "kind": t.kind, "path": t.path, "data": t.data, "name": t.name,
                "edits": copy.deepcopy(t.edits), "revision": t.revision, "meshes": self.meshes,
                "upstream": upstream, "upstream_key": key, "document_id": self.document_id,
                "open_name": t.open_name, "blobs": None if t.lookup is None else dict(t.blobs),
                "by_tab_id": self.by_tab_id}
        t.epoch += 1
        t.error, t.lean, t.sent = None, {}, key
        t.pool = SPARES.take(self._ctx, self.spares)
        t.take_meta(self._wait(t, t.pool.submit(W.load, spec)))

    def _stop(self, t):
        pool, t.pool = t.pool, None
        if pool is None:
            return
        procs = list((getattr(pool, "_processes", None) or {}).values())
        pool.shutdown(wait=False, cancel_futures=True)
        for p in procs:
            if not _wait_exit(p, STOP_GRACE_S):
                p.kill()
                _wait_exit(p, STOP_GRACE_S)

    def _died(self, t, exc):
        with t.lock:
            if t.error is None:
                t.error = f"its worker process (pid {t.pid}) stopped ({exc})"

    def _check_alive(self, t):
        """Mark the tab errored if its worker is gone, without sending it anything."""
        if t.error is None and t.pool is not None:
            procs = list((getattr(t.pool, "_processes", None) or {}).values())
            if getattr(t.pool, "_broken", False) or any(_wait_exit(p, 0) for p in procs):
                self._died(t, "the process exited")
        if t.error is not None:
            raise TabErrored(t.tab, t.error)

    def _wait(self, t, fut):
        try:
            return fut.result()
        except BrokenProcessPool as e:
            self._died(t, e)
            raise TabErrored(t.tab, t.error) from e
        except BaseException:
            t.sent = None           # whatever the worker holds now, resend the inputs with the next call
            raise

    def _submit(self, t, op, arg=None, seen=(), blobs=None):
        self._check_alive(t)
        upstream, key = self._upstream(t, seen)
        self._record_inputs(t, upstream)
        try:
            fut = t.pool.submit(W.call, op, arg, upstream if key != t.sent else None, key, blobs or None)
        except BrokenProcessPool as e:
            self._died(t, e)
            raise TabErrored(t.tab, t.error) from e
        t.sent = key                # calls run in order in the one worker, so the next call finds them there
        return fut

    def _call(self, t, op, arg=None, seen=()):
        return self._wait(t, self._submit(t, op, arg, seen))

    # ------------------------------------------------------------------ the Link graph
    def use_document(self, document_id, registry=None):
        """Say which Document the tabs belong to. A source naming another Document is then never matched to a
        tab, and is resolved through `registry` at its pinned Version (`_external`)."""
        if document_id != self.document_id or registry is not getattr(self.cross, "registry", None):
            self.document_id = document_id
            self.cross = links.LinkResolver(registry=registry) if registry is not None else None
            if self.cross is not None:
                self.cross.own_id = document_id
            self._pinned.clear()

    def _external(self, src):
        """(id, package, state) of the pinned view a cross-Document source names, or None when it does not
        resolve (the consumer's worker then reports the unresolved link). Cached per Document, folder path,
        Version and tab: a Version does not change."""
        kind = src.get("kind")
        if kind not in ("studio", "assembly", "variables") or self.cross is None:
            return None
        try:
            doc = src.get("document") or self.document_id      # no Document named: a Version of this one (I1.11)
            config = src.get("config") if kind == "studio" else None
            key = (doc, self.cross.registry.trusted_path(doc), src.get("version"), src.get("tab"), kind, config)
            hit = self._pinned.get(key)
            if hit is None:
                pkg = cross_package(kind, self.cross._cross_doc(dict(src, document=doc), kind), self._pin_enc,
                                    (config,) if config else ())
                hit = self._pinned[key] = (external_id(src), pkg, package_digest(kind, lean_package(pkg)))
            return hit
        except Exception:               # noqa: BLE001 -- an unreadable pin is the consumer's error, not ours
            return None

    def _match(self, src):
        """The open tab a link source names -- by uid, then file path -- opening the file as a tab of its own
        if no tab has it. The rule of InProcessEngine.provide; worker._WorkerEngine.provide applies it again
        to what is sent, so both sides agree. A source of another Document names no tab of this one."""
        kind = src.get("kind")
        if kind not in ("studio", "assembly", "variables") or links.is_pinned(src, self.document_id):
            return None
        uid, path = src.get("uid"), src.get("path")
        tabs = [t for t in self._tabs.values() if t.kind == kind]
        hit = self._tabs.get(src.get("tab")) if src.get("tab") else None
        if hit is not None and hit.kind != kind:
            hit = None
        if hit is None:
            hit = next((t for t in tabs if uid and t.uid == uid), None)
        if hit is None and path and not (src.get("tab") and self.by_tab_id):    # a tab id names it: never a path
            hit = self._by_path(path)
            if hit is None and os.path.exists(path):
                try:
                    if file_kind(path) == kind:
                        hit = self._tabs[self.open_tab(path)]
                except Exception:       # noqa: BLE001 -- an unreadable link is the consumer's error, not ours
                    hit = None
        return hit if hit is not None and hit.kind == kind else None

    def _upstream(self, t, seen=()):
        """({tab id: package}, key) of the outputs `t` consumes. The key changes whenever any of them does. A
        studio's package carries the configurations `t`'s sources name."""
        seen = seen + (t,)
        pkgs, key, wants, live = {}, [], {}, []
        for src in list(t.sources):
            if links.is_pinned(src, self.document_id):      # a pinned input: its package, and no live edge
                ext = self._external(src)
                if ext is not None:
                    wants.setdefault(ext[0], set()).add(link_key(src))
                    if ext[0] not in pkgs:
                        pkgs[ext[0]] = ext[1]
                        key.append((ext[0], ext[2]))
                continue
            u = self._match(src)
            if u is not None and u in seen and u.kind == "studio" and u.tab not in pkgs:
                # I1.8: a source that leads back to `t`. Its consumer gets an empty studio that is in error, so the
                # Derived that closes the loop turns red naming it; the edge keeps the cycle in the Link graph.
                loop = " -> ".join(self._label(x.tab) for x in (seen[seen.index(u):] + (u,)))
                msg = "Circular link: " + loop
                wants.setdefault(u.tab, set()).add(link_key(src))
                pkgs[u.tab] = {"kind": "studio", "uid": u.uid, "name": str(u.name), "path": u.path, "parts": [],
                               "error": msg}
                key.append((u.tab, ("cycle", msg)))
                continue
            if u is None or u in seen:
                continue
            wants.setdefault(u.tab, set()).add(link_key(src))
            if u not in live:
                live.append(u)
        for u in live:                  # once every source is seen: the configurations each tab is read in
            if u.tab in pkgs:
                continue
            lean = self._lean(u, seen, key_configs(wants[u.tab]))
            if lean is not None:
                pkgs[u.tab] = lean[1]
                key.append((u.tab, lean[0]))
        t.wants = {up: tuple(sorted(k)) for up, k in wants.items()}
        return pkgs, tuple(key)

    def _record_inputs(self, t, pkgs):
        """Record the edges into `t` for the outputs just handed to its worker. An upstream tab that was not
        handed (its worker is down) keeps the edge it had."""
        old = self.links.graph.inputs(t.tab)
        edges = {}
        for up, keys in t.wants.items():
            pkg = pkgs.get(up)
            if pkg is not None:
                edges[up] = links.Edge(pkg["kind"], keys, package_digest(pkg["kind"], lean_package(pkg), keys))
            elif up in old:
                edges[up] = old[up]
        self.links.record_inputs(t.tab, edges)

    def _digest_now(self, tab, edge):
        u = self._tabs.get(tab)
        lean = self._lean(u, (), key_configs(edge.keys)) if u is not None else None
        if lean is None:                          # closed or down: its consumers keep the version they have
            return edge.digest
        return package_digest(u.kind, lean_package(lean[1]), edge.keys)

    def _label(self, tab):
        t = self._tabs.get(tab)
        return f"{tab} “{t.name}”" if t is not None else str(tab)

    def rebuild_dependents(self, tab):
        self._get(tab)
        return propagate(self.links.graph, tab, self._digest_now,
                         lambda x: self._call(self._tabs[x], "rebuild"), self._label)

    def _lean(self, u, seen, configs=()):
        """(state key, package) of tab `u` as an input, carrying the configurations `configs` (a sorted tuple of
        names) besides the active one; cached per `configs` until its revision or its own inputs change.
        None while `u` is errored: its consumers then keep showing its last version, as when it is closed."""
        if u.error is not None:
            return None
        state = (u.epoch, u.revision, self._upstream(u, seen)[1], tuple(configs))
        hit = u.lean.get(state[3])
        if hit is None or hit[0] != state:
            u.lean = {c: v for c, v in u.lean.items() if v[0][:3] == state[:3]}     # drop what is out of date
            try:
                hit = u.lean[state[3]] = (state, self._call(u, "upstream", list(configs), seen=seen))
            except TabErrored:
                return None
        return hit

    # ------------------------------------------------------------------ tab registry
    def _get(self, tab) -> _Tab:
        t = self._tabs.get(tab)
        if t is None:
            raise EngineError(f"no tab {tab!r}")
        return t

    def _by_path(self, path):
        ap = os.path.abspath(path)
        return next((t for t in self._tabs.values() if t.path and os.path.abspath(t.path) == ap), None)

    def _register(self, t, tab):
        if tab is not None and tab in self._tabs:
            raise EngineError(f"tab id {tab!r} is already open")
        while tab is None or tab in self._tabs:
            tab = f"T{next(self._ids)}"
        t.tab = tab
        self._tabs[tab] = t
        try:
            upstream = self._upstream(t)
            self._start(t, *upstream)
            self._record_inputs(t, upstream[0])
        except BaseException:
            self._tabs.pop(tab, None)
            self._stop(t)
            raise
        return tab

    def open_tab(self, path, tab=None):
        open_already = self._by_path(path)
        if open_already is not None:
            return open_already.tab
        kind = file_kind(path)
        path = os.path.abspath(path)
        with open(path, "rb") as fh:
            data = fh.read()
        return self.open_tab_data(kind, data, tab, path=path)

    def open_tab_data(self, kind, data, tab=None, name=None, blobs=None, path=None):
        """The worker builds the tab from `data` (never from a file). The blobs the data names are read from
        `blobs` here and sent with the load, once; an edit that names blobs the worker was not sent carries them."""
        d = read_data(kind, data, path)
        if tab is not None and tab in self._tabs:
            raise EngineError(f"tab id {tab!r} is already open")
        if path is None:
            self.by_tab_id = True
        t = _Tab(kind, path, bytes(data), name or d.get("name"))
        t.uid = d.get("uid")
        t.open_name = name or None
        t.lookup = blobs
        t.blobs = fetch_blobs(d, blobs, {}) if blobs is not None else {}
        t.sources = _file_sources(kind, d, path)
        return self._register(t, tab)

    def tab_data(self, tab, blobs=None):
        """The worker returns the bytes, every blob key they name, and the bytes of the blobs this process does
        not hold yet (a Python feature's new stored build); those are kept here and all are put into the sink."""
        t = self._get(tab)
        data, keys, new = self._call(t, "data")
        t.blobs.update(new)
        sink = blobs if blobs is not None else t.lookup
        if sink is None and keys and t.path:
            sink = blobstore.store_for(t.path)
        missing = [k for k in keys if k not in t.blobs]
        if missing:
            raise EngineError(f"tab {tab!r}: the worker named blobs this process does not hold: {missing}")
        put_blobs(sink, {k: t.blobs[k] for k in keys}, tab)
        return data

    def new_tab(self, kind, name=None, tab=None, sources=(), blobs=None):
        """An empty tab; with `blobs`, an edit that names blobs carries them to the worker (as for a tab opened
        by data)."""
        if kind not in KINDS:
            raise EngineError(f"unknown tab kind {kind!r}")
        t = _Tab(kind, None, None, name)
        t.lookup = blobs
        t.sources = [copy.deepcopy(s) for s in sources if isinstance(s, dict)]
        return self._register(t, tab)

    def close_tab(self, tab):
        """Drop the tab and end its worker (killed if it has not finished within STOP_GRACE_S)."""
        t = self._get(tab)
        del self._tabs[tab]
        self.links.forget_tab(tab)
        self._stop(t)

    def tabs(self):
        return [(t.tab, t.kind, str(t.name), t.path) for t in self._tabs.values()]

    # ------------------------------------------------------------------ the contract
    def submit(self, tab, edit) -> Future:
        """Send one edit to the tab's worker; the Future gives the tab's new revision, or raises EngineError
        for an edit the tab refuses and TabErrored if the worker died first."""
        t = self._get(tab)
        src = links.source_edit(edit)
        if src is not None and src not in t.sources:
            t.sources.append(copy.deepcopy(src))                     # so its input goes with the edit
        logged = copy.deepcopy(edit)
        blobs = fetch_blobs(edit, t.lookup, t.blobs) if t.lookup is not None else {}
        t.blobs.update(blobs)                                        # a restart sends them with the load
        fut, out, epoch = self._submit(t, "edit", edit, blobs=blobs), Future(), t.epoch

        def done(f):
            try:
                meta = f.result()
            except BrokenProcessPool as e:
                self._died(t, e)
                out.set_exception(TabErrored(t.tab, t.error))
                return
            except BaseException as e:      # noqa: BLE001 -- handed to the caller
                t.sent = None
                out.set_exception(e)
                return
            if t.epoch == epoch:
                with t.lock:
                    t.edits.append(logged)
                t.take_meta(meta)
            out.set_result(meta["revision"])

        fut.add_done_callback(done)
        return out

    def submit_edit(self, tab, edit):
        revision = self.submit(tab, edit).result()
        self.rebuild_dependents(tab)
        return revision

    def outputs(self, tab):
        return self._call(self._get(tab), "outputs")

    # ------------------------------------------------------------------ crashes
    def errored(self, tab):
        """Why the tab's worker stopped, or None while it is running."""
        t = self._get(tab)
        try:
            self._check_alive(t)
        except TabErrored:
            pass
        return t.error

    def restart(self, tab):
        """A new worker for the tab, rebuilt from its file bytes and edit log; returns its outputs."""
        t = self._get(tab)
        self._stop(t)
        try:
            upstream = self._upstream(t)
            self._start(t, *upstream)
            self._record_inputs(t, upstream[0])
        except BrokenProcessPool as e:
            self._died(t, e)
            raise TabErrored(t.tab, t.error) from e
        return self.outputs(tab)

    def pid(self, tab):
        """The tab's worker process id."""
        return self._get(tab).pid

    def run_in_worker(self, tab, fn, *args):
        """fn(*args) in the tab's worker (fn importable by name there; worker.current() is the tab). For
        tests and diagnostics."""
        t = self._get(tab)
        self._check_alive(t)
        upstream, key = self._upstream(t)
        if key != t.sent:                  # fn reads the inputs as they are now, like any other call
            self._wait(t, t.pool.submit(W.call, "meta", None, upstream, key))
            t.sent = key
        return self._wait(t, t.pool.submit(fn, *args))
