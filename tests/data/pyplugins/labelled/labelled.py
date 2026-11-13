"""Test plugin for I4.7: features whose output is named through `inputs.make` (the naming builder), one
returning a plain shape (index names), and one returning two separate labelled solids."""
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

from freeshape.pyfeature.api import Feature, Length

FACES = ("front", "back", "left", "right", "top", "bottom")
SIZE = [Length("w", "Width", 10.0, min=0.001), Length("d", "Depth", 20.0, min=0.001),
        Length("h", "Height", 30.0, min=0.001)]


class LabelledBox(Feature):
    id = "box"
    label = "Labelled box"
    description = "A box whose six faces are labelled front, back, left, right, top and bottom."
    params = SIZE

    def build(self, params, inputs):
        return inputs.make.box(params["w"], params["d"], params["h"], labels={k: k for k in FACES})


class PlainBox(Feature):
    id = "plain"
    label = "Plain box"
    description = "A box returned as a plain shape, with no labels."
    params = SIZE

    def build(self, params, inputs):
        return BRepPrimAPI_MakeBox(params["w"], params["d"], params["h"]).Shape()


class BoxPair(Feature):
    id = "pair"
    label = "Box pair"
    description = "Two separate labelled cubes."
    params = [Length("s", "Size", 5.0, min=0.001)]

    def build(self, params, inputs):
        s = params["s"]
        a = inputs.make.box(s, s, s, labels={k: "a_" + k for k in FACES})
        b = inputs.make.box(s, s, s, origin=(3 * s, 0, 0), labels={k: "b_" + k for k in FACES})
        return [a, b]
