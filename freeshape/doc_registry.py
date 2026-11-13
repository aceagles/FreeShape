"""The Document registry: the app's own record of the Documents it knows, kept in the app's data directory.

For each Document id it records a list of folders (a copy of a Document folder shares the id, so one id can have
several): the folder `path` the app works in, `created` (the trust marker: the app made this folder, so it is
opened in place), and for an imported Document its `source` (the received folder) and `clone` (the managed copy,
equal to `path`). The trust marker lives here and never inside the Document folder, so a sender cannot ship one.
Finding a Document by id goes only through this registry.

Headless -- no Qt. Tests pass their own file; the app uses `default_registry()`.
"""
from __future__ import annotations

import json
import os
import sys

REGISTRY_NAME = "documents.json"
VERSION = 1


def data_dir():
    """The app's data directory (FREESHAPE_DATA_DIR overrides it)."""
    env = os.environ.get("FREESHAPE_DATA_DIR")
    if env:
        return env
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "freeshape")


def managed_root():
    """Where imported Documents are cloned."""
    return os.path.join(data_dir(), "documents")


def default_registry():
    return DocRegistry(os.path.join(data_dir(), REGISTRY_NAME))


def _norm(path):
    return os.path.realpath(path)


class DocRegistry:
    def __init__(self, path):
        self.path = path
        self.documents: dict[str, list[dict]] = {}
        self._load()

    def _load(self):
        try:
            with open(self.path) as fh:
                data = json.load(fh)
            docs = data.get("documents", {})
            self.documents = {k: [r for r in v if isinstance(r, dict) and isinstance(r.get("path"), str)]
                              for k, v in docs.items() if isinstance(v, list)}
        except (OSError, ValueError, AttributeError):
            self.documents = {}

    def _save(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump({"version": VERSION, "documents": self.documents}, fh, indent=1)
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------ record
    def _put(self, doc_id, rec):
        self._load()                                   # another process may have written since
        rows = [r for r in self.documents.get(doc_id, []) if _norm(r["path"]) != rec["path"]]
        rows.append(rec)
        self.documents[doc_id] = rows
        self._save()

    def register_created(self, doc_id, path):
        """The app created the folder at `path`: record the trust marker."""
        self._put(doc_id, {"path": _norm(path), "created": True, "source": None, "clone": None})

    def register_import(self, doc_id, source, clone):
        """The app imported `source` into the managed folder `clone`."""
        self._put(doc_id, {"path": _norm(clone), "created": False, "source": _norm(source),
                           "clone": _norm(clone)})

    # ------------------------------------------------------------------ lookup
    def records(self, doc_id):
        self._load()
        return [dict(r) for r in self.documents.get(doc_id, [])]

    def find(self, doc_id):
        """The folder to open for a Document id: the one the app created, else the latest import. None if unknown."""
        rows = self.records(doc_id)
        created = [r for r in rows if r.get("created")]
        pick = (created or rows or [None])[-1]
        return pick["path"] if pick else None

    def is_trusted(self, doc_id, path):
        """Whether the app made the folder at `path` (created it, or cloned it in an import) for this id."""
        p = _norm(path)
        return any(_norm(r["path"]) == p and (r.get("created") or r.get("clone") == p)
                   for r in self.records(doc_id))

    def import_for_source(self, source):
        """The existing managed clone of a received folder, if it was imported before and is still there."""
        s = _norm(source)
        self._load()
        for rows in self.documents.values():
            for r in rows:
                if r.get("source") == s and r.get("clone") and os.path.isdir(r["clone"]):
                    return r["clone"]
        return None

    def forget(self, doc_id, path):
        self._load()
        rows = [r for r in self.documents.get(doc_id, []) if _norm(r["path"]) != _norm(path)]
        if rows:
            self.documents[doc_id] = rows
        else:
            self.documents.pop(doc_id, None)
        self._save()
