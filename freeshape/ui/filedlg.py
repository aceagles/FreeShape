"""File dialogs that start in the folder used last (Open, Save as, Import, Export …), remembered between runs.
Same arguments as QFileDialog's, so call sites read the same.

Two folders are remembered, one per `kind`: "doc" for opening, saving and importing, "export" for exports. With
nothing remembered for a kind the dialog opens in the document's folder (`doc_path`), else on the Desktop, else
in the home folder."""
from __future__ import annotations

import os

from PySide6.QtCore import QSettings, QStandardPaths
from PySide6.QtWidgets import QFileDialog

_KEY = "last_folder"
# per kind: the setting written, then older names still read so a remembered folder survives an update
_KEYS = {"doc": (_KEY, "last_dir/doc"), "export": ("last_folder/export", "last_dir/export")}


def _settings():
    return QSettings("FreeShape", "FreeShape")


def _desktop():
    return QStandardPaths.writableLocation(QStandardPaths.DesktopLocation) or ""


def last_folder(kind="doc", settings=None):
    """The folder a `kind` dialog used last, or "" when none is remembered (or it is gone)."""
    s = _settings() if settings is None else settings
    for key in _KEYS[kind]:
        d = str(s.value(key, "") or "")
        if d and os.path.isdir(d):
            return d
    return ""


def start_dir(kind="doc", doc_path=None, desktop=None, settings=None):
    """Folder a `kind` dialog opens in: the last one used for that kind, else the document's, else the Desktop,
    else the home folder."""
    d = last_folder(kind, settings)
    if d:
        return d
    if doc_path and os.path.isdir(os.path.dirname(os.path.abspath(doc_path))):
        return os.path.dirname(os.path.abspath(doc_path))
    desk = _desktop() if desktop is None else desktop
    if desk and os.path.isdir(desk):
        return desk
    return os.path.expanduser("~")


def start_path(path="", kind="doc", doc_path=None, desktop=None, settings=None):
    """Where a dialog opens: a full path as given, otherwise the name (if any) in the folder `start_dir` gives."""
    path = path or ""
    if os.path.isabs(path) and os.path.isdir(os.path.dirname(path)):
        return path
    return os.path.join(start_dir(kind, doc_path, desktop, settings), path)


def remember(path, kind="doc", settings=None):
    if path:
        s = _settings() if settings is None else settings
        s.setValue(_KEYS[kind][0], os.path.dirname(os.path.abspath(path)))


def getOpenFileName(parent, caption="", path="", filt="", selected="", kind="doc", doc_path=None):
    res = QFileDialog.getOpenFileName(parent, caption, start_path(path, kind, doc_path), filt, selected)
    remember(res[0], kind)
    return res


def getOpenFileNames(parent, caption="", path="", filt="", selected="", kind="doc", doc_path=None):
    res = QFileDialog.getOpenFileNames(parent, caption, start_path(path, kind, doc_path), filt, selected)
    if res[0]:
        remember(res[0][0], kind)
    return res


def getSaveFileName(parent, caption="", path="", filt="", selected="", kind="doc", doc_path=None):
    res = QFileDialog.getSaveFileName(parent, caption, start_path(path, kind, doc_path), filt, selected)
    remember(res[0], kind)
    return res


def getExistingDirectory(parent, caption="", path="", kind="doc", doc_path=None):
    """Pick a folder. The folder chosen is not remembered: a Document folder is opened, not browsed into."""
    return QFileDialog.getExistingDirectory(parent, caption, start_path(path, kind, doc_path))
