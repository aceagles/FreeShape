"""One-shot tool for I1.4: renames the part id keys baked into tests/golden/ from the pre-I1.4
<fid>:<counter> syntax to the new <fid>/<n> syntax, without touching any value.

The mapping is model-specific (the old counter was replay-wide), so it can't be worked out from the
golden files alone: this script re-runs each golden-producing scenario with the current code, reads the
old id each part would have had off `ModelState.old_ids` (populated by every `new_part` call -- see
freeshape/features.py), and uses that to rename the matching keys in the committed golden file, at
whatever depth they appear inside that entry. Every id actually renamed is recorded in
tests/golden/rekey_map.json as {"<group>/<key>": {<old id>: <new id>}}, which scripts/smoke/s0_goldens.py reads so the frozen-goldens gate keeps passing.

Run from the repo root (safe to re-run: ids already renamed are left alone and the map keeps its entries):

    both fs_py scripts/rekey_goldens.py

It is not part of the test suite -- tests/test_golden_rekey.py checks its *result* (the committed
tests/golden/*.json and rekey_map.json), not this script itself.
"""
import importlib
import json
import os
import sys
import tempfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
TESTS_DIR = os.path.join(ROOT, "tests")
GOLDEN_DIR = os.path.join(TESTS_DIR, "golden")
REKEY_MAP_PATH = os.path.join(GOLDEN_DIR, "rekey_map.json")

for p in (ROOT, TESTS_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

CURRENT = {}        # old id -> new id seen since the last golden entry was finished
ENTRIES = {}        # "<group>/<key>" -> {old id: new id} for the model(s) that produced that entry
COLLISIONS = []     # (entry, old id, new id already recorded, conflicting new id) -- should stay empty


def _record(old_ids):
    """old_ids: a ModelState.old_ids dict (new id -> old id, see features.py). Folds every entry into the
    CURRENT table (old id -> new id), flagging (not raising -- the caller may want the whole picture before
    failing) any old id that would need two different targets inside one golden entry."""
    for new_id, old_id in old_ids.items():
        prev = CURRENT.get(old_id)
        if prev is not None and prev != new_id:
            COLLISIONS.append((old_id, prev, new_id))
            continue
        CURRENT[old_id] = new_id


def _finish_entry(name, *_a, **_kw):
    """Stand-in for assert_golden: the stored values are what we're about to rename keys in, so there is
    nothing to compare. Files the aliases gathered since the previous entry under this entry's name."""
    ENTRIES[name] = dict(CURRENT)
    CURRENT.clear()


def _capture_fp(orig_fp):
    """Wraps conftest.state_fingerprint (as imported into a test module's own namespace) so calling it the
    normal way -- as every golden-producing test already does -- also records aliases as a side effect."""
    def fp(doc_or_state):
        st = getattr(doc_or_state, "state", doc_or_state)
        _record(getattr(st, "old_ids", {}))
        return orig_fp(doc_or_state)
    return fp


def _patch(mod):
    mod.assert_golden = _finish_entry
    if hasattr(mod, "state_fingerprint"):
        mod.state_fingerprint = _capture_fp(mod.state_fingerprint)


def run_golden_features():
    mod = importlib.import_module("test_golden_features")
    _patch(mod)
    for name in mod.CASES:
        mod.test_feature_golden(name)


def run_golden_toponaming():
    mod = importlib.import_module("test_golden_toponaming")
    _patch(mod)
    for name in dir(mod):
        if name.startswith("test_") and name != "test_bracket_fillet_survives_width_sweep":
            getattr(mod, name)()
    for w in (30.0, 45.0, 80.0):
        mod.test_bracket_fillet_survives_width_sweep(w)


def run_golden_examples():
    mod = importlib.import_module("test_golden_examples")
    _patch(mod)
    for name in sorted(mod.STUDIOS):
        mod.test_example_studio_rebuilds(name)
        mod.test_example_studio_every_step(name)


def run_document_ops():
    mod = importlib.import_module("test_document_ops")
    _patch(mod)
    mod.test_insert_at_rollback_bar()


def run_file_versions():
    """Just the studio (document) round trip -- the only file_versions.json entries with part-id keys
    (hinge_fasm / v1_fdwg are keyed by instance id / plain fingerprint fields, not part ids). This golden
    file is newer than the base commit, so it is not frozen; it is rekeyed the same way."""
    from freeshape.document import Document
    for name in ("hinge_parts", "bracket"):
        doc = Document.load(os.path.join(ROOT, "examples", f"{name}.fshape"))
        _record(doc.state.old_ids)
        _finish_entry(f"file_versions/{name}")
        path = os.path.join(tempfile.mkdtemp(), f"{name}.fshape")
        doc.save(path)
        back = Document.load(path)
        _record(back.state.old_ids)
        _finish_entry(f"file_versions/{name}_reloaded")


def _rename_keys(x, table):
    """x, with every dict key that's an old id in `table` replaced by its new one, at any depth. Values and
    every other key are untouched."""
    if isinstance(x, dict):
        return {table.get(k, k): _rename_keys(v, table) for k, v in x.items()}
    if isinstance(x, list):
        return [_rename_keys(v, table) for v in x]
    return x


def _keys_of(x, out):
    if isinstance(x, dict):
        for k, v in x.items():
            out.add(k)
            _keys_of(v, out)
    elif isinstance(x, list):
        for v in x:
            _keys_of(v, out)
    return out


def main():
    run_golden_features()
    run_golden_toponaming()
    run_golden_examples()
    run_document_ops()
    run_file_versions()

    if COLLISIONS:
        print("REKEY FAILED: the same old id needs two different new ids in one entry:", file=sys.stderr)
        for old_id, a, b in COLLISIONS:
            print(f"  {old_id} -> {a} or {b}", file=sys.stderr)
        return 1

    applied = {}        # "<group>/<key>" -> {old id: new id} actually used; kept across re-runs
    if os.path.exists(REKEY_MAP_PATH):
        with open(REKEY_MAP_PATH) as fh:
            applied = json.load(fh)
    golden = {}
    for name, table in sorted(ENTRIES.items()):
        group, key = name.split("/", 1)
        if group not in golden:
            with open(os.path.join(GOLDEN_DIR, f"{group}.json")) as fh:
                golden[group] = json.load(fh)
        if key not in golden[group]:
            continue
        present = _keys_of(golden[group][key], set())
        used = {o: n for o, n in table.items() if o in present}
        if not used:
            continue
        golden[group][key] = _rename_keys(golden[group][key], used)
        applied.setdefault(name, {}).update(used)
        applied[name] = dict(sorted(applied[name].items()))

    for group, data in golden.items():
        with open(os.path.join(GOLDEN_DIR, f"{group}.json"), "w") as fh:
            json.dump(data, fh, indent=1, sort_keys=True)
            fh.write("\n")
    with open(REKEY_MAP_PATH, "w") as fh:
        json.dump(applied, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print(f"renamed {sum(len(v) for v in applied.values())} id(s) across {len(applied)} entries; "
          f"wrote {REKEY_MAP_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
