"""The child half of the process executor (process.py): one tab per worker process, run by a single-worker
`concurrent.futures.ProcessPoolExecutor`. The pool's initializer (`initialize`) starts the parent watchdog;
the first call (`load`) opens the tab. Headless; nothing here or in what it imports may pull in Qt.

Inside the worker the tab is an ordinary in-process tab (executor.py) held by a one-tab engine. What differs
is where its cross-tab inputs come from: the parent sends, with each call, the outputs this tab consumes --
upstream parts as BREP bytes with their hashes and element-name maps, a studio's variables, an assembly's
instance placements -- and `provide` answers links from those alone. The worker never opens another tab's
file: the parent holds the live, possibly unsaved, upstream state; the file on disk may be stale.

The worker also exits by itself when the process that started it goes away (`_watch_parent`): the pool's
pipes do not tell a child that its parent died, because a spawned child holds both ends of its own queue.
"""
from __future__ import annotations

import hashlib
import os
import sys
import threading
import time
from dataclasses import asdict
from types import SimpleNamespace

from .. import blobstore
from .. import kernel as K
from .. import links
from .. import naming as N
from ..features import Part, PartMap
from .contract import EngineError, SketchOut, plain
from .encode import connector_out, sketch_out
from .executor import InProcessEngine

_LETTER = {"face": "F", "edge": "E", "vertex": "V"}
_PARENT_POLL_S = 0.5

_state = None            # this process's _Worker, once `load` has opened its tab


# =========================================================================== the parent watchdog

def _parent_alive_check(ppid):
    """A no-argument callable: is the parent `ppid` still there?"""
    if os.name == "nt":                                         # no getppid re-parenting on Windows
        import ctypes
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x00100000, False, ppid)      # SYNCHRONIZE
        if not handle:
            return lambda: False
        return lambda: k32.WaitForSingleObject(handle, 0) != 0  # WAIT_OBJECT_0 (0) = the parent exited
    return lambda: os.getppid() == ppid                         # POSIX: an orphan is re-parented


def _watch_parent(ppid):
    alive = _parent_alive_check(ppid)

    def watch():
        while alive():
            time.sleep(_PARENT_POLL_S)
        os._exit(0)

    threading.Thread(target=watch, name="freeshape-parent-watch", daemon=True).start()


# =========================================================================== upstream tabs, as the parent sent them

class _StudioView:
    """What a link reads of a Part Studio (LinkResolver, links.repair_links), rebuilt from its outputs."""
    kind = "studio"

    def __init__(self, pkg, parts, configs=None):
        self.uid, self.name, self.path = pkg["uid"], pkg["name"], pkg["path"]
        self.part_aliases = dict(pkg.get("aliases") or {})
        self.hidden_parts = {p["id"] for p in pkg["parts"] if p["hidden"]}
        self.state = self._state(parts, pkg)
        # the configurations the parent sent (pkg["configs"]): name -> the state in that configuration
        self.configs = {c: self._state(cparts, pkg) for c, cparts in (configs or {}).items()}
        self.packages = {}       # configuration ("": none named) -> the links.StudioPackage link_inputs hands out

    def _state(self, parts, pkg):
        return SimpleNamespace(parts=PartMap(parts, aliases=self.part_aliases), vars=dict(pkg.get("vars") or {}),
                               sheet={})

    def state_for(self, config=None):
        """The state in configuration `config` (as PartStudioDoc.state_for), if the parent sent it; else the
        active one."""
        return self.configs.get(config, self.state) if config else self.state

    def package_for(self, config=None):
        return self.packages.get(config or "", self.packages.get(""))


class _VariablesView:
    """What a Part Studio's Variable Studio reference reads of a Variable Studio: its evaluated variables."""
    kind = "variables"

    def __init__(self, pkg):
        self.uid, self.name, self.path = pkg.get("uid"), pkg["name"], pkg["path"]
        self._vars = dict(pkg["vars"])

    def values(self):
        return dict(self._vars), {}


class _AssemblyView:
    """What a drawing's link reads of an Assembly: its instances' parts and placements."""
    kind = "assembly"

    def __init__(self, pkg, parts):
        self.uid, self.name, self.path = pkg["uid"], pkg["name"], pkg["path"]
        self.instances = [dict(i) for i in pkg["instances"]]
        self.parts = {i["id"]: parts[i["part"]] for i in self.instances if i["part"] in parts}

    def visible_instances(self):
        return [i for i in self.instances if not i.get("hidden") and i["id"] in self.parts]


def lean_part(enc, part, hidden=False):
    """One part as the parent passes it between tabs: PartOut fields minus the mesh, as a dict."""
    po = enc.part(part, hidden=hidden, mesh=False)
    return {"id": po.id, "name": po.name, "color": po.color, "hidden": po.hidden, "brep": po.brep,
            "hash": po.hash, "names": po.names, "face_colors": po.face_colors}


class _WorkerEngine(InProcessEngine):
    """A one-tab engine whose links resolve only against upstream outputs the parent sent (`install`)."""

    LINKS = False            # the parent owns the Link graph

    def __init__(self, meshes=True):
        super().__init__(meshes=meshes)
        self.upstream = {}       # parent tab id -> _StudioView / _AssemblyView
        self.upstream_key = None
        self._shapes = {}        # hash -> shape rebuilt from its BREP, so an unchanged part stays the same object

    def provide(self, kind, uid=None, path=None, tab=None):
        """The parent's matching rule (process.ProcessEngine._match), over the tabs it sent: by tab id, by
        uid, then by file path. Never opens a file: an upstream tab the parent did not send is not open."""
        views = [(t, v) for t, v in self.upstream.items() if v.kind == kind]
        hit = next(((t, v) for t, v in views if tab and t == tab), None)
        if hit is None and uid:
            hit = next(((t, v) for t, v in views if v.uid == uid), None)
        if hit is None and path and not (tab and self.by_tab_id):    # a tab id names it: never a path
            ap = os.path.abspath(path)
            hit = next(((t, v) for t, v in views if v.path and os.path.abspath(v.path) == ap), None)
        return hit

    def _shape(self, lp):
        shp = self._shapes.get(lp["hash"])
        if shp is None:
            shp = K.shape_from_brep(lp["brep"])
            self._shapes[lp["hash"]] = shp
        # the hash is the upstream tab's; re-writing the restored shape must not be needed to get it back
        self.encoder.seed(shp, lp["brep"], lp["hash"])
        return shp

    def _part(self, lp):
        shp = self._shape(lp)
        fc = {int(i) - 1: tuple(c) for i, c in lp["face_colors"].items()} if lp.get("face_colors") else None
        p = Part(lp["id"], lp["name"], shp, tuple(lp["color"]), fc)
        names = {(_LETTER[k], int(i)): n for k, m in lp["names"].items() for i, n in m.items()}
        if names:
            p.named = N.NamedShape(shp, names)
        return p

    def _studio_package(self, pkg, parts, config=""):
        """The links.StudioPackage of a studio the parent sent (in configuration `config`), with the digest
        LinkResolver.resolve_studio gives the same studio (the parts and sketches carry the hashes the parent
        computed)."""
        out = links.StudioPackage(
            {lp["id"]: links.PublishedPart(id=lp["id"], name=lp["name"], color=tuple(lp["color"]), hidden=lp["hidden"],
                                     shape=parts[lp["id"]].shape, names=lp["names"], hash=lp["hash"])
             for lp in pkg["parts"]},
            {sk["id"]: SketchOut(**sk) for sk in pkg.get("sketches") or ()}, dict(pkg.get("aliases") or {}),
            connectors={c["id"]: dict(c) for c in pkg.get("connectors") or ()},
            error=pkg.get("error") or "", config=config)
        out.digest = out.digest_for(None)
        return out

    def install(self, upstream, key):
        """Replace the upstream tabs with the parent's latest. Bumps the generation, so the tab re-reads its
        inputs on its next edit or outputs -- the same staleness rule as in process."""
        views, used = {}, set()
        for tid, pkg in (upstream or {}).items():
            if pkg["kind"] == "studio":
                configs = pkg.get("configs") or {}
                v = views[tid] = _StudioView(pkg, {lp["id"]: self._part(lp) for lp in pkg["parts"]},
                                             {c: {lp["id"]: self._part(lp) for lp in sub["parts"]}
                                              for c, sub in configs.items()})
                v.packages[""] = self._studio_package(pkg, v.state.parts)
                for c, sub in configs.items():
                    v.packages[c] = self._studio_package(dict(pkg, **sub), v.configs[c].parts, c)
                used |= {lp["hash"] for sub in [pkg, *configs.values()] for lp in sub["parts"]}
            elif pkg["kind"] == "variables":
                views[tid] = _VariablesView(pkg)
            else:
                views[tid] = _AssemblyView(pkg, {h: self._part(lp) for h, lp in pkg["parts"].items()})
                used |= set(pkg["parts"])
        self._shapes = {h: s for h, s in self._shapes.items() if h in used}
        self.upstream, self.upstream_key = views, key
        self.generation += 1


# =========================================================================== the worker

class _SentBlobs:
    """The tab's blob lookup in the worker: the blobs the parent sent (with the load, and with any edit that
    named new ones), plus those the tab puts. Never a file. The parent's own lookup checked them on the way out."""

    def __init__(self, blobs):
        self.data = dict(blobs or {})

    def get(self, key, max_bytes=blobstore.DEFAULT_MAX_BYTES):
        blobstore._check_key(key)
        data = self.data.get(key)
        if data is None:
            raise blobstore.BlobError(f"no blob {key}")
        if len(data) > max_bytes:
            raise blobstore.BlobError(f"blob is {len(data)} bytes, over the {max_bytes} byte limit")
        return data

    def has(self, key):
        return isinstance(key, str) and key in self.data

    def put(self, data):
        data = bytes(data)
        key = hashlib.sha256(data).hexdigest()
        self.data[key] = data
        return key


class _Worker:
    def __init__(self, spec):
        self.tab = spec["tab"]
        self.engine = _WorkerEngine(meshes=spec["meshes"])
        self.engine.use_document(spec.get("document_id"))     # whose tab ids the sources name: see CrossTabInputs
        self.engine.install(spec.get("upstream"), spec.get("upstream_key"))
        self.engine.by_tab_id = bool(spec.get("by_tab_id"))   # the parent's matching rule (process._match)
        sent = spec.get("blobs")
        self.blobs = None if sent is None else _SentBlobs(sent)
        self.known = set(sent or ())     # blob keys the parent holds the bytes of
        if spec.get("data") is None:
            self.engine.new_tab(spec["kind"], spec.get("name"), tab=self.tab)
        else:
            # from the parent's bytes, never from the file: `path` only places the tab (errors, relative link
            # paths, a Part Studio's blobs folder when it has one)
            self.engine.open_tab_data(spec["kind"], spec["data"], self.tab, spec.get("open_name"),
                                      blobs=self.blobs, path=spec.get("path"))
        t = self.engine._get(self.tab)
        for edit in spec.get("edits") or ():           # the parent's log of every edit applied since `data`
            if edit.get("op") == "save":               # already written; replaying it only keeps a save-as path
                if edit.get("path"):
                    t.doc.path = os.path.abspath(edit["path"])
                continue
            self.engine.submit_edit(self.tab, edit)
        t.revision = int(spec.get("revision") or 0)
        t.seen = self.engine.generation

    def add_blobs(self, blobs):
        """Blobs the parent sent with an edit that names them."""
        if blobs and self.blobs is not None:
            self.blobs.data.update(blobs)
            self.known |= set(blobs)

    def data(self):
        """(bytes, [every blob key they name], {key: bytes} of those the parent does not hold yet)."""
        data, named = self.engine._get(self.tab).data()
        new = {k: b for k, b in named.items() if k not in self.known}
        self.known |= set(new)
        if self.blobs is not None:
            self.blobs.data.update(new)
        return data, sorted(named), new

    @property
    def doc(self):
        return self.engine._get(self.tab).doc

    def meta(self):
        """The parent's view of this tab: name, path, uid, revision, and the link sources it holds."""
        t = self.engine._get(self.tab)
        doc = t.doc
        return {"name": str(doc.name), "path": doc.path, "uid": getattr(doc, "uid", None),
                "revision": t.revision, "pid": os.getpid(),
                "sources": plain(links.tab_sources(t.kind, doc))}

    def upstream(self, configs=()):
        """This tab's outputs as another tab consumes them: parts without meshes, variables, placements. A Part
        Studio's also carry the configurations `configs` names, each under "configs"."""
        t = self.engine._get(self.tab)
        t.sync()
        doc, enc = t.doc, self.engine.encoder
        pkg = {"kind": t.kind, "uid": getattr(doc, "uid", None), "name": str(doc.name), "path": doc.path}
        if t.kind == "studio":
            def body(st):
                return {"parts": [lean_part(enc, p, hidden=p.id in doc.hidden_parts) for p in st.parts.values()
                                  if not p.reference],      # I5.3: Context bodies are reference only
                        "sketches": [plain(asdict(sketch_out(fid, (doc.feature(fid) or {}).get("name", fid), res)))
                                     for fid, res in st.sketches.items()],
                        "connectors": [plain(asdict(connector_out(fid, c))) for fid, c in st.connectors.items()]}
            pkg.update(aliases=dict(doc.part_aliases), vars=plain(dict(doc.state.vars)),
                       error=links._studio_error(doc), **body(doc.state))
            if configs:
                pkg["configs"] = {c: body(doc.state_for(c)) for c in configs}
        elif t.kind == "variables":
            pkg.update(vars=plain(dict(doc.values()[0])))
        elif t.kind == "assembly":
            parts, insts = {}, []
            for inst in doc.instances:
                p = doc.parts.get(inst["id"])
                key = None
                if p is not None:
                    lp = lean_part(enc, p)
                    parts.setdefault(lp["hash"], lp)
                    key = lp["hash"]
                insts.append({"id": inst["id"], "T": plain(inst.get("T")), "hidden": bool(inst.get("hidden")),
                              "part": key})
            pkg.update(parts=parts, instances=insts)
        else:
            raise EngineError("a drawing is not an input of another tab")
        return pkg

    def call(self, op, arg):
        if op == "edit":
            self.engine.submit_edit(self.tab, arg)
            return self.meta()
        if op == "outputs":
            return self.engine.outputs(self.tab)
        if op == "upstream":
            return self.upstream(arg or ())
        if op == "rebuild":                       # a consumed output changed: re-read it now
            self.engine._get(self.tab).rebuild()
            return self.meta()
        if op == "meta":
            return self.meta()
        if op == "data":
            return self.data()
        raise EngineError(f"unknown worker call {op!r}")


# =========================================================================== the test-only open guard

TEST_GUARD_ENV = "FREESHAPE_TEST_REFUSE_TAB_OPENS"
_refused = []


def _guarded(path, exts, manifest):
    """A tab file (any registered extension, or its `.tmp`), the Document manifest, or a file in a `blobs`
    folder named by a blob key (or its `.tmp`)."""
    try:
        p = os.fsdecode(os.fspath(path))
    except TypeError:
        return False
    base = os.path.basename(p)
    stem = base[:-4] if base.endswith(".tmp") else base
    if stem == manifest or os.path.splitext(stem)[1].lower() in exts:
        return True
    return os.path.basename(os.path.dirname(p)) == "blobs" and len(stem) == 64 and \
        all(c in "0123456789abcdef" for c in stem)


def refuse_tab_file_opens():
    """For tests: make `open` / `io.open` / `os.open` refuse (PermissionError) any path `_guarded` names, and
    record each try (`refused_opens`). Kernel scratch files, Python sources and plugins are not touched. Returns
    a function that puts the real ones back. A worker installs it at start-up when TEST_GUARD_ENV is set."""
    import builtins
    import io

    from .. import tabkinds as TK
    from ..docstore import MANIFEST_NAME
    exts = tuple(TK.extensions())
    real = builtins.open, io.open, os.open

    def wrap(fn):
        def call(file, *a, **kw):
            if isinstance(file, (str, bytes, os.PathLike)) and _guarded(file, exts, MANIFEST_NAME):
                _refused.append(os.fsdecode(os.fspath(file)))
                raise PermissionError(f"test guard: opening {file!r} is refused")
            return fn(file, *a, **kw)
        return call

    builtins.open, io.open, os.open = wrap(real[0]), wrap(real[1]), wrap(real[2])

    def undo():
        builtins.open, io.open, os.open = real

    return undo


def refused_opens():
    """The paths the guard refused in this process, in order."""
    return list(_refused)


def initialize(parent_pid):
    """The pool initializer: start the parent watchdog. Unpickling this function has already imported the
    engine (and OCP, the slow part of starting a worker), so the worker is now warm and waits for `load`."""
    _watch_parent(parent_pid)
    if os.environ.get(TEST_GUARD_ENV):
        refuse_tab_file_opens()


def load(spec):
    """The worker's first call: open its tab (from the parent's bytes and edit log); returns `meta`."""
    global _state
    if _state is not None:
        raise EngineError("this worker already holds a tab")
    _state = _Worker(spec)
    return _state.meta()


def current() -> _Worker:
    """This process's worker (for code the parent runs here with ProcessEngine.run_in_worker)."""
    if _state is None:
        raise EngineError("no tab is loaded in this worker")
    return _state


def call(op, arg=None, upstream=None, key=None, blobs=None):
    """One request from the parent. `upstream` (with its `key`) is sent only when it changed; `blobs` are the
    blobs an edit names that the worker was not sent before."""
    w = current()
    if upstream is not None:
        w.engine.install(upstream, key)
    w.add_blobs(blobs)
    return w.call(op, arg)


def probe():
    """(pid, whether Qt was imported here) -- a worker must stay headless."""
    return os.getpid(), any(m.split(".")[0].startswith("PySide") for m in sys.modules)
