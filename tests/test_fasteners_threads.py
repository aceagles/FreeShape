"""I1.7: the optional real thread (`model_thread`) on ISO 4014, 4017, 4762 and 4032."""
import numpy as np
import pytest

from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib

from freeshape import kernel as K
from freeshape import pyfeature
from freeshape.pyfeature import registry  # noqa: F401  (loads the bundled plugins)
from freeshape.addons.fasteners import hex_bolt

pytestmark = pytest.mark.slow


def part(tid):
    cls = pyfeature.standard_part(f"sp:fasteners/{tid}")
    assert cls is not None, pyfeature.problems()
    return cls()


def tight_bbox(shape):
    """The exact box. K.bbox can read high on OCP 8.0 for B-spline and helical geometry, so the plain and the
    threaded solid are both compared on the tight box, which is the same on both OCP versions."""
    b = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, b, False, False)
    return np.array(b.CornerMin().Coord()), np.array(b.CornerMax().Coord())


def above(shape, z):
    """The part of `shape` at or above height z (the head of a screw)."""
    return K.common(shape, K.cylinder((0, 0, z), (0, 0, 1), 100.0, 100.0))


SCREWS = {"4762": ("iso4762", {"size": "M6", "length": 20}),
          "4014": ("iso4014", {"size": "M6", "length": 60}),
          "4017": ("iso4017", {"size": "M6", "length": 20})}


@pytest.fixture(scope="module", params=sorted(SCREWS))
def screw(request):
    tid, params = SCREWS[request.param]
    p = part(tid)
    return p, params, p.build(params), p.build({**params, "model_thread": True})


@pytest.fixture(scope="module")
def nut():
    p = part("iso4032")
    return p, p.build({"size": "M6"}), p.build({"size": "M6", "model_thread": True})


def test_threaded_screw(screw):
    p, params, plain, threaded = screw
    assert K.is_valid(threaded)
    assert K.volume(threaded) < K.volume(plain)
    (plo, phi), (tlo, thi) = tight_bbox(plain), tight_bbox(threaded)
    assert (tlo[2], thi[2]) == pytest.approx((plo[2], phi[2]), abs=0.01)      # same overall length
    assert tlo[:2] == pytest.approx(plo[:2], abs=0.01)                         # crests sit on the shank
    assert thi[:2] == pytest.approx(phi[:2], abs=0.01)
    # the head is untouched
    assert K.volume(above(threaded, 0.0)) == pytest.approx(K.volume(above(plain, 0.0)), rel=1e-6)


def test_thread_ranges():
    # M6: r = 0.25 (root fillet); b = 18 for ISO 4014 up to length 125
    assert hex_bolt.threaded_length("ISO4014", "M6", 60.0) == pytest.approx(18.0)     # z -60 .. -42
    assert hex_bolt.threaded_length("ISO4017", "M6", 20.0) == pytest.approx(19.75)    # z -20 .. -0.25
    assert hex_bolt.threaded_length("ISO4014", "M6", 16.0) == pytest.approx(15.75)    # shorter than b: all of it


def test_threaded_nut(nut):
    _, plain, threaded = nut
    assert K.is_valid(threaded)
    assert K.volume(threaded) < K.volume(plain)
    (plo, phi), (tlo, thi) = tight_bbox(plain), tight_bbox(threaded)
    assert np.allclose(np.r_[tlo, thi], np.r_[plo, phi], atol=0.01)


@pytest.mark.parametrize("tid,params", (("iso4762", {"size": "M6", "length": 20}),
                                        ("iso4014", {"size": "M6", "length": 60}),
                                        ("iso4017", {"size": "M6", "length": 20})))
def test_screw_connectors_unchanged(tid, params):
    p = part(tid)
    assert p.connectors(params) == p.connectors({**params, "model_thread": True})


def test_nut_connectors_unchanged():
    p = part("iso4032")
    assert p.connectors({"size": "M6"}) == p.connectors({"size": "M6", "model_thread": True})


@pytest.mark.parametrize("tid", ("iso4014", "iso4017", "iso4762", "iso4032"))
def test_parameter(tid):
    p = part(tid)
    d = p.default_params()
    assert d["model_thread"] is False
    assert p.validate({k: v for k, v in d.items() if k != "model_thread"})["model_thread"] is False
    assert p.validate({**d, "model_thread": True})["model_thread"] is True
    for bad in (1, "yes", None):
        with pytest.raises(K.KernelError, match="model_thread"):
            p.validate({**d, "model_thread": bad})


@pytest.mark.parametrize("tid", ("iso7049", "iso7089"))
def test_no_parameter_on_tapping_screw_and_washer(tid):
    assert "model_thread" not in part(tid).default_params()


def test_size_without_pitch_stays_plain():
    """M3.5 is a size of ISO 4017 that K.ISO_PITCH has no pitch for: the switch is accepted and ignored."""
    assert "M3.5" not in K.ISO_PITCH
    p = part("iso4017")
    params = {"size": "M3.5", "length": p.lengths("M3.5")[0]}
    plain, on = p.build(params), p.build({**params, "model_thread": True})
    assert p.validate({**params, "model_thread": True})["model_thread"] is True
    assert K.volume(on) == pytest.approx(K.volume(plain), rel=1e-12)
