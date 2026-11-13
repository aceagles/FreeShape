"""VariableStudioDoc: rows of {name, expression} evaluated with freeshape.expr, saved as a FolderDocument
tab. See freeshape/variable_studio.py and freeshape/ui/variable_panel.py (the table widget)."""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from freeshape import doc_folder as DF
from freeshape.variable_studio import VariableStudioDoc


# =========================================================================== evaluation
def test_rows_evaluate_in_order_with_later_rows_referencing_earlier_ones():
    doc = VariableStudioDoc()
    doc.add_row("w", "20 mm")
    doc.add_row("h", "#w * 2")
    values, errors = doc.values()
    assert not errors
    assert values["w"] == 20
    assert values["h"] == 40


def test_invalid_expression_reports_an_error_naming_the_row():
    doc = VariableStudioDoc()
    doc.add_row("w", "20 mm")
    doc.add_row("bad", "#missing + 1")
    values, errors = doc.values()
    assert "w" in values and values["w"] == 20
    assert len(errors) == 1
    message = next(iter(errors.values()))
    assert "bad" in message


def test_edit_and_delete_row():
    doc = VariableStudioDoc()
    doc.add_row("w", "20 mm")
    doc.set_row(0, expression="30 mm")
    assert doc.values()[0]["w"] == 30
    doc.remove_row(0)
    assert doc.rows == []


# =========================================================================== create / open / save via a Document
def test_variable_studio_tab_create_save_reopen(tmp_path):
    folder = DF.FolderDocument.create(str(tmp_path / "MyDoc"))
    tab = folder.add_tab("variables")
    tab.doc.add_row("w", "20 mm")
    tab.doc.add_row("h", "#w * 2")
    folder.save()

    reopened = DF.FolderDocument.open(folder.path)
    assert not reopened.errors, reopened.errors
    back = reopened.tab(tab.id).doc
    values, errors = back.values()
    assert not errors
    assert values["w"] == 20
    assert values["h"] == 40


def test_saved_file_has_no_unit_key_and_w_still_evaluates_to_20(tmp_path):
    folder = DF.FolderDocument.create(str(tmp_path / "MyDoc"))
    tab = folder.add_tab("variables")
    tab.doc.add_row("w", "20 mm")
    folder.save()

    path = os.path.join(folder.path, tab.file)
    with open(path) as fh:
        data = json.load(fh)
    assert "unit" not in data
    assert all("unit" not in row for row in data["rows"])

    loaded = VariableStudioDoc.load(path)
    values, errors = loaded.values()
    assert not errors
    assert values["w"] == 20


# =========================================================================== VariablePanel (offscreen Qt)
def test_panel_shows_rows_and_editing_a_cell_updates_the_document():
    from PySide6.QtWidgets import QApplication

    from freeshape.ui.variable_panel import EXPR, NAME, VariablePanel

    app = QApplication.instance() or QApplication([])
    doc = VariableStudioDoc()
    doc.add_row("w", "20 mm")
    doc.add_row("h", "#w * 2")

    panel = VariablePanel(doc)
    try:
        assert panel.table.rowCount() == 2
        assert panel.table.item(0, NAME).text() == "w"
        assert panel.table.item(1, EXPR).text() == "#w * 2"

        panel.table.item(1, EXPR).setText("#w * 3")
        app.processEvents()

        assert doc.rows[1]["expression"] == "#w * 3"
        values, errors = doc.values()
        assert not errors
        assert values["h"] == 60
        assert panel.table.item(1, 2).text() == "60"
    finally:
        panel.deleteLater()
