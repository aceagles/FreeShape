"""Cross-tab inputs: the one place the engine touches LinkResolver.

The parent side of the engine owns cross-tab inputs. A tab that consumes another tab's output (an assembly
instance's part, a drawing's model) asks this object, which asks the engine for the *open tab* behind the
link -- so a live, unsaved edit upstream is what gets consumed. If the upstream tab is not open yet, the
engine opens it as a tab of its own; a consuming tab never opens another tab's file itself. LinkResolver's
own load-from-disk fallback is therefore switched off here (its loaders refuse), leaving it only the
open-tab lookup, the part-id alias translation and the drawing-shape assembly it already does.

Links are matched by tab id (a source's "tab"), then by Part Studio / Assembly uid, then by file path. LinkResolver's
open-tab callbacks get only (uid, path), so the source being resolved is kept in `_srcs` while it resolves and
the callbacks read its "tab" from there.

Every tab that consumes others is recorded in the Link graph (`engine.links`): `recording(consumer)` collects
the tabs read while a block runs and, when it ends, records one edge per tab with a digest of what was read.
"""
from __future__ import annotations

from contextlib import contextmanager

from .. import kernel as K
from .. import links
from .linkgraph import norm_key


def _refuse_load(path):
    raise RuntimeError("the engine opens upstream tabs itself")


def external_id(src):
    """The id a cross-Document source's package goes by in the Link graph and in the consumer's worker: the
    Document, the pinned Version and the tab. Never a tab id of this Document, so it cannot match a local tab."""
    return f"{src.get('document')}@{src.get('version')}:{src.get('tab')}"


def cross_package(kind, doc, enc):
    """The package (worker._Worker.upstream's shape) of a tab document of a pinned Version, read by the parent
    from the resolver's read-only view. It carries no uid and no path, so the worker cannot match it by either."""
    from dataclasses import asdict
    from .contract import plain
    from .encode import sketch_out
    from .worker import lean_part
    pkg = {"kind": kind, "uid": None, "name": str(doc.name), "path": None}
    if kind == "studio":
        st = doc.state
        pkg.update(aliases=dict(doc.part_aliases), vars=plain(dict(st.vars)),
                   parts=[lean_part(enc, p, hidden=p.id in doc.hidden_parts) for p in st.parts.values()],
                   sketches=[plain(asdict(sketch_out(fid, (doc.feature(fid) or {}).get("name", fid), res)))
                             for fid, res in st.sketches.items()])
    elif kind == "variables":
        pkg.update(vars=plain(dict(doc.values()[0])))
    else:
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
    return pkg


class _Used(list):
    """The tab ids consumed in one recording, in first-use order, and the part ids read of each."""

    def __init__(self):
        super().__init__()
        self.keys = {}


class CrossTabInputs:
    """`part(src)` is an AssemblyDoc.resolver, `drawing_shape(src)` a DrawingDoc.resolver. `tabs` is the
    engine: `tabs.provide(kind, uid=, path=, tab=)` returns (tab id, live document) or None."""

    def __init__(self, tabs):
        self._tabs = tabs
        self._used = []          # one list per refresh in progress (refreshes nest: drawing -> assembly)
        self._srcs = []          # the sources being resolved, innermost last
        self.resolver = links.LinkResolver(
            find_open_studio=lambda uid, path: self._provide("studio", uid, path, self._cur().get("tab")),
            find_open_assembly=lambda uid, path: self._provide("assembly", uid, path, self._cur().get("tab")),
            load_document=_refuse_load, load_assembly=_refuse_load)
        self.resolver._cross_doc = self._external_doc       # a pinned Version arrives as a package, never a file

    def _external_doc(self, src, kind):
        """The view of the package the parent sent for a cross-Document source (see `external_id`)."""
        hit = self._tabs.provide(kind, tab=external_id(src))
        if hit is None:
            raise K.KernelError(f"Document “{src.get('document')}” at Version “{src.get('version')}” "
                                f"was not handed to this tab")
        return hit[1]

    def _cur(self):
        return self._srcs[-1] if self._srcs else {}

    @contextmanager
    def _resolving(self, src):
        self._srcs.append(src)
        try:
            yield
        finally:
            self._srcs.pop()

    def part(self, src):
        with self._resolving(src):
            return self.resolver.resolve_source(src)

    def drawing_shape(self, src):
        with self._resolving(src):
            return self.resolver.resolve_drawing_source(src)

    def variables(self, src):
        """{name: value} of the Variable Studio tab a "variable_studio_ref" source names (by tab id, uid, then
        file path), live and unsaved; None if no tab matches. A Document.var_inputs callable."""
        with self._resolving(src):
            doc = self._provide("variables", src.get("uid"), src.get("path"), src.get("tab"))
        return None if doc is None else dict(doc.values()[0])

    def studio(self, src):
        """The StudioPackage of the Part Studio tab `src` names (by tab id, uid, then file path), live and
        unsaved: its parts and sketches as the parent publishes them. A Document.link_inputs callable. A worker
        view carries the package the parent sent (`package`); otherwise it is read from the open tab. Raises
        KernelError when no tab matches."""
        with self._resolving(src):
            doc = self._provide("studio", src.get("uid"), src.get("path"), src.get("tab"))
            if doc is None:
                raise K.KernelError(f"Part Studio “{src.get('studio_name', '?')}” isn't open")
            pkg = getattr(doc, "package", None)
            return pkg if pkg is not None else self.resolver.resolve_studio(src)

    def repair(self, doc):
        """Write back drifted link uid / path / names -- the save-time step MainWindow runs before saving."""
        links.repair_links(self.resolver, doc)

    @contextmanager
    def recording(self, consumer=None):
        """Collects the tab ids consumed while the block runs, in first-use order. Given `consumer` (a tab id),
        the edges into it are recorded when the block ends without an error; otherwise `commit` them later."""
        used = _Used()
        self._used.append(used)
        try:
            yield used
        finally:
            self._used.pop()
        if consumer is not None:
            self.commit(consumer, used)

    def commit(self, consumer, used):
        """Record what `consumer` consumed (a `recording` result) as the edges into it, replacing the last ones."""
        if not self._tabs.LINKS:
            return
        edges = {}
        for tab_id in used:
            edge = self._tabs.make_edge(tab_id, tuple(sorted(getattr(used, "keys", {}).get(tab_id, ()))))
            if edge is not None:
                edges[tab_id] = edge
        self.resolver.record_inputs(consumer, edges)

    def _provide(self, kind, uid, path, tab=None):
        hit = self._tabs.provide(kind, uid=uid, path=path, tab=tab)
        if hit is None:
            return None
        tab_id, doc = hit
        if self._used:
            if tab_id not in self._used[-1]:
                self._used[-1].append(tab_id)
            self._used[-1].keys.setdefault(tab_id, set()).add(norm_key(self._cur().get("part")))
        return doc
