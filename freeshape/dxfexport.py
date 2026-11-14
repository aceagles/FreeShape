"""DXF export of 2D outlines for cutting machines: any sketch, any flat face, a sheet metal flat pattern or a nest.

Three pieces, kept apart so each can change alone:

* **Geometry** — lines, circles, arcs, polylines and text in millimetres, each tagged with a *role* (what the
  machine should do with it): "cut", "engrave", "bend", "note" or "sheet". Where the geometry came from (a sketch,
  a face, an unfolded part) does not matter from here on.
* **Profile** — how one program wants a DXF: for every role a layer name, a colour and a line type (or "leave it
  out"), plus the file format and whether text is written. `STANDARD` is the layout FreeShape has always written
  (CUT / BEND / NOTES / SHEET). `K40_WHISPERER` is what K40 Whisperer reads: blue = vector engrave, anything else
  = vector cut, no text. More are plain data: `Profile.from_dict` / `profiles_from_json`.
* **write()** — Geometry + Profile -> file.

Colours are AutoCAD colour index numbers (1 red, 2 yellow, 3 green, 4 cyan, 5 blue, 6 magenta, 7 black/white …).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

import numpy as np

ROLES = ("cut", "engrave", "bend", "note", "sheet")
ROLE_LABELS = {"cut": "Cut", "engrave": "Engrave", "bend": "Bend line", "note": "Note / label",
               "sheet": "Sheet outline"}
# the roles a sketch or a face can be given by hand (the others come from sheet metal and nesting)
PICKABLE_ROLES = ("cut", "engrave")

COLOURS = {1: "Red", 2: "Yellow", 3: "Green", 4: "Cyan", 5: "Blue", 6: "Magenta", 7: "Black / white", 8: "Grey"}
LINETYPE_NAMES = ("CONTINUOUS", "DASHED", "CENTER")
FORMATS = {"r2010": "AutoCAD 2010", "r12": "AutoCAD R12 (simplest, read by everything)"}


class DxfExportError(ValueError):
    pass


# ============================================================================ profiles
@dataclass
class RoleStyle:
    layer: str
    colour: int = 7
    linetype: str = "CONTINUOUS"
    skip: bool = False                 # leave geometry with this role out of the file

    def to_dict(self):
        return {"layer": self.layer, "colour": self.colour, "linetype": self.linetype, "skip": self.skip}

    @staticmethod
    def from_dict(d):
        lt = str(d.get("linetype", "CONTINUOUS")).upper()
        colour = int(d.get("colour", 7))
        return RoleStyle(str(d.get("layer") or "0"), colour if 1 <= colour <= 255 else 7,
                         lt if lt in LINETYPE_NAMES else "CONTINUOUS", bool(d.get("skip", False)))


@dataclass
class Profile:
    name: str
    roles: dict = field(default_factory=dict)      # role -> RoleStyle; a role not listed is left out
    format: str = "r2010"                          # "r2010" (needs ezdxf) or "r12"
    entity_colour: bool = False                    # write the colour on every entity as well as on its layer
    text: bool = True                              # write text entities (False: labels are left out)
    units_header: bool = False                     # R12 only: say "millimetres" in the header ($INSUNITS)
    about: str = ""
    builtin: bool = False

    def style(self, role):
        """The RoleStyle for a role, or None when this profile leaves that role out."""
        st = self.roles.get(role)
        return None if st is None or st.skip else st

    def to_dict(self):
        return {"name": self.name, "format": self.format, "entity_colour": self.entity_colour, "text": self.text,
                "units_header": self.units_header, "about": self.about,
                "roles": {r: st.to_dict() for r, st in self.roles.items()}}

    @staticmethod
    def from_dict(d):
        fmt = d.get("format", "r2010")
        return Profile(str(d.get("name") or "Profile"),
                       {r: RoleStyle.from_dict(st) for r, st in (d.get("roles") or {}).items() if r in ROLES},
                       fmt if fmt in FORMATS else "r2010", bool(d.get("entity_colour", False)),
                       bool(d.get("text", True)), bool(d.get("units_header", False)), str(d.get("about", "")))

    def copy(self, name=None):
        p = Profile.from_dict(self.to_dict())
        p.name = name or self.name
        return p


STANDARD = Profile(
    "Standard", {"cut": RoleStyle("CUT", 7), "engrave": RoleStyle("ENGRAVE", 5),
                 "bend": RoleStyle("BEND", 1, "DASHED"), "note": RoleStyle("NOTES", 3),
                 "sheet": RoleStyle("SHEET", 8)},
    about="Layers CUT, ENGRAVE, BEND (dashed), NOTES and SHEET. For CAD programs and most cutting services.",
    builtin=True)

# K40 Whisperer (scorchworks.com/K40whisperer): "Everything in a DXF file will come in as vector cut unless the
# color is blue or the features are on a layer with 'engrave' in the name". Text and raster are not read from DXF.
# So: engrave is blue, on a layer called ENGRAVE, with the colour on the entity too (whichever of the three the
# program looks at, it finds it); cut is red; labels and the sheet outline are left out, because anything that is
# not blue would be cut. The plain R12 format keeps to entities every reader knows.
K40_WHISPERER = Profile(
    "K40 Whisperer", {"cut": RoleStyle("CUT", 1), "engrave": RoleStyle("ENGRAVE", 5),
                      "bend": RoleStyle("ENGRAVE", 5), "note": RoleStyle("NOTES", 3, skip=True),
                      "sheet": RoleStyle("SHEET", 8, skip=True)},
    format="r12", entity_colour=True, text=False, units_header=True,
    about="Red = vector cut, blue = vector engrave. No text, no sheet outline (K40 Whisperer would cut them).",
    builtin=True)

BUILTIN = (STANDARD, K40_WHISPERER)


def profiles_from_json(text) -> list:
    """User profiles from the JSON kept in the settings. Anything unreadable is dropped, never an error: a bad
    setting must not stop an export. A user profile cannot take a built-in's name."""
    try:
        data = json.loads(text) if text else []
    except (TypeError, ValueError):
        return []
    out, seen = [], {p.name for p in BUILTIN}
    for d in data if isinstance(data, list) else []:
        try:
            p = Profile.from_dict(d)
        except (AttributeError, TypeError, ValueError):
            continue
        if p.name not in seen:
            seen.add(p.name)
            out.append(p)
    return out


def profiles_to_json(profiles) -> str:
    return json.dumps([p.to_dict() for p in profiles if not p.builtin])


def all_profiles(user=()) -> list:
    return list(BUILTIN) + list(user)


def find_profile(name, user=()):
    """The profile with that name, or STANDARD."""
    return next((p for p in all_profiles(user) if p.name == name), STANDARD)


# ============================================================================ geometry
class Geometry:
    """2D drawing entities in mm, each with a role. Items are (role, kind, data):
    line (a, b) · circle (c, r) · arc (c, r, a0, a1: degrees, counter-clockwise) · poly (pts, closed) ·
    text (pos, string, height, angle, halign, valign)."""

    def __init__(self):
        self.items = []

    def line(self, role, a, b):
        self.items.append((role, "line", (_pt(a), _pt(b))))

    def circle(self, role, c, r):
        self.items.append((role, "circle", (_pt(c), float(r))))

    def arc(self, role, c, r, a0, a1):
        self.items.append((role, "arc", (_pt(c), float(r), float(a0), float(a1))))

    def poly(self, role, pts, closed=False):
        pts = [_pt(p) for p in pts]
        if closed and len(pts) > 2 and math.dist(pts[0], pts[-1]) < 1e-9:
            pts = pts[:-1]
        if len(pts) >= 2:
            self.items.append((role, "poly", (pts, bool(closed))))

    def text(self, role, pos, s, height, angle=0.0, halign="left", valign="baseline"):
        self.items.append((role, "text", (_pt(pos), str(s), float(height), float(angle), halign, valign)))

    def extend(self, other: "Geometry"):
        self.items.extend(other.items)
        return self

    def count(self, role=None):
        return sum(1 for r, _k, _d in self.items if role is None or r == role)

    def moved(self, M, t) -> "Geometry":
        """A copy under the 2D map q -> M q + t, where M is a rotation or a rotation with a mirror (planes that
        face opposite ways). Circles and arcs stay circles and arcs."""
        M = np.asarray(M, float)
        t = np.asarray(t, float)
        flip = np.linalg.det(M) < 0

        def P(q):
            return _pt(M @ np.asarray(q, float) + t)

        def A(deg):
            a = math.radians(deg)
            v = M @ np.array([math.cos(a), math.sin(a)])
            return math.degrees(math.atan2(v[1], v[0]))

        out = Geometry()
        for role, kind, d in self.items:
            if kind == "line":
                out.items.append((role, kind, (P(d[0]), P(d[1]))))
            elif kind == "circle":
                out.items.append((role, kind, (P(d[0]), d[1])))
            elif kind == "arc":
                a0, a1 = (A(d[3]), A(d[2])) if flip else (A(d[2]), A(d[3]))
                out.items.append((role, kind, (P(d[0]), d[1], a0, a1)))
            elif kind == "poly":
                out.items.append((role, kind, ([P(q) for q in d[0]], d[1])))
            elif kind == "text":
                out.items.append((role, kind, (P(d[0]), d[1], d[2], A(d[3]), d[4], d[5])))
        return out

    def bounds(self):
        """((x0, y0), (x1, y1)) around everything (arcs by their whole circle: good enough for the header)."""
        xs, ys = [], []
        for _role, kind, d in self.items:
            if kind == "line":
                pts = d
            elif kind in ("circle", "arc"):
                (cx, cy), r = d[0], d[1]
                pts = ((cx - r, cy - r), (cx + r, cy + r))
            elif kind == "poly":
                pts = d[0]
            else:
                pts = (d[0],)
            xs += [p[0] for p in pts]
            ys += [p[1] for p in pts]
        if not xs:
            return (0.0, 0.0), (0.0, 0.0)
        return (min(xs), min(ys)), (max(xs), max(ys))


def _pt(p):
    return float(p[0]), float(p[1])


# ============================================================================ sources
def edge_entities(edges, to2d):
    """Solid-model edges as (lines, circles, arcs, polylines) in 2D; `to2d` maps a world point into the drawing.
    Straight edges and circular ones stay exact; any other curve becomes a fine polyline."""
    from . import kernel as K
    lines, circles, arcs, polys = [], [], [], []
    for e in edges:
        info = K.edge_info(e)
        if "p0" in info:
            lines.append((to2d(info["p0"]), to2d(info["p1"])))
            continue
        pts = K.discretize_edge(e, 0.005)
        if "r" in info and len(pts) >= 2:
            c = to2d(info["center"])
            p0, p1 = to2d(pts[0]), to2d(pts[-1])
            if np.linalg.norm(np.subtract(p0, p1)) < 1e-6:
                circles.append((c, info["r"]))
                continue
            mid = to2d(pts[len(pts) // 2])
            a0 = math.degrees(math.atan2(p0[1] - c[1], p0[0] - c[0]))
            a1 = math.degrees(math.atan2(p1[1] - c[1], p1[0] - c[0]))
            am = math.degrees(math.atan2(mid[1] - c[1], mid[0] - c[0]))
            # counter-clockwise from a0 to a1 must pass through the middle point
            if not ((am - a0) % 360 < (a1 - a0) % 360):
                a0, a1 = a1, a0
            arcs.append((c, info["r"], a0, a1))
            continue
        polys.append([to2d(q) for q in pts])
    return lines, circles, arcs, polys


def add_entities(geom: Geometry, role, lines=(), circles=(), arcs=(), polys=()):
    for a, b in lines:
        geom.line(role, a, b)
    for c, r in circles:
        geom.circle(role, c, r)
    for c, r, a0, a1 in arcs:
        geom.arc(role, c, r, a0, a1)
    for pts in polys:
        geom.poly(role, pts)
    return geom


def face_plane(face):
    """The plane of a flat face; DxfExportError for a curved one."""
    from . import kernel as K
    pl = K.planar_face_plane(face)
    if pl is None:
        raise DxfExportError("Only flat faces can be exported as DXF — this face is curved.")
    return pl


def face_geometry(face, role="cut", plane=None) -> Geometry:
    """Every edge of a flat face — its outline and the holes in it — seen square-on, in `plane`'s coordinates
    (the face's own plane by default)."""
    from . import kernel as K
    pl = plane or face_plane(face)
    return add_entities(Geometry(), role, *edge_entities(K.edges(face), pl.to_local))


def sketch_geometry(sk, role="cut", construction_role=None) -> Geometry:
    """A sketch's curves in sketch coordinates. Lines, circles and arcs stay exact; splines and ellipses become
    fine polylines; text becomes the outlines of its letters (cutting programs do not read DXF text).

    Construction geometry is left out unless `construction_role` names the role to give it — handy for marking
    engrave lines in the same sketch as the cut outline. The helper lines FreeShape adds itself (a centre
    rectangle's diagonal, copies of part edges) are always left out, and so are points."""
    g = Geometry()
    helpers = sk.helper_ids()
    for eid, e in sk.entities.items():
        t = e["type"]
        if t == "point" or eid in helpers:
            continue
        r = role
        if e.get("construction"):
            if not construction_role:
                continue
            r = construction_role
        if t == "line":
            g.line(r, sk.P(e["p1"]), sk.P(e["p2"]))
        elif t == "circle":
            g.circle(r, sk.P(e["c"]), e["r"])
        elif t == "arc":
            a1, a2 = sk.arc_angles(eid)
            g.arc(r, sk.P(e["c"]), sk.radius(eid), math.degrees(a1), math.degrees(a2))
        elif t == "ellipse":
            g.poly(r, sk.polyline(eid, 180), closed=True)
        elif t == "spline":
            g.poly(r, sk.polyline(eid, 64), closed=bool(e.get("closed")))
        elif t == "text":
            for loop in sk.polylines(eid):
                g.poly(r, loop, closed=True)
    return g


def plane_map(src, dst):
    """(M, t) taking `src` plane coordinates to `dst` plane coordinates, looking along dst's normal. The planes
    must be parallel (facing the same or opposite ways); DxfExportError otherwise."""
    if abs(abs(float(np.dot(src.normal, dst.normal))) - 1.0) > 1e-6:
        raise DxfExportError("not parallel")
    M = np.array([[src.xdir @ dst.xdir, src.ydir @ dst.xdir], [src.xdir @ dst.ydir, src.ydir @ dst.ydir]], float)
    return M, np.array(dst.to_local(src.origin), float)


def combine(sources) -> Geometry:
    """Several (Geometry, Plane) pairs in one drawing, in the first one's plane coordinates, each where it is in
    the model. A source on a plane that is not parallel to the first is refused."""
    out = Geometry()
    base = None
    for i, (g, pl) in enumerate(sources):
        if base is None:
            base = pl
            out.extend(g)
            continue
        try:
            M, t = plane_map(pl, base)
        except DxfExportError:
            raise DxfExportError(f"Item {i + 1} is not parallel to the first one. Export faces and sketches that "
                                 "lie in parallel planes together, the others on their own.") from None
        out.extend(g.moved(M, t))
    return out


# ============================================================================ writing
def write(geom: Geometry, path, profile: Profile = None, declare=()):
    """Write the geometry as DXF the way `profile` says. `declare` lists roles whose layers are made even when
    nothing is on them. Returns {role: entities written}; roles the profile leaves out do not appear."""
    profile = profile or STANDARD
    items = []
    for role, kind, d in geom.items:
        st = profile.style(role)
        if st is None or (kind == "text" and not profile.text):
            continue
        items.append((role, st, kind, d))
    if not items:
        raise DxfExportError(f"Nothing to export: the “{profile.name}” profile leaves out everything selected.")
    styles = {}
    for role in list(declare) + [it[0] for it in items]:
        st = profile.style(role)
        if st is not None:
            styles.setdefault(st.layer, st)
    use_ezdxf = profile.format != "r12"
    if use_ezdxf:
        try:
            import ezdxf  # noqa: F401
        except ImportError:
            use_ezdxf = False
    (_write_ezdxf if use_ezdxf else _write_r12)(items, styles, path, profile, geom)
    counts = {}
    for role, _st, _kind, _d in items:
        counts[role] = counts.get(role, 0) + 1
    return counts


_ALIGN = {("left", "baseline"): "LEFT", ("center", "baseline"): "CENTER", ("right", "baseline"): "RIGHT",
          ("left", "bottom"): "BOTTOM_LEFT", ("center", "bottom"): "BOTTOM_CENTER",
          ("right", "bottom"): "BOTTOM_RIGHT", ("left", "middle"): "MIDDLE_LEFT",
          ("center", "middle"): "MIDDLE_CENTER", ("right", "middle"): "MIDDLE_RIGHT",
          ("left", "top"): "TOP_LEFT", ("center", "top"): "TOP_CENTER", ("right", "top"): "TOP_RIGHT"}


def _write_ezdxf(items, styles, path, profile, _geom):
    import ezdxf
    from ezdxf.enums import TextEntityAlignment
    doc = ezdxf.new("R2010", setup=False)
    doc.units = ezdxf.units.MM
    doc.header["$MEASUREMENT"] = 1
    if any(st.linetype == "DASHED" for st in styles.values()):
        doc.linetypes.add("DASHED", pattern=[4.5, 3.0, -1.5], description="Dashed __ __ __")
    if any(st.linetype == "CENTER" for st in styles.values()):
        doc.linetypes.add("CENTER", pattern=[12.5, 8.0, -1.5, 1.5, -1.5], description="Center ____ _ ____ _")
    doc.styles.get("Standard").dxf.font = "arial.ttf"
    for st in styles.values():
        if st.layer != "0":
            doc.layers.add(st.layer, color=st.colour, linetype=st.linetype)
    msp = doc.modelspace()
    for _role, st, kind, d in items:
        at = {"layer": st.layer}
        if profile.entity_colour:
            at["color"] = st.colour
        if kind == "line":
            msp.add_line(d[0], d[1], dxfattribs=at)
        elif kind == "circle":
            msp.add_circle(d[0], d[1], dxfattribs=at)
        elif kind == "arc":
            msp.add_arc(d[0], d[1], d[2], d[3], dxfattribs=at)
        elif kind == "poly":
            msp.add_lwpolyline(d[0], close=d[1], dxfattribs=at)
        elif kind == "text":
            pos, s, h, ang, ha, va = d
            t = msp.add_text(s, height=h, rotation=ang, dxfattribs=at)
            t.set_placement(pos, align=getattr(TextEntityAlignment, _ALIGN.get((ha, va), "LEFT")))
    doc.saveas(path)


def _write_r12(items, styles, path, profile, geom):
    from .dxf import DxfWriter
    w = DxfWriter()
    for st in styles.values():
        if st.layer != "0":
            w.layer(st.layer, st.colour, st.linetype)
    if profile.entity_colour:
        w.entity_colours = {st.layer: st.colour for st in styles.values()}
    for _role, st, kind, d in items:
        if kind == "line":
            w.line(st.layer, d[0], d[1])
        elif kind == "circle":
            w.circle(st.layer, d[0], d[1])
        elif kind == "arc":
            w.arc(st.layer, d[0], d[1], d[2], d[3])
        elif kind == "poly":
            w.polyline(st.layer, d[0], d[1])
        elif kind == "text":
            pos, s, h, ang, ha, va = d
            w.text(st.layer, pos, s, h, ang, ha, va)
    lo, hi = geom.bounds()
    w.save(path, lo, hi, insunits=4 if profile.units_header else None)


def text_angle(p, q):
    """The angle (degrees) for a label along the line p -> q, turned so it never reads upside down."""
    ang = math.degrees(math.atan2(q[1] - p[1], q[0] - p[0]))
    if ang > 90 or ang <= -90:
        ang -= 180 if ang > 0 else -180
    return ang
