"""Serve hosts the built Browser client at / (I1.4): its files come from a table, never from the request path."""
from __future__ import annotations

import os

import pytest
from test_serve import Served

from freeshape import serve as SV
from freeshape.engine import process as P

INDEX = b"<!doctype html><title>client</title><script type=module src=/assets/app-1a2b.js></script>"
JS = b"console.log('app');"


@pytest.fixture
def client_dir(tmp_path, monkeypatch):
    """A client folder path that is not built yet; the server under test is told to use it."""
    d = tmp_path / "app"
    monkeypatch.setattr(SV, "_CLIENT_DIR", d)
    return d


def _build(d):
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_bytes(INDEX)
    (d / "assets" / "app-1a2b.js").write_bytes(JS)
    (d / "assets" / "app-1a2b.css").write_bytes(b"body{}")
    (d / "secret.bin").write_bytes(b"x")                       # an unknown type is never served


def _served(tmp_path, monkeypatch):
    monkeypatch.setenv("FREESHAPE_PLUGIN_DIR", str(tmp_path / "plugins"))
    P.SPARES.clear()
    return Served(str(tmp_path / "Doc"))


def test_root_returns_the_built_index(client_dir, tmp_path, monkeypatch):
    _build(client_dir)
    s = _served(tmp_path, monkeypatch)
    try:
        status, h, body = s.request("GET", "/")
        assert status == 200 and body == INDEX and h["Content-Type"].startswith("text/html")
        assert "script-src 'self'" in h["Content-Security-Policy"]
        status, h, body = s.request("GET", "/assets/app-1a2b.js")
        assert status == 200 and body == JS and h["Content-Type"].startswith("text/javascript")
        assert s.request("GET", "/assets/app-1a2b.css")[1]["Content-Type"].startswith("text/css")
        assert s.request("GET", "/viewer")[0] == 404            # the old viewer page is gone
    finally:
        s.close()


def test_root_says_the_client_is_not_built(client_dir, tmp_path, monkeypatch):
    s = _served(tmp_path, monkeypatch)
    try:
        status, h, body = s.request("GET", "/")
        assert status == 200 and h["Content-Type"].startswith("text/plain")
        assert b"npm --prefix web run build" in body
        client_dir.mkdir()                                      # a folder without index.html is not built either
        (client_dir / "a.js").write_bytes(JS)
        assert b"npm --prefix web run build" in s.request("GET", "/")[2]
        assert s.request("GET", "/a.js")[0] == 404
    finally:
        s.close()


def test_only_listed_files_are_served_and_no_path_reaches_the_disk(client_dir, tmp_path, monkeypatch):
    _build(client_dir)
    (tmp_path / "outside.js").write_bytes(b"outside")
    (client_dir / "linked.js").symlink_to(tmp_path / "outside.js")
    (client_dir / ".hidden.js").write_bytes(b"h")
    s = _served(tmp_path, monkeypatch)
    try:
        for path in ("/assets/", "/assets", "/assets/nope.js", "/assets/../index.html", "/assets/%2e%2e/index.html",
                     "/%2e%2e/outside.js", "/../outside.js", "/assets/app-1a2b.js/", "/assets//app-1a2b.js",
                     "/assets%2fapp-1a2b.js", "/assets/app-1a2b%2ejs", "/index.html", "/secret.bin", "/linked.js",
                     "/.hidden.js", "/assets\\app-1a2b.js", "/assets/app-1a2b.js%00", "/tabs/x/mesh.json",
                     "//assets/app-1a2b.js", "/static/viewer.html"):
            status, _h, body = s.request("GET", path)
            assert status == 404, (path, status)
            assert path.encode() not in body
        assert s.request("GET", "/assets/app-1a2b.js?v=1")[0] == 200
        assert s.request("POST", "/assets/app-1a2b.js", {})[0] == 405
        assert s.request("POST", "/", {})[0] == 405
    finally:
        s.close()


def test_the_client_listing_is_a_table_of_plain_names(client_dir):
    _build(client_dir)
    assert sorted(SV._client_files()) == ["/", "/assets/app-1a2b.css", "/assets/app-1a2b.js"]
    assert os.path.basename(str(SV._CLIENT_DIR)) == "app"
