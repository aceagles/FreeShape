"""Floating, Onshape-style feature dialogs (green check / red X), built from small schemas."""
from __future__ import annotations

import copy
from types import SimpleNamespace
import json

import numpy as np
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QBoxLayout, QLabel, QToolButton, QLineEdit, QComboBox, QCheckBox,
    QListWidget, QListWidgetItem, QWidget, QButtonGroup, QSizePolicy, QGraphicsDropShadowEffect, QTabWidget,
    QPushButton,
)

from .. import kernel as K
from .. import features as F
from .. import assembly as AS
from .icons import icon
from . import preview_rows as PR
from . import theme
from .numparse import parse_number, fmt, has_vars
from .viewport import Pick
from ..picking import make_item, region_touching  # noqa: F401 -- region_touching: re-exported, tests import it here
from .. import dialog_schema as SCHEMA
from ..dialog_schema import (  # noqa: F401 -- moved to a Qt-free module (I5.1); tests and plugins import them here
    apply_preselection,
    on_field_set,
    connector_stamp,
    OP_LABELS,
    SURFACE_OP_LABELS,
    CREATE_LABELS,
    THIN_CREATE_LABELS,
    _thin_fields,
    _fill_item,
    _fill_edge,
    _split_item,
    _same,
    _isnum,
    _plane_info,
    _axis_info,
    _hole_info,
    _num,
    _choice,
    _check,
    _sel,
    _sel_list,
    SCOPE_OPS,
    SCOPE_FEATURES,
    SWEEP_CONTROLS,
    LOFT_CONDITIONS,
    LOFT_END_CHOICES,
    _LOFT_GUIDES_WHY,
    _LOFT_SURFACE_WHY,
    _BOUNDARY_TIP,
    _loft_surface,
    _loft_point,
    _loft_plain,
    _loft_unfold,
    _loft_fold,
    thicken_op_for,
    _scope,
    _ops,
    OWNER_PICK,
    OWNER_SAME,
    _pattern_of,
    _UP_TO_FIELDS,
    SCHEMAS,
    params_to_fields,
    fields_to_params,
)


def _show(v):
    """Text for a number field: expressions stay as typed, numbers are tidied."""
    return v if isinstance(v, str) else fmt(v)


# ---------------------------------------------------------------------- pick <-> ref conversion

def _cross(c, s):
    """A three-axis cross of half-length `s` at point `c`, as one polyline."""
    c = np.asarray(c, float)
    pts = []
    for d in (np.array((s, 0, 0)), np.array((0, s, 0)), np.array((0, 0, s))):
        pts += [c - d, c + d, c]
    return np.array(pts)


def fill_gap_points(state, params):
    """The loose end points of the boundary a Fill's edges make (empty when it closes or an edge is missing)."""
    es = []
    try:
        for e in params.get("edges") or []:
            ref = e.get("ref") or {}
            if ref.get("kind") == "edge":
                es.append(F.resolve(state, ref["edge"], "edge")[1])
            else:
                es += [x for _, x in F._ref_edges(state, ref)[1]]
        return K.boundary_open_ends(es) if es else []
    except (K.KernelError, KeyError):
        return []



def pick_to_item(vp, pick: Pick, want: set, state=None):
    """Convert a viewport pick into a (kind, ref) item for a selection field.

    `state` is the ModelState the viewport is showing; with it a picked face, edge or vertex stores
    the element's name next to its signature (a part without a map gives a signature-only reference).
    The conversion itself is `picking.make_item`, which the server shares; the viewport gives it only its part
    shapes and its sketches."""
    sketches = {}
    for sfid, sr, _ in getattr(vp, "sketches", ()):       # a stand-in viewport may hold no sketches
        sketches.setdefault(sfid, sr)
    return make_item(getattr(vp, "part_shapes", {}), sketches, pick.kind, pick.key, want,
                     (lambda pid: state.named(pid)) if state is not None else None,
                     getattr(state, "engine_names", None))


class _PickState:
    """What `dialog_schema.apply_preselection` reads, for the desktop: the shapes and sketches the viewport shows,
    and everything else (parts, vars, named, engine_names) from the model state under the dialog."""

    def __init__(self, vp, base_state):
        self.part_shapes = getattr(vp, "part_shapes", {})
        self.sketches = {}
        for sfid, sr, _ in getattr(vp, "sketches", ()):
            self.sketches.setdefault(sfid, sr)
        self._base = base_state

    def __getattr__(self, name):
        return getattr(self._base, name)


def item_label(doc, item):
    kind, ref = item
    if kind == "region":
        return "Sketch region"
    if kind == "face":
        return "Face"
    if kind == "edge":
        return "Edge"
    if kind == "vertex":
        return "Vertex"
    if kind == "sketch":                 # a Mate connector on sketch geometry (ADR 0011)
        f = doc.feature(ref.get("sketch"))
        what = {"point": "point", "centre": "centre", "end1": "end 1", "end2": "end 2", "mid": "middle"}
        return f"{f['name'] if f else 'Sketch'} {ref.get('entity')} ({what.get(ref.get('role'), ref.get('role'))})"
    if kind == "part":
        p = doc.state.parts.get(ref)
        return p.name if p else ref
    if kind == "feature":
        f = doc.feature(ref)
        return f["name"] if f else ref
    if kind == "plane":
        if ref["kind"] == "std":
            return f"{ref['name']} plane"
        if ref["kind"] == "plane":
            f = doc.feature(ref["fid"])
            return f["name"] if f else "Plane"
        return "Planar face"
    if kind == "path":
        return "Sketch curves" if ref["kind"] == "sketch_chain" else "Edge"
    if kind == "sketch_line":            # the Axis tool's refs
        f = doc.feature(ref.get("sketch"))
        return f"{f['name'] if f else 'Sketch'} {ref.get('etype', 'line')}"
    if kind == "connector":
        f = doc.feature(ref.get("fid"))
        return f["name"] if f else "Mate connector"
    if kind == "axis":
        if ref["kind"] == "axis":        # an axis feature (the Axis tool)
            f = doc.feature(ref.get("fid"))
            return f["name"] if f else "Axis"
        if ref["kind"] == "connector":   # a mate connector's axis
            f = doc.feature(ref.get("fid"))
            return f"{f['name'] if f else 'Mate connector'} {(ref.get('axis') or 'z').upper()}"
        if ref["kind"] == "std":
            return f"{ref['name']} axis"
        if ref["kind"] == "sketch_line":
            return "Sketch line"
        if ref["kind"] == "sketch_circle":
            return "Sketch circle"
        if ref["kind"] == "face":
            try:
                _, face = F.resolve(doc.state, ref["face"], "face")
                return "Cylinder axis" if K.face_axis(face) is not None and \
                    K.face_info(face).get("type") == int(K.GeomAbs_Cylinder) else "Face axis"
            except K.KernelError:
                return "Face axis"
        return "Edge"
    return kind


def item_geometry(state, item, plane_half=40.0):
    """World geometry used to highlight a selected item: ('mesh', Mesh) or ('poly', ndarray)."""
    kind, ref = item
    try:
        if kind == "region":
            face = F.resolve_regions(state, [ref])[0]
            return ("mesh", K.tessellate(face))
        if kind == "face":
            _, face = F.resolve(state, ref, "face")
            return ("mesh", K.tessellate(face))
        if kind == "edge":
            _, e = F.resolve(state, ref, "edge")
            return ("poly", K.discretize_edge(e))
        if kind == "vertex":
            c = F.resolve_point(state, ref)
            s = plane_half * 0.06
            seg = [np.array(a) for a in ((s, 0, 0), (0, s, 0), (0, 0, s))]
            pts = []
            for d in seg:
                pts += [c - d, c + d, c]
            return ("poly", np.array(pts))
        if kind == "part":
            p = state.parts.get(ref)
            return ("mesh", K.tessellate(p.shape)) if p else None
        if kind == "plane":
            pl = F.resolve_plane(state, ref)
            h = plane_half
            return ("poly", np.array([pl.to_world(u, v) for u, v in ((-h, -h), (h, -h), (h, h), (-h, h), (-h, -h))]))
        if kind == "path":
            w = F._path_wire(state, [ref])
            m = K.Mesh(np.zeros((0, 3), np.float32), np.zeros((0, 3), np.float32), np.zeros((0, 3), np.uint32),
                       np.zeros(0, np.int32), [], [K.discretize_edge(e) for e in K.edges(w)])
            return ("mesh", m)
        if kind == "axis":
            a, d = F.resolve_axis(state, ref)
            if ref.get("kind") == "connector":       # short, from the connector origin along that axis
                return ("poly", np.array([a, a + d * plane_half * 0.5]))
            return ("poly", np.array([a - d * plane_half * 1.5, a + d * plane_half * 1.5]))
        if kind == "sketch_line":        # the Axis tool's refs
            a, d = F.resolve_axis(state, ref)
            return ("poly", np.array([a - d * plane_half * 1.5, a + d * plane_half * 1.5]))
        if kind == "sketch":
            c = F._axis_point(state, kind, ref)
            s = plane_half * 0.06
            return ("poly", np.array([c - (s, 0, 0), c + (s, 0, 0), c, c - (0, s, 0), c + (0, s, 0), c,
                                      c - (0, 0, s), c + (0, 0, s)]))
        if kind == "connector":
            c, _d = F._connector_axis(state, ref.get("fid"), "z")
            s = plane_half * 0.06
            return ("poly", np.array([c - (s, 0, 0), c + (s, 0, 0), c, c - (0, s, 0), c + (0, s, 0), c,
                                      c - (0, 0, s), c + (0, 0, s)]))
    except K.KernelError:
        return None
    return None


# ---------------------------------------------------------------------- selection field widget

class _RowWidget(QWidget):
    """A selection-list row that holds a number box; tells the field when the pointer enters it."""
    entered = Signal()

    def enterEvent(self, e):
        self.entered.emit()
        super().enterEvent(e)


class SelectionField(QFrame):
    """A list of picked items. With `number` each row also has a number box (`numbers[i]`: a float, an expression
    string, or None for "the default", which the box shows as `default_text()`); `number_parser(text)` reads
    a box. Another field can reuse this: map `items` and `numbers` in params_to_fields / fields_to_params."""
    activated = Signal(object)
    changed = Signal()
    number_changed = Signal()
    hovered = Signal(int)

    def __init__(self, key, label, want, multi=True, parent=None, number=False):
        super().__init__(parent)
        self.setObjectName("selField")
        self.key, self.want, self.multi = key, set(want), multi
        self.items: list = []
        self.number = number
        self.numbers: list = []
        self.number_parser = None
        self.default_text = lambda: ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(2)
        top = QHBoxLayout()
        self.title = QLabel(label)
        self.title.setWordWrap(True)
        self.title.setStyleSheet("color:#4b5563;")
        top.addWidget(self.title, 1)
        self.clear_btn = QToolButton()
        self.clear_btn.setText("✕")
        self.clear_btn.setToolTip("Clear selections")
        self.clear_btn.clicked.connect(self.clear)
        top.addWidget(self.clear_btn)
        lay.addLayout(top)
        self.list = QListWidget()
        self.list.setFixedHeight(22)
        self.extra = 0              # extra height (px) the dialog has been dragged taller by
        self._base_h = 22
        self.list.setStyleSheet("QListWidget { background: transparent; }")
        self.list.itemDoubleClicked.connect(self._remove_item)
        if number:
            self.list.setMouseTracking(True)
            self.list.itemEntered.connect(lambda it: self.hovered.emit(self.list.row(it)))
        lay.addWidget(self.list)
        self.set_active(False)

    def leaveEvent(self, e):
        if self.number:
            self.hovered.emit(-1)
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        self.activated.emit(self)

    def set_active(self, a):
        self.setProperty("active", "true" if a else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def refresh(self, doc, missing=()):
        self.list.clear()
        if not self.items:
            it = QListWidgetItem("Click to select" if self.property("active") == "true" else "—")
            it.setForeground(QColor("#8a94a3"))
            self.list.addItem(it)
        if self.number:
            self.numbers = (self.numbers + [None] * len(self.items))[:len(self.items)]
        for i, item in enumerate(self.items):
            it = QListWidgetItem("" if self.number else item_label(doc, item))
            if i in missing:
                it.setForeground(QColor("#d93025"))
                it.setToolTip("Reference could not be found — click \u2715 to remove")
            else:
                it.setToolTip("Click \u2715 (or double-click) to remove")
            self.list.addItem(it)
            if self.number:
                self._number_row(it, i, item_label(doc, item))
            else:
                self._remove_button_row(it, i)
        n = max(1, len(self.items))
        self._base_h = min(6, n) * (28 if self.number else 20) + 4
        self.list.setFixedHeight(self._base_h + self.extra)

    def _number_row(self, it, i, text):
        w = _RowWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(2, 0, 2, 0)
        h.addWidget(QLabel(f"{text} {i + 1}"), 1)
        ed = QLineEdit(self.default_text() if self.numbers[i] is None else _show(self.numbers[i]))
        ed.setFixedWidth(80)
        ed.setAlignment(Qt.AlignRight)
        ed.setToolTip("Radius at this vertex. Expressions and variables work: '#r * 2'. "
                      "Empty means the default radius. The radius changes smoothly between vertices.")
        ed.textEdited.connect(lambda t, i=i, ed=ed: self._number_edited(i, ed, t))
        h.addWidget(ed)
        h.addWidget(self._remove_button(i))
        w.entered.connect(lambda i=i: self.hovered.emit(i))
        it.setSizeHint(QSize(0, 28))
        self.list.setItemWidget(it, w)

    def _remove_button(self, i):
        """The \u2715 at the end of row i: removes that pick alone (the header's \u2715 clears them all)."""
        b = QToolButton()
        b.setObjectName("rowRemove")
        b.setText("\u2715")
        b.setToolTip("Remove")
        b.setAutoRaise(True)
        b.setFixedSize(18, 18)
        b.clicked.connect(lambda _=False, i=i: self._remove_row(i))
        return b

    def _remove_button_row(self, it, i):
        """A plain row keeps its text on the item; the widget over it only holds the remove button, on the right."""
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 2, 0)
        h.addStretch(1)
        h.addWidget(self._remove_button(i))
        self.list.setItemWidget(it, w)

    def _remove_row(self, row):
        if 0 <= row < len(self.items):
            self.remove_at(row)
            self.changed.emit()

    def _number_edited(self, i, ed, txt):
        txt = txt.strip()
        v = None if not txt else (self.number_parser(txt) if self.number_parser else parse_number(txt))
        bad = bool(txt) and (v is None or v <= 0)
        ed.setProperty("invalid", "true" if bad else "false")
        ed.style().unpolish(ed)
        ed.style().polish(ed)
        if bad or i >= len(self.numbers):
            return
        self.numbers[i] = None if not txt else (txt if has_vars(txt) else v)
        self.number_changed.emit()

    def remove_at(self, row):
        if 0 <= row < len(self.items):
            self.items.pop(row)
            if row < len(self.numbers):
                self.numbers.pop(row)

    def set_extra(self, px):
        self.extra = max(0, int(px))
        self.list.setFixedHeight(self._base_h + self.extra)
        self.layout().invalidate()          # Qt re-lays nested widgets lazily; the dialog needs the new size now
        self.updateGeometry()

    def _remove_item(self, it):
        row = self.list.row(it)
        if 0 <= row < len(self.items):
            self.remove_at(row)
            self.changed.emit()

    def clear(self):
        self.items = []
        self.numbers = []
        self.activated.emit(self)           # an emptied field is the one the next click is meant for
        self.changed.emit()

    def toggle(self, item):
        kind, ref = item
        if kind == "edges":
            for r in ref:
                self.toggle(("edge", r))
            return
        for i, ex in enumerate(self.items):
            if _same(ex, item):
                if self.number:             # a picked vertex already listed selects its row; double-click removes it
                    self.list.setCurrentRow(i)
                    return
                self.items.pop(i)
                self.changed.emit()
                return
        if not self.multi:
            self.items = []
        self.items.append(item)
        if self.number:
            self.numbers = (self.numbers + [None] * len(self.items))[:len(self.items)]
        self.changed.emit()


class GuideListField(SelectionField):
    """A selection list where one pick is one entry, shown as a numbered row with move-up, move-down and remove
    buttons. A loft's guides use it (`guides`: one inner list of references per entry); the Boundary surface's U
    and V lists can reuse it. `items` holds (kind, ref) as for any field; an entry that stands for several
    references (a saved guide of more than one path piece) is ("group", [refs]) and is kept as it is."""

    def __init__(self, key, label, want, multi=True, parent=None, number=False):
        super().__init__(key, label, want, True, parent, False)
        self.list.itemDoubleClicked.disconnect()
        self.list.setToolTip("Each pick is one entry. Use the arrows to reorder and \u2715 to remove.")

    def move(self, row, delta):
        """Move an entry up (delta -1) or down (+1); False when it is already at that end."""
        new = row + delta
        if not (0 <= row < len(self.items) and 0 <= new < len(self.items)):
            return False
        self.items[row], self.items[new] = self.items[new], self.items[row]
        self.changed.emit()
        return True

    def _row_label(self, doc, item, i):
        kind, ref = item
        what = f"{len(ref)} pieces" if kind == "group" else item_label(doc, item)
        return f"{i + 1}. {what}"

    def refresh(self, doc, missing=()):
        self.list.clear()
        if not self.items:
            it = QListWidgetItem("Click to select" if self.property("active") == "true" else "—")
            it.setForeground(QColor("#8a94a3"))
            self.list.addItem(it)
        for i, item in enumerate(self.items):
            it = QListWidgetItem("")
            self.list.addItem(it)
            w = _RowWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(2, 0, 2, 0)
            lab = QLabel(self._row_label(doc, item, i))
            if i in missing:
                lab.setStyleSheet("color:#d93025;")
            h.addWidget(lab, 1)
            for text, tip, fn, on in (("\u25b2", "Move up", lambda i=i: self.move(i, -1), i > 0),
                                      ("\u25bc", "Move down", lambda i=i: self.move(i, 1), i < len(self.items) - 1),
                                      ("\u2715", "Remove", lambda i=i: self._remove_row(i), True)):
                b = QToolButton()
                b.setText(text)
                b.setToolTip(tip)
                b.setEnabled(on)
                b.clicked.connect(lambda _=False, fn=fn: fn())
                h.addWidget(b)
            it.setSizeHint(QSize(0, 28))
            self.list.setItemWidget(it, w)
        self._base_h = min(6, max(1, len(self.items))) * 28 + 4
        self.list.setFixedHeight(self._base_h + self.extra)


def fillet_vertex_ends(state, edge_refs):
    """Positions of the vertices a variable fillet of these edges can set a radius at: the ends of the edges and of
    the edges tangent to them that the fillet pulls into the contour. [] when the edges do not resolve."""
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
    out = []
    try:
        for pid, es in F._group_by_part(state, edge_refs, "edge").items():
            mk = BRepFilletAPI_MakeFillet(state.parts[pid].shape)
            for e in es:
                mk.Add(e)
            for ic in range(1, mk.NbContours() + 1):
                out += [K._vertex_xyz(v) for v in K._contour_vertices(mk, ic)]
    except Exception:
        return []
    return out



# ---------------------------------------------------------------------- the dialog

class _ResizableFrame(QFrame):
    """A floating panel over the viewport that can be resized by dragging its right edge, bottom edge or
    bottom-right corner. Extra height goes to the selection lists (see adjustSize); the width is remembered
    for the next dialog."""
    GRIP = 9                    # px inside the edge that grabs the resize
    _remembered_width: dict = {}

    def _init_resizable(self, default_width, min_width=240):
        self._w = self._remembered_width.get(type(self).__name__, default_width)
        self._min_w = min_width
        self._user_h = 0
        self._rz = None
        self.setMouseTracking(True)
        self.setFixedWidth(self._w)

    def _fit(self):
        lay = self.layout()
        if lay is not None:
            lay.invalidate()
            lay.activate()
        QFrame.adjustSize(self)

    def adjustSize(self):
        fields = getattr(self, "fields", {})
        for f in fields.values():
            f.set_extra(0)
        self._fit()
        if self._user_h > self.height():
            vis = [f for f in fields.values() if f.isVisibleTo(self)]
            if vis:
                extra = (self._user_h - self.height()) // len(vis)
                for f in vis:
                    f.set_extra(extra)
                self._fit()

    def _grip_at(self, pos):
        return pos.x() >= self.width() - self.GRIP, pos.y() >= self.height() - self.GRIP

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            right, bottom = self._grip_at(e.position())
            if right or bottom:
                self._rz = (right, bottom, e.globalPosition(), self.width(), self.height())
                e.accept()
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._rz is not None:
            right, bottom, g0, w0, h0 = self._rz
            d = e.globalPosition() - g0
            par = self.parentWidget()
            if right:
                maxw = max(self._min_w, (par.width() - self.x() - 12) if par else 4000)
                self._w = int(max(self._min_w, min(maxw, w0 + d.x())))
                self.setFixedWidth(self._w)
            if bottom:
                maxh = max(120, (par.height() - self.y() - 12) if par else 4000)
                self._user_h = int(max(0, min(maxh, h0 + d.y())))
            self.adjustSize()
            e.accept()
            return
        right, bottom = self._grip_at(e.position())
        self.setCursor(Qt.SizeFDiagCursor if (right and bottom) else Qt.SizeHorCursor if right
                       else Qt.SizeVerCursor if bottom else Qt.ArrowCursor)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._rz is not None:
            self._rz = None
            self._remembered_width[type(self).__name__] = self._w
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def leaveEvent(self, e):
        if self._rz is None:
            self.unsetCursor()
        super().leaveEvent(e)


class FeatureDialog(_ResizableFrame):
    accepted = Signal()
    rejected = Signal()

    def __init__(self, main, feature, is_new, index, preselection=()):
        super().__init__(main.viewport)
        self.setObjectName("featureDialog")
        self._init_resizable(300)
        self.fields: dict = {}
        self.main = main
        self.doc = main.doc
        self.feature = copy.deepcopy(feature)
        self.ftype = feature["type"]
        self.is_new = is_new
        self.index = index
        self.base_state = self.doc.state_before(index)
        # settings added in later versions get their defaults when an older feature is edited
        try:
            from ..features import default_params
            for k_, v_ in default_params(self.ftype, self.base_state).items():
                self.feature["params"].setdefault(k_, v_)
        except Exception:
            pass
        if self.ftype == "loft":
            _loft_unfold(self.feature["params"])
            if F.front_kind(self.feature):
                self.feature["params"]["guides_on"] = True       # the V list is always there: _loft_fold keeps it
        self.schema = SCHEMAS.get(F.front_kind(self.feature) or self.ftype, [])
        if self.ftype.startswith("plugin:"):
            from .. import plugins
            self.schema = plugins.dialog_schema(self.ftype)
        self.fields: dict[str, SelectionField] = {}
        self.widgets = {}
        self._info_rows = []
        self.rows = []
        self.active_field = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._do_preview)
        sh = QGraphicsDropShadowEffect(self)
        sh.setBlurRadius(18)
        sh.setOffset(0, 3)
        sh.setColor(QColor(0, 0, 0, 60))
        self.setGraphicsEffect(sh)
        self._build()
        # initial field contents from params, then from the preselection
        for k, items in params_to_fields(self.ftype, self.feature["params"]).items():
            if k in self.fields:
                self.fields[k].items = list(items)
            elif k.endswith("_numbers") and k[:-8] in self.fields:
                self.fields[k[:-8]].numbers = list(items)
        if is_new and preselection:
            self._apply_preselection(preselection)
        if self.ftype == "rib" and is_new:
            self._default_rib_part()
        if self.ftype == "mate_connector":       # hover a face or edge: the same snap points the Assembly offers
            main.viewport.snap_provider = self.snap_candidates
        self._sync_visibility()              # also picks the field that takes the first click
        self.schedule_preview()
        self.adjustSize()
        self.move(12, 12)
        self.show()

    # ------------------------------------------------------------------ ui
    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(6)
        head = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(icon(F.door_kind(self.feature)).pixmap(20, 20))
        head.addWidget(ic)
        self.title = QLineEdit(self.feature["name"])
        self.title.setStyleSheet("QLineEdit { border: none; font-weight: 700; background: transparent; }"
                                 "QLineEdit:focus { border: 1px solid #1f6fd1; background: white; }")
        head.addWidget(self.title, 1)
        ok = QToolButton()
        ok.setObjectName("okButton")
        ok.setText("✓")
        ok.setToolTip("Accept (Enter)")
        ok.clicked.connect(self.accept)
        self.ok_button = ok
        cancel = QToolButton()
        cancel.setObjectName("cancelButton")
        cancel.setText("✕")
        cancel.setToolTip("Cancel (Esc)")
        cancel.clicked.connect(self.reject)
        head.addWidget(ok)
        head.addWidget(cancel)
        lay.addLayout(head)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color:#e3e6ea;")
        lay.addWidget(line)
        p = self.feature["params"]
        for spec in self.schema:
            w = self._make_row(spec, p)
            if w is not None:
                lay.addWidget(w)
                self.rows.append((spec, w))
        if self.ftype == "canvas":                       # I12.4
            self._add_calibrate(lay)
        self.err = QLabel("")
        self.err.setObjectName("dialogError")
        self.err.setWordWrap(True)
        self.err.hide()
        lay.addWidget(self.err)

    def _add_calibrate(self, lay):
        """Canvas: the Calibrate button and its two checkboxes (I12.4), as FreeCAD's task panel has them."""
        btn = QPushButton("Calibrate")
        btn.setObjectName("calibrateButton")
        btn.setToolTip("Click the two ends of a distance you know on the image, then type its real length")
        btn.clicked.connect(self.start_calibrate)
        self.calibrate_button = btn
        lay.addWidget(btn)
        self.calibrate_centre = QCheckBox("Centre on midpoint")
        self.calibrate_centre.setToolTip("Move the image so the middle of the line stays where it is")
        self.calibrate_snap = QCheckBox("Snap rotation to line")
        self.calibrate_snap.setToolTip("Turn the image so the line lies along an axis")
        for cb in (self.calibrate_centre, self.calibrate_snap):
            cb.setChecked(False)
            lay.addWidget(cb)

    def start_calibrate(self):
        """Put the 3D view in its two-click mode on this canvas (calibrate_mode.CalibrateMode)."""
        from .calibrate_mode import CalibrateMode
        vp = self.main.viewport
        self._timer.stop()
        self._do_preview()                               # the view holds this canvas as the dialog has it now
        if not any(c["id"] == self.feature["id"] for c in vp.canvases):
            self.main.status.showMessage("The canvas is not shown yet: fix what the dialog reports first", 5000)
            return
        vp.start_calibrating(CalibrateMode(
            vp, self.feature["id"], self.apply_calibration, lambda: self.main.status.showMessage("Calibrate cancelled", 3000),
            variables=self.base_state.vars, say=lambda t: self.main.status.showMessage(t, 0)))
        self.main.status.showMessage("Click one end of a distance you know on the image", 0)

    def apply_calibration(self, p0, p1, distance):
        """Scale (and with the checkboxes move and turn) the canvas in this dialog so the line p0 -> p1 measures
        `distance` mm: freeshape.canvas_calibrate.calibrate on the dialog's params, the same call the served dialog
        makes. Nothing is committed: accepting the dialog is the one undo step. Returns True when it was applied."""
        from .. import canvas_calibrate as CC
        p = self.feature["params"]
        have = {}
        for key in ("width", "height", "x", "y", "rotation"):
            raw = p.get(key)
            have[key] = parse_number(raw, angle=key == "rotation", variables=self.base_state.vars) \
                if raw is not None else None
        try:
            got = CC.calibrate(dict(p, **have), p0, p1, distance, self.calibrate_centre.isChecked(),
                               self.calibrate_snap.isChecked())
        except CC.CalibrateError as e:
            self.main.status.showMessage(str(e), 6000)
            return False
        for key in ("width", "height", "x", "y", "rotation"):
            if got[key] != have[key]:
                p[key] = got[key]
                if key in self.widgets:
                    self.widgets[key].setText(_show(got[key]))
                    self.widgets[key].setProperty("invalid", "false")
        self._sync_visibility()
        self.schedule_preview()
        self.main.status.showMessage("Calibrated", 3000)
        return True

    def _make_row(self, spec, p):
        t = spec["t"]
        if t == "ops":
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(0)
            grp = QButtonGroup(w)
            for val, lab in spec["options"]:
                b = QToolButton()
                b.setObjectName("segment")
                b.setText(lab)
                b.setCheckable(True)
                b.setChecked(p.get(spec["key"], spec.get("default")) == val)
                b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                b.clicked.connect(lambda _=False, v=val: self._set(spec["key"], v))
                grp.addButton(b)
                h.addWidget(b)
            return w
        if t == "split_tools":
            box = QWidget()
            v = QVBoxLayout(box)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(2)
            self._split_box, self._split_sig = box, None
            return box
        if t == "fill_rows":
            box = QWidget()
            v = QVBoxLayout(box)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(2)
            self._fill_box, self._fill_sig = box, None
            return box
        if t == "info":
            lab = QLabel("")
            lab.setWordWrap(True)
            lab.setStyleSheet("color:#2e9e44; font-weight:600;")
            self.widgets[spec["key"]] = lab
            self._info_rows.append((spec, lab))
            return lab
        if t == "sel":
            f = (GuideListField if spec.get("list") else SelectionField)(
                spec["key"], spec["label"], spec["want"], spec["multi"], number=spec.get("number", False))
            f.activated.connect(self.activate)
            f.changed.connect(self._fields_changed)
            if spec.get("tip"):
                f.setToolTip(spec["tip"])
            if f.number:
                f.number_parser = lambda t: parse_number(t, variables=self.base_state.vars)
                f.default_text = lambda: _show(self.feature["params"].get("radius", 0))
                f.number_changed.connect(self._numbers_changed)
                f.hovered.connect(self._hover_row)
            self.fields[spec["key"]] = f
            return f
        if t == "derived_tabs":
            return self._make_derived_tabs(spec, p)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        if t == "check":
            cb = QCheckBox(spec["label"])
            cb.setChecked(bool(p.get(spec["key"])))
            cb.toggled.connect(lambda v, k=spec["key"]: self._set(k, v))
            h.addWidget(cb)
            self.widgets[spec["key"]] = cb
            return row
        lab = QLabel(spec["label"])
        lab.setStyleSheet("color:#4b5563;")
        h.addWidget(lab, 1)
        if t == "num":
            ed = QLineEdit(_show(p.get(spec["key"], 0)))
            ed.setFixedWidth(110)
            ed.setAlignment(Qt.AlignRight)
            unit = spec["unit"]
            if unit:
                ed.setPlaceholderText(unit)
                ed.setToolTip(f"Value in {unit}. Expressions, units and variables work: '25/2', '1 in', '#wall * 2'.")

            def done(ed=ed, k=spec["key"], unit=unit, mn=spec.get("min"), signed=spec.get("signed"),
                     whole=bool(spec.get("int")), mx=spec.get("max")):
                txt = ed.text()
                v = parse_number(txt, angle=(unit == "deg"), variables=self.base_state.vars)
                bad = v is None or (mn is not None and v < mn) or (mx is not None and v > mx) \
                    or (signed and abs(v) < 1e-6)
                ed.setProperty("invalid", "true" if bad else "false")
                ed.style().unpolish(ed)
                ed.style().polish(ed)
                if not bad:
                    if has_vars(txt):
                        self._set(k, txt.strip())        # keep the expression so it follows the variable
                    else:
                        self._set(k, int(round(v)) if k in ("count", "count2") or whole else v)
            self.widgets[spec["key"]] = ed
            ed.textEdited.connect(lambda _t, f=done: f())
            ed.editingFinished.connect(lambda ed=ed, k=spec["key"]: ed.setText(_show(self.feature["params"].get(k, 0))))
            h.addWidget(ed)
            if unit:
                u = QLabel(unit)
                u.setStyleSheet("color:#8a94a3;")
                u.setFixedWidth(24)
                h.addWidget(u)
            return row
        if t == "text":
            ed = QLineEdit(str(p.get(spec["key"], "")))
            ed.setPlaceholderText(spec.get("placeholder", ""))
            ed.setFixedWidth(150)
            self.widgets[spec["key"]] = ed

            def text_done(_t=None, ed=ed, k=spec["key"], is_expr=spec.get("expr")):
                txt = ed.text().strip()
                bad = False
                if is_expr:
                    bad = parse_number(txt, variables=self.base_state.vars) is None
                ed.setProperty("invalid", "true" if bad else "false")
                ed.style().unpolish(ed)
                ed.style().polish(ed)
                self._set(k, txt)
            ed.textEdited.connect(text_done)
            h.addWidget(ed)
            return row
        if t == "choice":
            cb = QComboBox()
            for val, lab2 in spec["options"]:
                cb.addItem(lab2, val)
            idx = max(0, cb.findData(p.get(spec["key"], spec["options"][0][0])))
            cb.setCurrentIndex(idx)
            cb.currentIndexChanged.connect(lambda i, cb=cb, k=spec["key"]: self._set(k, cb.itemData(i)))
            h.addWidget(cb)
            self.widgets[spec["key"]] = cb
            return row
        if t == "sketch_choice":
            cb = QComboBox()
            cb.addItem("— select —", None)
            for fid, sr in self.base_state.sketches.items():
                f = self.doc.feature(fid)
                if f and any(e["type"] in ("point", "circle") for e in sr.sketch.entities.values()):
                    cb.addItem(f["name"], fid)
            if p.get(spec["key"]) is None and cb.count() > 1:
                p[spec["key"]] = cb.itemData(cb.count() - 1)
                self._default_hole_size(p)
            cb.setCurrentIndex(max(0, cb.findData(p.get(spec["key"]))))
            cb.currentIndexChanged.connect(lambda i, cb=cb, k=spec["key"]: self._choose_sketch(cb.itemData(i)))
            cb.setToolTip("Holes go at this sketch's points and circle centres. You can also just click the sketch.")
            self._sketch_cb = cb
            h.addWidget(cb)
            return row
        if t == "owner_part":
            cb = QComboBox()
            cb.addItem(OWNER_PICK if (p.get("ref") or {}).get("origin") else OWNER_SAME, None)
            for pid, part in self.base_state.parts.items():
                cb.addItem(getattr(part, "name", pid), pid)
            for sid in self.base_state.sketches:          # a sketch can own the origin connector too (I1.6)
                sf = self.doc.feature(sid)
                cb.addItem(f"{sf['name'] if sf else sid} (sketch)", sid)
            cb.setCurrentIndex(max(0, cb.findData(p.get(spec["key"]))))
            cb.currentIndexChanged.connect(lambda i, cb=cb, k=spec["key"]: self._set(k, cb.itemData(i)))
            cb.setToolTip("The part or sketch the connector belongs to: Assemblies mate it as that part's or "
                          "sketch's own connector. A connector on a face or edge belongs to that element's part "
                          "unless you choose another; the origin connector always needs an owner.")
            self.widgets[spec["key"]] = cb
            h.addWidget(cb)
            return row
        if t == "studio_choice":
            return self._make_studio_choice(spec, p, row, h)
        if t == "studio_names":
            return self._make_studio_names(spec, p, row, h)
        if t == "variable_document":
            return self._make_variable_document(spec, p, row, h)
        if t == "derived_document":
            return self._make_derived_document(spec, p, row, h)
        if t == "derived_source":
            return self._make_derived_source(spec, p, row, h)
        if t == "derived_checks":
            return self._make_derived_checks(spec, p, row, h)
        if t == "derived_connectors":
            return self._make_derived_connectors(spec, p, row, h)
        if t == "derived_location":
            return self._make_derived_location(spec, p, row, h)
        if t == "derived_base":
            return self._make_derived_base(spec, p, row, h)
        return None

    # ------------------------------------------------------------------ Derived rows (I1.9)
    # Shared state: `self._derived_fills` holds one refill callback per tick list; a change of source clears the
    # params the lists own and calls each. A later row that lists the source's output (Mate connectors) appends
    # its own callback and registers its params key in `_derived_owned`.
    def _derived_tabs(self):
        """The Part Studio tabs of the open Document's manifest other than this one, open or not; with another
        Document chosen (`_derived_scope` = (document id, Version)), the Part Studio tabs of that Version."""
        scope = getattr(self, "_derived_scope", None)
        if scope is not None:
            if scope[1] is None:
                return []
            try:
                tabs = self.main.folder.registry.describe(scope[0], scope[1])["tabs"]
            except K.KernelError:
                return []
            return [SimpleNamespace(id=t["id"], name=t["name"], kind=t["kind"]) for t in tabs
                    if t.get("kind") == "studio"]
        fd = getattr(self.main, "folder", None)
        pin = getattr(self, "_derived_pin", None)
        if pin and fd is not None:                      # pinned to a Version of this Document: that Version's tabs
            try:
                tabs = fd.registry.describe(fd.id, pin)["tabs"]
                return [SimpleNamespace(id=t["id"], name=t["name"], kind=t["kind"]) for t in tabs
                        if t.get("kind") == "studio"]
            except (K.KernelError, AttributeError):
                pass
        mine = self.main._folder_tab(self.doc) if hasattr(self.main, "_folder_tab") else None
        return [t for t in (fd.tabs if fd is not None else []) if t.kind == "studio" and t is not mine]

    def _derived_package(self):
        """(links.StudioPackage of the chosen source, "") or (None, why not): read from the source's current state."""
        src = self.feature["params"].get("source") or {}
        if not src.get("tab"):
            return None, ""
        try:
            return self.main.folder.resolver.resolve_studio(src), ""
        except Exception as e:          # source not found, in error, or not loadable: the list stays empty
            return None, str(e)

    def _derived_pending(self):
        """Another Document is chosen but no Version yet: the Derived cannot be committed (a link to another
        Document is always pinned)."""
        scope = getattr(self, "_derived_scope", None)
        return scope is not None and scope[1] is None

    def _derived_gate(self):
        pending = self._derived_pending()
        self.ok_button.setEnabled(not pending)
        self.ok_button.setToolTip("Choose a Version of the Document first" if pending else "Accept (Enter)")

    def _make_derived_document(self, spec, p, row, h):
        """`This Document`, or another Document and one of its Versions (ui.document_picker). The chosen scope decides
        which Part Studio tabs `derived_source` lists; the source is re-read when it changes."""
        from .document_picker import DocumentVersionPicker
        fd = self.main.folder
        def local_versions():                   # I1.11: newest first; none for a Document without Versions
            try:
                return [v["name"] for v in reversed(fd.versions())]
            except K.KernelError:
                return []
        pk = DocumentVersionPicker(fd.registry, exclude_id=getattr(fd, "id", None), local_label="This Document",
                                   local_versions=local_versions)
        pk.setObjectName("derived_document")
        src = p.get(spec["key"]) or {}
        self._derived_scope = None
        self._derived_pin = None                # the Version of This Document the source is pinned to, if any
        if src.get("document") and src["document"] != getattr(fd, "id", None):
            pk.select(src["document"], src.get("version"))
            self._derived_scope = (src["document"], src.get("version"))
        elif src.get("version"):
            pk.set_local_pin(src["version"])
            self._derived_pin = src["version"]
        self._derived_picker = pk

        def scope_changed():
            was_local = self._derived_scope is None
            self._derived_scope = None if pk.is_local() else (pk.doc_combo.currentData(), pk.version_combo.currentData())
            self._derived_pin = pk.local_pin()
            refill = getattr(self, "_derived_refill_tabs", None)
            if refill is not None:
                refill(keep=was_local and pk.is_local())        # only the pin changed: keep the source and parts
            self._derived_gate()
        pk.changed.connect(scope_changed)
        h.setDirection(QBoxLayout.TopToBottom)
        h.addWidget(pk)
        self.widgets["document"] = pk
        return row

    def _make_derived_source(self, spec, p, row, h):
        from .. import links as L
        self._derived_fills = []
        self._derived_owned = ["parts", "sketches", "connectors"]      # params keys that belong to the source and are cleared with it
        cb = QComboBox()
        key = spec["key"]
        cur = (p.get(key) or {}).get("tab")

        def fill_tabs(keep=None):
            cb.blockSignals(True)
            cb.clear()
            cb.addItem("— choose a Part Studio —", None)
            for t_ in self._derived_tabs():
                cb.addItem(t_.name, t_.id)
            if keep and cb.findData(keep) < 0:     # the source tab is gone: say so instead of silently changing it
                cb.addItem(f"{(p[key].get('studio_name') or keep)} (not in this Document)", keep)
            cb.setCurrentIndex(max(0, cb.findData(keep)))
            cb.blockSignals(False)
        fill_tabs(cur)
        self._derived_note = QLabel("")
        self._derived_note.setWordWrap(True)
        self._derived_note.setStyleSheet("color:#8a94a3;")
        if not self._derived_tabs():
            self._derived_note.setText("This Document has no other Part Studio to derive from.")

        def chosen(i, cb=cb):
            tid = cb.itemData(i)
            tab = next((x for x in self._derived_tabs() if x.id == tid), None)
            for k_ in self._derived_owned:
                self.feature["params"][k_] = []          # another source: its own parts and sketches
            scope = getattr(self, "_derived_scope", None)
            if tab is not None:
                self.feature["params"][key] = (L.studio_source(tab) if scope is None else
                                               L.studio_source(tab, document=scope[0], version=scope[1]))
                if scope is None and getattr(self, "_derived_pin", None):
                    self.feature["params"][key]["version"] = self._derived_pin
            elif tid is None:
                self.feature["params"][key] = {}
            for fill in self._derived_fills:
                fill()
            self._set_unlisted()
        cb.currentIndexChanged.connect(chosen)

        def scope_changed(keep=False):
            """Another Document or Version: its own tabs, so no source, parts or sketches carry over. `keep`: only the
            pin of This Document changed, so the source stays on the same tab and takes the new pin (or none)."""
            cur_src = self.feature["params"].get(key) or {}
            if keep and cur_src.get("tab"):
                if self._derived_pin:
                    cur_src["version"] = self._derived_pin
                else:
                    cur_src.pop("version", None)
                fill_tabs(cur_src["tab"])
                for fill in self._derived_fills:
                    fill()
                self._set_unlisted()
                return
            for k_ in self._derived_owned:
                self.feature["params"][k_] = []
            self.feature["params"][key] = {}
            fill_tabs()
            for fill in self._derived_fills:
                fill()
            self._set_unlisted()
        self._derived_refill_tabs = scope_changed
        self._derived_gate()
        h.addWidget(cb)
        h.setDirection(QBoxLayout.TopToBottom)
        h.addWidget(self._derived_note)
        self.widgets[key] = cb
        return row

    DERIVED_TABS_STYLE = """
    QTabWidget::pane { border: none; }
    QTabBar::tab {
        background: transparent; color: #4b5563; border: none; border-bottom: 2px solid #e3e6ea;
        padding: 4px 8px; margin: 0;
    }
    QTabBar::tab:hover:!selected { color: #1f2328; }
    QTabBar::tab:selected { color: #1f2328; font-weight: 600; border-bottom: 2px solid #1f6fd1; }
    """

    def _make_derived_tabs(self, spec, p):
        """Parts, Sketches and Mate connectors as the tabs of one row, so one tick list shows at a time. Each tab is
        built by its own `_make_derived_*` builder. A tab's title carries how many of its rows are ticked, so a
        hidden list still says what it brings in; the dialog opens on the first tab with a tick."""
        tabs = QTabWidget()
        tabs.setObjectName("derived_tabs")
        tabs.setDocumentMode(True)
        tabs.tabBar().setUsesScrollButtons(False)
        tabs.tabBar().setExpanding(True)
        tabs.setStyleSheet(self.DERIVED_TABS_STYLE)
        lists = []
        for sub in spec["tabs"]:
            page = QWidget()
            v = QVBoxLayout(page)
            v.setContentsMargins(0, 4, 0, 0)
            make = self._make_derived_connectors if sub["t"] == "derived_connectors" else self._make_derived_checks
            make(sub, p, page, v)
            tabs.addTab(page, sub["label"])
            lists.append((sub["label"], self.widgets[sub["key"]]))

        def ticked(lst):
            return sum(lst.item(i).checkState() == Qt.Checked for i in range(lst.count()))

        def titles(*_):
            for i, (label, lst) in enumerate(lists):
                n = ticked(lst)
                tabs.setTabText(i, f"{label} ({n})" if n else label)
        for _label, lst in lists:               # connected after the lists' own handlers, which may untick rows
            lst.itemChanged.connect(titles)
        self._derived_fills.append(titles)
        titles()
        for pf in getattr(self, "_derived_fillers", []):    # a list on a hidden tab still gets its previews
            pf.watch(tabs)
        tabs.setCurrentIndex(next((i for i, (_l, lst) in enumerate(lists) if ticked(lst)), 0))
        self.widgets["derived_tabs"] = tabs
        return tabs

    def _make_derived_checks(self, spec, p, row, h):
        """A tick list of what the source publishes under `spec["key"]` ("parts" or "sketches"): the ticked ids are
        the params list. Rows are the published part ids / sketch ids, shown by name."""
        h.setDirection(QBoxLayout.TopToBottom)
        key = spec["key"]
        lst = QListWidget()
        lst.setMaximumHeight(240)
        h.addWidget(lst)
        self.widgets[key] = lst
        previews = self._derived_previews(lst)

        def read():
            self.feature["params"][key] = [lst.item(i).data(Qt.UserRole) for i in range(lst.count())
                                           if lst.item(i).checkState() == Qt.Checked]
            self._set_unlisted()

        def fill():
            pkg, why = self._derived_package()
            err = why or (pkg.error if pkg is not None else "")
            self._derived_note.setText("⚠ " + err if err else "" if self._derived_tabs()
                                       else "This Document has no other Part Studio to derive from.")
            published = ({} if pkg is None else
                         {i: v for i, v in (pkg.parts if key == "parts" else pkg.sketches).items()})
            ticked = list(self.feature["params"].get(key) or [])
            lst.blockSignals(True)
            lst.clear()
            previews.reset()
            for i, v in list(published.items()) + [(i, None) for i in ticked if i not in published]:
                it = QListWidgetItem(v.name if v is not None else f"{i}  (no longer published)")
                it.setData(Qt.UserRole, i)
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked if i in ticked else Qt.Unchecked)
                lst.addItem(it)
                if v is None or err:                  # nothing published to draw, or the source is in error
                    it.setIcon(PR.placeholder_icon(PR.DERIVED_SIZE))
                    it.setToolTip(err or "no longer published")
                else:
                    jk, jm = (PR.part_job if key == "parts" else PR.sketch_job)(v, PR.DERIVED_SIZE)
                    previews.add(it.setIcon, jk, jm)
            lst.blockSignals(False)
        lst.itemChanged.connect(lambda _it: read())
        self._derived_fills.append(fill)
        fill()
        return row

    def _make_derived_connectors(self, spec, p, row, h):
        """The Mate connectors tick list (I3.6): the source's published connectors, shown "name (part)". A connector
        arrives on its part, so ticking one whose part is not ticked is refused, and unticking a part unticks its
        connectors; the note under the source says which."""
        h.setDirection(QBoxLayout.TopToBottom)
        key = spec["key"]
        lst = QListWidget()
        lst.setMaximumHeight(240)
        h.addWidget(lst)
        self.widgets[key] = lst
        previews = self._derived_previews(lst)
        owner = {}                      # connector id -> its source part id (resolved through aliases)

        def part_ticked(cid):
            pkg_ids = self.feature["params"].get("parts") or []
            return owner.get(cid) is None or owner[cid] in [self._derived_alias(i) for i in pkg_ids]

        def read():
            self.feature["params"][key] = [lst.item(i).data(Qt.UserRole) for i in range(lst.count())
                                           if lst.item(i).checkState() == Qt.Checked]
            self._set_unlisted()

        def changed(it):
            cid = it.data(Qt.UserRole)
            if it.checkState() == Qt.Checked and not part_ticked(cid):
                lst.blockSignals(True)
                it.setCheckState(Qt.Unchecked)
                lst.blockSignals(False)
                self._derived_note.setText(f"⚠ Tick part {owner[cid]} first: a connector comes in with its part.")
                return
            read()

        def parts_changed(_it=None):
            dropped = False
            lst.blockSignals(True)
            for i in range(lst.count()):
                it = lst.item(i)
                if it.checkState() == Qt.Checked and not part_ticked(it.data(Qt.UserRole)):
                    it.setCheckState(Qt.Unchecked)
                    dropped = True
            lst.blockSignals(False)
            if dropped:
                self._derived_note.setText("⚠ Connectors on a part you unticked were unticked too.")
                read()

        def fill():
            pkg, _why = self._derived_package()
            err = _why or (pkg.error if pkg is not None else "")
            self._derived_aliases = dict(pkg.aliases) if pkg is not None else {}
            published = {} if pkg is None else dict(pkg.connectors)
            owner.clear()
            owner.update({i: self._derived_alias(c["part"]) for i, c in published.items()})
            ticked = list(self.feature["params"].get(key) or [])
            lst.blockSignals(True)
            lst.clear()
            previews.reset()
            parts = {} if pkg is None else {i: v.name for i, v in pkg.parts.items()}
            for i, c in list(published.items()) + [(i, None) for i in ticked if i not in published]:
                it = QListWidgetItem(f"{c['name']}  ({parts.get(c['part'], c['part'])})" if c is not None
                                     else f"{i}  (no longer published)")
                it.setData(Qt.UserRole, i)
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked if i in ticked else Qt.Unchecked)
                lst.addItem(it)
                if c is None or err:
                    it.setIcon(PR.placeholder_icon(PR.DERIVED_SIZE))
                    it.setToolTip(err or "no longer published")
                else:
                    jk, jm = PR.connector_job(pkg, c, PR.DERIVED_SIZE)
                    previews.add(it.setIcon, jk, jm)
            lst.blockSignals(False)
        lst.itemChanged.connect(changed)
        if "parts" in self.widgets:
            self.widgets["parts"].itemChanged.connect(parts_changed)
        self._derived_fills.append(fill)
        fill()
        return row

    def _derived_previews(self, lst):
        """A PreviewFiller for one Derived tick list (previews drawn once the list is shown; see ui.preview_rows)."""
        lst.setIconSize(QSize(PR.DERIVED_SIZE, PR.DERIVED_SIZE))
        pf = PR.PreviewFiller(lst, PR.DERIVED_SIZE)
        self._derived_fillers = getattr(self, "_derived_fillers", []) + [pf]
        return pf

    def drain_previews(self):
        """Draw every pending Derived preview now (they are otherwise drawn one row per event-loop turn)."""
        for pf in getattr(self, "_derived_fillers", []):
            pf.drain()

    def _derived_alias(self, pid):
        return getattr(self, "_derived_aliases", {}).get(pid, pid)

    # Location and Placement base (I4.2): both write params["placement"] = {"location", "base"} through
    # `_derived_write_placement`; no Location means no `placement` key at all.
    def _derived_write_placement(self):
        p = self.feature["params"]
        loc = self._derived_loc.currentData()
        if loc is None:
            p.pop("placement", None)
        else:
            p["placement"] = {"location": loc, "base": self._derived_base.currentData() or "origin"}
        self._derived_base.setEnabled(loc is not None)
        self._set_unlisted()

    def _derived_location_candidates(self):
        """{connector id: connector} of this studio defined above the Derived (state.connectors of the state
        before it), in feature order."""
        return dict(getattr(self.base_state, "connectors", {}) or {})

    def _make_derived_location(self, spec, p, row, h):
        """Location: a mate connector of this studio defined above the Derived, from a list or by clicking its
        axes in the viewport (`overlays` draws them pickable, `_pick_location` takes the click)."""
        h.setDirection(QBoxLayout.TopToBottom)
        cb = QComboBox()
        cb.setObjectName("derived_location")
        cb.addItem("— not placed —", None)
        cands = self._derived_location_candidates()
        want = (p.get(spec["key"]) or {}).get("location")
        for cid, c in cands.items():
            cb.addItem(str(c.get("name") or cid), cid)
        if want and want not in cands:      # defined below the Derived now, or gone: say so, the build reports it
            cb.addItem(f"{want}  (not defined above this feature)", want)
        cb.setCurrentIndex(max(0, cb.findData(want)))
        cb.setToolTip("Put everything this Derived brings in so that its Placement base lands on this Mate connector.")
        hint = QLabel("Pick from the list, or click a connector's axes in the viewport.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#8a94a3;")
        self._derived_loc = cb
        cb.currentIndexChanged.connect(lambda _i: self._derived_write_placement())
        h.addWidget(cb)
        h.addWidget(hint)
        self.widgets["location"] = cb
        return row

    def _pick_location(self, pick):
        """A click on a connector's axes (an overlay keyed ("placement_loc", id)) sets the Location."""
        if self.ftype != "derived" or pick.kind != "overlay" or pick.key[0] != "placement_loc":
            return False
        i = self._derived_loc.findData(pick.key[1])
        if i >= 0:
            self._derived_loc.setCurrentIndex(i)
        return True

    def _derived_location_overlays(self):
        """One short polyline (X axis end, origin, Z axis end) per candidate Location, pickable in the viewport."""
        scale = self.main.viewport.cam.scale * 34
        out = []
        for cid, c in self._derived_location_candidates().items():
            fr = c["frame"]
            o, z, x = (np.asarray(fr[k], float) for k in ("o", "z", "x"))
            out.append((("placement_loc", cid), ("poly", np.array([o + x * scale * 0.8, o, o + z * scale * 1.25]))))
        return out

    def _make_derived_base(self, spec, p, row, h):
        """Placement base: Base origin, or one of the source's TICKED mate connectors. It refills when the source
        or the connector ticks change; a base that is no longer ticked goes back to Base origin."""
        cb = QComboBox()
        cb.setObjectName("derived_base")
        want = [(p.get(spec["key"]) or {}).get("base") or "origin"]
        self._derived_base = cb

        def fill():
            pkg, _why = self._derived_package()
            ticked = list(self.feature["params"].get("connectors") or [])
            keep = want[0]
            parts = {} if pkg is None else {i: v.name for i, v in pkg.parts.items()}
            cb.blockSignals(True)
            cb.clear()
            cb.addItem("Base origin", "origin")
            for cid in ticked:
                c = None if pkg is None else pkg.connectors.get(cid)
                cb.addItem(f"{c['name']}  ({parts.get(c['part'], c['part'])})" if c is not None
                           else f"{cid}  (no longer published)", cid)
            i = cb.findData(keep)
            cb.setCurrentIndex(max(0, i))
            cb.blockSignals(False)
            want[0] = cb.currentData()
            if hasattr(self, "_derived_loc"):
                self._derived_write_placement()

        def chosen(_i):
            want[0] = cb.currentData()
            self._derived_write_placement()
        cb.currentIndexChanged.connect(chosen)
        self._derived_fills.append(fill)
        for k_ in ("connectors", "parts"):
            if k_ in self.widgets:      # runs after the tick list's own handler, so params already hold the ticks
                self.widgets[k_].itemChanged.connect(lambda _it: fill())
        cb.setEnabled(bool((p.get("placement") or {}).get("location")))
        h.addWidget(cb)
        self.widgets["base"] = cb
        fill()
        return row

    # ------------------------------------------------------------------ Variable Studio reference rows
    def _variable_tabs(self):
        """The Variable Studio tabs of the open Document's manifest (none for a loose file); with another Document
        chosen (`_ref_scope` = (document id, Version)), the Variable Studio tabs of that Version."""
        scope = getattr(self, "_ref_scope", None)
        if scope is not None:
            if scope[1] is None:
                return []
            try:
                tabs = self.main.folder.registry.describe(scope[0], scope[1])["tabs"]
            except K.KernelError:
                return []
            return [SimpleNamespace(id=t["id"], name=t["name"], kind=t["kind"]) for t in tabs
                    if t.get("kind") == "variables"]
        fd = getattr(self.main, "folder", None)
        return [t for t in (fd.tabs if fd is not None else []) if t.kind == "variables"]

    def _ref_pending(self):
        """Another Document is chosen but no Version yet: the reference cannot be committed (a link to another
        Document is always pinned)."""
        scope = getattr(self, "_ref_scope", None)
        return scope is not None and scope[1] is None

    def _ref_gate(self):
        pending = self._ref_pending()
        self.ok_button.setEnabled(not pending)
        self.ok_button.setToolTip("Choose a Version of the Document first" if pending else "Accept (Enter)")

    def _ref_source(self, tab):
        """The source dict of a Variable Studio tab of the chosen scope (pinned when it is another Document's)."""
        src = {"kind": "variables", "tab": tab.id, "name": tab.name}
        scope = getattr(self, "_ref_scope", None)
        if scope is not None:
            src.update(document=scope[0], version=scope[1])
        return src

    def _studio_variable_names(self):
        """The names the chosen Variable Studio defines now, in table order (empty if it is not found)."""
        src = self.feature["params"].get("source") or {}
        if src.get("document") and src.get("version"):          # a pinned Version of another Document, read-only
            try:
                vs = self.main.folder.resolver._cross_doc(src, "variables")
            except K.KernelError:
                return []
            values, rows = vs.values()[0], vs.rows
        else:
            tab = next((t for t in self._variable_tabs() if t.id == src.get("tab")), None)
            if tab is None or getattr(tab, "doc", None) is None:
                return []
            values, rows = tab.doc.values()[0], tab.doc.rows
        return [n for n in dict.fromkeys(str(r.get("name") or "").strip() for r in rows) if n in values]

    def _make_variable_document(self, spec, p, row, h):
        """`This Document`, or another Document and one of its Versions (ui.document_picker). The chosen scope decides
        which Variable Studios `studio_choice` lists; changing it clears the source and the ticked names."""
        from .document_picker import DocumentVersionPicker
        fd = self.main.folder
        pk = DocumentVersionPicker(fd.registry, exclude_id=getattr(fd, "id", None), local_label="This Document")
        pk.setObjectName("variable_document")
        src = p.get(spec["key"]) or {}
        self._ref_scope = None
        if src.get("document") and src["document"] != getattr(fd, "id", None):
            pk.select(src["document"], src.get("version"))
            self._ref_scope = (src["document"], src.get("version"))

        def scope_changed():
            self._ref_scope = None if pk.is_local() else (pk.doc_combo.currentData(), pk.version_combo.currentData())
            refill = getattr(self, "_ref_refill_tabs", None)
            if refill is not None:
                refill()
            self._ref_gate()
        pk.changed.connect(scope_changed)
        h.setDirection(QBoxLayout.TopToBottom)
        h.addWidget(pk)
        self.widgets["document"] = pk
        return row

    def _make_studio_choice(self, spec, p, row, h):
        cb = QComboBox()                 # the row's label is already in `h`
        key = spec["key"]

        def fill_tabs():
            tabs = self._variable_tabs()
            cb.blockSignals(True)
            cb.clear()
            for t_ in tabs:
                cb.addItem(t_.name, t_.id)
            if not tabs:
                cb.addItem("— no Variable Studio in this Document —" if getattr(self, "_ref_scope", None) is None
                           else "— choose a Version first —" if self._ref_pending()
                           else "— no Variable Studio in this Version —", None)
            cb.blockSignals(False)
            return tabs

        tabs = fill_tabs()
        cur = (p.get(key) or {}).get("tab")
        if cur is None and tabs:
            p[key] = self._ref_source(tabs[0])
        cb.setCurrentIndex(max(0, cb.findData((p.get(key) or {}).get("tab"))))

        def chosen(i, cb=cb):
            tid = cb.itemData(i)
            t_ = next((x for x in self._variable_tabs() if x.id == tid), None)
            if t_ is not None:
                self.feature["params"].pop("names", None)           # another Studio: its own names
                self._set(key, self._ref_source(t_))
                self._fill_names()
        cb.currentIndexChanged.connect(chosen)

        def scope_changed():
            """Another Document or Version: its own Variable Studios, so no source and no names carry over."""
            self.feature["params"].pop("names", None)
            tabs = fill_tabs()
            self.feature["params"][key] = self._ref_source(tabs[0]) if tabs else {}
            cb.setCurrentIndex(0)
            self._fill_names()
            self._set_unlisted()
        self._ref_refill_tabs = scope_changed
        self._ref_gate()
        h.addWidget(cb)
        self.widgets[key] = cb
        return row

    def _make_studio_names(self, spec, p, row, h):
        """"All variables" (no names key) or a tick list of the Studio's variables (a names list)."""
        h.setDirection(QBoxLayout.TopToBottom)
        all_cb = QCheckBox("All variables")
        lst = QListWidget()
        lst.setMaximumHeight(140)
        h.addWidget(all_cb)
        h.addWidget(lst)
        self.widgets["names_all"], self.widgets["names"] = all_cb, lst
        key = spec["key"]
        busy = []

        def read():
            ticked = [lst.item(i).data(Qt.UserRole) for i in range(lst.count())
                      if lst.item(i).checkState() == Qt.Checked]
            if len(ticked) == lst.count():
                self.feature["params"].pop(key, None)
            else:
                self.feature["params"][key] = ticked
            self._set_unlisted()
            busy.append(1)
            all_cb.setChecked(key not in self.feature["params"])
            busy.pop()

        def fill():
            names = self._studio_variable_names()
            listed = self.feature["params"].get(key)
            lst.blockSignals(True)
            lst.clear()
            for n in names + [n for n in (listed or []) if n not in names]:
                it = QListWidgetItem(n if n in names else f"{n}  (not in the Studio)")
                it.setData(Qt.UserRole, n)
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked if listed is None or n in listed else Qt.Unchecked)
                lst.addItem(it)
            lst.blockSignals(False)
            busy.append(1)
            all_cb.setChecked(listed is None)
            busy.pop()

        def all_toggled(on):
            if busy:
                return
            lst.blockSignals(True)
            for i in range(lst.count()):
                lst.item(i).setCheckState(Qt.Checked if on else Qt.Unchecked)
            lst.blockSignals(False)
            read()

        lst.itemChanged.connect(lambda _it: None if busy else read())
        all_cb.toggled.connect(all_toggled)
        self._fill_names = fill
        fill()
        return row

    def _set_unlisted(self):
        """The names key changed (or was dropped): refresh the dialog and the preview."""
        self._sync_visibility()
    def _split_rows(self):
        """One row per Split tool: its name, Keep both sides, and Flip (which side stays) when not."""
        box = getattr(self, "_split_box", None)
        if box is None:
            return
        tools = F.split_tools(self.feature["params"])
        sig = tuple((t["kind"], bool(t.get("both", True)), bool(t.get("trim")),
                     json.dumps(t["ref"], sort_keys=True, default=str)) for t in tools)
        if sig == self._split_sig:
            return
        self._split_sig = sig
        lay = box.layout()
        while lay.count():
            w = lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for i, t in enumerate(tools):
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(4, 0, 0, 0)
            name = QLabel(f"{i + 1}. {item_label(self.doc, _split_item(t))}")
            name.setStyleSheet("color:#4b5563;")
            h.addWidget(name, 1)
            if t["kind"] == "face":
                tr = QCheckBox("Trim to face boundaries")
                tr.setToolTip("Split only as far as the face reaches. Off: the face is extended to cut right through.")
                tr.setChecked(bool(t.get("trim")))
                tr.toggled.connect(lambda on, i=i: self._split_set(i, "trim", on))
                h.addWidget(tr)
            cb = QCheckBox("Keep both sides")
            cb.setChecked(bool(t.get("both", True)))
            cb.toggled.connect(lambda on, i=i: self._split_set(i, "both", on))
            h.addWidget(cb)
            fl = QToolButton()
            fl.setText("⇅ Flip")
            fl.setToolTip("Keep the other side (or drag the arrow across)")
            fl.setVisible(not t.get("both", True))
            fl.clicked.connect(lambda _=False, i=i: self._split_set(i, "flip", None))
            h.addWidget(fl)
            lay.addWidget(row)

    def _fill_rows(self):
        """One row per Fill edge with a Tangency tick (the per-row mechanism Split uses for Trim). A sketch curve
        has no face beside it, so its row says Position and has no tick."""
        box = getattr(self, "_fill_box", None)
        if box is None:
            return
        edges = self.feature["params"].get("edges") or []
        sig = tuple((e.get("continuity"), json.dumps(e.get("ref"), sort_keys=True, default=str)) for e in edges)
        if sig == self._fill_sig:
            return
        self._fill_sig = sig
        lay = box.layout()
        while lay.count():
            w = lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for i, e in enumerate(edges):
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(4, 0, 0, 0)
            name = QLabel(f"{i + 1}. {item_label(self.doc, _fill_item(e))}")
            name.setStyleSheet("color:#4b5563;")
            h.addWidget(name, 1)
            if (e.get("ref") or {}).get("kind") == "edge":
                tg = QCheckBox("Tangency")
                tg.setToolTip("Aim the fill along the face beside this edge. Off: Position, the fill meets the edge only.")
                tg.setChecked(e.get("continuity") == "tangency")
                tg.toggled.connect(lambda on, i=i: self._fill_set(i, on))
                h.addWidget(tg)
            else:
                pos = QLabel("Position")
                pos.setToolTip("A sketch curve has no face beside it, so the fill meets it at Position only")
                pos.setStyleSheet("color:#8a94a3;")
                h.addWidget(pos)
            lay.addWidget(row)

    def _fill_set(self, i, tangency):
        p = self.feature["params"]
        edges = [dict(e) for e in p.get("edges") or []]
        if i >= len(edges):
            return
        edges[i]["continuity"] = "tangency" if tangency else "position"
        p["edges"] = edges
        QTimer.singleShot(0, self._sync_visibility)
        self.schedule_preview()

    def _split_set(self, i, key, value):
        tools = F.split_tools(self.feature["params"])
        if i >= len(tools):
            return
        tools = [dict(t) for t in tools]
        tools[i][key] = (not tools[i].get("flip")) if key == "flip" else bool(value)
        if key == "trim" and not value:
            tools[i].pop("trim", None)                # an extended face saves as it always did
        p = self.feature["params"]
        p["tools"] = tools
        p.pop("plane", None)
        p.pop("keep", None)
        QTimer.singleShot(0, self._sync_visibility)
        self.schedule_preview()

    def _sync_visibility(self):
        p = self.feature["params"]
        if self.ftype == "mate_connector" and self.widgets.get("owner") is not None:
            self.widgets["owner"].setItemText(0, OWNER_PICK if (p.get("ref") or {}).get("origin") else OWNER_SAME)
        if self.ftype == "split":
            self._split_rows()
        if self.ftype == "fill":
            self._fill_rows()
        for spec, lab in self._info_rows:
            txt = spec["text"](p)
            lab.setText(txt)
            w_ = max(min(lab.width() if lab.width() > 50 else 10 ** 6, self.width() - 24), 200)
            lab.setMinimumHeight(lab.heightForWidth(w_) if txt else 0)     # wrapped text gets all its lines
            if spec["key"] == "_info":           # warnings in red, hints in green
                lab.setStyleSheet("color:#d93025; font-weight:600;" if txt.startswith("⚠")
                                  else "color:#2e9e44; font-weight:600;")
        for spec, w in self.rows:
            vis = spec.get("visible")
            w.setVisible(True if vis is None else bool(vis(p)))
            if spec.get("enabled"):             # greyed with the reason when another setting rules it out
                ok = bool(spec["enabled"](p))
                w.setEnabled(ok)
                why_if = spec.get("why_if")          # (test, text): a more specific reason when `test(p)`
                w.setToolTip("" if ok else why_if[1] if why_if and why_if[0](p) else spec["why"])
            if spec["t"] == "ops":            # the buttons show the params (Surface limits the operation to New / Add)
                for (val, _), b in zip(spec["options"], w.findChildren(QToolButton)):
                    b.setChecked(p.get(spec["key"], spec.get("default")) == val)
        # clicks go to the active field, so it must be one the user can see: on open, or when a row has just
        # been hidden (Merge scope when the operation becomes New), the first visible field that can still take
        # a click gets it. A list that takes several picks keeps it even when it already holds some (a
        # preselected region leaves the regions field active, as in Onshape); a filled single pick hands on.
        af = self.active_field
        if af is None or not af.isVisibleTo(self):
            shown = [f for f in self.fields.values() if f.isVisibleTo(self)]
            nxt = next((f for f in shown if f.multi or not f.items), None) or (shown[0] if shown else None)
            if nxt is not None:
                self.activate(nxt)
        for f in self.fields.values():
            f.refresh(self.doc)
        self.adjustSize()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.accept()
        elif e.key() == Qt.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(e)

    # ------------------------------------------------------------------ selection
    def activate(self, field):
        if self.active_field is not None:
            self.active_field.set_active(False)
        self.active_field = field
        field.set_active(True)
        want = field.want
        kinds = set()
        if "region" in want:
            kinds |= {"region", "sketch_line"}
        if "face" in want or "plane" in want:
            kinds.add("face")
        if "element" in want:
            kinds |= {"face", "edge", "sketch_line", "sketch_point"}
        if "edge" in want:
            kinds |= {"edge", "face"}
        if "axis" in want or "path" in want:
            kinds |= {"edge", "sketch_line"}
        if "axis" in want:
            kinds.add("face")
        if "plane" in want:
            kinds.add("plane")
        if "sketch_line" in want:
            kinds.add("sketch_line")
        if "sketch_point" in want:
            kinds.add("sketch_point")
        if "axis_feature" in want or "axis" in want:
            kinds.add("axis")
        if "connector" in want or "axis" in want or "axis_connector" in want:
            kinds.add("connector")
        if "part" in want:
            kinds.add("part")
        if "vertex" in want:
            kinds.add("vertex")
        self.main.viewport.select_filter = kinds
        for f in self.fields.values():
            f.refresh(self.doc)
        self.main.viewport.update()

    def _default_hole_size(self, p):
        """A new hole on a sketch that has circles but no points: take the circles' size — what you drew."""
        if not self.is_new or self.ftype != "hole":
            return
        sr = self.base_state.sketches.get(p.get("sketch"))
        if sr is None:
            return
        _c, sizes = F.hole_centres(sr.sketch)
        if sizes and all(sizes):
            p["standard"] = "sketch"

    def _choose_sketch(self, fid):
        p = self.feature["params"]
        p["sketch"] = fid
        self._default_hole_size(p)
        w = self.widgets.get("standard") if hasattr(self, "widgets") else None
        if w is not None and hasattr(w, "findData"):
            w.blockSignals(True)
            w.setCurrentIndex(max(0, w.findData(p.get("standard"))))
            w.blockSignals(False)
        self._sync_visibility()
        self.schedule_preview()

    def _pick_sketch(self, pick):
        """Clicking a sketch (its region or a curve) picks it for features that take a whole sketch."""
        cb = getattr(self, "_sketch_cb", None)
        if cb is None or pick.kind not in ("region", "sketch_line"):
            return False
        i = cb.findData(pick.key[0])
        if i < 0:
            return False
        cb.setCurrentIndex(i)          # -> _choose_sketch
        return True

    def handle_pick(self, pick: Pick):
        """Called by the main window when the user clicks in the viewport."""
        if self._pick_sketch(pick) or self._pick_location(pick):
            return
        if self.ftype == "mate_connector" and pick.kind in ("sketch_line", "sketch_point"):
            self._pick_sketch_connector(pick)
            return
        if self.ftype == "mate_connector" and pick.kind != "overlay":
            self._pick_connector(pick)
            return
        if pick.kind == "overlay":
            key, i = pick.key
            fld = self.fields.get(key)
            if fld and i < len(fld.items):
                if fld.number:                  # a listed vertex clicked in the view selects its row
                    fld.list.setCurrentRow(i)
                    return
                fld.items.pop(i)
                self._fields_changed()
            return
        fld = self.active_field
        if fld is None:
            return
        item = pick_to_item(self.main.viewport, pick, fld.want, self.base_state)
        if item is not None and self.ftype == "fillet" and fld.key == "vertices" and not self._fillet_vertex_ok(item):
            try:
                self.main.status.showMessage(
                    "That vertex is not at the end of a selected edge (or an edge tangent to it), so it cannot "
                    "take a radius. Pick a vertex where a selected edge ends.", 8000)
            except Exception:
                pass
            return
        if item is not None and self.ftype == "axis" and item[0] == "face" and fld.key == "refs_sel" \
                and not any(_same(ex, item) for ex in fld.items) and self.main.viewport.last_click \
                and (self.feature["params"].get("type") == "face_point"
                     or F.axis_role(*item) != "plane"):
            # the point clicked on the face. A flat face alone gives an axis at its centre (as a mate connector
            # sits), so its click is kept only for "Perpendicular to face at click"; a curved face alone gives
            # an axis at the click, along the normal there.
            click = self.main.viewport.surface_point(*self.main.viewport.last_click)
            self.feature["params"]["click"] = [float(a) for a in click] if click is not None else None
        if item is None and self.ftype == "canvas" and pick.kind == "face":
            from ..picking import refusal
            try:                                  # "That face is not flat", as the Browser client says
                self.main.status.showMessage(refusal("face", fld.want, self.main.viewport.part_shapes.get(pick.key[0]))[1],
                                             8000)
            except Exception:
                pass
            return
        if item is None:
            if pick.kind == "sketch_line" and "region" in fld.want:
                try:
                    self.main.status.showMessage(
                        "That sketch isn't a closed shape, so it has no area to use. Close its outline "
                        "(the inside turns blue) and pick it again.", 8000)
                except Exception:
                    pass
            return
        fld.toggle(item)

    def _fillet_vertex_ok(self, item):
        try:
            pos = F.resolve_point(self.base_state, item[1])
        except K.KernelError:
            return False
        ends = fillet_vertex_ends(self.base_state, self.feature["params"].get("edges", []))
        return any(float(np.linalg.norm(np.asarray(pos, float) - q)) < 1e-6 for q in ends)

    # ------------------------------------------------------------------ mate connector
    def snap_candidates(self, pick: Pick):
        """Snap points the viewport draws while hovering a face or edge (assembly.snap_candidates, as in the
        Assembly); the studio's coordinates are the world's, so "pw" is the point itself."""
        if pick.kind in ("sketch_line", "sketch_point"):
            return self._sketch_snaps(pick)
        shape = self.main.viewport.part_shapes.get(pick.key[0]) if pick.kind in ("face", "edge") else None
        if shape is None:
            return []
        return [dict(c, pw=np.asarray(c["p"], float)) for c in AS.snap_candidates(shape, pick.kind, pick.key[1])]

    def _sketch_of(self, pick):
        """(plane dict, sketch dict, SketchResult) of the shown sketch a sketch pick is on, or None."""
        for sfid, sr, _ in self.main.viewport.sketches:
            if sfid == pick.key[0]:
                plane = {"origin": sr.plane.origin, "xdir": sr.plane.xdir, "normal": sr.plane.normal}
                return plane, sr.sketch.to_dict(), sr
        return None

    def _sketch_snaps(self, pick: Pick):
        """Snap points of a hovered sketch line, circle, arc or point: one per role (end 1, middle, end 2, centre,
        the point). "type" picks the label the viewport draws; "role" is what a click on the dot stores."""
        from .. import sketchref
        found = self._sketch_of(pick)
        if found is None:
            return []
        plane, sk, _sr = found
        eid = pick.key[1]
        kind = {"end1": "vertex", "end2": "vertex", "point": "vertex", "mid": "mid", "centre": "center"}
        out = []
        for role in sketchref.roles(sk, eid):
            o = np.asarray(sketchref.frame(plane, sk, eid, role)["o"], float)
            out.append({"type": kind[role], "p": o, "pw": o, "role": role, "sketch": pick.key[0], "entity": eid})
        return out

    def _pick_sketch_connector(self, pick: Pick):
        """A click on a sketch point, line, circle or arc: the ref I1.3 accepts. A highlighted snap dot gives its role;
        otherwise the role is the one nearest the click on screen (a line: end 1, middle or end 2)."""
        from .. import sketchref
        vp = self.main.viewport
        snap, base = vp.snap_hi, vp.snap_base
        if snap is not None and snap.get("role") and base is not None and base.kind in ("sketch_line", "sketch_point"):
            pick, role = base, snap["role"]
        else:
            role = None
        vp.clear_snaps()
        found = self._sketch_of(pick)
        if found is None:
            return
        plane, sk, _sr = found
        fid, eid = pick.key
        try:
            roles = sketchref.roles(sk, eid)
            if role not in roles:
                role = None
            if role is None:
                if vp.last_click and len(roles) > 1:
                    cx, cy = vp.last_click

                    def gap(r):
                        s = vp.cam.to_screen(np.asarray(sketchref.frame(plane, sk, eid, r)["o"], float))
                        return (s[0] - cx) ** 2 + (s[1] - cy) ** 2
                    role = min(roles, key=gap)
                else:
                    role = "mid" if "mid" in roles else roles[0]
            d = sketchref.describe(sk, eid, role)
        except K.KernelError:
            return
        ref = {"sketch": fid, "entity": eid, "role": role, "etype": d["etype"], "sig": d["sig"]}
        self.fields["element"].items = [("sketch", ref)]
        self._fields_changed()
        p = self.feature["params"]
        p["click"], p["snap"], p["x_ref"] = None, None, None
        self.schedule_preview()

    def _pick_connector(self, pick: Pick):
        """A click on a face or edge: a highlighted snap point wins over whatever is under the cursor (the
        Assembly's rule). The click point and the snap are stored with the reference, as the Assembly stores them."""
        vp = self.main.viewport
        snap, base = vp.snap_hi, vp.snap_base
        if snap is not None and base is not None and base.kind in ("face", "edge"):
            pick = base
        else:
            snap = None
        vp.clear_snaps()
        if pick.kind not in ("face", "edge"):
            return
        item = pick_to_item(vp, pick, {"element"}, self.base_state)
        if item is None:
            return
        click = None
        if pick.kind == "face" and vp.last_click:
            click = vp.surface_point(*vp.last_click)
        fld = self.fields["element"]
        fld.items = [item]
        self._fields_changed()
        self._stamp_connector(item, pick, click, snap)

    def _stamp_connector(self, item, pick, click, snap):
        """Store click, snap and the frame's X reference (the run rebuilds the frame from them)."""
        self.feature["params"].update(connector_stamp(
            self.base_state, item, click, snap, self.main.viewport.part_shapes, pick.key[0]))
        self.schedule_preview()

    def _apply_preselection(self, picks):
        fields = self._field_values()
        default = list(fields.get("plane_sel", ())) if self.ftype == "canvas" else None
        if default:
            fields["plane_sel"] = []         # a new Canvas starts on Top: the pick of a plane or face replaces that
        got = apply_preselection(self.ftype, fields, picks,
                                 _PickState(self.main.viewport, self.base_state), self.feature["params"])
        if default and not got.fields.get("plane_sel"):
            got.fields["plane_sel"] = default            # nothing picked fits: it stays on Top
        for key, items in got.fields.items():
            if key in self.fields and not key.endswith("_numbers"):
                self.fields[key].items = items
        fid = got.params.get("sketch")
        if fid is not None and getattr(self, "_sketch_cb", None) is not None:
            self._sketch_cb.setCurrentIndex(self._sketch_cb.findData(fid))          # -> _choose_sketch
        self._fields_changed()
        stamp = {k: v for k, v in got.params.items() if k != "sketch"}
        if stamp:
            self.feature["params"].update(stamp)
            self.schedule_preview()
        self.main.viewport.clear_selection()

    def _default_rib_part(self):
        """The Rib's part field is filled when one part is the obvious choice: the only part in the studio, or
        the only part the sketch plane of the picked curves cuts. A part the user picked is never replaced."""
        fld = self.fields.get("parts")
        if fld is None or fld.items:
            return
        parts = self.base_state.parts
        pid = next(iter(parts)) if len(parts) == 1 else None
        if pid is None and self.fields["path"].items:
            sr = self.base_state.sketches.get(self.fields["path"].items[0][1].get("sketch"))
            if sr is not None:
                n = np.asarray(sr.plane.normal, float)
                o = np.asarray(sr.plane.origin, float)
                cut = []
                for i, part in parts.items():
                    lo, hi = K.bbox(part.shape)
                    c, h = (lo + hi) / 2, (hi - lo) / 2
                    if abs(float(n @ (c - o))) <= float(np.abs(n) @ h) + 1e-9:
                        cut.append(i)
                pid = cut[0] if len(cut) == 1 else None
        if pid is not None:
            fld.items = [("part", pid)]
            self.feature["params"]["parts"] = [pid]

    def _fields_changed(self):
        fields = self._field_values()
        before = thicken_op_for(self.base_state, self.feature["params"]) if self.ftype == "thicken" else None
        fields_to_params(self.ftype, fields, self.feature["params"])
        if self.ftype == "thicken":
            # the picks choose the operation (a solid's face: Add; surface parts: New) when what is picked changes
            now = thicken_op_for(self.base_state, self.feature["params"])
            if now is not None and now != before:
                self.feature["params"]["op"] = now
        if self.ftype == "rib" and not self.fields["parts"].items:
            self._default_rib_part()
            fields["parts"] = self.fields["parts"].items
            fields_to_params(self.ftype, fields, self.feature["params"])
        self._sync_visibility()
        self.schedule_preview()

    def _set(self, key, value):
        p = self.feature["params"]
        before = dict(p)
        got = on_field_set(self.ftype, key, value, self._field_values(), p)
        p.clear()
        p.update(got.params)
        for k in ("thickness1", "thickness2", "draft_angle"):      # seeded by the change: show them
            if k in self.widgets and p.get(k) != before.get(k):
                self.widgets[k].setText(_show(p[k]))
        if self.ftype == "canvas":              # Keep aspect ratio: the other size follows (not the one being typed in)
            for k in ("width", "height"):
                if k in self.widgets and k != key and p.get(k) != before.get(k):
                    self.widgets[k].setText(_show(p[k]))
        for k, items in got.fields.items():
            if k in self.fields and self.fields[k].items != items:
                self.fields[k].items = items
        if got.active in self.fields:
            fld = self.fields[got.active]
            QTimer.singleShot(0, lambda: self.activate(fld))
        self._sync_visibility()
        self.schedule_preview()

    def _field_values(self):
        """{key: items}, plus {key_numbers: numbers} for a field with a number box."""
        out = {k: f.items for k, f in self.fields.items()}
        out.update({k + "_numbers": f.numbers for k, f in self.fields.items() if f.number})
        return out

    def _numbers_changed(self):
        """A number box was edited: store it and rebuild the preview without redrawing the list being typed in."""
        fields_to_params(self.ftype, self._field_values(), self.feature["params"])
        self.schedule_preview()

    def _hover_row(self, row):
        self._hover = row
        self.main.viewport.overlays = self.overlays()
        self.main.viewport.update()

    def overlays(self):
        """Geometry of the current selections, in the pre-feature model, for highlighting."""
        out = []
        half = self.main.viewport.plane_half()
        for key, fld in self.fields.items():
            if self.ftype == "split" and key == "parts" and self.fields.get("tools") and self.fields["tools"].items:
                continue                  # the preview shows what's kept; a highlight of the whole part would hide it
            for i, item in enumerate(fld.items):
                g = item_geometry(self.base_state, item, half)
                if g is not None:
                    out.append(((key, i), g))
                    if fld.number and i == getattr(self, "_hover", -1):      # the hovered row's vertex, larger
                        out.append((("hover", i), item_geometry(self.base_state, item, half * 2.5)))
        if self.ftype == "derived":
            out += self._derived_location_overlays()
        for i, c in enumerate(getattr(self, "_gap_points", [])):     # a Fill's open ends, a cross each
            out.append((("gap", i), ("poly", _cross(c, half * 0.15))))
        return out

    # ------------------------------------------------------------------ drag arrows
    def _set_quiet(self, key, value):
        """Set a param from a drag and update its widget without re-triggering edits."""
        self.feature["params"][key] = value
        w = self.widgets.get(key)
        if isinstance(w, QLineEdit):
            w.setText(fmt(value))
        elif isinstance(w, QCheckBox):
            w.blockSignals(True)
            w.setChecked(bool(value))
            w.blockSignals(False)

    def _snap_step(self):
        raw = self.main.viewport.cam.scale * 6        # roughly 6 px per step
        mag = 10 ** np.floor(np.log10(max(raw, 1e-6)))
        for m in (1, 2, 5, 10):
            if m * mag >= raw:
                return float(m * mag)
        return float(mag * 10)

    def _split_arrows(self, p):
        """An arrow on each Split tool that keeps one side, pointing at the side that stays; drag it across to
        keep the other side."""
        st = self.base_state
        ids = [i for i in p.get("parts", []) if i in st.parts]
        if not ids:
            return []
        lo, hi = np.full(3, 1e18), np.full(3, -1e18)
        for pid in ids:
            a, b = K.bbox(st.parts[pid].shape)
            lo, hi = np.minimum(lo, a), np.maximum(hi, b)
        size = float(np.linalg.norm(hi - lo)) * 2 + 10
        centre = (lo + hi) / 2
        length = max(float(np.linalg.norm(hi - lo)) * 0.25, 1.0)
        out = []
        for i, t in enumerate(F.split_tools(p)):
            if t.get("both", True):
                continue
            try:
                got = K._tool_normal_at(centre, F.split_tool_shape(st, t, centre, size))
            except Exception:
                got = None
            if got is None:
                continue
            _d, q, n = got
            flip0 = bool(t.get("flip"))

            def on_drag(v, i=i, flip0=flip0):
                want = flip0 if v >= 0 else not flip0
                tools = F.split_tools(self.feature["params"])
                if i < len(tools) and bool(tools[i].get("flip")) != want:
                    self._split_set(i, "flip", None)
            out.append({"origin": q, "dir": -n if flip0 else n, "value": length, "on_drag": on_drag,
                        "on_done": self.schedule_preview, "label": f"Keep {i + 1}"})
        return out

    def manipulators(self):
        """Onshape-style arrows: drag to set extrude depth / plane offset."""
        try:
            p = F.prepare(self.feature, self.base_state)["params"]      # expressions -> numbers
        except K.KernelError:
            return []
        try:
            if self.ftype == "split":
                return self._split_arrows(p)
            if self.ftype in ("extrude", "plane"):
                out = []
                for m in SCHEMA.manipulators(self.ftype, self.feature["params"], self.base_state):
                    symmetric, fld = m["symmetric"], m["field"]

                    def on_drag(v, symmetric=symmetric, fld=fld):
                        if symmetric:
                            self._set_quiet(fld, round(abs(v) * 2, 6))
                        else:
                            self._set_quiet(fld, round(abs(v), 6))
                            self._set_quiet("flip", v < 0)
                        lab = self.feature["params"][fld] if fld == "depth" else abs(v)
                        self.main.viewport.manipulators[0]["label"] = fmt(lab) + " mm"
                        self.schedule_preview()
                    out.append({"origin": np.asarray(m["origin"]), "dir": np.asarray(m["dir"]), "value": m["value"],
                                "on_drag": on_drag, "on_done": self.schedule_preview, "snap": self._snap_step(),
                                "min": m["min"], "label": m["label"]})
                return out
        except (K.KernelError, KeyError, IndexError, ValueError):
            return []
        return []

    # ------------------------------------------------------------------ preview / commit
    def schedule_preview(self):
        self._timer.start(90)

    def _do_preview(self):
        self.feature["name"] = self.title.text().strip() or self.feature["name"]
        vp = self.main.viewport
        if vp._manip_drag is None:          # don't rebuild the arrow while it's being dragged
            vp.manipulators = self.manipulators()
        if self.ftype == "fill":
            self._gap_points = []
        err = self.main.show_preview(self.feature, self.index, self.overlays())
        if self.ftype == "fill":             # an open boundary: mark the two open ends and hold OK back
            gap = bool(err) and err.startswith(K.FILL_GAP)
            if gap:
                self._gap_points = fill_gap_points(self.base_state, self.feature["params"])
                self.main.viewport.overlays = self.overlays()
                self.main.viewport.update()
            self.ok_button.setEnabled(not gap)
            self.ok_button.setToolTip("The boundary has a gap: close it before accepting" if gap else "Accept (Enter)")
        if err:
            self.err.setText("⚠ " + err)
            self.err.setStyleSheet("")
            self.err.show()
        elif self.ftype == "variable":
            p = self.feature["params"]
            val = parse_number(p.get("expr", ""), variables=self.base_state.vars)
            self.err.setText(f"#{str(p.get('name', '')).lstrip('#')} = {fmt(val)}" if val is not None else "")
            self.err.setStyleSheet("color:#2e9e44; font-weight:600;")
            self.err.setVisible(val is not None)
        else:
            self.err.hide()
        self.adjustSize()

    def use_origin(self):
        """Mate connector at origin: the reference is the studio origin; one part is the owner without asking."""
        p = self.feature["params"]
        p["ref"], p["kind"], p["click"], p["snap"], p["x_ref"] = {"origin": True}, "origin", None, None, None
        cb = self.widgets.get("owner")
        if cb is not None and p.get("owner") is None and len(self.base_state.parts) == 1:
            p["owner"] = next(iter(self.base_state.parts))
        if cb is not None:
            cb.setCurrentIndex(max(0, cb.findData(p.get("owner"))))
        self._sync_visibility()
        self.schedule_preview()

    def accept(self):
        if self.ftype == "derived" and self._derived_pending():
            return                              # another Document without a Version: nothing to pin yet
        if self.ftype == "variable_studio_ref" and self._ref_pending():
            return                              # likewise a Variable Studio reference
        self._timer.stop()
        if self.ftype == "mate_connector" and (self.feature["params"].get("ref") or {}).get("origin") \
                and self.feature["params"].get("owner") not in self.base_state.parts \
                and self.feature["params"].get("owner") not in self.base_state.sketches:
            self.err.setText("⚠ Pick the part or sketch that owns the origin connector")
            self.err.show()
            return
        self.feature["name"] = self.title.text().strip() or self.feature["name"]
        if self.ftype == "loft":
            _loft_fold(self.feature["params"], strip=True)
        if self.ftype == "hole":
            import re as _re
            try:
                label = F.hole_dims(self.feature["params"])[3]
                base = _re.sub(r"\s*\(.*\)$", "", self.feature["name"])
                self.feature["name"] = f"{base} ({label})"
            except Exception:
                pass
        if self.ftype == "variable":
            p = self.feature["params"]
            name = str(p.get("name", "")).strip().lstrip("#")
            p["name"] = name
            if self.feature["name"].startswith(("Variable", "#")):
                self.feature["name"] = f"#{name} = {p.get('expr', '')}"
        self.accepted.emit()

    def reject(self):
        self._timer.stop()
        self.rejected.emit()


class SketchDialog(_ResizableFrame):
    """The small Sketch panel: plane selection and display toggles."""
    accepted = Signal()
    rejected = Signal()
    planePicked = Signal(object)

    def __init__(self, main, feature, has_plane):
        super().__init__(main.viewport)
        self.setObjectName("featureDialog")
        self._init_resizable(260)
        self.main = main
        self.feature = feature
        sh = QGraphicsDropShadowEffect(self)
        sh.setBlurRadius(18)
        sh.setOffset(0, 3)
        sh.setColor(QColor(0, 0, 0, 60))
        self.setGraphicsEffect(sh)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(6)
        head = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(icon("sketch").pixmap(20, 20))
        head.addWidget(ic)
        self.title = QLineEdit(feature["name"])
        self.title.setStyleSheet("QLineEdit { border: none; font-weight: 700; background: transparent; }"
                                 "QLineEdit:focus { border: 1px solid #1f6fd1; background: white; }")
        head.addWidget(self.title, 1)
        ok = QToolButton()
        ok.setObjectName("okButton")
        ok.setText("✓")
        ok.setToolTip("Finish sketch")
        ok.clicked.connect(self.accepted.emit)
        cancel = QToolButton()
        cancel.setObjectName("cancelButton")
        cancel.setText("✕")
        cancel.setToolTip("Cancel sketch")
        cancel.clicked.connect(self.rejected.emit)
        head.addWidget(ok)
        head.addWidget(cancel)
        lay.addLayout(head)
        self.plane_field = SelectionField("plane", "Sketch plane", {"plane"}, multi=False)
        self.plane_field.activated.connect(self._activate)
        lay.addWidget(self.plane_field)
        self.show_cons = QCheckBox("Show constraints")
        self.show_cons.setChecked(True)
        self.show_regions = QCheckBox("Show sketch regions")
        self.show_regions.setChecked(True)
        self.see_through = QCheckBox("See through solids while sketching")
        self.see_through.setChecked(str(main.settings.value("sketch_see_through", "true")).lower() != "false")
        lay.addWidget(self.show_cons)
        lay.addWidget(self.show_regions)
        lay.addWidget(self.see_through)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#4b5563;")
        lay.addWidget(self.status)
        self.plane_field.items = [("plane", feature["params"]["plane"])] if has_plane else []
        self.plane_field.refresh(main.doc)
        if not has_plane:
            self._activate(self.plane_field)
        self.adjustSize()
        self.move(12, 12)
        self.show()

    def _activate(self, f):
        f.set_active(True)
        f.refresh(self.main.doc)
        self.main.viewport.select_filter = {"plane", "face"}
        self.main.viewport.sketcher = None
        self.main.sketch_waiting_for_plane = True

    def handle_pick(self, pick):
        item = pick_to_item(self.main.viewport, pick, {"plane"}, getattr(self.main.doc, "state", None))
        if item:
            self.plane_field.items = [item]
            self.plane_field.set_active(False)
            self.plane_field.refresh(self.main.doc)
            self.planePicked.emit(item[1])

    def set_status(self, text, ok=False):
        self.status.setText(text)
        self.status.setStyleSheet("color:%s;" % (theme.OK_GREEN if ok else theme.TEXT_MUTED))
