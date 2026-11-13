"""I4.4: the Part Studio class is PartStudioDoc; `Document` stays as an alias for the old name.

Saved files must not change: every example Part Studio loads and saves to the same bytes it gave before the rename.
"""
import ast
import glob
import hashlib
import json
import os

import pytest

import freeshape.document as FD

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# sha256 of load-then-save, captured on the tree before the rename. examples/bracket.fshape carries no uid, so a
# load makes a fresh one: the test pins `doc.uid` for a file without a uid, and only then are the bytes stable.
SAVED_SHA256 = {
    "examples/bolted_bracket_parts.fshape": "c6656dab63d22738468033383e115aa11b39d50193bfc5f6af3f600d387bcd8d",
    "examples/bracket.fshape": "947661246dac9d6987f63137705cae6d3e47c0c126eeb1a134b1434c3571f026",
    "examples/hinge_parts.fshape": "11482a5678b064a452fadbc1e90baa9b3b1b379c06e194205911646fce8ac6d5",
}


def test_class_and_alias():
    assert FD.PartStudioDoc.__name__ == "PartStudioDoc"
    assert FD.Document is FD.PartStudioDoc


def _real_uses(path):
    """Line numbers where `path` imports or uses the Part Studio class under the name `Document`."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    mod_names, bound, hits = set(), set(), []
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            m = n.module or ""
            if (n.level == 0 and m == "freeshape.document") or (n.level >= 1 and m == "document"):
                for a in n.names:
                    if a.name == "Document":
                        bound.add(a.asname or a.name)
                        hits.append(n.lineno)
            elif (n.level == 0 and m == "freeshape") or (n.level >= 1 and m == ""):
                mod_names.update(a.asname or a.name for a in n.names if a.name == "document")
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name == "freeshape.document":
                    mod_names.add(a.asname or "freeshape.document")
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and n.attr == "Document" and ast.unparse(n.value) in mod_names:
            hits.append(n.lineno)
        elif isinstance(n, ast.Name) and n.id in bound:
            hits.append(n.lineno)
    return sorted(set(hits))


def test_no_use_of_old_name_outside_document_module():
    found = {}
    for top in ("freeshape", "scripts"):
        for dp, _, files in os.walk(os.path.join(ROOT, top)):
            for f in files:
                p = os.path.join(dp, f)
                if f.endswith(".py") and os.path.relpath(p, ROOT) != "freeshape/document.py":
                    hits = _real_uses(p)
                    if hits:
                        found[os.path.relpath(p, ROOT)] = hits
    assert not found, f"still use the old name Document from freeshape.document: {found}"


def test_scanner_finds_each_import_form(tmp_path):
    forms = {
        "from freeshape.document import Document\nDocument()\n": 2,
        "from .document import Document as D\nD.load('x')\n": 2,
        "from . import document as M\nM.Document()\n": 1,
        "import freeshape.document\nfreeshape.document.Document()\n": 1,
        "from freeshape.document import PartStudioDoc\n'Document'\nclass FolderDocument: pass\n": 0,
    }
    for i, (src, n) in enumerate(forms.items()):
        p = tmp_path / f"m{i}.py"
        p.write_text(src)
        assert len(_real_uses(str(p))) == n, src


def test_examples_cover_all_shipped_part_studios():
    found = {os.path.relpath(p, ROOT) for p in glob.glob(os.path.join(ROOT, "examples", "*.fshape"))}
    assert found == set(SAVED_SHA256)


@pytest.mark.parametrize("rel", sorted(SAVED_SHA256))
def test_load_then_save_bytes_unchanged(rel, tmp_path):
    src = os.path.join(ROOT, rel)
    doc = FD.PartStudioDoc.load(src)
    with open(src, encoding="utf-8") as fh:
        if "uid" not in json.load(fh):
            doc.uid = "0" * 32
    out = str(tmp_path / "out.fshape")
    doc.save(out)
    with open(out, "rb") as fh:
        data = fh.read()
    # I4.9 bumped the Part Studio file version from 2 to 3 (import data moves to blobs; these files have none):
    # apart from that number the bytes are the ones recorded before the rename
    assert data.count(b'\n "version": 3,\n') == 1
    data = data.replace(b'\n "version": 3,\n', b'\n "version": 2,\n')
    assert hashlib.sha256(data).hexdigest() == SAVED_SHA256[rel]
