# -*- coding: utf-8 -*-
###############################################################################
#  Shared size, length and connector rules for the fastener Standard parts.
#  Ported from ScrewMaker.py (GetAllDiams, GetAllLengths, GetThreadLength,
#  GetTableProperty), FastenerBase.py (LenStr2Num) and screw_maker.py
#  (createScrew's dimTable lookup) at workbench commit
#  375a73d9706a30072c132fb061e7636e8ed99f82.
#
#  Copyright 2015 Shai Seger <shaise at gmail dot com>
#  Copyright (c) 2013, 2014, 2015 Ulrich Brammer <ulrich1a[at]users.sourceforge.net>
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 2 of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software
#  Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
#  MA 02110-1301, USA.
###############################################################################
"""What every fastener generator module imports.

Rules (module functions, table-driven, `type_key` is the workbench type such as "ISO4017"):

- `all_diams(type_key)`            GetAllDiams: the keys of `<type>def`, in file order.
- `all_lengths(type_key, size)`    GetAllLengths (without "Custom"): nominal lengths as strings, sorted. With a
                                   `<type>range` table, the `<type>length` nominals (or the range table's "all"
                                   row) between the size's Min_L and Max_L; without one, `<type>length[size]`.
- `nominal_lengths(type_key, size)` the same as numbers (int when whole), as `StandardPart.lengths` returns them.
- `thread_length(type_key, size)`  GetThreadLength: the `thr_len` column, else 10.0.
- `dim_row(type_key, size)`        the `<type>def` row, as createScrew sets `fa.dimTable`.
- `dia(size)`                      the numeric nominal diameter, via getDia with the DiaList table.

Classes:

- `ScrewPart`  base of a bolt or screw with connectors `under_head` and `tip`. A subclass sets `type_key`,
               `standard` ("ISO 4017"), `id`, `label` and implements `build_solid(size, length)`. The head's
               bearing face is at z = 0 and the shank runs toward -Z, as in the workbench.
- `FlatPart`   base of a nut or washer with connectors `bottom` and `top`. A subclass implements
               `build_solid(size)` and `thickness(size)`; the part spans z = 0 .. thickness.

Nothing here loads a table or builds a shape at import time.
"""
from __future__ import annotations

import functools

from freeshape import kernel as K
from freeshape.pyfeature.api import StandardPart

from . import geom
from .tables import tables


# ---------------------------------------------------------------- upstream rules

def len_str_2_num(len_str) -> float:
    """FastenerBase.LenStr2Num: a length cell ("12", "1 1/4in", "(8)") as mm."""
    if isinstance(len_str, (int, float)):
        return float(len_str)
    if "in" in len_str:
        total = 0.0
        for item in len_str.strip("in").split(" "):
            if "/" in item:
                a, b = item.split("/")
                total += float(a) / float(b)
            else:
                total += float(item)
        return total * 25.4
    return float(len_str.strip(" m()"))


def all_diams(type_key) -> list:
    """ScrewMaker.GetAllDiams: the size names of `<type>def`."""
    res = list(tables().fs_data()[type_key + "def"].keys())
    if "Unit" in res:
        res.remove("Unit")
    return res


def all_lengths(type_key, size) -> list:
    """ScrewMaker.GetAllLengths(type, diam, addCustom=False): nominal length strings, sorted by value."""
    data = tables().fs_data()
    range_name = type_key + "range"
    if range_name in data:
        range_table = data[range_name]
        lens = range_table["all"] if "all" in range_table else data[type_key + "length"]
        lo_s, hi_s = range_table[size][:2]
        lo, hi = len_str_2_num(lo_s), len_str_2_num(hi_s)
        out = [n for n in lens if lo <= len_str_2_num(n) <= hi]
    else:
        out = list(data[type_key + "length"][size])
    out.sort(key=len_str_2_num)
    return out


def nominal_lengths(type_key, size) -> list:
    """`all_lengths` as numbers: an int when the length is whole, else a float."""
    out = []
    for s in all_lengths(type_key, size):
        v = len_str_2_num(s)
        out.append(int(v) if v == int(v) else v)
    return out


def _table_property(type_key, size, name, default):
    """ScrewMaker.GetTableProperty: the `name` column of `<type>def` for `size`, or `default` if absent."""
    titles = tables().fs_titles()[type_key + "def"]
    if name not in titles:
        return default
    return tables().fs_data()[type_key + "def"][size][titles.index(name)]


def thread_length(type_key, size) -> float:
    """ScrewMaker.GetThreadLength: the `thr_len` column, 10.0 when the table has none."""
    return _table_property(type_key, size, "thr_len", 10.0)


def dim_row(type_key, size) -> tuple:
    """Screw.createScrew: `fa.dimTable = FsData[baseType + "def"][Diameter]`."""
    return tables().fs_data()[type_key + "def"][size]


def dia(size, is_nut=False) -> float:
    """Screw.getDia for a size name such as "M6"."""
    return geom.getDia(size, is_nut, tables().fs_data()["DiaList"])


# ---------------------------------------------------------------- shared checks

def _frame(o, z, x=(1.0, 0.0, 0.0)):
    return {"o": [float(a) for a in o], "z": [float(a) for a in z], "x": [float(a) for a in x]}


def _check_size(sizes, size):
    if not isinstance(size, str) or size not in sizes:
        raise K.KernelError(f"size: {str(size)[:40]!r} is not one of {', '.join(sizes)}")
    return size


def _check_length(lengths, length):
    if isinstance(length, bool) or not isinstance(length, (int, float)) or length not in lengths:
        raise K.KernelError(f"length: {str(length)[:40]!r} is not one of {lengths}")
    return length


class ThreadOption:
    """Opt-in mixin (listed before `ScrewPart` or `FlatPart`): adds the boolean parameter `model_thread`
    (default False). When true and the size is in `K.ISO_PITCH`, `build` cuts a real thread with the kernel's
    own thread code by calling `add_thread`; other sizes ignore the switch and stay plain. The switch changes
    neither the connectors nor the designation. Subclasses implement `add_thread(solid, size, *rest)`, where
    `rest` is `(length,)` for a screw and empty for a nut."""

    def default_params(self):
        return {**super().default_params(), "model_thread": False}

    def validate(self, params):
        flag = params.get("model_thread", False)
        if not isinstance(flag, bool):
            raise K.KernelError(f"model_thread: {str(flag)[:40]!r} is not true or false")
        return {**super().validate(params), "model_thread": flag}

    def build(self, params):
        p = self.validate(params)
        rest = (float(p["length"]),) if "length" in p else ()
        solid = self.build_solid(p["size"], *rest)
        if p["model_thread"] and p["size"] in K.ISO_PITCH:
            solid = self.add_thread(solid, p["size"], *rest)
        return solid

    def add_thread(self, solid, size, *rest):
        raise NotImplementedError


def screw_thread(solid, size, length, thread_length):
    """Thread the shank of a screw: the part of the shank `thread_length` long at the tip, along -Z from the
    head's bearing face at z = 0. The crests sit on the shank surface, so the box does not grow."""
    return K.external_thread(solid, (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), dia(size, False) / 2.0, K.ISO_PITCH[size],
                             -length, -length + thread_length)


class ScrewPart(StandardPart):
    """A bolt or screw: `under_head` and `tip` connectors, parameters `size` and `length`."""
    category = "Screws"
    insert_connector = "under_head"
    type_key = ""       # workbench type, e.g. "ISO4017"
    standard = ""       # designation prefix, e.g. "ISO 4017"

    def sizes(self):
        return all_diams(self.type_key)

    def lengths(self, size):
        return nominal_lengths(self.type_key, _check_size(self.sizes(), size))

    def default_params(self):
        size = self.sizes()[0]
        return {"size": size, "length": self.lengths(size)[0]}

    def validate(self, params):
        size = _check_size(self.sizes(), params.get("size"))
        length = _check_length(self.lengths(size), params.get("length"))
        return {"size": size, "length": length}

    def build(self, params):
        p = self.validate(params)
        return self.build_solid(p["size"], float(p["length"]))

    def build_solid(self, size, length):
        raise NotImplementedError

    def connectors(self, params):
        """under_head: on the axis at the bearing face, Z toward the tip. tip: at the end of the shank, Z away
        from the head. Both Z axes point out of their face, which here is the same direction (-Z)."""
        n = float(self.validate(params)["length"])
        return {"under_head": _frame((0, 0, 0), (0, 0, -1)),
                "tip": _frame((0, 0, -n), (0, 0, -1))}

    def designation(self, params):
        p = self.validate(params)
        return f"{self.standard} {p['size']} x {p['length']}"

    def match_hole(self, diameter):
        return None     # I3.2


class FlatPart(StandardPart):
    """A nut or washer: `bottom` (z = 0) and `top` (z = thickness) connectors, parameter `size` only.
    Subclasses declare their own `category` ("Nuts" or "Washers"); this base is never registered."""
    insert_connector = "bottom"
    type_key = ""
    standard = ""

    def sizes(self):
        return all_diams(self.type_key)

    def lengths(self, size):
        return []

    def default_params(self):
        return {"size": self.sizes()[0]}

    def validate(self, params):
        return {"size": _check_size(self.sizes(), params.get("size"))}

    def thickness(self, size) -> float:
        raise NotImplementedError

    def build(self, params):
        return self.build_solid(self.validate(params)["size"])

    def build_solid(self, size):
        raise NotImplementedError

    def connectors(self, params):
        t = self.thickness(self.validate(params)["size"])
        return {"bottom": _frame((0, 0, 0), (0, 0, -1)),
                "top": _frame((0, 0, t), (0, 0, 1))}

    def designation(self, params):
        return f"{self.standard} {self.validate(params)['size']}"

    def match_hole(self, diameter):
        return None     # I3.2
