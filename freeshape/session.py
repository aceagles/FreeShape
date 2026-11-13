"""A headless Document session (I5.3): one open Document, its tabs live in an engine, with no Qt (ADR 0002, 0009, 0010).

    with DocumentSession(FolderStore(path)) as s:        # or DocumentSession("/path/to/Document")
        s.tabs()                                         # [{"id", "kind", "name", "state", "error", "dirty"}, ...]
        s.submit_edit("T2", {"op": "set_row", "index": 0, "expression": "30 mm"})   # -> ["T2", "T3", "T4"]
        s.outputs("T4")                                  # a contract.TabOutputs of that tab
        s.save()                                         # edits are in the engine until this writes them

The session reads the Document's manifest from the store, starts a `ProcessEngine` (one worker process per tab) and
opens every tab by data, under its manifest tab id, with the tab's blob lookup from the store (`store.blobs(id)`).
Links inside the Document name tab ids, so they resolve by id exactly as in the GUI (`LiveTabs`). No tab file is
read from disk by the engine; the store is the only reader and writer.

Open order. A tab is opened after the tabs it consumes: the Link graph records an edge when a tab is handed its
inputs, and a tab opened before its inputs would start with none. The order is a topological sort of the link
sources in each tab's data (a Variable Studio reference, an Assembly instance, a Drawing source) by tab id, then by
uid, ties and cycles in manifest order. A tab that cannot be opened (bad data, a worker that fails to load it)
does not stop the others: its state is "failed" and `error` says why.

State of a tab (`tabs()[i]["state"]`):
    "ok"       the tab's worker is running and its outputs report no error
    "error"    the worker is running and the tab's outputs carry errors (a feature, row or instance failed);
               `error` is the first message, `outputs(tab).errors` has them all
    "crashed"  the tab's worker process stopped; the next call restarts it once from its bytes and edit log
    "failed"   the tab did not open; it has no worker and no outputs

Rebuilds. `submit_edit` applies the edit and then calls the engine's `rebuild_dependents`, synchronously: when
`ProcessEngine.submit_edit` returns, every consumer whose consumed output changed has been rebuilt, each once,
upstream first. Nothing is polled, so there is no settle timeout to tune (a call that never returns is a stuck
worker, see `edit_timeout`). The result is the edited tab followed by `engine.rebuild_order()`.

Saving. `save()` writes each open tab's `tab_data` through `store.write_tab`, stamped with `_tab_id` as
`FolderDocument._tab_bytes` does (same JSON, indent 1), and the blobs the data names through `store.blobs(id)`.
It does not rewrite the manifest or touch a tab that failed to open. An edit that was not saved is not in the
store: a fresh session shows the old Document.

Threads. The session does no locking. Call it from one thread at a time: a caller with several threads (the
serve routes of I5.4) must hold one lock around every call. The engine underneath is single-threaded by contract.

Imports no Qt (the test checks sys.modules in a fresh interpreter).
"""
from __future__ import annotations

import json
import os
from concurrent.futures import TimeoutError as FutureTimeout

from . import doc_folder as DF
from . import docstore as DS
from . import kernel as K
from .engine import EngineError, LinkCycleError, ProcessEngine, TabErrored
from .engine import wire
from .engine.executor import read_data
from . import links
from . import tabkinds as TK

STATES = ("ok", "error", "crashed", "failed")


class SessionError(Exception):
    """A call on a session that cannot be done: an unknown tab, an edit the tab refuses, a tab that did not
    open. The message says which and why; the session stays usable."""


class DocumentSession:
    """See the module docstring. `store` is a DocumentStore or the path of a Document folder; `spares` and
    `meshes` go to the ProcessEngine; `edit_timeout` (seconds, None for none) bounds how long `submit_edit` waits
    for a worker."""

    def __init__(self, store, spares=1, meshes=True, edit_timeout=300.0):
        if isinstance(store, (str, os.PathLike)):
            store = DS.FolderStore(os.fspath(store))
        self.store = store
        self.edit_timeout = edit_timeout
        self.cycle = None                       # the last circular-link message an edit made, if any
        self._failed = {}                       # tab id -> why it did not open
        self._state = {}                        # tab id -> (state, first error message)
        self._edited = {}                       # tab id -> edits applied since the last save
        self._closed = False
        self._eng = None
        manifest = self._read_manifest()
        self.id = manifest.get("id")
        self.name = manifest.get("name", "")
        self._entries = [{"id": e["id"], "kind": e.get("kind"), "file": e.get("file"),
                          "name": e.get("name") or e.get("kind") or ""}
                         for e in manifest.get("tabs", [])]
        self._eng = ProcessEngine(meshes=meshes, spares=spares)
        try:
            self._eng.use_document(self.id)
            self._open_all()
        except BaseException:
            self.close()
            raise

    # ------------------------------------------------------------------ open
    def _read_manifest(self):
        """The parsed, shape-checked manifest (FolderDocument's own check: format, version, tab ids)."""
        try:
            data = DF.FolderDocument._read_manifest(self.store.read_manifest())
        except K.KernelError as e:
            raise SessionError(str(e)) from e
        if data.get("version", 1) > DF.FORMAT_VERSION:
            raise SessionError(f"the Document was saved with file version {data['version']}; this build reads "
                               f"up to {DF.FORMAT_VERSION}")
        return data

    def _open_all(self):
        raw = {}
        for e in self._entries:
            tid = e["id"]
            try:
                if e["kind"] not in TK.KINDS:
                    raise SessionError(f"unknown tab kind {e['kind']!r}")
                self.store.place_tab(tid, e["file"])         # the store checks the manifest's file name
                raw[tid] = bytes(self.store.read_tab(tid))
            except Exception as ex:              # noqa: BLE001 -- a tab that cannot be read is that tab's failure
                self._failed[tid] = f"{type(ex).__name__}: {ex}"
        for e in self._order(raw):
            tid = e["id"]
            if tid in self._failed:
                continue
            try:
                self._eng.open_tab_data(e["kind"], raw[tid], tid, e["name"], blobs=self.store.blobs(tid))
            except Exception as ex:              # noqa: BLE001
                self._failed[tid] = f"{type(ex).__name__}: {ex}"
                continue
            self._edited[tid] = 0
        for e in self._entries:
            self._refresh_state(e["id"])

    def _order(self, raw):
        """The manifest entries, each after the tabs it consumes (by tab id, then uid)."""
        by_id = {e["id"]: e for e in self._entries}
        uids, deps = {}, {}
        for e in self._entries:
            tid = e["id"]
            if tid not in raw:
                continue
            try:
                d = read_data(e["kind"], raw[tid])
            except Exception:                    # noqa: BLE001 -- the engine's open reports it
                continue
            if d.get("uid"):
                uids.setdefault(d["uid"], tid)
            deps[tid] = d
        edges = {}
        for tid, d in deps.items():
            ups = []
            try:
                srcs = links.tab_sources(by_id[tid]["kind"], d)
            except Exception:                    # noqa: BLE001
                srcs = []
            for s in srcs:
                if s.get("document") or s.get("version"):
                    continue
                up = s.get("tab") if s.get("tab") in by_id else uids.get(s.get("uid"))
                if up and up != tid and up not in ups:
                    ups.append(up)
            edges[tid] = ups
        out, done, active = [], set(), set()

        def visit(tid):
            if tid in done or tid in active:     # a cycle: keep manifest order for the rest of it
                return
            active.add(tid)
            for up in edges.get(tid, ()):
                visit(up)
            active.discard(tid)
            done.add(tid)
            out.append(by_id[tid])
        for e in self._entries:
            visit(e["id"])
        return out

    # ------------------------------------------------------------------ reads
    def tabs(self):
        """[{"id", "kind", "name", "state", "error", "dirty"}] in manifest order. `state` is one of STATES; `error`
        is None or a message; `dirty` is True while the tab holds edits `save` has not written."""
        out = []
        for e in self._entries:
            tid = e["id"]
            state, err = self._tab_state(tid)
            name = e["name"]
            if tid not in self._failed and self._eng is not None:
                try:
                    name = next(t[2] for t in self._eng.tabs() if t[0] == tid)
                except StopIteration:
                    pass
            out.append({"id": tid, "kind": e["kind"], "name": name, "state": state, "error": err,
                        "dirty": self._edited.get(tid, 0) > 0})
        return out

    def outputs(self, tab):
        """The tab's `contract.TabOutputs` (a StudioOutputs, AssemblyOutputs, VariablesOutputs or DrawingOutputs).
        Raises SessionError for an unknown tab, a tab that failed to open, or a worker that cannot be restarted."""
        self._need_open(tab)
        try:
            out = self._retry(tab, lambda: self._eng.outputs(tab))
        except EngineError as e:
            raise SessionError(f"tab {tab!r}: {e}") from e
        self._note(tab, out)
        return out

    def outputs_wire(self, tab):
        """`outputs(tab)` as engine wire bytes (freeshape.engine.wire), what a browser client decodes."""
        return wire.to_wire(self.outputs(tab))

    def errors(self):
        """{tab id: message} for every tab that is not "ok": a tab that failed to open or crashed, and the first
        output error of a tab that reports some. Plus "" for the Document itself: the last circular link, if any."""
        out = {}
        for e in self._entries:
            state, err = self._tab_state(e["id"])
            if state != "ok":
                out[e["id"]] = err or state
        if self.cycle:
            out[""] = self.cycle
        return out

    # ------------------------------------------------------------------ edits
    def submit_edit(self, tab, edit):
        """Apply `edit` (a contract edit dict) to `tab`; returns the ids of every tab that rebuilt: `tab` first,
        then each consumer the engine rebuilt, upstream first. Raises SessionError, naming the tab, for an unknown
        or unopened tab or an edit the tab refuses; the session and the tab carry on as before."""
        self._need_open(tab)
        if not isinstance(edit, dict):
            raise SessionError("an edit is a dict with an 'op' key")
        try:
            self._retry(tab, lambda: self._eng.submit(tab, edit).result(self.edit_timeout))
            try:
                self._eng.rebuild_dependents(tab)
            except LinkCycleError as e:          # as the GUI: the edit stands, the cycle is reported
                self.cycle = str(e)
            else:
                self.cycle = None
        except FutureTimeout as e:
            raise SessionError(f"tab {tab!r}: the edit did not finish in {self.edit_timeout} s") from e
        except EngineError as e:
            raise SessionError(f"tab {tab!r}: {e}") from e
        except (TypeError, ValueError, KeyError, IndexError, AttributeError) as e:     # a malformed edit
            raise SessionError(f"tab {tab!r}: the edit {edit.get('op')!r} is malformed "
                               f"({type(e).__name__}: {e})") from e
        self._edited[tab] = self._edited.get(tab, 0) + 1
        rebuilt = [tab] + [t for t in self._eng.rebuild_order() if t != tab]
        for t in rebuilt:
            self._refresh_state(t)
        return rebuilt

    def save(self):
        """Write every open tab through the store (with its blobs); returns the tab ids written. A tab that did
        not open is left as it is in the store."""
        self._need_alive()
        written = []
        for e in self._entries:
            tid = e["id"]
            if tid in self._failed:
                continue
            data = self._retry(tid, lambda tid=tid: self._eng.tab_data(tid, blobs=self.store.blobs(tid)))
            d = json.loads(bytes(data).decode("utf-8"))
            d["_tab_id"] = tid
            self.store.write_tab(tid, json.dumps(d, indent=1).encode("utf-8"))
            self._edited[tid] = 0
            written.append(tid)
        return written

    # ------------------------------------------------------------------ end
    def pids(self):
        """{tab id: worker process id} of every open tab (the spare worker is not listed)."""
        if self._eng is None:
            return {}
        out = {}
        for t in self._eng.tabs():
            try:
                out[t[0]] = self._eng.pid(t[0])
            except EngineError:
                pass
        return out

    def close(self):
        """End every worker, the warm spare included; returns when they are gone. Safe to call again."""
        if self._closed:
            return
        self._closed = True
        eng, self._eng = self._eng, None
        if eng is not None:
            try:
                eng.close()
            finally:
                from .engine import process
                process.SPARES.clear()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------ helpers
    def _need_alive(self):
        if self._closed or self._eng is None:
            raise SessionError("the session is closed")

    def _need_open(self, tab):
        self._need_alive()
        if tab in self._failed:
            raise SessionError(f"tab {tab!r} did not open: {self._failed[tab]}")
        if tab not in {e["id"] for e in self._entries}:
            raise SessionError(f"no tab {tab!r} in this Document")

    def _retry(self, tab, call):
        """`call()`, and once more on a fresh worker if the tab's worker died (the edit log replays onto its
        bytes, so an edit that did not finish is not in it)."""
        try:
            return call()
        except TabErrored:
            try:
                self._eng.restart(tab)
            except EngineError as e:
                self._state[tab] = ("crashed", str(e))
                raise SessionError(f"tab {tab!r}: its worker stopped and could not be restarted ({e})") from e
            return call()

    def _tab_state(self, tid):
        if tid in self._failed:
            return "failed", self._failed[tid]
        if self._eng is not None:
            try:
                why = self._eng.errored(tid)
            except EngineError:
                why = None
            if why:
                return "crashed", why
        return self._state.get(tid, ("ok", None))

    def _note(self, tid, out):
        errs = dict(out.errors or {})
        self._state[tid] = ("error", next(iter(errs.values()))) if errs else ("ok", None)

    def _refresh_state(self, tid):
        if tid in self._failed or self._eng is None:
            return
        try:
            self._note(tid, self._eng.outputs(tid))
        except TabErrored as e:
            self._state[tid] = ("crashed", str(e))
        except EngineError as e:
            self._state[tid] = ("error", str(e))
