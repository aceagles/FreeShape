"""freeshape/engine/process.py: the process executor's own behaviour -- one spawned worker per tab, crash
detection and restart from the parent's edit log, cross-tab inputs sent by the parent, element names across
the process boundary, and worker lifetime. The shared engine contract runs on both executors in
test_engine.py.

Every test that starts workers ends them in a fixture finaliser, pass or fail, and kills any pid it recorded."""
import builtins
import os
import signal
import subprocess
import sys
import time

import pytest

from conftest import EXAMPLES_DIR, ROOT
from freeshape import kernel as K
from freeshape import naming as N
from freeshape.engine import InProcessEngine, ProcessEngine, TabErrored
from freeshape.engine import process as P
from freeshape.engine import worker as W
from freeshape.engine.encode import element_names

HINGE_ASM = os.path.join(EXAMPLES_DIR, "hinge.fasm")
HINGE_STUDIO = os.path.join(EXAMPLES_DIR, "hinge_parts.fshape")
BRACKET = os.path.join(EXAMPLES_DIR, "bracket.fshape")
DEPTH = {"op": "set_param", "feature": "F2", "key": "depth"}       # hinge_parts: the base plate's extrude
GONE_WITHIN_S = 5.0


def hashes(out):
    return {p.id: p.hash for p in out.parts}


def _alive(pid):
    """Running (a zombie, exited but not yet reaped by its parent, counts as gone)."""
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        return False
    except OSError:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _gone_within(pids, timeout=GONE_WITHIN_S):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not any(_alive(p) for p in pids):
            return True
        time.sleep(0.05)
    return not any(_alive(p) for p in pids)


def _descendants(root):
    parent = {}
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                with open(f"/proc/{d}/stat") as fh:
                    parent[int(d)] = int(fh.read().rsplit(")", 1)[1].split()[1])
            except (OSError, ValueError, IndexError):
                pass
    out, todo = [], [root]
    while todo:
        p = todo.pop()
        kids = [c for c, pp in parent.items() if pp == p]
        out += kids
        todo += kids
    return out


def _kill(pids):
    for p in pids:
        try:
            os.kill(p, signal.SIGKILL)
        except OSError:
            pass
        try:
            os.waitpid(p, os.WNOHANG)          # reap it if it was re-parented to us
        except OSError:
            pass


@pytest.fixture(scope="module", autouse=True)
def _no_spares_left():
    yield
    P.SPARES.clear()


@pytest.fixture
def engine():
    """A ProcessEngine; closed when the test ends, and every worker it had is killed if still there."""
    e = ProcessEngine()
    pids = []
    e.record = pids.append
    yield e
    pids += [t.pid for t in e._tabs.values() if t.pid]
    e.close()
    _kill([p for p in pids if _alive(p)])


def _open(e, path, **kw):
    tab = e.open_tab(path, **kw)
    e.record(e.pid(tab))
    return tab


# =========================================================================== workers and crashes
# Starting a worker costs `import OCP` (seconds), so each test below covers several steps with the workers it
# has already started.

def _die_soon():
    time.sleep(0.3)
    os._exit(9)


def test_a_killed_worker_errors_its_tab_only_and_restart_replays_its_unsaved_edits(engine):
    a, b = _open(engine, HINGE_STUDIO), _open(engine, BRACKET)
    # one spawned, headless, single-worker pool per tab
    assert len({engine.pid(a), engine.pid(b), os.getpid()}) == 3
    for tab in (a, b):
        pool = engine._get(tab).pool
        assert pool._mp_context.get_start_method() == "spawn" and pool._max_workers == 1
        assert engine.run_in_worker(tab, W.probe) == (engine.pid(tab), False)
    saved, other = hashes(engine.outputs(a)), hashes(engine.outputs(b))
    engine.submit_edit(a, dict(DEPTH, value=12))                          # edited, never saved
    engine.submit_edit(a, {"op": "rename_feature", "feature": "F2", "name": "Plate"})
    edited = engine.outputs(a)
    assert hashes(edited) != saved

    pid = engine.pid(a)
    os.kill(pid, signal.SIGKILL)
    with pytest.raises(TabErrored) as err:
        engine.outputs(a)
    assert err.value.tab == a and engine.errored(a)
    with pytest.raises(TabErrored):
        engine.submit_edit(a, dict(DEPTH, value=14))
    assert hashes(engine.outputs(b)) == other and engine.errored(b) is None     # the other tab answers
    assert (a, "studio") in [(t, k) for t, k, _n, _p in engine.tabs()]           # still open, errored

    out = engine.restart(a)                                  # rebuilt from the parent's log, not the file
    engine.record(engine.pid(a))
    assert engine.pid(a) != pid and engine.errored(a) is None
    assert hashes(out) == hashes(edited) != saved
    assert out.revision == edited.revision == 2
    plate = next(p for p in out.parts if p.id == "F2/1")
    lo, hi = K.bbox(K.shape_from_brep(plate.brep))
    assert hi[2] - lo[2] == pytest.approx(12)
    engine.submit_edit(a, {"op": "undo"})                    # the replayed undo history is the live one too
    engine.submit_edit(a, {"op": "undo"})
    assert hashes(engine.outputs(a)) == saved

    # an edit queued when the worker dies fails its future, and is not replayed
    engine._get(a).pool.submit(_die_soon)
    fut = engine.submit(a, dict(DEPTH, value=16))
    with pytest.raises(TabErrored):
        fut.result(timeout=60)
    assert engine.errored(a)
    out = engine.restart(a)
    engine.record(engine.pid(a))
    assert out.revision == 4 and hashes(out) == saved


# =========================================================================== cross-tab inputs and element names

def _spy_opens():
    """Record every path this worker opens from now on."""
    seen = []
    real = builtins.open

    def spy(file, *a, **k):
        if isinstance(file, (str, bytes, os.PathLike)):
            seen.append(os.path.realpath(file))
        return real(file, *a, **k)

    builtins.open = spy
    W.current().opened = seen


def _opened():
    return list(W.current().opened)


def _name_parts():
    """Give every part of this studio worker a name map; returns {part id: element_names(part)}."""
    out = {}
    for p in W.current().doc.state.parts.values():
        p.named = N.seed(p.shape, "T1")
        out[p.id] = element_names(p)
    return out


def test_a_consumer_gets_names_and_unsaved_upstream_edits_from_the_parent_never_from_disk(engine):
    studio = _open(engine, HINGE_STUDIO)
    # a name map set on a part in the worker arrives in the parent unchanged ...
    names = engine.run_in_worker(studio, _name_parts)
    sout = engine.outputs(studio)
    assert {p.id: p.names for p in sout.parts} == names
    assert all(n["face"] and n["edge"] and n["vertex"] for n in names.values())
    # ... and crosses on to a consumer, which gets the named parts from the parent
    asm = _open(engine, HINGE_ASM)
    before = engine.outputs(asm)
    assert before.consumes == (studio,) and not before.errors
    by_hash = {p.hash: p.names for p in sout.parts}
    assert {h: p.names for h, p in before.parts.items()} == {h: by_hash[h] for h in before.parts}

    # studio edited and not saved: the assembly's worker rebuilds with the new plate, never reading the file
    engine.run_in_worker(asm, _spy_opens)
    engine.submit_edit(studio, dict(DEPTH, value=12))
    after = engine.outputs(asm)
    plate = after.parts[after.instances[0].part]
    assert plate.hash != before.instances[0].part
    assert plate.hash == next(p.hash for p in engine.outputs(studio).parts if p.id == "F2/1")
    lo, hi = K.bbox(K.shape_from_brep(plate.brep))
    assert hi[2] - lo[2] == pytest.approx(12)

    # the upstream worker dies: the assembly still answers, with its last version of those parts
    os.kill(engine.pid(studio), signal.SIGKILL)
    with pytest.raises(TabErrored):
        engine.outputs(studio)
    stale = engine.outputs(asm)
    assert [i.part for i in stale.instances] == [i.part for i in after.instances]
    assert {"I1", "I2", "I3"} <= set(stale.errors)
    engine.restart(studio)                          # back with its unsaved edit, and the assembly follows
    engine.record(engine.pid(studio))
    back = engine.outputs(asm)
    assert not back.errors and [i.part for i in back.instances] == [i.part for i in after.instances]
    assert os.path.realpath(HINGE_STUDIO) not in engine.run_in_worker(asm, _opened)


def test_bracket_names_match_the_in_process_executor_and_closing_ends_the_worker(engine):
    tab = _open(engine, BRACKET)
    got = {p.id: p.names for p in engine.outputs(tab).parts}
    pid = engine.pid(tab)
    engine.close_tab(tab)
    assert _gone_within([pid]) and tab not in [t for t, *_ in engine.tabs()]
    ip = InProcessEngine()
    want = {p.id: p.names for p in ip.outputs(ip.open_tab(BRACKET)).parts}
    assert got == want
    if not any(any(m.values()) for m in want.values()):
        pytest.skip("parts carry no element map yet (I1.6 attaches them); transport is checked above")
    assert all(any(m.values()) for m in got.values())


# =========================================================================== lifetime

_HELPER = """
import sys
sys.path.insert(0, {root!r})
from freeshape.engine import ProcessEngine
if __name__ == "__main__":
    e = ProcessEngine(spares=0)
    tab = e.open_tab({path!r})
    print("ready", e.pid(tab), flush=True)
    sys.stdin.read()
"""


@pytest.fixture
def helper():
    """A Python process that starts an engine; killed (with everything it started) when the test ends."""
    procs, pids = [], []
    yield procs, pids
    for p in procs:
        pids += _descendants(p.pid)
        p.kill()
        p.wait(timeout=30)
    _kill([p for p in pids if _alive(p)])


def test_workers_exit_when_the_process_that_started_them_is_killed(helper):
    procs, pids = helper
    proc = subprocess.Popen([sys.executable, "-c", _HELPER.format(root=ROOT, path=HINGE_STUDIO)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, cwd=ROOT)
    procs.append(proc)
    line = proc.stdout.readline().split()
    assert line[:1] == ["ready"], line
    worker = int(line[1])
    kids = _descendants(proc.pid)
    pids += kids
    assert worker in kids and _alive(worker)
    proc.kill()                                                         # SIGKILL: no clean-up runs
    proc.wait(timeout=30)
    assert _gone_within(kids), [k for k in kids if _alive(k)]
