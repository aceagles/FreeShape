# Fasteners plugin: third-party material

The dimension tables in `data/*.csv` are unmodified copies of files from the FreeCAD Fasteners workbench:

- Repository: https://github.com/shaise/FreeCAD_FastenersWB
- Commit: `375a73d9706a30072c132fb061e7636e8ed99f82`
- Licence: LGPL-2.1-or-later
- Source path: `FsData/` (iso1478def, iso4014head, iso4017head, iso4032def, iso4757def, iso4762def, iso68def,
  iso7049head, iso7089def, DiaList)
- Copyright: the workbench authors named in the workbench sources (among them Ulrich Brammer, Shai Seger and
  Andrey Bekhterev).

`tables.py` ports the CSV reader `csv2dict` from the workbench's `FSutils.py`. `geom.py` is adapted from
`screw_maker.py` and `FSmake*.py` in the same repository.

`data/commercial/` holds FreeShape's own tables for commercial (non-ISO) sizes. Nothing there comes from the workbench.
