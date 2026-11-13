# ***************************************************************************
# *                                                                         *
# * This program is free software: you can redistribute it and/or modify    *
# * it under the terms of the GNU General Public License as published by    *
# * the Free Software Foundation, either version 3 of the License, or       *
# * (at your option) any later version.                                     *
# *                                                                         *
# * This program is distributed in the hope that it will be useful,         *
# * but WITHOUT ANY WARRANTY; without even the implied warranty of          *
# * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the           *
# * GNU General Public License for more details.                            *
# *                                                                         *
# * You should have received a copy of the GNU General Public License       *
# * along with this program.  If not, see <http://www.gnu.org/licenses/>.   *
# *                                                                         *
# ***************************************************************************
#
# Parameters and defaults follow the freecad.gears properties of InvoluteGear and BevelGear
# (freecad/gears/involutegear.py, bevelgear.py @ 4164073).

"""The Python features of the Gears plugin: a spur/helical gear and a straight bevel gear."""
from freeshape import kernel as K
from freeshape.pyfeature.api import Angle, Bool, Feature, Float, Integer, Length

from .bevel_geometry import bevel_gear
from .spur_geometry import spur_gear


def _finish(gear, inputs):
    """Name every face through the builder when there is one, else return the bare solid."""
    make = getattr(inputs, "make", None)
    if make is None:
        return gear.solid
    return make.labelled(gear.solid, face_labels=gear.face_labels)


class SpurGear(Feature):
    id = "spur"
    label = "Spur gear"
    description = "An involute spur, helical or herringbone gear with a bore."
    icon = "spur.svg"
    params = [
        Integer("num_teeth", "Teeth", 15, 3, 1000),
        Length("module", "Module", 1.0, min=0.05),
        Length("height", "Face width", 5.0, min=0.01),
        Angle("pressure_angle", "Pressure angle", 20.0, min=5, max=45),
        Angle("helix_angle", "Helix angle", 0.0, min=-60, max=60),
        Bool("double_helix", "Double helix (herringbone)", False,
             visible=lambda p: p.get("helix_angle", 0) not in (0, 0.0, "0")),
        Length("bore", "Bore diameter (0 = none)", 0.0, min=0),
        Float("shift", "Profile shift", 0.0),
        Float("clearance", "Clearance (× module)", 0.25, min=0),
        Float("head", "Head (× module)", 0.0),
        Length("backlash", "Backlash", 0.0, min=0),
        Bool("undercut", "Undercut", False),
    ]

    def build(self, params, inputs):
        try:
            gear = spur_gear(params["num_teeth"], params["module"], params["height"],
                             pressure_angle=params["pressure_angle"], helix_angle=params["helix_angle"],
                             double_helix=params["double_helix"], shift=params["shift"],
                             clearance=params["clearance"], head=params["head"], backlash=params["backlash"],
                             undercut=params["undercut"], bore=params["bore"])
        except ValueError as e:
            raise K.KernelError(str(e)) from e
        return _finish(gear, inputs)


class BevelGear(Feature):
    id = "bevel"
    label = "Bevel gear"
    description = "A straight bevel gear: heel on the XY plane, axis along +Z."
    icon = "bevel.svg"
    params = [
        Integer("num_teeth", "Teeth", 15, 3, 1000),
        Length("module", "Module", 1.0, min=0.05),
        Length("height", "Face width", 5.0, min=0.01),
        Angle("pitch_angle", "Pitch angle", 45.0, min=5, max=85),
        Angle("pressure_angle", "Pressure angle", 20.0, min=5, max=45),
        Float("clearance", "Clearance (× module)", 0.1, min=0),
        Length("backlash", "Backlash", 0.0, min=0),
        Length("bore", "Bore diameter (0 = none)", 0.0, min=0),
    ]

    def build(self, params, inputs):
        try:
            gear = bevel_gear(params["num_teeth"], params["module"], params["height"],
                              pitch_angle=params["pitch_angle"], pressure_angle=params["pressure_angle"],
                              clearance=params["clearance"], backlash=params["backlash"], bore=params["bore"])
        except ValueError as e:
            raise K.KernelError(str(e)) from e
        return _finish(gear, inputs)
