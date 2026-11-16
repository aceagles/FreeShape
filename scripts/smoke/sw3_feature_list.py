"""The feature list over `python -m freeshape serve` (slice S3, gate I3.4): no Qt, no network beyond loopback.

Part of the cumulative smoke gate (see s00_baseline.py for the convention; style as sw2_documents.py). One server
over a copy of the example bracket Document in a temporary Documents root and data directory; nothing touches a real
user folder. One line per step, ending `ok`; a failed step prints why and the script exits 1 with no `SW3 OK`. The
server is stopped (SIGTERM, then SIGKILL of its group) on success and on failure.

A part's volume is read from the served outputs: `GET /documents/<id>/tabs/<tab>/outputs` is decoded with the engine's
`wire.from_wire` and the volume of each part's display mesh is the signed sum of the triangles' tetrahedra over the
origin (closed mesh, outward winding), so it needs numpy only, no kernel. A chamfer or a slot is millimetres of
material, far above the display mesh's tolerance.

Steps, one clause of the slice claim each:
  the outputs list the bracket's eight features in file order, none suppressed, nothing to undo
  suppressing the last feature (Chamfer 1) over HTTP changes the part's volume; undo restores it
  rolling back above the Slot changes the volume, rolling forward restores it
  hiding the part with `set_part_override` hidden marks it hidden in the outputs; showing it undoes that

Nothing sleeps for a fixed time: the waits poll with a deadline.

    both fs_py scripts/smoke/sw3_feature_list.py
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

TMP = tempfile.mkdtemp(prefix="sw3_feature_list_")
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

def volume(out):
    """Total volume of the visible parts' display meshes, mm^3 (signed tetrahedra over the origin)."""
    total = 0.0
    for part in out.parts:
        if part.mesh is None or part.hidden:
            continue
        v = np.asarray(part.mesh.verts, dtype=np.float64)[np.asarray(part.mesh.tris)]       # (m, 3 corners, 3)
        total += float(np.einsum("ij,ij->i", v[:, 0], np.cross(v[:, 1], v[:, 2])).sum()) / 6.0
    return abs(total)


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
        print(f"SW3 OK: {len(STEPS)} steps")
    return code


def run():
    root, data, registry = new_area("one")
    doc_id, _folder = add_bracket(root, registry)
    server = Server(root, data)
    tab = f"/documents/{doc_id}/tabs/T1"

    def outputs():
        status, body = server.request("GET", f"{tab}/outputs")
        check(status == 200, f"GET outputs -> {status}")
        return wire.from_wire(body)

    def edit(body):
        return server.ok("POST", f"{tab}/edit", body)

    state = {}
    check(server.ok("POST", f"/documents/{doc_id}/open")["read_only"] is False, "open for editing")

    @step("the outputs list the bracket's eight features in file order, none suppressed, nothing to undo")
    def _():
        out = outputs()
        names = [f.name for f in out.features]
        check([f.id for f in out.features] == [f"F{i}" for i in range(1, 9)], names)
        check(names[-1] == "Chamfer 1" and names[2] == "Fillet 1", names)
        check(not any(f.suppressed or f.error for f in out.features), "a feature starts suppressed or failed")
        check(out.rollback == 8 and out.can_undo is False and out.can_redo is False, (out.rollback, out.can_undo, out.can_redo))
        state["volume"] = volume(out)
        check(state["volume"] > 1000.0, f"volume {state['volume']}")
        print(f"  bracket volume {state['volume']:.1f} mm3", flush=True)

    @step("suppressing the last feature over HTTP changes the part's volume; undo restores it")
    def _():
        before = state["volume"]
        edit({"op": "set_suppressed", "feature": "F8", "value": True})
        out = outputs()
        check(out.features[-1].suppressed is True and out.can_undo is True, "Chamfer 1 not suppressed")
        gone = volume(out)
        check(abs(gone - before) > 1.0, f"volume {before} -> {gone}: the chamfer's removal did not show")
        print(f"  suppressed Chamfer 1: {before:.1f} -> {gone:.1f} mm3", flush=True)
        edit({"op": "undo"})
        out = outputs()
        check(out.features[-1].suppressed is False and out.can_redo is True, "undo did not unsuppress")
        check(abs(volume(out) - before) < 1e-6 * before, f"undo: volume {volume(out)} is not {before}")

    @step("rolling back above the Slot changes the volume; rolling forward restores it")
    def _():
        before = state["volume"]
        edit({"op": "set_rollback", "index": 5})
        out = outputs()
        check(out.rollback == 5, out.rollback)
        back = volume(out)
        check(abs(back - before) > 1.0, f"volume {before} -> {back}: rolling back did not show")
        edit({"op": "set_rollback", "index": None})
        out = outputs()
        check(out.rollback == 8 and abs(volume(out) - before) < 1e-6 * before, (out.rollback, volume(out)))

    @step("hiding the part with set_part_override marks it hidden in the outputs; showing it undoes that")
    def _():
        out = outputs()
        check(len(out.parts) == 1 and out.parts[0].hidden is False, [p.hidden for p in out.parts])
        pid = out.parts[0].id
        edit({"op": "set_part_override", "part": pid, "hidden": True})
        check([p.hidden for p in outputs().parts] == [True], "the part is not hidden")
        edit({"op": "set_part_override", "part": pid, "hidden": False})
        check([p.hidden for p in outputs().parts] == [False], "the part is not shown again")
        server.ok("POST", f"/documents/{doc_id}/close", {"discard": True})


if __name__ == "__main__":
    sys.exit(main())
