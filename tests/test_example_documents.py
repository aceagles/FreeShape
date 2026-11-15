"""I1.1: the example Documents as folders (examples/documents/bracket, examples/documents/hinge, made by
examples/make_documents.py). Each test builds its own folders or copies the committed ones, so it passes alone."""
import importlib.util
import os
import shutil
import subprocess
import sys

import pytest

from freeshape import doc_folder as DF
from freeshape import docstore as DS
from freeshape import doc_registry as R
from freeshape.session import DocumentSession

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCUMENTS = os.path.join(ROOT, "examples", "documents")
NAMES = ("bracket", "hinge")


def _tabs(folder):
    doc = DF.FolderDocument.open(folder)
    try:
        return [(t.name, t.kind) for t in doc.tabs]
    finally:
        doc.close()


def _copy(tmp_path, name):
    dst = str(tmp_path / name)
    shutil.copytree(os.path.join(DOCUMENTS, name), dst)
    return dst


def _session(folder):
    return DocumentSession(DS.FolderStore(folder))


def test_each_folder_opens_and_lists_its_tabs(tmp_path):
    assert _tabs(_copy(tmp_path, "bracket")) == [("bracket", "studio")]
    assert _tabs(_copy(tmp_path, "hinge")) == [("hinge_parts", "studio"), ("hinge", "assembly")]


def test_bracket_session_outputs_one_part(tmp_path):
    folder = _copy(tmp_path, "bracket")
    with _session(folder) as s:
        (tab,) = s.tabs()
        assert tab["state"] != "failed" and tab["error"] is None
        out = s.outputs(tab["id"])
    assert len(out.parts) == 1


def test_hinge_assembly_outputs_list_instances(tmp_path):
    folder = _copy(tmp_path, "hinge")
    with _session(folder) as s:
        tabs = {t["kind"]: t for t in s.tabs()}
        out = s.outputs(tabs["assembly"]["id"])
    assert len(out.instances) >= 3


def test_make_documents_matches_committed(tmp_path):
    spec = importlib.util.spec_from_file_location("make_documents", os.path.join(ROOT, "examples", "make_documents.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = str(tmp_path / "out")
    os.makedirs(out)
    folders = mod.build(out, R.DocRegistry(str(tmp_path / "registry.json")))
    assert sorted(os.path.basename(f) for f in folders) == sorted(NAMES)
    for name in NAMES:
        assert _tabs(os.path.join(out, name)) == _tabs(os.path.join(DOCUMENTS, name))


def test_serve_prints_ready_line(tmp_path):
    folder = _copy(tmp_path, "bracket")
    proc = subprocess.Popen([sys.executable, "-m", "freeshape", "serve", folder, "--port", "0"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, cwd=ROOT)
    try:
        line = proc.stdout.readline()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    assert line.startswith("FREESHAPE_SERVE_READY port=")
