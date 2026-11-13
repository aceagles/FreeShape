"""VariablePanel: a table widget over a VariableStudioDoc -- add, edit and delete rows of `{name, expression}`.
VariableTabs (I3.8) shows one per Variable Studio tab of the window."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QPushButton, QStackedWidget,
                                QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..expr import fmt
from ..variable_studio import VariableStudioDoc

NAME, EXPR, VALUE = range(3)


class VariablePanel(QWidget):
    changed = Signal()

    def __init__(self, doc, parent=None):
        super().__init__(parent)
        self.doc = doc
        self._loading = False

        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["Name", "Expression", "Value"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(EXPR, QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        add = QPushButton("Add")
        add.clicked.connect(self.add_row)
        buttons.addWidget(add)
        delete = QPushButton("Delete")
        delete.clicked.connect(self.delete_selected)
        buttons.addWidget(delete)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.refresh()

    # ------------------------------------------------------------------ row actions
    def add_row(self):
        self.doc.add_row()
        self.refresh()
        self.changed.emit()

    def delete_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        for r in rows:
            self.doc.remove_row(r)
        self.refresh()
        self.changed.emit()

    def _on_item_changed(self, item):
        if self._loading:
            return
        row, col = item.row(), item.column()
        if col == NAME:
            self.doc.set_row(row, name=item.text())
        elif col == EXPR:
            self.doc.set_row(row, expression=item.text())
        else:
            return
        self.refresh()
        self.changed.emit()

    # ------------------------------------------------------------------ display
    def refresh(self):
        self._loading = True
        try:
            values, errors = self.doc.values()
            self.table.setRowCount(len(self.doc.rows))
            for r, row in enumerate(self.doc.rows):
                raw_name = str(row.get("name") or "")
                name = raw_name.strip()
                self.table.setItem(r, NAME, QTableWidgetItem(raw_name))
                self.table.setItem(r, EXPR, QTableWidgetItem(str(row.get("expression") or "")))
                error = errors.get(r)
                value_item = QTableWidgetItem(error if error else fmt(values.get(name)))
                value_item.setFlags(value_item.flags() & ~Qt.ItemIsEditable)
                if error:
                    value_item.setForeground(Qt.red)
                    value_item.setToolTip(error)
                self.table.setItem(r, VALUE, value_item)
        finally:
            self._loading = False


# =========================================================================== variables a Part Studio defines (I2.6)

OVERRIDDEN = Qt.UserRole + 1        # on a name item: True when a later definition of the same name is in force


class DefinedVariables(QTableWidget):
    """The variables a Part Studio defines, from Document.variable_table(): name, value and where it is defined. A
    name a later Variable or Variable Studio reference defines again is marked overridden (struck through, with
    "overridden" in the last column); the definition in force carries no mark."""

    def __init__(self, parent=None):
        super().__init__(0, 3, parent)
        self.setHorizontalHeaderLabels(["Name", "Value", "Defined in"])
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setStretchLastSection(True)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.NoSelection)

    def populate(self, doc, variables_of=None):
        """`variables_of(source) -> {name: value} | None` supplies the Studios a reference reads when the document
        has no engine-set `var_inputs` of its own (the window's copy of a tab does not)."""
        if not hasattr(doc, "variable_table"):
            rows = []
        elif doc.var_inputs is None and variables_of is not None:
            doc.var_inputs = variables_of
            try:
                rows = doc.variable_table()
            finally:
                doc.var_inputs = None
        else:
            rows = doc.variable_table()
        self.setRowCount(len(rows))
        for r, row in enumerate(rows):
            origin = next((doc.feature(x["feature"]) for x in rows[r + 1:] if x["name"] == row["name"]), None)
            home = doc.feature(row["feature"])
            name = QTableWidgetItem("#" + row["name"])
            name.setData(OVERRIDDEN, bool(row["overridden"]))
            src = QTableWidgetItem((home or {}).get("name", row["feature"]))
            if row["overridden"]:
                font = name.font()
                font.setStrikeOut(True)
                name.setFont(font)
                name.setForeground(QColor("#8a919a"))
                src.setText("overridden")
                src.setForeground(QColor("#b26a00"))
                tip = "Overridden by " + ((origin or {}).get("name") or "a later definition")
                name.setToolTip(tip)
                src.setToolTip(tip)
            self.setItem(r, 0, name)
            self.setItem(r, 1, QTableWidgetItem(fmt(row["value"])))
            self.setItem(r, 2, src)

    def overridden(self, row):
        return bool(self.item(row, 0).data(OVERRIDDEN))


# =========================================================================== the window's Variable Studio tabs (I3.8)

class VariableTabs:
    """A Variable Studio tab of the window: a VariablePanel in a page of the window's view stack, with empty pages
    in the toolbar and left-panel stacks. `prepare` gives the document the few attributes the window's generic tab
    code reads (kind, listeners, notify, an empty model state); an edit in the table notifies the document's
    listeners, one of which sends the change to the engine (partstudio_engine.LiveTabs)."""

    def __init__(self, window):
        self.w = window
        self.stack = QStackedWidget()
        self.panels = {}                       # id(doc) -> (doc, VariablePanel)
        self.view_index = window.view_stack.addWidget(self.stack)
        self.tool_index = window.tool_stack.addWidget(QWidget())
        self.left_index = window.left_stack.addWidget(QWidget())

    @staticmethod
    def is_variables(doc):
        return getattr(doc, "kind", None) == "variables"

    def prepare(self, doc):
        """Make a VariableStudioDoc usable as a window tab (no-op for any other document)."""
        if not isinstance(doc, VariableStudioDoc) or self.is_variables(doc):
            return
        import uuid
        from ..features import ModelState
        doc.kind, doc.uid = "variables", uuid.uuid4().hex
        doc.listeners = []
        doc.notify = lambda d=doc: [cb() for cb in list(d.listeners)]
        doc.tab_id = getattr(doc, "tab_id", None)
        # the Part-Studio-shaped attributes some generic window code looks at (as AssemblyDoc has)
        doc.state, doc.features, doc.errors = ModelState(), [], {}
        doc.hidden_parts, doc.hidden_features = set(), set()
        doc.views = doc.rows                   # one list: so `MainWindow._has_content` sees the rows
        doc.can_undo = doc.can_redo = lambda: False
        self.prune()

    def prune(self):
        """Drop the panels of tabs the window no longer has."""
        live = {id(d) for d in self.w.docs.values()}
        for key in [k for k in self.panels if k not in live]:
            _doc, panel = self.panels.pop(key)
            self.stack.removeWidget(panel)
            panel.deleteLater()

    def panel_for(self, doc):
        hit = self.panels.get(id(doc))
        if hit is None:
            panel = VariablePanel(doc)
            panel.changed.connect(lambda d=doc: d.notify())
            self.stack.addWidget(panel)
            hit = self.panels[id(doc)] = (doc, panel)
        return hit[1]

    def show(self, doc):
        """The tab of `doc` became the current one."""
        w = self.w
        panel = self.panel_for(doc)
        panel.refresh()
        self.stack.setCurrentWidget(panel)
        w.view_stack.setCurrentIndex(self.view_index)
        w.tool_stack.setCurrentIndex(self.tool_index)
        w.left_stack.setCurrentIndex(self.left_index)
        w.viewport.drag_handler = None
        self.on_changed(doc)

    def on_changed(self, doc):
        w = self.w
        w.doc_label.setText(doc.name + ("  •" if doc.dirty else ""))
        w.setWindowTitle(f"{doc.name} — Variable Studio")
        w._refresh_tab_title(doc)
