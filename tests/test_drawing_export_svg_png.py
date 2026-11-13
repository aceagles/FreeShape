"""Drawing export as SVG (true size in mm) and PNG (at a given dpi), headless."""
import os
import re
import sys
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: E402
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication  # noqa: E402

from freeshape.drawing import DrawingDoc  # noqa: E402
from freeshape.ui import drawing_ui as DU  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def doc(app):
    box = BRepPrimAPI_MakeBox(60, 40, 10).Shape()
    g = DrawingDoc()
    g.source = {"kind": "studio"}
    g.resolver = lambda src: box
    g.refresh(notify=False)
    return g


def test_svg_size_in_mm(doc, tmp_path):
    path = str(tmp_path / "d.svg")
    DU.export_svg(doc, path)
    root = ET.parse(path).getroot()
    W, H = doc.sheet_size()
    assert root.tag.endswith("svg")
    assert root.get("width") == f"{W:g}mm" and root.get("height") == f"{H:g}mm"
    vb = [float(x) for x in root.get("viewBox").replace(",", " ").split()]
    assert vb[2] / vb[3] == pytest.approx(W / H, rel=1e-3)
    assert any(el.tag.split("}")[1] in ("path", "line", "polyline") for el in root.iter())


def test_svg_portrait(doc, tmp_path):
    doc.sheet["portrait"] = True
    path = str(tmp_path / "p.svg")
    DU.export_svg(doc, path)
    W, H = doc.sheet_size()
    root = ET.parse(path).getroot()
    assert float(re.match(r"[\d.]+", root.get("width")).group()) == pytest.approx(W)
    assert W < H


@pytest.mark.parametrize("dpi", [200, 96])
def test_png_pixels_and_content(doc, tmp_path, dpi):
    path = str(tmp_path / "d.png")
    if dpi == 200:
        DU.export_png(doc, path)
    else:
        DU.export_png(doc, path, dpi=dpi)
    img = QImage(path)
    assert not img.isNull()
    W, H = doc.sheet_size()
    assert abs(img.width() - W / 25.4 * dpi) <= 1
    assert abs(img.height() - H / 25.4 * dpi) <= 1
    colours = {img.pixel(x, y) for x in range(0, img.width(), 7) for y in range(0, img.height(), 7)}
    assert len(colours) > 1


def test_pdf_still_exports(doc, tmp_path):
    path = str(tmp_path / "d.pdf")
    DU.export_pdf(doc, path)
    assert open(path, "rb").read(4) == b"%PDF"
