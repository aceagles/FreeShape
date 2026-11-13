"""Headless Document-safety smoke (slice S2, gate I2.4): no MainWindow, no Qt, no network.

Part of the cumulative smoke gate (see s00_baseline.py for the convention). In a temporary directory it builds a
Document with a Part Studio, an Assembly, a Drawing and a Variable Studio, then walks the slice claim one clause
per step: a truncated tab file leaves the other three tabs open and is listed with a reason; a manifest with
duplicate tab ids, a non-object tab entry or invalid JSON is refused with a message naming the fault; unknown
manifest keys survive open and save; a Part Studio save that fails part-way leaves the previous file byte-identical;
an Assembly file opened as a Part Studio gives a named error; a Variable Studio file from a newer version is refused
through the same migration chain as an Assembly. Ends by asserting PySide6 was never imported.

One line per step, ending `ok`; a failed step prints why and the script exits 1 with no `SQ2 OK`.

    both fs_py scripts/smoke/sq2_documents.py
"""
import json
import os
import re
import shutil
import sys
import tempfile
import traceback

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

TMP = tempfile.mkdtemp(prefix="sq2_documents_")
os.environ["FREESHAPE_DATA_DIR"] = os.path.join(TMP, "appdata")     # nothing may touch a real user folder
os.environ["XDG_DATA_HOME"] = os.path.join(TMP, "xdg")
os.environ["HOME"] = os.path.join(TMP, "home")
for _d in ("appdata", "xdg", "home"):
    os.makedirs(os.path.join(TMP, _d))

from conftest import TOP, add, rect_lines, region_refs, sketch_feature  # noqa: E402
from freeshape import doc_folder as DF  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import migrations as M  # noqa: E402
from freeshape.assembly import AssemblyDoc  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.variable_studio import VariableStudioDoc  # noqa: E402


# ---------------------------------------------------------------------- helpers
class Fail(Exception):
    pass


def check(cond, msg):
    if not cond:
        raise Fail(msg)


STEPS = []


def step(name):
    """Run a step function; print `name ... ok`, or the reason and exit 1."""
    def deco(fn):
        try:
            fn()
        except Exception as e:                       # noqa: BLE001 -- any failure fails the gate
            print(f"{name} ... FAILED: {e}")
            if not isinstance(e, Fail):
                traceback.print_exc()
            _cleanup()
            sys.exit(1)
        STEPS.append(name)
        print(f"{name} ... ok", flush=True)
        return fn
    return deco


def _cleanup():
    shutil.rmtree(TMP, ignore_errors=True)


def p(*parts):
    return os.path.join(TMP, *parts)


def raises(exc, fn, *fragments):
    """Call fn; it must raise `exc` whose message contains every fragment. Returns the message."""
    try:
        fn()
    except exc as e:
        msg = str(e)
        for f in fragments:
            check(f in msg, f"message {msg!r} does not name {f!r}")
        return msg
    raise Fail(f"{getattr(fn, '__name__', 'call')} did not raise {exc.__name__}")


def manifest_path(folder):
    return os.path.join(folder, DF.MANIFEST_NAME)


def read_manifest(folder):
    with open(manifest_path(folder)) as fh:
        return json.load(fh)


def write_manifest(folder, data):
    with open(manifest_path(folder), "w") as fh:
        fh.write(data if isinstance(data, str) else json.dumps(data))


def build_document(name):
    """A saved Document with a Part Studio (a box), an Assembly, a Drawing and a Variable Studio."""
    doc = DF.FolderDocument.create(p(name), name=name)
    studio = doc.add_tab("studio", "Plate").doc
    sid = sketch_feature(studio, lambda s: rect_lines(s, 0, 0, 20.0, 20.0), TOP)
    add(studio, "extrude", regions=region_refs(studio, sid), depth=10.0, op="new")
    check(not studio.errors, studio.errors)
    doc.add_tab("assembly", "Asm")
    doc.add_tab("drawing", "Dwg")
    vs = doc.add_tab("variables", "Dims").doc
    vs.rows = [{"name": "w", "expression": "20 mm"}]
    doc.save()
    check([t.kind for t in doc.tabs] == ["studio", "assembly", "drawing", "variables"], [t.kind for t in doc.tabs])
    check(not doc.errors, doc.errors)
    return doc


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


S = {}          # state shared between steps


# ---------------------------------------------------------------------- the claim, one clause per step
@step("build Document: Part Studio, Assembly, Drawing, Variable Studio")
def _():
    S["doc"] = build_document("Doc")


@step("truncated tab: other three open, damaged one listed with a reason")
def _():
    doc = build_document("Trunc")
    bad = doc.tabs[2]                                    # the Drawing
    full = os.path.join(doc.path, bad.file)
    text = read_bytes(full)
    with open(full, "wb") as fh:
        fh.write(text[: len(text) // 2])
    opened = DF.FolderDocument.open(doc.path)
    check(len(opened.tabs) == 4, len(opened.tabs))
    loaded = [t.doc is not None for t in opened.tabs]
    check(loaded == [True, True, False, True], loaded)
    check(list(opened.errors) == [bad.id] and opened.errors[bad.id].strip(), opened.errors)
    check(any(bad.id in w["message"] for w in opened.warnings), opened.warnings)


@step("duplicate tab ids refused, message names the fault")
def _():
    doc = build_document("Dup")
    data = read_manifest(doc.path)
    data["tabs"][1]["id"] = data["tabs"][0]["id"]
    write_manifest(doc.path, data)
    raises(K.KernelError, lambda: DF.FolderDocument.open(doc.path), "duplicate tab id", data["tabs"][0]["id"])


@step("tab entry that is not an object refused, message names the fault")
def _():
    doc = build_document("NotObj")
    data = read_manifest(doc.path)
    data["tabs"][1] = "T2"
    write_manifest(doc.path, data)
    raises(K.KernelError, lambda: DF.FolderDocument.open(doc.path), "must be an object")


@step("manifest that is not JSON refused, message names the fault")
def _():
    doc = build_document("NotJson")
    write_manifest(doc.path, "{nope")
    raises(K.KernelError, lambda: DF.FolderDocument.open(doc.path), "not valid JSON")


@step("unknown manifest keys survive open and save")
def _():
    doc = build_document("Keys")
    data = read_manifest(doc.path)
    data["future_top"] = {"a": [1]}
    data["tabs"][2]["future_tab"] = "keep"
    write_manifest(doc.path, data)
    opened = DF.FolderDocument.open(doc.path)
    check(not opened.errors, opened.errors)
    opened.name = "Renamed"
    opened.save()
    out = read_manifest(doc.path)
    check(out["future_top"] == {"a": [1]}, out)
    check(out["tabs"][2]["future_tab"] == "keep" and "future_tab" not in out["tabs"][0], out["tabs"])
    check(out["name"] == "Renamed", out["name"])


@step("Part Studio save that fails part-way leaves the file byte-identical")
def _():
    doc = build_document("Atomic")
    studio = doc.tab("T1").doc
    path = p("plate.fshape")
    studio.save(path)
    before = read_bytes(path)
    studio.name = "changed"
    real_replace = os.replace

    def boom(*a, **k):
        raise OSError("interrupted")
    os.replace = boom
    try:
        raises(OSError, lambda: studio.save(path), "interrupted")
    finally:
        os.replace = real_replace
    check(read_bytes(path) == before, "the previous file changed after a failed save")
    check(not os.path.exists(path + ".tmp"), "a temp file was left behind")
    studio.save(path)
    check(read_bytes(path) != before, "the save did not write after the failure cleared")


@step("Assembly file opened as a Part Studio gives a named error")
def _():
    doc = build_document("AsPS")
    asm_file = os.path.join(doc.path, doc.tab("T2").file)
    msg = raises(K.KernelError, lambda: Document.load(asm_file), "Assembly")
    check(msg.strip(), "empty message")


@step("Variable Studio from a newer version refused, same chain as Assembly")
def _():
    doc = build_document("NewVS")
    path = p("dims.fvars")
    doc.tab("T4").doc.save(path)
    d = json.load(open(path))
    check(d["version"] == M.latest_version("variables"), d["version"])
    d["version"] += 1
    with open(path, "w") as fh:
        json.dump(d, fh)
    var_msg = raises(K.KernelError, lambda: VariableStudioDoc.load(path), "file version", "Update FreeShape")
    asm = p("new.fasm")
    with open(asm, "w") as fh:
        json.dump({"format": "freeshape-assembly", "version": M.latest_version("assembly") + 1}, fh)
    asm_msg = raises(K.KernelError, lambda: AssemblyDoc.load(asm), "file version", "Update FreeShape")

    def shape(msg, file):
        return re.sub(r"\d+", "N", msg.replace(file, "<file>"))
    check(shape(var_msg, path) == shape(asm_msg, asm), (var_msg, asm_msg))


@step("headless: PySide6 never imported")
def _():
    check("PySide6" not in sys.modules, "PySide6 was imported")


print(f"SQ2 OK: {len(STEPS)} steps")
_cleanup()
