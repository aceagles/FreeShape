"""I2.3: `freeshape serve` over a folder of Documents (freeshape/serve.py): the registry-backed list, create, open,
close and take-over routes, the Document lock, the Document-keyed routes and socket, hostile ids and names, and the
single-Document aliases. Each test makes its own root, registry and server, so it passes alone."""
import asyncio
import http.client
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import aiohttp
import pytest

from freeshape import doc_folder as DF
from freeshape import doc_lock
from freeshape import doc_registry as R
from freeshape import serve as SV
from freeshape.engine import process as P
from freeshape.session import DocumentSession

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRACKET = os.path.join(ROOT, "examples", "documents", "bracket")
EDIT = {"op": "set_param", "feature": "F5", "key": "diameter", "value": 7.0}
CLIENT = "client-aaaa1111"


class _Root:
    """A Documents root and data directory under tmp_path, and an in-process server over their registry."""

    def __init__(self, tmp_path):
        self.root = str(tmp_path / "Documents")
        self.data = str(tmp_path / "data")
        os.makedirs(self.root)
        self.registry = R.DocRegistry(os.path.join(self.data, R.REGISTRY_NAME))
        self.server = SV.make_server(None, 0, registry=self.registry, root=self.root)
        self.port = self.server.port
        self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        assert self.server.wait_started(30)

    def bracket(self, name="Bracket", doc_id=None):
        """A copy of the example bracket Document in the root, registered the way the desktop would (under its own
        manifest id, or under `doc_id`, which is written into the copy); (its id, its real path)."""
        path = os.path.join(self.root, name)
        shutil.copytree(BRACKET, path)
        with open(os.path.join(path, "document.json")) as fh:
            manifest = json.load(fh)
        if doc_id is not None:
            manifest["id"], manifest["name"] = doc_id, name
            with open(os.path.join(path, "document.json"), "w") as fh:
                json.dump(manifest, fh)
        doc_id = manifest["id"]
        self.registry.register_created(doc_id, path)
        return doc_id, os.path.realpath(path)

    def request(self, method, path, body=None, headers=None, raw=None):
        """(status, parsed JSON or bytes). `body` is JSON-encoded unless `raw` bytes are given."""
        h = {"Host": f"127.0.0.1:{self.port}"}
        if method == "POST":
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        data = raw if raw is not None else (json.dumps(body if body is not None else {}).encode()
                                            if method == "POST" else None)
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=90)
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

    def ok(self, method, path, body=None, headers=None):
        status, payload = self.request(method, path, body, headers)
        assert status == 200, (method, path, status, payload)
        return payload

    def on_loop(self, fn):
        async def call():
            return fn()
        return asyncio.run_coroutine_threadsafe(call(), self.server._loop).result(10)

    def close(self):
        try:
            SV.close_all(self.server, None)
        finally:
            P.SPARES.clear()


@pytest.fixture
def root(tmp_path):
    made = []

    def make():
        r = _Root(tmp_path)
        made.append(r)
        return r
    try:
        yield make
    finally:
        for r in made:
            r.close()


def _env(tmp_path):
    return dict(os.environ, PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""),
                QT_QPA_PLATFORM="offscreen", FREESHAPE_PLUGIN_DIR=str(tmp_path / "plugins"))


def _tree(path):
    return sorted(os.path.join(d, n) for d, ds, fs in os.walk(path) for n in ds + fs)


# ---------------------------------------------------------------------------------------------- create and list
def test_post_documents_creates_a_folder_doc_folder_reads_back_and_the_list_shows_it(root):
    r = root()
    assert r.ok("GET", "/documents") == {"documents": []}
    status, made = r.request("POST", "/documents", {"name": "Widget"})
    assert status == 201 and made["name"] == "Widget"
    folder = os.path.join(r.root, "Widget")
    assert os.path.isdir(folder)
    doc = DF.FolderDocument.open(folder)
    try:
        assert doc.id == made["id"] and doc.name == "Widget" and [t.kind for t in doc.tabs] == ["studio"]
    finally:
        doc.close()
    (row,) = r.ok("GET", "/documents")["documents"]
    assert row["id"] == made["id"] and row["name"] == "Widget"
    assert row["holder"] is None and row["open"] is False and row["missing"] is False
    assert isinstance(row["modified"], float)
    # the same name again gets its own folder
    status, again = r.request("POST", "/documents", {"name": "Widget"})
    assert status == 201 and again["id"] != made["id"] and os.path.isdir(os.path.join(r.root, "Widget 2"))


def test_a_document_made_by_the_server_is_found_by_a_registry_on_the_same_data_dir(root):
    r = root()
    _, made = r.request("POST", "/documents", {"name": "Found"})
    desktop = R.DocRegistry(os.path.join(r.data, R.REGISTRY_NAME))           # what the desktop builds for that dir
    assert desktop.find(made["id"]) == os.path.realpath(os.path.join(r.root, "Found"))
    assert desktop.trusted_path(made["id"]) == desktop.find(made["id"])
    assert [d["id"] for d in desktop.list_documents()] == [made["id"]]
    assert desktop.describe(made["id"])["tabs"][0]["kind"] == "studio"


def test_the_list_shows_the_lock_holder(root):
    r = root()
    doc_id, folder = r.bracket()
    lock = doc_lock.acquire(folder, "desktop")
    try:
        (row,) = r.ok("GET", "/documents")["documents"]
        assert row["holder"]["program"] == "desktop" and row["holder"]["stale"] is False
    finally:
        lock.release()


# ---------------------------------------------------------------------------------------------- open and close
def test_open_lists_the_tabs_and_close_releases_the_lock_file(root):
    r = root()
    doc_id, folder = r.bracket()
    opened = r.ok("POST", f"/documents/{doc_id}/open")
    assert opened["document"]["id"] == doc_id and opened["read_only"] is False and opened["holder"] is None
    tabs = r.ok("GET", f"/documents/{doc_id}/tabs")
    assert [t["id"] for t in tabs["tabs"]] == ["T1"] and tabs["read_only"] is False
    held = doc_lock.holder(folder)
    assert held is not None and held.program == "browser" and not held.stale
    assert r.ok("GET", "/documents")["documents"][0]["open"] is True
    assert r.ok("POST", f"/documents/{doc_id}/open")["document"]["id"] == doc_id      # opening again is fine
    assert r.ok("POST", f"/documents/{doc_id}/close") == {"closed": True}
    assert doc_lock.holder(folder) is None and not os.path.exists(doc_lock.lock_path(folder))
    status, body = r.request("GET", f"/documents/{doc_id}/tabs")
    assert status == 409 and "not open" in body["error"]


def test_a_second_open_is_refused_naming_the_open_one_until_it_is_closed(root):
    r = root()
    first, _ = r.bracket("One", "doc-one-0001")
    second, folder2 = r.bracket("Two", "doc-two-0001")
    r.ok("POST", f"/documents/{first}/open")
    status, body = r.request("POST", f"/documents/{second}/open")
    assert status == 409 and body["open"]["id"] == first and "One" in body["error"]
    assert doc_lock.holder(folder2) is None                                   # the refused one took no lock
    r.ok("POST", f"/documents/{first}/close")
    assert r.ok("POST", f"/documents/{second}/open")["document"]["name"] == "Two"


# ---------------------------------------------------------------------------------------------- the lock
def test_with_the_lock_held_elsewhere_open_is_read_only_edits_are_423_and_take_over_allows_them(root):
    r = root()
    doc_id, folder = r.bracket()
    desktop = doc_lock.acquire(folder, "desktop")
    opened = r.ok("POST", f"/documents/{doc_id}/open")
    assert opened["read_only"] is True and opened["holder"]["program"] == "desktop"
    assert len(r.ok("GET", f"/documents/{doc_id}/tabs/T1/mesh.json")["meshes"]) >= 1      # reads work
    assert r.request("GET", f"/documents/{doc_id}/tabs/T1/outputs")[0] == 200
    status, body = r.request("POST", f"/documents/{doc_id}/tabs/T1/edit", EDIT)
    assert status == 423 and body["holder"]["program"] == "desktop" and "desktop" in body["error"]
    status, body = r.request("POST", f"/documents/{doc_id}/save", {})
    assert status == 423 and body["holder"]["program"] == "desktop"
    assert doc_lock.holder(folder).instance == desktop.instance                # we never touched their lock
    taken = r.ok("POST", f"/documents/{doc_id}/take-over")
    assert taken == {"read_only": False, "holder": None}
    assert doc_lock.holder(folder).program == "browser" and not desktop.still_mine()
    assert r.ok("POST", f"/documents/{doc_id}/tabs/T1/edit", EDIT)["rebuilt"] == ["T1"]
    assert r.ok("POST", f"/documents/{doc_id}/save", {})["saved"] == ["T1"]
    assert r.ok("POST", f"/documents/{doc_id}/take-over") == {"read_only": False, "holder": None}   # already ours


def test_a_lock_taken_over_while_open_stops_edits_at_once_and_the_refresh_reports_it(root):
    r = root()
    doc_id, folder = r.bracket()
    r.ok("POST", f"/documents/{doc_id}/open")
    assert r.server.refresh_locks_now() == {doc_id: True}                    # a refresh while the lock is ours
    assert r.ok("POST", f"/documents/{doc_id}/tabs/T1/edit", EDIT)["rebuilt"] == ["T1"]
    doc_lock.take_over(folder, "desktop")

    async def go():
        async with aiohttp.ClientSession() as s, s.ws_connect(f"{r.base}/ws?client={CLIENT}&document={doc_id}") as ws:
            assert (await _next(ws, "hello"))["read_only"] is False
            assert r.server.refresh_locks_now() == {doc_id: False}
            lock = await _next(ws, "lock")
            assert lock["document"] == doc_id and lock["read_only"] is True and lock["holder"]["program"] == "desktop"
    asyncio.run(asyncio.wait_for(go(), 60))
    status, body = r.request("POST", f"/documents/{doc_id}/tabs/T1/edit", EDIT)
    assert status == 423 and body["holder"]["program"] == "desktop"
    r.ok("POST", f"/documents/{doc_id}/close")
    assert doc_lock.holder(folder).program == "desktop"                       # close never removes their lock


def test_a_take_over_between_refreshes_is_seen_by_the_next_edit(root):
    r = root()
    doc_id, folder = r.bracket()
    r.ok("POST", f"/documents/{doc_id}/open")
    doc_lock.take_over(folder, "desktop")                                    # no refresh has run
    assert r.request("POST", f"/documents/{doc_id}/tabs/T1/edit", EDIT)[0] == 423


# ---------------------------------------------------------------------------------------------- hostile input
HOSTILE_NAMES = ["../x", "../../outside", "..", "...", "/etc/passwd", "a/b", "a\\b", "C:\\x", "..\\x", "", "   ",
                 "x" * 1000, "bad\x00name", "tab\there", "line\nbreak", "\u202e", 5, None, ["a"], {"a": 1}]


def test_a_hostile_document_name_is_refused_and_nothing_is_created(root, tmp_path):
    r = root()
    before_root, before_parent = _tree(r.root), sorted(os.listdir(tmp_path))
    for name in HOSTILE_NAMES:
        status, body = r.request("POST", "/documents", {"name": name})
        assert status == 400 and "error" in body, (name, status, body)
    assert r.request("POST", "/documents", {})[0] == 400
    assert _tree(r.root) == before_root and sorted(os.listdir(tmp_path)) == before_parent
    assert not os.path.exists(os.path.join(r.data, R.REGISTRY_NAME))          # nothing was registered


def test_odd_but_legal_names_get_a_plain_folder_directly_under_the_root(root):
    r = root()
    for name, folder in [("  Brackets & Hinges  ", "Brackets - Hinges"), ("Caf\u00e9 .v2", "Caf\u00e9 .v2"),
                         (".hidden", "hidden"), ("a" * 100, "a" * 80)]:
        status, made = r.request("POST", "/documents", {"name": name})
        assert status == 201, (name, status, made)
        assert os.path.isdir(os.path.join(r.root, folder)), (name, os.listdir(r.root))
    assert sorted(os.listdir(r.root)) == sorted(["Brackets - Hinges", "Caf\u00e9 .v2", "hidden", "a" * 80])


def test_folder_name_for_never_returns_a_separator_or_a_leading_dot():
    for name in ["a", "A b", "x.y", "-x-", "..x", "x..", "q?*<>|", "\u00fcber", "a  b", "a--b"]:
        stem = SV.folder_name_for(name)
        assert stem and "/" not in stem and "\\" not in stem and not stem.startswith(".") and stem == stem.strip()
    for name in ["", " ", "..", "a/b", "a\\b", "a:b", "x" * 101, "\x00", "?*", None, 3]:
        with pytest.raises(ValueError):
            SV.folder_name_for(name)


def test_a_hostile_document_id_never_reaches_a_path_or_the_registry(root, tmp_path):
    r = root()
    doc_id, folder = r.bracket()
    before = _tree(tmp_path)
    ids = ["..", ".", "%2e%2e", "..%2fetc", "..%2f..%2fetc%2fpasswd", "a%2fb", "a%20b", "x" * 65, "id\\x", "%00",
           "caf%C3%A9", "-" * 64, "nope", "A" * 64]
    for ident in ids:
        for method, tail in [("GET", "/tabs"), ("POST", "/open"), ("POST", "/close"), ("POST", "/take-over"),
                             ("POST", "/save"), ("GET", "/tabs/T1/outputs"), ("POST", "/tabs/T1/edit")]:
            status, body = r.request(method, f"/documents/{ident}{tail}", {})
            assert status == 404, (ident, method, tail, status, body)
    for path in ["/documents/", "/documents/../tabs", "/documents/" + doc_id, "/documents/" + doc_id + "/",
                 f"/documents/{doc_id}/tabs/../open", f"/documents/{doc_id}/tabs/T1/nope",
                 f"/documents/{doc_id}/tabs/%2e%2e/outputs", "/documents//tabs"]:
        assert r.request("GET", path)[0] == 404, path
    assert _tree(tmp_path) == before and doc_lock.holder(folder) is None
    assert r.ok("GET", "/documents")["documents"][0]["open"] is False


def test_the_new_routes_check_host_origin_body_type_and_size(root):
    r = root()
    doc_id, _ = r.bracket()
    routes = [("GET", "/documents"), ("POST", "/documents"), ("POST", f"/documents/{doc_id}/open"),
              ("POST", f"/documents/{doc_id}/close"), ("POST", f"/documents/{doc_id}/take-over"),
              ("GET", f"/documents/{doc_id}/tabs"), ("POST", f"/documents/{doc_id}/save")]
    for method, path in routes:
        assert r.request(method, path, headers={"Host": "evil.example"})[0] == 421, path
        assert r.request(method, path, headers={"Origin": "https://evil.example"})[0] == 403, path
    assert r.request("POST", "/documents", headers={"Content-Type": "text/plain"}, raw=b'{"name": "a"}')[0] == 415
    assert r.request("POST", "/documents", raw=b"{not json")[0] == 400
    assert r.request("POST", "/documents", raw=b'["name"]')[0] == 400
    assert r.request("POST", "/documents", raw=b'{"name": "' + b"a" * (SV.MAX_BODY + 1) + b'"}')[0] == 413
    assert r.request("PUT", "/documents")[0] == 405 and r.request("DELETE", f"/documents/{doc_id}/close")[0] == 405
    assert r.request("GET", f"/documents/{doc_id}/open")[0] == 405
    assert r.request("PUT", "/documents/" + doc_id + "/tabs")[0] == 405
    assert os.listdir(r.root) == ["Bracket"]


def test_the_old_unprefixed_routes_do_not_exist_on_a_documents_root(root):
    r = root()
    doc_id, _ = r.bracket()
    r.ok("POST", f"/documents/{doc_id}/open")
    for method, path in [("GET", "/tabs"), ("GET", "/tabs/T1/outputs"), ("POST", "/tabs/T1/edit"),
                         ("POST", "/save")]:
        assert r.request(method, path, {})[0] == 404, path


# ---------------------------------------------------------------------------------------------- the socket
async def _next(ws, kind, timeout=30):
    while True:
        msg = await asyncio.wait_for(ws.receive(), timeout)
        assert msg.type == aiohttp.WSMsgType.TEXT, msg
        data = json.loads(msg.data)
        if data.get("type") == kind:
            return data


def test_the_socket_subscribes_by_document_id_and_every_message_names_it(root):
    r = root()
    doc_id, _ = r.bracket()
    r.ok("POST", f"/documents/{doc_id}/open")

    async def go():
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(f"{r.base}/ws?client={CLIENT}") as bare, \
                    s.ws_connect(f"{r.base}/ws?client=client-bbbb2222&document={doc_id}") as ws:
                hello = await _next(bare, "hello")
                assert hello["document"] is None and hello["tabs"] == []        # a root has no implicit Document
                await bare.send_json({"type": "edit", "id": 1, "tab": "T1", "edit": EDIT})
                assert "name an open Document" in (await _next(bare, "error"))["message"]
                await bare.send_json({"type": "subscribe", "document": "nope"})
                assert (await _next(bare, "error"))["document"] == "nope"
                hello = await _next(ws, "hello")
                assert hello["document"]["id"] == doc_id and [t["id"] for t in hello["tabs"]] == ["T1"]
                await bare.send_json({"type": "edit", "id": 2, "document": doc_id, "tab": "T1", "edit": EDIT})
                # the bare socket is not subscribed, but an edit that names the Document is still an edit of it
                edited = await _next(bare, "edited")
                assert edited["id"] == 2 and edited["document"] == doc_id
                assert (await _next(ws, "changed"))["document"] == doc_id          # and the subscriber hears of it
    asyncio.run(asyncio.wait_for(go(), 90))


def test_a_ws_edit_and_the_close_reach_only_the_subscribers_of_that_document(root):
    r = root()
    doc_id, _ = r.bracket()
    r.ok("POST", f"/documents/{doc_id}/open")

    async def go():
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(f"{r.base}/ws?client={CLIENT}&document={doc_id}") as ws, \
                    s.ws_connect(f"{r.base}/ws?client=client-bbbb2222") as other:
                await _next(ws, "hello")
                await _next(other, "hello")
                await ws.send_json({"type": "edit", "id": 7, "document": doc_id, "tab": "T1", "edit": EDIT})
                changed = await _next(ws, "changed")                                # pushed before the reply
                assert changed["document"] == doc_id and changed["client"] == CLIENT
                edited = await _next(ws, "edited")
                assert edited["id"] == 7 and edited["document"] == doc_id and edited["rebuilt"] == ["T1"]
                await other.send_json({"type": "subscribe", "document": doc_id})
                assert (await _next(other, "hello"))["document"]["id"] == doc_id      # nothing earlier was queued for it
                await asyncio.get_running_loop().run_in_executor(None, r.ok, "POST", f"/documents/{doc_id}/close")
                assert (await _next(ws, "closed"))["document"] == doc_id
                assert (await _next(other, "closed"))["document"] == doc_id
    asyncio.run(asyncio.wait_for(go(), 90))


def test_a_ws_edit_on_a_read_only_document_is_a_locked_error(root):
    r = root()
    doc_id, folder = r.bracket()
    lock = doc_lock.acquire(folder, "desktop")
    r.ok("POST", f"/documents/{doc_id}/open")

    async def go():
        async with aiohttp.ClientSession() as s, s.ws_connect(f"{r.base}/ws?client={CLIENT}&document={doc_id}") as ws:
            hello = await _next(ws, "hello")
            assert hello["read_only"] is True and hello["holder"]["program"] == "desktop"
            await ws.send_json({"type": "edit", "id": 3, "document": doc_id, "tab": "T1", "edit": EDIT})
            err = await _next(ws, "error")
            assert err["id"] == 3 and err["locked"] is True and err["holder"]["program"] == "desktop"
    try:
        asyncio.run(asyncio.wait_for(go(), 60))
    finally:
        lock.release()


# ---------------------------------------------------------------------------------------------- a single Document
def test_a_single_document_server_keeps_the_old_routes_and_adds_the_keyed_ones(tmp_path):
    folder = str(tmp_path / "Doc")
    shutil.copytree(BRACKET, folder)
    session = DocumentSession(folder, spares=0)
    server = SV.make_server(session, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert server.wait_started(30)

        def get(path, method="GET", body=None):
            c = http.client.HTTPConnection("127.0.0.1", server.port, timeout=60)
            try:
                data = None if body is None else json.dumps(body).encode()
                c.request(method, path, body=data, headers={"Content-Type": "application/json"} if data else {})
                resp = c.getresponse()
                return resp.status, json.loads(resp.read())
            finally:
                c.close()
        status, old = get("/tabs")
        assert status == 200 and set(old) >= {"document", "tabs"}
        doc_id = old["document"]["id"]
        assert get(f"/documents/{doc_id}/tabs")[1]["tabs"] == old["tabs"]
        (row,) = get("/documents")[1]["documents"]
        assert row["id"] == doc_id and row["open"] is True and row["holder"] is None      # no lock file: in process
        assert get(f"/documents/{doc_id}/open", "POST", {})[0] == 200                      # already open
        assert get(f"/documents/{doc_id}/close", "POST", {})[0] == 409
        assert get("/documents", "POST", {"name": "Another"})[0] == 409
        assert get("/tabs/T1/edit", "POST", EDIT)[1] == {"rebuilt": ["T1"]}
        assert get(f"/documents/{doc_id}/tabs/T1/edit", "POST", EDIT)[1] == {"rebuilt": ["T1"]}
        assert get("/save", "POST", {})[1] == {"saved": ["T1"]}
        assert get("/documents/nope/tabs")[0] == 404
    finally:
        SV.close_all(server, session)
        P.SPARES.clear()


# ---------------------------------------------------------------------------------------------- the command
def _start(cmd, tmp_path):
    return subprocess.Popen([sys.executable, "-m", "freeshape", "serve", *cmd], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, cwd=str(tmp_path), env=_env(tmp_path))


def test_the_command_serves_a_documents_root_with_the_registry_of_data_dir_without_qt(tmp_path):
    root, data = tmp_path / "Documents", tmp_path / "data"
    root.mkdir()
    proc = _start([str(root), "--port", "0", "--data-dir", str(data)], tmp_path)
    try:
        line = proc.stdout.readline()
        assert line.startswith(SV.READY_PREFIX + " port="), (line, proc.stderr.read() if proc.poll() else "")
        port = int(line.split()[1].split("=")[1])

        def call(method, path, body=None):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=90)
            try:
                c.request(method, path, body=None if body is None else json.dumps(body),
                          headers={"Content-Type": "application/json"} if body is not None else {})
                resp = c.getresponse()
                return resp.status, json.loads(resp.read())
            finally:
                c.close()
        assert call("GET", "/health")[1]["qt_modules"] == []
        status, made = call("POST", "/documents", {"name": "From the command"})
        assert status == 201
        assert R.DocRegistry(str(data / R.REGISTRY_NAME)).find(made["id"]) == os.path.realpath(
            str(root / "From the command"))
        assert [d["id"] for d in call("GET", "/documents")[1]["documents"]] == [made["id"]]
        assert call("POST", f"/documents/{made['id']}/open", {})[0] == 200
        assert call("GET", "/health")[1]["qt_imported"] is False
        proc.terminate()
        assert proc.wait(60) == 0
        assert not os.path.exists(doc_lock.lock_path(str(root / "From the command")))   # released on the way out
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stdout.close()
        proc.stderr.close()


def test_the_command_refuses_a_registry_whose_documents_are_all_missing(tmp_path):
    data = tmp_path / "data"
    root = tmp_path / "Documents"
    root.mkdir()
    gone = str(tmp_path / "config" / "Documents" / "Lost One")
    R.DocRegistry(str(data / R.REGISTRY_NAME)).register_created("lost-0001", gone)
    proc = _start([str(root), "--port", "0", "--data-dir", str(data)], tmp_path)
    try:
        out, err = proc.communicate(timeout=120)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert proc.returncode == 1 and SV.READY_PREFIX not in out
    assert os.path.realpath(gone) in err and "same path" in err and "mount" in err


def test_a_registry_with_one_existing_document_is_served(tmp_path):
    reg = R.DocRegistry(str(tmp_path / "data" / R.REGISTRY_NAME))
    here = tmp_path / "Here"
    here.mkdir()
    reg.register_created("here-0001", str(here))
    reg.register_created("gone-0001", str(tmp_path / "Gone"))
    assert SV.registry_mismatch(reg) is None
    assert {d["id"]: d["missing"] for d in reg.list_documents()} == {"here-0001": False, "gone-0001": True}
    assert SV.registry_mismatch(R.DocRegistry(str(tmp_path / "empty" / R.REGISTRY_NAME))) is None


def test_the_serve_import_path_loads_no_qt():
    code = ("import sys; import freeshape.serve, freeshape.doc_registry, freeshape.doc_lock, freeshape.session; "
            "bad = [m for m in sys.modules if m.split('.')[0] in ('PySide6', 'PyQt5', 'PyQt6', 'shiboken6')]; "
            "assert not bad, bad")
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True, timeout=120, env=_env(Path(ROOT) / ".nope"))
