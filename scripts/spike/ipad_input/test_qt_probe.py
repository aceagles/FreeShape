"""Self-test for qt_probe.py. Runs on the offscreen platform, no display needed.

    QT_QPA_PLATFORM=offscreen python -m unittest test_qt_probe -v

Each test builds its own probe window and closes it.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import qt_probe  # noqa: E402  (import after the platform default is set)


def read_log(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def kinds(records: list[dict], kind: str) -> list[dict]:
    return [r for r in records if r["kind"] == kind]


class ProbeCase(unittest.TestCase):
    target = "widget"

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(["qt_probe_test"])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log_path = Path(self.tmp.name) / "probe.jsonl"
        self.log = qt_probe.JsonlLog(str(self.log_path))
        qt_probe.log_start(self.app, self.log, "offscreen", self.target)
        self.window = qt_probe.make_window(self.app, self.log, self.target)
        self.addCleanup(self.window.close)
        self.window.show()
        self.pump()

    def pump(self):
        from PySide6.QtTest import QTest
        QTest.qWait(50)

    def send_two_point_touch(self):
        from PySide6.QtCore import QPoint
        from PySide6.QtTest import QTest
        device = QTest.createTouchDevice()
        seq = QTest.touchEvent(self.window, device)
        seq.press(0, QPoint(100, 100), self.window)
        seq.press(1, QPoint(300, 200), self.window)
        seq.commit()
        seq = QTest.touchEvent(self.window, device)
        seq.release(0, QPoint(100, 100), self.window)
        seq.release(1, QPoint(300, 200), self.window)
        seq.commit()
        self.pump()

    def click(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        QTest.mouseClick(self.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(50, 60))
        self.pump()

    def test_start_record_has_platform_and_devices(self):
        start = kinds(read_log(self.log_path), "start")[0]
        self.assertEqual(start["platform"], "offscreen")
        self.assertIsInstance(start["devices"], list)

    def test_window_exposed_logged_once(self):
        self.window.hide()
        self.window.show()
        self.pump()
        self.assertEqual(len(kinds(read_log(self.log_path), "window-exposed")), 1)

    def test_two_point_touch_is_logged_with_both_points(self):
        self.send_two_point_touch()
        touches = kinds(read_log(self.log_path), "touch")
        self.assertTrue(touches)
        self.assertEqual(touches[0]["point_count"], 2)
        self.assertEqual({p["id"] for p in touches[0]["points"]}, {0, 1})
        self.assertTrue(any("TOUCH" in line and "points=2" in line for line in self.window.recent))

    def test_mouse_click_is_logged_as_not_synthesised(self):
        self.click()
        mice = kinds(read_log(self.log_path), "mouse")
        self.assertTrue(mice)
        self.assertTrue(all(m["synthesised"] is False for m in mice))
        self.assertEqual(mice[0]["type"], "MouseButtonPress")

    def test_newest_event_is_first_in_readout(self):
        self.click()
        self.assertTrue(self.window.recent[0].startswith("MOUSE MouseButtonRelease"))

    def test_summarise_shows_pressure_and_pointer(self):
        line = qt_probe.summarise({"kind": "tablet", "type": "TabletMove", "pointer_type": "Pen",
                                   "pressure": 0.5})
        self.assertIn("pressure=0.50", line)
        self.assertIn("Pen", line)


class ProbeOpenGLCase(ProbeCase):
    """Same checks with a QOpenGLWidget as the target."""
    target = "opengl"


class OptionTest(unittest.TestCase):
    def test_pick_prefers_argument_over_env(self):
        os.environ["PROBE_TARGET"] = "widget"
        self.addCleanup(os.environ.pop, "PROBE_TARGET")
        self.assertEqual(qt_probe.pick("opengl", "PROBE_TARGET", qt_probe.TARGETS, "x"), "opengl")
        self.assertEqual(qt_probe.pick(None, "PROBE_TARGET", qt_probe.TARGETS, "x"), "widget")

    def test_pick_rejects_unknown_value(self):
        with self.assertRaises(SystemExit):
            qt_probe.pick("wayland2", "PROBE_PLATFORM", qt_probe.PLATFORMS, "xcb")


if __name__ == "__main__":
    unittest.main()
