"""Helpers for scripts/web (common.sh, the browser specs): they build and change the Documents root the served
client runs against, using the app's own modules (doc_registry, doc_lock). No Qt. Run from the repo root.

    doc_tools.py registry <root> <data dir>      register every Document folder of <root> the way the desktop does
    doc_tools.py hold <root> <folder> <program>  write a live lock on <root>/<folder> for <program> (e.g. desktop)
    doc_tools.py release <root> <folder>         remove that lock again
    doc_tools.py remove <root> <data dir> <folder>   delete a Document folder a spec made, and forget it
"""
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")))

from freeshape import doc_lock  # noqa: E402
from freeshape import doc_registry as R  # noqa: E402


def manifest_id(folder):
    with open(os.path.join(folder, R.MANIFEST_NAME)) as fh:
        return json.load(fh)["id"]


def main(argv):
    cmd, args = argv[1], argv[2:]
    if cmd == "registry":
        root, data = args
        registry = R.DocRegistry(os.path.join(data, R.REGISTRY_NAME))
        for name in sorted(os.listdir(root)):
            folder = os.path.join(root, name)
            if os.path.isfile(os.path.join(folder, R.MANIFEST_NAME)):
                registry.register_created(manifest_id(folder), folder)
    elif cmd == "hold":
        root, name, program = args
        doc_lock.acquire(os.path.join(root, name), program, timeout=3600)
    elif cmd == "release":
        root, name = args
        folder = os.path.join(root, name)
        held = doc_lock.holder(folder, timeout=3600)
        if held is not None:
            doc_lock.Lock(folder, held.program, held.instance).release()
    elif cmd == "remove":
        root, data, name = args
        folder = os.path.join(root, name)
        if os.path.isfile(os.path.join(folder, R.MANIFEST_NAME)):
            R.DocRegistry(os.path.join(data, R.REGISTRY_NAME)).forget(manifest_id(folder), folder)
        shutil.rmtree(folder, ignore_errors=True)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
