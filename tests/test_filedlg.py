"""Where a file dialog opens (freeshape/ui/filedlg.py): the folder used last for that kind of dialog, else the
document's folder, else the Desktop, else home. Open / save / import ("doc") and export keep separate folders."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from freeshape.ui import filedlg  # noqa: E402


class _Settings:
    """Stands in for QSettings: value(key, default) and setValue(key, value)."""

    def __init__(self, **values):
        self.d = dict(values)

    def value(self, key, default=None):
        return self.d.get(key, default)

    def setValue(self, key, value):
        self.d[key] = value


def _dirs(tmp_path, *names):
    out = []
    for n in names:
        d = tmp_path / n
        d.mkdir()
        out.append(d)
    return out


def test_start_dir_remembered_then_document_then_desktop(tmp_path):
    s = _Settings()
    desk, docdir, last = _dirs(tmp_path, "desk", "docs", "last")
    doc = str(docdir / "a.part")
    assert filedlg.start_dir("doc", None, desktop=str(desk), settings=s) == str(desk)
    assert filedlg.start_dir("doc", doc, desktop=str(desk), settings=s) == str(docdir)
    filedlg.remember(str(last / "x.part"), "doc", settings=s)
    assert filedlg.start_dir("doc", doc, desktop=str(desk), settings=s) == str(last)
    assert filedlg.start_dir("export", doc, desktop=str(desk), settings=s) == str(docdir)      # per kind
    last.rmdir()                                                   # a vanished folder is ignored
    assert filedlg.start_dir("doc", None, desktop=str(desk), settings=s) == str(desk)
    assert filedlg.start_dir("doc", None, desktop=str(tmp_path / "gone"), settings=s) == os.path.expanduser("~")


def test_export_folder_is_kept_apart_from_the_open_folder(tmp_path):
    s = _Settings()
    desk, opened, exported = _dirs(tmp_path, "desk", "opened", "exported")
    filedlg.remember(str(opened / "a.part"), settings=s)
    filedlg.remember(str(exported / "a.step"), "export", settings=s)
    assert filedlg.last_folder("doc", s) == str(opened)
    assert filedlg.last_folder("export", s) == str(exported)
    assert filedlg.start_path("b.step", "export", desktop=str(desk), settings=s) == str(exported / "b.step")
    assert filedlg.start_path("b.part", desktop=str(desk), settings=s) == str(opened / "b.part")


def test_a_full_path_wins_over_the_remembered_folder(tmp_path):
    s = _Settings()
    (last, other) = _dirs(tmp_path, "last", "other")
    filedlg.remember(str(last / "x.part"), settings=s)
    full = str(other / "y.part")
    assert filedlg.start_path(full, settings=s) == full            # Save as an existing file


def test_folders_remembered_under_the_old_setting_names_are_still_used(tmp_path):
    old_doc, old_export, desk = _dirs(tmp_path, "old_doc", "old_export", "desk")
    s = _Settings(**{"last_dir/doc": str(old_doc), "last_dir/export": str(old_export)})
    assert filedlg.start_dir("doc", None, desktop=str(desk), settings=s) == str(old_doc)
    assert filedlg.start_dir("export", None, desktop=str(desk), settings=s) == str(old_export)
    newer = tmp_path / "newer"
    newer.mkdir()
    filedlg.remember(str(newer / "a.part"), settings=s)            # the current name takes over once written
    assert filedlg.start_dir("doc", None, desktop=str(desk), settings=s) == str(newer)
    assert s.d["last_dir/doc"] == str(old_doc)
