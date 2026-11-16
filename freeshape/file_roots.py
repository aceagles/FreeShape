"""Server file roots (I11.2): read-only folders of STEP files that `freeshape serve --file-root NAME=PATH` offers to
the browser client, and the one place that reads a file out of them.

No Qt here. Each root gets one index, built by `step_library.scan` on first use (callers run `FileRoot.index` off
the event loop) and rebuilt by `rescan`. The index keeps regular files that really live inside the root: symlinks and
anything whose realpath leaves the root are dropped. If the root holds a `manifest.csv` with the columns
source, licence, path, bytes, authors, url (as /home/ncr/component-library does), each file gets its licence,
authors and url from the row whose `source/path` is its relative path, shown as written and never interpreted.

A request value is never joined to a path. A folder is a key in the index's own table of folders; a file is an
opaque id (the first 16 hex digits of the sha256 of its relative path) looked up in the index. `FileRoot.read`
then checks the file again, because the folder can change after indexing, and opens it without following a link."""
from __future__ import annotations

import csv
import hashlib
import os
import re
import stat
import threading
from dataclasses import dataclass, field

from . import step_library as SL

NAME_RE = re.compile(r"[a-z0-9-]{1,32}")
ID_RE = re.compile(r"[0-9a-f]{16}")
MANIFEST = "manifest.csv"
MAX_LIMIT = 500                  # most entries one listing returns
DEFAULT_LIMIT = 200


class FileRootError(Exception):
    """A refusal with the HTTP status the server answers it with."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def parse_spec(value):
    """`(name, realpath)` of a `NAME=PATH` argument, or ValueError with the reason."""
    name, sep, path = value.partition("=")
    if not sep or NAME_RE.fullmatch(name) is None:
        raise ValueError("the name must be 1 to 32 characters of a-z, 0-9 and -, as NAME=PATH")
    if not path or not os.path.isdir(path):
        raise ValueError("the path is not an existing folder")
    return name, os.path.realpath(path)


def entry_id(rel):
    return hashlib.sha256(rel.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def _inside(real, root):
    return real.startswith(root.rstrip(os.sep) + os.sep)


@dataclass(frozen=True)
class Item:
    id: str
    entry: SL.Entry
    licence: str | None = None
    authors: str | None = None
    url: str | None = None

    def view(self):
        e = self.entry
        return {"id": self.id, "name": e.name, "file": e.rel.rsplit("/", 1)[-1], "path": e.rel, "size": e.size,
                "description": e.description, "tags": list(e.tags),
                "licence": self.licence, "authors": self.authors, "url": self.url}


@dataclass
class _Folder:
    subdirs: set = field(default_factory=set)
    files: list = field(default_factory=list)       # Items directly in the folder
    total: int = 0                                  # Items in the folder and below


class Index:
    """The files of one root. `folders` maps a folder's relative path ("" is the root) to its contents; it is the
    only thing a `dir` value is compared against."""

    def __init__(self, items):
        self.items = items
        self.by_id = {i.id: i for i in items}
        self.by_rel = {i.entry.rel: i for i in items}
        self.folders = {"": _Folder()}
        for it in items:
            parts = it.entry.rel.split("/")[:-1]
            path = ""
            self.folders[""].total += 1
            for p in parts:
                parent = self.folders[path]
                path = f"{path}/{p}" if path else p
                parent.subdirs.add(p)
                self.folders.setdefault(path, _Folder()).total += 1
            self.folders[path].files.append(it)
        for f in self.folders.values():
            f.files.sort(key=lambda i: (i.entry.name.lower(), i.entry.rel))

    def listing(self, folder, offset=0, limit=DEFAULT_LIMIT):
        """One folder: its sub-folders and a page of its files. None when `folder` is not a folder of the index."""
        f = self.folders.get(folder)
        if f is None:
            return None
        prefix = folder + "/" if folder else ""
        dirs = [{"name": n, "path": prefix + n, "files": self.folders[prefix + n].total}
                for n in sorted(f.subdirs, key=str.lower)]
        page = f.files[offset:offset + limit]
        return {"dir": folder, "dirs": dirs, "entries": [i.view() for i in page], "total": len(f.files)}

    def search(self, query, folder="", offset=0, limit=DEFAULT_LIMIT):
        """The files whose name, tags or path hold every word of `query` (`step_library.search`), best first;
        with `folder`, only those below it. None when `folder` is not a folder of the index."""
        if folder not in self.folders:
            return None
        pool = [i.entry for i in self.items if not folder or i.entry.rel.startswith(folder + "/")]
        hits = SL.search(pool, query)
        page = [self.by_rel[e.rel].view() for e in hits[offset:offset + limit]]
        return {"dir": folder, "dirs": [], "entries": page, "total": len(hits)}


def _manifest(root):
    """{"source/path": (licence, authors, url)} from the root's manifest.csv; empty if absent or unreadable."""
    path = os.path.join(root, MANIFEST)
    out = {}
    try:
        if os.path.islink(path) or not _inside(os.path.realpath(path), root) or not os.path.isfile(path):
            return out
        with open(path, encoding="utf-8", errors="replace", newline="") as fh:
            for row in csv.DictReader(fh):
                rel = "/".join(p for p in ((row.get("source") or ""), (row.get("path") or "")) if p)
                if rel:
                    out[rel] = (row.get("licence"), row.get("authors"), row.get("url"))
    except (OSError, csv.Error):
        pass
    return out


def build_index(root):
    """The Index of the folder `root` (a realpath)."""
    meta = _manifest(root)
    items = []
    for e in SL.scan(root):
        if os.path.islink(e.path) or not _inside(os.path.realpath(e.path), root):
            continue
        lic, authors, url = meta.get(e.rel, (None, None, None))
        items.append(Item(entry_id(e.rel), e, lic, authors, url))
    return Index(items)


class FileRoot:
    """One named, read-only root. `index`, `rescan` and `read` block (walk or read files): run them off the loop."""

    def __init__(self, name, path):
        if NAME_RE.fullmatch(name) is None or not os.path.isdir(path):
            raise ValueError(f"bad file root {name!r}")
        self.name = name
        self.path = os.path.realpath(path)
        self._lock = threading.Lock()
        self._index = None

    def index(self):
        with self._lock:
            if self._index is None:
                self._index = build_index(self.path)
            return self._index

    def rescan(self):
        with self._lock:
            self._index = build_index(self.path)
            return self._index

    def read(self, eid, max_bytes):
        """`(file name, bytes)` of the entry `eid`. Raises FileRootError: 404 for an id the index does not hold,
        409 when the file is no longer a regular file inside the root, 413 when it is over `max_bytes`."""
        item = self.index().by_id.get(eid) if ID_RE.fullmatch(eid) else None
        if item is None:
            raise FileRootError(404, "not found")
        path = item.entry.path
        changed = FileRootError(409, "the file changed since it was indexed; rescan the root")
        try:
            if not _inside(os.path.realpath(path), self.path):
                raise changed
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)    # a link or a FIFO is not followed
        except OSError:
            raise changed from None
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):                        # a folder, a FIFO, a device
                raise changed
            if st.st_size > max_bytes:
                raise FileRootError(413, f"the file is over {max_bytes} bytes")
            with os.fdopen(fd, "rb", closefd=False) as fh:
                data = fh.read(max_bytes + 1)
        finally:
            os.close(fd)
        if len(data) > max_bytes:
            raise FileRootError(413, f"the file is over {max_bytes} bytes")
        return item.entry.rel.rsplit("/", 1)[-1], data
