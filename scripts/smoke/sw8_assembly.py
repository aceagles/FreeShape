"""An Assembly moved over `python -m freeshape serve` (slice S8, gate I8.3): no Qt, no network beyond loopback.

Part of the cumulative smoke gate (see s00_baseline.py for the convention; style as sw7_sketch_session.py). One server
over a Documents root and data directory of its own, holding a copy of the example hinge Document; nothing touches a
real user folder. One line per step, ending `ok`; a failed step prints why and the script exits 1 with no `SW8 OK`.
The server is stopped on success and on failure.

The slice claim: the client's Assembly edits (hide an instance, then the three calls of a drag by a Drag handle) change
what /outputs serves, step by step, and one undo takes each back.

Steps:
  open the hinge: three instances, none hidden, the leaf I2 free and the base I1 fixed
  set_hidden on I3: /outputs shows it hidden and the other two shown
  move_begin on I2, move_to a 20 then a 35 degree turn about the pin, move_end: I2 stands where 35 degrees puts it,
    the fixed base and the hidden instance stay put
  undo once: I2 is back where it began and I3 is still hidden
  undo once more: I3 is shown again

    both fs_py scripts/smoke/sw8_assembly.py
"""

import http.client
import json
import math
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

TMP = tempfile.mkdtemp(prefix="sw8_assembly_")
os.environ["FREESHAPE_DATA_DIR"] = os.path.join(TMP, "appdata")     # nothing may touch a real user folder
os.environ["XDG_DATA_HOME"] = os.path.join(TMP, "xdg")
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["FREESHAPE_PLUGIN_DIR"] = os.path.join(TMP, "plugins")
for _d in ("appdata", "xdg", "home", "plugins"):
    os.makedirs(os.path.join(TMP, _d), exist_ok=True)

import numpy as np  # noqa: E402

from freeshape import doc_registry as R  # noqa: E402
from freeshape.engine import wire  # noqa: E402

READY_TIMEOUT = 120
STOP_TIMEOUT = 60
HTTP_TIMEOUT = 120
DEADLINE = 120.0


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

    def request(self, method, path, body=None, client=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=HTTP_TIMEOUT)
        try:
            data = json.dumps(body if body is not None else {}).encode() if method in ("POST", "DELETE") else None
            c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            c.putheader("Host", f"127.0.0.1:{self.port}")
            if client is not None:
                c.putheader("X-Client-Id", client)
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





HINGE = os.path.join(ROOT, "examples", "documents", "hinge")
CLIENT = "client-sw8-smoke"


def add_hinge(root, registry):
    """A copy of the example hinge Document in `root`, registered the way the desktop does; its id."""
    path = os.path.join(root, "Hinge")
    shutil.copytree(HINGE, path)
    with open(os.path.join(path, "document.json")) as fh:
        doc_id = json.load(fh)["id"]
    registry.register_created(doc_id, path)
    return doc_id


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
        print(f"SW8 OK: {len(STEPS)} steps")
    return code


def turn_about(origin, axis, deg):
    """The 4x4 world motion of `deg` degrees about the line through `origin` along `axis` (Rodrigues)."""
    a = np.asarray(axis, float) / np.linalg.norm(axis)
    o = np.asarray(origin, float)
    k = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    t = math.radians(deg)
    r = np.eye(3) + math.sin(t) * k + (1 - math.cos(t)) * (k @ k)
    m = np.eye(4)
    m[:3, :3] = r
    m[:3, 3] = o - r @ o
    return m


def run():
    root, data, registry = new_area("one")
    doc_id = add_hinge(root, registry)
    server = Server(root, data)
    check(server.ok("POST", f"/documents/{doc_id}/open")["read_only"] is False, "open for editing")
    tab = f"/documents/{doc_id}/tabs/T2"
    state = {}

    def outputs():
        status, body = server.request("GET", f"{tab}/outputs")
        check(status == 200, f"GET outputs -> {status}")
        return wire.from_wire(body)

    def placements():
        return {i.id: np.array(i.T, float) for i in outputs().instances}

    def hidden():
        return sorted(i.id for i in outputs().instances if i.hidden)

    def edit(body):
        return server.ok("POST", f"{tab}/edit", body)

    @step("open the hinge Assembly: three instances, none hidden, the base fixed and the leaf free")
    def _():
        out = outputs()
        check(out.kind == "assembly" and [i.id for i in out.instances] == ["I1", "I2", "I3"], out.instances)
        check(hidden() == [], hidden())
        check([i.id for i in out.instances if i.fixed] == ["I1"], [(i.id, i.fixed) for i in out.instances])
        check(any(m.type == "revolute" for m in out.mates), [m.type for m in out.mates])
        state["start"] = placements()

    @step("set_hidden on I3: /outputs lists it hidden and nothing else; no instance moved")
    def _():
        before = outputs().revision
        edit({"op": "set_hidden", "instance": "I3", "value": True})
        check(hidden() == ["I3"], hidden())
        check(outputs().revision > before, "the revision did not rise")
        now = placements()
        for k, T in state["start"].items():
            check(np.allclose(now[k], T, atol=1e-9), f"{k} moved when I3 was hidden")

    @step("move_begin I2, move_to 20 then 35 degrees about the pin, move_end: I2 turns 35 degrees; the base and I3 stay")
    def _():
        mate = next(m for m in outputs().mates if m.type == "revolute")
        w = mate.a.world
        o, z = w["o"], w["z"]
        edit({"op": "move_begin", "instance": "I2"})
        edit({"op": "move_to", "T": turn_about(o, z, 20).tolist()})
        mid = placements()
        check(not np.allclose(mid["I2"], state["start"]["I2"], atol=1e-3), "I2 did not move at 20 degrees")
        edit({"op": "move_to", "T": turn_about(o, z, 35).tolist()})
        edit({"op": "move_end"})
        end = placements()
        want = turn_about(o, z, 35) @ state["start"]["I2"]
        check(np.allclose(end["I2"], want, atol=1e-3), "I2 is not where a 35 degree turn about the pin puts it")
        check(np.allclose(end["I1"], state["start"]["I1"], atol=1e-9), "the fixed base moved")
        check(np.allclose(end["I3"], state["start"]["I3"], atol=1e-6), "I3 moved")
        check(hidden() == ["I3"], hidden())

    @step("undo once: I2 is back where it began (one undo for the whole drag) and I3 is still hidden")
    def _():
        edit({"op": "undo"})
        now = placements()
        for k, T in state["start"].items():
            check(np.allclose(now[k], T, atol=1e-6), f"{k} is not back at its start")
        check(hidden() == ["I3"], hidden())

    @step("undo once more: I3 is shown again")
    def _():
        edit({"op": "undo"})
        check(hidden() == [], hidden())


if __name__ == "__main__":
    sys.exit(main())
