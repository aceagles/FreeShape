"""F4: a choice row of a feature dialog always carries a value that is one of its options (the desktop combo's rule:
a missing or unknown value shows the first option), so the client never draws it blank."""
import glob
import json
import os

import pytest

from freeshape.session import FeatureDialog

ROOT = os.path.join(os.path.dirname(__file__), "..", "examples", "documents")


def _features():
    out = []
    for path in sorted(glob.glob(os.path.join(ROOT, "*", "*.fshape"))):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except ValueError:
            continue
        out += [(os.path.basename(path), ft) for ft in data.get("features", [])]
    return out


def _dialog(feature):
    return FeatureDialog("t", json.loads(json.dumps(feature)), {}, None)


def test_fillet_1_type_shows_the_desktop_label():
    ft = next(f for n, f in _features() if n == "bracket.fshape" and f.get("name") == "Fillet 1")
    assert "fillet_type" not in ft["params"]            # the stored feature predates the field
    d = _dialog(ft)
    spec = next(s for s in d.description()["fields"] if s["key"] == "fillet_type")
    label = dict(map(tuple, spec["options"]))[d.values()["fillet_type"]["value"]]
    assert label == "Constant radius"


def test_an_unknown_choice_value_shows_the_first_option():
    d = _dialog({"id": "F9", "type": "fillet", "name": "x", "params": {"fillet_type": "bogus", "radius": 1}})
    assert d.values()["fillet_type"]["value"] == "constant"
    d = _dialog({"id": "F9", "type": "fillet", "name": "x", "params": {"fillet_type": "variable", "radius": 1}})
    assert d.values()["fillet_type"]["value"] == "variable"


@pytest.mark.parametrize("name,feature", _features(), ids=lambda x: x if isinstance(x, str) else x.get("id", "?"))
def test_every_choice_row_of_the_example_features_resolves_to_an_option(name, feature):
    d = _dialog(feature)
    for spec in d.description()["fields"]:
        if spec["kind"] != "choice":
            continue
        labels = {v: lab for v, lab in map(tuple, spec["options"])}
        assert d.values()[spec["key"]]["value"] in labels, (name, feature["id"], spec["key"])
