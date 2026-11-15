"""The Context bar: a slim strip at the top of the 3D view of a Part Studio that has a Context feature. It names the
Assembly, shows whether the capture is current, and holds the Context display controls (hide, select through,
transparency). Not modal: a Context is a feature, not a mode, so there is no Done button and the bar stays usable
while a feature dialog or a sketch is open."""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSlider, QToolButton,
                               QVBoxLayout)


class _InstancePopup(QFrame):
    """The Hide/show instances list: one row per neighbour of the Context, by name, with an eye each."""

    def __init__(self, bar):
        super().__init__(bar, Qt.Popup)
        self.setObjectName("featureDialog")
        self.bar = bar
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(8, 6, 8, 6)
        self.layout().setSpacing(2)
        self.rows = {}              # part id -> its eye button

    def rebuild(self):
        lay = self.layout()
        while lay.count():
            w = lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.rows = {}
        for pid, name in self.bar.instances():
            row = QHBoxLayout()
            eye = QToolButton()
            eye.setObjectName("context_row_eye")
            eye.setCheckable(True)
            eye.setProperty("part_id", pid)
            eye.toggled.connect(lambda on, p=pid: self.bar.set_instance_hidden(p, on))
            lbl = QLabel(name)
            lbl.setObjectName("context_row_name")
            row.addWidget(eye)
            row.addWidget(lbl, 1)
            lay.addLayout(row)
            self.rows[pid] = eye
        if not self.rows:
            lay.addWidget(QLabel("The Context has no instances"))
        self.sync()
        self.adjustSize()

    def sync(self):
        hidden = self.bar.main.doc.hidden_parts if self.bar.main.doc is not None else set()
        for pid, eye in self.rows.items():
            eye.blockSignals(True)
            eye.setChecked(pid in hidden)
            eye.setText("Show" if pid in hidden else "Hide")
            eye.setToolTip("Show this instance" if pid in hidden else "Hide this instance")
            eye.blockSignals(False)


class ContextBar(QFrame):
    def __init__(self, main):
        super().__init__(main.viewport)
        self.setObjectName("featureDialog")
        self.main = main
        self.fid = None             # the Context feature the bar addresses
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 3, 10, 3)
        lay.setSpacing(8)
        self.label = QLabel()
        self.label.setObjectName("context_label")
        lay.addWidget(self.label)
        self.status = QLabel()
        self.status.setObjectName("context_status")
        lay.addWidget(self.status)
        self.update_btn = QPushButton("Update context")
        self.update_btn.setObjectName("context_update")
        self.update_btn.clicked.connect(lambda *_: self.update_context())
        lay.addWidget(self.update_btn)
        self.goto_btn = QPushButton("Go to assembly")
        self.goto_btn.setObjectName("context_goto_assembly")
        self.goto_btn.clicked.connect(lambda *_: self.go_to_assembly())
        lay.addWidget(self.goto_btn)
        self.eye = QToolButton()
        self.eye.setObjectName("context_eye")
        self.eye.setCheckable(True)          # checked = the whole Context is hidden
        self.eye.toggled.connect(self.set_context_hidden)
        lay.addWidget(self.eye)
        self.instances_btn = QPushButton("Hide/show instances")
        self.instances_btn.setObjectName("context_instances")
        self.instances_btn.clicked.connect(lambda *_: self.show_instances())
        lay.addWidget(self.instances_btn)
        self.through = QCheckBox("Select through context")
        self.through.setObjectName("context_select_through")
        self.through.toggled.connect(lambda on: self.main.set_select_through_context(on))
        lay.addWidget(self.through)
        lay.addWidget(QLabel("Transparency"))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setObjectName("context_alpha_slider")
        self.slider.setRange(0, 100)           # 0 fully see-through (faces hidden, edges kept), 100 opaque
        self.slider.setFixedWidth(120)
        self.slider.valueChanged.connect(self.set_alpha)
        lay.addWidget(self.slider)
        lay.addStretch(1)
        self.popup = _InstancePopup(self)
        main.viewport.installEventFilter(self)
        self.hide()

    # ------------------------------------------------------------------ what the bar addresses
    def _contexts(self):
        doc = self.main.doc
        if doc is None or getattr(doc, "kind", None) != "studio":
            return []
        return [f["id"] for f in doc.features if f["type"] == "context" and not f.get("suppressed")]

    def context_fid(self):
        """The Context the bar addresses: the selected feature row's, else the first unsuppressed one; None if none."""
        ctxs = self._contexts()
        if not ctxs:
            return None
        sel = [i for i in self.main.left.features.selected_feature_ids() if i in ctxs]
        return sel[0] if sel else ctxs[0]

    def instances(self):
        """[(part id, instance name)] of the addressed Context's neighbours (the Assembly's name for the instance, as
        it stands now, else the name the capture stored)."""
        doc, fid = self.main.doc, self.fid
        if doc is None or fid is None:
            return []
        tab = self.assembly_tab()
        asm = tab.doc if tab is not None else None
        out = []
        for p in doc.state.parts.values():
            if p.reference and p.id.split("/")[0] == fid:
                inst = asm.instance(p.id.split("/", 1)[1]) if asm is not None and "/" in p.id else None
                out.append((p.id, (inst or {}).get("name") or p.name))     # the instance's name, else the capture's
        return out

    def assembly_tab(self):
        """The Assembly tab the addressed Context names, or None."""
        folder, doc = self.main.folder, self.main.doc
        f = doc.feature(self.fid) if doc is not None and self.fid else None
        want = ((f or {}).get("params") or {}).get("assembly")
        if folder is None or not want:
            return None
        return next((t for t in folder.tabs if t.kind == "assembly" and t.doc is not None
                     and want in (t.id, getattr(t.doc, "uid", None))), None)

    # ------------------------------------------------------------------ refresh
    def refresh(self):
        """Show or hide the bar and bring every control up to date with the Document and the window settings."""
        self.fid = self.context_fid()
        if self.fid is None:
            self.popup.hide()
            self.hide()
            return
        doc = self.main.doc
        tab = self.assembly_tab()
        status = self.main._context_status_of(self.fid) or ""
        self.label.setText("Context of " + (tab.name if tab is not None else "(missing assembly)"))
        self.status.setText(status)
        self.update_btn.setProperty("outOfDate", status == "out of date")
        self.update_btn.setStyleSheet("background: #f5c542; color: #222; font-weight: bold;"
                                      if status == "out of date" else "")
        self.update_btn.setToolTip("Recapture the Context from its Assembly")
        self.goto_btn.setEnabled(tab is not None)
        hidden = self.fid in doc.hidden_features
        self._set(self.eye, hidden)
        self.eye.setText("Show" if hidden else "Hide")
        self.eye.setToolTip("Show the whole Context" if hidden else "Hide the whole Context")
        self._set(self.through, bool(self.main.viewport.select_through))
        self._set(self.slider, int(round(self.main.viewport.context_alpha * 100)))
        if self.popup.isVisible():
            self.popup.rebuild()
        self.show()
        self.place()
        self.raise_()

    @staticmethod
    def _set(widget, value):
        widget.blockSignals(True)
        widget.setValue(value) if isinstance(widget, QSlider) else widget.setChecked(value)
        widget.blockSignals(False)

    def place(self):
        vp = self.main.viewport
        self.setFixedWidth(vp.width())
        self.adjustSize()
        self.move(0, 0)

    def eventFilter(self, obj, ev):
        if obj is self.main.viewport and ev.type() == QEvent.Resize and not self.isHidden():
            self.place()
        return False

    # ------------------------------------------------------------------ controls (each callable without a mouse)
    def update_context(self):
        if self.fid is not None:
            self.main.update_context(self.fid)
        self.refresh()

    def go_to_assembly(self):
        """Make the Assembly tab current. Returns False when the Context's Assembly is not a tab."""
        self.fid = self.context_fid()
        tab = self.assembly_tab()
        if tab is None:
            self.main.status.showMessage("This Context's Assembly is not in the Document", 6000)
            return False
        self.main.tabs.setCurrentIndex(self.main._tab_index(tab.doc))
        return True

    def set_context_hidden(self, hidden):
        if self.fid is not None:
            self.main.set_context_hidden([self.fid], bool(hidden))

    def show_instances(self):
        self.popup.rebuild()
        self.popup.move(self.instances_btn.mapToGlobal(self.instances_btn.rect().bottomLeft()))
        self.popup.show()

    def set_instance_hidden(self, pid, hidden):
        if hidden:
            self.main.hide_context_instances([pid])
        else:
            self.main.doc.checkpoint()
            self.main.doc.set_parts_hidden([pid], False)

    def set_select_through(self, on):
        self.main.set_select_through_context(bool(on))

    def set_alpha(self, value):
        """The slider's value, 0 (see-through) to 100 (opaque)."""
        self.main.set_context_alpha(value / 100.0)
