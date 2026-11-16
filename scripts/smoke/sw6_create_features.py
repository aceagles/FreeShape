"""Creating features over `python -m freeshape serve` (slice S6, gate I6.6): no Qt, no network beyond loopback.

Part of the cumulative smoke gate (see s00_baseline.py for the convention; style as sw4_refs.py). One server over a
Documents root and data directory of its own; nothing touches a real user folder. One line per step, ending `ok`; a
failed step prints why and the script exits 1 with no `SW6 OK`. The server is stopped (SIGTERM, then SIGKILL of its
group) on success and on failure.

The slice claim: the feature dialog routes create a feature from picks, and each feature changes the part as the
desktop's would. The fixture is made here, from scratch through the server (no example Document is touched): a new
Document, its Part Studio, and one sketch of a 40 x 25 mm rectangle, with no solid.

Steps:
  the fixture: one sketch with one region and no part
  extrude the sketch region 10 mm: one part, a 40 x 25 x 10 block (volume 10000, 6 faces)
  fillet one vertical edge 2 mm: one more face, the volume falls by the corner's share (4 - pi) * 10
  shell with the top face removed, 1 mm thick: the volume falls and the part stays one solid
  save, close, open the folder with doc_folder: the three features and the same volume

    both fs_py scripts/smoke/sw6_create_features.py
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

TMP = tempfile.mkdtemp(prefix="sw6_create_")
os.environ["FREESHAPE_DATA_DIR"] = os.path.join(TMP, "appdata")     # nothing may touch a real user folder
os.environ["XDG_DATA_HOME"] = os.path.join(TMP, "xdg")
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["FREESHAPE_PLUGIN_DIR"] = os.path.join(TMP, "plugins")
for _d in ("appdata", "xdg", "home", "plugins"):
    os.makedirs(os.path.join(TMP, _d), exist_ok=True)

from freeshape import doc_folder as DF  # noqa: E402
from freeshape import doc_lock  # noqa: E402
from freeshape import doc_registry as R  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.engine import wire  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402

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




CLIENT = "client-sw6-smoke"
TOP = {"kind": "std", "name": "Top"}
W, D, H = 40.0, 25.0, 10.0


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
        print(f"SW6 OK: {len(STEPS)} steps")
    return code


def rectangle():
    s = Sketch()
    p = [s.add_point(0, 0), s.add_point(W, 0), s.add_point(W, D), s.add_point(0, D)]
    for a, b in zip(p, p[1:] + p[:1]):
        s.add_line(a, b)
    s.solve()
    return s.to_dict()


def volume_of(part):
    return K.volume(K.shape_from_brep(part.brep))


def run():
    root, data, _registry = new_area("one")
    server = Server(root, data)
    made = server.ok("POST", "/documents", {"name": "Creating"}, status=201)
    doc_id = made["id"]
    folder = os.path.join(root, "Creating")
    check(server.ok("POST", f"/documents/{doc_id}/open")["read_only"] is False, "open for editing")
    tab = f"/documents/{doc_id}/tabs/T1"

    def outputs():
        status, body = server.request("GET", f"{tab}/outputs")
        check(status == 200, f"GET outputs -> {status}")
        return wire.from_wire(body)

    def revision():
        return server.ok("GET", f"/documents/{doc_id}/tabs")["tabs"][0]["revision"]

    def create(ftype, picks, values):
        """Open a create dialog with these picks, set these values, accept; the new revision."""
        before = revision()
        got = server.request("POST", f"{tab}/features/dialog", {"type": ftype, "picks": picks}, client=CLIENT)
        check(got[0] == 201, f"open {ftype} dialog -> {got}")
        dialog = got[1]
        check(dialog["feature"] is None and revision() == before, "opening is a query")
        for key, items in dialog["fields"].items():
            check(all(i["ok"] for i in items), (key, items))
        if values:
            status, upd = server.request("POST", f"{tab}/dialog/{dialog['dialog']}/update", {"values": values}, client=CLIENT)
            check(status == 200 and not upd["errors"], (status, upd))
        status, done = server.request("POST", f"{tab}/dialog/{dialog['dialog']}/accept", {}, client=CLIENT)
        check(status == 200 and done["revision"] == before + 1, (status, done, before))
        return dialog

    def pick(part, kind, index):
        return {"part": part, "kind": kind, "index": index, "want": [kind]}

    state = {}

    @step("the fixture: a Part Studio with one rectangle sketch, one region and no part")
    def _():
        feature = {"id": "F1", "type": "sketch", "name": "Sketch 1", "suppressed": False,
                   "params": {"plane": TOP, "sketch": rectangle()}}
        check(server.ok("POST", f"{tab}/edit", {"op": "add_feature", "feature": feature})["rebuilt"] == ["T1"], "add the sketch")
        out = outputs()
        check([(f.id, f.type) for f in out.features] == [("F1", "sketch")] and not out.parts, out.features)
        check(len(out.sketches) == 1 and len(out.sketches[0].regions) == 1, "the sketch has one region")

    @step("extrude the sketch region 10 mm: one part, a 40 x 25 x 10 block")
    def _():
        dialog = create("extrude", [pick("F1", "region", 1)], {"depth": H})
        check(dialog["fields"]["profiles"][0]["kind"] == "region", dialog["fields"])
        out = outputs()
        check([f.type for f in out.features] == ["sketch", "extrude"] and out.features[1].error is None, out.features)
        check(len(out.parts) == 1, f"{len(out.parts)} parts")
        part = out.parts[0]
        check(abs(volume_of(part) - W * D * H) < 1e-3, volume_of(part))
        lo, hi = K.bbox(K.shape_from_brep(part.brep))
        check(all(abs(a - b) < 1e-6 for a, b in zip(hi - lo, (W, D, H))), (lo, hi))
        check(len(part.names["face"]) == 6, len(part.names["face"]))
        state.update(part=part.id, volume=volume_of(part), faces=6)

    @step("fillet one vertical edge 2 mm: one more face and the corner's share of the volume gone")
    def _():
        part = outputs().parts[0]
        shape = K.shape_from_brep(part.brep)
        vertical = [i for i, e in enumerate(K.edges(shape), 1) if abs(abs(K.edge_info(e)["d"][2]) - 1.0) < 1e-9]
        check(len(vertical) == 4, f"{len(vertical)} vertical edges")
        create("fillet", [pick(part.id, "edge", vertical[0])], {"radius": 2})
        out = outputs()
        check(out.features[-1].type == "fillet" and out.features[-1].error is None, out.features[-1])
        after = out.parts[0]
        check(len(after.names["face"]) == state["faces"] + 1, len(after.names["face"]))
        lost = state["volume"] - volume_of(after)
        want = (4 - math.pi) * 2 * 2 / 4 * H       # r^2 (1 - pi/4) times the edge's length
        check(abs(lost - want) < 1e-3, (lost, want))
        state.update(volume=volume_of(after), faces=len(after.names["face"]))

    @step("shell with the top face removed, 1 mm: the volume falls and the part stays one solid")
    def _():
        part = outputs().parts[0]
        shape = K.shape_from_brep(part.brep)
        top = [i for i, f in enumerate(K.faces(shape), 1)
               if (n := K.face_info(f).get("n")) is not None and abs(n[2] - 1.0) < 1e-9 and abs(K.face_info(f)["c"][2] - H) < 1e-6]
        check(len(top) == 1, f"{len(top)} top faces")
        create("shell", [pick(part.id, "face", top[0])], {"thickness": 1})
        out = outputs()
        check(out.features[-1].type == "shell" and out.features[-1].error is None, out.features[-1])
        check(len(out.parts) == 1, f"{len(out.parts)} parts")
        after = out.parts[0]
        v = volume_of(after)
        check(0 < v < state["volume"] * 0.5, (v, state["volume"]))
        check(len(after.names["face"]) > state["faces"], len(after.names["face"]))
        state.update(volume=v)

    @step("save, close, open the folder with doc_folder: three features and the same volume")
    def _():
        check(server.ok("POST", f"/documents/{doc_id}/save")["saved"], "nothing saved")
        check(server.ok("POST", f"/documents/{doc_id}/close") == {"closed": True}, "close")
        check(doc_lock.holder(folder) is None, "the lock is still there after close")
        fd = DF.FolderDocument.open(folder)
        try:
            check(fd.id == doc_id and len(fd.tabs) == 1, (fd.id, len(fd.tabs)))
            doc = fd.tabs[0].doc
            check([f["type"] for f in doc.features] == ["sketch", "extrude", "fillet", "shell"], [f["type"] for f in doc.features])
            check(not doc.errors, doc.errors)
            (part,) = doc.state.parts.values()
            check(abs(K.volume(part.shape) - state["volume"]) < 1e-6, (K.volume(part.shape), state["volume"]))
        finally:
            fd.close()


if __name__ == "__main__":
    sys.exit(main())
