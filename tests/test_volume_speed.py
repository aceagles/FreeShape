"""Finding F2: kernel.volume and kernel.mass_props must not hang on gear solids.

OpenCascade's adaptive volume integrator measures every face about one reference point, the mean of the
shape's vertices. A plane face through that point contributes nothing, and the integrator then refines without
end; a bevel gear without a bore has 30 such faces and took minutes. kernel._gk_shape moves the reference
point off those planes.

Times measured with the fix on a heavily loaded box (OCP 8.0, 2026-11-13), default gears (15 teeth, module 1,
face width 5): bevel volume 0.05 s and mass properties 0.4 s (both over 60 s before); spur mass properties
0.2 s (4.5 s before); helical 0.1 s and 0.7 s (unchanged). The bound is 30 s per call. Each call runs in a
forked child that is killed at the bound, so a regression fails the test instead of hanging the suite.

Reference volumes are the adaptive integrator converged to ten digits (tolerances 1e-8 and 1e-10 agree to
1e-10 relative) on the same solids.
"""
import math
import multiprocessing

import numpy as np
import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

import freeshape.kernel as K
from freeshape.plugins.gears.bevel_geometry import bevel_gear
from freeshape.plugins.gears.spur_geometry import spur_gear

BOUND = 30.0                                  # seconds per call
BEVEL_VOLUME = 432.764387014                  # bevel_gear(15, 1.0, 5.0)
BEVEL_BORE_VOLUME = 397.421469662             # bevel_gear(15, 1.0, 5.0, bore=3.0)
SPUR_VOLUME = 851.344225309                   # spur_gear(15, 1.0, 5.0)
HELICAL_VOLUME = 851.344139671                # spur_gear(15, 1.0, 5.0, helix_angle=20.0)


def bounded(fn, shape):
    """fn(shape), computed in a forked child killed after BOUND seconds."""
    recv, send = multiprocessing.Pipe(False)

    def child():
        send.send(fn(shape))

    proc = multiprocessing.get_context("fork").Process(target=child)
    proc.start()
    try:
        if not recv.poll(BOUND):
            pytest.fail(f"{fn.__name__} did not return within {BOUND:.0f} s")
        return recv.recv()
    finally:
        proc.kill()
        proc.join()


def _mass_props(shape):
    m = K.mass_props(shape)
    return m["volume"], m["area"], [float(x) for x in m["centroid"]]


GEARS = [
    ("bevel", lambda: bevel_gear(15, 1.0, 5.0), BEVEL_VOLUME, 1e-5),
    ("bevel_bore", lambda: bevel_gear(15, 1.0, 5.0, bore=3.0), BEVEL_BORE_VOLUME, 1e-5),
    ("spur", lambda: spur_gear(15, 1.0, 5.0), SPUR_VOLUME, 1e-7),
    ("helical", lambda: spur_gear(15, 1.0, 5.0, helix_angle=20.0), HELICAL_VOLUME, 1e-5),
]


@pytest.mark.parametrize("build, reference, rel", [g[1:] for g in GEARS], ids=[g[0] for g in GEARS])
def test_default_gear_volume_and_mass_props_are_quick_and_right(build, reference, rel):
    solid = build().solid
    assert bounded(K.volume, solid) == pytest.approx(reference, rel=rel)
    volume, area, centroid = bounded(_mass_props, solid)
    assert volume == pytest.approx(reference, rel=rel)
    assert area > 0
    assert centroid[0] == pytest.approx(0.0, abs=1e-4) and centroid[1] == pytest.approx(0.0, abs=1e-4)
    assert 0 < centroid[2] < 5.0


def test_bevel_centre_of_mass_matches_converged_value():
    _, _, centroid = bounded(_mass_props, bevel_gear(15, 1.0, 5.0).solid)
    assert centroid[2] == pytest.approx(1.730769, abs=1e-5)


def test_bevel_gear_is_integrated_about_a_moved_reference_point():
    """The default bevel gear is the shape that hung: its reference point must be moved."""
    solid = bevel_gear(15, 1.0, 5.0).solid
    assert K._gk_shape(solid) is not solid
    assert len(K.faces(K._gk_shape(solid))) == len(K.faces(solid))


def test_shapes_with_no_plane_through_the_reference_point_are_left_alone():
    box = BRepPrimAPI_MakeBox(gp_Pnt(1, 2, 3), 10, 20, 30).Shape()
    assert K._gk_shape(box) is box
    assert K.volume(box) == pytest.approx(6000.0, abs=1e-9)
    bored = bevel_gear(15, 1.0, 5.0, bore=3.0).solid
    assert K._gk_shape(bored) is bored


def test_half_cylinder_with_every_vertex_on_its_flat_face():
    """All four vertices of a half cylinder lie on its flat face, so that plane holds the reference point.
    Volume and centre of mass are known exactly: pi r^2 h / 2, and 4 r / (3 pi) from the flat face."""
    r, h = 4.0, 6.0
    cyl = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1), gp_Dir(1, 0, 0)), r, h).Shape()
    half = K.cut(cyl, BRepPrimAPI_MakeBox(gp_Pnt(0, -10, -1), 10, 20, 8).Shape())     # keeps x <= 0, drops the seam
    assert K._gk_shape(half) is not half
    assert bounded(K.volume, half) == pytest.approx(math.pi * r * r * h / 2, rel=1e-6)
    volume, _, centroid = bounded(_mass_props, half)
    assert volume == pytest.approx(math.pi * r * r * h / 2, rel=1e-6)
    assert np.allclose(centroid, [-4 * r / (3 * math.pi), 0.0, h / 2], atol=1e-5)
