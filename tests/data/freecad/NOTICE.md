# FreeCAD test shapes

Kernel-stress geometry used by `tests/test_golden_freecad.py`. Every file comes from the FreeCAD repository
(https://github.com/FreeCAD/FreeCAD) at commit `f29e11c1af`. Each keeps its own licence, listed below; the
full texts are in `LICENSES/`. FreeShape's own GPL-3.0-or-later licence does not apply to these files.

The FreeCAD clone used for extraction was shallow (one commit), so per-file authors and original commits are
not recorded here except where the file says so itself. FreeCAD's own copyright holders are the FreeCAD
contributors; the repository licence is LGPL-2.1-or-later (`LICENSE` in the FreeCAD repo).

Files are unmodified copies unless the extraction column says otherwise. `.brep` files are OpenCASCADE BREP text.

## Files

| File here | FreeCAD source path | Licence | Extraction |
|---|---|---|---|
| `brep/unorientableSolid.brep` | `tests/src/Mod/Part/App/brepfiles/unorientableSolid.brep` | LGPL-2.1-or-later | copy |
| `brep/countersink_cone.brep` | `tests/src/Mod/Part/App/brepfiles/countersink_cone.brep` | LGPL-2.1-or-later | copy |
| `brep/helix1.brep` | `tests/src/Mod/Part/App/brepfiles/helix1.brep` | LGPL-2.1-or-later | copy |
| `brep/part_cylinder1.brep` | `tests/src/Mod/Part/App/brepfiles/cylinder1.brep` | LGPL-2.1-or-later | copy, renamed (`ModelRefineTests/cylinder1.brep` is a different file) |
| `brep/roof_subtraction.brep` | `src/Mod/BIM/bimtests/test_roof_subtraction.brep` | LGPL-2.1-or-later | copy, renamed |
| `brep/cylinder7_V2.brep` | `data/tests/ModelRefineTests/cylinder7_V2.brep` | LGPL-2.1-or-later | copy |
| `brep/cylinderSeam3.brep` | `data/tests/ModelRefineTests/cylinderSeam3.brep` | LGPL-2.1-or-later | copy |
| `brep/cylinderSeam10.brep` | `data/tests/ModelRefineTests/cylinderSeam10.brep` | LGPL-2.1-or-later | copy |
| `brep/spline.brep` | `data/tests/ModelRefineTests/spline.brep` | LGPL-2.1-or-later | copy |
| `brep/shelf.brep` | `data/tests/ModelRefineTests/shelf.brep` | LGPL-2.1-or-later | copy |
| `brep/refine_breaks_cylinder.brep` | `data/tests/ModelRefineTests/refine_breaks_cylinder.brep` | LGPL-2.1-or-later | copy |
| `brep/holesInFace2.brep` | `data/tests/ModelRefineTests/holesInFace2.brep` | LGPL-2.1-or-later | copy |
| `brep/blocks144.brep.gz` | `data/tests/ModelRefineTests/blocks144.brep` | LGPL-2.1-or-later | `gzip -9n` |
| `step/Schenkel.stp` | `data/examples/Schenkel.stp` | CC-BY-SA-4.0 | copy |
| `brep/groove002.brep` | `data/examples/PartDesignExample.FCStd` | Public Domain | unzip member `Groove002.Shape.brp`, rename |
| `brep/assembly_shape015.brep` | `data/examples/AssemblyExample.FCStd` | CC-BY-4.0 | unzip member `Shape015.Shape.brp`, rename |
| `brep/hole_puzzle_partshape4.brep.gz` | `src/Mod/CAM/DemoParts/hole_puzzle.fcstd` | CC-BY-3.0 | unzip member `PartShape4.brp`, rename, `gzip -9n` |

The `ModelRefineTests` files are a 2014 manual refine corpus (see `log.txt` beside them in FreeCAD).

## Attribution

- `step/Schenkel.stp`: Copyright (C) 2011 Juergen Riegel, licensed under CC-BY-SA 4.0
  (https://creativecommons.org/licenses/by-sa/4.0/). The file header states this. Shared as is, without changes.
- `brep/assembly_shape015.brep`: from FreeCAD's `AssemblyExample.FCStd`, whose document metadata gives the licence
  as "CC-BY 4.0" (https://creativecommons.org/licenses/by/4.0/). Shape extracted without changes.
- `brep/hole_puzzle_partshape4.brep.gz`: from FreeCAD's CAM demo part `hole_puzzle.fcstd` (document dated 2013-11-29),
  whose metadata gives the licence as "CC-BY 3.0" (http://creativecommons.org/licenses/by/3.0/). Shape extracted
  without changes, then compressed. No author is named in the file's metadata.
- `brep/groove002.brep`: from FreeCAD's `PartDesignExample.FCStd`, whose document metadata gives the licence as
  "Public Domain". Shape extracted without changes.
- The LGPL-2.1-or-later files carry the licence of the FreeCAD repository. They are data, not linked code.

## Licences

`LICENSES/LGPL-2.1.txt`, `LICENSES/CC-BY-SA-4.0.txt`, `LICENSES/CC-BY-4.0.txt`, `LICENSES/CC-BY-3.0.txt`.
Texts fetched from gnu.org and creativecommons.org.
