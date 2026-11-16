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
It does not touch a tab that failed to open. An edit that was not saved is not in the store: a fresh session
shows the old Document.

Tabs (I2.4). `add_tab`, `rename_tab` and `delete_tab` change the tab list. The manifest is edited by a
manifest-only `FolderDocument` (`_fd`, tabs with no loaded document) so the ids, file names and manifest bytes
are the desktop's own. Nothing reaches the store until `save()`: it writes the new tabs' files, removes the files
of deleted tabs, then rewrites the manifest (only when the tab list changed, so a save with no tab change leaves
the manifest bytes alone). A deleted tab's worker ends at once; an added tab that is never saved leaves nothing
on disk. `tabs_unsaved()` says whether the list has changes `save()` has not written.

Threads. The session does no locking. Call it from one thread at a time: a caller with several threads (the
serve routes of I5.4) must hold one lock around every call. The engine underneath is single-threaded by contract.

Imports no Qt (the test checks sys.modules in a fresh interpreter).
"""
from __future__ import annotations

import json
import os
import re
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
NEW_KINDS = ("studio", "assembly")      # what `add_tab` makes
MAX_TAB_NAME = 100                      # longest tab name `check_tab_name` accepts


def check_tab_name(name):
    """The tab name `name` as stored (stripped), or SessionError. Text of 1..MAX_TAB_NAME characters with no
    control character. It is a label, never a file name: see `tab_file_stem`."""
    if not isinstance(name, str):
        raise SessionError("the tab name must be text")
    text = name.strip()
    if not text:
        raise SessionError("the tab name is empty")
    if len(text) > MAX_TAB_NAME:
        raise SessionError(f"the tab name is longer than {MAX_TAB_NAME} characters")
    if any(not c.isprintable() for c in text):
        raise SessionError("the tab name may not contain a control character")
    return text


def tab_file_stem(name, fallback):
    """The file name stem for a tab called `name`: only letters, digits, space, `-`, `_` and `.` survive (the rest
    becomes `-`), runs collapse, leading and trailing `.`, `-` and space go, at most 60 characters; `fallback` when
    nothing is left. Never holds a separator and never starts with a dot."""
    out = "".join(c if (c.isalnum() or c in " _.-") else "-" for c in name)
    stem = re.sub(r"-{2,}", "-", re.sub(r" {2,}", " ", out)).strip(".- ")[:60].strip(".- ")
    return stem or fallback


class SessionError(Exception):
    """A call on a session that cannot be done: an unknown tab, an edit the tab refuses, a tab that did not
    open. The message says which and why; the session stays usable."""


class TabLinked(SessionError):
    """`delete_tab` of a tab other tabs link to. `tab` is its id, `linked_by` is [{"id", "name"}]."""

    def __init__(self, tab, linked_by):
        self.tab, self.linked_by = tab, linked_by
        names = ", ".join(f"{t['name']!r} ({t['id']})" for t in linked_by)
        super().__init__(f"tab {tab!r} cannot be deleted: linked from {names}")


class SaveRefused(SessionError, EngineError):
    """A `save` edit sent to `submit_edit`: both a SessionError (serve answers 400) and an EngineError."""


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
        self._renamed = set()                   # tab ids renamed here: their name is the manifest's, not the data's
        self._added = set()                     # tab ids added here and not yet saved
        self._deleted = []                      # [(tab id, file)] deleted here whose file save() removes
        self._manifest_dirty = False            # the tab list changed since the last save
        self._closed = False
        self._eng = None
        manifest = self._read_manifest()
        self.id = manifest.get("id")
        self.name = manifest.get("name", "")
        self._entries = [{"id": e["id"], "kind": e.get("kind"), "file": e.get("file"),
                          "name": e.get("name") or e.get("kind") or ""}
                         for e in manifest.get("tabs", [])]
        self._fd = self._manifest_folder(manifest)
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

    def _manifest_folder(self, manifest):
        """A FolderDocument holding only the manifest (its tabs have no document): the one thing that adds a
        tab id and a file name and writes document.json, as the desktop does."""
        fd = DF.FolderDocument()
        fd._store = self.store
        fd.id, fd.name = manifest.get("id") or fd.id, manifest.get("name", fd.name)
        fd.extra = {k: v for k, v in manifest.items() if k not in DF._MANIFEST_KEYS}
        for entry in manifest.get("tabs", []):
            tab = DF.Tab(entry["id"], entry.get("kind"), entry.get("file"),
                         entry.get("name") or entry.get("kind") or "")
            tab.extra = {k: v for k, v in entry.items() if k not in DF._TAB_KEYS}
            fd.tabs.append(tab)
        fd._counter = max([int("".join(c for c in t.id if c.isdigit()) or 0) for t in fd.tabs] + [0])
        return fd

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
            if tid not in self._failed and tid not in self._renamed and self._eng is not None:
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
        if edit.get("op") == "save":
            # the worker writes a save edit wherever its `path` points (or over the tab's file, behind the store's
            # back); a session saves through `save()` only
            raise SaveRefused(f"tab {tab!r}: a save edit is refused; use the session's save()")
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
        live_files = {e["file"] for e in self._entries}
        for tid, file in self._deleted:
            if file not in live_files:               # a later tab may have taken the name
                self.store.delete_tab(tid)
        written = []
        for e in self._entries:
            tid = e["id"]
            if tid in self._failed:
                continue
            data = self._retry(tid, lambda tid=tid: self._eng.tab_data(tid, blobs=self.store.blobs(tid)))
            d = json.loads(bytes(data).decode("utf-8"))
            d["_tab_id"] = tid
            if tid in self._renamed and "name" in d:
                d["name"] = e["name"]                # the file carries the name the manifest gives it
            self.store.write_tab(tid, json.dumps(d, indent=1).encode("utf-8"))
            self._edited[tid] = 0
            written.append(tid)
        if self._manifest_dirty:
            for tab in self._fd.tabs:                # the manifest's name of each tab is the session's
                tab.name = next(e["name"] for e in self._entries if e["id"] == tab.id)
            self._fd.save()                          # no tab has a document: this writes document.json only
            self._manifest_dirty = False
            self._added.clear()
            self._deleted.clear()
        return written

    def tabs_unsaved(self):
        """True while the tab list (an added, renamed or deleted tab) has changes `save()` has not written."""
        return self._manifest_dirty

    # ------------------------------------------------------------------ the tab list
    def add_tab(self, kind, name=None):
        """A new, empty tab of `kind` ("studio" or "assembly"), opened in its own worker; returns its tab id. `name`
        is checked by `check_tab_name`; None gives "Part Studio 2", "Assembly 1", ... The tab id and file name come
        from the manifest folder (`new_tab_id`, `_unique_filename`), the file name from the name through
        `tab_file_stem`, and no file name is one that is already in the folder. Nothing is written until `save()`."""
        self._need_alive()
        if kind not in NEW_KINDS:
            raise SessionError(f"a new tab is one of {', '.join(NEW_KINDS)}, not {kind!r}")
        label = TK.get(kind).label
        if name is None:
            taken = {e["name"] for e in self._entries}
            n = 1
            while f"{label} {n}" in taken:
                n += 1
            name = f"{label} {n}"
        name = check_tab_name(name)
        fd = self._fd
        ext = TK.get(kind).ext
        stem = tab_file_stem(name, label)
        file = fd._unique_filename(stem, ext)
        n = 2
        while self.store.file_taken(file):
            file = fd._unique_filename(f"{stem} {n}", ext)
            n += 1
        tid = fd.new_tab_id()
        try:
            self.store.place_tab(tid, file)          # the store checks the file name
            self._eng.new_tab(kind, name, tab=tid, blobs=self.store.blobs(tid))
        except Exception as ex:                      # noqa: BLE001
            raise SessionError(f"could not add a {label}: {ex}") from ex
        fd.tabs.append(DF.Tab(tid, kind, file, name))
        self._entries.append({"id": tid, "kind": kind, "file": file, "name": name})
        self._edited[tid] = 1                        # not saved yet
        self._added.add(tid)
        self._manifest_dirty = True
        self._refresh_state(tid)
        return tid

    def rename_tab(self, tab, name):
        """Give `tab` a new name (checked by `check_tab_name`). The file keeps its name: a tab is its id, not its
        file. The tab counts as edited until `save()` writes the manifest and the file's own name field."""
        self._need_open(tab)
        name = check_tab_name(name)
        entry = next(e for e in self._entries if e["id"] == tab)
        if entry["name"] == name and tab in self._renamed:
            return
        entry["name"] = name
        self._renamed.add(tab)
        self._edited[tab] = self._edited.get(tab, 0) + 1
        self._manifest_dirty = True

    def linked_by(self, tab):
        """[{"id", "name"}] of the other tabs that link to `tab`: an Assembly instance, a Drawing source, a Part
        Studio's Variable Studio reference or Derived feature, a Context feature naming it. A tab that has edits
        is read from the engine's Link graph; a tab with none is also read from its saved bytes, so a tab that
        did not open still counts."""
        self._need_alive()
        mine = next((e for e in self._entries if e["id"] == tab), None)
        if mine is None:
            raise SessionError(f"no tab {tab!r} in this Document")
        uid = None
        try:
            uid = read_data(mine["kind"], bytes(self.store.read_tab(tab))).get("uid") if self.store.has_tab(tab) else None
        except Exception:                            # noqa: BLE001
            uid = None
        found = set(self._eng.links.graph.dependents(tab))
        for e in self._entries:
            other = e["id"]
            if other == tab or other in found or self._edited.get(other, 0) > 0 or not self.store.has_tab(other):
                continue
            try:
                d = read_data(e["kind"], bytes(self.store.read_tab(other)))
            except Exception:                        # noqa: BLE001
                continue
            for s in links.tab_sources(e["kind"], d):
                if s.get("document") or s.get("version"):
                    continue
                if s.get("tab") == tab or (s.get("tab") not in {x["id"] for x in self._entries} and uid
                                           and s.get("uid") == uid):
                    found.add(other)
            for f in (d.get("features") or []) if e["kind"] == "studio" else ():
                want = (f.get("params") or {}).get("assembly")
                if want and want in (tab, uid):
                    found.add(other)
        return [{"id": e["id"], "name": e["name"]} for e in self._entries if e["id"] in found]

    def delete_tab(self, tab):
        """Remove `tab` and end its worker. A tab another tab links to is refused with a SessionError that names
        it (`linked_by` is the list). The tab's file is removed, and document.json rewritten, by `save()`."""
        self._need_alive()
        if tab not in {e["id"] for e in self._entries}:
            raise SessionError(f"no tab {tab!r} in this Document")
        by = self.linked_by(tab)
        if by:
            raise TabLinked(tab, by)
        entry = next(e for e in self._entries if e["id"] == tab)
        if tab not in self._failed:
            try:
                self._eng.close_tab(tab)
            except EngineError as ex:
                raise SessionError(f"tab {tab!r}: {ex}") from ex
        self._entries.remove(entry)
        self._fd.tabs[:] = [t for t in self._fd.tabs if t.id != tab]
        for table in (self._failed, self._state, self._edited):
            table.pop(tab, None)
        self._renamed.discard(tab)
        if tab in self._added:                       # never saved: nothing to remove
            self._added.discard(tab)
        elif entry.get("file"):
            self._deleted.append((tab, entry["file"]))
        self._manifest_dirty = True

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
