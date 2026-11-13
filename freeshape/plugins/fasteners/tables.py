"""Dimension tables for the Fasteners plugin: a loader for the FreeCAD Fasteners workbench CSV format.

Ported from `csv2dict` in the workbench's FSutils.py and the `FsData` / `FsTitles` loop in FastenerBase.py
(commit 375a73d9706a30072c132fb061e7636e8ed99f82). Format rules, as upstream reads them:

- One file holds several tables. A line with a single field is a table name; consecutive name lines give one
  table several names. The next line is the header row, then data rows. A blank line ends the table.
- A file with no name line takes the file stem as its table name (DiaList, iso1478def, iso4757def).
- The first column is the row key. A row is the remaining cells as a tuple, so `P, b, dk = table[key]` works as
  in the workbench generators. The header row is kept as `titles`, also without the first column.
- QUOTE_NONNUMERIC typing: an unquoted cell is a float, a quoted cell is a str, an empty unquoted cell is ''.
- Difference from upstream: a key in brackets, "(M3.5)", is a non-preferred size. Upstream keeps the brackets
  in the key and callers strip them; here the key is the bare name and `Table.nonpreferred` lists it.

A second source, `data/commercial/`, holds FreeShape's own tables under the standard name `commercial`.
"""
from __future__ import annotations

import csv
import functools
import os
import re
from dataclasses import dataclass, field

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
COMMERCIAL_DIR = os.path.join(DATA_DIR, "commercial")
WORKBENCH = "workbench"
COMMERCIAL = "commercial"
_BRACKETED = re.compile(r"\(([^()]*)\)\Z")


@dataclass
class Table:
    name: str                                   # the first of its names
    names: tuple                                # every name the table is reachable under
    titles: tuple = ()                          # header cells after the first column
    key_title: str = ""                         # header cell of the first column
    rows: dict = field(default_factory=dict)    # key -> tuple of cells, in file order
    nonpreferred: set = field(default_factory=set)   # keys written in brackets
    standard: str = WORKBENCH                   # "workbench" or "commercial"
    source: str = ""                            # the file it came from

    def column(self, key, title):
        """The cell of row `key` under header `title`."""
        return self.rows[key][self.titles.index(title)]

    def row_dict(self, key) -> dict:
        return dict(zip(self.titles, self.rows[key]))


class Tables:
    """Every loaded table, reachable by any of its names."""

    def __init__(self):
        self._by_name: dict[str, Table] = {}

    def __contains__(self, name):
        return name in self._by_name

    def __getitem__(self, name) -> Table:
        return self._by_name[name]

    def get(self, name, default=None):
        return self._by_name.get(name, default)

    def names(self) -> list[str]:
        return list(self._by_name)

    def standard(self, standard) -> dict[str, Table]:
        """{name: Table} for the tables of one standard ("workbench" or "commercial")."""
        return {n: t for n, t in self._by_name.items() if t.standard == standard}

    def fs_data(self) -> dict:
        """Upstream-shaped FsData: {table name: {key: row tuple}}."""
        return {n: t.rows for n, t in self._by_name.items()}

    def fs_titles(self) -> dict:
        """Upstream-shaped FsTitles: {table name: header tuple (without the first column)}."""
        return {n: t.titles for n, t in self._by_name.items()}

    def _add(self, table: Table):
        for n in table.names:
            if n in self._by_name:
                raise ValueError(f"table {n!r} is defined twice ({self._by_name[n].source} and {table.source})")
            self._by_name[n] = table


def _bare_key(key):
    if isinstance(key, str):
        m = _BRACKETED.match(key)
        if m:
            return m.group(1), True
    return key, False


def parse_csv(path, standard=WORKBENCH) -> list[Table]:
    """The tables in one CSV file, in file order (a table with several names appears once)."""
    stem = os.path.splitext(os.path.basename(path))[0]
    out: list[Table] = []
    cur = None                 # the table rows are being added to
    names: list[str] = []      # names collected for the next table
    need_header = False
    open_names = False         # still collecting name lines
    with open(path, encoding="utf-8", newline="") as fp:
        reader = csv.reader(fp, skipinitialspace=True, dialect="unix", quoting=csv.QUOTE_NONNUMERIC)
        for cells in reader:
            if not cells:
                cur = None
                continue
            if len(cells) == 1:
                if not open_names:
                    names = []
                    open_names = True
                names.append(str(cells[0]))
                cur = None
                continue
            if cur is None:
                if open_names:
                    open_names = False
                elif not out:
                    names = [stem]          # no name line before the first table: the file stem
                else:
                    raise ValueError(f"{path}: data row {cells[0]!r} after a blank line has no table name")
                cur = Table(name=names[0], names=tuple(names), standard=standard, source=os.path.basename(path),
                            key_title=str(cells[0]), titles=tuple(cells[1:]))
                out.append(cur)
                continue
            key, nonpref = _bare_key(cells[0])
            cur.rows[key] = tuple(cells[1:])
            if nonpref:
                cur.nonpreferred.add(key)
    return out


def _load_dir(tables: Tables, folder, standard):
    if not os.path.isdir(folder):
        return
    for fn in sorted(os.listdir(folder)):
        if fn.lower().endswith(".csv"):
            for t in parse_csv(os.path.join(folder, fn), standard):
                tables._add(t)


def load_tables(data_dir=None, commercial_dir=None) -> Tables:
    """Read the workbench tables from `data_dir` and the commercial tables from `commercial_dir`
    (defaults: the plugin's own `data/` and `data/commercial/`). A commercial table may not reuse the name
    of a workbench table."""
    data_dir = DATA_DIR if data_dir is None else data_dir
    commercial_dir = COMMERCIAL_DIR if commercial_dir is None else commercial_dir
    tables = Tables()
    _load_dir(tables, data_dir, WORKBENCH)
    _load_dir(tables, commercial_dir, COMMERCIAL)
    return tables


@functools.lru_cache(maxsize=1)
def tables() -> Tables:
    """The shipped tables, loaded once."""
    return load_tables()


def split_lengths(cell) -> list[str]:
    """The nominal lengths in a commercial `lengths` cell ("6.5;9.5;13") as strings."""
    return [s.strip() for s in str(cell).split(";") if s.strip()]
