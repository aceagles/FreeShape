"""A sketch being edited by one client of `freeshape serve` (I7.3, ADR 0014, ADR 0015). No Qt, no kernel.

The server keeps one `SketchSession` per client id, in that client's registry entry (serve.py, "Sketch sessions"), so
it survives a reconnect. The session holds the sketch (freeshape.sketch.Sketch), the plane it lies on and the
undo and redo stacks. Every change is one step: the state is copied onto the undo stack, the change is applied,
the sketch is solved, and the changed sketch with its solve status is the answer. A step that cannot be done
(bad input, a degenerate shape, a solve that leaves a coordinate that is not a finite number) leaves the sketch
exactly as it was and raises SketchError with a sentence to show.

Shapes are made by the shared builders of freeshape.sketch_ops (I7.2), as the desktop's Sketcher calls them, and
never re-implemented here; a point and a line are a single `add_point` / `add_line`, as on the desktop.

Message bodies (the dicts `add`, `delete` and `construction` take) are checked here, field by field, and a bad one
is a SketchError naming the field. See `add` for the shapes, picks and constraints.

Imports only numpy and freeshape.sketch / freeshape.sketch_ops, which load the CAD kernel only for text, splines and
used model edges, never on the paths here (tests/test_sketch_session.py checks sys.modules).
"""
from __future__ import annotations

import copy
import math
import threading
import time

from . import sketch_ops as OPS
from .sketch import ORIGIN, Sketch

MAX_UNDO = 300                  # undo steps a session keeps (the desktop's limit)
MAX_ITEMS = 5000                # points plus entities one sketch may hold through a session
MAX_CONSTRAINTS = 5000          # constraints one sketch may hold through a session
MAX_STEP_CONSTRAINTS = 64       # constraints one add message may attach
MAX_IDS = 1000                  # ids one delete or construction message may name
MAX_COORD = 1e9                 # largest absolute coordinate a pick may give (mm)

SHAPES = {                      # shape -> number of picks, in the order the desktop's tool is clicked
    "point": 1,                 # the point
    "line": 2,                  # start, end
    "rect": 2,                  # corner, opposite corner              (sketch_ops.rect)
    "center_rect": 2,           # centre, corner                       (sketch_ops.center_rect)
    "circle": 1,                # centre, plus "radius"                (sketch_ops.circle)
    "arc3": 3,                  # start, end, a point the arc passes   (sketch_ops.arc3)
    "arc_center": 3,            # centre, start, end direction, plus "sweep" (sketch_ops.arc_center)
}

# The constraints an add message may attach: type -> the accepted ref kinds, one tuple per accepted form.
#   P  a point id                    L  a line             R  a circle or an arc
#   C  a circle, arc or ellipse, or a point (its centre is used)
#   Q  a point, or a circle, arc, ellipse or point entity (its centre / point is used)
#   K  a line, circle, arc, ellipse or spline (a curve a point can lie on)
CONSTRAINT_FORMS = {
    "coincident": [("P", "P")],
    "point_on": [("P", "K")],
    "horizontal": [("L",), ("P", "P")],
    "vertical": [("L",), ("P", "P")],
    "parallel": [("L", "L")],
    "perpendicular": [("L", "L")],
    "equal": [("L", "L"), ("R", "R")],
    "concentric": [("C", "C")],
    "midpoint": [("P", "L")],
    "tangent": [("L", "R"), ("R", "L"), ("R", "R")],
    "symmetric": [("P", "P", "L")],
    "fix": [("P",)],
    "distance": [("L",), ("Q", "Q"), ("P", "L"), ("L", "P")],
    "hdistance": [("L",), ("Q", "Q"), ("P", "L"), ("L", "P")],
    "vdistance": [("L",), ("Q", "Q"), ("P", "L"), ("L", "P")],
    "radius": [("R",)],
    "diameter": [("R",)],
    "angle": [("L", "L")],
}
POSITIVE = {"distance", "hdistance", "vdistance", "radius", "diameter"}      # value > 0
NEEDS_VALUE = POSITIVE | {"angle"}


class SketchError(ValueError):
    """A sketch message that cannot be applied; the message names the field or says why. Nothing changed."""


# ---------------------------------------------------------------------------------------------- checks
def _finite(value, at, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SketchError(f"{at}: must be a number")
    v = float(value)
    if not math.isfinite(v) or abs(v) > MAX_COORD:
        raise SketchError(f"{at}: must be a finite number")
    if positive and v <= 0:
        raise SketchError(f"{at}: must be more than 0")
    return v


def _uv(value, at):
    if not isinstance(value, list) or len(value) != 2:
        raise SketchError(f"{at}: must be [u, v], two numbers")
    return [_finite(value[0], f"{at}[0]"), _finite(value[1], f"{at}[1]")]


def _only(obj, allowed, at):
    if not isinstance(obj, dict):
        raise SketchError(f"{at}: must be an object")
    extra = sorted(str(k) for k in obj if k not in allowed)
    if extra:
        raise SketchError(f"{at}.{extra[0][:40]}: unknown field")


def _ids(value, at):
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_IDS:
        raise SketchError(f"{at}: must be a list of 1 to {MAX_IDS} ids")
    for i, x in enumerate(value):
        if not isinstance(x, str) or not 1 <= len(x) <= 64:
            raise SketchError(f"{at}[{i}]: must be an id")
    return list(dict.fromkeys(value))


def status_of(sk: Sketch):
    """The solve status of `sk` (after `sk.solve()`), per item, as the desktop colours it:
        {"ok": every constraint met, "dof": degrees of freedom left,
         "entities": {id: "free" | "defined" | "conflicting"}, "points": {id: same},
         "constraints": {id: "ok" | "conflicting"}}
    "conflicting": named by a constraint the solve could not meet; "free": it can still move; "defined": fully
    defined. Construction is not a status: it is the entity's own "construction" flag."""
    st = sk.status or {}
    bad = set(st.get("bad", ()))
    free = set(st.get("free", ()))
    refs = set()
    for c in sk.constraints:
        if c.get("id") in bad:
            refs.update(c.get("refs", ()))

    def one(i, moves):
        return "conflicting" if i in refs else ("free" if moves else "defined")
    return {"ok": bool(st.get("ok", True)), "dof": int(st.get("dof", 0)),
            "entities": {eid: one(eid, sk.is_entity_free(eid)) for eid in sk.entities},
            "points": {pid: one(pid, pid in free) for pid in sk.points},
            "constraints": {c["id"]: ("conflicting" if c["id"] in bad else "ok") for c in sk.constraints}}


# ---------------------------------------------------------------------------------------------- the session
class SketchSession:
    """One client's sketch. `feature` is the id of the stored sketch feature being edited, or None for a new
    sketch; `plane` the plane reference the feature stores (its `plane` param); `frame` and `edges` what the
    engine's sketch_frame gave (I7.1). Use `lock` around every call (two sockets of one client may send at once)."""

    def __init__(self, document, tab, feature, plane, sketch=None, frame=None, edges=None, name=None):
        self.document = document
        self.tab = tab
        self.feature = feature
        self.plane = copy.deepcopy(plane)
        self.name = name
        self.frame = frame or {}
        self.edges = edges or []
        self.sk = Sketch.from_dict(sketch or {})
        self.undo_stack = []
        self.redo_stack = []
        self.lock = threading.Lock()
        self.stored = None                     # the stored feature dict of an existing sketch (the server sets it)
        self.committing = False                # a commit is on its way (the server sets it, under `lock`)
        self.drag = None                       # the drag in progress (drag_begin), or None
        self.drag_pending = None               # serve.py: the one move waiting for the solver (latest wins)
        self.drag_worker = None                # serve.py: the task that solves the waiting moves
        self.drag_solves = 0                   # moves solved so far (tests count them)
        work = self.sk.copy()                  # the stored sketch is shown as stored; its status from a solved copy
        work.solve()
        self._status = status_of(work)

    # ------------------------------------------------------------------ reading
    def view(self):
        """{"document", "tab", "feature", "name", "plane", "sketch": {points, entities, constraints}, "status",
        "can_undo", "can_redo"}."""
        return {"document": self.document, "tab": self.tab, "feature": self.feature, "name": self.name,
                "plane": copy.deepcopy(self.plane), "sketch": self.sk.to_dict(), "status": self._status,
                "can_undo": bool(self.undo_stack), "can_redo": bool(self.redo_stack)}

    def params(self):
        """The sketch feature's params to store at commit: {"plane", "sketch"}."""
        return {"plane": copy.deepcopy(self.plane), "sketch": self.sk.to_dict()}

    # ------------------------------------------------------------------ one step
    def _step(self, change):
        """Run `change()` as one undoable step, then solve. Any failure puts the sketch back as it was."""
        self._not_dragging()
        before = self.sk.to_dict()
        status = self._status
        try:
            out = change()
            self._check_size()
            self.sk.solve()
            self._check_finite()
        except SketchError:
            self.sk = Sketch.from_dict(before)
            self._status = status
            raise
        except (ValueError, KeyError, IndexError, TypeError, ZeroDivisionError, ArithmeticError) as e:
            self.sk = Sketch.from_dict(before)
            self._status = status
            raise SketchError(str(e) if isinstance(e, ValueError) and str(e) else
                              f"the change could not be made ({type(e).__name__})") from None
        self._status = status_of(self.sk)
        self.undo_stack.append(before)
        if len(self.undo_stack) > MAX_UNDO:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        return out

    def _check_size(self):
        if len(self.sk.points) + len(self.sk.entities) > MAX_ITEMS:
            raise SketchError(f"a sketch edited here holds at most {MAX_ITEMS} points and entities")
        if len(self.sk.constraints) > MAX_CONSTRAINTS:
            raise SketchError(f"a sketch edited here holds at most {MAX_CONSTRAINTS} constraints")

    def _check_finite(self):
        for pid, p in self.sk.points.items():
            if not all(math.isfinite(float(x)) for x in p):
                raise SketchError("the solve did not converge to a finite position")
        for e in self.sk.entities.values():
            if "r" in e and not math.isfinite(float(e["r"])):
                raise SketchError("the solve did not converge to a finite radius")

    # ------------------------------------------------------------------ geometry
    def add(self, body):
        """Add one shape, with optional constraints, as one step. `body`:
            {"shape": one of SHAPES, "picks": [pick, ...] (as many as SHAPES says, in that order),
             "construction": bool (default false), "radius": number (circle only), "sweep": number (arc_center
             only: below 0 the pen went clockwise and the ends swap; default 1),
             "constraints": [{"type", "refs": [ref, ...], "value"?}, ...] (optional, at most 64)}
        A pick is [u, v] (a new point there, sketch coordinates) or {"at": [u, v], "point": id} (that existing
        point is used; "origin" is the sketch origin). A constraint's ref is an id already in the sketch or a
        placeholder for what this step makes: "$p<i>" the point of pick i, "$e<i>" the i-th entity of "made"
        (below). A coincident between a point this step made and one it did not merges the new point into the
        old one (the desktop's snap), so the shape shares that point; every other constraint is added as given.
        Returns {"points": [point id per pick], "entities": [entity ids the builder returned]}: rect four lines
        from the first corner round, center_rect those and the helper diagonal, circle/arc/line one id, point the
        point entity's id."""
        _only(body, {"shape", "picks", "construction", "radius", "sweep", "constraints"}, "add")
        shape = body.get("shape")
        if shape not in SHAPES:
            raise SketchError(f"add.shape: must be one of {', '.join(SHAPES)}")
        picks = body.get("picks")
        if not isinstance(picks, list) or len(picks) != SHAPES[shape]:
            raise SketchError(f"add.picks: a {shape} takes {SHAPES[shape]} pick(s)")
        parsed = [self._parse_pick(p, f"add.picks[{i}]") for i, p in enumerate(picks)]
        cons = body.get("construction", False)
        if not isinstance(cons, bool):
            raise SketchError("add.construction: must be true or false")
        radius = sweep = None
        if shape == "circle":
            radius = _finite(body.get("radius"), "add.radius", positive=True)
        elif "radius" in body:
            raise SketchError("add.radius: only a circle takes a radius")
        if shape == "arc_center":
            sweep = _finite(body.get("sweep", 1.0), "add.sweep")
        elif "sweep" in body:
            raise SketchError("add.sweep: only arc_center takes a sweep")
        wanted = body.get("constraints", [])
        if not isinstance(wanted, list) or len(wanted) > MAX_STEP_CONSTRAINTS:
            raise SketchError(f"add.constraints: must be a list of at most {MAX_STEP_CONSTRAINTS}")
        for i, c in enumerate(wanted):
            _only(c, {"type", "refs", "value"}, f"add.constraints[{i}]")
        return self._step(lambda: self._add(shape, parsed, cons, radius, sweep, wanted))

    def _parse_pick(self, p, at):
        if isinstance(p, dict):
            _only(p, {"at", "point"}, at)
            uv = _uv(p.get("at"), f"{at}.at")
            pid = p.get("point")
            if not isinstance(pid, str) or pid not in self.sk.points:
                raise SketchError(f"{at}.point: no such point in the sketch")
            return uv, pid
        return _uv(p, at), None

    def _add(self, shape, parsed, cons, radius, sweep, wanted):
        sk = self.sk
        got = [None] * len(parsed)            # point id per pick
        new = set()                           # point ids this step made

        def src(i):
            uv, pid = parsed[i]

            def make():
                if got[i] is None:
                    if pid is not None:
                        got[i] = pid
                    else:
                        got[i] = sk.add_point(*uv)
                        new.add(got[i])
                return got[i]
            return (uv, make)

        if shape == "point":
            ents = [sk.add_point_entity(src(0)[1]())]
        elif shape == "line":
            a, b = src(0)[1](), src(1)[1]()
            if a == b or float(math.dist(sk.points[a], sk.points[b])) < OPS.TOL:
                raise SketchError("The line's ends are in the same place")
            ents = [sk.add_line(a, b, cons)]
        elif shape == "rect":
            ents = OPS.rect(sk, src(0), src(1), cons)
        elif shape == "center_rect":
            ents = OPS.center_rect(sk, src(0), src(1), cons)
        elif shape == "circle":
            ents = [OPS.circle(sk, src(0), radius, cons)]
        elif shape == "arc3":
            ents = [OPS.arc3(sk, src(0), src(1), src(2), cons)]
        else:
            ents = [OPS.arc_center(sk, src(0), src(1), src(2), sweep, cons)]
        for i in range(len(got)):              # a pick a builder never asked for (none today) still gets its point
            if got[i] is None:
                src(i)[1]()
        made = {"points": got, "entities": ents}
        for i, c in enumerate(wanted):
            self._attach(c, made, new, f"add.constraints[{i}]")
        return made

    def _resolve(self, ref, made, at):
        if isinstance(ref, str) and ref[:2] in ("$p", "$e") and ref[2:].isdigit() and len(ref) <= 6:
            table = made["points"] if ref[1] == "p" else made["entities"]
            n = int(ref[2:])
            if n >= len(table):
                raise SketchError(f"{at}: this step made no {ref}")
            return table[n]
        if not isinstance(ref, str) or not (ref in self.sk.points or ref in self.sk.entities):
            raise SketchError(f"{at}: no such point or entity in the sketch")
        return ref

    def _kind_ok(self, ref, kind):
        sk = self.sk
        e = sk.entities.get(ref)
        t = e["type"] if e is not None else None
        if kind == "P":
            return ref in sk.points
        if kind == "L":
            return t == "line"
        if kind == "R":
            return t in ("circle", "arc")
        if kind == "C":
            return ref in sk.points or t in ("circle", "arc", "ellipse")
        if kind == "Q":
            return ref in sk.points or t in ("circle", "arc", "ellipse", "point")
        if kind == "K":
            return t in ("line", "circle", "arc", "ellipse", "spline")
        return False

    def _attach(self, c, made, new, at):
        sk = self.sk
        ctype = c.get("type")
        forms = CONSTRAINT_FORMS.get(ctype) if isinstance(ctype, str) else None
        if forms is None:
            raise SketchError(f"{at}.type: must be one of {', '.join(CONSTRAINT_FORMS)}")
        refs = c.get("refs")
        if not isinstance(refs, list) or not 1 <= len(refs) <= 3:
            raise SketchError(f"{at}.refs: must be a list of 1 to 3 ids")
        refs = [self._resolve(r, made, f"{at}.refs[{i}]") for i, r in enumerate(refs)]
        if len(set(refs)) != len(refs):
            raise SketchError(f"{at}.refs: an item is named twice")
        if not any(len(f) == len(refs) and all(self._kind_ok(r, k) for r, k in zip(refs, f)) for f in forms):
            raise SketchError(f"{at}.refs: a {ctype} constraint does not take these items")
        value = c.get("value")
        if ctype in NEEDS_VALUE:
            value = _finite(value, f"{at}.value", positive=ctype in POSITIVE)
            if ctype == "angle" and not 0 <= value <= 180:
                raise SketchError(f"{at}.value: an angle is 0 to 180 degrees")
        elif ctype == "fix":
            value = list(sk.P(refs[0])) if value is None else _uv(value, f"{at}.value")
        elif ctype == "tangent" and value is not None:
            value = _finite(value, f"{at}.value")         # arc to arc: below 0 internal, else external
        elif value is not None:
            raise SketchError(f"{at}.value: a {ctype} constraint takes no value")
        if ctype == "coincident" and (refs[0] in new) != (refs[1] in new):
            keep, drop = (refs[1], refs[0]) if refs[0] in new else (refs[0], refs[1])
            sk.merge_points(keep, drop)
            made["points"] = [keep if p == drop else p for p in made["points"]]
            new.discard(drop)
            return
        sk.add_constraint(ctype, *refs, value=value)

    def delete(self, body):
        """{"ids": [id, ...]}: delete those entities, points or constraints, and what depends on them (the
        desktop's delete). The origin cannot be deleted."""
        _only(body, {"ids"}, "delete")
        ids = _ids(body.get("ids"), "delete.ids")
        known = set(self.sk.points) | set(self.sk.entities) | {c["id"] for c in self.sk.constraints}
        for i, x in enumerate(ids):
            if x == ORIGIN:
                raise SketchError(f"delete.ids[{i}]: the origin cannot be deleted")
            if x not in known:
                raise SketchError(f"delete.ids[{i}]: no such item in the sketch")
        return self._step(lambda: self.sk.delete(ids))

    def construction(self, body):
        """{"ids": [entity id, ...], "value": bool (optional)}: set those entities' construction flag to `value`,
        or, without it, turn each one's flag over. A point entity is always construction and is refused."""
        _only(body, {"ids", "value"}, "construction")
        ids = _ids(body.get("ids"), "construction.ids")
        value = body.get("value")
        if value is not None and not isinstance(value, bool):
            raise SketchError("construction.value: must be true or false")
        for i, x in enumerate(ids):
            e = self.sk.entities.get(x)
            if e is None or e["type"] == "point":
                raise SketchError(f"construction.ids[{i}]: not a line, curve or text of the sketch")

        def flip():
            for x in ids:
                e = self.sk.entities[x]
                e["construction"] = (not e.get("construction", False)) if value is None else value
        return self._step(flip)

    # ------------------------------------------------------------------ constraints (I7.5)
    def constrain(self, body):
        """{"tool": a name of OPS.FIT_TOOLS, "picks": [point or entity id, ...]}: what the desktop's constraint tool
        makes of those picks (sketch_ops.constraints_for), as one step. A pick that is not a point or entity of the
        sketch, or picks the tool cannot use, are a SketchError. If the solve cannot meet a new constraint (or the
        sketch was solvable and no longer is) the sketch, its status and the undo and redo stacks are left as they
        were. Returns {"accepted": bool, "made": [constraint ids] (those added), "conflicts": [constraint ids] (the
        new ones the solve could not meet, when not accepted)}."""
        _only(body, {"tool", "picks"}, "constrain")
        tool = body.get("tool")
        if tool not in OPS.FIT_TOOLS:
            raise SketchError(f"constrain.tool: must be one of {', '.join(OPS.FIT_TOOLS)}")
        picks = _ids(body.get("picks"), "constrain.picks")
        for i, x in enumerate(picks):
            if x not in self.sk.points and x not in self.sk.entities:
                raise SketchError(f"constrain.picks[{i}]: no such point or entity in the sketch")
        self._not_dragging()
        made = OPS.constraints_for(self.sk, tool, picks)
        if not made:
            raise SketchError(f"constrain.picks: a {tool} constraint does not take these items")
        before_ids = {c["id"] for c in self.sk.constraints}
        before, status, redo = self.sk.to_dict(), self._status, list(self.redo_stack)
        new = self._step(lambda: [c["id"] for c in OPS.apply_constraints(self.sk, tool, made)])
        bad = [i for i in new if self._status["constraints"].get(i) == "conflicting"]
        if bad or (status["ok"] and not self._status["ok"]):
            self.sk = Sketch.from_dict(before)
            self._status = status
            self.undo_stack.pop()
            self.redo_stack[:] = redo
            return {"accepted": False, "made": [], "conflicts": bad}
        return {"accepted": True, "made": [i for i in new if i not in before_ids], "conflicts": []}

    def unconstrain(self, body):
        """{"constraint": constraint id}: remove that one constraint (nothing else), as one step. (Not "id": a
        message's own "id" is the request id the answer echoes.)"""
        _only(body, {"constraint"}, "unconstrain")
        self._not_dragging()
        cid = body.get("constraint")
        if not isinstance(cid, str) or not any(c["id"] == cid for c in self.sk.constraints):
            raise SketchError("unconstrain.constraint: no such constraint in the sketch")
        self._step(lambda: self.sk.delete([cid]))

    def fits(self, body):
        """{"selection": [point or entity id, ...]}: the constraint tools that take that selection
        (sketch_ops.fits), in OPS.FIT_TOOLS order; nothing is changed."""
        _only(body, {"selection"}, "fits")
        sel = body.get("selection")
        if not isinstance(sel, list) or len(sel) > MAX_IDS or not all(isinstance(x, str) for x in sel):
            raise SketchError(f"fits.selection: must be a list of at most {MAX_IDS} ids")
        for i, x in enumerate(sel):
            if x not in self.sk.points and x not in self.sk.entities:
                raise SketchError(f"fits.selection[{i}]: no such point or entity in the sketch")
        return OPS.fits(self.sk, sel)

    # ------------------------------------------------------------------ undo
    def undo(self):
        self._not_dragging()
        if not self.undo_stack:
            raise SketchError("nothing to undo")
        self.redo_stack.append(self.sk.to_dict())
        self._restore(self.undo_stack.pop())

    def redo(self):
        self._not_dragging()
        if not self.redo_stack:
            raise SketchError("nothing to redo")
        self.undo_stack.append(self.sk.to_dict())
        self._restore(self.redo_stack.pop())

    def _restore(self, d):
        self.sk = Sketch.from_dict(d)          # exactly the state the step started from
        work = self.sk.copy()
        work.solve()
        self._status = status_of(work)


    # ------------------------------------------------------------------ drag (I7.4)
    def _not_dragging(self):
        if self.drag is not None:
            raise SketchError("a drag is in progress; end it first")

    def drag_begin(self, body):
        """{"point": id | "entity": id, "at": [u, v]}: start dragging a point or an entity from `at`. The sketch
        stays as it is (every move is solved from this state, so nothing drifts) until drag_end."""
        _only(body, {"point", "entity", "at"}, "drag")
        self._not_dragging()
        if ("point" in body) == ("entity" in body):
            raise SketchError("drag: name either point or entity")
        start = _uv(body.get("at"), "drag.at")
        kind = "point" if "point" in body else "entity"
        item = body[kind]
        table = self.sk.points if kind == "point" else self.sk.entities
        if not isinstance(item, str) or item not in table:
            raise SketchError(f"drag.{kind}: no such {kind} in the sketch")
        if item == ORIGIN:
            raise SketchError("drag.point: the origin cannot be moved")
        if kind == "entity" and self.sk.entities[item]["type"] == "point":
            item = self.sk.entities[item].get("p", item)       # a point entity is dragged by its point
            kind = "point"
        self.drag = {"kind": kind, "id": item, "start": start, "orig": self.sk.to_dict(), "cur": None}

    def drag_move(self, body):
        """{"at": [u, v], "t"?: the client's send time}: solve the sketch as it was at drag_begin with the dragged
        items moved by the offset from the start and held heavily (the desktop's Sketcher.mouse_drag). Returns
        {"t", "solve_ms", "points": {id: [u, v]}, "radii": {entity id: r}, "held": bool}: `held` true when the
        constraints would not let the items go there and the original positions are returned. Does not change
        the sketch; drag_end keeps the last result."""
        _only(body, {"at", "t"}, "drag")
        d = self.drag
        if d is None:
            raise SketchError("no drag is in progress")
        uv = _uv(body.get("at"), "drag.at")
        t = body.get("t")
        if t is not None:
            t = _finite(t, "drag.t")
        began = time.perf_counter()
        base = Sketch.from_dict(d["orig"])
        work = base.copy()
        sk, kind, i = work, d["kind"], d["id"]
        delta = [uv[0] - d["start"][0], uv[1] - d["start"][1]]
        weights = {}
        if kind == "point":
            sk.points[i] = [float(base.points[i][0]) + delta[0], float(base.points[i][1]) + delta[1]]
            weights = {(i, 0): 1e3, (i, 1): 1e3}
        elif sk.entities[i]["type"] == "circle":
            c = base.points[sk.entities[i]["c"]]
            sk.entities[i]["r"] = float(max(math.hypot(uv[0] - c[0], uv[1] - c[1]), 1e-3))
            weights = {(i, "r"): 1e3}
        else:
            for pid in sk.entity_points(i):
                if pid != ORIGIN:
                    sk.points[pid] = [float(base.points[pid][0]) + delta[0], float(base.points[pid][1]) + delta[1]]
                    weights[(pid, 0)] = weights[(pid, 1)] = 1e3
        held = False
        try:
            sk.solve(weights=weights)
            held = not sk.status.get("ok", True)
            if not held:
                self.sk, saved = work, self.sk
                try:
                    self._check_finite()
                finally:
                    self.sk = saved
        except (SketchError, ValueError, KeyError, IndexError, TypeError, ZeroDivisionError, ArithmeticError):
            held = True
        shown = base if held else work
        d["cur"] = None if held else work
        self.drag_solves += 1
        return {"t": t, "solve_ms": max(time.perf_counter() - began, 1e-6) * 1000.0,
                "points": {k: [float(x) for x in p] for k, p in shown.points.items()},
                "radii": {k: float(e["r"]) for k, e in shown.entities.items() if "r" in e}, "held": held}

    @staticmethod
    def _same(orig, cur):
        """True when `cur` (a solved Sketch) has every point and radius of `orig` (a dict) to within 1e-6 mm."""
        for k, p in orig["points"].items():
            if max(abs(float(a) - float(b)) for a, b in zip(p, cur.points[k])) > 1e-6:
                return False
        return all(abs(float(e["r"]) - float(cur.entities[k]["r"])) <= 1e-6
                   for k, e in orig["entities"].items() if "r" in e)

    def drag_end(self, cancel=False):
        """End the drag. The last solved position becomes the sketch as ONE undo step; a drag that never moved,
        or whose last move was held or left everything where it was, changes nothing and adds no step. `cancel` drops the result."""
        d, self.drag = self.drag, None
        if d is None:
            raise SketchError("no drag is in progress")
        if cancel or d["cur"] is None or self._same(d["orig"], d["cur"]):
            return
        self.undo_stack.append(self.sk.to_dict())
        if len(self.undo_stack) > MAX_UNDO:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self.sk = d["cur"]
        self._status = status_of(self.sk)
