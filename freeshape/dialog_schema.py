"""The feature dialogs' schema, without Qt: SCHEMAS (the rows of each feature's dialog), the builders that make them
and the conversion between a feature's params and its selection fields. `freeshape.ui.dialogs` draws these rows with
Qt; the Browser client gets them from `describe`, as plain data. Moved out of the Qt module unchanged (I5.1)."""
from __future__ import annotations

import numpy as np

from types import SimpleNamespace
from typing import NamedTuple

from . import assembly as AS
from . import features as F
from . import kernel as K
from .picking import make_item
from .expr import fmt

OP_LABELS = [("new", "New"), ("add", "Add"), ("remove", "Remove"), ("intersect", "Intersect")]
SURFACE_OP_LABELS = OP_LABELS[:2]        # Remove and Intersect are not available for surfaces
CREATE_LABELS = [("solid", "Solid"), ("surface", "Surface")]
THIN_CREATE_LABELS = CREATE_LABELS + [("thin", "Thin")]            # Extrude, Revolve and Sweep

def _thin_fields(what):
    """The Thin rows of Extrude and Revolve, named as in Onshape. Thickness 2 and Flip wall go when Mid plane
    splits Thickness 1 half each side."""
    thin = lambda p: p.get("creation") == "thin"
    return [
        _check("mid_plane", "Mid plane", visible=thin),
        _num("thickness1", "Thickness 1", minimum=0, visible=thin),
        _num("thickness2", "Thickness 2", minimum=0, visible=lambda p: thin(p) and not p.get("mid_plane")),
        _check("flip_wall", "Flip wall", visible=lambda p: thin(p) and not p.get("mid_plane")),
    ]


def _fill_item(e):
    """The selection item of one Fill edge: a model edge, or the chain of sketch curves a picked curve belongs to."""
    ref = e.get("ref") or {}
    return ("edge", ref["edge"]) if ref.get("kind") == "edge" else ("path", ref)


def _fill_edge(k, r, prev=None):
    """One Fill edge from a selection item; Position unless `prev` (the same edge before) says otherwise."""
    return {"ref": {"kind": "edge", "edge": r} if k == "edge" else r,
            "continuity": prev.get("continuity", "position") if prev else "position",
            "faces": list(prev.get("faces") or []) if prev else []}


def _split_item(tool):
    """The selection item a Split tool shows as: a surface tool is a part pick."""
    return ("part" if tool["kind"] == "surface" else tool["kind"], tool["ref"])


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


def _axis_info(p):
    """What the axis dialog says about the current selection."""
    items = F.axis_items(p)
    typ = F.axis_type(p)
    if not items:
        return ("Select an edge, a round or flat face, two points or two planes. The axis type is picked from what "
                "you select.")
    if typ is None:
        return ("⚠ That selection doesn't fit an axis type. Try an edge, a round or flat face, two points, two "
                "planes, or a point and a face.")
    if (p.get("type") or "auto") == "auto":
        return f"Type: {F.AXIS_TYPE_NAMES.get(typ, typ)}"
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


def _sel(key, label, want, multi=True, visible=None, number=False, tip=None):
    return {"t": "sel", "key": key, "label": label, "want": want, "multi": multi, "visible": visible,
            "number": number, "tip": tip}


def _sel_list(key, label, want, visible=None, tip=None):
    """A list field where one pick is one entry (GuideListField)."""
    return {**_sel(key, label, want, True, visible, False, tip), "list": True}


SCOPE_OPS = ("add", "remove", "intersect")
SCOPE_FEATURES = ("extrude", "revolve", "sweep", "loft")


SWEEP_CONTROLS = [("", "None"), ("keep", "Keep profile orientation"), ("lock", "Lock profile direction")]
LOFT_CONDITIONS = [("", "None"), ("normal", "Normal to profile"), ("tangent", "Tangent to profile")]
LOFT_END_CHOICES = LOFT_CONDITIONS + [("match_tangent", "Match tangent")]     # I7.5: with Adjacent faces


_LOFT_GUIDES_WHY = ("Guides, end conditions, a closed loft and a point profile cannot be combined yet: "
                    "untick Guides and continuity, Closed, or take the point out first")


_LOFT_SURFACE_WHY = "A surface loft takes no end conditions: choose Solid to set them"


_BOUNDARY_TIP = ("A surface through curves in a U direction and, optionally, a V direction. It does not match "
                 "tangent or curvature to neighbouring faces (use Fill for a patch with tangency), and has no "
                 "magnitude, isocurve display or trimming. V curves need OpenCascade 8.0.")


def _loft_surface(p):
    """True when a loft is made as a surface (Create = Surface, I7.8)."""
    return p.get("creation") == "surface"


def _loft_point(p):
    """True when a loft's profile list has a point (a vertex or a sketch point) in it (I7.7)."""
    return any(isinstance(it, dict) and it.get("kind") in ("vertex", "sketch") for it in p.get("profiles") or [])


def _loft_plain(p):
    """True when nothing in a loft's params rules out end conditions and guides: no Closed, no point profile."""
    return not (p.get("closed") or _loft_point(p))


def _loft_unfold(p):
    """The dialog edits a loft's `start` and `end` ({cond, mag, faces}) as flat fields start_cond, start_mag,
    start_faces, ..."""
    for end in ("start", "end"):
        c = p.get(end) if isinstance(p.get(end), dict) else {}
        p[f"{end}_cond"] = c.get("cond") or ""
        p[f"{end}_mag"] = c.get("mag", 1.0)
        p[f"{end}_faces"] = list(c.get("faces") or [])
    p["guides_on"] = any(g for g in p.get("guides") or [])


def _loft_fold(p, strip=False):
    """The flat dialog fields back into `start` and `end`. No condition (or a ruled loft, which hides
    them) leaves no key, so an old loft keeps the legacy route; strip=True drops the flat fields.
    `guides` goes the same way: no tick, a ruled loft or no entries leaves no key. `closed` (I7.7) is
    kept only when ticked, and a closed loft or a point profile (which grey the conditions and guides
    out) leaves none of them."""
    plain = _loft_plain(p)
    if p.get("front") and any(p.get("guides") or []):
        p["ruled"] = False               # a Boundary surface with V curves is smooth (the row is greyed out)
    if not p.get("closed"):
        p.pop("closed", None)
    if not _loft_surface(p):
        p.pop("curves", None)            # a solid loft saves no curves key
    if "guides_on" in p:
        if p.get("ruled") or not plain or not p["guides_on"] or not any(p.get("guides") or []):
            p.pop("guides", None)
        if strip:
            p.pop("guides_on", None)
    for end in ("start", "end"):
        if f"{end}_cond" not in p:
            continue
        cond = "" if p.get("ruled") or not plain or _loft_surface(p) else p.get(f"{end}_cond") or ""
        if cond:
            p[end] = {"cond": cond, "mag": p.get(f"{end}_mag", 1.0)}
            if cond == "match_tangent" and p.get(f"{end}_faces"):     # none picked: no key, the default faces
                p[end]["faces"] = list(p[f"{end}_faces"])
        else:
            p.pop(end, None)
        if strip:
            p.pop(f"{end}_cond", None)
            p.pop(f"{end}_mag", None)
            p.pop(f"{end}_faces", None)


def thicken_op_for(state, p):
    """The operation a Thicken pick implies: "add" when a picked face belongs to a solid (the new solid joins
    it), "new" when only surface parts, or faces of them, are picked, None with nothing picked."""
    if not p.get("faces") and not p.get("parts"):
        return None
    for r in p.get("faces", []):
        part = state.parts.get(r.get("part")) if isinstance(r, dict) else None
        if part is not None and not K.is_surface(part.shape):
            return "add"
    return "new"


def _scope():
    return _sel("scope", "Merge scope (empty = every part it touches)", {"part"},
                visible=lambda p: p.get("op", "add") in SCOPE_OPS)


def _ops(options=None):
    return {"t": "ops", "key": "op", "options": options or OP_LABELS}


OWNER_PICK = "— select —"                              # Owner part: the origin connector has no default owner
OWNER_SAME = "Same as the picked face or edge"         # ...any other connector defaults to its element's part
def _pattern_of(what):
    """The 'Parts / Features' switch and feature picker shared by Mirror and the patterns."""
    return [
        _choice("type", f"What to {what}", [("parts", "Parts"), ("features", "Features")]),
        _sel("features", "Features (click them in the feature list)", {"feature"},
             visible=lambda p: p.get("type") == "features"),
    ]


_UP_TO_FIELDS = [
    _sel("upto_face", "Face or plane to go up to", {"face", "plane"}, multi=False,
         visible=lambda p: p.get("end") == "up_to_face"),
    _sel("upto_part", "Part to go up to", {"part"}, multi=False, visible=lambda p: p.get("end") == "up_to_part"),
    _sel("upto_vertex", "Vertex to go up to", {"vertex"}, multi=False,
         visible=lambda p: p.get("end") == "up_to_vertex"),
]


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
    "sm_hem": [
        _sel("edges", "Edges to hem", {"edge"}),
        _num("length", "Hem length (to the outside of the fold)", minimum=1e-6),
        _check("down", "Fold the other way"),
        _check("own_radius", "Choose the fold radius (else half the thickness)"),
        _num("radius", "Fold radius (inside)", visible=lambda p: p.get("own_radius"), minimum=0.01),
    ],
    "sm_jog": [
        _sel("edges", "Edges to jog from", {"edge"}),
        _num("offset", "Offset (the step)", minimum=1e-6),
        _num("length", "Length after the step (to the outside)", minimum=1e-6),
        _check("down", "Step the other way"),
        _check("own_radius", "Use a different bend radius"),
        _num("radius", "Bend radius (inside)", visible=lambda p: p.get("own_radius"), minimum=0),
    ],
    "sm_tab": [
        _sel("profiles", "Sketch region(s) on a panel's face, touching it", {"region"}),
    ],
    "sm_convert": [
        _sel("faces", "One of the plate's big flat faces", {"face"}, multi=False),
        _num("radius", "Bend radius for flanges added later", minimum=0),
        _num("k", "K-factor (0.44 suits most steel / aluminium)", unit="", minimum=1e-6),
        _check("relief", "Corner reliefs where two bends meet"),
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
        {"t": "ops", "key": "op", "options": OP_LABELS, "visible": lambda p: p.get("creation") != "surface"},
        {"t": "ops", "key": "op", "options": SURFACE_OP_LABELS, "visible": lambda p: p.get("creation") == "surface"},
        {"t": "ops", "key": "creation", "options": THIN_CREATE_LABELS, "default": "solid"},
        _sel("profiles", "Faces and sketch regions to extrude", {"region", "face"},
             visible=lambda p: p.get("creation") != "surface"),
        _sel("curves", "Sketch curves to extrude", {"path"},
             visible=lambda p: p.get("creation") in ("surface", "thin")),
        *_thin_fields("extrude"),
        _choice("end", "End type", [("blind", "Blind"), ("symmetric", "Symmetric"), ("through", "Through all")]
                + F.UP_TO_LABELS),
        _num("depth", "Depth", visible=lambda p: p.get("end") not in ("through",) + F.UP_TO, signed=True),
        *_UP_TO_FIELDS,
        _num("offset", "Stop short by",
             visible=lambda p: p.get("end") in ("up_to_face", "up_to_vertex")),
        _check("flip", "Opposite direction", visible=lambda p: p.get("end") != "symmetric"),
        _check("second", "Second end position", visible=lambda p: p.get("end") == "blind"),
        _num("depth2", "Second depth", visible=lambda p: p.get("end") == "blind" and p.get("second")),
        _check("both", "Both directions", visible=lambda p: p.get("end") == "through"),
        _check("draft", "Draft", visible=lambda p: p.get("end") in ("blind", "symmetric")),
        _num("draft_angle", "Draft angle", unit="deg", minimum=1e-6,
             visible=lambda p: p.get("end") in ("blind", "symmetric") and p.get("draft")),
        _check("draft_flip", "Draft the other way",
               visible=lambda p: p.get("end") in ("blind", "symmetric") and p.get("draft")),
        _scope(),
    ],
    "revolve": [
        {"t": "ops", "key": "op", "options": OP_LABELS, "visible": lambda p: p.get("creation") != "surface"},
        {"t": "ops", "key": "op", "options": SURFACE_OP_LABELS, "visible": lambda p: p.get("creation") == "surface"},
        {"t": "ops", "key": "creation", "options": THIN_CREATE_LABELS, "default": "solid"},
        _sel("profiles", "Faces and sketch regions to revolve", {"region", "face"},
             visible=lambda p: p.get("creation") != "surface"),
        _sel("curves", "Sketch curves to revolve", {"path"},
             visible=lambda p: p.get("creation") in ("surface", "thin")),
        *_thin_fields("revolve"),
        _sel("axis_sel", "Revolve axis (axis, sketch line or edge)", {"axis"}, multi=False),
        _choice("axis_std", "…or world axis", [("", "—"), ("X", "X axis"), ("Y", "Y axis"), ("Z", "Z axis")]),
        _choice("end", "Revolve type", [("full", "Full"), ("one", "One direction"), ("symmetric", "Symmetric")]
                + F.UP_TO_LABELS),
        _num("angle", "Revolve angle", unit="deg", visible=lambda p: p.get("end") in ("one", "symmetric"),
             signed=True),
        *_UP_TO_FIELDS,
        _num("offset", "Stop short by",
             visible=lambda p: p.get("end") == "up_to_face"),
        _check("flip", "Opposite direction", visible=lambda p: p.get("end") == "one" or p.get("end") in F.UP_TO),
        _scope(),
    ],
    "fillet": [
        _sel("edges", "Edges or faces to fillet", {"edge"}),
        _choice("fillet_type", "Type", [("constant", "Constant radius"), ("variable", "Variable radius")]),
        _num("radius", "Radius", minimum=1e-6),
        _sel("vertices", "Vertices (a radius at each)", {"vertex"}, number=True,
             tip="The radius changes smoothly between the vertices. Pick vertices at the ends of the selected edges.",
             visible=lambda p: p.get("fillet_type") == "variable"),
    ],
    "chamfer": [
        _sel("edges", "Edges or faces to chamfer", {"edge"}),
        _choice("chamfer_type", "Type", [("equal", "Equal distance"), ("two", "Two distances"),
                                         ("angle", "Distance and angle")]),
        _num("distance", "Distance", minimum=1e-6),
        _num("distance2", "Distance 2", minimum=1e-6, visible=lambda p: p.get("chamfer_type") == "two"),
        _num("angle", "Angle", unit="deg", visible=lambda p: p.get("chamfer_type") == "angle"),
        _check("flip", "Flip (swap the faces)", visible=lambda p: p.get("chamfer_type") in ("two", "angle")),
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
    "thicken": [
        _ops(),
        _sel("faces", "Surface parts and faces to thicken", {"face", "part"}),
        _num("thickness1", "Thickness 1", minimum=1e-6),
        _num("thickness2", "Thickness 2", minimum=0, visible=lambda p: not p.get("mid_plane")),
        _check("mid_plane", "Mid plane"),
        _check("flip", "Opposite direction"),
        _check("keep_tools", "Keep tools"),
        {"t": "info", "key": "_info", "text": lambda p: "Thickness is the total, split half each side."
         if p.get("mid_plane") else ""},
        _scope(),
    ],
    "enclose": [
        _ops(),
        _sel("entities", "Surface parts, faces and planes that surround a volume", {"part", "face", "plane"}),
        _check("keep_tools", "Keep tools"),
        _scope(),
    ],
    "fill": [
        _ops(SURFACE_OP_LABELS),
        _sel("edges", "Edges and sketch curves around the hole", {"edge", "path"}),
        {"t": "fill_rows", "key": "_fill_rows"},
        _sel("guides", "Guide points the fill passes through (optional)", {"vertex"}),
        _scope(),
    ],
    "delete_part": [
        _sel("parts", "Parts to delete", {"part"}),
    ],
    "axis": [
        _choice("type", "Axis type", F.AXIS_TYPES),
        _sel("refs_sel", "Edges, faces, points, planes, axes and connectors",
             {"face", "edge", "vertex", "plane", "sketch_line", "sketch_point", "axis_feature", "connector"}),
        {"t": "info", "key": "_info", "text": lambda p: _axis_info(p)},
        _choice("connector_axis", "Connector axis", [("z", "Z"), ("x", "X"), ("y", "Y")],
                visible=lambda p: F.axis_type(p) == "connector"),
        _check("flip", "Opposite direction", visible=lambda p: F.axis_type(p) is not None),
    ],
    "plane": [
        _choice("type", "Plane type", F.PLANE_TYPES),
        _sel("refs_sel", "Planes, faces, edges, axes, sketch curves and points",
             {"plane", "face", "edge", "vertex", "sketch_line", "sketch_point", "axis_feature", "axis_connector"}),
        {"t": "info", "key": "_info", "text": lambda p: _plane_info(p)},
        _num("offset", "Offset distance", visible=lambda p: F.plane_type(p) == "offset"),
        _num("angle", "Angle", unit="deg", visible=lambda p: F.plane_type(p) == "line_angle"),
        _check("flip", "Flip direction", visible=lambda p: F.plane_type(p) is not None),
    ],
    "mirror": [
        *_pattern_of("mirror"),
        {**_ops([("new", "New"), ("add", "Add")]), "visible": lambda p: p.get("type") != "features"},
        _sel("parts", "Parts to mirror", {"part"}, visible=lambda p: p.get("type") != "features"),
        _sel("plane_sel", "Mirror plane", {"plane"}, multi=False),
    ],
    "linear_pattern": [
        *_pattern_of("pattern"),
        {**_ops([("new", "New"), ("add", "Add")]), "visible": lambda p: p.get("type") != "features"},
        _sel("parts", "Parts to pattern", {"part"}, visible=lambda p: p.get("type") != "features"),
        _sel("dir_sel", "Direction (axis, edge or sketch line)", {"axis"}, multi=False),
        _choice("dir_std", "…or world axis", [("", "—"), ("X", "X axis"), ("Y", "Y axis"), ("Z", "Z axis")]),
        _num("spacing", "Distance"),
        _num("count", "Instance count", unit=""),
        _check("flip", "Opposite direction"),
        _check("dir2_on", "Second direction"),
        _sel("dir2_sel", "Second direction (axis, edge or sketch line)", {"axis"}, multi=False,
             visible=lambda p: p.get("dir2_on")),
        _choice("dir2_std", "…or world axis", [("", "—"), ("X", "X axis"), ("Y", "Y axis"), ("Z", "Z axis")],
                visible=lambda p: p.get("dir2_on")),
        _num("spacing2", "Distance 2", visible=lambda p: p.get("dir2_on")),
        _num("count2", "Instance count 2", unit="", visible=lambda p: p.get("dir2_on")),
        _check("flip2", "Opposite second direction", visible=lambda p: p.get("dir2_on")),
    ],
    "circular_pattern": [
        *_pattern_of("pattern"),
        {**_ops([("new", "New"), ("add", "Add")]), "visible": lambda p: p.get("type") != "features"},
        _sel("parts", "Parts to pattern", {"part"}, visible=lambda p: p.get("type") != "features"),
        _sel("axis_sel", "Axis of pattern (axis, edge or sketch line)", {"axis"}, multi=False),
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
        {"t": "ops", "key": "op", "options": OP_LABELS, "visible": lambda p: p.get("creation") != "surface"},
        {"t": "ops", "key": "op", "options": SURFACE_OP_LABELS, "visible": lambda p: p.get("creation") == "surface"},
        {"t": "ops", "key": "creation", "options": THIN_CREATE_LABELS, "default": "solid"},
        _sel("profiles", "Faces and sketch regions to sweep", {"region", "face"},
             visible=lambda p: p.get("creation") != "surface"),
        _sel("curves", "Sketch curves to sweep (open or closed)", {"path"},
             visible=lambda p: p.get("creation") in ("surface", "thin")),
        *_thin_fields("sweep"),
        _sel("path", "Sweep path (sketch curves or edges)", {"path"}),
        _choice("control", "Profile control", SWEEP_CONTROLS),
        _sel("lock_dir", "Direction (axis, edge or sketch line)", {"axis"}, multi=False,
             visible=lambda p: p.get("control") == "lock"),
        _scope(),
    ],
    "rib": [
        _sel("path", "Sketch curves for the rib (an open chain of lines and arcs)", {"path"}),
        _sel("parts", "Part for the rib", {"part"}, multi=False),
        _num("thickness", "Thickness", minimum=1e-6),
        _check("flip", "Opposite direction"),
    ],
    "loft": [
        {"t": "ops", "key": "op", "options": OP_LABELS, "visible": lambda p: p.get("creation") != "surface"},
        {"t": "ops", "key": "op", "options": SURFACE_OP_LABELS, "visible": lambda p: p.get("creation") == "surface"},
        {"t": "ops", "key": "creation", "options": CREATE_LABELS, "default": "solid"},
        _sel("profiles", "Profiles — pick them in order (a vertex or a sketch point may be the first or the last)",
             {"region", "face", "vertex", "sketch_point"}, visible=lambda p: p.get("creation") != "surface"),
        _sel("curves", "Sketch curves or edges to loft through — pick them in order", {"path"},
             visible=lambda p: p.get("creation") == "surface"),
        _check("ruled", "Straight sides (ruled)"),
        {**_choice("start_cond", "Start profile condition", LOFT_END_CHOICES, visible=lambda p: not p.get("ruled")),
         "enabled": lambda p: not p.get("guides_on") and _loft_plain(p) and not _loft_surface(p),
         "why": _LOFT_GUIDES_WHY, "why_if": (_loft_surface, _LOFT_SURFACE_WHY)},
        _num("start_mag", "Magnitude", unit="", signed=True,
             visible=lambda p: not p.get("ruled") and bool(p.get("start_cond"))),
        {**_sel("start_faces", "Adjacent faces", {"face"},
                visible=lambda p: not p.get("ruled") and p.get("start_cond") == "match_tangent"),
         "enabled": lambda p: not p.get("guides_on") and _loft_plain(p) and not _loft_surface(p),
         "why": _LOFT_GUIDES_WHY, "why_if": (_loft_surface, _LOFT_SURFACE_WHY)},
        {**_choice("end_cond", "End profile condition", LOFT_END_CHOICES, visible=lambda p: not p.get("ruled")),
         "enabled": lambda p: not p.get("guides_on") and _loft_plain(p) and not _loft_surface(p),
         "why": _LOFT_GUIDES_WHY, "why_if": (_loft_surface, _LOFT_SURFACE_WHY)},
        _num("end_mag", "Magnitude", unit="", signed=True,
             visible=lambda p: not p.get("ruled") and bool(p.get("end_cond"))),
        {**_sel("end_faces", "Adjacent faces", {"face"},
                visible=lambda p: not p.get("ruled") and p.get("end_cond") == "match_tangent"),
         "enabled": lambda p: not p.get("guides_on") and _loft_plain(p) and not _loft_surface(p),
         "why": _LOFT_GUIDES_WHY, "why_if": (_loft_surface, _LOFT_SURFACE_WHY)},
        {**_check("guides_on", "Guides and continuity", visible=lambda p: not p.get("ruled")),
         "enabled": lambda p: (_loft_surface(p) or not (p.get("start_cond") or p.get("end_cond"))) and _loft_plain(p),
         "why": "Guides cannot be combined with end conditions, a closed loft or a point profile yet: "
                "clear the start and end profile conditions, Closed and the point first"},
        {**_check("closed", "Closed (the last profile joins the first)",
                  visible=lambda p: len(p.get("curves" if _loft_surface(p) else "profiles") or []) >= 3),
         "enabled": lambda p: not (p.get("start_cond") or p.get("end_cond") or p.get("guides_on") or _loft_point(p)),
         "why": "A closed loft cannot be combined with end conditions, guides or a point profile yet: "
                "clear those first"},
        _sel_list("guides", "Guides — one pick is one guide (sketch curves or an edge)", {"path"},
                  visible=lambda p: not p.get("ruled") and bool(p.get("guides_on"))),
        _scope(),
    ],
    # I7.9: a surface loft opened through its front door (params["front"] == "boundary_surface"): the loft's
    # `curves` are the U direction, its `guides` the V direction. Same runner, same saved keys.
    "boundary_surface": [
        {"t": "ops", "key": "op", "options": SURFACE_OP_LABELS},
        _sel_list("curves", "U direction profiles — pick sketch curves or edges in order",
                  {"path"}, tip=_BOUNDARY_TIP),
        _sel_list("guides", "V direction profiles (optional; two or more, each touching every U curve)",
                  {"path"}, tip=_BOUNDARY_TIP),
        {**_check("ruled", "Straight sides (ruled)"),
         "enabled": lambda p: not any(p.get("guides") or []),
         "why": "Straight sides apply to U curves only: remove the V curves first"},
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
        _sel("tools", "Split with — planes, faces, sketch curves or surfaces", {"plane", "face", "path", "part"}),
        {"t": "split_tools", "key": "_split_tools"},
        _check("keep_tools", "Keep tools"),
        {"t": "info", "key": "_info", "text": lambda p: "" if F.split_tools(p) else
         "Pick one or more: each cuts right through. Untick “Keep both sides” to keep only the side its arrow "
         "points to."},
    ],
    "move": [
        _sel("parts", "Parts to move", {"part"}),
        _num("dx", "Move X"),
        _num("dy", "Move Y"),
        _num("dz", "Move Z"),
        _num("rotate", "Rotate", unit="deg"),
        _sel("axis_sel", "Rotate about (axis, edge or sketch line)", {"axis"}, multi=False,
             visible=lambda p: _isnum(p.get("rotate")) is not False),
        _choice("axis_std", "…or world axis", [("", "—"), ("X", "X axis"), ("Y", "Y axis"), ("Z", "Z axis")],
                visible=lambda p: _isnum(p.get("rotate")) is not False),
        _num("scale", "Scale", unit="", minimum=1e-6),
        _sel("center", "Scale about (vertex, default the origin)", {"vertex"}, multi=False,
             visible=lambda p: _isnum(p.get("scale")) is not False and p.get("scale") not in (None, "", 1, 1.0, "1")),
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
        _sel("element", "Face, edge or sketch geometry (hover for snap points)", {"element"}, multi=False),
        {"t": "owner_part", "key": "owner", "label": "Owner part"},
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
    #   Parts, Sketches and Mate connectors tick lists: the tabs of the one `derived_tabs` row. Add a tick list
    #   by adding its spec to "tabs".
    #   Location and Placement base: after that.
    "derived": [
        {"t": "derived_document", "key": "source", "label": "Document"},
        {"t": "derived_source", "key": "source", "label": "Part Studio"},
        {"t": "derived_tabs", "tabs": [
            {"t": "derived_checks", "key": "parts", "label": "Parts"},
            {"t": "derived_checks", "key": "sketches", "label": "Sketches"},
            {"t": "derived_connectors", "key": "connectors", "label": "Mate connectors"},
        ]},
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
    if ftype.startswith("plugin:"):
        from . import plugins
        for spec in plugins.dialog_schema(ftype):
            if spec["t"] == "sel":
                f[spec["key"]] = [tuple(it) for it in p.get(spec["key"]) or []]
        return f
    if ftype in ("mirror", "linear_pattern", "circular_pattern"):
        f["features"] = [("feature", i) for i in p.get("features", [])]
    if ftype in ("extrude", "revolve"):
        u = p.get("up_to") or {}
        f["upto_face"] = [(u["kind"], u["ref"])] if u.get("kind") in ("face", "plane") else []
        f["upto_vertex"] = [("vertex", u["ref"])] if u.get("kind") == "vertex" else []
        f["upto_part"] = [("part", u["id"])] if u.get("kind") == "part" else []
    if ftype in ("extrude", "revolve", "sweep"):
        f["profiles"] = [("region", r) for r in p.get("regions", [])] + [("face", r) for r in p.get("faces", [])]
    if ftype in ("extrude", "revolve", "sweep", "loft"):
        f["curves"] = [("path", r) for r in p.get("curves", [])]
    if ftype in SCOPE_FEATURES:
        f["scope"] = [("part", i) for i in p.get("scope", [])]
    if ftype in ("sm_base", "sm_tab"):
        f["profiles"] = [("region", r) for r in p.get("regions", [])]
    if ftype in ("sm_flange", "sm_hem", "sm_jog"):
        f["edges"] = [("edge", r) for r in p.get("edges", [])]
    if ftype == "sm_convert":
        f["faces"] = [("face", r) for r in p.get("faces", [])]
    if ftype == "sweep":
        f["path"] = [("path", r) for r in p.get("path", [])]
        d = p.get("lock_dir") or {}
        f["lock_dir"] = [("axis", d)] if d and d.get("kind") != "std" else []
    if ftype == "rib":
        f["path"] = [("path", r) for r in p.get("path", [])]
        f["parts"] = [("part", i) for i in p.get("parts", [])]
    if ftype == "loft":
        f["profiles"] = [(it["kind"], it["ref"]) for it in p.get("profiles", [])]
        f["guides"] = [("path", g[0]) if len(g) == 1 else ("group", list(g)) for g in p.get("guides") or [] if g]
        for end in ("start", "end"):
            refs = p[f"{end}_faces"] if f"{end}_faces" in p else (p.get(end) or {}).get("faces")
            f[f"{end}_faces"] = [("face", r) for r in refs or []]
    if ftype == "draft":
        f["faces"] = [("face", r) for r in p.get("faces", [])]
        f["neutral_sel"] = [("plane", p["neutral"])] if p.get("neutral") else []
    if ftype in ("split", "move", "delete_part"):
        f["parts"] = [("part", i) for i in p.get("parts", [])]
    if ftype == "mate_connector":
        ref = p.get("ref")
        f["element"] = [(p.get("kind", "face"), ref)] if ref and not ref.get("origin") else []
    if ftype == "split":
        f["tools"] = [_split_item(t) for t in F.split_tools(p)]
    if ftype == "move":
        a = p.get("axis") or {}
        f["axis_sel"] = [("axis", a)] if a and a.get("kind") != "std" else []
        f["center"] = [("vertex", p["center"])] if p.get("center") else []
    if ftype == "revolve":
        ax = p.get("axis") or {}
        f["axis_sel"] = [("axis", ax)] if ax and ax.get("kind") != "std" else []
    if ftype in ("fillet", "chamfer"):
        f["edges"] = [("edge", r) for r in p.get("edges", [])]
    if ftype == "fillet":
        rows = p.get("vertex_radii") or []
        f["vertices"] = [("vertex", e["vertex"]) for e in rows]
        f["vertices_numbers"] = [None if e.get("radius") == p.get("radius") else e.get("radius") for e in rows]
    if ftype in ("shell", "thread", "delete_face", "move_face", "offset_surface"):
        f["faces"] = [("face", r) for r in p.get("faces", [])]
    if ftype == "thicken":              # one field holds faces and surface parts
        f["faces"] = [("face", r) for r in p.get("faces", [])] + [("part", i) for i in p.get("parts", [])]
        f["scope"] = [("part", i) for i in p.get("scope", [])]
    if ftype == "fill":                 # one field holds model edges and sketch curves; guides are vertices
        f["edges"] = [_fill_item(e) for e in p.get("edges", [])]
        f["guides"] = [("vertex", r) for r in p.get("guides", [])]
        f["scope"] = [("part", i) for i in p.get("scope", [])]
    if ftype == "enclose":              # one field holds parts, faces and planes, one entity each
        f["entities"] = [(e["kind"], e["ref"]) for e in p.get("entities", [])]
        f["scope"] = [("part", i) for i in p.get("scope", [])]
    if ftype == "plane":
        f["refs_sel"] = [(k, r) for k, r in F.plane_items(p)]
    if ftype == "axis":
        f["refs_sel"] = [(k, r) for k, r in F.axis_items(p)]
    if ftype == "mirror":
        f["parts"] = [("part", i) for i in p.get("parts", [])]
        f["plane_sel"] = [("plane", p["plane"])] if p.get("plane") else []
    if ftype == "linear_pattern":
        f["parts"] = [("part", i) for i in p.get("parts", [])]
        d = p.get("direction") or {}
        f["dir_sel"] = [("axis", d)] if d and d.get("kind") != "std" else []
        d2 = p.get("dir2") or {}
        f["dir2_sel"] = [("axis", d2)] if d2 and d2.get("kind") != "std" else []
    if ftype == "circular_pattern":
        f["parts"] = [("part", i) for i in p.get("parts", [])]
        a = p.get("axis") or {}
        f["axis_sel"] = [("axis", a)] if a and a.get("kind") != "std" else []
    if ftype == "boolean":
        f["tools"] = [("part", i) for i in p.get("tools", [])]
        f["targets"] = [("part", i) for i in p.get("targets", [])]
    return f


def fields_to_params(ftype, fields, p):
    if ftype.startswith("plugin:"):
        from . import plugins
        for spec in plugins.dialog_schema(ftype):
            if spec["t"] == "sel":
                p[spec["key"]] = [[k, r] for k, r in fields.get(spec["key"], [])]
        return
    if ftype in SCOPE_FEATURES:
        p["scope"] = [r for k, r in fields.get("scope", [])]
    if ftype in ("sm_base", "sm_tab"):
        p["regions"] = [r for k, r in fields.get("profiles", []) if k == "region"]
    if ftype in ("sm_flange", "sm_hem", "sm_jog"):
        p["edges"] = [r for k, r in fields.get("edges", []) if k == "edge"]
    if ftype == "sm_convert":
        p["faces"] = [r for k, r in fields.get("faces", []) if k == "face"]
    if ftype in ("mirror", "linear_pattern", "circular_pattern"):
        p["features"] = [r for k, r in fields.get("features", []) if k == "feature"]
    if ftype in ("extrude", "revolve"):
        key = {"up_to_face": "upto_face", "up_to_vertex": "upto_vertex", "up_to_part": "upto_part"}.get(p.get("end"))
        sel = fields.get(key, []) if key else []
        if sel:
            k, r = sel[0]
            p["up_to"] = {"kind": "part", "id": r} if k == "part" else {"kind": k, "ref": r}
        else:
            p.pop("up_to", None)             # no target picked, or an end that has none: keep no stale reference
    if ftype in ("extrude", "revolve", "sweep"):
        p["regions"] = [r for k, r in fields.get("profiles", []) if k == "region"]
        p["faces"] = [r for k, r in fields.get("profiles", []) if k == "face"]
    if ftype in ("extrude", "revolve", "sweep", "loft"):
        curves = [r for k, r in fields.get("curves", [])]
        if curves:
            p["curves"] = curves
        else:
            p.pop("curves", None)            # a solid feature saves no curves key
    if ftype == "sweep":
        p["path"] = [r for k, r in fields.get("path", [])]
        lock = [r for k, r in fields.get("lock_dir", [])]
        if p.get("control") == "lock" and lock:
            p["lock_dir"] = lock[0]
        else:
            p.pop("lock_dir", None)
        if not p.get("control"):
            p.pop("control", None)           # None saves no key
    if ftype == "rib":
        p["path"] = [r for k, r in fields.get("path", [])]
        p["parts"] = [r for k, r in fields.get("parts", [])]
    if ftype == "loft":
        p["profiles"] = [{"kind": k, "ref": r} for k, r in fields.get("profiles", [])]
        p["guides"] = [list(r) if k == "group" else [r] for k, r in fields.get("guides", [])]
        for end in ("start", "end"):
            p[f"{end}_faces"] = [r for k, r in fields.get(f"{end}_faces", []) if k == "face"]
        _loft_fold(p)
    if ftype == "draft":
        p["faces"] = [r for k, r in fields.get("faces", [])]
        sel = fields.get("neutral_sel", [])
        p["neutral"] = sel[0][1] if sel else {"kind": "std", "name": "Top"}
    if ftype in ("split", "move", "delete_part"):
        p["parts"] = [r for k, r in fields.get("parts", [])]
    if ftype == "mate_connector" and "element" in fields:
        sel = fields["element"]
        if sel:
            p["kind"], p["ref"] = sel[0]
        elif not (p.get("ref") or {}).get("origin"):
            p.update(ref=None, click=None, snap=None, x_ref=None)
    if ftype == "split":
        old = list(F.split_tools(p))
        tools = []
        for k, r in fields.get("tools", []):
            k = "surface" if k == "part" else k           # a part pick is a surface part
            prev = next((t for t in old if t["kind"] == k and _same((k, t["ref"]), (k, r))), None)
            if prev is not None:
                old.remove(prev)
            tool = {"kind": k, "ref": r, "both": prev.get("both", True) if prev else True,
                    "flip": prev.get("flip", False) if prev else False}
            if prev is not None and prev.get("trim"):
                tool["trim"] = True
            tools.append(tool)
        p["tools"] = tools
        p.pop("plane", None)                      # an older split's single plane is now its first tool
        p.pop("keep", None)
    if ftype == "move":
        sel = fields.get("axis_sel", [])
        if sel:
            p["axis"] = sel[0][1]
            p["axis_std"] = ""
        else:
            p["axis"] = {"kind": "std", "name": p.get("axis_std") or "Z"}
        sel = fields.get("center", [])
        if sel:
            p["center"] = sel[0][1]
        else:
            p.pop("center", None)
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
    if ftype == "fillet" and "vertices" in fields:
        if p.get("fillet_type") == "variable":      # an empty number follows the default radius
            nums = list(fields.get("vertices_numbers") or [])
            nums += [None] * (len(fields["vertices"]) - len(nums))
            p["vertex_radii"] = [{"vertex": r, "radius": p.get("radius", 1.0) if n is None else n}
                                 for (k, r), n in zip(fields["vertices"], nums)]
        else:                                       # a constant fillet saves what it always did
            p.pop("vertex_radii", None)
            if p.get("fillet_type") == "constant":
                p.pop("fillet_type")
    if ftype in ("shell", "thread", "delete_face", "move_face", "offset_surface"):
        p["faces"] = [r for k, r in fields.get("faces", [])]
    if ftype == "thicken":
        p["faces"] = [r for k, r in fields.get("faces", []) if k == "face"]
        p["parts"] = [r for k, r in fields.get("faces", []) if k == "part"]
        p["scope"] = [r for k, r in fields.get("scope", [])]
    if ftype == "fill":                 # the continuity of an edge that stays in the list is kept
        old = list(p.get("edges") or [])
        out = []
        for k, r in fields.get("edges", []):
            prev = next((e for e in old if _same(_fill_item(e), (k, r))), None)
            if prev is not None:
                old.remove(prev)
            out.append(_fill_edge(k, r, prev))
        p["edges"] = out
        p["guides"] = [r for k, r in fields.get("guides", [])]
        p["scope"] = [r for k, r in fields.get("scope", [])]
    if ftype == "enclose":
        p["entities"] = [{"kind": k, "ref": r} for k, r in fields.get("entities", [])]
        p["scope"] = [r for k, r in fields.get("scope", [])]
    if ftype == "plane":
        p["refs"] = [{"k": k, "ref": r} for k, r in fields.get("refs_sel", [])]
        p.pop("ref", None)
    if ftype == "axis":
        p["refs"] = [{"k": k, "ref": r} for k, r in fields.get("refs_sel", [])]
        if not any(k == "face" for k, _ in fields.get("refs_sel", [])):
            p["click"] = None                # the click belongs to a picked face
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
        sel2 = fields.get("dir2_sel", [])
        if sel2:
            p["dir2"] = sel2[0][1]
            p["dir2_std"] = ""
        else:
            p["dir2"] = {"kind": "std", "name": p.get("dir2_std") or "Y"}
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


# ---------------------------------------------------------------------- the schema as plain data

STANDARD_KINDS = ("num", "choice", "check", "sel", "ticks")   # the rows a generic client builds from the schema alone
SIMPLE_KINDS = ("ops", "info", "text")                  # also plain: segmented buttons, a read-only line, a text box


def _plain(v):
    """`v` as JSON-able data: tuples and sets become lists (sets sorted), nothing else changes."""
    if isinstance(v, (set, frozenset)):
        return sorted(_plain(x) for x in v)
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    return v


def _schema_for(ftype, params):
    if ftype.startswith("plugin:"):
        from . import plugins
        return plugins.dialog_schema(ftype)
    key = F.front_kind({"type": ftype, "params": params}) or ftype
    return SCHEMAS.get(key, [])


def _call(fn, p, default):
    try:
        return fn(p)
    except Exception:
        return default


def _describe_field(spec, p):
    kind = spec["t"]
    vis = spec.get("visible")
    out = {"key": spec.get("key"), "kind": kind, "standard": kind in STANDARD_KINDS or kind in SIMPLE_KINDS,
           "visible": True if vis is None else bool(_call(vis, p, True))}
    if "label" in spec:
        out["label"] = spec["label"]
    if kind == "num":
        out.update(unit=spec.get("unit"), min=spec.get("min"), signed=bool(spec.get("signed")))
    elif kind in ("choice", "ops"):
        out["options"] = _plain(spec.get("options") or [])
        if "default" in spec:
            out["default"] = spec["default"]
    elif kind == "sel":
        out.update(want=sorted(spec.get("want") or ()), multi=bool(spec.get("multi", True)),
                   number=bool(spec.get("number")), list=bool(spec.get("list")), tip=spec.get("tip"))
    elif kind == "info":
        text = spec.get("text")
        out["text"] = _call(text, p, "") if callable(text) else (text or "")
    elif kind == "text":
        out.update(placeholder=spec.get("placeholder", ""), expr=bool(spec.get("expr")))
    return out


def describe(ftype, params, state=None):
    """The dialog of a feature type as plain JSON-able data, for the parameters `params`.

    {"type": ftype,
     "fields": [{"key", "kind", "standard", "visible", ...}, ...],     # in dialog order
     "custom": [key, ...]}                                             # keys of the hand-built rows, [] if none
    kind is "num" (unit, min, signed), "choice" and "ops" (options: [[value, label], ...]), "check", "sel"
    (want: sorted kinds, the strings of freeshape.picking.WANTED; multi, number, list, tip), "info" (text, evaluated
    for `params`), "text" (placeholder, expr) or, for a hand-built row, its own name (derived_source, split_tools,
    ...). `standard` is true for every kind named above; false for the hand-built ones, which carry only key, kind,
    standard, visible and label. `visible` is the row's visible rule evaluated for `params`. `state` is the
    ModelState the feature is built on; no rule needs it yet."""
    p = dict(params or {})
    fields = [_describe_field(s, p) for s in _schema_for(ftype, p)]
    return {"type": ftype, "fields": fields, "custom": [f["key"] for f in fields if not f["standard"]]}


def custom_feature_types():
    """The feature types in SCHEMAS with at least one hand-built row (a row the schema cannot describe fully), less
    the SERVED_TYPES: their server dialog describes every row with the standard kinds (derived_describe)."""
    return sorted(t for t, rows in SCHEMAS.items()
                  if t not in SERVED_TYPES and any(r["t"] not in STANDARD_KINDS and r["t"] not in SIMPLE_KINDS
                                                   for r in rows))


def startable_feature_types():
    """The feature types a client can start a dialog for (I6.1): those with a schema that also make default
    parameters and have a label (SCHEMAS holds a stub or two that are not features yet), sorted. Python-feature and
    plugin types are never listed: the desktop adds them to SCHEMAS and FEATURE_LABELS when it loads them, so the
    answer would depend on what the process has imported. They are told apart by their type string."""
    from .plugins import is_plugin_type
    from .pyfeature.registry import is_python_type
    return sorted(t for t in SCHEMAS if t in F.FEATURE_LABELS and not is_python_type(t) and not is_plugin_type(t))


# ---------------------------------------------------------------------- Derived in the Browser client (I10.2)
# The desktop draws SCHEMAS["derived"] with its own widgets (freeshape/ui/dialogs.py `_make_derived_*`). The server's
# dialog describes the same rows with standard kinds, filled from the Document the dialog is on (a DerivedContext the
# session makes), and changes the params with the desktop's rules, in the desktop's order of keys, so a Derived made
# in the browser saves the bytes the desktop saves for the same choices. Only a source in this Document: another
# Document and a Version pin stay on the desktop.

SERVED_TYPES = ("derived",)         # hand-built on the desktop, all standard rows in the server's dialog

DESKTOP_ONLY = "Desktop only for now"
# the desktop's notes, word for word (freeshape/ui/dialogs.py)
DERIVED_NO_SOURCE = "This Document has no other Part Studio to derive from."
DERIVED_PART_FIRST = "⚠ Tick part {part} first: a connector comes in with its part."
DERIVED_UNTICKED = "⚠ Connectors on a part you unticked were unticked too."
DERIVED_SCOPED = ("⚠ This Derived reads another Document or a Version of this one: change that on the desktop. "
                  "Choose a Part Studio here to read this Document instead.")
DERIVED_TICKS = ("parts", "sketches", "connectors")    # the params the source owns, cleared when it changes
DERIVED_GROUP = "derived_tabs"                          # the tick lists are the tabs of one row, as on the desktop


class DerivedContext(NamedTuple):
    studios: list           # [(tab id, name)]: the other Part Studio tabs of this Document, in manifest order
    package: object         # what the source publishes (ProcessEngine.published: parts, sketches, connectors,
    #                         aliases, error), or None (no source, another Document, or not open)
    why: str                # why there is no package, "" when there is or no source is chosen
    locations: list         # [(connector id, name)]: this Part Studio's Mate connectors above the Derived


def _scoped(src):
    return bool(src.get("document") or src.get("version"))


def _published(ctx, key):
    """{id: label} of what the source publishes under `key` (parts, sketches, connectors), in its order."""
    pkg = ctx.package or {}
    if key == "connectors":
        names = {p["id"]: p["name"] for p in pkg.get("parts") or ()}
        return {c["id"]: f"{c['name']}  ({names.get(c['part'], c['part'])})" for c in pkg.get("connectors") or ()}
    return {x["id"]: x["name"] for x in pkg.get(key) or ()}


def _tick_options(ctx, key, ticked):
    """The rows of a tick list: what the source publishes, then each ticked id it no longer does (the desktop's)."""
    pub = _published(ctx, key)
    return [[i, label] for i, label in pub.items()] + [[i, f"{i}  (no longer published)"] for i in ticked
                                                       if i not in pub]


def _alias(ctx, pid):
    return ((ctx.package or {}).get("aliases") or {}).get(pid, pid)


def _connector_owner(ctx):
    """{connector id: the source part it is on, through the source's aliases} (the desktop's `owner`)."""
    return {c["id"]: _alias(ctx, c["part"]) for c in (ctx.package or {}).get("connectors") or ()}


def _part_ticked(ctx, params, cid, owner):
    return owner.get(cid) is None or owner[cid] in [_alias(ctx, i) for i in params.get("parts") or []]


def derived_note(params, ctx):
    """The note under the Part Studio chooser when nothing was just refused or unticked (the desktop's fill)."""
    src = params.get("source") or {}
    if _scoped(src):
        return DERIVED_SCOPED
    err = ctx.why or (ctx.package or {}).get("error") or ""
    if err:
        return "⚠ " + err
    return "" if ctx.studios else DERIVED_NO_SOURCE


def derived_describe(params, ctx, note=None):
    """The Derived dialog as describe() gives a dialog, every row a standard kind:
        document    choice: This Document; Another Document and A Version of this Document are listed with
                    `disabled` {value: "Desktop only for now"}
        source      choice of the other Part Studio tabs ("" none), `picker` "studio": a client may draw it as its
                    Part Studio list
        _note       info: the desktop's note under the chooser (`note` when given, else derived_note)
        parts, sketches, connectors
                    ticks: `options` [[id, label], ...] of what the source publishes, `group` "derived_tabs" (the
                    three are the tabs of one row)
        location    choice: "" (not placed) or one of this Part Studio's Mate connectors above the Derived
        base        choice: "origin" or one of the ticked connectors; shown once a Location is chosen"""
    p = params or {}
    src = p.get("source") or {}
    place = p.get("placement") or {}

    def row(key, kind, label, **more):
        return {"key": key, "kind": kind, "standard": True, "visible": True, "label": label, **more}
    tab = src.get("tab")
    studios = [[t, n] for t, n in ctx.studios]
    if tab and (_scoped(src) or tab not in dict(ctx.studios)):
        suffix = DESKTOP_ONLY if _scoped(src) else "not in this Document"
        studios.append([tab, f"{src.get('studio_name') or tab} ({suffix})"])
    locs = [[c, n] for c, n in ctx.locations]
    want = place.get("location")
    if want and want not in dict(ctx.locations):
        locs.append([want, f"{want}  (not defined above this feature)"])
    ticked_c = list(p.get("connectors") or [])
    conns = dict(_tick_options(ctx, "connectors", ticked_c))
    fields = [
        row("document", "choice", "Document",
            options=[["this", "This Document"], ["other", "Another Document"], ["version", "A Version of this Document"]],
            disabled={"other": DESKTOP_ONLY, "version": DESKTOP_ONLY}),
        row("source", "choice", "Part Studio", options=[["", "— choose a Part Studio —"], *studios], picker="studio",
            disabled={tab: DESKTOP_ONLY} if tab and _scoped(src) else {}),
        {"key": "_note", "kind": "info", "standard": True, "visible": True,
         "text": derived_note(p, ctx) if note is None else note},
        row("parts", "ticks", "Parts", options=_tick_options(ctx, "parts", p.get("parts") or []), group=DERIVED_GROUP),
        row("sketches", "ticks", "Sketches", options=_tick_options(ctx, "sketches", p.get("sketches") or []),
            group=DERIVED_GROUP),
        row("connectors", "ticks", "Mate connectors", options=_tick_options(ctx, "connectors", ticked_c),
            group=DERIVED_GROUP),
        row("location", "choice", "Location", options=[["", "— not placed —"], *locs]),
        row("base", "choice", "Placement base", options=[["origin", "Base origin"], *[[c, conns[c]] for c in ticked_c]]),
    ]
    fields[-2]["visible"] = len(locs) > 0           # nothing to place on: no row (the desktop's combo is then empty)
    fields[-1]["visible"] = bool(want)
    return {"type": "derived", "fields": fields, "custom": []}


def derived_values(params, ctx):
    """{key: {"value"}} of the Derived dialog's rows (what derived_describe's choices and tick lists show)."""
    p = params or {}
    src = p.get("source") or {}
    place = p.get("placement") or {}
    doc = "other" if src.get("document") else "version" if src.get("version") else "this"
    base = place.get("base") or "origin"
    return {"document": {"value": doc}, "source": {"value": src.get("tab") or ""},
            **{k: {"value": list(p.get(k) or [])} for k in DERIVED_TICKS},
            "location": {"value": place.get("location") or ""},
            "base": {"value": base if base == "origin" or base in (p.get("connectors") or []) else "origin"}}


class DerivedSet(NamedTuple):
    error: str              # why the change was refused, in part or whole ("" when it was not)
    note: object            # the note to show now (None: the standing note, derived_note)
    reread: bool            # the source changed: the session must read its package again


def _write_placement(p, ctx, location=None, base=None):
    """The desktop's `_derived_write_placement`, run after every change as its Placement base list refills: no
    Location drops `placement`; else {"location", "base"}, a base that is not a ticked connector being Base origin."""
    place = p.get("placement") or {}
    loc = place.get("location") if location is None else location
    if not loc:
        p.pop("placement", None)
        return
    want = (place.get("base") or "origin") if base is None else base
    p["placement"] = {"location": loc, "base": want if want in (p.get("connectors") or []) else "origin"}


def derived_set(key, value, params, ctx):
    """Apply one change of the Derived dialog to `params` (changed in place, keys in the desktop's order), with the
    desktop's rules: another source clears the tick lists; a connector whose part is not ticked is refused; unticking
    a part unticks its connectors; the Placement base falls back to Base origin when its connector is unticked. A
    tick list's value is the ids ticked, stored in the list's order whatever order they come in. `value` must be
    one the description offers (FeatureDialog.check). Returns DerivedSet."""
    p = params
    src = p.get("source") or {}
    if key == "document":
        if value != "this":
            return DerivedSet(DESKTOP_ONLY, None, False)
        if not _scoped(src):
            return DerivedSet("", None, False)
        for k in DERIVED_TICKS:               # another scope: no source, parts or sketches carry over
            p[k] = []
        p["source"] = {}
        _write_placement(p, ctx)
        return DerivedSet("", None, True)
    if key == "source":
        if value and value == src.get("tab") and not _scoped(src):
            return DerivedSet("", None, False)
        names = dict(ctx.studios)
        if value and value not in names:
            return DerivedSet(DESKTOP_ONLY if _scoped(src) else "That Part Studio is not in this Document", None,
                              False)
        for k in DERIVED_TICKS:               # another source: its own parts and sketches
            p[k] = []
        from . import links as L
        p["source"] = L.studio_source(SimpleNamespace(id=value, name=names[value])) if value else {}
        _write_placement(p, ctx)
        return DerivedSet("", None, True)
    if key in DERIVED_TICKS:
        order = [i for i, _label in _tick_options(ctx, key, p.get(key) or [])]
        want = set(value)
        error, note = "", None
        if key == "connectors":
            owner = _connector_owner(ctx)
            had = set(p.get(key) or [])
            refused = [c for c in order if c in want and c not in had and not _part_ticked(ctx, p, c, owner)]
            if refused:
                error = DERIVED_PART_FIRST.format(part=owner[refused[0]])
                want -= set(refused)
        p[key] = [i for i in order if i in want]
        if key == "parts":
            owner = _connector_owner(ctx)
            kept = [c for c in p.get("connectors") or [] if _part_ticked(ctx, p, c, owner)]
            if kept != list(p.get("connectors") or []):
                p["connectors"] = kept
                note = DERIVED_UNTICKED
        _write_placement(p, ctx)
        return DerivedSet(error, note, False)
    if key == "location":
        if not value:
            p.pop("placement", None)
        else:
            _write_placement(p, ctx, location=value)
        return DerivedSet("", None, False)
    if key == "base":
        _write_placement(p, ctx, base=value)
        return DerivedSet("", None, False)
    raise KeyError(key)


# ---------------------------------------------------------------------- behaviour (I5.2)
# What a dialog does with the picks it was started with and when a field changes. FeatureDialog calls these and
# keeps only its widgets; the Browser client's server session calls the same functions.

def _sel_specs(ftype, params):
    """The selection rows of the dialog for `params`, in dialog order."""
    return [s for s in _schema_for(ftype, params or {}) if s["t"] == "sel"]


def _pick_parts(pk):
    """(kind, key) of a pick: a viewport Pick (`.kind`, `.key`) or a plain (kind, key) pair."""
    return (pk.kind, pk.key) if hasattr(pk, "kind") else (pk[0], pk[1])


def _toggle(items, item, multi):
    """`items` after a click on `item`: an item already listed goes, otherwise it is added (it replaces the list
    in a single-pick field). A face's "edges" item toggles each of its edges."""
    items = list(items)
    if item[0] == "edges":
        for r in item[1]:
            items = _toggle(items, ("edge", r), multi)
        return items
    for i, ex in enumerate(items):
        if _same(ex, item):
            items.pop(i)
            return items
    if not multi:
        items = []
    items.append(item)
    return items


def connector_stamp(state, item, click=None, snap=None, part_shapes=None, pick_part=None):
    """The params that make a Mate connector's frame rebuildable from `item` (a face or edge item): click, snap and
    the frame's X reference. `state` needs .parts ({id: part with .shape}), .named(pid) and .engine_names;
    `part_shapes` ({id: shape}, default the parts' own) is where the picked element lives and `pick_part` its part
    id: the viewport shows a part's shape that need not be the object `state` holds. If the frame cannot be built,
    all three are None (the preview shows why; the picked reference stays)."""
    kind, ref = item
    try:
        pid, sub = F.resolve(state, ref, kind)
        shape = state.parts[pid].shape
        subs = K.faces(shape) if kind == "face" else K.edges(shape)
        index = next(i for i, s in enumerate(subs) if s.IsSame(sub))
        named = state.named(pid)
        st = None
        if snap is not None:
            vshape = (part_shapes or {}).get(pick_part, shape)
            edge_ref = None
            if snap.get("edge") is not None:
                edge_ref = AS._ref_signature(vshape, "edge", snap["edge"], named)
                nm = ((getattr(state, "engine_names", None) or {}).get(pick_part) or {}) \
                    .get("edge", {}).get(snap["edge"] + 1)
                if nm:
                    edge_ref["name"] = nm
            st = {"type": snap["type"], "p": [float(a) for a in snap["p"]], "edge_ref": edge_ref}
            fr = AS.snap_frame(shape, kind, index, st, named)
        else:
            fr = AS.connector_frame(shape, kind, index, click)
        return {"click": [float(a) for a in click] if click is not None else None, "snap": st,
                "x_ref": AS.connector_x_ref(fr)}
    except Exception:
        return {"click": None, "snap": None, "x_ref": None}


class Preselected(NamedTuple):
    fields: dict        # {field key: [item, ...]}: `fields` with the picks added
    params: dict        # params to set besides the fields: "sketch" (a picked sketch), a Mate connector's stamp


def apply_preselection(ftype, fields, picks, state, params=None):
    """Seed a new feature's selection fields from the picks its tool was started with.

    `fields`: {key: [item, ...]} as params_to_fields gives them (not changed). `picks`: viewport Picks or
    (kind, key) pairs, in selection order; keys are the viewport's: (part id, 0-based face / edge index in
    kernel.faces / kernel.edges order), (sketch id, region index), ... `params` is the feature's params (it
    chooses the front-door schema; default {}).

    `state` carries what the picks refer to: `part_shapes` {part id: shape} and `sketches` {sketch id:
    SketchResult} (what picking.make_item reads), `named(pid)` and `engine_names` (as for make_item; engine_names
    may be missing or None), and for a Mate connector also `parts` ({id: part with .shape}). It is the viewport's
    contents on the desktop and the worker's document state on the server.

    Each pick goes to the first field that still takes it and wants its kind (a full single-pick field and a
    field with a number box take none). A pick on a sketch's region or curve also chooses the sketch of a
    feature with a "sketch_choice" row, if that sketch has a point or a circle. Returns Preselected(fields, params)."""
    picks = list(picks)
    out = {k: list(v) for k, v in fields.items()}
    extra: dict = {}
    schema = _schema_for(ftype, params or {})
    for row in schema:
        if row["t"] != "sketch_choice":
            continue
        for pk in picks:
            kind, key = _pick_parts(pk)
            sr = state.sketches.get(key[0]) if kind in ("region", "sketch_line") else None
            if sr is not None and any(e["type"] in ("point", "circle") for e in sr.sketch.entities.values()):
                extra[row["key"]] = key[0]
                break
        break
    named = state.named if callable(getattr(state, "named", None)) else None
    used = set()
    for spec in (s for s in schema if s["t"] == "sel"):
        k = spec["key"]
        if k not in out:
            continue
        for i, pk in enumerate(picks):
            if i in used or (not spec.get("multi", True) and out[k]) or spec.get("number"):
                continue
            kind, key = _pick_parts(pk)
            item = make_item(state.part_shapes, state.sketches, kind, key, set(spec["want"]), named,
                             getattr(state, "engine_names", None))
            if item is None:
                continue
            used.add(i)
            out[k] = _toggle(out[k], item, spec.get("multi", True))
            if ftype == "mate_connector":
                extra.update(connector_stamp(state, item, None, None, state.part_shapes, key[0]))
    return Preselected(out, extra)


class FieldSet(NamedTuple):
    fields: dict            # the fields after the change
    active: object          # key of the field to make the one clicks go to, or None to leave it
    params: dict            # the params after the change, side effects included


_UPTO_FIELD = {"up_to_face": "upto_face", "up_to_vertex": "upto_vertex", "up_to_part": "upto_part"}
_STD_AXIS_FIELD = {"axis_std": "axis_sel", "dir_std": "dir_sel", "dir2_std": "dir2_sel"}


def on_field_set(ftype, key, value, fields, params=None):
    """What setting the param `key` to `value` does besides storing it. `fields`: {key: [item, ...]} (plus
    {key_numbers: numbers} for a field with a number box), as the dialog holds them; `params`: the feature's
    params before the change (not changed). Returns FieldSet(fields, active, params):

    - a world axis (axis_std, dir_std, dir2_std) empties the picked-axis field it replaces;
    - Surface drops an Extrude / Revolve / Sweep / Loft operation that surfaces lack (Remove, Intersect: Add);
      Thin seeds its wall thicknesses and the draft switch seeds the draft angle (not in default_params);
    - the field returned as `active` is the one the next click should fill: an "Up to ..." end's pick field, the
      features or parts field of a Mirror / pattern, the curves of a Thin or surface Loft, a Sweep's lock
      direction, a Loft's guides, a variable Fillet's vertices; None when nothing changes the active field;
    - the params are the fields' params again (fields_to_params), which is why they are returned."""
    p = dict(params or {})
    out = {k: list(v) for k, v in fields.items()}
    active = None
    p[key] = value
    if key == "type" and ftype in ("mirror", "linear_pattern", "circular_pattern"):
        fk = "features" if value == "features" else "parts"
        if fk in out:
            active = fk
    if key == "end" and ftype in ("extrude", "revolve"):
        fk = _UPTO_FIELD.get(value)
        if fk in out:
            active = fk
    if key == "creation" and ftype in ("extrude", "revolve", "sweep", "loft") and value == "surface" \
            and p.get("op", "add") not in ("new", "add"):
        p["op"] = "add"
    if key == "creation" and ftype == "loft" and value == "surface" and "curves" in out:
        active = "curves"
    if key == "creation" and ftype in ("extrude", "revolve", "sweep") and value == "thin":
        for k, v in (("thickness1", 1.0), ("thickness2", 0.0)):
            p.setdefault(k, v)
        if "curves" in out:
            active = "curves"
    if key == "control" and ftype == "sweep" and value == "lock" and "lock_dir" in out:
        active = "lock_dir"
    if key == "draft" and value and ftype == "extrude" and not p.get("draft_angle"):
        p["draft_angle"] = 5.0
    if key in _STD_AXIS_FIELD and value and _STD_AXIS_FIELD[key] in out:
        out[_STD_AXIS_FIELD[key]] = []
    fields_to_params(ftype, out, p)
    if key == "guides_on" and value and "guides" in out:
        active = "guides"
    if key == "fillet_type" and value == "variable" and "vertices" in out:
        active = "vertices"
    return FieldSet(out, active, p)


# =========================================================================== drag arrows (I6.3)

def _vec(v):
    return [float(x) for x in np.asarray(v, float).reshape(-1)]


def manipulators(ftype, params, base_state):
    """The drag arrows of a feature dialog as plain data, one dict each (the desktop's `FeatureDialog.manipulators`
    wraps this and adds its closures; the engine preview sends it to the Browser client):
        field      the param the arrow drives ("depth", "offset")
        flip       the param that carries the sign of a drag past the origin, or None ("flip")
        origin     [x, y, z] where the arrow starts
        dir        [x, y, z] unit vector the value is measured along
        value      the current signed distance along `dir`
        symmetric  True when `value` is half of the field (Extrude's Symmetric end)
        snap       "view": snap to a step the viewer picks from its zoom
        min        the smallest absolute value a drag may give, or None
        label      the value as the desktop prints it, with its unit
    `params` may hold expressions; they are evaluated against `base_state` (the model before the feature).
    Extrude and an offset Plane have an arrow, every other type has none (Split's keep-side arrows stay
    desktop-only). Never raises for a feature that cannot be drawn: it gives []. Qt-free."""
    if ftype not in ("extrude", "plane"):
        return []
    fid = getattr(base_state, "current_fid", None)       # prepare() records the feature it is about to run
    try:
        p = F.prepare({"id": fid, "type": ftype, "params": dict(params or {})}, base_state)["params"]
    except K.KernelError:
        return []
    try:
        if ftype == "extrude":
            if p.get("end") == "through":
                return []
            profs = F._profiles(base_state, p)
            if not profs:
                return []
            n = np.asarray(F._profile_normal(base_state, p, profs), float)
            if p.get("op") in ("remove", "intersect") and not p.get("regions"):
                n = -n                   # cuts from a face go into the part
            infos = [K.face_info(f) for f in profs]
            area = sum(i["area"] for i in infos) or 1.0
            origin = sum(np.array(i["c"]) * i["area"] for i in infos) / area
            symmetric = p.get("end") == "symmetric"
            depth, flip = F.signed_extent(p.get("depth", 10), p.get("flip"))
            value = depth / 2 if symmetric else (-depth if flip else depth)
            return [{"field": "depth", "flip": "flip", "origin": _vec(origin), "dir": _vec(n), "value": float(value),
                     "symmetric": symmetric, "snap": "view", "min": 1e-3 if symmetric else None,
                     "label": fmt(depth) + " mm"}]
        if F.plane_type(p) != "offset":
            return []
        planes_ = [(k, r) for k, r in F.plane_items(p) if F.plane_role(k, r) == "plane"]
        if planes_:
            k_, r_ = planes_[0]
            base = F.resolve_plane(base_state, r_ if k_ == "plane" else {"kind": "face", "face": r_})
        else:
            base = K.std_plane("Top")
        off = float(p.get("offset", 0))
        return [{"field": "offset", "flip": "flip", "origin": _vec(base.origin), "dir": _vec(base.normal),
                 "value": -off if p.get("flip") else off, "symmetric": False, "snap": "view", "min": None,
                 "label": fmt(off) + " mm"}]
    except (K.KernelError, KeyError, IndexError, ValueError):
        return []
