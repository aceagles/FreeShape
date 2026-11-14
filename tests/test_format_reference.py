"""I4.2: docs/reference/document-format.md names every key the code writes, lists every migration step, and
the glossary and decision records in the repo link to each other.

Each test builds one object of a kind with every optional key present, saves it, and checks that each key of
what was written appears as a backticked name in the first two cells of a table row under the right heading.
Removing a key name from the reference makes the matching test fail.
"""
import glob
import json
import os
import re

import pytest
from conftest import TOP, add, rect_lines, region_refs, sketch_feature
from test_drawing import DIRECTIONS, drawing, plate
from test_golden_mates import _asm, _down, _face, _src, _up
from test_pyfeature_outputs import LABELLED, add_py

from freeshape import assembly as A
from freeshape import doc_folder as DF
from freeshape import links as L
from freeshape import migrations as M
from freeshape import pyfeature
from freeshape.document import Document
from freeshape.drawing import DrawingDoc
from freeshape.variable_studio import VariableStudioDoc

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REFERENCE = os.path.join(REPO, "docs", "reference", "document-format.md")
ADR_DIR = os.path.join(REPO, "docs", "adr")


# =========================================================================== reading the reference

def _sections():
    """{heading: text} for every heading of the reference (levels 2 to 4), text up to the next heading."""
    out, name, buf = {}, None, []
    with open(REFERENCE, encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"#{2,4} (.+?)\s*$", line)
            if m:
                if name is not None:
                    out[name] = "".join(buf)
                name, buf = m.group(1), []
            else:
                buf.append(line)
    if name is not None:
        out[name] = "".join(buf)
    return out


def _named(section):
    """Every backticked name in the first two cells of the table rows under `section`."""
    text = _sections()[section]
    names = set()
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        for cell in line.strip().strip("|").split("|")[:2]:
            names.update(re.findall(r"`([^`]+)`", cell))
    return names


def _missing(written, section):
    return sorted(set(written) - _named(section))


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    user = tmp_path / "user_plugins"
    user.mkdir()
    monkeypatch.setenv("FREESHAPE_PLUGIN_DIR", str(user))
    before = {p.id for p in pyfeature.plugins()}
    yield
    for p in pyfeature.plugins():
        if p.id not in before:
            pyfeature.unregister_plugin(p.id)


# =========================================================================== builders

def _studio():
    """A Part Studio with a Python feature (so it stores a build), one feature of every kind that carries a
    key outside the common ones, a part override with a material, and hidden parts and features."""
    assert pyfeature.register_plugin(LABELLED) is not None
    doc = Document()
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 4, 4), TOP)
    add(doc, "extrude", regions=region_refs(doc, sid), depth=2, op="new")
    py = add_py(doc, "py:labelled/box")
    assert doc.errors == {}
    tab = DF.Tab("T9", "studio", "other.fshape", "Other")
    extra = []
    for ftype in ("derived", "variable_studio_ref", "mate_connector", "context", "delete_part", "import", "axis"):
        extra.append(doc.make_feature(ftype))
    extra[0]["params"]["source"] = L.studio_source(tab, "F1/1", document="doc-id", version="v1")
    extra[1]["params"].update(source={"kind": "variables", "tab": "T3", "name": "V"}, names=["w"])
    extra[3]["params"].update(assembly="T2", instance="I1", capture="0" * 64)
    extra[5]["params"].update(data="U1RFUA==", filename="p.step")      # written as `blob` (I4.9)
    doc.features.extend(extra)
    doc.part_overrides = {f"{py['id']}/1": {"name": "Box", "color": [0.1, 0.2, 0.3], "material": "Steel"}}
    doc.hidden_parts = {f"{py['id']}/1"}
    doc.hidden_features = {sid}
    doc.part_aliases = {"F1:1": "F1/1"}
    doc.rollback = None
    return doc, extra, py


def _assembly():
    a = _asm()
    ip, ib1, ib2, ib3, ib4 = (a.insert(_src(n)) for n in ("plate", "block", "block", "block", "block"))
    m1 = a.make_mate("slider")
    m1.update(a=a.connector(ip, "face", _face(a, ip, _up)), b=a.connector(ib1, "face", _face(a, ib1, _down)))
    a.add_mate(m1)
    m2 = a.make_mate("slider")
    m2.update(a=a.connector(ip, "face", _face(a, ip, lambda i: i.get("n") is not None and i["n"][0] > 0.9)),
              b=a.connector(ib2, "face", _face(a, ib2, _down)))
    a.add_mate(m2)
    a.set_limits(m1["id"], "tz", 0.0, 50.0)
    r = a.make_relation("linear")
    r.update(a={"mate": m1["id"], "dof": "tz"}, b={"mate": m2["id"], "dof": "tz"}, ratio=2.0)
    a.add_relation(r)
    a.add_group([ib3, ib4])
    cid = a.add_instance_origin_connector(ip)
    m3 = a.make_mate("fastened")
    m3.update(a=a.named_connector(ip, cid), b=a.connector(ib3, "face", _face(a, ib3, _down)))
    a.add_mate(m3)
    a.instance(ib1)["color"] = [0.5, 0.5, 0.5]
    a.instance(ib2)["suppressed"] = True
    # a studio source of every form, and the other two source kinds
    tab = DF.Tab("T9", "studio", "other.fshape", "Other")
    a.instance(ib1)["source"].update(L.studio_source(tab, "F1/1", document="doc-id", version="v1"))
    a.instance(ib2)["source"]["path"] = os.path.join(REPO, "x.fshape")
    key = a.add_step_blob("U1RFUA==")
    a.instances.append({"id": "I90", "name": "Step <1>", "source": {"kind": "step", "blob": key, "index": 0,
                                                                     "filename": "p.step"},
                        "T": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], "fixed": False,
                        "hidden": False, "part_name": "p", "material": None})
    a.instances.append({"id": "I91", "name": "Std <1>", "T": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0],
                                                                [0, 0, 0, 1]], "fixed": False, "hidden": False,
                        "part_name": "s", "material": None,
                        "source": {"kind": "standard", "type": "sp:fasteners/iso4762", "plugin": "fasteners",
                                   "plugin_version": "1", "params": {"size": "M3", "length": 10}}})
    return a


def _drawing(tmp_path):
    g = drawing([plate()], projection="first")
    g.source = {"kind": "studio", "tab": "T1", "part": "F1/1", "studio_name": "S", "part_name": "P", "flat": False,
                "bends": [], "document": "doc-id", "version": "v1"}
    front = g.add_view(*DIRECTIONS["Front"], (100, 150), checkpoint=False)
    g.add_projected(front, (220, 150))
    g.add_view(*DIRECTIONS["Isometric"], (300, 80), kind="iso", checkpoint=False)
    lo, hi = (0.0, 0.0, 0.0), (60.0, 0.0, 0.0)
    end = lambda p: {"kind": "vertex", "p": list(p), "i": 0, "n": 8}
    g.add_annot({"type": "lin", "view": front["id"], "a": end(lo), "b": end(hi), "orient": "h", "pos": [100, 120],
                 "dp": 2, "prefix": "~", "suffix": " max", "text": "", "tol": {"type": "sym", "up": 0.1, "lo": 0.1,
                                                                                "dp": 2}})
    g.add_annot({"type": "note", "text": "hello", "pos": [20.0, 20.0], "height": 3.5})
    g.refresh(notify=False)
    return g


# =========================================================================== keys written

def test_manifest_and_tab_entry_keys_are_documented(tmp_path):
    fd = DF.FolderDocument()
    fd.path = str(tmp_path / "Doc")
    for kind in ("studio", "assembly", "drawing", "variables"):
        fd.add_tab(kind)
    fd.save()
    with open(os.path.join(fd.path, DF.MANIFEST_NAME)) as fh:
        data = json.load(fh)
    assert data["version"] == DF.FORMAT_VERSION
    assert _missing(data, "Document manifest") == []
    assert _missing({k for t in data["tabs"] for k in t}, "Tab entry") == []
    for t in data["tabs"]:
        with open(os.path.join(fd.path, t["file"])) as fh:
            assert json.load(fh)["_tab_id"] == t["id"]
    assert "`_tab_id`" in _sections()["Key stamped into every Tab file"]
    assert f"Current version: {DF.FORMAT_VERSION}." in _sections()["Document manifest"]


def test_part_studio_keys_are_documented(tmp_path):
    doc, extra, py = _studio()
    path = str(tmp_path / "s.fshape")
    doc.save(path)
    with open(path) as fh:
        data = json.load(fh)
    assert "python_builds" in data and data["hidden"] and data["hidden_features"] and data["overrides"]
    assert data["version"] == M.latest_version("document")
    assert _missing(data, "Part Studio file") == []
    assert _missing({k for f in data["features"] for k in f}, "Feature") == []
    assert "plugin" in {k for f in data["features"] for k in f}
    assert _missing({k for o in data["overrides"].values() for k in o}, "Part override") == []
    entry = data["python_builds"][py["id"]]
    assert _missing(entry, "Python build entry") == []
    assert _missing({k for p in entry["parts"] for k in p}, "Python build entry") == []
    params = {k for f in extra for k in f["params"] if k != "resolved"}
    params |= {k for f in data["features"] if f["type"] == "import" for k in f["params"]}
    assert "blob" in params
    assert _missing(params, "Feature types") == []
    assert f"Latest version {data['version']}." in _sections()["Chain document"]


def test_assembly_keys_are_documented(tmp_path):
    a = _assembly()
    path = str(tmp_path / "a.fasm")
    a.path = path
    data = json.loads(a.to_json())
    assert data["connectors"] and data["groups"] and data["relations"] and "blobs" not in data
    assert data["version"] == M.latest_version("assembly")
    assert _missing(data, "Assembly file") == []
    assert _missing({k for i in data["instances"] for k in i}, "Instance") == []
    assert {"color", "suppressed", "_brep_blob", "material"} <= {k for i in data["instances"] for k in i}
    assert _missing({k for i in data["instances"] for k in i["source"]}, "Instance source") == []
    assert _missing({k for m in data["mates"] for k in m}, "Mate") == []
    assert "limits" in {k for m in data["mates"] for k in m}
    assert _missing({k for m in data["mates"] for end in (m["a"], m["b"]) for k in end}, "Mate end") == []
    assert _missing({k for g in data["groups"] for k in g}, "Group") == []
    assert _missing({k for r in data["relations"] for k in r}, "Relation") == []
    assert _missing({k for c in data["connectors"] for k in c}, "Assembly connector") == []


def test_drawing_keys_are_documented(tmp_path):
    g = _drawing(tmp_path)
    g.path = str(tmp_path / "d.fdwg")
    data = json.loads(g.to_json())
    assert "_brep_blob" in data and "_views_blob" in data
    assert data["version"] == M.latest_version("drawing")
    assert _missing(data, "Drawing file") == []
    assert _missing(data["source"], "Drawing source") == []
    assert _missing(data["sheet"], "Sheet") == []
    assert _missing(data["title"], "Title block") == []
    assert _missing({k for v in data["views"] for k in v}, "View") == []
    assert _missing({k for a in data["annots"] for k in a}, "Annotation") == []
    assert {v["type"] for v in data["views"]} == {"base", "projected", "iso"}


def test_variable_studio_keys_are_documented(tmp_path):
    v = VariableStudioDoc()
    v.add_row("w", "20 mm")
    data = json.loads(v.to_json())
    assert data["version"] == M.latest_version("variables")
    assert _missing(data, "Variable Studio file") == []
    assert _missing({k for r in data["rows"] for k in r}, "Row") == []


# =========================================================================== migration chains

@pytest.mark.parametrize("fmt", sorted(M._CHAINS))
def test_every_registered_step_is_listed(fmt):
    section = _sections()[f"Chain {fmt}"]
    rows = re.findall(r"^\| (\d+) to (\d+) \| `([^`]+)` \|", section, flags=re.M)
    chain = M._CHAINS[fmt]
    assert len(rows) == len(chain)
    assert [r[2] for r in rows] == [s.__name__ for s in chain]
    assert [(int(a), int(b)) for a, b, _ in rows] == [(i + 1, i + 2) for i in range(len(chain))]
    assert f"Latest version {M.latest_version(fmt)}." in section
    assert f"{len(chain)} step" in section


# =========================================================================== glossary and decision records

def test_glossary_and_decision_records_are_in_the_repo():
    assert os.path.isfile(os.path.join(REPO, "CONTEXT.md"))
    names = sorted(os.path.basename(p) for p in glob.glob(os.path.join(ADR_DIR, "*.md")))
    assert [n[:4] for n in names] == [f"{i:04d}" for i in range(1, 12)]


def _markdown_files():
    return [os.path.join(REPO, "CONTEXT.md"), REFERENCE] + sorted(glob.glob(os.path.join(ADR_DIR, "*.md")))


def test_no_vault_tags_or_wiki_links_remain():
    for path in _markdown_files():
        text = open(path, encoding="utf-8").read()
        assert "#exclude-all-tags" not in text, path
        assert "[[" not in text, path


def test_relative_links_resolve():
    link = re.compile(r"\]\(([^)\s]+)\)")
    checked = 0
    for path in _markdown_files():
        for target in link.findall(open(path, encoding="utf-8").read()):
            if re.match(r"[a-z]+:", target) or target.startswith("#"):
                continue
            dest = os.path.normpath(os.path.join(os.path.dirname(path), target.split("#")[0]))
            assert os.path.exists(dest), f"{os.path.relpath(path, REPO)} links to missing {target}"
            checked += 1
    assert checked > 10


def test_adr_0002_has_the_implementation_state():
    text = open(os.path.join(ADR_DIR, "0002-one-worker-process-per-tab.md"), encoding="utf-8").read()
    assert "## State of the implementation" in text
