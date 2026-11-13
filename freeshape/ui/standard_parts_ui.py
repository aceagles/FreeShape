"""The UI side of Standard parts in an Assembly.

`StandardPartEditDialog` edits size, length and `Model the thread` of one or more Standard part instances (card I3.5).
`StandardPartsPane` is the Standard parts tab of the Assembly Insert panel (card I3.4).

Nothing here knows a particular Plugin: the fields come from each type's `sizes()`, `lengths(size)` and
`default_params()`.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFormLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
    QSpinBox, QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QHBoxLayout, QWidget,
)

from .. import pyfeature
from .numparse import fmt

THREAD_PARAM = "model_thread"


def length_text(v) -> str:
    return fmt(v) if isinstance(v, (int, float)) else str(v)


def type_lengths(type_id, size, params=None):
    """Lengths the type offers for `size` ([] for a type without a length). `point` and `standard` of the
    instance's params are passed on for types that take them (the tapping screw)."""
    cls = pyfeature.standard_part(type_id)
    if cls is None:
        return []
    extra = {k: params[k] for k in ("point", "standard") if params and k in params}
    try:
        return list(cls().lengths(size, **extra))
    except TypeError:
        return list(cls().lengths(size))
    except Exception:
        return []


class StandardPartEditDialog(QDialog):
    """Size, length and `Model the thread` for the Standard parts in `ids` of `doc`.

    A field whose value differs across the selection starts blank and is left unchanged unless set; with a
    mixed selection only the fields every type has are shown. `apply()` makes the one `set_standard_params` call
    (what OK does) without a modal loop: it sets `result`, `message`, emits `applied(result)` and returns the
    result. `accept()` is `apply()` followed by closing. Object names: spe_size, spe_length, spe_thread, spe_ok,
    spe_cancel, spe_status."""

    applied = Signal(object)

    def __init__(self, doc, ids, parent=None):
        super().__init__(parent)
        self.setObjectName("standardPartEdit")
        self.setWindowTitle("Edit standard part")
        self.doc = doc
        self.ids = [i for i in dict.fromkeys(ids) if self._params(i) is not None]
        self.result = None
        self.message = ""
        self._types = {i: doc.instance(i)["source"]["type"] for i in self.ids}
        self._ptypes = [t for t in dict.fromkeys(self._types.values()) if pyfeature.standard_part(t) is not None]
        self.size = self.length = self.thread = None
        self._initial = {}

        lay = QVBoxLayout(self)
        form = QFormLayout()
        lay.addLayout(form)
        shared = self._shared_fields()

        self.size = QComboBox()
        self.size.setObjectName("spe_size")
        self._fill_sizes()
        form.addRow("Size", self.size)
        self._initial["size"] = self._common("size")
        self._select(self.size, self._initial["size"])

        if "length" in shared:
            self.length = QComboBox()
            self.length.setObjectName("spe_length")
            form.addRow("Length (mm)", self.length)
            self._initial["length"] = self._common("length")
            self._fill_lengths()
            self.size.currentIndexChanged.connect(lambda _=0: self._fill_lengths())

        if "thread" in shared:
            self.thread = QCheckBox("Model the thread")
            self.thread.setObjectName("spe_thread")
            vals = {bool(self._params(i).get(THREAD_PARAM)) for i in self.ids}
            if len(vals) == 1:
                self._initial["thread"] = Qt.Checked if vals.pop() else Qt.Unchecked
            else:
                self.thread.setTristate(True)
                self._initial["thread"] = Qt.PartiallyChecked
            self.thread.setCheckState(self._initial["thread"])
            form.addRow("", self.thread)

        n = len(self.ids)
        self.note = QLabel("" if n <= 1 else f"{n} standard parts selected. A blank field stays as it is.")
        self.note.setObjectName("muted")
        lay.addWidget(self.note)
        self.status = QLabel("")
        self.status.setObjectName("spe_status")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        row = QHBoxLayout()
        row.addStretch(1)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("spe_cancel")
        self.cancel_btn.clicked.connect(self.reject)
        self.ok_btn = QPushButton("OK")
        self.ok_btn.setObjectName("spe_ok")
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self.accept)
        self.ok_btn.setEnabled(bool(self._ptypes))
        row.addWidget(self.cancel_btn)
        row.addWidget(self.ok_btn)
        lay.addLayout(row)

    # ------------------------------------------------------------------ fields
    def _params(self, iid):
        inst = self.doc.instance(iid)
        if inst is None or inst["source"].get("kind") != "standard":
            return None
        return inst["source"].get("params") or {}

    def _shared_fields(self):
        """The fields every installed type in the selection has."""
        out = {"size"}
        if not self._ptypes:
            return out
        if all(self._has_length(t) for t in self._ptypes):
            out.add("length")
        if all(THREAD_PARAM in pyfeature.standard_part(t)().default_params() for t in self._ptypes):
            out.add("thread")
        return out

    def _has_length(self, type_id):
        return any(type_lengths(type_id, i_size, self._params(i))
                   for i in self.ids if self._types[i] == type_id
                   for i_size in [self._params(i).get("size")])

    def _common(self, key):
        vals = [self._params(i).get(key) for i in self.ids]
        return vals[0] if vals and all(v == vals[0] for v in vals) else None

    @staticmethod
    def _select(combo, value):
        k = combo.findData(value) if value is not None else 0
        combo.setCurrentIndex(max(k, 0))

    def _fill_sizes(self):
        sizes = None
        for t in self._ptypes:
            s = list(pyfeature.standard_part(t)().sizes())
            sizes = s if sizes is None else [x for x in sizes if x in s]
        self.size.addItem("", None)
        for s in sizes or []:
            self.size.addItem(s, s)
        for i in self.ids:                      # a size outside the lists (an unvalid stored one) still shows
            v = self._params(i).get("size")
            if v is not None and self.size.findData(v) < 0:
                self.size.addItem(str(v), v)

    def valid_lengths(self, size=None):
        """Lengths valid for `size`; with no size, every length valid for some selected instance's own size.
        Shared by all types in the selection."""
        per_type = []
        for t in self._ptypes:
            vals = set()
            for i in self.ids:
                if self._types[i] != t:
                    continue
                p = self._params(i)
                s = size if size is not None else p.get("size")
                vals.update(type_lengths(t, s, p))
            per_type.append(vals)
        out = set.intersection(*per_type) if per_type else set()
        return sorted(out)

    def _fill_lengths(self):
        size = self.size.currentData()
        cur = self.length.currentData() if self.length.count() else self._initial.get("length")
        lens = self.valid_lengths(size)
        self.length.blockSignals(True)
        self.length.clear()
        self.length.addItem("", None)
        for v in lens:
            self.length.addItem(length_text(v), v)
        k = self.length.findData(cur) if cur is not None else -1
        if k < 0 and cur is not None and size is None and cur == self._initial.get("length"):
            self.length.addItem(length_text(cur), cur)       # the shared stored length, though off the list
            k = self.length.count() - 1
        self.length.setCurrentIndex(max(k, 0))
        self.length.blockSignals(False)

    # ------------------------------------------------------------------ result
    def changes(self) -> dict:
        """The params the person set: a field that is blank, or left as it started, is absent."""
        out = {}
        s = self.size.currentData()
        if s is not None and s != self._initial["size"]:
            out["size"] = s
        if self.length is not None:
            v = self.length.currentData()
            if v is not None and v != self._initial["length"]:
                out["length"] = v
        if self.thread is not None:
            st = self.thread.checkState()
            if st != self._initial["thread"] and st != Qt.PartiallyChecked:
                out[THREAD_PARAM] = st == Qt.Checked
        return out

    def apply(self):
        """One `set_standard_params` call for the selection. Returns its result, or None when nothing was set."""
        ch = self.changes()
        if not ch or not self.ids:
            self.result, self.message = None, ""
            self.status.setText("")
            return None
        res = self.doc.set_standard_params(self.ids, ch)
        self.result = res
        self.message = self._message(res)
        self.status.setText(self.message)
        self.applied.emit(res)
        return res

    def _message(self, res):
        n = len(res["changed"])
        msg = f"Changed {n} standard part(s)" if n else "No standard part changed"
        if res["rejected"]:
            parts = []
            for iid, why in res["rejected"].items():
                inst = self.doc.instance(iid)
                parts.append(f"{inst['name'] if inst else iid}: {why}")
            msg += ". Not changed: " + "; ".join(parts)
        return msg

    def accept(self):
        self.apply()
        super().accept()


# =========================================================================== the Insert panel's tab

CATEGORY_ORDER = ("Screws", "Nuts", "Washers")
PARAM_LABELS = {THREAD_PARAM: "Model the thread", "point": "Point", "standard": "Standard"}
PARAM_NAMES = {THREAD_PARAM: "sp_thread", "point": "sp_point", "standard": "sp_standard"}
POINT_CHOICES = ("C", "F", "R")
POINT_TEXT = {"C": "C (conical)", "F": "F (flat)", "R": "R (rounded)"}
ROLE_TYPE = Qt.UserRole + 1


class StandardPartsPane(QWidget):
    """Insert Standard parts into the holes selected in the viewport, or one free instance when none match.

    Pick a type in the tree (grouped by category). Below it sit the type's extra parameters, then either a
    results table (one row per matched hole size: size, hole count, a length drop-down preset to the plan's
    default) with a line naming the holes that fit no size, or, when the selection holds no usable hole, one size
    and one length drop-down. `Insert` makes one `apply_insert` call (or one `insert_standard_part`), writes a
    status line, clears the viewport selection and leaves the pane as it is.

    `doc` is the AssemblyDoc, or a callable returning the current one. `viewport` supplies the selection
    (`viewport.selection`, refreshed on `selectionChanged`); with none, `set_picks` stands in for it. The plan is
    recomputed only when the type, an extra parameter or the selection changes. Everything runs without a modal
    loop: `select_type`, `set_param`, `set_row_length`, `set_free_size`, `set_free_length`, `insert`.

    Object names: sp_tree, sp_params, sp_sizes (the table), sp_skipped, sp_size, sp_length, sp_insert, sp_status;
    sp_thread, sp_point, sp_standard for the extra parameters and sp_row_length_<size> for each row's length drop-down."""

    inserted = Signal(object)

    def __init__(self, doc, viewport=None, parent=None):
        super().__init__(parent)
        self.setObjectName("standardPartsPane")
        self._doc = doc
        self.viewport = viewport
        self._picks = []
        self.type_id = None
        self.plan = None
        self.free = True
        self.last_result = None
        self._building = False
        self._param_widgets = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.tree = QTreeWidget()
        self.tree.setObjectName("sp_tree")
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumHeight(130)
        self.tree.currentItemChanged.connect(self._on_tree)
        lay.addWidget(self.tree)

        self.params_box = QWidget()
        self.params_box.setObjectName("sp_params")
        self.params_form = QFormLayout(self.params_box)
        self.params_form.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.params_box)

        self.table = QTableWidget(0, 2)
        self.table.setObjectName("sp_sizes")
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setMinimumHeight(90)
        lay.addWidget(self.table)

        self.skipped = QLabel("")
        self.skipped.setObjectName("sp_skipped")
        self.skipped.setWordWrap(True)
        lay.addWidget(self.skipped)

        self.free_box = QWidget()
        ff = QFormLayout(self.free_box)
        ff.setContentsMargins(0, 0, 0, 0)
        self.size = QComboBox()
        self.size.setObjectName("sp_size")
        self.size.currentIndexChanged.connect(self._on_free_size)
        ff.addRow("Size", self.size)
        self.length = QComboBox()
        self.length.setObjectName("sp_length")
        self.length_label = QLabel("Length (mm)")
        ff.addRow(self.length_label, self.length)
        lay.addWidget(self.free_box)

        self.quantity = QSpinBox()
        self.quantity.setObjectName("sp_quantity")
        self.quantity.setRange(1, 999)
        self.quantity.setToolTip("How many to insert. With holes picked, the hole count decides.")
        self.quantity_row = QWidget()
        qf = QFormLayout(self.quantity_row)
        qf.setContentsMargins(0, 0, 0, 0)
        qf.addRow("Quantity", self.quantity)
        lay.addWidget(self.quantity_row)

        self.insert_btn = QPushButton("Insert")
        self.insert_btn.setObjectName("sp_insert")
        self.insert_btn.setDefault(True)
        self.insert_btn.clicked.connect(lambda: self.insert())
        lay.addWidget(self.insert_btn)
        self.status = QLabel("")
        self.status.setObjectName("sp_status")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        self._fill_tree()
        self._show_mode()
        if viewport is not None:
            viewport.selectionChanged.connect(self._on_selection)

    # ------------------------------------------------------------------ inputs
    @property
    def doc(self):
        return self._doc() if callable(self._doc) else self._doc

    def picks(self):
        """The picks the plan is made from: the viewport's selection, else those set with `set_picks`."""
        return list(self.viewport.selection) if self.viewport is not None else list(self._picks)

    def set_picks(self, picks):
        """For a pane without a viewport: set the picks and rebuild."""
        self._picks = list(picks)
        self.refresh()

    def _on_selection(self):
        self.refresh()

    # ------------------------------------------------------------------ type tree
    def _fill_tree(self):
        cats = {}
        for type_id, cls, _info in pyfeature.standard_parts():
            cats.setdefault(getattr(cls, "category", "") or "Other", []).append((type_id, cls))
        order = [c for c in CATEGORY_ORDER if c in cats] + sorted(c for c in cats if c not in CATEGORY_ORDER)
        for c in order:
            top = QTreeWidgetItem([c])
            top.setFlags(Qt.ItemIsEnabled)
            for type_id, cls in sorted(cats[c], key=lambda t: t[1].label):
                it = QTreeWidgetItem([cls.label])
                it.setData(0, ROLE_TYPE, type_id)
                top.addChild(it)
            self.tree.addTopLevelItem(top)
            top.setExpanded(True)

    def type_items(self):
        """{category: [type id, ...]} as the tree shows it."""
        out = {}
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            out[top.text(0)] = [top.child(k).data(0, ROLE_TYPE) for k in range(top.childCount())]
        return out

    def select_type(self, type_id):
        """Choose a type, as a click in the tree does. Returns False for a type the tree does not list."""
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for k in range(top.childCount()):
                if top.child(k).data(0, ROLE_TYPE) == type_id:
                    self.tree.setCurrentItem(top.child(k))
                    return True
        return False

    def _on_tree(self, cur, _prev):
        tid = cur.data(0, ROLE_TYPE) if cur is not None else None
        self.type_id = tid
        self._build_params()
        self.refresh()

    def _cls(self):
        return pyfeature.standard_part(self.type_id) if self.type_id else None

    # ------------------------------------------------------------------ extra parameters
    def _build_params(self):
        while self.params_form.rowCount():
            self.params_form.removeRow(0)
        self._param_widgets = {}
        cls = self._cls()
        if cls is None:
            self.params_box.hide()
            return
        inst = cls()
        defaults = inst.default_params()
        for key, val in defaults.items():
            if key in ("size", "length"):
                continue
            if key == "standard":
                choices = list(inst.standards()) if hasattr(inst, "standards") else []
                if len(choices) < 2:
                    continue                        # one standard is no choice
                w = QComboBox()
                for c in choices:
                    w.addItem(str(c), c)
            elif key == "point":
                w = QComboBox()
                for c in POINT_CHOICES:
                    w.addItem(POINT_TEXT.get(c, c), c)
            elif isinstance(val, bool):
                w = QCheckBox()
                w.setChecked(val)
            else:
                continue
            if isinstance(w, QComboBox):
                k = w.findData(val)
                w.setCurrentIndex(max(k, 0))
                w.currentIndexChanged.connect(lambda _=0: self._on_param())
            else:
                w.toggled.connect(lambda _=False: self._on_param())
            w.setObjectName(PARAM_NAMES.get(key, f"sp_param_{key}"))
            self._param_widgets[key] = w
            self.params_form.addRow(PARAM_LABELS.get(key, key), w)
        self.params_box.setVisible(bool(self._param_widgets))

    def param_keys(self):
        """The extra parameters shown for the current type."""
        return list(self._param_widgets)

    def base_params(self):
        """{key: value} of the extra parameters (without size and length)."""
        out = {}
        for k, w in self._param_widgets.items():
            out[k] = w.currentData() if isinstance(w, QComboBox) else w.isChecked()
        return out

    def set_param(self, key, value):
        w = self._param_widgets[key]
        if isinstance(w, QComboBox):
            w.setCurrentIndex(max(w.findData(value), 0))
        else:
            w.setChecked(bool(value))

    def _on_param(self):
        if not self._building:
            self.refresh()

    # ------------------------------------------------------------------ plan and table
    def refresh(self):
        """Rebuild the plan and what shows it. Called for a type, parameter or selection change."""
        self._building = True
        try:
            self.plan = None
            self.skipped.setText("")
            self.skipped.hide()
            self.table.setRowCount(0)
            cls = self._cls()
            doc = self.doc
            if cls is not None and doc is not None and self.picks():
                from ..standard_insert import plan_insert
                try:
                    self.plan = plan_insert(doc, self.type_id, self.picks(), self.base_params())
                except Exception as e:
                    self.plan = {"type": self.type_id, "groups": [], "unmatched": []}
                    self.skipped.setText(f"Could not read the selection: {e}")
                    self.skipped.show()
            self.free = not (self.plan and self.plan["groups"])
            if not self.free:
                self._fill_table()
            else:
                self._fill_free()
            if self.plan and self.plan["unmatched"]:
                self.skipped.setText(self.skipped_text(self.plan["unmatched"]))
                self.skipped.show()
            self._show_mode()
        finally:
            self._building = False

    @staticmethod
    def skipped_text(targets):
        n = len(targets)
        dias = sorted({round(float(t["diameter"]), 3) for t in targets})
        return (f"Skipped {n} hole{'s' if n != 1 else ''} (no size fits): "
                + ", ".join(f"{d:.1f} mm" if abs(d * 10 - round(d * 10)) < 1e-6 else f"{d:g} mm" for d in dias))

    def _has_lengths(self, groups):
        return any(g["lengths"] for g in groups)

    def _fill_table(self):
        groups = self.plan["groups"]
        with_len = self._has_lengths(groups)
        self.table.setColumnCount(3 if with_len else 2)
        self.table.setHorizontalHeaderLabels(["Size", "Holes"] + (["Length (mm)"] if with_len else []))
        self.table.setRowCount(len(groups))
        for r, g in enumerate(groups):
            self.table.setItem(r, 0, QTableWidgetItem(g["size"]))
            self.table.setItem(r, 1, QTableWidgetItem(str(len(g["targets"]))))
            if with_len:
                cb = QComboBox()
                cb.setObjectName(f"sp_row_length_{g['size']}")
                for v in g["lengths"]:
                    cb.addItem(length_text(v), v)
                k = cb.findData(g["default_length"])
                cb.setCurrentIndex(max(k, 0))
                self.table.setCellWidget(r, 2, cb)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Stretch)
        self.table.resizeRowsToContents()

    def rows(self):
        """[(size, hole count, length or None)] of the table as it stands (empty in free mode)."""
        out = []
        for r in range(self.table.rowCount() if not self.free else 0):
            cb = self.table.cellWidget(r, 2)
            out.append((self.table.item(r, 0).text(), int(self.table.item(r, 1).text()),
                        cb.currentData() if cb is not None else None))
        return out

    def lengths_by_size(self):
        return {s: v for s, _n, v in self.rows() if v is not None}

    def has_length_column(self):
        return not self.free and self.table.columnCount() == 3

    def set_row_length(self, size, length):
        """Choose `length` in the length drop-down of the row for `size`."""
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0).text() == size:
                cb = self.table.cellWidget(r, 2)
                k = cb.findData(length)
                if k < 0:
                    raise ValueError(f"{length} is not offered for {size}")
                cb.setCurrentIndex(k)
                return
        raise ValueError(f"no row for {size}")

    # ------------------------------------------------------------------ free instance
    def _fill_free(self):
        cls = self._cls()
        self.size.blockSignals(True)
        self.size.clear()
        if cls is not None:
            try:
                for s in cls().sizes(**self._kw(cls().sizes)):
                    self.size.addItem(s, s)
            except Exception:
                pass
        self.size.blockSignals(False)
        self._fill_free_lengths()

    def _kw(self, fn):
        import inspect
        have = inspect.signature(fn).parameters
        return {k: v for k, v in self.base_params().items() if k in have and k != "size"}

    def _fill_free_lengths(self):
        from ..standard_insert import _default_length, _nominal_diameter
        cls = self._cls()
        size = self.size.currentData()
        lens = []
        if cls is not None and size is not None:
            lens = type_lengths(self.type_id, size, self.base_params())
        self.length.blockSignals(True)
        self.length.clear()
        for v in lens:
            self.length.addItem(length_text(v), v)
        if lens:
            d = _default_length(lens, _nominal_diameter(cls(), size))
            self.length.setCurrentIndex(max(self.length.findData(d), 0))
        self.length.blockSignals(False)

    def _on_free_size(self, _i=0):
        if not self._building:
            self._fill_free_lengths()
            self._show_mode()

    def set_free_size(self, size):
        k = self.size.findData(size)
        if k < 0:
            raise ValueError(f"{size} is not offered")
        self.size.setCurrentIndex(k)

    def set_free_length(self, length):
        k = self.length.findData(length)
        if k < 0:
            raise ValueError(f"{length} is not offered")
        self.length.setCurrentIndex(k)

    def _show_mode(self):
        """Which controls show: nothing before a type is chosen; the table or the two drop-downs after.
        Uses setHidden, which holds before the pane itself is shown."""
        chosen = self.type_id is not None
        self.table.setHidden(not (chosen and not self.free))
        self.free_box.setHidden(not (chosen and self.free))
        if chosen and self.free:
            has = self.length.count() > 0
            self.length.setHidden(not has)
            self.length_label.setHidden(not has)
        self.insert_btn.setEnabled(chosen and (not self.free or self.size.count() > 0))
        # holes picked: the hole count decides, so the field shows it and cannot be edited
        self.quantity_row.setHidden(not chosen)
        self.quantity.setEnabled(chosen and self.free)
        if chosen and not self.free:
            self.quantity.setValue(sum(len(g["targets"]) for g in self.plan["groups"]))

    # ------------------------------------------------------------------ insert
    def insert(self):
        """What the Insert button does. Returns the apply_insert result (or {"instances": [iid], "mates": []}
        for a free instance), or None when nothing was inserted (the status line says why)."""
        doc, cls = self.doc, self._cls()
        if doc is None or cls is None:
            return None
        from .. import kernel as K
        label = cls.label
        base = self.base_params()
        try:
            if not self.free:
                from ..standard_insert import apply_insert
                res = apply_insert(doc, self.plan, self.lengths_by_size(), base)
                n = len(res["instances"])
                msg = f"Inserted {n} {label} in {len(res['mates'])} hole{'s' if len(res['mates']) != 1 else ''}"
            else:
                size = self.size.currentData()
                if size is None:
                    return None
                params = dict(base, size=size)
                if self.length.count():
                    params["length"] = self.length.currentData()
                from ..standard_insert import insert_free
                res = insert_free(doc, self.type_id, params, self.quantity.value())
                msg = f"Inserted {len(res['instances'])} {label}"
        except K.KernelError as e:
            self.status.setText(f"Not inserted: {e}")
            return None
        self.last_result = res
        self.status.setText(msg)
        self.inserted.emit(res)
        if self.viewport is not None:
            self.viewport.clear_selection()
        else:
            self.set_picks([])
        return res
