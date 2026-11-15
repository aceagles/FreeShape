"""Read a DXF file into neutral primitives for `sketchimport.insert`. No Qt; built on ezdxf.

    prims, report = read(path_or_text, *, units=None, points=False)

`path_or_text` is a file path, or the text of a DXF file (anything containing a newline). The primitives
are in millimetres, y up, in the format `sketchimport` documents: line, arc (counter-clockwise from p1
to p2), circle, ellipse (a = absolute end of the major axis, r = minor radius), bezier (4 points) and
fitspline (pts, closed), each with the entity's layer name under "layer". With points=True a POINT
entity gives {"type": "point", "p": (x, y), "layer": ...}; `sketchimport.insert` has no such type, so
a caller that asks for points places them itself.

Model space only. INSERT blocks are exploded. LINE, CIRCLE, ARC, LWPOLYLINE and 2D POLYLINE use the
entity's OCS, so an extrusion of (0, 0, -1) mirrors an arc. Polyline bulges become arcs. A full ELLIPSE
is an ellipse; a partial one and a SPLINE with control points go through `ezdxf.path.make_path` to
beziers. A SPLINE with fit points only is a fitspline through them, checked against `make_path`; extra
points are added when it strays more than TOL_MM.

Units: `$INSUNITS`, else `$MEASUREMENT` (0 inches, 1 millimetres), else the `units` argument (a name
such as "mm" or "in", or an ezdxf INSUNITS code; default mm).

Skipped and counted in report["skipped"]: TEXT, MTEXT, DIMENSION, HATCH, IMAGE, 3D entities, paper
space, entities on frozen layers, entities not parallel to the XY plane, and kinds not listed here.

The report: "units" ({"source", "scale"}), "read" (entities seen, by DXF type), "prims" (by type),
"skipped" ({kind: count}), "layers" (layer names that carry a primitive), "recovered" (True when the
tag stream was damaged and ezdxf.recover was used) and "spline_checks" (one dict per fit spline:
points, error in mm).
"""
from __future__ import annotations

import io
import math
import os

TOL_MM = 0.01               # a fit spline may stray this far from ezdxf's own curve
_MAX_INTERIOR = 15          # extra points per span when refining a fit spline
_FLAT = 0.005               # flattening distance (mm) of the reference curve in the fit check
_UNIT_NAMES = {"mm": 4, "millimeter": 4, "millimetre": 4, "millimeters": 4, "millimetres": 4,
               "cm": 5, "centimeter": 5, "centimetre": 5, "m": 6, "meter": 6, "metre": 6,
               "in": 1, "inch": 1, "inches": 1, "ft": 2, "foot": 2, "feet": 2,
               "mi": 3, "mile": 3, "km": 7, "kilometer": 7, "kilometre": 7,
               "yd": 10, "yard": 10, "um": 13, "micron": 13, "dm": 14, "decimeter": 14}
_SKIP_NAMES = {"TEXT": "TEXT", "MTEXT": "MTEXT", "ATTRIB": "TEXT", "ATTDEF": "TEXT",
               "DIMENSION": "DIMENSION", "LEADER": "DIMENSION", "MLEADER": "DIMENSION",
               "HATCH": "HATCH", "IMAGE": "IMAGE", "WIPEOUT": "IMAGE", "OLE2FRAME": "IMAGE",
               "3DFACE": "3D", "SOLID": "3D", "TRACE": "3D", "MESH": "3D", "3DSOLID": "3D",
               "BODY": "3D", "REGION": "3D", "SURFACE": "3D", "POLYFACE": "3D"}


def _unit_code(units):
    if units is None:
        return 4
    if isinstance(units, str):
        try:
            return _UNIT_NAMES[units.strip().lower()]
        except KeyError:
            raise ValueError(f"unknown units {units!r}") from None
    return int(units)


def _load(src):
    """Open the document; returns (doc, recovered). ValueError('Not a readable DXF file') when neither
    ezdxf.readfile nor ezdxf.recover can read it."""
    import ezdxf
    from ezdxf import recover
    is_text = isinstance(src, str) and ("\n" in src or "\r" in src)
    if not is_text and not os.path.exists(os.fspath(src)):
        raise FileNotFoundError(os.fspath(src))
    try:
        if is_text:
            return ezdxf.read(io.StringIO(src)), False
        return ezdxf.readfile(os.fspath(src)), False
    except (ezdxf.DXFError, OSError, UnicodeDecodeError, ValueError, KeyError, IndexError, TypeError,
            AttributeError):
        pass
    try:
        if is_text:
            doc, _auditor = recover.read(io.BytesIO(src.encode("utf-8", "replace")))
        else:
            doc, _auditor = recover.readfile(os.fspath(src))
        return doc, True
    except Exception:
        raise ValueError("Not a readable DXF file") from None


def _scale(doc, units):
    """(factor to mm, source name)."""
    from ezdxf import units as U
    code = int(doc.header.get("$INSUNITS", 0) or 0)
    if code != 0:
        try:
            return U.conversion_factor(code, U.MM), "INSUNITS"
        except Exception:
            pass
    meas = doc.header.get("$MEASUREMENT")
    if meas is not None:
        return U.conversion_factor(U.IN if int(meas) == 0 else U.MM, U.MM), "MEASUREMENT"
    return U.conversion_factor(_unit_code(units), U.MM), "argument"


def _xy_ocs(ocs, v):
    w = ocs.to_wcs(v)
    return (float(w.x), float(w.y))


def _parallel_xy(ez, tol=1e-9):
    """+1 or -1 when the extrusion is (0, 0, +-1), else 0."""
    if abs(ez[0]) > tol or abs(ez[1]) > tol:
        return 0
    return 1 if ez[2] > 0 else -1


class _Reader:
    def __init__(self, scale, want_points):
        self.k = scale
        self.want_points = want_points
        self.prims = []
        self.skipped = {}
        self.spline_checks = []

    def skip(self, kind):
        self.skipped[kind] = self.skipped.get(kind, 0) + 1

    def pt(self, p):
        return (p[0] * self.k, p[1] * self.k)

    def add(self, layer, **kw):
        self.prims.append({"layer": layer, **kw})

    def line(self, layer, a, b):
        self.add(layer, type="line", p1=self.pt(a), p2=self.pt(b))

    def arc(self, layer, c, p1, p2, mirrored):
        """c, p1, p2 in WCS xy; (p1, p2) run counter-clockwise in the entity's OCS, so clockwise in the
        sketch when the OCS is mirrored."""
        if mirrored:
            p1, p2 = p2, p1
        self.add(layer, type="arc", c=self.pt(c), p1=self.pt(p1), p2=self.pt(p2))

    # ---------------------------------------------------------------- entities
    def entity(self, e):
        from ezdxf.math import Vec3
        t = e.dxftype()
        layer = e.dxf.layer if e.dxf.hasattr("layer") else "0"
        if t in _SKIP_NAMES:
            return self.skip(_SKIP_NAMES[t])
        if t == "LINE":
            a, b = Vec3(e.dxf.start), Vec3(e.dxf.end)
            if abs(a.z - b.z) > 1e-9:
                return self.skip("not parallel to XY")
            return self.line(layer, a, b)
        if t in ("CIRCLE", "ARC"):
            ez = tuple(e.dxf.extrusion)
            s = _parallel_xy(ez)
            if not s:
                return self.skip("not parallel to XY")
            ocs = e.ocs()
            c, r = Vec3(e.dxf.center), float(e.dxf.radius)
            if t == "CIRCLE":
                return self.add(layer, type="circle", c=self.pt(_xy_ocs(ocs, c)), r=r * self.k)
            a0, a1 = math.radians(e.dxf.start_angle), math.radians(e.dxf.end_angle)
            p1 = c + Vec3(r * math.cos(a0), r * math.sin(a0), 0)
            p2 = c + Vec3(r * math.cos(a1), r * math.sin(a1), 0)
            return self.arc(layer, _xy_ocs(ocs, c), _xy_ocs(ocs, p1), _xy_ocs(ocs, p2), s < 0)
        if t == "LWPOLYLINE":
            s = _parallel_xy(tuple(e.dxf.extrusion))
            if not s:
                return self.skip("not parallel to XY")
            pts = [(x, y, b) for x, y, b in e.get_points("xyb")]
            return self.polyline(layer, e.ocs(), pts, bool(e.closed), s < 0)
        if t == "POLYLINE":
            if not e.is_2d_polyline:
                return self.skip("3D")
            s = _parallel_xy(tuple(e.dxf.extrusion))
            if not s:
                return self.skip("not parallel to XY")
            pts = [(v.dxf.location.x, v.dxf.location.y, v.dxf.bulge if v.dxf.hasattr("bulge") else 0.0)
                   for v in e.vertices if not v.is_face_record and not v.is_poly_face_mesh_vertex]
            return self.polyline(layer, e.ocs(), pts, bool(e.is_closed), s < 0)
        if t == "ELLIPSE":
            return self.ellipse(layer, e)
        if t == "SPLINE":
            return self.spline(layer, e)
        if t == "POINT":
            if not self.want_points:
                return self.skip("POINT")
            p = Vec3(e.dxf.location)
            return self.add(layer, type="point", p=self.pt((p.x, p.y)))
        return self.skip(t)

    def polyline(self, layer, ocs, pts, closed, mirrored):
        from ezdxf.math import Vec2, Vec3, bulge_to_arc
        n = len(pts)
        for i in range(n if closed else n - 1):
            x0, y0, b = pts[i]
            x1, y1, _ = pts[(i + 1) % n]
            a, bb = Vec2(x0, y0), Vec2(x1, y1)
            if b and a != bb:
                c, _s, _e, _r = bulge_to_arc(a, bb, b)
                wa, wb = _xy_ocs(ocs, Vec3(a)), _xy_ocs(ocs, Vec3(bb))
                wc = _xy_ocs(ocs, Vec3(c))
                if b > 0:               # counter-clockwise from the first vertex to the second
                    self.arc(layer, wc, wa, wb, mirrored)
                else:
                    self.arc(layer, wc, wb, wa, mirrored)
            else:
                self.line(layer, _xy_ocs(ocs, Vec3(a)), _xy_ocs(ocs, Vec3(bb)))

    def ellipse(self, layer, e):
        from ezdxf.math import Vec3
        if not _parallel_xy(tuple(e.dxf.extrusion)):
            return self.skip("not parallel to XY")
        c, A = Vec3(e.dxf.center), Vec3(e.dxf.major_axis)
        if abs(A.z) > 1e-9:
            return self.skip("not parallel to XY")
        s, t = float(e.dxf.start_param), float(e.dxf.end_param)
        span = (t - s) % (2 * math.pi)
        if span < 1e-9 or abs(span - 2 * math.pi) < 1e-9:
            return self.add(layer, type="ellipse", c=self.pt((c.x, c.y)),
                            a=self.pt((c.x + A.x, c.y + A.y)), r=float(e.dxf.ratio) * A.magnitude * self.k)
        self.via_path(layer, e)

    def via_path(self, layer, e):
        from ezdxf import path as P
        try:
            p = P.make_path(e)
        except Exception:
            return self.skip(e.dxftype())
        self.path_prims(layer, p)

    def path_prims(self, layer, p):
        from ezdxf.path import Command
        cur = p.start
        for cmd in p.commands():
            if cmd.type == Command.MOVE_TO:
                cur = cmd.end
            elif cmd.type == Command.LINE_TO:
                self.line(layer, cur, cmd.end)
                cur = cmd.end
            elif cmd.type == Command.CURVE4_TO:
                self.bez(layer, cur, cmd.ctrl1, cmd.ctrl2, cmd.end)
                cur = cmd.end
            elif cmd.type == Command.CURVE3_TO:
                q = cmd.ctrl
                self.bez(layer, cur, cur + (q - cur) * (2 / 3), cmd.end + (q - cmd.end) * (2 / 3), cmd.end)
                cur = cmd.end

    def bez(self, layer, a, b, c, d):
        self.add(layer, type="bezier", pts=[self.pt(a), self.pt(b), self.pt(c), self.pt(d)])

    def spline(self, layer, e):
        ctl = [tuple(v) for v in e.control_points]
        fit = [tuple(v) for v in e.fit_points]
        zs = [v[2] for v in (ctl or fit)]
        if not zs:
            return self.skip("SPLINE")
        if max(zs) - min(zs) > 1e-9:
            return self.skip("not parallel to XY")
        if ctl:
            return self.via_path(layer, e)
        if len(fit) < 2:
            return self.skip("SPLINE")
        closed = bool(e.closed)
        pts = self.fit_points(e, fit, closed)
        self.add(layer, type="fitspline", pts=[self.pt(p) for p in pts], closed=closed)

    def fit_points(self, e, fit, closed):
        """The fit points, plus points taken from make_path's curve where an interpolating spline through
        the fit points alone strays more than TOL_MM (in mm) from it."""
        import numpy as np
        try:
            from ezdxf import path as P
            from . import kernel as K
            from .sketchimport import _seg_dist
            ref = np.array([(v.x, v.y) for v in P.make_path(e).flattening(_FLAT / self.k)]) * self.k
        except Exception:
            return [p[:2] for p in fit]
        base = np.array([p[:2] for p in fit], float) * self.k
        idx = [int(np.argmin(np.hypot(*(ref - q).T))) for q in base]
        if closed:
            idx.append(len(ref) - 1)
        spans = len(idx) - 1
        if spans < 1 or any(b <= a for a, b in zip(idx, idx[1:])):
            return [p[:2] for p in fit]
        err = 0.0
        for extra in (0, 1, 3, 7, _MAX_INTERIOR):
            pts = []
            for i in range(spans if closed else spans + 1):
                pts.append(tuple(base[i]))
                if i < spans:
                    pts += [tuple(ref[idx[i] + (idx[i + 1] - idx[i]) * j // (extra + 1)])
                            for j in range(1, extra + 1)
                            if idx[i] + (idx[i + 1] - idx[i]) * j // (extra + 1) not in (idx[i], idx[i + 1])]
            try:
                curve = K.spline_curve_2d(pts, closed)
            except Exception:
                curve = None
            if curve is None:
                break
            ts = np.linspace(curve.FirstParameter(), curve.LastParameter(), 24 * len(pts) + 1)
            dense = np.array([(curve.Value(t).X(), curve.Value(t).Y()) for t in ts])
            err = float(_seg_dist(ref, dense[:-1], dense[1:]).max())
            if err <= TOL_MM:
                break
        self.spline_checks.append({"points": len(pts), "error": err})
        return [(x / self.k, y / self.k) for x, y in pts]


def read(path_or_text, *, units=None, points=False):
    """Read a DXF file. Returns (prims, report); see the module docstring."""
    from ezdxf import disassemble
    doc, recovered = _load(path_or_text)
    scale, source = _scale(doc, units)
    msp = doc.modelspace()
    rd = _Reader(scale, points)
    frozen = {l.dxf.name for l in doc.layers if l.is_frozen()}
    seen = {}
    for e in msp:
        t = e.dxftype()
        seen[t] = seen.get(t, 0) + 1
        if e.dxf.hasattr("layer") and e.dxf.layer in frozen:
            rd.skip("frozen layer")
            continue
        try:
            if t == "INSERT":
                for v in disassemble.recursive_decompose([e]):
                    rd.entity(v)
            else:
                rd.entity(e)
        except Exception:
            rd.skip(f"{t} (unreadable)")
    for lay in doc.layouts:
        if not lay.is_modelspace:
            n = sum(1 for _ in lay)
            if n:
                rd.skipped["paper space"] = rd.skipped.get("paper space", 0) + n
    counts = {}
    for p in rd.prims:
        counts[p["type"]] = counts.get(p["type"], 0) + 1
    report = {"units": {"source": source, "scale": scale}, "read": seen, "prims": counts,
              "skipped": rd.skipped, "layers": sorted({p["layer"] for p in rd.prims}),
              "recovered": recovered, "spline_checks": rd.spline_checks}
    return rd.prims, report
