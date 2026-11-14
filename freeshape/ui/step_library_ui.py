"""Component library browser: search a folder of STEP files, see a preview of each, insert the chosen one.

`StepLibraryDialog` lists step_library.scan(folder) in an icon grid. A tile asks `ThumbService` for its
thumbnail only when the view paints it, so a library of ten thousand files opens at once and draws what is
on screen. Thumbnails are PNGs beside the Home thumbnails, keyed by the file's path, size and modified time;
a file that cannot be drawn leaves a marker so it is not tried again. The dialog inserts through the callback
it is given (MainWindow.import_step_file)."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from collections import OrderedDict

from PySide6.QtCore import (QAbstractListModel, QCoreApplication, QModelIndex, QObject, QProcess, QSize, Qt, QTimer, Signal)
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListView,
                               QPushButton, QSplitter, QVBoxLayout, QWidget)

from .. import step_library as L
from .preview_rows import placeholder_icon

THUMB_PX = 256            # the size a thumbnail is drawn and cached at
TILE_PX = 128             # the size it is shown at in the grid
MAX_PENDING = 300         # tiles scrolled past long ago are dropped from the queue
JOB_TIMEOUT_MS = 120_000
ICON_CACHE = 600
SETTING = "step_library/folder"
DEFAULT_FOLDER = os.path.join(os.path.expanduser("~"), "component-library")


def thumb_file(path):
    """Where the thumbnail of `path` is cached. A changed file gets a new name."""
    from .home import thumbs_dir
    st = os.stat(path)
    key = f"steplib:{os.path.normcase(os.path.abspath(path))}:{st.st_mtime_ns}:{st.st_size}:{THUMB_PX}"
    return os.path.join(thumbs_dir(), "steplib-" + hashlib.sha1(key.encode()).hexdigest()[:20] + ".png")


def library_folder(settings):
    """The remembered folder, else $FREESHAPE_STEP_LIBRARY, else ~/component-library. "" when none exists."""
    for cand in (str(settings.value(SETTING, "") or "") if settings is not None else "",
                 os.environ.get("FREESHAPE_STEP_LIBRARY", ""), DEFAULT_FOLDER):
        if cand and os.path.isdir(cand):
            return cand
    return ""


class ThumbService(QObject):
    """Draws thumbnails in background processes (freeshape.step_thumb_worker), newest request first.

    `request(path)` returns at once; `ready(path)` fires when the PNG is on disk or the file was given up on.
    With FREESHAPE_NO_STEP_WORKER set the drawing happens in this process, one file per event-loop turn."""

    ready = Signal(str)

    def __init__(self, parent=None, workers=None):
        super().__init__(parent)
        self.inline = bool(os.environ.get("FREESHAPE_NO_STEP_WORKER"))
        self.n = workers or max(1, min(3, (os.cpu_count() or 2) // 2))
        self.pending: list[str] = []          # the last one is drawn next
        self.procs: list[dict] = []
        self._inline_queued = False
        self.stats = {"drawn": 0, "failed": 0}
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)

    # -- the cache on disk
    @staticmethod
    def cached(path):
        """The thumbnail file of `path` if it has been drawn, else None."""
        try:
            f = thumb_file(path)
        except OSError:
            return None
        return f if os.path.exists(f) else None

    @staticmethod
    def failed(path):
        try:
            return os.path.exists(thumb_file(path) + ".fail")
        except OSError:
            return True

    # -- queue
    def request(self, path):
        if self.failed(path) or any(w["job"] == path for w in self.procs):
            return
        if path in self.pending:
            self.pending.remove(path)
        self.pending.append(path)
        del self.pending[:-MAX_PENDING]
        self._pump()

    def _pump(self):
        if self.inline:
            if self.pending and not self._inline_queued:
                self._inline_queued = True
                QTimer.singleShot(0, self._inline_one)
            return
        while self.pending:
            w = next((w for w in self.procs if w["job"] is None), None)
            if w is None:
                if len(self.procs) >= self.n:
                    return
                w = self._spawn()
            self._give(w, self.pending.pop())

    def _finish(self, path, ok, error=""):
        self.stats["drawn" if ok else "failed"] += 1
        if not ok:
            try:
                with open(thumb_file(path) + ".fail", "w", encoding="utf-8") as fh:
                    fh.write(error)
            except OSError:
                pass
        self.ready.emit(path)

    # -- in this process (tests, and FREESHAPE_NO_STEP_WORKER)
    def _inline_one(self):
        self._inline_queued = False
        if self.pending:
            self._draw_here(self.pending.pop())
        self._pump()

    def _draw_here(self, path):
        from ..step_thumb_worker import render
        try:
            render(path, thumb_file(path), THUMB_PX)
            self._finish(path, True)
        except Exception as e:
            self._finish(path, False, f"{type(e).__name__}: {e}")

    def drain(self):
        """Draw everything pending now, in this process (tests)."""
        while self.pending:
            self._draw_here(self.pending.pop())

    # -- worker processes
    def _spawn(self):
        from .progress import ROOT
        proc = QProcess(self)
        proc.setWorkingDirectory(ROOT)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        timer = QTimer(self)
        timer.setSingleShot(True)
        w = {"proc": proc, "job": None, "buf": "", "timer": timer}
        proc.readyReadStandardOutput.connect(lambda w=w: self._read(w))
        proc.finished.connect(lambda *_a, w=w: self._died(w))
        proc.errorOccurred.connect(lambda _e, w=w: self._died(w))
        timer.timeout.connect(proc.kill)                       # a file that never finishes: give up on it
        if getattr(sys, "frozen", False):
            proc.start(sys.executable, ["--step-thumb-worker"])
        else:
            proc.start(sys.executable, ["-m", "freeshape.step_thumb_worker"])
        self.procs.append(w)
        return w

    def _give(self, w, path):
        try:
            job = {"path": path, "out": thumb_file(path), "size": THUMB_PX}
        except OSError:
            self._finish(path, False, "file not found")
            return
        w["job"] = path
        w["timer"].start(JOB_TIMEOUT_MS)
        w["proc"].write((json.dumps(job) + "\n").encode())

    def _read(self, w):
        w["buf"] += bytes(w["proc"].readAllStandardOutput()).decode(errors="ignore")
        *lines, w["buf"] = w["buf"].split("\n")
        for ln in lines:
            if not ln.startswith("@@FS "):
                continue
            try:
                answer = json.loads(ln[5:])
            except ValueError:
                continue
            w["timer"].stop()
            w["job"] = None
            self._finish(str(answer.get("path")), bool(answer.get("ok")), str(answer.get("error") or ""))
        self._pump()

    def _died(self, w):
        if w not in self.procs:
            return
        self.procs.remove(w)
        w["timer"].stop()
        job, w["job"] = w["job"], None
        w["proc"].deleteLater()
        if job is not None:
            self._finish(job, False, "the STEP reader stopped on this file")
        self._pump()

    def shutdown(self):
        self.pending.clear()
        for w in list(self.procs):
            self.procs.remove(w)
            w["timer"].stop()
            w["proc"].kill()
            w["proc"].waitForFinished(1000)


class LibraryModel(QAbstractListModel):
    """The entries shown in the grid. The icon of a row is its cached thumbnail; asking for a missing one
    queues it, and the row repaints when `ThumbService.ready` names its file."""

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.entries: list = []
        self.rows: dict = {}
        self.icons: OrderedDict = OrderedDict()
        service.ready.connect(self._thumb_ready)

    def set_entries(self, entries):
        self.beginResetModel()
        self.entries = list(entries)
        self.rows = {e.path: i for i, e in enumerate(self.entries)}
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.entries)

    def entry(self, index):
        return self.entries[index.row()] if index.isValid() and index.row() < len(self.entries) else None

    def data(self, index, role=Qt.DisplayRole):
        e = self.entry(index)
        if e is None:
            return None
        if role == Qt.DisplayRole:
            return e.name
        if role == Qt.ToolTipRole:
            return e.rel
        if role == Qt.DecorationRole:
            return self._icon(e.path)
        return None

    def _icon(self, path):
        ic = self.icons.get(path)
        if ic is not None:
            self.icons.move_to_end(path)
            return ic
        f = self.service.cached(path)
        if f is None:
            self.service.request(path)
            return placeholder_icon(TILE_PX)
        pm = QPixmap(f)
        if pm.isNull():
            return placeholder_icon(TILE_PX)
        ic = QIcon(pm.scaled(TILE_PX, TILE_PX, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.icons[path] = ic
        while len(self.icons) > ICON_CACHE:
            self.icons.popitem(last=False)
        return ic

    def _thumb_ready(self, path):
        row = self.rows.get(path)
        if row is not None:
            ix = self.index(row)
            self.dataChanged.emit(ix, ix, [Qt.DecorationRole])


def _size_text(n):
    return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{max(1, round(n / 1e3))} kB"


class StepLibraryDialog(QDialog):
    """Search box, folder filter, a grid of previews and a detail pane with Insert / Open in new tab.

    `on_insert(path, into_current)` does the import. The dialog is not modal and stays open, so several parts
    can be inserted one after another."""

    def __init__(self, settings, on_insert, parent=None, folder=None, service=None):
        super().__init__(parent)
        self.setWindowTitle("Component library")
        self.resize(1040, 680)
        self.settings, self.on_insert = settings, on_insert
        self.service = service or ThumbService(self)
        self.entries: list = []
        self.folder = ""

        self.search = QLineEdit()
        self.search.setClearButtonEnabled(True)
        self.group = QComboBox()
        self.group.setMinimumWidth(220)
        self.count = QLabel()
        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(self.group)
        top.addWidget(self.count)

        self.model = LibraryModel(self.service, self)
        self.view = QListView()
        self.view.setModel(self.model)
        self.view.setViewMode(QListView.IconMode)
        self.view.setResizeMode(QListView.Adjust)
        self.view.setMovement(QListView.Static)
        self.view.setUniformItemSizes(True)
        self.view.setLayoutMode(QListView.Batched)
        self.view.setBatchSize(400)
        self.view.setIconSize(QSize(TILE_PX, TILE_PX))
        self.view.setGridSize(QSize(TILE_PX + 28, TILE_PX + 50))
        self.view.setWordWrap(True)
        self.view.setTextElideMode(Qt.ElideRight)
        self.view.setSelectionMode(QListView.SingleSelection)

        self.big = QLabel()
        self.big.setFixedSize(THUMB_PX, THUMB_PX)
        self.big.setAlignment(Qt.AlignCenter)
        self.title = QLabel()
        self.title.setWordWrap(True)
        f = self.title.font()
        f.setPointSizeF(f.pointSizeF() * 1.2)
        self.title.setFont(f)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.info.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.insert_btn = QPushButton("Insert into this tab")
        self.insert_btn.setDefault(True)
        self.new_tab_btn = QPushButton("Open in new tab")
        side = QWidget()
        sv = QVBoxLayout(side)
        sv.setContentsMargins(8, 0, 0, 0)
        sv.addWidget(self.big)
        sv.addWidget(self.title)
        sv.addWidget(self.info, 1)
        sv.addWidget(self.insert_btn)
        sv.addWidget(self.new_tab_btn)
        side.setFixedWidth(THUMB_PX + 16)

        split = QSplitter()
        split.addWidget(self.view)
        split.addWidget(side)
        split.setStretchFactor(0, 1)
        split.setCollapsible(1, False)

        self.folder_label = QLabel()
        self.folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        change = QPushButton("Change folder…")
        rescan = QPushButton("Rescan")
        close = QPushButton("Close")
        bottom = QHBoxLayout()
        bottom.addWidget(self.folder_label, 1)
        bottom.addWidget(change)
        bottom.addWidget(rescan)
        bottom.addWidget(close)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(split, 1)
        lay.addLayout(bottom)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(150)
        self._debounce.timeout.connect(self.refilter)
        self.search.textChanged.connect(lambda _t: self._debounce.start())
        self.search.returnPressed.connect(self.refilter)
        self.group.currentIndexChanged.connect(lambda _i: self.refilter())
        self.view.selectionModel().currentChanged.connect(lambda _c, _p: self._show_current())
        self.view.doubleClicked.connect(lambda _ix: self.insert(True))
        self.insert_btn.clicked.connect(lambda: self.insert(True))
        self.new_tab_btn.clicked.connect(lambda: self.insert(False))
        self.service.ready.connect(self._thumb_ready)
        change.clicked.connect(self.choose_folder)
        rescan.clicked.connect(lambda: self.set_folder(self.folder))
        close.clicked.connect(self.close)

        self.set_folder(folder if folder is not None else library_folder(settings))
        self.search.setFocus()

    # -- the folder
    def set_folder(self, folder):
        self.folder = folder or ""
        self.entries = L.scan(self.folder) if self.folder else []
        self.group.blockSignals(True)
        self.group.clear()
        self.group.addItem("All folders", None)
        for g in L.groups(self.entries):
            self.group.addItem(g, g)
        self.group.blockSignals(False)
        self.search.setPlaceholderText(f"Search {len(self.entries):,} parts: name, tag or folder")
        self.folder_label.setText(self.folder or "No library folder chosen. Choose a folder that holds STEP files.")
        self.refilter()

    def choose_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Component library folder", self.folder or os.path.expanduser("~"))
        if d:
            if self.settings is not None:
                self.settings.setValue(SETTING, d)
            self.set_folder(d)

    # -- the list
    def refilter(self):
        self._debounce.stop()
        hits = L.search(self.entries, self.search.text(), self.group.currentData())
        self.model.set_entries(hits)
        self.count.setText(f"{len(hits):,} of {len(self.entries):,}")
        if hits:
            self.view.setCurrentIndex(self.model.index(0))
        self._show_current()

    def current(self):
        return self.model.entry(self.view.currentIndex())

    def _show_current(self):
        e = self.current()
        self.insert_btn.setEnabled(e is not None)
        self.new_tab_btn.setEnabled(e is not None)
        if e is None:
            self.big.setPixmap(placeholder_icon(THUMB_PX).pixmap(THUMB_PX, THUMB_PX))
            self.title.setText("")
            self.info.setText("Nothing matches." if self.entries else "")
            return
        f = self.service.cached(e.path)
        pm = QPixmap(f) if f else QPixmap()
        if pm.isNull():
            pm = placeholder_icon(THUMB_PX).pixmap(THUMB_PX, THUMB_PX)
            if not self.service.failed(e.path):
                self.service.request(e.path)
        self.big.setPixmap(pm)
        self.title.setText(e.name)
        lines = [e.description] if e.description and e.description.rstrip(".").lower() != e.name.lower() else []
        if e.tags:
            lines.append("Tags: " + ", ".join(e.tags))
        lines.append(e.rel)
        lines.append(_size_text(e.size))
        if self.service.failed(e.path):
            lines.append("No preview: this file could not be drawn. It may still import.")
        self.info.setText("\n\n".join(lines))

    def _thumb_ready(self, path):
        e = self.current()
        if e is not None and e.path == path:
            self._show_current()

    # -- inserting
    def insert(self, into_current):
        e = self.current()
        if e is not None:
            self.on_insert(e.path, into_current)

    def closeEvent(self, ev):
        self.service.shutdown()               # the readers hold memory; they start again when the window reopens
        super().closeEvent(ev)
