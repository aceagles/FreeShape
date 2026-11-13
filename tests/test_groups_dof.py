"""Groups (I4.2): a group counts as one body in DOF reporting and mate placement."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_assembly import lib, make, src, mate  # noqa: F401  (lib is a fixture)
from tests.test_groups import _top_bottom, _rel, _top_z


def _scene(lib):
    a = make(lib)
    ip, i1, i2 = a.insert(src("plate")), a.insert(src("block")), a.insert(src("block"))
    a.add_group([i1, i2])
    return a, ip, i1, i2


def test_two_free_grouped_blocks_report_six_dof(lib):
    a, ip, i1, i2 = _scene(lib)
    a.solve()
    assert a.dof == 6
    assert a.instance_dof(i1) == 6 and a.instance_dof(i2) == 6


def test_fastening_one_member_to_a_fixed_plate_reports_zero(lib):
    a, ip, i1, i2 = _scene(lib)
    top, bot = _top_bottom(a, ip, i1)
    mate(a, "fastened", top, bot)
    assert not a.errors
    assert a.dof == 0
    assert a.instance_dof(i1) == 0 and a.instance_dof(i2) == 0


def test_planar_mate_on_one_member_leaves_three_dof_for_both(lib):
    a, ip, i1, i2 = _scene(lib)
    top, bot = _top_bottom(a, ip, i1)
    mate(a, "planar", top, bot)
    assert a.dof == 3
    assert a.instance_dof(i1) == 3 and a.instance_dof(i2) == 3


def test_add_mate_moves_the_whole_far_away_group(lib):
    a, ip, i1, i2 = _scene(lib)
    for iid, dx in ((i1, 500.0), (i2, 560.0)):
        T = np.array(a.instance(iid)["T"], float)
        T[:3, 3] += [dx, 300.0, 80.0]
        a.instance(iid)["T"] = T.tolist()
    rel0 = _rel(a, i1, i2)
    top, bot = _top_bottom(a, ip, i1)
    m = mate(a, "planar", top, bot)
    assert not a.errors
    assert abs(_top_z(a, i1) - 10) < 1e-6
    assert np.allclose(_rel(a, i1, i2), rel0, atol=1e-9)
    assert m["id"] not in a.errors
