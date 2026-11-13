"""What changed between two states of a Part Studio, in words and in 3D: features added / removed / changed,
setting by setting; sketch edits; variables; part volumes; and the material added and removed.

From upstream FreeShape 2.0's version history. Upstream keeps its versions inside the Part Studio file and makes
one on every save. This fork does not: a Version is a git tag of the whole Document (ADR 0001,
doc_folder.FolderDocument.create_version), so the in-file storage (create / restore / version blobs) is not
here. What is kept is the comparison, which works on two snapshots however they were obtained: `snapshot(doc)`
of an open Part Studio, or the same dict built from a Part Studio loaded out of a Version. `compare_versions` does
that for a whole Document: two Versions (or one and the open tabs), Part Studio tab by tab, each Version read
through FolderDocument.open_version."""
from __future__ import annotations

import copy
import datetime as _dt
import json

from . import kernel as K
from .sketch import DIMENSIONS, CONSTRAINT_LABELS


# ---------------------------------------------------------------------- snapshots
def snapshot(doc) -> dict:
    """The parts of a Part Studio a comparison reads (not the view: hidden parts, camera …), as a copy that
    later edits to `doc` do not change."""
    return {"features": copy.deepcopy(doc.features), "configs": copy.deepcopy(doc.configs),
            "folders": copy.deepcopy(doc.folders), "overrides": copy.deepcopy(doc.part_overrides),
            "rollback": doc.rollback}


def _same(a, b) -> bool:
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def now_iso():
    return _dt.datetime.now().replace(microsecond=0).isoformat()


def nice_time(iso) -> str:
    try:
        t = _dt.datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return str(iso or "")
    today = _dt.date.today()
    if t.date() == today:
        return "Today " + t.strftime("%H:%M")
    if t.date() == today - _dt.timedelta(days=1):
        return "Yesterday " + t.strftime("%H:%M")
    return t.strftime("%d %b %Y %H:%M").lstrip("0")


def current(doc) -> dict:
    return {"features": copy.deepcopy(doc.features), "configs": doc.configs, "folders": doc.folders,
            "overrides": doc.part_overrides, "rollback": doc.rollback}


def state_of(doc, snap):
    """The model a snapshot builds (a scratch Part Studio, so the open one isn't touched). It shares `doc`'s
    blobs, link inputs and build cache, so imports, Derived features and unchanged features resolve as they do
    in `doc`."""
    from .document import PartStudioDoc
    d = PartStudioDoc()
    d.uid = doc.uid
    d.features = copy.deepcopy(snap["features"])
    d.configs = copy.deepcopy(snap.get("configs") or {"rows": [], "active": 0})
    d.folders = copy.deepcopy(snap.get("folders") or {})
    d.part_overrides = copy.deepcopy(snap.get("overrides") or {})
    d.rollback = snap.get("rollback")
    d.plugin_fallback = doc.plugin_fallback
    d.python_builds = doc.python_builds
    d.part_aliases = doc.part_aliases
    d.path, d.blob_view, d._blob_bytes = doc.path, doc.blob_view, doc._blob_bytes
    d.var_inputs, d.link_inputs = doc.var_inputs, doc.link_inputs
    d._cache = doc._cache                     # unchanged features aren't rebuilt
    d.rebuild()
    return d.state, d.errors


# ---------------------------------------------------------------------- describing settings
LENGTH_KEYS = {"depth", "depth2", "radius", "distance", "thickness", "offset", "spacing", "diameter",
               "counterbore_dia", "counterbore_depth", "countersink_dia", "dx", "dy", "dz", "major", "pitch",
               "length", "clearance", "thread_clearance"}
ANGLE_KEYS = {"angle", "rotate", "draft", "twist"}
LABELS = {
    "depth": "Depth", "depth2": "Second depth", "end": "End", "op": "Result", "angle": "Angle",
    "radius": "Radius", "distance": "Distance", "thickness": "Thickness", "offset": "Offset", "count": "Count",
    "spacing": "Spacing", "flip": "Flip", "second": "Second direction", "regions": "Sketch regions",
    "faces": "Faces", "edges": "Edges", "parts": "Parts", "tools": "Tool parts", "targets": "Target parts",
    "plane": "Plane", "axis": "Axis", "direction": "Direction", "diameter": "Diameter", "size": "Size",
    "standard": "Standard", "style": "Style", "fit": "Fit", "outward": "Outward", "keep": "Keep",
    "copy": "Make a copy", "dx": "Move X", "dy": "Move Y", "dz": "Move Z", "rotate": "Rotate", "k": "K-factor",
    "length": "Length", "pitch": "Pitch", "major": "Major diameter", "left_hand": "Left-hand", "type": "Type",
    "profiles": "Profiles", "path": "Path", "neutral": "Neutral plane", "up_to": "Up to", "features": "Features",
    "mode": "Mode", "position": "Position", "down": "Other way", "relief": "Corner reliefs", "config": "Configuration",
    "sketch": "Sketch", "expr": "Value", "name": "Name", "keep_tools": "Keep tool parts", "ruled": "Ruled",
}
VALUE_NAMES = {"new": "New", "add": "Add", "remove": "Remove", "intersect": "Intersect", "union": "Union",
               "subtract": "Subtract", "blind": "Blind", "through": "Through all", "full": "Full turn",
               "up_to_face": "Up to face", "up_to_part": "Up to part", "up_to_vertex": "Up to vertex",
               "symmetric": "Symmetric"}


def _label(ftype, key):
    if str(ftype).startswith("plugin:"):
        from . import plugins
        spec = plugins.FEATURES.get(ftype)
        for f in spec.fields if spec else []:
            if f["key"] == key:
                return f["label"]
    return LABELS.get(key, key.replace("_", " ").capitalize())


def _num(v, key):
    if isinstance(v, str):
        return v                                 # an expression, e.g. "#wall * 2"
    if isinstance(v, bool):
        return "on" if v else "off"
    if isinstance(v, int) and key not in LENGTH_KEYS | ANGLE_KEYS:
        return str(v)
    s = f"{float(v):.4f}".rstrip("0").rstrip(".")
    return s + (" mm" if key in LENGTH_KEYS else "°" if key in ANGLE_KEYS else "")


def _value(v, key):
    if v is None:
        return "—"
    if isinstance(v, (bool, int, float)) or (isinstance(v, str) and key in LENGTH_KEYS | ANGLE_KEYS):
        return _num(v, key)
    if isinstance(v, str):
        return VALUE_NAMES.get(v, v if len(v) < 40 else v[:37] + "…")
    if isinstance(v, dict):
        if v.get("kind") == "std" and v.get("name"):
            return v["name"]
        return "a selection"
    return str(v)


def _plural(n, word):
    irregular = {"vertex": "vertices", "axis": "axes"}
    return f"{n} {word if n == 1 else irregular.get(word, word + 's')}"


def describe_params(ftype, old, new):
    """Readable lines for what changed in a feature's settings."""
    out = []
    if ftype == "sketch":
        out += sketch_changes(old.get("sketch") or {}, new.get("sketch") or {})
        if not _same(old.get("plane"), new.get("plane")):
            out.append(f"Moved to another plane ({_value(old.get('plane'), 'plane')} → "
                       f"{_value(new.get('plane'), 'plane')})")
        return out
    if ftype == "variable":
        if old.get("name") != new.get("name"):
            out.append(f"Renamed #{old.get('name')} → #{new.get('name')}")
        if str(old.get("expr")) != str(new.get("expr")):
            out.append(f"#{new.get('name')}: {old.get('expr')} → {new.get('expr')}")
        return out
    if ftype == "import":
        if old.get("data") != new.get("data"):
            out.append(f"Geometry replaced ({new.get('filename') or 'new file'})")
        return out
    for key in list(old) + [k for k in new if k not in old]:
        if key.startswith("_") or _same(old.get(key), new.get(key)):
            continue
        a, b = old.get(key), new.get(key)
        lab = _label(ftype, key)
        if isinstance(a, list) or isinstance(b, list):
            na, nb = len(a or []), len(b or [])
            word = lab.lower()
            out.append(f"{lab}: {na} → {nb} selected" if na != nb else f"{lab}: a different selection"
                       if word else lab)
        else:
            out.append(f"{lab}: {_value(a, key)} → {_value(b, key)}")
    return out


_ENTITY_WORDS = {"line": "line", "circle": "circle", "arc": "arc", "point": "point", "spline": "spline",
                 "ellipse": "ellipse", "text": "text"}


def sketch_changes(old, new):
    out = []
    oe, ne = old.get("entities", {}), new.get("entities", {})
    for label, ids, src in (("Added ", [k for k in ne if k not in oe], ne),
                            ("Removed ", [k for k in oe if k not in ne], oe)):
        counts = {}
        for k in ids:
            e = src[k]
            word = ("construction " if e.get("construction") else "") + _ENTITY_WORDS.get(e["type"], e["type"])
            counts[word] = counts.get(word, 0) + 1
        if counts:
            out.append(label + ", ".join(_plural(n, w) for w, n in counts.items()))
    for k in ne:
        if k in oe and oe[k].get("type") == "text" and oe[k].get("text") != ne[k].get("text"):
            out.append(f"Text “{oe[k].get('text')}” → “{ne[k].get('text')}”")
        elif k in oe and oe[k].get("construction") != ne[k].get("construction"):
            out.append(f"A {_ENTITY_WORDS.get(ne[k]['type'], 'curve')} made "
                       + ("construction" if ne[k].get("construction") else "normal geometry"))
    oc = {c["id"]: c for c in old.get("constraints", []) if "id" in c}
    nc = {c["id"]: c for c in new.get("constraints", []) if "id" in c}

    def dim(c):
        v = c.get("value")
        if isinstance(v, str):
            return v
        if v is None:
            return "?"
        return f"{float(v):.4f}".rstrip("0").rstrip(".") + ("°" if c["type"] == "angle" else " mm")

    def name(c):
        return CONSTRAINT_LABELS.get(c["type"], c["type"]).lower()
    for i, c in nc.items():
        if c["type"] in DIMENSIONS:
            if i not in oc:
                out.append(f"Added a {name(c)} dimension ({dim(c)})")
            elif not _same(oc[i].get("value"), c.get("value")):
                out.append(f"{name(c).capitalize()} dimension {dim(oc[i])} → {dim(c)}")
    for i, c in oc.items():
        if c["type"] in DIMENSIONS and i not in nc:
            out.append(f"Removed a {name(c)} dimension ({dim(c)})")
    for label, a, b in (("Added ", nc, oc), ("Removed ", oc, nc)):
        kinds = {}
        for i, c in a.items():
            if i not in b and c["type"] not in DIMENSIONS:
                kinds[name(c)] = kinds.get(name(c), 0) + 1
        if kinds:
            n = sum(kinds.values())
            out.append(f"{label}{_plural(n, 'constraint')} ({', '.join(sorted(kinds))})")
    op, npt = old.get("points", {}), new.get("points", {})
    moved = sum(1 for k, p in npt.items() if k in op and max(abs(p[0] - op[k][0]), abs(p[1] - op[k][1])) > 1e-6)
    if moved and not out:
        out.append(f"{_plural(moved, 'point')} moved")
    return out


def _lcs_ids(a, b):
    """Ids in both lists that kept their order (longest common subsequence)."""
    n, m = len(a), len(b)
    L = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            L[i][j] = L[i + 1][j + 1] + 1 if a[i] == b[j] else max(L[i + 1][j], L[i][j + 1])
    out, i, j = set(), 0, 0
    while i < n and j < m:
        if a[i] == b[j]:
            out.add(a[i])
            i, j = i + 1, j + 1
        elif L[i + 1][j] >= L[i][j + 1]:
            i += 1
        else:
            j += 1
    return out


def feature_changes(old_snap, new_snap):
    """[{"kind": added|removed|changed, "name", "type", "lines": [..]}] in the newer version's order."""
    of = {f["id"]: f for f in old_snap["features"]}
    nf = {f["id"]: f for f in new_snap["features"]}
    kept_order = _lcs_ids([f["id"] for f in old_snap["features"] if f["id"] in nf],
                          [f["id"] for f in new_snap["features"] if f["id"] in of])
    out = []
    for f in new_snap["features"]:
        o = of.get(f["id"])
        if o is None:
            out.append({"kind": "added", "name": f["name"], "type": f["type"], "lines": []})
            continue
        lines = []
        if o.get("name") != f.get("name"):
            lines.append(f"Renamed from “{o.get('name')}”")
        if bool(o.get("suppressed")) != bool(f.get("suppressed")):
            lines.append("Suppressed" if f.get("suppressed") else "Unsuppressed")
        if f["id"] not in kept_order:
            lines.append("Moved in the feature list")
        if o.get("type") == f.get("type"):
            lines += describe_params(f["type"], o.get("params", {}), f.get("params", {}))
        else:
            lines.append("Changed type")
        if lines:
            out.append({"kind": "changed", "name": f["name"], "type": f["type"], "lines": lines})
    for f in old_snap["features"]:
        if f["id"] not in nf:
            out.append({"kind": "removed", "name": f["name"], "type": f["type"], "lines": []})
    return out


def other_changes(old_snap, new_snap):
    out = []
    oc, nc = old_snap.get("configs") or {}, new_snap.get("configs") or {}
    on = [r["name"] for r in oc.get("rows", [])]
    nn = [r["name"] for r in nc.get("rows", [])]
    for n in nn:
        if n not in on:
            out.append(f"Configuration “{n}” added")
    for n in on:
        if n not in nn:
            out.append(f"Configuration “{n}” removed")
    orow = {r["name"]: r for r in oc.get("rows", [])}
    for r in nc.get("rows", []):
        if r["name"] in orow and not _same(orow[r["name"]], r):
            out.append(f"Configuration “{r['name']}” changed")
    oo, no = old_snap.get("overrides") or {}, new_snap.get("overrides") or {}
    for pid in set(oo) | set(no):
        a, b = oo.get(pid, {}), no.get(pid, {})
        if a.get("name") != b.get("name") and b.get("name"):
            out.append(f"Part renamed {a.get('name') or pid} → {b['name']}")
        if a.get("color") != b.get("color") and b.get("color"):
            out.append(f"{b.get('name') or a.get('name') or 'A part'}'s colour changed")
    return out


# ---------------------------------------------------------------------- geometry
def geometry_changes(old_state, new_state, shapes=True):
    """{"rows": [(name, old volume or None, new volume or None)], "added": [shape], "removed": [shape]}.
    Volumes in mm³; shapes are the material added / removed (for showing in 3D)."""
    rows, added, removed = [], [], []
    for pid, p in new_state.parts.items():
        o = old_state.parts.get(pid)
        if o is None:
            rows.append((p.name, None, K.volume(p.shape)))
            if shapes:
                added.append(p.shape)
            continue
        if o.shape.IsSame(p.shape):
            continue
        vo, vn = K.volume(o.shape), K.volume(p.shape)
        rows.append((p.name, vo, vn))
        if shapes:
            for sh, into in ((K.cut(p.shape, o.shape), added), (K.cut(o.shape, p.shape), removed)):
                try:
                    if K.solids(sh) and K.volume(sh) > 1e-9 * max(vo, vn, 1.0):
                        into.append(sh)
                except Exception:
                    pass
    for pid, o in old_state.parts.items():
        if pid not in new_state.parts:
            rows.append((o.name, K.volume(o.shape), None))
            if shapes:
                removed.append(o.shape)
    return {"rows": rows, "added": added, "removed": removed}


def compare(doc, old_snap, new_snap=None, geometry=True):
    """What changed from `old_snap` to `new_snap` (snapshots, see `snapshot`; None = the Part Studio `doc` as it
    is now). `doc` is the open Part Studio the snapshots belong to: its blobs and links build them."""
    old_snap = current(doc) if old_snap is None else old_snap
    now = new_snap is None
    new_snap = current(doc) if now else new_snap
    res = {"features": feature_changes(old_snap, new_snap), "other": other_changes(old_snap, new_snap)}
    if geometry:
        os_, _ = state_of(doc, old_snap)
        ns_ = doc.state if now else state_of(doc, new_snap)[0]
        res["geometry"] = geometry_changes(os_, ns_)
    return res


# ---------------------------------------------------------------------- whole Documents (git Versions)
NOW = None          # the "Version" name meaning the open Document as it is now, unsaved edits included
NOT_COMPARED = "Not compared: the comparison covers Part Studios."


def document_snapshot(folder) -> dict:
    """The tabs of a FolderDocument as a comparison reads them: {"tabs": [{"id", "kind", "name", "snap"}]}, in tab
    order. `snap` is `snapshot(tab.doc)` for a Part Studio and None for any other kind or an unreadable tab; an
    unreadable tab also carries "error"."""
    tabs = []
    for t in folder.tabs:
        row = {"id": t.id, "kind": t.kind, "name": t.name or (t.doc.name if t.doc is not None else t.id),
               "snap": snapshot(t.doc) if t.kind == "studio" and t.doc is not None else None}
        if t.doc is None:
            row["error"] = folder.errors.get(t.id, "This tab could not be read")
        tabs.append(row)
    return {"tabs": tabs}


def version_snapshot(folder, name) -> dict:
    """`document_snapshot` of Version `name` of `folder`, read through `folder.open_version` (a read-only scratch
    copy, closed straight after): the working folder and its open tabs are not touched."""
    view = folder.open_version(name)
    try:
        return document_snapshot(view)
    finally:
        view.close()


def compare_documents(old, new) -> dict:
    """What changed from Document snapshot `old` to `new` (see `document_snapshot`), tab by tab.

    {"tabs": [{"id", "name", "kind", "status", "lines", "features", "other", "old", "new"}]} in the newer side's tab
    order, then the removed tabs. `status` is "added", "removed", "changed", "same", "not compared" (a tab that is
    not a Part Studio) or "unreadable". `lines` are tab-level notes in words (a rename, why a tab was not compared);
    `features` and `other` are `feature_changes` and `other_changes` of a Part Studio on both sides; `old` and `new`
    are its two snapshots (None on a side that lacks it), for building each side's geometry."""
    from . import tabkinds as TK
    before = {t["id"]: t for t in old["tabs"]}
    after = {t["id"] for t in new["tabs"]}
    out = []

    def row(t, status, o=None, n=None, lines=(), features=(), other=()):
        rec = TK.find(t["kind"])
        return {"id": t["id"], "name": t["name"], "kind": t["kind"], "label": rec.label if rec else t["kind"],
                "status": status, "lines": list(lines), "features": list(features), "other": list(other),
                "old": o, "new": n}

    for t in new["tabs"]:
        o = before.get(t["id"])
        lines = [f"Renamed from “{o['name']}”"] if o is not None and o["name"] != t["name"] else []
        if t["kind"] != "studio":
            out.append(row(t, "not compared", lines=lines + [NOT_COMPARED]))
        elif t.get("error") or (o is not None and o.get("error")):
            out.append(row(t, "unreadable", lines=lines + [t.get("error") or o.get("error")]))
        elif o is None:
            out.append(row(t, "added", n=t["snap"]))
        elif o["kind"] != "studio" or o["snap"] is None:
            out.append(row(t, "added", n=t["snap"], lines=lines))
        else:
            feats, other = feature_changes(o["snap"], t["snap"]), other_changes(o["snap"], t["snap"])
            status = "changed" if feats or other or lines else "same"
            out.append(row(t, status, o["snap"], t["snap"], lines, feats, other))
    for t in old["tabs"]:
        if t["id"] not in after:
            out.append(row(t, "removed", o=t["snap"]))
    return {"tabs": out}


def compare_versions(folder, old, new=NOW, cache=None) -> dict:
    """What changed in the open Document `folder` from Version `old` to Version `new` (a name, or NOW for the open
    tabs as they are, unsaved edits included): `compare_documents` of the two sides plus "old_label"/"new_label".
    Each Version is read through `FolderDocument.open_version`; nothing is checked out. `cache` (a dict) keeps the
    Versions' snapshots between calls: a Version never changes."""
    cache = {} if cache is None else cache

    def side(name):
        if name is NOW:
            return document_snapshot(folder)
        if name not in cache:
            cache[name] = version_snapshot(folder, name)
        return cache[name]
    res = compare_documents(side(old), side(new))
    res["old_label"] = "now" if old is NOW else old
    res["new_label"] = "now" if new is NOW else new
    return res


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_html(res, old_label="before", new_label="now") -> str:
    """The comparison as simple HTML (for the History panel)."""
    h = []
    feats = res.get("features", [])
    marks = {"added": ("+", "#1e8e3e"), "removed": ("−", "#d93025"), "changed": ("~", "#1a73e8")}
    if feats:
        h.append("<p><b>Features</b></p><table cellspacing=0 cellpadding=1>")
        for c in feats:
            sym, col = marks[c["kind"]]
            what = {"added": "added", "removed": "removed", "changed": ""}[c["kind"]]
            h.append(f"<tr><td valign=top style='color:{col}; font-weight:bold; padding-right:6px'>{sym}</td>"
                     f"<td><b>{_esc(c['name'])}</b> <span style='color:#888'>{what}</span>")
            for ln in c["lines"]:
                h.append(f"<br>&nbsp;&nbsp;{_esc(ln)}")
            h.append("</td></tr>")
        h.append("</table>")
    if res.get("other"):
        h.append("<p><b>Other</b></p><ul style='margin-left:-20px'>" + "".join(
            f"<li>{_esc(x)}</li>" for x in res["other"]) + "</ul>")
    g = res.get("geometry")
    if g is not None:
        if g["rows"]:
            h.append("<p><b>Parts</b> <span style='color:#888'>(volume)</span></p><table cellspacing=0 cellpadding=1>")
            for name, a, b in g["rows"]:
                if a is None:
                    txt = f"new · {b / 1000:.2f} cm³"
                elif b is None:
                    txt = f"removed · was {a / 1000:.2f} cm³"
                else:
                    d = (b - a) / 1000
                    pct = f" ({d / (a / 1000) * 100:+.1f}%)" if a > 1e-9 else ""
                    txt = f"{a / 1000:.2f} → {b / 1000:.2f} cm³ <span style='color:{'#1e8e3e' if d >= 0 else '#d93025'}'>" \
                          f"{d:+.2f}{pct}</span>" if abs(d) > 5e-6 else "same volume, different shape"
                h.append(f"<tr><td style='padding-right:10px'>{_esc(name)}</td><td>{txt}</td></tr>")
            h.append("</table>")
        elif feats:
            h.append("<p style='color:#888'>The parts' shapes didn't change.</p>")
    if not h:
        h.append(f"<p style='color:#888'>No changes between {_esc(old_label)} and {_esc(new_label)}.</p>")
    return "".join(h)


def document_html(res) -> str:
    """A `compare_versions` result as simple HTML: a heading per tab, then what changed in it."""
    old_label, new_label = res.get("old_label", "before"), res.get("new_label", "now")
    h = [f"<p style='color:#555'>From <b>{_esc(old_label)}</b> to <b>{_esc(new_label)}</b>:</p>"]
    status_words = {"added": "added", "removed": "removed", "same": "no changes", "changed": "",
                    "not compared": "", "unreadable": "could not be read"}
    for t in res["tabs"]:
        word = status_words.get(t["status"], t["status"])
        h.append(f"<p style='margin-bottom:2px'><b>{_esc(t['name'])}</b> <span style='color:#888'>"
                 f"{_esc(t['label'])}{' · ' + word if word else ''}</span></p>")
        for ln in t["lines"]:
            h.append(f"<p style='color:#888; margin:0 0 0 10px'>{_esc(ln)}</p>")
        if t["status"] == "changed" and (t["features"] or t["other"]):
            h.append(to_html({"features": t["features"], "other": t["other"]}, old_label, new_label))
    if not res["tabs"]:
        h.append("<p style='color:#888'>This Document has no tabs.</p>")
    return "".join(h)
