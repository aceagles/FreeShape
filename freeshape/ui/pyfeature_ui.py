"""The UI side of Python features: dialog rows, command-search entries, toolbar menus, icons and notices.

Nothing here knows a particular plugin. Every registered type gets its dialog from `cls.params`, a command in the
search and an entry in its plugin's toolbar menu. FreeCAD shows a FeaturePython's properties in its property
editor; FreeShape instead reuses its own feature dialog (`dialogs.SCHEMAS`), so a Python feature looks like a
built-in one (original choice, recorded on card I4.8).
"""
from __future__ import annotations

import os

from .. import pyfeature
from . import dialogs
from .icons import icon

GROUP = "Python features"
NOTICE_COLOR = "#b26a00"


def initials(label: str) -> str:
    """Two letters for the fallback icon: 'Spur gear' -> 'SG', 'Box' -> 'BO'."""
    words = [w for w in str(label).replace("_", " ").split() if w[:1].isalnum()]
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    return (words[0][:2] if words else "PY").upper()


def schema_for(ftype: str) -> list:
    """Dialog row specs (the `_num`, `_choice`, `_check` kind) for a registered Python feature type."""
    cls = pyfeature.get(ftype)
    if cls is None:
        return []
    rows = []
    for p in cls.params:
        vis = p.visible
        if p.kind == "length":
            rows.append(dialogs._num(p.key, p.label, "mm", visible=vis, minimum=p.min))
        elif p.kind == "angle":
            rows.append(dialogs._num(p.key, p.label, "deg", visible=vis, minimum=p.min))
        elif p.kind in ("integer", "float"):
            rows.append(dialogs._num(p.key, p.label, "", visible=vis, minimum=p.min))
        elif p.kind == "bool":
            rows.append(dialogs._check(p.key, p.label, visible=vis))
        elif p.kind == "choice":
            rows.append(dialogs._choice(p.key, p.label, list(p.options), visible=vis))
    return rows


def install() -> None:
    """Add the dialog rows of every registered type to `dialogs.SCHEMAS`. Safe to call again after plugins load."""
    for ftype, _cls, _info in pyfeature.feature_types():
        dialogs.SCHEMAS[ftype] = schema_for(ftype)


def svg_path(ftype: str):
    """The plugin's icon file for a type, or None. The file name must have no path parts."""
    cls, info = pyfeature.get(ftype), pyfeature.plugin_for(ftype)
    name = getattr(cls, "icon", None)
    if cls is None or info is None or not isinstance(name, str) or not name or name != os.path.basename(name) \
            or name in (".", ".."):
        return None
    path = os.path.join(info.path, name)
    return path if os.path.isfile(path) and not os.path.islink(path) else None


def feature_icon(ftype: str):
    """The QIcon for a Python feature type, or None when its SVG is absent or unreadable (icons.icon falls back)."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon, QPainter, QPixmap
    from PySide6.QtSvg import QSvgRenderer
    try:
        path = svg_path(ftype)
        if path is None or os.path.getsize(path) > 1 << 20:
            return None
        with open(path, "rb") as fh:
            r = QSvgRenderer(fh.read())
        if not r.isValid():
            return None
        ic = QIcon()
        for scale in (1, 2):
            pm = QPixmap(32 * scale, 32 * scale)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            r.render(p)
            p.end()
            pm.setDevicePixelRatio(scale)
            ic.addPixmap(pm)
        return ic
    except Exception:
        return None


def fallback_text(ftype: str) -> str:
    cls = pyfeature.get(ftype)
    return initials(getattr(cls, "label", "") or ftype.rsplit("/", 1)[-1])


def palette_commands(window) -> list:
    """One command per registered type, group 'Python features'; keywords come from the plugin name."""
    from .palette import Command
    install()
    cmds = []
    for ftype, cls, info in pyfeature.feature_types():
        cmds.append(Command(ftype, cls.label, cls.description or f"{cls.label} ({info.name} plugin).",
                            lambda t=ftype: window.start_feature(t),
                            f"{info.name} {info.id} python plugin", GROUP))
    return cmds


def toolbar_button(window, layout):
    """One QToolButton with a menu per plugin ('Gears' -> 'Spur gear', 'Bevel gear'). Returns the buttons."""
    from PySide6.QtCore import QSize
    from PySide6.QtWidgets import QMenu, QToolButton
    install()
    by_plugin = {}
    for ftype, cls, info in pyfeature.feature_types():
        by_plugin.setdefault(info.id, (info, []))[1].append((ftype, cls))
    buttons = []
    for pid, (info, types) in by_plugin.items():
        b = QToolButton()
        b.setIcon(icon(types[0][0]))
        b.setProperty("icon_name", types[0][0])
        b.setIconSize(QSize(26, 26))
        b.setToolTip(f"{info.name} — Python feature plugin")
        b.setAutoRaise(True)
        b.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(b)
        for ftype, cls in types:
            act = menu.addAction(icon(ftype), cls.label)
            act.setToolTip(cls.description)
            act.triggered.connect(lambda _=False, t=ftype: window.start_feature(t))
        b.setMenu(menu)
        b.setObjectName(f"py_plugin_{pid}")
        layout.addWidget(b)
        buttons.append(b)
    return buttons


def notices_for(features) -> dict:
    """{feature id: notice text} for the feature list; empty when there is nothing to say or lookup fails."""
    try:
        return pyfeature.notices(features)
    except Exception:
        return {}
