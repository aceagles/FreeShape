"""STL export options, with no Qt: the resolution presets of the desktop's STL dialog (`ui/export_dialog.py`) and
the checking of the options a request names (`freeshape serve`, GET .../export)."""
from __future__ import annotations

# name -> (linear deflection in mm, angular deflection in rad)
PRESETS = {"Coarse": (0.2, 0.5), "Medium": (0.1, 0.4), "Fine": (0.05, 0.3)}
CUSTOM = "Custom"
DEFAULT_PRESET = "Fine"

DEFL_RANGE = (0.001, 10.0)        # mm, for a custom deflection
ANGLE_RANGE = (0.01, 1.5)         # rad, for a custom angle


def _number(text, what, low, high):
    try:
        v = float(str(text).strip())
    except ValueError:
        raise ValueError(f"{what} must be a number") from None
    if not low <= v <= high:                # a NaN fails here too
        raise ValueError(f"{what} must be between {low:g} and {high:g}")
    return v


def _flag(text, what):
    t = str(text).strip().lower()
    if t in ("1", "true"):
        return True
    if t in ("0", "false"):
        return False
    raise ValueError(f"{what} must be 1 or 0")


def stl_options(preset=None, defl=None, angle=None, ascii=None) -> dict:
    """{"defl", "angle", "ascii"} for the strings a request gives (None for one it leaves out). `preset` is a name
    of PRESETS or "custom" (any case); with no preset, a deflection or angle means Custom and one nothing names is
    Fine's. A named preset with `defl` or `angle` as well is refused. ValueError, naming the field, for a name that
    is not a preset, a value that is not a number or lies outside DEFL_RANGE / ANGLE_RANGE, or a flag that is not
    1 or 0."""
    name = None
    if preset is not None:
        name = next((n for n in PRESETS if n.lower() == str(preset).strip().lower()), None)
        if name is None and str(preset).strip().lower() != CUSTOM.lower():
            raise ValueError(f"preset must be one of {', '.join(PRESETS)}, {CUSTOM}")
    if name is not None:
        if defl is not None or angle is not None:
            raise ValueError(f"defl and angle go with preset={CUSTOM}, not with {name}")
        d, a = PRESETS[name]
    else:
        d, a = PRESETS[DEFAULT_PRESET]
        if defl is not None:
            d = _number(defl, "defl", *DEFL_RANGE)
        if angle is not None:
            a = _number(angle, "angle", *ANGLE_RANGE)
    return {"defl": d, "angle": a, "ascii": False if ascii is None else _flag(ascii, "ascii")}


MAX_EXPORT_NAME = 100              # characters of a file name's stem
MAX_NAME_BYTES = 255               # bytes of UTF-8 in a whole file name (the limit of most file systems)
_NAME_SWAP = '<>:"|?*'             # characters a file name may not hold on Windows


def display_name(name) -> str:
    """`name` as shown, never as a path: what follows the last `/` or `\\`, characters that are not printable
    dropped, stripped. The one rule behind `step_import.clean_file_name` and `export_file_name`."""
    text = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    return "".join(c for c in text if c.isprintable()).strip()


def export_file_name(name, fmt) -> str:
    """The name an export of `name` is saved under (a download, or a file in an export root: the one rule, never a
    path): `display_name`, `<>:"|?*` swapped for `_`, leading dots dropped, an ending `.stl`, `.step` or `.stp` (any
    case) taken off, at most MAX_EXPORT_NAME characters and, with the extension, MAX_NAME_BYTES bytes of UTF-8, then
    `.stl` or `.step`. "export" when nothing is left."""
    text = "".join("_" if c in _NAME_SWAP else c for c in display_name(name))
    low = text.lower()
    for ext in (".stl", ".step", ".stp"):
        if low.endswith(ext):
            text = text[:-len(ext)]
            break
    ext = ".stl" if fmt == "stl" else ".step"
    stem = text.strip().lstrip(".").strip()[:MAX_EXPORT_NAME]
    while stem and len((stem + ext).encode("utf-8")) > MAX_NAME_BYTES:
        stem = stem[:-1]
    return (stem or "export") + ext
