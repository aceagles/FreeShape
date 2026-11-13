"""I2.4: a received Document is hostile. Its repository names a filter driver (through `.git/info/attributes`, through a
tracked `.gitattributes`, and through an `include.path` file), a merge driver, `diff.external`, `core.sshCommand`,
`core.fsmonitor`, `core.hooksPath` with a hook and a hook in `.git/hooks`, each pointing at a script that writes
its own sentinel file. The control test shows plain git really runs every one of them; the test proper shows that
opening (which imports), saving, Create version, listing versions, opening a version and pushing run none.

Every script only ever writes a file under pytest's tmp_path."""
import builtins
import json
import os
import shutil
import subprocess

import pytest

from conftest import TOP, add, rect_lines, region_refs, sketch_feature
from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import gitstore as G
from freeshape import kernel as K
from freeshape import links as L

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

SENTINELS = ["filter_info", "filter_tracked", "filter_include", "merge", "diff_external", "ssh", "fsmonitor",
             "hookspath", "hook"]


def plain(args, cwd, base):
    """Un-hardened git, as a user with no wrapper would run it (only isolated from the machine's own config)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "HOME": str(base),
                "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@t"})
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)


def _script(base, name, body):
    path = base / "scripts" / f"{name}.sh"
    path.parent.mkdir(exist_ok=True)
    sentinel = base / "sentinels" / name
    sentinel.parent.mkdir(exist_ok=True)
    path.write_text(f"#!/bin/sh\ntouch '{sentinel}'\n{body}\n")
    path.chmod(0o755)
    return path


def _studio_with_box(doc):
    st = doc.add_tab("studio", "Plate")
    sid = sketch_feature(st.doc, lambda s: rect_lines(s, 0, 0, 20, 20), TOP)
    add(st.doc, "extrude", regions=region_refs(st.doc, sid), depth=10.0, op="new")
    pid = next(iter(st.doc.state.parts))
    asm = doc.add_tab("assembly", "Asm")
    asm.doc.insert(L.studio_source(st, pid), name="Plate")
    asm.doc.refresh(solve=False, notify=False)
    doc.add_tab("drawing", "Dwg")
    return st


def _edit_json(path, **extra):
    with open(path) as fh:
        data = json.load(fh)
    data.update(extra)
    with open(path, "w") as fh:
        json.dump(data, fh, indent=1)


def build_hostile(base, mutate=None):
    """A Document folder as a stranger would send it: valid content, a hostile repository. `mutate(src, git)` runs
    on the content before the hostile config goes in, `git(*args)` being plain git in the source."""
    base.mkdir(exist_ok=True)
    src = base / "received"
    builder = R.DocRegistry(str(base / "builder.json"))        # the sender's machine, not ours
    doc = DF.FolderDocument.create(str(src), registry=builder)
    _studio_with_box(doc)
    doc.save()
    doc.create_version("v1", "sender's version")

    def git(*args):
        r = plain(list(args), str(src), base)
        assert r.returncode == 0, (args, r.stderr)
        return r

    studio = next(src / t.file for t in doc.tabs if t.kind == "studio")
    asm = next(src / t.file for t in doc.tabs if t.kind == "assembly")
    # a tracked .gitattributes naming a filter driver (on the assembly) and a merge driver (on the studio)
    (src / ".gitattributes").write_text("document.json -merge\n*.fasm filter=evil_tracked\n"
                                        "*.fshape merge=evil_merge\n")
    git("add", ".gitattributes")
    git("commit", "-m", "attributes")
    # two commits that both change the studio, so a merge has to call the merge driver
    git("checkout", "-q", "-b", "other")
    _edit_json(studio, _other=1)
    git("commit", "-am", "other")
    git("checkout", "-q", "main")
    _edit_json(studio, _main=1)
    git("commit", "-am", "main")
    if mutate:
        mutate(src, git)
    # now the repository turns hostile
    s = {n: _script(base, n, "cat") for n in ("filter_info", "filter_tracked", "filter_include")}
    other = {n: _script(base, n, "") for n in ("merge", "diff_external", "ssh", "fsmonitor", "hookspath", "hook")}
    (base / "hooks_dir").mkdir()
    shutil.copy(other["hookspath"], base / "hooks_dir" / "pre-commit")
    shutil.copy(other["hook"], src / ".git" / "hooks" / "pre-commit")
    (src / ".git" / "info").mkdir(exist_ok=True)
    (src / ".git" / "info" / "attributes").write_text("*.fshape filter=evil_info\n*.fdwg filter=evil_include\n")
    include = base / "included.cfg"
    include.write_text(f'[filter "evil_include"]\n\tclean = sh {s["filter_include"]}\n\tsmudge = sh {s["filter_include"]}\n')
    with open(src / ".git" / "config", "a") as fh:
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
	hooksPath = {base / "hooks_dir"}
[include]
	path = {include}
''')
    return src, base / "sentinels"


def _fired(sentinels):
    return sorted(os.listdir(sentinels)) if sentinels.exists() else []


def _make_dirty(src_or_doc_path, base):
    """Touch every tab file so a plain `git add` has something to filter."""
    for name in os.listdir(src_or_doc_path):
        if name.endswith((".fshape", ".fasm", ".fdwg")):
            p = os.path.join(src_or_doc_path, name)
            with open(p, "a") as fh:
                fh.write("\n")


def test_control_plain_git_runs_every_hostile_vector(tmp_path):
    """Proves the repository really is hostile: with no wrapper, plain git writes every sentinel."""
    src, sentinels = build_hostile(tmp_path / "control")
    base = tmp_path / "control"
    assert _fired(sentinels) == []                                   # building it fired nothing
    plain(["merge", "other"], str(src), base)                        # the merge driver
    plain(["merge", "--abort"], str(src), base)
    _make_dirty(str(src), base)
    plain(["add", "--all"], str(src), base)                          # filter drivers (clean) and fsmonitor
    plain(["status"], str(src), base)
    plain(["diff", "HEAD"], str(src), base)                          # diff.external
    plain(["commit", "-m", "x"], str(src), base)                     # core.hooksPath hook
    plain(["-c", f"core.hooksPath={src}/.git/hooks", "commit", "--allow-empty", "-m", "y"], str(src), base)
    plain(["ls-remote", "ssh://example.invalid/repo"], str(src), base)   # core.sshCommand
    assert _fired(sentinels) == sorted(SENTINELS)


def _bare_remote(tmp_path):
    bare = tmp_path / "remote.git"
    r = plain(["init", "--bare", "--quiet", str(bare)], str(tmp_path), tmp_path)
    assert r.returncode == 0, r.stderr
    return bare


def _drive(tmp_path, src, sentinels, bare):
    """Open (which imports), save, Create version, list, open a version read-only, push."""
    registry = R.DocRegistry(str(tmp_path / "app" / "documents.json"))
    root = tmp_path / "app" / "documents"
    with G.recording() as calls:
        doc = DF.FolderDocument.open_document(str(src), registry=registry, managed_root=str(root))
        for tab in doc.tabs:
            if tab.doc is not None:
                tab.doc.dirty = True
        doc.save()
        doc.create_version("r1", "received and edited")
        names = [v["name"] for v in doc.versions()]
        view = doc.open_version("v1")
        doc.push(str(bare))
        again = doc.open_version("r1")
    return doc, view, again, names, calls


def test_hostile_document_runs_nothing_through_the_app(tmp_path):
    src, sentinels = build_hostile(tmp_path / "recv")
    bare = _bare_remote(tmp_path)
    before = sorted(os.listdir(src))
    doc, view, again, names, calls = _drive(tmp_path, src, sentinels, bare)
    assert _fired(sentinels) == []
    assert names == ["v1", "r1"] and view.read_only and again.read_only
    assert not doc.errors and not view.errors
    assert os.path.realpath(doc.path) != os.path.realpath(src)
    assert os.path.dirname(doc.path) == os.path.realpath(tmp_path / "app" / "documents")
    assert sorted(os.listdir(src)) == before                          # the received folder was never written to
    # the push arrived
    r = plain(["--git-dir", str(bare), "tag", "--list"], str(tmp_path), tmp_path)
    assert "r1" in r.stdout and "v1" in r.stdout

    # every git call: hardened, and none runs in or against the received folder
    root_src = os.path.realpath(src)
    clones = [c for c in calls if "clone" in c["argv"]]
    assert len(clones) == 1 and "--no-local" in clones[0]["argv"]
    assert os.path.realpath(clones[0]["cwd"]) == os.path.realpath(tmp_path / "app" / "documents")
    for c in calls:
        argv, cwd = c["argv"], c["cwd"]
        assert argv[0] == "git" and "protocol.allow=never" in argv and "core.fsmonitor=" in argv
        assert any(a.startswith("core.hooksPath=") for a in argv)
        if cwd is not None:
            assert os.path.commonpath([root_src, os.path.realpath(cwd)]) != root_src, c
        for a in argv:
            assert not a.startswith("--git-dir") and a != "-C", c
            if c not in clones:
                assert root_src not in os.path.realpath(a) if os.path.isabs(a) else True, c
        allows_file = "protocol.file.allow=always" in argv
        assert allows_file == (c in clones or "push" in argv), c
    # the child environment is the isolated one
    assert all(G._env()[k] == v for k, v in (("GIT_CONFIG_NOSYSTEM", "1"), ("GIT_CONFIG_GLOBAL", os.devnull)))


def test_hostile_clone_has_no_driver_definitions(tmp_path):
    src, sentinels = build_hostile(tmp_path / "recv")
    doc, view, again, names, calls = _drive(tmp_path, src, sentinels, _bare_remote(tmp_path))
    cfg = open(os.path.join(doc.path, ".git", "config")).read()
    assert "evil" not in cfg and "sshCommand" not in cfg and "include" not in cfg and "fsmonitor" not in cfg
    assert os.listdir(os.path.join(doc.path, ".git", "hooks")) == [] or all(
        n.endswith(".sample") for n in os.listdir(os.path.join(doc.path, ".git", "hooks")))
    assert "evil_tracked" in open(os.path.join(doc.path, ".gitattributes")).read()    # tracked, but nothing to run


def test_overrides_alone_do_not_protect_git_run_inside_the_received_folder(tmp_path):
    """The wrapper's overrides are a second layer: a filter driver the `-c` list does not name still fires if git is
    run inside the received folder through the wrapper. Import is what protects, which is why the app never does."""
    src, sentinels = build_hostile(tmp_path / "recv")
    _make_dirty(str(src), tmp_path)
    G.run(["add", "--all"], str(src))                                  # deliberately the wrong thing to do
    assert "filter_info" in _fired(sentinels) or "filter_tracked" in _fired(sentinels)


# ---------------------------------------------------------------------- content of a received Document
def _manifest_attack(base):
    secret = base / "managed" / "secret.fshape"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("{}")
    outside = base / "outside.fshape"
    outside.write_text("{}")

    def mutate(src, git):
        manifest = src / "document.json"
        with open(manifest) as fh:
            data = json.load(fh)
        data["tabs"].append({"id": "T8", "kind": "studio", "file": "../secret.fshape", "name": "Escapes"})
        data["tabs"].append({"id": "T9", "kind": "studio", "file": "link.fshape", "name": "Symlink"})
        manifest.write_text(json.dumps(data, indent=1))
        os.symlink(str(outside), src / "link.fshape")
        git("add", "--all")
        git("commit", "-m", "hostile content")
    return mutate


def test_escaping_manifest_entry_and_symlinked_tab_are_refused_without_reading_the_target(tmp_path):
    base = tmp_path / "recv"
    base.mkdir()
    src, sentinels = build_hostile(base, _manifest_attack(base))
    seen = []
    real_open = builtins.open

    def spy(file, *a, **k):
        seen.append(str(file))
        return real_open(file, *a, **k)

    registry = R.DocRegistry(str(tmp_path / "app" / "documents.json"))
    builtins.open = spy
    try:
        doc = DF.FolderDocument.open_document(str(src), registry=registry,
                                              managed_root=str(base / "managed"))
    finally:
        builtins.open = real_open
    assert "'..'" in doc.errors["T8"] or "outside the Document folder" in doc.errors["T8"]
    assert "outside the Document folder" in doc.errors["T9"] or "symlink" in doc.errors["T9"]
    assert doc.tab("T8").doc is None and doc.tab("T9").doc is None
    assert os.path.islink(os.path.join(doc.path, "link.fshape"))      # the clone recreated the tracked symlink
    assert not any(f.endswith(("secret.fshape", "outside.fshape")) for f in seen)
    assert _fired(sentinels) == []
    # ... and the same when the version is opened read-only
    doc.save()
    doc.create_version("r1", "n")
    view = doc.open_version("r1")
    assert "T8" in view.errors and "T9" in view.errors
    assert view.tab("T8").doc is None and view.tab("T9").doc is None


def test_workspace_create_switch_and_back_in_hostile_document_fires_nothing(tmp_path):
    """Checkout runs smudge filters, so this is the case that matters: a Workspace made in the imported hostile
    Document, switched to and back, leaves every sentinel unwritten."""
    src, sentinels = build_hostile(tmp_path / "recv")
    registry = R.DocRegistry(str(tmp_path / "app" / "documents.json"))
    doc = DF.FolderDocument.open_document(str(src), registry=registry, managed_root=str(tmp_path / "app" / "documents"))
    for tab in doc.tabs:
        if tab.doc is not None:
            tab.doc.dirty = True
    doc.save()
    doc.create_workspace("alt", "v1")
    assert doc.current_workspace() == "alt" and not doc.errors
    doc.tab(doc.tabs[0].id).doc.dirty = True
    doc.save()                                                    # a change on the new Workspace, committed by the switch
    doc.switch_workspace("main")
    doc.switch_workspace("alt")
    doc.switch_workspace("main")
    assert doc.current_workspace() == "main" and not doc.errors
    assert _fired(sentinels) == []
