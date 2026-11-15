"""Builds the example Documents as folders, ready for `python -m freeshape serve <folder>`:

    examples/documents/bracket/   one Part Studio (bracket)
    examples/documents/hinge/     the hinge Part Studio (hinge_parts) and the hinge Assembly (hinge), linked

They are made from the single example files beside this script (bracket.fshape, hinge_parts.fshape, hinge.fasm;
see make_bracket.py and make_hinge.py) with FolderDocument.create and add_files. Tests copy these folders to a
temporary folder before editing them.

    python examples/make_documents.py [output folder]      (default: examples/documents)

The folder's `.git` repository is removed after the build: a git repository cannot be committed inside this one.
Versions need a repository, so a copy that wants them starts one. Tab ids are random, so a rebuild writes
different ids; the tab names and kinds stay the same.
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape import doc_folder as DF
from freeshape import doc_registry as R

HERE = os.path.dirname(os.path.abspath(__file__))

# Document folder name -> (Document name, the single files that become its tabs, in tab order)
DOCUMENTS = {
    "bracket": ("Bracket", ["bracket.fshape"]),
    "hinge": ("Hinge", ["hinge_parts.fshape", "hinge.fasm"]),
}


def build(out_dir, registry):
    """Write every example Document under `out_dir`, replacing a folder of the same name. Returns the folders."""
    folders = []
    for folder, (name, files) in DOCUMENTS.items():
        path = os.path.join(out_dir, folder)
        shutil.rmtree(path, ignore_errors=True)
        doc = DF.FolderDocument.create(path, name=name, registry=registry)
        doc.add_files([os.path.join(HERE, f) for f in files])
        doc.close()
        shutil.rmtree(os.path.join(path, ".git"), ignore_errors=True)
        folders.append(path)
    return folders


def main(argv):
    out_dir = argv[1] if len(argv) > 1 else os.path.join(HERE, "documents")
    os.makedirs(out_dir, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:      # keep the user's Document registry out of it
        for path in build(out_dir, R.DocRegistry(os.path.join(scratch, "registry.json"))):
            print("wrote", path)


if __name__ == "__main__":
    main(sys.argv)
