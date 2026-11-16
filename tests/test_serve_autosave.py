"""I2.6: the server's own autosave, recovery and idle close (freeshape/autosave.py, freeshape/serve.py).

No test waits for a timer to do its job: the tests drive `autosave_now()`, `expire_idle_now()` and `refresh_locks_now()`.
One test per timer proves the real timer fires (the autosave interval, the burst timer, the idle timer); each polls with
a generous deadline and sets its own short time on the server under test. The tests that kill a server run it as a
separate process; its lock goes stale through `--lock-timeout`, never by waiting 60 s. Each test makes its own folders
and servers, so it passes alone."""
import asyncio
import hashlib
import http.client
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time

import aiohttp
import pytest

from freeshape import autosave as AS
from freeshape import doc_lock
from freeshape import doc_registry as R
from freeshape import kernel as K
from freeshape import serve as SV
from freeshape.engine import process as P
from freeshape.engine import wire
from freeshape.session import DocumentSession

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRACKET = os.path.join(ROOT, "examples", "documents", "bracket")
DOC_ID = "bracket-0001"
ORIGINAL = {"op": "set_param", "feature": "F5", "key": "diameter", "value": 6.5}
EDIT_A = {"op": "set_param", "feature": "F5", "key": "diameter", "value": 7.0}      # what the browser client does
EDIT_B = {"op": "set_param", "feature": "F5", "key": "diameter", "value": 9.0}      # what the second holder saves
CLIENT = "client-aaaa1111"
DEADLINE = 120.0                                                                   # polling deadline, never a sleep


class _Work:
    """A Documents root, a data directory and the example bracket Document registered in them."""

    def __init__(self, tmp_path):
        self.root = str(tmp_path / "Documents")
        self.data = str(tmp_path / "data")
        os.makedirs(self.root)
        self.folder = os.path.realpath(os.path.join(self.root, "Bracket"))
        shutil.copytree(BRACKET, self.folder)
        with open(os.path.join(self.folder, "document.json")) as fh:
            manifest = json.load(fh)
        manifest["id"] = DOC_ID
        with open(os.path.join(self.folder, "document.json"), "w") as fh:
            json.dump(manifest, fh)
        self.registry = R.DocRegistry(os.path.join(self.data, R.REGISTRY_NAME))
        self.registry.register_created(DOC_ID, self.folder)

    @property
    def autosave_root(self):
        return os.path.join(self.data, AS.DIR_NAME)

    def autosaves(self):
        """The instance ids that have an autosave folder."""
        try:
            return sorted(os.listdir(self.autosave_root))
        except OSError:
            return []

    def tab_file(self):
        with open(os.path.join(self.folder, "bracket.fshape"), "rb") as fh:
            return fh.read()


class _Call:
    """HTTP calls to a server on `port`."""

    def __init__(self, port):
        self.port = port

    def request(self, method, path, body=None, headers=None):
        h = {"Host": f"127.0.0.1:{self.port}"}
        if method == "POST":
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        data = json.dumps(body if body is not None else {}).encode() if method == "POST" else None
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
        try:
            c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for k, v in h.items():
                c.putheader(k, v)
            if data is not None:
                c.putheader("Content-Length", str(len(data)))
            c.endheaders(data)
            r = c.getresponse()
            payload = r.read()
            if r.getheader("Content-Type", "").startswith("application/json"):
                payload = json.loads(payload)
            return r.status, payload
        finally:
            c.close()

    def ok(self, method, path, body=None):
        status, payload = self.request(method, path, body)
        assert status == 200, (method, path, status, payload)
        return payload

    def open(self):
        return self.ok("POST", f"/documents/{DOC_ID}/open")

    def edit(self, edit, tab="T1"):
        return self.request("POST", f"/documents/{DOC_ID}/tabs/{tab}/edit", edit)

    def row(self):
        (row,) = [r for r in self.ok("GET", "/documents")["documents"] if r["id"] == DOC_ID]
        return row

    def volumes(self, tab="T1"):
        status, body = self.request("GET", f"/documents/{DOC_ID}/tabs/{tab}/outputs")
        assert status == 200
        return _volumes(wire.from_wire(body))


def _volumes(out):
    parts = out.parts.values() if isinstance(out.parts, dict) else out.parts
    return sorted(round(K.volume(K.shape_from_brep(p.brep)), 4) for p in parts)


def _session_volumes(folder):
    with DocumentSession(folder, spares=0) as s:
        return _volumes(s.outputs("T1"))


class _InProc(_Call):
    """An in-process server over the work area's registry, with the autosave on."""

    def __init__(self, work, **kw):
        kw.setdefault("autosave_interval", 3600.0)
        kw.setdefault("autosave_burst", 3600.0)
        self.work = work
        self.server = SV.make_server(None, 0, registry=work.registry, root=work.root, data_dir=work.data, **kw)
        super().__init__(self.server.port)
        self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        assert self.server.wait_started(30)

    def close(self):
        try:
            SV.close_all(self.server, None)
        finally:
            P.SPARES.clear()


@pytest.fixture
def work(tmp_path):
    return _Work(tmp_path)


@pytest.fixture
def inproc(work):
    made = []

    def make(**kw):
        s = _InProc(work, **kw)
        made.append(s)
        return s
    try:
        yield make
    finally:
        for s in made:
            s.close()


def _env(tmp_path):
    return dict(os.environ, PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""),
                QT_QPA_PLATFORM="offscreen", FREESHAPE_PLUGIN_DIR=str(tmp_path / "plugins"))


class _Proc(_Call):
    """`freeshape serve` as a separate process over the work area."""

    def __init__(self, work, tmp_path, *extra):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "freeshape", "serve", work.root, "--port", "0", "--data-dir", work.data, *extra],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(tmp_path), env=_env(tmp_path))
        line = self.proc.stdout.readline()
        assert line.startswith(SV.READY_PREFIX + " port="), (line, self.proc.stderr.read() if self.proc.poll() else "")
        super().__init__(int(line.split()[1].split("=")[1]))

    def kill(self):
        """SIGKILL: nothing is saved, no lock released, no autosave written on the way out."""
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGKILL)
        self.proc.wait(60)

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(60)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        for f in (self.proc.stdout, self.proc.stderr):
            f.close()


@pytest.fixture
def procs(work, tmp_path):
    made = []

    def make(*extra):
        p = _Proc(work, tmp_path, *extra)
        made.append(p)
        return p
    try:
        yield make
    finally:
        for p in made:
            p.stop()


def _wait(pred, what, deadline=DEADLINE):
    """Poll `pred` until it is true; a deadline, never a fixed sleep."""
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        got = pred()
        if got:
            return got
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def _wait_info(work, what):
    """The first autosave's info once its manifest exists (the folder appears before it)."""
    def info():
        for inst in work.autosaves():
            try:
                got = AS.read_info(work.data, inst)
            except (OSError, ValueError, KeyError):
                continue
            if got is not None:
                return got
        return None
    return _wait(info, what)


def _instance(work):
    """The instance id in the Document's lock file."""
    h = doc_lock.holder(work.folder)
    assert h is not None
    return h.instance


def _manifest(work, instance):
    with open(os.path.join(work.autosave_root, instance, AS.MANIFEST)) as fh:
        return json.load(fh)


def _tree_hash(path):
    h = hashlib.sha256()
    for d, ds, fs in sorted(os.walk(path)):
        ds.sort()
        for f in sorted(fs):
            full = os.path.join(d, f)
            h.update(os.path.relpath(full, path).encode())
            with open(full, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()


async def _next(ws, kind, timeout=60):
    while True:
        msg = await asyncio.wait_for(ws.receive(), timeout)
        assert msg.type == aiohttp.WSMsgType.TEXT, msg
        data = json.loads(msg.data)
        if data.get("type") == kind:
            return data


# ---------------------------------------------------------------------------------------------- written, cleared
def test_an_edit_is_autosaved_under_the_holder_instance_and_a_save_clears_it(work, inproc):
    s = inproc()
    assert s.open()["autosave"] is None
    assert s.server.autosave_now() == {DOC_ID: False}                          # nothing to write yet
    assert s.edit(EDIT_A)[0] == 200
    assert s.server.autosave_now() == {DOC_ID: True}
    instance = _instance(work)
    assert work.autosaves() == [instance]
    m = _manifest(work, instance)
    assert m["document"] == DOC_ID and m["instance"] == instance and set(m["tabs"]) == {"T1"}
    assert abs(m["time"] - time.time()) < 600
    assert s.server.autosave_now() == {DOC_ID: False}                          # nothing changed since
    assert os.path.isdir(os.path.join(work.data, "autosave-server", instance))
    assert s.ok("POST", f"/documents/{DOC_ID}/save")["saved"] == ["T1"]
    assert work.autosaves() == []
    # a live autosave is the server's own: it is not offered back to it
    s.edit(EDIT_B)
    s.server.autosave_now()
    assert work.autosaves() == [instance] and s.open()["autosave"] is None and s.row()["autosave"] is None


def test_the_autosave_is_the_same_bytes_a_save_writes(work, inproc):
    s = inproc()
    s.open()
    s.edit(EDIT_A)
    s.server.autosave_now()
    instance = _instance(work)
    info = AS.read_info(work.data, instance)
    snap = AS.load(work.data, info)
    s.ok("POST", f"/documents/{DOC_ID}/save")
    assert snap["tabs"]["T1"] == work.tab_file()
    assert info.tabs["T1"]["base"] == hashlib.sha256(
        open(os.path.join(BRACKET, "bracket.fshape"), "rb").read()).hexdigest()


def test_the_interval_timer_writes_the_autosave(work, inproc):
    s = inproc(autosave_interval=0.2)
    s.open()
    s.edit(EDIT_A)
    assert _wait_info(work, "the interval autosave").document == DOC_ID


def test_the_burst_timer_writes_the_autosave_after_an_edit(work, inproc):
    s = inproc(autosave_burst=0.2)                                             # the interval is an hour
    s.open()
    assert work.autosaves() == []
    s.edit(EDIT_A)
    _wait_info(work, "the burst autosave")


def test_tab_adds_renames_and_deletes_are_autosaved_and_recovered(work, inproc):
    s = inproc()
    s.open()
    status, added = s.request("POST", f"/documents/{DOC_ID}/tabs", {"kind": "assembly", "name": "Frame"})
    assert status == 201
    new = added["tab"]["id"]
    assert s.request("POST", f"/documents/{DOC_ID}/tabs/T1/rename", {"name": "Plate"})[0] == 200
    s.edit(EDIT_A)
    assert s.server.autosave_now() == {DOC_ID: True}
    m = _manifest(work, _instance(work))
    assert set(m["tabs"]) == {"T1", new} and m["added"] == [new] and m["renamed"] == ["T1"]
    assert s.server.expire_idle_now() == [DOC_ID]                              # the lock goes, the autosave stays
    assert work.tab_file() == open(os.path.join(BRACKET, "bracket.fshape"), "rb").read()
    s.open()
    offer = s.row()["autosave"]
    assert offer["count"] == 1
    opened = s.open()["autosave"]
    assert {t["id"] for t in opened["tabs"]} == {"T1", new} and opened["moved_on"] == []
    recovered = s.ok("POST", f"/documents/{DOC_ID}/recover")
    assert set(recovered["recovered"]) == {"T1", new} and recovered["moved_on"] == []
    tabs = {t["id"]: t for t in recovered["tabs"]}
    assert tabs["T1"]["name"] == "Plate" and tabs[new]["name"] == "Frame" and tabs[new]["kind"] == "assembly"
    assert all(t["dirty"] for t in tabs.values())                              # recovered edits are unsaved
    assert s.volumes() != _session_volumes(work.folder)                        # the edit is in the session, not on disk
    assert s.ok("POST", f"/documents/{DOC_ID}/save")["saved"] == ["T1", new]
    with open(os.path.join(work.folder, "document.json")) as fh:
        saved = {t["id"]: t["name"] for t in json.load(fh)["tabs"]}
    assert saved == {"T1": "Plate", new: "Frame"}
    assert s.ok("GET", f"/documents/{DOC_ID}/tabs")["tabs"][1]["id"] == new
    # a deleted tab is recovered as deleted
    s.ok("POST", f"/documents/{DOC_ID}/save")
    assert s.request("DELETE", f"/documents/{DOC_ID}/tabs/{new}")[0] == 200
    s.server.autosave_now()
    s.server.expire_idle_now()
    again = s.open()["autosave"]
    assert again["deleted"] == [new]
    got = s.ok("POST", f"/documents/{DOC_ID}/recover")
    assert [t["id"] for t in got["tabs"]] == ["T1"]


# ---------------------------------------------------------------------------------------------- a killed server
def _killed_with_an_edit(work, tmp_path, procs):
    """Server A edits and is killed with its autosave written; returns the autosave's instance id."""
    a = procs("--autosave-interval", "0.2")
    a.open()
    assert a.edit(EDIT_A)[0] == 200
    instance = _wait(lambda: next(iter(work.autosaves()), None), "A's autosave")
    _wait(lambda: AS.read_info(work.data, instance) is not None, "A's complete autosave")
    a.kill()
    assert doc_lock.holder(work.folder).instance == instance                   # the lock is still there, fresh for now
    return instance


def _stale_server(work, procs, instance):
    """A new server whose lock timeout is short, started once the killed server's heartbeat is older than it."""
    short = 0.5
    _wait(lambda: doc_lock.holder(work.folder, timeout=short).stale, "the killed server's lock going stale")
    return procs("--lock-timeout", str(short))


def test_after_a_kill_the_new_servers_open_reports_the_autosave_and_recover_brings_the_edit_back(work, tmp_path, procs):
    original = _session_volumes(work.folder)
    instance = _killed_with_an_edit(work, tmp_path, procs)
    b = _stale_server(work, procs, instance)
    assert b.row()["holder"]["stale"] is True and b.row()["autosave"]["count"] == 1    # listed, not yet opened
    opened = b.open()
    assert opened["read_only"] is False
    assert opened["autosave"]["count"] == 1 and [t["id"] for t in opened["autosave"]["tabs"]] == ["T1"]
    assert opened["autosave"]["moved_on"] == [] and opened["autosave"]["writable"] is True
    # nothing happens without the call: the session shows the saved Document and the files are all still there
    assert b.volumes() == original and work.autosaves() == [instance]
    assert b.open()["autosave"]["count"] == 1
    recovered = b.ok("POST", f"/documents/{DOC_ID}/recover")
    assert recovered["recovered"] == ["T1"] and recovered["moved_on"] == []
    edited = b.volumes()
    assert edited != original
    assert work.tab_file() == open(os.path.join(BRACKET, "bracket.fshape"), "rb").read()    # not written to disk
    assert instance not in work.autosaves() and len(work.autosaves()) == 1                  # moved under B's lock
    assert work.autosaves() == [doc_lock.holder(work.folder).instance]
    assert b.open()["autosave"] is None
    b.ok("POST", f"/documents/{DOC_ID}/save")
    assert work.autosaves() == [] and _session_volumes(work.folder) == edited


def test_after_a_kill_discard_autosave_leaves_the_saved_state(work, tmp_path, procs):
    original = _session_volumes(work.folder)
    instance = _killed_with_an_edit(work, tmp_path, procs)
    b = _stale_server(work, procs, instance)
    assert b.open()["autosave"]["count"] == 1
    assert work.autosaves() == [instance]                                      # opening deleted nothing
    assert b.volumes() == original
    assert b.ok("POST", f"/documents/{DOC_ID}/discard-autosave") == {"discarded": 1}
    assert work.autosaves() == [] and b.open()["autosave"] is None and b.volumes() == original
    assert b.request("POST", f"/documents/{DOC_ID}/discard-autosave")[0] == 404
    assert b.request("POST", f"/documents/{DOC_ID}/recover")[0] == 404
    assert work.tab_file() == open(os.path.join(BRACKET, "bracket.fshape"), "rb").read()


def test_a_second_server_process_neither_lists_nor_deletes_a_live_autosave(work, tmp_path, procs):
    a = procs("--autosave-interval", "0.2")
    a.open()
    a.edit(EDIT_A)
    instance = _wait(lambda: next(iter(work.autosaves()), None), "A's autosave")
    _wait(lambda: AS.read_info(work.data, instance) is not None, "A's complete autosave")
    before = _tree_hash(work.autosave_root)
    b = procs()                                                                # the default 60 s timeout: A's lock is fresh
    assert doc_lock.holder(work.folder).instance == instance and not doc_lock.holder(work.folder).stale
    assert b.row()["autosave"] is None and b.row()["holder"]["program"] == "browser"
    opened = b.open()
    assert opened["read_only"] is True and opened["autosave"] is None
    for call in ("recover", "discard-autosave"):
        assert b.request("POST", f"/documents/{DOC_ID}/{call}")[0] == 423      # read-only: it may not touch anything
    b.ok("POST", f"/documents/{DOC_ID}/close")
    assert work.autosaves() == [instance]
    # A keeps rewriting its own autosave; B never changed it: compare what B could have touched, the other files
    assert set(os.listdir(os.path.join(work.autosave_root, instance))) == {AS.MANIFEST, AS.BLOBS}
    assert _tree_hash(work.autosave_root) == before                            # B changed nothing
    a.ok("POST", f"/documents/{DOC_ID}/save")
    assert work.autosaves() == []                                              # A's own save clears A's own autosave


# ---------------------------------------------------------------------------------------------- idle close
def test_idle_close_autosaves_releases_the_lock_and_a_second_holder_cannot_lose_the_first_edit(work, inproc, tmp_path):
    s = inproc(client_ttl=3600.0)
    original = _session_volumes(work.folder)
    s.open()
    assert s.edit(EDIT_A)[0] == 200
    instance = _instance(work)

    async def connected():
        async with aiohttp.ClientSession() as http, \
                http.ws_connect(f"{s.base}/ws?client={CLIENT}&document={DOC_ID}") as ws:
            assert (await _next(ws, "hello"))["document"]["id"] == DOC_ID
            assert await asyncio.get_running_loop().run_in_executor(None, s.server.expire_idle_now) == []
    asyncio.run(asyncio.wait_for(connected(), 120))                            # a connected client keeps it open
    assert s.row()["open"] is True
    # the client is gone: the expiry time has run out
    assert s.server.expire_idle_now() == [DOC_ID]
    assert s.row()["open"] is False and DOC_ID not in s.server.docs
    assert doc_lock.holder(work.folder) is None
    assert work.autosaves() == [instance] and set(_manifest(work, instance)["tabs"]) == {"T1"}
    assert s.row()["autosave"]["count"] == 1                                   # no lock: it is recoverable
    assert s.server.expire_idle_now() == []

    async def comes_back():
        async with aiohttp.ClientSession() as http, \
                http.ws_connect(f"{s.base}/ws?client={CLIENT}&document={DOC_ID}") as ws:
            closed = await _next(ws, "closed")
            assert closed["document"] == DOC_ID and closed["reason"] == "idle"
    asyncio.run(asyncio.wait_for(comes_back(), 120))
    # a second holder takes the lock and saves a different edit
    second = doc_lock.acquire(work.folder, "desktop")
    with DocumentSession(work.folder, spares=0) as other:
        other.submit_edit("T1", EDIT_B)
        other.save()
    saved_by_second = work.tab_file()
    saved_volumes = _session_volumes(work.folder)
    assert saved_volumes != original
    # the first client's next edit is refused, and its reopen is read-only while the second holds the lock
    assert s.edit(EDIT_A)[0] == 409
    reopened = s.open()
    assert reopened["read_only"] is True and reopened["holder"]["program"] == "desktop"
    assert reopened["autosave"]["count"] == 1 and reopened["autosave"]["moved_on"] == ["T1"]
    assert reopened["autosave"]["writable"] is False
    assert s.request("POST", f"/documents/{DOC_ID}/recover")[0] == 423
    assert s.request("POST", f"/documents/{DOC_ID}/discard-autosave")[0] == 423
    assert s.edit(EDIT_A)[0] == 423
    s.ok("POST", f"/documents/{DOC_ID}/close")
    assert doc_lock.holder(work.folder).instance == second.instance            # never touched their lock
    # once the lock is free the reopen re-acquires it, and the autosave is still offered, saying the Document moved on
    second.release()
    reacquired = s.open()
    assert reacquired["read_only"] is False and reacquired["autosave"]["moved_on"] == ["T1"]
    assert s.volumes() == saved_volumes                                       # the session shows the second holder's save
    got = s.ok("POST", f"/documents/{DOC_ID}/recover")
    assert got["recovered"] == ["T1"] and got["moved_on"] == ["T1"]
    assert s.volumes() not in (saved_volumes, original)                       # the first client's edit is back, unsaved ...
    assert work.tab_file() == saved_by_second and _session_volumes(work.folder) == saved_volumes   # ... the disk is intact


def test_the_idle_timer_closes_a_document_nobody_is_connected_to(work, inproc):
    s = inproc(client_ttl=0.3)
    s.open()
    s.edit(EDIT_A)
    instance = _instance(work)
    _wait(lambda: not s.row()["open"], "the idle close")
    assert doc_lock.holder(work.folder) is None and work.autosaves() == [instance]


def test_a_socket_on_the_document_stops_the_idle_timer_and_its_leaving_starts_it(work, inproc):
    s = inproc(client_ttl=0.3)
    s.open()

    async def go():
        async with aiohttp.ClientSession() as http, \
                http.ws_connect(f"{s.base}/ws?client={CLIENT}&document={DOC_ID}") as ws:
            await _next(ws, "hello")
            assert s.server.docs[DOC_ID].idle is None                          # a client is connected: no timer
    asyncio.run(asyncio.wait_for(go(), 120))
    _wait(lambda: not s.row()["open"], "the idle close after the socket went")


def test_a_close_keeps_the_unsaved_edit_as_an_autosave_unless_it_says_discard(work, inproc):
    s = inproc()
    s.open()
    s.edit(EDIT_A)
    instance = _instance(work)
    assert s.ok("POST", f"/documents/{DOC_ID}/close") == {"closed": True}      # nothing was autosaved yet: close does it
    assert work.autosaves() == [instance] and doc_lock.holder(work.folder) is None
    assert s.open()["autosave"]["count"] == 1
    s.ok("POST", f"/documents/{DOC_ID}/discard-autosave")
    s.edit(EDIT_B)
    assert s.request("POST", f"/documents/{DOC_ID}/close", {"discard": True})[0] == 200
    assert work.autosaves() == []                                              # Discard in the leave dialog


def test_a_graceful_stop_keeps_the_unsaved_edit(work, tmp_path, procs):
    a = procs("--autosave-interval", "3600")
    a.open()
    a.edit(EDIT_A)
    assert work.autosaves() == []                                              # the timer is an hour: only the stop wrote
    a.proc.terminate()
    assert a.proc.wait(120) == 0
    (instance,) = work.autosaves()
    assert AS.read_info(work.data, instance).document == DOC_ID and doc_lock.holder(work.folder) is None


def test_a_lock_taken_over_autosaves_once_and_edits_stop(work, inproc):
    s = inproc()
    s.open()
    s.edit(EDIT_A)
    instance = _instance(work)
    doc_lock.take_over(work.folder, "desktop")
    assert s.server.refresh_locks_now() == {DOC_ID: False}
    assert work.autosaves() == [instance]                                      # written when the lock was lost
    assert s.edit(EDIT_B)[0] == 423 and s.server.autosave_now() == {DOC_ID: False}
    assert s.row()["autosave"]["count"] == 1                                   # its holder's lock is gone: recoverable


# ---------------------------------------------------------------------------------------------- the storage itself
def _state(tab=b'{"a": 1}', base=None, blobs=None):
    return {"document": "doc-1", "name": "Doc", "entries": [{"id": "T1", "kind": "studio", "file": "a.fshape", "name": "A"}],
            "added": [], "renamed": [], "deleted": [], "tabs": {"T1": (tab, base)}, "blobs": blobs or {},
            "manifest_base": None}


def test_a_write_is_atomic_a_half_written_manifest_is_never_read_as_an_autosave(tmp_path, monkeypatch):
    inst = "a" * 32
    AS.write(str(tmp_path), inst, _state(b'{"v": 1}'))
    first = AS.read_info(str(tmp_path), inst)
    assert AS.load(str(tmp_path), first)["tabs"]["T1"] == b'{"v": 1}'
    real = os.replace

    def crash(src, dst, *a, **k):
        if str(dst).endswith(AS.MANIFEST):
            with open(str(src), "ab") as fh:                                   # a crash in the middle of the manifest
                fh.truncate(5)
            raise OSError("power cut")
        return real(src, dst, *a, **k)
    monkeypatch.setattr(os, "replace", crash)
    with pytest.raises(OSError):
        AS.write(str(tmp_path), inst, _state(b'{"v": 2}'))
    monkeypatch.undo()
    again = AS.read_info(str(tmp_path), inst)
    assert again is not None and again.time == first.time                      # the old autosave, whole
    assert AS.load(str(tmp_path), again)["tabs"]["T1"] == b'{"v": 1}'
    # a first write that crashed leaves no autosave at all
    other = "b" * 32
    monkeypatch.setattr(os, "replace", crash)
    with pytest.raises(OSError):
        AS.write(str(tmp_path), other, _state())
    monkeypatch.undo()
    assert AS.read_info(str(tmp_path), other) is None and [i.instance for i in AS.scan(str(tmp_path))] == [inst]


def test_a_damaged_or_foreign_manifest_is_not_an_autosave_and_is_left_alone(tmp_path):
    data = str(tmp_path)
    inst = "c" * 32
    AS.write(data, inst, _state())
    path = os.path.join(data, AS.DIR_NAME, inst, AS.MANIFEST)
    good = open(path, "rb").read()
    for bad in (b"", b"{", b"[]", good.replace(b'"format": 1', b'"format": 2'), good.replace(inst.encode(), b"d" * 32),
                good.replace(b'"document": "doc-1"', b'"document": "../x"'),
                good.replace(b'"id": "T1"', b'"id": "../T1"')):
        with open(path, "wb") as fh:
            fh.write(bad)
        assert AS.read_info(data, inst) is None and AS.scan(data) == []
        assert os.path.exists(path)                                            # nothing is deleted for being unreadable
    with open(path, "wb") as fh:
        fh.write(good)
    info = AS.read_info(data, inst)
    blob = os.path.join(data, AS.DIR_NAME, inst, AS.BLOBS, info.tabs["T1"]["sha"])
    with open(blob, "wb") as fh:
        fh.write(b"tampered")
    with pytest.raises(AS.AutosaveError):
        AS.load(data, info)


def test_a_path_comes_from_a_valid_instance_id_only(tmp_path):
    for bad in ("../x", "..", "", "a" * 31, "A" * 32, "a" * 33, "a/" + "a" * 30, "a" * 32 + "/", "x" * 32, None, 5):
        with pytest.raises(AS.AutosaveError):
            AS.folder(str(tmp_path), bad)
        with pytest.raises(AS.AutosaveError):
            AS.remove(str(tmp_path), bad)
        with pytest.raises(AS.AutosaveError):
            AS.write(str(tmp_path), bad, _state())
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("x")
    os.makedirs(tmp_path / AS.DIR_NAME)
    os.symlink(str(outside), str(tmp_path / AS.DIR_NAME / ("e" * 32)))         # a link in place of an autosave folder
    assert AS.remove(str(tmp_path), "e" * 32) is True and (outside / "keep").exists()
    assert AS.scan(str(tmp_path)) == []


def test_ownership_follows_the_lock_a_fresh_holder_is_live_anything_else_is_recoverable(tmp_path):
    inst = "f" * 32
    info = AS.write(str(tmp_path), inst, _state())
    now = time.time()
    fresh = doc_lock.Holder("browser", inst, now, stale=False)
    stale = doc_lock.Holder("browser", inst, now - 999, stale=True)
    other = doc_lock.Holder("desktop", "9" * 32, now, stale=False)
    assert AS.recoverable([info], fresh) == []                                 # its holder still holds a fresh lock
    assert AS.recoverable([info], stale) == [info]
    assert AS.recoverable([info], other) == [info]                             # the lock is someone else's now
    assert AS.recoverable([info], None) == [info]                              # the lock is gone
    assert AS.recoverable([info], stale, own=inst) == []                       # the asking server's own is not offered


def test_hostile_document_ids_on_the_new_routes_are_404_and_touch_nothing(work, inproc):
    s = inproc()
    s.open()
    s.edit(EDIT_A)
    s.server.autosave_now()
    before = _tree_hash(work.data)
    for ident in ("..", "%2e%2e", "..%2f..%2fetc", "a%20b", "x" * 65, "nope"):
        for tail in ("recover", "discard-autosave"):
            assert s.request("POST", f"/documents/{ident}/{tail}", {})[0] == 404, (ident, tail)
        assert s.request("GET", f"/documents/{ident}/recover")[0] in (404, 405)
    assert s.request("GET", f"/documents/{DOC_ID}/recover")[0] == 405
    assert s.request("POST", f"/documents/{DOC_ID}/recover", {}, {"Origin": "https://evil.example"})[0] == 403
    assert s.request("POST", f"/documents/{DOC_ID}/discard-autosave", {}, {"Host": "evil.example"})[0] == 421
    assert _tree_hash(work.data) == before


def test_the_autosave_module_and_the_serve_path_import_no_qt():
    code = ("import sys; import freeshape.autosave, freeshape.serve, freeshape.session; "
            "bad = [m for m in sys.modules if m.split('.')[0] in ('PySide6', 'PyQt5', 'PyQt6', 'shiboken6')]; "
            "assert not bad, bad")
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True, timeout=120,
                   env=dict(os.environ, PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", "")))
