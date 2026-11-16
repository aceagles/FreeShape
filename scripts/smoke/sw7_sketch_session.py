"""A part made from nothing over `python -m freeshape serve` (slice S7, gate I7.14): no Qt, no network beyond loopback.

Part of the cumulative smoke gate (see s00_baseline.py for the convention; style as sw6_create_features.py). One server
over a Documents root and data directory of its own; nothing touches a real user folder. One line per step, ending `ok`;
a failed step prints why and the script exits 1 with no `SW7 OK`. The server is stopped on success and on failure.

The slice claim: a sketch is drawn, dimensioned and committed over the live socket as the client does it, and the part
made from it is the one the numbers say. Nothing is made but a new Document and its Part Studio.

Steps:
  over the socket: a sketch on the Top plane, a 40 x 20 corner rectangle with a circle inside it, dimensioned, committed
  over HTTP: extrude the region 10 mm; the volume is (40 x 20 - pi r^2) x 10 within 1e-6 relative
  fillet one vertical edge 2 mm: the volume falls by the corner's share
  save, close, open with doc_folder and the desktop's document class: same volume, same Element names

    both fs_py scripts/smoke/sw7_sketch_session.py
"""
import asyncio
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
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="sw7_sketch_")
os.environ["FREESHAPE_DATA_DIR"] = os.path.join(TMP, "appdata")     # nothing may touch a real user folder
os.environ["XDG_DATA_HOME"] = os.path.join(TMP, "xdg")
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["FREESHAPE_PLUGIN_DIR"] = os.path.join(TMP, "plugins")
for _d in ("appdata", "xdg", "home", "plugins"):
    os.makedirs(os.path.join(TMP, _d), exist_ok=True)

import aiohttp  # noqa: E402

from freeshape import doc_folder as DF  # noqa: E402
from freeshape import doc_lock  # noqa: E402
from freeshape import doc_registry as R  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.engine import wire  # noqa: E402
from freeshape.engine.encode import element_names  # noqa: E402

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




CLIENT = "client-sw7-smoke"
TOP = {"kind": "std", "name": "Top"}
W, D, H, R_HOLE, R_FILLET = 40.0, 20.0, 10.0, 4.0, 2.0


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
        print(f"SW7 OK: {len(STEPS)} steps")
    return code


def volume_of(part):
    return K.volume(K.shape_from_brep(part.brep))


class Socket:
    """One WebSocket of the client id, speaking the sketch.* messages; each call returns the answer or fails on an error."""

    def __init__(self, port):
        self.port = port
        self.session = None
        self.ws = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        self.ws = await self.session.ws_connect(f"http://127.0.0.1:{self.port}/ws?client={CLIENT}")
        return self

    async def __aexit__(self, *exc):
        await self.ws.close()
        await self.session.close()

    async def call(self, message, want):
        await self.ws.send_str(json.dumps(message))
        while True:
            msg = await asyncio.wait_for(self.ws.receive(), 60)
            check(msg.type == aiohttp.WSMsgType.TEXT, f"the socket gave {msg.type!r} while waiting for {want!r}")
            data = json.loads(msg.data)
            if data.get("type") == "error":
                raise Fail(f"{message['type']} refused: {data.get('message')}")
            if data.get("type") == want:
                return data


def run_async(coro):
    return asyncio.run(asyncio.wait_for(coro, 180))


def run():
    root, data, _registry = new_area("one")
    server = Server(root, data)
    made = server.ok("POST", "/documents", {"name": "From nothing"}, status=201)
    doc_id = made["id"]
    folder = os.path.join(root, "From nothing")
    check(server.ok("POST", f"/documents/{doc_id}/open")["read_only"] is False, "open for editing")
    tab = f"/documents/{doc_id}/tabs/T1"
    state = {}

    def outputs():
        status, body = server.request("GET", f"{tab}/outputs")
        check(status == 200, f"GET outputs -> {status}")
        return wire.from_wire(body)

    def revision():
        return server.ok("GET", f"/documents/{doc_id}/tabs")["tabs"][0]["revision"]

    def create(ftype, picks, values):
        before = revision()
        got = server.request("POST", f"{tab}/features/dialog", {"type": ftype, "picks": picks}, client=CLIENT)
        check(got[0] == 201, f"open {ftype} dialog -> {got}")
        dialog = got[1]
        for key, items in dialog["fields"].items():
            check(all(i["ok"] for i in items), (key, items))
        status, upd = server.request("POST", f"{tab}/dialog/{dialog['dialog']}/update", {"values": values}, client=CLIENT)
        check(status == 200 and not upd["errors"], (status, upd))
        status, done = server.request("POST", f"{tab}/dialog/{dialog['dialog']}/accept", {}, client=CLIENT)
        check(status == 200 and done["revision"] == before + 1, (status, done, before))

    @step("over the socket: a sketch on the Top plane, a 40 x 20 corner rectangle on the origin and a circle inside it, dimensioned and committed")
    def _():
        async def go():
            async with Socket(server.port) as ws:
                opened = await ws.call({"type": "sketch.open", "document": doc_id, "tab": "T1", "plane": TOP}, "sketch.opened")
                check(opened["feature"] is None and opened["status"]["ok"], opened["status"])
                rect = (await ws.call({"type": "sketch.add", "shape": "rect",
                                       "picks": [{"at": [0, 0], "point": "origin"}, [W, D]]}, "sketch.changed"))["made"]
                circle = (await ws.call({"type": "sketch.add", "shape": "circle", "picks": [[W / 2, D / 2]],
                                         "radius": R_HOLE}, "sketch.changed"))["made"]
                bottom, right = rect["entities"][0], rect["entities"][1]
                (hole,) = circle["entities"]
                for picks, pos, value in (([bottom], [W / 2, -6], W), ([right], [W + 6, D / 2], D), ([hole], [W / 2, D / 2 + 8], 2 * R_HOLE)):
                    got = await ws.call({"type": "sketch.dimension", "picks": picks, "pos": pos, "value": value}, "sketch.changed")
                    check(abs(got["made"]["value"] - value) < 1e-9, got["made"])
                # the origin holds one corner, the lengths are dimensioned: only the circle's place is free (2 dof)
                st = await ws.call({"type": "sketch.state"}, "sketch.state")
                check(st["status"]["ok"] and st["status"]["dof"] == 2, st["status"])
                committed = await ws.call({"type": "sketch.commit"}, "sketch.committed")
                check(committed["feature"] == "F1" and committed["rebuilt"], committed)
        run_async(go())
        out = outputs()
        check([(f.id, f.type) for f in out.features] == [("F1", "sketch")], out.features)
        (sk,) = out.sketches
        areas = sorted(r["area"] for r in sk.regions)
        check(len(areas) == 2, areas)
        check(abs(areas[1] - (W * D - math.pi * R_HOLE ** 2)) < 1e-6 and abs(areas[0] - math.pi * R_HOLE ** 2) < 1e-6, areas)
        state["region"] = max(range(len(sk.regions)), key=lambda i: sk.regions[i]["area"]) + 1

    @step("over HTTP: extrude the region with the hole 10 mm; the volume is (40 x 20 - pi 4^2) x 10")
    def _():
        create("extrude", [{"part": "F1", "kind": "region", "index": state["region"], "want": ["region"]}], {"depth": H})
        out = outputs()
        check(out.features[-1].type == "extrude" and out.features[-1].error is None, out.features[-1])
        check(len(out.parts) == 1, f"{len(out.parts)} parts")
        v = volume_of(out.parts[0])
        want = (W * D - math.pi * R_HOLE ** 2) * H
        check(abs(v - want) <= 1e-6 * want, (v, want))
        state.update(volume=v, faces=len(out.parts[0].names["face"]))

    @step("fillet one vertical edge of the box 2 mm: the volume is the computed one within 1e-6 relative")
    def _():
        part = outputs().parts[0]
        shape = K.shape_from_brep(part.brep)
        infos = {i: K.edge_info(e) for i, e in enumerate(K.edges(shape), 1)}
        vertical = [i for i, f in infos.items() if "d" in f and abs(abs(f["d"][2]) - 1.0) < 1e-9]
        check(len(vertical) >= 4, f"{len(vertical)} vertical edges")
        # a corner edge of the box: the one farthest from the middle of the plate (the hole's wall is a circle, not a line)
        far = max(vertical, key=lambda i: sum(abs(c - m) for c, m in zip(infos[i]["c"][:2], (W / 2, D / 2))))
        create("fillet", [{"part": part.id, "kind": "edge", "index": far, "want": ["edge"]}], {"radius": R_FILLET})
        out = outputs()
        check(out.features[-1].type == "fillet" and out.features[-1].error is None, out.features[-1])
        check(len(out.parts) == 1, f"{len(out.parts)} parts")
        after = out.parts[0]
        check(len(after.names["face"]) == state["faces"] + 1, len(after.names["face"]))
        want = (W * D - math.pi * R_HOLE ** 2) * H - (4 - math.pi) * R_FILLET ** 2 / 4 * H
        v = volume_of(after)
        check(abs(v - want) <= 1e-6 * want, (v, want))
        state.update(volume=v, names={k: {int(i): n for i, n in after.names[k].items()} for k in ("face", "edge", "vertex")})

    @step("save, close, open the folder with doc_folder and the desktop's document class: same volume, same Element names")
    def _():
        check(server.ok("POST", f"/documents/{doc_id}/save")["saved"], "nothing saved")
        check(server.ok("POST", f"/documents/{doc_id}/close") == {"closed": True}, "close")
        check(doc_lock.holder(folder) is None, "the lock is still there after close")
        fd = DF.FolderDocument.open(folder)
        try:
            check(fd.id == doc_id and len(fd.tabs) == 1, (fd.id, len(fd.tabs)))
            doc = fd.tabs[0].doc
            check([f["type"] for f in doc.features] == ["sketch", "extrude", "fillet"], [f["type"] for f in doc.features])
            check(not doc.errors, doc.errors)
            ((pid, part),) = doc.state.parts.items()
            check(abs(K.volume(part.shape) - state["volume"]) <= 1e-6 * state["volume"], (K.volume(part.shape), state["volume"]))
            names = element_names(SimpleNamespace(shape=part.shape, named=doc.state.named(pid)))
            check(names["face"] and names == state["names"], "the Element names differ after reopening")
        finally:
            fd.close()


if __name__ == "__main__":
    sys.exit(main())
