"""Headless serve smoke (slice S5, gate I5.6): `python -m freeshape serve` over loopback HTTP, no Qt, no network.

Part of the cumulative smoke gate (see s00_baseline.py for the convention). In a temporary folder it builds a
Document (a Variable Studio, two Part Studios and an Assembly that instances both; the builder is
tests/test_document_session._layout_document), reads the engine's own outputs for it in this process, then starts
`sys.executable -m freeshape serve <folder> --port 0` as a subprocess in its own session and walks the slice claim
one clause per step over HTTP (urllib, no proxy, 127.0.0.1): the server runs with no Qt module and listens on
127.0.0.1 only (ready line, a connection, the listening socket in /proc/net/tcp, a foreign Host header refused);
the tab list names every tab with its kind; a Part Studio's and the Assembly's outputs in the wire format decode to
arrays equal to the engine's own; the mesh JSON has consistent lengths; one `set_param` edit rebuilds the studio
and the Assembly once each and changes both meshes; the viewer page is served and references only its own origin;
a request to bind 0.0.0.0 or :: is refused; and SIGTERM stops the server and every worker with no process or port
left. The server is stopped in a `finally`, whatever fails.

One line per step, ending `ok`; a failed step prints why and the script exits 1 with no `SQ5 OK`.

    both fs_py scripts/smoke/sq5_serve.py
"""
import http.client
import json
import os
import queue
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

# A multiprocessing worker re-imports this file as __mp_main__: it must reuse the parent's folder, not make its own.
TMP = os.environ.get("SQ5_SERVE_TMP") or tempfile.mkdtemp(prefix="sq5_serve_")
os.environ["SQ5_SERVE_TMP"] = TMP
os.environ["FREESHAPE_DATA_DIR"] = os.path.join(TMP, "appdata")     # nothing may touch a real user folder
os.environ["XDG_DATA_HOME"] = os.path.join(TMP, "xdg")
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["FREESHAPE_PLUGIN_DIR"] = os.path.join(TMP, "plugins")
for _d in ("appdata", "xdg", "home", "plugins"):
    os.makedirs(os.path.join(TMP, _d), exist_ok=True)

from test_document_session import _layout_document  # noqa: E402
from test_engine_wire import same  # noqa: E402

from freeshape import docstore as DS  # noqa: E402
from freeshape.engine import process as P  # noqa: E402
from freeshape.engine import wire  # noqa: E402
from freeshape.session import DocumentSession  # noqa: E402

READY_TIMEOUT = 120
STOP_TIMEOUT = 60
HTTP_TIMEOUT = 60
LOOPBACK_HEX = "0100007F"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Fail(Exception):
    pass


def check(cond, msg):
    if not cond:
        raise Fail(msg)


STEPS = []
SERVER = {}          # proc, port, stderr (list of lines), workers (set of pids seen)


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


# ---------------------------------------------------------------------- process and socket helpers
def _stat(pid):
    """(state, ppid, pgrp) of a live process, or None."""
    try:
        with open(f"/proc/{pid}/stat") as fh:
            rest = fh.read().rsplit(")", 1)[1].split()
    except OSError:
        return None
    return rest[0], int(rest[1]), int(rest[2])


def alive(pid):
    s = _stat(pid)
    return s is not None and s[0] != "Z"


def descendants(pid):
    kids = set()
    for p in os.listdir("/proc"):
        if p.isdigit():
            s = _stat(p)
            if s and (s[1] == pid or s[1] in kids):
                kids.add(int(p))
    return kids


def _cmd(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return fh.read().replace(b"\0", b" ").decode(errors="replace")[:100]
    except OSError:
        return "?"


def group_members(pgid):
    out = set()
    for p in os.listdir("/proc"):
        if p.isdigit():
            s = _stat(p)
            if s and s[2] == pgid and s[0] != "Z":
                out.add(int(p))
    return out


def listeners(pid=None):
    """{(local address hex, port)} of every listening TCP socket (IPv4 and IPv6) in the network namespace."""
    out = set()
    for name in ("tcp", "tcp6"):
        path = f"/proc/{pid or 'self'}/net/{name}"
        try:
            with open(path) as fh:
                rows = fh.read().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            f = row.split()
            if f[3] == "0A":
                addr, port = f[1].rsplit(":", 1)
                out.add((addr, int(port, 16), name))
    return out


def can_connect(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=2).close()
        return True
    except OSError:
        return False


def server_env():
    return dict(os.environ, PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""),
                QT_QPA_PLATFORM="offscreen")


# ---------------------------------------------------------------------- HTTP helpers
def req(method, path, body=None, headers=None):
    """(status, headers, body bytes) over urllib; an HTTP error status is returned, not raised."""
    data = None if body is None else json.dumps(body).encode()
    h = dict(headers or {})
    if method == "POST":
        h.setdefault("Content-Type", "application/json")
    r = urllib.request.Request(f"http://127.0.0.1:{SERVER['port']}{path}", data=data, headers=h, method=method)
    try:
        with OPENER.open(r, timeout=HTTP_TIMEOUT) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def get_json(path):
    status, _h, body = req("GET", path)
    check(status == 200, f"GET {path} -> {status} {body[:200]!r}")
    return json.loads(body)


def check_mesh(m, where):
    pos, nor, idx = m["positions"], m["normals"], m["indices"]
    check(len(pos) == len(nor) and len(pos) % 3 == 0 and len(pos) > 0, f"{where}: positions/normals lengths")
    check(len(idx) % 3 == 0 and len(idx) > 0, f"{where}: indices length")
    nv, nt = len(pos) // 3, len(idx) // 3
    check(all(isinstance(i, int) and 0 <= i < nv for i in idx), f"{where}: an index is out of range")
    check(len(m["face_ids"]) == nt, f"{where}: face_ids has {len(m['face_ids'])} entries for {nt} triangles")
    covered = []
    for fid, (a, b) in enumerate(m["face_ranges"], start=1):
        check(0 <= a <= b <= nt, f"{where}: face range {a},{b} outside 0..{nt}")
        check(all(m["face_ids"][t] == fid for t in range(a, b)), f"{where}: face_ids disagree with face {fid}")
        covered.extend(range(a, b))
    check(sorted(covered) == list(range(nt)), f"{where}: face ranges do not cover every triangle exactly once")
    check(all(len(e) % 3 == 0 for e in m["edges"]) and len(m["vertices"]) % 3 == 0, f"{where}: edges/vertices")


def check_doc(doc, kind, n):
    check(doc["kind"] == kind and len(doc["meshes"]) == n, f"{kind}: {doc['kind']} with {len(doc['meshes'])} meshes")
    for e in doc["meshes"]:
        check(e["mesh"] is not None, f"{kind}: entry {e['name']!r} has no mesh")
        check_mesh(e["mesh"], f"{kind} {e['name']}")
        if kind == "assembly":
            check(not e["hidden"] and len(e["matrix"]) == 16 and all(isinstance(x, float) for x in e["matrix"]),
                  f"assembly instance {e['name']!r}: matrix {e['matrix']!r}")
        else:
            check(e["matrix"] is None, "a studio entry has a matrix")


def start_server(folder):
    proc = subprocess.Popen([sys.executable, "-m", "freeshape", "serve", folder, "--port", "0"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=TMP, env=server_env(),
                            start_new_session=True)
    SERVER.update(proc=proc, stderr=[], workers=set())
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
            SERVER["ready"] = line.strip()
            m = re.fullmatch(r"FREESHAPE_SERVE_READY port=(\d+) url=(http://127\.0\.0\.1:(\d+)/)", line.strip())
            check(m and m.group(1) == m.group(3), f"unexpected ready line {line!r}")
            SERVER["port"], SERVER["url"] = int(m.group(1)), m.group(2)
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
            SERVER["workers"] |= descendants(proc.pid)
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


def main():
    state = {}
    try:
        run(state)
    except SystemExit as e:
        code = e.code
    except BaseException:                               # noqa: BLE001
        traceback.print_exc()
        code = 1
    else:
        code = 0
    finally:
        left = group_members(SERVER["proc"].pid) if SERVER.get("proc") else set()
        stop_server()
        P.SPARES.clear()
        shutil.rmtree(TMP, ignore_errors=True)
    if code == 0 and left:
        print(f"processes alive at exit: {sorted(left)}")
        code = 1
    if code == 0:
        print(f"SQ5 OK: {len(STEPS)} steps")
    return code


def run(state):
    # ------------------------------------------------------------------ setup: the Document and the engine's own outputs
    folder = os.path.join(TMP, "Doc")
    _fd, ids, ext = _layout_document(DS.FolderStore(folder))
    with DocumentSession(folder, spares=0) as own:
        engine = {"a": own.outputs(ids["a"]), "asm": own.outputs(ids["asm"])}
    P.SPARES.clear()
    stray = [p for p in descendants(os.getpid()) if alive(p) and "resource_tracker" not in _cmd(p)]   # Python's own helper
    check(not stray, f"the in-process session left a worker behind: {[(p, _cmd(p)) for p in stray]}")

    start_server(folder)
    port, proc = SERVER["port"], SERVER["proc"]

    @step("serve starts on a free port and prints the ready line")
    def _():
        check(SERVER["ready"] == f"FREESHAPE_SERVE_READY port={port} url=http://127.0.0.1:{port}/", SERVER["ready"])
        check(port > 0 and proc.poll() is None, "server not running")

    @step("serve answers on 127.0.0.1 only: connection, listening socket, foreign Host refused")
    def _():
        check(can_connect(port), "no connection on 127.0.0.1")
        mine = {(a, p, n) for a, p, n in listeners(proc.pid) if p == port}
        check(mine == {(LOOPBACK_HEX, port, "tcp")}, f"listening sockets on the port: {sorted(mine)} (want only 127.0.0.1)")
        check(not any(a != LOOPBACK_HEX for a, p, _n in listeners(proc.pid) if p == port), "a wildcard listener")
        for host in ("evil.example", f"evil.example:{port}"):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=HTTP_TIMEOUT)
            try:
                c.putrequest("GET", "/health", skip_host=True)
                c.putheader("Host", host)
                c.endheaders()
                status = c.getresponse().status
            finally:
                c.close()
            check(status == 421, f"Host {host!r} -> {status}, want 421")
        check(req("GET", "/health")[0] == 200, "/health with the right Host is not 200")

    @step("no Qt module in the server (/health) or in this script")
    def _():
        h = get_json("/health")
        check(h["ok"] is True and h["qt_imported"] is False and h["qt_modules"] == [], h)
        check(not _qt(), f"this script imported {_qt()}")

    @step("tab list names every tab with its kind")
    def _():
        t = get_json("/tabs")
        got = [(x["id"], x["kind"], x["state"]) for x in t["tabs"]]
        want = [(ids["vars"], "variables", "ok"), (ids["a"], "studio", "ok"), (ids["b"], "studio", "ok"),
                (ids["asm"], "assembly", "ok")]
        check(got == want, f"tabs {got} != {want}")
        check(all(x["name"] for x in t["tabs"]) and not any(x["dirty"] for x in t["tabs"]), t["tabs"])

    @step("Part Studio outputs in the wire format equal the engine's own")
    def _():
        status, h, body = req("GET", f"/tabs/{ids['a']}/outputs")
        check(status == 200 and h["Content-Type"] == "application/octet-stream", (status, h.get("Content-Type")))
        same(wire.from_wire(body), engine["a"])
        state["revision_wire"] = wire.from_wire(body).revision

    @step("Assembly outputs in the wire format equal the engine's own")
    def _():
        status, _h, body = req("GET", f"/tabs/{ids['asm']}/outputs")
        check(status == 200, status)
        same(wire.from_wire(body), engine["asm"])

    @step("Part Studio mesh JSON: positions, normals, indices, face ranges consistent")
    def _():
        doc = get_json(f"/tabs/{ids['a']}/mesh.json")
        check(doc["tab"] == ids["a"], doc["tab"])
        check_doc(doc, "studio", 1)
        state["a0"] = doc

    @step("Assembly mesh JSON: consistent meshes, a 16-number matrix per instance")
    def _():
        doc = get_json(f"/tabs/{ids['asm']}/mesh.json")
        check_doc(doc, "assembly", 2)
        check([e["name"] for e in doc["meshes"]] == ["Plate A <1>", "Plate B <1>"], [e["name"] for e in doc["meshes"]])
        state["asm0"] = doc

    @step("set_param edit rebuilds the studio then the Assembly, once each")
    def _():
        status, _h, body = req("POST", f"/tabs/{ids['a']}/edit",
                               {"op": "set_param", "feature": ext["a"], "key": "depth", "value": 5})
        check(status == 200, f"edit -> {status} {body[:200]!r}")
        rebuilt = json.loads(body)["rebuilt"]
        check(rebuilt == [ids["a"], ids["asm"]], f"rebuilt {rebuilt}, want [studio, assembly] once each")

    @step("both meshes changed, Plate B did not, a second GET is stable")
    def _():
        a1 = get_json(f"/tabs/{ids['a']}/mesh.json")
        asm1 = get_json(f"/tabs/{ids['asm']}/mesh.json")
        check_doc(a1, "studio", 1)
        check_doc(asm1, "assembly", 2)
        check(a1["meshes"][0]["mesh"] != state["a0"]["meshes"][0]["mesh"], "the studio mesh did not change")
        check(asm1["meshes"][0]["mesh"] != state["asm0"]["meshes"][0]["mesh"], "the Assembly's Plate A mesh did not change")
        check(asm1["meshes"][1]["mesh"] == state["asm0"]["meshes"][1]["mesh"], "Plate B's mesh changed")
        p = a1["meshes"][0]["mesh"]["positions"]
        ext_z = sorted(round(max(p[i::3]) - min(p[i::3])) for i in range(3))
        check(ext_z == [5, 10, 10], f"studio extent after depth 5: {ext_z}")
        check(get_json(f"/tabs/{ids['a']}/mesh.json") == a1, "a second GET changed the studio mesh")
        check(get_json(f"/tabs/{ids['asm']}/mesh.json") == asm1, "a second GET changed the Assembly mesh")
        print(f"  (revision before/after the edit: studio {state['a0']['revision']}/{a1['revision']}, "
              f"assembly {state['asm0']['revision']}/{asm1['revision']})")

    @step("viewer page is served with a strict Content-Security-Policy")
    def _():
        status, h, body = req("GET", "/")
        check(status == 200 and h["Content-Type"].startswith("text/html"), (status, h.get("Content-Type")))
        csp = h.get("Content-Security-Policy", "")
        check("connect-src 'self'" in csp and "default-src 'none'" in csp, f"CSP {csp!r}")
        state["page"] = body.decode("utf-8")

    @step("viewer page references only same-origin URLs")
    def _():
        page = state["page"]
        check(not re.search(r"https?:", page, re.I), "an http(s) URL in the page")
        check(not re.search(r"""["'`(=,]\s*//[\w.-]""", page), "a scheme-relative URL in the page")
        check("@import" not in page and "url(" not in page, "@import or url( in the page")
        for m in re.finditer(r"""\b(src|href)\s*=\s*["']?([^"'\s>]*)""", page, re.I):
            target = m.group(2)
            check(target.startswith("/") and not target.startswith("//") or target.startswith("#"),
                  f"{m.group(1)}= points off-origin: {target!r}")

    @step("a request to bind 0.0.0.0 or :: is refused and nothing listens")
    def _():
        before = listeners()
        for host in ("0.0.0.0", "::"):
            t0 = time.monotonic()
            done = subprocess.run([sys.executable, "-m", "freeshape", "serve", folder, "--host", host, "--port", "0"],
                                  capture_output=True, text=True, timeout=120, cwd=TMP, env=server_env())
            check(done.returncode != 0, f"--host {host} exited 0")
            check("authentication that does not exist yet" in done.stderr, f"--host {host}: stderr {done.stderr!r}")
            check(done.stdout == "", f"--host {host} printed on stdout: {done.stdout!r}")
            check(time.monotonic() - t0 < 60, f"--host {host} took too long to refuse")
        check(listeners() == before, f"listening sockets changed: {sorted(before ^ listeners())}")

    @step("SIGTERM stops the server (exit 0), every worker, and the port")
    def _():
        state["workers"] = descendants(proc.pid)
        check(state["workers"], "no worker processes to watch")
        code = stop_server()
        check(code == 0, f"server exit code {code!r}; stderr tail: {''.join(SERVER['stderr'])[-300:]}")
        left = [p for p in state["workers"] | SERVER["workers"] if alive(p)]
        check(not left, f"worker processes left alive: {left}")
        check(not group_members(proc.pid), f"processes left in the server's group: {sorted(group_members(proc.pid))}")
        check(not can_connect(port), "the port still accepts a connection")
        check(not [p for p in listeners() if p[1] == port], "a socket still listens on the port")

    @step("this script imported no Qt module")
    def _():
        check(not _qt(), f"imported {_qt()}")
        check(len(STEPS) >= 7, "fewer than 7 steps")


def _qt():
    return sorted(m for m in sys.modules if m.startswith(("PySide6", "PyQt", "shiboken")))


if __name__ == "__main__":
    sys.exit(main())
