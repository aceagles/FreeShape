"""I10.2: the Derived feature's dialog over HTTP, its rows described with standard kinds.

Each test stands up its own server on a temp copy of the bracket Document with a new, empty Part Studio "Use" (T2)
and drives the dialog the way the Browser client does: POST .../features/dialog, .../dialog/<id>/update with
{"values": ...}, .../accept. The last test makes the same Derived with the desktop's own dialog in
MainWindow(engine=True) and compares the saved tab file bytes."""
import http.client
import json
import os
import shutil
import threading

import pytest

from freeshape import dialog_schema as DSC
from freeshape import doc_registry as R
from freeshape import kernel as K
from freeshape import serve as SV
from freeshape.engine import process as P
from freeshape.engine import wire
from freeshape.sketch import Sketch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRACKET = os.path.join(ROOT, "examples", "documents", "bracket")
CLIENT = "client-derived-1"
# the desktop's notes (freeshape/ui/dialogs.py `_make_derived_*`), written out here so a change of wording shows
PART_FIRST = "⚠ Tick part {} first: a connector comes in with its part."
UNTICKED = "⚠ Connectors on a part you unticked were unticked too."
NO_SOURCE = "This Document has no other Part Studio to derive from."


def _base_folder(tmp_path, name="Base"):
    """A copy of the bracket with a second, empty Part Studio "Use" saved into it (by a server that then closes)."""
    root = str(tmp_path / name)
    os.makedirs(root)
    path = os.path.join(root, "Doc")
    shutil.copytree(BRACKET, path)
    s = Server(tmp_path / f"{name}-srv", path)
    try:
        s.call("POST", f"/documents/{s.doc}/tabs", {"kind": "studio", "name": "Use"}, expect=201)
        s.call("POST", f"/documents/{s.doc}/save")
    finally:
        s.close()
    return path


class Server:
    """A server holding the Document folder `path` (registered and opened)."""

    def __init__(self, tmp, path):
        os.makedirs(tmp, exist_ok=True)
        registry = R.DocRegistry(str(tmp / "data" / R.REGISTRY_NAME))
        self.server = SV.make_server(None, 0, registry=registry, root=os.path.dirname(path))
        self.port = self.server.port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        assert self.server.wait_started(30)
        self.path = path
        with open(os.path.join(path, "document.json")) as fh:
            self.doc = json.load(fh)["id"]
        registry.register_created(self.doc, path)
        self.call("POST", f"/documents/{self.doc}/open")

    def close(self):
        try:
            SV.close_all(self.server, None)
        finally:
            P.SPARES.clear()

    def request(self, method, path, body=None):
        h = {"Host": f"127.0.0.1:{self.port}", "X-Client-Id": CLIENT}
        data = None
        if method == "POST":
            h["Content-Type"] = "application/json"
            data = json.dumps(body if body is not None else {}).encode()
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=180)
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

    def call(self, method, path, body=None, expect=200):
        status, payload = self.request(method, path, body)
        assert status == expect, (method, path, status, payload)
        return payload

    def tab(self, tab, tail):
        return f"/documents/{self.doc}/tabs/{tab}/{tail}"

    def outputs(self, tab):
        status, raw = self.request("GET", self.tab(tab, "outputs"))
        assert status == 200, raw
        return wire.from_wire(raw)

    def edit(self, tab, edit):
        return self.call("POST", self.tab(tab, "edit"), edit)

    # -- the dialog
    def new(self, tab="T2"):
        return self.call("POST", self.tab(tab, "features/dialog"), {"type": "derived"}, expect=201)

    def open(self, fid, tab="T2"):
        return self.call("POST", self.tab(tab, f"features/{fid}/dialog"), {}, expect=201)

    def update(self, did, values, tab="T2"):
        return self.call("POST", self.tab(tab, f"dialog/{did}/update"), {"values": values})

    def accept(self, did, tab="T2"):
        return self.call("POST", self.tab(tab, f"dialog/{did}/accept"))

    def derived(self, tab="T2"):
        return [f for f in self.outputs(tab).features if f.type == "derived"]


@pytest.fixture
def srv(tmp_path):
    path = _base_folder(tmp_path)
    s = Server(tmp_path / "srv", path)
    try:
        yield s
    finally:
        s.close()


def field(view, key):
    return next(f for f in view["description"]["fields"] if f["key"] == key)


def value(view, key):
    return view["values"][key]["value"]


def volume(part):
    return K.volume(K.shape_from_brep(part.brep))


def add_connector(s, tab, owner, name, fid):
    """A Mate connector at the origin owned by `owner` (a part or sketch id), added to `tab` by an edit."""
    p = {"name": name, "ref": {"origin": True}, "kind": "face", "click": None, "snap": None, "x_ref": None,
         "flip": False, "owner": owner}
    s.edit(tab, {"op": "add_feature", "feature": {"id": fid, "type": "mate_connector", "name": name,
                                                  "suppressed": False, "params": p}})
    assert any(c.id == fid for c in s.outputs(tab).connectors)
    return fid


# ---------------------------------------------------------------------------------------------------------------------


def test_a_new_derived_lists_the_bracket_studio_and_its_part_and_brings_it_in_with_its_volume(srv):
    s = srv
    (bracket,) = s.outputs("T1").parts
    got = s.new()
    assert got["description"]["custom"] == [] and all(f["standard"] for f in got["description"]["fields"])
    src = field(got, "source")
    assert src["kind"] == "choice" and src["options"] == [["", "— choose a Part Studio —"], ["T1", "bracket"]]
    assert field(got, "parts")["options"] == [] and value(got, "source") == ""
    assert field(got, "location")["visible"] is False and field(got, "base")["visible"] is False   # nothing to place on
    up = s.update(got["dialog"], {"source": "T1"})
    assert not up["errors"]
    assert field(up, "parts")["kind"] == "ticks" and field(up, "parts")["options"] == [[bracket.id, bracket.name]]
    assert [f["key"] for f in up["description"]["fields"] if f["kind"] == "ticks"] == ["parts", "sketches", "connectors"]
    assert {f["group"] for f in up["description"]["fields"] if f["kind"] == "ticks"} == {"derived_tabs"}
    up = s.update(got["dialog"], {"parts": [bracket.id]})
    assert not up["errors"] and value(up, "parts") == [bracket.id]
    s.accept(got["dialog"])
    (f,) = s.derived()
    assert f.error is None
    (part,) = s.outputs("T2").parts
    assert part.id == f"{f.id}/1" and part.name == bracket.name
    assert volume(part) == pytest.approx(volume(bracket), rel=1e-9)


def test_a_connector_without_its_part_is_refused_and_unticking_the_part_unticks_its_connectors(srv):
    s = srv
    (bracket,) = s.outputs("T1").parts
    cid = add_connector(s, "T1", bracket.id, "Pin", "F9")
    got = s.new()
    s.update(got["dialog"], {"source": "T1"})
    up = s.update(got["dialog"], {"connectors": [cid]})
    assert field(up, "connectors")["options"] == [[cid, f"Pin  ({bracket.name})"]]
    assert up["errors"] == {"connectors": PART_FIRST.format(bracket.id)} and value(up, "connectors") == []
    assert up["values"]["connectors"]["error"] == PART_FIRST.format(bracket.id)
    up = s.update(got["dialog"], {"parts": [bracket.id]})
    up = s.update(got["dialog"], {"connectors": [cid]})
    assert not up["errors"] and value(up, "connectors") == [cid]
    up = s.update(got["dialog"], {"parts": []})
    assert value(up, "parts") == [] and value(up, "connectors") == []
    assert field(up, "_note")["text"] == UNTICKED


def test_editing_the_derived_and_unticking_the_part_leaves_no_part_and_reopening_shows_the_stored_ticks(srv):
    s = srv
    (bracket,) = s.outputs("T1").parts
    got = s.new()
    s.update(got["dialog"], {"source": "T1"})
    s.update(got["dialog"], {"parts": [bracket.id]})
    s.accept(got["dialog"])
    (f,) = s.derived()
    assert len(s.outputs("T2").parts) == 1
    again = s.open(f.id)
    assert value(again, "source") == "T1" and value(again, "parts") == [bracket.id]
    assert field(again, "parts")["options"] == [[bracket.id, bracket.name]]
    s.update(again["dialog"], {"parts": []})
    s.accept(again["dialog"])
    assert s.outputs("T2").parts == [] and len(s.derived()) == 1
    third = s.open(f.id)
    assert value(third, "source") == "T1" and value(third, "parts") == []


def test_types_reports_derived_standard_and_every_other_type_as_before(srv):
    s = srv
    rows = {t["type"]: t for t in s.call("GET", s.tab("T2", "features/types"))["types"]}
    assert rows["derived"]["standard"] is True and rows["derived"]["can_start"] is True
    plain = ("num", "choice", "check", "sel", "ops", "info", "text")     # the rule before I10.2
    before = {t for t, spec in DSC.SCHEMAS.items() if any(r["t"] not in plain for r in spec)}
    assert "derived" in before
    for t, row in rows.items():
        if t not in ("derived", "mate_connector"):           # mate_connector is served since I10.4
            assert row["standard"] is (t not in before), t


def test_the_source_list_leaves_out_this_studio_and_the_document_chooser_offers_only_this_document(srv):
    s = srv
    got = s.new(tab="T1")                     # the bracket's own studio: "Use" is the other one
    assert field(got, "source")["options"] == [["", "— choose a Part Studio —"], ["T2", "Use"]]
    s.call("POST", s.tab("T1", f"dialog/{got['dialog']}/cancel"))
    doc = field(got, "document")
    assert doc["options"][0] == ["this", "This Document"] and value(got, "document") == "this"
    assert doc["disabled"] == {"other": "Desktop only for now", "version": "Desktop only for now"}
    got = s.new()
    up = s.update(got["dialog"], {"document": "other"})
    assert up["errors"] == {"document": "Desktop only for now"} and value(up, "document") == "this"
    status, body = s.request("POST", s.tab("T2", f"dialog/{got['dialog']}/update"), {"values": {"source": "T9"}})
    assert status == 400 and "values.source" in body["error"]


def test_the_note_says_there_is_no_other_part_studio(tmp_path):
    path = os.path.join(str(tmp_path / "One"), "Doc")
    shutil.copytree(BRACKET, path)
    s = Server(tmp_path / "srv", path)
    try:
        got = s.new(tab="T1")
        assert field(got, "source")["options"] == [["", "— choose a Part Studio —"]]
        assert field(got, "_note")["text"] == NO_SOURCE
    finally:
        s.close()


def test_location_and_placement_base_follow_the_ticked_connectors(srv):
    s = srv
    (bracket,) = s.outputs("T1").parts
    cid = add_connector(s, "T1", bracket.id, "Pin", "F9")
    sk = Sketch()
    sk.add_circle(sk.add_point(50, 0), 2)
    sk.solve()
    s.edit("T2", {"op": "add_feature", "feature": {"id": "F1", "type": "sketch", "name": "Mark", "suppressed": False,
                                                   "params": {"plane": {"kind": "std", "name": "Top"},
                                                              "sketch": sk.to_dict()}}})
    here = add_connector(s, "T2", "F1", "Here", "F2")
    got = s.new()
    loc = field(got, "location")
    assert loc["options"] == [["", "— not placed —"], [here, "Here"]] and value(got, "location") == ""
    assert field(got, "base")["visible"] is False
    s.update(got["dialog"], {"source": "T1"})
    s.update(got["dialog"], {"parts": [bracket.id]})
    up = s.update(got["dialog"], {"connectors": [cid], "location": here})
    assert field(up, "base")["visible"] is True
    assert field(up, "base")["options"] == [["origin", "Base origin"], [cid, f"Pin  ({bracket.name})"]]
    up = s.update(got["dialog"], {"base": cid})
    assert value(up, "base") == cid
    up = s.update(got["dialog"], {"connectors": []})
    assert value(up, "base") == "origin" and field(up, "base")["options"] == [["origin", "Base origin"]]
    s.accept(got["dialog"])
    (f,) = s.derived()
    assert f.error is None
    out = s.outputs("T2")
    (part,) = out.parts
    at = next(c for c in out.connectors if c.id == here).frame["o"]
    lo, _hi = K.bbox(K.shape_from_brep(part.brep))
    blo, _bhi = K.bbox(K.shape_from_brep(bracket.brep))
    assert [round(a - b, 6) for a, b in zip(lo, blo)] == [round(float(v), 6) for v in at]   # Base origin onto "Here"
    again = s.open(f.id)
    assert value(again, "location") == here and value(again, "base") == "origin" and value(again, "connectors") == []


def test_the_preview_reads_the_chosen_source(srv):
    """A Derived's preview gets its source as its accept does: the previewed model holds the derived part."""
    import time
    s = srv
    (bracket,) = s.outputs("T1").parts
    got = s.new()
    s.update(got["dialog"], {"source": "T1"})
    up = s.update(got["dialog"], {"parts": [bracket.id]})
    assert up["preview"] is not None
    path = s.tab("T2", f"dialog/{got['dialog']}/preview")
    deadline = time.monotonic() + 120                # the preview is built off the request; poll for its answer
    while True:
        status, raw = s.request("GET", path)
        if status == 200 and wire.from_wire(raw).parts:
            break
        assert time.monotonic() < deadline, (status, raw)
        time.sleep(0.2)
    (part,) = wire.from_wire(raw).parts
    assert volume(part) == pytest.approx(volume(bracket), rel=1e-9)


# ---------------------------------------------------------------------------------------------------------------------
# saved bytes: the browser saves what the desktop saves for the same choices


def _desktop_derived(tmp_path, path, src_tab, part):
    """Make a Derived of part `part` of tab `src_tab` in tab T2 of the Document folder `path` with the desktop's own
    dialog in MainWindow(engine=True), driven through its widgets, then save."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtWidgets import QApplication, QMessageBox
    from freeshape.ui import home as H
    from freeshape.ui import mainwindow as MW
    mp = pytest.MonkeyPatch()
    root = tmp_path / "desk"
    for d in ("autosave", "thumbs", "appdata"):
        os.makedirs(root / d, exist_ok=True)
    mp.setenv("FREESHAPE_DATA_DIR", str(root / "appdata"))
    mp.setenv("FREESHAPE_NO_RECOVERY", "1")
    mp.setenv("FREESHAPE_NO_HOME", "1")
    app = QApplication.instance() or QApplication([])
    mp.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(root / "autosave")))
    mp.setattr(MW.MainWindow, "autosave_dir", classmethod(lambda cls: str(root / "autosave")))
    mp.setattr(H, "thumbs_dir", lambda: str(root / "thumbs"))
    for name in ("warning", "critical", "information"):
        mp.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.Ok)
    w = MW.MainWindow(engine=True)
    try:
        w.settings = QSettings(str(root / "settings.ini"), QSettings.IniFormat)
        w.open_document(path)
        assert w.studio_engine.settle(240)
        use = w.folder.tab("T2")
        w.tabs.setCurrentIndex(w._tab_index(use.doc))
        assert w.studio_engine.settle(60)
        w.start_feature("derived")
        d = w.dialog
        combo = d.widgets["source"]
        combo.setCurrentIndex(combo.findData(src_tab))
        lst = d.widgets["parts"]
        for i in range(lst.count()):
            if lst.item(i).data(Qt.UserRole) == part:
                lst.item(i).setCheckState(Qt.Checked)
        w.commit_dialog()
        assert w.studio_engine.settle(120)
        assert [f["type"] for f in use.doc.features] == ["derived"]
        app.processEvents()
        assert use.doc.dirty, "the desktop marks the tab edited"
        assert w.save()
        app.processEvents()
        return w.folder.path
    finally:
        w.studio_engine.shutdown()
        w.close()
        P.SPARES.clear()
        mp.undo()


def _tab_bytes(path, tab):
    with open(os.path.join(path, "document.json")) as fh:
        entry = next(t for t in json.load(fh)["tabs"] if t["id"] == tab)
    with open(os.path.join(path, entry["file"]), "rb") as fh:
        return fh.read()


def _blob_shape(path, tab, key):
    from freeshape import blobstore as B
    with open(os.path.join(path, "document.json")) as fh:
        entry = next(t for t in json.load(fh)["tabs"] if t["id"] == tab)
    return B.shape_from_brep(B.store_for(os.path.join(path, entry["file"])).get(key))


def _without_breps(data):
    """The tab file's JSON with each stored build's BREP blob key taken out (what is left must be byte-equal)."""
    out = json.loads(data)
    for build in (out.get("python_builds") or {}).values():
        for rec in build["parts"]:
            rec.pop("brep")
    return json.dumps(out, indent=1).encode()


def test_a_derived_made_in_the_browser_saves_the_bytes_the_desktop_saves(tmp_path):
    """The feature list is byte for byte what the desktop writes for the same choices (the dialog's work). The stored
    build of the Derived (python_builds, the saved copy of I1.3) keys the same inputs and names the same parts; its
    BREP blob is the engine's serialisation of the derived part, written from the source shape on the desktop and
    from the shape the server's worker rebuilt from the source's BREP, so the two blobs hold the same solid in text
    that differs: the test compares their geometry."""
    base = _base_folder(tmp_path)
    web, desk = str(tmp_path / "web" / "Doc"), str(tmp_path / "desk" / "Doc")
    shutil.copytree(base, web)
    shutil.copytree(base, desk)
    s = Server(tmp_path / "srv", web)
    try:
        (bracket,) = s.outputs("T1").parts
        got = s.new()
        s.update(got["dialog"], {"source": "T1"})
        s.update(got["dialog"], {"parts": [bracket.id]})
        s.accept(got["dialog"])
        s.call("POST", f"/documents/{s.doc}/save")
    finally:
        s.close()
    desk = _desktop_derived(tmp_path, desk, "T1", bracket.id)    # the desktop saves where it imported the folder
    web_bytes, desk_bytes = _tab_bytes(web, "T2"), _tab_bytes(desk, "T2")
    made, want = json.loads(web_bytes), json.loads(desk_bytes)
    assert [f["type"] for f in made["features"]] == ["derived"]
    assert made["features"][0]["params"] == {"source": {"kind": "studio", "tab": "T1", "part": None,
                                                        "studio_name": "bracket"},
                                             "parts": [bracket.id], "sketches": [], "connectors": []}
    assert json.dumps(made["features"], indent=1) == json.dumps(want["features"], indent=1)
    assert _without_breps(web_bytes) == _without_breps(desk_bytes)
    (fid,) = made["python_builds"]
    a = _blob_shape(web, "T2", made["python_builds"][fid]["parts"][0]["brep"])
    b = _blob_shape(desk, "T2", want["python_builds"][fid]["parts"][0]["brep"])
    assert K.volume(a) == pytest.approx(K.volume(b), rel=1e-9) and len(K.faces(a)) == len(K.faces(b))
    assert K.volume(a) == pytest.approx(volume(bracket), rel=1e-9)
