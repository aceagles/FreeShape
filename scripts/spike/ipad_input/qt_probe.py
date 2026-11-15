"""Qt input probe: a window that logs every touch, tablet, mouse, gesture and wheel event Qt delivers to it.

    python qt_probe.py [--platform xcb|wayland|offscreen] [--target widget|opengl] [--log probe.jsonl]

Environment equivalents (command line wins): PROBE_PLATFORM, PROBE_TARGET, PROBE_LOG. It stands in for FreeShape's
QOpenGLWidget viewport, so `--target opengl` uses a QOpenGLWidget and `--target widget` a plain QWidget.

Each event is appended to the log as one JSON line with a "kind": start, window-exposed, touch, tablet, mouse,
gesture, wheel. The on-screen readout shows the newest events first, in large high-contrast text.
Needs PySide6 (6.x); the module imports without one so tests of the helpers can run anywhere.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import deque
from pathlib import Path

DEFAULT_LOG = "qt_probe.jsonl"
PLATFORMS = ("xcb", "wayland", "offscreen")
TARGETS = ("widget", "opengl")
READOUT_LINES = 14


class JsonlLog:
    """Append-only JSON lines file. Opened per write so a crash loses nothing."""

    def __init__(self, path: str):
        self.path = path

    def write(self, kind: str, **fields) -> dict:
        record = {"ts": round(time.time(), 4), "kind": kind, **fields}
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
        return record


def pick(cli_value: str | None, env_name: str, allowed: tuple[str, ...], default: str) -> str:
    value = cli_value or os.environ.get(env_name) or default
    if value not in allowed:
        raise SystemExit(f"{env_name}/argument must be one of {allowed}, got {value!r}")
    return value


def summarise(record: dict) -> str:
    """One readout line: the facts that matter first (kind, pointer, pressure, count, synthesised)."""
    kind = record["kind"]
    if kind == "touch":
        return f"TOUCH {record['state']} points={record['point_count']} synth=no"
    if kind == "tablet":
        return (f"TABLET {record['type']} {record['pointer_type']} "
                f"pressure={record['pressure']:.2f} synth=no")
    if kind == "mouse":
        return (f"MOUSE {record['type']} {record['pointer_type']} "
                f"synth={record['synthesised']}")
    if kind == "gesture":
        return f"GESTURE {record['gesture']} value={record['value']:.2f}"
    if kind == "wheel":
        return f"WHEEL angle={record['angle_delta']} pixel={record['pixel_delta']} phase={record['phase']}"
    return kind


def _enum_name(value) -> str:
    return getattr(value, "name", str(value))


def describe_device(device) -> dict:
    return {
        "name": device.name(),
        "type": _enum_name(device.type()),
        "capabilities": _enum_name(device.capabilities()),
        "seat": device.seatName(),
        "pointer_type": _enum_name(device.pointerType()) if hasattr(device, "pointerType") else None,
        "max_points": device.maximumPoints() if hasattr(device, "maximumPoints") else None,
    }


def build_probe_classes():
    """Define the widget classes. Done lazily so the module imports without PySide6."""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QColor, QFont, QPainter
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
    from PySide6.QtWidgets import QWidget

    class ProbeMixin:
        def init_probe(self, log: JsonlLog):
            self.log = log
            self.recent = deque(maxlen=READOUT_LINES)
            self.exposed_logged = False
            self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
            self.setMouseTracking(True)
            self.setWindowTitle("Qt input probe")
            self.resize(1024, 768)

        # -- recording -------------------------------------------------
        def record(self, kind: str, **fields):
            entry = self.log.write(kind, **fields)
            self.recent.appendleft(summarise(entry))
            self.update()

        def log_exposed_once(self):
            if self.exposed_logged:
                return
            self.exposed_logged = True
            self.log.write("window-exposed", size=[self.width(), self.height()],
                           target=type(self).__name__)

        # -- event decoding --------------------------------------------
        def event(self, ev):
            kind = ev.type()
            if kind in (QEvent.Type.TouchBegin, QEvent.Type.TouchUpdate,
                        QEvent.Type.TouchEnd, QEvent.Type.TouchCancel):
                self.log_touch(ev)
            elif kind in (QEvent.Type.TabletPress, QEvent.Type.TabletMove,
                          QEvent.Type.TabletRelease):
                self.log_tablet(ev)
            elif kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove,
                          QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonDblClick):
                self.log_mouse(ev)
            elif kind == QEvent.Type.NativeGesture:
                self.log_gesture(ev)
            elif kind == QEvent.Type.Wheel:
                self.log_wheel(ev)
            elif kind == QEvent.Type.Show:
                # Exposure proper arrives on the QWindow; see ExposeWatcher. Show is a fallback.
                pass
            return super().event(ev)

        def log_touch(self, ev):
            points = [{"id": p.id(), "state": _enum_name(p.state()),
                       "x": round(p.position().x(), 1), "y": round(p.position().y(), 1),
                       "pressure": round(p.pressure(), 3)} for p in ev.points()]
            self.record("touch", type=_enum_name(ev.type()), state="+".join(sorted({p["state"] for p in points})),
                        point_count=len(points), points=points,
                        device=ev.device().name() if ev.device() else None)

        def log_tablet(self, ev):
            self.record("tablet", type=_enum_name(ev.type()),
                        pointer_type=_enum_name(ev.pointerType()), pressure=round(ev.pressure(), 4),
                        x=round(ev.position().x(), 1), y=round(ev.position().y(), 1),
                        tilt=[ev.xTilt(), ev.yTilt()], device=ev.device().name() if ev.device() else None)

        def log_mouse(self, ev):
            device = ev.pointingDevice()
            synthesised = _enum_name(ev.source())
            self.record("mouse", type=_enum_name(ev.type()), button=_enum_name(ev.button()),
                        x=round(ev.position().x(), 1), y=round(ev.position().y(), 1),
                        device_type=_enum_name(device.type()) if device else None,
                        pointer_type=_enum_name(device.pointerType()) if device else None,
                        source=synthesised,
                        synthesised=ev.source() != Qt.MouseEventSource.MouseEventNotSynthesized)

        def log_gesture(self, ev):
            self.record("gesture", gesture=_enum_name(ev.gestureType()), value=ev.value(),
                        x=round(ev.position().x(), 1), y=round(ev.position().y(), 1),
                        fingers=ev.fingerCount())

        def log_wheel(self, ev):
            angle, pixel = ev.angleDelta(), ev.pixelDelta()
            self.record("wheel", angle_delta=[angle.x(), angle.y()], pixel_delta=[pixel.x(), pixel.y()],
                        phase=_enum_name(ev.phase()), inverted=ev.inverted(),
                        synthesised=ev.source() != Qt.MouseEventSource.MouseEventNotSynthesized)

        # -- readout ---------------------------------------------------
        def draw_readout(self, painter: QPainter):
            painter.fillRect(self.rect(), QColor("black"))
            painter.setPen(QColor("yellow"))
            font = QFont("monospace")
            font.setStyleHint(QFont.StyleHint.Monospace)
            font.setPixelSize(36)
            font.setBold(True)
            painter.setFont(font)
            lines = list(self.recent) or ["Touch, pen or click here"]
            line_height = 46
            for row, text in enumerate(lines):
                painter.drawText(20, 50 + row * line_height, text)

    class ProbeWidget(ProbeMixin, QWidget):
        def __init__(self, log: JsonlLog):
            super().__init__()
            self.init_probe(log)

        def paintEvent(self, _ev):
            painter = QPainter(self)
            self.draw_readout(painter)
            painter.end()

    class ProbeGLWidget(ProbeMixin, QOpenGLWidget):
        def __init__(self, log: JsonlLog):
            super().__init__()
            self.init_probe(log)

        def paintGL(self):
            painter = QPainter(self)
            self.draw_readout(painter)
            painter.end()

    return ProbeWidget, ProbeGLWidget


def install_expose_watcher(widget):
    """Log "window-exposed" once, when the native window is first exposed (not merely shown)."""
    from PySide6.QtCore import QEvent, QObject

    class ExposeWatcher(QObject):
        def eventFilter(self, _obj, ev):
            if ev.type() == QEvent.Type.Expose and widget.windowHandle().isExposed():
                widget.log_exposed_once()
            return False

    widget.create()
    watcher = ExposeWatcher(widget)
    widget.windowHandle().installEventFilter(watcher)
    widget._expose_watcher = watcher


def log_start(app, log: JsonlLog, platform: str, target: str):
    from PySide6.QtGui import QInputDevice

    devices = [describe_device(d) for d in QInputDevice.devices()]
    log.write("start", platform=app.platformName(), requested_platform=platform, target=target,
              pid=os.getpid(), devices=devices)


def make_window(app, log: JsonlLog, target: str):
    widget_cls, gl_cls = build_probe_classes()
    window = (gl_cls if target == "opengl" else widget_cls)(log)
    install_expose_watcher(window)
    return window


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--platform", help="Qt platform: xcb, wayland or offscreen (env PROBE_PLATFORM)")
    parser.add_argument("--target", help="widget or opengl (env PROBE_TARGET, default opengl)")
    parser.add_argument("--log", help=f"JSON lines path (env PROBE_LOG, default {DEFAULT_LOG})")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    platform = pick(args.platform, "PROBE_PLATFORM", PLATFORMS, os.environ.get("QT_QPA_PLATFORM") or "xcb")
    target = pick(args.target, "PROBE_TARGET", TARGETS, "opengl")
    log_path = args.log or os.environ.get("PROBE_LOG") or DEFAULT_LOG
    os.environ["QT_QPA_PLATFORM"] = platform

    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1] + ["-platform", platform])
    log = JsonlLog(log_path)
    log_start(app, log, platform, target)
    window = make_window(app, log, target)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
