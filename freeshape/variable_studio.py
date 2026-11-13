"""VariableStudioDoc: a Document tab holding named value rows, Onshape's "Variable Studio" --
`{name, expression}` pairs evaluated in order with freeshape.expr, each row able to reference an earlier
one as "#name" (the same variable syntax every other expression field in FreeShape already uses -- see
freeshape/expr.py). A unit is written inside the expression itself ("20 mm"), since expr.py already parses
it; there is no separate unit column or key.

Saved as its own small JSON file, following the same shape and atomic-write convention as AssemblyDoc /
DrawingDoc (see freeshape/assembly.py, freeshape/drawing.py) and registered as a FolderDocument tab kind
in freeshape/doc_folder.py. Headless -- no Qt; freeshape/ui/variable_panel.py is the table widget on top
of this.
"""
from __future__ import annotations

import json
import os

from . import kernel as K
from . import migrations as M
from .expr import ExprError, parse_number

FILE_FORMAT = "freeshape-variables"


class VariableStudioDoc:
    def __init__(self):
        self.name = "Variables 1"
        self.rows: list[dict] = []     # [{"name": str, "expression": str}, ...], in evaluation order
        self.path = None
        self.dirty = False

    # ------------------------------------------------------------------ rows
    def add_row(self, name="", expression="", index=None):
        row = {"name": name, "expression": expression}
        self.rows.insert(len(self.rows) if index is None else index, row)
        self.dirty = True
        return row

    def remove_row(self, index):
        self.rows.pop(index)
        self.dirty = True

    def set_row(self, index, name=None, expression=None):
        row = self.rows[index]
        if name is not None:
            row["name"] = name
        if expression is not None:
            row["expression"] = expression
        self.dirty = True

    # ------------------------------------------------------------------ evaluation
    def values(self):
        """Evaluate every row in order, each able to reference an earlier row's value as "#name".
        Returns (values, errors): values maps row name -> float for every row that evaluated;
        errors maps row index -> a message that names the row ("w: unknown variable #x")."""
        values, errors = {}, {}
        for i, row in enumerate(self.rows):
            name = str(row.get("name") or "").strip()
            label = name or f"row {i + 1}"
            try:
                v = parse_number(row.get("expression", ""), variables=values, strict=True)
            except ExprError as e:
                errors[i] = f"{label}: {e}"
                continue
            if name:
                values[name] = v
        return values, errors

    # ------------------------------------------------------------------ files
    def to_json(self):
        return json.dumps({"format": FILE_FORMAT, "version": M.latest_version("variables"), "name": self.name,
                            "rows": [dict(r) for r in self.rows]}, indent=1)

    def to_data(self) -> bytes:
        """The tab's data as a Document store holds it (the bytes `save` writes)."""
        return self.to_json().encode("utf-8")

    def save(self, path=None):
        self.path = path or self.path
        txt = self.to_data().decode("utf-8")
        tmp = self.path + ".tmp"
        with open(tmp, "w") as fh:
            fh.write(txt)
        os.replace(tmp, self.path)
        self.dirty = False

    @staticmethod
    def load(path):
        with open(path, "rb") as fh:
            data = fh.read()
        return VariableStudioDoc.from_data(data, path=path)

    @staticmethod
    def from_data(data, path=None, blobs=None):
        """A Variable Studio from its bytes. `path` is the file they came from, if any (it names the file in
        errors and becomes the tab's `path`). `blobs` is unused: a Variable Studio keeps none."""
        d = json.loads(bytes(data).decode("utf-8"))
        if d.get("format") != FILE_FORMAT:
            raise K.KernelError("Not a FreeShape Variable Studio")
        d = M.migrate("variables", d, path if path is not None else "This Variable Studio")
        doc = VariableStudioDoc()
        doc.name = d.get("name", doc.name)
        doc.rows = [{"name": r.get("name", ""), "expression": r.get("expression", "")}
                    for r in d.get("rows", [])]
        doc.path = path
        return doc
