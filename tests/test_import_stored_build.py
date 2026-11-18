"""#64 (I2.1): an import keeps a stored build. After the first good import, a save stores one BREP blob per
distinct geometry and a small entry in `python_builds` (per part its name, colour, face colours, base and
placement, plus a stamp of the STEP blob key, the reader and naming versions and OCCT). Opening the file again
builds the parts from that, in the window and in the engine worker, and never reads the STEP file; a stamp that
does not match, or a stored blob that will not read, falls back to the STEP file."""
import json
import os
import signal
import tempfile

import numpy as np
import pytest

from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCP.STEPCAFControl import STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_AsIs
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDocStd import TDocStd_Document
from OCP.TopLoc import TopLoc_Location
from OCP.XCAFDoc import XCAFDoc_ColorGen, XCAFDoc_ColorSurf, XCAFDoc_DocumentTool

from freeshape import blobstore
from freeshape import features as F
from freeshape import kernel as K
from freeshape.document import PartStudioDoc

from conftest import add

BRACKETS, PINS = 8, 3
PARTS = BRACKETS + PINS + 1


def _loc(k, z=0.0):
    t = gp_Trsf()
    t.SetTranslation(gp_Vec(30.0 * k, 5.0 * (k % 3), z + 2.0 * k))
    r = gp_Trsf()
    r.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 0.4 * k + 0.1)
    t.Multiply(r)
    return TopLoc_Location(t)


def _write_step(path):
    """An assembly STEP: a red filleted bracket placed 8 times in two sub-assemblies, a pin with one blue face
    placed 3 times, and one plate with no name, no colour and no placement."""
    doc = TDocStd_Document(TCollection_ExtendedString("t"))
    st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    ct = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
    box = BRepPrimAPI_MakeBox(12.0, 8.0, 5.0).Shape()
    mk = BRepFilletAPI_MakeFillet(box)
    for e in K.edges(box)[:4]:
        mk.Add(1.0, e)
    bracket = st.AddShape(mk.Shape(), False)
    TDataStd_Name.Set_s(bracket, TCollection_ExtendedString("Bracket"))
    ct.SetColor(bracket, Quantity_Color(0.8, 0.1, 0.1, Quantity_TOC_RGB), XCAFDoc_ColorGen)
    cyl = BRepPrimAPI_MakeCylinder(2.0, 10.0).Shape()
    pin = st.AddShape(cyl, False)
    TDataStd_Name.Set_s(pin, TCollection_ExtendedString("Pin"))
    face = st.AddSubShape(pin, K.faces(cyl)[0])
    ct.SetColor(face, Quantity_Color(0.1, 0.2, 0.9, Quantity_TOC_RGB), XCAFDoc_ColorSurf)
    st.AddShape(BRepPrimAPI_MakeBox(50.0, 40.0, 2.0).Shape(), False)     # a free shape: no placement at all
    top = st.NewShape()
    for s in range(2):
        sub = st.NewShape()
        for k in range(BRACKETS // 2):
            st.AddComponent(sub, bracket, _loc(k))
        st.AddComponent(top, sub, _loc(s + 7))
    for k in range(PINS):
        st.AddComponent(top, pin, _loc(k, 40.0))
    st.UpdateAssemblies()
    w = STEPCAFControl_Writer()
    w.SetNameMode(True)
    w.SetColorMode(True)
    w.Transfer(doc, STEPControl_AsIs)
    w.Write(str(path))
    return str(path)


@pytest.fixture(scope="module")
def step_file():
    return _write_step(os.path.join(tempfile.mkdtemp(), "asm.step"))


def _imported(step_file, folder):
    """A Part Studio that imports `step_file`, saved as `folder`/s.fshape; (doc, path, import feature id)."""
    with open(step_file, "rb") as fh:
        raw = fh.read()
    doc = PartStudioDoc()
    import base64
    f = add(doc, "import", data=base64.b64encode(raw).decode("ascii"), filename="asm.step")
    path = os.path.join(str(folder), "s.fshape")
    doc.save(path)
    return doc, path, f["id"]


def _snapshot(doc):
    """Per part: id, name, colour, face colours, placement, bounding box and every element name."""
    out = {}
    for pid, p in doc.state.parts.items():
        named = doc.state.named(pid)
        out[pid] = {"name": p.name, "color": tuple(np.round(p.color, 9)),
                    "faces": {int(i): tuple(np.round(c, 9)) for i, c in (p.face_colors or {}).items()},
                    "loc": K.location_matrix(p.shape.Location()), "bbox": np.concatenate(K.bbox(p.shape)),
                    "names": dict(named.names) if named is not None else None}
    return out


def _same(a, b):
    assert list(a) == list(b)                          # the same part ids, in the same order
    for pid in a:
        x, y = a[pid], b[pid]
        assert (x["name"], x["color"], x["faces"]) == (y["name"], y["color"], y["faces"]), pid
        assert np.allclose(x["loc"], y["loc"], atol=1e-9), pid
        assert np.allclose(x["bbox"], y["bbox"], atol=1e-6), pid
        assert x["names"] and x["names"] == y["names"], pid


def _entry(path, fid):
    with open(path) as fh:
        return json.load(fh)["python_builds"][fid]


def _step_key(path, fid):
    with open(path) as fh:
        return next(f for f in json.load(fh)["features"] if f["id"] == fid)["params"]["blob"]


@pytest.fixture
def step_reads(monkeypatch):
    """Every time an import in this process reads a STEP file (its cache included)."""
    calls = []
    real = K.import_step_with_colors
    monkeypatch.setattr(K, "import_step_with_colors", lambda *a, **k: calls.append(a[0]) or real(*a, **k))
    K._STEP_CACHE.clear()
    yield calls
    K._STEP_CACHE.clear()


def test_the_fixture_has_names_colours_face_colours_and_placed_copies(step_file, tmp_path):
    doc, _path, _fid = _imported(step_file, tmp_path)
    parts = list(doc.state.parts.values())
    assert len(parts) == PARTS
    assert sum(p.name == "Bracket" for p in parts) == BRACKETS
    assert any(p.face_colors for p in parts)
    assert all(p.color[0] > 0.85 > 0.4 > p.color[1] for p in parts if p.name == "Bracket")   # the file's red, as sRGB
    assert any(p.shape.Location().IsIdentity() for p in parts)


def test_a_save_stores_one_brep_per_geometry_and_an_entry_per_part(step_file, tmp_path):
    _doc, path, fid = _imported(step_file, tmp_path)
    entry = _entry(path, fid)
    assert F.is_import_build(entry)
    assert len(entry["parts"]) == PARTS
    assert len(entry["bases"]) == 3                   # bracket, pin, plate: each written once
    store = blobstore.store_for(path)
    assert all(store.has(k) for k in entry["bases"])
    assert json.loads(entry["stamp"])[-1] == _step_key(path, fid)


def test_reopen_with_the_step_file_deleted_builds_the_same_parts(step_file, tmp_path, step_reads):
    fresh, path, fid = _imported(step_file, tmp_path)
    want = _snapshot(fresh)
    os.remove(os.path.join(os.path.dirname(path), "blobs", _step_key(path, fid)))
    del step_reads[:]
    again = PartStudioDoc.load(path)
    assert not again.errors, again.errors
    assert not step_reads                              # built from the stored build alone
    _same(_snapshot(again), want)


def test_every_element_name_of_a_reopened_import_equals_a_fresh_imports(step_file, tmp_path):
    fresh, path, _fid = _imported(step_file, tmp_path)
    again = PartStudioDoc.load(path)
    for pid, p in fresh.state.parts.items():
        a, b = fresh.state.named(pid), again.state.named(pid)
        assert {k[0] for k in a.names} == {"F", "E", "V"} and sum(k[0] == "F" for k in a.names) == len(K.faces(p.shape))
        assert a.names == b.names, pid
        assert b.shape.IsSame(again.state.parts[pid].shape)


def test_a_stamp_that_does_not_match_reads_the_step_file(step_file, tmp_path, step_reads):
    fresh, path, fid = _imported(step_file, tmp_path)
    want = _snapshot(fresh)
    with open(path) as fh:
        d = json.load(fh)
    d["python_builds"][fid]["stamp"] = d["python_builds"][fid]["stamp"].replace('"step"', '"older reader"')
    with open(path, "w") as fh:
        json.dump(d, fh)
    del step_reads[:]
    again = PartStudioDoc.load(path)
    assert not again.errors, again.errors
    assert len(step_reads) == 1                        # the file itself, once
    _same(_snapshot(again), want)


def test_a_damaged_stored_blob_reads_the_step_file(step_file, tmp_path, step_reads):
    fresh, path, fid = _imported(step_file, tmp_path)
    want = _snapshot(fresh)
    blob = os.path.join(os.path.dirname(path), "blobs", _entry(path, fid)["bases"][0])
    with open(blob, "wb") as fh:
        fh.write(b"not a brep")
    del step_reads[:]
    again = PartStudioDoc.load(path)
    assert not again.errors, again.errors
    assert len(step_reads) == 1
    _same(_snapshot(again), want)


def test_a_stamp_mismatch_with_the_step_file_gone_is_an_error_not_old_geometry(step_file, tmp_path):
    _fresh, path, fid = _imported(step_file, tmp_path)
    with open(path) as fh:
        d = json.load(fh)
    d["python_builds"][fid]["stamp"] = "stale"
    with open(path, "w") as fh:
        json.dump(d, fh)
    os.remove(os.path.join(os.path.dirname(path), "blobs", _step_key(path, fid)))
    again = PartStudioDoc.load(path)
    assert "missing" in again.errors.get(fid, "")
    assert not again.state.parts


def test_saving_again_adds_no_blob_and_keeps_the_entry(step_file, tmp_path):
    _fresh, path, fid = _imported(step_file, tmp_path)
    store = blobstore.store_for(path)
    keys, entry = store.keys(), _entry(path, fid)
    again = PartStudioDoc.load(path)
    again.save()
    assert store.keys() == keys and _entry(path, fid) == entry
    with open(path) as fh:                             # a fallback to STEP stores the very same blobs again
        d = json.load(fh)
    d["python_builds"][fid]["stamp"] = "stale"
    with open(path, "w") as fh:
        json.dump(d, fh)
    PartStudioDoc.load(path).save()
    assert store.keys() == keys and _entry(path, fid) == entry


def test_a_save_to_the_folder_that_holds_the_step_file_does_not_read_it(step_file, tmp_path, monkeypatch):
    _fresh, path, fid = _imported(step_file, tmp_path)
    again = PartStudioDoc.load(path)
    step = _step_key(path, fid)
    read = []
    real = blobstore.BlobStore.get
    monkeypatch.setattr(blobstore.BlobStore, "get", lambda self, key, **k: read.append(key) or real(self, key, **k))
    again.save()
    assert step not in read
    assert step not in again._blob_bytes               # never loaded into this process at all


def test_a_save_elsewhere_copies_the_step_file_and_the_stored_build(step_file, tmp_path):
    _fresh, path, fid = _imported(step_file, tmp_path)
    again = PartStudioDoc.load(path)
    other = tmp_path / "other"
    other.mkdir()
    again.save(str(other / "s.fshape"))
    store = blobstore.store_for(str(other / "s.fshape"))
    assert store.has(_step_key(path, fid)) and all(store.has(k) for k in _entry(path, fid)["bases"])


def test_replacing_the_imported_file_drops_the_old_build(step_file, tmp_path, step_reads):
    import base64
    doc, path, fid = _imported(step_file, tmp_path)
    plate = os.path.join(str(tmp_path), "plate.brep")
    K.export_brep([BRepPrimAPI_MakeBox(5.0, 5.0, 5.0).Shape()], plate)
    with open(plate, "rb") as fh:
        f = dict(doc.feature(fid), params={"data": base64.b64encode(fh.read()).decode(), "filename": "plate.brep"})
    doc.replace_feature(f)
    assert not doc.errors and len(doc.state.parts) == 1
    doc.save()
    entry = _entry(path, fid)
    assert len(entry["parts"]) == 1 and json.loads(entry["stamp"])[-2:] == ["brep", _step_key(path, fid)]
    assert len(PartStudioDoc.load(path).state.parts) == 1


def test_an_unsaved_import_that_never_built_stores_nothing(step_file, tmp_path):
    doc, path, fid = _imported(step_file, tmp_path)
    again = PartStudioDoc.load(path)
    again.python_builds = {}
    again._import_built.clear()
    again.save()
    with open(path) as fh:
        assert fid not in (json.load(fh).get("python_builds") or {})   # a save never reads the STEP just to store


def test_the_import_build_entry_is_in_the_format_reference(step_file, tmp_path):
    from test_format_reference import _missing
    _doc, path, fid = _imported(step_file, tmp_path)
    entry = _entry(path, fid)
    assert _missing(entry, "Import build entry") == []
    assert _missing({k for p in entry["parts"] for k in p}, "Import build entry") == []


def test_a_document_saved_and_versioned_twice_stores_the_build_once(step_file, tmp_path, step_reads):
    """In a Document (a git repository, ADR 0001): saving again and making a second Version adds no blob, so git
    holds the stored build once; the Version opens from the stored build."""
    import base64
    import shutil
    from freeshape import doc_folder as DF
    from freeshape import doc_registry as R
    from freeshape import gitstore as G
    if shutil.which("git") is None:
        pytest.skip("needs git")
    reg = R.DocRegistry(str(tmp_path / "data" / "documents.json"))
    doc = DF.FolderDocument.create(str(tmp_path / "Doc"), registry=reg)
    st = doc.add_tab("studio", "Imported")
    with open(step_file, "rb") as fh:
        f = add(st.doc, "import", data=base64.b64encode(fh.read()).decode("ascii"), filename="asm.step")
    want = _snapshot(st.doc)
    doc.save()
    doc.create_version("v1")
    entry = st.doc.python_builds[f["id"]]
    assert F.is_import_build(entry)

    def blob_files(rev):
        out = G.run(["ls-tree", "-r", "--name-only", rev], doc.path).stdout.split()
        return sorted(n for n in out if "/blobs/" in "/" + n)
    v1 = blob_files("v1")
    assert all(any(n.endswith(k) for n in v1) for k in entry["bases"])
    again = DF.FolderDocument.open_document(doc.path, registry=reg, managed_root=str(tmp_path / "managed"))
    st2 = next(t for t in again.tabs if t.kind == "studio")
    assert st2.doc.python_builds[f["id"]] == entry and not st2.doc.errors
    st2.doc.dirty = True
    again.save()
    again.create_version("v2")
    assert blob_files("v2") == v1                     # the same blobs: nothing stored twice
    del step_reads[:]
    view = again.open_version("v1")
    old = next(t for t in view.tabs if t.kind == "studio").doc
    assert not old.errors and not step_reads           # the Version reads the stored build, not the STEP file
    _same(_snapshot(old), want)


# =========================================================================== the real app: engine mode

def _window():
    os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")
    os.environ.setdefault("FREESHAPE_NO_HOME", "1")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    from test_partstudio_engine import _Settings
    w = MainWindow(engine=True)
    w.settings = _Settings()
    return w


def _engine_names(doc):
    return {pid: {k: dict(v) for k, v in names.items()} for pid, names in doc.state.engine_names.items()}


def test_engine_mode_reopen_builds_from_the_stored_build_in_window_and_worker(step_file, tmp_path, monkeypatch,
                                                                            step_reads):
    from freeshape.engine import process as P
    from freeshape.ui import mainwindow as MW
    from freeshape.ui import progress
    from test_partstudio_engine import _children
    before = set(_children())
    warned, prefetched = [], []
    monkeypatch.setattr(MW.QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    monkeypatch.setattr(MW.QMessageBox, "critical", lambda *a, **k: warned.append(a[2]))
    monkeypatch.setattr(progress, "read_step_in_background", lambda *a, **k: prefetched.append(1) or True)
    w = _window()
    try:
        eng = w.studio_engine
        w.import_step_file(step_file)
        assert eng.settle(180)
        doc = w.doc
        assert not warned and not doc.errors, (warned, doc.errors)
        want, want_names = _snapshot_engine(doc), _engine_names(doc)
        fid = next(f["id"] for f in doc.features if f["type"] == "import")
        assert F.is_import_build(doc.python_builds.get(fid))   # the worker's build came back to the window
        path = str(tmp_path / "s.fshape")
        doc.path = path
        assert w.save()
        assert F.is_import_build(_entry(path, fid))
        w.close_tab(w._tab_index(doc))
        assert eng.settle(60)
        os.remove(os.path.join(str(tmp_path), "blobs", _step_key(path, fid)))     # the STEP file is gone
        del step_reads[:], prefetched[:]
        w.open_file(path)
        assert eng.settle(180)
        again = w.doc
        assert again.path == path and eng.attached(again)
        assert not warned and not again.errors, (warned, again.errors)
        assert not step_reads and not prefetched       # no STEP read in the window, nor prefetched for it
        assert len(eng.displayed(again)) == PARTS      # every part drawn from the worker's meshes
        got = _snapshot_engine(again)
        assert list(got) == list(want)
        for pid in want:
            assert got[pid][:3] == want[pid][:3], pid
            assert np.allclose(got[pid][3], want[pid][3], atol=1e-6), pid
        assert _engine_names(again) == want_names      # every face, edge and vertex name as the fresh import's
    finally:
        eng.shutdown()
        P.SPARES.clear()
        leftover = sorted(set(_children()) - before)
        for p in leftover:
            try:
                os.kill(p, signal.SIGKILL)
            except OSError:
                pass


def _snapshot_engine(doc):
    return {pid: (p.name, tuple(np.round(p.color, 6)),
                  {int(i): tuple(np.round(c, 6)) for i, c in (p.face_colors or {}).items()},
                  np.concatenate(K.bbox(p.shape)))
            for pid, p in doc.state.parts.items()}


def test_import_timing_reports_the_reopen(tmp_path, monkeypatch):
    import re
    import sys
    from PySide6.QtWidgets import QMessageBox
    from freeshape import import_timing
    from freeshape.engine import process as P
    from test_partstudio_engine import _children
    for name in ("FREESHAPE_DATA_DIR", "FREESHAPE_NO_RECOVERY"):       # run() sets these: put them back after
        monkeypatch.setenv(name, os.environ.get(name, ""))
    for name in ("warning", "critical", "information", "question"):     # and patches these
        monkeypatch.setattr(QMessageBox, name, getattr(QMessageBox, name))
    before = set(_children())
    out = tmp_path / "out.txt"
    try:
        rc = import_timing.run(str(out), "synthetic:10", steady_wait=0.2)
    finally:
        P.SPARES.clear()
        for p in sorted(set(_children()) - before):
            try:
                os.kill(p, signal.SIGKILL)
            except OSError:
                pass
    text = out.read_text()
    assert rc == 0, text
    got = dict(re.findall(r"(\w+)=(\S+)", text))
    assert got["reopen_parts"] == got["parts"], text
    assert float(got["reopen_s"]) > 0 and 0 <= float(got["reopen_blocked_s"]) <= float(got["reopen_s"]), text
    if sys.platform != "darwin":        # tree_mb reads /proc or the Windows process list; macOS has neither
        assert float(got["window_steady_after_reopen_mb"]) > 0, text
