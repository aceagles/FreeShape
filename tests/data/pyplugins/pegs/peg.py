"""Test plugin: a Standard part that is a plain cylinder, axis +Z from z=0 to z=length."""
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

from freeshape import kernel as K
from freeshape.pyfeature.api import StandardPart

DIAMETERS = {"P3": 3.0, "P5": 5.0}
LENGTHS = {"P3": [10, 20], "P5": [10, 20]}


class Peg(StandardPart):
    id = "peg"
    label = "Peg"
    category = "Screws"
    insert_connector = "bottom"

    def sizes(self):
        return list(DIAMETERS)

    def lengths(self, size):
        return list(LENGTHS[size])

    def default_params(self):
        return {"size": "P3", "length": 10}

    def validate(self, params):
        size = params.get("size")
        if size not in DIAMETERS:
            raise K.KernelError(f"size: {str(size)[:40]!r} is not one of {', '.join(DIAMETERS)}")
        length = params.get("length")
        if isinstance(length, bool) or not isinstance(length, (int, float)) or length not in LENGTHS[size]:
            raise K.KernelError(f"length: {str(length)[:40]!r} is not one of {LENGTHS[size]}")
        return {"size": size, "length": length}

    def build(self, params):
        p = self.validate(params)
        ax = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1), gp_Dir(1, 0, 0))
        return BRepPrimAPI_MakeCylinder(ax, DIAMETERS[p["size"]] / 2, float(p["length"])).Shape()

    def connectors(self, params):
        n = float(self.validate(params)["length"])
        return {"bottom": {"o": [0.0, 0.0, 0.0], "z": [0.0, 0.0, -1.0], "x": [1.0, 0.0, 0.0]},
                "top": {"o": [0.0, 0.0, n], "z": [0.0, 0.0, 1.0], "x": [1.0, 0.0, 0.0]}}

    def designation(self, params):
        p = self.validate(params)
        return f"Peg {p['size']} x {p['length']}"

    def match_hole(self, diameter):
        for size, d in DIAMETERS.items():
            if abs(d - diameter) < 1e-6:
                return size
        return None
