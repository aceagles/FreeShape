"""References from picks over `python -m freeshape serve` (slice S4, gate I4.4): no Qt, no network beyond loopback.

Part of the cumulative smoke gate (see s00_baseline.py for the convention; style as sw3_feature_list.py). One server
over a copy of the example bracket Document in a temporary Documents root and data directory; nothing touches a real
user folder. One line per step, ending `ok`; a failed step prints why and the script exits 1 with no `SW4 OK`. The
server is stopped (SIGTERM, then SIGKILL of its group) on success and on failure.

The slice claim: what the viewport selects, the server turns into the reference a feature stores, and that reference
finds the same element again. The refs route (`POST /documents/<id>/tabs/<tab>/refs`, 1-based indices) is asked for a
face, an edge and a vertex of the bracket; each returned reference is resolved with `features.resolve` /
`resolve_point` against a model this script builds itself from the same file, and must land on the picked element.

Steps:
  a face's reference carries the Element name the served outputs give that face and resolves to the face
  an edge's reference carries the Element name of the edge and resolves to the edge
  a vertex's reference carries the Element name of the vertex and resolves to the vertex
  a face asked for as an edge gives all of its edges, each resolving to an edge of that face
  an index of 0 or past the end is refused, and asking changed nothing (the revision does not move)

    both fs_py scripts/smoke/sw4_refs.py
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

TMP = tempfile.mkdtemp(prefix="sw4_refs_")
os.environ["FREESHAPE_DATA_DIR"] = os.path.join(TMP, "appdata")     # nothing may touch a real user folder
os.environ["XDG_DATA_HOME"] = os.path.join(TMP, "xdg")
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["FREESHAPE_PLUGIN_DIR"] = os.path.join(TMP, "plugins")
for _d in ("appdata", "xdg", "home", "plugins"):
    os.makedirs(os.path.join(TMP, _d), exist_ok=True)

from freeshape import doc_registry as R  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.document import PartStudioDoc  # noqa: E402
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
        print(f"SW4 OK: {len(STEPS)} steps")
    return code


def same(a, b):
    return a.IsSame(b)


def run():
    root, data, registry = new_area("one")
    doc_id, folder = add_bracket(root, registry)
    server = Server(root, data)
    tab = f"/documents/{doc_id}/tabs/T1"
    check(server.ok("POST", f"/documents/{doc_id}/open")["read_only"] is False, "open for editing")

    status, body = server.request("GET", f"{tab}/outputs")
    check(status == 200, f"GET outputs -> {status}")
    out = wire.from_wire(body)
    check(len(out.parts) == 1, "the bracket has one part")
    part = out.parts[0]
    revision = out.revision

    # the model as the server built it: the same file, built here, so a reference can be resolved against it
    with open(os.path.join(folder, "bracket.fshape"), "rb") as fh:
        doc = PartStudioDoc.from_data(fh.read(), path=os.path.join(folder, "bracket.fshape"))
    state = doc.state
    check(part.id in state.parts, f"part {part.id!r} is not in the local model {sorted(state.parts)}")
    shape = state.parts[part.id].shape
    faces, edges, verts = K.faces(shape), K.edges(shape), K.vertex_points(shape)
    names = {k: {int(i): n for i, n in part.names[k].items()} for k in ("face", "edge", "vertex")}
    check(len(names["face"]) == len(faces) and len(names["edge"]) == len(edges), "the outputs name every face and edge")
    print(f"  {len(faces)} faces, {len(edges)} edges, {len(verts)} vertices", flush=True)

    def refs(picks):
        got = server.ok("POST", f"{tab}/refs", {"picks": picks})
        check(len(got["items"]) == len(picks), got)
        return got["items"]

    def pick(kind, index, want):
        return {"part": part.id, "kind": kind, "index": index, "want": want}

    @step("a face's reference carries the Element name the outputs give that face and resolves to the face")
    def _():
        idx = [1, len(faces) // 2 or 1, len(faces)]
        for item, i in zip(refs([pick("face", i, ["face"]) for i in idx]), idx):
            check(item.get("ok") is True and item["kind"] == "face", item)
            check(item["name"] == names["face"][i], (item["name"], names["face"][i]))
            check(item["ref"].get("name") == item["name"], item)
            pid, sub = F.resolve(state, item["ref"], "face")
            check(pid == part.id and same(sub, faces[i - 1]), f"face {i}: the reference resolves to another face")

    @step("an edge's reference carries the Element name of the edge and resolves to the edge")
    def _():
        idx = [1, len(edges) // 2 or 1, len(edges)]
        for item, i in zip(refs([pick("edge", i, ["edge"]) for i in idx]), idx):
            check(item.get("ok") is True and item["kind"] == "edge", item)
            check(item["name"] == names["edge"][i], (item["name"], names["edge"][i]))
            pid, sub = F.resolve(state, item["ref"], "edge")
            check(pid == part.id and same(sub, edges[i - 1]), f"edge {i}: the reference resolves to another edge")

    @step("a vertex's reference carries the Element name of the vertex and resolves to the vertex")
    def _():
        idx = [1, len(verts) // 2 or 1, len(verts)]
        for item, i in zip(refs([pick("vertex", i, ["vertex"]) for i in idx]), idx):
            check(item.get("ok") is True and item["kind"] == "vertex", item)
            check(item["name"] == names["vertex"][i], (item["name"], names["vertex"].get(i)))
            at = F.resolve_point(state, item["ref"])
            check(float(((at - verts[i - 1]) ** 2).sum()) < 1e-12, f"vertex {i}: {at} is not {verts[i - 1]}")

    @step("a face asked for as an edge gives all of its edges, each resolving to an edge of that face")
    def _():
        (item,) = refs([pick("face", 1, ["edge"])])
        check(item.get("ok") is True and item["kind"] == "edges", item)
        own = K.edges(faces[0])
        check(len(item["ref"]) == len(own), (len(item["ref"]), len(own)))
        for r in item["ref"]:
            pid, sub = F.resolve(state, r, "edge")
            check(pid == part.id and any(same(sub, e) for e in edges), "a face edge resolves to no edge of the part")
            check(any(same(sub, e) for e in own), "a face edge resolves to an edge that is not the face's")

    @step("an index of 0 or past the end is refused, and asking changed nothing")
    def _():
        for bad in (0, len(faces) + 1):
            status, payload = server.request("POST", f"{tab}/refs", {"picks": [pick("face", bad, ["face"])]})
            check(status == 400 and "index" in payload.get("error", ""), (bad, status, payload))
        after = wire.from_wire(server.request("GET", f"{tab}/outputs")[1])
        check(after.revision == revision and after.can_undo is False, (after.revision, revision, after.can_undo))
        server.ok("POST", f"/documents/{doc_id}/close", {"discard": True})


if __name__ == "__main__":
    sys.exit(main())
