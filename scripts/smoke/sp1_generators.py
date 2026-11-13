"""S1 smoke: the bundled Fasteners Plugin's six Standard part types.

With a fresh import of freeshape.pyfeature (bundled Plugins only) it checks that the six type ids are
registered with no registry problem, builds every size at its shortest and longest listed length for each
type (each point type for ISO 7049) and checks validity, compares every entry of
tests/data/fasteners/freecad_goldens.json (volume within 0.1 %, bounding box within 0.01 mm), and checks
each type's connectors by name. Prints "SP1 OK" and exits 0, or names the first failure and exits non-zero.
Parts are built with default thread settings.

    both fs_py scripts/smoke/sp1_generators.py
"""
import json
import os
import sys

import numpy as np
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)

from freeshape import kernel as K  # noqa: E402
from freeshape import pyfeature  # noqa: E402
from freeshape.pyfeature import registry  # noqa: E402,F401  (loads the bundled plugins)

GOLDENS = os.path.join(ROOT, "tests", "data", "fasteners", "freecad_goldens.json")
TYPE_IDS = tuple(f"sp:fasteners/iso{n}" for n in (4014, 4017, 4032, 7089, 4762, 7049))
SCREWS = ("iso4014", "iso4017", "iso4762", "iso7049")
FLATS = ("iso4032", "iso7089")
GOLDEN_KEYS = ("ISO4014", "ISO4017", "ISO4032", "ISO7089", "ISO4762", "ISO7049-C", "ISO7049-F", "ISO7049-R")
VOLUME_REL = 1e-3
BBOX_ABS = 0.01


class Failure(Exception):
    pass


def check(cond, msg):
    if not cond:
        raise Failure(msg)


def tight_bbox(shape):
    """Exact box. K.bbox is up to 0.15 mm too high on a B-spline head on OCP 8.0, so ISO 7049 is compared
    on this one, as tests/test_fasteners_socket_tapping.py does."""
    b = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, b, False, False)
    return np.array(b.CornerMin().Coord()), np.array(b.CornerMax().Coord())


def part(tid, **kw):
    cls = pyfeature.standard_part(f"sp:fasteners/{tid}")
    check(cls is not None, f"{tid} is not registered")
    return cls(**kw)


def num(v):
    v = float(v)
    return int(v) if v == int(v) else v


def check_registry():
    ids = [t for t, _cls, _info in pyfeature.standard_parts()]
    missing = [t for t in TYPE_IDS if t not in ids]
    check(not missing, f"Standard part types missing: {missing}")
    probs = pyfeature.problems()
    check(not probs, f"registry problems: {probs}")
    print(f"{len(TYPE_IDS)} Standard part types present, {len(probs)} registry problems")


def variants(tid):
    """[(label, part, extra params)]: ISO 7049 once per point type."""
    if tid == "iso7049":
        return [(f"{tid}-{pt}", part(tid), {"point": pt}) for pt in "CFR"]
    return [(tid, part(tid), {})]


def check_builds():
    builds = 0
    for tid in SCREWS + FLATS:
        for label, p, extra in variants(tid):
            for size in p.sizes(**extra):
                lengths = p.lengths(size, **extra) if tid in SCREWS else [None]
                check(lengths or tid in FLATS, f"{label} {size}: no lengths listed")
                picks = [lengths[0]] if len(lengths) == 1 or lengths[0] is None else [lengths[0], lengths[-1]]
                for length in picks:
                    params = {"size": size, **extra}
                    if length is not None:
                        params["length"] = length
                    try:
                        shape = p.build(params)
                        ok = K.is_valid(shape) and K.volume(shape) > 0
                    except Exception as e:  # noqa: BLE001
                        raise Failure(f"{label} {params}: build raised {e!r}")
                    check(ok, f"{label} {params}: invalid or empty solid")
                    builds += 1
    print(f"{builds} solids built, all valid (shortest and longest length per size)")


def golden_part(entry):
    t = entry["type"]
    params = {"size": entry["diameter"].strip("()")}   # "(M14)" marks a non-preferred size
    if entry["length"] is not None:
        params["length"] = num(entry["length"])
    if t.startswith("ISO7049"):
        params["point"] = t[-1]
        return part("iso7049"), params
    return part(t.lower()), params


def check_goldens():
    with open(GOLDENS) as f:
        entries = json.load(f)["entries"]
    check(set(entries) == set(GOLDEN_KEYS), f"golden type keys are {sorted(entries)}")
    total = sum(len(v) for v in entries.values())
    compared = outside = 0
    for key in GOLDEN_KEYS:
        for e in entries[key]:
            p, params = golden_part(e)
            shape = p.build(params)
            tag = f"{key} {params}"
            check(K.is_valid(shape), f"{tag}: invalid solid")
            vol = K.volume(shape)
            compared += 1
            if abs(vol - e["volume"]) > VOLUME_REL * abs(e["volume"]):
                outside += 1
                raise Failure(f"{tag}: volume {vol:.4f} vs FreeCAD {e['volume']:.4f}")
            lo, hi = tight_bbox(shape) if key.startswith("ISO7049") else K.bbox(shape)
            for got, want in zip(list(lo) + list(hi), e["bbox"]):
                if abs(got - want) > BBOX_ABS:
                    outside += 1
                    raise Failure(f"{tag}: bbox {[round(float(x), 4) for x in list(lo) + list(hi)]} "
                                  f"vs FreeCAD {e['bbox']}")
    check(compared == total, f"compared {compared} of {total} golden entries")
    print(f"golden entries under {len(entries)} type keys: {compared} of {total} compared, "
          f"{outside} outside tolerance")


def check_connectors():
    for tid in SCREWS:
        for label, p, extra in variants(tid):
            size = p.sizes(**extra)[0]
            length = p.lengths(size, **extra)[-1]
            params = {"size": size, "length": length, **extra}
            c = p.connectors(params)
            check({"under_head", "tip"} <= set(c), f"{label}: connectors {sorted(c)} lack under_head/tip")
            check(p.insert_connector == "under_head", f"{label}: insert connector {p.insert_connector!r}")
            uh, tip = np.array(c["under_head"]["o"]), np.array(c["tip"]["o"])
            z = np.array(c["under_head"]["z"], dtype=float)
            check(abs(np.linalg.norm(uh - tip) - float(length)) < 1e-6, f"{label} {params}: under_head to tip is not the length")
            check(np.allclose(c["tip"]["z"], c["under_head"]["z"]), f"{label}: tip and under_head z differ")
            shape = p.build(params)
            check(K.material_at(shape, uh + 1.0 * z), f"{label} {params}: no material 1 mm along under_head Z")
    for tid in FLATS:
        p = part(tid)
        size = p.sizes()[0]
        params = {"size": size}
        c = p.connectors(params)
        check({"bottom", "top"} <= set(c), f"{tid}: connectors {sorted(c)} lack bottom/top")
        check(p.insert_connector == "bottom", f"{tid}: insert connector {p.insert_connector!r}")
        height = float(np.linalg.norm(np.array(c["top"]["o"]) - np.array(c["bottom"]["o"])))
        lo, hi = K.bbox(p.build(params))
        check(height > 0 and abs(height - (hi[2] - lo[2])) < 1e-6, f"{tid} {size}: top-bottom distance is not the thickness")
    print(f"connectors checked by name on {len(TYPE_IDS)} types")


def main():
    for step in (check_registry, check_builds, check_goldens, check_connectors):
        try:
            step()
        except Failure as e:
            print(f"SP1 FAILED in {step.__name__}: {e}", file=sys.stderr)
            return 1
    print("SP1 OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
