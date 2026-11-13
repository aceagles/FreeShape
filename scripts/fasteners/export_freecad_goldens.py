"""Export golden volumes and bounding boxes from the FreeCAD Fasteners workbench.

Runs under FreeCAD's own Python (FreeCADCmd), not under FreeShape's. It builds
each fastener with threads off, using the workbench's own `ScrewMaker.Instance`
(the object the GUI command calls), and records volume, bounding box and the
dimension-table row used. Later port tickets check their OCP solids against
the JSON this writes (volume within 0.1 %, bounding box within 0.01 mm).

Regenerate (FreeCAD 1.0.2 AppImage extracted, workbench checked out at the
pinned commit):

    FASTENERS_WB=/path/to/FreeCAD_FastenersWB \\
    FASTENERS_GOLDENS_OUT=tests/data/fasteners/freecad_goldens.json \\
    /path/to/squashfs-root/usr/bin/freecadcmd scripts/fasteners/export_freecad_goldens.py

FASTENERS_WB must be a git checkout; its HEAD hash goes into the JSON header.
The upstream copies in tests/data/fasteners/upstream/ must come from the same
commit.

Headless notes: no stub and no change to workbench code was needed. The
workbench modules import `FreeCADGui`, which FreeCADCmd provides as an empty
stand-in, and nothing in the build path touches the GUI. The script puts the
checkout on `sys.path` and calls `ScrewMaker.Instance.createFastener` with a
plain attribute object holding the fields the GUI object's `execute` sets
(`baseType`, `calc_diam`, `calc_len`, `Thread`, `LeftHanded`). It sets
`dimTable` itself, the way `Screw.createScrew` does.

Sizes: first, middle and last row of the type's table, in `GetAllDiams` order.
Lengths: for types with a length, the shortest and the middle entry of
`GetAllLengths(type, diam)` (without "Custom"), per size.
"""
import json
import math
import os
import subprocess
import sys
import types

# Types to export and whether they take a length.
TYPES = (
    ("ISO4014", True),
    ("ISO4017", True),
    ("ISO4762", True),
    ("ISO4032", False),
    ("ISO7089", False),
    ("ISO7049-C", True),
    ("ISO7049-F", True),
    ("ISO7049-R", True),
)


def _sig(x, digits=9):
    """Round to `digits` decimals; keeps 0.0 instead of -0.0."""
    r = round(float(x), digits)
    return 0.0 if r == 0 else r


def _pick_sizes(diams):
    idx = sorted({0, len(diams) // 2, len(diams) - 1})
    return [diams[i] for i in idx]


def _pick_lengths(lengths):
    idx = sorted({0, len(lengths) // 2})
    return [lengths[i] for i in idx]


def _table_row(fb, type_key, diam):
    row = fb.FsData[type_key + "def"][diam]
    return [x if isinstance(x, str) else _sig(x) for x in row]


def build_entry(sm, fb, type_key, diam, length):
    fa = types.SimpleNamespace(
        Type=type_key,
        baseType=type_key,
        Diameter=diam,
        calc_diam=diam,
        calc_len=length,
        calc_pitch=None,
        Thread=False,
        LeftHanded=False,
        MatchOuter=False,
        dimTable=None,
    )
    sm.updateFastenerParameters()
    shape = sm.createFastener(fa)
    if shape is None:
        raise RuntimeError("no shape for %s %s %s" % (type_key, diam, length))
    bb = shape.BoundBox
    entry = {
        "type": type_key,
        "diameter": diam,
        "length": length,
        "dim_table": type_key + "def",
        "dim_row": _table_row(fb, type_key, diam),
        "volume": _sig(shape.Volume, 6),
        "bbox": [_sig(v, 6) for v in
                 (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)],
        "shape_type": shape.ShapeType,
        "valid": bool(shape.isValid()),
    }
    if not (entry["volume"] > 0 and all(math.isfinite(v) for v in entry["bbox"])):
        raise RuntimeError("bad shape for %r" % (entry,))
    return entry


def main():
    wb = os.environ.get("FASTENERS_WB")
    out = os.environ.get("FASTENERS_GOLDENS_OUT")
    if not wb or not out:
        sys.exit("set FASTENERS_WB and FASTENERS_GOLDENS_OUT")
    wb = os.path.abspath(wb)
    sys.path.insert(0, wb)

    commit = subprocess.check_output(
        ["git", "-C", wb, "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(
        ["git", "-C", wb, "status", "--porcelain"], text=True).strip()
    if dirty:
        sys.exit("workbench checkout is not clean:\n" + dirty)

    import FreeCAD
    import FastenerBase
    import ScrewMaker

    sm = ScrewMaker.Instance
    ver = FreeCAD.Version()
    header = {
        "workbench_repo": "https://github.com/shaise/FreeCAD_FastenersWB",
        "workbench_commit": commit,
        "freecad_version": "%s.%s.%s (build %s)" % (ver[0], ver[1], ver[2], ver[3].split()[0]),
        "threads": False,
        "units": "mm, mm3",
        "dim_table_titles": {},
    }
    entries = {}
    for type_key, has_len in TYPES:
        header["dim_table_titles"][type_key] = list(
            FastenerBase.FsTitles[type_key + "def"])
        diams = sm.GetAllDiams(type_key)
        items = []
        for diam in _pick_sizes(diams):
            if has_len:
                lengths = sm.GetAllLengths(type_key, diam, addCustom=False)
                for length in _pick_lengths(lengths):
                    items.append(build_entry(sm, FastenerBase, type_key, diam, length))
            else:
                items.append(build_entry(sm, FastenerBase, type_key, diam, None))
        entries[type_key] = items
        print("%s: %d entries" % (type_key, len(items)))

    doc = {"header": header, "entries": entries}
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
        f.write("\n")
    print("wrote", out)


main()
