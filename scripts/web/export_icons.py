"""Export the desktop's tool icons (freeshape/ui/icons.py) to SVG files for the Browser client.

    python scripts/web/export_icons.py            writes web/src/icons/<name>.svg, one per tool
    python scripts/web/export_icons.py --check    writes nothing; exits 1 when a committed file differs

icons.py draws with QPainter, so each icon is painted onto a QSvgGenerator (a 32 x 32 viewBox, the size the desktop
draws at). The files are committed; the client imports them from web/src/icons (see web/src/tools/icons.tsx).
Needs PySide6 with QtSvg; QT_QPA_PLATFORM defaults to offscreen. Add a name to TOOLS when a strip gets a new tool.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "web" / "src" / "icons"

# The icon name of every tool on the feature strip and the Desktop toolbar (web/src/tools/defs.ts), by desktop icon name.
TOOLS = (
    "sketch", "plane", "axis",
    "extrude", "revolve", "sweep", "rib", "loft", "boundary_surface",
    "fillet", "chamfer", "draft", "shell", "hole", "thread", "delete_face", "move_face", "offset_surface",
    "thicken", "enclose", "fill", "delete_part",
    "sm_base", "sm_flange", "sm_hem", "sm_jog", "sm_tab", "sm_convert",
    "mirror", "linear_pattern", "circular_pattern", "boolean", "split", "move",
    # the Desktop-mode feature toolbar's other buttons (web/src/tools/defs.ts DESKTOP_TOOLBAR)
    "undo", "redo", "variable", "variable_studio_ref", "configs", "derived", "history", "plugin",
    "flat", "flat_dxf", "nest", "mate_connector", "import", "export", "section", "interference", "fit", "iso",
)
SIZE = 32


def render(name: str) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, QSize
    from PySide6.QtGui import QPainter
    from PySide6.QtSvg import QSvgGenerator

    from freeshape.ui import icons

    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    gen = QSvgGenerator()
    gen.setOutputDevice(buf)
    gen.setSize(QSize(SIZE, SIZE))
    gen.setViewBox(QRectF(0, 0, SIZE, SIZE))
    gen.setTitle(name)
    gen.setDescription("")
    p = QPainter(gen)
    p.setRenderHint(QPainter.Antialiasing)
    icons.draw(name, p)
    p.end()
    buf.close()
    return bytes(data)


def main(argv: list[str]) -> int:
    check = "--check" in argv
    sys.path.insert(0, str(ROOT))
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 (fonts for the few icons with text)
    OUT.mkdir(parents=True, exist_ok=True)
    stale = []
    for name in TOOLS:
        svg = render(name)
        path = OUT / f"{name}.svg"
        if check:
            if not path.exists() or path.read_bytes() != svg:
                stale.append(name)
        else:
            path.write_bytes(svg)
    if check:
        print("stale: " + ", ".join(stale) if stale else "icons up to date")
        return 1 if stale else 0
    print(f"wrote {len(TOOLS)} icons to {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
