"""Headless Document lifecycle smoke (slice S2): no MainWindow, no Qt, the real git binary, no network.

Part of the cumulative smoke gate (see s00_baseline.py for the convention). In a temporary directory it drives a
folder Document through create (studio, assembly, drawing) -> v1 -> Workspace -> v2 -> merge (a manifest change
on both sides, a dangling link caught in the preview) -> read-only v1 -> a second Document pinned to v1 that reports
v2 -> push to a bare repository; then shows stock git merges whole-file, that a received Document carrying nine
hostile git settings runs none of them through open, save, Create version, Workspace switch, merge and push, that a
manifest path escape or a symlinked tab is refused, and that a clean plain-git merge leaving a dangling link is
reported when the Document opens.

One line per step, ending `ok`; a failed step prints why and the script exits 1 with no `S2 OK`.

    both fs_py scripts/smoke/s2_lifecycle.py
"""
import builtins
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

TMP = tempfile.mkdtemp(prefix="s2_lifecycle_")
os.environ["FREESHAPE_DATA_DIR"] = os.path.join(TMP, "appdata")     # nothing may touch a real user folder
os.environ["XDG_DATA_HOME"] = os.path.join(TMP, "xdg")
os.environ["HOME"] = os.path.join(TMP, "home")
for _d in ("appdata", "xdg", "home"):
    os.makedirs(os.path.join(TMP, _d))

from conftest import TOP, add, rect_lines, region_refs, shape_fingerprint, sketch_feature  # noqa: E402
from freeshape import doc_folder as DF  # noqa: E402
from freeshape import doc_merge as DM  # noqa: E402
from freeshape import doc_registry as R  # noqa: E402
from freeshape import gitstore as G  # noqa: E402
from freeshape import links as L  # noqa: E402


# ---------------------------------------------------------------------- helpers
class Fail(Exception):
    pass


def check(cond, msg):
    if not cond:
        raise Fail(msg)


def step(name):
    """Run a step function; print `name ... ok`, or the reason and exit 1."""
    def deco(fn):
        try:
            fn()
        except Exception as e:                       # noqa: BLE001 -- any failure fails the gate
            print(f"{name} ... FAILED: {e}")
            if not isinstance(e, Fail):
                traceback.print_exc()
            _cleanup()
            sys.exit(1)
        print(f"{name} ... ok", flush=True)
        return fn
    return deco


def _cleanup():
    shutil.rmtree(TMP, ignore_errors=True)


def plain(args, cwd, home=None):
    """Un-hardened git, as a user with no wrapper would run it (isolated only from the machine's own config)."""
    home = home or TMP
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "HOME": str(home),
                "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@t"})
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)


def p(*parts):
    return os.path.join(TMP, *parts)


def box(studio, size, depth):
    sid = sketch_feature(studio, lambda s: rect_lines(s, 0, 0, size, size), TOP)
    add(studio, "extrude", regions=region_refs(studio, sid), depth=depth, op="new")
    check(not studio.errors, studio.errors)


def set_depth(doc, tid, depth):
    studio = doc.tab(tid).doc
    feature = next(f for f in studio.features if f["type"] == "extrude")
    feature["params"]["depth"] = depth
    studio.replace_feature(feature)
    check(not studio.errors, studio.errors)
    studio.dirty = True


def rename(doc, tid, name):
    doc.tab(tid).name = doc.tab(tid).doc.name = name
    doc.tab(tid).doc.dirty = True


def link(doc, asm_id, studio_id, name):
    studio = doc.tab(studio_id)
    asm = doc.tab(asm_id).doc
    asm.insert(L.studio_source(studio, next(iter(studio.doc.state.parts))), name=name)
    asm.refresh(solve=False, notify=False)
    asm.dirty = True


def delete_tab(doc, tid):
    file = os.path.join(doc.path, doc.tab(tid).file)
    doc.remove_tab(tid)
    os.remove(file)


def volume(doc, tid):
    return sum(round(shape_volume(p_.shape), 3) for p_ in doc.tab(tid).doc.state.parts.values())


def shape_volume(shape):
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return props.Mass()


def asm_fp(asm_doc):
    check(not asm_doc.errors, asm_doc.errors)
    return sorted(repr(shape_fingerprint(asm_doc.parts[i["id"]].shape)) for i in asm_doc.instances)


def on(doc, workspace, edit):
    """Make `edit(doc)` in `workspace` and save it (the next switch or merge commits it there)."""
    doc.switch_workspace(workspace)
    edit(doc)
    doc.save()


def no_markers(path):
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in files:
            with open(os.path.join(root, f), "rb") as fh:
                check(b"<<<<<<<" not in fh.read(), f"conflict marker in {f}")


# ---------------------------------------------------------------------- the main lifecycle
registry = R.DocRegistry(p("appdata", "documents.json"))
S = {}          # state shared between steps


@step("create")
def _():
    doc = DF.FolderDocument.create(p("Doc"), name="Doc", registry=registry)
    plate = doc.add_tab("studio", "Plate")
    box(plate.doc, 20.0, 10.0)
    bracket = doc.add_tab("studio", "Bracket")
    box(bracket.doc, 10.0, 5.0)
    asm = doc.add_tab("assembly", "Asm")
    check([plate.id, bracket.id, asm.id] == ["T1", "T2", "T3"], (plate.id, bracket.id, asm.id))
    link(doc, "T3", "T1", "Plate")
    drawing = doc.add_tab("drawing", "Dwg")
    doc.save()
    check([t.kind for t in doc.tabs] == ["studio", "studio", "assembly", "drawing"], [t.kind for t in doc.tabs])
    check(drawing.id == "T4" and not doc.errors, doc.errors)
    manifest = json.load(open(os.path.join(doc.path, "document.json")))
    check(len(manifest["tabs"]) == 4, manifest)
    for t in doc.tabs:
        check(os.path.isfile(os.path.join(doc.path, t.file)), f"missing {t.file}")
    check(G.is_repo(doc.path), "Document folder is not a git repository")
    S["doc"] = doc


@step("v1")
def _():
    doc = S["doc"]
    v = doc.create_version("v1", "base")
    check(v["name"] == "v1" and [x["name"] for x in doc.versions()] == ["v1"], doc.versions())
    check(not G.has_changes(doc.path), "tree dirty after Create version")
    asm = doc.tab("T3").doc
    S["v1_fp"] = asm_fp(asm)
    S["pid"] = next(iter(doc.tab("T1").doc.state.parts))
    S["v1_volume"] = volume(doc, "T1")
    check(S["v1_volume"] == 20 * 20 * 10, S["v1_volume"])


@step("workspace")
def _():
    doc = S["doc"]
    doc.create_workspace("alt", "v1", switch=False)
    doc.create_workspace("dangle", "v1", switch=False)
    check(sorted(doc.workspaces()) == ["alt", "dangle", "main"] or
          {"alt", "dangle", "main"} <= set(w if isinstance(w, str) else w.get("name") for w in doc.workspaces()),
          doc.workspaces())

    def alt(d):
        set_depth(d, "T1", 30.0)
        rename(d, "T3", "Asm alt")
    on(doc, "alt", alt)
    on(doc, "dangle", lambda d: link(d, "T3", "T2", "Bracket"))
    doc.switch_workspace("main")
    check(doc.current_workspace() == "main" and volume(doc, "T1") == S["v1_volume"], "main changed by other Workspaces")


@step("v2")
def _():
    doc = S["doc"]

    def main(d):
        set_depth(d, "T1", 15.0)
        rename(d, "T4", "Dwg main")
        delete_tab(d, "T2")
    main(doc)
    doc.save()
    v = doc.create_version("v2", "main edits")
    check([x["name"] for x in doc.versions()] == ["v1", "v2"] and v["name"] == "v2", doc.versions())
    check(volume(doc, "T1") == 20 * 20 * 15, volume(doc, "T1"))
    # stock-git clone of the pre-merge state, for the later "stock git merge" step
    r = plain(["clone", "--quiet", "--no-local", doc.path, p("stock")], TMP)
    check(r.returncode == 0, r.stderr)


@step("merge-preview manifest+tabs")
def _():
    doc = S["doc"]
    pv = doc.merge_preview("alt")
    check([c.key for c in pv.conflicts] == ["T1"], f"conflicts {pv.conflicts}")
    check("document.json" in pv.git_conflicts, f"git saw manifest conflict? {pv.git_conflicts}")
    check("Plate.fshape" in pv.git_conflicts, pv.git_conflicts)
    check(not pv.clean and pv.blocking, "preview should block until T1 is chosen")
    check(all(w["problem"] != "dangling-link" for w in pv.warnings), pv.warnings)
    head = G.head(doc.path)
    try:
        doc.merge_workspace("alt")
    except DM.MergeRefused:
        pass
    else:
        raise Fail("merge with an unchosen conflict was not refused")
    check(G.head(doc.path) == head, "a refused merge committed something")
    S["pre_merge_head"] = head


@step("dangling-link caught")
def _():
    doc = S["doc"]
    pv = doc.merge_preview("dangle")
    got = [(w["problem"], w["tab"], w["target"]) for w in pv.warnings]
    check(got == [("dangling-link", "T3", "T2")], got)
    check(pv.problems and not pv.clean, pv.problems)
    head = G.head(doc.path)
    try:
        doc.merge_workspace("dangle")
    except DM.MergeRefused as e:
        check("Asm" in str(e) and "T2" in str(e), str(e))
    else:
        raise Fail("merge leaving a dangling link was not refused")
    check(G.head(doc.path) == head and not G.has_changes(doc.path) and doc.current_workspace() == "main",
          "refused merge changed the Document")


@step("merge")
def _():
    doc = S["doc"]
    commit = doc.merge_workspace("alt", {"T1": "theirs"})
    check(commit, "no merge commit")
    check(volume(doc, "T1") == 20 * 20 * 30, volume(doc, "T1"))
    names = {t.id: t.name for t in doc.tabs}
    check(names.get("T3") == "Asm alt" and names.get("T4") == "Dwg main" and "T2" not in names, names)
    check(doc.current_workspace() == "main" and not doc.errors and doc.warnings == [], (doc.errors, doc.warnings))
    no_markers(doc.path)
    fresh = DF.FolderDocument.open(doc.path)
    check(not fresh.errors and fresh.warnings == [], (fresh.errors, fresh.warnings))
    for t in fresh.tabs:
        if t.kind == "studio":
            t.doc.rebuild()
            check(not t.doc.errors and t.doc.state.parts, t.doc.errors)
    check(not G.has_changes(doc.path), "tree dirty after merge")
    parents = G.run(["rev-list", "--parents", "-n", "1", "HEAD"], doc.path).stdout.split()
    check(len(parents) == 3, f"not a merge commit: {parents}")


@step("read-only v1 links at v1")
def _():
    doc = S["doc"]
    view = doc.open_version("v1")
    try:
        check(view.read_only and not view.errors, view.errors)
        asm = view.tab("T3").doc
        asm.refresh(solve=False, notify=False)
        check(asm_fp(asm) == S["v1_fp"], "v1 view's link does not resolve to v1 geometry")
        check(volume(view, "T1") == S["v1_volume"], volume(view, "T1"))
        check(asm_fp(doc.tab("T3").doc) != S["v1_fp"], "current Document did not move on (check is vacuous)")
        try:
            view.save()
        except Exception:                           # noqa: BLE001
            pass
        else:
            raise Fail("a read-only version accepted save()")
    finally:
        view.close()


@step("cross-doc pin v1 reports v2")
def _():
    a = S["doc"]
    b = DF.FolderDocument.create(p("B"), name="B", registry=registry)
    asm = b.add_tab("assembly", "Asm")
    st = a.tab("T1")
    asm.doc.insert(L.studio_source(st, S["pid"], document=a.id, version="v1"), name="Plate")
    asm.doc.refresh(solve=False, notify=False)
    check(not asm.doc.errors, asm.doc.errors)
    src = asm.doc.instances[0]["source"]
    check(src["document"] == a.id and src["version"] == "v1" and "path" not in src, src)
    shape = asm.doc.parts[asm.doc.instances[0]["id"]].shape
    check(round(shape_volume(shape), 3) == S["v1_volume"], "pinned link did not resolve to v1 geometry")
    status = b.resolver.link_status(src)
    check(status["update_available"] and status["latest"] == "v2" and status["pinned"] == "v1"
          and status["error"] is None, status)
    ups = L.cross_link_updates(b)
    check([(u["document"], u["latest"], u["tab"]) for u in ups] == [(a.id, "v2", asm.id)], ups)


@step("push bare")
def _():
    bare = p("remote.git")
    r = plain(["init", "--bare", "--quiet", bare], TMP)
    check(r.returncode == 0, r.stderr)
    S["doc"].push(bare)
    tags = plain(["-C", bare, "tag"], TMP).stdout.split()
    check("v1" in tags and "v2" in tags, tags)


@step("stock git merge whole-file conflict")
def _():
    clone = p("stock")
    r = plain(["merge", "origin/alt"], clone)
    check(r.returncode == 1, f"expected a conflict: {r.stdout} {r.stderr}")
    status = plain(["status", "--porcelain"], clone).stdout.splitlines()
    check("UU Plate.fshape" in status, status)
    text = open(os.path.join(clone, "Plate.fshape")).read()
    check("<<<<<<<" not in text and ">>>>>>>" not in text, "stock merge wrote line-level markers")
    json.loads(text)                                   # one whole file (ours), still valid


# ---------------------------------------------------------------------- hostile received Document
SENTINELS = ["filter_info", "filter_tracked", "filter_include", "merge", "diff_external", "ssh", "fsmonitor",
             "hookspath", "hook"]


def _script(base, name, body):
    path = os.path.join(base, "scripts", f"{name}.sh")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    sentinel = os.path.join(base, "sentinels", name)
    os.makedirs(os.path.dirname(sentinel), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(f"#!/bin/sh\ntouch '{sentinel}'\n{body}\n")
    os.chmod(path, 0o755)
    return path


def _edit_json(path, **extra):
    with open(path) as fh:
        data = json.load(fh)
    data.update(extra)
    with open(path, "w") as fh:
        json.dump(data, fh, indent=1)


def build_hostile(base, mutate=None):
    """A Document folder as a stranger would send it: valid content, a hostile repository (the nine I2.4 cases).
    Returns (source folder, sentinel folder)."""
    os.makedirs(base)
    src = os.path.join(base, "received")
    builder = R.DocRegistry(os.path.join(base, "builder.json"))        # the sender's machine, not ours
    doc = DF.FolderDocument.create(src, registry=builder)
    st = doc.add_tab("studio", "Plate")
    box(st.doc, 20.0, 10.0)
    asm = doc.add_tab("assembly", "Asm")
    asm.doc.insert(L.studio_source(st, next(iter(st.doc.state.parts))), name="Plate")
    asm.doc.refresh(solve=False, notify=False)
    doc.add_tab("drawing", "Dwg")
    doc.save()
    doc.create_version("v1", "sender's version")

    def git(*args):
        r = plain(list(args), src, base)
        check(r.returncode == 0, (args, r.stderr))
        return r

    studio = next(os.path.join(src, t.file) for t in doc.tabs if t.kind == "studio")
    with open(os.path.join(src, ".gitattributes"), "w") as fh:
        fh.write("document.json -merge\n*.fasm filter=evil_tracked\n*.fshape merge=evil_merge\n")
    git("add", ".gitattributes")
    git("commit", "-m", "attributes")
    git("checkout", "-q", "-b", "other")            # two commits that both change the studio: a merge calls the driver
    _edit_json(studio, _other=1)
    git("commit", "-am", "other")
    git("checkout", "-q", "main")
    _edit_json(studio, _main=1)
    git("commit", "-am", "main")
    if mutate:
        mutate(src, git)
    s = {n: _script(base, n, "cat") for n in ("filter_info", "filter_tracked", "filter_include")}
    other = {n: _script(base, n, "") for n in ("merge", "diff_external", "ssh", "fsmonitor", "hookspath", "hook")}
    os.makedirs(os.path.join(base, "hooks_dir"))
    shutil.copy(other["hookspath"], os.path.join(base, "hooks_dir", "pre-commit"))
    shutil.copy(other["hook"], os.path.join(src, ".git", "hooks", "pre-commit"))
    os.makedirs(os.path.join(src, ".git", "info"), exist_ok=True)
    with open(os.path.join(src, ".git", "info", "attributes"), "w") as fh:
        fh.write("*.fshape filter=evil_info\n*.fdwg filter=evil_include\n")
    include = os.path.join(base, "included.cfg")
    with open(include, "w") as fh:
        fh.write(f'[filter "evil_include"]\n\tclean = sh {s["filter_include"]}\n\tsmudge = sh {s["filter_include"]}\n')
    with open(os.path.join(src, ".git", "config"), "a") as fh:
        fh.write(f'''[filter "evil_info"]
	clean = sh {s["filter_info"]}
	smudge = sh {s["filter_info"]}
[filter "evil_tracked"]
	clean = sh {s["filter_tracked"]}
	smudge = sh {s["filter_tracked"]}
[merge "evil_merge"]
	driver = sh {other["merge"]} %O %A %B
[diff]
	external = sh {other["diff_external"]}
[core]
	sshCommand = sh {other["ssh"]}
	fsmonitor = sh {other["fsmonitor"]}
	hooksPath = {os.path.join(base, "hooks_dir")}
[include]
	path = {include}
''')
    return src, os.path.join(base, "sentinels")


def fired(sentinels):
    return sorted(os.listdir(sentinels)) if os.path.isdir(sentinels) else []


@step("hostile control: plain git add ran the filter")
def _():
    base = p("control")
    src, sentinels = build_hostile(base)
    check(fired(sentinels) == [], f"building the control fired {fired(sentinels)}")
    plain(["merge", "other"], src, base)                                      # merge driver
    plain(["merge", "--abort"], src, base)
    for name in os.listdir(src):                                              # dirty every tab so add has work
        if name.endswith((".fshape", ".fasm", ".fdwg")):
            with open(os.path.join(src, name), "a") as fh:
                fh.write("\n")
    plain(["add", "--all"], src, base)                                        # filter drivers, fsmonitor
    plain(["status"], src, base)
    plain(["diff", "HEAD"], src, base)                                        # diff.external
    plain(["commit", "-m", "x"], src, base)                                   # core.hooksPath hook
    plain(["-c", f"core.hooksPath={src}/.git/hooks", "commit", "--allow-empty", "-m", "y"], src, base)   # .git/hooks
    plain(["ls-remote", "ssh://example.invalid/repo"], src, base)             # core.sshCommand
    check(fired(sentinels) == sorted(SENTINELS), f"plain git fired only {fired(sentinels)}")


@step("hostile import ran nothing (9 sentinels absent)")
def _():
    base = p("recv")
    src, sentinels = build_hostile(base)
    reg = R.DocRegistry(p("app2", "documents.json"))
    root = p("app2", "documents")
    bare = p("recv_remote.git")
    check(plain(["init", "--bare", "--quiet", bare], TMP).returncode == 0, "bare init")
    before = sorted(os.listdir(src))
    doc = DF.FolderDocument.open_document(src, registry=reg, managed_root=root)                  # import by clone
    check(os.path.realpath(doc.path) != os.path.realpath(src) and not doc.errors, doc.errors)
    for tab in doc.tabs:
        if tab.doc is not None:
            tab.doc.dirty = True
    doc.save()                                                                                  # save
    doc.create_version("r1", "received and edited")                                             # Create version
    check([v["name"] for v in doc.versions()] == ["v1", "r1"], doc.versions())
    doc.open_version("v1").close()
    studio = next(t for t in doc.tabs if t.kind == "studio")
    asm = next(t for t in doc.tabs if t.kind == "assembly")
    doc.create_workspace("alt", "v1")                                                           # Workspace switch
    set_depth(doc, studio.id, 30.0)
    doc.tab(asm.id).doc.dirty = True
    doc.save()
    doc.switch_workspace("main")
    set_depth(doc, studio.id, 15.0)
    doc.save()
    pv = doc.merge_preview("alt")                                                               # merge
    check([c.key for c in pv.conflicts] == [studio.id], pv.conflicts)
    doc.merge_workspace("alt", {studio.id: "theirs"})
    check(volume(doc, studio.id) == 20 * 20 * 30, volume(doc, studio.id))
    doc.switch_workspace("alt")
    doc.switch_workspace("main")
    doc.push(bare)                                                                              # push
    tags = plain(["--git-dir", bare, "tag", "--list"], TMP).stdout.split()
    check("r1" in tags and "v1" in tags, tags)
    check(sorted(os.listdir(src)) == before, "the received folder was written to")
    check(not doc.errors, doc.errors)
    check(fired(sentinels) == [], f"hostile sentinels fired: {fired(sentinels)}")


# ---------------------------------------------------------------------- hostile content
def _attack(base):
    secret = os.path.join(base, "managed", "secret.fshape")
    os.makedirs(os.path.dirname(secret), exist_ok=True)
    with open(secret, "w") as fh:
        fh.write("{}")
    outside = os.path.join(base, "outside.fshape")
    with open(outside, "w") as fh:
        fh.write("{}")

    def mutate(src, git):
        manifest = os.path.join(src, "document.json")
        with open(manifest) as fh:
            data = json.load(fh)
        data["tabs"].append({"id": "T8", "kind": "studio", "file": "../secret.fshape", "name": "Escapes"})
        data["tabs"].append({"id": "T9", "kind": "studio", "file": "link.fshape", "name": "Symlink"})
        with open(manifest, "w") as fh:
            json.dump(data, fh, indent=1)
        os.symlink(outside, os.path.join(src, "link.fshape"))
        git("add", "--all")
        git("commit", "-m", "hostile content")
    return mutate


@step("manifest path escape refused")
def _():
    base = p("attack")
    os.makedirs(base)
    src, sentinels = build_hostile(os.path.join(base, "r"), _attack(base))
    seen = []
    real_open = builtins.open

    def spy(file, *a, **k):
        seen.append(str(file))
        return real_open(file, *a, **k)

    reg = R.DocRegistry(p("app3", "documents.json"))
    builtins.open = spy
    try:
        doc = DF.FolderDocument.open_document(src, registry=reg, managed_root=os.path.join(base, "managed"))
    finally:
        builtins.open = real_open
    S["attack_doc"] = doc
    check("'..'" in doc.errors.get("T8", "") or "outside the Document folder" in doc.errors.get("T8", ""),
          doc.errors)
    check(doc.tab("T8").doc is None, "escaping tab was opened")
    check(not any(f.endswith(("secret.fshape", "outside.fshape")) for f in seen), "a file outside the folder was read")
    check(fired(sentinels) == [], fired(sentinels))
    doc.save()
    doc.create_version("r1", "n")
    view = doc.open_version("r1")
    check("T8" in view.errors and view.tab("T8").doc is None, view.errors)
    view.close()


@step("symlinked tab refused")
def _():
    doc = S["attack_doc"]
    err = doc.errors.get("T9", "")
    check("outside the Document folder" in err or "symlink" in err, doc.errors)
    check(doc.tab("T9").doc is None, "symlinked tab was opened")
    check(os.path.islink(os.path.join(doc.path, "link.fshape")), "the clone dropped the tracked symlink")


# ---------------------------------------------------------------------- plain-git clean merge
@step("plain-git clean merge: dangling link reported on open")
def _():
    reg = R.DocRegistry(p("app4", "documents.json"))
    doc = DF.FolderDocument.create(p("PG"), registry=reg)
    box(doc.add_tab("studio", "Plate").doc, 20.0, 10.0)
    box(doc.add_tab("studio", "Bracket").doc, 10.0, 5.0)
    asm = doc.add_tab("assembly", "Asm")
    plate = doc.tab("T1")
    asm.doc.insert(L.studio_source(plate, next(iter(plate.doc.state.parts))), name="Plate")
    doc.save()
    doc.create_version("v1", "base")
    doc.create_workspace("alt", "v1")
    bracket = doc.tab("T2")
    doc.tab("T3").doc.insert(L.studio_source(bracket, next(iter(bracket.doc.state.parts))), name="Bracket")
    doc.tab("T3").doc.dirty = True
    doc.save()
    doc.switch_workspace("main")
    delete_tab(doc, "T2")
    doc.save()
    G.commit_all(doc.path, "delete Bracket")
    clone = p("PG_plain")
    r = plain(["clone", "--quiet", "--no-local", doc.path, clone], TMP)
    check(r.returncode == 0, r.stderr)
    r = plain(["merge", "--no-edit", "origin/alt"], clone)
    check(r.returncode == 0 and "CONFLICT" not in r.stdout, (r.stdout, r.stderr))
    opened = DF.FolderDocument.open(clone)
    got = [(w["problem"], w["tab"], w["target"]) for w in opened.warnings]
    check(got == [("dangling-link", "T3", "T2")], got)
    msg = opened.warnings[0]["message"]
    check("Asm" in msg and "T2" in msg and "Bracket" in msg, msg)
    check(opened.tab("T3").doc is not None and not opened.errors, opened.errors)


print("S2 OK")
_cleanup()
