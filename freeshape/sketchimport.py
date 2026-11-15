"""Put imported curves into a sketch: scale and place, weld, de-duplicate, close.

No Qt. The SVG and DXF readers hand `insert` a neutral list of primitives in millimetres, y up. Each
primitive is a dict with a "type", an optional "layer" (a layer or group name) and these keys, all
points being (x, y):

  line       p1, p2
  arc        c, p1, p2            counter-clockwise from p1 to p2
  circle     c, r
  ellipse    c, a, r              a = end of the major axis (absolute), r = minor radius
  bezier     pts                  4 points: start, two controls, end
  fitspline  pts, closed          a smooth curve through the points

`insert(sk, prims, ...)` adds sketch entities and returns (entity ids, report). It adds no constraints:
topology comes from end points that share one sketch point id, which makes closure exact. Beziers
become lines (when flat) or interpolating splines, one per smooth run of a chain; a chain is split
wherever the tangent turns by more than CORNER_DEG. Beziers are never turned into arcs.

Chains are read in list order: a bezier continues the previous one when its start welds to the
previous end. The readers emit the segments of a path in path order.

Placement order: scale, rotate about the file origin, then (use_file_origin False) move the centre of
the bounding box to the origin, then add `origin`. The bounding box of a spline is taken from its
through-points.
"""
from __future__ import annotations

import math

import numpy as np

CORNER_DEG = 1.0            # a tangent turn above this splits a bezier chain
SAMPLES_PER_BEZIER = 16     # error check: samples per bezier against the fitted spline
START_INTERIOR = 2          # interior spline points per bezier to begin with
MAX_INTERIOR = 16           # stop refining here and report the remaining error
_DENSE = 24                 # polyline samples per spline span when measuring the error


# ---------------------------------------------------------------------------------------- placement
def _xform_pt(p, s, ca, sa):
    x, y = p[0] * s, p[1] * s
    return (x * ca - y * sa, x * sa + y * ca)


def _transform(prims, scale, rotation):
    s = float(scale)
    if not s > 0:
        raise ValueError("scale must be positive")
    a = math.radians(rotation)
    ca, sa = math.cos(a), math.sin(a)
    out = []
    for pr in prims:
        t = pr["type"]
        q = dict(pr)
        for k in ("p1", "p2", "c", "a"):
            if k in q:
                q[k] = _xform_pt(q[k], s, ca, sa)
        if "pts" in q:
            q["pts"] = [_xform_pt(p, s, ca, sa) for p in q["pts"]]
        if "r" in q:
            q["r"] = float(q["r"]) * s
        if t not in ("line", "arc", "circle", "ellipse", "bezier", "fitspline"):
            raise ValueError(f"unknown primitive type {t!r}")
        out.append(q)
    return out


def _bbox(prims):
    pts = []
    for pr in prims:
        t = pr["type"]
        if t == "line":
            pts += [pr["p1"], pr["p2"]]
        elif t == "circle":
            cx, cy = pr["c"]
            r = pr["r"]
            pts += [(cx - r, cy - r), (cx + r, cy + r)]
        elif t == "arc":
            c = np.array(pr["c"], float)
            p1, p2 = np.array(pr["p1"], float), np.array(pr["p2"], float)
            r = float(np.hypot(*(p1 - c)))
            a1 = math.atan2(*(p1 - c)[::-1])
            a2 = math.atan2(*(p2 - c)[::-1])
            while a2 <= a1 + 1e-12:
                a2 += 2 * math.pi
            angs = [a1, a2] + [k * math.pi / 2 for k in range(-8, 9) if a1 < k * math.pi / 2 < a2]
            pts += [(c[0] + r * math.cos(x), c[1] + r * math.sin(x)) for x in angs]
        elif t == "ellipse":
            c = np.array(pr["c"], float)
            A = np.array(pr["a"], float) - c
            n = float(np.hypot(*A))
            B = pr["r"] * np.array([-A[1], A[0]]) / n if n > 0 else np.zeros(2)
            hx, hy = math.hypot(A[0], B[0]), math.hypot(A[1], B[1])
            pts += [(c[0] - hx, c[1] - hy), (c[0] + hx, c[1] + hy)]
        elif t == "bezier":
            pts += list(_bez_samples(pr["pts"], 16))
        else:
            pts += list(pr["pts"])
    if not pts:
        return None
    a = np.asarray(pts, float)
    return a.min(axis=0), a.max(axis=0)


# ---------------------------------------------------------------------------------------- bezier maths
def _bez_samples(P, n):
    P = np.asarray(P, float)
    t = np.linspace(0.0, 1.0, n + 1)[:, None]
    u = 1 - t
    return u ** 3 * P[0] + 3 * u ** 2 * t * P[1] + 3 * u * t ** 2 * P[2] + t ** 3 * P[3]


def _bez_flat(P, tol):
    """Both controls within tol of the chord and their projections inside it."""
    P = np.asarray(P, float)
    d = P[3] - P[0]
    L = float(np.hypot(*d))
    if L < 1e-12:
        return float(np.max(np.hypot(*(P[1:3] - P[0]).T))) <= tol
    u = d / L
    for q in P[1:3]:
        v = q - P[0]
        s = float(v @ u)
        if abs(float(u[0] * v[1] - u[1] * v[0])) > tol or s < -tol or s > L + tol:
            return False
    return True


def _tangent(P, end):
    """Unit tangent at the start (end=False) or the end (end=True) of a cubic; None if all points coincide."""
    P = np.asarray(P, float)
    order = ((3, 2), (3, 1), (3, 0)) if end else ((1, 0), (2, 0), (3, 0))
    for a, b in order:
        v = P[a] - P[b]
        n = float(np.hypot(*v))
        if n > 1e-12:
            return v / n
    return None


def _turn_deg(t_out, t_in):
    if t_out is None or t_in is None:
        return 0.0
    return math.degrees(math.acos(max(-1.0, min(1.0, float(t_out @ t_in)))))


def _seg_dist(pts, a, b):
    """Distance from each of pts (n, 2) to the polyline segments a -> b (m, 2 each)."""
    ab = b - a
    L2 = np.maximum((ab ** 2).sum(1), 1e-30)
    best = np.full(len(pts), np.inf)
    for i in range(0, len(pts), 256):
        p = pts[i:i + 256]
        w = p[:, None, :] - a[None, :, :]
        t = np.clip((w * ab[None]).sum(2) / L2[None], 0.0, 1.0)
        d = w - t[..., None] * ab[None]
        best[i:i + 256] = np.sqrt((d ** 2).sum(2)).min(1)
    return best


def _fit_run(segs, closed, tol):
    """Interpolating spline for one smooth run of cubics (each 4 points). Returns (points, closed,
    max error, capped): the through-points are the run's join points plus interior points, refined
    until SAMPLES_PER_BEZIER samples per bezier lie within tol of the spline or MAX_INTERIOR is hit."""
    from . import kernel as K
    m = len(segs)
    k = [START_INTERIOR] * m
    tests = [_bez_samples(s, SAMPLES_PER_BEZIER) for s in segs]
    lens = np.array([max(float(np.hypot(*np.diff(np.asarray(s, float), axis=0).T).sum()), 1e-9)
                     for s in segs])
    cum = np.concatenate([[0.0], np.cumsum(lens)])
    total = cum[-1]
    err_seg = np.zeros(m)
    while True:
        pts = []
        for i, s in enumerate(segs):
            pts.append(tuple(s[0]))
            for j in range(1, k[i] + 1):
                pts.append(tuple(_bez_samples(s, k[i] + 1)[j]))
        if not closed:
            pts.append(tuple(segs[-1][3]))
        curve = K.spline_curve_2d(pts, closed)
        if curve is None:
            return pts, closed, float("inf"), True
        n = _DENSE * len(pts)
        ts = np.linspace(curve.FirstParameter(), curve.LastParameter(), n + 1)
        dense = np.array([(curve.Value(t).X(), curve.Value(t).Y()) for t in ts])
        dl = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(dense, axis=0).T))])
        frac = dl / max(dl[-1], 1e-12)
        for i in range(m):
            lo = (cum[i] - 2.5 * lens[i]) / total
            hi = (cum[i + 1] + 2.5 * lens[i]) / total
            sel = np.nonzero((frac >= lo) & (frac <= hi))[0]
            if closed or len(sel) < 2:          # a closed run wraps round: compare against all of it
                sel = np.arange(len(dense))
            else:
                sel = np.arange(max(sel[0] - 1, 0), min(sel[-1] + 2, len(dense)))
            err_seg[i] = float(_seg_dist(tests[i], dense[sel][:-1], dense[sel][1:]).max())
        worst = [i for i in range(m) if err_seg[i] > tol and k[i] < MAX_INTERIOR]
        if not worst:
            return pts, closed, float(err_seg.max()), bool((err_seg > tol).any())
        for i in worst:
            k[i] = min(k[i] * 2, MAX_INTERIOR)


# ---------------------------------------------------------------------------------------- welding
class _Welder:
    """Clusters points on a grid of cell size `weld`; a point joins the first cluster within `weld` of it."""

    def __init__(self, weld):
        self.w = max(float(weld), 1e-12)
        self.cells: dict = {}
        self.xy: list = []

    def __call__(self, p):
        w = self.w
        ix, iy = int(math.floor(p[0] / w)), int(math.floor(p[1] / w))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for ci in self.cells.get((ix + dx, iy + dy), ()):
                    q = self.xy[ci]
                    if math.hypot(q[0] - p[0], q[1] - p[1]) <= w:
                        return ci
        self.xy.append((float(p[0]), float(p[1])))
        self.cells.setdefault((ix, iy), []).append(len(self.xy) - 1)
        return len(self.xy) - 1


def _arc_centre(p1, p2, c):
    """The centre moved onto the perpendicular bisector of p1 p2, so |p1 - c| = |p2 - c|."""
    p1, p2, c = np.asarray(p1, float), np.asarray(p2, float), np.asarray(c, float)
    mid = (p1 + p2) / 2
    d = p2 - p1
    L = float(np.hypot(*d))
    n = np.array([-d[1], d[0]]) / L
    return mid + float((c - mid) @ n) * n


def _sweep(p1, p2, c):
    a1 = math.atan2(p1[1] - c[1], p1[0] - c[0])
    a2 = math.atan2(p2[1] - c[1], p2[0] - c[0])
    while a2 <= a1 + 1e-9:
        a2 += 2 * math.pi
    return a1, a2


# ---------------------------------------------------------------------------------------- insert
def insert(sk, prims, *, scale=1.0, origin=(0, 0), rotation=0.0, use_file_origin=False,
           weld=0.001, tol=0.01, construction=()):
    """Add `prims` to sketch `sk`. Returns (entity ids, report). See the module docstring.

    The report is a dict: counts by primitive type ("input"), entities made ("made"), "dropped_short",
    "dropped_duplicate", "open_ends" (sketch point ids used by exactly one curve end),
    "splines" (one dict per fitted run: id, points, error, capped), "spline_max_error", "bbox"
    ((x0, y0, x1, y1) of the placed geometry) and "points" (new sketch point ids)."""
    construction = set(construction or ())
    prims = _transform(prims, scale, rotation)
    report = {"input": {}, "made": {}, "dropped_short": 0, "dropped_duplicate": 0, "open_ends": [],
              "splines": [], "spline_max_error": 0.0, "bbox": None, "points": []}
    for pr in prims:
        report["input"][pr["type"]] = report["input"].get(pr["type"], 0) + 1
    bb = _bbox(prims)
    if bb is None:
        return [], report
    shift = np.array(origin, float) - ((bb[0] + bb[1]) / 2 if not use_file_origin else 0.0)
    shift = np.asarray(shift, float)

    def mv(p):
        return (p[0] + shift[0], p[1] + shift[1])

    for pr in prims:
        for k in ("p1", "p2", "c", "a"):
            if k in pr:
                pr[k] = mv(pr[k])
        if "pts" in pr:
            pr["pts"] = [mv(p) for p in pr["pts"]]
    weld = float(weld)
    cl = _Welder(weld)

    # ---- phase 1: curves as records. ends = cluster ids (welded); "own" points are not welded.
    recs = []                       # {"type", "layer", "ends": (a, b) | None, "mid": (x, y), ...}
    seen: dict = {}                 # (type, ends) -> [mids] ; circles/ellipses use their own list

    def is_dup(kind, ends, mid):
        key = (kind, frozenset(ends) if ends else None)
        for m in seen.get(key, ()):
            if math.hypot(m[0] - mid[0], m[1] - mid[1]) <= weld:
                return True
        seen.setdefault(key, []).append(mid)
        return False

    def add(rec, kind, ends, mid):
        if is_dup(kind, ends, mid):
            report["dropped_duplicate"] += 1
        else:
            recs.append(rec)

    def add_line(layer, a, b):
        pa, pb = cl.xy[a], cl.xy[b]
        if a == b or math.hypot(pa[0] - pb[0], pa[1] - pb[1]) < weld:
            report["dropped_short"] += 1
            return
        add({"type": "line", "layer": layer, "ends": (a, b)}, "line", (a, b),
            ((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2))

    def add_spline(layer, ends, pts, closed):
        n = len(pts)
        mid = tuple((np.asarray(pts[(n - 1) // 2], float) + np.asarray(pts[n // 2], float)) / 2)
        add({"type": "spline", "layer": layer, "ends": ends, "pts": pts, "closed": closed},
            "spline", ends if not closed else None, mid)

    chain: list = []                # bezier records of the current chain: (layer, P, ca, cb)

    def flush():
        if not chain:
            return
        _bezier_chain(list(chain))
        chain.clear()

    def _bezier_chain(ch):
        layer = ch[0][0]
        segs = [c[1] for c in ch]
        n = len(ch)
        turn = []                   # turn[i]: at the join before segment i (0 = wrap)
        for i in range(n):
            prev = segs[i - 1]
            turn.append(_turn_deg(_tangent(prev, True), _tangent(segs[i], False)))
        wrap_closed = ch[-1][3] == ch[0][2]
        corner = [turn[i] > CORNER_DEG for i in range(n)]
        if not wrap_closed:
            corner[0] = True
        runs = []                   # (list of seg indices, closed)
        if wrap_closed and not any(corner):
            runs.append((list(range(n)), True))
        else:
            order = list(range(n))
            if wrap_closed:         # start the walk at a corner so no run straddles the wrap
                first = corner.index(True)
                order = order[first:] + order[:first]
            cur = []
            for i in order:
                if corner[i] and cur:
                    runs.append((cur, False))
                    cur = []
                cur.append(i)
            runs.append((cur, False))
        for idxs, closed in runs:
            sg = [segs[i] for i in idxs]
            a, b = ch[idxs[0]][2], ch[idxs[-1]][3]
            span = np.asarray(sg, float).reshape(-1, 2)
            if a == b and not closed and np.ptp(span, axis=0).max() < weld:
                report["dropped_short"] += 1
                continue
            if len(sg) == 1 and _bez_flat(sg[0], tol):
                add_line(layer, a, b)
                continue
            if a == b and not closed:
                closed = True       # a loop that returns to its own start: a closed spline
            pts, closed, err, capped = _fit_run(sg, closed, tol)
            if closed and np.hypot(*(np.asarray(pts[0]) - np.asarray(pts[-1]))) < 1e-9 and len(pts) > 1:
                pts = pts[:-1]
            # the run's end points are the welded cluster points, so shared ends share a sketch point
            pts = list(pts)
            if not closed:
                pts[0], pts[-1] = cl.xy[a], cl.xy[b]
            add_spline(layer, (a, b) if not closed else (a, a), pts, closed)
            rec = recs[-1] if recs and recs[-1].get("pts") is pts else None
            report["splines"].append({"points": len(pts), "error": err, "capped": capped,
                                      "_rec": rec})
            report["spline_max_error"] = max(report["spline_max_error"], err)

    for pr in prims:
        t = pr["type"]
        layer = pr.get("layer")
        if t != "bezier":
            flush()
        if t == "line":
            add_line(layer, cl(pr["p1"]), cl(pr["p2"]))
        elif t == "arc":
            a, b = cl(pr["p1"]), cl(pr["p2"])
            p1, p2 = cl.xy[a], cl.xy[b]
            c = pr["c"]
            r1 = math.hypot(p1[0] - c[0], p1[1] - c[1])
            chord = math.hypot(p1[0] - p2[0], p1[1] - p2[1])
            if a == b or chord < 1e-12:
                _, a2 = _sweep(p1, pr["p2"], c)
                a1 = math.atan2(p1[1] - c[1], p1[0] - c[0])
                if r1 * (a2 - a1) < weld:
                    report["dropped_short"] += 1
                    continue
                # both ends weld to one point yet the arc is long: it is a full circle
                add({"type": "circle", "layer": layer, "c": tuple(c), "r": r1}, "circle", None,
                    (c[0] + r1, c[1]))
                continue
            cc = tuple(_arc_centre(p1, p2, c))
            a1, a2 = _sweep(p1, p2, cc)
            r = math.hypot(p1[0] - cc[0], p1[1] - cc[1])
            if chord < weld and r * (a2 - a1) < weld:
                report["dropped_short"] += 1
                continue
            am = (a1 + a2) / 2
            add({"type": "arc", "layer": layer, "ends": (a, b), "c": cc}, "arc", (a, b),
                (cc[0] + r * math.cos(am), cc[1] + r * math.sin(am)))
        elif t == "circle":
            r = float(pr["r"])
            if 2 * math.pi * r < weld:
                report["dropped_short"] += 1
                continue
            add({"type": "circle", "layer": layer, "c": tuple(pr["c"]), "r": r}, "circle", None,
                (pr["c"][0] + r, pr["c"][1]))
        elif t == "ellipse":
            A = (pr["a"][0] - pr["c"][0], pr["a"][1] - pr["c"][1])
            if math.hypot(*A) < weld or pr["r"] < weld:
                report["dropped_short"] += 1
                continue
            add({"type": "ellipse", "layer": layer, "c": tuple(pr["c"]), "a": tuple(pr["a"]),
                 "r": float(pr["r"])}, "ellipse", None, (pr["a"][0], pr["a"][1]))
        elif t == "bezier":
            P = [tuple(p) for p in pr["pts"]]
            ca, cb = cl(P[0]), cl(P[3])
            if chain and (chain[-1][3] != ca or chain[-1][0] != layer):
                flush()
            if _bez_flat(P, tol):
                flush()
                add_line(layer, ca, cb)
                continue
            chain.append((layer, P, ca, cb))
        elif t == "fitspline":
            flush()
            pts = [tuple(p) for p in pr["pts"]]
            if len(pts) < 2:
                report["dropped_short"] += 1
                continue
            a, b = cl(pts[0]), cl(pts[-1])
            closed = bool(pr.get("closed"))
            if a == b and len(pts) > 2:
                closed = True
                pts = pts[:-1]
            if len(pts) == 2 and not closed:
                add_line(layer, a, b)
                continue
            if not closed:
                pts[0], pts[-1] = cl.xy[a], cl.xy[b]
            add_spline(layer, (a, b) if not closed else (a, a), pts, closed)
    flush()

    # ---- phase 2: sketch entities. One sketch point per cluster that a kept curve uses.
    pid: dict = {}

    def cpoint(ci):
        if ci not in pid:
            pid[ci] = sk.add_point(*cl.xy[ci])
            report["points"].append(pid[ci])
        return pid[ci]

    def own(xy):
        q = sk.add_point(*xy)
        report["points"].append(q)
        return q

    ids = []
    ends_used: dict = {}
    spline_ent = {}
    for rec in recs:
        cons = rec.get("layer") in construction if rec.get("layer") is not None else False
        t = rec["type"]
        if t == "line":
            a, b = rec["ends"]
            eid = sk.add_line(cpoint(a), cpoint(b), construction=cons)
        elif t == "arc":
            a, b = rec["ends"]
            eid = sk.add_arc(own(rec["c"]), cpoint(a), cpoint(b), construction=cons)
        elif t == "circle":
            eid = sk.add_circle(own(rec["c"]), rec["r"], construction=cons)
        elif t == "ellipse":
            eid = sk.add_ellipse(own(rec["c"]), own(rec["a"]), rec["r"], construction=cons)
        else:
            a, b = rec["ends"]
            pts = rec["pts"]
            if rec["closed"]:
                q = [cpoint(a)] + [own(p) for p in pts[1:]]
            else:
                q = [cpoint(a)] + [own(p) for p in pts[1:-1]] + [cpoint(b)]
            eid = sk.add_spline(q, closed=rec["closed"], construction=cons)
            spline_ent[id(rec)] = eid
        ids.append(eid)
        report["made"][t] = report["made"].get(t, 0) + 1
        if rec.get("ends") and not (t == "spline" and rec["closed"]):
            for ci in rec["ends"]:
                ends_used[ci] = ends_used.get(ci, 0) + 1
    for s in report["splines"]:
        rec = s.pop("_rec", None)
        if rec is not None and id(rec) in spline_ent:
            s["id"] = spline_ent[id(rec)]
    report["open_ends"] = [pid[ci] for ci, n in ends_used.items() if n == 1]
    xy = np.array([sk.points[q] for q in report["points"]]) if report["points"] else np.zeros((0, 2))
    if len(xy):
        report["bbox"] = (float(xy[:, 0].min()), float(xy[:, 1].min()),
                          float(xy[:, 0].max()), float(xy[:, 1].max()))
    return ids, report
