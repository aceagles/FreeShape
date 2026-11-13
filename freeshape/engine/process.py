"""The process executor: the engine contract with one worker process per tab (ADR 0002).

Each tab runs in its own `concurrent.futures.ProcessPoolExecutor(max_workers=1)` with the "spawn" start
method (the only one a packaged Windows / macOS build has); its first call (worker.load) loads the tab. The
standard pool supervises the child: if it dies, the pool breaks and every pending future raises
`BrokenProcessPool`, which marks the tab errored (`TabErrored`) -- the other tabs, in other processes, carry
on. `restart(tab)` builds a new pool and brings the tab back.

The parent holds the authoritative state of every tab: the bytes of the file it was opened from (read once,
at open) and every edit applied since, saved or not. A restart replays that log onto those bytes -- not onto
the file, which may be stale or rewritten -- so a crash loses no edit, however long ago the last save was.

The parent also owns the Link graph. A tab that consumes others (an assembly, a drawing) gets, with each call,
the outputs of the tabs its links name -- parts as BREP bytes with their hashes and element-name maps, a
studio's variables, an assembly's placements -- matched to open tabs by the in-process rule (uid, then file
path, opening a linked file as a tab of its own if no tab has it). They are sent only when they changed. The
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
import json
import multiprocessing
import multiprocessing.connection
import os
import threading
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

from .. import migrations as M
from . import worker as W
from .contract import Engine, EngineError, TabErrored
from .executor import KINDS, file_kind

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
        self.sent = None         # key of the upstream outputs the worker holds
        self.lean = None         # (state key, package): this tab's outputs as an input of other tabs
        self.lock = threading.Lock()

    def take_meta(self, meta):
        with self.lock:
            self.name, self.path, self.uid = meta["name"], meta["path"], meta["uid"]
            self.revision, self.pid = meta["revision"], meta["pid"]
            for src in meta["sources"]:
                if src not in self.sources:
                    self.sources.append(src)


def _file_sources(kind, d, path):
    """The link sources a .fasm / .fdwg holds, read without loading it (so its inputs can go with the load)."""
    if kind == "variables":
        return []
    try:
        d = M.migrate(kind, copy.deepcopy(d), path)
    except Exception:            # noqa: BLE001 -- the worker's own load reports a bad file
        return []
    if kind == "studio":
        srcs = [f.get("params", {}).get("source") for f in d.get("features", [])
                if f.get("type") == "variable_studio_ref"]
    else:
        srcs = [i.get("source") for i in d.get("instances", [])] if kind == "assembly" else [d.get("source")]
    out = []
    for src in srcs:
        if not isinstance(src, dict):
            continue
        src = dict(src)
        if src.get("relpath") and not (src.get("path") and os.path.exists(src["path"])):    # as the loaders do
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
        self._tabs: dict[str, _Tab] = {}
        self._ids = itertools.count(1)

    # ------------------------------------------------------------------ workers
    def _start(self, t, upstream, key):
        spec = {"tab": t.tab, "kind": t.kind, "path": t.path, "data": t.data, "name": t.name,
                "edits": copy.deepcopy(t.edits), "revision": t.revision, "meshes": self.meshes,
                "upstream": upstream, "upstream_key": key}
        t.epoch += 1
        t.error, t.lean, t.sent = None, None, key
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

    def _submit(self, t, op, arg=None, seen=()):
        self._check_alive(t)
        upstream, key = self._upstream(t, seen)
        try:
            fut = t.pool.submit(W.call, op, arg, upstream if key != t.sent else None, key)
        except BrokenProcessPool as e:
            self._died(t, e)
            raise TabErrored(t.tab, t.error) from e
        t.sent = key                # calls run in order in the one worker, so the next call finds them there
        return fut

    def _call(self, t, op, arg=None, seen=()):
        return self._wait(t, self._submit(t, op, arg, seen))

    # ------------------------------------------------------------------ the Link graph
    def _match(self, src):
        """The open tab a link source names -- by uid, then file path -- opening the file as a tab of its own
        if no tab has it. The rule of InProcessEngine.provide; worker._WorkerEngine.provide applies it again
        to what is sent, so both sides agree."""
        kind = src.get("kind")
        if kind not in ("studio", "assembly", "variables"):
            return None
        uid, path = src.get("uid"), src.get("path")
        tabs = [t for t in self._tabs.values() if t.kind == kind]
        hit = self._tabs.get(src.get("tab")) if kind == "variables" and src.get("tab") else None
        if hit is None:
            hit = next((t for t in tabs if uid and t.uid == uid), None)
        if hit is None and path:
            hit = self._by_path(path)
            if hit is None and os.path.exists(path):
                try:
                    if file_kind(path) == kind:
                        hit = self._tabs[self.open_tab(path)]
                except Exception:       # noqa: BLE001 -- an unreadable link is the consumer's error, not ours
                    hit = None
        return hit if hit is not None and hit.kind == kind else None

    def _upstream(self, t, seen=()):
        """({tab id: package}, key) of the outputs `t` consumes. The key changes whenever any of them does."""
        seen = seen + (t,)
        pkgs, key = {}, []
        for src in list(t.sources):
            u = self._match(src)
            if u is None or u in seen or u.tab in pkgs:
                continue
            lean = self._lean(u, seen)
            if lean is not None:
                pkgs[u.tab] = lean[1]
                key.append((u.tab, lean[0]))
        return pkgs, tuple(key)

    def _lean(self, u, seen):
        """(state key, package) of tab `u` as an input, cached until its revision or its own inputs change.
        None while `u` is errored: its consumers then keep showing its last version, as when it is closed."""
        if u.error is not None:
            return None
        state = (u.epoch, u.revision, self._upstream(u, seen)[1])
        if u.lean is None or u.lean[0] != state:
            try:
                u.lean = (state, self._call(u, "upstream", seen=seen))
            except TabErrored:
                return None
        return u.lean

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
            self._start(t, *self._upstream(t))
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
        d = json.loads(data)
        t = _Tab(kind, path, data, d.get("name"))
        t.uid = d.get("uid")
        t.sources = _file_sources(kind, d, path)
        return self._register(t, tab)

    def new_tab(self, kind, name=None, tab=None):
        if kind not in KINDS:
            raise EngineError(f"unknown tab kind {kind!r}")
        return self._register(_Tab(kind, None, None, name), tab)

    def close_tab(self, tab):
        """Drop the tab and end its worker (killed if it has not finished within STOP_GRACE_S)."""
        t = self._get(tab)
        del self._tabs[tab]
        self._stop(t)

    def tabs(self):
        return [(t.tab, t.kind, str(t.name), t.path) for t in self._tabs.values()]

    # ------------------------------------------------------------------ the contract
    def submit(self, tab, edit) -> Future:
        """Send one edit to the tab's worker; the Future gives the tab's new revision, or raises EngineError
        for an edit the tab refuses and TabErrored if the worker died first."""
        t = self._get(tab)
        if isinstance(edit, dict) and edit.get("op") == "set_source" and isinstance(edit.get("source"), dict):
            if edit["source"] not in t.sources:
                t.sources.append(copy.deepcopy(edit["source"]))      # so its input goes with the edit
        if isinstance(edit, dict) and edit.get("op") in ("add_feature", "replace_feature"):
            f = edit.get("feature")
            src = f.get("params", {}).get("source") if isinstance(f, dict) and f.get("type") == "variable_studio_ref" else None
            if isinstance(src, dict) and src not in t.sources:
                t.sources.append(copy.deepcopy(src))                 # so its input goes with the edit
        logged = copy.deepcopy(edit)
        fut, out, epoch = self._submit(t, "edit", edit), Future(), t.epoch

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
        return self.submit(tab, edit).result()

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
            self._start(t, *self._upstream(t))
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
        return self._wait(t, t.pool.submit(fn, *args))
