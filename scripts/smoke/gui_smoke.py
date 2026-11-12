"""GUI smoke: starts MainWindow offscreen, opens a file with open_any, reports unresolved instances.

Named without the `s*.py` pattern on purpose: it takes a file argument and scripts/smoke/run_all.sh
passes none, so it is run separately, not by the cumulative loop.

    QT_QPA_PLATFORM=offscreen both fs_py scripts/smoke/gui_smoke.py examples/hinge.fasm
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")


class _Settings(dict):
    """A QSettings stand-in that keeps this smoke from touching the real one on disk."""

    def value(self, k, default=None):
        return self.get(k, default)

    def setValue(self, k, v):
        self[k] = v


def main():
    if len(sys.argv) != 2:
        print("usage: gui_smoke.py <path>", file=sys.stderr)
        return 2
    path = os.path.abspath(sys.argv[1])

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    w.settings = _Settings()
    app.processEvents()
    w.open_any(path)
    app.processEvents()

    assert w.doc is not None, f"{path} did not open"
    unresolved = len(getattr(w.doc, "errors", {}))
    print(f"GUI OK unresolved={unresolved}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
