"""One interference kernel for Part Studio and Assembly (headless: no Qt, no document types).

Pipeline: a numpy bounding-box reject, then K.common per surviving pair, then a classification.
A boolean that fails is reported as a row of kind "failed", never read as "no clash"."""
from dataclasses import dataclass, field

import numpy as np
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

from . import kernel as K

# The numbers below come from scripts/spikes/op2_interference_bench.result.txt (I5.1):
#   DECISION interference min_depth=0.0001 min_volume=1e-3 engine=helper open_runs_at_most=90
# Nothing reads that file at run time.
MIN_DEPTH = 1e-4      # mm; average overlap thickness (2 v / area) at or below this is touching
MIN_VOLUME = 1e-3     # mm3
BOX_SLACK = 1e-6      # mm
CONTAIN_TOL = 1e-6


@dataclass
class Body:
    id: str
    shape: object              # placed in world coordinates
    geom_key: object = None    # hashable identity of the unplaced geometry; None = do not cache
    T: np.ndarray = field(default_factory=lambda: np.eye(4))   # placement, for the cache key
    standard: bool = False


@dataclass
class Clash:
    a: str
    b: str
    kind: str                  # "overlap" | "contained" | "thread" | "failed"
    volume: float
    lo: np.ndarray
    hi: np.ndarray
    shape: object              # the overlap solid; None for "failed"
    note: str = ""             # the kernel error text for "failed"


class ClashList(list):
    """The clashes, largest first. `incomplete` is True when the run was cancelled;
    `pairs_checked` / `pairs_total` count the pairs that survived the box reject."""
    incomplete = False
    pairs_checked = 0
    pairs_total = 0


def _area(shape) -> float:
    g = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, g)
    return g.Mass()


def _boxes_overlap(los, his):
    """Index pairs (i < j) whose boxes are not separated by more than BOX_SLACK."""
    n = len(los)
    if n < 2:
        return []
    i, j = np.triu_indices(n, 1)
    sep = np.any(his[i] < los[j] - BOX_SLACK, axis=1) | np.any(his[j] < los[i] - BOX_SLACK, axis=1)
    return list(zip(i[~sep].tolist(), j[~sep].tolist()))


def _rel_key(a: Body, b: Body):
    if a.geom_key is None or b.geom_key is None:
        return None
    rel = np.linalg.inv(np.asarray(a.T, float)) @ np.asarray(b.T, float)
    return (a.geom_key, b.geom_key, tuple((np.round(rel, 6) + 0.0).ravel().tolist()))


def _common(sa, sb):
    """(overlap shape or None, volume, area); raises KernelError when both attempts fail."""
    try:
        ov = K.common(sa, sb)
    except K.KernelError:
        ov = K.common(sa, sb, check=True)
    if ov is None or ov.IsNull() or not K.solids(ov):
        return None, 0.0, 0.0
    return ov, K.volume(ov), _area(ov)


def find_interferences(bodies, *, skip_pairs=frozenset(), expected_pairs=frozenset(),
                       min_volume=MIN_VOLUME, min_depth=MIN_DEPTH,
                       progress=None, cancelled=None, cache=None) -> ClashList:
    """Clashes between the bodies, largest volume first. Pairs are frozensets of two body ids."""
    out = ClashList()
    bodies = list(bodies)
    if cache is None:
        cache = {}
    boxes = [K.bbox(b.shape) for b in bodies]
    los = np.array([b[0] for b in boxes]).reshape(-1, 3)
    his = np.array([b[1] for b in boxes]).reshape(-1, 3)
    pairs = [(i, j) for i, j in _boxes_overlap(los, his)
             if frozenset((bodies[i].id, bodies[j].id)) not in skip_pairs]
    total = len(pairs)
    out.pairs_total = total
    vols = {}

    def body_volume(i):
        if i not in vols:
            vols[i] = K.volume(bodies[i].shape)
        return vols[i]

    if progress:
        progress(0, total)
    for n, (i, j) in enumerate(pairs):
        if cancelled is not None and cancelled():
            out.incomplete = True
            break
        a, b = bodies[i], bodies[j]
        key = _rel_key(a, b)
        try:
            if key is not None and key in cache:
                loc, v, area = cache[key]
                ov = None if loc is None else K.located(loc, a.T)
            else:
                ov, v, area = _common(a.shape, b.shape)
                if key is not None:
                    loc = None if ov is None else K.located(ov, np.linalg.inv(np.asarray(a.T, float)))
                    cache[key] = (loc, v, area)
        except K.KernelError as e:
            lo, hi = np.maximum(los[i], los[j]), np.minimum(his[i], his[j])
            out.append(Clash(a.id, b.id, "failed", 0.0, lo, hi, None, str(e) or "Boolean operation failed"))
        else:
            if ov is not None and v > min_volume and area > 0 and 2 * v / area > min_depth:
                lo, hi = K.bbox(ov)
                if v >= (1 - CONTAIN_TOL) * min(body_volume(i), body_volume(j)):
                    kind = "contained"
                elif frozenset((a.id, b.id)) in expected_pairs:
                    kind = "thread"
                else:
                    kind = "overlap"
                out.append(Clash(a.id, b.id, kind, v, lo, hi, ov))
        out.pairs_checked = n + 1
        if progress:
            progress(n + 1, total)
    out.sort(key=lambda c: -c.volume)
    return out


def studio_bodies(doc):
    """Bodies of a Part Studio: parts that are not hidden, not Context bodies and have a solid.
    `doc` needs `.state.parts` and `.hidden_parts`. Placement is the identity; the geom key is the part id."""
    out = []
    for p in doc.state.parts.values():
        if p.id in doc.hidden_parts or getattr(p, "reference", False) or p.shape is None:
            continue
        if not K.solids(p.shape):
            continue
        out.append(Body(p.id, p.shape, p.id, np.eye(4), False))
    return out
