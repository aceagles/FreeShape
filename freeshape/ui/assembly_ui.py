"""Assembly tab interface: instance & mate lists, the Insert panel, the Mate dialog, dragging parts
through their mates, precise moves, interference check and the bill of materials."""
from __future__ import annotations

import copy
import json
import math
import os

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, Signal, QSize
from PySide6.QtGui import QAction, QColor, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QToolButton, QLineEdit, QListWidget, QListWidgetItem, QWidget,
    QGraphicsDropShadowEffect, QTreeWidget, QTreeWidgetItem, QPushButton, QDialog, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QComboBox, QFileDialog, QMessageBox, QCheckBox, QTabWidget,
    QMenu,
)

from .. import kernel as K
from .. import assembly as A
from . import filedlg
from .icons import icon
from . import preview_rows as PR
from .numparse import parse_number, fmt
from .panels import swatch
from .pyfeature_ui import NOTICE_COLOR
from .viewport import Pick

ROLE_ID = Qt.UserRole + 1
ROLE_KIND = Qt.UserRole
SKETCH_ROLE_WORD = {"point": "point", "centre": "centre", "end1": "end 1", "end2": "end 2", "mid": "middle"}
SKETCH_SNAP_KIND = {"end1": "vertex", "end2": "vertex", "point": "vertex", "mid": "mid", "centre": "center"}
ROLE_NOTICE = Qt.UserRole + 2        # a Standard part instance's version notice text (marker in the list)
NOTICE_MARK = "   ↻"


def _floating(frame: QFrame):
    frame.setObjectName("featureDialog")
    sh = QGraphicsDropShadowEffect(frame)
    sh.setBlurRadius(18)
    sh.setOffset(0, 3)
    sh.setColor(QColor(0, 0, 0, 60))
    frame.setGraphicsEffect(sh)


def _head(frame, title, ic, on_ok, on_cancel, ok_tip="Accept (Enter)", cancel_tip="Cancel (Esc)"):
    head = QHBoxLayout()
    lab = QLabel()
    lab.setPixmap(icon(ic).pixmap(20, 20))
    head.addWidget(lab)
    t = QLineEdit(title)
    t.setStyleSheet("QLineEdit { border: none; font-weight: 700; background: transparent; }"
                    "QLineEdit:focus { border: 1px solid #1f6fd1; background: white; }")
    head.addWidget(t, 1)
    if on_ok is not None:
        ok = QToolButton()
        ok.setObjectName("okButton")
        ok.setText("✓")
        ok.setToolTip(ok_tip)
        ok.clicked.connect(on_ok)
        head.addWidget(ok)
    cancel = QToolButton()
    cancel.setObjectName("cancelButton")
    cancel.setText("✕")
    cancel.setToolTip(cancel_tip)
    cancel.clicked.connect(on_cancel)
    head.addWidget(cancel)
    return head, t


def _hline():
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setStyleSheet("color:#e3e6ea;")
    return line


# =========================================================================== left panel

# What each Mate value is called in the UI, and its unit (rotations in degrees, translations in mm).
VALUE_LABELS = {"rz": "Rotation", "tz": "Slide", "tx": "Slide X", "ty": "Slide Y"}


def value_unit(dof):
    return "°" if dof.startswith("r") else "mm"


def value_rows(mate):
    """(freedom, label, text, unit) for each Mate value of `mate`; empty for fastened and ball."""
    vals = mate.get("values") or {}
    return [(d, VALUE_LABELS.get(d, d), fmt(float(vals.get(d, 0.0))), value_unit(d))
            for d in A.MATE_VALUE_DOFS.get(mate["type"], ())]


def limit_rows(mate):
    """(freedom, label, unit, lo, hi) for each limitable freedom of `mate`; lo/hi are None for no bound."""
    lim = mate.get("limits") or {}
    return [(d, VALUE_LABELS.get(d, d), value_unit(d), *(lim.get(d) or (None, None)))
            for d in A.MATE_VALUE_DOFS.get(mate["type"], ())]


def animate_defaults(mate, dof, length_scale):
    """(start, end) for animating `dof` of `mate`: its limits, per end, else 0 and 360° or 0 and `length_scale`."""
    lo, hi = (mate.get("limits") or {}).get(dof) or (None, None)
    full = 360.0 if dof.startswith("r") else float(length_scale)
    return (0.0 if lo is None else float(lo)), (full if hi is None else float(hi))


ANIMATE_PLAYBACK_LABELS = {"single": "Single", "reciprocate": "Reciprocate", "loop": "Loop"}
ANIMATE_TICK_MS = 40       # one frame per tick: the default 60 steps play in 2.4 s


class AnimateDialog(QFrame):
    """Play one Mate value of a mate from a start to an end value (Onshape's Animate). The model side is
    AssemblyDoc.animate_frame / end_animate; this dialog only chooses the values and paces the frames."""
    finished = Signal()

    def __init__(self, ctl, mid, dof=None):
        super().__init__(ctl.w.viewport)
        _floating(self)
        self.ctl, self.doc, self.mid = ctl, ctl.doc, mid
        mate = self.doc.mate(mid)
        self.rows = value_rows(mate)
        self.dofs = [r[0] for r in self.rows]
        self._seq, self._idx = [], 0
        self._reverse = False
        self._open = False          # an animation is open in the doc (end_animate still owed)
        self._framing = False       # inside animate_frame: ignore the notification it sends
        self._closed = False
        self._timer = QTimer(self)
        self._timer.setInterval(ANIMATE_TICK_MS)
        self._timer.timeout.connect(self._tick)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(6)
        head, self.title = _head(self, "Animate " + mate["name"], "m_" + mate["type"], None, self.reject,
                                 cancel_tip="Close (Esc)")
        self.title.setReadOnly(True)
        lay.addLayout(head)
        lay.addWidget(_hline())

        def row(label, widget, unit=None):
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            lab = QLabel(label)
            lab.setFixedWidth(80)
            h.addWidget(lab)
            h.addWidget(widget, 1)
            unit_lab = QLabel(unit or "")
            unit_lab.setFixedWidth(24)
            h.addWidget(unit_lab)
            lay.addWidget(w)
            return w, unit_lab

        self.dof_box = QComboBox()
        for d, label, _t, unit in self.rows:
            self.dof_box.addItem(f"{label} ({unit})", d)
        self.dof_row, _ = row("Freedom", self.dof_box)
        self.dof_row.setVisible(len(self.rows) > 1)
        self.start_edit, self.end_edit = QLineEdit(), QLineEdit()
        _, self.unit_start = row("Start", self.start_edit)
        _, self.unit_end = row("End", self.end_edit)
        self.steps_edit = QLineEdit("60")
        row("Steps", self.steps_edit)
        self.playback = QComboBox()
        for k, label in ANIMATE_PLAYBACK_LABELS.items():
            self.playback.addItem(label, k)
        row("Playback", self.playback)
        self.current = QLineEdit()
        self.current.setReadOnly(True)
        _, self.unit_cur = row("Current", self.current)
        btns = QHBoxLayout()
        self.play_btn = QPushButton("Play")
        self.play_btn.clicked.connect(self.toggle_play)
        self.reverse_btn = QPushButton("Reverse")
        self.reverse_btn.setToolTip("Play the other way round")
        self.reverse_btn.clicked.connect(self.reverse)
        btns.addWidget(self.play_btn)
        btns.addWidget(self.reverse_btn)
        lay.addLayout(btns)
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        self.msg.setObjectName("muted")
        lay.addWidget(self.msg)
        self.dof_box.currentIndexChanged.connect(lambda _i: self._freedom_changed())
        if dof in self.dofs:
            self.dof_box.setCurrentIndex(self.dofs.index(dof))
        self._freedom_changed()
        self.doc.listeners.append(self._doc_notified)
        self.setFixedWidth(300)
        self.adjustSize()
        self.move(12, 12)
        self.show()

    # -- fields
    @property
    def dof(self):
        return self.dof_box.currentData()

    def _freedom_changed(self):
        mate = self.doc.mate(self.mid)
        unit = value_unit(self.dof)
        for lab in (self.unit_start, self.unit_end, self.unit_cur):
            lab.setText(unit)
        if mate is not None:
            lo, hi = animate_defaults(mate, self.dof, self.doc.length_scale())
            self.start_edit.setText(fmt(lo))
            self.end_edit.setText(fmt(hi))
        self._show_current()

    def _show_current(self):
        mate = self.doc.mate(self.mid)
        if mate is not None:
            self.current.setText(fmt(float((mate.get("values") or {}).get(self.dof, 0.0))))

    def _read(self):
        """(start, end, steps, playback) from the fields; raises ValueError saying which one is wrong."""
        ang = self.dof.startswith("r")
        vals = []
        for name, ed in (("Start", self.start_edit), ("End", self.end_edit)):
            v = parse_number(ed.text(), angle=ang)
            if v is None:
                raise ValueError(f"{name}: can't read \"{ed.text()}\" as a number")
            vals.append(float(v))
        try:
            steps = int(self.steps_edit.text().strip())
        except ValueError:
            raise ValueError(f"Steps: can't read \"{self.steps_edit.text()}\" as a whole number") from None
        return vals[0], vals[1], steps, self.playback.currentData()

    # -- playback
    @property
    def running(self):
        return self._timer.isActive()

    def toggle_play(self):
        if self.running:
            self.stop()
        else:
            self.play()

    def play(self):
        if self.running or self._closed:
            return
        try:
            start, end, steps, mode = self._read()
            seq = A.animate_values(start, end, steps, mode)
        except ValueError as e:
            self.msg.setText(str(e))
            return
        self.msg.setText("")
        self._seq = seq[::-1] if self._reverse else seq
        self._idx = 0
        self._set_running(True)
        self._timer.start()

    def stop(self):
        """Stop the run and put the assembly back as it was."""
        self._set_running(False)
        self._end()
        self._show_current()

    def reverse(self):
        """Play the other way round: from the next Play, or from here when running."""
        self._reverse = not self._reverse
        self.reverse_btn.setDown(False)
        if self.running and self._idx:
            self._seq = self._seq[::-1]
            self._idx = min(len(self._seq), len(self._seq) - self._idx + 1)

    def _set_running(self, on):
        if not on:
            self._timer.stop()
        self.play_btn.setText("Stop" if on else "Play")
        for w in (self.dof_box, self.start_edit, self.end_edit, self.steps_edit, self.playback):
            w.setEnabled(not on)

    def _tick(self):
        """One frame. A Single run ends, and the assembly goes back, after its last frame."""
        if not self._seq or self._closed:
            return
        if self._idx >= len(self._seq):
            self._idx = 0                       # loop (Single never gets here: it ends on the last frame)
        value = self._seq[self._idx]
        self._idx += 1
        self._open = True
        self._framing = True
        try:
            reached = self.doc.animate_frame(self.mid, self.dof, value)
        except ValueError as e:                 # the mate was deleted under us
            self._framing = False
            self.msg.setText(str(e))
            self.stop()
            return
        finally:
            self._framing = False
        self.current.setText(fmt(reached))
        if self._idx >= len(self._seq) and self.playback.currentData() == "single":
            self._set_running(False)            # keep the end value showing; the assembly goes back
            self._end()

    def _end(self):
        if self._open:
            self._open = False
            self._framing = True
            try:
                self.doc.end_animate()
            finally:
                self._framing = False

    def _doc_notified(self):
        """Every animate_frame notifies; ours are ignored. Anything else that deletes the mate ends the run."""
        if self._framing or self._closed:
            return
        if self.doc.mate(self.mid) is None:
            self.reject()

    # -- closing
    def _close(self):
        if self._closed:
            return
        self._closed = True
        self._set_running(False)
        self._end()
        if self._doc_notified in self.doc.listeners:
            self.doc.listeners.remove(self._doc_notified)
        self.finished.emit()

    def accept(self):
        self._close()

    def reject(self):
        self._close()

    def closeEvent(self, e):
        self._close()
        super().closeEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(e)


class AssemblyPanel(QWidget):
    instanceClicked = Signal(str)
    instanceDouble = Signal(str)
    instanceContext = Signal(str, object)
    mateClicked = Signal(str)
    mateDouble = Signal(str)
    mateContext = Signal(str, object)
    groupClicked = Signal(str)
    groupContext = Signal(str, object)
    relationClicked = Signal(str)
    relationDouble = Signal(str)
    relationContext = Signal(str, object)
    visibilityChanged = Signal()

    # What the visibility dropdown switches: (key, label, shown at first, tooltip)
    VISIBILITY = (
        ("connectors", "Mate connectors", True, "The mate connectors each part brings from its Part Studio, and the "
                                                "ones added at a part's origin"),
        ("mates", "Mates", False, "The two connectors of every mate, not only of the mates you select"),
        ("sketches", "Sketches", True, "Sketches inserted into this Assembly"),
    )

    def __init__(self):
        super().__init__()
        self.setObjectName("leftpanel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 0, 6, 0)
        hl.setSpacing(0)
        self.inst_header = QLabel("Instances")
        self.inst_header.setObjectName("sectionHeader")
        hl.addWidget(self.inst_header, 1)
        # like Onshape's eye on the instance list: what the Assembly draws besides its parts
        self.vis_btn = QToolButton()
        self.vis_btn.setObjectName("asm_visibility")
        self.vis_btn.setText("Show")
        self.vis_btn.setToolTip("Show or hide mate connectors, mates and sketches")
        self.vis_btn.setPopupMode(QToolButton.InstantPopup)
        vm = QMenu(self.vis_btn)
        vm.setToolTipsVisible(True)
        self.vis_actions = {}
        for key, label, on, tip in self.VISIBILITY:
            a = QAction(label, self)
            a.setCheckable(True)
            a.setChecked(on)
            a.setToolTip(tip)
            a.toggled.connect(lambda _on=False: self.visibilityChanged.emit())
            vm.addAction(a)
            self.vis_actions[key] = a
        self.vis_btn.setMenu(vm)
        hl.addWidget(self.vis_btn)
        lay.addWidget(head)
        self.instances = QListWidget()
        self.instances.setIconSize(QSize(14, 14))
        self.instances.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.instances.setContextMenuPolicy(Qt.CustomContextMenu)
        self.instances.customContextMenuRequested.connect(
            lambda pos: self._ctx(self.instances, pos, self.instanceContext))
        self.instances.itemClicked.connect(lambda it: self.instanceClicked.emit(it.data(ROLE_ID)))
        self.instances.itemDoubleClicked.connect(lambda it: self.instanceDouble.emit(it.data(ROLE_ID)))
        lay.addWidget(self.instances, 3)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background:#d5d9de;")
        lay.addWidget(sep)
        self.group_header = QLabel("Groups")
        self.group_header.setObjectName("sectionHeader")
        lay.addWidget(self.group_header)
        self.groups = QListWidget()
        self.groups.setIconSize(QSize(18, 18))
        self.groups.setContextMenuPolicy(Qt.CustomContextMenu)
        self.groups.customContextMenuRequested.connect(lambda pos: self._ctx(self.groups, pos, self.groupContext))
        self.groups.itemClicked.connect(lambda it: self.groupClicked.emit(it.data(ROLE_ID)))
        lay.addWidget(self.groups, 1)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background:#d5d9de;")
        lay.addWidget(sep)
        self.mate_header = QLabel("Mates")
        self.mate_header.setObjectName("sectionHeader")
        lay.addWidget(self.mate_header)
        self.mates = QListWidget()
        self.mates.setIconSize(QSize(18, 18))
        self.mates.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.mates.setContextMenuPolicy(Qt.CustomContextMenu)
        self.mates.customContextMenuRequested.connect(lambda pos: self._ctx(self.mates, pos, self.mateContext))
        self.mates.itemClicked.connect(lambda it: self.mateClicked.emit(it.data(ROLE_ID)))
        self.mates.itemDoubleClicked.connect(lambda it: self.mateDouble.emit(it.data(ROLE_ID)))
        lay.addWidget(self.mates, 2)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background:#d5d9de;")
        lay.addWidget(sep)
        self.relation_header = QLabel("Relations")
        self.relation_header.setObjectName("sectionHeader")
        lay.addWidget(self.relation_header)
        self.relations = QListWidget()
        self.relations.setIconSize(QSize(18, 18))
        self.relations.setContextMenuPolicy(Qt.CustomContextMenu)
        self.relations.customContextMenuRequested.connect(
            lambda pos: self._ctx(self.relations, pos, self.relationContext))
        self.relations.itemClicked.connect(lambda it: self.relationClicked.emit(it.data(ROLE_ID)))
        self.relations.itemDoubleClicked.connect(lambda it: self.relationDouble.emit(it.data(ROLE_ID)))
        lay.addWidget(self.relations, 1)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        self.status.setContentsMargins(10, 6, 10, 8)
        lay.addWidget(self.status)
        self.setMinimumWidth(220)

    def _ctx(self, lst, pos, sig):
        it = lst.itemAt(pos)
        if it is not None:
            sig.emit(it.data(ROLE_ID), lst.viewport().mapToGlobal(pos))

    def populate(self, doc, editing=None):
        for lst in (self.instances, self.mates, self.groups, self.relations):
            lst.blockSignals(True)
        sel_g = set(self.selected_groups())
        sel_i = set(self.selected_instances())
        sel_m = set(self.selected_mates())
        self.instances.clear()
        notices = doc.standard_notices()
        for inst in doc.instances:
            label = inst["name"] + ("   ⚓" if inst.get("fixed") else "") + (NOTICE_MARK if inst["id"] in notices else "")
            it = QListWidgetItem(swatch(doc.instance_color(inst)), label)
            it.setData(ROLE_ID, inst["id"])
            tips = []
            if inst.get("fixed"):
                tips.append("Fixed — it won't move (right-click → Unfix to let it move)")
            err = doc.errors.get(inst["id"])
            if inst.get("suppressed"):
                it.setForeground(QColor("#9aa3ae"))
                f = QFont()
                f.setStrikeOut(True)
                it.setFont(f)
                tips.append("Suppressed — left out of the model (right-click → Unsuppress)")
            elif err:
                it.setForeground(QColor("#d93025") if inst["id"] not in doc.parts else QColor("#b26a00"))
                tips.append(err)
            elif inst.get("hidden"):
                it.setForeground(QColor("#9aa3ae"))
                tips.append("Hidden")
            src = inst["source"]
            if src.get("kind") == "standard":
                tips.append("Standard part " + str(src.get("type")))
            else:
                tips.append("From " + (src.get("filename") or "STEP") if src.get("kind") == "step" else
                            "From Part Studio “%s”" % (src.get("studio_name") or "?"))
            note = notices.get(inst["id"])
            if note:
                it.setData(ROLE_NOTICE, note["text"])
                if not err:
                    it.setForeground(QColor(NOTICE_COLOR))
                tips.insert(0, note["text"] + " (right-click → Accept plugin update)")
            it.setToolTip("\n".join(tips))
            g = doc.group_of(inst["id"])
            if g is not None:
                tips.append("In " + g["name"])
                it.setToolTip("\n".join(tips))
            self.instances.addItem(it)
            if inst["id"] in sel_i:
                it.setSelected(True)
        self.groups.clear()
        for g in doc.groups:
            names = [doc.instance(i)["name"] for i in g["instances"] if doc.instance(i) is not None]
            it = QListWidgetItem(icon("group"), f"{g['name']}   ({', '.join(names)})")
            it.setData(ROLE_ID, g["id"])
            it.setToolTip(f"{g['name']}: " + ", ".join(names) + "\nMembers move together as one body")
            self.groups.addItem(it)
            if g["id"] in sel_g:
                it.setSelected(True)
        self.group_header.setText(f"Groups ({len(doc.groups)})")
        self.mates.clear()
        for m in doc.mates:
            limited = any(lo is not None or hi is not None for _d, _l, _u, lo, hi in limit_rows(m))
            it = QListWidgetItem(icon("m_" + m["type"]), m["name"] + (" ⇥" if limited else ""))
            it.setData(ROLE_ID, m["id"])
            names = []
            for side in ("a", "b"):
                c = m.get(side)
                inst = doc.instance(c["inst"]) if c else None
                nm = inst["name"] if inst else "?"
                if c and c.get("kind") == "sketch":      # sketch geometry: "<sketch name> <role word>"
                    nm += " " + SKETCH_ROLE_WORD.get(c.get("role"), str(c.get("role")))
                names.append(nm)
            tip = f"{A.MATE_TYPES[m['type']][0]}: {names[0]}  ↔  {names[1]}"
            err = doc.errors.get(m["id"])
            if m.get("suppressed"):
                it.setForeground(QColor("#9aa3ae"))
                f = QFont()
                f.setStrikeOut(True)
                it.setFont(f)
                tip += "\nSuppressed"
            elif doc.mate_inactive(m):
                it.setForeground(QColor("#9aa3ae"))
                f = QFont()
                f.setStrikeOut(True)
                it.setFont(f)
                tip += "\nInactive: an instance it connects is suppressed"
            elif err:
                it.setForeground(QColor("#d93025"))
                it.setIcon(icon("error"))
                tip += "\n" + err
            for _d, label, text, unit in value_rows(m):
                tip += f"\n{label}: {text} {unit}"
            for _d, label, unit, lo, hi in limit_rows(m):
                if lo is not None or hi is not None:
                    tip += (f"\n{label} limits: {'no min' if lo is None else fmt(lo)} to "
                            f"{'no max' if hi is None else fmt(hi)} {unit}")
            if m["id"] == editing:
                it.setBackground(QColor("#ffe9b8"))
            it.setToolTip(tip)
            self.mates.addItem(it)
            if m["id"] in sel_m:
                it.setSelected(True)
        sel_r = set(self.selected_relations())
        self.relations.clear()
        for r in doc.relations:
            it = QListWidgetItem(icon("r_" + r["type"]), r["name"])
            it.setData(ROLE_ID, r["id"])
            names = []
            for side in ("a", "b"):
                m = doc.mate(r[side]["mate"]) if r.get(side) else None
                names.append(m["name"] if m else "?")
            tip = f"{A.RELATION_TYPES[r['type']]['label']}: {names[0]}  ↔  {names[1]}"
            err = doc.errors.get(r["id"])
            if r.get("suppressed"):
                it.setForeground(QColor("#9aa3ae"))
                f = QFont()
                f.setStrikeOut(True)
                it.setFont(f)
                tip += "\nSuppressed"
            elif err:
                it.setForeground(QColor("#d93025"))
                it.setIcon(icon("error"))
                tip += "\n" + err
            it.setToolTip(tip)
            self.relations.addItem(it)
            if r["id"] in sel_r:
                it.setSelected(True)
        self.relation_header.setText(f"Relations ({len(doc.relations)})")
        for pt in doc.patterns:                      # instance patterns are listed with the mates
            n = sum(1 for row in pt["copies"] for c in row if c)
            it = QListWidgetItem(icon("linear_pattern" if pt["type"] == "linear" else "circular_pattern"),
                                 f"{pt['name']} (×{pt['count']})")
            it.setData(ROLE_ID, pt["id"])
            err = doc.errors.get(pt["id"])
            it.setToolTip((f"⚠ {err}\n" if err else "") + f"{n} copies of "
                          + ", ".join((doc.instance(sd) or {}).get("name", "?") for sd in pt["seeds"])
                          + "\nDouble-click to change the count or spacing")
            self.mates.addItem(it)
            if pt["id"] in sel_m:
                it.setSelected(True)
        self.inst_header.setText(f"Instances ({len(doc.instances)})")
        self.mate_header.setText(f"Mates ({len(doc.mates)})")
        if not doc.instances:
            self.status.setText("Insert parts from your Part Studios (or a STEP file) with the Insert button.")
        else:
            free = sum(1 for i in doc.instances if not i.get("fixed"))
            bad = sum(1 for m in doc.mates if m["id"] in doc.errors and not m.get("suppressed"))
            txt = f"{free} free instance(s) · {doc.dof} degree(s) of freedom left"
            if bad:
                txt += f" · {bad} mate(s) failing"
            self.status.setText(txt + "\nDrag parts to move them through their mates.")
        for lst in (self.instances, self.mates, self.groups, self.relations):
            lst.blockSignals(False)

    def selected_relations(self):
        return [it.data(ROLE_ID) for it in self.relations.selectedItems()]

    def select_mates(self, ids):
        self.mates.blockSignals(True)
        for i in range(self.mates.count()):
            it = self.mates.item(i)
            it.setSelected(it.data(ROLE_ID) in ids)
        self.mates.blockSignals(False)

    def selected_groups(self):
        return [it.data(ROLE_ID) for it in self.groups.selectedItems()]

    def selected_instances(self):
        return [it.data(ROLE_ID) for it in self.instances.selectedItems()]

    def selected_mates(self):
        return [it.data(ROLE_ID) for it in self.mates.selectedItems()]

    def shows(self, key):
        """Whether the visibility dropdown has `key` ("connectors", "mates", "sketches") ticked."""
        return self.vis_actions[key].isChecked()

    def select_instances(self, ids):
        self.instances.blockSignals(True)
        for i in range(self.instances.count()):
            it = self.instances.item(i)
            it.setSelected(it.data(ROLE_ID) in ids)
        self.instances.blockSignals(False)


# =========================================================================== insert panel

class InsertPanel(QFrame):
    """Onshape-style Insert panel: pick a part (or a whole Part Studio) and it's added to the assembly.
    It stays open so you can insert several things."""
    closed = Signal()

    def __init__(self, ctl):
        super().__init__(ctl.w.viewport)
        _floating(self)
        self.ctl = ctl
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(6)
        head, self.title = _head(self, "Insert parts", "insert", None, self.close_panel, cancel_tip="Close (Esc)")
        self.title.setReadOnly(True)
        lay.addLayout(head)
        lay.addWidget(_hline())
        parts_page = QWidget()
        pl = QVBoxLayout(parts_page)
        pl.setContentsMargins(0, 6, 0, 0)
        pl.setSpacing(6)
        hint = QLabel("Double-click a part to insert it. Pick a Part Studio to insert all its parts as they "
                      "sit there. Inserted parts stay linked: edit them in their tab and the assembly updates.")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        pl.addWidget(hint)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIconSize(QSize(PR.INSERT_SIZE, PR.INSERT_SIZE))
        self.previews = PR.PreviewFiller(self.tree, PR.INSERT_SIZE)
        self.tree.setMinimumHeight(220)
        self.tree.itemDoubleClicked.connect(lambda it, _c: self._insert_item(it))
        pl.addWidget(self.tree)
        row = QHBoxLayout()
        b = QPushButton("Insert")
        b.setDefault(True)
        b.clicked.connect(lambda: self._insert_item(self.tree.currentItem()))
        row.addWidget(b)
        f = QPushButton("From file…")
        f.setToolTip("A FreeShape document (.fshape) or a STEP file")
        f.clicked.connect(self._from_file)
        row.addWidget(f)
        self.other_doc_action = QAction("Other Document…", self)
        self.other_doc_action.setObjectName("insert_other_document")
        self.other_doc_action.setToolTip("A part of a Version of another Document on this machine")
        self.other_doc_action.triggered.connect(self._from_other_document)
        od = QPushButton("Other Document…")
        od.setToolTip(self.other_doc_action.toolTip())
        od.clicked.connect(self.other_doc_action.trigger)
        row.addWidget(od)
        pl.addLayout(row)
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        self.msg.setObjectName("muted")
        pl.addWidget(self.msg)
        from .standard_parts_ui import StandardPartsPane
        self.standard = StandardPartsPane(lambda: ctl.doc, ctl.w.viewport)
        std_page = QWidget()
        sl = QVBoxLayout(std_page)
        sl.setContentsMargins(0, 6, 0, 0)
        sl.addWidget(self.standard)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("insert_tabs")
        self.tabs.addTab(parts_page, "Parts")
        self.tabs.addTab(std_page, "Standard parts")
        lay.addWidget(self.tabs)
        self.fill()
        self.setFixedWidth(300)
        self.adjustSize()
        self.move(12, 12)
        self.show()

    def drain_previews(self):
        self.previews.drain()

    def fill(self):
        self.tree.clear()
        self.previews.reset()
        for doc in self.ctl.w.studio_docs():
            cfgs = doc.config_names() if hasattr(doc, "config_names") else []
            # one entry per configuration (only the active one opened), or just the studio
            for cfg in (cfgs or [None]):
                st = doc.state_for(cfg) if cfg else doc.state
                top = QTreeWidgetItem([doc.name + (f" · {cfg}" if cfg else "")])
                top.setIcon(0, icon("configs" if cfg else "part", 16))
                top.setData(0, ROLE_KIND, "studio")
                top.setData(0, ROLE_ID, (doc.uid, cfg))
                shown = [p for p in st.parts.values() if not p.reference]    # Context bodies are not insertable
                for p in shown:
                    it = QTreeWidgetItem([p.name])
                    it.setIcon(0, swatch(p.color))
                    it.setData(0, ROLE_KIND, "part")
                    it.setData(0, ROLE_ID, (doc.uid, p.id, cfg))
                    top.addChild(it)
                    key, make = PR.part_job(p, PR.INSERT_SIZE)
                    self.previews.add(lambda ic, it=it: it.setIcon(0, ic), key, make)
                if not shown:
                    it = QTreeWidgetItem(["(no parts yet)"])
                    it.setDisabled(True)
                    top.addChild(it)
                for sid in st.sketches:                          # the studio's sketches, under its parts
                    it = QTreeWidgetItem([str((doc.feature(sid) or {}).get("name", sid))])
                    it.setIcon(0, icon("sketch", 16))
                    it.setData(0, ROLE_KIND, "sketch")
                    it.setData(0, ROLE_ID, (doc.uid, sid))
                    top.addChild(it)
                self.tree.addTopLevelItem(top)
                top.setExpanded(cfg is None or cfg == doc.active_config())
        if self.tree.topLevelItemCount() == 0:
            it = QTreeWidgetItem(["No Part Studios open — use “From file…”"])
            it.setDisabled(True)
            self.tree.addTopLevelItem(it)

    def _insert_item(self, it):
        if it is None or it.isDisabled():
            return
        kind = it.data(0, ROLE_KIND)
        if kind == "part":
            uid, pid, cfg = it.data(0, ROLE_ID)
            self.ctl.insert_studio_parts(uid, [pid], cfg)
        elif kind == "sketch":
            uid, sid = it.data(0, ROLE_ID)
            self.ctl.insert_studio_sketch(uid, sid)
        elif kind == "studio":
            uid, cfg = it.data(0, ROLE_ID)
            self.ctl.insert_studio_parts(uid, None, cfg)
        self.msg.setText(f"Inserted {it.text(0)}")

    def _from_file(self):
        path, _ = filedlg.getOpenFileName(self, "Insert from file", "",
                                              "Parts (*.fshape *.fcad *.step *.stp);;All files (*)")
        if not path:
            return
        if path.lower().endswith((".step", ".stp")):
            n = self.ctl.insert_step(path)
            self.msg.setText(f"Inserted {n} part(s) from {os.path.basename(path)}")
        else:
            doc = self.ctl.w.open_studio_for_link(path)
            if doc is not None:
                self.fill()
                self.ctl.insert_studio_parts(doc.uid, None)
                self.msg.setText(f"Inserted the parts of {doc.name}")

    def _from_other_document(self):
        from .document_picker import OtherDocumentDialog
        folder = self.ctl.w.folder
        if folder is None:
            self.msg.setText("Save this as a Document to insert from another one.")
            return
        dlg = OtherDocumentDialog(folder, self)
        if dlg.exec() == QDialog.Accepted:
            n = self.ctl.insert_document_sources(dlg.sources())
            self.msg.setText(f"Inserted {n} part(s) from another Document")

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close_panel()
        else:
            super().keyPressEvent(e)

    def close_panel(self):
        self.closed.emit()


# =========================================================================== exploded view

class ExplodePanel(QFrame):
    """The exploded view: parts slid apart along directions, step by step (shown only while this is open; the
    real positions and mates don't change)."""
    closed = Signal()

    def __init__(self, ctl):
        super().__init__(ctl.w.viewport)
        from PySide6.QtWidgets import QSlider, QDoubleSpinBox
        _floating(self)
        self.ctl = ctl
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(6)
        head, self.title = _head(self, "Exploded view", "explode", None, self.close_panel, cancel_tip="Close (Esc)")
        self.title.setReadOnly(True)
        lay.addLayout(head)
        lay.addWidget(_hline())
        hint = QLabel("Parts are only moved apart for showing (and drawings); their real positions and mates "
                      "don't change.")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        lay.addWidget(hint)
        auto = QPushButton("Auto explode")
        auto.setToolTip("Move every part out from the fixed one along its nearest main axis")
        auto.clicked.connect(self._auto)
        lay.addWidget(auto)
        lay.addWidget(QLabel("<b>Add a step</b> — select parts, then:"))
        row = QHBoxLayout()
        self.dir = QComboBox()
        for lab, v in (("+Z", (0, 0, 1)), ("−Z", (0, 0, -1)), ("+X", (1, 0, 0)), ("−X", (-1, 0, 0)),
                       ("+Y", (0, 1, 0)), ("−Y", (0, -1, 0))):
            self.dir.addItem(lab, v)
        row.addWidget(self.dir)
        self.dist = QDoubleSpinBox()
        self.dist.setRange(-100000, 100000)
        self.dist.setValue(30)
        self.dist.setSuffix(" mm")
        row.addWidget(self.dist, 1)
        add = QPushButton("Add")
        add.clicked.connect(self._add)
        row.addWidget(add)
        lay.addLayout(row)
        self.steps = QListWidget()
        self.steps.setMinimumHeight(110)
        lay.addWidget(self.steps)
        row = QHBoxLayout()
        rm = QPushButton("Remove step")
        rm.clicked.connect(self._remove)
        row.addWidget(rm)
        clr = QPushButton("Clear all")
        clr.clicked.connect(lambda: (self.ctl.doc.clear_explode(), self.fill()))
        row.addWidget(clr)
        lay.addLayout(row)
        lay.addWidget(QLabel("Assembled ↔ exploded"))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(int(round(100 * float(self.ctl.doc.explode.get("amount", 1.0)))))
        self.slider.valueChanged.connect(self._amount)
        lay.addWidget(self.slider)
        dwg = QPushButton("Drawing of this exploded view…")
        dwg.clicked.connect(lambda: self.ctl.w.new_drawing({"kind": "assembly", "uid": self.ctl.doc.uid,
                                                            "part": "*", "exploded": True}))
        lay.addWidget(dwg)
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        self.msg.setObjectName("muted")
        lay.addWidget(self.msg)
        self.fill()
        self.setFixedWidth(300)
        self.adjustSize()
        self.move(12, 12)
        self.show()

    def fill(self):
        doc = self.ctl.doc
        self.steps.clear()
        for st in doc.explode.get("steps", []):
            names = ", ".join((doc.instance(i) or {}).get("name", "?") for i in st["ids"])
            d = st["dir"]
            ax = next((lab for lab, v in (("+X", (1, 0, 0)), ("−X", (-1, 0, 0)), ("+Y", (0, 1, 0)),
                                          ("−Y", (0, -1, 0)), ("+Z", (0, 0, 1)), ("−Z", (0, 0, -1)))
                       if np.allclose(d, v, atol=1e-6)), "along " + ", ".join(f"{x:.2f}" for x in d))
            self.steps.addItem(f"{names}: {ax} {st['dist']:.1f} mm")
        self.ctl.show_scene()

    def _auto(self):
        try:
            n = self.ctl.doc.auto_explode()
            self.slider.setValue(100)
            self.msg.setText(f"{n} part(s) moved apart — adjust or add steps below")
        except K.KernelError as e:
            self.msg.setText(str(e))
        self.fill()

    def _add(self):
        ids = self.ctl.panel.selected_instances() or list({pk.key[0] for pk in self.ctl.vp.selection
                                                           if pk.kind in ("face", "edge", "part")})
        try:
            self.ctl.doc.add_explode_step(ids, self.dir.currentData(), self.dist.value())
            self.msg.setText(f"Moved {len(ids)} part(s)")
        except K.KernelError as e:
            self.msg.setText(str(e))
        self.fill()

    def _remove(self):
        r = self.steps.currentRow()
        doc = self.ctl.doc
        if 0 <= r < len(doc.explode.get("steps", [])):
            doc.checkpoint()
            doc.explode["steps"].pop(r)
            doc.notify()
            self.fill()

    def _amount(self, v):
        self.ctl.doc.explode["amount"] = v / 100.0
        self.ctl.doc.dirty = True
        self.ctl.show_scene()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close_panel()
        else:
            super().keyPressEvent(e)

    def close_panel(self):
        self.closed.emit()


# =========================================================================== mate dialog

class ConnectorField(QFrame):
    activated = Signal(object)

    def __init__(self, label):
        super().__init__()
        self.setObjectName("selField")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(2)
        top = QHBoxLayout()
        t = QLabel(label)
        t.setStyleSheet("color:#4b5563;")
        top.addWidget(t, 1)
        self.clear_btn = QToolButton()
        self.clear_btn.setText("✕")
        self.clear_btn.setToolTip("Clear")
        top.addWidget(self.clear_btn)
        lay.addLayout(top)
        self.text = QLabel("—")
        self.text.setWordWrap(True)
        lay.addWidget(self.text)
        self.set_active(False)

    def mousePressEvent(self, e):
        self.activated.emit(self)

    def set_active(self, a):
        self.setProperty("active", "true" if a else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class MateDialog(QFrame):
    accepted = Signal()
    rejected = Signal()

    def __init__(self, ctl, mate, is_new, preselection=()):
        super().__init__(ctl.w.viewport)
        _floating(self)
        self.ctl = ctl
        self.doc = ctl.doc
        self.mate = copy.deepcopy(mate)
        self.is_new = is_new
        self._backup = copy.deepcopy(self.doc.instances)
        self._backup_mates = copy.deepcopy(self.doc.mates)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._preview)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(6)
        head, self.title = _head(self, self.mate["name"], "m_" + self.mate["type"], self.accept, self.reject)
        self.head_icon = head.itemAt(0).widget()
        lay.addLayout(head)
        lay.addWidget(_hline())
        # mate type, as a row of icon buttons (like Onshape's mate dialog)
        trow = QHBoxLayout()
        trow.setSpacing(2)
        self.type_buttons = {}
        for t, (label, tip) in A.MATE_TYPES.items():
            b = QToolButton()
            b.setIcon(icon("m_" + t))
            b.setIconSize(QSize(22, 22))
            b.setCheckable(True)
            b.setToolTip(f"{label} — {tip}")
            b.clicked.connect(lambda _=False, tt=t: self.set_type(tt))
            trow.addWidget(b)
            self.type_buttons[t] = b
        trow.addStretch(1)
        lay.addLayout(trow)
        self.type_label = QLabel("")
        self.type_label.setObjectName("muted")
        self.type_label.setWordWrap(True)
        lay.addWidget(self.type_label)
        self.fa = ConnectorField("First mate connector")
        self.fb = ConnectorField("Second mate connector")
        for f, side in ((self.fa, "a"), (self.fb, "b")):
            f.activated.connect(self.activate)
            f.clear_btn.clicked.connect(lambda _=False, s=side: self._clear(s))
            lay.addWidget(f)
        hint = QLabel("Click a flat face, a round face (picks the end nearest the click), a circular edge "
                      "or a straight edge on each part."
                      + (" On a sketch, click a point, a line end or middle, a circle centre, or a teal connector."
                         if any(self.doc.is_sketch_instance(i["id"]) for i in self.doc.instances) else ""))
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        lay.addWidget(hint)
        # options
        orow = QHBoxLayout()
        self.flip = QToolButton()
        self.flip.setText("⇅ Flip")
        self.flip.setCheckable(True)
        self.flip.setToolTip("Flip the primary (Z) axis — e.g. put a pin in the other way round")
        self.flip.clicked.connect(lambda c: self._set("flip", bool(c)))
        orow.addWidget(self.flip)
        self.rot = QToolButton()
        self.rot.setText("⟳ 90°")
        self.rot.setToolTip("Turn the second part 90° about the Z axis")
        self.rot.clicked.connect(self._rotate90)
        orow.addWidget(self.rot)
        orow.addStretch(1)
        lay.addLayout(orow)
        self.offset_row, self.offset = self._num_row("Offset", self.mate.get("offset", 0.0), "offset", "mm")
        lay.addWidget(self.offset_row)
        self.angle_row, self.angle = self._num_row("Angle", self.mate.get("angle", 0.0), "angle", "°")
        lay.addWidget(self.angle_row)
        self.values_box = QWidget()
        self.values_lay = QVBoxLayout(self.values_box)
        self.values_lay.setContentsMargins(0, 0, 0, 0)
        self.values_lay.setSpacing(2)
        self.value_labels = {}
        lay.addWidget(self.values_box)
        # Limits: staged here, applied with set_limits by the controller on Accept (Cancel never touches them)
        self.limits_check = QCheckBox("Limits")
        self.limits_check.setChecked(bool(self.mate.get("limits")))
        self.limits_check.toggled.connect(lambda _c: self._refresh_limits())
        lay.addWidget(self.limits_check)
        self.limits_box = QWidget()
        self.limits_lay = QVBoxLayout(self.limits_box)
        self.limits_lay.setContentsMargins(0, 0, 0, 0)
        self.limits_lay.setSpacing(2)
        self.limit_edits = {}
        self._limit_text = {d: [fmt(lo), fmt(hi)] for d, _l, _u, lo, hi in limit_rows(self.mate)}
        self.limits = None               # {freedom: (lo, hi)} once accepted
        lay.addWidget(self.limits_box)
        self.err = QLabel("")
        self.err.setObjectName("dialogError")
        self.err.setWordWrap(True)
        self.err.hide()
        lay.addWidget(self.err)
        self.active = None
        self._live = True
        self.doc.listeners.append(self._doc_notified)
        self.set_type(self.mate["type"], preview=False)
        self.flip.setChecked(bool(self.mate.get("flip")))
        self._apply_preselection(preselection)
        self.activate(self.fa if not self.mate.get("a") else self.fb if not self.mate.get("b") else self.fa)
        self._refresh_fields()
        self.setFixedWidth(300)
        self.adjustSize()
        self.move(12, 12)
        self.show()
        self.schedule()

    def _num_row(self, label, value, key, unit):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        lab = QLabel(label)
        lab.setFixedWidth(60)
        h.addWidget(lab)
        ed = QLineEdit(fmt(float(value or 0)))
        ed.setProperty("unit", unit)

        def done(ed=ed, key=key):
            try:
                v = parse_number(ed.text(), angle=(key == "angle"))
                if v is None:
                    raise ValueError
            except Exception:
                ed.setProperty("invalid", "true")
                ed.style().unpolish(ed)
                ed.style().polish(ed)
                return
            ed.setProperty("invalid", "false")
            ed.style().unpolish(ed)
            ed.style().polish(ed)
            self._set(key, float(v))
        ed.editingFinished.connect(done)
        ed.returnPressed.connect(done)
        h.addWidget(ed, 1)
        h.addWidget(QLabel(unit))
        return w, ed

    # ------------------------------------------------------------------ state
    def set_type(self, t, preview=True):
        self.mate["type"] = t
        for k, b in self.type_buttons.items():
            b.setChecked(k == t)
        label, tip = A.MATE_TYPES[t]
        self.type_label.setText(f"<b>{label}</b> — {tip}")
        self.head_icon.setPixmap(icon("m_" + t).pixmap(20, 20))
        base = self.title.text().rsplit(" ", 1)
        if self.is_new and len(base) == 2 and base[0] in [v[0] for v in A.MATE_TYPES.values()]:
            self.title.setText(self.doc.make_mate(t)["name"] if self.is_new else self.title.text())
        self.angle_row.setVisible(t in ("fastened", "slider"))
        self.offset_row.setVisible(t in ("fastened", "revolute", "planar", "pin_slot", "ball"))
        self.rot.setVisible(t in ("fastened", "slider"))
        self.flip.setVisible(t != "ball")
        self._refresh_limits()
        self._refresh_values()
        self.adjustSize()
        if preview:
            self.schedule()

    def _set(self, key, value):
        self.mate[key] = value
        self.schedule()

    # read-only Mate values: one row per freedom, kept current while the document changes
    def _refresh_values(self):
        live = self.doc.mate(self.mate["id"]) if not self.is_new else None
        src = dict(self.mate)
        if live is not None and live["type"] == self.mate["type"]:
            src["values"] = live.get("values")
        rows = value_rows(src)
        if list(self.value_labels) != [r[0] for r in rows]:
            while self.values_lay.count():
                self.values_lay.takeAt(0).widget().deleteLater()
            self.value_labels = {}
            for d, label, _t, unit in rows:
                w = QWidget()
                h = QHBoxLayout(w)
                h.setContentsMargins(0, 0, 0, 0)
                lab = QLabel("Value" if len(rows) == 1 else label)
                lab.setFixedWidth(60)
                h.addWidget(lab)
                val = QLabel()
                val.setObjectName("muted")
                h.addWidget(val, 1)
                h.addWidget(QLabel(unit))
                self.values_lay.addWidget(w)
                self.value_labels[d] = val
        for d, _l, text, _u in rows:
            self.value_labels[d].setText(text)
        self.values_box.setVisible(bool(rows))

    # Limits: a min and a max field per freedom, blank meaning no limit at that end
    def _refresh_limits(self):
        dofs = A.MATE_VALUE_DOFS.get(self.mate["type"], ())
        for d, (lo, hi) in self.limit_edits.items():
            self._limit_text[d] = [lo.text(), hi.text()]
        while self.limits_lay.count():
            self.limits_lay.takeAt(0).widget().deleteLater()
        self.limit_edits = {}
        self.limits_check.setVisible(bool(dofs))
        show = bool(dofs) and self.limits_check.isChecked()
        if show:
            for d in dofs:
                w = QWidget()
                h = QHBoxLayout(w)
                h.setContentsMargins(0, 0, 0, 0)
                lab = QLabel(VALUE_LABELS.get(d, d))
                lab.setFixedWidth(60)
                h.addWidget(lab)
                lo_t, hi_t = self._limit_text.get(d, ["", ""])
                lo, hi = QLineEdit(lo_t), QLineEdit(hi_t)
                lo.setPlaceholderText("min")
                hi.setPlaceholderText("max")
                h.addWidget(lo, 1)
                h.addWidget(hi, 1)
                h.addWidget(QLabel(value_unit(d)))
                self.limits_lay.addWidget(w)
                self.limit_edits[d] = (lo, hi)
        self.limits_box.setVisible(show)

    def limit_values(self):
        """{freedom: (lo, hi)} for every freedom of the mate type; None is no bound, and an unticked Limits
        gives no bounds anywhere. Raises ValueError (with a message for the user) on bad text or min > max."""
        out = {d: (None, None) for d in A.MATE_VALUE_DOFS.get(self.mate["type"], ())}
        if not self.limits_check.isChecked():
            return out
        for d, edits in self.limit_edits.items():
            bounds = []
            for ed, end in zip(edits, ("min", "max")):
                text = ed.text().strip()
                if not text:
                    bounds.append(None)
                    continue
                v = parse_number(text, angle=value_unit(d) == "°")
                if v is None:
                    raise ValueError(f"{VALUE_LABELS.get(d, d)} {end}: can't read \"{text}\" as a number")
                bounds.append(float(v))
            if None not in bounds and bounds[0] > bounds[1]:
                raise ValueError(f"{VALUE_LABELS.get(d, d)}: the minimum is above the maximum")
            out[d] = tuple(bounds)
        return out

    def _doc_notified(self):
        if not self._live:
            return
        try:
            self._refresh_values()
        except RuntimeError:             # the dialog was deleted without accept/reject
            self._detach()

    def _detach(self):
        self._live = False
        if self._doc_notified in self.doc.listeners:
            self.doc.listeners.remove(self._doc_notified)

    def _rotate90(self):
        a = (float(self.mate.get("angle", 0.0) or 0.0) + 90.0) % 360.0
        self.mate["angle"] = a
        self.angle.setText(fmt(a))
        self.schedule()

    def _clear(self, side):
        self.mate[side] = None
        self.activate(self.fa if side == "a" else self.fb)
        self._refresh_fields()
        self.schedule()

    def activate(self, field):
        if self.active is not None:
            self.active.set_active(False)
        self.active = field
        field.set_active(True)
        self.ctl.w.viewport.select_filter = {"face", "edge", "sketch_point", "sketch_line"}
        self._refresh_fields()

    def _refresh_fields(self):
        for f, side in ((self.fa, "a"), (self.fb, "b")):
            c = self.mate.get(side)
            if not c:
                f.text.setText("Click to select" if f is self.active else "—")
                f.text.setStyleSheet("color:#8a94a3;")
            else:
                inst = self.doc.instance(c["inst"])
                if c.get("kind") == "sketch":
                    f.text.setText(f"{inst['name'] if inst else '?'} {SKETCH_ROLE_WORD.get(c.get('role'), c.get('role'))}")
                else:
                    f.text.setText(f"{inst['name'] if inst else '?'} — {c['frame'].get('what', c['kind'])}")
                f.text.setStyleSheet("")
        self.ctl.update_triads()

    def snap_candidates(self, pick: Pick):
        """Snap points for the viewport to draw while hovering a face or edge, or a Sketch instance's geometry
        (one dot per role of the entity: end 1, middle, end 2, centre, the point)."""
        if pick.kind in ("sketch_line", "sketch_point"):
            return self._sketch_snaps(*pick.key)
        if pick.kind not in ("face", "edge"):
            return []
        iid, idx = pick.key
        return self.doc.snap_points(iid, pick.kind, idx)

    def own_connectors(self):
        """[(instance id, published connector dict, world frame)] for every instance's own mate connectors."""
        out = []
        for inst in self.doc.visible_instances():
            for pc in self.doc.published_connectors(inst["id"]) or []:
                out.append((inst["id"], pc, self.doc.world_frame(self.doc.named_connector(inst["id"], pc["id"]))))
        return out

    def pick_named(self, iid, cid):
        """Use instance `iid`'s own published connector `cid` for the active field."""
        try:
            c = self.doc.named_connector(iid, cid)
        except Exception as e:
            self._error(f"Can't use that: {e}")
            return
        side = "a" if self.active is self.fa else "b"
        other = self.mate.get("b" if side == "a" else "a")
        if other and other["inst"] == iid:
            self._error("Pick the second connector on a different part.")
            return
        self.mate[side] = c
        if side == "a" and not self.mate.get("b"):
            self.activate(self.fb)
        elif side == "b" and not self.mate.get("a"):
            self.activate(self.fa)
        self._refresh_fields()
        self.schedule()

    def _own_connector_at(self, xy, tol=12.0):
        """The (instance id, connector id) whose triad origin is within `tol` px of screen point `xy`, or None."""
        best = None
        for iid, pc, fr in self.own_connectors():
            sp = self.ctl.w.viewport.cam.to_screen(np.asarray(fr["o"], float))
            d = math.hypot(sp[0] - xy[0], sp[1] - xy[1])
            if d <= tol and (best is None or d < best[0]):
                best = (d, iid, pc["id"])
        return None if best is None else best[1:]

    def _sketch_roles(self):
        """[(instance id, entity id, role, world point)] for every role of every entity of every shown Sketch
        instance. vp.sketches already carries each plane moved by its instance transform, so these are world points."""
        from .. import sketchref
        out = []
        for iid, sr, _m in self.ctl.w.viewport.sketches:
            if not self.doc.is_sketch_instance(iid):
                continue
            plane = {"origin": sr.plane.origin, "xdir": sr.plane.xdir, "normal": sr.plane.normal}
            sk = sr.sketch.to_dict()
            for eid in sk["entities"]:
                try:
                    for role in sketchref.roles(sk, eid):
                        out.append((iid, eid, role, np.asarray(sketchref.frame(plane, sk, eid, role)["o"], float)))
                except K.KernelError:
                    continue
        return out

    def _sketch_snaps(self, iid, eid):
        """Snap dots for the hovered sketch entity `eid` of Sketch instance `iid`: one per role. "type" picks the
        label the viewport draws; "role" is what a click on the dot stores."""
        return [{"type": SKETCH_SNAP_KIND[r], "p": pw, "pw": pw, "role": r, "inst": i, "entity": e}
                for i, e, r, pw in self._sketch_roles() if i == iid and e == eid]

    def _screen_hit(self, xy, tol=12.0):
        """What is under screen point `xy` among the things that must win over the face and edge pick: a triad of an
        instance's own connector ("named", iid, cid) or a sketch role dot ("sketch", iid, eid, role). The nearest in
        screen space wins; a triad wins a tie. None when neither is within `tol` px."""
        cam = self.ctl.w.viewport.cam
        best = None
        for iid, pc, fr in self.own_connectors():
            sp = cam.to_screen(np.asarray(fr["o"], float))
            d = math.hypot(sp[0] - xy[0], sp[1] - xy[1])
            if d <= tol and (best is None or d < best[0]):
                best = (d, ("named", iid, pc["id"]))
        for iid, eid, role, pw in self._sketch_roles():
            sp = cam.to_screen(pw)
            d = math.hypot(sp[0] - xy[0], sp[1] - xy[1])
            if d <= tol and (best is None or d < best[0]):
                best = (d, ("sketch", iid, eid, role))
        return None if best is None else best[1]

    def _nearest_role(self, iid, eid, xy):
        """The role of sketch entity `eid` whose dot is nearest screen point `xy` (a click on the line, off every dot)."""
        cam = self.ctl.w.viewport.cam
        cand = [(math.hypot(cam.to_screen(pw)[0] - xy[0], cam.to_screen(pw)[1] - xy[1]), r)
                for i, e, r, pw in self._sketch_roles() if i == iid and e == eid]
        return min(cand)[1] if cand else None

    def _use_hit(self, hit):
        if hit[0] == "named":
            self.pick_named(hit[1], hit[2])
        else:
            self.pick_sketch(*hit[1:])

    def pick_sketch(self, iid, eid, role):
        """Use `role` of sketch entity `eid` on Sketch instance `iid` for the active field."""
        try:
            c = self.doc.sketch_end(iid, eid, role)
        except Exception as e:
            self._error(f"Can't use that: {e}")
            return
        side = "a" if self.active is self.fa else "b"
        other = self.mate.get("b" if side == "a" else "a")
        if other and other["inst"] == iid:
            self._error("Pick the second connector on a different part.")
            return
        self.mate[side] = c
        if side == "a" and not self.mate.get("b"):
            self.activate(self.fb)
        elif side == "b" and not self.mate.get("a"):
            self.activate(self.fa)
        self._refresh_fields()
        self.schedule()

    def handle_miss(self):
        """A click that hit no face or edge: it may still be on a connector triad or a sketch point floating off the part."""
        vp = self.ctl.w.viewport
        hit = self._screen_hit(vp.last_click) if vp.last_click else None
        if hit is not None:
            vp.clear_snaps()
            self._use_hit(hit)

    def handle_pick(self, pick: Pick):
        vp = self.ctl.w.viewport
        if pick.kind == "connector":         # chosen from Select other: the triad itself, whatever is under the pointer
            vp.clear_snaps()
            self.pick_named(*pick.key)
            return
        hit = self._screen_hit(vp.last_click) if vp.last_click else None
        if hit is not None:                  # a triad or a sketch dot under the pointer beats the face under it
            vp.clear_snaps()
            self._use_hit(hit)
            return
        if pick.kind in ("sketch_line", "sketch_point"):     # on a sketch line but off every dot: the nearest role
            vp.clear_snaps()
            iid, eid = pick.key
            role = self._nearest_role(iid, eid, vp.last_click) if vp.last_click else None
            if role is not None:
                self.pick_sketch(iid, eid, role)
            return
        snap, base = vp.snap_hi, vp.snap_base
        if snap is not None and base is not None and base.kind in ("face", "edge"):
            pick = base                      # a highlighted snap point wins over whatever is under the cursor
        else:
            snap = None
        vp.clear_snaps()
        if pick.kind not in ("face", "edge"):
            return
        iid, idx = pick.key
        click = vp.surface_point(*vp.last_click) if vp.last_click else None
        if pick.kind == "edge" and vp.last_click:
            click = None
        try:
            c = self.doc.connector(iid, pick.kind, idx, click, snap)
        except Exception as e:
            self._error(f"Can't use that: {e}")
            return
        side = "a" if self.active is self.fa else "b"
        other = self.mate.get("b" if side == "a" else "a")
        if other and other["inst"] == iid:
            self._error("Pick the second connector on a different part.")
            return
        self.mate[side] = c
        if side == "a" and not self.mate.get("b"):
            self.activate(self.fb)
        elif side == "b" and not self.mate.get("a"):
            self.activate(self.fa)
        self._refresh_fields()
        self.schedule()

    def _apply_preselection(self, picks):
        for pk in picks:
            if pk.kind not in ("face", "edge"):
                continue
            side = "a" if not self.mate.get("a") else "b" if not self.mate.get("b") else None
            if side is None:
                break
            iid, idx = pk.key
            if side == "b" and self.mate["a"]["inst"] == iid:
                continue
            try:
                self.mate[side] = self.doc.connector(iid, pk.kind, idx, None)
            except Exception:
                pass
        self.ctl.w.viewport.clear_selection()

    def _error(self, text):
        self.err.setText(text)
        self.err.setVisible(bool(text))
        self.adjustSize()

    # ------------------------------------------------------------------ preview
    def schedule(self):
        self._timer.start(30)

    def _restore(self):
        self.doc.instances = copy.deepcopy(self._backup)
        self.doc.mates = copy.deepcopy(self._backup_mates)

    def _preview(self):
        self._restore()
        m = self.mate
        if m.get("a") and m.get("b"):
            mates = [x for x in self.doc.mates if x["id"] != m["id"]]
            mates.append(m)
            self.doc.mates = mates
            self.doc.place_for_mate(m)
            self.doc.solve()
            err = self.doc.errors.get(m["id"])
            self._error(err or "")
        else:
            self.doc.solve()
            self._error("")
        self._refresh_values()
        self.ctl.show_scene()
        self.ctl.update_triads()

    def accept(self):
        self._timer.stop()
        self.mate["name"] = self.title.text().strip() or self.mate["name"]
        if not (self.mate.get("a") and self.mate.get("b")):
            self._error("Pick a mate connector on each of the two parts first.")
            return
        try:
            self.limits = self.limit_values()
        except ValueError as e:
            self._error(str(e))
            return
        lim = {d: v for d, v in (self.mate.get("limits") or {}).items() if d in self.limits}
        if lim:
            self.mate["limits"] = lim          # drop limits on freedoms a changed type no longer has
        else:
            self.mate.pop("limits", None)
        self._detach()
        self._restore()
        self.accepted.emit()

    def reject(self):
        self._timer.stop()
        self._detach()
        self._restore()
        self.doc.solve()
        self.rejected.emit()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.accept()
        elif e.key() == Qt.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(e)


# =========================================================================== relation dialog

def mate_freedoms(mate, kind):
    """The freedoms of `mate` of one kind (DOF_KINDS), in MATE_VALUE_DOFS order."""
    return [d for d in A.MATE_VALUE_DOFS.get(mate["type"], ()) if A.dof_kind(d) == kind]


class RelationDialog(QFrame):
    """Add or edit a relation (gear, rack and pinion, screw, linear), driven by A.RELATION_TYPES. Two mate fields
    are filled from the mate list or by clicking a part near a mate's connector (one field for a same_mate type).
    A freedom chooser appears for a side only when its mate has several freedoms of the kind that side needs.
    Nothing is previewed: adding a relation moves no part (ADR 0005)."""
    accepted = Signal()
    rejected = Signal()

    def __init__(self, ctl, rel, is_new):
        super().__init__(ctl.w.viewport)
        _floating(self)
        self.ctl = ctl
        self.doc = ctl.doc
        self.rel = copy.deepcopy(rel)
        self.is_new = is_new
        self.result = None
        self.sel = {s: (rel.get(s) or {}).get("mate") for s in ("a", "b")}
        self.dof_choice = {s: (rel.get(s) or {}).get("dof") for s in ("a", "b")}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(6)
        head, self.title = _head(self, self.rel["name"], "r_" + self.rel["type"], self.accept, self.reject)
        self.head_icon = head.itemAt(0).widget()
        lay.addLayout(head)
        lay.addWidget(_hline())
        trow = QHBoxLayout()
        trow.setSpacing(2)
        self.type_buttons = {}
        for t, spec in A.RELATION_TYPES.items():
            b = QToolButton()
            b.setIcon(icon("r_" + t))
            b.setIconSize(QSize(22, 22))
            b.setCheckable(True)
            b.setToolTip(f"{spec['label']} — {spec['help']}")
            b.clicked.connect(lambda _=False, tt=t: self.set_type(tt))
            trow.addWidget(b)
            self.type_buttons[t] = b
        trow.addStretch(1)
        lay.addLayout(trow)
        self.type_label = QLabel("")
        self.type_label.setObjectName("muted")
        self.type_label.setWordWrap(True)
        lay.addWidget(self.type_label)
        self.fields, self.choosers = {}, {}
        for side in ("a", "b"):
            f = ConnectorField("")
            f.activated.connect(self.activate)
            f.clear_btn.clicked.connect(lambda _=False, s=side: self._clear(s))
            lay.addWidget(f)
            self.fields[side] = f
            combo = QComboBox()
            combo.currentIndexChanged.connect(lambda _i, s=side: self._chosen(s))
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            lab = QLabel("Freedom")
            lab.setFixedWidth(60)
            h.addWidget(lab)
            h.addWidget(combo, 1)
            lay.addWidget(row)
            self.choosers[side] = (row, combo)
        hint = QLabel("Click a mate in the list, or a part next to a mate's connector in the view.")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        lay.addWidget(hint)
        rrow = QWidget()
        h = QHBoxLayout(rrow)
        h.setContentsMargins(0, 0, 0, 0)
        self.ratio_label = QLabel("Ratio")
        self.ratio_label.setFixedWidth(60)
        h.addWidget(self.ratio_label)
        self.ratio = QLineEdit(fmt(float(self.rel.get("ratio", 1.0))))
        h.addWidget(self.ratio, 1)
        self.ratio_unit = QLabel("")
        h.addWidget(self.ratio_unit)
        lay.addWidget(rrow)
        self.reverse = QCheckBox("Reverse")
        self.reverse.setChecked(bool(self.rel.get("reverse")))
        self.reverse.setToolTip("Make the second freedom move the opposite way")
        lay.addWidget(self.reverse)
        self.err = QLabel("")
        self.err.setObjectName("dialogError")
        self.err.setWordWrap(True)
        self.err.hide()
        lay.addWidget(self.err)
        self.active = self.fields["a"]
        self.set_type(self.rel["type"])
        self.activate(self.fields["a"] if not self.sel["a"] else self.fields["b"] if not self.sel["b"]
                      else self.fields["a"])
        self.setFixedWidth(300)
        self.adjustSize()
        self.move(12, 12)
        self.show()

    # ------------------------------------------------------------------ state
    @property
    def spec(self):
        return A.RELATION_TYPES[self.rel["type"]]

    def set_type(self, t):
        old = self.rel["type"]
        self.rel["type"] = t
        spec = self.spec
        for k, b in self.type_buttons.items():
            b.setChecked(k == t)
        self.type_label.setText(f"<b>{spec['label']}</b> — {spec['help']}")
        self.head_icon.setPixmap(icon("r_" + t).pixmap(20, 20))
        if self.is_new and self.title.text() == self.doc.make_relation(old)["name"]:
            self.title.setText(self.doc.make_relation(t)["name"])
        self.ratio_label.setText("Distance" if spec["ratio_unit"].endswith("/rev") else "Ratio")
        self.ratio_unit.setText(spec["ratio_unit"])
        if spec.get("same_mate"):
            self.sel["a"] = self.sel["a"] or self.sel["b"]
            self.sel["b"] = self.sel["a"]
            self.active = self.fields["a"]
        elif A.RELATION_TYPES[old].get("same_mate") and self.sel["a"] == self.sel["b"]:
            self.sel["b"] = None
        self.fields["b"].setVisible(not spec.get("same_mate"))
        self._normalise()
        self._refresh()

    def _side_label(self, side):
        spec = self.spec
        if spec.get("same_mate"):
            return "Cylindrical mate"
        kind = spec[side]
        if spec["a"] != spec["b"]:
            return f"{'Rotation' if kind == 'rotation' else 'Slide'} mate"
        return f"{'First' if side == 'a' else 'Second'} mate"

    def _options(self, side):
        m = self.doc.mate(self.sel[side]) if self.sel[side] else None
        return mate_freedoms(m, self.spec[side]) if m is not None else []

    def _normalise(self):
        """A type with two different kinds (rack and pinion) wants the rotation on a and the translation on b:
        if the mates were picked the other way round, swap them."""
        spec = self.spec
        if spec["a"] == spec["b"] or spec.get("same_mate") or not (self.sel["a"] and self.sel["b"]):
            return
        if not self._options("a") or not self._options("b"):
            swapped = {"a": self.sel["b"], "b": self.sel["a"]}
            ma, mb = self.doc.mate(swapped["a"]), self.doc.mate(swapped["b"])
            if ma is not None and mb is not None and mate_freedoms(ma, spec["a"]) and mate_freedoms(mb, spec["b"]):
                self.sel = swapped
                self.dof_choice = {"a": None, "b": None}

    def _refresh(self):
        problems = []
        for side in ("a", "b"):
            f = self.fields[side]
            f.findChild(QLabel).setText(self._side_label(side) + f" ({self.spec[side]})")
            mid = self.sel[side]
            m = self.doc.mate(mid) if mid else None
            if m is None:
                self.sel[side] = None
                f.text.setText("Click to select" if f is self.active else "—")
                f.text.setStyleSheet("color:#8a94a3;")
            else:
                f.text.setText(m["name"])
                f.text.setStyleSheet("")
            opts = self._options(side)
            row, combo = self.choosers[side]
            if m is not None and not opts:
                problems.append(f"{m['name']} has no {self.spec[side]} freedom.")
            if [combo.itemData(i) for i in range(combo.count())] != opts:
                combo.blockSignals(True)
                combo.clear()
                for d in opts:
                    combo.addItem(f"{VALUE_LABELS.get(d, d)} ({value_unit(d)})", d)
                combo.blockSignals(False)
            if self.dof_choice[side] not in opts:
                self.dof_choice[side] = opts[0] if opts else None
            if opts:
                combo.blockSignals(True)
                combo.setCurrentIndex(opts.index(self.dof_choice[side]))
                combo.blockSignals(False)
            row.setVisible(len(opts) > 1)
        self._error(" ".join(problems))
        self.adjustSize()
        self.ctl.update_triads()

    def _chosen(self, side):
        d = self.choosers[side][1].currentData()
        if d is not None:
            self.dof_choice[side] = d

    def _clear(self, side):
        self.sel[side] = None
        if self.spec.get("same_mate"):
            self.sel["a"] = self.sel["b"] = None
        self.dof_choice[side] = None
        self.activate(self.fields[side])
        self._refresh()

    def activate(self, field):
        if self.active is not None:
            self.active.set_active(False)
        self.active = field
        field.set_active(True)
        self.ctl.w.viewport.select_filter = {"face", "edge"}
        self._refresh()

    def _active_side(self):
        return "a" if self.active is self.fields["a"] or self.spec.get("same_mate") else "b"

    def pick_mate(self, mid):
        """Put mate `mid` into the active field; when only the other side's kind fits (rack and pinion) it
        goes there instead. Returns True if the mate was taken."""
        m = self.doc.mate(mid)
        if m is None:
            return False
        spec = self.spec
        want = self._active_side()
        sides = [want] if spec.get("same_mate") else [want, "b" if want == "a" else "a"]
        target = next((s for s in sides if mate_freedoms(m, spec[s])), None)
        if target is None:
            kinds = " or ".join(sorted({spec["a"], spec["b"]}))
            self._error(f"{m['name']} has no {kinds} freedom for a {spec['label']} relation.")
            return False
        self.sel[target] = mid
        self.dof_choice[target] = None
        if spec.get("same_mate"):
            self.sel["b"] = mid
            self.dof_choice["b"] = None
        self._normalise()
        other = "b" if target == "a" else "a"
        if not self.sel[other] and not spec.get("same_mate"):
            self.activate(self.fields[other])
        else:
            self._refresh()
        return True

    def handle_pick(self, pick: Pick):
        """A click on a part: take the mate whose connector on that part is nearest the click."""
        if pick.kind not in ("face", "edge", "part"):
            return
        iid = pick.key[0]
        vp = self.ctl.w.viewport
        click = vp.surface_point(*vp.last_click) if vp.last_click else None
        spec = self.spec
        cands = []
        for m in self.doc.mates:
            for side in ("a", "b"):
                c = m.get(side)
                if c and c["inst"] == iid and any(mate_freedoms(m, spec[s]) for s in ("a", "b")):
                    o = np.asarray(self.doc.world_frame(c)["o"], float)
                    d = 0.0 if click is None else float(np.linalg.norm(o - click))
                    cands.append((d, m["id"]))
        if not cands:
            self._error("No mate on that part has a freedom this relation can use.")
            return
        self.pick_mate(min(cands)[1])

    def _error(self, text):
        self.err.setText(text)
        self.err.setVisible(bool(text))
        self.adjustSize()

    # ------------------------------------------------------------------ result
    def build(self):
        """The relation as the fields stand. Raises ValueError, with a message for the user, if it can't be."""
        if not (self.sel["a"] and self.sel["b"]):
            raise ValueError("Pick a mate for each side first." if not self.spec.get("same_mate")
                             else "Pick a cylindrical mate first.")
        text = self.ratio.text().strip()
        v = None
        try:
            v = parse_number(text)
        except Exception:
            pass
        if v is None:
            raise ValueError(f"{self.ratio_label.text()}: can't read \"{text}\" as a number")
        r = copy.deepcopy(self.rel)
        r["name"] = self.title.text().strip() or r["name"]
        r["a"] = {"mate": self.sel["a"], "dof": self.dof_choice["a"]}
        r["b"] = {"mate": self.sel["b"], "dof": self.dof_choice["b"]}
        r["ratio"] = float(v)
        r["reverse"] = self.reverse.isChecked()
        self.doc._check_relation(r)
        return r

    def accept(self):
        try:
            self.result = self.build()
        except ValueError as e:
            self._error(str(e))
            return
        self.accepted.emit()

    def reject(self):
        self.rejected.emit()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.accept()
        elif e.key() == Qt.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(e)


# =========================================================================== precise move

class MoveDialog(QFrame):
    """Move or rotate the selected instances by exact amounts (also works on fixed parts)."""
    accepted = Signal()
    rejected = Signal()

    def __init__(self, ctl, ids):
        super().__init__(ctl.w.viewport)
        _floating(self)
        self.ctl, self.doc, self.ids = ctl, ctl.doc, list(ids)
        self._backup = copy.deepcopy(self.doc.instances)
        self.vals = {"dx": 0.0, "dy": 0.0, "dz": 0.0, "angle": 0.0}
        self.axis = "Z"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(6)
        names = ", ".join(self.doc.instance(i)["name"] for i in self.ids)
        head, self.title = _head(self, "Move", "move", self.accept, self.reject)
        self.title.setReadOnly(True)
        lay.addLayout(head)
        lay.addWidget(_hline())
        lab = QLabel(names)
        lab.setWordWrap(True)
        lab.setObjectName("muted")
        lay.addWidget(lab)
        self.eds = {}
        for key, label, unit in (("dx", "Move X", "mm"), ("dy", "Move Y", "mm"), ("dz", "Move Z", "mm"),
                                 ("angle", "Rotate", "°")):
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            l_ = QLabel(label)
            l_.setFixedWidth(60)
            h.addWidget(l_)
            ed = QLineEdit("0")
            ed.editingFinished.connect(lambda k=key, e=ed: self._num(k, e))
            ed.returnPressed.connect(lambda k=key, e=ed: self._num(k, e))
            h.addWidget(ed, 1)
            h.addWidget(QLabel(unit))
            if key == "angle":
                cb = QComboBox()
                cb.addItems(["about X", "about Y", "about Z"])
                cb.setCurrentIndex(2)
                cb.currentIndexChanged.connect(lambda i: (setattr(self, "axis", "XYZ"[i]), self._preview()))
                h.addWidget(cb)
            self.eds[key] = ed
            lay.addWidget(w)
        note = QLabel("Rotation is about the part's centre. Mated parts follow their mates afterwards.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        lay.addWidget(note)
        self.setFixedWidth(300)
        self.adjustSize()
        self.move(12, 12)
        self.show()
        self.eds["dx"].setFocus()

    def _num(self, key, ed):
        try:
            v = parse_number(ed.text(), angle=(key == "angle"))
            if v is None:
                return
        except Exception:
            return
        self.vals[key] = float(v)
        self._preview()

    def matrix(self):
        lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
        for iid in self.ids:
            s = self.doc.world_shape(iid)
            if s is not None:
                a, b = K.bbox(s)
                lo, hi = np.minimum(lo, a), np.maximum(hi, b)
        c = (lo + hi) / 2 if np.all(np.isfinite(lo)) else np.zeros(3)
        ax = {"X": [1, 0, 0], "Y": [0, 1, 0], "Z": [0, 0, 1]}[self.axis]
        R = A.rot_about(ax, np.radians(self.vals["angle"]), c)
        return A.translation([self.vals["dx"], self.vals["dy"], self.vals["dz"]]) @ R

    def _preview(self):
        self.doc.instances = copy.deepcopy(self._backup)
        M = self.matrix()
        for iid in self.ids:
            inst = self.doc.instance(iid)
            inst["T"] = A.orthonormalize(M @ np.array(inst["T"], float)).tolist()
        self.ctl.show_scene()

    def accept(self):
        for key, ed in self.eds.items():
            self._num(key, ed)
        M = self.matrix()
        self.doc.instances = copy.deepcopy(self._backup)
        self.accepted.emit()
        self.doc.checkpoint()
        for iid in self.ids:
            self.doc.move_instance(iid, M, checkpoint=False, solve=False)
        self.doc.solve(keep=self.ids[0] if len(self.ids) == 1 else None)
        self.doc.notify()

    def reject(self):
        self.doc.instances = copy.deepcopy(self._backup)
        self.rejected.emit()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.accept()
        elif e.key() == Qt.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(e)


# =========================================================================== bill of materials

class BomDialog(QDialog):
    def __init__(self, parent, doc):
        super().__init__(parent)
        self.setWindowTitle(f"Bill of materials — {doc.name}")
        self.resize(720, 380)
        self.rows = doc.bom()
        lay = QVBoxLayout(self)
        self.total = doc.bom_total(self.rows)
        t = QTableWidget(len(self.rows) + 1, 7)
        t.setHorizontalHeaderLabels(["Item", "Part", "Quantity", "From", "Material", "Volume (mm³)", "Mass (g)"])
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        for r, row in enumerate(self.rows):
            vals = [str(r + 1), row["name"], str(row["qty"]), row["source"], row["material"] or "—",
                    fmt(row["volume"], 1) if row["volume"] is not None else "—",
                    fmt(row["mass"], 2) if row["mass"] is not None else "—"]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c in (0, 2, 5, 6):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                it.setToolTip(", ".join(row["instances"]))
                t.setItem(r, c, it)
        for c, v in enumerate(["", "Total", str(self.total["qty"]), "", "", "", fmt(self.total["mass"], 2)]):
            it = QTableWidgetItem(v)
            f = it.font()
            f.setBold(True)
            it.setFont(f)
            if c in (2, 6):
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            t.setItem(len(self.rows), c, it)
        t.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        t.resizeColumnsToContents()
        t.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        lay.addWidget(t)
        total = sum(r["qty"] for r in self.rows)
        lay.addWidget(QLabel(f"{len(self.rows)} different part(s), {total} in total"))
        row = QHBoxLayout()
        row.addStretch(1)
        cp = QPushButton("Copy")
        cp.setToolTip("Copy as a table you can paste into a spreadsheet")
        cp.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.csv("\t")))
        row.addWidget(cp)
        ex = QPushButton("Export CSV…")
        ex.clicked.connect(self._export)
        row.addWidget(ex)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)

    def csv(self, sep=","):
        out = [sep.join(["Item", "Part", "Quantity", "From", "Material", "Volume (mm3)", "Mass (g)"])]
        table = []
        for r, row in enumerate(self.rows):
            v = row["volume"]
            table.append([str(r + 1), row["name"], str(row["qty"]), row["source"], row["material"] or "",
                          f"{v:.1f}" if v is not None else "",
                          f"{row['mass']:.2f}" if row["mass"] is not None else ""])
        table.append(["", "Total", str(self.total["qty"]), "", "", "", f"{self.total['mass']:.2f}"])
        for cells in table:
            if sep == ",":
                cells = ['"%s"' % c.replace('"', '""') if ("," in c or '"' in c) else c for c in cells]
            out.append(sep.join(cells))
        return "\n".join(out) + "\n"

    def _export(self):
        path, _ = filedlg.getSaveFileName(self, "Export bill of materials", "bom.csv", "CSV (*.csv)")
        if path:
            if not path.lower().endswith(".csv"):
                path += ".csv"
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.csv())


# =========================================================================== controller

class AssemblyController:
    """Everything the main window does differently while an Assembly tab is current."""

    def __init__(self, w):
        self.w = w
        self.panel = AssemblyPanel()
        self.insert_panel = None
        self.explode_panel = None
        self.mate_dialog = None
        self.relation_dialog = None
        self.move_dialog = None
        self._drag = None
        self._interf = None          # last interference result (ghost meshes)
        from .gizmo import Gizmo
        self.gizmo = Gizmo(self)     # Onshape-style move / rotate triad on the clicked part
        p = self.panel
        p.instanceClicked.connect(self._instance_clicked)
        p.instanceDouble.connect(self.open_source)
        p.instanceContext.connect(self.instance_menu)
        p.mateClicked.connect(self._mate_clicked)
        p.mateDouble.connect(self.edit_mate)
        p.mateContext.connect(self.mate_menu)
        p.groupClicked.connect(self.select_group)
        p.groupContext.connect(self.group_menu)
        p.relationClicked.connect(self.select_relation)
        p.relationDouble.connect(self.edit_relation)
        p.relationContext.connect(self.relation_menu)
        p.visibilityChanged.connect(self.show_scene)
        from PySide6.QtCore import QObject
        from PySide6.QtGui import QAction
        # parented on the window so it is found by name; tests build this controller on a plain stand-in window
        self.act_edit_in_context = QAction("Edit in context", w if isinstance(w, QObject) else None)
        self.act_edit_in_context.setObjectName("edit_in_context")
        self.act_edit_in_context.triggered.connect(lambda _=False: self.edit_in_context())

    @property
    def doc(self):
        return self.w.doc

    @property
    def vp(self):
        return self.w.viewport

    # ------------------------------------------------------------------ view
    def show_scene(self):
        doc = self.doc
        parts = []
        sketches = []
        exploding = self.explode_panel is not None
        for inst in doc.visible_instances():
            if doc.is_sketch_instance(inst["id"]):
                # edges and points only, no faces: drawn by the viewport's sketch path, not as a part
                T = np.array(inst["T"], float)
                sr = self._sketch_instance_result(inst["id"], T) if self.panel.shows("sketches") else None
                if sr is not None:
                    sketches.append((inst["id"], sr))
                continue
            p = doc.parts[inst["id"]]
            T = doc.display_T(inst["id"], exploded=exploding)
            fc = p.face_colors if not inst.get("color") else None
            parts.append((inst["id"], K.located(p.shape, T), doc.instance_color(inst), fc, T, p.shape))
        self.vp.set_scene(parts, [], sketches)
        self.vp.ghosts = self._interf or []
        self.update_triads()

    def _sketch_instance_result(self, iid, T):
        """A features.SketchResult (no regions) for Sketch instance `iid`, its plane moved by the instance's
        transform `T` so the viewport draws it in the assembly's coordinates; None if the sketch can't be read."""
        from .. import features as F
        from ..sketch import Sketch
        doc = self.doc
        if doc.studio_resolver is None and not hasattr(getattr(doc.resolver, "__self__", None), "resolve_studio"):
            # a loose Assembly resolves through the window, which can't publish a studio: give it the LinkResolver
            doc.studio_resolver = self.w._resolver_for(doc).resolve_studio
        self._wire_connector_resolver()                                               # a sketch's own connectors
        out = doc.sketch_out(iid)
        if out is None:
            return None
        R, t = T[:3, :3], T[:3, 3]
        pl = out.plane
        plane = K.Plane(R @ np.asarray(pl["origin"], float) + t, R @ np.asarray(pl["xdir"], float),
                        R @ np.asarray(pl["normal"], float))
        return F.SketchResult(plane, Sketch.from_dict(out.sketch), [], [])

    def _wire_connector_resolver(self):
        """A loose Assembly resolves through the window, which has no connector lookup: give it the LinkResolver's."""
        doc = self.doc
        make = getattr(self.w, "_resolver_for", None)       # tests drive the controller with a plain stand-in window
        if make is not None and doc.connector_resolver is None \
                and not hasattr(getattr(doc.resolver, "__self__", None), "resolve_connectors"):
            doc.connector_resolver = make(doc).resolve_connectors

    def _own_connector_triads(self, points=None):
        """A triad for every mate connector a visible instance offers (its Part Studio's and its origin one). With a
        list in `points`, each triad's Pick and position are added to it for Select other."""
        doc, out = self.doc, []
        self._wire_connector_resolver()
        for inst in doc.visible_instances():
            for pc in doc.published_connectors(inst["id"]) or []:
                try:
                    fr = doc.world_frame(doc.named_connector(inst["id"], pc["id"]))
                except Exception:           # a connector that can't be placed is simply not drawn
                    continue
                z, x = A.unit(fr["z"]), A.unit(fr["x"])
                out.append({"o": fr["o"], "x": x, "y": np.cross(z, x), "z": z, "color": (0.15, 0.7, 0.75)})
                if points is not None:
                    points.append((Pick("connector", (inst["id"], pc["id"])), fr["o"]))
        return out

    def update_triads(self):
        doc = self.doc
        tri = []
        conns = []
        points = []                          # what Select other may list: only triads that are drawn
        if self.mate_dialog is not None:
            m = self.mate_dialog.mate
            conns = [m.get("a"), m.get("b")]
        elif self.relation_dialog is not None:
            for side in ("a", "b"):
                m = doc.mate(self.relation_dialog.sel[side])
                if m:
                    conns += [m.get("a"), m.get("b")]
        else:
            shown = [m["id"] for m in doc.mates] if self.panel.shows("mates") else self.panel.selected_mates()
            for mid in shown:
                m = doc.mate(mid)
                if m:
                    conns += [m.get("a"), m.get("b")]
        for c in conns:
            if not c or doc.instance(c["inst"]) is None:
                continue
            fr = doc.world_frame(c)
            z = A.unit(fr["z"])
            x = A.unit(fr["x"])
            tri.append({"o": fr["o"], "x": x, "y": np.cross(z, x), "z": z})
        if self.mate_dialog is not None:     # each part's own published connectors, to pick
            for _iid, _pc, fr in self.mate_dialog.own_connectors():
                z = A.unit(fr["z"])
                x = A.unit(fr["x"])
                tri.append({"o": fr["o"], "x": x, "y": np.cross(z, x), "z": z, "color": (0.15, 0.7, 0.75),
                            "size_px": 26, "width": 1.8})
                points.append((Pick("connector", (_iid, _pc["id"])), fr["o"]))
        elif self.panel.shows("connectors"):  # ...and, with no dialog open, each part's connectors, to see
            tri += self._own_connector_triads(points)
        self.vp.triads = tri
        self.vp.pick_points = points
        self.vp.update()

    def on_changed(self):
        if self._interf is not None:
            # parts may have moved: the old overlap display would be wrong
            self._interf = None
            self.vp.ghosts = []
            if hasattr(self.w, "btn_interf"):
                self.w.btn_interf.setChecked(False)
        editing = self.mate_dialog.mate["id"] if self.mate_dialog is not None and not self.mate_dialog.is_new \
            else None
        self.panel.populate(self.doc, editing)
        if self.w.mode == "idle":
            self.show_scene()

    # ------------------------------------------------------------------ inserting
    def toggle_explode(self):
        if self.explode_panel is not None:
            self.close_explode()
            return
        if self.w.mode != "idle":
            return
        self.close_insert()
        self.explode_panel = ExplodePanel(self)
        self.explode_panel.closed.connect(self.close_explode)
        self.show_scene()

    def close_explode(self):
        if self.explode_panel is not None:
            self.explode_panel.hide()
            self.explode_panel.deleteLater()
            self.explode_panel = None
            self.show_scene()

    def toggle_insert(self):
        if self.insert_panel is not None:
            self.close_insert()
            return
        if self.w.mode != "idle":
            return
        self.insert_panel = InsertPanel(self)
        self.w._adapt_tree(self.insert_panel)
        self.insert_panel.closed.connect(self.close_insert)

    def close_insert(self):
        if self.insert_panel is not None:
            self.insert_panel.hide()
            self.insert_panel.deleteLater()
            self.insert_panel = None

    def insert_studio_parts(self, uid, part_ids=None, config=None):
        studio = self.w.studio_by_uid(uid)
        if studio is None:
            return
        doc = self.doc
        st = studio.state_for(config) if config else studio.state
        pids = part_ids or [p.id for p in st.parts.values() if not p.reference]
        if not pids:
            return
        doc.checkpoint()
        first_new = []
        # a whole studio keeps its parts where they sit relative to each other
        shift = None
        if part_ids is None and doc.instances:
            lo, hi = doc.bounds()
            slo, shi = np.full(3, np.inf), np.full(3, -np.inf)
            for pid in pids:
                a, b = K.bbox(st.parts[pid].shape)
                slo, shi = np.minimum(slo, a), np.maximum(shi, b)
            gap = max(0.15 * float(np.linalg.norm(shi - slo)), 5.0)
            shift = A.translation([hi[0] + gap - slo[0], 0, lo[2] - slo[2]])
        for pid in pids:
            src = self.w.studio_link(studio, pid)
            if config:
                src["config"] = config
            try:
                T = shift if part_ids is None and doc.instances and shift is not None else None
                if part_ids is None and not doc.instances:
                    T = np.eye(4)
                iid = doc.insert(src, T=T, checkpoint=False)
                first_new.append(iid)
            except K.KernelError as e:
                QMessageBox.warning(self.w, "Insert", str(e))
        if part_ids is None and len(first_new) > 1 and not any(
                doc.instance(i).get("fixed") for i in first_new[1:]):
            pass
        self.w.status.showMessage(f"Inserted {len(first_new)} instance(s)", 4000)
        QTimer.singleShot(0, lambda: self.vp.fit())

    def insert_studio_sketch(self, uid, sketch_id):
        """Insert one Sketch instance of sketch `sketch_id` of the Part Studio `uid` (one undo checkpoint).
        Returns the new instance id, or None when the studio or sketch is not there."""
        studio = self.w.studio_by_uid(uid)
        if studio is None or sketch_id not in studio.state.sketches:
            return None
        src = self.w.studio_link(studio, None)
        # inside a Document the source names the tab (like a part's); loose studios carry uid + path
        src = {**{k: v for k, v in src.items() if k != "part"}, "kind": "sketch", "sketch": sketch_id}
        doc = self.doc
        first = not doc.instances
        try:
            iid = doc.insert(src, T=np.eye(4) if first else None)
        except K.KernelError as e:
            QMessageBox.warning(self.w, "Insert", str(e))
            return None
        self.w.status.showMessage("Inserted 1 sketch instance", 4000)
        QTimer.singleShot(0, lambda: self.vp.fit())
        return iid

    def insert_document_sources(self, sources):
        """Insert one instance per cross-Document source (a part pinned to a Version of another Document)."""
        n = 0
        self.doc.checkpoint()
        for src in sources:
            try:
                self.doc.insert(src, checkpoint=False)
                n += 1
            except K.KernelError as e:
                QMessageBox.warning(self.w, "Insert", str(e))
        if n:
            self.w.status.showMessage(f"Inserted {n} instance(s)", 4000)
            QTimer.singleShot(0, lambda: self.vp.fit())
        return n

    def insert_step(self, path):
        import base64
        from .progress import ImportProgress, read_step_in_background
        with open(path, "rb") as fh:
            raw = fh.read()
        data = base64.b64encode(raw).decode()
        doc = self.doc
        key = doc.add_step_blob(data)
        prog = ImportProgress(self.w, f"Inserting {os.path.basename(path)}")
        try:
            if not read_step_in_background(raw, prog, self.w):
                return 0
            prog.stage("Building the parts…")
            sols = doc._step_parts({"blob": key})
        except Exception as e:
            prog.close()
            prog = None
            QMessageBox.warning(self.w, "Insert", f"Couldn't read {os.path.basename(path)}:\n{e}")
            return 0
        finally:
            if prog is not None:
                prog.close()
        doc.checkpoint()
        empty = not doc.instances
        shift = None
        if not empty:
            lo, hi = doc.bounds()
            slo, shi = np.full(3, np.inf), np.full(3, -np.inf)
            for s, *_ in sols:
                a, b = K.bbox(s)
                slo, shi = np.minimum(slo, a), np.maximum(shi, b)
            gap = max(0.15 * float(np.linalg.norm(shi - slo)), 5.0)
            shift = A.translation([hi[0] + gap - slo[0], 0, lo[2] - slo[2]])
        n = 0
        for i in range(len(sols)):
            src = {"kind": "step", "blob": key, "index": i, "filename": os.path.basename(path)}
            doc.insert(src, T=np.eye(4) if empty else shift, checkpoint=False)
            n += 1
        QTimer.singleShot(0, lambda: self.vp.fit())
        return n

    # ------------------------------------------------------------------ mates
    def start_mate(self, mtype, edit_mid=None):
        w = self.w
        if w.mode != "idle":
            if self.mate_dialog is not None and self.mate_dialog.is_new and edit_mid is None:
                self.mate_dialog.set_type(mtype)
            return
        if len([i for i in self.doc.instances if i["id"] in self.doc.parts]) < 2:
            w.status.showMessage("Insert at least two parts before adding a mate", 5000)
            return
        self.close_insert()
        if edit_mid:
            m = self.doc.mate(edit_mid)
            is_new, pre = False, []
        else:
            m = self.doc.make_mate(mtype)
            is_new, pre = True, list(self.vp.selection)
        w.mode = "dialog"
        self.mate_dialog = MateDialog(self, m, is_new, pre)
        w._adapt_tree(self.mate_dialog)
        self.mate_dialog.accepted.connect(self._mate_accepted)
        self.mate_dialog.rejected.connect(self._mate_rejected)
        self.vp.pick_handler = self.mate_dialog.handle_pick
        self.vp.snap_provider = self.mate_dialog.snap_candidates
        self.vp.miss_handler = self.mate_dialog.handle_miss
        self.panel.populate(self.doc, None if is_new else m["id"])

    def edit_mate(self, mid):
        if self.doc.pattern(mid) is not None:        # an instance pattern, listed with the mates
            self.pattern_dialog(edit=mid)
            return
        m = self.doc.mate(mid)
        if m is not None and self.w.mode == "idle":
            self.start_mate(m["type"], mid)

    # ------------------------------------------------------------------ instance patterns
    def pattern_dialog(self, ptype="linear", edit=None):
        from PySide6.QtWidgets import QDialog, QFormLayout, QDialogButtonBox, QDoubleSpinBox, QSpinBox
        doc = self.doc
        pat = doc.pattern(edit) if edit else None
        seeds = pat["seeds"] if pat else self.selected_instance_ids()
        seeds = [s_ for s_ in seeds if doc.instance(s_) is not None and not doc.instance(s_).get("pattern")]
        if not seeds:
            QMessageBox.information(self.w, "Pattern", "Select the parts to pattern first (in the list or the view).")
            return
        ptype = pat["type"] if pat else ptype
        dlg = QDialog(self.w)
        dlg.setWindowTitle(("Linear" if ptype == "linear" else "Circular") + " pattern")
        form = QFormLayout(dlg)
        form.addRow(QLabel(", ".join(doc.instance(s_)["name"] for s_ in seeds)))
        axis = QComboBox()
        for lab, ax in (("X axis", {"kind": "world", "name": "X"}), ("Y axis", {"kind": "world", "name": "Y"}),
                        ("Z axis", {"kind": "world", "name": "Z"})):
            axis.addItem(lab, ax)
        for m in doc.mates:
            if m["type"] in ("revolute", "cylindrical", "slider"):
                axis.addItem(f"Axis of {m['name']}", {"kind": "mate", "mate": m["id"]})
        form.addRow("Direction:" if ptype == "linear" else "Round:", axis)
        flip = QCheckBox("Opposite direction")
        form.addRow("", flip)
        count = QSpinBox()
        count.setRange(2, 1000)
        count.setValue(pat["count"] if pat else (3 if ptype == "linear" else 6))
        form.addRow("Count (with the original):", count)
        amt = QDoubleSpinBox()
        amt.setRange(-100000, 100000)
        amt.setDecimals(3)
        if ptype == "linear":
            amt.setSuffix(" mm")
            amt.setValue(pat["spacing"] if pat else 20.0)
            form.addRow("Spacing:", amt)
        else:
            amt.setSuffix("°")
            amt.setValue(pat["angle"] if pat else 360.0)
            form.addRow("Angle (360 = all the way round):", amt)
        if pat:
            for i in range(axis.count()):
                if axis.itemData(i) == {k_: v for k_, v in pat["axis"].items() if k_ != "flip"}:
                    axis.setCurrentIndex(i)
            flip.setChecked(bool(pat["axis"].get("flip")))
        elif ptype == "circular":
            axis.setCurrentIndex(2)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() != QDialog.Accepted:
            return
        ax = dict(axis.currentData())
        if flip.isChecked():
            ax["flip"] = True
        kw = {"count": count.value(), "axis": ax}
        kw["spacing" if ptype == "linear" else "angle"] = amt.value()
        try:
            if pat:
                doc.edit_pattern(pat["id"], **kw)
            else:
                doc.add_pattern(ptype, seeds, **kw)
        except K.KernelError as e:
            QMessageBox.warning(self.w, "Pattern", str(e))

    def _close_mate(self):
        d = self.mate_dialog
        if d is not None:
            d.hide()
            d.deleteLater()
        self.mate_dialog = None
        self.w.mode = "idle"
        vp = self.vp
        vp.pick_handler = None
        vp.miss_handler = None
        vp.snap_provider = None
        vp.clear_snaps()
        vp.select_filter = None
        vp.clear_selection()
        return d

    def _mate_accepted(self):
        d = self._close_mate()
        m = d.mate
        if d.is_new:
            self.doc.add_mate(m)
        else:
            self.doc.replace_mate(m)
        self.apply_limits(m["id"], d.limits)
        err = self.doc.errors.get(m["id"])
        self.w.status.showMessage(f"{m['name']}: {err}" if err else f"{m['name']} added" if d.is_new
                                  else f"{m['name']} updated", 6000)

    def apply_limits(self, mid, limits):
        """Store the dialog's limits with set_limits, one call per freedom that changed."""
        mt = self.doc.mate(mid)
        for dof, (lo, hi) in (limits or {}).items():
            cur = (mt.get("limits") or {}).get(dof) or (None, None)
            if (lo, hi) == tuple(cur):
                continue
            try:
                self.doc.set_limits(mid, dof, lo, hi)
            except ValueError as e:
                self.w.status.showMessage(str(e), 6000)

    def _mate_rejected(self):
        self._close_mate()
        self.on_changed()

    # ------------------------------------------------------------------ relations
    def _mate_clicked(self, mid):
        if self.relation_dialog is not None:
            self.relation_dialog.pick_mate(mid)
        self.update_triads()

    def start_relation(self, rtype, edit_rid=None):
        w = self.w
        if w.mode != "idle":
            if self.relation_dialog is not None and self.relation_dialog.is_new and edit_rid is None:
                self.relation_dialog.set_type(rtype)
            return
        if not self.doc.mates:
            w.status.showMessage("Add a mate before adding a relation", 5000)
            return
        self.close_insert()
        if edit_rid:
            r = self.doc.relation(edit_rid)
            if r is None:
                return
            is_new = False
        else:
            r, is_new = self.doc.make_relation(rtype), True
        w.mode = "dialog"
        self.relation_dialog = RelationDialog(self, r, is_new)
        w._adapt_tree(self.relation_dialog)
        self.relation_dialog.accepted.connect(self._relation_accepted)
        self.relation_dialog.rejected.connect(self._relation_rejected)
        self.vp.pick_handler = self.relation_dialog.handle_pick
        if is_new:                                   # mates already selected in the list fill the fields
            for mid in self.panel.selected_mates()[:2]:
                self.relation_dialog.pick_mate(mid)

    def edit_relation(self, rid):
        r = self.doc.relation(rid)
        if r is not None and self.w.mode == "idle":
            self.start_relation(r["type"], rid)

    def select_relation(self, rid):
        """Highlight a relation's two mates (their connector triads), as the relation dialog does."""
        r = self.doc.relation(rid)
        if r is None:
            return
        self.panel.select_mates({r[s]["mate"] for s in ("a", "b") if r.get(s)})
        self.vp.clear_selection()
        self.update_triads()

    def rename_relation(self, rid, name=None):
        # the model's rename() does not know relations; edit behind a checkpoint, as rename_group does
        from PySide6.QtWidgets import QInputDialog
        r = self.doc.relation(rid)
        if r is None:
            return
        if name is None:
            name, ok = QInputDialog.getText(self.w, "Rename relation", "Name:", text=r["name"])
            if not ok:
                return
        name = name.strip()
        if name and name != r["name"]:
            self.doc.checkpoint()
            r["name"] = name
            self.doc.notify()

    def suppress_relation(self, rid, flag):
        if self.doc.relation(rid) is not None:
            self.doc.set_relation_suppressed(rid, flag)

    def delete_relation(self, rid):
        if self.doc.relation(rid) is not None:
            self.doc.delete_relation(rid)

    def relation_menu(self, rid, gpos):
        from PySide6.QtWidgets import QMenu
        r = self.doc.relation(rid)
        if r is None or self.w.mode != "idle":
            return
        m = QMenu(self.w)
        m.addAction("Edit…", lambda: self.edit_relation(rid))
        m.addAction("Rename…", lambda: self.rename_relation(rid))
        sup = bool(r.get("suppressed"))
        m.addAction("Unsuppress" if sup else "Suppress", lambda: self.suppress_relation(rid, not sup))
        m.addSeparator()
        m.addAction("Delete", lambda: self.delete_relation(rid))
        m.exec(gpos)

    def _close_relation(self):
        d = self.relation_dialog
        if d is not None:
            d.hide()
            d.deleteLater()
        self.relation_dialog = None
        self.w.mode = "idle"
        vp = self.vp
        vp.pick_handler = None
        vp.select_filter = None
        vp.clear_selection()
        return d

    def _relation_accepted(self):
        d = self._close_relation()
        r = d.result
        try:
            if d.is_new:
                self.doc.add_relation(r)
            else:
                self.doc.replace_relation(r)
        except ValueError as e:
            self.w.status.showMessage(str(e), 6000)
        else:
            self.w.status.showMessage(f"{r['name']} added" if d.is_new else f"{r['name']} updated", 6000)
        self.update_triads()

    def _relation_rejected(self):
        self._close_relation()
        self.on_changed()

    # ------------------------------------------------------------------ moving
    def start_move(self, ids=None):
        ids = ids or self.selected_instance_ids()
        if not ids:
            self.w.status.showMessage("Select a part (in the list or the graphics area) to move it", 5000)
            return
        if self.w.mode != "idle":
            return
        self.close_insert()
        self.w.mode = "dialog"
        self.move_dialog = MoveDialog(self, ids)
        self.w._adapt_tree(self.move_dialog)
        self.move_dialog.accepted.connect(self._close_move)
        self.move_dialog.rejected.connect(lambda: (self._close_move(), self.on_changed()))

    def _close_move(self):
        if self.move_dialog is not None:
            self.move_dialog.hide()
            self.move_dialog.deleteLater()
        self.move_dialog = None
        self.w.mode = "idle"

    def selected_instance_ids(self):
        ids = list(self.panel.selected_instances())
        for pk in self.vp.selection:
            if pk.kind in ("face", "edge", "part") and pk.key[0] not in ids:
                ids.append(pk.key[0])
        return [i for i in ids if self.doc.instance(i) is not None]

    # dragging (the viewport calls these) ---------------------------------
    def press(self, x, y, e):
        if self.w.mode != "idle" or self.explode_panel is not None:
            return False                      # (parts aren't dragged while the exploded view is shown)
        gz = self.gizmo
        h = gz.hit(x, y)
        if h is not None:
            inst = self.doc.instance(gz.inst)
            if h[0] != "label" and inst.get("fixed"):
                self.w.status.showMessage("This part is fixed — right-click it and choose Unfix to move it", 4000)
                self._drag = {"gizmo": "blocked", "moved": False}
                return True
            if h[0] == "label":
                self._drag = {"gizmo": "label", "moved": False}
                return True
            if gz.start(h, x, y):
                self._drag = {"gizmo": h, "moved": False, "snap": gz.drag["snap"]}
                return True
            return False
        pk = self.vp.pick(x, y, kinds={"face"})
        if pk is None:
            gz.clear()
            return False
        iid = pk.key[0]
        inst = self.doc.instance(iid)
        if inst is None:
            return False
        p = self.vp.surface_point(x, y)
        if p is None:
            return False
        T = np.array(inst["T"], float)
        Ti = np.linalg.inv(T)
        cam = self.vp.cam
        self._drag = {"inst": iid, "local": Ti[:3, :3] @ p + Ti[:3, 3], "plane_o": p, "n": cam.back.copy(),
                      "P": np.array([cam.right, cam.up]), "snap": self.doc._snapshot(), "moved": False,
                      "fixed": bool(inst.get("fixed")), "start_T": T}
        return True

    def hover(self, x, y):
        """Mouse over the triad? (highlights the handle; the viewport then skips its own hover)."""
        gz = self.gizmo
        h = gz.hit(x, y) if self.w.mode == "idle" else None
        if h != gz.hover:
            gz.hover = h
            self.vp.update()
        return h is not None

    def draw_overlay(self, p):
        self.gizmo.draw(p)

    def _moves_with(self, iid):
        """The instances that move rigidly with `iid`: its mate-connected instances and its Group's members,
        closed over both."""
        doc = self.doc
        group = {iid}
        while True:
            more = set(group)
            for i in group:
                more |= doc._groups()[i]
                g = doc.group_of(i)
                if g is not None:
                    more |= set(g["instances"])
            if more == group:
                return group
            group = more

    def apply_motion(self, iid, M, T0s, local_pts, exact=False, turn=False):
        """Move instance `iid` by the world motion M (from the poses T0s), as far as its mates allow:
        a part with no mates, or a mated group with nothing fixed, moves exactly (the whole group together);
        otherwise the solver moves it within its mates' freedom."""
        doc = self.doc
        group = self._moves_with(iid)
        if not any(doc.instance(i).get("fixed") for i in group):
            for i in group:
                doc.instance(i)["T"] = A.orthonormalize(M @ T0s[i]).tolist()
        else:
            T0 = T0s[iid]
            if exact:
                for i in doc.instances:
                    i["T"] = T0s[i["id"]].tolist()
            o = np.asarray(local_pts[0], float)
            if turn:
                # turning: aim for the new orientation and let the mates decide where the part sits
                # (a hinged part turns about its hinge, wherever the triad is)
                dirs = [(lp - o, (M @ T0)[:3, :3] @ (lp - o)) for lp in map(np.asarray, local_pts[1:])]
                drag = {"inst": iid, "dirs": dirs}
            else:
                drag = {"inst": iid, "points": [(o, (M @ T0 @ np.append(o, 1.0))[:3])]}
            doc.solve(drag=drag, iters=40 if exact else 10)
        self.show_scene()

    def typed_value_applied(self):
        doc = self.doc
        doc.solve()
        doc.dirty = True
        doc.notify()

    def move(self, x, y, e):
        d = self._drag
        if d is None:
            return
        if "gizmo" in d:
            if d["gizmo"] not in ("blocked", "label"):
                d["moved"] = True
                self.gizmo.move(x, y)
            return
        if d["fixed"]:
            self.w.status.showMessage("This part is fixed — right-click it and choose Unfix to drag it", 4000)
            return
        o, dr = self.vp.cam.ray(x, y)
        denom = dr @ d["n"]
        if abs(denom) < 1e-12:
            return
        t = ((d["plane_o"] - o) @ d["n"]) / denom
        target = o + t * dr
        d["moved"] = True
        doc = self.doc
        group = self._moves_with(d["inst"])
        if not any(doc.instance(i).get("fixed") for i in group):
            # nothing in this group is fixed: slide the whole group together, like Onshape — the mates
            # between them stay exactly as they are and nothing spins round
            T = np.array(doc.instance(d["inst"])["T"], float)
            delta = target - (T[:3, :3] @ d["local"] + T[:3, 3])
            for i in group:
                Ti = np.array(doc.instance(i)["T"], float)
                Ti[:3, 3] += delta
                doc.instance(i)["T"] = Ti.tolist()
        else:
            doc.solve(drag={"inst": d["inst"], "local": d["local"], "target": target, "P": d["P"]}, iters=8)
        self.show_scene()

    def release(self, x, y, e, moved):
        d, self._drag = self._drag, None
        if d is None:
            return
        doc = self.doc
        if "gizmo" in d:
            gd = self.gizmo.finish()
            if d["gizmo"] == "label" and not moved:
                self.gizmo.open_editor()
            if gd is not None and d["moved"] and gd["moved"]:
                doc.solve()
                doc._undo.append(d["snap"])
                doc._redo.clear()
                doc.dirty = True
                doc.notify()
                # ready for typing: the value is selected, so a number + Enter sets it exactly
                self.gizmo.open_editor()
            self.vp.update()
            return
        if not moved or not d["moved"]:
            # a plain click on a part: put the move / rotate triad where it was clicked
            self.gizmo.place(d["inst"], d["plane_o"])
            return
        doc.solve()
        doc._undo.append(d["snap"])
        doc._redo.clear()
        doc.dirty = True
        doc.notify()

    # ------------------------------------------------------------------ menus
    def _instance_clicked(self, iid):
        if self.w.mode == "idle":
            self.vp.clear_selection()
            self.update_triads()
            if self.doc.instance(iid) is not None and not self.doc.instance(iid).get("hidden"):
                lo, hi = K.bbox(self.doc.world_shape(iid))
                self.gizmo.place(iid, (lo + hi) / 2)

    def instance_menu(self, iid, gpos, other=False):
        from PySide6.QtWidgets import QMenu
        doc = self.doc
        inst = doc.instance(iid)
        if inst is None or self.w.mode != "idle":
            return
        ids = self.selected_instance_ids()
        if iid not in ids:
            ids = [iid]
        m = QMenu(self.w)
        if other:                            # a right-click in the viewport (the Instances list passes no point there)
            self._add_select_other(m, gpos)
        m.addAction("Rename…", lambda: self.rename(iid))
        m.addAction("Unfix" if inst.get("fixed") else "Fix", lambda: doc.set_fixed(iid, not inst.get("fixed")))
        m.addAction("Show" if inst.get("hidden") else "Hide",
                    lambda: self.hide_instances([iid], not inst.get("hidden")))
        m.addAction("Isolate", lambda: self.isolate(iid))
        sup = bool(inst.get("suppressed"))
        m.addAction("Unsuppress" if sup else "Suppress", lambda: self.suppress_instances([iid], not sup))
        m.addAction("Change colour…", lambda: self.recolor(iid))
        if inst.get("color"):
            m.addAction("Use the part's colour", lambda: doc.set_color(iid, None))
        m.addSeparator()
        a = m.addAction("Add mate connector to instance origin", lambda: doc.add_instance_origin_connector(iid))
        a.setObjectName("add_connector_instance_origin")
        m.addAction("Move / rotate…", lambda: self.start_move(ids))
        ga = m.addAction("Group", lambda: self.group_selected(ids))
        ga.setEnabled(self.can_group(ids))
        m.addAction("Duplicate", lambda: self.duplicate(ids))
        if inst["source"].get("kind") == "studio":
            m.addAction("Open Part Studio", lambda: self.open_source(iid))
            if ids != [iid]:
                self.vp.set_selection([Pick("part", (iid,))])    # Edit in context acts on the selection
            m.addAction(self.act_edit_in_context)
        self.add_standard_actions(m, ids)
        a = m.addAction("Mass properties", lambda: self.w._mass_props_shape(
            inst["name"], doc.world_shape(iid), inst.get("material")))
        a.setObjectName("mass_properties")
        if doc.is_sketch_instance(iid):
            a.setEnabled(False)                              # a sketch has no mass
            a.setToolTip("A sketch instance has no mass")
        dof = doc.instance_dof(iid)
        a = m.addAction(f"Degrees of freedom: {dof}" + (" (fixed)" if inst.get("fixed") else ""))
        a.setEnabled(False)
        m.addSeparator()
        m.addAction("Delete", lambda: self.delete_instances(ids))
        m.exec(gpos)

    # groups ------------------------------------------------------------------
    def can_group(self, ids=None):
        """Group needs two or more instances, none of them already in a group (as in FreeCAD, where a part
        belongs to one container)."""
        ids = list(dict.fromkeys(self.selected_instance_ids() if ids is None else ids))
        return len(ids) >= 2 and all(self.doc.group_of(i) is None for i in ids)

    def group_selected(self, ids=None):
        ids = self.selected_instance_ids() if ids is None else ids
        if not self.can_group(ids):
            return None
        return self.doc.add_group(ids)

    def ungroup(self, gid):
        if any(g["id"] == gid for g in self.doc.groups):
            self.doc.delete_group(gid)

    def rename_group(self, gid, name=None):
        from PySide6.QtWidgets import QInputDialog
        g = next((g for g in self.doc.groups if g["id"] == gid), None)
        if g is None:
            return
        if name is None:
            name, ok = QInputDialog.getText(self.w, "Rename group", "Name:", text=g["name"])
            if not ok:
                return
        name = name.strip()
        if name and name != g["name"]:
            self.doc.checkpoint()
            g["name"] = name
            self.doc.notify()

    def select_group(self, gid):
        g = next((g for g in self.doc.groups if g["id"] == gid), None)
        if g is None:
            return
        self.panel.select_instances(set(g["instances"]))
        self.vp.clear_selection()
        self.update_triads()

    def group_menu(self, gid, gpos):
        from PySide6.QtWidgets import QMenu
        if self.w.mode != "idle":
            return
        m = QMenu(self.w)
        m.addAction("Rename…", lambda: self.rename_group(gid))
        m.addAction("Delete (ungroup)", lambda: self.ungroup(gid))
        m.exec(gpos)

    def add_standard_actions(self, menu, ids):
        """`Edit standard part…` and `Accept plugin update` for the Standard parts among `ids`. Returns the actions."""
        doc = self.doc
        std = [i for i in ids if (doc.instance(i) or {}).get("source", {}).get("kind") == "standard"]
        out = []
        if not std:
            return out
        out.append(menu.addAction("Edit standard part…", lambda: self.edit_standard_parts(std)))
        marked = [i for i in std if i in doc.standard_notices()]
        if marked:
            out.append(menu.addAction("Accept plugin update", lambda: self.accept_plugin_update(marked)))
        return out

    def standard_dialog(self, ids):
        """A StandardPartEditDialog for `ids` whose result is reported in the status bar (not shown or run)."""
        from .standard_parts_ui import StandardPartEditDialog
        dlg = StandardPartEditDialog(self.doc, ids, parent=self.w)
        dlg.applied.connect(lambda res, d=dlg: self.w.status.showMessage(d.message, 8000))
        return dlg

    def edit_standard_parts(self, ids):
        dlg = self.standard_dialog(ids)
        dlg.exec()

    def accept_plugin_update(self, ids):
        done = self.doc.acknowledge_standard_versions(ids)
        self.w.status.showMessage(f"Accepted the plugin update on {len(done)} standard part(s)", 5000)

    def mate_menu(self, mid, gpos):
        from PySide6.QtWidgets import QMenu
        doc = self.doc
        mt = doc.mate(mid)
        if mt is None and self.w.mode == "idle" and doc.pattern(mid) is not None:
            m = QMenu(self.w)
            m.addAction("Edit…", lambda: self.edit_mate(mid))
            m.addAction("Rename…", lambda: self.rename(mid))
            m.addSeparator()
            m.addAction("Delete", lambda: doc.delete_mates([mid]))
            m.exec(gpos)
            return
        if mt is None or self.w.mode != "idle":
            return
        ids = self.panel.selected_mates()
        if mid not in ids:
            ids = [mid]
        m = QMenu(self.w)
        m.addAction("Edit…", lambda: self.edit_mate(mid))
        m.addAction("Rename…", lambda: self.rename(mid))
        m.addAction("Unsuppress" if mt.get("suppressed") else "Suppress",
                    lambda: doc.set_suppressed(mid, not mt.get("suppressed")))
        if A.MATE_VALUE_DOFS.get(mt["type"]):
            m.addAction("Set value…", lambda: self.set_value_dialog(mid))
        if A.MATE_VALUE_DOFS.get(mt["type"]):
            m.addAction("Animate…", lambda: self.start_animate(mid))
        limited = [r for r in limit_rows(mt) if r[3] is not None or r[4] is not None]
        if limited:
            sub = m.addMenu("Apply limit position")
            for dof, label, unit, lo, hi in limited:
                for end, bound in (("Min", lo), ("Max", hi)):
                    if bound is not None:
                        sub.addAction(f"{label} {end} ({fmt(bound)} {unit})",
                                      lambda d=dof, b=bound: self.set_mate_value(mid, d, b))
        m.addSeparator()
        m.addAction("Delete", lambda: doc.delete_mates(ids))
        err = doc.errors.get(mid)
        if err:
            m.addSeparator()
            a = m.addAction("⚠ " + err)
            a.setEnabled(False)
        m.exec(gpos)

    def set_value_dialog(self, mid):
        """Ask for the freedom (when there are several) and a number, then drive the mate there."""
        from PySide6.QtWidgets import QInputDialog
        mt = self.doc.mate(mid)
        rows = value_rows(mt) if mt else []
        if not rows:
            return
        row = rows[0]
        if len(rows) > 1:
            names = [f"{r[1]} ({r[3]})" for r in rows]
            pick, ok = QInputDialog.getItem(self.w, "Set value", "Freedom:", names, 0, False)
            if not ok:
                return
            row = rows[names.index(pick)]
        dof, label, text, unit = row
        v, ok = QInputDialog.getDouble(self.w, "Set value", f"{label} ({unit}):", float(text), -1e6, 1e6, 3)
        if ok:
            self.set_mate_value(mid, dof, v)

    animate_dialog = None

    def start_animate(self, mid):
        mt = self.doc.mate(mid)
        if mt is None or not A.MATE_VALUE_DOFS.get(mt["type"]) or self.w.mode != "idle":
            return
        self.close_insert()
        self.w.mode = "dialog"
        self.animate_dialog = AnimateDialog(self, mid)
        self.w._adapt_tree(self.animate_dialog)
        self.animate_dialog.finished.connect(self._close_animate)

    def _close_animate(self):
        d = self.animate_dialog
        if d is not None:
            d.hide()
            d.deleteLater()
        self.animate_dialog = None
        self.w.mode = "idle"
        self.on_changed()

    def set_mate_value(self, mid, dof, value):
        """Drive freedom `dof` of mate `mid` to `value`; a failure goes to the status bar. Returns (reached, ok)."""
        mt = self.doc.mate(mid)
        name = mt["name"] if mt else mid
        try:
            reached, ok = self.doc.drive_mate(mid, dof, float(value))
        except ValueError as e:
            self.w.status.showMessage(str(e), 6000)
            return None, False
        if not ok:
            self.w.status.showMessage(
                f"{name}: stopped at {fmt(reached)} {value_unit(dof)}, it can't reach {fmt(float(value))}", 8000)
        return reached, ok

    def row_label(self, pk):
        """Select other's row text for what only the Assembly names: an instance as the Instances list shows it,
        or 'Mate connector <name> of <instance>'. None leaves the generic 'Face of <instance>'."""
        if pk.kind not in ("part", "connector"):
            return None
        inst = self.doc.instance(pk.key[0])
        if inst is None:
            return None
        if pk.kind == "part":
            return inst["name"]
        cname = next((c["name"] for c in self.doc.published_connectors(pk.key[0]) or []
                      if str(c["id"]) == str(pk.key[1])), "")
        return f"Mate connector {cname} of {inst['name']}" if cname else f"Mate connector of {inst['name']}"

    def _add_select_other(self, m, gpos):
        """'Select other…' on menu `m` when two or more things are under the right-click."""
        lp = self.vp.mapFromGlobal(gpos)
        if len(self.vp.pick_all(lp.x(), lp.y())) >= 2:
            m.addAction("Select other\u2026", lambda: self.vp.open_select_other(lp.x(), lp.y()))
            m.addSeparator()

    def viewport_menu(self, pk, gpos):
        from PySide6.QtWidgets import QMenu
        if pk is not None and pk.kind in ("face", "edge") and self.doc.instance(pk.key[0]) is not None:
            self.panel.select_instances({pk.key[0]})
            self.instance_menu(pk.key[0], gpos, other=True)
            return
        m = QMenu(self.w)
        self._add_select_other(m, gpos)
        m.addAction("Insert parts…", self.toggle_insert)
        m.addAction("Mass properties", lambda: self.w._mass_props_assembly(self.doc))
        m.addAction("Zoom to fit", lambda: self.vp.fit())
        vm = m.addMenu("Views")
        for name in ("Front", "Back", "Left", "Right", "Top", "Bottom", "Isometric"):
            vm.addAction(name, lambda n=name: self.vp.set_standard_view(n))
        if any(i.get("hidden") for i in self.doc.instances):
            m.addAction("Show all", self.show_all)
        m.exec(gpos)

    def rename(self, obj_id):
        from PySide6.QtWidgets import QInputDialog
        o = self.doc.instance(obj_id) or self.doc.mate(obj_id)
        name, ok = QInputDialog.getText(self.w, "Rename", "Name:", text=o["name"])
        if ok and name.strip():
            self.doc.rename(obj_id, name.strip())

    def recolor(self, iid):
        from PySide6.QtWidgets import QColorDialog
        inst = self.doc.instance(iid)
        c = QColorDialog.getColor(QColor.fromRgbF(*self.doc.instance_color(inst)), self.w, "Instance colour")
        if c.isValid():
            self.doc.set_color(iid, [c.redF(), c.greenF(), c.blueF()])

    def isolate(self, iid):
        self.doc.isolate(iid)

    def _need_selection(self):
        """The selected instance ids, or [] with the usual status message when nothing is selected."""
        ids = self.selected_instance_ids()
        if not ids:
            self.w.status.showMessage("Select a part first (in the list or the graphics area)", 4000)
        return ids

    def suppress_instances(self, ids, flag):
        """Suppress (flag True) or unsuppress instances; the menu and Search tools both call this."""
        for iid in ids:
            if self.doc.instance(iid) is not None:
                self.doc.set_instance_suppressed(iid, flag)

    def suppress_selected(self, flag=True):
        ids = [i for i in self._need_selection() if bool(self.doc.instance(i).get("suppressed")) != flag]
        if ids:
            self.suppress_instances(ids, flag)

    def hide_instances(self, ids, flag=True):
        """Hide (or show) instances as one undo step; the menu and Search tools both call this."""
        ids = [i for i in ids if self.doc.instance(i) is not None]
        if not ids:
            return
        self.doc.checkpoint()
        for iid in ids:
            self.doc.instance(iid)["hidden"] = bool(flag)
        self.doc.notify()

    def hide_selected(self):
        ids = self._need_selection()
        if ids:
            self.hide_instances(ids, True)

    def isolate_selected(self):
        ids = self._need_selection()
        if len(ids) == 1:
            self.isolate(ids[0])
        elif ids:
            self.doc.checkpoint()
            for i in self.doc.instances:
                i["hidden"] = i["id"] not in ids
            self.doc.notify()

    def animate_selected(self):
        """Animate the selected mate; with none selected, the only mate that has a value to drive."""
        doc = self.doc
        ok = [m["id"] for m in doc.mates if A.MATE_VALUE_DOFS.get(m["type"])]
        pick = [i for i in self.panel.selected_mates() if i in ok] or (ok if len(ok) == 1 else [])
        if not pick:
            self.w.status.showMessage("Select a mate that has a value to animate (in the Mates list)", 4000)
            return
        self.start_animate(pick[0])

    def palette_commands(self, C):
        """Search tools entries for an Assembly tab; each calls what its toolbar button or menu item calls."""
        w = self.w
        cmds = [C("insert", "Insert", "Add parts from your Part Studios or a file (I).", self.toggle_insert,
                  "add part import component")]
        kw = {"fastened": "joint rigid weld lock", "revolute": "joint hinge axle pin", "slider": "joint prismatic",
              "cylindrical": "joint shaft bearing", "planar": "joint plane", "pin_slot": "joint pinslot pin slot",
              "parallel": "joint axis", "ball": "joint spherical"}
        for t, (label, tip) in A.MATE_TYPES.items():
            cmds.append(C("m_" + t, label, f"Add a {label} mate — {tip}.", lambda tt=t: self.start_mate(tt),
                          "mate " + kw.get(t, "joint"), "Mate"))
        for t, spec in A.RELATION_TYPES.items():
            cmds.append(C("r_" + t, f"{spec['label']} relation", f"{spec['help']}.",
                          lambda tt=t: self.start_relation(tt), "relation mate connector link", "Relation"))
        cmds += [
            C("move", "Move", "Move or rotate the selected parts by exact amounts.", lambda: self.start_move(),
              "translate rotate transform triad"),
            C("fix", "Fix", "Fix or unfix the selected parts so they do not move.", w._asm_toggle_fix,
              "unfix ground lock anchor"),
            C("group", "Group", "Group the selected parts so they move as one body.", w._asm_group,
              "subassembly rigid"),
            C("animate", "Animate", "Drive the selected mate through a range and play it back.",
              self.animate_selected, "motion drive play simulate"),
            C("suppress", "Suppress", "Leave the selected parts out of the model without deleting them.",
              lambda: self.suppress_selected(True), "disable deactivate exclude"),
            C("suppress", "Unsuppress", "Put the selected suppressed parts back.",
              lambda: self.suppress_selected(False), "enable activate include restore"),
            C("hide", "Hide", "Hide the selected parts.", self.hide_selected, "visibility"),
            C("hide", "Isolate", "Hide every part but the selected ones.", self.isolate_selected, "only visibility"),
            C("hide", "Show all", "Show every hidden part.", self.show_all, "unhide visibility"),
            C("interference", "Interference", "Show where parts overlap.", w.btn_interf.click,
              "clash collision overlap check"),
            C("bom", "Bill of materials", "List the parts with quantities and mass.", self.show_bom,
              "bom parts list table"),
            C("bom", "Mass properties", "Mass by material, total mass and centre of mass of the whole assembly.",
              lambda: w._mass_props_assembly(self.doc), "weight centre of mass volume density"),
            C("section", "Section view", "Cut the assembly open with a plane to see inside (Ctrl+Shift+X).",
              w.toggle_section, "cross cut", "View"),
            C("fit", "Zoom to fit", "Fit the whole assembly in the view (F).", lambda: self.vp.fit(), "", "View"),
            C("iso", "Isometric view", "Look from the isometric corner (Shift+7).",
              lambda: self.vp.set_standard_view("Isometric"), "", "View"),
        ]
        for i, name in enumerate(("Front", "Back", "Left", "Right", "Top", "Bottom"), 1):
            cmds.append(C("iso", f"{name} view", f"Look at the assembly from the {name.lower()} (Shift+{i}).",
                          lambda n=name: self.vp.set_standard_view(n), "", "View"))
        cmds += [C("export", "Export", "Export the assembly (STEP keeps the parts, names and colours).",
                   lambda: w.export(None, None), "save step stl", "File"),
                 C("undo", "Undo", "Undo the last change (Ctrl+Z).", w.undo, "", "Edit"),
                 C("redo", "Redo", "Redo what was undone (Ctrl+Y).", w.redo, "", "Edit")]
        return cmds

    def show_all(self):
        self.doc.show_all()

    def duplicate(self, ids):
        doc = self.doc
        doc.checkpoint()
        for iid in ids:
            inst = doc.instance(iid)
            lo, hi = K.bbox(doc.world_shape(iid))
            T = A.translation([0, (hi[1] - lo[1]) * 1.2 + 5, 0]) @ np.array(inst["T"], float)
            new = doc.insert(inst["source"], T=T, name=inst.get("part_name"), checkpoint=False)
            if inst.get("color"):
                doc.instance(new)["color"] = inst["color"]

    def delete_instances(self, ids):
        names = ", ".join(self.doc.instance(i)["name"] for i in ids)
        nm = sum(1 for m in self.doc.mates if m["a"]["inst"] in ids or m["b"]["inst"] in ids)
        extra = f"\n\n{nm} mate(s) on them will be deleted too." if nm else ""
        if QMessageBox.question(self.w, "Delete", f"Delete {names}?{extra}") == QMessageBox.Yes:
            self.doc.delete_instances(ids)

    def delete_selected(self):
        mids = self.panel.selected_mates()
        if mids:
            self.doc.delete_mates(mids)
            return
        ids = self.selected_instance_ids()
        if ids:
            self.delete_instances(ids)

    def open_source(self, iid):
        inst = self.doc.instance(iid)
        if inst is None:
            return
        src = inst["source"]
        if src.get("kind") != "studio":
            self.w.status.showMessage("This part came from a STEP file — there's no Part Studio to open", 5000)
            return
        studio = self.w.show_linked_studio(src)
        if studio is None:
            QMessageBox.information(self.w, "Open Part Studio", "The Part Studio for this part isn't open and its "
                                    "file can't be found.")

    def edit_in_context(self):
        """Edit the selected instance's Part Studio in the context of this Assembly (ADR 0004): switch to the
        studio and add a Context feature, or update the one that already names this Assembly and instance.
        Returns the refusal message, or None."""
        from .. import links
        folder, asm = self.w.folder, self.doc
        ids = self.selected_instance_ids()
        inst = asm.instance(ids[0]) if len(ids) == 1 else None
        src = inst["source"] if inst is not None else {}
        tab = folder.tab(src.get("tab")) if folder is not None and src.get("kind") == "studio" else None
        if inst is None:
            msg = "Select one instance to edit in context"
        elif folder is None or src.get("kind") != "studio":
            msg = "Edit in context needs a Part Studio in this Document"
        elif links.is_cross_link(src, folder.id):
            msg = "Can't edit in context: this part's Part Studio is in another Document"
        elif folder.read_only:
            msg = "Can't edit in context: this Document is a read-only Version"
        elif tab is None or tab.kind != "studio" or tab.doc is None:
            msg = "Can't edit in context: this part's Part Studio isn't in this Document"
        else:
            msg = None
        if msg:
            self.w.status.showMessage(msg, 6000)
            return msg
        iid, studio = inst["id"], tab.doc
        have = next((f for f in studio.features if f["type"] == "context"
                     and f["params"].get("assembly") == asm.uid and f["params"].get("instance") == iid), None)
        try:
            if have is not None:
                folder.update_context(tab.id, have["id"])
            else:
                feat = studio.make_feature("context")
                feat["params"].update(assembly=asm.uid, instance=iid, capture=studio.store_context(A.capture_context(asm, iid)))
                studio.add_feature(feat)
        except K.KernelError as e:
            self.w.status.showMessage(str(e), 6000)
            return str(e)
        self.w.show_linked_studio(src)
        return None

    # ------------------------------------------------------------------ analysis
    def toggle_interference(self, on=None):
        btn = self.w.btn_interf
        if on is None:
            on = btn.isChecked()
        if not on:
            self._interf = None
            self.vp.ghosts = []
            self.vp.update()
            btn.setChecked(False)
            return
        from PySide6.QtWidgets import QProgressDialog, QApplication
        prog = QProgressDialog("Checking for interference…", None, 0, 0, self.w)
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(400)

        def step(n, total):
            prog.setMaximum(max(total, 1))
            prog.setValue(n)
            QApplication.processEvents()
        try:
            res = self.doc.interferences(step)
        finally:
            prog.close()
        doc = self.doc
        if not res:
            self._interf = None
            btn.setChecked(False)
            self.vp.ghosts = []
            self.vp.update()
            QMessageBox.information(self.w, "Interference", "No interference — no parts overlap.")
            return
        ghosts = []
        lines = []
        for a, b, v, ov in res:
            try:
                ghosts.append((K.tessellate(ov), (0.92, 0.15, 0.12, 0.55)))
            except Exception:
                pass
            lines.append(f"• {doc.instance(a)['name']}  ×  {doc.instance(b)['name']}:  {fmt(v, 2)} mm³")
        self._interf = ghosts
        btn.setChecked(True)
        self.vp.ghosts = ghosts
        self.vp.update()
        self.w.status.showMessage(f"{len(res)} interference(s) found — shown in red. Click the button again "
                                  "to clear.", 8000)
        QMessageBox.information(self.w, "Interference", f"{len(res)} pair(s) of parts overlap "
                                "(shown in red):\n\n" + "\n".join(lines[:30]))

    def show_bom(self):
        if not self.doc.instances:
            QMessageBox.information(self.w, "Bill of materials", "Insert some parts first.")
            return
        d = BomDialog(self.w, self.doc)
        self.w._adapt_tree(d)
        d.exec()

    def escape(self):
        if self.mate_dialog is not None:
            self.mate_dialog.reject()
        elif self.relation_dialog is not None:
            self.relation_dialog.reject()
        elif self.move_dialog is not None:
            self.move_dialog.reject()
        elif self.animate_dialog is not None:
            self.animate_dialog.reject()
        elif self.insert_panel is not None:
            self.close_insert()
        else:
            self.vp.clear_selection()
            self.panel.select_instances(set())
            self.panel.mates.clearSelection()
            self.update_triads()

    def leave(self):
        """Close any open assembly dialog (switching tabs etc.)."""
        if self.mate_dialog is not None:
            self.mate_dialog.reject()
        if self.relation_dialog is not None:
            self.relation_dialog.reject()
        if self.move_dialog is not None:
            self.move_dialog.reject()
        if self.animate_dialog is not None:
            self.animate_dialog.reject()
        self.close_insert()
        self._drag = None
        self.vp.triads = []
        self.vp.ghosts = []
        self._interf = None
        if hasattr(self.w, "btn_interf"):
            self.w.btn_interf.setChecked(False)
