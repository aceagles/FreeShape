"""gitstore: the one module that runs git. Every call a Document makes goes through `run`.

A Document folder is under git (ADR 0001) and a Document someone sends you carries its own `.git/config`,
`.git/info/attributes` and tracked `.gitattributes`. Those can name filter drivers (run on add and checkout),
merge drivers, `diff.external`, `core.sshCommand`, `core.fsmonitor`, hooks and an `include.path` file, and a list
of `-c` overrides cannot name every key that runs code. git's own advice (git-scm.com/docs/git#_security) is that
running git inside a `.git` from an untrusted source is not safe, and that the safe step is `git clone --no-local`,
which copies no config and no hooks.

So the protection is *import*: `clone` below copies a received Document into a folder the app manages, and the
app then works only in that clone -- it never runs git with the received folder as its working directory or git
dir (the one `clone` call names it as a source argument only). A clone has no driver definitions and no hooks.

What `run` adds on top is a second layer, not the protection: `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`,
every inherited `GIT_*` variable dropped, `-c core.hooksPath=<empty dir> -c core.fsmonitor= -c protocol.allow=never`,
`--` before user-derived arguments, ref names checked with `git check-ref-format`, and git older than 2.38 refused.
`protocol.allow=never` also blocks the `file` transport, so `clone --no-local` and `push` to a local bare
repository pass `allow_file=True` (`protocol.file.allow=always`), and nothing else does.

The commit identity comes from the environment `run` builds, never from a user's git config, so a commit works
with no global config and no identity.
"""
from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import tempfile

from . import kernel as K

MIN_GIT = (2, 38)
IDENTITY = ("FreeShape", "freeshape@localhost")
DEFAULT_BRANCH = "main"
_HEX = re.compile(r"^[0-9a-f]{40,64}$")


class GitError(K.KernelError):
    """A git call failed, or git itself is not usable."""


class GitUnavailable(GitError):
    """git is not installed or older than MIN_GIT."""


_state = {"empty_hooks": None, "checked": False, "recorder": None}


def set_recorder(fn):
    """Install `fn(argv, cwd, env)` to see every git call (tests), or None to stop. Returns the previous one."""
    old, _state["recorder"] = _state["recorder"], fn
    return old


@contextlib.contextmanager
def recording():
    """Yield a list that collects `{"argv", "cwd"}` for every git call made inside the block."""
    calls = []
    old = set_recorder(lambda argv, cwd, env: calls.append({"argv": list(argv), "cwd": cwd}))
    try:
        yield calls
    finally:
        set_recorder(old)


def _empty_hooks_dir():
    d = _state["empty_hooks"]
    if d is None or not os.path.isdir(d):
        d = _state["empty_hooks"] = tempfile.mkdtemp(prefix="freeshape-nohooks-")
    return d


def _env():
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0",
                "GIT_AUTHOR_NAME": IDENTITY[0], "GIT_AUTHOR_EMAIL": IDENTITY[1],
                "GIT_COMMITTER_NAME": IDENTITY[0], "GIT_COMMITTER_EMAIL": IDENTITY[1],
                "LC_ALL": "C"})
    return env


def _argv(args, allow_file):
    cmd = ["git", "-c", f"core.hooksPath={_empty_hooks_dir()}", "-c", "core.fsmonitor=",
           "-c", "protocol.allow=never"]
    if allow_file:
        cmd += ["-c", "protocol.file.allow=always"]
    return cmd + list(args)


def run(args, cwd=None, *, allow_file=False, check=True, text=True):
    """Run `git <args>` with the hardened environment and return the CompletedProcess. `allow_file` is for
    `clone --no-local` and `push` to a local bare repository only."""
    if not _state["checked"]:
        _check_version()
    return _run(args, cwd, allow_file, check, text)


def _run(args, cwd, allow_file, check, text):
    argv, env = _argv(args, allow_file), _env()
    if _state["recorder"] is not None:
        _state["recorder"](argv, cwd, env)
    try:
        r = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=text, stdin=subprocess.DEVNULL)
    except OSError as e:
        raise GitUnavailable(f"git could not be run: {e}") from e
    if check and r.returncode != 0:
        err = r.stderr if text else r.stderr.decode("utf-8", "replace")
        raise GitError(f"git {args[0] if args else ''} failed: {err.strip()}")
    return r


def _check_version():
    r = _run(["--version"], None, False, True, True)
    m = re.search(r"(\d+)\.(\d+)", r.stdout)
    if not m or (int(m.group(1)), int(m.group(2))) < MIN_GIT:
        raise GitUnavailable(f"git {'.'.join(map(str, MIN_GIT))} or newer is required, found {r.stdout.strip()!r}")
    _state["checked"] = True


# ---------------------------------------------------------------------- names
def check_name(name, what="name"):
    """Reject a version name that is not a safe tag name, before any git runs for the cheap cases and with
    `git check-ref-format` for the rest. Returns the name."""
    if not isinstance(name, str) or not name.strip():
        raise GitError(f"A version {what} is required")
    if (name != name.strip() or name.startswith(("-", "/", ".")) or ".." in name or "\\" in name
            or "\x00" in name or any(ord(c) < 32 for c in name)):
        raise GitError(f"{name!r} is not a valid version {what}")
    r = run(["check-ref-format", f"refs/tags/{name}"], check=False)
    if r.returncode != 0:
        raise GitError(f"{name!r} is not a valid version {what}")
    return name


def _commit_id(value):
    if not _HEX.match(value or ""):
        raise GitError(f"not a commit id: {value!r}")
    return value


# ---------------------------------------------------------------------- repository
def init_repo(path):
    """`git init` in a folder the app made."""
    run(["init", "--quiet", f"--initial-branch={DEFAULT_BRANCH}", "--", path])


def is_repo(path):
    """Whether `path` is the top of a work tree. Only for folders the app manages."""
    return os.path.isdir(os.path.join(path, ".git"))


def ensure_attributes(path, lines):
    """Write `.gitattributes` with `lines` unless the folder already has one."""
    full = os.path.join(path, ".gitattributes")
    if not os.path.exists(full):
        with open(full, "w") as fh:
            fh.write("\n".join(lines) + "\n")


def head(path):
    r = run(["rev-parse", "--verify", "--quiet", "HEAD"], path, check=False)
    return r.stdout.strip() or None


def commit_all(path, message):
    """Stage everything and commit it (an empty commit is fine). Returns the commit id."""
    run(["add", "--all", "--", "."], path)
    run(["commit", "--quiet", "--allow-empty", "--no-verify", "-m", message], path)
    return head(path)


def tag(path, name, note):
    check_name(name)
    run(["tag", "--annotate", "-m", note or name, "--", name, "HEAD"], path)


def list_tags(path):
    """Annotated tags as dicts {name, commit, note, time}, oldest first (by position in HEAD's history)."""
    r = run(["for-each-ref", "--format=%(refname:strip=2)%1f%(objecttype)%1f%(*objectname)%1f%(creatordate:unix)"
             "%1f%(contents)%1e", "refs/tags/"], path)
    out = []
    for rec in r.stdout.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        name, otype, commit, when, note = rec.split("\x1f", 4)
        if otype == "tag" and commit:
            out.append({"name": name, "commit": commit, "note": note.strip(), "time": int(when or 0)})
    order = {}
    if head(path):
        order = {c: i for i, c in enumerate(run(["rev-list", "--reverse", "HEAD"], path).stdout.split())}
    out.sort(key=lambda t: (order.get(t["commit"], len(order)), t["time"], t["name"]))
    return out


def resolve_version(path, name):
    check_name(name)
    r = run(["rev-parse", "--verify", "--quiet", f"refs/tags/{name}^{{commit}}"], path, check=False)
    if r.returncode != 0 or not r.stdout.strip():
        raise GitError(f"No version named {name!r}")
    return _commit_id(r.stdout.strip())


# ---------------------------------------------------------------------- workspaces (branches)
def check_branch_name(name):
    """Reject a Workspace name that is not a safe branch name (same cheap checks as `check_name`, then
    `git check-ref-format`). Returns the name."""
    if not isinstance(name, str) or not name.strip():
        raise GitError("A Workspace name is required")
    if (name != name.strip() or name.startswith(("-", "/", ".")) or ".." in name or "\\" in name
            or "\x00" in name or any(ord(c) < 32 for c in name)):
        raise GitError(f"{name!r} is not a valid Workspace name")
    r = run(["check-ref-format", f"refs/heads/{name}"], check=False)
    if r.returncode != 0:
        raise GitError(f"{name!r} is not a valid Workspace name")
    return name


def current_branch(path):
    """The checked-out branch, or None on a detached HEAD."""
    r = run(["symbolic-ref", "--quiet", "--short", "HEAD"], path, check=False)
    return r.stdout.strip() or None


def list_branches(path):
    """Branch names, sorted, with the default branch first."""
    r = run(["for-each-ref", "--format=%(refname:strip=2)", "refs/heads/"], path)
    names = sorted(n for n in r.stdout.split("\n") if n)
    return sorted(names, key=lambda n: n != DEFAULT_BRANCH)


def has_changes(path):
    """Whether the work tree differs from HEAD (modified, deleted or untracked files)."""
    return bool(run(["status", "--porcelain", "--untracked-files=all"], path).stdout.strip())


def create_branch(path, name, commit):
    """Make branch `name` at `commit` without checking it out."""
    check_branch_name(name)
    if name in list_branches(path):
        raise GitError(f"A Workspace named {name!r} already exists")
    run(["branch", "--quiet", "--", name, _commit_id(commit)], path)


def switch_branch(path, name):
    """Check out the existing branch `name`. The work tree must already be committed (the caller snapshots it):
    git then rewrites only the files that differ between the two branches."""
    check_branch_name(name)
    if name not in list_branches(path):
        raise GitError(f"No Workspace named {name!r}")
    run(["switch", "--quiet", "--no-guess", name, "--"], path)


def read_tree(path, commit, dest):
    """Write the files of `commit` into the empty folder `dest` without touching the work tree and without
    running any filter: each blob is read raw with `cat-file`. Symlink entries are written as symlinks (the Document
    loader refuses them without reading the target). Returns the list of paths written."""
    commit = _commit_id(commit)
    listing = run(["ls-tree", "-r", "-z", "--full-tree", commit], path, text=False).stdout
    dest_real = os.path.realpath(dest)
    written = []
    for entry in listing.split(b"\0"):
        if not entry:
            continue
        meta, _, rel = entry.partition(b"\t")
        mode, otype, sha = meta.decode().split()
        rel = rel.decode("utf-8", "surrogateescape")
        parts = rel.split("/")
        if otype != "blob" or rel.startswith("/") or any(p in ("", ".", "..", ".git") for p in parts):
            continue
        target = os.path.join(dest_real, *parts)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.commonpath([dest_real, os.path.realpath(os.path.dirname(target))]) != dest_real:
            continue
        data = run(["cat-file", "blob", _commit_id(sha)], path, text=False).stdout
        if mode == "120000":
            os.symlink(data.decode("utf-8", "surrogateescape"), target)
        else:
            with open(target, "wb") as fh:
                fh.write(data)
            os.chmod(target, 0o444)
        written.append(rel)
    return written


# ---------------------------------------------------------------------- import / push
def clone(source, managed_root, name):
    """Import a received Document: `git clone --no-local <source> <managed_root>/<name>`. `source` is a source
    argument only; git runs with `managed_root` as its working directory. Returns the clone's path."""
    if not os.path.isdir(source):
        raise GitError(f"Not a folder: {source!r}")
    os.makedirs(managed_root, exist_ok=True)
    dest = os.path.join(os.path.realpath(managed_root), name)
    run(["clone", "--quiet", "--no-local", "--", os.path.realpath(source), dest],
        os.path.realpath(managed_root), allow_file=True)
    return dest


def adopt_plain_folder(source, managed_root, name, message="Import"):
    """Import a received folder that has no repository: copy its files (never its `.git`) into the managed
    root, then `git init` and commit there."""
    os.makedirs(managed_root, exist_ok=True)
    dest = os.path.join(os.path.realpath(managed_root), name)
    shutil.copytree(source, dest, symlinks=True, ignore=shutil.ignore_patterns(".git"))
    init_repo(dest)
    commit_all(dest, message)
    return dest


def push(path, remote):
    """Push HEAD and the version tags to a local bare repository (`remote` is a folder path)."""
    if not isinstance(remote, str) or remote.startswith("-") or not os.path.isdir(remote):
        raise GitError(f"Not a local repository folder: {remote!r}")
    run(["push", "--quiet", "--tags", "--", os.path.realpath(remote), f"HEAD:refs/heads/{DEFAULT_BRANCH}"],
        path, allow_file=True)
