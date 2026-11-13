"""I4.6: the bundled Gears plugin (freeshape/addons/gears): SpurGear and BevelGear as Python features.

Bevel gears are measured with OCC's plain volume integrator (see tests/test_gears_bevel.py): K.volume and
conftest.shape_fingerprint take minutes on them.
"""
import copy
import os
import re
import sys

import pytest
from conftest import add
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import freeshape.document  # noqa: E402,F401  (registers the bundled plugins)
from freeshape import kernel as K  # noqa: E402
from freeshape import namedbuild  # noqa: E402
from freeshape import pyfeature  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.pyfeature import host  # noqa: E402
from freeshape.pyfeature.api import Inputs  # noqa: E402

GEARS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "freeshape", "addons", "gears")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    user = tmp_path / "user_plugins"
    user.mkdir()
    monkeypatch.setenv("FREESHAPE_PLUGIN_DIR", str(user))


def _volume(shape):
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return g.Mass()


def _face_count(shape):
    ex, n = TopExp_Explorer(shape, TopAbs_FACE), 0
    while ex.More():
        n += 1
        ex.Next()
    return n


def _teeth(doc, pid):
    names = host.names_json(doc.state.named(pid))
    return sum(1 for n in names["face"].values() if re.match(r"t\d+_flankA", n))


def test_plugin_registered():
    types = {t for t, _, _ in pyfeature.feature_types()}
    assert {"py:gears/spur", "py:gears/bevel"} <= types
    assert not [p for p in pyfeature.problems() if "gears" in p[0]]
    assert pyfeature.plugin("gears").source == "bundled"


def test_icons_exist():
    for ftype in ("py:gears/spur", "py:gears/bevel"):
        cls = pyfeature.get(ftype)
        assert os.path.isfile(os.path.join(GEARS, cls.icon))


def test_spur_feature_builds_and_is_named():
    doc = Document()
    f = add(doc, "py:gears/spur")
    assert doc.errors == {} and f["name"] == "Spur gear 1"
    assert list(doc.state.parts) == [f"{f['id']}/1"]
    assert _teeth(doc, f"{f['id']}/1") == 15


def test_bevel_feature_builds_and_is_named():
    doc = Document()
    f = add(doc, "py:gears/bevel")
    assert doc.errors == {} and f["name"] == "Bevel gear 1"
    assert list(doc.state.parts) == [f"{f['id']}/1"]
    assert _teeth(doc, f"{f['id']}/1") == 15


def test_variable_drives_tooth_count():
    doc = Document()
    var = add(doc, "variable", name="z", expr="20")
    f = add(doc, "py:gears/spur", num_teeth="#z")
    pid = f"{f['id']}/1"
    assert _teeth(doc, pid) == 20
    changed = copy.deepcopy(var)
    changed["params"]["expr"] = "24"
    doc.replace_feature(changed)
    assert doc.errors == {}
    assert _teeth(doc, pid) == 24


def _faces_by_name(named):
    return host.names_json(named)["face"]


def test_names_are_stable_across_tooth_count():
    cls = pyfeature.get("py:gears/spur")
    params = pyfeature.default_params("py:gears/spur")
    out = {}
    for z in (15, 17):
        make = namedbuild.Builder("F9")
        named = cls().build(dict(params, num_teeth=z), Inputs("F9", {}, make))
        names = _faces_by_name(named)
        assert len(names) == _face_count(named.shape)
        assert not [n for n in names.values() if re.fullmatch(r"f\d+", n)]       # every face is named
        out[z] = set(names.values())
    for label in ("top", "bottom", "t1_flankA"):
        assert any(n.startswith(label + ";") for n in out[15]) and any(n.startswith(label + ";") for n in out[17])
    assert {n for n in out[15] if n.split(";")[0] in ("top", "bottom", "t1_flankA")} == \
           {n for n in out[17] if n.split(";")[0] in ("top", "bottom", "t1_flankA")}


def test_bore_name_is_stable():
    cls = pyfeature.get("py:gears/spur")
    params = dict(pyfeature.default_params("py:gears/spur"), bore=4.0)
    sets = []
    for z in (15, 17):
        named = cls().build(dict(params, num_teeth=z), Inputs("F9", {}, namedbuild.Builder("F9")))
        sets.append({n for n in _faces_by_name(named).values() if n.startswith("bore;")})
    assert len(sets[0]) == 1 and sets[0] == sets[1]


def test_build_without_builder_returns_a_plain_solid():
    cls = pyfeature.get("py:gears/spur")
    out = cls().build(pyfeature.default_params("py:gears/spur"), Inputs("F9", {}, None))
    assert not hasattr(out, "names") and K.solids(out)


def test_bevel_height_above_cone_limit_names_the_limit():
    doc = Document()
    f = doc.make_feature("py:gears/bevel")
    f["params"]["height"] = 8.0                  # limit is 15 * 1 / 2 / tan(45 deg) = 7.5
    doc.add_feature(f)
    assert "7.5" in doc.errors[f["id"]]


def test_too_many_teeth_is_a_validation_error():
    doc = Document()
    f = doc.make_feature("py:gears/spur")
    f["params"]["num_teeth"] = 5000
    doc.add_feature(f)
    assert "Teeth" in doc.errors[f["id"]]
    assert doc.state.parts == {} or not any(k.startswith(f["id"]) for k in doc.state.parts)


def test_spur_bore_too_large_is_an_error_message():
    doc = Document()
    f = doc.make_feature("py:gears/spur")
    f["params"]["bore"] = 50.0
    doc.add_feature(f)
    assert "bore" in doc.errors[f["id"]]


@pytest.mark.parametrize("ftype,params", [("py:gears/spur", {}), ("py:gears/bevel", {})])
def test_save_load_rebuild_keeps_volume(tmp_path, ftype, params):
    doc = Document()
    f = add(doc, ftype, **params)
    pid = f"{f['id']}/1"
    shape = doc.state.parts[pid].shape
    before = (_volume(shape), _face_count(shape))
    path = str(tmp_path / "studio.fshape")
    doc.save(path)
    loaded = Document.load(path)
    assert loaded.errors == {}
    loaded.rebuild()
    shape = loaded.state.parts[pid].shape
    assert (_volume(shape), _face_count(shape)) == pytest.approx(before)
