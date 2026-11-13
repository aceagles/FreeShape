"""Cross-tab inputs: the one place the engine touches LinkResolver.

The parent side of the engine owns cross-tab inputs. A tab that consumes another tab's output (an assembly
instance's part, a drawing's model) asks this object, which asks the engine for the *open tab* behind the
link -- so a live, unsaved edit upstream is what gets consumed. If the upstream tab is not open yet, the
engine opens it as a tab of its own; a consuming tab never opens another tab's file itself. LinkResolver's
own load-from-disk fallback is therefore switched off here (its loaders refuse), leaving it only the
open-tab lookup, the part-id alias translation and the drawing-shape assembly it already does.

Links are matched by Part Studio / Assembly uid, then by file path, the way MainWindow._find_open_studio
does. `TabSource.provide` already takes a tab id as well; when links start naming tabs by id (I2.2),
pass the new tab-id lookup through `_provide` and nothing above this module changes.
"""
from __future__ import annotations

from contextlib import contextmanager

from .. import links


def _refuse_load(path):
    raise RuntimeError("the engine opens upstream tabs itself")


class CrossTabInputs:
    """`part(src)` is an AssemblyDoc.resolver, `drawing_shape(src)` a DrawingDoc.resolver. `tabs` is the
    engine: `tabs.provide(kind, uid=, path=, tab=)` returns (tab id, live document) or None."""

    def __init__(self, tabs):
        self._tabs = tabs
        self._used = []          # one list per refresh in progress (refreshes nest: drawing -> assembly)
        self.resolver = links.LinkResolver(
            find_open_studio=lambda uid, path: self._provide("studio", uid, path),
            find_open_assembly=lambda uid, path: self._provide("assembly", uid, path),
            load_document=_refuse_load, load_assembly=_refuse_load)

    def part(self, src):
        return self.resolver.resolve_source(src)

    def drawing_shape(self, src):
        return self.resolver.resolve_drawing_source(src)

    def variables(self, src):
        """{name: value} of the Variable Studio tab a "variable_studio_ref" source names (by tab id, uid, then
        file path), live and unsaved; None if no tab matches. A Document.var_inputs callable."""
        doc = self._provide("variables", src.get("uid"), src.get("path"), src.get("tab"))
        return None if doc is None else dict(doc.values()[0])

    def repair(self, doc):
        """Write back drifted link uid / path / names -- the save-time step MainWindow runs before saving."""
        links.repair_links(self.resolver, doc)

    @contextmanager
    def recording(self):
        """Collects the tab ids consumed while the block runs, in first-use order."""
        used = []
        self._used.append(used)
        try:
            yield used
        finally:
            self._used.pop()

    def _provide(self, kind, uid, path, tab=None):
        hit = self._tabs.provide(kind, uid=uid, path=path, tab=tab)
        if hit is None:
            return None
        tab_id, doc = hit
        if self._used and tab_id not in self._used[-1]:
            self._used[-1].append(tab_id)
        return doc
