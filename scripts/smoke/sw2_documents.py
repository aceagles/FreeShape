"""Documents, tabs and the lock over `python -m freeshape serve` (slice S2, gate I2.7): no Qt, no network beyond loopback.

Part of the cumulative smoke gate (see s00_baseline.py for the convention; style as sw1_serve_live.py). Every server
is a separate process in its own session over a Documents root and a registry in a temporary data directory; nothing
touches a real user folder. One line per step, ending `ok`; a failed step prints why and the script exits 1 with no
`SW2 OK`. Every server is stopped (SIGTERM, then SIGKILL of its group) on success and on failure.

Steps, one clause of the slice claim each:
  create a Document over HTTP, add a Part Studio, save, close; `doc_folder` opens the folder and lists both tabs
  the lock held by a second holder: an edit answers 423 naming it; take-over answers 200 and the edit then works, and
    the first holder's `still_mine()` is false
  two servers on one Root: the second opens the first's Document read-only and neither lists nor touches its autosave;
    after the first is killed and its heartbeat is older than the timeout, a new server opens it for editing, offers
    the autosave of the edit the first left unsaved, and recovers it (the Document on disk is only written by a save)
  serve on a registry whose Documents are not under its mount exits 1 with the mount message

Nothing sleeps for a fixed time: the waits poll with a deadline, and the lock timeout and autosave interval of the
servers that need a stale lock or a quick autosave are short.

    both fs_py scripts/smoke/sw2_documents.py
"""
import http.client
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="sw2_documents_")
os.environ["FREESHAPE_DATA_DIR"] = os.path.join(TMP, "appdata")     # nothing may touch a real user folder
os.environ["XDG_DATA_HOME"] = os.path.join(TMP, "xdg")
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["FREESHAPE_PLUGIN_DIR"] = os.path.join(TMP, "plugins")
for _d in ("appdata", "xdg", "home", "plugins"):
    os.makedirs(os.path.join(TMP, _d), exist_ok=True)

from freeshape import autosave as AS  # noqa: E402
from freeshape import doc_folder as DF  # noqa: E402
from freeshape import doc_lock  # noqa: E402
from freeshape import doc_registry as R  # noqa: E402

READY_TIMEOUT = 120
STOP_TIMEOUT = 60
HTTP_TIMEOUT = 120
DEADLINE = 120.0
EDIT = {"op": "set_param", "feature": "F5", "key": "diameter", "value": 7.0}
BRACKET = os.path.join(ROOT, "examples", "documents", "bracket")


class Fail(Exception):
    pass


def check(cond, msg):
    if not cond:
        raise Fail(msg)


STEPS = []
SERVERS = []         # every Server started, so the cleanup reaches them all


def step(name):
    """Run a step function; print `name ... ok`, or the reason and exit 1 (the servers are stopped by the caller)."""
    def deco(fn):
        try:
            fn()
        except BaseException as e:                   # noqa: BLE001 -- any failure fails the gate (asserts included)
            print(f"{name} ... FAILED: {e!r}", flush=True)
            if not isinstance(e, Fail):
                traceback.print_exc()
            raise SystemExit(1)
        STEPS.append(name)
        print(f"{name} ... ok", flush=True)
        return fn
    return deco


def wait_for(pred, what, deadline=DEADLINE):
    """Poll `pred` until it is true; a deadline, never a fixed sleep."""
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        got = pred()
        if got:
            return got
        time.sleep(0.05)
    raise Fail(f"timed out waiting for {what}")


def server_env():
    return dict(os.environ, PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""),
                QT_QPA_PLATFORM="offscreen")


class Server:
    """`freeshape serve <root> --data-dir <data> ...` as a process of its own."""

    def __init__(self, root, data, *extra):
        self.stderr = []
        self.proc = subprocess.Popen([sys.executable, "-m", "freeshape", "serve", root, "--port", "0",
                                      "--data-dir", data, *extra],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=TMP,
                                     env=server_env(), start_new_session=True)
        SERVERS.append(self)
        lines = queue.Queue()

        def pump(stream, sink):
            for line in iter(stream.readline, ""):          # drains for ever so a full pipe never stalls the server
                sink(line)

        threading.Thread(target=pump, args=(self.proc.stdout, lines.put), daemon=True).start()
        threading.Thread(target=pump, args=(self.proc.stderr, self.stderr.append), daemon=True).start()
        deadline = time.monotonic() + READY_TIMEOUT
        while True:
            try:
                line = lines.get(timeout=0.5)
            except queue.Empty:
                line = None
            if line is not None and line.startswith("FREESHAPE_SERVE_READY port="):
                self.port = int(line.split()[1].split("=")[1])
                return
            if line is not None:
                raise Fail(f"unexpected stdout before the ready line: {line!r}")
            if self.proc.poll() is not None:
                time.sleep(0.3)
                raise Fail(f"the server exited {self.proc.returncode} before the ready line; stderr: {''.join(self.stderr)}")
            if time.monotonic() > deadline:
                raise Fail(f"no ready line within {READY_TIMEOUT} s; stderr: {''.join(self.stderr)[-500:]}")

    def request(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=HTTP_TIMEOUT)
        try:
            data = json.dumps(body if body is not None else {}).encode() if method in ("POST", "DELETE") else None
            c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            c.putheader("Host", f"127.0.0.1:{self.port}")
            if data is not None:
                c.putheader("Content-Type", "application/json")
                c.putheader("Content-Length", str(len(data)))
            c.endheaders(data)
            r = c.getresponse()
            payload = r.read()
            if r.getheader("Content-Type", "").startswith("application/json"):
                payload = json.loads(payload)
            return r.status, payload
        finally:
            c.close()

    def ok(self, method, path, body=None, status=200):
        got, payload = self.request(method, path, body)
        check(got == status, f"{method} {path} -> {got} {payload!r}")
        return payload

    def row(self, doc_id):
        (row,) = [r for r in self.ok("GET", "/documents")["documents"] if r["id"] == doc_id]
        return row

    def kill(self):
        """SIGKILL: nothing is saved, no lock released, no autosave written on the way out."""
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGKILL)
        self.proc.wait(STOP_TIMEOUT)

    def stop(self):
        """SIGTERM, bounded wait; whatever happens, kill what is left of the process group."""
        try:
            if self.proc.poll() is None:
                self.proc.send_signal(signal.SIGTERM)
                try:
                    self.proc.wait(STOP_TIMEOUT)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)    # a no-op when the group is already empty
            except (ProcessLookupError, PermissionError):
                pass
            self.proc.wait()
            for s in (self.proc.stdout, self.proc.stderr):
                try:
                    s.close()
                except Exception:                           # noqa: BLE001
                    pass


def new_area(name):
    """A Documents root and a data directory (with its registry) of their own."""
    root, data = os.path.join(TMP, name, "Documents"), os.path.join(TMP, name, "data")
    os.makedirs(root)
    return root, data, R.DocRegistry(os.path.join(data, R.REGISTRY_NAME))


def add_bracket(root, registry):
    """A copy of the example bracket Document in `root`, registered the way the desktop does; (id, real path)."""
    path = os.path.join(root, "Bracket")
    shutil.copytree(BRACKET, path)
    with open(os.path.join(path, "document.json")) as fh:
        doc_id = json.load(fh)["id"]
    registry.register_created(doc_id, path)
    return doc_id, os.path.realpath(path)


def tab_bytes(folder):
    with open(os.path.join(folder, "bracket.fshape"), "rb") as fh:
        return fh.read()


def autosaves(data):
    try:
        return sorted(os.listdir(os.path.join(data, AS.DIR_NAME)))
    except OSError:
        return []


def main():
    code = 1
    try:
        run()
        code = 0
    except SystemExit as e:
        code = e.code
    except BaseException:                               # noqa: BLE001
        traceback.print_exc()
    finally:
        for s in SERVERS:
            s.stop()
        shutil.rmtree(TMP, ignore_errors=True)
    if code == 0:
        print(f"SW2 OK: {len(STEPS)} steps")
    return code


def run():
    root, data, registry = new_area("one")
    first = Server(root, data)
    bracket_id, bracket = add_bracket(root, registry)
    state = {}

    @step("create a Document over HTTP, add a Part Studio, save, close; doc_folder lists both tabs")
    def _():
        made = first.ok("POST", "/documents", {"name": "Widget"}, status=201)
        doc_id = made["id"]
        folder = os.path.join(root, "Widget")
        check(first.row(doc_id)["open"] is False and made["name"] == "Widget", made)
        opened = first.ok("POST", f"/documents/{doc_id}/open")
        check(opened["read_only"] is False and doc_lock.holder(folder).program == "browser", opened)
        added = first.ok("POST", f"/documents/{doc_id}/tabs", {"kind": "studio", "name": "Second"}, status=201)
        check(added["tab"]["name"] == "Second", added)
        listed = first.ok("GET", f"/documents/{doc_id}/tabs")["tabs"]
        check(len(listed) == 2 and all("revision" in t for t in listed), listed)     # the client drops a tab without one
        check(first.ok("POST", f"/documents/{doc_id}/save")["saved"], "nothing saved")
        check(first.ok("POST", f"/documents/{doc_id}/close") == {"closed": True}, "close")
        check(doc_lock.holder(folder) is None, "the lock file is still there after close")
        doc = DF.FolderDocument.open(folder)
        try:
            check(doc.id == doc_id and doc.name == "Widget", (doc.id, doc.name))
            check([(t.kind, t.name) for t in doc.tabs][-1] == ("studio", "Second") and len(doc.tabs) == 2,
                  [(t.kind, t.name) for t in doc.tabs])
        finally:
            doc.close()

    @step("a Document held by a second holder: an edit is 423, take-over makes it 200, the other's lock is gone")
    def _():
        desktop = doc_lock.acquire(bracket, "desktop")
        opened = first.ok("POST", f"/documents/{bracket_id}/open")
        check(opened["read_only"] is True and opened["holder"]["program"] == "desktop", opened)
        status, body = first.request("POST", f"/documents/{bracket_id}/tabs/T1/edit", EDIT)
        check(status == 423 and body["holder"]["program"] == "desktop", (status, body))
        check(doc_lock.holder(bracket).instance == desktop.instance, "the server touched the other holder's lock")
        check(first.ok("POST", f"/documents/{bracket_id}/take-over") == {"read_only": False, "holder": None}, "take-over")
        check(first.ok("POST", f"/documents/{bracket_id}/tabs/T1/edit", EDIT)["rebuilt"] == ["T1"], "edit after take-over")
        check(not desktop.still_mine(), "the first holder still thinks the lock is its own")
        check(doc_lock.holder(bracket).program == "browser", doc_lock.holder(bracket))
        first.ok("POST", f"/documents/{bracket_id}/close", {"discard": True})
        check(tab_bytes(bracket) == open(os.path.join(BRACKET, "bracket.fshape"), "rb").read(), "the edit was saved")

    first.stop()

    # ------------------------------------------------------------------ two servers, a kill, a recovery
    root, data, registry = new_area("two")
    doc_id, folder = add_bracket(root, registry)

    @step("the first server edits and autosaves; a second server opens the Document read-only and leaves its autosave")
    def _():
        a = Server(root, data, "--autosave-interval", "0.2")
        state["a"] = a
        check(a.ok("POST", f"/documents/{doc_id}/open")["read_only"] is False, "A open")
        check(a.request("POST", f"/documents/{doc_id}/tabs/T1/edit", EDIT)[0] == 200, "A edit")
        instance = wait_for(lambda: next(iter(autosaves(data)), None), "A's autosave")
        wait_for(lambda: AS.read_info(data, instance) is not None, "A's complete autosave")
        state["instance"] = instance
        before = sorted(os.listdir(os.path.join(data, AS.DIR_NAME, instance)))
        check(doc_lock.holder(folder).instance == instance, "the autosave is not under A's lock")
        b = Server(root, data)                                  # the default timeout: A's heartbeat is fresh
        row = b.row(doc_id)
        check(row["autosave"] is None and row["holder"]["program"] == "browser", row)
        opened = b.ok("POST", f"/documents/{doc_id}/open")
        check(opened["read_only"] is True and opened["autosave"] is None, opened)
        for call in ("recover", "discard-autosave"):
            check(b.request("POST", f"/documents/{doc_id}/{call}")[0] == 423, f"{call} on a read-only Document")
        check(b.request("POST", f"/documents/{doc_id}/tabs/T1/edit", EDIT)[0] == 423, "B edit")
        b.ok("POST", f"/documents/{doc_id}/close")
        b.stop()
        check(autosaves(data) == [instance], autosaves(data))
        check(sorted(os.listdir(os.path.join(data, AS.DIR_NAME, instance))) == before, "B changed A's autosave")
        check(doc_lock.holder(folder).instance == instance, "B touched A's lock")

    @step("after the first is killed and its heartbeat is stale, a new server opens it for editing and offers the autosave")
    def _():
        state["a"].kill()
        short = 0.5
        check(doc_lock.holder(folder, timeout=short) is not None, "the kill released the lock")
        wait_for(lambda: doc_lock.holder(folder, timeout=short).stale, "the killed server's lock going stale")
        c = Server(root, data, "--lock-timeout", str(short))
        state["c"] = c
        check(c.row(doc_id)["autosave"]["count"] == 1, c.row(doc_id))
        opened = c.ok("POST", f"/documents/{doc_id}/open")
        check(opened["read_only"] is False, opened)
        check(opened["autosave"]["count"] == 1 and [t["id"] for t in opened["autosave"]["tabs"]] == ["T1"], opened["autosave"])
        check(autosaves(data) == [state["instance"]], "opening deleted the autosave")

    @step("recover brings the edit back unsaved; a save writes it and clears the autosave")
    def _():
        c = state["c"]
        original = tab_bytes(folder)
        check(original == open(os.path.join(BRACKET, "bracket.fshape"), "rb").read(), "the killed edit reached the disk")
        got = c.ok("POST", f"/documents/{doc_id}/recover")
        check(got["recovered"] == ["T1"], got)
        tabs = c.ok("GET", f"/documents/{doc_id}/tabs")["tabs"]
        check(tabs[0]["dirty"] is True, tabs)
        check(tab_bytes(folder) == original, "recover wrote the Document")
        check(c.ok("POST", f"/documents/{doc_id}/save")["saved"] == ["T1"], "save")
        check(tab_bytes(folder) != original, "the save did not write the recovered edit")
        check(autosaves(data) == [], autosaves(data))
        c.ok("POST", f"/documents/{doc_id}/close")
        c.stop()

    # ------------------------------------------------------------------ a registry that does not match the mount
    @step("serve on a registry whose Documents are not under its mount exits 1 with the mount message")
    def _():
        root3, data3, registry3 = new_area("three")
        gone = os.path.join(TMP, "three", "config", "Documents", "Lost One")
        registry3.register_created("lost-0001", gone)
        proc = subprocess.run([sys.executable, "-m", "freeshape", "serve", root3, "--port", "0", "--data-dir", data3],
                              capture_output=True, text=True, cwd=TMP, env=server_env(), timeout=READY_TIMEOUT)
        check(proc.returncode == 1 and "FREESHAPE_SERVE_READY" not in proc.stdout, (proc.returncode, proc.stdout))
        check(os.path.realpath(gone) in proc.stderr and "mount" in proc.stderr, proc.stderr)


if __name__ == "__main__":
    sys.exit(main())
