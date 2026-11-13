"""Content-addressed sidecar store: round trips, BREP stability, and refusal of hostile folders."""
import builtins
import hashlib
import os

import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.TopExp import TopExp_Explorer

from freeshape import kernel as K
from freeshape.blobstore import (BlobError, BlobStore, brep_bytes, dump_json, load_json, shape_from_brep,
                                 store_for)


def _box():
    return BRepPrimAPI_MakeBox(10, 20, 30).Shape()


def _filleted():
    box = _box()
    return K.fillet(box, [K.edges(box)[0]], 2.0)



def _iter(s, name):
    from OCP import TopAbs
    ex = TopExp_Explorer(s, getattr(TopAbs.TopAbs_ShapeEnum, "TopAbs_" + name))
    while ex.More():
        yield ex.Current()
        ex.Next()


def _volume(s):
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    p = GProp_GProps()
    BRepGProp.VolumeProperties_s(s, p)
    return p.Mass()


@pytest.fixture
def store(tmp_path):
    return BlobStore(str(tmp_path / "blobs"))


def test_round_trip_and_idempotent(store):
    k = store.put(b"hello")
    assert k == hashlib.sha256(b"hello").hexdigest()
    assert store.put(b"hello") == k
    assert store.get(k) == b"hello"
    assert os.listdir(store.root) == [k]
    assert store.has(k) and store.keys() == [k]
    assert not store.has("0" * 64) and not store.has("../x")


def test_no_tmp_left_and_corrupt_blob_is_replaced(store):
    k = store.put(b"abc")
    with open(os.path.join(store.root, k), "wb") as fh:
        fh.write(b"bad")
    assert store.put(b"abc") == k
    assert store.get(k) == b"abc"
    assert os.listdir(store.root) == [k]


def test_store_for_creates_folder_only_on_put(tmp_path):
    tab = str(tmp_path / "part.fshape")
    s = store_for(tab)
    assert s.root == str(tmp_path / "blobs")
    assert s.keys() == [] and not s.has("0" * 64)
    with pytest.raises(BlobError):
        s.get("0" * 64)
    assert not (tmp_path / "blobs").exists()
    s.put(b"x")
    assert (tmp_path / "blobs").is_dir()


@pytest.mark.parametrize("make", [_box, _filleted], ids=["box", "filleted"])
def test_brep_round_trip(store, make):
    shape = make()
    key = store.put(brep_bytes(shape))
    back = shape_from_brep(store.get(key))
    assert _volume(back) == pytest.approx(_volume(shape), rel=1e-9)
    assert len(list(_iter(back, "FACE"))) == len(list(_iter(shape, "FACE")))


def test_same_model_built_twice_gives_same_key(store):
    assert store.put(brep_bytes(_filleted())) == store.put(brep_bytes(_filleted()))
    assert len(store.keys()) == 1


def test_brep_bytes_is_encode_brep_text():
    from freeshape.engine.encode import brep_text
    s = _filleted()
    assert brep_bytes(s) == brep_text(s)


def test_shape_from_brep_rejects_garbage():
    for bad in (b"", b"not a brep", b"\x00\xff" * 50):
        with pytest.raises(BlobError):
            shape_from_brep(bad)


def test_json_is_canonical():
    a = dump_json({"b": 1, "a": {"y": [1, 2], "x": None}})
    assert a == dump_json({"a": {"x": None, "y": [1, 2]}, "b": 1})
    assert load_json(a) == {"a": {"x": None, "y": [1, 2]}, "b": 1}
    with pytest.raises(BlobError):
        load_json(b"{nope")
    with pytest.raises(BlobError):
        load_json(b"\xff\xfe")


class _Opens:
    """Patch builtins.open and os.open; record every path opened."""

    def __init__(self, monkeypatch):
        self.paths = []
        real_open, real_os_open = builtins.open, os.open

        def spy(path, *a, **k):
            self.paths.append(os.fspath(path) if not isinstance(path, int) else path)
            return real_open(path, *a, **k)

        def spy_os(path, *a, **k):
            self.paths.append(os.fspath(path))
            return real_os_open(path, *a, **k)
        monkeypatch.setattr(builtins, "open", spy)
        monkeypatch.setattr(os, "open", spy_os)

    def outside(self, root):
        root = os.path.realpath(root)
        return [p for p in self.paths if isinstance(p, str)
                and not os.path.realpath(p).startswith(root + os.sep)]


@pytest.mark.parametrize("key", ["../etc/passwd", "/etc/passwd", "a" * 63, "A" * 64, "a" * 65, "", "..",
                                 "a" * 63 + "/", ("a" * 62) + "\n\n", None, 5])
def test_bad_keys_refused_without_io(store, monkeypatch, key):
    store.put(b"x")
    spy = _Opens(monkeypatch)
    with pytest.raises(BlobError):
        store.get(key)
    assert spy.paths == []


def test_uppercase_form_of_real_key_refused(store):
    k = store.put(b"x")
    with pytest.raises(BlobError):
        store.get(k.upper())


def test_altered_content_refused(store):
    k = store.put(b"original")
    with open(os.path.join(store.root, k), "wb") as fh:
        fh.write(b"tampered")
    with pytest.raises(BlobError, match="match"):
        store.get(k)


def test_symlinked_blob_file_refused(tmp_path, monkeypatch):
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"secret")
    s = BlobStore(str(tmp_path / "tab" / "blobs"))
    s.put(b"x")
    key = hashlib.sha256(b"secret").hexdigest()  # even a correct hash must not be followed
    os.symlink(secret, os.path.join(s.root, key))
    spy = _Opens(monkeypatch)
    with pytest.raises(BlobError):
        s.get(key)
    assert not s.has(key) and key not in s.keys()
    assert spy.outside(s.root) == []
    assert spy.paths == []


def test_symlinked_blobs_folder_refused(tmp_path, monkeypatch):
    tab = tmp_path / "tab"
    tab.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    key = hashlib.sha256(b"payload").hexdigest()
    (elsewhere / key).write_bytes(b"payload")
    os.symlink(elsewhere, tab / "blobs")
    s = store_for(str(tab / "part.fshape"))
    spy = _Opens(monkeypatch)
    with pytest.raises(BlobError):
        s.get(key)
    assert spy.paths == []
    assert not s.has(key) and s.keys() == []
    with pytest.raises(BlobError):
        s.put(b"new")
    assert os.listdir(elsewhere) == [key]


def test_root_resolving_outside_tab_folder_refused(tmp_path):
    real = tmp_path / "real"
    (real / "blobs").mkdir(parents=True)
    key = hashlib.sha256(b"p").hexdigest()
    (real / "blobs" / key).write_bytes(b"p")
    os.symlink(real, tmp_path / "linked_tab")  # tab folder itself a link is fine: blobs/ is a real child
    assert BlobStore(str(tmp_path / "linked_tab" / "blobs")).get(key) == b"p"
    # a root whose own name is a link out of the folder is not
    os.symlink(real / "blobs", tmp_path / "blobs")
    with pytest.raises(BlobError):
        BlobStore(str(tmp_path / "blobs")).get(key)


def test_oversize_blob_refused(store):
    k = store.put(b"x" * 100)
    assert store.get(k, max_bytes=100) == b"x" * 100
    with pytest.raises(BlobError, match="limit"):
        store.get(k, max_bytes=99)


def test_default_cap_is_64_mib(store, monkeypatch):
    k = store.put(b"x")
    real = os.lstat

    class St:
        st_mode = real(os.path.join(store.root, k)).st_mode
        st_size = 64 * 2 ** 20 + 1
    monkeypatch.setattr(os, "lstat", lambda p, *a, **kw: St if p.endswith(k) else real(p, *a, **kw))
    with pytest.raises(BlobError, match="limit"):
        store.get(k)


def test_directory_named_like_key_refused(store):
    store.put(b"x")
    key = "b" * 64
    os.mkdir(os.path.join(store.root, key))
    with pytest.raises(BlobError):
        store.get(key)
    assert key not in store.keys()


def test_module_is_headless():
    import freeshape.blobstore as m
    assert "PySide6" not in open(m.__file__).read()
