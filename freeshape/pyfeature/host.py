"""The runner registered in features.RUNNERS for every "py:" feature type.

`run(state, f)` looks the type up in the registry (never importing anything named by `f`), validates the
stored parameters against the class's declarations -- a received Document is hostile data -- calls `build`,
and adds each solid of the result as a new part. In this slice a Python feature only creates parts.
"""
from __future__ import annotations

from .. import kernel as K
from . import registry
from .api import Inputs


def validate(cls, params, variables) -> dict:
    """The parameters `build` receives: one value per declaration, checked by type and range. Unknown keys
    are ignored and missing keys take their default. Raises K.KernelError naming the parameter's label."""
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise K.KernelError(f"{cls.label}: parameters must be a mapping")
    out = {}
    for p in cls.params:
        out[p.key] = p.check(params.get(p.key, p.default), variables)
    return out


def _shapes(result, label):
    """The TopoDS_Shapes in a build result: a shape, a NamedShape, or a (nested) list or tuple of either."""
    from OCP.TopoDS import TopoDS_Shape
    from ..naming import NamedShape
    if isinstance(result, NamedShape):
        result = result.shape
    if isinstance(result, TopoDS_Shape):
        return [] if result.IsNull() else [result]
    if isinstance(result, (list, tuple)):
        out = []
        for r in result:
            out += _shapes(r, label)
        return out
    raise K.KernelError(f"{label}: build returned {type(result).__name__}, not a shape")


def run(state, f):
    cls = registry.get(f.get("type"))
    if cls is None:
        registry.load_user_plugins()
        cls = registry.get(f.get("type"))
    if cls is None:
        raise K.KernelError(registry.missing_message(f))
    params = validate(cls, f.get("params"), state.vars)
    inputs = Inputs(feature_id=f["id"], vars=dict(state.vars))
    try:
        result = cls().build(params, inputs)
        shapes = _shapes(result, cls.label)
    except K.KernelError:
        raise
    except (Exception, SystemExit) as e:   # a plugin calling sys.exit must not end the app
        raise K.KernelError(f"{cls.label}: {type(e).__name__}: {e}") from e
    solids = [s for sh in shapes for s in K.solids(sh)]
    if not solids:
        raise K.KernelError(f"{cls.label}: the build made no solid")
    for s in solids:
        state.new_part(s, f["id"])
