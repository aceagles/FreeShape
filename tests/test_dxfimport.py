"""freeshape/dxfimport.py: read a DXF file into primitives for sketchimport."""
import io
import math
import os
import sys

import numpy as np
import pytest

ezdxf = pytest.importorskip("ezdxf", reason="ezdxf is not installed")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from OCP.BRepGProp import BRepGProp  # noqa: E402
from OCP.GProp import GProp_GProps  # noqa: E402
from freeshape import dxfexport as DX  # noqa: E402
from freeshape import dxfimport as DI  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import sketch as S  # noqa: E402
from freeshape import sketchimport as SI  # noqa: E402

PLANE = K.std_plane("Top")


def new(insunits=4, measurement=None):
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = insunits
    if measurement is None:
        if "$MEASUREMENT" in doc.header:
            del doc.header["$MEASUREMENT"]
    else:
        doc.header["$MEASUREMENT"] = measurement
    return doc


def text(doc):
    s = io.StringIO()
    doc.write(s)
    return s.getvalue()


def rd(doc, **kw):
    return DI.read(text(doc), **kw)


def area(face):
    g = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, g)
    return g.Mass()


def of(prims, t):
    return [p for p in prims if p["type"] == t]


def test_closed_two_point_polyline_with_bulge_one_is_a_circle_of_the_chord_diameter():
    doc = new()
    doc.modelspace().add_lwpolyline([(0, 0, 1), (10, 0, 1)], format="xyb", close=True)
    prims, rep = rd(doc)
    arcs = of(prims, "arc")
    assert len(arcs) == 2 and len(prims) == 2
    for a in arcs:
        assert a["c"] == pytest.approx((5, 0))
        assert math.dist(a["p1"], a["c"]) == pytest.approx(5)
        assert math.dist(a["p2"], a["c"]) == pytest.approx(5)
    # counter-clockwise: the first arc runs from (0,0) round the bottom to (10,0)
    first = arcs[0]
    assert first["p1"] == pytest.approx((0, 0)) and first["p2"] == pytest.approx((10, 0))
    sk = S.Sketch()
    SI.insert(sk, prims, use_file_origin=True)
    regs = K.sketch_regions(PLANE, sk.curves())
    assert len(regs) == 1 and area(regs[0]) == pytest.approx(math.pi * 25, rel=1e-6)


def test_negative_bulge_runs_clockwise_and_open_polyline_has_no_closing_segment():
    doc = new()
    doc.modelspace().add_lwpolyline([(0, 0, -1), (10, 0, 0), (10, 10, 0)], format="xyb")
    prims, _ = rd(doc)
    arc = of(prims, "arc")[0]
    assert arc["c"] == pytest.approx((5, 0))
    assert arc["p1"] == pytest.approx((10, 0)) and arc["p2"] == pytest.approx((0, 0))   # ccw end to start
    assert len(of(prims, "line")) == 1 and len(prims) == 2


def test_2d_polyline_with_bulge_and_closed_flag():
    doc = new()
    pl = doc.modelspace().add_polyline2d([(0, 0), (10, 0), (10, 10), (0, 10)], close=True)
    pl.vertices[0].dxf.bulge = 0.0
    prims, _ = rd(doc)
    assert len(of(prims, "line")) == 4


@pytest.mark.parametrize("insunits, measurement, factor, source", [
    (1, None, 25.4, "INSUNITS"),
    (0, 0, 25.4, "MEASUREMENT"),
    (0, 1, 1.0, "MEASUREMENT"),
    (0, None, 1.0, "argument"),
    (4, 0, 1.0, "INSUNITS"),
])
def test_units_from_insunits_then_measurement_then_the_argument(insunits, measurement, factor, source):
    doc = new(insunits, measurement)
    doc.modelspace().add_line((0, 0), (10, 0))
    prims, rep = rd(doc)
    assert prims[0]["p2"][0] == pytest.approx(10 * factor, rel=1e-6)
    assert rep["units"]["source"] == source


def test_units_argument_is_used_only_when_the_file_has_no_units():
    doc = new(0, None)
    doc.modelspace().add_line((0, 0), (10, 0))
    assert rd(doc, units="in")[0][0]["p2"][0] == pytest.approx(254, rel=1e-6)
    assert rd(doc, units="cm")[0][0]["p2"][0] == pytest.approx(100)
    assert rd(doc, units=1)[0][0]["p2"][0] == pytest.approx(254, rel=1e-6)
    with pytest.raises(ValueError):
        rd(doc, units="furlong")
    doc = new(4, None)
    doc.modelspace().add_line((0, 0), (10, 0))
    assert rd(doc, units="in")[0][0]["p2"][0] == pytest.approx(10)


def test_insert_with_scale_two_and_rotation_ninety_gives_a_two_by_two_square_in_place():
    doc = new()
    blk = doc.blocks.new("SQ")
    blk.add_lwpolyline([(0, 0), (1, 0), (1, 1), (0, 1)], close=True)
    doc.modelspace().add_blockref("SQ", (10, 5), dxfattribs={"xscale": 2, "yscale": 2, "rotation": 90})
    prims, rep = rd(doc)
    lines = of(prims, "line")
    assert len(lines) == 4
    pts = {(round(p[0], 6), round(p[1], 6)) for ln in lines for p in (ln["p1"], ln["p2"])}
    assert pts == {(10, 5), (10, 7), (8, 7), (8, 5)}
    sk = S.Sketch()
    SI.insert(sk, prims, use_file_origin=True)
    regs = K.sketch_regions(PLANE, sk.curves())
    assert len(regs) == 1 and area(regs[0]) == pytest.approx(4)


def test_nested_insert_is_exploded():
    doc = new()
    inner = doc.blocks.new("IN")
    inner.add_line((0, 0), (1, 0))
    outer = doc.blocks.new("OUT")
    outer.add_blockref("IN", (0, 3))
    doc.modelspace().add_blockref("OUT", (100, 0))
    prims, _ = rd(doc)
    assert len(prims) == 1 and prims[0]["p1"] == pytest.approx((100, 3))


def test_arc_with_extrusion_minus_z_is_mirrored():
    doc = new()
    msp = doc.modelspace()
    msp.add_arc((10, 0), 5, 0, 90)                                   # OCS = WCS
    msp.add_arc((10, 0), 5, 0, 90, dxfattribs={"extrusion": (0, 0, -1)})
    a, m = of(rd(doc)[0], "arc")
    assert a["c"] == pytest.approx((10, 0))
    assert a["p1"] == pytest.approx((15, 0)) and a["p2"] == pytest.approx((10, 5))
    # OCS x is negated: the centre goes to (-10, 0) and the quarter lands in the second quadrant,
    # still counter-clockwise from p1 to p2 in the sketch
    assert m["c"] == pytest.approx((-10, 0))
    assert m["p1"] == pytest.approx((-10, 5)) and m["p2"] == pytest.approx((-15, 0))


def test_entities_not_parallel_to_xy_are_skipped_and_counted():
    doc = new()
    msp = doc.modelspace()
    msp.add_circle((0, 0), 5, dxfattribs={"extrusion": (1, 0, 0)})
    msp.add_line((0, 0, 0), (1, 0, 5))
    msp.add_line((0, 0, 3), (1, 0, 3))
    prims, rep = rd(doc)
    assert [p["type"] for p in prims] == ["line"]
    assert rep["skipped"] == {"not parallel to XY": 2}


def test_full_ellipse_is_an_ellipse_and_partial_is_beziers():
    doc = new()
    msp = doc.modelspace()
    msp.add_ellipse((5, 5), major_axis=(10, 0), ratio=0.5)
    msp.add_ellipse((0, 0), major_axis=(0, 4), ratio=0.5, start_param=0, end_param=math.pi)
    prims, _ = rd(doc)
    e = of(prims, "ellipse")
    assert len(e) == 1 and e[0]["c"] == pytest.approx((5, 5)) and e[0]["a"] == pytest.approx((15, 5))
    assert e[0]["r"] == pytest.approx(5)
    assert of(prims, "bezier") and all(len(b["pts"]) == 4 for b in of(prims, "bezier"))


def _dist_to_polyline(samples, poly):
    poly = np.asarray(poly, float)
    return SI._seg_dist(np.asarray(samples, float), poly[:-1], poly[1:])


def test_spline_with_fit_points_goes_through_them():
    fit = [(0, 0), (10, 8), (20, -4), (30, 6), (40, 0)]
    doc = new()
    doc.modelspace().add_spline(fit_points=fit)
    prims, rep = rd(doc)
    assert [p["type"] for p in prims] == ["fitspline"]
    pts = prims[0]["pts"]
    for q in fit:
        assert min(math.dist(q, p) for p in pts) < 1e-6
    sk = S.Sketch()
    SI.insert(sk, prims, use_file_origin=True)
    (eid,) = sk.entities
    poly = sk.polyline(eid, 64)
    assert _dist_to_polyline(fit, poly).max() < 1e-3        # polyline sampling sag
    # and it follows the ezdxf curve closely
    ref = [(v.x, v.y) for v in ezdxf.path.make_path(doc.modelspace()[0]).flattening(0.01)]
    assert _dist_to_polyline(ref, poly).max() < DI.TOL_MM * 1.5
    assert rep["spline_checks"]


def test_spline_with_control_points_follows_the_ezdxf_path():
    ctl = [(0, 0), (10, 20), (20, -10), (35, 15), (50, 0), (60, 12)]
    doc = new()
    sp = doc.modelspace().add_open_spline(ctl, degree=3)
    prims, _ = rd(doc)
    assert all(p["type"] in ("bezier", "line") for p in prims) and prims
    sk = S.Sketch()
    SI.insert(sk, prims, use_file_origin=True)
    path = ezdxf.path.make_path(sp)
    ref = [(v.x, v.y) for v in path.flattening(0.001, segments=8)]
    samples = [ref[i] for i in np.linspace(0, len(ref) - 1, 32).astype(int)]
    polys = [sk.polyline(eid, 64) for eid, e in sk.entities.items() if e["type"] == "spline"]
    lines = [[sk.P(e["p1"]), sk.P(e["p2"])] for e in sk.entities.values() if e["type"] == "line"]
    d = np.min([_dist_to_polyline(samples, p) for p in polys + lines], axis=0)
    assert d.max() <= 0.01


def test_closed_fit_spline():
    fit = [(10, 0), (0, 10), (-10, 0), (0, -10)]
    doc = new()
    doc.modelspace().add_spline(fit_points=fit).closed = True
    prims, _ = rd(doc)
    assert prims[0]["type"] == "fitspline" and prims[0]["closed"] is True


def test_hatch_and_mtext_are_skipped_and_counted_and_every_primitive_has_its_layer():
    doc = new()
    doc.layers.add("CUT")
    doc.layers.add("ENGRAVE")
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "CUT"})
    msp.add_circle((5, 5), 2, dxfattribs={"layer": "ENGRAVE"})
    msp.add_mtext("hello", dxfattribs={"layer": "CUT"})
    msp.add_text("hi")
    h = msp.add_hatch()
    h.paths.add_polyline_path([(0, 0), (4, 0), (4, 4)], is_closed=True)
    prims, rep = rd(doc)
    assert [(p["type"], p["layer"]) for p in prims] == [("line", "CUT"), ("circle", "ENGRAVE")]
    assert rep["skipped"] == {"MTEXT": 1, "TEXT": 1, "HATCH": 1}
    assert rep["layers"] == ["CUT", "ENGRAVE"] and rep["prims"] == {"line": 1, "circle": 1}


def test_points_are_read_only_when_asked():
    doc = new()
    doc.modelspace().add_point((3, 4))
    prims, rep = rd(doc)
    assert prims == [] and rep["skipped"] == {"POINT": 1}
    prims, rep = rd(doc, points=True)
    assert prims == [{"layer": "0", "type": "point", "p": (3.0, 4.0)}] and rep["skipped"] == {}


def test_paper_space_and_frozen_layers_are_skipped():
    doc = new()
    doc.layers.add("OLD", dxfattribs={"flags": 1})
    doc.modelspace().add_line((0, 0), (1, 0))
    doc.modelspace().add_line((0, 0), (2, 0), dxfattribs={"layer": "OLD"})
    doc.layout().add_line((0, 0), (3, 0))
    prims, rep = rd(doc)
    assert len(prims) == 1
    assert rep["skipped"] == {"frozen layer": 1, "paper space": 1}


def test_reads_a_file_by_path(tmp_path):
    doc = new()
    doc.modelspace().add_line((0, 0), (7, 0))
    p = tmp_path / "a.dxf"
    doc.saveas(p)
    prims, rep = DI.read(str(p))
    assert prims[0]["p2"] == pytest.approx((7, 0)) and rep["recovered"] is False
    with pytest.raises(FileNotFoundError):
        DI.read(str(tmp_path / "missing.dxf"))


def test_damaged_tag_stream_is_recovered():
    doc = new()
    doc.modelspace().add_line((0, 0), (7, 0))
    good = text(doc)
    damaged = good[:good.rindex("  0\nENDSEC")]          # cut off before the end of the ENTITIES section
    with pytest.raises(ezdxf.DXFError):
        ezdxf.read(io.StringIO(damaged))
    prims, rep = DI.read(damaged)
    assert rep["recovered"] is True
    assert prims and prims[0]["p2"] == pytest.approx((7, 0))


def test_unreadable_file_raises_value_error():
    with pytest.raises(ValueError, match="Not a readable DXF file"):
        DI.read("this is\nnot\na dxf file\nat all\n")


def test_export_then_import_gives_regions_of_the_same_areas(tmp_path):
    sk = S.Sketch()
    q = [sk.add_point(*p) for p in [(0, 0), (40, 0), (40, 25), (0, 25)]]
    for i in range(4):
        sk.add_line(q[i], q[(i + 1) % 4])
    sk.add_circle(sk.add_point(70, 10), 8)
    c = sk.add_point(20, -40)
    p1, p2 = sk.add_point(35, -40), sk.add_point(5, -40)
    sk.add_arc(c, p1, p2)
    sk.add_line(p2, p1)
    path = tmp_path / "out.dxf"
    DX.write(DX.sketch_geometry(sk), str(path))
    prims, rep = DI.read(str(path))
    sk2 = S.Sketch()
    SI.insert(sk2, prims, use_file_origin=True)
    want = sorted(area(f) for f in K.sketch_regions(PLANE, sk.curves()))
    got = sorted(area(f) for f in K.sketch_regions(PLANE, sk2.curves()))
    assert len(want) == 3 and len(got) == 3
    for w, g in zip(want, got):
        assert g == pytest.approx(w, rel=1e-3)
