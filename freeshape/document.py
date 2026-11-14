"""PartStudioDoc: the ordered feature list, rollback bar, undo/redo, save/load, and a cached rebuild."""
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
                       carry_face_colors, context_blobs, context_listing, check_reference_parts, REFERENCE_ONLY,
                       saved_copy_key, saved_copy_record, saved_copy_notice)
from .expr import parse_number
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
    after it. Replays the feature list once (the same way PartStudioDoc.compute does), rewriting every
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


def _import_params_with_blob(params, key):
    """An "import" feature's params with its inline base64 `data` replaced, in place, by `blob`: its key."""
    return {("blob" if k == "data" else k): (key if k == "data" else v) for k, v in params.items()}


def _move_import_data_to_blobs(data):
    """FILE_VERSION 2 -> 3 (I4.9, ADR 0009): an "import" feature's STEP file moves to the store's blobs. Its
    base64 `params.data` becomes `params.blob`, the sha256 key of the decoded bytes. The bytes wait in
    `data[blobstore.HELD]` for `from_data`, which keeps them until the next save writes them out (see
    assembly._move_geometry_to_blobs). A value that is not base64 is left as it is; the import reports it."""
    for f in data.get("features", []):
        p = f.get("params") if isinstance(f, dict) and f.get("type") == "import" else None
        if isinstance(p, dict) and "blob" not in p:
            key = blobstore.hold_base64(data, p.get("data"))
            if key is not None:
                f["params"] = _import_params_with_blob(p, key)
    return data


M.register("document", _move_import_data_to_blobs)


def _adopt_upstream_2_0(data):
    """FILE_VERSION 3 -> 4 (the upstream 2.0 merge). From this version a Part Studio file may hold upstream's
    feature-list folders ("folders", and "folder" on a feature), configurations ("configs"), saved copies of
    plugin features ("plugin_copies") and the upstream feature types, which a build before the merge would
    drop or fail on: the version says so. One conversion: an extrude saved with this fork's up-to end
    (end "upto", target in "upto") takes upstream's shape (features.legacy_up_to). Upstream's in-file
    "versions" / "version_blobs" and "derived_copies" are not read: Versions are git tags of the Document
    (ADR 0001) and a Derived feature reads its source through links.py."""
    from .features import legacy_up_to
    for f in data.get("features", []):
        p = f.get("params") if isinstance(f, dict) and f.get("type") == "extrude" else None
        if isinstance(p, dict) and p.get("end") == "upto":
            f["params"] = legacy_up_to(p)
    return data


M.register("document", _adopt_upstream_2_0)


CONTEXT_CURRENT, CONTEXT_OUT_OF_DATE, CONTEXT_ASSEMBLY_MISSING = "current", "out of date", "assembly missing"


def context_status(doc, fid, asm):
    """Whether the "context" feature `fid` of studio `doc` still matches the Assembly `asm` (the live
    AssemblyDoc, or None): "current", "out of date" or "assembly missing" (ADR 0004). Out of date means a
    captured neighbour's hash differs, its position relative to the edited instance moves by more than 1e-6,
    or a neighbour was added or removed. The edited instance's own shape is not compared. A query only:
    nothing is rebuilt, changed or acted on. A capture that cannot be read counts as out of date."""
    import numpy as np
    from .assembly import capture_context
    f = doc.feature(fid)
    if f is None or f.get("type") != "context":
        raise K.KernelError(f"No Context feature {fid}")
    p = f.get("params") or {}
    if asm is None or asm.instance(p.get("instance")) is None:
        return CONTEXT_ASSEMBLY_MISSING
    try:
        stored = {n["id"]: n for n in context_listing(doc._blob_get, p.get("capture"))["neighbours"]}
    except (blobstore.BlobError, ValueError):
        return CONTEXT_OUT_OF_DATE
    now = {n["id"]: n for n in capture_context(asm, p["instance"])["neighbours"]}
    if set(now) != set(stored):
        return CONTEXT_OUT_OF_DATE
    for k, n in now.items():
        if n["hash"] != stored[k]["hash"] or not np.allclose(n["T"], stored[k]["T"], rtol=0, atol=1e-6):
            return CONTEXT_OUT_OF_DATE
    return CONTEXT_CURRENT


def update_context(doc, fid, asm):
    """Recapture the "context" feature `fid` from the Assembly `asm`: new blobs, a new capture key, one undoable
    param edit that rebuilds the studio. Raises KernelError when the Assembly or the instance is missing."""
    from .assembly import capture_context
    f = doc.feature(fid)
    if f is None or f.get("type") != "context":
        raise K.KernelError(f"No Context feature {fid}")
    p = f.get("params") or {}
    if asm is None or asm.instance(p.get("instance")) is None:
        raise K.KernelError("Can't update the Context: its Assembly or instance is missing")
    key = doc.store_context(capture_context(asm, p["instance"]))
    new = copy.deepcopy(f)
    new["params"]["capture"] = key
    doc.replace_feature(new)


def config_row(configs, name=None):
    """The row of a configuration table ({"rows", "active"}) named `name`, the active row for None; None when the
    table has no rows or no row of that name."""
    rows = (configs or {}).get("rows", [])
    if not rows:
        return None
    if name is None:
        return rows[min(max(int(configs.get("active", 0)), 0), len(rows) - 1)]
    return next((r for r in rows if r["name"] == name), None)


class PartStudioDoc:
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
        self._import_keys: dict = {}                # id(base64 text) -> (text, key) of an inline import; memory only
        self.hidden_parts: set = set()
        self.hidden_features: set = set()         # sketches / planes hidden in the view
        # feature-list folders: id -> {"name": str, "open": bool}; a feature in one has f["folder"] = that id.
        # A folder groups a run of features that sit next to each other in the list (like Onshape).
        self.folders: dict = {}
        # configurations: named rows that set variables (by name) and suppress features (by id), like Onshape's
        # configuration table: {"rows": [{"name", "vars": {name: expr}, "suppress": [fid]}], "active": index}
        self.configs: dict = {"rows": [], "active": 0}
        self._on_copy: dict = {}                  # derived feature id -> True while its last build used the saved copy
        self.path = None
        self.blob_view = None     # this tab's blobs in a Document store (docstore.DocumentStore.blobs), for no path
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
                           "hidden_features": sorted(self.hidden_features), "folders": self.folders,
                           "configs": self.configs})

    def _restore(self, snap: str):
        d = json.loads(snap)
        self.features = d["features"]
        self.rollback = d["rollback"]
        self.part_overrides = d["overrides"]
        self.hidden_parts = set(d["hidden"])
        self.hidden_features = set(d.get("hidden_features", []))
        self.folders = d.get("folders", {})
        self.configs = d.get("configs", {"rows": [], "active": 0})

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

    def _hold_import(self, feat):
        """An "import" feature made with its STEP file inline (base64 `params.data`, as the Import command makes
        it) is turned, in place and before it first builds, into the form the file keeps: `params.blob`, the
        bytes held until the next save (I4.9)."""
        stored = self._file_feature(feat)
        if stored is not feat:
            feat["params"] = stored["params"]
        return feat

    def add_feature(self, feat, index=None):
        self._hold_import(feat)
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
        self._hold_import(feat)
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

    def set_part_colors(self, colors: dict):
        """Give several parts new colours in one undo step. colors: part id -> (r, g, b) in 0..1."""
        if not colors:
            return
        self.checkpoint()
        for pid, c in colors.items():
            self.part_overrides.setdefault(pid, {})["color"] = [float(x) for x in c]
        self._apply_overrides(self.state)
        self.notify()

    def toggle_part_hidden(self, pid):
        self.hidden_parts ^= {pid}
        self.notify()

    def toggle_feature_hidden(self, fid):
        self.hidden_features ^= {fid}
        self.notify()

    def set_parts_hidden(self, pids, hidden: bool):
        """Hide (or show) several parts at once — one redraw for the whole selection."""
        pids = set(pids)
        self.hidden_parts = (self.hidden_parts | pids) if hidden else (self.hidden_parts - pids)
        self.notify()

    def set_features_hidden(self, fids, hidden: bool):
        """Hide (or show) several sketches / planes at once."""
        fids = set(fids)
        self.hidden_features = (self.hidden_features | fids) if hidden else (self.hidden_features - fids)
        self.notify()

    # ------------------------------------------------------------------ rebuild
    var_inputs = None   # (source dict) -> {name: value} of the Variable Studio it names, or None; the engine sets it

    link_inputs = None  # (source dict) -> links.StudioPackage of the Part Studio it names; raises KernelError if none

    def _ref_inputs(self, f):
        """The variables a "variable_studio_ref" feature consumes (None if its link finds nothing)."""
        src = (f.get("params") or {}).get("source")
        vals = self.var_inputs(src) if self.var_inputs is not None and isinstance(src, dict) else None
        names = (f.get("params") or {}).get("names")
        if vals is not None and names is not None:     # a subset: unticked names never reach the cache key
            vals = {k: v for k, v in vals.items() if k in names}
        return None if vals is None else {str(k): float(v) for k, v in vals.items()}

    def variable_table(self):
        """One row per variable definition in history order (suppressed and rolled-back features skipped):
        {"name", "value", "feature" (id of the defining feature), "overridden"}. A definition is overridden
        when a later "variable" feature or Variable Studio reference defines the same name; the last one is
        the value the model uses."""
        rows, running = [], {}
        for f in self.features[:self.active_count()]:
            if f.get("suppressed"):
                continue
            p = f.get("params") or {}
            if f.get("type") == "variable":
                name = str(p.get("name", "")).strip().lstrip("#")
                val = parse_number(p.get("expr", ""), variables=running)
                defs = {name: val} if name and val is not None else {}
            elif f.get("type") == "variable_studio_ref":
                defs = self._ref_inputs(f) or {}
                if p.get("names") is not None:
                    defs = {n: defs[n] for n in p["names"] if n in defs}
            else:
                continue
            for name, val in defs.items():
                for r in rows:
                    if r["name"] == name:
                        r["overridden"] = True
                rows.append({"name": name, "value": val, "feature": f["id"], "overridden": False})
                running[name] = val
        return rows

    def _with_consumed_inputs(self, h, f):
        """Cache-key step for cross-tab inputs: mixes what a feature of links.SOURCE_FEATURES reads into the
        running hash `h`, so a changed input misses from that feature on. A "variable_studio_ref" reads the
        variables of its Variable Studio; any other such feature reads the StudioPackage of its source, and its
        digest for the part it names goes into the key. Returns the feature to run (with what it read as
        params["resolved"]; None if the link found nothing); any other feature comes back unchanged."""
        from . import links
        if f.get("type") == "context" and not f.get("suppressed"):
            key = (f.get("params") or {}).get("capture")
            h.update(b"1" if self._has_blob(key) else b"0")      # a capture blob that appears on a save rebuilds
            return dict(f, params=dict(f["params"], resolved=self._blob_get))
        if f.get("type") == "import" and not f.get("suppressed") and (f.get("params") or {}).get("blob"):
            h.update(b"1" if self._has_blob(f["params"]["blob"]) else b"0")   # the STEP file's blob (I4.9)
            return dict(f, params=dict(f["params"], resolved=self._blob_get))
        if f.get("type") not in links.SOURCE_FEATURES or f.get("suppressed"):
            return f
        if f.get("type") == "variable_studio_ref":
            ref = self._ref_inputs(f)
            h.update(json.dumps(ref, sort_keys=True).encode())
            return dict(f, params=dict(f["params"], resolved=ref))
        src = links.feature_source(f)
        pkg = None
        if src is not None and self.link_inputs is not None:
            try:
                pkg = self.link_inputs(src)
            except K.KernelError:
                pass
        h.update(json.dumps(None if pkg is None else pkg.digest_for(src.get("part"))).encode())
        entry = self._saved_copy(f) if pkg is None and f.get("type") == "derived" else None
        if f.get("type") == "derived":
            self._on_copy[f["id"]] = entry is not None
        if entry is not None:       # the source is missing: build from the copy saved with the file (I1.3)
            h.update(json.dumps(entry, sort_keys=True).encode())
            return dict(f, params=dict(f["params"], resolved=None, saved=entry, saved_get=self._blob_get))
        return dict(f, params=dict(f["params"], resolved=pkg))

    def _saved_copy(self, f):
        """The saved copy of the derived feature `f` (its entry in python_builds) when it was made for the
        feature as it is now -- same source (configuration included), parts and placement -- else None."""
        entry = self.python_builds.get(f.get("id"))
        if isinstance(entry, dict) and entry.get("key") == saved_copy_key(f):
            return entry
        return None

    def _has_blob(self, key):
        if key in self._blob_bytes:
            return True
        store = self._blob_store()
        return bool(store is not None and isinstance(key, str) and store.has(key))

    def _blob_get(self, key):
        """Bytes of one blob: held in memory (a capture not yet saved), else read from the blob store
        beside the tab file (hash-checked) and kept so a save elsewhere can write it. Raises BlobError."""
        data = self._blob_bytes.get(key)
        if data is None:
            store = self._blob_store()
            if store is None:
                raise blobstore.BlobError("the Part Studio has no folder")
            data = self._blob_bytes[key] = store.get(key)
        return data

    def store_context(self, capture):
        """Keep a capture (assembly.capture_context) as blobs, written by the next save; returns the blob key
        for a "context" feature's params["capture"]."""
        key, blobs = context_blobs(capture)
        self._blob_bytes.update(blobs)
        return key

    def _context_blobs(self):
        """{key: bytes} of every blob a context feature names that can be read now (a feature whose blobs are
        gone is red and contributes what is left)."""
        out = {}
        for f in self.features:
            key = (f.get("params") or {}).get("capture") if f.get("type") == "context" else None
            if not key:
                continue
            try:
                out[key] = self._blob_get(key)
                for n in context_listing(self._blob_get, key)["neighbours"]:
                    for k in (n["brep"], n["names"]):
                        out[k] = self._blob_get(k)
            except (blobstore.BlobError, ValueError):
                continue
        return out

    @staticmethod
    def _key(prefix_json: str) -> str:
        return hashlib.sha1(prefix_json.encode()).hexdigest()

    # ------------------------------------------------------------------ configurations
    def config_names(self):
        return [r["name"] for r in self.configs.get("rows", [])]

    def active_config(self):
        rows = self.configs.get("rows", [])
        if not rows:
            return None
        return rows[min(max(int(self.configs.get("active", 0)), 0), len(rows) - 1)]["name"]

    def _config_row(self, name=None):
        return config_row(self.configs, name)

    @staticmethod
    def _configured(f, row):
        """Feature f as the configuration `row` has it: its variable value and whether it's suppressed."""
        if row is None:
            return f
        if f["id"] in row.get("suppress", []) and not f.get("suppressed"):
            f = dict(f, suppressed=True)
        if f.get("type") == "variable":
            name = str(f["params"].get("name", "")).strip().lstrip("#")
            val = row.get("vars", {}).get(name)
            if val not in (None, ""):
                f = dict(f, params=dict(f["params"], expr=str(val)))
        return f

    def set_configs(self, rows, active=0):
        """Replace the configuration table (one undo step) and rebuild in the active configuration."""
        self.checkpoint()
        self.configs = {"rows": [dict(r) for r in rows], "active": int(active) if rows else 0}
        self.rebuild()

    def set_active_config(self, name_or_index):
        rows = self.configs.get("rows", [])
        i = name_or_index if isinstance(name_or_index, int) else next(
            (k for k, r in enumerate(rows) if r["name"] == name_or_index), None)
        if i is None or not (0 <= i < len(rows)) or i == self.configs.get("active", 0):
            return
        self.checkpoint()            # one undo step, so undo switches back
        self.configs["active"] = i
        self.rebuild()

    def state_for(self, config=None):
        """The model in another configuration (for assemblies, drawings and derived parts), without changing
        this tab. None or unknown: the active one."""
        if not config or self._config_row(config) is None or config == self.active_config():
            return self.state
        st = self.compute(self.features, self.active_count(), None, config=config).copy()
        self._apply_overrides(st)
        return st

    def compute(self, features, upto=None, errors=None, config=None):
        """Replay features (with caching). Returns the resulting ModelState."""
        state = ModelState()
        from . import plugins as _P      # upstream's plugin features ("plugin:<id>"); loads no Qt
        _P.CURRENT_DOC[0] = self.uid
        h = hashlib.sha1()
        n = len(features) if upto is None else upto
        new_cache = {}
        row = self._config_row(config)
        for f in features[:n]:
            f = self._configured(f, row)
            # a feature's folder is where it sits in the list, not what it builds: kept out of the key
            h.update(json.dumps({k: v for k, v in f.items() if k != "folder"}, sort_keys=True).encode())
            if pyfeature.is_python_type(f.get("type")):
                h.update(self._python_key(f).encode())
            elif str(f.get("type", "")).startswith(_P.PREFIX):
                _P.ensure_loaded()                                      # an engine worker loads the folder here
                h.update(b"P" if f["type"] in _P.FEATURES else b"-")    # rebuild once the plugin is (un)installed
                f = dict(f, _raw_stamp=_P.stamp(f))
                fb = self.python_builds.get(f["id"])
                if _P.is_copy(fb):                        # its saved copy, read through the blob store
                    f = dict(f, _fallback=fb, _fallback_get=self._blob_get)
                    if f["type"] not in _P.FEATURES:      # still work out its expressions without the plugin
                        from . import features as _F
                        _F.EXTRA_NUMERIC.setdefault(f["type"], set(fb.get("numeric", [])))
            f = self._with_consumed_inputs(h, f)
            key = h.hexdigest()
            if key in self._cache:
                state, err = self._cache[key]
            else:
                prior = state
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
                            run = RUNNERS.get(f["type"])
                            if run is None and str(f["type"]).startswith(_P.PREFIX):
                                run = _P.run_feature
                            if run is None:
                                raise K.KernelError(f"Unknown feature type '{f['type']}' (made by a newer FreeShape?)")
                            run(state, prepare(f, state))
                        check_reference_parts(before, state)
                        for pid, part in list(state.parts.items()):
                            old = before.get(pid) or before.get(pid.split(".")[0])
                            if old is not None and old.face_colors and part.shape is not old.shape \
                                    and part.face_colors is None:
                                state.parts[pid] = Part(part.id, part.name, part.shape, part.color,
                                                        carry_face_colors(old, part.shape))
                    except K.KernelError as e:
                        err = str(e)
                        if err == REFERENCE_ONLY:
                            state = prior.copy()      # nothing the feature did to a Context body stays
                    except Exception as e:  # never let one feature kill the model
                        traceback.print_exc()
                        err = f"{type(e).__name__}: {e}"
                    if err is None:
                        # remembered so later features (feature patterns, mirror) can repeat what this one did
                        state.history[f["id"]] = (prior, state)
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
        return blobstore.store_for(self.path) if self.path else self.blob_view

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
        out = pyfeature.notices(self.features, self.locked_builds())
        for f in self.features:
            if f.get("type") == "derived" and not f.get("suppressed") and f["id"] not in self.errors \
                    and self._on_copy.get(f["id"]):
                out[f["id"]] = saved_copy_notice(f)
        return out

    # ------------------------------------------------------------------ folders
    def folder_members(self, folder_id):
        return [f["id"] for f in self.features if f.get("folder") == folder_id]

    def is_run(self, fids):
        """True if the features sit next to each other in the list (a folder holds one unbroken run)."""
        idx = sorted(i for i, f in enumerate(self.features) if f["id"] in set(fids))
        return bool(idx) and idx[-1] - idx[0] + 1 == len(idx)

    def make_folder(self, fids, name=None):
        """Put a run of features into a new folder (taking them out of any folder they were in).
        Returns the folder id, or raises ValueError if the features aren't next to each other."""
        fids = [f["id"] for f in self.features if f["id"] in set(fids)]
        if not fids:
            raise ValueError("Select the features to put in the folder")
        if not self.is_run(fids):
            raise ValueError("A folder holds features that are next to each other in the list. "
                             "Drag them together first, or select a run of features.")
        self.checkpoint()
        n = 1
        while f"D{n}" in self.folders:
            n += 1
        fo = f"D{n}"
        names = {v["name"] for v in self.folders.values()}
        k = 1
        while name is None or name in names:
            name, k = f"Folder {k}", k + 1
        self.folders[fo] = {"name": name, "open": True}
        for f in self.features:
            if f["id"] in fids:
                f["folder"] = fo
        self._prune_folders()
        self.notify()
        return fo

    def rename_folder(self, fo, name):
        if fo in self.folders and name:
            self.checkpoint()
            self.folders[fo]["name"] = name
            self.notify()

    def set_folder_open(self, fo, open_):
        """Expand / collapse a folder in the list (a view setting: no undo step)."""
        if fo in self.folders and self.folders[fo].get("open", True) != open_:
            self.folders[fo]["open"] = bool(open_)
            self.dirty = True
            self.notify()

    def ungroup_folder(self, fo):
        """Remove a folder, keeping its features where they are."""
        if fo not in self.folders:
            return
        self.checkpoint()
        for f in self.features:
            if f.get("folder") == fo:
                f.pop("folder", None)
        self.folders.pop(fo, None)
        self.notify()

    def remove_from_folder(self, fids):
        """Take features out of their folder. Ones at either end just leave it; ones from the middle move to
        just after the folder, so it stays one unbroken run. If that move would break the model (a later member
        uses a moved feature), nothing changes and ValueError is raised."""
        fids = set(fids)
        if not any(f["id"] in fids and f.get("folder") for f in self.features):
            return
        snap = self._snapshot()
        errs_before = set(self.errors)
        self.checkpoint()
        for fo in {f["folder"] for f in self.features if f["id"] in fids and f.get("folder")}:
            run = [i for i, f in enumerate(self.features) if f.get("folder") == fo]
            span = self.features[run[0]:run[-1] + 1]
            out = [f["id"] in fids for f in span]
            # leading and trailing features just leave the folder
            lo, hi = 0, len(span)
            while lo < hi and out[lo]:
                lo += 1
            while hi > lo and out[hi - 1]:
                hi -= 1
            for f in span:
                if f["id"] in fids:
                    f.pop("folder", None)
            middle = [f for f in span[lo:hi] if f["id"] in fids]
            if middle:
                inner = [f for f in span[lo:hi] if f["id"] not in fids]
                self.features[run[0]:run[-1] + 1] = span[:lo] + inner + middle + span[hi:]
        self._prune_folders()
        self.rebuild()
        if set(self.errors) - errs_before:
            self._undo.pop()
            self._restore(snap)
            self.rebuild()
            raise ValueError("Those features are used by later features in the folder, so they can't be moved "
                             "out of its middle. Take them out from the top or bottom of the folder instead.")

    def set_folder_suppressed(self, fo, value):
        self.checkpoint()
        for f in self.features:
            if f.get("folder") == fo:
                f["suppressed"] = bool(value)
        self.rebuild()

    def _prune_folders(self):
        """Drop empty folders and forget folders features point at that no longer exist."""
        used = {f.get("folder") for f in self.features}
        for fo in [k for k in self.folders if k not in used]:
            del self.folders[fo]
        for f in self.features:
            if f.get("folder") and f["folder"] not in self.folders:
                f.pop("folder", None)

    def rebuild(self):
        self._prune_folders()
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
                                        None if "color" in ov else p.face_colors, p.reference)
                # Part (features.py) has no material field; the assembly reads it from this attribute
                state.parts[pid].material = ov.get("material") or None

    def notify(self):
        for cb in list(self.listeners):
            cb()

    # ------------------------------------------------------------------ files
    def _python_builds(self):
        """({feature id: entry}, {blob key: bytes}) of the stored last build of every Python feature in the
        full feature list: each one that builds without error now, and each one locked on its stored build
        (kept as it is). A feature's parts are those with id `<fid>/...` in the (cached) state right after it."""
        from . import plugins as _P
        builds, blobs = {}, {}
        for i, f in enumerate(self.features):
            if f.get("type") == "derived" and not f.get("suppressed"):
                self._derived_build(f, i, builds, blobs)
                continue
            if _P.is_plugin_type(f.get("type")) and not f.get("suppressed"):
                self._plugin_build(f, builds, blobs)
                continue
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

    def _derived_build(self, f, i, builds, blobs):
        """Add the saved copy of the derived feature `f` (index `i`) to `builds` and `blobs`: the parts it builds
        now, or, while its source is missing and it shows its copy, that copy as it is. A feature in error
        keeps no entry."""
        fid, errors = f["id"], {}
        state = self.compute(self.features, i + 1, errors)
        if fid in errors:
            return
        if self._on_copy.get(fid):
            entry = builds[fid] = self.python_builds[fid]
            for rec in entry["parts"]:
                for k in (rec["brep"], rec["names"]):
                    if k in self._blob_bytes:
                        blobs[k] = self._blob_bytes[k]
            return
        memo = self._build_memo.get(fid)
        if memo is None or memo[0] is not state:
            memo = self._build_memo[fid] = (state, *(saved_copy_record(state, fid, saved_copy_key(f)) or (None, {})))
        if memo[1] is not None:
            builds[fid] = memo[1]
            blobs.update(memo[2])

    def _plugin_build(self, f, builds, blobs):
        """Add the saved copy of the plugin feature `f` to `builds` and `blobs`: the one this process built for
        `f` as it is now, else the one held (read from the file, or brought back from the engine worker that
        built it, `take_plugin_copies`), kept as it is."""
        from . import plugins as _P
        got = _P.saved_copy(f, self.uid)
        if got is None:
            entry = self.python_builds.get(f["id"])
            if not _P.is_copy(entry):
                return
            got = entry, {}
        entry, new = got
        builds[f["id"]] = entry
        blobs.update(new)
        for k in entry["breps"]:
            if k not in blobs:
                try:
                    blobs[k] = self._blob_get(k)
                except (blobstore.BlobError, ValueError, TypeError):
                    pass

    def take_plugin_copies(self, entries, blobs):
        """Keep the saved copies of plugin features that another process built ({feature id: entry}, their
        {blob key: bytes}): an engine worker, whose tab data brings them back. The next save writes them."""
        from . import plugins as _P
        self._blob_bytes.update(blobs)
        for fid, entry in entries.items():
            if _P.is_copy(entry):
                self.python_builds[fid] = entry

    def _import_key(self, text):
        """The blob key of an "import" feature's inline base64 STEP file (as a feature made by the Import command
        holds it in memory), its bytes held for the next save; None when it is not base64. Decoded once."""
        hit = self._import_keys.get(id(text))
        if hit is not None and hit[0] is text:
            return hit[1]
        import base64
        import binascii
        try:
            raw = base64.b64decode(text)
        except (binascii.Error, ValueError, TypeError):
            return None
        key = blobstore.key_of(raw)
        self._blob_bytes[key] = raw
        self._import_keys[id(text)] = (text, key)
        return key

    def _file_feature(self, f):
        """`f` as the file holds it: an "import" feature names its STEP file by blob key (I4.9)."""
        p = f.get("params")
        if f.get("type") == "import" and isinstance(p, dict) and isinstance(p.get("data"), str) and p["data"]:
            key = self._import_key(p["data"])
            if key is not None:
                return dict(f, params=_import_params_with_blob(p, key))
        return f

    def _import_blobs(self):
        """{key: bytes} of the STEP file of every "import" feature that can be read now (a missing one stays
        missing; its feature is red)."""
        out = {}
        for f in self.features:
            key = (self._file_feature(f).get("params") or {}).get("blob") if f.get("type") == "import" else None
            if isinstance(key, str):
                try:
                    out[key] = self._blob_get(key)
                except blobstore.BlobError:
                    continue
        return out

    def to_json(self):
        data = {"format": "freeshape", "version": M.latest_version("document"), "name": self.name,
                "uid": self.uid, "features": [self._file_feature(f) for f in self.features],
                "rollback": self.rollback,
                "overrides": self.part_overrides, "hidden": sorted(self.hidden_parts),
                "hidden_features": sorted(self.hidden_features),
                "part_aliases": self.part_aliases}
        builds, _ = self._python_builds()
        if builds:
            data["python_builds"] = builds
        # upstream 2.0 keys, written only when there is something to keep (a file that uses none of them is
        # byte for byte what it was, apart from the version)
        if self.folders:
            data["folders"] = self.folders
        if self.configs.get("rows"):
            data["configs"] = self.configs
        return json.dumps(data, indent=1)

    def to_data(self) -> bytes:
        """The tab's data as a Document store holds it (the bytes `save` writes)."""
        return self.to_json().encode("utf-8")

    def write_blobs(self, path=None, store=None):
        """Put the stored last build of every Python feature into `store` (an object with `put`; by default the
        blob store beside `path`, the tab file about to be written), before the JSON that names the keys.
        Returns the builds."""
        builds, blobs = self._python_builds()
        blobs.update(self._context_blobs())
        blobs.update(self._import_blobs())
        if blobs and (store is not None or path or self.path):     # nowhere to write: they stay held
            store = store if store is not None else blobstore.store_for(path or self.path)
            for data in blobs.values():
                store.put(data)
        self.python_builds = builds
        return builds

    def save(self, path=None):
        path = path or self.path
        self.write_blobs(path)
        txt = self.to_data().decode("utf-8")
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
    def load(path) -> "PartStudioDoc":
        with open(path, "rb") as fh:
            data = fh.read()
        return PartStudioDoc.from_data(data, path=path)

    @staticmethod
    def from_data(data, path=None, blobs=None) -> "PartStudioDoc":
        """A Part Studio from its bytes. `path` is the file they came from, if any: it names the file in errors,
        becomes the studio's `path` and places its blobs beside it. Without one, `blobs` (an object with
        `get`, `has` and `put`, see docstore.DocumentStore.blobs) holds them."""
        d = json.loads(bytes(data).decode("utf-8"))
        fmt = d.get("format") if isinstance(d, dict) else None
        kinds = {"freeshape-assembly": "an Assembly", "freeshape-drawing": "a Drawing",
                 "freeshape-variables": "a Variable Studio"}
        if fmt in kinds:
            raise K.KernelError(f"This file is {kinds[fmt]}, not a FreeShape Part Studio")
        if fmt is not None and fmt != "freeshape":
            raise K.KernelError("Not a FreeShape Part Studio")
        if not isinstance(d, dict) or not isinstance(d.get("features"), list):
            raise K.KernelError("Not a FreeShape Part Studio (no features list)")
        d = M.migrate("document", d, path if path is not None else "This Part Studio")
        doc = PartStudioDoc()
        doc._blob_bytes.update(blobstore.take_held(d))     # STEP files an old file carried inline (I4.9)
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
        folders, configs, copies = d.get("folders"), d.get("configs"), d.get("plugin_copies")
        doc.folders = folders if isinstance(folders, dict) else {}
        if isinstance(configs, dict) and isinstance(configs.get("rows"), list):
            doc.configs = configs
        if isinstance(copies, dict):          # upstream's inline copies: blobs held for the next save
            from . import plugins as _P
            for fid, fb in copies.items():
                entry = _P.adopt_inline_copy(fb, doc._blob_bytes) if isinstance(fid, str) else None
                if entry is not None and fid not in doc.python_builds:
                    doc.python_builds[fid] = entry
        doc.path = path
        doc.blob_view = blobs
        nums = [int("".join(c for c in f["id"] if c.isdigit()) or 0) for f in doc.features]
        doc._counter = max(nums + [0])
        doc.rebuild()
        return doc

    def visible_parts(self):
        return [p for p in self.state.parts.values() if p.id not in self.hidden_parts and not p.reference]

    def export(self, path, part_ids=None, options=None):
        parts = [p for p in self.state.parts.values() if (part_ids is None or p.id in part_ids) and not p.reference]
        if not parts:
            raise K.KernelError("Nothing to export")
        shapes = [p.shape for p in parts]
        if str(path).lower().endswith((".stl",)):
            K.export_stl(shapes, path, **(options or {}))
        elif str(path).lower().endswith((".3mf", ".obj")):
            from . import meshexport as ME
            ME.export_parts([(p.shape, p.name, p.color) for p in parts], path, **(options or {}))
        elif str(path).lower().endswith((".brep", ".brp")):
            K.export_brep(shapes, path)
        else:
            return K.export_step_named([(p.shape, np.eye(4), p.name, p.color) for p in parts], path, self.name)


Document = PartStudioDoc   # the old name of this class, kept so existing imports and tests still work
