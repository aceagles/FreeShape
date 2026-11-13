"""Delete part: a Part Studio feature that removes the selected parts from that point on."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from conftest import (add, block_doc, edge_refs, line_along, only_part, rect_lines, region_refs, sketch_feature,  # noqa: E402
                      try_add)
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.engine import InProcessEngine  # noqa: E402


def _two_parts():
    """A 40x20x10 block and a 10x10x10 block beside it: returns (doc, first part id, second part id)."""
    doc = block_doc(40, 20, 10)
    sid = sketch_feature(doc, lambda s: rect_lines(s, 60, 0, 10, 10))
    add(doc, "extrude", regions=region_refs(doc, sid), depth=10.0, op="new")
    a, b = list(doc.state.parts)
    return doc, a, b


def _vertical_edges(part):
    return edge_refs(part, line_along((0, 0, 1)), expect=4)


def _named_edge_refs(doc, pid):
    """Vertical edges of part `pid` as references that carry their element names."""
    part, named = doc.state.parts[pid], doc.state.named(pid)
    return [F.make_ref(pid, e, "edge", part.shape, named)
            for e in K.edges(part.shape) if line_along((0, 0, 1))(K.edge_info(e))]


def _names(doc, pid):
    part, named = doc.state.parts[pid], doc.state.named(pid)
    assert named is not None
    return [named.name_of(f) for f in K.faces(part.shape)] + [named.name_of(e) for e in K.edges(part.shape)]


def test_delete_one_of_two_parts_then_fillet_the_rest():
    doc, a, b = _two_parts()
    assert list(doc.state.parts) == [a, b]
    gone = add(doc, "delete_part", parts=[b])
    assert list(doc.state.parts) == [a]
    assert b not in doc.state.names and b not in doc.state.sheet
    add(doc, "fillet", edges=_vertical_edges(doc.state.parts[a]), radius=2.0)
    assert list(doc.state.parts) == [a]
    assert len(K.faces(doc.state.parts[a].shape)) == 10            # 6 faces + 4 fillet faces
    doc.set_rollback(doc.features.index(gone))                     # the bar sits before the delete
    assert list(doc.state.parts) == [a, b]
    doc.set_rollback(None)
    assert list(doc.state.parts) == [a]


def test_a_later_reference_to_the_deleted_part_is_a_missing_reference_and_the_rest_builds():
    doc, a, b = _two_parts()
    refs = _named_edge_refs(doc, b)
    assert len(refs) == 4 and all(r.get("name") for r in refs)
    add(doc, "delete_part", parts=[b])
    bad, err = try_add(doc, "fillet", edges=refs, radius=1.0)
    assert err, "a reference to a deleted part must be an error"
    assert "is gone" in err, err          # the existing lost-element error (ADR 0003)
    good = add(doc, "fillet", edges=_vertical_edges(doc.state.parts[a]), radius=2.0)
    assert doc.errors.get(bad["id"]) and not doc.errors.get(good["id"])
    assert len(K.faces(doc.state.parts[a].shape)) == 10


def test_remaining_part_keeps_its_element_names():
    with_delete, a, b = _two_parts()
    add(with_delete, "delete_part", parts=[b])
    without, a2, _b2 = _two_parts()
    assert a == a2
    assert _names(with_delete, a) == _names(without, a2)


def test_delete_needs_a_selection_and_ignores_unknown_ids():
    doc, a, b = _two_parts()
    _f, err = try_add(doc, "delete_part", parts=[])
    assert err == "Select parts"
    _f, err = try_add(doc, "delete_part", parts=["nope/1"])
    assert err == "Select parts"
    assert list(doc.state.parts) == [a, b]


def test_delete_everything_leaves_an_empty_studio():
    doc, a, b = _two_parts()
    add(doc, "delete_part", parts=[a, b])
    assert doc.state.parts == {}


def test_registered_everywhere_a_feature_type_lives():
    from freeshape.ui import dialogs
    assert F.FEATURE_LABELS["delete_part"] == "Delete part"
    assert "delete_part" in F.RUNNERS
    assert F.default_params("delete_part") == {"parts": []}
    assert [s["key"] for s in dialogs.SCHEMAS["delete_part"]] == ["parts"]
    assert dialogs.SCHEMAS["delete_part"][0]["want"] == {"part"}
    fields = dialogs.params_to_fields("delete_part", {"parts": ["F3/1"]})
    assert fields["parts"] == [("part", "F3/1")]
    out = {}
    dialogs.fields_to_params("delete_part", fields, out)
    assert out["parts"] == ["F3/1"]


def test_outputs_no_longer_list_the_deleted_part(tmp_path):
    doc, a, b = _two_parts()
    path = str(tmp_path / "two.fshape")
    doc.save(path)
    e = InProcessEngine()
    tab = e.open_tab(path)
    assert [p.id for p in e.outputs(tab).parts] == [a, b]
    add(doc, "delete_part", parts=[b])
    doc.save(path)
    e2 = InProcessEngine()
    tab2 = e2.open_tab(path)
    assert [p.id for p in e2.outputs(tab2).parts] == [a]


def test_palette_and_dialog_offscreen():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    from freeshape.ui.palette import search_commands
    w = MainWindow()
    app.processEvents()
    hits = [c.name for c in search_commands(w._palette_commands(), "delete part")]
    assert "Delete part" in hits
    w.start_feature("delete_part")
    assert w.mode == "dialog" and w.dialog.ftype == "delete_part"
    spec = next(s for s in w.dialog.schema if s["key"] == "parts")
    assert spec["t"] == "sel" and "part" in spec["want"]
    w.cancel_dialog()
    from freeshape.ui.icons import icon
    assert not icon("delete_part").isNull()
