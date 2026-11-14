"""Options dialogs for exports: STL (mesh resolution, ASCII or binary) and DXF (profile, what each selected item
is for, and the profile editor)."""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton, QVBoxLayout, QWidget)

from .. import dxfexport as DX

# name -> (linear deflection in mm, angular deflection in rad)
PRESETS = {"Coarse": (0.2, 0.5), "Medium": (0.1, 0.4), "Fine": (0.05, 0.3)}
CUSTOM = "Custom"
_KEY = "export_stl"


class StlOptionsDialog(QDialog):
    def __init__(self, parent=None, settings=None, mesh_kind="STL"):
        super().__init__(parent)
        self.setWindowTitle(f"{mesh_kind} export options")
        self.settings = settings
        self.has_format = mesh_kind == "STL"     # 3MF and OBJ have no ASCII/binary choice
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
        if self.has_format:
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
        o = {"defl": d, "angle": a}
        if self.has_format:
            o["ascii"] = self.format.currentText() == "ASCII"
        return o

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
            if self.has_format:
                s.setValue(f"{_KEY}/format", self.format.currentText())
        self.accept()

    @classmethod
    def ask(cls, parent, settings, mesh_kind="STL"):
        """Show the dialog. Returns the options dict, or False if cancelled."""
        d = cls(parent, settings, mesh_kind)
        return d.options() if d.exec() == QDialog.Accepted else False


# =========================================================================== DXF
DXF_PROFILES_KEY = "dxf/profiles"      # the user's own profiles, as JSON (dxfexport.profiles_to_json)
DXF_PROFILE_KEY = "dxf/profile"        # the name of the profile used last


def dxf_user_profiles(settings) -> list:
    if settings is None:
        return []
    try:
        return DX.profiles_from_json(str(settings.value(DXF_PROFILES_KEY, "") or ""))
    except Exception:       # noqa: BLE001 -- a broken setting must not stop an export
        return []


def dxf_last_profile(settings):
    """The profile used last (the standard one the first time, or if it has been deleted since)."""
    name = str(settings.value(DXF_PROFILE_KEY, "") or "") if settings is not None else ""
    return DX.find_profile(name, dxf_user_profiles(settings))


def dxf_remember_profile(settings, profile):
    if settings is not None:
        settings.setValue(DXF_PROFILE_KEY, profile.name)


class DxfProfileCombo(QComboBox):
    """The DXF profiles to choose from: the built-in ones, then the user's. Starts on the one used last."""

    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.reload(dxf_last_profile(settings).name)

    def reload(self, select=None):
        select = select or self.currentText()
        self._profiles = DX.all_profiles(dxf_user_profiles(self.settings))
        self.blockSignals(True)
        self.clear()
        for pr in self._profiles:
            self.addItem(pr.name)
            self.setItemData(self.count() - 1, pr.about, 3)          # Qt.ToolTipRole
        i = self.findText(select)
        self.setCurrentIndex(i if i >= 0 else 0)
        self.blockSignals(False)
        self.currentIndexChanged.emit(self.currentIndex())

    def profile(self):
        i = self.currentIndex()
        return self._profiles[i] if 0 <= i < len(self._profiles) else DX.STANDARD

    def remember(self):
        dxf_remember_profile(self.settings, self.profile())


class DxfExportDialog(QDialog):
    """What to write and how: the profile (which program the DXF is for), what each selected sketch or face is
    for (cut or engrave), and what to do with a sketch's construction lines.

    `sources` is [(label, kind)] with kind "sketch" or "face"; with none (a flat pattern, which already knows
    what is a cut and what is a bend) only the profile is asked."""

    CONSTRUCTION = ((None, "Leave out"), ("cut", "Cut"), ("engrave", "Engrave"))

    def __init__(self, parent=None, settings=None, sources=()):
        super().__init__(parent)
        self.setWindowTitle("Export DXF")
        self.settings = settings
        self.sources = list(sources)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        lay.addLayout(form)
        row = QHBoxLayout()
        self.profile_combo = DxfProfileCombo(settings)
        row.addWidget(self.profile_combo, 1)
        self.edit_btn = QPushButton("Profiles…")
        self.edit_btn.setToolTip("Change the layers and colours a profile writes, or add a profile of your own.")
        self.edit_btn.clicked.connect(self.edit_profiles)
        row.addWidget(self.edit_btn)
        form.addRow("Profile", row)
        self.about = QLabel()
        self.about.setWordWrap(True)
        self.about.setStyleSheet("color:#777;")
        form.addRow("", self.about)
        self.role_combos = []
        for label, _kind in self.sources:
            c = QComboBox()
            self.role_combos.append(c)
            form.addRow(label, c)
        self.construction = QComboBox()
        for _role, text in self.CONSTRUCTION:
            self.construction.addItem(text)
        self.construction.setToolTip("Construction lines in a sketch are normally left out. Give them a job to draw "
                                     "the cut outline and the engraving in one sketch.")
        self.has_sketch = any(kind == "sketch" for _l, kind in self.sources)
        if self.has_sketch:
            form.addRow("Construction lines", self.construction)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self.profile_combo.currentIndexChanged.connect(self._sync)
        self._sync()

    def _roles(self):
        """The roles a sketch or face can be given under the chosen profile."""
        pr = self.profile_combo.profile()
        return [r for r in DX.PICKABLE_ROLES if pr.style(r) is not None]

    def _sync(self, *_):
        pr = self.profile_combo.profile()
        self.about.setText(pr.about)
        self.about.setVisible(bool(pr.about))
        roles = self._roles()
        for c in self.role_combos:
            keep = c.currentData()
            c.blockSignals(True)
            c.clear()
            for r in roles:
                c.addItem(DX.ROLE_LABELS[r], r)
            i = c.findData(keep)
            c.setCurrentIndex(i if i >= 0 else 0)
            c.blockSignals(False)
        for i, (role, _text) in enumerate(self.CONSTRUCTION):       # an option the profile leaves out is greyed
            item = self.construction.model().item(i)
            if item is not None:
                item.setEnabled(role is None or role in roles)
        if self.construction_role() not in [None] + roles:
            self.construction.setCurrentIndex(0)

    def set_role(self, i, role):
        j = self.role_combos[i].findData(role)
        if j >= 0:
            self.role_combos[i].setCurrentIndex(j)

    def set_construction(self, role):
        self.construction.setCurrentIndex([r for r, _t in self.CONSTRUCTION].index(role))

    def construction_role(self):
        return self.CONSTRUCTION[max(self.construction.currentIndex(), 0)][0] if self.has_sketch else None

    def options(self):
        """{'profile': Profile, 'roles': [a role per source], 'construction': role or None}."""
        return {"profile": self.profile_combo.profile(),
                "roles": [c.currentData() or "cut" for c in self.role_combos],
                "construction": self.construction_role()}

    def edit_profiles(self):
        d = DxfProfilesDialog(self, self.settings, self.profile_combo.currentText())
        if d.exec() == QDialog.Accepted:
            self.profile_combo.reload(d.current_name())

    def _accept(self):
        self.profile_combo.remember()
        self.accept()

    @classmethod
    def ask(cls, parent, settings, sources=()):
        """Show the dialog. Returns the options dict, or False if cancelled."""
        d = cls(parent, settings, sources)
        return d.options() if d.exec() == QDialog.Accepted else False


class DxfProfilesDialog(QDialog):
    """The DXF profiles: for each role (cut, engrave, bend line, note, sheet outline) whether it is written, on
    which layer, in which colour and line type; plus the file format. The built-in profiles cannot be changed —
    Duplicate one and change the copy. OK saves the user's profiles to the settings."""

    def __init__(self, parent=None, settings=None, select=None):
        super().__init__(parent)
        self.setWindowTitle("DXF profiles")
        self.settings = settings
        self.user = [p.copy() for p in dxf_user_profiles(settings)]
        self._row = -1
        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        lay.addLayout(left)
        self.list = QListWidget()
        left.addWidget(self.list, 1)
        b = QHBoxLayout()
        self.dup_btn = QPushButton("Duplicate")
        # bound methods, not lambdas: a lambda holding `self` ties the dialog into a reference cycle, and a dialog
        # freed by the cycle collector takes its widgets down in an order Qt does not survive
        self.dup_btn.clicked.connect(self._duplicate_clicked)
        self.del_btn = QPushButton("Delete")
        self.del_btn.clicked.connect(self.remove_profile)
        b.addWidget(self.dup_btn)
        b.addWidget(self.del_btn)
        left.addLayout(b)
        right = QVBoxLayout()
        lay.addLayout(right, 2)
        self.editor = QWidget()
        right.addWidget(self.editor)
        form = QFormLayout(self.editor)
        self.name = QLineEdit()
        form.addRow("Name", self.name)
        self.format = QComboBox()
        for key, text in DX.FORMATS.items():
            self.format.addItem(text, key)
        form.addRow("Format", self.format)
        self.entity_colour = QCheckBox("Write the colour on every entity as well as on its layer")
        form.addRow("", self.entity_colour)
        self.text = QCheckBox("Write text (labels); off for programs that do not read DXF text")
        form.addRow("", self.text)
        self.units = QCheckBox("Say the units are millimetres in the header (R12 format)")
        form.addRow("", self.units)
        table = QWidget()              # the grid has a parent from the start: its labels are owned at once
        grid = QGridLayout(table)
        grid.setContentsMargins(0, 0, 0, 0)
        for col, head in enumerate(("Write", "Layer", "Colour", "Line type")):
            grid.addWidget(QLabel(f"<b>{head}</b>"), 0, col)
        self.rows = {}
        for i, role in enumerate(DX.ROLES, 1):
            on = QCheckBox(DX.ROLE_LABELS[role])
            layer = QLineEdit()
            colour = QComboBox()
            for n, cname in DX.COLOURS.items():
                colour.addItem(f"{n}  {cname}", n)
            lt = QComboBox()
            lt.addItems([x.capitalize() for x in DX.LINETYPE_NAMES])
            for col, wdg in enumerate((on, layer, colour, lt)):
                grid.addWidget(wdg, i, col)
            self.rows[role] = (on, layer, colour, lt)
        form.addRow(table)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:#777;")
        right.addWidget(self.hint)
        right.addStretch(1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        right.addWidget(self.buttons)
        self._fill(select)
        self.list.currentRowChanged.connect(self._show)
        self._show(self.list.currentRow())

    # ------------------------------------------------------------------ the list
    def _all(self):
        return DX.all_profiles(self.user)

    def _fill(self, select=None):
        self.list.blockSignals(True)
        self.list.clear()
        names = [p.name + ("  (built in)" if p.builtin else "") for p in self._all()]
        self.list.addItems(names)
        plain = [p.name for p in self._all()]
        self.list.setCurrentRow(plain.index(select) if select in plain else 0)
        self.list.blockSignals(False)

    def current(self):
        i = self.list.currentRow()
        ps = self._all()
        return ps[i] if 0 <= i < len(ps) else DX.STANDARD

    def current_name(self):
        return self.current().name

    def select(self, name):
        self._store()
        plain = [p.name for p in self._all()]
        if name in plain:
            self.list.setCurrentRow(plain.index(name))

    def _free_name(self, base):
        taken = {p.name for p in self._all()}
        name, n = f"{base} copy", 2
        while name in taken:
            name, n = f"{base} copy {n}", n + 1
        return name

    def duplicate(self, name=None):
        """A user profile copied from the selected one; it becomes the selected one. Returns it."""
        self._store()
        src = self.current()
        p = src.copy(name or self._free_name(src.name))
        self.user.append(p)
        self._row = -1
        self._fill(p.name)
        self._show(self.list.currentRow())
        return p

    def _duplicate_clicked(self, *_):
        self.duplicate()

    def remove_profile(self, *_):
        """Delete the selected user profile (a built-in one cannot be deleted)."""
        p = self.current()
        if p.builtin:
            return
        self.user = [q for q in self.user if q is not p]
        self._row = -1
        self._fill()
        self._show(self.list.currentRow())

    # ------------------------------------------------------------------ the editor
    def _show(self, row):
        if self._row != row:
            self._store()
        self._row = row
        p = self.current()
        self.name.setText(p.name)
        self.format.setCurrentIndex(max(self.format.findData(p.format), 0))
        self.entity_colour.setChecked(p.entity_colour)
        self.text.setChecked(p.text)
        self.units.setChecked(p.units_header)
        for role, (on, layer, colour, lt) in self.rows.items():
            st = p.roles.get(role)
            on.setChecked(st is not None and not st.skip)
            layer.setText(st.layer if st else role.upper())
            n = st.colour if st else 7
            if colour.findData(n) < 0:
                colour.addItem(f"{n}  Colour {n}", n)
            colour.setCurrentIndex(colour.findData(n))
            lt.setCurrentIndex(DX.LINETYPE_NAMES.index(st.linetype if st else "CONTINUOUS"))
        self.editor.setEnabled(not p.builtin)
        self.del_btn.setEnabled(not p.builtin)
        self.hint.setText(("A built-in profile: Duplicate it to change it. " if p.builtin else "") + p.about)

    def _store(self):
        """Put what the editor shows into the user profile it was showing."""
        ps = self._all()
        if not (0 <= self._row < len(ps)) or ps[self._row].builtin:
            return
        p = ps[self._row]
        name = self.name.text().strip()
        if name and name != p.name and name not in {q.name for q in ps}:
            p.name = name
            model = self.list.model()          # through the model: no Python handle on the list's own item
            model.setData(model.index(self._row, 0), name)
        p.format = self.format.currentData() or "r2010"
        p.entity_colour = self.entity_colour.isChecked()
        p.text = self.text.isChecked()
        p.units_header = self.units.isChecked()
        for role, (on, layer, colour, lt) in self.rows.items():
            p.roles[role] = DX.RoleStyle(layer.text().strip() or role.upper(), int(colour.currentData() or 7),
                                         DX.LINETYPE_NAMES[max(lt.currentIndex(), 0)], not on.isChecked())

    def profiles(self):
        """The user's profiles as edited so far."""
        self._store()
        return list(self.user)

    def commit(self):
        """Save the user's profiles to the settings."""
        if self.settings is not None:
            self.settings.setValue(DXF_PROFILES_KEY, DX.profiles_to_json(self.profiles()))

    def _accept(self):
        self.commit()
        self.accept()
