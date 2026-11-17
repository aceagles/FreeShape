"""`FreeShape --open-timing <out file> [cold|prewarm] [seconds on Home]`: time opening a small Document (#52).

Starts the real window with the engine on, makes a Document like the one in the report (one empty Part
Studio), waits on Home for the given seconds, then opens it from Home as a card click does (`open_any`) and
waits for the Part Studio's first rebuild to come back from its worker. Every stage (freeshape.timing) goes to
the out file, with a summary line last:

    RESULT mode=cold home_wait=0.0 ui_blocked=0.051 first_result=5.912

`cold` turns the start-up prewarm off (the behaviour before #52); `prewarm` leaves it on. Used by the Windows
build on GitHub (.github/workflows/mac.yml); runs the same from source: python -m freeshape --open-timing ...
"""
import os
import sys
import tempfile
import time


def run(out_path, mode="prewarm", home_wait=0.0):
    from . import timing
    timing.enable()
    timing.begin(f"startup ({mode}, {home_wait:.0f} s on Home)")
    tmp = tempfile.mkdtemp(prefix="freeshape-open-timing-")
    os.environ["FREESHAPE_DATA_DIR"] = os.path.join(tmp, "appdata")
    os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")
    if mode == "cold":
        os.environ["FREESHAPE_PREWARM"] = "0"
    lines, result = [], "RESULT failed"
    try:
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication(sys.argv[:1])
        for name in ("warning", "critical", "information"):
            setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))
        from . import doc_folder, doc_registry
        from . import kernel  # noqa: F401
        timing.mark("startup: kernel imported")
        path = os.path.join(tmp, "Test Shapes")
        fd = doc_folder.FolderDocument.create(path, "Test Shapes", registry=doc_registry.default_registry())
        fd.add_tab("studio", "Part Studio 1")
        fd.save()
        timing.mark("startup: test Document made")
        from .ui import mainwindow
        w = mainwindow.MainWindow(None, engine=True)
        w.settings = QSettings(os.path.join(tmp, "settings.ini"), QSettings.IniFormat)
        w.show()
        app.processEvents()
        timing.mark("startup: window shown")
        w.prewarm_engine()
        w.show_home()
        end = time.monotonic() + home_wait
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)
        w.open_any(path)                            # a Home card click; starts the clock again
        ui = timing.since_begin()
        settled = w.studio_engine.settle(300)
        first = timing.since_begin()
        if settled and w.folder is not None:
            result = f"RESULT mode={mode} home_wait={home_wait:.1f} ui_blocked={ui:.3f} first_result={first:.3f}"
        else:
            result = f"RESULT failed: settled={settled} folder={w.folder is not None} after {first:.1f} s"
        w.studio_engine.shutdown()
        w.close()
    except Exception as e:      # noqa: BLE001
        import traceback
        result = "RESULT failed: " + "".join(traceback.format_exception(e))
    lines = [f"{t:8.3f} s  {stage}" for t, stage in timing.marks()]
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines + [result]) + "\n")
    return 0 if result.startswith("RESULT mode=") else 1
