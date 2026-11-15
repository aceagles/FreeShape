"""Read an SVG document into neutral sketch primitives. No Qt.

`read(text, *, ppi=None)` returns `(prims, report)`. The primitives are in millimetres, y up, in the
format `freeshape.sketchimport.insert` takes (see that module's docstring): line, arc, circle, ellipse
and bezier. Each carries a "layer": the id or Inkscape label of the top-level group it sits in, else "0".

What is read: svg (nested too), g, a, use (with x, y; recursion limit 32, a cycle is an error), the full
transform stack (matrix, translate, scale, rotate with an optional centre, skewX, skewY) and the shapes
path, rect (with rx, ry), circle, ellipse, line, polyline, polygon. Path data goes through
fontTools.svgLib.path.parse_path with a recording pen and our own arc class, so arcs arrive exact.

Arcs: the W3C endpoint-to-centre conversion. A circular arc stays a native arc under a similarity
transform (uniform scale, rotation, reflection); under any other matrix it becomes cubic Beziers in
45 degree pieces. Quadratics are raised to cubics exactly. The y flip reverses direction, so an arc is
emitted counter-clockwise from p1 to p2 by swapping its ends when needed.

Units: a root width or height in an absolute unit plus a viewBox gives millimetres per user unit.
Otherwise a user unit is a px at `ppi`; ppi=None picks 90 for an Inkscape version attribute before 0.92
and 96 otherwise. The origin is the viewBox top-left (0, 0 without one), after the y flip.

Skipped and counted in report["skipped"]: text, image, clipPath, mask, pattern, gradients, symbols,
display:none, unreferenced defs and unknown elements. Fill and stroke are ignored: a stroked path imports
as its centre line, and the report says so when a file has strokes and no fills.
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET

import numpy as np

try:
    from fontTools.svgLib.path import parse_path as _parse_path
except ImportError:  # pragma: no cover - fontTools is a declared dependency
    _parse_path = None

MAX_USE_DEPTH = 32
MAX_NEST = 200
_ERR = "Not a readable SVG file"
_SVG = "{http://www.w3.org/2000/svg}"
_XLINK = "{http://www.w3.org/1999/xlink}"
_INK = "{http://www.inkscape.org/namespaces/inkscape}"
_MM_PER_UNIT = {"mm": 1.0, "cm": 10.0, "in": 25.4}   # px, pt and pc follow the ppi
_USER_PER_UNIT = {"": 1.0, "px": 1.0, "pt": 4 / 3, "pc": 16.0, "in": 96.0, "cm": 96 / 2.54, "mm": 96 / 25.4}
_SKIP_TAGS = {"text": "text", "image": "image", "clipPath": "clipPath", "mask": "mask", "pattern": "pattern",
              "linearGradient": "gradient", "radialGradient": "gradient", "meshgradient": "gradient",
              "symbol": "symbol", "foreignObject": "foreignObject", "filter": "filter", "marker": "marker"}
_QUIET_TAGS = {"title", "desc", "metadata", "style", "script", "namedview", "RDF", "comment"}
_NUM = r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?"
_LEN_RE = re.compile(rf"^\s*({_NUM})\s*([a-zA-Z%]*)\s*$")


def _fail(why):
    return ValueError(f"{_ERR}: {why}")


# ---------------------------------------------------------------------------------------- matrices
def _mat(a, b, c, d, e, f):
    return np.array([[a, c, e], [b, d, f], [0.0, 0.0, 1.0]])


def _parse_transform(s):
    """The matrix of a transform attribute; the list is applied right to left, as in SVG."""
    m = np.eye(3)
    if not s or not s.strip():
        return m
    pos = 0
    for mo in re.finditer(r"\s*,?\s*([A-Za-z]+)\s*\(([^)]*)\)", s):
        if s[pos:mo.start()].strip(" ,\t\r\n"):
            raise _fail(f"bad transform {s!r}")
        pos = mo.end()
        name = mo.group(1)
        try:
            v = [float(x) for x in re.findall(_NUM, mo.group(2))]
        except ValueError:
            raise _fail(f"bad transform {s!r}") from None
        n = len(v)
        if name == "matrix" and n == 6:
            t = _mat(*v)
        elif name == "translate" and n in (1, 2):
            t = _mat(1, 0, 0, 1, v[0], v[1] if n == 2 else 0.0)
        elif name == "scale" and n in (1, 2):
            t = _mat(v[0], 0, 0, v[1] if n == 2 else v[0], 0, 0)
        elif name == "rotate" and n in (1, 3):
            a = math.radians(v[0])
            r = _mat(math.cos(a), math.sin(a), -math.sin(a), math.cos(a), 0, 0)
            t = r if n == 1 else _mat(1, 0, 0, 1, v[1], v[2]) @ r @ _mat(1, 0, 0, 1, -v[1], -v[2])
        elif name == "skewX" and n == 1:
            t = _mat(1, 0, math.tan(math.radians(v[0])), 1, 0, 0)
        elif name == "skewY" and n == 1:
            t = _mat(1, math.tan(math.radians(v[0])), 0, 1, 0, 0)
        else:
            raise _fail(f"bad transform {s!r}")
        m = m @ t
    if s[pos:].strip(" ,\t\r\n"):
        raise _fail(f"bad transform {s!r}")
    return m


def _pt(m, p):
    return (m[0, 0] * p[0] + m[0, 1] * p[1] + m[0, 2], m[1, 0] * p[0] + m[1, 1] * p[1] + m[1, 2])


def _similarity(m):
    """Scale factor if the linear part of m is a uniform scale times a rotation or reflection, else None."""
    a = m[:2, :2]
    s2 = abs(float(np.linalg.det(a)))
    if s2 < 1e-24:
        return None
    return math.sqrt(s2) if np.allclose(a.T @ a, s2 * np.eye(2), rtol=0, atol=1e-9 * s2) else None


# ---------------------------------------------------------------------------------------- arcs
def _arc_params(p1, rx, ry, phi_deg, large, sweep, p2):
    """W3C F.6.5. Returns None to drop (equal ends), ("line",) for a zero radius, else
    ("arc", c, rx, ry, phi, theta1, dtheta) in SVG space with radii corrected."""
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2 and y1 == y2:
        return None
    rx, ry = abs(rx), abs(ry)
    if rx == 0 or ry == 0:
        return ("line",)
    phi = math.radians(phi_deg)
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p, y1p = cp * dx + sp * dy, -sp * dx + cp * dy
    lam = x1p ** 2 / rx ** 2 + y1p ** 2 / ry ** 2
    if lam > 1:
        rx *= math.sqrt(lam)
        ry *= math.sqrt(lam)
    num = rx ** 2 * ry ** 2 - rx ** 2 * y1p ** 2 - ry ** 2 * x1p ** 2
    den = rx ** 2 * y1p ** 2 + ry ** 2 * x1p ** 2
    k = math.sqrt(max(0.0, num / den)) if den > 0 else 0.0
    if bool(large) == bool(sweep):
        k = -k
    cxp, cyp = k * rx * y1p / ry, -k * ry * x1p / rx
    cx = cp * cxp - sp * cyp + (x1 + x2) / 2
    cy = sp * cxp + cp * cyp + (y1 + y2) / 2
    ux, uy = (x1p - cxp) / rx, (y1p - cyp) / ry
    vx, vy = (-x1p - cxp) / rx, (-y1p - cyp) / ry
    th1 = math.atan2(uy, ux)
    dth = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
    if not sweep and dth > 0:
        dth -= 2 * math.pi
    elif sweep and dth < 0:
        dth += 2 * math.pi
    return ("arc", (cx, cy), rx, ry, phi, th1, dth)


def _arc_beziers(c, rx, ry, phi, th1, dth, p_start, p_end):
    """Cubic Beziers for an elliptical arc, pieces of at most 45 degrees; the outer ends are the exact
    path points."""
    n = max(1, math.ceil(abs(dth) / (math.pi / 4) - 1e-9))
    d = dth / n
    k = 4 / 3 * math.tan(d / 4)
    cp, sp = math.cos(phi), math.sin(phi)

    def pos(t):
        x, y = rx * math.cos(t), ry * math.sin(t)
        return (c[0] + cp * x - sp * y, c[1] + sp * x + cp * y)

    def der(t):
        x, y = -rx * math.sin(t), ry * math.cos(t)
        return (cp * x - sp * y, sp * x + cp * y)

    out = []
    for i in range(n):
        a, b = th1 + i * d, th1 + (i + 1) * d
        p0 = p_start if i == 0 else pos(a)
        p3 = p_end if i == n - 1 else pos(b)
        da, db = der(a), der(b)
        pa, pb = pos(a), pos(b)
        out.append([p0, (pa[0] + k * da[0], pa[1] + k * da[1]), (pb[0] - k * db[0], pb[1] - k * db[1]), p3])
    return out


class _Arc:
    """Stands in for fontTools' arc class: remembers the arc instead of flattening it."""

    def __init__(self, current_point, rx, ry, rotation, large, sweep, target_point):
        self.args = ((current_point.real, current_point.imag), rx, ry, rotation, large, sweep,
                     (target_point.real, target_point.imag))

    def draw(self, pen):
        pen.arc_segment(*self.args)


class _Recorder:
    """A pen that lists the path's segments in user space: ("L", a, b), ("C", p0, c1, c2, p3),
    ("A", p1, rx, ry, rotation, large, sweep, p2). It has no arcTo, so parse_path uses _Arc."""

    def __init__(self):
        self.segs = []
        self.cur = None

    def moveTo(self, p):
        self.cur = p

    def lineTo(self, p):
        self.segs.append(("L", self.cur, p))
        self.cur = p

    def curveTo(self, c1, c2, p):
        self.segs.append(("C", self.cur, c1, c2, p))
        self.cur = p

    def qCurveTo(self, *pts):
        if len(pts) != 2:
            raise ValueError("unexpected quadratic")
        q, p = pts
        p0 = self.cur
        self.segs.append(("C", p0, (p0[0] + 2 / 3 * (q[0] - p0[0]), p0[1] + 2 / 3 * (q[1] - p0[1])),
                          (p[0] + 2 / 3 * (q[0] - p[0]), p[1] + 2 / 3 * (q[1] - p[1])), p))
        self.cur = p

    def arc_segment(self, p1, rx, ry, rotation, large, sweep, p2):
        self.segs.append(("A", p1, rx, ry, rotation, large, sweep, p2))
        self.cur = p2

    def closePath(self):
        pass

    def endPath(self):
        pass


# ---------------------------------------------------------------------------------------- the reader
def _length(s, default=0.0):
    """A shape attribute as user units (px = 1)."""
    if s is None:
        return default
    mo = _LEN_RE.match(s)
    if not mo or mo.group(2) not in _USER_PER_UNIT:
        return default
    return float(mo.group(1)) * _USER_PER_UNIT[mo.group(2)]


def _fmt(v):
    return format(float(v), ".17g")


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _style(el):
    out = {}
    st = el.get("style")
    if st:
        for part in st.split(";"):
            if ":" in part:
                k, v = part.split(":", 1)
                out[k.strip()] = v.strip()
    return out


class _Reader:
    def __init__(self, root, ppi):
        self.root = root
        self.prims = []
        self.skipped = {}
        self.counts = {}
        self.filled = 0
        self.stroked = 0
        self.notes = []
        self.used_ids = set()
        self.ids = {}
        for el in root.iter():
            i = el.get("id")
            if i and i not in self.ids:
                self.ids[i] = el
        self.ppi, self.ppi_source = ppi, "argument"
        self.inkscape_version = root.get(_INK + "version")
        if ppi is None:
            self.ppi, self.ppi_source = 96.0, "default"
            mo = re.match(r"\s*(\d+)\.(\d+)", self.inkscape_version or "")
            if mo and (int(mo.group(1)), int(mo.group(2))) < (0, 92):
                self.ppi, self.ppi_source = 90.0, "inkscape before 0.92"

    def skip(self, kind):
        self.skipped[kind] = self.skipped.get(kind, 0) + 1

    def count(self, kind):
        self.counts[kind] = self.counts.get(kind, 0) + 1

    # ---- units
    def root_scale(self):
        """(mm per user unit, unit source, viewBox x, viewBox y)."""
        root = self.root
        px_mm = 25.4 / self.ppi
        vb = self.viewbox(root)
        mm = {"px": px_mm, "": px_mm, "pt": 4 / 3 * px_mm, "pc": 16 * px_mm, **_MM_PER_UNIT}
        dims = []
        for name in ("width", "height"):
            mo = _LEN_RE.match(root.get(name) or "")
            if mo and mo.group(2) in mm:
                dims.append((name, float(mo.group(1)) * mm[mo.group(2)], mo.group(2)))
        if vb and dims:
            ratios = []
            for name, size, unit in dims:
                ratios.append(size / (vb[2] if name == "width" else vb[3]))
            s = min(ratios)
            if len(ratios) == 2 and abs(ratios[0] - ratios[1]) > 1e-6 * max(ratios):
                if "none" in (root.get("preserveAspectRatio") or ""):
                    self.notes.append("preserveAspectRatio is none with unequal width and height scales; "
                                      "the smaller scale was used for both axes")
                else:
                    self.notes.append("width and height scale the viewBox unequally; the smaller scale was used")
            return s, "viewBox", vb[0], vb[1]
        return px_mm, f"px at {self.ppi:g} ppi", (vb[0] if vb else 0.0), (vb[1] if vb else 0.0)

    @staticmethod
    def viewbox(el):
        v = el.get("viewBox")
        if not v:
            return None
        try:
            x, y, w, h = [float(t) for t in re.findall(_NUM, v)][:4]
        except ValueError:
            return None
        return (x, y, w, h) if w > 0 and h > 0 else None

    # ---- walking
    def walk(self, el, m, props, depth, layer, stack, top):
        if depth > MAX_NEST:
            raise _fail("elements nested too deeply")
        tag = _local(el.tag)
        st = _style(el)
        if (el.get("display") or st.get("display") or "").strip() == "none":
            self.skip("display:none")
            return
        props = dict(props)
        for k in ("fill", "stroke"):
            v = st.get(k, el.get(k))
            if v is not None:
                props[k] = v.strip()
        m = m @ _parse_transform(el.get("transform"))

        if tag in ("svg", "g", "a", "switch"):
            if tag == "svg" and depth > 0:
                m = m @ self.nested_svg(el)
            for ch in el:
                if not isinstance(ch.tag, str):
                    continue
                lay = layer
                if layer is None and _local(ch.tag) == "g":
                    lay = ch.get(_INK + "label") or ch.get("id") or "0"
                self.walk(ch, m, props, depth + 1, lay, stack, False)
        elif tag == "defs":
            for ch in el:
                if not isinstance(ch.tag, str):
                    continue
                cl = _local(ch.tag)
                if cl in _SKIP_TAGS:
                    self.skip(_SKIP_TAGS[cl])
                elif ch.get("id") not in self.used_ids and cl not in _QUIET_TAGS:
                    self._unreferenced.append(ch)
        elif tag == "use":
            self.use(el, m, props, depth, layer, stack)
        elif tag in ("path", "rect", "circle", "ellipse", "line", "polyline", "polygon"):
            self.shape(el, tag, m, props, layer or "0")
        elif tag in _SKIP_TAGS:
            self.skip(_SKIP_TAGS[tag])
        elif tag not in _QUIET_TAGS:
            self.skip(tag)

    def nested_svg(self, el):
        x, y = _length(el.get("x")), _length(el.get("y"))
        vb = self.viewbox(el)
        w, h = _length(el.get("width"), 0.0), _length(el.get("height"), 0.0)
        if vb and w > 0 and h > 0:
            s = min(w / vb[2], h / vb[3])
            return _mat(1, 0, 0, 1, x, y) @ _mat(s, 0, 0, s, 0, 0) @ _mat(1, 0, 0, 1, -vb[0], -vb[1])
        return _mat(1, 0, 0, 1, x, y)

    def use(self, el, m, props, depth, layer, stack):
        ref = el.get("href") or el.get(_XLINK + "href") or ""
        if not ref.startswith("#"):
            self.skip("use")
            return
        rid = ref[1:]
        target = self.ids.get(rid)
        if target is None:
            self.skip("use")
            return
        if rid in stack:
            raise _fail(f"use cycle through #{rid}")
        if len(stack) >= MAX_USE_DEPTH:
            raise _fail("use elements nested more than 32 deep")
        self.used_ids.add(rid)
        self.count("use")
        m = m @ _mat(1, 0, 0, 1, _length(el.get("x")), _length(el.get("y")))
        tag = _local(target.tag)
        if tag == "symbol":      # a used symbol draws its children like a group
            for ch in target:
                if isinstance(ch.tag, str):
                    self.walk(ch, m, props, depth + 1, layer, stack + [rid], False)
        else:
            self.walk(target, m, props, depth + 1, layer, stack + [rid], False)

    # ---- shapes
    def shape(self, el, tag, m, props, layer):
        self.count(tag)
        if props.get("fill", "black") not in ("none", "transparent"):
            self.filled += 1
        if props.get("stroke", "none") != "none":
            self.stroked += 1
        if tag == "path":
            self.path(el.get("d") or "", m, layer)
        elif tag == "rect":
            x, y = _length(el.get("x")), _length(el.get("y"))
            w, h = _length(el.get("width")), _length(el.get("height"))
            if w <= 0 or h <= 0:
                self.skip("empty rect")
                return
            rx, ry = el.get("rx"), el.get("ry")
            rx = _length(rx, -1.0) if rx is not None else -1.0
            ry = _length(ry, -1.0) if ry is not None else -1.0
            if rx < 0 and ry < 0:
                rx = ry = 0.0
            elif rx < 0:
                rx = ry
            elif ry < 0:
                ry = rx
            rx, ry = min(rx, w / 2), min(ry, h / 2)
            if rx > 0 and ry > 0:
                f = _fmt
                d = (f"M{f(x + rx)} {f(y)}H{f(x + w - rx)}A{f(rx)} {f(ry)} 0 0 1 {f(x + w)} {f(y + ry)}"
                     f"V{f(y + h - ry)}A{f(rx)} {f(ry)} 0 0 1 {f(x + w - rx)} {f(y + h)}"
                     f"H{f(x + rx)}A{f(rx)} {f(ry)} 0 0 1 {f(x)} {f(y + h - ry)}"
                     f"V{f(y + ry)}A{f(rx)} {f(ry)} 0 0 1 {f(x + rx)} {f(y)}Z")
            else:
                d = f"M{_fmt(x)} {_fmt(y)}H{_fmt(x + w)}V{_fmt(y + h)}H{_fmt(x)}Z"
            self.path(d, m, layer)
        elif tag in ("circle", "ellipse"):
            cx, cy = _length(el.get("cx")), _length(el.get("cy"))
            if tag == "circle":
                rx = ry = _length(el.get("r"))
            else:
                rx, ry = _length(el.get("rx")), _length(el.get("ry"))
            if rx <= 0 or ry <= 0:
                self.skip("empty " + tag)
                return
            self.ellipse((cx, cy), rx, ry, m, layer)
        elif tag == "line":
            self.line(((_length(el.get("x1")), _length(el.get("y1"))),
                       (_length(el.get("x2")), _length(el.get("y2")))), m, layer)
        else:
            try:
                v = [float(t) for t in re.findall(_NUM, el.get("points") or "")]
            except ValueError:
                v = []
            pts = list(zip(v[0::2], v[1::2]))
            if len(pts) < 2:
                self.skip("empty " + tag)
                return
            if tag == "polygon":
                pts.append(pts[0])
            for a, b in zip(pts, pts[1:]):
                self.line((a, b), m, layer)

    def line(self, seg, m, layer):
        a, b = _pt(m, seg[0]), _pt(m, seg[1])
        if math.hypot(a[0] - b[0], a[1] - b[1]) > 1e-12:
            self.prims.append({"type": "line", "p1": a, "p2": b, "layer": layer})

    def ellipse(self, c, rx, ry, m, layer):
        a = m[:2, :2] @ np.diag([rx, ry])
        u, s, _ = np.linalg.svd(a)
        cc = _pt(m, c)
        if s[1] < 1e-12 * max(s[0], 1e-300):
            self.skip("degenerate ellipse")
            return
        if s[0] - s[1] <= 1e-9 * s[0]:
            self.prims.append({"type": "circle", "c": cc, "r": float(s[0]), "layer": layer})
            return
        major = (cc[0] + u[0, 0] * s[0], cc[1] + u[1, 0] * s[0])
        self.prims.append({"type": "ellipse", "c": cc, "a": major, "r": float(s[1]), "layer": layer})

    def path(self, d, m, layer):
        rec = _Recorder()
        try:
            _parse_path(d, rec, arc_class=_Arc)
        except (ValueError, IndexError, TypeError, KeyError):
            self.skip("bad path data")
            return
        sim = _similarity(m)
        for seg in rec.segs:
            kind = seg[0]
            if kind == "L":
                self.line(seg[1:], m, layer)
            elif kind == "C":
                pts = [_pt(m, p) for p in seg[1:]]
                self.prims.append({"type": "bezier", "pts": pts, "layer": layer})
            else:
                self.arc(seg, m, sim, layer)

    def arc(self, seg, m, sim, layer):
        _, p1, rx, ry, rot, large, sweep, p2 = seg
        r = _arc_params(p1, rx, ry, rot, large, sweep, p2)
        if r is None:
            return
        if r[0] == "line":
            self.line((p1, p2), m, layer)
            return
        _, c, rx, ry, phi, th1, dth = r
        if sim is not None and abs(rx - ry) <= 1e-9 * rx:
            a, b, cc = _pt(m, p1), _pt(m, p2), _pt(m, c)
            if dth * float(np.linalg.det(m[:2, :2])) < 0:
                a, b = b, a
            self.prims.append({"type": "arc", "c": cc, "p1": a, "p2": b, "layer": layer})
            return
        for q in _arc_beziers(c, rx, ry, phi, th1, dth, p1, p2):
            self.prims.append({"type": "bezier", "pts": [_pt(m, p) for p in q], "layer": layer})

    def run(self):
        s, source, vbx, vby = self.root_scale()
        m = _mat(s, 0, 0, -s, -s * vbx, s * vby)       # user units to mm, y up, viewBox corner at the origin
        self._unreferenced = []
        self.walk(self.root, m, {}, 0, None, [], True)
        self.skipped_defs()
        stroke_only = self.stroked > 0 and self.filled == 0
        if stroke_only:
            self.notes.append("The file has strokes and no fills: each stroked path was imported as its "
                              "centre line, not as an outline.")
        report = {"scale": s, "unit_source": source, "ppi": self.ppi, "ppi_source": self.ppi_source,
                  "inkscape_version": self.inkscape_version, "counts": self.counts, "skipped": self.skipped,
                  "stroke_only": stroke_only, "notes": self.notes, "primitives": {}}
        for p in self.prims:
            report["primitives"][p["type"]] = report["primitives"].get(p["type"], 0) + 1
        return self.prims, report

    def skipped_defs(self):
        for ch in self._unreferenced:
            if ch.get("id") not in self.used_ids:
                self.skip("defs")


def read(text, *, ppi=None):
    """Read SVG text (str or bytes). Returns (prims, report); raises ValueError("Not a readable SVG
    file: ...") on broken XML, a use cycle or an unreadable transform. ppi=None picks 90 for an Inkscape
    file older than 0.92 and 96 otherwise; pass 96, 90 or 72 to override. See the module docstring."""
    if _parse_path is None:
        raise RuntimeError("Reading SVG needs the fontTools package (pip install fonttools), which is not installed")
    raw = text.decode("utf-8", "replace") if isinstance(text, (bytes, bytearray)) else text
    if "<!ENTITY" in raw:
        raise _fail("entity declarations are not allowed")
    try:
        root = ET.fromstring(text)
    except (ET.ParseError, ValueError) as e:
        raise _fail(str(e)) from None
    if _local(root.tag) != "svg":
        raise _fail("the root element is not svg")
    return _Reader(root, ppi).run()
