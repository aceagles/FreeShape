"""Names on faces made by the up-to sweep (I1.1).

An up-to end that is not a blind extrude in disguise (a curved face, a part, next, every revolve up-to) sweeps
past the target and trims. Its faces are named from the profile through the sweep's and the trim's histories,
and the end cap on the target takes the role `end`, so it keeps its name when the target moves."""
import copy
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402

TOP = {"kind": "std", "name": "Top"}
RIGHT = {"kind": "std", "name": "Right"}
FRONT = {"kind": "std", "name": "Front"}
END = ";:Gend;"


def _sketch(doc, build, plane=TOP):
    s = Sketch()
    build(s)
    s.solve()
    f = doc.make_feature("sketch")
    f["params"].update(sketch=s.to_dict(), plane=plane)
    doc.add_feature(f)
    return f["id"], doc.state.sketches[f["id"]]


def _rect(x0, y0, x1, y1):
    def b(s):
        p = [s.add_point(x0, y0), s.add_point(x1, y0), s.add_point(x1, y1), s.add_point(x0, y1)]
        for a, c in zip(p, p[1:] + p[:1]):
            s.add_line(a, c)
    return b


def _circ(x, y, r):
    return lambda s: s.add_circle(s.add_point(x, y), r)


def _add(doc, ftype, **params):
    f = doc.make_feature(ftype)
    f["params"].update(params)
    doc.add_feature(f)
    assert not doc.errors.get(f["id"]), doc.errors.get(f["id"])
    return f


def _extrude(doc, sid, sr, **kw):
    return _add(doc, "extrude", regions=[F.region_ref(sr, sid, r) for r in sr.regions], **kw)


def _part_of(doc, f):
    return next(p for pid, p in doc.state.parts.items() if pid.startswith(f["id"]))


def _face_names(doc, part):
    nm = doc.state.named(part.id)
    assert nm is not None
    return [nm.name_of(f) for f in K.faces(part.shape)]


def _assert_all_named(doc, part):
    names = _face_names(doc, part)
    assert all(names), names
    assert len(set(names)) == len(names), names
    return names


def _bar_doc(dz=1.0):
    """A round bar (r 6) along X, its axis at z 30 + dz: the move puts it there."""
    doc = Document()
    sid, sr = _sketch(doc, _circ(0, 30, 6), RIGHT)
    _extrude(doc, sid, sr, end="symmetric", depth=60, op="new")
    bar = next(iter(doc.state.parts))
    mv = _add(doc, "move", parts=[bar], dz=dz)
    bar = next(iter(doc.state.parts))
    return doc, bar, mv


def _round_face(doc, pid):
    sh = doc.state.parts[pid].shape
    cf = max(K.faces(sh), key=lambda f: K.face_info(f)["area"])
    return F.make_ref(pid, cf, "face", sh, doc.state.named(pid))


def _slab_doc():
    doc = Document()
    sid, sr = _sketch(doc, _rect(0, 0, 40, 40))
    _extrude(doc, sid, sr, depth=5, op="new")
    _add(doc, "move", parts=list(doc.state.parts), dz=30)          # slab at z 30..35
    return doc, next(iter(doc.state.parts))


# ------------------------------------------------------------------ every face named, no two alike
def test_extrude_up_to_a_curved_face_names_every_face_and_the_end_cap():
    doc, bar, _ = _bar_doc()
    sid, sr = _sketch(doc, _circ(0, 0, 3))
    e = _extrude(doc, sid, sr, end="up_to_face", up_to={"kind": "face", "ref": _round_face(doc, bar)}, op="new")
    names = _assert_all_named(doc, _part_of(doc, e))
    assert sum(END in n for n in names) == 1, names           # the cap on the bar, and only that


def test_extrude_up_to_a_part_names_every_face():
    doc, slab = _slab_doc()
    sid, sr = _sketch(doc, _circ(20, 20, 5))
    e = _extrude(doc, sid, sr, end="up_to_part", up_to={"kind": "part", "id": slab}, op="new")
    names = _assert_all_named(doc, _part_of(doc, e))
    assert sum(END in n for n in names) == 1, names


def test_extrude_up_to_next_names_every_face():
    doc, _slab = _slab_doc()
    sid, sr = _sketch(doc, _circ(20, 20, 5))
    e = _extrude(doc, sid, sr, end="up_to_next", op="new")
    names = _assert_all_named(doc, _part_of(doc, e))
    assert sum(END in n for n in names) == 1, names


def test_extrude_up_to_next_with_two_profiles_names_every_face():
    doc, _slab = _slab_doc()
    sid, sr = _sketch(doc, lambda s: (_circ(10, 10, 3)(s), _circ(30, 30, 3)(s)))
    e = _extrude(doc, sid, sr, end="up_to_next", op="new")
    names = [n for pid, p in doc.state.parts.items() if pid.startswith(e["id"]) for n in _assert_all_named(doc, p)]
    assert len(set(names)) == len(names) and sum(END in n for n in names) == 2, names


def test_revolve_up_to_a_face_names_every_face():
    doc = Document()
    sid, sr = _sketch(doc, _rect(-10, 30, 0, 40))                   # a block whose face x = 0 is the target
    _extrude(doc, sid, sr, depth=10, op="new")
    block = next(iter(doc.state.parts))
    sh = doc.state.parts[block].shape
    face = next(f for f in K.faces(sh) if abs(K.face_info(f)["c"][0]) < 1e-6)
    ref = F.make_ref(block, face, "face", sh, doc.state.named(block))
    sid, sr = _sketch(doc, _rect(10, 0, 20, 10), FRONT)
    r = _add(doc, "revolve", regions=[F.region_ref(sr, sid, x) for x in sr.regions], axis={"kind": "std", "name": "Z"},
             end="up_to_face", up_to={"kind": "face", "ref": ref}, op="new")
    names = _assert_all_named(doc, _part_of(doc, r))
    assert sum(END in n for n in names) == 1, names


# ------------------------------------------------------------------ the end cap keeps its name
def test_fillet_on_the_end_cap_survives_moving_the_target():
    doc, bar, mv = _bar_doc(dz=1.0)
    sid, sr = _sketch(doc, _rect(-2, -2, 2, 2))
    e = _extrude(doc, sid, sr, end="up_to_face", up_to={"kind": "face", "ref": _round_face(doc, bar)}, op="new")
    part = _part_of(doc, e)
    nm = doc.state.named(part.id)
    cap = next(f for f in K.faces(part.shape) if END in nm.name_of(f))
    straight = [ed for ed in K.edges(cap) if K.edge_line(ed) is not None]
    assert len(straight) == 2                                     # the two edges along the bar's axis
    ref = F.make_ref(part.id, straight[0], "edge", part.shape, nm)
    assert ref.get("name")
    fil = _add(doc, "fillet", edges=[ref], radius=0.5)
    before = K.volume(doc.state.parts[part.id].shape)

    moved = copy.deepcopy(doc.feature(mv["id"]))
    moved["params"]["dz"] = 3.0                                   # the bar's underside goes up 2 mm
    doc.replace_feature(moved)
    assert not doc.errors, doc.errors
    assert fil["id"] not in doc.state.recovered                   # found by its name, not by its shape
    assert K.volume(doc.state.parts[part.id].shape) > before + 30   # a 4 x 4 profile 2 mm longer


# ------------------------------------------------------------------ a blind extrude is untouched
def _blind(doc):
    sid, sr = _sketch(doc, _rect(50, 0, 60, 10))
    return _extrude(doc, sid, sr, depth=17, op="new")


def test_blind_extrude_names_are_unchanged():
    ref_doc = Document()
    recorded = sorted(_face_names(ref_doc, _part_of(ref_doc, _blind(ref_doc))))
    assert all(recorded)

    doc = Document()                                              # the same blind extrude, then an up-to one
    b = _blind(doc)
    sid, sr = _sketch(doc, _rect(0, 0, 40, 40))
    s = _extrude(doc, sid, sr, depth=5, op="new")
    slab = _part_of(doc, s).id
    _add(doc, "move", parts=[slab], dz=30)
    sid, sr = _sketch(doc, _circ(20, 20, 5))
    u = _extrude(doc, sid, sr, end="up_to_part", up_to={"kind": "part", "id": slab}, op="new")
    _assert_all_named(doc, _part_of(doc, u))
    assert sorted(_face_names(doc, _part_of(doc, b))) == recorded
