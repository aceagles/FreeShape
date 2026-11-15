"""The Interference detection panel: where parts of one tab overlap, found off the GUI thread.

The panel is the same for every tab kind; it talks to its tab through a host adapter (AssemblyHost here; the Part
Studio gets its own later). The check runs `interference.find_interferences` on a one-thread executor polled by a
QTimer, as HistoryPanel._start_3d does: the bodies are snapshotted on the GUI thread, a shared progress counter is
read by the timer, a flag cancels between pairs, and a run id drops a result that has been overtaken.

The place where a check is started is `launch_check`: it is the one function that decides which engine runs it.

View state the panel borrows (ghosts, tool_transparent, view_only_ids, the feature highlight) is put back on close;
it never writes to the Document."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel, QMenu, QProgressBar, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from .. import assembly as A
from .. import interference as I
from .. import kernel as K
from .assembly_ui import _floating, _head, _hline
from .numparse import fmt
from .panels import ROLE_ID
from .viewport import Pick

# From scripts/spikes/op2_interference_bench.result.txt (I5.1):
#   DECISION interference min_depth=0.0001 min_volume=1e-3 engine=helper open_runs_at_most=90
# Nothing reads that file at run time.
OPEN_RUNS_AT_MOST = 90

RED = (0.92, 0.15, 0.12)
STRONG, FAINT, ALL = 0.9, 0.3, 0.55        # ghost alpha: the current row, the other rows, no row current
MARK = {"overlap": "●", "contained": "⊂", "thread": "≈", "failed": "⚠"}
KIND_WORD = {"overlap": "Overlap", "contained": "B inside A", "thread": "Thread fit (expected)",
             "failed": "Could not check"}
ROLE_CLASH = Qt.UserRole + 1
AMBER = (0.95, 0.60, 0.10)                  # an expected thread overlap, when drawn
GREY = QBrush(QColor("#9aa0a6"))
MIN_BOX = 1.0                               # mm; the zoom never frames less than this


def launch_check(executor, bodies, skip, expected, progress, cancelled, cache):
    """Start one check and return its Future. The only place a check is launched: swap the engine here."""
    return executor.submit(I.find_interferences, bodies, skip_pairs=skip, expected_pairs=expected,
                           progress=progress, cancelled=cancelled, cache=cache)


def surviving_pairs(bodies, skip):
    """How many pairs the check will run its boolean on (box reject applied), without running it."""
    boxes = [K.bbox(b.shape) for b in bodies]
    if len(boxes) < 2:
        return 0
    los = np.array([b[0] for b in boxes]).reshape(-1, 3)
    his = np.array([b[1] for b in boxes]).reshape(-1, 3)
    return sum(1 for i, j in I._boxes_overlap(los, his) if frozenset((bodies[i].id, bodies[j].id)) not in skip)


class AssemblyHost(QObject):
    """What the panel needs from an Assembly tab."""
    changed = Signal()
    has_standard = True                       # the panel shows "Include standard content" for this host

    def __init__(self, ctl):
        super().__init__()
        self.ctl = ctl
        self.doc = ctl.doc
        self._cb = lambda: self.changed.emit()
        self.doc.listeners.append(self._cb)

    def detach(self):
        if self._cb in self.doc.listeners:
            self.doc.listeners.remove(self._cb)

    @property
    def main(self):
        return self.ctl.w

    def parent_widget(self):
        return self.ctl.vp

    def visible_ids(self):
        return [i["id"] for i in self.doc.visible_instances() if not A.is_sketch_source(i["source"])]

    def bodies(self, ids):
        return self.doc.interference_bodies(ids)

    def pair_rules(self, include_standard):
        return self.doc.pair_rules(include_standard)

    def name(self, iid):
        inst = self.doc.instance(iid)
        return inst["name"] if inst else "?"

    def selected_ids(self):
        return self.ctl.selected_instance_ids()

    def select(self, ids):
        self.ctl.panel.select_instances(set(ids))
        self.ctl.vp.set_selection([Pick("part", (i,)) for i in ids])

    def exploded(self):
        return self.ctl.explode_panel is not None

    def redraw(self):
        self.ctl.show_scene()


class StudioHost(QObject):
    """What the panel needs from a Part Studio tab: parts that are shown, not Context and have a solid."""
    changed = Signal()
    has_standard = False                      # no Standard content in a Part Studio: the checkbox stays hidden
    min_parts = 2                             # fewer parts than this and the panel says so instead of "no overlap"
    clears_cache = True                       # a part keeps its id when a feature edit changes its shape

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.doc = win.doc
        self._cb = lambda: self.changed.emit()
        self.doc.listeners.append(self._cb)

    def detach(self):
        if self._cb in self.doc.listeners:
            self.doc.listeners.remove(self._cb)

    @property
    def main(self):
        return self.win

    def parent_widget(self):
        return self.win.viewport

    def visible_ids(self):
        return [b.id for b in I.studio_bodies(self.doc)]

    def bodies(self, ids):
        return [b for b in I.studio_bodies(self.doc) if ids is None or b.id in ids]

    def left_out(self):
        """How many parts studio_bodies leaves out (hidden, Context or no solid), for the footer."""
        return len(self.doc.state.parts) - len(I.studio_bodies(self.doc))

    def name(self, pid):
        p = self.doc.state.parts.get(pid)
        return p.name if p is not None else "?"

    def selected_ids(self):
        ids = list(self.win.left.parts.selected_part_ids())
        for pk in self.win.viewport.selection:
            if pk.kind in ("face", "edge", "part") and pk.key[0] not in ids:
                ids.append(pk.key[0])
        return [i for i in ids if i in self.doc.state.parts]

    def select(self, ids):
        lst = self.win.left.parts
        lst.blockSignals(True)
        lst.clearSelection()
        for r in range(lst.count()):
            it = lst.item(r)
            if it.data(ROLE_ID) in ids:
                it.setSelected(True)
        lst.blockSignals(False)
        self.win.viewport.set_selection([Pick("part", (i,)) for i in ids])

    def exploded(self):
        return False

    def redraw(self):
        if self.win.mode == "idle" and self.win.doc is self.doc:
            self.win._show_state(self.doc.state)


class _Item(QTreeWidgetItem):
    """A result row that sorts by volume, not by its text."""

    def __lt__(self, other):
        if isinstance(other, _Group):
            return self.treeWidget().header().sortIndicatorOrder() == Qt.AscendingOrder
        col = self.treeWidget().sortColumn() if self.treeWidget() else 0
        if col == 2:
            return self.clash.volume < other.clash.volume
        return self.text(col).lower() < other.text(col).lower()


class _Group(QTreeWidgetItem):
    """The collapsed "Expected (threads)" group: it always sorts below the counted rows."""

    def __lt__(self, other):
        tw = self.treeWidget()
        return bool(tw) and tw.header().sortIndicatorOrder() == Qt.DescendingOrder


class InterferencePanel(QFrame):
    closed = Signal()

    def __init__(self, host):
        super().__init__(host.parent_widget())
        _floating(self)
        self.host = host
        self.main = host.main
        self.vp = self.main.viewport
        self.animate = True
        self.executor = None
        self._future = None
        self._run_of_future = 0
        self._run = 0                     # a finished run is shown only if no later one was started
        self._cancel = None
        self._prog = (0, 0)
        self._running = False
        self._closed = False
        self._stale = False
        self._result = None               # the last ClashList shown
        self._scope = None                # None = every visible part, else a set of ids
        self._cache = {}
        self._left = 0                    # standard parts the last check left out
        self._left_out = 0                # Part Studio parts the last check left out (hidden or Context)
        self._nbodies = 0
        self._group = None                # the Expected (threads) group item, or None
        self._ignored = set()             # frozenset pairs ignored this session
        self._ghost_meshes = {}           # id(clash) -> Mesh
        self._given = None                # the list this panel put in vp.ghosts
        self._before = None               # the ghosts that were there before it, put back on close
        self._hl = None                   # the feature highlight this panel set
        self._tt = set()                  # ids this panel added to main.tool_transparent
        self._iso_prev = False
        self._saved_only = None
        self._current = None
        self._poll = QTimer(self)
        self._poll.setInterval(30)
        self._poll.timeout.connect(self._poll_run)
        self.setFixedWidth(340)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(6)
        head, t = _head(self, "Interference detection", "interference", None, self.close_panel,
                        cancel_tip="Close (Esc)")
        t.setReadOnly(True)
        lay.addLayout(head)
        lay.addWidget(_hline())

        lay.addWidget(QLabel("<b>Parts to check</b>"))
        row = QHBoxLayout()
        self.scope_lab = QLabel("")
        row.addWidget(self.scope_lab, 1)
        self.btn_sel = QPushButton("Selection")
        self.btn_sel.setToolTip("Check only the selected parts")
        self.btn_sel.clicked.connect(self.use_selection)
        row.addWidget(self.btn_sel)
        self.btn_all = QPushButton("All")
        self.btn_all.setToolTip("Check every visible part")
        self.btn_all.clicked.connect(lambda: self.set_scope(None))
        row.addWidget(self.btn_all)
        lay.addLayout(row)

        self.btn_check = QPushButton("Check")
        self.btn_check.clicked.connect(self._check_or_cancel)
        lay.addWidget(self.btn_check)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        lay.addWidget(self.bar)
        self.prog_lab = QLabel("")
        self.prog_lab.setObjectName("muted")
        lay.addWidget(self.prog_lab)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Part A", "Part B", "Volume"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSortingEnabled(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setMouseTracking(True)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.setMinimumHeight(170)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, self.tree.header().ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, self.tree.header().ResizeMode.Stretch)
        self.tree.currentItemChanged.connect(lambda cur, _prev: self._show_row(cur))
        self.tree.itemEntered.connect(self._hover)
        self.tree.itemDoubleClicked.connect(self._double)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.viewport().installEventFilter(self)
        lay.addWidget(self.tree)

        self.include_std = QCheckBox("Include standard content")
        self.include_std.setChecked(True)
        self.include_std.setToolTip("Check Standard parts too. A screw fastened into its hole is listed as an expected "
                                    "thread overlap, not counted. Off: no pair with a Standard part is checked.")
        self.include_std.toggled.connect(lambda _on: self.check())
        self.include_std.setVisible(bool(getattr(host, "has_standard", False)))
        lay.addWidget(self.include_std)
        self.iso = QCheckBox("Isolate the two parts")
        self.iso.setToolTip("Show only the parts of the current row (view only: nothing is hidden in the Assembly)")
        self.iso.toggled.connect(lambda _on: self._apply_view())
        lay.addWidget(self.iso)
        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        lay.addWidget(self.detail)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setObjectName("muted")
        lay.addWidget(self.status)

        host.changed.connect(self._doc_changed)
        self._refresh_scope()
        self._update_buttons()
        self.adjustSize()
        self.move(12, 12)
        self.show()
        self.raise_()
        self.setFocus()
        self._open_run()

    # ------------------------------------------------------------------ scope
    def _refresh_scope(self):
        n = len(self.host.visible_ids())
        if self._scope is None:
            self.scope_lab.setText(f"All visible parts ({n})")
        else:
            self.scope_lab.setText(f"{len(self._scope)} selected")
        self.btn_all.setEnabled(self._scope is not None)

    def set_scope(self, ids):
        """Check only `ids` (None: every visible part)."""
        self._scope = None if ids is None else set(ids)
        self._refresh_scope()

    def use_selection(self):
        ids = self.host.selected_ids()
        if len(ids) < 2:
            self.status.setText("Select two or more parts first.")
            return
        self.set_scope(ids)

    # ------------------------------------------------------------------ running
    @property
    def running(self):
        return self._running

    def _open_run(self):
        """Opening runs a check at once when it is small enough; a big one waits for the Check button."""
        if self.host.exploded():
            self.status.setText("Close the exploded view first: the check uses the real positions.")
            return
        bodies = self.host.bodies(self._scope)
        n = surviving_pairs(bodies, self._rules()[0])
        if n <= OPEN_RUNS_AT_MOST:
            self.check()
        else:
            self.status.setText(f"{n:,} pairs to check: press Check to start.")

    def _rules(self):
        """(skip_pairs, expected_pairs, standard parts left out) for the checkbox as it stands now."""
        if not getattr(self.host, "has_standard", False):
            return frozenset(), frozenset(), 0
        return self.host.pair_rules(self.include_std.isChecked())

    def set_include_standard(self, on):
        """Tick or untick "Include standard content"; a change re-runs the check."""
        self.include_std.setChecked(bool(on))

    def _check_or_cancel(self):
        if self._running:
            self.cancel()
        else:
            self.check()

    def check(self):
        """Snapshot the bodies and start a run; a run already going is dropped."""
        if self.host.exploded():
            self.status.setText("Close the exploded view first: the check uses the real positions.")
            self.main.status.showMessage("Interference detection needs the real positions: close the exploded view.",
                                         5000)
            return
        self._stop_run()
        bodies = self.host.bodies(self._scope)
        self._nbodies = len(bodies)
        if getattr(self.host, "clears_cache", False):
            self._cache = {}                  # a run still going keeps the old dict
        self._left_out = self.host.left_out() if hasattr(self.host, "left_out") else 0
        self._run += 1
        self._stale = False
        self._cancel = threading.Event()
        self._prog = (0, 0)
        self._running = True
        if self.executor is None:
            self.executor = ThreadPoolExecutor(1, thread_name_prefix="interference")
        ev = self._cancel
        skip, expected, self._left = self._rules()
        self._future = launch_check(self.executor, bodies, skip, expected,
                                    self._set_progress, ev.is_set, self._cache)
        self._run_of_future = self._run
        self.status.setText("Checking…")
        self._update_buttons()
        self._poll.start()

    def _set_progress(self, n, total):
        self._prog = (n, total)

    def cancel(self):
        if self._running and self._cancel is not None:
            self._cancel.set()
            self.status.setText("Cancelling after the pair in progress…")

    def _stop_run(self):
        self._run += 1
        if self._cancel is not None:
            self._cancel.set()
        self._poll.stop()
        self._future = None
        self._running = False

    def wait(self, timeout=60.0):
        """Pump events until the run in flight has been shown (for tests); True if it finished."""
        import time
        end = time.time() + timeout
        while self._running and time.time() < end:
            QApplication.processEvents()
            time.sleep(0.01)
        QApplication.processEvents()
        return not self._running

    def _poll_run(self):
        n, total = self._prog
        self.bar.setMaximum(max(total, 1))
        self.bar.setValue(n)
        self.prog_lab.setText(f"pair {n:,} of {total:,}" if total else "")
        f = self._future
        if f is None or not f.done():
            return
        self._poll.stop()
        self._future = None
        if self._run_of_future != self._run:
            return
        self._running = False
        try:
            res = f.result()
        except Exception as e:                                  # the kernel is meant to report, not raise
            self._update_buttons()
            self.status.setText(f"Could not check: {e}")
            return
        self.bar.setValue(self.bar.maximum())
        self.prog_lab.setText(f"pair {res.pairs_checked:,} of {res.pairs_total:,}")
        self._update_buttons()
        self._show_result(res)

    def _update_buttons(self):
        self.btn_check.setText("Cancel" if self._running else "Check")
        for w in (self.btn_sel, self.btn_all):
            w.setEnabled(not self._running and (w is self.btn_sel or self._scope is not None))

    # ------------------------------------------------------------------ result
    def _show_result(self, res):
        self._result = res
        self._ghost_meshes = {}
        self._set_current(None)
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        self._group = None
        for c in res:
            if frozenset((c.a, c.b)) in self._ignored:
                continue
            it = _Item([f"{MARK[c.kind]} {self.host.name(c.a)}", self.host.name(c.b),
                        fmt(c.volume, 2) if c.kind != "failed" else "–"])
            it.clash = c
            it.setData(0, ROLE_CLASH, c.kind)
            it.setTextAlignment(2, Qt.AlignRight | Qt.AlignVCenter)
            it.setToolTip(0, KIND_WORD[c.kind] + (f": {c.note}" if c.note else ""))
            if c.kind == "thread":
                if self._group is None:
                    self._group = _Group(["Expected (threads)"])
                    self._group.setFirstColumnSpanned(True)
                    self.tree.addTopLevelItem(self._group)
                self._group.addChild(it)
            else:
                self.tree.addTopLevelItem(it)
        self.tree.setSortingEnabled(True)
        self.tree.sortItems(2, Qt.DescendingOrder)
        if self._group is not None:
            self._group.setText(0, f"Expected (threads) ({self._group.childCount()})")
            self._group.setToolTip(0, "A screw is modelled at its major diameter and a tapped hole at the tap drill, "
                                      "so they overlap by design. Not counted.")
            self._group.setExpanded(False)
        for it in self._items():
            self._grey(it, self._stale)
        self.detail.setText("")
        self._footer()
        self._give(self._all_ghosts(None))

    def _footer(self):
        res = self._result
        if res is None:
            return
        shown = [c for c in res if frozenset((c.a, c.b)) not in self._ignored]
        n_thread = sum(1 for c in shown if c.kind == "thread")
        n_ok = sum(1 for c in shown if c.kind not in ("failed", "thread"))
        n_fail = len(shown) - n_ok - n_thread
        parts = []
        if res.incomplete:
            parts.append(f"Incomplete: stopped after {res.pairs_checked:,} of {res.pairs_total:,} pairs.")
        if n_ok:
            parts.append(f"{n_ok} interference{'s' if n_ok != 1 else ''}.")
        elif not n_fail and not res.incomplete:
            parts.append("No interference." if n_thread else "No interference: no parts overlap.")
        if n_thread:
            parts.append(f"{n_thread} expected thread overlap{'s' if n_thread != 1 else ''} not counted.")
        if n_fail:
            parts.append(f"{n_fail} pair{'s' if n_fail != 1 else ''} could not be checked.")
        if self._nbodies < getattr(self.host, "min_parts", 0):
            parts = ["Interference needs two or more parts."]
        if self._left_out:
            parts.append(f"{self._left_out} part{'s' if self._left_out != 1 else ''} not checked: hidden or reference.")
        if self._left:
            parts.append(f"{self._left} standard part{'s' if self._left != 1 else ''} not checked.")
        if self._ignored:
            parts.append(f"{len(self._ignored)} ignored this session.")
        if self._stale:
            parts.append("Out of date. Check again.")
        self.status.setText(" ".join(parts))

    def _items(self):
        """Every result row item: the counted rows top to bottom, then those in the Expected (threads) group."""
        out = []
        for r in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(r)
            if isinstance(it, _Group):
                out.extend(it.child(k) for k in range(it.childCount()))
            else:
                out.append(it)
        return sorted(out, key=lambda i: i.clash.kind == "thread")

    def rows(self):
        """The rows as shown, top to bottom: dicts with a, b, a_name, b_name, volume, kind, note, lo, hi."""
        out = []
        for it in self._items():
            c = it.clash
            out.append({"a": c.a, "b": c.b, "a_name": self.host.name(c.a), "b_name": self.host.name(c.b),
                        "volume": c.volume, "kind": c.kind, "note": c.note, "lo": c.lo, "hi": c.hi})
        return out

    @property
    def stale(self):
        return self._stale

    @property
    def incomplete(self):
        return bool(self._result is not None and self._result.incomplete)

    def _doc_changed(self):
        """The Document changed: grey the list and say so; the rows stay."""
        if self._closed or self._stale:
            return
        self._stale = True
        for it in self._items():
            self._grey(it, True)
        if self._result is not None:
            self._footer()
        elif not self._running:
            self.status.setText("Out of date. Check again.")

    def _grey(self, it, on):
        for col in range(3):
            it.setForeground(col, GREY if on else QBrush())

    # ------------------------------------------------------------------ drawing
    def _mesh(self, c):
        m = self._ghost_meshes.get(id(c))
        if m is None and c.shape is not None:
            try:
                m = K.tessellate(c.shape)
            except Exception:
                m = None
            self._ghost_meshes[id(c)] = m
        return m

    def _all_ghosts(self, current):
        out = []
        for c in self._result or []:
            if c.kind == "failed" or frozenset((c.a, c.b)) in self._ignored:
                continue
            m = self._mesh(c)
            if m is not None:
                alpha = ALL if current is None else (STRONG if c is current else FAINT)
                out.append((m, (*(AMBER if c.kind == "thread" else RED), alpha)))
        return out

    def _give(self, ghosts):
        """Hand the viewport `ghosts`. Whatever was there first (another tool's) is put back on release, but only
        while the viewport still shows this panel's list."""
        vp = self.vp
        if ghosts:
            if self._given is None or vp.ghosts is not self._given:
                self._before = vp.ghosts
            self._given = ghosts
            vp.ghosts = ghosts
        elif self._given is not None:
            if vp.ghosts is self._given:
                vp.ghosts = self._before if self._before is not None else []
            self._given = None
            self._before = None
        vp.update()

    @staticmethod
    def _box_polys(lo, hi):
        lo, hi = np.asarray(lo, float), np.asarray(hi, float)
        c = [np.array([x, y, z]) for z in (lo[2], hi[2]) for x, y in
             ((lo[0], lo[1]), (hi[0], lo[1]), (hi[0], hi[1]), (lo[0], hi[1]))]
        loop = [c[0], c[1], c[2], c[3], c[0], c[4], c[5], c[6], c[7], c[4]]
        return [np.array(loop), np.array([c[1], c[5]]), np.array([c[2], c[6]]), np.array([c[3], c[7]])]

    def _set_hl(self, hl):
        self._hl = hl
        self.vp.feature_hl = hl
        self.vp.update()

    def _release_hl(self):
        if self._hl is not None and self.vp.feature_hl is self._hl:
            self.vp.feature_hl = None
        self._hl = None

    def _hover(self, item, _col=0):
        c = item.clash if item is not None else None
        polys = self._box_polys(self._current.clash.lo, self._current.clash.hi) if self._current is not None else []
        if c is None:
            self._set_hl({"polys": polys} if polys else None)
            return
        meshes = {pid: m for pid, m, *_ in self.vp.parts}
        faces = {pid: set(range(len(meshes[pid].face_ranges))) for pid in (c.a, c.b) if pid in meshes}
        self._set_hl({"faces": faces, "polys": polys})

    def eventFilter(self, obj, ev):
        if obj is self.tree.viewport() and ev.type() == ev.Type.Leave:
            self._hover(None)
        return super().eventFilter(obj, ev)

    # ------------------------------------------------------------------ a row
    def _set_current(self, item):
        self._current = item

    def activate(self, row):
        """Make row `row` (0-based, as rows() lists it) the current one, as a click does."""
        items = self._items()
        if not 0 <= row < len(items):
            return
        it = items[row]
        if it.parent() is not None:
            it.parent().setExpanded(True)
        self.tree.blockSignals(True)
        self.tree.setCurrentItem(it)
        self.tree.blockSignals(False)
        self._show_row(it)

    def _show_row(self, item):
        if item is None or not hasattr(item, "clash"):
            return
        c = item.clash
        self._set_current(item)
        self.host.select([c.a, c.b])
        lo, hi = np.asarray(c.lo, float), np.asarray(c.hi, float)
        ext = np.maximum(hi - lo, MIN_BOX)
        mid = (lo + hi) / 2
        self.vp.fit_bounds(mid - ext, mid + ext, animate=self.animate)     # the box padded by half its size each side
        self._give(self._all_ghosts(c))
        self._set_hl({"polys": self._box_polys(lo, hi)})
        d = np.abs(hi - lo)
        a, b = self.host.name(c.a), self.host.name(c.b)
        word = {"overlap": f"{a} and {b} overlap", "contained": f"{b} is inside {a}",
                "thread": "Thread fit (expected)", "failed": f"Could not check: {c.note}"}[c.kind]
        self.detail.setText(f"{fmt(d[0], 2)} x {fmt(d[1], 2)} x {fmt(d[2], 2)} mm. {word}.")
        self._apply_view()

    def _double(self, item, _col=0):
        self.iso.setChecked(True)

    def _apply_view(self):
        """Transparency of the current pair (always) and Isolate (when ticked); both are view state only."""
        c = self._current.clash if self._current is not None else None
        main = self.main
        main.tool_transparent -= self._tt
        self._tt = {i for i in (c.a, c.b) if i not in main.tool_transparent} if c is not None else set()
        main.tool_transparent |= self._tt
        want = {c.a, c.b} if (c is not None and self.iso.isChecked()) else None
        if want is not None:
            if not self._iso_prev:
                self._saved_only = main.view_only_ids
                self._iso_prev = True
            main.view_only_ids = want
        elif self._iso_prev:
            main.view_only_ids = self._saved_only
            self._iso_prev = False
            self._saved_only = None
        self.vp.set_transparent(set(main._transparent.get(main.doc, ())) | set(main.tool_transparent))
        self.host.redraw()

    # ------------------------------------------------------------------ menu
    def _menu(self, pos):
        it = self.tree.itemAt(pos)
        m = QMenu(self)
        if it is not None and hasattr(it, "clash"):
            m.addAction("Ignore for this session", lambda: self.ignore(it))
            m.addAction("Copy row", lambda: self.copy_row(it))
        if self._ignored:
            m.addAction(f"Stop ignoring {len(self._ignored)} pair(s)", self._unignore)
        if not m.isEmpty():
            m.exec(self.tree.viewport().mapToGlobal(pos))

    def ignore(self, item):
        c = item.clash
        self._ignored.add(frozenset((c.a, c.b)))
        if self._current is item:
            self._set_current(None)
            self._set_hl(None)
            self._apply_view()
        par = item.parent()
        if par is not None:
            par.removeChild(item)
            if par.childCount() == 0:
                self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(par))
                self._group = None
            else:
                par.setText(0, f"Expected (threads) ({par.childCount()})")
        else:
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(item))
        self._give(self._all_ghosts(self._current.clash if self._current is not None else None))
        self._footer()

    def _unignore(self):
        self._ignored.clear()
        if self._result is not None:
            self._show_result(self._result)

    def copy_row(self, item):
        c = item.clash
        text = "\t".join([self.host.name(c.a), self.host.name(c.b),
                          fmt(c.volume, 2) if c.kind != "failed" else "", KIND_WORD[c.kind]])
        QApplication.clipboard().setText(text)

    # ------------------------------------------------------------------ closing
    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close_panel()
        else:
            super().keyPressEvent(e)

    def close_panel(self):
        self.closed.emit()

    def shutdown(self):
        """Put everything the panel borrowed back and stop its run. Safe to call twice."""
        if self._closed:
            return
        self._closed = True
        self._stop_run()
        if self.executor is not None:
            self.executor.shutdown(wait=False, cancel_futures=True)
            self.executor = None
        self.host.detach()
        self._release_hl()
        self._give([])
        self.main.tool_transparent -= self._tt
        self._tt = set()
        if self._iso_prev:
            self.main.view_only_ids = self._saved_only
            self._iso_prev = False
        self.vp.set_transparent(set(self.main._transparent.get(self.main.doc, ())) | set(self.main.tool_transparent))
        self.host.redraw()
