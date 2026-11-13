# Hardware plugin: third-party material

`data/HeatInserts.csv` and `data/iso262def.csv` are unmodified copies of files from the FreeCAD Fasteners workbench:

- Repository: https://github.com/shaise/FreeCAD_FastenersWB
- Commit: `375a73d9706a30072c132fb061e7636e8ed99f82`
- Licence: LGPL-2.1-or-later
- Source path: `FsData/`
- Copyright: the workbench authors named in the workbench sources (among them Shai Seger).

`heat_insert.py` ports `iutMakeFace` and `makeHeatInsert` from `FsFunctions/FSmakeHeatInsert.py` there.

`data/bearings.csv` is not from the workbench. It holds the deep groove ball bearing sizes of FreeShape 2.0
(`freeshape/stdparts.py` at 355fc85), unchanged and not checked against a standard.
