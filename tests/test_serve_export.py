"""I11.3: GET /documents/<id>/tabs/<tab>/export (a Part Studio or Assembly as STEP or STL, served), the export options
(freeshape/export_options.py) and the engine's `export` request under it. Each test builds its own root and server,
so each passes alone."""
import http.client
import json
import os
import shutil
import subprocess
import sys
import threading
import urllib.parse

import numpy as np
import pytest

from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import export_options as EO
from freeshape import kernel as K
from freeshape import serve as SV
from freeshape.engine import process as P

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(ROOT, "examples", "documents")
FINE = {"defl": 0.05, "angle": 0.3, "ascii": False}


class _Root:
    def __init__(self, tmp_path):
        self.root = str(tmp_path / "Documents")
        self.data = str(tmp_path / "data")
        os.makedirs(self.root)
        self.registry = R.DocRegistry(os.path.join(self.data, R.REGISTRY_NAME))
        self.server = SV.make_server(None, 0, registry=self.registry, root=self.root)
        self.port = self.server.port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        assert self.server.wait_started(30)

    def document(self, example):
        path = os.path.join(self.root, example)
        shutil.copytree(os.path.join(EXAMPLES, example), path)
        with open(os.path.join(path, "document.json")) as fh:
            doc_id = json.load(fh)["id"]
        self.registry.register_created(doc_id, path)
        return doc_id, os.path.realpath(path)

    def request(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=300)
        try:
            c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            c.putheader("Host", f"127.0.0.1:{self.port}")
            c.putheader("X-Client-Id", "client-aaaa1111")
            data = None
            if method == "POST":
                data = json.dumps(body if body is not None else {}).encode()
                c.putheader("Content-Type", "application/json")
                c.putheader("Content-Length", str(len(data)))
            c.endheaders(data)
            r = c.getresponse()
            payload = r.read()
            if r.getheader("Content-Type", "").startswith("application/json"):
                payload = json.loads(payload)
            return r.status, r, payload
        finally:
            c.close()

    def ok(self, method, path, body=None):
        status, _, payload = self.request(method, path, body)
        assert status == 200, (method, path, status, payload)
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


def _open(root, example="bracket"):
    r = root()
    doc_id, folder = r.document(example)
    r.ok("POST", f"/documents/{doc_id}/open")
    return r, doc_id, folder


def _export(r, doc_id, tab="T1", **query):
    q = "?" + urllib.parse.urlencode(query) if query else ""
    return r.request("GET", f"/documents/{doc_id}/tabs/{tab}/export{q}")


def _desktop_studio(folder):
    return DF.FolderDocument.open(folder).tab("T1").doc


def _write(tmp_path, name, data):
    path = str(tmp_path / name)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def test_stl_with_the_fine_preset_is_byte_equal_to_the_desktop_export(root, tmp_path):
    r, doc_id, folder = _open(root)
    status, resp, body = _export(r, doc_id, format="stl", preset="Fine")
    assert status == 200, body
    assert resp.getheader("Content-Type") == "model/stl"
    assert resp.getheader("X-FreeShape-Skipped") == ""
    assert resp.getheader("Content-Disposition") == "attachment; filename=\"bracket.stl\"; filename*=UTF-8''bracket.stl"
    want = str(tmp_path / "desktop.stl")
    _desktop_studio(folder).export(want, None, FINE)
    with open(want, "rb") as fh:
        assert body == fh.read() and len(body) > 1000
    status, _, default = _export(r, doc_id, format="stl")             # no preset is Fine
    assert status == 200 and default == body


def test_ascii_stl_starts_with_solid_and_holds_no_server_path(root, tmp_path):
    r, doc_id, folder = _open(root)
    status, _, body = _export(r, doc_id, format="stl", ascii="1")
    assert status == 200 and body.startswith(b"solid")
    assert b"freeshape-export" not in body and str(tmp_path).encode() not in body
    want = str(tmp_path / "desktop.stl")
    _desktop_studio(folder).export(want, None, {**FINE, "ascii": True})
    with open(want, "rb") as fh:
        assert body.splitlines()[1:] == fh.read().splitlines()[1:]     # the first line names the file


def test_a_custom_resolution_is_used(root, tmp_path):
    r, doc_id, folder = _open(root)
    status, _, body = _export(r, doc_id, format="stl", preset="custom", defl="0.4", angle="0.5")
    assert status == 200
    want = str(tmp_path / "desktop.stl")
    _desktop_studio(folder).export(want, None, {"defl": 0.4, "angle": 0.5, "ascii": False})
    with open(want, "rb") as fh:
        assert body == fh.read()


def test_step_of_the_part_studio_has_the_parts_volume(root, tmp_path):
    r, doc_id, folder = _open(root)
    status, resp, body = _export(r, doc_id, format="step")
    assert status == 200, body
    assert resp.getheader("Content-Type") == "model/step"
    assert resp.getheader("Content-Disposition").startswith("attachment; filename=\"bracket.step\"")
    assert body.startswith(b"ISO-10303-21;") and str(tmp_path).encode() not in body
    shapes = K.import_step(_write(tmp_path, "got.step", body))
    studio = _desktop_studio(folder)
    want = sum(K.volume(p.shape) for p in studio.state.parts.values())
    assert len(shapes) == len(studio.state.parts)
    assert abs(sum(K.volume(s) for s in shapes) - want) <= 1e-6 * want


def test_step_of_the_hinge_assembly_is_one_solid_per_shown_instance_at_its_placement(root, tmp_path):
    r, doc_id, folder = _open(root, "hinge")
    status, resp, body = _export(r, doc_id, "T2", format="step")
    assert status == 200, body
    assert resp.getheader("Content-Disposition").startswith("attachment; filename=\"hinge.step\"")
    asm = DF.FolderDocument.open(folder).tab("T2").doc
    asm.refresh(solve=True, notify=False)
    shown = [i for i in asm.instances if i["id"] in asm.parts and not i.get("hidden") and not i.get("suppressed")]
    assert len(shown) >= 2

    def key(shape):
        lo, hi = K.bbox(shape)
        return [round(K.volume(shape), 3)] + [round(float(x), 3) for x in list(lo) + list(hi)]
    got = sorted(key(s) for s in K.import_step(_write(tmp_path, "got.step", body)))
    assert got == sorted(key(asm.world_shape(i["id"])) for i in shown)


def test_a_hidden_instance_is_left_out_of_an_assembly_export(root, tmp_path):
    r, doc_id, folder = _open(root, "hinge")
    _, _, whole = _export(r, doc_id, "T2", format="step")
    asm = DF.FolderDocument.open(folder).tab("T2").doc
    r.ok("POST", f"/documents/{doc_id}/tabs/T2/edit", {"op": "set_hidden", "instance": asm.instances[0]["id"],
                                                        "value": True})
    status, _, less = _export(r, doc_id, "T2", format="step")
    assert status == 200
    assert len(K.import_step(_write(tmp_path, "less.step", less))) == len(K.import_step(_write(tmp_path, "whole.step", whole))) - 1


def test_an_unsaved_edit_is_in_the_export_and_the_file_is_untouched(root, tmp_path):
    r, doc_id, folder = _open(root)
    with open(os.path.join(folder, "bracket.fshape"), "rb") as fh:
        saved = fh.read()
    _, _, before = _export(r, doc_id, format="step")
    r.ok("POST", f"/documents/{doc_id}/tabs/T1/edit", {"op": "set_param", "feature": "F2", "key": "depth", "value": 30.0})
    status, _, after = _export(r, doc_id, format="step")
    assert status == 200 and after != before
    v = lambda raw, n: sum(K.volume(s) for s in K.import_step(_write(tmp_path, n, raw)))        # noqa: E731
    assert v(after, "after.step") < v(before, "before.step") * 0.95
    with open(os.path.join(folder, "bracket.fshape"), "rb") as fh:
        assert fh.read() == saved                                   # nothing was saved
    # the same edit made on the desktop class gives the same volume
    studio = _desktop_studio(folder)
    f = next(x for x in studio.features if x["id"] == "F2")
    f["params"]["depth"] = 30.0
    studio.replace_feature(f)
    assert abs(v(after, "after2.step") - sum(K.volume(p.shape) for p in studio.state.parts.values())) < 1e-3


def test_parts_selects_and_an_unknown_part_id_is_400(root, tmp_path):
    r, doc_id, folder = _open(root)
    part = next(iter(_desktop_studio(folder).state.parts))
    status, _, body = _export(r, doc_id, format="stl", parts=part)
    assert status == 200
    _, _, everything = _export(r, doc_id, format="stl")
    assert body == everything                                       # the bracket has the one part
    for bad in ("nope", f"{part},nope", ""):
        status, _, reply = _export(r, doc_id, format="stl", parts=bad)
        assert status == 400 and "error" in reply, (bad, reply)


def test_bad_requests_are_400(root):
    r, doc_id, _ = _open(root)
    for q in ({"format": "obj"}, {"format": "3mf"}, {"format": "brep"}, {"format": "dxf"}, {}, {"format": ""},
              {"format": "stl", "preset": "Huge"}, {"format": "stl", "preset": "Fine", "defl": "0.1"},
              {"format": "stl", "defl": "0"}, {"format": "stl", "defl": "-1"}, {"format": "stl", "defl": "1e9"},
              {"format": "stl", "defl": "nan"}, {"format": "stl", "defl": "abc"}, {"format": "stl", "angle": "7"},
              {"format": "stl", "ascii": "maybe"}, {"format": "stl", "colour": "red"}):
        status, _, reply = _export(r, doc_id, **q)
        assert status == 400 and "error" in reply, (q, status, reply)
    status, _, reply = r.request("GET", f"/documents/{doc_id}/tabs/T1/export?format=stl&format=step")
    assert status == 400
    status, _, reply = _export(r, doc_id, "T9", format="stl")
    assert status == 404


def test_an_assembly_takes_no_parts(root):
    r, doc_id, _ = _open(root, "hinge")
    status, _, reply = _export(r, doc_id, "T2", format="step", parts="x")
    assert status == 400 and "Assembly" in reply["error"]


def test_a_drawing_tab_is_400(root):
    from freeshape import drawing as D
    r = root()
    doc_id, folder = r.document("bracket")
    doc = DF.FolderDocument.open(folder)
    doc.adopt_tab(D.DrawingDoc(), "drawing", "T9", "Sheet")
    doc.save()
    r.ok("POST", f"/documents/{doc_id}/open")
    status, _, reply = _export(r, doc_id, "T9", format="step")
    assert status == 400 and "drawing" in reply["error"], reply


def test_the_file_name_of_a_tab_called_a_slash_b_colon_c_is_sanitised(root):
    r, doc_id, _ = _open(root)
    for name, want in (("a/b:c", "b_c"), ("..\\..\\evil\"name", "evil_name"), ("Brüäcke x.STL", "Brüäcke x")):
        r.ok("POST", f"/documents/{doc_id}/tabs/T1/rename", {"name": name})
        status, resp, _ = _export(r, doc_id, format="stl")
        assert status == 200
        disp = resp.getheader("Content-Disposition")
        ascii_part = disp.split('filename="', 1)[1].split('"', 1)[0]
        assert ascii_part.isascii() and "/" not in ascii_part and ":" not in ascii_part and '"' not in ascii_part
        assert disp.endswith("filename*=UTF-8''" + urllib.parse.quote(want + ".stl", safe="")), disp


def test_a_read_only_document_can_export(root):
    from freeshape import doc_lock
    r = root()
    doc_id, folder = r.document("bracket")
    lock = doc_lock.acquire(folder, "desktop")
    try:
        assert r.ok("POST", f"/documents/{doc_id}/open")["read_only"] is True
        status, _, body = _export(r, doc_id, format="stl")
        assert status == 200 and len(body) > 1000
    finally:
        lock.release()


def test_the_export_leaves_no_temporary_file(root, tmp_path, monkeypatch):
    import tempfile
    mine = tmp_path / "tmp"
    mine.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(mine))
    r, doc_id, _ = _open(root)
    status, _, _ = _export(r, doc_id, format="stl")
    assert status == 200
    status, _, _ = _export(r, doc_id, format="stl", parts="nope")
    assert status == 400 and os.listdir(mine) == []


def test_the_in_process_engine_exports_the_same_bytes(tmp_path):
    from freeshape.engine import executor as X
    eng = X.InProcessEngine()
    tab = eng.open_tab(os.path.join(EXAMPLES, "bracket", "bracket.fshape"))
    got = eng.export(tab, "stl", None, FINE)
    want = str(tmp_path / "desktop.stl")
    _desktop_studio(os.path.join(EXAMPLES, "bracket")).export(want, None, FINE)
    with open(want, "rb") as fh:
        assert got["data"] == fh.read() and got["skipped"] == []
    with pytest.raises(X.EngineError):
        eng.export(tab, "stl", ["nope"], FINE)


def test_the_options_checker():
    assert EO.stl_options() == FINE
    assert EO.stl_options("coarse") == {"defl": 0.2, "angle": 0.5, "ascii": False}
    assert EO.stl_options("Custom", "0.3", None, "true") == {"defl": 0.3, "angle": 0.3, "ascii": True}
    assert EO.stl_options(None, None, "0.2", "0") == {"defl": 0.05, "angle": 0.2, "ascii": False}
    for bad in (("x",), ("Fine", "0.1"), (None, "0"), (None, "11"), (None, None, "2"), (None, "inf"),
                (None, None, None, "yes")):
        with pytest.raises(ValueError):
            EO.stl_options(*bad)


def test_the_export_file_name_rule():
    n = EO.export_file_name
    assert n("bracket", "stl") == "bracket.stl" and n("bracket", "step") == "bracket.step"
    assert n("a/b/../c.STL", "stl") == "c.stl" and n("..\\..\\x", "stl") == "x.stl" and n(".hidden", "step") == "hidden.step"
    assert n("part.step", "stl") == "part.stl" and n("p.stp", "step") == "p.step"
    for empty in ("", None, "...", "\x00\x07", "/", ".stl"):
        assert n(empty, "stl") == "export.stl"
    assert n("x" * 300, "stl") == "x" * 100 + ".stl"


def test_the_desktop_stl_dialog_still_offers_the_three_presets_and_custom():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from freeshape.ui import export_dialog as ED
    assert ED.PRESETS is EO.PRESETS and ED.CUSTOM == EO.CUSTOM
    d = ED.StlOptionsDialog()
    assert [d.resolution.itemText(i) for i in range(d.resolution.count())] == ["Coarse", "Medium", "Fine", "Custom"]
    assert d.options() == FINE
    for name, (dv, av) in EO.PRESETS.items():
        d.resolution.setCurrentText(name)
        assert d.options() == {"defl": dv, "angle": av, "ascii": False}
    assert EO.PRESETS == {"Coarse": (0.2, 0.5), "Medium": (0.1, 0.4), "Fine": (0.05, 0.3)}


def test_export_options_and_the_serve_module_load_no_qt():
    code = "import sys; import freeshape.export_options, freeshape.serve; " \
           "print([m for m in sys.modules if m.split('.')[0].startswith('PySide')])"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, timeout=120)
    assert out.stdout.strip() == "[]", (out.stdout, out.stderr)
