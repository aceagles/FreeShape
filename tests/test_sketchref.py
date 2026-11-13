import subprocess
import sys

import pytest

from freeshape import kernel as K
from freeshape import sketchref as S

XY = {"origin": [0, 0, 0], "xdir": [1, 0, 0], "normal": [0, 0, 1]}
YZ5 = {"origin": [0, 0, 5], "xdir": [0, 1, 0], "normal": [1, 0, 0]}


def sk():
    return {
        "points": {"origin": [0, 0], "p1": [10, 0], "p2": [10, 20], "p3": [3, 4], "pc": [1, 1],
                   "pa": [5, 0], "pb": [0, 5]},
        "entities": {
            "l1": {"type": "line", "p1": "p1", "p2": "p2", "construction": True},
            "pt": {"type": "point", "p": "p3"},
            "c1": {"type": "circle", "c": "pc", "r": 2.5},
            "a1": {"type": "arc", "c": "origin", "p1": "pa", "p2": "pb"},
        },
    }


def close(a, b):
    assert all(abs(x - y) < 1e-9 for x, y in zip(a, b)), (a, b)


def test_line_mid_xy():
    f = S.frame(XY, sk(), "l1", "mid")
    close(f["o"], (10, 10, 0)); close(f["z"], (0, 0, 1)); close(f["x"], (0, 1, 0))


def test_line_ends_and_roles():
    assert S.roles(sk(), "l1") == ["end1", "end2", "mid"]
    close(S.frame(XY, sk(), "l1", "end2")["o"], (10, 20, 0))


def test_rotated_plane():
    f = S.frame(YZ5, sk(), "l1", "mid")
    close(f["z"], (1, 0, 0))
    # local (10,10): u along xdir=(0,1,0), v along normal x xdir = (0,0,1)
    close(f["o"], (0, 10, 15)); close(f["x"], (0, 0, 1))


def test_point_origin_circle_arc():
    s = sk()
    for eid in ("pt", "p3", "origin"):
        assert S.roles(s, eid) == ["point"]
        f = S.frame(YZ5, s, eid, "point")
        close(f["x"], (0, 1, 0)); close(f["z"], (1, 0, 0))
    close(S.frame(XY, s, "origin", "point")["o"], (0, 0, 0))
    assert S.roles(s, "c1") == ["centre"]
    close(S.frame(XY, s, "c1", "centre")["o"], (1, 1, 0))
    assert S.roles(s, "a1") == ["centre", "end1", "end2"]
    close(S.frame(XY, s, "a1", "end2")["o"], (0, 5, 0))
    close(S.frame(XY, s, "a1", "end2")["x"], (1, 0, 0))


def test_describe():
    d = S.describe(sk(), "l1", "mid")
    assert d == {"etype": "line", "sig": {"at": [10.0, 10.0], "len": 20.0}}
    assert S.describe(sk(), "c1", "centre")["sig"]["r"] == 2.5
    assert S.describe(sk(), "a1", "end1")["sig"]["r"] == 5.0
    assert S.describe(sk(), "p3", "point")["etype"] == "point"


def test_check():
    s = sk()
    ref = {"entity": "l1", "etype": "line", **S.describe(s, "l1", "mid")}
    S.check(s, ref)
    s["points"]["p2"] = [15, 30]
    S.check(s, ref)  # moved: fine
    del s["entities"]["l1"]
    with pytest.raises(K.KernelError, match="l1"):
        S.check(s, ref)
    s["entities"]["l1"] = {"type": "circle", "c": "pc", "r": 1}
    with pytest.raises(K.KernelError, match="l1"):
        S.check(s, ref)


def test_bad_role_and_missing():
    with pytest.raises(K.KernelError):
        S.frame(XY, sk(), "l1", "centre")
    with pytest.raises(K.KernelError, match="zz"):
        S.roles(sk(), "zz")


def test_pure_imports():
    code = ("import sys, freeshape.sketchref;"
            "bad=[m for m in sys.modules if m.startswith(('PySide','PyQt','freeshape.assembly'))];"
            "print(bad); sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
