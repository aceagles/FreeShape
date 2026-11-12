"""The comparison behind scripts/smoke/s0_goldens.py (the frozen-goldens check every later gate relies
on): a value present at the base commit must still be there in tests/golden/, unchanged; added keys and
new golden files are fine."""
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "smoke"))

from s0_goldens import compare_golden_data  # noqa: E402

BASE = {
    "_meta": {"cadquery-ocp": "7.9.3.1.1"},
    "part": {
        "F2:1": {"volume": 7000.0, "faces": 6},
        "F4:2": {"volume": 500.0, "faces": 6},
    },
}


def test_changed_value_is_reported():
    current = copy.deepcopy(BASE)
    current["part"]["F2:1"]["volume"] = 6999.0
    problems, compared = compare_golden_data(BASE, current)
    assert problems == ["/part/F2:1/volume"]
    assert compared == 4        # every leaf is still visited; the mismatch just also lands in problems


def test_added_key_passes():
    current = copy.deepcopy(BASE)
    current["part"]["F6:3"] = {"volume": 1.0, "faces": 4}
    current["part"]["F2:1"]["new_field"] = "whatever"
    problems, compared = compare_golden_data(BASE, current)
    assert problems == []
    assert compared == 4


def test_removed_key_fails():
    current = copy.deepcopy(BASE)
    del current["part"]["F4:2"]["faces"]
    problems, compared = compare_golden_data(BASE, current)
    assert problems == ["/part/F4:2/faces"]
    assert compared == 3


def test_meta_block_is_ignored():
    current = copy.deepcopy(BASE)
    current["_meta"] = {"cadquery-ocp": "8.0.1.1.0", "note": "different build"}
    problems, compared = compare_golden_data(BASE, current)
    assert problems == []
    assert compared == 4


def test_rekey_map_matches_a_renamed_part_id():
    current = {"part": {"P1": BASE["part"]["F2:1"], "P2": BASE["part"]["F4:2"]}}
    problems, compared = compare_golden_data(BASE, current, rekey_map={"F2:1": "P1", "F4:2": "P2"})
    assert problems == []
    assert compared == 4


def test_missing_whole_file_entry_is_reported():
    current = {"part": {}}
    problems, _ = compare_golden_data(BASE, current)
    assert problems == ["/part/F2:1", "/part/F4:2"]
