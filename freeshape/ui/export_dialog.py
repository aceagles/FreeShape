"""Options dialog for STL export: mesh resolution and ASCII or binary."""
from __future__ import annotations

from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit)

# name -> (linear deflection in mm, angular deflection in rad)
PRESETS = {"Coarse": (0.2, 0.5), "Medium": (0.1, 0.4), "Fine": (0.05, 0.3)}
CUSTOM = "Custom"
_KEY = "export_stl"


class StlOptionsDialog(QDialog):
    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self.setWindowTitle("STL export options")
        self.settings = settings
        form = QFormLayout(self)
        self.resolution = QComboBox()
        self.resolution.addItems([f"{n}" for n in PRESETS] + [CUSTOM])
        form.addRow("Resolution", self.resolution)
        self.defl_edit = QLineEdit("0.05")
        self.angle_edit = QLineEdit("0.3")
        form.addRow("Deflection (mm)", self.defl_edit)
        form.addRow("Angle (rad)", self.angle_edit)
        self.format = QComboBox()
        self.format.addItems(["Binary", "ASCII"])
        form.addRow("Format", self.format)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        form.addRow(self.buttons)
        self.resolution.setCurrentText("Fine")
        self.resolution.currentTextChanged.connect(self._sync)
        self._load()
        self._sync()

    def _sync(self, *_):
        name = self.resolution.currentText()
        custom = name == CUSTOM
        if not custom:
            d, a = PRESETS[name]
            self.defl_edit.setText(f"{d:g}")
            self.angle_edit.setText(f"{a:g}")
        self.defl_edit.setEnabled(custom)
        self.angle_edit.setEnabled(custom)

    def _num(self, edit, default):
        try:
            v = float(edit.text().replace(",", "."))
        except ValueError:
            return default
        return v if v > 0 else default

    def options(self):
        """{'defl', 'angle', 'ascii'} for the current choices."""
        name = self.resolution.currentText()
        if name == CUSTOM:
            d, a = self._num(self.defl_edit, 0.05), self._num(self.angle_edit, 0.3)
        else:
            d, a = PRESETS[name]
        return {"defl": d, "angle": a, "ascii": self.format.currentText() == "ASCII"}

    def _load(self):
        s = self.settings
        if s is None:
            return
        try:
            res = str(s.value(f"{_KEY}/resolution", "Fine"))
            if self.resolution.findText(res) >= 0:
                self.resolution.setCurrentText(res)
            self.defl_edit.setText(str(s.value(f"{_KEY}/defl", "0.05")))
            self.angle_edit.setText(str(s.value(f"{_KEY}/angle", "0.3")))
            self.format.setCurrentText("ASCII" if str(s.value(f"{_KEY}/format", "Binary")) == "ASCII" else "Binary")
        except Exception:
            pass

    def _accept(self):
        s = self.settings
        if s is not None:
            o = self.options()
            s.setValue(f"{_KEY}/resolution", self.resolution.currentText())
            s.setValue(f"{_KEY}/defl", f"{o['defl']:g}")
            s.setValue(f"{_KEY}/angle", f"{o['angle']:g}")
            s.setValue(f"{_KEY}/format", self.format.currentText())
        self.accept()

    @classmethod
    def ask(cls, parent, settings):
        """Show the dialog. Returns the options dict, or False if cancelled."""
        d = cls(parent, settings)
        return d.options() if d.exec() == QDialog.Accepted else False
