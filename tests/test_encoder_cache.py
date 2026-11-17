"""The Encoder's caches hold the whole model (I1.2): a model with more parts than the old fixed limit of 512 is
written and meshed once, and the next `outputs` reads every part back from the caches."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: E402
from OCP.gp import gp_Trsf, gp_Vec  # noqa: E402
from OCP.TopLoc import TopLoc_Location  # noqa: E402

from freeshape.engine import InProcessEngine  # noqa: E402
from freeshape.engine import encode  # noqa: E402
from freeshape.engine.encode import Encoder  # noqa: E402

BASES = 600          # above the old limit of 512


def _box(k):
    return BRepPrimAPI_MakeBox(1.0 + 0.01 * k, 2.0, 3.0).Shape()


def _placed(shape, k):
    t = gp_Trsf()
    t.SetTranslation(gp_Vec(10.0 * k, 0.0, 0.0))
    return shape.Located(TopLoc_Location(t))


def _counting(monkeypatch):
    calls = {"brep": 0, "mesh": 0}
    brep_text, mesh_out = encode.brep_text, encode.mesh_out

    def count_brep(shape):
        calls["brep"] += 1
        return brep_text(shape)

    def count_mesh(shape, bases=None):
        calls["mesh"] += 1
        return mesh_out(shape, bases)
    monkeypatch.setattr(encode, "brep_text", count_brep)
    monkeypatch.setattr(encode, "mesh_out", count_mesh)
    return calls


def test_outputs_twice_on_a_model_above_the_old_limit_encodes_each_base_once(monkeypatch):
    calls = _counting(monkeypatch)
    with InProcessEngine(placed=True) as e:
        tab = e.new_tab("studio")
        studio = e._tabs[tab]
        studio.sync()
        st = studio.doc.state
        for k in range(BASES):
            st.new_part(_placed(_box(k), k), "F1")              # a placed copy: sent as its base plus `loc`
            st.new_part(_box(BASES + k), "F1")                   # one at the origin: sent as it sits
        first = studio.outputs()
        assert len(first.parts) == 2 * BASES
        assert len({p.base for p in first.parts if p.base}) == BASES
        assert calls == {"brep": 2 * BASES, "mesh": 2 * BASES}
        second = studio.outputs()
        assert calls == {"brep": 2 * BASES, "mesh": 2 * BASES}       # nothing written or meshed again
        assert [p.hash for p in second.parts] == [p.hash for p in first.parts]


def test_the_caches_keep_the_most_recently_used_entries_up_to_the_limit():
    enc = Encoder(meshes=False)
    shapes = [_box(k) for k in range(600)]
    for s in shapes:
        enc.brep(s)
    assert enc.limit == 512 and len(enc._brep) == 512
    assert id(shapes[0]) not in enc._brep and id(shapes[-1]) in enc._brep
    enc.brep(shapes[100])                     # read again: now the newest, so the next eviction passes it by
    enc.brep(_box(1000))
    assert id(shapes[100]) in enc._brep and id(shapes[88]) not in enc._brep and id(shapes[89]) in enc._brep


def test_fit_sizes_the_caches_from_the_model_and_trims_when_it_shrinks():
    enc = Encoder(meshes=False)
    enc.fit(1000)
    assert enc.limit == 2000
    shapes = [_box(k) for k in range(1500)]
    for s in shapes:
        enc.brep(s)
    assert len(enc._brep) == 1500
    enc.fit(10)
    assert enc.limit == 512 and len(enc._brep) == 512
    assert id(shapes[-1]) in enc._brep


def test_a_synthetic_source_can_name_its_number_of_distinct_screws(tmp_path):
    from freeshape import kernel as K
    from freeshape.import_timing import write_synthetic
    items = K._import_step_with_colors(write_synthetic(str(tmp_path / "s.step"), 30, 12))
    assert len(items) == 33                                     # 3 modules of a board and 10 screws
    bases = []
    for s, *_ in items:
        u = s.Located(TopLoc_Location())
        if not any(u.IsSame(b) for b in bases):
            bases.append(u)
    assert len(bases) == 12 + 2                                 # every screw variant, and the 2 boards
