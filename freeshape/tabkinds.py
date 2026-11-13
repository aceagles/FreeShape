"""The tab-kind registry: one record per kind of Tab (Part Studio, Assembly, Drawing, Variable Studio).

A record holds what every other module used to repeat: the kind name, the file extension, the format tag the
file carries, the migration chain name, how to load a file, how to make a new empty tab, the display
labels, and how to load a tab's data from bytes a Document store returns (optional: a kind without it is
loaded from its file). Headless and Qt-free; it imports no document class until a loader or factory is first called, so
importing it pulls in neither the CAD kernel nor `freeshape.engine`.

The record holds an extension and nothing about where a file lives in a Document folder: that is the
Document store's business (ADR 0009).

Per-kind wiring that is not about the file format (which resolver an Assembly load takes, how the engine runs
a tab) stays with the code that owns it. `register` / `unregister` add or drop a kind; `EXTENSIONS`,
`LOADERS`, `FACTORIES` and `KINDS` are live views, so a kind registered later appears in them.
"""
from __future__ import annotations

import importlib
import os
from collections.abc import MutableMapping, Sequence
from dataclasses import dataclass
from typing import Callable


@dataclass
class TabKind:
    kind: str                    # "studio" | "assembly" | "drawing" | "variables"
    ext: str                     # ".fshape": lower case, with the dot
    format_tag: str              # the "format" value the file carries
    chain: str | None            # migration chain name in freeshape/migrations.py (None: no chain)
    load: Callable               # path -> document
    make: Callable               # () -> a new empty document
    label: str                   # "Part Studio"
    plural: str                  # "Part Studios"
    from_data: Callable | None = None   # (bytes, path=, blobs=) -> document; None: the kind loads only from a file


_KINDS: dict[str, TabKind] = {}


def register(rec: TabKind) -> TabKind:
    if rec.kind in _KINDS:
        raise ValueError(f"tab kind {rec.kind!r} is already registered")
    if rec.ext in extensions():
        raise ValueError(f"extension {rec.ext!r} already belongs to kind {kind_for_ext(rec.ext)!r}")
    _KINDS[rec.kind] = rec
    return rec


def unregister(kind: str) -> None:
    _KINDS.pop(kind, None)


def get(kind) -> TabKind:
    """The record for `kind`; KeyError for an unknown kind."""
    return _KINDS[kind]


def find(kind) -> TabKind | None:
    return _KINDS.get(kind)


def kinds() -> list[str]:
    return list(_KINDS)


def records() -> list[TabKind]:
    return list(_KINDS.values())


def extensions() -> tuple[str, ...]:
    return tuple(r.ext for r in _KINDS.values())


def kind_for_ext(ext) -> str | None:
    ext = (ext or "").lower()
    return next((r.kind for r in _KINDS.values() if r.ext == ext), None)


def kind_for_path(path) -> str | None:
    """The kind whose extension `path` ends with (case-insensitive), or None."""
    return kind_for_ext(os.path.splitext(str(path))[1])


def kind_for_format(tag) -> str | None:
    return next((r.kind for r in _KINDS.values() if r.format_tag == tag), None)


def glob(kind) -> str:
    """`*.fshape`, for a file dialog filter."""
    return "*" + _KINDS[kind].ext


def extension_list() -> str:
    """".fshape, .fasm, .fdwg and .fvars": the registered extensions as prose."""
    exts = list(extensions())
    return exts[0] if len(exts) == 1 else ", ".join(exts[:-1]) + " and " + exts[-1]


# ---------------------------------------------------------------- live views (the old module-level tables)

class _AttrView(MutableMapping):
    """kind -> one field of its record. Writing replaces the field (tests patch a loader this way)."""

    def __init__(self, attr):
        self._attr = attr

    def __getitem__(self, kind):
        return getattr(_KINDS[kind], self._attr)

    def __setitem__(self, kind, value):
        setattr(_KINDS[kind], self._attr, value)

    def __delitem__(self, kind):
        raise TypeError("unregister a tab kind with tabkinds.unregister")

    def __iter__(self):
        return iter(list(_KINDS))

    def __len__(self):
        return len(_KINDS)

    def __repr__(self):
        return repr(dict(self))


class _Names(Sequence):
    """The registered kind names, in order; behaves like the old KINDS tuple."""

    def __getitem__(self, i):
        return tuple(_KINDS)[i]

    def __len__(self):
        return len(_KINDS)

    def __contains__(self, kind):
        return kind in _KINDS

    def __eq__(self, other):
        return tuple(self) == tuple(other)

    def __hash__(self):
        return hash(tuple(self))

    def __repr__(self):
        return repr(tuple(_KINDS))


EXTENSIONS = _AttrView("ext")
LOADERS = _AttrView("load")
FACTORIES = _AttrView("make")
KINDS = _Names()


# ---------------------------------------------------------------- the four built-in kinds

def _lazy(module, name, attr=None):
    """A callable that imports `module.name` (and `.attr` of it) on first use."""
    def call(*args, **kw):
        obj = getattr(importlib.import_module(module, __package__), name)
        return (getattr(obj, attr) if attr else obj)(*args, **kw)
    return call


for _kind, _ext, _tag, _chain, _mod, _cls, _label, _plural in (
        ("studio", ".fshape", "freeshape", "document", ".document", "PartStudioDoc", "Part Studio", "Part Studios"),
        ("assembly", ".fasm", "freeshape-assembly", "assembly", ".assembly", "AssemblyDoc", "Assembly",
         "Assemblies"),
        ("drawing", ".fdwg", "freeshape-drawing", "drawing", ".drawing", "DrawingDoc", "Drawing", "Drawings"),
        ("variables", ".fvars", "freeshape-variables", "variables", ".variable_studio", "VariableStudioDoc",
         "Variable Studio", "Variable Studios")):
    register(TabKind(_kind, _ext, _tag, _chain, _lazy(_mod, _cls, "load"), _lazy(_mod, _cls), _label, _plural,
                     _lazy(_mod, _cls, "from_data")))
del _kind, _ext, _tag, _chain, _mod, _cls, _label, _plural
