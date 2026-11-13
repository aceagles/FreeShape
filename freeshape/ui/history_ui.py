"""The Compare Versions panel: pick two Versions of the open Document (or one Version and the Document as it is now)
and read, per Part Studio tab, which features were added, removed and changed.

Versions are git tags of the whole Document (ADR 0001). Each one is read through FolderDocument.open_version (a
read-only scratch copy), never by checking out; the comparison itself is history.compare_documents. The panel is
opened by VersionsUI.compare (the Versions list's Compare button, the toolbar's history button and the palette's
Compare Versions command) and floats over the view."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QFrame, QLabel, QListWidget, QListWidgetItem, QTextBrowser, QVBoxLayout,
)

from .. import history as H
from .. import kernel as K
from .assembly_ui import _floating, _head, _hline

NOW = "__now__"          # the list row for the open Document as it is now


class HistoryPanel(QFrame):
    def __init__(self, ui, select=()):
        from .versions_ui import version_label
        main = ui.w
        super().__init__(main.view_stack)
        _floating(self)
        self.ui = ui
        self.main = main
        self.folder = main.folder
        self.result = None           # the last history.compare_versions result shown
        self.pair = None             # (old, new): Version names, H.NOW for the open Document
        self._versions = {}          # name -> document_snapshot of that Version (Versions never change)
        self._watched = []           # tab docs whose edits re-run a comparison with now
        self._listener = lambda: QTimer.singleShot(0, self._doc_changed)
        self.setMinimumWidth(340)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(6)
        head, t = _head(self, "Compare Versions", "history", None, self.close_panel, cancel_tip="Close (Esc)")
        t.setReadOnly(True)
        lay.addLayout(head)
        lay.addWidget(_hline())
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.itemSelectionChanged.connect(self._selection_changed)
        self.list.setMinimumHeight(150)
        lay.addWidget(self.list, 2)
        self.hint = QLabel("Click a version to see what has changed since. Ctrl+click two to compare them.")
        self.hint.setWordWrap(True)
        self.hint.setObjectName("muted")
        lay.addWidget(self.hint)
        self.text = QTextBrowser()
        self.text.setMinimumHeight(170)
        lay.addWidget(self.text, 3)
        self._label_of = version_label
        self.fill(select)
        host = main.view_stack
        self.resize(380, min(720, max(520, host.height() - 24)))
        self.move(12, 12)
        self.show()
        self.raise_()

    # ------------------------------------------------------------------ list
    def fill(self, select=()):
        """List now and the Versions, newest first, and select `select` (Version names; H.NOW for now)."""
        keep = [NOW if s is H.NOW else s for s in select]
        self.list.blockSignals(True)
        self.list.clear()
        it = QListWidgetItem("● Now")
        it.setData(Qt.UserRole, NOW)
        f = it.font()
        f.setBold(True)
        it.setFont(f)
        self.list.addItem(it)
        try:
            versions = self.folder.versions()
        except K.KernelError as e:
            versions = []
            self.text.setHtml(f"<p style='color:#d93025'>{H._esc(e)}</p>")
        for v in reversed(versions):
            it = QListWidgetItem(self._label_of(v))
            it.setData(Qt.UserRole, v["name"])
            self.list.addItem(it)
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) in keep:
                self.list.item(i).setSelected(True)
        self.list.blockSignals(False)
        if not versions:
            self.text.setHtml("<p style='color:#888'>No versions yet. Use <b>Create version</b> to keep the "
                              "Document as it is now, then compare it with later states.</p>")
        self._selection_changed()

    def _selected(self):
        """The selected rows' keys, newest first (now, then the Versions as listed)."""
        order = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]
        return sorted((it.data(Qt.UserRole) for it in self.list.selectedItems()), key=order.index)

    def _selection_changed(self):
        ids = self._selected()
        if len(ids) > 2:
            self.list.blockSignals(True)
            for it in self.list.selectedItems():
                if it.data(Qt.UserRole) not in ids[:2]:
                    it.setSelected(False)
            self.list.blockSignals(False)
            ids = ids[:2]
        if not ids or ids == [NOW]:
            self.pair = self.result = None
            if ids == [NOW] and self.list.count() > 1:
                self.text.setHtml("<p style='color:#888'>Pick a version to compare with now.</p>")
            return
        new, old = (ids[0], ids[1]) if len(ids) == 2 else (NOW, ids[0])
        self.pair = (old, H.NOW if new == NOW else new)
        self.text.setHtml("<p style='color:#888'>Working out what changed…</p>")
        QTimer.singleShot(10, self.compare)

    # ------------------------------------------------------------------ comparing
    def compare(self):
        """Compare the selected pair now and show it. Returns the result (None when nothing is selected or it
        failed)."""
        if self.pair is None:
            return None
        old, new = self.pair
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            res = H.compare_versions(self.folder, old, new, cache=self._versions)
        except (K.KernelError, OSError) as e:
            res = None
            self.text.setHtml(f"<p style='color:#d93025'>Couldn't compare: {H._esc(e)}</p>")
        finally:
            QApplication.restoreOverrideCursor()
        self.result = res
        if res is not None:
            self.text.setHtml(H.document_html(res))
        self._watch(new is H.NOW)
        return res

    def _watch(self, on):
        """Follow edits to the open tabs while one side is now."""
        for d in self._watched:
            if self._listener in d.listeners:
                d.listeners.remove(self._listener)
        self._watched = [t.doc for t in self.folder.tabs if t.doc is not None
                         and hasattr(t.doc, "listeners")] if on else []
        for d in self._watched:
            d.listeners.append(self._listener)

    def _doc_changed(self):
        if self.main.folder is not self.folder:
            self.close_panel()
        elif self.pair is not None and self.pair[1] is H.NOW:
            self.compare()

    def refresh(self):
        """After a save, a new Version or a Workspace change: list the Versions again, keeping the selection.
        Closes the panel when the window no longer shows this Document."""
        if self.main.folder is not self.folder:
            self.close_panel()
            return
        self.fill([H.NOW if s == NOW else s for s in self._selected()])

    # ------------------------------------------------------------------ closing
    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close_panel()
        else:
            super().keyPressEvent(e)

    def close_panel(self):
        self._watch(False)
        if getattr(self.ui, "compare_panel", None) is self:
            self.ui.compare_panel = None
        self.hide()
        self.deleteLater()
