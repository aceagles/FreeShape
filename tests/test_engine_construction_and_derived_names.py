"""Two ways construction geometry and references went missing on the process executor.

1. A studio that derives a part from another tab holds references to the derived part's faces by element name.
   The source tab handed its parts to the deriving tab (`upstream`) without their name maps whenever it had been
   rebuilt since its own outputs were last read -- any live edit. The derived part then arrived nameless and
   every feature referring to one of its faces failed with "... is gone".

2. The window re-ran plane and axis features itself, against the finished parts. A plane or axis placed on a
   face that a later feature changed (and so renamed) could not be found there, and silently vanished from the
   window: not drawn, and not selectable. The engine now sends the planes and axes it built, each against the
   model as it was at that feature (`StudioOutputs.planes` / `.axes`), and the window uses those."""
import os

import numpy as np
import pytest

from conftest import add, block_doc, only_part, rect_lines, region_refs, sketch_feature
from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import features as F
from freeshape import kernel as K
from freeshape import links as L
from freeshape.document import Document
from freeshape.engine import ProcessEngine
from freeshape.engine import process as P
from freeshape.sketch import Sketch

H = 5.0


@pytest.fixture(scope="module", autouse=True)
def _no_spares_left():
    yield
    P.SPARES.clear()


def _top(shape):
    """(index, face) of the top face (normal +z) of a shape."""
    for i, f in enumerate(K.faces(shape)):
        n = K.face_info(f).get("n")
        if n is not None and n[2] > 0.99:
            return i, f
    raise AssertionError("no top face")


def _width_edit(w):
    s = Sketch()
    rect_lines(s, 0, 0, w, 10)
    s.solve()
    return {"op": "set_param", "feature": "F1", "key": "sketch", "value": s.to_dict()}


def _source_and_child(tmp_path):
    """Source: a 20 x 10 x 5 block. Child: derives the block."""
    fd = DF.FolderDocument.create(str(tmp_path / "Doc"), registry=R.default_registry())
    src = fd.add_tab("studio", "Source")
    sid = sketch_feature(src.doc, lambda s: rect_lines(s, 0, 0, 20, 10))
    add(src.doc, "extrude", regions=region_refs(src.doc, sid), depth=H, op="new")
    block = next(iter(src.doc.state.parts))
    child = fd.add_tab("studio", "Child")
    add(child.doc, "derived", source=L.studio_source(src), parts=[block], sketches=[], connectors=[])
    assert not (src.doc.errors or child.doc.errors)
    fd.save()
    return fd, src, child


def _open(fd, src, child):
    eng = ProcessEngine()
    for t in (src, child):
        eng.open_tab(os.path.join(fd.path, t.file), tab=t.id)
    return eng


def _add_axis_on_derived_top_face(eng, child):
    """What the Axis dialog stores when the top face of the derived part is clicked: a reference that carries
    the face's element name, as the engine reports it. Returns the new feature's id."""
    part = eng.outputs(child.id).parts[0]
    shape = K.shape_from_brep(part.brep)
    i, face = _top(shape)
    assert part.names["face"], "the derived part carries no element names"
    name = part.names["face"][i + 1]
    assert name and "DRV" in name, name
    ref = dict(F.make_ref(part.id, face, "face", shape), name=name)
    f = Document().make_feature("axis")
    f["params"].update(refs=[{"k": "face", "ref": ref}], click=[4.0, 3.0, H])
    before = {x["id"] for x in child.doc.features}
    eng.submit_edit(child.id, {"op": "add_feature", "feature": f})
    out = eng.outputs(child.id)
    assert not out.errors, out.errors
    return f["id"] if f["id"] not in before else None


def test_a_live_edit_to_the_source_keeps_the_names_a_deriving_studio_refers_to(tmp_path):
    fd, src, child = _source_and_child(tmp_path)
    eng = _open(fd, src, child)
    try:
        _add_axis_on_derived_top_face(eng, child)
        for width in (30, 12, 20):                          # each edit rebuilds Source, then Child from it
            eng.submit_edit(src.id, _width_edit(width))
            out = eng.outputs(child.id)
            assert not out.errors, (width, out.errors)
            assert any(out.parts[0].names.values()), (width, "the derived part lost its element names")
            lo, hi = K.bbox(K.shape_from_brep(out.parts[0].brep))
            assert abs((hi[0] - lo[0]) - width) < 1e-6, "the edit reached the deriving studio"
    finally:
        eng.close()


def test_studio_outputs_carry_the_axes_the_engine_built(tmp_path):
    fd, src, child = _source_and_child(tmp_path)
    eng = _open(fd, src, child)
    try:
        assert eng.outputs(child.id).axes == {} and eng.outputs(child.id).planes == {}
        _add_axis_on_derived_top_face(eng, child)
        out = eng.outputs(child.id)
        assert len(out.axes) == 1
        ax = next(iter(out.axes.values()))
        assert np.allclose(ax["d"], [0, 0, 1]) and np.allclose(ax["o"][:2], [4.0, 3.0])
        eng.submit_edit(src.id, _width_edit(30))            # and they follow a live edit of the source
        assert len(eng.outputs(child.id).axes) == 1
    finally:
        eng.close()


# =========================================================================== the window

@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("FREESHAPE_NO_RECOVERY", "1")
    monkeypatch.setenv("FREESHAPE_NO_HOME", "1")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow(engine=True)
    yield w
    w.studio_engine.shutdown()
    P.SPARES.clear()
    w.deleteLater()
    app.processEvents()


def _settle(w):
    from PySide6.QtWidgets import QApplication
    for _ in range(4):
        QApplication.processEvents()
        assert w.studio_engine.settle(180)
    QApplication.processEvents()


def test_the_window_takes_axes_from_the_engine_and_does_not_rebuild_them_itself(win, monkeypatch):
    """Stand-in for "the axis's face was changed by a later feature": the window's own axis runner cannot build
    the axis at all. The worker is another process and builds it; the window must show what the worker built."""
    doc = block_doc(20, 10, H)
    part = only_part(doc)
    _i, face = _top(part.shape)
    ref = F.make_ref(part.id, face, "face", part.shape)
    axis = add(doc, "axis", refs=[{"k": "face", "ref": ref}], click=[4.0, 3.0, H])["id"]
    want = dict(doc.state.axes[axis])

    def cannot(_state, _feature):
        raise K.KernelError("Face … is gone: the model no longer has an element of that name")
    monkeypatch.setitem(F.RUNNERS, "axis", cannot)
    win._add_tab(doc)
    _settle(win)
    assert win.studio_engine.broken is None, win.studio_engine.broken
    assert not doc.errors, doc.errors
    assert axis in doc.state.axes, "the axis vanished from the window"
    got = doc.state.axes[axis]
    assert np.allclose(got["o"], want["o"]) and np.allclose(got["d"], want["d"])
    assert any(key == ("axis", axis) for key, *_ in win._axes_for(doc.state)), "and it is handed to the viewport"
