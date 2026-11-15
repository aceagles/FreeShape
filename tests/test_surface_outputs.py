"""I2.4: what leaves the Part Studio when it holds a surface body (a part with faces and no solid).

No feature makes a surface yet, so the surface part is put into the built state by hand, as
tests/test_surface_bodies.py does. Which code path each test drives is named in its docstring.
Published output = the Part Studio's package (links.StudioPackage): in process it is LinkResolver.resolve_studio and
engine.linkgraph.live_package, in a process engine it is the worker's `upstream` package."""
import os
import struct
import sys
import zipfile

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: E402
from OCP.gp import gp_Pnt  # noqa: E402

from conftest import block_doc  # noqa: E402
from freeshape import assembly as A  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import history as H  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import links as L  # noqa: E402
from freeshape.engine import linkgraph as LG  # noqa: E402
from freeshape.engine.encode import Encoder  # noqa: E402


def sheet(w=30.0, d=20.0, at=(100.0, 0.0, 0.0)):
    """A one-face surface shape: the top face of a box."""
    top = BRepPrimAPI_MakeBox(gp_Pnt(*at), w, d, 5.0).Shape()
    return max(K.faces(top), key=lambda f: K.face_info(f)["c"][2])


def with_surface(doc=None):
    """A built Part Studio whose state holds its solid part and a surface part. Returns (doc, solid id, surface id)."""
    doc = doc or block_doc()
    (sid,) = doc.state.parts
    surf = doc.state.new_part(sheet(), "S9", surface=True)
    assert K.is_surface(surf.shape) and surf.name == "Surface 1"
    return doc, sid, surf.id


def resolver_for(doc):
    return L.LinkResolver(find_open_studio=lambda uid, path: doc)


def studio_src(doc, part=None):
    return {"kind": "studio", "uid": doc.uid, "part": part, "studio_name": doc.name}


# ------------------------------------------------------------------------------------ Published output
def test_published_output_in_process_leaves_the_surface_out():
    """LinkResolver.resolve_studio (the in-process path every consumer and the Assembly uses)."""
    doc, sid, surf = with_surface()
    pkg = resolver_for(doc).resolve_studio(studio_src(doc))
    assert list(pkg.parts) == [sid] and surf not in pkg.parts
    plain = block_doc()
    base = resolver_for(plain).resolve_studio(studio_src(plain))
    assert [(p.name, p.hash) for p in pkg.parts.values()] == [(p.name, p.hash) for p in base.parts.values()]


def test_published_output_engine_live_package_leaves_the_surface_out():
    """engine.linkgraph.live_package: what the in-process engine hashes and hands a consumer tab."""
    doc, sid, surf = with_surface()
    view = LG.live_package("studio", doc, Encoder(meshes=False))
    assert list(view["parts"]) == [sid]
    plain = block_doc()
    assert view["parts"] == LG.live_package("studio", plain, Encoder(meshes=False))["parts"]
    doc.state.parts.pop(surf)
    assert LG.live_package("studio", doc, Encoder(meshes=False))["parts"] == view["parts"]


def _worker_package_with_surface():
    """Run in a worker: add a surface part to the tab's built model, then read the package the worker sends the parent."""
    from freeshape.engine import worker as W
    cur = W.current()
    doc = cur.doc
    (sid,) = doc.state.parts
    doc.state.new_part(sheet(), "S9", surface=True)
    pkg = cur.upstream()
    return sid, [lp["id"] for lp in pkg["parts"]]


def test_published_output_process_worker_package_leaves_the_surface_out(tmp_path):
    """engine.worker._Worker.upstream in a ProcessEngine worker (the package a consumer tab receives in the app)."""
    from freeshape.engine import ProcessEngine
    from freeshape.engine import process as P
    path = str(tmp_path / "studio.fshape")
    block_doc().save(path)
    e = ProcessEngine()
    try:
        tab = e.open_tab(path)
        sid, ids = e.run_in_worker(tab, _worker_package_with_surface)
    finally:
        e.close()
        P.SPARES.clear()
    assert ids == [sid]


def test_in_process_engine_consumer_reads_no_surface(tmp_path):
    """InProcessEngine: a consumer tab's link_inputs reads the source tab's live state."""
    from freeshape.engine import InProcessEngine
    path = str(tmp_path / "studio.fshape")
    block_doc().save(path)
    e = InProcessEngine()
    studio = e.open_tab(path)
    src = {"kind": "studio", "tab": studio, "part": None, "studio_name": "Src"}
    consumer = e.new_tab("studio", "Consumer", sources=[src])
    sdoc = e._get(studio).doc
    (sid,) = sdoc.state.parts
    sdoc.state.new_part(sheet(), "S9", surface=True)
    pkg = e._get(consumer).doc.link_inputs(src)
    assert list(pkg.parts) == [sid]


def test_drawing_and_whole_studio_shape_leave_the_surface_out():
    """links.LinkResolver.resolve_drawing_source for a whole studio and for the surface part by id."""
    doc, sid, surf = with_surface()
    r = resolver_for(doc)
    shape = r.resolve_drawing_source(studio_src(doc))
    assert not K.is_surface(shape) and len(K.solids(shape)) == 1 and len(K.faces(shape)) == 6
    with pytest.raises(K.KernelError):
        r.resolve_drawing_source(studio_src(doc, surf))
    with pytest.raises(K.KernelError):
        r.resolve_source(studio_src(doc, surf))


# ------------------------------------------------------------------------------------ Assembly
def asm_of(doc):
    r = resolver_for(doc)
    a = A.AssemblyDoc()
    a.resolver = r.resolve_source
    a.studio_resolver = r.resolve_studio
    pkg = r.resolve_studio(studio_src(doc))
    for pid in pkg.parts:                      # what the Insert panel does for "the whole Part Studio"
        a.insert(studio_src(doc, pid), checkpoint=False)
    return a


def test_assembly_inserting_the_studio_gets_only_the_solid_and_equal_mass_and_bom():
    doc, sid, surf = with_surface()
    a = asm_of(doc)
    plain = asm_of(block_doc())
    assert len(a.instances) == 1 and a.instances[0]["source"]["part"] == sid
    assert a.mass_summary()["mass"] == plain.mass_summary()["mass"] > 0
    assert a.mass_summary()["by_material"] == plain.mass_summary()["by_material"]
    assert np.allclose(a.mass_summary()["com"], plain.mass_summary()["com"])
    rows, prows = a.bom(), plain.bom()
    assert [(r["name"], r["qty"], r["volume"], r["mass"]) for r in rows] == \
           [(r["name"], r["qty"], r["volume"], r["mass"]) for r in prows]
    assert A.AssemblyDoc.bom_total(rows) == A.AssemblyDoc.bom_total(prows)


def test_assembly_guards_hold_when_a_surface_instance_already_exists():
    """An Assembly saved before surfaces were left out (or a changed source) can still hold a surface instance:
    it has no mass, no weight, and is not the base instance."""
    a = A.AssemblyDoc()
    cube = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 10, 10, 10).Shape()
    big = sheet(200.0, 200.0, (0.0, 0.0, 0.0))
    lib = {"cube": cube, "sheet": big}
    a.resolver = lambda s: F.Part("x", s["part"], lib[s["part"]], (0.7, 0.7, 0.7), None)
    ic = a.insert({"kind": "studio", "uid": "u", "part": "cube"}, T=np.eye(4), checkpoint=False)
    isf = a.insert({"kind": "studio", "uid": "u", "part": "sheet"}, T=A.translation([300, 0, 0]), checkpoint=False)
    a.instance(ic)["fixed"] = a.instance(isf)["fixed"] = False
    solo = A.AssemblyDoc()
    solo.resolver = a.resolver
    solo.insert({"kind": "studio", "uid": "u", "part": "cube"}, T=np.eye(4), checkpoint=False)
    assert a.mass_summary()["mass"] == solo.mass_summary()["mass"]
    rows = {r["name"]: r for r in a.bom()}
    assert rows["sheet"]["volume"] is None and rows["sheet"]["mass"] is None
    assert A.AssemblyDoc.bom_total(a.bom())["mass"] == A.AssemblyDoc.bom_total(solo.bom())["mass"]
    before = np.array(a.instance(isf)["T"], float)
    a.auto_explode()                    # the base is the biggest by bounding box: the sheet, not the cube
    assert np.allclose(a.instance(isf)["T"], before)


# ------------------------------------------------------------------------------------ Insert list
@pytest.fixture
def win(tmp_path, monkeypatch):
    """engine=False window (the Insert panel reads the open Part Studio's own state; no feature can make a
    surface in an engine worker yet, so engine mode cannot be driven here)."""
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    doc = block_doc()
    w._add_tab(doc)
    w.new_assembly()
    app.processEvents()
    w.studio = doc
    yield w
    w.deleteLater()


def test_insert_list_and_insert_whole_studio_leave_the_surface_out(win):
    from freeshape.ui.assembly_ui import ROLE_ID, ROLE_KIND
    doc = win.studio
    with_surface(doc)
    win.asm.toggle_insert()
    panel = win.asm.insert_panel
    top = next(panel.tree.topLevelItem(i) for i in range(panel.tree.topLevelItemCount())
               if panel.tree.topLevelItem(i).data(0, ROLE_ID) == (doc.uid, None))
    names = [top.child(i).text(0) for i in range(top.childCount()) if top.child(i).data(0, ROLE_KIND) == "part"]
    assert names == ["Part 1"]
    win.asm.close_insert()
    win.asm.insert_studio_parts(doc.uid, None)
    assert len(win.doc.instances) == 1 and win.doc.instances[0]["part_name"] == "Part 1"


# ------------------------------------------------------------------------------------ Export
def stl_triangles(path):
    with open(path, "rb") as fh:
        head = fh.read(84)
    return struct.unpack("<I", head[80:84])[0]


def test_stl_3mf_obj_leave_the_surface_out_and_step_brep_keep_it(tmp_path):
    doc, sid, surf = with_surface()
    plain = block_doc()
    a, b = str(tmp_path / "both.stl"), str(tmp_path / "solid.stl")
    doc.export(a)
    assert doc.last_export_skipped == ["Surface 1"]
    plain.export(b)
    assert plain.last_export_skipped == []
    assert stl_triangles(a) == stl_triangles(b) == 12
    assert open(a, "rb").read() == open(b, "rb").read()
    for ext in ("3mf", "obj"):
        pa, pb = str(tmp_path / f"both.{ext}"), str(tmp_path / f"solid.{ext}")
        doc.export(pa)
        plain.export(pb)
        if ext == "obj":
            assert open(pa).read() == open(pb).read()
        else:
            with zipfile.ZipFile(pa) as za, zipfile.ZipFile(pb) as zb:
                assert za.read("3D/3dmodel.model") == zb.read("3D/3dmodel.model")
    with pytest.raises(K.KernelError, match="surface"):
        doc.export(str(tmp_path / "only.stl"), part_ids=[surf])
    # STEP and BREP keep the surface
    step = str(tmp_path / "both.step")
    doc.export(step)
    text = open(step, errors="replace").read()
    assert "MANIFOLD_SOLID_BREP" in text and ("SHELL_BASED_SURFACE_MODEL" in text or "OPEN_SHELL" in text)
    only = str(tmp_path / "only.step")
    doc.export(only, part_ids=[surf])
    t2 = open(only, errors="replace").read()
    assert "MANIFOLD_SOLID_BREP" not in t2 and ("SHELL_BASED_SURFACE_MODEL" in t2 or "OPEN_SHELL" in t2)
    brep = str(tmp_path / "both.brep")
    doc.export(brep)
    from OCP.BRep import BRep_Builder
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS_Shape
    back = TopoDS_Shape()
    assert BRepTools.Read_s(back, brep, BRep_Builder())
    assert len(K.bodies(back)) == 2


def test_solid_only_studio_exports_the_same_as_a_studio_that_never_had_a_surface(tmp_path):
    """The solid-only STL does not depend on the new code: same bytes as K.export_stl of the shape."""
    plain = block_doc()
    a, b = str(tmp_path / "a.stl"), str(tmp_path / "b.stl")
    plain.export(a)
    K.export_stl([p.shape for p in plain.state.parts.values()], b)
    assert open(a, "rb").read() == open(b, "rb").read()


# ------------------------------------------------------------------------------------ Compare
def state_with(*shapes):
    st = F.ModelState()
    for s in shapes:
        st.new_part(s, "S1", surface=True)
    return st


def test_compare_shows_area_for_a_surface_and_no_volume():
    old, new = state_with(sheet(30, 20)), state_with(sheet(40, 20))
    g = H.geometry_changes(old, new)
    assert g["rows"] == []
    ((name, a, b),) = g["surface_rows"]
    assert name == "Surface 1" and a == pytest.approx(600.0) and b == pytest.approx(800.0)
    html = H.to_html({"features": [], "geometry": g})
    assert "cm²" in html and "cm³" not in html and "Surface 1" in html
    added = H.geometry_changes(F.ModelState(), new)
    assert added["rows"] == [] and added["surface_rows"] == [("Surface 1", None, pytest.approx(800.0))]
    gone = H.geometry_changes(new, F.ModelState())
    assert gone["rows"] == [] and gone["surface_rows"] == [("Surface 1", pytest.approx(800.0), None)]


def test_compare_still_gives_volume_rows_for_solids_alongside_a_surface():
    old = block_doc(40, 20, 10).state
    new = block_doc(40, 20, 12).state
    new.new_part(sheet(), "S9", surface=True)
    g = H.geometry_changes(old, new)
    (sid,) = old.parts
    (row,) = g["rows"]
    assert row[1] == pytest.approx(8000.0) and row[2] == pytest.approx(9600.0)
    assert [r[0] for r in g["surface_rows"]] == ["Surface 1"] and g["surface_rows"][0][1] is None
