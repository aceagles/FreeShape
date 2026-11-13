"""Floating, Onshape-style feature dialogs (green check / red X), built from small schemas."""
from __future__ import annotations

import copy
from types import SimpleNamespace

import numpy as np
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QBoxLayout, QLabel, QToolButton, QLineEdit, QComboBox, QCheckBox,
    QListWidget, QListWidgetItem, QWidget, QButtonGroup, QSizePolicy, QGraphicsDropShadowEffect,
)

from .. import kernel as K
from .. import features as F
from .. import assembly as AS
from .icons import icon
from . import preview_rows as PR
from . import theme
from .numparse import parse_number, fmt, has_vars
from .viewport import Pick

OP_LABELS = [("new", "New"), ("add", "Add"), ("remove", "Remove"), ("intersect", "Intersect")]


def _show(v):
    """Text for a number field: expressions stay as typed, numbers are tidied."""
    return v if isinstance(v, str) else fmt(v)


# ---------------------------------------------------------------------- pick <-> ref conversion

def region_touching(sr, eid):
    """Index of the sketch region whose outline runs along sketch entity `eid` (the smallest such region, so a
    hole's circle picks the hole rather than the plate around it), or None."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.gp import gp_Pnt
    try:
        pts = sr.sketch.polyline(eid, 16)
    except Exception:
        return None
    if not len(pts):
        return None
    u, v = pts[len(pts) // 2]
    P = sr.plane.to_world(u, v)
    vert = BRepBuilderAPI_MakeVertex(gp_Pnt(*map(float, P))).Vertex()
    best = None
    for ri, face in enumerate(sr.regions):
        dd = None
        for e in K.edges(face):
            ext = BRepExtrema_DistShapeShape(vert, e)
            if ext.IsDone():
                d = ext.Value()
                dd = d if dd is None else min(dd, d)
        if dd is not None and dd < 1e-5 * max(1.0, float(np.linalg.norm(P))) + 1e-6:
            area = K.face_info(face).get("area", 0.0)
            if best is None or area < best[0]:
                best = (area, ri)
    return None if best is None else best[1]


def pick_to_item(vp, pick: Pick, want: set, state=None):
    """Convert a viewport pick into a (kind, ref) item for a selection field.

    `state` is the ModelState the viewport is showing; with it a picked face, edge or vertex stores
    the element's name next to its signature (a part without a map gives a signature-only reference)."""
    k = pick.kind
    names = (lambda pid: state.named(pid)) if state is not None else (lambda pid: None)

    def engine_named(ref, pid, kind, idx):
        # a Part Studio tab drawn from the engine: the name comes from the part's element-name map by
        # the picked element's 1-based index, not from re-finding the element in a rebuilt shape
        n = ((getattr(state, "engine_names", None) or {}).get(pid) or {}).get(kind, {}).get(idx + 1)
        if n:
            ref["name"] = n
        return ref
    if "element" in want and k in ("face", "edge"):      # a Mate connector: one face or one edge, never a face's edges
        pid, i = pick.key
        shape = vp.part_shapes.get(pid)
        if shape is None:
            return None
        sub = (K.faces if k == "face" else K.edges)(shape)[i]
        return (k, engine_named(F.make_ref(pid, sub, k, shape, names(pid)), pid, k, i))
    if k == "region" and "region" in want:
        fid, ri = pick.key
        for sfid, sr, _ in vp.sketches:
            if sfid == fid and ri < len(sr.regions):
                return ("region", F.region_ref(sr, fid, sr.regions[ri]))
    if k == "face":
        pid, fi = pick.key
        shape = vp.part_shapes.get(pid)
        if shape is None:
            return None
        face = K.faces(shape)[fi]
        if "face" in want:
            return ("face", engine_named(F.make_ref(pid, face, "face", shape, names(pid)), pid, "face", fi))
        if "plane" in want:
            pl = K.planar_face_plane(face)
            if pl is not None:
                return ("plane", {"kind": "face",
                                  "face": engine_named(F.make_ref(pid, face, "face", shape, names(pid)), pid, "face", fi),
                                  "origin": pl.origin.tolist(), "xdir": pl.xdir.tolist()})
        if "edge" in want:          # selecting a face selects all of its edges (fillet/chamfer)
            return ("edges", [F.make_ref(pid, e, "edge", shape, names(pid)) for e in K.edges(face)])
        if "part" in want:
            return ("part", pid)
    if k == "part" and "part" in want:
        return ("part", pick.key[0])
    if k == "edge":
        pid, ei = pick.key
        shape = vp.part_shapes.get(pid)
        if shape is None:
            return None
        edge = K.edges(shape)[ei]
        if "edge" in want:
            return ("edge", engine_named(F.make_ref(pid, edge, "edge", shape, names(pid)), pid, "edge", ei))
        if "axis" in want:
            return ("axis", {"kind": "edge",
                             "edge": engine_named(F.make_ref(pid, edge, "edge", shape, names(pid)), pid, "edge", ei)})
    if k == "sketch_line" and "region" in want:
        # clicking a sketch's line (or circle) picks the closed area it bounds
        fid, eid = pick.key
        for sfid, sr, _ in vp.sketches:
            if sfid != fid or not sr.regions:
                continue
            ri = region_touching(sr, eid)
            if ri is not None:
                return ("region", F.region_ref(sr, fid, sr.regions[ri]))
        return None
    if k == "sketch_line" and "path" in want:
        fid, eid = pick.key
        return ("path", {"kind": "sketch_chain", "sketch": fid, "entity": eid})
    if k == "edge" and "path" in want:
        pid, ei = pick.key
        shape = vp.part_shapes.get(pid)
        if shape is not None:
            return ("path", {"kind": "edge", "edge": F.make_ref(pid, K.edges(shape)[ei], "edge", shape, names(pid))})
    if k == "sketch_line" and "axis" in want:
        fid, eid = pick.key
        return ("axis", {"kind": "sketch_line", "sketch": fid, "line": eid})
    if k == "vertex" and "vertex" in want:
        pid, vi = pick.key
        shape = vp.part_shapes.get(pid)
        if shape is None:
            return None
        pts = K.vertex_points(shape)
        if vi >= len(pts):
            return None
        return ("vertex", engine_named(F.make_vertex_ref(pid, pts[vi], shape, names(pid)), pid, "vertex", vi))
    if k == "plane" and "plane" in want:
        key = pick.key
        if key[0] == "std":
            return ("plane", {"kind": "std", "name": key[1]})
        return ("plane", {"kind": "plane", "fid": key[1]})
    return None


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
    if kind == "part":
        p = doc.state.parts.get(ref)
        return p.name if p else ref
    if kind == "plane":
        if ref["kind"] == "std":
            return f"{ref['name']} plane"
        if ref["kind"] == "plane":
            f = doc.feature(ref["fid"])
            return f["name"] if f else "Plane"
        return "Planar face"
    if kind == "path":
        return "Sketch curves" if ref["kind"] == "sketch_chain" else "Edge"
    if kind == "axis":
        if ref["kind"] == "std":
            return f"{ref['name']} axis"
        if ref["kind"] == "sketch_line":
            return "Sketch line"
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
            return ("poly", np.array([a - d * plane_half * 1.5, a + d * plane_half * 1.5]))
    except K.KernelError:
        return None
    return None


# ---------------------------------------------------------------------- selection field widget

class SelectionField(QFrame):
    activated = Signal(object)
    changed = Signal()

    def __init__(self, key, label, want, multi=True, parent=None):
        super().__init__(parent)
        self.setObjectName("selField")
        self.key, self.want, self.multi = key, set(want), multi
        self.items: list = []
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
        lay.addWidget(self.list)
        self.set_active(False)

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
        for i, item in enumerate(self.items):
            it = QListWidgetItem(item_label(doc, item))
            if i in missing:
                it.setForeground(QColor("#d93025"))
                it.setToolTip("Reference could not be found — double-click to remove")
            else:
                it.setToolTip("Double-click to remove")
            self.list.addItem(it)
        n = max(1, len(self.items))
        self._base_h = min(6, n) * 20 + 4
        self.list.setFixedHeight(self._base_h + self.extra)

    def set_extra(self, px):
        self.extra = max(0, int(px))
        self.list.setFixedHeight(self._base_h + self.extra)
        self.layout().invalidate()          # Qt re-lays nested widgets lazily; the dialog needs the new size now
        self.updateGeometry()

    def _remove_item(self, it):
        row = self.list.row(it)
        if 0 <= row < len(self.items):
            self.items.pop(row)
            self.changed.emit()

    def clear(self):
        self.items = []
        self.changed.emit()

    def toggle(self, item):
        kind, ref = item
        if kind == "edges":
            for r in ref:
                self.toggle(("edge", r))
            return
        for i, ex in enumerate(self.items):
            if _same(ex, item):
                self.items.pop(i)
                self.changed.emit()
                return
        if not self.multi:
            self.items = []
        self.items.append(item)
        self.changed.emit()


def _same(a, b):
    if a[0] != b[0]:
        return False
    ra, rb = a[1], b[1]
    if isinstance(ra, str) or isinstance(rb, str):
        return ra == rb
    if "c" in ra and "c" in rb:
        return np.linalg.norm(np.array(ra["c"]) - np.array(rb["c"])) < 1e-6 and ra.get("sketch") == rb.get("sketch")
    return ra == rb


# ---------------------------------------------------------------------- schemas

def _isnum(v):
    try:
        return abs(float(v)) > 1e-12
    except (TypeError, ValueError):
        return True if isinstance(v, str) and v.strip() else False


def _plane_info(p):
    """What the plane dialog says about the current selection."""
    items = F.plane_items(p)
    typ = F.plane_type(p)
    if typ is None:
        return "⚠ That selection doesn't fit a plane type. Try a plane or face, an edge, or vertices."
    name = F.PLANE_TYPE_NAMES.get(typ, typ)
    if (p.get("type") or "auto") == "auto":
        if not items:
            return "Select a plane or face, an edge, or vertices — the plane type is picked from what you select."
        return f"Type: {name}"
    return ""


def _hole_info(p):
    if p.get("standard") == "sketch":
        return "Each hole is the size of the circle you drew"
    try:
        d, cb, cs, label = F.hole_dims(p)
    except Exception:
        return ""
    t = f"{label}: drill Ø{fmt(d)}"
    if cb:
        t += f", counterbore Ø{fmt(cb[0])} × {fmt(cb[1])} deep"
    if cs:
        t += f", countersink Ø{fmt(cs)} × 90°"
    return t


def _num(key, label, unit="mm", visible=None, minimum=None, signed=False):
    """signed: a negative value is allowed and means the other way (the runner flips); only zero is rejected."""
    return {"t": "num", "key": key, "label": label, "unit": unit, "visible": visible, "min": minimum,
            "signed": signed}


def _choice(key, label, options, visible=None):
    return {"t": "choice", "key": key, "label": label, "options": options, "visible": visible}


def _check(key, label, visible=None):
    return {"t": "check", "key": key, "label": label, "visible": visible}


def _sel(key, label, want, multi=True, visible=None):
    return {"t": "sel", "key": key, "label": label, "want": want, "multi": multi, "visible": visible}


SCOPE_OPS = ("add", "remove", "intersect")
SCOPE_FEATURES = ("extrude", "revolve", "sweep", "loft")


def _scope():
    return _sel("scope", "Merge scope (empty = every part it touches)", {"part"},
                visible=lambda p: p.get("op", "add") in SCOPE_OPS)


def _ops(options=None):
    return {"t": "ops", "key": "op", "options": options or OP_LABELS}


SCHEMAS = {
    "sm_base": [
        _sel("profiles", "Sketch region(s) — the sheet's outline", {"region"}),
        _num("thickness", "Thickness", minimum=1e-6),
        _num("radius", "Bend radius (inside)", minimum=0),
        _num("k", "K-factor (0.44 suits most steel / aluminium)", unit="", minimum=1e-6),
        _check("flip", "Thickness on the other side"),
        _check("relief", "Corner reliefs where two bends meet"),
        _num("relief_scale", "Relief size (× thickness)", unit="", visible=lambda p: p.get("relief", True),
             minimum=0.1),
    ],
    "sm_flange": [
        _sel("edges", "Edges to bend a flange from", {"edge"}),
        _num("length", "Flange length (to the outside)", minimum=1e-6),
        _num("angle", "Bend angle", unit="deg", minimum=1e-6),
        _choice("position", "Where the bend goes", [("outside", "Outside the edge (the part grows)"),
                                                    ("inside", "Inside the edge (keeps the outline size)")]),
        _check("down", "Bend the other way"),
        _check("own_radius", "Use a different bend radius"),
        _num("radius", "Bend radius (inside)", visible=lambda p: p.get("own_radius"), minimum=0),
    ],
    "extrude": [
        _ops(),
        _sel("profiles", "Faces and sketch regions to extrude", {"region", "face"}),
        _choice("end", "End type", [("blind", "Blind"), ("symmetric", "Symmetric"), ("through", "Through all")]),
        _num("depth", "Depth", visible=lambda p: p.get("end") != "through", signed=True),
        _check("flip", "Opposite direction", visible=lambda p: p.get("end") != "symmetric"),
        _check("second", "Second end position", visible=lambda p: p.get("end") == "blind"),
        _num("depth2", "Second depth", visible=lambda p: p.get("end") == "blind" and p.get("second")),
        _check("both", "Both directions", visible=lambda p: p.get("end") == "through"),
        _scope(),
    ],
    "revolve": [
        _ops(),
        _sel("profiles", "Faces and sketch regions to revolve", {"region", "face"}),
        _sel("axis_sel", "Revolve axis (sketch line or edge)", {"axis"}, multi=False),
        _choice("axis_std", "…or world axis", [("", "—"), ("X", "X axis"), ("Y", "Y axis"), ("Z", "Z axis")]),
        _choice("end", "Revolve type", [("full", "Full"), ("one", "One direction"), ("symmetric", "Symmetric")]),
        _num("angle", "Revolve angle", unit="deg", visible=lambda p: p.get("end") != "full", signed=True),
        _check("flip", "Opposite direction", visible=lambda p: p.get("end") == "one"),
        _scope(),
    ],
    "fillet": [
        _sel("edges", "Edges or faces to fillet", {"edge"}),
        _num("radius", "Radius", minimum=1e-6),
    ],
    "chamfer": [
        _sel("edges", "Edges or faces to chamfer", {"edge"}),
        _num("distance", "Distance", minimum=1e-6),
    ],
    "shell": [
        _sel("faces", "Faces to remove", {"face"}),
        _num("thickness", "Thickness", minimum=1e-6),
        _check("outward", "Shell outside"),
    ],
    "delete_face": [
        _sel("faces", "Faces to delete", {"face"}),
        _choice("mode", "After deleting", [("heal", "Heal (extend the surrounding faces)"),
                                           ("cap", "Cap (close with a flat face)"),
                                           ("open", "Leave the void open")]),
        {"t": "info", "key": "_info", "text": lambda p: {
            "heal": "Removes fillets, chamfers, bosses and holes from an imported part.",
            "cap": "Covers the opening with a flat face.",
            "open": "The part becomes an open shell."}.get(p.get("mode", "heal"), "")},
    ],
    "move_face": [
        _sel("faces", "Flat faces to move", {"face"}),
        _num("distance", "Distance (outwards)"),
        _check("flip", "Flip direction"),
    ],
    "offset_surface": [
        _sel("faces", "Faces to offset", {"face"}),
        _num("distance", "Distance (outwards)"),
        _check("flip", "Flip direction"),
    ],
    "plane": [
        _choice("type", "Plane type", F.PLANE_TYPES),
        _sel("refs_sel", "Planes, faces, edges and vertices", {"plane", "face", "edge", "vertex"}),
        {"t": "info", "key": "_info", "text": lambda p: _plane_info(p)},
        _num("offset", "Offset distance", visible=lambda p: F.plane_type(p) == "offset"),
        _num("angle", "Angle", unit="deg", visible=lambda p: F.plane_type(p) == "line_angle"),
        _check("flip", "Flip direction", visible=lambda p: F.plane_type(p) is not None),
    ],
    "mirror": [
        _ops([("new", "New"), ("add", "Add")]),
        _sel("parts", "Parts to mirror", {"part"}),
        _sel("plane_sel", "Mirror plane", {"plane"}, multi=False),
    ],
    "linear_pattern": [
        _ops([("new", "New"), ("add", "Add")]),
        _sel("parts", "Parts to pattern", {"part"}),
        _sel("dir_sel", "Direction (edge or sketch line)", {"axis"}, multi=False),
        _choice("dir_std", "…or world axis", [("", "—"), ("X", "X axis"), ("Y", "Y axis"), ("Z", "Z axis")]),
        _num("spacing", "Distance"),
        _num("count", "Instance count", unit=""),
        _check("flip", "Opposite direction"),
    ],
    "circular_pattern": [
        _ops([("new", "New"), ("add", "Add")]),
        _sel("parts", "Parts to pattern", {"part"}),
        _sel("axis_sel", "Axis of pattern (edge or sketch line)", {"axis"}, multi=False),
        _choice("axis_std", "…or world axis", [("", "—"), ("X", "X axis"), ("Y", "Y axis"), ("Z", "Z axis")]),
        _num("angle", "Angle", unit="deg"),
        _num("count", "Instance count", unit=""),
    ],
    "boolean": [
        {"t": "ops", "key": "op", "options": [("union", "Union"), ("subtract", "Subtract"), ("intersect", "Intersect")]},
        _sel("tools", "Tools", {"part"}),
        _sel("targets", "Targets", {"part"}, visible=lambda p: p.get("op") != "union"),
        _check("keep_tools", "Keep tools", visible=lambda p: p.get("op") != "union"),
    ],
    "sweep": [
        _ops(),
        _sel("profiles", "Faces and sketch regions to sweep", {"region", "face"}),
        _sel("path", "Sweep path (sketch curves or edges)", {"path"}),
        _scope(),
    ],
    "loft": [
        _ops(),
        _sel("profiles", "Profiles — pick them in order", {"region", "face"}),
        _check("ruled", "Straight sides (ruled)"),
        _scope(),
    ],
    "draft": [
        _sel("faces", "Faces to draft", {"face"}),
        _sel("neutral_sel", "Neutral plane", {"plane"}, multi=False),
        _num("angle", "Draft angle", unit="deg"),
        _check("flip", "Opposite direction"),
    ],
    "split": [
        _sel("parts", "Parts to split", {"part"}),
        _sel("plane_sel", "Split with plane or planar face", {"plane"}, multi=False),
        _choice("keep", "Keep", [("both", "Both sides"), ("front", "Front side only"), ("back", "Back side only")]),
    ],
    "move": [
        _sel("parts", "Parts to move", {"part"}),
        _num("dx", "Move X"),
        _num("dy", "Move Y"),
        _num("dz", "Move Z"),
        _num("rotate", "Rotate", unit="deg"),
        _sel("axis_sel", "Rotate about (edge or sketch line)", {"axis"}, multi=False,
             visible=lambda p: _isnum(p.get("rotate")) is not False),
        _choice("axis_std", "…or world axis", [("", "—"), ("X", "X axis"), ("Y", "Y axis"), ("Z", "Z axis")],
                visible=lambda p: _isnum(p.get("rotate")) is not False),
        _check("copy", "Keep the original (make a copy)"),
    ],
    "thread": [
        _sel("faces", "Round faces to thread (holes or shafts)", {"face"}),
        _choice("size", "Size", [("auto", "Automatic (from the diameter)")] + [(x, x) for x in K.ISO_PITCH]
                + [("custom", "Custom")]),
        _num("major", "Major diameter", visible=lambda p: p.get("size") == "custom", minimum=1e-3),
        _num("pitch", "Pitch", visible=lambda p: p.get("size") == "custom", minimum=1e-3),
        _choice("length_mode", "Length", [("full", "Whole face"), ("length", "Set length")]),
        _num("length", "Thread length", visible=lambda p: p.get("length_mode") == "length", minimum=1e-3),
        _check("left_hand", "Left-hand thread"),
        _num("clearance", "Print clearance"),
        {"t": "info", "key": "_info", "text": lambda p: "Real ISO metric thread geometry. Holes get an internal "
                                                       "thread, shafts an external one. For 3D printing, add "
                                                       "about 0.1–0.2 mm clearance."},
    ],
    "mate_connector": [
        {"t": "text", "key": "name", "label": "Name", "placeholder": "e.g. Hinge pin"},
        _sel("element", "Face or edge (hover for snap points)", {"element"}, multi=False),
        {"t": "owner_part", "key": "owner", "label": "Owner part", "visible": lambda p: (p.get("ref") or {}).get("origin")},
        _check("flip", "Flip (reverse the Z axis)"),
    ],
    "variable": [
        {"t": "text", "key": "name", "label": "Name   #", "placeholder": "e.g. wall"},
        {"t": "text", "key": "expr", "label": "Value", "placeholder": "e.g. 3 or #wall * 2", "expr": True},
    ],
    "variable_studio_ref": [
        {"t": "variable_document", "key": "source", "label": "Document"},
        {"t": "studio_choice", "key": "source", "label": "Variable Studio"},
        {"t": "studio_names", "key": "names", "label": "Variables"},
    ],
    # Derived: one row per control group, top to bottom. Add a row here and a `_make_derived_*` builder below.
    #   Document chooser and Version pin: before `derived_source` (they decide which Part Studio list it shows).
    #   Mate connectors tick list: after `derived_sketches`, key "connectors".
    #   Location and Placement base: after that.
    "derived": [
        {"t": "derived_document", "key": "source", "label": "Document"},
        {"t": "derived_source", "key": "source", "label": "Part Studio"},
        {"t": "derived_checks", "key": "parts", "label": "Parts"},
        {"t": "derived_checks", "key": "sketches", "label": "Sketches"},
        {"t": "derived_connectors", "key": "connectors", "label": "Mate connectors"},
        {"t": "derived_location", "key": "placement", "label": "Location"},
        {"t": "derived_base", "key": "placement", "label": "Placement base"},
    ],
    "hole": [
        {"t": "sketch_choice", "key": "sketch", "label": "Sketch with hole centres"},
        _choice("standard", "Standard", [("clearance", "ISO clearance"), ("tapped", "ISO tapped"),
                                         ("custom", "Custom size"), ("sketch", "Size of the sketch circles")]),
        _choice("size", "Size", [(x, x) for x in F.HOLE_SIZES],
                visible=lambda p: p.get("standard") not in ("custom", "sketch")),
        _choice("fit", "Fit", [("close", "Close"), ("normal", "Normal"), ("loose", "Loose")],
                visible=lambda p: p.get("standard") == "clearance"),
        _num("diameter", "Hole diameter", minimum=1e-6, visible=lambda p: p.get("standard") == "custom"),
        _choice("style", "Hole style", [("simple", "Simple"), ("counterbore", "Counterbore"),
                                        ("countersink", "Countersink")]),
        _num("counterbore_dia", "Counterbore diameter",
             visible=lambda p: p.get("style") == "counterbore" and p.get("standard") in ("custom", "sketch")),
        _num("counterbore_depth", "Counterbore depth",
             visible=lambda p: p.get("style") == "counterbore" and p.get("standard") in ("custom", "sketch")),
        _num("countersink_dia", "Countersink diameter",
             visible=lambda p: p.get("style") == "countersink" and p.get("standard") in ("custom", "sketch")),
        _check("model_thread", "Model the thread (real geometry — slower)", visible=lambda p: p.get("standard") == "tapped"),
        _num("thread_clearance", "Print clearance", visible=lambda p: p.get("standard") == "tapped" and p.get("model_thread")),
        _check("left_hand", "Left-hand thread", visible=lambda p: p.get("standard") == "tapped" and p.get("model_thread")),
        _choice("end", "Termination", [("through", "Through all"), ("blind", "Blind")]),
        _num("depth", "Hole depth", visible=lambda p: p.get("end") == "blind"),
        _check("tip", "Drill point (118°)", visible=lambda p: p.get("end") == "blind"),
        {"t": "info", "key": "_info", "text": lambda p: _hole_info(p)},
    ],
}


def params_to_fields(ftype, p):
    """Selection-field contents for a feature's params."""
    f = {}
    if ftype in ("extrude", "revolve", "sweep"):
        f["profiles"] = [("region", r) for r in p.get("regions", [])] + [("face", r) for r in p.get("faces", [])]
    if ftype in SCOPE_FEATURES:
        f["scope"] = [("part", i) for i in p.get("scope", [])]
    if ftype == "sm_base":
        f["profiles"] = [("region", r) for r in p.get("regions", [])]
    if ftype == "sm_flange":
        f["edges"] = [("edge", r) for r in p.get("edges", [])]
    if ftype == "sweep":
        f["path"] = [("path", r) for r in p.get("path", [])]
    if ftype == "loft":
        f["profiles"] = [(it["kind"], it["ref"]) for it in p.get("profiles", [])]
    if ftype == "draft":
        f["faces"] = [("face", r) for r in p.get("faces", [])]
        f["neutral_sel"] = [("plane", p["neutral"])] if p.get("neutral") else []
    if ftype in ("split", "move"):
        f["parts"] = [("part", i) for i in p.get("parts", [])]
    if ftype == "mate_connector":
        ref = p.get("ref")
        f["element"] = [(p.get("kind", "face"), ref)] if ref and not ref.get("origin") else []
    if ftype == "split":
        f["plane_sel"] = [("plane", p["plane"])] if p.get("plane") else []
    if ftype == "move":
        a = p.get("axis") or {}
        f["axis_sel"] = [("axis", a)] if a and a.get("kind") != "std" else []
    if ftype == "revolve":
        ax = p.get("axis") or {}
        f["axis_sel"] = [("axis", ax)] if ax and ax.get("kind") != "std" else []
    if ftype in ("fillet", "chamfer"):
        f["edges"] = [("edge", r) for r in p.get("edges", [])]
    if ftype in ("shell", "thread", "delete_face", "move_face", "offset_surface"):
        f["faces"] = [("face", r) for r in p.get("faces", [])]
    if ftype == "plane":
        f["refs_sel"] = [(k, r) for k, r in F.plane_items(p)]
    if ftype == "mirror":
        f["parts"] = [("part", i) for i in p.get("parts", [])]
        f["plane_sel"] = [("plane", p["plane"])] if p.get("plane") else []
    if ftype == "linear_pattern":
        f["parts"] = [("part", i) for i in p.get("parts", [])]
        d = p.get("direction") or {}
        f["dir_sel"] = [("axis", d)] if d and d.get("kind") != "std" else []
    if ftype == "circular_pattern":
        f["parts"] = [("part", i) for i in p.get("parts", [])]
        a = p.get("axis") or {}
        f["axis_sel"] = [("axis", a)] if a and a.get("kind") != "std" else []
    if ftype == "boolean":
        f["tools"] = [("part", i) for i in p.get("tools", [])]
        f["targets"] = [("part", i) for i in p.get("targets", [])]
    return f


def fields_to_params(ftype, fields, p):
    if ftype in SCOPE_FEATURES:
        p["scope"] = [r for k, r in fields.get("scope", [])]
    if ftype == "sm_base":
        p["regions"] = [r for k, r in fields.get("profiles", []) if k == "region"]
    if ftype == "sm_flange":
        p["edges"] = [r for k, r in fields.get("edges", []) if k == "edge"]
    if ftype in ("extrude", "revolve", "sweep"):
        p["regions"] = [r for k, r in fields.get("profiles", []) if k == "region"]
        p["faces"] = [r for k, r in fields.get("profiles", []) if k == "face"]
    if ftype == "sweep":
        p["path"] = [r for k, r in fields.get("path", [])]
    if ftype == "loft":
        p["profiles"] = [{"kind": k, "ref": r} for k, r in fields.get("profiles", [])]
    if ftype == "draft":
        p["faces"] = [r for k, r in fields.get("faces", [])]
        sel = fields.get("neutral_sel", [])
        p["neutral"] = sel[0][1] if sel else {"kind": "std", "name": "Top"}
    if ftype in ("split", "move"):
        p["parts"] = [r for k, r in fields.get("parts", [])]
    if ftype == "mate_connector" and "element" in fields:
        sel = fields["element"]
        if sel:
            p["kind"], p["ref"] = sel[0]
        elif not (p.get("ref") or {}).get("origin"):
            p.update(ref=None, click=None, snap=None, x_ref=None)
    if ftype == "split":
        sel = fields.get("plane_sel", [])
        p["plane"] = sel[0][1] if sel else {"kind": "std", "name": "Right"}
    if ftype == "move":
        sel = fields.get("axis_sel", [])
        if sel:
            p["axis"] = sel[0][1]
            p["axis_std"] = ""
        else:
            p["axis"] = {"kind": "std", "name": p.get("axis_std") or "Z"}
    if ftype == "revolve":
        sel = fields.get("axis_sel", [])
        if sel:
            p["axis"] = sel[0][1]
            p["axis_std"] = ""
        elif p.get("axis_std"):
            p["axis"] = {"kind": "std", "name": p["axis_std"]}
        else:
            p["axis"] = {}
    if ftype in ("fillet", "chamfer"):
        p["edges"] = [r for k, r in fields.get("edges", [])]
    if ftype in ("shell", "thread", "delete_face", "move_face", "offset_surface"):
        p["faces"] = [r for k, r in fields.get("faces", [])]
    if ftype == "plane":
        p["refs"] = [{"k": k, "ref": r} for k, r in fields.get("refs_sel", [])]
        p.pop("ref", None)
    if ftype in ("mirror", "linear_pattern", "circular_pattern"):
        p["parts"] = [r for k, r in fields.get("parts", [])]
    if ftype == "mirror":
        sel = fields.get("plane_sel", [])
        p["plane"] = sel[0][1] if sel else {"kind": "std", "name": "Right"}
    if ftype == "linear_pattern":
        sel = fields.get("dir_sel", [])
        if sel:
            p["direction"] = sel[0][1]
            p["dir_std"] = ""
        else:
            p["direction"] = {"kind": "std", "name": p.get("dir_std") or "X"}
    if ftype == "circular_pattern":
        sel = fields.get("axis_sel", [])
        if sel:
            p["axis"] = sel[0][1]
            p["axis_std"] = ""
        else:
            p["axis"] = {"kind": "std", "name": p.get("axis_std") or "Z"}
    if ftype == "boolean":
        p["tools"] = [r for k, r in fields.get("tools", [])]
        p["targets"] = [r for k, r in fields.get("targets", [])]


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
        self.schema = SCHEMAS.get(self.ftype, [])
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
        if is_new and preselection:
            self._apply_preselection(preselection)
        first = next((f for f in self.fields.values() if not f.items), None) or next(iter(self.fields.values()), None)
        if first:
            self.activate(first)
        if self.ftype == "mate_connector":       # hover a face or edge: the same snap points the Assembly offers
            main.viewport.snap_provider = self.snap_candidates
        self._sync_visibility()
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
        ic.setPixmap(icon(self.ftype).pixmap(20, 20))
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
        self.err = QLabel("")
        self.err.setObjectName("dialogError")
        self.err.setWordWrap(True)
        self.err.hide()
        lay.addWidget(self.err)

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
                b.setChecked(p.get(spec["key"]) == val)
                b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                b.clicked.connect(lambda _=False, v=val: self._set(spec["key"], v))
                grp.addButton(b)
                h.addWidget(b)
            return w
        if t == "info":
            lab = QLabel("")
            lab.setWordWrap(True)
            lab.setStyleSheet("color:#2e9e44; font-weight:600;")
            self.widgets[spec["key"]] = lab
            self._info_rows.append((spec, lab))
            return lab
        if t == "sel":
            f = SelectionField(spec["key"], spec["label"], spec["want"], spec["multi"])
            f.activated.connect(self.activate)
            f.changed.connect(self._fields_changed)
            self.fields[spec["key"]] = f
            return f
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

            def done(ed=ed, k=spec["key"], unit=unit, mn=spec.get("min"), signed=spec.get("signed")):
                txt = ed.text()
                v = parse_number(txt, angle=(unit == "deg"), variables=self.base_state.vars)
                bad = v is None or (mn is not None and v < mn) or (signed and abs(v) < 1e-6)
                ed.setProperty("invalid", "true" if bad else "false")
                ed.style().unpolish(ed)
                ed.style().polish(ed)
                if not bad:
                    if has_vars(txt):
                        self._set(k, txt.strip())        # keep the expression so it follows the variable
                    else:
                        self._set(k, int(round(v)) if k == "count" else v)
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
            cb.addItem("— select —", None)
            for pid, part in self.base_state.parts.items():
                cb.addItem(getattr(part, "name", pid), pid)
            cb.setCurrentIndex(max(0, cb.findData(p.get(spec["key"]))))
            cb.currentIndexChanged.connect(lambda i, cb=cb, k=spec["key"]: self._set(k, cb.itemData(i)))
            cb.setToolTip("The part the origin connector belongs to. Assemblies mate it as that part's own connector.")
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
        self.schedule_preview()

    def _sync_visibility(self):
        p = self.feature["params"]
        for spec, lab in self._info_rows:
            txt = spec["text"](p)
            lab.setText(txt)
            if spec["key"] == "_info":           # warnings in red, hints in green
                lab.setStyleSheet("color:#d93025; font-weight:600;" if txt.startswith("⚠")
                                  else "color:#2e9e44; font-weight:600;")
        for spec, w in self.rows:
            vis = spec.get("visible")
            w.setVisible(True if vis is None else bool(vis(p)))
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
            kinds |= {"face", "edge"}
        if "edge" in want:
            kinds |= {"edge", "face"}
        if "axis" in want or "path" in want:
            kinds |= {"edge", "sketch_line"}
        if "plane" in want:
            kinds.add("plane")
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
        if self.ftype == "mate_connector" and pick.kind != "overlay":
            self._pick_connector(pick)
            return
        if pick.kind == "overlay":
            key, i = pick.key
            fld = self.fields.get(key)
            if fld and i < len(fld.items):
                fld.items.pop(i)
                self._fields_changed()
            return
        fld = self.active_field
        if fld is None:
            return
        item = pick_to_item(self.main.viewport, pick, fld.want, self.base_state)
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

    # ------------------------------------------------------------------ mate connector
    def snap_candidates(self, pick: Pick):
        """Snap points the viewport draws while hovering a face or edge (assembly.snap_candidates, as in the
        Assembly); the studio's coordinates are the world's, so "pw" is the point itself."""
        shape = self.main.viewport.part_shapes.get(pick.key[0]) if pick.kind in ("face", "edge") else None
        if shape is None:
            return []
        return [dict(c, pw=np.asarray(c["p"], float)) for c in AS.snap_candidates(shape, pick.kind, pick.key[1])]

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
        p = self.feature["params"]
        kind, ref = item
        try:
            pid, sub = F.resolve(self.base_state, ref, kind)
            shape = self.base_state.parts[pid].shape
            subs = K.faces(shape) if kind == "face" else K.edges(shape)
            index = next(i for i, s in enumerate(subs) if s.IsSame(sub))
            named = self.base_state.named(pid)
            st = None
            if snap is not None:
                vshape = self.main.viewport.part_shapes.get(pick.key[0], shape)
                edge_ref = None
                if snap.get("edge") is not None:
                    edge_ref = AS._ref_signature(vshape, "edge", snap["edge"], named)
                    nm = ((getattr(self.base_state, "engine_names", None) or {}).get(pick.key[0]) or {}) \
                        .get("edge", {}).get(snap["edge"] + 1)
                    if nm:
                        edge_ref["name"] = nm
                st = {"type": snap["type"], "p": [float(a) for a in snap["p"]], "edge_ref": edge_ref}
                fr = AS.snap_frame(shape, kind, index, st, named)
            else:
                fr = AS.connector_frame(shape, kind, index, click)
            p["click"] = [float(a) for a in click] if click is not None else None
            p["snap"] = st
            p["x_ref"] = AS.connector_x_ref(fr)
        except Exception:       # the preview shows why the connector is red; the picked reference is kept
            p["click"], p["snap"], p["x_ref"] = None, None, None
        self.schedule_preview()

    def _apply_preselection(self, picks):
        for pk in picks:
            if self._pick_sketch(pk):
                break
        used = set()
        for key, fld in self.fields.items():
            for i, pk in enumerate(picks):
                if i in used or (not fld.multi and fld.items):
                    continue
                item = pick_to_item(self.main.viewport, pk, fld.want, self.base_state)
                if item is None:
                    continue
                used.add(i)
                fld.toggle(item)
                if self.ftype == "mate_connector":
                    self._stamp_connector(item, pk, None, None)
        self.main.viewport.clear_selection()

    def _fields_changed(self):
        fields = {k: f.items for k, f in self.fields.items()}
        fields_to_params(self.ftype, fields, self.feature["params"])
        self._sync_visibility()
        self.schedule_preview()

    def _set(self, key, value):
        p = self.feature["params"]
        p[key] = value
        if key in ("axis_std", "dir_std") and value:
            # choosing a world axis replaces a picked axis
            fk = "axis_sel" if key == "axis_std" else "dir_sel"
            if fk in self.fields:
                self.fields[fk].items = []
        fields = {k: f.items for k, f in self.fields.items()}
        fields_to_params(self.ftype, fields, p)
        self._sync_visibility()
        self.schedule_preview()

    def overlays(self):
        """Geometry of the current selections, in the pre-feature model, for highlighting."""
        out = []
        half = self.main.viewport.plane_half()
        for key, fld in self.fields.items():
            for i, item in enumerate(fld.items):
                g = item_geometry(self.base_state, item, half)
                if g is not None:
                    out.append(((key, i), g))
        if self.ftype == "derived":
            out += self._derived_location_overlays()
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

    def manipulators(self):
        """Onshape-style arrows: drag to set extrude depth / plane offset."""
        try:
            p = F.prepare(self.feature, self.base_state)["params"]      # expressions -> numbers
        except K.KernelError:
            return []
        try:
            if self.ftype == "extrude":
                if p.get("end") == "through":
                    return []
                profs = F._profiles(self.base_state, p)
                if not profs:
                    return []
                n = np.asarray(F._profile_normal(self.base_state, p, profs), float)
                if p.get("op") in ("remove", "intersect") and not p.get("regions"):
                    n = -n                   # cuts from a face go into the part
                infos = [K.face_info(f) for f in profs]
                area = sum(i["area"] for i in infos) or 1.0
                origin = sum(np.array(i["c"]) * i["area"] for i in infos) / area
                symmetric = p.get("end") == "symmetric"
                depth, flip = F.signed_extent(p.get("depth", 10), p.get("flip"))
                value = depth / 2 if symmetric else (-depth if flip else depth)

                def on_drag(v, symmetric=symmetric):
                    if symmetric:
                        self._set_quiet("depth", round(abs(v) * 2, 6))
                    else:
                        self._set_quiet("depth", round(abs(v), 6))
                        self._set_quiet("flip", v < 0)
                    self.main.viewport.manipulators[0]["label"] = fmt(self.feature["params"]["depth"]) + " mm"
                    self.schedule_preview()
                return [{"origin": origin, "dir": n, "value": value, "on_drag": on_drag,
                         "on_done": self.schedule_preview, "snap": self._snap_step(),
                         "min": 1e-3 if symmetric else None, "label": fmt(depth) + " mm"}]
            if self.ftype == "plane":
                if F.plane_type(p) != "offset":
                    return []
                planes_ = [(k, r) for k, r in F.plane_items(p) if F.plane_role(k, r) == "plane"]
                if planes_:
                    k_, r_ = planes_[0]
                    base = F.resolve_plane(self.base_state, r_ if k_ == "plane" else {"kind": "face", "face": r_})
                else:
                    base = K.std_plane("Top")
                off = float(p.get("offset", 0))
                value = -off if p.get("flip") else off

                def on_drag(v):
                    self._set_quiet("offset", round(abs(v), 6))
                    self._set_quiet("flip", v < 0)
                    self.main.viewport.manipulators[0]["label"] = fmt(abs(v)) + " mm"
                    self.schedule_preview()
                return [{"origin": base.origin, "dir": base.normal, "value": value, "on_drag": on_drag,
                         "on_done": self.schedule_preview, "snap": self._snap_step(), "label": fmt(off) + " mm"}]
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
        err = self.main.show_preview(self.feature, self.index, self.overlays())
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
                and self.feature["params"].get("owner") not in self.base_state.parts:
            self.err.setText("⚠ Pick the part that owns the origin connector")
            self.err.show()
            return
        self.feature["name"] = self.title.text().strip() or self.feature["name"]
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
