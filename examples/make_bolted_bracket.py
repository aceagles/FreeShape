"""Builds the bolted-bracket example with the Python API: a Part Studio (examples/bolted_bracket_parts.fshape)
with an L bracket and a base plate, and an assembly (examples/bolted_bracket.fasm) that seats the bracket on the
plate with a fastened mate (plate fixed). It is the small two-part model for trying the Standard parts in the
Insert panel: washers, screws and nuts in the four M4 holes, M3 screws in the two tapped holes, and a 2.5 mm hole
in the upright for a tapping screw.

    cd examples && python make_bolted_bracket.py

Plate: 80 x 50 x 6 mm, x -10..70, y -25..25, z 0..6. Four M4 clearance holes (4.5 mm) at x 15 and 50, y -12 and 12.
Bracket: base 60 x 40 x 6 mm (x 0..60, y -20..20) with a 6 mm upright 30 mm high at x 0..6.
It has the same four M4 clearance holes, two M3 tapped holes (2.5 mm tap drill, through the base) at x 32.5,
y -12 and 12, and a 2.5 mm hole through the upright at y 0, z 20. In the assembly the bracket sits on the plate,
its base on z 6..12, so the bracket's top face is z 12.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape import features as F, kernel as K
from freeshape.assembly import AssemblyDoc
from freeshape.document import Document
from freeshape.sketch import Sketch

HERE = os.path.dirname(os.path.abspath(__file__))

PLATE_T = 6.0
BASE_T = 6.0
M4_HOLE = F.CLEARANCE["M4"][1]            # 4.5 mm, normal fit
M3_TAP = F.TAP_DRILL["M3"]                # 2.5 mm
UPRIGHT_HOLE = F.TAP_DRILL["M3"]          # 2.5 mm: also the hole size of an ST2.2 / M2.3 tapping screw
M4_XS, HOLE_YS, M3_X = (15.0, 50.0), (-12.0, 12.0), 32.5
BRACKET_DX = 100.0                        # in the Part Studio the bracket sits 100 mm along x from the plate, so a
                                          # through hole in one never cuts the other
UPRIGHT_HOLE_Z = 20.0


def rect(s, x0, y0, w, h):
    p = [s.add_point(x0, y0), s.add_point(x0 + w, y0), s.add_point(x0 + w, y0 + h), s.add_point(x0, y0 + h)]
    for a, b in zip(p, p[1:] + p[:1]):
        s.add_line(a, b)


def sketch(doc, name, plane, build):
    s = Sketch()
    build(s)
    s.solve()
    f = doc.make_feature("sketch")
    f["name"] = name
    f["params"].update(plane={"kind": "std", "name": plane}, sketch=s.to_dict())
    doc.add_feature(f)
    return f["id"]


def points(s, pts):
    for x, y in pts:
        s.add_point_entity(s.add_point(x, y))


def hole(doc, name, sk, **params):
    f = doc.make_feature("hole")
    f["name"] = name
    f["params"].update(sketch=sk, end="through", style="simple", **params)
    doc.add_feature(f)


def build_studio():
    studio = Document()
    studio.name = "Bolted bracket parts"
    dx = BRACKET_DX

    # plate: 80 x 50 x 6, extruded up from the Top plane
    sk = sketch(studio, "Plate", "Top", lambda s: rect(s, -10, -25, 80, 50))
    sr = studio.state.sketches[sk]
    ex = studio.make_feature("extrude")
    ex["params"].update(regions=[F.region_ref(sr, sk, sr.regions[0])], op="new", depth=PLATE_T)
    studio.add_feature(ex)
    plate = list(studio.state.parts)[-1]

    # bracket: L profile (base 60 long, upright 6 thick and 30 high) on the Front plane, 40 wide (y -20..20)
    def profile(s):
        pts = [s.add_point(dx + x, z) for x, z in ((0, 0), (60, 0), (60, BASE_T), (6, BASE_T), (6, 30), (0, 30))]
        for a, b in zip(pts, pts[1:] + pts[:1]):
            s.add_line(a, b)
    sk = sketch(studio, "Bracket profile", "Front", profile)
    sr = studio.state.sketches[sk]
    ex = studio.make_feature("extrude")
    ex["params"].update(regions=[F.region_ref(sr, sk, sr.regions[0])], op="new", end="symmetric", depth=40.0)
    studio.add_feature(ex)
    studio.hidden_features.add(sk)
    bracket = list(studio.state.parts)[-1]

    # eight M4 clearance holes, four in each part (one through hole cuts every part in its column)
    sk = sketch(studio, "M4 hole positions", "Top",
                lambda s: points(s, [(x + off, y) for off in (0, dx) for x in M4_XS for y in HOLE_YS]))
    hole(studio, "M4 clearance holes", sk, standard="clearance", size="M4", fit="normal")
    studio.hidden_features.add(sk)
    # two M3 tapped holes through the bracket base
    sk = sketch(studio, "M3 hole positions", "Top", lambda s: points(s, [(dx + M3_X, y) for y in HOLE_YS]))
    hole(studio, "M3 tapped holes", sk, standard="tapped", size="M3")
    studio.hidden_features.add(sk)
    # a 2.5 mm hole through the upright, drilled along x on the Right plane (u = y, v = z)
    sk = sketch(studio, "Upright hole position", "Right", lambda s: points(s, [(0, UPRIGHT_HOLE_Z)]))
    hole(studio, "2.5 mm hole", sk, standard="custom", diameter=UPRIGHT_HOLE)
    studio.hidden_features.add(sk)

    for pid, name, col in ((plate, "Plate", (0.62, 0.66, 0.72)), (bracket, "Bracket", (0.36, 0.56, 0.86))):
        studio.set_part_override(pid, name=name, color=list(col))
    return studio, plate, bracket


def build(out_dir=HERE):
    """Write bolted_bracket_parts.fshape and bolted_bracket.fasm into `out_dir`; returns the AssemblyDoc."""
    studio, plate, bracket = build_studio()
    assert not studio.errors, studio.errors
    studio.save(os.path.join(out_dir, "bolted_bracket_parts.fshape"))

    asm = AssemblyDoc()
    asm.name = "Bolted bracket"
    asm.resolver = lambda src: studio.state.parts[src["part"]]
    # a path relative to the working directory (run from examples/), so the committed file has no machine path
    path = os.path.relpath(studio.path)
    src = lambda pid: {"kind": "studio", "uid": studio.uid, "part": pid, "path": path, "studio_name": studio.name}
    i_plate, i_bracket = asm.insert(src(plate)), asm.insert(src(bracket))

    def rim(iid, cx, cy, z):
        """The M4 hole's circular edge at height z, centred at (cx, cy), in the part's own frame."""
        for i, e in enumerate(K.edges(asm.parts[iid].shape)):
            info = K.edge_info(e)
            if (abs(info.get("r", 0) - M4_HOLE / 2) < 1e-6 and abs(info["center"][2] - z) < 1e-6
                    and abs(info["center"][0] - cx) < 1e-6 and abs(info["center"][1] - cy) < 1e-6):
                return asm.connector(iid, "edge", i)
        raise RuntimeError("hole rim not found")

    # the bracket's underside rim on the plate's top rim of the same hole; the plate (first instance) stays put
    m = asm.make_mate("fastened")
    m["a"] = rim(i_plate, M4_XS[0], HOLE_YS[0], PLATE_T)
    m["b"] = rim(i_bracket, BRACKET_DX + M4_XS[0], HOLE_YS[0], 0.0)
    asm.add_mate(m)
    asm.save(os.path.join(out_dir, "bolted_bracket.fasm"))
    return asm


def main(out_dir=HERE):
    asm = build(out_dir)
    print("wrote bolted_bracket_parts.fshape and bolted_bracket.fasm in", out_dir, "-", asm.dof, "degrees of freedom left")


if __name__ == "__main__":
    main()
