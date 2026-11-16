"""The server's own autosave (I2.6): edits made through `freeshape serve` that `save` has not written. No Qt.

Why it is not the desktop's. The desktop keeps one folder per process id under a path Qt gives it, calls a folder
orphaned when that process id is not alive in its own container, and its recovery prompt deletes the files whichever
answer is given. From the desktop's container a running server looks dead. So the server keeps a separate autosave,
in a place the desktop never looks:

    <data dir>/autosave-server/<holder instance id>/
        manifest.json       {"format", "document", "name", "instance", "time", "entries", "added", "renamed",
                             "deleted", "tabs": {tab id: {"sha", "base"}}, "blobs": [key], "manifest_base"}
        blobs/<sha256>      the data of each dirty tab and every blob it names, content addressed

`<data dir>` is the directory that holds documents.json (`--data-dir`, $FREESHAPE_DATA_DIR). The desktop looks
only inside `<its Qt data location>/FreeShape/autosave`, for numeric folder names and tab files, so `autosave-server`
is neither inside that folder nor named like what it scans for.

The folder name is the instance id of the Document lock the server held when it wrote the autosave (doc_lock), so
ownership follows the lock: an autosave whose holder still holds a fresh lock is live and nobody else lists, offers
or deletes it; one whose holder's lock is gone, stale or someone else's is recoverable (`recoverable`). A path is
built from that instance id (32 hex characters) and a name made here, never from anything else; a Document id and
the tab ids are only ever read from and written to the manifest, checked against the patterns below.

Writes are atomic as a whole: tab data goes into the content-addressed `blobs/` first (temp file, then rename), then
the manifest is replaced (temp file, fsync, rename), then files nothing names any more are removed. A crash at any
point leaves the old manifest with every file it names, or the new one with every file it names; a half-written
file is never named by a manifest, and `load` checks every file against its hash.

Nothing here deletes an autosave except `remove`, which the server calls for its own autosave after a save and
for the two routes that say so (recover, discard-autosave).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time

from . import blobstore

DIR_NAME = "autosave-server"
MANIFEST = "manifest.json"
BLOBS = "blobs"
FORMAT = 1
MAX_MANIFEST = 8 * 2 ** 20
MAX_ENTRIES = 2000
_INSTANCE = re.compile(r"[0-9a-f]{32}")
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_KEY = re.compile(r"[0-9a-f]{64}")


class AutosaveError(Exception):
    """An autosave that cannot be written, or whose files do not match its manifest."""


def valid_instance(instance):
    return isinstance(instance, str) and _INSTANCE.fullmatch(instance) is not None


def root(data_dir):
    return os.path.join(os.fspath(data_dir), DIR_NAME)


def folder(data_dir, instance):
    """The autosave folder of lock instance `instance`; AutosaveError unless it is 32 lowercase hex characters."""
    if not valid_instance(instance):
        raise AutosaveError("not an instance id")
    return os.path.join(root(data_dir), instance)


class Info:
    """What an autosave's manifest says (read back by `read_info`). `tabs` is {tab id: {"sha", "base"}}: the key of
    the saved data and the sha256 of the saved file the session had loaded when the autosave was written (None for a
    tab added since). `entries` is the whole tab list at that time: [{"id", "kind", "file", "name"}]."""

    def __init__(self, m):
        self.instance = m["instance"]
        self.document = m["document"]
        self.name = m["name"]
        self.time = m["time"]
        self.entries = m["entries"]
        self.added = m["added"]
        self.renamed = m["renamed"]
        self.deleted = m["deleted"]
        self.tabs = m["tabs"]
        self.blobs = m["blobs"]
        self.manifest_base = m["manifest_base"]

    @property
    def list_changed(self):
        """The autosave holds a change to the tab list (an added, renamed or deleted tab)."""
        return bool(self.added or self.renamed or self.deleted)

    def bases(self):
        return {tid: t["base"] for tid, t in self.tabs.items()}

    def summary(self):
        """The part of the autosave the routes tell the client."""
        names = {e["id"]: e["name"] for e in self.entries}
        return {"time": self.time, "tabs": [{"id": tid, "name": names.get(tid, tid)} for tid in sorted(self.tabs)],
                "deleted": [d["id"] for d in self.deleted]}


def _sha(data):
    return hashlib.sha256(bytes(data)).hexdigest()


def _ids(value):
    return isinstance(value, list) and len(value) <= MAX_ENTRIES and all(
        isinstance(x, str) and _ID.fullmatch(x) for x in value)


def _text(value, limit):
    return isinstance(value, str) and len(value) <= limit


def _parse(raw, instance):
    """The manifest dict from `raw` bytes, or None when it is not a well-formed manifest of `instance`."""
    try:
        m = json.loads(raw.decode("utf-8"))
        ok = (isinstance(m, dict) and m.get("format") == FORMAT and m.get("instance") == instance
              and isinstance(m.get("document"), str) and _ID.fullmatch(m["document"])
              and _text(m.get("name"), 400) and isinstance(m.get("time"), (int, float))
              and not isinstance(m["time"], bool)
              and isinstance(m.get("entries"), list) and len(m["entries"]) <= MAX_ENTRIES
              and all(isinstance(e, dict) and isinstance(e.get("id"), str) and _ID.fullmatch(e["id"])
                      and _text(e.get("kind"), 40) and _text(e.get("file"), 400) and _text(e.get("name"), 400)
                      for e in m["entries"])
              and _ids(m.get("added")) and _ids(m.get("renamed"))
              and isinstance(m.get("deleted"), list) and len(m["deleted"]) <= MAX_ENTRIES
              and all(isinstance(d, dict) and isinstance(d.get("id"), str) and _ID.fullmatch(d["id"])
                      for d in m["deleted"])
              and isinstance(m.get("tabs"), dict) and len(m["tabs"]) <= MAX_ENTRIES
              and all(_ID.fullmatch(k) and isinstance(v, dict) and isinstance(v.get("sha"), str)
                      and _KEY.fullmatch(v["sha"]) and (v.get("base") is None or (
                          isinstance(v["base"], str) and _KEY.fullmatch(v["base"]))) for k, v in m["tabs"].items())
              and isinstance(m.get("blobs"), list) and len(m["blobs"]) <= 10 * MAX_ENTRIES
              and all(isinstance(k, str) and _KEY.fullmatch(k) for k in m["blobs"])
              and (m.get("manifest_base") is None or (isinstance(m["manifest_base"], str)
                                                      and _KEY.fullmatch(m["manifest_base"]))))
    except (ValueError, UnicodeDecodeError, AttributeError, TypeError):
        return None
    return m if ok else None


def read_info(data_dir, instance):
    """The `Info` of instance `instance`'s autosave, or None when it has none or its manifest is not valid."""
    try:
        path = os.path.join(folder(data_dir, instance), MANIFEST)
        if os.path.islink(path):
            return None
        with open(path, "rb") as fh:
            raw = fh.read(MAX_MANIFEST + 1)
    except (OSError, AutosaveError):
        return None
    if len(raw) > MAX_MANIFEST:
        return None
    m = _parse(raw, instance)
    return Info(m) if m is not None else None


def scan(data_dir):
    """Every valid autosave under the data directory, oldest first. A folder whose name is not an instance id, or
    whose manifest is missing or malformed, is not listed (and is left alone)."""
    try:
        names = os.listdir(root(data_dir))
    except OSError:
        return []
    out = [read_info(data_dir, n) for n in names if valid_instance(n)]
    return sorted((i for i in out if i is not None), key=lambda i: (i.time, i.instance))


def index(data_dir):
    """{Document id: [Info, oldest first]} for every valid autosave."""
    out = {}
    for info in scan(data_dir):
        out.setdefault(info.document, []).append(info)
    return out


def is_live(info, holder):
    """Whether `info`'s holder still holds a fresh lock: `holder` is the Document's doc_lock.Holder (or None)."""
    return holder is not None and holder.instance == info.instance and not holder.stale


def recoverable(infos, holder, own=None):
    """The autosaves of `infos` (one Document's) that may be offered: not the live one (see `is_live`) and not
    `own` (the instance id of the lock the asking server holds, whose autosave is its own)."""
    return [i for i in infos if i.instance != own and not is_live(i, holder)]


def _write(path, data, sync=False):
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
        if sync:
            fh.flush()
            os.fsync(fh.fileno())
    os.replace(tmp, path)


def write(data_dir, instance, state, now=None):
    """Write `state` (what DocumentSession.autosave_state gives) as the autosave of lock instance `instance`,
    replacing that instance's earlier one; returns the `Info`. See the module docstring for the order of writes."""
    where = folder(data_dir, instance)
    os.makedirs(where, exist_ok=True)
    store = blobstore.BlobStore(os.path.join(where, BLOBS))
    tabs = {}
    for tid, (data, base) in state["tabs"].items():
        tabs[tid] = {"sha": store.put(data), "base": base}
    keys = sorted(state["blobs"])
    for key in keys:
        if not store.has(key):                                # content addressed: one that is there is the same
            store.put(state["blobs"][key])
    manifest = {"format": FORMAT, "document": state["document"], "name": state["name"], "instance": instance,
                "time": time.time() if now is None else now, "entries": state["entries"], "added": sorted(state["added"]),
                "renamed": sorted(state["renamed"]), "deleted": state["deleted"], "tabs": tabs, "blobs": keys,
                "manifest_base": state["manifest_base"]}
    raw = json.dumps(manifest, indent=1).encode("utf-8")
    if _parse(raw, instance) is None:
        raise AutosaveError("the state to write is not a valid autosave")
    _write(os.path.join(where, MANIFEST), raw, sync=True)
    named = {t["sha"] for t in tabs.values()} | set(keys)
    for key in store.keys():                                  # what nothing names any more
        if key not in named:
            try:
                os.unlink(os.path.join(where, BLOBS, key))
            except OSError:
                pass
    return Info(json.loads(raw.decode("utf-8")))


def load(data_dir, info):
    """The data of `info`'s autosave: {"tabs": {tab id: bytes}, "blobs": {key: bytes}} with the rest of the manifest
    (`entries`, `added`, `renamed`, `deleted`). Every file is checked against its hash; AutosaveError names the
    one that is missing or does not match."""
    store = blobstore.BlobStore(os.path.join(folder(data_dir, info.instance), BLOBS))
    try:
        tabs = {tid: store.get(t["sha"]) for tid, t in info.tabs.items()}
        blobs = {key: store.get(key) for key in info.blobs}
    except blobstore.BlobError as e:
        raise AutosaveError(f"the autosave is damaged: {e}") from e
    return {"entries": info.entries, "added": info.added, "renamed": info.renamed, "deleted": info.deleted,
            "tabs": tabs, "blobs": blobs}


def remove(data_dir, instance):
    """Delete instance `instance`'s autosave folder; True when there was one."""
    where = folder(data_dir, instance)
    if not os.path.lexists(where):
        return False
    if os.path.islink(where):
        os.unlink(where)
    else:
        shutil.rmtree(where, ignore_errors=True)
    return True
