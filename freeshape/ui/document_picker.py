"""Pick something out of another Document: a Document, one of its Versions, and (for an insert) a Part Studio tab
and part. `DocumentVersionPicker` is the Document + Version half, built to be shared (the Insert panel's
OtherDocumentDialog uses it; a Derived dialog can too). Only Versions are offered: a cross-Document link pins one."""
from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import kernel as K
from .. import links

ROLE_ROW = Qt.UserRole + 5


class DocumentVersionPicker(QWidget):
    """Two combos, objectNames `picker_document` and `picker_version`. `selection()` is (document id, Version name)
    or None. Lists the registry's trusted Documents whose folder exists, except `exclude_id` (the open Document)."""
    changed = Signal()

    def __init__(self, registry, exclude_id=None, parent=None):
        super().__init__(parent)
        self.registry = registry
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        self.doc_combo = QComboBox()
        self.doc_combo.setObjectName("picker_document")
        self.version_combo = QComboBox()
        self.version_combo.setObjectName("picker_version")
        form.addRow("Document", self.doc_combo)
        form.addRow("Version", self.version_combo)
        self.note = QLabel("")
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        form.addRow(self.note)
        docs = registry.list_documents() if registry is not None else []
        for d in docs:
            if not d["missing"] and d["id"] != exclude_id:
                self.doc_combo.addItem(d["name"], d["id"])
        self.doc_combo.currentIndexChanged.connect(self._fill_versions)
        self.version_combo.currentIndexChanged.connect(lambda _i: self.changed.emit())
        self._fill_versions()

    def _fill_versions(self):
        self.version_combo.blockSignals(True)
        self.version_combo.clear()
        self.note.setText("")
        doc = self.doc_combo.currentData()
        if doc is None:
            self.note.setText("No other Documents on this machine.")
        else:
            try:
                versions = self.registry.describe(doc)["versions"]
            except K.KernelError as e:
                versions = []
                self.note.setText(str(e))
            for v in versions:
                self.version_combo.addItem(v, v)
            if doc is not None and not versions and not self.note.text():
                self.note.setText("This Document has no Versions yet: create one to insert from it.")
        self.version_combo.blockSignals(False)
        self.changed.emit()

    def selection(self):
        doc, ver = self.doc_combo.currentData(), self.version_combo.currentData()
        return (doc, ver) if doc is not None and ver is not None else None

    def select(self, document, version):
        """Choose a Document by id and one of its Versions; False if either is not listed."""
        i = self.doc_combo.findData(document)
        if i < 0:
            return False
        self.doc_combo.setCurrentIndex(i)
        j = self.version_combo.findData(version)
        if j < 0:
            return False
        self.version_combo.setCurrentIndex(j)
        return True


def version_studios(folder, document, version, registry=None):
    """The Part Studio tabs of `document` at `version` with their parts, read-only through the open Document's
    resolver: [{tab, name, parts: [{id, name}]}]. A tab that cannot be read lists no parts."""
    desc = (registry or folder.registry).describe(document, version)
    out = []
    for t in desc["tabs"]:
        if t.get("kind") != "studio":
            continue
        src = {"kind": "studio", "tab": t["id"], "studio_name": t["name"], "document": document,
               "version": version}
        try:
            parts = [{"id": p.id, "name": p.name} for p in folder.resolver._cross_doc(src, "studio").state.parts.values()]
        except K.KernelError:
            parts = []
        out.append({"tab": t["id"], "name": t["name"], "parts": parts})
    return out


class OtherDocumentDialog(QDialog):
    """Insert from another Document: the picker, then a tree of its Part Studios and parts. Rows carry the keys
    document, version, tab, part (ROLE_ROW data); `sources()` is the instance sources of the chosen rows."""

    def __init__(self, folder, parent=None):
        super().__init__(parent)
        self.folder = folder
        self.setWindowTitle("Insert from another Document")
        self.setMinimumWidth(380)
        lay = QVBoxLayout(self)
        self.picker = DocumentVersionPicker(folder.registry, exclude_id=getattr(folder, "id", None))
        lay.addWidget(self.picker)
        self.tree = QTreeWidget()
        self.tree.setObjectName("picker_parts")
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumHeight(200)
        lay.addWidget(self.tree)
        self.msg = QLabel("")
        self.msg.setObjectName("muted")
        self.msg.setWordWrap(True)
        lay.addWidget(self.msg)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Insert")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.tree.itemDoubleClicked.connect(lambda *_: self.accept())
        self.picker.changed.connect(self.fill)
        self.fill()

    def fill(self):
        self.tree.clear()
        self.msg.setText("")
        sel = self.picker.selection()
        if sel is None:
            return
        doc, ver = sel
        try:
            studios = version_studios(self.folder, doc, ver)
        except K.KernelError as e:
            self.msg.setText(str(e))
            return
        for s in studios:
            top = QTreeWidgetItem([s["name"]])
            top.setData(0, ROLE_ROW, {"document": doc, "version": ver, "tab": s["tab"], "part": None,
                                      "name": s["name"]})
            for p in s["parts"]:
                it = QTreeWidgetItem([p["name"]])
                it.setData(0, ROLE_ROW, {"document": doc, "version": ver, "tab": s["tab"], "part": p["id"],
                                         "name": s["name"]})
                top.addChild(it)
            self.tree.addTopLevelItem(top)
            top.setExpanded(True)
        if not studios:
            self.msg.setText("This Version has no Part Studios.")

    def item_for(self, tab, part=None):
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            if top.data(0, ROLE_ROW)["tab"] != tab:
                continue
            if part is None:
                return top
            for j in range(top.childCount()):
                if top.child(j).data(0, ROLE_ROW)["part"] == part:
                    return top.child(j)
        return None

    def choose(self, document, version, tab, part=None):
        """Select a row by keys; False if the Document, Version, tab or part is not listed."""
        if not self.picker.select(document, version):
            return False
        it = self.item_for(tab, part)
        if it is None:
            return False
        self.tree.setCurrentItem(it)
        return True

    def sources(self):
        """The instance source of each chosen part: the selected part, or every part of a selected tab."""
        it = self.tree.currentItem()
        row = it.data(0, ROLE_ROW) if it is not None else None
        if row is None:
            return []
        parts = [row["part"]] if row["part"] is not None else \
            [c.data(0, ROLE_ROW)["part"] for c in (it.child(i) for i in range(it.childCount()))]
        tab = SimpleNamespace(id=row["tab"], name=row["name"])
        return [links.studio_source(tab, p, document=row["document"], version=row["version"]) for p in parts]
