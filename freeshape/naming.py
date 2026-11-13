"""Element naming: every face, edge and vertex of a shape carries a name built from the history of
the operation that made it. Headless -- no Qt, no UI, no document.

This is the port of FreeCAD 1.0's element map described in docs/reference/element-naming.md; the
section numbers in the comments below are that document's. A name is a plain structured string
whose fragments read oldest first, e.g.

    E2;SKT;:HF1:4,E;:G;XTR;:HF2:8,F;:M;CUT;:HF3:8,F

-- "the side face swept from edge 2 of sketch F1 by extrude F2, then modified by cut F3". There is
no string hasher (ADR 0003): names are stored expanded and every name is recomputed from scratch on
each rebuild, so nothing but the references themselves is ever persisted.

`NamedShape` is the shape plus that map. `name_result` is the naming step: hand it the result of a
kernel helper called with `with_history=True`, the helper's history and the `NamedShape`s it was
given, and it returns the result's own `NamedShape`. The kernel composes one history per helper
call, so one call is one operation and contributes one fragment per element, under the op code of
the helper (`OP_FOR`) and the feature id as the tag.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from OCP.BRepTools import BRepTools
from OCP.TopAbs import TopAbs_VERTEX, TopAbs_EDGE, TopAbs_FACE, TopAbs_WIRE, TopAbs_SHAPE
from OCP.TopExp import TopExp

from . import kernel as K

# --------------------------------------------------------------------------- the grammar (§ 1)

PREFIX = ";"             # the element-map prefix: the one character that separates fragments
TAG = ";:H"              # the tag postfix, followed by the tag, ':' and the op text length in hex
GEN, MOD, MODGEN = ";:G", ";:M", ";:MG"      # how a new element relates to the source it came from
UPPER, LOWER = ";:U", ";:L"                  # named from a higher / from the lower elements
EXTRA = ";K"             # marks an additional source of one new element
DUP = ";D"               # disambiguates two elements that constructed the same name
PARALLEL, COPLANAR = "0", "00"               # the two reserved positions (§ 1, § 4)
MAX_EXTRA = 4            # how many additional sources a name records, to bound its length
MAX_DUP = 100            # how many times a duplicate name is retried

# Op codes, spelled as FreeCAD spells them, one per kernel helper.
OP_EXTRUDE, OP_REVOLVE = "XTR", "RVL"
OP_FUSE, OP_CUT, OP_COMMON = "FUS", "CUT", "CMN"
OP_SKETCH, OP_MIRROR, OP_MAKER = "SKT", "MIR", "MAK"
OP_CONTEXT = "CTX"       # a Context feature restoring a stored neighbour (ADR 0004); FreeShape only

OP_FOR = {
    "extrude": OP_EXTRUDE,
    "revolve": OP_REVOLVE,
    "fuse": OP_FUSE,
    "cut": OP_CUT,
    "common": OP_COMMON,
    "fuse_all": OP_FUSE,
    "mirror": OP_MIRROR,
    "chamfer": "CHF",
    "circular_pattern": "XFM",
    "delete_face": OP_MAKER,
    "draft": "DFT",
    "fillet": "FLT",
    "flat_pattern": "UNF",
    "import": "IMP",
    "linear_pattern": "XFM",
    "loft": "LFT",
    "move": "XFM",
    "offset_surface": "OFS",
    "shell": "THK",
    "split": "SPT",
    "sweep": "PSH",
}

VERTEX, EDGE, FACE = "V", "E", "F"
KINDS = (VERTEX, EDGE, FACE)                 # low to high: the order every pass walks
_OCC_KIND = {VERTEX: TopAbs_VERTEX, EDGE: TopAbs_EDGE, FACE: TopAbs_FACE}
_RANK = {VERTEX: 0, EDGE: 1, FACE: 2}
_HIGH = 3                # sort offset of a high-level history report, so it sorts after the rest
_RESERVED = ";.:,|()#"   # characters a tag or a seed label may not contain
_ANGULAR, _CONFUSION = 1e-9, 1e-7            # OCCT's Precision::Angular / ::Confusion


class NamingError(Exception):
    """A shape could not be named (a bad tag, or an element no pass could reach)."""


# --------------------------------------------------------------------------- name encoding (§ 1, § 2)

def _tag_text(tag) -> str:
    """The tag as it is written into a name. FreeCAD hexes a numeric object id; a FreeShape feature
    id is a short string ("F3") and is written verbatim."""
    if isinstance(tag, bool) or tag is None or tag == "":
        raise NamingError(f"a name needs a feature id as its tag, got {tag!r}")
    if isinstance(tag, int):
        return f"-{-tag:x}" if tag < 0 else f"{tag:x}"
    text = str(tag)
    if text.split() != [text] or any(c in text for c in _RESERVED):
        raise NamingError(f"feature id {text!r} cannot go in an element name")
    return text


def _tag_of(name: str):
    """The tag of the name's last fragment, or None if it carries no operation."""
    pos = name.rfind(TAG)
    if pos < 0:
        return None
    rest = name[pos + len(TAG):]
    for i, c in enumerate(rest):
        if c in ":,":
            return rest[:i]
    return None


def _drop_tag(name: str) -> str:
    """The name with its last tag postfix cut off (the op text in front of it stays)."""
    pos = name.rfind(TAG)
    return name if pos < 0 else name[:pos]


def split_last(name: str):
    """One step back down a name (§ 2): (name before the last operation, that operation's text,
    its tag, the element type letter), or None when the name carries no operation. The length field
    in the tag postfix is what makes this possible -- op codes are not self-delimiting."""
    pos = name.rfind(TAG)
    if pos < 0:
        return None
    head, _, tail = name[pos + len(TAG):].partition(",")
    tag, _, length = head.partition(":")
    n = int(length, 16) if length else 0
    return name[:pos - n], name[pos - n:pos], tag, tail


def _encode(base: str, op_text: str, op, tag, letter: str) -> str:
    """`base` with one operation appended: the marker/index text, the op code, then the tag postfix
    carrying the tag, the length in hex of everything appended in front of it, and the element type."""
    text = op_text
    if text and not text.startswith(PREFIX):
        text = PREFIX + text
    if op:
        text += PREFIX + op
    tagtext = _tag_text(tag)
    if not text:
        if _tag_of(base) == tagtext:
            return base              # § 2: nothing to add and the tag is already there -- a no-op
    elif _tag_of(base) == tagtext and _tag_of(_drop_tag(base)) == tagtext:
        base = _drop_tag(base)       # § 2: one extra level per tag, not one per kernel step
    out = f"{base}{text}{TAG}{tagtext}"
    if text:
        out += f":{len(text):x}"
    return f"{out},{letter}"


def _index_text(index) -> str:
    """The 1-based position OCCT reported an element at: position 1 is written as nothing, so the
    one-to-one case stays short, and the two reserved positions are written as they are."""
    if isinstance(index, str):
        return index
    return str(index) if index > 1 else ""


def _marker(signs) -> str:
    if len(set(signs)) > 1:
        return MODGEN                # one source both generated and modified this element
    return MOD if signs[0] > 0 else GEN


# --------------------------------------------------------------------------- elements of a shape

def _kind_of(shape):
    """(the map a history image is named into, its sort offset). A maker may report a shape of a
    higher level than the element it really tells us about -- extruding a face reports the whole
    solid as generated. Those reports are explored down to faces and sorted last (§ 4)."""
    t = shape.ShapeType()
    if t == TopAbs_SHAPE:
        return None, 0               # not a shape type we can name anything from
    if t == TopAbs_VERTEX:
        return VERTEX, 0
    if t == TopAbs_EDGE:
        return EDGE, 0
    if t == TopAbs_FACE:
        return FACE, 0
    if t == TopAbs_WIRE:
        return EDGE, _HIGH
    return FACE, _HIGH


class _Elements:
    """A shape's vertices, edges and faces: index -> sub-shape and, through OCCT's own identity
    lookup, sub-shape -> index. Indices are this shape's own and are never stored anywhere."""

    def __init__(self, shape):
        self._lists, self._maps = {}, {}
        for letter, kind in _OCC_KIND.items():
            m = K.TopTools_IndexedMapOfShape()      # the kernel already picks the right OCP class
            if shape is not None:
                TopExp.MapShapes_s(shape, kind, m)
            self._maps[letter] = m
            self._lists[letter] = [m.FindKey(i) for i in range(1, m.Size() + 1)]

    def keys(self, letter=None):
        letters = KINDS if letter is None else (letter,)
        return [(ltr, i) for ltr in letters for i in range(1, len(self._lists[ltr]) + 1)]

    def shape(self, key):
        return self._lists[key[0]][key[1] - 1]

    def key(self, sub):
        """The key of a face, edge or vertex of this shape, or None when it is not one."""
        letter, offset = _kind_of(sub)
        if letter is None or offset:
            return None
        i = self._maps[letter].FindIndex(sub)
        return (letter, i) if i else None

    def subs(self, key, letter):
        """The sub-shapes of one element, as (key, sub-shape) in the parent's own order."""
        out = []
        for s in K._sub_shapes(self.shape(key), _OCC_KIND[letter]):
            k = self.key(s)
            if k is not None:
                out.append((k, s))
        return out

    def outer_edges(self, key):
        """The edges of a face's outer wire only -- punching a hole must not rename the face (§ 4)."""
        wire = BRepTools.OuterWire_s(K._as_face(self.shape(key)))
        out = []
        for s in K._sub_shapes(wire, TopAbs_EDGE):
            k = self.key(s)
            if k is not None:
                out.append((k, s))
        return out


@dataclass
class NamedShape:
    """An OCCT shape plus the name of each of its faces, edges and vertices.

    `names` is keyed by (type letter, 1-based index in this shape) -- a key is a handle into *this*
    build and means nothing after a rebuild. What a reference stores is the name."""

    shape: object
    names: dict = field(default_factory=dict)
    # (tag, the map before `stamp_all` stamped it with that tag, or None): this map is already stamped with `tag`
    stamped_from: object = None

    def __post_init__(self):
        self.el = _Elements(self.shape)

    # ------------------------------------------------------------------ lookups
    def keys(self, letter=None):
        return self.el.keys(letter)

    def named(self, letter):
        """[(sub-shape, name)] for the named elements of one kind, in index order."""
        return [(self.el.shape(k), self.names[k]) for k in self.el.keys(letter) if k in self.names]

    def name_of(self, sub):
        """The name of one face / edge / vertex of this shape, or None."""
        key = self.el.key(sub)
        return None if key is None else self.names.get(key)

    def shape_of(self, name):
        """The element a stored name refers to, or None when the name did not survive the rebuild.
        Per ADR 0003 a missing name is the caller's error to report; there is no nearest match."""
        for key, value in self.names.items():
            if value == name:
                return self.el.shape(key)
        return None

    def names_of(self, letter):
        return [self.names[k] for k in self.el.keys(letter) if k in self.names]

    def restrict(self, sub):
        """The map of `sub`, a solid or other part of this shape (e.g. one piece of a split result):
        every element of `sub` keeps the name it has here, matched by sub-shape identity."""
        out = NamedShape(sub, {})
        for letter in KINDS:
            for s, name in self.named(letter):
                key = out.el.key(s)
                if key is not None:
                    out.names[key] = name
        if self.stamped_from is not None:
            out.stamped_from = (self.stamped_from[0], None)
        return out

    def unnamed(self):
        """The keys of every element no pass could name (§ 6: a name is never invented)."""
        return [k for k in self.el.keys() if k not in self.names]


def seed(shape, tag, op=OP_SKETCH, labels=None) -> NamedShape:
    """The starting map for a shape no operation of ours made: a sketch's regions, a primitive, an
    import. Each element gets `labels[key]` (the caller's stable id for it, e.g. a sketch curve id)
    under `op` and `tag`, falling back to its position in this shape, which is all there is to go on
    before a sketch hands its ids over. FreeCAD seeds a sketch the same way, from the sketch
    geometry's own id rather than its position (§ 4)."""
    el = _Elements(shape)
    names = {}
    for key in el.keys():
        label = (labels or {}).get(key) or f"{key[0].lower()}{key[1]}"
        if label.split() != [label] or any(c in label for c in _RESERVED):
            raise NamingError(f"seed label {label!r} cannot go in an element name")
        names[key] = _encode(label, "", op, tag, key[0])
    return NamedShape(shape, names)


def name_context(shape, names, tag, label) -> NamedShape:
    """The map of a neighbour a Context feature restored (ADR 0004). Each element keeps the name it had when
    captured (`names`, by key in `shape`) and gains a CTX fragment under `tag`, the Context feature's id, so
    the name says it came from a stored shape. An element the capture left unnamed is seeded from `label`
    and its position, so every element of a Context body has a name."""
    el = _Elements(shape)
    out = {}
    for key in el.keys():
        base = names.get(key)
        if not base:
            base = f"{label}{key[0].lower()}{key[1]}"
            if any(c in base for c in _RESERVED) or base.split() != [base]:
                raise NamingError(f"context label {label!r} cannot go in an element name")
        out[key] = _encode(base, "", OP_CONTEXT, tag, key[0])
    return NamedShape(shape, out)


# --------------------------------------------------------------------------- history reading (§ 3)

def _images(history, sub, generated):
    """What the operation did to one input element. An OCCT history failure degrades the names and
    never fails the operation, so an exception here reads as "nothing known" (§ 3)."""
    try:
        if history.IsRemoved(sub):
            return []                # § 6: a deleted element simply has no name
        return list(history.Generated(sub) if generated else history.Modified(sub))
    except Exception:
        return []


def _plane_pin(plane, face):
    """Whether a generated face is coplanar or merely parallel to its planar source face. These get
    the reserved fixed positions instead of an OCCT ordinal, which is what keeps the top and bottom
    face of an extrusion stable when the profile changes (§ 1, § 4)."""
    other = K.planar_face_plane(K._as_face(face))
    if other is None:
        return None
    if abs(float(np.dot(plane.normal, other.normal))) < 1.0 - _ANGULAR:
        return None
    gap = other.origin - plane.origin
    if abs(float(gap @ plane.normal)) <= _CONFUSION and abs(float(gap @ other.normal)) <= _CONFUSION:
        return COPLANAR
    return PARALLEL


@dataclass
class _Cand:
    """One source element's claim on one new element."""
    sort: tuple                  # (source kind + high-level offset, tag, source name)
    name: str
    letter: str                  # the source element's kind, which its fragment records
    sign: int                    # +1 the source was modified into this element, -1 it generated it
    index: object                # the position OCCT reported, or a reserved position
    deferred: bool               # a high-level report: used only if nothing better turns up


class _Naming:
    """One naming step. Three passes in the order of § 2: history, then unnamed lower elements from
    the higher ones that contain them, then unnamed higher elements from their lower ones. If
    anything is still unnamed the whole loop runs again with the deferred high-level names allowed
    in."""

    def __init__(self, shape, history, sources, tag, op, stamp=True):
        self.stamp = stamp
        self.out = NamedShape(shape, {})
        self.el = self.out.el
        self.history = history
        self.sources = sources
        self.tag = tag
        self.op = op
        self.taken = {}                      # name -> key, so a name never points at two elements

    # ------------------------------------------------------------------ storing
    def _store(self, key, name):
        """Keep `name` for `key`, disambiguated if another element already built it (§ 1)."""
        candidate = name
        for i in range(1, MAX_DUP + 1):
            if candidate not in self.taken:
                self.out.names[key] = candidate
                self.taken[candidate] = key
                return
            candidate = _encode(name, f"{DUP}{i:x}", None, self.tag, key[0])
        raise NamingError(f"unresolved duplicate element name {name}")

    # ------------------------------------------------------------------ pass 1: history
    def _carry_forward(self):
        """An element the operation left alone keeps its name and only picks up the new op code and
        tag. Matched by sub-shape identity, so it survives renumbering (§ 2)."""
        for src in self.sources:
            for letter in KINDS:
                for sub, name in src.named(letter):
                    key = self.el.key(sub)
                    if key is not None and key not in self.out.names:
                        self._store(key, _encode(name, "", self.op, self.tag, key[0]) if self.stamp
                                    else name)

    def _claims(self):
        """key -> [_Cand] from the history: what every named input element turned into."""
        claims = {}
        for src in self.sources:
            for letter in KINDS:
                for sub, name in src.named(letter):
                    plane = K.planar_face_plane(K._as_face(sub)) if letter == FACE else None
                    for generated in (False, True):
                        for img in _images(self.history, sub, generated):
                            self._claim(claims, letter, name, plane, generated, img)
        return claims

    def _claim(self, claims, letter, name, plane, generated, img):
        kind, offset = _kind_of(img)
        if kind is None:
            return
        if not generated and (offset or kind != letter):
            return                   # a modified element keeps its type; anything else is noise
        pieces = [img] if not offset else K._sub_shapes(img, _OCC_KIND[kind])
        pinned = set()
        for position, piece in enumerate(pieces, 1):
            key = self.el.key(piece)
            if key is None:
                continue             # § 3: an entry naming a shape that is not in the result
            index = position
            if generated and plane is not None and kind == FACE:
                pin = _plane_pin(plane, piece)
                if pin is not None and pin not in pinned:
                    pinned.add(pin)
                    index = pin
                elif pin is not None and PARALLEL not in pinned:
                    pinned.add(PARALLEL)
                    index = PARALLEL
            claims.setdefault(key, []).append(
                _Cand((_RANK[letter] + offset, _tag_text(self.tag), name), name, letter,
                      -1 if generated else 1, index, bool(offset)))

    def _from_history(self, claims, delayed):
        """Name what the history accounts for: the first source in sort order supplies the name, the
        rest are appended as the parenthesised list of § 1, which is also the merge rule of § 5."""
        for key in sorted(claims, key=lambda k: (_RANK[k[0]], k[1])):
            cands = sorted(claims[key], key=lambda c: c.sort)
            first = cands[0]
            if not delayed and first.deferred and not isinstance(first.index, str):
                continue             # a high-level name, and the pinned ones are not delayed
            if key in self.out.names:
                if not delayed:
                    claims[key] = []
                continue
            signs, extras = [first.sign], []
            for other in cands[1:]:
                if other.deferred and not first.deferred:
                    break            # precise mappings are available, so drop the vague ones
                signs.append(other.sign)
                mark = "" if other.index == 1 else f"{EXTRA}{_index_text(other.index)}"
                extras.append(_encode(other.name, mark, None, self.tag, other.letter))
                if len(extras) == MAX_EXTRA:
                    break
            text = _marker(signs) + _index_text(first.index)
            if extras:
                text += "(" + "|".join(extras) + ")"
            self._store(key, _encode(first.name, text, self.op, self.tag, key[0]))
            if not delayed and not first.deferred:
                claims[key] = []

    # ------------------------------------------------------------------ pass 2: from the parents
    def _from_higher(self, claims, delayed):
        """An unnamed edge is named from the face that contains it, a vertex from its edge: the
        parent's name plus the position of the child inside it. A child of several parents collects
        several candidates, sorted so the choice is deterministic (§ 4)."""
        for parent, child in ((FACE, EDGE), (EDGE, VERTEX)):
            found = {}
            for key in self.el.keys(parent):
                if not delayed and claims.get(key):
                    continue         # its own name is still deferred; do not spread it yet
                name = self.out.names.get(key)
                if name is None:
                    continue
                position = 1
                for ckey, _ in self.el.subs(key, child):
                    if ckey in self.out.names:
                        continue
                    found.setdefault(ckey, {}).setdefault(name, position)
                    position += 1
            for ckey in sorted(found, key=lambda k: k[1]):
                name = sorted(found[ckey])[0]
                index = found[ckey][name]
                text = UPPER + (str(index) if index > 1 else "")
                self._store(ckey, _encode(name, text, self.op, self.tag, ckey[0]))

    # ------------------------------------------------------------------ pass 3: from the children
    def _from_lower(self, claims, delayed):
        """A still-unnamed face is named from the edges of its outer wire, an edge from its vertices:
        the lowest-sorting child name, then `;:L` and up to four more. Only if every child is named;
        otherwise the element waits for the next round (§ 4). True when something is still unnamed."""
        unnamed = False
        for child, parent in ((VERTEX, EDGE), (EDGE, FACE)):
            for key in self.el.keys(parent):
                if key in self.out.names:
                    continue
                children = self.el.outer_edges(key) if parent == FACE else self.el.subs(key, child)
                found = []
                for ckey, _ in children:
                    if (not delayed and claims.get(ckey)) or ckey not in self.out.names:
                        found = []
                        break
                    if self.out.names[ckey] not in found:
                        found.append(self.out.names[ckey])
                if not found:
                    unnamed = True
                    continue
                found.sort()
                text = LOWER
                if len(found) > 1:
                    text += "(" + "|".join(found[1:1 + MAX_EXTRA]) + ")"
                self._store(key, _encode(found[0], text, self.op, self.tag, key[0]))
        return unnamed

    # ------------------------------------------------------------------ the whole step
    def run(self):
        claims = self._claims()
        self._carry_forward()
        delayed = False
        while True:
            self._from_history(claims, delayed)
            self._from_higher(claims, delayed)
            unnamed = self._from_lower(claims, delayed)
            if not unnamed or delayed or not any(claims.values()):
                return self.out
            delayed = True


def stamp_all(named: NamedShape, tag, op) -> NamedShape:
    """`named` with the pass-through stamp of operation `op` and feature `tag` on every name, as `name_result`
    gives an element the operation left alone (§ 2). A name that already ends in this tag stays as it is.
    The result remembers the unstamped map in `stamped_from`: if the same feature then runs on this piece too,
    `features.name_feature` names from that map, so the piece is stamped once, as a part the feature ran on."""
    op = OP_FOR.get(op, op)
    return NamedShape(named.shape, {k: _encode(v, "", op, tag, k[0]) for k, v in named.names.items()},
                      (tag, named))


def name_result(shape, history, sources, tag, op=OP_MAKER, stamp=True) -> NamedShape:
    """The names of a kernel helper's result.

    `shape` and `history` are what the helper returned with `with_history=True`, `sources` are the
    `NamedShape`s it was given (in the order it took them), `tag` is the feature id the operation
    belongs to and `op` its op code -- `OP_FOR[<helper name>]`.

    `stamp=False` is a FreeShape departure from § 2: an element the operation left alone keeps its
    name exactly, instead of gaining the new op code and tag. A part a later feature merely touches
    then keeps the names of its other faces, so they stay short and identical across the feature.
    """
    if shape is None:
        raise NamingError("nothing to name: the operation returned no shape")
    sources = [s for s in sources if s is not None]
    return _Naming(shape, history, sources, tag, op, stamp).run()


# --------------------------------------------------------------------------- sketch roots (§ 4)

def name_sketch(regions, history, edges, tag) -> NamedShape:
    """The names of a sketch's regions, their edges and their vertices.

    `regions`, `history` and `edges` are what `kernel.sketch_regions(..., with_history=True)` returns:
    the region faces, the Splitter's history and the (sketch entity id, edge) pairs it split the
    plane with. `tag` is the sketch feature's id.

    FreeCAD names a sketch's geometry by the sketch's own geometry ids before any solid exists (§ 4),
    so each input edge is seeded as `<entity id>;SKT;:H<tag>` and its vertices as
    `<entity id>v<n>;SKT;...`. The Splitter's history then carries those names onto the region
    edges: an edge the split left alone keeps its name, one it rebuilt gets `;:M`, and an edge cut
    in pieces gives `;:M`, `;:M2`, ... in the order OCCT reports them (§ 4 "An edge is split").
    Region faces have no history of their own, so the forward pass names each one from the edges of
    its outer wire, `;:L` -- the rule FreeCAD applies to a pad's faces, and why a hole in a region
    does not rename it. Only the regions are named; the unbounded pieces of the split plane are not
    part of the result, so their history entries are skipped (§ 3).
    """
    seeds = []
    for i, (eid, edge) in enumerate(edges, 1):
        label = str(eid) if eid else f"n{i}"         # no entity id: only a caller outside a sketch
        el = _Elements(edge)
        labels = {(EDGE, 1): label}
        labels.update({(VERTEX, k): f"{label}v{k}" for _, k in el.keys(VERTEX)})
        seeds.append(seed(edge, tag, OP_SKETCH, labels))
    if not regions:
        return NamedShape(None, {})
    return name_result(K.compound(regions), history, seeds, tag, op=None)
