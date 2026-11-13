"""The naming builder: the API a Python feature builds its geometry through, so its faces, edges and
vertices get element names like a built-in feature's. Headless -- no Qt, no document.

`Builder(tag)` is made once per feature run with the feature id as `tag`. Every method returns a
`naming.NamedShape`:

* `extrude`, `revolve`, `fuse`, `cut`, `common` call the kernel helper with `with_history=True` and
  name the result from that history (`naming.name_result`), as FreeCAD names a maker's result through
  its maker mapper (docs/reference/element-naming.md section 3).
* `labelled` names a shape that has no usable maker history -- a pipe shell, a sewn shell, a loft
  trimmed afterwards -- from labels the caller gives, the role FreeCAD's explicit-table mappers
  (`ShapeMapper` / `GenericShapeMapper`) play for operations OCCT keeps no history for (section 3).
* `profile` seeds a planar face with edge labels, so the side faces an extrude or revolve sweeps from
  it are named after those edges (section 4).
* `box` and `cylinder` are labelled primitives; `transformed` is a rigid move that keeps every name
  one-to-one (section 7).

Labels. A label is the caller's stable id for an element, e.g. `bore` or `tooth3_left`. It must
match `[A-Za-z0-9_+-]+`: naming reserves `;.:,|()#` for the name grammar, so the dotted form
`tooth3.left` is written `tooth3_left`. A bad label raises `naming.NamingError`. `labelled` fills
the gaps the caller leaves:

1. an unlabelled edge whose adjacent faces are all labelled gets their labels, sorted, de-duplicated
   and joined with `+` (`bore+top`); an edge with no face, or with an unlabelled face, gets none;
2. then an unlabelled vertex gets the labels of its edges the same way (explicit or from rule 1);
3. anything still unlabelled gets `naming.seed`'s index name (`f3`, `e7`, `v2`). An index name is a
   position in this build, so it is not stable across a parameter change; label what matters.

Like FreeCAD's forward pass (section 4, rule 4) an element is named from its neighbours only when
every one of them is labelled, so a name never mixes a stable label with a position.

Two elements of one kind that end up with the same label are told apart the way naming tells any
duplicate apart (section 1): the first in index order keeps the name, later ones get `;D1`, `;D2`,
... appended. Give every element you will reference a label of its own.

Op code. Names written by `labelled` (and so by `profile`, `box`, `cylinder`) carry the op code
`PYF` ("Python feature") and the builder's tag: `bore;PYF;:HF3:4,F`.
"""
from __future__ import annotations

import re

import numpy as np

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Pnt, gp_Trsf
from OCP.TopLoc import TopLoc_Location

from . import kernel as K
from . import naming as N

OP_PYFEATURE = "PYF"
LABEL_RE = re.compile(r"[A-Za-z0-9_+-]+")
JOIN = "+"

BOX_FACES = ("left", "right", "front", "back", "bottom", "top")   # -X +X -Y +Y -Z +Z
CYLINDER_FACES = ("side", "top", "bottom")

_KIND_LETTERS = (N.FACE, N.EDGE, N.VERTEX)
_RIGID_TOL = 1e-9


def check_label(label) -> str:
    """`label` if it can go in an element name, else NamingError."""
    if not isinstance(label, str) or not LABEL_RE.fullmatch(label):
        raise N.NamingError(f"label {label!r} must match [A-Za-z0-9_+-]+ "
                            "(write tooth3.left as tooth3_left)")
    return label


def _as_named_list(named):
    if isinstance(named, N.NamedShape):
        return [named]
    out = list(named)
    for n in out:
        if not isinstance(n, N.NamedShape):
            raise TypeError(f"expected a NamedShape, got {type(n).__name__}")
    return out


def _faces_of(sources):
    faces = []
    for n in sources:
        faces.extend(K.faces(n.shape))
    return faces


def _dup_name(name, i, tag, letter):
    """`name` with the i-th duplicate marker appended -- the rule naming applies to a name two
    elements built (section 1). Kept here because naming exposes no public helper for it."""
    return N._encode(name, f"{N.DUP}{i:x}", None, tag, letter)


class Builder:
    """Builds and names geometry for one feature. `tag` is the feature id, written into every name."""

    def __init__(self, tag):
        N._tag_text(tag)                     # a bad feature id fails here, not at the first name
        self.tag = tag

    # ------------------------------------------------------------------ makers with history
    def _named(self, result, sources, helper):
        shape, history = result
        return N.name_result(shape, history, sources, self.tag, N.OP_FOR[helper])

    def extrude(self, named_faces, direction, depth, **kw) -> N.NamedShape:
        """Sweeps the faces of `named_faces` (a NamedShape or a list of them) along `direction` by
        `depth`. Extra keywords (`symmetric`, `depth2`) go to `kernel.extrude`."""
        sources = _as_named_list(named_faces)
        res = K.extrude(_faces_of(sources), np.asarray(direction, float), depth,
                        with_history=True, **kw)
        return self._named(res, sources, "extrude")

    def revolve(self, named_faces, axis_p, axis_d, angle_deg, **kw) -> N.NamedShape:
        """Revolves the faces of `named_faces` about the axis through `axis_p` along `axis_d`.
        Extra keywords (`symmetric`) go to `kernel.revolve`."""
        sources = _as_named_list(named_faces)
        res = K.revolve(_faces_of(sources), axis_p, axis_d, angle_deg, with_history=True, **kw)
        return self._named(res, sources, "revolve")

    def fuse(self, a: N.NamedShape, b: N.NamedShape, **kw) -> N.NamedShape:
        return self._named(K.fuse(a.shape, b.shape, with_history=True, **kw), [a, b], "fuse")

    def cut(self, a: N.NamedShape, b: N.NamedShape, **kw) -> N.NamedShape:
        return self._named(K.cut(a.shape, b.shape, with_history=True, **kw), [a, b], "cut")

    def common(self, a: N.NamedShape, b: N.NamedShape, **kw) -> N.NamedShape:
        return self._named(K.common(a.shape, b.shape, with_history=True, **kw), [a, b], "common")

    # ------------------------------------------------------------------ explicit labels
    def labelled(self, shape, face_labels=None, edge_labels=None, vertex_labels=None) -> N.NamedShape:
        """Names `shape` from caller labels. Each labels argument is a list of `(sub_shape, label)`
        pairs, matched to `shape`'s elements with `IsSame`, or a callable `fn(sub_shape)` returning a
        label or None. A pair whose sub-shape is not in `shape` is skipped, as FreeCAD skips a
        history entry naming a shape that is not in the result (section 3). The gap-filling rules
        are in the module docstring."""
        if shape is None:
            raise N.NamingError("nothing to name: no shape")
        el = N._Elements(shape)
        labels = {}
        for letter, given in zip(_KIND_LETTERS, (face_labels, edge_labels, vertex_labels)):
            labels.update(self._explicit(el, letter, given))
        for parent, child in ((N.FACE, N.EDGE), (N.EDGE, N.VERTEX)):
            labels.update(self._from_parents(el, labels, parent, child))
        named = N.seed(shape, self.tag, OP_PYFEATURE, labels)
        return self._unique(named)

    @staticmethod
    def _explicit(el, letter, given):
        out = {}
        if given is None:
            return out
        if callable(given):
            for key in el.keys(letter):
                label = given(el.shape(key))
                if label is not None:
                    out[key] = check_label(label)
            return out
        for pair in given:
            sub, label = pair
            check_label(label)
            key = el.key(sub)
            if key is not None and key[0] == letter:
                out[key] = label
        return out

    @staticmethod
    def _from_parents(el, labels, parent, child):
        """child key -> joined parent labels, for unlabelled children whose parents are all labelled."""
        parents = {}
        for pkey in el.keys(parent):
            for ckey, _ in el.subs(pkey, child):
                parents.setdefault(ckey, set()).add(pkey)
        out = {}
        for ckey in el.keys(child):
            if ckey in labels or ckey not in parents:
                continue
            found = [labels.get(p) for p in parents[ckey]]
            if all(found):
                out[ckey] = JOIN.join(sorted(set(found)))
        return out

    def _unique(self, named):
        seen = {}
        for key in named.keys():
            name = named.names[key]
            n = seen.get(name, 0)
            seen[name] = n + 1
            if n:
                named.names[key] = _dup_name(name, n, self.tag, key[0])
        return named

    def profile(self, face, edge_labels) -> N.NamedShape:
        """A planar face seeded with edge labels (pairs or a callable, as in `labelled`), ready for
        `extrude` or `revolve`: each side face is then named after the edge it was swept from."""
        face = K._as_face(face)
        if K.planar_face_plane(face) is None:
            raise K.KernelError("A profile must be a planar face")
        return self.labelled(face, edge_labels=edge_labels)

    # ------------------------------------------------------------------ primitives
    def _primitive_labels(self, defaults, labels):
        out = dict(zip(defaults, defaults))
        for k, v in (labels or {}).items():
            if k not in out:
                raise N.NamingError(f"unknown face {k!r}; expected one of {', '.join(defaults)}")
            out[k] = check_label(v)
        return out

    def box(self, dx, dy, dz, origin=(0, 0, 0), labels=None) -> N.NamedShape:
        """An axis-aligned box from `origin`. `labels` maps any of left/right (-X/+X), front/back
        (-Y/+Y), bottom/top (-Z/+Z) to a label; a face left out is labelled with its key."""
        names = self._primitive_labels(BOX_FACES, labels)
        shape = BRepPrimAPI_MakeBox(gp_Pnt(*map(float, origin)), float(dx), float(dy), float(dz)).Shape()

        def which(face):             # by outward normal: OCCT's own Left/Front naming is not -X/-Y
            n = K.planar_face_plane(K._as_face(face)).normal
            axis = int(np.argmax(np.abs(n)))
            return names[BOX_FACES[2 * axis + (1 if n[axis] > 0 else 0)]]

        return self.labelled(shape, face_labels=which)

    def cylinder(self, radius, height, origin=(0, 0, 0), axis=(0, 0, 1), labels=None) -> N.NamedShape:
        """A cylinder standing on `origin` along `axis`. `labels` maps any of side/top/bottom to a
        label; a face left out is labelled with its key. Top is the cap at `origin + height*axis`."""
        names = self._primitive_labels(CYLINDER_FACES, labels)
        base = np.asarray(origin, float)
        ax = np.asarray(axis, float)
        ax = ax / np.linalg.norm(ax)
        shape = K.cylinder(base, ax, radius, height)

        def which(face):
            plane = K.planar_face_plane(K._as_face(face))
            if plane is None:
                return names["side"]
            return names["top"] if float((plane.origin - base) @ ax) > height / 2 else names["bottom"]

        return self.labelled(shape, face_labels=which)

    # ------------------------------------------------------------------ copies
    def transformed(self, named: N.NamedShape, trsf) -> N.NamedShape:
        """`named` moved rigidly by the gp_Trsf `trsf`, every name kept on the moved copy of its
        element. A rigid move does not rebuild topology, so as in FreeCAD there is no history to
        read and the map is carried over one-to-one (section 7). Scaling and mirroring rebuild the
        shape and raise NamingError (mirror through the kernel's mirror instead)."""
        if not isinstance(trsf, gp_Trsf):
            raise TypeError("transformed needs a gp_Trsf")
        if abs(trsf.ScaleFactor() - 1.0) > _RIGID_TOL or trsf.IsNegative():
            raise N.NamingError("transformed takes a rigid move only (no scale, no mirror)")
        moved = named.shape.Moved(TopLoc_Location(trsf))
        out = N.NamedShape(moved, dict(named.names))
        if any(len(out.keys(k)) != len(named.keys(k)) for k in N.KINDS):
            raise N.NamingError("moved shape does not match its source element for element")
        return out
