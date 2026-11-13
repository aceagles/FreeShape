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
    "set_part_override": "part, plus any of name / color",
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
class StudioOutputs(TabOutputs):
    parts: list = field(default_factory=list)          # [PartOut], in the studio's part order
    sketches: list = field(default_factory=list)       # [SketchOut]
    connectors: list = field(default_factory=list)     # [MateConnectorOut], in feature order
    variables: dict = field(default_factory=dict)      # name -> number


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
    def new_tab(self, kind, name=None, tab=None, sources=()) -> str:
        """A new empty tab of `kind` ("studio" | "assembly" | "drawing"). `sources` are the link sources it
        already consumes (a reopened tab): the process executor records their edges at once, before any edit."""

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
