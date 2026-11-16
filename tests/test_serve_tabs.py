"""I2.4: add, rename and delete tabs through `freeshape serve` (POST /documents/<id>/tabs, POST .../tabs/<tab>/rename,
DELETE .../tabs/<tab>) and the DocumentSession calls under them. Each test builds its own root, registry and
server, so each passes alone."""
import asyncio
import http.client
import json
import os
import shutil
import threading

import aiohttp
import pytest

from freeshape import doc_lock
from freeshape import doc_registry as R
from freeshape import serve as SV
from freeshape.engine import process as P
from freeshape.engine import wire
from freeshape.session import DocumentSession, SessionError, check_tab_name, tab_file_stem

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HINGE = os.path.join(ROOT, "examples", "documents", "hinge")
BRACKET = os.path.join(ROOT, "examples", "documents", "bracket")
CLIENT = "client-aaaa1111"


class _Root:
    def __init__(self, tmp_path):
        self.root = str(tmp_path / "Documents")
        self.data = str(tmp_path / "data")
        os.makedirs(self.root)
        self.registry = R.DocRegistry(os.path.join(self.data, R.REGISTRY_NAME))
        self.server = SV.make_server(None, 0, registry=self.registry, root=self.root)
        self.port = self.server.port
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        assert self.server.wait_started(30)

    def copy(self, source, name):
        """A copy of an example Document in the root, registered; (its id, its real path)."""
        path = os.path.join(self.root, name)
        shutil.copytree(source, path)
        with open(os.path.join(path, "document.json")) as fh:
            doc_id = json.load(fh)["id"]
        self.registry.register_created(doc_id, path)
        return doc_id, os.path.realpath(path)

    def request(self, method, path, body=None, headers=None, raw=None):
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

    def ok(self, method, path, body=None, expect=200):
        status, payload = self.request(method, path, body)
        assert status == expect, (method, path, status, payload)
        return payload

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


def _hinge(root):
    """An open copy of the hinge Document (T1 a Part Studio, T2 the Assembly that links it); (id, folder)."""
    r = root()
    doc_id, folder = r.copy(HINGE, "Hinge")
    r.ok("POST", f"/documents/{doc_id}/open")
    return r, doc_id, folder


def _manifest(folder):
    with open(os.path.join(folder, "document.json")) as fh:
        return json.load(fh)


def _tree(path):
    """Every path under `path` but the lock file (held while the Document is open)."""
    return sorted(os.path.join(d, n) for d, ds, fs in os.walk(path) for n in ds + fs if n != doc_lock.LOCK_NAME)


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"       # a zombie is gone
    except OSError:
        return False


async def _next(ws, kind, timeout=30):
    while True:
        msg = await asyncio.wait_for(ws.receive(), timeout)
        assert msg.type == aiohttp.WSMsgType.TEXT, msg
        data = json.loads(msg.data)
        if data.get("type") == kind:
            return data


# ---------------------------------------------------------------------------------------------- add
def test_a_new_part_studio_is_listed_empty_and_survives_save_close_and_reopen(root):
    r, doc_id, folder = _hinge(root)
    made = r.ok("POST", f"/documents/{doc_id}/tabs", {"kind": "studio", "name": "Plate"}, expect=201)
    tid = made["tab"]["id"]
    assert made["document"] == doc_id and made["tab"]["kind"] == "studio" and made["tab"]["name"] == "Plate"
    assert made["tab"]["state"] == "ok" and isinstance(made["tab"]["revision"], int)
    assert [t["id"] for t in made["tabs"]] == ["T1", "T2", tid]
    listed = r.ok("GET", f"/documents/{doc_id}/tabs")["tabs"]
    assert [t["id"] for t in listed] == ["T1", "T2", tid] and listed[2]["dirty"] is True
    out = wire.from_wire(r.request("GET", f"/documents/{doc_id}/tabs/{tid}/outputs")[1])
    assert out.kind == "studio" and out.parts == [] and out.errors in ({}, None)
    assert _manifest(folder)["tabs"][-1]["id"] == "T2"                    # nothing is written before save
    r.ok("POST", f"/documents/{doc_id}/save")
    entry = _manifest(folder)["tabs"][-1]
    assert entry == {"id": tid, "kind": "studio", "file": "Plate.fshape", "name": "Plate"}
    assert os.path.isfile(os.path.join(folder, "Plate.fshape"))
    r.ok("POST", f"/documents/{doc_id}/close")
    r.ok("POST", f"/documents/{doc_id}/open")
    again = r.ok("GET", f"/documents/{doc_id}/tabs")["tabs"]
    assert [(t["id"], t["name"], t["state"], t["dirty"]) for t in again] == \
        [("T1", "hinge_parts", "ok", False), ("T2", "hinge", "ok", False), (tid, "Plate", "ok", False)]
    out = wire.from_wire(r.request("GET", f"/documents/{doc_id}/tabs/{tid}/outputs")[1])
    assert out.kind == "studio" and out.parts == []


def test_a_new_assembly_gets_a_default_name_and_a_file_that_is_not_taken(root):
    r, doc_id, folder = _hinge(root)
    a = r.ok("POST", f"/documents/{doc_id}/tabs", {"kind": "assembly"}, expect=201)["tab"]
    b = r.ok("POST", f"/documents/{doc_id}/tabs", {"kind": "assembly"}, expect=201)["tab"]
    assert (a["name"], b["name"]) == ("Assembly 1", "Assembly 2") and a["kind"] == "assembly"
    open(os.path.join(folder, "Assembly 3.fasm"), "w").close()         # a stray file the manifest does not list
    c = r.ok("POST", f"/documents/{doc_id}/tabs", {"kind": "assembly", "name": "Assembly 3"}, expect=201)["tab"]
    r.ok("POST", f"/documents/{doc_id}/save")
    files = {t["name"]: t["file"] for t in _manifest(folder)["tabs"]}
    assert files["Assembly 1"] == "Assembly 1.fasm" and files["Assembly 2"] == "Assembly 2.fasm"
    assert files[c["name"]] == "Assembly 3 2.fasm"                       # the stray file is not overwritten
    assert os.path.getsize(os.path.join(folder, "Assembly 3.fasm")) == 0


def test_an_unsaved_add_followed_by_close_leaves_nothing_and_no_worker(root):
    r, doc_id, folder = _hinge(root)
    before = _tree(folder)
    manifest = open(os.path.join(folder, "document.json"), "rb").read()
    tid = r.ok("POST", f"/documents/{doc_id}/tabs", {"kind": "studio", "name": "Gone"}, expect=201)["tab"]["id"]
    session = r.server.docs[doc_id].session
    pids = session.pids()
    assert tid in pids and _alive(pids[tid])
    r.ok("POST", f"/documents/{doc_id}/close")
    assert _tree(folder) == before and open(os.path.join(folder, "document.json"), "rb").read() == manifest
    assert not any(_alive(p) for p in pids.values())
    r.ok("POST", f"/documents/{doc_id}/open")
    assert [t["id"] for t in r.ok("GET", f"/documents/{doc_id}/tabs")["tabs"]] == ["T1", "T2"]


def test_a_save_that_changes_no_tab_leaves_the_manifest_bytes_alone(root):
    r, doc_id, folder = _hinge(root)
    path = os.path.join(folder, "document.json")
    os.utime(path, (1, 1))
    before = open(path, "rb").read()
    r.ok("POST", f"/documents/{doc_id}/save")
    assert open(path, "rb").read() == before and os.stat(path).st_mtime == 1


# ---------------------------------------------------------------------------------------------- rename
def test_rename_is_listed_at_once_and_in_the_manifest_after_save_under_the_same_id_and_file(root):
    r, doc_id, folder = _hinge(root)
    done = r.ok("POST", f"/documents/{doc_id}/tabs/T1/rename", {"name": "  Hinge leaf  "})
    assert done["tab"]["id"] == "T1" and done["tab"]["name"] == "Hinge leaf" and done["tab"]["dirty"] is True
    assert [t["name"] for t in r.ok("GET", f"/documents/{doc_id}/tabs")["tabs"]] == ["Hinge leaf", "hinge"]
    assert _manifest(folder)["tabs"][0]["name"] == "hinge_parts"          # not before save
    r.ok("POST", f"/documents/{doc_id}/save")
    entry = _manifest(folder)["tabs"][0]
    assert entry == {"id": "T1", "kind": "studio", "file": "hinge_parts.fshape", "name": "Hinge leaf"}
    with open(os.path.join(folder, "hinge_parts.fshape")) as fh:
        data = json.load(fh)
    assert data["name"] == "Hinge leaf" and data["_tab_id"] == "T1"
    assert [t["dirty"] for t in r.ok("GET", f"/documents/{doc_id}/tabs")["tabs"]] == [False, False]
    r.ok("POST", f"/documents/{doc_id}/close")
    r.ok("POST", f"/documents/{doc_id}/open")
    assert [t["name"] for t in r.ok("GET", f"/documents/{doc_id}/tabs")["tabs"]] == ["Hinge leaf", "hinge"]


# ---------------------------------------------------------------------------------------------- delete
def test_deleting_a_part_studio_an_assembly_links_to_is_409_naming_the_assembly(root):
    r, doc_id, folder = _hinge(root)
    status, body = r.request("DELETE", f"/documents/{doc_id}/tabs/T1")
    assert status == 409 and body["linked_by"] == [{"id": "T2", "name": "hinge"}] and "hinge" in body["error"]
    assert [t["id"] for t in r.ok("GET", f"/documents/{doc_id}/tabs")["tabs"]] == ["T1", "T2"]
    r.ok("POST", f"/documents/{doc_id}/save")
    assert [t["id"] for t in _manifest(folder)["tabs"]] == ["T1", "T2"]


def test_a_link_made_by_an_edit_in_this_session_blocks_a_delete_too(root):
    r, doc_id, folder = _hinge(root)
    r.ok("POST", f"/documents/{doc_id}/tabs/T2/rename", {"name": "hinge 2"})      # no effect on the link
    status, body = r.request("DELETE", f"/documents/{doc_id}/tabs/T1")
    assert status == 409 and body["linked_by"] == [{"id": "T2", "name": "hinge 2"}]


def test_deleting_an_unlinked_tab_succeeds_stops_its_worker_and_save_removes_its_file(root):
    r, doc_id, folder = _hinge(root)
    tid = r.ok("POST", f"/documents/{doc_id}/tabs", {"kind": "studio", "name": "Spare"}, expect=201)["tab"]["id"]
    r.ok("POST", f"/documents/{doc_id}/save")
    assert os.path.isfile(os.path.join(folder, "Spare.fshape"))
    pid = r.server.docs[doc_id].session.pids()[tid]
    done = r.ok("DELETE", f"/documents/{doc_id}/tabs/{tid}")
    assert done["deleted"] == tid and [t["id"] for t in done["tabs"]] == ["T1", "T2"]
    assert not _alive(pid) and tid not in r.server.docs[doc_id].session.pids()
    assert os.path.isfile(os.path.join(folder, "Spare.fshape"))          # written on save, not before
    assert r.request("GET", f"/documents/{doc_id}/tabs/{tid}/outputs")[0] == 404
    assert r.request("DELETE", f"/documents/{doc_id}/tabs/{tid}")[0] == 404
    r.ok("POST", f"/documents/{doc_id}/save")
    assert not os.path.exists(os.path.join(folder, "Spare.fshape"))
    assert [t["id"] for t in _manifest(folder)["tabs"]] == ["T1", "T2"]
    r.ok("POST", f"/documents/{doc_id}/close")
    r.ok("POST", f"/documents/{doc_id}/open")
    assert [t["id"] for t in r.ok("GET", f"/documents/{doc_id}/tabs")["tabs"]] == ["T1", "T2"]


def test_a_tab_added_and_deleted_before_a_save_writes_nothing_and_a_new_tab_may_take_its_name(root):
    r, doc_id, folder = _hinge(root)
    one = r.ok("POST", f"/documents/{doc_id}/tabs", {"kind": "studio", "name": "Same"}, expect=201)["tab"]["id"]
    r.ok("DELETE", f"/documents/{doc_id}/tabs/{one}")
    two = r.ok("POST", f"/documents/{doc_id}/tabs", {"kind": "studio", "name": "Same"}, expect=201)["tab"]["id"]
    assert two != one                                                   # an id is not reused in a session
    r.ok("POST", f"/documents/{doc_id}/save")
    assert [t["id"] for t in _manifest(folder)["tabs"]] == ["T1", "T2", two]
    assert os.path.isfile(os.path.join(folder, "Same.fshape"))


def test_a_file_deleted_on_save_is_not_one_a_later_tab_took_over(root):
    r, doc_id, folder = _hinge(root)
    one = r.ok("POST", f"/documents/{doc_id}/tabs", {"kind": "studio", "name": "Same"}, expect=201)["tab"]["id"]
    r.ok("POST", f"/documents/{doc_id}/save")
    r.ok("DELETE", f"/documents/{doc_id}/tabs/{one}")
    two = r.ok("POST", f"/documents/{doc_id}/tabs", {"kind": "studio", "name": "Same"}, expect=201)["tab"]["id"]
    r.ok("POST", f"/documents/{doc_id}/save")
    entries = {t["id"]: t["file"] for t in _manifest(folder)["tabs"]}
    assert one not in entries and os.path.isfile(os.path.join(folder, entries[two]))
    r.ok("POST", f"/documents/{doc_id}/close")
    r.ok("POST", f"/documents/{doc_id}/open")
    assert r.ok("GET", f"/documents/{doc_id}/tabs")["tabs"][-1]["state"] == "ok"


# ---------------------------------------------------------------------------------------------- the socket
def test_a_connected_socket_receives_tabs_after_each_change(root):
    r, doc_id, folder = _hinge(root)

    async def go():
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(f"{r.base}/ws?client={CLIENT}&document={doc_id}") as ws:
                await _next(ws, "hello")
                loop = asyncio.get_running_loop()

                async def call(*args):
                    return await loop.run_in_executor(None, lambda: r.request(*args))
                status, made = await call("POST", f"/documents/{doc_id}/tabs", {"kind": "assembly", "name": "Frame"})
                assert status == 201
                msg = await _next(ws, "tabs")
                assert msg["document"] == doc_id and [t["id"] for t in msg["tabs"]] == ["T1", "T2", made["tab"]["id"]]
                assert msg["tabs"][2]["name"] == "Frame" and "revision" in msg["tabs"][2]
                assert (await call("POST", f"/documents/{doc_id}/tabs/T1/rename", {"name": "Leaf"}))[0] == 200
                msg = await _next(ws, "tabs")
                assert msg["document"] == doc_id and msg["tabs"][0]["name"] == "Leaf"
                assert (await call("DELETE", f"/documents/{doc_id}/tabs/{made['tab']['id']}"))[0] == 200
                msg = await _next(ws, "tabs")
                assert [t["id"] for t in msg["tabs"]] == ["T1", "T2"]
                assert (await call("DELETE", f"/documents/{doc_id}/tabs/T1"))[0] == 409     # a refusal pushes nothing
                assert (await call("POST", f"/documents/{doc_id}/tabs/T2/rename", {"name": "Door"}))[0] == 200
                msg = await _next(ws, "tabs")
                assert [t["name"] for t in msg["tabs"]] == ["Leaf", "Door"]
    asyncio.run(asyncio.wait_for(go(), 120))


# ---------------------------------------------------------------------------------------------- hostile input
def test_hostile_tab_ids_never_reach_a_path_or_the_session(root):
    r, doc_id, folder = _hinge(root)
    before = _tree(folder)
    for bad in ("..", "%2e%2e", "..%2fsecret", "T1%2f..", "T1;x", "a" * 65, "T%201", "%00", "T1%0a", "%5Cx", "%C3%A9"):
        for method, suffix in (("POST", "/rename"), ("DELETE", ""), ("GET", "/outputs")):
            status, _ = r.request(method, f"/documents/{doc_id}/tabs/{bad}{suffix}", {"name": "x"} if method == "POST" else None)
            assert status in (404, 400, 405), (method, bad, status)
    assert r.request("DELETE", f"/documents/{doc_id}/tabs/T99")[0] == 404         # well formed, not a tab
    assert r.request("POST", f"/documents/{doc_id}/tabs/T99/rename", {"name": "x"})[0] == 404
    assert r.request("DELETE", f"/documents/{doc_id}/tabs/")[0] in (404, 405)
    assert r.request("DELETE", "/documents/..%2f/tabs/T1")[0] in (404, 400, 405)
    assert r.request("DELETE", f"/documents/nope/tabs/T1")[0] == 404
    assert [t["id"] for t in r.ok("GET", f"/documents/{doc_id}/tabs")["tabs"]] == ["T1", "T2"]
    assert _tree(folder) == before


def test_hostile_names_are_refused_or_never_become_a_file_name(root, tmp_path):
    r, doc_id, folder = _hinge(root)
    before = _tree(folder)
    bad = [None, 5, [], {}, "", "   ", "x" * 101, "a\nb", "a\x00b", "tab\there", "a\x1bb", "bidi\u202eexe", "a\u2028b"]
    for name in bad:
        status, body = r.request("POST", f"/documents/{doc_id}/tabs", {"kind": "studio", "name": name})
        if name is None:
            assert status == 201                                        # no name: the default
            r.ok("DELETE", f"/documents/{doc_id}/tabs/{body['tab']['id']}")
            continue
        assert status == 400, (name, status)
        assert r.request("POST", f"/documents/{doc_id}/tabs/T1/rename", {"name": name})[0] == 400, name
    assert r.request("POST", f"/documents/{doc_id}/tabs/T1/rename", {})[0] == 400
    assert r.request("POST", f"/documents/{doc_id}/tabs", {"kind": "drawing"})[0] == 400
    assert r.request("POST", f"/documents/{doc_id}/tabs", {"kind": "variables", "name": "x"})[0] == 400
    assert r.request("POST", f"/documents/{doc_id}/tabs", {"kind": ["studio"]})[0] == 400
    assert r.request("POST", f"/documents/{doc_id}/tabs", {})[0] == 400
    assert _tree(folder) == before
    hostile = ["../../escape", "/etc/passwd", "..\\..\\x", "C:\\Windows\\x", "...", "a/b", ".hidden", "CON:", "<b>x</b>", "💥"]
    for i, name in enumerate(hostile):
        made = r.ok("POST", f"/documents/{doc_id}/tabs", {"kind": "studio", "name": name}, expect=201)
        assert made["tab"]["name"] == name                               # the label is kept as given
    r.ok("POST", f"/documents/{doc_id}/save")
    files = [t["file"] for t in _manifest(folder)["tabs"]]
    assert len(set(files)) == len(files)
    for f in files:
        assert os.sep not in f and "/" not in f and "\\" not in f and not f.startswith(".") and ":" not in f, f
        assert os.path.isfile(os.path.join(folder, f)), f
    assert not os.path.exists(os.path.join(os.path.dirname(folder), "escape.fshape"))
    assert not os.path.exists(str(tmp_path / "escape.fshape"))
    assert sorted(os.listdir(r.root)) == ["Hinge"]


def test_the_tab_routes_check_host_origin_body_type_and_size(root):
    r, doc_id, folder = _hinge(root)
    routes = [("POST", f"/documents/{doc_id}/tabs"), ("POST", f"/documents/{doc_id}/tabs/T1/rename"),
              ("DELETE", f"/documents/{doc_id}/tabs/T2")]
    for method, path in routes:
        assert r.request(method, path, headers={"Host": "evil.example"})[0] == 421, path
        assert r.request(method, path, headers={"Origin": "https://evil.example"})[0] == 403, path
    path = f"/documents/{doc_id}/tabs"
    assert r.request("POST", path, headers={"Content-Type": "text/plain"}, raw=b'{"kind": "studio"}')[0] == 415
    assert r.request("POST", path, raw=b"{not json")[0] == 400
    assert r.request("POST", path, raw=b'["studio"]')[0] == 400
    assert r.request("POST", path, raw=b'{"kind": "studio", "name": "' + b"a" * (SV.MAX_BODY + 1) + b'"}')[0] == 413
    assert r.request("DELETE", f"{path}/T2", raw=b"{}")[0] == 400                  # DELETE takes no body
    assert r.request("PUT", f"{path}/T2")[0] == 405 and r.request("GET", f"{path}/T2")[0] == 405
    assert r.request("DELETE", path)[0] == 405 and r.request("DELETE", f"{path}/T1/outputs")[0] == 405
    assert r.request("GET", f"{path}/T1/rename")[0] == 405
    assert [t["id"] for t in r.ok("GET", path)["tabs"]] == ["T1", "T2"]


def test_a_document_locked_by_someone_else_refuses_tab_changes(root):
    r = root()
    doc_id, folder = r.copy(BRACKET, "Bracket")
    lock = doc_lock.acquire(folder, "desktop")
    try:
        r.ok("POST", f"/documents/{doc_id}/open")
        for method, path, body in (("POST", f"/documents/{doc_id}/tabs", {"kind": "studio"}),
                                   ("POST", f"/documents/{doc_id}/tabs/T1/rename", {"name": "x"}),
                                   ("DELETE", f"/documents/{doc_id}/tabs/T1", None)):
            status, payload = r.request(method, path, body)
            assert status == 423 and payload["holder"]["program"] == "desktop", (method, path)
        assert [(t["id"], t["name"]) for t in r.ok("GET", f"/documents/{doc_id}/tabs")["tabs"]] == [("T1", "bracket")]
    finally:
        lock.release()


def test_the_old_routes_of_a_single_document_server_still_list_the_tabs(tmp_path):
    folder = str(tmp_path / "Bracket")
    shutil.copytree(BRACKET, folder)
    server = SV.make_server(DocumentSession(folder), 0, folder=folder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert server.wait_started(30)
        c = http.client.HTTPConnection("127.0.0.1", server.port, timeout=60)
        c.request("GET", "/tabs", headers={"Host": f"127.0.0.1:{server.port}"})
        assert [t["id"] for t in json.loads(c.getresponse().read())["tabs"]] == ["T1"]
        c.close()
    finally:
        SV.close_all(server, None)
        P.SPARES.clear()


# ---------------------------------------------------------------------------------------------- the session
def test_tab_name_checks_and_file_stems():
    assert check_tab_name("  Plate 2 ") == "Plate 2"
    for bad in (None, 3, "", " ", "x" * 101, "a\nb", "a\x00b"):
        with pytest.raises(SessionError):
            check_tab_name(bad)
    assert tab_file_stem("../../x", "Part Studio") == "x"
    assert tab_file_stem("..", "Part Studio") == "Part Studio"
    assert tab_file_stem("a/b\\c:d", "P") == "a-b-c-d"
    assert tab_file_stem("  ..hidden", "P") == "hidden"
    assert len(tab_file_stem("n" * 90, "P")) == 60


def test_a_session_tab_change_is_in_the_store_only_after_save(tmp_path):
    folder = str(tmp_path / "Hinge")
    shutil.copytree(HINGE, folder)
    before = _tree(folder)
    with DocumentSession(folder) as s:
        tid = s.add_tab("studio", "Extra")
        s.rename_tab("T1", "Leaf")
        assert s.tabs_unsaved() and _tree(folder) == before
        with pytest.raises(SessionError) as e:
            s.delete_tab("T1")
        assert "hinge" in str(e.value) and e.value.linked_by == [{"id": "T2", "name": "hinge"}]
        s.delete_tab(tid)
        assert s.save() == ["T1", "T2"] and not s.tabs_unsaved()
    assert [t["name"] for t in _manifest(folder)["tabs"]] == ["Leaf", "hinge"]
    assert not os.path.exists(os.path.join(folder, "Extra.fshape"))
