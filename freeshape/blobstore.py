"""Content-addressed store for the binary sidecar data a tab keeps beside its file.

A tab is a JSON file in a git folder (ADR 0001), so bytes that do not belong in JSON -- a Python feature's
last built shape (BREP) and its element names, captured Context shapes (ADR 0004), an Assembly instance's or a
Drawing's saved part copy, a Drawing's exact views and imported STEP files (I4.9) -- go to
`<tab dir>/blobs/<sha256 hex>`. FreeCAD keeps the equivalent inside the FCStd zip (a FeaturePython's `Shape`
is saved with the object); here the file is a separate blob, so equal content is stored once and diffs stay small.

The folder may come from someone else, so every blob is hostile data. `get` refuses, with `BlobError`:
a key that is not 64 lowercase hex characters, a symlinked root or blob file, a root that is not a direct child
of the tab folder it names, a blob over `max_bytes`, and content whose sha256 is not the key. Nothing outside
the root is opened, and bytes reach a parser (`shape_from_brep`, `load_json`) only after the hash check.

Writes follow the repo's atomic-save convention (docstore._atomic_write): write `<path>.tmp`, `os.replace`.
No Qt.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile

DEFAULT_MAX_BYTES = 64 * 2 ** 20

_KEY_RE = re.compile(r"[0-9a-f]{64}")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class BlobError(Exception):
    """A blob key, file or content was refused."""


def _check_key(key) -> str:
    if not isinstance(key, str) or _KEY_RE.fullmatch(key) is None:
        raise BlobError("blob key must be 64 lowercase hex characters")
    return key


class BlobStore:
    def __init__(self, root):
        self.root = os.fspath(root)

    # -- paths -----------------------------------------------------------------------------------------
    def _check_root(self):
        """Raise unless `root` exists as a real directory directly inside its parent (the tab folder)."""
        root = os.path.abspath(self.root)
        try:
            st = os.lstat(root)
        except FileNotFoundError:
            raise BlobError("blob folder does not exist") from None
        if stat.S_ISLNK(st.st_mode):
            raise BlobError("blob folder is a symlink")
        if not stat.S_ISDIR(st.st_mode):
            raise BlobError("blob folder is not a directory")
        parent = os.path.realpath(os.path.dirname(root))
        if os.path.realpath(root) != os.path.join(parent, os.path.basename(root)):
            raise BlobError("blob folder resolves outside its tab folder")
        return root

    def _path(self, key) -> str:
        return os.path.join(os.path.abspath(self.root), _check_key(key))

    # -- API -------------------------------------------------------------------------------------------
    def put(self, data: bytes) -> str:
        """Store `data`; returns its sha256 hex. Idempotent. Creates the folder on first use."""
        data = bytes(data)
        key = hashlib.sha256(data).hexdigest()
        root = os.path.abspath(self.root)
        if not os.path.lexists(root):
            os.makedirs(root, exist_ok=True)
        root = self._check_root()
        path = os.path.join(root, key)
        try:
            if self.get(key, max_bytes=len(data)) == data:
                return key
        except BlobError:
            pass  # missing, corrupt or a symlink: replace it below (os.replace swaps the link, not its target)
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | _NOFOLLOW, 0o644)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
        return key

    def get(self, key, max_bytes: int = DEFAULT_MAX_BYTES) -> bytes:
        path = os.path.join(self._check_root(), _check_key(key))
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            raise BlobError(f"no blob {key}") from None
        if not stat.S_ISREG(st.st_mode):
            raise BlobError("blob is not a regular file (symlink?)")
        if st.st_size > max_bytes:
            raise BlobError(f"blob is {st.st_size} bytes, over the {max_bytes} byte limit")
        try:
            fd = os.open(path, os.O_RDONLY | _NOFOLLOW)
        except OSError as e:
            raise BlobError(f"cannot read blob: {e.strerror}") from None
        with os.fdopen(fd, "rb") as fh:
            data = fh.read(max_bytes + 1)  # the file may have grown since lstat
        if len(data) > max_bytes:
            raise BlobError(f"blob is over the {max_bytes} byte limit")
        if hashlib.sha256(data).hexdigest() != key:
            raise BlobError("blob content does not match its key")
        return data

    def has(self, key) -> bool:
        """True when a regular, non-symlink blob of this (valid) key is present. Content is not hashed."""
        try:
            path = os.path.join(self._check_root(), _check_key(key))
            return stat.S_ISREG(os.lstat(path).st_mode)
        except (BlobError, OSError):
            return False

    def keys(self) -> list:
        """Sorted keys of the regular files whose names are valid keys. Stray names are ignored."""
        try:
            root = self._check_root()
            names = os.listdir(root)
        except (BlobError, OSError):
            return []
        return sorted(n for n in names if _KEY_RE.fullmatch(n) and self.has(n))


def store_for(tab_path) -> BlobStore:
    """The store beside a tab file. The folder is created by the first `put`, not here."""
    return BlobStore(os.path.join(os.path.dirname(os.fspath(tab_path)), "blobs"))


# -- one sidecar mechanism for every tab (I4.9) ------------------------------------------------------------
# Old Assembly, Drawing and Part Studio files carry binary data inline (base64). A migration step is a pure
# function on the parsed dict and may run where there is no store (a loose file, a read-only Version, a
# MemoryStore Document), so it moves the bytes out of the tab data into `data[HELD]`, {sha256 key: bytes},
# and leaves the key in their place. `from_data` pops that dict and keeps it as the tab's held blobs: bytes in
# memory that the next save writes to the store (`write_blobs`), exactly as a Context capture not yet saved.
HELD = "_held_blobs"


def key_of(data: bytes) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def hold(data: dict, raw: bytes) -> str:
    """Keep `raw` in `data[HELD]` (see HELD) and return its key: for a migration step."""
    key = key_of(raw)
    data.setdefault(HELD, {})[key] = bytes(raw)
    return key


def take_held(data: dict) -> dict:
    """Pop `data[HELD]` for `from_data`. Only bytes a migration step put there count: JSON read from a file
    cannot hold bytes, so a file that names the key itself contributes nothing."""
    held = data.pop(HELD, None)
    return {k: v for k, v in held.items() if isinstance(v, bytes)} if isinstance(held, dict) else {}


def hold_base64(data: dict, text):
    """`hold` the bytes of a base64 string; None when it is not one (the caller leaves that value alone)."""
    import base64
    import binascii
    if not isinstance(text, str) or not text:
        return None
    try:
        return hold(data, base64.b64decode(text))      # as the loaders before I4.9 decoded it
    except (binascii.Error, ValueError):
        return None


def read_held(held: dict, key, view=None, path=None) -> bytes:
    """A tab's blob: from `held` (bytes migrated from an old file or made since the last save), else from the
    tab's store -- `view` (a docstore.BlobView or BlobStore handed to `from_data`), then the blobs folder beside
    `path` -- and kept in `held` so a save elsewhere can write it. Raises BlobError naming the key."""
    data = held.get(key) if isinstance(key, str) else None
    if data is not None:
        return data
    stores = [s for s in (view, store_for(path) if path else None) if s is not None]
    if not stores:
        raise BlobError(f"blob {key} is missing: the tab has no blob store")
    why = None
    for store in stores:
        try:
            data = store.get(key)
            break
        except BlobError as e:
            why = why or e
    else:
        raise BlobError(f"blob {key} is missing or unreadable ({why})")
    held[key] = data
    return data


# -- BREP and JSON helpers ---------------------------------------------------------------------------------
def brep_bytes(shape) -> bytes:
    """The BREP of a shape, written so the same geometry gives the same bytes (engine.encode.brep_text)."""
    from .engine.encode import brep_text
    return brep_text(shape)


_FLAGS = re.compile(rb"[01]{7}")


def geometry_key(shape) -> str:
    """A key of a shape's geometry and topology alone: the sha256 of `brep_bytes` with the "checked" digit of
    every flag line set to 0. That digit records whether OCCT has validated the sub-shape yet (a shape just read
    from text has not; meshing it validates it), so it varies with what was done to the shape, not with what it
    is. Used to tell whether a saved part copy (K.shape_to_brep bytes, which carry the shape's own sharing,
    stray pcurves and any display triangles) holds the same shape as a live one, so that an unchanged shape keeps
    the key it was saved under (assembly.AssemblyDoc._part_brep, drawing.DrawingDoc._copy)."""
    lines = brep_bytes(shape).split(b"\n")
    for i, ln in enumerate(lines):
        if _FLAGS.fullmatch(ln):
            lines[i] = ln[:2] + b"0" + ln[3:]
    return hashlib.sha256(b"\n".join(lines)).hexdigest()


def same_copy(shape, raw, known=None):
    """True when `raw` (K.shape_to_brep bytes of a saved copy) holds the same shape as `shape` (geometry_key).
    `known` is an optional {sha256 of raw: geometry key} memo, so each saved copy is parsed once."""
    from . import kernel as K
    memo = known if known is not None else {}
    k = key_of(raw)
    if k not in memo:
        try:
            memo[k] = geometry_key(K.shape_from_brep(raw))
        except Exception:                   # noqa: BLE001 -- an unreadable copy is simply not the same
            memo[k] = None
    try:
        return memo[k] is not None and memo[k] == geometry_key(shape)
    except Exception:                       # noqa: BLE001
        return False


def shape_from_brep(data: bytes):
    """Parse BREP bytes into a shape. Raises BlobError on unreadable data; never returns a null shape.
    Call only with bytes that came out of `BlobStore.get`, which has checked their hash."""
    from OCP.BRep import BRep_Builder
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS_Shape
    if not data:
        raise BlobError("empty BREP data")
    shape = TopoDS_Shape()
    try:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "shape.brep")
            with open(path, "wb") as fh:
                fh.write(data)
            ok = BRepTools.Read_s(shape, path, BRep_Builder())
    except OSError as e:
        raise BlobError(f"cannot read BREP: {e}") from None
    if not ok or shape.IsNull():
        raise BlobError("unreadable BREP data")
    return shape


def dump_json(obj) -> bytes:
    """Compact JSON with sorted keys, so equal content gives the same blob key."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def load_json(data: bytes):
    """Parse JSON bytes; BlobError if they are not valid JSON."""
    try:
        return json.loads(bytes(data).decode("utf-8"))
    except (ValueError, RecursionError) as e:
        raise BlobError(f"not valid JSON: {e}") from None
