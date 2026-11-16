"""The engine contract: what goes into a tab (`submit_edit`) and what comes out (`outputs`). Headless, and
free of OCCT: every output object below holds only str / int / float / bool / None / bytes, numpy arrays of
numbers, and lists / tuples / dicts of those, so it pickles and can cross a process (ADR 0002, I3.2) or a
machine boundary unchanged.

Outputs
-------
A Variable Studio tab ("variables") returns `VariablesOutputs`; a Part Studio holding a "variable_studio_ref"
feature reads its variables, live and unsaved, and lists that tab in `consumes`.

A part crosses as `PartOut`: its BREP (`brep`, zlib-compressed OCCT BREP text -- the same encoding as
`kernel.shape_to_brep`, so `kernel.shape_from_brep(part.brep)` rebuilds it), a content hash of that BREP
text, a mesh for display, and its element-name map.

Element indices. Everything that names a face, edge or vertex by number uses one rule: the 1-based position
of the sub-shape in `TopExp.MapShapes` order for that kind -- the order `kernel.faces` / `kernel.edges`
return (shifted by one) and the rule behind FreeCAD's "Face1" / "Edge1" names
(Part::TopoShape::getSubShape). `PartOut.names` is `{"face": {i: name}, "edge": {i: name},
"vertex": {i: name}}`; `MeshOut.tri_face[t]` is the face index of triangle t; `MeshOut.edge_polys[i - 1]`
is edge i; `PartOut.face_colors` and `ConnectorOut.index` use the same 1-based index. A consumer rebuilds
the shape from `brep`, indexes its sub-shapes the same way, and looks names up by index.

Edits
-----
An edit is a plain dict with an "op" key; see `STUDIO_OPS`, `ASSEMBLY_OPS`, `DRAWING_OPS` for what each
tab kind accepts and the keys each op reads. An unknown op, or one naming something that is not there,
raises `EngineError`; a feature that fails to rebuild is not an exception -- it shows up in `errors`, the
same as in the Part Studio itself.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field

import numpy as np


class EngineError(Exception):
    """An edit the engine cannot apply, or a tab it does not have."""


class TabErrored(EngineError):
    """The tab's worker process died (the process executor). The tab stays open, holding every edit it had,
    until `restart(tab)` rebuilds it or `close_tab(tab)` drops it."""

    def __init__(self, tab, reason):
        super().__init__(f"tab {tab!r} stopped: {reason}")
        self.tab = tab
        self.reason = reason


class BadPick(EngineError):
    """A pick `refs` cannot read: a field of the wrong type or range, a part or sketch the tab does not have, an
    index past the end. The message names the field ("picks[2].index: ..."); the tab is untouched."""


class LinkCycleError(EngineError):
    """Tabs that consume each other, directly or through a chain. `tabs` lists the cycle, first tab repeated last.
    Raised by `submit_edit` after the edit itself has been applied; nothing is rebuilt for the cycle."""

    def __init__(self, tabs, message):
        super().__init__(message)
        self.tabs = list(tabs)


# =========================================================================== edits

STUDIO_OPS = {
    "set_param": "feature, key (a params key or a list of keys into nested params), value",
    "replace_feature": "feature (the whole feature dict, matched by its id)",
    "add_feature": "feature (a whole feature dict), index (optional; default: at the rollback bar)",
    "delete_features": "ids",
    "set_suppressed": "feature, value",
    "set_rollback": "index (None = all features)",
    "rename_feature": "feature, name",
    "set_part_override": "part, plus any of name / color (None puts it back) / hidden (the eye: a view setting, no "
                         "undo step); or colors ({part: color}) for several parts in one undo step",
    "set_features_hidden": "ids (sketch / plane / axis feature ids, or a standard plane's name), value (true hides, "
                           "false shows: the eye, a view setting stored in the Document's hidden_features; no undo step)",
    "make_folder": "ids (a run of features), name (optional), folder (optional id to give it)",
    "ungroup_folder": "folder",
    "remove_from_folder": "ids",
    "rename_folder": "folder, name",
    "set_folder_open": "folder, value (a view setting: no undo step)",
    "set_folder_suppressed": "folder, value",
    "set_configs": "rows ([{name, vars: {variable name: expression}, suppress: [feature id]}]), active (index, "
                   "optional, default 0): the configuration table, replaced in one undo step",
    "set_active_config": "config (a row's name or index)",
    "set_stored_build": "feature (id), build (its `python_builds` entry, naming blobs by key; None drops it): "
                        "the stored build the feature shows where it cannot build. No undo step, no rebuild",
    "undo": "", "redo": "",
    "save": "path (optional)",
}
ASSEMBLY_OPS = {
    "move_instance": "instance, T (4x4), solve (optional, default True)",
    "set_hidden": "instance, value",
    "set_fixed": "instance, value",
    "set_suppressed": "mate, value",
    "rename": "id (instance or mate), name",
    "delete_instances": "ids",
    "delete_mates": "ids",
    "solve": "",
    "refresh": "",
    "move_begin": "instance: start a drag; remembers every placement and opens one undo step",
    "move_to": "T (4x4 world motion applied to the placements at move_begin; latest wins), then solve",
    "move_end": "close the drag (nothing moved: the undo step is dropped)",
    "add_mate": "mate (a whole mate dict, as a mate dialog's result gives it; its id must be free): one undo step",
    "replace_mate": "mate (a whole mate dict, matched by its id): one undo step",
    "undo": "", "redo": "",
    "save": "path (optional)",
}
DRAWING_OPS = {
    "set_source": "source (a drawing source dict: kind, uid, path, part ...)",
    "auto_layout": "",
    "refresh": "",
    "undo": "", "redo": "",
    "save": "path (optional)",
}
VARIABLES_OPS = {
    "add_row": "name, expression, index (optional; default: at the end)",
    "set_row": "index, plus name and / or expression",
    "remove_row": "index",
    "save": "path (optional)",
}


# =========================================================================== outputs

@dataclass
class MeshOut:
    """A display mesh. verts / normals (n, 3) float32, tris (m, 3) uint32 into verts, tri_face (m,) int32
    1-based face index, edge_polys one (k, 3) float32 polyline per edge (edge i at [i - 1]), vertices
    (v, 3) float32 in vertex-index order."""
    verts: np.ndarray
    normals: np.ndarray
    tris: np.ndarray
    tri_face: np.ndarray
    edge_polys: list
    vertices: np.ndarray
    # face_ranges[i - 1] = (start, end): the triangles of face i are tris[start:end] (tri_face == i there)
    face_ranges: list = field(default_factory=list)


@dataclass
class PartOut:
    id: str
    name: str
    color: tuple
    hidden: bool
    brep: bytes              # zlib-compressed BREP text, no triangulation (kernel.shape_from_brep reads it)
    hash: str                # sha256 hex of the uncompressed BREP text
    names: dict              # {"face": {i: name}, "edge": {...}, "vertex": {...}}, 1-based i; empty if unnamed
    mesh: MeshOut | None
    face_colors: dict | None = None   # 1-based face index -> (r, g, b)


@dataclass
class SketchOut:
    id: str
    name: str
    plane: dict              # {"origin", "xdir", "normal"}: 3-lists
    sketch: dict             # the solved sketch (Sketch.to_dict)
    regions: list            # per closed region: {"c", "area", "loops", "edges"}
    names: dict = field(default_factory=dict)         # {"face": {i: name}, "edge": {...}, "vertex": {...}}, 1-based
    #                                                   i into the sketch's region compound (SketchResult.named)
    region_names: list = field(default_factory=list)  # regions[i]'s element name, None if it has none
    hash: str = ""           # sha256 hex over plane, sketch, regions and names: changes when any of them does


@dataclass
class MateConnectorOut:
    """A Mate connector a Part Studio publishes (a mate_connector feature, I3.2)."""
    id: str                  # the mate_connector feature's id
    name: str
    part: str                # id of the part that owns it
    frame: dict              # {"o", "z", "x"}: 3-lists, in the studio's coordinates
    hash: str = ""           # sha256 hex over name, part and frame: changes when the connector moves
    sketch: str = ""         # id of the sketch feature that owns it ("" for a part-owned connector; then part is "")


@dataclass
class ConnectorOut:
    """One side of a mate: a face or edge of an instance's part, and the frame on it."""
    instance: str
    kind: str                # "face" | "edge"
    index: int | None        # 1-based element index in the instance's part
    frame: dict              # {"o", "x", "z", "what"} in the part's own coordinates
    world: dict              # the same frame placed by the instance's transform


@dataclass
class MateOut:
    id: str
    name: str
    type: str
    suppressed: bool
    a: ConnectorOut
    b: ConnectorOut
    params: dict             # flip, offset, angle ... (whatever else the mate stores)


@dataclass
class InstanceOut:
    id: str
    name: str
    part: str | None         # key into AssemblyOutputs.parts (a content hash); None when it has no part
    T: list                  # 4x4 placement, nested lists of float
    hidden: bool
    fixed: bool
    color: tuple
    source: dict             # the link as stored (kind, uid, path, part ...)


@dataclass
class TabOutputs:
    tab: str
    kind: str
    name: str
    revision: int            # bumps on every edit applied to this tab
    errors: dict             # feature / instance / mate id (or "" for the tab itself) -> message
    consumes: tuple          # tab ids whose outputs this tab read, in first-use order


@dataclass
class FeatureOut:
    """One row of a Part Studio's feature list, as the desktop's feature tree shows it (I3.1). Presentation
    only: nothing here enters a Link content hash (those are computed from the document, linkgraph.py)."""
    id: str                  # the feature's id: stable across edits and revisions (undo brings the same id back)
    type: str                # the feature type ("extrude", "sketch", "plugin:<id>" ...): the client maps it to an icon
    label: str               # the type's display label ("Extrude"; a Boundary surface's own "Boundary surface")
    name: str                # the row's text: the feature's own name, editable by rename_feature
    suppressed: bool         # the feature's own flag; a feature in a suppressed folder is not marked here
    error: str | None = None     # the message the feature failed with (doc.errors), None when it built
    folder: str | None = None    # id of the folder the feature sits in (a key of StudioOutputs.folders), or None
    hidden: bool = False     # the eye: a sketch, plane or axis hidden in the viewport (doc.hidden_features)


@dataclass
class FolderOut:
    """A feature-list folder. It is drawn where its first member is; members are contiguous in `features`."""
    id: str
    name: str
    open: bool               # expanded in the list
    suppressed: bool         # every member is suppressed (the desktop draws the row struck out)
    failed: int = 0          # how many members carry an error


@dataclass
class CanvasOut:
    """An image a Canvas feature places on a plane or planar face (I12.1). Not a part: it has no shape and no
    element names. The image's bytes are the blob `blob` of the Part Studio; the client fetches them by that key."""
    id: str                  # the canvas feature's id
    name: str
    blob: str                # sha256 key of the image (PNG or JPEG); "" while it is held inline
    origin: list             # the image centre, a 3-list in the studio's coordinates
    xdir: list               # unit 3-list: the image's left-to-right direction (rotation and flip_h applied)
    ydir: list               # unit 3-list: the image's bottom-to-top direction (rotation and flip_v applied)
    normal: list             # unit 3-list: the plane's normal (flips do not change it)
    width: float             # mm, along xdir
    height: float            # mm, along ydir
    opacity: float = 50.0    # 0 (clear) to 100 (opaque)
    flip_h: bool = False
    flip_v: bool = False
    selectable: bool = True
    hidden: bool = False     # the eye in the feature list (doc.hidden_features)
    filename: str = ""
    pixels: list = field(default_factory=list)        # [width, height] of the image in pixels


@dataclass
class StudioOutputs(TabOutputs):
    parts: list = field(default_factory=list)          # [PartOut], in the studio's part order
    context: list = field(default_factory=list)        # [PartOut] of the Context bodies (reference parts), for the
    #                                                    window only; never in `parts`, so no consumer takes them as the studio's
    sketches: list = field(default_factory=list)       # [SketchOut]
    connectors: list = field(default_factory=list)     # [MateConnectorOut], in feature order
    variables: dict = field(default_factory=dict)      # name -> number
    # Construction geometry as the studio built it, each feature against the model as it was at that feature:
    # plane feature id -> {"o", "x", "n"} (kernel.Plane.to_dict), axis feature id -> {"o", "d", "span"}. A client
    # that rebuilt these itself from the finished parts would lose any whose face a later feature changed.
    planes: dict = field(default_factory=dict)
    axes: dict = field(default_factory=dict)
    error_refs: dict = field(default_factory=dict)     # failed feature id -> [reference dicts] to where it failed
    # The feature tree (I3.1). Absent in a payload recorded before it: from_wire then fills the defaults below.
    features: list = field(default_factory=list)       # [FeatureOut], in file order (the order they build in)
    folders: list = field(default_factory=list)        # [FolderOut], in the order of their first member
    rollback: int = 0        # the number of features that build: features[:rollback] are active, the rest rolled
    #                          back. len(features) when nothing is rolled back. set_rollback's `index` takes it.
    can_undo: bool = False
    can_redo: bool = False
    # The drag arrows of the dialog being previewed (I6.3), dialog_schema.manipulators' dicts: field, flip, origin,
    # dir, value, symmetric, snap, min, label. Only a preview carries them; the committed outputs have none.
    manipulators: list = field(default_factory=list)
    # The Canvas features' placed images (I12.1), [CanvasOut] in feature order. Absent in an older payload:
    # from_wire then gives [].
    canvases: list = field(default_factory=list)


@dataclass
class AssemblyOutputs(TabOutputs):
    parts: dict = field(default_factory=dict)          # hash -> PartOut, shared by instances of one part
    instances: list = field(default_factory=list)      # [InstanceOut]
    mates: list = field(default_factory=list)          # [MateOut] -- the mate connectors live here
    dof: int = 0


@dataclass
class VariablesOutputs(TabOutputs):
    """A Variable Studio tab. `errors` maps a row index (as a str) to its message."""
    variables: dict = field(default_factory=dict)      # name -> number, every row that evaluated
    rows: list = field(default_factory=list)           # [{"name", "expression"}], in evaluation order


@dataclass
class DrawingOutputs(TabOutputs):
    source: dict | None = None
    sheet: dict = field(default_factory=dict)
    title: dict = field(default_factory=dict)
    views: list = field(default_factory=list)          # the view dicts as stored
    annots: list = field(default_factory=list)
    curves: dict = field(default_factory=dict)         # view id -> {"visible", "hidden", "tangent"}
    model: str | None = None                           # content hash of the shape being drawn


# =========================================================================== plain data

_SCALARS = (str, int, float, bool, bytes, type(None))


def plain(x):
    """`x` as plain data: dicts / lists / tuples of scalars and numeric numpy arrays, numpy scalars turned
    into Python ones. Anything else (an OCCT object, a Sketch, a Plane ...) raises EngineError, so nothing
    the caller cannot pickle or read in another process leaks into an output by accident."""
    if isinstance(x, _SCALARS):
        return x
    if isinstance(x, np.generic):
        return x.item()
    if isinstance(x, np.ndarray):
        if x.dtype.kind not in "biuf":
            raise EngineError(f"not plain data: array of {x.dtype}")
        return x.copy()
    if isinstance(x, dict):
        return {plain(k): plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        out = [plain(v) for v in x]
        return out if isinstance(x, list) else tuple(out)
    if isinstance(x, (set, frozenset)):
        return sorted(plain(v) for v in x)
    raise EngineError(f"not plain data: {type(x).__module__}.{type(x).__qualname__}")


# =========================================================================== the interface

class Engine(abc.ABC):
    """`submit_edit(tab, edit)` -> `outputs(tab)`. Tabs are opened from bytes or a file (or made new) and addressed
    by a string tab id. The engine, not the tab, owns cross-tab inputs: a tab is handed the outputs it
    consumes and never opens another tab's file itself."""

    @abc.abstractmethod
    def open_tab(self, path, tab=None) -> str:
        """Open the Part Studio / Assembly / Drawing at `path`; returns its tab id (`tab` if given). A wrapper:
        it reads the file and calls `open_tab_data` with its bytes and `path`."""

    @abc.abstractmethod
    def open_tab_data(self, kind, data, tab=None, name=None, blobs=None, path=None) -> str:
        """Open a tab of `kind` from `data`, its bytes as a Document store holds them; returns its tab id (`tab`
        if given). `name` (if given) replaces the name the data carries. `blobs` is the tab's blob lookup (an
        object with `get`, `has` and `put`, see docstore.DocumentStore.blobs): every tab kind builds through it,
        and `tab_data` puts the blobs a save names back into it. `path` is the file the bytes came from, if
        any (what `open_tab` passes): it names the file in errors, relative link paths are found against its
        folder, and a Part Studio with a path reads its blobs beside it. A tab opened with no path is matched
        by tab id: a link source that names a tab id never falls back to a path or the filesystem."""

    @abc.abstractmethod
    def tab_data(self, tab, blobs=None) -> bytes:
        """The tab's data as a Document store holds it: the bytes a "save" edit writes, without writing any
        file. The blobs those bytes name (a Python feature's stored build, a Context capture) are put into
        `blobs`, else into the lookup the tab was opened with, else beside the tab's file; EngineError if
        there are blobs and none of those is there."""

    @abc.abstractmethod
    def new_tab(self, kind, name=None, tab=None, sources=(), blobs=None) -> str:
        """A new empty tab of `kind` ("studio" | "assembly" | "drawing"). `sources` are the link sources it
        already consumes (a reopened tab): the process executor records their edges at once, before any edit.
        `blobs` is the tab's blob lookup, as `open_tab_data` takes it: a Part Studio reads the blobs its edits
        name (a `set_stored_build`) through it."""

    @abc.abstractmethod
    def close_tab(self, tab) -> None:
        ...

    @abc.abstractmethod
    def tabs(self) -> list:
        """[(tab id, kind, name, path)] of every open tab, including ones opened to feed another."""

    @abc.abstractmethod
    def submit_edit(self, tab, edit) -> int:
        """Apply one edit; returns the tab's new revision."""

    @abc.abstractmethod
    def outputs(self, tab) -> TabOutputs:
        ...

    @abc.abstractmethod
    def refs(self, tab, picks, base=None) -> list:
        """Turn picks into stored references (I4.3). A QUERY: it reads the tab as last built and changes nothing --
        no edit, no revision, no undo step, no rebuild of this tab or of any tab that consumes it. A Part Studio
        only (EngineError for another kind).

        `base` (F3) picks against the model a feature dialog edits on instead of the last build: None (the last
        build), or {"feature": the id of the stored feature being edited (the model before it, as the desktop's
        dialog picks against), or None (a new feature: the model at the rollback bar, where it is added)}. The model
        is `base_outputs(tab, base["feature"])`; EngineError for an unknown feature or a malformed `base`.

        `picks` is a list of dicts, each {"part": part id (a sketch feature id for kinds "region", "sketch_line"
        and "sketch_point"), "kind": "face" | "edge" | "vertex" | "part" | "region" | "sketch_line" |
        "sketch_point", "index": the element's 1-based index (face, edge, vertex, region: the rule of "Element
        indices" above), "entity": a sketch entity id (sketch_line, sketch_point), "point": [x, y, z] (optional),
        "want": [kinds the selection field accepts, from freeshape.picking.WANTED]}. Raises BadPick for a pick that
        is malformed or names something the tab does not have.

        Returns one dict per pick, in order: {"ok": True, "kind": the item's kind, "ref": the reference as a
        feature's params store it, "name": its Element name or None} or {"ok": False, "code", "reason"}."""

    @abc.abstractmethod
    def feature(self, tab, feature) -> dict:
        """One feature of a Part Studio as stored (I5.3), for a dialog that edits it. A QUERY, like `refs`: nothing
        changes. Returns {"feature": a deep copy of the feature dict ("id", "type", "name", "params", ...),
        "variables": {name: number}, the variables defined before the feature, "defaults": the params a new feature of
        its type starts with (a dialog fills in the ones an older feature lacks)}. EngineError for an unknown feature or
        a tab that is not a Part Studio."""

    @abc.abstractmethod
    def start_feature(self, tab, type, picks) -> dict:
        """A new feature of `type` as a dialog starts it (I6.1). A QUERY: nothing changes, no id is taken. `picks` is
        a list of the refs route's picks (1-based indices; `want` may be left out), possibly empty: they seed the
        feature's selection fields with dialog_schema.apply_preselection, run on this tab's built state. Returns
        {"feature": {"type", "name" (the next free one), "suppressed": False, "params": the defaults, no "id"},
         "variables": {name: number} before the insert position, "defaults": the same params,
         "fields": {key: [[kind, ref], ...]} the selection rows after preselection, "params": params the picks set
         besides the fields ("sketch", a Mate connector's stamp)}. BadPick for a pick the tab cannot read;
        EngineError for a type with no dialog or a tab that is not a Part Studio."""

    @abc.abstractmethod
    def startable(self, tab, picks) -> dict:
        """{feature type: bool} for every type with a dialog: whether a tool of that type can start with `picks`
        (some pick fills one of its fields; with no picks every type can). A QUERY."""

    def preview(self, tab, feature, type, params) -> dict:
        """What a Part Studio would look like with a feature's proposed parameters (I5.4), for an open dialog. A
        QUERY, like `refs`: the committed model, the revision, the undo stack and the saved data are untouched, and
        no tab that consumes this one is rebuilt. `feature` is the id of the stored feature being edited (the model
        is rebuilt from the state before it, with `params` in its place, and stops there, as the desktop's dialog
        preview does), or None for a new feature of `type` (added where a new feature goes, the rollback bar). For an
        existing feature `type` must be its stored type. Returns
            {"preview": n, the tab's preview number (1, 2, ... per tab, in the order the requests were made),
             "feature": the feature id (None for a new one), "ok": bool,
             "outputs": a StudioOutputs of the previewed model (its revision, features, can_undo and can_redo are
                        the committed tab's; `errors` holds the previewed feature's error, if any; `manipulators`
                        holds the dialog's drag arrows, dialog_schema.manipulators, empty for most types), or None,
             "error": None, or a message naming the feature when it cannot build ("Extrude 1: ..."),
             "superseded": True only on a request a newer one replaced before it started (no outputs then)}.
        A preview that cannot build is an answer with "ok": False, not an exception. EngineError for an unknown
        feature, a type that does not match, or a tab that is not a Part Studio."""
        return self.submit_preview(tab, feature, type, params)[1].result()

    @abc.abstractmethod
    def base_outputs(self, tab, feature) -> "StudioOutputs":
        """The model an open feature dialog picks against (F3), as a StudioOutputs: the Part Studio built up to just
        before the stored feature `feature` (an id), or, for None (a new feature), up to the rollback bar. A QUERY,
        like `refs`: nothing changes. Its revision, features, can_undo and can_redo are the committed tab's; `errors`
        and `error_refs` are empty. `refs(tab, picks, {"feature": feature})` reads this model, with the same
        element indices. EngineError for an unknown feature or a tab that is not a Part Studio."""

    @abc.abstractmethod
    def submit_preview(self, tab, feature, type, params) -> tuple:
        """`preview` without waiting: (n, Future of the answer). The process executor keeps at most one preview of a
        tab waiting behind the one its worker has: a newer request supersedes a waiting one, whose Future then
        answers {"preview": its n, "superseded": True, "ok": False, "outputs": None, ...}."""

    @abc.abstractmethod
    def sketch_frame(self, tab, plane) -> dict:
        """What a sketch session needs of a plane (I7.1). A QUERY, like `refs`: nothing changes. `plane` is a plane
        reference as a sketch feature's `plane` param stores it ({"kind": "std", "name"}, {"kind": "plane", "fid"},
        {"kind": "face", "face": a face reference}) or the id of an existing sketch feature (a string). Returns
            {"origin": [x, y, z], "xdir", "ydir", "normal": the sketch plane's frame in world coordinates,
             "edges": [{"pick": {"part", "kind": "edge", "index": 1-based, "want": ["edge"]}, a pick the refs route
                        accepts, "points": [[u, v], ...] the edge as a polyline in sketch coordinates,
                        "height": the largest distance of the edge from the plane, 0 for an edge lying in it}],
             "variables": {name: number, no "#"} the variables in force at the sketch: those defined before an existing
                          sketch feature, or before the rollback bar for a new one (as the desktop's Sketcher has them)},
        for every non-seam edge of the committed model. EngineError, naming the face, for a face that is not
        planar; EngineError for a reference that finds nothing, or a tab that is not a Part Studio."""

    @abc.abstractmethod
    def project_edge(self, tab, plane, pick) -> dict:
        """One model edge as the sketch entity the desktop's Use tool adds (I7.1). A QUERY. `plane` as in
        `sketch_frame`; `pick` a pick of kind "edge" (the refs route's format; `want` may be left out). Returns
            {"kind": "line" | "circle" | "arc" | "point",
             "entity": {"type": "line", "a", "b"} | {"type": "circle", "c", "r"} | {"type": "arc", "c", "p1", "p2"
                        (counter-clockwise from p1 to p2)} | {"type": "point", "p"}, each with "fixed": True, in
                        sketch coordinates,
             "ref": the reference the entity stores as `src` so it follows the part, or None}.
        EngineError for an edge that is neither straight nor circular on the plane; BadPick for a malformed pick."""

    # ------------------------------------------------------------------ mate dialogs (I10.5, freeshape/mate_session.py)
    @abc.abstractmethod
    def mate_dialog_open(self, tab, dialog, spec) -> dict:
        """Open a mate dialog `dialog` (an id the caller picks) on an Assembly tab: a MateSession kept in the tab (in
        its worker for the process executor) until `mate_dialog_close`. `spec` is {"type": a mate type, "picks":
        [(instance, "face" | "edge", 0-based index, point or None)]} for a new mate, or {"mate": id} for a stored one.
        A QUERY for the tab: no edit, no revision, no undo step; the session previews and puts the Assembly back
        (MateSession.preview_detached). Returns `mate_dialog_update`'s answer. EngineError with the desktop's
        message for an Assembly with fewer than two parts, an unknown type or mate, an id already open, or a tab
        that is not an Assembly."""

    @abc.abstractmethod
    def mate_dialog_update(self, tab, dialog, changes) -> dict:
        """Apply `changes` (mate_session.parse_changes's output) to the open mate dialog and preview it. A QUERY.
        Returns {**MateSession.view(), "placements": {instance id: 4x4} as previewed, "values": [{"dof", "label",
        "value", "unit"}], "error": a refused pick's message, else the previewed mate's error, else None,
        "dof": the previewed degrees of freedom}. EngineError for a dialog that is not open."""

    @abc.abstractmethod
    def mate_dialog_result(self, tab, dialog) -> dict:
        """The edit that accepts the open mate dialog: {"op": "add_mate" | "replace_mate", "mate": the mate}. A QUERY:
        the dialog stays open (the caller submits the edit, then closes it). EngineError with the desktop's message
        while a side is empty, or for a dialog that is not open, or an edited mate that is gone."""

    @abc.abstractmethod
    def mate_dialog_close(self, tab, dialog) -> None:
        """Forget the mate dialog (nothing changes; a dialog that is not open is no error)."""

    # ------------------------------------------------------------------ the Link graph
    def rebuild_dependents(self, tab) -> list:
        """Rebuild the tabs that consume `tab`, each once, after the tabs they consume, and only those whose
        consumed output changed. `submit_edit` calls this; returns the tab ids rebuilt, in order."""
        return []

    def rebuild_count(self, tab) -> int:
        """How many times the engine rebuilt `tab` because an output it consumed changed (its own edits do
        not count: see `revision`)."""
        return self.links.graph.rebuilds.get(tab, 0)

    def rebuild_counts(self) -> dict:
        """{tab id: rebuild_count} for every tab rebuilt so far."""
        return dict(self.links.graph.rebuilds)

    def rebuild_order(self) -> list:
        """The tab ids the last `rebuild_dependents` rebuilt, in the order it rebuilt them."""
        return list(self.links.graph.last_order)

    def link_edges(self) -> list:
        """[(consumer tab id, upstream tab id)]: every output one tab has been handed from another."""
        return self.links.graph.edges()

    def close(self) -> None:
        """Close every tab (and so, in the process executor, end every worker)."""
        for tab, *_rest in list(self.tabs()):
            self.close_tab(tab)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
