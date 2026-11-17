"""TEMPORARY (#52): time git calls on this computer, plain and through freeshape.gitstore. Not for the PR."""
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from freeshape import gitstore as G  # noqa: E402



def t(label, fn, n=2):
    for i in range(n):
        s = time.perf_counter()
        try:
            fn()
            err = ""
        except Exception as e:  # noqa: BLE001
            err = f" ERROR {e}"
        print(f"{(time.perf_counter() - s) * 1000:8.0f} ms  {label} #{i}{err}", flush=True)


def plain(*args, env=None):
    return lambda: subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=env,
                                  stdin=subprocess.DEVNULL)


def basics():
    fmt = "--format=%(refname:strip=2)"
    for stage in ("empty repo", "after a commit"):
        print(f"--- {stage}", flush=True)
        t("plain for-each-ref fmt refs/heads/", plain("for-each-ref", fmt, "refs/heads/"))
        t("plain for-each-ref refs/heads/", plain("for-each-ref", "refs/heads/"))
        t("plain for-each-ref", plain("for-each-ref"))
        t("plain branch --format", plain("branch", "--list", fmt))
        t("plain symbolic-ref", plain("symbolic-ref", "--quiet", "--short", "HEAD"))
        t("plain rev-parse --git-dir", plain("rev-parse", "--git-dir"))
        t("gitstore for-each-ref (list_branches)", lambda: G.list_branches(repo))
        t("gitstore symbolic-ref", lambda: G.current_branch(repo))
        env = G._env()
        t("hardened env, plain argv, for-each-ref", plain("for-each-ref", fmt, "refs/heads/", env=env))
        for drop in ("GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL", "LC_ALL"):
            e = dict(env)
            e.pop(drop)
            t(f"hardened env minus {drop}, for-each-ref", plain("for-each-ref", fmt, "refs/heads/", env=e))
        hooks = G._empty_hooks_dir()
        t("-c hooksPath only, for-each-ref", plain("-c", f"core.hooksPath={hooks}", "for-each-ref", fmt, "refs/heads/"))
        t("-c core.fsmonitor= only, for-each-ref", plain("-c", "core.fsmonitor=", "for-each-ref", fmt, "refs/heads/"))
        t("-c protocol.allow=never only, for-each-ref",
          plain("-c", "protocol.allow=never", "for-each-ref", fmt, "refs/heads/"))
        t("-c core.fsmonitor= only, symbolic-ref", plain("-c", "core.fsmonitor=", "symbolic-ref", "--quiet", "HEAD"))
        if stage == "empty repo":
            with open(os.path.join(repo, "a.txt"), "w") as fh:
                fh.write("a")
            subprocess.run(["git", "-c", "user.name=a", "-c", "user.email=a@b", "add", "a.txt"], cwd=repo)
            subprocess.run(["git", "-c", "user.name=a", "-c", "user.email=a@b", "commit", "-qm", "a"], cwd=repo)
    print(subprocess.run(["git", "--version"], capture_output=True, text=True).stdout)


def overlap():
    import multiprocessing
    import threading
    from concurrent.futures import ProcessPoolExecutor
    from freeshape.engine import worker as W
    print("--- overlap with a spawn worker", flush=True)
    ctx = multiprocessing.get_context("spawn")
    t("list_branches before any worker", lambda: G.list_branches(repo))
    pools = []

    def spawn():
        s = time.perf_counter()
        p = ProcessPoolExecutor(max_workers=1, mp_context=ctx, initializer=W.initialize, initargs=(os.getpid(),))
        f = p.submit(W.probe)
        print(f"{(time.perf_counter() - s) * 1000:8.0f} ms  (spawn submitted)", flush=True)
        pools.append((p, f, s))

    spawn()
    t("list_branches while worker 1 starts", lambda: G.list_branches(repo), n=3)
    p, f, s = pools[-1]
    f.result()
    print(f"{(time.perf_counter() - s) * 1000:8.0f} ms  (worker 1 warm)", flush=True)
    t("list_branches with worker 1 warm, idle", lambda: G.list_branches(repo), n=3)
    th = threading.Thread(target=spawn)
    th.start()
    t("list_branches while worker 2 starts from another thread", lambda: G.list_branches(repo), n=3)
    th.join()
    pools[-1][1].result()
    t("list_branches with both warm", lambda: G.list_branches(repo), n=2)
    for p, *_ in pools:
        p.shutdown(wait=True)
    t("list_branches after workers ended", lambda: G.list_branches(repo), n=2)


if __name__ == "__main__":
    repo = tempfile.mkdtemp(prefix="gd-")
    subprocess.run(["git", "init", "-q", repo], check=True)
    basics()
    overlap()
