"""I6.5: every feature type the types route reports as all-standard can be created over HTTP.

One parametrised case per type. Each case stands up its own server on a temp copy of the bracket Document, strips it
to an empty Part Studio, builds the fixture the type needs from code in this file (a box, two boxes, a pin, a sheet
metal plate ...), opens a create dialog with POST .../features/dialog, fills the fields from picks and values over
POST .../dialog/<id>/update, accepts, and asserts a named effect (volume, face count, part count, a new plane or axis).

The table is CASES, beside the test. A type with no row, or a row with no type, fails
test_the_table_covers_exactly_the_types_the_types_route_calls_standard, so a new feature type fails the suite until it
is added here. A type that cannot be driven over HTTP goes in UNDRIVEN with the reason; its case then runs as a strict
xfail, so the day it can be driven the suite says so. Each case builds its own server, so each passes alone by node id."""
import http.client
import json
import math
import os
import shutil
import threading

import pytest

from freeshape import doc_registry as R
from freeshape import kernel as K
from freeshape import serve as SV
from freeshape.engine import process as P
from freeshape.engine import wire
from freeshape.sketch import Sketch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRACKET = os.path.join(ROOT, "examples", "documents", "bracket")
CLIENT = "client-cover-1"
TOP = {"kind": "std", "name": "Top"}
FRONT = {"kind": "std", "name": "Front"}
Z = (0.0, 0.0, 1.0)
PIN_AREA = math.pi * 3 ** 2          # the pin of the pattern fixtures: radius 3


# ---------------------------------------------------------------------- a server and a studio to drive it


class Studio:
    """One server on a temp copy of the bracket, cut back to an empty Part Studio, and the calls a case makes."""

    def __init__(self, tmp_path):
        self.root = str(tmp_path / "Documents")
        os.makedirs(self.root)
        registry = R.DocRegistry(str(tmp_path / "data" / R.REGISTRY_NAME))
        self.server = SV.make_server(None, 0, registry=registry, root=self.root)
        self.port = self.server.port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        assert self.server.wait_started(30)
        path = os.path.join(self.root, "Doc")
        shutil.copytree(BRACKET, path)
        with open(os.path.join(path, "document.json")) as fh:
            self.doc = json.load(fh)["id"]
        registry.register_created(self.doc, path)
        self.call("POST", f"/documents/{self.doc}/open")
        self.edit({"op": "delete_features", "ids": [f"F{n}" for n in range(1, 9)]})
        assert not self.outputs().features

    def close(self):
        try:
            SV.close_all(self.server, None)
        finally:
            P.SPARES.clear()

    # -- http
    def request(self, method, path, body=None):
        h = {"Host": f"127.0.0.1:{self.port}", "X-Client-Id": CLIENT}
        data = None
        if method == "POST":
            h["Content-Type"] = "application/json"
            data = json.dumps(body if body is not None else {}).encode()
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
        try:
            c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for k, v in h.items():
                c.putheader(k, v)
            if data is not None:
                c.putheader("Content-Length", str(len(data)))
            c.endheaders(data)
            r = c.getresponse()
            payload = r.read()
            if r.getheader("Content-Type", "").startswith("application/json"):
                payload = json.loads(payload)
            return r.status, payload
        finally:
            c.close()

    def call(self, method, path, body=None, expect=200):
        status, payload = self.request(method, path, body)
        assert status == expect, (method, path, status, payload)
        return payload

    def tab(self, tail):
        return f"/documents/{self.doc}/tabs/T1/{tail}"

    def edit(self, edit):
        return self.call("POST", self.tab("edit"), edit)

    def outputs(self):
        status, raw = self.request("GET", self.tab("outputs"))
        assert status == 200, raw
        return wire.from_wire(raw)

    def types(self):
        return self.call("GET", self.tab("features/types"))["types"]

    # -- the model
    def feature(self, fid):
        return next(f for f in self.outputs().features if f.id == fid)

    def shape(self, part):
        if isinstance(part, str):
            part = next(p for p in self.outputs().parts if p.id == part)
        return K.shape_from_brep(part.brep)

    def volume(self, part):
        return K.volume(self.shape(part))

    def total(self, out=None):
        return sum(self.volume(p) for p in (out or self.outputs()).parts)

    def n_faces(self, part):
        return len(K.faces(self.shape(part)))

    def face(self, part, normal, nth=0):
        """The 1-based index of the nth planar face of `part` that looks along `normal`."""
        hits = []
        for i, f in enumerate(K.faces(self.shape(part)), 1):
            info = K.face_info(f)
            if info.get("n") is not None and sum(a * b for a, b in zip(info["n"], normal)) > 0.999:
                hits.append(i)
        return hits[nth]

    def faces_of(self, part, kind):
        """1-based indices of the faces of `part` of a geometric type: "cylinder" or "plane"."""
        want = {"cylinder": int(K.GeomAbs_Cylinder), "plane": int(K.GeomAbs_Plane)}[kind]
        return [i for i, f in enumerate(K.faces(self.shape(part)), 1) if K.face_info(f)["type"] == want]

    def edges_where(self, part, direction, at=None):
        """1-based indices of the straight edges of `part` along `direction` whose centre satisfies `at(c)`."""
        out = []
        for i, e in enumerate(K.edges(self.shape(part)), 1):
            info = K.edge_info(e)
            if "d" in info and abs(sum(a * b for a, b in zip(info["d"], direction))) > 0.999 \
                    and (at is None or at(info["c"])):
                out.append(i)
        return out

    def bbox(self, part):
        lo, hi = K.bbox(self.shape(part))
        return [round(float(v), 4) for v in (*lo, *hi)]

    # -- building fixtures
    def sketch(self, fid, build, plane=TOP):
        s = Sketch()
        self.made = build(s)                 # whatever the builder returns (entity ids) stays in `made`
        s.solve()
        f = {"id": fid, "type": "sketch", "name": fid, "suppressed": False,
             "params": {"plane": plane, "sketch": s.to_dict()}}
        self.edit({"op": "add_feature", "feature": f})
        assert self.feature(fid).error is None
        return fid

    def rect(self, fid, x0, y0, w, h, plane=TOP):
        def build(s):
            p = [s.add_point(x0, y0), s.add_point(x0 + w, y0), s.add_point(x0 + w, y0 + h), s.add_point(x0, y0 + h)]
            for a, b in zip(p, p[1:] + p[:1]):
                s.add_line(a, b)
        return self.sketch(fid, build, plane)

    def circle(self, fid, x, y, r, plane=TOP):
        return self.sketch(fid, lambda s: s.add_circle(s.add_point(x, y), r), plane)

    def box(self, x0=-30, y0=-20, w=60, h=40, depth=20, fid="S1", **values):
        """A part: a w x h plate on the Top plane extruded `depth` up. Returns the part id."""
        self.rect(fid, x0, y0, w, h)
        self.drive("extrude", [region(fid)], dict(depth=depth, **values))
        return self.outputs().parts[-1].id

    def pin(self, fid="S2", depth=40, x=10, y=0):
        """A pin of radius 3 on the Top plane, extruded and added to the part. Returns the feature id."""
        self.circle(fid, x, y, 3)
        return self.drive("extrude", [region(fid)], {"depth": depth, "op": "add"})

    # -- the dialog
    def item(self, pick, want):
        """What the refs route makes of `pick` for a field that takes `want`: the {"kind", "ref"} a field is set with."""
        got = self.call("POST", self.tab("refs"), {"picks": [dict(pick, want=want)]})["items"][0]
        assert got["ok"], got
        return {"kind": got["kind"], "ref": got["ref"]}

    def start(self, ftype, picks=None):
        body = {"type": ftype} if picks is None else {"type": ftype, "picks": picks}
        return self.call("POST", self.tab("features/dialog"), body, expect=201)

    def drive(self, ftype, picks=None, values=None, fields=None):
        """Create a `ftype` feature the way the client does: start with `picks`, set `values` and `fields` (selection
        rows: key -> [{"kind", "ref"}]) in one update, accept. Returns the id of the new feature."""
        before = [f.id for f in self.outputs().features]
        got = self.start(ftype, picks)
        assert got["feature"] is None and got["type"] == ftype
        body = {**(values or {}), **(fields or {})}
        if body:
            upd = self.call("POST", self.tab(f"dialog/{got['dialog']}/update"), {"values": body})
            assert not upd["errors"], upd["errors"]
        self.call("POST", self.tab(f"dialog/{got['dialog']}/accept"))
        new = [f for f in self.outputs().features if f.id not in before]
        assert len(new) == 1 and new[0].type == ftype and new[0].error is None, [(f.id, f.type, f.error) for f in new]
        return new[0].id


def region(sketch_id, n=1):
    return {"part": sketch_id, "kind": "region", "index": n}


def pick(part, kind, index):
    return {"part": part, "kind": kind, "index": index}


def part_item(pid):
    return {"kind": "part", "ref": pid}


def feature_item(fid):
    return {"kind": "feature", "ref": fid}


def near(a, b, rel=1e-3):
    return a == pytest.approx(b, rel=rel)


# ---------------------------------------------------------------------- the cases: one per all-standard type
# Each takes a Studio and returns nothing; it builds its fixture, drives the type and asserts the effect.

BOX = 60 * 40 * 20


def case_axis(s):
    pid = s.box()
    n = len(s.outputs().axes)
    fid = s.drive("axis", [pick(pid, "edge", s.edges_where(pid, Z)[0])])
    out = s.outputs()
    assert len(out.axes) == n + 1 and fid in out.axes
    assert abs(out.axes[fid]["d"][2]) > 0.999                       # it runs along the edge it was made from


def case_plane(s):
    pid = s.box()
    fid = s.drive("plane", [pick(pid, "face", s.face(pid, Z))], {"offset": 15})
    out = s.outputs()
    assert fid in out.planes and len(out.planes) == 1
    assert out.planes[fid]["o"][2] == pytest.approx(35) and abs(out.planes[fid]["n"][2]) > 0.999   # 15 above the top face


def case_extrude(s):
    s.rect("S1", 0, 0, 30, 20)
    s.drive("extrude", [region("S1")], {"depth": 12})
    (p,) = s.outputs().parts
    assert near(s.volume(p), 30 * 20 * 12) and s.n_faces(p) == 6


def case_revolve(s):
    s.rect("S1", 10, 0, 10, 30, FRONT)               # x 10..20, up 30; revolved about the world Z axis
    s.drive("revolve", [region("S1")], {"axis_std": "Z"})
    (p,) = s.outputs().parts
    assert near(s.volume(p), math.pi * (20 ** 2 - 10 ** 2) * 30) and s.n_faces(p) == 4


def case_loft(s):
    s.rect("S1", 0, 0, 20, 20)
    s.drive("plane", None, {"offset": 30})           # no picks: 30 above the Top plane
    plane = s.outputs().planes
    (pf,) = plane
    s.rect("S2", 0, 0, 10, 10, {"kind": "plane", "fid": pf})
    s.drive("loft", [region("S1"), region("S2")])
    (p,) = s.outputs().parts
    assert near(s.volume(p), 30 / 3 * (400 + 100 + 200)) and s.n_faces(p) == 6      # a frustum of a pyramid


def case_sweep(s):
    s.circle("S1", 0, 0, 5)
    s.sketch("S2", lambda b: b.add_line(b.add_point(0, 0), b.add_point(0, 50)), FRONT)    # straight up the Z axis
    line = {"part": "S2", "kind": "sketch_line", "entity": s.made}
    s.drive("sweep", [region("S1")], None, {"path": [s.item(line, ["path"])]})
    (p,) = s.outputs().parts
    assert near(s.volume(p), math.pi * 25 * 50)


def case_fillet(s):
    pid = s.box()
    edges = s.edges_where(pid, (1, 0, 0), lambda c: c[2] > 19)           # the two top edges along X
    s.drive("fillet", [pick(pid, "edge", e) for e in edges], {"radius": 3})
    (p,) = s.outputs().parts
    assert s.n_faces(p) == 6 + 2 and s.volume(p) < BOX
    assert near(s.volume(p), BOX - 2 * 60 * (9 - math.pi * 9 / 4))


def case_chamfer(s):
    pid = s.box()
    edges = s.edges_where(pid, (1, 0, 0), lambda c: c[2] > 19)
    s.drive("chamfer", [pick(pid, "edge", e) for e in edges], {"distance": 2})
    (p,) = s.outputs().parts
    assert s.n_faces(p) == 6 + 2 and near(s.volume(p), BOX - 2 * 60 * 2)


def case_shell(s):
    pid = s.box()
    s.drive("shell", [pick(pid, "face", s.face(pid, Z))], {"thickness": 2})
    (p,) = s.outputs().parts
    assert near(s.volume(p), BOX - 56 * 36 * 18)


def case_canvas(s):
    from test_canvas_feature import png
    data = png(200, 100)
    h = {"Host": f"127.0.0.1:{s.port}", "X-Client-Id": CLIENT, "Content-Type": "application/octet-stream",
         "Content-Length": str(len(data))}
    c = http.client.HTTPConnection("127.0.0.1", s.port, timeout=120)
    try:
        c.request("POST", s.tab("images?name=plan.png"), body=data, headers=h)
        r = c.getresponse()
        up = json.loads(r.read())
        assert r.status == 200, up
    finally:
        c.close()
    got = s.call("POST", s.tab("features/dialog"),
                 {"type": "canvas", "params": {"blob": up["blob"], "filename": up["filename"]}}, expect=201)
    s.call("POST", s.tab(f"dialog/{got['dialog']}/update"), {"values": {"width": 100}})
    s.call("POST", s.tab(f"dialog/{got['dialog']}/accept"))
    (placed,) = s.outputs().canvases
    assert near(placed.width, 100) and near(placed.height, 50)      # 200 x 100 pixels, the height follows the width
    assert placed.blob == up["blob"] and not s.outputs().parts


def case_derived(s):
    pid = s.box()                                            # the source: this box in T1
    use = s.call("POST", f"/documents/{s.doc}/tabs", {"kind": "studio", "name": "Use"}, expect=201)["tab"]["id"]
    at = f"/documents/{s.doc}/tabs/{use}"
    got = s.call("POST", f"{at}/features/dialog", {"type": "derived"}, expect=201)
    for values in ({"source": "T1"}, {"parts": [pid]}):     # the parts list is the chosen source's
        assert not s.call("POST", f"{at}/dialog/{got['dialog']}/update", {"values": values})["errors"]
    s.call("POST", f"{at}/dialog/{got['dialog']}/accept")
    status, raw = s.request("GET", f"{at}/outputs")
    assert status == 200
    (p,) = wire.from_wire(raw).parts
    assert near(s.volume(p), BOX)                            # the box, brought into the other Part Studio


def case_draft(s):
    pid = s.box()
    side = s.face(pid, (1, 0, 0))
    bottom = s.item(pick(pid, "face", s.face(pid, (0, 0, -1))), ["plane"])
    s.drive("draft", [pick(pid, "face", side)], {"angle": 5}, {"neutral_sel": [bottom]})
    (p,) = s.outputs().parts
    assert near(s.volume(p), BOX + 0.5 * 20 ** 2 * math.tan(math.radians(5)) * 40)     # the side leans out by 5 degrees


def case_delete_face(s):
    pid = s.box()
    edges = s.edges_where(pid, (1, 0, 0), lambda c: c[2] > 19 and c[1] < 0)
    s.drive("fillet", [pick(pid, "edge", edges[0])], {"radius": 3})
    p = s.outputs().parts[0]
    assert s.n_faces(p) == 7
    round_ = s.faces_of(p, "cylinder")[0]
    s.drive("delete_face", [pick(p.id, "face", round_)])
    (p,) = s.outputs().parts
    assert s.n_faces(p) == 6 and near(s.volume(p), BOX)                 # healed back to the box


def case_move_face(s):
    pid = s.box()
    s.drive("move_face", [pick(pid, "face", s.face(pid, Z))], {"distance": 5})
    (p,) = s.outputs().parts
    assert near(s.volume(p), 60 * 40 * 25)


def case_offset_surface(s):
    pid = s.box()
    s.drive("offset_surface", [pick(pid, "face", s.face(pid, Z))], {"distance": 4})
    out = s.outputs()
    assert len(out.parts) == 2 and any(s.volume(p) == 0 for p in out.parts)       # a surface body beside the box


def case_boolean(s):
    a = s.box(fid="S1")
    s.rect("S2", 10, -10, 40, 30)
    s.drive("extrude", [region("S2")], {"depth": 10, "op": "new"})
    a, b = [p.id for p in s.outputs().parts]
    s.drive("boolean", [{"part": a, "kind": "part"}, {"part": b, "kind": "part"}], {"op": "union"})
    out = s.outputs()
    assert len(out.parts) == 1 and near(s.volume(out.parts[0]), BOX + 40 * 30 * 10 - 20 * 30 * 10)   # 2 parts became 1


def case_delete_part(s):
    s.box()
    s.rect("S2", 100, 0, 10, 10)
    s.drive("extrude", [region("S2")], {"depth": 5, "op": "new"})
    a, b = [p.id for p in s.outputs().parts]
    s.drive("delete_part", [{"part": b, "kind": "part"}])
    (p,) = s.outputs().parts
    assert p.id == a and near(s.volume(p), BOX)


def case_move(s):
    pid = s.box()
    s.drive("move", [{"part": pid, "kind": "part"}], {"dx": 25})
    (p,) = s.outputs().parts
    assert s.bbox(p)[0] == pytest.approx(-5) and s.bbox(p)[3] == pytest.approx(55) and near(s.volume(p), BOX)


def case_linear_pattern(s):
    s.box()
    pin = s.pin()
    base = s.total()
    s.drive("linear_pattern", None, {"type": "features", "count": 3, "spacing": 8, "dir_std": "X"}, {"features": [feature_item(pin)]})
    (p,) = s.outputs().parts
    assert near(s.volume(p), base + 2 * PIN_AREA * 20)


def case_circular_pattern(s):
    s.box()
    pin = s.pin()
    base = s.total()
    s.drive("circular_pattern", None, {"type": "features", "count": 4, "axis_std": "Z"}, {"features": [feature_item(pin)]})
    (p,) = s.outputs().parts
    assert near(s.volume(p), base + 3 * PIN_AREA * 20)


def case_mirror(s):
    s.box()
    pin = s.pin()
    mid = s.drive("plane", None, {"offset": 40})                 # the plane at the pin's top, 40 above the Top plane
    s.drive("mirror", None, {"type": "features"}, {"features": [feature_item(pin)],
                                   "plane_sel": [{"kind": "plane", "ref": {"kind": "plane", "fid": mid}}]})
    (p,) = s.outputs().parts
    assert near(s.volume(p), BOX + PIN_AREA * 40) and s.bbox(p)[5] == pytest.approx(60)    # a second pin on top of the first


def case_rib(s):
    pid = s.box()
    s.drive("shell", [pick(pid, "face", s.face(pid, Z))], {"thickness": 2})
    hollow = s.total()
    s.sketch("S2", lambda b: b.add_line(b.add_point(-25, 10), b.add_point(25, 10)), FRONT)   # across the cavity, 10 up
    line = {"part": "S2", "kind": "sketch_line", "entity": s.made}
    s.drive("rib", None, {"thickness": 2}, {"path": [s.item(line, ["path"])], "parts": [part_item(pid)]})
    assert near(s.total(), hollow + 2 * 8 * 56)                      # a 2 thick wall across the cavity, 8 high, 56 long


def case_enclose(s):
    pid = s.box()
    s.drive("enclose", [pick(pid, "face", i) for i in range(1, 7)])          # the six faces bound a volume
    parts = s.outputs().parts
    assert len(parts) == 2 and near(s.volume(parts[1]), BOX)                  # a new part beside the box


def case_thicken(s):
    pid = s.box()
    s.drive("thicken", [pick(pid, "face", s.face(pid, Z))], {"thickness1": 3, "op": "new"})
    parts = s.outputs().parts
    assert len(parts) == 2 and near(s.volume(parts[1]), 60 * 40 * 3)          # a new 3 thick slab on the face


def case_thread(s):
    s.circle("S1", 0, 0, 5)
    s.drive("extrude", [region("S1")], {"depth": 20})
    pid = s.outputs().parts[0].id
    before = s.volume(pid)
    s.drive("thread", [pick(pid, "face", s.faces_of(pid, "cylinder")[0])])
    (p,) = s.outputs().parts
    assert s.volume(p) < before and s.n_faces(p) > 6                          # the thread cuts into the cylinder


def case_variable(s):
    s.drive("variable", None, {"name": "width", "expr": "12 mm"})
    assert s.outputs().variables["width"] == pytest.approx(12)


def case_sm_base(s):
    s.rect("S1", -30, -20, 60, 40)
    s.drive("sm_base", [region("S1")], {"thickness": 2})
    (p,) = s.outputs().parts
    assert near(s.volume(p), 60 * 40 * 2)                                     # a 2 thick plate


def sm_plate(s):
    s.rect("S1", -30, -20, 60, 40)
    s.drive("sm_base", [region("S1")], {"thickness": 2})
    return s.outputs().parts[0].id


def case_sm_convert(s):
    s.rect("S1", -30, -20, 60, 40)
    s.drive("extrude", [region("S1")], {"depth": 2})
    pid = s.outputs().parts[0].id
    s.drive("sm_convert", [pick(pid, "face", s.face(pid, Z))])
    assert near(s.total(), 60 * 40 * 2) and s.outputs().features[-1].type == "sm_convert"


def case_sm_flange(s):
    pid = sm_plate(s)
    e = s.edges_where(pid, (1, 0, 0), lambda c: c[1] > 19)
    s.drive("sm_flange", [pick(pid, "edge", e[0])], {"length": 10})
    assert 4800 + 60 * 10 * 2 < s.total() < 4800 + 60 * 10 * 2 + 100          # a flange 10 long, 60 wide, 2 thick, and its bend


def case_sm_hem(s):
    pid = sm_plate(s)
    e = s.edges_where(pid, (1, 0, 0), lambda c: c[1] > 19)
    s.drive("sm_hem", [pick(pid, "edge", e[0])])
    assert s.total() > 4800 + 60 * 2 * 2                                       # the edge folds back on itself


def case_sm_jog(s):
    pid = sm_plate(s)
    e = s.edges_where(pid, (1, 0, 0), lambda c: c[1] > 19)
    s.drive("sm_jog", [pick(pid, "edge", e[0])])
    assert s.total() > 4800 + 60 * 2 * 2                                       # the jog adds a step and a run


def case_sm_tab(s):
    sm_plate(s)
    s.rect("S2", 30, -5, 15, 10)
    s.drive("sm_tab", [region("S2")])
    assert near(s.total(), 4800 + 15 * 10 * 2)                                # a 15 x 10 tab of 2 thick sheet


CASES = {name[len("case_"):]: fn for name, fn in sorted(globals().items()) if name.startswith("case_")}

# A type whose case cannot pass because the server cannot drive it: {type: why}. Its case still runs, as a strict
# xfail with this reason, so the suite says so the day it works. Empty: all 30 types are driven.
UNDRIVEN = {}


# ---------------------------------------------------------------------- the tests


@pytest.fixture
def studio(tmp_path):
    s = Studio(tmp_path)
    try:
        yield s
    finally:
        s.close()


def test_the_table_covers_exactly_the_types_the_types_route_calls_standard(studio):
    standard = {t["type"] for t in studio.types() if t["standard"]}
    assert standard, "the types route lists no standard type"
    assert standard - set(CASES) == set(), "standard types with no case (add a case_<type> function)"
    assert set(CASES) - standard == set(), "cases for types the types route does not call standard (drop them)"
    assert set(UNDRIVEN) <= set(CASES)


@pytest.mark.parametrize("ftype", [
    pytest.param(t, marks=pytest.mark.xfail(strict=True, reason=UNDRIVEN[t])) if t in UNDRIVEN else t
    for t in sorted(CASES)])
def test_every_standard_type_can_be_created_over_http(studio, ftype):
    CASES[ftype](studio)
