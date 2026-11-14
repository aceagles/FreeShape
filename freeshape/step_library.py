"""Component library: an index of the STEP files under one folder, searchable by name, tags and path.

No Qt here. `scan(root)` walks the folder once; a `parts.json` found on the way (a list of
{id, name, description, tags, aliases, ...}, as published by step.parts) names the files below it whose
stem equals an id. Everything else is named after its file. `search` keeps the entries that contain every
word of the query."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

STEP_EXTS = (".step", ".stp")
CATALOGUE = "parts.json"


@dataclass(frozen=True)
class Entry:
    path: str                 # absolute
    rel: str                  # relative to the library folder, forward slashes
    name: str
    group: str                # the folder it is filtered by: the first one or two levels below the library folder
    description: str = ""
    tags: tuple = ()
    size: int = 0
    text: str = ""            # lower-case haystack for search


def scan(root) -> list[Entry]:
    """Every STEP file under `root`, sorted by relative path. A missing folder gives an empty list."""
    root = os.path.abspath(root)
    out: list[Entry] = []
    if not os.path.isdir(root):
        return out
    catalogues: dict[str, dict] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort(key=str.lower)
        meta = _load_catalogue(os.path.join(dirpath, CATALOGUE)) if CATALOGUE in filenames else None
        if meta is None:
            meta = catalogues.get(os.path.dirname(dirpath), {})
        catalogues[dirpath] = meta
        for fn in sorted(filenames, key=str.lower):
            stem, ext = os.path.splitext(fn)
            if ext.lower() not in STEP_EXTS:
                continue
            path = os.path.join(dirpath, fn)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            out.append(_entry(root, path, stem, size, meta.get(stem, {})))
    return out


def _load_catalogue(path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if isinstance(data, dict):
        data = data.get("parts", [])
    if not isinstance(data, list):
        return None
    return {str(p["id"]): p for p in data if isinstance(p, dict) and p.get("id")}


def _words(v) -> tuple:
    return tuple(str(x) for x in v) if isinstance(v, (list, tuple)) else ()


def _entry(root, path, stem, size, meta) -> Entry:
    rel = os.path.relpath(path, root).replace(os.sep, "/")
    dirs = rel.split("/")[:-1]
    group = "/".join(dirs[:2] if len(dirs) >= 3 else dirs[:1])
    name = str(meta.get("name") or "") or " ".join(stem.replace("_", " ").replace("-", " ").split()) or stem
    description = str(meta.get("description") or "")
    tags = _words(meta.get("tags"))
    extra = [str(meta.get(k) or "") for k in ("category", "family")]
    text = " ".join([name, stem, description, rel, *tags, *_words(meta.get("aliases")), *extra]).lower()
    return Entry(path=path, rel=rel, name=name, group=group, description=description, tags=tags, size=size, text=text)


def groups(entries) -> list[str]:
    return sorted({e.group for e in entries if e.group}, key=str.lower)


def search(entries, query="", group=None) -> list[Entry]:
    """Entries containing every word of `query` (case-insensitive), those matching in the name first.
    `group` keeps one folder. An empty query keeps everything, in path order."""
    terms = query.lower().split()
    pool = [e for e in entries if not group or e.group == group]
    if not terms:
        return pool
    hits = []
    for i, e in enumerate(pool):
        if all(t in e.text for t in terms):
            name = e.name.lower()
            hits.append((sum(t not in name for t in terms), len(e.name), i, e))
    hits.sort(key=lambda h: h[:3])
    return [h[3] for h in hits]
