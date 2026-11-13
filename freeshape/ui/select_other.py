"""A small pick list ("Select other"): rows of (label, payload) with hover, choose and close callbacks.
Knows nothing about sketches or picks, so any tool that has several candidates under the cursor can reuse it."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidget, QListWidgetItem


class SelectOtherPopup(QListWidget):
    """rows: [(label, payload)]. on_hover(payload) fires as the highlighted row changes; on_choose(payload, modifiers)
    on a click or Enter; on_close() once, however the popup goes away."""

    def __init__(self, rows, on_hover=None, on_choose=None, on_close=None, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.on_hover, self.on_choose, self.on_close = on_hover, on_choose, on_close
        self._closed = False
        self._mods = Qt.NoModifier
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        for label, payload in rows:
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, payload)
            self.addItem(it)
        self.currentItemChanged.connect(lambda cur, _prev: self._hover(cur))
        self.itemEntered.connect(self.setCurrentItem)
        self.itemClicked.connect(lambda it: self.choose(it))
        h = self.sizeHintForRow(0) * self.count() + 2 * self.frameWidth() + 2
        w = max((self.sizeHintForColumn(0), 120)) + 2 * self.frameWidth() + 4
        self.resize(w, h)

    def popup_at(self, global_pos):
        self.move(global_pos)
        self.show()
        self.setFocus()

    def _hover(self, item):
        if item is not None and self.on_hover:
            self.on_hover(item.data(Qt.UserRole))

    def choose(self, item=None):
        item = item or self.currentItem()
        if item is None:
            return
        mods = self._mods
        payload = item.data(Qt.UserRole)
        self.close()
        if self.on_choose:
            self.on_choose(payload, mods)

    def mousePressEvent(self, e):
        self._mods = e.modifiers()
        super().mousePressEvent(e)

    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key_Return, Qt.Key_Enter):
            self._mods = e.modifiers()
            self.choose()
        elif k == Qt.Key_Escape:
            self.close()
        elif k in (Qt.Key_Up, Qt.Key_Down):
            super().keyPressEvent(e)
        else:
            e.accept()

    def closeEvent(self, e):
        super().closeEvent(e)
        if not self._closed:
            self._closed = True
            if self.on_close:
                self.on_close()
