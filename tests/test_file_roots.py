"""I11.2: freeshape/file_roots.py, the index of a server file root. Each test builds its own small root in a
temporary folder (never the real component library), so each passes alone."""
import hashlib
import json
import os
import subprocess
import sys

import pytest

from freeshape import file_roots as FR

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STEP = b"ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;\n"
MANIFEST = ("source,licence,path,bytes,authors,url\n"
            'lib1,CC-BY-3.0,a/b608.step,10,"Ann Author, Bo Writer",https://example.org/b608.step\n'
            "lib1,Weird <licence> as written,c/deep/d.stp,10,,\n")


def _w(path, data=STEP):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def make_root(tmp_path):
    """root/: lib1/a/{b608,plain}.step (+ parts.json naming b608), lib1/c/deep/d.stp, top.step, manifest.csv, and
    three links that lead out of it: link.step and lib1/a/linked (a folder) and lib1/a/dirlink.step."""
    root = tmp_path / "root"
    out = tmp_path / "outside"
    _w(str(root / "lib1" / "a" / "b608.step"))
    _w(str(root / "lib1" / "a" / "plain_part.step"))
    _w(str(root / "lib1" / "c" / "deep" / "d.stp"))
    _w(str(root / "top.step"))
    _w(str(out / "secret.step"))
    _w(str(out / "folder" / "inner.step"))
    (root / "lib1" / "a" / "parts.json").write_text(json.dumps(
        [{"id": "b608", "name": "Skate Bearing 608", "description": "8 mm bore", "tags": ["bearing", "skate"]}]))
    (root / "manifest.csv").write_text(MANIFEST)
    os.symlink(str(out / "secret.step"), str(root / "link.step"))
    os.symlink(str(out / "folder"), str(root / "lib1" / "a" / "linked"))
    os.symlink(str(out / "secret.step"), str(root / "lib1" / "a" / "dirlink.step"))
    return str(root), str(out)


def _index(tmp_path):
    root, out = make_root(tmp_path)
    r = FR.FileRoot("lib", root)
    return r, r.index(), root, out


def test_the_index_names_files_from_parts_json_and_licences_from_the_manifest_and_leaves_links_out(tmp_path):
    r, idx, root, out = _index(tmp_path)
    assert sorted(i.entry.rel for i in idx.items) == [
        "lib1/a/b608.step", "lib1/a/plain_part.step", "lib1/c/deep/d.stp", "top.step"]
    b = idx.by_rel["lib1/a/b608.step"]
    assert b.entry.name == "Skate Bearing 608" and b.entry.tags == ("bearing", "skate")
    assert (b.licence, b.authors, b.url) == ("CC-BY-3.0", "Ann Author, Bo Writer", "https://example.org/b608.step")
    d = idx.by_rel["lib1/c/deep/d.stp"]
    assert d.licence == "Weird <licence> as written" and d.authors == "" and d.url == ""      # as written
    p = idx.by_rel["top.step"]
    assert (p.licence, p.authors, p.url) == (None, None, None)
    assert all("link" not in i.entry.rel and "inner" not in i.entry.rel and "secret" not in i.entry.rel
               for i in idx.items)


def test_an_entry_id_is_the_first_16_hex_digits_of_the_sha256_of_its_relative_path(tmp_path):
    r, idx, *_ = _index(tmp_path)
    for i in idx.items:
        assert i.id == hashlib.sha256(i.entry.rel.encode()).hexdigest()[:16]
        assert idx.by_id[i.id] is i and i.id in json.dumps(i.view())
    assert len({i.id for i in idx.items}) == len(idx.items)


def test_folders_are_a_table_the_dir_value_is_looked_up_in(tmp_path):
    r, idx, *_ = _index(tmp_path)
    assert sorted(idx.folders) == ["", "lib1", "lib1/a", "lib1/c", "lib1/c/deep"]
    top = idx.listing("")
    assert [d["path"] for d in top["dirs"]] == ["lib1"] and top["dirs"][0]["files"] == 3
    assert [e["file"] for e in top["entries"]] == ["top.step"] and top["total"] == 1
    a = idx.listing("lib1/a")
    assert a["dirs"] == [] and [e["name"] for e in a["entries"]] == ["plain part", "Skate Bearing 608"]
    for bad in ("..", "../..", "/etc", "lib1/../lib1", "lib1/", "lib1/a/linked", "nope", "lib1\\a", "lib1/a\x00"):
        assert idx.listing(bad) is None, bad
        assert idx.search("step", bad) is None, bad


def test_listing_pages_and_search_keeps_every_word_and_can_stay_in_a_folder(tmp_path):
    r, idx, *_ = _index(tmp_path)
    assert [e["file"] for e in idx.listing("lib1/a", 1, 1)["entries"]] == ["b608.step"]
    hit = idx.search("bearing")
    assert [e["path"] for e in hit["entries"]] == ["lib1/a/b608.step"] and hit["total"] == 1 and hit["dirs"] == []
    assert idx.search("bearing skate")["total"] == 1 and idx.search("bearing nothing")["total"] == 0
    assert idx.search("step", "lib1/c")["total"] == 0 and idx.search("deep", "lib1/c")["total"] == 1


def test_read_returns_the_file_name_and_bytes(tmp_path):
    r, idx, *_ = _index(tmp_path)
    eid = idx.by_rel["lib1/a/b608.step"].id
    assert r.read(eid, 1000) == ("b608.step", STEP)


def _refused(r, eid, status, limit=1000):
    with pytest.raises(FR.FileRootError) as e:
        r.read(eid, limit)
    assert e.value.status == status, e.value


def test_read_refuses_ids_the_index_does_not_hold(tmp_path):
    r, idx, *_ = _index(tmp_path)
    for bad in ("", "0" * 16, "../../etc/passwd", "ZZZZZZZZZZZZZZZZ", idx.by_rel["top.step"].id + "0", "top.step"):
        _refused(r, bad, 404)


def test_read_refuses_a_file_replaced_by_a_link_after_indexing(tmp_path):
    r, idx, root, out = _index(tmp_path)
    eid = idx.by_rel["top.step"].id
    os.remove(os.path.join(root, "top.step"))
    os.symlink(os.path.join(out, "secret.step"), os.path.join(root, "top.step"))
    _refused(r, eid, 409)
    eid = idx.by_rel["lib1/a/plain_part.step"].id               # a link to a file inside the root is refused too
    os.remove(os.path.join(root, "lib1", "a", "plain_part.step"))
    os.symlink(os.path.join(root, "top.step"), os.path.join(root, "lib1", "a", "plain_part.step"))
    _refused(r, eid, 409)


def test_read_refuses_a_folder_swapped_for_a_link_out_of_the_root(tmp_path):
    r, idx, root, out = _index(tmp_path)
    eid = idx.by_rel["lib1/c/deep/d.stp"].id
    _w(os.path.join(out, "deep", "d.stp"))
    os.rename(os.path.join(root, "lib1", "c"), os.path.join(root, "gone"))
    os.symlink(out, os.path.join(root, "lib1", "c"))
    _refused(r, eid, 409)


def test_read_refuses_a_missing_file_a_folder_and_a_fifo(tmp_path):
    r, idx, root, out = _index(tmp_path)
    eid = idx.by_rel["top.step"].id
    os.remove(os.path.join(root, "top.step"))
    _refused(r, eid, 409)
    os.mkdir(os.path.join(root, "top.step"))
    _refused(r, eid, 409)
    os.rmdir(os.path.join(root, "top.step"))
    os.mkfifo(os.path.join(root, "top.step"))
    _refused(r, eid, 409)


def test_read_refuses_a_file_that_grew_over_the_limit_after_indexing(tmp_path):
    r, idx, root, out = _index(tmp_path)
    eid = idx.by_rel["top.step"].id
    assert idx.by_rel["top.step"].entry.size == len(STEP)
    _w(os.path.join(root, "top.step"), STEP + b"x" * 2000)
    _refused(r, eid, 413, limit=1000)
    assert r.read(eid, 5000)[1].endswith(b"x" * 2000)


def test_a_manifest_that_is_a_link_out_of_the_root_is_ignored(tmp_path):
    root, out = make_root(tmp_path)
    os.remove(os.path.join(root, "manifest.csv"))
    _w(os.path.join(out, "manifest.csv"), MANIFEST.encode())
    os.symlink(os.path.join(out, "manifest.csv"), os.path.join(root, "manifest.csv"))
    idx = FR.FileRoot("lib", root).index()
    assert idx.by_rel["lib1/a/b608.step"].licence is None


def test_rescan_sees_new_files_and_keeps_ids(tmp_path):
    r, idx, root, out = _index(tmp_path)
    eid = idx.by_rel["top.step"].id
    _w(os.path.join(root, "new", "n.step"))
    assert r.index() is idx and "new/n.step" not in idx.by_rel         # built once, until a rescan
    again = r.rescan()
    assert "new/n.step" in again.by_rel and again.by_rel["top.step"].id == eid and "new" in again.folders


def test_root_specs(tmp_path):
    assert FR.parse_spec(f"lib={tmp_path}") == ("lib", os.path.realpath(tmp_path))
    link = tmp_path / "ln"
    os.symlink(str(tmp_path), str(link))
    assert FR.parse_spec(f"a-1={link}")[1] == os.path.realpath(tmp_path)          # kept as its realpath
    for bad in ("", "lib", "=x", f"Lib={tmp_path}", f"a_b={tmp_path}", f"{'a' * 33}={tmp_path}", f"lib=\n{tmp_path}",
                f"lib={tmp_path}/missing", "lib=", f"lib\n={tmp_path}", f"a b={tmp_path}"):
        with pytest.raises(ValueError):
            FR.parse_spec(bad)
    f = tmp_path / "file"
    f.write_text("x")
    with pytest.raises(ValueError):
        FR.parse_spec(f"lib={f}")


def test_file_roots_loads_no_qt():
    code = ("import sys\nimport freeshape.file_roots\n"
            "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('PySide6', 'PyQt5', 'PyQt6', 'shiboken6'))\n"
            "print('QT', bad)\nassert not bad")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
