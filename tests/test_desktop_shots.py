"""I9.13: scripts/web/desktop_shots.py grabs the desktop program offscreen for the Desktop-mode reference shots."""
import importlib.util
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtGui import QImage  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _script():
    spec = importlib.util.spec_from_file_location("desktop_shots", os.path.join(ROOT, "scripts", "web", "desktop_shots.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_desktop_studio_shot_has_the_size_and_a_top_bar(tmp_path):
    ds = _script()
    out = str(tmp_path / "shots")
    line = ds.capture_state("desktop-studio", 1440, 900, out)
    assert line.startswith("wrote ")
    img = QImage(os.path.join(out, "desktop-studio-1440x900.png"))
    assert not img.isNull()
    assert (img.width(), img.height()) == (1440, 900)
    assert not ds.top_flat(img, 44), "the top 44 px are one flat colour: the top bar did not draw"
