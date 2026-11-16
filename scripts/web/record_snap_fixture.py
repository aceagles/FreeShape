"""Records web/src/sketch/tools/snapFixture.json: pointer paths and what the desktop's inference (freeshape/ui/sketcher.py
Sketcher.snap and the point/constraint creation of _draw_click) makes of them, for the kinds the browser client's snap code
(web/src/sketch/tools/snap.ts) has: coincident with an existing point or the origin, point on a line or circle, and level or
plumb from the line's start. The client's Vitest reads the fixture and must give the same snaps and constraints.

Run in the gate image:  fs_py scripts/web/record_snap_fixture.py

The screen is the one tests/test_sketch_inference.py uses: 10 px per mm, the sketch origin at (400, 300), v up.
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import numpy as np  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402
from freeshape.ui.sketcher import Sketcher  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "..", "..", "web", "src", "sketch", "tools", "snapFixture.json")


class _Cam:
    scale = 1.0

    def to_screen(self, w):
        return (w[0] * 10 + 400, 300 - w[1] * 10, 0)


class _Vp:
    cam = _Cam()

    def ray_plane(self, x, y, plane):
        return ((x - 400) / 10, (300 - y) / 10, 0)


VP = _Vp()


def base():
    """A line through the origin, a circle above it, and a lone point."""
    sk = Sketch()
    a = sk.add_point(-20, 0)
    b = sk.add_point(20, 0)
    sk.add_line(a, b)                       # l..: along the u axis, through the origin
    c = sk.add_point(0, 20)
    sk.add_circle(c, 8)
    sk.add_point(30, 15)
    return sk


def run(tool, steps):
    sk = base()
    before = sk.to_dict()
    s = Sketcher(K.std_plane("Top"), sk, "f1", lambda *a: None)
    s._offer_dims = lambda *a, **k: None
    s.set_tool(tool)
    recorded = []
    for x, y in steps:
        anchor = s.sk.P(s.chain_anchor) if tool == "line" and s.chain_anchor is not None else None
        uv, snap = s.snap(VP, x, y, anchor)
        recorded.append({"x": x, "y": y, "uv": [round(float(uv[0]), 9), round(float(uv[1]), 9)],
                         "snap": None if snap is None else {"kind": snap[0], "id": snap[1]},
                         "infer": getattr(s, "infer", None)})
        s._draw_click(VP, x, y)
    after = s.sk
    # new ids -> the server's "$p<i>" (the point of pick i) and "$e<i>" (entity i made)
    new_points = [p for p in after.points if p not in before["points"]]
    new_entities = [e for e in after.entities if e not in before["entities"]]
    pick_of = {}
    for pid in new_points:
        for i, r in enumerate(recorded):
            if np.allclose(after.points[pid], r["uv"], atol=1e-6):
                pick_of.setdefault(pid, i)
    ref = lambda r: f"$p{pick_of[r]}" if r in pick_of else (f"$e{new_entities.index(r)}" if r in new_entities else r)  # noqa: E731
    cons = []
    for i, r in enumerate(recorded):
        if r["snap"] and r["snap"]["kind"] == "point":
            cons.append({"type": "coincident", "refs": [f"$p{i}", r["snap"]["id"]]})   # the desktop reuses the point
    known = {c["id"] for c in before["constraints"]}
    for c in after.constraints:
        if c["id"] in known:
            continue
        if c["type"] == "point_on" or (tool == "line" and c["type"] in ("horizontal", "vertical") and len(c["refs"]) == 1):
            cons.append({"type": c["type"], "refs": [ref(x) for x in c["refs"]]})
    return {"tool": tool, "steps": recorded, "constraints": cons}


CASES = {
    "line, both ends free": ("line", [(300, 250), (500, 200)]),
    "line starting on the lone point": ("line", [(703, 152), (600, 250)]),
    "line ending on an existing point": ("line", [(300, 100), (204, 297)]),
    "line ending on the origin": ("line", [(300, 100), (402, 298)]),
    "line within 3 degrees of level": ("line", [(350, 250), (600, 253)]),
    "line within 3 degrees of plumb": ("line", [(350, 250), (352, 100)]),
    "line 5 degrees off level": ("line", [(350, 250), (600, 272)]),
    "line ending on a line": ("line", [(300, 200), (430, 303)]),
    "line ending on a circle": ("line", [(600, 200), (482, 100)]),
    "line plumb from its start onto a line": ("line", [(350, 200), (352, 303)]),
    "rectangle between a point and a line": ("rect", [(703, 152), (450, 300)]),
    "rectangle, both corners free": ("rect", [(300, 200), (520, 150)]),
    "centre rectangle from the origin": ("center_rect", [(401, 299), (480, 240)]),
    "circle centred on a line": ("circle", [(350, 301), (400, 380)]),
    "point on the circle": ("point", [(401, 22)]),
    "point on the lone point": ("point", [(701, 151)]),
}

if __name__ == "__main__":
    out = {"screen": {"origin": [400, 300], "pxPerMm": 10}, "base": base().to_dict(), "cases": {k: run(*v) for k, v in CASES.items()}}
    with open(OUT, "w") as f:
        json.dump(out, f, indent=1)
        f.write("\n")
    for name, c in out["cases"].items():
        print(name, [(s["snap"], s["infer"]) for s in c["steps"]], c["constraints"])
