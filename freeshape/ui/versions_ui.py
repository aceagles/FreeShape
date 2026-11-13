"""I2.10: the GUI for Versions of the open Document: Create version, the Versions list (open read-only, new Workspace
from a version, import again), the "update available" badge for cross-Document links, and the read-only view.

All logic lives in doc_folder.py and links.py; this module only calls it. It never runs git (gitstore.py is the
only module that does -- see tests/test_git_single_entry.py). The main window holds one `VersionsUI` and wires its
top-bar buttons and refresh calls (see the "Versions" section of mainwindow.py).
"""
from __future__ import annotations

import os
import time

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QToolButton, QVBoxLayout, QWidget,
)

from .. import doc_folder
from .. import doc_registry
from .. import kernel as K
from .. import links

APP_NAME = "FreeShape"


def _when(t):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else ""


def version_label(v):
    """One row of the Versions list: name, note (when it adds something) and date."""
    note = f" — {v['note']}" if v.get("note") and v["note"] != v["name"] else ""
    when = _when(v.get("time"))
    return f"{v['name']}{note}" + (f"    {when}" if when else "")


class CreateVersionDialog(QDialog):
    """Name and optional note. A refused name shows its error here and the dialog stays open."""

    def __init__(self, ui):
        super().__init__(ui.w)
        self.ui = ui
        self.created = None
        self.setWindowTitle("Create version")
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("v1")
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("What changed (optional)")
        form.addRow("Name", self.name_edit)
        form.addRow("Note", self.note_edit)
        lay.addLayout(form)
        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #d9534f;")
        lay.addWidget(self.error_label)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Create version")
        self.buttons.accepted.connect(self.submit)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)

    def submit(self):
        """Create the version; on a refusal show the text inline and keep the dialog open. True when created."""
        name = self.name_edit.text().strip()
        if not name:
            self.error_label.setText("Give the version a name.")
            return False
        err = self.ui.create_version(name, self.note_edit.text().strip())
        if err:
            self.error_label.setText(err)
            return False
        self.created = name
        self.accept()
        return True


class VersionsDialog(QDialog):
    """The Versions of the open Document, newest first, with the actions on the selected row."""

    def __init__(self, ui):
        super().__init__(ui.w)
        self.ui = ui
        self.setWindowTitle("Versions")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        self.problem = ui.problem()
        self.empty_label = QLabel("")
        self.empty_label.setObjectName("muted")
        lay.addWidget(self.empty_label)
        self.list = QListWidget()
        self.list.itemSelectionChanged.connect(self._sync)
        self.list.itemDoubleClicked.connect(lambda _it: self.open_selected())
        lay.addWidget(self.list)
        row = QHBoxLayout()
        self.open_btn = QPushButton("Open read-only")
        self.open_btn.clicked.connect(self.open_selected)
        self.workspace_btn = QPushButton("New Workspace from this version")
        self.workspace_btn.clicked.connect(self.new_workspace_selected)
        row.addWidget(self.open_btn)
        row.addWidget(self.workspace_btn)
        lay.addLayout(row)
        self.import_btn = QPushButton("Import again from source")
        self.import_btn.clicked.connect(self.import_again)
        lay.addWidget(self.import_btn)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        lay.addWidget(close)
        self.fill()

    def fill(self):
        self.list.clear()
        versions = []
        if self.problem is None:
            try:
                versions = self.ui.w.folder.versions()
            except K.KernelError as e:
                self.problem = str(e)
        for v in reversed(versions):
            it = QListWidgetItem(version_label(v))
            it.setData(256, v["name"])
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)
        self.empty_label.setText("" if self.list.count() or self.problem else "No versions yet. Use Create version.")
        self.empty_label.setVisible(bool(self.empty_label.text()))
        self.import_btn.setVisible(self.ui.import_source() is not None)
        self._sync()

    def selected(self):
        it = self.list.currentItem()
        return it.data(256) if it is not None else None

    def _sync(self):
        reason = self.problem
        have = self.selected() is not None
        for b in (self.open_btn, self.workspace_btn):
            b.setEnabled(reason is None and have)
            b.setToolTip(f"Unavailable: {reason}" if reason else "" if have else "Pick a version first")
        self.import_btn.setEnabled(reason is None)
        self.import_btn.setToolTip(f"Unavailable: {reason}" if reason else
                                   "Clone the sender's Document folder again to pick up its newer versions")

    def open_selected(self):
        name = self.selected()
        if name is not None and self.ui.open_view(name):
            self.accept()

    def new_workspace_selected(self):
        name = self.selected()
        if name is not None and self.ui.new_workspace_from(name):
            self.accept()

    def import_again(self):
        if self.ui.import_again():
            self.accept()


class UpdatesDialog(QDialog):
    """Cross-Document links whose pinned version is behind: one row per pin with Update, and Update all."""

    def __init__(self, ui):
        super().__init__(ui.w)
        self.ui = ui
        self.setWindowTitle("Updates available")
        self.setMinimumWidth(440)
        self.lay = QVBoxLayout(self)
        self.rows = QVBoxLayout()
        self.lay.addLayout(self.rows)
        self.all_btn = QPushButton("Update all")
        self.all_btn.clicked.connect(self.update_all)
        self.lay.addWidget(self.all_btn)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        self.lay.addWidget(close)
        self.update_buttons = {}
        self.fill()

    def fill(self):
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.update_buttons = {}
        ups = self.ui.updates()
        for u in ups:
            roww = QWidget()
            h = QHBoxLayout(roww)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(QLabel(f"{self.ui.document_name(u['document'])}: pinned {u['pinned']}, latest {u['latest']}"), 1)
            b = QPushButton("Update")
            b.clicked.connect(lambda _c=False, d=u["document"]: self.update_one(d))
            h.addWidget(b)
            self.update_buttons[u["document"]] = b
            self.rows.addWidget(roww)
        if not ups:
            self.rows.addWidget(QLabel("All links are up to date."))
        self.all_btn.setEnabled(bool(ups))

    def update_one(self, document):
        self.ui.update_links(document)
        self.fill()

    def update_all(self):
        self.ui.update_links(None)
        self.fill()


class VersionsUI:
    """Top-bar buttons, dialogs and the read-only view, for one MainWindow."""

    def __init__(self, window):
        self.w = window
        self.view = None            # {"fd": the read-only FolderDocument, "docs": [tab docs shown], "name": version}
        self.create_btn = self.versions_btn = self.updates_btn = self.back_btn = self.banner = None
        self._widgets = ()

    # ------------------------------------------------------------------ top bar
    def build(self, layout):
        def button(text, tip, slot):
            b = QToolButton()
            b.setObjectName("menuButton")
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda _c=False: slot())
            layout.addWidget(b)
            b.setVisible(False)
            return b
        self.create_btn = button("Create version", "Save and tag the Document as a named version", self.create_dialog)
        self.versions_btn = button("Versions", "List the versions of this Document", self.list_dialog)
        self.updates_btn = button("Updates", "Linked Documents have newer versions", self.updates_dialog)
        self.banner = QLabel("")
        self.banner.setObjectName("muted")
        self.banner.setVisible(False)
        layout.addWidget(self.banner)
        self.back_btn = button("Back to live Document", "Close the read-only version and return", self.close_view)
        self._widgets = (self.create_btn, self.versions_btn)

    def problem(self):
        fd = self.w.folder
        return fd.git_problem() if fd is not None else None

    def refresh(self):
        """Recompute visibility, tooltips and the update badge. Called on open, close, save and Workspace change."""
        fd = self.w.folder
        problem = self.problem()
        for b in self._widgets:
            b.setVisible(fd is not None)
            b.setEnabled(problem is None and self.view is None)
        why = f"Unavailable: {problem}" if problem else ("Close the read-only version first" if self.view else None)
        self.create_btn.setToolTip(why or "Save and tag the Document as a named version")
        self.versions_btn.setToolTip(f"Unavailable: {problem}" if problem else "List the versions of this Document")
        self.versions_btn.setEnabled(problem is None)
        count = len(self.updates()) if fd is not None and problem is None else 0
        self.updates_btn.setVisible(fd is not None and (count > 0 or problem is not None))
        self.updates_btn.setEnabled(problem is None and count > 0)
        if problem:
            self.updates_btn.setText("Updates")
            self.updates_btn.setToolTip(f"Unavailable: {problem}")
        else:
            self.updates_btn.setText(f"Updates ({count})")
            self.updates_btn.setToolTip(f"{count} linked Document(s) have newer versions: click to update")
        self.banner.setVisible(self.view is not None)
        self.back_btn.setVisible(self.view is not None)
        if self.view is not None:
            self.banner.setText(f"Viewing version “{self.view['name']}” (read-only)")

    def update_count(self):
        """The number shown on the badge (0 when it is hidden)."""
        return len(self.updates()) if self.w.folder is not None and self.problem() is None else 0

    # ------------------------------------------------------------------ create version
    def create_dialog(self):
        if self.w.folder is None or self.problem() is not None:
            return False
        dlg = CreateVersionDialog(self)
        dlg.exec()
        return dlg.created

    def create_version(self, name, note=""):
        """Save the Document through the window's save, then create the version. Returns None, or the error text."""
        fd = self.w.folder
        if fd is None:
            return "No Document is open."
        if self.view is not None:
            return "Close the read-only version first."
        self.w._leave_modes()
        if not self.w._save_document():
            return "The Document could not be saved, so no version was created."
        try:
            fd.create_version(name, note)
        except (K.KernelError, OSError) as e:
            return str(e)
        self.w.status.showMessage(f"Created version “{name}”", 5000)
        self.refresh()
        return None

    # ------------------------------------------------------------------ versions list
    def list_dialog(self):
        if self.w.folder is None or self.problem() is not None:
            return None
        dlg = VersionsDialog(self)
        dlg.exec()
        return dlg

    def new_workspace_from(self, version):
        name, ok = QInputDialog.getText(self.w, APP_NAME, f"Name for the new Workspace (from version “{version}”):")
        if not ok or not name.strip():
            return False
        return self.w.create_workspace(name.strip(), version)

    # ------------------------------------------------------------------ read-only view
    def open_view(self, name):
        """Show the tabs of version `name` read-only, next to the live tabs. Returns the view or None."""
        fd = self.w.folder
        if fd is None:
            return None
        self.close_view()
        w = self.w
        w._leave_modes()
        try:
            view = fd.open_version(name)
        except (K.KernelError, OSError) as e:
            QMessageBox.warning(w, APP_NAME, f"Couldn't open version “{name}”:\n{e}")
            return None
        live = w._folder_tab(w.doc)
        shown, current = [], None
        for tab in view.tabs:
            if tab.doc is None:
                continue
            tab.doc.name = f"{tab.name or tab.doc.name}  [{name}, read-only]"
            tab.doc.read_only_version = name
            w._add_tab(tab.doc, switch=False)
            if tab.kind == "assembly":          # _add_tab points a loose assembly at the window's resolver
                tab.doc.resolver = view.resolver.resolve_source
            elif tab.kind == "drawing":
                tab.doc.resolver = view.resolver.resolve_drawing_source
            tab.doc.dirty = False
            w._refresh_tab_title(tab.doc)
            shown.append(tab.doc)
            if live is not None and tab.id == live.id:
                current = tab.doc
        self.view = {"fd": view, "docs": shown, "name": name, "back": w.doc}
        if shown:
            w.tabs.setCurrentIndex(w._tab_index(current or shown[0]))
        w.show_workspace()
        w.status.showMessage(f"Viewing version “{name}” read-only. Use Back to live Document to return.", 8000)
        self.refresh()
        return view

    def close_view(self):
        """Close the read-only view, if one is open, and return to the live Document."""
        v = self.view
        if v is None:
            return False
        self.view = None
        w = self.w
        w._leave_modes()
        back = v["back"]
        if back is not None and w._tab_index(back) >= 0:
            w.tabs.setCurrentIndex(w._tab_index(back))
        for doc in v["docs"]:
            doc.dirty = False
            w._remove_tab_silently(doc)
        v["fd"].close()
        self.refresh()
        return True

    def refuse_save(self):
        """True (after saying why) when the current tab belongs to a read-only version, so Save must not run."""
        ver = getattr(self.w.doc, "read_only_version", None)
        if ver is None:
            return False
        self.w.status.showMessage(f"Version “{ver}” is read-only: it can't be saved. Use Back to live Document, or "
                                  "start a Workspace from this version to edit it.", 10000)
        QMessageBox.information(self.w, APP_NAME, f"Version “{ver}” is read-only, so it can't be saved or "
                                "edited. Close it with “Back to live Document”, or use “New Workspace from this "
                                "version” to work from it.")
        return True

    # ------------------------------------------------------------------ cross-Document updates
    def updates(self):
        fd = self.w.folder
        return links.cross_link_updates(fd) if fd is not None else []

    def document_name(self, doc_id):
        fd = self.w.folder
        reg = (fd.registry if fd is not None else None) or doc_registry.default_registry()
        path = reg.trusted_path(doc_id)
        if path:
            try:
                return doc_folder.FolderDocument.open(path).name
            except (K.KernelError, OSError, ValueError):
                pass
        return str(doc_id)

    def updates_dialog(self):
        if self.w.folder is None or self.problem() is not None:
            return None
        dlg = UpdatesDialog(self)
        dlg.exec()
        return dlg

    def update_links(self, document=None):
        """Re-pin the cross-Document links (of `document`, or all) to the newest version and re-render."""
        fd = self.w.folder
        if fd is None:
            return 0
        n = links.update_cross_links(fd, document=document)
        for tab in fd.tabs:
            if tab.doc is not None:
                self.w._refresh_tab_title(tab.doc)
        self.w._on_doc_changed()
        if n:
            self.w.status.showMessage(f"Updated {n} link(s) to the newest version", 5000)
        self.refresh()
        return n

    # ------------------------------------------------------------------ import again
    def import_source(self):
        """The sender's folder when the open Document was imported (never for one the app created), else None."""
        fd = self.w.folder
        if fd is None or fd.path is None:
            return None
        reg = fd.registry or doc_registry.default_registry()
        here = os.path.realpath(fd.path)
        for r in reg.records(fd.id):
            if os.path.realpath(r["path"]) == here and not r.get("created") and r.get("source"):
                return r["source"]
        return None

    def import_again(self):
        """After a confirmation naming the source folder, clone it again and open the result in place of this one."""
        w = self.w
        source = self.import_source()
        if source is None:
            return False
        r = QMessageBox.question(w, APP_NAME, f"Import “{os.path.basename(source)}” again from\n{source}?\n\n"
                                 "This replaces the imported copy with a fresh one and picks up the versions the "
                                 "sender has added since.", QMessageBox.Yes | QMessageBox.No)
        if r != QMessageBox.Yes:
            return False
        old_path = w.folder.path
        registry = w.folder.registry or doc_registry.default_registry()
        if not w._close_document():
            return False
        try:
            fd = doc_folder.FolderDocument.reimport_document(source, registry=registry)
        except (K.KernelError, OSError, ValueError) as e:
            QMessageBox.warning(w, APP_NAME, f"Couldn't import again:\n{e}")
            w.open_document(old_path)            # the earlier copy is untouched by a failed import
            return False
        w._adopt_document(fd)
        w.show_workspace()
        w.status.showMessage(f"Imported again from {source}", 6000)
        return True
