"""Frozen-goldens check: every later gate in this Run relies on tests/golden/ not drifting underneath it.

Compares each file in tests/golden/ (as it stands now) with the same file at the Run's base commit --
`git show harness-base:tests/golden/<file>`, falling back to `origin/master` if the `harness-base` branch
doesn't exist. A value present at the base must still be there, unchanged; new keys and whole new golden
files are fine (a slice is allowed to add golden coverage, just not touch what's already frozen). The
`_meta` block (OCP version, update-golden note) is ignored, since it's expected to vary. If
tests/golden/rekey_map.json exists, a part-id key missing by its base name is looked up through that map
before being reported missing -- I1.4 renames part ids and writes that file; before it exists, the map is
simply empty and every key is compared by its own name.

The comparison itself lives in `compare_golden_data` so it can be unit-tested (tests/test_goldens_frozen.py)
without touching git or the filesystem.

    both fs_py scripts/smoke/s0_goldens.py
"""
import json
import os
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
GOLDEN_DIR = os.path.join(ROOT, "tests", "golden")
BASE_BRANCH = "harness-base"
FALLBACK_REF = "origin/master"
REKEY_MAP_NAME = "rekey_map.json"


def compare_golden_data(base, current, rekey_map=None):
    """Compare two parsed golden-file trees: `base` as recorded at the Run's base commit, `current` as it
    stands now. Returns (problems, compared) -- `problems` is a list of path strings for every value
    present in `base` that's missing or different in `current` (added keys in `current` are not problems);
    `compared` counts how many leaf values matched. `rekey_map` (old key -> new key) is consulted for a
    base dict key that's missing by its own name in `current`, so a part-id rename doesn't look like data
    loss."""
    rekey_map = rekey_map or {}
    problems = []
    compared = 0

    def walk(b, c, path):
        nonlocal compared
        if isinstance(b, dict):
            if not isinstance(c, dict):
                problems.append(path or "/")
                return
            for k, v in b.items():
                if k == "_meta":
                    continue
                ck = k if k in c else rekey_map.get(k, k)
                if ck not in c:
                    problems.append(f"{path}/{k}")
                    continue
                walk(v, c[ck], f"{path}/{k}")
        elif isinstance(b, list):
            if not isinstance(c, list) or len(c) != len(b):
                problems.append(path or "/")
                return
            for i, (bv, cv) in enumerate(zip(b, c)):
                walk(bv, cv, f"{path}[{i}]")
        else:
            compared += 1
            if b != c:
                problems.append(path or "/")

    walk(base, current, "")
    return problems, compared


def _ref_exists(ref):
    return subprocess.run(["git", "-C", ROOT, "rev-parse", "--verify", "--quiet", ref],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def _base_ref():
    if _ref_exists(BASE_BRANCH):
        return BASE_BRANCH
    if _ref_exists(FALLBACK_REF):
        return FALLBACK_REF
    return None


def _at_ref(ref, relpath):
    """Parsed JSON of relpath at ref, or None if it doesn't exist there."""
    p = subprocess.run(["git", "-C", ROOT, "show", f"{ref}:{relpath}"],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    if p.returncode != 0:
        return None
    return json.loads(p.stdout)


def _load_rekey_map():
    path = os.path.join(GOLDEN_DIR, REKEY_MAP_NAME)
    if not os.path.exists(path):
        return {}
    with open(path) as fh:
        return json.load(fh)


def main():
    ref = _base_ref()
    if ref is None:
        print(f"S0 GOLDENS FAILED: neither '{BASE_BRANCH}' nor '{FALLBACK_REF}' could be resolved",
              file=sys.stderr)
        return 1

    rekey_map = _load_rekey_map()
    names = sorted(n for n in os.listdir(GOLDEN_DIR) if n.endswith(".json") and n != REKEY_MAP_NAME)
    all_problems = []
    total_compared = 0
    for name in names:
        with open(os.path.join(GOLDEN_DIR, name)) as fh:
            current = json.load(fh)
        base = _at_ref(ref, f"tests/golden/{name}")
        if base is None:
            continue    # new golden file since the base commit -- allowed
        problems, compared = compare_golden_data(base, current, rekey_map)
        total_compared += compared
        all_problems += [f"{name}{p}" for p in problems]

    if all_problems:
        print(f"S0 GOLDENS FAILED: {len(all_problems)} value(s) changed or missing since {ref}:",
              file=sys.stderr)
        for p in all_problems[:40]:
            print(f"  {p}", file=sys.stderr)
        if len(all_problems) > 40:
            print(f"  ... and {len(all_problems) - 40} more", file=sys.stderr)
        return 1

    print(f"GOLDENS FROZEN OK: {total_compared} value(s) compared against {ref}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
