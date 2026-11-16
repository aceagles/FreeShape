"""I4.3: POST /documents/<id>/tabs/<tab>/refs turns picks into stored references (freeshape/picking.py), through the
engine's `refs` request. The shapes are in the serve.py docstring ("Refs"). Each test builds its own root, registry and
server, so each passes alone."""
import http.client
import json
import os
import shutil
import socket
import threading

import pytest

from conftest import EXAMPLES_DIR
from freeshape import doc_lock
from freeshape import doc_registry as R
from freeshape import features as F
from freeshape import kernel as K
from freeshape import picking as PK
from freeshape import serve as SV
from freeshape.document import Document
from freeshape.engine import InProcessEngine, ProcessEngine
from freeshape.engine import process as P
from freeshape.engine import wire
from freeshape.engine.contract import BadPick, EngineError
from freeshape.sketch import Sketch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HINGE = os.path.join(ROOT, "examples", "documents", "hinge")
BRACKET = os.path.join(ROOT, "examples", "documents", "bracket")
BRACKET_FILE = os.path.join(EXAMPLES_DIR, "bracket.fshape")


class _Root:
    def __init__(self, tmp_path):
        self.root = str(tmp_path / "Documents")
        self.data = str(tmp_path / "data")
        os.makedirs(self.root)
        self.registry = R.DocRegistry(os.path.join(self.data, R.REGISTRY_NAME))
        self.server = SV.make_server(None, 0, registry=self.registry, root=self.root)
        self.port = self.server.port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        assert self.server.wait_started(30)

    def copy(self, source, name):
        path = os.path.join(self.root, name)
        shutil.copytree(source, path)
        with open(os.path.join(path, "document.json")) as fh:
            doc_id = json.load(fh)["id"]
        self.registry.register_created(doc_id, path)
        return doc_id, os.path.realpath(path)

    def request(self, method, path, body=None, headers=None, raw=None):
        h = {"Host": f"127.0.0.1:{self.port}"}
        if method == "POST":
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        data = raw if raw is not None else (json.dumps(body if body is not None else {}).encode()
                                            if method == "POST" else None)
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=90)
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

    def ok(self, method, path, body=None, expect=200):
        status, payload = self.request(method, path, body)
        assert status == expect, (method, path, status, payload)
        return payload

    def close(self):
        try:
            SV.close_all(self.server, None)
        finally:
            P.SPARES.clear()


@pytest.fixture
def root(tmp_path):
    made = []

    def make():
        r = _Root(tmp_path)
        made.append(r)
        return r
    try:
        yield make
    finally:
        for r in made:
            r.close()


def _open(root, source=BRACKET, name="Doc"):
    r = root()
    doc_id, folder = r.copy(source, name)
    r.ok("POST", f"/documents/{doc_id}/open")
    return r, doc_id, folder


def _outputs(r, doc_id, tab="T1"):
    return wire.from_wire(r.request("GET", f"/documents/{doc_id}/tabs/{tab}/outputs")[1])


def _refs(r, doc_id, picks, tab="T1", expect=200):
    status, payload = r.request("POST", f"/documents/{doc_id}/tabs/{tab}/refs", {"picks": picks})
    assert status == expect, (status, payload)
    return payload


def _pick(part, kind, index=None, want=("face",), **kw):
    p = {"part": part, "kind": kind, "want": list(want), **kw}
    if index is not None:
        p["index"] = index
    return p


def _same(a, b, path="item"):
    """Equal as plain data, floats to 1e-9."""
    if isinstance(a, dict):
        assert isinstance(b, dict) and a.keys() == b.keys(), path
        for k in a:
            _same(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, (list, tuple)):
        assert len(a) == len(b), path
        for i, (x, y) in enumerate(zip(a, b)):
            _same(x, y, f"{path}[{i}]")
    elif isinstance(a, float):
        assert a == pytest.approx(b, abs=1e-9, rel=1e-9), path
    else:
        assert a == b, path


def _local_state():
    """The bracket built in this process: the answer key for the route."""
    eng = InProcessEngine()
    tab = eng.open_tab(BRACKET_FILE)
    eng.outputs(tab)
    return eng, eng._get(tab).doc.state


def _add_sketch(r, doc_id):
    """Add a sketch to the bracket through an edit: a plate with a hole, an open line and a point. (feature id, ids)."""
    doc = Document()
    s = Sketch()
    p = [s.add_point(0, 0), s.add_point(60, 0), s.add_point(60, 40), s.add_point(0, 40)]
    lines = [s.add_line(a, b) for a, b in zip(p, p[1:] + p[:1])]
    hole = s.add_circle(s.add_point(20, 20), 5)
    open_line = s.add_line(s.add_point(80, 0), s.add_point(90, 10))
    pt = s.add_point_entity(s.add_point(30, 30))
    s.solve()
    f = doc.make_feature("sketch")
    f["id"], f["name"] = "Fsk", "Pick me"
    f["params"]["sketch"] = s.to_dict()
    r.ok("POST", f"/documents/{doc_id}/tabs/T1/edit", {"op": "add_feature", "feature": f})
    return "Fsk", {"line": lines[0], "hole": hole, "open": open_line, "point": pt}


# ---------------------------------------------------------------------- the answers


def test_face_edge_and_vertex_items_carry_the_engine_names_and_equal_the_local_ones(root):
    r, doc_id, _ = _open(root)
    out = _outputs(r, doc_id)
    part = out.parts[0]
    _eng, st = _local_state()
    assert part.id in st.parts
    picks = []
    for kind, names in (("face", part.names["face"]), ("edge", part.names["edge"]), ("vertex", part.names["vertex"])):
        for i in sorted(names)[:6]:
            picks.append(_pick(part.id, kind, i, want=(kind,), point=[1.0, 2.0, 3.0]))
    got = _refs(r, doc_id, picks)
    assert got["document"] == doc_id and got["tab"] == "T1" and got["revision"] == out.revision
    assert len(got["items"]) == len(picks) >= 12
    names = {"face": part.names["face"], "edge": part.names["edge"], "vertex": part.names["vertex"]}
    for p, item in zip(picks, got["items"]):
        assert item["ok"] is True and item["kind"] == p["kind"], item
        assert item["name"] == item["ref"]["name"] == names[p["kind"]][p["index"]]       # the 1-based Element name
        want = PK.make_item({q: s.shape for q, s in st.parts.items()}, st.sketches, p["kind"],
                            (p["part"], p["index"] - 1), set(p["want"]), st.named,
                            {pid: {} for pid in st.parts} | {part.id: part.names})
        _same(PK.jsonable(want[1]), item["ref"], str(p))
    json.dumps(got)


def test_the_index_is_one_based_and_off_by_one_is_a_different_element(root):
    r, doc_id, _ = _open(root)
    part = _outputs(r, doc_id).parts[0]
    nf = len(part.names["face"])
    a, b = _refs(r, doc_id, [_pick(part.id, "face", 1), _pick(part.id, "face", 2)])["items"]
    assert a["name"] == part.names["face"][1] and b["name"] == part.names["face"][2]
    assert a["name"] != b["name"] and a["ref"] != b["ref"]                      # the same pick, one apart
    last = _refs(r, doc_id, [_pick(part.id, "face", nf)])["items"][0]
    assert last["name"] == part.names["face"][nf]
    for bad in (0, nf + 1, -1):                                                    # neither end is wrapped or clamped
        err = _refs(r, doc_id, [_pick(part.id, "face", bad)], expect=400)["error"]
        assert err.startswith("picks[0].index"), err
    for kind in ("edge", "vertex"):
        n = len(part.names[kind])
        assert _refs(r, doc_id, [_pick(part.id, kind, n, want=(kind,))])["items"][0]["ok"]
        assert _refs(r, doc_id, [_pick(part.id, kind, n + 1, want=(kind,))], expect=400)["error"].startswith(
            "picks[0].index")


def test_a_pick_the_field_cannot_take_is_a_refusal_with_a_reason(root):
    r, doc_id, _ = _open(root)
    part = _outputs(r, doc_id).parts[0]
    items = _refs(r, doc_id, [
        _pick(part.id, "vertex", 1, want=("edge",)),            # a vertex where an edge is needed
        _pick(part.id, "edge", 1, want=("face",)),
        _pick(part.id, "face", 1, want=("vertex",)),
        _pick(part.id, "face", 1, want=("edge",)),              # a face gives all its edges, as on the desktop
        _pick(part.id, "face", 1, want=("face",)),
    ])["items"]
    assert items[0]["ok"] is False and items[0]["code"] == "wrong_kind"
    assert "an edge is needed" in items[0]["reason"] and "vertex" in items[0]["reason"]
    assert items[1]["ok"] is False and "a face is needed" in items[1]["reason"]
    assert items[2]["ok"] is False and "a vertex is needed" in items[2]["reason"]
    assert items[3]["ok"] is True and items[3]["kind"] == "edges" and items[3]["name"] is None
    assert all(e["name"] for e in items[3]["ref"])
    assert items[4]["ok"] is True
    # a flat or round face where a plane or an axis is wanted, and one that is not
    out = _outputs(r, doc_id)
    nf = len(out.parts[0].names["face"])
    res = _refs(r, doc_id, [_pick(part.id, "face", i, want=("plane",)) for i in range(1, nf + 1)])["items"]
    assert any(x["ok"] and x["kind"] == "plane" and x["name"] == x["ref"]["face"]["name"] for x in res)
    assert any(not x["ok"] and x["code"] == "not_flat" for x in res), "the bracket has a curved face (a fillet)"
    res = _refs(r, doc_id, [_pick(part.id, "face", i, want=("axis",)) for i in range(1, nf + 1)])["items"]
    assert any(x["ok"] and x["kind"] == "axis" for x in res) and any(x["code"] == "no_axis" for x in res if not x["ok"])


def test_sketch_regions_and_entities_through_the_route(root):
    r, doc_id, _ = _open(root)
    fid, ids = _add_sketch(r, doc_id)
    out = _outputs(r, doc_id)
    sk = next(s for s in out.sketches if s.id == fid)
    doc = Document()                                    # the answer key: the same sketch built here
    f = doc.make_feature("sketch")
    f["id"] = fid
    f["params"]["sketch"] = sk.sketch
    doc.add_feature(f)
    sr = doc.state.sketches[fid]
    nregions = len(sr.regions)
    assert nregions == len(sk.regions) >= 2
    got = _refs(r, doc_id, [
        _pick(fid, "region", 1, want=("region",)),
        _pick(fid, "region", nregions, want=("region",)),
        _pick(fid, "sketch_line", want=("region",), entity=ids["hole"]),
        _pick(fid, "sketch_line", want=("region",), entity=ids["open"]),
        _pick(fid, "sketch_line", want=("path",), entity=ids["open"]),
        _pick(fid, "sketch_line", want=("axis",), entity=ids["hole"]),
        _pick(fid, "sketch_line", want=("axis",), entity=ids["line"]),
        _pick(fid, "sketch_line", want=("sketch_line", "axis_connector"), entity=ids["hole"]),
        _pick(fid, "sketch_line", want=("sketch_line",), entity=ids["hole"]),
        _pick(fid, "sketch_point", want=("sketch_point",), entity=ids["point"]),
        _pick(fid, "region", 1, want=("face",)),
    ])["items"]
    _same(PK.jsonable(F.region_ref(sr, fid, sr.regions[0])), got[0]["ref"])
    _same(PK.jsonable(F.region_ref(sr, fid, sr.regions[nregions - 1])), got[1]["ref"])
    assert got[0]["kind"] == got[1]["kind"] == "region" and got[0]["ref"] != got[1]["ref"]
    ri = PK.region_touching(sr, ids["hole"])
    assert got[2]["kind"] == "region"
    _same(PK.jsonable(F.region_ref(sr, fid, sr.regions[ri])), got[2]["ref"])
    assert got[3]["ok"] is False and got[3]["code"] == "no_region" and "closed area" in got[3]["reason"]
    assert got[4]["ref"] == {"kind": "sketch_chain", "sketch": fid, "entity": ids["open"]}
    assert got[5]["ref"] == {"kind": "sketch_circle", "sketch": fid, "entity": ids["hole"]}
    assert got[6]["ref"] == {"kind": "sketch_line", "sketch": fid, "line": ids["line"]}
    assert got[7]["kind"] == "sketch_line" and got[7]["ref"]["etype"] == "circle"
    assert got[8]["kind"] == "sketch_line" and "etype" not in got[8]["ref"]
    assert got[9]["kind"] == "sketch" and got[9]["ref"]["role"] == "point" and got[9]["ref"]["sig"]["at"] == [30.0, 30.0]
    assert got[10]["ok"] is False and got[10]["code"] == "wrong_kind"
    # a region index and an entity that are not there
    assert _refs(r, doc_id, [_pick(fid, "region", nregions + 1, want=("region",))], expect=400)[
        "error"].startswith("picks[0].index")
    err = _refs(r, doc_id, [_pick(fid, "sketch_line", want=("axis",), entity="nope")], expect=400)["error"]
    assert err.startswith("picks[0].entity")
    err = _refs(r, doc_id, [_pick("Fnone", "region", 1, want=("region",))], expect=400)["error"]
    assert err.startswith("picks[0].part")


def test_a_part_pick_and_the_ids_are_only_keys(root):
    r, doc_id, _ = _open(root)
    part = _outputs(r, doc_id).parts[0]
    item = _refs(r, doc_id, [_pick(part.id, "part", want=("part",))])["items"][0]
    assert item == {"ok": True, "kind": "part", "ref": part.id, "name": None}
    for pid in ("../../etc/passwd", "nope", "F2/9", part.id + "x"):
        err = _refs(r, doc_id, [_pick(pid, "face", 1)], expect=400)["error"]
        assert err.startswith("picks[0].part"), err


# ---------------------------------------------------------------------- the route's rules

def test_refs_is_allowed_on_a_read_only_document(root):
    r = root()
    doc_id, folder = r.copy(BRACKET, "Doc")
    held = doc_lock.acquire(folder, "desktop")
    opened = r.ok("POST", f"/documents/{doc_id}/open")
    assert opened["read_only"] is True and opened["holder"]["program"] == "desktop"
    part = _outputs(r, doc_id).parts[0]
    item = _refs(r, doc_id, [_pick(part.id, "face", 1)])["items"][0]
    assert item["ok"] and item["name"] == part.names["face"][1]
    status, body = r.request("POST", f"/documents/{doc_id}/tabs/T1/edit",
                             {"op": "set_param", "feature": "F2", "key": "depth", "value": 9})
    assert status == 423                                  # the same Document still refuses an edit
    assert doc_lock.holder(folder).instance == held.instance


def test_unknown_document_tab_and_method(root):
    r, doc_id, _ = _open(root)
    part = _outputs(r, doc_id).parts[0]
    body = {"picks": [_pick(part.id, "face", 1)]}
    assert r.request("POST", f"/documents/{doc_id}/tabs/T9/refs", body)[0] == 404
    assert r.request("POST", "/documents/nosuchdoc/tabs/T1/refs", body)[0] == 404
    assert r.request("GET", f"/documents/{doc_id}/tabs/T1/refs")[0] == 405
    assert r.request("POST", "/tabs/T1/refs", body)[0] == 404         # no un-prefixed alias for a new route
    assert r.request("POST", f"/documents/{doc_id}/tabs/T1/refs/x", body)[0] == 404


def test_refs_on_an_assembly_tab_is_a_400(root):
    r, doc_id, _ = _open(root, HINGE, "Hinge")
    status, body = r.request("POST", f"/documents/{doc_id}/tabs/T2/refs",
                             {"picks": [_pick("x", "face", 1)]})
    assert status == 400 and "Part Studio" in body["error"], body


def test_host_and_origin_and_body_rules_hold(root):
    r, doc_id, _ = _open(root)
    part = _outputs(r, doc_id).parts[0]
    path = f"/documents/{doc_id}/tabs/T1/refs"
    good = {"picks": [_pick(part.id, "face", 1)]}
    assert r.request("POST", path, good, headers={"Host": "evil.example"})[0] in (403, 421)
    assert r.request("POST", path, good, headers={"Origin": "https://evil.example"})[0] == 403
    assert r.request("POST", path, good, headers={"Content-Type": "text/plain"})[0] == 415
    for head, code in ((f"Content-Length: {SV.MAX_BODY + 1}\r\n", b"413"), ("", b"411")):   # sent without any body
        s = socket.create_connection(("127.0.0.1", r.port), timeout=30)
        try:
            s.sendall((f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{r.port}\r\nContent-Type: application/json\r\n"
                       f"{head}\r\n").encode())
            assert s.recv(4096).split(b" ", 2)[1] == code
        finally:
            s.close()


def test_hostile_bodies_are_a_400_naming_the_field_never_a_500(root):
    r, doc_id, _ = _open(root)
    part = _outputs(r, doc_id).parts[0]
    path = f"/documents/{doc_id}/tabs/T1/refs"
    ok = _pick(part.id, "face", 1)

    def raw(text, expect=400):
        status, body = r.request("POST", path, raw=text.encode() if isinstance(text, str) else text)
        assert status == expect, (text[:80], status, body)
        assert isinstance(body, dict) and body["error"], body
        return body["error"]

    def post(body):
        status, payload = r.request("POST", path, body)
        assert status == 400, (str(body)[:100], status, payload)
        return payload["error"]
    # the list
    assert post({"picks": [ok] * (PK.MAX_PICKS + 1)}).startswith("picks:")
    assert post({"picks": [ok] * 5000}).startswith("picks:")
    assert post({"picks": []}).startswith("picks:")
    assert post({}).startswith("picks:") and post({"picks": None}).startswith("picks:")
    assert post({"picks": "face"}).startswith("picks:") and post({"picks": {"a": 1}}).startswith("picks:")
    assert post({"picks": [ok], "extra": 1}).startswith("extra:")
    # numbers that are not finite, written as the JSON parser of Python accepts them
    for lit in ("NaN", "Infinity", "-Infinity", "1e999", "-1e999", "1e300"):
        assert raw('{"picks": [{"part": "%s", "kind": "face", "index": 1, "want": ["face"], "point": [1, 2, %s]}]}'
                   % (part.id, lit)).startswith("picks[0].point")
    for lit in ("NaN", "Infinity", "1e999", "1.5", "-3", "0", "99999999999999999999999999", "true", '"1"', "null",
                "[1]", "{}"):
        assert raw('{"picks": [{"part": "%s", "kind": "face", "index": %s, "want": ["face"]}]}'
                   % (part.id, lit)).startswith("picks[0].index")
    # wrong types and unknown values, in the second pick (the message says which)
    for bad, field in (({"part": 7, "kind": "face", "index": 1, "want": ["face"]}, "picks[1].part"),
                       ({"part": ["a"], "kind": "face", "index": 1, "want": ["face"]}, "picks[1].part"),
                       ({"part": "", "kind": "face", "index": 1, "want": ["face"]}, "picks[1].part"),
                       ({"part": "p" * 5000, "kind": "face", "index": 1, "want": ["face"]}, "picks[1].part"),
                       ({"part": part.id, "kind": "solid", "index": 1, "want": ["face"]}, "picks[1].kind"),
                       ({"part": part.id, "kind": None, "index": 1, "want": ["face"]}, "picks[1].kind"),
                       ({"part": part.id, "kind": ["face"], "index": 1, "want": ["face"]}, "picks[1].kind"),
                       ({"part": part.id, "kind": "face", "index": "1", "want": ["face"]}, "picks[1].index"),
                       ({"part": part.id, "kind": "face", "index": 1, "want": "face"}, "picks[1].want"),
                       ({"part": part.id, "kind": "face", "index": 1, "want": ["nope"]}, "picks[1].want"),
                       ({"part": part.id, "kind": "face", "index": 1, "want": [["face"]]}, "picks[1].want"),
                       ({"part": part.id, "kind": "face", "index": 1, "want": ["face"] * 100}, "picks[1].want"),
                       ({"part": part.id, "kind": "face", "index": 1, "want": ["face"], "point": "0,0,0"},
                        "picks[1].point"),
                       ({"part": part.id, "kind": "face", "index": 1, "want": ["face"], "point": [1, 2]},
                        "picks[1].point"),
                       ({"part": part.id, "kind": "face", "index": 1, "want": ["face"], "point": [1, 2, "3"]},
                        "picks[1].point"),
                       ({"part": part.id, "kind": "face", "index": 1, "want": ["face"], "point": [[1], 2, 3]},
                        "picks[1].point"),
                       ({"part": part.id, "kind": "face", "index": 1, "want": ["face"], "point": [True, 2, 3]},
                        "picks[1].point"),
                       ({"part": part.id, "kind": "face", "index": 1, "want": ["face"], "evil": {"__proto__": 1}},
                        "picks[1].evil"),
                       ({"part": part.id, "kind": "face", "index": 1, "index2": 1, "want": ["face"]}, "picks[1].index2"),
                       ({"part": part.id, "kind": "sketch_line", "entity": 5, "want": ["axis"]}, "picks[1].entity"),
                       ({"part": part.id, "kind": "face", "index": 1, "entity": "l1", "want": ["face"]},
                        "picks[1].entity"),
                       ([1, 2, 3], "picks[1]"), ("pick", "picks[1]"), (None, "picks[1]"), (5, "picks[1]")):
        assert post({"picks": [ok, bad]}).startswith(field), (bad, field)
    # nested far past the parser's depth and past what a pick may hold: a 400, and the server carries on
    for depth in (200, 5000, 300000):
        assert raw('{"picks": ' + "[" * depth + "]" * depth + "}") is not None
        assert raw('{"picks": [{"part": ' + "[" * depth + "1" + "]" * depth + "}]}") is not None
        assert raw('{"picks": [{"part": "' + part.id + '", "kind": "face", "index": 1, "want": ["face"], "point": '
                   + "[" * depth + "]" * depth + "}]}") is not None
    assert raw('{"picks": ' + "{" * 200000) .startswith("body is not valid JSON")        # not even closed
    # not JSON, not an object, not UTF-8
    assert raw("[1, 2]").startswith("body must be")
    assert raw("not json").startswith("body is not valid JSON")
    assert raw(b'{"picks": [\xff\xfe]}').startswith("body is not valid JSON")
    assert raw("").startswith("body")                      # an empty body is a 400 too
    # still alive, and still right
    assert _refs(r, doc_id, [ok])["items"][0]["name"] == part.names["face"][1]
    assert r.request("GET", "/health")[0] == 200


def test_a_full_list_of_picks_is_answered(root):
    r, doc_id, _ = _open(root)
    part = _outputs(r, doc_id).parts[0]
    n = len(part.names["face"])
    picks = [_pick(part.id, "face", 1 + i % n, want=("face",)) for i in range(PK.MAX_PICKS)]
    items = _refs(r, doc_id, picks)["items"]
    assert len(items) == PK.MAX_PICKS and all(x["ok"] for x in items)


# ---------------------------------------------------------------------- a query changes nothing

def _state_of(r, doc_id, tab):
    d = r.server.docs[doc_id]
    sess = d.session
    eng = sess._eng
    out = eng.outputs(tab)
    return {"views": r.ok("GET", f"/documents/{doc_id}/tabs")["tabs"], "revision": out.revision,
            "undo": (getattr(out, "can_undo", None), getattr(out, "can_redo", None)),
            "dirty": {t["id"]: t["dirty"] for t in sess.tabs()}, "unsaved": sess.has_unsaved(), "seq": d.seq,
            "rebuilds": eng.rebuild_counts(), "order": eng.rebuild_order(), "edges": eng.link_edges(),
            "digests": {t: {u: (e.digest, e.keys) for u, e in eng.links.graph.inputs(t).items()}
                        for t, *_ in eng.tabs()}}


def test_a_refs_call_changes_nothing_not_even_the_link_digests(root):
    r, doc_id, _ = _open(root, HINGE, "Hinge")
    part = _outputs(r, doc_id).parts[0]
    # an edit first, so there is something to undo, something unsaved, and a rebuilt consumer behind it
    out = _outputs(r, doc_id)
    feat = next(f for f in out.features if f.type == "extrude")
    r.ok("POST", f"/documents/{doc_id}/tabs/T1/edit", {"op": "rename_feature", "feature": feat.id, "name": "Boss"})
    before = _state_of(r, doc_id, "T1")
    asm_before = _state_of(r, doc_id, "T2")
    assert before["undo"][0] is True and before["unsaved"] is True and before["dirty"]["T1"] is True
    assert before["digests"]["T2"], "the Assembly has no recorded input from the Part Studio"
    picks = [_pick(part.id, "face", 1, want=("face",)), _pick(part.id, "face", 2, want=("edge",)),
             _pick(part.id, "edge", 1, want=("axis",)), _pick(part.id, "vertex", 1, want=("vertex",)),
             _pick(part.id, "vertex", 1, want=("face",))]
    for _ in range(3):
        assert all(x for x in _refs(r, doc_id, picks)["items"])
    _refs(r, doc_id, [_pick(part.id, "face", 0)], expect=400)                  # a refused call changes nothing either
    _refs(r, doc_id, [_pick("nope", "face", 1)], expect=400)
    assert _state_of(r, doc_id, "T1") == before
    assert _state_of(r, doc_id, "T2") == asm_before


def test_a_refs_call_on_a_document_nobody_edited_leaves_it_clean(root):
    r2, doc2, _ = _open(root, BRACKET, "Clean")
    p2 = _outputs(r2, doc2).parts[0]
    clean = _state_of(r2, doc2, "T1")
    assert clean["undo"] == (False, False) and clean["unsaved"] is False
    _refs(r2, doc2, [_pick(p2.id, "face", 3)])
    assert _state_of(r2, doc2, "T1") == clean


def test_the_engine_request_is_a_query_on_both_executors():
    """The contract: `refs` on the in-process and on the process engine give one answer, move no revision, add
    nothing to the process engine's edit log, and refuse what it should."""
    picks = [_pick("F2/1", "face", i, want=(w,)) for i in (1, 2, 3) for w in ("face", "plane", "axis", "edge")]
    picks += [_pick("F2/1", "vertex", 1, want=("vertex",)), _pick("F2/1", "part", want=("part",)),
              _pick("F1", "region", 1, want=("region",))]
    answers = []
    for make in (InProcessEngine, ProcessEngine):
        with make() as eng:
            tab = eng.open_tab(BRACKET_FILE)
            out = eng.outputs(tab)
            part = next(iter(out.parts))
            assert part.id == "F2/1"
            before = (out.revision, out.can_undo, out.can_redo, eng.rebuild_counts())
            log = list(eng._get(tab).edits) if make is ProcessEngine else None
            got = eng.refs(tab, picks)
            assert got == eng.refs(tab, picks)
            after = eng.outputs(tab)
            assert (after.revision, after.can_undo, after.can_redo, eng.rebuild_counts()) == before
            if log is not None:
                assert eng._get(tab).edits == log and eng._get(tab).revision == before[0]
            for bad in ([_pick("nope", "face", 1)], [_pick("F2/1", "face", 999)], [{"kind": "face"}], "x", [],
                        [_pick("F2/1", "face", 1, want=("bogus",))]):
                with pytest.raises(BadPick):
                    eng.refs(tab, bad)
            answers.append(got)
            assert eng.outputs(tab).revision == before[0]
    _same(answers[0], answers[1], "process vs in-process")
    assert any(not a["ok"] for a in answers[0]) and any(a["ok"] for a in answers[0])
    with ProcessEngine() as eng:
        t2 = eng.new_tab("assembly")
        with pytest.raises(EngineError, match="Part Studio"):
            eng.refs(t2, picks)
        t1 = eng.open_tab(BRACKET_FILE)
        with pytest.raises(EngineError):
            eng.refs("T99", picks)
        assert eng.refs(t1, picks[:1])[0]["ok"] is True
        assert K is not None
