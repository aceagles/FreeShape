"""I4.3: one registry of tab kinds (freeshape/tabkinds.py) instead of a copy in every module that dispatches on
the kind of a tab file."""
import ast
import json
import os
import subprocess
import sys

import pytest

from freeshape import doc_folder as DF
from freeshape import kernel as K
from freeshape import tabkinds as TK
from freeshape.engine import executor as EX

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "freeshape")
EXPECTED = {"studio": ".fshape", "assembly": ".fasm", "drawing": ".fdwg", "variables": ".fvars"}


def test_the_registry_has_the_four_kinds():
    assert TK.kinds() == ["studio", "assembly", "drawing", "variables"]
    assert {k: TK.get(k).ext for k in TK.kinds()} == EXPECTED
    assert [TK.get(k).label for k in TK.kinds()] == ["Part Studio", "Assembly", "Drawing", "Variable Studio"]
    assert [TK.get(k).chain for k in TK.kinds()] == ["document", "assembly", "drawing", "variables"]


@pytest.mark.parametrize("kind", list(EXPECTED))
def test_a_records_format_tag_is_the_tag_its_class_writes(kind):
    rec = TK.get(kind)
    doc = rec.make()
    assert json.loads(doc.to_json())["format"] == rec.format_tag
    assert TK.kind_for_format(rec.format_tag) == kind
    from freeshape import migrations as M
    assert M.latest_version(rec.chain) >= 1


def test_kind_for_path_maps_each_extension():
    for kind, ext in EXPECTED.items():
        assert TK.kind_for_path("/x/part" + ext) == kind
        assert TK.kind_for_path("/x/PART" + ext.upper()) == kind
    assert TK.kind_for_path("/x/model.step") is None
    assert TK.kind_for_path("/x/noext") is None


def test_the_old_tables_are_live_views_of_the_registry():
    assert dict(DF.EXTENSIONS) == EXPECTED
    assert set(DF.LOADERS) == set(DF.FACTORIES) == set(EXPECTED)
    assert tuple(EX.KINDS) == tuple(EXPECTED) and "variables" in EX.KINDS


def test_the_registry_imports_without_qt_or_the_engine():
    code = ("import sys; import freeshape.tabkinds; "
            "bad = [m for m in sys.modules if m.startswith(('PySide6', 'freeshape.engine', 'freeshape.ui'))]; "
            "print(bad)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                         env={**os.environ, "PYTHONPATH": os.path.dirname(PKG)})
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"


# =========================================================================== a fifth kind
class _Stub:
    """The least a tab document needs: name, dirty, path, to_json, save, load."""

    def __init__(self, name="Stub 1"):
        self.name = name
        self.dirty = True
        self.path = None
        self.note = ""

    def to_json(self):
        return json.dumps({"format": "freeshape-stub", "version": 1, "name": self.name, "note": self.note})

    def save(self, path=None):
        raise AssertionError("FolderDocument writes the tab file itself")

    @staticmethod
    def load(path):
        with open(path) as fh:
            d = json.load(fh)
        doc = _Stub(d["name"])
        doc.note = d["note"]
        doc.dirty = False
        doc.path = path
        return doc


def test_a_fifth_kind_is_listed_loaded_and_saved_by_a_folder_document(tmp_path):
    TK.register(TK.TabKind("stub", ".fstub", "freeshape-stub", None, _Stub.load, _Stub, "Stub", "Stubs"))
    try:
        assert TK.kinds()[-1] == "stub" and "stub" in EX.KINDS and DF.EXTENSIONS["stub"] == ".fstub"
        assert TK.kind_for_path("a.fstub") == "stub"
        fd = DF.FolderDocument.create(str(tmp_path / "D"))
        tab = fd.add_tab("stub", "Widget")
        tab.doc.note = "hello"
        fd.save()
        assert tab.file == "Widget.fstub" and os.path.exists(os.path.join(fd.path, tab.file))
        assert "*.fstub -merge" in DF._attribute_lines()
        back = DF.FolderDocument.open(fd.path)
        assert not back.errors, back.errors
        assert [(t.kind, t.doc.note) for t in back.tabs] == [("stub", "hello")]
        assert not [w for w in back.document_warnings() if w["code"] == "not-in-manifest"]
    finally:
        TK.unregister("stub")
    assert "stub" not in TK.kinds() and "stub" not in DF.EXTENSIONS and "stub" not in EX.KINDS
    assert TK.kind_for_path("a.fstub") is None


# =========================================================================== no stray copies
# string literals that name an extension in prose a person reads, not in dispatch (module, text)
ALLOWED = [
    ("ui/assembly_ui.py", "A FreeShape document (.fshape) or a STEP file"),   # a tooltip about the Part picker
    ("ui/assembly_ui.py", "Parts (*.fshape *.fcad *.step *.stp);;All files (*)"),  # the Part picker takes Part Studios only
    ("ui/mainwindow.py", "Add this Part Studio to a Document to use Derived for a loose .fshape."),  # a status message
]


def _literals(tree):
    """Every string constant that is not a docstring."""
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                docs.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            yield node.lineno, node.value


def test_no_module_but_tabkinds_names_a_tab_extension():
    exts = tuple(EXPECTED.values())
    found = []
    for root, _dirs, files in os.walk(PKG):
        for f in files:
            if not f.endswith(".py"):
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, PKG).replace(os.sep, "/")
            if rel == "tabkinds.py":
                continue
            with open(full, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), full)
            for line, text in _literals(tree):
                if any(e in text for e in exts) and (rel, text) not in ALLOWED:
                    found.append(f"{rel}:{line}: {text[:70]!r}")
    assert not found, "extension strings outside freeshape/tabkinds.py:\n" + "\n".join(found)


# =========================================================================== .fvars is accepted
def test_add_files_accepts_a_variable_studio_file(tmp_path):
    from freeshape.variable_studio import VariableStudioDoc
    v = VariableStudioDoc()
    v.name = "Loose vars"
    v.rows = [{"name": "w", "expression": "12"}]
    loose = tmp_path / "Loose vars.fvars"
    v.save(str(loose))
    fd = DF.FolderDocument.create(str(tmp_path / "D"))
    added = fd.add_files([str(loose)])
    assert [(t.kind, t.file) for t in added] == [("variables", "Loose vars.fvars")]
    assert added[0].doc.rows == [{"name": "w", "expression": "12"}]
    back = DF.FolderDocument.open(fd.path)
    assert not back.errors and [t.kind for t in back.tabs] == ["variables"]
    with pytest.raises(K.KernelError, match=r"\.fvars"):
        fd.add_files([str(tmp_path / "x.step")])


def test_homes_open_filter_lists_variable_studios():
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from freeshape.ui import home as H
    groups = H.open_dialog_filter().split(";;")
    assert "*.fvars" in groups[0]
    assert "Variable Studios (*.fvars)" in groups
    assert "Part Studios (*.fshape *.fcad)" in groups
    assert groups[-1] == "All files (*)"
    assert H.kind_label(".fvars") == "Variable Studio" and H.kind_label(".STP") == "STEP file"
