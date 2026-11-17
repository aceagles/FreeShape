"""Sketch editing operations: fillet, offset, slot, mirror and patterns.

Pure geometry on a Sketch (no Qt), so they can be tested headlessly and reused by scripts.
Each raises ValueError with a user-readable message when it can't be done.
"""
from __future__ import annotations

import math

import numpy as np

from .sketch import Sketch, ORIGIN

TOL = 1e-7


def _ang(c, p):
    return math.atan2(p[1] - c[1], p[0] - c[0])


def _ccw_contains(c, p1, p2, p3):
    a1 = _ang(c, p1)
    a2 = (_ang(c, p2) - a1) % (2 * math.pi)
    a3 = (_ang(c, p3) - a1) % (2 * math.pi)
    return a3 < a2


def _users(sk: Sketch, pid):
    return [eid for eid, e in sk.entities.items() if pid in sk.entity_points(eid) and e["type"] != "point"]


# --------------------------------------------------------------------------- fillet

def fillet_corner(sk: Sketch, pid: str, r: float) -> str:
    """Round the corner where exactly two lines meet at point `pid`. Returns the new arc id."""
    if r <= 0:
        raise ValueError("Radius must be positive")
    users = _users(sk, pid)
    lines = [u for u in users if sk.entities[u]["type"] == "line"]
    if len(users) != 2 or len(lines) != 2:
        raise ValueError("Click a corner where two lines meet")
    P = sk.P(pid)
    ends = []
    for lid in lines:
        e = sk.entities[lid]
        other = e["p2"] if e["p1"] == pid else e["p1"]
        ends.append((lid, "p1" if e["p1"] == pid else "p2", sk.P(other)))
    (l1, k1, A), (l2, k2, B) = ends
    la, lb = np.linalg.norm(A - P), np.linalg.norm(B - P)
    if la < TOL or lb < TOL:
        raise ValueError("Lines are too short")
    u1, u2 = (A - P) / la, (B - P) / lb
    phi = math.acos(float(np.clip(u1 @ u2, -1, 1)))
    if phi < 1e-3 or phi > math.pi - 1e-3:
        raise ValueError("The two lines are in line with each other")
    theta = phi / 2
    dist = r / math.tan(theta)
    if dist >= la - 1e-9 or dist >= lb - 1e-9:
        raise ValueError("Radius is too large for these lines")
    T1, T2 = P + u1 * dist, P + u2 * dist
    bis = (u1 + u2) / np.linalg.norm(u1 + u2)
    C = P + bis * (r / math.sin(theta))
    t1, t2, c = sk.add_point(*T1), sk.add_point(*T2), sk.add_point(*C)
    sk.entities[l1][k1] = t1
    sk.entities[l2][k2] = t2
    construction = sk.entities[l1].get("construction", False) and sk.entities[l2].get("construction", False)
    M = C + (P - C) / np.linalg.norm(P - C) * r
    aid = sk.add_arc(c, t1, t2, construction) if _ccw_contains(C, T1, T2, M) else sk.add_arc(c, t2, t1, construction)
    # Keep the old corner as a "virtual sharp": it stays on both lines, and any length
    # dimensions on those lines now measure to it — so a dimensioned shape keeps its size.
    far = {l1: sk.entities[l1]["p2" if k1 == "p1" else "p1"], l2: sk.entities[l2]["p2" if k2 == "p1" else "p1"]}
    for k in sk.constraints:
        if k["type"] in ("distance", "hdistance", "vdistance") and len(k["refs"]) == 1 and k["refs"][0] in far:
            k["refs"] = [far[k["refs"][0]], pid]
    if not any(e["type"] == "point" and e["p"] == pid for e in sk.entities.values()):
        vs = sk.add_point_entity(pid)
        sk.entities[vs]["virtual"] = True
    sk.add_constraint("point_on", pid, l1)
    sk.add_constraint("point_on", pid, l2)
    sk.add_constraint("tangent", aid, l1)
    sk.add_constraint("tangent", aid, l2)
    sk.add_constraint("normal_at", aid, l1, t1)
    sk.add_constraint("normal_at", aid, l2, t2)
    rc = sk.add_constraint("radius", aid, value=r)
    sk.place_dim(rc, M + (M - C) / np.linalg.norm(M - C) * max(r, 1.0) * 1.2)
    sk.solve()
    return aid


# --------------------------------------------------------------------------- offset

def _ends(e, splines=True):
    """(start, end) point ids of an open curve: a line, arc or open spline; None for anything else."""
    if e["type"] in ("line", "arc"):
        return e["p1"], e["p2"]
    if splines and e["type"] == "spline" and not e.get("closed") and len(e.get("pts", [])) >= 2:
        return e["pts"][0], e["pts"][-1]
    return None


def _chain(sk: Sketch, eid: str, splines=False):
    """Ordered list of (eid, forward) for the chain of connected lines/arcs (and open splines, with
    splines=True) containing eid."""
    ents = {k: dict(e, p1=_ends(e, splines)[0], p2=_ends(e, splines)[1]) if _ends(e, splines) else e
            for k, e in sk.entities.items()}
    adj = {}
    for k, e in ents.items():
        if _ends(sk.entities[k], splines):
            for p in (e["p1"], e["p2"]):
                adj.setdefault(p, []).append(k)
    chain = [(eid, True)]
    seen = {eid}
    closed = False
    cur, fwd = eid, True
    while True:
        e = ents[cur]
        end = e["p2"] if fwd else e["p1"]
        nb = [x for x in adj.get(end, []) if x != cur]
        if len(adj.get(end, [])) != 2 or not nb:
            break
        nxt = nb[0]
        if nxt == eid:
            closed = True
            break
        if nxt in seen:
            break
        seen.add(nxt)
        nf = ents[nxt]["p1"] == end
        chain.append((nxt, nf))
        cur, fwd = nxt, nf
    if not closed:
        cur = eid
        while True:
            start = ents[cur]["p1"] if chain[0][1] else ents[cur]["p2"]
            nb = [x for x in adj.get(start, []) if x != cur]
            if len(adj.get(start, [])) != 2 or not nb or nb[0] in seen:
                break
            prv = nb[0]
            seen.add(prv)
            pf = ents[prv]["p2"] == start
            chain.insert(0, (prv, pf))
            cur = prv
    return chain, closed


def _left_normal(v):
    n = np.array([-v[1], v[0]])
    return n / max(np.linalg.norm(n), 1e-12)


def _intersections(c1, c2):
    """Intersections of two curves given as ('line', P, D) or ('circle', C, r)."""
    if c1[0] == "line" and c2[0] == "line":
        P, D = c1[1], c1[2]
        Q, E = c2[1], c2[2]
        den = D[0] * E[1] - D[1] * E[0]
        if abs(den) < 1e-12:
            return []
        w = Q - P
        t = (w[0] * E[1] - w[1] * E[0]) / den
        return [P + t * D]
    if c1[0] == "circle" and c2[0] == "line":
        c1, c2 = c2, c1
    if c1[0] == "line" and c2[0] == "circle":
        P, D = c1[1], c1[2] / np.linalg.norm(c1[2])
        C, r = c2[1], c2[2]
        f = P - C
        b = f @ D
        disc = b * b - (f @ f - r * r)
        if disc < -1e-9:
            return []
        s = math.sqrt(max(disc, 0))
        return [P + (-b - s) * D, P + (-b + s) * D]
    C1, r1, C2, r2 = c1[1], c1[2], c2[1], c2[2]
    d = np.linalg.norm(C2 - C1)
    if d < 1e-12 or d > r1 + r2 + 1e-9 or d < abs(r1 - r2) - 1e-9:
        return []
    a = (r1 * r1 - r2 * r2 + d * d) / (2 * d)
    h = math.sqrt(max(r1 * r1 - a * a, 0))
    m = C1 + a * (C2 - C1) / d
    perp = np.array([-(C2 - C1)[1], (C2 - C1)[0]]) / d
    return [m + h * perp, m - h * perp]


def _offset_dimension(sk: Sketch, pairs, dist: float, first=None):
    """The one `offset` dimension that holds every (new, source) pair `dist` apart, on the side each new entity
    was just placed. The pair of `first` (a source id) leads, so the dimension is drawn there."""
    pairs = sorted(pairs, key=lambda p: p[1] != first)
    refs, signs = [], []
    for new, src in pairs:
        es = sk.entities[src]
        if es["type"] == "line":
            a, b = sk.P(es["p1"]), sk.P(es["p2"])
            d, w = b - a, sk.P(sk.entities[new]["p1"]) - a
            side = d[0] * w[1] - d[1] * w[0]
        else:
            side = sk.radius(new) - sk.radius(src)
        refs += [new, src]
        signs.append(1.0 if side >= 0 else -1.0)
    c = sk.add_constraint("offset", *refs, value=abs(dist))
    c["signs"] = signs
    return c


_OFFSET_TOO_LARGE = "Offset is too large for this shape"


def _offset_side(sk: Sketch, chain, closed: bool, s: float):
    """Where the copy of `chain` lands when each segment moves `s` to its left (pure: nothing is added to the
    sketch). Returns (items, joints). Raises ValueError when an arc radius is not positive or a line would reverse."""
    items = []
    for k, fwd in chain:
        e = sk.entities[k]
        if e["type"] == "line":
            S, E = (sk.P(e["p1"]), sk.P(e["p2"])) if fwd else (sk.P(e["p2"]), sk.P(e["p1"]))
            n = _left_normal(E - S)
            items.append({"eid": k, "fwd": fwd, "type": "line", "S": S + s * n, "E": E + s * n,
                          "curve": ("line", S + s * n, E - S)})
        else:
            C = sk.P(e["c"])
            r = sk.radius(k)
            r2 = r - s if fwd else r + s
            if r2 <= TOL:
                raise ValueError(_OFFSET_TOO_LARGE)
            S, E = (sk.P(e["p1"]), sk.P(e["p2"])) if fwd else (sk.P(e["p2"]), sk.P(e["p1"]))
            items.append({"eid": k, "fwd": fwd, "type": "arc", "C": e["c"],
                          "S": C + (S - C) / np.linalg.norm(S - C) * r2,
                          "E": C + (E - C) / np.linalg.norm(E - C) * r2,
                          "curve": ("circle", C, r2)})
    n = len(items)
    joints = []          # new vertex positions; joint i is between item i-1 and item i
    for i in range(n):
        if i == 0 and not closed:
            joints.append(items[0]["S"])
            continue
        a, b = items[i - 1], items[i]
        guess = (a["E"] + b["S"]) / 2
        if np.linalg.norm(a["E"] - b["S"]) < 1e-6:
            joints.append(guess)
            continue
        cand = _intersections(a["curve"], b["curve"])
        joints.append(min(cand, key=lambda p: np.linalg.norm(p - guess)) if cand else guess)
    if not closed:
        joints.append(items[-1]["E"])
    for i, it in enumerate(items):
        if it["type"] == "line":
            a = joints[i]
            b = joints[(i + 1) % len(joints)] if closed else joints[i + 1]
            e = sk.entities[it["eid"]]
            src = sk.P(e["p2"]) - sk.P(e["p1"])
            if not it["fwd"]:
                src = -src
            if (b - a) @ src <= TOL * max(np.linalg.norm(src), 1.0):      # reversed or collapsed to a point
                raise ValueError(_OFFSET_TOO_LARGE)
    return items, joints


def _offset_build(sk: Sketch, items, joints, closed: bool, cons: bool):
    """Add one side's points and entities to the sketch. Returns (new ids, point ids)."""
    pids = [sk.add_point(*p) for p in joints]
    new = []
    for i, it in enumerate(items):
        a = pids[i]
        b = pids[(i + 1) % len(pids)] if closed else pids[i + 1]
        if it["type"] == "line":
            lid = sk.add_line(a, b, cons)
            sk.add_constraint("parallel", lid, it["eid"])
            new.append(lid)
        else:
            new.append(sk.add_arc(it["C"], a, b, cons) if it["fwd"] else sk.add_arc(it["C"], b, a, cons))
    return new, pids


def offset(sk: Sketch, eid: str, dist: float, both: bool = False, caps=None):
    """Offset the chain containing `eid`. Positive distance = outward for closed chains. The copy is held by one
    `offset` dimension of |dist|, so it follows its source and the gap can be edited.

    both=True also offsets to the other side, with the same dimension driving both. caps="round" on an open chain
    then closes the two copies with a half-disc round each end (arcs centred on the source end points, tangent
    to the copies) and makes the source chain construction, so the result is one region. A closed chain or a
    circle gives two loops and no caps. Raises ValueError when the offset is too large for the shape."""
    if abs(dist) < TOL:
        raise ValueError("Offset distance can't be zero")
    if caps not in (None, "none", "round"):
        raise ValueError("Caps must be 'none' or 'round'")
    e0 = sk.entities.get(eid)
    if e0 is None or e0["type"] in ("point", "spline", "ellipse", "text"):
        raise ValueError("Offset works on lines, arcs and circles")
    cons = e0.get("construction", False)
    if e0["type"] == "circle":
        radii = [e0["r"] + dist] + ([e0["r"] - dist] if both else [])
        if min(radii) <= TOL:
            raise ValueError(_OFFSET_TOO_LARGE)
        new = [sk.add_circle(e0["c"], r, cons) for r in radii]
        _offset_dimension(sk, [(n, eid) for n in new], dist)
        sk.solve()
        return new
    chain, closed = _chain(sk, eid)
    # orientation: positive offset = outward on a closed chain
    s = dist
    if closed:
        pts = []
        for k, fwd in chain:
            pl = sk.polyline(k, 32)
            pts.extend(pl if fwd else pl[::-1])
        pts = np.array(pts)
        area = 0.5 * np.sum(pts[:, 0] * np.roll(pts[:, 1], -1) - np.roll(pts[:, 0], -1) * pts[:, 1])
        s = -dist if area > 0 else dist          # CCW: outward is to the right
    sides = [_offset_side(sk, chain, closed, s)]
    if both:
        sides.append(_offset_side(sk, chain, closed, -s))      # validated before anything is added
    new, pairs, ends = [], [], []
    for items, joints in sides:
        ids, pids = _offset_build(sk, items, joints, closed, cons)
        new += ids
        pairs += [(k, it["eid"]) for k, it in zip(ids, items)]
        ends.append((ids, pids))
    if both and caps == "round" and not closed:
        (ids_p, pids_p), (ids_m, pids_m) = ends
        first, last = chain[0], chain[-1]
        e_first, e_last = sk.entities[first[0]], sk.entities[last[0]]
        c_start = e_first["p1"] if first[1] else e_first["p2"]
        c_end = e_last["p2"] if last[1] else e_last["p1"]
        # CCW arcs, as slot() draws them: the end cap runs minus -> plus round the front, the start cap plus -> minus
        cap_end = sk.add_arc(c_end, pids_m[-1], pids_p[-1], cons)
        cap_start = sk.add_arc(c_start, pids_p[0], pids_m[0], cons)
        for cap, a, b in ((cap_start, ids_p[0], ids_m[0]), (cap_end, ids_p[-1], ids_m[-1])):
            sk.add_constraint("tangent", cap, a)
            if sk.entities[a]["type"] == "arc":      # arc-to-arc tangency needs its internal/external sign
                d = np.linalg.norm(sk.P(sk.entities[cap]["c"]) - sk.P(sk.entities[a]["c"]))
                sk.constraints[-1]["value"] = -1.0 if d < max(sk.radius(cap), sk.radius(a)) else 1.0
            sk.add_constraint("tangent", cap, b)
            if sk.entities[b]["type"] == "arc":
                d = np.linalg.norm(sk.P(sk.entities[cap]["c"]) - sk.P(sk.entities[b]["c"]))
                sk.constraints[-1]["value"] = -1.0 if d < max(sk.radius(cap), sk.radius(b)) else 1.0
        for k, _fwd in chain:
            sk.entities[k]["construction"] = True
        new += [cap_start, cap_end]
    _offset_dimension(sk, pairs, dist, first=eid)
    sk.solve()
    return new


# --------------------------------------------------------------------------- slot

def slot(sk: Sketch, a, b, width: float, construction=False, a_pid=None, b_pid=None):
    """Straight slot between centre points a and b (sketch coords). Returns the new entity ids."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    L = np.linalg.norm(b - a)
    if L < TOL:
        raise ValueError("Slot ends are in the same place")
    if width <= 0:
        raise ValueError("Width must be positive")
    w = width / 2
    d = (b - a) / L
    n = np.array([-d[1], d[0]])
    A = a_pid or sk.add_point(*a)
    B = b_pid or sk.add_point(*b)
    pa_l, pb_l = sk.add_point(*(a + n * w)), sk.add_point(*(b + n * w))
    pb_r, pa_r = sk.add_point(*(b - n * w)), sk.add_point(*(a - n * w))
    top = sk.add_line(pa_l, pb_l, construction)
    bot = sk.add_line(pb_r, pa_r, construction)
    end_b = sk.add_arc(B, pb_r, pb_l, construction)
    end_a = sk.add_arc(A, pa_l, pa_r, construction)
    centre = sk.add_line(A, B, True)
    for arc in (end_a, end_b):
        sk.add_constraint("tangent", arc, top)
        sk.add_constraint("tangent", arc, bot)
    sk.add_constraint("equal", end_a, end_b)
    rc = sk.add_constraint("radius", end_b, value=w)
    sk.place_dim(rc, b + d * w * 1.6 + n * w * 0.6)
    sk.solve()
    return [top, bot, end_a, end_b, centre]


# --------------------------------------------------------------------------- polygon

POLYGON_MIN_SIDES, POLYGON_MAX_SIDES = 3, 30


def polygon(sk: Sketch, centre, corner, sides: int, mode: str = "inscribed", construction=False, centre_pid=None):
    """Regular polygon about `centre` (sketch coords). Returns (line ids, construction circle id).

    mode "inscribed": `corner` is a vertex; every vertex is point_on the circle through it.
    mode "circumscribed": `corner` is where the circle meets the first side's mid-point; the circle
    (radius = |corner - centre|) is tangent to every side; a second construction circle through the corners
    keeps the polygon regular.
    One point per corner, shared by the two lines that meet there, so the region closes. Inscribed: n
    point_on plus n-1 `equal` constraints. The circle's centre point is `centre_pid` when given."""
    if mode not in ("inscribed", "circumscribed"):
        raise ValueError("Polygon mode must be inscribed or circumscribed")
    if isinstance(sides, bool) or int(sides) != sides:
        raise ValueError("Sides must be a whole number")
    sides = int(sides)
    if not POLYGON_MIN_SIDES <= sides <= POLYGON_MAX_SIDES:
        raise ValueError(f"Polygon needs {POLYGON_MIN_SIDES} to {POLYGON_MAX_SIDES} sides")
    c = np.asarray(centre, float)
    v = np.asarray(corner, float) - c
    r = float(np.linalg.norm(v))
    if r < TOL:
        raise ValueError("Polygon corner is at the centre")
    a0 = math.atan2(v[1], v[0])
    step = 2 * math.pi / sides
    if mode == "inscribed":
        ang, R = [a0 + k * step for k in range(sides)], r
    else:
        ang, R = [a0 - step / 2 + k * step for k in range(sides)], r / math.cos(step / 2)
    C = centre_pid or sk.add_point(*c)
    circ = sk.add_circle(C, r, True)
    pids = [sk.add_point(*(c + R * np.array([math.cos(a), math.sin(a)]))) for a in ang]
    lines = [sk.add_line(pids[i], pids[(i + 1) % sides], construction) for i in range(sides)]
    outer = circ
    if mode == "circumscribed":
        # tangent sides + equal sides leave a free shape for even n (a rhombus-like family), so the corners
        # also sit on a second construction circle about the same centre; that fixes the polygon as regular
        outer = sk.add_circle(C, R, True)
    for i in range(sides):
        if mode == "circumscribed":
            sk.add_constraint("tangent", lines[i], circ)
        sk.add_constraint("point_on", pids[i], outer)
        if i and mode == "inscribed":
            sk.add_constraint("equal", lines[i - 1], lines[i])
    sk.solve()
    return lines, circ


# --------------------------------------------------------------------------- drawing tools
# The Qt-free body of the sketcher's rectangle, centre rectangle, circle and three-point / centre arc tools.
# Each takes picks: a position (sketch coords), or (position, src) where src says which point the pick is:
# None to make a new one, a point id to reuse, or a callable returning a point id (the desktop's snapping, which
# is called at the moment the point is needed so point ids come out in the order the tools always made them).

def _pick(p):
    if isinstance(p, tuple) and len(p) == 2 and not np.isscalar(p[0]) and not isinstance(p[0], float):
        return np.asarray(p[0], float), p[1]
    return np.asarray(p, float), None


def _pid(sk: Sketch, uv, src):
    if src is None:
        return sk.add_point(*uv)
    return src() if callable(src) else src


def circumcenter(a, b, c):
    """Centre of the circle through three points, or None when they are in line."""
    a, b, c = map(np.asarray, (a, b, c))
    d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if abs(d) < 1e-12:
        return None
    ux = ((a @ a) * (b[1] - c[1]) + (b @ b) * (c[1] - a[1]) + (c @ c) * (a[1] - b[1])) / d
    uy = ((a @ a) * (c[0] - b[0]) + (b @ b) * (a[0] - c[0]) + (c @ c) * (b[0] - a[0])) / d
    return np.array([ux, uy])


def _four_lines(sk: Sketch, a, b, c, d, construction):
    l1 = sk.add_line(a, b, construction)
    l2 = sk.add_line(b, c, construction)
    l3 = sk.add_line(c, d, construction)
    l4 = sk.add_line(d, a, construction)
    sk.add_constraint("horizontal", l1)
    sk.add_constraint("horizontal", l3)
    sk.add_constraint("vertical", l2)
    sk.add_constraint("vertical", l4)
    return [l1, l2, l3, l4]


def rect(sk: Sketch, corner, opposite, construction=False):
    """Axis-aligned rectangle between two opposite corners: four lines, two horizontal and two vertical.
    Returns the line ids, from the first corner round to it."""
    (u0, s0), (u1, s1) = _pick(corner), _pick(opposite)
    if np.linalg.norm(u1 - u0) < TOL:
        raise ValueError("Rectangle corners are in the same place")
    a = _pid(sk, u0, s0)
    c = _pid(sk, u1, s1)
    b = sk.add_point(u1[0], u0[1])
    d = sk.add_point(u0[0], u1[1])
    return _four_lines(sk, a, b, c, d, construction)


def center_rect(sk: Sketch, centre, corner, construction=False):
    """Rectangle about `centre` through `corner`. As rect, plus a construction diagonal marked helper with the
    centre as its midpoint, so the centre stays in the middle when a side is dragged. Returns the four line
    ids and then the diagonal's."""
    (u0, s0), (u1, s1) = _pick(centre), _pick(corner)
    if np.linalg.norm(u1 - u0) < TOL:
        raise ValueError("Rectangle corner is at the centre")
    ctr = _pid(sk, u0, s0)
    half = u1 - u0
    a = sk.add_point(*(u0 - half))
    c = _pid(sk, u1, s1)
    b = sk.add_point(u0[0] + half[0], u0[1] - half[1])
    d = sk.add_point(u0[0] - half[0], u0[1] + half[1])
    lines = _four_lines(sk, a, b, c, d, construction)
    diag = sk.add_line(a, c, True)
    sk.entities[diag]["helper"] = True
    sk.add_constraint("midpoint", ctr, diag)
    return lines + [diag]


def circle(sk: Sketch, centre, radius: float, construction=False) -> str:
    """Circle about `centre`. Returns its id."""
    u0, s0 = _pick(centre)
    if radius < 1e-6:
        raise ValueError("Radius is too small")
    return sk.add_circle(_pid(sk, u0, s0), radius, construction)


def arc3(sk: Sketch, start, end, through, construction=False) -> str:
    """Arc between two end picks passing through a third (the order the tool is clicked in). The centre is the
    circumcentre; the ends are swapped when the counter-clockwise arc start-end would miss `through` (a clockwise
    pick). The third pick is kept as a real point, point_on the arc, so the arc stays defined by its three points.
    Returns the arc id."""
    (p1, s1), (p2, s2), (p3, s3) = _pick(start), _pick(end), _pick(through)
    ctr = circumcenter(p1, p2, p3)
    if ctr is None:
        raise ValueError("The three points are in line")
    if not _ccw_contains(ctr, p1, p2, p3):
        p1, s1, p2, s2 = p2, s2, p1, s1
    c = sk.add_point(*ctr)
    a = _pid(sk, p1, s1)
    b = _pid(sk, p2, s2)
    aid = sk.add_arc(c, a, b, construction)
    m = _pid(sk, p3, s3)
    if m not in (a, b, c):
        sk.add_constraint("point_on", m, aid)
        if m not in sk.points or not any(m in sk.entity_points(e) for e in sk.entities):
            sk.add_point_entity(m)
    return aid


def arc_center(sk: Sketch, centre, start, end, sweep: float = 1.0, construction=False) -> str:
    """Arc about `centre` from `start`; `end` only gives the direction of the arc's far end (it is moved onto
    the circle through `start` unless it is an existing point). sweep < 0: the pen went clockwise, so the ends
    swap. Returns the arc id."""
    (cu, cs), (p1, s1), (p2, s2) = _pick(centre), _pick(start), _pick(end)
    r = float(np.linalg.norm(p1 - cu))
    if r < 1e-9:
        raise ValueError("Arc radius is too small")
    v = p2 - cu
    if np.linalg.norm(v) > 1e-9:
        p2 = cu + v / np.linalg.norm(v) * r
    c = _pid(sk, cu, cs)
    a = _pid(sk, p1, s1)
    b = _pid(sk, p2, s2)
    if sweep < 0:
        a, b = b, a
    return sk.add_arc(c, a, b, construction)


# --------------------------------------------------------------------------- extend

def _crossings_along(sk: Sketch, oid, a, d):
    """Parameters t where entity `oid` crosses the infinite line a + t*d, only where the crossing lies on
    the entity itself (a line's own span, an arc's own sweep; a whole circle)."""
    o = sk.entities[oid]
    out = []
    if o["type"] == "line":
        c, e = sk.P(o["p1"]), sk.P(o["p2"])
        f = e - c
        den = d[0] * f[1] - d[1] * f[0]
        if abs(den) < 1e-12:
            return out
        w = c - a
        t = (w[0] * f[1] - w[1] * f[0]) / den
        s = (w[0] * d[1] - w[1] * d[0]) / den
        if -1e-9 <= s <= 1 + 1e-9:
            out.append(t)
    elif o["type"] in ("circle", "arc"):
        c = sk.P(o["c"])
        r = sk.radius(oid)
        f = a - c
        A, B, C = d @ d, 2 * f @ d, f @ f - r * r
        disc = B * B - 4 * A * C
        if disc < 0 or A < 1e-12:
            return out
        for sgn in (-1, 1):
            t = (-B + sgn * math.sqrt(disc)) / (2 * A)
            if o["type"] == "arc":
                a1, a2 = sk.arc_angles(oid)
                pt = a + t * d
                ang = math.atan2(pt[1] - c[1], pt[0] - c[0])
                while ang < a1 - 1e-9:
                    ang += 2 * math.pi
                if ang > a2 + 1e-9:
                    continue
            out.append(t)
    elif o["type"] == "ellipse":
        c, major, r = sk.ellipse_axes(oid)
        ra = float(np.linalg.norm(major))
        if ra < 1e-12 or r < 1e-12:
            return out
        ex = major / ra
        ey = np.array([-ex[1], ex[0]])
        f = a - c
        # in the ellipse's own axes, scaled so it is the unit circle
        p0 = np.array([f @ ex / ra, f @ ey / r])
        dd = np.array([d @ ex / ra, d @ ey / r])
        A, B, C = dd @ dd, 2 * p0 @ dd, p0 @ p0 - 1.0
        disc = B * B - 4 * A * C
        if disc < 0 or A < 1e-12:
            return out
        for sgn in (-1, 1):
            out.append((-B + sgn * math.sqrt(disc)) / (2 * A))
    elif o["type"] == "spline":
        S = sk.polyline(oid)                          # the sampled curve the solver holds points to
        for c, e in zip(S[:-1], S[1:]):
            f = e - c
            den = d[0] * f[1] - d[1] * f[0]
            if abs(den) < 1e-12:
                continue
            w = c - a
            s = (w[0] * d[1] - w[1] * d[0]) / den
            if -1e-9 <= s <= 1 + 1e-9:
                out.append((w[0] * f[1] - w[1] * f[0]) / den)
    return out


def extend(sk: Sketch, lid: str, end: str):
    """Lengthen one end of line `lid` ("p1" or "p2") along its own direction to the nearest crossing with
    another line, circle, arc, ellipse or spline. The end point moves there and gets a point_on constraint to that curve.
    Returns {"point", "target", "from", "to"}, or None (sk untouched) when nothing lies beyond the end.
    Raises ValueError (sk untouched) when the end point is shared with other geometry, or when the
    constraints already on the line, such as a length dimension, would not let the end move."""
    e = sk.entities.get(lid)
    if e is None or e["type"] != "line":
        raise ValueError("Extend works on a line")
    if end not in ("p1", "p2"):
        raise ValueError("end must be p1 or p2")
    pid = e[end]
    a, b = sk.P(e["p1"]), sk.P(e["p2"])
    d = b - a
    L = float(np.linalg.norm(d))
    if L < TOL:
        raise ValueError("That line has no length")
    beyond = []                                       # (distance beyond the end, entity id)
    for oid, o in sk.entities.items():
        if oid == lid or o["type"] == "point":
            continue
        for t in _crossings_along(sk, oid, a, d):
            if end == "p2" and (t - 1) * L > 1e-6:
                beyond.append((t, oid))
            elif end == "p1" and -t * L > 1e-6:
                beyond.append((-t, oid))
    if not beyond:
        return None
    best, oid = min(beyond, key=lambda x: x[0])
    t = best if end == "p2" else -best
    if any(o != lid and pid in sk.entity_points(o) for o in sk.entities):
        raise ValueError("Can't extend: that end is shared with other geometry")
    work = sk.copy()
    old = [float(v) for v in work.points[pid]]
    new = a + t * d
    work.points[pid] = [float(new[0]), float(new[1])]
    work.add_constraint("point_on", pid, oid)
    ok = work.solve()
    moved = np.linalg.norm(work.P(pid) - new)
    drift = max((np.linalg.norm(work.P(q) - sk.P(q)) for q in sk.points if q != pid), default=0.0)
    if not ok or moved > 1e-6 or drift > 1e-6:
        raise ValueError("Can't extend: a dimension or constraint on this line holds its length")
    sk.points, sk.constraints, sk.status = work.points, work.constraints, work.status
    return {"point": pid, "target": oid, "from": old, "to": [float(v) for v in work.P(pid)]}


# --------------------------------------------------------------------------- mirror & patterns

def _geometry(sk: Sketch, ids):
    ents = [i for i in ids if i in sk.entities]
    if not ents:
        raise ValueError("Select the sketch geometry first")
    return ents


def _copy(sk: Sketch, ents, transform, keep=frozenset(), orient_flip=False, copy_cons=()):
    """Copy entities with transform(uv)->uv. Points in `keep` map to themselves."""
    pmap = {}

    def P(pid):
        if pid in keep:
            return pid
        if pid not in pmap:
            pmap[pid] = sk.add_point(*transform(sk.P(pid)))
        return pmap[pid]

    emap = {}
    for eid in ents:
        e = sk.entities[eid]
        c = e.get("construction", False)
        t = e["type"]
        if t == "line":
            emap[eid] = sk.add_line(P(e["p1"]), P(e["p2"]), c)
        elif t == "circle":
            emap[eid] = sk.add_circle(P(e["c"]), e["r"], c)
        elif t == "arc":
            p1, p2 = P(e["p1"]), P(e["p2"])
            emap[eid] = sk.add_arc(P(e["c"]), p2, p1, c) if orient_flip else sk.add_arc(P(e["c"]), p1, p2, c)
        elif t == "point":
            emap[eid] = sk.add_point_entity(P(e["p"]))
        elif t == "spline":
            emap[eid] = sk.add_spline([P(q) for q in e["pts"]], e.get("closed", False), c)
        elif t == "ellipse":
            emap[eid] = sk.add_ellipse(P(e["c"]), P(e["a"]), e["r"], c)
        elif t == "text":
            # text keeps reading left to right: it moves and turns with the copy but is never mirrored
            o = np.asarray(sk.P(e["p"]), float)
            d = np.asarray(transform(o + np.array([1.0, 0.0])), float) - np.asarray(transform(o), float)
            turn = 0.0 if orient_flip else math.degrees(math.atan2(d[1], d[0]))
            vals = {k: e[k] for k in ("text", "size", "font", "bold", "italic") if k in e}
            emap[eid] = sk.add_text(P(e["p"]), angle=e.get("angle", 0.0) + turn, **vals)
    if copy_cons:
        allmap = {**pmap, **emap}
        inside = set(ents) | set(pmap)
        for k in list(sk.constraints):
            if k["type"] in copy_cons and all(r in inside or r in keep for r in k["refs"]) \
                    and any(r in inside for r in k["refs"]):
                sk.add_constraint(k["type"], *[allmap.get(r, r) for r in k["refs"]], value=k.get("value"))
    return pmap, emap


SHAPE_CONS_ROT = ("coincident", "point_on", "parallel", "perpendicular", "equal", "tangent", "concentric", "midpoint")
SHAPE_CONS_LIN = SHAPE_CONS_ROT + ("horizontal", "vertical")


def mirror(sk: Sketch, ids, axis_line: str):
    """Mirror selected geometry about a line, linked with symmetric constraints."""
    ents = [i for i in _geometry(sk, ids) if i != axis_line]
    ax = sk.entities.get(axis_line)
    if ax is None or ax["type"] != "line":
        raise ValueError("Pick a line to mirror about")
    A, B = sk.P(ax["p1"]), sk.P(ax["p2"])
    d = (B - A) / max(np.linalg.norm(B - A), 1e-12)

    def refl(p):
        v = p - A
        return A + 2 * (v @ d) * d - v

    pts = {p for e in ents for p in sk.entity_points(e)}
    on_axis = {p for p in pts if abs((sk.P(p) - A)[0] * d[1] - (sk.P(p) - A)[1] * d[0]) < 1e-7}
    pmap, emap = _copy(sk, ents, refl, keep=frozenset(on_axis), orient_flip=True)
    for old, new in pmap.items():
        sk.add_constraint("symmetric", old, new, axis_line)
    for old, new in emap.items():
        if sk.entities[new]["type"] == "circle":
            sk.add_constraint("equal", old, new)
    sk.solve()
    return list(emap.values())


def linear_pattern(sk: Sketch, ids, direction, count: int, spacing: float):
    ents = _geometry(sk, ids)
    count = int(count)
    if count < 2:
        raise ValueError("Count must be at least 2")
    d = np.asarray(direction, float)
    d = d / max(np.linalg.norm(d), 1e-12)
    new = []
    for k in range(1, count):
        off = d * spacing * k
        _, emap = _copy(sk, ents, lambda p, off=off: p + off, copy_cons=SHAPE_CONS_LIN)
        new += list(emap.values())
    sk.solve()
    return new


def circular_pattern(sk: Sketch, ids, centre, count: int, total_angle: float = 360.0):
    ents = _geometry(sk, ids)
    count = int(count)
    if count < 2:
        raise ValueError("Count must be at least 2")
    c = np.asarray(centre, float)
    full = abs(abs(total_angle) - 360) < 1e-9
    step = math.radians(total_angle) / (count if full else count - 1)
    new = []
    for k in range(1, count):
        a = step * k
        R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
        _, emap = _copy(sk, ents, lambda p, R=R: c + R @ (p - c), copy_cons=SHAPE_CONS_ROT)
        new += list(emap.values())
    sk.solve()
    return new


def extent_along(sk: Sketch, ids, d):
    d = np.asarray(d, float) / max(np.linalg.norm(d), 1e-12)
    vals = []
    for eid in ids:
        if eid in sk.entities:
            vals += [float(p @ d) for p in sk.polyline(eid, 24)]
    return (max(vals) - min(vals)) if vals else 0.0


def pattern_direction(sk: Sketch, ids, line: str, uv):
    """The direction of a linear pattern: the picked line's p2 - p1, turned towards the side `uv` was clicked on
    (away from the middle of the selection), not the way the line happened to be drawn."""
    e = sk.entities[line]
    d = sk.P(e["p2"]) - sk.P(e["p1"])
    pts = np.vstack([o for i in ids for o in sk.polylines(i, 16)])
    if d @ (np.asarray(uv, float) - pts.mean(axis=0)) < 0:
        d = -d
    return d


def pattern_spacing(sk: Sketch, ids, d, floor: float):
    """The spacing a linear pattern opens with: 1.25 times the selection's extent along `d`, at least `floor`."""
    return round(max(extent_along(sk, ids, d) * 1.25, floor), 3)


# --------------------------------------------------------------------------- used (projected) model edges
# The Qt-free half of the sketcher's Use tool (I7.1). The desktop's Sketcher and the engine's `project_edge`
# request both go through these, so an edge is brought into a sketch the same way in both.

def is_fixed(sk: Sketch, pid) -> bool:
    return any(c["type"] == "fix" and c["refs"][0] == pid for c in sk.constraints)


def used_line(sk: Sketch, a, b):
    """The line already projected into the sketch from a part edge running a-b (both ends fixed there), or None."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    tol = 1e-6 * max(1.0, float(np.linalg.norm(b - a)))
    for eid, e in sk.entities.items():
        if e["type"] != "line":
            continue
        p, q = sk.P(e["p1"]), sk.P(e["p2"])
        same = (np.linalg.norm(p - a) < tol and np.linalg.norm(q - b) < tol) or \
               (np.linalg.norm(p - b) < tol and np.linalg.norm(q - a) < tol)
        if same and is_fixed(sk, e["p1"]) and is_fixed(sk, e["p2"]):
            return eid
    return None


def used_circle(sk: Sketch, c, r):
    """The circle already projected from a part edge of centre c and radius r (centre fixed), or None."""
    for eid, e in sk.entities.items():
        if e["type"] == "circle" and abs(sk.radius(eid) - r) < 1e-6 * max(1.0, r) \
                and np.linalg.norm(sk.P(e["c"]) - np.asarray(c, float)) < 1e-6 * max(1.0, r) \
                and is_fixed(sk, e["c"]):
            return eid
    return None


def add_used_line(sk: Sketch, a, b, cons) -> str:
    pa, pb = sk.add_point(*a), sk.add_point(*b)
    eid = sk.add_line(pa, pb, cons)
    sk.add_constraint("fix", pa, value=[float(v) for v in a])
    sk.add_constraint("fix", pb, value=[float(v) for v in b])
    return eid


def edge_src(state, pid, edge, shape):
    """The reference a used edge stores so it follows the part (features.refresh_used_edges), or None when
    the edge has no element name in the model before this sketch (`state`, None when unknown). It is kept only
    when that name finds this same edge there: an edge of a later feature, or of a part with no names, stays
    a frozen copy."""
    if state is None:
        return None
    from . import features as F          # OCP: loaded only when an edge is used, so this module stays numpy-only
    from . import kernel as K
    try:
        ref = F.make_ref(pid, edge, "edge", shape, state.named(pid))
        if not ref.get("name"):
            return None
        _, found = F.resolve(state, ref, "edge")
        a, b = K.edge_info(found), K.edge_info(edge)
    except (K.KernelError, KeyError):
        return None
    tol = 1e-6 * max(1.0, float(a.get("len", 1.0)))
    if a.get("type") != b.get("type") or abs(a["len"] - b["len"]) > tol \
            or float(np.linalg.norm(np.asarray(a["c"], float) - np.asarray(b["c"], float))) > tol:
        return None
    return ref


def add_projected_edge(sk: Sketch, got, cons):
    """Add what `features.project_edge` returned (`got`, not None and not "curve") to the sketch as fixed
    geometry. Returns (entity or point id, reused), `reused` being the id of the entity already projected from
    the same edge (nothing is added then) or None."""
    kind = got[0]
    reused = None
    if kind == "circle":
        c, r = got[1], got[2]
        reused = used_circle(sk, c, r)
        if reused is not None:
            out = reused
        else:
            pc = sk.add_point(*c)
            sk.add_constraint("fix", pc, value=c.tolist())
            out = sk.add_circle(pc, r, cons)
            sk.add_constraint("radius", out, value=r)
    elif kind == "arc":
        c = got[1]                                         # partial circular edge: an arc
        pc = sk.add_point(*c)
        sk.add_constraint("fix", pc, value=c.tolist())
        a, b = sk.add_point(*got[2]), sk.add_point(*got[3])
        out = sk.add_arc(pc, a, b, cons)
        for p_ in (a, b):
            sk.add_constraint("fix", p_, value=list(sk.P(p_)))
    elif kind == "point":
        a = got[1]
        out = sk.add_point(*a)                             # the edge points straight at you: a point
        sk.add_constraint("fix", out, value=a.tolist())
    else:
        a, b = got[1], got[2]                              # a line between the outline's two extreme points
        reused = used_line(sk, a, b)
        out = reused or add_used_line(sk, a, b, cons)
    return out, reused


# --------------------------------------------------------------------------- constraint tools (I7.5)
# One mapping from a constraint tool and the picked items to constraints, used by the desktop's Sketcher and by the
# sketch session of `freeshape serve`. A client sends the tool and the picks; it never copies these rules.

_CURVES = ("circle", "arc")

# Which selections each tool takes, as the kinds of the picked items, in any order: "point", "line", "circle",
# "arc"; "curve" stands for circle or arc, and a trailing "+" for one or more of that kind. Read off the branches of
# constraints_for. Where the old card text and the code differed, the code is followed:
#   horizontal / vertical also take several lines (each made so); concentric also takes a point with a curve and two
#   points; there is no point_on tool (coincident makes it); symmetric also takes three lines (the third is the axis) and a circle with an arc; fix takes any items.
FITS = {
    "coincident": [("point", "point"), ("point", "line"), ("point", "curve"), ("curve", "curve")],
    "horizontal": [("line+",), ("point", "point")],
    "vertical": [("line+",), ("point", "point")],
    "parallel": [("line", "line")],
    "perpendicular": [("line", "line")],
    "tangent": [("line", "curve"), ("curve", "curve")],
    "equal": [("line", "line"), ("curve", "curve")],
    "midpoint": [("point", "line")],
    "concentric": [("curve", "curve"), ("point", "curve"), ("point", "point")],
    "symmetric": [("point", "point", "line"), ("curve", "curve", "line"), ("line", "line", "line")],
    "fix": [("any+",)],
    "normal": [("line", "curve")],
}
FIT_TOOLS = tuple(FITS)


def sketch_etype(sk: Sketch, i):
    """The kind of item `i`: "point" (a point or a point entity), an entity's type, or None."""
    if i in sk.points:
        return "point"
    if i in sk.entities:
        return sk.entities[i]["type"]
    return None


def _as_point(sk: Sketch, i):
    if i in sk.entities and sk.entities[i]["type"] == "point":
        return sk.entities[i]["p"]
    return i


def _shape_fits(kinds, row):
    """Does the multiset of `kinds` match `row` (see FITS)?"""
    kinds = list(kinds)
    if len(row) == 1 and row[0].endswith("+"):
        base = row[0][:-1]
        return bool(kinds) and all(base == "any" or k == base or (base == "curve" and k in _CURVES) for k in kinds)
    if len(kinds) != len(row):
        return False
    rest = list(kinds)
    for want in sorted(row, key=lambda w: w == "curve"):          # exact kinds first, then "curve"
        for k in rest:
            if k == want or (want == "curve" and k in _CURVES):
                rest.remove(k)
                break
        else:
            return False
    return True


def constraints_for(sk: Sketch, tool: str, picks) -> list:
    """What the constraint `tool` makes of the picked items (point ids, entity ids), as a list of
    (type, refs, value) to give to `apply_constraints`; empty when the picks do not fit the tool. Pure: `sk` is
    only read. `picks` are already resolved to points and entities (the desktop resolves a picked constraint to
    its point first), without repeats. The rules:
      coincident   two points; a point and a line or curve is point_on; two curves are concentric
      tangent      line and curve; two curves carry value 1 (external) or -1 (internal, centre distance < a radius)
      fix          every point of the item at its place (not the origin), and a circle's radius
      normal       point_on of the curve's centre on the line, flagged "normal" by `apply_constraints`
      symmetric    the axis is the third pick (else the last line); two curves also become equal"""
    picks = list(picks)
    types = [sketch_etype(sk, p) for p in picks]
    curves = _CURVES
    made = []
    if tool == "fix" and picks:
        for p, t in zip(picks, types):
            if t is None:
                continue
            for pid in ([_as_point(sk, p)] if t == "point" else sk.entity_points(p)):
                if pid != ORIGIN:
                    made.append(("fix", [pid], list(sk.points[pid])))
            if p in sk.entities and sk.entities[p]["type"] == "circle":
                made.append(("radius", [p], sk.entities[p]["r"]))
    elif tool in ("horizontal", "vertical") and picks and all(t == "line" for t in types):
        for p in picks:                        # one or several lines: each one made horizontal / vertical
            made.append((tool, [p], None))
    elif len(picks) == 2:
        a, b = picks
        ta, tb = types
        if tool == "coincident":
            if ta == "point" and tb == "point":
                made.append(("coincident", [_as_point(sk, a), _as_point(sk, b)], None))
            elif ta == "point" and tb in ("line",) + curves:
                made.append(("point_on", [_as_point(sk, a), b], None))
            elif tb == "point" and ta in ("line",) + curves:
                made.append(("point_on", [_as_point(sk, b), a], None))
            elif ta in curves and tb in curves:
                made.append(("concentric", [a, b], None))
        elif tool in ("horizontal", "vertical") and ta == "point" and tb == "point":
            made.append((tool, [_as_point(sk, a), _as_point(sk, b)], None))
        elif tool in ("parallel", "perpendicular") and ta == "line" and tb == "line":
            made.append((tool, [a, b], None))
        elif tool == "equal" and ((ta == tb == "line") or (ta in curves and tb in curves)):
            made.append(("equal", [a, b], None))
        elif tool == "concentric" and ta in curves + ("point",) and tb in curves + ("point",):
            made.append(("concentric", [a if ta in curves else _as_point(sk, a),
                                        b if tb in curves else _as_point(sk, b)], None))
        elif tool == "midpoint":
            if ta == "point" and tb == "line":
                made.append(("midpoint", [_as_point(sk, a), b], None))
            elif tb == "point" and ta == "line":
                made.append(("midpoint", [_as_point(sk, b), a], None))
        elif tool == "tangent":
            if (ta == "line" and tb in curves) or (tb == "line" and ta in curves) or (ta in curves and tb in curves):
                val = None
                if ta in curves and tb in curves:
                    d = np.linalg.norm(sk.P(sk.entities[a]["c"]) - sk.P(sk.entities[b]["c"]))
                    internal = d < max(sk.radius(a), sk.radius(b))
                    val = -1.0 if internal else 1.0
                if tb == "line":
                    a, b = b, a                 # stored line first, so the solver always takes it
                made.append(("tangent", [a, b], val))
    if tool == "normal" and len(picks) == 2:
        a, b = picks
        ta, tb = types
        if ta == "line" and tb in curves:
            a, b = b, a
        elif not (tb == "line" and ta in curves):
            a = None
        if a is not None:
            # point-on-line residual on the curve's centre, flagged so the badge reads "normal"
            made.append(("point_on", [sk.entities[a]["c"], b], None))
    if tool == "symmetric" and len(picks) == 3:
        lines_ = [p for p, t in zip(picks, types) if t == "line"]
        axis = picks[2] if types[2] == "line" else (lines_[-1] if lines_ else None)
        others = [(p, t) for p, t in zip(picks, types) if p != axis]
        if axis and len(others) == 2:
            (a, ta), (b, tb) = others
            if ta == "point" and tb == "point":
                made.append(("symmetric", [_as_point(sk, a), _as_point(sk, b), axis], None))
            elif ta in curves and tb in curves:
                made.append(("symmetric", [sk.entities[a]["c"], sk.entities[b]["c"], axis], None))
                made.append(("equal", [a, b], None))
            elif ta == "line" and tb == "line":
                ea, eb = sk.entities[a], sk.entities[b]
                A0, A1 = sk.P(sk.entities[axis]["p1"]), sk.P(sk.entities[axis]["p2"])
                d = (A1 - A0) / max(np.linalg.norm(A1 - A0), 1e-12)
                refl = lambda q: A0 + 2 * ((q - A0) @ d) * d - (q - A0)
                straight = np.linalg.norm(refl(sk.P(ea["p1"])) - sk.P(eb["p1"]))
                crossed = np.linalg.norm(refl(sk.P(ea["p1"])) - sk.P(eb["p2"]))
                q1, q2 = (eb["p1"], eb["p2"]) if straight <= crossed else (eb["p2"], eb["p1"])
                made.append(("symmetric", [ea["p1"], q1, axis], None))
                made.append(("symmetric", [ea["p2"], q2, axis], None))
    return made


def fits(sk: Sketch, selection) -> list:
    """The constraint tools (FIT_TOOLS order) that take this selection: its shape is a row of FITS and
    `constraints_for` makes something of it."""
    sel = list(dict.fromkeys(selection))
    kinds = [sketch_etype(sk, p) for p in sel]
    if not sel or None in kinds:
        return []
    return [t for t, rows in FITS.items()
            if any(_shape_fits(kinds, r) for r in rows) and constraints_for(sk, t, sel)]


def _hv_pair(sk: Sketch, refs):
    """The two points a horizontal / vertical constraint lines up (from a line, or two points), unordered."""
    if len(refs) == 1 and refs[0] in sk.entities and sk.entities[refs[0]].get("type") == "line":
        e = sk.entities[refs[0]]
        return frozenset((e["p1"], e["p2"]))
    return frozenset(refs[:2])


def _swing_to(sk: Sketch, pair, t):
    """Turn two points about their midpoint so they line up horizontally / vertically, keeping their distance.
    Without this a vertical line made horizontal is squashed to a dot (the smallest move that satisfies the
    solver), instead of being turned."""
    a, b = sorted(pair)
    if ORIGIN in (a, b) or a not in sk.points or b not in sk.points:
        return
    A, B = sk.P(a), sk.P(b)
    d = B - A
    L = float(np.linalg.norm(d))
    along = abs(d[0]) if t == "horizontal" else abs(d[1])
    across = abs(d[1]) if t == "horizontal" else abs(d[0])
    if L < 1e-9 or along >= across:
        return                              # already more that way than the other: the solver just nudges it
    m = (A + B) / 2
    sgn = 1.0 if (d[1] if t == "horizontal" else d[0]) >= 0 else -1.0
    half = np.array([sgn * L / 2, 0.0]) if t == "horizontal" else np.array([0.0, sgn * L / 2])
    sk.points[a] = (m - half).tolist()
    sk.points[b] = (m + half).tolist()


def apply_constraints(sk: Sketch, tool: str, made) -> list:
    """Add what `constraints_for` returned to `sk` (the caller solves). A line can't be both horizontal and
    vertical: one replaces the other, and asking again for what it already is changes nothing. Returns the new
    constraint dicts."""
    out = []
    for t, refs, val in made:
        if t in ("horizontal", "vertical"):
            pair = _hv_pair(sk, refs)
            other = "vertical" if t == "horizontal" else "horizontal"
            if any(c["type"] == t and _hv_pair(sk, c["refs"]) == pair for c in sk.constraints):
                continue
            sk.constraints = [c for c in sk.constraints
                              if not (c["type"] == other and _hv_pair(sk, c["refs"]) == pair)]
            _swing_to(sk, pair, t)
        c = sk.add_constraint(t, *refs, value=val)
        if tool == "normal":
            c["normal"] = True
        out.append(c)
    return out


def new_relations(sk: Sketch, made) -> list:
    """`made` (from `constraints_for`) without the relations `sk` already holds: a pair has at most one relation of
    each kind (Sketch.find_constraint). Empty when every one is already there. The desktop and the served sketch
    session both filter with this."""
    return [m for m in made if sk.find_constraint(m[0], m[1]) is None]


def unmet(sk: Sketch, ids) -> set:
    """Which of the constraints `ids` the last solve of `sk` did not meet: the ones it reports bad (they fight what
    is there) and the ones it skipped (no equation for that pair). A new constraint in this set is rolled back."""
    st = sk.status or {}
    return set(ids) & (set(st.get("bad", ())) | set(st.get("skipped", ())))


# --------------------------------------------------------------------------- dimensions and trim (I7.6)
# Moved out of the desktop's Sketcher (_dim_spec, _trim); the desktop and the sketch session of `freeshape serve` both
# call these.

def _cross2(a, b):
    return float(a[0] * b[1] - a[1] * b[0])


def _pt_line_dist(p, a, b):
    d = b - a
    n = np.linalg.norm(d)
    if n < 1e-12:
        return float(np.linalg.norm(p - a))
    return float(abs(d[0] * (p - a)[1] - d[1] * (p - a)[0]) / n)


def _intersections_with_line(sk, oid, a, b):
    """Parameters t in [0,1] along segment a->b where entity oid crosses it."""
    o = sk.entities[oid]
    d = b - a
    out = []
    if o["type"] == "line":
        c, e = sk.P(o["p1"]), sk.P(o["p2"])
        f = e - c
        den = d[0] * f[1] - d[1] * f[0]
        if abs(den) < 1e-12:
            return out
        w = c - a
        t = (w[0] * f[1] - w[1] * f[0]) / den
        s = (w[0] * d[1] - w[1] * d[0]) / den
        if -1e-9 <= s <= 1 + 1e-9:
            out.append(t)
    elif o["type"] in ("circle", "arc"):
        c = sk.P(o["c"])
        r = sk.radius(oid)
        f = a - c
        A = d @ d
        B = 2 * f @ d
        C = f @ f - r * r
        disc = B * B - 4 * A * C
        if disc < 0 or A < 1e-12:
            return out
        for sgn in (-1, 1):
            t = (-B + sgn * math.sqrt(disc)) / (2 * A)
            pt = a + t * d
            if o["type"] == "arc":
                a1, a2 = sk.arc_angles(oid)
                ang = math.atan2(pt[1] - c[1], pt[0] - c[0])
                while ang < a1:
                    ang += 2 * math.pi
                if ang > a2:
                    continue
            out.append(t)
    return out


def dim_spec(sk: Sketch, a, b, pos):
    """(type, refs, current value) of the dimension between picks `a` and `b` (ids of points or entities; `b` None
    for a dimension of one item) with its label at `pos` (u, v) or None, or None when the picks take no dimension.
    Two points far apart in both directions take a horizontal or vertical distance when `pos` lies beside their box."""
    def etype(i):
        if i in sk.points:
            return "point"
        if i in sk.entities:
            t = sk.entities[i]["type"]
            return "point" if t == "point" else t
        return None

    def as_point(i):
        if i in sk.entities and sk.entities[i]["type"] == "point":
            return sk.entities[i]["p"]
        return i

    ta = etype(a)
    tb = etype(b) if b else None
    ctype, refs, val = None, None, None
    if b is None:
        if ta == "line":
            ctype, refs = "distance", [a]
            e = sk.entities[a]
            val = float(np.linalg.norm(sk.P(e["p2"]) - sk.P(e["p1"])))
        elif ta == "circle":
            ctype, refs, val = "diameter", [a], 2 * sk.radius(a)
        elif ta == "arc":
            ctype, refs, val = "radius", [a], sk.radius(a)
        elif ta == "ellipse":           # half its width; the length is set with its dashed axis line
            ctype, refs, val = "radius", [a], sk.radius(a)
        else:
            return None
    else:
        def ptof(i, t):
            if t == "point":
                return as_point(i)
            if t in ("circle", "arc", "ellipse"):
                return sk.entities[i]["c"]
            if t == "text":
                return sk.entities[i]["p"]          # where the text starts
            return None
        if ta == "line" and tb == "line":
            e1, e2 = sk.entities[a], sk.entities[b]
            d1 = sk.P(e1["p2"]) - sk.P(e1["p1"])
            d2 = sk.P(e2["p2"]) - sk.P(e2["p1"])
            cr = abs(_cross2(d1 / np.linalg.norm(d1), d2 / np.linalg.norm(d2)))
            if cr < 1e-3:
                ctype, refs = "distance", [e2["p1"], a]
                val = _pt_line_dist(sk.P(e2["p1"]), sk.P(e1["p1"]), sk.P(e1["p2"]))
            else:
                ctype, refs = "angle", [a, b]
                dt = (d1 @ d2) / (np.linalg.norm(d1) * np.linalg.norm(d2))
                val = math.degrees(math.atan2(cr, dt))
        elif "line" in (ta, tb):
            line, other, to = (a, b, tb) if ta == "line" else (b, a, ta)
            p = ptof(other, to)
            if p is None:
                return None
            e = sk.entities[line]
            ctype, refs = "distance", [p, line]
            val = _pt_line_dist(sk.P(p), sk.P(e["p1"]), sk.P(e["p2"]))
        else:
            pa, pb = ptof(a, ta), ptof(b, tb)
            if pa is None or pb is None:
                return None
            A, B = sk.P(pa), sk.P(pb)
            lo, hi = np.minimum(A, B), np.maximum(A, B)
            ctype = "distance"
            if pos is not None and abs(A[0] - B[0]) > 1e-6 and abs(A[1] - B[1]) > 1e-6:
                if lo[0] <= pos[0] <= hi[0] and not (lo[1] <= pos[1] <= hi[1]):
                    ctype = "hdistance"
                elif lo[1] <= pos[1] <= hi[1] and not (lo[0] <= pos[0] <= hi[0]):
                    ctype = "vdistance"
            refs = [pa, pb]
            val = {"distance": np.linalg.norm(A - B), "hdistance": abs(A[0] - B[0]),
                   "vdistance": abs(A[1] - B[1])}[ctype]
    if ctype is None:
        return None
    return ctype, refs, val


def trim(sk: Sketch, eid: str, uv):
    """Trim the entity `eid` at the point `uv` (u, v): a line is cut back to its neighbouring intersections with the
    other lines, circles and arcs, on each side of `uv`, and what is left of it stays as one or two lines (with the
    old end points where they remain); a circle, arc or anything else that is not a line is deleted whole. The
    solve is left to the caller. Returns {"removed": eid, "made": [ids of the lines left]}."""
    e = sk.entities[eid]
    if e["type"] != "line":
        sk.delete([eid])
        return {"removed": eid, "made": []}
    uv = np.asarray(uv, float)
    a, b = sk.P(e["p1"]), sk.P(e["p2"])
    d = b - a
    ts = []
    for oid, o in sk.entities.items():
        if oid == eid or o["type"] == "point":
            continue
        for t in _intersections_with_line(sk, oid, a, b):
            if 1e-6 < t < 1 - 1e-6:
                ts.append(t)
    t_click = float(np.clip(((uv - a) @ d) / max(d @ d, 1e-12), 0, 1))
    lo = max([t for t in ts if t < t_click], default=0.0)
    hi = min([t for t in ts if t > t_click], default=1.0)
    p1, p2 = e["p1"], e["p2"]
    cons = e.get("construction", False)
    sk.delete([eid])
    made = []
    if lo > 1e-6:
        q = sk.add_point(*(a + lo * d))
        if p1 not in sk.points:
            p1 = sk.add_point(*a)
        made.append(sk.add_line(p1, q, cons))
    if hi < 1 - 1e-6:
        q = sk.add_point(*(a + hi * d))
        if p2 not in sk.points:
            p2 = sk.add_point(*b)
        made.append(sk.add_line(q, p2, cons))
    return {"removed": eid, "made": made}
