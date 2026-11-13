"""SP2 smoke: Standard part screws in an Assembly stay seated, are saved without a shape, and are counted.

Through the public Document and AssemblyDoc API it builds a 60 x 40 x 8 plate Part Studio with four 3.4 mm
through holes and one 2.5 mm hole, inserts the plate into an Assembly, inserts four ISO 4762 M3 x 10 and fastens
each by under_head to a 3.4 mm hole's top rim, and one more into the 2.5 mm hole. It changes all five to
length 16 with set_standard_params, saves to a temporary folder and loads the file into a fresh AssemblyDoc.
After the length change and again after the reload it checks seating (under_head on the rim centre within
1e-6 mm, tip inside the hole below the top surface, no mate errors); it checks the saved JSON (five standard
instances of length 16, no shape, no blobs, mate ends of kind named), the BOM (one row, ISO 4762 M3 x 16,
quantity 5) and that interferences() returns no pair.

Prints "SP2 OK" and exits 0, or names the first failure and exits non-zero.

    both fs_py scripts/smoke/sp2_instances.py
"""
import json
import os
import shutil
import sys
import tempfile
import time

import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from conftest import add, rect_lines, region_refs, sketch_feature  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import links as L  # noqa: E402
from freeshape.assembly import AssemblyDoc  # noqa: E402
from freeshape.document import PartStudioDoc  # noqa: E402
from freeshape.pyfeature import registry  # noqa: E402,F401  (loads the bundled plugins)

SCREW = "sp:fasteners/iso4762"
W, D, T = 60.0, 40.0, 8.0                       # plate; its top surface is z = T
THROUGH = [(10.0, 10.0), (50.0, 10.0), (10.0, 30.0), (50.0, 30.0)]      # 3.4 mm through holes
TAP = (30.0, 20.0)                                                     # 2.5 mm hole
R_THROUGH, R_TAP = 1.7, 1.25
LENGTH = 16


class Failure(Exception):
    pass


def check(cond, msg):
    if not cond:
        raise Failure(msg)


def plate_document():
    doc = PartStudioDoc()
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, W, D))
    add(doc, "extrude", regions=region_refs(doc, sid), depth=T, op="new")
    for pts, dia in ((THROUGH, 2 * R_THROUGH), ([TAP], 2 * R_TAP)):
        def points(s, pts=pts):
            for x, y in pts:
                s.add_point_entity(s.add_point(x, y))
        hs = sketch_feature(doc, points)
        add(doc, "hole", sketch=hs, standard="custom", diameter=dia, end="through", style="simple")
    check(len(doc.state.parts) == 1, f"the plate Part Studio has {len(doc.state.parts)} parts, expected 1")
    part = next(iter(doc.state.parts.values()))
    lo, hi = K.bbox(part.shape)
    check(np.allclose(hi - lo, [W, D, T], atol=1e-6), f"plate is {(hi - lo).round(6).tolist()}, expected {[W, D, T]}")
    holes = [e for e in K.edges(part.shape) if K.edge_info(e).get("center") is not None]
    check(len(holes) == 2 * (len(THROUGH) + 1), f"plate has {len(holes)} circular edges, expected 10")
    return doc, part.id


def rim_index(asm, iid, xy, r):
    """Index of the hole's circular edge on the top surface, at xy with radius r."""
    for i, e in enumerate(K.edges(asm.parts[iid].shape)):
        info = K.edge_info(e)
        c = info.get("center")
        if (c is not None and abs(c[2] - T) < 1e-6 and abs(c[0] - xy[0]) < 1e-6 and abs(c[1] - xy[1]) < 1e-6
                and abs(info["r"] - r) < 1e-6):
            return i
    raise Failure(f"no top rim at {xy} with radius {r} on the plate")


def build_assembly(studio):
    resolver = L.LinkResolver(find_open_studio=lambda uid, path: studio if uid == studio.uid else None)
    asm = AssemblyDoc()
    asm.resolver = resolver.resolve_source
    plate_part = next(iter(studio.state.parts.values()))
    plate = asm.insert({"kind": "studio", "uid": studio.uid, "part": plate_part.id, "studio_name": studio.name})
    check(asm.instance(plate)["fixed"], "the plate is not fixed as the first instance")
    holes = [(xy, R_THROUGH) for xy in THROUGH] + [(TAP, R_TAP)]
    screws = []
    for xy, r in holes:
        s = asm.insert_standard_part(SCREW, {"size": "M3", "length": 10})
        rim = asm.connector(plate, "edge", rim_index(asm, plate, xy, r))
        m = asm.make_mate("fastened")
        m["a"], m["b"] = rim, asm.named_connector(s, "under_head")
        asm.add_mate(m)
        screws.append((s, xy, r, m))
    check(asm.errors == {}, f"errors after fastening: {asm.errors}")
    return asm, resolver, plate, screws


def check_seated(asm, plate, screws, length, label):
    check(asm.errors == {}, f"{label}: mate errors {asm.errors}")
    mate_ids = {m["id"] for *_rest, m in screws}
    check(mate_ids <= {m["id"] for m in asm.mates}, f"{label}: a fastened mate is missing")
    pshape = asm.world_shape(plate)
    for s, xy, r, m in screws:
        head = asm.world_frame(asm.named_connector(s, "under_head"))
        tip = asm.world_frame(asm.named_connector(s, "tip"))
        want = [xy[0], xy[1], T]
        check(np.allclose(head["o"], want, atol=1e-6),
              f"{label}: {asm.instance(s)['name']} under_head at {np.round(head['o'], 6).tolist()}, rim centre {want}")
        check(np.allclose(head["z"], [0, 0, -1], atol=1e-6),
              f"{label}: {asm.instance(s)['name']} is not head-up, under_head Z {np.round(head['z'], 6).tolist()}")
        check(tip["o"][2] < T - 1e-6 and np.hypot(tip["o"][0] - xy[0], tip["o"][1] - xy[1]) < r,
              f"{label}: {asm.instance(s)['name']} tip {np.round(tip['o'], 6).tolist()} is not inside the hole "
              f"below z = {T}")
        check(abs(tip["o"][2] - (T - length)) < 1e-6,
              f"{label}: tip at z = {tip['o'][2]:.6f}, expected {T - length} for length {length}")
        if r > R_TAP + 1e-9:     # a 3.4 mm hole clears the shank; the 2.5 mm one is meant to overlap
            vol = K.volume(K.common(asm.world_shape(s), pshape))
            check(vol < 1e-3, f"{label}: {asm.instance(s)['name']} overlaps the plate by {vol:.4f} mm^3 in a 3.4 mm hole")


def check_json(path, screw_ids):
    with open(path) as fh:
        raw = fh.read()
    d = json.loads(raw)
    std = [i for i in d["instances"] if i["source"].get("kind") == "standard"]
    check(len(std) == 5, f"saved JSON has {len(std)} standard instances, expected 5")
    check({i["id"] for i in std} == set(screw_ids), "saved standard instance ids differ from the inserted screws")
    for i in std:
        src = i["source"]
        check(src["type"] == SCREW and src["params"].get("length") == LENGTH and src["params"].get("size") == "M3",
              f"saved {i['id']} source is {src}, expected {SCREW} M3 length {LENGTH}")
        check("_brep" not in i and "_brep_blob" not in i, f"saved {i['id']} carries a shape (_brep_blob)")
    check(not d.get("blobs"), f"saved JSON has blobs: {list(d.get('blobs', {}))}")    # no `blobs` since I4.9
    fastened = [m for m in d["mates"] if m.get("type") == "fastened"]
    check(len(fastened) == 5, f"saved JSON has {len(fastened)} fastened mates, expected 5")
    ends = [m["b"] for m in fastened]
    check(all(e.get("kind") == "named" and e.get("name") == "under_head" and e["inst"] in screw_ids for e in ends),
          f"saved mate ends are not all named under_head on a screw: {ends}")
    check(all("index" not in e and "ref" not in e for e in ends), "a saved named mate end carries index or ref")
    check(len(raw) < 200_000, f"saved file is {len(raw)} bytes; the screws look embedded")


def check_bom_and_clashes(asm, plate, label):
    rows = [r for r in asm.bom() if r["source"] == "Standard part"]
    check(len(rows) == 1, f"{label}: {len(rows)} Standard part BOM rows {[(r['name'], r['qty']) for r in rows]}, "
                          "expected 1")
    check(rows[0]["name"] == "ISO 4762 M3 x 16" and rows[0]["qty"] == 5,
          f"{label}: BOM row is {rows[0]['name']!r} x {rows[0]['qty']}, expected 'ISO 4762 M3 x 16' x 5")
    check(len(asm.bom()) == 2, f"{label}: BOM has {len(asm.bom())} rows, expected the plate and the screws")
    pairs = [(a, b) for a, b, *_ in asm.interferences()]
    check(pairs == [], f"{label}: interferences() reported {pairs}")


def main():
    t0 = time.time()
    tmp = tempfile.mkdtemp(prefix="sp2_instances_")
    try:
        studio, _pid = plate_document()
        print(f"plate Part Studio: {W:g} x {D:g} x {T:g}, {len(THROUGH)} x 3.4 mm + 1 x 2.5 mm holes")
        asm, resolver, plate, screws = build_assembly(studio)
        ids = [s for s, *_ in screws]
        check_seated(asm, plate, screws, 10, "M3 x 10")
        print(f"{len(screws)} ISO 4762 M3 x 10 fastened by under_head to the top rims, seated")

        res = asm.set_standard_params(ids, {"length": LENGTH})
        check(res["rejected"] == {} and sorted(res["changed"]) == sorted(ids), f"set_standard_params gave {res}")
        check_seated(asm, plate, screws, LENGTH, "after length change")
        check_bom_and_clashes(asm, plate, "after length change")
        print(f"length {LENGTH}: still seated, BOM ISO 4762 M3 x {LENGTH} x 5, no interference")

        path = os.path.join(tmp, "sp2.fasm")
        asm.save(path)
        check_json(path, ids)
        print("saved JSON: 5 standard instances, no shape, no blobs, mate ends named")

        back = AssemblyDoc.load(path, resolver=resolver.resolve_source)
        back.refresh()
        check_seated(back, plate, screws, LENGTH, "after reload")
        check_bom_and_clashes(back, plate, "after reload")
        print("reloaded: still seated, BOM and interference unchanged")
        print(f"run time {time.time() - t0:.1f} s")
        print("SP2 OK")
    except Failure as e:
        print(f"SP2 FAILED: {e}")
        sys.exit(1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
