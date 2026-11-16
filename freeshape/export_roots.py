"""Server export roots (I11.13): writable folders that `freeshape serve --export-root NAME=PATH` saves an export into
(the folder Bambu Studio reads), and the one place that writes there. File roots (`file_roots.py`) stay read-only;
an export root is a separate, explicit thing.

No Qt here. A name from a request is one path component (`clean_export_name`, which is `export_options.export_file_name`,
the rule of a download's name) and is never joined to a path. `ExportRoot.write` opens the root as a directory
descriptor (no symlink followed), checks that its realpath is still the one kept at start, and does everything else
relative to that descriptor.

The file is written under a temporary name in the same folder (so the last step is a rename on one file system) and
published without replace by `os.link` (atomic, fails when the name exists, whatever it is), or, where the file system
refuses hard links, by creating the name with O_EXCL and copying (not atomic: a reader can see a short file for a
moment). With replace it is `os.replace`, after `lstat` shows the name absent or a regular file.

Mode: the saved file is 0644 (`FILE_MODE`), set with fchmod so the umask does not matter. A folder like the shared
one (setgid, group-writable) gives it the folder's group; Bambu Studio runs as the same user and group and needs
only to read it."""
from __future__ import annotations

import errno
import os
import re
import secrets
import stat
import time

from . import export_options as EO
from .file_roots import NAME_RE

FILE_MODE = 0o644
TMP_PREFIX = ".freeshape-export-"
TMP_SUFFIX = ".tmp"
TMP_MAX_AGE = 3600.0                 # seconds after which a leftover temporary file is removed at start
_NO_LINKS = {errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS, errno.EXDEV, errno.EMLINK}
_FULL = {errno.ENOSPC, errno.EDQUOT}


class OverlapError(ValueError):
    """An export root that overlaps something it must not, or a name that is used twice."""


class ExportError(Exception):
    """A refusal with the HTTP status the server answers it with. `code` is "exists" for a taken name. The message
    never holds a server path."""

    def __init__(self, status, message, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


def clean_export_name(name, fmt) -> str:
    """The file name a saved export gets: one path component, the same as a download's name
    (`export_options.export_file_name`), `export.stl` / `export.step` when nothing is left."""
    return EO.export_file_name(name, fmt)


def parse_spec(value):
    """`(name, realpath)` of a `NAME=PATH` argument, or ValueError with the reason. The folder must exist and be
    writable by this process."""
    name, sep, path = value.partition("=")
    if not sep or NAME_RE.fullmatch(name) is None:
        raise ValueError("the name must be 1 to 32 characters of a-z, 0-9 and -, as NAME=PATH")
    if not path or not os.path.isdir(path):
        raise ValueError("the path is not an existing folder")
    if not os.access(path, os.W_OK | os.X_OK):
        raise ValueError("the folder is not writable by this user")
    return name, os.path.realpath(path)


def _inside(real, root):
    return real.startswith(root.rstrip(os.sep) + os.sep)


def overlap(path, other):
    """True when the real folders `path` and `other` are the same, or one holds the other."""
    a, b = os.path.realpath(path), os.path.realpath(other)
    return a == b or _inside(a, b) or _inside(b, a)


def check_overlaps(export_roots, protected):
    """OverlapError (a ValueError) naming the root and what it overlaps, for the first of `export_roots` ((name, path) pairs) that is
    equal to, inside or around a folder of `protected` ((label, path) pairs: the Documents, the data folder, the file
    roots). Two export roots may not overlap each other either."""
    for i, (name, path) in enumerate(export_roots):
        for label, other in protected:
            if other and overlap(path, other):
                raise OverlapError(f"export root {name!r} overlaps {label}")
        for other_name, other in export_roots[:i]:
            if overlap(path, other):
                raise OverlapError(f"export root {name!r} overlaps export root {other_name!r}")


def _check_component(name):
    if (not isinstance(name, str) or not name or name in (".", "..") or "/" in name or "\\" in name or "\0" in name
            or name.startswith(TMP_PREFIX) or len(name.encode("utf-8", "surrogatepass")) > EO.MAX_NAME_BYTES):
        raise ExportError(400, "not a usable file name")


def _write_all(fd, data):
    view = memoryview(data)
    while len(view):
        n = os.write(fd, view)
        view = view[n:]


class ExportRoot:
    """One export root: `name` and `path`, the realpath kept at start. `write` is the only thing that writes."""

    def __init__(self, name, path):
        self.name = name
        self.path = os.path.realpath(path)
        self.clean_stale()

    def clean_stale(self, now=None):
        """Remove this root's own `.freeshape-export-*.tmp` regular files older than TMP_MAX_AGE (what a crash
        between the write and the cleanup leaves). Anything else, a user's `x.tmp` included, is kept."""
        now = time.time() if now is None else now
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError:
            return 0
        removed = 0
        try:
            for entry in os.listdir(fd):
                if not (entry.startswith(TMP_PREFIX) and entry.endswith(TMP_SUFFIX)):
                    continue
                try:
                    st = os.lstat(entry, dir_fd=fd)
                    if stat.S_ISREG(st.st_mode) and now - st.st_mtime > TMP_MAX_AGE:
                        os.unlink(entry, dir_fd=fd)
                        removed += 1
                except OSError:
                    pass
        finally:
            os.close(fd)
        return removed

    def write(self, name, data, replace=False) -> bool:
        """Save `data` as the file `name` (a name `clean_export_name` made: one component) in the root; True when it
        replaced a file. ExportError: 409 code "exists" when the name is taken and `replace` is not set; 409 when the
        name is a symlink, a folder or anything but a regular file (with or without `replace`), or when the folder is
        no longer the one the server started with; 507 when the disk is full; 500 for another write failure. A failure
        leaves neither the file nor a temporary one."""
        _check_component(name)
        try:
            dfd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError:
            raise ExportError(409, "the export folder is not the one the server started with") from None
        tmp = None
        try:
            if os.path.realpath(self.path) != self.path:
                raise ExportError(409, "the export folder is not the one the server started with")
            tmp = TMP_PREFIX + secrets.token_hex(8) + TMP_SUFFIX
            try:
                tfd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, FILE_MODE,
                              dir_fd=dfd)
            except OSError as e:
                tmp = None
                raise self._failure(e) from None
            try:
                os.fchmod(tfd, FILE_MODE)
                _write_all(tfd, data)
                os.fsync(tfd)
            except OSError as e:
                raise self._failure(e) from None
            finally:
                os.close(tfd)
            return self._publish(dfd, tmp, name, data, replace)
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp, dir_fd=dfd)
                except OSError:
                    pass
            os.close(dfd)

    @staticmethod
    def _failure(e):
        if e.errno in _FULL:
            return ExportError(507, "the export folder is full")
        return ExportError(500, f"cannot write to the export folder ({os.strerror(e.errno or 0)})")

    def _publish(self, dfd, tmp, name, data, replace):
        if replace:
            try:
                st = os.lstat(name, dir_fd=dfd)
            except FileNotFoundError:
                existed = False
            except OSError as e:
                raise self._failure(e) from None
            else:
                if not stat.S_ISREG(st.st_mode):
                    raise ExportError(409, f"{name} is not a regular file")
                existed = True
            try:
                os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
            except OSError as e:
                raise self._failure(e) from None
            return existed
        try:
            os.link(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd, follow_symlinks=False)
            return False
        except FileExistsError:
            raise self._exists(dfd, name) from None
        except OSError as e:
            if e.errno not in _NO_LINKS:
                raise self._failure(e) from None
        self._copy_in(dfd, name, data)                       # the file system refuses hard links
        return False

    @staticmethod
    def _exists(dfd, name):
        try:
            if not stat.S_ISREG(os.lstat(name, dir_fd=dfd).st_mode):
                return ExportError(409, f"{name} is not a regular file")
        except OSError:
            pass
        return ExportError(409, f"{name} exists", "exists")

    def _copy_in(self, dfd, name, data):
        """The no-hard-link way to publish without replace: create the name with O_EXCL (so nothing is overwritten and
        a symlink is not followed), copy, fsync; remove it again when the copy fails."""
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, FILE_MODE,
                         dir_fd=dfd)
        except FileExistsError:
            raise self._exists(dfd, name) from None
        except OSError as e:
            raise self._failure(e) from None
        try:
            os.fchmod(fd, FILE_MODE)
            _write_all(fd, data)
            os.fsync(fd)
        except OSError as e:
            try:
                os.unlink(name, dir_fd=dfd)
            except OSError:
                pass
            raise self._failure(e) from None
        finally:
            os.close(fd)
