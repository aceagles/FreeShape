"""I11.13: freeshape/export_roots.py, the one place that writes an export into a server folder. No server here. Each
test builds its own folder, so each passes alone."""
import errno
import os
import stat
import subprocess
import sys
import time

import pytest

from freeshape import export_roots as ER
from freeshape import step_import as SI

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _root(tmp_path, name="out"):
    folder = tmp_path / name
    folder.mkdir()
    return ER.ExportRoot(name, str(folder)), folder


def _names(folder):
    return sorted(os.listdir(folder))


def test_clean_export_name_makes_one_safe_component():
    n = ER.clean_export_name
    assert n("bracket", "stl") == "bracket.stl" and n("bracket", "step") == "bracket.step"
    assert n("a/b/../c.STL", "stl") == "c.stl" and n("..\\..\\x", "stl") == "x.stl" and n(".hidden", "stl") == "hidden.stl"
    assert n("part.step", "stl") == "part.stl" and n("p.stp", "step") == "p.step"
    for empty in ("", None, "...", "\x00\x07", "/", "..", ".stl"):
        assert n(empty, "stl") == "export.stl" and n(empty, "step") == "export.step"
    assert n("x" * 300, "stl") == "x" * 100 + ".stl"
    wide = n("\U0001F600" * 80, "step")                       # 4-byte characters: 100 of them would be 400 bytes
    assert wide.endswith(".step") and len(wide.encode()) <= 255 and wide[:-5] == "\U0001F600" * 62
    assert len(n("\U0001F600" * 300, "stl").encode()) <= 255
    for name in ("../x", "/etc/passwd", "a/b", "..", "a\\b", "x\x00y"):
        assert "/" not in n(name, "stl") and "\\" not in n(name, "stl") and "\0" not in n(name, "stl")


def test_import_names_are_as_before_with_one_shared_helper():
    c = SI.clean_file_name
    assert c("a\\b\\c.step") == "c.step" and c("a\x00b\n.step") == "ab.step"
    long = c("n" * 300 + ".step")
    assert long.endswith(".step") and len(long) == SI.MAX_FILE_NAME
    assert c("") == "import.step" and c(None, "brep") == "import.brep" and c("..") == "import.step"
    assert c("part") == "part.step" and c("part.stp") == "part.stp"
    assert c("a.brep", "step") == "a.brep.step" and c("a.step", "brep") == "a.step.brep"
    assert c(".hidden.step") == ".hidden.step"                 # the import name keeps a leading dot, as before
    from freeshape import export_options as EO
    assert SI.display_name is EO.display_name                  # one display rule, used by both names


def test_write_makes_the_file_with_mode_0644_and_leaves_no_temporary(tmp_path):
    r, folder = _root(tmp_path)
    old = os.umask(0o077)                                      # the mode is set on purpose, not by the umask
    try:
        assert r.write("a.stl", b"solid a") is False
    finally:
        os.umask(old)
    assert _names(folder) == ["a.stl"] and (folder / "a.stl").read_bytes() == b"solid a"
    st = os.lstat(folder / "a.stl")
    assert stat.S_IMODE(st.st_mode) == 0o644 == ER.FILE_MODE and stat.S_ISREG(st.st_mode) and st.st_nlink == 1
    assert st.st_uid == os.getuid() and st.st_gid == os.lstat(folder).st_gid     # the folder's group on a setgid folder


def test_a_second_write_without_replace_is_refused_and_replace_overwrites(tmp_path):
    r, folder = _root(tmp_path)
    r.write("a.stl", b"first")
    with pytest.raises(ER.ExportError) as e:
        r.write("a.stl", b"second")
    assert e.value.status == 409 and e.value.code == "exists"
    assert (folder / "a.stl").read_bytes() == b"first" and _names(folder) == ["a.stl"]
    assert r.write("a.stl", b"second", replace=True) is True
    assert (folder / "a.stl").read_bytes() == b"second" and _names(folder) == ["a.stl"]
    assert r.write("b.stl", b"x", replace=True) is False       # replace of a name that is not there is a plain save
    assert stat.S_IMODE(os.lstat(folder / "a.stl").st_mode) == 0o644


@pytest.mark.parametrize("replace", [False, True])
def test_a_symlink_destination_is_refused_and_the_outside_file_is_unchanged(tmp_path, replace):
    r, folder = _root(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"precious")
    os.symlink(str(outside), str(folder / "link.stl"))
    os.symlink(str(tmp_path / "nowhere"), str(folder / "dangling.stl"))
    for name in ("link.stl", "dangling.stl"):
        with pytest.raises(ER.ExportError) as e:
            r.write(name, b"new", replace=replace)
        assert e.value.status == 409
    assert outside.read_bytes() == b"precious" and not (tmp_path / "nowhere").exists()
    assert _names(folder) == ["dangling.stl", "link.stl"] and os.path.islink(folder / "link.stl")


@pytest.mark.parametrize("replace", [False, True])
def test_a_folder_destination_is_refused(tmp_path, replace):
    r, folder = _root(tmp_path)
    (folder / "d.stl").mkdir()
    (folder / "d.stl" / "keep").write_bytes(b"k")
    with pytest.raises(ER.ExportError) as e:
        r.write("d.stl", b"new", replace=replace)
    assert e.value.status == 409 and _names(folder) == ["d.stl"] and _names(folder / "d.stl") == ["keep"]


@pytest.mark.parametrize("replace", [False, True])
def test_a_fifo_destination_is_refused(tmp_path, replace):
    r, folder = _root(tmp_path)
    os.mkfifo(str(folder / "p.stl"))
    with pytest.raises(ER.ExportError) as e:
        r.write("p.stl", b"new", replace=replace)               # a blocking open of the FIFO would hang this test
    assert e.value.status == 409
    assert _names(folder) == ["p.stl"] and stat.S_ISFIFO(os.lstat(folder / "p.stl").st_mode)


def test_a_root_swapped_for_a_symlink_after_start_is_refused(tmp_path):
    r, folder = _root(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    folder.rmdir()
    os.symlink(str(other), str(folder))
    with pytest.raises(ER.ExportError) as e:
        r.write("a.stl", b"x")
    assert e.value.status == 409 and os.listdir(other) == []
    assert "started" in str(e.value) and str(tmp_path) not in str(e.value)


def test_a_root_whose_parent_was_swapped_for_a_symlink_is_refused(tmp_path):
    parent = tmp_path / "parent"
    (parent / "out").mkdir(parents=True)
    r = ER.ExportRoot("out", str(parent / "out"))
    moved = tmp_path / "moved"
    other = tmp_path / "other"
    (other / "out").mkdir(parents=True)
    parent.rename(moved)
    os.symlink(str(other), str(parent))                        # the folder opens, but its realpath is not the kept one
    with pytest.raises(ER.ExportError):
        r.write("a.stl", b"x")
    assert os.listdir(other / "out") == []


def test_a_write_that_fails_part_way_leaves_no_file_and_no_temporary(tmp_path, monkeypatch):
    r, folder = _root(tmp_path)
    real = os.write
    calls = []

    def full(fd, data):
        calls.append(len(data))
        if len(calls) == 2:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(fd, data[:4])                              # a short write, then the failure
    monkeypatch.setattr(ER.os, "write", full)
    with pytest.raises(ER.ExportError) as e:
        r.write("a.stl", b"0123456789")
    assert e.value.status == 507 and len(calls) == 2 and str(tmp_path) not in str(e.value)
    assert _names(folder) == []
    monkeypatch.undo()
    assert r.write("a.stl", b"0123456789") is False and (folder / "a.stl").read_bytes() == b"0123456789"


def test_when_hard_links_are_refused_the_name_is_created_with_o_excl_and_copied(tmp_path, monkeypatch):
    r, folder = _root(tmp_path)
    linked = []

    def refuse(*a, **k):
        linked.append(a)
        raise OSError(errno.EPERM, "Operation not permitted")
    monkeypatch.setattr(ER.os, "link", refuse)
    assert r.write("a.stl", b"bytes") is False
    assert linked and (folder / "a.stl").read_bytes() == b"bytes" and _names(folder) == ["a.stl"]
    assert stat.S_IMODE(os.lstat(folder / "a.stl").st_mode) == 0o644
    with pytest.raises(ER.ExportError) as e:                    # the fallback never overwrites either
        r.write("a.stl", b"other")
    assert e.value.code == "exists" and (folder / "a.stl").read_bytes() == b"bytes"
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"precious")
    os.symlink(str(outside), str(folder / "l.stl"))
    with pytest.raises(ER.ExportError):
        r.write("l.stl", b"other")
    assert outside.read_bytes() == b"precious" and _names(folder) == ["a.stl", "l.stl"]


def test_a_failed_copy_in_the_no_hard_link_path_removes_the_name(tmp_path, monkeypatch):
    r, folder = _root(tmp_path)
    monkeypatch.setattr(ER.os, "link", lambda *a, **k: (_ for _ in ()).throw(OSError(errno.ENOTSUP, "no links")))
    real = os.write
    calls = []

    def full(fd, data):
        calls.append(1)
        if len(calls) > 1:                                      # the temporary file is written, the copy is not
            raise OSError(errno.ENOSPC, "full")
        return real(fd, data)
    monkeypatch.setattr(ER.os, "write", full)
    with pytest.raises(ER.ExportError) as e:
        r.write("a.stl", b"bytes")
    assert e.value.status == 507 and _names(folder) == []


def test_the_temporary_file_is_made_in_the_roots_own_folder(tmp_path, monkeypatch):
    r, folder = _root(tmp_path)
    seen = []
    real = os.open

    def spy(path, flags, mode=0o777, *, dir_fd=None):
        seen.append((path, dir_fd is not None))
        return real(path, flags, mode, dir_fd=dir_fd)
    monkeypatch.setattr(ER.os, "open", spy)
    r.write("a.stl", b"x")
    tmp = [p for p, rel in seen if str(p).startswith(ER.TMP_PREFIX)]
    assert len(tmp) == 1 and all(rel for p, rel in seen if p != r.path)      # relative to the root's descriptor


def test_a_name_must_be_one_component(tmp_path):
    r, folder = _root(tmp_path)
    for bad in ("", ".", "..", "a/b", "../x", "a\\b", "x\0y", "x" * 300, ER.TMP_PREFIX + "a.tmp", None, 5):
        with pytest.raises(ER.ExportError) as e:
            r.write(bad, b"x")
        assert e.value.status == 400
    assert _names(folder) == []


def test_stale_temporary_files_are_removed_at_start(tmp_path):
    folder = tmp_path / "out"
    folder.mkdir()
    old, fresh, mine = ER.TMP_PREFIX + "old.tmp", ER.TMP_PREFIX + "fresh.tmp", "x.tmp"
    link, sub = ER.TMP_PREFIX + "link.tmp", ER.TMP_PREFIX + "dir.tmp"
    for n in (old, fresh, mine):
        (folder / n).write_bytes(b"t")
    os.symlink(str(tmp_path), str(folder / link))
    (folder / sub).mkdir()
    long_ago = time.time() - 2 * 3600
    for n in (old, mine):
        os.utime(folder / n, (long_ago, long_ago))
    ER.ExportRoot("out", str(folder))
    assert _names(folder) == sorted([fresh, mine, link, sub])


def test_parse_spec_refuses_what_it_must(tmp_path):
    folder = tmp_path / "out"
    folder.mkdir()
    assert ER.parse_spec(f"shared={folder}") == ("shared", os.path.realpath(folder))
    link = tmp_path / "link"
    os.symlink(str(folder), str(link))
    assert ER.parse_spec(f"x={link}")[1] == os.path.realpath(folder)      # kept as its realpath
    (tmp_path / "file").write_bytes(b"x")
    for bad in ("Bad_Name=" + str(folder), "justaname", "=" + str(folder), "x" * 33 + "=" + str(folder),
                f"x={tmp_path}/missing", f"x={tmp_path}/file", "x="):
        with pytest.raises(ValueError):
            ER.parse_spec(bad)


@pytest.mark.skipif(os.getuid() == 0, reason="root can write anywhere")
def test_parse_spec_refuses_a_folder_it_cannot_write(tmp_path):
    folder = tmp_path / "ro"
    folder.mkdir()
    folder.chmod(0o555)
    try:
        with pytest.raises(ValueError, match="writable"):
            ER.parse_spec(f"x={folder}")
    finally:
        folder.chmod(0o755)


def test_check_overlaps_refuses_equal_inside_and_around(tmp_path):
    docs, data, lib = tmp_path / "Docs", tmp_path / "data", tmp_path / "lib"
    for p in (docs, data, lib, tmp_path / "out", docs / "inner"):
        p.mkdir(exist_ok=True)
    protected = [("the Documents root", str(docs)), ("the data folder", str(data)), ("file root 'lib'", str(lib)),
                 ("the Document folder", None)]
    ER.check_overlaps([("a", str(tmp_path / "out"))], protected)
    for bad in (docs, docs / "inner", tmp_path, data, lib):
        with pytest.raises(ER.OverlapError):
            ER.check_overlaps([("a", str(bad))], protected)
    with pytest.raises(ER.OverlapError):
        ER.check_overlaps([("a", str(tmp_path / "out")), ("b", str(tmp_path / "out"))], protected)
    ER.check_overlaps([("a", str(tmp_path / "out"))], [("x", str(tmp_path / "out2"))])    # a name prefix is not inside


def test_the_module_loads_no_qt():
    code = "import sys; import freeshape.export_roots, freeshape.serve; " \
           "print([m for m in sys.modules if m.split('.')[0].startswith('PySide')])"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, timeout=120)
    assert out.stdout.strip() == "[]", (out.stdout, out.stderr)
