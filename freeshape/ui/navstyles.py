"""Mouse styles for the 3D view: which buttons and keys orbit, pan and zoom. Every style is one entry in STYLES.

A rule is (buttons held, modifier key, action):
  buttons   "L", "M", "R" or a chord such as "LM" (both held); "" is a move with no button held (touchpads)
  modifier  "", "ctrl", "shift" or "alt" (the only modifier held), or two joined as "ctrl+shift"
  action    "orbit", "pan" or "zoom" (drag up / down to zoom)
The first matching rule wins. A left click that doesn't move still selects in every style; with no rule for a plain
left drag it draws a selection box. "wheel" is what the wheel (or two-finger scroll) does without Ctrl:
"zoom", or "pan" (then Ctrl + scroll and pinching zoom; no built-in style uses it now). Inside a sketch the
left button belongs to the sketch; a style that orbits with a plain left drag orbits with Alt + left there instead.
"""
from __future__ import annotations

from PySide6.QtCore import Qt

STYLES = {
    "onshape": {"label": "Onshape", "wheel": "zoom", "rules": [
        ("R", "", "orbit"), ("M", "", "pan"), ("R", "ctrl", "pan"), ("M", "ctrl", "pan")]},
    # two-finger scroll and pinch both zoom; pan is Shift+move, zoom is also Ctrl+move
    "touchpad": {"label": "Touchpad", "wheel": "zoom", "rules": [
        ("L", "alt", "orbit"), ("L", "shift", "pan"), ("R", "", "orbit"), ("M", "", "pan"), ("R", "ctrl", "pan"),
        ("", "alt", "orbit"), ("", "shift", "pan"), ("", "ctrl", "zoom")]},
    "cad": {"label": "CAD (FreeCAD)", "wheel": "zoom", "rules": [
        ("LM", "", "orbit"), ("MR", "", "orbit"), ("M", "", "pan"), ("M", "ctrl", "orbit")]},
    "gesture": {"label": "Gesture (FreeCAD)", "wheel": "zoom", "rules": [
        ("L", "", "orbit"), ("R", "", "pan"), ("R", "ctrl", "pan"), ("M", "", "pan"), ("LR", "", "zoom")]},
    "blender": {"label": "Blender", "wheel": "zoom", "rules": [
        ("M", "", "orbit"), ("M", "shift", "pan"), ("M", "ctrl", "zoom"), ("R", "", "orbit")]},
    "solidworks": {"label": "SolidWorks", "wheel": "zoom_reverse", "rules": [
        ("M", "", "orbit"), ("M", "ctrl", "pan"), ("M", "shift", "zoom"), ("R", "", "orbit")]},
    "openinventor": {"label": "OpenInventor (FreeCAD)", "wheel": "zoom", "rules": [
        ("L", "", "orbit"), ("M", "", "pan"), ("LM", "", "zoom"), ("R", "", "orbit")]},
    "revit": {"label": "Revit", "wheel": "zoom", "rules": [
        ("M", "", "pan"), ("M", "shift", "orbit"), ("R", "", "orbit")]},
    "openscad": {"label": "OpenSCAD", "wheel": "zoom", "rules": [
        ("L", "", "orbit"), ("R", "", "pan"), ("M", "", "zoom"), ("R", "shift", "zoom")]},
}
DEFAULT = "onshape"
ZOOM_SPEEDS = [("slow", "Slow", 0.5), ("normal", "Normal", 1.0), ("fast", "Fast", 1.8)]

_BTN = {"L": Qt.LeftButton, "M": Qt.MiddleButton, "R": Qt.RightButton}


def _held(buttons) -> str:
    """Qt buttons held -> 'L', 'LM', 'MR' …"""
    return "".join(k for k in "LMR" if buttons & _BTN[k])


def _modifier(mods) -> str:
    """The modifier held ('' for none); two held are joined, as in 'ctrl+shift'."""
    return "+".join(n for n, flag in (("ctrl", Qt.ControlModifier), ("shift", Qt.ShiftModifier),
                                      ("alt", Qt.AltModifier)) if mods & flag)


def style(name):
    return STYLES.get(name) or STYLES[DEFAULT]


def drag_action(name, buttons, mods, editing=False):
    """What a drag with these buttons and keys does in this style: 'orbit', 'pan', 'zoom', or None.
    `editing` is True inside a sketch: the left button is the sketch's, so no left-button rule applies, except
    that Alt + left orbits in a style whose plain left drag orbits."""
    held, mod = _held(buttons), _modifier(mods)
    if not held:
        return None
    rules = style(name)["rules"]
    if editing and held == "L":
        return "orbit" if mod == "alt" and ("L", "", "orbit") in rules else None
    for b, m, action in rules:
        if b == held and m == mod:
            return action
    return None


def hover_action(name, mods):
    """What moving the pointer with no button held does while these keys are down (the Touchpad style: Shift
    pans, Alt orbits, Ctrl zooms), or None."""
    mod = _modifier(mods)
    if not mod:
        return None
    for b, m, action in style(name)["rules"]:
        if b == "" and m == mod:
            return action
    return None


_BUTTON_NAMES = {"L": "Left", "M": "Middle", "R": "Right"}


def describe(name, action) -> str:
    """How to do `action` in this style, read from its rules: "Ctrl+Right-drag / Middle-drag"."""
    st = style(name)
    out = []
    for b, m, a in st["rules"]:
        if a != action:
            continue
        keys = [k.capitalize() for k in m.split("+") if k]
        what = "+".join(_BUTTON_NAMES[c] for c in b) + "-drag" if b else "move"
        text = "+".join(keys + [what])
        if text not in out:
            out.append(text)
    if action == "zoom":
        out.insert(0, "Ctrl+scroll" if st["wheel"] == "pan" else "Wheel")
    elif action == "pan" and st["wheel"] == "pan":
        out.insert(0, "Scroll")
    return " / ".join(out) or "-"


def help_line(name) -> str:
    """One line saying how this style orbits, pans and zooms (the tooltip of its menu entry)."""
    return "Orbit: %s   Pan: %s   Zoom: %s" % (describe(name, "orbit"), describe(name, "pan"), describe(name, "zoom"))


def wheel_action(name, mods):
    """What the wheel / two-finger scroll does: 'zoom', 'zoom_reverse' or 'pan'. Ctrl always zooms."""
    w = style(name)["wheel"]
    if mods & Qt.ControlModifier and w == "pan":
        return "zoom"
    return w
