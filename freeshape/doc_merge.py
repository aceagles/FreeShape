"""doc_merge: merge one Workspace (git branch) of a folder Document into the current one. Headless -- no Qt.

Git does the merge bookkeeping: `git merge-tree --write-tree` (gitstore.merge_tree) lists the conflicting paths
without touching the work tree, and the merge is committed with `commit-tree` and checked out with a fast-forward.
A Document's `.gitattributes` marks every tab file and document.json `-merge`, so git never writes conflict markers
into them: a conflict is a whole file and the person picks ours or theirs per tab.

The app does not rely on that attribute for the content, though. A received Document carries its own tracked
`.gitattributes`, which may name another merge driver (none is defined in the imported clone, so git falls back to
its text merge) or none at all. So every path is also classified three-way from the trees themselves (base, ours,
theirs blob ids): changed on one side only -> taken without asking; changed on both sides to different results -> a
conflict, whatever git said. The result tree is assembled from whole blobs, never from git's merged content.

document.json is merged as a union by tab id, three-way per entry: a tab added on either side is kept, a tab
removed on one side and untouched on the other is removed, and an entry changed on both sides is a conflict of that
tab. Tab ids are a per-Document counter, so two Workspaces that each add a tab usually give it the same id: the
tab added on the theirs side is then re-keyed (a fresh id, a fresh file name if that clashes too, its `_tab_id`
stamp rewritten, and every link to it from a file changed on the theirs side rewritten).

Before the merge is committed the merged tree is written to a scratch folder and opened as a Document, and its
`document_warnings` (the same checks every open runs: manifest vs folder, dangling links) are the preview's
problems. The merge is committed only when every conflict has a choice and there are no problems.

FreeCAD has no branch merge for its documents (an .FCStd is a single zip), so there was nothing there to port; the
per-file ours/theirs choice is git's own `--ours` / `--theirs` checkout semantics applied per tab.
"""
from __future__ import annotations

import json
import os
import posixpath
import tempfile

from . import doc_folder as DF
from . import gitstore as G
from . import kernel as K

MANIFEST = DF.MANIFEST_NAME
SIDES = ("ours", "theirs")


class MergeRefused(K.KernelError):
    """The merge was not committed: `problems` lists why (unchosen conflicts, dangling links, ...)."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("Merge refused:\n" + "\n".join(f"• {p}" for p in self.problems))


class Conflict:
    """A tab (or a non-tab file) changed on both sides. `key` is the tab id, or "file:<path>"."""

    def __init__(self, key, name, kind, detail, paths):
        self.key, self.name, self.kind, self.detail, self.paths = key, name, kind, detail, paths

    def __repr__(self):
        return f"Conflict({self.key!r}, {self.name!r}, {self.detail!r})"


def _entry(e):
    return {"id": e.get("id"), "kind": e.get("kind"), "file": e.get("file"), "name": e.get("name")}


def _three_way(b, o, t):
    """(result, conflict?) for one value: one-sided changes win, equal changes agree, different changes conflict."""
    if o == t:
        return o, False
    if o == b:
        return t, False
    if t == b:
        return o, False
    return None, True


def _norm(rel):
    return posixpath.normpath(rel.replace("\\", "/")) if isinstance(rel, str) and rel else None


def _rewrite_links(data, mapping):
    """Re-point every intra-Document link `{"kind", "tab"}` in a parsed tab file through `mapping`. True if any."""
    changed = False
    if isinstance(data, dict):
        if data.get("kind") in ("studio", "assembly") and data.get("tab") in mapping and not data.get("document"):
            data["tab"] = mapping[data["tab"]]
            changed = True
        for v in data.values():
            changed = _rewrite_links(v, mapping) or changed
    elif isinstance(data, list):
        for v in data:
            changed = _rewrite_links(v, mapping) or changed
    return changed


class _Side:
    """One commit's tree and manifest."""

    def __init__(self, path, commit):
        self.commit = commit
        self.files = G.tree_entries(path, commit)
        self.manifest = {}
        self.entries = {}
        if MANIFEST in self.files:
            try:
                self.manifest = json.loads(G.read_blob(path, self.files[MANIFEST][1]).decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as e:
                raise K.KernelError(f"{MANIFEST} at {commit[:10]} is not readable: {e}") from e
        if not isinstance(self.manifest, dict):
            self.manifest = {}
        tabs = self.manifest.get("tabs")
        for e in tabs if isinstance(tabs, list) else []:
            if isinstance(e, dict) and isinstance(e.get("id"), str):
                self.entries[e["id"]] = _entry(e)
        self.order = list(self.entries)


class MergePreview:
    """What merging Workspace `theirs` into `ours` gives. `conflicts` need a choice each (`choose`); `auto` lists the
    tabs taken from one side without asking; `problems` are the merged tree's Document warnings; `clean` says the
    merge may be committed. `git_conflicts` is what `git merge-tree` itself reported."""

    def __init__(self, folder, ours, theirs):
        self.folder, self.ours, self.theirs = folder, ours, theirs
        path = folder.path
        self.head = G.head(path)
        self.other = G.resolve_branch(path, theirs)
        self.base = G.merge_base(path, self.head, self.other)
        if self.base is None:
            raise G.GitError(f"Workspaces “{ours}” and “{theirs}” share no history")
        self.up_to_date = self.base == self.other
        self.choices = {}
        self.conflicts, self.auto, self.problems, self.warnings, self.git_conflicts = [], [], [], [], []
        self.renamed = {}           # theirs tab id -> new id, for tabs re-keyed because both sides added that id
        self._moved = set()         # theirs paths moved away by that re-keying
        if self.up_to_date:
            return
        self.git_tree, self.git_conflicts = G.merge_tree(path, self.head, self.other)
        self._b, self._o, self._t = (_Side(path, c) for c in (self.base, self.head, self.other))
        self._rekey_theirs()
        self._classify()
        self._evaluate()

    # ------------------------------------------------------------------ choices
    @property
    def unresolved(self):
        return [c for c in self.conflicts if c.key not in self.choices]

    @property
    def blocking(self):
        """Everything that stops the merge, as messages."""
        out = [f"Choose ours (“{self.ours}”) or theirs (“{self.theirs}”) for “{c.name}”" for c in self.unresolved]
        return out + [w["message"] for w in self.warnings]

    @property
    def clean(self):
        return not self.up_to_date and not self.blocking

    def conflict(self, key):
        return next((c for c in self.conflicts if c.key == key), None)

    def choose(self, key, side, evaluate=True):
        if side not in SIDES:
            raise ValueError(f"side must be 'ours' or 'theirs', not {side!r}")
        if self.conflict(key) is None:
            raise K.KernelError(f"“{key}” is not a conflict of this merge")
        self.choices[key] = side
        if evaluate:
            self._evaluate()

    def choose_all(self, choices):
        for key, side in choices.items():
            self.choose(key, side, evaluate=False)
        if choices and not self.up_to_date:
            self._evaluate()

    # ------------------------------------------------------------------ re-keying tabs added on both sides
    def _fresh_id(self, used):
        n = max([int("".join(c for c in i if c.isdigit()) or 0) for i in used] + [0])
        while f"T{n}" in used:
            n += 1
        return f"T{n}"

    def _rekey_theirs(self):
        b, o, t = self._b, self._o, self._t
        path = self.folder.path
        used_ids = set(b.entries) | set(o.entries) | set(t.entries)
        used_paths = set(b.files) | set(o.files) | set(t.files)
        moved = {}
        for tid in list(t.order):
            te = t.entries[tid]
            if tid in b.entries:
                continue
            oe = o.entries.get(tid)
            same = oe is not None and oe == te and o.files.get(_norm(te["file"])) == t.files.get(_norm(te["file"]))
            file_clash = (_norm(te["file"]) in o.files and _norm(te["file"]) not in b.files and
                          o.files[_norm(te["file"])] != t.files.get(_norm(te["file"])))
            if same or (oe is None and not file_clash):
                continue
            new_id = tid if oe is None else self._fresh_id(used_ids)
            used_ids.add(new_id)
            old_file = _norm(te["file"])
            new_file = old_file
            if file_clash:
                stem, ext = posixpath.splitext(old_file)
                n = 2
                while f"{stem} {n}{ext}" in used_paths:
                    n += 1
                new_file = f"{stem} {n}{ext}"
                used_paths.add(new_file)
            if new_file != old_file:
                self._moved.add(old_file)
            if old_file in t.files:
                mode, sha = t.files.pop(old_file)
                try:
                    data = json.loads(G.read_blob(path, sha).decode("utf-8"))
                    if isinstance(data, dict) and new_id != tid:
                        data["_tab_id"] = new_id
                        sha = G.write_blob(path, json.dumps(data, indent=1).encode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    pass
                t.files[new_file] = (mode, sha)
            t.entries.pop(tid)
            t.entries[new_id] = dict(te, id=new_id, file=new_file if old_file else te["file"])
            t.order[t.order.index(tid)] = new_id
            if new_id != tid:
                moved[tid] = new_id
        self.renamed = moved
        if not moved:
            return
        # links to a re-keyed tab can only come from files the theirs side changed (the id is new on that side)
        for rel, (mode, sha) in list(t.files.items()):
            if not rel.endswith((".fasm", ".fdwg")) or b.files.get(rel) == (mode, sha):
                continue
            try:
                data = json.loads(G.read_blob(path, sha).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if _rewrite_links(data, moved):
                t.files[rel] = (mode, G.write_blob(path, json.dumps(data, indent=1).encode("utf-8")))

    # ------------------------------------------------------------------ classification
    def _owner(self):
        owner = {}
        for side in (self._b, self._t, self._o):          # ours last: it wins a disagreement
            for tid, e in side.entries.items():
                if _norm(e["file"]):
                    owner[_norm(e["file"])] = tid
        return owner

    def _classify(self):
        b, o, t = self._b, self._o, self._t
        owner = self._owner()
        paths = (set(b.files) | set(o.files) | set(t.files)) - {MANIFEST}
        self._paths = {}                 # path -> (result or None, conflict?)
        for p in sorted(paths):
            res, clash = _three_way(b.files.get(p), o.files.get(p), t.files.get(p))
            if p in self.git_conflicts and p not in self._moved and o.files.get(p) != t.files.get(p):
                clash = True
            self._paths[p] = (res, clash)
        self._entries = {}
        ids = list(dict.fromkeys(o.order + t.order + b.order))
        for tid in ids:
            self._entries[tid] = _three_way(b.entries.get(tid), o.entries.get(tid), t.entries.get(tid))
        tab_paths = {}
        for p, tid in owner.items():
            if p in self._paths:
                tab_paths.setdefault(tid, []).append(p)
        self._tab_paths = tab_paths
        for tid in ids:
            mine = tab_paths.get(tid, [])
            clash = self._entries[tid][1] or any(self._paths[p][1] for p in mine)
            name = next((s.entries[tid]["name"] for s in (o, t, b) if tid in s.entries), tid)
            kind = next((s.entries[tid]["kind"] for s in (o, t, b) if tid in s.entries), None)
            states = [self._state(tid, s) for s in (o, t)]
            if clash:
                self.conflicts.append(Conflict(tid, name, kind, f"{states[0]} in “{self.ours}”, {states[1]} in "
                                                                f"“{self.theirs}”", mine))
                continue
            changed = [side for side, s in zip(SIDES, (o, t)) if self._changed(tid, s)]
            if changed:
                self.auto.append({"key": tid, "name": name, "kind": kind,
                                  "side": changed[0] if len(changed) == 1 else "both",
                                  "detail": " and ".join(f"{self._state(tid, s)} in “{w}”" for w, s in
                                                         zip((self.ours, self.theirs), (o, t)) if self._changed(tid, s))})
        for p, (res, clash) in self._paths.items():
            if p in owner:
                continue
            if clash:
                self.conflicts.append(Conflict(f"file:{p}", p, None, f"changed in “{self.ours}” and in "
                                                                     f"“{self.theirs}”", [p]))

    def _changed(self, tid, side):
        b = self._b
        if side.entries.get(tid) != b.entries.get(tid):
            return True
        return any(side.files.get(p) != b.files.get(p) for p in self._tab_paths.get(tid, []))

    def _state(self, tid, side):
        if tid not in side.entries:
            return "deleted" if tid in self._b.entries else "absent"
        if tid not in self._b.entries:
            return "added"
        return "changed" if self._changed(tid, side) else "unchanged"

    # ------------------------------------------------------------------ the merged tree
    def _side(self, name):
        return self._o if name == "ours" else self._t

    def merged_files(self):
        """{path: (mode, blob id)} of the merged tree for the current choices (an unchosen conflict counts as ours,
        for the preview only), with the merged document.json blob."""
        path = self.folder.path
        files = {}
        pick = {}
        for c in self.conflicts:
            side = self._side(self.choices.get(c.key, "ours"))
            for p in c.paths:
                pick[p] = side
        for p, (res, clash) in self._paths.items():
            if p in pick:
                res = pick[p].files.get(p)
            elif clash:
                res = self._o.files.get(p)
            if res is not None:
                files[p] = res
        manifest = self._merged_manifest()
        files[MANIFEST] = ("100644", G.write_blob(path, (json.dumps(manifest, indent=1)).encode("utf-8")))
        return files

    def _merged_manifest(self):
        b, o, t = self._b, self._o, self._t
        data = dict(o.manifest) if isinstance(o.manifest, dict) else {}
        for key in ("name",):
            val, clash = _three_way(b.manifest.get(key), o.manifest.get(key), t.manifest.get(key))
            data[key] = o.manifest.get(key) if clash else val
        tabs = []
        for tid, (res, clash) in self._entries.items():
            if clash or self.conflict(tid) is not None:
                res = self._side(self.choices.get(tid, "ours")).entries.get(tid)
            if res is not None:
                tabs.append(res)
        data["tabs"] = tabs
        return data

    def _evaluate(self):
        """Write the merged tree for the current choices to a scratch folder, open it, and keep its warnings."""
        path = self.folder.path
        self.tree = G.write_tree(path, self.merged_files())
        scratch = tempfile.mkdtemp(prefix="freeshape-merge-")
        try:
            folder = os.path.join(scratch, "Document")
            os.makedirs(folder)
            G.read_tree(path, self.tree, folder)
            try:
                view = DF.FolderDocument.open(folder, registry=self.folder.registry)
                self.warnings = view.warnings
            except (K.KernelError, OSError, ValueError) as e:
                self.warnings = [{"problem": "unreadable", "tab": None, "tab_name": None, "target": None,
                                  "message": f"The merged Document does not open: {e}"}]
        finally:
            DF._rmtree(scratch)
        self.problems = [w["message"] for w in self.warnings]


def preview(folder, ours, theirs):
    return MergePreview(folder, ours, theirs)


def commit(folder, p):
    """Commit a clean preview as a merge of HEAD and the other Workspace and check it out. Returns the commit id, or
    None when there was nothing to merge. Raises MergeRefused otherwise."""
    if p.up_to_date:
        return None
    if G.head(folder.path) != p.head:
        raise MergeRefused(["The Workspace changed since the preview: preview the merge again"])
    if not p.clean:
        raise MergeRefused(p.blocking)
    message = f"Merge Workspace {p.theirs} into {p.ours}"
    picks = [f"{c.name}: {p.choices[c.key]}" for c in p.conflicts]
    if picks:
        message += "\n\n" + "\n".join(picks)
    c = G.commit_tree(folder.path, p.tree, [p.head, p.other], message)
    G.fast_forward(folder.path, c)
    return c
