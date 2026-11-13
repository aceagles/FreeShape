"""Document: the ordered feature list, rollback bar, undo/redo, save/load, and a cached rebuild."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import traceback

import numpy as np

from . import blobstore
from . import kernel as K
from . import migrations as M
from .features import (RUNNERS, FEATURE_LABELS, ModelState, PartMap, default_params, Part, prepare,
                       carry_face_colors)
from . import pyfeature   # registers the bundled Python-feature plugins in any process that rebuilds


def _rewrite_part_ids(x, alias):
    """Replace every occurrence of an old-syntax part id (as a dict key or a string value) with its new one,
    recursively. `alias` only ever holds actual <fid>:<counter> ids, so this can't misfire on unrelated
    strings (feature/sketch ids never contain ':')."""
    if isinstance(x, str):
        return alias.get(x, x)
    if isinstance(x, dict):
        return {(alias.get(k, k) if isinstance(k, str) else k): _rewrite_part_ids(v, alias) for k, v in x.items()}
    if isinstance(x, list):
        return [_rewrite_part_ids(v, alias) for v in x]
    return x


def _migrate_part_ids(data):
    """FILE_VERSION 1 -> 2 (I1.4): part ids become <feature id>/<output index> instead of a replay-wide
    <feature id>:<counter>, so inserting an upstream feature no longer renumbers later parts that come
    after it. Replays the feature list once (the same way Document.compute does), rewriting every
    old-style id a feature's own params name as soon as its new id is known -- so later features still
    find their inputs -- and records the whole old -> new table as "part_aliases", so a .fasm/.fdwg that
    still names an old id can be translated by LinkResolver (see links.py) until it's next saved."""
    alias = dict(data.get("part_aliases") or {})
    state = ModelState()
    features = []
    for f in data.get("features", []):
        f = _rewrite_part_ids(f, alias)
        if not f.get("suppressed"):
            state = state.copy()
            try:
                RUNNERS[f["type"]](state, prepare(f, state))
            except Exception:
                pass
            else:
                for new_id, old_id in state.old_ids.items():
                    alias[old_id] = new_id
        features.append(f)
    data["features"] = features
    data["overrides"] = _rewrite_part_ids(data.get("overrides", {}), alias)
    data["hidden"] = _rewrite_part_ids(data.get("hidden", []), alias)
    data["part_aliases"] = alias
    return data


M.register("document", _migrate_part_ids)


class Document:
    kind = "studio"

    def __init__(self):
        import uuid
        self.uid = uuid.uuid4().hex                 # lets assemblies find this Part Studio again
        self.name = "Part Studio 1"
        self.features: list[dict] = []
        self.rollback: int | None = None          # number of active features; None = all
        self.part_overrides: dict = {}            # part id -> {"name":..., "color":...}
        self.part_aliases: dict = {}               # old (pre-I1.4) part id -> its new id, from a v1 load
        # Python feature id -> its stored last build {"plugin", "parts": [{"id", "brep", "names"}]} (from the
        # file; hostile data, see pyfeature.host.restore). Shown when the feature's plugin is not installed.
        self.python_builds: dict = {}
        self._blob_bytes: dict = {}                 # blob key -> bytes read while restoring a stored build
        self._build_memo: dict = {}                 # feature id -> (state, entry, blobs) of its last record
        self.hidden_parts: set = set()
        self.hidden_features: set = set()         # sketches / planes hidden in the view
        self.path = None
        self.dirty = False
        self._undo: list[str] = []
        self._redo: list[str] = []
        self._cache: dict[str, ModelState] = {}
        self.errors: dict[str, str] = {}
        self.state = ModelState()
        self._counter = 0
        self.listeners = []

    # ------------------------------------------------------------------ ids / names
    def new_id(self, prefix="F"):
        self._counter += 1
        ids = {f["id"] for f in self.features}
        while f"{prefix}{self._counter}" in ids:
            self._counter += 1
        return f"{prefix}{self._counter}"

    def next_name(self, ftype):
        label = FEATURE_LABELS.get(ftype, ftype.title())
        n = 1
        names = {f["name"] for f in self.features}
        while f"{label} {n}" in names:
            n += 1
        return f"{label} {n}"

    def make_feature(self, ftype, state=None):
        if pyfeature.is_python_type(ftype):
            info = pyfeature.plugin_for(ftype)
            if info is None:
                raise KeyError(ftype)
            return {"id": self.new_id(), "type": ftype, "name": self.next_name(ftype), "suppressed": False,
                    "params": pyfeature.default_params(ftype), "plugin": {"id": info.id, "version": info.version}}
        return {"id": self.new_id(), "type": ftype, "name": self.next_name(ftype),
                "suppressed": False, "params": default_params(ftype, state or self.state)}

    # ------------------------------------------------------------------ undo / redo
    def _snapshot(self) -> str:
        return json.dumps({"features": self.features, "rollback": self.rollback,
                           "overrides": self.part_overrides, "hidden": sorted(self.hidden_parts),
                           "hidden_features": sorted(self.hidden_features)})

    def _restore(self, snap: str):
        d = json.loads(snap)
        self.features = d["features"]
        self.rollback = d["rollback"]
        self.part_overrides = d["overrides"]
        self.hidden_parts = set(d["hidden"])
        self.hidden_features = set(d.get("hidden_features", []))

    def checkpoint(self):
        """Call before any user-visible change."""
        self._undo.append(self._snapshot())
        if len(self._undo) > 200:
            self._undo.pop(0)
        self._redo.clear()
        self.dirty = True

    def can_undo(self):
        return bool(self._undo)

    def can_redo(self):
        return bool(self._redo)

    def undo(self):
        if self._undo:
            self._redo.append(self._snapshot())
            self._restore(self._undo.pop())
            self.dirty = True
            self.rebuild()

    def redo(self):
        if self._redo:
            self._undo.append(self._snapshot())
            self._restore(self._redo.pop())
            self.dirty = True
            self.rebuild()

    # ------------------------------------------------------------------ editing
    def active_count(self):
        return len(self.features) if self.rollback is None else min(self.rollback, len(self.features))

    def insert_index(self):
        return self.active_count()

    def add_feature(self, feat, index=None):
        self.checkpoint()
        if index is None:
            index = self.insert_index()
        self.features.insert(index, feat)
        if self.rollback is not None:
            self.rollback = index + 1
            if self.rollback >= len(self.features):
                self.rollback = None
        self.rebuild()

    def replace_feature(self, feat):
        self.checkpoint()
        for i, f in enumerate(self.features):
            if f["id"] == feat["id"]:
                self.features[i] = feat
                break
        self.rebuild()

    @staticmethod
    def _sketches_used(feat):
        """Ids of the sketches a feature uses (its regions, profiles, path, hole positions …)."""
        out = set()

        def walk(x):
            if isinstance(x, dict):
                for k, v in x.items():
                    if k == "sketch" and isinstance(v, str):
                        out.add(v)
                    else:
                        walk(v)
            elif isinstance(x, (list, tuple)):
                for v in x:
                    walk(v)
        if feat.get("type") != "sketch":
            walk(feat.get("params", {}))
        return out

    def delete_features(self, ids):
        self.checkpoint()
        idx = [i for i, f in enumerate(self.features) if f["id"] in ids]
        freed = set()
        for f in self.features:
            if f["id"] in ids:
                freed |= self._sketches_used(f)
        self.features = [f for f in self.features if f["id"] not in ids]
        # sketches were hidden when a feature used them: show them again once nothing uses them any more
        still = set()
        for f in self.features:
            still |= self._sketches_used(f)
        self.hidden_features -= (freed - still)
        if self.rollback is not None:
            self.rollback -= sum(1 for i in idx if i < self.rollback)
            if self.rollback >= len(self.features):
                self.rollback = None
        self.rebuild()

    def set_suppressed(self, fid, value):
        self.checkpoint()
        for f in self.features:
            if f["id"] == fid:
                f["suppressed"] = value
        self.rebuild()

    def acknowledge_plugin_version(self, fid):
        """Record the installed plugin version in a Python feature (clears its version notice). Undoable.
        Returns False when there is nothing to change."""
        f = self.feature(fid)
        info = pyfeature.plugin_for(f.get("type")) if f else None
        if info is None or f.get("plugin") == {"id": info.id, "version": info.version}:
            return False
        new = copy.deepcopy(f)
        new["plugin"] = {"id": info.id, "version": info.version}
        self.replace_feature(new)
        return True

    def rename_feature(self, fid, name):
        self.checkpoint()
        for f in self.features:
            if f["id"] == fid:
                f["name"] = name
        self.notify()

    def move_feature(self, fid, new_index):
        self.checkpoint()
        i = next(i for i, f in enumerate(self.features) if f["id"] == fid)
        f = self.features.pop(i)
        self.features.insert(min(new_index, len(self.features)), f)
        self.rebuild()

    def set_rollback(self, n):
        n = None if n is None or n >= len(self.features) else max(0, n)
        if n != self.rollback:
            self.rollback = n
            self.rebuild()

    def feature(self, fid):
        return next((f for f in self.features if f["id"] == fid), None)

    def feature_index(self, fid):
        return next((i for i, f in enumerate(self.features) if f["id"] == fid), None)

    def set_part_override(self, pid, **kw):
        self.checkpoint()
        self.part_overrides.setdefault(pid, {}).update(kw)
        self._apply_overrides(self.state)
        self.notify()

    def toggle_part_hidden(self, pid):
        self.hidden_parts ^= {pid}
        self.notify()

    def toggle_feature_hidden(self, fid):
        self.hidden_features ^= {fid}
        self.notify()

    # ------------------------------------------------------------------ rebuild
    var_inputs = None   # (source dict) -> {name: value} of the Variable Studio it names, or None; the engine sets it

    def _ref_inputs(self, f):
        """The variables a "variable_studio_ref" feature consumes (None if its link finds nothing)."""
        src = (f.get("params") or {}).get("source")
        vals = self.var_inputs(src) if self.var_inputs is not None and isinstance(src, dict) else None
        return None if vals is None else {str(k): float(v) for k, v in vals.items()}

    def _with_consumed_inputs(self, h, f):
        """Cache-key step for cross-tab inputs: mixes the variables a "variable_studio_ref" feature reads into
        the running hash `h`, so a changed input misses from that feature on. Returns the feature to run
        (with the values as params["resolved"]); any other feature comes back unchanged."""
        if f.get("type") != "variable_studio_ref" or f.get("suppressed"):
            return f
        ref = self._ref_inputs(f)
        h.update(json.dumps(ref, sort_keys=True).encode())
        return dict(f, params=dict(f["params"], resolved=ref))

    @staticmethod
    def _key(prefix_json: str) -> str:
        return hashlib.sha1(prefix_json.encode()).hexdigest()

    def compute(self, features, upto=None, errors=None):
        """Replay features (with caching). Returns the resulting ModelState."""
        state = ModelState()
        h = hashlib.sha1()
        n = len(features) if upto is None else upto
        new_cache = {}
        for f in features[:n]:
            h.update(json.dumps(f, sort_keys=True).encode())
            if pyfeature.is_python_type(f.get("type")):
                h.update(self._python_key(f).encode())
            f = self._with_consumed_inputs(h, f)
            key = h.hexdigest()
            if key in self._cache:
                state, err = self._cache[key]
            else:
                state = state.copy()
                err = None
                if not f.get("suppressed"):
                    before = dict(state.parts)
                    try:
                        if pyfeature.is_python_type(f.get("type")):
                            # the type is only a registry key: never imported or evaluated. host.run loads
                            # the user plugins once if it is unregistered, then reports the missing plugin.
                            # It evaluates its own expressions, so prepare() is skipped. Still unregistered
                            # with a stored build: lock on that build (I4.7).
                            entry = self._stored_build(f)
                            if entry is not None:
                                self._blob_bytes.update(pyfeature.host.restore(state, f, entry, self._blob_store()))
                            else:
                                pyfeature.host.run(state, f)
                        else:
                            RUNNERS[f["type"]](state, prepare(f, state))
                        for pid, part in list(state.parts.items()):
                            old = before.get(pid) or before.get(pid.split(".")[0])
                            if old is not None and old.face_colors and part.shape is not old.shape \
                                    and part.face_colors is None:
                                state.parts[pid] = Part(part.id, part.name, part.shape, part.color,
                                                        carry_face_colors(old, part.shape))
                    except K.KernelError as e:
                        err = str(e)
                    except Exception as e:  # never let one feature kill the model
                        traceback.print_exc()
                        err = f"{type(e).__name__}: {e}"
            new_cache[key] = (state, err)
            if errors is not None and err:
                errors[f["id"]] = err
        self._cache.update(new_cache)
        if len(self._cache) > 400:
            self._cache = new_cache
        return state

    def _stored_build(self, f):
        """The stored build to lock `f` on: only when its plugin is (still) not installed."""
        if pyfeature.get(f.get("type")) is not None:
            return None
        return self.python_builds.get(f.get("id"))

    def _blob_store(self):
        return blobstore.store_for(self.path) if self.path else None

    def _python_key(self, f):
        """What a Python feature's result depends on besides its JSON: the plugin that builds it (so
        registering or removing a plugin rebuilds), or, while it is missing, the stored build it is locked on."""
        if pyfeature.get(f.get("type")) is None:
            pyfeature.load_user_plugins()
        info = pyfeature.plugin_for(f.get("type"))
        if info is not None:
            return json.dumps(["plugin", info.id, info.version, info.path])
        entry = self.python_builds.get(f.get("id"))
        return json.dumps(["missing", entry, self.path], sort_keys=True, default=str)

    def locked_builds(self) -> dict:
        """{feature id: stored-build entry} of the Python features shown on their stored last build: the
        plugin is not installed, there is a stored build, and the last rebuild reported no error for it."""
        out = {}
        for f in self.features:
            if pyfeature.is_python_type(f.get("type")) and not f.get("suppressed") \
                    and f["id"] not in self.errors:
                entry = self._stored_build(f)
                if entry is not None:
                    out[f["id"]] = entry
        return out

    def notices(self) -> dict:
        """{feature id: text}: pyfeature.notices for this Part Studio, locked features included."""
        return pyfeature.notices(self.features, self.locked_builds())

    def rebuild(self):
        errors = {}
        self.state = self.compute(self.features, self.active_count(), errors).copy()
        self.errors = errors
        self._apply_overrides(self.state)
        self.notify()

    def preview(self, feat, index, extra=()):
        """Model state with `feat` inserted (or replacing its old self) at index. `extra` are not-yet-saved
        features (e.g. a plane made for a new sketch) that go in just before it."""
        feats = [f for f in self.features if f["id"] != feat["id"]]
        index = min(index, len(feats))
        feats = feats[:index] + list(extra) + [feat]
        errors = {}
        st = self.compute(feats, None, errors).copy()
        self._apply_overrides(st)
        return st, errors.get(feat["id"])

    def state_before(self, index):
        st = self.compute(self.features, index).copy()
        self._apply_overrides(st)
        return st

    def _apply_overrides(self, state):
        if self.part_aliases and not isinstance(state.parts, PartMap):
            state.parts = PartMap(state.parts, aliases=self.part_aliases)
        for pid, ov in self.part_overrides.items():
            p = state.parts.get(pid)
            if p:
                state.parts[pid] = Part(p.id, ov.get("name", p.name), p.shape, tuple(ov.get("color", p.color)),
                                        None if "color" in ov else p.face_colors)

    def notify(self):
        for cb in list(self.listeners):
            cb()

    # ------------------------------------------------------------------ files
    def _python_builds(self):
        """({feature id: entry}, {blob key: bytes}) of the stored last build of every Python feature in the
        full feature list: each one that builds without error now, and each one locked on its stored build
        (kept as it is). A feature's parts are those with id `<fid>/...` in the (cached) state right after it."""
        builds, blobs = {}, {}
        for i, f in enumerate(self.features):
            if not pyfeature.is_python_type(f.get("type")) or f.get("suppressed"):
                continue
            fid = f["id"]
            errors = {}
            state = self.compute(self.features, i + 1, errors)
            if fid in errors:
                continue
            entry = self._stored_build(f)
            if entry is not None:            # locked: keep the entry, and its bytes for a save elsewhere
                builds[fid] = entry
                for rec in entry.get("parts", []):
                    for k in (rec.get("brep"), rec.get("names")):
                        if k in self._blob_bytes:
                            blobs[k] = self._blob_bytes[k]
                continue
            info = pyfeature.plugin_for(f.get("type"))
            if info is None:
                continue
            memo = self._build_memo.get(fid)
            if memo is None or memo[0] is not state:
                rec = pyfeature.host.build_record(state, fid, {"id": info.id, "version": info.version})
                memo = self._build_memo[fid] = (state, *rec)
            if memo[1]["parts"]:
                builds[fid] = memo[1]
                blobs.update(memo[2])
        return builds, blobs

    def to_json(self):
        data = {"format": "freeshape", "version": M.latest_version("document"), "name": self.name,
                "uid": self.uid, "features": self.features, "rollback": self.rollback,
                "overrides": self.part_overrides, "hidden": sorted(self.hidden_parts),
                "hidden_features": sorted(self.hidden_features),
                "part_aliases": self.part_aliases}
        builds, _ = self._python_builds()
        if builds:
            data["python_builds"] = builds
        return json.dumps(data, indent=1)

    def write_blobs(self, path=None):
        """Put the stored last build of every Python feature into the blob store beside `path` (the tab
        file about to be written), before the JSON that names the keys. Returns the builds."""
        builds, blobs = self._python_builds()
        if blobs:
            store = blobstore.store_for(path or self.path)
            for data in blobs.values():
                store.put(data)
        self.python_builds = builds
        return builds

    def save(self, path=None):
        path = path or self.path
        self.write_blobs(path)
        txt = self.to_json()
        tmp = str(path) + ".tmp"
        try:
            with open(tmp, "w") as fh:
                fh.write(txt)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        self.path = path
        self.dirty = False

    @staticmethod
    def load(path) -> "Document":
        with open(path) as fh:
            d = json.load(fh)
        fmt = d.get("format") if isinstance(d, dict) else None
        kinds = {"freeshape-assembly": "an Assembly", "freeshape-drawing": "a Drawing",
                 "freeshape-variables": "a Variable Studio"}
        if fmt in kinds:
            raise K.KernelError(f"This file is {kinds[fmt]}, not a FreeShape Part Studio")
        if fmt is not None and fmt != "freeshape":
            raise K.KernelError("Not a FreeShape Part Studio")
        if not isinstance(d, dict) or not isinstance(d.get("features"), list):
            raise K.KernelError("Not a FreeShape Part Studio (no features list)")
        d = M.migrate("document", d, path)
        doc = Document()
        doc.name = d.get("name", "Part Studio 1")
        doc.uid = d.get("uid") or doc.uid
        doc.features = d["features"]
        doc.rollback = d.get("rollback")
        doc.part_overrides = d.get("overrides", {})
        doc.part_aliases = d.get("part_aliases", {})
        doc.hidden_parts = set(d.get("hidden", []))
        doc.hidden_features = set(d.get("hidden_features", []))
        builds = d.get("python_builds")
        doc.python_builds = {k: v for k, v in builds.items() if isinstance(k, str)} if isinstance(builds, dict) else {}
        doc.path = path
        nums = [int("".join(c for c in f["id"] if c.isdigit()) or 0) for f in doc.features]
        doc._counter = max(nums + [0])
        doc.rebuild()
        return doc

    def visible_parts(self):
        return [p for p in self.state.parts.values() if p.id not in self.hidden_parts]

    def export(self, path, part_ids=None, options=None):
        parts = [p for p in self.state.parts.values() if part_ids is None or p.id in part_ids]
        if not parts:
            raise K.KernelError("Nothing to export")
        shapes = [p.shape for p in parts]
        if str(path).lower().endswith((".stl",)):
            K.export_stl(shapes, path, **(options or {}))
        elif str(path).lower().endswith((".3mf", ".obj")):
            from . import meshexport as ME
            ME.export_parts([(p.shape, p.name, p.color) for p in parts], path, **(options or {}))
        else:
            return K.export_step_named([(p.shape, np.eye(4), p.name, p.color) for p in parts], path, self.name)
