"""A mate dialog session, without Qt (I10.5): the mate being added or edited in an Assembly, its picks and its preview.

The desktop's MateDialog (freeshape/ui/assembly_ui.py) keeps its widgets and hands the state to a MateSession; the
server keeps one MateSession per open mate dialog in the Assembly tab's engine worker (engine.executor.AssemblyTab,
the `mate_dialog_*` requests of contract.Engine) and serves it on .../tabs/<tab>/mates/dialog (serve.py).

The session edits a deep copy of the mate. `preview()` puts the Assembly into the previewed state (the instances it
backed up, the mate in place of or added to the mates, the side moved onto the other with `place_for_mate`, then
`solve`) and leaves it there, as the desktop shows it while the dialog is open; `restore()` puts back the instances
and mates backed up. `preview_detached()` is the server's: it backs up what is there now, previews, reads the
placements, the Mate values and the error, and restores, so the committed Assembly is never left in a preview.
Accept is the caller's: `result()` gives the mate to store with `add_mate` (new) or `replace_mate` (edited)."""
from __future__ import annotations

import copy
import re

from . import assembly as A

NEED_TWO_PARTS = "Insert at least two parts before adding a mate"
SAME_PART = "Pick the second connector on a different part."
NEED_BOTH = "Pick a mate connector on each of the two parts first."

VALUE_LABELS = {"rz": "Rotation", "tz": "Slide", "tx": "Slide X", "ty": "Slide Y"}

OPTIONS = ("type", "name", "flip", "offset", "angle", "turn90", "a", "b")   # what an update may set, applied in order
MAX_NAME = 200


class MateSessionError(Exception):
    """A request the dialog refuses, with the message the desktop shows in the dialog."""


def value_unit(dof):
    return "°" if dof.startswith("r") else "mm"


def can_start(doc):
    """Whether a mate dialog can open on `doc`: it needs at least two instances that have a part."""
    return len([i for i in doc.instances if i["id"] in doc.parts]) >= 2


def other(side):
    return "b" if side == "a" else "a"


def _default_name(doc, mtype):
    """The name `doc.make_mate(mtype)` would give, without taking an id."""
    n = 1
    label = A.MATE_TYPES[mtype][0]
    names = {m["name"] for m in doc.mates}
    while f"{label} {n}" in names:
        n += 1
    return f"{label} {n}"


_DEFAULT = re.compile(r"^(.*) (\d+)$")


class MateSession:
    """See the module docstring. `mate` is copied; `is_new` says whether accept adds it or replaces the stored mate of
    its id. `preselection` is [(instance id, "face" | "edge", 0-based index, click point or None)]: the first fills
    side a, the next one on another instance side b (the desktop's preselection: a pick that fails is skipped)."""

    def __init__(self, doc, mate, is_new, preselection=()):
        self.doc = doc
        self.mate = copy.deepcopy(mate)
        self.is_new = bool(is_new)
        self.backup()
        self.preselect(preselection)

    @classmethod
    def start(cls, doc, mtype=None, mate=None, preselection=()):
        """A session for a new mate of `mtype` or for the stored mate of id `mate` (then `mtype` is ignored).
        MateSessionError with the desktop's message when the Assembly has fewer than two parts, or for an unknown
        type or mate."""
        if not can_start(doc):
            raise MateSessionError(NEED_TWO_PARTS)
        if mate is not None:
            m = doc.mate(mate)
            if m is None:
                raise MateSessionError(f"no mate {mate!r} in “{doc.name}”")
            return cls(doc, m, False)
        if mtype not in A.MATE_TYPES:
            raise MateSessionError(f"type: not a mate type ({', '.join(A.MATE_TYPES)})")
        return cls(doc, doc.make_mate(mtype), True, preselection)

    # ------------------------------------------------------------------ state
    def backup(self):
        """Remember the instances and mates as they are now: what `restore` puts back."""
        self._backup = copy.deepcopy(self.doc.instances)
        self._backup_mates = copy.deepcopy(self.doc.mates)

    def restore(self):
        self.doc.instances = copy.deepcopy(self._backup)
        self.doc.mates = copy.deepcopy(self._backup_mates)

    def set_type(self, t, rename=True):
        """Change the mate's type. With `rename`, a new mate whose name is still a default one ("Revolute 1") takes
        the next free default name of the new type."""
        if t not in A.MATE_TYPES:
            raise MateSessionError(f"type: not a mate type ({', '.join(A.MATE_TYPES)})")
        self.mate["type"] = t
        if rename and self.is_new:
            m = _DEFAULT.match(str(self.mate.get("name") or ""))
            if m is not None and m.group(1) in [v[0] for v in A.MATE_TYPES.values()]:
                self.mate["name"] = _default_name(self.doc, t)

    def rotate90(self):
        """Turn the second part 90 degrees about Z: the angle goes up by 90, modulo 360. Returns the new angle."""
        a = (float(self.mate.get("angle", 0.0) or 0.0) + 90.0) % 360.0
        self.mate["angle"] = a
        return a

    def clear(self, side):
        self.mate[side] = None

    def set_side(self, side, c):
        """Use connector `c` for `side`. MateSessionError(SAME_PART) when the other side is on the same instance.
        Returns the side to fill next ("b" after a when b is empty, "a" after b when a is), else None."""
        o = self.mate.get(other(side))
        if o and o["inst"] == c["inst"]:
            raise MateSessionError(SAME_PART)
        self.mate[side] = c
        if side == "a" and not self.mate.get("b"):
            return "b"
        if side == "b" and not self.mate.get("a"):
            return "a"
        return None

    def pick_element(self, side, iid, kind, index, click=None, snap=None):
        """A face or edge pick (0-based `index`) of instance `iid`, through `AssemblyDoc.connector`."""
        try:
            c = self.doc.connector(iid, kind, index, click, snap)
        except Exception as e:                                   # noqa: BLE001  (the desktop shows any failure)
            raise MateSessionError(f"Can't use that: {e}") from e
        return self.set_side(side, c)

    def pick_named(self, side, iid, cid):
        """An instance's own connector `cid` (a published mate connector or a named one), through `named_connector`."""
        try:
            c = self.doc.named_connector(iid, cid)
        except Exception as e:                                   # noqa: BLE001
            raise MateSessionError(f"Can't use that: {e}") from e
        return self.set_side(side, c)

    def pick_sketch(self, side, iid, eid, role):
        """A role of sketch entity `eid` on Sketch instance `iid` (desktop only), through `sketch_end`."""
        try:
            c = self.doc.sketch_end(iid, eid, role)
        except Exception as e:                                   # noqa: BLE001
            raise MateSessionError(f"Can't use that: {e}") from e
        return self.set_side(side, c)

    def preselect(self, picks):
        for iid, kind, idx, click in picks:
            if kind not in ("face", "edge"):
                continue
            side = "a" if not self.mate.get("a") else "b" if not self.mate.get("b") else None
            if side is None:
                break
            if side == "b" and self.mate["a"]["inst"] == iid:
                continue
            try:
                self.mate[side] = self.doc.connector(iid, kind, idx, click)
            except Exception:                                    # noqa: BLE001
                pass

    def apply(self, changes):
        """Apply an update (`parse_changes`'s output) in OPTIONS order. A refused pick leaves its side as it was;
        the first refusal's message is returned (None when none was refused)."""
        refused = None
        for key in OPTIONS:
            if key not in changes:
                continue
            v = changes[key]
            try:
                if key == "type":
                    self.set_type(v)
                elif key == "turn90":
                    if v:
                        self.rotate90()
                elif key in ("a", "b"):
                    if v is None:
                        self.clear(key)
                    elif "connector" in v:
                        self.pick_named(key, v["instance"], v["connector"])
                    else:
                        # as the desktop: a click point picks the end of a round face; an edge takes none
                        click = v.get("point") if v["kind"] == "face" else None
                        self.pick_element(key, v["instance"], v["kind"], v["index"], click)
                else:
                    self.mate[key] = v
            except MateSessionError as e:
                if key == "type":
                    raise
                refused = refused or str(e)
        return refused

    # ------------------------------------------------------------------ preview
    def complete(self):
        return bool(self.mate.get("a") and self.mate.get("b"))

    def preview(self):
        """Put the Assembly into the previewed state and leave it there; returns the mate's error ("" for none)."""
        self.restore()
        m = self.mate
        if self.complete():
            mates = [x for x in self.doc.mates if x["id"] != m["id"]]
            mates.append(m)
            self.doc.mates = mates
            self.doc.place_for_mate(m)
            self.doc.solve()
            return self.doc.errors.get(m["id"]) or ""
        self.doc.solve()
        return ""

    def values_source(self):
        """The mate as its Mate values should show: the stored mate's values while the Assembly holds it with the same
        type (they follow the document), else the session's own."""
        live = self.doc.mate(self.mate["id"]) if not self.is_new else None
        src = dict(self.mate)
        if live is not None and live["type"] == self.mate["type"]:
            src["values"] = live.get("values")
        return src

    def values(self):
        """[{"dof", "label", "value", "unit"}] for each Mate value of the mate's type, in MATE_VALUE_DOFS order."""
        src = self.values_source()
        vals = src.get("values") or {}
        return [{"dof": d, "label": VALUE_LABELS.get(d, d), "value": float(vals.get(d, 0.0)), "unit": value_unit(d)}
                for d in A.MATE_VALUE_DOFS.get(src["type"], ())]

    _STATUS = ("errors", "dof", "_conflicts", "_limit_failed", "_limits_active", "_limit_holds")

    def preview_detached(self):
        """Preview on the Assembly as it is now and put it back exactly. Returns {"placements": {instance id: 4x4},
        "values": `values()`, "error": the mate's error or None, "dof": the previewed degrees of freedom, "a", "b":
        `side_view` with the frames placed as previewed}."""
        doc = self.doc
        status = {k: copy.deepcopy(getattr(doc, k)) for k in self._STATUS if hasattr(doc, k)}
        self.backup()
        try:
            err = self.preview()
            out = {"placements": {str(i["id"]): [[float(x) for x in row] for row in doc.T(i["id"])]
                                  for i in doc.instances},
                   "values": self.values(), "error": err or None, "dof": int(doc.dof),
                   "a": self.side_view("a"), "b": self.side_view("b")}
        finally:
            self.restore()
            for k, v in status.items():
                setattr(doc, k, v)
        return out

    # ------------------------------------------------------------------ accept / cancel
    def check_complete(self):
        if not self.complete():
            raise MateSessionError(NEED_BOTH)

    def prune_limits(self):
        """Drop limits on freedoms the (possibly changed) type no longer has."""
        dofs = A.MATE_VALUE_DOFS.get(self.mate["type"], ())
        lim = {d: v for d, v in (self.mate.get("limits") or {}).items() if d in dofs}
        if lim:
            self.mate["limits"] = lim
        else:
            self.mate.pop("limits", None)

    def result(self):
        """The mate to store on accept (a deep copy). MateSessionError(NEED_BOTH) while a side is empty."""
        self.check_complete()
        self.prune_limits()
        return copy.deepcopy(self.mate)

    def cancel(self):
        """Put the instances and mates back as they were and solve (the desktop's reject)."""
        self.restore()
        self.doc.solve()

    # ------------------------------------------------------------------ what a client sees
    def side_view(self, side):
        """One side as a client shows it, or None: {"instance", "kind", "index" (1-based; None for a connector),
        "connector" (a named connector's id or name, else None), "what", "world": {"o", "z", "x"}}."""
        c = self.mate.get(side)
        if not c:
            return None
        try:
            world = A.frame_to_json(self.doc.world_frame(c))
        except Exception:                                        # noqa: BLE001  (an instance that went away)
            world = None
        index = c.get("index")
        return {"instance": str(c["inst"]), "kind": str(c.get("kind")),
                "index": None if index is None or c.get("kind") not in ("face", "edge") else int(index) + 1,
                "connector": c.get("named") or c.get("name"), "what": (c.get("frame") or {}).get("what"),
                "world": None if world is None else {k: [float(x) for x in world[k]] for k in ("o", "z", "x")}}

    def view(self):
        m = self.mate
        return {"mate": str(m["id"]), "new": self.is_new, "type": m["type"], "name": str(m.get("name", "")),
                "flip": bool(m.get("flip")), "offset": float(m.get("offset", 0.0) or 0.0),
                "angle": float(m.get("angle", 0.0) or 0.0), "a": self.side_view("a"), "b": self.side_view("b")}


# =========================================================================== checking a request

def _number(key, v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or abs(v) > 1e9:
        raise ValueError(f"{key}: must be a number")
    return float(v)


def _side(key, v):
    if v is None:
        return None
    if not isinstance(v, dict):
        raise ValueError(f"{key}: must be a pick object or null")
    inst = v.get("instance")
    if not isinstance(inst, str) or not 1 <= len(inst) <= 64:
        raise ValueError(f"{key}.instance: must be an instance id")
    if "connector" in v:
        extra = sorted(set(v) - {"instance", "connector"})
        if extra:
            raise ValueError(f"{key}.{str(extra[0])[:40]}: unknown field")
        cid = v["connector"]
        if not isinstance(cid, str) or not 1 <= len(cid) <= 200:
            raise ValueError(f"{key}.connector: must be a connector id")
        return {"instance": inst, "connector": cid}
    extra = sorted(set(v) - {"instance", "kind", "index", "point"})
    if extra:
        raise ValueError(f"{key}.{str(extra[0])[:40]}: unknown field")
    kind = v.get("kind")
    if kind not in ("face", "edge"):
        raise ValueError(f"{key}.kind: must be \"face\" or \"edge\"")
    index = v.get("index")
    if isinstance(index, bool) or not isinstance(index, int) or not 1 <= index <= 10 ** 7:
        raise ValueError(f"{key}.index: must be a 1-based element index")
    point = v.get("point")
    if point is not None:
        if not isinstance(point, list) or len(point) != 3:
            raise ValueError(f"{key}.point: must be [x, y, z]")
        point = [_number(f"{key}.point", p) for p in point]
    return {"instance": inst, "kind": kind, "index": index - 1, "point": point}


def parse_changes(body):
    """An update body checked and made plain: {key: value} for the OPTIONS keys it names. Raises ValueError naming
    the field for an unknown key or a value of the wrong type. A side's 1-based index comes back 0-based."""
    if not isinstance(body, dict):
        raise ValueError("an update is an object")
    extra = sorted(str(k) for k in body if k not in OPTIONS)
    if extra:
        raise ValueError(f"{extra[0][:40]}: unknown field")
    out = {}
    for key, v in body.items():
        if key == "type":
            if v not in A.MATE_TYPES:
                raise ValueError(f"type: not a mate type ({', '.join(A.MATE_TYPES)})")
            out[key] = v
        elif key == "name":
            if not isinstance(v, str) or not v.strip() or len(v) > MAX_NAME:
                raise ValueError(f"name: must be text of 1..{MAX_NAME} characters")
            out[key] = v.strip()
        elif key in ("flip", "turn90"):
            if not isinstance(v, bool):
                raise ValueError(f"{key}: must be true or false")
            out[key] = v
        elif key in ("offset", "angle"):
            out[key] = _number(key, v)
        else:
            out[key] = _side(key, v)
    return out


def parse_picks(picks):
    """A dialog's preselected picks ([{"instance", "kind", "index" (1-based), "point"?}]) as the
    [(instance, kind, 0-based index, point)] MateSession takes. ValueError naming the pick when one is malformed."""
    if not isinstance(picks, list) or len(picks) > 2:
        raise ValueError("picks: must be a list of at most two face or edge picks")
    out = []
    for n, p in enumerate(picks):
        s = _side(f"picks[{n}]", p)
        if s is None or "connector" in s:
            raise ValueError(f"picks[{n}]: must be a face or edge pick")
        out.append((s["instance"], s["kind"], s["index"], s["point"]))
    return out
