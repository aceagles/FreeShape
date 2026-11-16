"""The in-process executor: every tab runs in this process, wrapping the existing Part Studio
(document.PartStudioDoc), AssemblyDoc and DrawingDoc. I3.2 runs the same contract with one worker process per
tab (ADR 0002); everything `outputs` returns is plain data for that reason.

Staleness follows FreeCAD's touched-then-recompute idea at tab granularity: every applied edit bumps the
engine's generation, and a tab that consumes other tabs (an assembly, a drawing) re-reads its inputs on its
next edit or `outputs` if the generation moved since it last did. A Part Studio rebuilds on the edit itself.
"""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import os
import re
from concurrent.futures import Future
from types import SimpleNamespace

import numpy as np

from .. import assembly as A
from .. import drawing as D
from .. import features as F
from ..document import PartStudioDoc
from ..variable_studio import VariableStudioDoc
from .contract import (ASSEMBLY_OPS, BadPick, DRAWING_OPS, STUDIO_OPS, VARIABLES_OPS, AssemblyOutputs, ConnectorOut,
                       DrawingOutputs, Engine, EngineError, InstanceOut, MateOut, StudioOutputs, VariablesOutputs,
                       plain)
from .encode import Encoder, connector_out, element_names, feature_list, sketch_out
from .inputs import CrossTabInputs
from .linkgraph import live_package, package_digest, propagate
from .. import blobstore
from .. import dialog_schema as DSC
from .. import kernel as K
from .. import picking as PK
from .. import sketch_ops as OPS
from ..sketch import Sketch
from .. import links
from .. import mate_session as MS
from .. import tabkinds as TK

KINDS = TK.KINDS            # the registered tab kinds (freeshape/tabkinds.py), a live view


def file_kind(path):
    """The tab kind ("studio", "assembly", "drawing", "variables") from the file's own format tag."""
    try:
        with open(path) as fh:
            fmt = json.load(fh).get("format")
    except (OSError, ValueError, AttributeError) as e:
        raise EngineError(f"Cannot read {path}: {e}") from e
    kind = TK.kind_for_format(fmt)
    if kind is None:
        raise EngineError(f"{path} is not a FreeShape Part Studio, Assembly or Drawing")
    return kind


def read_data(kind, data, path=None):
    """The decoded JSON of a tab's bytes, checked to be a tab of `kind` (EngineError otherwise)."""
    label = path or f"this {kind} tab's data"
    if kind not in KINDS:
        raise EngineError(f"unknown tab kind {kind!r}")
    try:
        d = json.loads(bytes(data).decode("utf-8"))
        fmt = d.get("format")
    except (TypeError, ValueError, AttributeError) as e:
        raise EngineError(f"Cannot read {label}: {e}") from e
    if TK.kind_for_format(fmt) != kind:
        raise EngineError(f"{label} is not a FreeShape {TK.get(kind).label}")
    return d


_BLOB_KEY = re.compile(r"[0-9a-f]{64}")


def fetch_blobs(obj, lookup, have):
    """{key: bytes} of the blobs `lookup` holds that `obj` (decoded JSON: tab data or an edit) names and `have`
    does not hold yet, followed through blobs that are JSON themselves (a Context capture's listing names its
    neighbours' blobs). A blob key is any 64-hex string the lookup has, so a kind that adds blob keys to its data
    needs nothing here."""
    found, todo = {}, [obj]
    while todo:
        x = todo.pop()
        if isinstance(x, dict):
            todo.extend(x.keys())
            todo.extend(x.values())
        elif isinstance(x, (list, tuple)):
            todo.extend(x)
        elif isinstance(x, str) and len(x) == 64 and x not in have and x not in found \
                and _BLOB_KEY.fullmatch(x) and lookup.has(x):
            b = found[x] = bytes(lookup.get(x))
            if b[:1] in (b"{", b"["):
                try:
                    todo.append(json.loads(b.decode("utf-8")))
                except ValueError:
                    pass
    return found


def put_blobs(sink, blobs, tab):
    """Put {key: bytes} into `sink` (an object with `put`); EngineError if there are blobs and no sink."""
    if not blobs:
        return
    if sink is None:
        raise EngineError(f"tab {tab!r} has blobs to keep and no blob lookup: pass blobs= to tab_data")
    for b in blobs.values():
        sink.put(b)


class _Collect:
    """A blob sink that keeps what is put into it ({key: bytes})."""

    def __init__(self):
        self.blobs = {}

    def put(self, data):
        key = hashlib.sha256(data).hexdigest()
        self.blobs[key] = bytes(data)
        return key


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
        self.blobs = None        # the blob lookup the tab was opened with (open_tab_data)

    @property
    def path(self):
        return self.doc.path

    def data(self):
        """(bytes, {key: bytes}): the tab's data as `save` writes it, and the blobs it names (a Part Studio's
        Python-feature builds and Context captures), without writing anything."""
        sink = _Collect()
        if hasattr(self.doc, "write_blobs"):
            self.doc.write_blobs(store=sink)
        return self.doc.to_data(), sink.blobs

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

    def refs(self, picks, base=None):
        raise EngineError(f"only a Part Studio has elements to pick (this tab is a {self.kind})")

    def base_outputs(self, fid):
        raise EngineError(f"only a Part Studio has a feature dialog (this tab is a {self.kind})")

    def feature_info(self, fid):
        raise EngineError(f"only a Part Studio has features (this tab is a {self.kind})")

    def start_feature(self, ftype, picks):
        raise EngineError(f"only a Part Studio has features (this tab is a {self.kind})")

    def startable(self, picks):
        raise EngineError(f"only a Part Studio has features (this tab is a {self.kind})")

    def preview(self, fid, ftype, params):
        raise EngineError(f"only a Part Studio has a feature preview (this tab is a {self.kind})")

    def sketch_frame(self, plane):
        raise EngineError(f"only a Part Studio has sketch planes (this tab is a {self.kind})")

    def project_edge(self, plane, pick):
        raise EngineError(f"only a Part Studio has edges to project (this tab is a {self.kind})")

    def mate_dialog(self, verb, dialog, arg=None):
        raise EngineError(f"only an Assembly has mates (this tab is a {self.kind})")


def apply_part_override(doc, edit):
    """The `set_part_override` edit on a Part Studio Document: a part's name / color (None puts it back) and
    `hidden` (the eye: true hides the part, false shows it; a view setting, no undo step), or
    `colors`, a {part: color} for several parts in one undo step. The window runs this too, so both copies of a
    tab change the same way."""
    colors = edit.get("colors")
    if colors is not None:
        doc.set_part_colors(colors)
        return
    part = _need(edit, "part")
    hidden = edit.get("hidden")
    if hidden is not None:
        if not isinstance(hidden, bool):
            raise EngineError("set_part_override: hidden is true or false")
        if part not in doc.state.parts:
            raise EngineError(f"no part {part!r} in “{doc.name}”")
    kw = {k: edit[k] for k in ("name", "color") if k in edit}
    gone = [k for k, v in kw.items() if v is None]
    if kw and not gone:
        doc.set_part_override(part, **kw)
    elif gone:
        doc.checkpoint()
        ov = doc.part_overrides.setdefault(part, {})
        ov.update({k: v for k, v in kw.items() if v is not None})
        for k in gone:
            ov.pop(k, None)
        if not ov:
            del doc.part_overrides[part]
        doc.rebuild()
    if hidden is not None:      # the eye: a view setting like the desktop's toggle, so no undo step
        doc.set_parts_hidden([part], hidden)


class StudioTab(_Tab):
    kind = "studio"
    OPS = STUDIO_OPS
    previews = 0             # previews built on this tab (I5.4): a test counts the rebuilds a run of requests made

    def _refs(self):
        return [f for f in self.doc.features if links.feature_source(f) is not None and not f.get("suppressed")]

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
        apply_part_override(self.doc, edit)

    def _set_features_hidden(self, edit):
        ids = _need(edit, "ids")
        value = _need(edit, "value")
        if not isinstance(value, bool) or not isinstance(ids, (list, tuple)):
            raise EngineError("set_features_hidden: ids is a list, value is true or false")
        for i in ids:
            f = self.doc.feature(i)
            if f is None and i not in ("Top", "Front", "Right"):
                raise EngineError(f"no feature {i!r} in “{self.doc.name}”")
            if f is not None and f.get("type") not in ("sketch", "plane", "axis", "context"):
                raise EngineError(f"feature {i!r} has no eye (it is a {f.get('type')})")
        self.doc.set_features_hidden(ids, value)

    def _folder(self, edit):
        fo = _need(edit, "folder")
        if fo not in self.doc.folders:
            raise EngineError(f"no folder {fo!r} in “{self.doc.name}”")
        return fo

    def _make_folder(self, edit):
        want = edit.get("folder")
        if want and want in self.doc.folders:
            raise EngineError(f"“{self.doc.name}” already has a folder {want!r}")
        try:
            fo = self.doc.make_folder(list(_need(edit, "ids")), edit.get("name"))
        except ValueError as e:
            raise EngineError(str(e)) from e
        name = edit.get("name")
        if name:                    # make_folder gives a name another folder has a new one
            self.doc.folders[fo]["name"] = str(name)
        if want and want != fo:     # the window's own folder id, so later edits name the same folder
            self.doc.folders[want] = self.doc.folders.pop(fo)
            for f in self.doc.features:
                if f.get("folder") == fo:
                    f["folder"] = want

    def _ungroup_folder(self, edit):
        self.doc.ungroup_folder(self._folder(edit))

    def _remove_from_folder(self, edit):
        try:
            self.doc.remove_from_folder(list(_need(edit, "ids")))
        except ValueError as e:
            raise EngineError(str(e)) from e

    def _rename_folder(self, edit):
        self.doc.rename_folder(self._folder(edit), str(_need(edit, "name")))

    def _set_folder_open(self, edit):
        self.doc.set_folder_open(self._folder(edit), bool(_need(edit, "value")))

    def _set_folder_suppressed(self, edit):
        self.doc.set_folder_suppressed(self._folder(edit), bool(_need(edit, "value")))

    def _set_configs(self, edit):
        rows = _need(edit, "rows")
        if not isinstance(rows, (list, tuple)) or not all(isinstance(r, dict) and r.get("name") for r in rows):
            raise EngineError("set_configs needs rows: a list of {name, vars, suppress}")
        names = [r["name"] for r in rows]
        if len(set(names)) != len(names):
            raise EngineError("two configurations have the same name")
        active = edit.get("active", 0)
        if rows and not (isinstance(active, int) and 0 <= active < len(rows)):
            raise EngineError(f"no configuration {active!r} to make active")
        self.doc.set_configs(copy.deepcopy(list(rows)), active)

    def _set_active_config(self, edit):
        config = _need(edit, "config")
        names = self.doc.config_names()
        if not (config in names or isinstance(config, int) and 0 <= config < len(names)):
            raise EngineError(f"no configuration {config!r} in “{self.doc.name}”")
        self.doc.set_active_config(config)

    def _set_stored_build(self, edit):
        """Keep (or drop) a feature's stored build: what it shows while its plugin or source is missing. The
        entry names its blobs by key; the tab reads them through its blob lookup. Not a change the person made:
        no undo step, and nothing rebuilds until the features do."""
        fid, build = _need(edit, "feature"), _need(edit, "build")
        if not isinstance(fid, str) or not (build is None or isinstance(build, dict)):
            raise EngineError("set_stored_build needs feature (an id) and build (an entry or None)")
        if build is None:
            self.doc.python_builds.pop(fid, None)
        else:
            self.doc.python_builds[fid] = copy.deepcopy(build)

    def feature_info(self, fid):
        """`Engine.feature`: a copy of the stored feature, the variables defined before it and the defaults a
        feature of its type has now (settings added since it was saved). Reads only: the Document is not changed."""
        f = self._feature(fid)
        base = self.doc.state_before(self.doc.features.index(f))
        try:
            defaults = plain(F.default_params(f["type"], base))
        except Exception:                                     # noqa: BLE001  (as the desktop dialog: none then)
            defaults = {}
        return {"feature": copy.deepcopy(f), "variables": plain(dict(base.vars)), "defaults": defaults}

    def start_feature(self, ftype, picks):
        """`Engine.start_feature`: a new feature of `ftype` as a dialog starts it, its fields seeded from `picks` by
        dialog_schema.apply_preselection on this tab's built state. A query: nothing is changed, no id is taken."""
        if not isinstance(ftype, str) or ftype not in DSC.startable_feature_types():
            raise EngineError(f"no feature type {ftype!r} can be started here")
        self.sync()
        doc = self.doc
        base = doc.state_before(doc.insert_index())
        try:
            params = plain(F.default_params(ftype, base))
        except Exception as e:                                # noqa: BLE001
            raise EngineError(f"{ftype}: cannot make its default parameters: {e}") from e
        fields = DSC.params_to_fields(ftype, params)
        got = DSC.Preselected(fields, {})
        if picks:
            state, keys = _StudioStart(doc).state(picks)
            got = DSC.apply_preselection(ftype, fields, keys, state, params)
        return {"feature": {"type": ftype, "name": doc.next_name(ftype), "suppressed": False,
                            "params": params},
                "variables": plain(dict(base.vars)),
                "defaults": plain(params),
                "fields": {k: [[kind, plain(PK.jsonable(ref))] for kind, ref in v] for k, v in got.fields.items()
                           if not k.endswith("_numbers")},
                "params": plain(PK.jsonable(got.params))}

    def startable(self, picks):
        """`Engine.startable`: {feature type: bool}, whether a tool of that type can start with `picks`."""
        self.sync()
        doc = self.doc
        base = doc.state_before(doc.insert_index())
        out = {}
        read = _StudioStart(doc).state(picks) if picks else None
        for ftype in DSC.startable_feature_types():
            if read is None:
                out[ftype] = True
                continue
            try:
                params = plain(F.default_params(ftype, base))
                fields = DSC.params_to_fields(ftype, params)
                got = DSC.apply_preselection(ftype, fields, read[1], read[0], params)
                out[ftype] = any(got.fields.get(k) != fields.get(k) for k in got.fields)
            except Exception:                                 # noqa: BLE001  (a type that cannot start says no)
                out[ftype] = False
        return out

    def refs(self, picks, base=None):
        try:
            picks = PK.parse_picks(picks)
        except ValueError as e:
            raise BadPick(str(e)) from None
        if base is None:
            st = self.doc.state
        else:
            if not isinstance(base, dict) or set(base) != {"feature"} \
                    or not (base["feature"] is None or isinstance(base["feature"], str)):
                raise EngineError("base: must be {\"feature\": a feature id or None}")
            st = self._base_state(base["feature"])
        reader = _StudioRefs(self.doc, st)
        return [reader.one(i, p) for i, p in enumerate(picks)]

    def _base_state(self, fid):
        """The model a dialog on feature `fid` picks against (F3): the state before it, or for None (a new feature)
        the state at the rollback bar, where a new feature goes. Reads only (compute adds to the rebuild cache)."""
        self.sync()
        doc = self.doc
        index = doc.insert_index() if fid is None else doc.features.index(self._feature(fid))
        return doc.state_before(index)

    def base_outputs(self, fid):
        return self._outputs_of(self._base_state(fid), {}, {})

    def outputs(self):
        self.sync()
        return self._outputs_of(self.doc.state, dict(self.doc.errors), getattr(self.doc, "error_refs", {}))

    # ------------------------------------------------------------------ the sketch session's queries (I7.1)
    def _sketch_plane(self, plane):
        """(the K.Plane, the model before the sketch) for `plane`: a plane reference dict (std / plane feature /
        face, as a sketch feature's `plane` param stores it) or the id of a sketch feature. Reads only."""
        self.sync()
        if isinstance(plane, str):
            f = self._feature(plane)
            if f.get("type") != "sketch":
                raise EngineError(f"feature {plane!r} is a {f.get('type')}, not a sketch")
            sr = self.doc.state.sketches.get(plane)
            if sr is None:
                raise EngineError(f"sketch {plane!r} has no plane (it did not build)")
            return sr.plane, self.doc.state_before(self.doc.features.index(f))
        if not isinstance(plane, dict):
            raise EngineError("plane: must be a plane reference (an object) or a sketch feature id (a string)")
        try:
            return F.resolve_plane(self.doc.state, plane), self.doc.state
        except (K.KernelError, KeyError) as e:
            if str(e) == "Face is not planar":
                face = plane.get("face") or {}
                raise EngineError(f"face {face.get('name') or '(unnamed)'} of part {face.get('part') or '?'} is "
                                  f"not planar: a sketch needs a flat face") from None
            raise EngineError(f"plane: {e}") from None

    def sketch_frame(self, plane):
        pl, base = self._sketch_plane(plane)
        if not isinstance(plane, str):                        # a new sketch is inserted at the rollback bar
            base = self.doc.state_before(self.doc.insert_index())
        edges = []
        for pid, part in self.doc.state.parts.items():
            seams = K.seam_edge_indices(part.shape)
            for i, e in enumerate(K.edges(part.shape)):
                if i in seams:
                    continue
                poly = np.asarray(K.discretize_edge(e), float)
                if not len(poly):
                    continue
                d = (poly - pl.origin) @ pl.normal
                edges.append({"pick": {"part": pid, "kind": "edge", "index": i + 1, "want": ["edge"]},
                              "points": [list(pl.to_local(q)) for q in poly],
                              "height": float(np.abs(d).max())})
        return plain({"origin": pl.origin, "xdir": pl.xdir, "ydir": pl.ydir, "normal": pl.normal, "edges": edges,
                      "variables": dict(base.vars)})

    def project_edge(self, plane, pick):
        pl, base = self._sketch_plane(plane)
        try:
            pk = PK.parse_pick(0, {"want": ["edge"], **pick} if isinstance(pick, dict) else pick)
        except ValueError as e:
            raise BadPick(str(e)) from None
        if pk["kind"] != "edge":
            raise BadPick(f"picks[0].kind: project_edge takes an edge (got {pk['kind']})")
        part = self.doc.state.parts.get(pk["part"])
        if part is None:
            raise BadPick(f"picks[0].part: no part {pk['part']!r} in this tab")
        edges = K.edges(part.shape)
        _need_index("picks[0]", f"part {pk['part']!r} has edges: it", len(edges), pk["index"])
        edge = edges[pk["index"] - 1]
        got = F.project_edge(pl, edge)
        if got is None:
            raise EngineError("that edge has no points to project")
        if got[0] == "curve":
            raise EngineError("Only straight or circular edges can be used here")
        sk = Sketch()                      # a scratch sketch: the entity the desktop's Use tool would add
        out, _ = OPS.add_projected_edge(sk, got, False)
        if got[0] == "point":
            ent = {"type": "point", "p": list(sk.P(out))}
        else:
            e = sk.entities[out]
            if e["type"] == "line":
                ent = {"type": "line", "a": list(sk.P(e["p1"])), "b": list(sk.P(e["p2"]))}
            elif e["type"] == "circle":
                ent = {"type": "circle", "c": list(sk.P(e["c"])), "r": sk.radius(out)}
            else:
                ent = {"type": "arc", "c": list(sk.P(e["c"])), "p1": list(sk.P(e["p1"])), "p2": list(sk.P(e["p2"]))}
        src = OPS.edge_src(base, pk["part"], edge, part.shape)
        return plain({"kind": got[0], "entity": dict(ent, fixed=True), "ref": src})

    def preview(self, fid, ftype, params):
        """`Engine.preview` without its number: the model rebuilt with the proposed params (PartStudioDoc.preview,
        the desktop dialog's replay). Reads only: the features, revision, undo stack and built state stay as they
        are (compute only adds to the doc's rebuild cache). A failure is an answer, never an exception."""
        self.sync()
        doc = self.doc
        if fid is None:
            if not isinstance(ftype, str) or not ftype:
                raise EngineError("preview: a new feature needs its type")
            index = doc.insert_index()
            used = {f["id"] for f in doc.features}
            new_id = next(f"P{n}" for n in itertools.count(1) if f"P{n}" not in used)
            feat = {"id": new_id, "type": ftype, "name": doc.next_name(ftype), "suppressed": False}
        else:
            stored = self._feature(fid)
            if ftype is not None and ftype != stored["type"]:
                raise EngineError(f"preview: feature {fid!r} is a {stored['type']}, not a {ftype}")
            index = doc.features.index(stored)
            feat = {k: v for k, v in stored.items() if k != "params"}
        if not isinstance(params, dict):
            raise EngineError("preview: params must be a dict")
        feat = copy.deepcopy(feat)
        feat["params"] = copy.deepcopy(params)
        name = str(feat.get("name") or feat["type"])
        self.previews += 1
        try:
            st, err = doc.preview(feat, index)
            errors = {feat["id"]: str(err)} if err else {}
            refs = {feat["id"]: doc._error_refs[feat["id"]]} if err and feat["id"] in getattr(doc, "_error_refs", {}) \
                else {}
            out = self._outputs_of(st, errors, refs)
            if not err:                              # I6.3: the dialog's drag arrows, read on the state before it
                out.manipulators = self._manipulators(feat, index)
        except Exception as e:                       # noqa: BLE001  (a preview never takes the worker down)
            return {"feature": fid, "ok": False, "outputs": None, "error": f"{name}: {type(e).__name__}: {e}"}
        return {"feature": fid, "ok": not err, "outputs": out, "error": f"{name}: {err}" if err else None}

    def _manipulators(self, feat, index):
        """The drag arrows of a previewed feature, read on the model before it. They are a hint: any failure is []."""
        try:
            return plain(DSC.manipulators(feat["type"], feat["params"], self.doc.state_before(index)))
        except Exception:                            # noqa: BLE001
            return []

    def _outputs_of(self, st, errors, error_refs):
        doc, enc = self.doc, self.engine.encoder
        for p in st.parts.values():     # I3.7: hand each part its element-name map (encode.element_names reads it)
            if getattr(p, "named", None) is None:
                p.named = st.named(p.id)
        parts = [enc.part(p, hidden=p.id in doc.hidden_parts) for p in st.parts.values()
                 if not p.reference]     # I5.3: a Context body never leaves the studio ...
        context = [enc.part(p, hidden=p.id in doc.hidden_parts) for p in st.parts.values()
                   if p.reference]       # ... except to its own window, in `context`, so it can show and mark them
        sketches = []
        for fid, res in st.sketches.items():
            f = doc.feature(fid)
            sketches.append(sketch_out(fid, f["name"] if f else fid, res))
        connectors = [connector_out(fid, c) for fid, c in st.connectors.items()]
        planes = {str(fid): plain(pl.to_dict()) for fid, pl in st.planes.items()}
        axes = {str(fid): plain(dict(ax)) for fid, ax in getattr(st, "axes", {}).items()}
        from .contract import CanvasOut      # I12.1: the Canvas features' images, in feature order
        canvases = [CanvasOut(**plain(dict(c)), hidden=fid in doc.hidden_features)
                    for fid, c in getattr(st, "canvases", {}).items()]
        features, folders, rollback = feature_list(doc)
        return StudioOutputs(tab=self.tab, kind=self.kind, name=str(doc.name), revision=self.revision,
                             errors=plain(dict(errors)), consumes=self.consumes, parts=parts, context=context,
                             sketches=sketches, connectors=connectors, variables=plain(dict(st.vars)),
                             planes=planes, axes=axes, error_refs=plain(dict(error_refs)),
                             features=features, folders=folders, rollback=rollback,
                             can_undo=bool(doc.can_undo()), can_redo=bool(doc.can_redo()), canvases=canvases)


def _need_index(at, what, n, index):
    if index > n:
        raise BadPick(f"{at}.index: {what} has {n} (got {index}, the first is 1)")


class _StudioRefs:
    """`StudioTab.refs`: the picks read against the model as last built, or against `st` (a dialog's base, F3).
    Reads, never writes: the tab's Document is not touched, nothing is rebuilt and no revision moves."""

    def __init__(self, doc, st=None):
        self.st = doc.state if st is None else st
        self.counts = {}

    def counts_of(self, pid, kind):
        key = (pid, kind)
        if key not in self.counts:
            shape = self.st.parts[pid].shape
            self.counts[key] = len((K.faces if kind == "face" else K.edges if kind == "edge" else K.vertex_points)(shape))
        return self.counts[key]

    def one(self, i, pick):
        st, at = self.st, f"picks[{i}]"
        kind, pid, want = pick["kind"], pick["part"], set(pick["want"])
        if kind in ("region", "sketch_line", "sketch_point"):
            sr = st.sketches.get(pid)
            if sr is None:
                raise BadPick(f"{at}.part: no sketch {pid!r} in this tab")
            if kind == "region":
                _need_index(at, "the sketch has regions: it", len(sr.regions), pick["index"])
                key = (pid, pick["index"] - 1)
            else:
                if pick["entity"] not in sr.sketch.entities:
                    raise BadPick(f"{at}.entity: no entity {pick['entity']!r} in sketch {pid!r}")
                key = (pid, pick["entity"])
        elif pid not in st.parts:
            raise BadPick(f"{at}.part: no part {pid!r} in this tab")
        elif kind == "part":
            key = (pid,)
        else:
            _need_index(at, f"part {pid!r} has {kind}s: it", self.counts_of(pid, kind), pick["index"])
            key = (pid, pick["index"] - 1)
        part = st.parts.get(pid)
        names = {pid: element_names(SimpleNamespace(shape=part.shape, named=st.named(pid)))} if part else {}
        item = PK.make_item({p: q.shape for p, q in st.parts.items()}, st.sketches, kind, key, want,
                            st.named, names)
        if item is not None:
            return PK.result_of(item)
        code, reason = PK.refusal(kind, want, shape=part.shape if kind == "face" and part else None)
        return {"ok": False, "code": code, "reason": reason}


class _StudioStart(_StudioRefs):
    """The picks of a new feature's start (`StudioTab.start_feature`), read against the model as last built: the
    refs route's pick format (1-based indices), turned into the viewport's keys (0-based) with the state
    dialog_schema.apply_preselection reads. A pick may leave `want` out. Reads only."""

    def state(self, raw):
        """(state, [(kind, key)]) for `raw` picks; BadPick for a pick the tab has no such element for."""
        raw = [dict(p, want=p.get("want", ["element"])) if isinstance(p, dict) else p for p in raw]
        try:
            picks = PK.parse_picks(raw)
        except ValueError as e:
            raise BadPick(str(e)) from None
        st = self.st
        keys, names = [], {}
        for i, pick in enumerate(picks):
            kind, pid, at = pick["kind"], pick["part"], f"picks[{i}]"
            if kind in ("region", "sketch_line", "sketch_point"):
                sr = st.sketches.get(pid)
                if sr is None:
                    raise BadPick(f"{at}.part: no sketch {pid!r} in this tab")
                if kind == "region":
                    _need_index(at, "the sketch has regions: it", len(sr.regions), pick["index"])
                    key = (pid, pick["index"] - 1)
                else:
                    if pick["entity"] not in sr.sketch.entities:
                        raise BadPick(f"{at}.entity: no entity {pick['entity']!r} in sketch {pid!r}")
                    key = (pid, pick["entity"])
            elif pid not in st.parts:
                raise BadPick(f"{at}.part: no part {pid!r} in this tab")
            elif kind == "part":
                key = (pid,)
            else:
                _need_index(at, f"part {pid!r} has {kind}s: it", self.counts_of(pid, kind), pick["index"])
                key = (pid, pick["index"] - 1)
            if pid in st.parts and pid not in names:
                names[pid] = element_names(SimpleNamespace(shape=st.parts[pid].shape, named=st.named(pid)))
            keys.append((kind, key))
        return _StartState(st, names), keys


class _StartState:
    """What apply_preselection reads on the worker: the built parts' shapes and sketches, with the rest of the
    model state (`named`, `parts`, `vars` ...) as it is."""

    def __init__(self, st, engine_names):
        self._st = st
        self.part_shapes = {p: q.shape for p, q in st.parts.items()}
        self.sketches = st.sketches
        self.engine_names = engine_names

    def __getattr__(self, name):
        return getattr(self._st, name)


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

    def data(self):
        self.engine.inputs.repair(self.doc)      # as `_save` does before writing
        return super().data()


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
                if po.hash not in parts:
                    # the part's Element names, as the Part Studio it links to has them (a STEP part has none)
                    src = inst.get("source") or {}
                    named = getattr(p, "named", None)
                    # a worker's parts carry the names the parent sent (worker._part); in this process the studio hands
                    # them out only once it has produced its own outputs, so ask it
                    if named is None and not hasattr(self.engine, "upstream") \
                            and src.get("kind") not in ("step", "standard") and not A.is_sketch_source(src):
                        named = self.engine.inputs.named(src)
                    po.names = element_names(SimpleNamespace(named=named, shape=p.shape))
                    parts[po.hash] = po
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

    # -- a drag by a Drag handle (I8.2): move_to is absolute against the placements move_begin remembered, so a
    # dropped (stale) move_to loses nothing; move_begin opens the one undo step and move_end closes it.
    def _drag_close(self):
        d, self._drag = getattr(self, "_drag", None), None
        if d is not None and len(self.doc._undo) == d["undo_len"] and not any(
                not np.allclose(i["T"], d["T"][i["id"]], atol=1e-9) for i in self.doc.instances if i["id"] in d["T"]):
            self.doc._undo.pop()                      # nothing moved (or the mates put everything back): no undo step
        return d

    def _move_begin(self, edit):
        iid = self._instance(_need(edit, "instance"))
        self._drag_close()
        self.doc.checkpoint()
        self._drag = {"inst": iid, "undo_len": len(self.doc._undo),
                      "T": {i["id"]: copy.deepcopy(i["T"]) for i in self.doc.instances}}

    def _move_to(self, edit):
        d = getattr(self, "_drag", None)
        if d is None:
            raise EngineError("move_to without move_begin")
        M = np.asarray(_need(edit, "T"), float)
        if M.shape != (4, 4):
            raise EngineError("T must be a 4x4 matrix")
        for i in self.doc.instances:
            if i["id"] in d["T"]:
                i["T"] = copy.deepcopy(d["T"][i["id"]])
        inst = self.doc.instance(d["inst"])
        inst["T"] = A.orthonormalize(M @ np.array(d["T"][d["inst"]], float)).tolist()
        self.doc.solve(keep=d["inst"])
        self.doc.notify()

    def _move_end(self, edit):
        self._drag_close()

    # -- mates from a mate dialog (I10.5): the edits its accept sends, checked here because any client may send them
    def _checked_mate(self, edit):
        m = _need(edit, "mate")
        if not isinstance(m, dict) or not isinstance(m.get("id"), str) or m.get("type") not in A.MATE_TYPES:
            raise EngineError("mate: must be a mate dict with an id and a mate type")
        m = copy.deepcopy(m)
        for side in ("a", "b"):
            c = m.get(side)
            fr = c.get("frame") if isinstance(c, dict) else None
            if not isinstance(fr, dict) or not all(isinstance(fr.get(k), (list, tuple)) and len(fr[k]) == 3
                                                   for k in ("o", "z", "x")):
                raise EngineError(f"mate.{side}: must be a connector with a frame")
            self._instance(c.get("inst"))
        if m["a"]["inst"] == m["b"]["inst"]:
            raise EngineError(MS.SAME_PART)
        m.setdefault("name", A.MATE_TYPES[m["type"]][0])
        return m

    def _add_mate(self, edit):
        m = self._checked_mate(edit)
        if self.doc.mate(m["id"]) is not None or self.doc.instance(m["id"]) is not None:
            raise EngineError(f"mate.id: {m['id']!r} is in use")
        self.doc.add_mate(m)

    def _replace_mate(self, edit):
        m = self._checked_mate(edit)
        self._mate(m["id"])
        self.doc.replace_mate(m)

    # -- mate dialog sessions (I10.5): {dialog id: MateSession}, kept here, never in the saved data
    def mate_dialog(self, verb, dialog, arg=None):
        sessions = self.__dict__.setdefault("mate_sessions", {})
        if verb == "close":
            sessions.pop(dialog, None)
            return None
        self.sync()
        if verb == "open":
            if dialog in sessions:
                raise EngineError(f"mate dialog {dialog!r} is already open")
            try:
                s = MS.MateSession.start(self.doc, arg.get("type"), arg.get("mate"),
                                         [tuple(p) for p in arg.get("picks") or ()])
            except MS.MateSessionError as e:
                raise EngineError(str(e)) from e
            sessions[dialog] = s
            return {**s.view(), **s.preview_detached()}
        s = sessions.get(dialog)
        if s is None:
            raise EngineError(f"no mate dialog {dialog!r} on this tab")
        if verb == "update":
            try:
                refused = s.apply(arg)
            except MS.MateSessionError as e:
                raise EngineError(str(e)) from e
            got = s.preview_detached()
            if refused:
                got["error"] = refused
            return {**s.view(), **got}
        if verb == "result":
            if not s.is_new and self.doc.mate(s.mate["id"]) is None:
                raise EngineError(f"the mate {s.mate.get('name')!r} was deleted")
            try:
                m = s.result()
            except MS.MateSessionError as e:
                raise EngineError(str(e)) from e
            return {"op": "add_mate" if s.is_new else "replace_mate", "mate": plain(m)}
        raise EngineError(f"unknown mate dialog request {verb!r}")


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
        self.document_id = None        # the Document these tabs belong to (`use_document`)
        self.cross = None              # resolves a source naming another Document, pinned to a Version
        self.by_tab_id = False         # a tab was opened by data with no path: tab-id sources match by id only
        self._tabs: dict[str, _Tab] = {}
        self._counter = 0

    def use_document(self, document_id, registry=None):
        """Say which Document the tabs belong to: a source naming another Document is then never matched to a
        tab, and is read at its pinned Version through `registry` (the rule of ProcessEngine.use_document)."""
        self.document_id = document_id
        self.links.own_id = document_id
        self.cross = links.LinkResolver(registry=registry) if registry is not None else None
        if self.cross is not None:
            self.cross.own_id = document_id

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
        with open(path, "rb") as fh:
            data = fh.read()
        return self.open_tab_data(kind, data, tab, path=path)

    def open_tab_data(self, kind, data, tab=None, name=None, blobs=None, path=None):
        read_data(kind, data, path)
        if tab is not None and tab in self._tabs:
            raise EngineError(f"tab id {tab!r} is already open")
        if path is None:
            self.by_tab_id = True
        with self.inputs.recording() as used:
            if kind == "studio":
                doc = PartStudioDoc.from_data(data, path=path, blobs=blobs)
                doc.var_inputs = self.inputs.variables
                doc.link_inputs = self.inputs.studio
                if any(links.feature_source(f) is not None for f in doc.features):
                    doc.rebuild()            # the load ran without the resolver
            elif kind == "variables":
                doc = VariableStudioDoc.from_data(data, path=path, blobs=blobs)
            elif kind == "assembly":
                doc = A.AssemblyDoc.from_data(data, path=path, resolver=self.inputs.part, blobs=blobs)
            else:
                doc = D.DrawingDoc.from_data(data, path=path, resolver=self.inputs.drawing_shape, blobs=blobs)
        if name:
            doc.name = str(name)
        tab = self._register(kind, doc, tab, used)
        self._tabs[tab].blobs = blobs
        self.inputs.commit(tab, used)
        return tab

    def tab_data(self, tab, blobs=None):
        t = self._get(tab)
        data, named = t.data()
        sink = blobs if blobs is not None else t.blobs
        if sink is None and named and t.path:
            sink = blobstore.store_for(t.path)
        put_blobs(sink, named, tab)
        return data

    def new_tab(self, kind, name=None, tab=None, sources=(), blobs=None):
        if kind not in KINDS:
            raise EngineError(f"unknown tab kind {kind!r}")
        if kind == "studio":
            doc = PartStudioDoc()
            doc.var_inputs = self.inputs.variables
            doc.link_inputs = self.inputs.studio
            doc.blob_view = blobs
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
        tab = self._register(kind, doc, tab)
        self._tabs[tab].blobs = blobs
        return tab

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
        if t is None and path and not (tab and self.by_tab_id):     # a tab id names it: never a path
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
        return links.Edge(u.kind, keys, package_digest(u.kind, live_package(u.kind, u.doc, self.encoder, keys), keys))

    def _digest_now(self, tab, edge):
        u = self._tabs.get(tab)
        if u is None:
            return edge.digest                    # closed: its consumers keep the version they have
        u.sync()
        return package_digest(u.kind, live_package(u.kind, u.doc, self.encoder, edge.keys), edge.keys)

    def _label(self, tab):
        t = self._tabs.get(tab)
        return f"{tab} “{t.doc.name}”" if t is not None else str(tab)

    def rebuild_dependents(self, tab):
        self._get(tab)
        return propagate(self.links.graph, tab, self._digest_now, lambda x: self._tabs[x].rebuild(), self._label)

    def outputs(self, tab):
        return self._get(tab).outputs()

    def refs(self, tab, picks, base=None):
        return self._get(tab).refs(picks, base)

    def base_outputs(self, tab, feature):
        return self._get(tab).base_outputs(feature)

    def sketch_frame(self, tab, plane):
        return self._get(tab).sketch_frame(plane)

    def project_edge(self, tab, plane, pick):
        return self._get(tab).project_edge(plane, pick)

    def feature(self, tab, feature):
        return self._get(tab).feature_info(feature)

    def mate_dialog_open(self, tab, dialog, spec):
        return self._get(tab).mate_dialog("open", dialog, spec)

    def mate_dialog_update(self, tab, dialog, changes):
        return self._get(tab).mate_dialog("update", dialog, changes)

    def mate_dialog_result(self, tab, dialog):
        return self._get(tab).mate_dialog("result", dialog)

    def mate_dialog_close(self, tab, dialog):
        return self._get(tab).mate_dialog("close", dialog)

    def start_feature(self, tab, type, picks):
        return self._get(tab).start_feature(type, picks)

    def startable(self, tab, picks):
        return self._get(tab).startable(picks)

    def submit_preview(self, tab, feature, type, params):
        """Built here and now (the in-process executor has no queue to supersede in)."""
        t = self._get(tab)
        got = t.preview(feature, type, params)
        t.preview_count = n = getattr(t, "preview_count", 0) + 1
        fut = Future()
        fut.set_result({"preview": n, **got})
        return n, fut
