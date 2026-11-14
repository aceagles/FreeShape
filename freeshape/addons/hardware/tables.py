"""Dimension tables of the Hardware plugin, read from `data/*.csv` with the Fasteners plugin's CSV reader.

Sources:
- `HeatInserts.csv` (table IUTHeatInsertdef) and `iso262def.csv` (ISO262def, coarse pitch) are unmodified copies of
  FsData/ in the FreeCAD Fasteners workbench, commit 375a73d9706a30072c132fb061e7636e8ed99f82 (LGPL-2.1-or-later).
- `bearings.csv` (table UpstreamBearingdef) holds the deep groove ball bearing sizes FreeShape 2.0 offered
  (freeshape/stdparts.py at 355fc85), copied unchanged. The workbench has no bearing table and no standard was
  consulted, so these values are upstream's and are not checked against one.
"""
from __future__ import annotations

import functools
import os

from freeshape.addons.fasteners.tables import parse_csv

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
UPSTREAM = "upstream"          # Table.standard of a table that is FreeShape 2.0's own, not the workbench's


@functools.lru_cache(maxsize=1)
def tables() -> dict:
    """{table name: Table} for every CSV in `data/`."""
    out = {}
    for fn in sorted(os.listdir(DATA_DIR)):
        if fn.lower().endswith(".csv"):
            for t in parse_csv(os.path.join(DATA_DIR, fn), UPSTREAM if fn == "bearings.csv" else "workbench"):
                out[t.name] = t
    return out
