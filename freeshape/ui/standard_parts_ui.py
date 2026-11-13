"""The UI side of Standard parts in an Assembly.

`StandardPartEditDialog` edits size, length and `Model the thread` of one or more Standard part instances (card I3.5).
`StandardPartsPane`, the Standard parts tab of the Insert panel, is added to this module by card I3.4.

Nothing here knows a particular Plugin: the fields come from each type's `sizes()`, `lengths(size)` and
`default_params()`.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
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
