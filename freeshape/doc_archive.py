"""A Document as one file: the `.fsdoc` archive (ADR 0017). Headless; no Qt.

A Document is stored as a folder under git (ADR 0001). To move it as one file (a download, an upload, a mail
attachment) it is packed into a zip:

    freeshape-archive.json      the marker: {"format": "freeshape-archive", "version": 1}, always the first entry
    .gitattributes              when the folder has one
    document.json               the manifest, as saved
    <tab file>                  each file the manifest names
    <tab dir>/blobs/<sha256>    every blob beside a tab file

Nothing else goes in: no `.git`, no lock file, no other dot-file, no autosave and no file the manifest does not
name. Versions and Workspaces are not carried. Entry names are sorted after the marker and every entry has the
same fixed time and mode, so packing the same folder twice gives the same bytes.

`pack(folder) -> bytes` reads the folder through docstore.FolderStore and the blob store, so a tab file that is
a symlink or resolves outside the folder, a blob folder that is a symlink and a blob whose content is not its
key are refused with the same checks a Document open makes.

`unpack(data, parent, name) -> folder` treats the bytes as hostile. Before anything is written it refuses an
archive that is not a zip, has no marker or a marker of another format or a newer version, has more than
MAX_ENTRIES entries, an entry name that is absolute, holds a `..` segment, a backslash or a drive colon, or
matches no allowed pattern, a duplicate name, a symlink entry, an encrypted entry or one compressed other than
stored or deflated, more than MAX_BYTES unpacked, or an entry over RATIO_FLOOR bytes that is more than MAX_RATIO
times its compressed size. It then unpacks into a temporary folder, refuses a manifest that is not a Document or
names a tab file the archive lacks, gives the Document a fresh id, and copies the folder into
`<parent>/<folder_name_for(name)>` (`<stem> 2`, `<stem> 3`, ... on a clash) with a new repository made by
gitstore.adopt_plain_folder, the step FolderDocument._import uses for a received folder with no repository.
The folder is registered as one the app created, so it opens in place. On any refusal or failure nothing is
left in `parent`.

Both functions take and return plain values (bytes, paths) and hold no global state, so a server can run them
on a worker thread.
"""
from __future__ import annotations

import io
import json
import os
import posixpath
import re
import shutil
import stat
import tempfile
import uuid
import zipfile

from . import blobstore
from . import doc_lock
from . import doc_registry as R
from . import docstore as DS
from . import gitstore as G
from . import kernel as K

EXTENSION = ".fsdoc"
MARKER_NAME = "freeshape-archive.json"
FORMAT = "freeshape-archive"
VERSION = 1
ATTRIBUTES_NAME = ".gitattributes"

MAX_ENTRIES = 5000
MAX_BYTES = 1 << 30            # unpacked, all entries together
MAX_RATIO = 200                # unpacked size over compressed size, per entry ...
RATIO_FLOOR = 1 << 16          # ... for an entry over this many bytes unpacked
MAX_MARKER_BYTES = 1 << 16

_FIXED_TIME = (1980, 1, 1, 0, 0, 0)
_FILE_MODE = 0o100644
_CHUNK = 1 << 20

# a path segment: no separator, colon or control character, and no leading dot (so no dot-file, `.` or `..`)
_SEG = r"[^/\\:\x00-\x1f\x7f.][^/\\:\x00-\x1f\x7f]*"
_TAB_RE = re.compile(rf"(?:{_SEG}/)*{_SEG}")
_BLOB_RE = re.compile(rf"(?:{_SEG}/)*blobs/[0-9a-f]{{64}}")


class ArchiveError(K.KernelError):
    """A Document could not be packed, or an archive was refused. The message is for the person."""


# =========================================================================== entry names

def _tab_extensions():
    from . import tabkinds as TK
    return tuple(e.lower() for e in TK.extensions())


def name_problem(name):
    """Why `name` may not be an entry of an archive, or None when it may. Allowed: the marker, `document.json`,
    `.gitattributes`, a tab file (`a/b/c.fshape`, any tab kind's extension) and a blob (`a/b/blobs/<sha256>`), each
    with no segment that starts with a dot."""
    if not isinstance(name, str) or not name:
        return "an empty name isn't allowed"
    if name.startswith("/"):
        return "an absolute path isn't allowed"
    if "\\" in name:
        return "a backslash isn't allowed"
    if re.match(r"[A-Za-z]:", name) or ":" in name:
        return "a drive or a colon isn't allowed"
    if ".." in name.split("/"):
        return "a '..' segment isn't allowed"
    if name in (MARKER_NAME, DS.MANIFEST_NAME, ATTRIBUTES_NAME):
        return None
    if _BLOB_RE.fullmatch(name):
        return None
    if _TAB_RE.fullmatch(name) and name.lower().endswith(_tab_extensions()):
        return None
    return "it matches no file a Document archive holds"


def _check_name(name, label):
    why = name_problem(name)
    if why is not None:
        raise ArchiveError(f"{label} {name!r}: {why}")


# =========================================================================== pack

def _attributes(folder):
    """The bytes of the folder's `.gitattributes`, or None when it has none. Refused when it is a symlink or not a
    regular file."""
    path = os.path.join(folder, ATTRIBUTES_NAME)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(st.st_mode):
        raise ArchiveError(f"{ATTRIBUTES_NAME}: refusing a symlink or a file that is not a regular file")
    with open(path, "rb") as fh:
        return fh.read(MAX_BYTES + 1)


def _entries(folder):
    """{entry name: bytes} of everything a Document folder's archive holds, the marker aside."""
    from . import doc_folder as DF
    store = DS.FolderStore(folder)
    try:
        raw = store.read_manifest(max_bytes=MAX_BYTES)
    except FileNotFoundError:
        raise ArchiveError(f"{folder} is not a Document folder: it has no {DS.MANIFEST_NAME}") from None
    manifest = DF.FolderDocument._read_manifest(raw)
    out = {DS.MANIFEST_NAME: bytes(raw)}
    attributes = _attributes(folder)
    if attributes is not None:
        out[ATTRIBUTES_NAME] = attributes
    for entry in manifest.get("tabs", []):
        tab_id, file = entry["id"], entry.get("file")
        label = f"tab {entry.get('name') or tab_id!r}"
        store.place_tab(tab_id, file)
        rel = posixpath.normpath(file)
        _check_name(rel, f"{label}: its file")
        if rel in out:
            raise ArchiveError(f"{label}: its file {file!r} is named by another tab too")
        with open(store.checked_tab_path(tab_id), "rb") as fh:
            out[rel] = fh.read(MAX_BYTES + 1)
        blobs = store.blobs(tab_id)
        for key in blobs.keys():
            name = posixpath.join(posixpath.dirname(rel), "blobs", key)
            if name not in out:
                try:
                    out[name] = blobs.get(key, max_bytes=MAX_BYTES)
                except blobstore.BlobError as e:
                    raise ArchiveError(f"{label}: blob {key}: {e}") from None
    return out


def _marker():
    return json.dumps({"format": FORMAT, "version": VERSION}, indent=1, sort_keys=True).encode("utf-8")


def pack(folder) -> bytes:
    """The `.fsdoc` archive of the Document folder at `folder`, as saved on disk. Raises ArchiveError (a
    K.KernelError) when the folder is not a Document, a tab file or blob is refused by the store's checks, or the
    result would be refused by `unpack`."""
    entries = _entries(folder)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in [(MARKER_NAME, _marker())] + sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=_FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = _FILE_MODE << 16
            z.writestr(info, data)
    out = buf.getvalue()
    with zipfile.ZipFile(io.BytesIO(out)) as z:
        _inspect(z)                         # never make an archive that unpack refuses
    return out


# =========================================================================== unpack

def _inspect(z):
    """The entries of the open zip `z`, after every check that needs only its directory. Raises ArchiveError."""
    infos = z.infolist()
    if len(infos) > MAX_ENTRIES:
        raise ArchiveError(f"the archive has {len(infos)} entries, over the limit of {MAX_ENTRIES}")
    names = {i.filename for i in infos}
    if MARKER_NAME not in names:
        raise ArchiveError(f"not a FreeShape Document archive: it has no {MARKER_NAME}")
    seen, total = set(), 0
    for info in infos:
        name = info.filename
        _check_name(name, "entry")
        folded = name.casefold()
        if folded in seen:
            raise ArchiveError(f"entry {name!r}: the archive holds that name twice")
        seen.add(folded)
        if stat.S_ISLNK(info.external_attr >> 16):
            raise ArchiveError(f"entry {name!r}: a symlink isn't allowed")
        if info.flag_bits & 0x1:
            raise ArchiveError(f"entry {name!r}: an encrypted entry isn't allowed")
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise ArchiveError(f"entry {name!r}: only stored or deflated entries are allowed")
        total += info.file_size
        if total > MAX_BYTES:
            raise ArchiveError(f"the archive unpacks to more than {MAX_BYTES} bytes")
        if info.file_size > RATIO_FLOOR and info.file_size > MAX_RATIO * max(info.compress_size, 1):
            raise ArchiveError(f"entry {name!r}: it unpacks to more than {MAX_RATIO} times its packed size")
    if DS.MANIFEST_NAME not in names:
        raise ArchiveError(f"the archive has no {DS.MANIFEST_NAME}")
    return infos


def _read(z, info, limit):
    """An entry's bytes, read at most `limit` + 1 bytes far. Raises ArchiveError over `limit` or when damaged."""
    try:
        with z.open(info) as fh:
            data = fh.read(limit + 1)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, EOFError, ValueError) as e:
        raise ArchiveError(f"entry {info.filename!r} is damaged ({e})") from None
    if len(data) > limit:
        raise ArchiveError(f"entry {info.filename!r} is over {limit} bytes")
    return data


def _check_marker(z, info):
    try:
        marker = json.loads(_read(z, info, MAX_MARKER_BYTES).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ArchiveError(f"{MARKER_NAME} is not valid JSON") from None
    if not isinstance(marker, dict) or marker.get("format") != FORMAT:
        raise ArchiveError(f"not a FreeShape Document archive: {MARKER_NAME} names another format")
    version = marker.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ArchiveError(f"{MARKER_NAME}: version must be an integer, not {version!r}")
    if version > VERSION:
        raise ArchiveError(f"the archive was made with archive version {version}, but this build of FreeShape "
                           f"opens only up to version {VERSION}. Update FreeShape to open it.")


def _extract(z, infos, into):
    """Write every entry under the fresh folder `into`, counting what is written against MAX_BYTES."""
    written = 0
    root = os.path.realpath(into)
    for info in infos:
        dest = os.path.join(root, *info.filename.split("/"))
        if os.path.commonpath([root, os.path.realpath(os.path.dirname(dest))]) != root:
            raise ArchiveError(f"entry {info.filename!r} resolves outside the folder")
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with z.open(info) as src, open(dest, "xb") as out:
                while True:
                    chunk = src.read(_CHUNK)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_BYTES:
                        raise ArchiveError(f"the archive unpacks to more than {MAX_BYTES} bytes")
                    out.write(chunk)
        except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError) as e:
            raise ArchiveError(f"entry {info.filename!r} is damaged ({e})") from None
        except OSError as e:
            raise ArchiveError(f"entry {info.filename!r} cannot be written ({e.strerror or e})") from None


def _check_tabs(folder):
    """Refuse a manifest that is not a Document or names a tab file the folder lacks; returns the manifest."""
    from . import doc_folder as DF
    store = DS.FolderStore(folder)
    manifest = DF.FolderDocument._read_manifest(store.read_manifest(max_bytes=MAX_BYTES))
    for entry in manifest.get("tabs", []):
        store.place_tab(entry["id"], entry.get("file"))
        store.checked_tab_path(entry["id"])
    return manifest


def _folder_stem(name):
    from .serve import folder_name_for
    try:
        return folder_name_for(name)
    except ValueError as e:
        raise ArchiveError(f"the Document name {name!r}: {e}") from None


def _adopt(staging, parent, stem):
    """Copy `staging` into a new folder `<parent>/<stem>` (or `<stem> 2`, ...) and give it a repository; returns
    its path. Without a usable git the folder is kept with no repository, as FolderDocument.create keeps one."""
    from . import doc_folder as DF
    G.ensure_attributes(staging, DF._attribute_lines())
    os.makedirs(parent, exist_ok=True)
    root = os.path.realpath(parent)
    for n in range(1, 1000):
        leaf = stem if n == 1 else f"{stem} {n}"
        dest = os.path.join(root, leaf)
        if os.path.lexists(dest):
            continue
        try:
            G.adopt_plain_folder(staging, root, leaf, message="Import archive")
        except FileExistsError:
            continue                        # made by someone else in between: not ours to remove
        except G.GitUnavailable:
            pass
        except BaseException:
            shutil.rmtree(dest, ignore_errors=True)
            raise
        doc_lock.ensure_excluded(dest)
        return dest
    raise ArchiveError(f"too many Documents are called {stem!r} in {parent}")


def unpack(data, parent, name, registry=None) -> str:
    """Unpack the `.fsdoc` archive `data` (bytes) into a new Document folder under `parent`, named from `name`
    by `folder_name_for`. Returns the folder's path. The Document gets a fresh id, a new repository and a record in
    `registry` (the app's own by default) as a Document the app created. Raises ArchiveError (a K.KernelError)
    with a message for the person, and then leaves nothing in `parent`."""
    stem = _folder_stem(name)
    try:
        z = zipfile.ZipFile(io.BytesIO(bytes(data)))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError, EOFError):
        raise ArchiveError("not a FreeShape Document archive: it is not a zip file") from None
    with z:
        infos = _inspect(z)
        _check_marker(z, z.getinfo(MARKER_NAME))
        payload = [i for i in infos if i.filename != MARKER_NAME]
        with tempfile.TemporaryDirectory(prefix="fsdoc-") as tmp:
            staging = os.path.join(tmp, "document")
            os.mkdir(staging)
            _extract(z, payload, staging)
            try:
                manifest = _check_tabs(staging)
            except ArchiveError:
                raise
            except K.KernelError as e:
                raise ArchiveError(f"the archive's Document is refused: {e}") from None
            manifest["id"] = doc_id = uuid.uuid4().hex
            DS.FolderStore(staging).write_manifest(json.dumps(manifest, indent=1).encode("utf-8"))
            dest = _adopt(staging, parent, stem)
    try:
        (registry or R.default_registry()).register_created(doc_id, dest)
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return dest
