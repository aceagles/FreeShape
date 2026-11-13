"""VariablePanel: a standalone table widget over a VariableStudioDoc -- add, edit and delete rows of
`{name, expression}`. No MainWindow wiring here; opening this as a tab is I3.8's job."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QPushButton, QTableWidget,
                                QTableWidgetItem, QVBoxLayout, QWidget)

from ..expr import fmt

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
