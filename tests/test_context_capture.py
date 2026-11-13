"""capture_context: the neighbours of one instance, as plain data (ADR 0004)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from freeshape import assembly as A
from freeshape import kernel as K
from freeshape import blobstore
from tests.test_assembly import box, cyl, face, make, mate, src  # noqa: F401
import pytest


@pytest.fixture
def lib():
    return {"plate": box(60, 40, 10), "pin": cyl(4, 30)}


def two(lib):
    a = make(lib)
    ip, ipin = a.insert(src("plate")), a.insert(src("pin"))
    top = a.connector(ip, "face", face(a, ip, lambda f: f.get("n") and f["n"][2] > 0.9))
    pb = a.connector(ipin, "face", face(a, ipin, lambda f: f.get("n") and f["n"][2] < -0.9))
    mate(a, "fastened", top, pb)
    return a, ip, ipin


def test_relative_transform_places_neighbour(lib):
    a, ip, ipin = two(lib)
    cap = A.capture_context(a, ip)
    assert cap["assembly"] == a.uid and cap["instance"] == ip and cap["solved"] is True
    assert len(cap["neighbours"]) == 1
    n = cap["neighbours"][0]
    assert n["id"] == ipin and n["name"] == a.instance(ipin)["name"]
    shape = blobstore.shape_from_brep(n["brep"])
    mine = K.located(shape, np.array(n["T"]))
    # world = T(ip) @ relative
    got = K.located(mine, a.T(ip))
    lo1, hi1 = K.bbox(got)
    lo2, hi2 = K.bbox(a.world_shape(ipin))
    assert np.allclose(lo1, lo2, atol=1e-6) and np.allclose(hi1, hi2, atol=1e-6)


def test_names_match_named(lib):
    a, ip, ipin = two(lib)
    from freeshape.naming import NamedShape
    nm = NamedShape(a.parts[ipin].shape, {("F", 1): "Top"})
    a.named_resolver = lambda s: nm if s["part"] == "pin" else None
    n = A.capture_context(a, ip)["neighbours"][0]
    assert n["names"] == a._named(ipin).names == {("F", 1): "Top"}


def test_capture_twice_identical(lib):
    a, ip, _ = two(lib)
    c1, c2 = A.capture_context(a, ip), A.capture_context(a, ip)
    assert c1 == c2
    assert c1["neighbours"][0]["hash"] == c2["neighbours"][0]["hash"]


def test_unsolved_mate_still_captures_poses(lib):
    a, ip, ipin = two(lib)
    a.errors["m1"] = "Can't be solved together with the other mates (shown red)"
    before = np.array(a.instance(ipin)["T"])
    cap = A.capture_context(a, ip)
    assert cap["solved"] is False and len(cap["neighbours"]) == 1
    assert np.allclose(np.array(a.instance(ipin)["T"]), before)
    assert np.allclose(np.array(cap["neighbours"][0]["T"]), np.linalg.inv(a.T(ip)) @ a.T(ipin))
