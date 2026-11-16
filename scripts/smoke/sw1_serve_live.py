"""Live serve smoke (slice S1, gate I1.12): `python -m freeshape serve` on the bracket example, over HTTP and the
live channel (WebSocket), no Qt, no network beyond loopback.

Part of the cumulative smoke gate (see s00_baseline.py for the convention). It copies examples/documents/bracket to a
temporary folder, starts `sys.executable -m freeshape serve <copy> --port 0 --allow-origin https://cad.example.test`
as a subprocess in its own session, and walks the slice claim one clause per step: the ready line and /health (no Qt);
the old /viewer page is gone; a WebSocket connects and is greeted with `hello` naming the tab and its revision; a
`set_param` edit posted over HTTP comes back on the socket as `changed` naming the tab, the sender and a higher
revision, and the same revision is the one /tabs/<id>/outputs now carries; a `save` edit with a path is refused and
writes no file anywhere; with --allow-origin set, the allowed Host and Origin are accepted on HTTP and on the socket
and another Origin is refused with 403 on both (a Host that is not allowed with 421); the socket answers 200 pings (the loopback round trip is printed as a baseline).
The server is stopped in a `finally`, whatever fails, and SIGTERM must leave no process behind.

One line per step, ending `ok`; a failed step prints why and the script exits 1 with no `SW1 OK`.

    both fs_py scripts/smoke/sw1_serve_live.py
"""
import asyncio
import http.client
import json
import os
import queue
import re
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

TMP = tempfile.mkdtemp(prefix="sw1_serve_live_")
os.environ["FREESHAPE_DATA_DIR"] = os.path.join(TMP, "appdata")     # nothing may touch a real user folder
os.environ["XDG_DATA_HOME"] = os.path.join(TMP, "xdg")
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["FREESHAPE_PLUGIN_DIR"] = os.path.join(TMP, "plugins")
for _d in ("appdata", "xdg", "home", "plugins"):
    os.makedirs(os.path.join(TMP, _d), exist_ok=True)

import aiohttp  # noqa: E402

from freeshape.engine import wire  # noqa: E402

READY_TIMEOUT = 120
STOP_TIMEOUT = 60
HTTP_TIMEOUT = 60
PUBLIC = "https://cad.example.test"
PUBLIC_HOST = "cad.example.test"
CLIENT = "smoke-client-sw1"
EDIT = {"op": "set_param", "feature": "F5", "key": "diameter", "value": 7.0}


class Fail(Exception):
    pass


def check(cond, msg):
    if not cond:
        raise Fail(msg)


STEPS = []
SERVER = {}          # proc, port, stderr (list of lines)


def step(name):
    """Run a step function; print `name ... ok`, or the reason and exit 1 (the server is stopped by the caller)."""
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


def server_env():
    return dict(os.environ, PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""),
                QT_QPA_PLATFORM="offscreen")


def start_server(folder):
    proc = subprocess.Popen([sys.executable, "-m", "freeshape", "serve", folder, "--port", "0",
                             "--allow-origin", PUBLIC],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=TMP, env=server_env(),
                            start_new_session=True)
    SERVER.update(proc=proc, stderr=[])
    lines = queue.Queue()

    def pump(stream, sink):
        for line in iter(stream.readline, ""):          # drains for ever so a full pipe never stalls the server
            sink(line)

    threading.Thread(target=pump, args=(proc.stdout, lines.put), daemon=True).start()
    threading.Thread(target=pump, args=(proc.stderr, SERVER["stderr"].append), daemon=True).start()
    deadline = time.monotonic() + READY_TIMEOUT
    while True:
        try:
            line = lines.get(timeout=0.5)
        except queue.Empty:
            line = None
        if line is not None and line.startswith("FREESHAPE_SERVE_READY"):
            m = re.fullmatch(r"FREESHAPE_SERVE_READY port=(\d+) url=(http://127\.0\.0\.1:(\d+)/)", line.strip())
            check(m and m.group(1) == m.group(3), f"unexpected ready line {line!r}")
            SERVER["port"] = int(m.group(1))
            return
        if line is not None:
            raise Fail(f"unexpected stdout before the ready line: {line!r}")
        if proc.poll() is not None:
            time.sleep(0.3)
            raise Fail(f"the server exited {proc.returncode} before the ready line; stderr: {''.join(SERVER['stderr'])}")
        if time.monotonic() > deadline:
            raise Fail(f"no ready line within {READY_TIMEOUT} s; stderr: {''.join(SERVER['stderr'])[-500:]}")


def stop_server():
    """SIGTERM, bounded wait; whatever happens, kill what is left of the process group. Returns the exit code."""
    proc = SERVER.get("proc")
    if proc is None:
        return None
    code = None
    try:
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                code = proc.wait(STOP_TIMEOUT)
            except subprocess.TimeoutExpired:
                code = "timeout"
        else:
            code = proc.returncode
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)         # a no-op when the group is already empty
        except (ProcessLookupError, PermissionError):
            pass
        proc.wait()
        for s in (proc.stdout, proc.stderr):
            try:
                s.close()
            except Exception:                           # noqa: BLE001
                pass
        SERVER["proc"] = None
    return code


def group_alive(pgid):
    """Pids still in the process group (zombies do not count)."""
    out = set()
    for p in os.listdir("/proc"):
        if p.isdigit():
            try:
                with open(f"/proc/{p}/stat") as fh:
                    rest = fh.read().rsplit(")", 1)[1].split()
            except OSError:
                continue
            if int(rest[2]) == pgid and rest[0] != "Z":
                out.add(int(p))
    return out


# ---------------------------------------------------------------------- HTTP (http.client: exact Host and Origin)
def req(method, path, body=None, host=None, origin=None, headers=None):
    """(status, body bytes) with the Host and Origin headers exactly as given."""
    c = http.client.HTTPConnection("127.0.0.1", SERVER["port"], timeout=HTTP_TIMEOUT)
    try:
        data = None if body is None else json.dumps(body).encode()
        c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        c.putheader("Host", host or f"127.0.0.1:{SERVER['port']}")
        if origin is not None:
            c.putheader("Origin", origin)
        for k, v in (headers or {}).items():
            c.putheader(k, v)
        if data is not None:
            c.putheader("Content-Type", "application/json")
            c.putheader("Content-Length", str(len(data)))
        c.endheaders(data)
        r = c.getresponse()
        return r.status, r.read()
    finally:
        c.close()


def get_json(path, **kw):
    status, body = req("GET", path, **kw)
    check(status == 200, f"GET {path} -> {status} {body[:200]!r}")
    return json.loads(body)


# ---------------------------------------------------------------------- WebSocket (aiohttp client)
def run_async(coro):
    return asyncio.run(asyncio.wait_for(coro, 120))


async def next_of(ws, kind, timeout=60):
    """The next message of type `kind` (others are skipped)."""
    while True:
        msg = await asyncio.wait_for(ws.receive(), timeout)
        if msg.type != aiohttp.WSMsgType.TEXT:
            raise Fail(f"the socket gave {msg.type!r} while waiting for {kind!r}")
        data = json.loads(msg.data)
        if data.get("type") == kind:
            return data


def files_under(folder):
    return sorted(os.path.join(d, f) for d, _dirs, fs in os.walk(folder) for f in fs)


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
        left = group_alive(SERVER["proc"].pid) if SERVER.get("proc") else set()
        stop_server()
        shutil.rmtree(TMP, ignore_errors=True)
    if code == 0 and left:
        print(f"processes alive at exit: {sorted(left)}")
        code = 1
    if code == 0:
        print(f"SW1 OK: {len(STEPS)} steps")
    return code


def run():
    folder = os.path.join(TMP, "bracket")
    shutil.copytree(os.path.join(ROOT, "examples", "documents", "bracket"), folder)
    start_server(folder)
    port, proc = SERVER["port"], SERVER["proc"]
    state = {}

    @step("serve starts on a free port, /health says no Qt")
    def _():
        check(port > 0 and proc.poll() is None, "server not running")
        h = get_json("/health")
        check(h["ok"] is True and h["qt_imported"] is False and h["qt_modules"] == [], h)

    @step("the old /viewer page is gone (404)")
    def _():
        check(req("GET", "/viewer")[0] == 404, "/viewer is still served")

    @step("a WebSocket connects and hello names the Document, the tab and its revision")
    def _():
        async def go():
            async with aiohttp.ClientSession() as s, s.ws_connect(f"http://127.0.0.1:{port}/ws?client={CLIENT}") as ws:
                return await next_of(ws, "hello")
        hello = run_async(go())
        check(hello["client"] == CLIENT and hello["document"]["name"], hello)
        tabs = hello["tabs"]
        check(len(tabs) == 1 and isinstance(tabs[0]["revision"], int), tabs)
        state["tab"], state["before"] = tabs[0]["id"], tabs[0]["revision"]

    @step("a set_param edit posted over HTTP arrives as changed, and /outputs carries the new revision")
    def _():
        tab, before = state["tab"], state["before"]

        async def go():
            async with aiohttp.ClientSession() as s, s.ws_connect(f"http://127.0.0.1:{port}/ws?client={CLIENT}") as ws:
                await next_of(ws, "hello")
                async with s.post(f"http://127.0.0.1:{port}/tabs/{tab}/edit", json=EDIT,
                                  headers={"X-Client-Id": CLIENT, "X-Base-Revision": str(before)}) as resp:
                    check(resp.status == 200, f"edit -> {resp.status} {await resp.text()}")
                    check((await resp.json())["rebuilt"] == [tab], "the edit did not rebuild the tab")
                return await next_of(ws, "changed")
        changed = run_async(go())
        check(changed["rebuilt"] == [tab] and changed["client"] == CLIENT and changed["base"] == before, changed)
        after = changed["revisions"][tab]
        check(after > before, f"revision {before} -> {after}: not higher")
        status, body = req("GET", f"/tabs/{tab}/outputs")
        check(status == 200, status)
        check(wire.from_wire(body).revision == after, f"/outputs is at {wire.from_wire(body).revision}, changed said {after}")
        check(get_json("/tabs")["tabs"][0]["dirty"] is True, "the edited tab is not dirty")
        state["after"] = after

    @step("a save edit carrying a path is refused and no file is written")
    def _():
        tab = state["tab"]
        target = os.path.join(TMP, "written-by-save-edit.fshape")
        folder_before, tmp_before = files_under(folder), sorted(os.listdir(TMP))
        for edit in ({"op": "save", "path": target}, {"op": "save"}):
            status, body = req("POST", f"/tabs/{tab}/edit", edit)
            check(status == 400 and "save" in json.loads(body)["error"], f"{edit} -> {status} {body[:200]!r}")

        async def over_socket():
            async with aiohttp.ClientSession() as s, s.ws_connect(f"http://127.0.0.1:{port}/ws?client={CLIENT}") as ws:
                await next_of(ws, "hello")
                await ws.send_str(json.dumps({"type": "edit", "id": 7, "tab": tab, "edit": {"op": "save", "path": target}}))
                return await next_of(ws, "error")
        err = run_async(over_socket())
        check(err["id"] == 7 and "save" in err["message"], err)
        check(not os.path.exists(target), "the save edit wrote its path")
        check(files_under(folder) == folder_before, "a file of the Document changed or appeared")
        check(sorted(os.listdir(TMP)) == tmp_before, "a file appeared beside the Document")
        check(get_json(f"/tabs/{tab}/mesh.json")["revision"] == state["after"], "a refused edit moved the revision")

    @step("with --allow-origin, the allowed Host and Origin are accepted on HTTP")
    def _():
        check(req("GET", "/health", host=PUBLIC_HOST)[0] == 200, "allowed Host refused")
        check(req("GET", "/health", host=PUBLIC_HOST + ":443")[0] == 200, "allowed Host with :443 refused")
        check(req("GET", "/health", host=PUBLIC_HOST, origin=PUBLIC)[0] == 200, "allowed Host and Origin refused")
        check(req("GET", "/tabs", host=PUBLIC_HOST, origin=PUBLIC)[0] == 200, "/tabs with the allowed Origin refused")
        check(req("GET", "/health")[0] == 200, "loopback stopped working")

    @step("another Origin is refused with 403 and another Host with 421 on HTTP")
    def _():
        for origin in ("https://evil.example", "http://cad.example.test", "https://cad.example.test:8443"):
            for host in (PUBLIC_HOST, None):
                status = req("GET", "/health", host=host, origin=origin)[0]
                check(status == 403, f"Origin {origin!r} (Host {host!r}) -> {status}, want 403")
        check(req("POST", "/save", {}, host=PUBLIC_HOST, origin="https://evil.example")[0] == 403, "POST /save with a foreign Origin")
        check(req("GET", "/health", host="evil.example")[0] == 421, "a foreign Host is not 421")

    @step("the socket takes the allowed Host and Origin and refuses another Origin (403) and Host (421)")
    def _():
        async def go():
            url = f"http://127.0.0.1:{port}/ws?client={CLIENT}"
            async with aiohttp.ClientSession() as s:
                ws = await s.ws_connect(url, origin=PUBLIC, headers={"Host": PUBLIC_HOST})
                await next_of(ws, "hello")
                await ws.send_str(json.dumps({"type": "ping", "t": 4321.5}))
                check(await next_of(ws, "pong") == {"type": "pong", "t": 4321.5}, "no pong over the public origin")
                await ws.close()
                for kw, want in (({"origin": "https://evil.example"}, 403),
                                 ({"origin": PUBLIC, "headers": {"Host": "evil.example"}}, 421),
                                 ({"origin": "https://evil.example", "headers": {"Host": PUBLIC_HOST}}, 403)):
                    try:
                        await s.ws_connect(url, **kw)
                    except aiohttp.WSServerHandshakeError as e:
                        check(e.status == want, f"socket {kw} -> {e.status}, want {want}")
                    else:
                        raise Fail(f"the socket accepted {kw}")
        run_async(go())

    @step("200 pings over the socket come back with their own timestamp (loopback round trip printed)")
    def _():
        async def go():
            times = []
            async with aiohttp.ClientSession() as s, s.ws_connect(f"http://127.0.0.1:{port}/ws?client={CLIENT}") as ws:
                await next_of(ws, "hello")
                for i in range(200):
                    t0 = time.perf_counter()
                    await ws.send_str(json.dumps({"type": "ping", "t": i}))
                    check((await next_of(ws, "pong"))["t"] == i, f"pong {i} came back with another timestamp")
                    times.append((time.perf_counter() - t0) * 1000)
            return sorted(times)
        times = run_async(go())
        median = (times[99] + times[100]) / 2
        print(f"  (round trip over 200 pings, loopback: median {median:.2f} ms, worst {times[-1]:.2f} ms)")
        check(median < 50, f"median round trip {median:.1f} ms on loopback")

    @step("SIGTERM stops the server (exit 0) and every process of it")
    def _():
        code = stop_server()
        check(code == 0, f"server exit code {code!r}; stderr tail: {''.join(SERVER['stderr'])[-300:]}")
        check(not group_alive(proc.pid), f"processes left in the server's group: {sorted(group_alive(proc.pid))}")
        check(not any(m.startswith(("PySide6", "PyQt", "shiboken")) for m in sys.modules), "this script imported Qt")


if __name__ == "__main__":
    sys.exit(main())
