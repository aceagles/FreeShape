"""I4.1: Python feature registry, plugin loader and host runner.

Every test points FREESHAPE_PLUGIN_DIR at an empty tmp dir, so none reads or writes the real user plugin
folder, and unregisters the plugins it registered.
"""
import importlib
import importlib.util
import json
import os
import shutil

import pytest
from conftest import add, region_refs, rect_lines, sketch_feature

from freeshape import features as F
from freeshape import kernel as K
from freeshape import pyfeature
from freeshape.document import Document
from freeshape.pyfeature import registry

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "pyplugins")
BOXES = os.path.join(DATA, "boxes")
PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "freeshape", "pyfeature")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    user = tmp_path / "user_plugins"
    user.mkdir()
    monkeypatch.setenv("FREESHAPE_PLUGIN_DIR", str(user))
    before = {p.id for p in pyfeature.plugins()}
    yield user
    for p in pyfeature.plugins():
        if p.id not in before:
            pyfeature.unregister_plugin(p.id)


@pytest.fixture
def boxes():
    info = pyfeature.register_plugin(BOXES)
    assert info is not None, pyfeature.problems()
    return info


def copy_plugin(src, root, name=None, **manifest):
    dst = os.path.join(root, name or os.path.basename(src))
    shutil.copytree(src, dst)
    if manifest:
        path = os.path.join(dst, "plugin.json")
        with open(path) as fh:
            m = json.load(fh)
        m.update(manifest)
        with open(path, "w") as fh:
            json.dump(m, fh)
    return dst


def write_plugin(root, folder, manifest, files=None):
    d = os.path.join(root, folder)
    os.makedirs(d)
    with open(os.path.join(d, "plugin.json"), "w") as fh:
        json.dump(manifest, fh)
    for name, text in (files or {}).items():
        with open(os.path.join(d, name), "w") as fh:
            fh.write(text)
    return d


def manifest(pid, *features, version="1.0.0"):
    return {"id": pid, "name": pid.title(), "version": version, "licence": "GPL-3.0-or-later",
            "features": list(features)}


def add_py(doc, ftype, **params):
    f = doc.make_feature(ftype)
    f["params"].update(params)
    doc.add_feature(f)
    return f


def volume(doc, pid):
    return K.volume(doc.state.parts[pid].shape)


def problem_for(path):
    return [m for p, m in pyfeature.problems() if os.path.abspath(p) == os.path.abspath(path)]


# --------------------------------------------------------------------------- building

def test_box_feature_makes_one_part(boxes):
    doc = Document()
    f = add_py(doc, "py:boxes/box")
    assert doc.errors == {}
    assert list(doc.state.parts) == [f"{f['id']}/1"]
    assert volume(doc, f"{f['id']}/1") == pytest.approx(10 * 20 * 30)
    assert f["name"] == "Box 1"
    assert f["plugin"] == {"id": "boxes", "version": "1.0.0"}
    assert f["params"] == {"w": 10.0, "d": 20.0, "h": 30.0}


def test_registration_mutates_runners_and_labels(boxes):
    assert F.RUNNERS["py:boxes/box"] is pyfeature.host.run
    assert F.FEATURE_LABELS["py:boxes/box"] == "Box"
    assert [t for t, _, _ in pyfeature.feature_types() if t.startswith("py:boxes/")] == ["py:boxes/box",
                                                                                       "py:boxes/row"]
    assert pyfeature.default_params("py:boxes/row") == {"count": 2, "size": "small", "gap": True, "pitch": 20.0}
    pyfeature.unregister_plugin("boxes")
    assert "py:boxes/box" not in F.RUNNERS and "py:boxes/box" not in F.FEATURE_LABELS
    assert pyfeature.get("py:boxes/box") is None
    with pytest.raises(KeyError):
        pyfeature.default_params("py:boxes/box")


def test_every_solid_becomes_a_part(boxes):
    doc = Document()
    f = add_py(doc, "py:boxes/row", count=3, size="large")
    assert doc.errors == {}
    assert list(doc.state.parts) == [f"{f['id']}/1", f"{f['id']}/2", f"{f['id']}/3"]
    assert f["name"] == "Box row 1"


def test_expression_uses_variable(boxes):
    doc = Document()
    add(doc, "variable", name="w", expr="30")
    f = add_py(doc, "py:boxes/box", w="#w * 2")
    assert doc.errors == {}
    assert volume(doc, f"{f['id']}/1") == pytest.approx(60 * 20 * 30)


def test_param_named_like_a_builtin_numeric_key_is_left_to_the_host(tmp_path):
    # features.prepare would evaluate "count" and "angle" itself; a Python feature validates its own
    write_plugin(tmp_path, "keys", manifest("keys", "k:K"), {"k.py": (
        "from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox\n"
        "from freeshape.pyfeature.api import Feature, Integer, Angle\n"
        "class K(Feature):\n"
        "    id = 'k'\n    label = 'Keys'\n    description = 'x'\n"
        "    params = [Integer('count', 'Copies', 1, 1, 3), Angle('angle', 'Tilt', 0)]\n"
        "    def build(self, params, inputs):\n"
        "        assert isinstance(params['count'], int)\n"
        "        return BRepPrimAPI_MakeBox(params['count'], 1, 1).Shape()\n")})
    assert pyfeature.register_plugin(os.path.join(tmp_path, "keys")) is not None
    doc = Document()
    f = add_py(doc, "py:keys/k", count="#nope")
    assert doc.errors[f["id"]] == "Copies: unknown variable #nope"


def test_save_load_round_trip(boxes, tmp_path):
    doc = Document()
    add(doc, "variable", name="w", expr="30")
    f = add_py(doc, "py:boxes/box", w="#w * 2")
    v = volume(doc, f"{f['id']}/1")
    path = str(tmp_path / "studio.fshape")
    doc.save(path)
    with open(path) as fh:
        text = fh.read()
    assert '"type": "py:boxes/box"' in text
    saved = next(x for x in json.loads(text)["features"] if x["id"] == f["id"])
    assert saved == {"id": f["id"], "type": "py:boxes/box", "name": "Box 1", "suppressed": False,
                     "params": {"w": "#w * 2", "d": 20.0, "h": 30.0},
                     "plugin": {"id": "boxes", "version": "1.0.0"}}
    for leak in ("box.py", "BRepPrimAPI", "freeshape_user_plugins", "def build", "BoxRow", "pyplugins", "import"):
        assert leak not in text
    doc2 = Document.load(path)
    assert doc2.errors == {}
    assert volume(doc2, f"{f['id']}/1") == pytest.approx(v)


# --------------------------------------------------------------------------- hostile parameters

def test_invalid_params_error_and_later_feature_rebuilds(boxes):
    doc = Document()
    bad = [add_py(doc, "py:boxes/row", count=11),
           add_py(doc, "py:boxes/row", size="hexagon"),
           add_py(doc, "py:boxes/row", gap="yes"),
           add_py(doc, "py:boxes/box", w="#w +* (")]
    expected = ["Number of boxes", "Box size", "Leave gaps", "Width"]
    for f, label in zip(bad, expected):
        assert doc.errors.get(f["id"], "").startswith(label + ":"), (label, doc.errors)
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 40, 20))
    ext = add(doc, "extrude", regions=region_refs(doc, sid), depth=10.0, op="new")
    assert ext["id"] not in doc.errors
    assert volume(doc, f"{ext['id']}/1") == pytest.approx(40 * 20 * 10)


@pytest.mark.parametrize("params, label", [
    ({"count": 2.5}, "Number of boxes"), ({"count": True}, "Number of boxes"), ({"count": 0}, "Number of boxes"),
    ({"count": [1]}, "Number of boxes"), ({"count": float("nan")}, "Number of boxes"),
    ({"size": 1}, "Box size"), ({"gap": 1}, "Leave gaps"), ({"pitch": {"x": 1}}, "Pitch"),
    ({"pitch": "1e999"}, "Pitch"),
])
def test_host_rejects_bad_values(boxes, params, label):
    cls = pyfeature.get("py:boxes/row")
    with pytest.raises(K.KernelError, match=f"^{label}:"):
        pyfeature.host.validate(cls, params, {})


def test_unknown_keys_ignored_missing_keys_default(boxes):
    cls = pyfeature.get("py:boxes/row")
    out = pyfeature.host.validate(cls, {"count": 3.0, "__class__": "os.system", "evil": object()}, {})
    assert out == {"count": 3, "size": "small", "gap": True, "pitch": 20.0}
    assert type(out["count"]) is int
    with pytest.raises(K.KernelError):
        pyfeature.host.validate(cls, ["count", 3], {})


def test_build_failures_become_kernel_errors(tmp_path):
    write_plugin(tmp_path, "faulty", manifest("faulty", "m:Raises", "m:Empty", "m:Junk"), {"m.py": (
        "from freeshape.pyfeature.api import Feature\n"
        "class Raises(Feature):\n    id = 'raises'\n    label = 'Raiser'\n"
        "    def build(self, params, inputs):\n        return 1 / 0\n"
        "class Empty(Feature):\n    id = 'empty'\n    label = 'Empty'\n"
        "    def build(self, params, inputs):\n        return []\n"
        "class Junk(Feature):\n    id = 'junk'\n    label = 'Junk'\n"
        "    def build(self, params, inputs):\n        return 'a shape'\n")})
    assert pyfeature.register_plugin(os.path.join(tmp_path, "faulty")) is not None, pyfeature.problems()
    doc = Document()
    a, b, c = (add_py(doc, f"py:faulty/{x}") for x in ("raises", "empty", "junk"))
    assert doc.errors[a["id"]] == "Raiser: ZeroDivisionError: division by zero"
    assert doc.errors[b["id"]] == "Empty: the build made no solid"
    assert doc.errors[c["id"]] == "Junk: build returned str, not a shape"
    assert doc.state.parts == {}


# --------------------------------------------------------------------------- a Document cannot cause an import

def test_unknown_or_pathlike_types_import_nothing(boxes, tmp_path, monkeypatch, isolated):
    doc = Document()
    var = add(doc, "variable", name="w", expr="30")
    box = add_py(doc, "py:boxes/box")
    doc.features += [{"id": "F90", "type": "py:os/system", "name": "System 1", "suppressed": False,
                      "params": {"cmd": "rm -rf /"}, "plugin": {"id": "os", "version": "1"}},
                     {"id": "F91", "type": "py:../../evil/x", "name": "Evil 1", "suppressed": False,
                      "params": {}, "plugin": {"id": "../../evil", "version": "1"}}]
    path = str(tmp_path / "hostile.fshape")
    doc.save(path)

    calls = []
    real_import, real_spec = importlib.import_module, importlib.util.spec_from_file_location

    def rec_import(name, *a, **kw):
        calls.append(("import_module", str(name)))
        return real_import(name, *a, **kw)

    def rec_spec(name, location=None, *a, **kw):
        calls.append(("spec_from_file_location", f"{name} {location}"))
        return real_spec(name, location, *a, **kw)

    monkeypatch.setattr(importlib, "import_module", rec_import)
    monkeypatch.setattr(importlib.util, "spec_from_file_location", rec_spec)
    loaded = Document.load(path)

    assert str(os.path.abspath(isolated)) in registry._USER_ROOTS_LOADED   # the lazy user load did run
    for _, arg in calls:
        for bad in ("system", "evil", "..", "os."):
            assert bad not in arg, calls
        assert arg.split(" ")[0] not in ("os", "py:os/system")
    assert loaded.errors["F90"] == 'Python feature "System 1" needs plugin "os" 1, which is not installed'
    assert loaded.errors["F91"].startswith('Python feature "Evil 1" needs plugin "../../evil" 1')
    assert var["id"] not in loaded.errors and box["id"] not in loaded.errors
    assert f"{box['id']}/1" in loaded.state.parts


def test_missing_plugin_message():
    doc = Document()
    doc.features = [{"id": "F1", "type": "py:gears/spur", "name": "Spur gear 1", "suppressed": False,
                     "params": {}, "plugin": {"id": "gears", "version": "1.0.0"}}]
    doc.rebuild()
    assert doc.errors == {"F1": 'Python feature "Spur gear 1" needs plugin "gears" 1.0.0, which is not installed'}


def test_make_feature_of_unregistered_python_type_raises():
    with pytest.raises(KeyError):
        Document().make_feature("py:nope/x")


# --------------------------------------------------------------------------- loader

def test_addon_disabled_is_skipped(tmp_path):
    d = copy_plugin(BOXES, tmp_path)
    open(os.path.join(d, "ADDON_DISABLED"), "w").close()
    assert pyfeature.load_user_plugins(str(tmp_path)) == []
    assert pyfeature.plugin("boxes") is None


def test_user_plugin_replaces_bundled_one(tmp_path):
    bundled, user = tmp_path / "bundled", tmp_path / "user"
    copy_plugin(BOXES, bundled)
    copy_plugin(BOXES, user, version="2.0.0")
    [b] = pyfeature.load_bundled_plugins(str(bundled))
    assert (b.source, b.version) == ("bundled", "1.0.0")
    [u] = pyfeature.load_user_plugins(str(user))
    assert pyfeature.plugin("boxes") == u
    assert (u.source, u.version) == ("user", "2.0.0")
    assert pyfeature.get("py:boxes/box").__module__ == "freeshape_user_plugins.boxes.box"
    assert pyfeature.load_user_plugins(str(user)) == []       # idempotent
    doc = Document()
    f = add_py(doc, "py:boxes/box")
    assert f["plugin"] == {"id": "boxes", "version": "2.0.0"} and doc.errors == {}


def test_failing_plugins_are_problems_and_others_load(tmp_path):
    broken = copy_plugin(os.path.join(DATA, "broken_import"), tmp_path)
    badm = copy_plugin(os.path.join(DATA, "bad_manifest"), tmp_path)
    copy_plugin(BOXES, tmp_path)
    loaded = pyfeature.load_user_plugins(str(tmp_path))
    assert [p.id for p in loaded] == ["boxes"]
    assert any("RuntimeError: this plugin fails at import" in m for m in problem_for(broken))
    assert problem_for(badm)
    assert pyfeature.plugin("broken_import") is None
    assert not [n for n in __import__("sys").modules if n.startswith("freeshape_user_plugins.broken_import")]


@pytest.mark.parametrize("pid", ["a/b", "..", "../evil", "Boxes", "1x", "", "a.b"])
def test_manifest_id_must_be_plain(tmp_path, pid):
    d = copy_plugin(BOXES, tmp_path, name="plug", id=pid)
    assert pyfeature.load_user_plugins(str(tmp_path)) == []
    assert any("plugin id" in m for m in problem_for(d))
    assert pyfeature.plugin(pid) is None and pyfeature.plugin("boxes") is None


def test_bad_declaration_is_a_problem(tmp_path):
    d = write_plugin(tmp_path, "baddecl", manifest("baddecl", "m:NoBounds"), {"m.py": (
        "from freeshape.pyfeature.api import Feature, Integer, Choice\n"
        "class NoBounds(Feature):\n    id = 'nb'\n    label = 'No bounds'\n"
        "    params = [Integer('n', 'Teeth', 5, None, None), Choice('c', 'Kind', 'z', [('a', 'A')])]\n"
        "    def build(self, params, inputs):\n        return None\n")})
    assert pyfeature.load_user_plugins(str(tmp_path)) == []
    [msg] = problem_for(d)
    assert "Integer needs whole-number min and max" in msg and "bad default" in msg


def test_user_root_defaults_without_qt(monkeypatch, tmp_path):
    monkeypatch.delenv("FREESHAPE_PLUGIN_DIR")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    if os.name == "posix" and not __import__("sys").platform == "darwin":
        assert pyfeature.user_root() == os.path.join(str(tmp_path), "FreeShape", "plugins")
    monkeypatch.setenv("FREESHAPE_PLUGIN_DIR", "/somewhere")
    assert pyfeature.user_root() == "/somewhere"


# --------------------------------------------------------------------------- notices

def test_version_notice_acknowledge_and_undo(boxes):
    doc = Document()
    f = doc.make_feature("py:boxes/box")
    f["plugin"]["version"] = "0.9"
    doc.add_feature(f)
    assert doc.errors == {}
    assert pyfeature.notices(doc.features) == {f["id"]: "made with boxes 0.9, rebuilt with 1.0.0"}
    assert doc.acknowledge_plugin_version(f["id"]) is True
    assert pyfeature.notices(doc.features) == {}
    assert doc.feature(f["id"])["plugin"] == {"id": "boxes", "version": "1.0.0"}
    doc.undo()
    assert pyfeature.notices(doc.features) == {f["id"]: "made with boxes 0.9, rebuilt with 1.0.0"}


def test_missing_plugin_notice():
    feats = [{"id": "F1", "type": "py:gears/spur", "name": "Spur gear 1", "suppressed": False, "params": {},
              "plugin": {"id": "gears", "version": "1.0.0"}},
             {"id": "F2", "type": "extrude", "name": "Extrude 1", "suppressed": False, "params": {}}]
    assert pyfeature.notices(feats) == {"F1": 'needs plugin "gears" 1.0.0, which is not installed'}


# --------------------------------------------------------------------------- static checks

def test_package_is_headless():
    for name in os.listdir(PKG):
        if name.endswith(".py"):
            with open(os.path.join(PKG, name)) as fh:
                assert "PySide6" not in fh.read(), name
