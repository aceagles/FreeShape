"""Element-naming smoke (slice S1): builds every topology-change scenario headless, one result line each.

Part of the cumulative smoke gate (see s00_baseline.py for the convention). The scenarios are the ones in
tests/test_scenarios_naming.py -- sketch dimension change, sketch curve added, hole splitting a face, merged
faces, inserted part, consumed edge, twin edges, mirrored copy, hole through a fillet, legacy recovery and
unopened dependents -- built through Documents and feature runners, with no MainWindow and no Qt. Each is run
here as the test runs it, goldens included (tests/golden/scenarios_naming.json); a failed assertion prints the
scenario's line as FAIL and the script exits 1.

    both fs_py scripts/smoke/s1_naming.py
"""
import inspect
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import test_scenarios_naming as T  # noqa: E402


def run(fn):
    """Run one scenario; returns None on success or the failure text. A scenario that wants `tmp_path` gets a
    fresh directory."""
    kwargs = {}
    if "tmp_path" in inspect.signature(fn).parameters:
        kwargs["tmp_path"] = Path(tempfile.mkdtemp(prefix="s1_naming_"))
    try:
        fn(**kwargs)
    except BaseException as e:                  # pytest.fail raises a BaseException
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        return "".join(traceback.format_exception_only(type(e), e)).strip() + "\n" + traceback.format_exc(limit=-3)
    return None


def main():
    failed = []
    for fn in T.SCENARIOS:
        name = fn.__name__.removeprefix("test_")
        t0 = time.time()
        err = run(fn)
        if err is None:
            print(f"S1 {name}: ok ({time.time() - t0:.1f}s)")
        else:
            print(f"S1 {name}: FAIL\n{err}")
            failed.append(name)
    assert "PySide6" not in sys.modules
    if failed:
        print(f"S1 FAILED: {', '.join(failed)}")
        sys.exit(1)
    print(f"S1 OK: {len(T.SCENARIOS)} scenarios ocp={os.environ.get('OCP', '?')}")


if __name__ == "__main__":
    main()
