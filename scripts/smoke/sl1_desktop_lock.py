"""Gate of slice S13 (I13.2): the daily desktop's branch holds the Document lock against a second program.

Two real processes share only a Document folder. A child process stands for the browser server: plain Python, no
Qt, `doc_lock.acquire(folder, "browser")` and a refresh every half second, which is what `freeshape serve` does
with this same module. The parent is the desktop, offscreen, `MainWindow(engine=True)` as the real app starts it,
with LOCK_TIMEOUT 3 s and LOCK_INTERVAL 0.5 s. One line per step, ending `ok`; a failed step prints why and the
script exits 1 with no `SL1 OK`.

  1. while the child holds the lock the window opens the Document read-only: the banner names "browser", Save is
     refused, autosave writes nothing, and no file in the folder changes
  2. the child is killed with SIGKILL; once the lock is stale a reopen is editable and the lock names "desktop"
  3. the heartbeat keeps the held lock fresh across more than one refresh interval; an edit and a save succeed
  4. a second child that tries to acquire is refused, and its error names "desktop"
  5. the window closes, the lock file is gone, and a third child acquires at once
  6. (measurement) a desktop process given SIGTERM leaves its lock behind: it reads as held until the timeout,
     then a second program takes it over. A real offscreen window, `engine=False` so no worker is orphaned.

Every wait is a bounded poll on the lock's state (`doc_lock.holder`), never a bare sleep. The stale lock is judged
with the `timeout` the code exposes (3 s here, as `MainWindow.LOCK_TIMEOUT` for the window and `acquire(...,
timeout=)` for a child), not the 60 s default. Every child is killed in a `finally`.

    both fs_py scripts/smoke/sl1_desktop_lock.py

Entry points that start a ProcessEngine need the `__main__` guard: a worker imports this file again.
"""
import os
import sys
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

TIMEOUT = 3.0        # the lock timeout of the window and of the child that takes over
INTERVAL = 0.5       # the refresh interval of the window and of the child standing for the server
POLL = 0.05


# ---------------------------------------------------------------------- the children (no Qt unless "window")
def child_main(argv):
    """`hold FOLDER`: the server's side. `try FOLDER`: a program that wants the lock. `window FOLDER`: a desktop."""
    mode, folder = argv[0], argv[1]
    from freeshape import doc_lock as L
    if mode == "hold":
        lock = L.acquire(folder, "browser")
        print("READY", flush=True)
        while True:
            time.sleep(INTERVAL)
            lock.refresh()
    if mode == "try":
        timeout = float(argv[2]) if len(argv) > 2 else L.DEFAULT_TIMEOUT
        try:
            lock = L.acquire(folder, "browser", timeout)
        except L.LockHeld as e:
            print("REFUSED " + str(e), flush=True)
            return 3
        print("ACQUIRED", flush=True)
        lock.release()
        return 0
    if mode == "window":
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["FREESHAPE_NO_RECOVERY"] = "1"
        os.environ["FREESHAPE_NO_HOME"] = "1"
        from PySide6.QtWidgets import QApplication
        from freeshape.ui import mainwindow as MW
        MW.MainWindow.LOCK_TIMEOUT = TIMEOUT
        MW.MainWindow.LOCK_INTERVAL = INTERVAL
        app = QApplication([])
        w = MW.MainWindow(engine=False)
        w.open_document(folder)
        w.show()
        print("READY", flush=True)
        return app.exec()
    return 2


class Fail(Exception):
    pass


def check(cond, msg):
    if not cond:
        raise Fail(msg)


def say(msg):
    print(msg, flush=True)


def poll(what, cond, limit=60.0):
    """Wait for `cond()` by polling the state, for at most `limit` seconds; Fail naming `what` when it never holds."""
    end = time.time() + limit
    while not cond():
        if time.time() > end:
            raise Fail(f"gave up waiting for {what}")
        time.sleep(POLL)


CHILDREN = []


def spawn(mode, folder, *extra, env=None):
    import subprocess
    p = subprocess.Popen([sys.executable, os.path.abspath(__file__), "child", mode, folder, *map(str, extra)],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env or os.environ.copy())
    CHILDREN.append(p)
    return p


def wait_ready(p):
    seen = []
    while True:                                            # Qt prints platform notes first; EOF ends the loop
        line = p.stdout.readline()
        if line.strip() == "READY":
            return
        check(line != "", f"child exited without READY: {seen!r}")
        seen.append(line.strip())


def run_try(folder, timeout=None):
    """A program that tries to take the lock: (exit code, its one output line)."""
    p = spawn("try", folder, *([] if timeout is None else [timeout]))
    out = p.communicate(timeout=120)[0].strip()
    return p.returncode, out


def kill_all():
    import signal
    for p in CHILDREN:
        if p.poll() is None:
            try:
                p.send_signal(signal.SIGKILL)
            except OSError:
                pass
        try:
            p.wait(30)
        except Exception:                                  # noqa: BLE001
            pass
        if p.stdout is not None:
            p.stdout.close()


def children_left():
    return [p.pid for p in CHILDREN if p.poll() is None]


def snapshot(folder):
    """Every file of the Document folder except the lock file and its private files, and the repository's HEAD and
    refs: what 'no file changes' means."""
    out = {}
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in files:
            if f.startswith(".freeshape-lock"):
                continue
            full = os.path.join(root, f)
            with open(full, "rb") as fh:
                out[os.path.relpath(full, folder)] = fh.read()
    for sub in ("HEAD", "packed-refs"):
        full = os.path.join(folder, ".git", sub)
        if os.path.exists(full):
            with open(full, "rb") as fh:
                out[".git/" + sub] = fh.read()
    for root, _dirs, files in os.walk(os.path.join(folder, ".git", "refs")):
        for f in files:
            full = os.path.join(root, f)
            with open(full, "rb") as fh:
                out[os.path.relpath(full, folder)] = fh.read()
    return out


def main():
    import shutil
    import signal
    import tempfile
    import traceback

    tmp = tempfile.mkdtemp(prefix="sl1_desktop_lock_")
    os.environ["FREESHAPE_DATA_DIR"] = os.path.join(tmp, "appdata")     # nothing may touch a real user folder
    os.environ["XDG_DATA_HOME"] = os.path.join(tmp, "xdg")
    os.environ["HOME"] = os.path.join(tmp, "home")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["FREESHAPE_NO_RECOVERY"] = "1"
    os.environ["FREESHAPE_NO_HOME"] = "1"
    for d in ("appdata", "xdg", "home", "autosave", "thumbs", "docs"):
        os.makedirs(os.path.join(tmp, d))
    from freeshape.engine import process as P
    before = set(_spawned())
    win = []
    try:
        run(tmp, win)
    except BaseException as e:                         # noqa: BLE001 -- any failure fails the gate
        say(f"SL1 FAILED: {e}")
        if not isinstance(e, Fail):
            traceback.print_exc()
        code = 1
    else:
        code = 0
    finally:
        for w in win:
            try:
                for d in w.docs.values():
                    d.dirty = False
                w.close()
                if w.studio_engine is not None:
                    w.studio_engine.shutdown()
            except Exception:                          # noqa: BLE001
                pass
        kill_all()
        P.SPARES.clear()
        for p in sorted(set(_spawned()) - before):
            try:
                os.kill(p, signal.SIGKILL)
            except OSError:
                pass
        shutil.rmtree(tmp, ignore_errors=True)
    if code == 0:
        left = children_left()
        if left:
            say(f"SL1 FAILED: child processes still running: {left}")
            return 1
        say("SL1 OK")
    return code


def _spawned():
    """Worker processes of this process (the engine's spawn_main children)."""
    me, out = os.getpid(), []
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                with open(f"/proc/{d}/stat") as fh:
                    rest = fh.read().rsplit(")", 1)[1].split()
                with open(f"/proc/{d}/cmdline", "rb") as fh:
                    cmd = fh.read()
                if int(rest[1]) == me and rest[0] != "Z" and b"spawn_main" in cmd:
                    out.append(int(d))
            except (OSError, ValueError, IndexError):
                pass
    return out


def run(tmp, win):
    import signal

    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QMessageBox

    from conftest import TOP, add, rect_lines, region_refs, sketch_feature
    from freeshape import doc_folder as DF
    from freeshape import doc_lock as L
    from freeshape import doc_registry as R
    from freeshape.ui import home as H
    from freeshape.ui import mainwindow as MW

    app = QApplication.instance() or QApplication([])
    MW.MainWindow.LOCK_TIMEOUT = TIMEOUT
    MW.MainWindow.LOCK_INTERVAL = INTERVAL
    MW.MainWindow.autosave_root = staticmethod(lambda: os.path.join(tmp, "autosave"))
    MW.MainWindow.autosave_dir = classmethod(lambda cls: os.path.join(tmp, "autosave"))
    H.thumbs_dir = lambda: os.path.join(tmp, "thumbs")
    for name in ("warning", "critical", "information"):      # never block on a message box
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)

    sid_box = {}

    def build():
        fd = DF.FolderDocument.create(os.path.join(tmp, "docs", "Doc"), "Doc", registry=R.default_registry())
        tab = fd.add_tab("studio", "Plate")
        sid = sketch_feature(tab.doc, lambda s: rect_lines(s, 0, 0, 20, 20), TOP)
        add(tab.doc, "extrude", regions=region_refs(tab.doc, sid), depth=10.0, op="new")
        check(not tab.doc.errors, f"the box did not build: {tab.doc.errors}")
        fd.save()
        sid_box["path"] = fd.path
        fd.close()
        return fd.path

    def new_window():
        w = MW.MainWindow(engine=True)
        w.settings = QSettings(os.path.join(tmp, "settings.ini"), QSettings.IniFormat)
        app.processEvents()
        win.append(w)
        return w

    def open_in(w, folder):
        check(w.open_document(folder) is not None, "open_document refused the folder")
        check(w.studio_engine.settle(180), "the engine did not settle")

    def action(w, text):
        return next(a for a in w._lock_actions if a.text() == text)

    folder = build()
    check(os.path.dirname(L.lock_path(folder)) == folder and L.holder(folder) is None, "the folder starts unlocked")
    say("setup: a folder Document made, registered at its own folder, unlocked ... ok")

    # ---- 1. read-only while another program holds the lock
    server = spawn("hold", folder)
    wait_ready(server)
    held = L.holder(folder, TIMEOUT)
    check(held is not None and held.program == "browser", f"the child does not hold the lock: {held}")
    before = snapshot(folder)
    w = new_window()
    open_in(w, folder)
    check(w._lock_hold is None and w._lock_ro is not None, "the window did not open the Document read-only")
    check(not w.lock_banner.isHidden() and "browser" in w.lock_label.text(),
          f"the banner does not name the browser: {w.lock_label.text()!r}")
    check(not action(w, "Save").isEnabled() and w.save() is False, "save was not refused")
    studio = w.folder.tabs[0].doc
    studio.dirty = True                                    # unsaved work: the server autosaves, the desktop must not
    w.autosave()
    check(os.listdir(os.path.join(tmp, "autosave")) == [], "a read-only window wrote an autosave")
    studio.dirty = False
    poll("the child's heartbeat to move", lambda: (L.holder(folder, TIMEOUT).heartbeat or 0) > held.heartbeat)
    check(L.holder(folder, TIMEOUT).instance == held.instance, "the window disturbed the child's lock")
    check(snapshot(folder) == before, "a file in the folder changed while the Document was read-only")
    say("1. a fresh browser lock: read-only, banner names browser, save refused, no autosave, no file changed ... ok")

    # ---- 2. the child dies without releasing; the lock goes stale; a reopen is editable
    server.send_signal(signal.SIGKILL)
    server.wait(30)
    check(os.path.exists(L.lock_path(folder)), "the killed child's lock file vanished")
    poll("the dead child's lock to go stale", lambda: L.holder(folder, TIMEOUT).stale)
    check(L.holder(folder, TIMEOUT).program == "browser", "the stale lock does not name the browser")
    check(w._close_document(), "the window would not close the read-only Document")
    open_in(w, folder)
    check(w._lock_ro is None and w._lock_hold is not None and w._lock_hold.running(),
          "the reopen is not editable")
    got = L.holder(folder, TIMEOUT)
    check(got.program == "desktop" and not got.stale and w.lock_banner.isHidden(), f"the lock does not name desktop: {got}")
    say("2. SIGKILL, lock stale after the timeout: the reopen is editable and the lock names desktop ... ok")

    # ---- 3. the heartbeat keeps it fresh; an edit and a save succeed
    start = L.holder(folder, TIMEOUT).heartbeat
    poll("three refreshes", lambda: L.holder(folder, TIMEOUT).heartbeat >= start + 3 * INTERVAL * 0.9, 30)
    mid = L.holder(folder, TIMEOUT)
    check(not mid.stale and mid.instance == got.instance, "the heartbeat did not keep the lock fresh")
    feature = next(f for f in w.folder.tabs[0].doc.features if f["type"] == "extrude")
    feature["params"]["depth"] = 17.0
    w.folder.tabs[0].doc.replace_feature(feature)
    w.folder.tabs[0].doc.dirty = True
    before = snapshot(folder)
    check(action(w, "Save").isEnabled() and w.save() is True, "save was refused on the editable Document")
    check(snapshot(folder) != before, "the save changed no file")
    check(L.holder(folder, TIMEOUT).program == "desktop", "the save disturbed the lock")
    say("3. the heartbeat held the lock across several refresh intervals; an edit and a save succeed ... ok")

    # ---- 4. a second program is refused, and its error names desktop
    rc, out = run_try(folder)
    check(rc == 3 and out.startswith("REFUSED") and "desktop" in out, f"the second child was not refused by desktop: {rc} {out!r}")
    check(L.holder(folder, TIMEOUT).instance == got.instance, "the refused child disturbed the lock")
    say(f"4. a second program is refused: {out} ... ok")

    # ---- 5. close: the lock is gone and a third program acquires at once
    for d in w.docs.values():
        d.dirty = False
    hold = w._lock_hold
    w.close()
    check(not os.path.exists(L.lock_path(folder)) and not hold.running(), "close left the lock file or the thread")
    rc, out = run_try(folder)
    check(rc == 0 and out == "ACQUIRED", f"a third child did not acquire at once: {rc} {out!r}")
    check(not os.path.exists(L.lock_path(folder)), "the third child left the lock file")
    say("5. the window closed: the lock file is gone and a third program acquired at once ... ok")

    # ---- 6. SIGTERM does not release the lock (measurement)
    p = spawn("window", folder, env={**os.environ, "PYTHONPATH": os.path.abspath(ROOT)})
    wait_ready(p)
    h = L.holder(folder, TIMEOUT)
    check(h is not None and h.program == "desktop", f"the desktop process does not hold the lock: {h}")
    t0 = time.time()
    p.send_signal(signal.SIGTERM)
    rc = p.wait(60)
    left = os.path.exists(L.lock_path(folder))
    check(left, "SIGTERM released the lock: the finding of I13.1 no longer holds")
    after = L.holder(folder, TIMEOUT)
    check(after.program == "desktop", "the lock left behind does not name desktop")
    held_now = not after.stale
    rc2, out2 = run_try(folder, 60)                        # a program with the 60 s default timeout
    check(rc2 == 3 and "desktop" in out2, f"a second program was not refused just after SIGTERM: {rc2} {out2!r}")
    poll("the SIGTERMed desktop's lock to go stale", lambda: L.holder(folder, TIMEOUT).stale)
    waited = time.time() - t0
    rc3, out3 = run_try(folder, TIMEOUT)
    check(rc3 == 0 and out3 == "ACQUIRED", f"a second program could not take over the stale lock: {rc3} {out3!r}")
    say(f"6. SIGTERM: exit code {rc}, lock file left: {left}, named desktop, read as held at once: {held_now}, "
        f"refused by a 60 s timeout, taken over after {waited:.1f} s with a {TIMEOUT:.0f} s timeout ... ok")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "child":
        sys.exit(child_main(sys.argv[2:]))
    sys.exit(main())
