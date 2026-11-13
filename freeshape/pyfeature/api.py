"""The contract a Python feature implements: a `Feature` subclass declares typed parameters and builds a shape.

Plugin modules import from here (`from freeshape.pyfeature.api import Feature, Length, ...`), never from the
`freeshape.pyfeature` package, which loads plugins as it is imported.

The declaration types port the FreeCAD property types the gears addon uses (App::PropertyLength,
PropertyAngle, PropertyIntegerConstraint, PropertyFloat, PropertyBool, PropertyEnumeration). As in a FreeCAD
FeaturePython proxy (e.g. src/Mod/Assembly/JointObject.py: `Joint.__init__` adds properties, `execute`
builds), a feature declares its parameters and `build` makes the shape. Here the declarations are class data,
not `addProperty` calls, so the dialog and the host's validation can read them without an instance.

Parameter values come from a Document, which may be a received, hostile file: `Param.check` accepts only
values of the declared type and range, and evaluates expressions only through `expr.parse_number`.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .. import kernel as K
from ..expr import parse_number, var_names

ID_RE = re.compile(r"[a-z][a-z0-9_]*\Z")          # plugin ids and feature ids
KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")    # parameter keys


class Param:
    """One declared parameter. `visible(params) -> bool` has the meaning of `visible` in ui/dialogs.py SCHEMAS."""
    kind = ""

    def __init__(self, key, label, default, *, visible=None):
        self.key = key
        self.label = label
        self.default = default
        self.visible = visible

    def check(self, value, variables):
        """The value `build` receives for `value` (as stored in a Document); raises K.KernelError naming the
        parameter's label when it is not acceptable."""
        raise NotImplementedError

    def problems(self):
        """Why this declaration is unusable (empty when it is fine). The registry rejects a plugin whose
        features declare a parameter with problems."""
        out = []
        if not isinstance(self.key, str) or not KEY_RE.match(self.key):
            out.append(f"parameter key {self.key!r} must match {KEY_RE.pattern}")
        if not isinstance(self.label, str) or not self.label.strip():
            out.append(f"parameter {self.key!r} needs a label")
        if self.visible is not None and not callable(self.visible):
            out.append(f"parameter {self.key!r}: visible must be callable")
        if not out:
            try:
                self.check(self.default, {})
            except K.KernelError as e:
                out.append(f"parameter {self.key!r}: bad default ({e})")
        return out

    def _fail(self, why):
        raise K.KernelError(f"{self.label}: {why}")

    def __repr__(self):
        return f"{type(self).__name__}({self.key!r}, {self.label!r}, {self.default!r})"


class _Number(Param):
    angle = False

    def __init__(self, key, label, default, min=None, max=None, *, visible=None):
        super().__init__(key, label, default, visible=visible)
        self.min = min
        self.max = max

    def _number(self, value, variables):
        if isinstance(value, bool):
            self._fail("expected a number")
        if isinstance(value, (int, float)):
            v = float(value)
        elif isinstance(value, str):
            v = parse_number(value, angle=self.angle, variables=variables)
            if v is None:
                bad = [n for n in var_names(value) if n not in variables]
                self._fail(f"unknown variable #{bad[0]}" if bad else f"can't read {value[:40]!r}")
        else:
            self._fail("expected a number")
        if not math.isfinite(v):
            self._fail("must be a finite number")
        if self.min is not None and v < self.min:
            self._fail(f"must be at least {self.min:g}")
        if self.max is not None and v > self.max:
            self._fail(f"must be at most {self.max:g}")
        return v

    def check(self, value, variables):
        return self._number(value, variables)

    def problems(self):
        out = []
        for b in (self.min, self.max):
            if b is not None and (isinstance(b, bool) or not isinstance(b, (int, float))):
                out.append(f"parameter {self.key!r}: min and max must be numbers")
        return out or super().problems()


class Length(_Number):
    """A length in mm (App::PropertyLength). Accepts a number or an expression such as "#w * 2"."""
    kind = "length"


class Angle(_Number):
    """An angle in degrees (App::PropertyAngle)."""
    kind = "angle"
    angle = True


class Float(_Number):
    """A plain number (App::PropertyFloat)."""
    kind = "float"


class Integer(_Number):
    """A whole number within [min, max] (App::PropertyIntegerConstraint); both bounds are required, so a
    received Document cannot ask for a billion teeth."""
    kind = "integer"

    def __init__(self, key, label, default, min, max, *, visible=None):
        super().__init__(key, label, default, min, max, visible=visible)

    def check(self, value, variables):
        v = self._number(value, variables)
        n = round(v)
        if abs(v - n) > 1e-9:
            self._fail("must be a whole number")
        return int(n)

    def problems(self):
        if not all(isinstance(b, int) and not isinstance(b, bool) for b in (self.min, self.max)):
            return [f"parameter {self.key!r}: Integer needs whole-number min and max"]
        if self.min > self.max:
            return [f"parameter {self.key!r}: min is above max"]
        return super().problems()


class Bool(Param):
    """True or false (App::PropertyBool)."""
    kind = "bool"

    def __init__(self, key, label, default, *, visible=None):
        super().__init__(key, label, default, visible=visible)

    def check(self, value, variables):
        if not isinstance(value, bool):
            self._fail("expected true or false")
        return value


class Choice(Param):
    """One of a fixed set of values (App::PropertyEnumeration). `options` is [(value, text), ...]."""
    kind = "choice"

    def __init__(self, key, label, default, options, *, visible=None):
        super().__init__(key, label, default, visible=visible)
        self.options = [tuple(o) for o in options] if isinstance(options, (list, tuple)) else options

    def values(self):
        return [o[0] for o in self.options]

    def check(self, value, variables):
        # compare by type as well, so True does not pass for an option 1
        if not any(type(value) is type(v) and value == v for v in self.values()):
            self._fail(f"{str(value)[:40]!r} is not one of the options")
        return value

    def problems(self):
        ok = isinstance(self.options, list) and self.options and all(
            len(o) == 2 and isinstance(o[0], (str, int, float)) and isinstance(o[1], str) for o in self.options)
        if not ok:
            return [f"parameter {self.key!r}: Choice needs options [(value, text), ...]"]
        return super().problems()


@dataclass
class Inputs:
    """What `build` gets besides its parameters. `vars` is a copy of the Part Studio's variables;
    `make` is the naming builder (attached by I4.7; None until then)."""
    feature_id: str
    vars: dict = field(default_factory=dict)
    make: object = None


class Feature:
    """Base class of a Python feature. Subclasses set the class attributes and implement `build`.

    id           matches [a-z][a-z0-9_]*, unique within its plugin; the type is "py:<plugin id>/<id>"
    label        feature-list label and default name stem, e.g. "Spur gear"
    description  one sentence, for the command search
    icon         optional SVG file name relative to the plugin folder
    params       list of declarations (Length, Angle, Integer, Float, Bool, Choice)
    """
    id: str = ""
    label: str = ""
    description: str = ""
    icon: str | None = None
    params: list = []

    def build(self, params: dict, inputs: Inputs):
        """Return a TopoDS_Shape, a freeshape.naming.NamedShape, or a list of either. Every solid in the
        result becomes a new part. Raise K.KernelError with a readable message for a bad combination."""
        raise NotImplementedError


def declaration_problems(cls) -> list[str]:
    """Why a Feature subclass cannot be registered (empty when it can)."""
    name = getattr(cls, "__name__", repr(cls))
    if not (isinstance(cls, type) and issubclass(cls, Feature)) or cls is Feature:
        return [f"{name} is not a subclass of freeshape.pyfeature.api.Feature"]
    out = []
    if not isinstance(cls.id, str) or not ID_RE.match(cls.id):
        out.append(f"{name}.id {cls.id!r} must match {ID_RE.pattern}")
    if not isinstance(cls.label, str) or not cls.label.strip():
        out.append(f"{name} needs a label")
    if not isinstance(cls.description, str):
        out.append(f"{name}.description must be a string")
    if cls.icon is not None and (not isinstance(cls.icon, str) or "/" in cls.icon or "\\" in cls.icon
                                 or cls.icon.startswith(".")):
        out.append(f"{name}.icon must be a file name in the plugin folder")
    if not callable(getattr(cls, "build", None)) or cls.build is Feature.build:
        out.append(f"{name} must implement build(params, inputs)")
    if not isinstance(cls.params, (list, tuple)):
        return out + [f"{name}.params must be a list of declarations"]
    keys = set()
    for p in cls.params:
        if not isinstance(p, Param):
            out.append(f"{name}.params holds {p!r}, which is not a declaration")
            continue
        out += p.problems()
        if p.key in keys:
            out.append(f"{name}: parameter key {p.key!r} is declared twice")
        keys.add(p.key)
    return out


CATEGORIES = ("Screws", "Nuts", "Washers", "Pins", "Bearings", "Inserts")


class StandardPart:
    """Base class of a Standard part (a screw, nut, washer...): a generated solid an Assembly holds as an
    instance with named mate connectors. A Plugin lists these under "standard_parts"; the type is
    "sp:<plugin id>/<id>".

    id               matches [a-z][a-z0-9_]*, unique within its plugin
    label            display name, e.g. "Hex head screw"
    category         one of CATEGORIES
    insert_connector name of the connector used when inserting the part onto a hole

    A frame is {"o": origin, "z": axis, "x": reference direction}, as in `assembly.frame_to_json`. Every
    connector's Z axis points out of its face.
    """
    id: str = ""
    label: str = ""
    category: str = ""
    insert_connector: str = ""

    def sizes(self) -> list:
        """Ordered size names, e.g. ["M3", "M4"]."""
        raise NotImplementedError

    def lengths(self, size) -> list:
        """Ordered nominal lengths in mm for `size`; empty when the type has no length."""
        raise NotImplementedError

    def default_params(self) -> dict:
        raise NotImplementedError

    def validate(self, params) -> dict:
        """The cleaned params; raises (K.KernelError or ValueError) with a message naming the bad key."""
        raise NotImplementedError

    def build(self, params):
        """Return one OCP solid."""
        raise NotImplementedError

    def connectors(self, params) -> dict:
        """{connector name: frame}."""
        raise NotImplementedError

    def designation(self, params) -> str:
        """The BOM text, e.g. "ISO 4017 M5 x 20"."""
        raise NotImplementedError

    def match_hole(self, diameter):
        """The size name that fits a hole of `diameter` mm, or None."""
        raise NotImplementedError


_STANDARD_METHODS = ("sizes", "lengths", "default_params", "validate", "build", "connectors", "designation",
                     "match_hole")


def standard_declaration_problems(cls) -> list[str]:
    """Why a StandardPart subclass cannot be registered (empty when it can)."""
    name = getattr(cls, "__name__", repr(cls))
    if not (isinstance(cls, type) and issubclass(cls, StandardPart)) or cls is StandardPart:
        return [f"{name} is not a subclass of freeshape.pyfeature.api.StandardPart"]
    out = []
    if not isinstance(cls.id, str) or not ID_RE.match(cls.id):
        out.append(f"{name}.id {cls.id!r} must match {ID_RE.pattern}")
    if not isinstance(cls.label, str) or not cls.label.strip():
        out.append(f"{name} needs a label")
    if cls.category not in CATEGORIES:
        out.append(f"{name}.category {cls.category!r} must be one of {', '.join(CATEGORIES)}")
    if not isinstance(cls.insert_connector, str) or not cls.insert_connector.strip():
        out.append(f"{name} needs an insert_connector")
    for m in _STANDARD_METHODS:
        if not callable(getattr(cls, m, None)) or getattr(cls, m) is getattr(StandardPart, m):
            out.append(f"{name} must implement {m}()")
    return out
