"""Test plugin: a box from three Length parameters, and a row of boxes exercising Integer, Choice and Bool."""
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Pnt

from freeshape.pyfeature.api import Bool, Choice, Feature, Integer, Length


class Box(Feature):
    id = "box"
    label = "Box"
    description = "A rectangular block from width, depth and height."
    params = [Length("w", "Width", 10.0, min=0.001), Length("d", "Depth", 20.0, min=0.001),
              Length("h", "Height", 30.0, min=0.001)]

    def build(self, params, inputs):
        return BRepPrimAPI_MakeBox(params["w"], params["d"], params["h"]).Shape()


class BoxRow(Feature):
    id = "row"
    label = "Box row"
    description = "A row of separate cubes along X."
    params = [Integer("count", "Number of boxes", 2, 1, 10),
              Choice("size", "Box size", "small", [("small", "Small (5 mm)"), ("large", "Large (10 mm)")]),
              Bool("gap", "Leave gaps", True),
              Length("pitch", "Pitch", 20.0, visible=lambda p: p.get("gap", True))]

    def build(self, params, inputs):
        s = 5.0 if params["size"] == "small" else 10.0
        step = params["pitch"] if params["gap"] else s * 3
        return [BRepPrimAPI_MakeBox(gp_Pnt(i * step, 0, 0), s, s, s).Shape() for i in range(params["count"])]
