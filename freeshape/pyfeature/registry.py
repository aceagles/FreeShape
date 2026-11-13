"""Plugin discovery and the registry of Python feature types.

A Plugin is a folder holding `plugin.json`:
    {"id": "gears", "name": "Gears", "version": "1.0.0", "licence": "GPL-3.0-or-later",
     "features": ["<module file stem>:<ClassName>", ...]}
Each listed class (a `api.Feature` subclass) becomes the feature type "py:<plugin id>/<feature id>", added to
`features.RUNNERS` (runner `host.run`) and `features.FEATURE_LABELS` by mutating those dicts.

Discovery ports FreeCAD's `InitApplications` (src/App/FreeCADInit.py, tag 1.0.2): two roots are scanned in
order, the bundled root `freeshape/plugins/` and then the user root; a folder holding a file named
`ADDON_DISABLED` is skipped; a user plugin with the same id as a bundled one replaces it; a plugin that fails
is recorded in `problems()` and loading continues -- the loader never raises.

Security: code is imported only from these two roots. A feature type read from a Document is only ever a key
into `_TYPES` -- nothing here builds a module name or path from it (unlike FreeCAD's
PropertyPythonObject::Restore, which imports the module a file names). User plugins are imported under the
synthetic package `freeshape_user_plugins.<id>` with `importlib.util.spec_from_file_location`; the user root
is never put on sys.path.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import re
import sys
import threading
import types
from dataclasses import dataclass

from .. import features as _features
from . import api

BUNDLED_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins")
BUNDLED_PACKAGE = "freeshape.plugins"
USER_PACKAGE = "freeshape_user_plugins"
OTHER_BUNDLED_PACKAGE = "freeshape_bundled_plugins"   # a bundled root other than freeshape/plugins (tests)
PREFIX = "py:"
STANDARD_PREFIX = "sp:"
STOP_FILE = "ADDON_DISABLED"
MANIFEST = "plugin.json"
MANIFEST_MAX_BYTES = 1 << 20
_FEATURE_REF = re.compile(r"([A-Za-z_][A-Za-z0-9_]*):([A-Za-z_][A-Za-z0-9_]*)\Z")


@dataclass(frozen=True)
class PluginInfo:
    id: str
    name: str
    version: str
    licence: str
    path: str                 # the plugin folder
    source: str               # "bundled" or "user"
    types: tuple = ()         # the feature types it registered
    package: str = ""         # the package its modules were imported under
    standard_types: tuple = ()   # the Standard part types it registered ("sp:<plugin id>/<part id>")


_LOCK = threading.RLock()
_PLUGINS: dict[str, PluginInfo] = {}          # plugin id -> info
_TYPES: dict[str, tuple] = {}                 # type -> (Feature subclass, PluginInfo)
_STANDARD: dict[str, tuple] = {}              # type -> (StandardPart subclass, PluginInfo); never in _TYPES
_PROBLEMS: list[tuple[str, str]] = []
_USER_ROOTS_LOADED: set[str] = set()


# --------------------------------------------------------------------------- type strings

def is_python_type(ftype) -> bool:
    return isinstance(ftype, str) and ftype.startswith(PREFIX)


def type_id(plugin_id: str, feature_id: str) -> str:
    return f"{PREFIX}{plugin_id}/{feature_id}"


def _clip(x, n=60) -> str:
    s = str(x)
    return s if len(s) <= n else s[:n] + "..."


def recorded_plugin(f) -> tuple[str, str]:
    """(plugin id, version) a feature dict says it was made with, as display text. Read from hostile data:
    only ever used for messages."""
    rec = f.get("plugin") if isinstance(f, dict) else None
    rec = rec if isinstance(rec, dict) else {}
    pid = rec.get("id")
    if not isinstance(pid, str):
        t = f.get("type") if isinstance(f, dict) else ""
        pid = t[len(PREFIX):].split("/")[0] if is_python_type(t) else "?"
    ver = rec.get("version")
    return _clip(pid, 40), (_clip(ver, 20) if isinstance(ver, str) else "")


def missing_message(f) -> str:
    pid, ver = recorded_plugin(f)
    name = _clip(f.get("name", "") if isinstance(f, dict) else "")
    return f'Python feature "{name}" needs plugin "{pid}" {ver}, which is not installed'.replace("  ", " ")


# --------------------------------------------------------------------------- lookups

def get(ftype):
    """The Feature subclass registered for `ftype`, or None."""
    entry = _TYPES.get(ftype) if isinstance(ftype, str) else None
    return entry[0] if entry else None


def plugin_for(ftype) -> PluginInfo | None:
    entry = _TYPES.get(ftype) if isinstance(ftype, str) else None
    return entry[1] if entry else None


def plugin(plugin_id) -> PluginInfo | None:
    return _PLUGINS.get(plugin_id)


def plugins() -> list[PluginInfo]:
    return list(_PLUGINS.values())


def feature_types() -> list[tuple[str, type, PluginInfo]]:
    """[(type, Feature subclass, PluginInfo)] for every registered Python feature, in registration order."""
    return [(t, cls, info) for t, (cls, info) in _TYPES.items()]


def standard_type_id(plugin_id: str, part_id: str) -> str:
    return f"{STANDARD_PREFIX}{plugin_id}/{part_id}"


def is_standard_type(type_id) -> bool:
    return isinstance(type_id, str) and type_id.startswith(STANDARD_PREFIX)


def standard_part(type_id):
    """The StandardPart subclass registered for `type_id`, or None."""
    entry = _STANDARD.get(type_id) if isinstance(type_id, str) else None
    return entry[0] if entry else None


def standard_parts() -> list[tuple[str, type, PluginInfo]]:
    """[(type id, StandardPart subclass, PluginInfo)] for every registered Standard part."""
    return [(t, cls, info) for t, (cls, info) in _STANDARD.items()]


def default_params(ftype) -> dict:
    """{key: default} from the declarations; KeyError for an unregistered type (as features.default_params)."""
    cls = get(ftype)
    if cls is None:
        raise KeyError(ftype)
    return {p.key: p.default for p in cls.params}


def problems() -> list[tuple[str, str]]:
    """[(path, message)] for every plugin that failed to load."""
    return list(_PROBLEMS)


def _problem(path, msg):
    entry = (str(path), str(msg))
    if entry not in _PROBLEMS:
        _PROBLEMS.append(entry)


# --------------------------------------------------------------------------- roots

def user_root() -> str:
    """$FREESHAPE_PLUGIN_DIR, else <GenericDataLocation>/FreeShape/plugins, computed without Qt the way
    QStandardPaths.GenericDataLocation resolves it (ui/home.py thumbs_dir uses the same base)."""
    env = os.environ.get("FREESHAPE_PLUGIN_DIR")
    if env:
        return env
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "FreeShape", "plugins")


# --------------------------------------------------------------------------- loading

def _read_manifest(folder) -> dict:
    path = os.path.join(folder, MANIFEST)
    if os.path.getsize(path) > MANIFEST_MAX_BYTES:
        raise ValueError(f"{MANIFEST} is larger than {MANIFEST_MAX_BYTES} bytes")
    with open(path, encoding="utf-8") as fh:
        m = json.load(fh)
    if not isinstance(m, dict):
        raise ValueError(f"{MANIFEST} must hold a JSON object")
    pid = m.get("id")
    if not isinstance(pid, str) or not api.ID_RE.match(pid):
        raise ValueError(f"plugin id {_clip(pid)!r} must match {api.ID_RE.pattern}")
    for k in ("name", "version", "licence"):
        if not isinstance(m.get(k), str) or not m[k].strip():
            raise ValueError(f"{MANIFEST} needs a {k!r} string")
    feats = m.get("features")
    if "standard_parts" in m:
        sps = m["standard_parts"]
        if not isinstance(sps, list):
            raise ValueError(f"{MANIFEST} 'standard_parts' must be a list")
        for ref in sps:
            if not isinstance(ref, str) or not _FEATURE_REF.match(ref):
                raise ValueError(f"standard part entry {_clip(ref)!r} must be '<module file stem>:<ClassName>'")
        if feats is None:
            feats = []
    if not isinstance(feats, list) or (not feats and "standard_parts" not in m):
        raise ValueError(f"{MANIFEST} needs a non-empty 'features' list")
    for ref in feats:
        if not isinstance(ref, str) or not _FEATURE_REF.match(ref):
            raise ValueError(f"feature entry {_clip(ref)!r} must be '<module file stem>:<ClassName>'")
    return m


def _synthetic_package(name, folder=None):
    """A package module in sys.modules whose __path__ is [folder] (or empty), so a plugin's own modules can
    import each other relatively without the folder going on sys.path."""
    mod = sys.modules.get(name)
    if mod is None or (folder is not None and list(getattr(mod, "__path__", [])) != [folder]):
        init = os.path.join(folder, "__init__.py") if folder else None
        if init and os.path.isfile(init):
            spec = importlib.util.spec_from_file_location(name, init, submodule_search_locations=[folder])
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            try:
                spec.loader.exec_module(mod)
            except BaseException:
                sys.modules.pop(name, None)
                raise
        else:
            mod = types.ModuleType(name)
            mod.__path__ = [folder] if folder else []
            mod.__package__ = name
            sys.modules[name] = mod
    return mod


def _drop_modules(package):
    for name in [n for n in sys.modules if n == package or n.startswith(package + ".")]:
        del sys.modules[name]


def _import_module(folder, package, stem, real_bundled):
    if real_bundled:
        return importlib.import_module(f"{package}.{stem}")
    path = os.path.join(folder, stem + ".py")
    if not os.path.isfile(path):
        raise ImportError(f"no module file {stem}.py")
    name = f"{package}.{stem}"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


def register_plugin(path, source="user") -> PluginInfo | None:
    """Load the plugin in folder `path` and register its feature types. If a plugin with the same id is
    already registered, the new one replaces it -- but only once it has loaded completely, so a broken
    update leaves the working copy in place. Returns the PluginInfo, or None after recording a problem."""
    folder = os.path.abspath(os.fspath(path))
    with _LOCK:
        try:
            m = _read_manifest(folder)
        except Exception as e:
            _problem(folder, f"bad {MANIFEST}: {type(e).__name__}: {e}")
            return None
        pid = m["id"]
        real_bundled = os.path.dirname(folder) == os.path.abspath(BUNDLED_ROOT)
        if real_bundled:
            if os.path.basename(folder) != pid:
                _problem(folder, f"bundled plugin folder must be named after its id {pid!r}")
                return None
            package = f"{BUNDLED_PACKAGE}.{pid}"
        else:
            package = f"{USER_PACKAGE if source == 'user' else OTHER_BUNDLED_PACKAGE}.{pid}"
        old = _PLUGINS.get(pid)
        stash = {}
        if old is not None and old.package == package:
            # same import names as the plugin being replaced: set its modules aside so this loads fresh
            stash = {n: sys.modules.pop(n) for n in list(sys.modules)
                     if n == package or n.startswith(package + ".")}
        try:
            if not real_bundled:
                _synthetic_package(package.split(".")[0])
                _synthetic_package(package, folder)
            classes = []
            for ref in m.get("features", []):
                stem, cname = ref.split(":")
                mod = _import_module(folder, package, stem, real_bundled)
                cls = getattr(mod, cname, None)
                if cls is None:
                    raise ImportError(f"module {stem} has no class {cname}")
                bad = api.declaration_problems(cls)
                if bad:
                    raise ValueError("; ".join(bad))
                classes.append(cls)
            ids = [c.id for c in classes]
            dup = {i for i in ids if ids.count(i) > 1}
            if dup:
                raise ValueError(f"feature id {sorted(dup)[0]!r} is declared twice")
            sp_classes = []
            for ref in m.get("standard_parts", []):
                stem, cname = ref.split(":")
                mod = _import_module(folder, package, stem, real_bundled)
                cls = getattr(mod, cname, None)
                if cls is None:
                    raise ImportError(f"module {stem} has no class {cname}")
                bad = api.standard_declaration_problems(cls)
                if bad:
                    raise ValueError("; ".join(bad))
                sp_classes.append(cls)
            sp_ids = [c.id for c in sp_classes]
            dup = {i for i in sp_ids if sp_ids.count(i) > 1}
            if dup:
                raise ValueError(f"standard part id {sorted(dup)[0]!r} is declared twice")
        except BaseException as e:   # a plugin may even call sys.exit; it must not stop the app
            if isinstance(e, KeyboardInterrupt):
                raise
            _drop_modules(package)
            sys.modules.update(stash)
            _problem(folder, f"{type(e).__name__}: {e}")
            return None
        if old is not None:
            _unregister(pid, drop_modules=old.package != package)
        info = PluginInfo(pid, m["name"], m["version"], m["licence"], folder, source,
                          tuple(type_id(pid, c.id) for c in classes), package,
                          tuple(standard_type_id(pid, c.id) for c in sp_classes))
        _PLUGINS[pid] = info
        from . import host
        for cls, t in zip(classes, info.types):
            _TYPES[t] = (cls, info)
            _features.RUNNERS[t] = host.run
            _features.FEATURE_LABELS[t] = cls.label
        for cls, t in zip(sp_classes, info.standard_types):
            _STANDARD[t] = (cls, info)
        return info


def _unregister(plugin_id, drop_modules=True):
    info = _PLUGINS.pop(plugin_id, None)
    if info is None:
        return False
    for t in info.types:
        _TYPES.pop(t, None)
        _features.RUNNERS.pop(t, None)
        _features.FEATURE_LABELS.pop(t, None)
    for t in info.standard_types:
        _STANDARD.pop(t, None)
    if drop_modules:
        _drop_modules(info.package)
    return True


def unregister_plugin(plugin_id) -> bool:
    """Remove a plugin's feature types (from RUNNERS and FEATURE_LABELS too). True if it was registered."""
    with _LOCK:
        return _unregister(plugin_id)


def load_root(root, source) -> list[PluginInfo]:
    """Register every plugin folder directly under `root`, in name order. Never raises."""
    out = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    seen = {}
    for name in names:
        folder = os.path.join(root, name)
        if name.startswith((".", "_")) or not os.path.isdir(folder):
            continue
        if os.path.exists(os.path.join(folder, STOP_FILE)) or not os.path.isfile(os.path.join(folder, MANIFEST)):
            continue
        try:
            pid = _read_manifest(folder)["id"]
        except Exception:
            pid = None          # register_plugin records why
        if pid in seen:
            _problem(folder, f"plugin id {pid!r} is already used by {seen[pid]}")
            continue
        info = register_plugin(folder, source)
        if info is not None:
            seen[pid] = folder
            out.append(info)
    return out


def load_bundled_plugins(root=None) -> list[PluginInfo]:
    """Register the plugins shipped in freeshape/plugins (or `root`, for tests)."""
    with _LOCK:
        return load_root(root or BUNDLED_ROOT, "bundled")


def load_user_plugins(root=None) -> list[PluginInfo]:
    """Register the plugins in the user root (or `root`). Idempotent: each root is scanned once per process.
    Called at app start, and by host.run the first time a rebuild meets an unregistered py: type."""
    root = os.path.abspath(root or user_root())
    with _LOCK:
        if root in _USER_ROOTS_LOADED:
            return []
        _USER_ROOTS_LOADED.add(root)
        return load_root(root, "user")


# --------------------------------------------------------------------------- notices

def notices(features, locked=None) -> dict:
    """{feature id: text} for each Python feature whose plugin is not installed, or whose installed plugin
    version differs from the one recorded in the feature. Notices are not errors.

    `locked` is {feature id: stored-build entry} of the features shown on their stored last build (I4.7);
    those read `locked: plugin "gears" 1.0.0 is not installed; showing its last build`. A Part Studio
    `Document` may be passed as `features`: its features and its `locked_builds()` are then used."""
    if locked is None and hasattr(features, "locked_builds"):
        locked = features.locked_builds()
    features = getattr(features, "features", features)
    locked = locked or {}
    feats = [f for f in features if isinstance(f, dict) and is_python_type(f.get("type"))]
    if any(get(f["type"]) is None for f in feats):
        load_user_plugins()
    out = {}
    for f in feats:
        info = plugin_for(f["type"])
        pid, ver = recorded_plugin(f)
        if info is None and f.get("id") in locked:
            from .host import locked_message
            out[f.get("id")] = locked_message(locked[f.get("id")], f)
        elif info is None:
            out[f.get("id")] = f'needs plugin "{pid}" {ver}, which is not installed'.replace("  ", " ")
        elif ver != info.version:
            out[f.get("id")] = f"made with {pid} {ver or '(no version)'}, rebuilt with {info.version}"
    return out
