"""Building the "import" feature of a STEP or BREP file, with no Qt: the desktop's Import STEP command
(`MainWindow.import_step_file`) and `freeshape serve` (POST .../import) both make the feature here.

The feature carries the file's bytes inline (base64 `params.data`); `PartStudioDoc.add_feature` turns that into a
held blob before it first builds (I4.9). The parse happens when the feature builds, in the tab's worker."""
from __future__ import annotations

import base64
import os

from .document import PartStudioDoc
from .export_options import display_name

MAX_FILE_NAME = 100             # longest file name kept for display
STEP_MAGIC = b"ISO-10303-21;"
BREP_MAGIC = b"DBRep_DrawableShape"
_BOM = b"\xef\xbb\xbf"


def sniff(raw) -> str | None:
    """"step" or "brep" when `raw` starts, after a UTF-8 BOM and whitespace, with the magic of that format, else None."""
    head = bytes(raw[:64])
    if head.startswith(_BOM):
        head = head[len(_BOM):]
    head = head.lstrip()
    if head.startswith(STEP_MAGIC):
        return "step"
    if head.startswith(BREP_MAGIC):
        return "brep"
    return None


def clean_file_name(name, kind="step") -> str:
    """`name` as shown, never as a path: what follows the last `/` or `\\`, control characters dropped, at most
    MAX_FILE_NAME characters. A name with nothing left is "import.step" / "import.brep". The feature reads a file
    as BREP by its extension (`features.is_brep_name`), so the name ends in an extension of `kind`; where it does
    not, the right one is appended (`a.brep` holding STEP is `a.brep.step`)."""
    text = display_name(name)
    ext = ".brep" if kind == "brep" else ".step"
    if not text or set(text) <= {"."}:
        return "import" + ext
    exts = (".brep", ".brp") if kind == "brep" else (".step", ".stp")
    own = next((e for e in exts if text.lower().endswith(e)), None)
    stem = text[:-len(own)] if own else text
    own = text[len(stem):] if own else ext                   # the extension as the name spells it
    return stem[:MAX_FILE_NAME - len(own)] + own


def import_feature(fname, raw, doc=None) -> dict:
    """The import feature for the file `fname` holding `raw`. With `doc` it is made by `doc.make_feature` (the
    desktop: the id and the type's defaults come from the Part Studio); without, the id is "" and the engine gives
    the feature one when it adds it."""
    if doc is not None:
        f = doc.make_feature("import")
    else:
        f = {"id": "", "type": "import", "suppressed": False}
    f["name"] = "Import " + fname
    f["params"] = {"data": base64.b64encode(raw).decode(), "filename": fname}
    return f


def new_studio(fname) -> PartStudioDoc:
    """An empty Part Studio named after the file (its name without the extension)."""
    doc = PartStudioDoc()
    doc.name = os.path.splitext(fname)[0]
    return doc
