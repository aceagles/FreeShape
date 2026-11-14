"""What the Hardware plugin's Standard parts share: one size parameter, no length, a single solid per size."""
from __future__ import annotations

from freeshape import kernel as K
from freeshape.addons.fasteners import hole_match
from freeshape.pyfeature.api import StandardPart


def frame(o, z, x=(1.0, 0.0, 0.0)):
    return {"o": [float(a) for a in o], "z": [float(a) for a in z], "x": [float(a) for a in x]}


class SizedPart(StandardPart):
    """A part with the parameter `size` only. A subclass sets `id`, `label`, `category`, `insert_connector` and
    implements `sizes`, `build_solid(size)`, `connectors`, `designation` and `hole_diameters(size)`."""

    def lengths(self, size):
        return []

    def default_params(self):
        return {"size": self.sizes()[0]}

    def validate(self, params):
        size = params.get("size")
        if not isinstance(size, str) or size not in self.sizes():
            raise K.KernelError(f"size: {str(size)[:40]!r} is not one of {', '.join(self.sizes())}")
        return {"size": size}

    def build(self, params):
        return self.build_solid(self.validate(params)["size"])

    def build_solid(self, size):
        raise NotImplementedError

    def hole_diameters(self, size) -> list:
        """The hole diameters this size fits."""
        raise NotImplementedError

    def tie_break(self, size) -> float:
        """Of two sizes that fit a hole equally well, the one with the larger value wins."""
        raise NotImplementedError

    def match_hole(self, diameter):
        """The size with a hole diameter within 0.05 mm of `diameter` (see hole_match.match_nearest), or None."""
        return hole_match.match_nearest(self.sizes(), diameter, self.hole_diameters, self.tie_break)
