"""I1.4 renamed the part-id keys in tests/golden/ (scripts/rekey_goldens.py) without touching a value.
Check that: each golden file at the base commit, with tests/golden/rekey_map.json applied, equals the
committed file apart from `_meta` -- every value identical, no base key lost. Entries added since the base are allowed."""
import json
import os
import subprocess

import pytest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
GOLDEN_DIR = os.path.join(ROOT, "tests", "golden")


def _git(*args):
    return subprocess.run(["git", "-C", ROOT, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _base_ref():
    for ref in ("harness-base", "origin/master"):
        if _git("rev-parse", "--verify", "--quiet", ref).returncode == 0:
            return ref
    pytest.fail("cannot find the base commit: neither 'harness-base' nor 'origin/master' resolves in this "
                "repo, so the golden files cannot be read at their base version (run with git available "
                "and one of those refs present)")


def _rename(x, table):
    if isinstance(x, dict):
        return {table.get(k, k): _rename(v, table) for k, v in x.items()}
    if isinstance(x, list):
        return [_rename(v, table) for v in x]
    return x


def _base_files(ref):
    out = _git("ls-tree", "--name-only", ref, "tests/golden/")
    assert out.returncode == 0, out.stderr
    return sorted(os.path.basename(p) for p in out.stdout.split() if p.endswith(".json"))


def test_rekeyed_goldens_equal_base_apart_from_part_id_keys():
    ref = _base_ref()
    with open(os.path.join(GOLDEN_DIR, "rekey_map.json")) as fh:
        rekey = json.load(fh)
    names = _base_files(ref)
    assert "features.json" in names
    seen_entries = set()
    for name in names:
        if name == "rekey_map.json":
            continue
        shown = _git("show", f"{ref}:tests/golden/{name}")
        assert shown.returncode == 0, f"git show {ref}:tests/golden/{name} failed: {shown.stderr}"
        base = json.loads(shown.stdout)
        with open(os.path.join(GOLDEN_DIR, name)) as fh:
            current = json.load(fh)
        group = name[:-len(".json")]
        expected = {}
        for key, value in base.items():
            if key == "_meta":
                continue
            table = rekey.get(f"{group}/{key}", {})
            if table:
                seen_entries.add(f"{group}/{key}")
            expected[key] = _rename(value, table)
        got = {k: v for k, v in current.items() if k != "_meta"}
        lost = sorted(set(expected) - set(got))
        assert not lost, f"{name}: base entries lost: {lost}"      # entries added since the base are allowed
        for key in expected:
            assert got[key] == expected[key], f"{name}/{key}: differs from the base after applying the rekey map"
    assert seen_entries == {e for e in rekey if e.split("/")[0] + ".json" in names}, \
        "rekey_map.json names entries that are not in the base goldens"


def test_no_old_style_part_id_keys_remain():
    for name in sorted(os.listdir(GOLDEN_DIR)):
        if name == "rekey_map.json" or not name.endswith(".json"):
            continue
        with open(os.path.join(GOLDEN_DIR, name)) as fh:
            text = fh.read()
        import re
        assert not re.search(r'"F\d+:\d+', text), name
