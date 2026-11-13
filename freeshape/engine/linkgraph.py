"""Rebuilding dependents over the Link graph, shared by both executors.

The edges live in `links.LinkResolver` (`graph`): the engine records one every time it hands a tab another
tab's output, together with a digest of exactly the part of that output the tab read. When a tab changes,
`propagate` takes the tabs that consume it, directly or not, in the order `graphlib.TopologicalSorter` gives
(every tab after the tabs it consumes, each once -- a fan-in is one tab, not a cycle) and rebuilds those whose
consumed digest differs from the recorded one. A rebuild that leaves its own output the same stops the walk
there, because the next tab's digest is then unchanged too.

A digest is taken over a neutral "package" of an upstream tab: what the in-process executor reads off a live
document (`live_package`) and what the process executor holds as the worker's `upstream` output
(`lean_package`) give the same package, so both executors agree on when an input changed.
"""
from __future__ import annotations

import hashlib
import json

from .. import links
from .contract import LinkCycleError, TabErrored
from .encode import connector_out, sketch_out


def norm_key(part):
    """The key a link source reads from a Part Studio: its part id, or "*" for all of them."""
    return "*" if part in (None, "*") else str(part)


def link_key(src):
    """The key a link source reads: `norm_key` of its part, and for a source that names a configuration
    ("config") that part in that configuration, as "<part>@<configuration>" (a part id never holds an "@")."""
    key = norm_key(src.get("part"))
    return f"{key}@{src['config']}" if src.get("config") else key


def split_key(key):
    """(part key, configuration name or None) of a `link_key` (or of a bare part id, None for the whole studio)."""
    if key is None:
        return None, None
    part, at, config = str(key).partition("@")
    return part, (config if at else None)


def key_configs(keys):
    """The configuration names `keys` read, sorted, each once: what a studio's package has to carry."""
    return tuple(sorted({c for c in (split_key(k)[1] for k in keys) if c}))


def _round_T(T):
    return [round(float(x), 9) for row in (T or ()) for x in row]


def _live_studio(doc, st, enc):
    return {"parts": {str(p.id): enc.brep(p.shape)[1] for p in st.parts.values()
                      if not p.reference},      # I5.3: Context bodies are reference only
            "hidden": sorted(map(str, doc.hidden_parts)), "aliases": dict(getattr(doc, "part_aliases", {})),
            "sketches": {str(fid): sketch_out(fid, "", res).hash for fid, res in st.sketches.items()},
            "connectors": {str(fid): connector_out(fid, c).hash for fid, c in st.connectors.items()}}


def live_package(kind, doc, enc, keys=()):
    """What a consumer can read of a live tab document (in this process). `keys` (link keys) name the
    configurations it reads besides the active one: each is under "configs"."""
    if kind == "studio":
        view = _live_studio(doc, doc.state, enc)
        configs = key_configs(keys)
        if configs:
            view["configs"] = {c: _live_studio(doc, doc.state_for(c), enc) for c in configs}
        error = links._studio_error(doc)
        if error:                    # I1.8: a source that turns red rekeys its consumers (clean: digest unchanged)
            view["error"] = error
        return view
    if kind == "variables":
        return {"vars": dict(doc.values()[0])}
    if kind == "assembly":
        out = []
        for inst in doc.instances:
            p = doc.parts.get(inst["id"])
            out.append([str(inst["id"]), _round_T(inst.get("T")), enc.brep(p.shape)[1] if p is not None else None,
                        bool(inst.get("hidden"))])
        return {"instances": out}
    return {}


def _lean_studio(pkg, aliases):
    return {"parts": {str(p["id"]): p["hash"] for p in pkg["parts"]},
            "hidden": sorted(str(p["id"]) for p in pkg["parts"] if p["hidden"]),
            "aliases": dict(aliases or {}),
            "sketches": {str(s["id"]): s["hash"] for s in pkg.get("sketches") or []},
            "connectors": {str(c["id"]): c["hash"] for c in pkg.get("connectors") or []}}


def lean_package(pkg):
    """The same package from a worker's `upstream` output (worker._Worker.upstream)."""
    kind = pkg["kind"]
    if kind == "studio":
        view = _lean_studio(pkg, pkg.get("aliases"))
        if pkg.get("configs"):
            view["configs"] = {c: _lean_studio(sub, pkg.get("aliases")) for c, sub in pkg["configs"].items()}
        if pkg.get("error"):
            view["error"] = pkg["error"]
        return view
    if kind == "variables":
        return {"vars": dict(pkg["vars"])}
    if kind == "assembly":
        return {"instances": [[str(i["id"]), _round_T(i["T"]), i["part"], bool(i["hidden"])]
                              for i in pkg["instances"]]}
    return {}


def _studio_body(view, keys):
    parts, aliases = view["parts"], view["aliases"]
    if not keys or "*" in keys:
        body = [[pid, h, pid in view["hidden"]] for pid, h in sorted(parts.items())]
        if view.get("sketches"):     # a consumer of the whole studio also sees its sketches
            body = [body, sorted(view["sketches"].items())]
        if view.get("connectors"):   # ... and its mate connectors
            body = [body, ["connectors", sorted(view["connectors"].items())]]
        return body
    return [[k, parts.get(aliases.get(k, k))] for k in sorted(keys)]


def package_digest(kind, view, keys=()):
    """Hash of what a consumer reading `keys` (link keys: part ids, "*": all, "<part>@<configuration>") takes
    from the package `view`. Keys that name no configuration hash exactly as they did before configurations."""
    if kind == "studio":
        plain, by_config = set(), {}
        for k in keys:
            part, config = split_key(k)
            if config:
                by_config.setdefault(config, set()).add(norm_key(part))
            else:
                plain.add(norm_key(part))
        body = _studio_body(view, plain) if plain or not by_config else None
        if by_config:                # each configuration read is a package of its own
            configs = view.get("configs") or {}
            empty = {"parts": {}, "hidden": [], "aliases": {}}
            body = [body, ["configs", [[c, _studio_body(configs.get(c) or empty, ks)]
                                       for c, ks in sorted(by_config.items())]]]
    else:
        body = view
    digest = hashlib.sha1(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
    if kind == "studio" and view.get("error"):      # the fold StudioPackage.digest_for applies
        digest = hashlib.sha1(f"{digest}|{view['error']}".encode()).hexdigest()
    return digest


def propagate(graph, origin, digest_now, rebuild, label=str):
    """Rebuild what consumes `origin` and saw its input change. `digest_now(upstream, edge)` is the digest of
    the output `edge` consumed as it is now; `rebuild(tab)` rebuilds one tab and records its new edges.
    Returns the tabs rebuilt, in order, and counts each one in `graph.rebuilds`. A tab whose worker has died is
    skipped; the others still rebuild."""
    graph.last_order = []
    try:
        order = graph.order(origin)
    except links.LinkCycle as e:
        raise LinkCycleError(e.tabs, "Circular link: " + " -> ".join(label(t) for t in e.tabs)) from e
    for tab in order:
        if not any(digest_now(up, edge) != edge.digest for up, edge in graph.inputs(tab).items()):
            continue
        try:
            rebuild(tab)
        except TabErrored:
            continue
        graph.rebuilds[tab] = graph.rebuilds.get(tab, 0) + 1
        graph.last_order.append(tab)
    return list(graph.last_order)
