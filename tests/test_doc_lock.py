"""I2.1: the Document lock (freeshape/doc_lock.py). A second holder is refused and named; release by a non-holder
and take_over; two separate processes sharing only the folder; staleness by heartbeat alone; the lock file
never enters a Version and survives a restore or a Workspace switch."""
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

from freeshape import doc_folder as DF
from freeshape import doc_lock as L
from freeshape import doc_registry as R
from freeshape import gitstore as G

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

# a child process: load doc_lock by path (nothing else of freeshape), take the lock, refresh until killed
_CHILD = """
import importlib.util, sys, time
spec = importlib.util.spec_from_file_location("doc_lock", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
lock = m.acquire(sys.argv[2], "child", timeout=float(sys.argv[3]))
print("held", flush=True)
while True:
    lock.refresh()
    time.sleep(0.1)
"""


def _folder(tmp_path):
    d = tmp_path / "Doc"
    d.mkdir()
    return str(d)


def _read_lock(folder):
    with open(L.lock_path(folder), "rb") as fh:
        return fh.read()


def test_second_acquire_fails_and_names_the_first_holder(tmp_path):
    folder = _folder(tmp_path)
    first = L.acquire(folder, "desktop")
    with pytest.raises(L.LockHeld) as e:
        L.acquire(folder, "server")
    assert e.value.holder.program == "desktop" and e.value.holder.instance == first.instance
    assert "desktop" in str(e.value)
    assert first.still_mine()
    h = L.holder(folder)
    assert (h.program, h.instance, h.stale) == ("desktop", first.instance, False)
    assert L.holder(str(tmp_path)) is None


def test_release_by_a_non_holder_leaves_the_lock_and_take_over_changes_the_holder(tmp_path):
    folder = _folder(tmp_path)
    old = L.acquire(folder, "desktop")
    other = L.Lock(folder, "server", "f" * 32)
    assert other.release() is False
    assert os.path.exists(L.lock_path(folder)) and old.still_mine()
    new = L.take_over(folder, "server")
    assert new.instance != old.instance
    assert L.holder(folder).instance == new.instance and L.holder(folder).program == "server"
    assert old.still_mine() is False and new.still_mine() is True
    before = _read_lock(folder)
    assert old.refresh() is False and old.release() is False
    assert _read_lock(folder) == before
    assert new.refresh() is True
    assert new.release() is True and L.holder(folder) is None


def test_refresh_moves_the_heartbeat_and_a_stale_lock_can_be_acquired(tmp_path):
    folder = _folder(tmp_path)
    lock = L.acquire(folder, "desktop", now=1000.0)
    assert L.holder(folder, timeout=60, now=1059.0).stale is False
    assert L.holder(folder, timeout=60, now=1061.0).stale is True
    with pytest.raises(L.LockHeld):
        L.acquire(folder, "server", now=1059.0)
    assert lock.refresh(now=1060.0) is True
    assert L.holder(folder, timeout=60, now=1061.0).stale is False
    taker = L.acquire(folder, "server", now=1200.0)
    assert taker.still_mine() and not lock.still_mine()
    assert sorted(os.listdir(folder)) == [L.LOCK_NAME]      # no temp or aside files left behind


def test_a_lock_naming_another_host_and_a_live_looking_pid_is_judged_by_heartbeat_only(tmp_path):
    folder = _folder(tmp_path)
    body = {"program": "server", "instance": "a" * 32, "heartbeat": 1000.0,
            "host": "some-other-container", "hostname": "elsewhere", "pid": os.getpid()}
    with open(L.lock_path(folder), "w") as fh:
        json.dump(body, fh)
    assert L.holder(folder, timeout=60, now=1030.0).stale is False
    with pytest.raises(L.LockHeld):
        L.acquire(folder, "desktop", now=1030.0)
    assert L.holder(folder, timeout=60, now=1100.0).stale is True
    lock = L.acquire(folder, "desktop", now=1100.0)
    assert lock.still_mine()


def test_an_unreadable_lock_file_expires_by_its_modification_time(tmp_path):
    folder = _folder(tmp_path)
    with open(L.lock_path(folder), "w") as fh:
        fh.write("not json")
    h = L.holder(folder, timeout=60)
    assert h.stale is False and h.program == "unknown"
    old = time.time() - 600
    os.utime(L.lock_path(folder), (old, old))
    assert L.holder(folder, timeout=60).stale is True
    assert L.acquire(folder, "desktop").still_mine()


def test_two_processes_sharing_only_the_folder(tmp_path):
    folder = _folder(tmp_path)
    src = os.path.join(ROOT, "freeshape", "doc_lock.py")
    proc = subprocess.Popen([sys.executable, "-c", _CHILD, src, folder, "1"], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "held"      # the child holds the lock and keeps refreshing
        for _ in range(3):
            time.sleep(0.4)
            with pytest.raises(L.LockHeld) as e:
                L.acquire(folder, "parent", timeout=1.0)
            assert e.value.holder.program == "child"
    finally:
        proc.kill()
        proc.wait()
    deadline = time.time() + 30
    lock = None
    while lock is None and time.time() < deadline:
        try:
            lock = L.acquire(folder, "parent", timeout=1.0)
        except L.LockHeld:
            time.sleep(0.2)
    assert lock is not None and lock.still_mine()


def _plain_document(tmp_path):
    reg = R.DocRegistry(str(tmp_path / "data" / "documents.json"))
    doc = DF.FolderDocument.create(str(tmp_path / "Doc"), registry=reg)
    doc.add_tab("studio", "Plate")
    # a Document made before the lock existed: no exclude rule at all
    exclude = os.path.join(doc.path, ".git", "info", "exclude")
    if os.path.exists(exclude):
        os.remove(exclude)
    return doc


def _git_lines(doc, *args):
    return G.run(list(args), doc.path).stdout.splitlines()


@needs_git
def test_the_lock_file_never_enters_a_version(tmp_path):
    doc = _plain_document(tmp_path)
    assert not os.path.exists(os.path.join(doc.path, ".git", "info", "exclude"))
    L.acquire(doc.path, "desktop")
    assert os.path.exists(L.lock_path(doc.path))
    doc.create_version("v1", "first")
    assert L.LOCK_NAME not in "\n".join(_git_lines(doc, "ls-tree", "-r", "--name-only", "HEAD"))
    assert _git_lines(doc, "status", "--porcelain", "--untracked-files=all") == []
    assert _git_lines(doc, "ls-files", "--others", "--exclude-standard") == []
    # a second acquire (a later open) does not repeat the rule
    L.take_over(doc.path, "desktop")
    with open(os.path.join(doc.path, ".git", "info", "exclude")) as fh:
        assert fh.read().count(L.LOCK_NAME) == 1


@needs_git
def test_restoring_a_version_and_switching_workspace_leave_the_lock_alone(tmp_path):
    doc = _plain_document(tmp_path)
    lock = L.acquire(doc.path, "desktop")
    doc.create_version("v1", "first")
    doc.add_tab("studio", "Second")
    doc.save()
    doc.create_version("v2", "second")
    before = _read_lock(doc.path)
    mtime = os.stat(L.lock_path(doc.path)).st_mtime_ns
    doc.create_workspace("from-v1", version="v1")             # restores v1's files into the work tree
    assert doc.current_workspace() == "from-v1"
    assert [t.name for t in doc.tabs] == ["Plate"]
    assert _read_lock(doc.path) == before and os.stat(L.lock_path(doc.path)).st_mtime_ns == mtime
    doc.switch_workspace(G.DEFAULT_BRANCH)
    assert [t.name for t in doc.tabs] == ["Plate", "Second"]
    assert _read_lock(doc.path) == before and lock.still_mine()
    view = doc.open_version("v1")
    try:
        assert not os.path.exists(os.path.join(view.path, L.LOCK_NAME))
    finally:
        view.close()
    assert _read_lock(doc.path) == before
    assert _git_lines(doc, "status", "--porcelain", "--untracked-files=all") == []


def test_a_document_without_a_repository_yet_is_locked_and_covered_once_it_gets_one(tmp_path):
    folder = _folder(tmp_path)
    lock = L.acquire(folder, "desktop")                       # no .git: nothing to exclude yet, no error
    assert lock.still_mine() and not os.path.exists(os.path.join(folder, ".git"))
    os.makedirs(os.path.join(folder, ".git"))
    assert L.ensure_excluded(folder) is True and L.ensure_excluded(folder) is True
    with open(os.path.join(folder, ".git", "info", "exclude")) as fh:
        assert fh.read().splitlines().count("/" + L.LOCK_NAME + "*") == 1
