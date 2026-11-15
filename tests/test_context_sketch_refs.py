"""Projecting and constraining to a Context edge, with twin neighbours (I6.3, ADR 0004, ADR 0003).

The rig is a Plate studio holding a Context of the Plate instance, and an Assembly of the Plate and three
neighbours: wheel A and wheel B (two instances of one cylinder part) and a Cap C. The sketch in the Plate studio
uses wheel B's rim (the Use tool of the sketcher), makes a circle concentric to it and a line tangent to it.
Windows run in the same mode as the other test_context_* files (not engine mode)."""
import copy
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from conftest import add, block_doc, region_refs, sketch_feature, try_add  # noqa: E402
from freeshape import assembly as A  # noqa: E402
from freeshape import doc_folder as DF  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import links as L  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch, ORIGIN  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402
from test_sketch_inference import _sketcher  # noqa: E402

TOP = {"kind": "std", "name": "Top"}
R_WHEEL = 8.0
A_AT, B_AT = (-25.0, 5.0, 20.0), (25.0, 5.0, 40.0)     # wheel placements; a wheel is 4 high, its rim at z + 4


class _Viewport:
    def __init__(self, state):
        self.part_shapes = {pid: p.shape for pid, p in state.parts.items()}


def _wheel_doc():
    doc = Document()
    sid = sketch_feature(doc, lambda s: s.add_circle(s.add_point(0, 0), R_WHEEL))
    add(doc, "extrude", regions=region_refs(doc, sid), depth=4.0, op="new")
    return doc


def _folder(tmp_path):
    """Returns (folder, plate tab, asm tab, context id, {"A", "B", "C"} -> instance id)."""
    folder = DF.FolderDocument.create(str(tmp_path / "Doc"))
    plate = folder.add_tab("studio", "Plate")
    plate.doc.__dict__.update(block_doc(40, 20, 10).__dict__)
    wheel = folder.add_tab("studio", "Wheel")
    wheel.doc.__dict__.update(_wheel_doc().__dict__)
    cap = folder.add_tab("studio", "Cap")
    cap.doc.__dict__.update(block_doc(6, 6, 4).__dict__)
    for t in (plate, wheel, cap):
        folder._attach(t)
    asm = folder.add_tab("assembly", "Asm")
    a = asm.doc

    def source(tab):
        return L.studio_source(tab, f"{tab.doc.features[1]['id']}/1")

    ip = a.insert(source(plate), T=np.eye(4), name="Plate")
    iids = {"A": a.insert(source(wheel), T=A.translation(A_AT), name="Wheel A"),
            "B": a.insert(source(wheel), T=A.translation(B_AT), name="Wheel B"),
            "C": a.insert(source(cap), T=A.translation((0, 0, 60)), name="Cap C")}
    a.refresh(solve=False, notify=False)
    key = plate.doc.store_context(A.capture_context(a, ip))
    ctx = add(plate.doc, "context", assembly=a.uid, instance=ip, capture=key, ids="instance")
    return folder, plate, asm, ctx["id"], iids


def _rim_index(doc, pid):
    """The index of the circular edge at the top of the wheel body `pid` (the seam is skipped)."""
    shape = doc.state.parts[pid].shape
    seams = K.seam_edge_indices(shape)
    hits = [i for i, e in enumerate(K.edges(shape)) if i not in seams and "r" in K.edge_info(e)]
    return max(hits, key=lambda i: K.edge_info(K.edges(shape)[i])["c"][2])


def _centre(sk, eid):
    return sk.P(sk.entities[eid]["c"])


class Rig:
    """The twin-neighbour rig with the sketch built: `rim` is the projected circle, `conc` the concentric
    circle, `line` the tangent line (ids in the sketch feature `sid`)."""

    def __init__(self, tmp_path):
        self.folder, self.plate, self.asm, self.cid, self.iids = _folder(tmp_path)
        self.doc = self.plate.doc
        self.pid = f"{self.cid}/{self.iids['B']}"
        assert not self.doc.errors, self.doc.errors
        s = _sketcher()
        s.base_state = self.doc.state
        self.rim = s._use_edge(_Viewport(self.doc.state), Pick("edge", (self.pid, _rim_index(self.doc, self.pid))))
        sk = s.sk
        cx, cy = _centre(sk, self.rim)
        self.first = (cx, cy)
        self.conc = sk.add_circle(sk.add_point(cx, cy + 1), 3.0)
        sk.add_constraint("concentric", self.conc, self.rim)
        p1, p2 = sk.add_point(cx - 10, cy + R_WHEEL), sk.add_point(cx + 10, cy + R_WHEEL)
        self.line = sk.add_line(p1, p2)
        sk.add_constraint("horizontal", self.line)
        sk.add_constraint("tangent", self.line, self.rim)
        sk.add_constraint("hdistance", p1, sk.entities[self.rim]["c"], value=10.0)
        sk.add_constraint("hdistance", p1, p2, value=20.0)
        assert sk.solve()
        f = self.doc.make_feature("sketch")
        f["params"].update(plane=TOP, sketch=sk.to_dict())
        self.doc.add_feature(f)
        self.sid = f["id"]
        assert not self.doc.errors, self.doc.errors

    def built(self):
        return self.doc.state.sketches[self.sid].sketch

    def update(self):
        self.folder.update_context(self.plate.id, self.cid)

    def line_distance(self, sk):
        """Distance of the circle's centre from the line, to compare with the radius."""
        a, b = sk.P(sk.entities[self.line]["p1"]), sk.P(sk.entities[self.line]["p2"])
        d, w = b - a, _centre(sk, self.rim) - a
        return abs(d[0] * w[1] - d[1] * w[0]) / np.linalg.norm(d)


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def test_use_on_wheel_b_rim_adds_a_fixed_circle_and_the_constraints_hold(rig):
    sk = rig.built()
    ent = sk.entities[rig.rim]
    assert ent["type"] == "circle" and ent["src"]["part"] == rig.pid and ent["src"]["name"]
    assert np.allclose(_centre(sk, rig.rim), B_AT[:2], atol=1e-6) and abs(sk.radius(rig.rim) - R_WHEEL) < 1e-6
    assert any(c["type"] == "fix" and c["refs"][0] == ent["c"] for c in sk.constraints)    # the centre is fixed
    assert np.allclose(_centre(sk, rig.conc), B_AT[:2], atol=1e-6)                         # concentric after solve
    assert abs(rig.line_distance(sk) - R_WHEEL) < 1e-6                                    # the line touches it
    assert not rig.doc.errors


def test_the_projection_and_what_hangs_off_it_follow_wheel_b_when_it_moves(rig):
    rig.asm.doc.move_instance(rig.iids["B"], A.translation((10, 0, 0)), solve=False)
    assert rig.folder.context_status(rig.plate.id, rig.cid) == "out of date"
    rig.update()
    assert not rig.doc.errors, rig.doc.errors
    sk = rig.built()
    want = (B_AT[0] + 10, B_AT[1])
    assert np.allclose(_centre(sk, rig.rim), want, atol=1e-6)
    assert np.allclose(_centre(sk, rig.conc), want, atol=1e-6)
    assert abs(rig.line_distance(sk) - R_WHEEL) < 1e-6
    p1, p2 = (sk.P(sk.entities[rig.line][k]) for k in ("p1", "p2"))
    assert np.allclose((p1 + p2) / 2, [want[0], want[1] + R_WHEEL], atol=1e-6)            # the line moved 10 too


def test_deleting_wheel_a_leaves_the_projection_on_wheel_b(rig):
    rig.asm.doc.delete_instances([rig.iids["A"]])
    rig.update()
    assert not rig.doc.errors, rig.doc.errors
    assert rig.pid in rig.doc.state.parts and f"{rig.cid}/{rig.iids['A']}" not in rig.doc.state.parts
    sk = rig.built()
    assert np.allclose(_centre(sk, rig.rim), B_AT[:2], atol=1e-6)        # not A's old place
    assert not np.allclose(_centre(sk, rig.rim), A_AT[:2], atol=1e-3)
    assert np.allclose(_centre(sk, rig.conc), B_AT[:2], atol=1e-6)
    assert rig.sid not in rig.doc.state.recovered


def test_removing_wheel_b_reports_the_lost_edge_and_keeps_the_constraints(rig):
    before = copy.deepcopy(rig.doc.feature(rig.sid)["params"]["sketch"])
    rig.asm.doc.delete_instances([rig.iids["B"]])
    rig.update()
    assert rig.pid not in rig.doc.state.parts
    err = str(rig.doc.errors.get(rig.sid))
    assert "used in this sketch is gone" in err
    stored = rig.doc.feature(rig.sid)["params"]["sketch"]
    assert stored["constraints"] == before["constraints"] and stored["entities"] == before["entities"]
    sk = Sketch.from_dict(stored)
    lost = []
    again = F.refresh_used_edges(rig.doc.state_before(rig.doc.feature_index(rig.sid)), K.std_plane("Top"), sk, lost)
    assert again is False and lost == [rig.rim]
    assert sk.to_dict()["constraints"] == before["constraints"]


def test_extrude_up_to_wheel_b_cylindrical_face_ends_on_the_cylinder(rig):
    shape = rig.doc.state.parts[rig.pid].shape
    cyl = [f for f in K.faces(shape) if K.face_info(f).get("type") == int(K.GeomAbs_Cylinder)]
    assert len(cyl) == 1
    ref = F.make_ref(rig.pid, cyl[0], "face", shape, named=rig.doc.state.named(rig.pid))
    assert ref.get("name")
    cy, z = B_AT[1], B_AT[2]
    sid = sketch_feature(rig.doc, lambda s: s.add_circle(s.add_point(cy, z + 2), 1.0), plane={"kind": "std", "name": "Right"})
    eid = add(rig.doc, "extrude", regions=region_refs(rig.doc, sid), end="up_to_face",
              up_to={"kind": "face", "ref": ref}, op="new")["id"]
    bar = next(p for k, p in rig.doc.state.parts.items() if k.startswith(eid + "/"))
    lo, hi = K.bbox(bar.shape)
    wall = B_AT[0] - float(np.sqrt(R_WHEEL ** 2 - 1.0))              # where the bar's widest point meets the cylinder
    assert abs(hi[0] - (B_AT[0] - R_WHEEL)) < 0.1 and hi[0] >= wall - 1e-6 and lo[0] < 1e-6


def test_a_fillet_aimed_at_a_context_body_is_refused(rig):
    shape = rig.doc.state.parts[rig.pid].shape
    named = rig.doc.state.named(rig.pid)
    idx = _rim_index(rig.doc, rig.pid)
    ref = F.make_ref(rig.pid, K.edges(shape)[idx], "edge", shape, named)
    before = rig.doc.state.parts[rig.pid].shape
    _f, err = try_add(rig.doc, "fillet", edges=[ref], radius=0.5)
    assert err == "Context bodies are reference only"
    assert rig.doc.state.parts[rig.pid].reference and rig.doc.state.parts[rig.pid].shape is before


def test_use_on_a_sketch_instance_edge_after_its_source_sketch_gains_an_entity(tmp_path):
    """Documents behaviour, not a requirement: the Use is on an edge of a Sketch-instance neighbour. Its element
    names are positional, so what follows a source sketch that gains an entity is whatever this test observed
    (written into the assertions below), and may change when sketch-instance names become stable. Observed: the
    edge keeps its name when the new entity is appended, and the Use still lands on the same line."""
    from tests.test_sketch_instance import assembly_for, make_studio, sk_src
    doc, sid, _pid = make_studio(with_part=True)
    a, _ = assembly_for(doc)
    ip, isk = a.insert(A_part(doc)), a.insert(sk_src(doc, sid))
    a.refresh()
    holder = block_doc()
    key = holder.store_context(A.capture_context(a, ip))
    ctx = add(holder, "context", assembly=a.uid, instance=ip, capture=key, ids="instance")
    pid = f"{ctx['id']}/{isk}"
    part = holder.state.parts[pid]
    s = _sketcher()
    s.base_state = holder.state
    ei = 0
    eid = s._use_edge(_Viewport(holder.state), Pick("edge", (pid, ei)))
    src = s.sk.entities[eid].get("src") if eid in s.sk.entities else None
    assert src and src.get("name") and K.edge_info(K.edges(part.shape)[ei])["len"] == 30.0
    sf = holder.make_feature("sketch")
    sf["params"].update(plane=TOP, sketch=s.sk.to_dict())
    holder.add_feature(sf)
    assert not holder.errors, holder.errors
    before = np.array(holder.state.sketches[sf["id"]].sketch.P(s.sk.entities[eid]["p1"]))
    f = copy.deepcopy(doc.feature(sid))                   # the source sketch gains a line, and the Context is updated
    sk = Sketch.from_dict(f["params"]["sketch"])
    sk.add_line(sk.add_point(0, 30), sk.add_point(10, 30))
    assert sk.solve()
    f["params"]["sketch"] = sk.to_dict()
    doc.replace_feature(f)
    a.refresh()
    from freeshape.document import update_context
    update_context(holder, ctx["id"], a)
    assert len(K.edges(holder.state.parts[pid].shape)) == 3
    # Observed: the new entity was added after the old ones, so the used edge kept its name and the Use still
    # projects the same edge to the same place. A change that reorders entities would show up here.
    assert not holder.errors, holder.errors
    after = np.array(holder.state.sketches[sf["id"]].sketch.P(s.sk.entities[eid]["p1"]))
    assert np.allclose(before, after, atol=1e-6)


def A_part(doc):
    from tests.test_sketch_instance import part_src
    return part_src(doc, next(iter(doc.state.parts)))
