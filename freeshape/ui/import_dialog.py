"""Insert DXF or SVG into a sketch: reading a file by its extension, and the options dialog shown before inserting.

`read_source(path)` returns an `ImportSource`; `ImportDialog(source)` edits the options and `choice()` gives what
`freeshape.sketchimport.insert` needs. The dialog is plain Qt widgets; the sketcher runs it through a method a test
can stub."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel,
                               QListWidget, QListWidgetItem, QVBoxLayout)
from PySide6.QtCore import Qt

from .. import sketchimport as SI

# Copied from scripts/spikes/op2_import_bench.result.txt: "DECISION import warn=1500 confirm=4000" (sketch
# points). The packaged app does not ship scripts/, so nothing reads that file at run time.
IMPORT_WARN_POINTS = 1500
IMPORT_CONFIRM_POINTS = 4000

PPI_CHOICES = (96, 90, 72)
DXF_UNITS = ("mm", "cm", "m", "in", "ft")
_UNIT_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "ft": 304.8}
DWG_MESSAGE = "DWG is not supported - save it as DXF"


@dataclass
class ImportSource:
    path: str
    kind: str                         # "dxf" or "svg"
    text: str = ""                    # the SVG text

    @property
    def name(self):
        return os.path.basename(self.path)

    def read(self, units="mm", ppi=None):
        """(prims, report) at the file's own units, or at `units` (DXF) / `ppi` (SVG) where the file has none."""
        if self.kind == "dxf":
            from .. import dxfimport
            return dxfimport.read(self.path, units=units)
        from .. import svgimport
        return svgimport.read(self.text, ppi=ppi)


def read_source(path):
    """Check the extension and open the file. Raises ValueError with a message for the user when it is not a DXF or
    SVG file or cannot be read (DXF is parsed here too, so a bad file fails before any dialog)."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".dwg":
        raise ValueError(DWG_MESSAGE)
    if ext not in (".dxf", ".svg"):
        raise ValueError(f"Cannot insert a {ext or 'file without an extension'} file: choose a DXF or SVG file")
    if ext == ".dxf":
        src = ImportSource(path, "dxf")
        src.read()
        return src
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise ValueError(f"Cannot read {os.path.basename(path)}: {e.strerror or e}") from None
    src = ImportSource(path, "svg", raw.decode("utf-8", "replace"))
    src.read()
    return src


@dataclass
class ImportChoice:
    prims: list
    kwargs: dict
    report: dict = field(default_factory=dict)        # the reader's report
    name: str = ""


class ImportDialog(QDialog):
    def __init__(self, source: ImportSource, parent=None):
        super().__init__(parent)
        self.source = source
        self.setWindowTitle("Insert DXF or SVG")
        self.prims, self.report, self._factor, self._lo = [], {}, 1.0, 0.0
        self._native_w = 0.0
        self.points = 0
        form = QFormLayout()
        top = QVBoxLayout(self)
        top.addLayout(form)
        form.addRow(QLabel(source.name))

        self.units_source = QComboBox()
        self.units_source.addItems(["File", "Default"])
        self.units_source.setToolTip("File: use the size units the file states. Default: ignore them and use the "
                                     "value below (also used when the file states none).")
        form.addRow("Units from", self.units_source)
        if source.kind == "dxf":
            self.units = QComboBox()
            self.units.addItems(DXF_UNITS)
            form.addRow("Units", self.units)
            self.ppi = None
        else:
            self.ppi = QComboBox()
            self.ppi.addItems([str(p) for p in PPI_CHOICES])
            form.addRow("Pixels per inch", self.ppi)
            self.units = None
        self.size_label = QLabel()
        form.addRow("Size", self.size_label)
        self.scale = QDoubleSpinBox()
        self.scale.setDecimals(6)
        self.scale.setRange(0.000001, 1000000)
        self.scale.setValue(1.0)
        form.addRow("Scale", self.scale)
        self.fit = QDoubleSpinBox()
        self.fit.setDecimals(4)
        self.fit.setRange(0, 1000000)
        self.fit.setSpecialValueText("off")
        self.fit.setSuffix(" mm")
        form.addRow("Fit width", self.fit)
        self.use_origin = QCheckBox("Use file origin position")
        self.use_origin.setToolTip("Off: the bounding box is centred on the sketch origin.")
        form.addRow(self.use_origin)
        self.tol = QDoubleSpinBox()
        self.tol.setDecimals(3)
        self.tol.setRange(0.001, 1.0)
        self.tol.setSingleStep(0.005)
        self.tol.setValue(0.01)
        self.tol.setSuffix(" mm")
        form.addRow("Curve tolerance", self.tol)
        self.layers = QListWidget()
        self.layers.setMaximumHeight(110)
        form.addRow("Layers" if source.kind == "dxf" else "Groups", self.layers)
        self.as_construction = QCheckBox("Import as construction")
        self.as_construction.setToolTip("Bring the unticked ones in as construction geometry instead of leaving "
                                        "them out.")
        form.addRow(self.as_construction)
        self.counts = QLabel()
        top.addWidget(self.counts)
        self.warning = QLabel()
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color:#b45309;")
        top.addWidget(self.warning)
        self.confirm = QCheckBox("Insert this many points anyway")
        top.addWidget(self.confirm)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        top.addWidget(self.buttons)
        self.ok = self.buttons.button(QDialogButtonBox.Ok)

        self._load()
        self.units_source.currentIndexChanged.connect(self._reload)
        for w in (self.units, self.ppi):
            if w is not None:
                w.currentIndexChanged.connect(self._reload)
        self.scale.valueChanged.connect(self._scale_edited)
        self.fit.valueChanged.connect(self._fit_edited)
        self.tol.valueChanged.connect(self._refresh)
        self.use_origin.toggled.connect(self._refresh)
        self.layers.itemChanged.connect(self._layers_changed)
        self.as_construction.toggled.connect(self._refresh)
        self.confirm.toggled.connect(self._refresh)
        self._refresh()

    # ------------------------------------------------------------------ reading
    def _read_args(self):
        default = self.units_source.currentText() == "Default"
        if self.source.kind == "dxf":
            return {"units": self.units.currentText()}, default
        return {"ppi": int(self.ppi.currentText()) if default else None}, default

    def _load(self):
        kw, default = self._read_args()
        if self.source.kind == "svg" and not default:
            kw = {"ppi": None}
        prims, rep = self.source.read(**kw)
        self.report = rep
        self._factor = 1.0
        if default:
            if self.source.kind == "dxf":
                self._factor = _UNIT_MM[self.units.currentText()] / rep["units"]["scale"]
            else:
                self._factor = (25.4 / int(self.ppi.currentText())) / rep["scale"]
        self.prims = prims
        checked = {self.layers.item(i).data(Qt.UserRole): self.layers.item(i).checkState()
                   for i in range(self.layers.count())}
        counts = {}
        for p in prims:
            counts[p.get("layer", "0")] = counts.get(p.get("layer", "0"), 0) + 1
        self.layers.blockSignals(True)
        self.layers.clear()
        for name in sorted(counts):
            it = QListWidgetItem(f"{name}  ({counts[name]})")
            it.setData(Qt.UserRole, name)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(checked.get(name, Qt.Checked))
            self.layers.addItem(it)
        self.layers.blockSignals(False)

    def _reload(self, *_):
        self._load()
        self._refresh()

    # ------------------------------------------------------------------ choices
    def layer_items(self):
        return [self.layers.item(i) for i in range(self.layers.count())]

    def ticked(self):
        return {it.data(Qt.UserRole) for it in self.layer_items() if it.checkState() == Qt.Checked}

    def _selected_prims(self):
        ticked = self.ticked()
        if self.as_construction.isChecked():
            return list(self.prims)
        return [p for p in self.prims if p.get("layer", "0") in ticked]

    def _effective_scale(self):
        if self.fit.value() > 0 and self._native_w > 0:
            return self.fit.value() / self._native_w
        return self.scale.value()

    def _native_width(self):
        bb = SI._bbox([p for p in self.prims if p.get("layer", "0") in self.ticked()])
        return 0.0 if bb is None else float(bb[1][0] - bb[0][0]) * self._factor

    def _scale_edited(self, *_):
        self.fit.blockSignals(True)
        self.fit.setValue(0)
        self.fit.blockSignals(False)
        self._refresh()

    def _fit_edited(self, *_):
        if self.fit.value() > 0 and self._native_w > 0:
            self.scale.blockSignals(True)
            self.scale.setValue(self.fit.value() / self._native_w)
            self.scale.blockSignals(False)
        self._refresh()

    def _layers_changed(self, *_):
        w = self._native_width()
        if self.fit.value() > 0 and w > 0:           # keep the requested width across a changed layer set
            self.scale.blockSignals(True)
            self.scale.setValue(self.fit.value() / w)
            self.scale.blockSignals(False)
        self._refresh()

    def _kwargs(self):
        ticked = self.ticked()
        cons = ({p.get("layer", "0") for p in self.prims} - ticked) if self.as_construction.isChecked() else ()
        return {"scale": self._effective_scale() * self._factor, "use_file_origin": self.use_origin.isChecked(),
                "tol": self.tol.value(), "construction": set(cons)}

    def choice(self):
        return ImportChoice(self._selected_prims(), self._kwargs(), self.report, self.source.name)

    # ------------------------------------------------------------------ feedback
    def _refresh(self, *_):
        from ..sketch import Sketch
        self._native_w = self._native_width()
        if self.fit.value() > 0 and self._native_w > 0:
            self.scale.blockSignals(True)
            self.scale.setValue(self.fit.value() / self._native_w)
            self.scale.blockSignals(False)
        prims = self._selected_prims()
        bb = SI._bbox(prims)
        k = self._effective_scale() * self._factor
        if bb is None:
            self.size_label.setText("nothing to insert")
        else:
            self.size_label.setText(f"{(bb[1][0] - bb[0][0]) * k:.3f} x {(bb[1][1] - bb[0][1]) * k:.3f} mm")
        n_pts = 0
        if prims:
            try:
                _ids, rep = SI.insert(Sketch(), prims, **self._kwargs())
                n_pts = len(rep["points"])
            except ValueError:
                n_pts = 0
        self.points = n_pts
        n_cur = len(prims)
        self.counts.setText(f"{n_cur} curves, {n_pts} points")
        warn = n_pts > IMPORT_WARN_POINTS
        need = n_pts > IMPORT_CONFIRM_POINTS
        if need:
            self.warning.setText(f"{n_pts} points is a lot: the sketch will be slow to edit. Raise the curve "
                                 f"tolerance or tick fewer {'layers' if self.source.kind == 'dxf' else 'groups'}, "
                                 f"or tick the box below to insert it anyway.")
        elif warn:
            self.warning.setText(f"{n_pts} points: the sketch may be slow to edit. Raising the curve tolerance or "
                                 f"ticking fewer {'layers' if self.source.kind == 'dxf' else 'groups'} brings the "
                                 f"count down.")
        else:
            self.warning.setText("")
        self.warning.setVisible(warn)
        self.confirm.setVisible(need)
        self.ok.setEnabled(bool(prims) and (not need or self.confirm.isChecked()))
