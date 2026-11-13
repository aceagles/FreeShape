"""The in-process executor: every tab runs in this process, wrapping the existing Part Studio
(document.Document), AssemblyDoc and DrawingDoc. I3.2 runs the same contract with one worker process per
tab (ADR 0002); everything `outputs` returns is plain data for that reason.

Staleness follows FreeCAD's touched-then-recompute idea at tab granularity: every applied edit bumps the
engine's generation, and a tab that consumes other tabs (an assembly, a drawing) re-reads its inputs on its
next edit or `outputs` if the generation moved since it last did. A Part Studio rebuilds on the edit itself.
"""
from __future__ import annotations

import copy
import json
import os

import numpy as np

from .. import assembly as A
from .. import drawing as D
from ..document import Document
from ..variable_studio import FILE_FORMAT as VARIABLES_FORMAT
from ..variable_studio import VariableStudioDoc
from .contract import (ASSEMBLY_OPS, DRAWING_OPS, STUDIO_OPS, VARIABLES_OPS, AssemblyOutputs, ConnectorOut,
                       DrawingOutputs, Engine, EngineError, InstanceOut, MateOut, StudioOutputs, VariablesOutputs,
                       plain)
from .encode import Encoder, sketch_out
from .inputs import CrossTabInputs
from .linkgraph import live_package, package_digest, propagate
from .. import links

KINDS = ("studio", "assembly", "drawing", "variables")
_FORMAT_KIND = {"freeshape": "studio", A.FILE_FORMAT: "assembly", D.FILE_FORMAT: "drawing",
                VARIABLES_FORMAT: "variables"}
REF_TYPE = "variable_studio_ref"


def file_kind(path):
    """"studio" | "assembly" | "drawing" from the file's own format tag."""
    try:
        with open(path) as fh:
            fmt = json.load(fh).get("format")
    except (OSError, ValueError, AttributeError) as e:
        raise EngineError(f"Cannot read {path}: {e}") from e
    kind = _FORMAT_KIND.get(fmt)
    if kind is None:
        raise EngineError(f"{path} is not a FreeShape Part Studio, Assembly or Drawing")
    return kind


def _need(edit, key):
    if key not in edit:
        raise EngineError(f"edit {edit.get('op')!r} needs {key!r}")
    return edit[key]


# =========================================================================== tabs

class _Tab:
    kind = ""
    OPS: dict = {}

    def __init__(self, engine, tab, doc, consumes=()):
        self.engine = engine
        self.tab = tab
        self.doc = doc
        self.revision = 0
        self.consumes = tuple(consumes)
        self.seen = engine.generation
        self._syncing = False

    @property
    def path(self):
        return self.doc.path

    def sync(self):
        """Bring the tab up to date with the tabs it consumes."""

    def rebuild(self):
        """Re-read the tabs it consumes now: what the Link graph asks for when a consumed output changed."""

    def apply(self, edit):
        if not isinstance(edit, dict):
            raise EngineError("an edit is a dict with an 'op' key")
        op = edit.get("op")
        if op not in self.OPS:
            raise EngineError(f"a {self.kind} tab has no edit {op!r} (it has: {', '.join(self.OPS)})")
        self.sync()
        getattr(self, "_" + op)(edit)

    def _undo(self, edit):
        self.doc.undo()

    def _redo(self, edit):
        self.doc.redo()

    def _save(self, edit):
        path = edit.get("path") or self.doc.path
        if not path:
            raise EngineError("save needs a path for a tab that has never been saved")
        self.doc.save(os.path.abspath(path))

    def outputs(self):
        raise NotImplementedError


class StudioTab(_Tab):
    kind = "studio"
    OPS = STUDIO_OPS

    def _refs(self):
        return [f for f in self.doc.features if f.get("type") == REF_TYPE and not f.get("suppressed")]

    def _track(self):
        """Rebuild (cheap: cached) while recording which Variable Studio tabs the references read."""
        gen = self.engine.generation
        if not self._refs():
            self.consumes = ()
            self.engine.inputs.commit(self.tab, ())
        else:
            self._syncing = True
            try:
                with self.engine.inputs.recording(self.tab) as used:
                    self.doc.rebuild()
                self.consumes = tuple(used)
            finally:
                self._syncing = False
        self.seen = gen

    def rebuild(self):
        self._track()

    def sync(self):
        """Re-read the Variable Studios the references consume if any tab was edited since."""
        if not self._syncing and self.seen != self.engine.generation:
            self._track()

    def apply(self, edit):
        super().apply(edit)
        self._track()

    def _feature(self, fid):
        f = self.doc.feature(fid)
        if f is None:
            raise EngineError(f"no feature {fid!r} in “{self.doc.name}”")
        return f

    def _set_param(self, edit):
        f = copy.deepcopy(self._feature(_need(edit, "feature")))
        keys = _need(edit, "key")
        keys = list(keys) if isinstance(keys, (list, tuple)) else [keys]
        node = f["params"]
        for k in keys[:-1]:
            node = node[k]
        node[keys[-1]] = copy.deepcopy(_need(edit, "value"))
        self.doc.replace_feature(f)

    def _replace_feature(self, edit):
        f = copy.deepcopy(_need(edit, "feature"))
        self._feature(f.get("id"))
        self.doc.replace_feature(f)

    def _add_feature(self, edit):
        f = copy.deepcopy(_need(edit, "feature"))
        if not f.get("id") or self.doc.feature(f["id"]) is not None:
            f["id"] = self.doc.new_id()
        self.doc.add_feature(f, edit.get("index"))

    def _delete_features(self, edit):
        self.doc.delete_features(set(_need(edit, "ids")))

    def _set_suppressed(self, edit):
        self._feature(_need(edit, "feature"))
        self.doc.set_suppressed(edit["feature"], bool(_need(edit, "value")))

    def _set_rollback(self, edit):
        self.doc.set_rollback(edit.get("index"))

    def _rename_feature(self, edit):
        self._feature(_need(edit, "feature"))
        self.doc.rename_feature(edit["feature"], str(_need(edit, "name")))

    def _set_part_override(self, edit):
        kw = {k: edit[k] for k in ("name", "color") if k in edit}
        self.doc.set_part_override(_need(edit, "part"), **kw)

    def outputs(self):
        self.sync()
        doc, enc = self.doc, self.engine.encoder
        st = doc.state
        for p in st.parts.values():     # I3.7: hand each part its element-name map (encode.element_names reads it)
            if getattr(p, "named", None) is None:
                p.named = st.named(p.id)
        parts = [enc.part(p, hidden=p.id in doc.hidden_parts) for p in st.parts.values()]
        sketches = []
        for fid, res in st.sketches.items():
            f = doc.feature(fid)
            sketches.append(sketch_out(fid, f["name"] if f else fid, res))
        return StudioOutputs(tab=self.tab, kind=self.kind, name=str(doc.name), revision=self.revision,
                             errors=plain(dict(doc.errors)), consumes=self.consumes, parts=parts,
                             sketches=sketches, variables=plain(dict(st.vars)))


class VariablesTab(_Tab):
    kind = "variables"
    OPS = VARIABLES_OPS

    def _row(self, edit):
        i = _need(edit, "index")
        if not isinstance(i, int) or not 0 <= i < len(self.doc.rows):
            raise EngineError(f"no row {i!r} in “{self.doc.name}”")
        return i

    def _add_row(self, edit):
        self.doc.add_row(str(edit.get("name", "")), str(edit.get("expression", "")), edit.get("index"))

    def _set_row(self, edit):
        self.doc.set_row(self._row(edit), name=edit.get("name"), expression=edit.get("expression"))

    def _remove_row(self, edit):
        self.doc.remove_row(self._row(edit))

    def values(self):
        return self.doc.values()[0]

    def outputs(self):
        doc = self.doc
        values, errors = doc.values()
        return VariablesOutputs(tab=self.tab, kind=self.kind, name=str(doc.name), revision=self.revision,
                                errors={str(i): m for i, m in errors.items()}, consumes=(),
                                variables=plain(dict(values)), rows=plain([dict(r) for r in doc.rows]))


class _ConsumerTab(_Tab):
    """A tab that consumes other tabs' outputs through the engine's CrossTabInputs."""

    def _refresh(self):
        raise NotImplementedError

    def sync(self, force=False):
        if self._syncing or not (force or self.seen != self.engine.generation):
            return
        self._syncing = True
        try:
            with self.engine.inputs.recording(self.tab) as used:
                self._refresh()
            self.consumes = tuple(used)
            self.seen = self.engine.generation
        finally:
            self._syncing = False

    def rebuild(self):
        self.sync(force=True)

    def apply(self, edit):
        # the "refresh" edit is a forced sync (its name would otherwise dispatch to the _refresh hook)
        if isinstance(edit, dict) and edit.get("op") == "refresh":
            return self.sync(force=True)
        return super().apply(edit)

    def _save(self, edit):
        self.engine.inputs.repair(self.doc)
        super()._save(edit)


class AssemblyTab(_ConsumerTab):
    kind = "assembly"
    OPS = ASSEMBLY_OPS

    def _refresh(self):
        self.doc.refresh(solve=True, notify=False)

    def _instance(self, iid):
        if self.doc.instance(iid) is None:
            raise EngineError(f"no instance {iid!r} in “{self.doc.name}”")
        return iid

    def _mate(self, mid):
        if self.doc.mate(mid) is None:
            raise EngineError(f"no mate {mid!r} in “{self.doc.name}”")
        return mid

    def _move_instance(self, edit):
        T = np.asarray(_need(edit, "T"), float)
        if T.shape != (4, 4):
            raise EngineError("T must be a 4x4 matrix")
        self.doc.move_instance(self._instance(_need(edit, "instance")), T, solve=bool(edit.get("solve", True)))

    def _set_hidden(self, edit):
        self.doc.set_hidden(self._instance(_need(edit, "instance")), bool(_need(edit, "value")))

    def _set_fixed(self, edit):
        self.doc.set_fixed(self._instance(_need(edit, "instance")), bool(_need(edit, "value")))

    def _set_suppressed(self, edit):
        self.doc.set_suppressed(self._mate(_need(edit, "mate")), bool(_need(edit, "value")))

    def _rename(self, edit):
        oid = _need(edit, "id")
        if self.doc.instance(oid) is None and self.doc.mate(oid) is None:
            raise EngineError(f"no instance or mate {oid!r} in “{self.doc.name}”")
        self.doc.rename(oid, str(_need(edit, "name")))

    def _delete_instances(self, edit):
        self.doc.delete_instances(set(_need(edit, "ids")))

    def _delete_mates(self, edit):
        self.doc.delete_mates(set(_need(edit, "ids")))

    def _solve(self, edit):
        self.doc.solve()

    def _connector(self, c):
        frame = c.get("frame") or {}
        world = {}
        if frame:
            world = A.frame_to_json(self.doc.world_frame(c))
        index = c.get("index")
        return ConnectorOut(instance=str(c.get("inst")), kind=str(c.get("kind")),
                            index=None if index is None else int(index) + 1,
                            frame=plain(dict(frame)), world=plain(world))

    def outputs(self):
        self.sync()
        doc, enc = self.doc, self.engine.encoder
        parts, instances, mates = {}, [], []
        for inst in doc.instances:
            p = doc.parts.get(inst["id"])
            key = None
            if p is not None:
                po = enc.part(p)
                parts.setdefault(po.hash, po)
                key = po.hash
            instances.append(InstanceOut(
                id=str(inst["id"]), name=str(inst.get("name", "")), part=key,
                T=[[float(x) for x in row] for row in doc.T(inst["id"])], hidden=bool(inst.get("hidden")),
                fixed=bool(inst.get("fixed")), color=tuple(float(c) for c in doc.instance_color(inst)),
                source=plain(copy.deepcopy(inst.get("source") or {}))))
        for m in doc.mates:
            rest = {k: v for k, v in m.items() if k not in ("id", "name", "type", "suppressed", "a", "b")}
            mates.append(MateOut(id=str(m["id"]), name=str(m.get("name", "")), type=str(m.get("type")),
                                 suppressed=bool(m.get("suppressed")), a=self._connector(m["a"]),
                                 b=self._connector(m["b"]), params=plain(rest)))
        return AssemblyOutputs(tab=self.tab, kind=self.kind, name=str(doc.name), revision=self.revision,
                               errors=plain(dict(doc.errors)), consumes=self.consumes, parts=parts,
                               instances=instances, mates=mates, dof=int(doc.dof))


class DrawingTab(_ConsumerTab):
    kind = "drawing"
    OPS = DRAWING_OPS

    def _refresh(self):
        self.doc.refresh(notify=False)

    def _set_source(self, edit):
        self.doc.checkpoint()
        self.doc.source = copy.deepcopy(_need(edit, "source"))
        self.sync(force=True)

    def _auto_layout(self, edit):
        self.doc.auto_layout()

    def outputs(self):
        self.sync()
        doc = self.doc
        model = None if doc.shape is None else self.engine.encoder.brep(doc.shape)[1]
        curves = {str(v["id"]): plain(doc.curves(v)) for v in doc.views}
        return DrawingOutputs(tab=self.tab, kind=self.kind, name=str(doc.name), revision=self.revision,
                              errors={"": str(doc.error)} if doc.error else {}, consumes=self.consumes,
                              source=plain(copy.deepcopy(doc.source)), sheet=plain(dict(doc.sheet)),
                              title=plain(dict(doc.title)), views=plain(copy.deepcopy(doc.views)),
                              annots=plain(copy.deepcopy(doc.annots)), curves=curves, model=model)


_TAB_CLASS = {"studio": StudioTab, "assembly": AssemblyTab, "drawing": DrawingTab, "variables": VariablesTab}


# =========================================================================== the engine

class InProcessEngine(Engine):
    LINKS = True         # record the Link graph and rebuild dependents (off in a worker: the parent owns the graph)

    def __init__(self, meshes=True):
        self.generation = 0
        self.encoder = Encoder(meshes=meshes)
        self.inputs = CrossTabInputs(self)
        self.links = self.inputs.resolver
        self._tabs: dict[str, _Tab] = {}
        self._counter = 0

    # ------------------------------------------------------------------ tab registry
    def _new_id(self):
        self._counter += 1
        while f"T{self._counter}" in self._tabs:
            self._counter += 1
        return f"T{self._counter}"

    def _get(self, tab) -> _Tab:
        t = self._tabs.get(tab)
        if t is None:
            raise EngineError(f"no tab {tab!r}")
        return t

    def _register(self, kind, doc, tab, consumes=()):
        if tab is not None and tab in self._tabs:
            raise EngineError(f"tab id {tab!r} is already open")
        tab = tab or self._new_id()
        self._tabs[tab] = _TAB_CLASS[kind](self, tab, doc, consumes)
        return tab

    def _by_path(self, path):
        ap = os.path.abspath(path)
        return next((t for t in self._tabs.values() if t.path and os.path.abspath(t.path) == ap), None)

    def open_tab(self, path, tab=None):
        open_already = self._by_path(path)
        if open_already is not None:
            return open_already.tab
        kind = file_kind(path)
        path = os.path.abspath(path)
        with self.inputs.recording() as used:
            if kind == "studio":
                doc = Document.load(path)
                doc.var_inputs = self.inputs.variables
                if any(f.get("type") == REF_TYPE for f in doc.features):
                    doc.rebuild()            # the load ran without the resolver
            elif kind == "variables":
                doc = VariableStudioDoc.load(path)
            elif kind == "assembly":
                doc = A.AssemblyDoc.load(path, resolver=self.inputs.part)
            else:
                doc = D.DrawingDoc.load(path, resolver=self.inputs.drawing_shape)
        tab = self._register(kind, doc, tab, used)
        self.inputs.commit(tab, used)
        return tab

    def new_tab(self, kind, name=None, tab=None, sources=()):
        if kind not in KINDS:
            raise EngineError(f"unknown tab kind {kind!r}")
        if kind == "studio":
            doc = Document()
            doc.var_inputs = self.inputs.variables
        elif kind == "variables":
            doc = VariableStudioDoc()
        elif kind == "assembly":
            doc = A.AssemblyDoc()
            doc.resolver = self.inputs.part
        else:
            doc = D.DrawingDoc()
            doc.resolver = self.inputs.drawing_shape
        if name:
            doc.name = str(name)
        return self._register(kind, doc, tab)

    def close_tab(self, tab):
        self._get(tab)
        del self._tabs[tab]
        self.links.forget_tab(tab)
        self.generation += 1          # whatever consumed it re-reads its inputs

    def tabs(self):
        return [(t.tab, t.kind, str(t.doc.name), t.path) for t in self._tabs.values()]

    def provide(self, kind, uid=None, path=None, tab=None):
        """(tab id, live document) for a link -- by tab id, then uid, then file path -- opening the file as a
        tab of its own if no tab has it yet. None if nothing matches. The parent half of CrossTabInputs."""
        t = self._tabs.get(tab) if tab else None
        if t is not None and t.kind != kind:
            t = None
        if t is None and uid:
            t = next((x for x in self._tabs.values() if x.kind == kind and getattr(x.doc, "uid", None) == uid), None)
        if t is None and path:
            t = self._by_path(path)
            if t is None and os.path.exists(path):
                try:
                    if file_kind(path) == kind:
                        t = self._tabs[self.open_tab(path)]
                except Exception:
                    t = None
        if t is None or t.kind != kind:
            return None
        t.sync()
        return t.tab, t.doc

    # ------------------------------------------------------------------ the contract
    def submit_edit(self, tab, edit):
        t = self._get(tab)
        t.apply(edit)
        t.revision += 1
        self.generation += 1
        t.seen = self.generation
        if self.LINKS:
            self.rebuild_dependents(tab)
        return t.revision

    # ------------------------------------------------------------------ the Link graph
    def make_edge(self, tab, keys):
        """The edge for a consumer that read `keys` of tab `tab` just now (None if the tab is gone)."""
        u = self._tabs.get(tab)
        if u is None:
            return None
        return links.Edge(u.kind, keys, package_digest(u.kind, live_package(u.kind, u.doc, self.encoder), keys))

    def _digest_now(self, tab, edge):
        u = self._tabs.get(tab)
        if u is None:
            return edge.digest                    # closed: its consumers keep the version they have
        u.sync()
        return package_digest(u.kind, live_package(u.kind, u.doc, self.encoder), edge.keys)

    def _label(self, tab):
        t = self._tabs.get(tab)
        return f"{tab} “{t.doc.name}”" if t is not None else str(tab)

    def rebuild_dependents(self, tab):
        self._get(tab)
        return propagate(self.links.graph, tab, self._digest_now, lambda x: self._tabs[x].rebuild(), self._label)

    def outputs(self, tab):
        return self._get(tab).outputs()
