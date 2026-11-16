"""Editing a feature over `python -m freeshape serve` (slice S5, gate I5.8): no Qt, no network beyond loopback.

Part of the cumulative smoke gate (see s00_baseline.py for the convention; style as sw4_refs.py). One server over a
copy of the example bracket Document in a temporary Documents root and data directory; nothing touches a real user
folder. One line per step, ending `ok`; a failed step prints why and the script exits 1 with no `SW5 OK`. The server
is stopped (SIGTERM, then SIGKILL of its group) on success and on failure.

The slice claim: a client opens a dialog on the bracket's extrude, changes the depth, sees a preview that differs from
the committed part (and that committed nothing), and accepting it changes what /outputs serves. Every dialog call
carries the client id.

Steps:
  open a dialog on Extrude 1 (F2): the stored depth is in it, the revision and the volume are the committed ones
  update the depth: the preview the server builds has a volume that differs from the committed part, and the
    committed revision and volume have not moved
  cancel drops the dialog; a second dialog is accepted: the revision rises and /outputs serves a new volume
  the dialog is gone after accept, and a dialog of another client id was never able to reach it

    both fs_py scripts/smoke/sw5_edit_feature.py
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

TMP = tempfile.mkdtemp(prefix="sw5_edit_")
os.environ["FREESHAPE_DATA_DIR"] = os.path.join(TMP, "appdata")     # nothing may touch a real user folder
os.environ["XDG_DATA_HOME"] = os.path.join(TMP, "xdg")
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["FREESHAPE_PLUGIN_DIR"] = os.path.join(TMP, "plugins")
for _d in ("appdata", "xdg", "home", "plugins"):
    os.makedirs(os.path.join(TMP, _d), exist_ok=True)

from freeshape import doc_registry as R  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.engine import wire  # noqa: E402

READY_TIMEOUT = 120
STOP_TIMEOUT = 60
HTTP_TIMEOUT = 120
DEADLINE = 120.0
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

    def request(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=HTTP_TIMEOUT)
        try:
            data = json.dumps(body if body is not None else {}).encode() if method in ("POST", "DELETE") else None
            c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            c.putheader("Host", f"127.0.0.1:{self.port}")
            for k, v in (headers or {}).items():
                c.putheader(k, v)
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

    def ok(self, method, path, body=None, status=200, headers=None):
        got, payload = self.request(method, path, body, headers)
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
        print(f"SW5 OK: {len(STEPS)} steps")
    return code


CLIENT = {"X-Client-Id": "client-sw5-0001"}
OTHER = {"X-Client-Id": "client-sw5-0002"}


def run():
    root, data, registry = new_area("one")
    doc_id, _folder = add_bracket(root, registry)
    server = Server(root, data)
    tab = f"/documents/{doc_id}/tabs/T1"
    check(server.ok("POST", f"/documents/{doc_id}/open")["read_only"] is False, "open for editing")

    def committed():
        """(revision, volume) of what /outputs serves."""
        out = wire.from_wire(server.ok("GET", f"{tab}/outputs"))
        return out.revision, round(sum(K.volume(K.shape_from_brep(p.brep)) for p in out.parts), 3)

    def revision_listed():
        return server.ok("GET", f"/documents/{doc_id}/tabs")["tabs"][0]["revision"]

    rev0, vol0 = committed()
    state = {}

    def open_dialog():
        got = server.ok("POST", f"{tab}/features/F2/dialog", {}, status=201, headers=CLIENT)
        return got

    def update(did, values):
        return server.ok("POST", f"{tab}/dialog/{did}/update", {"values": values}, headers=CLIENT)

    def preview_volume(did, number):
        """The volume of the dialog's preview number `number` (or newer); polls, no fixed sleep."""
        def got():
            status, payload = server.request("GET", f"{tab}/dialog/{did}/preview", headers=CLIENT)
            if status != 200:
                return None
            out = wire.from_wire(payload)
            return round(sum(K.volume(K.shape_from_brep(p.brep)) for p in out.parts), 3)
        return wait_for(got, f"preview {number}")

    @step("a dialog on Extrude 1 holds the stored depth; the committed revision and volume are the ones served")
    def _():
        got = open_dialog()
        check(got["feature"] == "F2" and got["values"]["depth"]["value"] == 40.0, got["values"].get("depth"))
        check(got["revision"] == rev0 == revision_listed(), (got["revision"], rev0))
        state["cancelled"] = got["dialog"]

    @step("updating the depth builds a preview whose volume differs from the committed part, and commits nothing")
    def _():
        did = state["cancelled"]
        got = update(did, {"depth": "10 mm"})
        check(got["errors"] == {} and got["values"]["depth"]["value"] == 10.0, got)
        check(isinstance(got["preview"], int), got.get("preview"))
        vol = preview_volume(did, got["preview"])
        check(vol != vol0, f"preview volume {vol} equals the committed {vol0}")
        check(committed() == (rev0, vol0), ("a preview changed what is committed", committed()))
        check(revision_listed() == rev0, "the revision moved")
        state["preview_volume"] = vol

    @step("cancel drops the dialog and the committed revision and volume stay")
    def _():
        did = state["cancelled"]
        check(server.ok("POST", f"{tab}/dialog/{did}/cancel", {}, headers=CLIENT) == {"cancelled": True}, "cancel")
        server.ok("POST", f"{tab}/dialog/{did}/update", {"values": {"depth": 11}}, status=404, headers=CLIENT)
        check(committed() == (rev0, vol0), committed())

    @step("accepting a second dialog raises the revision and /outputs serves a new volume")
    def _():
        got = open_dialog()
        did = got["dialog"]
        # another client id cannot reach it
        server.ok("POST", f"{tab}/dialog/{did}/update", {"values": {"depth": 10}}, status=404, headers=OTHER)
        server.ok("POST", f"{tab}/dialog/{did}/accept", {}, status=404, headers=OTHER)
        update(did, {"depth": "10 mm"})
        done = server.ok("POST", f"{tab}/dialog/{did}/accept", {}, headers=CLIENT)
        rev1, vol1 = committed()
        check(done["revision"] == rev1 > rev0, (done, rev1, rev0))
        check(revision_listed() == rev1, "the tab listing has the new revision")
        check(vol1 != vol0, (vol1, vol0))      # the preview stops at the feature, so its volume is not the whole part's
        server.ok("POST", f"{tab}/dialog/{did}/accept", {}, status=404, headers=CLIENT)   # closed by accept
        server.ok("POST", f"/documents/{doc_id}/close", {"discard": True})


if __name__ == "__main__":
    sys.exit(main())
