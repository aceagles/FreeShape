"""What changed between two states of a Part Studio, in words and as added / removed material (freeshape/history.py).

From upstream 2.0's version history tests. Upstream keeps versions inside the Part Studio file; this fork keeps
them as git tags of the Document, so the three upstream tests of in-file storage, restore and the History panel
built on it are not here (see the merge notes). The comparison is tested on snapshots."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import math  # noqa: E402

from freeshape import history as H, kernel as K  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402
from test_20_stage4 import _sketch, _extrude  # noqa: E402


def _block():
    doc = Document()
    sid, sr = _sketch(doc, [(0, 0), (40, 0), (40, 20), (0, 20)])
    e = _extrude(doc, sid, sr, depth=10, op="new")
    return doc, sid, e


def _vol(state):
    return sum(K.volume(p.shape) for p in state.parts.values())


def test_what_changed_reads_like_a_person_wrote_it():
    doc, sid, e = _block()
    before = H.snapshot(doc)
    f = doc.feature(e["id"])
    doc.replace_feature(dict(f, name="Base", params=dict(f["params"], depth=15)))
    sid2, sr2 = _sketch(doc, circle=(20, 10, 4))
    _extrude(doc, sid2, sr2, end="through", op="remove")
    sk = doc.feature(sid)
    s = Sketch.from_dict(sk["params"]["sketch"])
    s.add_constraint("distance", next(k for k, x in s.entities.items() if x["type"] == "line"), value=40.0)
    s.add_line(s.add_point(0, 0), s.add_point(5, 5), construction=True)
    doc.replace_feature(dict(sk, params=dict(sk["params"], sketch=s.to_dict())))
    res = H.compare(doc, before)
    by = {c["name"]: c for c in res["features"]}
    assert by["Base"]["lines"] == ["Renamed from “Extrude 1”", "Depth: 10 mm → 15 mm"]
    assert "Added a distance dimension (40 mm)" in by["Sketch 1"]["lines"]
    assert "Added 1 construction line" in by["Sketch 1"]["lines"]
    assert by["Sketch 2"]["kind"] == "added" and by["Extrude 1"]["kind"] == "added"
    (name, a, b), = res["geometry"]["rows"]
    assert abs(a - 8000) < 1e-6 and abs(b - (12000 - math.pi * 16 * 15)) < 1e-6
    add, rem = res["geometry"]["added"], res["geometry"]["removed"]
    assert abs(sum(K.volume(x) for x in add) - (4000 - math.pi * 16 * 5)) < 1e-6        # the top 5 mm, less the hole
    assert abs(sum(K.volume(x) for x in rem) - math.pi * 16 * 10) < 1e-6                 # the hole in the old block
    html = H.to_html(res)
    assert "Depth: 10 mm → 15 mm" in html and "cm³" in html


def test_variables_suppression_and_reordering():
    doc, sid, e = _block()
    v = doc.make_feature("variable")
    v["params"].update(name="wall", expr="3")
    doc.add_feature(v, 0)
    before = H.snapshot(doc)
    doc.replace_feature(dict(doc.feature(v["id"]), params={"name": "wall", "expr": "4"}))
    doc.set_suppressed(e["id"], True)
    doc.move_feature(v["id"], 2)
    res = H.compare(doc, before, geometry=False)
    lines = {c["name"]: c["lines"] for c in res["features"]}
    assert "#wall: 3 → 4" in lines["Variable 1"] and "Suppressed" in lines["Extrude 1"]
    assert any("Moved in the feature list" in x for x in lines.values() for x in x)


def test_two_snapshots_compare_without_touching_the_open_part_studio():
    doc, sid, e = _block()
    first = H.snapshot(doc)
    f = doc.feature(e["id"])
    doc.replace_feature(dict(f, params=dict(f["params"], depth=30)))
    second = H.snapshot(doc)
    doc.replace_feature(dict(doc.feature(e["id"]), params=dict(f["params"], depth=50)))
    res = H.compare(doc, first, second)
    assert res["features"][0]["lines"] == ["Depth: 10 mm → 30 mm"]
    (name, a, b), = res["geometry"]["rows"]
    assert abs(a - 8000) < 1e-6 and abs(b - 24000) < 1e-6
    assert abs(_vol(doc.state) - 40000) < 1e-6          # the open Part Studio is still at depth 50
