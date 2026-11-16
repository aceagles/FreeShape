"""I5.1: the feature dialogs' schema lives in freeshape/dialog_schema.py, without Qt, and describes itself as data."""
import copy
import json
import os
import subprocess
import sys

from freeshape import dialog_schema as DS
from freeshape import features as F
from freeshape import picking as PK
from freeshape.dialog_schema import SCHEMAS, describe


def _field(d, key):
    return next(f for f in d["fields"] if f["key"] == key)


def test_dialog_schema_imports_no_qt():
    code = ("import sys, freeshape.dialog_schema; "
            "bad = [m for m in sys.modules if m.split('.')[0] in ('PySide6', 'PyQt5', 'PyQt6', 'shiboken6')]; "
            "print('QT:' + ','.join(bad))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                         cwd=os.path.join(os.path.dirname(__file__), ".."))
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("QT:"), out.stdout


def test_describe_fillet_has_an_edge_selection_and_a_number():
    d = describe("fillet", F.default_params("fillet"))
    sels = [f for f in d["fields"] if f["kind"] == "sel" and f["key"] == "edges"]
    assert len(sels) == 1 and sels[0]["want"] == ["edge"] and sels[0]["standard"] and sels[0]["visible"]
    nums = [f for f in d["fields"] if f["kind"] == "num" and f["visible"]]
    assert [f["key"] for f in nums] == ["radius"]
    assert nums[0]["unit"] == "mm" and nums[0]["label"]


def test_describe_extrude_visibility_follows_the_end_type():
    base = F.default_params("extrude")

    def visible(**kw):
        d = describe("extrude", {**base, **kw})
        return {f["key"] for f in d["fields"] if f["visible"] and f["key"]}

    spec = SCHEMAS["extrude"]
    for end in ("blind", "symmetric", "up_to_face", "up_to_part", "up_to_vertex", "through"):
        p = {**base, "end": end}
        want = {s["key"] for s in spec if s.get("key") and (s.get("visible") is None or s["visible"](p))}
        assert visible(end=end) == want, end
    assert "upto_face" in visible(end="up_to_face") and "upto_face" not in visible(end="blind")
    assert "upto_part" in visible(end="up_to_part") and "upto_part" not in visible(end="up_to_face")
    assert "depth" in visible(end="blind")


def test_every_feature_type_round_trips_its_default_params():
    assert len(SCHEMAS) > 20
    for ftype in SCHEMAS:
        try:
            p = F.default_params(ftype)
        except Exception:
            p = {}
        if not p:
            continue
        out = copy.deepcopy(p)
        DS.fields_to_params(ftype, DS.params_to_fields(ftype, p), out)
        for k, v in p.items():
            assert out.get(k) == v, (ftype, k, v, out.get(k))
        again = copy.deepcopy(out)
        DS.fields_to_params(ftype, DS.params_to_fields(ftype, out), again)
        assert again == out, ftype


def test_describe_marks_hand_built_rows_custom():
    custom = DS.custom_feature_types()
    assert custom and "split" in custom and "extrude" not in custom
    d = describe("split", F.default_params("split"))
    assert d["custom"] == [f["key"] for f in d["fields"] if not f["standard"]] and d["custom"]
    assert any(f["kind"] == "split_tools" and not f["standard"] for f in d["fields"])
    assert describe("fillet", F.default_params("fillet"))["custom"] == []
    for t in custom:
        assert any(f["kind"] not in ("num", "choice", "check", "sel", "ops", "info", "text")
                   for f in describe(t, F.default_params(t) if t in SCHEMAS else {})["fields"]), t


def test_describe_is_plain_json_and_selection_kinds_are_refs_route_kinds():
    for ftype in SCHEMAS:
        try:
            p = F.default_params(ftype)
        except Exception:
            p = {}
        d = describe(ftype, p)
        assert json.loads(json.dumps(d)) == d, ftype
        for f in d["fields"]:
            if f["kind"] == "sel":
                assert f["want"] and set(f["want"]) <= PK.WANTED, (ftype, f["key"], f["want"])


def test_describe_info_text_is_evaluated():
    d = describe("hole", {**F.default_params("hole"), "standard": "sketch"})
    assert _field(d, "_info")["text"] == "Each hole is the size of the circle you drew"
