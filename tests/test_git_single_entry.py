"""I2.4: freeshape/gitstore.py is the only module that runs git. Walks every .py file under freeshape/ with ast."""
import ast
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "freeshape")
GITSTORE = os.path.join(PKG, "gitstore.py")
# these two run other programs (pip and relaunch; reveal in the file manager) and must never run git
ALLOWED = {os.path.join(PKG, "bootstrap.py"), os.path.join(PKG, "ui", "home.py")}
RUNNERS = {"subprocess", "pty", "popen2", "commands"}
GIT_LIBS = {"git", "pygit2", "dulwich", "pygit"}
OS_RUN = {"system", "popen", "execv", "execve", "execvp", "execvpe", "execl", "execle", "execlp", "execlpe",
          "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe", "posix_spawn",
          "posix_spawnp", "startfile"}
GIT_WORD = re.compile(r"(^|[\s/\\])git(\.exe)?($|\s)", re.I)


def _py_files():
    for dirpath, _dirs, files in os.walk(PKG):
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(dirpath, f)


def _uses_process_api(tree):
    """Names of process-running APIs a module touches (imports, os.system and friends, dynamic imports)."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names} & (RUNNERS | GIT_LIBS)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top = node.module.split(".")[0]
            if top in RUNNERS | GIT_LIBS:
                found.add(top)
            elif top == "os":
                found |= {f"os.{a.name}" for a in node.names if a.name in OS_RUN}
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os" \
                and node.attr in OS_RUN:
            found.add(f"os.{node.attr}")
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
            if name in ("__import__", "import_module") and node.args and isinstance(node.args[0], ast.Constant) \
                    and str(node.args[0].value).split(".")[0] in RUNNERS | GIT_LIBS:
                found.add(str(node.args[0].value))
    return found


def _git_strings(tree):
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and GIT_WORD.search(n.value)]


def test_only_gitstore_runs_git():
    offenders = {}
    for path in _py_files():
        with open(path) as fh:
            tree = ast.parse(fh.read(), path)
        used = _uses_process_api(tree)
        if path == GITSTORE:
            assert "subprocess" in used           # the walk can see a runner when there is one
            continue
        if used and path not in ALLOWED:
            offenders[os.path.relpath(path, ROOT)] = sorted(used)
        if path in ALLOWED:
            assert not _git_strings(tree), (path, _git_strings(tree))
    assert not offenders, f"only freeshape/gitstore.py may run processes; found {offenders}"


def test_allowed_modules_never_pass_git_as_a_command():
    for path in ALLOWED:
        if not os.path.exists(path):
            continue
        with open(path) as fh:
            tree = ast.parse(fh.read(), path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess" and node.args:
                first = node.args[0]
                consts = [n.value for n in ast.walk(first) if isinstance(n, ast.Constant)]
                assert not any(isinstance(c, str) and GIT_WORD.search(c) for c in consts), (path, node.lineno)
                if isinstance(first, (ast.List, ast.Tuple)) and first.elts \
                        and isinstance(first.elts[0], ast.Constant):
                    assert os.path.basename(str(first.elts[0].value)).lower() not in ("git", "git.exe")
