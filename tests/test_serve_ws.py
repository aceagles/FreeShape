"""The live channel of `freeshape serve` (I1.5, ADR 0014): GET /ws, the broadcasts after an edit or a save, the
per-client registry and its expiry, the Host and Origin rules on the upgrade. The client side is aiohttp's."""
import asyncio
import json
import os
import shutil
import threading

import aiohttp
import pytest

from freeshape import serve as SV
from freeshape.engine import process as P
from freeshape.session import DocumentSession

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
A, B = "client-aaaa1111", "client-bbbb2222"
EDIT = {"op": "set_param", "feature": "F5", "key": "diameter", "value": 7.0}


class _Running:
    def __init__(self, tmp_path, ttl=600.0, allow=()):
        folder = str(tmp_path / "Doc")
        shutil.copytree(os.path.join(ROOT, "examples", "documents", "bracket"), folder)
        self.expired = []
        self.session = DocumentSession(folder, spares=0)
        self.server = SV.make_server(self.session, 0, list(allow), client_ttl=ttl, client_expired=self.expired.append)
        self.port = self.server.port
        self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        assert self.server.wait_started(30)

    def on_loop(self, fn):
        """`fn()` on the server's event loop (the registry belongs to it); its result."""
        async def call():
            return fn()
        return asyncio.run_coroutine_threadsafe(call(), self.server._loop).result(10)

    def close(self):
        try:
            SV.close_all(self.server, self.session)
        finally:
            P.SPARES.clear()


@pytest.fixture
def running(tmp_path):
    made = []

    def make(**kw):
        r = _Running(tmp_path, **kw)
        made.append(r)
        return r
    try:
        yield make
    finally:
        for r in made:
            r.close()


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, 90))


async def _next(ws, kind, timeout=30):
    """The next message of type `kind` (others are skipped)."""
    while True:
        msg = await asyncio.wait_for(ws.receive(), timeout)
        assert msg.type == aiohttp.WSMsgType.TEXT, msg
        data = json.loads(msg.data)
        if data.get("type") == kind:
            return data


async def _edit(session, r, value=7.0, headers=None):
    body = dict(EDIT, value=value)
    async with session.post(f"{r.base}/tabs/T1/edit", json=body, headers=headers or {}) as resp:
        assert resp.status == 200, await resp.text()
        return await resp.json()


def test_hello_names_the_client_the_document_and_the_tabs(running):
    r = running()

    async def go():
        async with aiohttp.ClientSession() as s, s.ws_connect(f"{r.base}/ws?client={A}") as ws:
            hello = await _next(ws, "hello")
            assert hello["client"] == A and hello["document"]["name"] == "Bracket"
            assert [t["id"] for t in hello["tabs"]] == ["T1"]
            assert isinstance(hello["tabs"][0]["revision"], int)
        async with aiohttp.ClientSession() as s, s.ws_connect(f"{r.base}/ws") as ws:     # no id: the server makes one
            assert SV._CLIENT_ID.match((await _next(ws, "hello"))["client"])
    _run(go())


def test_an_edit_pushes_changed_naming_the_tab_with_a_higher_revision(running):
    r = running()

    async def go():
        async with aiohttp.ClientSession() as s, s.ws_connect(f"{r.base}/ws?client={A}") as ws:
            before = (await _next(ws, "hello"))["tabs"][0]["revision"]
            await _edit(s, r, headers={"X-Client-Id": A, "X-Base-Revision": str(before)})
            changed = await _next(ws, "changed")
            assert changed["rebuilt"] == ["T1"]
            assert changed["revisions"]["T1"] > before
            assert changed["client"] == A and changed["base"] == before
            tabs = await _next(ws, "tabs")                    # the tab became dirty
            assert tabs["tabs"][0]["dirty"] is True and tabs["tabs"][0]["revision"] == changed["revisions"]["T1"]
    _run(go())


def test_two_sockets_both_get_changed_naming_the_sender(running):
    r = running()

    async def go():
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(f"{r.base}/ws?client={A}") as wa, s.ws_connect(f"{r.base}/ws?client={B}") as wb:
                await _next(wa, "hello")
                await _next(wb, "hello")
                await _edit(s, r, 7.0, {"X-Client-Id": A})            # A edits over HTTP
                assert (await _next(wa, "changed"))["client"] == A
                assert (await _next(wb, "changed"))["client"] == A
                await wb.send_str(json.dumps({"type": "edit", "id": 5, "tab": "T1", "edit": dict(EDIT, value=8.0),
                                              "base": 3}))           # B edits over the socket
                for ws in (wa, wb):                                  # `changed` is pushed before the reply
                    got = await _next(ws, "changed")
                    assert got["client"] == B and got["base"] == 3
                done = await _next(wb, "edited")
                assert done["id"] == 5 and done["rebuilt"] == ["T1"]
    _run(go())
    assert r.on_loop(lambda: r.server.clients.get(B).last_edit)["base"] == 3        # recorded, not acted on


def test_a_save_pushes_changed_and_tabs(running):
    r = running()

    async def go():
        async with aiohttp.ClientSession() as s, s.ws_connect(f"{r.base}/ws?client={A}") as ws:
            await _next(ws, "hello")
            await _edit(s, r)
            await _next(ws, "tabs")
            async with s.post(f"{r.base}/save", json={}, headers={"X-Client-Id": A}) as resp:
                assert resp.status == 200
            changed = await _next(ws, "changed")
            assert changed["saved"] == ["T1"] and changed["rebuilt"] == [] and changed["client"] == A
            assert (await _next(ws, "tabs"))["tabs"][0]["dirty"] is False
    _run(go())


def test_a_bad_client_id_or_base_revision_is_refused(running):
    r = running()

    async def go():
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{r.base}/ws?client=x") as resp:
                assert resp.status == 400
            async with s.post(f"{r.base}/tabs/T1/edit", json=EDIT, headers={"X-Client-Id": "bad id!"}) as resp:
                assert resp.status == 400
            async with s.post(f"{r.base}/tabs/T1/edit", json=EDIT, headers={"X-Base-Revision": "-1"}) as resp:
                assert resp.status == 400
            async with s.get(f"{r.base}/ws") as resp:                  # a plain GET is not an upgrade
                assert resp.status == 400
    _run(go())


def test_a_reconnect_with_the_same_id_keeps_the_entry_and_expiry_drops_it(running):
    r = running(ttl=1.0)

    async def go():
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(f"{r.base}/ws?client={A}") as ws:
                await _next(ws, "hello")
                r.on_loop(lambda: r.server.clients.get(A).data.update(dialog="open"))
            await asyncio.sleep(0.3)                               # closed, inside the expiry time
            assert r.expired == []
            async with s.ws_connect(f"{r.base}/ws?client={A}") as ws:
                await _next(ws, "hello")
                assert r.on_loop(lambda: r.server.clients.get(A).data) == {"dialog": "open"}
                await asyncio.sleep(1.4)                           # connected: it does not expire
                assert r.expired == []
        for _ in range(60):                                        # no socket left: it expires
            if r.expired:
                break
            await asyncio.sleep(0.1)
    _run(go())
    assert [e.client for e in r.expired] == [A]
    assert r.expired[0].data == {"dialog": "open"}
    assert r.on_loop(lambda: r.server.clients.get(A)) is None


def test_a_foreign_origin_is_refused_on_the_upgrade(running):
    r = running()

    async def go():
        async with aiohttp.ClientSession() as s:
            with pytest.raises(aiohttp.WSServerHandshakeError) as e:
                await s.ws_connect(f"{r.base}/ws?client={A}", origin="https://evil.example")
            assert e.value.status == 403
            with pytest.raises(aiohttp.WSServerHandshakeError) as e:
                await s.ws_connect(f"{r.base}/ws?client={A}", headers={"Host": "evil.example"})
            assert e.value.status == 421
            ws = await s.ws_connect(f"{r.base}/ws?client={A}", origin=r.base)       # the page's own origin is fine
            await _next(ws, "hello")
            await ws.close()
    _run(go())


def test_an_allowed_public_origin_may_upgrade(running):
    r = running(allow=["https://cad.example.test"])

    async def go():
        async with aiohttp.ClientSession() as s:
            ws = await s.ws_connect(f"{r.base}/ws?client={A}", origin="https://cad.example.test",
                                    headers={"Host": "cad.example.test"})
            await _next(ws, "hello")
            await ws.close()
    _run(go())


def test_ping_returns_pong_with_the_same_timestamp(running):
    r = running()

    async def go():
        async with aiohttp.ClientSession() as s, s.ws_connect(f"{r.base}/ws?client={A}") as ws:
            await _next(ws, "hello")
            await ws.send_str(json.dumps({"type": "ping", "t": 1234.5}))
            assert await _next(ws, "pong") == {"type": "pong", "t": 1234.5}
            await ws.send_str("not json")
            assert (await _next(ws, "error"))["message"] == "unknown message"
    _run(go())
