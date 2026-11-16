"""The Document lock: one program edits a Document folder at a time. Headless -- no Qt, no git, no thread.

The desktop and the server run in different containers that mount the same Documents folder, so the lock is a
file in the Document folder (`.freeshape-lock`), not a process or a host fact. It holds JSON
`{program, instance, heartbeat}`: the program name ("desktop", "server"), a random instance id and the wall-clock
time of the holder's last refresh. A lock is stale when its heartbeat is older than the timeout (DEFAULT_TIMEOUT,
60 s); staleness never looks at a host name or a process id, because the two programs share neither.

Nothing here starts a thread. The holder calls `Lock.refresh()` every REFRESH_INTERVAL (10 s) from its own timer
(a QTimer on the desktop, an asyncio task or `run_in_executor` call on the server). A refresh that returns False
means the lock was taken over or removed: stop editing. Calls are plain blocking file I/O on one small file.

The file is created atomically (a hard link from a private temp file, so a reader never sees a half-written
lock; `O_EXCL` where links are not supported) and rewritten with `os.replace`. Taking a stale lock renames it
aside first, so two programs racing for the same stale lock cannot both win.

The lock file never enters a Version: `acquire` adds it to the Document's `.git/info/exclude` with plain file
I/O (git is not run here), so Documents made before the lock existed are covered the first time they are opened.
`ensure_excluded` is public for the places that make a repository afterwards.
"""
from __future__ import annotations

import json
import os
import time
import uuid

LOCK_NAME = ".freeshape-lock"
DEFAULT_TIMEOUT = 60.0
REFRESH_INTERVAL = 10.0
_EXCLUDE_LINE = "/" + LOCK_NAME + "*"       # the lock and its private temp files
_MAX_BYTES = 4096


class LockHeld(Exception):
    """acquire() found a live lock held by someone else; `holder` is that `Holder`."""

    def __init__(self, folder, holder):
        self.folder = folder
        self.holder = holder
        super().__init__(f"The Document {folder} is being edited by {holder.program} ({holder.instance[:8]})")


class Holder:
    """Who a lock file names. `heartbeat` is None for a lock file that cannot be read (see `holder`)."""

    def __init__(self, program, instance, heartbeat, stale=False):
        self.program = program
        self.instance = instance
        self.heartbeat = heartbeat
        self.stale = stale

    def __repr__(self):
        return f"Holder({self.program!r}, {self.instance[:8]!r}, heartbeat={self.heartbeat}, stale={self.stale})"


def lock_path(folder):
    return os.path.join(folder, LOCK_NAME)


def ensure_excluded(folder):
    """Add the lock file to `<folder>/.git/info/exclude` (once). Does nothing when the folder has no `.git`
    directory yet, when `.git` is a link or a file, or when the file cannot be written: a lock must still work."""
    git = os.path.join(folder, ".git")
    try:
        if os.path.islink(git) or not os.path.isdir(git):
            return False
        info = os.path.join(git, "info")
        if os.path.islink(info):
            return False
        os.makedirs(info, exist_ok=True)
        path = os.path.join(info, "exclude")
        if os.path.islink(path):
            return False
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except FileNotFoundError:
            text = ""
        if _EXCLUDE_LINE in text.splitlines():
            return True
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(("" if not text or text.endswith("\n") else "\n") + _EXCLUDE_LINE + "\n")
        return True
    except OSError:
        return False


def _payload(program, instance, now):
    return json.dumps({"program": program, "instance": instance, "heartbeat": now}).encode("utf-8")


def _read(folder):
    """(program, instance, heartbeat, raw) of the lock file, or None when there is none. An unreadable or
    malformed file gives instance "" and a heartbeat from its modification time, so it expires like any lock."""
    path = lock_path(folder)
    try:
        if os.path.islink(path):
            return "unknown", "", 0.0, b""
        with open(path, "rb") as fh:
            raw = fh.read(_MAX_BYTES)
        mtime = os.path.getmtime(path)
    except FileNotFoundError:
        return None
    except OSError:
        return "unknown", "", 0.0, b""
    try:
        data = json.loads(raw.decode("utf-8"))
        program, instance, beat = data["program"], data["instance"], float(data["heartbeat"])
        if not isinstance(program, str) or not isinstance(instance, str) or not instance:
            raise ValueError
    except (ValueError, KeyError, TypeError, AttributeError):
        return "unknown", "", mtime, raw
    return program, instance, beat, raw


def holder(folder, timeout=DEFAULT_TIMEOUT, now=None):
    """The `Holder` of a Document's lock, or None when it is not locked. `stale` is judged by the heartbeat age
    alone."""
    got = _read(folder)
    if got is None:
        return None
    program, instance, beat, _raw = got
    now = time.time() if now is None else now
    return Holder(program, instance, beat, stale=(now - beat) > timeout)


def _tmp_path(folder, instance):
    return os.path.join(folder, f"{LOCK_NAME}.{instance}.tmp")


def _create(folder, data, instance):
    """Atomically make the lock file with `data`; False when one already exists."""
    tmp = _tmp_path(folder, instance)
    with open(tmp, "wb") as fh:
        fh.write(data)
    try:
        try:
            os.link(tmp, lock_path(folder))
            return True
        except FileExistsError:
            return False
        except OSError:                 # no hard links on this file system: O_EXCL, then write
            try:
                fd = os.open(lock_path(folder), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                return False
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            return True
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _replace(folder, data, instance):
    tmp = _tmp_path(folder, instance)
    with open(tmp, "wb") as fh:
        fh.write(data)
    try:
        os.replace(tmp, lock_path(folder))
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Lock:
    """A lock this program holds. Make one with `acquire` or `take_over`."""

    def __init__(self, folder, program, instance, timeout=DEFAULT_TIMEOUT):
        self.folder = folder
        self.program = program
        self.instance = instance
        self.timeout = timeout

    def still_mine(self):
        """Whether the lock file still names this instance (not taken over, not removed)."""
        got = _read(self.folder)
        return got is not None and got[1] == self.instance

    def refresh(self, now=None):
        """Write a new heartbeat. Returns False, writing nothing, when the lock is no longer ours."""
        if not self.still_mine():
            return False
        _replace(self.folder, _payload(self.program, self.instance, time.time() if now is None else now),
                 self.instance)
        return True

    def release(self):
        """Remove the lock if it is ours. Returns whether it was removed; a lock someone else holds stays."""
        if not self.still_mine():
            return False
        try:
            os.unlink(lock_path(self.folder))
        except FileNotFoundError:
            return False
        return True

    def __repr__(self):
        return f"Lock({self.folder!r}, {self.program!r}, {self.instance[:8]!r})"


def acquire(folder, program, timeout=DEFAULT_TIMEOUT, now=None):
    """Take the Document's lock for `program`. Returns a `Lock`; raises `LockHeld` (naming the holder) when
    another holder's heartbeat is younger than `timeout`. A stale lock is replaced."""
    ensure_excluded(folder)
    instance = uuid.uuid4().hex
    now = time.time() if now is None else now
    data = _payload(program, instance, now)
    for _ in range(5):
        if _create(folder, data, instance):
            return Lock(folder, program, instance, timeout)
        got = _read(folder)
        if got is None:
            continue                                  # removed between our two looks: try again
        _program, _instance, beat, raw = got
        if (now - beat) <= timeout:
            raise LockHeld(folder, Holder(_program, _instance, beat, stale=False))
        _break_stale(folder, raw, instance)
    raise LockHeld(folder, holder(folder, timeout, now) or Holder("unknown", "", None))


def _break_stale(folder, seen_raw, instance):
    """Move the stale lock we read out of the way. If what we moved is not what we read (someone made a fresh
    lock in between), put it back."""
    aside = os.path.join(folder, f"{LOCK_NAME}.{instance}.stale")
    try:
        os.rename(lock_path(folder), aside)
    except OSError:
        return
    try:
        with open(aside, "rb") as fh:
            moved = fh.read(_MAX_BYTES)
        if moved != seen_raw:
            try:
                os.link(aside, lock_path(folder))     # best effort: fails if yet another lock appeared
            except OSError:
                pass
    except OSError:
        pass
    finally:
        try:
            os.unlink(aside)
        except OSError:
            pass


def take_over(folder, program, timeout=DEFAULT_TIMEOUT, now=None):
    """Replace whoever holds the lock (or no one) with this program. The old holder's `still_mine()` turns False
    and its next `refresh()` returns False. Returns the new `Lock`."""
    ensure_excluded(folder)
    instance = uuid.uuid4().hex
    _replace(folder, _payload(program, instance, time.time() if now is None else now), instance)
    return Lock(folder, program, instance, timeout)
