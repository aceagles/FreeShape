"""The Document store (ADR 0009): the one interface a Document's data goes through -- its manifest, each tab's
data by tab id, and blobs by content hash. Headless; no Qt.

`DocumentStore` is the interface. `FolderStore` is the first implementation and holds exactly the on-disk
behaviour a Document folder had before the interface existed (ADR 0001): `document.json` in the folder, one
file per tab named by the manifest's `file` entry, blobs in a `blobs/` folder beside the tab file, every write a
`<path>.tmp` moved into place with `os.replace`, and the path-safety checks below.

A store keys tab data by tab id. A folder also needs the file name the manifest gives a tab; the Document hands
it over with `place_tab` before it touches that tab's data. A store that keys data by tab id alone ignores it.

Git is outside the interface: Versions, Workspaces and merge run git in a `FolderStore`'s folder, found through
`git_folder`, which refuses a store that has no folder.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod

from . import blobstore
from . import kernel as K

MANIFEST_NAME = "document.json"


def _atomic_write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(text)
    os.replace(tmp, path)


class DocumentStore(ABC):
    """Where a Document's data lives. Data is bytes; the store never parses a tab."""

    has_folder = False      # True: the data is files in `folder`, so git may run there (see git_folder)
    folder = None

    @abstractmethod
    def describe(self) -> str:
        """Where the data lives, for messages: "the Document folder /x/y"."""

    # -- the manifest --------------------------------------------------------------------------------------
    @abstractmethod
    def read_manifest(self, max_bytes=None) -> bytes:
        """The manifest's bytes. Raises K.KernelError when it is refused, or when over `max_bytes`."""

    @abstractmethod
    def write_manifest(self, data: bytes) -> None:
        ...

    # -- tab data, by tab id -------------------------------------------------------------------------------
    def place_tab(self, tab_id, file) -> None:
        """Tell the store the manifest's `file` entry for `tab_id`. Raises K.KernelError when the store
        cannot hold data under that name. A store keyed by tab id alone has nothing to check."""

    @abstractmethod
    def list_tabs(self, tab_ids=None) -> list:
        """Ids of the tabs whose data the store holds (an entry that would be refused on read counts), from
        `tab_ids` when given."""

    @abstractmethod
    def has_tab(self, tab_id) -> bool:
        """True when the store holds readable data for `tab_id`."""

    @abstractmethod
    def read_tab(self, tab_id) -> bytes:
        """A tab's data. Raises K.KernelError naming the tab when it is missing or refused."""

    @abstractmethod
    def write_tab(self, tab_id, data: bytes) -> None:
        ...

    @abstractmethod
    def delete_tab(self, tab_id) -> None:
        """Drop a tab's data; nothing happens when there is none."""

    def strays(self, tab_ids) -> list:
        """Names of tab data the store holds that belongs to none of `tab_ids`, for a warning."""
        return []

    def tab_path(self, tab_id):
        """A file path holding the tab's data, or None when the store keeps no files. The path a loaded tab
        records as its `path`."""
        return None

    def checked_tab_path(self, tab_id):
        """`tab_path` after the checks `read_tab` makes, for a kind that can only load from a file."""
        raise K.KernelError(f"tab {tab_id!r}: {self.describe()} keeps no files, and this kind of tab loads "
                            f"only from a file")

    def file_taken(self, file) -> bool:
        """True when `file` would clash with data already in the store under that name."""
        return False

    # -- blobs, by content hash ----------------------------------------------------------------------------
    @abstractmethod
    def read_blob(self, key, tab_id=None, max_bytes=blobstore.DEFAULT_MAX_BYTES) -> bytes:
        """The blob whose sha256 is `key`, checked against the key. Raises blobstore.BlobError. `tab_id`
        names the tab the blob belongs to, for a store that keeps blobs beside each tab."""

    @abstractmethod
    def write_blob(self, data: bytes, tab_id=None) -> str:
        """Store `data`; returns its sha256 hex. Idempotent."""

    @abstractmethod
    def has_blob(self, key, tab_id=None) -> bool:
        ...

    def blobs(self, tab_id=None):
        """An object with the BlobStore methods `get`, `put` and `has`, bound to one tab's blobs."""
        return BlobView(self, tab_id)


class BlobView:
    """One tab's blobs in a store, with the method names of blobstore.BlobStore."""

    def __init__(self, store, tab_id=None):
        self.store, self.tab_id = store, tab_id

    def get(self, key, max_bytes=blobstore.DEFAULT_MAX_BYTES):
        return self.store.read_blob(key, self.tab_id, max_bytes)

    def put(self, data):
        return self.store.write_blob(data, self.tab_id)

    def has(self, key):
        return self.store.has_blob(key, self.tab_id)


def git_folder(store):
    """The folder git runs in for Versions, Workspaces and merge (None for a folder not chosen yet). Raises
    K.KernelError for a store that keeps no folder."""
    if not getattr(store, "has_folder", False):
        raise K.KernelError(f"Versions, Workspaces and merge need a Document kept in a folder under git; "
                            f"this one is kept in {store.describe()}")
    return store.folder


class FolderStore(DocumentStore):
    """A Document folder on disk, exactly as ADR 0001 keeps it.

    The folder may come from someone else, so the manifest's file names are untrusted: a tab's `file` must be a
    relative path with no '..' segment (checked by `place_tab`, before anything is read or written), must
    resolve inside the folder when read (no escape through a symlinked directory), and neither a tab file nor
    the manifest may be a symlink when read. A write never needs the resolve check: `_atomic_write` replaces
    the target with `os.replace`, which never follows a symlink at the destination."""

    has_folder = True

    def __init__(self, folder):
        self.folder = folder
        self._files = {}

    def describe(self):
        return f"the Document folder {self.folder}"

    # -- the manifest --------------------------------------------------------------------------------------
    def read_manifest(self, max_bytes=None):
        path = os.path.join(self.folder, MANIFEST_NAME)
        if os.path.islink(path):
            raise K.KernelError(f"{MANIFEST_NAME}: refusing to open a symlink")
        if max_bytes is not None and os.path.getsize(path) > max_bytes:
            raise K.KernelError(f"{MANIFEST_NAME} is over {max_bytes} bytes")
        with open(path, "rb") as fh:
            return fh.read()

    def write_manifest(self, data):
        os.makedirs(self.folder, exist_ok=True)
        _atomic_write(os.path.join(self.folder, MANIFEST_NAME), bytes(data).decode("utf-8"))

    # -- tab data ------------------------------------------------------------------------------------------
    def _resolve(self, rel, label):
        """Join `rel` onto the folder after structural checks that need no filesystem access: no absolute
        path, no '..' segment."""
        if not rel or not isinstance(rel, str):
            raise K.KernelError(f"{label}: missing a file path")
        if os.path.isabs(rel) or (len(rel) >= 2 and rel[1] == ":"):
            raise K.KernelError(f"{label}: an absolute path isn't allowed ({rel!r})")
        if ".." in rel.replace("\\", "/").split("/"):
            raise K.KernelError(f"{label}: a '..' segment isn't allowed ({rel!r})")
        return os.path.normpath(os.path.join(self.folder, rel))

    def place_tab(self, tab_id, file):
        self._resolve(file, f"tab {tab_id!r}")
        self._files[tab_id] = file

    def _file(self, tab_id):
        file = self._files.get(tab_id)
        if file is None:
            raise K.KernelError(f"tab {tab_id!r}: no file is known for it in {self.describe()}")
        return file

    def tab_path(self, tab_id):
        return self._resolve(self._file(tab_id), f"tab {tab_id!r}")

    def checked_tab_path(self, tab_id):
        """`tab_path` plus the checks made before any read: the real path lands inside the folder (this follows
        a symlinked leaf to see where it points, so only a read uses it), the file exists and is no symlink."""
        label, file = f"tab {tab_id!r}", self._file(tab_id)
        full = self._resolve(file, label)
        root = os.path.realpath(self.folder)
        if os.path.commonpath([root, os.path.realpath(full)]) != root:
            raise K.KernelError(f"{label}: resolves outside the Document folder ({file!r})")
        if not os.path.lexists(full):
            raise K.KernelError(f"{label}: its file {file!r} is missing from the Document folder")
        if os.path.islink(full):
            raise K.KernelError(f"{label}: refusing to open a symlink ({file!r})")
        return full

    def list_tabs(self, tab_ids=None):
        ids = self._files if tab_ids is None else [i for i in tab_ids if i in self._files]
        return [i for i in ids if os.path.lexists(self.tab_path(i))]

    def has_tab(self, tab_id):
        return tab_id in self._files and os.path.exists(self.tab_path(tab_id))

    def read_tab(self, tab_id):
        with open(self.checked_tab_path(tab_id), "rb") as fh:
            return fh.read()

    def write_tab(self, tab_id, data):
        full = self.tab_path(tab_id)
        os.makedirs(self.folder, exist_ok=True)
        _atomic_write(full, bytes(data).decode("utf-8"))

    def delete_tab(self, tab_id):
        if tab_id in self._files:
            full = self.tab_path(tab_id)
            if os.path.lexists(full):
                os.remove(full)

    def file_taken(self, file):
        return os.path.exists(os.path.join(self.folder, file))

    def strays(self, tab_ids):
        """Files in the folder (outside dot folders) with a tab kind's extension that no tab in `tab_ids` owns."""
        from . import tabkinds as TK
        listed = {os.path.normpath(os.path.relpath(self.tab_path(i), self.folder))
                  for i in tab_ids if i in self._files}
        exts = TK.extensions()
        out = []
        for root, dirs, files in os.walk(self.folder or "."):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for f in sorted(files):
                rel = os.path.normpath(os.path.relpath(os.path.join(root, f), self.folder))
                if f.endswith(exts) and rel not in listed:
                    out.append(rel)
        return out

    # -- blobs: `blobs/` beside the tab file (the folder itself when no tab is named) -----------------------
    def blobs(self, tab_id=None):
        """The blobstore.BlobStore beside `tab_id`'s file."""
        anchor = self.tab_path(tab_id) if tab_id is not None else os.path.join(self.folder, MANIFEST_NAME)
        return blobstore.store_for(anchor)

    def read_blob(self, key, tab_id=None, max_bytes=blobstore.DEFAULT_MAX_BYTES):
        return self.blobs(tab_id).get(key, max_bytes=max_bytes)

    def write_blob(self, data, tab_id=None):
        return self.blobs(tab_id).put(data)

    def has_blob(self, key, tab_id=None):
        return self.blobs(tab_id).has(key)
